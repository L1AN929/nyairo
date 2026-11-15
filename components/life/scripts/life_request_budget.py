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

#: How many finished run windows and days are retained in the ledger.
MAX_WINDOWS = 32
MAX_DAYS = 64
#: How many recent reservation tokens are kept (audit trail, bounded).
MAX_TOKENS = 256

_KEYS = frozenset({
    "schema_version", "kind", "active_day", "days", "windows", "run_id",
    "tokens", "opened_at", "updated_at", "day_tokens",
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
            with _exclusive(self.state_root):
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

    def check(self, *, now: Optional[datetime] = None) -> dict[str, Any]:
        """Is one more request allowed right now?  Read-only, and fail closed."""

        try:
            with _exclusive(self.state_root):
                data = self._load_locked()
        except RequestBudgetError as exc:
            return {"allowed": False, "reason_code": exc.reason_code, "detail": exc.detail}
        return self._verdict(data, moment=now)

    def _verdict(self, data: Mapping[str, Any], *,
                 moment: Optional[datetime] = None) -> dict[str, Any]:
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
        if self.daily_token_budget is not None and day_tokens_used >= self.daily_token_budget:
            return dict(base, allowed=False, reason_code=REASON_DAILY_TOKEN_BUDGET,
                        detail="business day %s (%s) reserved %s of %s est tokens"
                               % (day, self.business_tz_label, day_tokens_used,
                                  self.daily_token_budget))
        return dict(base, allowed=True, reason_code="")

    def reserve(self, kind: str = REQUEST_KIND_DECISION, *,
                est_tokens: Optional[int] = None,
                now: Optional[datetime] = None) -> dict[str, Any]:
        """Persist one attempt's reservation **before** the caller sends anything.

        ``est_tokens`` is the reservation's estimated token cost -- the wake ticket's own
        ``est_tokens`` when one exists (07 章 §二).  ``None`` charges the conservative
        constant ``DEFAULT_CONSERVATIVE_TOKEN_RESERVATION``.  A given estimate must be a
        non-negative int (booleans refused).  Raises :class:`RequestBudgetError` when any
        ceiling is reached or the ledger is corrupt.  There is no path that gives a
        reservation back -- the estimate keeps its reservation like the call does.
        """

        if kind not in REQUEST_KINDS:
            raise ValueError("unknown request kind %r" % (kind,))
        if est_tokens is None:
            est = DEFAULT_CONSERVATIVE_TOKEN_RESERVATION
        else:
            if isinstance(est_tokens, bool) or not isinstance(est_tokens, int) or est_tokens < 0:
                raise ValueError("est_tokens must be a non-negative int, got %r" % (est_tokens,))
            est = int(est_tokens)
        with self._thread_lock, _exclusive(self.state_root):
            self.check_failstop()
            data = self._load_locked()
            verdict = self._verdict(data, moment=now)
            if not verdict["allowed"]:
                raise RequestBudgetError(verdict["reason_code"], verdict["detail"])
            day = self._active_day(data, moment=now)
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
            return {"reservation": token, "utc_day": day,
                    "business_day": day, "business_tz": self.business_tz_label,
                    "utc_day_calls": days[day],
                    "utc_day_tokens": day_tokens[day],
                    "run_window_calls": self._window_calls(data, self.run_id)}


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
    "CONTENT_WRITER_STAGES",
    "DEFAULT_CONSERVATIVE_TOKEN_RESERVATION",
    "DEFAULT_DAILY_TOKEN_BUDGET",
    "FAILURE_DIAGNOSTIC_KEYS",
    "FAILURE_STAGE_UNKNOWN",
    "KIND",
    "LOCK_FILENAME",
    "LifeRequestBudget",
    "REASON_DAILY_TOKEN_BUDGET",
    "REASON_RUN_WINDOW_LIMIT",
    "REASON_RUN_WINDOW_NOT_AUTHORIZED",
    "REASON_STATE_CORRUPT",
    "REASON_UNREADABLE",
    "REASON_UTC_DAY_LIMIT",
    "REQUEST_KIND_CONTENT",
    "REQUEST_KIND_DECISION",
    "REQUEST_KIND_RETRY",
    "REQUEST_KINDS",
    "RequestBudgetError",
    "SCHEMA_VERSION",
    "STATE_ABSENT",
    "STATE_CORRUPT",
    "STATE_OK",
    "attach_safe_failure_metadata",
    "budget_path",
    "lock_path",
    "safe_failure_diagnostics",
    "safe_failure_metadata",
]
