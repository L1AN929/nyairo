#!/usr/bin/env python3
"""v0.5：她自己排一天 —— Life 日计划（自选）与外部义务（operator 只读）两个域。

为什么拆成两份文档
------------------
一天的安排里有一部分是她自己决定的，另一部分她不能决定（例如 operator 给的上学/打工）。
把两类塞进同一个文档会有两个写主体（她和外部），取消/改期权限只能在领域里按类型分流，
而且会改写既有 ``life_future_items`` 的边界（那一域明写"不是别人递给她的清单"）。因此：

* ``life_day_plan.json`` —— **只有她的提交 Decision 能写**。一条记录只引用一个既有的
  ``fitem:``（不复制标题正文），加业务日与必要的身份/版本。文件缺失 = 今天没排任何事，
  输出与旧行为逐 ref 相同。
* ``life_day_obligations.json`` —— **只有 operator 正规入口能写**，Life 只读。义务必须绑定
  一个已存在、可路由、可核结果的 ``artifact:`` 执行目标与一个完成依据；没有执行对象或目标
  不可读/不可续时具名拒绝，绝不把"标题"当"动作"。

本模块只提供纯领域：读写、校验、业务日换算、候选预留项构造、只读结果投影。
它不启动任何东西、不调用模型、不改任何 owner；"她今天做成了没有"只从本轮真实的
owner 回执另行投影，不写回义务文档，也不引入 MISSED / 日终顺延状态机。

业务日
------
业务日由调用方注入的业务 clock 按 ``+08:00`` 换算（与 Activity/手机 today 同一日界）。
安全预算、lease、权限仍走真实 UTC，不经过这里。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

PLAN_SCHEMA_VERSION = "chiyo.life.day_plan.v1"
PLAN_FILENAME = "life_day_plan.json"

OBLIGATIONS_SCHEMA_VERSION = "chiyo.life.day_obligations.v1"
OBLIGATIONS_FILENAME = "life_day_obligations.json"

PLAN_REF_PREFIX = "plan"
PLAN_REF_NAMESPACE = "plan:"
OBLIGATION_REF_PREFIX = "oblig"
OBLIGATION_REF_NAMESPACE = "oblig:"

#: 业务日界：+08:00（与 ``components/phone/service.py`` 的 today 视图同一日界）。
BUSINESS_UTC_OFFSET = timedelta(hours=8)
BUSINESS_TZ = timezone(BUSINESS_UTC_OFFSET)

#: 自选每天最多 3 条（0 条合法）。这是上限，不是配额。
MAX_PLAN_ENTRIES_PER_DAY = 3
MAX_OBLIGATION_TITLE_CHARS = 120
MAX_OBLIGATION_SOURCE_CHARS = 120

CLASS_SELF_PLANNED = "SELF_PLANNED"
ALL_CLASSES = (CLASS_SELF_PLANNED,)

PLAN_STATUS_PLANNED = "PLANNED"
PLAN_STATUS_ABANDONED = "ABANDONED"

SOURCE_SUBJECT_DECISION = "SUBJECT_DECISION"
SOURCE_OPERATOR = "OPERATOR"

#: 候选里的两类预留位。它们不是新的 owner 种类：仍然是 ``popt:`` 个人机会，
#: 只是带一个明确的今日类型，方便模型与报告分辨"我自己排的"和"外面给的"。
KIND_SELF_PLANNED = "TODAY_SELF_PLANNED"
KIND_OBLIGATION = "TODAY_OBLIGATION"
RESERVED_KINDS = (KIND_SELF_PLANNED, KIND_OBLIGATION)

OBLIGATION_STATUS_PLANNED = "PLANNED"
OBLIGATION_RESULT_PENDING = "PENDING"
OBLIGATION_RESULT_DONE = "DONE"
OBLIGATION_RESULT_UNKNOWN = "UNKNOWN"

COMPLETION_KIND_STEP_RECEIPT = "ARTIFACT_STEP_RECEIPT"
ALL_COMPLETION_KINDS = (COMPLETION_KIND_STEP_RECEIPT,)
#: Where a DONE row was read from.  ``PERSISTED_ACTION_RECEIPT`` means it was reconstructed from
#: the Action ledger + the Decision that named this obligation + the owner's own receipt, not from
#: this process's memory.
EVIDENCE_PATH_MEMORY = "CURRENT_STEP_RECEIPT"
EVIDENCE_PATH_PERSISTED = "PERSISTED_ACTION_RECEIPT"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ARTIFACT_REF_RE = re.compile(r"^artifact:[^\s]{1,120}$")
_FITEM_REF_RE = re.compile(r"^fitem:[^\s]{1,120}$")


class DayPlanError(RuntimeError):
    """This domain refused a read or a write.  ``reason_code`` always says which rule."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}:{detail}" if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)


# ---------------------------------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_iso(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_instant(value: Any) -> Optional[datetime]:
    """A public spelling of "parse an instant or answer None" for callers of this domain."""

    return _parse_iso(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def business_date(now: Any) -> str:
    """The +08:00 business day of ``now`` (a datetime or an ISO instant).

    The caller always passes its own business clock instant; this function never reads the wall
    clock itself, so a compressed day and a real day cannot disagree about "today".
    """

    moment = _parse_iso(now)
    if moment is None:
        raise DayPlanError("DAY_PLAN_NOW_INVALID", str(now)[:40])
    return moment.astimezone(BUSINESS_TZ).date().isoformat()


def _clean_date(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        raise DayPlanError("DAY_PLAN_DATE_INVALID", text[:40])
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise DayPlanError("DAY_PLAN_DATE_INVALID", text[:40]) from None
    return text


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise DayPlanError("OBLIGATION_TITLE_MISSING")
    if len(title) > MAX_OBLIGATION_TITLE_CHARS:
        raise DayPlanError("OBLIGATION_TITLE_TOO_LONG",
                           f"{len(title)} > {MAX_OBLIGATION_TITLE_CHARS}")
    return title


def _artifact_identity(value: Any) -> Optional[str]:
    """The owner's own bare artifact id named by a ref, or ``None``.

    ManagedArtifact ids are minted *with* the ``artifact:`` prefix and some seams add it again,
    so comparison must be about the object, not the spelling.
    """

    text = str(value or "").strip()
    if not text or text.startswith("artifactversion:"):
        return None
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text or None


def _owner_ref(value: Any) -> Optional[str]:
    bare = _artifact_identity(value)
    return f"artifact:{bare}" if bare else None


def artifact_identity(value: Any) -> Optional[str]:
    """The bare object id behind an ``artifact:`` ref (public spelling for other seams)."""

    return _artifact_identity(value)


def owner_ref(value: Any) -> Optional[str]:
    """The owner's canonical ``artifact:<bare>`` spelling, or ``None``."""

    return _owner_ref(value)


def obligation_option_ref(obligation_ref: Any, target_ref: Any, day: Any) -> str:
    """The deterministic ``popt:`` ref a today-obligation reserved slot is offered under.

    This is the one identity the candidate materializer and a later read-only result scan must
    agree on; keeping it here means a re-read after reassembly cannot silently invent a second
    spelling.  It validates its inputs like every other write/read in this domain.
    """

    ref = str(obligation_ref or "").strip()
    if not ref.startswith(OBLIGATION_REF_NAMESPACE):
        raise DayPlanError("OBLIGATION_REF_INVALID", ref[:80])
    target = _owner_ref(target_ref)
    if target is None:
        raise DayPlanError("OBLIGATION_TARGET_NOT_AN_ARTIFACT", str(target_ref or "")[:80])
    identity = "|".join([KIND_OBLIGATION, ref, target, _clean_date(day)])
    return f"popt:{_sha(identity)[:16]}"


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:  # pragma: no cover - directory fsync is best effort
        return
    try:
        os.fsync(dir_fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(dir_fd)


def _read_json(path: Path, *, unreadable: str, corrupt: str, mismatch: str,
               schema_version: str) -> Optional[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DayPlanError(unreadable, str(exc)[:120]) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DayPlanError(corrupt, str(exc)[:120]) from exc
    if not isinstance(data, dict) or data.get("schema_version") != schema_version:
        raise DayPlanError(mismatch, str(data)[:80])
    return data


# ---------------------------------------------------------------------------------------------
# 她自己排的今天（Life 自有，只有她的 Decision 写）
# ---------------------------------------------------------------------------------------------


class DayPlan:
    """The one store of "what she put into today", under one Life state root.

    A record is a *reference*: ``item_ref`` points at a real ``fitem:``; the title and the due
    time are read from that store, never copied.  Cancelling or rescheduling the item is still
    the existing ``future_item_actions`` path -- this store writes nothing on those events, it
    projects the item's own status at read time.
    """

    def __init__(self, state_root: Path | str, *, subject_id: Optional[str] = None) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / PLAN_FILENAME
        self.subject_id = str(subject_id) if subject_id else None

    # -- storage ----------------------------------------------------------------------

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": PLAN_SCHEMA_VERSION, "subject_id": self.subject_id,
                "revision": 0, "entries": {}}

    def read_state(self) -> dict[str, Any]:
        data = _read_json(self.path,
                          unreadable="DAY_PLAN_STATE_UNREADABLE",
                          corrupt="DAY_PLAN_STATE_CORRUPT",
                          mismatch="DAY_PLAN_STATE_SCHEMA_MISMATCH",
                          schema_version=PLAN_SCHEMA_VERSION)
        if data is None:
            return self._empty()
        entries = data.get("entries")
        data["entries"] = dict(entries) if isinstance(entries, dict) else {}
        data.setdefault("subject_id", self.subject_id)
        data.setdefault("revision", 0)
        return data

    def _write_state(self, state: Mapping[str, Any]) -> None:
        _atomic_write(self.path, {
            "schema_version": PLAN_SCHEMA_VERSION,
            "subject_id": state.get("subject_id"),
            "revision": int(state.get("revision") or 0),
            "entries": dict(state.get("entries") or {}),
        })

    # -- reads ------------------------------------------------------------------------

    def entries(self, *, date: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.read_state()
        wanted = _clean_date(date) if date is not None else None
        rows: list[dict[str, Any]] = []
        for raw in (state.get("entries") or {}).values():
            if not isinstance(raw, Mapping):
                raise DayPlanError("DAY_PLAN_STATE_CORRUPT", "entry is not a mapping")
            row = dict(raw)
            if not str(row.get("plan_ref") or "").startswith(PLAN_REF_NAMESPACE):
                raise DayPlanError("DAY_PLAN_STATE_CORRUPT", "entry has no plan_ref")
            row["date"] = _clean_date(row.get("date"))
            if wanted is not None and row["date"] != wanted:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (str(row.get("date") or ""),
                                   str(row.get("item_ref") or ""),
                                   str(row.get("plan_ref") or "")))
        return rows

    def for_date(self, date: str) -> list[dict[str, Any]]:
        return self.entries(date=date)

    def get(self, plan_ref: Any) -> Optional[dict[str, Any]]:
        ref = str(plan_ref or "")
        raw = (self.read_state().get("entries") or {}).get(ref)
        return dict(raw) if isinstance(raw, Mapping) else None

    # -- the one write ----------------------------------------------------------------

    def plan(self, item: Mapping[str, Any], *, date: Any, decision_ref: Any,
             now: Any = None, source: str = SOURCE_SUBJECT_DECISION) -> dict[str, Any]:
        """把一件既有事项排进 ``date`` 这一天（只允许今天），幂等且绑定提交的身份。

        ``item`` 是调用方刚从 FutureItems 读出的真实行，本函数不自己读那个 store：
        计划域只存引用与身份，标题/到期仍由 ``fitem:`` 那一边持有。

        返回 ``{"entry": ..., "created": bool, "reason_code": ...}``；不满足条件时抛
        ``DayPlanError``，调用方按具名拒绝报告，不静默失败。
        """

        moment = _parse_iso(now) or datetime.now(timezone.utc)
        today = business_date(moment)
        wanted_date = _clean_date(date)
        if wanted_date != today:
            # 首版只支持"排今天"：不新增日历/未来日排程语义。
            raise DayPlanError("DAY_PLAN_DATE_NOT_TODAY",
                               f"{wanted_date} != {today}")
        item_ref = str((item or {}).get("item_ref") or "").strip()
        if not _FITEM_REF_RE.match(item_ref):
            raise DayPlanError("DAY_PLAN_ITEM_REF_INVALID", item_ref[:80])
        if str((item or {}).get("status") or "").upper() != "OPEN":
            raise DayPlanError("DAY_PLAN_ITEM_NOT_OPEN",
                               str((item or {}).get("status") or "")[:40])
        item_subject = str((item or {}).get("subject_id") or "")
        if self.subject_id and item_subject and item_subject != self.subject_id:
            raise DayPlanError("DAY_PLAN_SUBJECT_MISMATCH", item_subject[:80])
        decision = str(decision_ref or "").strip()
        if not decision:
            raise DayPlanError("DAY_PLAN_DECISION_REF_MISSING")
        if source != SOURCE_SUBJECT_DECISION:
            raise DayPlanError("DAY_PLAN_SOURCE_NOT_A_SUBJECT_DECISION", str(source)[:40])

        intent = "|".join([decision, wanted_date, item_ref])
        idem = f"day_plan_intent:{_sha(intent)[:24]}"
        state = self.read_state()
        entries: dict[str, Any] = state["entries"]
        for existing in entries.values():
            if not isinstance(existing, Mapping):
                raise DayPlanError("DAY_PLAN_STATE_CORRUPT", "entry is not a mapping")
            if str(existing.get("idempotency_key") or "") == idem:
                return {"entry": dict(existing), "created": False}
        # 新 Decision 点名同一件事、同一天：不再多建一条（计划是"排进今天"，不是流水）。
        for existing in entries.values():
            if (isinstance(existing, Mapping)
                    and str(existing.get("date")) == wanted_date
                    and str(existing.get("item_ref")) == item_ref
                    and str(existing.get("status")) == PLAN_STATUS_PLANNED):
                return {"entry": dict(existing), "created": False, "deduplicated": True}

        used = sum(1 for row in entries.values()
                   if isinstance(row, Mapping)
                   and str(row.get("date")) == wanted_date
                   and str(row.get("status")) == PLAN_STATUS_PLANNED)
        if used >= MAX_PLAN_ENTRIES_PER_DAY:
            raise DayPlanError("DAY_PLAN_CAPACITY_FULL",
                               f"{used} planned on {wanted_date}")

        plan_ref = f"{PLAN_REF_NAMESPACE}{_sha(intent)[:16]}"
        observed = _iso(moment)
        entry = {
            "plan_ref": plan_ref,
            "date": wanted_date,
            "class": CLASS_SELF_PLANNED,
            "item_ref": item_ref,
            "status": PLAN_STATUS_PLANNED,
            "source": SOURCE_SUBJECT_DECISION,
            "decision_ref": decision,
            "revision": 1,
            "created_at": observed,
            "updated_at": observed,
            "idempotency_key": idem,
        }
        entries[plan_ref] = entry
        state["revision"] = int(state.get("revision") or 0) + 1
        self._write_state(state)
        return {"entry": dict(entry), "created": True}


# ---------------------------------------------------------------------------------------------
# 外部义务（operator 写，Life 只读）
# ---------------------------------------------------------------------------------------------


class DayObligations:
    """A **read-only** view of the operator's obligations for one Life state root.

    Deliberately has no write method: the Life cycle cannot create, change or cancel an
    obligation even by accident.  The writer is the module-level operator entry
    (:func:`import_obligation`), which is only reachable from ``scripts/own_day.py``.

    A missing file means "no obligations today" (old behaviour).  An unreadable/corrupt file or
    a different schema is a **named error**, never an empty table.
    """

    def __init__(self, state_root: Path | str, *, subject_id: Optional[str] = None) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / OBLIGATIONS_FILENAME
        self.subject_id = str(subject_id) if subject_id else None

    def read_state(self) -> dict[str, Any]:
        data = _read_json(self.path,
                          unreadable="OBLIGATIONS_STATE_UNREADABLE",
                          corrupt="OBLIGATIONS_STATE_CORRUPT",
                          mismatch="OBLIGATIONS_STATE_SCHEMA_MISMATCH",
                          schema_version=OBLIGATIONS_SCHEMA_VERSION)
        if data is None:
            return {"schema_version": OBLIGATIONS_SCHEMA_VERSION,
                    "subject_id": self.subject_id, "revision": 0, "entries": {}}
        entries = data.get("entries")
        data["entries"] = dict(entries) if isinstance(entries, dict) else {}
        data.setdefault("revision", 0)
        return data

    def entries(self, *, date: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.read_state()
        wanted = _clean_date(date) if date is not None else None
        rows: list[dict[str, Any]] = []
        for raw in (state.get("entries") or {}).values():
            if not isinstance(raw, Mapping):
                raise DayPlanError("OBLIGATIONS_STATE_CORRUPT", "entry is not a mapping")
            row = dict(raw)
            if not str(row.get("obligation_ref") or "").startswith(OBLIGATION_REF_NAMESPACE):
                raise DayPlanError("OBLIGATIONS_STATE_CORRUPT", "entry has no obligation_ref")
            row["date"] = _clean_date(row.get("date"))
            if wanted is not None and row["date"] != wanted:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (str(row.get("due_at") or ""),
                                   str(row.get("obligation_ref") or "")))
        return rows

    def for_date(self, date: str) -> list[dict[str, Any]]:
        return self.entries(date=date)


def _clean_completion(raw: Any, *, target_ref: str) -> dict[str, Any]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Mapping):
        raise DayPlanError("OBLIGATION_COMPLETION_MISSING")
    allowed = {"kind", "target_ref", "expected"}
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise DayPlanError("OBLIGATION_COMPLETION_UNKNOWN_FIELD",
                           ",".join(sorted(map(str, unknown))))
    kind = str(raw.get("kind") or "").strip()
    if kind not in ALL_COMPLETION_KINDS:
        raise DayPlanError("OBLIGATION_COMPLETION_KIND_UNSUPPORTED", kind[:60])
    completion_target = _owner_ref(raw.get("target_ref") or target_ref)
    if completion_target != target_ref:
        # 完成依据必须指向同一个执行对象；不能让"别的作品的版本"冒充本次完成。
        raise DayPlanError("OBLIGATION_COMPLETION_TARGET_MISMATCH",
                           str(raw.get("target_ref") or "")[:80])
    expected = str(raw.get("expected") or "SUCCEEDED").strip().upper()
    if expected != "SUCCEEDED":
        raise DayPlanError("OBLIGATION_COMPLETION_EXPECTATION_UNSUPPORTED", expected[:40])
    return {"kind": kind, "target_ref": completion_target, "expected": expected}


def import_obligation(
    state_root: Path | str,
    *,
    date: Any,
    title: Any,
    target_ref: Any,
    due_at: Any,
    source_ref: Any,
    operation_key: Any,
    completion: Optional[Mapping[str, Any]] = None,
    subject_id: Optional[str] = None,
    target_verifier: Any = None,
    expect_revision: Optional[int] = None,
    now: Any = None,
) -> dict[str, Any]:
    """The operator's one正规 entry: define an obligation bound to an existing work.

    There is deliberately no model path to this function.  The target must be an existing
    ``artifact:`` the owner answers for (``target_verifier`` is required: the CLI builds it from
    the instance's own Supply port), the completion basis must point at that same object, and a
    replayed ``operation_key`` returns the existing row instead of writing twice.
    """

    moment = _parse_iso(now) or datetime.now(timezone.utc)
    day = _clean_date(date)
    clean_title = _clean_title(title)
    target = _owner_ref(target_ref)
    if not _ARTIFACT_REF_RE.match(str(target_ref or "").strip()) or target is None:
        raise DayPlanError("OBLIGATION_TARGET_NOT_AN_ARTIFACT", str(target_ref or "")[:80])
    moment_due = _parse_iso(due_at)
    if moment_due is None:
        raise DayPlanError("OBLIGATION_DUE_AT_INVALID", str(due_at)[:40])
    if business_date(moment_due) != day:
        raise DayPlanError("OBLIGATION_DUE_DATE_MISMATCH",
                           f"{business_date(moment_due)} != {day}")
    source = " ".join(str(source_ref or "").split())[:MAX_OBLIGATION_SOURCE_CHARS]
    if not source:
        raise DayPlanError("OBLIGATION_SOURCE_REF_MISSING")
    operation = " ".join(str(operation_key or "").split())
    if not operation:
        raise DayPlanError("OBLIGATION_OPERATION_KEY_MISSING")
    if not callable(target_verifier):
        # 没有现场校验器就不写：一份"目标可能不存在"的义务不是可执行安排。
        raise DayPlanError("OBLIGATION_TARGET_VERIFIER_MISSING")
    try:
        verified = target_verifier(target)
    except Exception as exc:
        raise DayPlanError("OBLIGATION_TARGET_UNREADABLE",
                           f"{type(exc).__name__}: {exc}"[:160]) from exc
    if verified is None or verified is False:
        raise DayPlanError("OBLIGATION_TARGET_UNKNOWN", target[:80])
    if isinstance(verified, Mapping) and verified.get("resumable") is False:
        raise DayPlanError("OBLIGATION_TARGET_NOT_EXECUTABLE", target[:80])
    basis = _clean_completion(completion or {"kind": COMPLETION_KIND_STEP_RECEIPT},
                              target_ref=target)

    reader = DayObligations(state_root, subject_id=subject_id)
    state = reader.read_state()
    if expect_revision is not None and int(state.get("revision") or 0) != int(expect_revision):
        raise DayPlanError("OBLIGATION_STATE_REVISION_CONFLICT",
                           f"{state.get('revision')} != {expect_revision}")
    entries: dict[str, Any] = state["entries"]
    intent = "|".join([operation, day, target, clean_title])
    idem = f"obligation_intent:{_sha(intent)[:24]}"
    for existing in entries.values():
        if isinstance(existing, Mapping) and str(existing.get("idempotency_key")) == idem:
            return {"entry": dict(existing), "created": False}

    obligation_ref = f"{OBLIGATION_REF_NAMESPACE}{_sha(intent)[:16]}"
    observed = _iso(moment)
    entry = {
        "obligation_ref": obligation_ref,
        "date": day,
        "title": clean_title,
        "due_at": _iso(moment_due),
        "status": OBLIGATION_STATUS_PLANNED,
        "source": SOURCE_OPERATOR,
        "source_ref": source,
        "target_ref": target,
        "completion": dict(basis),
        "revision": 1,
        "created_at": observed,
        "updated_at": observed,
        "idempotency_key": idem,
    }
    entries[obligation_ref] = entry
    state["revision"] = int(state.get("revision") or 0) + 1
    state["schema_version"] = OBLIGATIONS_SCHEMA_VERSION
    if subject_id:
        state["subject_id"] = str(subject_id)
    _atomic_write(reader.path, {
        "schema_version": OBLIGATIONS_SCHEMA_VERSION,
        "subject_id": state.get("subject_id"),
        "revision": int(state.get("revision") or 0),
        "entries": dict(entries),
    })
    return {"entry": dict(entry), "created": True}


# ---------------------------------------------------------------------------------------------
# 决策提案的请求形状（AG-1 校验、DecisionRecord 重校验共用）
# ---------------------------------------------------------------------------------------------


def validate_plan_request(raw: Any) -> dict[str, Any]:
    """``plan_today`` 的唯一合法形状：``{"item_ref": "fitem:..."}``。

    日期不从模型来：今天是哪一天由业务 clock 决定，模型只能点名"把哪一件排进今天"。
    """

    if isinstance(raw, (str, bytes)) or not isinstance(raw, Mapping):
        raise DayPlanError("DAY_PLAN_REQUEST_NOT_A_MAPPING")
    allowed = {"item_ref"}
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise DayPlanError("DAY_PLAN_REQUEST_UNKNOWN_FIELD",
                           ",".join(sorted(map(str, unknown))))
    ref = str(raw.get("item_ref") or "").strip()
    if not _FITEM_REF_RE.match(ref):
        raise DayPlanError("DAY_PLAN_REQUEST_ITEM_REF_INVALID", ref[:80])
    return {"item_ref": ref}


# ---------------------------------------------------------------------------------------------
# 今日预留位（只读投影给候选构造）
# ---------------------------------------------------------------------------------------------


def _is_executable(point: Any) -> bool:
    if point is None or point is False:
        return False
    if isinstance(point, Mapping):
        return bool(point.get("resumable"))
    return bool(getattr(point, "resumable", False))


def project_plan_entries(entries: Iterable[Mapping[str, Any]], *,
                         futures_lookup: Any) -> list[dict[str, Any]]:
    """把计划行的状态按它所引用事项的**当前**状态投影出来（只读，不写回）。

    事项被取消/完成/不可读 → 这条计划不再算"今天要做的事"；它仍是历史事实，
    但不会占预留位，也不会被改写成别的终态。
    """

    out: list[dict[str, Any]] = []
    for raw in entries or ():
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        item = None
        try:
            item = futures_lookup(row.get("item_ref")) if callable(futures_lookup) else None
        except Exception as exc:  # 读失败具名报告，不当"完成"
            row["status"] = PLAN_STATUS_ABANDONED
            row["reason_code"] = "PLAN_ITEM_UNREADABLE"
            row["detail"] = f"{type(exc).__name__}: {exc}"[:120]
            out.append(row)
            continue
        if item is None:
            row["status"] = PLAN_STATUS_ABANDONED
            row["reason_code"] = "PLAN_ITEM_UNREADABLE"
        elif str(item.get("status") or "").upper() != "OPEN":
            row["status"] = PLAN_STATUS_ABANDONED
            row["reason_code"] = "PLAN_ITEM_CLOSED"
        else:
            row["status"] = PLAN_STATUS_PLANNED
            row["reason_code"] = None
            row["title"] = str(item.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS]
            row["due_at"] = str(item.get("due_at") or "")
            row["due"] = True
        out.append(row)
    return out


def obligation_result_view(obligations: DayObligations, *, date: Any, now: Any,
                           evidence: Optional[Mapping[str, Mapping[str, Any]]] = None
                           ) -> dict[str, Any]:
    """今天义务的只读结果投影。

    ``evidence`` 只来自本进程真实收到的 owner 回执（cycle 的 ``StepReceipt``），键是
    ``obligation_ref``。没有回执就是 ``PENDING``/``UNKNOWN``，**绝不**因为"同一作品有历史版本"
    就报完成；也绝不写回 operator 的文档。
    """

    day = _clean_date(date)
    moment = _parse_iso(now)
    receipts = dict(evidence or {})
    rows: list[dict[str, Any]] = []
    for entry in obligations.for_date(day):
        row = {
            "obligation_ref": str(entry.get("obligation_ref")),
            "date": day,
            "title": str(entry.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS],
            "due_at": str(entry.get("due_at") or ""),
            "target_ref": str(entry.get("target_ref") or ""),
            "source": str(entry.get("source") or ""),
            "definition_status": str(entry.get("status") or ""),
        }
        found = receipts.get(row["obligation_ref"])
        if isinstance(found, Mapping) and found.get("status") == OBLIGATION_RESULT_DONE:
            row["result"] = OBLIGATION_RESULT_DONE
            row["evidence"] = dict(found)
        elif isinstance(found, Mapping):
            row["result"] = OBLIGATION_RESULT_UNKNOWN
            row["evidence"] = dict(found)
        else:
            row["result"] = OBLIGATION_RESULT_PENDING
        due = _parse_iso(row["due_at"])
        row["due"] = bool(moment and due and due <= moment)
        rows.append(row)
    return {"status": "OK", "date": day, "entries": rows}


def record_obligation_receipt(evidence: dict[str, dict[str, Any]],
                              obligations: DayObligations, *, date: Any,
                              receipt: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """把本轮**真实**的 owner 步回执绑定到同目标的今日义务上（内存，不写文档）。

    只有 ``effect_state == APPLIED`` 的回执才算完成；失败/UNKNOWN 记成 UNKNOWN 证据，
    绝不改义务定义，也不伪造回执。返回绑定的证据行（没有匹配则 ``None``）。
    """

    if not isinstance(receipt, Mapping):
        return None
    artifact = _artifact_identity(receipt.get("artifact_id") or receipt.get("target_ref"))
    if not artifact:
        return None
    applied = str(receipt.get("effect_state") or "").upper() == "APPLIED"
    try:
        entries = obligations.for_date(_clean_date(date))
    except Exception:
        return None
    for entry in entries:
        target = _artifact_identity(entry.get("target_ref"))
        if target != artifact:
            continue
        ref = str(entry.get("obligation_ref") or "")
        if not ref:
            continue
        row = {
            "status": OBLIGATION_RESULT_DONE if applied else OBLIGATION_RESULT_UNKNOWN,
            "artifact_ref": f"artifact:{artifact}",
            "version_id": receipt.get("version_id"),
            "version_number": receipt.get("version_number"),
            "operation_key": receipt.get("operation_key"),
            "decision_id": receipt.get("decision_id"),
            "activity_ref": receipt.get("activity_ref"),
            "observed_at": receipt.get("observed_at"),
            "evidence_source": COMPLETION_KIND_STEP_RECEIPT,
            "evidence_path": EVIDENCE_PATH_MEMORY,
        }
        if not applied:
            row["reason_code"] = "STEP_NOT_APPLIED"
        evidence[ref] = row
        return dict(row)
    return None


def persisted_obligation_evidence(
    entries: Iterable[Mapping[str, Any]], *, day: Any,
    action_lister: Any, decision_getter: Any, receipt_lister: Any,
    candidate_id_for: Any,
) -> dict[str, dict[str, Any]]:
    """Rebuild today's obligation results from the **existing** persistent records only.

    The association has to be proven, not guessed:

    * an Action in the AR-0 ledger names the same real artifact target and has an ``activity_ref``;
    * its ``decision_ref`` resolves to a committed Decision that is a ``START`` and whose
      ``selected_candidate_ref`` is the extra personal-opportunity candidate this obligation's own
      deterministic reserved ``popt:`` ref would have become;
    * only then does a successful Action plus that Action's owner receipt (with a version) count as
      ``DONE``.

    A mere newer version of the same work, an Action by another Decision, an Action on another
    target, or a receipt without the association above is never treated as completion.  If an
    Action on the same target cannot be associated either way (missing Decision / missing
    selection), the honest answer is ``UNKNOWN`` -- not ``PENDING``, which would invite a second
    execution.  A definite failure/cancellation stays unprojected (``PENDING``): the obligation is
    still owed and a new Decision may retry it.

    This function only reads; it writes no result store and mutates neither the obligation
    document nor any owner.
    """

    day_text = _clean_date(day)
    rows = [dict(row) for row in (entries or ()) if isinstance(row, Mapping)]
    if not rows:
        return {}
    if not (callable(action_lister) and callable(decision_getter)
            and callable(candidate_id_for)):
        return {}
    try:
        actions = [dict(row) for row in (action_lister() or [])
                   if isinstance(row, Mapping)]
    except Exception:
        return {str(row.get("obligation_ref")): {
            "status": OBLIGATION_RESULT_UNKNOWN,
            "evidence_path": EVIDENCE_PATH_PERSISTED,
            "reason_code": "OBLIGATION_ACTION_READ_FAILED",
        } for row in rows if row.get("obligation_ref")}

    evidence: dict[str, dict[str, Any]] = {}
    for entry in rows:
        ref = str(entry.get("obligation_ref") or "")
        target = owner_ref(entry.get("target_ref"))
        if not ref or target is None:
            continue
        bare = artifact_identity(target)
        try:
            option_ref = obligation_option_ref(ref, target, day_text)
            expected_candidate = candidate_id_for(option_ref, 1)
        except Exception:
            expected_candidate = ""
        if not expected_candidate:
            continue
        unknown: Optional[dict[str, Any]] = None
        unknown_assoc: Optional[dict[str, Any]] = None
        for action in actions:
            if artifact_identity(action.get("target_ref")) != bare:
                continue
            action_id = str(action.get("action_id") or "")
            activity_ref = str(action.get("activity_ref") or "")
            decision_ref = str(action.get("decision_ref") or "")
            if not decision_ref:
                unknown_assoc = unknown_assoc or {
                    "action_id": action_id, "activity_ref": activity_ref,
                    "reason_code": "OBLIGATION_ASSOCIATION_UNVERIFIED"}
                continue
            try:
                decision = decision_getter(decision_ref)
            except Exception:
                decision = None
            if not isinstance(decision, Mapping):
                unknown_assoc = unknown_assoc or {
                    "action_id": action_id, "activity_ref": activity_ref,
                    "decision_id": decision_ref,
                    "reason_code": "OBLIGATION_ASSOCIATION_UNVERIFIED"}
                continue
            selected = str(decision.get("selected_candidate_ref") or "")
            if not selected:
                unknown_assoc = unknown_assoc or {
                    "action_id": action_id, "activity_ref": activity_ref,
                    "decision_id": decision_ref,
                    "reason_code": "OBLIGATION_ASSOCIATION_UNVERIFIED"}
                continue
            if selected != expected_candidate:
                # A known, different Decision.  It is not this obligation, and its work is not
                # claimed here; the obligation remains pending rather than becoming UNKNOWN.
                continue
            if str(decision.get("decision") or "").upper() != "START":
                # The candidate matches but the Decision did not start it; an Action under a
                # non-START Decision is not a completion this obligation may claim.
                unknown = unknown or {
                    "action_id": action_id, "decision_id": decision_ref,
                    "activity_ref": activity_ref,
                    "reason_code": "OBLIGATION_ASSOCIATION_UNVERIFIED"}
                continue
            status = str(action.get("status") or "").upper()
            if status == "SUCCEEDED":
                receipt = _matching_owner_receipt(
                    action_id, receipt_lister=receipt_lister)
                version_id = str((receipt or {}).get("details", {}).get("version_id") or "")
                if not activity_ref:
                    unknown = unknown or {
                        "action_id": action_id, "decision_id": decision_ref,
                        "reason_code": "OBLIGATION_ACTIVITY_ASSOCIATION_MISSING"}
                    continue
                if receipt is None or not version_id:
                    unknown = unknown or {
                        "action_id": action_id, "decision_id": decision_ref,
                        "activity_ref": activity_ref,
                        "reason_code": "OBLIGATION_OWNER_RECEIPT_MISSING"}
                    continue
                details = dict(receipt.get("details") or {})
                evidence[ref] = {
                    "status": OBLIGATION_RESULT_DONE,
                    "artifact_ref": f"artifact:{bare}",
                    "version_id": version_id,
                    "version_number": details.get("version_number"),
                    "operation_key": str(receipt.get("provider_operation_ref")
                                         or details.get("operation_key") or ""),
                    "decision_id": decision_ref,
                    "activity_ref": activity_ref,
                    "action_id": action_id,
                    "receipt_ref": str(receipt.get("receipt_id") or ""),
                    "observed_at": str(receipt.get("observed_at") or ""),
                    "evidence_source": COMPLETION_KIND_STEP_RECEIPT,
                    "evidence_path": EVIDENCE_PATH_PERSISTED,
                }
                break
            if status == "UNKNOWN":
                unknown = unknown or {
                    "action_id": action_id, "decision_id": decision_ref,
                    "activity_ref": activity_ref,
                    "reason_code": "OBLIGATION_ACTION_OUTCOME_UNKNOWN"}
            elif status in {"FAILED", "CANCELLED"}:
                # Definite failure: the obligation is still owed, so leave it PENDING.
                continue
            else:
                unknown = unknown or {
                    "action_id": action_id, "decision_id": decision_ref,
                    "activity_ref": activity_ref,
                    "reason_code": "OBLIGATION_ACTION_NOT_SETTLED"}
        if ref in evidence:
            continue
        row = unknown or unknown_assoc
        if row is not None:
            evidence[ref] = {
                "status": OBLIGATION_RESULT_UNKNOWN,
                "artifact_ref": f"artifact:{bare}",
                "action_id": row.get("action_id"),
                "activity_ref": row.get("activity_ref"),
                "decision_id": row.get("decision_id"),
                "evidence_source": COMPLETION_KIND_STEP_RECEIPT,
                "evidence_path": EVIDENCE_PATH_PERSISTED,
                "reason_code": str(row.get("reason_code")
                                   or "OBLIGATION_ASSOCIATION_UNVERIFIED"),
            }
        # else: no association and no ambiguity -> PENDING (a definite failure is still owed).
    return evidence


def _matching_owner_receipt(action_id: Any, *, receipt_lister: Any
                            ) -> Optional[dict[str, Any]]:
    """The last owner receipt for ``action_id`` that claims success and names a version."""

    if not action_id or not callable(receipt_lister):
        return None
    try:
        receipts = [dict(row) for row in (receipt_lister(str(action_id)) or [])
                    if isinstance(row, Mapping)]
    except Exception:
        return None
    for receipt in reversed(receipts):
        details = receipt.get("details")
        details = dict(details) if isinstance(details, Mapping) else {}
        claim = str(receipt.get("status_claim") or "").upper()
        if claim == "SUCCEEDED" and details.get("version_id"):
            return receipt
    return None


def reserved_options(*, plan_entries: Iterable[Mapping[str, Any]],
                     obligations: Iterable[Mapping[str, Any]], now: Any,
                     futures_lookup: Any, target_reader: Any = None,
                     paused_targets: Optional[Iterable[Any]] = None,
                     limit: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """今天的两类预留位：最多 1 条自选 + 1 条义务（各自按到期/ref 稳定取最早）。

    返回 ``(options, withheld)``。``withheld`` 是具名未提供的行（不可读/不可执行/挂起），
    不静默当成"今天没有安排"。

    每个 option 都保持 ``popt:`` 个人机会的形状，只多出 ``kind``/``reserved``/``work_ref``：
    * 自选：引用既有 ``fitem:``；有作品对象时 ``work_ref`` 指向它，没有时按普通新项目选项处理。
    * 义务：``work_ref`` 必须指向 operator 绑定的 ``artifact:`` 执行对象。
    """

    moment = _parse_iso(now)
    if moment is None:
        raise DayPlanError("DAY_PLAN_NOW_INVALID", str(now)[:40])
    day = business_date(moment)
    paused = {ident for ident in (_artifact_identity(ref) for ref in (paused_targets or ()))
              if ident}
    cap = max(1, int(limit))
    self_rows: list[dict[str, Any]] = []
    obligation_rows: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []

    for entry in project_plan_entries(plan_entries, futures_lookup=futures_lookup):
        if str(entry.get("date")) != day:
            continue
        if str(entry.get("status")) != PLAN_STATUS_PLANNED:
            withheld.append({"kind": KIND_SELF_PLANNED,
                             "ref": str(entry.get("plan_ref") or ""),
                             "item_ref": str(entry.get("item_ref") or ""),
                             "reason_code": str(entry.get("reason_code") or "PLAN_NOT_ACTIVE")})
            continue
        item_ref = str(entry.get("item_ref") or "")
        item = futures_lookup(item_ref) if callable(futures_lookup) else None
        if item is None:
            withheld.append({"kind": KIND_SELF_PLANNED, "ref": entry.get("plan_ref"),
                             "item_ref": item_ref, "reason_code": "PLAN_ITEM_UNREADABLE"})
            continue
        due = _parse_iso(item.get("due_at"))
        if due is None or due > moment:
            # 排进今天但还没到点：不提前变成可开工的候选；到期后自然进入。
            continue
        artifact = next((str(ref) for ref in (item.get("source_refs") or [])
                         if _artifact_identity(ref)), None)
        identity = "|".join([KIND_SELF_PLANNED, str(entry.get("plan_ref")),
                             item_ref, day])
        self_rows.append({
            "option_ref": f"popt:{_sha(identity)[:16]}",
            "kind": KIND_SELF_PLANNED,
            "title": str(item.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS],
            "seed_text": str(item.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS],
            "source_refs": [item_ref] + ([artifact] if artifact else []),
            "basis_ref": str(entry.get("plan_ref") or ""),
            "material_ref": artifact,
            "work_ref": artifact,
            "note": f"date={day};item={item_ref}",
            "reserved": True,
            "class": CLASS_SELF_PLANNED,
            "date": day,
            "due_at": str(item.get("due_at") or ""),
            "dedupe_refs": [item_ref, str(entry.get("plan_ref") or "")],
        })

    for entry in obligations or ():
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("date")) != day:
            continue
        if str(entry.get("status") or "").upper() != OBLIGATION_STATUS_PLANNED:
            continue
        ref = str(entry.get("obligation_ref") or "")
        target = _owner_ref(entry.get("target_ref"))
        due = _parse_iso(entry.get("due_at"))
        if due is None:
            withheld.append({"kind": KIND_OBLIGATION, "ref": ref, "target_ref": target,
                             "reason_code": "OBLIGATION_DUE_AT_INVALID"})
            continue
        if due > moment:
            continue
        if target is None:
            withheld.append({"kind": KIND_OBLIGATION, "ref": ref,
                             "reason_code": "OBLIGATION_TARGET_NOT_AN_ARTIFACT"})
            continue
        if _artifact_identity(target) in paused:
            # 挂起中的对象是既有 RESUME/资格门的事，预留位也不绕过它。
            withheld.append({"kind": KIND_OBLIGATION, "ref": ref, "target_ref": target,
                             "reason_code": "PAUSED_TARGET_REQUIRES_RESUME"})
            continue
        try:
            point = target_reader(target) if callable(target_reader) else None
        except Exception as exc:
            withheld.append({"kind": KIND_OBLIGATION, "ref": ref, "target_ref": target,
                             "reason_code": "OBLIGATION_TARGET_UNREADABLE",
                             "detail": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        if not _is_executable(point):
            withheld.append({"kind": KIND_OBLIGATION, "ref": ref, "target_ref": target,
                             "reason_code": "OBLIGATION_TARGET_NOT_EXECUTABLE"})
            continue
        option_ref = obligation_option_ref(ref, target, day)
        obligation_rows.append({
            "option_ref": option_ref,
            "kind": KIND_OBLIGATION,
            "title": str(entry.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS],
            "seed_text": str(entry.get("title") or "")[:MAX_OBLIGATION_TITLE_CHARS],
            "source_refs": [ref, target],
            "basis_ref": ref,
            "material_ref": target,
            "work_ref": target,
            "note": f"date={day};target={target}",
            "reserved": True,
            "class": KIND_OBLIGATION,
            "date": day,
            "due_at": str(entry.get("due_at") or ""),
            "dedupe_refs": [ref, target],
        })

    self_rows.sort(key=lambda row: (str(row.get("due_at") or ""), str(row.get("option_ref") or "")))
    obligation_rows.sort(key=lambda row: (str(row.get("due_at") or ""),
                                          str(row.get("option_ref") or "")))
    return self_rows[:cap] + obligation_rows[:cap], withheld


__all__ = [
    "ALL_CLASSES",
    "ALL_COMPLETION_KINDS",
    "BUSINESS_TZ",
    "BUSINESS_UTC_OFFSET",
    "CLASS_SELF_PLANNED",
    "COMPLETION_KIND_STEP_RECEIPT",
    "DayObligations",
    "DayPlan",
    "DayPlanError",
    "KIND_OBLIGATION",
    "KIND_SELF_PLANNED",
    "MAX_OBLIGATION_TITLE_CHARS",
    "MAX_PLAN_ENTRIES_PER_DAY",
    "OBLIGATIONS_FILENAME",
    "OBLIGATIONS_SCHEMA_VERSION",
    "OBLIGATION_RESULT_DONE",
    "OBLIGATION_RESULT_PENDING",
    "OBLIGATION_RESULT_UNKNOWN",
    "OBLIGATION_STATUS_PLANNED",
    "PLAN_FILENAME",
    "PLAN_SCHEMA_VERSION",
    "PLAN_STATUS_ABANDONED",
    "PLAN_STATUS_PLANNED",
    "RESERVED_KINDS",
    "SOURCE_OPERATOR",
    "SOURCE_SUBJECT_DECISION",
    "EVIDENCE_PATH_MEMORY",
    "EVIDENCE_PATH_PERSISTED",
    "artifact_identity",
    "business_date",
    "import_obligation",
    "obligation_option_ref",
    "obligation_result_view",
    "owner_ref",
    "parse_instant",
    "persisted_obligation_evidence",
    "project_plan_entries",
    "record_obligation_receipt",
    "reserved_options",
    "validate_plan_request",
]
