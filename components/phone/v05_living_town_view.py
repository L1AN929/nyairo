#!/usr/bin/env python3
"""HTTP view & action gate for ``/api/phone/v05/living-town`` (v0.5 Ch. 117-129).

Contracts:
1. ``read_v05_living_town_view``:
   - 100% zero-write on empty, non-existent, or populated ``day_root``;
   - Returns HTTP 200 envelope (mirrored top-level and ``data``) containing
     ``elevation_map``, ``overlook_sightlines``, ``ground_strata``, ``alley_kitten``,
     ``rare_spectacle``, ``third_spaces``, ``regulars``, ``chalkboard_and_favors``,
     ``partner_presence``, ``ta_presence``, ``umbrella_reunion``, ``world_context``,
     ``allowed_actions``, ``llm_calls=0``, ``zero_write=True``.
2. ``handle_v05_living_town_action`` / ``mutate_v05_living_town_view``:
   - Strict per-action field allowlists and recursive forbidden identity/score key scan
     (``400 INVALID_FIELDS``);
   - Strict role gate (``403 FORBIDDEN_ACTOR_SIDE``) for subject-only actions
     (``set_subject_town_state``, ``advance_town_pulse``).
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
from components.world.scripts import v05_living_town_engine as lt_engine

ENDPOINT_PATH = "/api/phone/v05/living-town"
VIEW_SCHEMA = "nyairo.v05.living_town.v1"

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "author_side",
    "left_by_side",
    "actor",
    "role",
    "caller",
    "caller_side",
    "is_subject",
    "subject_capability",
    "has_subject_capability",
    "visitor_side",
    "perspective",
    "intimacy_score",
    "affection_score",
    "favorability_score",
    "mood_score",
    "bond_score",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "move_partner_to_spot",
    "invite_walk_together",
    "visit_third_space",
    "log_neighbor_favor",
    "claim_surplus_bag",
    "greet_or_feed_kitten",
    "leave_drifting_book",
    "borrow_or_annotate_drifting_book",
    "post_bulletin_followup",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "set_subject_town_state",
    "advance_town_pulse",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "move_partner_to_spot": frozenset({
        "action",
        "target_spot_id",
        "spot_id",
        "bringing_umbrella",
        "carried_heavy_count",
        "companion_note",
        "carried_kind",
        "operation_key",
    }),
    "invite_walk_together": frozenset({
        "action",
        "target_spot_id",
        "spot_id",
        "bringing_umbrella",
        "carried_heavy_count",
        "companion_note",
        "operation_key",
    }),
    "visit_third_space": frozenset({
        "action",
        "spot_id",
        "garment_name_cn",
        "carried_kind",
        "note_cn",
        "operation_key",
    }),
    "log_neighbor_favor": frozenset({
        "action",
        "regular_id",
        "favor_kind",
        "note_cn",
        "operation_key",
    }),
    "claim_surplus_bag": frozenset({
        "action",
        "operation_key",
    }),
    "greet_or_feed_kitten": frozenset({
        "action",
        "interaction_kind",
        "spot_id",
        "nickname",
        "note_cn",
        "operation_key",
    }),
    "set_subject_town_state": frozenset({
        "action",
        "ta_location_id",
        "solo_walk_lock",
        "resting",
        "sick",
        "refusal_lock_active",
        "returning_home_progress",
        "companion_note",
        "neighbor_encounter",
        "third_space_trace",
        "operation_key",
    }),
    "advance_town_pulse": frozenset({
        "action",
        "elapsed_hours",
        "weather_override",
        "days_lived_override",
        "yesterday_gale",
        "rainy_streak_days",
        "operation_key",
    }),
    "leave_drifting_book": frozenset({
        "action",
        "book_title",
        "slip_note_cn",
        "spot_id",
        "operation_key",
    }),
    "borrow_or_annotate_drifting_book": frozenset({
        "action",
        "book_id",
        "op",
        "margin_note_cn",
        "operation_key",
    }),
    "post_bulletin_followup": frozenset({
        "action",
        "notice_id",
        "title_cn",
        "followup_cn",
        "operation_key",
    }),
}

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "revision",
    "observed_at",
    "world_context",
    "season_solar",
    "elevation_map",
    "overlook_sightlines",
    "ground_strata",
    "alley_kitten",
    "rare_spectacle",
    "third_spaces",
    "regulars",
    "chalkboard_and_favors",
    "partner_presence",
    "ta_presence",
    "umbrella_reunion",
    "sound_scent_map",
    "drifting_bookbox",
    "bulletin_followups",
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
        raise lt_engine.V05LivingTownError(400, "INVALID_FIELDS", "Payload nesting depth exceeded")
    if isinstance(node, Mapping):
        for raw_key, raw_val in node.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise lt_engine.V05LivingTownError(
                    400,
                    "INVALID_FIELDS",
                    f"Blank or whitespace-padded key forbidden: {raw_key!r}",
                )
            low = raw_key.lower()
            if raw_key in FORBIDDEN_IDENTITY_KEYS or low in FORBIDDEN_IDENTITY_KEYS:
                raise lt_engine.V05LivingTownError(
                    400,
                    "INVALID_FIELDS",
                    f"Forged identity or forbidden field rejected: {raw_key!r}",
                )
            _scan_payload_keys_recursive(raw_val, depth=depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_payload_keys_recursive(item, depth=depth + 1)


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
    """Filter all living-town item collections and featured refs through the zero-write forget barrier."""
    out = copy.deepcopy(dict(snapshot))
    fail_closed = bool(barrier.get("fail_closed"))

    for list_key in ("regulars", "drifting_bookbox", "bulletin_followups", "sound_scent_map", "encounters"):
        if isinstance(out.get(list_key), list):
            out[list_key] = _rlf.filter_collection_by_barrier(out[list_key], barrier)

    caf = out.get("chalkboard_and_favors")
    if isinstance(caf, dict):
        for sub_key in ("recent_favors", "active_courtesies", "encounters", "notices"):
            if isinstance(caf.get(sub_key), list):
                caf[sub_key] = _rlf.filter_collection_by_barrier(caf[sub_key], barrier)

    ts = out.get("third_spaces")
    if isinstance(ts, dict):
        if isinstance(ts.get("recent_third_space_visits"), list):
            ts["recent_third_space_visits"] = _rlf.filter_collection_by_barrier(
                ts["recent_third_space_visits"], barrier
            )
        overlook = ts.get("hillside_overlook")
        if isinstance(overlook, dict) and isinstance(overlook.get("visible_landmarks"), list):
            overlook["visible_landmarks"] = _rlf.filter_collection_by_barrier(
                overlook["visible_landmarks"], barrier
            )
        laund = ts.get("coin_laundromat")
        if isinstance(laund, dict) and laund.get("last_pocket_find") is not None:
            if fail_closed or _rlf.is_entity_forget_blocked(laund["last_pocket_find"], barrier):
                laund["last_pocket_find"] = None

    sight = out.get("overlook_sightlines")
    if isinstance(sight, dict) and isinstance(sight.get("visible_landmarks"), list):
        sight["visible_landmarks"] = _rlf.filter_collection_by_barrier(
            sight["visible_landmarks"], barrier
        )

    strata = out.get("ground_strata")
    if isinstance(strata, dict) and isinstance(strata.get("grove_visits"), list):
        strata["grove_visits"] = _rlf.filter_collection_by_barrier(
            strata["grove_visits"], barrier
        )

    emap = out.get("elevation_map")
    if isinstance(emap, dict) and isinstance(emap.get("discovered_shortcuts"), list):
        emap["discovered_shortcuts"] = _rlf.filter_collection_by_barrier(
            emap["discovered_shortcuts"], barrier
        )

    if out.get("rare_spectacle") is not None:
        if fail_closed or _rlf.is_entity_forget_blocked(out["rare_spectacle"], barrier):
            out["rare_spectacle"] = None

    for ref_key in ("partner_presence", "ta_presence", "umbrella_reunion", "alley_kitten"):
        sub = out.get(ref_key)
        if isinstance(sub, dict) and (fail_closed or _rlf.is_entity_forget_blocked(sub, barrier)):
            for txt_key in ("companion_note", "note_cn", "last_seen_note"):
                if txt_key in sub:
                    sub[txt_key] = ""
            for id_key in ("encounter_id", "trace_id", "event_id", "source_id"):
                if id_key in sub:
                    sub[id_key] = None
    return out


def read_v05_living_town_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read v0.5 Living Town snapshot with 100% zero disk writes and unified forget barrier enforcement."""
    try:
        barrier = _rlf.load_unified_forget_barrier_readonly(day_root)
        snap = _apply_forget_barrier(
            lt_engine.read_living_town_snapshot(day_root, now_iso=now_iso),
            barrier,
        )
        return 200, _compose_envelope(
            snap,
            has_subject_capability=bool(has_subject_capability),
            read_only=True,
            command_status="READ",
        )
    except lt_engine.V05LivingTownError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail)


def handle_v05_living_town_action(
    day_root: Path | str,
    payload: Any,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Validate strict field allowlist & role boundaries, then mutate living-town state."""
    actor_side = "subject" if bool(has_subject_capability) else "partner"
    if not isinstance(payload, Mapping) or len(payload) == 0:
        return _error_response(400, "INVALID_FIELDS", "Payload must be a non-empty JSON object")
    try:
        _scan_payload_keys_recursive(payload)
        raw_action = payload.get("action")
        if not isinstance(raw_action, str) or not raw_action.strip() or raw_action != raw_action.strip():
            raise lt_engine.V05LivingTownError(400, "INVALID_FIELDS", "Field 'action' must be a non-empty string")
        action = raw_action
        if action not in ACTION_ALLOWED_FIELDS:
            raise lt_engine.V05LivingTownError(400, "INVALID_FIELDS", f"Unknown action: {action!r}")
        allowed_actions = SUBJECT_ALLOWED_ACTIONS if has_subject_capability else PARTNER_ALLOWED_ACTIONS
        if action not in allowed_actions:
            return _error_response(
                403,
                "FORBIDDEN_ACTOR_SIDE",
                f"Action {action!r} is subject-only and forbidden for actor_side={actor_side!r}",
            )
        unknown = sorted(set(payload.keys()) - ACTION_ALLOWED_FIELDS[action])
        if unknown:
            raise lt_engine.V05LivingTownError(
                400,
                "INVALID_FIELDS",
                f"Unknown fields for {action!r}: {unknown}",
            )

        receipt: dict[str, Any]
        if action == "move_partner_to_spot":
            target = payload.get("target_spot_id") or payload.get("spot_id") or ""
            receipt = lt_engine.move_partner_to_spot(
                day_root,
                target_spot_id=str(target),
                bringing_umbrella=bool(payload.get("bringing_umbrella", False)),
                carried_heavy_count=int(payload.get("carried_heavy_count", 0) or 0),
                companion_note=str(payload.get("companion_note") or ""),
                carried_kind=str(payload.get("carried_kind") or "empty"),
                now_iso=now_iso,
            )
        elif action == "invite_walk_together":
            target = payload.get("target_spot_id") or payload.get("spot_id") or ""
            receipt = lt_engine.invite_walk_together(
                day_root,
                target_spot_id=str(target),
                bringing_umbrella=bool(payload.get("bringing_umbrella", False)),
                carried_heavy_count=int(payload.get("carried_heavy_count", 0) or 0),
                companion_note=str(payload.get("companion_note") or ""),
                now_iso=now_iso,
            )
        elif action == "visit_third_space":
            receipt = lt_engine.visit_third_space(
                day_root,
                spot_id=str(payload.get("spot_id") or ""),
                visitor_side=actor_side,
                garment_name_cn=str(payload.get("garment_name_cn") or "棉布外套"),
                carried_kind=str(payload.get("carried_kind") or "empty"),
                note_cn=str(payload.get("note_cn") or ""),
                now_iso=now_iso,
            )
        elif action == "log_neighbor_favor":
            receipt = lt_engine.log_neighbor_favor(
                day_root,
                regular_id=str(payload.get("regular_id") or ""),
                favor_kind=str(payload.get("favor_kind") or "return_umbrella"),
                note_cn=str(payload.get("note_cn") or ""),
                actor_side=actor_side,
                now_iso=now_iso,
            )
        elif action == "claim_surplus_bag":
            receipt = lt_engine.claim_surplus_bag(
                day_root,
                actor_side=actor_side,
                now_iso=now_iso,
            )
        elif action == "greet_or_feed_kitten":
            receipt = lt_engine.greet_or_feed_kitten(
                day_root,
                interaction_kind=str(payload.get("interaction_kind") or "feed"),
                spot_id=(str(payload["spot_id"]) if payload.get("spot_id") is not None else None),
                nickname=(str(payload["nickname"]) if payload.get("nickname") is not None else None),
                note_cn=str(payload.get("note_cn") or ""),
                actor_side=actor_side,
                now_iso=now_iso,
            )
        elif action == "set_subject_town_state":
            receipt = lt_engine.set_subject_town_state(
                day_root,
                ta_location_id=payload.get("ta_location_id"),
                solo_walk_lock=payload.get("solo_walk_lock"),
                resting=payload.get("resting"),
                sick=payload.get("sick"),
                refusal_lock_active=payload.get("refusal_lock_active"),
                returning_home_progress=payload.get("returning_home_progress"),
                companion_note=payload.get("companion_note"),
                neighbor_encounter=payload.get("neighbor_encounter"),
                third_space_trace=payload.get("third_space_trace"),
                now_iso=now_iso,
            )
        elif action == "advance_town_pulse":
            receipt = lt_engine.advance_town_pulse(
                day_root,
                elapsed_hours=float(payload.get("elapsed_hours", 1.0) if payload.get("elapsed_hours") is not None else 1.0),
                weather_override=payload.get("weather_override"),
                days_lived_override=payload.get("days_lived_override"),
                yesterday_gale=payload.get("yesterday_gale"),
                rainy_streak_days=payload.get("rainy_streak_days"),
                now_iso=now_iso,
            )
        elif action == "leave_drifting_book":
            receipt = lt_engine.leave_drifting_book(
                day_root,
                book_title=payload.get("book_title"),
                slip_note_cn=payload.get("slip_note_cn"),
                spot_id=payload.get("spot_id"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "borrow_or_annotate_drifting_book":
            receipt = lt_engine.borrow_or_annotate_drifting_book(
                day_root,
                book_id=payload.get("book_id"),
                op=payload.get("op"),
                margin_note_cn=payload.get("margin_note_cn", ""),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        elif action == "post_bulletin_followup":
            receipt = lt_engine.post_bulletin_followup(
                day_root,
                notice_id=payload.get("notice_id"),
                title_cn=payload.get("title_cn"),
                followup_cn=payload.get("followup_cn"),
                author_side=actor_side,
                operation_key=payload.get("operation_key"),
                now_iso=now_iso,
            )
        else:
            raise lt_engine.V05LivingTownError(400, "INVALID_FIELDS", f"Unsupported action: {action!r}")

        snap = lt_engine.read_living_town_snapshot(day_root, now_iso=now_iso)
        return 200, _compose_envelope(
            snap,
            has_subject_capability=bool(has_subject_capability),
            read_only=False,
            command_status="APPLIED",
            action_receipt=receipt,
        )
    except lt_engine.V05LivingTownError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.detail)
    except (TypeError, ValueError) as exc:
        return _error_response(400, "INVALID_FIELDS", str(exc))


def mutate_v05_living_town_view(
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
    return handle_v05_living_town_action(
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
    "handle_v05_living_town_action",
    "mutate_v05_living_town_view",
    "read_v05_living_town_view",
]
