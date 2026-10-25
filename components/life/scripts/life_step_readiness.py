#!/usr/bin/env python3
"""v0.4 一天生活：前台 Activity 的**只读步骤就绪**，以及有限、可失效的「再考虑」台账。

这个模块只回答两个问题，都不花钱、不写 owner：

1. **``step_readiness``：当前前台 Activity 还有没有一步能真的执行？**
   「有材料」不等于「有可执行步骤」。材料读尽 / 来源被撤 / 作品 writer 在同一版本上明确
   ``no_change`` / 前台的 target 是这个世界进程执行不了的动词 / 前台本身就是只有一个动作、
   一个完成条件的 BODY Activity（没有定义过的下一步） —— 这几种情况下 owner 里那条
   Activity 仍是 ``ACTIVE``，但这一轮没有任何一步可以落地。判定只读，且**必须绑定**：只有
   同时拿到 ``activity_ref`` 与它自己的 ``target_ref``（外加 source revision）时才算一条可
   对外陈述的事实；否则 ``available=None``（不知道），绝不冒充「做不下去了」。

   唯一对外的形状是 AG-1 已经消费的那一个 ASCII 布尔键
   ``current_step_unavailable_true``（``agency_decision_ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE``），
   且只在 ``available is False`` **且**绑定成立时出现。它不是授权：不产生候选、不改容量、
   不改世界事实、不关闭 Activity、不代替她做选择。

2. **``ReconsiderationLedger``：同一个状态不要反复花钱问。**
   一个状态（target + source revision + 无步骤原因）被考虑过一次、她也没动之后，下一轮在
   *完全相同* 的状态上再发一次决策请求就是纯浪费。台账只存 fingerprint / 引用 / 时间 / 计数
   —— 不存任何私人正文、不存模型的理由、不存 Decision 本身，因此它既不是人格库也不是任务库。
   它有限（最多 8 条）、有寿命（默认 6 小时）、可显式解除，而且只要状态真的变了（新 revision、
   新页面、不同 target、不同原因）fingerprint 就不同，抑制自然失效。

这两件事都只读 owner 的既有记录；本模块不 import cycle / runner / owner，也不写任何 owner 状态。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# 1. Step readiness
# ---------------------------------------------------------------------------

#: The one fact key AG-1 already consumes (``agency_decision_ag1.py:2215``).  Only an ASCII key
#: whose value **is** the boolean ``True`` counts there, so this module never emits a string.
STEP_UNAVAILABLE_FACT_KEY = "current_step_unavailable_true"

#: Mirrors ``subject_private_materials.MAX_FACT_KEYS``: the prompt's own bound.  Kept as a literal
#: so this module stays import-free of the materials reader.
MAX_FACT_KEYS = 24

REASON_STEP_AVAILABLE = "STEP_AVAILABLE"
REASON_NO_FOREGROUND = "NO_FOREGROUND_ACTIVITY"
REASON_FOREGROUND_NOT_ACTIVE = "FOREGROUND_NOT_ACTIVE"
REASON_CALLER_CANNOT_ACT = "CALLER_CANNOT_ACT"
REASON_NO_BOUND_TARGET = "NO_BOUND_TARGET"
REASON_WORK_NO_CHANGE = "WORK_NO_CHANGE_CONFIRMED"
REASON_WORLD_STEP_AVAILABLE = "WORLD_STEP_AVAILABLE"
REASON_WORLD_STEP_NOT_WIRED = "WORLD_STEP_NOT_WIRED"
#: A BODY Activity is one action with one completion condition.  No second step is defined for it
#: anywhere in this tree: no same-id retry policy, no "repeat the same pose as progress", no
#: staged REST protocol.  The name says only that, never that the action failed or succeeded --
#: submission, settlement and LR-5 completion stay separate layers.
REASON_BODY_SINGLE_ACTION = "BODY_SINGLE_ACTION_NO_FURTHER_STEP"
REASON_ARTIFACT_STEP_AVAILABLE = "ARTIFACT_STEP_AVAILABLE"
REASON_STAY_CHECKPOINT = "STAY_CHECKPOINT_STEP"
REASON_READING_PREFIX = "READING_"
REASON_UNKNOWN_TARGET = "TARGET_NAMESPACE_UNKNOWN"

#: The target namespaces this module can classify.  They are the ones the Life core already reads
#: back from a committed candidate; nothing new is invented here.
TARGET_STAY = "stay:"
TARGET_PLACE = "place:"
TARGET_OBJECT = "object:"
TARGET_BODY_ACTION = "bodyact:"
#: v0.4 lived day: B's one-target encoding for her own everyday hand work (PICK_UP / PLACE).
#: It is a World step like the others -- the owner still decides -- so it is classified by the
#: same World branch below and never as an artifact or a stay.
TARGET_DAILY_ACTION = "dailyact:"
TARGET_ARTIFACT = "artifact:"

#: Activity kinds that carry a Life-side internal checkpoint instead of an owner verb.
STAY_ACTIVITY_KIND = "STAY_AT_OBSERVED_PLACE"
#: The Activity shape of one body step (mirrors ``autonomous_life_cycle.BODY_ACTION_ACTIVITY_KIND``).
#: One such Activity carries **one** body target -- one action with one completion condition -- so
#: it is the one World-shaped Activity that has no follow-up step of its own.
BODY_ACTIVITY_KIND = "BODY_ACTION_AT_OBSERVED_PLACE"


def _token(value: Any) -> str:
    """One ASCII/underscore token for a reason code (never free text)."""

    text = str(value or "").strip().upper()
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in text)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "UNKNOWN"


def namespace_of(target_ref: Any) -> Optional[str]:
    """Which family a target ref belongs to, or ``None`` when it is not one of them.

    The Activity owner records a step target in the namespace the *contract* used, which for a
    World-side Activity is LR-5's ``world_fact:`` wrapper around the same spelling, and an artifact
    may carry this tree's domain wrapper in front of the owner's own id.  Both are folded away first
    (``canonical_target_ref``), so a stay/place/object/body/artifact target is recognised from the
    Activity's own record instead of being reported as an unknown namespace.  A bare *version* ref
    still belongs to the artifact family here, because that is the family whose step reads it.
    """

    canonical = canonical_target_ref(target_ref)
    if canonical is not None:
        if is_artifact_target(canonical):
            return TARGET_ARTIFACT
        for prefix in (TARGET_STAY, TARGET_PLACE, TARGET_OBJECT, TARGET_BODY_ACTION,
                       TARGET_DAILY_ACTION):
            if canonical.startswith(prefix):
                return prefix
        return None
    text = str(target_ref or "").strip()
    while text.startswith(WORLD_FACT_WRAPPER):
        text = text[len(WORLD_FACT_WRAPPER):]
    if text.startswith(VERSION_REF_PREFIX):
        return TARGET_ARTIFACT
    return None


#: The ManagedArtifact owner mints its ids **with** this prefix (``artifact:<uuid>``), so it is part
#: of the owner's identity, not a namespace this tree may add or drop.  This tree's *domain* wrapper
#: happens to spell the same prefix again (``artifact:artifact:<uuid>``) when a ref is built from an
#: owner id, which is why every leading occurrence is folded into exactly one owner id below.
OWNER_ARTIFACT_PREFIX = "artifact:"
VERSION_REF_PREFIX = "artifactversion:"
WORLD_FACT_WRAPPER = "world_fact:"


def owner_artifact_id(ref: Any) -> Optional[str]:
    """The artifact owner's own id behind ``ref`` (``artifact:<uuid>``), or ``None``.

    Folds every leading ``artifact:`` into one, so the owner's id and this tree's domain wrapper for
    the same object compare equal -- and never masks, hashes or truncates the id itself.  A version
    ref names a *revision*, so it is not an artifact id.
    """

    text = str(ref or "").strip()
    while text.startswith(WORLD_FACT_WRAPPER):
        text = text[len(WORLD_FACT_WRAPPER):]
    if not text or text.startswith(VERSION_REF_PREFIX):
        return None
    if not text.startswith(OWNER_ARTIFACT_PREFIX):
        return None
    while text.startswith(OWNER_ARTIFACT_PREFIX):
        text = text[len(OWNER_ARTIFACT_PREFIX):]
    return "%s%s" % (OWNER_ARTIFACT_PREFIX, text) if text else None


def canonical_target_ref(ref: Any) -> Optional[str]:
    """The identity this cycle stores and compares for one target ref.

    * an artifact (in the owner's spelling *or* this tree's domain wrapper) -> ``artifact:<uuid>``;
    * ``artifactversion:…`` -> ``None`` (a revision is not a target);
    * ``world_fact:<x>`` -> ``<x>`` (LR-5's wrapper is not part of the identity);
    * ``place:``/``object:``/``bodyact:``/``dailyact:``/``stay:`` -> unchanged;
    * anything else -> ``None`` (unknown, never guessed).

    Both spellings of the same object therefore produce one identity, which is what lets a target
    recorded before a rebuild be looked up after it.
    """

    text = str(ref or "").strip()
    while text.startswith(WORLD_FACT_WRAPPER):
        text = text[len(WORLD_FACT_WRAPPER):]
    if not text:
        return None
    if text.startswith(VERSION_REF_PREFIX):
        return None
    owner = owner_artifact_id(text)
    if owner is not None:
        return owner
    if text.startswith((TARGET_STAY, TARGET_PLACE, TARGET_OBJECT, TARGET_BODY_ACTION,
                        TARGET_DAILY_ACTION)):
        return text
    return None


def is_artifact_target(ref: Any) -> bool:
    """Is this ref the *object* (not a revision, not a World target)?"""

    return owner_artifact_id(ref) is not None


@dataclass(frozen=True)
class StepReadiness:
    """Whether the foreground Activity still has one executable step, and on what grounds.

    ``available`` is deliberately tri-state: ``True`` (there is a step), ``False`` (there is
    positively none, on the grounds named by ``reason_code``) and ``None`` (not enough was read to
    say either way -- the caller must not turn that into "no step").
    """

    available: Optional[bool]
    reason_code: str
    activity_ref: Optional[str] = None
    activity_kind: Optional[str] = None
    target_ref: Optional[str] = None
    source_revision: Optional[str] = None
    detail: str = ""

    @property
    def bound(self) -> bool:
        """Is this a fact about one named Activity *and* one named target?"""

        return bool(self.activity_ref) and bool(self.target_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason_code": self.reason_code,
            "activity_ref": self.activity_ref,
            "activity_kind": self.activity_kind,
            "target_ref": self.target_ref,
            "source_revision": self.source_revision,
            "bound": self.bound,
            "detail": self.detail,
        }

    def fact_keys(self) -> dict[str, Any]:
        """The ``observed_facts`` entry for this readiness -- empty unless it is a real fact."""

        if self.available is False and self.bound:
            return {STEP_UNAVAILABLE_FACT_KEY: True}
        return {}


def assess_step_readiness(
    *,
    activity: Optional[Mapping[str, Any]] = None,
    target_ref: Optional[str] = None,
    source_revision: Optional[str] = None,
    reading_state: Optional[Mapping[str, Any]] = None,
    no_change: Optional[Mapping[str, Any]] = None,
    world_step_enabled: bool = False,
    caller_can_act: bool = True,
) -> StepReadiness:
    """Pure decision table for one foreground Activity.

    Order matters and is the whole contract:

    * no caller capability -> ``None`` (a fact about *this caller*, not about the step);
    * no foreground / not ACTIVE -> ``None`` (nothing to be ready for);
    * no bound target -> ``None`` (an unknown target is not "no step");
    * a stay -> its own internal checkpoint always exists;
    * a BODY Activity (its formal kind, or a ``bodyact:`` target) -> ``False``: one body target is
      one action with one completion condition, and no follow-up step exists in this tree;
    * the reading material -> its own situation's ``available`` (EXHAUSTED / REVOKED / ... is a
      *named* reason, and a page left is a real step);
    * a work whose writer already said ``no_change`` on this very revision -> ``False``;
    * a World target -> whether this process really has a World step adapter wired;
    * anything else -> ``None``.
    """

    ref = str((activity or {}).get("activity_ref")
              or (activity or {}).get("activity_id") or "") or None
    kind = str((activity or {}).get("activity_kind") or "") or None
    status = str((activity or {}).get("status") or "")
    revision = None if source_revision is None else str(source_revision)

    def build(available: Optional[bool], reason: str, detail: str = "") -> StepReadiness:
        return StepReadiness(available=available, reason_code=reason, activity_ref=ref,
                             activity_kind=kind, target_ref=(str(target_ref) if target_ref else None),
                             source_revision=revision, detail=detail)

    if not caller_can_act:
        return build(None, REASON_CALLER_CANNOT_ACT,
                     "this caller may not execute anything, so it cannot judge executability")
    if activity is None or not ref:
        return build(None, REASON_NO_FOREGROUND, "no foreground Activity was read")
    if status != "ACTIVE":
        return build(None, REASON_FOREGROUND_NOT_ACTIVE,
                     "the foreground Activity is %s, so there is no running step" % (status or "?"))
    if not target_ref:
        return build(None, REASON_NO_BOUND_TARGET,
                     "the foreground Activity names no target this cycle can execute")

    namespace = namespace_of(target_ref)
    if namespace is None:
        return build(None, REASON_UNKNOWN_TARGET,
                     "target %r is in no namespace this cycle executes" % (str(target_ref),))
    if kind == BODY_ACTIVITY_KIND or namespace == TARGET_BODY_ACTION:
        #: One body target == one action.  Its idempotency key already binds place + revision +
        #: target, its completion criterion is a single ``WORLD_FACT_MATCH``, and neither a same-id
        #: retry nor a repeated action is defined as progress -- so there is positively no next
        #: step to execute.  This is a statement about step availability only: it does not settle
        #: the Action, does not turn a pre-submission refusal into a terminal state, and does not
        #: say whether the one action succeeded.
        return build(False, REASON_BODY_SINGLE_ACTION,
                     "a BODY Activity carries one action and one completion condition; this tree "
                     "defines no further step for it")
    if namespace == TARGET_STAY or kind == STAY_ACTIVITY_KIND:
        return build(True, REASON_STAY_CHECKPOINT,
                     "a stay's step is its own internal checkpoint")
    if namespace == TARGET_ARTIFACT:
        if isinstance(reading_state, Mapping):
            state = _token(reading_state.get("state") or "UNKNOWN")
            if reading_state.get("available"):
                return build(True, REASON_READING_PREFIX + state,
                             "a page is left on the current revision")
            return build(False, REASON_READING_PREFIX + state,
                         str(reading_state.get("detail") or "")[:160])
        if isinstance(no_change, Mapping):
            recorded = _token(no_change.get("reason_code") or "NO_CHANGE")
            if str(no_change.get("source_revision") or "") == str(revision or ""):
                return build(False, REASON_WORK_NO_CHANGE,
                             "the writer reported %s on this revision; no version was written"
                             % recorded)
        return build(True, REASON_ARTIFACT_STEP_AVAILABLE,
                     "the work has a resumable head, so one more version is executable")
    if world_step_enabled:
        return build(True, REASON_WORLD_STEP_AVAILABLE,
                     "a real World step adapter is wired for this target")
    return build(False, REASON_WORLD_STEP_NOT_WIRED,
                 "no World step adapter is wired in this process, so this target has no step")


def merge_priority_facts(
    base: Optional[Mapping[str, Any]],
    priority: Optional[Mapping[str, Any]],
    *,
    max_keys: int = MAX_FACT_KEYS,
) -> dict[str, Any]:
    """One bounded ``observed_facts`` map with ``priority`` first.

    The reading/World merge (``subject_private_materials.merge_decision_facts``) is unchanged and
    still owns its own ordering; this is only the last, smallest step -- a single critical fact that
    must not be crowded out by a place dump.  Keys that do not fit the prompt's own bound are
    dropped (never truncated), and the caller reports what was dropped.
    """

    merged: dict[str, Any] = {}
    for source in (priority, base):
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            if len(merged) >= int(max_keys):
                return merged
            text = str(key)
            if not text or len(text) > 64:
                continue
            if not text.isascii() or not text.replace("_", "").isalnum():
                continue
            if text in merged:
                continue
            merged[text] = value
    return merged


# ---------------------------------------------------------------------------
# 2. The bounded reconsideration ledger
# ---------------------------------------------------------------------------

#: Lives in the cycle's own state root, beside the step journal and the continuity watermark.
RECONSIDERATION_FILENAME = "life_reconsideration.json"
RECONSIDERATION_SCHEMA = "chiyo.life.reconsideration.v1"
#: How long one recorded state stays suppressed.  Finite on purpose: a state nobody changed is
#: worth one more honest look eventually, and a day that never asks again is not a day either.
DEFAULT_TTL_SECONDS = 6 * 3600
#: Bounded, oldest-evicted.  This is a cache of *considerations*, not a history.
MAX_ENTRIES = 8
#: Outcomes that mean "she was asked about this state and nothing happened".
RECORDED_OUTCOMES = frozenset({"NO_ACTION", "DEFERRED", "NO_STEP_AVAILABLE", "ALREADY_DONE"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def fingerprint(*, target_ref: Any, source_revision: Any, reason_code: Any) -> str:
    """The identity of one *state*: this target, this revision, this reason.  Nothing else.

    Two rounds share a fingerprint only when nothing that could make the answer different moved.
    A new page, a new material revision, a different target or a different no-step reason is a new
    fingerprint, so the suppression never has to be lifted by hand for the normal cases.
    """

    material = "|".join(str(part or "") for part in (target_ref, source_revision, reason_code))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


#: The stable parts of one *situation*.  Only owner facts, and deliberately no timestamps, no
#: validity windows, no opportunity ids and no raw World revision: a World tick that changes nothing
#: she can perceive is not a new situation, while a new head / page / place / pose / opportunity is.
SITUATION_KEYS = (
    "foreground_activity_ref", "foreground_status", "foreground_kind",
    "target_ref", "target_revision", "step_reason",
    "reading_state", "reading_revision", "reading_next_page",
    "work_target", "work_revision",
    "place", "pose", "orientation",
    "opportunities", "stay_target",
    #: v0.4 B: an eligibility LR-4 just opened is a new situation -- it reopens a question
    #: this loop may already have settled (a decline recorded before the eligibility existed
    #: must not silence the resume).  Sorted eligibility ids only, no text.
    "resume_eligibility_refs",
    #: v0.4 stage A: what she herself planned is part of the situation.  A newly due "later" item
    #: or a newly supported project option is a real change (it reopens a settled question); the
    #: same set stays quiet, exactly like the World opportunity *set* does.  Sorted refs only --
    #: no item text ever enters the fingerprint.
    "due_items", "project_options",
    #: v0.4 research: the readable materials *at their revisions* and the research heads *with the
    #: source checks the owner reported*.  A re-imported or archived material, an unreadable
    #: document or a new research head is therefore a new situation, while a mere clock tick is
    #: not: nothing here is derived from time, and no text ever enters (ids, versions, status
    #: words only).
    "research_materials", "research_works",
    #: v0.4 C: what her own practice workspace holds -- the recallable experiences (id + when +
    #: outcome word), the methods (id + head + state) and the recent reviews (id + head).  A new
    #: experience, a withdrawn one, a method re-written or moved to TRIAL/CONTESTED is a new
    #: situation; a clock tick is not.  Ids, heads and state words only -- no text.
    "practice_experiences", "practice_methods", "practice_reviews",
)


def _situation_value(value: Any) -> str:
    """One comparable, bounded spelling of a situation part (no free text ever enters here)."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted({str(item) for item in value if str(item)})
        return ",".join(items)
    return str(value)


def situation_fingerprint(parts: Optional[Mapping[str, Any]]) -> str:
    """The identity of one *whole* narrow situation, over the whitelisted parts only.

    Two rounds share this fingerprint only when every part she could have acted on is the same:
    which Activity holds the foreground and in what state, what its step target is and at which
    revision, the private material's own state/revision/page, the work's head, where she is and in
    what pose, the stay plan, and the *set* of opportunity targets the World really offered.

    A new page, a new head, a new place or pose, a different foreground or a different opportunity
    set is therefore a new question; an unrelated World tick is not.
    """

    source = parts if isinstance(parts, Mapping) else {}
    material = "|".join(
        "%s=%s" % (key, _situation_value(source.get(key))) for key in SITUATION_KEYS)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def situation_parts_view(parts: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """The normalized parts, for a report -- the same bounded spelling the fingerprint hashes."""

    source = parts if isinstance(parts, Mapping) else {}
    return {key: _situation_value(source.get(key)) for key in SITUATION_KEYS}


class ReconsiderationLedger:
    """A tiny, bounded, expiring record of "this exact state was already considered".

    Two shapes live in the one file, both bounded and both expiring:

    * ``entries`` -- one *target* at one revision (``artifact:…`` + its head version) with the
      reason it had no step; this is what the readiness lookup reads back synchronously;
    * ``situations`` -- the whole narrow situation fingerprint (foreground + target + revision +
      reading + work head + place/pose + the opportunity *set*), which is what decides whether a
      round is worth a request at all.

    Neither stores private text, a model rationale or a Decision, so nothing here can be replayed as
    a decision and this is not a task list.
    """

    def __init__(self, state_root: Path | str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 max_entries: int = MAX_ENTRIES) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / RECONSIDERATION_FILENAME
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))

    # -- storage -------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _rows(self, name: str) -> list[dict[str, Any]]:
        rows = [dict(row) for row in (self._load().get(name) or [])
                if isinstance(row, Mapping) and row.get("fingerprint")]
        rows.sort(key=lambda row: str(row.get("last_at") or ""))
        return rows

    def entries(self) -> list[dict[str, Any]]:
        return self._rows("entries")

    def situations(self) -> list[dict[str, Any]]:
        return self._rows("situations")

    def _write(self, rows: Sequence[Mapping[str, Any]],
               situations: Optional[Sequence[Mapping[str, Any]]] = None) -> None:
        payload = {"schema_version": RECONSIDERATION_SCHEMA,
                   "kind": "life_reconsideration",
                   "ttl_seconds": self.ttl_seconds,
                   "entries": [dict(row) for row in rows][-self.max_entries:],
                   "situations": [dict(row) for row in (situations
                                                        if situations is not None
                                                        else self.situations())][-self.max_entries:],
                   "updated_at": _now_iso()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def expired(self, entry: Mapping[str, Any], *, at: Optional[str] = None) -> bool:
        last = _parse(entry.get("last_at"))
        moment = _parse(at) or _parse(_now_iso())
        if last is None or moment is None:
            return True
        return (moment - last).total_seconds() >= self.ttl_seconds

    def live_entries(self, *, at: Optional[str] = None) -> list[dict[str, Any]]:
        return [row for row in self.entries() if not self.expired(row, at=at)]

    def find(self, key: str, *, at: Optional[str] = None) -> Optional[dict[str, Any]]:
        for row in self.live_entries(at=at):
            if str(row.get("fingerprint")) == str(key):
                return row
        return None

    def no_change(self, target_ref: Any, source_revision: Any, *,
                  at: Optional[str] = None) -> Optional[dict[str, Any]]:
        """The recorded ``no_change`` for this target *on this revision*, or ``None``.

        A ``no_change`` on an older revision says nothing about the current one, which is exactly
        why the revision is part of both the lookup and the fingerprint.  The target is compared by
        its **canonical identity** (``canonical_target_ref``), so the owner's own ``artifact:<uuid>``
        and this tree's ``artifact:artifact:<uuid>`` wrapper for the same object are one target --
        otherwise a record written before a rebuild could never be found after it.
        """

        wanted = canonical_target_ref(target_ref) or str(target_ref or "")
        revision = str(source_revision or "")
        if not wanted or not revision:
            return None
        for row in self.live_entries(at=at):
            stored = canonical_target_ref(row.get("target_ref")) or str(row.get("target_ref") or "")
            if (stored == wanted
                    and str(row.get("source_revision") or "") == revision
                    and str(row.get("reason_code") or "") == REASON_WORK_NO_CHANGE):
                return row
        return None

    # -- writing -------------------------------------------------------------------

    def record(self, *, readiness: StepReadiness, outcome: str, at: Optional[str] = None,
               target_ref: Optional[str] = None, source_revision: Optional[str] = None,
               reason_code: Optional[str] = None,
               activity_ref: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Remember one target at one revision.  Only a target is required.

        ``readiness`` supplies the defaults, but a caller that already knows the *selected* target
        and its owner revision can record it even when no Activity holds the foreground yet (a
        ``START`` whose content writer answered ``no_change``).  Such an entry names the target and
        the revision it was said on -- never another Activity's target, and never a guessed
        revision: with no revision there is nothing to bind, so nothing is recorded.
        """

        source = target_ref if target_ref is not None else readiness.target_ref
        #: One identity per *object*: the owner's ``artifact:<id>`` and this tree's
        #: ``artifact:artifact:<id>`` wrapper are the same target, so what is stored is what a
        #: later lookup (``no_change``) compares -- never the spelling whoever spoke last used.
        wanted = canonical_target_ref(source) or str(source or "")
        revision = str(source_revision if source_revision is not None
                       else readiness.source_revision or "")
        reason = str(reason_code or readiness.reason_code or "")
        if not wanted or not revision or not reason:
            return None
        key = fingerprint(target_ref=wanted, source_revision=revision, reason_code=reason)
        moment = at or _now_iso()
        existing = self.entries()
        rows = [row for row in existing if str(row.get("fingerprint")) != key]
        previous = next((row for row in existing
                         if str(row.get("fingerprint")) == key), None)
        rows.append({
            "fingerprint": key,
            "target_ref": wanted,
            "source_revision": revision,
            "reason_code": reason,
            "outcome": str(outcome),
            "activity_ref": (activity_ref if activity_ref is not None
                             else readiness.activity_ref),
            "first_at": str((previous or {}).get("first_at") or moment),
            "last_at": moment,
            "count": int((previous or {}).get("count") or 0) + 1,
        })
        rows.sort(key=lambda row: str(row.get("last_at") or ""))
        self._write(rows[-self.max_entries:])
        return rows[-1]

    def record_situation(self, *, parts: Mapping[str, Any], outcome: str,
                         at: Optional[str] = None, target_ref: Optional[str] = None,
                         activity_ref: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Remember one whole narrow situation, so an identical one is not asked about again.

        This is the shape that makes "she already declined this exact situation" survive a restart:
        the parts are the owner facts the fingerprint hashes (never timestamps or validity
        windows), and only a decline outcome is stored.
        """

        key = situation_fingerprint(parts)
        moment = at or _now_iso()
        existing = self.situations()
        rows = [row for row in existing if str(row.get("fingerprint")) != key]
        previous = next((row for row in existing
                         if str(row.get("fingerprint")) == key), None)
        rows.append({
            "fingerprint": key,
            "parts": situation_parts_view(parts),
            "outcome": str(outcome),
            "target_ref": (str(target_ref) if target_ref else None),
            "activity_ref": (str(activity_ref) if activity_ref else None),
            "first_at": str((previous or {}).get("first_at") or moment),
            "last_at": moment,
            "count": int((previous or {}).get("count") or 0) + 1,
        })
        rows.sort(key=lambda row: str(row.get("last_at") or ""))
        self._write(self.entries(), rows[-self.max_entries:])
        return rows[-1]

    def find_situation(self, key: str, *, at: Optional[str] = None
                       ) -> Optional[dict[str, Any]]:
        for row in self.situations():
            if self.expired(row, at=at):
                continue
            if str(row.get("fingerprint")) == str(key):
                return row
        return None

    def release(self, *, refs: Iterable[Any] = (), key: Optional[str] = None,
                at: Optional[str] = None) -> list[str]:
        """Drop the records an explicit reason names; return the fingerprints that were released.

        Two callers only: an event whose own ref *is* the recorded target/activity (a real change
        about this object, never "any event at all"), and an operator/round asking for a fresh
        consideration.
        """

        wanted = {str(ref) for ref in refs if str(ref)}
        #: The same object can be named in this tree's domain spelling or in the owner's own
        #: ``artifact:<id>``, so an explicit release matches on the *identity* (``canonical_target_ref``)
        #: as well as on the literal ref -- one object, one release, no second spelling to remember.
        wanted_ids = {canonical_target_ref(ref) for ref in wanted} - {None}

        def doomed(row: Mapping[str, Any]) -> bool:
            if key is not None and str(row.get("fingerprint")) == str(key):
                return True
            if not wanted:
                return False
            target = str(row.get("target_ref") or "")
            return (target in wanted or canonical_target_ref(target) in wanted_ids
                    or str(row.get("activity_ref") or "") in wanted)

        released: list[str] = []
        kept: list[dict[str, Any]] = []
        for row in self.entries():
            if doomed(row):
                released.append(str(row.get("fingerprint")))
                continue
            kept.append(row)
        kept_situations: list[dict[str, Any]] = []
        for row in self.situations():
            if doomed(row):
                released.append(str(row.get("fingerprint")))
                continue
            kept_situations.append(row)
        if released:
            self._write(kept, kept_situations)
        return released

    # -- the one question the cycle asks --------------------------------------------

    def block_situation(self, *, parts: Mapping[str, Any], at: Optional[str] = None
                        ) -> dict[str, Any]:
        """Should this round skip the request because this *whole* situation was already declined?

        The old gate asked "is there a no-step Activity?" and then vetoed itself whenever any World
        opportunity happened to be visible -- which is always, so a quiet state could never stay
        quiet.  The question is not "does something exist", it is "is it the *same* something": the
        opportunity set is one of the hashed parts, so a real new opportunity is a new situation
        while a re-observed identical one is not.
        """

        key = situation_fingerprint(parts)
        view: dict[str, Any] = {"blocked": False, "reason_code": "NOT_SUPPRESSED",
                                "fingerprint": key, "entry": None,
                                "parts": situation_parts_view(parts)}
        entry = self.find_situation(key, at=at)
        if entry is None:
            view["reason_code"] = "STATE_NOT_CONSIDERED_YET"
            return view
        if str(entry.get("outcome") or "") not in RECORDED_OUTCOMES:
            view["reason_code"] = "RECORDED_OUTCOME_IS_NOT_A_DECLINE"
            return view
        view["blocked"] = True
        view["reason_code"] = "SAME_STATE_ALREADY_CONSIDERED"
        view["entry"] = dict(entry)
        return view

    def block(self, *, readiness: StepReadiness, at: Optional[str] = None,
              other_opportunities: Sequence[str] = ()) -> dict[str, Any]:
        """Report the keyed no-step state (kept for the readiness report and its tests).

        It no longer *vetoes* on the mere presence of another opportunity: that belongs to the
        whole-situation gate above, where the opportunity set is part of the identity.
        """

        view: dict[str, Any] = {
            "blocked": False,
            "reason_code": "NOT_SUPPRESSED",
            "fingerprint": None,
            "entry": None,
            "other_opportunities": [str(item) for item in other_opportunities],
        }
        if readiness.available is not False or not readiness.bound:
            view["reason_code"] = "NOT_A_BOUND_NO_STEP_STATE"
            return view
        key = fingerprint(target_ref=readiness.target_ref,
                          source_revision=readiness.source_revision,
                          reason_code=readiness.reason_code)
        view["fingerprint"] = key
        entry = self.find(key, at=at)
        if entry is None:
            view["reason_code"] = "STATE_NOT_CONSIDERED_YET"
            return view
        if str(entry.get("outcome") or "") not in RECORDED_OUTCOMES:
            view["reason_code"] = "RECORDED_OUTCOME_IS_NOT_A_DECLINE"
            return view
        view["blocked"] = True
        view["reason_code"] = "SAME_STATE_ALREADY_CONSIDERED"
        view["entry"] = dict(entry)
        return view


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_ENTRIES",
    "SITUATION_KEYS",
    "situation_fingerprint",
    "situation_parts_view",
    "MAX_FACT_KEYS",
    "RECONSIDERATION_FILENAME",
    "RECONSIDERATION_SCHEMA",
    "RECORDED_OUTCOMES",
    "REASON_ARTIFACT_STEP_AVAILABLE",
    "REASON_BODY_SINGLE_ACTION",
    "REASON_CALLER_CANNOT_ACT",
    "REASON_FOREGROUND_NOT_ACTIVE",
    "REASON_NO_BOUND_TARGET",
    "REASON_NO_FOREGROUND",
    "REASON_STAY_CHECKPOINT",
    "REASON_STEP_AVAILABLE",
    "REASON_UNKNOWN_TARGET",
    "REASON_WORK_NO_CHANGE",
    "REASON_WORLD_STEP_AVAILABLE",
    "REASON_WORLD_STEP_NOT_WIRED",
    "BODY_ACTIVITY_KIND",
    "ReconsiderationLedger",
    "STEP_UNAVAILABLE_FACT_KEY",
    "STAY_ACTIVITY_KIND",
    "StepReadiness",
    "TARGET_ARTIFACT",
    "TARGET_BODY_ACTION",
    "TARGET_DAILY_ACTION",
    "TARGET_OBJECT",
    "TARGET_PLACE",
    "TARGET_STAY",
    "assess_step_readiness",
    "canonical_target_ref",
    "fingerprint",
    "is_artifact_target",
    "merge_priority_facts",
    "namespace_of",
    "owner_artifact_id",
]
