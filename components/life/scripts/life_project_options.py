#!/usr/bin/env python3
"""v0.4 stage A: turn what she already has into *choices* for a new private project.

Why this module exists
----------------------
The day loop could only continue a work that already existed (or the one artifact bound at
start-up), so "what she does next" was decided by which artifact happened to be bound, never by
her.  This module produces bounded, source-backed **options** for a *new* private project out of
things that really exist:

* a question she wrote down while reading (her own private ``unclear`` marker),
* a "later" item she already proposed whose time has come,
* and the projects she already has (used to *avoid* proposing a duplicate, and to report what
  is already hers -- continuing those is the artifact continuation path's job, not a new one).

What it never does
------------------
It creates nothing by itself.  An option is a proposal; a Decision has to select it, and only
then does the narrow gateway below ask the *project owner* to create the work.  The owner is
B's service (``private_project_service``); when no owner is wired the gateway answers
``PRIVATE_PROJECT_OWNER_UNAVAILABLE`` and nothing is written -- no stand-in artifact, no local
copy of the project store, no second library of works.
Also deliberately absent: a global "LifeManager", a scoring of options, a queue, and any
automatic choice.  Everything here is 0 LLM and text-bounded.

v0.4 research (stage A + B): when a research workspace is really wired, a question seed becomes
one ``KIND_RESEARCH`` option instead.  Its ``materials`` are exactly the hits the workspace just
returned, with the owner's own ``artifact_id`` + ``source_revision`` -- and an empty list when it
returned none, in which case the option says so in its own ``uncertainty`` rather than pretending
to have sources.  The option is still only an option: B's service creates the research work, and
only after a Decision selected that very option.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.life.project_options.v1"

OPTION_REF_PREFIX = "popt"
OPTION_REF_NAMESPACE = f"{OPTION_REF_PREFIX}:"

KIND_QUESTION = "QUESTION_FROM_MATERIAL"
#: v0.4 stage A/B: a question that becomes a *research work* (an owner artifact carrying B's
#: research marker) rather than a blank private project.  It is offered only when the research
#: workspace is really wired, because the alternative is a menu of options that fail when chosen.
KIND_RESEARCH = "RESEARCH_FROM_QUESTION"
KIND_FUTURE_ITEM = "FUTURE_ITEM_DUE"
ALL_KINDS = (KIND_QUESTION, KIND_FUTURE_ITEM, KIND_RESEARCH)
#: v0.4 C: a **review** of her own governance-cleared experiences -- created by B's practice
#: workspace as a real ``ManagedArtifact``, not by the plain project gateway.  It is offered only
#: when that workspace is wired *and* A's Memory really returns an experience to review, so the
#: option can never be a promise to write about a past she cannot read.
KIND_REVIEW = "REVIEW_FROM_EXPERIENCE"
ALL_KINDS = (KIND_QUESTION, KIND_FUTURE_ITEM, KIND_RESEARCH, KIND_REVIEW)
#: How many experiences one review option names.  Two is enough to see the material of a review
#: without turning the decision prompt into a memory dump.
MAX_REVIEW_EXPERIENCES = 2
#: What a review option says about itself when it has no experience behind it.  Such an option is
#: never offered (see :func:`build_options`), so this string only guards the wording.
NO_EXPERIENCE_UNCERTAINTY = "暂定，尚未验证；当前没有可读经历，只是一份问题"

MAX_OPTIONS = 2
MAX_TITLE_CHARS = 80
MAX_SEED_CHARS = 600
MAX_PROJECTS_REPORTED = 12
#: How much of a material the option (and therefore the prompt) may carry, and how many hits.
MAX_RESEARCH_MATERIALS = 3
MAX_MATERIAL_EXCERPT_CHARS = 160
#: What a fresh research note says about its own standing.  Never "verified": nothing is.
DEFAULT_UNCERTAINTY = "暂定，尚未验证"
NO_MATERIAL_UNCERTAINTY = "暂定，尚未验证；当前没有可读资料，问题保持开放"

#: What the gateway says when B's owner service is not wired.  A named refusal, never a fake.
REASON_OWNER_UNAVAILABLE = "PRIVATE_PROJECT_OWNER_UNAVAILABLE"
REASON_OWNER_REFUSED = "PRIVATE_PROJECT_OWNER_REFUSED"
REASON_OWNER_UNUSABLE = "PRIVATE_PROJECT_OWNER_UNUSABLE"
REASON_NO_WORKSPACE = "PRIVATE_PROJECT_WORKSPACE_MISSING"

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_\-]+")


class ProjectOptionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}:{detail}" if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _safe_ref_id(value: Any) -> str:
    """A structured-ref-safe token for an owner id we are only *naming*, never re-interpreting."""

    text = _SAFE_ID_RE.sub("-", str(value or "")).strip("-")
    if not text:
        text = _sha(str(value or ""))[:16]
    return text[:80]


def _artifact_id_of(record: Mapping[str, Any]) -> Optional[str]:
    raw = record.get("artifact_id") or record.get("project_ref") or record.get("id")
    if not raw:
        return None
    text = str(raw)
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text or None


@dataclass
class ProjectOption:
    """One thing she could start writing, and exactly where it came from."""

    option_ref: str
    kind: str
    title: str
    seed_text: str
    source_refs: list[str] = field(default_factory=list)
    basis_ref: Optional[str] = None
    material_ref: Optional[str] = None
    note: Optional[str] = None
    #: v0.4 stage A/B: research-only fields.  ``materials`` are the *real* hits this option was
    #: built from (owner id + the revision the hit was read at); ``scope``/``uncertainty`` are what
    #: the created research document will state about itself.  They are empty for an ordinary
    #: project option, which carries no claim about sources at all.
    materials: list[dict[str, Any]] = field(default_factory=list)
    scope: str = ""
    uncertainty: str = ""
    #: v0.4 C: review-only field.  ``experiences`` are the *real* recallable experiences the
    #: review would cite (id + when + a bounded excerpt), read through A's Memory just now.  Empty
    #: for every other kind, and a review option is never built on an empty list.
    experiences: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "option_ref": self.option_ref,
            "kind": self.kind,
            "title": self.title,
            "seed_text": self.seed_text,
            "source_refs": list(self.source_refs),
            "basis_ref": self.basis_ref,
            "material_ref": self.material_ref,
            "note": self.note,
            #: True only for an option that starts a research work through B's service.
            "research": self.kind == KIND_RESEARCH,
            "materials": [dict(row) for row in self.materials],
            "scope": self.scope,
            "uncertainty": self.uncertainty,
            #: True only for an option that asks B's practice workspace for a review.
            "review": self.kind == KIND_REVIEW,
            "experiences": [dict(row) for row in self.experiences],
            #: The candidate a project option becomes.  Kept byte-identical to ``option_ref`` so a
            #: Decision selecting the candidate and this module reading the option name one thing.
            "target_ref": self.option_ref,
        }


# ---------------------------------------------------------------------------------------------
# sources -> options
# ---------------------------------------------------------------------------------------------


def _artifact_id_of(record: Mapping[str, Any]) -> Optional[str]:
    """The owner's own id for one created project, **verbatim**.

    The ManagedArtifact owner spells its ids ``artifact:<id>`` and answers ``NOT_FOUND`` for a
    bare one, so this seam must not "normalise" the id: whatever the owner confirmed is what the
    caller passes back to it.  The cycle normalises when it builds an Activity target, not here.
    """
    raw = (record.get("artifact_id") or record.get("project_ref") or record.get("id"))
    if not raw:
        return None
    text = str(raw)
    return text or None

def question_seeds(rows: Optional[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Normalise her own private ``unclear`` markers into bounded seeds.

    Only what the private document already holds: the note she wrote and the refs it was written
    against.  An empty note is not a seed -- a question that says nothing cannot start a project.
    """
    seeds: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        note = _one_line(row.get("note"), MAX_SEED_CHARS)
        if not note:
            continue
        marker_id = str(row.get("marker_id") or row.get("item_id") or "")
        source_id = str(row.get("source_id") or "")
        revision = str(row.get("source_revision") or "")
        refs = [f"pnote:{_safe_ref_id(marker_id)}"]
        if source_id:
            refs.append(f"material:{_safe_ref_id(source_id)}")
        seeds.append({
            "text": note,
            "marker_id": marker_id,
            "source_id": source_id,
            "source_revision": revision,
            "source_refs": refs,
        })
    seeds.sort(key=lambda seed: (seed["source_id"], seed["marker_id"]))
    return seeds


def _title_from(text: str, *, prefix: str) -> str:
    clean = _one_line(text, MAX_TITLE_CHARS)
    if not clean:
        return prefix
    return f"{prefix}{clean}"


def materials_view(materials: Optional[Iterable[Mapping[str, Any]]],
                   *, limit: int = MAX_RESEARCH_MATERIALS) -> list[dict[str, Any]]:
    """The real material hits an option may cite, bounded, with nothing invented.

    A hit without an owner ``artifact_id`` is not a hit: it is dropped.  The ``source_revision``
    is kept **verbatim** as the workspace reported it, because the research document has to name
    the version it actually read -- a normalised or guessed revision would be a different claim.
    """

    rows: list[dict[str, Any]] = []
    cap = max(0, int(limit))
    for row in materials or ():
        if not isinstance(row, Mapping):
            continue
        artifact_id = str(row.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        rows.append({
            "artifact_id": artifact_id,
            "source_revision": str(row.get("source_revision") or ""),
            "title": _one_line(row.get("title"), MAX_TITLE_CHARS),
            "source_kind": _one_line(row.get("source_kind"), 40),
            "origin": _one_line(row.get("origin"), 80),
            "excerpt": str(row.get("excerpt") or "")[:MAX_MATERIAL_EXCERPT_CHARS],
        })
        if len(rows) >= cap:
            break
    return rows


def material_refs(materials: Iterable[Mapping[str, Any]]) -> list[str]:
    """``artifact:<id>@<revision>`` spellings of the hits, for a candidate's provenance only."""

    refs: list[str] = []
    for row in materials or ():
        if not isinstance(row, Mapping):
            continue
        artifact_id = str(row.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        revision = str(row.get("source_revision") or "").strip()
        refs.append(f"{artifact_id}@{revision}" if revision else artifact_id)
    return refs


def _research_scope(materials: list[dict[str, Any]]) -> str:
    """What a research note may claim to be about: the material titles it actually has."""

    named: list[str] = []
    for row in materials:
        title = row.get("title") or row.get("artifact_id") or ""
        revision = row.get("source_revision") or ""
        named.append(f"{title}（{revision}）" if revision else str(title))
    if not named:
        return "只围绕她写下的这个问题；还没有可读资料"
    return "只依据：" + "；".join(named)

def review_seed(experiences: Optional[Iterable[Mapping[str, Any]]], *,
                available: bool = False,
                limit: int = MAX_REVIEW_EXPERIENCES) -> Optional[dict[str, Any]]:
    """Turn the experiences A's Memory really returned into **one** review option (or ``None``).

    A review is "what did I actually see, where did I see it, what is still unknown" -- so its
    material is the recallable experiences themselves, bounded and attributable, and its own
    ``uncertainty`` says it is a draft.  With no readable experience (or no practice workspace to
    write it into) there is no option: nothing is invented to fill the slot.
    """

    if not available:
        return None
    rows: list[dict[str, Any]] = []
    for row in experiences or ():
        if not isinstance(row, Mapping):
            continue
        experience_id = str(row.get("experience_id") or "").strip()
        if not experience_id:
            continue
        rows.append({
            "experience_id": experience_id,
            "occurred_at": str(row.get("occurred_at") or ""),
            "excerpt": _one_line(row.get("excerpt") or row.get("text"),
                                 MAX_MATERIAL_EXCERPT_CHARS),
            "artifact_ref": str(row.get("artifact_ref") or ""),
            "version_ref": str(row.get("version_ref") or ""),
            "outcome_kind": str(row.get("outcome_kind") or ""),
        })
        if len(rows) >= max(1, int(limit)):
            break
    if not rows:
        return None
    material = "；".join(
        "%s%s" % (row["excerpt"] or row["experience_id"],
                  "（%s）" % row["occurred_at"] if row["occurred_at"] else "")
        for row in rows)
    question = _one_line("回看确实发生过的这些事：%s" % material, MAX_SEED_CHARS)
    title = _one_line("回顾：%s" % (rows[0]["excerpt"] or rows[0]["experience_id"]),
                      MAX_TITLE_CHARS)
    return {
        "title": title,
        "question": question,
        "experiences": rows,
        "scope": _one_line("只依据这几条现在仍能读回来的经历；其他时候的事不在此列",
                           MAX_SEED_CHARS),
        "uncertainty": DEFAULT_UNCERTAINTY,
    }



def build_options(*, questions: Optional[Iterable[Mapping[str, Any]]] = None,
                  due_items: Optional[Iterable[Mapping[str, Any]]] = None,
                  existing_projects: Optional[Iterable[Mapping[str, Any]]] = None,
                  research_materials: Optional[Iterable[Mapping[str, Any]]] = None,
                  research_available: bool = False,
                  experiences: Optional[Iterable[Mapping[str, Any]]] = None,
                  practice_available: bool = False,
                  limit: int = MAX_OPTIONS) -> list[dict[str, Any]]:
    """Build at most ``limit`` project options, deterministically ordered (0 LLM).

    A source that is empty produces nothing: no option is invented to fill a quota.  A seed that
    would duplicate a title she already has a project for is skipped and reported by absence, not
    by a silent rewrite of the title.

    ``research_available`` says a research workspace is really wired.  When it is, a question seed
    becomes **one** research option (never two options for the same seed) whose ``materials`` are
    exactly the hits the workspace just returned -- an empty list when it returned none, in which
    case the option says so in its own ``uncertainty`` instead of pretending it has sources.

    ``practice_available`` says B's practice workspace is really wired, and ``experiences`` are
    the experiences A's Memory returned for *this* round.  Only when both are true does she get
    **one** review option -- a review of what she can still read back, whose ``experiences`` are
    exactly those rows.  No experience means no review option at all: "write about a past you
    cannot read" is not a choice, it is a trap.
    """

    existing_titles = {
        _one_line(record.get("title"), MAX_TITLE_CHARS).casefold()
        for record in (existing_projects or ()) if isinstance(record, Mapping)
    }
    hits = materials_view(research_materials) if research_available else []
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    review_row = review_seed(experiences, available=practice_available)
    if review_row is not None:
        title = _one_line(review_row["title"], MAX_TITLE_CHARS)
        if title.casefold() not in existing_titles:
            identity = "|".join([KIND_REVIEW] + [row["experience_id"]
                                                for row in review_row["experiences"]])
            ref = f"{OPTION_REF_NAMESPACE}{_sha(identity)[:16]}"
            seen.add(ref)
            options.append(ProjectOption(
                option_ref=ref, kind=KIND_REVIEW, title=title,
                seed_text=_one_line(review_row["question"], MAX_SEED_CHARS),
                source_refs=[f"experience:{row['experience_id']}"
                             for row in review_row["experiences"]],
                basis_ref=(f"experience:{_safe_ref_id(review_row['experiences'][0]['experience_id'])}"),
                note=("experiences=%d" % len(review_row["experiences"])),
                experiences=list(review_row["experiences"]),
                scope=review_row["scope"], uncertainty=review_row["uncertainty"],
            ).to_dict())
    for seed in question_seeds(questions):
        text = seed["text"]
        if research_available:
            title = _one_line("研究：" + _one_line(text, MAX_TITLE_CHARS), MAX_TITLE_CHARS)
            # The identity carries the hits (ids + revisions), so a changed material is a *new*
            # option rather than a silently reused one built on an older revision.
            identity = "|".join([KIND_RESEARCH, seed["marker_id"], _sha(text)[:16],
                                 "~".join(material_refs(hits))])
            if _one_line(title, MAX_TITLE_CHARS).casefold() in existing_titles:
                continue
            ref = f"{OPTION_REF_NAMESPACE}{_sha(identity)[:16]}"
            if ref in seen:
                continue
            seen.add(ref)
            options.append(ProjectOption(
                option_ref=ref, kind=KIND_RESEARCH, title=title, seed_text=text,
                # Only her own note ref plus the *verified* hits: the marker's recorded source id
                # is not cited as a source unless the workspace really returns that material now.
                source_refs=([entry for entry in seed["source_refs"]
                              if str(entry).startswith("pnote:")] + material_refs(hits)),
                basis_ref=f"question:{_safe_ref_id(seed['marker_id'])}",
                material_ref=(str(hits[0]["artifact_id"]) if hits else None),
                note=("materials=%d" % len(hits)),
                materials=hits, scope=_research_scope(hits),
                uncertainty=(DEFAULT_UNCERTAINTY if hits else NO_MATERIAL_UNCERTAINTY),
            ).to_dict())
            continue
        title = _title_from(text, prefix="把「") + "」写成一个东西"
        if _one_line(title, MAX_TITLE_CHARS).casefold() in existing_titles:
            continue
        identity = f"{KIND_QUESTION}|{seed['marker_id']}|{_sha(text)[:16]}"
        ref = f"{OPTION_REF_NAMESPACE}{_sha(identity)[:16]}"
        if ref in seen:
            continue
        seen.add(ref)
        options.append(ProjectOption(
            option_ref=ref, kind=KIND_QUESTION, title=_one_line(title, MAX_TITLE_CHARS),
            seed_text=text, source_refs=list(seed["source_refs"]),
            basis_ref=(f"question:{_safe_ref_id(seed['marker_id'])}"),
            material_ref=(f"artifact:{_safe_ref_id(seed['source_id'])}"
                          if seed.get("source_id") else None),
            note=(f"source_revision={seed['source_revision']}" if seed.get("source_revision")
                  else None),
        ).to_dict())

    for item in due_items or ():
        if not isinstance(item, Mapping):
            continue
        title_text = _one_line(item.get("title"), MAX_TITLE_CHARS)
        if not title_text:
            continue
        if _one_line(title_text, MAX_TITLE_CHARS).casefold() in existing_titles:
            continue
        item_ref = str(item.get("item_ref") or "")
        identity = f"{KIND_FUTURE_ITEM}|{item_ref}|{_sha(title_text)[:16]}"
        ref = f"{OPTION_REF_NAMESPACE}{_sha(identity)[:16]}"
        if ref in seen:
            continue
        seen.add(ref)
        options.append(ProjectOption(
            option_ref=ref, kind=KIND_FUTURE_ITEM, title=title_text,
            seed_text=title_text,
            source_refs=[r for r in (item_ref,) if r] + list(item.get("source_refs") or []),
            basis_ref=(item_ref or None),
            note="due_at=%s" % (item.get("due_at") or ""),
        ).to_dict())

    return options[:max(0, int(limit))]


def projects_report(records: Optional[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """A bounded, read-only list of the projects she already has (for the loop's report)."""

    rows: list[dict[str, Any]] = []
    for record in records or ():
        if not isinstance(record, Mapping):
            continue
        artifact_id = _artifact_id_of(record)
        if not artifact_id:
            continue
        rows.append({
            "artifact_id": artifact_id,
            "title": _one_line(record.get("title"), MAX_TITLE_CHARS),
            "lifecycle": record.get("lifecycle"),
            "revision": record.get("revision"),
            "head_version_id": record.get("head_version_id") or record.get("head_version"),
        })
    rows.sort(key=lambda row: (str(row.get("title") or ""), row["artifact_id"]))
    return rows[:MAX_PROJECTS_REPORTED]


# ---------------------------------------------------------------------------------------------
# the owner gateway (B's service)
# ---------------------------------------------------------------------------------------------


class ProjectOwnerGateway:
    """The one narrow seam onto the private-project owner.

    Expected owner interface (B's ``private_project_service``)::

        owner.list_projects() -> {"projects": [{artifact_id|project_ref, title,
                                               lifecycle, revision, head_version_id}, ...]}
        owner.create_project(*, title, seed_text, source_refs, operation_key,
                             workspace_id, subject_id=None)
                             -> {"status": "OK", "artifact_id": ..., "version_id": ...,
                                 "project_ref": optional, "created": optional}

    The gateway accepts a *provider object* so a test (or a future wiring) can hand in the real
    owner without this module importing it at import time.  ``create_project`` must be idempotent
    on ``operation_key``; the key is derived from the option's own identity, never from the
    attempt, so a retried adoption cannot mint a second project.
    """

    def __init__(self, provider: Any = None, *, subject_id: Optional[str] = None,
                 workspace_id: Optional[str] = None) -> None:
        self.provider = provider
        self.subject_id = str(subject_id) if subject_id else None
        self.workspace_id = str(workspace_id) if workspace_id else None
        self.last_error: Optional[str] = None

    # -- availability ------------------------------------------------------------------

    def available(self) -> bool:
        return self.provider is not None and callable(
            getattr(self.provider, "create_project", None))

    def availability(self) -> dict[str, Any]:
        if not self.available():
            return {"status": "UNAVAILABLE", "reason_code": REASON_OWNER_UNAVAILABLE}
        return {"status": "READY", "reason_code": "PRIVATE_PROJECT_OWNER_OK",
                "owner": type(self.provider).__name__}

    # -- reads -------------------------------------------------------------------------

    def list_projects(self) -> list[dict[str, Any]]:
        """The owner's own list of her projects; ``[]`` when it cannot answer.

        An unavailable owner is reported through ``last_error`` and answered as an empty list --
        the loop then simply has no existing-project context, which cannot forge anything.
        """
        if not self.available():
            self.last_error = REASON_OWNER_UNAVAILABLE
            return []
        try:
            answer = self.provider.list_projects()
        except Exception as exc:  # a failed read is reported, never guessed
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return []
        rows = answer.get("projects") if isinstance(answer, Mapping) else None
        if isinstance(answer, Mapping) and answer.get("status") not in (None, "OK"):
            self.last_error = f"{answer.get('status')}:{answer.get('reason') or ''}"[:200]
            return []
        return [dict(row) for row in (rows or []) if isinstance(row, Mapping)]

    # -- the one write -----------------------------------------------------------------

    def create_project(self, *, title: str, seed_text: str, source_refs: Sequence[str],
                       option_ref: str) -> dict[str, Any]:
        """Ask the owner to create one private project.  Returns the owner's own answer.

        A refusal or an unusable answer raises ``ProjectOptionError`` naming the reason: the
        caller must not report a project that the owner never confirmed.
        """
        if not self.available():
            raise ProjectOptionError(REASON_OWNER_UNAVAILABLE,
                                     "no private-project owner service is wired")
        if not self.workspace_id:
            raise ProjectOptionError(REASON_NO_WORKSPACE,
                                     "a project needs a workspace to be created in")
        title_text = _one_line(title, MAX_TITLE_CHARS)
        seed = str(seed_text or "")[:MAX_SEED_CHARS]
        if not title_text or not seed:
            raise ProjectOptionError(REASON_OWNER_REFUSED, "title and seed text are required")
        operation_key = "project:" + _sha(
            "|".join([str(option_ref), title_text, _sha(seed)[:16]]))[:24]
        try:
            answer = self.provider.create_project(
                title=title_text,
                seed_text=seed,
                source_refs=[str(ref) for ref in (source_refs or ())],
                operation_key=operation_key,
                workspace_id=self.workspace_id,
                subject_id=self.subject_id,
            )
        except ProjectOptionError:
            raise
        except Exception as exc:
            raise ProjectOptionError(REASON_OWNER_UNUSABLE,
                                     f"{type(exc).__name__}: {exc}"[:180]) from exc
        if not isinstance(answer, Mapping):
            raise ProjectOptionError(REASON_OWNER_UNUSABLE, "owner answered without a mapping")
        status = str(answer.get("status") or "")
        if status != "OK":
            raise ProjectOptionError(REASON_OWNER_REFUSED,
                                     f"{status}:{answer.get('reason') or ''}"[:180])
        artifact_id = _artifact_id_of(answer)
        if not artifact_id:
            raise ProjectOptionError(REASON_OWNER_UNUSABLE, "owner confirmed no artifact id")
        return {
            "status": "OK",
            "operation_key": str(answer.get("operation_key") or operation_key),
            "artifact_id": artifact_id,
            "version_id": answer.get("version_id"),
            "version_number": answer.get("version_number"),
            "project_ref": answer.get("project_ref"),
            "created": answer.get("created"),
        }


def resolve_gateway(module: Any = None, *, environment: Optional[Mapping[str, str]] = None,
                    subject_id: Optional[str] = None,
                    workspace_id: Optional[str] = None) -> ProjectOwnerGateway:
    """Resolve the project owner without importing it eagerly.

    Accepted, in order: an explicitly passed module/owner object, then B's module
    ``private_project_service`` on the life scripts path with one of the documented factories.
    Nothing is guessed: if none of that resolves, the gateway is *unavailable* and says so.
    """
    if module is not None:
        if callable(getattr(module, "create_project", None)):
            return ProjectOwnerGateway(module, subject_id=subject_id, workspace_id=workspace_id)
        for factory_name in ("private_projects_from_env", "service_from_env", "from_env",
                             "private_project_service_from_env"):
            factory = getattr(module, factory_name, None)
            if callable(factory):
                try:
                    provider = factory(dict(environment or {}))
                except Exception:
                    continue
                return ProjectOwnerGateway(provider, subject_id=subject_id,
                                           workspace_id=workspace_id)
        return ProjectOwnerGateway(None, subject_id=subject_id, workspace_id=workspace_id)
    try:  # pragma: no cover - B's module is not in this tree yet
        import private_project_service as _pps  # type: ignore
    except Exception:
        return ProjectOwnerGateway(None, subject_id=subject_id, workspace_id=workspace_id)
    return resolve_gateway(_pps, environment=environment, subject_id=subject_id,
                           workspace_id=workspace_id)


__all__ = [
    "ALL_KINDS",
    "DEFAULT_UNCERTAINTY",
    "KIND_FUTURE_ITEM",
    "KIND_QUESTION",
    "KIND_RESEARCH",
    "MAX_MATERIAL_EXCERPT_CHARS",
    "MAX_OPTIONS",
    "MAX_RESEARCH_MATERIALS",
    "NO_MATERIAL_UNCERTAINTY",
    "OPTION_REF_NAMESPACE",
    "OPTION_REF_PREFIX",
    "ProjectOption",
    "ProjectOptionError",
    "ProjectOwnerGateway",
    "REASON_NO_WORKSPACE",
    "REASON_OWNER_REFUSED",
    "REASON_OWNER_UNAVAILABLE",
    "REASON_OWNER_UNUSABLE",
    "SCHEMA_VERSION",
    "build_options",
    "material_refs",
    "materials_view",
    "projects_report",
    "question_seeds",
    "resolve_gateway",
]
