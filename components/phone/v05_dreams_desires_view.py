#!/usr/bin/env python3
"""HTTP view & action gate for ``/api/phone/v05/inner-spark`` (v0.5 Ch. 102, 109, 111, 114, 130).

Contracts:
1. ``read_v05_inner_spark_view``:
   - 100% zero-write on empty, non-existent, or populated ``day_root``;
   - Strictly filters unshared dreams & private subject desires for partner perspective;
   - Strictly enforces anti-spoiler redaction for unread tucked page notes written by the other side;
   - Dynamically projects live melt ratio on window frost without disk writes.
2. ``handle_v05_inner_spark_action`` / ``mutate_v05_inner_spark_view``:
   - Strict per-action field allowlists and recursive forbidden identity/score key scan
     (``400 INVALID_FIELDS``);
   - Strict anti-puppeteer role gate (``403 FORBIDDEN_ACTOR_SIDE``) blocking partner attempts
     to create/resolve TA's desires or mutate TA's dreams.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_representation_lease_forget as _rlf
from components.life.scripts import v05_dreams_desires_foreshadow as dd_engine

ENDPOINT_PATH = "/api/phone/v05/inner-spark"
VIEW_SCHEMA = "nyairo.v05.inner_spark.v1"

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "role",
    "caller",
    "caller_side",
    "is_subject",
    "subject_capability",
    "has_subject_capability",
    "viewer_side",
    "perspective",
    "intimacy_score",
    "affection_score",
    "favorability_score",
    "mood_score",
    "bond_score",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "respond_to_desire",
    "tuck_page_note",
    "advance_reading_page",
    "sketch_window_frost",
    "trigger_object_chemistry",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "record_subject_desire",
    "resolve_subject_desire",
    "settle_morning_dream_pulse",
    "keep_or_share_dream",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "respond_to_desire": frozenset({
        "action",
        "desire_id",
        "partner_note",
        "note_cn",
        "operation_key",
    }),
    "tuck_page_note": frozenset({
        "action",
        "book_title",
        "target_page",
        "note_text",
        "item_kind",
        "total_pages",
        "current_page",
        "operation_key",
    }),
    "advance_reading_page": frozenset({
        "action",
        "book_title",
        "new_page",
        "total_pages",
        "operation_key",
    }),
    "sketch_window_frost": frozenset({
        "action",
        "text",
        "weather",
        "temp_c",
        "humidity_hint",
        "strokes",
        "window_misty",
        "operation_key",
    }),
    "trigger_object_chemistry": frozenset({
        "action",
        "kind",
        "params",
        "operation_key",
    }),
    "record_subject_desire": frozenset({
        "action",
        "layer",
        "text",
        "origin",
        "shared",
        "operation_key",
    }),
    "resolve_subject_desire": frozenset({
        "action",
        "desire_id",
        "status",
        "resolution_note",
        "operation_key",
    }),
    "settle_morning_dream_pulse": frozenset({
        "action",
        "recent_echoes",
        "calm_quiet",
        "force_roll_seed",
        "phase",
        "last_sleep_at",
        "fades_after_hour",
        "operation_key",
    }),
    "keep_or_share_dream": frozenset({
        "action",
        "kept_in_notebook",
        "shared_with_partner",
        "dream_id",
        "operation_key",
    }),
}

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "revision",
    "observed_at",
    "perspective",
    "viewer_side",
    "season_solar",
    "desires",
    "sleep_wake_state",
    "dream_notebook",
    "foreshadow_books",
    "window_frost",
    "recent_chemistry_sparks",
    "allowed_actions",
    "action_receipt",
    "llm_calls",
    "zero_write",
)


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    body: dict[str, Any] = {
        "ok": False,
        "status": "rejected" if status_code < 500 else "unavailable",
        "command_status": "REJECTED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }
    if extra:
        body.update(dict(extra))
    return int(status_code), body


def _scan_payload_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise dd_engine.V05InnerSparkError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise dd_engine.V05InnerSparkError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            low = raw_key.lower()
            if raw_key in FORBIDDEN_IDENTITY_KEYS or low in FORBIDDEN_IDENTITY_KEYS:
                raise dd_engine.V05InnerSparkError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    has_subject_capability: bool,
    read_only: bool,
    command_status: str,
    action_receipt: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        k: copy.deepcopy(projected[k])
        for k in _MIRRORED_DATA_KEYS
        if k in projected
    }
    data["allowed_actions"] = sorted(
        SUBJECT_ALLOWED_ACTIONS if has_subject_capability else PARTNER_ALLOWED_ACTIONS
    )
    data["perspective"] = "subject" if has_subject_capability else "partner"
    data["llm_calls"] = 0
    data["zero_write"] = bool(read_only)
    if action_receipt is not None:
        data["action_receipt"] = copy.deepcopy(dict(action_receipt))

    envelope: dict[str, Any] = {
        "ok": True,
        "status": "ok",
        "command_status": command_status,
        "read_only": bool(read_only),
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "data": data,
    }
    for key, val in data.items():
        envelope[key] = copy.deepcopy(val)
    return envelope


def _apply_forget_barrier(
    projected: Mapping[str, Any],
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter all inner-spark desires, dreams, foreshadow books, frost, and sparks through the zero-write forget barrier."""
    out = copy.deepcopy(dict(projected))
    fail_closed = bool(barrier.get("fail_closed"))

    desires = out.get("desires")
    if isinstance(desires, dict):
        orig_items = desires.get("items") if isinstance(desires.get("items"), list) else []
        filtered_items = _rlf.filter_collection_by_barrier(orig_items, barrier)
        desires["items"] = filtered_items
        layers = desires.get("layers")
        if isinstance(layers, dict):
            for lk, lv in list(layers.items()):
                if isinstance(lv, list):
                    layers[lk] = _rlf.filter_collection_by_barrier(lv, barrier)
            if len(filtered_items) != len(orig_items) or fail_closed or barrier.get("barrier_active"):
                active_counts: dict[str, int] = {}
                counts: dict[str, int] = {}
                for lk, lv in layers.items():
                    rows = lv if isinstance(lv, list) else []
                    counts[lk] = len(rows)
                    active_counts[lk] = sum(
                        1
                        for it in rows
                        if isinstance(it, Mapping)
                        and str(it.get("status") or "active").strip().lower() == "active"
                    )
                counts["total_visible"] = len(filtered_items)
                if isinstance(desires.get("active_counts"), dict):
                    desires["active_counts"] = active_counts
                if isinstance(desires.get("counts"), dict):
                    desires["counts"] = counts

    sw = out.get("sleep_wake_state")
    if isinstance(sw, dict) and sw.get("morning_dream") is not None:
        if fail_closed or _rlf.is_entity_forget_blocked(sw["morning_dream"], barrier):
            sw["morning_dream"] = None

    for list_key in ("dream_notebook", "foreshadow_books", "recent_chemistry_sparks"):
        if isinstance(out.get(list_key), list):
            out[list_key] = _rlf.filter_collection_by_barrier(out[list_key], barrier)

    if out.get("window_frost") is not None:
        if fail_closed or _rlf.is_entity_forget_blocked(out["window_frost"], barrier):
            out["window_frost"] = None

    return out


def read_v05_inner_spark_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.5 Inner Spark (open desires, dreams, foreshadow book, window frost) with 0 disk writes and forget barrier."""
    try:
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        projected = _apply_forget_barrier(
            dd_engine.project_v05_inner_spark_state(
                day_root,
                has_subject_capability=bool(has_subject_capability),
                now_iso=now_iso,
            ),
            barrier,
        )
        return 200, _compose_envelope(
            projected,
            has_subject_capability=bool(has_subject_capability),
            read_only=True,
            command_status="READ",
        )
    except dd_engine.V05InnerSparkError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def handle_v05_inner_spark_action(
    day_root: Path | str,
    payload: Any,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field allowlist & anti-puppeteer gates, then mutate inner-spark state."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    if not isinstance(payload, Mapping) or len(payload) == 0:
        return _error_response(400, "INVALID_FIELDS", "Payload must be a non-empty JSON object")
    try:
        _scan_payload_keys_recursive(payload)
        raw_action = payload.get("action")
        if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
            raise dd_engine.V05InnerSparkError(400, "INVALID_FIELDS", "Field 'action' must be a non-empty string")
        action = raw_action
        if action not in ACTION_ALLOWED_FIELDS:
            raise dd_engine.V05InnerSparkError(400, "INVALID_FIELDS", f"Unknown action: {action!r}")
        allowed_actions = SUBJECT_ALLOWED_ACTIONS if has_subject_capability else PARTNER_ALLOWED_ACTIONS
        if action not in allowed_actions:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Action {action!r} is subject-only and forbidden for actor_side={actor_side!r}",
            )
        unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
        if unknown:
            raise dd_engine.V05InnerSparkError(
                400,
                "INVALID_FIELDS",
                f"Unknown fields for {action!r}: {unknown}",
            )

        receipt: dict[str, Any]
        if action == "respond_to_desire":
            note = payload.get("partner_note") if payload.get("partner_note") is not None else payload.get("note_cn")
            receipt = dd_engine.respond_to_desire(
                day_root,
                desire_id=str(payload.get("desire_id") or ""),
                partner_note=str(note or ""),
                now_iso=now_iso,
                actor_side=actor_side,
            )
        elif action == "tuck_page_note":
            receipt = dd_engine.tuck_page_note(
                day_root,
                book_title=str(payload.get("book_title") or ""),
                target_page=int(payload.get("target_page")),
                note_text=str(payload.get("note_text") or ""),
                item_kind=str(payload.get("item_kind") or "note"),
                total_pages=(int(payload["total_pages"]) if payload.get("total_pages") is not None else None),
                current_page=(int(payload["current_page"]) if payload.get("current_page") is not None else None),
                now_iso=now_iso,
                actor_side=actor_side,
            )
        elif action == "advance_reading_page":
            receipt = dd_engine.advance_reading_page(
                day_root,
                book_title=str(payload.get("book_title") or ""),
                new_page=int(payload.get("new_page")),
                total_pages=(int(payload["total_pages"]) if payload.get("total_pages") is not None else None),
                now_iso=now_iso,
                actor_side=actor_side,
            )
        elif action == "sketch_window_frost":
            receipt = dd_engine.sketch_window_frost(
                day_root,
                text=str(payload.get("text") or ""),
                weather=str(payload.get("weather") or "breezy_cool"),
                temp_c=float(payload.get("temp_c", 8.0) if payload.get("temp_c") is not None else 8.0),
                humidity_hint=str(payload.get("humidity_hint") or "misty"),
                strokes=payload.get("strokes"),
                window_misty=payload.get("window_misty"),
                now_iso=now_iso,
                actor_side=actor_side,
            )
        elif action == "trigger_object_chemistry":
            receipt = dd_engine.trigger_object_chemistry(
                day_root,
                kind=str(payload.get("kind") or ""),
                params=payload.get("params"),
                now_iso=now_iso,
                actor_side=actor_side,
            )
        elif action == "record_subject_desire":
            receipt = dd_engine.record_subject_desire(
                day_root,
                layer=str(payload.get("layer") or ""),
                text=str(payload.get("text") or ""),
                origin=str(payload.get("origin") or "self_emerged"),
                shared=bool(payload.get("shared", True)),
                now_iso=now_iso,
                actor_side="subject",
            )
        elif action == "resolve_subject_desire":
            receipt = dd_engine.resolve_subject_desire(
                day_root,
                desire_id=str(payload.get("desire_id") or ""),
                status=str(payload.get("status") or ""),
                resolution_note=payload.get("resolution_note"),
                now_iso=now_iso,
                actor_side="subject",
            )
        elif action == "settle_morning_dream_pulse":
            receipt = dd_engine.settle_morning_dream_pulse(
                day_root,
                recent_echoes=tuple(payload.get("recent_echoes") or ()),
                calm_quiet=bool(payload.get("calm_quiet", False)),
                force_roll_seed=payload.get("force_roll_seed"),
                now_iso=now_iso,
                phase=str(payload.get("phase") or "just_woke"),
                last_sleep_at=payload.get("last_sleep_at"),
                fades_after_hour=int(payload.get("fades_after_hour", 12) if payload.get("fades_after_hour") is not None else 12),
                actor_side="subject",
            )
        elif action == "keep_or_share_dream":
            receipt = dd_engine.keep_or_share_dream(
                day_root,
                kept_in_notebook=payload.get("kept_in_notebook"),
                shared_with_partner=payload.get("shared_with_partner"),
                dream_id=payload.get("dream_id"),
                now_iso=now_iso,
                actor_side="subject",
            )
        else:
            raise dd_engine.V05InnerSparkError(400, "INVALID_FIELDS", f"Unsupported action: {action!r}")

        projected = dd_engine.project_v05_inner_spark_state(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(
            projected,
            has_subject_capability=bool(has_subject_capability),
            read_only=False,
            command_status="APPLIED",
            action_receipt=receipt,
        )
    except dd_engine.V05InnerSparkError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)
    except (TypeError, ValueError) as exc:
        return _error_response(400, "INVALID_FIELDS", str(exc))


def mutate_v05_inner_spark_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    norm = str(actor_side or "partner").strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        return _error_response(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return handle_v05_inner_spark_action(
        day_root,
        payload,
        has_subject_capability=(norm == "subject"),
        now_iso=now_iso,
    )


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "ENDPOINT_PATH",
    "FORBIDDEN_IDENTITY_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "handle_v05_inner_spark_action",
    "mutate_v05_inner_spark_view",
    "read_v05_inner_spark_view",
]
