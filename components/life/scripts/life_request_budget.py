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
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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

#: How many finished run windows and days are retained in the ledger.
MAX_WINDOWS = 32
MAX_DAYS = 64
#: How many recent reservation tokens are kept (audit trail, bounded).
MAX_TOKENS = 256

_KEYS = frozenset({
    "schema_version", "kind", "active_day", "days", "windows", "run_id",
    "tokens", "opened_at", "updated_at",
})


class RequestBudgetError(RuntimeError):
    """A refusal from the request budget.  Never a model choice."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _utc_day(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def budget_path(state_root: Path | str) -> Path:
    return Path(state_root) / BUDGET_FILENAME


def lock_path(state_root: Path | str) -> Path:
    return Path(state_root) / LOCK_FILENAME


@contextmanager
def _exclusive(state_root: Path | str) -> Iterator[None]:
    """The cross-process lock every read-modify-write of the ledger is taken under."""

    path = lock_path(state_root)
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
            "days": {}, "windows": {}, "run_id": None, "tokens": [],
            "opened_at": _iso(), "updated_at": _iso()}


class LifeRequestBudget:
    """The hard, persisted ceiling on model requests.

    ``utc_day_max`` is the per-UTC-day ceiling and ``run_window_max`` the ceiling for
    one *run window* (one 24h run identity, which survives restarts).  Either may be
    ``None``, which means "no ceiling of that kind" -- never "unlimited": the other
    one still applies, and a budget with both ``None`` is refused by the caller, not
    silently honoured here.
    """

    def __init__(self, state_root: Path | str, *, utc_day_max: Optional[int] = None,
                 run_window_max: Optional[int] = None, run_id: Optional[str] = None) -> None:
        self.state_root = Path(state_root)
        self.utc_day_max = None if utc_day_max is None else max(0, int(utc_day_max))
        self.run_window_max = None if run_window_max is None else max(0, int(run_window_max))
        self.run_id = str(run_id).strip() if run_id else None
        self._thread_lock = threading.RLock()

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
        return data

    def _save_locked(self, data: dict[str, Any]) -> None:
        days = dict(data.get("days") or {})
        if len(days) > MAX_DAYS:
            keep = sorted(days)[-MAX_DAYS:]
            data["days"] = {key: days[key] for key in keep}
        windows = dict(data.get("windows") or {})
        if len(windows) > MAX_WINDOWS:
            ordered = sorted(windows, key=lambda key: str(
                (windows[key] or {}).get("opened_at") or ""))
            data["windows"] = {key: windows[key] for key in ordered[-MAX_WINDOWS:]}
        tokens = list(data.get("tokens") or [])[-MAX_TOKENS:]
        data["tokens"] = tokens
        data["updated_at"] = _iso()
        _write_atomically(self.path, data)

    # -- view ----------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        """Read-only view.  Never raises for a corrupt ledger: it reports ``corrupt``."""

        try:
            with _exclusive(self.state_root):
                data = self._load_locked()
        except RequestBudgetError as exc:
            return {"state": STATE_CORRUPT, "reason_code": exc.reason_code,
                    "detail": exc.detail, "path": str(self.path),
                    "utc_day": _utc_day(), "utc_day_calls": None,
                    "run_id": self.run_id, "run_window_calls": None,
                    "utc_day_max": self.utc_day_max, "run_window_max": self.run_window_max}
        active = self._active_day(data)
        return {
            "state": STATE_OK if data.get("days") or data.get("windows") else STATE_ABSENT,
            "path": str(self.path),
            "utc_day": active,
            "utc_day_calls": int((data.get("days") or {}).get(active) or 0),
            "utc_day_max": self.utc_day_max,
            "history": {key: int(value) for key, value in sorted((data.get("days") or {}).items())},
            "run_id": self.run_id,
            "run_window_calls": self._window_calls(data, self.run_id),
            "run_window_max": self.run_window_max,
            "windows": {key: int((value or {}).get("calls") or 0)
                        for key, value in (data.get("windows") or {}).items()},
            "recent_tokens": list(data.get("tokens") or [])[-8:],
            "refund_policy": "no release API exists: a timeout, a crash or a retry keeps its reservation",
        }

    @staticmethod
    def _active_day(data: Mapping[str, Any]) -> str:
        """The day the counters live in.  Monotonic: a rollback never resets them."""

        stored = str(data.get("active_day") or "")
        today = _utc_day()
        return max(stored, today) if stored else today

    def _window_calls(self, data: Mapping[str, Any], run_id: Optional[str]) -> Optional[int]:
        if not run_id:
            return None
        window = (data.get("windows") or {}).get(run_id)
        return int((window or {}).get("calls") or 0) if window is not None else None

    # -- the window ----------------------------------------------------------

    def ensure_window(self, *, allow_new: bool = False) -> dict[str, Any]:
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
            windows[self.run_id] = {"calls": 0, "opened_at": _iso(), "closed_at": None}
            data["windows"] = windows
            data["run_id"] = self.run_id
            data["active_day"] = self._active_day(data)
            self._save_locked(data)
            return {"state": "opened", "run_id": self.run_id, "calls": 0}

    # -- the reservation -----------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Is one more request allowed right now?  Read-only, and fail closed."""

        try:
            with _exclusive(self.state_root):
                data = self._load_locked()
        except RequestBudgetError as exc:
            return {"allowed": False, "reason_code": exc.reason_code, "detail": exc.detail}
        return self._verdict(data)

    def _verdict(self, data: Mapping[str, Any]) -> dict[str, Any]:
        day = self._active_day(data)
        day_used = int((data.get("days") or {}).get(day) or 0)
        window_used = self._window_calls(data, self.run_id)
        if self.utc_day_max is not None and day_used >= self.utc_day_max:
            return {"allowed": False, "reason_code": REASON_UTC_DAY_LIMIT,
                    "detail": "utc day %s spent %s of %s" % (day, day_used, self.utc_day_max),
                    "utc_day": day, "utc_day_calls": day_used,
                    "run_window_calls": window_used}
        if self.run_window_max is not None and window_used is not None \
                and window_used >= self.run_window_max:
            return {"allowed": False, "reason_code": REASON_RUN_WINDOW_LIMIT,
                    "detail": "run window %s spent %s of %s"
                              % (self.run_id, window_used, self.run_window_max),
                    "utc_day": day, "utc_day_calls": day_used,
                    "run_window_calls": window_used}
        return {"allowed": True, "reason_code": "", "utc_day": day,
                "utc_day_calls": day_used, "run_window_calls": window_used}

    def reserve(self, kind: str = REQUEST_KIND_DECISION, *,
                now: Optional[datetime] = None) -> dict[str, Any]:
        """Persist one attempt's reservation **before** the caller sends anything.

        Raises :class:`RequestBudgetError` when the ceiling is reached or the ledger is
        corrupt.  There is no path that gives a reservation back.
        """

        if kind not in REQUEST_KINDS:
            raise ValueError("unknown request kind %r" % (kind,))
        with self._thread_lock, _exclusive(self.state_root):
            data = self._load_locked()
            verdict = self._verdict(data)
            if not verdict["allowed"]:
                raise RequestBudgetError(verdict["reason_code"], verdict["detail"])
            day = self._active_day(data)
            days = dict(data.get("days") or {})
            days[day] = int(days.get(day) or 0) + 1
            windows = dict(data.get("windows") or {})
            if self.run_id:
                window = dict(windows.get(self.run_id) or {"calls": 0, "opened_at": _iso(),
                                                           "closed_at": None})
                window["calls"] = int(window.get("calls") or 0) + 1
                windows[self.run_id] = window
            token = {"token": "lreq:%s" % uuid.uuid4().hex[:16], "kind": kind,
                     "utc_day": day, "run_id": self.run_id, "at": _iso(now)}
            data["days"] = days
            data["windows"] = windows
            data["active_day"] = day
            data["run_id"] = self.run_id
            data["tokens"] = list(data.get("tokens") or []) + [token]
            if not data.get("opened_at"):
                data["opened_at"] = _iso()
            self._save_locked(data)
            return {"reservation": token, "utc_day": day,
                    "utc_day_calls": days[day],
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
                 admission: Optional[Callable[[str, int], None]] = None) -> None:
        self.inner = inner
        self.budget = budget
        self.kind = kind
        #: W05R-R1b: the same pause request-start handshake as the cognition adapter.
        self.admission = admission
        self.refusals: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    def complete(self, *args: Any, **kwargs: Any) -> Any:
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
    "KIND",
    "LOCK_FILENAME",
    "LifeRequestBudget",
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
    "budget_path",
    "lock_path",
]
