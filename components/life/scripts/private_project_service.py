"""v0.4 B: the subject's own private *projects*, on the existing ManagedArtifact owner.

What this module is
-------------------
One small service that both the Life subject (A) and the local phone (B) call to treat a
``ManagedArtifact`` as a private project: **list** the projects, **read** one with its version
history, **create** one, **commit** a new version of one, and **close** one with the owner's own
``archive_artifact`` verb.

What it is not
--------------
* **No second work library.**  A project *is* a ``ManagedArtifact`` row the existing Supply owner
  already holds; every read and every write goes through ``LifeArtifactPort``-shaped calls
  (``list_artifacts`` / ``read_artifact`` / ``create_artifact`` / ``commit_version`` /
  ``archive_artifact``).  This module adds no store, no table, no wallet and no second Memory.
* **No migration, no rebuild.**  An artifact that existed before this module did is listed here
  exactly as the owner reports it -- nothing is copied, rewritten or re-created, and no marker is
  stamped into old content.
* **Not a proposal machine.**  ``create_project`` needs an **explicit title and content**.  A
  proposal from ``private_project_cognition`` is never created by being proposed; the caller must
  choose one and pass its fields here itself.
* **Not an exception-thrower for owner answers.**  Programming mistakes raise
  (``ProjectContractRefused``); what the *owner* answers is always a dict with one of the
  documented ``status`` values, so a caller never has to tell "refused" from "crashed".

What a project is, and what it is not
-------------------------------------
A project is an artifact of this subject in this workspace, **minus** the artifacts the caller
names as non-projects.  The reading material and the private-items document are exactly those:
they are materials and private bookkeeping, not projects, so the caller passes them in
``excluded_artifact_ids`` and this module keeps them out of the list -- while still letting a
direct ``read_project`` on one of them answer honestly (``PROJECT_ARTIFACT_EXCLUDED``) instead of
pretending it does not exist.

Version history
---------------
The owner keeps every version (``artifact_versions`` is immutable) but the *read* surface this
module speaks to only guarantees the head.  So history has two honest answers:

* ``complete=True`` -- the port exposed a ``list_versions(artifact_id)`` callable and the owner
  answered; ``entries`` is every version, newest first.
* ``complete=False`` -- the port has no such callable yet (``reason_code
  = PORT_LACKS_LIST_VERSIONS``); ``entries`` carries the one version the owner *did* report (the
  head, with its number, parent and byte length).  Nothing is invented to fill the gap.

Wiring ``list_versions`` into ``life_artifact_port.LifeArtifactPort`` is A's change; this module
picks it up with no edit of its own (see ``docs/engineering/v04-private-projects-stage-result.md``).
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import (  # noqa: E402
    ArtifactOutcome,
    ArtifactOwnerRefused,
    EffectState,
    LifeArtifactError,
    LifeArtifactUnavailable,
    operation_key_for,
)

#: What the ``source`` field says: every fact in an answer came from this owner.
SOURCE_OWNER = "ManagedArtifact"

#: Artifact lifecycles this module distinguishes.
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_ARCHIVED = "ARCHIVED"

#: The statuses a caller may see.  They are the *outcome* of an intended effect, never a guess.
STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_CONFLICT = "conflict"
STATUS_NOT_FOUND = "not_found"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"

#: Why a history is short (never why a version is missing).
REASON_PORT_LACKS_LIST_VERSIONS = "PORT_LACKS_LIST_VERSIONS"
REASON_ARTIFACT_EXCLUDED = "PROJECT_ARTIFACT_EXCLUDED"
#: An EP-16 album picture is a real artifact of hers, but it is a media manifest -- never a work
#: that may be continued, compared, derived or closed from the project surface.
REASON_ALBUM_IMAGE = "PROJECT_IS_AN_ALBUM_IMAGE"
ALBUM_MARKER = "album.image.v1"
REASON_OPERATION_NEVER_LANDED = "OPERATION_NEVER_LANDED"
REASON_RESOLUTION_INCONCLUSIVE = "RESOLUTION_INCONCLUSIVE"
REASON_RECONCILED = "RECONCILED_AFTER_UNKNOWN"

MAX_TITLE_CHARS = 120
MAX_CONTENT_CHARS = 20000
MAX_LIST = 100
MAX_HISTORY = 50

#: v0.4 work-revisit: the structured marker a *sourced* new work carries in its first version.
#: Same physical convention the research/material artifacts already use (one HTML-comment line at
#: the top: ``<!-- <name>:v1 {json} -->``), so no second provenance store and no schema change --
#: the marker lives inside an immutable version row the owner already keeps forever.
WORK_SOURCE_MARKER = "nyairo-work-source:v1"
MARKER_OPEN = "<!-- "
MARKER_CLOSE = " -->"
#: What the old material is being taken *for*: the three purposes the design names.
SOURCE_PURPOSES: tuple[str, ...] = ("quote", "adapt", "reuse")
PURPOSE_LABELS: dict[str, str] = {"quote": "引用", "adapt": "改编", "reuse": "直接复用"}
MAX_MARKER_CHARS = 800
MAX_SOURCE_NOTE_CHARS = 120
#: Bounds of one comparison: how much text of each side is compared, and how much context one
#: difference quotes.
MAX_COMPARE_CHARS = 8000
MAX_COMPARE_LINES = 400
#: How many claimed-source hops a derived work may be checked through before the walk refuses to
#: keep going ("too deep").  Small on purpose: a citation chain is a citation, not a directory tree.
MAX_SOURCE_DEPTH = 3
#: How much of one earlier version may be offered to a writing request (never a history dump).
MAX_EARLIER_EXCERPT_CHARS = 600
COMPARE_CONTEXT_CHARS = 120
REASON_PORT_LACKS_READ_VERSION = "PORT_LACKS_READ_VERSION"
REASON_VERSION_NOT_IN_ARTIFACT = "VERSION_NOT_IN_ARTIFACT"
REASON_SOURCE_NOT_READABLE = "SOURCE_NOT_READABLE"

#: The port surface this service calls.  Checked once at construction so a wiring mistake is a
#: clear refusal instead of an ``AttributeError`` halfway through a write.
REQUIRED_PORT_METHODS: tuple[str, ...] = (
    "list_artifacts",
    "read_artifact",
    "create_artifact",
    "commit_version",
    "archive_artifact",
)

#: An *optional* port capability: the full version list.  See the module docstring.
OPTIONAL_PORT_METHODS: tuple[str, ...] = ("list_versions",)


class PrivateProjectError(RuntimeError):
    """Base class for this service's own refusals (a programming/wiring mistake, not an answer)."""


class ProjectContractRefused(PrivateProjectError):
    """The caller asked for something this service does not accept at all."""


class ProjectUnavailable(PrivateProjectError):
    """No usable artifact port was supplied (the owner was not even asked)."""


def project_operation_key(*parts: Any) -> str:
    """A deterministic operation key for one intended project effect.

    The owner keys idempotency on ``operation_key``, so a key derived from *what is intended*
    (never from the attempt) is what makes "resolve the UNKNOWN, then retry with the same key"
    safe instead of creating a second project or a second version.
    """
    cleaned = [str(part) for part in parts if str(part) != ""]
    if not cleaned:
        raise ProjectContractRefused("project_operation_key requires at least one part")
    digest = operation_key_for("private_project", *cleaned)
    return "project:%s" % digest.split(":", 1)[-1]


def is_album_manifest(content: Any) -> bool:
    """True only for the owner's structured album manifest (classification, not authentication).

    The project surface must not present a picture's media manifest as her text.  A malformed or
    unrelated JSON document is simply not an album manifest; an unreadable answer is handled by
    the caller as "cannot classify", never as "not a work".
    """
    if not isinstance(content, str) or not content or len(content) > 4096:
        return False
    try:
        doc = json.loads(content)
    except (TypeError, ValueError):
        return False
    if not isinstance(doc, dict) or doc.get("kind") != ALBUM_MARKER:
        return False
    media = doc.get("media")
    source = doc.get("source")
    return (
        isinstance(doc.get("caption"), str)
        and doc.get("content_type") in ("image/png", "image/jpeg")
        and isinstance(media, dict)
        and isinstance(media.get("name"), str)
        and isinstance(media.get("sha256"), str)
        and isinstance(source, dict)
        and source.get("origin") == "user_import"
        and source.get("declared") == "self_declared"
    )


def _text(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ProjectContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ProjectContractRefused("%s must not contain NUL" % name)
    if not allow_empty and not text.strip():
        raise ProjectContractRefused("%s must not be blank" % name)
    if len(text) > int(limit):
        raise ProjectContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return text


def _title(value: Any) -> str:
    text = _text(value, "title", limit=MAX_TITLE_CHARS)
    one_line = " ".join(part for part in text.splitlines() if part.strip()).strip()
    if not one_line:
        raise ProjectContractRefused("title must not be blank")
    return one_line[:MAX_TITLE_CHARS]


def _artifact_id(value: Any) -> str:
    """The owner's own artifact id, canonicalised.

    A caller may hold either the full id (``artifact:<hex>``) or the bare hex it saw in a display
    ref (``life_project_options._artifact_id_of`` strips the prefix).  Both name the same artifact,
    so both are accepted; the id the owner is asked about is always the full one.
    """
    if not isinstance(value, str) or not value.strip():
        raise ProjectContractRefused("artifact_id is required")
    text = value.strip()
    if len(text) > 200:
        raise ProjectContractRefused("artifact_id is too long")
    if not text.startswith("artifact:") and re.match(r"^[0-9a-fA-F]{8,64}$", text):
        # A bare hex id is a display ref of a managed artifact (``life_project_options`` strips the
        # ``artifact:`` prefix); anything else is left exactly as the caller spelled it.
        return "artifact:" + text
    return text


def _operation_key(value: Any, *, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise ProjectContractRefused("operation_key is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectContractRefused("operation_key must be a non-empty string")
    text = value.strip()
    if len(text) > 128:
        raise ProjectContractRefused("operation_key is too long")
    return text


def _number(value: Any) -> Optional[float]:
    """A sortable float, or ``None``.  Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

def _version_id(value: Any) -> str:
    """The owner's own version id (``artifact-version:<hex>``), unrewritten."""
    if not isinstance(value, str) or not value.strip():
        raise ProjectContractRefused("version_id is required")
    text = value.strip()
    if len(text) > 200 or any(ch in text for ch in ("\x00", "\r", "\n")):
        raise ProjectContractRefused("version_id is not a usable owner id")
    return text


def _one_line(text: str) -> str:
    return str(text).replace("\r", " ").replace("\n", " ").strip()


def work_source_marker_line(payload: Mapping[str, Any]) -> str:
    """The single physical line a sourced work carries as its first line, or a refusal.

    Machine-written only: it is built from owner facts (the source artifact, the pinned version,
    that version's stored hash) plus the purpose the caller declared, and it is bounded so it can
    never become a place to smuggle a body into the header.
    """
    body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    line = MARKER_OPEN + WORK_SOURCE_MARKER + " " + body + MARKER_CLOSE
    if len(line) > MAX_MARKER_CHARS:
        raise ProjectContractRefused("source marker exceeds its own bound")
    if "\n" in line or "\r" in line:
        raise ProjectContractRefused("source marker must be one physical line")
    return line


def split_work_source_marker(content: Any) -> tuple[Optional[dict[str, Any]], str]:
    """``(provenance, body)`` for a sourced work, ``(None, content)`` for anything else.

    Only *our* marker is stripped: a material/research/practice marker is somebody else's header
    and is returned inside ``body`` exactly as it was stored.
    """
    if not isinstance(content, str) or not content:
        return None, "" if content is None else str(content)
    first, sep, rest = content.partition("\n")
    line = first.strip()
    if not (line.startswith(MARKER_OPEN) and line.endswith(MARKER_CLOSE)):
        return None, content
    inner = line[len(MARKER_OPEN):-len(MARKER_CLOSE)]
    name, _, raw = inner.partition(" ")
    if name.strip() != WORK_SOURCE_MARKER:
        return None, content
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None, content
    if not isinstance(payload, dict):
        return None, content
    return payload, (rest if sep else "")


def _marker_identity(provenance: Any) -> Optional[tuple]:
    """The identity of a source marker, for "did the *header* change?" questions.

    Only the fields that say *what was taken and why*: two markers that agree here describe the
    same claim, whether one of them came from the raw first version and the other from the
    sanitised view a caller sees.
    """
    if not isinstance(provenance, Mapping):
        return None
    return (provenance.get("source_artifact_id"), provenance.get("source_version_id"),
            provenance.get("purpose"), str(provenance.get("note") or ""))


def _bounded_slice(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_COMPARE_CHARS:
        return text, False
    return text[:MAX_COMPARE_CHARS], True


def _first_difference(left: str, right: str) -> Optional[dict[str, Any]]:
    """Where two bounded bodies first differ, with a small quoted window on each side."""
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    if index == limit and len(left) == len(right):
        return None
    half = COMPARE_CONTEXT_CHARS // 2
    start = max(0, index - half)
    return {
        "index": index,
        "from_context": _one_line(left[start:index + half]),
        "to_context": _one_line(right[start:index + half]),
        "from_char": (left[index] if index < len(left) else ""),
        "to_char": (right[index] if index < len(right) else ""),
    }


def _line_delta(left: str, right: str) -> dict[str, Any]:
    """How many lines were added / removed, on a bounded prefix of both bodies."""
    left_lines = left.split("\n")[:MAX_COMPARE_LINES]
    right_lines = right.split("\n")[:MAX_COMPARE_LINES]
    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return {"added_lines": added, "removed_lines": removed,
            "bounded_lines": min(len(left.split("\n")), len(right.split("\n"))) > MAX_COMPARE_LINES}

class PrivateProjectService:
    """List / read / create / commit / close one subject's private projects."""

    def __init__(
        self,
        port: Any,
        workspace_id: str,
        grant_id: str,
        *,
        subject_id: Optional[str] = None,
        excluded_artifact_ids: Iterable[str] = (),
        max_list: int = MAX_LIST,
    ) -> None:
        if port is None:
            raise ProjectUnavailable("PORT_REQUIRED")
        missing = [name for name in REQUIRED_PORT_METHODS
                   if not callable(getattr(port, name, None))]
        if missing:
            raise ProjectUnavailable("PORT_METHODS_MISSING:%s" % ",".join(missing))
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProjectContractRefused("workspace_id is required")
        if not isinstance(grant_id, str) or not grant_id.strip():
            raise ProjectContractRefused("grant_id is required")
        resolved_subject = subject_id
        if resolved_subject is None:
            resolved_subject = getattr(port, "subject_id", None)
        if not isinstance(resolved_subject, str) or not resolved_subject.strip():
            raise ProjectContractRefused("subject_id is required (pass it, or use a port that has one)")
        excluded: list[str] = []
        for entry in excluded_artifact_ids or ():
            if not isinstance(entry, str) or not entry.strip():
                continue
            try:
                text = _artifact_id(entry)
            except ProjectContractRefused:
                continue
            if text not in excluded:
                excluded.append(text)
        self.port = port
        self.workspace_id = workspace_id.strip()
        self.grant_id = grant_id.strip()
        self.subject_id = resolved_subject.strip()
        #: Artifacts the caller declared non-projects (reading material, private-items document).
        self.excluded_artifact_ids = tuple(sorted(excluded))
        self.max_list = max(1, int(max_list))

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """What this service is wired to.  No owner call, no private text."""
        has_versions = callable(getattr(self.port, "list_versions", None))
        return {
            "kind": "private_project_service",
            "source": SOURCE_OWNER,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "grant_present": bool(self.grant_id),
            "excluded_artifact_ids": list(self.excluded_artifact_ids),
            "history_complete_available": has_versions,
            "max_list": self.max_list,
        }

    def is_excluded(self, artifact_id: str) -> bool:
        return str(artifact_id) in self.excluded_artifact_ids

    def _is_album_artifact(self, artifact_id: str) -> bool:
        """Whether this artifact's current head is an album media manifest.

        A read failure answers ``False``: a caller that cannot classify must let the owner answer
        normally rather than silently hide one of her works.  Only a positively parsed album
        manifest is excluded from the text-work surface.
        """
        try:
            view = self.port.read_artifact(artifact_id)
        except Exception:
            return False
        return is_album_manifest((view or {}).get("content"))

    def _album_refusal(self, artifact_id: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": STATUS_NOT_FOUND,
            "reason_code": REASON_ALBUM_IMAGE,
            "detail": "an album picture is a media manifest, not a text work",
            "artifact_id": str(artifact_id),
            "source": SOURCE_OWNER,
        }

    # -- reads ---------------------------------------------------------------------------

    def list_projects(self, *, include_archived: bool = True,
                      limit: Optional[int] = None) -> dict[str, Any]:
        """Every project the owner holds for this subject, newest revision first.

        An artifact named in ``excluded_artifact_ids`` is not a project and is left out of the
        list (it stays fully readable through its own owner surface).  An archived project is
        still listed unless ``include_archived=False``: closing a project is not deleting it.
        """
        cap = self.max_list if limit is None else max(1, min(int(limit), self.max_list))
        try:
            rows = self.port.list_artifacts()
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            return self._owner_failure(exc)
        except Exception as exc:  # a broken port is not "no projects"
            return self._unavailable("PROJECT_PORT_ERROR", exc)
        projects: list[dict[str, Any]] = []
        counts = {"total": 0, "active": 0, "archived": 0, "excluded": 0}
        scanned = 0
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            # Bounded classification: every album picture needs one head read, so the scan stops
            # after a small multiple of this surface's own page size.
            scanned += 1
            if scanned > self.max_list * 4:
                break
            if self.is_excluded(artifact_id) or self._is_album_artifact(artifact_id):
                counts["excluded"] += 1
                continue
            counts["total"] += 1
            status = "archived" if str(row.get("lifecycle")) == LIFECYCLE_ARCHIVED else "active"
            counts[status] += 1
            if status == "archived" and not include_archived:
                continue
            projects.append(self._project_row(row))

        def order(project: Mapping[str, Any]) -> tuple:
            updated = _number(project.get("updated_at"))
            created = _number(project.get("created_at"))
            return (-(updated if updated is not None else -1.0),
                    -(created if created is not None else -1.0),
                    str(project.get("title") or ""))

        projects.sort(key=order)
        truncated = len(projects) > cap
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "projects": projects[:cap],
            "counts": counts,
            "excluded_artifact_ids": list(self.excluded_artifact_ids),
            "truncated": bool(truncated),
            # A list carries no project text: the body only ever appears in read_project.
            "private_text_included": False,
        }

    def read_project(self, artifact_id: str, *, include_history: bool = True,
                     history_limit: int = MAX_HISTORY) -> dict[str, Any]:
        """One project with the owner's current text, head and (as far as readable) history."""
        target = _artifact_id(artifact_id)
        if self.is_excluded(target):
            return {
                "ok": False,
                "status": STATUS_NOT_FOUND,
                "reason_code": REASON_ARTIFACT_EXCLUDED,
                "detail": "this artifact is a material / private document, not a project",
                "artifact_id": target,
                "source": SOURCE_OWNER,
            }
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["artifact_id"] = target
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, artifact_id=target)
        artifact = dict(view.get("artifact") or {})
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        content = view.get("content")
        if not artifact:
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": "PROJECT_NOT_FOUND", "detail": "", "artifact_id": target,
                    "source": SOURCE_OWNER}
        if is_album_manifest(content):
            return self._album_refusal(target)
        project = self._project_row(artifact)
        project["head_version_id"] = head.get("version_id") or artifact.get("head_version_id")
        project["version_number"] = head.get("version_number")
        project["parent_version_id"] = head.get("parent_version_id")
        raw_content = content if isinstance(content, str) else ""
        # A sourced work keeps its provenance in its own first version; the head may still carry
        # that machine line, and it is never shown as if she had written it.
        head_provenance, body = split_work_source_marker(raw_content)
        gate = self._source_gate(target)
        project["content"] = body
        project["content_chars"] = len(body)
        project["stored_chars"] = len(raw_content)
        project["content_available"] = isinstance(content, str)
        project["provenance"] = self._provenance_view(head_provenance, gate)
        project["source_marker_present"] = head_provenance is not None
        project["source_gate"] = gate
        if gate["source_blocked"]:
            # The work's own body is still hers, but what was *taken* from a source she can no
            # longer open is not served through this surface (metadata/ids stay visible).
            project["derived_text_withheld"] = bool(head_provenance)
            if head_provenance:
                project["content"] = ""
                project["content_chars"] = 0
                project["content_available"] = False
        answer: dict[str, Any] = {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "artifact_id": target,
            "workspace_id": self.workspace_id,
            "project": project,
            "private_text_included": not bool(project.get("derived_text_withheld")),
        }
        if include_history:
            answer["history"] = self._history(target, head, content,
                                              limit=history_limit)
        return answer

    def read_project_history(self, artifact_id: str, *,
                             limit: int = MAX_HISTORY,
                             before_version_number: Optional[int] = None) -> dict[str, Any]:
        """One page of the project's version history, when the port can report one.

        ``before_version_number`` is the cursor the previous answer returned
        (``next_before_version_number``): it asks the owner for the page *older* than that version,
        so paging can never skip or repeat a version.
        """
        target = _artifact_id(artifact_id)
        if self.is_excluded(target):
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": REASON_ARTIFACT_EXCLUDED, "artifact_id": target,
                    "source": SOURCE_OWNER, "entries": [], "complete": False}
        if self._is_album_artifact(target):
            answer = self._album_refusal(target)
            answer.update({"entries": [], "complete": False})
            return answer
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer.update({"artifact_id": target, "entries": [], "complete": False})
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, artifact_id=target,
                                     entries=[], complete=False)
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        history = self._history(target, head, view.get("content"), limit=limit,
                                before_version_number=before_version_number)
        out = dict(history)
        out.update({"ok": True, "status": STATUS_OK, "source": SOURCE_OWNER,
                    "artifact_id": target})
        return out

    # -- v0.4 work-revisit: one named old version, a bounded compare, a sourced new work ----

    def _list_versions(self, artifact_id: str, *, limit: Optional[int] = None,
                       before_version_number: Optional[int] = None) -> Any:
        """Call the port's version lister in whichever spelling it has (both are supported)."""
        lister = getattr(self.port, "list_versions", None)
        if not callable(lister):
            raise ProjectContractRefused(REASON_PORT_LACKS_LIST_VERSIONS)
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = int(limit)
        if before_version_number is not None:
            kwargs["before_version_number"] = int(before_version_number)
        try:
            return lister(artifact_id, **kwargs)
        except TypeError:
            # An older port only knows ``list_versions(artifact_id)``: it has no cursor, so the
            # answer it gives is the head-only one and stays honestly incomplete.
            return lister(artifact_id)

    def read_project_version(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        """Read one *named* old version of a project, under today's readability.

        A pinned version is history, not an amnesty: the artifact must still be this subject's,
        still not tombstoned, and the version must belong to *this* artifact (the owner refuses a
        version of another artifact and this service re-checks the binding).  An ``ARCHIVED``
        project stays readable -- closing a work is not deleting it -- but the answer says
        ``continuable=False`` so nothing pretends the old text may be committed over.
        """
        target = _artifact_id(artifact_id)
        if self.is_excluded(target):
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": REASON_ARTIFACT_EXCLUDED, "artifact_id": target,
                    "version_id": str(version_id or ""), "source": SOURCE_OWNER}
        if self._is_album_artifact(target):
            answer = self._album_refusal(target)
            answer["version_id"] = str(version_id or "")
            return answer
        answer = self._read_version_raw(target, version_id)
        if not answer.get("ok"):
            return answer
        gate = self._source_gate(target)
        answer["source_gate"] = gate
        answer["provenance"] = self._provenance_view(answer.get("provenance"), gate)
        if gate["source_blocked"] and gate.get("source_derived"):
            # Metadata (which version, whose, how long, whose hash) stays; the derived prose does
            # not, and neither does the provenance note: both came from the blocked source.
            answer["content"] = ""
            answer["derived_text_withheld"] = True
            answer["content_available"] = False
            answer["private_text_included"] = False
            answer["reason_code"] = "SOURCE_NOT_READABLE"
            answer["detail"] = "a source this version was taken from is not readable now"
            return answer
        answer["derived_text_withheld"] = False
        answer["content_available"] = True
        return answer

    def continuation_excerpt(self, artifact_id: str, *, max_chars: int = 600) -> dict[str, Any]:
        """A bounded, **version-pinned** look at what this same work said one version ago.

        This is what the normal Life continuation may offer a model instead of a history dump: the
        body of the version immediately before the head, read through the owner's ``read_version``,
        cut to ``max_chars``, and only when the work's own source gate is open.  It carries the
        version id it came from, so a caller can tell "this is version 2" rather than "some old
        text"; and it never returns the head (continuing from the head is the writer's own job).

        A blocked source returns ``ok=False`` with no text at all: nothing derived is handed out.
        """
        target = _artifact_id(artifact_id)
        cap = max(1, min(int(max_chars), MAX_EARLIER_EXCERPT_CHARS))
        gate = self._source_gate(target)
        if gate.get("source_blocked") and gate.get("source_derived"):
            return self._withholding(gate, artifact_id=target, excerpt=None,
                                     private_text_included=False)
        try:
            page = self._list_versions(target, limit=2)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer.update({"artifact_id": target, "excerpt": None})
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, artifact_id=target, excerpt=None)
        entries = page.get("entries") if isinstance(page, Mapping) else page
        entries = [row for row in (entries or []) if isinstance(row, Mapping)]
        older = [row for row in entries if row.get("version_number") not in (1,)]
        older.sort(key=lambda row: -(int(row.get("version_number") or 0)))
        if not entries:
            return {"ok": True, "status": STATUS_OK, "artifact_id": target, "excerpt": None,
                    "reason_code": "NO_VERSIONS", "source": SOURCE_OWNER,
                    "private_text_included": False, "source_gate": gate}
        head_number = max(int(row.get("version_number") or 0) for row in entries)
        candidates = [row for row in entries if int(row.get("version_number") or 0) < head_number]
        candidates.sort(key=lambda row: -int(row.get("version_number") or 0))
        if not candidates:
            return {"ok": True, "status": STATUS_OK, "artifact_id": target, "excerpt": None,
                    "reason_code": "NO_EARLIER_VERSION", "source": SOURCE_OWNER,
                    "private_text_included": False, "source_gate": gate}
        row = candidates[0]
        read = self._read_version_raw(target, str(row.get("version_id")))
        if not read.get("ok"):
            answer = dict(read)
            answer["excerpt"] = None
            return answer
        body = read.get("content") if isinstance(read.get("content"), str) else ""
        if not body.strip():
            return {"ok": True, "status": STATUS_OK, "artifact_id": target, "excerpt": None,
                    "reason_code": "EARLIER_VERSION_EMPTY", "source": SOURCE_OWNER,
                    "version_id": read.get("version_id"),
                    "version_number": read.get("version_number"),
                    "private_text_included": False, "source_gate": gate}
        excerpt = body[:cap]
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "artifact_id": target,
            "version_id": read.get("version_id"),
            "version_number": read.get("version_number"),
            "excerpt": excerpt,
            "excerpt_chars": len(excerpt),
            "truncated": len(body) > len(excerpt),
            "content_sha256": read.get("content_sha256"),
            "reason_code": "",
            "private_text_included": True,
            "source_gate": gate,
        }
        return answer

    def _read_version_raw(self, target: str, version_id: Any) -> dict[str, Any]:
        wanted = _version_id(version_id)
        reader = getattr(self.port, "read_version", None)
        if not callable(reader):
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": REASON_PORT_LACKS_READ_VERSION, "artifact_id": target,
                    "version_id": wanted, "source": SOURCE_OWNER,
                    "detail": "this owner port cannot read a named version"}
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["version_id"] = wanted
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, version_id=wanted)
        artifact = dict(view.get("artifact") or {})
        if not artifact:
            return {"ok": False, "status": STATUS_NOT_FOUND, "reason_code": "PROJECT_NOT_FOUND",
                    "artifact_id": target, "version_id": wanted, "source": SOURCE_OWNER}
        lifecycle = str(artifact.get("lifecycle") or "")
        if lifecycle == "TOMBSTONED":
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": "ARTIFACT_TOMBSTONED", "artifact_id": target,
                    "version_id": wanted, "source": SOURCE_OWNER,
                    "detail": "the artifact was tombstoned; its history is withheld"}
        try:
            row = reader(target, wanted)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["version_id"] = wanted
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, version_id=wanted)
        if not isinstance(row, Mapping):
            return {"ok": False, "status": STATUS_UNKNOWN, "reason_code": "VERSION_UNREADABLE",
                    "artifact_id": target, "version_id": wanted, "source": SOURCE_OWNER}
        if str(row.get("artifact_id") or "") != target:
            # Owner-side binding, re-checked here: a version of another artifact is never served
            # through this artifact's name, not even as a "history" curiosity.
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": REASON_VERSION_NOT_IN_ARTIFACT, "artifact_id": target,
                    "version_id": str(row.get("version_id") or wanted), "source": SOURCE_OWNER}
        raw_text = row.get("content") if isinstance(row.get("content"), str) else ""
        provenance, body = split_work_source_marker(raw_text)
        truncated = bool(row.get("content_truncated"))
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "artifact_id": target,
            "version_id": str(row.get("version_id") or wanted),
            "version_number": row.get("version_number"),
            "parent_version_id": row.get("parent_version_id"),
            "created_at": row.get("created_at"),
            "operation_key": row.get("operation_key"),
            "content": body,
            "content_chars": row.get("content_chars") if isinstance(row.get("content_chars"), int)
            else len(raw_text),
            "returned_chars": len(body),
            "content_sha256": row.get("content_sha256"),
            "content_truncated": truncated,
            "lifecycle": lifecycle,
            "continuable": lifecycle == LIFECYCLE_ACTIVE and not truncated,
            "provenance": provenance,
            "private_text_included": True,
        }

    def compare_project_versions(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        """Compare one pinned old version with the artifact's *current* head, bounded.

        The honest question this answers is "did the text really change, or did only the machine
        head move?".  When the owner reports a stored ``content_sha256`` for both sides, that hash
        decides -- so an identical body under a new version id is reported as
        ``machine_head_only=True`` instead of being dressed up as an edit.  Bodies are compared as
        bounded slices; when a slice was cut the answer says so (``comparison.*_truncated``) and
        never claims the full text was compared.
        """
        target = _artifact_id(artifact_id)
        old = self.read_project_version(target, version_id)
        if not old.get("ok"):
            return old
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["artifact_id"] = target
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, artifact_id=target)
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        head_raw = view.get("content") if isinstance(view.get("content"), str) else ""
        head_provenance, head_body = split_work_source_marker(head_raw)
        from_body = old.get("content") if isinstance(old.get("content"), str) else ""
        left, left_cut = _bounded_slice(from_body)
        right, right_cut = _bounded_slice(head_body)
        from_hash = str(old.get("content_sha256") or "")
        to_hash = str(head.get("content_sha256") or "")
        gate = self._source_gate(target)
        from_meta = {
            "version_id": old.get("version_id"),
            "version_number": old.get("version_number"),
            "created_at": old.get("created_at"),
            "content_chars": old.get("content_chars"),
            "content_sha256": from_hash or None,
            "lifecycle": old.get("lifecycle"),
            "is_head": str(old.get("version_id")) == str(head.get("version_id")),
        }
        to_meta = {
            "version_id": head.get("version_id"),
            "version_number": head.get("version_number"),
            "created_at": head.get("created_at"),
            "content_chars": len(head_body),
            "content_sha256": to_hash or None,
            "lifecycle": old.get("lifecycle"),
            "is_head": True,
        }
        if old.get("derived_text_withheld") or (gate["source_blocked"] and gate.get("source_derived")):
            # No derived prose may be compared out of a blocked source -- not even as a "diff".
            withheld = self._withholding(
                gate, artifact_id=target,
                **{"from": dict(from_meta, provenance=None, content_withheld=True),
                   "to": dict(to_meta, provenance=None, content_withheld=True),
                   "content_changed": None, "machine_head_only": None,
                   "body_changed": None, "headers_differ": None, "hash_changed": None})
            return withheld
        # D: what the user asked about is the *body*, not the machine header.  The stored hash is
        # over content-that-may-include-our-own-marker, so it can differ while the body is the same
        # (and it can match while a header was added/removed).  Both facts are reported separately.
        if left_cut or right_cut:
            body_changed: Optional[bool] = None
            basis = "bounded_prefix"
            reason = "COMPARISON_INCOMPLETE"
        else:
            body_changed = left != right
            basis = "stripped_body"
            reason = ""
        hash_changed: Optional[bool] = (from_hash != to_hash) if (from_hash and to_hash) else None
        headers_differ = _marker_identity(old.get("provenance")) != _marker_identity(head_provenance)
        lines = (_line_delta(left, right) if body_changed
                 else {"added_lines": 0, "removed_lines": 0, "bounded_lines": False})
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "artifact_id": target,
            "from": dict(from_meta, provenance=old.get("provenance"), content_withheld=False),
            "to": dict(to_meta,
                       provenance=self._provenance_view(head_provenance, gate),
                       content_withheld=False),
            # ``content_changed`` keeps its old meaning: the owner's stored hash moved.
            "content_changed": hash_changed,
            "hash_changed": hash_changed,
            "body_changed": body_changed,
            "headers_differ": bool(headers_differ),
            # Honest machine-head verdict: only when both stored hashes AND the stripped bodies
            # agree is this "the head moved, the text did not".
            "machine_head_only": bool(hash_changed is False and body_changed is False),
            "identical_version": str(old.get("version_id")) == str(head.get("version_id")),
            "char_delta": len(head_body) - len(from_body),
            "lines": lines,
            "first_difference": (_first_difference(left, right) if body_changed else None),
            "comparison": {
                "basis": basis,
                "bounded_chars": MAX_COMPARE_CHARS,
                "from_truncated": left_cut,
                "to_truncated": right_cut,
                "marker_stripped": True,
            },
            "source_gate": gate,
            "reason_code": reason,
            "private_text_included": True,
        }

    def read_project_provenance(self, artifact_id: str) -> dict[str, Any]:
        """Where a sourced work came from, read from its **first** version (immutable forever).

        A later version does not have to repeat the header: the first version row cannot be
        rewritten (the owner's triggers refuse it), so the provenance of the work cannot be
        laundered by continuing it.
        """
        target = _artifact_id(artifact_id)
        if self._is_album_artifact(target):
            answer = self._album_refusal(target)
            answer["provenance"] = None
            return answer
        try:
            page = self._list_versions(target, limit=1, before_version_number=2)
        except ProjectContractRefused as exc:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": str(exc),
                    "artifact_id": target, "provenance": None, "source": SOURCE_OWNER}
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer.update({"artifact_id": target, "provenance": None})
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, artifact_id=target,
                                     provenance=None)
        entries = page.get("entries") if isinstance(page, Mapping) else page
        entries = [row for row in (entries or []) if isinstance(row, Mapping)]
        if not entries:
            return {"ok": True, "status": STATUS_OK, "artifact_id": target, "provenance": None,
                    "reason_code": "NO_FIRST_VERSION", "source": SOURCE_OWNER}
        first = entries[-1] if isinstance(page, Mapping) else entries[0]
        number = first.get("version_number")
        if number != 1:
            return {"ok": True, "status": STATUS_OK, "artifact_id": target, "provenance": None,
                    "reason_code": "FIRST_VERSION_NOT_REACHED", "source": SOURCE_OWNER,
                    "seen_version_number": number}
        read = self._read_version_raw(target, str(first.get("version_id")))
        if not read.get("ok"):
            answer = dict(read)
            answer["provenance"] = None
            return answer
        gate = self._source_gate(target)
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "artifact_id": target,
            "version_id": read.get("version_id"),
            "version_number": read.get("version_number"),
            "provenance": self._provenance_view(read.get("provenance"), gate),
            "source_gate": gate,
            "reason_code": "SOURCE_NOT_READABLE" if gate["source_blocked"] else "",
            "private_text_included": False,
        }

    # -- v0.4 work-revisit (review A/C/F): current eligibility of every claimed source -----

    def _first_version_provenance(self, artifact_id: str) -> dict[str, Any]:
        """The source marker of the artifact's **first** version, or ``None``.

        Read from the immutable first row, never from the head: a later version dropping the
        header must not be able to launder where the work came from, and a head that carries a
        header written by a plain commit is not proof of anything either (see
        ``provenance["verified"]``).
        """
        try:
            page = self._list_versions(artifact_id, limit=1, before_version_number=2)
        except Exception:
            # Cannot read the first version at all: fail **closed** for derived-text purposes.
            # An unverified first version is treated as "claims a source it will not name", which
            # the chain walk refuses, instead of being silently treated as unsourced.
            return {"source_artifact_id": "", "unverified_first_version": True}
        entries = page.get("entries") if isinstance(page, Mapping) else page
        entries = [row for row in (entries or []) if isinstance(row, Mapping)]
        if not entries:
            return None
        first = entries[-1] if isinstance(page, Mapping) else entries[0]
        if first.get("version_number") != 1:
            return {"source_artifact_id": "", "unverified_first_version": True,
                    "reason": "FIRST_VERSION_NOT_REACHED"}
        read = self._read_version_raw(artifact_id, str(first.get("version_id")))
        if not read.get("ok"):
            return {"source_artifact_id": "", "unverified_first_version": True,
                    "reason": str(read.get("reason_code") or "FIRST_VERSION_UNREADABLE")}
        provenance = read.get("provenance")
        return dict(provenance) if isinstance(provenance, Mapping) else None

    def _source_chain(self, artifact_id: str, *, depth: int = 0,
                      seen: Optional[list] = None) -> dict[str, Any]:
        """Is every *claimed* source behind this work still readable **now**?

        This is a read-time eligibility walk, not an authorization: the marker itself is
        user-writable prose (a plain ``create``/``commit`` can contain one), so it can never
        *grant* a read -- the owner's own ownership/lifecycle checks do that at every hop.  What
        the walk decides is only whether this projection may keep serving text that was derived
        from something the subject can no longer open.

        Bounded on purpose: ``MAX_SOURCE_DEPTH`` hops, and a repeat of an artifact id is a cycle.
        No second ledger is built -- the chain is recomputed from the owner each time.
        """
        chain = list(seen or ())
        if depth > MAX_SOURCE_DEPTH:
            return {"readable": False, "reason": "SOURCE_CHAIN_TOO_DEEP", "chain": chain,
                    "blocked_at": artifact_id}
        if artifact_id in chain:
            return {"readable": False, "reason": "SOURCE_CYCLE", "chain": chain,
                    "blocked_at": artifact_id,
                    "cycle": chain[chain.index(artifact_id):] + [artifact_id]}
        chain.append(artifact_id)
        provenance = self._first_version_provenance(artifact_id)
        derived = provenance is not None
        if provenance is None:
            return {"readable": True, "reason": "", "chain": chain, "blocked_at": None,
                    "derived": False}
        source = str(provenance.get("source_artifact_id") or "")
        if not source:
            return {"readable": False, "reason": "SOURCE_ID_MISSING", "chain": chain,
                    "blocked_at": artifact_id}
        try:
            view = self.port.read_artifact(source)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            return {"readable": False,
                    "reason": str(getattr(exc, "reason", "") or "SOURCE_NOT_READABLE").upper(),
                    "chain": chain, "blocked_at": source}
        artifact = dict(view.get("artifact") or {})
        if not artifact or str(artifact.get("lifecycle")) == "TOMBSTONED":
            return {"readable": False, "reason": "SOURCE_NOT_READABLE", "chain": chain,
                    "blocked_at": source}
        deeper = self._source_chain(source, depth=depth + 1, seen=chain)
        if not deeper.get("readable"):
            return deeper
        return {"readable": True, "reason": "", "chain": deeper.get("chain") or chain,
                "blocked_at": None, "derived": derived}

    def _source_gate(self, artifact_id: str) -> dict[str, Any]:
        """The one decision every derived-text surface makes before serving anything."""
        state = self._source_chain(artifact_id)
        derived = bool(state.get("derived"))
        if not derived:
            # A blocked walk stops before it reports the flag; the target's own first version is
            # the authority on "is this work sourced at all".
            derived = self._first_version_provenance(artifact_id) is not None
        return {
            "source_chain_checked": True,
            "source_derived": derived,
            "source_chain": list(state.get("chain") or []),
            "source_blocked": not state.get("readable"),
            "source_reason_code": str(state.get("reason") or ""),
            "source_blocked_at": state.get("blocked_at"),
        }

    def _provenance_view(self, provenance: Optional[Mapping[str, Any]],
                         gate: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """The provenance a caller may see: identities always, derived prose only when legal.

        ``verified`` is deliberately ``False``: the marker lives in writable content, so this
        service cannot prove the service wrote it.  It is a *claim*, backed only by the owner's
        immutable first version -- it is never an authorization and never carries text on its own.
        """
        if provenance is None:
            return None
        out = {
            "source_artifact_id": provenance.get("source_artifact_id"),
            "source_version_id": provenance.get("source_version_id"),
            "source_version_number": provenance.get("source_version_number"),
            "source_content_sha256": provenance.get("source_content_sha256"),
            "purpose": provenance.get("purpose"),
            "taken_at": provenance.get("taken_at"),
            "verified": False,
            "verification": "SELF_DECLARED_FIRST_VERSION_MARKER",
            "note": "",
            "note_withheld": False,
        }
        if gate.get("source_blocked"):
            out["note_withheld"] = True
            out["source_reason_code"] = str(gate.get("source_reason_code") or "")
            return out
        note = provenance.get("note")
        out["note"] = note if isinstance(note, str) else ""
        return out

    def _withholding(self, gate: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        """The exact answer a blocked derived surface returns: identities yes, text no."""
        answer = {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "content_withheld": True,
            "reason_code": "SOURCE_NOT_READABLE",
            "detail": "a source this work was taken from is not readable now",
            "private_text_included": False,
            "source_gate": dict(gate),
        }
        answer.update(extra)
        return answer


    # -- writes --------------------------------------------------------------------------

    def create_project(
        self,
        *,
        title: str,
        content: str,
        operation_key: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create one project and commit its first version, as one owner command.

        The title and the text are the caller's own explicit choice: nothing here accepts a
        proposal object, so a proposal can never become a project by accident.  An empty project is
        refused -- the first version must have text -- so a "created" project is never a placeholder.
        ``proposal_id`` is echoed back so a caller can record *which* proposal it chose; it is not
        written into the owner (no marker is stamped into the artifact).
        """
        clean_title = _title(title)
        clean_content = _text(content, "content", limit=MAX_CONTENT_CHARS)
        key = _operation_key(operation_key, required=False) or project_operation_key(
            "create", self.subject_id, self.workspace_id, clean_title, clean_content)
        try:
            outcome = self.port.create_artifact(
                workspace_id=self.workspace_id,
                title=clean_title,
                content=clean_content,
                grant_id=self.grant_id,
                operation_key=key,
            )
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["operation_key"] = key
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, operation_key=key)
        answer = self._settle(outcome, operation_key=key)
        answer["title"] = clean_title
        if proposal_id:
            answer["proposal_id"] = str(proposal_id)[:120]
        return answer

    def create_derived_project(
        self,
        *,
        title: str,
        content: str,
        source_artifact_id: str,
        source_version_id: str,
        purpose: str,
        note: str = "",
        taken_at: Optional[str] = None,
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a **new** work that says, in its first version, what it was taken from.

        This is not a fork and not a restore: the source artifact is untouched (no new version of
        it, no head move), the new work is one artifact with one parent version (its own first
        version), and the provenance is recorded by reusing the existing structured-marker
        convention -- one machine-written comment line in the first version, holding the source
        artifact id, the *pinned* source version id and number, that version's stored
        ``content_sha256``, the declared purpose and a bounded note.

        The source is re-read from the owner at this moment, so an unreadable / tombstoned /
        truncated source is a named refusal instead of a citation to something nobody can open.
        A source may legitimately be a *material* (the reading material is not a project, but it is
        hers to take from): the exclusion list only keeps artifacts out of the project list.
        """
        clean_title = _title(title)
        body = _text(content, "content", limit=MAX_CONTENT_CHARS)
        wanted_purpose = str(purpose or "").strip().lower()
        if wanted_purpose not in SOURCE_PURPOSES:
            raise ProjectContractRefused(
                "purpose must be one of %s (what the old material is taken for)"
                % ", ".join(SOURCE_PURPOSES))
        source = _artifact_id(source_artifact_id)
        read = self._read_version_raw(source, source_version_id)
        if not read.get("ok"):
            answer = dict(read)
            answer["source_reason_code"] = str(read.get("reason_code") or "")
            answer["reason_code"] = REASON_SOURCE_NOT_READABLE
            answer["artifact_id"] = read.get("artifact_id")
            return answer
        if read.get("content_truncated"):
            # A cut body is not the body: never cite text that was not actually read whole.
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": REASON_SOURCE_NOT_READABLE,
                    "source_reason_code": "SOURCE_VERSION_TRUNCATED",
                    "artifact_id": source, "version_id": read.get("version_id"),
                    "source": SOURCE_OWNER}
        # ``taken_at`` is the **source version's own owner-recorded time**, never the wall clock:
        # a replayed intent must produce byte-identical content (and therefore the same owner
        # request digest), so a clock reading inside the payload would turn one intent into two.
        taken = str(taken_at or read.get("created_at") or "")
        intent = {
            "operation": "create_derived_project",
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "source_artifact_id": source,
            "source_version_id": str(read.get("version_id")),
            "title": clean_title,
            "body": body,
            "purpose": wanted_purpose,
            "note": _one_line(str(note or ""))[:MAX_SOURCE_NOTE_CHARS],
        }
        provenance = {
            "source_artifact_id": source,
            "source_version_id": intent["source_version_id"],
            "source_version_number": read.get("version_number"),
            "source_content_sha256": read.get("content_sha256"),
            "purpose": wanted_purpose,
            "note": intent["note"],
            "taken_at": taken,
        }
        first_version = work_source_marker_line(provenance) + "\n" + body
        if len(first_version) > MAX_CONTENT_CHARS:
            raise ProjectContractRefused(
                "the sourced first version exceeds this owner's content bound")
        # B: the default key covers the *whole* intent (including purpose and note), so two calls
        # that differ only in what they mean cannot share a key; and an explicit key is checked
        # against the owner's own operation row before anything is written.
        key = _operation_key(operation_key, required=False) or project_operation_key(
            *(str(intent[name]) for name in ("source_artifact_id", "source_version_id", "title",
                                             "body", "purpose", "note")))
        if operation_key:
            clash = self._explicit_key_clash(key, intent, provenance)
            if clash is not None:
                return clash
        replay = self._replay_derived(key, intent)
        if replay is not None:
            return replay
        try:
            outcome = self.port.create_artifact(
                workspace_id=self.workspace_id,
                title=clean_title,
                content=first_version,
                grant_id=self.grant_id,
                operation_key=key,
            )
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["operation_key"] = key
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, operation_key=key)
        answer = self._settle(outcome, operation_key=key)
        answer.update({
            "title": clean_title,
            "provenance": self._provenance_view(provenance, {"source_blocked": False}),
            "source_artifact_id": source,
            "source_version_id": provenance["source_version_id"],
            "purpose": wanted_purpose,
            "intent_digest": project_operation_key(
                *(str(intent[name]) for name in ("source_artifact_id", "source_version_id",
                                                 "title", "body", "purpose", "note"))),
        })
        # C: the source was read *before* the write; re-check it *after* the owner committed, so a
        # source that became unreadable in between is reported now instead of being served later.
        gate = self._source_gate(str(answer.get("artifact_id") or ""))
        answer["source_gate_after_commit"] = gate
        if gate["source_blocked"]:
            answer["source_blocked"] = True
            answer["reason_code"] = "SOURCE_NOT_READABLE_AFTER_COMMIT"
            answer["content_withheld"] = True
        return answer

    def _replay_derived(self, key: str, intent: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """The owner's own record of ``key``, if this exact intent already landed.

        Uses ``lookup_operation`` (the ManagedArtifact owner's ledger): no second store, no local
        cache.  A row whose result is *this* request answers ``already_applied``; anything else is
        left to the owner to refuse (it returns CONFLICT for a reused key with a different digest).
        """
        try:
            row = self.port.operation_row(key)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError):
            raise
        except Exception:
            return None
        if not isinstance(row, Mapping):
            return None
        try:
            result = json.loads(str(row.get("result_json") or "{}"))
        except (TypeError, ValueError):
            result = {}
        if not isinstance(result, Mapping):
            result = {}
        return {
            "ok": True,
            "status": STATUS_ALREADY_APPLIED,
            "source": SOURCE_OWNER,
            "operation_key": key,
            "artifact_id": result.get("artifact_id") or row.get("artifact_id"),
            "version_id": result.get("version_id"),
            "version_number": result.get("version_number"),
            "effect_state": EffectState.NO_CHANGE,
            "owner_status": str(row.get("result_status") or result.get("status") or ""),
            "replayed": True,
            "intent_digest": key,
            "reason_code": "OPERATION_ALREADY_LANDED",
            "detail": "this exact intent already created a work under this key",
        }

    def _explicit_key_clash(self, key: str, intent: Mapping[str, Any],
                            provenance: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Refuse a caller-supplied key that already belongs to a *different* intent.

        The owner would refuse it too (same key + different request digest = CONFLICT), but the
        refusal has to happen before any content is built, and it must be a *named* refusal so no
        caller can read an old success as an answer about its new provenance.
        """
        try:
            row = self.port.operation_row(key)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer["operation_key"] = key
            return answer
        except Exception:
            return None
        if not isinstance(row, Mapping):
            return None
        try:
            result = json.loads(str(row.get("result_json") or "{}"))
        except (TypeError, ValueError):
            result = {}
        landed_artifact = str((result or {}).get("artifact_id") or row.get("artifact_id") or "")
        if not landed_artifact:
            return None
        existing = self._first_version_provenance(landed_artifact)
        if existing is None:
            return {"ok": False, "status": STATUS_CONFLICT,
                    "reason_code": "OPERATION_KEY_REUSED",
                    "operation_key": key,
                    "artifact_id": landed_artifact,
                    "detail": "this key already created an unsourced work; use a new key",
                    "source": SOURCE_OWNER}
        same = (str(existing.get("source_artifact_id") or "") == str(provenance["source_artifact_id"])
                and str(existing.get("source_version_id") or "") == str(provenance["source_version_id"])
                and str(existing.get("purpose") or "") == str(provenance["purpose"])
                and str(existing.get("note") or "") == str(provenance["note"]))
        if same:
            return None  # identical intent: the normal replay path answers it
        return {"ok": False, "status": STATUS_CONFLICT,
                "reason_code": "OPERATION_KEY_REUSED_WITH_DIFFERENT_INTENT",
                "operation_key": key,
                "artifact_id": landed_artifact,
                "detail": "this key already created a work from a different source or purpose",
                "existing_provenance": self._provenance_view(existing, {"source_blocked": False}),
                "source": SOURCE_OWNER}

    def commit_project(
        self,
        *,
        artifact_id: str,
        content: str,
        expected_head: Optional[str],
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Commit the next version of one project.

        ``expected_head`` is the caller's compare-and-swap precondition and may not be omitted:
        the caller must have read that head first.  A head that moved in the meantime is refused
        by the owner as ``conflict`` -- the caller re-reads and reconsiders instead of silently
        overwriting somebody else's version.
        """
        target = _artifact_id(artifact_id)
        if self.is_excluded(target):
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": REASON_ARTIFACT_EXCLUDED, "artifact_id": target,
                    "source": SOURCE_OWNER}
        if self._is_album_artifact(target):
            return self._album_refusal(target)
        if not isinstance(expected_head, str) or not expected_head.strip():
            raise ProjectContractRefused(
                "expected_head is required: pass the head_version_id you read before editing")
        clean_content = _text(content, "content", limit=MAX_CONTENT_CHARS)
        head = expected_head.strip()
        key = _operation_key(operation_key, required=False) or project_operation_key(
            "commit", target, head, clean_content)
        try:
            outcome = self.port.commit_version(
                artifact_id=target,
                expected_head=head,
                content=clean_content,
                grant_id=self.grant_id,
                operation_key=key,
            )
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer.update({"operation_key": key, "artifact_id": target})
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, operation_key=key,
                                     artifact_id=target)
        answer = self._settle(outcome, operation_key=key, artifact_id=target)
        return answer

    def close_project(
        self,
        *,
        artifact_id: str,
        expected_revision: int,
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Close one project with the owner's ``archive_artifact`` verb -- never a delete.

        ``expected_revision`` is the artifact ``revision`` the caller read before deciding (not a
        version id).  Archiving keeps every version, the head and the text exactly where they are:
        the project simply stops being ACTIVE, and it stays readable and listable afterwards.
        """
        target = _artifact_id(artifact_id)
        if self.is_excluded(target):
            return {"ok": False, "status": STATUS_NOT_FOUND,
                    "reason_code": REASON_ARTIFACT_EXCLUDED, "artifact_id": target,
                    "source": SOURCE_OWNER}
        if self._is_album_artifact(target):
            return self._album_refusal(target)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 1:
            raise ProjectContractRefused(
                "expected_revision must be the integer artifact revision read from the owner (>= 1)")
        key = _operation_key(operation_key, required=False) or project_operation_key(
            "close", target, str(int(expected_revision)))
        try:
            outcome = self.port.archive_artifact(
                artifact_id=target,
                expected_revision=int(expected_revision),
                grant_id=self.grant_id,
                operation_key=key,
            )
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc)
            answer.update({"operation_key": key, "artifact_id": target})
            return answer
        except Exception as exc:
            return self._unavailable("PROJECT_PORT_ERROR", exc, operation_key=key,
                                     artifact_id=target)
        return self._settle(outcome, operation_key=key, artifact_id=target)

    # -- views ---------------------------------------------------------------------------

    def _project_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """One public project row, built only from the owner's own artifact row."""
        lifecycle = str(row.get("lifecycle") or "")
        return {
            "artifact_id": str(row.get("artifact_id") or ""),
            "title": str(row.get("title") or ""),
            "lifecycle": lifecycle,
            "status": "archived" if lifecycle == LIFECYCLE_ARCHIVED else "active",
            "revision": row.get("revision"),
            "head_version_id": row.get("head_version_id"),
            "version_number": None,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "workspace_id": str(row.get("workspace_id") or ""),
            "source": SOURCE_OWNER,
        }

    def _history(self, artifact_id: str, head: Mapping[str, Any], content: Any, *,
                 limit: int, before_version_number: Optional[int] = None) -> dict[str, Any]:
        """Every version the owner reports, paged and honestly bounded.

        Two port spellings are supported without favouring either: the v0.4 port answers with a
        dict (``entries`` + ``complete`` + cursor + ``total_versions``); an older port answers with
        a plain sequence and no cursor.  A plain sequence is reported ``complete`` when it is
        non-empty exactly as before, so nothing that used to be listed stops being listed.
        """
        cap = max(1, int(limit))
        entries: list[dict[str, Any]] = []
        complete = False
        reason = REASON_PORT_LACKS_LIST_VERSIONS
        total: Optional[int] = None
        next_cursor: Optional[int] = None
        source = SOURCE_OWNER
        lister = getattr(self.port, "list_versions", None)
        if callable(lister):
            try:
                raw = self._list_versions(artifact_id, limit=cap,
                                          before_version_number=before_version_number)
            except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
                failure = self._owner_failure(exc)
                return {"complete": False, "entries": [],
                        "reason_code": str(failure.get("reason_code") or "PROJECT_PORT_ERROR"),
                        "detail": str(failure.get("detail") or ""),
                        "source": SOURCE_OWNER, "count": 0}
            except Exception as exc:
                return {"complete": False, "entries": [],
                        "reason_code": "PROJECT_PORT_ERROR",
                        "detail": type(exc).__name__, "source": SOURCE_OWNER, "count": 0}
            if isinstance(raw, Mapping):
                rows = raw.get("entries")
                rows = rows if isinstance(rows, list) else []
                complete = bool(raw.get("complete"))
                reason = str(raw.get("reason_code") or "") or (
                    "" if complete else "HISTORY_INCOMPLETE")
                total = raw.get("total_versions") if isinstance(raw.get("total_versions"), int) \
                    else None
                cursor = raw.get("next_before_version_number")
                next_cursor = cursor if isinstance(cursor, int) and not isinstance(cursor, bool) \
                    else None
                source = str(raw.get("source") or SOURCE_OWNER)
            else:
                rows = list(raw or ())
                complete = bool(rows)
                reason = "" if rows else REASON_PORT_LACKS_LIST_VERSIONS
            for item in rows:
                if not isinstance(item, Mapping) or not item.get("version_id"):
                    continue
                content_chars = item.get("content_chars")
                if not isinstance(content_chars, int) or isinstance(content_chars, bool):
                    content_chars = (len(item["content"])
                                     if isinstance(item.get("content"), str) else None)
                entries.append({
                    "version_id": str(item.get("version_id")),
                    "version_number": item.get("version_number"),
                    "parent_version_id": item.get("parent_version_id"),
                    "created_at": item.get("created_at"),
                    "operation_key": item.get("operation_key"),
                    "content_chars": content_chars,
                    "content_sha256": item.get("content_sha256"),
                    "is_head": str(item.get("version_id")) == str(head.get("version_id")),
                })
        if not entries and head.get("version_id"):
            entries.append({
                "version_id": str(head.get("version_id")),
                "version_number": head.get("version_number"),
                "parent_version_id": head.get("parent_version_id"),
                "created_at": head.get("created_at"),
                "operation_key": head.get("operation_key"),
                "content_chars": (len(content) if isinstance(content, str) else None),
                "content_sha256": head.get("content_sha256"),
                "is_head": True,
            })
        entries.sort(key=lambda entry: (
            -(entry.get("version_number") if isinstance(entry.get("version_number"), int)
              and not isinstance(entry.get("version_number"), bool) else 0)))
        out = {
            "complete": bool(complete),
            "reason_code": reason,
            "entries": entries[:cap],
            "count": len(entries),
            "total_versions": total,
            "has_more": next_cursor is not None,
            "next_before_version_number": next_cursor,
            "source": source,
        }
        return out

    # -- answers -------------------------------------------------------------------------

    def _owner_failure(self, exc: Exception) -> dict[str, Any]:
        """Map one port refusal onto a status, keeping the owner's own reason verbatim."""
        name = type(exc).__name__
        if name == "LifeArtifactUnavailable":
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": "SUPPLY_UNAVAILABLE",
                    "detail": str(exc)[:200], "source": SOURCE_OWNER}
        owner_status = str(getattr(exc, "status", "") or "")
        reason = str(getattr(exc, "reason", "") or "") or owner_status or "OWNER_REFUSED"
        detail = str(getattr(exc, "detail", "") or str(exc))[:200]
        status = STATUS_NOT_FOUND if owner_status == "NOT_FOUND" else STATUS_UNAVAILABLE
        return {"ok": False, "status": status,
                "reason_code": reason.upper()[:64], "owner_status": owner_status,
                "detail": detail, "source": SOURCE_OWNER}

    def _unavailable(self, reason_code: str, exc: Exception, **extra: Any) -> dict[str, Any]:
        answer = {"ok": False, "status": STATUS_UNAVAILABLE, "reason_code": reason_code,
                  "detail": type(exc).__name__, "source": SOURCE_OWNER}
        answer.update(extra)
        return answer

    def _settle(self, outcome: ArtifactOutcome, *, operation_key: str,
                artifact_id: Optional[str] = None) -> dict[str, Any]:
        """Classify one owner answer, resolving an UNKNOWN before anyone retries it."""
        state = outcome.effect_state
        base: dict[str, Any] = {
            "source": SOURCE_OWNER,
            "operation_key": operation_key,
            "artifact_id": outcome.artifact_id or artifact_id,
            "effect_state": state,
            "owner_status": outcome.status,
            "version_id": outcome.version_id,
            "version_number": outcome.version_number,
            "head_version_id": outcome.head_version_id,
            "parent_version_id": outcome.parent_version_id,
            "lifecycle": outcome.lifecycle,
            "revision": outcome.revision,
            "reason_code": "",
            "detail": outcome.detail or "",
            "reconciled": False,
        }
        if state == EffectState.APPLIED:
            base.update({"ok": True, "status": STATUS_APPLIED})
            return base
        if state == EffectState.NO_CHANGE:
            base.update({"ok": True, "status": STATUS_ALREADY_APPLIED})
            return base
        if state == EffectState.CONFLICT:
            base.update({"ok": False, "status": STATUS_CONFLICT,
                         "reason_code": str(outcome.reason or "CAS_CONFLICT").upper()[:64]})
            return base
        if state == EffectState.NOT_FOUND:
            base.update({"ok": False, "status": STATUS_NOT_FOUND,
                         "reason_code": str(outcome.reason or "ARTIFACT_NOT_FOUND").upper()[:64]})
            return base
        if state == EffectState.REFUSED:
            base.update({"ok": False, "status": STATUS_REJECTED,
                         "reason_code": str(outcome.reason or outcome.status
                                            or "OWNER_REFUSED").upper()[:64]})
            return base
        # UNKNOWN: ask the owner what became of *this* key before anyone retries it.
        try:
            resolved = self.port.resolve_operation(operation_key)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            base.update(self._owner_failure(exc))
            base["retryable"] = False
            base["effect_state"] = EffectState.UNKNOWN
            base["operation_key"] = operation_key
            return base
        except Exception as exc:
            unavailable = self._unavailable("PROJECT_PORT_ERROR", exc)
            base.update(unavailable)
            base["retryable"] = False
            base["effect_state"] = EffectState.UNKNOWN
            base["operation_key"] = operation_key
            return base
        if resolved.effect_state == EffectState.APPLIED:
            base.update({"ok": True, "status": STATUS_APPLIED, "reconciled": True,
                         "reason_code": REASON_RECONCILED,
                         "version_id": resolved.version_id or base.get("version_id"),
                         "artifact_id": resolved.artifact_id or base.get("artifact_id")})
            return base
        if resolved.effect_state == EffectState.NO_CHANGE:
            # The owner positively knows nothing landed: the caller may retry with the *same* key.
            base.update({"ok": False, "status": STATUS_UNKNOWN, "retryable": True,
                         "reconciled": True, "reason_code": REASON_OPERATION_NEVER_LANDED})
            return base
        base.update({"ok": False, "status": STATUS_UNKNOWN, "retryable": False,
                     "reason_code": REASON_RESOLUTION_INCONCLUSIVE})
        return base


def default_excluded_artifact_ids(*artifact_ids: Optional[str]) -> tuple[str, ...]:
    """The non-project artifacts a phone/life instance already names (reading + private items).

    A helper only: the caller decides what is a material and what is a project.  ``None`` and
    empty values are simply absent.
    """
    out: list[str] = []
    for value in artifact_ids:
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if text not in out:
            out.append(text)
    return tuple(sorted(out))


# ---------------------------------------------------------------------------------------------
# A's documented seam (``life_project_options.ProjectOwnerGateway``)
# ---------------------------------------------------------------------------------------------
# A's gateway expects an owner whose ``list_projects``/``create_project`` answer with an
# *uppercase* ``status``, and whose create call is spelled
# ``create_project(title=, seed_text=, source_refs=, operation_key=, workspace_id=, subject_id=)``.
# The adapter below is that spelling over the same service: it adds no second store and no second
# semantics, renames nothing the owner said and invents nothing.

#: Environment names a Life runtime may already carry.  The first non-empty one wins.
ENV_SOCKET_NAMES = ("NYAIRO_LIFE_SUPPLY_SOCKET", "CHIYO_LIFE_SUPPLY_SOCKET",
                    "LIFE_SUPPLY_SOCKET")
ENV_WORKSPACE_NAMES = ("NYAIRO_LIFE_WORKSPACE_ID", "CHIYO_LIFE_SUPPLY_WORKSPACE_ID",
                       "LIFE_SUPPLY_WORKSPACE_ID")
ENV_GRANT_NAMES = ("NYAIRO_ARTIFACT_GRANT", "NYA02_ARTIFACT_GRANT",
                   "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT", "LIFE_SUPPLY_ARTIFACT_GRANT")
ENV_SUBJECT_NAMES = ("NYAIRO_LIFE_SUBJECT", "CHIYO_LIFE_SUPPLY_SUBJECT",
                     "LIFE_SUPPLY_SUBJECT")
#: Comma separated artifact ids that are *not* projects (reading material, private-items doc).
ENV_EXCLUDED_NAMES = ("NYAIRO_LIFE_NON_PROJECT_ARTIFACTS",)

#: The uppercase statuses A's seam speaks.
OWNER_STATUS_OK = "OK"
OWNER_STATUS_REFUSED = "REFUSED"
OWNER_STATUS_CONFLICT = "CONFLICT"
OWNER_STATUS_UNAVAILABLE = "UNAVAILABLE"
OWNER_STATUS_UNKNOWN = "UNKNOWN"


def _first_env(environment: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = environment.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class LifeProjectOwner:
    """A's ``ProjectOwnerGateway`` seam onto one :class:`PrivateProjectService`.

    Two methods, exactly the shape A documented:

    * ``list_projects()`` -> ``{"status": "OK", "projects": [...], "counts": {...}}``
    * ``create_project(*, title, seed_text, source_refs, operation_key, workspace_id,
      subject_id)`` -> ``{"status": "OK", "artifact_id", "version_id", ...}``

    ``seed_text`` is the project's first text (the owner's first committed version) and
    ``source_refs`` are echoed back to the caller only -- they are never written into the
    artifact, because a project is her text, not a citation table.  A ``workspace_id`` /
    ``subject_id`` that disagrees with the ones this owner is wired to is a refusal, never a
    silent redirect.
    """

    def __init__(self, service: PrivateProjectService) -> None:
        self.service = service

    # -- A's reads -----------------------------------------------------------------------

    def list_projects(self) -> dict[str, Any]:
        answer = self.service.list_projects()
        if not answer.get("ok"):
            return {"status": OWNER_STATUS_UNAVAILABLE,
                    "reason": str(answer.get("reason_code")
                                  or answer.get("status") or "PROJECTS_UNAVAILABLE"),
                    "projects": [], "counts": dict(answer.get("counts") or {})}
        return {"status": OWNER_STATUS_OK,
                "projects": list(answer.get("projects") or []),
                "counts": dict(answer.get("counts") or {}),
                "excluded_artifact_ids": list(answer.get("excluded_artifact_ids") or []),
                "source": answer.get("source")}

    def read_project(self, artifact_id: str, **kwargs: Any) -> dict[str, Any]:
        answer = self.service.read_project(artifact_id, **kwargs)
        if not answer.get("ok"):
            return {"status": OWNER_STATUS_UNAVAILABLE,
                    "reason": str(answer.get("reason_code")
                                  or answer.get("status") or "PROJECT_UNAVAILABLE"),
                    "artifact_id": str(artifact_id)}
        return {"status": OWNER_STATUS_OK, "artifact_id": answer.get("artifact_id"),
                "project": answer.get("project") or {},
                "history": answer.get("history") or {}}

    # -- A's one write -------------------------------------------------------------------

    def create_project(
        self,
        *,
        title: str,
        seed_text: Optional[str] = None,
        content: Optional[str] = None,
        source_refs: Iterable[str] = (),
        operation_key: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if workspace_id and str(workspace_id) != self.service.workspace_id:
            return {"status": OWNER_STATUS_REFUSED, "reason": "WORKSPACE_MISMATCH",
                    "detail": "this owner is wired to another workspace"}
        if subject_id and str(subject_id) != self.service.subject_id:
            return {"status": OWNER_STATUS_REFUSED, "reason": "SUBJECT_MISMATCH",
                    "detail": "this owner is wired to another subject"}
        text = content if content is not None else seed_text
        if not isinstance(text, str) or not text.strip():
            return {"status": OWNER_STATUS_REFUSED, "reason": "PROJECT_TEXT_REQUIRED",
                    "detail": "seed_text (or content) is required"}
        refs = [str(ref) for ref in (source_refs or ()) if isinstance(ref, str) and ref]
        answer = self.service.create_project(title=title, content=text,
                                            operation_key=operation_key,
                                            proposal_id=proposal_id)
        status = str(answer.get("status") or "")
        if answer.get("ok") and status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return {"status": OWNER_STATUS_OK,
                    "artifact_id": answer.get("artifact_id"),
                    "version_id": answer.get("version_id"),
                    "version_number": answer.get("version_number"),
                    "operation_key": answer.get("operation_key"),
                    "already_applied": status == STATUS_ALREADY_APPLIED,
                    "reconciled": bool(answer.get("reconciled")),
                    "source_refs": refs,
                    "owner_status": answer.get("owner_status")}
        mapped = {
            STATUS_CONFLICT: OWNER_STATUS_CONFLICT,
            STATUS_UNKNOWN: OWNER_STATUS_UNKNOWN,
            STATUS_NOT_FOUND: OWNER_STATUS_REFUSED,
            STATUS_REJECTED: OWNER_STATUS_REFUSED,
            STATUS_UNAVAILABLE: OWNER_STATUS_UNAVAILABLE,
        }.get(status, OWNER_STATUS_REFUSED)
        return {"status": mapped,
                "reason": str(answer.get("reason_code") or status or "OWNER_REFUSED"),
                "detail": str(answer.get("detail") or "")[:200],
                "operation_key": answer.get("operation_key"),
                "source_refs": refs}

    # -- passthroughs (a caller that already holds this owner needs no second object) -----

    def commit_project(self, **kwargs: Any) -> dict[str, Any]:
        return self.service.commit_project(**kwargs)

    def close_project(self, **kwargs: Any) -> dict[str, Any]:
        return self.service.close_project(**kwargs)

    def describe(self) -> dict[str, Any]:
        described = self.service.describe()
        described["adapter"] = "life_project_owner"
        return described



def life_supply_port_from_env(environment: Optional[Mapping[str, str]] = None) -> Any:
    """Build the narrow artifact port from the environment a Life/phone runtime carries.

    Raises ``ProjectUnavailable`` when the socket or the subject is missing: a half-wired owner
    is never turned into a half-working one.
    """
    env = dict(environment if environment is not None else {})
    socket_path = _first_env(env, ENV_SOCKET_NAMES)
    subject = _first_env(env, ENV_SUBJECT_NAMES)
    if not socket_path:
        raise ProjectUnavailable("SUPPLY_SOCKET_NOT_CONFIGURED")
    if not subject:
        raise ProjectUnavailable("SUBJECT_NOT_CONFIGURED")
    from life_artifact_port import LifeArtifactPort  # local: importing it eagerly is not needed

    return LifeArtifactPort(socket_path, subject)


def private_projects_from_env(environment: Optional[Mapping[str, str]] = None) -> LifeProjectOwner:
    """The factory A's ``resolve_gateway`` looks for.  Nothing is guessed.

    It answers a :class:`LifeProjectOwner`, so ``resolve_gateway(private_project_service)`` wires
    the real owner with no edit on either side.  When the environment does not name a socket,
    workspace, grant and subject it raises, and A's gateway answers
    ``PRIVATE_PROJECT_OWNER_UNAVAILABLE`` -- never a stand-in store.
    """
    env = dict(environment if environment is not None else {})
    workspace_id = _first_env(env, ENV_WORKSPACE_NAMES)
    grant_id = _first_env(env, ENV_GRANT_NAMES)
    if not workspace_id:
        raise ProjectUnavailable("WORKSPACE_NOT_CONFIGURED")
    if not grant_id:
        raise ProjectUnavailable("ARTIFACT_GRANT_NOT_CONFIGURED")
    port = life_supply_port_from_env(env)
    excluded_raw = _first_env(env, ENV_EXCLUDED_NAMES) or ""
    excluded = default_excluded_artifact_ids(*excluded_raw.split(","))
    service = PrivateProjectService(port, workspace_id, grant_id,
                                    excluded_artifact_ids=excluded)
    return LifeProjectOwner(service)


#: The other names A's ``resolve_gateway`` accepts for the same factory.
service_from_env = private_projects_from_env
from_env = private_projects_from_env
private_project_service_from_env = private_projects_from_env


__all__ = [
    "ENV_EXCLUDED_NAMES",
    "ENV_GRANT_NAMES",
    "ENV_SOCKET_NAMES",
    "ENV_SUBJECT_NAMES",
    "ENV_WORKSPACE_NAMES",
    "LIFECYCLE_ACTIVE",
    "LIFECYCLE_ARCHIVED",
    "LifeProjectOwner",
    "MAX_CONTENT_CHARS",
    "MAX_HISTORY",
    "MAX_LIST",
    "MAX_TITLE_CHARS",
    "OPTIONAL_PORT_METHODS",
    "OWNER_STATUS_CONFLICT",
    "OWNER_STATUS_OK",
    "OWNER_STATUS_REFUSED",
    "OWNER_STATUS_UNAVAILABLE",
    "OWNER_STATUS_UNKNOWN",
    "PrivateProjectError",
    "PrivateProjectService",
    "ProjectContractRefused",
    "ProjectUnavailable",
    "REASON_ARTIFACT_EXCLUDED",
    "REASON_OPERATION_NEVER_LANDED",
    "REASON_PORT_LACKS_LIST_VERSIONS",
    "REASON_RECONCILED",
    "REQUIRED_PORT_METHODS",
    "SOURCE_OWNER",
    "STATUS_ALREADY_APPLIED",
    "STATUS_APPLIED",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "default_excluded_artifact_ids",
    "from_env",
    "life_supply_port_from_env",
    "private_project_service_from_env",
    "private_projects_from_env",
    "project_operation_key",
    "service_from_env",
]
