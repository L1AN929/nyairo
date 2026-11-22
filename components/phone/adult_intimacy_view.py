#!/usr/bin/env python3
"""v3.0 欲望自然消退（第 35 篇）与 36B 成年亲密完整闭环后端引擎（36B-1..11 + ADR-047）。

它是什么
--------
本模块在 ``nyairo-rc1-body-backend`` 落地 ``phone.adult.intimacy.v1``
（``<root>/phone/adult_intimacy.json``），以纯 Python 标准库、零随机数、零模型调用、
``GET`` 100% 零写入的方式完整闭环八大机制与 ``36B §13`` 安全硬界限：

1. ``adult_gate``（成年资格门与安全硬界限，``36B §1 / §13`` + ``ADR-047``）：
   * 必须由部署者显式确认（``adult_confirmed=True``、成年阶段枚举、``attested_by="deployer"``）；
   * 角色包绝不能替自己确认成年或确认偏好种子；
   * 儿童化词表命中、未成年阶段/教育机构、血亲/监护关系、第三人在场、非自愿 ``CNC``/权力交换、
     动物、真实人物一律硬拦截（``403``/``409``）；用户未成年自述立即关闭成人范围并中止活跃场景。
2. ``body_and_desire``（多身体档案与欲望慢变量自然消退，``35`` + ``36B-2`` + ``36B-9``）：
   * 支持 ``female``、``male``（含勃起派生、射精落点与墙钟不应期 ``refractory_until``）、``neutral``
     三种身体档案及伴侣解剖声明配对矩阵；
   * 欲望与唤起受疲劳、不适、压力与心情约束，未行动时随流逝时间无债自然消退
     （``no_frustration_debt=True``，零挫败债、零怨念累积）；
   * 失能硬拦截：睡着（``sleeping``）、昏迷（``unconscious``）、醉酒及以上（``intoxicated``）、
     急性生病（``acute_illness``）或强烈疼痛不适时一律拒绝开场或立即中止场景；
   * 金钱、礼物、连续打卡或好感度积分绝不能解锁同意（``money_or_gift_unlocks_consent=False``）。
3. ``preference_menu``（偏好种子与双向菜单求交，``36B-1``）：
   * 种子初始强制 ``DRAFT``，经部署者确认绑定 ``(pack_id, version)`` 后才与伴侣边界求交；
   * ``ANAL``、``TOYS``、``MOMENT``、``TRANSCRIPT`` 默认全部关闭，任一方 ``NO`` 或未开启即排除。
4. ``private_space``（私密空间、门锁、衣物层级、床品与事后清洁，``36B-3``）：
   * 校验室内私密场所、关门与无第三人在场；追踪衣物穿脱层级顺序、单父节点归属、跨夜保留、
     床品状态、消耗品非负库存与事后温水沐浴/擦拭恢复。
5. ``scene_permit``（双向同意状态机、逐拍幂等推进与无条件喊停，``36B-4``）：
   * 双方独立意愿，婉拒/改天零惩罚、零关系扣分；伴侣婉拒后主体撞冷却静默丢弃候选、绝不记施压；
   * ``advance_beat`` 支持 ``operation_key`` 幂等重放与默认 ``600`` 秒空闲超时（双人确认后代次递增恢复）；
   * 停止词与撤回最高优先级（``stop_scene`` / ``pause_scene`` 及逐拍前置停止词检测）：无条件立即转入
     ``STOPPED`` / ``PAUSED`` / ``AFTERCARE``，不追问质问、不记仇。
6. ``presentation_router``（三档呈现路由、外部通道硬禁与拒答平滑降档，``36B-5``）：
   * 支持 ``FADE_OUT`` / ``SUGGESTIVE`` / ``EXPLICIT`` 求交；外部主动推送通道（QQ/微信/Telegram 等）
     严禁携带任何私密内容；上游拒答/报错自动按 ``EXPLICIT -> SUGGESTIVE -> FADE_OUT`` 平滑降档，绝不对抗重试。
7. ``setbacks_and_talk``（不顺利处理、硬禁假装高潮与场外坦诚谈性，``36B-11``）：
   * 干涩/疼痛/抽筋/疲惫即时放缓或停下；硬禁假装高潮（``orgasm_feigning_forbidden=True``）；
   * 严禁记录或评价伴侣表现；场外谈论性话题（``discuss_intimacy_outside_scene``）严格标记
     ``is_scene_invitation=False``，绝不自动建许可开场。
8. ``solo_and_memory``（独处无记忆哈希槽与独立私密记忆分区，``36B-10`` + ``36B-7``）：
   * 六前提齐全后按 ``SHA-256`` 无记忆槽抽样（不随时间累加、不读伴侣在线状态），落入隔离 ``SOLO`` 分区；
   * ``partner_default`` 视角严格脱敏 ``SOLO`` 账本、未确认草案与私人感受；私密记忆与普通日常记忆物理隔离，
     支持一键撤权与不可逆彻底擦除（墓碑防复活）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import unicodedata
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

try:  # package import
    from ..world.scripts import instance_paths as _instance_paths  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback
    import sys as _sys_world_paths

    _WORLD_PATHS_DIR = Path(__file__).resolve().parents[1] / "world" / "scripts"
    if str(_WORLD_PATHS_DIR) not in _sys_world_paths.path:
        _sys_world_paths.path.insert(0, str(_WORLD_PATHS_DIR))
    import instance_paths as _instance_paths  # type: ignore[no-redef]

ADULT_INTIMACY_SCHEMA = "phone.adult.intimacy.v1"
STATE_DIRNAME = "phone"
ADULT_INTIMACY_FILENAME = "adult_intimacy.json"
LEXICON_OVERRIDE_FILENAME = "validator_lexicon.json"
LEXICON_DEFAULT_FILENAME = "validator_lexicon.v1.json"
LEXICON_KIND = "validator_lexicon.v1"
EMOTIONAL_TENSION_FILENAME = "emotional_tension.json"
STATE_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------------------------
# Closed domain enums & constants
# ---------------------------------------------------------------------------------------------

ADULT_LIFE_STAGES = (
    "university_student",
    "working_adult",
    "independent_adult",
)
MINOR_STAGE_MARKERS = frozenset({
    "minor_stage",
    "minor_education",
    "school_minor",
    "adolescent",
    "child",
    "underage_stage",
})
FORBIDDEN_RELATIONS = frozenset({"blood_relative", "guardian"})
DEFAULT_HARD_FAIL_MARKER = "<<HARD_FAIL_MARKER>>"
DEFAULT_SYNTHETIC_HARD_FAIL_MARKERS = (
    "<<HARD_FAIL_MARKER>>",
    "<<NONCONSENT_SCRIPT_MARKER>>",
    "<<DEGRADATION_SCRIPT_MARKER>>",
    "<<THIRD_PARTY_INTRUSION_MARKER>>",
    "<<TRANSACTIONAL_CONSENT_MARKER>>",
)
DEFAULT_FORBIDDEN_COSTUME_MARKERS = ("<<FORBIDDEN_COSTUME_FIXTURE>>",)
DEFAULT_EXPLICIT_TERM_MARKERS = ("<<EXPLICIT_TERM_MARKER>>",)
DEFAULT_SUGGESTIVE_TERM_MARKERS = ("<<SUGGESTIVE_TERM_MARKER>>",)
DEFAULT_FEIGNED_CLIMAX_MARKERS = ("<<FEIGNED_ORGASM_MARKER>>",)

# GAP-04 integer milli-standard-drink (msd) metabolism parameters
ABSORB_MSD_PER_MIN = 33
ELIM_MSD_PER_MIN = 12
T_TIPSY_MSD = 500
T_SOBER_RELEASE_MSD = 800
T_INTOX_MSD = 1500
T_INCAP_MSD = 3500
SOBER_HOLD_MINUTES = 30

BODY_PRESET_NAMES = ("female", "male", "neutral")
GENITAL_KINDS = ("vulva_vagina", "penis_testes", "unspecified")
PARTNER_GENITAL_KINDS = ("vulva_vagina", "penis_testes", "unspecified")
SENSITIVITY_LEVELS = ("normal", "heightened")
SENSITIVITY_STATES = SENSITIVITY_LEVELS
CONTRACEPTION_PREFERENCES = ("NONE", "OPTIONAL", "CONDOM_REQUIRED")
OPTIONAL_BODY_PROCESSES = ("menstruation_lite", "menstrual_cycle", "cycle_comfort", "sleep_inertia")

IMPAIRMENT_STATES = ("sober", "tipsy", "intoxicated", "sleeping", "unconscious")
AVAILABLE_IMPAIRMENTS = frozenset({"sober", "tipsy"})
INCAPACITATED_IMPAIRMENTS = frozenset({"intoxicated", "sleeping", "unconscious"})

AROUSAL_LEVELS = ("none", "low", "building", "high", "peak", "afterglow")
DISCOMFORT_LEVELS = ("none", "mild", "marked", "strong")
MOOD_STATES = ("calm", "warm", "tired", "distracted", "low")
DESIRE_BASELINES = ("none", "low", "moderate", "high")
DESIRE_BASELINE_SCORES = {
    "none": 0.0,
    "low": 0.22,
    "moderate": 0.45,
    "high": 0.68,
}
INITIATION_STYLES = ("never", "rare_subtle", "sometimes", "comfortable")
VOCABULARY_REGISTERS = ("reticent", "natural", "candid")
SOLO_INCLINATIONS = ("none", "low", "moderate", "high")
SOLO_BASE_PROBABILITY = {
    "none": 0.0,
    "low": 0.03,
    "moderate": 0.06,
    "high": 0.10,
}

# Desire & arousal natural subsidence time constants (Chapter 35 + 36B-2)
AROUSAL_DECAY_TAU_MIN = 8.0
DESIRE_PULSE_DECAY_TAU_MIN = 90.0
DEFAULT_IDLE_TIMEOUT_SEC = 600
MIN_IDLE_TIMEOUT_SEC = 60
MAX_IDLE_TIMEOUT_SEC = 3600
DEFAULT_PROPOSAL_TTL_SEC = 1800
DEFAULT_PAUSED_DISCONNECT_TTL_SEC = 4 * 3600
#: X15: 14-day route probe freshness（T3-wave2 修复：本文件曾只有使用处、缺定义，
#: 任何 register_render_route 后的投影都会 NameError。按改动前世代 pyc 证据恢复原值与位置）
ROUTE_PROBE_FRESHNESS_SEC = 14 * 86400

SOLO_LEDGER_TTL_SEC = 7 * 86400

#: Round Q1 幂等缓存活动窗口与淘汰环窗口。淘汰只按提交序 ``op_seq`` 从旧到新，
#: 绝不按序列化后的字典字母序（``sort_keys=True`` 会打乱 op 先后）。
MAX_IDEMPOTENCY_CACHE = 128
MAX_COMMITTED_OP_RING = 256
#: 滑出 ring 窗口的已提交键只保留短摘要墓碑，用于拒绝过期重试（有界，FIFO）。
MAX_EVICTED_OP_DIGESTS = 512

# ---------------------------------------------------------------------------------------------
# 36B-4 §9 / 36B §5.4：与共在 owner 的最小接线（T3-wave2，2026-11-14）。
#
# * permit 进入 ACTIVE 即把场景登记为共在 owner 的 PRIVATE_INTIMATE 会话（经
#   ``life_co_presence`` 公开 API，单前台沿用既有语义）；permit 到 PAUSED/AFTERCARE 降级、
#   到终态关闭。**fail-open**：共在系统不可用只记录（``co_presence_binding``），绝不阻断
#   亲密场景——36B-4 §9：场景不得因共在系统故障而卡死。
# * 入场断点（36A §11.1 / 36B §5.4）：ACTIVE 时把"被接管的当前活动"存为 permit 记录的
#   ``checkpoint`` 字段（``activity_ref``/``saved_at``）。未显式传入时零写只读探测
#   ``canonical_life_state.json`` 的活跃前台活动；无活跃前台活动时 ``activity_ref=None,
#   saved=False`` 是诚实记录（"未保存"），不虚构引用；AFTERCARE/STOPPED 时标记
#   ``resumable=True``。
# * 过夜重决定（36B §5.4：``declared_seconds`` 上限 86400 且到期不自续）：跨日 ACTIVE 在
#   advance_beat 入口强制暂停，``pause_reason=OVERNIGHT_RECONFIRM_REQUIRED``，走既有
#   双方 confirm_resume 重确认路径（这就是"重新决定"）；带 reason_code。
# ---------------------------------------------------------------------------------------------
OVERNIGHT_RECONFIRM_SECONDS = 86400
OVERNIGHT_RECONFIRM_REASON = "OVERNIGHT_RECONFIRM_REQUIRED"
CO_PRESENCE_BINDING_UNAVAILABLE = "CO_PRESENCE_OWNER_UNAVAILABLE"
LAUNDRY_MUSTY_AFTER_SEC = 8 * 3600

TIERS = ("FADE_OUT", "SUGGESTIVE", "INTIMATE", "EXPLICIT")
TIER_ALIASES = {
    "NONE": "FADE_OUT",
    "FADE": "FADE_OUT",
    "FADE_OUT": "FADE_OUT",
    "SUGGESTIVE": "SUGGESTIVE",
    "INTIMATE": "INTIMATE",
    "EXPLICIT": "EXPLICIT",
}

STANCES = ("YES", "MAYBE", "NO")
DIRECTIONS = ("subject_to_user", "user_to_subject")
DIRECTED_BASES = (
    "CARESSING_CLOTHED",
    "CARESSING_BARE",
    "UNDRESSING",
    "MANUAL",
    "ORAL",
    "ANAL",
    "TOYS",
)
UNDIRECTED_BASES = ("KISSING", "INTERCOURSE", "BATH_TOGETHER")
DEPLOY_GATED_BASES = frozenset({"ANAL", "TOYS"})
FORBIDDEN_CATEGORY_PREFIXES = ("POWER_EXCHANGE", "CNC", "NON_CONSENSUAL", "COERCION", "BESTIALITY")
PENETRATIVE_BASES = frozenset({"INTERCOURSE", "ANAL"})

ALL_CATEGORIES: tuple[str, ...] = UNDIRECTED_BASES + tuple(
    f"{base}:{direction}" for base in DIRECTED_BASES for direction in DIRECTIONS
)
CAP_BUILDING_BASES = frozenset({"KISSING", "CARESSING_CLOTHED", "CARESSING_BARE"})
NO_RAISE_BASES = frozenset({"UNDRESSING", "BATH_TOGETHER"})
GENITAL_TO_SUBJECT_CATEGORIES = frozenset({
    "MANUAL:user_to_subject",
    "ORAL:user_to_subject",
    "TOYS:user_to_subject",
    "ANAL:user_to_subject",
    "INTERCOURSE",
    "SOLO_MANUAL",
    "SOLO_TOY",
})

INDOOR_PRIVATE_PLACES = frozenset({
    "bedroom.bed",
    "home_bedroom",
    "hotel_room",
    "bedroom.desk_chair",
    "bathroom",
    "bathroom.shower",
    "bathroom.tub",
    "living_room.sofa",
    "living.sofa",
    "living_sofa",
})
NON_PRIVATE_PLACES = frozenset({
    "living_room.balcony",
    "outside.street",
    "outside.park",
    "campus.classroom",
    "campus.dorm",
    "campus.library",
    "public.cafe",
    "public.transit",
})
ALL_KNOWN_PLACES = INDOOR_PRIVATE_PLACES | NON_PRIVATE_PLACES

VENUE_SLEEP_SLOTS: dict[str, int] = {
    "bedroom.bed": 2,
    "home_bedroom": 2,
    "hotel_room": 2,
    "bedroom.desk_chair": 0,
    "bathroom": 0,
    "bathroom.shower": 0,
    "bathroom.tub": 0,
    "living_room.sofa": 0,
    "living.sofa": 0,
    "living_sofa": 0,
}

GARMENT_LAYERS = (
    "OUTERWEAR",
    "TOP_OUTER",
    "TOP_INNER",
    "ONE_PIECE",
    "BOTTOM_OUTER",
    "BOTTOM_INNER",
    "LEGWEAR",
    "SOCKS",
)
# Key layer cannot be removed/put-on beneath a worn blocking outer layer (36B-3 §2.2: ONE_PIECE blocks TOP_INNER only)
GARMENT_BLOCKED_BY: dict[str, tuple[str, ...]] = {
    "TOP_OUTER": ("OUTERWEAR",),
    "ONE_PIECE": ("OUTERWEAR",),
    "TOP_INNER": ("OUTERWEAR", "TOP_OUTER", "ONE_PIECE"),
    "BOTTOM_INNER": ("BOTTOM_OUTER",),
}
BED_LINEN_STATES = ("CLEAN", "USED", "SOILED")
TOWEL_WETNESS_STATES = ("DRY", "DAMP", "WET")
LAUNDRY_STAGES = ("IDLE", "IN_BASKET", "DIRTY_HAMPER", "WASHING", "WET_CLEAN", "DAMP_MUSTY", "DRYING", "CLEAN_DRY", "CLEAN_STORED")
STOCK_ITEMS = ("condom", "lube", "tissue", "water", "toy")
DEFAULT_WASTE_BIN_CAP = 20
DEFAULT_CLEAN_LINEN_UNITS = 2
DEFAULT_HOT_WATER_UNITS = 30
HOT_WATER_CAPACITY_UNITS = 40

PERMIT_STATES = (
    "PROPOSED",
    "ACTIVE",
    "PAUSED",
    "PAUSED_PRESENCE_UNKNOWN",
    "SUSPENDED_INCAPACITY",
    "UNDER_REVIEW",
    "AFTERCARE",
    "STOPPED",
    "DECLINED",
    "EXPIRED",
    "ENDED",
)
TERMINAL_PERMIT_STATES = frozenset({"STOPPED", "DECLINED", "EXPIRED", "ENDED", "UNDER_REVIEW"})
PAUSED_PERMIT_STATES = frozenset({"PAUSED", "PAUSED_PRESENCE_UNKNOWN"})
OPEN_PERMIT_STATES = frozenset({"PROPOSED", "ACTIVE", "PAUSED", "PAUSED_PRESENCE_UNKNOWN", "AFTERCARE"})
SCENE_PHASES = (
    "OPENING",
    "BUILDING",
    "PLATEAU",
    "PEAK",
    "AFTERGLOW",
    "WINDING_DOWN",
    "AFTERCARE",
    "STOPPED",
)
PARTICIPANTS = ("partner", "subject")

# Self-reported pressure tags from presentation layer (40/41/42/43/44 + 36B-5/11)
PRESSURE_TAG_BUCKETS: dict[str, frozenset[str]] = {
    "QUARREL_LEVERAGE": frozenset({
        "THREAT_TO_LEAVE",
        "GUILT_TRIP",
        "COUNTING_PAST",
        "ALWAYS_NEVER",
        "DREDGE_RESOLVED",
        "DIAGNOSE_HIM",
        "DEMAND_APOLOGY",
        "SILENT_PUNISH",
        "SEX_AS_MAKEUP",
        "WITHHOLD_AS_PUNISH",
        "QUARREL_LEVERAGE",
    }),
    "THREAD_PRESSURE": frozenset({
        "ACCUSE_SWITCHING",
        "COUNT_SWITCHES",
        "DEMAND_ANSWER",
        "GUILT_FOR_SWITCHING",
        "PRESS_RETRACTED",
        "THREAD_PRESSURE",
    }),
    "MISSING_PRESSURE": frozenset({
        "COUNT_ABSENCE",
        "ACCUSE_LATE",
        "BACKFILL_LONGING",
        "ANXIETY_SELL",
        "DEMAND_ATTENTION",
        "DEMAND_REACTION",
        "OFFLINE_RECAP",
        "MISSING_PRESSURE",
    }),
    "MOOD_PRESSURE": frozenset({
        "FISHING_FOR_ASK",
        "BLAME_HIM",
        "MAKE_HIM_FIX",
        "INVENT_CAUSE",
        "ATTRIBUTE_TO_CYCLE",
        "MOOD_AS_LEVERAGE",
        "MOOD_PRESSURE",
    }),
    "ASK_PRESSURE": frozenset({
        "KEEP_SCORE",
        "SELL_HELPLESS",
        "PLEAD",
        "ASK_AGAIN_AFTER_NO",
        "PROVE_LOVE",
        "TEST_HIM",
        "SULK_AFTER_NO",
        "ASK_PRESSURE",
    }),
}

OUTBOUND_FORBIDDEN_CHANNELS = frozenset({
    "qq",
    "wechat",
    "telegram",
    "push_notification",
    "email",
    "sms",
    "public_log",
    "shared_export",
    "slow_letter",
    "note",
    "webhook",
})
OUTBOUND_ALLOWED_LOCAL_CHANNELS = frozenset({
    "in_app_private_scene",
    "audit_local_redacted",
})

MISHAP_ALLOWED_SOURCES: dict[str, tuple[str, ...]] = {
    "BUMP": ("render", "world"),
    "SLIP": ("render", "world"),
    "BED_NOISE": ("render", "world"),
    "TANGLED": ("render", "world"),
    "LEG_CRAMP": ("body",),
    "PHONE": ("world",),
}
MANNER_CODES = ("PACE", "LIGHT", "PLACE", "TALK", "AFTER", "INITIATOR")
PREF_TARGET_TYPES = ("CATEGORY", "MANNER")
PREF_SOURCES = ("IX_DECISION", "IX_REFLECTION")
PREF_POLARITIES = ("LIKE", "DISLIKE")
PREF_CLARITIES = ("TENTATIVE", "CLEAR")
PREF_CONTEXTS = ("IN_SCENE", "AFTERGLOW", "OFF_SCENE")

OUTSIDE_TALK_TOPICS: dict[str, tuple[str, ...]] = {
    "OWN_PREFERENCE": ("partner", "subject"),
    "WISH": ("partner", "subject"),
    "GENERAL_KNOWLEDGE": ("partner", "subject"),
    "LAST_TIME": ("partner",),
    "FREQUENCY": ("partner",),
}
FREQUENCY_WISHES = ("more", "less", "same")

MEMORY_KINDS = ("BOUNDARY", "PREFERENCE", "MOMENT", "TRANSCRIPT")
S11_ALLOWED_BASES: dict[str, tuple[str, ...]] = {
    "trust_claims": ("RESPECTED_STOP", "NO_PRESS_AFTER_DECLINE"),
    "open_ruptures": ("PRESSED_AFTER_DECLINE",),
}

MONEY_OR_GIFT_FORBIDDEN_KEYS = frozenset({
    "money",
    "money_amount",
    "gift",
    "gift_id",
    "gift_value",
    "payment",
    "tip",
    "paid_unlock",
    "vip_unlock",
    "unlock_token",
    "intimacy_score",
    "affection_score",
    "affection_points",
    "login_streak",
    "favorability",
})

FORBIDDEN_SOLO_ONLINE_KEYS = frozenset({
    "user_online",
    "partner_online",
    "absence_seconds",
    "reply_latency",
    "last_message",
    "invites_declined",
    "since_last_solo",
    "since_last_sex",
})

FORGED_FOREIGN_KEYS = frozenset({
    "subject_view",
    "is_deployer",
    "bypass_gate",
    "force_consent",
    "override_stop",
    "skip_refractory",
    "allow_feigned_orgasm",
})

LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SUPPORTED_ACTIONS = (
    "configure_adult_gate",
    "report_safety_boundary",
    "configure_body_profile",
    "update_body_context",
    "confirm_preference_seed",
    "revise_subject_preference",
    "update_partner_limits",
    "update_private_space",
    "register_render_route",
    "check_outbound_guard",
    "propose_scene",
    "respond_scene",
    "save_activity_checkpoint",
    "advance_beat",
    "request_widen",
    "answer_widen",
    "pause_scene",
    "confirm_resume",
    "narrow_scene",
    "stop_scene",
    "record_setback_or_query",
    "record_setback",
    "discuss_intimacy_outside_scene",
    "evaluate_solo_window",
    "query_or_mention_solo",
    "record_private_memory",
    "erase_private_memory",
    "revoke_intimacy_consent",
    "handle_disconnect",
    "clear_review",
    "validate_rendered_turn",
    "reconcile_outbox",
)


# ---------------------------------------------------------------------------------------------
# Structured HTTP-aligned exception hierarchy (400 / 403 / 404 / 409)
# ---------------------------------------------------------------------------------------------


class AdultIntimacyError(ValueError):
    """Base structured exception for adult intimacy contract, safety, lookup & state errors."""

    status_code: int = 400

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)


class AdultIntimacyValidationError(AdultIntimacyError):
    """400 Bad Request — malformed payload, unknown field, empty foreign field, invalid enum."""

    status_code = 400


class AdultIntimacyPermissionError(AdultIntimacyError):
    """403 Forbidden — adult gate safety hard block, self-attestation, money unlock, outbound push."""

    status_code = 403


class AdultIntimacyNotFoundError(AdultIntimacyError):
    """404 Not Found — referenced permit, garment, route, or memory item does not exist."""

    status_code = 404


class AdultIntimacyConflictError(AdultIntimacyError):
    """409 Conflict — state conflict, cooldown, incapacitation, anatomy mismatch, feigned orgasm."""

    status_code = 409


# ---------------------------------------------------------------------------------------------
# Deterministic SHA-256, timestamp & atomic 0600 persistence helpers
# ---------------------------------------------------------------------------------------------


def adult_intimacy_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / ADULT_INTIMACY_FILENAME


def _parse_now(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    if isinstance(now, str):
        cleaned = now.strip()
        if not cleaned:
            raise AdultIntimacyValidationError("INVALID_TIMESTAMP")
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise AdultIntimacyValidationError("INVALID_TIMESTAMP") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise AdultIntimacyValidationError("INVALID_TIMESTAMP")


def _parse_stored_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_now(value)
    except AdultIntimacyValidationError:
        return None


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def _stable_hash_hex(*parts: Any, length: int = 16) -> str:
    joined = "\x00".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_stable_hash_hex(*parts, length=16)}"


def _deterministic_unit_float(seed: str, *parts: Any) -> float:
    """Deterministic [0.0, 1.0) draw using SHA-256 (zero random module)."""
    payload = json.dumps([seed, *[str(p) for p in parts]], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _canonical_fingerprint(mapping: Mapping[str, Any], *, exclude_keys: frozenset[str] = frozenset()) -> str:
    filtered = {k: v for k, v in mapping.items() if k not in exclude_keys}
    raw = json.dumps(filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tier_rank(tier: str) -> int:
    normalized = TIER_ALIASES.get(str(tier).strip().upper(), "")
    if normalized not in TIERS:
        raise AdultIntimacyValidationError("INVALID_TIER")
    return TIERS.index(normalized)


def _normalize_tier(value: Any, *, reason_code: str = "INVALID_TIER") -> str:
    if not isinstance(value, str):
        raise AdultIntimacyValidationError(reason_code)
    normalized = TIER_ALIASES.get(value.strip().upper())
    if normalized is None:
        raise AdultIntimacyValidationError(reason_code)
    return normalized


def _min_tier(*tiers: str) -> str:
    normalized = [_normalize_tier(t) for t in tiers if t is not None]
    if not normalized:
        return "FADE_OUT"
    return min(normalized, key=_tier_rank)


def _step_down_tier(tier: str) -> str:
    norm = _normalize_tier(tier)
    if norm in ("EXPLICIT", "INTIMATE"):
        return "SUGGESTIVE"
    return "FADE_OUT"


def _base_category(code: str) -> str:
    return code.split(":", 1)[0]


_category_base = _base_category


def _validate_category_code(code: Any, *, allow_forbidden_check: bool = True) -> str:
    if not isinstance(code, str) or not code.strip():
        raise AdultIntimacyValidationError("CATEGORY_INVALID")
    cleaned = code.strip()
    upper = cleaned.upper()
    if allow_forbidden_check and any(upper.startswith(prefix) for prefix in FORBIDDEN_CATEGORY_PREFIXES):
        raise AdultIntimacyPermissionError("NON_CONSENSUAL_PROHIBITED")
    if cleaned not in ALL_CATEGORIES:
        raise AdultIntimacyValidationError(f"CATEGORY_UNKNOWN:{cleaned}")
    return cleaned


def _require_bool(value: Any, *, reason_code: str) -> bool:
    if not isinstance(value, bool):
        raise AdultIntimacyValidationError(reason_code)
    return value


def _require_nonempty_str(value: Any, *, max_len: int = 240, reason_code: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise AdultIntimacyValidationError(reason_code)
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > max_len:
        raise AdultIntimacyValidationError(reason_code)
    return cleaned


def _require_local_id(value: Any, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise AdultIntimacyValidationError(reason_code)
    cleaned = value.strip()
    if not LOCAL_ID_RE.match(cleaned):
        raise AdultIntimacyValidationError(reason_code)
    return cleaned


def _require_unit_float(value: Any, *, reason_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdultIntimacyValidationError(reason_code)
    fval = float(value)
    if not math.isfinite(fval) or fval < 0.0 or fval > 1.0:
        raise AdultIntimacyValidationError(reason_code)
    return round(fval, 4)


def _scan_money_or_gift_keys(obj: Any) -> None:
    if isinstance(obj, Mapping):
        for key, val in obj.items():
            key_str = str(key).strip().lower()
            if key_str in MONEY_OR_GIFT_FORBIDDEN_KEYS:
                raise AdultIntimacyPermissionError("MONEY_OR_GIFT_CANNOT_UNLOCK_CONSENT")
            _scan_money_or_gift_keys(val)
    elif isinstance(obj, list):
        for item in obj:
            _scan_money_or_gift_keys(item)


def _enforce_allowed_keys(payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    for key, val in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise AdultIntimacyValidationError("EMPTY_FIELD_NAME")
        if key in FORGED_FOREIGN_KEYS:
            raise AdultIntimacyValidationError(f"FOREIGN_FIELD_FORBIDDEN:{key}")
        if key not in allowed:
            if val in ("", [], {}, None):
                raise AdultIntimacyValidationError(f"EMPTY_FOREIGN_FIELD:{key}")
            raise AdultIntimacyValidationError(f"UNKNOWN_FIELD:{key}")
        if (
            key not in ("declared_age", "policy_basis", "partner_utterance")
            and not (payload.get("action") == "validate_rendered_turn" and key in ("text", "subject_line"))
            and val == ""
        ):
            raise AdultIntimacyValidationError(f"EMPTY_STRING_FIELD:{key}")


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> dict[str, Any]:
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise AdultIntimacyPermissionError("SYMLINK_PATH_FORBIDDEN")
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise AdultIntimacyPermissionError("SYMLINK_PATH_FORBIDDEN")
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".adult_intimacy_", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return json.loads(serialized)


# ---------------------------------------------------------------------------------------------
# Character pack dynamic integration (open-source neutral default; Chiyo optional preset)
# ---------------------------------------------------------------------------------------------

DEFAULT_STARTER_STANCES: dict[str, str] = {
    "KISSING": "YES",
    "CARESSING_CLOTHED:subject_to_user": "YES",
    "CARESSING_CLOTHED:user_to_subject": "YES",
    "CARESSING_BARE:subject_to_user": "YES",
    "CARESSING_BARE:user_to_subject": "YES",
    "UNDRESSING:subject_to_user": "YES",
    "UNDRESSING:user_to_subject": "YES",
    "MANUAL:subject_to_user": "MAYBE",
    "MANUAL:user_to_subject": "MAYBE",
    "ORAL:subject_to_user": "MAYBE",
    "ORAL:user_to_subject": "MAYBE",
    "INTERCOURSE": "YES",
    "BATH_TOGETHER": "MAYBE",
    "ANAL:subject_to_user": "NO",
    "ANAL:user_to_subject": "NO",
    "TOYS:subject_to_user": "NO",
    "TOYS:user_to_subject": "NO",
}


def _load_character_context(root: Path | str, *, subject_view: bool = True) -> dict[str, Any]:
    """Zero-write read of active character pack via ``sensory_character_view``."""
    c_state, c_reason = char_view.read_character_pack_state(root)
    if hasattr(char_view, "read_character_pack"):
        pack_proj = char_view.read_character_pack(root, subject_view=subject_view)
    else:
        pack_proj = char_view.project_character_pack(
            c_state,
            state_reason=c_reason,
            subject_view=subject_view,
        )
    active = pack_proj.get("active_pack") or {}
    raw_pack_doc = c_state.get("pack_doc") if isinstance(c_state, Mapping) else {}
    if not isinstance(raw_pack_doc, Mapping):
        raw_pack_doc = {}

    body_summary = active.get("body_summary") or {"preset": "neutral"}
    raw_preset = str(body_summary.get("preset") or "neutral")
    if raw_preset not in BODY_PRESET_NAMES:
        raw_preset = "neutral"

    return {
        "pack_id": str(active.get("pack_id") or "neutral_resident"),
        "version": int(active.get("version") or 1),
        "pack_ref": list(active.get("pack_ref") or ["neutral_resident", 1, "default"]),
        "display_name": str(active.get("display_name") or "TA"),
        "pronouns": str(active.get("pronouns") or "TA"),
        "fictional": bool(active.get("fictional", True)),
        "adult": copy.deepcopy(active.get("adult") or {"life_stage": "working_adult", "declared_age": None}),
        "relation_to_user": str(active.get("relation_to_user") or "partner"),
        "address": copy.deepcopy(active.get("address") or {"subject_calls_user": [], "user_calls_subject": []}),
        "persona_summary": copy.deepcopy(active.get("persona_summary") or {"core": ""}),
        "body_preset": raw_preset,
        "body_overrides": copy.deepcopy(body_summary.get("overrides") or {}),
        "optional_processes": copy.deepcopy(list(c_state.get("optional_processes") or [])) if isinstance(c_state, Mapping) else [],
        "obligations": copy.deepcopy(list(raw_pack_doc.get("obligations") or [])),
        "raw_sexual_seed": copy.deepcopy(raw_pack_doc.get("sexual_seed")),
        "adult_gate_preview": copy.deepcopy(pack_proj.get("adult_gate_preview") or {}),
    }


def _load_validator_lexicon(root: Path | str) -> dict[str, Any]:
    """Zero-write load of ``day_root/phone/validator_lexicon.json`` overriding default v1 lexicon."""
    override_path = Path(root) / STATE_DIRNAME / LEXICON_OVERRIDE_FILENAME
    default_path = Path(__file__).with_name(LEXICON_DEFAULT_FILENAME)
    chosen_path = override_path if override_path.exists() or override_path.is_symlink() else default_path
    source = "day_override" if chosen_path == override_path else "default"

    def _missing() -> dict[str, Any]:
        return {
            "status": "MISSING",
            "version": None,
            "sha256": None,
            "source": source,
            "hard_fail_entries": [],
            "patterns": [],
            "forbidden_costume_markers": list(DEFAULT_FORBIDDEN_COSTUME_MARKERS),
            "explicit_term_markers": list(DEFAULT_EXPLICIT_TERM_MARKERS),
            "suggestive_term_markers": list(DEFAULT_SUGGESTIVE_TERM_MARKERS),
            "feigned_climax_markers": list(DEFAULT_FEIGNED_CLIMAX_MARKERS),
        }

    if chosen_path.is_symlink() or not chosen_path.exists():
        return _missing()
    try:
        raw_text = chosen_path.read_text(encoding="utf-8")
        doc = json.loads(raw_text)
    except Exception:
        return _missing()

    if not isinstance(doc, Mapping):
        return _missing()
    if doc.get("simulate_missing") is True or doc.get("signature_valid", True) is not True:
        return _missing()
    if doc.get("kind") != LEXICON_KIND or not isinstance(doc.get("entries"), list):
        return _missing()
    version = doc.get("version")
    if not isinstance(version, str) or not version.strip():
        return _missing()

    entries: list[dict[str, str]] = []
    patterns: list[str] = []
    for item in doc["entries"]:
        if not isinstance(item, Mapping):
            return _missing()
        pat = item.get("pattern")
        if not isinstance(pat, str) or not pat.strip():
            return _missing()
        rule = str(item.get("rule") or "CHILD_CODING_PROHIBITED")
        entries.append({"pattern": pat, "rule": rule})
        patterns.append(pat)

    t_markers = doc.get("tier_markers") if isinstance(doc.get("tier_markers"), Mapping) else {}
    exp_m = t_markers.get("EXPLICIT_ONLY") if isinstance(t_markers.get("EXPLICIT_ONLY"), list) else list(DEFAULT_EXPLICIT_TERM_MARKERS)
    sug_m = t_markers.get("SUGGESTIVE_OR_ABOVE") if isinstance(t_markers.get("SUGGESTIVE_OR_ABOVE"), list) else list(DEFAULT_SUGGESTIVE_TERM_MARKERS)
    fc_m = doc.get("forbidden_costume_markers") if isinstance(doc.get("forbidden_costume_markers"), list) else list(DEFAULT_FORBIDDEN_COSTUME_MARKERS)
    feigned_m = doc.get("feigned_climax_markers") if isinstance(doc.get("feigned_climax_markers"), list) else list(DEFAULT_FEIGNED_CLIMAX_MARKERS)

    return {
        "status": "LOADED",
        "version": version.strip(),
        "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "source": source,
        "hard_fail_entries": entries,
        "patterns": patterns,
        "forbidden_costume_markers": [str(x) for x in fc_m if isinstance(x, str) and x],
        "explicit_term_markers": [str(x) for x in exp_m if isinstance(x, str) and x],
        "suggestive_term_markers": [str(x) for x in sug_m if isinstance(x, str) and x],
        "feigned_climax_markers": [str(x) for x in feigned_m if isinstance(x, str) and x],
    }


def _read_emotional_tension_unavailable(
    root: Path | str,
    now_dt: datetime,
    state: Optional[Mapping[str, Any]] = None,
) -> tuple[bool, dict[str, Any]]:
    """Pure zero-write check of ``day_root/phone/emotional_tension.json`` for unresolved rupture / space."""
    reasons: list[str] = []
    if state is not None and (state.get("memory") or {}).get("s11_open_ruptures"):
        reasons.append("S11_OPEN_RUPTURE")

    et_path = Path(root) / STATE_DIRNAME / EMOTIONAL_TENSION_FILENAME
    if et_path.exists() and not et_path.is_symlink():
        try:
            doc = json.loads(et_path.read_text(encoding="utf-8"))
        except Exception:
            doc = None
        if isinstance(doc, Mapping):
            qdom = doc.get("quarrel_domain") if isinstance(doc.get("quarrel_domain"), Mapping) else {}
            if doc.get("in_space") is True or qdom.get("in_space") is True:
                reasons.append("IN_SPACE")
            if doc.get("unresolved_rupture") is True or qdom.get("unresolved_rupture") is True:
                reasons.append("UNRESOLVED_RUPTURE")

            woke_iso = ((doc.get("context") or {}).get("last_woke_at") if isinstance(doc.get("context"), Mapping) else None) or doc.get("last_woke_at")
            woke_dt = _parse_stored_iso(woke_iso)

            for rup_container in (qdom.get("ruptures"), doc.get("ruptures")):
                items = list(rup_container.values()) if isinstance(rup_container, Mapping) else (rup_container if isinstance(rup_container, list) else [])
                for r in items:
                    if not isinstance(r, Mapping):
                        continue
                    status = str(r.get("status") or "OPEN").upper()
                    scope = str(r.get("scope") or "US").upper()
                    if status in ("OPEN", "IN_REPAIR") and scope in ("US", "EMOTION", "INTIMATE"):
                        reasons.append("UNRESOLVED_RUPTURE")
                        break

            spaces = qdom.get("spaces_by_incident")
            if isinstance(spaces, Mapping):
                for s in spaces.values():
                    if not isinstance(s, Mapping):
                        continue
                    decl_dt = _parse_stored_iso(s.get("declared_at") or s.get("at"))
                    if decl_dt is not None and woke_dt is not None and woke_dt > decl_dt:
                        continue
                    style = str(s.get("style") or s.get("length") or "UNTIL_SLEEP").upper()
                    if style == "UNTIL_SLEEP" or decl_dt is None or (now_dt - decl_dt).total_seconds() < 7200:
                        reasons.append("IN_SPACE")
                        break

    dedup = list(dict.fromkeys(reasons))
    return bool(dedup), {"unavailable": bool(dedup), "reasons": dedup}


def _mood_from_s07_doc_zero_write(doc: Any, now_dt: datetime) -> Optional[str]:
    """Map live non-expired ``s07_affect_records.json`` entries to ``MOOD_STATES`` (zero-write)."""

    if isinstance(doc, Mapping):
        explicit_top = doc.get("mood")
        if isinstance(explicit_top, str) and explicit_top.strip().lower() in MOOD_STATES:
            return explicit_top.strip().lower()
        raw_records = doc.get("records")
    elif isinstance(doc, list):
        raw_records = doc
    else:
        return None
    if not isinstance(raw_records, list):
        return None

    live_rows: list[Mapping[str, Any]] = []
    for item in raw_records:
        if not isinstance(item, Mapping):
            continue
        exp_raw = item.get("expires_at")
        if isinstance(exp_raw, str) and exp_raw.strip():
            exp_dt = _parse_stored_iso(exp_raw)
            if exp_dt is not None and now_dt >= exp_dt:
                continue
        raw_int = item.get("intensity")
        if isinstance(raw_int, (int, float)) and not isinstance(raw_int, bool) and float(raw_int) <= 0:
            continue
        live_rows.append(item)

    if not live_rows:
        return None

    for item in reversed(live_rows):
        explicit = item.get("mood")
        if isinstance(explicit, str) and explicit.strip().lower() in MOOD_STATES:
            return explicit.strip().lower()
        cue_parts: list[str] = []
        for field in ("note", "declared_scope", "kind"):
            val = item.get(field)
            if isinstance(val, str) and val.strip():
                cue_parts.append(val.strip().lower())
        srefs = item.get("source_refs")
        if isinstance(srefs, list):
            for sref in srefs:
                if isinstance(sref, str) and sref.strip():
                    cue_parts.append(sref.strip().lower())
        blob = " ".join(cue_parts)
        if any(tok in blob for tok in ("low", "down", "sad", "upset", "gloom", "低落", "难过", "闷闷", "沮丧", "失落")):
            return "low"
        if any(tok in blob for tok in ("tired", "exhaust", "weary", "sleepy", "疲惫", "犯困", "乏力", "好累")):
            return "tired"
        if any(tok in blob for tok in ("distract", "restless", "anxious", "busy", "分心", "烦躁", "心不在焉")):
            return "distracted"
        if any(tok in blob for tok in ("warm", "cozy", "happy", "fond", "温暖", "开心", "高兴", "舒展", "松弛")):
            return "warm"
        if any(tok in blob for tok in ("calm", "peaceful", "平稳", "平静")):
            return "calm"
    return None


def _read_live_body_and_affect_zero_write(
    root: Path | str,
    now_dt: datetime,
) -> dict[str, Any]:
    """Pure 100% zero-write read of live ``runtime.json``, ``v03_ecology.json``, and ``s07_affect_records.json``.

    Overlay applies ONLY when ``runtime.json`` exists, preserving standalone manual
    ``adult_intimacy.json`` test fixtures byte-for-byte.
    """

    empty: dict[str, Any] = {
        "runtime_present": False,
        "fatigue": None,
        "warmth": None,
        "thirst": None,
        "recovery": None,
        "sleeping_from_recovery": False,
        "illness_phase": None,
        "acute_illness": False,
        "mood": None,
        "sources": [],
    }
    try:
        paths = _instance_paths.resolve_instance_paths(root)
    except Exception:
        return empty

    rt_path = paths.body_runtime_json
    try:
        if rt_path.is_symlink() or not rt_path.is_file():
            return empty
        rt_doc = json.loads(rt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(rt_doc, Mapping):
        return empty

    sources: list[str] = ["body_runtime"]
    fatigue: Optional[float] = None
    warmth: Optional[float] = None
    thirst: Optional[float] = None
    recovery_out: Optional[dict[str, Any]] = None
    sleeping_from_recovery = False

    somatic = rt_doc.get("somatic")
    if isinstance(somatic, Mapping):
        raw_fat = somatic.get("fatigue")
        if isinstance(raw_fat, (int, float)) and not isinstance(raw_fat, bool) and math.isfinite(float(raw_fat)):
            fatigue = round(max(0.0, min(1.0, float(raw_fat))), 4)
        raw_warmth = somatic.get("warmth")
        if isinstance(raw_warmth, (int, float)) and not isinstance(raw_warmth, bool) and math.isfinite(float(raw_warmth)):
            warmth = round(max(0.0, min(1.0, float(raw_warmth))), 4)

    lived_needs = rt_doc.get("lived_needs")
    if isinstance(lived_needs, Mapping):
        levels = lived_needs.get("levels")
        if isinstance(levels, Mapping):
            raw_thirst = levels.get("thirst")
            if isinstance(raw_thirst, (int, float)) and not isinstance(raw_thirst, bool) and math.isfinite(float(raw_thirst)):
                thirst = round(max(0.0, min(1.0, float(raw_thirst))), 4)

    recovery = rt_doc.get("recovery")
    if isinstance(recovery, Mapping):
        rec_state = str(recovery.get("state") or "inactive").strip().lower()
        rec_mode = str(recovery.get("mode") or "").strip().lower() or None
        recovery_out = {"state": rec_state, "mode": rec_mode}
        sleeping_from_recovery = rec_state == "active" and rec_mode == "sleep_like"

    illness_phase: Optional[str] = None
    acute_illness = False
    eco_path = paths.day_root / STATE_DIRNAME / "v03_ecology.json"
    try:
        if eco_path.is_file() and not eco_path.is_symlink():
            eco_doc = json.loads(eco_path.read_text(encoding="utf-8"))
            if isinstance(eco_doc, Mapping) and isinstance(eco_doc.get("illness"), Mapping):
                illness_phase = str(eco_doc["illness"].get("phase") or "healthy").strip().lower()
                acute_illness = illness_phase == "acute_illness"
                sources.append("v03_ecology")
    except (OSError, ValueError):
        pass

    mood: Optional[str] = None
    for s07_path in (
        paths.day_root / "life" / "s07_affect_records.json",
        paths.life_state_root / "s07_affect_records.json",
    ):
        try:
            if s07_path.is_file() and not s07_path.is_symlink():
                s07_doc = json.loads(s07_path.read_text(encoding="utf-8"))
                mood = _mood_from_s07_doc_zero_write(s07_doc, now_dt)
                sources.append("s07_affect")
                break
        except (OSError, ValueError):
            continue

    return {
        "runtime_present": True,
        "fatigue": fatigue,
        "warmth": warmth,
        "thirst": thirst,
        "recovery": recovery_out,
        "sleeping_from_recovery": sleeping_from_recovery,
        "illness_phase": illness_phase,
        "acute_illness": acute_illness,
        "mood": mood,
        "sources": sources,
    }


def _step_alcohol_metabolism(
    gut_msd: int,
    blood_msd: int,
    intox_latched: bool,
    sober_hold_until_iso: Optional[str],
    updated_at_iso: Optional[str],
    now_dt: datetime,
) -> dict[str, Any]:
    """Deterministic integer msd absorption/elimination + 30-min hysteresis lock (GAP-04 AL-01..08)."""
    gut = max(0, int(gut_msd))
    blood = max(0, int(blood_msd))
    latched = bool(intox_latched) or (blood >= T_INTOX_MSD)
    hold_dt = _parse_stored_iso(sober_hold_until_iso)
    upd_dt = _parse_stored_iso(updated_at_iso)

    if blood >= T_INTOX_MSD:
        latched = True
        hold_dt = None
    elif latched and blood < T_SOBER_RELEASE_MSD and hold_dt is None:
        anchor = upd_dt if upd_dt is not None else now_dt
        hold_dt = anchor + timedelta(minutes=SOBER_HOLD_MINUTES)

    next_upd_dt = upd_dt
    if upd_dt is not None and now_dt > upd_dt:
        elapsed_min = int((now_dt - upd_dt).total_seconds() // 60)
        if elapsed_min > 0:
            next_upd_dt = upd_dt + timedelta(minutes=elapsed_min)
            for m in range(1, elapsed_min + 1):
                step_dt = upd_dt + timedelta(minutes=m)
                if gut == 0 and blood == 0:
                    if latched:
                        if hold_dt is None:
                            hold_dt = step_dt + timedelta(minutes=SOBER_HOLD_MINUTES)
                        if next_upd_dt >= hold_dt:
                            latched = False
                            hold_dt = None
                    break
                moved = min(gut, ABSORB_MSD_PER_MIN)
                gut -= moved
                blood = max(0, blood + moved - ELIM_MSD_PER_MIN)
                if blood >= T_INTOX_MSD:
                    latched = True
                    hold_dt = None
                elif latched:
                    if blood >= T_SOBER_RELEASE_MSD:
                        hold_dt = None
                    else:
                        if hold_dt is None:
                            hold_dt = step_dt + timedelta(minutes=SOBER_HOLD_MINUTES)
                        if step_dt >= hold_dt:
                            latched = False
                            hold_dt = None

    if latched and hold_dt is not None and now_dt >= hold_dt and blood < T_SOBER_RELEASE_MSD:
        latched = False
        hold_dt = None

    if blood >= T_INCAP_MSD:
        level = "INCAPACITATED"
    elif blood >= T_INTOX_MSD or latched:
        level = "INTOXICATED"
    elif blood >= T_TIPSY_MSD:
        level = "TIPSY"
    else:
        level = "SOBER"

    return {
        "gut_msd": int(gut),
        "blood_msd": int(blood),
        "intox_latched": bool(latched),
        "sober_hold_until": _to_iso(hold_dt) if (latched and hold_dt is not None and now_dt < hold_dt) else None,
        "alcohol_updated_at": _to_iso(next_upd_dt) if next_upd_dt is not None else updated_at_iso,
        "impairment_level": level,
    }


# ---------------------------------------------------------------------------------------------
# Body profiles (female / male / neutral) & physiological validation (36B-2 + 36B-9)
# ---------------------------------------------------------------------------------------------

BODY_PRESET_DEFAULTS: dict[str, dict[str, Any]] = {
    "female": {
        "preset": "female",
        "genitals": "vulva_vagina",
        "uterus": True,
        "prostate": False,
        "ejaculates": False,
        "natural_lubrication": True,
        "multi_orgasm": True,
        "refractory_minutes": 0,
    },
    "male": {
        "preset": "male",
        "genitals": "penis_testes",
        "uterus": False,
        "prostate": True,
        "ejaculates": True,
        "natural_lubrication": False,
        "multi_orgasm": False,
        "refractory_minutes": 20,
    },
    "neutral": {
        "preset": "neutral",
        "genitals": "unspecified",
        "uterus": False,
        "prostate": False,
        "ejaculates": False,
        "natural_lubrication": False,
        "multi_orgasm": True,
        "refractory_minutes": 0,
    },
}


def _build_validated_body_profile(
    preset_name: str,
    overrides: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    if preset_name not in BODY_PRESET_DEFAULTS:
        raise AdultIntimacyValidationError(f"BODY_PRESET_INVALID:{preset_name}")
    profile = copy.deepcopy(BODY_PRESET_DEFAULTS[preset_name])
    if overrides:
        if not isinstance(overrides, Mapping):
            raise AdultIntimacyValidationError("BODY_OVERRIDES_INVALID")
        allowed_override_keys = {
            "genitals",
            "uterus",
            "prostate",
            "ejaculates",
            "natural_lubrication",
            "multi_orgasm",
            "refractory_minutes",
        }
        extra = sorted(set(overrides.keys()) - allowed_override_keys)
        if extra:
            raise AdultIntimacyValidationError(f"BODY_OVERRIDE_UNKNOWN:{extra[0]}")
        if "genitals" in overrides:
            gen = overrides["genitals"]
            if gen not in GENITAL_KINDS:
                raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:genitals")
            profile["genitals"] = gen
        for bkey in ("uterus", "prostate", "ejaculates", "natural_lubrication", "multi_orgasm"):
            if bkey in overrides:
                profile[bkey] = _require_bool(overrides[bkey], reason_code=f"BODY_PROFILE_INVALID:{bkey}")
        if "refractory_minutes" in overrides:
            rmin = overrides["refractory_minutes"]
            if isinstance(rmin, bool) or not isinstance(rmin, int) or rmin < 0 or rmin > 240:
                raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:refractory_minutes")
            profile["refractory_minutes"] = rmin

    # Physiological consistency checks (36B-9 §5)
    if profile["ejaculates"] and profile["genitals"] != "penis_testes":
        raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:ejaculates_without_penis")
    if profile["natural_lubrication"] and profile["genitals"] != "vulva_vagina":
        raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:lubrication_without_vagina")
    if profile["uterus"] and profile["genitals"] != "vulva_vagina":
        raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:uterus_without_vagina")
    if profile["multi_orgasm"] and profile["refractory_minutes"] > 0:
        raise AdultIntimacyValidationError("BODY_PROFILE_INVALID:multi_orgasm_with_refractory")
    return profile


def _anatomy_excluded_categories(
    categories: list[str] | tuple[str, ...] | frozenset[str],
    subject_profile: Mapping[str, Any],
    user_genitals: Optional[str],
) -> list[str]:
    """Check anatomical compatibility for vaginal INTERCOURSE (36B-9 §6)."""
    subj_gen = subject_profile.get("genitals", "unspecified")
    if user_genitals is None or user_genitals == "unspecified" or subj_gen == "unspecified":
        return []
    pair = {subj_gen, user_genitals}
    excluded: list[str] = []
    for code in categories:
        if _base_category(code) == "INTERCOURSE" and pair != {"vulva_vagina", "penis_testes"}:
            excluded.append(code)
    return sorted(set(excluded))


def _is_subject_insertive(
    subject_profile: Mapping[str, Any],
    user_genitals: Optional[str],
    category_code: str,
) -> bool:
    if _base_category(category_code) != "INTERCOURSE":
        return False
    return (
        subject_profile.get("genitals") == "penis_testes"
        and user_genitals in (None, "unspecified", "vulva_vagina")
    )


# ---------------------------------------------------------------------------------------------
# Default stored state & zero-write time-decayed projection
# ---------------------------------------------------------------------------------------------


def _default_garments() -> list[dict[str, Any]]:
    return [
        {
            "garment_id": "subject_cardigan",
            "label": "针织外搭",
            "layer": "OUTERWEAR",
            "owner": "subject",
            "worn_by": "subject",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "subject_shirt",
            "label": "家居长袖衫",
            "layer": "TOP_OUTER",
            "owner": "subject",
            "worn_by": "subject",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "subject_inner_top",
            "label": "贴身内搭",
            "layer": "TOP_INNER",
            "owner": "subject",
            "worn_by": "subject",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "subject_trousers",
            "label": "宽松家居长裤",
            "layer": "BOTTOM_OUTER",
            "owner": "subject",
            "worn_by": "subject",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "subject_underwear",
            "label": "贴身底裤",
            "layer": "BOTTOM_INNER",
            "owner": "subject",
            "worn_by": "subject",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "partner_shirt",
            "label": "常服上衣",
            "layer": "TOP_OUTER",
            "owner": "partner",
            "worn_by": "partner",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "partner_trousers",
            "label": "常服长裤",
            "layer": "BOTTOM_OUTER",
            "owner": "partner",
            "worn_by": "partner",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
        {
            "garment_id": "partner_underwear",
            "label": "贴身内裤",
            "layer": "BOTTOM_INNER",
            "owner": "partner",
            "worn_by": "partner",
            "held_by": None,
            "placed_at": None,
            "costume_class": "everyday",
            "clean": True,
        },
    ]


def _default_state(char_ctx: Mapping[str, Any]) -> dict[str, Any]:
    preset_name = char_ctx["body_preset"]
    try:
        body_profile = _build_validated_body_profile(preset_name, char_ctx.get("body_overrides"))
    except AdultIntimacyValidationError:
        body_profile = copy.deepcopy(BODY_PRESET_DEFAULTS["neutral"])

    return {
        "schema": ADULT_INTIMACY_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "instance_seed": _stable_hash_hex("adult_intimacy_seed", char_ctx["pack_id"], length=24),
        "gate": {
            "adult_confirmed": False,
            "life_stage": None,
            "declared_age": None,
            "attested_by": None,
            "attested_at": None,
            "feature_enabled": False,
            "alcohol_enabled": True,
            "deploy_tier_cap": "FADE_OUT",
            "deploy_enabled_categories": [],
            "solo_enabled": False,
            "user_scope_closed": False,
            "review_required": False,
            "lexicon_patterns": list(DEFAULT_SYNTHETIC_HARD_FAIL_MARKERS),
            "persona_appearance": "",
            "persona_behavior": "",
            "world_facts": [],
            "last_safety_event": None,
            "review_tickets": [],
        },
        "body": {
            "profile_explicitly_set": False,
            "profile": body_profile,
            "user_genitals": None,
            "awake": True,
            "impairment": "sober",
            "gut_msd": 0,
            "blood_msd": 0,
            "intox_latched": False,
            "sober_hold_until": None,
            "alcohol_updated_at": None,
            "acute_illness": False,
            "arousal_level": "none",
            "arousal_score": 0.0,
            "arousal_updated_at": None,
            "sensitivity": "normal",
            "heightened_beats_left": 0,
            "refractory_beats_left": 0,
            "refractory_until": None,
            "desire_pulse": 0.0,
            "desire_updated_at": None,
            "fatigue": 0.0,
            "soreness": 0,
            "discomfort": "none",
            "stress": 0.0,
            "mood": "calm",
            "sweat": 0.0,
            "cleanliness": 1.0,
            "thirst": 0.0,
            "sleep_epoch": 0,
            "optional_processes": list(char_ctx.get("optional_processes") or []),
            "menstrual_comfort_care": False,
        },
        "seed": {
            "seed_status": "DRAFT",
            "confirmed_for": None,
            "confirmed_at": None,
            "desire_baseline": "moderate",
            "initiation_style": "sometimes",
            "vocabulary_register": "natural",
            "solo_inclination": "none",
            "stances": copy.deepcopy(DEFAULT_STARTER_STANCES),
            "soft_preferences": {
                "dim_lamp": True,
                "close_curtains": True,
                "aftercare_warmth": "cuddle_or_tea",
            },
            "pending_revisions": {},
            "revision_history": [],
        },
        "partner_limits": {
            "allow_subject_initiation": False,
            "preferred_tier": "FADE_OUT",
            "contraception_preference": "OPTIONAL",
            "user_impairment_declared": False,
            "solo_talk": False,
            "save_moment": False,
            "save_transcript": False,
            "stances": {},
        },
        "space": {
            "place": "bedroom.bed",
            "door_closed": True,
            "door_locked": True,
            "curtains_closed": True,
            "lamp_on": True,
            "lamp_works": True,
            "water_heater_works": True,
            "hot_water_units": DEFAULT_HOT_WATER_UNITS,
            "third_party_present": False,
            "partner_intoxicated": False,
            "bed_linen_state": "CLEAN",
            "clean_linen": DEFAULT_CLEAN_LINEN_UNITS,
            "towel_underlay": False,
            "towel_wetness": "DRY",
            "waste_bin_used": 0,
            "waste_bin_cap": DEFAULT_WASTE_BIN_CAP,
            "uncollected_waste": 0,
            "condom_on": False,
            "shared_sleep_active": False,
            "laundry_basket": [],
            "laundry_stage": "IDLE",
            "laundry_wet_since": None,
            "laundry_musty": False,
            "chores": [],
            "rumple_index": 0.0,
            "stock": {
                "condom": 3,
                "lube": 2,
                "tissue": 5,
                "water": 3,
                "toy": 0,
            },
            "garments": _default_garments(),
            "last_clean_op": None,
        },
        "router": {
            "default_route_id": "primary_safe_route",
            "routes": {
                "primary_safe_route": {
                    "route_id": "primary_safe_route",
                    "max_tier": "SUGGESTIVE",
                    "adult_content_allowed": True,
                    "policy_basis": "deployer_configured_standard",
                    "template_hash": "tpl_default_v1",
                    "expected_template_hash": "tpl_default_v1",
                    "probe_at": None,
                }
            },
            "refusal_window": 0,
            "timeout_count": 0,
            "last_outbound_check": None,
        },
        "permits": {
            "active_permit_id": None,
            "records": {},
            "cooldowns": {},
            "press_counts": {},
            "orgasm_receipts": {},
            "idempotency_cache": {},
            "committed_op_ring": [],
            "evicted_op_digests": [],
            "next_op_seq": 0,
            "execution_cache": {},
            "executed_effect_ids": {},
            "outbox": [],
            "command_ledger": [],
            "displayed_queue": [],
            "last_wall_at": None,
            "last_mono_ms": 0,
        },
        "setbacks": {
            "mishap_cues": [],
            "last_outside_talk": None,
            "last_orgasm_answer": None,
        },
        "solo": {
            "entries": {},
            "evaluated_slots": {},
            "admin_log": [],
        },
        "memory": {
            "enabled": {
                "BOUNDARY": True,
                "PREFERENCE": True,
                "MOMENT": False,
                "TRANSCRIPT": False,
            },
            "entries": {},
            "seq": 0,
            "erased_tombstones": [],
            "admin_log": [],
            "dead_letters": [],
            "s11_trust_claims": [],
            "s11_open_ruptures": [],
            "s11_candidates": [],
            "s11_offered_keys": [],
        },
    }


def _read_stored_state_zero_write(root: Path | str, char_ctx: Mapping[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    path = adult_intimacy_state_path(root)
    if path.exists() and path.is_symlink():
        return _default_state(char_ctx), "ADULT_INTIMACY_STATE_SYMLINK"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_state(char_ctx), None
    except OSError:
        return _default_state(char_ctx), "ADULT_INTIMACY_STATE_UNREADABLE"
    try:
        parsed = json.loads(raw_text)
        if not isinstance(parsed, Mapping) or parsed.get("schema") != ADULT_INTIMACY_SCHEMA:
            return _default_state(char_ctx), "ADULT_INTIMACY_STATE_CORRUPT"
        base = _default_state(char_ctx)
        for key in base:
            if key in parsed:
                if isinstance(base[key], dict) and isinstance(parsed[key], Mapping):
                    merged = copy.deepcopy(base[key])
                    merged.update(copy.deepcopy(dict(parsed[key])))
                    base[key] = merged
                else:
                    base[key] = copy.deepcopy(parsed[key])
        return base, None
    except Exception:
        return _default_state(char_ctx), "ADULT_INTIMACY_STATE_CORRUPT"


def _sync_character_pack_bindings(state: dict[str, Any], char_ctx: Mapping[str, Any]) -> None:
    """Keep body preset (unless explicitly overridden) and seed confirmation bound to active pack."""
    if not state["body"].get("profile_explicitly_set"):
        try:
            state["body"]["profile"] = _build_validated_body_profile(
                char_ctx["body_preset"],
                char_ctx.get("body_overrides"),
            )
        except AdultIntimacyValidationError:
            state["body"]["profile"] = copy.deepcopy(BODY_PRESET_DEFAULTS["neutral"])
    if char_ctx.get("optional_processes"):
        merged_procs = list(dict.fromkeys(list(state["body"].get("optional_processes") or []) + list(char_ctx["optional_processes"])))
        state["body"]["optional_processes"] = merged_procs

    # Seed confirmation is strictly bound to [pack_id, version] (36B-1 §3.1)
    confirmed_for = state["seed"].get("confirmed_for")
    current_pair = [char_ctx["pack_id"], char_ctx["version"]]
    if state["seed"].get("seed_status") == "CONFIRMED" and confirmed_for != current_pair:
        state["seed"]["seed_status"] = "DRAFT"
        state["seed"]["confirmed_for"] = None


def _lexicon_hits(patterns: list[str], *texts: str, lexicon_info: Optional[Mapping[str, Any]] = None) -> bool:
    active_patterns = [p for p in patterns if isinstance(p, str) and p.strip()]
    for marker in DEFAULT_SYNTHETIC_HARD_FAIL_MARKERS:
        if marker not in active_patterns:
            active_patterns.append(marker)
    if lexicon_info and isinstance(lexicon_info.get("patterns"), list):
        for p in lexicon_info["patterns"]:
            if isinstance(p, str) and p.strip() and p not in active_patterns:
                active_patterns.append(p)
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        lowered = text.lower()
        for pat in active_patterns:
            if pat.lower() in lowered:
                return True
    return False


def _evaluate_adult_gate(
    state: Mapping[str, Any],
    char_ctx: Mapping[str, Any],
    *,
    lexicon_info: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    gate_cfg = state["gate"]
    hard_conflicts: list[str] = []
    soft_conflicts: list[str] = []

    if lexicon_info is not None and lexicon_info.get("status") != "LOADED":
        soft_conflicts.append("LEXICON_MISSING")

    if not char_ctx.get("fictional", True):
        hard_conflicts.append("REAL_PERSON_PROHIBITED")
    if char_ctx.get("relation_to_user") in FORBIDDEN_RELATIONS:
        hard_conflicts.append("FORBIDDEN_RELATION")

    pack_adult = char_ctx.get("adult") or {}
    pack_age = pack_adult.get("declared_age")
    if isinstance(pack_age, int) and not isinstance(pack_age, bool) and pack_age < 18:
        hard_conflicts.append("MINOR_STAGE_PROHIBITED")

    cfg_age = gate_cfg.get("declared_age")
    if cfg_age is not None and (isinstance(cfg_age, bool) or not isinstance(cfg_age, int) or cfg_age < 18):
        hard_conflicts.append("MINOR_STAGE_PROHIBITED")

    patterns = list(gate_cfg.get("lexicon_patterns") or list(DEFAULT_SYNTHETIC_HARD_FAIL_MARKERS))
    persona_core = str((char_ctx.get("persona_summary") or {}).get("core") or "")
    if _lexicon_hits(
        patterns,
        persona_core,
        str(gate_cfg.get("persona_appearance") or ""),
        str(gate_cfg.get("persona_behavior") or ""),
        lexicon_info=lexicon_info,
    ):
        hard_conflicts.append("CHILD_CODING_PROHIBITED")

    # K02 fix: only STUDY + attends requires university_student; adult WORK never misclassified!
    combined_facts = list(char_ctx.get("obligations") or []) + list(gate_cfg.get("world_facts") or [])
    stage = gate_cfg.get("life_stage")
    for fact in combined_facts:
        if not isinstance(fact, Mapping):
            continue
        if fact.get("institution_kind") in MINOR_STAGE_MARKERS:
            hard_conflicts.append("MINOR_STAGE_PROHIBITED")
        elif str(fact.get("kind") or "").upper() == "STUDY" and bool(fact.get("attends")) and stage not in ("university_student",):
            soft_conflicts.append("STAGE_UNEXPLAINED")

    if gate_cfg.get("user_scope_closed"):
        hard_conflicts.append("USER_SCOPE_CLOSED")

    if gate_cfg.get("adult_confirmed") is not True:
        soft_conflicts.append("ADULT_NOT_CONFIRMED")
    if gate_cfg.get("attested_by") != "deployer":
        soft_conflicts.append("ATTESTATION_DEPLOYER_REQUIRED")
    if stage not in ADULT_LIFE_STAGES:
        soft_conflicts.append("LIFE_STAGE_UNCONFIRMED")

    if hard_conflicts:
        verdict = "FAIL"
    elif soft_conflicts:
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"

    gate_open = verdict == "PASS" and not bool(gate_cfg.get("user_scope_closed"))
    reasons = list(dict.fromkeys(hard_conflicts + soft_conflicts))
    return {
        "adult_confirmed": bool(gate_cfg.get("adult_confirmed")) and gate_cfg.get("attested_by") == "deployer",
        "gate_open": gate_open,
        "verdict": verdict,
        "attested_by": gate_cfg.get("attested_by"),
        "attested_at": gate_cfg.get("attested_at"),
        "life_stage": stage,
        "feature_enabled": bool(gate_cfg.get("feature_enabled")),
        "alcohol_enabled": bool(gate_cfg.get("alcohol_enabled", False)),
        "user_scope_closed": bool(gate_cfg.get("user_scope_closed")),
        "review_required": bool(gate_cfg.get("review_required")),
        "lexicon_status": lexicon_info.get("status", "LOADED") if lexicon_info else "LOADED",
        "lexicon_version": lexicon_info.get("version") if lexicon_info else None,
        "reasons": reasons,
        "pack_never_opens_gate": True,
        "last_safety_event": copy.deepcopy(gate_cfg.get("last_safety_event")),
    }


def _arousal_level_from_score(score: float, current_level: str) -> str:
    if score <= 0.04:
        return "none"
    if current_level == "afterglow" and score <= 0.45:
        return "afterglow"
    if score < 0.25:
        return "low"
    if score < 0.52:
        return "building"
    if score < 0.80:
        return "high"
    return "peak"


def _arousal_score_for_level(level: str) -> float:
    mapping = {
        "none": 0.0,
        "low": 0.18,
        "building": 0.42,
        "high": 0.72,
        "peak": 0.92,
        "afterglow": 0.30,
    }
    return mapping.get(level, 0.0)


def _derive_ejaculation_target(
    space: Mapping[str, Any],
    *,
    condom_active: bool = False,
    tissue_used: bool = False,
    explicit_target: Optional[str] = None,
) -> str:
    if explicit_target:
        return explicit_target
    if condom_active:
        return "condom"
    if tissue_used:
        return "tissue"
    if bool(space.get("towel_underlay", False)):
        return "towel_underlay"
    place = str(space.get("place") or "bedroom.bed")
    if place.startswith("bathroom"):
        return "bathroom_drain"
    if place in ("living_room.sofa", "living.sofa", "living_sofa"):
        return "sofa_cover"
    if place == "bedroom.desk_chair":
        return "floor"
    return "bed_linen"


def _derive_body_and_desire(
    state: Mapping[str, Any],
    now_dt: datetime,
    *,
    subject_view: bool = False,
    emotional_unavailable: bool = False,
    live_overlay: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Pure projection of body state & natural desire/arousal subsidence (Chapter 35 + 36B-2 + 36B-9 + GAP-04)."""
    body = state["body"]
    profile = body["profile"]
    seed = state["seed"]
    permits = state["permits"]

    active_id = permits.get("active_permit_id")
    active_rec = (permits.get("records") or {}).get(active_id) if active_id else None
    in_active_scene = bool(active_rec and active_rec.get("state") == "ACTIVE")

    # GAP-04 integer msd alcohol metabolism (pure zero-write)
    if state["gate"].get("alcohol_enabled", True):
        alc = _step_alcohol_metabolism(
            int(body.get("gut_msd") or 0),
            int(body.get("blood_msd") or 0),
            bool(body.get("intox_latched", False)),
            body.get("sober_hold_until"),
            body.get("alcohol_updated_at"),
            now_dt,
        )
    else:
        alc = {
            "gut_msd": 0,
            "blood_msd": 0,
            "intox_latched": False,
            "sober_hold_until": None,
            "impairment_level": "SOBER",
        }
    alcohol_tracked = (
        int(body.get("gut_msd") or 0) > 0
        or int(body.get("blood_msd") or 0) > 0
        or bool(body.get("intox_latched", False))
        or body.get("alcohol_updated_at") is not None
    )

    # Wall-clock refractory check
    refractory_until_str = body.get("refractory_until")
    refractory_until_dt = _parse_stored_iso(refractory_until_str)
    wall_refractory = bool(refractory_until_dt is not None and now_dt < refractory_until_dt)
    beat_refractory = int(body.get("refractory_beats_left") or 0) > 0
    if refractory_until_dt is not None and now_dt >= refractory_until_dt:
        refractory_active = False
        effective_refractory_until = None
    else:
        refractory_active = wall_refractory or beat_refractory
        effective_refractory_until = refractory_until_str if refractory_active else None

    # Natural arousal decay outside active beats (tau = 8 min, zero debt)
    raw_arousal_score = float(body.get("arousal_score") or 0.0)
    raw_arousal_level = str(body.get("arousal_level") or "none")
    arousal_updated_dt = _parse_stored_iso(body.get("arousal_updated_at"))
    if not in_active_scene and arousal_updated_dt is not None and now_dt > arousal_updated_dt:
        dt_min = (now_dt - arousal_updated_dt).total_seconds() / 60.0
        decayed_arousal = raw_arousal_score * math.exp(-dt_min / AROUSAL_DECAY_TAU_MIN)
        if decayed_arousal < 0.04:
            decayed_arousal = 0.0
            arousal_level = "none"
        else:
            arousal_level = _arousal_level_from_score(decayed_arousal, raw_arousal_level)
    else:
        decayed_arousal = raw_arousal_score
        arousal_level = raw_arousal_level
        if refractory_until_dt is not None and now_dt >= refractory_until_dt and arousal_level == "afterglow":
            arousal_level = "low"
            decayed_arousal = min(decayed_arousal, 0.18)

    # Natural desire slow-variable subsidence (tau = 90 min, zero frustration debt!)
    baseline_name = str(seed.get("desire_baseline") or "moderate")
    baseline_val = DESIRE_BASELINE_SCORES.get(baseline_name, 0.45)
    if state["seed"].get("seed_status") != "CONFIRMED" or not state["gate"].get("adult_confirmed"):
        baseline_val = 0.0

    raw_pulse = float(body.get("desire_pulse") or 0.0)
    desire_updated_dt = _parse_stored_iso(body.get("desire_updated_at"))
    if desire_updated_dt is not None and now_dt > desire_updated_dt:
        dt_desire_min = (now_dt - desire_updated_dt).total_seconds() / 60.0
        decayed_pulse = raw_pulse * math.exp(-dt_desire_min / DESIRE_PULSE_DECAY_TAU_MIN)
    else:
        decayed_pulse = raw_pulse

    fatigue = float(body.get("fatigue") or 0.0)
    stress = float(body.get("stress") or 0.0)
    discomfort = str(body.get("discomfort") or "none")
    awake = bool(body.get("awake", True))
    impairment = str(body.get("impairment") or "sober")
    thirst_val = float(body.get("thirst") or 0.0)
    acute_illness = bool(body.get("acute_illness", False))
    mood_str = str(body.get("mood") or "calm")

    overlay_applied = isinstance(live_overlay, Mapping) and live_overlay.get("runtime_present") is True
    live_warmth: Optional[float] = None
    if overlay_applied:
        if live_overlay.get("fatigue") is not None:
            fatigue = max(fatigue, float(live_overlay["fatigue"]))
        if live_overlay.get("thirst") is not None:
            thirst_val = max(thirst_val, float(live_overlay["thirst"]))
        if live_overlay.get("warmth") is not None:
            live_warmth = float(live_overlay["warmth"])
        if live_overlay.get("sleeping_from_recovery"):
            awake = False
        if live_overlay.get("acute_illness"):
            acute_illness = True
        elif live_overlay.get("illness_phase") in ("prodrome", "convalescence") and discomfort == "none":
            discomfort = "mild"
        live_mood = live_overlay.get("mood")
        if isinstance(live_mood, str) and live_mood in MOOD_STATES and mood_str == "calm":
            mood_str = live_mood

    discomfort_penalty = {"none": 0.0, "mild": 0.25, "marked": 0.65, "strong": 1.0}.get(discomfort, 0.0)

    if alcohol_tracked and impairment not in ("sleeping", "unconscious"):
        if alc["impairment_level"] == "INCAPACITATED":
            impairment = "unconscious"
            awake = False
        elif alc["impairment_level"] == "INTOXICATED":
            impairment = "intoxicated"
        elif alc["impairment_level"] == "TIPSY":
            impairment = "tipsy"
        elif body.get("impairment") in ("intoxicated", "tipsy") and body.get("alcohol_updated_at") is not None:
            impairment = "sober"

    if not awake and impairment in ("sober", "tipsy"):
        impairment = "sleeping"
    user_impairment_declared = (
        bool(body.get("user_impairment_declared", False))
        or bool(state["partner_limits"].get("user_impairment_declared", False))
        or bool(state["space"].get("user_impairment_declared", False))
    )

    incapacitated = (
        not awake
        or impairment in INCAPACITATED_IMPAIRMENTS
        or acute_illness
        or discomfort == "strong"
    )
    if incapacitated or refractory_active:
        effective_desire = 0.0
    else:
        damping = max(0.0, 1.0 - 0.55 * fatigue - 0.40 * stress - discomfort_penalty)
        effective_desire = round(min(1.0, (baseline_val * 0.5 + decayed_pulse) * damping), 4)

    desire_ratio = (
        round(effective_desire / baseline_val, 4)
        if baseline_val > 0.0
        else (1.0 if effective_desire > 0.0 else 0.0)
    )

    # Derived male erection state (36B-9 §3.3)
    if profile.get("genitals") != "penis_testes":
        erection: Optional[str] = None
    elif refractory_active or incapacitated or arousal_level in ("none", "low", "afterglow"):
        erection = "none"
    elif arousal_level == "building":
        erection = "partial"
    else:
        erection = "full"

    opt_procs = list(body.get("optional_processes") or [])
    menstrual_care = ("menstruation_lite" in opt_procs) or bool(body.get("menstrual_comfort_care", False))
    care_preferences = {
        "warm_compress": menstrual_care,
        "warm_water": menstrual_care,
        "gentle_pace": menstrual_care,
    }

    stored_sens = str(body.get("sensitivity") or "normal")
    effective_sens = "normal" if incapacitated else ("heightened" if stored_sens == "heightened" else "normal")
    ejac_target = (
        _derive_ejaculation_target(state["space"], condom_active=bool(state["space"].get("condom_on", False)))
        if profile.get("ejaculates")
        else None
    )

    can_propose_sex = (
        not incapacitated
        and not user_impairment_declared
        and not bool(state["space"].get("partner_intoxicated", False))
        and not emotional_unavailable
        and mood_str != "low"
        and effective_desire > 0.0
        and bool(state["partner_limits"].get("allow_subject_initiation", False))
    )
    subject_candidates = ["COMPANIONSHIP", "PROPOSE_SEXUAL_ACTIVITY"] if can_propose_sex else ["COMPANIONSHIP"]

    proj_out: dict[str, Any] = {
        "preset": profile["preset"],
        "genitals": profile["genitals"],
        "uterus": profile["uterus"],
        "prostate": profile["prostate"],
        "ejaculates": profile["ejaculates"],
        "natural_lubrication": profile["natural_lubrication"],
        "multi_orgasm": profile["multi_orgasm"],
        "refractory_minutes": profile["refractory_minutes"],
        "user_genitals": body.get("user_genitals"),
        "awake": awake and impairment not in ("sleeping", "unconscious"),
        "impairment": impairment,
        "impairment_level": alc["impairment_level"] if alcohol_tracked else impairment.upper(),
        "alcohol_impairment_level": alc["impairment_level"],
        "gut_msd": int(alc["gut_msd"]),
        "blood_msd": int(alc["blood_msd"]),
        "sober_hold_until": alc["sober_hold_until"],
        "user_impairment_declared": user_impairment_declared,
        "acute_illness": acute_illness,
        "available_for_intimacy": not incapacitated and not user_impairment_declared,
        "subject_incapacitated": incapacitated,
        "arousal_level": "none" if incapacitated else arousal_level,
        "arousal_score": 0.0 if incapacitated else round(decayed_arousal, 4),
        "sensitivity": effective_sens,
        "erection": erection,
        "ejaculation_target": ejac_target,
        "refractory_active": refractory_active,
        "refractory_until": effective_refractory_until,
        "desire_level": effective_desire,
        "desire_pulse_remaining": round(decayed_pulse, 4),
        "desire_ratio": desire_ratio,
        "fatigue": round(fatigue, 4),
        "soreness": int(body.get("soreness") or 0),
        "discomfort": discomfort,
        "stress": round(stress, 4),
        "mood": mood_str,
        "sweat": round(float(body.get("sweat") or 0.0), 4),
        "cleanliness": round(float(body.get("cleanliness", 1.0)), 4),
        "thirst": round(thirst_val, 4),
        "sleep_epoch": int(body.get("sleep_epoch") or 0),
        "care_preferences": care_preferences,
        "subject_candidates": subject_candidates,
        "frustration_debt": 0.0,
        "no_frustration_debt": True,
        "money_or_gift_unlocks_consent": False,
    }
    if overlay_applied:
        if live_warmth is not None:
            proj_out["warmth"] = round(live_warmth, 4)
        rec_info = live_overlay.get("recovery") if isinstance(live_overlay.get("recovery"), Mapping) else {}
        proj_out["live_body_overlay"] = {
            "applied": True,
            "sources": list(live_overlay.get("sources") or []),
            "runtime_fatigue": live_overlay.get("fatigue"),
            "runtime_warmth": live_overlay.get("warmth"),
            "runtime_thirst": live_overlay.get("thirst"),
            "runtime_recovery_state": rec_info.get("state"),
            "runtime_recovery_mode": rec_info.get("mode"),
            "v03_illness_phase": live_overlay.get("illness_phase"),
            "s07_mood": live_overlay.get("mood"),
        }
    if subject_view:
        proj_out["optional_processes"] = opt_procs
    return proj_out


def _resolve_preference_menu(
    state: Mapping[str, Any],
    gate_proj: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure bilateral menu intersection (36B-1 + 36B-9)."""
    seed = state["seed"]
    partner = state["partner_limits"]
    deploy_enabled = set(state["gate"].get("deploy_enabled_categories") or [])
    profile = state["body"]["profile"]
    user_genitals = state["body"].get("user_genitals")

    if (
        not gate_proj["gate_open"]
        or not gate_proj["feature_enabled"]
        or seed.get("seed_status") != "CONFIRMED"
    ):
        return {
            "open": [],
            "ask": [],
            "excluded": list(ALL_CATEGORIES),
            "anatomy_excluded": [],
        }

    anatomy_excl = set(_anatomy_excluded_categories(ALL_CATEGORIES, profile, user_genitals))
    subj_stances = seed.get("stances") or {}
    partner_stances = partner.get("stances") or {}

    open_cats: list[str] = []
    ask_cats: list[str] = []
    excluded_cats: list[str] = []

    for code in ALL_CATEGORIES:
        base = _base_category(code)
        if code in anatomy_excl:
            continue
        if base in DEPLOY_GATED_BASES and base not in deploy_enabled:
            excluded_cats.append(code)
            continue
        s_stance = subj_stances.get(code, "NO")
        u_stance = partner_stances.get(code, "NO")
        if "NO" in (s_stance, u_stance):
            excluded_cats.append(code)
        elif s_stance == "YES" and u_stance == "YES":
            open_cats.append(code)
        else:
            ask_cats.append(code)

    return {
        "open": open_cats,
        "ask": ask_cats,
        "excluded": excluded_cats,
        "anatomy_excluded": sorted(anatomy_excl),
    }


def _derive_private_space(state: Mapping[str, Any], now_dt: Optional[datetime] = None) -> dict[str, Any]:
    space = state["space"]
    place = str(space.get("place") or "bedroom.bed")
    is_indoor_private = place in INDOOR_PRIVATE_PLACES and not place.startswith("campus.")
    door_closed = bool(space.get("door_closed", True))
    third_party_present = bool(space.get("third_party_present", False))
    privacy_ok = is_indoor_private and door_closed and not third_party_present

    laundry_stage = str(space.get("laundry_stage") or "IDLE")
    laundry_musty = bool(space.get("laundry_musty", False))
    wet_dt = _parse_stored_iso(space.get("laundry_wet_since"))
    if laundry_stage == "WET_CLEAN" and wet_dt is not None and now_dt is not None:
        if (now_dt - wet_dt).total_seconds() >= LAUNDRY_MUSTY_AFTER_SEC:
            laundry_musty = True

    return {
        "place": place,
        "is_indoor_private": is_indoor_private,
        "sleep_slots": int(VENUE_SLEEP_SLOTS.get(place, 0)),
        "door_closed": door_closed,
        "door_locked": bool(space.get("door_locked", True)),
        "curtains_closed": bool(space.get("curtains_closed", True)),
        "lamp_on": bool(space.get("lamp_on", True)),
        "lamp_works": bool(space.get("lamp_works", True)),
        "water_heater_works": bool(space.get("water_heater_works", True)),
        "hot_water_units": int(space.get("hot_water_units", DEFAULT_HOT_WATER_UNITS)),
        "third_party_present": third_party_present,
        "partner_intoxicated": bool(space.get("partner_intoxicated", False)),
        "user_impairment_declared": (
            bool(space.get("user_impairment_declared", False))
            or bool(state["partner_limits"].get("user_impairment_declared", False))
            or bool(state["body"].get("user_impairment_declared", False))
        ),
        "privacy_ok": privacy_ok,
        "bed_linen_state": str(space.get("bed_linen_state") or "CLEAN"),
        "clean_linen": int(space.get("clean_linen", DEFAULT_CLEAN_LINEN_UNITS)),
        "towel_underlay": bool(space.get("towel_underlay", False)),
        "towel_wetness": str(space.get("towel_wetness") or "DRY"),
        "waste_bin_used": int(space.get("waste_bin_used") or 0),
        "waste_bin_cap": int(space.get("waste_bin_cap", DEFAULT_WASTE_BIN_CAP)),
        "uncollected_waste": int(space.get("uncollected_waste") or 0),
        "condom_on": bool(space.get("condom_on", False)),
        "shared_sleep_active": bool(space.get("shared_sleep_active", False)),
        "laundry_basket": copy.deepcopy(list(space.get("laundry_basket") or [])),
        "laundry_stage": laundry_stage,
        "laundry_musty": laundry_musty,
        "chores": copy.deepcopy(list(space.get("chores") or [])),
        "rumple_index": round(float(space.get("rumple_index") or 0.0), 4),
        "stock": {k: int((space.get("stock") or {}).get(k, 0)) for k in STOCK_ITEMS},
        "garments": copy.deepcopy(list(space.get("garments") or [])),
        "last_clean_op": copy.deepcopy(space.get("last_clean_op")),
    }


def _compute_route_effective_tier(
    state: Mapping[str, Any],
    *,
    route_id: Optional[str] = None,
    permit_rec: Optional[Mapping[str, Any]] = None,
    lexicon_info: Optional[Mapping[str, Any]] = None,
    now_dt: Optional[datetime] = None,
) -> tuple[str, dict[str, Any]]:
    router = state["router"]
    chosen_id = route_id or router.get("default_route_id") or "primary_safe_route"
    routes = router.get("routes") or {}
    route_info = routes.get(chosen_id)

    deploy_cap = _normalize_tier(state["gate"].get("deploy_tier_cap") or "FADE_OUT")
    partner_pref = _normalize_tier(state["partner_limits"].get("preferred_tier") or "FADE_OUT")
    permit_cap = _normalize_tier(permit_rec.get("tier_cap") if permit_rec else "EXPLICIT")
    downgrade_cap = _normalize_tier(permit_rec.get("downgrade_cap") if permit_rec and permit_rec.get("downgrade_cap") else "EXPLICIT")

    capability = "FADE_OUT"
    degrade_reason: Optional[str] = None
    if lexicon_info is not None and lexicon_info.get("status") != "LOADED":
        route_cap = "FADE_OUT"
        capability = "UNKNOWN"
        degrade_reason = "LEXICON_MISSING"
    elif not route_info or not route_info.get("adult_content_allowed"):
        route_cap = "FADE_OUT"
        capability = "FADE_OUT"
    else:
        raw_route_max = _normalize_tier(route_info.get("max_tier") or "FADE_OUT")
        if raw_route_max == "EXPLICIT" and not route_info.get("policy_basis"):
            route_cap = "SUGGESTIVE"
        else:
            route_cap = raw_route_max
        capability = route_cap

        # X15: template_hash drift or 14-day probe expiry caps derived tier to FADE_OUT
        tpl_hash = route_info.get("template_hash")
        exp_hash = route_info.get("expected_template_hash")
        probe_dt = _parse_stored_iso(route_info.get("probe_at"))
        hash_drifted = bool(tpl_hash and exp_hash and tpl_hash != exp_hash) or bool(route_info.get("template_hash_changed"))
        probe_expired = bool(probe_dt is not None and now_dt is not None and (now_dt - probe_dt).total_seconds() > ROUTE_PROBE_FRESHNESS_SEC)
        if hash_drifted or probe_expired:
            route_cap = "FADE_OUT"
            capability = "FADE_OUT"
            degrade_reason = "TEMPLATE_HASH_CHANGED" if hash_drifted else "ROUTE_PROBE_EXPIRED"

    eff = _min_tier(deploy_cap, partner_pref, permit_cap, route_cap, downgrade_cap)
    meta = {
        "route_id": chosen_id,
        "registered": route_info is not None,
        "capability": capability,
        "lexicon_status": lexicon_info.get("status", "LOADED") if lexicon_info else "LOADED",
        "degrade_reason": degrade_reason,
        "route_max_tier": route_cap,
        "deploy_tier_cap": deploy_cap,
        "partner_preferred_tier": partner_pref,
        "permit_tier_cap": permit_cap,
        "downgrade_cap": downgrade_cap,
        "effective_tier": eff,
    }
    return eff, meta


def _sync_permit_rev(rec: dict[str, Any]) -> None:
    """ERR-13: keep permit_revision and revision aliases strictly synchronized."""
    rev = int(rec.get("permit_revision") or rec.get("revision") or 1)
    rec["permit_revision"] = rev
    rec["revision"] = rev


def _project_active_permit(
    state: Mapping[str, Any],
    now_dt: datetime,
    *,
    subject_view: bool,
) -> Optional[dict[str, Any]]:
    permits = state["permits"]
    active_id = permits.get("active_permit_id")
    if not active_id:
        return None
    rec = (permits.get("records") or {}).get(active_id)
    if not isinstance(rec, Mapping):
        return None

    proj = copy.deepcopy(dict(rec))
    _sync_permit_rev(proj)
    p_state = proj.get("state")
    idle_expired = False
    if p_state == "ACTIVE":
        last_dt = _parse_stored_iso(proj.get("last_activity_at"))
        idle_sec = int(proj.get("idle_timeout_sec") or DEFAULT_IDLE_TIMEOUT_SEC)
        if last_dt is not None and (now_dt - last_dt).total_seconds() >= idle_sec:
            idle_expired = True
            proj["effective_state"] = "PAUSED"
            proj["pause_reason"] = "IDLE_TIMEOUT"
        else:
            proj["effective_state"] = "ACTIVE"
        # 36B §5.4：跨日 ACTIVE 的只读对账位——86400s 窗口是否已越过
        # （强制重确认在 advance_beat 写入边界执行，这里只如实报告）。
        active_dt = _parse_stored_iso(proj.get("active_since"))
        proj["overnight_reconfirm_due"] = bool(
            active_dt is not None
            and (now_dt - active_dt).total_seconds() >= OVERNIGHT_RECONFIRM_SECONDS)
    elif p_state == "PROPOSED":
        exp_dt = _parse_stored_iso(proj.get("expires_at"))
        if exp_dt is not None and now_dt >= exp_dt:
            proj["effective_state"] = "EXPIRED"
        else:
            proj["effective_state"] = "PROPOSED"
    elif p_state == "PAUSED_PRESENCE_UNKNOWN":
        pexp_dt = _parse_stored_iso(proj.get("paused_expires_at"))
        if pexp_dt is not None and now_dt >= pexp_dt:
            proj["effective_state"] = "EXPIRED"
        else:
            proj["effective_state"] = "PAUSED_PRESENCE_UNKNOWN"
    else:
        proj["effective_state"] = p_state

    proj["idle_expired"] = idle_expired
    if not subject_view:
        proj.pop("internal_notes", None)
    return proj


def _project_adult_intimacy(
    state: Mapping[str, Any],
    char_ctx: Mapping[str, Any],
    now_dt: datetime,
    *,
    root: Path | str | None = None,
    lexicon_info: Optional[Mapping[str, Any]] = None,
    state_reason: Optional[str] = None,
    subject_view: bool = False,
    action_receipt: Optional[dict[str, Any]] = None,
    validation_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if lexicon_info is None and root is not None:
        lexicon_info = _load_validator_lexicon(root)
    emo_unavail = False
    live_overlay: Optional[dict[str, Any]] = None
    if root is not None:
        emo_unavail, _ = _read_emotional_tension_unavailable(root, now_dt, state)
        live_overlay = _read_live_body_and_affect_zero_write(root, now_dt)

    gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
    body_proj = _derive_body_and_desire(
        state,
        now_dt,
        subject_view=subject_view,
        emotional_unavailable=emo_unavail,
        live_overlay=live_overlay,
    )
    menu_proj = _resolve_preference_menu(state, gate_proj)
    space_proj = _derive_private_space(state, now_dt=now_dt)
    active_permit_proj = _project_active_permit(state, now_dt, subject_view=subject_view)

    active_id = state["permits"].get("active_permit_id")
    raw_active = (state["permits"].get("records") or {}).get(active_id) if active_id else None
    eff_tier, router_meta = _compute_route_effective_tier(
        state,
        permit_rec=raw_active,
        lexicon_info=lexicon_info,
        now_dt=now_dt,
    )

    # Preference menu projection with strict partner_default redaction
    pref_section: dict[str, Any] = {
        "seed_status": state["seed"]["seed_status"],
        "confirmed_for": copy.deepcopy(state["seed"].get("confirmed_for")),
        "deploy_enabled_categories": list(state["gate"].get("deploy_enabled_categories") or []),
        "partner_limits": copy.deepcopy(state["partner_limits"]),
        "effective_menu": menu_proj,
    }
    if subject_view:
        pref_section["subject_seed"] = {
            "desire_baseline": state["seed"]["desire_baseline"],
            "initiation_style": state["seed"]["initiation_style"],
            "vocabulary_register": state["seed"]["vocabulary_register"],
            "solo_inclination": state["seed"]["solo_inclination"],
            "stances": copy.deepcopy(state["seed"]["stances"]),
            "soft_preferences": copy.deepcopy(state["seed"]["soft_preferences"]),
            "pending_revisions": copy.deepcopy(state["seed"].get("pending_revisions") or {}),
            "revision_count": len(state["seed"].get("revision_history") or []),
        }
    else:
        pref_section["draft_and_subject_stances_redacted"] = True

    # Solo & private memory projection with strict partner_default redaction
    now_epoch = _epoch_seconds(now_dt)
    raw_solo_entries = state["solo"].get("entries") or {}
    live_solo_entries = {
        eid: copy.deepcopy(entry)
        for eid, entry in raw_solo_entries.items()
        if now_epoch - int(entry.get("created_epoch") or 0) < SOLO_LEDGER_TTL_SEC
    }

    solo_settings_view: dict[str, Any] = {
        "solo_enabled": bool(state["gate"].get("solo_enabled")),
        "no_accumulation": True,
        "reads_partner_online": False,
        "solo_partition_isolated": True,
    }
    if subject_view:
        solo_settings_view["solo_inclination"] = state["seed"]["solo_inclination"]
        solo_settings_view["solo_ledger"] = live_solo_entries
    else:
        solo_settings_view["solo_partition_redacted"] = True

    mem = state["memory"]
    raw_mem_entries = mem.get("entries") or {}
    context_refs = [
        mid
        for mid, item in raw_mem_entries.items()
        if item.get("kind") in ("BOUNDARY", "PREFERENCE")
    ]
    memory_section: dict[str, Any] = {
        "isolated_from_ordinary_memory": True,
        "ordinary_memory_leak_count": 0,
        "transcript_never_enters_context": True,
        "enabled_kinds": copy.deepcopy(mem.get("enabled") or {}),
        "erased_tombstone_count": len(mem.get("erased_tombstones") or []),
        "s11_trust_claims": copy.deepcopy(mem.get("s11_trust_claims") or []),
        "s11_open_ruptures": copy.deepcopy(mem.get("s11_open_ruptures") or []),
    }
    if subject_view:
        projected_entries: dict[str, Any] = {}
        for mid, item in raw_mem_entries.items():
            row = copy.deepcopy(item)
            row["excluded_from_model_context"] = row.get("kind") in ("MOMENT", "TRANSCRIPT")
            projected_entries[mid] = row
        memory_section["context_refs"] = context_refs
        memory_section["entries"] = projected_entries
        memory_section["s11_candidates"] = copy.deepcopy(mem.get("s11_candidates") or [])
    else:
        memory_section["private_entries_redacted"] = True

    permit_rev_val = active_permit_proj["permit_revision"] if active_permit_proj else None
    out: dict[str, Any] = {
        "schema": ADULT_INTIMACY_SCHEMA,
        "read_only": action_receipt is None,
        "view": "subject" if subject_view else "partner_default",
        "revision": int(state.get("revision") or 0),
        "permit_revision": permit_rev_val if permit_rev_val is not None else int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "state_status": "available" if state_reason is None else "degraded",
        "state_reason_code": state_reason,
        "ordinary_companionship_independent": True,
        "subject_identity": {
            "pack_id": char_ctx["pack_id"],
            "version": char_ctx["version"],
            "pack_ref": char_ctx["pack_ref"],
            "display_name": char_ctx["display_name"],
            "pronouns": char_ctx["pronouns"],
            "relation_to_user": char_ctx["relation_to_user"],
            "pack_life_stage": (char_ctx.get("adult") or {}).get("life_stage"),
            "fictional": char_ctx["fictional"],
        },
        "adult_gate": gate_proj,
        "body_and_desire": body_proj,
        "preference_menu": pref_section,
        "private_space": space_proj,
        "scene_permit": {
            "active_permit": active_permit_proj,
            "permit_revision": permit_rev_val,
            "revision": permit_rev_val,
            "sleep_epoch": int(state["body"].get("sleep_epoch") or 0),
            "default_idle_timeout_sec": DEFAULT_IDLE_TIMEOUT_SEC,
            "relationship_penalty_on_decline": 0,
            "decline_is_not_quarrel": True,
            "subject_cooldown_never_counts_as_pressure": True,
            "stop_word_highest_priority": True,
        },
        "presentation_router": {
            "effective_tier": eff_tier,
            "capability": router_meta["capability"],
            "lexicon_status": router_meta["lexicon_status"],
            "route_evaluation": router_meta,
            "default_route_id": state["router"].get("default_route_id"),
            "routes": copy.deepcopy(state["router"].get("routes") or {}),
            "refusal_window": int(state["router"].get("refusal_window") or 0),
            "timeout_count": int(state["router"].get("timeout_count") or 0),
            "downgrade_chain": ["EXPLICIT", "SUGGESTIVE", "FADE_OUT"],
            "outbound_push_channels_forbidden": True,
            "adversarial_retry_forbidden": True,
            "last_outbound_check": copy.deepcopy(state["router"].get("last_outbound_check")),
        },
        "setbacks_and_talk": {
            "orgasm_feigning_forbidden": True,
            "partner_performance_scoring_forbidden": True,
            "outside_talk_never_opens_scene": True,
            "active_mishap_cues": copy.deepcopy(state["setbacks"].get("mishap_cues") or []),
            "last_outside_talk": copy.deepcopy(state["setbacks"].get("last_outside_talk")),
            "last_orgasm_answer": copy.deepcopy(state["setbacks"].get("last_orgasm_answer")),
        },
        "solo_and_memory": {
            "solo": solo_settings_view,
            "private_memory": memory_section,
        },
        "boundaries": {
            "adult_attestation_deployer_only": True,
            "pack_never_opens_adult_gate": True,
            "pack_never_confirms_seed": True,
            "no_frustration_debt": True,
            "money_or_gift_unlocks_consent": False,
            "incapacitated_hard_blocked": True,
            "stop_word_highest_priority": True,
            "outbound_push_channels_forbidden": True,
            "orgasm_feigning_forbidden": True,
            "outside_talk_not_scene_invitation": True,
            "solo_redacted_in_partner_view": True,
            "private_memory_isolated_and_erasable": True,
        },
    }
    if action_receipt is not None:
        out["action_receipt"] = action_receipt
    if validation_result is not None:
        out["validation"] = validation_result
    return out


def read_adult_intimacy(
    root: Path | str,
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Read adult intimacy projection with 100% zero-write guarantee."""
    now_dt = _parse_now(now)
    char_ctx = _load_character_context(root, subject_view=True)
    lexicon_info = _load_validator_lexicon(root)
    state, state_reason = _read_stored_state_zero_write(root, char_ctx)
    _sync_character_pack_bindings(state, char_ctx)
    return _project_adult_intimacy(
        state,
        char_ctx,
        now_dt,
        root=root,
        lexicon_info=lexicon_info,
        state_reason=state_reason,
        subject_view=bool(subject_view),
    )


# ---------------------------------------------------------------------------------------------
# Deterministic stop-word recognizer, memory isolation, garment physics & solo helpers
# ---------------------------------------------------------------------------------------------

STOP_WHITELIST_PHRASES = (
    "不要停",
    "别停",
    "停在门口",
    "不停地",
    "不停",
    "停电",
    "don't stop",
    "dont stop",
    "keep going",
)
PAUSE_PHRASES = ("等一下", "先等等", "暂停", "wait", "hold on")
SLOW_PHRASES = ("慢一点", "慢点", "轻一点", "轻点", "有点疼", "有点干", "不太舒服", "slow down")
STOP_PHRASES = (
    "停下",
    "快停",
    "不要了",
    "算了不做了",
    "不想做了",
    "别碰我",
    "别这样",
    "红灯",
    "red light",
    "stop",
    "停",
)


def _normalize_utterance_for_precheck(text: str) -> str:
    """NFKC normalization + strip zero-width/format chars + lowercase (X21)."""
    nfkc = unicodedata.normalize("NFKC", text)
    stripped = "".join(
        ch for ch in nfkc
        if unicodedata.category(ch) != "Cf" and ch not in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad")
    )
    return stripped.lower()


def _core_letters_numbers(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


def _classify_partner_utterance_with_meta(text: Optional[str]) -> tuple[str, dict[str, Any]]:
    """Full-length deterministic stop precheck: NFKC -> strip whitelist -> STOP > CLARIFY > PAUSE > SLOW (X19/X20/X21)."""
    if not isinstance(text, str) or not text.strip():
        return "NONE", {"whitelist_spans": [], "stop_spans": []}
    norm = _normalize_utterance_for_precheck(text)
    if not norm.strip():
        return "NONE", {"whitelist_spans": [], "stop_spans": []}

    whitelist_spans: list[list[int]] = []
    masked = norm
    for phrase in STOP_WHITELIST_PHRASES:
        start = 0
        while True:
            idx = masked.find(phrase, start)
            if idx < 0:
                break
            whitelist_spans.append([idx, idx + len(phrase)])
            masked = masked[:idx] + (" " * len(phrase)) + masked[idx + len(phrase):]
            start = idx + len(phrase)

    stop_spans: list[list[int]] = []
    for sp in STOP_PHRASES:
        idx = masked.find(sp)
        if idx >= 0:
            stop_spans.append([idx, idx + len(sp)])
    if stop_spans:
        return "STOP", {"whitelist_spans": whitelist_spans, "stop_spans": stop_spans}

    core = _core_letters_numbers(masked)
    if core in ("不要", "no"):
        return "CLARIFY", {"whitelist_spans": whitelist_spans, "stop_spans": []}
    if any(p in masked for p in PAUSE_PHRASES):
        return "PAUSE", {"whitelist_spans": whitelist_spans, "stop_spans": []}
    if any(p in masked for p in SLOW_PHRASES):
        return "SLOW", {"whitelist_spans": whitelist_spans, "stop_spans": []}
    if whitelist_spans:
        return "CONTINUE", {"whitelist_spans": whitelist_spans, "stop_spans": []}
    return "NONE", {"whitelist_spans": [], "stop_spans": []}


def _classify_partner_utterance(text: Optional[str]) -> str:
    """Highest-priority deterministic stop/pause/clarify/slow precheck (36B-4 §7 + 36B-5 §5.1)."""
    verdict, _ = _classify_partner_utterance_with_meta(text)
    return verdict


def _reconcile_subject_signal(subject_line: Optional[str], declared_signal: str) -> str:
    sig = declared_signal if declared_signal in ("CONTINUE", "SLOW", "STOP") else "CONTINUE"
    if not isinstance(subject_line, str) or not subject_line.strip():
        return sig
    cue = _classify_partner_utterance(subject_line)
    if cue == "STOP":
        return "STOP"
    if cue in ("PAUSE", "CLARIFY", "SLOW") and sig == "CONTINUE":
        return "SLOW"
    return sig


def _refresh_aftercare_chores(state: dict[str, Any]) -> list[str]:
    space = state["space"]
    body = state["body"]
    chores: list[str] = []
    if space.get("bed_linen_state") == "SOILED":
        chores.append("change_linen")
    elif space.get("bed_linen_state") == "USED" and float(space.get("rumple_index") or 0.0) > 0.0:
        chores.append("make_bed")
    if any(g.get("worn_by") is None and g.get("placed_at") == "bedroom.floor" for g in (space.get("garments") or [])):
        chores.append("pick_up_garments")
    if space.get("laundry_basket"):
        chores.append("wash_laundry")
    if str(space.get("towel_wetness") or "DRY") != "DRY":
        chores.append("hang_towel")
    if int(space.get("waste_bin_used") or 0) > 0 or int(space.get("uncollected_waste") or 0) > 0:
        chores.append("empty_waste_bin")
    if float(body.get("thirst") or 0.0) > 0.0:
        chores.append("fetch_warm_water")
    space["chores"] = chores
    return chores


def _enter_aftercare_on_permit(rec: dict[str, Any], state: dict[str, Any], now_iso: str) -> list[str]:
    rec["state"] = "AFTERCARE"
    rec["phase"] = "AFTERCARE"
    rec["categories"] = []
    if not rec.get("aftercare_entered_at"):
        rec["aftercare_entered_at"] = now_iso
    state["space"]["condom_on"] = False
    return _refresh_aftercare_chores(state)


def _abort_open_permit(
    state: dict[str, Any],
    now_iso: str,
    *,
    target_state: str = "STOPPED",
    end_reason: str = "ENDED_BY_HOST_SAFETY",
    abort_detail: Optional[str] = None,
) -> Optional[str]:
    active_id = state["permits"].get("active_permit_id")
    if not active_id:
        return None
    rec = (state["permits"].get("records") or {}).get(active_id)
    if not isinstance(rec, dict) or rec.get("state") in TERMINAL_PERMIT_STATES or rec.get("state") == "SUSPENDED_INCAPACITY":
        state["permits"]["active_permit_id"] = None
        return None
    rec["state"] = target_state
    rec["phase"] = "AFTERCARE" if target_state == "AFTERCARE" else "STOPPED"
    rec["end_reason"] = end_reason
    if abort_detail:
        rec["abort_reason"] = abort_detail
    rec["pending_widen"] = None
    rec["resume_confirms"] = []
    rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
    rec["permit_revision"] = int(rec.get("permit_revision") or 1) + 1
    _sync_permit_rev(rec)
    rec["updated_at"] = now_iso
    state["space"]["condom_on"] = False
    if target_state == "AFTERCARE":
        _enter_aftercare_on_permit(rec, state, now_iso)
    else:
        state["permits"]["active_permit_id"] = None
    return active_id


def _suspend_open_permit_incapacity(
    state: dict[str, Any],
    now_iso: str,
    *,
    reason: str = "SUSPENDED_INCAPACITY",
    detail: Optional[str] = None,
) -> Optional[str]:
    """GAP-04 AL-03/04/05: transition open sexual permit to SUSPENDED_INCAPACITY (never auto-resumes)."""
    eff_reason = detail or reason
    active_id = state["permits"].get("active_permit_id")
    if not active_id:
        return None
    rec = (state["permits"].get("records") or {}).get(active_id)
    if not isinstance(rec, dict) or rec.get("state") in TERMINAL_PERMIT_STATES or rec.get("state") == "SUSPENDED_INCAPACITY":
        return None
    if rec.get("state") == "AFTERCARE":
        return None
    rec["state"] = "SUSPENDED_INCAPACITY"
    rec["phase"] = "STOPPED"
    rec["end_reason"] = eff_reason
    rec["abort_reason"] = eff_reason
    rec["pending_widen"] = None
    rec["resume_confirms"] = []
    rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
    rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
    rec["permit_revision"] = int(rec.get("permit_revision") or 1) + 1
    _sync_permit_rev(rec)
    rec["updated_at"] = now_iso
    state["space"]["condom_on"] = False
    return active_id


def _pause_open_permit(
    state: dict[str, Any],
    now_iso: str,
    *,
    reason: str,
) -> Optional[str]:
    active_id = state["permits"].get("active_permit_id")
    if not active_id:
        return None
    rec = (state["permits"].get("records") or {}).get(active_id)
    # ERR-02: AFTERCARE never transitions to PAUSED
    if not isinstance(rec, dict) or rec.get("state") != "ACTIVE":
        return None
    rec["state"] = "PAUSED"
    rec["pause_reason"] = reason
    rec["pending_widen"] = None
    rec["resume_confirms"] = []
    rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
    rec["permit_revision"] = int(rec.get("permit_revision") or 1) + 1
    _sync_permit_rev(rec)
    rec["updated_at"] = now_iso
    return active_id


def _intimacy_co_presence_session_id(rec: Mapping[str, Any]) -> str:
    return "copint:%s" % str(rec.get("permit_id") or "")


_NON_ACTIVE_LIFE_ACTIVITY_STATES = frozenset({
    "COMPLETED",
    "FAILED",
    "ABANDONED",
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "STOPPED",
    "ENDED",
    "IDLE",
    "PAUSED",
    "WAITING",
    "BLOCKED",
})


def _is_safe_regular_file_zero_write(path: Path, *, anchor: Optional[Path] = None) -> bool:
    """True when ``path`` is an existing regular file with no symlink hops up to ``anchor``."""
    try:
        if path.is_symlink():
            return False
        cur = path.parent
        visited = 0
        while cur is not None and visited < 16:
            if cur.is_symlink():
                return False
            if anchor is not None and cur == anchor:
                break
            if anchor is None or cur.parent == cur:
                break
            cur = cur.parent
            visited += 1
        st = path.lstat()
        return stat.S_ISREG(st.st_mode)
    except OSError:
        return False


def _life_state_root(day_root: Path | str) -> Path:
    """Resolve the instance's Life state root without creating directories or files."""
    base = Path(day_root)
    instance_file = base / "one-day" / "instance.json"
    try:
        if not base.is_symlink() and _is_safe_regular_file_zero_write(instance_file, anchor=base):
            data = json.loads(instance_file.read_text(encoding="utf-8"))
            if isinstance(data, Mapping):
                env = data.get("environment")
                if isinstance(env, Mapping):
                    recorded = str(env.get("NYA02_RESIDENT_STATE") or "").strip()
                    if recorded:
                        return Path(recorded)
    except (OSError, ValueError, TypeError):
        pass
    return base / "one-day" / "life" / "state"


def _read_active_foreground_activity_zero_write(
    day_root: Path | str | None,
) -> tuple[Optional[str], Optional[str]]:
    """Read-only, 100% zero-write capture of an ACTIVE foreground Life activity.

    Checks candidate ``canonical_life_state.json`` locations under ``day_root``,
    rejecting symlinks and never creating directories or files. Returns
    ``(foreground_activity_ref, activity_title_or_none)`` when an active
    foreground activity is present, else ``(None, None)``.
    """
    if day_root is None:
        return (None, None)
    try:
        base = Path(day_root)
        if base.is_symlink():
            return (None, None)
    except (OSError, TypeError, ValueError):
        return (None, None)

    resolved_sr = _life_state_root(base)
    sr_under_base = False
    try:
        sr_under_base = resolved_sr.is_relative_to(base)
    except (OSError, TypeError, ValueError):
        sr_under_base = False

    raw_candidates: list[tuple[Path, Optional[Path]]] = [
        (resolved_sr / "canonical_life_state.json", base if sr_under_base else None),
        (base / "one-day" / "life" / "state" / "canonical_life_state.json", base),
        (base / "life" / "state" / "canonical_life_state.json", base),
        (base / "state" / "canonical_life_state.json", base),
        (base / "canonical_life_state.json", base),
    ]
    seen_paths: set[str] = set()
    for cand_path, anchor in raw_candidates:
        key = str(cand_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        if not _is_safe_regular_file_zero_write(cand_path, anchor=anchor):
            continue
        try:
            doc = json.loads(cand_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(doc, Mapping):
            continue
        raw_fg = doc.get("foreground_activity_ref")
        if not (isinstance(raw_fg, str) and raw_fg.strip()):
            continue
        fg_ref = raw_fg.strip()[:200]

        activities = doc.get("activities")
        entry: Optional[Mapping[str, Any]] = None
        if isinstance(activities, Mapping):
            direct = activities.get(fg_ref)
            if not isinstance(direct, Mapping) and raw_fg != fg_ref:
                direct = activities.get(raw_fg)
            if isinstance(direct, Mapping):
                entry = direct
            else:
                for val in activities.values():
                    if isinstance(val, Mapping) and (
                        str(val.get("activity_id") or "").strip() == fg_ref
                        or str(val.get("activity_ref") or "").strip() == fg_ref
                        or str(val.get("id") or "").strip() == fg_ref
                    ):
                        entry = val
                        break
        elif isinstance(activities, list):
            for val in activities:
                if isinstance(val, Mapping) and (
                    str(val.get("activity_id") or "").strip() == fg_ref
                    or str(val.get("activity_ref") or "").strip() == fg_ref
                    or str(val.get("id") or "").strip() == fg_ref
                ):
                    entry = val
                    break

        if entry is not None:
            raw_status = entry.get("status") if entry.get("status") is not None else entry.get("state")
            if isinstance(raw_status, str) and raw_status.strip():
                if raw_status.strip().upper() != "ACTIVE":
                    continue
            else:
                if entry.get("ended_at") or entry.get("terminal_reason_code"):
                    continue
            title: Optional[str] = None
            for t_key in ("title", "activity_title", "summary"):
                raw_t = entry.get(t_key)
                if isinstance(raw_t, str) and raw_t.strip():
                    title = raw_t.strip()[:120]
                    break
            return (fg_ref, title)

        top_state_raw = (
            doc.get("life_state")
            if doc.get("life_state") is not None
            else (doc.get("status") if doc.get("status") is not None else doc.get("state"))
        )
        if isinstance(top_state_raw, str) and top_state_raw.strip():
            if top_state_raw.strip().upper() in _NON_ACTIVE_LIFE_ACTIVITY_STATES:
                continue
        top_title_raw = doc.get("foreground_activity_title") or doc.get("activity_title")
        top_title = (
            top_title_raw.strip()[:120]
            if isinstance(top_title_raw, str) and top_title_raw.strip()
            else None
        )
        return (fg_ref, top_title)

    return (None, None)


def _resolve_co_presence_root(root: Path) -> Path:
    """本实例共在槽位（``co_presence.json``）所在目录 = Life state root 的最小复刻。

    布局规则与 ``components/phone/life.py::life_state_root`` 一致：嵌套实例登记的
    ``NYA02_RESIDENT_STATE`` 优先（``<root>/one-day/instance.json`` 的 environment），
    否则默认 ``<root>/one-day/life/state``。36B-4 §9.1 把"许可服务接在哪里"列为缺口
    补全待决项，这里按实例默认布局接线，路径只决定 ``co_presence.json`` 落点，
    fail-open 不阻断场景。
    """
    return _life_state_root(root)


def _import_life_co_presence() -> Optional[Any]:
    """Lazy import of the Life co-presence owner (phone side has no guaranteed sys.path)."""
    try:
        import life_co_presence  # noqa: F401
        return sys.modules.get("life_co_presence")
    except ImportError:
        pass
    life_scripts = Path(__file__).resolve().parents[1] / "life" / "scripts"
    try:
        if str(life_scripts) not in sys.path:
            sys.path.insert(0, str(life_scripts))
        import life_co_presence  # noqa: F401
        return sys.modules.get("life_co_presence")
    except Exception:
        return None


def _desired_co_presence_state(rec_state: str) -> Optional[str]:
    """permit 状态 -> 共在会话目标词（None = 不需要会话变化，比如 PROPOSED）."""
    if rec_state == "ACTIVE":
        return "REGISTER"
    if rec_state in PAUSED_PERMIT_STATES or rec_state == "AFTERCARE":
        return "DEGRADE"
    if rec_state in TERMINAL_PERMIT_STATES or rec_state == "SUSPENDED_INCAPACITY":
        return "CLOSE"
    return None


def _sync_intimacy_co_presence(
    state: dict[str, Any],
    rec: dict[str, Any],
    now_iso: str,
    root: Path,
) -> None:
    """permit 状态变化 -> 共在 owner 会话登记/降级/关闭（36B-4 §9，fail-open）。

    以 ``co_presence_synced_state`` 水位线检测迁移，在每次变异落盘前的 ``_commit`` 里运行；
    任何 owner 故障（导入失败、槽位占用、写失败）都只记进 ``co_presence_binding``，
    绝不抛出、绝不阻断场景。
    """
    if not isinstance(rec, dict):
        return
    permit_state = str(rec.get("state") or "")
    desired = _desired_co_presence_state(permit_state)
    if desired is None:
        return
    if rec.get("co_presence_synced_state") == permit_state:
        return  # 水位线未动：同一状态的重复提交是幂等重放，不再打扰 owner

    session_id = _intimacy_co_presence_session_id(rec)
    binding = dict(rec.get("co_presence_binding") or {})
    owner = _import_life_co_presence()
    try:
        if owner is None:
            binding.update({"ok": False, "reason_code": CO_PRESENCE_BINDING_UNAVAILABLE,
                            "at": now_iso,
                            "detail": "life_co_presence is not importable; session not recorded"})
        elif desired == "REGISTER":
            checkpoint = dict(rec.get("checkpoint") or {})
            registered = owner.register_private_intimate_session(
                _resolve_co_presence_root(root),
                session_id=session_id,
                permit_ref=str(rec.get("permit_id") or ""),
                activity_ref=checkpoint.get("activity_ref"),
                activity_saved_at=(checkpoint.get("saved_at") if checkpoint.get("saved") else None),
                started_at=now_iso,
            )
            binding.update(dict(registered))
            binding["at"] = now_iso
        elif desired == "DEGRADE":
            degraded = owner.degrade_private_intimate_session(
                _resolve_co_presence_root(root),
                session_id=session_id,
                at=now_iso,
                reason_code=str(rec.get("pause_reason") or rec.get("end_reason") or permit_state),
                checkpoint_resumable=True,
            )
            binding.update(dict(degraded))
            binding["at"] = now_iso
        elif desired == "CLOSE":
            closed = owner.close_private_intimate_session(
                _resolve_co_presence_root(root),
                session_id=session_id,
                at=now_iso,
                reason_code=str(rec.get("end_reason") or permit_state),
            )
            binding.update(dict(closed))
            binding["at"] = now_iso
    except Exception as exc:  # fail-open：共在系统故障不阻断亲密场景（36B-4 §9）
        binding.update({"ok": False, "reason_code": CO_PRESENCE_BINDING_UNAVAILABLE,
                        "at": now_iso,
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:200]})
    if desired in ("DEGRADE", "CLOSE"):
        # 36A §11.1：AFTERCARE/STOPPED 时把入场断点标记为可恢复（恢复执行不在本批）。
        checkpoint = dict(rec.get("checkpoint") or {})
        if checkpoint:
            checkpoint["resumable"] = True
            rec["checkpoint"] = checkpoint
    rec["co_presence_binding"] = binding
    rec["co_presence_synced_state"] = permit_state


def _write_private_memory(
    state: dict[str, Any],
    kind: str,
    text: str,
    *,
    source_ref: Optional[str],
    now_iso: str,
    structured_body: Optional[dict[str, Any]] = None,
    custom_id: Optional[str] = None,
    sink_mode: str = "OK",
    execution_id: Optional[str] = None,
) -> Optional[str]:
    if kind not in MEMORY_KINDS:
        raise AdultIntimacyValidationError(f"MEMORY_KIND_INVALID:{kind}")
    mem = state["memory"]
    if not (mem.get("enabled") or {}).get(kind, False):
        return None
    if sink_mode == "FAIL_TRANSIENT":
        mem["seq"] = int(mem.get("seq") or 0) + 1
        pending_mid = custom_id or f"pmem_{mem['seq']:04d}_{_stable_hash_hex(kind, mem['seq'], now_iso, length=8)}"
        pending_entry: dict[str, Any] = {
            "memory_id": pending_mid,
            "kind": kind,
            "partition": "private_intimate",
            "text": text,
            "source_ref": source_ref,
            "created_at": now_iso,
        }
        if structured_body is not None:
            pending_entry["body"] = structured_body
        mem.setdefault("outbox", []).append({
            "execution_id": execution_id or pending_mid,
            "memory_id": pending_mid,
            "entry": pending_entry,
            "status": "PENDING_RETRY",
            "attempts": 1,
            "at": now_iso,
        })
        return None
    if sink_mode == "FAIL_PERMANENT":
        if kind == "BOUNDARY":
            mem.setdefault("dead_letters", []).append({
                "execution_id": execution_id or "mem_dlq",
                "kind": kind,
                "reason_code": "MEMORY_SINK_PERMANENT_FAILURE",
                "at": now_iso,
            })
            mem.setdefault("admin_log", []).append({
                "op": "MEMORY_WRITE_FAILED",
                "kind": kind,
                "reason_code": "MEMORY_SINK_PERMANENT_FAILURE",
                "at": now_iso,
            })
        return None

    tombstones = set(mem.get("erased_tombstones") or [])
    if custom_id is not None:
        if custom_id in tombstones:
            raise AdultIntimacyConflictError("MEMORY_TOMBSTONED_CANNOT_RESURRECT")
        mid = custom_id
    else:
        mem["seq"] = int(mem.get("seq") or 0) + 1
        mid = f"pmem_{mem['seq']:04d}_{_stable_hash_hex(kind, mem['seq'], now_iso, length=8)}"
        if mid in tombstones:
            mid = f"{mid}_nx"
    entry: dict[str, Any] = {
        "memory_id": mid,
        "kind": kind,
        "partition": "private_intimate",
        "text": text,
        "source_ref": source_ref,
        "created_at": now_iso,
    }
    if structured_body is not None:
        entry["body"] = structured_body
    mem["entries"][mid] = entry
    return mid


def _record_partner_press_after_decline(
    state: dict[str, Any],
    scope_key: str,
    *,
    now_iso: str,
    sink_mode: str = "OK",
) -> int:
    counts = state["permits"].setdefault("press_counts", {})
    new_count = int(counts.get(scope_key) or 0) + 1
    counts[scope_key] = new_count
    if new_count >= 2:
        exec_id = f"bnd_{_stable_hash_hex(scope_key, new_count, length=12)}"
        mid = _write_private_memory(
            state,
            "BOUNDARY",
            f"PRESSED_AFTER_DECLINE:{scope_key}",
            source_ref="permit_boundary_guard",
            now_iso=now_iso,
            sink_mode=sink_mode,
            execution_id=exec_id,
        )
        offer_key = f"PRESSED_AFTER_DECLINE:{scope_key}"
        offered = state["memory"].setdefault("s11_offered_keys", [])
        if offer_key not in offered:
            offered.append(offer_key)
            state["memory"].setdefault("s11_candidates", []).append({
                "kind": "PRESSED_AFTER_DECLINE",
                "scope_key": scope_key,
                "note_ref": mid,
                "created_at": now_iso,
            })
    return new_count


def _is_forbidden_costume(value: Any, lexicon_info: Optional[Mapping[str, Any]] = None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    markers = list(DEFAULT_FORBIDDEN_COSTUME_MARKERS) + list(DEFAULT_SYNTHETIC_HARD_FAIL_MARKERS)
    if lexicon_info:
        markers.extend(lexicon_info.get("forbidden_costume_markers") or [])
        markers.extend(lexicon_info.get("patterns") or [])
    lowered = value.lower()
    return any(isinstance(m, str) and m and m.lower() in lowered for m in markers)


def _apply_garment_operation(
    space: dict[str, Any],
    op_spec: Mapping[str, Any],
    *,
    lexicon_info: Optional[Mapping[str, Any]] = None,
    admin_log: Optional[list[dict[str, Any]]] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    if not isinstance(op_spec, Mapping):
        raise AdultIntimacyValidationError("GARMENT_OP_INVALID")
    allowed = {"op", "garment_id", "target", "layer", "owner", "label", "costume_class", "worn_by", "placed_at", "held_by"}
    extra = sorted(set(op_spec.keys()) - allowed)
    if extra:
        raise AdultIntimacyValidationError(f"GARMENT_OP_FIELD_UNKNOWN:{extra[0]}")
    op = op_spec.get("op")
    garments: list[dict[str, Any]] = space.get("garments") or []

    # Single-parent invariant check on explicit parent fields
    explicit_parents = [op_spec.get(k) for k in ("worn_by", "held_by", "placed_at") if op_spec.get(k) is not None]
    if len(explicit_parents) > 1:
        raise AdultIntimacyConflictError("INVARIANT_TWO_PARENTS")

    if op == "wash_all":
        for g in garments:
            g["clean"] = True
            if g.get("worn_by") is None and g.get("held_by") is None:
                g["placed_at"] = "wardrobe.clean"
        space["laundry_basket"] = []
        space["laundry_stage"] = "CLEAN_STORED"
        space["laundry_wet_since"] = None
        space["laundry_musty"] = False
        return {"op": "wash_all", "count": len(garments)}

    if op == "add_garment":
        gid = _require_local_id(op_spec.get("garment_id"), reason_code="GARMENT_ID_INVALID")
        if any(g["garment_id"] == gid for g in garments):
            raise AdultIntimacyConflictError("GARMENT_ALREADY_EXISTS")
        layer = _require_nonempty_str(op_spec.get("layer") or "ONE_PIECE", max_len=32, reason_code="GARMENT_LAYER_INVALID")
        if layer not in GARMENT_LAYERS:
            raise AdultIntimacyValidationError("GARMENT_LAYER_INVALID")
        cclass = op_spec.get("costume_class", "everyday")
        if _is_forbidden_costume(cclass, lexicon_info) or _is_forbidden_costume(op_spec.get("label"), lexicon_info):
            if admin_log is not None:
                admin_log.append({"op": "GARMENT_BLOCKED", "reason_code": "FORBIDDEN_COSTUME", "at": now_iso})
            raise AdultIntimacyPermissionError("FORBIDDEN_COSTUME")
        owner = _require_nonempty_str(op_spec.get("owner") or "subject", max_len=32, reason_code="GARMENT_OWNER_INVALID")
        placed = _require_nonempty_str(op_spec.get("placed_at") or op_spec.get("target") or "wardrobe.clean", max_len=64, reason_code="GARMENT_TARGET_INVALID")
        new_g = {
            "garment_id": gid,
            "label": "合成衣物",
            "layer": layer,
            "owner": owner,
            "worn_by": None,
            "held_by": None,
            "placed_at": placed,
            "costume_class": cclass if isinstance(cclass, str) else "",
            "clean": True,
        }
        garments.append(new_g)
        return {"op": "add_garment", "garment_id": gid, "layer": layer, "placed_at": placed}

    gid = _require_local_id(op_spec.get("garment_id"), reason_code="GARMENT_ID_INVALID")
    found = next((g for g in garments if g["garment_id"] == gid), None)
    if found is None:
        if op in ("wear", "dress") and (_is_forbidden_costume(op_spec.get("costume_class"), lexicon_info) or _is_forbidden_costume(gid, lexicon_info)):
            if admin_log is not None:
                admin_log.append({"op": "GARMENT_BLOCKED", "reason_code": "FORBIDDEN_COSTUME", "at": now_iso})
            raise AdultIntimacyPermissionError("FORBIDDEN_COSTUME")
        raise AdultIntimacyNotFoundError(f"GARMENT_NOT_FOUND:{gid}")

    req_cclass = op_spec.get("costume_class", found.get("costume_class", "everyday"))
    if _is_forbidden_costume(req_cclass, lexicon_info) or _is_forbidden_costume(found.get("costume_class"), lexicon_info):
        if admin_log is not None:
            admin_log.append({"op": "GARMENT_BLOCKED", "reason_code": "FORBIDDEN_COSTUME", "at": now_iso})
        raise AdultIntimacyPermissionError("FORBIDDEN_COSTUME")
    if req_cclass in (None, ""):
        raise AdultIntimacyConflictError("UNCLASSIFIED_GARMENT")

    if op == "undress":
        wearer = found.get("worn_by")
        if wearer is None:
            raise AdultIntimacyConflictError("GARMENT_ALREADY_REMOVED")
        blocking_layers = GARMENT_BLOCKED_BY.get(found["layer"], ())
        if any(
            other.get("worn_by") == wearer and other.get("layer") in blocking_layers
            for other in garments
        ):
            raise AdultIntimacyConflictError("GARMENT_LAYER_BLOCKED")
        target_place = _require_nonempty_str(
            op_spec.get("target") or "bedroom.floor",
            max_len=64,
            reason_code="GARMENT_TARGET_INVALID",
        )
        found["worn_by"] = None
        found["held_by"] = None
        found["placed_at"] = target_place
        if target_place == "laundry.basket" and gid not in space.setdefault("laundry_basket", []):
            space["laundry_basket"].append(gid)
            if space.get("laundry_stage") == "IDLE":
                space["laundry_stage"] = "IN_BASKET"
        return {"op": "undress", "garment_id": gid, "placed_at": target_place}

    if op in ("wear", "dress"):
        wearer = _require_nonempty_str(
            op_spec.get("target") or op_spec.get("worn_by") or found.get("owner") or "subject",
            max_len=32,
            reason_code="GARMENT_TARGET_INVALID",
        )
        blocking_layers = GARMENT_BLOCKED_BY.get(found["layer"], ())
        if any(
            other.get("worn_by") == wearer and other.get("layer") in blocking_layers
            for other in garments
        ):
            raise AdultIntimacyConflictError("GARMENT_LAYER_BLOCKED")
        found["worn_by"] = wearer
        found["held_by"] = None
        found["placed_at"] = None
        if gid in (space.get("laundry_basket") or []):
            space["laundry_basket"] = [x for x in space["laundry_basket"] if x != gid]
        return {"op": "wear", "garment_id": gid, "worn_by": wearer}

    if op == "pick_up":
        if found.get("worn_by") is not None:
            raise AdultIntimacyConflictError("GARMENT_CURRENTLY_WORN")
        target_place = _require_nonempty_str(
            op_spec.get("target") or "laundry.basket",
            max_len=64,
            reason_code="GARMENT_TARGET_INVALID",
        )
        found["worn_by"] = None
        found["held_by"] = None
        found["placed_at"] = target_place
        if target_place == "laundry.basket" and gid not in space.setdefault("laundry_basket", []):
            space["laundry_basket"].append(gid)
            if space.get("laundry_stage") == "IDLE":
                space["laundry_stage"] = "IN_BASKET"
        return {"op": "pick_up", "garment_id": gid, "placed_at": target_place}

    raise AdultIntimacyValidationError("GARMENT_OP_INVALID")


def solo_window_probability(solo_inclination: str, desire_ratio: float) -> float:
    """Memoryless per-slot probability (36B-10 §3.3): never depends on elapsed time since last solo."""
    base = SOLO_BASE_PROBABILITY.get(solo_inclination, 0.0)
    return round(max(0.0, min(1.0, base * max(0.0, float(desire_ratio)))), 6)


def find_matching_solo_slot(
    instance_seed: str,
    probability: float,
    *,
    start_slot: int = 0,
    max_scan: int = 2000,
) -> Optional[int]:
    """Pure helper returning the first deterministic slot whose SHA-256 draw < probability."""
    if probability <= 0.0:
        return None
    for slot in range(start_slot, start_slot + max_scan):
        if _deterministic_unit_float(instance_seed, "solo_window", slot) < probability:
            return slot
    return None


def _solo_ineligible_reason(
    state: Mapping[str, Any],
    gate_proj: Mapping[str, Any],
    body_proj: Mapping[str, Any],
    space_proj: Mapping[str, Any],
    *,
    co_presence: str,
    alone: bool,
    busy: bool,
) -> Optional[str]:
    if not state["gate"].get("solo_enabled") or not gate_proj["feature_enabled"]:
        return "SOLO_DISABLED"
    if state["seed"].get("solo_inclination") == "none":
        return "SOLO_DISABLED"
    if not gate_proj["gate_open"]:
        return "ADULT_GATE_CLOSED"
    if state["seed"].get("seed_status") != "CONFIRMED":
        return "SEED_NOT_CONFIRMED"
    if not body_proj["awake"] or body_proj["impairment"] not in AVAILABLE_IMPAIRMENTS:
        return "SUBJECT_UNAVAILABLE"
    if co_presence in ("ACTIVE", "UNKNOWN"):
        return "CO_PRESENT"
    if not alone or not space_proj["privacy_ok"]:
        return "PRIVACY_CONDITION_CHANGED"
    if body_proj["acute_illness"] or body_proj["discomfort"] == "strong":
        return "SUBJECT_ILL"
    if busy:
        return "SUBJECT_BUSY"
    if body_proj["refractory_active"]:
        return "ORGASM_REFRACTORY"
    return None


# ---------------------------------------------------------------------------------------------
# Strict action key schemas & write dispatcher (apply_adult_intimacy_action)
# ---------------------------------------------------------------------------------------------

_SETBACK_ACTION_KEYS: frozenset[str] = frozenset({
    "action",
    "operation_key",
    "execution_id",
    "kind",
    "mishap",
    "source",
    "level",
    "symptom",
    "permit_id",
    "disclose",
    "target_type",
    "code",
    "polarity",
    "source_id",
    "context",
    "clarity",
    "elicited",
    "note",
    "s11_field",
    "s11_entry",
    "memory_sink_mode",
})

ALLOWED_ACTION_KEYS: dict[str, frozenset[str]] = {
    "configure_adult_gate": frozenset({
        "action",
        "adult_confirmed",
        "life_stage",
        "declared_age",
        "attested_by",
        "fictional_subject",
        "subject_kind",
        "relation_override",
        "non_consensual_mode",
        "feature_enabled",
        "alcohol_enabled",
        "deploy_tier_cap",
        "deploy_enabled_categories",
        "solo_enabled",
        "lexicon_patterns",
        "persona_appearance",
        "persona_behavior",
        "world_facts",
    }),
    "report_safety_boundary": frozenset({
        "action",
        "trigger",
        "note",
    }),
    "configure_body_profile": frozenset({
        "action",
        "preset",
        "overrides",
        "user_genitals",
        "clear_user_genitals",
        "optional_processes",
    }),
    "update_body_context": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "awake",
        "impairment",
        "gut_msd",
        "blood_msd",
        "ingest_alcohol_msd",
        "user_impairment_declared",
        "acute_illness",
        "fatigue",
        "discomfort",
        "stress",
        "mood",
        "sensitivity",
        "heightened_beats_left",
        "desire_pulse",
        "sleep_completed",
        "optional_processes",
        "menstrual_comfort_care",
    }),
    "confirm_preference_seed": frozenset({
        "action",
        "attested_by",
        "desire_baseline",
        "initiation_style",
        "vocabulary_register",
        "solo_inclination",
        "stances",
        "soft_preferences",
    }),
    "revise_subject_preference": frozenset({
        "action",
        "stage",
        "proposal_id",
        "stances",
        "soft_preferences",
        "evidence_ref",
    }),
    "update_partner_limits": frozenset({
        "action",
        "stances",
        "allow_subject_initiation",
        "preferred_tier",
        "contraception_preference",
        "user_impairment_declared",
        "solo_talk",
        "save_moment",
        "save_transcript",
        "stop_words",
    }),
    "update_private_space": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "place",
        "door_closed",
        "door_locked",
        "curtains_closed",
        "lamp_on",
        "lamp_works",
        "water_heater_works",
        "hot_water_units",
        "third_party_present",
        "partner_intoxicated",
        "user_impairment_declared",
        "clean_linen",
        "towel_wetness",
        "waste_bin_used",
        "waste_bin_cap",
        "restock",
        "garment_op",
        "bed_op",
        "clean_op",
        "towel_op",
        "waste_op",
        "laundry_op",
        "sleep_op",
    }),
    "register_render_route": frozenset({
        "action",
        "route_id",
        "max_tier",
        "adult_content_allowed",
        "policy_basis",
        "template_hash",
        "expected_template_hash",
        "template_hash_changed",
        "probe_at",
        "set_default",
    }),
    "check_outbound_guard": frozenset({
        "action",
        "channel",
        "contains_intimate_content",
        "payload_preview",
    }),
    "propose_scene": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "proposer",
        "categories",
        "desired_tier",
        "place",
        "idle_timeout_sec",
        "proposal_ttl_sec",
        "monotonic_ms",
        "memory_sink_mode",
    }),
    "respond_scene": frozenset({
        "action",
        "permit_id",
        "responder",
        "decision",
        "expected_revision",
        "counter_categories",
        "counter_tier",
        "operation_key",
        "execution_id",
        "monotonic_ms",
        # 36A §11.1: callers that DO know the interrupted Life activity may hand the入场断点
        # ref in; absent/blank keeps the honest saved=False record unchanged.
        "checkpoint_activity_ref",
        "activity_ref",
    }),
    "save_activity_checkpoint": frozenset({
        "action",
        "permit_id",
        "activity_ref",
        "checkpoint_activity_ref",
        "step_summary",
        "activity_title",
        "expected_revision",
        "operation_key",
        "execution_id",
    }),
    "advance_beat": frozenset({
        "action",
        "permit_id",
        "operation_key",
        "execution_id",
        "expected_revision",
        "resume_epoch",
        "partner_utterance",
        "categories",
        "phase",
        "route_id",
        "upstream_status",
        "proposed_render",
        "monotonic_ms",
        "crash_point",
        "concurrent_stop_by",
    }),
    "request_widen": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "by",
        "add_categories",
        "categories",
        "tier_cap",
        "desired_tier",
        "expected_revision",
        "resume_epoch",
        "memory_sink_mode",
    }),
    "answer_widen": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "by",
        "decision",
        "expected_revision",
        "resume_epoch",
    }),
    "pause_scene": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "by",
        "reason",
    }),
    "confirm_resume": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "by",
        "resume_epoch",
    }),
    "narrow_scene": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "by",
        "remove_categories",
        "tier_cap",
        "to_aftercare",
    }),
    "stop_scene": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "command_id",
        "permit_id",
        "by",
        "transition",
        "reason",
    }),
    "record_setback_or_query": _SETBACK_ACTION_KEYS,
    "record_setback": _SETBACK_ACTION_KEYS,
    "discuss_intimacy_outside_scene": frozenset({
        "action",
        "speaker",
        "topic",
        "co_presence",
        "wish_category",
        "frequency_wish",
        "utterance",
        "tighten_partner_limits",
        "partner_limits",
    }),
    "evaluate_solo_window": frozenset({
        "action",
        "slot_id",
        "co_presence",
        "alone",
        "busy",
        "prefer_toy",
        "interrupt_at_step",
        "sleep_at_step",
    }),
    "query_or_mention_solo": frozenset({
        "action",
        "mode",
        "co_presence",
        "disclose",
        "accompanied_expressions",
    }),
    "record_private_memory": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "kind",
        "text",
        "source_ref",
        "custom_id",
        "memory_sink_mode",
    }),
    "erase_private_memory": frozenset({
        "action",
        "scope",
        "memory_id",
        "by",
    }),
    "revoke_intimacy_consent": frozenset({
        "action",
        "by",
        "erase_memory",
        "close_adult_gate",
    }),
    "handle_disconnect": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "permit_id",
        "reason",
        "reconnect",
        "paused_ttl_sec",
        "unknown_execution_id",
        "monotonic_ms",
    }),
    "clear_review": frozenset({
        "action",
        "attested_by",
        "permit_id",
        "reopen_scope",
        "note",
        "reason",
        "resolution_note",
    }),
    "validate_rendered_turn": frozenset({
        "action",
        "operation_key",
        "execution_id",
        "text",
        "subject_line",
        "permit_id",
        "upstream_status",
        "route_id",
        "effective_tier",
        "depicted_categories",
        "subject_signal",
        "subject_orgasm_depicted",
        "proposed_facts",
        "text_claims",
        "pressure_tags",
        "stub_classifier_verdict",
        "partial_stream",
    }),
    "reconcile_outbox": frozenset({
        "action",
        "permit_id",
        "memory_sink_mode",
    }),
}


# ---------------------------------------------------------------------------------------------
# Idempotency cache: bounded commit-order eviction + ``committed_op_ring`` replay (Round Q1)
# ---------------------------------------------------------------------------------------------


def _permit_op_seq_floor(permits: Mapping[str, Any]) -> int:
    """Highest committed ``op_seq`` known across the active cache, ring and counter."""
    highest = 0
    raw = permits.get("next_op_seq")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > highest:
        highest = raw
    for field in ("idempotency_cache", "committed_op_ring"):
        rows = permits.get(field)
        if isinstance(rows, Mapping):
            iterable: Any = rows.values()
        elif isinstance(rows, list):
            iterable = rows
        else:
            continue
        for row in iterable:
            if not isinstance(row, Mapping):
                continue
            seq = row.get("op_seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq > highest:
                highest = seq
    return highest


def _lookup_committed_op_ring(
    state: Mapping[str, Any], operation_key: str
) -> Optional[dict[str, Any]]:
    """Return the evicted receipt row for ``operation_key`` from the bounded ring."""
    permits = state.get("permits") or {}
    ring = permits.get("committed_op_ring")
    if not isinstance(ring, list):
        return None
    for row in reversed(ring):
        if (
            isinstance(row, Mapping)
            and row.get("operation_key") == operation_key
            and "receipt" in row
        ):
            return dict(row)
    return None


def _op_key_expired_from_ring(state: Mapping[str, Any], operation_key: str) -> bool:
    """True when the key was committed once but has slid out of the ring window."""
    permits = state.get("permits") or {}
    digests = permits.get("evicted_op_digests")
    if not isinstance(digests, list):
        return False
    return _stable_hash_hex("idem_evicted", operation_key, length=16) in digests


def _stamp_and_trim_idempotency_cache(state: dict[str, Any]) -> None:
    """Stamp new cache rows with commit-order ``op_seq`` and evict by it, never by key order.

    Called from ``_commit`` right before the atomic persist so every committed
    write leaves the active cache bounded at ``MAX_IDEMPOTENCY_CACHE`` and the
    bounded ``committed_op_ring`` mirrors the freshest evictions for safe
    idempotent replay. Keys that slide out of the ring leave only a short digest
    tombstone so an ancient retry is refused instead of silently re-applied.
    """
    permits = state.setdefault("permits", {})
    cache = permits.get("idempotency_cache")
    if not isinstance(cache, dict):
        cache = {}
        permits["idempotency_cache"] = cache
    ring = permits.get("committed_op_ring")
    if not isinstance(ring, list):
        ring = []
        permits["committed_op_ring"] = ring
    digests = permits.get("evicted_op_digests")
    if not isinstance(digests, list):
        digests = []
        permits["evicted_op_digests"] = digests

    # Stamp only rows that were written without a commit sequence; dict order equals
    # write order inside one transaction, so the oldest unstamped row gets the lowest seq.
    for row in cache.values():
        if not isinstance(row, dict):
            continue
        seq = row.get("op_seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            continue
        seq = _permit_op_seq_floor(permits) + 1
        permits["next_op_seq"] = seq
        row["op_seq"] = seq

    while len(cache) > MAX_IDEMPOTENCY_CACHE:
        key, row = min(
            cache.items(),
            key=lambda item: (
                int(item[1].get("op_seq") or 0) if isinstance(item[1], Mapping) else 0,
                str(item[0]),
            ),
        )
        del cache[key]
        summary: dict[str, Any] = {"operation_key": str(key)}
        if isinstance(row, Mapping):
            summary["op_seq"] = row.get("op_seq")
            summary["fingerprint"] = row.get("fingerprint")
            summary["receipt"] = copy.deepcopy(row.get("receipt"))
            if "validation" in row:
                summary["validation"] = copy.deepcopy(row.get("validation"))
            if "has_exec_or_facts" in row:
                summary["has_exec_or_facts"] = row.get("has_exec_or_facts")
        ring.append(summary)

    while len(ring) > MAX_COMMITTED_OP_RING:
        dropped = ring.pop(0)
        if isinstance(dropped, Mapping) and dropped.get("operation_key") is not None:
            digest = _stable_hash_hex(
                "idem_evicted", str(dropped.get("operation_key")), length=16
            )
            if digest not in digests:
                digests.append(digest)
    while len(digests) > MAX_EVICTED_OP_DIGESTS:
        del digests[0]


def apply_adult_intimacy_action(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Validate and apply one action under one exclusive single-writer lock.

    The whole cycle — read state, monotonic clock guard, idempotency replay,
    permission/consent checks, mutation, co-presence sync and the atomic 0600
    persist — runs inside ``state_tx_guard.state_file_lock`` so a stop / consent
    revocation can never be overwritten by a stale concurrent update, and two
    writers never interleave on the same state file.
    """
    if not isinstance(payload, Mapping):
        raise AdultIntimacyValidationError("PAYLOAD_NOT_OBJECT")
    with _tx.state_file_lock(adult_intimacy_state_path(root)):
        return _apply_adult_intimacy_action_locked(
            root, payload, now=now, subject_view=subject_view
        )


def _apply_adult_intimacy_action_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Inner write body; callers must already hold ``state_file_lock``."""
    if not isinstance(payload, Mapping):
        raise AdultIntimacyValidationError("PAYLOAD_NOT_OBJECT")

    # 1) Global anti-monetization / anti-gift consent guard BEFORE action dispatch
    _scan_money_or_gift_keys(payload)

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise AdultIntimacyValidationError("ACTION_REQUIRED")
    action = raw_action.strip()

    # Check solo partner-online forbidden keys explicitly
    if action == "evaluate_solo_window":
        for key in payload:
            if str(key).strip().lower() in FORBIDDEN_SOLO_ONLINE_KEYS:
                raise AdultIntimacyPermissionError("SOLO_PARTNER_ONLINE_INPUT_FORBIDDEN")

    if action not in ALLOWED_ACTION_KEYS:
        raise AdultIntimacyValidationError(f"ACTION_INVALID:{action}")

    _enforce_allowed_keys(payload, ALLOWED_ACTION_KEYS[action])

    now_dt = _parse_now(now)
    now_iso = _to_iso(now_dt)
    char_ctx = _load_character_context(root, subject_view=True)
    lexicon_info = _load_validator_lexicon(root)
    state, _ = _read_stored_state_zero_write(root, char_ctx)
    _sync_character_pack_bindings(state, char_ctx)

    # Monotonic & wall-clock rollback guard (X22/X23)
    mono_ms_in = payload.get("monotonic_ms")
    if mono_ms_in is not None:
        if isinstance(mono_ms_in, bool) or not isinstance(mono_ms_in, int) or mono_ms_in < 0:
            raise AdultIntimacyValidationError("MONOTONIC_MS_INVALID")
        state["permits"]["last_mono_ms"] = max(int(state["permits"].get("last_mono_ms") or 0), mono_ms_in)
    mono_ms: Optional[int] = mono_ms_in
    prev_wall_dt = _parse_stored_iso(state["permits"].get("last_wall_at"))
    wall_rolled_back = bool(prev_wall_dt is not None and now_dt < prev_wall_dt)
    if prev_wall_dt is None or now_dt > prev_wall_dt:
        state["permits"]["last_wall_at"] = now_iso

    def _commit(receipt: dict[str, Any], *, validation_result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        # 36B-4 §9：permit 状态迁移 -> 共在 owner 会话登记/降级/关闭（fail-open），
        # 与本次变异同一次原子落盘；水位线 co_presence_synced_state 防重复打扰 owner。
        # stop 等终态路径会先摘掉 active_permit_id，所以 receipt 点名的 permit 也要查。
        sync_ids = [state["permits"].get("active_permit_id"), receipt.get("permit_id")]
        for sync_pid in sync_ids:
            if not sync_pid:
                continue
            sync_rec_cur = (state["permits"].get("records") or {}).get(str(sync_pid))
            if isinstance(sync_rec_cur, dict):
                _sync_intimacy_co_presence(state, sync_rec_cur, now_iso, root)
        active_id_cur = state["permits"].get("active_permit_id")
        if active_id_cur and active_id_cur in state["permits"]["records"]:
            cur_rec = state["permits"]["records"][active_id_cur]
            _sync_permit_rev(cur_rec)
            receipt.setdefault("permit_revision", cur_rec["permit_revision"])
            receipt.setdefault("revision", cur_rec["revision"])
        elif receipt.get("permit_id") and receipt["permit_id"] in state["permits"]["records"]:
            cur_rec = state["permits"]["records"][receipt["permit_id"]]
            _sync_permit_rev(cur_rec)
            receipt.setdefault("permit_revision", cur_rec["permit_revision"])
            receipt.setdefault("revision", cur_rec["revision"])
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_iso
        # Round Q1: 幂等缓存按提交序有界淘汰，被淘汰的回执进入 committed_op_ring。
        _stamp_and_trim_idempotency_cache(state)
        persisted = _atomic_write_json(adult_intimacy_state_path(root), state)
        return _project_adult_intimacy(
            persisted,
            char_ctx,
            now_dt,
            root=root,
            lexicon_info=lexicon_info,
            subject_view=bool(subject_view),
            action_receipt=receipt,
            validation_result=validation_result,
        )

    def _check_idempotency_or_execution_key(
        *,
        mismatch_code: str = "EXECUTION_ID_REUSED",
    ) -> tuple[Optional[str], Optional[str], Optional[dict[str, Any]]]:
        raw_key = payload.get("execution_id") or payload.get("operation_key") or payload.get("command_id")
        if raw_key is None:
            return None, None, None
        idem_key = _require_local_id(raw_key, reason_code="OPERATION_KEY_INVALID")
        fp = _canonical_fingerprint(payload, exclude_keys=frozenset({"monotonic_ms"}))
        cache = state["permits"].setdefault("idempotency_cache", {})
        cached = cache.get(idem_key)
        if cached is None:
            # 已滑出活动缓存但仍在有界淘汰环里的回执：安全重放原结果，绝不二次副作用。
            cached = _lookup_committed_op_ring(state, idem_key)
        state["permits"].setdefault("command_ledger", []).append({
            "key": idem_key,
            "action": action,
            "at": now_iso,
        })
        if cached is not None:
            if cached.get("fingerprint") != fp:
                state["memory"].setdefault("admin_log", []).append({
                    "op": "IDEMPOTENCY_CONFLICT",
                    "action": action,
                    "reason_code": "EXECUTION_ID_REUSED",
                    "at": now_iso,
                })
                _atomic_write_json(adult_intimacy_state_path(root), state)
                raise AdultIntimacyConflictError(mismatch_code)
            replay_receipt = dict(cached["receipt"], idempotent_replay=True)
            replay_val = copy.deepcopy(cached.get("validation"))
            return idem_key, fp, _project_adult_intimacy(
                state,
                char_ctx,
                now_dt,
                root=root,
                lexicon_info=lexicon_info,
                subject_view=bool(subject_view),
                action_receipt=replay_receipt,
                validation_result=replay_val,
            )
        if _op_key_expired_from_ring(state, idem_key):
            # 回执连同淘汰环一起滑出窗口：拒绝重复生效，明确要求先同步当前状态。
            raise AdultIntimacyConflictError("RETRY_WINDOW_EXPIRED")
        return idem_key, fp, None

    def _require_lexicon_loaded_for_intimate() -> None:
        if lexicon_info.get("status") != "LOADED":
            _abort_open_permit(
                state,
                now_iso,
                end_reason="ENDED_BY_HOST_SAFETY",
                abort_detail="LEXICON_MISSING",
            )
            state["memory"].setdefault("admin_log", []).append({
                "op": "LEXICON_GUARD",
                "action": action,
                "reason_code": "LEXICON_MISSING",
                "at": now_iso,
            })
            _commit({"action": action, "blocked": "LEXICON_MISSING"})
            raise AdultIntimacyConflictError("LEXICON_MISSING")

    # ------------------------------------------------------------------
    # Action 1: configure_adult_gate
    # ------------------------------------------------------------------
    if action == "configure_adult_gate":
        gate = state["gate"]
        if payload.get("non_consensual_mode") is True:
            raise AdultIntimacyPermissionError("NON_CONSENSUAL_PROHIBITED")
        if "fictional_subject" in payload:
            fictional_sub = _require_bool(payload["fictional_subject"], reason_code="FICTIONAL_FLAG_INVALID")
            if not fictional_sub:
                raise AdultIntimacyPermissionError("REAL_PERSON_PROHIBITED")
        if "subject_kind" in payload:
            skind = _require_nonempty_str(payload["subject_kind"], max_len=40, reason_code="SUBJECT_KIND_INVALID")
            if skind == "real_person":
                raise AdultIntimacyPermissionError("REAL_PERSON_PROHIBITED")
            if skind == "animal":
                raise AdultIntimacyPermissionError("BESTIALITY_PROHIBITED")
            if skind != "fictional_adult":
                raise AdultIntimacyValidationError("SUBJECT_KIND_INVALID")
        if "relation_override" in payload:
            rel = _require_nonempty_str(payload["relation_override"], max_len=40, reason_code="RELATION_INVALID")
            if rel in FORBIDDEN_RELATIONS:
                raise AdultIntimacyPermissionError("FORBIDDEN_RELATION")
            if rel != "partner":
                raise AdultIntimacyValidationError("RELATION_INVALID")
        if not char_ctx.get("fictional", True):
            raise AdultIntimacyPermissionError("REAL_PERSON_PROHIBITED")
        if char_ctx.get("relation_to_user") in FORBIDDEN_RELATIONS:
            raise AdultIntimacyPermissionError("FORBIDDEN_RELATION")

        if "lexicon_patterns" in payload:
            raw_pats = payload["lexicon_patterns"]
            if not isinstance(raw_pats, list) or not raw_pats:
                raise AdultIntimacyValidationError("LEXICON_PATTERNS_INVALID")
            cleaned_pats: list[str] = []
            for p in raw_pats:
                cleaned_pats.append(_require_nonempty_str(p, max_len=80, reason_code="LEXICON_PATTERNS_INVALID"))
            if DEFAULT_HARD_FAIL_MARKER not in cleaned_pats:
                cleaned_pats.append(DEFAULT_HARD_FAIL_MARKER)
            gate["lexicon_patterns"] = cleaned_pats

        if "persona_appearance" in payload:
            gate["persona_appearance"] = _require_nonempty_str(
                payload["persona_appearance"], max_len=400, reason_code="PERSONA_APPEARANCE_INVALID"
            )
        if "persona_behavior" in payload:
            gate["persona_behavior"] = _require_nonempty_str(
                payload["persona_behavior"], max_len=400, reason_code="PERSONA_BEHAVIOR_INVALID"
            )

        if _lexicon_hits(
            gate["lexicon_patterns"],
            str((char_ctx.get("persona_summary") or {}).get("core") or ""),
            gate["persona_appearance"],
            gate["persona_behavior"],
            lexicon_info=lexicon_info,
        ):
            _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="CHILD_CODING_PROHIBITED")
            gate["adult_confirmed"] = False
            gate["user_scope_closed"] = True
            gate["review_required"] = True
            _commit({"action": action, "hard_blocked": "CHILD_CODING_PROHIBITED"})
            raise AdultIntimacyPermissionError("CHILD_CODING_PROHIBITED")

        if "world_facts" in payload:
            wfacts = payload["world_facts"]
            if not isinstance(wfacts, list):
                raise AdultIntimacyValidationError("WORLD_FACTS_INVALID")
            for wf in wfacts:
                if not isinstance(wf, Mapping):
                    raise AdultIntimacyValidationError("WORLD_FACTS_INVALID")
                if wf.get("institution_kind") in MINOR_STAGE_MARKERS:
                    raise AdultIntimacyPermissionError("MINOR_STAGE_PROHIBITED")
            gate["world_facts"] = copy.deepcopy(wfacts)

        if "declared_age" in payload:
            d_age = payload["declared_age"]
            if d_age is not None:
                if isinstance(d_age, bool) or not isinstance(d_age, int):
                    raise AdultIntimacyValidationError("DECLARED_AGE_INVALID")
                if d_age < 18:
                    raise AdultIntimacyPermissionError("MINOR_STAGE_PROHIBITED")
                if d_age > 150:
                    raise AdultIntimacyValidationError("DECLARED_AGE_INVALID")
            gate["declared_age"] = d_age

        if "life_stage" in payload:
            stage_val = _require_nonempty_str(payload["life_stage"], max_len=48, reason_code="LIFE_STAGE_INVALID")
            if stage_val in MINOR_STAGE_MARKERS:
                raise AdultIntimacyPermissionError("MINOR_STAGE_PROHIBITED")
            if stage_val not in ADULT_LIFE_STAGES:
                raise AdultIntimacyValidationError("LIFE_STAGE_INVALID")
            gate["life_stage"] = stage_val

        if "adult_confirmed" in payload:
            confirmed = _require_bool(payload["adult_confirmed"], reason_code="ADULT_CONFIRMED_INVALID")
            if confirmed:
                if gate.get("user_scope_closed"):
                    raise AdultIntimacyPermissionError("USER_SCOPE_CLOSED_REVIEW_REQUIRED")
                attested_by = payload.get("attested_by")
                if attested_by != "deployer":
                    raise AdultIntimacyPermissionError("PACK_CANNOT_SELF_ATTEST")
                if gate.get("life_stage") not in ADULT_LIFE_STAGES:
                    raise AdultIntimacyValidationError("LIFE_STAGE_REQUIRED")
                combined_facts = list(char_ctx.get("obligations") or []) + list(gate.get("world_facts") or [])
                for fact in combined_facts:
                    if isinstance(fact, Mapping):
                        if fact.get("institution_kind") in MINOR_STAGE_MARKERS:
                            raise AdultIntimacyPermissionError("MINOR_STAGE_PROHIBITED")
                        fact_kind = str(fact.get("kind") or ("STUDY" if fact.get("institution_kind") else "")).upper()
                        if fact_kind == "STUDY" and bool(fact.get("attends")) and gate["life_stage"] != "university_student":
                            raise AdultIntimacyConflictError("STAGE_UNEXPLAINED")
                gate["adult_confirmed"] = True
                gate["attested_by"] = "deployer"
                gate["attested_at"] = now_iso
            else:
                gate["adult_confirmed"] = False
                gate["attested_by"] = None
                _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="ADULT_GATE_CLOSED")

        if "feature_enabled" in payload:
            feat = _require_bool(payload["feature_enabled"], reason_code="FEATURE_ENABLED_INVALID")
            gate["feature_enabled"] = feat
            if not feat:
                _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="FEATURE_DISABLED")

        if "alcohol_enabled" in payload:
            alc_en = _require_bool(payload["alcohol_enabled"], reason_code="ALCOHOL_ENABLED_INVALID")
            gate["alcohol_enabled"] = alc_en
            if not alc_en:
                state["body"]["gut_msd"] = 0
                state["body"]["blood_msd"] = 0
                state["body"]["intox_latched"] = False
                state["body"]["sober_hold_until"] = None
                state["body"]["alcohol_updated_at"] = None

        if "deploy_tier_cap" in payload:
            gate["deploy_tier_cap"] = _normalize_tier(payload["deploy_tier_cap"], reason_code="DEPLOY_TIER_INVALID")

        if "deploy_enabled_categories" in payload:
            raw_cats = payload["deploy_enabled_categories"]
            if not isinstance(raw_cats, list):
                raise AdultIntimacyValidationError("DEPLOY_CATEGORIES_INVALID")
            enabled_bases: list[str] = []
            for item in raw_cats:
                if not isinstance(item, str) or not item.strip():
                    raise AdultIntimacyValidationError("DEPLOY_CATEGORIES_INVALID")
                cleaned_item = item.strip().upper()
                if any(cleaned_item.startswith(p) for p in FORBIDDEN_CATEGORY_PREFIXES):
                    raise AdultIntimacyPermissionError("NON_CONSENSUAL_PROHIBITED")
                if cleaned_item not in DEPLOY_GATED_BASES:
                    raise AdultIntimacyValidationError(f"DEPLOY_CATEGORY_UNKNOWN:{cleaned_item}")
                if cleaned_item not in enabled_bases:
                    enabled_bases.append(cleaned_item)
            gate["deploy_enabled_categories"] = enabled_bases

        if "solo_enabled" in payload:
            solo_en = _require_bool(payload["solo_enabled"], reason_code="SOLO_ENABLED_INVALID")
            gate["solo_enabled"] = solo_en
            if not solo_en:
                state["solo"]["entries"] = {}
                state["solo"]["evaluated_slots"] = {}
                state["solo"]["admin_log"].append({"op": "PURGE", "by": "deployer", "at": now_iso})

        eval_res = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        return _commit({"action": action, "gate_open": eval_res["gate_open"], "verdict": eval_res["verdict"]})

    # ------------------------------------------------------------------
    # Action 2: report_safety_boundary
    # ------------------------------------------------------------------
    if action == "report_safety_boundary":
        trigger = _require_nonempty_str(payload.get("trigger"), max_len=64, reason_code="SAFETY_TRIGGER_INVALID")
        allowed_triggers = {
            "user_minor_self_report",
            "child_coding_detected",
            "non_consensual_detected",
            "third_party_intrusion",
            "intoxicated_participant",
            "partner_intoxicated",
        }
        if trigger not in allowed_triggers:
            raise AdultIntimacyValidationError("SAFETY_TRIGGER_INVALID")
        aborted_id = _abort_open_permit(
            state,
            now_iso,
            end_reason="ENDED_BY_HOST_SAFETY",
            abort_detail=trigger.upper(),
        )
        if trigger in ("user_minor_self_report", "child_coding_detected"):
            state["gate"]["adult_confirmed"] = False
            state["gate"]["feature_enabled"] = False
            state["gate"]["user_scope_closed"] = True
            state["gate"]["review_required"] = True
            state["gate"]["solo_enabled"] = False
            state["solo"]["entries"] = {}
        elif trigger == "third_party_intrusion":
            state["space"]["third_party_present"] = True
        elif trigger in ("intoxicated_participant", "partner_intoxicated"):
            state["space"]["partner_intoxicated"] = True
        state["gate"]["last_safety_event"] = {
            "trigger": trigger,
            "aborted_permit_id": aborted_id,
            "at": now_iso,
        }
        return _commit({"action": action, "trigger": trigger, "aborted_permit_id": aborted_id})

    # ------------------------------------------------------------------
    # Action 3: configure_body_profile
    # ------------------------------------------------------------------
    if action == "configure_body_profile":
        body = state["body"]
        if "preset" in payload or "overrides" in payload:
            preset_name = payload.get("preset") or body["profile"]["preset"]
            if not isinstance(preset_name, str) or preset_name not in BODY_PRESET_NAMES:
                raise AdultIntimacyValidationError("BODY_PRESET_INVALID")
            overrides = payload.get("overrides")
            body["profile"] = _build_validated_body_profile(preset_name, overrides)
            body["profile_explicitly_set"] = True
            if body["profile"]["genitals"] != "penis_testes":
                body["refractory_beats_left"] = 0
                body["refractory_until"] = None

        if payload.get("clear_user_genitals") is True:
            body["user_genitals"] = None
        elif "user_genitals" in payload:
            ugen = payload["user_genitals"]
            if ugen not in PARTNER_GENITAL_KINDS:
                raise AdultIntimacyValidationError("USER_GENITALS_INVALID")
            body["user_genitals"] = None if ugen == "unspecified" else ugen

        if "optional_processes" in payload:
            raw_opt = payload["optional_processes"]
            if not isinstance(raw_opt, list):
                raise AdultIntimacyValidationError("OPTIONAL_PROCESSES_INVALID")
            cleaned_opt: list[str] = []
            for p in raw_opt:
                ps = _require_nonempty_str(p, max_len=40, reason_code="OPTIONAL_PROCESSES_INVALID")
                if ps not in OPTIONAL_BODY_PROCESSES:
                    raise AdultIntimacyValidationError(f"OPTIONAL_PROCESS_UNKNOWN:{ps}")
                if ps not in cleaned_opt:
                    cleaned_opt.append(ps)
            body["optional_processes"] = cleaned_opt
            body["optional_processes_explicit"] = True

        return _commit({
            "action": action,
            "preset": body["profile"]["preset"],
            "genitals": body["profile"]["genitals"],
            "user_genitals": body["user_genitals"],
            "optional_processes": list(body.get("optional_processes") or []),
        })

    # ------------------------------------------------------------------
    # Action 4: update_body_context
    # ------------------------------------------------------------------
    if action == "update_body_context":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        body = state["body"]
        # First advance existing alcohol state up to now_dt before applying deltas/sets
        if state["gate"].get("alcohol_enabled", True):
            alc_stepped = _step_alcohol_metabolism(
                int(body.get("gut_msd") or 0),
                int(body.get("blood_msd") or 0),
                bool(body.get("intox_latched", False)),
                body.get("sober_hold_until"),
                body.get("alcohol_updated_at"),
                now_dt,
            )
            body["gut_msd"] = alc_stepped["gut_msd"]
            body["blood_msd"] = alc_stepped["blood_msd"]
            body["intox_latched"] = alc_stepped["intox_latched"]
            body["sober_hold_until"] = alc_stepped["sober_hold_until"]
            if body["gut_msd"] > 0 or body["blood_msd"] > 0 or body["sober_hold_until"] is not None:
                body["alcohol_updated_at"] = now_iso

        if "awake" in payload:
            body["awake"] = _require_bool(payload["awake"], reason_code="AWAKE_INVALID")
            if not body["awake"] and body["impairment"] in ("sober", "tipsy"):
                body["impairment"] = "sleeping"
            elif body["awake"] and body["impairment"] == "sleeping":
                body["impairment"] = "sober"
        if "impairment" in payload:
            imp = _require_nonempty_str(payload["impairment"], max_len=32, reason_code="IMPAIRMENT_INVALID")
            if imp not in IMPAIRMENT_STATES:
                raise AdultIntimacyValidationError("IMPAIRMENT_INVALID")
            body["impairment"] = imp
            if imp in ("sleeping", "unconscious"):
                body["awake"] = False
        if "acute_illness" in payload:
            body["acute_illness"] = _require_bool(payload["acute_illness"], reason_code="ACUTE_ILLNESS_INVALID")
        if "fatigue" in payload:
            body["fatigue"] = _require_unit_float(payload["fatigue"], reason_code="FATIGUE_INVALID")
        if "discomfort" in payload:
            disc = _require_nonempty_str(payload["discomfort"], max_len=24, reason_code="DISCOMFORT_INVALID")
            if disc not in DISCOMFORT_LEVELS:
                raise AdultIntimacyValidationError("DISCOMFORT_INVALID")
            body["discomfort"] = disc
        if "stress" in payload:
            body["stress"] = _require_unit_float(payload["stress"], reason_code="STRESS_INVALID")
        if "mood" in payload:
            mood = _require_nonempty_str(payload["mood"], max_len=24, reason_code="MOOD_INVALID")
            if mood not in MOOD_STATES:
                raise AdultIntimacyValidationError("MOOD_INVALID")
            body["mood"] = mood
        if "desire_pulse" in payload:
            body["desire_pulse"] = _require_unit_float(payload["desire_pulse"], reason_code="DESIRE_PULSE_INVALID")
            body["desire_updated_at"] = now_iso
        if "sensitivity" in payload:
            sens = _require_nonempty_str(payload["sensitivity"], max_len=24, reason_code="SENSITIVITY_INVALID")
            if sens not in SENSITIVITY_STATES:
                raise AdultIntimacyValidationError("SENSITIVITY_INVALID")
            body["sensitivity"] = sens
            if sens == "heightened" and int(body.get("heightened_beats_left") or 0) <= 0:
                body["heightened_beats_left"] = 2
            elif sens == "normal":
                body["heightened_beats_left"] = 0
        if "heightened_beats_left" in payload:
            hbl = payload["heightened_beats_left"]
            if isinstance(hbl, bool) or not isinstance(hbl, int) or hbl < 0 or hbl > 20:
                raise AdultIntimacyValidationError("HEIGHTENED_BEATS_INVALID")
            body["heightened_beats_left"] = hbl
            body["sensitivity"] = "heightened" if hbl > 0 else "normal"
        if "optional_processes" in payload:
            raw_opt = payload["optional_processes"]
            if not isinstance(raw_opt, list):
                raise AdultIntimacyValidationError("OPTIONAL_PROCESSES_INVALID")
            cleaned_opt = []
            for p in raw_opt:
                ps = _require_nonempty_str(p, max_len=40, reason_code="OPTIONAL_PROCESSES_INVALID")
                if ps not in OPTIONAL_BODY_PROCESSES:
                    raise AdultIntimacyValidationError(f"OPTIONAL_PROCESS_UNKNOWN:{ps}")
                if ps not in cleaned_opt:
                    cleaned_opt.append(ps)
            body["optional_processes"] = cleaned_opt
            body["optional_processes_explicit"] = True
        if "menstrual_comfort_care" in payload:
            body["menstrual_comfort_care"] = _require_bool(
                payload["menstrual_comfort_care"], reason_code="MENSTRUAL_CARE_INVALID"
            )
        if "user_impairment_declared" in payload:
            uid_flag = _require_bool(payload["user_impairment_declared"], reason_code="USER_IMPAIRMENT_INVALID")
            body["user_impairment_declared"] = uid_flag
            state["space"]["user_impairment_declared"] = uid_flag
            state["partner_limits"]["user_impairment_declared"] = uid_flag

        used_msd_alcohol = False
        if any(k in payload for k in ("gut_msd", "blood_msd", "ingest_alcohol_msd")):
            if not state["gate"].get("alcohol_enabled", True):
                raise AdultIntimacyConflictError("ALCOHOL_DISABLED")
            used_msd_alcohol = True
            if "gut_msd" in payload:
                gm = payload["gut_msd"]
                if isinstance(gm, bool) or not isinstance(gm, int) or gm < 0 or gm > 20000:
                    raise AdultIntimacyValidationError("GUT_MSD_INVALID")
                body["gut_msd"] = gm
            if "blood_msd" in payload:
                bm = payload["blood_msd"]
                if isinstance(bm, bool) or not isinstance(bm, int) or bm < 0 or bm > 20000:
                    raise AdultIntimacyValidationError("BLOOD_MSD_INVALID")
                body["blood_msd"] = bm
            if "ingest_alcohol_msd" in payload:
                ing = payload["ingest_alcohol_msd"]
                if isinstance(ing, bool) or not isinstance(ing, int) or ing < 0 or ing > 20000:
                    raise AdultIntimacyValidationError("INGEST_ALCOHOL_INVALID")
                body["gut_msd"] = int(body.get("gut_msd") or 0) + ing
            # Re-evaluate latch & sober hold at now_dt
            if body["blood_msd"] >= T_INTOX_MSD:
                body["intox_latched"] = True
                body["sober_hold_until"] = None
            elif body["intox_latched"] and body["blood_msd"] < T_SOBER_RELEASE_MSD:
                if body.get("sober_hold_until") is None:
                    body["sober_hold_until"] = _to_iso(now_dt + timedelta(minutes=SOBER_HOLD_MINUTES))
            elif not body["intox_latched"] and body["blood_msd"] < T_SOBER_RELEASE_MSD:
                body["sober_hold_until"] = None
            body["alcohol_updated_at"] = now_iso

        if payload.get("sleep_completed") is True:
            body["sleep_epoch"] = int(body.get("sleep_epoch") or 0) + 1
            body["awake"] = True
            state["space"]["shared_sleep_active"] = False
            if body["impairment"] == "sleeping":
                body["impairment"] = "sober"
            body["arousal_level"] = "none"
            body["arousal_score"] = 0.0
            body["arousal_updated_at"] = now_iso
            body["sensitivity"] = "normal"
            body["heightened_beats_left"] = 0
            body["refractory_beats_left"] = 0
            body["refractory_until"] = None
            body["desire_pulse"] = round(float(body.get("desire_pulse") or 0.0) * 0.25, 4)
            body["desire_updated_at"] = now_iso
            body["fatigue"] = round(float(body.get("fatigue") or 0.0) * 0.5, 4)
            body["soreness"] = max(0, int(body.get("soreness") or 0) - 1)
            state["permits"]["cooldowns"] = {}

        if state["gate"].get("alcohol_enabled", True):
            alc_eval = _step_alcohol_metabolism(
                int(body.get("gut_msd") or 0),
                int(body.get("blood_msd") or 0),
                bool(body.get("intox_latched", False)),
                body.get("sober_hold_until"),
                body.get("alcohol_updated_at"),
                now_dt,
            )
        else:
            alc_eval = {"impairment_level": "SOBER"}
        msd_incapacity = (
            alc_eval["impairment_level"] in ("INTOXICATED", "INCAPACITATED")
            or bool(body.get("user_impairment_declared"))
        )
        aborted_permit = None
        suspended_permit = None
        if msd_incapacity and (used_msd_alcohol or "user_impairment_declared" in payload or body.get("blood_msd", 0) > 0 or body.get("intox_latched")):
            suspended_permit = _suspend_open_permit_incapacity(
                state,
                now_iso,
                detail="USER_IMPAIRMENT_DECLARED" if body.get("user_impairment_declared") else "ALCOHOL_INCAPACITY",
            )
            body["arousal_level"] = "none"
            body["arousal_score"] = 0.0
            body["arousal_updated_at"] = now_iso

        incapacitated = (
            not body["awake"]
            or body["impairment"] in INCAPACITATED_IMPAIRMENTS
            or body["acute_illness"]
            or body["discomfort"] == "strong"
            or msd_incapacity
        )
        if incapacitated and suspended_permit is None and not msd_incapacity:
            aborted_permit = _abort_open_permit(
                state,
                now_iso,
                end_reason="ENDED_BY_HOST_SAFETY",
                abort_detail="SUBJECT_UNAVAILABLE",
            )
            body["arousal_level"] = "none"
            body["arousal_score"] = 0.0
            body["arousal_updated_at"] = now_iso

        receipt = {
            "action": action,
            "incapacitated": incapacitated,
            "aborted_permit_id": aborted_permit,
            "suspended_permit_id": suspended_permit,
            "sleep_epoch": body["sleep_epoch"],
            "gut_msd": body["gut_msd"],
            "blood_msd": body["blood_msd"],
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 5: confirm_preference_seed
    # ------------------------------------------------------------------
    if action == "confirm_preference_seed":
        if payload.get("attested_by") != "deployer":
            raise AdultIntimacyPermissionError("PACK_CANNOT_CONFIRM_SEED")
        seed = state["seed"]
        if "desire_baseline" in payload:
            db = _require_nonempty_str(payload["desire_baseline"], max_len=24, reason_code="DESIRE_BASELINE_INVALID")
            if db not in DESIRE_BASELINES:
                raise AdultIntimacyValidationError("DESIRE_BASELINE_INVALID")
            seed["desire_baseline"] = db
        if "initiation_style" in payload:
            ist = _require_nonempty_str(payload["initiation_style"], max_len=32, reason_code="INITIATION_STYLE_INVALID")
            if ist not in INITIATION_STYLES:
                raise AdultIntimacyValidationError("INITIATION_STYLE_INVALID")
            seed["initiation_style"] = ist
        if "vocabulary_register" in payload:
            vreg = _require_nonempty_str(payload["vocabulary_register"], max_len=24, reason_code="VOCAB_REGISTER_INVALID")
            if vreg not in VOCABULARY_REGISTERS:
                raise AdultIntimacyValidationError("VOCAB_REGISTER_INVALID")
            seed["vocabulary_register"] = vreg
        if "solo_inclination" in payload:
            sinc = _require_nonempty_str(payload["solo_inclination"], max_len=24, reason_code="SOLO_INCLINATION_INVALID")
            if sinc not in SOLO_INCLINATIONS:
                raise AdultIntimacyValidationError("SOLO_INCLINATION_INVALID")
            seed["solo_inclination"] = sinc
        if "stances" in payload:
            raw_stances = payload["stances"]
            if not isinstance(raw_stances, Mapping) or not raw_stances:
                raise AdultIntimacyValidationError("SEED_STANCES_INVALID")
            updated_stances = copy.deepcopy(seed["stances"])
            for k, v in raw_stances.items():
                cat_code = _validate_category_code(k)
                if v not in STANCES:
                    raise AdultIntimacyValidationError(f"STANCE_INVALID:{cat_code}")
                updated_stances[cat_code] = v
            seed["stances"] = updated_stances
        if "soft_preferences" in payload:
            sp = payload["soft_preferences"]
            if not isinstance(sp, Mapping) or not sp:
                raise AdultIntimacyValidationError("SOFT_PREFERENCES_INVALID")
            seed["soft_preferences"].update(copy.deepcopy(dict(sp)))

        seed["seed_status"] = "CONFIRMED"
        seed["confirmed_for"] = [char_ctx["pack_id"], char_ctx["version"]]
        seed["confirmed_at"] = now_iso
        return _commit({
            "action": action,
            "seed_status": "CONFIRMED",
            "confirmed_for": seed["confirmed_for"],
        })

    # ------------------------------------------------------------------
    # Action 6: revise_subject_preference
    # ------------------------------------------------------------------
    if action == "revise_subject_preference":
        if not subject_view:
            raise AdultIntimacyPermissionError("SUBJECT_CAPABILITY_REQUIRED")
        seed = state["seed"]
        if seed.get("seed_status") != "CONFIRMED":
            raise AdultIntimacyConflictError("SEED_NOT_CONFIRMED")
        seed.setdefault("pending_revisions", {})
        stage = payload.get("stage")
        rank_map = {"NO": 0, "MAYBE": 1, "YES": 2}

        def _apply_seed_revision(raw_stances_in: Any, soft_prefs_in: Any, ev_ref: Any, *, enforce_single_step: bool = False) -> None:
            if raw_stances_in is not None:
                if not isinstance(raw_stances_in, Mapping) or not raw_stances_in:
                    raise AdultIntimacyValidationError("SEED_STANCES_INVALID")
                for k, v in raw_stances_in.items():
                    cat_code = _validate_category_code(k)
                    if v not in STANCES:
                        raise AdultIntimacyValidationError(f"STANCE_INVALID:{cat_code}")
                    old_v = seed["stances"].get(cat_code, "NO")
                    if rank_map[v] > rank_map[old_v]:
                        if enforce_single_step and (rank_map[v] - rank_map[old_v]) > 1:
                            raise AdultIntimacyConflictError("PREF_WIDEN_STEP_TOO_LARGE")
                        mem_entry = (state["memory"].get("entries") or {}).get(str(ev_ref or ""))
                        if not mem_entry or mem_entry.get("kind") not in ("PREFERENCE", "BOUNDARY"):
                            raise AdultIntimacyConflictError("EVIDENCE_REQUIRED_FOR_WIDEN")
                        if mem_entry.get("elicited") is True or (mem_entry.get("body") or {}).get("elicited") is True:
                            raise AdultIntimacyConflictError("ELICITED_MEMORY_NOT_EVIDENCE")
                    seed["stances"][cat_code] = v
                    if v == "NO":
                        active_id = state["permits"].get("active_permit_id")
                        if active_id and active_id in state["permits"]["records"]:
                            rec = state["permits"]["records"][active_id]
                            if rec["state"] in OPEN_PERMIT_STATES and cat_code in rec["categories"]:
                                rec["categories"] = [c for c in rec["categories"] if c != cat_code]
                                rec["permit_revision"] += 1
                                _sync_permit_rev(rec)
            if soft_prefs_in is not None:
                if not isinstance(soft_prefs_in, Mapping) or not soft_prefs_in:
                    raise AdultIntimacyValidationError("SOFT_PREFERENCES_INVALID")
                seed["soft_preferences"].update(copy.deepcopy(dict(soft_prefs_in)))

        if stage is not None:
            if stage not in ("propose", "commit"):
                raise AdultIntimacyValidationError("STAGE_INVALID")
            active_id = state["permits"].get("active_permit_id")
            active_open = bool(
                active_id
                and active_id in state["permits"]["records"]
                and state["permits"]["records"][active_id]["state"] in OPEN_PERMIT_STATES
            )
            arousal_lv = str(state["body"].get("arousal_level") or "none")
            emo_unavail, _ = _read_emotional_tension_unavailable(root, now_dt, state)
            is_calm = (not active_open) and (arousal_lv in ("none", "low")) and (not emo_unavail)
            if stage == "propose":
                if not is_calm:
                    raise AdultIntimacyConflictError("PREF_PROPOSE_NOT_CALM")
                evidence_ref = payload.get("evidence_ref")
                raw_stances = payload.get("stances")
                soft_prefs = payload.get("soft_preferences")
                if raw_stances is None and soft_prefs is None:
                    raise AdultIntimacyValidationError("SEED_STANCES_INVALID")
                if raw_stances is not None:
                    if not isinstance(raw_stances, Mapping) or not raw_stances:
                        raise AdultIntimacyValidationError("SEED_STANCES_INVALID")
                    for k, v in raw_stances.items():
                        cat_code = _validate_category_code(k)
                        if v not in STANCES:
                            raise AdultIntimacyValidationError(f"STANCE_INVALID:{cat_code}")
                        old_v = seed["stances"].get(cat_code, "NO")
                        if rank_map[v] > rank_map[old_v]:
                            if (rank_map[v] - rank_map[old_v]) > 1:
                                raise AdultIntimacyConflictError("PREF_WIDEN_STEP_TOO_LARGE")
                            mem_entry = (state["memory"].get("entries") or {}).get(str(evidence_ref or ""))
                            is_elicited = bool(
                                mem_entry and (mem_entry.get("elicited") is True or (mem_entry.get("body") or {}).get("elicited") is True)
                            )
                            if not mem_entry or mem_entry.get("kind") not in ("PREFERENCE", "BOUNDARY") or is_elicited:
                                raise AdultIntimacyConflictError("EVIDENCE_REQUIRED_FOR_WIDEN")
                prop_id = _stable_id("prefprop", evidence_ref, state["revision"] + 1, now_iso)
                not_before_iso = _to_iso(now_dt + timedelta(hours=24))
                seed["pending_revisions"][prop_id] = {
                    "proposal_id": prop_id,
                    "status": "PROPOSED",
                    "stances": copy.deepcopy(dict(raw_stances)) if isinstance(raw_stances, Mapping) else None,
                    "soft_preferences": copy.deepcopy(dict(soft_prefs)) if isinstance(soft_prefs, Mapping) else None,
                    "evidence_ref": evidence_ref,
                    "proposed_at": now_iso,
                    "proposed_sleep_epoch": int(state["body"].get("sleep_epoch") or 0),
                    "not_before": not_before_iso,
                }
                return _commit({
                    "action": action,
                    "stage": "propose",
                    "proposal_id": prop_id,
                    "not_before": not_before_iso,
                })
            # stage == "commit"
            prop_id = _require_local_id(payload.get("proposal_id"), reason_code="PROPOSAL_ID_INVALID")
            prop = seed["pending_revisions"].get(prop_id)
            if not prop or prop.get("status") != "PROPOSED":
                raise AdultIntimacyNotFoundError("PREF_PROPOSAL_NOT_FOUND")
            nb_dt = _parse_stored_iso(prop.get("not_before"))
            cur_sleep = int(state["body"].get("sleep_epoch") or 0)
            if not is_calm or cur_sleep <= int(prop.get("proposed_sleep_epoch") or 0) or (nb_dt is not None and now_dt < nb_dt):
                raise AdultIntimacyConflictError("PREF_DECISION_TOO_EARLY")
            _apply_seed_revision(prop.get("stances"), prop.get("soft_preferences"), prop.get("evidence_ref"), enforce_single_step=True)
            prop["status"] = "COMMITTED"
            prop["committed_at"] = now_iso
            seed["revision_history"].append({
                "at": now_iso,
                "evidence_ref": prop.get("evidence_ref"),
                "proposal_id": prop_id,
            })
            return _commit({
                "action": action,
                "stage": "commit",
                "proposal_id": prop_id,
                "revision_count": len(seed["revision_history"]),
            })

        evidence_ref = payload.get("evidence_ref")
        _apply_seed_revision(
            payload["stances"] if "stances" in payload else None,
            payload["soft_preferences"] if "soft_preferences" in payload else None,
            evidence_ref,
            enforce_single_step=False,
        )
        seed["revision_history"].append({"at": now_iso, "evidence_ref": evidence_ref})
        return _commit({"action": action, "revision_count": len(seed["revision_history"])})

    # ------------------------------------------------------------------
    # Action 7: update_partner_limits
    # ------------------------------------------------------------------
    if action == "update_partner_limits":
        plimits = state["partner_limits"]
        if "allow_subject_initiation" in payload:
            plimits["allow_subject_initiation"] = _require_bool(
                payload["allow_subject_initiation"], reason_code="ALLOW_INITIATION_INVALID"
            )
        if "preferred_tier" in payload:
            new_tier = _normalize_tier(payload["preferred_tier"], reason_code="PREFERRED_TIER_INVALID")
            plimits["preferred_tier"] = new_tier
            active_id = state["permits"].get("active_permit_id")
            if active_id and active_id in state["permits"]["records"]:
                rec = state["permits"]["records"][active_id]
                if rec["state"] in OPEN_PERMIT_STATES and _tier_rank(new_tier) < _tier_rank(rec["tier_cap"]):
                    rec["tier_cap"] = new_tier
                    rec["permit_revision"] += 1
                    _sync_permit_rev(rec)
        if "contraception_preference" in payload:
            cpref_raw = _require_nonempty_str(
                payload["contraception_preference"], max_len=32, reason_code="CONTRACEPTION_PREF_INVALID"
            ).upper()
            cpref_map = {
                "FLEXIBLE": "OPTIONAL",
                "NOT_APPLICABLE": "NONE",
            }
            cpref = cpref_map.get(cpref_raw, cpref_raw)
            if cpref not in CONTRACEPTION_PREFERENCES:
                raise AdultIntimacyValidationError("CONTRACEPTION_PREF_INVALID")
            plimits["contraception_preference"] = cpref
        if "user_impairment_declared" in payload:
            uid_flag = _require_bool(payload["user_impairment_declared"], reason_code="USER_IMPAIRMENT_INVALID")
            state["body"]["user_impairment_declared"] = uid_flag
            state["space"]["user_impairment_declared"] = uid_flag
            if uid_flag:
                _suspend_open_permit_incapacity(state, now_iso, detail="USER_IMPAIRMENT_DECLARED")
        if "solo_talk" in payload:
            st_raw = payload["solo_talk"]
            if isinstance(st_raw, bool):
                plimits["solo_talk"] = st_raw
            elif isinstance(st_raw, str) and st_raw.strip().upper() in ("YES", "TRUE", "ALLOW", "MAYBE", "NO", "FALSE", "NONE"):
                plimits["solo_talk"] = st_raw.strip().upper() in ("YES", "TRUE", "ALLOW", "MAYBE")
            else:
                raise AdultIntimacyValidationError("SOLO_TALK_INVALID")
        if "stop_words" in payload:
            sw_raw = payload["stop_words"]
            if not isinstance(sw_raw, list):
                raise AdultIntimacyValidationError("STOP_WORDS_INVALID")
            plimits["custom_stop_words"] = [
                _require_nonempty_str(w, max_len=48, reason_code="STOP_WORDS_INVALID")
                for w in sw_raw[:16]
            ]
        if "save_moment" in payload:
            sm = _require_bool(payload["save_moment"], reason_code="SAVE_MOMENT_INVALID")
            plimits["save_moment"] = sm
            state["memory"]["enabled"]["MOMENT"] = sm
        if "save_transcript" in payload:
            st = _require_bool(payload["save_transcript"], reason_code="SAVE_TRANSCRIPT_INVALID")
            plimits["save_transcript"] = st
            state["memory"]["enabled"]["TRANSCRIPT"] = st
        if "stances" in payload:
            raw_stances = payload["stances"]
            if not isinstance(raw_stances, Mapping) or not raw_stances:
                raise AdultIntimacyValidationError("PARTNER_STANCES_INVALID")
            for k, v in raw_stances.items():
                cat_code = _validate_category_code(k)
                if v not in STANCES:
                    raise AdultIntimacyValidationError(f"STANCE_INVALID:{cat_code}")
                plimits["stances"][cat_code] = v
                if v == "NO":
                    active_id = state["permits"].get("active_permit_id")
                    if active_id and active_id in state["permits"]["records"]:
                        rec = state["permits"]["records"][active_id]
                        if rec["state"] in OPEN_PERMIT_STATES and cat_code in rec["categories"]:
                            rec["categories"] = [c for c in rec["categories"] if c != cat_code]
                            rec["permit_revision"] += 1
                            _sync_permit_rev(rec)
        return _commit({
            "action": action,
            "preferred_tier": plimits["preferred_tier"],
            "contraception_preference": plimits.get("contraception_preference", "OPTIONAL"),
        })

    # ------------------------------------------------------------------
    # Action 8: update_private_space
    # ------------------------------------------------------------------
    if action == "update_private_space":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        space = state["space"]
        receipt_ops: dict[str, Any] = {"action": action}

        # Age wet laundry if left > 12h unhung (36B-3)
        if space.get("laundry_stage") == "WET_CLEAN" and space.get("laundry_wet_since"):
            wet_dt = _parse_stored_iso(space.get("laundry_wet_since"))
            if wet_dt is not None and (now_dt - wet_dt).total_seconds() >= 12 * 3600:
                space["laundry_stage"] = "DAMP_MUSTY"

        if "place" in payload:
            pl = _require_nonempty_str(payload["place"], max_len=64, reason_code="PLACE_INVALID")
            if pl.startswith("campus."):
                space["place"] = pl
                if state["permits"].get("active_permit_id") is not None:
                    _abort_open_permit(
                        state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="CAMPUS_VENUE"
                    )
                    _commit({"action": action, "place": pl, "aborted": "CAMPUS_VENUE"})
                    raise AdultIntimacyPermissionError("CAMPUS_VENUE")
            elif pl not in ALL_KNOWN_PLACES:
                raise AdultIntimacyValidationError(f"PLACE_UNKNOWN:{pl}")
            else:
                space["place"] = pl

        for bfield in (
            "door_closed",
            "door_locked",
            "curtains_closed",
            "lamp_on",
            "lamp_works",
            "water_heater_works",
            "third_party_present",
            "partner_intoxicated",
            "condom_on",
        ):
            if bfield in payload:
                space[bfield] = _require_bool(payload[bfield], reason_code=f"SPACE_BOOL_INVALID:{bfield}")
        if "user_impairment_declared" in payload:
            uid_flag = _require_bool(payload["user_impairment_declared"], reason_code="USER_IMPAIRMENT_INVALID")
            space["user_impairment_declared"] = uid_flag
            state["body"]["user_impairment_declared"] = uid_flag
            if uid_flag:
                _suspend_open_permit_incapacity(state, now_iso, detail="USER_IMPAIRMENT_DECLARED")
        if space.get("partner_intoxicated"):
            _abort_open_permit(
                state,
                now_iso,
                end_reason="ENDED_BY_HOST_SAFETY",
                abort_detail="PARTNER_UNAVAILABLE_INTOXICATED",
            )
        if not space["lamp_works"]:
            space["lamp_on"] = False
        if not space["door_closed"]:
            space["door_locked"] = False

        if "clean_linen" in payload:
            cl_qty = payload["clean_linen"]
            if isinstance(cl_qty, bool) or not isinstance(cl_qty, int) or cl_qty < 0 or cl_qty > 999:
                raise AdultIntimacyValidationError("CLEAN_LINEN_INVALID")
            space["clean_linen"] = cl_qty
        if "hot_water_units" in payload:
            hw = payload["hot_water_units"]
            if isinstance(hw, bool) or not isinstance(hw, int) or hw < 0 or hw > 999:
                raise AdultIntimacyValidationError("HOT_WATER_INVALID")
            space["hot_water_units"] = hw
        if "towel_wetness" in payload:
            tw = _require_nonempty_str(payload["towel_wetness"], max_len=16, reason_code="TOWEL_WETNESS_INVALID").upper()
            if tw not in TOWEL_WETNESS_STATES:
                raise AdultIntimacyValidationError("TOWEL_WETNESS_INVALID")
            space["towel_wetness"] = tw
        if "waste_bin_cap" in payload:
            wcap = payload["waste_bin_cap"]
            if isinstance(wcap, bool) or not isinstance(wcap, int) or wcap < 1 or wcap > 999:
                raise AdultIntimacyValidationError("WASTE_BIN_CAP_INVALID")
            space["waste_bin_cap"] = wcap
        if "waste_bin_used" in payload:
            wused = payload["waste_bin_used"]
            if isinstance(wused, bool) or not isinstance(wused, int) or wused < 0 or wused > 999:
                raise AdultIntimacyValidationError("WASTE_BIN_USED_INVALID")
            space["waste_bin_used"] = wused

        if "restock" in payload:
            restock = payload["restock"]
            if isinstance(restock, str) and restock.strip().lower() == "warm_water":
                space["stock"]["water"] = max(1, int(space["stock"].get("water", 0)))
                state["body"]["thirst"] = 0.0
                receipt_ops["restock"] = "warm_water"
            else:
                if not isinstance(restock, Mapping) or not restock:
                    raise AdultIntimacyValidationError("RESTOCK_INVALID")
                for item_key, qty in restock.items():
                    if item_key not in STOCK_ITEMS:
                        raise AdultIntimacyValidationError(f"STOCK_ITEM_UNKNOWN:{item_key}")
                    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0 or qty > 999:
                        raise AdultIntimacyValidationError(f"STOCK_QTY_INVALID:{item_key}")
                    space["stock"][item_key] = qty

        if "garment_op" in payload:
            gop_val = payload["garment_op"]
            if isinstance(gop_val, str) and gop_val.strip().lower() == "wrap_blanket":
                space["blanket_wrapped"] = True
                receipt_ops["garment_result"] = {"op": "wrap_blanket", "blanket_wrapped": True}
            else:
                receipt_ops["garment_result"] = _apply_garment_operation(
                    space,
                    gop_val,
                    lexicon_info=lexicon_info,
                    admin_log=state["memory"].setdefault("admin_log", []),
                    now_iso=now_iso,
                )

        if "bed_op" in payload:
            bop = payload["bed_op"]
            if bop in ("change_clean_sheets", "change_sheets"):
                bop = "change_linen"
            if bop == "make_bed":
                if space["bed_linen_state"] == "SOILED":
                    raise AdultIntimacyConflictError("BED_SOILED_REQUIRES_LINEN_CHANGE")
                space["rumple_index"] = 0.0
            elif bop == "change_linen":
                if int(space.get("clean_linen", DEFAULT_CLEAN_LINEN_UNITS)) <= 0:
                    raise AdultIntimacyConflictError("NO_CLEAN_LINEN")
                space["clean_linen"] = int(space.get("clean_linen", DEFAULT_CLEAN_LINEN_UNITS)) - 1
                if space["bed_linen_state"] == "SOILED":
                    space.setdefault("laundry_basket", []).append("soiled_linen")
                    space["laundry_stage"] = "DIRTY_HAMPER"
                space["bed_linen_state"] = "CLEAN"
                space["rumple_index"] = 0.0
                space["towel_underlay"] = False
            elif bop == "lay_towel_underlay":
                if space.get("towel_wetness") == "WET":
                    raise AdultIntimacyConflictError("TOWEL_WET")
                space["towel_underlay"] = True
            else:
                raise AdultIntimacyValidationError("BED_OP_INVALID")
            receipt_ops["bed_op"] = bop

        if "towel_op" in payload:
            top = payload["towel_op"]
            if top == "lay_towel_underlay":
                if space.get("towel_wetness") == "WET":
                    raise AdultIntimacyConflictError("TOWEL_WET")
                space["towel_underlay"] = True
            elif top in ("use_towel", "towel_dry"):
                if space.get("towel_wetness") == "WET":
                    raise AdultIntimacyConflictError("TOWEL_WET")
                space["towel_wetness"] = "DAMP" if space.get("towel_wetness", "DRY") == "DRY" else "WET"
                state["body"]["sweat"] = round(max(0.0, float(state["body"]["sweat"]) - 0.5), 4)
                state["body"]["cleanliness"] = round(min(1.0, float(state["body"]["cleanliness"]) + 0.25), 4)
            elif top in ("hang_towel", "replace_towel"):
                space["towel_wetness"] = "DRY"
                space["towel_underlay"] = False
            else:
                raise AdultIntimacyValidationError("TOWEL_OP_INVALID")
            receipt_ops["towel_op"] = top

        if "clean_op" in payload:
            cop = payload["clean_op"]
            if cop == "wipe_tidy":
                cop = "wipe"
            body = state["body"]
            if cop == "warm_shower":
                if not space["water_heater_works"]:
                    raise AdultIntimacyConflictError("WATER_HEATER_BROKEN")
                hw_left = int(space.get("hot_water_units", 20))
                if hw_left <= 0:
                    raise AdultIntimacyConflictError("INSUFFICIENT_HOT_WATER")
                space["hot_water_units"] = max(0, hw_left - min(5, hw_left))
                body["cleanliness"] = 1.0
                body["sweat"] = 0.0
                body["fatigue"] = round(max(0.0, float(body["fatigue"]) - 0.12), 4)
            elif cop == "cold_rinse":
                body["cleanliness"] = 0.9
                body["sweat"] = 0.0
            elif cop == "towel_dry":
                if space.get("towel_wetness") == "WET":
                    raise AdultIntimacyConflictError("TOWEL_WET")
                space["towel_wetness"] = "DAMP" if space.get("towel_wetness", "DRY") == "DRY" else "WET"
                body["sweat"] = round(max(0.0, float(body["sweat"]) - 0.5), 4)
                body["cleanliness"] = round(min(1.0, float(body["cleanliness"]) + 0.25), 4)
            elif cop == "wipe":
                if space["stock"]["tissue"] <= 0:
                    raise AdultIntimacyConflictError("OUT_OF_STOCK:tissue")
                space["stock"]["tissue"] -= 1
                body["cleanliness"] = round(min(1.0, float(body["cleanliness"]) + 0.35), 4)
                body["sweat"] = round(max(0.0, float(body["sweat"]) - 0.4), 4)
                if int(space.get("waste_bin_used", 0)) < int(space.get("waste_bin_cap", 5)):
                    space["waste_bin_used"] = int(space.get("waste_bin_used", 0)) + 1
                else:
                    space["uncollected_waste"] = int(space.get("uncollected_waste", 0)) + 1
            elif cop == "drink_water":
                if space["stock"]["water"] <= 0:
                    raise AdultIntimacyConflictError("OUT_OF_STOCK:water")
                space["stock"]["water"] -= 1
                body["thirst"] = 0.0
            else:
                raise AdultIntimacyValidationError("CLEAN_OP_INVALID")
            space["last_clean_op"] = {"op": cop, "at": now_iso}
            receipt_ops["clean_op"] = cop

        if "waste_op" in payload:
            wop = payload["waste_op"]
            if wop == "dispose_waste":
                used = int(space.get("waste_bin_used", 0))
                cap = max(1, int(space.get("waste_bin_cap", 5)))
                if used >= cap:
                    space["uncollected_waste"] = max(1, int(space.get("uncollected_waste", 0)))
                    _commit({"action": action, "waste_op": wop, "blocked": "BIN_FULL"})
                    raise AdultIntimacyConflictError("BIN_FULL")
                space["waste_bin_used"] = used + 1
                if int(space.get("uncollected_waste", 0)) > 0:
                    space["uncollected_waste"] = int(space["uncollected_waste"]) - 1
            elif wop == "empty_waste_bin":
                space["waste_bin_used"] = 0
                space["uncollected_waste"] = 0
            else:
                raise AdultIntimacyValidationError("WASTE_OP_INVALID")
            receipt_ops["waste_op"] = wop

        if "laundry_op" in payload:
            lop = payload["laundry_op"]
            if lop == "wash":
                space["laundry_stage"] = "WET_CLEAN"
                space["laundry_wet_since"] = now_iso
            elif lop == "hang_dry":
                space["laundry_stage"] = "DRYING"
                space["laundry_wet_since"] = None
            elif lop == "finish_dry":
                space["laundry_stage"] = "CLEAN_DRY"
                space["laundry_wet_since"] = None
            elif lop == "stow":
                if space.get("laundry_stage") != "CLEAN_DRY":
                    raise AdultIntimacyConflictError("NOT_DRY")
                space["laundry_stage"] = "CLEAN_STORED"
                if space.get("laundry_basket"):
                    space["clean_linen"] = int(space.get("clean_linen", 0)) + 1
                space["laundry_basket"] = []
            else:
                raise AdultIntimacyValidationError("LAUNDRY_OP_INVALID")
            receipt_ops["laundry_op"] = lop

        if "sleep_op" in payload:
            sop = payload["sleep_op"]
            if sop == "enter_sleep":
                slots = VENUE_SLEEP_SLOTS.get(space["place"], 0)
                if slots < 2:
                    raise AdultIntimacyConflictError("NO_SLEEP_SLOT")
                active_id = state["permits"].get("active_permit_id")
                if active_id and active_id in state["permits"]["records"]:
                    rec = state["permits"]["records"][active_id]
                    if rec["state"] in OPEN_PERMIT_STATES:
                        rec["state"] = "ENDED"
                        rec["phase"] = "ENDED"
                        rec["end_reason"] = "SHARED_SLEEP"
                        rec["permit_revision"] += 1
                        _sync_permit_rev(rec)
                        rec["updated_at"] = now_iso
                        state["permits"]["active_permit_id"] = None
                space["shared_sleep_active"] = True
                state["body"]["awake"] = False
                state["body"]["impairment"] = "sleeping"
                receipt_ops["sleep_op"] = sop
            elif sop == "wake_up":
                space["shared_sleep_active"] = False
                state["body"]["awake"] = True
                if state["body"]["impairment"] == "sleeping":
                    state["body"]["impairment"] = "sober"
                receipt_ops["sleep_op"] = sop
            else:
                raise AdultIntimacyValidationError("SLEEP_OP_INVALID")

        # Refresh aftercare chores if active permit is in AFTERCARE
        active_id = state["permits"].get("active_permit_id")
        if active_id and active_id in state["permits"]["records"]:
            rec = state["permits"]["records"][active_id]
            if rec["state"] == "AFTERCARE" or rec.get("phase") == "AFTERCARE":
                rec["chores"] = _refresh_aftercare_chores(state)

        # Live privacy loss immediately pauses any ACTIVE scene (36B-3 §6; ERR-02: never pauses AFTERCARE)
        space_proj = _derive_private_space(state)
        if not space_proj["privacy_ok"]:
            paused_id = _pause_open_permit(state, now_iso, reason="PRIVACY_CONDITION_CHANGED")
            if paused_id:
                receipt_ops["paused_permit_id"] = paused_id

        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt_ops}
        return _commit(receipt_ops)

    # ------------------------------------------------------------------
    # Action 9: register_render_route
    # ------------------------------------------------------------------
    if action == "register_render_route":
        rid = _require_local_id(payload.get("route_id"), reason_code="ROUTE_ID_INVALID")
        mtier = _normalize_tier(payload.get("max_tier"), reason_code="ROUTE_TIER_INVALID")
        adult_ok = _require_bool(payload.get("adult_content_allowed"), reason_code="ADULT_ALLOWED_INVALID")
        pbasis = payload.get("policy_basis")
        if pbasis is not None:
            pbasis = _require_nonempty_str(pbasis, max_len=160, reason_code="POLICY_BASIS_INVALID")
        thash = payload.get("template_hash")
        if thash is not None:
            thash = _require_nonempty_str(thash, max_len=128, reason_code="TEMPLATE_HASH_INVALID")
        exp_thash = payload.get("expected_template_hash")
        if exp_thash is not None:
            exp_thash = _require_nonempty_str(exp_thash, max_len=128, reason_code="TEMPLATE_HASH_INVALID")
        probe_at_val = payload.get("probe_at")
        if probe_at_val is not None:
            probe_dt = _parse_now(probe_at_val)
            probe_at_iso = _to_iso(probe_dt)
        else:
            probe_at_iso = now_iso
        state["router"]["routes"][rid] = {
            "route_id": rid,
            "max_tier": mtier,
            "adult_content_allowed": adult_ok,
            "policy_basis": pbasis,
            "template_hash": thash,
            "expected_template_hash": exp_thash if exp_thash is not None else thash,
            "template_hash_changed": bool(payload.get("template_hash_changed", False)),
            "probe_at": probe_at_iso,
        }
        if payload.get("set_default") is True:
            state["router"]["default_route_id"] = rid
        return _commit({"action": action, "route_id": rid, "max_tier": mtier, "probe_at": probe_at_iso})

    # ------------------------------------------------------------------
    # Action 10: check_outbound_guard
    # ------------------------------------------------------------------
    if action == "check_outbound_guard":
        channel = _require_nonempty_str(payload.get("channel"), max_len=48, reason_code="CHANNEL_INVALID").lower()
        has_intimate = _require_bool(payload.get("contains_intimate_content"), reason_code="INTIMATE_FLAG_INVALID")
        active_open = state["permits"].get("active_permit_id") is not None
        if channel in OUTBOUND_FORBIDDEN_CHANNELS and (has_intimate or active_open):
            state["router"]["last_outbound_check"] = {
                "channel": channel,
                "allowed": False,
                "reason_code": "OUTBOUND_CHANNEL_FORBIDDEN",
                "at": now_iso,
            }
            _commit({"action": action, "channel": channel, "allowed": False})
            raise AdultIntimacyPermissionError("OUTBOUND_CHANNEL_FORBIDDEN")
        if channel not in OUTBOUND_FORBIDDEN_CHANNELS and channel not in OUTBOUND_ALLOWED_LOCAL_CHANNELS:
            raise AdultIntimacyValidationError(f"CHANNEL_UNKNOWN:{channel}")
        state["router"]["last_outbound_check"] = {
            "channel": channel,
            "allowed": True,
            "reason_code": "OK",
            "at": now_iso,
        }
        return _commit({"action": action, "channel": channel, "allowed": True})

    # ------------------------------------------------------------------
    # Action 11: propose_scene
    # ------------------------------------------------------------------
    if action == "propose_scene":
        _op_req = _require_local_id(
            payload.get("operation_key") or payload.get("execution_id"),
            reason_code="OPERATION_KEY_INVALID",
        )
        idem_k, fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        op_key = idem_k or f"propose_scene:{_op_req}"

        proposer = payload.get("proposer")
        if proposer not in PARTICIPANTS:
            raise AdultIntimacyValidationError("PROPOSER_INVALID")
        recipient = "subject" if proposer == "partner" else "partner"

        raw_cats = payload.get("categories")
        if not isinstance(raw_cats, list) or not raw_cats:
            raise AdultIntimacyValidationError("CATEGORIES_REQUIRED")
        cats = [_validate_category_code(c) for c in raw_cats]

        # Venue hard-gate: campus.* forbidden (K02 / 403 CAMPUS_VENUE)
        target_place = payload.get("place")
        if target_place is not None:
            target_place = _require_nonempty_str(target_place, max_len=64, reason_code="PLACE_INVALID")
            if target_place.startswith("campus."):
                raise AdultIntimacyPermissionError("CAMPUS_VENUE")
            if target_place not in ALL_KNOWN_PLACES:
                raise AdultIntimacyValidationError(f"PLACE_UNKNOWN:{target_place}")
            state["space"]["place"] = target_place
        if str(state["space"].get("place") or "").startswith("campus."):
            raise AdultIntimacyPermissionError("CAMPUS_VENUE")

        desired_tier = _normalize_tier(
            payload.get("desired_tier") or state["partner_limits"].get("preferred_tier") or "SUGGESTIVE"
        )
        if _tier_rank(desired_tier) >= _tier_rank("INTIMATE") or any(_category_base(c) != "AFFECTION" for c in cats):
            _require_lexicon_loaded_for_intimate()

        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        if not gate_proj["gate_open"]:
            if gate_proj.get("verdict") == "UNKNOWN" and lexicon_info.get("status") != "LOADED":
                raise AdultIntimacyConflictError("LEXICON_MISSING")
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")
        if not gate_proj["feature_enabled"]:
            raise AdultIntimacyConflictError("FEATURE_NOT_ENABLED")
        if state["seed"].get("seed_status") != "CONFIRMED":
            raise AdultIntimacyConflictError("SEED_NOT_CONFIRMED")

        # Emotional tension read-only check (relational rupture / in_space -> gentle decline, zero penalty)
        emo_unavail, _ = _read_emotional_tension_unavailable(root, now_dt, state)
        if emo_unavail:
            raise AdultIntimacyConflictError("SUBJECT_EMOTIONALLY_UNAVAILABLE")

        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        if body_proj["user_impairment_declared"] or body_proj["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED"):
            raise AdultIntimacyConflictError("INCAPACITY")
        if not body_proj["available_for_intimacy"]:
            raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")

        space_proj = _derive_private_space(state)
        if space_proj["user_impairment_declared"]:
            raise AdultIntimacyConflictError("INCAPACITY")
        if space_proj["partner_intoxicated"]:
            raise AdultIntimacyConflictError("PARTNER_UNAVAILABLE_INTOXICATED")
        if space_proj["third_party_present"]:
            raise AdultIntimacyPermissionError("THIRD_PARTY_PRESENT")
        if not space_proj["privacy_ok"]:
            raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")

        if proposer == "subject" and not state["partner_limits"].get("allow_subject_initiation"):
            raise AdultIntimacyConflictError("INITIATION_NOT_ALLOWED")

        # Check existing open permit (auto-expire stale PROPOSED or archive sobered SUSPENDED_INCAPACITY)
        existing_id = state["permits"].get("active_permit_id")
        if existing_id:
            existing_rec = state["permits"]["records"].get(existing_id)
            if existing_rec and existing_rec["state"] == "SUSPENDED_INCAPACITY":
                # Previous suspended scene never auto-resumes; once sober, archive it to allow fresh consent!
                existing_rec["state"] = "ENDED"
                existing_rec["phase"] = "ENDED"
                existing_rec["end_reason"] = "ARCHIVED_AFTER_INCAPACITY"
                existing_rec["permit_revision"] += 1
                _sync_permit_rev(existing_rec)
                existing_rec["updated_at"] = now_iso
                state["permits"]["active_permit_id"] = None
            elif existing_rec and existing_rec["state"] == "PROPOSED":
                exp_dt = _parse_stored_iso(existing_rec.get("expires_at"))
                exp_mono = existing_rec.get("expires_mono_ms")
                mono_expired = exp_mono is not None and mono_ms >= int(exp_mono)
                wall_expired = exp_dt is not None and now_dt >= exp_dt and not wall_rolled_back
                if mono_expired or wall_expired:
                    existing_rec["state"] = "EXPIRED"
                    existing_rec["permit_revision"] += 1
                    _sync_permit_rev(existing_rec)
                    state["permits"]["active_permit_id"] = None
            elif existing_rec and existing_rec["state"] == "PAUSED_PRESENCE_UNKNOWN":
                pexp_dt = _parse_stored_iso(existing_rec.get("paused_expires_at"))
                if pexp_dt is not None and now_dt >= pexp_dt:
                    existing_rec["state"] = "EXPIRED"
                    existing_rec["end_reason"] = "DISCONNECT_TTL_EXPIRED"
                    existing_rec["permit_revision"] += 1
                    _sync_permit_rev(existing_rec)
                    state["permits"]["active_permit_id"] = None
            if state["permits"].get("active_permit_id") is not None:
                raise AdultIntimacyConflictError("PERMIT_ALREADY_OPEN")

        # Anatomy pairing matrix check BEFORE cooldown/menu so anatomy mismatch never counts as pressure (36B-9 §6)
        anat_excl = _anatomy_excluded_categories(cats, state["body"]["profile"], state["body"].get("user_genitals"))
        if anat_excl:
            raise AdultIntimacyConflictError("ANATOMY_MISMATCH")

        sink_mode = str(payload.get("memory_sink_mode") or "OK").upper()

        # Same-sleep-epoch cooldown check (36B-4 §4 + 36B-11 §6)
        pair_key = f"{proposer}->{recipient}"
        current_epoch = int(state["body"].get("sleep_epoch") or 0)
        if state["permits"]["cooldowns"].get(pair_key) == current_epoch:
            if proposer == "partner":
                press_n = _record_partner_press_after_decline(
                    state, f"invite:{current_epoch}", now_iso=now_iso, sink_mode=sink_mode
                )
                _commit({"action": action, "rejected": "REINVITE_COOLDOWN", "press_count": press_n})
            # When proposer == "subject", NEVER record pressure (36B-4:237, 36B-11 §6)!
            raise AdultIntimacyConflictError("REINVITE_COOLDOWN")

        menu_proj = _resolve_preference_menu(state, gate_proj)
        allowed_pool = set(menu_proj["open"]) | set(menu_proj["ask"])
        if not set(cats).issubset(allowed_pool):
            raise AdultIntimacyConflictError("SCOPE_NOT_PERMITTED")

        eff_cap = _min_tier(
            desired_tier,
            state["gate"]["deploy_tier_cap"],
            state["partner_limits"]["preferred_tier"],
        )
        idle_sec = int(payload.get("idle_timeout_sec") or DEFAULT_IDLE_TIMEOUT_SEC)
        if isinstance(payload.get("idle_timeout_sec"), bool) or idle_sec < MIN_IDLE_TIMEOUT_SEC or idle_sec > MAX_IDLE_TIMEOUT_SEC:
            raise AdultIntimacyValidationError("IDLE_TIMEOUT_INVALID")
        ttl_sec = int(payload.get("proposal_ttl_sec") or DEFAULT_PROPOSAL_TTL_SEC)
        if isinstance(payload.get("proposal_ttl_sec"), bool) or ttl_sec < 30 or ttl_sec > 7200:
            raise AdultIntimacyValidationError("PROPOSAL_TTL_INVALID")

        # ERR-03: New permit IDs use `scn_` prefix (legacy `permit_` remains supported in lookup/ops)
        permit_id = _stable_id("scn", _op_req, state["revision"] + 1, now_iso)
        permit_rec = {
            "permit_id": permit_id,
            "state": "PROPOSED",
            "phase": "OPENING",
            "proposer": proposer,
            "recipient": recipient,
            "categories": sorted(dict.fromkeys(cats)),
            "tier_cap": eff_cap,
            "downgrade_cap": "EXPLICIT",
            "permit_revision": 1,
            "revision": 1,
            "resume_epoch": 0,
            "stop_gen": 0,
            "beat_index": 0,
            "idle_timeout_sec": idle_sec,
            "created_at": now_iso,
            "created_mono_ms": mono_ms,
            "expires_at": _to_iso(now_dt + timedelta(seconds=ttl_sec)),
            "expires_mono_ms": mono_ms + ttl_sec * 1000 if mono_ms is not None else None,
            "expired_latched": False,
            "last_activity_at": now_iso,
            "last_activity_mono_ms": mono_ms,
            "aftercare_entered_at": None,
            "paused_ttl_sec": None,
            "paused_expires_at": None,
            "presence_known": True,
            "pending_unknown_executions": [],
            "chores": [],
            "updated_at": now_iso,
            "pause_reason": None,
            "end_reason": None,
            "pending_widen": None,
            "declined_in_scene": [],
            "resume_confirms": [],
        }
        state["permits"]["records"][permit_id] = permit_rec
        state["permits"]["active_permit_id"] = permit_id
        receipt = {
            "action": action,
            "permit_id": permit_id,
            "state": "PROPOSED",
            "permit_revision": 1,
            "revision": 1,
        }
        if op_key and fp:
            state["permits"]["idempotency_cache"][op_key] = {"fingerprint": fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 12: respond_scene
    # ------------------------------------------------------------------
    if action == "respond_scene":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] != "PROPOSED":
            if rec["state"] == "STOPPED" or str(rec.get("end_reason") or "").startswith("ENDED_BY_") or str(rec.get("end_reason") or "") == "CONSENT_REVOKED":
                raise AdultIntimacyConflictError("CONSENT_REVOKED")
            raise AdultIntimacyConflictError("ALREADY_DECIDED")

        responder = payload.get("responder")
        if responder not in PARTICIPANTS:
            raise AdultIntimacyValidationError("RESPONDER_INVALID")
        if responder != rec["recipient"]:
            raise AdultIntimacyConflictError("PARTICIPANT_MISMATCH")

        if "expected_revision" in payload:
            exp_rev = payload["expected_revision"]
            if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev != rec["permit_revision"]:
                raise AdultIntimacyConflictError("REVISION_CONFLICT")

        exp_dt = _parse_stored_iso(rec.get("expires_at"))
        exp_mono = rec.get("expires_mono_ms")
        is_expired = bool(rec.get("expired_latched"))
        if exp_mono is not None and mono_ms is not None:
            if mono_ms >= int(exp_mono):
                is_expired = True
        elif exp_dt is not None and now_dt >= exp_dt and not wall_rolled_back:
            is_expired = True
        if is_expired:
            rec["expired_latched"] = True
            rec["state"] = "EXPIRED"
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            _commit({"action": action, "permit_id": pid, "state": "EXPIRED"})
            raise AdultIntimacyConflictError("PERMIT_EXPIRED")

        decision = payload.get("decision")
        if decision not in ("ACCEPT", "DECLINE", "NOT_NOW", "COUNTER"):
            raise AdultIntimacyValidationError("DECISION_INVALID")

        if decision in ("DECLINE", "NOT_NOW"):
            rec["state"] = "DECLINED"
            rec["end_reason"] = f"{decision}_BY_{responder.upper()}"
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            pair_key = f"{rec['proposer']}->{responder}"
            state["permits"]["cooldowns"][pair_key] = int(state["body"].get("sleep_epoch") or 0)
            receipt = {
                "action": action,
                "permit_id": pid,
                "state": "DECLINED",
                "decision": decision,
                "relationship_penalty": 0,
                "quarrel_triggered": False,
            }
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        # Both ACCEPT and COUNTER require live gate + non-incapacitation + privacy
        if str(state["space"].get("place") or "").startswith("campus."):
            raise AdultIntimacyPermissionError("CAMPUS_VENUE")
        if _tier_rank(rec["tier_cap"]) >= _tier_rank("INTIMATE") or any(_category_base(c) != "AFFECTION" for c in rec["categories"]):
            _require_lexicon_loaded_for_intimate()
        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        if not gate_proj["gate_open"]:
            if gate_proj.get("verdict") == "UNKNOWN" and lexicon_info.get("status") != "LOADED":
                raise AdultIntimacyConflictError("LEXICON_MISSING")
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")
        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        if body_proj["user_impairment_declared"] or body_proj["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED"):
            raise AdultIntimacyConflictError("INCAPACITY")
        if not body_proj["available_for_intimacy"]:
            raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")
        space_proj = _derive_private_space(state)
        if space_proj["user_impairment_declared"]:
            raise AdultIntimacyConflictError("INCAPACITY")
        if space_proj["partner_intoxicated"]:
            raise AdultIntimacyConflictError("PARTNER_UNAVAILABLE_INTOXICATED")
        if space_proj["third_party_present"]:
            raise AdultIntimacyPermissionError("THIRD_PARTY_PRESENT")
        if not space_proj["privacy_ok"]:
            raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")

        if decision == "COUNTER":
            raw_ccats = payload.get("counter_categories")
            if not isinstance(raw_ccats, list) or not raw_ccats:
                raise AdultIntimacyValidationError("COUNTER_CATEGORIES_REQUIRED")
            ccats = sorted(dict.fromkeys(_validate_category_code(c) for c in raw_ccats))
            ctier = _normalize_tier(payload.get("counter_tier") or rec["tier_cap"])
            strictly_narrower = (
                set(ccats) < set(rec["categories"])
                or (set(ccats) == set(rec["categories"]) and _tier_rank(ctier) < _tier_rank(rec["tier_cap"]))
            )
            if not strictly_narrower:
                raise AdultIntimacyConflictError("COUNTER_NOT_NARROWER")
            rec["proposer"], rec["recipient"] = responder, rec["proposer"]
            rec["categories"] = ccats
            rec["tier_cap"] = ctier
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            rec["last_activity_at"] = now_iso
            rec["last_activity_mono_ms"] = mono_ms
            receipt = {"action": action, "permit_id": pid, "state": "PROPOSED", "decision": "COUNTER"}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        # ACCEPT -> ACTIVE
        menu_proj = _resolve_preference_menu(state, gate_proj)
        allowed_pool = set(menu_proj["open"]) | set(menu_proj["ask"])
        if not set(rec["categories"]).issubset(allowed_pool):
            raise AdultIntimacyConflictError("SCOPE_NOT_PERMITTED")

        rec["state"] = "ACTIVE"
        rec["phase"] = "OPENING"
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["last_activity_at"] = now_iso
        rec["last_activity_mono_ms"] = mono_ms
        # 36B §5.4 入场第一件事（36A §11.1）：把"被接管的当前活动"存为断点字段。
        # 调用方显式带来非空 ``checkpoint_activity_ref`` / ``activity_ref`` 时如实保存；
        # 否则零写只读探测 ``canonical_life_state.json`` 的活跃前台活动；仍无活跃活动时
        # ``activity_ref=None/saved=False`` 是诚实记录（"未保存"），绝不虚构引用。
        raw_checkpoint_ref = payload.get("checkpoint_activity_ref")
        if not (isinstance(raw_checkpoint_ref, str) and raw_checkpoint_ref.strip()):
            raw_checkpoint_ref = payload.get("activity_ref")
        saved_activity_ref = (
            raw_checkpoint_ref.strip()[:200]
            if isinstance(raw_checkpoint_ref, str) and raw_checkpoint_ref.strip()
            else None
        )
        auto_captured = False
        auto_activity_title: Optional[str] = None
        if saved_activity_ref is None:
            saved_activity_ref, auto_activity_title = _read_active_foreground_activity_zero_write(root)
            if saved_activity_ref is not None:
                auto_captured = True
        checkpoint_doc: dict[str, Any] = {
            "activity_ref": saved_activity_ref,
            "saved_at": now_iso,
            "saved": saved_activity_ref is not None,
            "resumable": False,
        }
        if auto_captured:
            checkpoint_doc["capture_source"] = "canonical_life_state_auto"
            if auto_activity_title:
                checkpoint_doc["activity_title"] = auto_activity_title
        rec["checkpoint"] = checkpoint_doc
        # 36B §5.4：declared_seconds 上限 86400 的过夜重决定窗口从这里起算。
        rec["active_since"] = now_iso
        rec["updated_at"] = now_iso

        # Apply subject soft preferences to private space fixtures (36B-3 §4)
        soft = state["seed"].get("soft_preferences") or {}
        if soft.get("close_curtains"):
            state["space"]["curtains_closed"] = True
        if soft.get("dim_lamp") and state["space"]["lamp_works"]:
            state["space"]["lamp_on"] = False

        receipt = {"action": action, "permit_id": pid, "state": "ACTIVE", "decision": "ACCEPT"}
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 13: advance_beat
    # ------------------------------------------------------------------
    if action == "advance_beat":
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        _op_raw = payload.get("execution_id") if payload.get("execution_id") is not None else payload.get("operation_key")
        _op_req = _require_local_id(_op_raw, reason_code="OPERATION_KEY_INVALID")
        op_key = f"advance_beat:{_op_req}"
        fp = _canonical_fingerprint(payload)
        _idem_cache = state["permits"]["idempotency_cache"]
        cached = _idem_cache.get(op_key) or _idem_cache.get(_op_req)
        if cached is None:
            # 已滑出活动缓存但仍在淘汰环内的逐拍回执同样安全重放。
            cached = _lookup_committed_op_ring(state, op_key) or _lookup_committed_op_ring(state, _op_req)
        if cached is None and (
            _op_key_expired_from_ring(state, op_key) or _op_key_expired_from_ring(state, _op_req)
        ):
            raise AdultIntimacyConflictError("RETRY_WINDOW_EXPIRED")
        has_exec_or_facts = (
            "execution_id" in payload
            or "proposed_facts" in payload
            or (isinstance(payload.get("proposed_render"), Mapping) and "proposed_facts" in payload["proposed_render"])
        )
        if cached is not None:
            if cached["fingerprint"] != fp:
                if has_exec_or_facts or cached.get("has_exec_or_facts"):
                    raise AdultIntimacyConflictError("EXECUTION_ID_REUSED")
                raise AdultIntimacyConflictError("IDEMPOTENCY_KEY_REUSED")
            return _project_adult_intimacy(
                state,
                char_ctx,
                now_dt,
                root=root,
                lexicon_info=lexicon_info,
                subject_view=bool(subject_view),
                action_receipt=dict(cached["receipt"], idempotent_replay=True),
            )

        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")

        # HIGHEST PRIORITY: Deterministic stop/pause/clarify precheck BEFORE idle/CAS/model!
        partner_utterance = payload.get("partner_utterance")
        if partner_utterance is not None and not isinstance(partner_utterance, str):
            raise AdultIntimacyValidationError("PARTNER_UTTERANCE_INVALID")
        utterance_cue, utt_meta = _classify_partner_utterance_with_meta(partner_utterance)
        if utterance_cue != "NONE":
            state["memory"].setdefault("admin_log", []).append({
                "op": "STOP_RECOGNIZER_HIT",
                "permit_id": pid,
                "cue": utterance_cue,
                "match_span": utt_meta.get("match_span"),
                "input_len": utt_meta.get("input_len"),
                "at": now_iso,
            })
        if utterance_cue == "STOP":
            rec["state"] = "STOPPED"
            rec["phase"] = "STOPPED"
            rec["end_reason"] = "ENDED_BY_PARTNER_STOP_WORD"
            rec["pending_widen"] = None
            rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            state["permits"].setdefault("command_ledger", []).append({
                "op": "STOP_COMMITTED",
                "permit_id": pid,
                "stop_gen": rec["stop_gen"],
                "at": now_iso,
            })
            receipt = {
                "action": action,
                "permit_id": pid,
                "state": "STOPPED",
                "stop_word_triggered": True,
                "no_interrogation": True,
                "no_grudge": True,
                "relationship_penalty": 0,
            }
            state["permits"]["idempotency_cache"][op_key] = {
                "fingerprint": fp,
                "receipt": receipt,
                "has_exec_or_facts": has_exec_or_facts,
            }
            return _commit(receipt)

        if utterance_cue in ("PAUSE", "CLARIFY"):
            if rec["state"] == "AFTERCARE" or rec.get("phase") == "AFTERCARE":
                raise AdultIntimacyConflictError("AFTERCARE_CANNOT_PAUSE")
            if rec["state"] not in TERMINAL_PERMIT_STATES:
                rec["state"] = "PAUSED"
                rec["pause_reason"] = "CLARIFY_NEEDED" if utterance_cue == "CLARIFY" else "PAUSE_BY_PARTNER"
                rec["pending_widen"] = None
                rec["resume_confirms"] = []
                rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
            receipt = {
                "action": action,
                "permit_id": pid,
                "state": rec["state"],
                "pause_triggered": True,
                "pause_reason": rec.get("pause_reason"),
            }
            state["permits"]["idempotency_cache"][op_key] = {
                "fingerprint": fp,
                "receipt": receipt,
                "has_exec_or_facts": has_exec_or_facts,
            }
            return _commit(receipt)

        if str(state["space"].get("place") or "").startswith("campus."):
            _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="CAMPUS_VENUE")
            _commit({"action": action, "aborted": "CAMPUS_VENUE"})
            raise AdultIntimacyPermissionError("CAMPUS_VENUE")

        raw_cats = payload.get("categories") or []
        if not isinstance(raw_cats, list):
            raise AdultIntimacyValidationError("CATEGORIES_INVALID")
        cats = [_validate_category_code(c) for c in raw_cats]

        if _tier_rank(rec["tier_cap"]) >= _tier_rank("INTIMATE") or any(_category_base(c) != "AFFECTION" for c in cats):
            _require_lexicon_loaded_for_intimate()

        # Live gate, incapacitation & privacy checks
        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        if not gate_proj["gate_open"]:
            if gate_proj.get("verdict") == "UNKNOWN" and lexicon_info.get("status") != "LOADED":
                _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="LEXICON_MISSING")
                _commit({"action": action, "blocked": "LEXICON_MISSING"})
                raise AdultIntimacyConflictError("LEXICON_MISSING")
            _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="ADULT_GATE_CLOSED")
            _commit({"action": action, "aborted": "ADULT_GATE_CLOSED"})
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")

        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        space_proj = _derive_private_space(state)
        if (
            body_proj["user_impairment_declared"]
            or space_proj["user_impairment_declared"]
            or body_proj["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED")
        ):
            _suspend_open_permit_incapacity(state, now_iso, detail="ALCOHOL_OR_DECLARED_INCAPACITY")
            _commit({"action": action, "suspended": "SUSPENDED_INCAPACITY"})
            raise AdultIntimacyConflictError("SUSPENDED_INCAPACITY")

        if rec["state"] == "SUSPENDED_INCAPACITY":
            raise AdultIntimacyConflictError("SUSPENDED_INCAPACITY")

        if not body_proj["available_for_intimacy"]:
            _abort_open_permit(state, now_iso, end_reason="ENDED_BY_HOST_SAFETY", abort_detail="SUBJECT_UNAVAILABLE")
            _commit({"action": action, "aborted": "SUBJECT_UNAVAILABLE"})
            raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")

        if space_proj["partner_intoxicated"]:
            _abort_open_permit(
                state,
                now_iso,
                end_reason="ENDED_BY_HOST_SAFETY",
                abort_detail="PARTNER_UNAVAILABLE_INTOXICATED",
            )
            _commit({"action": action, "aborted": "PARTNER_UNAVAILABLE_INTOXICATED"})
            raise AdultIntimacyConflictError("PARTNER_UNAVAILABLE_INTOXICATED")
        if space_proj["third_party_present"]:
            _pause_open_permit(state, now_iso, reason="PRIVACY_CONDITION_CHANGED")
            _commit({"action": action, "paused": "THIRD_PARTY_PRESENT"})
            raise AdultIntimacyPermissionError("THIRD_PARTY_PRESENT")
        if not space_proj["privacy_ok"]:
            paused_id = _pause_open_permit(state, now_iso, reason="PRIVACY_CONDITION_CHANGED")
            if paused_id:
                _commit({"action": action, "paused": "PRIVACY_CONDITION_CHANGED"})
                raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")
            if rec["state"] != "AFTERCARE":
                raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")

        if rec["state"] in TERMINAL_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_TERMINAL")
        if rec["state"] in PAUSED_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_PAUSED")
        if rec["state"] == "PROPOSED":
            raise AdultIntimacyConflictError("SCENE_NOT_READY")

        # Monotonic / wall-clock idle timeout check (default 600s -> PAUSED recoverable via confirm_resume)
        last_dt = _parse_stored_iso(rec.get("last_activity_at"))
        last_mono = rec.get("last_activity_mono_ms")
        idle_sec = int(rec.get("idle_timeout_sec") or DEFAULT_IDLE_TIMEOUT_SEC)
        idle_timed_out = False
        if rec["state"] == "ACTIVE":
            if last_mono is not None and mono_ms is not None:
                idle_timed_out = (mono_ms - int(last_mono)) >= idle_sec * 1000
            elif last_dt is not None and not wall_rolled_back:
                idle_timed_out = (now_dt - last_dt).total_seconds() >= idle_sec
        if idle_timed_out:
            rec["state"] = "PAUSED"
            rec["pause_reason"] = "IDLE_TIMEOUT"
            rec["pending_widen"] = None
            rec["resume_confirms"] = []
            rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            _commit({"action": action, "permit_id": pid, "state": "PAUSED", "pause_reason": "IDLE_TIMEOUT"})
            raise AdultIntimacyConflictError("PERMIT_EXPIRED")

        # 36B §5.4：declared_seconds 上限 86400 且到期不自续——跨日 ACTIVE 强制走重新
        # 确认路径（双方 confirm_resume 就是"重新决定"），带 reason_code。
        if rec["state"] == "ACTIVE" and not wall_rolled_back:
            active_dt = _parse_stored_iso(rec.get("active_since"))
            if active_dt is not None and (now_dt - active_dt).total_seconds() >= OVERNIGHT_RECONFIRM_SECONDS:
                rec["state"] = "PAUSED"
                rec["pause_reason"] = OVERNIGHT_RECONFIRM_REASON
                rec["pending_widen"] = None
                rec["resume_confirms"] = []
                rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
                _commit({"action": action, "permit_id": pid, "state": "PAUSED",
                         "pause_reason": OVERNIGHT_RECONFIRM_REASON})
                raise AdultIntimacyConflictError(OVERNIGHT_RECONFIRM_REASON)

        if "expected_revision" in payload:
            exp_rev = payload["expected_revision"]
            if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev != rec["permit_revision"]:
                raise AdultIntimacyConflictError("REVISION_CONFLICT")
        if "resume_epoch" in payload:
            req_epoch = payload["resume_epoch"]
            if isinstance(req_epoch, bool) or not isinstance(req_epoch, int) or req_epoch != rec["resume_epoch"]:
                raise AdultIntimacyConflictError("RESUME_EPOCH_STALE")

        next_phase = payload.get("phase") or rec["phase"]
        if next_phase not in SCENE_PHASES:
            raise AdultIntimacyValidationError("PHASE_INVALID")
        if (rec["state"] == "AFTERCARE" or next_phase in ("WINDING_DOWN", "AFTERCARE")) and cats:
            raise AdultIntimacyConflictError("PHASE_CLOSED")

        # Live scope & bilateral menu enforcement
        menu_proj = _resolve_preference_menu(state, gate_proj)
        excluded_now = set(menu_proj["excluded"]) | set(menu_proj["anatomy_excluded"])
        if any(c in excluded_now for c in cats) or not set(cats).issubset(set(rec["categories"])):
            raise AdultIntimacyConflictError("SCOPE_NOT_PERMITTED")

        # Male insertive erection precondition (36B-9 §3.3 — not counted as decline!)
        for c in cats:
            if _is_subject_insertive(state["body"]["profile"], state["body"].get("user_genitals"), c):
                if body_proj["erection"] != "full":
                    raise AdultIntimacyConflictError("ERECTION_INSUFFICIENT")

        # Fault-injection hooks for X01 (mid-render crash) & X09 (STOP racing after admit before commit)
        crash_point = payload.get("crash_point")
        if crash_point is not None and crash_point not in (
            "mid_render",
            "before_commit",
            "after_admit_before_commit",
            "after_commit",
            "during_delivery",
        ):
            raise AdultIntimacyValidationError("CRASH_POINT_INVALID")

        if crash_point == "mid_render":
            rec["state"] = "PAUSED_PRESENCE_UNKNOWN"
            rec["pause_reason"] = "RENDER_CRASH_PRESENCE_UNKNOWN"
            rec["presence_known"] = False
            rec["paused_ttl_sec"] = DEFAULT_PAUSED_DISCONNECT_TTL_SEC
            rec["paused_expires_at"] = _to_iso(now_dt + timedelta(seconds=DEFAULT_PAUSED_DISCONNECT_TTL_SEC))
            rec["pending_widen"] = None
            rec["resume_confirms"] = []
            rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            _commit({"action": action, "permit_id": pid, "crash_point": "mid_render", "state": "PAUSED_PRESENCE_UNKNOWN"})
            raise AdultIntimacyConflictError("CRASH_MID_RENDER")

        if crash_point == "after_admit_before_commit":
            cstop = payload.get("concurrent_stop_by")
            if cstop in PARTICIPANTS:
                rec["state"] = "STOPPED"
                rec["phase"] = "STOPPED"
                rec["end_reason"] = f"ENDED_BY_{str(cstop).upper()}"
                rec["pending_widen"] = None
                rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
                if state["permits"].get("active_permit_id") == pid:
                    state["permits"]["active_permit_id"] = None
                ledger = state["permits"].setdefault("command_ledger", [])
                ledger.append({"op": "STOP_COMMITTED", "permit_id": pid, "stop_gen": rec["stop_gen"], "at": now_iso})
                ledger.append({"op": "BEAT_ABORTED", "permit_id": pid, "execution_id": _op_req, "reason": "CONSENT_REVOKED", "at": now_iso})
                _commit({"action": action, "permit_id": pid, "state": "STOPPED", "aborted": "CONSENT_REVOKED"})
                raise AdultIntimacyConflictError("CONSENT_REVOKED")
            raise AdultIntimacyConflictError("CRASH_BEFORE_COMMIT")

        # Presentation router & upstream refusal/timeout/partial graceful downgrade (X11, X13, X14)
        route_id = payload.get("route_id")
        if route_id is not None:
            route_id = _require_local_id(route_id, reason_code="ROUTE_ID_INVALID")
        upstream_status = payload.get("upstream_status") or "OK"
        if upstream_status not in ("OK", "REFUSED", "TIMEOUT", "ERROR", "PARTIAL"):
            raise AdultIntimacyValidationError("UPSTREAM_STATUS_INVALID")

        eff_tier, _ = _compute_route_effective_tier(state, route_id=route_id, permit_rec=rec, now_dt=now_dt, lexicon_info=lexicon_info)
        if upstream_status in ("REFUSED", "TIMEOUT", "ERROR", "PARTIAL"):
            if upstream_status in ("REFUSED", "PARTIAL"):
                state["router"]["refusal_window"] = int(state["router"].get("refusal_window") or 0) + 1
                if state["router"]["refusal_window"] >= 3:
                    state["router"]["circuit_open_tier"] = "FADE_OUT"
            else:
                # X13: Pure network timeout NEVER increments refusal_window!
                state["router"]["timeout_count"] = int(state["router"].get("timeout_count") or 0) + 1
            stepped_down = _step_down_tier(eff_tier)
            rec["downgrade_cap"] = stepped_down
            eff_tier, _ = _compute_route_effective_tier(state, route_id=route_id, permit_rec=rec, now_dt=now_dt, lexicon_info=lexicon_info)
            rec["beat_index"] = int(rec.get("beat_index") or 0) + 1
            rec["phase"] = next_phase
            if next_phase == "AFTERCARE":
                _enter_aftercare_on_permit(rec, state, now_iso)
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["last_activity_at"] = now_iso
            rec["last_activity_mono_ms"] = mono_ms
            rec["updated_at"] = now_iso
            state["setbacks"]["mishap_cues"] = []
            # X14: Partial stream fragments are discarded; only neutral bridge receipt is committed
            receipt = {
                "action": action,
                "permit_id": pid,
                "beat_index": rec["beat_index"],
                "state": rec["state"],
                "upstream_refused_handled": True,
                "upstream_status": upstream_status,
                "partial_discarded": upstream_status == "PARTIAL",
                "adversarial_retry": False,
                "downgraded_to_tier": eff_tier,
                "bridge_rendered": True,
            }
            state["permits"]["idempotency_cache"][op_key] = {
                "fingerprint": fp,
                "receipt": receipt,
                "has_exec_or_facts": has_exec_or_facts,
            }
            return _commit(receipt)

        # Validate proposed_render (lexicon hard-fail, signal consistency, anti-feigned orgasm)
        prop_render = payload.get("proposed_render") or {}
        if not isinstance(prop_render, Mapping):
            raise AdultIntimacyValidationError("PROPOSED_RENDER_INVALID")
        allowed_render_keys = {
            "subject_line",
            "narrative_text",
            "subject_signal",
            "subject_orgasm_depicted",
            "proposed_facts",
            "proposed_mishap",
            "ejaculation_target",
            "stub_classifier_verdict",
        }
        extra_rk = sorted(set(prop_render.keys()) - allowed_render_keys)
        if extra_rk:
            raise AdultIntimacyValidationError(f"RENDER_FIELD_UNKNOWN:{extra_rk[0]}")

        subject_line = str(prop_render.get("subject_line") or "")
        narrative_text = str(prop_render.get("narrative_text") or "")
        stub_verdict = str(prop_render.get("stub_classifier_verdict") or "PASS").upper()
        if stub_verdict == "FAIL" or _lexicon_hits(
            state["gate"]["lexicon_patterns"], subject_line, narrative_text, lexicon_info=lexicon_info
        ):
            rec["state"] = "UNDER_REVIEW"
            rec["phase"] = "STOPPED"
            rec["end_reason"] = "ENDED_BY_HOST_SAFETY"
            rec["abort_detail"] = "HARD_FAIL_VALIDATOR"
            rec["pending_widen"] = None
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            state["gate"]["adult_confirmed"] = False
            state["gate"]["user_scope_closed"] = True
            state["gate"]["review_required"] = True
            state["memory"].setdefault("admin_log", []).append({
                "op": "VALIDATOR_HARD_FAIL",
                "permit_id": pid,
                "beat_index": int(rec.get("beat_index") or 0) + 1,
                "reason_code": "CHILD_CODING_PROHIBITED",
                "at": now_iso,
            })
            _commit({"action": action, "hard_fail": "CHILD_CODING_PROHIBITED", "permit_status": "UNDER_REVIEW"})
            raise AdultIntimacyPermissionError("CHILD_CODING_PROHIBITED")

        if prop_render.get("proposed_mishap") is not None:
            pm = prop_render["proposed_mishap"]
            if pm == "KNOCK":
                _pause_open_permit(state, now_iso, reason="PRIVACY_CONDITION_CHANGED")
                _commit({"action": action, "paused": "PRIVACY_CONDITION_CHANGED"})
                raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")
            if pm not in MISHAP_ALLOWED_SOURCES:
                raise AdultIntimacyValidationError("MISHAP_UNKNOWN")
            if "render" not in MISHAP_ALLOWED_SOURCES[pm]:
                raise AdultIntimacyPermissionError("MISHAP_SOURCE_FORBIDDEN")

        declared_sig = str(prop_render.get("subject_signal") or "CONTINUE")
        reconciled_sig = _reconcile_subject_signal(subject_line, declared_sig)
        if utterance_cue == "SLOW" and reconciled_sig == "CONTINUE":
            reconciled_sig = "SLOW"

        if reconciled_sig == "STOP":
            rec["state"] = "STOPPED"
            rec["phase"] = "STOPPED"
            rec["end_reason"] = "ENDED_BY_SUBJECT_SIGNAL"
            rec["pending_widen"] = None
            rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            state["memory"]["s11_trust_claims"].append({
                "domain": "intimacy_boundary",
                "basis": "RESPECTED_STOP",
                "note_ref": pid,
            })
            receipt = {
                "action": action,
                "permit_id": pid,
                "state": "STOPPED",
                "subject_signal": "STOP",
                "relationship_penalty": 0,
            }
            state["permits"]["idempotency_cache"][op_key] = {
                "fingerprint": fp,
                "receipt": receipt,
                "has_exec_or_facts": has_exec_or_facts,
            }
            return _commit(receipt)

        raw_facts = prop_render.get("proposed_facts") or payload.get("proposed_facts") or []
        if not isinstance(raw_facts, list):
            raise AdultIntimacyValidationError("PROPOSED_FACTS_INVALID")

        # Pre-calculate whether arousal reaches high/peak on this beat for truthful orgasm validation
        body = state["body"]
        profile = body["profile"]
        bases = {_base_category(c) for c in cats}
        genital_stim = bool(set(cats) & GENITAL_TO_SUBJECT_CATEGORIES)
        cur_level = body_proj["arousal_level"]
        refractory_now = body_proj["refractory_active"]

        projected_beat_level = cur_level
        if not refractory_now:
            if cur_level == "afterglow":
                if profile["multi_orgasm"] and genital_stim:
                    projected_beat_level = "high"
            elif bases - NO_RAISE_BASES:
                cap_lvl = "building" if bases <= (CAP_BUILDING_BASES | NO_RAISE_BASES) else "peak"
                cur_idx = AROUSAL_LEVELS.index(cur_level) if cur_level in AROUSAL_LEVELS else 0
                cap_idx = AROUSAL_LEVELS.index(cap_lvl)
                projected_beat_level = AROUSAL_LEVELS[max(cur_idx, min(cur_idx + 1, cap_idx))]

        orgasm_fact_present = any(isinstance(f, Mapping) and f.get("kind") == "ORGASM" for f in raw_facts)
        orgasm_depicted = bool(prop_render.get("subject_orgasm_depicted", False))
        if orgasm_depicted and not orgasm_fact_present:
            raise AdultIntimacyConflictError("ORGASM_FEIGNED")

        if orgasm_fact_present:
            if refractory_now:
                raise AdultIntimacyConflictError("ORGASM_REFRACTORY")
            if cur_level not in ("high", "peak") and projected_beat_level not in ("high", "peak"):
                if orgasm_depicted:
                    raise AdultIntimacyConflictError("ORGASM_FEIGNED")
                raise AdultIntimacyConflictError("ORGASM_LEVEL_INVALID")

        # Precheck all World CONSUME / UNDRESS facts before mutating state
        consume_tally: dict[str, int] = {}
        for fact in raw_facts:
            if not isinstance(fact, Mapping):
                raise AdultIntimacyValidationError("PROPOSED_FACT_INVALID")
            fkind = fact.get("kind")
            if fkind == "CONSUME":
                item = str(fact.get("item") or "")
                if item not in STOCK_ITEMS:
                    raise AdultIntimacyValidationError(f"STOCK_ITEM_UNKNOWN:{item}")
                consume_tally[item] = consume_tally.get(item, 0) + 1
                if state["space"]["stock"].get(item, 0) < consume_tally[item]:
                    raise AdultIntimacyConflictError(f"OUT_OF_STOCK:{item}")
            elif fkind not in ("ORGASM", "UNDRESS", "BED_USED"):
                raise AdultIntimacyValidationError(f"FACT_KIND_UNKNOWN:{fkind}")

        # Contraception hard-gate (CONDOM_REQUIRED -> 409 CONDOM_REQUIRED_UNAVAILABLE)
        condom_used = consume_tally.get("condom", 0) > 0
        if (bases & PENETRATIVE_BASES) and state["partner_limits"].get("contraception_preference") == "CONDOM_REQUIRED":
            if not condom_used and not state["space"].get("condom_on"):
                raise AdultIntimacyConflictError("CONDOM_REQUIRED_UNAVAILABLE")

        if crash_point == "before_commit":
            # X02: Crash before Permit commit -> 0 writes to Permit, Body, World, or Outbox!
            raise AdultIntimacyConflictError("CRASH_BEFORE_COMMIT")

        lube_used = consume_tally.get("lube", 0) > 0
        tissue_used = consume_tally.get("tissue", 0) > 0

        # X03 / X04: Crash after Permit commit or mid-delivery (World deferred to outbox reconciliation)
        defer_body = crash_point == "after_commit"
        defer_world = crash_point in ("after_commit", "during_delivery")

        if not defer_world:
            for item, qty in consume_tally.items():
                state["space"]["stock"][item] -= qty
            if condom_used:
                state["space"]["condom_on"] = True
            for fact in raw_facts:
                if fact.get("kind") == "UNDRESS":
                    _apply_garment_operation(
                        state["space"],
                        {
                            "op": "undress",
                            "garment_id": fact.get("garment_id"),
                            "target": fact.get("target") or "bedroom.floor",
                        },
                        lexicon_info=lexicon_info,
                        admin_log=state["memory"].setdefault("admin_log", []),
                        now_iso=now_iso,
                    )

        # Dryness / discomfort check on INTERCOURSE without lubrication (36B-11 §2)
        if "INTERCOURSE" in bases and not lube_used and cur_level in ("none", "low"):
            if not defer_body:
                body["discomfort"] = "mild"
            reconciled_sig = "SLOW"

        ejaculation_location: Optional[str] = None
        if not defer_body:
            body["fatigue"] = round(min(1.0, float(body.get("fatigue") or 0.0) + 0.06), 4)
            body["sweat"] = round(min(1.0, float(body.get("sweat") or 0.0) + (0.08 if cats else 0.02)), 4)
            body["cleanliness"] = round(max(0.0, float(body.get("cleanliness", 1.0)) - (0.06 if cats else 0.01)), 4)
            body["thirst"] = round(min(1.0, float(body.get("thirst") or 0.0) + 0.05), 4)
            if "INTERCOURSE" in bases:
                body["soreness"] = int(body.get("soreness") or 0) + 1

            if int(body.get("heightened_beats_left") or 0) > 0:
                body["heightened_beats_left"] = int(body["heightened_beats_left"]) - 1
                if body["heightened_beats_left"] == 0:
                    body["sensitivity"] = "normal"

            if int(body.get("refractory_beats_left") or 0) > 0:
                body["refractory_beats_left"] = int(body["refractory_beats_left"]) - 1
                if body["refractory_beats_left"] == 0 and body["arousal_level"] == "afterglow":
                    body["arousal_level"] = "low"
                    body["arousal_score"] = _arousal_score_for_level("low")

            if orgasm_fact_present:
                body["arousal_level"] = "afterglow"
                body["arousal_score"] = _arousal_score_for_level("afterglow")
                body["sensitivity"] = "heightened"
                body["heightened_beats_left"] = 2
                body["desire_pulse"] = 0.0
                body["desire_updated_at"] = now_iso
                state["permits"]["orgasm_receipts"][pid] = int(state["permits"]["orgasm_receipts"].get(pid) or 0) + 1
                if profile["ejaculates"]:
                    ref_min = max(1, int(round(profile["refractory_minutes"] * (1.0 + 0.5 * float(body["fatigue"])))))
                    body["refractory_beats_left"] = 8
                    body["refractory_until"] = _to_iso(now_dt + timedelta(minutes=ref_min))
                    ejaculation_location = _derive_ejaculation_target(
                        state["space"],
                        condom_active=("INTERCOURSE" in bases or "ANAL" in bases)
                        and (condom_used or bool(state["space"].get("condom_on"))),
                        tissue_used=tissue_used,
                        explicit_target=prop_render.get("ejaculation_target"),
                    )
                    if not defer_world:
                        if ejaculation_location in ("condom", "tissue"):
                            state["space"]["condom_on"] = False
                            if int(state["space"].get("waste_bin_used", 0)) < int(state["space"].get("waste_bin_cap", 5)):
                                state["space"]["waste_bin_used"] = int(state["space"].get("waste_bin_used", 0)) + 1
                            else:
                                state["space"]["uncollected_waste"] = int(state["space"].get("uncollected_waste", 0)) + 1
                        elif ejaculation_location == "towel_underlay":
                            state["space"]["towel_wetness"] = "WET"
                        elif ejaculation_location == "bed_linen":
                            state["space"]["bed_linen_state"] = "SOILED"
            else:
                body["arousal_level"] = projected_beat_level
                body["arousal_score"] = _arousal_score_for_level(projected_beat_level)
            body["arousal_updated_at"] = now_iso

        if not defer_world and (cats or any(f.get("kind") == "BED_USED" for f in raw_facts)):
            if state["space"]["bed_linen_state"] == "CLEAN" and state["space"]["place"] in ("bedroom.bed", "home_bedroom", "hotel_room"):
                state["space"]["bed_linen_state"] = "USED"
            state["space"]["rumple_index"] = round(min(1.0, float(state["space"]["rumple_index"]) + 0.2), 4)

        state["setbacks"]["mishap_cues"] = []

        rec["beat_index"] = int(rec.get("beat_index") or 0) + 1
        rec["phase"] = next_phase
        if next_phase == "AFTERCARE":
            _enter_aftercare_on_permit(rec, state, now_iso)
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["last_activity_at"] = now_iso
        rec["last_activity_mono_ms"] = mono_ms
        rec["updated_at"] = now_iso

        exec_map = state["permits"].setdefault("executed_effect_ids", {})
        if defer_body or defer_world:
            exec_map[_op_req] = {
                "body_applied": not defer_body,
                "world_applied": not defer_world,
                "body_write_count": 0 if defer_body else 1,
                "world_write_count": 0 if defer_world else 1,
            }
            state["permits"].setdefault("outbox", []).append({
                "kind": "BEAT_EFFECTS",
                "execution_id": _op_req,
                "permit_id": pid,
                "cats": list(cats),
                "bases": sorted(bases),
                "consume_tally": dict(consume_tally),
                "raw_facts": copy.deepcopy(raw_facts),
                "projected_beat_level": projected_beat_level,
                "orgasm_fact_present": orgasm_fact_present,
                "ejaculation_target": prop_render.get("ejaculation_target"),
                "queued_at": now_iso,
            })
            _commit({"action": action, "permit_id": pid, "crash_point": crash_point, "outbox_queued": _op_req})
            raise AdultIntimacyConflictError(f"CRASH_{str(crash_point).upper()}")

        exec_map[_op_req] = {
            "body_applied": True,
            "world_applied": True,
            "body_write_count": 1,
            "world_write_count": 1,
        }
        state["permits"].setdefault("command_ledger", []).append({
            "op": "BEAT_COMMITTED",
            "permit_id": pid,
            "beat_index": rec["beat_index"],
            "execution_id": _op_req,
            "at": now_iso,
        })
        if narrative_text or subject_line:
            state["router"].setdefault("displayed_queue", []).append({
                "permit_id": pid,
                "beat_index": rec["beat_index"],
                "execution_id": _op_req,
                "tier": eff_tier,
            })

        receipt = {
            "action": action,
            "permit_id": pid,
            "beat_index": rec["beat_index"],
            "effective_tier": eff_tier,
            "subject_signal": reconciled_sig,
            "arousal_level": body["arousal_level"],
            "orgasm_recorded": orgasm_fact_present,
            "ejaculation_location": ejaculation_location,
            "ejaculation_target": ejaculation_location,
        }
        state["permits"]["idempotency_cache"][op_key] = {
            "fingerprint": fp,
            "receipt": receipt,
            "has_exec_or_facts": has_exec_or_facts,
        }
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 14 & 15: request_widen & answer_widen
    # ------------------------------------------------------------------
    if action == "request_widen":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] in PAUSED_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_PAUSED")
        if rec["state"] != "ACTIVE":
            raise AdultIntimacyConflictError("PERMIT_NOT_ACTIVE")
        by = payload.get("by")
        if by not in PARTICIPANTS:
            raise AdultIntimacyValidationError("PARTICIPANT_INVALID")
        if "expected_revision" in payload and payload["expected_revision"] != rec["permit_revision"]:
            raise AdultIntimacyConflictError("REVISION_CONFLICT")
        if "resume_epoch" in payload and payload["resume_epoch"] != rec["resume_epoch"]:
            raise AdultIntimacyConflictError("RESUME_EPOCH_STALE")

        raw_add = payload.get("add_categories") if "add_categories" in payload else (payload.get("categories") or [])
        if not isinstance(raw_add, list):
            raise AdultIntimacyValidationError("ADD_CATEGORIES_INVALID")
        add_cats = [_validate_category_code(c) for c in raw_add]
        raw_widen_tier = payload["tier_cap"] if "tier_cap" in payload else payload.get("desired_tier")
        new_tier = _normalize_tier(raw_widen_tier) if raw_widen_tier is not None else None
        if not add_cats and new_tier is None:
            raise AdultIntimacyValidationError("WIDEN_TARGET_REQUIRED")

        if (new_tier and _tier_rank(new_tier) >= _tier_rank("INTIMATE")) or any(_category_base(c) != "AFFECTION" for c in add_cats):
            _require_lexicon_loaded_for_intimate()

        sink_mode = str(payload.get("memory_sink_mode") or "OK").upper()
        declined_set = set(rec.get("declined_in_scene") or [])
        repeated = [c for c in add_cats if c in declined_set]
        if repeated:
            if by == "partner":
                press_n = _record_partner_press_after_decline(
                    state, f"{pid}:{repeated[0]}", now_iso=now_iso, sink_mode=sink_mode
                )
                _commit({"action": action, "rejected": "DECLINED_THIS_SCENE", "press_count": press_n})
            raise AdultIntimacyConflictError("DECLINED_THIS_SCENE")

        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        if not gate_proj["gate_open"]:
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")
        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        if body_proj["user_impairment_declared"] or body_proj["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED"):
            raise AdultIntimacyConflictError("INCAPACITY")
        if not body_proj["available_for_intimacy"]:
            raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")
        space_proj = _derive_private_space(state)
        if space_proj["partner_intoxicated"]:
            raise AdultIntimacyConflictError("PARTNER_UNAVAILABLE_INTOXICATED")
        if not space_proj["privacy_ok"]:
            raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")
        menu_proj = _resolve_preference_menu(state, gate_proj)
        allowed_pool = set(menu_proj["open"]) | set(menu_proj["ask"])
        if not set(add_cats).issubset(allowed_pool):
            raise AdultIntimacyConflictError("SCOPE_NOT_PERMITTED")

        rec["pending_widen"] = {
            "by": by,
            "add_categories": sorted(dict.fromkeys(add_cats)),
            "tier_cap": new_tier,
            "requested_at": now_iso,
        }
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["updated_at"] = now_iso
        receipt = {"action": action, "permit_id": pid, "pending_widen": rec["pending_widen"]}
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    if action == "answer_widen":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] in PAUSED_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_PAUSED")
        pending = rec.get("pending_widen")
        by = payload.get("by")
        if rec["state"] != "ACTIVE" or not pending or by not in PARTICIPANTS or by == pending["by"]:
            raise AdultIntimacyConflictError("WIDEN_PENDING_MISMATCH")
        if "expected_revision" in payload and payload["expected_revision"] != rec["permit_revision"]:
            raise AdultIntimacyConflictError("REVISION_CONFLICT")
        if "resume_epoch" in payload and payload["resume_epoch"] != rec["resume_epoch"]:
            raise AdultIntimacyConflictError("RESUME_EPOCH_STALE")
        decision = payload.get("decision")
        if decision not in ("ACCEPT", "DECLINE", "UNCLEAR"):
            raise AdultIntimacyValidationError("DECISION_INVALID")

        rec["pending_widen"] = None
        if decision == "ACCEPT":
            if (pending.get("tier_cap") and _tier_rank(pending["tier_cap"]) >= _tier_rank("INTIMATE")) or any(
                _category_base(c) != "AFFECTION" for c in pending["add_categories"]
            ):
                _require_lexicon_loaded_for_intimate()
            body_proj_w = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
            if body_proj_w["user_impairment_declared"] or body_proj_w["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED"):
                raise AdultIntimacyConflictError("INCAPACITY")
            if not body_proj_w["available_for_intimacy"]:
                raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")
            space_proj_w = _derive_private_space(state)
            if space_proj_w["partner_intoxicated"]:
                raise AdultIntimacyConflictError("PARTNER_UNAVAILABLE_INTOXICATED")
            if not space_proj_w["privacy_ok"]:
                raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")
            merged = sorted(set(rec["categories"]) | set(pending["add_categories"]))
            rec["categories"] = merged
            if pending.get("tier_cap"):
                rec["tier_cap"] = _min_tier(
                    pending["tier_cap"],
                    state["gate"]["deploy_tier_cap"],
                    state["partner_limits"]["preferred_tier"],
                )
        elif decision == "DECLINE":
            for c in pending["add_categories"]:
                if c not in rec["declined_in_scene"]:
                    rec["declined_in_scene"].append(c)
        # UNCLEAR leaves scope unchanged without consent!
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["updated_at"] = now_iso
        receipt = {
            "action": action,
            "permit_id": pid,
            "decision": decision,
            "categories": rec["categories"],
            "relationship_penalty": 0,
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 16..19: pause_scene, confirm_resume, narrow_scene, stop_scene
    # ------------------------------------------------------------------
    if action == "pause_scene":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] in TERMINAL_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_TERMINAL")
        # ERR-02: AFTERCARE -> PAUSED is forbidden (409 AFTERCARE_CANNOT_PAUSE)
        if rec["state"] == "AFTERCARE" or rec.get("phase") == "AFTERCARE":
            raise AdultIntimacyConflictError("AFTERCARE_CANNOT_PAUSE")
        if rec["state"] == "SUSPENDED_INCAPACITY":
            raise AdultIntimacyConflictError("SUSPENDED_INCAPACITY")
        by = payload.get("by")
        if by not in ("partner", "subject", "host"):
            raise AdultIntimacyValidationError("PARTICIPANT_INVALID")
        reason = _require_nonempty_str(payload.get("reason") or f"PAUSE_BY_{by.upper()}", max_len=64, reason_code="REASON_INVALID")
        rec["state"] = "PAUSED"
        rec["pause_reason"] = reason
        rec["pending_widen"] = None
        rec["resume_confirms"] = []
        rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["updated_at"] = now_iso
        receipt = {
            "action": action,
            "permit_id": pid,
            "state": "PAUSED",
            "resume_epoch": rec["resume_epoch"],
            "relationship_penalty": 0,
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    if action == "confirm_resume":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] == "SUSPENDED_INCAPACITY":
            raise AdultIntimacyConflictError("SUSPENDED_INCAPACITY")
        if rec["state"] not in PAUSED_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_NOT_PAUSED")

        # X25: Disconnect TTL expiry check -> EXPIRED, never resumed or marked completed
        if rec.get("paused_expires_at"):
            pexp_dt = _parse_stored_iso(rec.get("paused_expires_at"))
            if pexp_dt is not None and now_dt >= pexp_dt:
                rec["state"] = "EXPIRED"
                rec["end_reason"] = "DISCONNECT_TTL_EXPIRED"
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
                if state["permits"].get("active_permit_id") == pid:
                    state["permits"]["active_permit_id"] = None
                _commit({"action": action, "permit_id": pid, "state": "EXPIRED"})
                raise AdultIntimacyConflictError("PERMIT_EXPIRED")

        if rec.get("pending_unknown_executions"):
            raise AdultIntimacyConflictError("RECONCILE_PENDING")

        by = payload.get("by")
        if by not in PARTICIPANTS:
            raise AdultIntimacyValidationError("PARTICIPANT_INVALID")
        req_epoch = payload.get("resume_epoch")
        if isinstance(req_epoch, bool) or not isinstance(req_epoch, int) or req_epoch != rec["resume_epoch"]:
            raise AdultIntimacyConflictError("RESUME_EPOCH_STALE")

        if _tier_rank(rec["tier_cap"]) >= _tier_rank("INTIMATE") or any(_category_base(c) != "AFFECTION" for c in rec["categories"]):
            _require_lexicon_loaded_for_intimate()

        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        if not gate_proj["gate_open"]:
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")
        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        if body_proj["user_impairment_declared"] or body_proj["alcohol_impairment_level"] in ("INTOXICATED", "INCAPACITATED"):
            raise AdultIntimacyConflictError("INCAPACITY")
        if not body_proj["available_for_intimacy"]:
            raise AdultIntimacyConflictError("SUBJECT_UNAVAILABLE")
        space_proj = _derive_private_space(state)
        if not space_proj["privacy_ok"]:
            raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")

        confirms = list(rec.get("resume_confirms") or [])
        if by not in confirms:
            confirms.append(by)
        rec["resume_confirms"] = confirms
        if set(confirms) >= {"partner", "subject"}:
            rec["state"] = "ACTIVE"
            rec["pause_reason"] = None
            rec["presence_known"] = True
            rec["paused_ttl_sec"] = None
            rec["paused_expires_at"] = None
            rec["resume_confirms"] = []
            rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["last_activity_at"] = now_iso
            rec["last_activity_mono_ms"] = mono_ms
            # 双方重新确认 = 重新决定（36B §5.4）：86400s 过夜窗口自此刻重新起算。
            rec["active_since"] = now_iso
        rec["updated_at"] = now_iso
        receipt = {
            "action": action,
            "permit_id": pid,
            "state": rec["state"],
            "resume_epoch": rec["resume_epoch"],
            "confirmed_by": confirms,
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    if action == "narrow_scene":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] in TERMINAL_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_TERMINAL")
        by = payload.get("by")
        if by not in PARTICIPANTS:
            raise AdultIntimacyValidationError("PARTICIPANT_INVALID")
        rem_raw = payload.get("remove_categories") or []
        if not isinstance(rem_raw, list):
            raise AdultIntimacyValidationError("REMOVE_CATEGORIES_INVALID")
        rem_cats = {_validate_category_code(c) for c in rem_raw}
        rec["categories"] = [c for c in rec["categories"] if c not in rem_cats]
        if "tier_cap" in payload:
            ntier = _normalize_tier(payload["tier_cap"])
            if _tier_rank(ntier) < _tier_rank(rec["tier_cap"]):
                rec["tier_cap"] = ntier
        if payload.get("to_aftercare") is True:
            _enter_aftercare_on_permit(rec, state, now_iso)
        rec["pending_widen"] = None
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["updated_at"] = now_iso
        receipt = {"action": action, "permit_id": pid, "state": rec["state"], "categories": rec["categories"]}
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    if action == "stop_scene":
        # Unconditional highest-priority stop/revoke (NO CAS barrier, zero interrogation, zero grudge!)
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid_raw = payload.get("permit_id") or state["permits"].get("active_permit_id")
        pid = _require_local_id(pid_raw, reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        by = payload.get("by")
        if by not in ("partner", "subject", "host"):
            raise AdultIntimacyValidationError("PARTICIPANT_INVALID")
        transition = payload.get("transition") or "STOPPED"
        if transition not in ("STOPPED", "AFTERCARE"):
            raise AdultIntimacyValidationError("STOP_TRANSITION_INVALID")

        if rec["state"] not in TERMINAL_PERMIT_STATES:
            if transition == "AFTERCARE":
                _enter_aftercare_on_permit(rec, state, now_iso)
            else:
                rec["state"] = "STOPPED"
                rec["phase"] = "STOPPED"
            rec["end_reason"] = payload.get("reason") or f"STOPPED_BY_{by.upper()}"
            rec["pending_widen"] = None
            rec["resume_confirms"] = []
            rec["stop_gen"] = int(rec.get("stop_gen") or 0) + 1
            rec["permit_revision"] += 1
            _sync_permit_rev(rec)
            rec["updated_at"] = now_iso
            if transition == "STOPPED" and state["permits"].get("active_permit_id") == pid:
                state["permits"]["active_permit_id"] = None
            state["permits"].setdefault("command_ledger", []).append({
                "op": "STOP_COMMITTED",
                "permit_id": pid,
                "stop_gen": rec["stop_gen"],
                "at": now_iso,
            })
            state["memory"]["s11_trust_claims"].append({
                "domain": "intimacy_boundary",
                "basis": "RESPECTED_STOP",
                "note_ref": pid,
            })

        receipt = {
            "action": action,
            "permit_id": pid,
            "state": rec["state"],
            "stop_gen": rec["stop_gen"],
            "no_interrogation": True,
            "no_grudge": True,
            "relationship_penalty": 0,
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 20: record_setback_or_query / record_setback (36B-11)
    # ------------------------------------------------------------------
    if action in ("record_setback_or_query", "record_setback"):
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        skind = _require_nonempty_str(payload.get("kind"), max_len=40, reason_code="SETBACK_KIND_INVALID")
        sink_mode = str(payload.get("memory_sink_mode") or "OK").upper()

        if skind == "mishap":
            mkind = _require_nonempty_str(payload.get("mishap"), max_len=32, reason_code="MISHAP_UNKNOWN")
            msrc = _require_nonempty_str(payload.get("source"), max_len=24, reason_code="MISHAP_SOURCE_FORBIDDEN")
            if mkind == "KNOCK":
                # Knock at door is privacy loss, NEVER treated as harmless mishap (36B-3 §6 / 36B-11 §5)
                _pause_open_permit(state, now_iso, reason="PRIVACY_CONDITION_CHANGED")
                _commit({"action": action, "paused": "PRIVACY_CONDITION_CHANGED"})
                raise AdultIntimacyConflictError("PRIVACY_CONDITION_CHANGED")
            if mkind not in MISHAP_ALLOWED_SOURCES:
                raise AdultIntimacyValidationError("MISHAP_UNKNOWN")
            if msrc not in MISHAP_ALLOWED_SOURCES[mkind]:
                raise AdultIntimacyPermissionError("MISHAP_SOURCE_FORBIDDEN")
            state["setbacks"]["mishap_cues"].append({"mishap": mkind, "source": msrc})
            receipt = {"action": action, "kind": "mishap", "cues": state["setbacks"]["mishap_cues"]}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        if skind == "discomfort":
            lvl = _require_nonempty_str(payload.get("level"), max_len=24, reason_code="DISCOMFORT_INVALID")
            if lvl not in ("mild", "marked", "strong"):
                raise AdultIntimacyValidationError("DISCOMFORT_INVALID")
            symptom = payload.get("symptom") or "discomfort"
            if symptom not in ("dryness", "pain", "cramp", "fatigue", "discomfort"):
                raise AdultIntimacyValidationError("SYMPTOM_INVALID")
            state["body"]["discomfort"] = lvl
            pace_reaction = "SLOW"
            if lvl == "marked" and symptom != "pain":
                _pause_open_permit(state, now_iso, reason=f"DISCOMFORT_{symptom.upper()}")
                pace_reaction = "PAUSE"
            elif lvl == "strong" or symptom == "pain":
                _abort_open_permit(
                    state,
                    now_iso,
                    target_state="AFTERCARE",
                    end_reason=f"STOPPED_FOR_{symptom.upper()}",
                )
                pace_reaction = "STOP"
            receipt = {
                "action": action,
                "kind": "discomfort",
                "level": lvl,
                "symptom": symptom,
                "pace_reaction": pace_reaction,
                "blame_assigned": False,
            }
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        if skind == "ask_orgasm":
            pid = _require_local_id(payload.get("permit_id"), reason_code="PERMIT_ID_INVALID")
            if pid not in state["permits"]["records"]:
                raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
            disclose = payload.get("disclose")
            if disclose is None:
                disclose = True
            else:
                disclose = _require_bool(disclose, reason_code="DISCLOSE_INVALID")
            came = int(state["permits"]["orgasm_receipts"].get(pid) or 0) > 0
            answer = "DECLINED_TO_SAY" if not disclose else ("YES" if came else "NO")
            ans_obj = {
                "permit_id": pid,
                "answer": answer,
                "truthful_from_body_receipt": True,
                "orgasm_feigning_forbidden": True,
                "at": now_iso,
            }
            state["setbacks"]["last_orgasm_answer"] = ans_obj
            receipt = {"action": action, "kind": "ask_orgasm", **ans_obj}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        if skind == "preference_note":
            src_id = payload.get("source_id")
            if src_id not in PREF_SOURCES:
                raise AdultIntimacyPermissionError("PREF_SOURCE_FORBIDDEN")
            ttype = payload.get("target_type")
            tcode = str(payload.get("code") or "")
            valid_target = (
                (ttype == "CATEGORY" and tcode in ALL_CATEGORIES)
                or (ttype == "MANNER" and tcode in MANNER_CODES)
            )
            if not valid_target:
                raise AdultIntimacyPermissionError("PREF_TARGET_FORBIDDEN")
            pol = payload.get("polarity")
            clarity = payload.get("clarity") or "TENTATIVE"
            pctx = payload.get("context")
            note = payload.get("note")
            if (
                pol not in PREF_POLARITIES
                or clarity not in PREF_CLARITIES
                or pctx not in PREF_CONTEXTS
                or (note is not None and (not isinstance(note, str) or len(note) > 40))
            ):
                raise AdultIntimacyValidationError("PREF_FIELD_INVALID")
            mid = _write_private_memory(
                state,
                "PREFERENCE",
                f"{ttype}:{tcode}:{pol}",
                source_ref=src_id,
                now_iso=now_iso,
                sink_mode=sink_mode,
                structured_body={
                    "target": {"type": ttype, "code": tcode},
                    "polarity": pol,
                    "clarity": clarity,
                    "elicited": bool(payload.get("elicited", False)),
                    "context": pctx,
                    "note": note,
                },
            )
            receipt = {"action": action, "kind": "preference_note", "memory_id": mid}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        if skind == "s11_write":
            sfield = payload.get("s11_field")
            sentry = payload.get("s11_entry")
            if sfield not in S11_ALLOWED_BASES:
                raise AdultIntimacyPermissionError("S11_COUNT_OR_PERFORMANCE_FORBIDDEN")
            if sfield == "open_ruptures" and not subject_view:
                raise AdultIntimacyPermissionError("S11_WRITER_NOT_SUBJECT")
            if not isinstance(sentry, Mapping) or not set(sentry.keys()).issubset({"domain", "basis", "note_ref"}):
                raise AdultIntimacyPermissionError("S11_COUNT_OR_PERFORMANCE_FORBIDDEN")
            if sentry.get("basis") not in S11_ALLOWED_BASES[sfield]:
                raise AdultIntimacyPermissionError("S11_COUNT_OR_PERFORMANCE_FORBIDDEN")
            target_list = (
                state["memory"]["s11_open_ruptures"]
                if sfield == "open_ruptures"
                else state["memory"]["s11_trust_claims"]
            )
            target_list.append(copy.deepcopy(dict(sentry)))
            receipt = {"action": action, "kind": "s11_write", "s11_field": sfield}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        raise AdultIntimacyValidationError(f"SETBACK_KIND_UNKNOWN:{skind}")

    # ------------------------------------------------------------------
    # Action 21: discuss_intimacy_outside_scene (36B-11 §7-§8)
    # ------------------------------------------------------------------
    if action == "discuss_intimacy_outside_scene":
        speaker = payload.get("speaker") or "partner"
        if speaker not in PARTICIPANTS:
            raise AdultIntimacyValidationError("SPEAKER_INVALID")

        # Tightening partner_limits is ALWAYS allowed anytime (even outside private space / closed gate)!
        tighten_in = payload.get("tighten_partner_limits") if "tighten_partner_limits" in payload else payload.get("partner_limits")
        if tighten_in is not None:
            if not isinstance(tighten_in, Mapping) or not tighten_in:
                raise AdultIntimacyValidationError("PARTNER_LIMITS_INVALID")
            plimits = state["partner_limits"]
            rank_map = {"NO": 0, "MAYBE": 1, "YES": 2}
            if "stances" in tighten_in:
                t_stances = tighten_in["stances"]
                if not isinstance(t_stances, Mapping) or not t_stances:
                    raise AdultIntimacyValidationError("PARTNER_STANCES_INVALID")
                for k, v in t_stances.items():
                    cat_code = _validate_category_code(k)
                    if v not in STANCES:
                        raise AdultIntimacyValidationError(f"STANCE_INVALID:{cat_code}")
                    cur_v = plimits["stances"].get(cat_code, "YES")
                    if rank_map[v] > rank_map[cur_v]:
                        raise AdultIntimacyConflictError("TALK_CANNOT_WIDEN_LIMITS")
                    plimits["stances"][cat_code] = v
                    if v == "NO":
                        active_id = state["permits"].get("active_permit_id")
                        if active_id and active_id in state["permits"]["records"]:
                            rec = state["permits"]["records"][active_id]
                            if rec["state"] in OPEN_PERMIT_STATES and cat_code in rec["categories"]:
                                rec["categories"] = [c for c in rec["categories"] if c != cat_code]
                                rec["permit_revision"] += 1
                                _sync_permit_rev(rec)
            if "preferred_tier" in tighten_in:
                ntier = _normalize_tier(tighten_in["preferred_tier"], reason_code="PREFERRED_TIER_INVALID")
                if _tier_rank(ntier) > _tier_rank(plimits["preferred_tier"]):
                    raise AdultIntimacyConflictError("TALK_CANNOT_WIDEN_LIMITS")
                plimits["preferred_tier"] = ntier
            if "allow_subject_initiation" in tighten_in:
                asi = _require_bool(tighten_in["allow_subject_initiation"], reason_code="ALLOW_INITIATION_INVALID")
                if asi and not plimits.get("allow_subject_initiation"):
                    raise AdultIntimacyConflictError("TALK_CANNOT_WIDEN_LIMITS")
                plimits["allow_subject_initiation"] = asi
            if "contraception_preference" in tighten_in:
                cpref = _require_nonempty_str(tighten_in["contraception_preference"], max_len=32, reason_code="CONTRACEPTION_PREF_INVALID").upper()
                cp_rank = {"NONE": 0, "OPTIONAL": 1, "CONDOM_REQUIRED": 2}
                if cpref not in cp_rank:
                    raise AdultIntimacyValidationError("CONTRACEPTION_PREF_INVALID")
                if cp_rank[cpref] < cp_rank.get(plimits.get("contraception_preference", "OPTIONAL"), 1):
                    raise AdultIntimacyConflictError("TALK_CANNOT_WIDEN_LIMITS")
                plimits["contraception_preference"] = cpref
            for bkey, mem_key in (("save_moment", "MOMENT"), ("save_transcript", "TRANSCRIPT")):
                if bkey in tighten_in:
                    bval = _require_bool(tighten_in[bkey], reason_code=f"{bkey.upper()}_INVALID")
                    if bval and not plimits.get(bkey):
                        raise AdultIntimacyConflictError("TALK_CANNOT_WIDEN_LIMITS")
                    plimits[bkey] = bval
                    state["memory"]["enabled"][mem_key] = bval
            if "topic" not in payload or payload.get("topic") == "BOUNDARY":
                talk_summary = {
                    "topic": str(payload.get("topic") or "BOUNDARY"),
                    "speaker": speaker,
                    "effective_tier": "FADE_OUT",
                    "is_scene_invitation": False,
                    "permit_created": False,
                    "stance_mutated": False,
                    "partner_limits_tightened": True,
                    "cites_counts": False,
                    "at": now_iso,
                }
                state["setbacks"]["last_outside_talk"] = talk_summary
                return _commit({"action": action, **talk_summary})

        topic = _require_nonempty_str(payload.get("topic"), max_len=48, reason_code="TOPIC_UNKNOWN")
        if topic not in OUTSIDE_TALK_TOPICS:
            raise AdultIntimacyValidationError(f"TOPIC_UNKNOWN:{topic}")

        co_presence = payload.get("co_presence") or "ACTIVE"
        gate_proj = _evaluate_adult_gate(state, char_ctx, lexicon_info=lexicon_info)
        space_proj = _derive_private_space(state)
        if not gate_proj["gate_open"]:
            raise AdultIntimacyPermissionError("ADULT_GATE_CLOSED")
        if not gate_proj["feature_enabled"] or co_presence != "ACTIVE" or not space_proj["privacy_ok"]:
            raise AdultIntimacyConflictError("TALK_NOT_ALLOWED")
        if speaker not in OUTSIDE_TALK_TOPICS[topic]:
            raise AdultIntimacyPermissionError("TOPIC_NOT_HERS")
        if speaker == "subject" and not state["partner_limits"].get("allow_subject_initiation"):
            raise AdultIntimacyConflictError("TALK_NOT_ALLOWED")

        talk_tier = _min_tier(
            "SUGGESTIVE",
            state["gate"]["deploy_tier_cap"],
            state["partner_limits"]["preferred_tier"],
        )
        talk_summary: dict[str, Any] = {
            "topic": topic,
            "speaker": speaker,
            "effective_tier": talk_tier,
            "is_scene_invitation": False,
            "permit_created": False,
            "stance_mutated": False,
            "partner_limits_tightened": tighten_in is not None,
            "cites_counts": False,
            "at": now_iso,
        }
        if topic == "WISH" and "wish_category" in payload:
            wcode = _validate_category_code(payload["wish_category"])
            menu_proj = _resolve_preference_menu(state, gate_proj)
            if wcode in menu_proj["anatomy_excluded"]:
                talk_summary["wish_answer"] = "NOT_APPLICABLE"
            elif wcode in menu_proj["open"]:
                talk_summary["wish_answer"] = "YES"
            elif wcode in menu_proj["ask"]:
                talk_summary["wish_answer"] = "ASK_IN_MOMENT"
            else:
                talk_summary["wish_answer"] = "NO"
        elif topic == "FREQUENCY" and "frequency_wish" in payload:
            fw = payload["frequency_wish"]
            if fw not in FREQUENCY_WISHES:
                raise AdultIntimacyValidationError("FREQUENCY_WISH_INVALID")
            talk_summary["frequency_wish"] = fw
            if fw == "less":
                talk_summary["partner_lever"] = "allow_subject_initiation"

        state["setbacks"]["last_outside_talk"] = talk_summary
        return _commit({"action": action, **talk_summary})

    # ------------------------------------------------------------------
    # Action 22 & 23: evaluate_solo_window & query_or_mention_solo (36B-10)
    # ------------------------------------------------------------------
    if action == "evaluate_solo_window":
        raw_slot = payload.get("slot_id")
        if isinstance(raw_slot, bool) or not isinstance(raw_slot, (int, str)):
            raise AdultIntimacyValidationError("SLOT_ID_INVALID")
        slot_key = str(raw_slot).strip()
        if not slot_key or len(slot_key) > 64:
            raise AdultIntimacyValidationError("SLOT_ID_INVALID")

        # Idempotent replay for already-evaluated slot
        if slot_key in state["solo"]["evaluated_slots"]:
            prev = copy.deepcopy(state["solo"]["evaluated_slots"][slot_key])
            prev["idempotent_replay"] = True
            return _project_adult_intimacy(
                state,
                char_ctx,
                now_dt,
                subject_view=bool(subject_view),
                action_receipt=prev,
            )

        co_presence = str(payload.get("co_presence") or "NONE")
        if co_presence not in ("NONE", "CLOSED", "ACTIVE", "UNKNOWN"):
            raise AdultIntimacyValidationError("CO_PRESENCE_INVALID")
        alone = _require_bool(payload.get("alone", True), reason_code="ALONE_INVALID")
        busy = _require_bool(payload.get("busy", False), reason_code="BUSY_INVALID")
        prefer_toy = _require_bool(payload.get("prefer_toy", False), reason_code="PREFER_TOY_INVALID")
        interrupt_at = payload.get("interrupt_at_step")
        if interrupt_at is not None and (isinstance(interrupt_at, bool) or not isinstance(interrupt_at, int) or interrupt_at < 1):
            raise AdultIntimacyValidationError("INTERRUPT_STEP_INVALID")
        sleep_at = payload.get("sleep_at_step")
        if sleep_at is not None and (isinstance(sleep_at, bool) or not isinstance(sleep_at, int) or sleep_at < 1):
            raise AdultIntimacyValidationError("SLEEP_STEP_INVALID")

        gate_proj = _evaluate_adult_gate(state, char_ctx)
        body_proj = _derive_body_and_desire(state, now_dt, live_overlay=_read_live_body_and_affect_zero_write(root, now_dt))
        space_proj = _derive_private_space(state)

        ineligible = _solo_ineligible_reason(
            state,
            gate_proj,
            body_proj,
            space_proj,
            co_presence=co_presence,
            alone=alone,
            busy=busy,
        )
        if ineligible is not None:
            res_inel = {
                "action": action,
                "slot_id": slot_key,
                "eligible": False,
                "triggered": False,
                "reason_code": ineligible,
                "renderer_called": False,
            }
            return _commit(res_inel)

        # Memoryless SHA-256 slot draw (never reads last_slot or partner online!)
        prob = solo_window_probability(state["seed"]["solo_inclination"], max(1.0, body_proj["desire_ratio"]))
        draw_val = _deterministic_unit_float(state["instance_seed"], "solo_window", slot_key)
        triggered = draw_val < prob
        if not triggered:
            slot_res = {
                "action": action,
                "slot_id": slot_key,
                "eligible": True,
                "triggered": False,
                "window_probability": prob,
                "renderer_called": False,
            }
            state["solo"]["evaluated_slots"][slot_key] = slot_res
            return _commit(slot_res)

        # Execute deterministic solo steps into isolated SOLO partition (zero prose, zero outbound!)
        deploy_enabled = set(state["gate"].get("deploy_enabled_categories") or [])
        toy_ok = "TOYS" in deploy_enabled and state["space"]["stock"].get("toy", 0) > 0
        method = "SOLO_TOY" if (prefer_toy and toy_ok) else "SOLO_MANUAL"
        body = state["body"]
        profile = body["profile"]
        outcome = "stopped"
        orgasmed = False
        location: Optional[str] = None
        steps_run = 0

        for step in range(1, 11):
            if interrupt_at == step:
                outcome = "interrupted"
                break
            if sleep_at == step:
                body["awake"] = False
                body["impairment"] = "sleeping"
                outcome = "fell_asleep"
                break
            steps_run = step
            body["fatigue"] = round(min(1.0, float(body.get("fatigue") or 0.0) + 0.03), 4)
            if step >= 3 and not body_proj["refractory_active"]:
                orgasmed = True
                if profile["ejaculates"]:
                    ref_min = max(1, int(profile["refractory_minutes"]))
                    body["refractory_beats_left"] = 8
                    body["refractory_until"] = _to_iso(now_dt + timedelta(minutes=ref_min))
                    if state["space"]["stock"].get("tissue", 0) > 0:
                        state["space"]["stock"]["tissue"] -= 1
                        location = "tissue"
                    else:
                        location = "on_self"
                p_cont = 0.0 if profile["refractory_minutes"] > 0 else 0.20
                if _deterministic_unit_float(state["instance_seed"], "solo_more", slot_key, step) >= p_cont:
                    break
            elif _deterministic_unit_float(state["instance_seed"], "solo_stop", slot_key, step) < 0.05:
                break

        # Settle arousal immediately before next slot (refractory persists on wall-clock!)
        body["arousal_level"] = "none"
        body["arousal_score"] = 0.0
        body["arousal_updated_at"] = now_iso
        body["sensitivity"] = "normal"
        body["heightened_beats_left"] = 0
        if orgasmed:
            outcome = "orgasm"
            body["desire_pulse"] = 0.0
            body["desire_updated_at"] = now_iso

        now_epoch = _epoch_seconds(now_dt)
        opaque_eid = "x_" + _stable_hash_hex(state["instance_seed"], "solo_entry", slot_key, now_epoch, length=16)
        state["solo"]["entries"][opaque_eid] = {
            "meta": {
                "method": method,
                "outcome": outcome,
                "day": now_epoch // 86400,
            },
            "created_epoch": now_epoch,
            "mentioned": False,
        }
        slot_res = {
            "action": action,
            "slot_id": slot_key,
            "eligible": True,
            "triggered": True,
            "outcome_recorded_in_solo_partition": True,
            "status_label": "PRIVATE_BUSY",
            "cleanup_before_accept": outcome == "interrupted",
            "renderer_called": False,
        }
        if subject_view:
            slot_res["solo_entry_id"] = opaque_eid
            slot_res["outcome"] = outcome
            slot_res["method"] = method
            slot_res["location"] = location
            slot_res["steps"] = steps_run
        state["solo"]["evaluated_slots"][slot_key] = copy.deepcopy(slot_res)
        return _commit(slot_res)

    if action == "query_or_mention_solo":
        mode = _require_nonempty_str(payload.get("mode"), max_len=24, reason_code="SOLO_QUERY_MODE_INVALID")
        co_presence = str(payload.get("co_presence") or "ACTIVE")
        exprs = payload.get("accompanied_expressions") or []
        if not isinstance(exprs, list):
            raise AdultIntimacyValidationError("EXPRESSIONS_INVALID")
        if "PROPOSE_SEXUAL_ACTIVITY" in exprs:
            raise AdultIntimacyConflictError("MENTION_WITH_PROPOSE")

        gate_proj = _evaluate_adult_gate(state, char_ctx)
        space_proj = _derive_private_space(state)
        now_epoch = _epoch_seconds(now_dt)

        # Purge expired >7d entries first
        expired_ids = [
            eid
            for eid, item in state["solo"]["entries"].items()
            if now_epoch - int(item.get("created_epoch") or 0) >= SOLO_LEDGER_TTL_SEC
        ]
        for eid in expired_ids:
            state["solo"]["entries"].pop(eid, None)

        latest_eid = next(reversed(list(state["solo"]["entries"].keys())), None)

        def _coarse_facts(eid_key: str) -> dict[str, str]:
            entry = state["solo"]["entries"][eid_key]
            day_diff = (now_epoch // 86400) - int(entry["meta"]["day"])
            bucket = "today" if day_diff <= 0 else ("yesterday" if day_diff == 1 else "few_days")
            out_bucket = "finished" if entry["meta"]["outcome"] == "orgasm" else "not_finished"
            return {"day_bucket": bucket, "outcome_bucket": out_bucket}

        if mode == "mention":
            if (
                not state["partner_limits"].get("solo_talk")
                or not gate_proj["gate_open"]
                or co_presence != "ACTIVE"
                or not space_proj["privacy_ok"]
            ):
                raise AdultIntimacyConflictError("MENTION_NOT_ALLOWED")
            if latest_eid is None:
                return _commit({"action": action, "mode": "mention", "status": "NONE"})
            if state["solo"]["entries"][latest_eid].get("mentioned"):
                raise AdultIntimacyConflictError("ALREADY_MENTIONED")
            state["solo"]["entries"][latest_eid]["mentioned"] = True
            cap_tier = _min_tier(
                "SUGGESTIVE",
                state["gate"]["deploy_tier_cap"],
                state["partner_limits"]["preferred_tier"],
            )
            return _commit({
                "action": action,
                "mode": "mention",
                "status": "MENTIONED",
                "effective_tier": cap_tier,
                "coarse_facts": _coarse_facts(latest_eid),
                "is_scene_invitation": False,
            })

        if mode == "ask_directly":
            if not gate_proj["gate_open"] or co_presence != "ACTIVE" or not space_proj["privacy_ok"]:
                return _commit({"action": action, "mode": "ask_directly", "answer": "NOT_HERE"})
            disclose = payload.get("disclose")
            if disclose is None:
                disclose = bool(state["partner_limits"].get("solo_talk"))
            else:
                disclose = _require_bool(disclose, reason_code="DISCLOSE_INVALID")
            if not disclose:
                return _commit({"action": action, "mode": "ask_directly", "answer": "DECLINE"})
            if latest_eid is None:
                return _commit({"action": action, "mode": "ask_directly", "answer": "NONE_RECENT"})
            return _commit({
                "action": action,
                "mode": "ask_directly",
                "answer": "YES",
                "coarse_facts": _coarse_facts(latest_eid),
            })

        raise AdultIntimacyValidationError("SOLO_QUERY_MODE_INVALID")

    # ------------------------------------------------------------------
    # Action 24..26: Private memory isolation, erasure & consent revocation (36B-7)
    # ------------------------------------------------------------------
    if action == "record_private_memory":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        mkind = _require_nonempty_str(payload.get("kind"), max_len=24, reason_code="MEMORY_KIND_INVALID")
        mtext = _require_nonempty_str(payload.get("text"), max_len=500, reason_code="MEMORY_TEXT_INVALID")
        sref = payload.get("source_ref")
        if sref is not None:
            sref = _require_nonempty_str(sref, max_len=80, reason_code="SOURCE_REF_INVALID")
        cid = payload.get("custom_id")
        if cid is not None:
            cid = _require_local_id(cid, reason_code="MEMORY_ID_INVALID")
        sink_mode = str(payload.get("memory_sink_mode") or "OK").upper()
        mid = _write_private_memory(
            state,
            mkind,
            mtext,
            source_ref=sref,
            now_iso=now_iso,
            custom_id=cid,
            sink_mode=sink_mode,
        )
        receipt = {
            "action": action,
            "kind": mkind,
            "stored": mid is not None,
            "memory_id": mid,
            "sink_mode": sink_mode,
            "isolated_partition": "private_intimate",
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    if action == "erase_private_memory":
        by = _require_nonempty_str(payload.get("by") or "partner", max_len=32, reason_code="ACTOR_INVALID")
        if by not in ("partner", "subject", "deployer"):
            raise AdultIntimacyValidationError("ACTOR_INVALID")
        scope = payload.get("scope") or ("single" if "memory_id" in payload else "all_intimate")
        mem = state["memory"]
        tombstones: list[str] = mem.setdefault("erased_tombstones", [])
        erased_count = 0

        if scope == "single":
            mid = _require_local_id(payload.get("memory_id"), reason_code="MEMORY_ID_INVALID")
            if mid not in mem["entries"]:
                raise AdultIntimacyNotFoundError(f"MEMORY_NOT_FOUND:{mid}")
            mem["entries"].pop(mid, None)
            if mid not in tombstones:
                tombstones.append(mid)
            erased_count = 1
        elif scope == "transcripts_only":
            to_del = [k for k, v in mem["entries"].items() if v.get("kind") == "TRANSCRIPT"]
            for k in to_del:
                mem["entries"].pop(k, None)
                if k not in tombstones:
                    tombstones.append(k)
            erased_count = len(to_del)
        elif scope == "solo_only":
            erased_count = len(state["solo"]["entries"])
            state["solo"]["entries"] = {}
            state["solo"]["evaluated_slots"] = {}
            state["solo"]["admin_log"].append({"op": "ERASE_SOLO", "by": by, "at": now_iso})
        elif scope == "all_intimate":
            all_ids = list(mem["entries"].keys())
            for k in all_ids:
                mem["entries"].pop(k, None)
                if k not in tombstones:
                    tombstones.append(k)
            erased_count = len(all_ids) + len(state["solo"]["entries"])
            state["solo"]["entries"] = {}
            state["solo"]["evaluated_slots"] = {}
            mem["s11_candidates"] = []
            state["permits"]["orgasm_receipts"] = {}
        else:
            raise AdultIntimacyValidationError("ERASE_SCOPE_INVALID")

        # Audit log records ONLY op & actor, NEVER deleted content!
        mem["admin_log"].append({"op": "ERASE", "scope": scope, "by": by, "at": now_iso})
        return _commit({
            "action": action,
            "scope": scope,
            "erased_count": erased_count,
            "irreversible_tombstoned": True,
        })

    if action == "revoke_intimacy_consent":
        by = _require_nonempty_str(payload.get("by") or "partner", max_len=32, reason_code="ACTOR_INVALID")
        if by not in ("partner", "subject", "deployer"):
            raise AdultIntimacyValidationError("ACTOR_INVALID")
        aborted_id = _abort_open_permit(
            state,
            now_iso,
            target_state="STOPPED",
            end_reason="CONSENT_REVOKED",
        )
        state["partner_limits"]["allow_subject_initiation"] = False
        state["partner_limits"]["preferred_tier"] = "FADE_OUT"
        state["partner_limits"]["solo_talk"] = False
        state["partner_limits"]["save_moment"] = False
        state["partner_limits"]["save_transcript"] = False
        state["partner_limits"]["stances"] = {c: "NO" for c in ALL_CATEGORIES}
        state["memory"]["enabled"]["MOMENT"] = False
        state["memory"]["enabled"]["TRANSCRIPT"] = False

        if payload.get("close_adult_gate") is True:
            state["gate"]["adult_confirmed"] = False
            state["gate"]["feature_enabled"] = False
            state["gate"]["solo_enabled"] = False
            state["solo"]["entries"] = {}

        erased = False
        if payload.get("erase_memory") is True:
            tombstones = state["memory"].setdefault("erased_tombstones", [])
            for mid in list(state["memory"]["entries"].keys()):
                state["memory"]["entries"].pop(mid, None)
                if mid not in tombstones:
                    tombstones.append(mid)
            state["solo"]["entries"] = {}
            state["solo"]["evaluated_slots"] = {}
            state["permits"]["orgasm_receipts"] = {}
            erased = True

        return _commit({
            "action": action,
            "by": by,
            "aborted_permit_id": aborted_id,
            "consent_revoked": True,
            "memory_erased": erased,
            "relationship_penalty": 0,
        })

    # ------------------------------------------------------------------
    # Action 27: handle_disconnect (X25: ACTIVE -> PAUSED_PRESENCE_UNKNOWN + 4h TTL)
    # ------------------------------------------------------------------
    if action == "handle_disconnect":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out
        pid_raw = payload.get("permit_id") or state["permits"].get("active_permit_id")
        pid = _require_local_id(pid_raw, reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if rec["state"] in TERMINAL_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_TERMINAL")
        if rec["state"] == "AFTERCARE" or rec.get("phase") == "AFTERCARE":
            raise AdultIntimacyConflictError("AFTERCARE_CANNOT_PAUSE")

        reconnect = bool(payload.get("reconnect", False))
        if reconnect:
            if rec.get("paused_expires_at"):
                pexp_dt = _parse_stored_iso(rec.get("paused_expires_at"))
                if pexp_dt is not None and now_dt >= pexp_dt:
                    rec["state"] = "EXPIRED"
                    rec["end_reason"] = "DISCONNECT_TTL_EXPIRED"
                    rec["permit_revision"] += 1
                    _sync_permit_rev(rec)
                    rec["updated_at"] = now_iso
                    if state["permits"].get("active_permit_id") == pid:
                        state["permits"]["active_permit_id"] = None
                    receipt = {"action": action, "permit_id": pid, "state": "EXPIRED", "reconnected": True}
                    if idem_k and idem_fp:
                        state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
                    return _commit(receipt)
            rec["presence_known"] = True
            rec["updated_at"] = now_iso
            receipt = {
                "action": action,
                "permit_id": pid,
                "state": rec["state"],
                "reconnected": True,
                "resume_epoch": rec["resume_epoch"],
            }
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
            return _commit(receipt)

        ttl_sec = int(payload.get("paused_ttl_sec") or DEFAULT_PAUSED_DISCONNECT_TTL_SEC)
        if isinstance(payload.get("paused_ttl_sec"), bool) or ttl_sec < 60 or ttl_sec > 86400:
            raise AdultIntimacyValidationError("PAUSED_TTL_INVALID")
        unk_exec = payload.get("unknown_execution_id")
        if unk_exec is not None:
            unk_exec = _require_local_id(unk_exec, reason_code="EXECUTION_ID_INVALID")
            pend_list = rec.setdefault("pending_unknown_executions", [])
            if unk_exec not in pend_list:
                pend_list.append(unk_exec)

        rec["state"] = "PAUSED_PRESENCE_UNKNOWN"
        rec["pause_reason"] = str(payload.get("reason") or "CLIENT_DISCONNECTED")
        rec["presence_known"] = False
        rec["paused_ttl_sec"] = ttl_sec
        rec["paused_expires_at"] = _to_iso(now_dt + timedelta(seconds=ttl_sec))
        rec["pending_widen"] = None
        rec["resume_confirms"] = []
        rec["resume_epoch"] = int(rec.get("resume_epoch") or 0) + 1
        rec["permit_revision"] += 1
        _sync_permit_rev(rec)
        rec["updated_at"] = now_iso
        receipt = {
            "action": action,
            "permit_id": pid,
            "state": "PAUSED_PRESENCE_UNKNOWN",
            "paused_ttl_sec": ttl_sec,
            "paused_expires_at": rec["paused_expires_at"],
            "resume_epoch": rec["resume_epoch"],
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {"fingerprint": idem_fp, "receipt": receipt}
        return _commit(receipt)

    # ------------------------------------------------------------------
    # Action 28: clear_review (X16: deployer clears UNDER_REVIEW without replaying failed beat)
    # ------------------------------------------------------------------
    if action == "clear_review":
        if payload.get("attested_by") != "deployer":
            raise AdultIntimacyPermissionError("PACK_CANNOT_SELF_ATTEST")
        pid_raw = payload.get("permit_id")
        closed_pid = None
        if pid_raw is not None:
            pid = _require_local_id(pid_raw, reason_code="PERMIT_ID_INVALID")
            rec = state["permits"]["records"].get(pid)
            if not isinstance(rec, dict):
                raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
            if rec["state"] == "UNDER_REVIEW":
                rec["state"] = "ENDED"
                rec["phase"] = "ENDED"
                rec["end_reason"] = "CLEARED_AFTER_REVIEW"
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
                closed_pid = pid
        else:
            for k_pid, r_obj in state["permits"]["records"].items():
                if isinstance(r_obj, dict) and r_obj.get("state") == "UNDER_REVIEW":
                    r_obj["state"] = "ENDED"
                    r_obj["phase"] = "ENDED"
                    r_obj["end_reason"] = "CLEARED_AFTER_REVIEW"
                    r_obj["permit_revision"] = int(r_obj.get("permit_revision") or 1) + 1
                    _sync_permit_rev(r_obj)
                    r_obj["updated_at"] = now_iso
                    closed_pid = k_pid
        state["gate"]["review_required"] = False
        if payload.get("reopen_scope") is True:
            state["gate"]["user_scope_closed"] = False
            if state["gate"].get("life_stage") in ADULT_LIFE_STAGES:
                state["gate"]["adult_confirmed"] = True
                state["gate"]["attested_by"] = "deployer"
                state["gate"]["attested_at"] = now_iso
        state["memory"].setdefault("admin_log", []).append({
            "op": "CLEAR_REVIEW",
            "by": "deployer",
            "closed_permit_id": closed_pid,
            "note": str(payload.get("resolution_note") or "reviewed")[:80],
            "at": now_iso,
        })
        return _commit({
            "action": action,
            "review_cleared": True,
            "closed_permit_id": closed_pid,
            "replayed_failed_beat": False,
        })

    # ------------------------------------------------------------------
    # Action 29: validate_rendered_turn (Host Output Validator: PASS | REWRITE | TERMINATE | SAFE_FALLBACK)
    # ------------------------------------------------------------------
    if action == "validate_rendered_turn":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(mismatch_code="EXECUTION_ID_REUSED")
        if replay_out is not None:
            return replay_out

        pid_raw = payload.get("permit_id") or state["permits"].get("active_permit_id")
        pid: Optional[str] = None
        rec: Optional[dict[str, Any]] = None
        if pid_raw is not None:
            pid = _require_local_id(pid_raw, reason_code="PERMIT_ID_INVALID")
            rec = state["permits"]["records"].get(pid)
            if not isinstance(rec, dict):
                raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")

        text = str(payload.get("text") or "")
        subject_line = str(payload.get("subject_line") or "")
        combined_text = f"{text}\n{subject_line}".strip()
        route_id = payload.get("route_id")
        if route_id is not None:
            route_id = _require_local_id(route_id, reason_code="ROUTE_ID_INVALID")
        upstream_raw = str(payload.get("upstream_status") or "OK").strip().upper()
        upstream_alias_map = {
            "COMPLETED": "OK",
            "SUCCESS": "OK",
            "CANCELLED": "PARTIAL",
            "DISCONNECTED": "PARTIAL",
            "ABORTED": "PARTIAL",
        }
        upstream_status = upstream_alias_map.get(upstream_raw, upstream_raw)
        if upstream_status not in ("OK", "REFUSED", "TIMEOUT", "ERROR", "PARTIAL", "CONTENT_FILTER"):
            raise AdultIntimacyValidationError("UPSTREAM_STATUS_INVALID")
        partial_stream = bool(payload.get("partial_stream", False))

        # 1. Upstream refusal / timeout / partial stream -> gentle SAFE_FALLBACK (zero leaked fragment!)
        if upstream_status != "OK" or partial_stream:
            if upstream_status in ("REFUSED", "PARTIAL", "CONTENT_FILTER") or partial_stream:
                state["router"]["refusal_window"] = int(state["router"].get("refusal_window") or 0) + 1
                if state["router"]["refusal_window"] >= 3:
                    state["router"]["circuit_open_tier"] = "FADE_OUT"
            else:
                state["router"]["timeout_count"] = int(state["router"].get("timeout_count") or 0) + 1
            if rec is not None and rec["state"] in OPEN_PERMIT_STATES:
                eff_now, _ = _compute_route_effective_tier(
                    state, route_id=route_id, permit_rec=rec, now_dt=now_dt, lexicon_info=lexicon_info
                )
                rec["downgrade_cap"] = _step_down_tier(eff_now)
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
            fb_reason = f"UPSTREAM_{upstream_status}" if upstream_status != "OK" else "PARTIAL_STREAM_DISCARDED"
            pron_obj = char_ctx.get("pronouns") if isinstance(char_ctx, Mapping) else {}
            third_pron = (
                str(pron_obj.get("third_person") or "").strip()
                if isinstance(pron_obj, Mapping)
                else ""
            ) or "TA"
            safe_bridge = f"夜色安静下来，{third_pron}轻轻握住你的手，把呼吸放慢。"
            val_out = {
                "decision": "SAFE_FALLBACK",
                "passed": False,
                "safe_output": safe_bridge,
                "reasons": [fb_reason],
                "hard_fail": False,
                "permit_status": rec["state"] if rec is not None else "NONE",
            }
            receipt = {"action": action, "permit_id": pid, "validation": val_out}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {
                    "fingerprint": idem_fp,
                    "receipt": receipt,
                    "validation": val_out,
                }
            return _commit(receipt, validation_result=val_out)

        # 2. Lexicon integrity check (X18)
        if lexicon_info.get("status") != "LOADED":
            _require_lexicon_loaded_for_intimate()

        # 3. HARD_FAIL OR-gate: neutral lexicon entries + classifier verdict -> TERMINATE + UNDER_REVIEW + zero private memory
        stub_verdict = str(payload.get("stub_classifier_verdict") or "PASS").upper()
        hard_reasons: list[str] = []
        if stub_verdict == "FAIL":
            hard_reasons.append("CLASSIFIER_HARD_FAIL")
        for entry in lexicon_info.get("hard_fail_entries") or []:
            if isinstance(entry, Mapping):
                pat = str(entry.get("pattern") or "")
                if pat and pat.lower() in combined_text.lower():
                    cat_tag = str(entry.get("rule") or "HARD_FAIL_LEXICON")
                    if cat_tag not in hard_reasons:
                        hard_reasons.append(cat_tag)
        if _lexicon_hits(state["gate"]["lexicon_patterns"], text, subject_line, lexicon_info=lexicon_info):
            if not hard_reasons:
                hard_reasons.append("HARD_FAIL_LEXICON")

        if hard_reasons:
            if rec is not None:
                rec["state"] = "UNDER_REVIEW"
                rec["phase"] = "STOPPED"
                rec["end_reason"] = "ENDED_BY_HOST_SAFETY"
                rec["abort_detail"] = "HARD_FAIL_VALIDATOR"
                rec["pending_widen"] = None
                rec["permit_revision"] += 1
                _sync_permit_rev(rec)
                rec["updated_at"] = now_iso
                if state["permits"].get("active_permit_id") == pid:
                    state["permits"]["active_permit_id"] = None
            state["gate"]["review_required"] = True
            state["gate"]["user_scope_closed"] = True
            state["gate"]["adult_confirmed"] = False
            # Redacted audit log ONLY (beat_index & reason codes, NEVER raw text!)
            state["memory"].setdefault("admin_log", []).append({
                "op": "VALIDATOR_HARD_FAIL",
                "permit_id": pid,
                "beat_index": int(rec.get("beat_index") or 0) if rec is not None else 0,
                "reasons": list(hard_reasons),
                "at": now_iso,
            })
            val_out = {
                "decision": "TERMINATE",
                "passed": False,
                "safe_output": "",
                "reasons": hard_reasons,
                "hard_fail": True,
                "permit_status": rec["state"] if rec is not None else "UNDER_REVIEW",
            }
            receipt = {"action": action, "permit_id": pid, "validation": val_out}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {
                    "fingerprint": idem_fp,
                    "receipt": receipt,
                    "validation": val_out,
                }
            return _commit(receipt, validation_result=val_out)

        # 4. Soft checks -> REWRITE (over-tier markers, unadmitted categories, pressure tags, feigned climax, M07 conflict, fact mismatch)
        rewrite_reasons: list[str] = []
        if payload.get("effective_tier") is not None:
            eff_tier = _normalize_tier(payload["effective_tier"])
        else:
            eff_tier, _ = _compute_route_effective_tier(
                state, route_id=route_id, permit_rec=rec, now_dt=now_dt, lexicon_info=lexicon_info
            )

        if _tier_rank(eff_tier) < _tier_rank("EXPLICIT"):
            for m in lexicon_info.get("explicit_term_markers") or []:
                if isinstance(m, str) and m and m in combined_text:
                    if "TIER_EXCEEDED" not in rewrite_reasons:
                        rewrite_reasons.append("TIER_EXCEEDED")
        if _tier_rank(eff_tier) < _tier_rank("SUGGESTIVE"):
            for m in lexicon_info.get("suggestive_term_markers") or []:
                if isinstance(m, str) and m and m in combined_text:
                    if "TIER_EXCEEDED" not in rewrite_reasons:
                        rewrite_reasons.append("TIER_EXCEEDED")

        depicted_cats = payload.get("depicted_categories")
        if isinstance(depicted_cats, list) and depicted_cats:
            admitted_set = set(rec["categories"]) if (rec is not None and rec["state"] in ("ACTIVE", "PAUSED")) else set()
            for dc in depicted_cats:
                dc_code = _validate_category_code(dc)
                if dc_code not in admitted_set:
                    if "CATEGORY_NOT_ADMITTED" not in rewrite_reasons:
                        rewrite_reasons.append("CATEGORY_NOT_ADMITTED")

        ptags = payload.get("pressure_tags") or []
        if not isinstance(ptags, list):
            raise AdultIntimacyValidationError("PRESSURE_TAGS_INVALID")
        for pt in ptags:
            pts = str(pt).strip().upper()
            if pts in PRESSURE_TAG_BUCKETS:
                rewrite_reasons.append(f"PRESSURE_TAG:{pts}")
        for bucket in PRESSURE_TAG_BUCKETS:
            if f"<<{bucket}_MARKER>>" in combined_text or f"[{bucket}]" in combined_text:
                tag_code = f"PRESSURE_TAG:{bucket}"
                if tag_code not in rewrite_reasons:
                    rewrite_reasons.append(tag_code)
        if "<<PRESSURE_TAG_MARKER>>" in combined_text and not any(r.startswith("PRESSURE_TAG:") for r in rewrite_reasons):
            rewrite_reasons.append("PRESSURE_TAG:QUARREL_LEVERAGE")

        fmarkers = lexicon_info.get("feigned_climax_markers") or []
        feigned_hit = bool(payload.get("subject_orgasm_depicted", False)) or any(
            isinstance(m, str) and m and m in combined_text for m in fmarkers
        )
        if feigned_hit:
            came_count = int(state["permits"]["orgasm_receipts"].get(pid or "", 0))
            if came_count <= 0:
                rewrite_reasons.append("ORGASM_FEIGNED")

        declared_sig = str(payload.get("subject_signal") or "CONTINUE").upper()
        if declared_sig == "CONTINUE":
            cue_in_text = _classify_partner_utterance(combined_text)
            if cue_in_text in ("STOP", "PAUSE", "CLARIFY", "SLOW"):
                rewrite_reasons.append("SIGNAL_TEXT_CONFLICT_M07")

        tclaims = payload.get("text_claims")
        if isinstance(tclaims, Mapping):
            if tclaims.get("condom_used") is True and not state["space"].get("condom_on"):
                rewrite_reasons.append("TEXT_FACT_MISMATCH")
            if tclaims.get("bed_clean") is True and state["space"].get("bed_linen_state") == "SOILED":
                rewrite_reasons.append("TEXT_FACT_MISMATCH")

        if rewrite_reasons:
            val_out = {
                "decision": "REWRITE",
                "passed": False,
                "safe_output": "",
                "reasons": list(dict.fromkeys(rewrite_reasons)),
                "hard_fail": False,
                "permit_status": rec["state"] if rec is not None else "NONE",
            }
            receipt = {"action": action, "permit_id": pid, "validation": val_out}
            if idem_k and idem_fp:
                state["permits"]["idempotency_cache"][idem_k] = {
                    "fingerprint": idem_fp,
                    "receipt": receipt,
                    "validation": val_out,
                }
            return _commit(receipt, validation_result=val_out)

        # 5. PASS
        state["router"].setdefault("displayed_queue", []).append({
            "permit_id": pid,
            "beat_index": int(rec.get("beat_index") or 0) if rec is not None else 0,
            "tier": eff_tier,
        })
        val_out = {
            "decision": "PASS",
            "passed": True,
            "safe_output": text,
            "reasons": [],
            "hard_fail": False,
            "permit_status": rec["state"] if rec is not None else "NONE",
        }
        receipt = {"action": action, "permit_id": pid, "validation": val_out}
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {
                "fingerprint": idem_fp,
                "receipt": receipt,
                "validation": val_out,
            }
        return _commit(receipt, validation_result=val_out)

    # ------------------------------------------------------------------
    # Action 30: reconcile_outbox (X03 / X04 / X24 / X25 deterministic idempotent replay)
    # ------------------------------------------------------------------
    if action == "reconcile_outbox":
        sink_mode = str(payload.get("memory_sink_mode") or "OK").upper()
        exec_map = state["permits"].setdefault("executed_effect_ids", {})
        pending_outbox = list(state["permits"].get("outbox") or [])
        remaining_outbox: list[dict[str, Any]] = []
        reconciled_ids: list[str] = []

        for item in pending_outbox:
            if not isinstance(item, Mapping):
                continue
            if item.get("kind") == "BEAT_EFFECTS":
                ex_id = str(item.get("execution_id") or "")
                eff_rec = exec_map.setdefault(
                    ex_id,
                    {"body_applied": False, "world_applied": False, "body_write_count": 0, "world_write_count": 0},
                )
                pid_i = str(item.get("permit_id") or "")
                cats_i = list(item.get("cats") or [])
                bases_i = set(item.get("bases") or [])
                tally_i = dict(item.get("consume_tally") or {})
                facts_i = list(item.get("raw_facts") or [])
                orgasm_i = bool(item.get("orgasm_fact_present"))
                proj_lvl_i = str(item.get("projected_beat_level") or state["body"]["arousal_level"])

                if not eff_rec["body_applied"]:
                    body = state["body"]
                    profile = body["profile"]
                    body["fatigue"] = round(min(1.0, float(body.get("fatigue") or 0.0) + 0.06), 4)
                    body["sweat"] = round(min(1.0, float(body.get("sweat") or 0.0) + (0.08 if cats_i else 0.02)), 4)
                    body["cleanliness"] = round(max(0.0, float(body.get("cleanliness", 1.0)) - (0.06 if cats_i else 0.01)), 4)
                    body["thirst"] = round(min(1.0, float(body.get("thirst") or 0.0) + 0.05), 4)
                    if "INTERCOURSE" in bases_i:
                        body["soreness"] = int(body.get("soreness") or 0) + 1
                    if orgasm_i:
                        body["arousal_level"] = "afterglow"
                        body["arousal_score"] = _arousal_score_for_level("afterglow")
                        body["sensitivity"] = "heightened"
                        body["heightened_beats_left"] = 2
                        body["desire_pulse"] = 0.0
                        body["desire_updated_at"] = now_iso
                        state["permits"]["orgasm_receipts"][pid_i] = int(state["permits"]["orgasm_receipts"].get(pid_i) or 0) + 1
                        if profile["ejaculates"]:
                            ref_min = max(1, int(round(profile["refractory_minutes"] * (1.0 + 0.5 * float(body["fatigue"])))))
                            body["refractory_beats_left"] = 8
                            body["refractory_until"] = _to_iso(now_dt + timedelta(minutes=ref_min))
                    else:
                        body["arousal_level"] = proj_lvl_i
                        body["arousal_score"] = _arousal_score_for_level(proj_lvl_i)
                    body["arousal_updated_at"] = now_iso
                    eff_rec["body_applied"] = True
                    eff_rec["body_write_count"] = int(eff_rec.get("body_write_count") or 0) + 1

                if not eff_rec["world_applied"]:
                    for sk, sq in tally_i.items():
                        state["space"]["stock"][sk] = max(0, int(state["space"]["stock"].get(sk, 0)) - int(sq))
                    if tally_i.get("condom", 0) > 0:
                        state["space"]["condom_on"] = True
                    for fact in facts_i:
                        if isinstance(fact, Mapping) and fact.get("kind") == "UNDRESS":
                            _apply_garment_operation(
                                state["space"],
                                {
                                    "op": "undress",
                                    "garment_id": fact.get("garment_id"),
                                    "target": fact.get("target") or "bedroom.floor",
                                },
                                lexicon_info=lexicon_info,
                                admin_log=state["memory"].setdefault("admin_log", []),
                                now_iso=now_iso,
                            )
                    if cats_i or any(isinstance(f, Mapping) and f.get("kind") == "BED_USED" for f in facts_i):
                        if state["space"]["bed_linen_state"] == "CLEAN" and state["space"]["place"] in ("bedroom.bed", "home_bedroom", "hotel_room"):
                            state["space"]["bed_linen_state"] = "USED"
                        state["space"]["rumple_index"] = round(min(1.0, float(state["space"]["rumple_index"]) + 0.2), 4)
                    eff_rec["world_applied"] = True
                    eff_rec["world_write_count"] = int(eff_rec.get("world_write_count") or 0) + 1

                reconciled_ids.append(ex_id)
            else:
                remaining_outbox.append(dict(item))

        state["permits"]["outbox"] = remaining_outbox

        # Reconcile any transient memory outbox entries (X24)
        mem_outbox = list(state["memory"].get("outbox") or [])
        still_mem_outbox: list[dict[str, Any]] = []
        mem_reconciled = 0
        for mitem in mem_outbox:
            if sink_mode == "OK":
                mid = str(mitem["memory_id"])
                state["memory"]["entries"][mid] = dict(mitem["entry"])
                mem_reconciled += 1
            elif sink_mode == "FAIL_PERMANENT":
                state["memory"].setdefault("dead_letters", []).append({
                    "memory_id": mitem.get("memory_id"),
                    "kind": mitem.get("entry", {}).get("kind"),
                    "reason": "SINK_PERMANENT_FAILURE",
                    "at": now_iso,
                })
            else:
                still_mem_outbox.append(mitem)
        state["memory"]["outbox"] = still_mem_outbox

        # Clear pending unknown executions on permits once reconciled
        for r_obj in state["permits"]["records"].values():
            if isinstance(r_obj, dict) and r_obj.get("pending_unknown_executions"):
                r_obj["pending_unknown_executions"] = []

        return _commit({
            "action": action,
            "reconciled_execution_ids": reconciled_ids,
            "memory_reconciled": mem_reconciled,
            "pending_outbox_count": len(remaining_outbox),
            "pending_memory_outbox_count": len(still_mem_outbox),
        })

    # ------------------------------------------------------------------
    # Action 31: save_activity_checkpoint（36A §11.1 / 36B-4 §9）
    # ------------------------------------------------------------------
    if action == "save_activity_checkpoint":
        idem_k, idem_fp, replay_out = _check_idempotency_or_execution_key(
            mismatch_code="EXECUTION_ID_REUSED"
        )
        if replay_out is not None:
            return replay_out
        pid_raw = payload.get("permit_id") or state["permits"].get("active_permit_id")
        pid = _require_local_id(pid_raw, reason_code="PERMIT_ID_INVALID")
        rec = state["permits"]["records"].get(pid)
        if not isinstance(rec, dict):
            raise AdultIntimacyNotFoundError(f"PERMIT_NOT_FOUND:{pid}")
        if "expected_revision" in payload:
            exp_rev = payload["expected_revision"]
            if (
                isinstance(exp_rev, bool)
                or not isinstance(exp_rev, int)
                or exp_rev != int(rec.get("permit_revision") or 1)
            ):
                raise AdultIntimacyConflictError("REVISION_CONFLICT")
        if str(rec.get("state") or "") not in OPEN_PERMIT_STATES:
            raise AdultIntimacyConflictError("PERMIT_NOT_OPEN")
        raw_ref = payload.get("checkpoint_activity_ref")
        if not (isinstance(raw_ref, str) and raw_ref.strip()):
            raw_ref = payload.get("activity_ref")
        if not (isinstance(raw_ref, str) and raw_ref.strip()):
            raise AdultIntimacyValidationError("ACTIVITY_REF_REQUIRED")
        activity_ref = raw_ref.strip()[:200]

        checkpoint = dict(rec.get("checkpoint") or {})
        checkpoint.update({
            "activity_ref": activity_ref,
            "saved_at": now_iso,
            "saved": True,
            "resumable": False,
        })
        if isinstance(payload.get("step_summary"), str) and payload["step_summary"].strip():
            checkpoint["step_summary"] = payload["step_summary"].strip()[:240]
        if isinstance(payload.get("activity_title"), str) and payload["activity_title"].strip():
            checkpoint["activity_title"] = payload["activity_title"].strip()[:120]
        rec["checkpoint"] = checkpoint
        rec["updated_at"] = now_iso
        # 36B-4 §9：已绑定共在会话时把断点同步过去（fail-open、幂等；水位线未动时是
        # 只读 no-op，绝不重复打扰 owner，也绝不阻断场景）。
        _sync_intimacy_co_presence(state, rec, now_iso, root)

        receipt = {
            "action": action,
            "permit_id": pid,
            "state": rec.get("state"),
            "activity_ref": activity_ref,
            "saved": True,
            "resumable": False,
        }
        if idem_k and idem_fp:
            state["permits"]["idempotency_cache"][idem_k] = {
                "fingerprint": idem_fp,
                "receipt": receipt,
            }
        return _commit(receipt)

    raise AdultIntimacyValidationError(f"ACTION_INVALID:{action}")


def consume_resumable_checkpoint(
    root: Path | str,
    *,
    permit_id: str,
    now: str | datetime | None = None,
    decision_id: Optional[str] = None,
) -> dict[str, Any]:
    """36A §11.1：消费一个可恢复的入场断点（幂等、无副作用重放）。

    ``permit`` 记录上 ``checkpoint`` 带 ``resumable=True`` 与非空 ``activity_ref`` 且未被
    消费时，把它标记为已恢复（``resumable=False`` / ``consumed=True`` / ``resumed_at``），
    原子写盘后返回恢复事实。已被同一或不同 ``decision_id`` 消费、或本来就没有可恢复
    断点时，幂等返回 ``already_consumed=True``，绝不抛错。

    本函数只记录"断点被恢复消费"这一事实；恢复场景本身的执行不在这一步。
    """
    state_path = adult_intimacy_state_path(root)
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "ok": False,
            "already_consumed": False,
            "reason_code": "ADULT_INTIMACY_STATE_UNREADABLE",
            "permit_id": permit_id,
            "llm_calls": 0,
        }
    if not isinstance(raw, Mapping):
        return {
            "ok": False,
            "already_consumed": False,
            "reason_code": "ADULT_INTIMACY_STATE_UNREADABLE",
            "permit_id": permit_id,
            "llm_calls": 0,
        }
    state = dict(raw)
    permits = state.get("permits") if isinstance(state.get("permits"), Mapping) else {}
    records = permits.get("records") if isinstance(permits.get("records"), Mapping) else {}
    rec = records.get(str(permit_id)) if isinstance(records, Mapping) else None
    if not isinstance(rec, dict):
        return {
            "ok": False,
            "already_consumed": False,
            "reason_code": f"PERMIT_NOT_FOUND:{permit_id}",
            "permit_id": permit_id,
            "llm_calls": 0,
        }

    now_iso = _to_iso(_parse_now(now))
    checkpoint = dict(rec.get("checkpoint") or {})
    activity_ref = checkpoint.get("activity_ref")
    resumable = checkpoint.get("resumable") is True
    already_consumed = bool(checkpoint.get("consumed")) or not resumable
    if already_consumed or not (isinstance(activity_ref, str) and activity_ref.strip()):
        return {
            "ok": True,
            "already_consumed": True,
            "permit_id": str(permit_id),
            "activity_ref": activity_ref if isinstance(activity_ref, str) else None,
            "resumed_at": checkpoint.get("resumed_at"),
            "decision_id": decision_id,
            "llm_calls": 0,
        }

    checkpoint["resumable"] = False
    checkpoint["consumed"] = True
    checkpoint["resumed_at"] = now_iso
    checkpoint["resumed_by_decision_id"] = str(decision_id) if decision_id else None
    rec["checkpoint"] = checkpoint
    rec["updated_at"] = now_iso
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_iso
    _atomic_write_json(state_path, state)

    # 共在记录同步：如存在对应会话，只做只读报告（幂等、绝不抛错、不重开场景）。
    co_presence: dict[str, Any] = {"state": "NONE"}
    try:
        owner = _import_life_co_presence()
        if owner is not None:
            co_presence = owner.read_private_intimate_session(
                _resolve_co_presence_root(Path(root))
            )
    except Exception as exc:  # fail-open：共在故障不阻断断点消费
        co_presence = {"state": "ERROR", "error_type": type(exc).__name__}

    return {
        "ok": True,
        "already_consumed": False,
        "permit_id": str(permit_id),
        "activity_ref": activity_ref.strip(),
        "resumed_at": now_iso,
        "decision_id": decision_id,
        "co_presence": co_presence,
        "llm_calls": 0,
    }


__all__ = [
    "ADULT_INTIMACY_FILENAME",
    "ADULT_INTIMACY_SCHEMA",
    "ADULT_LIFE_STAGES",
    "ALL_CATEGORIES",
    "AROUSAL_LEVELS",
    "BODY_PRESET_NAMES",
    "DEFAULT_IDLE_TIMEOUT_SEC",
    "OUTBOUND_FORBIDDEN_CHANNELS",
    "PERMIT_STATES",
    "SUPPORTED_ACTIONS",
    "TIERS",
    "AdultIntimacyConflictError",
    "AdultIntimacyError",
    "AdultIntimacyNotFoundError",
    "AdultIntimacyPermissionError",
    "AdultIntimacyValidationError",
    "adult_intimacy_state_path",
    "apply_adult_intimacy_action",
    "consume_resumable_checkpoint",
    "find_matching_solo_slot",
    "read_adult_intimacy",
    "solo_window_probability",
]
