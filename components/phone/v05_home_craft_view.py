#!/usr/bin/env python3
"""HTTP view & action gate for ``/api/phone/v05/home-craft`` (v0.5 Ch. 67, 98, 103, 106, 112).

Contracts:
1. ``read_v05_home_craft_view``:
   - 100% zero-write on empty, non-existent, or populated ``day_root``;
   - Returns HTTP 200 envelope (mirrored top-level and ``data``) containing
     ``object_patina``, ``home_arrangement``, ``moving_carton``,
     ``parallel_postcards``, ``world_context``, ``season_solar``,
     ``allowed_actions``, ``llm_calls=0``, ``zero_write=True``.
2. ``handle_v05_home_craft_action`` / ``mutate_v05_home_craft_view``:
   - Strict per-action field allowlists and recursive forbidden identity/score
     key scan (``400 INVALID_FIELDS``);
   - Server-derived ``author_side`` from verified capability (never from body);
   - Real PNG steganographic postcard mint & import via
     ``v05_unified_world_fabric.embed_postcard_png_chunk`` /
     ``extract_postcard_png_chunk``.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_representation_lease_forget as _rlf
from components.world.scripts import v05_home_craft_engine as hc_engine

ENDPOINT_PATH = "/api/phone/v05/home-craft"
VIEW_SCHEMA = "nyairo.v05.home_craft.v1"

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "proposed_by",
    "resolved_by",
    "packed_by_side",
    "minted_by_side",
    "imported_by_side",
    "actor",
    "role",
    "caller",
    "caller_side",
    "is_subject",
    "subject_capability",
    "has_subject_capability",
    "visitor_side",
    "viewer_side",
    "perspective",
    "intimacy_score",
    "affection_score",
    "favorability_score",
    "mood_score",
    "bond_score",
    "durability_pct",
    "wear_pct",
})

CRUMPLED_DRAFT_FORBIDDEN_EXACT_KEYS: frozenset[str] = frozenset({
    "text",
    "content",
    "body",
    "note",
    "note_cn",
    "draft",
    "raw_text",
    "message",
    "snippet",
    "quote",
    "summary",
    "summary_cn",
    "title",
    "title_cn",
    "html",
    "markdown",
    "file",
    "data",
    "base64",
})

CRUMPLED_DRAFT_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "text",
    "content",
    "body",
    "note",
    "draft",
)

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "log_object_patina_or_repair",
    "propose_arrangement_change",
    "resolve_arrangement_compromise",
    "pack_moving_carton",
    "unpack_carton_item",
    "mint_parallel_postcard",
    "import_parallel_postcard",
    "signal_crumpled_draft",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset()

SUBJECT_FORBIDDEN_PARTNER_ONLY_ACTIONS: frozenset[str] = frozenset({
    "signal_crumpled_draft",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = (
    (PARTNER_ALLOWED_ACTIONS - SUBJECT_FORBIDDEN_PARTNER_ONLY_ACTIONS) | SUBJECT_ONLY_ACTIONS
)

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "log_object_patina_or_repair": frozenset({
        "action",
        "object_id",
        "event_kind",
        "note_cn",
        "operation_key",
    }),
    "propose_arrangement_change": frozenset({
        "action",
        "object_id",
        "target_zone",
        "reason_cn",
        "operation_key",
    }),
    "resolve_arrangement_compromise": frozenset({
        "action",
        "proposal_id",
        "decision",
        "compromise_zone",
        "note_cn",
        "operation_key",
    }),
    "pack_moving_carton": frozenset({
        "action",
        "origin_town_cn",
        "items",
        "operation_key",
    }),
    "unpack_carton_item": frozenset({
        "action",
        "carton_id",
        "item_id",
        "target_zone",
        "settle_note_cn",
        "operation_key",
    }),
    "mint_parallel_postcard": frozenset({
        "action",
        "greeting_cn",
        "sender_label_cn",
        "town_name_cn",
        "operation_key",
    }),
    "import_parallel_postcard": frozenset({
        "action",
        "png_base64",
        "from_friend_note_cn",
        "operation_key",
    }),
    "signal_crumpled_draft": frozenset({
        "action",
        "disposal_kind",
        "operation_key",
    }),
}

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "revision",
    "observed_at",
    "world_context",
    "season_solar",
    "object_patina",
    "home_arrangement",
    "moving_carton",
    "parallel_postcards",
    "crumpled_draft_signal",
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
        raise hc_engine.V05HomeCraftError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise hc_engine.V05HomeCraftError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            low = raw_key.lower()
            if raw_key in FORBIDDEN_IDENTITY_KEYS or low in FORBIDDEN_IDENTITY_KEYS:
                raise hc_engine.V05HomeCraftError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


def _scan_crumpled_draft_raw_text_forbidden(node: Any, *, depth: int = 0) -> None:
    """Recursively reject ANY raw-text/content/body/note/draft/file/data key on signal_crumpled_draft."""
    if depth > 12:
        raise hc_engine.V05HomeCraftError(
            400,
            "RAW_TEXT_FORBIDDEN",
            "Crumpled draft disposal must never transmit raw text or content",
        )
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if isinstance(raw_key, str):
                low = raw_key.strip().lower()
                if low in CRUMPLED_DRAFT_FORBIDDEN_EXACT_KEYS or any(
                    sub in low for sub in CRUMPLED_DRAFT_FORBIDDEN_SUBSTRINGS
                ):
                    raise hc_engine.V05HomeCraftError(
                        400,
                        "RAW_TEXT_FORBIDDEN",
                        "Crumpled draft disposal must never transmit raw text or content",
                    )
            _scan_crumpled_draft_raw_text_forbidden(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_crumpled_draft_raw_text_forbidden(item, depth=depth + 1)


def _compose_envelope(
    snapshot: Mapping[str, Any],
    *,
    has_subject_capability: bool,
    read_only: bool,
    command_status: str,
    action_receipt: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        k: copy.deepcopy(snapshot[k])
        for k in _MIRRORED_DATA_KEYS
        if k in snapshot
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
    snapshot: Mapping[str, Any],
    barrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter all home-craft collections and featured refs through the zero-write forget barrier."""
    out = copy.deepcopy(dict(snapshot))
    fail_closed = bool(barrier.get("fail_closed"))

    if isinstance(out.get("object_patina"), list):
        out["object_patina"] = _rlf.filter_collection_by_barrier(out["object_patina"], barrier)

    ha = out.get("home_arrangement")
    if isinstance(ha, dict):
        if isinstance(ha.get("proposals"), list):
            ha["proposals"] = _rlf.filter_collection_by_barrier(ha["proposals"], barrier)
            ha["open_count"] = sum(
                1
                for prop in ha["proposals"]
                if isinstance(prop, Mapping)
                and str(prop.get("status") or "").strip().lower() in ("open", "pending")
            )

    mc = out.get("moving_carton")
    if isinstance(mc, dict) and isinstance(mc.get("cartons"), list):
        orig_len = len(mc["cartons"])
        mc["cartons"] = _rlf.filter_collection_by_barrier(mc["cartons"], barrier)
        if fail_closed:
            mc["unpacked_remaining_total"] = 0
        elif len(mc["cartons"]) != orig_len or barrier.get("barrier_active"):
            remaining = 0
            for carton in mc["cartons"]:
                if not isinstance(carton, Mapping):
                    continue
                items = carton.get("items")
                if isinstance(items, list):
                    remaining += sum(
                        1
                        for it in items
                        if isinstance(it, Mapping)
                        and not bool(it.get("unpacked"))
                        and str(it.get("status") or "").strip().lower() != "unpacked"
                    )
            mc["unpacked_remaining_total"] = remaining

    pp = out.get("parallel_postcards")
    if isinstance(pp, dict):
        for sub_key in ("sent", "received"):
            if isinstance(pp.get(sub_key), list):
                pp[sub_key] = _rlf.filter_collection_by_barrier(pp[sub_key], barrier)

    if out.get("crumpled_draft_signal") is not None:
        if fail_closed or _rlf.is_entity_forget_blocked(out["crumpled_draft_signal"], barrier):
            out["crumpled_draft_signal"] = None

    return out


def read_v05_home_craft_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.5 Home Craft snapshot with 100% zero disk writes and unified forget barrier enforcement."""
    try:
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        snap = _apply_forget_barrier(
            hc_engine.read_home_craft_snapshot(day_root, now_iso=now_iso),
            barrier,
        )
        return 200, _compose_envelope(
            snap,
            has_subject_capability=bool(has_subject_capability),
            read_only=True,
            command_status="READ",
        )
    except hc_engine.V05HomeCraftError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail)


def handle_v05_home_craft_action(
    day_root: Path | str,
    payload: Any,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field allowlist & identity boundaries, then mutate home-craft state."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    if not isinstance(payload, Mapping) or len(payload) == 0:
        return _error_response(400, "INVALID_FIELDS", "Payload must be a non-empty JSON object")
    try:
        if payload.get("action") == "signal_crumpled_draft" and not has_subject_capability:
            _scan_crumpled_draft_raw_text_forbidden(payload)
        _scan_payload_keys_recursive(payload)
        raw_action = payload.get("action")
        if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
            raise hc_engine.V05HomeCraftError(400, "INVALID_FIELDS", "Field 'action' must be a non-empty string")
        action = raw_action
        if action not in ACTION_ALLOWED_FIELDS:
            raise hc_engine.V05HomeCraftError(400, "INVALID_FIELDS", f"Unknown action: {action!r}")
        allowed_actions = SUBJECT_ALLOWED_ACTIONS if has_subject_capability else PARTNER_ALLOWED_ACTIONS
        if action not in allowed_actions:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Action {action!r} is forbidden for actor_side={actor_side!r}",
            )
        if action == "signal_crumpled_draft":
            _scan_crumpled_draft_raw_text_forbidden(payload)
        unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
        if unknown:
            raise hc_engine.V05HomeCraftError(
                400,
                "INVALID_FIELDS",
                f"Unknown fields for {action!r}: {unknown}",
            )

        receipt: dict[str, Any]
        if action == "log_object_patina_or_repair":
            receipt = hc_engine.log_object_patina_or_repair(
                day_root,
                object_id=payload.get("object_id"),
                event_kind=payload.get("event_kind"),
                note_cn=payload.get("note_cn"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "propose_arrangement_change":
            receipt = hc_engine.propose_arrangement_change(
                day_root,
                object_id=payload.get("object_id"),
                target_zone=payload.get("target_zone"),
                reason_cn=payload.get("reason_cn"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "resolve_arrangement_compromise":
            receipt = hc_engine.resolve_arrangement_compromise(
                day_root,
                proposal_id=payload.get("proposal_id"),
                decision=payload.get("decision"),
                compromise_zone=payload.get("compromise_zone"),
                note_cn=payload.get("note_cn", ""),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "pack_moving_carton":
            receipt = hc_engine.pack_moving_carton(
                day_root,
                origin_town_cn=payload.get("origin_town_cn"),
                items=payload.get("items"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "unpack_carton_item":
            receipt = hc_engine.unpack_carton_item(
                day_root,
                carton_id=payload.get("carton_id"),
                item_id=payload.get("item_id"),
                target_zone=payload.get("target_zone"),
                settle_note_cn=payload.get("settle_note_cn", ""),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "mint_parallel_postcard":
            receipt = hc_engine.mint_parallel_postcard(
                day_root,
                greeting_cn=payload.get("greeting_cn"),
                sender_label_cn=payload.get("sender_label_cn"),
                town_name_cn=payload.get("town_name_cn"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "import_parallel_postcard":
            receipt = hc_engine.import_parallel_postcard(
                day_root,
                png_base64=payload.get("png_base64"),
                from_friend_note_cn=payload.get("from_friend_note_cn", ""),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "signal_crumpled_draft":
            receipt = hc_engine.signal_crumpled_draft(
                day_root,
                disposal_kind=payload.get("disposal_kind"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        else:
            raise hc_engine.V05HomeCraftError(400, "INVALID_FIELDS", f"Unsupported action: {action!r}")

        snap = hc_engine.read_home_craft_snapshot(day_root, now_iso=now_iso)
        return 200, _compose_envelope(
            snap,
            has_subject_capability=bool(has_subject_capability),
            read_only=False,
            command_status="APPLIED",
            action_receipt=receipt,
        )
    except hc_engine.V05HomeCraftError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail)
    except (TypeError, ValueError) as exc:
        return _error_response(400, "INVALID_FIELDS", str(exc))


def mutate_v05_home_craft_view(
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
    return handle_v05_home_craft_action(
        day_root,
        payload,
        has_subject_capability=(norm == "subject"),
        now_iso=now_iso,
    )


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "CRUMPLED_DRAFT_FORBIDDEN_EXACT_KEYS",
    "CRUMPLED_DRAFT_FORBIDDEN_SUBSTRINGS",
    "ENDPOINT_PATH",
    "FORBIDDEN_IDENTITY_KEYS",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUBJECT_FORBIDDEN_PARTNER_ONLY_ACTIONS",
    "SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "handle_v05_home_craft_action",
    "mutate_v05_home_craft_view",
    "read_v05_home_craft_view",
]
