#!/usr/bin/env python3
"""W05: the life-side persistent pause bit.

Why this is a separate file from the World's pause
--------------------------------------------------
``chiyo_bundle/world_control.py`` (W04) is the **World's** pause: it stops new travel
segments from being submitted.  This module is the **Life's** pause: it stops new
decisions and therefore new external actions of any kind (world moves, artifact
commits).  They are two different switches over two different owners, and W05 keeps
them separate on purpose:

* ``one_day_life.py pause`` writes *this* file and never touches ``world_control.json``;
* ``one_day_life.py resume`` clears *this* file and never touches ``world_control.json``;
* the World runner's ``pause``/``resume`` never touch this file.

What a pause means here
-----------------------
After ``set_paused(..., True)`` returns, no new decision window starts and no new
external action is started.  A segment that has already really departed still
arrives and settles by real time -- the pause does not freeze physics and does not
rewind a position.  The one-day entry therefore settles the journey with
``settle_only=True`` while paused, which is exactly W04's own explicit mode.

Fail closed
-----------
A pause file that exists but cannot be parsed is **not** "not paused": it is
``corrupt`` and the gate reports ``paused=True`` with
``reason_code=PAUSE_STATE_CORRUPT``.  A wrong guess here would let her keep acting
while the operator believes she is held.

W05R: the confirmation boundary
------------------------------
Reading the bit once at the top of a round is not a confirmation boundary.  Between
that read and the owner call a pause may be confirmed, and the round would still
commit.  ``commit_boundary`` is the linearisation point:

* it takes a **shared** lock on ``life_pause.lock`` and only then re-reads the bit,
  so a boundary that starts after ``set_paused`` returned can never see "not paused";
* ``set_paused`` takes the **exclusive** lock, so its return means no boundary is
  mid-flight and none can start until it has written the bit;
* the shared lock is taken with a bounded, non-blocking attempt: a pause that is
  being confirmed never deadlocks behind an in-flight model call, and the caller
  treats "could not acquire" as held (fail closed).

W05R-R1: which waits may be inside a boundary
---------------------------------------------
A boundary must never span a *model or content network wait*.  Wrapping the whole
decision round in one boundary made the operator's ``pause`` block behind a model
call that might never return, and made an in-flight decision outrank a confirmation
that arrived while it was waiting.  The rule is now:

* the bit is re-read under the boundary **immediately before** each external effect
  -- before the decision request starts, again after the decision is committed and
  before adoption/content, again after the content request and before the owner
  call, and again inside the segment submit of ``settle_world_journey``;
* a request that has already started is allowed to finish, but its result may not
  reach any owner: the re-read after it returns is what aborts the round;
* the **finite owner call** is made while the shared lock is held, via
  :class:`BoundaryLease`.  That is the one wait the operator's ``pause`` may block
  on, because it is bounded by the owner's own timeout and never by a model.

``BoundaryLease`` exists because the owner call happens inside AG-2/AR-0, between
two hooks, so the lock has to be taken and released explicitly rather than by a
context manager.  Every holder releases it in a ``finally``, so a raise anywhere in
the round cannot leave the operator locked out.

Atomic persistence
------------------
The bit is written to a unique temporary name, flushed, ``fsync``-ed, ``os.replace``-d
onto the real name, and then the directory is ``fsync``-ed.  A crash can therefore
leave either the old bit or the new one, never a half-written file, and never a file
that a later reader mistakes for a pause.  The revision is read and incremented
*inside* the exclusive lock, so two concurrent ``pause``/``resume`` commands cannot
lose one another's revision.
"""
from __future__ import annotations

import errno
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

try:  # POSIX only; the Life runtime's own state root requires it too.
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

PAUSE_FILENAME = "life_pause.json"
LOCK_FILENAME = "life_pause.lock"
SCHEMA_VERSION = "life.pause.v1"
KIND = "life_pause"

STATE_ABSENT = "absent"
STATE_OK = "ok"
STATE_CORRUPT = "corrupt"

REASON_PAUSED = "LIFE_PAUSED"
REASON_PAUSE_STATE_CORRUPT = "PAUSE_STATE_CORRUPT"
#: W05R: a boundary that could not take the shared lock within its bounded attempt is
#: treated as held.  Waiting forever behind an operator pause is not an option.
REASON_PAUSE_CONTENDED = "PAUSE_STATE_CONTENDED"

#: How long a commit boundary may try to take the shared lock before giving up.
DEFAULT_BOUNDARY_WAIT_SECONDS = 0.25

_KEYS = frozenset({"schema_version", "kind", "revision", "paused", "reason",
                   "paused_at", "resumed_at", "changed_at", "changed_by"})


class PauseStateError(ValueError):
    """The pause file exists and is not readable as this schema."""


class PauseConfirmedError(RuntimeError):
    """W05R-R1: the operator's pause was confirmed *while this round was in flight*.

    Raised at a boundary that is re-read immediately before an external effect, so the
    round stops before that effect instead of being reported as a success.  ``stage``
    names which boundary saw it, so the receipt can say exactly how far the round got.
    """

    def __init__(self, reason_code: str, *, stage: str = "") -> None:
        super().__init__("%s at %s" % (reason_code or REASON_PAUSED, stage or "boundary"))
        self.reason_code = str(reason_code or REASON_PAUSED)
        self.stage = str(stage or "boundary")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def pause_path(state_root: Path | str) -> Path:
    return Path(state_root) / PAUSE_FILENAME


def lock_path(state_root: Path | str) -> Path:
    """The cross-process lock the confirmation boundary is linearised on.

    A *separate* file from the bit on purpose: the lock has to survive the atomic
    ``os.replace`` of the bit, and replacing the file a process holds a lock on would
    silently move the lock to the old inode.
    """

    return Path(state_root) / LOCK_FILENAME


# ---------------------------------------------------------------------------------
# the cross-process lock
# ---------------------------------------------------------------------------------


@contextmanager
def pause_lock(state_root: Path | str, *, exclusive: bool,
               wait_seconds: Optional[float] = None) -> Iterator[bool]:
    """Hold the pause lock.  Yields ``True`` when it was really taken.

    ``exclusive=False`` (a commit boundary) makes a bounded, non-blocking attempt and
    yields ``False`` when the operator's own command is holding the lock -- the caller
    then treats the instance as held rather than waiting.  ``exclusive=True`` (the
    operator's ``pause``/``resume``) blocks, because it is the operation that has to
    wait for an in-flight commit to finish, and that wait is bounded by the commit's
    own timeout.
    """

    path = lock_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    taken = False
    try:
        if fcntl is None:  # pragma: no cover - no flock means no cross-process claim
            yield True
            return
        if exclusive:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            taken = True
        else:
            deadline = time.monotonic() + max(
                0.0, float(DEFAULT_BOUNDARY_WAIT_SECONDS if wait_seconds is None
                           else wait_seconds))
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                    taken = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
        yield taken
    finally:
        if taken and fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - releasing a lock we hold
                pass
        handle.close()


@contextmanager
def commit_boundary(state_root: Path | str,
                    *, wait_seconds: Optional[float] = None) -> Iterator[dict[str, Any]]:
    """The linearisation point between the operator's pause and a real commit.

    Inside the boundary the bit is re-read *after* the shared lock is held, so the
    answer cannot be stale with respect to ``set_paused``.  A boundary that cannot
    take the lock reports ``blocked=True`` with ``PAUSE_STATE_CONTENDED``: fail closed.
    """

    with pause_lock(state_root, exclusive=False, wait_seconds=wait_seconds) as taken:
        if not taken:
            yield {"blocked": True, "reason_code": REASON_PAUSE_CONTENDED,
                   "paused": True, "acquired": False, "state": "contended"}
            return
        view = gate(state_root)
        view = dict(view)
        view["acquired"] = True
        yield view


def admit_request(state_root: Path | str, *, stage: str = "before_request",
                  wait_seconds: Optional[float] = None) -> dict[str, Any]:
    """W05R-R1b: the request-start handshake, at the *actual* send boundary.

    Reading the bit at the top of a round is not enough: the operator can confirm between
    that read and the transport call, and the request would still start.  This is the
    admission every real request attempt goes through immediately before it is sent:

    * it takes the **shared** confirmation lock, re-reads the bit *while holding it*, and
      raises :class:`PauseConfirmedError` when it is set (or when the lock is contended --
      fail closed);
    * it releases the lock before returning, so the caller's network *response* wait is
      never under a pause/shared/control lock, and the operator's ``set_paused`` is never
      queued behind a model;
    * because ``set_paused`` holds the exclusive lock while it writes, a confirmation that
      returns before this call can never be missed here, and a request that passes here
      counts as already started -- its result still has to pass the post-request checks
      before it may reach any owner.
    """

    with commit_boundary(state_root, wait_seconds=wait_seconds) as hold:
        view = dict(hold)
    if view.get("blocked"):
        raise PauseConfirmedError(str(view.get("reason_code") or REASON_PAUSED), stage=stage)
    return view

REASON_REQUEST_NOT_SENT = "REQUEST_NOT_SENT_UNCOORDINATED_TRANSPORT"


class RequestNotSentError(RuntimeError):
    """W05R-R1c: a request was refused **before** anything was sent.

    Raised when a send cannot be coordinated with the pause confirmation.  An opaque
    transport that cannot separate its finite *send phase* from the *response wait* gives
    no point at which the confirmation lock can be held across the actual write-out, so
    the request is not sent at all instead of being sent on the strength of an earlier
    check.  ``reason_code`` names that exactly; nothing is retried or substituted.
    """

    def __init__(self, reason_code: str = REASON_REQUEST_NOT_SENT,
                 detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail or
                                     "the transport cannot separate send from response"))
        self.reason_code = str(reason_code)
        self.detail = str(detail)


class RequestSendGuard:
    """W05R-R1c: holds the pause confirmation lock across one **finite send phase**.

    ``sending(kind)`` takes the shared confirmation lock, re-reads the bit **while holding
    it** and raises :class:`PauseConfirmedError` when it is set (or when the lock is
    contended -- fail closed).  The caller performs the actual write-out inside the
    ``with`` block; the lock is released when the block exits, so the **response wait never
    runs under a pause/shared/control lock**.  A confirmation that returned before
    ``sending()`` can therefore never be overtaken by a request that starts after it, and
    the permit to send is this held lock -- not an earlier check.
    """

    def __init__(self, state_root: Path | str,
                 *, wait_seconds: Optional[float] = None) -> None:
        self.state_root = Path(state_root)
        self._wait_seconds = wait_seconds

    @contextmanager
    def sending(self, kind: str = "request") -> Iterator[dict[str, Any]]:
        stage = "before_%s_send" % str(kind or "request")
        with pause_lock(self.state_root, exclusive=False,
                        wait_seconds=self._wait_seconds) as taken:
            if not taken:
                raise PauseConfirmedError(REASON_PAUSE_CONTENDED, stage=stage)
            view = dict(gate(self.state_root))
            if view.get("blocked"):
                raise PauseConfirmedError(str(view.get("reason_code") or REASON_PAUSED),
                                          stage=stage)
            yield view


class BoundaryLease:
    """W05R-R1: the shared confirmation lock, held explicitly across one owner call.

    A context manager cannot be used for this: the owner call happens inside AG-2/AR-0,
    between two hooks, so the lock has to be taken at the first hook and released at the
    second.  ``acquire`` is a bounded, non-blocking attempt that yields ``False`` when
    the operator's own ``pause`` holds the exclusive lock -- the caller treats that as
    held (fail closed) and starts no owner call.  ``release`` is idempotent, and every
    holder releases in a ``finally``, so a raise cannot lock the operator out.

    Holding it across the owner call is what closes the last TOCTOU window: a ``pause``
    confirmed anywhere before ``acquire`` returns makes it fail, and one confirmed
    during the call waits for the call -- a finite, owner-bounded wait, never a model.
    """

    def __init__(self, state_root: Path | str) -> None:
        self._root = Path(state_root)
        self._handle: Any = None
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self, *, wait_seconds: Optional[float] = None) -> bool:
        if self._held:
            return True
        path = lock_path(self._root)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        if fcntl is None:  # pragma: no cover - no flock means no cross-process claim
            self._handle, self._held = handle, True
            return True
        deadline = time.monotonic() + max(
            0.0, float(DEFAULT_BOUNDARY_WAIT_SECONDS if wait_seconds is None
                       else wait_seconds))
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                self._handle, self._held = handle, True
                return True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    handle.close()
                    raise
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(0.005)

    def release(self) -> None:
        handle, self._handle, self._held = self._handle, None, False
        if handle is None:
            return
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:  # pragma: no cover - releasing a lock we hold
                    pass
        finally:
            handle.close()


def boundary_lease(state_root: Path | str) -> BoundaryLease:
    """A :class:`BoundaryLease` over this state root's confirmation lock."""

    return BoundaryLease(state_root)

# ---------------------------------------------------------------------------------
# the bit
# ---------------------------------------------------------------------------------


def _read_raw(state_root: Path | str) -> Optional[dict[str, Any]]:
    path = pause_path(state_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PauseStateError("pause file unreadable: %s" % exc) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PauseStateError("pause file is not JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise PauseStateError("pause file is not an object")
    if set(data) - _KEYS:
        raise PauseStateError("pause file has unknown keys: %s"
                              % sorted(set(data) - _KEYS))
    if data.get("schema_version") != SCHEMA_VERSION or data.get("kind") != KIND:
        raise PauseStateError("pause file has an unexpected schema")
    if not isinstance(data.get("paused"), bool) or not isinstance(data.get("revision"), int):
        raise PauseStateError("pause file has an invalid shape")
    return data


def read(state_root: Path | str) -> dict[str, Any]:
    """The persisted bit, read-only.  ``absent`` / ``ok`` / ``corrupt`` are distinct."""

    try:
        data = _read_raw(state_root)
    except PauseStateError as exc:
        return {"state": STATE_CORRUPT, "paused": True, "revision": None,
                "paused_at": None, "resumed_at": None, "reason": None,
                "detail": str(exc), "path": str(pause_path(state_root))}
    if data is None:
        return {"state": STATE_ABSENT, "paused": False, "revision": None,
                "paused_at": None, "resumed_at": None, "reason": None,
                "path": str(pause_path(state_root))}
    return {"state": STATE_OK, "paused": bool(data["paused"]), "revision": data["revision"],
            "paused_at": data.get("paused_at"), "resumed_at": data.get("resumed_at"),
            "reason": data.get("reason"), "changed_at": data.get("changed_at"),
            "changed_by": data.get("changed_by"),
            "path": str(pause_path(state_root))}


def gate(state_root: Path | str) -> dict[str, Any]:
    """``{"blocked": bool, "reason_code": str}`` -- the decision gate, fail closed."""

    view = read(state_root)
    if view["state"] == STATE_CORRUPT:
        return {"blocked": True, "reason_code": REASON_PAUSE_STATE_CORRUPT,
                "state": view["state"], "paused": True, "view": view}
    if view["paused"]:
        return {"blocked": True, "reason_code": REASON_PAUSED, "state": view["state"],
                "paused": True, "view": view}
    return {"blocked": False, "reason_code": "", "state": view["state"], "paused": False,
            "view": view}


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - directory fsync is best effort
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(fd)

def _write_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    """Unique temp name + flush + fsync + replace + directory fsync."""

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


def set_paused(state_root: Path | str, paused: bool, *, reason: str = "operator",
               actor: str = "operator", now: Any = None) -> dict[str, Any]:
    """Write the bit atomically, under the exclusive lock.

    Holding the exclusive lock is what makes the confirmation real: when this returns,
    every commit boundary that was in flight has finished, and no new one can start
    without seeing the new bit.
    """

    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    moment = now if isinstance(now, datetime) else _now()
    with pause_lock(root, exclusive=True):
        try:
            previous = _read_raw(root)
        except PauseStateError:
            # A corrupt file is overwritten by an explicit operator command, and the
            # previous revision is reported as unknown rather than invented.
            previous = None
        revision = int((previous or {}).get("revision") or 0) + 1
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "revision": revision,
            "paused": bool(paused),
            "reason": str(reason or ""),
            "paused_at": _iso(moment) if paused else (previous or {}).get("paused_at"),
            "resumed_at": None if paused else _iso(moment),
            "changed_at": _iso(moment),
            "changed_by": str(actor or "operator"),
        }
        _write_atomically(pause_path(root), payload)
    return dict(payload)


__all__ = [
    "BoundaryLease",
    "DEFAULT_BOUNDARY_WAIT_SECONDS",
    "KIND",
    "LOCK_FILENAME",
    "PAUSE_FILENAME",
    "PauseConfirmedError",
    "PauseStateError",
    "REASON_PAUSE_CONTENDED",
    "REASON_PAUSE_STATE_CORRUPT",
    "REASON_PAUSED",
    "REASON_REQUEST_NOT_SENT",
    "RequestNotSentError",
    "RequestSendGuard",
    "SCHEMA_VERSION",
    "STATE_ABSENT",
    "STATE_CORRUPT",
    "STATE_OK",
    "admit_request",
    "boundary_lease",
    "commit_boundary",
    "gate",
    "lock_path",
    "pause_lock",
    "pause_path",
    "read",
    "set_paused",
]
