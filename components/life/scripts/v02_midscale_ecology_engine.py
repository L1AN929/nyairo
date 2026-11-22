#!/usr/bin/env python3
"""v0.2 Wave B1 Mid-Scale Ecology Engine (F-175 / F-176 / F-177 / F-178).

Deterministic, zero-LLM (``llm_calls == 0``) domain engine covering:
1. F-175 Environmental Expectation & Absence Observation:
   - Snapshots real room objects/placements from ``world/world_state.json``,
     ``phone/home_hearth_reunion_anchor.json`` (and ``phone/reunion_anchor.json``),
     ``world/v04_neighborhood_state.json``, plus optional explicit ``items``.
   - Zero-write live comparison detecting ``MISSING_OBJECT``, ``MOVED_OBJECT``,
     and ``SHOP_CLOSED_UNEXPECTED`` without ever guessing who moved an item
     (``who_moved == "UNKNOWN"`` unless explicitly acknowledged).
   - Partner-safe ``acknowledge_absence_observation`` overlaying a partner note
     without erasing the observation.
2. F-176 Autobiographical Episodes -> New Life Opportunities:
   - Pure zero-write derivation from ``state/v02_autobiography_skill_provenance.json``
     into up to 6 gentle, zero-debt opportunities (never fabricating memories on
     empty roots, strictly hiding private episodes from partner view).
3. F-177 Mid-Scale Activity Ecology Chains:
   - Subject-authored multi-step organic chains
     (``reading / inquiry / craft / palette / outing / share / gift``) with
     step-aware ``suggested_next_step_cn``.
   - Partner-safe ``suggest_ecology_spark`` (``status="OPTIONAL_SPARK"``,
     ``zero_debt=True``, non-coercive).
4. F-178 Practice-Driven Evaluation Standard Evolution:
   - Explicitly separates evaluation standard shifts ("眼光/标准变了") from
     mere success-rate gains ("成功率提高了").
   - Zero-write detection of practice-driven standard proposals from real
     ``state/v02_autobiography_skill_provenance.json`` (releases with ``trials >= 3``
     or ``method_patches``) and ``phone/v02_crossmodal.json`` (``palette_studies >= 2``).

Invariants:
- All read/detect/derive/evaluate functions are 100% zero-write (zero dirs,
  zero files, zero locks, zero mtime changes on empty or populated roots).
- Mutations use ``state_tx_guard`` atomic writes and file locks with zero-write
  preflight validation and ``operation_key`` replay/conflict checks BEFORE
  acquiring the lock.
- Pure LF, UTF-8 without BOM, zero emojis, neutral ``"TA"``, ``llm_calls == 0``.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from components.life.scripts import state_tx_guard as _tx_guard
except ImportError:  # pragma: no cover - flat life/scripts import path
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]


SCHEMA_VERSION = "nyairo.v02.midscale_ecology.v1"
VIEW_SCHEMA = "nyairo.v02.ecology_growth.v1"
STATE_REL_PATH = ("state", "v02_midscale_ecology.json")
LOCK_REL_PATH = (".locks", "v02_midscale_ecology.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

ABSENCE_KINDS: frozenset[str] = frozenset({
    "MISSING_OBJECT",
    "MOVED_OBJECT",
    "SHOP_CLOSED_UNEXPECTED",
})

ACKNOWLEDGED_WHO_MOVED_VALUES: frozenset[str] = frozenset({
    "UNKNOWN",
    "PARTNER",
    "SUBJECT",
})

ECOLOGY_STEP_KINDS: frozenset[str] = frozenset({
    "reading",
    "inquiry",
    "craft",
    "palette",
    "outing",
    "share",
    "gift",
})

PARTNER_SPARK_LINKED_KINDS: frozenset[str] = frozenset({
    "reading",
    "inquiry",
    "craft",
    "outing",
    "share",
})

SUGGESTED_NEXT_STEP_BY_KIND: dict[str, str] = {
    "reading": "顺着刚才读到的细节，可以起个小疑问或随手试一版配色",
    "inquiry": "查出头绪后，适合在书桌草稿里记一小段",
    "craft": "做顺手了，改天有兴致可以摆进展示角或挑一份分享",
    "palette": "试出顺眼的配色后，可以顺手带进下一次手作或小记里",
    "outing": "散步路上留下的印象，回屋后可以翻翻书或添进手账小段",
    "share": "聊完这段后先留点空隙，有新念头再慢慢接着长",
    "gift": "收下或送出这份小物件后，可以顺手在桌面留个小位置",
}

FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "has_subject_capability",
    "capability_token",
    "subject_id",
    "grant_id",
    "private_override",
    "intimacy_score",
    "affection_score",
    "love_points",
    "obedience_score",
    "debt_score",
    "compliance_score",
    "guilt_score",
    "auto_locked",
    "force_adopt",
    "forced_adoption",
    "__proto__",
    "constructor",
    "prototype",
})

_HOST_FS_PATH_RE = re.compile(
    r"(?:"
    r"(?:^|(?<=[\s\"'=]))/(?:root|mnt|home|Users|tmp|var|opt|etc|private|srv)(?:/[^\s\"']*)?"
    r"|\b[A-Za-z]:[\\/][^\s\"']*"
    r"|\bfile:///[^\s\"']+"
    r"|\\\\[A-Za-z0-9_.-]+\\[^\s\"']*"
    r")"
)
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_EPISODE_OUTCOME_PRIORITY: dict[str, int] = {
    "paused": 0,
    "failed": 1,
    "abandoned": 2,
    "partial": 3,
    "ongoing": 4,
    "succeeded": 5,
}

MAX_AUTOBIOGRAPHY_OPPORTUNITIES = 6
MAX_ABSENCE_OBSERVATIONS = 80
MAX_ECOLOGY_CHAINS = 40
MAX_LINKS_PER_CHAIN = 24
MAX_PARTNER_SPARKS = 40
MAX_STANDARD_EVOLUTIONS = 60

_PLACE_LABELS_CN: dict[str, str] = {
    "home": "家里",
    "home_desk": "书桌",
    "home_staging_table": "暂放台",
    "home_rack": "室内晾衣架",
    "porch": "门廊",
    "porch_hook": "门廊檐下挂钩",
    "porch_return_box": "门廊归还箱",
    "porch_message_board": "门廊留言板",
    "yard": "小院",
    "yard_line": "小院晾晒绳",
    "yard_bench": "院里木长椅",
    "street_corner": "坡道街角",
    "shared_shelf": "街角共享架",
    "bookstore": "街角旧书店",
    "riverside": "河边长椅步道",
    "carried": "随身携带",
    "hand_bag": "随身帆布袋",
}

_CLOSED_STATUS_TOKENS: frozenset[str] = frozenset({
    "closed",
    "temporarily_closed",
    "closed_unexpected",
    "closed_unexpectedly",
    "shut",
    "unavailable",
})

_ABSENT_STATUS_TOKENS: frozenset[str] = frozenset({
    "missing",
    "removed",
    "absent",
    "gone",
    "deleted",
    "disposed",
})


class V02MidscaleEcologyError(Exception):
    """Structured domain error with HTTP status_code and machine reason_code."""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"[{self.status_code} {self.reason_code}] {self.message}")


class V02ValidationError(V02MidscaleEcologyError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V02PermissionError(V02MidscaleEcologyError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V02NotFoundError(V02MidscaleEcologyError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V02ConflictError(V02MidscaleEcologyError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(409, reason_code, message)


# ---------------------------------------------------------------------------
# Helpers: time, sanitization, validation, zero-write I/O, atomic locked tx
# ---------------------------------------------------------------------------


def _parse_iso(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_TZ)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _now_iso(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        dt = _parse_iso(now_iso)
        if dt is None:
            raise V02ValidationError("INVALID_FIELDS", f"Invalid ISO timestamp: {now_iso!r}")
        return dt.isoformat(timespec="seconds")
    if now_iso is not None:
        raise V02ValidationError("INVALID_FIELDS", "now_iso must be an ISO timestamp string")
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def _safe_token(value: Any, fallback: str = "item") -> str:
    cleaned = _SAFE_TOKEN_RE.sub("_", str(value or "").strip()).strip("_.-")
    return (cleaned or fallback)[:64]


def _sanitize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _HOST_FS_PATH_RE.sub("[local_ref]", text)
    return cleaned


def _strip_forbidden_and_paths(node: Any) -> Any:
    if isinstance(node, Mapping):
        out: dict[str, Any] = {}
        for k, v in node.items():
            ks = str(k)
            if ks in FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS or ks.lower() in FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS:
                continue
            out[ks] = _strip_forbidden_and_paths(v)
        return out
    if isinstance(node, list):
        return [_strip_forbidden_and_paths(item) for item in node]
    if isinstance(node, tuple):
        return [_strip_forbidden_and_paths(item) for item in node]
    if isinstance(node, str):
        return _sanitize_text(node)
    return node


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_clean_str(value: Any, field: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} must be a non-empty string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} must not be blank")
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} exceeds max length {max_len}")
    return _sanitize_text(trimmed)


def _optional_clean_str(value: Any, field: str, *, max_len: int = 360) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} must be a string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} exceeds max length {max_len}")
    return _sanitize_text(trimmed)


def _optional_bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} must be a boolean")
    return value


def _clean_str_list(value: Any, field: str, *, max_items: int = 32, max_len: int = 160) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} must be a list of strings")
    if len(value) > max_items:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field!r} exceeds max items {max_items}")
    out: list[str] = []
    for idx, raw in enumerate(value):
        cleaned = _require_clean_str(raw, f"{field}[{idx}]", max_len=max_len)
        if cleaned not in out:
            out.append(cleaned)
    return out


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _state_lock_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*LOCK_REL_PATH)


def build_default_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "next_op_seq": 0,
        "room_expectations": [],
        "room_expectations_captured_at": None,
        "absence_observations": [],
        "absence_acknowledgements": {},
        "ecology_chains": [],
        "partner_ecology_sparks": [],
        "practice_standard_evolutions": [],
        "operations": {},
        "committed_op_ring": [],
    }


def _read_optional_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Pure zero-write optional JSON reader (never creates dirs, files, or locks)."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return dict(doc) if isinstance(doc, Mapping) else None


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """Strict 100% zero-write state loader."""
    path = _state_file_path(day_root)
    if path.is_symlink():
        raise V02MidscaleEcologyError(503, "STATE_SYMLINK_REFUSED", "State file must not be a symlink")
    if not path.exists():
        return False, build_default_state()
    if not path.is_file():
        raise V02MidscaleEcologyError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V02MidscaleEcologyError(503, "CORRUPT_STATE_FILE", f"Corrupted state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        raise V02MidscaleEcologyError(503, "CORRUPT_STATE_FILE", "Invalid schema in midscale ecology state file")
    merged = build_default_state()
    for key in merged:
        if key in doc:
            merged[key] = copy.deepcopy(doc[key])
    return True, merged


def _write_state_atomic(day_root: Path | str, state: Mapping[str, Any]) -> None:
    """Atomic JSON write with strict pure LF endings (caller must hold state_file_lock)."""
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    _tx_guard.atomic_write_json(target, dict(state), sort_keys=True)
    raw = target.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    if not normalized.endswith(b"\n"):
        normalized += b"\n"
    if normalized != raw:
        target.write_bytes(normalized)


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{seq:04d}"


def _compute_payload_fingerprint(action: str, actor_side: str, payload: Mapping[str, Any]) -> str:
    filtered = {
        k: v
        for k, v in sorted(payload.items())
        if k not in ("operation_key", "expected_revision")
    }
    return _tx_guard.payload_fingerprint({
        "action": action,
        "actor_side": actor_side,
        "payload": filtered,
    })


def _run_locked_tx(
    day_root: Path | str,
    *,
    action_name: str,
    actor_side: str,
    payload: Mapping[str, Any],
    now_iso: Optional[str],
    mutator: Callable[[dict[str, Any], str], dict[str, Any]],
) -> dict[str, Any]:
    """Execute a locked read-modify-write transaction with 100% zero-write preflight.

    Preflight validates inputs, checks ``expected_revision`` and ``operation_key``
    replay/conflicts, and dry-runs ``mutator`` on an in-memory copy BEFORE acquiring
    ``state_file_lock``. Errors and idempotent replays therefore never create locks
    or directories on disk.
    """
    stamp = _now_iso(now_iso)
    op_key = _optional_clean_str(payload.get("operation_key"), "operation_key", max_len=128)
    raw_exp_rev = payload.get("expected_revision")
    expected_rev: Optional[int] = None
    if raw_exp_rev is not None:
        if not _is_strict_int(raw_exp_rev) or raw_exp_rev < 0:
            raise V02ValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")
        expected_rev = int(raw_exp_rev)

    fp = _compute_payload_fingerprint(action_name, actor_side, payload)

    # Zero-write preflight before acquiring any OS lock
    _pre_exists, pre_state = _load_state_readonly(day_root)
    if op_key:
        kind, existing = _tx_guard.reconcile_evicted_or_active_op(
            day_root,
            pre_state,
            op_key,
            expected_revision=expected_rev,
            payload_hash=fp,
        )
        if kind == "conflict":
            raise V02ConflictError(
                "OPERATION_KEY_CONFLICT",
                f"Conflicting payload for operation_key={op_key}",
            )
        if kind == "expired":
            raise V02ConflictError("RETRY_WINDOW_EXPIRED", "Operation key retry window expired")
        if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
            replay = copy.deepcopy(dict(existing.get("result") or existing))
            replay["command_status"] = "DUPLICATE"
            replay["idempotent_replay"] = True
            replay["llm_calls"] = 0
            return replay

    pre_rev = int(pre_state.get("revision") or 0)
    if expected_rev is not None and expected_rev != pre_rev:
        raise V02ConflictError(
            "REVISION_CONFLICT",
            f"expected_revision={expected_rev} does not match current revision={pre_rev}",
        )

    # Dry-run validation on a copy so invalid fields / 404s never touch disk or locks
    mutator(copy.deepcopy(pre_state), stamp)

    # Acquire exclusive lock and re-verify state for concurrency safety
    lock_target = _state_lock_path(day_root)
    with _tx_guard.state_file_lock(lock_target):
        _exists, state = _load_state_readonly(day_root)
        if op_key:
            kind, existing = _tx_guard.reconcile_evicted_or_active_op(
                day_root,
                state,
                op_key,
                expected_revision=expected_rev,
                payload_hash=fp,
            )
            if kind == "conflict":
                raise V02ConflictError(
                    "OPERATION_KEY_CONFLICT",
                    f"Conflicting payload for operation_key={op_key}",
                )
            if kind == "expired":
                raise V02ConflictError("RETRY_WINDOW_EXPIRED", "Operation key retry window expired")
            if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
                replay = copy.deepcopy(dict(existing.get("result") or existing))
                replay["command_status"] = "DUPLICATE"
                replay["idempotent_replay"] = True
                replay["llm_calls"] = 0
                return replay

        current_rev = int(state.get("revision") or 0)
        if expected_rev is not None and expected_rev != current_rev:
            raise V02ConflictError(
                "REVISION_CONFLICT",
                f"expected_revision={expected_rev} does not match current revision={current_rev}",
            )

        result = mutator(state, stamp)
        new_rev = current_rev + 1
        state["revision"] = new_rev
        state["updated_at"] = stamp

        if op_key:
            _tx_guard.stamp_receipt(
                state,
                op_key,
                {"status": "applied", "ok": True, "result": copy.deepcopy(result)},
                revision=new_rev,
                payload_hash=fp,
                committed_at=stamp,
            )
            _tx_guard.trim_receipts_by_op_seq(state, max_keep=200, max_ring=1024)

        _write_state_atomic(day_root, state)
        out = copy.deepcopy(result)
        out["revision"] = new_rev
        out["updated_at"] = stamp
        out["command_status"] = "APPLIED"
        out["idempotent_replay"] = False
        out["llm_calls"] = 0
        return out


# ---------------------------------------------------------------------------
# Feature 1 (F-175): Environmental Expectation & Absence Observation
# ---------------------------------------------------------------------------


def _human_place_label(raw: Any) -> str:
    if isinstance(raw, Mapping):
        for key in (
            "place_cn",
            "expected_place_cn",
            "location_cn",
            "spot_cn",
            "label_cn",
            "surface_cn",
            "surface_id",
            "spot_id",
            "area_id",
            "location_id",
            "place_id",
            "place",
        ):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                cleaned = val.strip()
                return _PLACE_LABELS_CN.get(cleaned, _sanitize_text(cleaned))
        return ""
    if isinstance(raw, str) and raw.strip():
        cleaned = raw.strip()
        return _PLACE_LABELS_CN.get(cleaned, _sanitize_text(cleaned))
    return ""


def _resolve_world_state_doc_zero_write(day_root: Path) -> tuple[bool, Optional[dict[str, Any]]]:
    candidates = (
        day_root / "world" / "world_state.json",
        day_root / "data" / "world" / "world_state.json",
        day_root / "world_state.json",
    )
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            doc = _read_optional_json_zero_write(path)
            if isinstance(doc, Mapping):
                return True, dict(doc)
            return True, {}
    return False, None


def _extract_items_from_collection(
    raw_collection: Any,
    *,
    source: str,
    default_kind: str = "object",
) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    if isinstance(raw_collection, list):
        for entry in raw_collection:
            if not isinstance(entry, Mapping):
                continue
            item_id = str(
                entry.get("item_id")
                or entry.get("object_id")
                or entry.get("item_instance_id")
                or entry.get("id")
                or ""
            ).strip()
            if not item_id:
                continue
            title_cn = str(
                entry.get("title_cn")
                or entry.get("name_cn")
                or entry.get("label_cn")
                or entry.get("name")
                or entry.get("title")
                or entry.get("label")
                or item_id
            ).strip()
            place_cn = _human_place_label(
                entry.get("expected_place_cn")
                or entry.get("place_cn")
                or entry.get("location_cn")
                or entry.get("spot_cn")
                or entry.get("place")
                or entry.get("placement")
                or entry.get("surface_id")
                or entry.get("spot_id")
                or entry.get("area_id")
                or entry.get("location_id")
                or entry.get("location")
                or entry.get("place_id")
                or ""
            )
            kind = str(entry.get("kind") or default_kind).strip() or default_kind
            status_val = str(entry.get("status") or "").strip().lower()
            is_present = True
            if entry.get("present") is False or entry.get("removed") is True:
                is_present = False
            elif status_val in _ABSENT_STATUS_TOKENS:
                is_present = False
            shop_open: Optional[bool] = None
            if kind == "shop":
                if isinstance(entry.get("open"), bool):
                    shop_open = entry["open"]
                elif isinstance(entry.get("is_open"), bool):
                    shop_open = entry["is_open"]
                elif isinstance(entry.get("is_open_now"), bool):
                    shop_open = entry["is_open_now"]
                elif entry.get("closed") is True or entry.get("unexpected_closed") is True:
                    shop_open = False
                elif status_val in _CLOSED_STATUS_TOKENS:
                    shop_open = False
                elif status_val == "open":
                    shop_open = True
            extracted.append({
                "item_id": _sanitize_text(item_id),
                "title_cn": _sanitize_text(title_cn),
                "place_cn": place_cn,
                "kind": _sanitize_text(kind),
                "source": source,
                "present": is_present,
                "shop_open": shop_open,
                "shared": bool(entry.get("shared", True)),
            })
    elif isinstance(raw_collection, Mapping):
        for raw_id, entry in sorted(raw_collection.items()):
            item_id = str(raw_id or "").strip()
            if not item_id:
                continue
            if isinstance(entry, str):
                extracted.append({
                    "item_id": _sanitize_text(item_id),
                    "title_cn": _sanitize_text(item_id),
                    "place_cn": _human_place_label(entry),
                    "kind": default_kind,
                    "source": source,
                    "present": True,
                    "shop_open": None,
                    "shared": True,
                })
                continue
            if not isinstance(entry, Mapping):
                continue
            sub = dict(entry)
            sub.setdefault("item_id", item_id)
            extracted.extend(_extract_items_from_collection([sub], source=source, default_kind=default_kind))
    return extracted


def _extract_world_state_entries(world_doc: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(world_doc, Mapping):
        return []
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(rows: Sequence[dict[str, Any]]) -> None:
        for row in rows:
            iid = row["item_id"]
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
            entries.append(row)

    for key in ("room_objects", "objects", "items", "placements"):
        if key in world_doc:
            _add(_extract_items_from_collection(world_doc.get(key), source="world_state", default_kind="object"))

    room_block = world_doc.get("room")
    if isinstance(room_block, Mapping):
        for key in ("objects", "items", "placements"):
            if key in room_block:
                _add(_extract_items_from_collection(room_block.get(key), source="world_state", default_kind="object"))

    rooms_block = world_doc.get("rooms")
    if isinstance(rooms_block, Mapping):
        for room_name, room_val in sorted(rooms_block.items()):
            if isinstance(room_val, Mapping):
                for key in ("objects", "items", "placements"):
                    rows = _extract_items_from_collection(room_val.get(key), source="world_state", default_kind="object")
                    for r in rows:
                        if not r["place_cn"]:
                            r["place_cn"] = _human_place_label(str(room_name))
                    _add(rows)

    if "shops" in world_doc:
        _add(_extract_items_from_collection(world_doc.get("shops"), source="world_state", default_kind="shop"))
    if "places" in world_doc and isinstance(world_doc.get("places"), (list, Mapping)):
        place_rows = _extract_items_from_collection(world_doc.get("places"), source="world_state", default_kind="shop")
        for r in place_rows:
            if r.get("shop_open") is not None or r.get("kind") == "shop":
                r["kind"] = "shop"
                _add([r])
    return entries


def _extract_home_hearth_entries(day_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rel in (
        ("phone", "home_hearth_reunion_anchor.json"),
        ("phone", "reunion_anchor.json"),
    ):
        doc = _read_optional_json_zero_write(day_root.joinpath(*rel))
        if not isinstance(doc, Mapping):
            continue
        candidates: list[Any] = []
        anchor = doc.get("anchor")
        if isinstance(anchor, Mapping):
            items_box = anchor.get("items")
            if isinstance(items_box, Mapping) and "items" in items_box:
                candidates.append(items_box.get("items"))
            elif isinstance(items_box, (list, Mapping)):
                candidates.append(items_box)
        if "items" in doc:
            items_top = doc.get("items")
            if isinstance(items_top, Mapping) and "items" in items_top:
                candidates.append(items_top.get("items"))
            else:
                candidates.append(items_top)
        if "room_objects" in doc:
            candidates.append(doc.get("room_objects"))

        for col in candidates:
            for row in _extract_items_from_collection(col, source="home_hearth_anchor", default_kind="object"):
                if row["item_id"] not in seen_ids:
                    seen_ids.add(row["item_id"])
                    entries.append(row)
    return entries


def _v04_shop_is_open(place_doc: Mapping[str, Any], *, now_dt: Optional[datetime]) -> Optional[bool]:
    if isinstance(place_doc.get("open"), bool):
        return place_doc["open"]
    if isinstance(place_doc.get("is_open"), bool):
        return place_doc["is_open"]
    if isinstance(place_doc.get("is_open_now"), bool):
        return place_doc["is_open_now"]
    if place_doc.get("closed") is True or place_doc.get("unexpected_closed") is True or place_doc.get("closed_override") is True:
        return False
    status_val = str(place_doc.get("status") or "").strip().lower()
    if status_val in _CLOSED_STATUS_TOKENS:
        return False
    if status_val == "open":
        return True
    phys = place_doc.get("physical_states")
    if isinstance(phys, list):
        for ps in phys:
            text_blob = json.dumps(ps, ensure_ascii=False) if isinstance(ps, Mapping) else str(ps)
            if any(tok in text_blob for tok in ("闭店", "停业", "歇业", "临时关门", "转让告示", "closed_unexpected")):
                return False
    open_window = place_doc.get("open_window")
    if isinstance(open_window, str) and "-" in open_window and now_dt is not None:
        try:
            start_s, end_s = open_window.split("-", 1)
            sh, sm = [int(x) for x in start_s.strip().split(":", 1)]
            eh, em = [int(x) for x in end_s.strip().split(":", 1)]
            cur_min = now_dt.hour * 60 + now_dt.minute
            return (sh * 60 + sm) <= cur_min < (eh * 60 + em)
        except (ValueError, TypeError):
            return None
    return None


def _extract_v04_neighborhood_entries(
    day_root: Path,
    *,
    now_dt: Optional[datetime],
) -> tuple[bool, list[dict[str, Any]]]:
    v04_path = day_root / "world" / "v04_neighborhood_state.json"
    if not (v04_path.is_file() and not v04_path.is_symlink()):
        return False, []
    doc = _read_optional_json_zero_write(v04_path)
    if not isinstance(doc, Mapping):
        return True, []

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(row: dict[str, Any]) -> None:
        if row["item_id"] not in seen_ids:
            seen_ids.add(row["item_id"])
            entries.append(row)

    # Plant
    plant = doc.get("plant")
    if isinstance(plant, Mapping):
        pid = str(plant.get("plant_id") or "v04_porch_plant").strip()
        pname = str(plant.get("name") or plant.get("title_cn") or "门廊盆栽").strip()
        pplace = _human_place_label(plant.get("place_id") or plant.get("place_cn") or "porch")
        present = plant.get("present") is not False and plant.get("removed") is not True
        _add({
            "item_id": _sanitize_text(pid),
            "title_cn": _sanitize_text(pname),
            "place_cn": pplace,
            "kind": "plant",
            "source": "v04_neighborhood",
            "present": present,
            "shop_open": None,
            "shared": True,
        })

    # Drying cloths
    cloths = doc.get("drying_cloths") or doc.get("cloths")
    for row in _extract_items_from_collection(cloths, source="v04_neighborhood", default_kind="cloth"):
        _add(row)

    # Shared shelf items
    shelf = doc.get("shared_shelf") or doc.get("shelf_items")
    for row in _extract_items_from_collection(shelf, source="v04_neighborhood", default_kind="object"):
        if not row["place_cn"]:
            row["place_cn"] = "街角共享架"
        _add(row)

    # Places / shops (e.g. bookstore)
    places = doc.get("places")
    if isinstance(places, Mapping):
        for place_id, pdoc in sorted(places.items()):
            if not isinstance(pdoc, Mapping):
                continue
            has_shop_marker = bool(
                pdoc.get("open_window")
                or place_id in ("bookstore", "cafe", "corner_shop")
                or pdoc.get("kind") == "shop"
                or any(k in pdoc for k in ("open", "is_open", "is_open_now", "closed", "unexpected_closed", "closed_override"))
            )
            if not has_shop_marker:
                continue
            shop_open = _v04_shop_is_open(pdoc, now_dt=now_dt)
            title_cn = str(pdoc.get("name") or pdoc.get("title_cn") or _PLACE_LABELS_CN.get(str(place_id), str(place_id))).strip()
            _add({
                "item_id": _sanitize_text(str(place_id)),
                "title_cn": _sanitize_text(title_cn),
                "place_cn": str(pdoc.get("open_window") or _human_place_label(place_id) or "营业中"),
                "kind": "shop",
                "source": "v04_neighborhood",
                "present": True,
                "shop_open": shop_open,
                "shared": True,
            })

    # Explicit shops dict if present
    if "shops" in doc:
        for row in _extract_items_from_collection(doc.get("shops"), source="v04_neighborhood", default_kind="shop"):
            _add(row)

    return True, entries


def _validate_explicit_expectation_items(raw_items: Any) -> list[dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise V02ValidationError("INVALID_FIELDS", "Field 'items' must be a list of expectation objects")
    if len(raw_items) > 64:
        raise V02ValidationError("INVALID_FIELDS", "Field 'items' exceeds max length 64")
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_items):
        if not isinstance(entry, Mapping):
            raise V02ValidationError("INVALID_FIELDS", f"items[{idx}] must be an object")
        item_id = _require_clean_str(entry.get("item_id"), f"items[{idx}].item_id", max_len=96)
        title_cn = _require_clean_str(entry.get("title_cn"), f"items[{idx}].title_cn", max_len=120)
        expected_place_cn = _require_clean_str(
            entry.get("expected_place_cn"), f"items[{idx}].expected_place_cn", max_len=120
        )
        kind = _optional_clean_str(entry.get("kind"), f"items[{idx}].kind", max_len=40) or "object"
        shared = _optional_bool(entry.get("shared"), f"items[{idx}].shared", default=True)
        out.append({
            "item_id": item_id,
            "title_cn": title_cn,
            "expected_place_cn": expected_place_cn,
            "kind": kind,
            "source": "explicit_snapshot",
            "shared": shared,
        })
    return out


def snapshot_room_expectations(
    day_root: Path | str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Snapshot real room objects/placements as TA's familiar environmental expectations (subject-only)."""
    req = dict(payload) if isinstance(payload, Mapping) else {}
    root = Path(day_root)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        now_dt = _parse_iso(stamp)
        replace = _optional_bool(req.get("replace"), "replace", default=False)
        default_shared = _optional_bool(req.get("shared"), "shared", default=True)
        explicit_items = _validate_explicit_expectation_items(req.get("items"))

        world_exists, world_doc = _resolve_world_state_doc_zero_write(root)
        world_entries = _extract_world_state_entries(world_doc) if world_exists else []
        hearth_entries = _extract_home_hearth_entries(root)
        _v04_exists, v04_entries = _extract_v04_neighborhood_entries(root, now_dt=now_dt)

        merged_by_id: dict[str, dict[str, Any]] = {}
        if not replace:
            for existing in state.get("room_expectations") or []:
                if isinstance(existing, Mapping) and existing.get("item_id"):
                    merged_by_id[str(existing["item_id"])] = copy.deepcopy(dict(existing))

        for entry in (*world_entries, *hearth_entries, *v04_entries):
            if not entry.get("present", True):
                continue
            place_label = entry.get("place_cn") or ("营业中" if entry.get("kind") == "shop" else "原处")
            merged_by_id[entry["item_id"]] = {
                "item_id": entry["item_id"],
                "title_cn": entry["title_cn"],
                "expected_place_cn": place_label,
                "kind": entry["kind"],
                "source": entry["source"],
                "shared": bool(entry.get("shared", default_shared)),
                "captured_at": stamp,
            }

        for exp in explicit_items:
            merged_by_id[exp["item_id"]] = {
                "item_id": exp["item_id"],
                "title_cn": exp["title_cn"],
                "expected_place_cn": exp["expected_place_cn"],
                "kind": exp["kind"],
                "source": exp["source"],
                "shared": exp["shared"],
                "captured_at": stamp,
            }

        ordered = [merged_by_id[k] for k in sorted(merged_by_id.keys())]
        state["room_expectations"] = ordered
        state["room_expectations_captured_at"] = stamp
        return {
            "room_expectations_count": len(ordered),
            "room_expectations": copy.deepcopy(ordered),
            "captured_at": stamp,
        }

    return _run_locked_tx(
        root,
        action_name="snapshot_room_expectations",
        actor_side="subject",
        payload=req,
        now_iso=now_iso,
        mutator=_mutate,
    )


def _dynamic_observation_id(item_id: str, kind: str) -> str:
    return f"abs_det_{_safe_token(item_id)}_{kind.lower()}"


def _compose_absence_summary(
    *,
    kind: str,
    title_cn: str,
    expected_place_cn: str,
    observed_place_cn: str,
) -> str:
    if kind == "MOVED_OBJECT":
        obs_desc = observed_place_cn or "别处"
        return (
            f"原本记得「{title_cn}」放在{expected_place_cn}，刚才看已经挪到了{obs_desc}"
            "（暂不清楚是谁动过的）"
        )
    if kind == "SHOP_CLOSED_UNEXPECTED":
        return f"原本以为「{title_cn}」这会儿开着，刚才看去却没开门（暂不清楚原因）"
    return (
        f"原本记得「{title_cn}」放在{expected_place_cn}，现在那边没看到"
        "（暂不清楚是谁动过或收哪儿去了）"
    )


def detect_absence_and_mismatch_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Compare stored ``room_expectations`` against live disk state + persisted observations (100% zero-write).

    NEVER guesses who moved an item: ``who_moved`` is ALWAYS ``"UNKNOWN"`` unless
    explicitly acknowledged via ``acknowledge_absence_observation``.
    """
    root = Path(day_root)
    obs_iso = _now_iso(now_iso)
    now_dt = _parse_iso(obs_iso)

    state_exists, state = _load_state_readonly(root)
    if not state_exists:
        return []

    acks = state.get("absence_acknowledgements")
    ack_map: Mapping[str, Any] = acks if isinstance(acks, Mapping) else {}

    def _apply_ack(row: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(row)
        oid = str(out.get("observation_id") or "")
        ack = ack_map.get(oid) if isinstance(ack_map.get(oid), Mapping) else None
        if ack is not None:
            out["acknowledged_by_partner"] = bool(ack.get("acknowledged_by_partner", True))
            out["partner_note_cn"] = _sanitize_text(str(ack.get("partner_note_cn") or ""))
            ack_who = str(ack.get("who_moved") or "UNKNOWN").strip().upper()
            out["who_moved"] = ack_who if ack_who in ACKNOWLEDGED_WHO_MOVED_VALUES else "UNKNOWN"
            if ack.get("acknowledged_at"):
                out["acknowledged_at"] = str(ack["acknowledged_at"])
        else:
            out["acknowledged_by_partner"] = bool(out.get("acknowledged_by_partner", False))
            out["partner_note_cn"] = _sanitize_text(str(out.get("partner_note_cn") or ""))
            # Iron law: never guess who moved it when unacknowledged
            if not out["acknowledged_by_partner"]:
                out["who_moved"] = "UNKNOWN"
            else:
                w = str(out.get("who_moved") or "UNKNOWN").strip().upper()
                out["who_moved"] = w if w in ACKNOWLEDGED_WHO_MOVED_VALUES else "UNKNOWN"
        return out

    results: list[dict[str, Any]] = []
    seen_obs_ids: set[str] = set()
    seen_item_kinds: set[tuple[str, str]] = set()

    # 1. Persisted explicit absence observations
    for raw_obs in state.get("absence_observations") or []:
        if not isinstance(raw_obs, Mapping):
            continue
        is_shared = bool(raw_obs.get("shared", True))
        if not has_subject_capability and not is_shared:
            continue
        obs = _apply_ack({
            "observation_id": str(raw_obs.get("observation_id") or ""),
            "kind": str(raw_obs.get("kind") or "MISSING_OBJECT"),
            "item_id": str(raw_obs.get("item_id") or ""),
            "title_cn": _sanitize_text(str(raw_obs.get("title_cn") or "")),
            "expected_place_cn": _sanitize_text(str(raw_obs.get("expected_place_cn") or "")),
            "observed_place_cn": _sanitize_text(str(raw_obs.get("observed_place_cn") or "")),
            "who_moved": str(raw_obs.get("who_moved") or "UNKNOWN"),
            "summary_cn": _sanitize_text(str(raw_obs.get("summary_cn") or "")),
            "acknowledged_by_partner": bool(raw_obs.get("acknowledged_by_partner", False)),
            "partner_note_cn": _sanitize_text(str(raw_obs.get("partner_note_cn") or "")),
            "shared": is_shared,
            "detected_at": str(raw_obs.get("detected_at") or obs_iso),
        })
        if obs["observation_id"] and obs["observation_id"] not in seen_obs_ids:
            seen_obs_ids.add(obs["observation_id"])
            seen_item_kinds.add((obs["item_id"], obs["kind"]))
            results.append(obs)

    # 2. Dynamic live comparison against disk state
    expectations = state.get("room_expectations") or []
    if not expectations:
        return _strip_forbidden_and_paths(results)

    world_exists, world_doc = _resolve_world_state_doc_zero_write(root)
    world_entries = _extract_world_state_entries(world_doc) if world_exists else []
    v04_exists, v04_entries = _extract_v04_neighborhood_entries(root, now_dt=now_dt)

    world_by_id = {e["item_id"]: e for e in world_entries}
    v04_by_id = {e["item_id"]: e for e in v04_entries}
    combined_by_id: dict[str, dict[str, Any]] = {}
    combined_by_id.update(v04_by_id)
    combined_by_id.update(world_by_id)

    for exp in expectations:
        if not isinstance(exp, Mapping):
            continue
        is_shared = bool(exp.get("shared", True))
        if not has_subject_capability and not is_shared:
            continue
        item_id = str(exp.get("item_id") or "").strip()
        if not item_id:
            continue
        title_cn = _sanitize_text(str(exp.get("title_cn") or item_id))
        expected_place_cn = _sanitize_text(str(exp.get("expected_place_cn") or ""))
        kind_raw = str(exp.get("kind") or "object").strip().lower()
        source = str(exp.get("source") or "world_state")

        detected_kind: Optional[str] = None
        observed_place_cn = ""

        if kind_raw == "shop":
            live_shop = combined_by_id.get(item_id)
            if live_shop is not None and live_shop.get("shop_open") is False:
                detected_kind = "SHOP_CLOSED_UNEXPECTED"
                observed_place_cn = "暂未开门"
        else:
            # Choose authoritative live source map; never fabricate absences when no live disk source exists
            live_map: Optional[dict[str, dict[str, Any]]] = None
            if source == "v04_neighborhood":
                if v04_exists:
                    live_map = v04_by_id
            elif source == "world_state":
                if world_exists:
                    live_map = world_by_id
            else:
                if world_exists or v04_exists:
                    live_map = combined_by_id

            if live_map is not None:
                live_item = live_map.get(item_id)
                if live_item is None or not live_item.get("present", True):
                    detected_kind = "MISSING_OBJECT"
                    observed_place_cn = ""
                else:
                    live_place = _sanitize_text(str(live_item.get("place_cn") or ""))
                    if expected_place_cn and live_place and live_place != expected_place_cn:
                        detected_kind = "MOVED_OBJECT"
                        observed_place_cn = live_place

        if detected_kind is None:
            continue
        if (item_id, detected_kind) in seen_item_kinds:
            continue

        obs_id = _dynamic_observation_id(item_id, detected_kind)
        if obs_id in seen_obs_ids:
            continue

        summary_cn = _compose_absence_summary(
            kind=detected_kind,
            title_cn=title_cn,
            expected_place_cn=expected_place_cn,
            observed_place_cn=observed_place_cn,
        )
        obs = _apply_ack({
            "observation_id": obs_id,
            "kind": detected_kind,
            "item_id": item_id,
            "title_cn": title_cn,
            "expected_place_cn": expected_place_cn,
            "observed_place_cn": observed_place_cn,
            "who_moved": "UNKNOWN",
            "summary_cn": summary_cn,
            "acknowledged_by_partner": False,
            "partner_note_cn": "",
            "shared": is_shared,
            "detected_at": obs_iso,
        })
        seen_obs_ids.add(obs_id)
        seen_item_kinds.add((item_id, detected_kind))
        results.append(obs)

    return _strip_forbidden_and_paths(results)


def record_absence_observation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Explicitly record an absence or environmental mismatch observation (subject-only)."""
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        item_id = _require_clean_str(payload.get("item_id"), "item_id", max_len=96)
        title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=120)
        raw_kind = payload.get("kind")
        kind = "MISSING_OBJECT" if raw_kind is None else _require_clean_str(raw_kind, "kind", max_len=40)
        if kind not in ABSENCE_KINDS:
            raise V02ValidationError(
                "INVALID_FIELDS",
                f"Field 'kind' must be one of {sorted(ABSENCE_KINDS)}, got {kind!r}",
            )
        expected_place_cn = _require_clean_str(payload.get("expected_place_cn"), "expected_place_cn", max_len=120)
        observed_place_cn = _optional_clean_str(payload.get("observed_place_cn"), "observed_place_cn", max_len=120)
        summary_cn = _optional_clean_str(payload.get("summary_cn"), "summary_cn", max_len=320)
        if not summary_cn:
            summary_cn = _compose_absence_summary(
                kind=kind,
                title_cn=title_cn,
                expected_place_cn=expected_place_cn,
                observed_place_cn=observed_place_cn,
            )
        shared = _optional_bool(payload.get("shared"), "shared", default=True)
        obs_id = _optional_clean_str(payload.get("observation_id"), "observation_id", max_len=96)
        if not obs_id:
            obs_id = _allocate_id(state, "abs")

        entry = {
            "observation_id": obs_id,
            "kind": kind,
            "item_id": item_id,
            "title_cn": title_cn,
            "expected_place_cn": expected_place_cn,
            "observed_place_cn": observed_place_cn,
            "who_moved": "UNKNOWN",
            "summary_cn": summary_cn,
            "acknowledged_by_partner": False,
            "partner_note_cn": "",
            "shared": shared,
            "detected_at": stamp,
        }
        obs_list = list(state.get("absence_observations") or [])
        obs_list = [row for row in obs_list if not (isinstance(row, Mapping) and row.get("observation_id") == obs_id)]
        obs_list.insert(0, entry)
        state["absence_observations"] = obs_list[:MAX_ABSENCE_OBSERVATIONS]
        return {"absence_observation": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_absence_observation",
        actor_side="subject",
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def acknowledge_absence_observation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Acknowledge an absence observation with an optional note without erasing it (partner-safe)."""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    root = Path(day_root)
    has_cap = actor_side == "subject"

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        observation_id = _require_clean_str(payload.get("observation_id"), "observation_id", max_len=120)
        note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=320)
        raw_who = payload.get("who_moved")
        if raw_who is None:
            resolved_who = "UNKNOWN"
        else:
            who_clean = _require_clean_str(raw_who, "who_moved", max_len=32).upper()
            if who_clean not in ACKNOWLEDGED_WHO_MOVED_VALUES:
                raise V02ValidationError(
                    "INVALID_FIELDS",
                    f"Field 'who_moved' must be one of {sorted(ACKNOWLEDGED_WHO_MOVED_VALUES)}",
                )
            resolved_who = who_clean

        # Verify observation exists (either persisted or dynamically detected) and is visible to actor
        visible_obs = detect_absence_and_mismatch_zero_write(
            root,
            has_subject_capability=has_cap,
            now_iso=stamp,
        )
        matched_obs: Optional[dict[str, Any]] = None
        for obs in visible_obs:
            if obs.get("observation_id") == observation_id:
                matched_obs = copy.deepcopy(obs)
                break
        if matched_obs is None:
            raise V02NotFoundError(
                "OBSERVATION_NOT_FOUND",
                f"Absence observation {observation_id!r} not found",
            )

        ack_record = {
            "observation_id": observation_id,
            "acknowledged_by_partner": True,
            "partner_note_cn": note_cn,
            "who_moved": resolved_who,
            "acknowledged_by": actor_side,
            "acknowledged_at": stamp,
        }
        acks = state.setdefault("absence_acknowledgements", {})
        if not isinstance(acks, dict):
            acks = {}
            state["absence_acknowledgements"] = acks
        acks[observation_id] = ack_record

        # Also reflect acknowledgement on persisted entry if present, without erasing it
        for row in state.get("absence_observations") or []:
            if isinstance(row, dict) and row.get("observation_id") == observation_id:
                row["acknowledged_by_partner"] = True
                row["partner_note_cn"] = note_cn
                row["who_moved"] = resolved_who
                row["acknowledged_at"] = stamp

        matched_obs["acknowledged_by_partner"] = True
        matched_obs["partner_note_cn"] = note_cn
        matched_obs["who_moved"] = resolved_who
        matched_obs["acknowledged_at"] = stamp
        return {
            "absence_observation": matched_obs,
            "acknowledgement": copy.deepcopy(ack_record),
        }

    return _run_locked_tx(
        root,
        action_name="acknowledge_absence_observation",
        actor_side=actor_side,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


# ---------------------------------------------------------------------------
# Feature 2 (F-176): Autobiographical Episodes -> New Life Opportunities
# ---------------------------------------------------------------------------


def _load_forget_barrier_blocked_ids_zero_write(day_root: Path) -> set[str]:
    try:
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf
        except ImportError:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        if isinstance(barrier, Mapping):
            return {str(x) for x in (barrier.get("blocked_ids") or ()) if str(x).strip()}
    except Exception:
        pass
    return set()


def _build_opportunity_copy(title: str, outcome: str, domain: str) -> tuple[str, str]:
    clean_title = title[:64]
    clean_domain = domain[:40]
    if outcome == "paused":
        return (
            f"轻手再接一段：{clean_title}",
            f"之前把「{clean_title}」暂放了一会儿，哪天有空可以顺着当时的感觉轻手翻两步，没有进度压力。",
        )
    if outcome == "failed":
        return (
            f"换个轻切口再试：{clean_title}",
            f"上次在{clean_domain}里做「{clean_title}」没顺成，改天兴致来了可以拆成一个小步骤再试，不计较成败。",
        )
    if outcome == "abandoned":
        return (
            f"从旧念头里挑一小块：{clean_title}",
            f"当时把「{clean_title}」先放下了，里头留下的小片段改天仍可以随兴拿来玩一玩，绝无补课负担。",
        )
    if outcome in ("partial", "ongoing"):
        return (
            f"顺手接着看一眼：{clean_title}",
            f"「{clean_title}」还留着半截线索，什么时候手头空闲可以顺手添一两笔。",
        )
    return (
        f"顺着手感延展一步：{clean_title}",
        f"上次把「{clean_title}」做顺手了，改天有兴致可以沿着这条线长出一个新小变体。",
    )


def derive_autobiography_opportunities_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Derive up to 6 gentle, zero-debt life opportunities from real episodes (100% zero-write)."""
    _ = _now_iso(now_iso)
    root = Path(day_root)
    asp_path = root / "state" / "v02_autobiography_skill_provenance.json"
    doc = _read_optional_json_zero_write(asp_path)
    if not isinstance(doc, Mapping):
        return []

    auto = doc.get("autobiography")
    if not isinstance(auto, Mapping):
        return []
    episodes = auto.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return []

    blocked_ids = _load_forget_barrier_blocked_ids_zero_write(root)
    candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    seen_ep_ids: set[str] = set()

    for ep in episodes:
        if not isinstance(ep, Mapping):
            continue
        episode_id = str(ep.get("episode_id") or "").strip()
        if not episode_id or episode_id in seen_ep_ids or episode_id in blocked_ids:
            continue
        source_ref = str(ep.get("source_ref") or "").strip()
        if source_ref and source_ref in blocked_ids:
            continue

        vis_raw = ep.get("visibility")
        if vis_raw is not None:
            is_shared = str(vis_raw).strip().lower() == "shared" and (ep.get("shared") is not False)
        else:
            is_shared = bool(ep.get("shared", False))

        if not has_subject_capability and not is_shared:
            continue

        outcome = str(ep.get("outcome") or ep.get("arc_status") or "paused").strip().lower()
        if outcome not in _EPISODE_OUTCOME_PRIORITY:
            continue
        title = _sanitize_text(str(ep.get("title") or "").strip())
        domain = _sanitize_text(str(ep.get("domain") or "daily_life").strip())
        if not title:
            continue

        idea_title_cn, why_now_cn = _build_opportunity_copy(title, outcome, domain)
        occ = str(ep.get("occurred_at") or ep.get("recorded_at") or "")
        prio = _EPISODE_OUTCOME_PRIORITY[outcome]
        opp = {
            "opp_id": f"opp_ep_{_safe_token(episode_id)}",
            "source_episode_id": episode_id,
            "source_outcome": outcome,
            "domain": domain,
            "idea_title_cn": idea_title_cn,
            "why_now_cn": why_now_cn,
            "zero_debt": True,
            "shared": is_shared,
        }
        seen_ep_ids.add(episode_id)
        candidates.append((prio, occ, episode_id, opp))

    # Prioritize paused/failed/abandoned first, then newest occurred_at, deterministic tiebreak
    candidates.sort(key=lambda row: (row[0], _invert_sort_key(row[1]), row[2]))
    selected = [row[3] for row in candidates[:MAX_AUTOBIOGRAPHY_OPPORTUNITIES]]
    return _strip_forbidden_and_paths(selected)


def _invert_sort_key(text: str) -> tuple[int, ...]:
    return tuple(-ord(ch) for ch in text[:32])


# ---------------------------------------------------------------------------
# Feature 3 (F-177): Mid-Scale Activity Ecology Chains
# ---------------------------------------------------------------------------


def record_ecology_chain_link(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Create or append a real step link in TA's organic activity ecology chain (subject-only)."""
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        step_kind = _require_clean_str(payload.get("step_kind"), "step_kind", max_len=32).lower()
        if step_kind not in ECOLOGY_STEP_KINDS:
            raise V02ValidationError(
                "INVALID_FIELDS",
                f"Field 'step_kind' must be one of {sorted(ECOLOGY_STEP_KINDS)}, got {step_kind!r}",
            )
        ref_id = _require_clean_str(payload.get("ref_id"), "ref_id", max_len=120)
        title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
        theme_cn = _optional_clean_str(payload.get("theme_cn"), "theme_cn", max_len=120)
        note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=280)
        requested_chain_id = _optional_clean_str(payload.get("chain_id"), "chain_id", max_len=96)
        shared = _optional_bool(payload.get("shared"), "shared", default=True)

        chains = list(state.get("ecology_chains") or [])
        target_chain: Optional[dict[str, Any]] = None
        if requested_chain_id:
            for ch in chains:
                if isinstance(ch, dict) and ch.get("chain_id") == requested_chain_id:
                    target_chain = ch
                    break

        if target_chain is None:
            chain_id = requested_chain_id or _allocate_id(state, "chain")
            target_chain = {
                "chain_id": chain_id,
                "theme_cn": theme_cn or title_cn,
                "shared": shared,
                "links": [],
                "step_kinds_path": [],
                "latest_step_kind": step_kind,
                "suggested_next_step_cn": SUGGESTED_NEXT_STEP_BY_KIND[step_kind],
                "created_at": stamp,
                "updated_at": stamp,
            }
            chains.insert(0, target_chain)
        else:
            if theme_cn:
                target_chain["theme_cn"] = theme_cn
            if payload.get("shared") is False and not any(
                bool(lk.get("shared", False)) for lk in (target_chain.get("links") or []) if isinstance(lk, Mapping)
            ):
                target_chain["shared"] = False

        links = list(target_chain.get("links") or [])
        link_entry = {
            "step_index": len(links) + 1,
            "step_kind": step_kind,
            "ref_id": ref_id,
            "title_cn": title_cn,
            "note_cn": note_cn,
            "shared": shared,
            "recorded_at": stamp,
        }
        links.append(link_entry)
        if len(links) > MAX_LINKS_PER_CHAIN:
            links = links[-MAX_LINKS_PER_CHAIN:]
            for idx, lk in enumerate(links, start=1):
                lk["step_index"] = idx

        target_chain["links"] = links
        target_chain["step_kinds_path"] = [str(lk.get("step_kind") or "") for lk in links]
        target_chain["latest_step_kind"] = step_kind
        target_chain["suggested_next_step_cn"] = SUGGESTED_NEXT_STEP_BY_KIND[step_kind]
        target_chain["updated_at"] = stamp

        state["ecology_chains"] = chains[:MAX_ECOLOGY_CHAINS]
        return {
            "ecology_chain": copy.deepcopy(target_chain),
            "link_added": copy.deepcopy(link_entry),
        }

    return _run_locked_tx(
        day_root,
        action_name="record_ecology_chain_link",
        actor_side="subject",
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def suggest_ecology_spark(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Drop a gentle, zero-debt, non-coercive ecology spark for TA (partner-safe)."""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        theme_cn = _require_clean_str(payload.get("theme_cn"), "theme_cn", max_len=120)
        spark_text_cn = _require_clean_str(payload.get("spark_text_cn"), "spark_text_cn", max_len=320)
        raw_linked = payload.get("linked_kind")
        linked_kind: Optional[str] = None
        if raw_linked is not None:
            lk = _optional_clean_str(raw_linked, "linked_kind", max_len=32).lower()
            if lk:
                if lk not in PARTNER_SPARK_LINKED_KINDS:
                    raise V02ValidationError(
                        "INVALID_FIELDS",
                        f"Field 'linked_kind' must be one of {sorted(PARTNER_SPARK_LINKED_KINDS)}, got {lk!r}",
                    )
                linked_kind = lk

        spark_id = _optional_clean_str(payload.get("spark_id"), "spark_id", max_len=96)
        if not spark_id:
            spark_id = _allocate_id(state, "spark")

        entry = {
            "spark_id": spark_id,
            "theme_cn": theme_cn,
            "spark_text_cn": spark_text_cn,
            "linked_kind": linked_kind,
            "suggested_by": actor_side,
            "status": "OPTIONAL_SPARK",
            "zero_debt": True,
            "non_coercive": True,
            "auto_adopted": False,
            "shared": True,
            "created_at": stamp,
        }
        sparks = list(state.get("partner_ecology_sparks") or [])
        sparks = [s for s in sparks if not (isinstance(s, Mapping) and s.get("spark_id") == spark_id)]
        sparks.insert(0, entry)
        state["partner_ecology_sparks"] = sparks[:MAX_PARTNER_SPARKS]
        return {"partner_ecology_spark": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="suggest_ecology_spark",
        actor_side=actor_side,
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


# ---------------------------------------------------------------------------
# Feature 4 (F-178): Practice-Driven Evaluation Standard Evolution
# ---------------------------------------------------------------------------


def record_practice_standard_evolution(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record how sustained practice shifted TA's evaluation standard (subject-only)."""
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        domain = _require_clean_str(payload.get("domain"), "domain", max_len=80)
        before_criterion_cn = _require_clean_str(
            payload.get("before_criterion_cn"), "before_criterion_cn", max_len=320
        )
        after_criterion_cn = _require_clean_str(
            payload.get("after_criterion_cn"), "after_criterion_cn", max_len=320
        )
        raw_count = payload.get("practice_evidence_count")
        if not _is_strict_int(raw_count) or raw_count < 1 or raw_count > 100000:
            raise V02ValidationError(
                "INVALID_FIELDS",
                "Field 'practice_evidence_count' must be a positive integer",
            )
        evidence_refs = _clean_str_list(payload.get("evidence_refs"), "evidence_refs")
        distinction_note_cn = _optional_clean_str(
            payload.get("distinction_note_cn"), "distinction_note_cn", max_len=320
        )
        if not distinction_note_cn:
            distinction_note_cn = (
                "这是持续练习后眼光/标准变了（评判维度本身在演化），"
                "不同于同一套标准下的成功率提高了。"
            )
        shared = _optional_bool(payload.get("shared"), "shared", default=True)
        evolution_id = _optional_clean_str(payload.get("evolution_id"), "evolution_id", max_len=96)
        if not evolution_id:
            evolution_id = _allocate_id(state, "std_evo")

        entry = {
            "evolution_id": evolution_id,
            "kind": "RECORDED_STANDARD_EVOLUTION",
            "source": "subject_recorded",
            "domain": domain,
            "before_criterion_cn": before_criterion_cn,
            "after_criterion_cn": after_criterion_cn,
            "practice_evidence_count": int(raw_count),
            "evidence_refs": evidence_refs,
            "distinction_note_cn": distinction_note_cn,
            "change_category": "EVALUATION_STANDARD_SHIFT",
            "distinguishes_standard_shift_from_success_rate": True,
            "not_mere_success_rate_gain": True,
            "shared": shared,
            "recorded_at": stamp,
        }
        evolutions = list(state.get("practice_standard_evolutions") or [])
        evolutions = [
            ev for ev in evolutions if not (isinstance(ev, Mapping) and ev.get("evolution_id") == evolution_id)
        ]
        evolutions.insert(0, entry)
        state["practice_standard_evolutions"] = evolutions[:MAX_STANDARD_EVOLUTIONS]
        return {"practice_standard_evolution": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_practice_standard_evolution",
        actor_side="subject",
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def evaluate_practice_driven_standard_proposals_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Surface recorded standard evolutions + real practice-driven proposals (100% zero-write)."""
    obs_iso = _now_iso(now_iso)
    root = Path(day_root)

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Recorded practice standard evolutions from own state
    state_exists, state = _load_state_readonly(root)
    if state_exists:
        for ev in state.get("practice_standard_evolutions") or []:
            if not isinstance(ev, Mapping):
                continue
            is_shared = bool(ev.get("shared", True))
            if not has_subject_capability and not is_shared:
                continue
            eid = str(ev.get("evolution_id") or "")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)
            row = copy.deepcopy(dict(ev))
            row.setdefault("kind", "RECORDED_STANDARD_EVOLUTION")
            row.setdefault("change_category", "EVALUATION_STANDARD_SHIFT")
            row["distinguishes_standard_shift_from_success_rate"] = True
            row["not_mere_success_rate_gain"] = True
            out.append(row)

    # 2. Skill provenance releases (trials >= 3) & method_patches
    asp_doc = _read_optional_json_zero_write(root / "state" / "v02_autobiography_skill_provenance.json")
    if isinstance(asp_doc, Mapping) and isinstance(asp_doc.get("skills"), Mapping):
        skills = asp_doc["skills"]
        releases = skills.get("releases")
        if isinstance(releases, Mapping):
            for s_key in sorted(releases.keys()):
                rec = releases.get(s_key)
                if not isinstance(rec, Mapping):
                    continue
                eff = rec.get("method_efficacy") if isinstance(rec.get("method_efficacy"), Mapping) else {}
                raw_trials = eff.get("trials") if "trials" in eff else rec.get("trials")
                trials = int(raw_trials) if _is_strict_int(raw_trials) else 0
                if trials < 3:
                    continue
                method_id = _sanitize_text(str(rec.get("method_id") or s_key))
                domain = _sanitize_text(str(rec.get("domain") or rec.get("scope") or "practice"))
                title_cn = _sanitize_text(str(rec.get("title_cn") or method_id))
                prop_id = f"prop_skill_{_safe_token(method_id)}_{_safe_token(domain)}"
                if prop_id in seen_ids:
                    continue
                seen_ids.add(prop_id)
                out.append({
                    "evolution_id": prop_id,
                    "kind": "PRACTICE_STANDARD_PROPOSAL",
                    "source": "skill_provenance_trials",
                    "domain": domain,
                    "before_criterion_cn": f"起初做「{title_cn}」主要看步骤能不能跑通、是否勉强达标",
                    "after_criterion_cn": (
                        f"已累积 {trials} 次真实试做，可以温和重审对「{title_cn}」的评判眼光，"
                        "看标准是否已从单纯成败细化到质感与顺手度"
                    ),
                    "practice_evidence_count": trials,
                    "evidence_refs": [f"skill:{method_id}:{domain}"],
                    "distinction_note_cn": (
                        "这是反复练习带出的眼光/标准变了（重审评判维度），"
                        "不等同于同一套标准下成功率提高了。"
                    ),
                    "change_category": "EVALUATION_STANDARD_SHIFT",
                    "distinguishes_standard_shift_from_success_rate": True,
                    "not_mere_success_rate_gain": True,
                    "shared": True,
                    "recorded_at": str(rec.get("updated_at") or obs_iso),
                })

        raw_patches = skills.get("method_patches")
        if isinstance(raw_patches, list) and raw_patches:
            patches_by_method: dict[str, list[dict[str, Any]]] = {}
            for p in raw_patches:
                if not isinstance(p, Mapping):
                    continue
                p_shared = bool(p.get("shared", True))
                if not has_subject_capability and not p_shared:
                    continue
                mid = _sanitize_text(str(p.get("method_id") or p.get("scope") or "practice"))
                patches_by_method.setdefault(mid, []).append(dict(p))

            for mid in sorted(patches_by_method.keys()):
                group = patches_by_method[mid]
                prop_id = f"prop_patch_{_safe_token(mid)}"
                if prop_id in seen_ids:
                    continue
                seen_ids.add(prop_id)
                scope_label = _sanitize_text(str(group[0].get("scope") or mid))
                refs = [str(p.get("patch_id") or f"patch_{i}") for i, p in enumerate(group[:6], start=1)]
                group_shared = all(bool(p.get("shared", True)) for p in group)
                out.append({
                    "evolution_id": prop_id,
                    "kind": "PRACTICE_STANDARD_PROPOSAL",
                    "source": "skill_method_patches",
                    "domain": scope_label,
                    "before_criterion_cn": f"早先在「{mid}」上只看表面是否完成，没留意隐蔽毛病",
                    "after_criterion_cn": (
                        f"基于 {len(group)} 条针对性方法修补，TA 开始把关键细节纳入评判标准"
                    ),
                    "practice_evidence_count": len(group),
                    "evidence_refs": refs,
                    "distinction_note_cn": (
                        "方法修补暴露出眼光/标准变了（发现了过去忽略的品质门槛），"
                        "不能只用成功率提高了概括。"
                    ),
                    "change_category": "EVALUATION_STANDARD_SHIFT",
                    "distinguishes_standard_shift_from_success_rate": True,
                    "not_mere_success_rate_gain": True,
                    "shared": group_shared,
                    "recorded_at": str(group[-1].get("updated_at") or group[-1].get("recorded_at") or obs_iso),
                })

    # 3. Crossmodal palette_studies (>= 2)
    cm_doc = _read_optional_json_zero_write(root / "phone" / "v02_crossmodal.json")
    if isinstance(cm_doc, Mapping) and isinstance(cm_doc.get("palette_studies"), list):
        visible_studies: list[Mapping[str, Any]] = []
        shared_studies_count = 0
        for st in cm_doc["palette_studies"]:
            if not isinstance(st, Mapping):
                continue
            st_shared = bool(st.get("shared", False))
            if st_shared:
                shared_studies_count += 1
            if not has_subject_capability and not st_shared:
                continue
            visible_studies.append(st)

        if len(visible_studies) >= 2:
            prop_id = "prop_crossmodal_palette_standard"
            if prop_id not in seen_ids:
                seen_ids.add(prop_id)
                refs = [
                    str(st.get("study_id") or f"pal_{idx}")
                    for idx, st in enumerate(visible_studies[:6], start=1)
                ]
                out.append({
                    "evolution_id": prop_id,
                    "kind": "PRACTICE_STANDARD_PROPOSAL",
                    "source": "crossmodal_palette_studies",
                    "domain": "color_palette_taste",
                    "before_criterion_cn": "早先看配色主要凭第一眼顺不顺眼、有没有撞色",
                    "after_criterion_cn": (
                        f"对照过 {len(visible_studies)} 组真实采色练习后，"
                        "开始留意暖冷平衡与明暗呼吸感，审美标准本身也在悄悄长细"
                    ),
                    "practice_evidence_count": len(visible_studies),
                    "evidence_refs": refs,
                    "distinction_note_cn": (
                        "多轮真实试色带来的眼光/标准变了（审美辨别力在深化），"
                        "不同于同一套标准下的成功率提高了。"
                    ),
                    "change_category": "EVALUATION_STANDARD_SHIFT",
                    "distinguishes_standard_shift_from_success_rate": True,
                    "not_mere_success_rate_gain": True,
                    "shared": shared_studies_count >= 2,
                    "recorded_at": str(visible_studies[0].get("created_at") or obs_iso),
                })

    return _strip_forbidden_and_paths(out)


# ---------------------------------------------------------------------------
# Combined Zero-Write Reader & Unified Mutation Dispatcher
# ---------------------------------------------------------------------------


def _project_ecology_chains(
    raw_chains: Any,
    *,
    has_subject_capability: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw_ch in raw_chains or []:
        if not isinstance(raw_ch, Mapping):
            continue
        ch_shared = bool(raw_ch.get("shared", True))
        if not has_subject_capability and not ch_shared:
            continue
        links_in = raw_ch.get("links") or []
        visible_links: list[dict[str, Any]] = []
        for lk in links_in:
            if not isinstance(lk, Mapping):
                continue
            lk_shared = bool(lk.get("shared", ch_shared))
            if not has_subject_capability and not lk_shared:
                continue
            visible_links.append(copy.deepcopy(dict(lk)))
        if not has_subject_capability and not visible_links:
            continue
        ch = copy.deepcopy(dict(raw_ch))
        ch["links"] = visible_links
        if visible_links:
            latest_kind = str(visible_links[-1].get("step_kind") or ch.get("latest_step_kind") or "reading")
            ch["step_kinds_path"] = [str(lk.get("step_kind") or "") for lk in visible_links]
            ch["latest_step_kind"] = latest_kind
            ch["suggested_next_step_cn"] = SUGGESTED_NEXT_STEP_BY_KIND.get(
                latest_kind,
                str(ch.get("suggested_next_step_cn") or ""),
            )
        out.append(ch)
    return out


def read_midscale_ecology_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Combined 100% zero-write reader for F-175..F-178 with strict privacy filtering."""
    obs_iso = _now_iso(now_iso)
    root = Path(day_root)
    has_cap = bool(has_subject_capability)
    state_exists, state = _load_state_readonly(root)

    visible_expectations = [
        exp
        for exp in (state.get("room_expectations") or [])
        if isinstance(exp, Mapping) and (has_cap or bool(exp.get("shared", True)))
    ]
    absence_observations = detect_absence_and_mismatch_zero_write(
        root,
        has_subject_capability=has_cap,
        now_iso=obs_iso,
    )
    autobiography_opportunities = derive_autobiography_opportunities_zero_write(
        root,
        has_subject_capability=has_cap,
        now_iso=obs_iso,
    )
    ecology_chains = _project_ecology_chains(
        state.get("ecology_chains"),
        has_subject_capability=has_cap,
    )
    partner_sparks = [
        copy.deepcopy(dict(sp))
        for sp in (state.get("partner_ecology_sparks") or [])
        if isinstance(sp, Mapping) and (has_cap or bool(sp.get("shared", True)))
    ]
    practice_standard_evolutions = evaluate_practice_driven_standard_proposals_zero_write(
        root,
        has_subject_capability=has_cap,
        now_iso=obs_iso,
    )

    projected = {
        "state_exists": bool(state_exists),
        "perspective": "subject" if has_cap else "partner",
        "revision": int(state.get("revision") or 0),
        "observed_at": obs_iso,
        "room_expectations_count": len(visible_expectations),
        "absence_observations": absence_observations,
        "autobiography_opportunities": autobiography_opportunities,
        "ecology_chains": ecology_chains,
        "partner_ecology_sparks": partner_sparks,
        "practice_standard_evolutions": practice_standard_evolutions,
        "llm_calls": 0,
        "zero_write": True,
    }
    return _strip_forbidden_and_paths(projected)


PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "suggest_ecology_spark",
    "acknowledge_absence_observation",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "snapshot_room_expectations",
    "record_absence_observation",
    "record_ecology_chain_link",
    "record_practice_standard_evolution",
})


def apply_midscale_ecology_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Dispatch a validated mutation action and return ``(projected_view, command_status)``."""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "Payload must be a JSON object")

    action = _require_clean_str(payload.get("action"), "action", max_len=64)
    if action not in SUBJECT_ALLOWED_ACTIONS:
        raise V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    allowed = SUBJECT_ALLOWED_ACTIONS if actor_side == "subject" else PARTNER_ALLOWED_ACTIONS
    if action not in allowed:
        raise V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is forbidden for actor_side={actor_side!r}",
        )

    obs_iso = _now_iso(now_iso)
    has_cap = actor_side == "subject"

    if action == "snapshot_room_expectations":
        receipt = snapshot_room_expectations(day_root, payload, now_iso=obs_iso)
    elif action == "record_absence_observation":
        receipt = record_absence_observation(day_root, payload, now_iso=obs_iso)
    elif action == "acknowledge_absence_observation":
        receipt = acknowledge_absence_observation(day_root, payload, actor_side=actor_side, now_iso=obs_iso)
    elif action == "record_ecology_chain_link":
        receipt = record_ecology_chain_link(day_root, payload, now_iso=obs_iso)
    elif action == "suggest_ecology_spark":
        receipt = suggest_ecology_spark(day_root, payload, actor_side=actor_side, now_iso=obs_iso)
    elif action == "record_practice_standard_evolution":
        receipt = record_practice_standard_evolution(day_root, payload, now_iso=obs_iso)
    else:  # pragma: no cover - unreachable due to allowlist check
        raise V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    cmd_status = str(receipt.get("command_status") or "APPLIED")
    projected = read_midscale_ecology_view(
        day_root,
        has_subject_capability=has_cap,
        now_iso=obs_iso,
    )
    projected["action_receipt"] = _strip_forbidden_and_paths(receipt)
    return projected, cmd_status


__all__ = [
    "ABSENCE_KINDS",
    "ACKNOWLEDGED_WHO_MOVED_VALUES",
    "ECOLOGY_STEP_KINDS",
    "FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS",
    "LOCK_REL_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "PARTNER_SPARK_LINKED_KINDS",
    "SCHEMA_VERSION",
    "STATE_REL_PATH",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUGGESTED_NEXT_STEP_BY_KIND",
    "V02ConflictError",
    "V02MidscaleEcologyError",
    "V02NotFoundError",
    "V02PermissionError",
    "V02ValidationError",
    "VIEW_SCHEMA",
    "acknowledge_absence_observation",
    "apply_midscale_ecology_mutation",
    "build_default_state",
    "derive_autobiography_opportunities_zero_write",
    "detect_absence_and_mismatch_zero_write",
    "evaluate_practice_driven_standard_proposals_zero_write",
    "read_midscale_ecology_view",
    "record_absence_observation",
    "record_ecology_chain_link",
    "record_practice_standard_evolution",
    "snapshot_room_expectations",
    "suggest_ecology_spark",
]
