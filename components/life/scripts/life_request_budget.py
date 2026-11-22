#!/usr/bin/env python3
"""W05R: the persistent, atomically reserved request budget.

Why the W05 ``RoundBudget`` was not enough
-----------------------------------------
W05 counted *rounds* and *actions* per local day, and it counted cognition calls
*after* the fact (``note_cognition``), in the same file it rewrote wholesale.  The
launch plan then had to admit that "96" only described decisions, that content
generation was counted per process, and that a corrupt or rolled-back budget file
simply read as zero.  None of that is a hard limit on what the operator pays for.

What this module is
-------------------
One small persisted ledger, protected by a cross-process ``flock``, that is asked
**before** an HTTP attempt is made and never gives anything back:

* ``reserve(kind)`` checks the UTC-day limit and the *run window* limit, then
  persists the increment with a unique temporary name, ``flush``, ``fsync``,
  ``os.replace`` and a directory ``fsync``.  Only then does the caller send.
* There is deliberately **no** release/refund API.  A timeout, a crash, a retry that
  never came back and a schema rejection all keep their reservation, because in every
  one of those cases the provider may already have been paid.
* A corrupt or unreadable ledger refuses everything (``fail closed``) instead of
  reading as "nothing spent yet".
* Wall-clock rollback does not reset anything: the *active* UTC day is monotonic, so
  a clock that jumps backwards keeps counting into the day that was already open, and
  a clock that jumps forwards starts a new day while the history is retained.
* A restart continues the **same run window** when it carries the same run identity.
  Opening a *new* window is an explicit act (``ensure_window(..., allow_new=True)``),
  so a plain restart can never mint itself a fresh 24-hour allowance.

Kinds
-----
``decision`` / ``content`` / ``retry``.  All three are counted against both ceilings,
which is what "total request ceiling" has to mean.

The daily token budget (07 章 §四 / T3-wave2)
---------------------------------------------
A third, **parallel** ceiling: the per-business-day sum of the reservations' estimated
token cost (``est_tokens``).  It is configured explicitly (``daily_token_budget``) --
``None`` means "no ceiling of that kind", exactly like ``utc_day_max``/``run_window_max``,
never "unlimited"; ``DEFAULT_DAILY_TOKEN_BUDGET`` (50_000, 07 章 §四 ``tokens_max``) is the
module-level value a caller enables the dimension with.  A reservation without a
ticket-supplied estimate is charged the conservative constant
``DEFAULT_CONSERVATIVE_TOKEN_RESERVATION`` (50_000/24 ≈ 2083, rounded down to 2000, so 24
constant-rate reservations still stay under the daily token budget and the *call* ceiling
binds first).  The same iron laws apply unchanged: the reservation is persisted **before**
the HTTP attempt, nothing is ever refunded (a timeout, a crash or a retry keeps its
estimate), a corrupt ledger refuses everything, and a wall-clock rollback never resets the
active day's token count.
"""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

import life_pause
try:  # POSIX only; the Life state root already requires it.
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = "life.request_budget.v1"
KIND = "life_request_budget"
BUDGET_FILENAME = "life_request_budget.json"
LOCK_FILENAME = "life_request_budget.lock"

STATE_ABSENT = "absent"
STATE_OK = "ok"
STATE_CORRUPT = "corrupt"

REQUEST_KIND_DECISION = "decision"
REQUEST_KIND_CONTENT = "content"
REQUEST_KIND_RETRY = "retry"
REQUEST_KINDS = (REQUEST_KIND_DECISION, REQUEST_KIND_CONTENT, REQUEST_KIND_RETRY)

REASON_STATE_CORRUPT = "REQUEST_BUDGET_STATE_CORRUPT"
REASON_UTC_DAY_LIMIT = "REQUEST_BUDGET_UTC_DAY_LIMIT"
REASON_RUN_WINDOW_LIMIT = "REQUEST_BUDGET_RUN_WINDOW_LIMIT"
REASON_RUN_WINDOW_NOT_AUTHORIZED = "REQUEST_BUDGET_RUN_WINDOW_NOT_AUTHORIZED"
REASON_UNREADABLE = "REQUEST_BUDGET_UNREADABLE"
REASON_DAILY_TOKEN_BUDGET = "REQUEST_BUDGET_DAILY_TOKEN_BUDGET"

#: 07 章 §四 DailyEnvelope.tokens_max：日 token 预算的默认值（显式启用该维度的调用方使用）。
DEFAULT_DAILY_TOKEN_BUDGET = 50_000
#: 无票 est（``est_tokens=None``）时的保守常数：50_000/24 ≈ 2083，向下取 2000，
#: 24 次全按保守常数计（48_000）仍低于日 token 预算，调用上限先绑定。
DEFAULT_CONSERVATIVE_TOKEN_RESERVATION = 2000

#: 07 章 §四 SlotQuota / DailyEnvelope：时段槽位与日分类包络（v0.8 Wave 7 最小实装）。
#: 仅在调用方显式 ``enforce_slot_envelope=True`` 时生效，默认调用路径与此前逐字节一致。
SLOT_DAYTIME_ACTIVE = "DAYTIME_ACTIVE"
SLOT_EVENING_WIND_DOWN = "EVENING_WIND_DOWN"
SLOT_NIGHT_REST = "NIGHT_REST"
SLOT_QUOTAS: dict[str, int] = {
    SLOT_DAYTIME_ACTIVE: 2,
    SLOT_EVENING_WIND_DOWN: 1,
    SLOT_NIGHT_REST: 0,
}
CATEGORY_INITIATION = "INITIATION"
CATEGORY_CONTINUATION = "CONTINUATION"
CATEGORY_REFLECTION = "REFLECTION"
DAILY_ENVELOPE_CAPS: dict[str, Any] = {
    "daily_hard_cap": 8,
    "per_category_cap": {
        CATEGORY_INITIATION: 3,
        CATEGORY_CONTINUATION: 5,
        CATEGORY_REFLECTION: 2,
    },
}
REASON_SLOT_QUOTA_EXCEEDED = "REQUEST_BUDGET_SLOT_QUOTA_EXCEEDED"
REASON_DAILY_ENVELOPE_EXCEEDED = "REQUEST_BUDGET_DAILY_ENVELOPE_EXCEEDED"
REASON_UNKNOWN_SLOT_OR_CATEGORY = "REQUEST_BUDGET_UNKNOWN_SLOT_OR_CATEGORY"
MAX_SLOT_ENVELOPE_OPS = 64

#: 07 章 §四 四时段配额与结转（v0.8 补全）。与上方的三槽 ``SLOT_QUOTAS`` 并存：
#: 三槽是既有默认路径（仅在 ``enforce_slot_envelope=True`` 时生效），四时段只在调用方
#: 显式 ``slot_mode="v03_four_slot"`` 时启用，默认调用与旧账本形状逐字节不变。
#: 业务时区内按 ``morning 06-12 / afternoon 12-18 / evening 18-22 / night 22-06`` 划段；
#: 同一业务日内前一已过时段的未用完配额按 ``V03_SLOT_CARRY_RATIO`` 折半滚入下一时段，
#: 但单日四个时段的总支出绝不超过日硬顶 24 次 / 50000 tokens；跨午夜进入新业务日时
#: 早段无可继承的已过时段，结转自然归零（不攒跨天债）。
V03_FOUR_SLOT_QUOTAS: dict[str, dict[str, int]] = {
    "morning": {"calls": 6, "tokens": 12500},
    "afternoon": {"calls": 7, "tokens": 14000},
    "evening": {"calls": 7, "tokens": 14000},
    "night": {"calls": 4, "tokens": 9500},
}
V03_FOUR_SLOT_ORDER: tuple[str, ...] = ("morning", "afternoon", "evening", "night")
V03_SLOT_CARRY_RATIO: float = 0.5
V03_FOUR_SLOT_DAILY_CALL_CAP = sum(row["calls"] for row in V03_FOUR_SLOT_QUOTAS.values())
V03_FOUR_SLOT_DAILY_TOKEN_CAP = sum(row["tokens"] for row in V03_FOUR_SLOT_QUOTAS.values())
V03_SLOT_MODE = "v03_four_slot"
REASON_V03_SLOT_QUOTA_EXCEEDED = "REQUEST_BUDGET_V03_SLOT_QUOTA_EXCEEDED"
REASON_V03_DAILY_CAP_EXCEEDED = "REQUEST_BUDGET_V03_DAILY_CAP_EXCEEDED"
REASON_UNKNOWN_V03_SLOT = "REQUEST_BUDGET_UNKNOWN_V03_SLOT"


def v03_slot_for_hour(hour: int) -> str:
    """Map a local business-time hour to its four-slot name (22-06 is ``night``)."""

    h = int(hour) % 24
    if 6 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "afternoon"
    if 18 <= h < 22:
        return "evening"
    return "night"


def _v03_elapsed_slots_for_hour(hour: int) -> tuple[str, ...]:
    """The slots whose window has already closed within the current business day.

    ``00:00-06:00`` is the *night of a new business day*: no earlier slot of that day has
    elapsed yet, so nothing carries in -- this is exactly the "cross-midnight zero carry"
    rule, expressed as data rather than a special case in the caller.
    """

    h = int(hour) % 24
    if 6 <= h < 12:
        return ()
    if 12 <= h < 18:
        return ("morning",)
    if 18 <= h < 22:
        return ("morning", "afternoon")
    if 22 <= h < 24:
        return ("morning", "afternoon", "evening")
    return ()


def _v03_used_calls_tokens(row: Any) -> tuple[int, int]:
    if not isinstance(row, Mapping):
        return 0, 0
    raw_calls = row.get("calls")
    raw_tokens = row.get("tokens")
    calls = max(0, int(raw_calls)) if isinstance(raw_calls, int) and not isinstance(raw_calls, bool) else 0
    tokens = max(0, int(raw_tokens)) if isinstance(raw_tokens, int) and not isinstance(raw_tokens, bool) else 0
    return calls, tokens


def v03_four_slot_effective(
    day_bucket: Any,
    hour: int,
    *,
    daily_call_cap: int = V03_FOUR_SLOT_DAILY_CALL_CAP,
    daily_token_cap: int = V03_FOUR_SLOT_DAILY_TOKEN_CAP,
) -> dict[str, Any]:
    """Pure, read-only effective-quota projection for one business day.

    Each slot's effective quota is its base plus half of the *immediately preceding* slot's
    unused effective quota, but only when that preceding slot has actually elapsed inside
    this business day.  The per-day totals are the raw usage sum across all four slots and
    are capped at the daily hard envelope (24 calls / 50000 tokens).
    """

    raw_slots = (
        day_bucket.get("slots")
        if isinstance(day_bucket, Mapping) and isinstance(day_bucket.get("slots"), Mapping)
        else {}
    )
    elapsed = set(_v03_elapsed_slots_for_hour(hour))
    slots_out: dict[str, Any] = {}
    prev_eff_calls: Optional[int] = None
    prev_used_calls = 0
    prev_eff_tokens: Optional[int] = None
    prev_used_tokens = 0
    prev_elapsed = False
    for slot in V03_FOUR_SLOT_ORDER:
        base = V03_FOUR_SLOT_QUOTAS[slot]
        carry_calls = 0
        carry_tokens = 0
        if prev_eff_calls is not None and prev_elapsed:
            carry_calls = int(math.floor(
                max(0, prev_eff_calls - prev_used_calls) * V03_SLOT_CARRY_RATIO))
            carry_tokens = int(math.floor(
                max(0, prev_eff_tokens - prev_used_tokens) * V03_SLOT_CARRY_RATIO))
        eff_calls = int(base["calls"]) + carry_calls
        eff_tokens = int(base["tokens"]) + carry_tokens
        used_calls, used_tokens = _v03_used_calls_tokens(raw_slots.get(slot))
        slots_out[slot] = {
            "calls": {
                "base": int(base["calls"]),
                "carry": carry_calls,
                "effective": eff_calls,
                "used": used_calls,
                "remaining": max(0, eff_calls - used_calls),
            },
            "tokens": {
                "base": int(base["tokens"]),
                "carry": carry_tokens,
                "effective": eff_tokens,
                "used": used_tokens,
                "remaining": max(0, eff_tokens - used_tokens),
            },
        }
        prev_eff_calls, prev_used_calls = eff_calls, used_calls
        prev_eff_tokens, prev_used_tokens = eff_tokens, used_tokens
        prev_elapsed = slot in elapsed

    total_calls = sum(row["calls"]["used"] for row in slots_out.values())
    total_tokens = sum(row["tokens"]["used"] for row in slots_out.values())
    return {
        "slots": slots_out,
        "slot_order": list(V03_FOUR_SLOT_ORDER),
        "current_slot": v03_slot_for_hour(hour),
        "local_hour": int(hour) % 24,
        "total_calls_used": total_calls,
        "total_tokens_used": total_tokens,
        "daily_call_cap": int(daily_call_cap),
        "daily_token_cap": int(daily_token_cap),
        "daily_calls_remaining": max(0, int(daily_call_cap) - total_calls),
        "daily_tokens_remaining": max(0, int(daily_token_cap) - total_tokens),
        "carry_ratio": V03_SLOT_CARRY_RATIO,
    }

#: How many finished run windows and days are retained in the ledger.
MAX_WINDOWS = 32
MAX_DAYS = 64
#: How many recent reservation tokens are kept (audit trail, bounded).
MAX_TOKENS = 256

_KEYS = frozenset({
    "schema_version", "kind", "active_day", "days", "windows", "run_id",
    "tokens", "opened_at", "updated_at", "day_tokens", "slot_envelope_days",
})


class RequestBudgetError(RuntimeError):
    """A refusal from the request budget.  Never a model choice."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _business_day_key_module() -> Any:
    """业务日界的唯一权威：``components/world/scripts/business_day_key.py``（19 章 §一.4）。

    世界侧的脚本目录不在 Life 进程的默认导入路径上，这里沿用仓库既有的跨目录
    模式：先按名导入（测试与合设进程都把它放进过 ``sys.path``），失败就把该目录
    补进 ``sys.path`` 再导入。预算日界与世界日界从此读同一个模块的同一个答案。
    """

    try:
        import business_day_key  # noqa: F401  (世界侧 scripts 已在 sys.path 上)
        return business_day_key
    except ImportError:
        pass
    import sys

    scripts_dir = str(Path(__file__).resolve().parents[2] / "world" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import business_day_key

    return business_day_key


_bdk = _business_day_key_module()

#: 与世界侧同源的默认业务时区（固定偏移 +08:00）与解析/裁决函数。原有的模块级
#: 私有名全部保留为同一绑定的别名，调用方与测试的引用路径不变。
DEFAULT_BUSINESS_TZ = _bdk.DEFAULT_BUSINESS_TZ
DEFAULT_BUSINESS_TZ_LABEL = _bdk.DEFAULT_BUSINESS_TZ_LABEL
_parse_business_tz = _bdk.parse_business_tz
_format_tz_label = _bdk.format_tz_label
_load_policy_tz = _bdk._load_policy_tz
_resolve_default_business_tz = _bdk.resolve_default_business_tz
_business_day = _bdk.business_day


def _utc_day(moment: Optional[datetime] = None) -> str:
    """Resolve the active budget day in local business timezone (default +08:00)."""
    return _business_day(moment)


_DEFAULT_UTC_DAY_FN = _utc_day


def _iso(moment: Optional[datetime] = None) -> str:
    if moment is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_resolve_default_business_tz())
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def budget_path(state_root: Path | str) -> Path:
    return Path(state_root) / BUDGET_FILENAME


def lock_path(state_root: Path | str) -> Path:
    return Path(state_root) / LOCK_FILENAME


@contextmanager
def _exclusive(state_root: Path | str, filename: str = LOCK_FILENAME) -> Iterator[None]:
    """The cross-process lock every read-modify-write of the ledger is taken under."""

    path = Path(state_root) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass
        handle.close()


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def _write_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                     sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover
                pass
    _fsync_directory(path.parent)


def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "active_day": "",
            "days": {}, "windows": {}, "run_id": None, "tokens": [], "day_tokens": {},
            "opened_at": _iso(), "updated_at": _iso()}


FAILSTOP_FILENAME = "bounded_failstop.json"
REASON_FAILSTOP = "BOUNDED_RUN_FAILSTOP"

#: The fixed writer purposes a content request can carry.  These are the purposes the actual
#: client was constructed with; a purpose outside this closed set is recorded as ``UNKNOWN``
#: instead of being copied through.
CONTENT_WRITER_STAGES = frozenset({"artifact_edit", "reading_response", "shared_practice",
                                   "local_reply"})
FAILURE_STAGE_UNKNOWN = "UNKNOWN"
#: The provider's own closed finish-reason vocabulary.  Anything else (including a missing
#: field or a free-text sentence) is recorded as ``UNKNOWN``, never parsed.
FAILURE_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls",
                                    "function_call"})
MAX_FAILURE_CAP = 10_000_000


def _safe_failure_stage(value: Any) -> str:
    return (value if isinstance(value, str) and value in CONTENT_WRITER_STAGES
            else FAILURE_STAGE_UNKNOWN)


def _safe_failure_cap(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_FAILURE_CAP else None


def _safe_failure_finish_reason(value: Any) -> str:
    return (value if isinstance(value, str) and value in FAILURE_FINISH_REASONS
            else FAILURE_STAGE_UNKNOWN)


#: The three typed diagnostic facts the failstop marker may carry on top of its original four
#: keys.  They are re-validated here, so a caller cannot widen them by passing free text.
FAILURE_DIAGNOSTIC_KEYS = ("stage", "actual_cap", "finish_reason")


def safe_failure_diagnostics(source: Any) -> dict[str, Any]:
    """Re-type the three diagnostics from an already-derived mapping; missing facts -> UNKNOWN."""

    shape = source if isinstance(source, Mapping) else {}
    cap = _safe_failure_cap(shape.get("actual_cap"))
    return {
        "stage": _safe_failure_stage(shape.get("stage")),
        "actual_cap": cap if cap is not None else FAILURE_STAGE_UNKNOWN,
        "finish_reason": _safe_failure_finish_reason(shape.get("finish_reason")),
    }


#: Safe non-negative integer shape facts that may stay in the exception's metadata mapping.
#: Anything outside this allowlist (free text, unknown keys) is dropped by the rebuild.
_SAFE_SHAPE_INTS = ("content_chars", "reasoning_chars", "parse_error_offset",
                    "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens",
                    "max_output_tokens_sent")


def safe_failure_metadata(failure: Any, *, stage: Any = None,
                          actual_cap: Any = None) -> dict[str, Any]:
    """The three safe facts a content failure can carry: stage, actual cap, finish reason.

    ``stage`` is the fixed purpose the actual client was constructed with (or the wrapper's
    explicit purpose), never classified from a prompt or an exception string.  ``actual_cap``
    is the value the transport reports as actually sent, otherwise the ``max_output_tokens``
    argument of this very call; the client's construction default is **never** substituted, so
    a call that passed no cap stays ``UNKNOWN``.  A missing fact is ``UNKNOWN``; nothing is
    guessed from a successful request or from an exception message.
    """

    shape = getattr(failure, "metadata", None)
    shape = shape if isinstance(shape, Mapping) else {}
    cap = _safe_failure_cap(shape.get("max_output_tokens_sent"))
    if cap is None:
        cap = _safe_failure_cap(actual_cap)
    return {
        "stage": _safe_failure_stage(stage),
        "actual_cap": cap if cap is not None else FAILURE_STAGE_UNKNOWN,
        "finish_reason": _safe_failure_finish_reason(shape.get("finish_reason")),
    }


def attach_safe_failure_metadata(failure: Any, *, stage: Any = None,
                                 actual_cap: Any = None) -> dict[str, Any]:
    """Rebuild ``failure.metadata`` from safe facts in place; return the derived facts.

    This stays inside the *existing* ``AgencyModelClientError.metadata`` channel -- no new
    file, marker key or sink.  Only the allowlisted integer counts and the three derived
    closed/typed facts survive; the exception message and any free text are dropped.
    """

    facts = safe_failure_metadata(failure, stage=stage, actual_cap=actual_cap)
    raw = getattr(failure, "metadata", None)
    if isinstance(raw, dict):
        clean: dict[str, Any] = {}
        for name in _SAFE_SHAPE_INTS:
            value = _safe_failure_cap(raw.get(name))
            if value is not None:
                clean[name] = value
        clean.update(facts)
        raw.clear()
        raw.update(clean)
    return facts


def failure_category(value: Any) -> str:
    """闭集分型，不保存异常正文；截断、晚到不是传输失败。"""
    raw = value if isinstance(value, str) else getattr(value, "failure_class", "")
    token = str(raw or "").removeprefix("DECISION_ATTEMPT_FAILED:")
    if token in {"OUTPUT_TRUNCATED", "PROVIDER_OUTPUT_TRUNCATED"}:
        return "OUTPUT_TRUNCATED"
    if token == "LATE_RESULT_DISCARDED":
        return "LATE_RESULT_DISCARDED"
    if token in {"STEP_REFUSED", "WORLD_ACTION_REJECTED", "OWNER_REJECTED"}:
        # 0.7: the owner definitively refused the round's one World step (no unknown
        # side effect).  It is a closed category of its own, carried by fixed code only.
        return "STEP_REFUSED"
    if token in {"MODEL_TIMEOUT", "MODEL_CONNECTION", "TRANSPORT_TIMEOUT",
                 "TRANSPORT_CONNECTION", "MODEL_RATE_LIMITED", "MODEL_PROVIDER_ERROR",
                 "MODEL_AUTH_ERROR", "MODEL_UNAVAILABLE", "MODEL_REQUEST_REJECTED",
                 "PROVIDER_RATE_LIMITED", "PROVIDER_SERVER_ERROR", "PROVIDER_AUTH_ERROR",
                 "PROVIDER_UNAVAILABLE", "PROVIDER_REQUEST_REJECTED"}:
        return "PROVIDER_OR_TRANSPORT_FAILURE"
    if isinstance(value, (TimeoutError, ConnectionError, OSError)):
        return "PROVIDER_OR_TRANSPORT_FAILURE"
    return "STRUCTURE_OR_OTHER_FAILURE"


class LifeRequestBudget:
    """The hard, persisted ceiling on model requests.

    ``utc_day_max`` is the per-UTC-day ceiling and ``run_window_max`` the ceiling for
    one *run window* (one 24h run identity, which survives restarts).  Either may be
    ``None``, which means "no ceiling of that kind" -- never "unlimited": the other
    one still applies, and a budget with both ``None`` is refused by the caller, not
    silently honoured here.  ``daily_token_budget`` is the third, parallel ceiling (the
    per-business-day ``est_tokens`` sum); ``None`` means "not configured" and keeps the
    historical call-only accounting byte-for-byte.
    """

    def __init__(self, state_root: Path | str, *, utc_day_max: Optional[int] = None,
                 run_window_max: Optional[int] = None, run_id: Optional[str] = None,
                 failstop: bool = False, request_deadline: Optional[str] = None,
                 daily_token_budget: Optional[int] = None,
                 business_tz: Optional[tzinfo | str | int | float] = None) -> None:
        self.state_root = Path(state_root)
        self.utc_day_max = None if utc_day_max is None else max(0, int(utc_day_max))
        self.run_window_max = None if run_window_max is None else max(0, int(run_window_max))
        self.daily_token_budget = None if daily_token_budget is None \
            else max(0, int(daily_token_budget))
        self.run_id = str(run_id).strip() if run_id else None
        self._thread_lock = threading.RLock()
        self.failstop = bool(failstop)
        self.request_deadline = request_deadline
        self._failed_in_memory = False
        self._explicit_business_tz = _parse_business_tz(business_tz)
        if business_tz is not None and self._explicit_business_tz is None:
            raise ValueError(f"invalid business_tz={business_tz!r}")

    @property
    def business_tz(self) -> tzinfo:
        if self._explicit_business_tz is not None:
            return self._explicit_business_tz
        return _resolve_default_business_tz(self.state_root)

    @property
    def business_tz_label(self) -> str:
        return _format_tz_label(self.business_tz)

    def _resolve_day(self, moment: Optional[datetime] = None) -> str:
        if _utc_day is not _DEFAULT_UTC_DAY_FN:
            if moment is not None:
                try:
                    return _utc_day(moment)
                except TypeError:
                    return _utc_day()
            return _utc_day()
        return _business_day(moment, tz=self.business_tz)

    def check_failstop(self) -> None:
        if not self.failstop:
            return
        if self._failed_in_memory or (self.state_root / FAILSTOP_FILENAME).exists():
            raise RequestBudgetError(REASON_FAILSTOP)
        if self.request_deadline:
            try:
                deadline = datetime.fromisoformat(self.request_deadline)
                expired = deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline
            except (TypeError, ValueError):
                expired = True
            if expired:
                self.note_failure("DEADLINE_REACHED")
                raise RequestBudgetError(REASON_FAILSTOP)

    def note_failure(self, failure: Any, *, diagnostics: Any = None) -> None:
        if not self.failstop:
            return
        self._failed_in_memory = True
        # The persisted marker keeps its original four keys.  A content failure may carry the
        # three approved typed facts on top (stage / actual_cap / finish_reason), rebuilt from
        # the safe allowlist only; non-model stops (deadline, budget persistence, schema retry)
        # pass no diagnostics and keep the four-key shape unchanged.
        payload: dict[str, Any] = {
            "run_id": self.run_id, "reason_code": REASON_FAILSTOP,
            "failure_category": ("DEADLINE_REACHED" if failure == "DEADLINE_REACHED"
                                 else failure_category(failure)),
            "at": _iso(),
        }
        if diagnostics is not None:
            payload.update(safe_failure_diagnostics(diagnostics))
        # 先独占创建不可逆 marker，再填安全元数据；写一半也永久拒绝。
        path = self.state_root / FAILSTOP_FILENAME
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(path.parent)
        except FileExistsError:
            pass
        except OSError as exc:
            # 无法持久化停止位时绝不回到上层继续循环；进程硬退出保留原预留。
            raise SystemExit("BOUNDED_FAILSTOP_PERSIST_FAILED") from exc

    @contextmanager
    def attempt_guard(self) -> Iterator[None]:
        if not self.failstop:
            yield
            return
        # 两个模型出口串行到失败返回落锁；并发调用不能穿过下一次 reserve。
        with self._thread_lock, _exclusive(self.state_root, "bounded_attempt.lock"):
            self.check_failstop()
            yield

    # -- paths ---------------------------------------------------------------

    @property
    def path(self) -> Path:
        return budget_path(self.state_root)

    # -- raw io --------------------------------------------------------------

    def _load_locked(self) -> dict[str, Any]:
        """Read the ledger.  A corrupt or unreadable one raises: fail closed."""

        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty()
        except OSError as exc:
            raise RequestBudgetError(REASON_UNREADABLE, str(exc)) from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RequestBudgetError(REASON_STATE_CORRUPT, str(exc)) from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION \
                or data.get("kind") != KIND:
            raise RequestBudgetError(REASON_STATE_CORRUPT, "unexpected schema")
        if not isinstance(data.get("days"), dict) or not isinstance(data.get("windows"), dict):
            raise RequestBudgetError(REASON_STATE_CORRUPT, "days/windows are not objects")
        if not isinstance(data.get("active_day"), str):
            raise RequestBudgetError(REASON_STATE_CORRUPT, "active_day is not a string")
        if "day_tokens" in data and not isinstance(data.get("day_tokens"), dict):
            raise RequestBudgetError(REASON_STATE_CORRUPT, "day_tokens is not an object")
        # 可选字段：旧账本没有它是完全合法的；只有存在但类型错误才判损坏。
        if "slot_envelope_days" in data and not isinstance(data.get("slot_envelope_days"), dict):
            raise RequestBudgetError(REASON_STATE_CORRUPT, "slot_envelope_days is not an object")
        if "v03_four_slot_days" in data and not isinstance(data.get("v03_four_slot_days"), dict):
            raise RequestBudgetError(REASON_STATE_CORRUPT, "v03_four_slot_days is not an object")
        return data

    def _save_locked(self, data: dict[str, Any]) -> None:
        days = dict(data.get("days") or {})
        if len(days) > MAX_DAYS:
            keep = sorted(days)[-MAX_DAYS:]
            data["days"] = {key: days[key] for key in keep}
        day_tokens = dict(data.get("day_tokens") or {})
        if day_tokens:
            keep_tokens = sorted(day_tokens)[-MAX_DAYS:]
            data["day_tokens"] = {key: day_tokens[key] for key in keep_tokens}
        windows = dict(data.get("windows") or {})
        if len(windows) > MAX_WINDOWS:
            ordered = sorted(windows, key=lambda key: str(
                (windows[key] or {}).get("opened_at") or ""))
            data["windows"] = {key: windows[key] for key in ordered[-MAX_WINDOWS:]}
        tokens = list(data.get("tokens") or [])[-MAX_TOKENS:]
        data["tokens"] = tokens
        slot_days = data.get("slot_envelope_days")
        if isinstance(slot_days, dict) and len(slot_days) > MAX_DAYS:
            keep_slot_days = sorted(slot_days)[-MAX_DAYS:]
            data["slot_envelope_days"] = {
                key: slot_days[key] for key in keep_slot_days
            }
        four_days = data.get("v03_four_slot_days")
        if isinstance(four_days, dict) and len(four_days) > MAX_DAYS:
            keep_four_days = sorted(four_days)[-MAX_DAYS:]
            data["v03_four_slot_days"] = {
                key: four_days[key] for key in keep_four_days
            }
        data["updated_at"] = _iso()
        try:
            _write_atomically(self.path, data)
        except OSError as exc:
            if not self.failstop:
                raise
            self.note_failure("BUDGET_PERSIST_FAILED")
            raise RequestBudgetError(REASON_UNREADABLE) from exc

    # -- view ----------------------------------------------------------------

    def view(self, *, now: Optional[datetime] = None) -> dict[str, Any]:
        """Read-only view.  Never raises for a corrupt ledger: it reports ``corrupt``."""

        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            day = self._resolve_day(now)
            return {"state": STATE_CORRUPT, "reason_code": exc.reason_code,
                    "detail": exc.detail, "path": str(self.path),
                    "utc_day": day, "business_day": day,
                    "business_tz": self.business_tz_label,
                    "utc_day_calls": None,
                    "utc_day_tokens": None,
                    "run_id": self.run_id, "run_window_calls": None,
                    "utc_day_max": self.utc_day_max, "run_window_max": self.run_window_max,
                    "daily_token_budget": self.daily_token_budget}
        active = self._active_day(data, moment=now)
        return {
            "state": STATE_OK if data.get("days") or data.get("windows") else STATE_ABSENT,
            "path": str(self.path),
            "utc_day": active,
            "business_day": active,
            "business_tz": self.business_tz_label,
            "utc_day_calls": int((data.get("days") or {}).get(active) or 0),
            "utc_day_max": self.utc_day_max,
            "utc_day_tokens": int((data.get("day_tokens") or {}).get(active) or 0),
            "daily_token_budget": self.daily_token_budget,
            "day_tokens": {key: int(value) for key, value in sorted((data.get("day_tokens") or {}).items())},
            "history": {key: int(value) for key, value in sorted((data.get("days") or {}).items())},
            "run_id": self.run_id,
            "run_window_calls": self._window_calls(data, self.run_id),
            "run_window_max": self.run_window_max,
            "windows": {key: int((value or {}).get("calls") or 0)
                        for key, value in (data.get("windows") or {}).items()},
            "recent_tokens": list(data.get("tokens") or [])[-8:],
            "refund_policy": "no release API exists: a timeout, a crash or a retry keeps its reservation",
        }

    def _active_day(self, data: Mapping[str, Any], *, moment: Optional[datetime] = None) -> str:
        """The day the counters live in.  Monotonic: a rollback never resets them."""

        stored = str(data.get("active_day") or "")
        today = self._resolve_day(moment)
        return max(stored, today) if stored else today

    def _window_calls(self, data: Mapping[str, Any], run_id: Optional[str]) -> Optional[int]:
        if not run_id:
            return None
        window = (data.get("windows") or {}).get(run_id)
        return int((window or {}).get("calls") or 0) if window is not None else None

    # -- the window ----------------------------------------------------------

    def ensure_window(self, *, allow_new: bool = False,
                      now: Optional[datetime] = None) -> dict[str, Any]:
        """Continue this run identity's window, or explicitly open a new one.

        A restart that carries the same ``run_id`` continues the same allowance.  A
        caller that asks for a window under a *new* identity has to say so
        (``allow_new=True``); otherwise the call is refused, which is what stops a
        restart loop from minting a fresh 24-hour budget each time.
        """

        if not self.run_id:
            raise RequestBudgetError(REASON_RUN_WINDOW_NOT_AUTHORIZED,
                                     "no run identity was configured")
        with self._thread_lock, _exclusive(self.state_root):
            self.check_failstop()
            data = self._load_locked()
            windows = dict(data.get("windows") or {})
            if self.run_id in windows:
                return {"state": "existing", "run_id": self.run_id,
                        "calls": int((windows[self.run_id] or {}).get("calls") or 0)}
            if windows and not allow_new:
                raise RequestBudgetError(
                    REASON_RUN_WINDOW_NOT_AUTHORIZED,
                    "run window %r does not exist and this caller may not open one"
                    % self.run_id)
            windows[self.run_id] = {"calls": 0, "opened_at": _iso(now), "closed_at": None}
            data["windows"] = windows
            data["run_id"] = self.run_id
            data["active_day"] = self._active_day(data, moment=now)
            self._save_locked(data)
            return {"state": "opened", "run_id": self.run_id, "calls": 0}

    # -- the reservation -----------------------------------------------------

    def check(
        self,
        *,
        est_tokens: int = 0,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Is one more request allowed right now?  Read-only, and fail closed."""

        if isinstance(est_tokens, bool) or not isinstance(est_tokens, int) or est_tokens < 0:
            raise ValueError("est_tokens must be a non-negative int, got %r" % (est_tokens,))
        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            return {"allowed": False, "reason_code": exc.reason_code, "detail": exc.detail}
        return self._verdict(data, moment=now, est_tokens=int(est_tokens))

    def _verdict(
        self,
        data: Mapping[str, Any],
        *,
        moment: Optional[datetime] = None,
        est_tokens: int = 0,
    ) -> dict[str, Any]:
        day = self._active_day(data, moment=moment)
        day_used = int((data.get("days") or {}).get(day) or 0)
        window_used = self._window_calls(data, self.run_id)
        day_tokens_used = int((data.get("day_tokens") or {}).get(day) or 0)
        base = {"utc_day": day, "business_day": day,
                "business_tz": self.business_tz_label,
                "utc_day_calls": day_used,
                "utc_day_tokens": day_tokens_used,
                "run_window_calls": window_used}
        if self.utc_day_max is not None and day_used >= self.utc_day_max:
            return dict(base, allowed=False, reason_code=REASON_UTC_DAY_LIMIT,
                        detail="business day %s (%s) spent %s of %s"
                               % (day, self.business_tz_label, day_used, self.utc_day_max))
        if self.run_window_max is not None and window_used is not None \
                and window_used >= self.run_window_max:
            return dict(base, allowed=False, reason_code=REASON_RUN_WINDOW_LIMIT,
                        detail="run window %s spent %s of %s"
                               % (self.run_id, window_used, self.run_window_max))
        if self.daily_token_budget is not None:
            if day_tokens_used >= self.daily_token_budget:
                return dict(
                    base,
                    allowed=False,
                    reason_code=REASON_DAILY_TOKEN_BUDGET,
                    detail="business day %s (%s) reserved %s of %s est tokens"
                    % (day, self.business_tz_label, day_tokens_used, self.daily_token_budget),
                )
            if int(est_tokens) > 0 and (day_tokens_used + int(est_tokens)) > self.daily_token_budget:
                return dict(
                    base,
                    allowed=False,
                    reason_code=REASON_DAILY_TOKEN_BUDGET,
                    detail="business day %s (%s) reserved %s+%s of %s est tokens"
                    % (
                        day,
                        self.business_tz_label,
                        day_tokens_used,
                        int(est_tokens),
                        self.daily_token_budget,
                    ),
                )
        return dict(base, allowed=True, reason_code="")

    def _local_hour(self, moment: Optional[datetime] = None) -> int:
        """The hour of ``moment`` in this budget's business timezone."""

        if moment is None:
            moment = datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.business_tz)
        return int(moment.astimezone(self.business_tz).hour)

    def _four_slot_bucket(self, data: Mapping[str, Any], day: str) -> Optional[dict[str, Any]]:
        days = data.get("v03_four_slot_days")
        bucket = days.get(day) if isinstance(days, Mapping) else None
        return bucket if isinstance(bucket, dict) else None

    def _four_slot_verdict(
        self,
        day_bucket: Any,
        *,
        slot: str,
        hour: int,
        est_tokens: int,
        day: str,
    ) -> dict[str, Any]:
        projection = v03_four_slot_effective(day_bucket, hour)
        slots = projection["slots"]
        row = slots[slot]
        base = {
            "business_day": day,
            "business_tz": self.business_tz_label,
            "slot_name": slot,
            "local_hour": hour,
            "slot_calls": dict(row["calls"]),
            "slot_tokens": dict(row["tokens"]),
            "total_calls_used": projection["total_calls_used"],
            "total_tokens_used": projection["total_tokens_used"],
            "daily_call_cap": projection["daily_call_cap"],
            "daily_token_cap": projection["daily_token_cap"],
        }
        calls = row["calls"]
        tokens = row["tokens"]
        if calls["used"] >= calls["effective"]:
            return dict(base, allowed=False, reason_code=REASON_V03_SLOT_QUOTA_EXCEEDED,
                        detail="slot %s on %s (%s) spent %s of %s calls"
                               % (slot, day, self.business_tz_label,
                                  calls["used"], calls["effective"]))
        if int(tokens["used"]) + int(est_tokens) > int(tokens["effective"]):
            return dict(base, allowed=False, reason_code=REASON_V03_SLOT_QUOTA_EXCEEDED,
                        detail="slot %s on %s (%s) reserved %s+%s of %s est tokens"
                               % (slot, day, self.business_tz_label,
                                  tokens["used"], est_tokens, tokens["effective"]))
        if int(projection["total_calls_used"]) >= int(projection["daily_call_cap"]):
            return dict(base, allowed=False, reason_code=REASON_V03_DAILY_CAP_EXCEEDED,
                        detail="business day %s (%s) spent %s of %s daily calls"
                               % (day, self.business_tz_label,
                                  projection["total_calls_used"], projection["daily_call_cap"]))
        if int(projection["total_tokens_used"]) + int(est_tokens) \
                > int(projection["daily_token_cap"]):
            return dict(base, allowed=False, reason_code=REASON_V03_DAILY_CAP_EXCEEDED,
                        detail="business day %s (%s) reserved %s+%s of %s est tokens"
                               % (day, self.business_tz_label,
                                  projection["total_tokens_used"], est_tokens,
                                  projection["daily_token_cap"]))
        return dict(base, allowed=True, reason_code="")

    def check_v03_four_slot(
        self,
        *,
        slot_name: Optional[str] = None,
        est_tokens: int = 0,
        now: Optional[datetime] = None,
        now_iso: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read-only four-slot verdict (fail closed); the default call path never uses it."""

        moment = self._coerce_moment(now, now_iso)
        hour = self._local_hour(moment)
        slot = str(slot_name).strip() if slot_name else v03_slot_for_hour(hour)
        if slot not in V03_FOUR_SLOT_QUOTAS:
            return {"allowed": False, "reason_code": REASON_UNKNOWN_V03_SLOT,
                    "detail": "unknown v03 slot %r" % (slot,), "slot_name": slot}
        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            return {"allowed": False, "reason_code": exc.reason_code,
                    "detail": exc.detail, "slot_name": slot}
        day = self._active_day(data, moment=moment)
        return self._four_slot_verdict(
            self._four_slot_bucket(data, day),
            slot=slot,
            hour=hour,
            est_tokens=int(est_tokens or 0),
            day=day,
        )

    def four_slot_snapshot(
        self,
        *,
        now: Optional[datetime] = None,
        now_iso: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read-only snapshot of one business day's four-slot usage (never raises)."""

        moment = self._coerce_moment(now, now_iso)
        hour = self._local_hour(moment)
        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            day = self._resolve_day(moment)
            projection = v03_four_slot_effective(None, hour)
            return dict(projection, state=STATE_CORRUPT, reason_code=exc.reason_code,
                        detail=exc.detail, business_day=day,
                        business_tz=self.business_tz_label)
        day = self._active_day(data, moment=moment)
        bucket = self._four_slot_bucket(data, day)
        projection = v03_four_slot_effective(bucket, hour)
        return dict(
            projection,
            state=STATE_OK if bucket else STATE_ABSENT,
            business_day=day,
            business_tz=self.business_tz_label,
            slot_quotas={k: dict(v) for k, v in V03_FOUR_SLOT_QUOTAS.items()},
        )

    @staticmethod
    def _coerce_moment(now: Optional[datetime],
                       now_iso: Optional[str]) -> Optional[datetime]:
        if now is not None:
            return now
        if isinstance(now_iso, datetime):
            return now_iso
        if isinstance(now_iso, str) and now_iso.strip():
            text = now_iso.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None
        return None

    def reserve(self, kind: str = REQUEST_KIND_DECISION, *,
                est_tokens: Optional[int] = None,
                now: Optional[datetime] = None,
                slot_mode: Optional[str] = None,
                slot_name: Optional[str] = None) -> dict[str, Any]:
        """Persist one attempt's reservation **before** the caller sends anything.

        ``est_tokens`` is the reservation's estimated token cost -- the wake ticket's own
        ``est_tokens`` when one exists (07 章 §二).  ``None`` charges the conservative
        constant ``DEFAULT_CONSERVATIVE_TOKEN_RESERVATION``.  A given estimate must be a
        non-negative int (booleans refused).  Raises :class:`RequestBudgetError` when any
        ceiling is reached or the ledger is corrupt.  There is no path that gives a
        reservation back -- the estimate keeps its reservation like the call does.

        ``slot_mode="v03_four_slot"`` is the opt-in 07 章 §四 four-slot + carry ceiling.  It
        adds its own per-slot and per-day caps on top of the historical ceilings and writes
        only the optional ``v03_four_slot_days`` sub-key; every other call path (including
        the three-slot ``SLOT_QUOTAS``) is byte-for-byte unchanged.
        """

        if kind not in REQUEST_KINDS:
            raise ValueError("unknown request kind %r" % (kind,))
        if slot_mode not in (None, V03_SLOT_MODE):
            raise ValueError("unknown slot_mode %r" % (slot_mode,))
        if est_tokens is None:
            est = DEFAULT_CONSERVATIVE_TOKEN_RESERVATION
        else:
            if isinstance(est_tokens, bool) or not isinstance(est_tokens, int) or est_tokens < 0:
                raise ValueError("est_tokens must be a non-negative int, got %r" % (est_tokens,))
            est = int(est_tokens)
        four_mode = slot_mode == V03_SLOT_MODE
        with self._thread_lock, _exclusive(self.state_root):
            self.check_failstop()
            data = self._load_locked()
            verdict = self._verdict(data, moment=now, est_tokens=est)
            if not verdict["allowed"]:
                raise RequestBudgetError(verdict["reason_code"], verdict["detail"])
            day = self._active_day(data, moment=now)
            four_slot = None
            if four_mode:
                hour = self._local_hour(now)
                four_slot = str(slot_name).strip() if slot_name else v03_slot_for_hour(hour)
                if four_slot not in V03_FOUR_SLOT_QUOTAS:
                    raise RequestBudgetError(
                        REASON_UNKNOWN_V03_SLOT, "unknown v03 slot %r" % (four_slot,))
                gate = self._four_slot_verdict(
                    self._four_slot_bucket(data, day),
                    slot=four_slot,
                    hour=hour,
                    est_tokens=est,
                    day=day,
                )
                if not gate["allowed"]:
                    raise RequestBudgetError(gate["reason_code"], gate["detail"])
            days = dict(data.get("days") or {})
            days[day] = int(days.get(day) or 0) + 1
            day_tokens = dict(data.get("day_tokens") or {})
            day_tokens[day] = int(day_tokens.get(day) or 0) + est
            windows = dict(data.get("windows") or {})
            if self.run_id:
                window = dict(windows.get(self.run_id) or {"calls": 0, "opened_at": _iso(now),
                                                           "closed_at": None})
                window["calls"] = int(window.get("calls") or 0) + 1
                windows[self.run_id] = window
            if four_mode:
                four_days = data.get("v03_four_slot_days")
                if not isinstance(four_days, dict):
                    four_days = {}
                    data["v03_four_slot_days"] = four_days
                bucket = four_days.get(day)
                if not isinstance(bucket, dict):
                    bucket = {"slots": {}, "total_calls": 0, "total_tokens": 0, "ops": {}}
                    four_days[day] = bucket
                for key in ("slots", "ops"):
                    if not isinstance(bucket.get(key), dict):
                        bucket[key] = {}
                slot_row = bucket["slots"].get(four_slot)
                if not isinstance(slot_row, dict):
                    slot_row = {"calls": 0, "tokens": 0}
                slot_row["calls"] = int(slot_row.get("calls") or 0) + 1
                slot_row["tokens"] = int(slot_row.get("tokens") or 0) + est
                bucket["slots"][four_slot] = slot_row
                bucket["total_calls"] = int(bucket.get("total_calls") or 0) + 1
                bucket["total_tokens"] = int(bucket.get("total_tokens") or 0) + est
            token = {"token": "lreq:%s" % uuid.uuid4().hex[:16], "kind": kind,
                     "utc_day": day, "business_day": day,
                     "business_tz": self.business_tz_label,
                     "run_id": self.run_id, "at": _iso(now),
                     "est_tokens": est}
            data["days"] = days
            data["day_tokens"] = day_tokens
            data["windows"] = windows
            data["active_day"] = day
            data["run_id"] = self.run_id
            data["tokens"] = list(data.get("tokens") or []) + [token]
            if not data.get("opened_at"):
                data["opened_at"] = _iso(now)
            self._save_locked(data)
            result = {"reservation": token, "utc_day": day,
                      "business_day": day, "business_tz": self.business_tz_label,
                      "utc_day_calls": days[day],
                      "utc_day_tokens": day_tokens[day],
                      "run_window_calls": self._window_calls(data, self.run_id)}
            if four_mode:
                projection = v03_four_slot_effective(
                    self._four_slot_bucket(data, day), self._local_hour(now))
                result["slot_mode"] = V03_SLOT_MODE
                result["slot_name"] = four_slot
                result["v03_four_slot"] = projection
            return result


    # -- 07 章 §四 slot quota & daily envelope --------------------------------

    @staticmethod
    def _empty_slot_bucket() -> dict[str, Any]:
        return {"slots": {}, "categories": {}, "total": 0, "ops": {}}

    @staticmethod
    def _read_slot_usage(data: Mapping[str, Any], day: str) -> dict[str, Any]:
        days = data.get("slot_envelope_days")
        bucket = days.get(day) if isinstance(days, Mapping) else None
        if not isinstance(bucket, Mapping):
            return {"slots": {}, "categories": {}, "total": 0, "ops": {}}
        slots = bucket.get("slots") if isinstance(bucket.get("slots"), Mapping) else {}
        categories = bucket.get("categories") if isinstance(bucket.get("categories"), Mapping) else {}
        ops = bucket.get("ops") if isinstance(bucket.get("ops"), Mapping) else {}

        def _safe_count(raw: Any) -> int:
            if isinstance(raw, bool):
                return 0
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 0

        return {
            "slots": {str(k): _safe_count(v) for k, v in slots.items()},
            "categories": {str(k): _safe_count(v) for k, v in categories.items()},
            "total": _safe_count(bucket.get("total")),
            "ops": dict(ops),
        }

    @staticmethod
    def _slot_verdict(
        slot_kind: str,
        category: str,
        day: str,
        usage: Mapping[str, Any],
        *,
        business_tz_label: str,
    ) -> dict[str, Any]:
        slot_quota = int(SLOT_QUOTAS[slot_kind])
        category_cap = int(DAILY_ENVELOPE_CAPS["per_category_cap"][category])
        daily_hard_cap = int(DAILY_ENVELOPE_CAPS["daily_hard_cap"])
        slot_used = int((usage.get("slots") or {}).get(slot_kind) or 0)
        category_used = int((usage.get("categories") or {}).get(category) or 0)
        day_total = int(usage.get("total") or 0)
        base = {
            "utc_day": day,
            "business_day": day,
            "business_tz": business_tz_label,
            "slot_kind": slot_kind,
            "slot_used": slot_used,
            "slot_quota": slot_quota,
            "slot_remaining": max(0, slot_quota - slot_used),
            "category": category,
            "category_used": category_used,
            "category_cap": category_cap,
            "category_remaining": max(0, category_cap - category_used),
            "day_total": day_total,
            "daily_hard_cap": daily_hard_cap,
            "daily_remaining": max(0, daily_hard_cap - day_total),
        }
        if slot_used >= slot_quota:
            return dict(
                base,
                allowed=False,
                reason_code=REASON_SLOT_QUOTA_EXCEEDED,
                detail="slot %s on %s (%s) spent %s of %s"
                       % (slot_kind, day, business_tz_label, slot_used, slot_quota),
            )
        if category_used >= category_cap:
            return dict(
                base,
                allowed=False,
                reason_code=REASON_DAILY_ENVELOPE_EXCEEDED,
                detail="category %s on %s (%s) spent %s of %s"
                       % (category, day, business_tz_label, category_used, category_cap),
            )
        if day_total >= daily_hard_cap:
            return dict(
                base,
                allowed=False,
                reason_code=REASON_DAILY_ENVELOPE_EXCEEDED,
                detail="business day %s (%s) spent %s of %s daily envelope"
                       % (day, business_tz_label, day_total, daily_hard_cap),
            )
        return dict(base, allowed=True, reason_code="", detail="")

    def check_slot_and_daily_envelope(
        self,
        *,
        slot_kind: str,
        category: str,
        now_dt: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Read-only joint SlotQuota + DailyEnvelope check (fail closed)."""

        slot = str(slot_kind or "").strip()
        cat = str(category or "").strip()
        if slot not in SLOT_QUOTAS or cat not in DAILY_ENVELOPE_CAPS["per_category_cap"]:
            return {
                "allowed": False,
                "reason_code": REASON_UNKNOWN_SLOT_OR_CATEGORY,
                "detail": "unknown slot_kind=%r or category=%r" % (slot, cat),
                "slot_kind": slot,
                "category": cat,
            }
        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            return {
                "allowed": False,
                "reason_code": exc.reason_code,
                "detail": exc.detail,
                "slot_kind": slot,
                "category": cat,
            }
        day = self._active_day(data, moment=now_dt)
        usage = self._read_slot_usage(data, day)
        return self._slot_verdict(
            slot, cat, day, usage, business_tz_label=self.business_tz_label
        )

    def record_slot_and_daily_consumption(
        self,
        *,
        slot_kind: str,
        category: str,
        now_dt: Optional[datetime] = None,
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist one envelope consumption after both caps admit it (never refunds)."""

        slot = str(slot_kind or "").strip()
        cat = str(category or "").strip()
        if slot not in SLOT_QUOTAS or cat not in DAILY_ENVELOPE_CAPS["per_category_cap"]:
            raise RequestBudgetError(
                REASON_UNKNOWN_SLOT_OR_CATEGORY,
                "unknown slot_kind=%r or category=%r" % (slot, cat),
            )
        op_key = str(operation_key).strip() if operation_key else None
        with self._thread_lock, _exclusive(self.state_root):
            self.check_failstop()
            data = self._load_locked()
            day = self._active_day(data, moment=now_dt)
            days = data.get("slot_envelope_days")
            if not isinstance(days, dict):
                days = {}
                data["slot_envelope_days"] = days
            bucket = days.get(day)
            if not isinstance(bucket, dict):
                bucket = {"slots": {}, "categories": {}, "total": 0, "ops": {}}
                days[day] = bucket
            for key in ("slots", "categories", "ops"):
                if not isinstance(bucket.get(key), dict):
                    bucket[key] = {}

            usage = self._read_slot_usage(data, day)
            if op_key and op_key in usage["ops"]:
                cached = dict(usage["ops"][op_key])
                cached["already_applied"] = True
                return cached

            verdict = self._slot_verdict(
                slot, cat, day, usage, business_tz_label=self.business_tz_label
            )
            if not verdict["allowed"]:
                raise RequestBudgetError(verdict["reason_code"], verdict["detail"])

            bucket["slots"][slot] = int(verdict["slot_used"]) + 1
            bucket["categories"][cat] = int(verdict["category_used"]) + 1
            bucket["total"] = int(verdict["day_total"]) + 1
            result = dict(verdict)
            result["slot_used"] = bucket["slots"][slot]
            result["slot_remaining"] = max(0, verdict["slot_quota"] - bucket["slots"][slot])
            result["category_used"] = bucket["categories"][cat]
            result["category_remaining"] = max(
                0, verdict["category_cap"] - bucket["categories"][cat]
            )
            result["day_total"] = bucket["total"]
            result["daily_remaining"] = max(
                0, verdict["daily_hard_cap"] - bucket["total"]
            )
            result["already_applied"] = False
            result["operation_key"] = op_key

            if op_key:
                ops = bucket["ops"]
                ops[op_key] = dict(result)
                if len(ops) > MAX_SLOT_ENVELOPE_OPS:
                    for stale in list(ops.keys())[: len(ops) - MAX_SLOT_ENVELOPE_OPS]:
                        ops.pop(stale, None)

            data["active_day"] = day
            self._save_locked(data)
            return result

    def slot_and_daily_snapshot(
        self,
        *,
        now_dt: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Read-only snapshot of today's slot/category usage (never raises on corruption)."""

        try:
            data = self._load_locked()
        except RequestBudgetError as exc:
            day = self._resolve_day(now_dt)
            return {
                "state": STATE_CORRUPT,
                "reason_code": exc.reason_code,
                "detail": exc.detail,
                "business_day": day,
                "business_tz": self.business_tz_label,
                "slots": {},
                "categories": {},
                "day_total": 0,
                "daily_hard_cap": int(DAILY_ENVELOPE_CAPS["daily_hard_cap"]),
                "slot_quotas": dict(SLOT_QUOTAS),
                "per_category_cap": dict(DAILY_ENVELOPE_CAPS["per_category_cap"]),
            }
        day = self._active_day(data, moment=now_dt)
        usage = self._read_slot_usage(data, day)
        days = data.get("slot_envelope_days")
        present = isinstance(days, Mapping) and isinstance(days.get(day), Mapping)
        slots = {
            kind: {
                "used": int(usage["slots"].get(kind) or 0),
                "quota": int(quota),
                "remaining": max(0, int(quota) - int(usage["slots"].get(kind) or 0)),
            }
            for kind, quota in SLOT_QUOTAS.items()
        }
        categories = {
            name: {
                "used": int(usage["categories"].get(name) or 0),
                "cap": int(cap),
                "remaining": max(0, int(cap) - int(usage["categories"].get(name) or 0)),
            }
            for name, cap in DAILY_ENVELOPE_CAPS["per_category_cap"].items()
        }
        return {
            "state": STATE_OK if present else STATE_ABSENT,
            "business_day": day,
            "business_tz": self.business_tz_label,
            "slots": slots,
            "categories": categories,
            "day_total": int(usage["total"]),
            "daily_hard_cap": int(DAILY_ENVELOPE_CAPS["daily_hard_cap"]),
            "daily_remaining": max(
                0, int(DAILY_ENVELOPE_CAPS["daily_hard_cap"]) - int(usage["total"])
            ),
            "slot_quotas": dict(SLOT_QUOTAS),
            "per_category_cap": dict(DAILY_ENVELOPE_CAPS["per_category_cap"]),
        }

    def can_spend(
        self,
        *,
        now: Optional[datetime] = None,
        slot_kind: Optional[str] = None,
        category: Optional[str] = None,
        enforce_slot_envelope: bool = False,
    ) -> dict[str, Any]:
        """The existing ``check`` verdict, optionally joined with SlotQuota + DailyEnvelope.

        Without ``enforce_slot_envelope=True`` this is exactly ``check(now=now)`` -- the
        historical call path is byte-for-byte unchanged.
        """

        verdict = self.check(now=now)
        if not enforce_slot_envelope:
            return verdict
        if not verdict.get("allowed"):
            return verdict
        gate = self.check_slot_and_daily_envelope(
            slot_kind=str(slot_kind or ""),
            category=str(category or ""),
            now_dt=now,
        )
        merged = dict(verdict)
        merged["slot_envelope_checked"] = True
        merged["slot_and_daily"] = gate
        if not gate.get("allowed"):
            merged["allowed"] = False
            merged["reason_code"] = gate.get("reason_code", REASON_UNKNOWN_SLOT_OR_CATEGORY)
            merged["detail"] = gate.get("detail", "")
        return merged


# ---------------------------------------------------------------------------------
# the two wrappers: the reserve happens at the HTTP boundary, not after it
# ---------------------------------------------------------------------------------

def require_coordinated_send(inner: Any, admission: Any, kind: str, attempt: int) -> None:
    """W05R-R1c: the pause may only be promised if the *actual* send can be coordinated.

    The reservation is already durable and is never refunded.  What this decides is whether
    the request may really start: the send phase has to happen where the transport writes the
    bytes out, so the inner must be able to separate that finite phase from the response wait
    (``send_split_capable``).  Then the transport itself holds the pause confirmation lock
    across the write-out and releases it before waiting for the answer.  An opaque transport
    gives no such point, so the request is refused **before anything is sent** instead of
    being sent on the strength of an earlier check.
    """

    if admission is None:
        return
    if not getattr(inner, "send_split_capable", False):
        raise life_pause.RequestNotSentError(
            detail="%s cannot separate the send phase from the response wait"
                   % type(inner).__name__)
    if callable(admission):
        # An extra handshake the caller owns (the in-process doubles and the cycle's own hooks
        # use it).  It is not the permit to send: that is the lock the transport holds across
        # the write-out itself.
        admission(kind, attempt)



class BudgetedCognitionAdapter:
    """Wrap the AG-1 cognition handler so every attempt is reserved first.

    ``schema_retry_index == 0`` is a decision attempt; anything above it is a retry.
    Both are HTTP attempts and both are counted against both ceilings.
    """

    def __init__(self, inner: Any, budget: LifeRequestBudget, *,
                 admission: Optional[Callable[[str, int], None]] = None) -> None:
        self.inner = inner
        self.budget = budget
        #: W05R-R1b: the pause request-start handshake, run after the reservation and
        #: immediately before the transport call -- the real per-attempt send boundary.
        self.admission = admission
        self.refusals: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        if not getattr(self.budget, "failstop", False):
            return self._invoke(request, schema_retry_index=schema_retry_index)
        if schema_retry_index:
            self.budget.note_failure("SCHEMA_RETRY_REFUSED")
        with self.budget.attempt_guard():
            try:
                result = self._invoke(request, schema_retry_index=schema_retry_index)
            except (RequestBudgetError, life_pause.RequestNotSentError, life_pause.PauseConfirmedError):
                raise
            except Exception as exc:
                self.budget.note_failure(exc)
                raise
            failure = (result.get("failure_class") if isinstance(result, Mapping)
                       else getattr(result, "failure_class", None))
            if failure:
                self.budget.note_failure(failure)
            # AG1 对非法 JSON 可能再试；本 opt-in 在返回边界先封住重试。
            if self.budget.failstop and not isinstance(getattr(result, "raw_output", {}), Mapping):
                self.budget.note_failure("INVALID_JSON")
            return result

    def _invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        kind = (REQUEST_KIND_DECISION if int(schema_retry_index or 0) == 0
                else REQUEST_KIND_RETRY)
        try:
            record = self.budget.reserve(kind)
        except RequestBudgetError as exc:
            self.refusals.append({"kind": kind, "reason_code": exc.reason_code,
                                  "detail": exc.detail, "at": _iso()})
            raise
        self.attempts.append({"kind": kind, "reservation": record["reservation"],
                              "schema_retry_index": int(schema_retry_index or 0)})
        require_coordinated_send(self.inner, self.admission, kind,
                                 int(schema_retry_index or 0))
        return self.inner.invoke(request, schema_retry_index=schema_retry_index)


    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class BudgetedModelClient:
    """Wrap a model client so a content request is reserved before it is sent."""

    def __init__(self, inner: Any, budget: LifeRequestBudget,
                 *, kind: str = REQUEST_KIND_CONTENT,
                 admission: Optional[Callable[[str, int], None]] = None,
                 writer_purpose: Optional[str] = None) -> None:
        self.inner = inner
        self.budget = budget
        self.kind = kind
        #: W05R-R1b: the same pause request-start handshake as the cognition adapter.
        self.admission = admission
        #: The fixed content-writer purpose, when the caller knows it.  ``None`` means "read it
        #: from the actual client's own construction purpose"; a client without one is UNKNOWN.
        self.writer_purpose = writer_purpose
        self.refusals: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    def _failure_stage(self) -> Any:
        if self.writer_purpose is not None:
            return self.writer_purpose
        return getattr(self.inner, "purpose", None)

    def _actual_cap(self, kwargs: Mapping[str, Any]) -> Any:
        """Only this call's explicit ``max_output_tokens``; never the construction default."""

        return kwargs.get("max_output_tokens")

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self.budget, "failstop", False):
            return self._complete(*args, **kwargs)
        stage = self._failure_stage()
        actual_cap = self._actual_cap(kwargs)
        with self.budget.attempt_guard():
            try:
                result = self._complete(*args, **kwargs)
            except (RequestBudgetError, life_pause.RequestNotSentError, life_pause.PauseConfirmedError):
                raise
            except Exception as exc:
                # The request already left and may already be paid; the stop marker is written
                # synchronously before this exception reaches the caller, so the next reserve
                # cannot pass.  Safe shape facts ride the exception's own metadata channel, and
                # only the three typed facts are copied onto the persisted marker -- usage
                # counts, message text and every other metadata key stay out of it.
                facts = attach_safe_failure_metadata(exc, stage=stage, actual_cap=actual_cap)
                self.budget.note_failure(exc, diagnostics=facts)
                raise
            failure = (result.get("failure_class") if isinstance(result, Mapping)
                       else getattr(result, "failure_class", None))
            if failure:
                self.budget.note_failure(failure)
            return result

    def _complete(self, *args: Any, **kwargs: Any) -> Any:
        try:
            record = self.budget.reserve(self.kind)
        except RequestBudgetError as exc:
            self.refusals.append({"kind": self.kind, "reason_code": exc.reason_code,
                                  "detail": exc.detail, "at": _iso()})
            raise
        self.attempts.append({"kind": self.kind, "reservation": record["reservation"]})
        require_coordinated_send(self.inner, self.admission, self.kind, 0)
        return self.inner.complete(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


__all__ = [
    "BUDGET_FILENAME",
    "BudgetedCognitionAdapter",
    "BudgetedModelClient",
    "CATEGORY_CONTINUATION",
    "CATEGORY_INITIATION",
    "CATEGORY_REFLECTION",
    "CONTENT_WRITER_STAGES",
    "DAILY_ENVELOPE_CAPS",
    "DEFAULT_CONSERVATIVE_TOKEN_RESERVATION",
    "DEFAULT_DAILY_TOKEN_BUDGET",
    "FAILURE_DIAGNOSTIC_KEYS",
    "FAILURE_STAGE_UNKNOWN",
    "KIND",
    "LOCK_FILENAME",
    "LifeRequestBudget",
    "MAX_SLOT_ENVELOPE_OPS",
    "REASON_DAILY_ENVELOPE_EXCEEDED",
    "REASON_DAILY_TOKEN_BUDGET",
    "REASON_RUN_WINDOW_LIMIT",
    "REASON_RUN_WINDOW_NOT_AUTHORIZED",
    "REASON_SLOT_QUOTA_EXCEEDED",
    "REASON_STATE_CORRUPT",
    "REASON_UNKNOWN_SLOT_OR_CATEGORY",
    "REASON_UNREADABLE",
    "REASON_UTC_DAY_LIMIT",
    "REQUEST_KIND_CONTENT",
    "REQUEST_KIND_DECISION",
    "REQUEST_KIND_RETRY",
    "REQUEST_KINDS",
    "RequestBudgetError",
    "SCHEMA_VERSION",
    "SLOT_DAYTIME_ACTIVE",
    "SLOT_EVENING_WIND_DOWN",
    "SLOT_NIGHT_REST",
    "SLOT_QUOTAS",
    "STATE_ABSENT",
    "STATE_CORRUPT",
    "STATE_OK",
    "V03_FOUR_SLOT_DAILY_CALL_CAP",
    "V03_FOUR_SLOT_DAILY_TOKEN_CAP",
    "V03_FOUR_SLOT_ORDER",
    "V03_FOUR_SLOT_QUOTAS",
    "V03_SLOT_CARRY_RATIO",
    "V03_SLOT_MODE",
    "REASON_UNKNOWN_V03_SLOT",
    "REASON_V03_DAILY_CAP_EXCEEDED",
    "REASON_V03_SLOT_QUOTA_EXCEEDED",
    "attach_safe_failure_metadata",
    "budget_path",
    "lock_path",
    "safe_failure_diagnostics",
    "safe_failure_metadata",
    "v03_four_slot_effective",
    "v03_slot_for_hour",
]
