#!/usr/bin/env python3
"""共享的读-改-写（RMW）事务守卫：跨线程/跨进程锁、提交序回执、长程重试对账。

本模块只提供机制，不绑定任何业务状态机。Round B1/B2 把各调用方接到这里。

不变量
------
1. ``state_file_lock`` 只用于**写事务**。只读路径绝不调用它，因此零写读不会创建
   ``.lock`` 文件或目录（零写规则）。
2. 同一进程内按 ``resolve()`` 后的路径复用一把 ``threading.RLock``（支持同线程重入），
   再叠加 OS 级排他锁（POSIX ``fcntl.flock`` / Windows ``msvcrt.locking``）。
3. 回执按提交序号 ``op_seq`` 单调递增，淘汰永远按 ``op_seq`` 从旧到新，绝不按
   ``sort_keys=True`` 的字母序。
4. 被淘汰的回执进入有界 ``committed_op_ring``，仍能按原结果幂等重放；ring 也被挤掉后，
   可由 ``<root>/phone/operations.jsonl`` 兜底；再往前的过期重试显式返回
   ``RETRY_WINDOW_EXPIRED``，不会被当成全新请求。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

try:  # POSIX / WSL
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

try:  # Windows fallback
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None


__all__ = [
    "atomic_write_json",
    "payload_fingerprint",
    "reconcile_evicted_or_active_op",
    "stamp_receipt",
    "state_file_lock",
    "trim_receipts_by_op_seq",
]

MAX_COMMITTED_RING = 4096
_SUCCESS_STATUSES = frozenset({"applied", "already_applied", "APPLIED"})


# ---------------------------------------------------------------------------------------------
# 共享写锁
# ---------------------------------------------------------------------------------------------


class _PathLock:
    """一条路径对应的一把进程内重入锁 + OS 锁句柄。"""

    __slots__ = ("rlock", "depth", "handle")

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0
        self.handle: Optional[Any] = None


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, _PathLock] = {}


def _canonical_lock_key(state_path: Path) -> str:
    """Stable process-local lock key that cannot split on Windows path prefixes.

    On Windows ``Path.resolve()`` may flip between ``C:\\...`` and the extended-length
    ``\\\\?\\C:\\...`` form once the parent directory is created.  The same file would
    then get two ``_PathLock`` entries, each starting at depth 1, and the second
    ``msvcrt.locking`` call on the same process-owned byte deadlocks forever.  Strip
    the extended-length / UNC prefix and fold case before using the key.
    """

    resolved = str(state_path.resolve())
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
        resolved = os.path.normcase(os.path.normpath(resolved))
    return resolved


def _path_entry(state_path: Path) -> _PathLock:
    key = _canonical_lock_key(state_path)
    with _LOCKS_GUARD:
        entry = _PATH_LOCKS.get(key)
        if entry is None:
            entry = _PathLock()
            _PATH_LOCKS[key] = entry
        return entry


def _ensure_lock_byte(handle: Any) -> None:
    """Windows 上 ``msvcrt.locking`` 需要一个可锁定的字节位置。"""

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def _acquire_os_lock(lock_path: Path) -> Optional[Any]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 永远不删除锁文件：删除会让 POSIX 上已经打开同一 inode 的进程与新进程分属两把锁。
    handle = open(lock_path, "a+b")
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        elif _msvcrt is not None:  # pragma: no cover - Windows
            _ensure_lock_byte(handle)
            while True:
                try:
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        return handle
    except BaseException:
        try:
            handle.close()
        except OSError:
            pass
        raise


def _release_os_lock(handle: Optional[Any]) -> None:
    if handle is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        elif _msvcrt is not None:  # pragma: no cover - Windows
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
    finally:
        try:
            handle.close()
        except OSError:
            pass


@contextmanager
def state_file_lock(state_path: Path | str) -> Iterator[Path]:
    """把一次完整的事务包在「进程内重入锁 + OS 排他锁」里。

    只用于写事务。只读调用方不要调用本函数，否则零写读会凭空创建 ``.lock``。
    锁文件为 ``<state_path>.lock``，父目录缺失时按需创建。
    """

    state_path = Path(state_path)
    lock_path = Path(str(state_path) + ".lock")
    entry = _path_entry(state_path)
    with entry.rlock:
        entry.depth += 1
        if entry.depth == 1:
            entry.handle = _acquire_os_lock(lock_path)
        try:
            yield lock_path
        finally:
            if entry.depth == 1:
                handle, entry.handle = entry.handle, None
                _release_os_lock(handle)
            entry.depth -= 1


# ---------------------------------------------------------------------------------------------
# 回执指纹与落盘
# ---------------------------------------------------------------------------------------------


def _normalize_for_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize_for_json(item) for item in value), key=repr)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, Path):
        return str(value)
    return value


def payload_fingerprint(payload: Mapping[str, Any] | Any) -> str:
    """稳定 SHA256：规范化 JSON（键排序、无空白）后的十六进制摘要。"""

    normalized = _normalize_for_json(payload)
    blob = json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _next_op_seq(state: dict[str, Any], ops: Mapping[str, Any]) -> int:
    highest = _coerce_int(state.get("next_op_seq")) or 0
    for row in ops.values():
        if isinstance(row, Mapping):
            existing = _coerce_int(row.get("op_seq"))
            if existing is not None and existing > highest:
                highest = existing
    return highest + 1


def stamp_receipt(
    state: dict[str, Any],
    operation_key: str,
    receipt: Mapping[str, Any],
    *,
    revision: int,
    dict_key: str = "operations",
    payload_hash: str = "",
    committed_at: str = "",
) -> dict[str, Any]:
    """按提交顺序写入一条回执，并递增持久化的 ``next_op_seq``。"""

    ops = state.get(dict_key)
    if not isinstance(ops, dict):
        ops = {}
        state[dict_key] = ops

    op_seq = _next_op_seq(state, ops)
    state["next_op_seq"] = op_seq

    stored = copy.deepcopy(dict(receipt))
    stored["operation_key"] = operation_key
    stored["op_seq"] = op_seq
    stored["revision"] = int(revision)
    if payload_hash:
        stored["payload_hash"] = payload_hash
    if committed_at:
        stored["committed_at"] = committed_at

    ops[operation_key] = stored
    return stored


def _backfill_legacy_op_seq(ops: dict[str, Any]) -> None:
    """给守卫上线前写下的回执行补上确定性的低位 ``op_seq``（仅缺失的行）。"""

    missing: list[tuple[str, Any]] = []
    for key, row in ops.items():
        if not isinstance(row, dict):
            continue
        if _coerce_int(row.get("op_seq")) is None:
            missing.append((str(key), row))
    if not missing:
        return

    def _order(item: tuple[str, Any]) -> tuple[str, str]:
        key, row = item
        return (str(row.get("committed_at") or ""), key)

    missing.sort(key=_order)
    offset = -len(missing)
    for index, (_key, row) in enumerate(missing):
        row["op_seq"] = offset + index


def _row_sort_key(key: str, row: Any) -> tuple[int, str, str]:
    seq = 0
    committed_at = ""
    if isinstance(row, Mapping):
        seq = _coerce_int(row.get("op_seq")) or 0
        committed_at = str(row.get("committed_at") or "")
    return (seq, committed_at, str(key))


def _bump_evicted_revision(state: dict[str, Any], revision: Any) -> None:
    value = _coerce_int(revision)
    if value is None:
        return
    current = _coerce_int(state.get("evicted_up_to_revision")) or 0
    state["evicted_up_to_revision"] = max(current, value)


def _ring_summary(key: str, row: Any) -> dict[str, Any]:
    source = row if isinstance(row, Mapping) else {}
    status = source.get("status")
    ok_value = source.get("ok")
    if ok_value is None:
        ok_value = status is None or str(status) in _SUCCESS_STATUSES
    return {
        "operation_key": key,
        "op_seq": _coerce_int(source.get("op_seq")),
        "revision": _coerce_int(source.get("revision")),
        "status": status,
        "ok": bool(ok_value),
        "payload_hash": source.get("payload_hash") or "",
    }


def trim_receipts_by_op_seq(
    state: dict[str, Any],
    *,
    dict_key: str = "operations",
    max_keep: int = 200,
    max_ring: int = 4096,
) -> list[str]:
    """按 ``(op_seq, committed_at, key)`` 从旧到新淘汰回执，返回被淘汰的键。

    淘汰顺序只由提交序号决定，与 ``sort_keys=True`` 的字母序无关。每条被淘汰的回执
    压成紧凑行进入有界 ``committed_op_ring``，并推进 ``evicted_up_to_revision``。
    """

    ops = state.get(dict_key)
    if not isinstance(ops, dict):
        return []
    _backfill_legacy_op_seq(ops)

    ring = state.get("committed_op_ring")
    if not isinstance(ring, list):
        ring = []
        state["committed_op_ring"] = ring

    keep_limit = max(0, int(max_keep))
    ring_limit = max(0, int(max_ring))

    evicted: list[str] = []
    while len(ops) > keep_limit:
        key, row = min(ops.items(), key=lambda item: _row_sort_key(item[0], item[1]))
        del ops[key]
        evicted.append(str(key))
        summary = _ring_summary(str(key), row)
        ring.append(summary)
        _bump_evicted_revision(state, summary.get("revision"))

    if len(ring) > ring_limit:
        dropped = len(ring) - ring_limit
        for summary in ring[:dropped]:
            if isinstance(summary, Mapping):
                _bump_evicted_revision(state, summary.get("revision"))
        del ring[:dropped]

    return evicted


# ---------------------------------------------------------------------------------------------
# 长程对账
# ---------------------------------------------------------------------------------------------


def _reconcile_from_journal(root: Path | str, operation_key: str) -> Optional[dict[str, Any]]:
    journal = Path(root) / "phone" / "operations.jsonl"
    try:
        if not journal.is_file():
            return None
    except OSError:
        return None

    matched: Optional[dict[str, Any]] = None
    try:
        with journal.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(entry, Mapping):
                    continue
                if entry.get("operation_key") != operation_key:
                    continue
                status = str(entry.get("status") or "")
                if entry.get("ok") is True or status in _SUCCESS_STATUSES:
                    matched = dict(entry)  # 保留最后一条成功记录
    except OSError:
        return matched
    return matched


def _hash_conflict(payload_hash: Optional[str], stored_hash: Any) -> bool:
    if not payload_hash:
        return False
    expected = str(stored_hash or "")
    return bool(expected) and expected != payload_hash


def reconcile_evicted_or_active_op(
    root: Path | str | None,
    state: Mapping[str, Any],
    operation_key: str,
    *,
    dict_key: str = "operations",
    expected_revision: Optional[int] = None,
    payload_hash: Optional[str] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """把一次重试归类为 ``hit_full`` / ``hit_evicted`` / ``expired`` / ``conflict`` / ``miss``。"""

    ops = state.get(dict_key)
    if isinstance(ops, Mapping):
        existing = ops.get(operation_key)
        if isinstance(existing, Mapping):
            if _hash_conflict(payload_hash, existing.get("payload_hash")):
                return (
                    "conflict",
                    {"reason_code": "OPERATION_KEY_CONFLICT", "operation_key": operation_key},
                )
            return ("hit_full", copy.deepcopy(dict(existing)))

    row: Optional[dict[str, Any]] = None
    source = ""
    ring = state.get("committed_op_ring")
    if isinstance(ring, list):
        for candidate in reversed(ring):
            if isinstance(candidate, Mapping) and candidate.get("operation_key") == operation_key:
                row = dict(candidate)
                source = "committed_op_ring"
                break

    if row is None and root is not None:
        journal_row = _reconcile_from_journal(root, operation_key)
        if journal_row is not None:
            row = journal_row
            source = "operations.jsonl"

    if row is not None:
        if _hash_conflict(payload_hash, row.get("payload_hash")):
            return (
                "conflict",
                {"reason_code": "OPERATION_KEY_CONFLICT", "operation_key": operation_key},
            )
        revision = row.get("revision")
        if revision is None:
            revision = state.get("revision")
        status = row.get("status") or "already_applied"
        return (
            "hit_evicted",
            {
                "operation_key": operation_key,
                "already_applied": True,
                "reconciled_from": source,
                "ok": True,
                "status": status,
                "revision": revision,
            },
        )

    if expected_revision is not None:
        exp = _coerce_int(expected_revision)
        evicted_rev = _coerce_int(state.get("evicted_up_to_revision")) or 0
        cur_rev = _coerce_int(state.get("revision")) or 0
        if exp is not None and evicted_rev > 0 and exp <= evicted_rev and exp < cur_rev:
            return (
                "expired",
                {
                    "reason_code": "RETRY_WINDOW_EXPIRED",
                    "operation_key": operation_key,
                    "expected_revision": exp,
                    "evicted_up_to_revision": evicted_rev,
                    "current_revision": cur_rev,
                },
            )

    return ("miss", None)


# ---------------------------------------------------------------------------------------------
# 原子 JSON 落盘
# ---------------------------------------------------------------------------------------------


def atomic_write_json(
    path: Path | str,
    doc: Mapping[str, Any],
    *,
    sort_keys: bool = True,
) -> None:
    """``tempfile`` + ``fsync`` + ``os.replace`` 的原子写入；出错时清理临时文件。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(
        doc,
        ensure_ascii=False,
        sort_keys=sort_keys,
        indent=2,
        default=str,
    )
    tmp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as handle:
            tmp_name = handle.name
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
