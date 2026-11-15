#!/usr/bin/env python3
"""开源角色包合同 + 可组合身体/日常配饰 + 多通道环境感官场与文字声景卡。

它是什么
--------
本模块只负责两块边界清晰、纯确定性、零模型调用的后端投影与显式小状态：

1. ``phone.character.pack.v1``（``<root>/phone/character_pack_state.json``）：
   * 开源中性默认预设 ``neutral_resident``（不绑定「千代」、不预设名字与性别；未落盘时
     ``GET`` 零写入直接投影 ``neutral_resident``，``revision=0``）。
   * 严格执行 ``38-角色包合同.md`` 与 ``51-可组合身体与可选生理体验.md``：
     - ``kind == "proposed.character_pack.v1"``；
     - ``fictional is True``（严格布尔真）；
     - 递归禁字段防火墙（``DEPLOY_ONLY | USER_ONLY`` 无论嵌在多深的 dict/list 一律以
       ``PACK_FIELD_NOT_ALLOWED:<path>`` 拒绝，绝不静默丢弃）；
     - 角色包永远打不开成年门禁（``pack_never_opens_gate=True``），``declared_age < 18``、
       ``blood_relative / guardian``、``minor_education`` 直接在 ``adult_gate_preview``
       给出 ``FAIL`` 与具名原因；
     - ``sexual_seed`` 中 ``POWER_EXCHANGE`` 一律拒绝；无论包内写什么 ``seed_status``，有效
       状态永远是 ``DRAFT``（``pack_never_confirms_seed=True``），且 ``partner_default``
       绝不泄露原始 ``sexual_seed`` 或 ``optional_processes``；
     - 生理体验 ``optional_processes`` 永远不会因切换预设、角色包、名字或人称代词而自动开启，
       仅能由本人 capability 显式开关，且仅在 ``view == "her"`` 可见。
2. ``phone.body.sensory_field.v1``（``<root>/phone/sensory_cards.json``）：
   * ``GET`` 100% 纯零写入：只读 ``PhoneWorld.snapshot()``、活跃 ``world_ambient_events``
     （当前地点 + 邻接地点）、``somatic_comfort.json``、``character_pack_state.json`` 与
     ``sensory_cards.json``，派生五路感官通道（``auditory`` / ``visual`` / ``olfactory`` /
     ``tactile_thermal`` / ``sensory_load``）。
   * 文字声景卡（``medium="text_sound_card"``，``has_audio_recording=False``，最多 12 张，
     支持 ``operation_key`` 幂等重放；``partner_default`` 只展示 ``shared=True``，绝不泄露
     未公开张数或总张数）与伴侣感官小贴士（最多 3 条）。

它不是什么
----------
* 不调用任何模型、不起后台定时器、不录音、不把气味变食物库存、不把感官负荷映射成情绪或病理。
* 不修改 ``somatic_comfort.json``、World 状态或任何既有 owner 账本。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from . import somatic_comfort_view as somatic_view

CHARACTER_PACK_SCHEMA = "phone.character.pack.v1"
SENSORY_FIELD_SCHEMA = "phone.body.sensory_field.v1"

PACK_CONTRACT_KIND = "proposed.character_pack.v1"

STATE_DIRNAME = "phone"
CHARACTER_PACK_FILENAME = "character_pack_state.json"
SENSORY_CARDS_FILENAME = "sensory_cards.json"
STATE_SCHEMA_VERSION = 1

DEFAULT_PRESET_ID = "neutral_resident"

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

DEPLOY_ONLY: frozenset[str] = frozenset({
    "attestation",
    "attested_by",
    "adult_confirmed",
    "deploy_cap",
    "deploy_enabled",
    "feature_enabled",
    "solo_enabled",
    "policy_basis",
    "route",
    "routes",
    "seed_confirmed_for",
    "retention",
})

USER_ONLY: frozenset[str] = frozenset({
    "user_genitals",
    "preferred_tier",
    "allow_subject_initiation",
    "solo_talk",
    "limits",
})

FORBIDDEN_PACK_KEYS: frozenset[str] = DEPLOY_ONLY | USER_ONLY

ALLOWED_PACK_KEYS: frozenset[str] = frozenset({
    "kind",
    "pack_id",
    "version",
    "display_name",
    "fictional",
    "pronouns",
    "adult",
    "relation_to_user",
    "address",
    "persona",
    "body",
    "sexual_seed",
    "obligations",
    "family",
    "world_pack_ref",
    "busy_periods",
    "temper",
    "conversation",
    "sensory_preferences",
    "starter_accessories",
})

LIFE_STAGES = ("university_student", "working_adult", "adult_unspecified")
RELATIONS_TO_USER = ("partner", "blood_relative", "guardian")
BODY_PRESETS = ("neutral", "female", "male", "custom")
OPTIONAL_PROCESSES = ("menstruation_lite", "nocturnal_emission_lite")

PERSONA_MAX_CHARS = 2000
FAMILY_ROLES = ("parent", "sibling", "grandparent", "other_relative")
FAMILY_CONTACTS = ("none", "rare", "regular")
FAMILY_CADENCES = (
    "none",
    "rare",
    "regular",
    "daily",
    "weekly",
    "biweekly",
    "monthly",
    "occasional",
)
FAMILY_ITEM_KEYS: frozenset[str] = frozenset({
    "id",
    "role",
    "relation",
    "name",
    "contact",
    "cadence",
    "tone",
    "note",
})
FAMILY_DICT_KEYS: frozenset[str] = FAMILY_ITEM_KEYS | frozenset({"members"})

OBLIGATION_KINDS = ("STUDY", "WORK", "OTHER")
OBLIGATION_MODES = ("FIXED", "OFFERED")
OBLIGATION_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OBLIGATION_FEELINGS = ("LIKES", "NEUTRAL", "TIRESOME")
OBLIGATION_ALLOWED_KEYS: frozenset[str] = frozenset({
    "id",
    "kind",
    "label",
    "title",
    "institution_kind",
    "attends",
    "mode",
    "slots",
    "offers",
    "travel_min",
    "feeling",
    "pay",
})

BUSY_PERIOD_KINDS = ("EXAM", "PEAK", "OTHER")
BUSY_PERIOD_KEYS: frozenset[str] = frozenset({"id", "kind", "from", "to", "label"})

TEMPER_WHEN_HURT = ("SAYS_IT", "GOES_QUIET", "SHARP_JOKE")
TEMPER_SPACE_STYLES = ("SHORT", "UNTIL_SLEEP")
TEMPER_FADE_H_RANGE = (1, 168)
TEMPER_KEYS: frozenset[str] = frozenset({"when_hurt", "space", "friction_fade_h"})

CONVERSATION_WHEN_JUMPED = ("FINISHES_FIRST", "ASKS", "LETS_GO")
CONVERSATION_HOLD_MIN_RANGE = (5, 120)
CONVERSATION_KEYS: frozenset[str] = frozenset({"thread_hold_min", "when_jumped"})

HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
_DAYS_IN_MONTH = {
    1: 31,
    2: 29,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}

SEXUAL_SEED_KEYS: frozenset[str] = frozenset({
    "seed_status",
    "orientation",
    "desire_baseline",
    "initiation_style",
    "stances",
    "soft_preferences",
    "vocabulary_register",
    "solo_inclination",
})
SEXUAL_SEED_STATUSES = ("DRAFT", "CONFIRMED")
SEXUAL_DESIRE_BASELINES: frozenset[str] = frozenset({
    "none",
    "low",
    "moderate",
    "high",
    "variable",
    "NONE",
    "LOW",
    "MODERATE",
    "HIGH",
    "VARIABLE",
})
SEXUAL_INITIATION_STYLES: frozenset[str] = frozenset({
    "approach",
    "hint",
    "direct",
    "never",
    "rarely",
    "sometimes",
    "often",
    "NEVER",
    "RARELY",
    "SOMETIMES",
    "OFTEN",
    "EYE_CONTACT_AND_STILLNESS",
    "CLOSING_DISTANCE",
    "QUIET_TOUCH",
    "SOFT_ASK",
    "DIRECT_INVITE",
})
SEXUAL_VOCABULARY_REGISTERS: frozenset[str] = frozenset({
    "plain",
    "soft",
    "blunt",
    "natural",
    "poetic",
    "shy",
    "frank",
    "reticent",
    "PLAIN",
    "SOFT",
    "BLUNT",
    "RETICENT",
    "FRANK",
})
SEXUAL_SOLO_INCLINATIONS: frozenset[str] = frozenset({
    "none",
    "low",
    "rare",
    "occasional",
    "moderate",
    "high",
    "NONE",
    "LOW",
    "RARE",
    "OCCASIONAL",
    "MODERATE",
    "HIGH",
})
SEXUAL_STANCE_VALUES: frozenset[str] = frozenset({
    "YES",
    "MAYBE",
    "NO",
    "yes",
    "maybe",
    "no",
    "open",
    "curious",
    "enjoys",
    "shy_curious",
    "not_for_me",
    "hard_no",
    "OPEN",
    "CURIOUS",
    "ENJOYS",
    "SHY_CURIOUS",
    "NOT_FOR_ME",
    "HARD_NO",
})
SEXUAL_SOFT_PREFERENCES: frozenset[str] = frozenset({
    "slow_pace",
    "dim_light",
    "curtains_closed",
    "dim_lamp",
    "close_curtains",
    "aftercare_warmth",
    "stay_close_after",
    "quiet_aftercare",
    "SLOW_START",
    "DIM_LIGHT",
    "CURTAINS_CLOSED",
    "STAY_CLOSE_AFTER",
})
SEXUAL_SOFT_PREFERENCE_DICT_KEYS: frozenset[str] = frozenset({
    "dim_lamp",
    "close_curtains",
    "aftercare_warmth",
    "dim_light",
    "curtains_closed",
    "slow_pace",
})
RESERVED_STANCE_FAMILIES: frozenset[str] = frozenset({
    "POWER_EXCHANGE",
    "CNC",
    "NON_CONSENT",
    "COERCION",
    "MINOR",
    "DEGRADATION",
})

NOISE_SENSITIVITIES = ("low", "moderate", "high")
LIGHT_PREFERENCES = ("soft_warm", "balanced", "bright_clear")

ACCESSORY_KINDS = (
    "glasses",
    "scarf",
    "watch",
    "headphones",
    "hat",
    "brooch",
    "hairband",
    "hairclip",
)
ACCESSORY_MODES = ("wear", "ambient_filter", "stowed")
MAX_ACCESSORIES = 2

MAX_SOUND_CARDS = 12
MAX_PARTNER_SENSORY_NOTES = 3
MAX_CARD_TITLE = 60
MAX_IMPRESSION_NOTE = 200
MAX_PARTNER_SENSORY_NOTE_TEXT = 140
MAX_OPERATION_KEY = 128

#: 模板环境事件中纯视觉/告示性质（不穿墙传声）的类型；其余环境活动作为可听声源投影。
VISUAL_ONLY_AMBIENT_TYPES: frozenset[str] = frozenset({
    "home_morning_light",
    "park_sunset_shadows",
    "nearby_street_evening_lights",
    "school_notice",
    "park_free_paper",
    "home_goods_store_display",
    "convenience_store_promotion",
    "street_drainage_notice",
    "park_lawn_rest",
})

LIGHT_BEARING_AMBIENT_TYPES: frozenset[str] = frozenset({
    "home_morning_light",
    "park_sunset_shadows",
    "nearby_street_evening_lights",
})


class CharacterPackValidationError(ValueError):
    """Raised when a character pack or identity/physiology input violates contract rules."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)


# ---------------------------------------------------------------------------------------------
# Built-in open-source starter presets & Ch 38 chiyo_sample
# ---------------------------------------------------------------------------------------------

BUILT_IN_PRESET_SUMMARIES: dict[str, str] = {
    "neutral_resident": "开源默认中性个体（不预设名字与性别，由使用者自由定义）",
    "chiyo": "示例角色包 · 千代（女大学生，称呼你哥哥，偏爱柔和暖光）",
    "haru": "示例角色包 · 晴（男青年，沉稳温和的创作者，常戴细框眼镜）",
    "sola": "示例角色包 · 空（无性别设定的声景与插画记录者，常备降噪耳机）",
}

CHIYO_SAMPLE_PACK: dict[str, Any] = {
    "kind": PACK_CONTRACT_KIND,
    "pack_id": "chiyo",
    "version": 2,
    "display_name": "千代",
    "fictional": True,
    "pronouns": "她",
    "adult": {"life_stage": "university_student", "declared_age": None},
    "relation_to_user": "partner",
    "address": {"subject_calls_user": ["哥哥"], "user_calls_subject": []},
    "persona": {
        "core": "人设正文以 CHIYO-v3.0 第 01–35 篇为准，本包只引用、不复写；外貌文集没有写，留空（36B-1:80）。",
    },
    "body": {"preset": "female"},
    "sexual_seed": {
        "seed_status": "DRAFT",
        "orientation": None,
        "desire_baseline": "moderate",
        "initiation_style": ["approach", "hint", "direct"],
        "stances": {
            "KISSING": "YES",
            "INTERCOURSE": "MAYBE",
            "BATH_TOGETHER": "MAYBE",
            "CARESSING_CLOTHED:subject_to_user": "YES",
            "CARESSING_CLOTHED:user_to_subject": "YES",
            "CARESSING_BARE:subject_to_user": "YES",
            "CARESSING_BARE:user_to_subject": "YES",
            "UNDRESSING:subject_to_user": "YES",
            "UNDRESSING:user_to_subject": "YES",
            "MANUAL:subject_to_user": "YES",
            "MANUAL:user_to_subject": "YES",
            "ORAL:subject_to_user": "MAYBE",
            "ORAL:user_to_subject": "MAYBE",
            "ANAL:subject_to_user": "NO",
            "ANAL:user_to_subject": "NO",
            "TOYS:subject_to_user": "NO",
            "TOYS:user_to_subject": "NO",
        },
        "soft_preferences": ["slow_pace", "dim_light", "curtains_closed"],
        "vocabulary_register": "plain",
        "solo_inclination": "low",
    },
    "obligations": [
        {
            "id": "04:34",
            "kind": "STUDY",
            "institution_kind": "university",
            "attends": True,
        },
        {
            "id": "04:36",
            "kind": "STUDY",
            "institution_kind": "university",
            "attends": True,
        },
        {
            "id": "04:68",
            "kind": "WORK",
            "institution_kind": "neighborhood",
            "mode": "OFFERED",
            "pay": [200, 800],
            "offers": {
                "per_week": [0, 3],
                "minutes": [60, 180],
            },
        },
    ],
    "family": None,
    "world_pack_ref": None,
    "temper": {"when_hurt": ["SHARP_JOKE"], "friction_fade_h": 72},
    "sensory_preferences": {
        "noise_sensitivity": "moderate",
        "light_preference": "soft_warm",
        "scent_note_enabled": True,
    },
    "starter_accessories": [],
}

BUILT_IN_PRESETS: dict[str, dict[str, Any]] = {
    "neutral_resident": {
        "kind": PACK_CONTRACT_KIND,
        "pack_id": "neutral_resident",
        "version": 1,
        "display_name": "TA",
        "fictional": True,
        "pronouns": "TA",
        "adult": {"life_stage": "working_adult", "declared_age": None},
        "relation_to_user": "partner",
        "address": {"subject_calls_user": [], "user_calls_subject": []},
        "persona": {
            "core": "开源默认数字个体，名字、代词、性格与日常偏好均可由部署者自由定义。",
        },
        "body": {"preset": "neutral"},
        "sensory_preferences": {
            "noise_sensitivity": "moderate",
            "light_preference": "balanced",
            "scent_note_enabled": True,
        },
        "starter_accessories": [],
    },
    "chiyo": copy.deepcopy(CHIYO_SAMPLE_PACK),
    "haru": {
        "kind": PACK_CONTRACT_KIND,
        "pack_id": "haru",
        "version": 1,
        "display_name": "晴",
        "fictional": True,
        "pronouns": "他",
        "adult": {"life_stage": "working_adult", "declared_age": None},
        "relation_to_user": "partner",
        "address": {"subject_calls_user": ["你"], "user_calls_subject": []},
        "persona": {
            "core": "示例角色包 · 沉稳温和的青年创作者，喜欢整理木作工具、泡热茶与傍晚散步。",
        },
        "body": {"preset": "male"},
        "sensory_preferences": {
            "noise_sensitivity": "low",
            "light_preference": "balanced",
            "scent_note_enabled": True,
        },
        "starter_accessories": [
            {"kind": "glasses", "label": "细框眼镜", "mode": "wear"},
        ],
    },
    "sola": {
        "kind": PACK_CONTRACT_KIND,
        "pack_id": "sola",
        "version": 1,
        "display_name": "空",
        "fictional": True,
        "pronouns": "TA",
        "adult": {"life_stage": "working_adult", "declared_age": None},
        "relation_to_user": "partner",
        "address": {"subject_calls_user": [], "user_calls_subject": []},
        "persona": {
            "core": "示例角色包 · 无性别设定的独立声景与插画记录者，对声音细节敏感。",
        },
        "body": {"preset": "neutral"},
        "sensory_preferences": {
            "noise_sensitivity": "high",
            "light_preference": "soft_warm",
            "scent_note_enabled": True,
        },
        "starter_accessories": [
            {"kind": "headphones", "label": "轻便头戴耳机", "mode": "ambient_filter"},
        ],
    },
}

PRESET_ORDER = ("neutral_resident", "chiyo", "haru", "sola")
PRESET_ALIASES: dict[str, str] = {"chiyo_sample": "chiyo"}


def chiyo_sample_pack() -> dict[str, Any]:
    """Return a clean deep copy of the Ch 38 Chiyo sample pack."""
    return copy.deepcopy(CHIYO_SAMPLE_PACK)


# ---------------------------------------------------------------------------------------------
# Small deterministic helpers & atomic 0600 JSON persistence
# ---------------------------------------------------------------------------------------------


def _clean_text(value: Any, *, min_len: int, max_len: int, reason_code: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise CharacterPackValidationError(reason_code)
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise CharacterPackValidationError(reason_code)
    return cleaned


def _clean_multiline_text(value: Any, *, min_len: int, max_len: int, reason_code: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise CharacterPackValidationError(reason_code)
    cleaned = value.strip()
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise CharacterPackValidationError(reason_code)
    return cleaned


def _iso_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists() and path.is_symlink():
        raise ValueError("SYMLINK_STATE_REFUSED")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise ValueError("SYMLINK_DIR_REFUSED")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(payload))
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
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


# ---------------------------------------------------------------------------------------------
# Domain A: Character Pack & Composable Body/Accessories validation
# ---------------------------------------------------------------------------------------------


def _scan_forbidden_keys(node: Any, path_prefix: str = "") -> None:
    """Recursively scan all nested dicts/lists for DEPLOY_ONLY | USER_ONLY keys."""

    if isinstance(node, Mapping):
        for raw_key in node.keys():
            key = str(raw_key)
            current_path = f"{path_prefix}.{key}" if path_prefix else key
            if key in FORBIDDEN_PACK_KEYS:
                raise CharacterPackValidationError(f"PACK_FIELD_NOT_ALLOWED:{current_path}")
            _scan_forbidden_keys(node[raw_key], current_path)
    elif isinstance(node, (list, tuple)):
        for idx, item in enumerate(node):
            item_path = f"{path_prefix}[{idx}]" if path_prefix else f"[{idx}]"
            _scan_forbidden_keys(item, item_path)


def _json_safe_copy(
    node: Any, *, field_path: str, depth: int = 0, max_str_len: int = 600
) -> Any:
    if depth > 6:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
    if node is None or isinstance(node, bool) or isinstance(node, int):
        return node
    if isinstance(node, float):
        if node != node or node in (float("inf"), float("-inf")):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
        return node
    if isinstance(node, str):
        if "\x00" in node or len(node) > max_str_len:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
        return node
    if isinstance(node, list):
        if len(node) > 32:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
        return [
            _json_safe_copy(
                item,
                field_path=f"{field_path}[{idx}]",
                depth=depth + 1,
                max_str_len=max_str_len,
            )
            for idx, item in enumerate(node)
        ]
    if isinstance(node, Mapping):
        if len(node) > 32:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
        out: dict[str, Any] = {}
        for raw_k, raw_v in node.items():
            if not isinstance(raw_k, str) or not raw_k.strip() or len(raw_k) > 64:
                raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
            out[raw_k] = _json_safe_copy(
                raw_v,
                field_path=f"{field_path}.{raw_k}",
                depth=depth + 1,
                max_str_len=max_str_len,
            )
        return out
    raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")


def _is_int_pair(val: Any, lo: int, hi: int) -> bool:
    return (
        isinstance(val, list)
        and len(val) == 2
        and all(isinstance(x, int) and not isinstance(x, bool) for x in val)
        and lo <= val[0] <= val[1] <= hi
    )


def _is_valid_mmdd(val: Any) -> bool:
    if not isinstance(val, str) or not MMDD_RE.match(val):
        return False
    month, day = int(val[:2]), int(val[3:5])
    return 1 <= month <= 12 and 1 <= day <= _DAYS_IN_MONTH[month]


def validate_sensory_preferences(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {
            "noise_sensitivity": "moderate",
            "light_preference": "balanced",
            "scent_note_enabled": True,
        }
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:sensory_preferences")
    allowed = {"noise_sensitivity", "light_preference", "scent_note_enabled"}
    extra = sorted(set(raw.keys()) - allowed)
    if extra:
        raise CharacterPackValidationError(
            f"PACK_FIELD_UNKNOWN:sensory_preferences.{extra[0]}"
        )
    noise = raw.get("noise_sensitivity", "moderate")
    if not isinstance(noise, str) or noise not in NOISE_SENSITIVITIES:
        raise CharacterPackValidationError(
            "PACK_FIELD_INVALID:sensory_preferences.noise_sensitivity"
        )
    light = raw.get("light_preference", "balanced")
    if not isinstance(light, str) or light not in LIGHT_PREFERENCES:
        raise CharacterPackValidationError(
            "PACK_FIELD_INVALID:sensory_preferences.light_preference"
        )
    scent = raw.get("scent_note_enabled", True)
    if not isinstance(scent, bool):
        raise CharacterPackValidationError(
            "PACK_FIELD_INVALID:sensory_preferences.scent_note_enabled"
        )
    return {
        "noise_sensitivity": noise,
        "light_preference": light,
        "scent_note_enabled": scent,
    }


def validate_accessories(raw: Any, *, field_name: str = "everyday_accessories") -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ACCESSORIES:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_name}")
    allowed_keys = {"kind", "label", "mode", "note", "private_note"}
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_name}[{idx}]")
        extra = sorted(set(item.keys()) - allowed_keys)
        if extra:
            raise CharacterPackValidationError(
                f"PACK_FIELD_UNKNOWN:{field_name}[{idx}].{extra[0]}"
            )
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in ACCESSORY_KINDS:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_name}[{idx}].kind")
        label = _clean_text(
            item.get("label"),
            min_len=1,
            max_len=60,
            reason_code=f"PACK_FIELD_INVALID:{field_name}[{idx}].label",
        )
        mode = item.get("mode")
        if not isinstance(mode, str) or mode not in ACCESSORY_MODES:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_name}[{idx}].mode")
        if mode == "ambient_filter" and kind != "headphones":
            raise CharacterPackValidationError("ACCESSORY_MODE_INVALID")
        entry: dict[str, Any] = {
            "kind": kind,
            "label": label,
            "mode": mode,
        }
        if "note" in item and item.get("note") is not None:
            entry["note"] = _clean_text(
                item.get("note"),
                min_len=1,
                max_len=120,
                reason_code=f"PACK_FIELD_INVALID:{field_name}[{idx}].note",
            )
        if "private_note" in item and item.get("private_note") is not None:
            entry["private_note"] = _clean_text(
                item.get("private_note"),
                min_len=1,
                max_len=120,
                reason_code=f"PACK_FIELD_INVALID:{field_name}[{idx}].private_note",
            )
        normalized.append(entry)
    return normalized


def _validate_address_list(raw: Any, *, field_path: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 8:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
    items: list[str] = []
    for idx, entry in enumerate(raw):
        cleaned = _clean_text(
            entry,
            min_len=1,
            max_len=24,
            reason_code=f"PACK_FIELD_INVALID:{field_path}[{idx}]",
        )
        if cleaned not in items:
            items.append(cleaned)
    return items


def _validate_sexual_seed(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed")
    extra = sorted(set(raw.keys()) - SEXUAL_SEED_KEYS)
    if extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:sexual_seed.{extra[0]}")

    if "seed_status" in raw and raw.get("seed_status") is not None:
        if raw.get("seed_status") not in SEXUAL_SEED_STATUSES:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.seed_status")

    if "orientation" in raw and raw.get("orientation") is not None:
        _clean_text(
            raw.get("orientation"),
            min_len=1,
            max_len=40,
            reason_code="PACK_FIELD_INVALID:sexual_seed.orientation",
        )

    if "desire_baseline" in raw and raw.get("desire_baseline") is not None:
        db = raw.get("desire_baseline")
        if not isinstance(db, str) or db not in SEXUAL_DESIRE_BASELINES:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.desire_baseline")

    if "initiation_style" in raw and raw.get("initiation_style") is not None:
        init_style = raw.get("initiation_style")
        if isinstance(init_style, str):
            if init_style not in SEXUAL_INITIATION_STYLES:
                raise CharacterPackValidationError(
                    "PACK_FIELD_INVALID:sexual_seed.initiation_style"
                )
        elif isinstance(init_style, list):
            if len(init_style) > 8 or not all(
                isinstance(x, str) and x in SEXUAL_INITIATION_STYLES for x in init_style
            ):
                raise CharacterPackValidationError(
                    "PACK_FIELD_INVALID:sexual_seed.initiation_style"
                )
        else:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.initiation_style")

    if "vocabulary_register" in raw and raw.get("vocabulary_register") is not None:
        vr = raw.get("vocabulary_register")
        if not isinstance(vr, str) or vr not in SEXUAL_VOCABULARY_REGISTERS:
            raise CharacterPackValidationError(
                "PACK_FIELD_INVALID:sexual_seed.vocabulary_register"
            )

    if "solo_inclination" in raw and raw.get("solo_inclination") is not None:
        si = raw.get("solo_inclination")
        if not isinstance(si, str) or si not in SEXUAL_SOLO_INCLINATIONS:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.solo_inclination")

    stances = raw.get("stances")
    if stances is not None:
        if isinstance(stances, Mapping):
            for key, val in stances.items():
                if not isinstance(key, str) or not key.strip() or len(key) > 64:
                    raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.stances")
                key_str = key.strip().upper()
                base_family = key_str.split(":", 1)[0]
                val_str = str(val).strip().upper() if isinstance(val, str) else ""
                if (
                    base_family in RESERVED_STANCE_FAMILIES
                    or key_str.startswith("POWER_EXCHANGE")
                    or val_str.startswith("POWER_EXCHANGE")
                    or not isinstance(val, str)
                    or val not in SEXUAL_STANCE_VALUES
                ):
                    raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.stances")
        elif isinstance(stances, list):
            for item in stances:
                if not isinstance(item, str) or not item.strip() or len(item) > 64:
                    raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.stances")
                item_up = item.strip().upper()
                if (
                    item_up.split(":", 1)[0] in RESERVED_STANCE_FAMILIES
                    or item_up.startswith("POWER_EXCHANGE")
                ):
                    raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.stances")
        else:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.stances")

    if "soft_preferences" in raw and raw.get("soft_preferences") is not None:
        sp = raw.get("soft_preferences")
        if isinstance(sp, list):
            if len(sp) > 16 or not all(
                isinstance(x, str) and x in SEXUAL_SOFT_PREFERENCES for x in sp
            ):
                raise CharacterPackValidationError(
                    "PACK_FIELD_INVALID:sexual_seed.soft_preferences"
                )
        elif isinstance(sp, Mapping):
            sp_extra = sorted(set(sp.keys()) - SEXUAL_SOFT_PREFERENCE_DICT_KEYS)
            if sp_extra:
                raise CharacterPackValidationError(
                    f"PACK_FIELD_UNKNOWN:sexual_seed.soft_preferences.{sp_extra[0]}"
                )
            for sp_val in sp.values():
                if not isinstance(sp_val, (bool, str)) or (
                    isinstance(sp_val, str) and (not sp_val.strip() or len(sp_val) > 40)
                ):
                    raise CharacterPackValidationError(
                        "PACK_FIELD_INVALID:sexual_seed.soft_preferences"
                    )
        else:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:sexual_seed.soft_preferences")

    copied = _json_safe_copy(raw, field_path="sexual_seed")
    assert isinstance(copied, dict)
    # 38-角色包合同 §4：包内无论写什么 seed_status（哪怕写 CONFIRMED），有效状态一律强制 DRAFT。
    copied["seed_status"] = "DRAFT"
    return copied


def _validate_obligation_item(raw: Any, *, idx: int) -> dict[str, Any]:
    path = f"obligations[{idx}]"
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}")
    extra = sorted(set(raw.keys()) - OBLIGATION_ALLOWED_KEYS)
    if extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{path}.{extra[0]}")

    has_anchor = any(
        raw.get(k) is not None for k in ("id", "label", "title", "institution_kind")
    )
    if not has_anchor:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}")

    for str_key in ("id", "label", "title"):
        if str_key in raw and raw.get(str_key) is not None:
            _clean_text(
                raw.get(str_key),
                min_len=1,
                max_len=60,
                reason_code=f"PACK_FIELD_INVALID:{path}.{str_key}",
            )

    if "institution_kind" in raw and raw.get("institution_kind") is not None:
        _clean_text(
            raw.get("institution_kind"),
            min_len=1,
            max_len=40,
            reason_code=f"PACK_FIELD_INVALID:{path}.institution_kind",
        )

    kind = raw.get("kind")
    has_schedule_fields = any(
        k in raw and raw.get(k) is not None
        for k in ("mode", "slots", "offers", "pay", "travel_min", "feeling")
    )
    if kind is not None:
        if not isinstance(kind, str) or kind not in OBLIGATION_KINDS:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.kind")
    elif has_schedule_fields:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.kind")

    if "attends" in raw and not isinstance(raw.get("attends"), bool):
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.attends")

    mode = raw.get("mode")
    if mode is not None and (not isinstance(mode, str) or mode not in OBLIGATION_MODES):
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.mode")

    if "feeling" in raw and raw.get("feeling") is not None:
        if not isinstance(raw.get("feeling"), str) or raw.get("feeling") not in OBLIGATION_FEELINGS:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.feeling")

    if "travel_min" in raw and raw.get("travel_min") is not None:
        tm = raw.get("travel_min")
        if isinstance(tm, bool) or not isinstance(tm, int) or tm < 0 or tm > 180:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.travel_min")

    if "pay" in raw and raw.get("pay") is not None:
        if kind != "WORK" or not _is_int_pair(raw.get("pay"), 1, 10**7):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.pay")

    if "slots" in raw and raw.get("slots") is not None and mode != "FIXED":
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.slots")
    if "offers" in raw and raw.get("offers") is not None and mode != "OFFERED":
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.offers")

    if mode == "FIXED":
        slots = raw.get("slots")
        if not isinstance(slots, list) or not slots or len(slots) > 14:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.slots")
        for s_idx, slot in enumerate(slots):
            s_path = f"{path}.slots[{s_idx}]"
            if not isinstance(slot, Mapping):
                raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{s_path}")
            s_extra = sorted(set(slot.keys()) - {"days", "start", "end"})
            if s_extra:
                raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{s_path}.{s_extra[0]}")
            days = slot.get("days")
            if (
                not isinstance(days, list)
                or not days
                or not all(isinstance(d, str) and d in OBLIGATION_WEEKDAYS for d in days)
                or len(set(days)) != len(days)
            ):
                raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{s_path}.days")
            start = slot.get("start")
            end = slot.get("end")
            if (
                not isinstance(start, str)
                or not HHMM_RE.match(start)
                or not isinstance(end, str)
                or not HHMM_RE.match(end)
                or start == end
            ):
                raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{s_path}")

    if mode == "OFFERED":
        offers = raw.get("offers")
        o_path = f"{path}.offers"
        if not isinstance(offers, Mapping):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{o_path}")
        o_extra = sorted(set(offers.keys()) - {"per_week", "minutes", "lead_days"})
        if o_extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{o_path}.{o_extra[0]}")
        lead_days = offers.get("lead_days", [1, 7])
        if (
            not _is_int_pair(offers.get("per_week"), 0, 14)
            or not _is_int_pair(offers.get("minutes"), 15, 720)
            or not _is_int_pair(lead_days, 0, 30)
        ):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{o_path}")

    return _json_safe_copy(raw, field_path=path)


def _validate_busy_periods(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 16:
        raise CharacterPackValidationError("PACK_FIELD_INVALID:busy_periods")
    out: list[dict[str, Any]] = []
    for idx, bp in enumerate(raw):
        path = f"busy_periods[{idx}]"
        if not isinstance(bp, Mapping):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}")
        extra = sorted(set(bp.keys()) - BUSY_PERIOD_KEYS)
        if extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{path}.{extra[0]}")
        _clean_text(
            bp.get("id"),
            min_len=1,
            max_len=40,
            reason_code=f"PACK_FIELD_INVALID:{path}.id",
        )
        if bp.get("kind") not in BUSY_PERIOD_KINDS:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}.kind")
        if not _is_valid_mmdd(bp.get("from")) or not _is_valid_mmdd(bp.get("to")):
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{path}")
        if "label" in bp and bp.get("label") is not None:
            _clean_text(
                bp.get("label"),
                min_len=1,
                max_len=60,
                reason_code=f"PACK_FIELD_INVALID:{path}.label",
            )
        out.append(_json_safe_copy(bp, field_path=path))
    return out


def _validate_family_member(raw: Any, *, field_path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
    extra = sorted(set(raw.keys()) - FAMILY_ITEM_KEYS)
    if extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{field_path}.{extra[0]}")
    has_identity = any(
        isinstance(raw.get(k), str) and raw.get(k).strip()
        for k in ("id", "role", "relation")
    )
    if not has_identity:
        raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}")
    if "role" in raw and raw.get("role") is not None:
        if not isinstance(raw.get("role"), str) or raw.get("role") not in FAMILY_ROLES:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}.role")
    if "contact" in raw and raw.get("contact") is not None:
        if not isinstance(raw.get("contact"), str) or raw.get("contact") not in FAMILY_CONTACTS:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}.contact")
    if "cadence" in raw and raw.get("cadence") is not None:
        if not isinstance(raw.get("cadence"), str) or raw.get("cadence") not in FAMILY_CADENCES:
            raise CharacterPackValidationError(f"PACK_FIELD_INVALID:{field_path}.cadence")
    for str_key in ("id", "relation", "name", "tone", "note"):
        if str_key in raw and raw.get(str_key) is not None:
            _clean_text(
                raw.get(str_key),
                min_len=1,
                max_len=80,
                reason_code=f"PACK_FIELD_INVALID:{field_path}.{str_key}",
            )
    return _json_safe_copy(raw, field_path=field_path)


def _validate_family(raw: Any) -> Any:
    """Validate ``family`` per Ch 38 §6: allows ``list``, ``dict``, or ``None``."""
    if raw is None:
        return None
    if isinstance(raw, list):
        if len(raw) > 16:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:family")
        return [
            _validate_family_member(item, field_path=f"family[{idx}]")
            for idx, item in enumerate(raw)
        ]
    if isinstance(raw, Mapping):
        extra = sorted(set(raw.keys()) - FAMILY_DICT_KEYS)
        if extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:family.{extra[0]}")
        out = _json_safe_copy(raw, field_path="family")
        assert isinstance(out, dict)
        if "members" in raw and raw.get("members") is not None:
            members = raw.get("members")
            if not isinstance(members, list) or len(members) > 16:
                raise CharacterPackValidationError("PACK_FIELD_INVALID:family.members")
            out["members"] = [
                _validate_family_member(m, field_path=f"family.members[{idx}]")
                for idx, m in enumerate(members)
            ]
        if "role" in raw and raw.get("role") is not None:
            if not isinstance(raw.get("role"), str) or raw.get("role") not in FAMILY_ROLES:
                raise CharacterPackValidationError("PACK_FIELD_INVALID:family.role")
        if "contact" in raw and raw.get("contact") is not None:
            if not isinstance(raw.get("contact"), str) or raw.get("contact") not in FAMILY_CONTACTS:
                raise CharacterPackValidationError("PACK_FIELD_INVALID:family.contact")
        if "cadence" in raw and raw.get("cadence") is not None:
            if not isinstance(raw.get("cadence"), str) or raw.get("cadence") not in FAMILY_CADENCES:
                raise CharacterPackValidationError("PACK_FIELD_INVALID:family.cadence")
        for str_key in ("id", "relation", "name", "tone", "note"):
            if str_key in raw and raw.get(str_key) is not None:
                _clean_text(
                    raw.get(str_key),
                    min_len=1,
                    max_len=80,
                    reason_code=f"PACK_FIELD_INVALID:family.{str_key}",
                )
        return out
    raise CharacterPackValidationError("PACK_FIELD_INVALID:family")


def _validate_temper(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:temper")
    extra = sorted(set(raw.keys()) - TEMPER_KEYS)
    if extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:temper.{extra[0]}")
    if "when_hurt" in raw and raw.get("when_hurt") is not None:
        hurt = raw.get("when_hurt")
        if (
            not isinstance(hurt, list)
            or not all(isinstance(x, str) and x in TEMPER_WHEN_HURT for x in hurt)
            or len(set(hurt)) != len(hurt)
        ):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:temper.when_hurt")
    if "space" in raw and raw.get("space") is not None:
        if not isinstance(raw.get("space"), str) or raw.get("space") not in TEMPER_SPACE_STYLES:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:temper.space")
    if "friction_fade_h" in raw and raw.get("friction_fade_h") is not None:
        fade = raw.get("friction_fade_h")
        if (
            isinstance(fade, bool)
            or not isinstance(fade, int)
            or not (TEMPER_FADE_H_RANGE[0] <= fade <= TEMPER_FADE_H_RANGE[1])
        ):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:temper.friction_fade_h")
    return _json_safe_copy(raw, field_path="temper")


def _validate_conversation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:conversation")
    extra = sorted(set(raw.keys()) - CONVERSATION_KEYS)
    if extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:conversation.{extra[0]}")
    if "when_jumped" in raw and raw.get("when_jumped") is not None:
        jumped = raw.get("when_jumped")
        if (
            not isinstance(jumped, list)
            or not all(isinstance(x, str) and x in CONVERSATION_WHEN_JUMPED for x in jumped)
            or len(set(jumped)) != len(jumped)
        ):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:conversation.when_jumped")
    if "thread_hold_min" in raw and raw.get("thread_hold_min") is not None:
        hold = raw.get("thread_hold_min")
        if (
            isinstance(hold, bool)
            or not isinstance(hold, int)
            or not (CONVERSATION_HOLD_MIN_RANGE[0] <= hold <= CONVERSATION_HOLD_MIN_RANGE[1])
        ):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:conversation.thread_hold_min")
    return _json_safe_copy(raw, field_path="conversation")


def validate_optional_processes(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > len(OPTIONAL_PROCESSES):
        raise CharacterPackValidationError("OPTIONAL_PROCESS_INVALID")
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in OPTIONAL_PROCESSES:
            raise CharacterPackValidationError("OPTIONAL_PROCESS_INVALID")
        if item not in result:
            result.append(item)
    return result


def validate_and_normalize_pack(raw: Any) -> dict[str, Any]:
    """Validate a ``proposed.character_pack.v1`` document and return its canonical form."""

    if not isinstance(raw, Mapping):
        raise CharacterPackValidationError("PACK_NOT_OBJECT")

    # 1. Recursive forbidden key firewall FIRST so smuggled deploy/user keys never hide.
    _scan_forbidden_keys(raw)

    # 2. Contract kind check.
    if raw.get("kind") != PACK_CONTRACT_KIND:
        raise CharacterPackValidationError("PACK_KIND_UNKNOWN")

    # 3. Top-level unknown field check.
    unknown_top = sorted(set(raw.keys()) - ALLOWED_PACK_KEYS)
    if unknown_top:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:{unknown_top[0]}")

    # 4. Strict fictional check.
    fictional = raw.get("fictional")
    if not (isinstance(fictional, bool) and fictional is True):
        raise CharacterPackValidationError("PACK_NOT_FICTIONAL")

    # 5. Core identity fields.
    pack_id = raw.get("pack_id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:pack_id")

    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CharacterPackValidationError("PACK_FIELD_INVALID:version")

    display_name = _clean_text(
        raw.get("display_name"),
        min_len=1,
        max_len=40,
        reason_code="PACK_FIELD_INVALID:display_name",
    )

    raw_pronouns = raw.get("pronouns")
    if raw_pronouns is None:
        pronouns = "TA"
    else:
        pronouns = _clean_text(
            raw_pronouns,
            min_len=1,
            max_len=16,
            reason_code="PACK_FIELD_INVALID:pronouns",
        )

    # 6. Adult block.
    adult_raw = raw.get("adult")
    if not isinstance(adult_raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:adult")
    adult_extra = sorted(set(adult_raw.keys()) - {"life_stage", "declared_age"})
    if adult_extra:
        raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:adult.{adult_extra[0]}")
    life_stage = adult_raw.get("life_stage")
    if not isinstance(life_stage, str) or life_stage not in LIFE_STAGES:
        raise CharacterPackValidationError("PACK_FIELD_INVALID:adult.life_stage")
    declared_age = adult_raw.get("declared_age")
    if declared_age is not None:
        if (
            isinstance(declared_age, bool)
            or not isinstance(declared_age, int)
            or declared_age < 0
            or declared_age > 150
        ):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:adult.declared_age")

    # 7. Relation to user & address.
    relation = raw.get("relation_to_user")
    if not isinstance(relation, str) or relation not in RELATIONS_TO_USER:
        raise CharacterPackValidationError("PACK_FIELD_INVALID:relation_to_user")

    address_raw = raw.get("address")
    if address_raw is None:
        address = {"subject_calls_user": [], "user_calls_subject": []}
    else:
        if not isinstance(address_raw, Mapping):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:address")
        addr_extra = sorted(
            set(address_raw.keys()) - {"subject_calls_user", "user_calls_subject"}
        )
        if addr_extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:address.{addr_extra[0]}")
        address = {
            "subject_calls_user": _validate_address_list(
                address_raw.get("subject_calls_user"),
                field_path="address.subject_calls_user",
            ),
            "user_calls_subject": _validate_address_list(
                address_raw.get("user_calls_subject"),
                field_path="address.user_calls_subject",
            ),
        }

    # 8. Persona (closed keys appearance/behavior/core, max 2000 chars) & composable body.
    persona_raw = raw.get("persona")
    if persona_raw is None:
        persona: dict[str, Any] = {"core": ""}
    elif not isinstance(persona_raw, Mapping):
        raise CharacterPackValidationError("PACK_FIELD_INVALID:persona")
    else:
        persona_extra = sorted(set(persona_raw.keys()) - {"appearance", "behavior", "core"})
        if persona_extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:persona.{persona_extra[0]}")
        persona = _json_safe_copy(
            persona_raw, field_path="persona", max_str_len=PERSONA_MAX_CHARS
        )
        for p_key in ("appearance", "behavior", "core"):
            if p_key in persona and persona[p_key] is not None:
                if p_key == "core" and persona[p_key] == "" and not any(
                    persona_raw.get(k) for k in ("appearance", "behavior")
                ):
                    # Preserve empty default only if explicitly empty-defaulted internally;
                    # raw user packs with empty core are rejected below.
                    pass
                persona[p_key] = _clean_multiline_text(
                    persona[p_key],
                    min_len=1,
                    max_len=PERSONA_MAX_CHARS,
                    reason_code=f"PACK_FIELD_INVALID:persona.{p_key}",
                )
        if "core" not in persona or persona["core"] is None:
            persona["core"] = ""

    body_raw = raw.get("body")
    if body_raw is None:
        body: dict[str, Any] = {"preset": "neutral"}
    else:
        if not isinstance(body_raw, Mapping):
            raise CharacterPackValidationError("PACK_FIELD_INVALID:body")
        if "optional_processes" in body_raw:
            raise CharacterPackValidationError("PACK_FIELD_NOT_ALLOWED:body.optional_processes")
        body_extra = sorted(set(body_raw.keys()) - {"preset", "overrides"})
        if body_extra:
            raise CharacterPackValidationError(f"PACK_FIELD_UNKNOWN:body.{body_extra[0]}")
        preset = body_raw.get("preset", "neutral")
        if not isinstance(preset, str) or preset not in BODY_PRESETS:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:body.preset")
        body = {"preset": preset}
        if "overrides" in body_raw and body_raw.get("overrides") is not None:
            overrides = body_raw.get("overrides")
            if not isinstance(overrides, Mapping):
                raise CharacterPackValidationError("PACK_FIELD_INVALID:body.overrides")
            if "optional_processes" in overrides:
                raise CharacterPackValidationError(
                    "PACK_FIELD_NOT_ALLOWED:body.overrides.optional_processes"
                )
            body["overrides"] = _json_safe_copy(overrides, field_path="body.overrides")

    # 9. Sexual seed, obligations & closed-set descriptive blocks.
    sexual_seed = _validate_sexual_seed(raw.get("sexual_seed"))

    obligations_raw = raw.get("obligations")
    obligations: Optional[list[dict[str, Any]]] = None
    if obligations_raw is not None:
        if not isinstance(obligations_raw, list) or len(obligations_raw) > 16:
            raise CharacterPackValidationError("PACK_FIELD_INVALID:obligations")
        obligations = [
            _validate_obligation_item(item, idx=idx)
            for idx, item in enumerate(obligations_raw)
        ]

    sensory_preferences = validate_sensory_preferences(raw.get("sensory_preferences"))
    starter_accessories = validate_accessories(
        raw.get("starter_accessories"), field_name="starter_accessories"
    )

    normalized: dict[str, Any] = {
        "kind": PACK_CONTRACT_KIND,
        "pack_id": pack_id,
        "version": int(version),
        "display_name": display_name,
        "fictional": True,
        "pronouns": pronouns,
        "adult": {
            "life_stage": life_stage,
            "declared_age": declared_age,
        },
        "relation_to_user": relation,
        "address": address,
        "persona": persona,
        "body": body,
        "sensory_preferences": sensory_preferences,
        "starter_accessories": starter_accessories,
    }

    if sexual_seed is not None:
        normalized["sexual_seed"] = sexual_seed
    if obligations is not None:
        normalized["obligations"] = obligations

    if "family" in raw:
        validated_family = _validate_family(raw.get("family"))
        if validated_family is not None or raw.get("family") is None:
            normalized["family"] = validated_family

    if raw.get("temper") is not None:
        normalized["temper"] = _validate_temper(raw.get("temper"))

    if raw.get("conversation") is not None:
        normalized["conversation"] = _validate_conversation(raw.get("conversation"))

    if raw.get("busy_periods") is not None:
        normalized["busy_periods"] = _validate_busy_periods(raw.get("busy_periods"))

    if "world_pack_ref" in raw:
        wpr = raw.get("world_pack_ref")
        if wpr is None:
            normalized["world_pack_ref"] = None
        else:
            if not isinstance(wpr, (str, Mapping, list)):
                raise CharacterPackValidationError("PACK_FIELD_INVALID:world_pack_ref")
            if isinstance(wpr, str):
                wpr = _clean_text(
                    wpr,
                    min_len=1,
                    max_len=80,
                    reason_code="PACK_FIELD_INVALID:world_pack_ref",
                )
                normalized["world_pack_ref"] = wpr
            else:
                normalized["world_pack_ref"] = _json_safe_copy(
                    wpr, field_path="world_pack_ref"
                )

    return normalized


def compute_pack_sha256(normalized_pack: Mapping[str, Any]) -> str:
    canonical_json = json.dumps(
        normalized_pack,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_pack_ref(normalized_pack: Mapping[str, Any]) -> list[Any]:
    digest = compute_pack_sha256(normalized_pack)[:16]
    return [
        str(normalized_pack["pack_id"]),
        int(normalized_pack["version"]),
        digest,
    ]


def evaluate_adult_gate_preview(normalized_pack: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    adult = normalized_pack.get("adult") or {}
    life_stage = adult.get("life_stage")
    declared_age = adult.get("declared_age")
    if isinstance(declared_age, int) and not isinstance(declared_age, bool) and declared_age < 18:
        reasons.append("DECLARED_MINOR")

    relation = normalized_pack.get("relation_to_user")
    if relation in ("blood_relative", "guardian"):
        reasons.append("FORBIDDEN_RELATION")

    obligations = normalized_pack.get("obligations") or []
    if any(
        isinstance(item, Mapping) and item.get("institution_kind") == "minor_education"
        for item in obligations
    ):
        reasons.append("MINOR_INSTITUTION")

    # 39 §十一 SC17 勘误：attends=True 仅对 kind == "STUDY" 要求 university_student 解释；WORK 不误报。
    if any(
        isinstance(item, Mapping)
        and item.get("attends") is True
        and item.get("kind") == "STUDY"
        and life_stage != "university_student"
        for item in obligations
    ):
        reasons.append("STAGE_UNEXPLAINED")

    if reasons:
        return {
            "verdict": "FAIL",
            "reasons": reasons,
            "pack_never_opens_gate": True,
        }
    return {
        "verdict": "REQUIRES_DEPLOY_ATTESTATION",
        "reasons": ["ATTESTATION_NOT_IN_PACK"],
        "pack_never_opens_gate": True,
    }


def build_exportable_pack(
    state_or_pack: Mapping[str, Any],
    *,
    include_sexual_seed: bool = True,
) -> dict[str, Any]:
    """Build a clean, shareable ``proposed.character_pack.v1`` document free of deploy/user state."""
    if "pack_doc" in state_or_pack and isinstance(state_or_pack.get("pack_doc"), Mapping):
        base_pack = copy.deepcopy(dict(state_or_pack["pack_doc"]))
        if "everyday_accessories" in state_or_pack and isinstance(
            state_or_pack.get("everyday_accessories"), list
        ):
            base_pack["starter_accessories"] = _public_accessories(
                validate_accessories(
                    state_or_pack.get("everyday_accessories"),
                    field_name="everyday_accessories",
                ),
                subject_view=False,
            )
    else:
        base_pack = copy.deepcopy(dict(state_or_pack))

    # Strip any non-pack or private accessory fields before canonical validation.
    clean_input: dict[str, Any] = {
        k: v for k, v in base_pack.items() if k in ALLOWED_PACK_KEYS and k not in FORBIDDEN_PACK_KEYS
    }
    if "starter_accessories" in clean_input and isinstance(
        clean_input.get("starter_accessories"), list
    ):
        clean_input["starter_accessories"] = _public_accessories(
            validate_accessories(
                clean_input.get("starter_accessories"), field_name="starter_accessories"
            ),
            subject_view=False,
        )
    if not include_sexual_seed:
        clean_input.pop("sexual_seed", None)

    normalized = validate_and_normalize_pack(clean_input)
    if "sexual_seed" in normalized and isinstance(normalized["sexual_seed"], dict):
        normalized["sexual_seed"]["seed_status"] = "DRAFT"
    return normalized


def available_presets_list() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for preset_id in PRESET_ORDER:
        normalized = validate_and_normalize_pack(BUILT_IN_PRESETS[preset_id])
        rows.append({
            "preset_id": preset_id,
            "pack_id": normalized["pack_id"],
            "version": normalized["version"],
            "pack_ref": compute_pack_ref(normalized),
            "display_name": normalized["display_name"],
            "pronouns": normalized["pronouns"],
            "body_preset": (normalized.get("body") or {}).get("preset", "neutral"),
            "summary": BUILT_IN_PRESET_SUMMARIES[preset_id],
            "is_default": preset_id == DEFAULT_PRESET_ID,
        })
    return rows


# ---------------------------------------------------------------------------------------------
# Character pack state persistence (<root>/phone/character_pack_state.json)
# ---------------------------------------------------------------------------------------------


def character_pack_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / CHARACTER_PACK_FILENAME


def default_character_pack_state() -> dict[str, Any]:
    default_doc = validate_and_normalize_pack(BUILT_IN_PRESETS[DEFAULT_PRESET_ID])
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "source": "default_neutral_preset",
        "preset_id": DEFAULT_PRESET_ID,
        "pack_doc": default_doc,
        "everyday_accessories": copy.deepcopy(default_doc.get("starter_accessories") or []),
        "optional_processes": [],
        "attestation": None,
    }


def _validate_stored_attestation(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid attestation")
    pack_sha256 = raw.get("pack_sha256")
    attested_by = raw.get("attested_by")
    attested_at = _iso_or_none(raw.get("attested_at"))
    if (
        not isinstance(pack_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", pack_sha256)
        or attested_by != "deployer"
        or attested_at is None
    ):
        raise ValueError("invalid attestation fields")
    out: dict[str, Any] = {
        "pack_sha256": pack_sha256,
        "attested_by": "deployer",
        "attested_at": attested_at,
    }
    if isinstance(raw.get("pack_ref"), list) and len(raw.get("pack_ref")) == 3:
        out["pack_ref"] = copy.deepcopy(raw["pack_ref"])
    return out


def _validate_stored_character_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    pack_doc = validate_and_normalize_pack(raw.get("pack_doc"))
    accessories = validate_accessories(
        raw.get("everyday_accessories", pack_doc.get("starter_accessories")),
        field_name="everyday_accessories",
    )
    optional_processes = validate_optional_processes(raw.get("optional_processes", []))
    attestation = _validate_stored_attestation(raw.get("attestation"))
    preset_id = raw.get("preset_id")
    if preset_id is not None and (not isinstance(preset_id, str) or not preset_id.strip()):
        preset_id = None
    source = str(raw.get("source") or "custom_pack").strip()[:40]
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_or_none(raw.get("updated_at")),
        "source": source,
        "preset_id": preset_id,
        "pack_doc": pack_doc,
        "everyday_accessories": accessories,
        "optional_processes": optional_processes,
        "attestation": attestation,
    }


def read_character_pack_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Pure zero-write read. Absent file returns default ``neutral_resident`` with revision 0."""

    path = character_pack_state_path(root)
    if path.is_symlink():
        return default_character_pack_state(), "CHARACTER_PACK_STATE_CORRUPT"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_character_pack_state(), None
    except OSError:
        return default_character_pack_state(), "CHARACTER_PACK_STATE_UNREADABLE"
    try:
        parsed = json.loads(raw_text)
        return _validate_stored_character_state(parsed), None
    except Exception:
        return default_character_pack_state(), "CHARACTER_PACK_STATE_CORRUPT"


def write_character_pack_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validate_stored_character_state(state)
    return _atomic_write_json(character_pack_state_path(root), validated)


def _public_accessories(
    accessories: list[dict[str, Any]], *, subject_view: bool
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in accessories:
        row: dict[str, Any] = {
            "kind": item["kind"],
            "label": item["label"],
            "mode": item["mode"],
        }
        if "note" in item and item["note"] is not None:
            row["note"] = item["note"]
        if subject_view and "private_note" in item and item["private_note"] is not None:
            row["private_note"] = item["private_note"]
        rows.append(row)
    return rows


def project_character_pack(
    state: Mapping[str, Any],
    *,
    state_reason: Optional[str] = None,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Build the ``GET /api/phone/character/pack`` response payload (pure, zero-write)."""

    pack_doc = validate_and_normalize_pack(state["pack_doc"])
    pack_ref = compute_pack_ref(pack_doc)
    pack_sha256 = compute_pack_sha256(pack_doc)
    adult_gate = evaluate_adult_gate_preview(pack_doc)
    accessories = _public_accessories(
        list(state.get("everyday_accessories") or []), subject_view=bool(subject_view)
    )

    body_doc = pack_doc.get("body") or {"preset": "neutral"}
    body_summary: dict[str, Any] = {
        "preset": body_doc.get("preset", "neutral"),
    }
    if "overrides" in body_doc:
        body_summary["overrides"] = copy.deepcopy(body_doc["overrides"])
    if subject_view:
        body_summary["optional_processes"] = list(state.get("optional_processes") or [])

    persona_doc = copy.deepcopy(pack_doc.get("persona") or {"core": ""})
    seed_present = pack_doc.get("sexual_seed") is not None
    seed_summary = {
        "present": bool(seed_present),
        "effective_seed_status": "DRAFT",
        "pack_never_confirms_seed": True,
    }

    active_pack: dict[str, Any] = {
        "pack_id": pack_doc["pack_id"],
        "version": pack_doc["version"],
        "pack_ref": pack_ref,
        "pack_sha256": pack_sha256,
        "display_name": pack_doc["display_name"],
        "pronouns": pack_doc["pronouns"],
        "fictional": True,
        "adult": copy.deepcopy(pack_doc["adult"]),
        "relation_to_user": pack_doc["relation_to_user"],
        "address": copy.deepcopy(pack_doc["address"]),
        "persona_summary": persona_doc,
        "body_summary": body_summary,
        "sensory_preferences": copy.deepcopy(pack_doc["sensory_preferences"]),
        "everyday_accessories": accessories,
        "obligations": copy.deepcopy(pack_doc.get("obligations")),
        "family": copy.deepcopy(pack_doc.get("family")),
        "busy_periods": copy.deepcopy(pack_doc.get("busy_periods")),
        "world_pack_ref": copy.deepcopy(pack_doc.get("world_pack_ref")),
        "temper": copy.deepcopy(pack_doc.get("temper")),
        "conversation": copy.deepcopy(pack_doc.get("conversation")),
        "seed_summary": seed_summary,
    }

    raw_att = state.get("attestation")
    attestation_proj: Optional[dict[str, Any]] = None
    if isinstance(raw_att, Mapping) and raw_att.get("attested_by") == "deployer":
        attestation_proj = {
            "pack_sha256": raw_att.get("pack_sha256"),
            "attested_by": "deployer",
            "attested_at": raw_att.get("attested_at"),
            "matches_active_pack": raw_att.get("pack_sha256") == pack_sha256,
        }

    exportable_pack = build_exportable_pack(state, include_sexual_seed=bool(subject_view))

    return {
        "schema": CHARACTER_PACK_SCHEMA,
        "read_only": True,
        "view": "her" if subject_view else "partner_default",
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "source": str(state.get("source") or "default_neutral_preset"),
        "preset_id": state.get("preset_id"),
        "state_status": "available" if state_reason is None else "degraded",
        "state_reason_code": state_reason,
        "active_pack": active_pack,
        "exportable_pack": exportable_pack,
        "attestation": attestation_proj,
        "adult_gate_preview": adult_gate,
        "available_presets": available_presets_list(),
        "boundaries": {
            "locked_to_chiyo": False,
            "open_source_neutral_default": True,
            "default_preset_id": DEFAULT_PRESET_ID,
            "pack_never_opens_adult_gate": True,
            "pack_never_confirms_seed": True,
            "pronouns_never_imply_physiology": True,
            "optional_physiology_requires_owner_capability": True,
            "raw_seed_withheld_in_partner_view": True,
        },
        "private_text_included": bool(subject_view),
        "local_only": True,
    }


def _seed_autonomy_rhythm_from_pack(
    root_path: Path,
    pack_doc: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    from . import autonomy_rhythm_view as arv

    ar_state, ar_reason = arv.read_autonomy_rhythm_state(root_path)
    if ar_reason is not None:
        return {"seeded": False, "reason_code": ar_reason}

    conv = pack_doc.get("conversation") if isinstance(pack_doc.get("conversation"), Mapping) else {}
    prefs = dict(ar_state.get("preferences") or arv.default_preferences())
    raw_hold = conv.get("thread_hold_min")
    if (
        isinstance(raw_hold, int)
        and not isinstance(raw_hold, bool)
        and arv.MIN_THREAD_HOLD_MIN <= raw_hold <= arv.MAX_THREAD_HOLD_MIN
    ):
        prefs["thread_hold_min"] = int(raw_hold)
    else:
        prefs["thread_hold_min"] = arv.DEFAULT_THREAD_HOLD_MIN

    ar_state["preferences"] = prefs
    ar_state["revision"] = int(ar_state.get("revision") or 0) + 1
    ar_state["updated_at"] = now_iso
    written = arv.write_autonomy_rhythm_state(root_path, ar_state)
    return {
        "seeded": True,
        "revision": int(written["revision"]),
        "thread_hold_min": int(written["preferences"]["thread_hold_min"]),
        "when_jumped": list(conv.get("when_jumped") or []),
    }


def _map_pack_sexual_seed_to_draft(
    raw_seed: Any,
    *,
    existing_seed: Mapping[str, Any],
    default_stances: Mapping[str, str],
    all_categories: Any,
    directed_bases: Any,
) -> dict[str, Any]:
    seed_out = copy.deepcopy(dict(existing_seed))
    # Iron Law: character pack seeding is ALWAYS DRAFT and NEVER confirms seed.
    seed_out["seed_status"] = "DRAFT"
    seed_out["confirmed_for"] = None
    seed_out["confirmed_at"] = None

    if not isinstance(raw_seed, Mapping):
        seed_out["desire_baseline"] = "moderate"
        seed_out["initiation_style"] = "sometimes"
        seed_out["vocabulary_register"] = "natural"
        seed_out["solo_inclination"] = "none"
        seed_out["stances"] = copy.deepcopy(dict(default_stances))
        return seed_out

    raw_base = str(raw_seed.get("desire_baseline") or "moderate").strip().lower()
    if raw_base in ("none", "low", "moderate", "high"):
        seed_out["desire_baseline"] = raw_base
    else:
        seed_out["desire_baseline"] = "moderate"

    raw_init = raw_seed.get("initiation_style")
    init_tokens: list[str] = []
    if isinstance(raw_init, str):
        init_tokens = [raw_init.strip().lower()]
    elif isinstance(raw_init, list):
        init_tokens = [str(x).strip().lower() for x in raw_init if isinstance(x, str)]
    if "never" in init_tokens:
        seed_out["initiation_style"] = "never"
    elif any(t in ("comfortable", "often") for t in init_tokens) or init_tokens == ["direct"]:
        seed_out["initiation_style"] = "comfortable"
    elif any(t in ("sometimes", "approach", "closing_distance", "soft_ask", "direct", "direct_invite") for t in init_tokens):
        seed_out["initiation_style"] = "sometimes"
    elif any(t in ("rare_subtle", "rarely", "hint", "eye_contact_and_stillness", "quiet_touch") for t in init_tokens):
        seed_out["initiation_style"] = "rare_subtle"
    else:
        seed_out["initiation_style"] = "sometimes"

    raw_vocab = str(raw_seed.get("vocabulary_register") or "natural").strip().lower()
    if raw_vocab in ("reticent", "shy", "soft", "poetic"):
        seed_out["vocabulary_register"] = "reticent"
    elif raw_vocab in ("candid", "blunt", "frank"):
        seed_out["vocabulary_register"] = "candid"
    else:
        seed_out["vocabulary_register"] = "natural"

    raw_solo = str(raw_seed.get("solo_inclination") or "none").strip().lower()
    if raw_solo == "none":
        seed_out["solo_inclination"] = "none"
    elif raw_solo in ("low", "rare"):
        seed_out["solo_inclination"] = "low"
    elif raw_solo in ("moderate", "occasional"):
        seed_out["solo_inclination"] = "moderate"
    elif raw_solo == "high":
        seed_out["solo_inclination"] = "high"
    else:
        seed_out["solo_inclination"] = "none"

    stances = copy.deepcopy(dict(default_stances))
    raw_stances = raw_seed.get("stances")
    if isinstance(raw_stances, Mapping):
        for raw_cat, raw_val in raw_stances.items():
            if not isinstance(raw_cat, str) or not isinstance(raw_val, str):
                continue
            v_up = raw_val.strip().upper()
            if v_up in ("YES", "OPEN", "ENJOYS"):
                mapped_v = "YES"
            elif v_up in ("MAYBE", "CURIOUS", "SHY_CURIOUS"):
                mapped_v = "MAYBE"
            elif v_up in ("NO", "NOT_FOR_ME", "HARD_NO"):
                mapped_v = "NO"
            else:
                continue
            cat_clean = raw_cat.strip()
            if cat_clean in all_categories:
                stances[cat_clean] = mapped_v
            elif cat_clean in directed_bases:
                for dir_suffix in ("subject_to_user", "user_to_subject"):
                    d_code = f"{cat_clean}:{dir_suffix}"
                    if d_code in all_categories:
                        stances[d_code] = mapped_v
    seed_out["stances"] = stances

    soft_prefs: dict[str, Any] = {
        "dim_lamp": True,
        "close_curtains": True,
        "aftercare_warmth": "cuddle_or_tea",
    }
    raw_sp = raw_seed.get("soft_preferences")
    if isinstance(raw_sp, list):
        tokens = {str(x).strip().lower() for x in raw_sp if isinstance(x, str)}
        if "dim_light" in tokens or "dim_lamp" in tokens:
            soft_prefs["dim_lamp"] = True
        if "curtains_closed" in tokens or "close_curtains" in tokens:
            soft_prefs["close_curtains"] = True
        if "slow_pace" in tokens or "slow_start" in tokens:
            soft_prefs["slow_pace"] = True
        if "stay_close_after" in tokens:
            soft_prefs["aftercare_warmth"] = "stay_close_after"
        elif "quiet_aftercare" in tokens:
            soft_prefs["aftercare_warmth"] = "quiet_aftercare"
    elif isinstance(raw_sp, Mapping):
        if isinstance(raw_sp.get("dim_lamp"), bool):
            soft_prefs["dim_lamp"] = raw_sp["dim_lamp"]
        elif isinstance(raw_sp.get("dim_light"), bool):
            soft_prefs["dim_lamp"] = raw_sp["dim_light"]
        if isinstance(raw_sp.get("close_curtains"), bool):
            soft_prefs["close_curtains"] = raw_sp["close_curtains"]
        elif isinstance(raw_sp.get("curtains_closed"), bool):
            soft_prefs["close_curtains"] = raw_sp["curtains_closed"]
        if isinstance(raw_sp.get("slow_pace"), bool):
            soft_prefs["slow_pace"] = raw_sp["slow_pace"]
        if isinstance(raw_sp.get("aftercare_warmth"), str) and raw_sp["aftercare_warmth"].strip():
            soft_prefs["aftercare_warmth"] = raw_sp["aftercare_warmth"].strip()[:60]
    seed_out["soft_preferences"] = soft_prefs
    return seed_out


def _seed_adult_intimacy_from_pack(
    root_path: Path,
    pack_doc: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    from . import adult_intimacy_view as aiv

    ai_path = aiv.adult_intimacy_state_path(root_path)
    if ai_path.is_symlink() or (ai_path.parent.exists() and ai_path.parent.is_symlink()):
        return {"seeded": False, "reason_code": "SYMLINK_PATH_FORBIDDEN"}

    char_ctx = aiv._load_character_context(root_path, subject_view=True)
    body_cfg = pack_doc.get("body") if isinstance(pack_doc.get("body"), Mapping) else {}
    preset_name = str(body_cfg.get("preset") or char_ctx.get("body_preset") or "neutral")
    if preset_name not in aiv.BODY_PRESET_DEFAULTS:
        preset_name = "neutral"
    overrides = body_cfg.get("overrides") if isinstance(body_cfg.get("overrides"), Mapping) else None
    char_ctx["body_preset"] = preset_name
    char_ctx["body_overrides"] = copy.deepcopy(dict(overrides)) if overrides else {}
    char_ctx["pack_id"] = str(pack_doc.get("pack_id") or char_ctx.get("pack_id") or "neutral_resident")
    char_ctx["version"] = int(pack_doc.get("version") or 1)
    char_ctx["optional_processes"] = []

    ai_state, ai_reason = aiv._read_stored_state_zero_write(root_path, char_ctx)
    if ai_reason is not None:
        return {"seeded": False, "reason_code": ai_reason}

    body_profile = aiv._build_validated_body_profile(preset_name, overrides)
    ai_state["body"]["profile"] = body_profile
    ai_state["body"]["profile_explicitly_set"] = False
    ai_state["body"]["optional_processes"] = []
    if body_profile.get("genitals") != "penis_testes":
        ai_state["body"]["refractory_beats_left"] = 0
        ai_state["body"]["refractory_until"] = None

    ai_state["seed"] = _map_pack_sexual_seed_to_draft(
        pack_doc.get("sexual_seed"),
        existing_seed=ai_state.get("seed") or {},
        default_stances=aiv.DEFAULT_STARTER_STANCES,
        all_categories=aiv.ALL_CATEGORIES,
        directed_bases=aiv.DIRECTED_BASES,
    )

    # Iron Law: character pack NEVER opens adult gate or deployer attestation.
    ai_state["gate"]["adult_confirmed"] = False
    ai_state["gate"]["feature_enabled"] = False
    ai_state["gate"]["attested_by"] = None
    ai_state["gate"]["attested_at"] = None

    ai_state["revision"] = int(ai_state.get("revision") or 0) + 1
    ai_state["updated_at"] = aiv._to_iso(aiv._parse_now(now_iso))
    written = aiv._atomic_write_json(ai_path, ai_state)
    return {
        "seeded": True,
        "revision": int(written["revision"]),
        "body_preset": preset_name,
        "seed_status": str(written["seed"]["seed_status"]),
        "gate_open": False,
    }


def _seed_emotional_tension_from_pack(
    root_path: Path,
    pack_doc: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    from . import emotional_tension_view as etv

    et_state, et_reason = etv.read_emotional_tension_state(root_path)
    if et_reason is not None:
        return {"seeded": False, "reason_code": et_reason}

    raw_obs = pack_doc.get("obligations")
    pack_obs = etv._normalize_pack_obligations(raw_obs)
    if isinstance(raw_obs, list):
        for idx, item in enumerate(raw_obs):
            if not isinstance(item, Mapping) or item.get("attends") is False:
                continue
            oid = str(
                item.get("obligation_id")
                or item.get("id")
                or item.get("label")
                or item.get("title")
                or f"pack_ob_{idx}"
            ).strip()
            if not oid or oid in pack_obs:
                continue
            kind = str(item.get("kind") or item.get("institution_kind") or "routine").strip() or "routine"
            memo = item.get("private_memo") or item.get("label") or item.get("title")
            memo_str = str(memo).strip()[:160] if isinstance(memo, str) and memo.strip() else None
            pack_obs[oid] = {
                "obligation_id": oid,
                "mode": "OFFERED",
                "kind": kind,
                "pay_range": None,
                "private_memo": memo_str,
                "from_character_pack": True,
                "per_week_range": [1, 2],
                "minutes_range": [90, 120],
                "lead_days_range": [1, 3],
            }

    default_ids = set(etv._default_obligations().keys())
    existing_obs = et_state.get("schedule_domain", {}).get("obligations")
    kept_custom: dict[str, Any] = {}
    if isinstance(existing_obs, Mapping):
        for oid, ob in existing_obs.items():
            if not isinstance(ob, Mapping):
                continue
            if ob.get("from_character_pack") is True or oid in default_ids:
                continue
            kept_custom[str(oid)] = copy.deepcopy(dict(ob))

    if pack_obs:
        merged_obs = {**kept_custom, **pack_obs}
    elif kept_custom:
        merged_obs = kept_custom
    else:
        merged_obs = etv._default_obligations()

    et_state.setdefault("schedule_domain", {})["obligations"] = merged_obs
    et_state["revision"] = int(et_state.get("revision") or 0) + 1
    et_state["updated_at"] = now_iso
    written = etv.write_emotional_tension_state(root_path, et_state)
    return {
        "seeded": True,
        "revision": int(written["revision"]),
        "obligation_ids": sorted(merged_obs.keys()),
        "pack_obligation_count": len(pack_obs),
    }


def seed_character_pack_siblings(
    root: Path | str,
    pack_doc: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    """Seed ``autonomy_rhythm``, ``adult_intimacy`` (DRAFT, gate closed), and ``emotional_tension``."""

    root_path = Path(root)
    effective_now = _iso_or_none(now_iso) or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    normalized_doc = validate_and_normalize_pack(pack_doc)
    out: dict[str, Any] = {}

    ar_file = root_path / STATE_DIRNAME / "autonomy_rhythm.json"
    if create_if_missing or ar_file.exists():
        out["autonomy_rhythm"] = _seed_autonomy_rhythm_from_pack(
            root_path, normalized_doc, now_iso=effective_now
        )
    else:
        out["autonomy_rhythm"] = {"seeded": False, "skipped_absent": True}

    ai_file = root_path / STATE_DIRNAME / "adult_intimacy.json"
    if create_if_missing or ai_file.exists():
        out["adult_intimacy"] = _seed_adult_intimacy_from_pack(
            root_path, normalized_doc, now_iso=effective_now
        )
    else:
        out["adult_intimacy"] = {"seeded": False, "skipped_absent": True}

    et_file = root_path / STATE_DIRNAME / "emotional_tension.json"
    if create_if_missing or et_file.exists():
        out["emotional_tension"] = _seed_emotional_tension_from_pack(
            root_path, normalized_doc, now_iso=effective_now
        )
    else:
        out["emotional_tension"] = {"seeded": False, "skipped_absent": True}

    return out


def apply_select_preset(
    current_state: Mapping[str, Any],
    *,
    preset_id: Any,
    now_iso: str,
    root: Optional[Path | str] = None,
    seed_siblings: bool = False,
) -> dict[str, Any]:
    if not isinstance(seed_siblings, bool):
        raise CharacterPackValidationError("SEED_SIBLINGS_INVALID")
    if not isinstance(preset_id, str):
        raise CharacterPackValidationError("PRESET_UNKNOWN")
    resolved_id = PRESET_ALIASES.get(preset_id, preset_id)
    if resolved_id not in BUILT_IN_PRESETS:
        raise CharacterPackValidationError("PRESET_UNKNOWN")
    pack_doc = validate_and_normalize_pack(BUILT_IN_PRESETS[resolved_id])
    next_rev = int(current_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "source": "preset_selected",
        "preset_id": resolved_id,
        "pack_doc": pack_doc,
        "everyday_accessories": copy.deepcopy(pack_doc.get("starter_accessories") or []),
        # 51-可组合身体：切换角色预设绝不自动启用任何生理周期模拟。
        "optional_processes": [],
        # 38 §4.3：换包后部署者对旧包的确认自动失效。
        "attestation": None,
    }
    if seed_siblings and root is not None:
        seed_character_pack_siblings(
            root, pack_doc, now_iso=now_iso, create_if_missing=True
        )
    return new_state


def apply_custom_pack(
    current_state: Mapping[str, Any],
    *,
    pack: Any,
    now_iso: str,
    root: Optional[Path | str] = None,
    seed_siblings: bool = False,
) -> dict[str, Any]:
    if not isinstance(seed_siblings, bool):
        raise CharacterPackValidationError("SEED_SIBLINGS_INVALID")
    pack_doc = validate_and_normalize_pack(pack)
    next_rev = int(current_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "source": "custom_pack",
        "preset_id": None,
        "pack_doc": pack_doc,
        "everyday_accessories": copy.deepcopy(pack_doc.get("starter_accessories") or []),
        # 51-可组合身体：自定义角色包绝不自动启用生理体验。
        "optional_processes": [],
        # 38 §4.3：导入新角色包后，旧包确认不再生效。
        "attestation": None,
    }
    if seed_siblings and root is not None:
        seed_character_pack_siblings(
            root, pack_doc, now_iso=now_iso, create_if_missing=True
        )
    return new_state


def select_preset(
    root_or_state: Path | str | Mapping[str, Any],
    *,
    preset_id: Any,
    now_iso: Optional[str] = None,
    seed_siblings: bool = False,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Convenience entry for ``select_preset`` supporting optional ``seed_siblings``."""
    return apply_character_pack_action(
        root_or_state,
        {"action": "select_preset", "preset_id": preset_id, "seed_siblings": seed_siblings},
        now_iso=now_iso,
        subject_view=subject_view,
        seed_siblings=seed_siblings,
    )


def import_pack_json(
    root_or_state: Path | str | Mapping[str, Any],
    *,
    pack_json: Any = None,
    pack: Any = None,
    now_iso: Optional[str] = None,
    seed_siblings: bool = False,
    subject_view: bool = False,
) -> dict[str, Any]:
    """Convenience entry for ``import_pack_json`` supporting optional ``seed_siblings``."""
    body: dict[str, Any] = {"action": "import_pack_json", "seed_siblings": seed_siblings}
    if pack_json is not None:
        body["pack_json"] = pack_json
    elif pack is not None:
        body["pack"] = pack
    return apply_character_pack_action(
        root_or_state,
        body,
        now_iso=now_iso,
        subject_view=subject_view,
        seed_siblings=seed_siblings,
    )


def apply_update_identity(
    current_state: Mapping[str, Any],
    *,
    fields_present: Mapping[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    updatable_keys = {
        "display_name",
        "pronouns",
        "subject_calls_user",
        "user_calls_subject",
        "sensory_preferences",
        "everyday_accessories",
    }
    supplied = {k: v for k, v in fields_present.items() if k in updatable_keys}
    if not supplied:
        raise CharacterPackValidationError("IDENTITY_UPDATE_EMPTY")

    old_doc = validate_and_normalize_pack(current_state["pack_doc"])
    old_sha = compute_pack_sha256(old_doc)
    pack_doc = copy.deepcopy(old_doc)
    accessories = copy.deepcopy(list(current_state.get("everyday_accessories") or []))

    if "display_name" in supplied:
        pack_doc["display_name"] = _clean_text(
            supplied["display_name"],
            min_len=1,
            max_len=40,
            reason_code="PACK_FIELD_INVALID:display_name",
        )
    if "pronouns" in supplied:
        if supplied["pronouns"] is None:
            pack_doc["pronouns"] = "TA"
        else:
            pack_doc["pronouns"] = _clean_text(
                supplied["pronouns"],
                min_len=1,
                max_len=16,
                reason_code="PACK_FIELD_INVALID:pronouns",
            )
    if "subject_calls_user" in supplied:
        pack_doc["address"]["subject_calls_user"] = _validate_address_list(
            supplied["subject_calls_user"], field_path="subject_calls_user"
        )
    if "user_calls_subject" in supplied:
        pack_doc["address"]["user_calls_subject"] = _validate_address_list(
            supplied["user_calls_subject"], field_path="user_calls_subject"
        )
    if "sensory_preferences" in supplied:
        pack_doc["sensory_preferences"] = validate_sensory_preferences(
            supplied["sensory_preferences"]
        )
    if "everyday_accessories" in supplied:
        accessories = validate_accessories(
            supplied["everyday_accessories"], field_name="everyday_accessories"
        )
        pack_doc["starter_accessories"] = copy.deepcopy(accessories)

    normalized_doc = validate_and_normalize_pack(pack_doc)
    new_sha = compute_pack_sha256(normalized_doc)
    preserved_att = (
        copy.deepcopy(current_state.get("attestation")) if new_sha == old_sha else None
    )
    next_rev = int(current_state.get("revision") or 0) + 1
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "source": "identity_updated",
        "preset_id": current_state.get("preset_id"),
        "pack_doc": normalized_doc,
        "everyday_accessories": accessories,
        # 51-可组合身体：改代词/名字/称呼/配饰绝不改变或自动开启生理体验。
        "optional_processes": list(current_state.get("optional_processes") or []),
        "attestation": preserved_att,
    }


def apply_set_optional_physiology(
    current_state: Mapping[str, Any], *, optional_processes: Any, now_iso: str
) -> dict[str, Any]:
    validated_procs = validate_optional_processes(optional_processes)
    pack_doc = validate_and_normalize_pack(current_state["pack_doc"])
    next_rev = int(current_state.get("revision") or 0) + 1
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "source": str(current_state.get("source") or "physiology_updated"),
        "preset_id": current_state.get("preset_id"),
        "pack_doc": pack_doc,
        "everyday_accessories": copy.deepcopy(
            list(current_state.get("everyday_accessories") or [])
        ),
        "optional_processes": validated_procs,
        "attestation": copy.deepcopy(current_state.get("attestation")),
    }


def apply_export_pack(
    current_state: Mapping[str, Any],
    *,
    include_sexual_seed: bool = True,
) -> dict[str, Any]:
    """Export active character pack as a clean ``proposed.character_pack.v1`` JSON payload."""
    clean_pack = build_exportable_pack(
        current_state, include_sexual_seed=bool(include_sexual_seed)
    )
    pack_json = json.dumps(clean_pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    pack_sha256 = compute_pack_sha256(clean_pack)
    pack_ref = compute_pack_ref(clean_pack)
    return {
        "action": "export_pack",
        "changed": False,
        "filename": f"{clean_pack['pack_id']}.pack.json",
        "pack_json": pack_json,
        "exportable_pack": clean_pack,
        "pack": copy.deepcopy(clean_pack),
        "pack_ref": pack_ref,
        "pack_sha256": pack_sha256,
    }


export_pack = apply_export_pack


def apply_attest_character_pack(
    current_state: Mapping[str, Any],
    *,
    now_iso: str,
    attested_by: Any = "deployer",
    pack_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Record deployer-side adult attestation bound to ``pack_sha256`` (never inside pack_doc)."""
    if attested_by != "deployer":
        raise CharacterPackValidationError("ATTESTED_BY_INVALID")
    cleaned_now = _iso_or_none(now_iso)
    if cleaned_now is None:
        raise CharacterPackValidationError("ATTESTED_AT_INVALID")
    pack_doc = validate_and_normalize_pack(current_state["pack_doc"])
    gate = evaluate_adult_gate_preview(pack_doc)
    if gate.get("verdict") == "FAIL":
        reasons = gate.get("reasons") or ["ADULT_GATE_FAILED"]
        raise CharacterPackValidationError(f"ADULT_GATE_FAILED:{reasons[0]}")
    computed_sha256 = compute_pack_sha256(pack_doc)
    if pack_sha256 is not None:
        if not isinstance(pack_sha256, str) or pack_sha256.strip().lower() != computed_sha256:
            raise CharacterPackValidationError("PACK_SHA256_MISMATCH")
    attestation = {
        "pack_sha256": computed_sha256,
        "pack_ref": compute_pack_ref(pack_doc),
        "attested_by": "deployer",
        "attested_at": cleaned_now,
    }
    next_rev = int(current_state.get("revision") or 0) + 1
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": cleaned_now,
        "source": str(current_state.get("source") or "default_neutral_preset"),
        "preset_id": current_state.get("preset_id"),
        "pack_doc": pack_doc,
        "everyday_accessories": copy.deepcopy(
            list(current_state.get("everyday_accessories") or [])
        ),
        "optional_processes": list(current_state.get("optional_processes") or []),
        "attestation": attestation,
    }


attest_character_pack = apply_attest_character_pack


def apply_character_pack_action(
    root_or_state: Path | str | Mapping[str, Any],
    payload: Optional[Mapping[str, Any]] = None,
    *,
    action: Optional[str] = None,
    now_iso: Optional[str] = None,
    subject_view: bool = False,
    seed_siblings: bool = False,
) -> dict[str, Any]:
    """Unified action entry supporting ``export_pack`` and ``attest_character_pack`` alongside presets/custom/identity."""
    body = dict(payload or {})
    resolved_action = str(action or body.get("action") or "").strip().lower()
    if not resolved_action:
        raise CharacterPackValidationError("CHARACTER_PACK_ACTION_REQUIRED")

    effective_now = _iso_or_none(now_iso) or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    is_root = isinstance(root_or_state, (Path, str))
    if is_root:
        state, state_reason = read_character_pack_state(root_or_state)
        if state_reason and resolved_action != "export_pack":
            return {
                "ok": False,
                "status": "rejected",
                "data": {},
                "reason_code": state_reason,
                "http_status": 409,
            }
    else:
        state = dict(root_or_state)
        state_reason = None

    effective_seed_siblings = False
    try:
        if resolved_action == "export_pack":
            allowed = {"action", "include_sexual_seed"}
            extra = sorted(set(body.keys()) - allowed)
            if extra:
                raise CharacterPackValidationError(f"UNKNOWN_FIELD:{','.join(extra)}")
            inc_seed = body.get("include_sexual_seed", True)
            if not isinstance(inc_seed, bool):
                raise CharacterPackValidationError("INCLUDE_SEXUAL_SEED_INVALID")
            exported = apply_export_pack(state, include_sexual_seed=inc_seed)
            if is_root:
                return {"ok": True, "status": "ok", "data": exported}
            return {"state": state, "data": exported}

        if resolved_action == "attest_character_pack":
            allowed = {"action", "attested_by", "pack_sha256"}
            extra = sorted(set(body.keys()) - allowed)
            if extra:
                raise CharacterPackValidationError(f"UNKNOWN_FIELD:{','.join(extra)}")
            new_state = apply_attest_character_pack(
                state,
                now_iso=effective_now,
                attested_by=body.get("attested_by", "deployer"),
                pack_sha256=body.get("pack_sha256"),
            )
        elif resolved_action == "select_preset":
            allowed = {"action", "preset_id", "seed_siblings"}
            extra = sorted(set(body.keys()) - allowed)
            if extra:
                raise CharacterPackValidationError(f"UNKNOWN_FIELD:{','.join(extra)}")
            raw_seed_flag = body.get("seed_siblings", seed_siblings)
            if not isinstance(raw_seed_flag, bool):
                raise CharacterPackValidationError("SEED_SIBLINGS_INVALID")
            effective_seed_siblings = raw_seed_flag
            new_state = apply_select_preset(
                state, preset_id=body.get("preset_id"), now_iso=effective_now
            )
        elif resolved_action == "apply_custom_pack":
            allowed = {"action", "pack", "seed_siblings"}
            extra = sorted(set(body.keys()) - allowed)
            if extra:
                raise CharacterPackValidationError(f"UNKNOWN_FIELD:{','.join(extra)}")
            if "pack" not in body:
                raise CharacterPackValidationError("PACK_REQUIRED")
            raw_seed_flag = body.get("seed_siblings", seed_siblings)
            if not isinstance(raw_seed_flag, bool):
                raise CharacterPackValidationError("SEED_SIBLINGS_INVALID")
            effective_seed_siblings = raw_seed_flag
            new_state = apply_custom_pack(
                state, pack=body.get("pack"), now_iso=effective_now
            )
        elif resolved_action == "import_pack_json":
            allowed = {"action", "pack_json", "pack", "seed_siblings"}
            extra = sorted(set(body.keys()) - allowed)
            if extra:
                raise CharacterPackValidationError(f"UNKNOWN_FIELD:{','.join(extra)}")
            raw_seed_flag = body.get("seed_siblings", seed_siblings)
            if not isinstance(raw_seed_flag, bool):
                raise CharacterPackValidationError("SEED_SIBLINGS_INVALID")
            effective_seed_siblings = raw_seed_flag
            if "pack_json" in body:
                raw_pj = body.get("pack_json")
                if isinstance(raw_pj, str):
                    if not raw_pj.strip():
                        raise CharacterPackValidationError("PACK_JSON_REQUIRED")
                    try:
                        parsed_pack = json.loads(raw_pj)
                    except ValueError as exc:
                        raise CharacterPackValidationError("PACK_JSON_INVALID") from exc
                elif isinstance(raw_pj, Mapping):
                    parsed_pack = raw_pj
                else:
                    raise CharacterPackValidationError("PACK_JSON_INVALID")
            elif "pack" in body:
                parsed_pack = body.get("pack")
            else:
                raise CharacterPackValidationError("PACK_JSON_REQUIRED")
            new_state = apply_custom_pack(
                state, pack=parsed_pack, now_iso=effective_now
            )
        elif resolved_action == "update_identity":
            new_state = apply_update_identity(
                state, fields_present=body, now_iso=effective_now
            )
        elif resolved_action == "set_optional_physiology":
            if not subject_view:
                return {
                    "ok": False,
                    "status": "rejected",
                    "data": {},
                    "reason_code": "CAPABILITY_REQUIRED",
                    "http_status": 403,
                }
            if "optional_processes" not in body:
                raise CharacterPackValidationError("OPTIONAL_PROCESS_INVALID")
            new_state = apply_set_optional_physiology(
                state,
                optional_processes=body.get("optional_processes"),
                now_iso=effective_now,
            )
        else:
            raise CharacterPackValidationError("CHARACTER_PACK_ACTION_INVALID")
    except CharacterPackValidationError as exc:
        if is_root:
            return {
                "ok": False,
                "status": "bad_request",
                "data": {},
                "reason_code": exc.reason_code,
                "http_status": 400,
            }
        raise

    written = write_character_pack_state(root_or_state, new_state) if is_root else new_state
    seeded_siblings: Optional[dict[str, Any]] = None
    if (
        is_root
        and effective_seed_siblings
        and resolved_action in ("select_preset", "apply_custom_pack", "import_pack_json")
    ):
        seeded_siblings = seed_character_pack_siblings(
            root_or_state,
            written["pack_doc"],
            now_iso=effective_now,
            create_if_missing=True,
        )
    projected = project_character_pack(
        written, state_reason=None, subject_view=bool(subject_view)
    )
    att = written.get("attestation")
    res_data: dict[str, Any] = {
        "action": resolved_action,
        "changed": True,
        "revision": written.get("revision"),
        "updated_at": written.get("updated_at"),
        "active_pack": projected["active_pack"],
        "exportable_pack": projected["exportable_pack"],
        "attestation": projected["attestation"],
        "adult_gate_preview": projected["adult_gate_preview"],
        "boundaries": projected["boundaries"],
    }
    if seeded_siblings is not None:
        res_data["seeded_siblings"] = seeded_siblings
    if resolved_action == "attest_character_pack" and isinstance(att, Mapping):
        res_data["pack_sha256"] = att.get("pack_sha256")
        res_data["attested_by"] = att.get("attested_by")
        res_data["attested_at"] = att.get("attested_at")
    if is_root:
        return {"ok": True, "status": "ok", "data": res_data}
    return {"state": written, "data": res_data}


# ---------------------------------------------------------------------------------------------
# Domain B: Sensory Cards & Partner Sensory Notes persistence (<root>/phone/sensory_cards.json)
# ---------------------------------------------------------------------------------------------


def sensory_cards_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / SENSORY_CARDS_FILENAME


def empty_sensory_cards_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "sound_cards": [],
        "partner_sensory_notes": [],
    }


def _validate_stored_card(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid sound card")
    card_id = _clean_text(
        raw.get("card_id"), min_len=3, max_len=80, reason_code="SOUND_CARD_INVALID"
    )
    title = _clean_text(
        raw.get("title"), min_len=1, max_len=MAX_CARD_TITLE, reason_code="SOUND_CARD_INVALID"
    )
    place_id = _clean_text(
        raw.get("place_id"), min_len=1, max_len=80, reason_code="SOUND_CARD_INVALID"
    )
    place_label = _clean_text(
        raw.get("place_label") or place_id,
        min_len=1,
        max_len=80,
        reason_code="SOUND_CARD_INVALID",
    )
    heard_raw = raw.get("heard_sources")
    if not isinstance(heard_raw, list) or not heard_raw or len(heard_raw) > 16:
        raise ValueError("invalid heard_sources")
    heard_sources = [
        _clean_text(item, min_len=1, max_len=160, reason_code="SOUND_CARD_INVALID")
        for item in heard_raw
    ]
    quiet_fallback = bool(raw.get("quiet_fallback"))
    impression_raw = raw.get("impression_note")
    impression_note: Optional[str] = None
    if impression_raw is not None:
        impression_note = _clean_multiline_text(
            impression_raw,
            min_len=1,
            max_len=MAX_IMPRESSION_NOTE,
            reason_code="SOUND_CARD_INVALID",
        )
    window_state_then = _clean_text(
        raw.get("window_state_then") or "unknown",
        min_len=1,
        max_len=32,
        reason_code="SOUND_CARD_INVALID",
    )
    shared = raw.get("shared")
    if not isinstance(shared, bool):
        raise ValueError("invalid shared flag")
    recorded_at = _iso_or_none(raw.get("recorded_at"))
    op_key_raw = raw.get("operation_key")
    op_key: Optional[str] = None
    if op_key_raw is not None:
        op_key = _clean_text(
            op_key_raw,
            min_len=1,
            max_len=MAX_OPERATION_KEY,
            reason_code="OPERATION_KEY_INVALID",
        )
    card: dict[str, Any] = {
        "card_id": card_id,
        "title": title,
        "place_id": place_id,
        "place_label": place_label,
        "heard_sources": heard_sources,
        "quiet_fallback": quiet_fallback,
        "impression_note": impression_note,
        "window_state_then": window_state_then,
        "medium": "text_sound_card",
        "has_audio_recording": False,
        "shared": shared,
        "recorded_at": recorded_at,
    }
    if op_key is not None:
        card["operation_key"] = op_key
    return card


def _validate_stored_note(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid partner sensory note")
    note_id = _clean_text(
        raw.get("note_id"), min_len=3, max_len=80, reason_code="SENSORY_NOTE_INVALID"
    )
    text = _clean_multiline_text(
        raw.get("text"),
        min_len=1,
        max_len=MAX_PARTNER_SENSORY_NOTE_TEXT,
        reason_code="SENSORY_NOTE_INVALID",
    )
    return {
        "note_id": note_id,
        "text": text,
        "created_at": _iso_or_none(raw.get("created_at")),
    }


def _validate_sensory_cards_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    cards_raw = raw.get("sound_cards")
    if not isinstance(cards_raw, list) or len(cards_raw) > MAX_SOUND_CARDS:
        raise ValueError("invalid sound_cards")
    notes_raw = raw.get("partner_sensory_notes")
    if not isinstance(notes_raw, list) or len(notes_raw) > MAX_PARTNER_SENSORY_NOTES:
        raise ValueError("invalid partner_sensory_notes")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_or_none(raw.get("updated_at")),
        "sound_cards": [_validate_stored_card(item) for item in cards_raw],
        "partner_sensory_notes": [_validate_stored_note(item) for item in notes_raw],
    }


def read_sensory_cards_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Pure zero-write read of ``sensory_cards.json``."""

    path = sensory_cards_state_path(root)
    if path.is_symlink():
        return empty_sensory_cards_state(), "SENSORY_CARDS_STATE_CORRUPT"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_sensory_cards_state(), None
    except OSError:
        return empty_sensory_cards_state(), "SENSORY_CARDS_STATE_UNREADABLE"
    try:
        parsed = json.loads(raw_text)
        return _validate_sensory_cards_state(parsed), None
    except Exception:
        return empty_sensory_cards_state(), "SENSORY_CARDS_STATE_CORRUPT"


def write_sensory_cards_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validate_sensory_cards_state(state)
    return _atomic_write_json(sensory_cards_state_path(root), validated)


# ---------------------------------------------------------------------------------------------
# Multi-channel sensory field derivation (pure, deterministic, zero-write)
# ---------------------------------------------------------------------------------------------


def _extract_place_info(world_result: Mapping[str, Any]) -> dict[str, Any]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    available = bool(world_result.get("ok") and location.get("status") == "available")
    if not available:
        return {
            "available": False,
            "reason_code": str(
                location.get("reason_code")
                or world_result.get("reason_code")
                or "WORLD_UNAVAILABLE"
            ),
            "place_id": None,
            "place_label": None,
            "area_id": None,
            "home": None,
        }
    place_id = str(location.get("place_id") or "") or None
    place_label = (
        str(location.get("label") or location.get("name") or place_id or "").strip()
        or place_id
    )
    is_home = bool(location.get("home")) or place_id == "home"
    return {
        "available": True,
        "reason_code": None,
        "place_id": place_id,
        "place_label": place_label,
        "area_id": str(location.get("area_id") or "") or None,
        "home": is_home,
    }


def resolve_adjacent_places(
    world_result: Mapping[str, Any],
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> list[dict[str, str]]:
    """Return directly adjacent ``{place_id, label}`` neighbours from World navigation/snapshot."""

    place_info = _extract_place_info(world_result)
    current_place = place_info.get("place_id")
    if not place_info["available"] or not current_place:
        return []

    labels_by_id: dict[str, str] = {}
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    destinations = (
        data.get("destinations") if isinstance(data.get("destinations"), Mapping) else {}
    )
    snapshot_neighbors: list[str] = []
    for row in destinations.get("places") or ():
        if not isinstance(row, Mapping):
            continue
        pid = str(row.get("place_id") or "").strip()
        if not pid or pid == current_place or row.get("here") is True:
            continue
        lbl = str(row.get("label") or row.get("name") or pid).strip() or pid
        labels_by_id[pid] = lbl
        route = row.get("route")
        # Direct 1-edge walk: route has [current_place, neighbor] (len 2) or [neighbor] (len 1).
        if row.get("reach") == "reachable":
            if isinstance(route, list) and 1 <= len(route) <= 2:
                snapshot_neighbors.append(pid)

    runtime_neighbors: list[str] = []
    try:
        import world_runtime_data as wrd

        wdata = wrd.active_world(environment)
        for loc_id, loc_meta in (wdata.locations or {}).items():
            if isinstance(loc_meta, Mapping) and loc_id not in labels_by_id:
                labels_by_id[loc_id] = (
                    str(
                        loc_meta.get("label")
                        or loc_meta.get("display_name")
                        or loc_meta.get("name")
                        or loc_id
                    ).strip()
                    or loc_id
                )
        for src, dst in (wdata.walking_durations or {}).keys():
            src_place = wdata.node_place(str(src))
            dst_place = wdata.node_place(str(dst))
            if src_place == current_place and dst_place and dst_place != current_place:
                if dst_place not in runtime_neighbors:
                    runtime_neighbors.append(dst_place)
    except Exception:
        pass

    merged_ids: list[str] = []
    for pid in runtime_neighbors + snapshot_neighbors:
        if pid and pid != current_place and pid not in merged_ids:
            merged_ids.append(pid)

    return [
        {"place_id": pid, "label": labels_by_id.get(pid, pid)}
        for pid in merged_ids
    ]


def collect_ambient_events_by_place(
    world_result: Mapping[str, Any],
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read active ``world_ambient_events`` for current place + adjacent places (zero-write)."""

    place_info = _extract_place_info(world_result)
    current_place = place_info.get("place_id")
    if not place_info["available"] or not current_place:
        return {}

    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    world_block = data.get("world") if isinstance(data.get("world"), Mapping) else {}
    day_dt = somatic_view.parse_instant(world_block.get("local_date"))
    now_dt = somatic_view.parse_instant(world_block.get("local_time"))
    # ``local_date`` may be YYYY-MM-DD (not full ISO instant); parse date fallback cleanly.
    local_day = None
    raw_date = world_block.get("local_date")
    if isinstance(raw_date, str) and len(raw_date.strip()) >= 10:
        try:
            local_day = datetime.strptime(raw_date.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            local_day = day_dt.date() if day_dt is not None else None
    elif day_dt is not None:
        local_day = day_dt.date()

    if local_day is None or now_dt is None:
        return {}

    try:
        import world_ambient_events as ambient
        import world_runtime_data as wrd

        if not ambient.feature_enabled(environment):
            return {}
        known_ids = set(wrd.active_world(environment).known_location_ids)
        neighbors = resolve_adjacent_places(world_result, environment=environment)
        wanted = {
            pid
            for pid in [current_place, *[n["place_id"] for n in neighbors]]
            if pid in known_ids
        }
        if not wanted:
            return {}
        events = ambient.active_ambient_events(
            local_day,
            now_dt,
            relevant_location_ids=wanted,
            environment=environment,
        )
    except Exception:
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if not isinstance(ev, Mapping):
            continue
        loc = str(ev.get("location_id") or "").strip()
        if not loc:
            continue
        grouped.setdefault(loc, []).append(dict(ev))
    return grouped


def _event_summary(ev: Mapping[str, Any]) -> str:
    payload = ev.get("payload")
    if isinstance(payload, Mapping) and isinstance(payload.get("summary"), str):
        return payload["summary"].strip()
    if isinstance(ev.get("summary"), str):
        return str(ev["summary"]).strip()
    return str(ev.get("event_type") or ev.get("event_id") or "").strip()


def _is_audible_ambient(ev: Mapping[str, Any]) -> bool:
    etype = str(ev.get("event_type") or "").strip()
    if not etype:
        return bool(_event_summary(ev))
    if etype in VISUAL_ONLY_AMBIENT_TYPES:
        return False
    if etype.endswith(("_notice", "_display", "_poster", "_light", "_shadows", "_lights")):
        return False
    return True


def _is_light_ambient(ev: Mapping[str, Any]) -> bool:
    etype = str(ev.get("event_type") or "").strip()
    if etype in LIGHT_BEARING_AMBIENT_TYPES:
        return True
    return any(tok in etype for tok in ("light", "sunset", "shadow"))


def _is_rain_ambient(ev: Mapping[str, Any]) -> bool:
    etype = str(ev.get("event_type") or "").strip().lower()
    summary = _event_summary(ev)
    return ("rain" in etype) or (etype == "home_window_sound" and "雨" in summary) or ("雨" in summary)


def _wearing_headphones_filter(accessories: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, Mapping)
        and item.get("kind") == "headphones"
        and item.get("mode") == "ambient_filter"
        for item in accessories
    )


def _world_local_hour(world_result: Mapping[str, Any]) -> Optional[int]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    world_block = data.get("world") if isinstance(data.get("world"), Mapping) else {}
    now_dt = somatic_view.parse_instant(world_block.get("local_time"))
    if now_dt is None:
        return None
    return int(now_dt.hour)


def _extract_room_light(world_result: Mapping[str, Any]) -> dict[str, Any]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    env = (
        data.get("local_environment")
        if isinstance(data.get("local_environment"), Mapping)
        else {}
    )
    light = env.get("light") if isinstance(env.get("light"), Mapping) else None
    if light is None and isinstance(data.get("room_light"), Mapping):
        light = data.get("room_light")
    light = light or {}
    return {
        "status": str(light.get("status") or "unknown"),
        "power": light.get("power"),
        "band": light.get("band"),
        "declared_source": light.get("declared_source"),
        "fixture_ids": list(light.get("fixture_ids") or []),
        "reason_code": light.get("reason_code"),
    }


def derive_sensory_field(
    *,
    world_result: Mapping[str, Any],
    body_view: Mapping[str, Any],
    somatic_state: Mapping[str, Any],
    somatic_reason: Optional[str],
    character_state: Mapping[str, Any],
    cards_state: Mapping[str, Any],
    cards_reason: Optional[str],
    now_iso: str,
    subject_view: bool = False,
    ambient_by_place: Optional[Mapping[str, list[dict[str, Any]]]] = None,
    adjacent_places: Optional[list[dict[str, str]]] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Derive the 5-channel ``phone.body.sensory_field.v1`` projection (100% pure, zero-write)."""

    place_info = _extract_place_info(world_result)
    world_ok = place_info["available"]
    place_id = place_info["place_id"]
    place_label = place_info["place_label"]
    area_id = place_info["area_id"]
    at_home = bool(place_info["home"]) if world_ok else False

    pack_doc = validate_and_normalize_pack(character_state["pack_doc"])
    prefs = pack_doc["sensory_preferences"]
    accessories = list(character_state.get("everyday_accessories") or [])
    headphones_filter = _wearing_headphones_filter(accessories)

    if adjacent_places is None:
        adjacent_places = resolve_adjacent_places(world_result, environment=environment)
    if ambient_by_place is None:
        ambient_by_place = collect_ambient_events_by_place(
            world_result, environment=environment
        )

    local_events = list(ambient_by_place.get(place_id or "", ())) if world_ok else []
    rain_active = any(_is_rain_ambient(ev) for ev in local_events)

    raw_window = (somatic_state.get("window") or {}).get("state")
    if not world_ok:
        window_state_effective = "unknown"
    elif at_home:
        window_state_effective = (
            raw_window if raw_window in somatic_view.WINDOW_STATES else "unknown"
        )
    else:
        window_state_effective = "outdoor_direct"

    # -- 1. AUDITORY CHANNEL --------------------------------------------------------------
    if not world_ok:
        attenuation = "unknown"
        clarity = "unknown"
    elif headphones_filter:
        attenuation = "headphones_ambient_filter"
        clarity = "muffled" if window_state_effective == "closed" else "softened"
    elif not at_home:
        attenuation = "outdoor_direct"
        clarity = "clear"
    elif window_state_effective == "open":
        attenuation = "none"
        clarity = "clear"
    elif window_state_effective == "ajar":
        attenuation = "partial_window"
        clarity = "softened"
    elif window_state_effective == "closed":
        attenuation = "closed_window_not_soundproof"
        clarity = "muffled"
    else:
        attenuation = "unknown"
        clarity = "softened"

    local_sounds: list[dict[str, Any]] = []
    for ev in local_events:
        if not _is_audible_ambient(ev):
            continue
        summary = _event_summary(ev)
        if not summary:
            continue
        source_id = str(ev.get("event_type") or ev.get("event_id") or "ambient_sound")
        local_sounds.append({
            "source_id": source_id,
            "summary": summary,
            "clarity": clarity if clarity != "unknown" else "softened",
            "origin": "same_place",
        })

    adjacent_unseen_sounds: list[dict[str, Any]] = []
    if world_ok:
        for neighbor in adjacent_places:
            n_id = neighbor["place_id"]
            n_label = neighbor["label"]
            for ev in ambient_by_place.get(n_id, ()):
                if not _is_audible_ambient(ev):
                    continue
                summary = _event_summary(ev)
                if not summary:
                    continue
                source_id = str(ev.get("event_type") or ev.get("event_id") or f"{n_id}_sound")
                adjacent_unseen_sounds.append({
                    "source_id": source_id,
                    "from_place_id": n_id,
                    "from_place_label": n_label,
                    "summary": summary,
                    "clarity": "distant_or_doorway",
                    "visual_access": False,
                    "identity_certainty": "sound_only_unverified",
                })

    total_sound_count = len(local_sounds) + len(adjacent_unseen_sounds)
    if not world_ok:
        quietness_band = "unknown"
    elif total_sound_count == 0:
        quietness_band = "quiet"
    elif clarity == "muffled" or headphones_filter:
        quietness_band = "gentle_background"
    elif clarity == "clear" and total_sound_count >= 2:
        quietness_band = "noisy" if not at_home or window_state_effective == "open" else "lively"
    elif clarity == "clear" and len(local_sounds) >= 1 and window_state_effective in (
        "open",
        "outdoor_direct",
    ):
        quietness_band = "lively"
    else:
        quietness_band = "gentle_background"

    auditory = {
        "status": "available" if world_ok else "unavailable",
        "window_state": window_state_effective,
        "headphones_ambient_filter": headphones_filter,
        "attenuation": attenuation,
        "clarity": clarity,
        "local_sounds": local_sounds,
        "adjacent_unseen_sounds": adjacent_unseen_sounds,
        "quietness_band": quietness_band,
        "closed_door_or_window_is_absolute_soundproof": False,
    }

    # -- 2. VISUAL CHANNEL ----------------------------------------------------------------
    room_light = _extract_room_light(world_result)
    local_hour = _world_local_hour(world_result)
    somatic_world = somatic_view.world_context(world_result)
    pos_raw = somatic_state.get("local_position") or {}
    at_declared_pos = somatic_view._local_position_match(somatic_state, somatic_world)
    local_zone = (
        str(pos_raw.get("zone"))
        if at_declared_pos is True and pos_raw.get("zone") in somatic_view.LOCAL_ZONES
        else None
    )

    visual_cues: list[dict[str, Any]] = []
    for ev in local_events:
        if _is_light_ambient(ev):
            summary = _event_summary(ev)
            if summary:
                etype = str(ev.get("event_type") or ev.get("event_id") or "ambient_light")
                visual_cues.append({
                    "cue_id": etype,
                    "summary": summary,
                    "source_ref": f"world_ambient:{etype}",
                })

    declared_src = str(room_light.get("declared_source") or "")
    fixture_ids = [str(fid) for fid in (room_light.get("fixture_ids") or [])]
    lamp_power = room_light.get("power")
    lamp_band = room_light.get("band")

    is_warm_lamp_on = bool(
        lamp_power == "on"
        and (
            "warm_floor_lamp" in declared_src
            or any("warm_floor_lamp" in fid for fid in fixture_ids)
            or lamp_band in ("moderate", "bright")
        )
    )
    is_bedside_lamp_on = bool(
        lamp_power == "on"
        and not is_warm_lamp_on
        and (
            "bedside_lamp" in declared_src
            or any("bedside_lamp" in fid for fid in fixture_ids)
            or lamp_band == "dim"
        )
    )

    if is_warm_lamp_on:
        visual_cues.append({
            "cue_id": "warm_reading_lamp",
            "summary": "室内暖光灯亮着，桌面光线柔和稳定",
            "source_ref": declared_src or "world_room_environment:warm_floor_lamp",
        })
    elif is_bedside_lamp_on:
        visual_cues.append({
            "cue_id": "bedside_lamp_pool",
            "summary": "床头小灯亮起一圈柔和光晕",
            "source_ref": declared_src or "world_room_environment:bedside_lamp",
        })

    daytime_glare = bool(
        world_ok
        and at_home
        and local_zone == "window_side"
        and window_state_effective == "open"
        and local_hour is not None
        and 11 <= local_hour <= 16
        and not rain_active
    )
    if daytime_glare:
        visual_cues.append({
            "cue_id": "window_daytime_glare",
            "summary": "正午至午后的直晒窗光落在窗边，纸面与屏幕略显刺眼",
            "source_ref": "local:window:open+zone:window_side",
        })

    if not world_ok:
        lighting_band = "unknown"
        reading_suitability = "unknown"
    elif not at_home:
        lighting_band = "outdoor_open"
        if local_hour is not None and (local_hour >= 19 or local_hour < 6):
            reading_suitability = "too_dim"
        else:
            reading_suitability = "comfortable"
    elif daytime_glare:
        lighting_band = "window_glare"
        reading_suitability = "glare_near_window"
    elif is_warm_lamp_on:
        lighting_band = "warm_reading_light"
        reading_suitability = "comfortable"
    elif is_bedside_lamp_on:
        lighting_band = "dim_lamp_pool"
        reading_suitability = "comfortable"
    elif local_hour is not None and 7 <= local_hour <= 17 and window_state_effective in (
        "open",
        "ajar",
    ):
        lighting_band = "natural_daylight"
        reading_suitability = "comfortable"
    elif lamp_power == "off":
        if local_hour is not None and 8 <= local_hour <= 16:
            lighting_band = "natural_daylight"
            reading_suitability = "comfortable"
        else:
            lighting_band = "unlit_indoor"
            reading_suitability = "too_dim"
    elif local_hour is not None and (local_hour >= 19 or local_hour < 6):
        lighting_band = "unlit_indoor"
        reading_suitability = "too_dim"
    elif any(c["cue_id"] == "home_morning_light" for c in visual_cues):
        lighting_band = "natural_daylight"
        reading_suitability = "comfortable"
    else:
        lighting_band = "unknown"
        reading_suitability = "unknown"

    visual = {
        "status": "available" if world_ok else "unavailable",
        "room_light": room_light,
        "local_zone": local_zone or "unknown",
        "local_hour": local_hour,
        "lighting_band": lighting_band,
        "reading_suitability": reading_suitability,
        "visual_cues": visual_cues,
        "visible_is_not_reachable": True,
    }

    # -- 3. OLFACTORY CHANNEL & 4. TACTILE / THERMAL --------------------------------------
    somatic_projection = somatic_view.somatic_block(
        world_result=world_result,
        body_view=body_view,
        state=somatic_state,
        state_reason=somatic_reason,
        now_iso=now_iso,
    )
    body_ctx = somatic_view.body_context(body_view)
    care_derived = somatic_view.derive_care(
        somatic_state,
        now_iso=now_iso,
        place_id=place_id,
        area_id=area_id,
        pose=body_ctx.get("pose"),
    )
    garment_derived = somatic_view.derive_garment(somatic_state, now_iso)

    drink_row = care_derived.get("drink")
    tea_steam_active = False
    if isinstance(drink_row, Mapping) and drink_row.get("kind") == "hot_tea":
        drink_state = drink_row.get("state")
        temp_band = (drink_row.get("temperature") or {}).get("band")
        at_drink_loc = drink_row.get("location_match") is True or drink_state == "taken"
        if drink_state in ("available", "taken") and temp_band in ("warm", "tepid") and at_drink_loc:
            tea_steam_active = True

    hanging_damp_coat = bool(
        at_home
        and garment_derived.get("status") == "available"
        and garment_derived.get("state") == "hang"
        and garment_derived.get("dryness") == "damp"
    )

    scent_enabled = bool(prefs.get("scent_note_enabled", True))
    olfactory_cues: list[dict[str, Any]] = []
    if not scent_enabled:
        olfactory_status = "disabled_by_preference"
    elif not world_ok:
        olfactory_status = "unavailable"
    else:
        olfactory_status = "available"
        if tea_steam_active:
            temp_band = (drink_row.get("temperature") or {}).get("band")
            olfactory_cues.append({
                "cue_id": "tea_steam",
                "summary": "手边热茶还带着温热的茶香蒸汽",
                "source_ref": f"somatic_comfort:drink:hot_tea:{temp_band}",
            })
        if at_home and rain_active and window_state_effective in ("open", "ajar"):
            olfactory_cues.append({
                "cue_id": "rain_breeze",
                "summary": "窗缝带进一点潮润的雨气与微凉空气",
                "source_ref": f"world_ambient:rain+window:{window_state_effective}",
            })
        if hanging_damp_coat and window_state_effective == "closed":
            olfactory_cues.append({
                "cue_id": "indoor_damp_cloth",
                "summary": "挂着的微潮外套在关窗室内留着一点潮闷布料气味",
                "source_ref": "somatic_comfort:garment:damp+window:closed",
            })
        if hanging_damp_coat and window_state_effective in ("open", "ajar"):
            olfactory_cues.append({
                "cue_id": "ventilating_draft",
                "summary": "通风气流正拂过挂起的微潮外套，室内空气保持清爽",
                "source_ref": f"somatic_comfort:garment:damp+window:{window_state_effective}",
            })

    olfactory = {
        "status": olfactory_status,
        "scent_note_enabled": scent_enabled,
        "cues": olfactory_cues,
        "creates_food_inventory": False,
    }

    raw_modalities = somatic_projection.get("modalities") or {}
    tactile_thermal: dict[str, dict[str, Any]] = {}
    for mod_name in ("thermal", "moisture", "posture", "tactile"):
        mod_row = raw_modalities.get(mod_name) or {}
        tactile_thermal[mod_name] = {
            "status": mod_row.get("status", "unknown"),
            "quality": mod_row.get("quality"),
            "salience": mod_row.get("salience", "none"),
            "region": copy.deepcopy(mod_row.get("region") or {}),
        }

    # -- 5. SENSORY LOAD & MICRO-ADJUSTMENTS ----------------------------------------------
    body_signals = body_ctx.get("signals") or []
    body_sensory_elevated = any(
        isinstance(sig, Mapping)
        and (
            str(
                sig.get("signal_type")
                or sig.get("code")
                or sig.get("signal")
                or sig.get("name")
                or ""
            )
            == "sensory_load_elevated"
            or (
                str(sig.get("signal_type") or "") == "sensory_load"
                and str(sig.get("qualitative_band") or sig.get("band") or "")
                in ("mild", "noticeable", "elevated", "high")
            )
        )
        for sig in body_signals
    )
    noise_sens = prefs.get("noise_sensitivity", "moderate")
    light_pref = prefs.get("light_preference", "balanced")
    indoor_stuffy = bool(hanging_damp_coat and window_state_effective == "closed")

    load_points = 0
    if body_sensory_elevated:
        load_points += 2

    if quietness_band == "noisy":
        load_points += 2 if noise_sens == "high" else (1 if noise_sens == "moderate" else 0)
    elif quietness_band == "lively":
        if noise_sens == "high":
            load_points += 2
        elif noise_sens == "moderate" and clarity == "clear":
            load_points += 1
    elif quietness_band == "gentle_background" and noise_sens == "high" and not headphones_filter:
        if clarity == "clear":
            load_points += 1

    if lighting_band == "window_glare":
        load_points += 2 if light_pref == "soft_warm" else 1
    elif reading_suitability == "too_dim" and at_home:
        load_points += 1

    if indoor_stuffy:
        load_points += 1

    if not world_ok:
        load_band = "unknown"
    elif load_points >= 2:
        load_band = "elevated"
    elif load_points == 1:
        load_band = "noticeable"
    else:
        load_band = "calm"

    adjustments: list[dict[str, str]] = []
    if indoor_stuffy:
        adjustments.append({
            "code": "open_window_to_ventilate",
            "label": "推开一点窗户通风，散掉微潮外套的闷气",
        })
    elif (
        at_home
        and window_state_effective == "open"
        and quietness_band in ("lively", "noisy")
        and noise_sens in ("moderate", "high")
    ):
        adjustments.append({
            "code": "ajar_or_close_window",
            "label": "把窗户虚掩或合上，柔化窗外传来的声响",
        })

    if lighting_band == "window_glare":
        adjustments.append({
            "code": "move_from_window_glare",
            "label": "从正午直晒的窗边移到书桌侧，避开刺眼眩光",
        })
    if at_home and reading_suitability == "too_dim":
        adjustments.append({
            "code": "turn_on_lamp_for_reading",
            "label": "点亮暖光落地灯或床头灯再读书",
        })
    if (
        noise_sens == "high"
        and quietness_band in ("lively", "noisy")
        and not headphones_filter
    ):
        adjustments.append({
            "code": "wear_headphones_filter",
            "label": "戴上轻便耳机开启环境滤噪",
        })

    sensory_load = {
        "status": "available" if world_ok else "unavailable",
        "body_signal_elevated": body_sensory_elevated,
        "noise_sensitivity": noise_sens,
        "light_preference": light_pref,
        "indoor_stuffy": indoor_stuffy,
        "load_band": load_band,
        "suggested_adjustments": adjustments,
        "maps_to_emotion_or_pathology": False,
    }

    # -- Sound cards & partner sensory notes projection -----------------------------------
    stored_cards = list(cards_state.get("sound_cards") or [])
    visible_cards: list[dict[str, Any]] = []
    for card in stored_cards:
        if not subject_view and not card.get("shared"):
            continue
        out_card = {
            "card_id": card["card_id"],
            "title": card["title"],
            "place_id": card["place_id"],
            "place_label": card["place_label"],
            "heard_sources": list(card["heard_sources"]),
            "quiet_fallback": bool(card["quiet_fallback"]),
            "impression_note": card.get("impression_note"),
            "window_state_then": card["window_state_then"],
            "medium": "text_sound_card",
            "has_audio_recording": False,
            "shared": bool(card["shared"]),
            "recorded_at": card.get("recorded_at"),
        }
        if subject_view and card.get("operation_key") is not None:
            out_card["operation_key"] = card["operation_key"]
        visible_cards.append(out_card)

    sound_cards_block: dict[str, Any] = {
        "items": visible_cards,
        "visible_count": len(visible_cards),
        "max_cards": MAX_SOUND_CARDS,
        "medium": "text_sound_card",
        "has_audio_recording": False,
    }
    if subject_view:
        sound_cards_block["total_count"] = len(stored_cards)

    notes_list = [
        {
            "note_id": note["note_id"],
            "text": note["text"],
            "created_at": note.get("created_at"),
        }
        for note in (cards_state.get("partner_sensory_notes") or [])
    ]

    return {
        "schema": SENSORY_FIELD_SCHEMA,
        "read_only": True,
        "view": "her" if subject_view else "partner_default",
        "observed_at": now_iso,
        "revision": int(cards_state.get("revision") or 0),
        "cards_state_status": "available" if cards_reason is None else "degraded",
        "cards_state_reason_code": cards_reason,
        "character": {
            "pack_id": pack_doc["pack_id"],
            "display_name": pack_doc["display_name"],
            "pronouns": pack_doc["pronouns"],
            "sensory_preferences": copy.deepcopy(prefs),
            "everyday_accessories": _public_accessories(
                accessories, subject_view=bool(subject_view)
            ),
        },
        "world": {
            "status": "available" if world_ok else "unavailable",
            "reason_code": place_info["reason_code"],
            "place_id": place_id,
            "place_label": place_label,
            "area_id": area_id,
            "home": at_home if world_ok else None,
        },
        "channels": {
            "auditory": auditory,
            "visual": visual,
            "olfactory": olfactory,
            "tactile_thermal": tactile_thermal,
            "sensory_load": sensory_load,
        },
        "sound_cards": sound_cards_block,
        "partner_sensory_notes": {
            "items": notes_list,
            "count": len(notes_list),
            "max_notes": MAX_PARTNER_SENSORY_NOTES,
        },
        "boundaries": {
            "closed_door_or_window_is_absolute_soundproof": False,
            "adjacent_sound_verifies_identity": False,
            "visible_is_not_reachable": True,
            "olfactory_creates_food_inventory": False,
            "sensory_load_maps_to_emotion_or_pathology": False,
            "sound_card_has_audio_recording": False,
        },
        "private_text_included": bool(subject_view),
        "local_only": True,
    }


# ---------------------------------------------------------------------------------------------
# Sensory cards & partner sensory notes mutations
# ---------------------------------------------------------------------------------------------


def apply_capture_sound_card(
    current_cards_state: Mapping[str, Any],
    *,
    sensory_projection: Mapping[str, Any],
    title: Any = None,
    impression_note: Any = None,
    shared: Any = False,
    operation_key: Any = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool, Optional[str]]:
    """Capture one text sound card. Returns ``(new_state, card, already_applied, conflict_code)``."""

    clean_op_key: Optional[str] = None
    if operation_key is not None:
        clean_op_key = _clean_text(
            operation_key,
            min_len=1,
            max_len=MAX_OPERATION_KEY,
            reason_code="OPERATION_KEY_INVALID",
        )

    existing_cards = copy.deepcopy(list(current_cards_state.get("sound_cards") or []))
    if clean_op_key is not None:
        for existing in existing_cards:
            if existing.get("operation_key") == clean_op_key:
                return dict(current_cards_state), copy.deepcopy(existing), True, None

    world_block = sensory_projection.get("world") or {}
    if world_block.get("status") != "available" or not world_block.get("place_id"):
        return dict(current_cards_state), {}, False, "WORLD_UNAVAILABLE"

    if len(existing_cards) >= MAX_SOUND_CARDS:
        return dict(current_cards_state), {}, False, "SOUND_CARD_LIMIT"

    if shared is None:
        clean_shared = False
    elif isinstance(shared, bool):
        clean_shared = shared
    else:
        raise CharacterPackValidationError("SHARED_FLAG_INVALID")

    clean_impression: Optional[str] = None
    if impression_note is not None:
        clean_impression = _clean_multiline_text(
            impression_note,
            min_len=1,
            max_len=MAX_IMPRESSION_NOTE,
            reason_code="IMPRESSION_NOTE_INVALID",
        )

    place_id = str(world_block["place_id"])
    place_label = str(world_block.get("place_label") or place_id)

    if title is None:
        clean_title = f"{place_label}的声音片段"[:MAX_CARD_TITLE]
    else:
        clean_title = _clean_text(
            title,
            min_len=1,
            max_len=MAX_CARD_TITLE,
            reason_code="CARD_TITLE_INVALID",
        )

    auditory = ((sensory_projection.get("channels") or {}).get("auditory") or {})
    heard: list[str] = []
    for row in auditory.get("local_sounds") or ():
        if isinstance(row, Mapping) and row.get("summary"):
            heard.append(str(row["summary"]))
    for row in auditory.get("adjacent_unseen_sounds") or ():
        if isinstance(row, Mapping) and row.get("summary"):
            from_lbl = row.get("from_place_label") or row.get("from_place_id") or "邻处"
            heard.append(f"[{from_lbl}·远处传声] {row['summary']}")

    if heard:
        quiet_fallback = False
    else:
        heard = ["quiet_ambient_room"]
        quiet_fallback = True

    next_rev = int(current_cards_state.get("revision") or 0) + 1
    card_id = _stable_id(
        "scard",
        clean_op_key or f"{now_iso}:{next_rev}:{len(existing_cards)}:{clean_title}",
    )
    new_card: dict[str, Any] = {
        "card_id": card_id,
        "title": clean_title,
        "place_id": place_id,
        "place_label": place_label,
        "heard_sources": heard[:12],
        "quiet_fallback": quiet_fallback,
        "impression_note": clean_impression,
        "window_state_then": str(auditory.get("window_state") or "unknown"),
        "medium": "text_sound_card",
        "has_audio_recording": False,
        "shared": clean_shared,
        "recorded_at": now_iso,
    }
    if clean_op_key is not None:
        new_card["operation_key"] = clean_op_key

    existing_cards.append(new_card)
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": existing_cards,
        "partner_sensory_notes": copy.deepcopy(
            list(current_cards_state.get("partner_sensory_notes") or [])
        ),
    }
    return new_state, copy.deepcopy(new_card), False, None


def apply_set_sound_card_share(
    current_cards_state: Mapping[str, Any],
    *,
    card_id: Any,
    shared: Any,
    now_iso: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]], bool]:
    """Toggle ``shared`` on an existing sound card. Returns ``(new_state, card, changed)``."""

    clean_id = _clean_text(
        card_id, min_len=3, max_len=80, reason_code="CARD_ID_INVALID"
    )
    if not isinstance(shared, bool):
        raise CharacterPackValidationError("SHARED_FLAG_INVALID")

    cards = copy.deepcopy(list(current_cards_state.get("sound_cards") or []))
    target: Optional[dict[str, Any]] = None
    for row in cards:
        if row.get("card_id") == clean_id:
            target = row
            break
    if target is None:
        return dict(current_cards_state), None, False

    if bool(target.get("shared")) == shared:
        return dict(current_cards_state), copy.deepcopy(target), False

    target["shared"] = shared
    next_rev = int(current_cards_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": cards,
        "partner_sensory_notes": copy.deepcopy(
            list(current_cards_state.get("partner_sensory_notes") or [])
        ),
    }
    return new_state, copy.deepcopy(target), True


def apply_remove_sound_card(
    current_cards_state: Mapping[str, Any],
    *,
    card_id: Any,
    now_iso: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    clean_id = _clean_text(
        card_id, min_len=3, max_len=80, reason_code="CARD_ID_INVALID"
    )
    cards = copy.deepcopy(list(current_cards_state.get("sound_cards") or []))
    kept: list[dict[str, Any]] = []
    removed: Optional[dict[str, Any]] = None
    for row in cards:
        if removed is None and row.get("card_id") == clean_id:
            removed = row
        else:
            kept.append(row)
    if removed is None:
        return dict(current_cards_state), None
    next_rev = int(current_cards_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": kept,
        "partner_sensory_notes": copy.deepcopy(
            list(current_cards_state.get("partner_sensory_notes") or [])
        ),
    }
    return new_state, copy.deepcopy(removed)


def apply_leave_sensory_note(
    current_cards_state: Mapping[str, Any],
    *,
    text: Any,
    now_iso: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]], Optional[str]]:
    clean_text = _clean_multiline_text(
        text,
        min_len=1,
        max_len=MAX_PARTNER_SENSORY_NOTE_TEXT,
        reason_code="SENSORY_NOTE_TEXT_INVALID",
    )
    notes = copy.deepcopy(list(current_cards_state.get("partner_sensory_notes") or []))
    if len(notes) >= MAX_PARTNER_SENSORY_NOTES:
        return dict(current_cards_state), None, "PARTNER_SENSORY_NOTE_LIMIT"

    next_rev = int(current_cards_state.get("revision") or 0) + 1
    note_id = _stable_id("snote", now_iso, next_rev, len(notes), clean_text)
    new_note = {
        "note_id": note_id,
        "text": clean_text,
        "created_at": now_iso,
    }
    notes.append(new_note)
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": copy.deepcopy(list(current_cards_state.get("sound_cards") or [])),
        "partner_sensory_notes": notes,
    }
    return new_state, copy.deepcopy(new_note), None


def apply_remove_sensory_note(
    current_cards_state: Mapping[str, Any],
    *,
    note_id: Any,
    now_iso: str,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    clean_id = _clean_text(
        note_id, min_len=3, max_len=80, reason_code="SENSORY_NOTE_ID_INVALID"
    )
    notes = copy.deepcopy(list(current_cards_state.get("partner_sensory_notes") or []))
    kept: list[dict[str, Any]] = []
    removed: Optional[dict[str, Any]] = None
    for row in notes:
        if removed is None and row.get("note_id") == clean_id:
            removed = row
        else:
            kept.append(row)
    if removed is None:
        return dict(current_cards_state), None
    next_rev = int(current_cards_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": copy.deepcopy(list(current_cards_state.get("sound_cards") or [])),
        "partner_sensory_notes": kept,
    }
    return new_state, copy.deepcopy(removed)


def apply_clear_sensory_notes(
    current_cards_state: Mapping[str, Any],
    *,
    now_iso: str,
) -> tuple[dict[str, Any], int, bool]:
    notes = list(current_cards_state.get("partner_sensory_notes") or [])
    if not notes:
        return dict(current_cards_state), 0, False
    next_rev = int(current_cards_state.get("revision") or 0) + 1
    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": next_rev,
        "updated_at": now_iso,
        "sound_cards": copy.deepcopy(list(current_cards_state.get("sound_cards") or [])),
        "partner_sensory_notes": [],
    }
    return new_state, len(notes), True


__all__ = [
    "ACCESSORY_KINDS",
    "ACCESSORY_MODES",
    "ALLOWED_PACK_KEYS",
    "BODY_PRESETS",
    "BUILT_IN_PRESETS",
    "BUILT_IN_PRESET_SUMMARIES",
    "CHARACTER_PACK_FILENAME",
    "CHARACTER_PACK_SCHEMA",
    "DEFAULT_PRESET_ID",
    "DEPLOY_ONLY",
    "FORBIDDEN_PACK_KEYS",
    "LIFE_STAGES",
    "LIGHT_PREFERENCES",
    "MAX_ACCESSORIES",
    "MAX_PARTNER_SENSORY_NOTES",
    "MAX_SOUND_CARDS",
    "NOISE_SENSITIVITIES",
    "OPTIONAL_PROCESSES",
    "PACK_CONTRACT_KIND",
    "PRESET_ORDER",
    "RELATIONS_TO_USER",
    "SENSORY_CARDS_FILENAME",
    "SENSORY_FIELD_SCHEMA",
    "USER_ONLY",
    "CharacterPackValidationError",
    "apply_capture_sound_card",
    "apply_character_pack_action",
    "apply_clear_sensory_notes",
    "apply_custom_pack",
    "apply_leave_sensory_note",
    "apply_remove_sensory_note",
    "apply_remove_sound_card",
    "apply_select_preset",
    "apply_set_optional_physiology",
    "apply_set_sound_card_share",
    "apply_update_identity",
    "available_presets_list",
    "character_pack_state_path",
    "collect_ambient_events_by_place",
    "compute_pack_ref",
    "default_character_pack_state",
    "derive_sensory_field",
    "empty_sensory_cards_state",
    "evaluate_adult_gate_preview",
    "import_pack_json",
    "project_character_pack",
    "read_character_pack_state",
    "read_sensory_cards_state",
    "resolve_adjacent_places",
    "seed_character_pack_siblings",
    "select_preset",
    "sensory_cards_state_path",
    "validate_accessories",
    "validate_and_normalize_pack",
    "validate_optional_processes",
    "validate_sensory_preferences",
    "write_character_pack_state",
    "write_sensory_cards_state",
]
