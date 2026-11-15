#!/usr/bin/env python3
"""World 食物/物件丢弃与消耗 256 条环形去重防复活回执契约（零大模型调用）。

对应设计与勘误条款：
- ``Ch.13 §3 / §5``（餐桌厨房与饮食消耗生态：食物份数单调递减、变质丢弃 ``DISCARD`` 零热量防伪）；
- ``Ch.14``（共在状态机与消耗品物理接口：物理消亡回执与防凭空复活）；
- ``GAP-08``（``DISCARD`` 与 ``CONSUME`` 严禁混同；``Body`` 消费者硬拒绝把丢弃回执折算为营养摄入）；
- ``ERR-05``（全链路份数/件数严格使用正整数，禁止浮点残余导致幽灵份数）。

核心不变量：
1. **256 条有界环形去重账本（``MAX_DISPOSAL_RECEIPTS = 256``）**：
   - 按 FIFO 维护最近 256 条 ``DisposalReceipt`` 与 256 条终态防复活墓碑（``terminal_tombstones``），
     超出容量时自动挤出最旧槽位并累加 ``evicted_receipt_count``，绝不无限膨胀 JSON。
2. **``operation_key`` 幂等核验**：
   - 相同 ``operation_key`` + 相同 ``proposal_fingerprint`` -> 返回原 ``DisposalReceipt`` 且状态为
     ``"DUPLICATE"``，零重复扣减；
   - 相同 ``operation_key`` + 不同参数指纹 -> 409 ``OPERATION_FINGERPRINT_MISMATCH``。
3. **防复活与份数单调递减硬闸门**：
   - 一旦某 ``object_id`` 进入终态（``terminal == True`` / ``remaining_quantity == 0``），任何新
     ``operation_key`` 试图再次消耗、再次丢弃或将其重新摆放/复活，一律硬拦截（409
     ``ALREADY_DISPOSED`` / ``OBJECT_RESURRECTION_FORBIDDEN``）；
   - 非终态分次消耗时，新 ``remaining_quantity`` 必须严格 ``<= previous_remaining - quantity_disposed``，
     杜绝份数倒涨复活（409 ``PORTION_RESURRECTION_FORBIDDEN``）。
4. **丢弃零营养铁律（``GAP-08``）**：
   - ``DISCARD`` / ``SPOILED_DISCARD`` / ``CLEANUP_RESIDUE`` 的 ``energy_credit`` 恒为 ``0``，
     ``counts_as_nutrition`` 恒为 ``False``，``verify_body_ingest`` 一律拒绝入账身体能量。
5. **纯零写入读取**：
   - ``read_disposal_ledger_zero_write`` 在目录或文件不存在时绝不创建目录、绝不落盘。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterable, Mapping, Optional

SCHEMA_DISPOSAL_RECEIPT = "world.disposal.receipt.v1"
SCHEMA_DISPOSAL_LEDGER = "world.disposal.ledger.v1"
KIND_DISPOSAL_RECEIPT = "world_disposal_receipt"
KIND_DISPOSAL_LEDGER = "world_disposal_ledger"

MAX_DISPOSAL_RECEIPTS = 256
RING_CAPACITY = MAX_DISPOSAL_RECEIPTS
LEDGER_FILENAME = "world_disposal_receipts.json"
DEFAULT_LEDGER_RELPATH = Path("state") / LEDGER_FILENAME

DISPOSAL_KIND_DISCARD = "DISCARD"
DISPOSAL_KIND_CONSUME = "CONSUME"
DISPOSAL_KIND_SPOILED_DISCARD = "SPOILED_DISCARD"
DISPOSAL_KIND_CLEANUP_RESIDUE = "CLEANUP_RESIDUE"

DISPOSAL_KINDS: tuple[str, ...] = (
    DISPOSAL_KIND_DISCARD,
    DISPOSAL_KIND_CONSUME,
    DISPOSAL_KIND_SPOILED_DISCARD,
    DISPOSAL_KIND_CLEANUP_RESIDUE,
)
NON_NUTRITIONAL_DISPOSAL_KINDS: frozenset[str] = frozenset(
    {
        DISPOSAL_KIND_DISCARD,
        DISPOSAL_KIND_SPOILED_DISCARD,
        DISPOSAL_KIND_CLEANUP_RESIDUE,
    }
)

OBJECT_KINDS: frozenset[str] = frozenset({"food", "item", "consumable", "residue"})
DEFAULT_TZ = timezone(timedelta(hours=8))

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class DisposalContractError(ValueError):
    """Structured fail-closed error for disposal & anti-resurrection contract violations."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "DISPOSAL_CONTRACT_ERROR",
        status_code: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details: dict[str, Any] = dict(details or {})


def _process_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()) if path.exists() else str(path.absolute())
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _require_safe_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise DisposalContractError(
            f"{field_name} must be a non-empty string",
            code="INVALID_FIELD",
            status_code=400,
            details={"field": field_name},
        )
    text = value.strip()
    if not text or not _SAFE_ID_RE.match(text):
        raise DisposalContractError(
            f"{field_name} has invalid identifier syntax: {value!r}",
            code="INVALID_IDENTIFIER",
            status_code=400,
            details={"field": field_name},
        )
    return text


def _require_non_empty_str(value: Any, field_name: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise DisposalContractError(
            f"{field_name} must be a string",
            code="INVALID_FIELD",
            status_code=400,
            details={"field": field_name},
        )
    cleaned = value.replace("\x00", "").strip()
    if not cleaned:
        raise DisposalContractError(
            f"{field_name} must be non-empty",
            code="EMPTY_FIELD",
            status_code=400,
            details={"field": field_name},
        )
    if len(cleaned) > max_len:
        raise DisposalContractError(
            f"{field_name} exceeds max length {max_len}",
            code="FIELD_TOO_LONG",
            status_code=400,
            details={"field": field_name, "max_len": max_len},
        )
    return cleaned


def _require_strict_int(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int = 1_000_000,
) -> int:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
        code = "FLOAT_QUANTITY_FORBIDDEN" if isinstance(value, float) else "INVALID_INTEGER"
        raise DisposalContractError(
            f"{field_name} must be an exact integer (ERR-05 forbids floats)",
            code=code,
            status_code=400,
            details={"field": field_name, "received_type": type(value).__name__},
        )
    if value < minimum or value > maximum:
        raise DisposalContractError(
            f"{field_name} must be in [{minimum}, {maximum}], got {value}",
            code="OUT_OF_RANGE",
            status_code=400,
            details={"field": field_name, "minimum": minimum, "maximum": maximum},
        )
    return value


def _normalize_iso(value: Any, field_name: str = "disposed_at") -> str:
    if value is None:
        return datetime.now(tz=DEFAULT_TZ).isoformat(timespec="seconds")
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=DEFAULT_TZ)
        return dt.isoformat(timespec="seconds")
    if not isinstance(value, str) or not value.strip():
        raise DisposalContractError(
            f"{field_name} must be an ISO-8601 timestamp string",
            code="INVALID_TIMESTAMP",
            status_code=400,
            details={"field": field_name},
        )
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DisposalContractError(
            f"{field_name} is not valid ISO-8601: {value!r}",
            code="INVALID_TIMESTAMP",
            status_code=400,
            details={"field": field_name},
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.isoformat(timespec="seconds")


def compute_disposal_fingerprint(
    *,
    object_id: str,
    object_kind: str,
    disposal_kind: str,
    actor_id: str,
    quantity_disposed: int,
    remaining_quantity: int,
    unit: str,
    reason: str,
    energy_credit: int = 0,
) -> str:
    """Compute canonical SHA-256 hex digest for idempotency verification."""
    canonical = {
        "actor_id": actor_id,
        "disposal_kind": disposal_kind,
        "energy_credit": energy_credit,
        "object_id": object_id,
        "object_kind": object_kind,
        "quantity_disposed": quantity_disposed,
        "reason": reason,
        "remaining_quantity": remaining_quantity,
        "unit": unit,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deterministic_receipt_id(operation_key: str, object_id: str) -> str:
    seed = f"{SCHEMA_DISPOSAL_RECEIPT}:{operation_key}:{object_id}".encode("utf-8")
    return f"disp_rcpt_{hashlib.sha256(seed).hexdigest()[:16]}"


@dataclass(frozen=True)
class DisposalReceipt:
    """Immutable receipt proving one food/item/residue disposal or consumption fact."""

    schema_version: str
    kind: str
    receipt_id: str
    ring_seq: int
    operation_key: str
    proposal_fingerprint: str
    object_id: str
    object_kind: str
    disposal_kind: str
    actor_id: str
    body_id: Optional[str]
    quantity_disposed: int
    remaining_quantity: int
    terminal: bool
    unit: str
    reason: str
    energy_credit: int
    counts_as_nutrition: bool
    before_revision: int
    after_revision: int
    placement_before: Optional[dict[str, Any]]
    disposed_at: str

    @property
    def item_id(self) -> str:
        """Alias for callers using ``item_id`` instead of ``object_id``."""
        return self.object_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "receipt_id": self.receipt_id,
            "ring_seq": self.ring_seq,
            "operation_key": self.operation_key,
            "proposal_fingerprint": self.proposal_fingerprint,
            "object_id": self.object_id,
            "object_kind": self.object_kind,
            "disposal_kind": self.disposal_kind,
            "actor_id": self.actor_id,
            "body_id": self.body_id,
            "quantity_disposed": self.quantity_disposed,
            "remaining_quantity": self.remaining_quantity,
            "terminal": self.terminal,
            "unit": self.unit,
            "reason": self.reason,
            "energy_credit": self.energy_credit,
            "counts_as_nutrition": self.counts_as_nutrition,
            "before_revision": self.before_revision,
            "after_revision": self.after_revision,
            "placement_before": copy.deepcopy(self.placement_before),
            "disposed_at": self.disposed_at,
        }

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key == "item_id":
            return self.object_id
        return data[key]

    def get(self, key: str, default: Any = None) -> Any:
        if key == "item_id":
            return self.object_id
        return self.to_dict().get(key, default)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DisposalReceipt":
        if not isinstance(raw, Mapping):
            raise DisposalContractError(
                "DisposalReceipt payload must be a JSON object",
                code="CORRUPT_RECEIPT",
                status_code=500,
            )
        if raw.get("schema_version") != SCHEMA_DISPOSAL_RECEIPT:
            raise DisposalContractError(
                f"Unsupported receipt schema_version: {raw.get('schema_version')!r}",
                code="UNSUPPORTED_RECEIPT_SCHEMA",
                status_code=500,
            )
        if raw.get("kind") != KIND_DISPOSAL_RECEIPT:
            raise DisposalContractError(
                f"Unsupported receipt kind: {raw.get('kind')!r}",
                code="UNSUPPORTED_RECEIPT_KIND",
                status_code=500,
            )
        receipt_id = _require_safe_id(raw.get("receipt_id"), "receipt_id")
        ring_seq = _require_strict_int(raw.get("ring_seq"), "ring_seq", minimum=1, maximum=10_000_000_000)
        operation_key = _require_safe_id(raw.get("operation_key"), "operation_key")
        object_id = _require_safe_id(raw.get("object_id"), "object_id")
        object_kind = _require_non_empty_str(raw.get("object_kind", "item"), "object_kind", max_len=32).lower()
        if object_kind not in OBJECT_KINDS:
            raise DisposalContractError(
                f"Unsupported object_kind: {object_kind!r}",
                code="INVALID_OBJECT_KIND",
                status_code=400,
            )
        disposal_kind = _require_non_empty_str(raw.get("disposal_kind"), "disposal_kind", max_len=32).upper()
        if disposal_kind not in DISPOSAL_KINDS:
            raise DisposalContractError(
                f"Unsupported disposal_kind: {disposal_kind!r}",
                code="INVALID_DISPOSAL_KIND",
                status_code=400,
            )
        actor_id = _require_safe_id(raw.get("actor_id"), "actor_id")
        body_raw = raw.get("body_id")
        body_id = _require_safe_id(body_raw, "body_id") if body_raw is not None else None
        quantity_disposed = _require_strict_int(raw.get("quantity_disposed"), "quantity_disposed", minimum=1)
        remaining_quantity = _require_strict_int(raw.get("remaining_quantity", 0), "remaining_quantity", minimum=0)
        terminal_raw = raw.get("terminal")
        if not isinstance(terminal_raw, bool):
            raise DisposalContractError("terminal must be bool", code="INVALID_FIELD", status_code=400)
        terminal = bool(terminal_raw or remaining_quantity == 0)
        unit = _require_non_empty_str(raw.get("unit", "servings"), "unit", max_len=32)
        reason = _require_non_empty_str(raw.get("reason", "disposed"), "reason", max_len=240)
        energy_credit = _require_strict_int(raw.get("energy_credit", 0), "energy_credit", minimum=0)
        counts_raw = raw.get("counts_as_nutrition", False)
        if not isinstance(counts_raw, bool):
            raise DisposalContractError("counts_as_nutrition must be bool", code="INVALID_FIELD", status_code=400)
        if disposal_kind in NON_NUTRITIONAL_DISPOSAL_KINDS and (energy_credit != 0 or counts_raw):
            raise DisposalContractError(
                f"{disposal_kind} receipt must have energy_credit=0 and counts_as_nutrition=False (GAP-08)",
                code="DISCARD_ENERGY_FORBIDDEN",
                status_code=400,
            )
        before_rev = _require_strict_int(raw.get("before_revision", 0), "before_revision", minimum=0, maximum=10_000_000_000)
        after_rev = _require_strict_int(raw.get("after_revision", before_rev), "after_revision", minimum=before_rev, maximum=10_000_000_000)
        placement_raw = raw.get("placement_before")
        if placement_raw is not None and not isinstance(placement_raw, Mapping):
            raise DisposalContractError("placement_before must be dict or None", code="INVALID_FIELD", status_code=400)
        placement_before = copy.deepcopy(dict(placement_raw)) if isinstance(placement_raw, Mapping) else None
        disposed_at = _normalize_iso(raw.get("disposed_at"), "disposed_at")
        expected_fp = compute_disposal_fingerprint(
            object_id=object_id,
            object_kind=object_kind,
            disposal_kind=disposal_kind,
            actor_id=actor_id,
            quantity_disposed=quantity_disposed,
            remaining_quantity=remaining_quantity,
            unit=unit,
            reason=reason,
            energy_credit=energy_credit,
        )
        stored_fp = _require_non_empty_str(raw.get("proposal_fingerprint") or expected_fp, "proposal_fingerprint", max_len=128)
        if stored_fp != expected_fp:
            raise DisposalContractError(
                "DisposalReceipt proposal_fingerprint integrity check failed",
                code="RECEIPT_FINGERPRINT_TAMPERED",
                status_code=500,
            )
        return cls(
            schema_version=SCHEMA_DISPOSAL_RECEIPT,
            kind=KIND_DISPOSAL_RECEIPT,
            receipt_id=receipt_id,
            ring_seq=ring_seq,
            operation_key=operation_key,
            proposal_fingerprint=stored_fp,
            object_id=object_id,
            object_kind=object_kind,
            disposal_kind=disposal_kind,
            actor_id=actor_id,
            body_id=body_id,
            quantity_disposed=quantity_disposed,
            remaining_quantity=remaining_quantity,
            terminal=terminal,
            unit=unit,
            reason=reason,
            energy_credit=energy_credit,
            counts_as_nutrition=counts_raw,
            before_revision=before_rev,
            after_revision=after_rev,
            placement_before=placement_before,
            disposed_at=disposed_at,
        )


def build_disposal_receipt(
    *,
    operation_key: str,
    object_id: str,
    disposal_kind: str = DISPOSAL_KIND_DISCARD,
    object_kind: str = "food",
    actor_id: str = "chiyo",
    body_id: Optional[str] = None,
    quantity_disposed: int = 1,
    remaining_quantity: int = 0,
    terminal: Optional[bool] = None,
    unit: str = "servings",
    reason: str = "disposed",
    energy_credit: int = 0,
    before_revision: int = 0,
    after_revision: Optional[int] = None,
    placement_before: Optional[Mapping[str, Any]] = None,
    disposed_at: Any = None,
    ring_seq: int = 1,
) -> DisposalReceipt:
    """Validate inputs and construct a canonical ``DisposalReceipt``."""
    op_key = _require_safe_id(operation_key, "operation_key")
    obj_id = _require_safe_id(object_id, "object_id")
    kind_norm = _require_non_empty_str(disposal_kind, "disposal_kind", max_len=32).upper()
    if kind_norm not in DISPOSAL_KINDS:
        raise DisposalContractError(
            f"Unsupported disposal_kind: {disposal_kind!r}; expected one of {DISPOSAL_KINDS}",
            code="INVALID_DISPOSAL_KIND",
            status_code=400,
        )
    obj_kind_norm = _require_non_empty_str(object_kind, "object_kind", max_len=32).lower()
    if obj_kind_norm not in OBJECT_KINDS:
        raise DisposalContractError(
            f"Unsupported object_kind: {object_kind!r}; expected one of {sorted(OBJECT_KINDS)}",
            code="INVALID_OBJECT_KIND",
            status_code=400,
        )
    actor_norm = _require_safe_id(actor_id, "actor_id")
    body_norm = _require_safe_id(body_id, "body_id") if body_id is not None else None
    qty = _require_strict_int(quantity_disposed, "quantity_disposed", minimum=1)
    rem = _require_strict_int(remaining_quantity, "remaining_quantity", minimum=0)
    seq = _require_strict_int(ring_seq, "ring_seq", minimum=1, maximum=10_000_000_000)
    unit_norm = _require_non_empty_str(unit, "unit", max_len=32)
    reason_norm = _require_non_empty_str(reason, "reason", max_len=240)
    energy = _require_strict_int(energy_credit, "energy_credit", minimum=0)
    if kind_norm in NON_NUTRITIONAL_DISPOSAL_KINDS and energy != 0:
        raise DisposalContractError(
            f"{kind_norm} cannot grant energy_credit (got {energy}); GAP-08 requires 0",
            code="DISCARD_ENERGY_FORBIDDEN",
            status_code=400,
        )
    counts_nutrition = bool(kind_norm == DISPOSAL_KIND_CONSUME and obj_kind_norm in {"food", "consumable"})
    before_rev = _require_strict_int(before_revision, "before_revision", minimum=0, maximum=10_000_000_000)
    after_rev = (
        before_rev + 1
        if after_revision is None
        else _require_strict_int(after_revision, "after_revision", minimum=before_rev, maximum=10_000_000_000)
    )
    is_terminal = bool(rem == 0) if terminal is None else bool(terminal or rem == 0)
    if placement_before is not None and not isinstance(placement_before, Mapping):
        raise DisposalContractError("placement_before must be a mapping or None", code="INVALID_FIELD", status_code=400)
    ts_iso = _normalize_iso(disposed_at, "disposed_at")
    fp = compute_disposal_fingerprint(
        object_id=obj_id,
        object_kind=obj_kind_norm,
        disposal_kind=kind_norm,
        actor_id=actor_norm,
        quantity_disposed=qty,
        remaining_quantity=rem,
        unit=unit_norm,
        reason=reason_norm,
        energy_credit=energy,
    )
    return DisposalReceipt(
        schema_version=SCHEMA_DISPOSAL_RECEIPT,
        kind=KIND_DISPOSAL_RECEIPT,
        receipt_id=_deterministic_receipt_id(op_key, obj_id),
        ring_seq=seq,
        operation_key=op_key,
        proposal_fingerprint=fp,
        object_id=obj_id,
        object_kind=obj_kind_norm,
        disposal_kind=kind_norm,
        actor_id=actor_norm,
        body_id=body_norm,
        quantity_disposed=qty,
        remaining_quantity=rem,
        terminal=is_terminal,
        unit=unit_norm,
        reason=reason_norm,
        energy_credit=energy,
        counts_as_nutrition=counts_nutrition,
        before_revision=before_rev,
        after_revision=after_rev,
        placement_before=copy.deepcopy(dict(placement_before)) if isinstance(placement_before, Mapping) else None,
        disposed_at=ts_iso,
    )


def verify_body_ingest(receipt_or_dict: DisposalReceipt | Mapping[str, Any]) -> tuple[bool, Optional[str]]:
    """GAP-08 consumer gate: Body energy/satiety ingestion rejects all non-CONSUME receipts."""
    receipt = (
        receipt_or_dict
        if isinstance(receipt_or_dict, DisposalReceipt)
        else DisposalReceipt.from_dict(receipt_or_dict)
    )
    if receipt.disposal_kind in NON_NUTRITIONAL_DISPOSAL_KINDS:
        return False, "DISCARD_NOT_INGESTIBLE"
    if not receipt.counts_as_nutrition:
        return False, "NON_NUTRITIONAL_OBJECT"
    return True, None


def body_ingest_allowed(receipt_or_dict: DisposalReceipt | Mapping[str, Any]) -> bool:
    allowed, _reason = verify_body_ingest(receipt_or_dict)
    return allowed


class DisposalReceiptRing:
    """In-memory & JSON-serializable 256-entry ring buffer for idempotent anti-resurrection receipts."""

    def __init__(
        self,
        *,
        capacity: int = MAX_DISPOSAL_RECEIPTS,
        revision: int = 0,
        next_ring_seq: int = 1,
        evicted_receipt_count: int = 0,
        evicted_tombstone_count: int = 0,
        total_disposed_quantity: int = 0,
        receipts: Optional[Iterable[DisposalReceipt]] = None,
        terminal_tombstones: Optional[Iterable[Mapping[str, Any]]] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        cap = _require_strict_int(capacity, "capacity", minimum=1, maximum=MAX_DISPOSAL_RECEIPTS)
        self.capacity: int = cap
        self.revision: int = _require_strict_int(revision, "revision", minimum=0, maximum=10_000_000_000)
        self.next_ring_seq: int = _require_strict_int(next_ring_seq, "next_ring_seq", minimum=1, maximum=10_000_000_000)
        self.evicted_receipt_count: int = _require_strict_int(
            evicted_receipt_count, "evicted_receipt_count", minimum=0, maximum=10_000_000_000
        )
        self.evicted_tombstone_count: int = _require_strict_int(
            evicted_tombstone_count, "evicted_tombstone_count", minimum=0, maximum=10_000_000_000
        )
        self.total_disposed_quantity: int = _require_strict_int(
            total_disposed_quantity, "total_disposed_quantity", minimum=0, maximum=10_000_000_000
        )
        self._receipts: list[DisposalReceipt] = list(receipts or [])[-self.capacity :]
        self._tombstones: list[dict[str, Any]] = [
            dict(entry) for entry in list(terminal_tombstones or [])[-self.capacity :]
        ]
        self.updated_at: Optional[str] = _normalize_iso(updated_at) if updated_at is not None else None
        if self._receipts:
            max_seq = max(r.ring_seq for r in self._receipts)
            if self.next_ring_seq <= max_seq:
                self.next_ring_seq = max_seq + 1

    @property
    def receipts(self) -> tuple[DisposalReceipt, ...]:
        return tuple(self._receipts)

    @property
    def terminal_tombstones(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._tombstones))

    def __len__(self) -> int:
        return len(self._receipts)

    def find_by_operation_key(self, operation_key: str) -> Optional[DisposalReceipt]:
        op_key = _require_safe_id(operation_key, "operation_key")
        for receipt in reversed(self._receipts):
            if receipt.operation_key == op_key:
                return receipt
        return None

    def find_latest_for_object(self, object_id: str) -> Optional[DisposalReceipt]:
        obj_id = _require_safe_id(object_id, "object_id")
        for receipt in reversed(self._receipts):
            if receipt.object_id == obj_id:
                return receipt
        return None

    def is_terminally_disposed(self, object_id: str) -> bool:
        obj_id = _require_safe_id(object_id, "object_id")
        for tb in reversed(self._tombstones):
            if tb.get("object_id") == obj_id:
                return True
        for receipt in reversed(self._receipts):
            if receipt.object_id == obj_id and receipt.terminal:
                return True
        return False

    def assert_not_resurrected(self, object_id: str) -> None:
        """Fail closed (409) if ``object_id`` has already been terminally disposed/consumed."""
        obj_id = _require_safe_id(object_id, "object_id")
        if self.is_terminally_disposed(obj_id):
            raise DisposalContractError(
                f"Object {obj_id!r} was terminally disposed and cannot be resurrected",
                code="OBJECT_RESURRECTION_FORBIDDEN",
                status_code=409,
                details={"object_id": obj_id},
            )

    def filter_live_object_ids(self, candidate_object_ids: Iterable[str]) -> list[str]:
        """Filter out any object_id covered by a terminal disposal tombstone in the ring."""
        dead = {str(tb.get("object_id") or "") for tb in self._tombstones}
        dead.update(r.object_id for r in self._receipts if r.terminal)
        return [oid for oid in candidate_object_ids if oid not in dead]

    def record(
        self,
        *,
        operation_key: str,
        object_id: str,
        disposal_kind: str = DISPOSAL_KIND_DISCARD,
        object_kind: str = "food",
        actor_id: str = "chiyo",
        body_id: Optional[str] = None,
        quantity_disposed: int = 1,
        remaining_quantity: int = 0,
        terminal: Optional[bool] = None,
        unit: str = "servings",
        reason: str = "disposed",
        energy_credit: int = 0,
        placement_before: Optional[Mapping[str, Any]] = None,
        disposed_at: Any = None,
    ) -> tuple[DisposalReceipt, str]:
        """Record or deduplicate a disposal/consumption operation.

        Returns ``(receipt, outcome)`` where ``outcome`` is ``"APPLIED"`` or ``"DUPLICATE"``.
        """
        candidate = build_disposal_receipt(
            operation_key=operation_key,
            object_id=object_id,
            disposal_kind=disposal_kind,
            object_kind=object_kind,
            actor_id=actor_id,
            body_id=body_id,
            quantity_disposed=quantity_disposed,
            remaining_quantity=remaining_quantity,
            terminal=terminal,
            unit=unit,
            reason=reason,
            energy_credit=energy_credit,
            before_revision=self.revision,
            after_revision=self.revision + 1,
            placement_before=placement_before,
            disposed_at=disposed_at,
            ring_seq=self.next_ring_seq,
        )

        # 1. Operation-key idempotency check BEFORE terminal anti-resurrection check
        existing_op = self.find_by_operation_key(candidate.operation_key)
        if existing_op is not None:
            if existing_op.proposal_fingerprint != candidate.proposal_fingerprint:
                raise DisposalContractError(
                    f"operation_key {candidate.operation_key!r} replayed with altered disposal parameters",
                    code="OPERATION_FINGERPRINT_MISMATCH",
                    status_code=409,
                    details={
                        "operation_key": candidate.operation_key,
                        "existing_receipt_id": existing_op.receipt_id,
                    },
                )
            return existing_op, "DUPLICATE"

        # 2. Terminal anti-resurrection check for any new operation_key
        if self.is_terminally_disposed(candidate.object_id):
            raise DisposalContractError(
                f"Object {candidate.object_id!r} is already terminally disposed/consumed",
                code="ALREADY_DISPOSED",
                status_code=409,
                details={"object_id": candidate.object_id},
            )

        # 3. Monotonic portion conservation check against latest non-terminal receipt
        prior_obj = self.find_latest_for_object(candidate.object_id)
        if prior_obj is not None and not prior_obj.terminal:
            max_allowed_remaining = prior_obj.remaining_quantity - candidate.quantity_disposed
            if max_allowed_remaining < 0:
                raise DisposalContractError(
                    f"Cannot dispose {candidate.quantity_disposed} {candidate.unit} from "
                    f"{candidate.object_id!r}; only {prior_obj.remaining_quantity} remaining",
                    code="INSUFFICIENT_REMAINING_PORTIONS",
                    status_code=409,
                    details={
                        "object_id": candidate.object_id,
                        "available_remaining": prior_obj.remaining_quantity,
                        "requested_quantity": candidate.quantity_disposed,
                    },
                )
            if candidate.remaining_quantity > max_allowed_remaining:
                raise DisposalContractError(
                    f"Portion resurrection detected on {candidate.object_id!r}: "
                    f"remaining_quantity={candidate.remaining_quantity} > {max_allowed_remaining}",
                    code="PORTION_RESURRECTION_FORBIDDEN",
                    status_code=409,
                    details={
                        "object_id": candidate.object_id,
                        "previous_remaining": prior_obj.remaining_quantity,
                        "quantity_disposed": candidate.quantity_disposed,
                        "claimed_remaining": candidate.remaining_quantity,
                    },
                )

        # 4. Commit into 256-entry FIFO ring
        self._receipts.append(candidate)
        if len(self._receipts) > self.capacity:
            overflow = len(self._receipts) - self.capacity
            del self._receipts[:overflow]
            self.evicted_receipt_count += overflow

        if candidate.terminal:
            self._tombstones = [
                tb for tb in self._tombstones if tb.get("object_id") != candidate.object_id
            ]
            self._tombstones.append(
                {
                    "object_id": candidate.object_id,
                    "object_kind": candidate.object_kind,
                    "disposal_kind": candidate.disposal_kind,
                    "receipt_id": candidate.receipt_id,
                    "operation_key": candidate.operation_key,
                    "ring_seq": candidate.ring_seq,
                    "disposed_at": candidate.disposed_at,
                }
            )
            if len(self._tombstones) > self.capacity:
                tb_overflow = len(self._tombstones) - self.capacity
                del self._tombstones[:tb_overflow]
                self.evicted_tombstone_count += tb_overflow

        self.revision += 1
        self.next_ring_seq += 1
        self.total_disposed_quantity += candidate.quantity_disposed
        self.updated_at = candidate.disposed_at
        return candidate, "APPLIED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_DISPOSAL_LEDGER,
            "kind": KIND_DISPOSAL_LEDGER,
            "ring_capacity": self.capacity,
            "revision": self.revision,
            "next_ring_seq": self.next_ring_seq,
            "evicted_receipt_count": self.evicted_receipt_count,
            "evicted_tombstone_count": self.evicted_tombstone_count,
            "total_disposed_quantity": self.total_disposed_quantity,
            "updated_at": self.updated_at,
            "receipts": [r.to_dict() for r in self._receipts],
            "terminal_tombstones": copy.deepcopy(self._tombstones),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DisposalReceiptRing":
        if not isinstance(raw, Mapping):
            raise DisposalContractError(
                "Disposal ledger document must be a JSON object",
                code="CORRUPT_DISPOSAL_LEDGER",
                status_code=500,
            )
        if raw.get("schema_version") != SCHEMA_DISPOSAL_LEDGER:
            raise DisposalContractError(
                f"Unsupported disposal ledger schema: {raw.get('schema_version')!r}",
                code="UNSUPPORTED_LEDGER_SCHEMA",
                status_code=500,
            )
        if raw.get("kind") != KIND_DISPOSAL_LEDGER:
            raise DisposalContractError(
                f"Unsupported disposal ledger kind: {raw.get('kind')!r}",
                code="UNSUPPORTED_LEDGER_KIND",
                status_code=500,
            )
        cap = _require_strict_int(raw.get("ring_capacity", MAX_DISPOSAL_RECEIPTS), "ring_capacity", minimum=1, maximum=MAX_DISPOSAL_RECEIPTS)
        raw_receipts = raw.get("receipts")
        if not isinstance(raw_receipts, list) or len(raw_receipts) > cap:
            raise DisposalContractError(
                "Disposal ledger receipts must be a list within ring_capacity",
                code="CORRUPT_DISPOSAL_LEDGER",
                status_code=500,
            )
        parsed_receipts = [DisposalReceipt.from_dict(item) for item in raw_receipts]
        raw_tombstones = raw.get("terminal_tombstones", [])
        if not isinstance(raw_tombstones, list) or len(raw_tombstones) > cap:
            raise DisposalContractError(
                "Disposal ledger terminal_tombstones must be a list within ring_capacity",
                code="CORRUPT_DISPOSAL_LEDGER",
                status_code=500,
            )
        tombstones: list[dict[str, Any]] = []
        for entry in raw_tombstones:
            if not isinstance(entry, Mapping):
                raise DisposalContractError("Invalid tombstone entry", code="CORRUPT_DISPOSAL_LEDGER", status_code=500)
            tombstones.append(
                {
                    "object_id": _require_safe_id(entry.get("object_id"), "tombstone.object_id"),
                    "object_kind": _require_non_empty_str(entry.get("object_kind", "item"), "tombstone.object_kind", max_len=32),
                    "disposal_kind": _require_non_empty_str(entry.get("disposal_kind", DISPOSAL_KIND_DISCARD), "tombstone.disposal_kind", max_len=32),
                    "receipt_id": _require_safe_id(entry.get("receipt_id"), "tombstone.receipt_id"),
                    "operation_key": _require_safe_id(entry.get("operation_key"), "tombstone.operation_key"),
                    "ring_seq": _require_strict_int(entry.get("ring_seq", 1), "tombstone.ring_seq", minimum=1, maximum=10_000_000_000),
                    "disposed_at": _normalize_iso(entry.get("disposed_at"), "tombstone.disposed_at"),
                }
            )
        return cls(
            capacity=cap,
            revision=raw.get("revision", 0),
            next_ring_seq=raw.get("next_ring_seq", 1),
            evicted_receipt_count=raw.get("evicted_receipt_count", 0),
            evicted_tombstone_count=raw.get("evicted_tombstone_count", 0),
            total_disposed_quantity=raw.get("total_disposed_quantity", 0),
            receipts=parsed_receipts,
            terminal_tombstones=tombstones,
            updated_at=raw.get("updated_at"),
        )


def disposal_ledger_path(root: Path | str) -> Path:
    """Return canonical ledger path ``<root>/state/world_disposal_receipts.json`` without touching disk."""
    base = Path(root)
    return base / DEFAULT_LEDGER_RELPATH


def read_disposal_ledger_zero_write(
    root: Path | str,
    *,
    ledger_path: Optional[Path | str] = None,
) -> tuple[DisposalReceiptRing, Optional[str]]:
    """Pure zero-write read of the disposal ring ledger.

    Returns ``(ring, error_reason)``. Never creates directories or files.
    """
    target = Path(ledger_path) if ledger_path is not None else disposal_ledger_path(root)
    try:
        if target.is_symlink():
            return DisposalReceiptRing(), "SYMLINK_REJECTED"
        if not target.exists() or not target.is_file():
            return DisposalReceiptRing(), None
        raw = json.loads(target.read_text(encoding="utf-8"))
        return DisposalReceiptRing.from_dict(raw), None
    except DisposalContractError as exc:
        return DisposalReceiptRing(), exc.code
    except (OSError, ValueError):
        return DisposalReceiptRing(), "CORRUPT_DISPOSAL_LEDGER"


def _atomic_write_ledger(path: Path, ring: DisposalReceiptRing) -> None:
    if path.exists() and path.is_symlink():
        raise DisposalContractError(
            "Refusing to write disposal ledger through symlink",
            code="SYMLINK_REJECTED",
            status_code=500,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(ring.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def record_disposal(
    root: Path | str,
    *,
    operation_key: str,
    object_id: str,
    disposal_kind: str = DISPOSAL_KIND_DISCARD,
    object_kind: str = "food",
    actor_id: str = "chiyo",
    body_id: Optional[str] = None,
    quantity_disposed: int = 1,
    remaining_quantity: int = 0,
    terminal: Optional[bool] = None,
    unit: str = "servings",
    reason: str = "disposed",
    energy_credit: int = 0,
    placement_before: Optional[Mapping[str, Any]] = None,
    disposed_at: Any = None,
    ledger_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Persistently record & deduplicate one disposal/consumption operation in the 256-entry ring."""
    target = Path(ledger_path) if ledger_path is not None else disposal_ledger_path(root)
    with _process_lock_for(target):
        if target.exists():
            if target.is_symlink():
                raise DisposalContractError(
                    "Disposal ledger path is a symlink",
                    code="SYMLINK_REJECTED",
                    status_code=500,
                )
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DisposalContractError(
                    "Disposal ledger file is unreadable or corrupt JSON",
                    code="CORRUPT_DISPOSAL_LEDGER",
                    status_code=500,
                ) from exc
            ring = DisposalReceiptRing.from_dict(raw)
        else:
            ring = DisposalReceiptRing()

        receipt, outcome = ring.record(
            operation_key=operation_key,
            object_id=object_id,
            disposal_kind=disposal_kind,
            object_kind=object_kind,
            actor_id=actor_id,
            body_id=body_id,
            quantity_disposed=quantity_disposed,
            remaining_quantity=remaining_quantity,
            terminal=terminal,
            unit=unit,
            reason=reason,
            energy_credit=energy_credit,
            placement_before=placement_before,
            disposed_at=disposed_at,
        )
        if outcome == "APPLIED":
            _atomic_write_ledger(target, ring)
        return {
            "status": outcome,
            "receipt": receipt.to_dict(),
            "receipt_obj": receipt,
            "ring_size": len(ring),
            "ring_capacity": ring.capacity,
            "evicted_receipt_count": ring.evicted_receipt_count,
            "revision": ring.revision,
        }


def verify_not_disposed(
    root: Path | str,
    object_id: str,
    *,
    ledger_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Pure zero-write anti-resurrection check against the persistent 256-entry ring."""
    ring, err = read_disposal_ledger_zero_write(root, ledger_path=ledger_path)
    if err is not None:
        raise DisposalContractError(
            f"Cannot verify anti-resurrection state: {err}",
            code=err,
            status_code=500,
        )
    ring.assert_not_resurrected(object_id)
    latest = ring.find_latest_for_object(object_id)
    return {
        "ok": True,
        "object_id": object_id,
        "terminally_disposed": False,
        "latest_receipt": latest.to_dict() if latest is not None else None,
    }


__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "DISPOSAL_KINDS",
    "DISPOSAL_KIND_CLEANUP_RESIDUE",
    "DISPOSAL_KIND_CONSUME",
    "DISPOSAL_KIND_DISCARD",
    "DISPOSAL_KIND_SPOILED_DISCARD",
    "DisposalContractError",
    "DisposalReceipt",
    "DisposalReceiptRing",
    "KIND_DISPOSAL_LEDGER",
    "KIND_DISPOSAL_RECEIPT",
    "LEDGER_FILENAME",
    "MAX_DISPOSAL_RECEIPTS",
    "NON_NUTRITIONAL_DISPOSAL_KINDS",
    "OBJECT_KINDS",
    "RING_CAPACITY",
    "SCHEMA_DISPOSAL_LEDGER",
    "SCHEMA_DISPOSAL_RECEIPT",
    "body_ingest_allowed",
    "build_disposal_receipt",
    "compute_disposal_fingerprint",
    "disposal_ledger_path",
    "read_disposal_ledger_zero_write",
    "record_disposal",
    "verify_body_ingest",
    "verify_not_disposed",
]
