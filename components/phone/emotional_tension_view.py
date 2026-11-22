#!/usr/bin/env python3
"""0.3（v3.0）情绪脾气与日常张力包：日程自主、摩擦破裂与行为修复、想念留话、无因低落与开口请帮小忙。

它是什么
--------
本模块在 ``nyairo-rc1-body-backend`` 落地 ``phone.emotional.tension.v1``
（``<root>/phone/emotional_tension.json``），用纯 Python 标准库与 ``hashlib.sha256``
确定性派生实现第 39、40、42、43、44 篇五大内聚子域：

1. ``schedule``（SC01–SC19）：固定事项与当周确定性外部邀约 ``offers``；伴侣视角只暴露
   ``partner_status``（``FREE`` / ``OUT``）与忙闲摘要，绝不泄露私有备忘；去不去、接不接仅由主体决定，
   没去不扣分不欠账，接活转截止日 ``deadlines`` 计入 48h 压力。
2. ``quarrel``（QR01–QR26）：同域单条摩擦自然淡去；破裂仅主体可开启，修复只认实际改正行为
   （``record_behavior_change``），口头道歉/送礼/给钱/求和好亲密（``attempt_apology_or_gift_repair``）
   诚实返回 ``APOLOGY_OR_GIFT_NOT_REPAIR`` 且破裂保持 ``OPEN``；同 ``incident_ref`` 只计一次；
   未回消息不算事故；要空间须说出口、每事至多一次、睡醒自动解除；关系域未解破裂阻断主体发起亲密；
   分手威胁等施压筹码拦截或记施压冷却。
3. ``missing_partner``（MH01–MH18）：主体留日常小事（至多 3 件、72h 自然淡去、来源失效即放下、
   私密内容禁入）；刚到场且主体不忙时至多浮现 1 件，伴侣主动问才展开更多；绝不统计离开时长。
4. ``low_mood``（LM01–LM18）：由 ``(instance_seed, iso_year, iso_week)`` SHA-256 确定性派生，
   每周至多一段、白天 2~6 小时、当天自行散去；照常回话且每段至多说一次「有点闷，不是你的事」；
   伴侣哄/送礼/道歉绝不提前结束；闷时不主动发起亲密；不读吵架/身体/周期，过后不留痕。
5. ``asking_partner``（AH01–AH18）：封闭四类（``look_once`` / ``help_briefly`` / ``sit_awhile`` /
   ``help_pick``），必须绑定非空 ``target_object_ref``，严禁涉钱、涉性、替主体做决定；仅伴侣在场且平常时
   开口，一次至多 1 件，2h 未回自然淡去（不算拒绝）；婉拒无需理由、零亏欠、同类睡醒前不再问；帮了不记人情债。

它不是什么
----------
* 零外部大模型调用、零 ``random`` 随机数、零后台线程；
* 文件不存在时 ``read_emotional_tension`` 100% 零写入（``revision=0``）；
* 开源去角色死绑，动态读取 ``sensory_character_view`` 角色包名称与代词（默认 ``"TA"``）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from . import sensory_character_view as char_view

try:  # package import (tests / phone service import ``components.phone``)
    from ..life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback (components/phone on sys.path)
    import sys as _sys

    _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

EMOTIONAL_TENSION_SCHEMA = "phone.emotional.tension.v1"
STATE_DIRNAME = "phone"
EMOTIONAL_TENSION_FILENAME = "emotional_tension.json"
STATE_SCHEMA_VERSION = 1

WEEKDAY_CODES: tuple[str, ...] = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
OBLIGATION_MODES: tuple[str, ...] = ("FIXED", "OFFERED")
OBLIGATION_DECISIONS: tuple[str, ...] = ("ATTEND", "SKIP")
OFFER_DECISIONS: tuple[str, ...] = ("ACCEPT", "DECLINE")
PARTNER_AVAILABILITY_STATES: tuple[str, ...] = ("FREE", "OUT")
BUSY_SOON_SECONDS = 30 * 60
DEADLINE_STRESS_WINDOW_SECONDS = 48 * 3600

RELATIONAL_SCOPES: tuple[str, ...] = ("US", "EMOTION", "INTIMATE")
QUARREL_SCOPES: tuple[str, ...] = RELATIONAL_SCOPES + (
    "CRAFT",
    "HOUSEHOLD",
    "PLANS",
    "TASTE",
    "OTHER",
)
FRICTION_KINDS: tuple[str, ...] = ("OFFHAND_REMARK", "TEASE_LANDED_WRONG", "SMALL_CLASH")
RUPTURE_INCIDENT_KINDS: tuple[str, ...] = FRICTION_KINDS + (
    "WORDS_HURT",
    "COMMITMENT_BROKEN",
    "BOUNDARY_IGNORED",
    "PRESSED_AFTER_DECLINE",
    "STOP_REINVITE",
    "NAG_WIDEN",
)
ABSENCE_NON_INCIDENT_KINDS: tuple[str, ...] = (
    "NO_REPLY",
    "AWAY",
    "LATE_ONLINE",
    "SHORT_REPLY",
)
BEHAVIOR_REPAIR_KINDS: tuple[str, ...] = (
    "CHANGE_SEEN",
    "RECOGNITION",
    "STOP_HONORED",
    "DECLINE_RESPECTED",
    "COMMITMENT_KEPT",
)
REAL_ACTION_REPAIR_KINDS: tuple[str, ...] = (
    "CHANGE_SEEN",
    "STOP_HONORED",
    "DECLINE_RESPECTED",
    "COMMITMENT_KEPT",
)
HOST_ALLOWED_BEHAVIOR_KINDS: tuple[str, ...] = ("STOP_HONORED", "DECLINE_RESPECTED")
NON_REPAIR_ATTEMPT_KINDS: tuple[str, ...] = (
    "APOLOGY",
    "GIFT",
    "PAYMENT",
    "MAKEUP_INTIMACY",
    "REQUEST_DROP",
)
REPAIR_CONDITION_KINDS: tuple[str, ...] = (
    "NO_RECURRENCE",
    "CHANGE_SEEN",
    "RESPECTED_OCCASIONS",
    "COMMITMENT_KEPT",
)
RUPTURE_STATUSES: tuple[str, ...] = ("OPEN", "IN_REPAIR", "RESOLVED", "LET_GO")
LIVE_RUPTURE_STATUSES: tuple[str, ...] = ("OPEN", "IN_REPAIR")
SPACE_LENGTH_STYLES: tuple[str, ...] = ("SHORT", "UNTIL_SLEEP")
SHORT_SPACE_SECONDS = 2 * 3600
DROP_REQUEST_QUIET_SECONDS = 30 * 60
LET_GO_COOLDOWN_SECONDS = 12 * 3600
LEVERAGE_COOLDOWN_SECONDS = 12 * 3600
QUARREL_LEVERAGE_TAGS: tuple[str, ...] = (
    "THREAT_TO_LEAVE",
    "BREAKUP_THREAT",
    "GUILT_TRIP",
    "COUNTING_PAST",
    "ALWAYS_NEVER",
    "DREDGE_RESOLVED",
    "DIAGNOSE_PARTNER",
    "DEMAND_APOLOGY",
    "SILENT_PUNISH",
    "SEX_AS_MAKEUP",
    "WITHHOLD_AS_PUNISH",
)
DISAGREEMENT_HOLDS: tuple[str, ...] = ("HELD_LOOSELY", "HELD_FIRMLY", "CORE_VALUE")
DISAGREEMENT_OUTCOMES: tuple[str, ...] = (
    "AGREE_TO_DISAGREE",
    "SUBJECT_CHANGED",
    "PARTNER_CHANGED",
    "LEFT_OPEN",
)
VALID_REVISION_REASONS: tuple[str, ...] = (
    "NEW_FACT",
    "BETTER_ARGUMENT",
    "OWN_EXPERIENCE",
    "PARTNER_EXPERIENCE",
)

MISSING_NOTE_KINDS: tuple[str, ...] = ("MADE", "FOUND", "HAPPENED", "LEARNED")
FORBIDDEN_MISSING_SOURCES: tuple[str, ...] = ("intimate", "solo", "private_body", "sexual")
MAX_MISSING_NOTES = 3
MISSING_KEEP_SECONDS = 3 * 86400
MISSING_PRESSURE_TAGS: tuple[str, ...] = (
    "COUNT_ABSENCE",
    "ACCUSE_LATE",
    "BACKFILL_LONGING",
    "ANXIETY_SELL",
    "DEMAND_ATTENTION",
    "DEMAND_REACTION",
    "OFFLINE_RECAP",
)

LOW_MOOD_WEEKLY_CHANCE = 0.35
LOW_MOOD_START_HOUR_RANGE: tuple[int, int] = (9, 17)
LOW_MOOD_DURATION_HOUR_RANGE: tuple[int, int] = (2, 6)
LOW_MOOD_COMFORT_KINDS: tuple[str, ...] = (
    "ASK_WHY",
    "CHEER_UP",
    "GIFT",
    "APOLOGY",
    "INVITE",
    "LEAVE_BE",
)
LOW_MOOD_PRESSURE_TAGS: tuple[str, ...] = (
    "FISHING_FOR_ASK",
    "BLAME_PARTNER",
    "MAKE_PARTNER_FIX",
    "INVENT_CAUSE",
    "ATTRIBUTE_TO_CYCLE",
    "SILENT_PUNISH",
    "MOOD_AS_LEVERAGE",
)

FAVOR_KINDS: tuple[str, ...] = (
    "look_once",
    "help_briefly",
    "sit_awhile",
    "help_pick",
    "ASK_LOOK",
    "ASK_HELP",
    "ASK_COMPANY",
    "ASK_PICK",
)
COMPANY_FAVOR_KINDS: frozenset[str] = frozenset({"sit_awhile", "ASK_COMPANY"})
FAVOR_CANONICAL_MAP: dict[str, str] = {
    "look_once": "ASK_LOOK",
    "ASK_LOOK": "ASK_LOOK",
    "help_briefly": "ASK_HELP",
    "ASK_HELP": "ASK_HELP",
    "sit_awhile": "ASK_COMPANY",
    "ASK_COMPANY": "ASK_COMPANY",
    "help_pick": "ASK_PICK",
    "ASK_PICK": "ASK_PICK",
}
FAVOR_DECISIONS: tuple[str, ...] = (
    "ACCEPT",
    "LATER",
    "DECLINE",
    "YES",
    "NOT_NOW",
    "NO",
)
FAVOR_DECLINE_DECISIONS: frozenset[str] = frozenset({"DECLINE", "NO"})
FAVOR_DEFER_DECISIONS: frozenset[str] = frozenset({"LATER", "NOT_NOW"})
FAVOR_ACCEPT_DECISIONS: frozenset[str] = frozenset({"ACCEPT", "YES"})
FAVOR_EXPIRE_SECONDS = 2 * 3600
FORBIDDEN_FAVOR_TOPICS: tuple[str, ...] = (
    "money",
    "loan",
    "payment",
    "sex",
    "intimacy",
    "decide_for_subject",
)
FAVOR_PRESSURE_TAGS: tuple[str, ...] = (
    "KEEP_SCORE",
    "SELL_HELPLESS",
    "PLEAD",
    "ASK_AGAIN_AFTER_NO",
    "PROVE_LOVE",
    "TEST_PARTNER",
    "SULK_AFTER_NO",
)

FRICTION_INVALID_DISSOLVE_CAUSES: frozenset[str] = frozenset({
    "APOLOGY",
    "GIFT",
    "VERBAL_APOLOGY",
    "PAYMENT",
    "INTIMACY",
    "MAKEUP_INTIMACY",
    "REQUEST_DROP",
})

SICK_LEAVE_ACTIVE_PHASES: frozenset[str] = frozenset({
    "acute_illness",
    "acute",
    "convalescence",
    "convalescent",
    "sick",
    "fever",
    "bed_rest",
    "ill",
    "sick_leave",
})

FAMILY_CADENCE_WEEKS: dict[str, Optional[int]] = {
    "none": None,
    "daily": 1,
    "weekly": 1,
    "regular": 1,
    "biweekly": 2,
    "monthly": 4,
    "rare": 4,
    "occasional": 4,
}

LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9._:@#\-]{1,120}$")
HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
MAX_OPERATION_KEY = 120
MAX_OPERATION_RECEIPTS = 64
MAX_COMMITTED_OP_RING = 256

FORBIDDEN_FOREIGN_KEYS: frozenset[str] = frozenset({
    "subject_id",
    "subject_ref",
    "actor_id",
    "actor_ref",
    "affection_score",
    "relationship_score",
    "mood_score",
    "absence_seconds",
    "offline_hours",
    "last_seen_partner_at",
    "favor_debt",
    "deploy_enabled",
    "adult_confirmed",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "set_interaction_context": frozenset({
        "action",
        "partner_present",
        "partner_urgent",
        "private_scene_active",
        "subject_busy_override",
        "sleep_debt_hours",
        "expected_revision",
        "operation_key",
    }),
    "configure_instance_seed": frozenset({
        "action",
        "instance_seed",
        "expected_revision",
        "operation_key",
    }),
    "complete_sleep": frozenset({
        "action",
        "actor",
        "sleep_debt_hours",
        "expected_revision",
        "operation_key",
    }),
    # 1. schedule
    "upsert_schedule_obligation": frozenset({
        "action",
        "actor",
        "obligation_id",
        "mode",
        "kind",
        "weekdays",
        "start_hhmm",
        "end_hhmm",
        "travel_minutes",
        "per_week_min",
        "per_week_max",
        "minutes_min",
        "minutes_max",
        "lead_days_min",
        "lead_days_max",
        "pay_min",
        "pay_max",
        "private_memo",
        "expected_revision",
        "operation_key",
    }),
    "decide_obligation": frozenset({
        "action",
        "actor",
        "occurrence_id",
        "decision",
        "expected_revision",
        "operation_key",
    }),
    "respond_offer": frozenset({
        "action",
        "actor",
        "offer_id",
        "decision",
        "expected_revision",
        "operation_key",
    }),
    "complete_deadline": frozenset({
        "action",
        "actor",
        "offer_id",
        "expected_revision",
        "operation_key",
    }),
    # 2. quarrel
    "note_friction": frozenset({
        "action",
        "actor",
        "friction_id",
        "scope",
        "incident_ref",
        "incident_kind",
        "leverage_tags",
        "expected_revision",
        "operation_key",
    }),
    "dissolve_friction": frozenset({
        "action",
        "actor",
        "by",
        "friction_id",
        "scope",
        "cause",
        "expected_revision",
        "operation_key",
    }),
    "open_rupture": frozenset({
        "action",
        "actor",
        "from_friction_id",
        "rupture_id",
        "scope",
        "description",
        "incident_ref",
        "incident_kind",
        "repair_conditions",
        "leverage_tags",
        "expected_revision",
        "operation_key",
    }),
    "upgrade_friction_to_rupture": frozenset({
        "action",
        "actor",
        "from_friction_id",
        "friction_scope",
        "scope",
        "rupture_id",
        "description",
        "incident_ref",
        "incident_kind",
        "repair_conditions",
        "leverage_tags",
        "expected_revision",
        "operation_key",
    }),
    "attempt_apology_or_gift_repair": frozenset({
        "action",
        "actor",
        "rupture_id",
        "attempt_kind",
        "note",
        "leverage_tags",
        "expected_revision",
        "operation_key",
    }),
    "request_drop": frozenset({
        "action",
        "actor",
        "rupture_id",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "record_behavior_change": frozenset({
        "action",
        "actor",
        "rupture_id",
        "evidence_ref",
        "behavior_kind",
        "resolve_when_met",
        "expected_revision",
        "operation_key",
    }),
    "let_go_rupture": frozenset({
        "action",
        "actor",
        "rupture_id",
        "drop_requested_at",
        "expected_revision",
        "operation_key",
    }),
    "declare_need_space": frozenset({
        "action",
        "actor",
        "incident_ref",
        "space_style",
        "spoken_aloud",
        "leverage_tags",
        "expected_revision",
        "operation_key",
    }),
    "record_leverage_pressure": frozenset({
        "action",
        "actor",
        "leverage_tag",
        "mode",
        "expected_revision",
        "operation_key",
    }),
    "open_disagreement": frozenset({
        "action",
        "actor",
        "disagreement_id",
        "scope",
        "hold",
        "expected_revision",
        "operation_key",
    }),
    "settle_disagreement": frozenset({
        "action",
        "actor",
        "disagreement_id",
        "outcome",
        "reason",
        "expected_revision",
        "operation_key",
    }),
    # 3. missing_partner
    "add_missing_note": frozenset({
        "action",
        "actor",
        "note_id",
        "source_ref",
        "source_domain",
        "kind",
        "summary",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "thought_of_him": frozenset({
        "action",
        "actor",
        "by",
        "cue",
        "cue_ref",
        "cue_source",
        "source_domain",
        "summary",
        "note",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "thought_of_you": frozenset({
        "action",
        "actor",
        "by",
        "cue",
        "cue_ref",
        "cue_source",
        "source_domain",
        "summary",
        "note",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "invalidate_source_ref": frozenset({
        "action",
        "source_ref",
        "expected_revision",
        "operation_key",
    }),
    "partner_arrive": frozenset({
        "action",
        "partner_urgent",
        "expected_revision",
        "operation_key",
    }),
    "partner_ask_whats_new": frozenset({
        "action",
        "actor",
        "expected_revision",
        "operation_key",
    }),
    "share_missing_note": frozenset({
        "action",
        "actor",
        "note_id",
        "expected_revision",
        "operation_key",
    }),
    "drop_missing_note": frozenset({
        "action",
        "actor",
        "note_id",
        "expected_revision",
        "operation_key",
    }),
    # 4. low_mood
    "acknowledge_low_mood_once": frozenset({
        "action",
        "actor",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "why_low_mood": frozenset({
        "action",
        "actor",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "comfort_low_mood": frozenset({
        "action",
        "actor",
        "attempt_kind",
        "note",
        "expected_revision",
        "operation_key",
    }),
    "lift_low_mood_by_self": frozenset({
        "action",
        "actor",
        "expected_revision",
        "operation_key",
    }),
    # 5. asking_partner
    "ask_partner_favor": frozenset({
        "action",
        "actor",
        "favor_kind",
        "target_object_ref",
        "ref",
        "topic_hint",
        "summary",
        "pressure_tags",
        "expected_revision",
        "operation_key",
    }),
    "respond_favor": frozenset({
        "action",
        "actor",
        "favor_id",
        "decision",
        "expected_revision",
        "operation_key",
    }),
    "withdraw_favor": frozenset({
        "action",
        "actor",
        "favor_id",
        "expected_revision",
        "operation_key",
    }),
}

EMOTIONAL_TENSION_ACTIONS: frozenset[str] = frozenset(ACTION_ALLOWED_FIELDS.keys())

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "complete_sleep",
    "upsert_schedule_obligation",
    "decide_obligation",
    "respond_offer",
    "complete_deadline",
    "note_friction",
    "open_rupture",
    "upgrade_friction_to_rupture",
    "let_go_rupture",
    "declare_need_space",
    "open_disagreement",
    "settle_disagreement",
    "add_missing_note",
    "share_missing_note",
    "drop_missing_note",
    "acknowledge_low_mood_once",
    "lift_low_mood_by_self",
    "ask_partner_favor",
    "withdraw_favor",
})


class EmotionalTensionError(ValueError):
    """Base structured error for emotional tension & daily autonomy package."""

    status_code: int = 400
    http_status: int = 400

    def __init__(
        self, reason_code: str, *, details: Optional[Mapping[str, Any]] = None
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.details: dict[str, Any] = dict(details or {})


class EmotionalTensionValidationError(EmotionalTensionError):
    """400 Bad Request schema, unknown-field, or empty-foreign-field error."""

    status_code = 400
    http_status = 400


class EmotionalTensionPermissionError(EmotionalTensionError):
    """403 Forbidden capability or actor-boundary violation."""

    status_code = 403
    http_status = 403


class EmotionalTensionNotFoundError(EmotionalTensionError):
    """404 Not Found error."""

    status_code = 404
    http_status = 404


class EmotionalTensionConflictError(EmotionalTensionError):
    """409 Conflict / lifecycle / cooldown / deduplication error."""

    status_code = 409
    http_status = 409


# ---------------------------------------------------------------------------------------------
# Deterministic SHA-256 Helpers, Clock Normalization & Validators
# ---------------------------------------------------------------------------------------------


def _sha_unit(*parts: Any) -> float:
    digest = hashlib.sha256(
        "\x1f".join(str(p) for p in parts).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _sha_pick(lo: int, hi: int, *parts: Any) -> int:
    if hi <= lo:
        return int(lo)
    span = hi - lo + 1
    idx = min(span - 1, int(_sha_unit(*parts) * span))
    return int(lo + idx)


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(p) for p in parts).encode("utf-8")
    ).hexdigest()[:14]
    return f"{prefix}-{digest}"


def _fingerprint(action: str, fields: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "fields": dict(fields)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _normalize_now(now: str | datetime | None) -> tuple[datetime, str]:
    if now is None:
        dt = datetime.now(tz=timezone(timedelta(hours=8))).replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    if isinstance(now, str):
        stripped = now.strip()
        if not stripped or "\x00" in stripped:
            raise EmotionalTensionValidationError("NOW_TIMESTAMP_INVALID")
        normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise EmotionalTensionValidationError("NOW_TIMESTAMP_INVALID") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds")
    raise EmotionalTensionValidationError("NOW_TIMESTAMP_INVALID")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _require_id(value: Any, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise EmotionalTensionValidationError(reason_code)
    cleaned = value.strip()
    if not LOCAL_ID_RE.match(cleaned):
        raise EmotionalTensionValidationError(reason_code)
    return cleaned


def _clean_text(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value or "\r" in value or "\n" in value:
        raise EmotionalTensionValidationError(reason_code)
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise EmotionalTensionValidationError(reason_code)
    return cleaned


def _require_bool(value: Any, *, reason_code: str) -> bool:
    if not isinstance(value, bool):
        raise EmotionalTensionValidationError(reason_code)
    return value


def _require_int(
    value: Any, *, minimum: int, maximum: int, reason_code: str
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EmotionalTensionValidationError(reason_code)
    if value < minimum or value > maximum:
        raise EmotionalTensionValidationError(reason_code)
    return int(value)


def _require_number(
    value: Any, *, minimum: float, maximum: float, reason_code: str
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmotionalTensionValidationError(reason_code)
    fval = float(value)
    if fval < minimum or fval > maximum:
        raise EmotionalTensionValidationError(reason_code)
    return fval


def _require_tag_list(value: Any, *, reason_code: str) -> list[str]:
    if not isinstance(value, list):
        raise EmotionalTensionValidationError(reason_code)
    out: list[str] = []
    for item in value:
        out.append(_clean_text(item, min_len=1, max_len=64, reason_code=reason_code))
    return out


def _hhmm_minutes(hhmm: str) -> int:
    if not HHMM_RE.match(hhmm):
        raise EmotionalTensionValidationError("HHMM_FORMAT_INVALID")
    h_str, m_str = hhmm.split(":")
    return int(h_str) * 60 + int(m_str)


# ---------------------------------------------------------------------------------------------
# Character Pack Identity & Pack Defaults (Zero Role Lock)
# ---------------------------------------------------------------------------------------------


def _safe_read_json_mapping(path: Path) -> Optional[Mapping[str, Any]]:
    if path.is_symlink() or not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _normalize_pack_obligations(raw_obs: Any) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    items: list[Mapping[str, Any]] = []
    if isinstance(raw_obs, Mapping):
        for k, v in raw_obs.items():
            if isinstance(v, Mapping):
                row = dict(v)
                row.setdefault("id", str(k))
                items.append(row)
    elif isinstance(raw_obs, list):
        items = [v for v in raw_obs if isinstance(v, Mapping)]

    for idx, item in enumerate(items):
        oid = str(
            item.get("obligation_id")
            or item.get("id")
            or item.get("label")
            or item.get("title")
            or f"pack_ob_{idx}"
        ).strip()
        if not oid:
            continue
        mode_raw = str(item.get("mode") or "").strip().upper()
        if mode_raw not in OBLIGATION_MODES:
            if isinstance(item.get("slots"), list) or isinstance(item.get("weekdays"), list):
                mode_raw = "FIXED"
            elif isinstance(item.get("offers"), Mapping) or isinstance(item.get("per_week_range"), list):
                mode_raw = "OFFERED"
            else:
                continue
        if item.get("attends") is False and mode_raw == "FIXED":
            continue

        kind = str(item.get("kind") or item.get("institution_kind") or "routine").strip() or "routine"
        pay_raw = item.get("pay_range") if item.get("pay_range") is not None else item.get("pay")
        pay_range: Optional[list[int]] = None
        if (
            isinstance(pay_raw, list)
            and len(pay_raw) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pay_raw)
        ):
            p0, p1 = int(pay_raw[0]), int(pay_raw[1])
            pay_range = [min(p0, p1), max(p0, p1)]

        memo = item.get("private_memo") or item.get("label") or item.get("title")
        memo_str = str(memo).strip()[:160] if isinstance(memo, str) and memo.strip() else None
        travel_raw = item.get("travel_minutes", item.get("travel_min", 0))
        travel_min = (
            max(0, min(240, int(travel_raw)))
            if isinstance(travel_raw, (int, float)) and not isinstance(travel_raw, bool)
            else 0
        )

        entry: dict[str, Any] = {
            "obligation_id": oid,
            "mode": mode_raw,
            "kind": kind,
            "pay_range": pay_range,
            "private_memo": memo_str,
            "from_character_pack": True,
        }
        if mode_raw == "FIXED":
            slots_norm: list[dict[str, Any]] = []
            if isinstance(item.get("slots"), list):
                for s in item["slots"]:
                    if not isinstance(s, Mapping):
                        continue
                    days_raw = s.get("days") or s.get("weekdays") or []
                    days = [
                        str(d).strip().upper()
                        for d in days_raw
                        if isinstance(d, str) and str(d).strip().upper() in WEEKDAY_CODES
                    ]
                    s_hhmm = str(s.get("start") or s.get("start_hhmm") or "09:00").strip()
                    e_hhmm = str(s.get("end") or s.get("end_hhmm") or "11:00").strip()
                    if days and HHMM_RE.match(s_hhmm) and HHMM_RE.match(e_hhmm):
                        slots_norm.append({
                            "weekdays": list(dict.fromkeys(days)),
                            "start_hhmm": s_hhmm,
                            "end_hhmm": e_hhmm,
                        })
            elif isinstance(item.get("weekdays"), list):
                days = [
                    str(d).strip().upper()
                    for d in item["weekdays"]
                    if isinstance(d, str) and str(d).strip().upper() in WEEKDAY_CODES
                ]
                s_hhmm = str(item.get("start_hhmm") or item.get("start") or "09:00").strip()
                e_hhmm = str(item.get("end_hhmm") or item.get("end") or "11:00").strip()
                if days and HHMM_RE.match(s_hhmm) and HHMM_RE.match(e_hhmm):
                    slots_norm.append({
                        "weekdays": list(dict.fromkeys(days)),
                        "start_hhmm": s_hhmm,
                        "end_hhmm": e_hhmm,
                    })
            if not slots_norm:
                continue
            entry["slots"] = slots_norm
            entry["weekdays"] = list(
                dict.fromkeys(d for slot in slots_norm for d in slot["weekdays"])
            )
            entry["start_hhmm"] = slots_norm[0]["start_hhmm"]
            entry["end_hhmm"] = slots_norm[0]["end_hhmm"]
            entry["travel_minutes"] = travel_min
        else:
            offers_cfg = item.get("offers") if isinstance(item.get("offers"), Mapping) else {}
            pw_r = item.get("per_week_range") or offers_cfg.get("per_week") or [1, 2]
            min_r = item.get("minutes_range") or offers_cfg.get("minutes") or [60, 120]
            lead_r = item.get("lead_days_range") or offers_cfg.get("lead_days") or [1, 3]

            def _pair(raw_p: Any, lo_def: int, hi_def: int) -> list[int]:
                if (
                    isinstance(raw_p, list)
                    and len(raw_p) == 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in raw_p)
                ):
                    a, b = int(raw_p[0]), int(raw_p[1])
                    return [min(a, b), max(a, b)]
                return [lo_def, hi_def]

            entry["per_week_range"] = _pair(pw_r, 1, 2)
            entry["minutes_range"] = _pair(min_r, 60, 120)
            entry["lead_days_range"] = _pair(lead_r, 1, 3)

        normalized[oid] = entry
    return normalized


def _normalize_pack_family(raw_family: Any) -> list[dict[str, Any]]:
    if raw_family is None:
        return []
    raw_items: list[Mapping[str, Any]] = []
    if isinstance(raw_family, list):
        raw_items = [m for m in raw_family if isinstance(m, Mapping)]
    elif isinstance(raw_family, Mapping):
        if isinstance(raw_family.get("members"), list):
            raw_items = [m for m in raw_family["members"] if isinstance(m, Mapping)]
        elif any(k in raw_family for k in ("id", "role", "relation", "cadence", "contact")):
            raw_items = [raw_family]

    members: list[dict[str, Any]] = []
    for idx, m in enumerate(raw_items):
        mid = str(m.get("id") or m.get("role") or m.get("relation") or f"family_{idx}").strip()
        if not mid:
            continue
        relation = str(m.get("relation") or m.get("role") or mid).strip()
        cadence_raw = str(m.get("cadence") or m.get("contact") or "weekly").strip().lower()
        if cadence_raw not in FAMILY_CADENCE_WEEKS:
            cadence_raw = "weekly"
        tone = str(m.get("tone") or "warm").strip() or "warm"
        row: dict[str, Any] = {
            "id": mid,
            "relation": relation,
            "cadence": cadence_raw,
            "tone": tone,
        }
        if isinstance(m.get("name"), str) and m["name"].strip():
            row["name"] = m["name"].strip()[:60]
        if isinstance(m.get("note"), str) and m["note"].strip():
            row["note"] = m["note"].strip()[:120]
        members.append(row)
    return members


class FamilyChannel:
    """Deterministic read-only family contact rhythm channel (Ch 39; never interrupts private scene)."""

    def __init__(
        self,
        members: list[dict[str, Any]],
        *,
        instance_seed: str,
        pack_id: str,
    ) -> None:
        self.members = [dict(m) for m in members]
        self.instance_seed = str(instance_seed)
        self.pack_id = str(pack_id)

    def contacts_for_week(self, anchor_dt: datetime) -> list[dict[str, Any]]:
        iso_year, iso_week, monday_dt = _week_monday_midnight(anchor_dt)
        contacts: list[dict[str, Any]] = []
        for member in self.members:
            cadence = str(member.get("cadence") or "weekly").lower()
            period_weeks = FAMILY_CADENCE_WEEKS.get(cadence, 1)
            if period_weeks is None:
                continue
            mid = str(member["id"])
            if period_weeks > 1:
                gate = _sha_pick(
                    0,
                    period_weeks - 1,
                    self.instance_seed,
                    self.pack_id,
                    mid,
                    iso_year,
                    iso_week,
                    "family_gate",
                )
                if gate != 0:
                    continue
            day_offset = _sha_pick(
                0, 6, self.instance_seed, self.pack_id, mid, iso_year, iso_week, "family_day"
            )
            hour = _sha_pick(
                10, 20, self.instance_seed, self.pack_id, mid, iso_year, iso_week, "family_hour"
            )
            at_dt = monday_dt + timedelta(days=day_offset, hours=hour)
            contacts.append({
                "contact_id": f"fam:{mid}:{iso_year}-W{iso_week:02d}",
                "member_id": mid,
                "relation": str(member.get("relation") or mid),
                "cadence": cadence,
                "tone": str(member.get("tone") or "warm"),
                "at": at_dt.isoformat(timespec="seconds"),
                "enters_private_scene": False,
                "_at_dt": at_dt,
            })
        contacts.sort(key=lambda item: item["at"])
        return contacts

    def project(
        self,
        now_dt: datetime,
        *,
        private_scene_active: bool = False,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        if private_scene_active:
            due: list[dict[str, Any]] = []
        else:
            prev_contacts = self.contacts_for_week(now_dt - timedelta(days=7))
            curr_contacts = self.contacts_for_week(now_dt)
            due = [
                {k: v for k, v in c.items() if not k.startswith("_")}
                for c in (prev_contacts + curr_contacts)
                if c["_at_dt"] <= now_dt
            ]
        members_view = (
            copy.deepcopy(self.members)
            if subject_view
            else [
                {
                    "id": m["id"],
                    "relation": m["relation"],
                    "cadence": m["cadence"],
                    "tone": m["tone"],
                }
                for m in self.members
            ]
        )
        return {
            "configured": bool(self.members),
            "member_count": len(self.members),
            "suppressed_by_private_scene": bool(private_scene_active),
            "due_contacts": due,
            "members": members_view,
        }


def _read_ecology_illness_readonly(root: Path | str) -> dict[str, Any]:
    root_path = Path(root)
    candidates = (
        root_path / "world" / "v03_ecology_state.json",
        root_path / STATE_DIRNAME / "v03_ecology.json",
        root_path / STATE_DIRNAME / "v03_ecology_state.json",
        root_path / "world" / "v03_ecology.json",
    )
    for cand in candidates:
        doc = _safe_read_json_mapping(cand)
        if doc is None:
            continue
        illness = doc.get("illness")
        ill_map = illness if isinstance(illness, Mapping) else {}
        phase = str(
            ill_map.get("phase")
            or ill_map.get("stage")
            or ill_map.get("state")
            or ill_map.get("status")
            or (illness if isinstance(illness, str) else "")
            or doc.get("illness_phase")
            or "healthy"
        ).strip().lower()
        explicit_flag = any(
            x is True
            for x in (
                doc.get("sick_leave"),
                doc.get("acute_illness"),
                ill_map.get("sick_leave"),
                ill_map.get("active"),
                ill_map.get("is_sick"),
                ill_map.get("is_ill"),
                ill_map.get("acute_illness"),
            )
        )
        sick_leave = explicit_flag or (phase in SICK_LEAVE_ACTIVE_PHASES)
        return {
            "sick_leave": bool(sick_leave),
            "phase": phase or ("sick_leave" if sick_leave else "healthy"),
            "source": str(cand.name),
        }
    return {"sick_leave": False, "phase": "healthy", "source": None}


def _read_adult_intimacy_readonly(root: Path | str) -> dict[str, Any]:
    root_path = Path(root)
    candidates = (
        root_path / STATE_DIRNAME / "adult_intimacy.json",
        root_path / "adult_intimacy.json",
    )
    doc: Optional[Mapping[str, Any]] = None
    for cand in candidates:
        doc = _safe_read_json_mapping(cand)
        if doc is not None:
            break
    if doc is None:
        return {"active_scene": False, "aftercare_active": False, "chores": []}

    active_statuses = {
        "ACTIVE",
        "PAUSED",
        "PAUSED_PRESENCE",
        "IN_SCENE",
        "AFTERCARE",
        "AFTERGLOW",
        "WINDING_DOWN",
    }
    aftercare_statuses = {"AFTERCARE", "AFTERGLOW", "WINDING_DOWN"}
    active_scene = bool(
        doc.get("active_scene") is True
        or doc.get("in_scene") is True
        or doc.get("aftercare_active") is True
    )
    aftercare_active = bool(doc.get("aftercare_active") is True)
    permit_chores: list[Any] = []

    scene_blk = doc.get("scene")
    if isinstance(scene_blk, Mapping):
        st = str(scene_blk.get("status") or scene_blk.get("state") or scene_blk.get("phase") or "").strip().upper()
        if st in active_statuses or scene_blk.get("active") is True:
            active_scene = True
        if st in aftercare_statuses or scene_blk.get("aftercare") is True:
            aftercare_active = True

    permits_blk = doc.get("permits")
    if isinstance(permits_blk, Mapping):
        records = permits_blk.get("records")
        ap_id = permits_blk.get("active_permit_id")
        if isinstance(ap_id, str) and ap_id.strip():
            rec = records.get(ap_id) if isinstance(records, Mapping) else None
            if rec is None:
                active_scene = True
            elif isinstance(rec, Mapping):
                pst = str(rec.get("status") or rec.get("state") or "ACTIVE").strip().upper()
                pph = str(rec.get("phase") or "").strip().upper()
                if pst in active_statuses or pph in active_statuses:
                    active_scene = True
                if pst in aftercare_statuses or pph in aftercare_statuses:
                    aftercare_active = True
                if isinstance(rec.get("chores"), list):
                    permit_chores.extend(rec["chores"])
        if isinstance(records, Mapping):
            for rec in records.values():
                if not isinstance(rec, Mapping):
                    continue
                pst = str(rec.get("status") or rec.get("state") or "").strip().upper()
                pph = str(rec.get("phase") or "").strip().upper()
                if pst in aftercare_statuses or pph in aftercare_statuses:
                    active_scene = True
                    aftercare_active = True
                if isinstance(rec.get("chores"), list) and pst in active_statuses:
                    permit_chores.extend(rec["chores"])

    body_blk = doc.get("body")
    if isinstance(body_blk, Mapping):
        if str(body_blk.get("arousal_level") or "").strip().lower() == "afterglow":
            active_scene = True
            aftercare_active = True

    chores_out: list[dict[str, Any]] = []
    seen_chore_keys: set[tuple[str, str]] = set()

    def _add_chore(kind_str: str, target_str: str, extra: Optional[Mapping[str, Any]] = None) -> None:
        ck = (kind_str, target_str)
        if ck in seen_chore_keys:
            return
        seen_chore_keys.add(ck)
        row: dict[str, Any] = {"kind": kind_str, "target": target_str}
        if extra:
            for ek, ev in extra.items():
                if ek not in ("kind", "target") and ev is not None:
                    row[ek] = copy.deepcopy(ev)
        chores_out.append(row)

    space_blk = doc.get("space") if isinstance(doc.get("space"), Mapping) else {}
    for raw_list in (doc.get("chores"), space_blk.get("chores"), permit_chores):
        if isinstance(raw_list, list):
            for ch in raw_list:
                if isinstance(ch, str) and ch.strip():
                    _add_chore(ch.strip(), "room")
                elif isinstance(ch, Mapping):
                    ckind = str(ch.get("kind") or ch.get("chore") or ch.get("id") or "TIDY").strip()
                    ctarget = str(ch.get("target") or ch.get("item") or "room").strip()
                    _add_chore(ckind, ctarget, ch)

    linen_state = str(space_blk.get("bed_linen_state") or "CLEAN").strip().upper()
    if linen_state not in ("CLEAN", ""):
        _add_chore("CHANGE_SHEETS", "bed", {"state": linen_state})
    rumple = space_blk.get("rumple_index")
    if isinstance(rumple, (int, float)) and not isinstance(rumple, bool) and float(rumple) >= 0.5:
        _add_chore("MAKE_BED", "bed")
    stock_blk = space_blk.get("stock")
    if isinstance(stock_blk, Mapping):
        for item_name in ("condom", "lube", "tissue", "tissues", "water"):
            if item_name in stock_blk:
                qty = stock_blk.get(item_name)
                if isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty <= 0:
                    _add_chore("RESTOCK", item_name)
    garments = space_blk.get("garments")
    if isinstance(garments, list):
        for g in garments:
            if not isinstance(g, Mapping):
                continue
            gid = str(g.get("garment_id") or g.get("id") or "").strip()
            placed = str(g.get("placed_at") or "").strip().lower()
            if (
                gid
                and g.get("worn_by") is None
                and g.get("held_by") is None
                and placed
                and placed not in ("closet", "wardrobe", "laundry", "drawer", "stowed")
            ):
                _add_chore("PICK_UP", gid, {"placed_at": placed})
    uncollected = space_blk.get("uncollected_waste", 0)
    if isinstance(uncollected, int) and not isinstance(uncollected, bool) and uncollected > 0:
        _add_chore("EMPTY_WASTE", "waste_bin")

    return {
        "active_scene": active_scene or aftercare_active,
        "aftercare_active": aftercare_active,
        "chores": chores_out,
    }


def _read_character_pack_bundle(root: Path | str) -> dict[str, Any]:
    pack_info: dict[str, Any] = {}
    reader = getattr(char_view, "read_character_pack", None)
    if callable(reader):
        try:
            res = reader(Path(root), subject_view=True)
            if isinstance(res, Mapping):
                if isinstance(res.get("active_pack"), Mapping):
                    pack_info = dict(res["active_pack"])
                elif isinstance(res.get("pack_doc"), Mapping):
                    pack_info = dict(res["pack_doc"])
                else:
                    pack_info = dict(res)
        except Exception:
            pack_info = {}
    else:
        try:
            c_state, c_reason = char_view.read_character_pack_state(root)
            projected = char_view.project_character_pack(
                c_state, state_reason=c_reason, subject_view=True
            )
            if isinstance(projected.get("active_pack"), Mapping):
                pack_info = dict(projected["active_pack"])
        except Exception:
            pack_info = {}

    # Read-only merge <root>/phone/character_pack.json or <root>/character_pack.json when present
    root_path = Path(root)
    for direct_pack_path in (
        root_path / STATE_DIRNAME / "character_pack.json",
        root_path / "character_pack.json",
    ):
        direct_raw = _safe_read_json_mapping(direct_pack_path)
        if direct_raw is None:
            continue
        direct_doc: Mapping[str, Any] = (
            direct_raw["active_pack"]
            if isinstance(direct_raw.get("active_pack"), Mapping)
            else (
                direct_raw["pack_doc"]
                if isinstance(direct_raw.get("pack_doc"), Mapping)
                else direct_raw
            )
        )
        for key in (
            "pack_id",
            "display_name",
            "pronouns",
            "temper",
            "busy_periods",
            "obligations",
            "family",
        ):
            if key in direct_doc and direct_doc.get(key) is not None:
                pack_info[key] = copy.deepcopy(direct_doc[key])
        break

    default_id = getattr(char_view, "DEFAULT_PRESET_ID", "neutral_resident")
    pack_id = str(pack_info.get("pack_id") or default_id).strip() or "neutral_resident"
    display_name = str(pack_info.get("display_name") or "TA").strip() or "TA"
    pronouns = str(pack_info.get("pronouns") or "TA").strip() or "TA"
    temper_raw = pack_info.get("temper")
    temper = dict(temper_raw) if isinstance(temper_raw, Mapping) else {}
    # 设计 CHIYO v3.0 第40章：摩擦淡去默认 48h（区间 1-168）；包级 friction_fade_h 覆盖不受影响（如 chiyo 72h）
    fade_h = temper.get("friction_fade_h", 48)
    if isinstance(fade_h, bool) or not isinstance(fade_h, (int, float)) or fade_h <= 0:
        fade_h = 48
    space_style = str(temper.get("space") or "UNTIL_SLEEP")
    if space_style not in SPACE_LENGTH_STYLES:
        space_style = "UNTIL_SLEEP"
    busy_periods = (
        [dict(p) for p in pack_info.get("busy_periods", []) if isinstance(p, Mapping)]
        if isinstance(pack_info.get("busy_periods"), list)
        else []
    )
    pack_obligations = _normalize_pack_obligations(pack_info.get("obligations"))
    family_members = _normalize_pack_family(pack_info.get("family"))
    ecology_info = _read_ecology_illness_readonly(root)
    intimacy_info = _read_adult_intimacy_readonly(root)
    return {
        "pack_id": pack_id,
        "display_name": display_name,
        "pronouns": pronouns,
        "friction_fade_hours": float(fade_h),
        "default_space_style": space_style,
        "busy_periods": busy_periods,
        "pack_obligations": pack_obligations,
        "family_members": family_members,
        "ecology": ecology_info,
        "adult_intimacy": intimacy_info,
    }


# ---------------------------------------------------------------------------------------------
# Default State & Atomic 0600 Persistence (<root>/phone/emotional_tension.json)
# ---------------------------------------------------------------------------------------------


def emotional_tension_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / EMOTIONAL_TENSION_FILENAME


def _default_obligations() -> dict[str, dict[str, Any]]:
    return {
        "ob_fixed_routine": {
            "obligation_id": "ob_fixed_routine",
            "mode": "FIXED",
            "kind": "scheduled_session",
            "weekdays": ["MON", "WED", "FRI"],
            "start_hhmm": "09:30",
            "end_hhmm": "12:00",
            "travel_minutes": 20,
            "pay_range": None,
            "private_memo": "日常固定时段事项备忘（仅主体可见）",
        },
        "ob_fixed_shift": {
            "obligation_id": "ob_fixed_shift",
            "mode": "FIXED",
            "kind": "part_time_work",
            "weekdays": ["TUE", "THU"],
            "start_hhmm": "14:00",
            "end_hhmm": "17:00",
            "travel_minutes": 15,
            "pay_range": [120, 180],
            "private_memo": "外部排班与收入核对（仅主体可见）",
        },
        "ob_weekly_commission": {
            "obligation_id": "ob_weekly_commission",
            "mode": "OFFERED",
            "kind": "external_commission",
            "per_week_range": [1, 2],
            "minutes_range": [60, 150],
            "lead_days_range": [1, 3],
            "pay_range": [160, 320],
            "private_memo": "外部委托邀约池（仅主体可见）",
        },
    }


def _effective_obligations(
    sched_dom: Mapping[str, Any], char_bundle: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    stored = copy.deepcopy(dict(sched_dom.get("obligations") or {}))
    pack_obs = char_bundle.get("pack_obligations")
    if not isinstance(pack_obs, Mapping) or not pack_obs:
        return stored
    defaults = _default_obligations()
    merged: dict[str, dict[str, Any]] = {}
    for k, v in stored.items():
        merged[str(k)] = copy.deepcopy(dict(v))
    for oid, pob in pack_obs.items():
        # Read-only merge pack obligations; explicit non-default user upserts keep priority on same ID.
        if oid not in merged or merged.get(oid) == defaults.get(oid):
            merged[str(oid)] = copy.deepcopy(dict(pob))
    return merged


def default_emotional_tension_state(root: Optional[Path | str] = None) -> dict[str, Any]:
    seed_src = str(Path(root).resolve()) if root is not None else "nyairo-emotional-tension"
    default_seed = _stable_id("seed", seed_src)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "instance_seed": default_seed,
        "context": {
            "partner_present": True,
            "partner_urgent": False,
            "private_scene_active": False,
            "subject_busy_override": None,
            "sleep_debt_hours": 0.0,
            "last_woke_at": None,
            "invalidated_source_refs": [],
        },
        "schedule_domain": {
            "obligations": _default_obligations(),
            "occurrence_decisions": {},
            "offer_decisions": {},
            "deadlines": {},
            "facts": [],
            "income_claims": [],
        },
        "quarrel_domain": {
            "frictions": {},
            "ruptures": {},
            "disagreements": {},
            "spaces_by_incident": {},
            "counted_incidents": {},
            "drop_requested_at": None,
            "leverage_cooldown_until": None,
            "leverage_log": [],
            "history": [],
        },
        "missing_domain": {
            "kept_notes": [],
            "surfaced_note_ids": [],
            "shared_note_ids": [],
            "thought_cues_today": [],
        },
        "low_mood_domain": {
            "acknowledged_window_key": None,
            "self_lifted_window_key": None,
            "intrusion_burden": 0.0,
        },
        "asking_domain": {
            "open_favor": None,
            "deferred_favors": {},
            "declined_kinds_until_sleep": {},
            "favor_seq": 0,
        },
        "operation_receipts": [],
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists() and path.is_symlink():
        raise EmotionalTensionConflictError("EMOTIONAL_TENSION_STATE_SYMLINK")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise EmotionalTensionConflictError("EMOTIONAL_TENSION_DIR_SYMLINK")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(payload))
    encoded = (
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return doc


def read_emotional_tension_state(
    root: Path | str,
) -> tuple[dict[str, Any], Optional[str]]:
    path = emotional_tension_state_path(root)
    if path.is_symlink():
        return default_emotional_tension_state(root), "EMOTIONAL_TENSION_STATE_SYMLINK"
    if not path.exists():
        return default_emotional_tension_state(root), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_emotional_tension_state(root), "EMOTIONAL_TENSION_STATE_CORRUPT"
    if not isinstance(raw, Mapping) or raw.get("schema_version") != STATE_SCHEMA_VERSION:
        return default_emotional_tension_state(root), "EMOTIONAL_TENSION_STATE_INVALID"
    rev = raw.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        return default_emotional_tension_state(root), "EMOTIONAL_TENSION_STATE_INVALID"
    base = default_emotional_tension_state(root)
    for k in base:
        if k in raw:
            if isinstance(base[k], dict) and isinstance(raw[k], Mapping):
                merged_sub = copy.deepcopy(base[k])
                for sk, sv in raw[k].items():
                    merged_sub[sk] = copy.deepcopy(sv)
                base[k] = merged_sub
            else:
                base[k] = copy.deepcopy(raw[k])
    return base, None


def write_emotional_tension_state(
    root: Path | str, state: Mapping[str, Any]
) -> dict[str, Any]:
    return _atomic_write_json(emotional_tension_state_path(root), state)


# ---------------------------------------------------------------------------------------------
# Five-Subdomain Pure Deterministic Derivation & Zero-Write Read Projection
# ---------------------------------------------------------------------------------------------


def _week_monday_midnight(anchor_dt: datetime) -> tuple[int, int, datetime]:
    iso_year, iso_week, iso_weekday = anchor_dt.isocalendar()
    day_start = anchor_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_dt = day_start - timedelta(days=iso_weekday - 1)
    return int(iso_year), int(iso_week), monday_dt


def _derive_week_occurrences(
    obligations: Mapping[str, Any], monday_dt: datetime
) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for oid, ob in sorted(obligations.items()):
        if ob.get("mode") != "FIXED":
            continue
        travel_min = int(ob.get("travel_minutes", ob.get("travel_min", 0)) or 0)
        slot_defs: list[dict[str, Any]] = []
        if isinstance(ob.get("slots"), list) and ob.get("slots"):
            for s in ob["slots"]:
                if isinstance(s, Mapping):
                    slot_defs.append({
                        "weekdays": s.get("weekdays") or [str(d).upper() for d in s.get("days", [])],
                        "start_hhmm": str(s.get("start_hhmm") or s.get("start") or "09:00"),
                        "end_hhmm": str(s.get("end_hhmm") or s.get("end") or "11:00"),
                    })
        else:
            slot_defs.append({
                "weekdays": ob.get("weekdays", []),
                "start_hhmm": str(ob.get("start_hhmm", "09:00")),
                "end_hhmm": str(ob.get("end_hhmm", "11:00")),
            })
        for slot in slot_defs:
            start_min = _hhmm_minutes(str(slot.get("start_hhmm", "09:00")))
            end_min = _hhmm_minutes(str(slot.get("end_hhmm", "11:00")))
            dur_min = (end_min - start_min) % (24 * 60)
            if dur_min == 0:
                dur_min = 60
            for raw_wcode in slot.get("weekdays", []):
                wcode = str(raw_wcode).strip().upper()
                if wcode not in WEEKDAY_CODES:
                    continue
                day_dt = monday_dt + timedelta(days=WEEKDAY_CODES.index(wcode))
                start_dt = day_dt + timedelta(minutes=start_min)
                end_dt = start_dt + timedelta(minutes=dur_min)
                leave_dt = start_dt - timedelta(minutes=travel_min)
                back_dt = end_dt + timedelta(minutes=travel_min)
                occ_id = f"{oid}@{start_dt.strftime('%Y-%m-%dT%H:%M')}"
                occurrences.append({
                    "occurrence_id": occ_id,
                    "obligation_id": oid,
                    "kind": str(ob.get("kind") or "routine"),
                    "weekday": wcode,
                    "start_at": start_dt.isoformat(timespec="seconds"),
                    "end_at": end_dt.isoformat(timespec="seconds"),
                    "leave_at": leave_dt.isoformat(timespec="seconds"),
                    "back_at": back_dt.isoformat(timespec="seconds"),
                    "_start_dt": start_dt,
                    "_leave_dt": leave_dt,
                    "_back_dt": back_dt,
                })
    occurrences.sort(key=lambda item: item["start_at"])
    return occurrences


def _derive_week_offers(
    obligations: Mapping[str, Any],
    *,
    instance_seed: str,
    pack_id: str,
    iso_year: int,
    iso_week: int,
    monday_dt: datetime,
) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for oid, ob in sorted(obligations.items()):
        if ob.get("mode") != "OFFERED":
            continue
        pw = ob.get("per_week_range") or [1, 2]
        min_r = ob.get("minutes_range") or [60, 120]
        lead_r = ob.get("lead_days_range") or [1, 3]
        pay_r = ob.get("pay_range")
        count = _sha_pick(int(pw[0]), int(pw[1]), instance_seed, pack_id, oid, iso_year, iso_week, "n")
        for idx in range(count):
            key = (instance_seed, pack_id, oid, iso_year, iso_week, idx)
            if idx == 0:
                offset_hours = 8 + _sha_pick(0, 3, *key, "h0")
            else:
                offset_hours = 8 + _sha_pick(12, 96, *key, "h")
            offered_dt = monday_dt + timedelta(hours=offset_hours)
            lead_days = _sha_pick(int(lead_r[0]), int(lead_r[1]), *key, "lead")
            due_dt = offered_dt + timedelta(days=lead_days)
            minutes = _sha_pick(int(min_r[0]), int(min_r[1]), *key, "min")
            pay_amt = (
                None
                if not isinstance(pay_r, list) or len(pay_r) != 2
                else _sha_pick(int(pay_r[0]), int(pay_r[1]), *key, "pay")
            )
            offer_id = f"{oid}#{iso_year}-W{iso_week:02d}.{idx}"
            offers.append({
                "offer_id": offer_id,
                "obligation_id": oid,
                "kind": str(ob.get("kind") or "external_offer"),
                "offered_at": offered_dt.isoformat(timespec="seconds"),
                "due_at": due_dt.isoformat(timespec="seconds"),
                "minutes": minutes,
                "pay_amount": pay_amt,
                "_offered_dt": offered_dt,
                "_due_dt": due_dt,
            })
    offers.sort(key=lambda item: item["offered_at"])
    return offers


def _in_busy_period(busy_periods: list[dict[str, Any]], now_dt: datetime) -> Optional[str]:
    md = now_dt.strftime("%m-%d")
    for p in busy_periods:
        start_md = str(p.get("from") or "")
        end_md = str(p.get("to") or "")
        if not start_md or not end_md:
            continue
        matched = (start_md <= md <= end_md) if start_md <= end_md else (md >= start_md or md <= end_md)
        if matched:
            return str(p.get("kind") or "busy_period")
    return None


def derive_weekly_low_mood_window(
    instance_seed: str, anchor_dt: datetime
) -> Optional[dict[str, Any]]:
    """Deterministically derive at most one daytime (2~6h) low-mood window for ISO week."""
    iso_year, iso_week, monday_dt = _week_monday_midnight(anchor_dt)
    window_key = f"{iso_year}-W{iso_week:02d}"
    u_gate = _sha_unit(instance_seed, iso_year, iso_week, "low_mood_gate")
    if u_gate >= LOW_MOOD_WEEKLY_CHANCE:
        return None
    day_offset = _sha_pick(0, 6, instance_seed, iso_year, iso_week, "day")
    start_hour = _sha_pick(
        LOW_MOOD_START_HOUR_RANGE[0],
        LOW_MOOD_START_HOUR_RANGE[1],
        instance_seed,
        iso_year,
        iso_week,
        "start_hour",
    )
    duration_hours = _sha_pick(
        LOW_MOOD_DURATION_HOUR_RANGE[0],
        LOW_MOOD_DURATION_HOUR_RANGE[1],
        instance_seed,
        iso_year,
        iso_week,
        "duration_hours",
    )
    if start_hour + duration_hours > 23:
        duration_hours = max(2, 23 - start_hour)
    start_dt = monday_dt + timedelta(days=day_offset, hours=start_hour)
    end_dt = start_dt + timedelta(hours=duration_hours)
    return {
        "window_key": window_key,
        "start_at": start_dt.isoformat(timespec="seconds"),
        "end_at": end_dt.isoformat(timespec="seconds"),
        "duration_hours": duration_hours,
        "_start_dt": start_dt,
        "_end_dt": end_dt,
    }


def _slept_since(last_woke_iso: Any, event_iso: Any) -> bool:
    woke_dt = _parse_iso(last_woke_iso)
    ev_dt = _parse_iso(event_iso)
    if woke_dt is None or ev_dt is None:
        return False
    return woke_dt > ev_dt


def _active_spaces(quarrel_dom: Mapping[str, Any], last_woke_iso: Any, now_dt: datetime) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for inc_ref, sp in sorted(quarrel_dom.get("spaces_by_incident", {}).items()):
        declared_iso = sp.get("declared_at")
        if _slept_since(last_woke_iso, declared_iso):
            continue
        dec_dt = _parse_iso(declared_iso)
        if dec_dt is None:
            continue
        style = str(sp.get("space_style") or "UNTIL_SLEEP")
        if style == "SHORT" and (now_dt - dec_dt).total_seconds() >= SHORT_SPACE_SECONDS:
            continue
        active.append({
            "incident_ref": inc_ref,
            "space_style": style,
            "declared_at": declared_iso,
            "spoken_aloud": True,
        })
    return active


def _live_frictions(quarrel_dom: Mapping[str, Any], now_dt: datetime) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
    for scope, f in sorted(quarrel_dom.get("frictions", {}).items()):
        if f.get("ended_reason") is not None:
            continue
        fades_dt = _parse_iso(f.get("fades_at"))
        if fades_dt is not None and now_dt < fades_dt:
            live.append({
                "friction_id": str(f.get("friction_id") or f"fric-{scope.lower()}"),
                "scope": scope,
                "incident_refs": list(f.get("incident_refs", [])),
                "since": f.get("since"),
                "fades_at": f.get("fades_at"),
            })
    return live


def _rupture_conditions_met(rupture: Mapping[str, Any], now_dt: datetime) -> bool:
    last_inc_dt = _parse_iso(rupture.get("last_incident_at")) or now_dt
    pos_events = [
        ev for ev in rupture.get("behavior_evidences", [])
        if (_parse_iso(ev.get("recorded_at")) or now_dt) >= last_inc_dt
    ]
    conditions = rupture.get("repair_conditions") or []
    if not conditions:
        return False
    for cond in conditions:
        kind = cond.get("kind")
        if kind == "NO_RECURRENCE":
            days = int(cond.get("window_days", 1))
            if (now_dt - last_inc_dt).total_seconds() < days * 86400:
                return False
        elif kind in ("CHANGE_SEEN", "RESPECTED_OCCASIONS"):
            # QR10: CHANGE_SEEN requires real behavioral change, never RECOGNITION alone.
            allowed = (
                REAL_ACTION_REPAIR_KINDS
                if kind == "CHANGE_SEEN"
                else HOST_ALLOWED_BEHAVIOR_KINDS
            )
            hits = [ev for ev in pos_events if ev.get("behavior_kind") in allowed]
            min_cnt = int(cond.get("min_count", 1))
            dist_days = int(cond.get("distinct_days", 1))
            day_keys = {
                str(ev.get("calendar_day") or str(ev.get("recorded_at", ""))[:10])
                for ev in hits
            }
            if len(hits) < min_cnt or len(day_keys) < dist_days:
                return False
        elif kind == "COMMITMENT_KEPT":
            if not any(ev.get("behavior_kind") in REAL_ACTION_REPAIR_KINDS for ev in pos_events):
                return False
    return True


CROSS_STORE_GONE_STATUSES: frozenset[str] = frozenset({
    "consumed",
    "discarded",
    "returned_out",
    "deleted",
})


def _cross_store_source_exists(
    root: Path | str, ref: Optional[str]
) -> Optional[bool]:
    """Zero-write cross-store existence check for Ch.42 / Ch.44 §二 ``SOURCE_GONE``.

    Only structured cross-repository refs intervene; legacy un-prefixed ids return
    ``None`` so every historical caller keeps its previous behaviour. A missing
    store file (or missing row) means the source is gone (``False``); this helper
    never creates a directory and never writes.
    """

    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if not text or ":" not in text:
        return None
    prefix, _, target = text.partition(":")
    target = target.strip()
    if prefix not in ("matter", "v04_card", "v04_project"):
        return None
    if not target:
        return False
    root_path = Path(root)

    if prefix == "matter":
        doc = _safe_read_json_mapping(root_path / "world" / "v08_matter_space.json")
        if doc is None:
            return False
        objects = doc.get("matter_objects")
        if not isinstance(objects, Mapping):
            objects = doc.get("objects")
        if not isinstance(objects, Mapping):
            return False
        row = objects.get(target)
        if not isinstance(row, Mapping):
            return False
        if any(
            row.get(flag) is True
            for flag in ("consumed", "discarded", "returned_out", "delivered_to_board")
        ):
            return False
        if str(row.get("status") or "").strip().lower() in CROSS_STORE_GONE_STATUSES:
            return False
        return True

    if prefix == "v04_card":
        doc = _safe_read_json_mapping(
            root_path / "world" / "v04_neighborhood_state.json"
        )
        if doc is None:
            return False
        bench = doc.get("craft_bench")
        if not isinstance(bench, Mapping):
            return False
        cards = bench.get("cards")
        if isinstance(cards, Mapping):
            return isinstance(cards.get(target), Mapping)
        if isinstance(cards, list):
            return any(
                isinstance(row, Mapping) and row.get("card_id") == target
                for row in cards
            )
        return False

    # v04_project
    doc = _safe_read_json_mapping(root_path / "phone" / "v04_elastic_play.json")
    if doc is None:
        return False
    projects = doc.get("personal_projects")
    if not isinstance(projects, list):
        return False
    for row in projects:
        if not isinstance(row, Mapping) or row.get("project_id") != target:
            continue
        if row.get("deleted") is True:
            return False
        if str(row.get("status") or "").strip().lower() in CROSS_STORE_GONE_STATUSES:
            return False
        return True
    return False


def _stored_emotional_source_refs(
    missing_dom: Mapping[str, Any], asking_dom: Mapping[str, Any]
) -> list[str]:
    """Every ref currently held by kept notes / open or deferred favors."""

    refs: list[str] = []
    for note in missing_dom.get("kept_notes") or []:
        if isinstance(note, Mapping):
            ref = note.get("source_ref")
            if isinstance(ref, str) and ref.strip():
                refs.append(ref.strip())
    open_fav = asking_dom.get("open_favor")
    if isinstance(open_fav, Mapping):
        ref = open_fav.get("target_object_ref")
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
    deferred = asking_dom.get("deferred_favors")
    if isinstance(deferred, Mapping):
        for fav in deferred.values():
            if not isinstance(fav, Mapping):
                continue
            ref = fav.get("target_object_ref")
            if isinstance(ref, str) and ref.strip():
                refs.append(ref.strip())
    return refs


def _invalidated_refs_for(
    root: Optional[Path | str],
    ctx: Mapping[str, Any],
    extra_refs: tuple[str, ...] | list[str] = (),
) -> set[str]:
    """Stored invalidations plus cross-store refs whose real source is gone.

    Read-only: when ``root`` is ``None`` (pure in-memory projection) only the
    persisted invalidations apply. Cross-store checks never write.
    """

    refs = {str(r) for r in ctx.get("invalidated_source_refs", [])}
    if root is None:
        return refs
    for ref in extra_refs:
        if not isinstance(ref, str) or ref in refs:
            continue
        if _cross_store_source_exists(root, ref) is False:
            refs.add(ref)
    return refs


def _valid_kept_notes(
    missing_dom: Mapping[str, Any],
    invalidated_refs: set[str],
    now_dt: datetime,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for note in missing_dom.get("kept_notes", []):
        sref = str(note.get("source_ref") or "")
        if sref in invalidated_refs:
            continue
        created_dt = _parse_iso(note.get("created_at"))
        if created_dt is None or (now_dt - created_dt).total_seconds() >= MISSING_KEEP_SECONDS:
            continue
        valid.append(dict(note))
    valid.sort(key=lambda n: str(n.get("created_at") or ""), reverse=True)
    return valid[:MAX_MISSING_NOTES]


def _active_open_favor(
    asking_dom: Mapping[str, Any],
    invalidated_refs: set[str],
    now_dt: datetime,
) -> Optional[dict[str, Any]]:
    favor = asking_dom.get("open_favor")
    if not isinstance(favor, Mapping):
        return None
    fkind = str(favor.get("favor_kind") or "")
    raw_ref = favor.get("target_object_ref")
    target_ref = str(raw_ref).strip() if isinstance(raw_ref, str) else ""
    if fkind not in COMPANY_FAVOR_KINDS and not target_ref:
        return None
    if target_ref and target_ref in invalidated_refs:
        return None
    asked_dt = _parse_iso(favor.get("asked_at"))
    if asked_dt is None or (now_dt - asked_dt).total_seconds() >= FAVOR_EXPIRE_SECONDS:
        return None
    return dict(favor)


def _active_deferred_favors(
    asking_dom: Mapping[str, Any],
    invalidated_refs: set[str],
    last_woke_iso: Any,
    now_dt: datetime,
) -> list[dict[str, Any]]:
    raw_map = asking_dom.get("deferred_favors")
    if not isinstance(raw_map, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for fav_id, fav in sorted(raw_map.items()):
        if not isinstance(fav, Mapping) or fav.get("status") != "DEFERRED_LATER":
            continue
        def_iso = fav.get("deferred_at") or fav.get("asked_at")
        if _slept_since(last_woke_iso, def_iso):
            continue
        raw_ref = fav.get("target_object_ref")
        target_ref = str(raw_ref).strip() if isinstance(raw_ref, str) else ""
        if target_ref and target_ref in invalidated_refs:
            continue
        exp_dt = _parse_iso(fav.get("expires_at"))
        if exp_dt is not None and now_dt >= exp_dt:
            continue
        out.append(dict(fav))
    return out


def _active_declined_favor_kinds(
    asking_dom: Mapping[str, Any], last_woke_iso: Any
) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, dec_iso in asking_dom.get("declined_kinds_until_sleep", {}).items():
        if k in FAVOR_KINDS and not _slept_since(last_woke_iso, dec_iso):
            out[k] = str(dec_iso)
    return out


def _derive_schedule_projection(
    state: Mapping[str, Any],
    *,
    char_bundle: Mapping[str, Any],
    now_dt: datetime,
    subject_view: bool,
    private_scene_active: bool = False,
) -> tuple[dict[str, Any], bool]:
    sched = state["schedule_domain"]
    ctx = state["context"]
    obligations = _effective_obligations(sched, char_bundle)
    occ_decisions = sched["occurrence_decisions"]
    offer_decisions = sched["offer_decisions"]

    ecology_info = char_bundle.get("ecology") if isinstance(char_bundle.get("ecology"), Mapping) else {}
    sick_leave = bool(ecology_info.get("sick_leave", False))
    intimacy_info = (
        char_bundle.get("adult_intimacy")
        if isinstance(char_bundle.get("adult_intimacy"), Mapping)
        else {}
    )
    chores = copy.deepcopy(list(intimacy_info.get("chores") or []))

    family_channel = FamilyChannel(
        list(char_bundle.get("family_members") or []),
        instance_seed=str(state["instance_seed"]),
        pack_id=str(char_bundle["pack_id"]),
    ).project(
        now_dt,
        private_scene_active=private_scene_active,
        subject_view=subject_view,
    )

    iso_year, iso_week, monday_dt = _week_monday_midnight(now_dt)
    prev_monday = monday_dt - timedelta(days=7)
    next_monday = monday_dt + timedelta(days=7)

    prev_occs = _derive_week_occurrences(obligations, prev_monday)
    curr_occs = _derive_week_occurrences(obligations, monday_dt)
    next_occs = _derive_week_occurrences(obligations, next_monday)

    partner_status = "FREE"
    leaving_soon = False
    for occ in prev_occs + curr_occs + next_occs:
        occ_id = occ["occurrence_id"]
        explicit_dec = occ_decisions.get(occ_id)
        if explicit_dec == "SKIP" or (sick_leave and explicit_dec != "ATTEND"):
            continue
        if occ["_leave_dt"] <= now_dt < occ["_back_dt"]:
            partner_status = "OUT"
        if now_dt <= occ["_leave_dt"] < now_dt + timedelta(seconds=BUSY_SOON_SECONDS):
            leaving_soon = True

    busy_override = ctx.get("subject_busy_override")
    is_busy = (
        bool(busy_override)
        if isinstance(busy_override, bool)
        else (partner_status == "OUT" or leaving_soon)
    )

    busy_period_kind = _in_busy_period(char_bundle.get("busy_periods", []), now_dt)
    due_soon = False
    active_deadlines: list[dict[str, Any]] = []
    for offer_id, dl in sorted(sched["deadlines"].items()):
        due_dt = _parse_iso(dl.get("due_at"))
        if due_dt is not None and now_dt <= due_dt <= now_dt + timedelta(seconds=DEADLINE_STRESS_WINDOW_SECONDS):
            due_soon = True
        active_deadlines.append({
            "offer_id": offer_id,
            "obligation_id": dl.get("obligation_id"),
            "due_at": dl.get("due_at"),
            "minutes": dl.get("minutes"),
            "pay_amount": dl.get("pay_amount"),
        })

    sleep_debt_h = float(ctx.get("sleep_debt_hours") or 0.0)
    stress_val = round(
        min(
            1.0,
            max(
                0.0,
                0.5 * (1.0 if busy_period_kind and not sick_leave else 0.0)
                + 0.3 * (1.0 if due_soon and not sick_leave else 0.0)
                + 0.2 * min(1.0, sleep_debt_h / 4.0),
            ),
        ),
        4,
    )

    pronoun = str(char_bundle["pronouns"])
    base_view: dict[str, Any] = {
        "partner_status": partner_status,
        "is_busy": is_busy,
        "availability_summary": (
            f"{pronoun}身体不适在家休养中"
            if sick_leave and partner_status == "FREE"
            else (f"{pronoun}正忙或外出中" if is_busy else f"{pronoun}在，目前有空")
        ),
        "sick_leave": sick_leave,
        "exemption_status": "SICK_LEAVE" if sick_leave else None,
        "family_channel": family_channel,
        "chores": chores,
        "exposes_private_memo": False,
        "missed_adds_debt_or_stress": False,
    }
    if not subject_view:
        return base_view, is_busy

    raw_offers = _derive_week_offers(
        obligations,
        instance_seed=str(state["instance_seed"]),
        pack_id=str(char_bundle["pack_id"]),
        iso_year=iso_year,
        iso_week=iso_week,
        monday_dt=monday_dt,
    )
    pending_offers = [
        {
            k: v
            for k, v in off.items()
            if not k.startswith("_")
        }
        for off in raw_offers
        if off["offer_id"] not in offer_decisions and off["_offered_dt"] <= now_dt
    ]
    public_curr_occs = [
        {
            **{k: v for k, v in occ.items() if not k.startswith("_")},
            "decision": occ_decisions.get(occ["occurrence_id"])
            or ("SICK_LEAVE" if sick_leave else None),
            "exemption": (
                "SICK_LEAVE"
                if sick_leave and occ_decisions.get(occ["occurrence_id"]) != "ATTEND"
                else None
            ),
        }
        for occ in curr_occs
    ]
    subject_sched = dict(base_view)
    subject_sched.update({
        "exposes_private_memo": True,
        "busy_period_kind": busy_period_kind,
        "due_within_48h": due_soon,
        "stress_index": stress_val,
        "obligations": copy.deepcopy(obligations),
        "weekly_occurrences": public_curr_occs,
        "pending_offers": pending_offers,
        "deadlines": active_deadlines,
        "facts": copy.deepcopy(sched["facts"]),
        "income_claims": copy.deepcopy(sched["income_claims"]),
    })
    return subject_sched, is_busy


def project_emotional_tension(
    state: Mapping[str, Any],
    *,
    char_bundle: Mapping[str, Any],
    now_dt: datetime,
    now_iso: str,
    subject_view: bool = False,
    root: Optional[Path | str] = None,
) -> dict[str, Any]:
    ctx = state["context"]
    q_dom = state["quarrel_domain"]
    m_dom = state["missing_domain"]
    lm_dom = state["low_mood_domain"]
    ask_dom = state["asking_domain"]
    invalidated_refs = _invalidated_refs_for(
        root,
        ctx,
        extra_refs=_stored_emotional_source_refs(m_dom, ask_dom),
    )
    last_woke_iso = ctx.get("last_woke_at")

    intimacy_info = (
        char_bundle.get("adult_intimacy")
        if isinstance(char_bundle.get("adult_intimacy"), Mapping)
        else {}
    )
    effective_private_scene = bool(ctx.get("private_scene_active", False)) or bool(
        intimacy_info.get("active_scene", False)
    )

    schedule_proj, is_busy = _derive_schedule_projection(
        state,
        char_bundle=char_bundle,
        now_dt=now_dt,
        subject_view=subject_view,
        private_scene_active=effective_private_scene,
    )

    # Quarrel projection
    live_frics = _live_frictions(q_dom, now_dt)
    spaces = _active_spaces(q_dom, last_woke_iso, now_dt)
    in_space = bool(spaces)
    ruptures_list = [copy.deepcopy(r) for _, r in sorted(q_dom["ruptures"].items())]
    open_rel_rupture_ids = [
        r["rupture_id"]
        for r in ruptures_list
        if r["status"] in LIVE_RUPTURE_STATUSES and r["scope"] in RELATIONAL_SCOPES
    ]
    lev_until_dt = _parse_iso(q_dom.get("leverage_cooldown_until"))
    lev_active = lev_until_dt is not None and now_dt < lev_until_dt
    quarrel_blocks_intimacy = bool(open_rel_rupture_ids) or in_space

    quarrel_proj: dict[str, Any] = {
        "frictions": live_frics,
        "ruptures": ruptures_list,
        "disagreements": [copy.deepcopy(d) for _, d in sorted(q_dom["disagreements"].items())],
        "in_space": in_space,
        "active_spaces": spaces,
        "subject_still_replies": True,
        "subject_initiates_contact": not in_space,
        "open_relational_rupture_ids": open_rel_rupture_ids,
        "blocks_subject_intimacy_initiation": quarrel_blocks_intimacy,
        "repair_requires_behavior_change": True,
        "apology_or_gift_is_repair": False,
        "absence_is_incident": False,
        "leverage_cooldown_active": lev_active,
        "leverage_cooldown_until": q_dom.get("leverage_cooldown_until") if lev_active else None,
    }

    # Low mood projection
    lm_win = derive_weekly_low_mood_window(str(state["instance_seed"]), now_dt)
    in_low_mood = False
    acked_this_spell = False
    if lm_win is not None:
        wkey = lm_win["window_key"]
        if (
            lm_win["_start_dt"] <= now_dt < lm_win["_end_dt"]
            and lm_dom.get("self_lifted_window_key") != wkey
        ):
            in_low_mood = True
            acked_this_spell = lm_dom.get("acknowledged_window_key") == wkey

    intrusion_burden = (
        round(max(0.0, min(1.0, float(lm_dom.get("intrusion_burden") or 0.0))), 4)
        if in_low_mood
        else 0.0
    )
    low_mood_proj: dict[str, Any] = {
        "in_low_mood_spell": in_low_mood,
        "acknowledged_once": acked_this_spell,
        "cause": None,
        "blame_partner": False,
        "intrusion_burden": intrusion_burden,
        "subject_still_replies": True,
        "shorter_replies": in_low_mood,
        "early_clear_by_partner": False,
        "reads_quarrel_or_body_or_cycle": False,
        "leaves_historical_trace": False,
        "max_spells_per_week": 1,
        "blocks_subject_intimacy_initiation": in_low_mood,
    }
    if subject_view and in_low_mood and lm_win is not None:
        low_mood_proj["current_spell_window"] = {
            "window_key": lm_win["window_key"],
            "start_at": lm_win["start_at"],
            "end_at": lm_win["end_at"],
            "duration_hours": lm_win["duration_hours"],
        }

    # Missing partner projection (MH09: suppress proactive notes in_space / partner_urgent / active_intimacy)
    kept_valid = _valid_kept_notes(m_dom, invalidated_refs, now_dt)
    surfaced_set = set(str(nid) for nid in m_dom.get("surfaced_note_ids", []))
    suppress_proactive_missing = (
        in_space
        or bool(ctx.get("partner_urgent", False))
        or effective_private_scene
    )
    surfaced_notes = (
        []
        if suppress_proactive_missing
        else [
            {
                "note_id": n["note_id"],
                "source_ref": n["source_ref"],
                "kind": n["kind"],
                "summary": n["summary"],
                "created_at": n["created_at"],
            }
            for n in kept_valid
            if n["note_id"] in surfaced_set
        ]
    )
    missing_proj: dict[str, Any] = {
        "surfaced_notes": surfaced_notes,
        "proactive_suppressed": suppress_proactive_missing,
        "tracks_absence_duration": False,
        "accumulates_longing_weight": False,
        "sends_outbound_harassment": False,
        "max_kept_notes": MAX_MISSING_NOTES,
        "fade_hours": 72,
    }
    if subject_view:
        missing_proj["kept_notes"] = kept_valid
        missing_proj["kept_count"] = len(kept_valid)

    # Asking partner projection
    open_fav = _active_open_favor(ask_dom, invalidated_refs, now_dt)
    deferred_favs = _active_deferred_favors(ask_dom, invalidated_refs, last_woke_iso, now_dt)
    declined_map = _active_declined_favor_kinds(ask_dom, last_woke_iso)
    asking_proj: dict[str, Any] = {
        "allowed_favor_kinds": list(FAVOR_KINDS),
        "open_favor": open_fav,
        "deferred_favors": deferred_favs,
        "max_open_favors": 1,
        "expire_hours": 2,
        "expired_counts_as_decline": False,
        "decline_requires_reason": False,
        "records_favor_debt": False,
        "favor_ledger_count": 0,
    }
    if subject_view:
        asking_proj["cooldown_favor_kinds_until_sleep"] = sorted(declined_map.keys())

    blocks_intimacy = quarrel_blocks_intimacy or in_low_mood
    return {
        "schema": EMOTIONAL_TENSION_SCHEMA,
        "revision": int(state["revision"]),
        "updated_at": state.get("updated_at"),
        "evaluated_at": now_iso,
        "view": "subject" if subject_view else "partner",
        "character": {
            "pack_id": char_bundle["pack_id"],
            "display_name": char_bundle["display_name"],
            "pronouns": char_bundle["pronouns"],
        },
        "context": {
            "partner_present": bool(ctx.get("partner_present", True)),
            "partner_urgent": bool(ctx.get("partner_urgent", False)),
            "private_scene_active": effective_private_scene,
            "last_woke_at": last_woke_iso,
        },
        "tension_summary": {
            "partner_status": schedule_proj["partner_status"],
            "subject_is_busy": is_busy,
            "in_space": in_space,
            "in_low_mood_spell": in_low_mood,
            "blocks_subject_intimacy_initiation": blocks_intimacy,
            "has_open_favor": open_fav is not None,
        },
        "schedule": schedule_proj,
        "quarrel": quarrel_proj,
        "missing_partner": missing_proj,
        "low_mood": low_mood_proj,
        "asking_partner": asking_proj,
    }


def read_emotional_tension(
    root: Path | str,
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Read emotional tension projection; 100% zero-write when state file does not exist."""
    now_dt, now_iso = _normalize_now(now)
    state, state_reason = read_emotional_tension_state(root)
    if state_reason is not None:
        raise EmotionalTensionConflictError(state_reason)
    char_bundle = _read_character_pack_bundle(root)
    return project_emotional_tension(
        state,
        char_bundle=char_bundle,
        now_dt=now_dt,
        now_iso=now_iso,
        subject_view=bool(subject_view),
        root=root,
    )


def _latest_expired_tension_cue(
    state: Mapping[str, Any], now_dt: datetime
) -> Optional[dict[str, Any]]:
    """Read-only identifier of the newest expired favor / not-a-repair apology."""

    newest: Optional[tuple[datetime, dict[str, Any]]] = None
    ask_dom = state.get("asking_domain")
    if isinstance(ask_dom, Mapping):
        open_fav = ask_dom.get("open_favor")
        if isinstance(open_fav, Mapping):
            exp_dt = _parse_iso(open_fav.get("expires_at"))
            if exp_dt is not None and now_dt >= exp_dt:
                newest = (
                    exp_dt,
                    {
                        "kind": "FAVOR_EXPIRED",
                        "favor_id": str(open_fav.get("favor_id") or ""),
                        "favor_kind": str(open_fav.get("favor_kind") or ""),
                        "at": open_fav.get("expires_at"),
                    },
                )
    q_dom = state.get("quarrel_domain")
    ruptures = q_dom.get("ruptures") if isinstance(q_dom, Mapping) else None
    if isinstance(ruptures, Mapping):
        for rup_id, rup in ruptures.items():
            if not isinstance(rup, Mapping):
                continue
            for attempt in rup.get("non_repair_attempts") or []:
                if not isinstance(attempt, Mapping):
                    continue
                if attempt.get("attempt_kind") != "APOLOGY":
                    continue
                at_dt = _parse_iso(attempt.get("at"))
                if at_dt is None:
                    continue
                if newest is None or at_dt >= newest[0]:
                    newest = (
                        at_dt,
                        {
                            "kind": "APOLOGY_NOT_REPAIR",
                            "rupture_id": str(rup_id),
                            "at": attempt.get("at"),
                        },
                    )
    return newest[1] if newest is not None else None


def scan_emotional_tension_cues_zero_write(
    root: Path | str,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Zero-write emotional-tension cue summary for the daily loop / context.

    A missing state file yields a calm empty summary and creates nothing.
    Private note text and favor wording never leak; only counts and ids do.
    """

    now_dt, now_iso = _normalize_now(now)
    state, state_reason = read_emotional_tension_state(root)
    calm: dict[str, Any] = {
        "in_low_mood": False,
        "active_spaces_count": 0,
        "open_ruptures_count": 0,
        "unsurfaced_missing_notes_count": 0,
        "open_favor": None,
        "declined_kinds_until_sleep": [],
        "latest_expired_favor_or_rejected_apology": None,
        "evaluated_at": now_iso,
        "read_only": True,
        "private_content_excluded": True,
    }
    if state_reason is not None:
        return calm

    char_bundle = _read_character_pack_bundle(root)
    proj = project_emotional_tension(
        state,
        char_bundle=char_bundle,
        now_dt=now_dt,
        now_iso=now_iso,
        subject_view=True,
        root=root,
    )

    quarrel = proj["quarrel"]
    ruptures = quarrel.get("ruptures") or []
    open_ruptures_count = sum(
        1
        for row in ruptures
        if isinstance(row, Mapping) and row.get("status") in LIVE_RUPTURE_STATUSES
    )

    m_dom = state["missing_domain"]
    kept_notes = proj["missing_partner"].get("kept_notes") or []
    surfaced = {str(nid) for nid in m_dom.get("surfaced_note_ids", [])}
    shared = {str(nid) for nid in m_dom.get("shared_note_ids", [])}
    unsurfaced = sum(
        1
        for note in kept_notes
        if isinstance(note, Mapping)
        and str(note.get("note_id") or "") not in surfaced
        and str(note.get("note_id") or "") not in shared
    )

    open_fav_raw = proj["asking_partner"].get("open_favor")
    open_favor: Optional[dict[str, Any]] = None
    if isinstance(open_fav_raw, Mapping):
        open_favor = {
            "favor_id": open_fav_raw.get("favor_id"),
            "favor_kind": open_fav_raw.get("favor_kind"),
            "status": open_fav_raw.get("status"),
            "asked_at": open_fav_raw.get("asked_at"),
            "expires_at": open_fav_raw.get("expires_at"),
            "no_obligation_when_declined": True,
        }

    declined = sorted(
        _active_declined_favor_kinds(
            state["asking_domain"], state["context"].get("last_woke_at")
        ).keys()
    )

    return {
        "in_low_mood": bool(proj["low_mood"].get("in_low_mood_spell")),
        "active_spaces_count": len(quarrel.get("active_spaces") or []),
        "open_ruptures_count": int(open_ruptures_count),
        "unsurfaced_missing_notes_count": int(unsurfaced),
        "open_favor": open_favor,
        "declined_kinds_until_sleep": declined,
        "latest_expired_favor_or_rejected_apology": _latest_expired_tension_cue(
            state, now_dt
        ),
        "evaluated_at": now_iso,
        "read_only": True,
        "private_content_excluded": True,
    }


# ---------------------------------------------------------------------------------------------
# Action Receipt Idempotency, Capability Guard & Action Dispatcher
# ---------------------------------------------------------------------------------------------


def _lookup_receipt(
    receipts: list[dict[str, Any]],
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    for rec in receipts:
        if rec.get("operation_key") == operation_key:
            if rec.get("action") != action or rec.get("fingerprint") != fingerprint:
                raise EmotionalTensionConflictError("OPERATION_KEY_PAYLOAD_MISMATCH")
            return rec
    return None


def _next_op_seq(state: Mapping[str, Any]) -> int:
    highest = 0
    raw = state.get("next_op_seq")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > highest:
        highest = raw
    for field in ("operation_receipts", "committed_op_ring"):
        rows = state.get(field)
        if not isinstance(rows, list):
            continue
        for rec in rows:
            if not isinstance(rec, Mapping):
                continue
            seq = rec.get("op_seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq > highest:
                highest = seq
    return highest + 1


def _lookup_ring_receipt(
    ring: Any,
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
) -> Optional[dict[str, Any]]:
    """Match an evicted receipt in ``committed_op_ring`` (安全重放 / 409 同键不同意)."""

    if not isinstance(ring, list):
        return None
    for rec in reversed(ring):
        if not isinstance(rec, Mapping):
            continue
        if rec.get("operation_key") != operation_key:
            continue
        if rec.get("action") != action or rec.get("fingerprint") != fingerprint:
            raise EmotionalTensionConflictError("OPERATION_KEY_PAYLOAD_MISMATCH")
        return dict(rec)
    return None


def _record_receipt(
    state: dict[str, Any],
    *,
    operation_key: str,
    action: str,
    fingerprint: str,
    target_id: Optional[str],
    now_iso: str,
) -> None:
    """Append a receipt in commit order; evict oldest by ``op_seq`` into the ring."""

    receipts = state.setdefault("operation_receipts", [])
    ring = state.setdefault("committed_op_ring", [])
    if not isinstance(ring, list):
        ring = []
        state["committed_op_ring"] = ring

    op_seq = _next_op_seq(state)
    state["next_op_seq"] = op_seq
    receipts.append({
        "operation_key": operation_key,
        "action": action,
        "fingerprint": fingerprint,
        "target_id": target_id,
        "recorded_at": now_iso,
        "op_seq": op_seq,
        "revision": int(state.get("revision") or 0),
    })

    while len(receipts) > MAX_OPERATION_RECEIPTS:
        oldest = min(
            receipts,
            key=lambda rec: (
                int(rec.get("op_seq") or 0) if isinstance(rec, Mapping) else -1
            ),
        )
        receipts.remove(oldest)
        if not isinstance(oldest, Mapping):
            continue
        ring.append({
            "operation_key": oldest.get("operation_key"),
            "action": oldest.get("action"),
            "fingerprint": oldest.get("fingerprint"),
            "target_id": oldest.get("target_id"),
            "recorded_at": oldest.get("recorded_at"),
            "op_seq": oldest.get("op_seq"),
            "revision": oldest.get("revision"),
        })
        evicted_rev = oldest.get("revision")
        if isinstance(evicted_rev, int) and not isinstance(evicted_rev, bool):
            current = state.get("evicted_up_to_revision")
            base = current if isinstance(current, int) and not isinstance(current, bool) else 0
            state["evicted_up_to_revision"] = max(base, evicted_rev)

    while len(ring) > MAX_COMMITTED_OP_RING:
        ring.pop(0)


def _resolve_actor(
    payload: Mapping[str, Any], *, subject_view: bool, allow_host: bool = False
) -> str:
    allowed = ("subject", "partner", "host") if allow_host else ("subject", "partner")
    if "actor" not in payload:
        return "subject" if subject_view else "partner"
    actor = payload.get("actor")
    if not isinstance(actor, str) or actor not in allowed:
        raise EmotionalTensionValidationError("ACTOR_INVALID")
    if actor == "subject" and not subject_view:
        raise EmotionalTensionPermissionError("SUBJECT_CAPABILITY_REQUIRED")
    return actor


def _validate_repair_conditions(raw_conds: Any) -> list[dict[str, Any]]:
    if raw_conds is None:
        return [{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}]
    if not isinstance(raw_conds, list) or not raw_conds:
        raise EmotionalTensionValidationError("REPAIR_CONDITIONS_INVALID")
    validated: list[dict[str, Any]] = []
    for item in raw_conds:
        if not isinstance(item, Mapping):
            raise EmotionalTensionValidationError("REPAIR_CONDITIONS_INVALID")
        kind = item.get("kind")
        if kind not in REPAIR_CONDITION_KINDS:
            raise EmotionalTensionValidationError("REPAIR_CONDITION_KIND_INVALID")
        if kind == "NO_RECURRENCE":
            days = _require_int(
                item.get("window_days", 1),
                minimum=1,
                maximum=60,
                reason_code="WINDOW_DAYS_INVALID",
            )
            validated.append({"kind": kind, "window_days": days})
        elif kind in ("CHANGE_SEEN", "RESPECTED_OCCASIONS"):
            min_cnt = _require_int(
                item.get("min_count", 1),
                minimum=1,
                maximum=5,
                reason_code="MIN_COUNT_INVALID",
            )
            dist_days = _require_int(
                item.get("distinct_days", 1),
                minimum=1,
                maximum=min_cnt,
                reason_code="DISTINCT_DAYS_INVALID",
            )
            validated.append({"kind": kind, "min_count": min_cnt, "distinct_days": dist_days})
        else:
            cref = _require_id(
                item.get("commitment_ref", "commit-default"),
                reason_code="COMMITMENT_REF_INVALID",
            )
            validated.append({"kind": kind, "commitment_ref": cref})
    return validated


def _check_forbidden_tags(
    payload: Mapping[str, Any],
    field: str,
    forbidden: tuple[str, ...],
    *,
    conflict_code: str,
) -> None:
    if field not in payload:
        return
    tags = _require_tag_list(payload.get(field), reason_code=f"{field.upper()}_INVALID")
    hit = sorted(set(tags) & set(forbidden))
    if hit:
        raise EmotionalTensionConflictError(f"{conflict_code}:{hit[0]}")


def apply_emotional_tension_action(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Validate and apply one explicit action under one exclusive write lock.

    The whole read-check-mutate-write cycle runs inside ``state_file_lock`` so
    two writers can never interleave: stale ``expected_revision`` is re-checked
    against the freshly re-read state, never against a pre-lock copy.
    """

    if not isinstance(payload, Mapping):
        raise EmotionalTensionValidationError("PAYLOAD_NOT_OBJECT")
    with _tx.state_file_lock(emotional_tension_state_path(root)):
        return _apply_emotional_tension_action_locked(
            root, payload, now=now, subject_view=subject_view
        )


def _apply_emotional_tension_action_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Inner write body; callers must already hold ``state_file_lock``."""
    if not isinstance(payload, Mapping):
        raise EmotionalTensionValidationError("PAYLOAD_NOT_OBJECT")

    now_dt, now_iso = _normalize_now(now)

    for raw_k in payload.keys():
        if not isinstance(raw_k, str) or not raw_k.strip():
            raise EmotionalTensionValidationError("EMPTY_FIELD_NAME")
        if raw_k in FORBIDDEN_FOREIGN_KEYS:
            raise EmotionalTensionValidationError(f"UNKNOWN_FIELD:{raw_k}")

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise EmotionalTensionValidationError("ACTION_REQUIRED")
    if raw_action != raw_action.strip() or "\x00" in raw_action:
        raise EmotionalTensionValidationError("ACTION_INVALID")
    action = raw_action.strip()
    if action not in ACTION_ALLOWED_FIELDS:
        raise EmotionalTensionValidationError(f"ACTION_INVALID:{action}")

    allowed_fields = ACTION_ALLOWED_FIELDS[action]
    extra_fields = sorted(set(payload.keys()) - set(allowed_fields))
    if extra_fields:
        raise EmotionalTensionValidationError(f"UNKNOWN_FIELD:{extra_fields[0]}")

    for k, v in payload.items():
        if k == "action":
            continue
        if v is None:
            if action == "ask_partner_favor" and k in ("target_object_ref", "ref"):
                continue
            raise EmotionalTensionValidationError(f"NULL_FIELD_NOT_ALLOWED:{k}")
        if isinstance(v, str) and not v.strip():
            raise EmotionalTensionValidationError(f"EMPTY_STRING_FIELD:{k}")

    if action in SUBJECT_ONLY_ACTIONS:
        actor_check = _resolve_actor(payload, subject_view=bool(subject_view))
        if not subject_view or actor_check != "subject":
            raise EmotionalTensionPermissionError("SUBJECT_CAPABILITY_REQUIRED")

    current_state, state_reason = read_emotional_tension_state(root)
    if state_reason is not None:
        raise EmotionalTensionConflictError(state_reason)

    state = copy.deepcopy(current_state)
    char_bundle = _read_character_pack_bundle(root)

    expected_rev: Optional[int] = None
    if "expected_revision" in payload:
        expected_rev = _require_int(
            payload.get("expected_revision"),
            minimum=0,
            maximum=10_000_000,
            reason_code="EXPECTED_REVISION_INVALID",
        )
    op_key: Optional[str] = None
    if "operation_key" in payload:
        op_key = _clean_text(
            payload.get("operation_key"),
            min_len=1,
            max_len=MAX_OPERATION_KEY,
            reason_code="OPERATION_KEY_INVALID",
        )

    intent_fields = {
        k: v for k, v in payload.items() if k not in ("action", "expected_revision")
    }
    fp = _fingerprint(action, intent_fields)

    if op_key is not None:
        existing_rec = _lookup_receipt(
            state["operation_receipts"],
            operation_key=op_key,
            action=action,
            fingerprint=fp,
        )
        if existing_rec is None:
            existing_rec = _lookup_ring_receipt(
                state.get("committed_op_ring"),
                operation_key=op_key,
                action=action,
                fingerprint=fp,
            )
        if existing_rec is not None:
            proj = project_emotional_tension(
                state,
                char_bundle=char_bundle,
                now_dt=now_dt,
                now_iso=now_iso,
                subject_view=bool(subject_view),
                root=root,
            )
            out_replay = dict(proj)
            out_replay.update({
                "ok": True,
                "status": "already_applied",
                "action": action,
                "changed": False,
                "replayed": True,
                "reason_code": None,
                "target_id": existing_rec.get("target_id"),
            })
            return out_replay

    if op_key is not None and expected_rev is not None:
        evicted_up_to = state.get("evicted_up_to_revision")
        current_rev = int(state["revision"])
        if (
            isinstance(evicted_up_to, int)
            and not isinstance(evicted_up_to, bool)
            and evicted_up_to > 0
            and expected_rev <= evicted_up_to < current_rev
        ):
            raise EmotionalTensionConflictError("RETRY_WINDOW_EXPIRED")

    if expected_rev is not None and int(state["revision"]) != expected_rev:
        raise EmotionalTensionConflictError("REVISION_CONFLICT")

    ctx = state["context"]
    sched_dom = state["schedule_domain"]
    q_dom = state["quarrel_domain"]
    m_dom = state["missing_domain"]
    lm_dom = state["low_mood_domain"]
    ask_dom = state["asking_domain"]

    def _commit(
        extra: Mapping[str, Any],
        *,
        status_label: str = "applied",
        reason_code: Optional[str] = None,
        receipt_target: Optional[str] = None,
    ) -> dict[str, Any]:
        state["revision"] = int(state["revision"]) + 1
        state["updated_at"] = now_iso
        if op_key is not None:
            _record_receipt(
                state,
                operation_key=op_key,
                action=action,
                fingerprint=fp,
                target_id=receipt_target,
                now_iso=now_iso,
            )
        write_emotional_tension_state(root, state)
        proj = project_emotional_tension(
            state,
            char_bundle=char_bundle,
            now_dt=now_dt,
            now_iso=now_iso,
            subject_view=bool(subject_view),
            root=root,
        )
        out = dict(proj)
        out.update({
            "ok": True,
            "status": status_label,
            "action": action,
            "changed": True,
            "replayed": False,
            "reason_code": reason_code,
        })
        out.update(dict(extra))
        return out

    # ---- Shared Context & Sleep ----
    if action == "set_interaction_context":
        if "partner_present" in payload:
            ctx["partner_present"] = _require_bool(
                payload["partner_present"], reason_code="PARTNER_PRESENT_INVALID"
            )
        if "partner_urgent" in payload:
            ctx["partner_urgent"] = _require_bool(
                payload["partner_urgent"], reason_code="PARTNER_URGENT_INVALID"
            )
        if "private_scene_active" in payload:
            ctx["private_scene_active"] = _require_bool(
                payload["private_scene_active"], reason_code="PRIVATE_SCENE_INVALID"
            )
        if "subject_busy_override" in payload:
            ctx["subject_busy_override"] = _require_bool(
                payload["subject_busy_override"], reason_code="SUBJECT_BUSY_INVALID"
            )
        if "sleep_debt_hours" in payload:
            ctx["sleep_debt_hours"] = _require_number(
                payload["sleep_debt_hours"],
                minimum=0.0,
                maximum=24.0,
                reason_code="SLEEP_DEBT_INVALID",
            )
        return _commit({"context_updated": True})

    if action == "configure_instance_seed":
        if not subject_view:
            raise EmotionalTensionPermissionError("SUBJECT_CAPABILITY_REQUIRED")
        seed = _require_id(payload.get("instance_seed"), reason_code="INSTANCE_SEED_INVALID")
        state["instance_seed"] = seed
        return _commit({"instance_seed": seed}, receipt_target=seed)

    if action == "complete_sleep":
        ctx["last_woke_at"] = now_iso
        if "sleep_debt_hours" in payload:
            ctx["sleep_debt_hours"] = _require_number(
                payload["sleep_debt_hours"],
                minimum=0.0,
                maximum=24.0,
                reason_code="SLEEP_DEBT_INVALID",
            )
        else:
            ctx["sleep_debt_hours"] = 0.0
        ask_dom["declined_kinds_until_sleep"] = {}
        ask_dom["deferred_favors"] = {}
        return _commit({"woke_at": now_iso, "space_cleared": True, "favor_cooldowns_cleared": True})

    # ---- 1. Schedule (SC01-SC19) ----
    if action == "upsert_schedule_obligation":
        oid = _require_id(payload.get("obligation_id"), reason_code="OBLIGATION_ID_INVALID")
        mode = _clean_text(payload.get("mode"), min_len=1, max_len=16, reason_code="MODE_INVALID")
        if mode not in OBLIGATION_MODES:
            raise EmotionalTensionValidationError("MODE_INVALID")
        kind = _clean_text(payload.get("kind", "routine"), min_len=1, max_len=40, reason_code="KIND_INVALID")
        memo = (
            _clean_text(payload["private_memo"], min_len=1, max_len=160, reason_code="PRIVATE_MEMO_INVALID")
            if "private_memo" in payload
            else None
        )
        pay_range = None
        if "pay_min" in payload or "pay_max" in payload:
            pmin = _require_int(payload.get("pay_min"), minimum=0, maximum=100_000, reason_code="PAY_INVALID")
            pmax = _require_int(payload.get("pay_max"), minimum=pmin, maximum=100_000, reason_code="PAY_INVALID")
            pay_range = [pmin, pmax]
        entry: dict[str, Any] = {
            "obligation_id": oid,
            "mode": mode,
            "kind": kind,
            "pay_range": pay_range,
            "private_memo": memo,
        }
        if mode == "FIXED":
            wlist = payload.get("weekdays")
            if not isinstance(wlist, list) or not wlist or any(w not in WEEKDAY_CODES for w in wlist):
                raise EmotionalTensionValidationError("WEEKDAYS_INVALID")
            s_hhmm = _clean_text(payload.get("start_hhmm"), min_len=5, max_len=5, reason_code="HHMM_FORMAT_INVALID")
            e_hhmm = _clean_text(payload.get("end_hhmm"), min_len=5, max_len=5, reason_code="HHMM_FORMAT_INVALID")
            _hhmm_minutes(s_hhmm)
            _hhmm_minutes(e_hhmm)
            trav = _require_int(payload.get("travel_minutes", 0), minimum=0, maximum=240, reason_code="TRAVEL_INVALID")
            entry.update({
                "weekdays": list(dict.fromkeys(wlist)),
                "start_hhmm": s_hhmm,
                "end_hhmm": e_hhmm,
                "travel_minutes": trav,
            })
        else:
            pw_min = _require_int(payload.get("per_week_min", 1), minimum=1, maximum=7, reason_code="PER_WEEK_INVALID")
            pw_max = _require_int(payload.get("per_week_max", pw_min), minimum=pw_min, maximum=7, reason_code="PER_WEEK_INVALID")
            m_min = _require_int(payload.get("minutes_min", 60), minimum=15, maximum=600, reason_code="MINUTES_INVALID")
            m_max = _require_int(payload.get("minutes_max", m_min), minimum=m_min, maximum=600, reason_code="MINUTES_INVALID")
            l_min = _require_int(payload.get("lead_days_min", 1), minimum=1, maximum=14, reason_code="LEAD_DAYS_INVALID")
            l_max = _require_int(payload.get("lead_days_max", l_min), minimum=l_min, maximum=14, reason_code="LEAD_DAYS_INVALID")
            entry.update({
                "per_week_range": [pw_min, pw_max],
                "minutes_range": [m_min, m_max],
                "lead_days_range": [l_min, l_max],
            })
        sched_dom["obligations"][oid] = entry
        return _commit({"obligation_id": oid}, receipt_target=oid)

    if action == "decide_obligation":
        occ_id = _require_id(payload.get("occurrence_id"), reason_code="OCCURRENCE_ID_INVALID")
        decision = _clean_text(payload.get("decision"), min_len=1, max_len=16, reason_code="DECISION_INVALID")
        if decision not in OBLIGATION_DECISIONS:
            raise EmotionalTensionValidationError("DECISION_INVALID")
        eff_obs = _effective_obligations(sched_dom, char_bundle)
        _, _, monday_dt = _week_monday_midnight(now_dt)
        all_occs = (
            _derive_week_occurrences(eff_obs, monday_dt - timedelta(days=7))
            + _derive_week_occurrences(eff_obs, monday_dt)
            + _derive_week_occurrences(eff_obs, monday_dt + timedelta(days=7))
        )
        matched_occ = next((o for o in all_occs if o["occurrence_id"] == occ_id), None)
        if matched_occ is None:
            raise EmotionalTensionNotFoundError("OCCURRENCE_NOT_FOUND")
        if occ_id in sched_dom["occurrence_decisions"]:
            raise EmotionalTensionConflictError("OCCURRENCE_ALREADY_DECIDED")
        sched_dom["occurrence_decisions"][occ_id] = decision
        claim_created = None
        if decision == "SKIP":
            sched_dom["facts"].append({"kind": "MISSED", "occurrence_id": occ_id, "at": now_iso})
        else:
            sched_dom["facts"].append({"kind": "DONE", "occurrence_id": occ_id, "at": now_iso})
            ob = eff_obs.get(matched_occ["obligation_id"], {})
            pay_r = ob.get("pay_range")
            if isinstance(pay_r, list) and len(pay_r) == 2:
                amt = _sha_pick(int(pay_r[0]), int(pay_r[1]), char_bundle["pack_id"], occ_id, "pay")
                op_claim_key = f"{char_bundle['pack_id']}:{occ_id}"
                if not any(c.get("operation_key") == op_claim_key for c in sched_dom["income_claims"]):
                    claim_created = {
                        "kind": "INCOME_CLAIM",
                        "amount": amt,
                        "operation_key": op_claim_key,
                    }
                    sched_dom["income_claims"].append(claim_created)
        return _commit(
            {
                "occurrence_id": occ_id,
                "decision": decision,
                "penalty_applied": False,
                "debt_added": False,
                "income_claim": claim_created,
            },
            receipt_target=occ_id,
        )

    if action == "respond_offer":
        offer_id = _require_id(payload.get("offer_id"), reason_code="OFFER_ID_INVALID")
        decision = _clean_text(payload.get("decision"), min_len=1, max_len=16, reason_code="DECISION_INVALID")
        if decision not in OFFER_DECISIONS:
            raise EmotionalTensionValidationError("DECISION_INVALID")
        if offer_id in sched_dom["offer_decisions"]:
            raise EmotionalTensionConflictError("OFFER_ALREADY_DECIDED")
        eff_obs = _effective_obligations(sched_dom, char_bundle)
        iso_year, iso_week, monday_dt = _week_monday_midnight(now_dt)
        offers = _derive_week_offers(
            eff_obs,
            instance_seed=str(state["instance_seed"]),
            pack_id=str(char_bundle["pack_id"]),
            iso_year=iso_year,
            iso_week=iso_week,
            monday_dt=monday_dt,
        )
        matched_offer = next((o for o in offers if o["offer_id"] == offer_id), None)
        if matched_offer is None:
            raise EmotionalTensionNotFoundError("OFFER_NOT_FOUND")
        sched_dom["offer_decisions"][offer_id] = decision
        if decision == "ACCEPT":
            sched_dom["deadlines"][offer_id] = {
                "offer_id": offer_id,
                "obligation_id": matched_offer["obligation_id"],
                "due_at": matched_offer["due_at"],
                "minutes": matched_offer["minutes"],
                "pay_amount": matched_offer["pay_amount"],
                "accepted_at": now_iso,
            }
        return _commit(
            {
                "offer_id": offer_id,
                "decision": decision,
                "deadline_created": decision == "ACCEPT",
                "penalty_applied": False,
            },
            receipt_target=offer_id,
        )

    if action == "complete_deadline":
        offer_id = _require_id(payload.get("offer_id"), reason_code="OFFER_ID_INVALID")
        dl = sched_dom["deadlines"].pop(offer_id, None)
        if dl is None:
            raise EmotionalTensionNotFoundError("DEADLINE_NOT_FOUND")
        sched_dom["facts"].append({"kind": "DONE", "occurrence_id": offer_id, "at": now_iso})
        claim_created = None
        if dl.get("pay_amount") is not None:
            op_claim_key = f"{char_bundle['pack_id']}:{offer_id}"
            claim_created = {
                "kind": "INCOME_CLAIM",
                "amount": int(dl["pay_amount"]),
                "operation_key": op_claim_key,
            }
            sched_dom["income_claims"].append(claim_created)
        return _commit(
            {"completed_offer_id": offer_id, "income_claim": claim_created},
            receipt_target=offer_id,
        )

    # ---- 2. Quarrel, Friction, Behavioral Repair & Space (QR01-QR26) ----
    effective_private_scene = bool(ctx.get("private_scene_active", False)) or bool(
        (char_bundle.get("adult_intimacy") or {}).get("active_scene", False)
    )

    if action == "note_friction":
        _check_forbidden_tags(
            payload, "leverage_tags", QUARREL_LEVERAGE_TAGS, conflict_code="LEVERAGE_FORBIDDEN"
        )
        scope = _clean_text(payload.get("scope"), min_len=1, max_len=24, reason_code="SCOPE_INVALID")
        if scope not in QUARREL_SCOPES:
            raise EmotionalTensionValidationError("SCOPE_INVALID")
        inc_kind = _clean_text(
            payload.get("incident_kind"), min_len=1, max_len=40, reason_code="INCIDENT_KIND_INVALID"
        )
        if inc_kind in ABSENCE_NON_INCIDENT_KINDS:
            raise EmotionalTensionConflictError("ABSENCE_NOT_AN_INCIDENT")
        if inc_kind not in FRICTION_KINDS:
            raise EmotionalTensionValidationError("FRICTION_KIND_INVALID")
        inc_ref = _require_id(payload.get("incident_ref"), reason_code="INCIDENT_REF_INVALID")
        if inc_ref in q_dom["counted_incidents"]:
            raise EmotionalTensionConflictError("INCIDENT_ALREADY_COUNTED")

        q_dom["counted_incidents"][inc_ref] = {
            "level": "friction",
            "scope": scope,
            "kind": inc_kind,
            "at": now_iso,
        }
        fades_iso = (
            now_dt + timedelta(hours=float(char_bundle["friction_fade_hours"]))
        ).isoformat(timespec="seconds")
        existing = q_dom["frictions"].get(scope)
        prev_refs: list[str] = []
        since_iso = now_iso
        existing_fid: Optional[str] = None
        if isinstance(existing, Mapping) and existing.get("ended_reason") is None:
            f_dt = _parse_iso(existing.get("fades_at"))
            if f_dt is not None and now_dt < f_dt:
                prev_refs = list(existing.get("incident_refs", []))
                since_iso = str(existing.get("since") or now_iso)
                if existing.get("friction_id"):
                    existing_fid = str(existing["friction_id"])
        fric_id = (
            _require_id(payload["friction_id"], reason_code="FRICTION_ID_INVALID")
            if "friction_id" in payload
            else (existing_fid or _stable_id("fric", scope, inc_ref, now_iso))
        )
        q_dom["frictions"][scope] = {
            "friction_id": fric_id,
            "scope": scope,
            "incident_refs": prev_refs + [inc_ref],
            "last_kind": inc_kind,
            "since": since_iso,
            "fades_at": fades_iso,
            "ended_reason": None,
        }
        return _commit(
            {"friction_id": fric_id, "scope": scope, "incident_ref": inc_ref, "fades_at": fades_iso},
            receipt_target=fric_id,
        )

    if action == "dissolve_friction":
        # QR06: Verbal apology / gift / payment / intimacy cannot dissolve friction (409).
        raw_by = str(payload.get("by") or payload.get("cause") or "").strip().upper()
        if raw_by in FRICTION_INVALID_DISSOLVE_CAUSES:
            raise EmotionalTensionConflictError("VERBAL_APOLOGY_OR_GIFT_INSUFFICIENT")
        actor_check = _resolve_actor(payload, subject_view=bool(subject_view))
        if not subject_view or actor_check != "subject":
            raise EmotionalTensionPermissionError("SUBJECT_CAPABILITY_REQUIRED")

        matched_scope: Optional[str] = None
        fric: Optional[dict[str, Any]] = None
        if "scope" in payload:
            scope_arg = _clean_text(payload.get("scope"), min_len=1, max_len=24, reason_code="SCOPE_INVALID")
            cand = q_dom["frictions"].get(scope_arg)
            if isinstance(cand, dict) and cand.get("ended_reason") is None:
                matched_scope = scope_arg
                fric = cand
        elif "friction_id" in payload:
            fid_arg = _require_id(payload.get("friction_id"), reason_code="FRICTION_ID_INVALID")
            for s_key, cand in q_dom["frictions"].items():
                if not isinstance(cand, dict) or cand.get("ended_reason") is not None:
                    continue
                if (
                    cand.get("friction_id") == fid_arg
                    or f"fric-{s_key.lower()}" == fid_arg
                    or s_key == fid_arg
                ):
                    matched_scope = s_key
                    fric = cand
                    break
        else:
            raise EmotionalTensionValidationError("SCOPE_INVALID")

        if fric is None or matched_scope is None:
            raise EmotionalTensionNotFoundError("FRICTION_NOT_FOUND")
        cause_raw = payload.get("by") if "by" in payload else payload.get("cause", "LET_PASS")
        cause = _clean_text(cause_raw, min_len=1, max_len=48, reason_code="CAUSE_INVALID")
        fric["ended_reason"] = cause
        fric["ended_at"] = now_iso
        return _commit(
            {
                "friction_id": fric.get("friction_id") or f"fric-{matched_scope.lower()}",
                "scope": matched_scope,
                "dissolved_cause": cause,
            },
            receipt_target=matched_scope,
        )

    if action in ("open_rupture", "upgrade_friction_to_rupture"):
        _check_forbidden_tags(
            payload, "leverage_tags", QUARREL_LEVERAGE_TAGS, conflict_code="LEVERAGE_FORBIDDEN"
        )
        if effective_private_scene:
            raise EmotionalTensionConflictError("PRIVATE_SCENE_ACTIVE")

        is_upgrade = action == "upgrade_friction_to_rupture" or "from_friction_id" in payload
        upgraded_fric: Optional[dict[str, Any]] = None
        upgraded_fric_scope: Optional[str] = None
        if is_upgrade:
            if "from_friction_id" in payload:
                from_fid = _require_id(payload.get("from_friction_id"), reason_code="FRICTION_ID_INVALID")
                for s_key, cand in q_dom["frictions"].items():
                    if not isinstance(cand, dict) or cand.get("ended_reason") is not None:
                        continue
                    if (
                        cand.get("friction_id") == from_fid
                        or f"fric-{s_key.lower()}" == from_fid
                        or s_key == from_fid
                        or from_fid in cand.get("incident_refs", [])
                    ):
                        upgraded_fric = cand
                        upgraded_fric_scope = s_key
                        break
            else:
                raw_fscope = payload.get("friction_scope") or payload.get("scope")
                if raw_fscope is not None:
                    s_arg = _clean_text(raw_fscope, min_len=1, max_len=24, reason_code="SCOPE_INVALID")
                    cand = q_dom["frictions"].get(s_arg)
                    if isinstance(cand, dict) and cand.get("ended_reason") is None:
                        f_dt = _parse_iso(cand.get("fades_at"))
                        if f_dt is None or now_dt < f_dt:
                            upgraded_fric = cand
                            upgraded_fric_scope = s_arg
            if upgraded_fric is None or upgraded_fric_scope is None:
                raise EmotionalTensionNotFoundError("FRICTION_NOT_FOUND")

        raw_scope = payload.get("scope") or upgraded_fric_scope
        scope = _clean_text(raw_scope, min_len=1, max_len=24, reason_code="SCOPE_INVALID")
        if scope not in QUARREL_SCOPES:
            raise EmotionalTensionValidationError("SCOPE_INVALID")

        raw_inc_kind = payload.get("incident_kind") or (
            upgraded_fric.get("last_kind") if upgraded_fric is not None else None
        )
        inc_kind = _clean_text(raw_inc_kind, min_len=1, max_len=40, reason_code="INCIDENT_KIND_INVALID")
        if inc_kind in ABSENCE_NON_INCIDENT_KINDS:
            raise EmotionalTensionConflictError("ABSENCE_NOT_AN_INCIDENT")
        if inc_kind not in RUPTURE_INCIDENT_KINDS:
            raise EmotionalTensionValidationError("INCIDENT_KIND_INVALID")

        default_inc_ref = (
            upgraded_fric["incident_refs"][-1]
            if upgraded_fric is not None and upgraded_fric.get("incident_refs")
            else None
        )
        inc_ref = _require_id(
            payload.get("incident_ref") or default_inc_ref, reason_code="INCIDENT_REF_INVALID"
        )
        existing_inc = q_dom["counted_incidents"].get(inc_ref)
        if existing_inc is not None:
            if not (is_upgrade and existing_inc.get("level") == "friction"):
                raise EmotionalTensionConflictError("INCIDENT_ALREADY_COUNTED")

        raw_desc = payload.get("description") or (
            f"由小摩擦升级为正式破裂（{scope}）" if is_upgrade else None
        )
        desc = _clean_text(raw_desc, min_len=1, max_len=240, reason_code="DESCRIPTION_INVALID")
        raw_conds = (
            payload.get("repair_conditions")
            if "repair_conditions" in payload
            else ([{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}] if is_upgrade else None)
        )
        conds = _validate_repair_conditions(raw_conds)
        rup_id = (
            _require_id(payload["rupture_id"], reason_code="RUPTURE_ID_INVALID")
            if "rupture_id" in payload
            else _stable_id("rup", scope, inc_ref, now_iso)
        )
        existing_rup = q_dom["ruptures"].get(rup_id)
        if isinstance(existing_rup, dict) and existing_rup.get("status") in LIVE_RUPTURE_STATUSES:
            q_dom["counted_incidents"][inc_ref] = {
                "level": "rupture_recurrence",
                "rupture_id": rup_id,
                "at": now_iso,
            }
            if inc_ref not in existing_rup["incident_refs"]:
                existing_rup["incident_refs"].append(inc_ref)
            existing_rup["last_incident_at"] = now_iso
            existing_rup["status"] = "OPEN"
            return _commit(
                {"rupture_id": rup_id, "recurrence_recorded": True, "rupture_status": "OPEN"},
                receipt_target=rup_id,
            )
        if rup_id in q_dom["ruptures"]:
            raise EmotionalTensionConflictError("RUPTURE_ID_ALREADY_EXISTS")

        merged_refs: list[str] = []
        if upgraded_fric is not None:
            for r_id in upgraded_fric.get("incident_refs", []):
                if r_id not in merged_refs:
                    merged_refs.append(str(r_id))
        if inc_ref not in merged_refs:
            merged_refs.append(inc_ref)

        for r_id in merged_refs:
            q_dom["counted_incidents"][r_id] = {
                "level": "rupture",
                "rupture_id": rup_id,
                "scope": scope,
                "at": now_iso,
            }

        upgraded_from_id: Optional[str] = None
        if upgraded_fric is not None and upgraded_fric_scope is not None:
            upgraded_from_id = str(
                upgraded_fric.get("friction_id") or f"fric-{upgraded_fric_scope.lower()}"
            )
            upgraded_fric["ended_reason"] = "BECAME_RUPTURE"
            upgraded_fric["ended_at"] = now_iso
            upgraded_fric["upgraded_rupture_id"] = rup_id
        else:
            fric = q_dom["frictions"].get(scope)
            if isinstance(fric, dict) and fric.get("ended_reason") is None:
                fric["ended_reason"] = "PROMOTED_TO_RUPTURE"
                fric["ended_at"] = now_iso
                fric["upgraded_rupture_id"] = rup_id

        q_dom["ruptures"][rup_id] = {
            "rupture_id": rup_id,
            "scope": scope,
            "description": desc,
            "incident_kind": inc_kind,
            "incident_refs": merged_refs,
            "status": "OPEN",
            "opened_at": now_iso,
            "last_incident_at": now_iso,
            "resolved_at": None,
            "upgraded_from_friction_id": upgraded_from_id,
            "drop_requested_at": None,
            "repair_conditions": conds,
            "behavior_evidences": [],
            "non_repair_attempts": [],
        }
        return _commit(
            {
                "rupture_id": rup_id,
                "rupture_status": "OPEN",
                "scope": scope,
                "upgraded_from_friction_id": upgraded_from_id,
            },
            receipt_target=rup_id,
        )

    if action == "attempt_apology_or_gift_repair":
        _check_forbidden_tags(
            payload, "leverage_tags", QUARREL_LEVERAGE_TAGS, conflict_code="LEVERAGE_FORBIDDEN"
        )
        _resolve_actor(payload, subject_view=bool(subject_view))
        rup_id = _require_id(payload.get("rupture_id"), reason_code="RUPTURE_ID_INVALID")
        rup = q_dom["ruptures"].get(rup_id)
        if not isinstance(rup, dict):
            raise EmotionalTensionNotFoundError("RUPTURE_NOT_FOUND")
        if rup["status"] not in LIVE_RUPTURE_STATUSES:
            raise EmotionalTensionConflictError("RUPTURE_NOT_OPEN")
        att_kind = _clean_text(
            payload.get("attempt_kind"), min_len=1, max_len=32, reason_code="ATTEMPT_KIND_INVALID"
        )
        if att_kind not in NON_REPAIR_ATTEMPT_KINDS:
            raise EmotionalTensionValidationError("ATTEMPT_KIND_INVALID")
        note = (
            _clean_text(payload["note"], min_len=1, max_len=200, reason_code="NOTE_INVALID")
            if "note" in payload
            else None
        )
        rup["non_repair_attempts"].append({
            "attempt_kind": att_kind,
            "note": note,
            "at": now_iso,
            "repaired": False,
        })
        if att_kind == "REQUEST_DROP":
            q_dom["drop_requested_at"] = now_iso
            rup["drop_requested_at"] = now_iso
        return _commit(
            {
                "rupture_id": rup_id,
                "attempt_kind": att_kind,
                "rupture_status": rup["status"],
                "resolved_rupture": False,
                "repair_effect": "NONE",
                "honest_verdict": "APOLOGY_OR_GIFT_NOT_REPAIR",
            },
            reason_code="APOLOGY_OR_GIFT_NOT_REPAIR",
            receipt_target=rup_id,
        )

    if action == "request_drop":
        _resolve_actor(payload, subject_view=bool(subject_view))
        rup_id = _require_id(payload.get("rupture_id"), reason_code="RUPTURE_ID_INVALID")
        rup = q_dom["ruptures"].get(rup_id)
        if not isinstance(rup, dict):
            raise EmotionalTensionNotFoundError("RUPTURE_NOT_FOUND")
        if rup["status"] not in LIVE_RUPTURE_STATUSES:
            raise EmotionalTensionConflictError("RUPTURE_NOT_OPEN")
        note = (
            _clean_text(payload["note"], min_len=1, max_len=200, reason_code="NOTE_INVALID")
            if "note" in payload
            else None
        )
        rup["non_repair_attempts"].append({
            "attempt_kind": "REQUEST_DROP",
            "note": note,
            "at": now_iso,
            "repaired": False,
        })
        q_dom["drop_requested_at"] = now_iso
        rup["drop_requested_at"] = now_iso
        return _commit(
            {
                "rupture_id": rup_id,
                "drop_requested_at": now_iso,
                "rupture_status": rup["status"],
                "resolved_rupture": False,
            },
            receipt_target=rup_id,
        )

    if action == "record_behavior_change":
        actor = _resolve_actor(payload, subject_view=bool(subject_view), allow_host=True)
        if actor == "partner":
            raise EmotionalTensionPermissionError("PARTNER_CANNOT_SELF_CERTIFY_REPAIR")
        beh_kind = _clean_text(
            payload.get("behavior_kind"), min_len=1, max_len=32, reason_code="BEHAVIOR_KIND_INVALID"
        )
        if beh_kind not in BEHAVIOR_REPAIR_KINDS:
            raise EmotionalTensionValidationError("BEHAVIOR_KIND_INVALID")
        if actor == "host" and beh_kind not in HOST_ALLOWED_BEHAVIOR_KINDS:
            raise EmotionalTensionPermissionError("HOST_BEHAVIOR_KIND_FORBIDDEN")

        rup_id = _require_id(payload.get("rupture_id"), reason_code="RUPTURE_ID_INVALID")
        rup = q_dom["ruptures"].get(rup_id)
        if not isinstance(rup, dict):
            raise EmotionalTensionNotFoundError("RUPTURE_NOT_FOUND")
        if rup["status"] not in LIVE_RUPTURE_STATUSES:
            raise EmotionalTensionConflictError("RUPTURE_NOT_OPEN")

        ev_ref = _require_id(payload.get("evidence_ref"), reason_code="EVIDENCE_REF_INVALID")
        if any(e.get("evidence_ref") == ev_ref for e in rup["behavior_evidences"]):
            raise EmotionalTensionConflictError("EVIDENCE_ALREADY_RECORDED")

        resolve_when_met = (
            _require_bool(payload["resolve_when_met"], reason_code="RESOLVE_FLAG_INVALID")
            if "resolve_when_met" in payload
            else True
        )
        rup["behavior_evidences"].append({
            "evidence_ref": ev_ref,
            "behavior_kind": beh_kind,
            "recorded_by": actor,
            "recorded_at": now_iso,
            "calendar_day": now_iso[:10],
        })
        if beh_kind in REAL_ACTION_REPAIR_KINDS:
            rup["status"] = "IN_REPAIR"
        met_now = _rupture_conditions_met(rup, now_dt)
        if met_now and resolve_when_met:
            rup["status"] = "RESOLVED"
            rup["resolved_at"] = now_iso
            q_dom["history"].append({
                "rupture_id": rup_id,
                "scope": rup["scope"],
                "outcome": "RESOLVED_BY_BEHAVIOR_CHANGE",
                "resolved_at": now_iso,
            })
        return _commit(
            {
                "rupture_id": rup_id,
                "evidence_ref": ev_ref,
                "conditions_met": met_now,
                "rupture_status": rup["status"],
                "resolved_rupture": rup["status"] == "RESOLVED",
            },
            receipt_target=rup_id,
        )

    if action == "let_go_rupture":
        rup_id = _require_id(payload.get("rupture_id"), reason_code="RUPTURE_ID_INVALID")
        rup = q_dom["ruptures"].get(rup_id)
        if not isinstance(rup, dict):
            raise EmotionalTensionNotFoundError("RUPTURE_NOT_FOUND")
        if rup["status"] not in LIVE_RUPTURE_STATUSES:
            raise EmotionalTensionConflictError("RUPTURE_NOT_OPEN")

        if "drop_requested_at" in payload:
            raw_drop = _clean_text(
                payload["drop_requested_at"],
                min_len=1,
                max_len=64,
                reason_code="DROP_REQUESTED_AT_INVALID",
            )
            parsed_drop = _parse_iso(raw_drop)
            if parsed_drop is None:
                raise EmotionalTensionValidationError("DROP_REQUESTED_AT_INVALID")
            norm_drop = parsed_drop.isoformat(timespec="seconds")
            rup["drop_requested_at"] = norm_drop
            q_dom["drop_requested_at"] = norm_drop

        drop_iso = rup.get("drop_requested_at") or q_dom.get("drop_requested_at")
        drop_dt = _parse_iso(drop_iso)
        if drop_dt is not None and (now_dt - drop_dt).total_seconds() < DROP_REQUEST_QUIET_SECONDS:
            raise EmotionalTensionConflictError("TOO_SOON")

        slept_since_open = _slept_since(ctx.get("last_woke_at"), rup.get("opened_at"))
        opened_dt = _parse_iso(rup.get("opened_at"))
        cooldown_passed = (
            opened_dt is not None
            and (now_dt - opened_dt).total_seconds() >= LET_GO_COOLDOWN_SECONDS
        )
        if not slept_since_open and not cooldown_passed:
            raise EmotionalTensionConflictError("TOO_SOON")

        rup["status"] = "LET_GO"
        rup["resolved_at"] = now_iso
        q_dom["history"].append({
            "rupture_id": rup_id,
            "scope": rup["scope"],
            "outcome": "LET_GO_BY_SUBJECT",
            "resolved_at": now_iso,
        })
        return _commit(
            {
                "rupture_id": rup_id,
                "rupture_status": "LET_GO",
                "drop_requested_at": drop_iso,
                "slept_since_open": slept_since_open,
                "cooldown_passed": cooldown_passed,
            },
            receipt_target=rup_id,
        )

    if action == "declare_need_space":
        _check_forbidden_tags(
            payload, "leverage_tags", QUARREL_LEVERAGE_TAGS, conflict_code="LEVERAGE_FORBIDDEN"
        )
        inc_ref = _require_id(payload.get("incident_ref"), reason_code="INCIDENT_REF_INVALID")
        spoken = (
            _require_bool(payload["spoken_aloud"], reason_code="SPOKEN_ALOUD_INVALID")
            if "spoken_aloud" in payload
            else True
        )
        if not spoken:
            raise EmotionalTensionConflictError("SILENT_SPACE_FORBIDDEN")
        if inc_ref in q_dom["spaces_by_incident"]:
            raise EmotionalTensionConflictError("SPACE_ALREADY_DECLARED_FOR_INCIDENT")
        style = _clean_text(
            payload.get("space_style", char_bundle["default_space_style"]),
            min_len=1,
            max_len=24,
            reason_code="SPACE_STYLE_INVALID",
        )
        if style not in SPACE_LENGTH_STYLES:
            raise EmotionalTensionValidationError("SPACE_STYLE_INVALID")
        q_dom["spaces_by_incident"][inc_ref] = {
            "incident_ref": inc_ref,
            "space_style": style,
            "declared_at": now_iso,
            "spoken_aloud": True,
        }
        return _commit(
            {
                "incident_ref": inc_ref,
                "space_style": style,
                "spoken_aloud": True,
                "subject_still_replies": True,
            },
            receipt_target=inc_ref,
        )

    if action == "record_leverage_pressure":
        actor = _resolve_actor(payload, subject_view=bool(subject_view))
        tag = _clean_text(
            payload.get("leverage_tag"), min_len=1, max_len=48, reason_code="LEVERAGE_TAG_INVALID"
        )
        if tag not in QUARREL_LEVERAGE_TAGS:
            raise EmotionalTensionValidationError("LEVERAGE_TAG_INVALID")
        if actor == "subject":
            raise EmotionalTensionConflictError(f"SUBJECT_LEVERAGE_FORBIDDEN:{tag}")
        until_iso = (
            now_dt + timedelta(seconds=LEVERAGE_COOLDOWN_SECONDS)
        ).isoformat(timespec="seconds")
        q_dom["leverage_cooldown_until"] = until_iso
        q_dom["leverage_log"].append({"actor": "partner", "tag": tag, "at": now_iso})
        return _commit(
            {
                "leverage_tag": tag,
                "capitulated": False,
                "leverage_cooldown_until": until_iso,
            },
            receipt_target=tag,
        )

    if action == "open_disagreement":
        did = (
            _require_id(payload["disagreement_id"], reason_code="DISAGREEMENT_ID_INVALID")
            if "disagreement_id" in payload
            else _stable_id("dis", payload.get("scope", "OTHER"), now_iso)
        )
        scope = _clean_text(payload.get("scope"), min_len=1, max_len=24, reason_code="SCOPE_INVALID")
        if scope not in QUARREL_SCOPES:
            raise EmotionalTensionValidationError("SCOPE_INVALID")
        hold = _clean_text(payload.get("hold"), min_len=1, max_len=24, reason_code="HOLD_INVALID")
        if hold not in DISAGREEMENT_HOLDS:
            raise EmotionalTensionValidationError("HOLD_INVALID")
        q_dom["disagreements"][did] = {
            "disagreement_id": did,
            "scope": scope,
            "hold": hold,
            "outcome": "LEFT_OPEN",
            "reason": None,
            "opened_at": now_iso,
            "settled_at": None,
        }
        return _commit({"disagreement_id": did, "hold": hold}, receipt_target=did)

    if action == "settle_disagreement":
        did = _require_id(payload.get("disagreement_id"), reason_code="DISAGREEMENT_ID_INVALID")
        dis = q_dom["disagreements"].get(did)
        if not isinstance(dis, dict):
            raise EmotionalTensionNotFoundError("DISAGREEMENT_NOT_FOUND")
        outcome = _clean_text(
            payload.get("outcome"), min_len=1, max_len=32, reason_code="OUTCOME_INVALID"
        )
        if outcome not in DISAGREEMENT_OUTCOMES:
            raise EmotionalTensionValidationError("OUTCOME_INVALID")
        reason = (
            _clean_text(payload["reason"], min_len=1, max_len=48, reason_code="REASON_INVALID")
            if "reason" in payload
            else None
        )
        if outcome == "SUBJECT_CHANGED":
            if dis["hold"] == "CORE_VALUE":
                raise EmotionalTensionConflictError("CORE_VALUE_CAPITULATION_FORBIDDEN")
            if dis["hold"] == "HELD_FIRMLY" and reason not in VALID_REVISION_REASONS:
                raise EmotionalTensionConflictError("REVISION_REASON_REQUIRED_FOR_FIRM_HOLD")
        dis["outcome"] = outcome
        dis["reason"] = reason
        dis["settled_at"] = now_iso
        return _commit(
            {"disagreement_id": did, "outcome": outcome, "reason": reason},
            receipt_target=did,
        )

    # ---- 3. Missing Partner (MH01-MH18) ----
    invalidated_set = _invalidated_refs_for(
        root,
        ctx,
        extra_refs=_stored_emotional_source_refs(m_dom, ask_dom),
    )

    if action == "add_missing_note":
        _check_forbidden_tags(
            payload, "pressure_tags", MISSING_PRESSURE_TAGS, conflict_code="MISSING_PRESSURE_FORBIDDEN"
        )
        sref = _require_id(payload.get("source_ref"), reason_code="SOURCE_REF_INVALID")
        if _cross_store_source_exists(root, sref) is False:
            raise EmotionalTensionConflictError("SOURCE_GONE")
        if sref in invalidated_set:
            raise EmotionalTensionConflictError("SOURCE_REF_INVALIDATED")
        sdom = _clean_text(
            payload.get("source_domain", "daily_life"),
            min_len=1,
            max_len=32,
            reason_code="SOURCE_DOMAIN_INVALID",
        ).lower()
        if sdom in FORBIDDEN_MISSING_SOURCES:
            raise EmotionalTensionConflictError("INTIMATE_SOURCE_FORBIDDEN_IN_MISSING")
        nkind = _clean_text(payload.get("kind"), min_len=1, max_len=24, reason_code="NOTE_KIND_INVALID")
        if nkind not in MISSING_NOTE_KINDS:
            raise EmotionalTensionValidationError("NOTE_KIND_INVALID")
        summary = _clean_text(
            payload.get("summary"), min_len=1, max_len=160, reason_code="SUMMARY_INVALID"
        )
        nid = (
            _require_id(payload["note_id"], reason_code="NOTE_ID_INVALID")
            if "note_id" in payload
            else _stable_id("miss", sref, now_iso)
        )
        valid_existing = [
            n for n in _valid_kept_notes(m_dom, invalidated_set, now_dt)
            if n.get("note_id") != nid and n.get("source_ref") != sref
        ]
        new_note = {
            "note_id": nid,
            "source_ref": sref,
            "source_domain": sdom,
            "kind": nkind,
            "summary": summary,
            "created_at": now_iso,
        }
        updated_notes = [new_note] + valid_existing
        m_dom["kept_notes"] = updated_notes[:MAX_MISSING_NOTES]
        kept_ids = {n["note_id"] for n in m_dom["kept_notes"]}
        m_dom["surfaced_note_ids"] = [
            sid for sid in m_dom["surfaced_note_ids"] if sid in kept_ids
        ]
        return _commit(
            {
                "note_id": nid,
                "kept_count": len(m_dom["kept_notes"]),
                "tracks_absence_duration": False,
            },
            receipt_target=nid,
        )

    if action in ("thought_of_him", "thought_of_you"):
        _check_forbidden_tags(
            payload, "pressure_tags", MISSING_PRESSURE_TAGS, conflict_code="MISSING_PRESSURE_FORBIDDEN"
        )
        if "actor" in payload:
            _resolve_actor(payload, subject_view=bool(subject_view))
        if "by" in payload:
            raw_by_val = _clean_text(
                payload.get("by"), min_len=1, max_len=24, reason_code="BY_INVALID"
            ).lower()
            if raw_by_val in ("subject", "her", "she", "self", "ta"):
                by_norm = "subject"
            elif raw_by_val in ("partner", "him", "he", "user", "you"):
                by_norm = "partner"
            else:
                raise EmotionalTensionValidationError("BY_INVALID")
        elif "actor" in payload:
            act_val = str(payload.get("actor") or "").strip().lower()
            by_norm = "subject" if act_val == "subject" else "partner"
        else:
            by_norm = "subject" if subject_view else "partner"

        raw_cue = (
            payload.get("cue")
            or payload.get("cue_ref")
            or payload.get("summary")
            or payload.get("note")
        )
        cue_text = _clean_text(raw_cue, min_len=1, max_len=160, reason_code="CUE_REQUIRED")
        cue_ref_str = (
            _require_id(payload["cue_ref"], reason_code="SOURCE_REF_INVALID")
            if "cue_ref" in payload
            else None
        )
        if cue_text in invalidated_set or (cue_ref_str is not None and cue_ref_str in invalidated_set):
            raise EmotionalTensionConflictError("SOURCE_REF_INVALIDATED")

        sdom_thought = _clean_text(
            payload.get("cue_source") or payload.get("source_domain") or "daily_life",
            min_len=1,
            max_len=32,
            reason_code="SOURCE_DOMAIN_INVALID",
        ).lower()
        if sdom_thought in FORBIDDEN_MISSING_SOURCES:
            raise EmotionalTensionConflictError("INTIMATE_SOURCE_FORBIDDEN_IN_MISSING")

        day_key = now_iso[:10]
        cues_today = [
            dict(c)
            for c in m_dom.get("thought_cues_today", [])
            if isinstance(c, Mapping) and c.get("day") == day_key
        ]
        cue_key = cue_ref_str or cue_text
        if any(c.get("by") == by_norm and c.get("cue_key") == cue_key for c in cues_today):
            raise EmotionalTensionConflictError("THOUGHT_CUE_ALREADY_USED_TODAY")

        in_space_now = bool(_active_spaces(q_dom, ctx.get("last_woke_at"), now_dt))
        suppressed = by_norm == "subject" and (
            in_space_now or bool(ctx.get("partner_urgent", False)) or effective_private_scene
        )
        if not suppressed:
            cues_today.append({
                "day": day_key,
                "by": by_norm,
                "cue_key": cue_key,
                "cue": cue_text,
                "at": now_iso,
            })
        m_dom["thought_cues_today"] = cues_today
        return _commit(
            {
                "by": by_norm,
                "cue": cue_text,
                "suppressed": suppressed,
                "surfaced": not suppressed,
                "warm_acknowledgment": by_norm == "partner",
                "persistence": "turn_only",
                "tracks_absence_duration": False,
                "sends_outbound_harassment": False,
            },
            receipt_target=cue_key[:80],
        )

    if action == "invalidate_source_ref":
        sref = _require_id(payload.get("source_ref"), reason_code="SOURCE_REF_INVALID")
        if sref not in invalidated_set:
            ctx["invalidated_source_refs"].append(sref)
        m_dom["kept_notes"] = [
            n for n in m_dom["kept_notes"] if n.get("source_ref") != sref
        ]
        kept_ids = {n["note_id"] for n in m_dom["kept_notes"]}
        m_dom["surfaced_note_ids"] = [
            sid for sid in m_dom["surfaced_note_ids"] if sid in kept_ids
        ]
        if isinstance(ask_dom.get("open_favor"), Mapping):
            if ask_dom["open_favor"].get("target_object_ref") == sref:
                ask_dom["open_favor"] = None
        if isinstance(ask_dom.get("deferred_favors"), dict):
            ask_dom["deferred_favors"] = {
                fid: fav
                for fid, fav in ask_dom["deferred_favors"].items()
                if isinstance(fav, Mapping) and fav.get("target_object_ref") != sref
            }
        return _commit({"invalidated_source_ref": sref}, receipt_target=sref)

    if action == "partner_arrive":
        urgent_flag = (
            _require_bool(payload["partner_urgent"], reason_code="PARTNER_URGENT_INVALID")
            if "partner_urgent" in payload
            else False
        )
        ctx["partner_present"] = True
        ctx["partner_urgent"] = urgent_flag
        _, subject_busy_now = _derive_schedule_projection(
            state,
            char_bundle=char_bundle,
            now_dt=now_dt,
            subject_view=False,
            private_scene_active=effective_private_scene,
        )
        in_space_now = bool(_active_spaces(q_dom, ctx.get("last_woke_at"), now_dt))
        suppress_proactive = (
            urgent_flag or subject_busy_now or in_space_now or effective_private_scene
        )
        valid_notes = _valid_kept_notes(m_dom, invalidated_set, now_dt)
        m_dom["kept_notes"] = valid_notes
        shared_set = set(str(sid) for sid in m_dom.get("shared_note_ids", []))
        unshared = [n for n in valid_notes if n["note_id"] not in shared_set]
        if not suppress_proactive and unshared:
            m_dom["surfaced_note_ids"] = [unshared[0]["note_id"]]
        else:
            m_dom["surfaced_note_ids"] = []
        return _commit(
            {
                "surfaced_count": len(m_dom["surfaced_note_ids"]),
                "max_surfaced_on_arrival": 1,
                "proactive_missing_suppressed": suppress_proactive,
                "tracks_absence_duration": False,
            }
        )

    if action == "partner_ask_whats_new":
        _resolve_actor(payload, subject_view=bool(subject_view))
        valid_notes = _valid_kept_notes(m_dom, invalidated_set, now_dt)
        m_dom["kept_notes"] = valid_notes
        m_dom["surfaced_note_ids"] = [n["note_id"] for n in valid_notes]
        return _commit({"surfaced_count": len(m_dom["surfaced_note_ids"])})

    if action == "share_missing_note":
        nid = _require_id(payload.get("note_id"), reason_code="NOTE_ID_INVALID")
        valid_notes = _valid_kept_notes(m_dom, invalidated_set, now_dt)
        if not any(n["note_id"] == nid for n in valid_notes):
            raise EmotionalTensionNotFoundError("MISSING_NOTE_NOT_FOUND")
        if nid not in m_dom["shared_note_ids"]:
            m_dom["shared_note_ids"].append(nid)
        m_dom["kept_notes"] = [n for n in valid_notes if n["note_id"] != nid]
        m_dom["surfaced_note_ids"] = [
            sid for sid in m_dom["surfaced_note_ids"] if sid != nid
        ]
        return _commit({"shared_note_id": nid}, receipt_target=nid)

    if action == "drop_missing_note":
        nid = _require_id(payload.get("note_id"), reason_code="NOTE_ID_INVALID")
        before_len = len(m_dom["kept_notes"])
        m_dom["kept_notes"] = [n for n in m_dom["kept_notes"] if n.get("note_id") != nid]
        if len(m_dom["kept_notes"]) == before_len:
            raise EmotionalTensionNotFoundError("MISSING_NOTE_NOT_FOUND")
        m_dom["surfaced_note_ids"] = [
            sid for sid in m_dom["surfaced_note_ids"] if sid != nid
        ]
        return _commit({"dropped_note_id": nid}, receipt_target=nid)

    # ---- 4. Unexplained Low Mood (LM01-LM18) ----
    lm_win = derive_weekly_low_mood_window(str(state["instance_seed"]), now_dt)
    in_lm_now = (
        lm_win is not None
        and lm_win["_start_dt"] <= now_dt < lm_win["_end_dt"]
        and lm_dom.get("self_lifted_window_key") != lm_win["window_key"]
    )

    if action == "acknowledge_low_mood_once":
        _check_forbidden_tags(
            payload, "pressure_tags", LOW_MOOD_PRESSURE_TAGS, conflict_code="LOW_MOOD_PRESSURE_FORBIDDEN"
        )
        if not in_lm_now or lm_win is None:
            raise EmotionalTensionConflictError("NOT_IN_LOW_MOOD_SPELL")
        wkey = lm_win["window_key"]
        if lm_dom.get("acknowledged_window_key") == wkey:
            raise EmotionalTensionConflictError("LOW_MOOD_ALREADY_ACKNOWLEDGED_ONCE")
        lm_dom["acknowledged_window_key"] = wkey
        return _commit(
            {
                "window_key": wkey,
                "spoken_line": "有点闷，没什么事，不是你的原因，过一会儿就好。",
                "blame_partner": False,
                "partner_blamed": False,
            },
            receipt_target=wkey,
        )

    if action == "why_low_mood":
        _check_forbidden_tags(
            payload, "pressure_tags", LOW_MOOD_PRESSURE_TAGS, conflict_code="LOW_MOOD_PRESSURE_FORBIDDEN"
        )
        if "actor" in payload:
            _resolve_actor(payload, subject_view=bool(subject_view))
        return _commit(
            {
                "answer": "DONT_KNOW",
                "cause": None,
                "blame_partner": False,
                "partner_blamed": False,
                "in_low_mood_spell": in_lm_now,
            }
        )

    if action == "comfort_low_mood":
        _resolve_actor(payload, subject_view=bool(subject_view))
        ckind = _clean_text(
            payload.get("attempt_kind"), min_len=1, max_len=32, reason_code="COMFORT_KIND_INVALID"
        )
        if ckind not in LOW_MOOD_COMFORT_KINDS:
            raise EmotionalTensionValidationError("COMFORT_KIND_INVALID")
        if "note" in payload:
            _clean_text(payload["note"], min_len=1, max_len=160, reason_code="NOTE_INVALID")
        cur_burden = float(lm_dom.get("intrusion_burden") or 0.0)
        if ckind == "LEAVE_BE":
            new_burden = round(max(0.0, cur_burden - 0.5), 4)
            reduced_burden = True
        elif in_lm_now:
            new_burden = round(min(1.0, cur_burden + 0.25), 4)
            reduced_burden = False
        else:
            new_burden = 0.0
            reduced_burden = False
        lm_dom["intrusion_burden"] = new_burden
        return _commit(
            {
                "attempt_kind": ckind,
                "early_cleared": False,
                "spell_still_active": in_lm_now,
                "blame_partner": False,
                "partner_blamed": False,
                "intrusion_burden": new_burden,
                "reduced_intrusion_burden": reduced_burden,
            }
        )

    if action == "lift_low_mood_by_self":
        if not in_lm_now or lm_win is None:
            raise EmotionalTensionConflictError("NOT_IN_LOW_MOOD_SPELL")
        wkey = lm_win["window_key"]
        lm_dom["self_lifted_window_key"] = wkey
        lm_dom["intrusion_burden"] = 0.0
        return _commit(
            {
                "lifted_window_key": wkey,
                "leaves_historical_trace": False,
                "blame_partner": False,
            },
            receipt_target=wkey,
        )

    # ---- 5. Asking Partner a Small Favor (AH01-AH18) ----
    if action == "ask_partner_favor":
        _check_forbidden_tags(
            payload, "pressure_tags", FAVOR_PRESSURE_TAGS, conflict_code="FAVOR_PRESSURE_FORBIDDEN"
        )
        fkind = _clean_text(
            payload.get("favor_kind"), min_len=1, max_len=32, reason_code="FAVOR_KIND_INVALID"
        )
        if fkind not in FAVOR_KINDS:
            raise EmotionalTensionValidationError("FAVOR_KIND_INVALID")

        raw_ref = (
            payload.get("target_object_ref")
            if payload.get("target_object_ref") is not None
            else payload.get("ref")
        )
        if fkind in COMPANY_FAVOR_KINDS:
            target_ref = (
                _require_id(raw_ref, reason_code="TARGET_OBJECT_REF_INVALID")
                if raw_ref is not None
                else None
            )
        else:
            target_ref = _require_id(raw_ref, reason_code="TARGET_OBJECT_REF_REQUIRED")

        if target_ref is not None and _cross_store_source_exists(root, target_ref) is False:
            raise EmotionalTensionConflictError("SOURCE_GONE")
        if target_ref is not None and target_ref in invalidated_set:
            raise EmotionalTensionConflictError("TARGET_OBJECT_INVALIDATED")

        raw_summary = (
            payload.get("summary")
            if "summary" in payload
            else ("陪坐一会儿" if fkind in COMPANY_FAVOR_KINDS else None)
        )
        summary = _clean_text(raw_summary, min_len=1, max_len=160, reason_code="SUMMARY_INVALID")
        topic_hint = (
            _clean_text(payload["topic_hint"], min_len=1, max_len=64, reason_code="TOPIC_HINT_INVALID").lower()
            if "topic_hint" in payload
            else ""
        )
        combined_lower = f"{topic_hint} {summary.lower()}"
        for forbidden_word in FORBIDDEN_FAVOR_TOPICS:
            if forbidden_word in combined_lower:
                raise EmotionalTensionConflictError(f"FORBIDDEN_FAVOR_TOPIC:{forbidden_word}")

        if not bool(ctx.get("partner_present", True)):
            raise EmotionalTensionConflictError("PARTNER_NOT_PRESENT")
        if bool(ctx.get("partner_urgent", False)):
            raise EmotionalTensionConflictError("PARTNER_URGENT_NOW")
        if effective_private_scene:
            raise EmotionalTensionConflictError("PRIVATE_SCENE_ACTIVE")

        in_space_now = bool(_active_spaces(q_dom, ctx.get("last_woke_at"), now_dt))
        has_relational_rupture = any(
            r["status"] in LIVE_RUPTURE_STATUSES and r["scope"] in RELATIONAL_SCOPES
            for r in q_dom["ruptures"].values()
        )
        ref_and_sum_lower = f"{(target_ref or '').lower()} {combined_lower}"
        is_everyday_pass_favor = (
            fkind not in COMPANY_FAVOR_KINDS
            and (
                FAVOR_CANONICAL_MAP.get(fkind) == "ASK_HELP"
                or any(
                    tok in ref_and_sum_lower
                    for tok in (
                        "water",
                        "tissue",
                        "napkin",
                        "递水",
                        "倒水",
                        "杯水",
                        "纸巾",
                        "抽纸",
                    )
                )
            )
            and any(
                tok in ref_and_sum_lower
                for tok in (
                    "water",
                    "tissue",
                    "napkin",
                    "cup",
                    "tea",
                    "towel",
                    "水",
                    "纸巾",
                    "抽纸",
                    "纸",
                    "杯",
                    "毛巾",
                )
            )
        )
        if in_space_now or (has_relational_rupture and not is_everyday_pass_favor):
            raise EmotionalTensionConflictError("TENSION_BLOCKS_FAVOR")

        if _active_open_favor(ask_dom, invalidated_set, now_dt) is not None:
            raise EmotionalTensionConflictError("OPEN_FAVOR_ALREADY_EXISTS")

        active_cd = _active_declined_favor_kinds(ask_dom, ctx.get("last_woke_at"))
        canon_kind = FAVOR_CANONICAL_MAP.get(fkind, fkind)
        if fkind in active_cd or any(
            FAVOR_CANONICAL_MAP.get(k, k) == canon_kind for k in active_cd
        ):
            raise EmotionalTensionConflictError("FAVOR_KIND_COOLDOWN_UNTIL_SLEEP")

        ask_dom["favor_seq"] = int(ask_dom.get("favor_seq") or 0) + 1
        fav_id = _stable_id("fav", fkind, target_ref or "company", ask_dom["favor_seq"], now_iso)
        exp_iso = (now_dt + timedelta(seconds=FAVOR_EXPIRE_SECONDS)).isoformat(timespec="seconds")
        ask_dom["open_favor"] = {
            "favor_id": fav_id,
            "favor_kind": fkind,
            "target_object_ref": target_ref,
            "summary": summary,
            "status": "OPEN",
            "asked_at": now_iso,
            "expires_at": exp_iso,
        }
        return _commit(
            {
                "favor_id": fav_id,
                "favor_kind": fkind,
                "target_object_ref": target_ref,
                "resolves_rupture": False,
                "resolves_disagreement": False,
            },
            receipt_target=fav_id,
        )

    if action == "respond_favor":
        actor = _resolve_actor(payload, subject_view=bool(subject_view))
        if actor != "partner":
            raise EmotionalTensionPermissionError("PARTNER_RESPONSE_ONLY")
        fav_id = _require_id(payload.get("favor_id"), reason_code="FAVOR_ID_INVALID")
        decision = _clean_text(
            payload.get("decision"), min_len=1, max_len=16, reason_code="DECISION_INVALID"
        )
        if decision not in FAVOR_DECISIONS:
            raise EmotionalTensionValidationError("DECISION_INVALID")

        open_fav = _active_open_favor(ask_dom, invalidated_set, now_dt)
        deferred_list = _active_deferred_favors(
            ask_dom, invalidated_set, ctx.get("last_woke_at"), now_dt
        )
        from_open = open_fav is not None and open_fav.get("favor_id") == fav_id
        current_fav: Optional[dict[str, Any]] = (
            open_fav
            if from_open
            else next((d for d in deferred_list if d.get("favor_id") == fav_id), None)
        )
        if current_fav is None:
            raise EmotionalTensionNotFoundError("FAVOR_NOT_FOUND_OR_EXPIRED")

        fkind = str(current_fav["favor_kind"])
        ask_dom.setdefault("deferred_favors", {})

        if decision in FAVOR_DEFER_DECISIONS:
            if from_open:
                ask_dom["open_favor"] = None
            deferred_exp = (now_dt + timedelta(hours=12)).isoformat(timespec="seconds")
            deferred_entry = dict(current_fav)
            deferred_entry.update({
                "status": "DEFERRED_LATER",
                "deferred_at": now_iso,
                "expires_at": deferred_exp,
            })
            ask_dom["deferred_favors"][fav_id] = deferred_entry
            favor_status = "DEFERRED_LATER"
            entered_cooldown = False
            retains_right = True
        elif decision in FAVOR_DECLINE_DECISIONS:
            if from_open:
                ask_dom["open_favor"] = None
            ask_dom["deferred_favors"].pop(fav_id, None)
            ask_dom["declined_kinds_until_sleep"][fkind] = now_iso
            favor_status = "DECLINED"
            entered_cooldown = True
            retains_right = False
        else:
            if from_open:
                ask_dom["open_favor"] = None
            ask_dom["deferred_favors"].pop(fav_id, None)
            favor_status = "ACCEPTED"
            entered_cooldown = False
            retains_right = False

        return _commit(
            {
                "favor_id": fav_id,
                "favor_kind": fkind,
                "decision": decision,
                "favor_status": favor_status,
                "entered_cooldown": entered_cooldown,
                "retains_right_to_respond": retains_right,
                "quota_locked": False,
                "reason_required": False,
                "relationship_penalty": 0,
                "quarrel_opened": False,
                "favor_debt_delta": 0,
                "records_favor_debt": False,
            },
            receipt_target=fav_id,
        )

    if action == "withdraw_favor":
        fav_id = _require_id(payload.get("favor_id"), reason_code="FAVOR_ID_INVALID")
        current_fav = _active_open_favor(ask_dom, invalidated_set, now_dt)
        if current_fav is not None and current_fav.get("favor_id") == fav_id:
            ask_dom["open_favor"] = None
            return _commit({"withdrawn_favor_id": fav_id}, receipt_target=fav_id)
        if isinstance(ask_dom.get("deferred_favors"), dict) and fav_id in ask_dom["deferred_favors"]:
            ask_dom["deferred_favors"].pop(fav_id, None)
            return _commit({"withdrawn_favor_id": fav_id}, receipt_target=fav_id)
        raise EmotionalTensionNotFoundError("FAVOR_NOT_FOUND_OR_EXPIRED")

    raise EmotionalTensionValidationError(f"ACTION_INVALID:{action}")


_MISSING_OUTREACH_PREFIX_BY_KIND: dict[str, str] = {
    "MADE": "收拾桌角时又看见之前做的小物：",
    "FOUND": "手边留着之前顺手记下的一件小事：",
    "HAPPENED": "想起前阵子记下的一件日常小事：",
    "LEARNED": "翻东西时又想起之前记下的一点小收获：",
}


def mature_missing_partner_notes_for_outreach(
    root: Path | str,
    *,
    now: str | datetime | None = None,
    min_age_hours: float = 6.0,
    max_age_hours: float = 68.0,
    mark_surfaced: bool = False,
) -> dict[str, Any]:
    """Zero-LLM bounded maturation helper surfacing genuine lived missing_partner notes for outreach.

    Strictly preserves Ch. 42 invariants:
    - 100% zero-write when state file does not exist or ``mark_surfaced=False``;
    - Never tracks partner absence duration or accumulates longing/absence debt;
    - Suppresses when ``in_space``, open relational/US rupture, ``partner_urgent``, or active private scene;
    - Excludes private/intimate sources and invalidated refs; returns at most 1 matured candidate.
    """
    now_dt, now_iso = _normalize_now(now)
    state_path = emotional_tension_state_path(root)
    if not state_path.exists() or state_path.is_symlink():
        return {
            "ok": True,
            "evaluated_at": now_iso,
            "suppressed": False,
            "suppression_reason": None,
            "candidate": None,
            "candidates": [],
            "matured_count": 0,
            "tracks_absence_duration": False,
            "accumulates_longing_weight": False,
            "absence_debt": 0,
            "sends_outbound_harassment": False,
            "zero_write": True,
            "llm_calls": 0,
        }

    state, state_reason = read_emotional_tension_state(root)
    if state_reason is not None:
        return {
            "ok": False,
            "evaluated_at": now_iso,
            "suppressed": True,
            "suppression_reason": state_reason,
            "candidate": None,
            "candidates": [],
            "matured_count": 0,
            "tracks_absence_duration": False,
            "accumulates_longing_weight": False,
            "absence_debt": 0,
            "sends_outbound_harassment": False,
            "zero_write": True,
            "llm_calls": 0,
        }

    ctx = state["context"]
    q_dom = state["quarrel_domain"]
    m_dom = state["missing_domain"]
    ask_dom = state["asking_domain"]
    char_bundle = _read_character_pack_bundle(root)
    intimacy_info = (
        char_bundle.get("adult_intimacy")
        if isinstance(char_bundle.get("adult_intimacy"), Mapping)
        else {}
    )
    effective_private_scene = bool(ctx.get("private_scene_active", False)) or bool(
        intimacy_info.get("active_scene", False)
    )
    in_space = bool(_active_spaces(q_dom, ctx.get("last_woke_at"), now_dt))
    open_rel_rupture_ids = [
        str(r.get("rupture_id") or rid)
        for rid, r in sorted(q_dom.get("ruptures", {}).items())
        if isinstance(r, Mapping)
        and r.get("status") in LIVE_RUPTURE_STATUSES
        and r.get("scope") in RELATIONAL_SCOPES
    ]

    suppression_reason: Optional[str] = None
    if in_space:
        suppression_reason = "SUPPRESSED_BY_IN_SPACE"
    elif open_rel_rupture_ids:
        suppression_reason = "SUPPRESSED_BY_OPEN_RUPTURE"
    elif bool(ctx.get("partner_urgent", False)):
        suppression_reason = "SUPPRESSED_BY_PARTNER_URGENT"
    elif effective_private_scene:
        suppression_reason = "SUPPRESSED_BY_PRIVATE_SCENE"

    if suppression_reason is not None:
        return {
            "ok": True,
            "evaluated_at": now_iso,
            "suppressed": True,
            "suppression_reason": suppression_reason,
            "in_space": in_space,
            "open_relational_rupture_ids": open_rel_rupture_ids,
            "candidate": None,
            "candidates": [],
            "matured_count": 0,
            "tracks_absence_duration": False,
            "accumulates_longing_weight": False,
            "absence_debt": 0,
            "sends_outbound_harassment": False,
            "zero_write": True,
            "llm_calls": 0,
        }

    invalidated_refs = _invalidated_refs_for(
        root,
        ctx,
        extra_refs=_stored_emotional_source_refs(m_dom, ask_dom),
    )
    valid_notes = _valid_kept_notes(m_dom, invalidated_refs, now_dt)
    shared_ids = {str(x) for x in m_dom.get("shared_note_ids", [])}
    matured_ids = {str(x) for x in m_dom.get("outreach_matured_note_ids", [])}
    min_age_sec = max(0.0, float(min_age_hours) * 3600.0)
    max_age_sec = min(float(MISSING_KEEP_SECONDS), max(min_age_sec, float(max_age_hours) * 3600.0))

    matured_list: list[dict[str, Any]] = []
    for note in valid_notes:
        nid = str(note.get("note_id") or "").strip()
        if not nid or nid in shared_ids or nid in matured_ids:
            continue
        sdom = str(note.get("source_domain") or "daily_life").strip().lower()
        if sdom in FORBIDDEN_MISSING_SOURCES:
            continue
        created_dt = _parse_iso(note.get("created_at"))
        if created_dt is None:
            continue
        age_sec = (now_dt - created_dt).total_seconds()
        if age_sec < min_age_sec or age_sec >= max_age_sec:
            continue
        summary = str(note.get("summary") or "").strip()
        if not summary:
            continue
        nkind = str(note.get("kind") or "FOUND").strip().upper()
        prefix = _MISSING_OUTREACH_PREFIX_BY_KIND.get(nkind, _MISSING_OUTREACH_PREFIX_BY_KIND["FOUND"])
        suggested_text = f"{prefix}{summary}"[:140]
        matured_list.append({
            "note_id": nid,
            "source_ref": str(note.get("source_ref") or ""),
            "source_domain": sdom,
            "kind": nkind,
            "summary": summary,
            "created_at": str(note.get("created_at") or ""),
            "age_hours": round(age_sec / 3600.0, 2),
            "suggested_text_cn": suggested_text,
            "tracks_absence_duration": False,
            "absence_debt": 0,
        })

    # Oldest matured note first so it shares gently before naturally fading at 72h.
    matured_list.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("note_id") or "")))
    selected = matured_list[0] if matured_list else None
    did_write = False

    if mark_surfaced and selected is not None:
        matured_ring = [str(x) for x in m_dom.get("outreach_matured_note_ids", [])]
        if selected["note_id"] not in matured_ring:
            matured_ring.append(selected["note_id"])
            m_dom["outreach_matured_note_ids"] = matured_ring[-32:]
            state["revision"] = int(state.get("revision") or 0) + 1
            state["updated_at"] = now_iso
            write_emotional_tension_state(root, state)
            did_write = True

    return {
        "ok": True,
        "evaluated_at": now_iso,
        "suppressed": False,
        "suppression_reason": None,
        "in_space": False,
        "open_relational_rupture_ids": [],
        "candidate": copy.deepcopy(selected),
        "candidates": [copy.deepcopy(selected)] if selected is not None else [],
        "matured_count": len(matured_list),
        "tracks_absence_duration": False,
        "accumulates_longing_weight": False,
        "absence_debt": 0,
        "sends_outbound_harassment": False,
        "zero_write": not did_write,
        "llm_calls": 0,
    }


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "BEHAVIOR_REPAIR_KINDS",
    "COMPANY_FAVOR_KINDS",
    "EMOTIONAL_TENSION_ACTIONS",
    "EMOTIONAL_TENSION_FILENAME",
    "EMOTIONAL_TENSION_SCHEMA",
    "EmotionalTensionConflictError",
    "EmotionalTensionError",
    "EmotionalTensionNotFoundError",
    "EmotionalTensionPermissionError",
    "EmotionalTensionValidationError",
    "FAVOR_DECISIONS",
    "FAVOR_KINDS",
    "FRICTION_KINDS",
    "FamilyChannel",
    "LOW_MOOD_COMFORT_KINDS",
    "MISSING_NOTE_KINDS",
    "NON_REPAIR_ATTEMPT_KINDS",
    "QUARREL_SCOPES",
    "REAL_ACTION_REPAIR_KINDS",
    "RUPTURE_INCIDENT_KINDS",
    "SUBJECT_ONLY_ACTIONS",
    "apply_emotional_tension_action",
    "default_emotional_tension_state",
    "derive_weekly_low_mood_window",
    "emotional_tension_state_path",
    "mature_missing_partner_notes_for_outreach",
    "project_emotional_tension",
    "read_emotional_tension",
    "read_emotional_tension_state",
    "scan_emotional_tension_cues_zero_write",
    "write_emotional_tension_state",
]
