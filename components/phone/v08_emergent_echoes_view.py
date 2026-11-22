#!/usr/bin/env python3
"""HTTP 视图与动作闸门：``/api/phone/v08/emergent-echoes``。

涌现回响的五个零大模型小原语（给未来自己的问题、只有彼此懂的说法、
同一件往事的不同理解、兴趣迁移火花、两个人带回不一样的注意）。

职责与边界：
1. ``read_v08_emergent_echoes_view``：100% 零磁盘写入；伴侣视角过滤所有
   ``visibility == "private"`` 的问题/火花与未共享的往事重解，绝不泄露隐藏计数。
2. ``mutate_v08_emergent_echoes_view``：严格 ``ACTION_ALLOWED_FIELDS`` 白名单、
   身份/分数键拒绝、主体动作与伴侣动作权限边界、``operation_key`` 幂等重放。
3. ``summarize_emergent_echoes_for_context``：供聊天背景桥接的零写有界字符串摘要；
   ``include_private=False`` 时私密内容根本不会被读出。
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.life.scripts import v08_emergent_echoes as engine
except ImportError:  # pragma: no cover - flat life/scripts import path
    import v08_emergent_echoes as engine  # type: ignore[no-redef]

ROUTE_V08_EMERGENT_ECHOES = "/api/phone/v08/emergent-echoes"
ENDPOINT_PATH = ROUTE_V08_EMERGENT_ECHOES
VIEW_SCHEMA = engine.VIEW_SCHEMA

COMMON_COMMAND_FIELDS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "intent_fingerprint",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = engine.PARTNER_ALLOWED_ACTIONS
SUBJECT_ALLOWED_ACTIONS: frozenset[str] = engine.KNOWN_ACTIONS

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "plant_future_question": COMMON_COMMAND_FIELDS | frozenset({
        "question_cn",
        "anchor_kind",
        "anchor_ref",
        "keywords",
        "visibility",
    }),
    "revisit_future_question": COMMON_COMMAND_FIELDS | frozenset({
        "question_id",
        "resolution",
        "note_cn",
    }),
    "propose_shared_idiom": COMMON_COMMAND_FIELDS | frozenset({
        "phrase",
        "meaning_cn",
        "origin_summary_cn",
        "origin_ref",
    }),
    "confirm_shared_idiom": COMMON_COMMAND_FIELDS | frozenset({"idiom_id"}),
    "fade_shared_idiom": COMMON_COMMAND_FIELDS | frozenset({"idiom_id"}),
    "record_memory_reinterpretation": COMMON_COMMAND_FIELDS | frozenset({
        "episode_ref",
        "episode_title_cn",
        "original_fact_cn",
        "prior_view_cn",
        "new_view_cn",
        "trigger_summary_cn",
        "shared_with_partner",
        # Ch.23: a partial reinterpretation keeps the old reservation and names
        # the one new facet; an unconfirmed reading stays a hypothesis; a page is
        # only ever recorded when the caller supplies the real one.
        "preserved_reservation_cn",
        "appreciation_facet_cn",
        "hypothesis_status",
        "exact_page_ref",
    }),
    "share_memory_reinterpretation": COMMON_COMMAND_FIELDS | frozenset({
        "reinterpretation_id",
        "shared_with_partner",
    }),
    "record_cross_domain_spark": COMMON_COMMAND_FIELDS | frozenset({
        "source_domain",
        "source_observation_cn",
        "target_domain",
        "try_method_cn",
        "familiarity",
        "craft_fit",
        "mood_pull_today",
        "visibility",
    }),
    "settle_spark_attempt": COMMON_COMMAND_FIELDS | frozenset({
        "spark_id",
        "outcome",
        "choice_trace_cn",
    }),
    "subject_add_divergent_notice": COMMON_COMMAND_FIELDS | frozenset({
        "target_ref",
        "target_title_cn",
        "notice_cn",
    }),
    "partner_add_divergent_notice": COMMON_COMMAND_FIELDS | frozenset({
        "target_ref",
        "target_title_cn",
        "notice_cn",
    }),
    "sketch_day_direction": COMMON_COMMAND_FIELDS | frozenset({
        "horizon_note_cn",
        "next_decision_boundary_cn",
        "bound_ref",
        "visibility",
    }),
    "advance_or_interrupt_direction": COMMON_COMMAND_FIELDS | frozenset({
        "direction_id",
        "transition",
        "breakpoint_note_cn",
        "evidence_ref",
        "linked_object_ids",
        "linked_zone_id",
        "direction_hint",
    }),
    "note_lingering_thread": COMMON_COMMAND_FIELDS | frozenset({
        "summary_cn",
        "scope_kind",
        "scope_ref",
        "ttl_minutes",
        "visibility",
    }),
    "resolve_lingering_thread": COMMON_COMMAND_FIELDS | frozenset({
        "thread_id",
        "resolution",
    }),
    "record_craft_genealogy_step": COMMON_COMMAND_FIELDS | frozenset({
        "problem_pattern_cn",
        "operation_cn",
        "domain_tag",
        "outcome",
        "mismatch_boundary_cn",
        "source_work_ref",
        "shareable_tip_cn",
        "shared_tip_only",
    }),
    "toggle_share_craft_tip": COMMON_COMMAND_FIELDS | frozenset({
        "genealogy_id",
        "shared_tip_only",
        "shareable_tip_cn",
    }),
    "curate_personal_ritual": COMMON_COMMAND_FIELDS | frozenset({
        "ritual_op",
        "ritual_id",
        "title_cn",
        "new_title_cn",
        "origin_trace_cn",
        "context_scope_cn",
        "variant_note_cn",
        "linked_genealogy_id",
        "status_target",
        "visibility",
    }),
}

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "plant_future_question": frozenset({
        "question_cn",
        "anchor_kind",
        "anchor_ref",
        "keywords",
    }),
    "revisit_future_question": frozenset({"question_id", "resolution"}),
    "propose_shared_idiom": frozenset({"phrase", "meaning_cn", "origin_summary_cn"}),
    "confirm_shared_idiom": frozenset({"idiom_id"}),
    "fade_shared_idiom": frozenset({"idiom_id"}),
    "record_memory_reinterpretation": frozenset({
        "episode_ref",
        "episode_title_cn",
        "original_fact_cn",
        "new_view_cn",
        "trigger_summary_cn",
    }),
    "share_memory_reinterpretation": frozenset({"reinterpretation_id", "shared_with_partner"}),
    "record_cross_domain_spark": frozenset({
        "source_domain",
        "source_observation_cn",
        "target_domain",
        "try_method_cn",
        "familiarity",
        "craft_fit",
        "mood_pull_today",
    }),
    "settle_spark_attempt": frozenset({"spark_id", "outcome"}),
    "subject_add_divergent_notice": frozenset({"target_ref", "target_title_cn", "notice_cn"}),
    "partner_add_divergent_notice": frozenset({"target_ref", "target_title_cn", "notice_cn"}),
    "sketch_day_direction": frozenset({"horizon_note_cn", "next_decision_boundary_cn"}),
    "advance_or_interrupt_direction": frozenset({"direction_id", "transition"}),
    "note_lingering_thread": frozenset({"summary_cn", "scope_kind", "scope_ref"}),
    "resolve_lingering_thread": frozenset({"thread_id", "resolution"}),
    "record_craft_genealogy_step": frozenset({
        "problem_pattern_cn",
        "operation_cn",
        "domain_tag",
        "outcome",
        "mismatch_boundary_cn",
    }),
    "toggle_share_craft_tip": frozenset({"genealogy_id", "shared_tip_only"}),
    "curate_personal_ritual": frozenset({"ritual_op"}),
}

#: Partner-door only actions.  A subject call is refused rather than impersonating.
_PARTNER_ONLY_ACTIONS: frozenset[str] = frozenset({"partner_add_divergent_notice"})

_MIRRORED_DATA_KEYS: tuple[str, ...] = (
    "state_exists",
    "perspective",
    "revision",
    "observed_at",
    "pronoun",
    "display_name",
    "future_self_questions",
    "shared_idioms",
    "memory_reinterpretations",
    "style_and_interest_sparks",
    "divergent_notices",
    "day_directions",
    "lingering_threads",
    "craft_genealogy",
    "personal_rituals",
    "llm_calls",
)

_STRING_FIELDS: tuple[str, ...] = (
    "question_id",
    "anchor_ref",
    "note_cn",
    "idiom_id",
    "phrase",
    "meaning_cn",
    "origin_summary_cn",
    "origin_ref",
    "episode_ref",
    "episode_title_cn",
    "original_fact_cn",
    "prior_view_cn",
    "new_view_cn",
    "trigger_summary_cn",
    "preserved_reservation_cn",
    "appreciation_facet_cn",
    "reinterpretation_id",
    "source_domain",
    "source_observation_cn",
    "target_domain",
    "try_method_cn",
    "choice_trace_cn",
    "spark_id",
    "target_ref",
    "target_title_cn",
    "notice_cn",
    "direction_id",
    "horizon_note_cn",
    "next_decision_boundary_cn",
    "bound_ref",
    "transition",
    "breakpoint_note_cn",
    "evidence_ref",
    "linked_zone_id",
    "direction_hint",
    "summary_cn",
    "scope_kind",
    "scope_ref",
    "thread_id",
    "problem_pattern_cn",
    "operation_cn",
    "domain_tag",
    "mismatch_boundary_cn",
    "source_work_ref",
    "shareable_tip_cn",
    "genealogy_id",
    "ritual_op",
    "ritual_id",
    "title_cn",
    "new_title_cn",
    "origin_trace_cn",
    "context_scope_cn",
    "variant_note_cn",
    "linked_genealogy_id",
    "status_target",
)

_BOOL_FIELDS: tuple[str, ...] = ("shared_with_partner", "shared_tip_only")


def _error_response(status_code: int, reason_code: str, detail: str = "") -> tuple[int, dict[str, Any]]:
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
    raise engine.V08ValidationError(reason_code, detail or reason_code)


def _validate_scalar_fields(action: str, payload: Mapping[str, Any]) -> None:
    for key in ("operation_key", "intent_fingerprint"):
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or value != value.strip()
        ):
            _fail("INVALID_FIELDS", f"{key} must be a trimmed non-empty string")

    for key in _STRING_FIELDS:
        if key not in payload or payload.get(key) is None:
            continue
        value = payload[key]
        if not isinstance(value, str):
            _fail("INVALID_FIELDS", f"{key} must be a string")
        if key not in (
            "note_cn",
            "origin_ref",
            "prior_view_cn",
            "choice_trace_cn",
            "bound_ref",
            "breakpoint_note_cn",
            "evidence_ref",
            "preserved_reservation_cn",
            "appreciation_facet_cn",
        ):
            if not value.strip():
                _fail("INVALID_FIELDS", f"{key} must not be blank")

    for key in _BOOL_FIELDS:
        if key in payload and payload.get(key) is not None:
            if not isinstance(payload[key], bool):
                _fail("INVALID_FIELDS", f"{key} must be a JSON boolean")

    for key in ("visibility",):
        if key in payload and payload.get(key) is not None:
            if payload[key] not in engine.VISIBILITIES:
                _fail("INVALID_FIELDS", "visibility must be private or shared")

    if "keywords" in payload and payload.get("keywords") is not None:
        keywords = payload["keywords"]
        if not isinstance(keywords, list) or not (1 <= len(keywords) <= 6):
            _fail("INVALID_FIELDS", "keywords must contain 1..6 strings")
        for item in keywords:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 24:
                _fail("INVALID_FIELDS", "each keyword must be a non-empty string <= 24 chars")

    if action == "plant_future_question":
        if payload.get("anchor_kind") not in engine.ANCHOR_KINDS:
            _fail("INVALID_FIELDS", "anchor_kind must be work, object, place or topic")
        if not isinstance(payload.get("anchor_ref"), str) or not str(payload.get("anchor_ref")).strip():
            _fail("INVALID_FIELDS", "anchor_ref must be a non-empty string")

    if action == "record_memory_reinterpretation":
        hypothesis_status = payload.get("hypothesis_status")
        if hypothesis_status is not None and hypothesis_status not in engine.HYPOTHESIS_STATUSES:
            _fail(
                "INVALID_FIELDS",
                "hypothesis_status must be open_question, revised_by_observation or confirmed",
            )

    if action == "revisit_future_question":
        if payload.get("resolution") not in engine.RESOLUTIONS:
            _fail("INVALID_FIELDS", "resolution must be still_curious, answered_now or let_fade")

    if action == "record_cross_domain_spark":
        if payload.get("familiarity") not in engine.SPARK_FAMILIARITY:
            _fail("INVALID_FIELDS", "familiarity must be new, tried_once or familiar")
        if payload.get("craft_fit") not in engine.SPARK_CRAFT_FIT:
            _fail("INVALID_FIELDS", "craft_fit must be awkward, workable or steady")
        if payload.get("mood_pull_today") not in engine.SPARK_MOOD_PULL:
            _fail("INVALID_FIELDS", "mood_pull_today must be low, curious or eager")

    if action == "settle_spark_attempt":
        if payload.get("outcome") not in engine.SPARK_OUTCOMES:
            _fail("INVALID_FIELDS", "outcome must be helped, did_not_fit or skipped_today")

    if action == "record_craft_genealogy_step":
        if payload.get("outcome") not in engine.CRAFT_OUTCOMES:
            _fail("INVALID_FIELDS", "outcome must be worked or misfired")

    if action == "note_lingering_thread":
        if payload.get("scope_kind") not in engine.LINGERING_SCOPES:
            _fail("INVALID_FIELDS", "scope_kind must be work, conversation or object")
        ttl = payload.get("ttl_minutes")
        if ttl is not None:
            if isinstance(ttl, bool) or not isinstance(ttl, int) or not (15 <= ttl <= 720):
                _fail("INVALID_FIELDS", "ttl_minutes must be an integer in 15..720")

    if action == "resolve_lingering_thread":
        if payload.get("resolution") not in engine.LINGERING_RESOLUTIONS:
            _fail(
                "INVALID_FIELDS",
                "resolution must be talked_through, activity_finished or basis_changed",
            )

    if "linked_object_ids" in payload and payload.get("linked_object_ids") is not None:
        linked = payload["linked_object_ids"]
        if not isinstance(linked, list) or len(linked) > 8:
            _fail("INVALID_FIELDS", "linked_object_ids must be a list of at most 8 strings")
        for item in linked:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 96:
                _fail(
                    "INVALID_FIELDS",
                    "each linked object id must be a non-empty string <= 96 chars",
                )

    if action == "advance_or_interrupt_direction":
        if payload.get("transition") not in engine.DIRECTION_TRANSITIONS:
            _fail(
                "INVALID_FIELDS",
                "transition must be mark_started, pause_at_breakpoint, "
                "mark_completed_with_ref or let_fade",
            )
        if payload.get("transition") == "mark_completed_with_ref":
            evidence_ref = payload.get("evidence_ref")
            if not isinstance(evidence_ref, str) or not evidence_ref.strip():
                _fail("EVIDENCE_REF_REQUIRED", "mark_completed_with_ref requires evidence_ref")

    if action == "curate_personal_ritual":
        ritual_op = payload.get("ritual_op")
        if ritual_op not in engine.RITUAL_OPS:
            _fail(
                "INVALID_FIELDS",
                "ritual_op must be sketch_custom, reuse_variant, fade_or_omit "
                "or consider_milestone",
            )
        if ritual_op == "sketch_custom":
            for required in ("title_cn", "origin_trace_cn"):
                value = payload.get(required)
                if not isinstance(value, str) or not value.strip():
                    _fail("INVALID_FIELDS", f"sketch_custom requires {required}")
        else:
            ritual_id = payload.get("ritual_id")
            if not isinstance(ritual_id, str) or not ritual_id.strip():
                _fail("INVALID_FIELDS", f"{ritual_op} requires ritual_id")
        if payload.get("status_target") is not None:
            if ritual_op != "fade_or_omit":
                _fail("INVALID_FIELDS", "status_target is only valid for fade_or_omit")
            if payload.get("status_target") not in engine.RITUAL_FADE_TARGETS:
                _fail(
                    "INVALID_FIELDS",
                    "status_target must be faded_quietly or technique_retained",
                )


def _validate_request_payload(payload: Any, *, actor_side: str) -> tuple[str, str]:
    if actor_side not in ("partner", "subject"):
        raise engine.V08PermissionError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping) or not payload:
        _fail("INVALID_FIELDS", "payload must be a non-empty JSON object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            _fail("INVALID_FIELDS", "blank or padded key forbidden")

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip() or action != action.strip():
        _fail("INVALID_FIELDS", "Missing required field 'action'")
    action = action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        _fail("INVALID_FIELDS", f"Unknown action: {action!r}")

    # Personal rituals carry an immutable zero-debt floor: a client that tries to
    # submit a streak / habit / guilt counter is refused outright with 400 rather
    # than silently ignored by the whitelist.
    if action == "curate_personal_ritual":
        counter_keys = sorted(set(payload) & engine.RITUAL_IMMUTABLE_KEYS)
        if counter_keys:
            _fail(
                "INVALID_FIELDS",
                "ritual counters are refused: " + ", ".join(counter_keys),
            )

    forbidden = sorted(set(payload) & engine.FORBIDDEN_CLIENT_KEYS)
    if forbidden:
        raise engine.V08ConflictError(
            "CLIENT_IDENTITY_REFUSED",
            "client identity or score keys are refused: " + ", ".join(forbidden),
        )

    unknown = sorted(set(payload) - ACTION_ALLOWED_FIELDS[action])
    if unknown:
        _fail("INVALID_FIELDS", f"Unknown fields for {action}: {unknown}")

    missing = sorted(key for key in _REQUIRED_FIELDS[action] if payload.get(key) is None)
    if missing:
        _fail("INVALID_FIELDS", f"Missing fields for {action}: {missing}")

    # Permission boundary: partner-door actions are the partner's own; every
    # subject-only action needs a verified subject capability.
    if action in _PARTNER_ONLY_ACTIONS:
        if actor_side != "partner":
            raise engine.V08PermissionError(
                "FORBIDDEN_ACTOR_SIDE", f"Action {action!r} is the partner's own door"
            )
        effective_actor = "partner"
    elif action in PARTNER_ALLOWED_ACTIONS:
        # propose / confirm / fade an idiom: either side may act.
        effective_actor = actor_side
    else:
        if actor_side != "subject":
            raise engine.V08PermissionError(
                "FORBIDDEN_ACTOR_SIDE", f"Action {action!r} requires a verified subject capability"
            )
        effective_actor = "subject"

    _validate_scalar_fields(action, payload)
    return action, effective_actor


def _compose_envelope(
    projected: Mapping[str, Any],
    *,
    read_only: bool,
    command_status: str = "READ",
    action: Optional[str] = None,
) -> dict[str, Any]:
    data_block: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "read_only": bool(read_only),
    }
    for key in _MIRRORED_DATA_KEYS:
        if key in projected:
            data_block[key] = copy.deepcopy(projected[key])
    if action is not None:
        data_block["action"] = action

    return {
        "ok": True,
        "status": "ok" if read_only else ("already_applied" if command_status == "DUPLICATE" else "applied"),
        "command_status": command_status,
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "perspective": data_block.get("perspective"),
        "llm_calls": 0,
        "data": data_block,
    }


def read_v08_emergent_echoes_view(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """读取涌现回响投影（100% 零磁盘写入，伴侣视角过滤私密行且不泄露计数）。"""

    try:
        projected = engine.read_emergent_echoes_zero_write(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return 200, _compose_envelope(projected, read_only=True, command_status="READ")
    except engine.V08EchoesError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def mutate_v08_emergent_echoes_view(
    root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """执行一次涌现回响写动作（白名单闸门 + 权限隔离 + 幂等重放）。"""

    try:
        action, effective_actor = _validate_request_payload(payload, actor_side=actor_side)
        projected, command_status = engine.apply_emergent_echoes_mutation(
            root,
            dict(payload),
            actor_side=effective_actor,
            now_iso=now_iso,
        )
        return 200, _compose_envelope(
            projected,
            read_only=False,
            command_status=command_status,
            action=action,
        )
    except engine.V08EchoesError as exc:
        return _error_response(exc.status_code, exc.reason_code, exc.message)


def _short(text: Any, limit: int = 60) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def summarize_emergent_echoes_for_context(
    root: Path | str,
    *,
    incoming_text: str = "",
    include_private: bool = False,
) -> str:
    """Zero-write, bounded summary string for the lived-context bridge."""

    try:
        matched = engine.match_situational_echoes_zero_write(
            root,
            incoming_text=incoming_text,
            include_private=bool(include_private),
        )
    except Exception:  # noqa: BLE001 - a broken state is an honest empty summary
        return ""

    pronoun = "TA"
    try:
        identity = engine.read_character_identity_zero_write(root)
        pronoun = identity["pronouns"] or "TA"
    except Exception:  # noqa: BLE001 - identity is cosmetic here
        pronoun = "TA"

    lines: list[str] = []

    for row in matched.get("matched_idioms") or []:
        if not isinstance(row, Mapping):
            continue
        lines.append(f"只有彼此懂的说法「{_short(row.get('phrase'), 24)}」：{_short(row.get('meaning_cn'), 80)}")
        lines.append(str(row.get("obligation_free_note_cn") or engine.OBLIGATION_FREE_NOTE))
        break

    for row in matched.get("matched_questions") or []:
        if not isinstance(row, Mapping):
            continue
        lines.append(f"{pronoun}曾问过自己：{_short(row.get('question_cn'), 80)}")
        break

    for row in matched.get("matched_reinterpretations") or []:
        if not isinstance(row, Mapping):
            continue
        title = _short(row.get("episode_title_cn"), 32)
        view = _short(row.get("current_view_cn"), 90)
        lines.append(f"关于「{title}」，后来更愿意这样理解：{view}")
        break

    for row in matched.get("matched_sparks") or []:
        if not isinstance(row, Mapping):
            continue
        target = _short(row.get("target_domain"), 24)
        method = _short(row.get("try_method_cn"), 70)
        lines.append(f"对「{target}」有一点点想试试：{method}")
        break

    for row in matched.get("matched_notices") or []:
        if not isinstance(row, Mapping):
            continue
        title = _short(row.get("target_title_cn"), 32)
        subject_notice = _short(row.get("subject_notice_cn"), 50)
        partner_notice = _short(row.get("partner_notice_cn"), 50)
        parts = [part for part in (subject_notice, partner_notice) if part]
        if parts:
            lines.append(f"同一件「{title}」上，两个人带回的注意并不一样：" + " / ".join(parts))
        break

    for row in matched.get("matched_craft_methods") or []:
        if not isinstance(row, Mapping):
            continue
        operation = _short(row.get("operation_cn"), 80)
        if operation:
            lines.append(f"这类情形以前试过、确实奏效的做法：{operation}")
        break

    # Ch.16 §四 #7: at most one compact line for a variant still in use, or a
    # quiet fade whose method was retained.  Empty state stays a 0-char summary.
    for row in matched.get("relevant_personal_rituals") or []:
        if not isinstance(row, Mapping):
            continue
        title = _short(row.get("title_cn"), 30)
        if str(row.get("status") or "") == "reused_variant":
            variant = _short(
                row.get("variant_note_cn") or row.get("context_scope_cn"), 60
            )
            if variant:
                lines.append(f"「{title}」这个顺手做法的变体还在用：{variant}")
            else:
                lines.append(f"「{title}」这个顺手做法已经在别处自然沿用")
        else:  # technique_retained -- the practice faded, the method stayed
            lines.append(f"「{title}」已经安静淡出，里头的做法还留着")
        break

    return "\n".join(lines)


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "COMMON_COMMAND_FIELDS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "ROUTE_V08_EMERGENT_ECHOES",
    "SUBJECT_ALLOWED_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v08_emergent_echoes_view",
    "read_v08_emergent_echoes_view",
    "summarize_emergent_echoes_for_context",
]
