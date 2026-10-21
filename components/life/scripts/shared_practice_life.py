#!/usr/bin/env python3
"""v0.4 B: 共同实践的 Life 侧 —— 一次真实内容步骤要落到 A 的作品上。

What this module is
-------------------
The Life half of "有内容的相处": it consumes A's shared-practice service
(``shared_practice_service``) inside the **normal** resident step and turns an accepted joint
time into real content:

* the session is opened from an explicit invitation that names explicit shared *materials*
  (an artifact reference, read through the owner -- never a path, a channel or a subject the
  caller made up);
* an ordinary message that arrives **inside** that session becomes one real step: the person's
  own words are recorded on the user side, **one** normal content request goes to the model,
  and her final visible reply / optional piece / optional new work text are recorded on her
  side, bound to the *event reference* that prompted them;
* an explicit end ends the session: nothing is deleted, the work and every contribution stay
  readable, and re-opening the instance reads the same real contributions back.

What it deliberately is not
---------------------------
* not a second作品 owner: every write goes through ``SharedPracticeService`` (A), which owns the
  artifact, the CAS head, the operation key and the source verification;
* not a relationship store and not a chat database: there is no new file here at all;
* not a promise: a reply is never an agreement.  A content reply is always recorded with
  ``commitment_state=NONE``; a pending commitment stays pending until her own side says
  otherwise, and this module never claims ``ACCEPTED``;
* not lenient: the content request has a **strict** schema.  A truncated, fenced-broken or
  extra-key answer is ``INVALID_SCHEMA`` with no retry and no second guess; a transport failure
  is a named failure.  Neither is turned into "she answered";
* not a second foreground: it never pauses, starts or resumes an Activity -- the co-presence
  protocol and the Decision own that, and this module only records content against a session
  that already exists.

The transport extension (what the phone side sends, exactly)
-----------------------------------------------------------
One invitation payload may carry ``material_refs``: at most ``MAX_MATERIALS`` structured
artifact references (``^[A-Za-z][A-Za-z0-9_\\-]{1,31}:\\S{1,200}$``), deduplicated.  They are a
*request*: A reads each one through the owner, and an unreadable/archived/moved material is
refused at open.  Everything else about the invitation is unchanged (``invitation_id``,
``source_ref``, ``occurred_at``, ``scope``, ``requires_engagement``); a plain message may not
carry them.

The result contract (what the phone side reads back)
----------------------------------------------------
* ``OBSERVED`` + ``SHARED_PRACTICE_OBSERVED_ONLY`` -- a message outside any session: noticed,
  nothing else;
* ``OBSERVED`` + ``SHARED_PRACTICE_CONTENT_RECORDED`` -- a message inside an open session whose
  step really landed (see the ``practice`` block of the tick item for the counts and versions);
* ``UNKNOWN`` + a named reason (``SHARED_PRACTICE_MODEL_FAILED``, ``SHARED_PRACTICE_INVALID_SCHEMA``,
  ``SHARED_PRACTICE_OWNER_REFUSED``, ...) -- the step did not land; reconcile with
  ``inbox.operation(<operation_key>)``, never by re-sending the message;
* ``ACCEPTED`` -- the invitation's own Decision accepted; the practice session may be
  ``CANDIDATE`` (opened, no verified reply yet) -- opening is not acceptance;
* ``ENDED`` -- the explicit end; the ``practice`` block says whether the session actually ended.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE.parents[1]), str(HERE.parents[1] / "native"),
              str(HERE.parents[1] / "native" / "integration")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SCHEMA = "nyairo.shared-practice-life.v1"
STEP_REQUEST_SCHEMA = "nyairo.shared-practice-step-request.v1"

#: The step the model is asked for.  Exactly these keys -- an extra key is a schema failure.
STEP_FIELDS = ("reply", "contribution", "work_text")

#: How much of the session goes into one content request (bounded, text-first).
MAX_MATERIALS = 6
MAX_PROMPT_WORK_CHARS = 4000
MAX_PROMPT_EXCERPT_CHARS = 600
MAX_PROMPT_ENTRIES = 6
MAX_PROMPT_ENTRY_CHARS = 400
MAX_REPLY_CHARS = 2000
MAX_CONTRIBUTION_CHARS = 1200
MAX_WORK_CHARS = 12000
MAX_REASON_CHARS = 64

_MATERIAL_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{1,31}:\S{1,200}$")

#: Why a step did not land.  Every one is named; none is retried here.
REASON_SEAM_UNAVAILABLE = "SHARED_PRACTICE_SEAM_UNAVAILABLE"
REASON_NO_OPEN_PRACTICE = "SHARED_PRACTICE_NO_OPEN_SESSION"
REASON_MATERIAL_REFUSED = "SHARED_PRACTICE_MATERIAL_REFUSED"
REASON_OPEN_REFUSED = "SHARED_PRACTICE_OPEN_REFUSED"
REASON_OWNER_REFUSED = "SHARED_PRACTICE_OWNER_REFUSED"
REASON_MODEL_FAILED = "SHARED_PRACTICE_MODEL_FAILED"
REASON_MODEL_TRUNCATED = "SHARED_PRACTICE_MODEL_TRUNCATED"
REASON_INVALID_SCHEMA = "SHARED_PRACTICE_INVALID_SCHEMA"
REASON_RESPONDER_MISSING = "SHARED_PRACTICE_RESPONDER_MISSING"
REASON_END_REFUSED = "SHARED_PRACTICE_END_REFUSED"
REASON_BUDGET_REFUSED = "SHARED_PRACTICE_REQUEST_BUDGET_REFUSED"
REASON_OBSERVED_ONLY = "SHARED_PRACTICE_OBSERVED_ONLY"
REASON_CONTENT_RECORDED = "SHARED_PRACTICE_CONTENT_RECORDED"

#: Stages a step can stop at (so a failure says *where*, not just "it failed").
STAGE_USER_ENTRY = "user_entry"
STAGE_MODEL = "model"
STAGE_RESPONSE = "response"
STAGE_CONTRIBUTION = "contribution"
STAGE_WORK = "work"

#: The transport extension, named exactly for the phone side.
TRANSPORT_INVITATION_EXTENSION = ("material_refs",)
TRANSPORT_INVITATION_FIELDS = ("invitation_id", "source_ref", "occurred_at", "scope",
                               "requires_engagement", "material_refs")
TRANSPORT_MESSAGE_FIELDS = ("text", "operation_key", "source_ref", "occurred_at")
TRANSPORT_RESULT_FIELDS = ("outcome", "reason_code", "session_ref", "decision_ref",
                           "invitation_ref", "defer_condition")


class PracticeLifeError(RuntimeError):
    """This module's own wiring refusal (a programming mistake, not a model failure)."""


class PracticeStepRefused(PracticeLifeError):
    """A content answer that does not satisfy the strict step schema."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------------------------
# transport extension helpers
# ---------------------------------------------------------------------------------------------


def bound_material_refs(raw: Any) -> tuple[list[str], Optional[str]]:
    """Validate ``material_refs`` from a submit payload: structured refs only, bounded, unique.

    A path, a URL, an empty string or an arbitrary object is refused by name instead of being
    passed to the owner as if it were shareable.
    """

    if raw is None or raw == "":
        return [], None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return [], "%s: material_refs must be a list of structured references" % REASON_MATERIAL_REFUSED
    refs: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        if not _MATERIAL_REF_RE.match(text):
            return [], ("%s: %r is not a structured reference" % (REASON_MATERIAL_REFUSED,
                                                                  text[:80]))
        if text not in refs:
            refs.append(text)
    if len(refs) > MAX_MATERIALS:
        return [], "%s: at most %d materials" % (REASON_MATERIAL_REFUSED, MAX_MATERIALS)
    return refs, None


def transport_contract() -> dict[str, Any]:
    """The exact payload/result contract the phone side may rely on.  No private text."""

    return {
        "schema": SCHEMA,
        "invitation": {"fields": list(TRANSPORT_INVITATION_FIELDS),
                       "extension": list(TRANSPORT_INVITATION_EXTENSION),
                       "material_refs": {"max": MAX_MATERIALS,
                                         "shape": "^[A-Za-z][A-Za-z0-9_\\-]{1,31}:\\S{1,200}$",
                                         "meaning": "explicit shared materials, read through the "
                                                    "owner at open; unreadable => refused"}},
        "message": {"fields": list(TRANSPORT_MESSAGE_FIELDS),
                    "note": "a message never carries scope/invitation fields and never opens, "
                            "accepts or pauses anything"},
        "end": {"fields": ("invitation_id", "occurred_at", "explicit_end")},
        "results": {
            "fields": list(TRANSPORT_RESULT_FIELDS),
            "OBSERVED": [REASON_OBSERVED_ONLY, REASON_CONTENT_RECORDED],
            "UNKNOWN": [REASON_MODEL_FAILED, REASON_MODEL_TRUNCATED, REASON_INVALID_SCHEMA,
                        REASON_OWNER_REFUSED, REASON_BUDGET_REFUSED, REASON_RESPONDER_MISSING],
            "ACCEPTED": ["CO_PRESENCE_ACCEPTED"],
            "DEFERRED": ["CO_PRESENCE_DEFERRED"],
            "DECLINED": ["CO_PRESENCE_DECLINED"],
            "ENDED": ["CO_PRESENCE_ENDED", "CO_PRESENCE_END_JOINT_NOT_TERMINAL"],
            "practice_block": ("session_ref, state, joint_state, opened, response_recorded, "
                               "contribution_recorded, work_version_id, counts, llm_calls"),
        },
        "always_false": ("delivered_to_user", "relationship_state", "memory_written"),
    }


# ---------------------------------------------------------------------------------------------
# the content request and its strict answer
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PracticeContentRequest:
    """One bounded, text-first picture of the session a content answer is written against."""

    session_id: str
    event_ref: str
    title: str
    state: str
    work_text: str
    materials: tuple[dict[str, Any], ...]
    entries: tuple[dict[str, Any], ...]
    user_text: str
    schema: str = STEP_REQUEST_SCHEMA

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "session_id": self.session_id,
            "title": self.title,
            "state": self.state,
            "shared_materials": [dict(row) for row in self.materials],
            "joint_work_text": self.work_text,
            "recent_entries": [dict(row) for row in self.entries],
            "new_message_from_the_person": self.user_text,
            "answer_schema": {
                "reply": "最终可见的中文回应，<= %d 字，必填" % MAX_REPLY_CHARS,
                "contribution": "可选：这次真正加到作品里的一小块内容，<= %d 字，没有就 null"
                                % MAX_CONTRIBUTION_CHARS,
                "work_text": "可选：把作品改成的新全文，<= %d 字，不改就 null" % MAX_WORK_CHARS,
            },
            "rules": [
                "只输出一个 JSON 对象，不要 markdown，不要解释",
                "对象只允许 reply / contribution / work_text 三个键",
                "reply 是对方能看到的话，不要写推理过程",
                "不要把对方的问话当成承诺：答复不是答应",
            ],
        }


@dataclass
class PracticeAnswer:
    """Her answer, as the strict schema allows it."""

    reply: str
    contribution: Optional[str] = None
    work_text: Optional[str] = None
    model: dict[str, Any] = field(default_factory=dict)


def _head_of(view: Any) -> str:
    """The owner's current head: top level in a context view, inside ``project`` in a read."""

    if not isinstance(view, Mapping):
        return ""
    project = view.get("project")
    nested = project.get("head_version_id") if isinstance(project, Mapping) else None
    return str(view.get("head_version_id") or nested or "")


def _work_chars(view: Any) -> Optional[int]:
    """How many characters the work text has, read from whichever shape the owner answered."""

    if not isinstance(view, Mapping):
        return None
    if isinstance(view.get("text_chars"), int):
        return int(view["text_chars"])
    work = view.get("work")
    if isinstance(work, Mapping) and isinstance(work.get("chars"), int):
        return int(work["chars"])
    return None


def _contribution_count(view: Any) -> Optional[int]:
    if not isinstance(view, Mapping):
        return None
    if isinstance(view.get("contribution_count"), int):
        return int(view["contribution_count"])
    contributions = view.get("contributions")
    return len(contributions) if isinstance(contributions, (list, tuple)) else None


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def unwrap_fenced_json(text: Any) -> tuple[str, bool]:
    """Strip one ``` fence if the model used one.  The JSON itself is *not* repaired here."""

    raw = str(text or "").strip()
    if not raw.startswith("```"):
        return raw, False
    body = raw[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip(), True


def parse_content_answer(raw_text: Any) -> PracticeAnswer:
    """Strict parse.  Truncation, an extra key or a missing reply is a named refusal."""

    text = str(raw_text or "").strip()
    if not text:
        raise PracticeStepRefused(REASON_INVALID_SCHEMA, "the model answered nothing")
    body, fenced = unwrap_fenced_json(text)
    if not body.endswith("}") and not body.endswith("]"):
        # The provider stopped mid-object: that is truncation, not a schema choice.
        raise PracticeStepRefused(REASON_MODEL_TRUNCATED, "the answer is not a closed JSON object")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise PracticeStepRefused(REASON_INVALID_SCHEMA,
                                  "%s (fenced=%s)" % (type(exc).__name__, fenced)) from None
    if not isinstance(payload, Mapping):
        raise PracticeStepRefused(REASON_INVALID_SCHEMA, "the answer is not a JSON object")
    extra = sorted(key for key in payload if key not in STEP_FIELDS)
    if extra:
        raise PracticeStepRefused(REASON_INVALID_SCHEMA, "unknown keys: %s" % ",".join(extra[:5]))
    reply = payload.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise PracticeStepRefused(REASON_INVALID_SCHEMA, "reply is required and must be text")
    reply = " ".join(reply.split())
    if len(reply) > MAX_REPLY_CHARS:
        raise PracticeStepRefused(REASON_INVALID_SCHEMA,
                                  "reply is longer than %d characters" % MAX_REPLY_CHARS)
    out = PracticeAnswer(reply=reply)
    contribution = payload.get("contribution")
    if contribution is not None:
        if not isinstance(contribution, str):
            raise PracticeStepRefused(REASON_INVALID_SCHEMA, "contribution must be text or null")
        cleaned = " ".join(contribution.split())
        if cleaned:
            if len(cleaned) > MAX_CONTRIBUTION_CHARS:
                raise PracticeStepRefused(REASON_INVALID_SCHEMA,
                                          "contribution is longer than %d characters"
                                          % MAX_CONTRIBUTION_CHARS)
            out.contribution = cleaned
    work_text = payload.get("work_text")
    if work_text is not None:
        if not isinstance(work_text, str):
            raise PracticeStepRefused(REASON_INVALID_SCHEMA, "work_text must be text or null")
        if work_text.strip():
            if len(work_text) > MAX_WORK_CHARS:
                raise PracticeStepRefused(REASON_INVALID_SCHEMA,
                                          "work_text is longer than %d characters" % MAX_WORK_CHARS)
            out.work_text = work_text
    return out


SYSTEM_PROMPT = (
    "你在自己的实例里，和刚来的人一起做一件具体的小事。"
    "你看到的是这件事的真实材料、作品现在的正文、双方已经写下的内容，以及对方刚说的话。"
    "你要给出这一次的最终可见回应，必要时把一小块真实内容加进作品，或把作品正文改成新的全文。"
    "只输出一个 JSON 对象，键只允许 reply、contribution、work_text。"
    "reply 必填，是你对他说的话；contribution 只有真的加了一块内容才写，否则 null；"
    "work_text 只有真的改了作品正文才写全文，否则 null。"
    "不要输出思考过程、不要 markdown、不要解释 JSON 之外的东西。"
    "对方说的话不是承诺，你也不必答应任何事。"
)


def build_user_prompt(request: PracticeContentRequest) -> str:
    return json.dumps(request.prompt_payload(), ensure_ascii=False, sort_keys=True)


class PracticeResponder:
    """A content responder: one call in, one raw answer out.  No retry lives here."""

    def respond(self, request: PracticeContentRequest) -> dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"kind": type(self).__name__}


class ModelPracticeResponder(PracticeResponder):
    """The production responder: the instance's own model client, one send, no retry."""

    def __init__(self, client: Any, *, max_output_tokens: int = 700) -> None:
        if client is None:
            raise PracticeLifeError("a model client is required")
        self.client = client
        self.max_output_tokens = int(max_output_tokens)

    def describe(self) -> dict[str, Any]:
        return {"kind": "ModelPracticeResponder",
                "provider": str(getattr(self.client, "provider", "") or ""),
                "model": str(getattr(self.client, "model", "") or ""),
                "budgeted": hasattr(self.client, "budget")}

    def respond(self, request: PracticeContentRequest) -> dict[str, Any]:
        try:
            result = self.client.complete(
                system_prompt=SYSTEM_PROMPT, user_prompt=build_user_prompt(request),
                max_output_tokens=self.max_output_tokens)
        except Exception as exc:  # a transport failure is a named failure, never "she answered"
            failure = type(exc).__name__
            owner_reason = str(getattr(exc, "reason_code", "") or "")
            # A refused request is not a model failure: it never left the process, so say so.
            budgeted = ("budget" in failure.lower() or "BUDGET" in owner_reason.upper())
            return {"ok": False,
                    "reason_code": (REASON_BUDGET_REFUSED if budgeted else REASON_MODEL_FAILED),
                    "detail": _one_line("%s: %s" % (failure, exc), 200),
                    "failure_class": _one_line(getattr(exc, "failure_class", "") or failure, 64)}
        text = str(getattr(result, "text", "") or "")
        model = {"provider": _one_line(getattr(result, "provider", ""), 40),
                 "model": _one_line(getattr(result, "model", ""), 80),
                 "purpose": _one_line(getattr(result, "purpose", ""), 40),
                 "latency_ms": getattr(result, "latency_ms", None),
                 "status": getattr(result, "status", None)}
        if not text.strip():
            return {"ok": False, "reason_code": REASON_MODEL_FAILED,
                    "detail": "the provider returned no content", "model": model}
        return {"ok": True, "text": text, "model": model}


# ---------------------------------------------------------------------------------------------
# the seam over A's service
# ---------------------------------------------------------------------------------------------


class SharedPracticeLife:
    """The Life half of one shared practice, over A's own service.

    Two A services are held on purpose, because A's roles are separate:

    * ``subject`` -- ``actor="life"``: her reply, her contribution, the work text, resume/reopen,
      and every subject-side write is bound to a verified Life source;
    * ``person`` -- ``actor="user"``: only the person's own words, exactly as they arrived in the
      transport ledger.  It cannot write her half at all.
    """

    def __init__(self, *, subject: Any, person: Any = None, responder: Any = None,
                 clock: Optional[Callable[[], Any]] = None,
                 max_materials: int = MAX_MATERIALS) -> None:
        if subject is None or not callable(getattr(subject, "open_practice", None)):
            raise PracticeLifeError("A's SharedPracticeService is required")
        self.subject = subject
        self.person = person
        self.responder = responder
        self._clock = clock
        self.max_materials = max(1, min(int(max_materials), MAX_MATERIALS))
        self.last_step: Optional[dict[str, Any]] = None

    # -- what this thing is ----------------------------------------------------------------

    def available(self) -> bool:
        return True

    def model_ready(self) -> bool:
        return self.responder is not None

    def describe(self) -> dict[str, Any]:
        return {
            "owner": "SharedPracticeLife",
            "schema": SCHEMA,
            "source": "shared_practice_service",
            "actor": str(getattr(self.subject, "actor", "") or ""),
            "person_actor": str(getattr(self.person, "actor", "") or ""),
            "subject_id": str(getattr(self.subject, "subject_id", "") or ""),
            "workspace_id": str(getattr(self.subject, "workspace_id", "") or ""),
            "responder": (self.responder.describe() if self.responder is not None else None),
            "model_ready": self.model_ready(),
            "source_verifier_bound": getattr(self.subject, "source_verifier", None) is not None,
            "owns_activity": False,
            "owns_relationship": False,
            "owns_memory": False,
            "opens_without_materials": False,
            "commitment_from_a_reply": False,
            "rules": {
                "open": "one explicit invitation + explicit owner-readable materials",
                "message": "one content request, then her reply/contribution/work on her half",
                "end": "explicit only; nothing deleted, everything stays readable",
            },
            "transport": transport_contract(),
        }

    # -- small helpers ---------------------------------------------------------------------

    def _now(self) -> str:
        if self._clock is not None:
            value = self._clock()
            return value if isinstance(value, str) else str(value)
        return str(getattr(self.subject, "now", lambda: "")() or "")

    @staticmethod
    def _data(answer: Any) -> dict[str, Any]:
        return dict((answer or {}).get("data") or {}) if isinstance(answer, Mapping) else {}

    def _head(self, session_id: str) -> str:
        answer = self.subject.read_practice(session_id)
        if not answer.get("ok"):
            return ""
        return _head_of(self._data(answer))

    def _fail(self, reason_code: str, *, stage: str = "", detail: str = "",
              llm_calls: int = 0, session_id: str = "", **extra: Any) -> dict[str, Any]:
        out = {"ok": False, "reason_code": reason_code[:MAX_REASON_CHARS], "stage": stage,
               "detail": _one_line(detail, 300), "llm_calls": int(llm_calls),
               "session_id": session_id or None}
        out.update(extra)
        self.last_step = out
        return out

    # -- open / read -----------------------------------------------------------------------

    def find_open(self) -> Optional[dict[str, Any]]:
        """The newest session that is still open (paused included), read from the owner."""

        listing = self.subject.list_practices(include_ended=False)
        if not listing.get("ok"):
            return None
        rows = [dict(row) for row in self._data(listing).get("sessions") or []]
        for row in rows:
            if str(row.get("state") or "") in ("OPEN", "PAUSED"):
                return row
        return None

    def find_content_session(self) -> Optional[dict[str, Any]]:
        """The one session a message may continue: open right now, not paused, not ended.

        ``find_open`` deliberately includes ``PAUSED`` (a paused work is still "this joint
        time's work"), but a paused work must not keep producing content: a phone- or
        user-paused session, an ended one and a session somebody opened as a candidate are all
        *not* a place where a new message becomes her contribution.
        """

        row = self.find_open()
        if row is None:
            return None
        if str(row.get("state") or "") != "OPEN":
            return None
        return row

    def open_session(self, *, material_refs: Any, title: Any, opening_text: Any,
                     basis: Any = None, operation_key: Any = None) -> dict[str, Any]:
        """Open a session around explicit shared materials.  Opening is not acceptance."""

        refs, refusal = bound_material_refs(material_refs)
        if refusal:
            return self._fail(REASON_MATERIAL_REFUSED, stage="open", detail=refusal)
        if not refs:
            return self._fail(REASON_MATERIAL_REFUSED, stage="open",
                              detail="an invitation must name at least one shared material")
        existing = self.find_open()
        if existing is not None:
            # One joint time, one session: never a second parallel作品 for the same time.
            return {"ok": True, "reason_code": "", "reused": True,
                    "session_id": str(existing.get("session_id") or ""),
                    "state": str(existing.get("state") or ""),
                    "joint_state": str(existing.get("joint_state") or ""),
                    "materials": [], "llm_calls": 0}
        answer = self.subject.open_practice(
            title=title, opening_text=opening_text, material_refs=refs, basis=basis,
            operation_key=operation_key)
        if not answer.get("ok"):
            return self._fail(REASON_OPEN_REFUSED, stage="open",
                              detail=str(answer.get("reason_code") or ""),
                              owner_reason=str(answer.get("reason_code") or ""))
        data = self._data(answer)
        return {"ok": True, "reason_code": "", "reused": False,
                "session_id": str(data.get("session_id") or ""),
                "state": str(data.get("state") or ""),
                "joint_state": str(data.get("joint_state") or ""),
                "materials": list(data.get("materials") or []),
                "accepted": bool(data.get("accepted")),
                "llm_calls": 0}

    def context_view(self, *, session_id: Any = None) -> dict[str, Any]:
        """The bounded, text-first view: the real work, the real contributions, the materials."""

        target = str(session_id or (self.find_open() or {}).get("session_id") or "")
        if not target:
            return {"ok": False, "reason_code": REASON_NO_OPEN_PRACTICE}
        answer = self.subject.context_view(target, include_material_excerpt=True)
        if not answer.get("ok"):
            return {"ok": False, "reason_code": str(answer.get("reason_code") or
                                                    REASON_OWNER_REFUSED)}
        return {"ok": True, "data": self._data(answer)}

    def prompt_block(self, *, session_id: Any = None) -> dict[str, Any]:
        """A bounded text-first block for the Decision prompt: what this session really is."""

        view = self.context_view(session_id=session_id)
        if not view.get("ok"):
            return {"available": False, "reason_code": view.get("reason_code"),
                    "private_text_included": False}
        data = dict(view["data"])
        materials = [{"artifact_id": row.get("artifact_id"), "state": row.get("state"),
                      "title": _one_line(row.get("title"), 80),
                      "excerpt": _one_line(row.get("excerpt"), MAX_PROMPT_EXCERPT_CHARS)}
                     for row in (data.get("materials") or [])[:self.max_materials]]
        entries = [{"contributor": str(row.get("contributor") or ""),
                    "kind": str(row.get("kind") or ""),
                    "text": _one_line(row.get("text"), MAX_PROMPT_ENTRY_CHARS)}
                   for row in (data.get("contributions") or [])[-MAX_PROMPT_ENTRIES:]]
        return {
            "available": True,
            "session_id": data.get("session_id"),
            "title": _one_line(data.get("title"), 120),
            "state": data.get("state"),
            "joint_state": data.get("joint_state"),
            "opened": True,
            "work_text": _one_line(data.get("text"), MAX_PROMPT_WORK_CHARS),
            "work_chars": _work_chars(data),
            "materials": materials,
            "entries": entries,
            "last_response": _one_line((data.get("last_response") or {}).get("text"),
                                       MAX_PROMPT_ENTRY_CHARS),
            "pending_commitments": list(data.get("pending_commitments") or []),
            "private_text_included": bool(data.get("private_text_included")),
            "note": "这是共同的这一段作品本身：正文与贡献是真的，session 状态不是答应任何事。",
        }

    # -- one content step ------------------------------------------------------------------

    def step_message(self, *, text: Any, event_ref: Any,
                     operation_key: Any = None) -> dict[str, Any]:
        """Record the person's words, ask the model once, and record her real answer.

        Every write goes through A; the head after each write is re-read from the owner, so the
        CAS is always the owner's current head and never a guess.
        """

        session = self.find_content_session()
        if session is None:
            return self._fail(REASON_NO_OPEN_PRACTICE, stage=STAGE_USER_ENTRY,
                              detail="no shared practice session is open for content (a paused or "
                                     "ended session does not keep producing it)")
        session_id = str(session.get("session_id") or "")
        person_words = _one_line(text, MAX_CONTRIBUTION_CHARS)
        if not person_words:
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_USER_ENTRY,
                              detail="an empty message is not a contribution", session_id=session_id)
        if self.person is None:
            return self._fail(REASON_SEAM_UNAVAILABLE, stage=STAGE_USER_ENTRY,
                              detail="no user-role service: the person's words cannot be recorded",
                              session_id=session_id)
        head = self._head(session_id)
        if not head:
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_USER_ENTRY,
                              detail="the owner did not report a head version",
                              session_id=session_id)
        key = str(operation_key or "") or ("practice_user:%s" % str(event_ref or "")[:120])
        written = self.person.submit_user_contribution(session_id, text=person_words,
                                                       expected_head=head, operation_key=key)
        if not written.get("ok"):
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_USER_ENTRY,
                              detail=str(written.get("reason_code") or ""),
                              owner_reason=str(written.get("reason_code") or ""),
                              session_id=session_id)
        user_entry = dict(self._data(written).get("entry") or {})
        head = str(self._data(written).get("head_version_id") or "") or self._head(session_id)

        view = self.context_view(session_id=session_id)
        if not view.get("ok"):
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_MODEL,
                              detail="the session could not be read for a content request",
                              session_id=session_id)
        data = dict(view["data"])
        request = PracticeContentRequest(
            session_id=session_id, event_ref=str(event_ref or ""),
            title=_one_line(data.get("title"), 120), state=str(data.get("state") or ""),
            work_text=_one_line(data.get("text"), MAX_PROMPT_WORK_CHARS),
            materials=tuple({"artifact_id": row.get("artifact_id"),
                             "title": _one_line(row.get("title"), 80),
                             "state": row.get("state"),
                             "excerpt": _one_line(row.get("excerpt"), MAX_PROMPT_EXCERPT_CHARS)}
                            for row in (data.get("materials") or [])[:self.max_materials]),
            entries=tuple({"contributor": str(row.get("contributor") or ""),
                           "kind": str(row.get("kind") or ""),
                           "text": _one_line(row.get("text"), MAX_PROMPT_ENTRY_CHARS)}
                          for row in (data.get("contributions") or [])[-MAX_PROMPT_ENTRIES:]),
            user_text=person_words)
        if self.responder is None:
            return self._fail(REASON_RESPONDER_MISSING, stage=STAGE_MODEL,
                              detail="no content responder is wired: nothing is invented instead",
                              session_id=session_id)
        try:
            raw = dict(self.responder.respond(request) or {})
        except PracticeStepRefused as exc:
            return self._fail(exc.reason_code, stage=STAGE_MODEL, detail=exc.detail,
                              session_id=session_id)
        except Exception as exc:  # a responder bug is a named failure, not an answer
            return self._fail(REASON_MODEL_FAILED, stage=STAGE_MODEL,
                              detail="%s: %s" % (type(exc).__name__, exc),
                              session_id=session_id)
        if not raw.get("ok"):
            reason = str(raw.get("reason_code") or REASON_MODEL_FAILED)
            code = (REASON_BUDGET_REFUSED if "BUDGET" in reason.upper() else reason)
            return self._fail(code, stage=STAGE_MODEL, detail=str(raw.get("detail") or ""),
                              llm_calls=0 if code == REASON_BUDGET_REFUSED else 1,
                              session_id=session_id, model=raw.get("model"))
        try:
            answer = parse_content_answer(raw.get("text"))
        except PracticeStepRefused as exc:
            return self._fail(exc.reason_code, stage=STAGE_MODEL, detail=exc.detail,
                              llm_calls=1, session_id=session_id, model=raw.get("model"))
        answer.model = dict(raw.get("model") or {})

        basis = {"event_ref": str(event_ref or "")}
        if not basis["event_ref"]:
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_RESPONSE,
                              detail="a reply must be bound to the message it answers",
                              llm_calls=1, session_id=session_id)
        recorded = self.subject.submit_subject_response(
            session_id, text=answer.reply, basis=basis, expected_head=head,
            operation_key=str(operation_key or "") and ("%s:reply" % operation_key)
            or ("practice_reply:%s" % str(event_ref or "")[:120]))
        if not recorded.get("ok"):
            return self._fail(REASON_OWNER_REFUSED, stage=STAGE_RESPONSE,
                              detail=str(recorded.get("reason_code") or ""),
                              owner_reason=str(recorded.get("reason_code") or ""),
                              llm_calls=1, session_id=session_id, model=answer.model)
        response_entry = dict(self._data(recorded).get("entry") or {})
        head = str(self._data(recorded).get("head_version_id") or "") or self._head(session_id)
        joint_state = str(self._data(recorded).get("joint_state") or "")

        contribution_entry: dict[str, Any] = {}
        if answer.contribution:
            added = self.subject.submit_subject_contribution(
                session_id, text=answer.contribution, basis=basis, expected_head=head,
                operation_key="practice_contribution:%s" % str(event_ref or "")[:120])
            if not added.get("ok"):
                return self._fail(REASON_OWNER_REFUSED, stage=STAGE_CONTRIBUTION,
                                  detail=str(added.get("reason_code") or ""),
                                  owner_reason=str(added.get("reason_code") or ""),
                                  llm_calls=1, session_id=session_id,
                                  response_recorded=True, model=answer.model)
            contribution_entry = dict(self._data(added).get("entry") or {})
            head = str(self._data(added).get("head_version_id") or "") or self._head(session_id)

        work_version_id = ""
        if answer.work_text:
            committed = self.subject.commit_work_text(
                session_id, text=answer.work_text, basis=basis, expected_head=head,
                operation_key="practice_work:%s" % str(event_ref or "")[:120])
            if not committed.get("ok"):
                return self._fail(REASON_OWNER_REFUSED, stage=STAGE_WORK,
                                  detail=str(committed.get("reason_code") or ""),
                                  owner_reason=str(committed.get("reason_code") or ""),
                                  llm_calls=1, session_id=session_id,
                                  response_recorded=True, model=answer.model)
            work_version_id = str(self._data(committed).get("head_version_id")
                                  or self._data(committed).get("version_id") or "")
            head = work_version_id or self._head(session_id)

        final = self._data(self.subject.read_practice(session_id, include_history=False))
        out = {
            "ok": True, "reason_code": "", "stage": "done",
            "session_id": session_id,
            "state": str(final.get("state") or ""),
            "joint_state": joint_state or str(final.get("joint_state") or ""),
            "accepted": (joint_state or str(final.get("joint_state") or "")) == "JOINT",
            "user_entry": {"seq": user_entry.get("seq"), "chars": len(person_words)},
            "response_entry": {"seq": response_entry.get("seq"), "chars": len(answer.reply)},
            "response_text": answer.reply,
            "contribution_recorded": bool(contribution_entry),
            "contribution_seq": contribution_entry.get("seq"),
            "work_version_id": work_version_id or None,
            "head_version_id": _head_of(final) or head or "",
            "practice_revision": final.get("practice_revision"),
            "counts": {"contributions": _contribution_count(final),
                       "responses": len(final.get("responses") or [])},
            "commitment_state": "NONE",
            "llm_calls": 1,
            "model": answer.model,
            "private_text_included": bool(final.get("private_text_included")),
            "delivered_to_user": False,
        }
        self.last_step = out
        return out

    # -- end / read back -------------------------------------------------------------------

    def end_session(self, *, reason: Any = "", event_ref: Any = None) -> dict[str, Any]:
        """Explicit end.  The work,贡献 and history stay readable afterwards."""

        session = self.find_open()
        if session is None:
            return self._fail(REASON_NO_OPEN_PRACTICE, stage="end",
                              detail="no shared practice session is open")
        session_id = str(session.get("session_id") or "")
        head = self._head(session_id)
        answer = self.subject.end_practice(
            session_id, reason=reason or "EXPLICIT_END", expected_head=head,
            operation_key=("practice_end:%s" % str(event_ref or "")[:120])
            if event_ref else None)
        if not answer.get("ok"):
            return self._fail(REASON_END_REFUSED, stage="end",
                              detail=str(answer.get("reason_code") or ""),
                              owner_reason=str(answer.get("reason_code") or ""),
                              session_id=session_id)
        data = self._data(answer)
        final = self._data(self.subject.read_practice(session_id, include_history=False))
        out = {"ok": True, "reason_code": "", "session_id": session_id,
               "state": str(data.get("state") or final.get("state") or ""),
               "joint_state": str(final.get("joint_state") or ""),
               "ended": True,
               "work_chars": _work_chars(final),
               "counts": {"contributions": _contribution_count(final),
                          "responses": len(final.get("responses") or [])},
               "head_version_id": _head_of(final),
               "reopen_count": final.get("reopen_count"),
               "private_text_included": bool(final.get("private_text_included"))}
        self.last_step = out
        return out

    def read_back(self, *, session_id: Any = None,
                  include_history: bool = True) -> dict[str, Any]:
        """Read the session back as it really is -- what a reopened instance must see."""

        target = str(session_id or "")
        if not target:
            listing = self.subject.list_practices(include_ended=True)
            rows = [dict(row) for row in self._data(listing).get("sessions") or []]
            if not rows:
                return {"ok": False, "reason_code": REASON_NO_OPEN_PRACTICE}
            target = str(rows[0].get("session_id") or "")
        if not target:
            return {"ok": False, "reason_code": REASON_NO_OPEN_PRACTICE}
        answer = self.subject.read_practice(target, include_history=include_history,
                                           include_material_excerpt=False)
        if not answer.get("ok"):
            return {"ok": False, "reason_code": str(answer.get("reason_code")
                                                    or REASON_OWNER_REFUSED)}
        data = self._data(answer)
        contributions = [dict(row) for row in data.get("contributions") or []]
        responses = [dict(row) for row in data.get("responses") or []]
        return {
            "ok": True,
            "session_id": target,
            "title": data.get("title"),
            "state": data.get("state"),
            "joint_state": data.get("joint_state"),
            "practice_revision": data.get("practice_revision"),
            "head_version_id": _head_of(data),
            "text_chars": (data.get("work") or {}).get("chars"),
            "responses": responses,
            "contributions": contributions,
            "commitments": list(data.get("commitments") or []),
            "counts": {"contributions": len(contributions), "responses": len(responses)},
            "distinct_seqs": sorted({int(row.get("seq") or 0)
                                     for row in contributions + responses}),
            "material_count": len(data.get("materials") or []),
            "governance": dict(data.get("governance") or {}),
            "private_text_included": bool(data.get("private_text_included")),
        }


# ---------------------------------------------------------------------------------------------
# the source verifier A's subject-side writes are bound to
# ---------------------------------------------------------------------------------------------


class LifeSourceVerifier:
    """A's ``basis`` read port, composed from *this instance's* own read surfaces.

    A calls one method per basis kind; anything this instance cannot read is refused by name
    rather than answered with a fabricated record.
    """

    def __init__(self, *, inbox: Any = None, decision_reader: Any = None,
                 action_reader: Any = None, adoption_reader: Any = None) -> None:
        self.inbox = inbox
        self.decision_reader = decision_reader
        self.action_reader = action_reader
        self.adoption_reader = adoption_reader

    def describe(self) -> dict[str, Any]:
        return {"bound": {"event": self.inbox is not None,
                          "decision": self.decision_reader is not None,
                          "action": self.action_reader is not None,
                          "adoption": self.adoption_reader is not None},
                "private_text_included": False}

    def get_event(self, event_ref: str) -> Optional[dict[str, Any]]:
        if self.inbox is None:
            return None
        return dict(self.inbox.get_event(event_ref, include_body=False))

    def get_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        if self.decision_reader is None:
            return None
        return self.decision_reader.get_decision(decision_id)

    def get_action(self, action_id: str) -> Optional[dict[str, Any]]:
        if self.action_reader is None:
            return None
        return self.action_reader.get_action(action_id)

    def get_adoption_by_id(self, adoption_id: str) -> Optional[dict[str, Any]]:
        if self.adoption_reader is None:
            return None
        return self.adoption_reader.get_adoption_by_id(adoption_id)


__all__ = [
    "LifeSourceVerifier",
    "ModelPracticeResponder",
    "PracticeAnswer",
    "PracticeContentRequest",
    "PracticeLifeError",
    "PracticeResponder",
    "PracticeStepRefused",
    "REASON_BUDGET_REFUSED",
    "REASON_CONTENT_RECORDED",
    "REASON_END_REFUSED",
    "REASON_INVALID_SCHEMA",
    "REASON_MATERIAL_REFUSED",
    "REASON_MODEL_FAILED",
    "REASON_MODEL_TRUNCATED",
    "REASON_NO_OPEN_PRACTICE",
    "REASON_OBSERVED_ONLY",
    "REASON_OPEN_REFUSED",
    "REASON_OWNER_REFUSED",
    "REASON_RESPONDER_MISSING",
    "REASON_SEAM_UNAVAILABLE",
    "SCHEMA",
    "STEP_FIELDS",
    "SYSTEM_PROMPT",
    "SharedPracticeLife",
    "TRANSPORT_INVITATION_EXTENSION",
    "TRANSPORT_INVITATION_FIELDS",
    "TRANSPORT_MESSAGE_FIELDS",
    "bound_material_refs",
    "build_user_prompt",
    "parse_content_answer",
    "transport_contract",
    "unwrap_fenced_json",
]
