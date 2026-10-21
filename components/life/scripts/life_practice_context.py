"""v0.4 C: the bounded Life-side seam onto B's practice workspace (reviews and methods).

What this module is
-------------------
One small, bounded object -- :class:`PracticeSeam` -- so the Life main chain can:

* read the *governance-filtered experiences* this instance can really recall (through A's
  ``LifeMemoryService``, never a database);
* read her own **reviews** and **methods** out of B's ``PersonalPracticeService``
  (``ManagedArtifact`` rows the Supply owner already holds);
* ask that service to create **one** review when a committed Decision selected the round's
  review option, and to save **one** method draft when a committed Decision proposed one;
* record **one** bounded trial evaluation (``record_trial``) that names the experiences it is
  based on, so "it helped" can never be inferred from "a version was written";
* hand the decision prompt and the writing prompt a *bounded digest* of the methods that are
  relevant to what she is doing right now.

What it is not
--------------
* **Not a second library.**  A review and a method are ``ManagedArtifact`` rows; every read and
  every write goes through B's service, which goes through the private-project owner.
* **Not a marker definition.**  The practice marker (``<!-- nyairo-practice:v1 ... -->``) is
  parsed *only* by B's ``parse_practice_content``.  This module borrows that helper through
  :meth:`PracticeSeam.facts_of` and never spells the marker itself.
* **Not a Memory.**  Experiences are read through A's ``LifeMemoryService``; this module never
  opens a Memory database and never fabricates an experience id.
* **Not an executor.**  A method is prose a writing prompt may consult.  Nothing here compiles,
  schedules or runs a method, and a method is never a permission: the owners still decide every
  real action, exactly as before.

Failure is named, never empty
-----------------------------
An unwired seam answers ``NOT_WIRED`` and returns ``[]`` / ``None``; a failing service answers
with ``last_error`` set.  Neither is ever reported as "she has no practices".
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

#: Why a read produced nothing / why a write was refused.  Stable on purpose: the round reports
#: these words, and a test can tell "not wired" from "the owner said no".
REASON_SERVICE_MISSING = "PRACTICE_SERVICE_NOT_WIRED"
REASON_SERVICE_UNUSABLE = "PRACTICE_SERVICE_UNUSABLE"
REASON_ANSWER_NOT_A_MAPPING = "PRACTICE_ANSWER_NOT_A_MAPPING"
REASON_ENVELOPE_NOT_OK = "PRACTICE_ENVELOPE_NOT_OK"
REASON_CREATE_REFUSED = "PRACTICE_CREATE_REFUSED"
REASON_TRIAL_REFUSED = "PRACTICE_TRIAL_REFUSED"
REASON_NO_ARTIFACT = "PRACTICE_CREATE_CONFIRMED_NO_ARTIFACT"
REASON_NO_EVIDENCE = "PRACTICE_TRIAL_WITHOUT_READABLE_EVIDENCE"
REASON_METHOD_REF_UNKNOWN = "PRACTICE_METHOD_REF_UNKNOWN"

#: What the seam needs from B's service.  A service missing one of these is reported UNUSABLE
#: (never "empty"): a partial service would silently answer nothing forever.
REQUIRED_METHODS = (
    "list_reviews",
    "read_review",
    "create_review",
    "list_methods",
    "read_method",
    "create_method",
    "record_trial",
)

#: A method reference the Decision may name.  It is *not* an owner id: it is this seam's own
#: bounded handle onto one method artifact, so a model can name a method without ever holding a
#: writable id.  ``method_ref_for`` is the only place it is built.
METHOD_REF_PREFIX = "method:"
_METHOD_REF_RE = re.compile(r"^method:[0-9a-f]{8,40}$")

#: Parse helper owned by B.  A local copy of the marker would be a second definition.
PARSE_HELPER = "parse_practice_content"
#: The two kinds B defines; this module only ever compares against them.
KIND_REVIEW = "review"
KIND_METHOD = "method"

#: Trial outcomes, spelled exactly as B spells them (this seam never re-words an evaluation).
OUTCOME_HELPED = "HELPED"
OUTCOME_NOT_HELPED = "NOT_HELPED"
OUTCOME_UNKNOWN = "UNKNOWN"
VALID_OUTCOMES = frozenset({OUTCOME_HELPED, OUTCOME_NOT_HELPED, OUTCOME_UNKNOWN})

DEFAULT_MAX_EXPERIENCES = 4
DEFAULT_MAX_METHODS = 3
DEFAULT_EXCERPT_CHARS = 200
DEFAULT_TEXT_CHARS = 280


def method_ref_for(artifact_id: Any) -> str:
    """The bounded handle a Decision may name for one method artifact.

    Deterministic and derived only from the owner's own id, so the same method always has the
    same handle and a handle can always be resolved back through the round's own read set.
    """

    bare = str(artifact_id or "").strip()
    while bare.startswith("artifact:"):
        bare = bare[len("artifact:"):]
    if not bare:
        return ""
    return "%s%s" % (METHOD_REF_PREFIX, hashlib.sha256(bare.encode("utf-8")).hexdigest()[:16])


def is_method_ref(value: Any) -> bool:
    return bool(isinstance(value, str) and _METHOD_REF_RE.match(value.strip()))


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if limit else text


def _bounded(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit else text


def practice_module() -> Any:
    """B's module, or ``None`` when this tree has no practice service."""

    import importlib

    try:
        return importlib.import_module("personal_practice_service")
    except Exception:
        return None


class PracticeSeamError(RuntimeError):
    """A read or a write could not be answered.  Named, never silent."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)


class PracticeSeam:
    """A bounded, read-mostly view of one practice workspace service."""

    def __init__(
        self,
        service: Any = None,
        *,
        memory: Any = None,
        max_experiences: int = DEFAULT_MAX_EXPERIENCES,
        max_methods: int = DEFAULT_MAX_METHODS,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
        text_chars: int = DEFAULT_TEXT_CHARS,
    ) -> None:
        self.service = service
        #: A's ``LifeMemoryService`` (or the one B's service already holds).  ``None`` means the
        #: experiences half is unbound: reviews can still be read, but nothing may claim evidence.
        self.memory = memory if memory is not None else getattr(service, "memory_service", None)
        self.max_experiences = max(0, int(max_experiences))
        self.max_methods = max(0, int(max_methods))
        self.excerpt_chars = max(0, int(excerpt_chars))
        self.text_chars = max(0, int(text_chars))
        #: Why the last read/write answered nothing.  ``""`` when the last call really worked.
        self.last_error: str = ""
        self.last_experiences: list[dict[str, Any]] = []
        self.last_methods: list[dict[str, Any]] = []
        self.last_reviews: list[dict[str, Any]] = []
        #: Per-artifact head version seen when this round read the method, so a trial write is a
        #: real compare-and-set against the version she actually looked at.
        self.seen_heads: dict[str, str] = {}

    # -- availability -------------------------------------------------------------------

    def available(self) -> bool:
        service = self.service
        if service is None:
            return False
        return all(callable(getattr(service, name, None)) for name in REQUIRED_METHODS)

    def memory_bound(self) -> bool:
        memory = self.memory
        return bool(memory is not None
                    and callable(getattr(memory, "list_experiences", None))
                    and callable(getattr(memory, "read_experience", None)))

    def availability(self) -> dict[str, Any]:
        if self.service is None:
            return {"status": "NOT_WIRED", "reason_code": REASON_SERVICE_MISSING,
                    "memory": "NOT_WIRED"}
        if not self.available():
            missing = [name for name in REQUIRED_METHODS
                       if not callable(getattr(self.service, name, None))]
            return {"status": "UNUSABLE", "reason_code": REASON_SERVICE_UNUSABLE,
                    "missing": missing,
                    "memory": "BOUND" if self.memory_bound() else "NOT_WIRED"}
        return {"status": "READY", "reason_code": "PRACTICE_SERVICE_OK",
                "service": type(self.service).__name__,
                "memory": "BOUND" if self.memory_bound() else "NOT_WIRED"}

    def describe(self) -> dict[str, Any]:
        described = self.availability()
        described.update({
            "kind": "practice_seam",
            "bounds": {"max_experiences": self.max_experiences, "max_methods": self.max_methods,
                       "excerpt_chars": self.excerpt_chars, "text_chars": self.text_chars},
            # No marker definition of its own: the parse helper is B's.
            "parse_helper": callable(getattr(practice_module(), PARSE_HELPER, None)),
            "writes": ["create_review", "create_method", "record_trial"],
            "method_ref_prefix": METHOD_REF_PREFIX,
        })
        return described

    # -- envelopes ----------------------------------------------------------------------

    @staticmethod
    def _envelope(answer: Any) -> dict[str, Any]:
        """One documented envelope -> its ``data`` mapping, or a named refusal."""

        if not isinstance(answer, Mapping):
            raise PracticeSeamError(REASON_ANSWER_NOT_A_MAPPING, type(answer).__name__)
        status = str(answer.get("status") or "")
        ok = answer.get("ok")
        if ok is False:
            raise PracticeSeamError(
                REASON_ENVELOPE_NOT_OK,
                str(answer.get("reason_code") or status or "PRACTICE_REFUSED")[:80])
        if ok is None and status and status.lower() != "ok":
            raise PracticeSeamError(
                REASON_ENVELOPE_NOT_OK,
                str(answer.get("reason_code") or status or "PRACTICE_REFUSED")[:80])
        data = answer.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    def _call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not self.available():
            raise PracticeSeamError(REASON_SERVICE_MISSING,
                                    "no practice workspace service is wired")
        function = getattr(self.service, method)
        try:
            answer = function(*args, **kwargs)
        except PracticeSeamError:
            raise
        except Exception as exc:
            raise PracticeSeamError(REASON_SERVICE_UNUSABLE,
                                    "%s: %s" % (type(exc).__name__, str(exc)[:160])) from exc
        return self._envelope(answer)

    # -- experiences (A's Memory, through A's service only) -------------------------------

    def read_experience(self, experience_id: Any) -> Optional[dict[str, Any]]:
        """One experience A still calls recallable, or ``None`` (governed away / absent)."""

        key = str(experience_id or "").strip()
        if not key or not self.memory_bound():
            return None
        try:
            answer = self.memory.read_experience(key)
        except Exception as exc:
            self.last_error = "MEMORY_READ_FAILED:%s" % type(exc).__name__
            return None
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            return None
        data = answer.get("data") or {}
        row = data.get("experience")
        return dict(row) if isinstance(row, Mapping) else None

    def experiences(self, *, query: str = "",
                    limit: Optional[int] = None) -> list[dict[str, Any]]:
        """The governance-cleared experiences this round may cite.  Never a fabrication.

        A Memory that is unbound or failing yields ``[]`` *and* ``last_error``: "cannot recall"
        and "nothing to recall" stay distinguishable, exactly as A's own service keeps them.  At
        most **two** asks are made: the task-relevant query, and -- only when that matched nothing --
        A's own recent list, so a task match that finds nothing cannot hide a recallable past.
        """

        self.last_error = ""
        cap = (self.max_experiences if limit is None
               else max(0, min(int(limit), self.max_experiences)))
        self.last_experiences = []
        if not cap:
            return []
        if not self.memory_bound():
            self.last_error = "MEMORY_NOT_WIRED"
            return []
        try:
            answer = self.memory.list_experiences(query=str(query or ""), limit=cap)
            if (isinstance(answer, Mapping) and answer.get("ok")
                    and not ((answer.get("data") or {}).get("experiences") or [])
                    and str(query or "").strip()):
                answer = self.memory.list_experiences(query="", limit=cap)
        except Exception as exc:
            self.last_error = "MEMORY_LIST_FAILED:%s" % type(exc).__name__
            return []
        if not isinstance(answer, Mapping):
            self.last_error = "MEMORY_ANSWER_INVALID"
            return []
        if not answer.get("ok"):
            self.last_error = str(answer.get("reason")
                                  or answer.get("status") or "MEMORY_REFUSED")[:80]
            return []
        rows = (answer.get("data") or {}).get("experiences")
        view: list[dict[str, Any]] = []
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            experience_id = str(row.get("experience_id") or "").strip()
            if not experience_id:
                continue
            view.append({
                "experience_id": experience_id,
                "event_id": row.get("event_id"),
                "occurred_at": row.get("occurred_at"),
                "excerpt": _one_line(row.get("text"), self.excerpt_chars),
                "artifact_ref": row.get("artifact_ref"),
                "version_ref": row.get("version_ref"),
                "owner_result": row.get("owner_result"),
                "outcome_kind": row.get("outcome_kind"),
            })
            if len(view) >= cap:
                break
        self.last_experiences = view
        return view
    def evidence_usable(self, experience_ids: Iterable[Any]) -> list[dict[str, Any]]:
        """Which of these experiences A can still read, one check each (no invention)."""

        checks: list[dict[str, Any]] = []
        for experience_id in experience_ids or ():
            key = str(experience_id or "").strip()
            if not key:
                continue
            row = self.read_experience(key)
            checks.append({"experience_id": key, "usable": row is not None,
                           "occurred_at": (row or {}).get("occurred_at"),
                           "outcome_kind": (row or {}).get("outcome_kind")})
        return checks

    # -- reviews / methods ---------------------------------------------------------------

    def reviews(self, *, limit: int = 2) -> list[dict[str, Any]]:
        """Her reviews as bounded rows (no body): ids, question, basis, head."""

        self.last_error = ""
        self.last_reviews = []
        if not self.available():
            self.last_error = REASON_SERVICE_MISSING
            return []
        try:
            data = self._call("list_reviews", limit=max(1, int(limit)))
        except PracticeSeamError as exc:
            self.last_error = ("%s:%s" % (exc.reason_code, exc.detail)
                               if exc.detail else exc.reason_code)
            return []
        rows: list[dict[str, Any]] = []
        for row in data.get("projects") or ():
            if not isinstance(row, Mapping):
                continue
            rows.append({
                "artifact_id": str(row.get("artifact_id") or ""),
                "title": _one_line(row.get("title"), 120),
                "question": _one_line(row.get("question"), self.text_chars),
                "experience_basis": row.get("experience_basis"),
                "experience_count": row.get("experience_count"),
                "head_version_id": row.get("head_version_id"),
            })
        self.last_reviews = rows
        return rows

    def read_review(self, artifact_id: Any) -> Optional[dict[str, Any]]:
        target = str(artifact_id or "").strip()
        if not target or not self.available():
            return None
        try:
            data = self._call("read_review", target)
        except PracticeSeamError as exc:
            self.last_error = ("%s:%s" % (exc.reason_code, exc.detail)
                               if exc.detail else exc.reason_code)
            return None
        return self._bounded_document(data, KIND_REVIEW)

    @staticmethod
    def _method_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """One ``list_methods`` row -> the bounded row this seam offers (stable handle included)."""

        artifact_id = str(row.get("artifact_id") or "")
        head = str(row.get("head_version_id") or "")
        return {
            "method_ref": method_ref_for(artifact_id),
            "artifact_id": artifact_id,
            "title": _one_line(row.get("title"), 120),
            "instruction": _bounded(row.get("instruction"), DEFAULT_TEXT_CHARS),
            "scope": _one_line(row.get("scope"), 160),
            "uncertainty": _one_line(row.get("uncertainty"), 160),
            "method_state": row.get("method_state"),
            "head_version_id": head or None,
            "version_number": row.get("version_number"),
            "counterexample_count": row.get("counterexample_count"),
            "trial_count": row.get("trial_count"),
            "verified_skill": False,
        }

    def methods(self, *, query: str = "",
                limit: Optional[int] = None) -> list[dict[str, Any]]:
        """The methods relevant to what she is doing, bounded, each with a stable handle.

        The match is B's own deterministic, bounded search over title/question/body; this seam adds
        no second index and no embedding.  At most **two** asks are made: the task-relevant query,
        and -- only when that matched nothing -- B's own recent list, so a practice written a moment
        ago is still reachable.  Every row carries the head version it was read at, so a Decision
        that names it is naming *that* version.
        """

        self.last_error = ""
        cap = (self.max_methods if limit is None
               else max(0, min(int(limit), self.max_methods)))
        self.last_methods = []
        if not cap:
            return []
        if not self.available():
            self.last_error = REASON_SERVICE_MISSING
            return []
        rows: list[dict[str, Any]] = []
        needles = [str(query or ""), ""] if str(query or "").strip() else [""]
        for needle in needles:
            try:
                data = self._call("list_methods", query=needle, limit=cap)
            except PracticeSeamError as exc:
                self.last_error = ("%s:%s" % (exc.reason_code, exc.detail)
                                   if exc.detail else exc.reason_code)
                return []
            rows = []
            for row in data.get("projects") or ():
                if not isinstance(row, Mapping):
                    continue
                item = self._method_row(row)
                if not item["artifact_id"]:
                    continue
                item["instruction"] = _bounded(item["instruction"], self.text_chars)
                if item["head_version_id"]:
                    self.seen_heads[item["artifact_id"]] = item["head_version_id"]
                rows.append(item)
                if len(rows) >= cap:
                    break
            if rows:
                break
        self.last_methods = rows
        return rows

    def read_method(self, artifact_id: Any) -> Optional[dict[str, Any]]:
        """One method's bounded view, including the governance answer for its evidence."""

        target = str(artifact_id or "").strip()
        if not target or not self.available():
            return None
        try:
            data = self._call("read_method", target)
        except PracticeSeamError as exc:
            self.last_error = ("%s:%s" % (exc.reason_code, exc.detail)
                               if exc.detail else exc.reason_code)
            return None
        view = self._bounded_document(data, KIND_METHOD)
        if view is None:
            return None
        head = str(view.get("head_version_id") or "")
        if head:
            self.seen_heads[str(view.get("artifact_id"))] = head
        view["method_ref"] = method_ref_for(view.get("artifact_id"))
        return view

    def method_by_ref(self, method_ref: Any) -> Optional[dict[str, Any]]:
        """Resolve a Decision's ``method_ref`` inside *this round's* read set only."""

        wanted = str(method_ref or "").strip()
        if not is_method_ref(wanted):
            return None
        for row in self.last_methods:
            if str(row.get("method_ref")) == wanted:
                return dict(row)
        return None

    def method_refs(self) -> list[str]:
        return [str(row.get("method_ref")) for row in self.last_methods
                if row.get("method_ref")]

    def method_artifact_ids(self) -> list[str]:
        """The bare ids the workspace calls *method*: reference documents, not works."""

        if not self.available():
            return []
        ids: list[str] = []
        for row in self.methods(query="", limit=max(self.max_methods, 12)):
            text = str(row.get("artifact_id") or "")
            while text.startswith("artifact:"):
                text = text[len("artifact:"):]
            if text and text not in ids:
                ids.append(text)
        return ids

    @staticmethod
    def _bounded_document(data: Mapping[str, Any], kind: str) -> Optional[dict[str, Any]]:
        """One read answer -> a bounded view; the body only when B says it may be shown."""

        project = data.get("project")
        if not isinstance(project, Mapping):
            return None
        governance = data.get("governance") if isinstance(data.get("governance"),
                                                          Mapping) else {}
        body = data.get("body") if isinstance(data.get("body"), str) else ""
        view: dict[str, Any] = {
            "artifact_id": str(project.get("artifact_id") or ""),
            "kind": kind,
            "title": _one_line(project.get("title"), 120),
            "head_version_id": project.get("head_version_id"),
            "version_number": project.get("version_number"),
            "revision": project.get("revision"),
            "lifecycle": project.get("lifecycle"),
            "content_available": data.get("content_available") is not False,
            "governance_blocked": bool(governance.get("blocked")),
            "governance_reason": str(governance.get("reason_code") or ""),
            # A bounded body: enough to know what the document says, never the whole private work.
            "excerpt": _one_line(body, 400),
            "not_memory": True,
            "verified_skill": False,
        }
        if kind == KIND_METHOD:
            view.update({
                "instruction": _bounded(data.get("instruction"), 400),
                "scope": _one_line(data.get("scope"), 200),
                "uncertainty": _one_line(data.get("uncertainty"), 200),
                "method_state": data.get("method_state"),
                "experience_basis": data.get("experience_basis"),
                "experience_ids": [str(x) for x in (data.get("experience_ids") or [])],
                "review_refs": [str(x) for x in (data.get("review_refs") or [])],
                "trials": [dict(row) for row in (data.get("trials") or [])
                           if isinstance(row, Mapping)][-3:],
                "counterexamples": len(data.get("counterexamples") or ()),
                "revisions": data.get("revisions"),
                "evidence_checks": [dict(row) for row in (data.get("evidence_checks") or [])
                                    if isinstance(row, Mapping)],
            })
        else:
            view.update({
                "question": _one_line(data.get("question"), 300),
                "experience_basis": data.get("experience_basis"),
                "experience_ids": [str(x) for x in (data.get("experience_ids") or [])],
                "experience_checks": [dict(row) for row in (data.get("experience_checks") or [])
                                      if isinstance(row, Mapping)],
            })
        return view

    # -- prompts -------------------------------------------------------------------------

    def decision_context(self, *, query: str = "",
                         limit: Optional[int] = None) -> dict[str, Any]:
        """What the decision prompt may see: real experiences and the relevant methods.

        Both halves are bounded and attributable.  The methods carry their own handle, so the
        Decision can *name* one; none of them authorises anything.
        """

        availability = self.availability()
        experiences = self.experiences(query=query, limit=self.max_experiences)
        memory_error = self.last_error
        methods = self.methods(query=query, limit=limit)
        method_error = self.last_error
        view: dict[str, Any] = {
            "status": availability["status"],
            "memory": availability.get("memory"),
            "experiences": experiences,
            "methods": [{key: row.get(key) for key in
                         ("method_ref", "artifact_id", "title", "instruction", "scope",
                          "uncertainty", "method_state", "head_version_id",
                          "counterexample_count")}
                        for row in methods],
            "method_refs": [row["method_ref"] for row in methods],
            "note": ("方法是她自己写下的做法草案，只是文档参考，不是权限、可执行代码或人格信念；"
                     "引用 method_ref 必须来自这份列表。"),
        }
        errors = [text for text in (memory_error, method_error) if text]
        if errors:
            view["last_error"] = ";".join(errors)[:160]
        return view

    def writing_context(self, artifact_id: Any = "", *, query: str = "",
                        limit: Optional[int] = None) -> Optional[dict[str, Any]]:
        """The bounded document context one writing prompt may carry (or ``None``).

        Two halves:

        * ``methods`` -- the methods relevant to this work, with instruction / scope / uncertainty
          and the head version they were read at.  They are *reference*: the writer may follow one,
          it may not claim the method ran anything.
        * ``own_review`` -- present only when this very work is one of her reviews: its question and
          the (bounded) experiences it cited.  When a cited experience was withdrawn or deleted, B
          withholds the document's own text, so only its ref travels -- never derived prose.
        """

        if not self.available():
            return None
        target = str(artifact_id or "").strip()
        context: dict[str, Any] = {
            "kind": "practice_document_context",
            "artifact_id": target,
            "note": ("以下是她自己写下过的做法与回顾，只是写作参考：不是记忆、不是权限、"
                     "不是人格或稳定信念，也不要声称某条做法被执行过。"),
        }
        own = self.read_review(target) if target else None
        if own is not None:
            if own.get("content_available") is False:
                context["own_review"] = {"artifact_id": own.get("artifact_id"),
                                         "available": False}
            else:
                context["own_review"] = {
                    "artifact_id": own.get("artifact_id"),
                    "title": own.get("title"),
                    "question": own.get("question"),
                    "experience_basis": own.get("experience_basis"),
                    "experience_ids": list(own.get("experience_ids") or [])[:6],
                    "available": True,
                }
        methods = self.methods(query=query, limit=limit)
        if methods:
            context["methods"] = [
                {"method_ref": row["method_ref"], "artifact_id": row["artifact_id"],
                 "title": row["title"], "instruction": row["instruction"],
                 "scope": row["scope"], "uncertainty": row["uncertainty"],
                 "method_state": row["method_state"],
                 "head_version_id": row["head_version_id"]}
                for row in methods]
        if "own_review" not in context and "methods" not in context:
            return None
        return context

    # -- the writes ----------------------------------------------------------------------

    def create_review(
        self,
        *,
        question: str,
        experience_ids: Sequence[Any] = (),
        title: Optional[str] = None,
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ask B's service to create one review from experiences it can still read.

        The ids handed over are the ones the option was built from.  B re-reads every one of them
        through A's Memory before writing, so the document can only cite what is recallable now.
        A refusal raises by name; nothing here invents an artifact id.
        """

        ids = [str(x) for x in (experience_ids or ()) if str(x or "").strip()]
        if not self.available():
            raise PracticeSeamError(REASON_SERVICE_MISSING,
                                    "no practice workspace service is wired")
        payload: dict[str, Any] = {"question": _bounded(question, 400),
                                  "experience_ids": ids}
        if title:
            payload["title"] = _one_line(title, 120)
        if operation_key:
            payload["operation_key"] = str(operation_key)
        try:
            data = self._call("create_review", **payload)
        except PracticeSeamError as exc:
            raise PracticeSeamError(REASON_CREATE_REFUSED,
                                    "%s:%s" % (exc.reason_code, exc.detail)
                                    if exc.detail else exc.reason_code) from exc
        project = data.get("project")
        if not isinstance(project, Mapping):
            raise PracticeSeamError(REASON_CREATE_REFUSED, "PRACTICE_PROJECT_NOT_IN_ANSWER")
        artifact_id = str(project.get("artifact_id") or "")
        if not artifact_id:
            raise PracticeSeamError(REASON_NO_ARTIFACT, "the practice service confirmed no id")
        view = dict(project)
        view["status"] = "OK"
        view["kind"] = KIND_REVIEW
        view["head_version_id"] = (project.get("head_version_id")
                                   or data.get("version_id") or project.get("version_id"))
        view["version_id"] = view["head_version_id"]
        view["operation_key"] = str(data.get("operation_key") or operation_key or "")
        view["experience_ids"] = list(data.get("experience_ids") or ids)
        view["experience_checks"] = [dict(row) for row in (data.get("experience_checks") or [])
                                     if isinstance(row, Mapping)]
        return view

    def create_method(
        self,
        *,
        title: str,
        instruction: str,
        scope: str = "",
        uncertainty: str = "",
        experience_ids: Sequence[Any] = (),
        review_refs: Sequence[Any] = (),
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Save **one** TENTATIVE method draft: the concrete thing, its conditions, its unknown."""

        if not self.available():
            raise PracticeSeamError(REASON_SERVICE_MISSING,
                                    "no practice workspace service is wired")
        payload: dict[str, Any] = {
            "title": _one_line(title, 120),
            "instruction": _bounded(instruction, 1200),
            "scope": _one_line(scope, 300),
            "uncertainty": _one_line(uncertainty, 200),
            "experience_ids": [str(x) for x in (experience_ids or ()) if str(x or "").strip()],
            "review_refs": [str(x) for x in (review_refs or ()) if str(x or "").strip()],
        }
        if operation_key:
            payload["operation_key"] = str(operation_key)
        try:
            data = self._call("create_method", **payload)
        except PracticeSeamError as exc:
            raise PracticeSeamError(REASON_CREATE_REFUSED,
                                    "%s:%s" % (exc.reason_code, exc.detail)
                                    if exc.detail else exc.reason_code) from exc
        project = data.get("project")
        if not isinstance(project, Mapping):
            raise PracticeSeamError(REASON_CREATE_REFUSED, "PRACTICE_PROJECT_NOT_IN_ANSWER")
        artifact_id = str(project.get("artifact_id") or "")
        if not artifact_id:
            raise PracticeSeamError(REASON_NO_ARTIFACT, "the practice service confirmed no id")
        view = dict(project)
        view["status"] = "OK"
        view["kind"] = KIND_METHOD
        view["head_version_id"] = (project.get("head_version_id")
                                   or data.get("version_id") or project.get("version_id"))
        view["version_id"] = view["head_version_id"]
        view["method_ref"] = method_ref_for(artifact_id)
        view["method_state"] = data.get("method_state") or project.get("method_state")
        view["operation_key"] = str(data.get("operation_key") or operation_key or "")
        return view

    def record_trial(
        self,
        *,
        artifact_id: str,
        outcome: str,
        detail: str = "",
        experience_ids: Sequence[Any] = (),
        expected_head: Any = None,
    ) -> dict[str, Any]:
        """Record one bounded evaluation of a method against **readable** evidence.

        ``HELPED`` / ``NOT_HELPED`` without evidence is refused here (and again by B): a version
        that was written successfully is not a trial result, and ``UNKNOWN`` stays unknown.
        """

        target = str(artifact_id or "").strip()
        verdict = str(outcome or "").strip().upper()
        if verdict not in VALID_OUTCOMES:
            raise PracticeSeamError(REASON_TRIAL_REFUSED, "outcome must be one of %s"
                                    % ",".join(sorted(VALID_OUTCOMES)))
        ids = [str(x) for x in (experience_ids or ()) if str(x or "").strip()]
        if verdict != OUTCOME_UNKNOWN:
            checks = self.evidence_usable(ids)
            if not checks or not all(check.get("usable") for check in checks):
                raise PracticeSeamError(
                    REASON_NO_EVIDENCE,
                    "no readable experience supports this evaluation; only UNKNOWN may be recorded")
        head = str(expected_head or "").strip() or self.seen_heads.get(target, "")
        if not head:
            raise PracticeSeamError(
                REASON_TRIAL_REFUSED,
                "expected_head is required: read the method before evaluating it")
        try:
            data = self._call("record_trial", artifact_id=target, outcome=verdict,
                              detail=_bounded(detail, 300), experience_ids=ids,
                              expected_head=head)
        except PracticeSeamError as exc:
            raise PracticeSeamError(REASON_TRIAL_REFUSED,
                                    "%s:%s" % (exc.reason_code, exc.detail)
                                    if exc.detail else exc.reason_code) from exc
        return {
            "artifact_id": target,
            "outcome": data.get("outcome") or verdict,
            "method_state": data.get("method_state"),
            "evidence_usable": bool(data.get("evidence_usable")),
            "trials_recorded": data.get("trials_recorded"),
            "version_id": data.get("version_id") or data.get("head_version_id"),
            "status": data.get("status"),
            "note": "记录是评价，不改原事实；UNKNOWN 不变成功。",
        }

    # -- facts / quiet state -------------------------------------------------------------

    def facts_of(self, content: Any) -> Optional[dict[str, Any]]:
        """B's own parse of a practice document, or ``None`` when nothing can say."""

        module = practice_module()
        parser = getattr(module, PARSE_HELPER, None) if module is not None else None
        if not callable(parser):
            return None
        try:
            parsed = parser(content if isinstance(content, str) else str(content or ""))
        except Exception:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None

    def fingerprint_parts(self) -> dict[str, str]:
        """What the quiet ledger may hash for practices: ids, heads, states -- never text.

        Two rounds share these only when the same experiences are recallable, the same reviews
        carry the same heads and the same methods are readable in the same states.  A new
        experience, a withdrawn one, a new method head or a method moving to TRIAL/CONTESTED is
        therefore a new situation, and a mere clock tick is not.
        """

        experiences = self.experiences(limit=self.max_experiences)
        experience_part = ["%s@%s@%s" % (row.get("experience_id"), row.get("occurred_at"),
                                        row.get("outcome_kind"))
                           for row in experiences]
        methods = self.methods(query="", limit=max(self.max_methods, 6))
        method_part = ["%s@%s@%s" % (row.get("artifact_id"), row.get("head_version_id"),
                                     row.get("method_state"))
                       for row in methods]
        reviews = self.reviews(limit=2)
        review_part = ["%s@%s@%s" % (row.get("artifact_id"), row.get("head_version_id"),
                                     row.get("experience_count"))
                       for row in reviews]
        return {
            "practice_experiences": ",".join(sorted(experience_part)),
            "practice_methods": ",".join(sorted(method_part)),
            "practice_reviews": ",".join(sorted(review_part)),
        }


__all__ = [
    "DEFAULT_EXCERPT_CHARS",
    "DEFAULT_MAX_EXPERIENCES",
    "DEFAULT_MAX_METHODS",
    "KIND_METHOD",
    "KIND_REVIEW",
    "METHOD_REF_PREFIX",
    "OUTCOME_HELPED",
    "OUTCOME_NOT_HELPED",
    "OUTCOME_UNKNOWN",
    "PARSE_HELPER",
    "PracticeSeam",
    "PracticeSeamError",
    "REASON_ANSWER_NOT_A_MAPPING",
    "REASON_CREATE_REFUSED",
    "REASON_ENVELOPE_NOT_OK",
    "REASON_METHOD_REF_UNKNOWN",
    "REASON_NO_ARTIFACT",
    "REASON_NO_EVIDENCE",
    "REASON_SERVICE_MISSING",
    "REASON_SERVICE_UNUSABLE",
    "REASON_TRIAL_REFUSED",
    "REQUIRED_METHODS",
    "VALID_OUTCOMES",
    "is_method_ref",
    "method_ref_for",
    "practice_module",
]
