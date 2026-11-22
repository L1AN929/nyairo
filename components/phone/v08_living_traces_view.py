#!/usr/bin/env python3
"""HTTP view and action gate for ``/api/phone/v08/living-traces``.

Responsibilities
----------------
1. ``read_v08_living_traces_view`` — 100% zero-write projection.  A partner view
   strips every ``visibility == "private"`` trace, inscription and routine.
2. ``mutate_v08_living_traces_view`` — strict ``ACTION_ALLOWED_FIELDS``
   whitelist, partner/subject permission boundary and identity-key refusal.
   Responses carry ``llm_calls == 0`` and never echo client identity.
3. ``summarize_living_traces_for_context`` — a bounded, zero-write summary for
   the lived-context bridge.  ``include_private=False`` never reads a private
   row in the first place.
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.world.scripts import v08_open_affordance as engine
except ImportError:  # pragma: no cover - flat world/scripts import path
    import v08_open_affordance as engine  # type: ignore[no-redef]

try:
    from components.world.scripts import v08_universal_matter as matter
except ImportError:  # pragma: no cover - flat world/scripts import path
    import v08_universal_matter as matter  # type: ignore[no-redef]

try:
    from components.world.scripts import v08_microzone_ecology as microzone
except ImportError:  # pragma: no cover - flat world/scripts import path
    import v08_microzone_ecology as microzone  # type: ignore[no-redef]

ROUTE_V08_LIVING_TRACES = "/api/phone/v08/living-traces"
ENDPOINT_PATH = ROUTE_V08_LIVING_TRACES
VIEW_SCHEMA = engine.VIEW_SCHEMA

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "leave_partner_trace",
    "clear_partner_trace",
    "partner_bring_item",
    "partner_move_shared_object",
    "partner_propose_use",
    "preview_placement",
    "adjust_microzone_comfort",
})

#: Actions either door may call.  Unlike the partner-own acts above, these must
#: keep the real door actor so a subject can preview a private place while the
#: partner door stays filtered.
_BOTH_SIDES_ACTIONS: frozenset[str] = frozenset({
    "preview_placement",
    "adjust_microzone_comfort",
})

SUBJECT_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | frozenset({
    "execute_universal_verb",
    "apply_living_trace",
    "clear_living_trace",
    "save_personal_routine",
    "run_personal_routine",
    "retire_personal_routine",
    "compose_open_act",
    "request_loan_extension",
    "respond_proposed_use",
    "name_personal_corner",
    "adjust_microzone_comfort",
})

#: Subject-only matter actions.  A partner-door attempt is a named 403 rather
#: than the generic actor-side refusal used by the older living-trace actions.
_SUBJECT_ONLY_MATTER_ACTIONS: frozenset[str] = frozenset({
    "compose_open_act",
    "request_loan_extension",
    "respond_proposed_use",
    "name_personal_corner",
})

_MATTER_ACTIONS: frozenset[str] = frozenset({
    "partner_bring_item",
    "partner_move_shared_object",
    "partner_propose_use",
    "compose_open_act",
    "request_loan_extension",
    "respond_proposed_use",
    "name_personal_corner",
})

_OPEN_ACT_OPS: frozenset[str] = frozenset(matter.OPEN_PRIMITIVES)

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
})

_TRACE_COMMON = frozenset({
    "object_id",
    "gesture_cn",
    "state_sticker_cn",
    "fade_mode",
    "ttl_minutes",
})

_ROUTINE_STEP_FIELDS: frozenset[str] = frozenset({
    "kind",
    "object_id",
    "verb",
    "gesture_cn",
    "state_sticker_cn",
    "target_spot",
    "setting_value",
    "note_text",
    "fade_mode",
    "ttl_minutes",
    "mode",
    "container_object_id",
    "outcome_note",
    "partner_object_id",
    "visibility",
    "strenuous_outing",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "leave_partner_trace": COMMON_COMMAND_FIELDS | _TRACE_COMMON,
    "clear_partner_trace": COMMON_COMMAND_FIELDS | frozenset({"trace_id"}),
    "execute_universal_verb": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "verb",
        "target_spot",
        "container_object_id",
        "mode",
        "note_text",
        "setting_value",
        "outcome_note",
        "partner_object_id",
        "visibility",
        "strenuous_outing",
    }),
    "apply_living_trace": COMMON_COMMAND_FIELDS | _TRACE_COMMON | frozenset({"visibility"}),
    "clear_living_trace": COMMON_COMMAND_FIELDS | frozenset({"trace_id"}),
    "save_personal_routine": COMMON_COMMAND_FIELDS | frozenset({
        "title_cn",
        "when_cue",
        "steps",
        "visibility",
    }),
    "run_personal_routine": COMMON_COMMAND_FIELDS | frozenset({"routine_id"}),
    "retire_personal_routine": COMMON_COMMAND_FIELDS | frozenset({"routine_id"}),
    "partner_bring_item": COMMON_COMMAND_FIELDS | frozenset({
        "label_cn",
        "item_kind",
        "target_parent_id",
        "portions",
        "cook_state",
        "thermal",
        "paper_like",
        "shelf_life_hours",
        "lent_to_subject",
        "inscribed_text_cn",
        "material_units",
        "assembly_role",
    }),
    "partner_move_shared_object": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "target_parent_id",
    }),
    "partner_propose_use": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "proposed_use_cn",
    }),
    "compose_open_act": COMMON_COMMAND_FIELDS | frozenset({
        "intent_cn",
        "steps",
        "stop_after_step",
        "allow_partial",
        "visibility",
        "soils_surface_id",
        "strenuous_outing",
        # Compositional micro-action carriage (Round C1/C2 parity): a caller may
        # carry the same bounded posture/association/arrangement vocabulary at
        # the top level as well as inside a step.
        "placement_relation",
        "posture_tag",
        "anchor_page",
        "arrangement_id",
        "zone_or_carrier_id",
        "member_ids",
        "pattern",
        "target_id",
        "text",
        "association_text",
    }),
    "respond_proposed_use": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "decision",
    }),
    "request_loan_extension": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "extra_hours",
        "note_cn",
    }),
    # Ch.16 §3 #13: the subject's own corner vocabulary for a real place.
    "name_personal_corner": COMMON_COMMAND_FIELDS | frozenset({
        "zone_id",
        "nickname_cn",
        "shared_with_partner",
        "clear",
    }),
    "preview_placement": COMMON_COMMAND_FIELDS | frozenset({
        "object_id",
        "target_parent_id",
        "label_cn",
        "item_kind",
        "paper_like",
        "shelf_life_hours",
    }),
    "adjust_microzone_comfort": COMMON_COMMAND_FIELDS | frozenset({
        "window_state",
        "lamp_on",
        "lamp_angle",
        "target_spot_id",
        "posture_cn",
        "preserve_work_ref",
        "preserve_breakpoint_cn",
        "visibility",
        "doorstep_sound_cue_cn",
    }),
}

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "leave_partner_trace": frozenset({"object_id", "gesture_cn", "state_sticker_cn"}),
    "clear_partner_trace": frozenset({"trace_id"}),
    "execute_universal_verb": frozenset({"object_id", "verb"}),
    "apply_living_trace": frozenset({"object_id", "gesture_cn", "state_sticker_cn"}),
    "clear_living_trace": frozenset({"trace_id"}),
    "save_personal_routine": frozenset({"title_cn", "steps"}),
    "run_personal_routine": frozenset({"routine_id"}),
    "retire_personal_routine": frozenset({"routine_id"}),
    "partner_bring_item": frozenset({"label_cn", "item_kind", "target_parent_id"}),
    "partner_move_shared_object": frozenset({"object_id", "target_parent_id"}),
    "partner_propose_use": frozenset({"object_id", "proposed_use_cn"}),
    "compose_open_act": frozenset({"intent_cn", "steps"}),
    "respond_proposed_use": frozenset({"object_id", "decision"}),
    "request_loan_extension": frozenset({"object_id"}),
    "name_personal_corner": frozenset({"zone_id"}),
    "preview_placement": frozenset({"target_parent_id"}),
    "adjust_microzone_comfort": frozenset(),
}

_PARTNER_ACTION_SET = PARTNER_ALLOWED_ACTIONS


def _error_response(
    status_code: int,
    reason_code: str,
    detail: str = "",
) -> tuple[int, dict[str, Any]]:
    code_str = str(reason_code)
    return int(status_code), {
        "ok": False,
        "status": "rejected" if status_code < 500 else "unavailable",
        "command_status": "REJECTED",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "reason_code": code_str,
        "error_code": code_str,
        "reason": code_str,
        "detail": str(detail or code_str),
        "llm_calls": 0,
        "data": {},
    }


def _fail(reason_code: str, detail: str = "") -> None:
    raise engine.V08OpenAffordanceError(reason_code, detail or reason_code)


class _PartnerActionRefused(engine.V08OpenAffordanceError):
    """A partner-door attempt at a subject-only matter action (always 403)."""

    def __init__(self, reason_code: str = "FORBIDDEN_PARTNER_ACTION", message: str = "") -> None:
        super().__init__(reason_code, message or reason_code)
        self.status_code = 403


def _validate_scalar_fields(action: str, payload: Mapping[str, Any]) -> None:
    for key in ("operation_key", "intent_fingerprint"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
            _fail("INVALID_FIELDS", f"{key} must be a trimmed non-empty string")

    if "ttl_minutes" in payload and payload["ttl_minutes"] is not None:
        value = payload["ttl_minutes"]
        if isinstance(value, bool) or not isinstance(value, int):
            _fail("INVALID_FIELDS", "ttl_minutes must be an integer")

    if "strenuous_outing" in payload and payload["strenuous_outing"] is not None:
        if not isinstance(payload["strenuous_outing"], bool):
            _fail("INVALID_FIELDS", "strenuous_outing must be a JSON boolean")

    if "fade_mode" in payload and payload["fade_mode"] is not None:
        if payload["fade_mode"] not in engine.FADE_MODES:
            _fail("INVALID_FIELDS", "fade_mode must be timed or until_touched")

    if "visibility" in payload and payload["visibility"] is not None:
        if payload["visibility"] not in engine.VISIBILITIES:
            _fail("INVALID_FIELDS", "visibility must be shared or private")

    if action == "execute_universal_verb":
        verb = payload.get("verb")
        if not isinstance(verb, str) or verb.strip().upper() not in engine.UNIVERSAL_VERBS:
            _fail("INVALID_FIELDS", "verb must be one of the six universal verbs")

    if action == "save_personal_routine":
        cue = payload.get("when_cue")
        if cue is not None and cue not in engine.WHEN_CUES:
            _fail("INVALID_FIELDS", "when_cue is not a known cue")

    for key in (
        "object_id",
        "gesture_cn",
        "state_sticker_cn",
        "trace_id",
        "title_cn",
        "routine_id",
        "note_text",
        "target_spot",
        "container_object_id",
        "outcome_note",
        "partner_object_id",
        "label_cn",
        "item_kind",
        "target_parent_id",
        "proposed_use_cn",
        "intent_cn",
        "decision",
        "soils_surface_id",
        "note_cn",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            _fail("INVALID_FIELDS", f"{key} must be a string")
        if not value.strip():
            _fail("INVALID_FIELDS", f"{key} must not be blank")

    mode = payload.get("mode")
    if mode is not None and (not isinstance(mode, str) or not mode.strip()):
        _fail("INVALID_FIELDS", "mode must be a non-empty string")

    # -- v0.8 universal matter fields --------------------------------------
    if action == "partner_bring_item":
        if payload.get("item_kind") not in matter.ITEM_KINDS:
            _fail("INVALID_FIELDS", "item_kind must be one of the real item kinds")
        if payload.get("cook_state") is not None and payload["cook_state"] not in matter.COOK_STATES:
            _fail("INVALID_FIELDS", "cook_state must be none, raw or cooked")
        if payload.get("thermal") is not None and payload["thermal"] not in matter.THERMAL_STATES:
            _fail("INVALID_FIELDS", "thermal must be a known thermal state")
        for key in ("paper_like", "lent_to_subject"):
            if payload.get(key) is not None and not isinstance(payload[key], bool):
                _fail("INVALID_FIELDS", f"{key} must be a JSON boolean")
        if payload.get("portions") is not None:
            value = payload["portions"]
            if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 99):
                _fail("INVALID_FIELDS", "portions must be an integer 1..99")
        if payload.get("shelf_life_hours") is not None:
            value = payload["shelf_life_hours"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail("INVALID_FIELDS", "shelf_life_hours must be a number")
        if payload.get("inscribed_text_cn") is not None:
            value = payload["inscribed_text_cn"]
            if not isinstance(value, str):
                _fail("INVALID_FIELDS", "inscribed_text_cn must be a string")
            if not value.strip():
                _fail("INVALID_FIELDS", "inscribed_text_cn must not be blank")
            if len(value) > 120:
                _fail("INVALID_FIELDS", "inscribed_text_cn exceeds 120 characters")
        if payload.get("material_units") is not None:
            value = payload["material_units"]
            if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 24):
                _fail("INVALID_FIELDS", "material_units must be an integer 1..24")
        if payload.get("assembly_role") is not None and payload["assembly_role"] not in matter.ASSEMBLY_ROLES:
            _fail("INVALID_FIELDS", "assembly_role must be none, host_frame or insert_part")

    if action == "compose_open_act":
        _validate_open_act_steps(payload.get("steps"))
        stop_after = payload.get("stop_after_step")
        if stop_after is not None:
            steps = payload.get("steps")
            upper = len(steps) if isinstance(steps, list) else 6
            if isinstance(stop_after, bool) or not isinstance(stop_after, int) or not (1 <= stop_after <= upper):
                _fail("INVALID_FIELDS", "stop_after_step must be an integer inside the step range")
        if payload.get("allow_partial") is not None and not isinstance(payload["allow_partial"], bool):
            _fail("INVALID_FIELDS", "allow_partial must be a JSON boolean")

    if action == "respond_proposed_use" and payload.get("decision") not in ("adopt", "decline"):
        _fail("INVALID_FIELDS", "decision must be adopt or decline")

    if action == "request_loan_extension" and payload.get("extra_hours") is not None:
        value = payload["extra_hours"]
        if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 168):
            _fail("INVALID_FIELDS", "extra_hours must be an integer 1..168")

    if action == "name_personal_corner":
        for key in ("shared_with_partner", "clear"):
            if payload.get(key) is not None and not isinstance(payload[key], bool):
                _fail("INVALID_FIELDS", f"{key} must be a JSON boolean")
        clearing = payload.get("clear") is True
        nickname = payload.get("nickname_cn")
        if not clearing:
            if not isinstance(nickname, str) or not nickname.strip():
                _fail("INVALID_FIELDS", "nickname_cn is required unless clear is true")
            if len(nickname.strip()) > matter.MAX_NICKNAME_CHARS:
                _fail("INVALID_FIELDS", "nickname_cn exceeds 24 characters")
        elif nickname is not None:
            if not isinstance(nickname, str):
                _fail("INVALID_FIELDS", "nickname_cn must be a string")

    if action == "preview_placement":
        if payload.get("item_kind") is not None and payload["item_kind"] not in matter.ITEM_KINDS:
            _fail("INVALID_FIELDS", "item_kind must be one of the real item kinds")
        if payload.get("paper_like") is not None and not isinstance(payload["paper_like"], bool):
            _fail("INVALID_FIELDS", "paper_like must be a JSON boolean")
        if payload.get("shelf_life_hours") is not None:
            value = payload["shelf_life_hours"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail("INVALID_FIELDS", "shelf_life_hours must be a number")


def _validate_steps(steps: Any) -> None:
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        _fail("INVALID_FIELDS", "steps must contain 2..4 objects")
    for idx, step in enumerate(steps):
        if not isinstance(step, Mapping) or not step:
            _fail("INVALID_FIELDS", f"steps[{idx}] must be a non-empty object")
        if set(step) & {"uid", "author_side", "by", "actor", "subject", "owner"}:
            _fail("CLIENT_IDENTITY_REFUSED", f"steps[{idx}] carries an identity key")
        unknown = sorted(set(step) - _ROUTINE_STEP_FIELDS)
        if unknown:
            _fail("INVALID_FIELDS", f"steps[{idx}] has unknown fields: {unknown}")
        kind = step.get("kind")
        if kind not in ("verb", "trace"):
            _fail("INVALID_FIELDS", f"steps[{idx}].kind must be verb or trace")
        object_id = step.get("object_id")
        if not isinstance(object_id, str) or not object_id.strip():
            _fail("INVALID_FIELDS", f"steps[{idx}].object_id is required")
        if kind == "verb":
            verb = step.get("verb")
            if not isinstance(verb, str) or verb.strip().upper() not in engine.UNIVERSAL_VERBS:
                _fail("INVALID_FIELDS", f"steps[{idx}].verb is not a universal verb")
        else:
            for key in ("gesture_cn", "state_sticker_cn"):
                if not isinstance(step.get(key), str) or not str(step.get(key)).strip():
                    _fail("INVALID_FIELDS", f"steps[{idx}].{key} is required")
            if step.get("fade_mode") is not None and step["fade_mode"] not in engine.FADE_MODES:
                _fail("INVALID_FIELDS", f"steps[{idx}].fade_mode is invalid")
            ttl = step.get("ttl_minutes")
            if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int)):
                _fail("INVALID_FIELDS", f"steps[{idx}].ttl_minutes must be an integer")


_OPEN_ACT_IDENTITY_KEYS = frozenset({
    "uid", "author_side", "by", "actor", "subject", "owner", "euid", "gid", "authority",
})


def _validate_open_act_steps(steps: Any) -> None:
    """Open acts are 1..6 primitives, each one of the real open operations.

    Deliberately distinct from the 2..4 routine-step shape so a single-step act
    or a longer cook/serve combination is not mistaken for a malformed routine.
    """

    if not isinstance(steps, list) or not (1 <= len(steps) <= 6):
        _fail("INVALID_FIELDS", "steps must contain 1..6 open-act objects")
    for idx, step in enumerate(steps):
        if not isinstance(step, Mapping) or not step:
            _fail("INVALID_FIELDS", f"steps[{idx}] must be a non-empty object")
        if set(step) & _OPEN_ACT_IDENTITY_KEYS:
            _fail("CLIENT_IDENTITY_REFUSED", f"steps[{idx}] carries an identity key")
        op = step.get("op")
        if not isinstance(op, str) or op.strip().upper() not in _OPEN_ACT_OPS:
            _fail("INVALID_FIELDS", f"steps[{idx}].op must be one of the open primitives")
        op = op.strip().upper()

        relation = step.get("placement_relation")
        if relation is not None and relation not in matter.PLACEMENT_RELATIONS:
            _fail("INVALID_FIELDS", f"steps[{idx}].placement_relation is not a known relation")
        posture = step.get("posture_tag")
        if posture is not None and posture not in matter.POSTURE_TAGS:
            _fail("INVALID_FIELDS", f"steps[{idx}].posture_tag is not a known posture")
        anchor = step.get("anchor_page")
        if anchor is not None:
            if isinstance(anchor, bool) or not isinstance(anchor, int) or not (1 <= anchor <= 2000):
                _fail("INVALID_FIELDS", f"steps[{idx}].anchor_page must be an integer 1..2000")
            if relation is not None and relation != "tucked_in":
                _fail("INVALID_FIELDS", f"steps[{idx}].anchor_page needs placement_relation tucked_in")
        if op == "ARRANGE_OBJECTS":
            if step.get("pattern") not in matter.ARRANGEMENT_PATTERNS:
                _fail("INVALID_FIELDS", f"steps[{idx}].pattern must be row, arc, stack or cluster")
            members = step.get("member_ids")
            if not isinstance(members, list) or not (2 <= len(members) <= 8):
                _fail("INVALID_FIELDS", f"steps[{idx}].member_ids must contain 2..8 object ids")
            cleaned = [str(item).strip() for item in members if isinstance(item, str) and str(item).strip()]
            if len(cleaned) != len(members) or len(set(cleaned)) != len(cleaned):
                _fail("INVALID_FIELDS", f"steps[{idx}].member_ids must be unique non-blank strings")
            zone = step.get("zone_or_carrier_id")
            if not isinstance(zone, str) or not zone.strip():
                _fail("INVALID_FIELDS", f"steps[{idx}].zone_or_carrier_id is required")
        if op == "NOTE_ASSOCIATION":
            target = step.get("target_id")
            if not isinstance(target, str) or not target.strip():
                _fail("INVALID_FIELDS", f"steps[{idx}].target_id is required")
            text = step.get("text") if step.get("text") is not None else step.get("association_text")
            if not isinstance(text, str) or not text.strip():
                _fail("INVALID_FIELDS", f"steps[{idx}].text is required")
            if len(text) > matter.MAX_ASSOCIATION_CHARS:
                _fail("INVALID_FIELDS", f"steps[{idx}].text exceeds 80 characters")


def _validate_request_payload(payload: Any, *, actor_side: str) -> tuple[str, str]:
    if actor_side not in ("partner", "subject"):
        _fail("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or not payload:
        _fail("INVALID_FIELDS", "payload must be a non-empty JSON object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            _fail("INVALID_FIELDS", "blank or padded key forbidden")

    identity = sorted(set(payload) & {"uid", "author_side", "by", "actor", "subject", "owner"})
    if identity:
        _fail("CLIENT_IDENTITY_REFUSED", "client identity keys are refused: " + ", ".join(identity))

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        _fail("INVALID_FIELDS", "Missing required field 'action'")
    action = action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        _fail("INVALID_FIELDS", f"Unknown action: {action!r}")

    unknown = sorted(set(payload) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        _fail("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    missing = sorted(
        key for key in _REQUIRED_FIELDS[action] if payload.get(key) is None
    )
    if missing:
        _fail("INVALID_FIELDS", f"Missing fields for {action}: {missing}")

    # Permission boundary: partner-only actions are always the partner's own act;
    # every other action needs a verified subject capability.  Actions open to
    # both doors keep whichever side actually called.
    if action in _BOTH_SIDES_ACTIONS:
        effective_actor = "partner" if actor_side == "partner" else "subject"
    elif action in _PARTNER_ACTION_SET:
        effective_actor = "partner"
    else:
        if actor_side != "subject":
            if action in _SUBJECT_ONLY_MATTER_ACTIONS:
                raise _PartnerActionRefused(
                    "FORBIDDEN_PARTNER_ACTION",
                    f"Action {action!r} is the subject's own act; the partner door cannot call it",
                )
            _fail("FORBIDDEN_ACTOR_SIDE", f"Action {action!r} requires the subject side")
        effective_actor = "subject"

    _validate_scalar_fields(action, payload)
    if action == "save_personal_routine":
        _validate_steps(payload.get("steps"))
    return action, effective_actor


def _compose_envelope(
    result: Mapping[str, Any],
    *,
    action: str,
    perspective: str,
) -> dict[str, Any]:
    data = copy.deepcopy(dict(result))
    data["action"] = action
    data["perspective"] = perspective
    data["llm_calls"] = 0
    already = bool(result.get("already_applied") is True) or str(result.get("status") or "") == "already_applied"
    raw_status = str(result.get("status") or "")
    if already:
        status = "already_applied"
        command_status = "DUPLICATE"
    elif "partial" in raw_status:
        status = "partial"
        command_status = "APPLIED"
    elif raw_status == "preview":
        status = "preview"
        command_status = "PREVIEW"
    else:
        status = "applied"
        command_status = "APPLIED"
    envelope: dict[str, Any] = {
        "ok": True,
        "status": status,
        "command_status": command_status,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "perspective": perspective,
        "action": action,
        "llm_calls": 0,
        "data": data,
    }
    for key, value in data.items():
        if key in ("status", "command_status", "ok", "schema", "endpoint", "perspective", "action", "llm_calls"):
            continue
        envelope[key] = value
    return envelope


_PRIVATE_STORAGE_STATUS_CN = "私人抽屉 · 不对外展开"
_EMPTY_STORAGE_STATUS_CN = "空置 · 当前没有物件"
_UNAVAILABLE_STORAGE_STATUS_CN = "暂不可读 · 绝不冒充空置"


def _structural_zone_catalog_zero_write(day_root: Path | str) -> dict[str, dict[str, Any]]:
    """Collect structural zone definitions in memory without touching disk."""
    zones: dict[str, dict[str, Any]] = {}
    try:
        for zid, zrow in (matter._default_zones() or {}).items():
            if isinstance(zrow, Mapping) and str(zid).strip():
                zones[str(zid)] = dict(zrow)
    except Exception:  # noqa: BLE001
        pass
    try:
        custom_zones, _ = matter._discover_custom_world(day_root)
        for zrow in custom_zones or []:
            if not isinstance(zrow, Mapping):
                continue
            zid = str(zrow.get("zone_id") or "").strip()
            if zid and zid not in zones:
                zones[zid] = dict(zrow)
    except Exception:  # noqa: BLE001
        pass
    return zones


def _probe_matter_space_availability_zero_write(day_root: Path | str) -> tuple[bool, Optional[Mapping[str, Any]]]:
    """Verify whether v08_matter_space.json is readable & well-formed (zero-write).

    An absent state file is normal (returns ``(True, None)``).  A symlink,
    unreadable path, corrupt JSON or malformed root/section returns ``(False, None)``
    so callers surface ``UNAVAILABLE`` instead of pretending the space is empty.
    """
    root = Path(day_root)
    try:
        if root.exists() and root.is_symlink():
            return False, None
        world_dir = root / matter.STATE_DIRNAME
        if world_dir.exists() and world_dir.is_symlink():
            return False, None
        path = matter.state_path(root)
        if path.is_symlink():
            return False, None
        if not path.exists():
            return True, None
        if not path.is_file():
            return False, None
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeError, ValueError, TypeError):
        return False, None
    if not isinstance(parsed, Mapping):
        return False, None
    if "zones" in parsed and not isinstance(parsed["zones"], Mapping):
        return False, None
    if "matter_objects" in parsed and not isinstance(parsed["matter_objects"], (Mapping, list)):
        return False, None
    if "objects" in parsed and not isinstance(parsed["objects"], (Mapping, list)):
        return False, None
    return True, parsed


def _build_unavailable_matter_contract(
    day_root: Path | str,
    *,
    viewer: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = _structural_zone_catalog_zero_write(day_root)
    boundary_rows: list[dict[str, Any]] = []
    for zid in sorted(catalog):
        zdef = catalog[zid]
        vis = "private" if str(zdef.get("visibility") or "shared") == "private" else "shared"
        boundary_rows.append({
            "zone_id": zid,
            "label_cn": str(zdef.get("label_cn") or zid),
            "visibility": vis,
            "storage_status": "UNAVAILABLE",
            "storage_status_cn": _UNAVAILABLE_STORAGE_STATUS_CN,
            "item_count": None,
            "visible_item_count": None,
            "updated_at": None,
        })
    space_doc: dict[str, Any] = {
        "schema": matter.VIEW_SCHEMA,
        "actor_side": viewer,
        "matter_space_read_status": "UNAVAILABLE",
        "storage_status": "UNAVAILABLE",
        "storage_status_cn": _UNAVAILABLE_STORAGE_STATUS_CN,
        "zones": [],
        "objects": [],
        "zone_boundary_states": copy.deepcopy(boundary_rows),
        "llm_calls": 0,
    }
    return space_doc, boundary_rows


def _attach_matter_storage_contract(
    day_root: Path | str,
    space: dict[str, Any],
    *,
    viewer: str,
    parsed_state: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Attach explicit PRIVATE vs EMPTY vs POPULATED storage states without leaking private counts."""
    visible_objects = [
        row for row in (space.get("objects") or []) if isinstance(row, Mapping)
    ]
    counts_by_parent: dict[str, int] = {}
    for obj in visible_objects:
        pid = str(obj.get("parent_id") or "")
        if pid:
            counts_by_parent[pid] = counts_by_parent.get(pid, 0) + 1

    projected_zones_by_id: dict[str, dict[str, Any]] = {}
    for zrow in space.get("zones") or []:
        if not isinstance(zrow, dict):
            continue
        zid = str(zrow.get("zone_id") or "")
        vis = str(zrow.get("visibility") or "shared")
        if vis == "private" and viewer != "subject":
            zrow["storage_status"] = "PRIVATE"
            zrow["storage_status_cn"] = _PRIVATE_STORAGE_STATUS_CN
            zrow["visible_item_count"] = None
            zrow["updated_at"] = None
        else:
            cnt = counts_by_parent.get(zid, 0)
            if cnt > 0:
                zrow["storage_status"] = "POPULATED"
                zrow["storage_status_cn"] = f"有物 · 当前放着 {cnt} 件"
            else:
                zrow["storage_status"] = "EMPTY"
                zrow["storage_status_cn"] = _EMPTY_STORAGE_STATUS_CN
            zrow["visible_item_count"] = cnt
        if zid:
            projected_zones_by_id[zid] = zrow

    catalog = _structural_zone_catalog_zero_write(day_root)
    if isinstance(parsed_state, Mapping) and isinstance(parsed_state.get("zones"), Mapping):
        for raw_zid, raw_zdef in parsed_state["zones"].items():
            zid_str = str(raw_zid or "").strip()
            if zid_str and isinstance(raw_zdef, Mapping) and zid_str not in catalog:
                catalog[zid_str] = {
                    "zone_id": zid_str,
                    "label_cn": str(raw_zdef.get("label_cn") or zid_str),
                    "visibility": str(raw_zdef.get("visibility") or "shared"),
                }
    for zid, zrow in projected_zones_by_id.items():
        if zid not in catalog:
            catalog[zid] = dict(zrow)

    boundary_rows: list[dict[str, Any]] = []
    for zid in sorted(catalog):
        zdef = catalog[zid]
        proj_z = projected_zones_by_id.get(zid)
        vis = str((proj_z or zdef).get("visibility") or "shared")
        if vis == "private" and viewer != "subject":
            # Zero count leak, zero timestamp leak on private storage for partner!
            boundary_rows.append({
                "zone_id": zid,
                "label_cn": str(zdef.get("label_cn") or zid),
                "visibility": "private",
                "storage_status": "PRIVATE",
                "storage_status_cn": _PRIVATE_STORAGE_STATUS_CN,
                "item_count": None,
                "visible_item_count": None,
                "updated_at": None,
            })
        else:
            cnt = counts_by_parent.get(zid, 0)
            status_code = "POPULATED" if cnt > 0 else "EMPTY"
            status_cn = (
                f"有物 · 当前放着 {cnt} 件" if cnt > 0 else _EMPTY_STORAGE_STATUS_CN
            )
            boundary_rows.append({
                "zone_id": zid,
                "label_cn": str((proj_z or zdef).get("label_cn") or zid),
                "visibility": "private" if vis == "private" else "shared",
                "storage_status": status_code,
                "storage_status_cn": status_cn,
                "item_count": cnt,
                "visible_item_count": cnt,
            })

    space["matter_space_read_status"] = "OK"
    space["zone_boundary_states"] = copy.deepcopy(boundary_rows)
    return boundary_rows


def read_v08_living_traces_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Read the living-traces projection (pure zero-write, partner view strips private)."""

    try:
        viewer = "subject" if has_subject_capability else "partner"
        projected = engine.read_living_traces_view(day_root, viewer=viewer, now_iso=now_iso)
        data_block = copy.deepcopy(projected)
        data_block["perspective"] = viewer
        data_block["llm_calls"] = 0
        # Universal matter is a strictly zero-write read alongside the traces;
        # the partner projection already strips private zones and objects, while
        # zone_boundary_states explicitly distinguishes PRIVATE vs EMPTY vs UNAVAILABLE.
        matter_ok, parsed_matter = _probe_matter_space_availability_zero_write(day_root)
        if not matter_ok:
            unavail_space, boundary_rows = _build_unavailable_matter_contract(
                day_root, viewer=viewer
            )
            data_block["matter_space"] = unavail_space
            data_block["matter_space_read_status"] = "UNAVAILABLE"
            data_block["zone_boundary_states"] = boundary_rows
        else:
            try:
                raw_space = matter.read_matter_space_view(
                    day_root, actor_side=viewer, now_iso=now_iso
                )
                boundary_rows = _attach_matter_storage_contract(
                    day_root, raw_space, viewer=viewer, parsed_state=parsed_matter
                )
                data_block["matter_space"] = raw_space
                data_block["matter_space_read_status"] = "OK"
                data_block["zone_boundary_states"] = boundary_rows
            except Exception:  # noqa: BLE001 - never pretend unreadable state is empty
                unavail_space, boundary_rows = _build_unavailable_matter_contract(
                    day_root, viewer=viewer
                )
                data_block["matter_space"] = unavail_space
                data_block["matter_space_read_status"] = "UNAVAILABLE"
                data_block["zone_boundary_states"] = boundary_rows
        # Ch.39 §二: a bag's contents inherit the bag's effective location.  The
        # summary is display-only Chinese, never a raw id token.
        data_block["carrier_summaries"] = carrier_travel_summaries(
            data_block.get("matter_space"), include_private=(viewer == "subject"),
        )
        # Micro-zone ecology is a pure zero-write projection of the real
        # weather, window/lamp state and warm food; private work stays hidden.
        try:
            data_block["microzone_ecology"] = microzone.read_microzone_ecology_view(
                day_root, actor_side=viewer, now_iso=now_iso
            )
        except Exception:  # noqa: BLE001 - a broken micro-zone state is simply absent
            data_block["microzone_ecology"] = None
        return 200, {
            "ok": True,
            "status": "ok",
            "command_status": "READ",
            "schema": VIEW_SCHEMA,
            "endpoint": ENDPOINT_PATH,
            "perspective": viewer,
            "llm_calls": 0,
            "data": data_block,
            **{key: value for key, value in data_block.items() if key != "data"},
        }
    except engine.V08OpenAffordanceError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v08_living_traces_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Apply one living-traces action behind the whitelist and permission boundary."""

    try:
        action, effective_actor = _validate_request_payload(payload, actor_side=actor_side)
        body = dict(payload)
        if action == "leave_partner_trace":
            result = engine.leave_partner_trace(day_root, body, now_iso=now_iso)
            perspective = "partner"
        elif action == "clear_partner_trace":
            result = engine.clear_partner_trace(day_root, body, now_iso=now_iso)
            perspective = "partner"
        elif action == "execute_universal_verb":
            result = engine.execute_universal_verb(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "apply_living_trace":
            result = engine.apply_living_trace(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "clear_living_trace":
            result = engine.clear_living_trace(day_root, body, now_iso=now_iso)
            perspective = "subject"
        elif action == "save_personal_routine":
            result = engine.save_personal_routine(day_root, body, now_iso=now_iso)
            perspective = "subject"
        elif action == "run_personal_routine":
            result = engine.run_personal_routine(day_root, body, now_iso=now_iso)
            perspective = "subject"
        elif action == "retire_personal_routine":
            result = engine.retire_personal_routine(day_root, body, now_iso=now_iso)
            perspective = "subject"
        elif action == "partner_bring_item":
            result = matter.partner_bring_item(day_root, body, now_iso=now_iso)
            perspective = "partner"
        elif action == "partner_move_shared_object":
            result = matter.partner_move_shared_object(day_root, body, now_iso=now_iso)
            perspective = "partner"
        elif action == "partner_propose_use":
            result = matter.partner_propose_object_use(day_root, body, now_iso=now_iso)
            perspective = "partner"
        elif action == "compose_open_act":
            result = matter.compose_open_act(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "preview_placement":
            result = dict(matter.preview_placement_zero_write(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            ))
            result["status"] = "preview"
            perspective = "subject" if effective_actor == "subject" else "partner"
        elif action == "adjust_microzone_comfort":
            result = microzone.adjust_microzone_comfort(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = effective_actor
        elif action == "request_loan_extension":
            result = matter.request_loan_extension(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        elif action == "name_personal_corner":
            result = matter.name_personal_corner(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"
        else:  # respond_proposed_use
            result = matter.respond_proposed_use(
                day_root, body, actor_side=effective_actor, now_iso=now_iso
            )
            perspective = "subject"

        if action in _MATTER_ACTIONS:
            # Attach a refreshed read-only snapshot so the model sees the new
            # physical state without a second round-trip.  Still zero-write.
            try:
                result = dict(result)
                raw_space = matter.read_matter_space_view(
                    day_root, actor_side=effective_actor, now_iso=now_iso
                )
                _, parsed_matter = _probe_matter_space_availability_zero_write(day_root)
                boundary_rows = _attach_matter_storage_contract(
                    day_root, raw_space, viewer=effective_actor, parsed_state=parsed_matter
                )
                result["matter_space"] = raw_space
                result["matter_space_read_status"] = "OK"
                result["zone_boundary_states"] = boundary_rows
                result["carrier_summaries"] = carrier_travel_summaries(
                    result["matter_space"],
                    include_private=(effective_actor == "subject"),
                )
            except Exception:  # noqa: BLE001 - the act already landed; snapshot is best-effort
                pass
        return 200, _compose_envelope(result, action=action, perspective=perspective)
    except (
        engine.V08OpenAffordanceError,
        matter.V08MatterError,
        microzone.V08MicrozoneError,
    ) as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


#: Human-facing names for the places a journey record can name.  A carrier
#: summary must never leak a raw ``place_id`` token.
_PLACE_SUMMARY_CN: dict[str, str] = {
    "home": "家里",
    "porch": "家门口",
    "yard": "小院",
    "street_corner": "街角",
    "bookstore": "街角旧书店",
    "riverside": "河边",
    "in_transit": "路上",
}


def carrier_travel_summaries(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool = True,
) -> list[dict[str, Any]]:
    """Chinese display summaries for carriers that hold things or travel.

    A bag's contents inherit the bag's effective location through the parent
    chain, so the summary only needs to name the carrier and how many things it
    holds.  Summaries are pure display: no raw ``snake_case`` id ever appears in
    ``summary_cn``.
    """

    if not isinstance(space, Mapping):
        return []
    objects = [row for row in (space.get("objects") or []) if isinstance(row, Mapping)]
    by_id = {str(row.get("object_id") or ""): row for row in objects}
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }

    def label(obj: Mapping[str, Any]) -> str:
        text = str(obj.get("label_cn") or "").strip()
        return text or "随手带的包"

    def is_private(obj: Mapping[str, Any]) -> bool:
        if str(obj.get("visibility") or "shared") == "private":
            return True
        parent = str(obj.get("parent_id") or "")
        zone = zones.get(parent)
        if zone is not None and str(zone.get("visibility") or "shared") == "private":
            return True
        carrier = by_id.get(parent)
        return bool(carrier is not None and str(carrier.get("visibility") or "shared") == "private")

    summaries: list[dict[str, Any]] = []
    for obj in objects:
        if not include_private and is_private(obj):
            continue
        children = obj.get("children_ids")
        if not isinstance(children, list) or not children:
            continue
        # Only real carriers get a travel summary.  Furniture/surfaces that
        # merely hold something on their outside (a bag leant on a chair) are
        # not carriers and must not be described as "containing" anything.
        if int(obj.get("capacity_slots") or 0) <= 0 and not bool(obj.get("carried_on_journey")):
            continue
        chain = [str(part) for part in (obj.get("carrier_chain") or []) if str(part or "").strip()]
        carried = bool(obj.get("carried_on_journey"))
        place = str(obj.get("effective_place_id") or "home")
        count = len(children)
        name = label(obj)
        if carried and place == "in_transit":
            text = f"{name}随身带着，里面的{count}件东西跟着你一起在路上"
        elif carried:
            place_cn = _PLACE_SUMMARY_CN.get(place, "外面")
            text = f"{name}和里面的{count}件东西跟着你到了{place_cn}"
        elif chain:
            outer = by_id.get(chain[-1])
            outer_name = label(outer) if outer is not None else "另一只容器"
            relation = str(obj.get("placement_relation") or "")
            if relation == "leaning_on":
                text = f"{name}靠在{outer_name}旁，里面装着{count}件东西"
            elif relation == "draped_on":
                text = f"{name}搭在{outer_name}上，里面装着{count}件东西"
            elif relation == "attached_to":
                text = f"{name}挂在{outer_name}上，里面装着{count}件东西"
            elif relation == "on":
                text = f"{name}放在{outer_name}上，里面装着{count}件东西"
            else:
                text = f"{name}收在{outer_name}里，里面装着{count}件东西"
        else:
            zone = zones.get(str(obj.get("effective_zone_id") or obj.get("parent_id") or ""))
            spot = str((zone or {}).get("label_cn") or "手边")
            text = f"{name}在{spot}，里面装着{count}件东西"
        summaries.append({
            "carrier_id": str(obj.get("object_id") or ""),
            "summary_cn": text,
            "private": is_private(obj),
            "carried_on_journey": carried,
            "effective_place_id": place,
            "effective_zone_id": obj.get("effective_zone_id"),
        })
    return summaries


def _matter_hints(
    space: Optional[Mapping[str, Any]],
    *,
    include_private: bool,
    include_scaffold_cues: bool = True,
) -> list[dict[str, Any]]:
    """At most three short, objective hints derived from live physical state.

    ``include_scaffold_cues`` is False for the lived-context summary when a
    structured day root (``world/`` present) has no ``world/v08_matter_space.json``
    on disk yet: the in-memory default desk must never seed "ready workspace" /
    "familiar spot empty" hints, so a quiet room with only a micro-zone file stays
    byte-identical to having no matter layer at all.  The real read views keep
    projecting those cues unchanged.
    """

    if not isinstance(space, Mapping):
        return []
    objects = [row for row in (space.get("objects") or []) if isinstance(row, Mapping)]
    zones = {
        str(row.get("zone_id")): row
        for row in (space.get("zones") or [])
        if isinstance(row, Mapping)
    }

    def visible(obj: Mapping[str, Any]) -> bool:
        if include_private:
            return True
        if str(obj.get("visibility") or "shared") == "private":
            return False
        parent = zones.get(str(obj.get("parent_id") or ""))
        return not (parent is not None and str(parent.get("visibility") or "shared") == "private")

    def is_private(obj: Mapping[str, Any]) -> bool:
        if str(obj.get("visibility") or "shared") == "private":
            return True
        parent = zones.get(str(obj.get("parent_id") or ""))
        return bool(parent is not None and str(parent.get("visibility") or "shared") == "private")

    def label(obj: Mapping[str, Any]) -> str:
        return str(obj.get("label_cn") or obj.get("object_id") or "物件")

    def where(obj: Mapping[str, Any]) -> str:
        parent = zones.get(str(obj.get("parent_id") or ""))
        return str((parent or {}).get("label_cn") or "手边")

    hints: list[dict[str, Any]] = []

    # 1) Ready food worth noticing (thermal state is objective, never a command).
    for obj in objects:
        if not visible(obj) or str(obj.get("item_kind")) != "prepared_food":
            continue
        portions = obj.get("portions_left")
        if not isinstance(portions, int) or portions <= 0:
            continue
        if str(obj.get("freshness") or "") == "spoiled":
            continue
        thermal = str(obj.get("thermal") or "room")
        if thermal == "hot":
            state = "还热乎"
        elif thermal in ("cold", "cool"):
            state = "已放凉"
        else:
            state = ""
        text = f"{where(obj)}的{label(obj)}" + (f"{state}，" if state else "") + f"还能吃，剩{portions}份"
        hints.append({"text_cn": text, "private": is_private(obj)})

    # 2) A used vessel waiting to be washed.
    for obj in objects:
        if not visible(obj):
            continue
        if str(obj.get("item_kind")) == "vessel" and str(obj.get("cleanliness") or "") == "used":
            hints.append({"text_cn": f"{label(obj)}用过还没洗", "private": is_private(obj)})
            break

    # 3) Something moved while the subject was not looking.
    for obj in objects:
        if not visible(obj):
            continue
        if bool(obj.get("impression_stale")):
            hints.append({"text_cn": f"{label(obj)}被挪过，还没重新看一眼", "private": is_private(obj)})
            break

    # 4) A partner's pending use proposal.
    for obj in objects:
        if not visible(obj):
            continue
        proposal = obj.get("proposed_use")
        if isinstance(proposal, Mapping) and str(proposal.get("status") or "") == "proposed":
            text = str(proposal.get("proposed_use_cn") or "").strip()
            hint = f"伴侣提议拿{label(obj)}来{text}" if text else f"伴侣对{label(obj)}有个用法提议"
            hints.append({"text_cn": hint, "private": is_private(obj)})
            break

    # 4b) An attributed personal association.  It is always labelled as the
    #     author's own note/thought -- never as objective world state -- and it
    #     never changes what the object physically is or can do.
    for obj in objects:
        if not visible(obj):
            continue
        association = obj.get("subject_association")
        if not isinstance(association, Mapping):
            continue
        text = str(association.get("text") or "").strip()
        if not text:
            continue
        by = str(association.get("by") or "")
        if by == "subject":
            prefix = "自己记的"
        elif by == "partner":
            prefix = "伴侣记的"
        else:
            prefix = "有人记的"
        hints.append({
            "text_cn": f"{where(obj)}的{label(obj)}，{prefix}：「{text}」",
            "private": is_private(obj),
        })
        break

    # 5) Craft in progress: an oversized insert waiting, or stock worth splitting.
    for obj in objects:
        if not visible(obj) or str(obj.get("fit_status") or "") != "oversized_unfitted":
            continue
        hints.append({
            "text_cn": f"{where(obj)}的{label(obj)}偏大，还没装进去，可以修整再试",
            "private": is_private(obj),
        })
        break
    for obj in objects:
        if not visible(obj):
            continue
        if int(obj.get("material_units") or 1) < 2:
            continue
        if not (bool(obj.get("paper_like")) or str(obj.get("item_kind")) == "paper"):
            continue
        hints.append({"text_cn": f"{where(obj)}的{label(obj)}还能分出一小块", "private": is_private(obj)})
        break

    # 5b) A carrier that holds things and/or is travelling with them.
    for summary in carrier_travel_summaries(space, include_private=include_private):
        hints.append({
            "text_cn": str(summary.get("summary_cn") or ""),
            "private": bool(summary.get("private")),
        })
        break

    # 6) Ch.16: one calm, physically derived workspace scaffold, then one calm
    #    absence cue -- only when the expected spot was really witnessed empty.
    if include_scaffold_cues:
        for scaffold in space.get("workspace_scaffolds") or []:
            if not isinstance(scaffold, Mapping):
                continue
            text = str(scaffold.get("summary_cn") or "").strip()
            if not text:
                continue
            hints.append({"text_cn": text, "private": False})
            break
        for cue in space.get("familiar_absence_cues") or []:
            if not isinstance(cue, Mapping):
                continue
            text = str(cue.get("cue_cn") or "").strip()
            if not text:
                continue
            zone = zones.get(str(cue.get("expected_zone_id") or ""))
            private = bool(zone is not None and str(zone.get("visibility") or "shared") == "private")
            hints.append({"text_cn": text, "private": private})
            break

    return hints[:3]


def summarize_living_traces_for_context(
    day_root: Path | str,
    *,
    include_private: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bounded zero-write summary for the lived-context bridge.

    ``include_private=False`` reads the partner projection, so a private trace,
    inscription or routine is never even materialised here.
    """

    summary: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "perspective": "subject" if include_private else "partner",
        "active_trace_count": 0,
        "traces": [],
        "routines": [],
        "matter_hints": [],
        "llm_calls": 0,
    }
    try:
        view = engine.read_living_traces_view(
            day_root,
            viewer="subject" if include_private else "partner",
            now_iso=now_iso,
        )
    except Exception:  # noqa: BLE001 - a broken state is an empty, honest summary
        return summary

    traces = [row for row in view.get("active_traces") or [] if isinstance(row, Mapping)]
    traces.sort(
        key=lambda row: (
            0 if str(row.get("author_side") or "") == "partner" else 1,
            str(row.get("created_at") or ""),
        )
    )
    summary["active_trace_count"] = len(traces)
    for row in traces[:3]:
        summary["traces"].append({
            "trace_id": row.get("trace_id"),
            "object_id": row.get("object_id"),
            "object_label_cn": row.get("object_label_cn"),
            "author_side": row.get("author_side"),
            "gesture_cn": row.get("gesture_cn"),
            "state_sticker_cn": row.get("state_sticker_cn"),
            "visibility": row.get("visibility") or "shared",
            "fade_mode": row.get("fade_mode"),
            "remaining_minutes": row.get("remaining_minutes"),
        })

    routines = [row for row in view.get("routines") or [] if isinstance(row, Mapping)]
    summary["routine_count"] = len(routines)
    for row in routines[:3]:
        summary["routines"].append({
            "routine_id": row.get("routine_id"),
            "title_cn": row.get("title_cn"),
            "when_cue": row.get("when_cue"),
            "visibility": row.get("visibility") or "shared",
            "times_used": int(row.get("times_used") or 0),
        })
    try:
        space = matter.read_matter_space_view(
            day_root,
            actor_side="subject" if include_private else "partner",
            now_iso=now_iso,
        )
    except Exception:  # noqa: BLE001 - a broken matter file yields no hints
        space = None
    # Background-silence seam: a structured day root whose physical layer has not
    # been written yet (no ``world/v08_matter_space.json``) must not seed the
    # in-memory default desk's scaffold / familiar-absence hints, so a room
    # declared only by ``v08_microzone_state.json`` stays as silent as an absent
    # one.  A root carrying a real matter file, or a bare legacy root without the
    # ``world/`` day-root structure, keeps projecting the live scaffold cues.
    try:
        has_matter_state = bool(matter._state_file_exists(day_root))
        structured_root = (Path(day_root) / "world").is_dir()
    except Exception:  # noqa: BLE001 - an unreadable path is simply absent
        has_matter_state = False
        structured_root = False
    summary["matter_hints"] = _matter_hints(
        space,
        include_private=include_private,
        include_scaffold_cues=has_matter_state or not structured_root,
    )
    return summary


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "ROUTE_V08_LIVING_TRACES",
    "SUBJECT_ALLOWED_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v08_living_traces_view",
    "read_v08_living_traces_view",
    "summarize_living_traces_for_context",
]
