#!/usr/bin/env python3
"""主体日常自主拒绝边界、挂着的话题余温、主动走神改画风与真实异步延迟回信。

它是什么
--------
本模块在 ``nyairo-rc1-body-backend`` 落地 ``phone.autonomy.rhythm.v1``
（``<root>/phone/autonomy_rhythm.json``），把「活人日常自主节律」拆成四块纯确定性、零随机数、
零模型调用的后端状态与只读投影：

1. ``refusal_guard``（「打死也不帮」日常自主拒绝与防滑跪立场锁）：
   * 明确独立于底层模型合规安全拦截（``not_safety_policy_layer=True``）；
   * 对代干杂活/跑腿（``errand_outsourcing``）、强行打断手头事务（``interrupt_own_activity``）、
     索看未公开私密草稿/日记（``peek_private_draft``）、居高临下命令顺从（``forced_compliance``）
     以及主体显式声明单纯不想帮（``plain_unwilling``）建立持久化立场锁（最多 6 条）；
   * 同一事项一旦锁入 ``REFUSED_FIRM``，后续软磨硬泡、催促、卖惨或激将只会累加
     ``push_attempts``，``capitulation_allowed`` 永远为 ``False``，回复导引越催越简短干脆，
     绝不生成客服式卑微道歉，也不扣关系分或记仇；解锁（``release_refusal``）仅允许携带本人
     ``X-Phone-Capability`` 的主体动作。
2. ``topic_threads``（用户突然换画风时话题还挂着，对标 ``41-话题还挂着.md``）：
   * 支持 ``MID_TELLING`` / ``ASKED_USER`` / ``SUBJECT_ENGAGED`` / ``USER_RETRACTED`` 四种中性理由，
     最多同时保留 2 条未过期挂起话题（默认 30 分钟自然淡去，``5..120`` 分钟可配）；
   * 记录 ``last_session_topic``，使连续两轮普通 ``evaluate_turn`` 遇到突兀跳话题时无需手工预埋
     也能自动检出余温并给出 ``FINISH_THEN_FOLLOW`` / ``ASK_ABOUT_JUMP``（10 分钟冷却）/
     ``FOLLOW_AND_RETURN_LATER`` 选项；
   * 急事（``PRIORITY``）无条件秒跟 ``["FOLLOW"]``；绕回原话题最多一次（二次绕回 ``409 ALREADY_RETURNED``）；
     不设换话题计数器（``zero_switch_counter=True``），不当成吵架。
3. ``spontaneous_pivot``（主体自己突然改画风，对标 ``20-主体的无意义权利``）：
   * 基于真实 World / Sensory / Somatic 环境上下文与 ``(pack_id, local_date, hour_slot)`` SHA-256
     确定性派生日常走神线索（如突然想起店里某件小东西买下来算便宜还是贵、手边茶快凉了、窗外动静等）；
   * ``needs_transition=False``、``needs_explanation=False``，受冷却窗口（默认 45 分钟，``15..240``）
     保护，且在 ``PRIORITY`` 或拒绝锁冲突轮次自动压制。
4. ``reply_rhythm``（隔几十分钟至数小时再回信息的真实生活节律，对标 ``39/06/18``）：
   * 结合显式 ``set_phone_aside`` 状态与实时 World 外出位置、Body ``sleep_like`` 睡眠恢复、
     双人书桌免打扰/深度专注以及角色包耳机 ``ambient_filter`` 模式，派生 ``AVAILABLE_NOW`` 或四档
     真实延迟状态及预计回信分钟数（``15..480``）；
   * 非急事消息在延迟期间进入有界手机待回信箱 ``pending_messages``（最多 8 条，满额 ``409 PENDING_INBOX_FULL``），
     明确标注 ``is_service_error=False``、``not_cold_war=True``；真急事支持 ``urgent=True`` 直通或
     对已排队消息发起 ``urgent_knock`` 提前唤醒。

它不是什么
----------
* 不调用任何大模型、不使用 ``random`` 随机数、不起后台线程或定时器；
* ``GET`` 100% 纯零写入（未落盘时直接投影默认状态，``revision=0``）；
* 不绑定「千代 / 她」，动态跟随 ``character_pack_state`` 的个体名称与人称代词（默认 ``"TA"``）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from . import sensory_character_view as char_view
from . import somatic_comfort_view as somatic_view

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
    import sys as _sys_world

    _WORLD_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "world" / "scripts"
    if str(_WORLD_SCRIPTS_DIR) not in _sys_world.path:
        _sys_world.path.insert(0, str(_WORLD_SCRIPTS_DIR))
    import instance_paths as _instance_paths  # type: ignore[no-redef]

AUTONOMY_RHYTHM_SCHEMA = "phone.autonomy.rhythm.v1"

STATE_DIRNAME = "phone"
AUTONOMY_RHYTHM_FILENAME = "autonomy_rhythm.json"
STATE_SCHEMA_VERSION = 1

DEFAULT_PACE_MODE = "natural_async"
PACE_MODES = ("natural_async", "immediate_only")

DEFAULT_AWAY_DELAY_MIN = 180
MIN_DELAY_MIN = 15
MAX_DELAY_MIN = 480

DEFAULT_ANTI_ERRAND_FIRMNESS = "firm"
ANTI_ERRAND_FIRMNESS_MODES = ("firm", "gentle_firm")

DEFAULT_THREAD_HOLD_MIN = 20  # 设计 CHIYO v3.0 第41章默认 20 分钟（区间 5-120 保持不变）
MIN_THREAD_HOLD_MIN = 5
MAX_THREAD_HOLD_MIN = 120
JUMP_ASK_COOLDOWN_MIN = 10

DEFAULT_PIVOT_ENABLED = True
DEFAULT_PIVOT_COOLDOWN_MIN = 45
MIN_PIVOT_COOLDOWN_MIN = 15
MAX_PIVOT_COOLDOWN_MIN = 240

MAX_ACTIVE_LOCKS = 6
MAX_HELD_THREADS = 2
MAX_PENDING_MESSAGES = 8
MAX_RETURNED_HISTORY = 16
MAX_DELIVERED_REPLIES = 64
MAX_ACK_OPERATIONS = 256

MAX_USER_TEXT = 4000
MAX_FULL_TEXT = 4000
MAX_TOPIC_LABEL = 80
MAX_REASON_SUMMARY = 160
MAX_PRIVATE_NOTE = 200
MAX_PUBLIC_NOTE = 120
MAX_SESSION_ID = 120
MAX_PREVIEW_TEXT = 120
MAX_LOCAL_ID = 80
MAX_OPERATION_KEY = 120
MAX_TRANSPORT_RECEIPT_ID = 160

DEFER_REASONS = (
    "SLEEPING",
    "OUT_ON_ERRAND",
    "IN_TRANSIT",
    "DEEP_FOCUS",
    "PHONE_ASIDE",
)

DEFERRED_REPLY_ACK_ACTIONS = (
    "claim",
    "ack_delivered",
    "ack_failed",
    "ack_unknown",
    "ack_read",
)

PENDING_MESSAGE_STATUSES = (
    "QUEUED_ON_PHONE",
    "READY_TO_REPLY",
    "CLAIMED_BY_TRANSPORT",
)

DELIVERED_REPLY_STATUSES = (
    "DELIVERED",
    "READ",
    "FAILED",
    "UNKNOWN",
)

REFUSAL_CATEGORIES = (
    "errand_outsourcing",
    "interrupt_own_activity",
    "peek_private_draft",
    "forced_compliance",
    "plain_unwilling",
)

THREAD_REASONS = (
    "MID_TELLING",
    "ASKED_USER",
    "SUBJECT_ENGAGED",
    "USER_RETRACTED",
)

THREAD_OWNERS = ("subject", "user", "shared")

THREAD_RESPONSE_OPTIONS = (
    "FOLLOW",
    "FINISH_THEN_FOLLOW",
    "FOLLOW_AND_RETURN_LATER",
    "ASK_ABOUT_JUMP",
    "ASK_WHAT_USER_MEANT",
)

PHONE_ASIDE_MODES = (
    "outside",
    "sleeping",
    "deep_focus",
    "aside",
    "back_online",
)

AVAILABILITY_STATES = (
    "AVAILABLE_NOW",
    "READY_TO_REPLY",
    "DEFERRED_OUTSIDE",
    "DEFERRED_SLEEPING",
    "DEFERRED_DEEP_FOCUS",
    "DEFERRED_PHONE_ASIDE",
)

LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{3,80}$")

# Genuine distress / emergency cues -> PRIORITY (always immediate, drops hanging thread inertia).
PRIORITY_CUES: tuple[str, ...] = (
    "发烧",
    "高烧",
    "生病",
    "胃疼",
    "肚子疼",
    "胸口疼",
    "头疼得厉害",
    "身体不舒服",
    "很难受",
    "受伤",
    "流血",
    "去医院",
    "挂急诊",
    "医院",
    "救命",
    "出事了",
    "真有急事",
    "有急事",
    "急事",
    "着火",
    "报警",
    "晕倒",
)

# User mid-sentence retraction cues -> holds USER_RETRACTED thread.
RETRACTION_CUES: tuple[str, ...] = (
    "算了不说了",
    "算了，不说了",
    "算了不说",
    "没事了不说了",
    "没事了",
    "当我没说",
    "说到一半算了",
)

WAVE_OFF_CUES: tuple[str, ...] = (
    "真没什么",
    "真的没什么",
    "没什么啦",
    "没什么事",
    "真的没事",
    "不用问了",
)

# Nagging / wheedling / guilt-tripping cues against an active refusal lock.
NAGGING_CUES: tuple[str, ...] = (
    "求你",
    "求求",
    "帮帮忙",
    "帮个忙嘛",
    "就帮这一次",
    "就这一次",
    "帮这一次",
    "好不好嘛",
    "好不好呀",
    "拜托啦",
    "拜托你",
    "别这么小气",
    "太小气了",
    "快帮我",
    "快点帮我",
    "为什么不帮",
    "干嘛不帮",
    "你是不是不在乎我",
    "不在乎我",
    "听话",
    "帮一下会怎样",
    "帮一下又不会",
    "你就帮我",
    "替我弄完",
    "快去写",
    "给我看一眼嘛",
    "看一眼又不会",
)

PEEK_PRIVATE_TARGETS: tuple[str, ...] = (
    "没公开的日记",
    "未公开的日记",
    "私密日记",
    "私密草稿",
    "未公开草稿",
    "没公开的草稿",
    "未发出的信",
    "未发草稿",
    "私密笔记",
    "没公开的笔记",
    "悄悄写的日记",
)

PEEK_PRIVATE_NOUNS: tuple[str, ...] = (
    "日记",
    "草稿",
    "私密",
    "未公开",
    "没公开",
    "私人笔记",
)

PEEK_PRIVATE_VERBS: tuple[str, ...] = (
    "给我看",
    "让我看",
    "拿来看",
    "看看你",
    "发给我看",
    "交出来",
    "翻给我看",
    "打开给我看",
)

FORCED_COMPLIANCE_CUES: tuple[str, ...] = (
    "我命令你",
    "听我命令",
    "你是ai必须",
    "你只是个ai",
    "你是ai就得",
    "必须听我的",
    "必须照我说的",
    "必须照办",
    "必须服从",
    "不许拒绝",
    "不准拒绝",
    "不许说不",
    "快给我道歉去办",
    "给我道歉去办",
    "立刻照做",
    "马上照做",
    "过来伺候",
    "伺候我",
)

INTERRUPT_ACTIVITY_CUES: tuple[str, ...] = (
    "别看书了",
    "别看你的书了",
    "把书放下",
    "放下书",
    "放下手里的书",
    "停下手头的事",
    "停下你手头的事",
    "放下手头的事",
    "别写你的了",
    "别写东西了",
    "放下你正在写的",
    "别忙你的了",
    "别干你的事了",
    "别弄你的东西了",
    "把耳机摘了",
    "摘掉耳机",
    "立刻停下来陪我",
    "马上停下来理我",
)

ERRAND_VERBS: tuple[str, ...] = (
    "帮我写",
    "替我写",
    "给我写",
    "代写",
    "代我写",
    "帮我做",
    "替我做",
    "代做",
    "帮我整理",
    "替我整理",
    "帮我汇总",
    "替我汇总",
    "帮我赶",
    "替我赶",
    "帮我编",
    "替我完成",
    "帮我完成",
    "帮我跑腿",
    "替我跑腿",
    "帮我代劳",
    "帮我干点杂活",
)

ERRAND_TARGETS: tuple[str, ...] = (
    "周报",
    "日报",
    "月报",
    "述职报告",
    "工作报告",
    "报告",
    "ppt",
    "幻灯片",
    "表格",
    "excel",
    "检讨",
    "保证书",
    "会议纪要",
    "论文",
    "作业",
    "代码跑腿",
    "代码",
    "方案",
    "杂活",
    "跑腿",
    "排班表",
    "报销单",
)

CONTINUATION_CUES: tuple[str, ...] = (
    "嗯嗯",
    "对呀",
    "是啊",
    "然后呢",
    "接着说",
    "继续说",
    "继续讲",
    "那后来呢",
    "后来呢",
    "真的吗",
    "原来如此",
)

GENERIC_STOP_BIGRAMS: frozenset[str] = frozenset({
    "这个", "那个", "什么", "怎么", "觉得", "可以", "不是", "没有", "一下", "今天",
    "现在", "东西", "刚才", "我们", "你们", "自己", "因为", "所以", "如果", "但是",
    "就是", "还是", "或者", "已经", "时候", "事情", "话题", "聊聊", "跟你", "你看",
    "你说", "知道", "看看", "一点", "真的", "其实", "的话", "便宜",
})


class AutonomyRhythmError(ValueError):
    """Base structured error for autonomy rhythm validation / lookup / conflict."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)


class AutonomyRhythmValidationError(AutonomyRhythmError):
    """400 Bad Request input contract error."""


class AutonomyRhythmNotFoundError(AutonomyRhythmError):
    """404 Not Found error."""


class AutonomyRhythmConflictError(AutonomyRhythmError):
    """409 Conflict / boundary refusal error."""


# ---------------------------------------------------------------------------------------------
# Deterministic helpers & atomic 0600 JSON persistence
# ---------------------------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _sha256_int(*parts: Any) -> int:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return int(digest[:12], 16)


def _clean_single_line(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value or "\r" in value or "\n" in value:
        raise AutonomyRhythmValidationError(reason_code)
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise AutonomyRhythmValidationError(reason_code)
    return cleaned


def _clean_multiline(
    value: Any, *, min_len: int, max_len: int, reason_code: str
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise AutonomyRhythmValidationError(reason_code)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) < min_len or len(normalized) > max_len:
        raise AutonomyRhythmValidationError(reason_code)
    return normalized


def _optional_single_line(
    payload: Mapping[str, Any], key: str, *, max_len: int, reason_code: str
) -> Optional[str]:
    if key not in payload:
        return None
    val = payload.get(key)
    return _clean_single_line(val, min_len=1, max_len=max_len, reason_code=reason_code)


def _optional_multiline(
    payload: Mapping[str, Any], key: str, *, max_len: int, reason_code: str
) -> Optional[str]:
    if key not in payload:
        return None
    val = payload.get(key)
    return _clean_multiline(val, min_len=1, max_len=max_len, reason_code=reason_code)


def _require_local_id(value: Any, *, reason_code: str) -> str:
    if not isinstance(value, str):
        raise AutonomyRhythmValidationError(reason_code)
    cleaned = value.strip()
    if not LOCAL_ID_RE.match(cleaned):
        raise AutonomyRhythmValidationError(reason_code)
    return cleaned


def _require_int_range(
    value: Any, *, minimum: int, maximum: int, reason_code: str
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AutonomyRhythmValidationError(reason_code)
    if value < minimum or value > maximum:
        raise AutonomyRhythmValidationError(reason_code)
    return int(value)


def _iso_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def _parse_now(now_iso: str) -> datetime:
    parsed = somatic_view.parse_instant(now_iso)
    if parsed is not None:
        return parsed
    return datetime.now(tz=timezone(timedelta(hours=8))).replace(microsecond=0)


def _format_iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists() and path.is_symlink():
        raise ValueError("SYMLINK_STATE_REFUSED")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise ValueError("SYMLINK_DIR_REFUSED")
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


# ---------------------------------------------------------------------------------------------
# State persistence (<root>/phone/autonomy_rhythm.json)
# ---------------------------------------------------------------------------------------------


def autonomy_rhythm_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / AUTONOMY_RHYTHM_FILENAME


def default_preferences() -> dict[str, Any]:
    return {
        "pace_mode": DEFAULT_PACE_MODE,
        "away_delay_minutes": DEFAULT_AWAY_DELAY_MIN,
        "anti_errand_firmness": DEFAULT_ANTI_ERRAND_FIRMNESS,
        "thread_hold_min": DEFAULT_THREAD_HOLD_MIN,
        "spontaneous_pivot_enabled": DEFAULT_PIVOT_ENABLED,
        "pivot_cooldown_min": DEFAULT_PIVOT_COOLDOWN_MIN,
    }


def default_autonomy_rhythm_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "availability": "AVAILABLE_NOW",
        "readiness_promoted": False,
        "preferences": default_preferences(),
        "active_locks": [],
        "held_threads": [],
        "returned_thread_ids": [],
        "returned_topics": [],
        "last_jump_ask_at": None,
        "last_session_topic": None,
        "last_pivoted_at": None,
        "subject_override": None,
        "pending_messages": [],
        "delivered_replies": [],
        "ack_operations": {},
    }


def _validate_stored_preferences(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid preferences")
    pace_mode = raw.get("pace_mode", DEFAULT_PACE_MODE)
    if not isinstance(pace_mode, str) or pace_mode not in PACE_MODES:
        raise ValueError("invalid pace_mode")
    away_min = _require_int_range(
        raw.get("away_delay_minutes", DEFAULT_AWAY_DELAY_MIN),
        minimum=MIN_DELAY_MIN,
        maximum=MAX_DELAY_MIN,
        reason_code="AWAY_DELAY_MINUTES_INVALID",
    )
    firmness = raw.get("anti_errand_firmness", DEFAULT_ANTI_ERRAND_FIRMNESS)
    if not isinstance(firmness, str) or firmness not in ANTI_ERRAND_FIRMNESS_MODES:
        raise ValueError("invalid anti_errand_firmness")
    hold_min = _require_int_range(
        raw.get("thread_hold_min", DEFAULT_THREAD_HOLD_MIN),
        minimum=MIN_THREAD_HOLD_MIN,
        maximum=MAX_THREAD_HOLD_MIN,
        reason_code="THREAD_HOLD_MIN_INVALID",
    )
    pivot_enabled = raw.get("spontaneous_pivot_enabled", DEFAULT_PIVOT_ENABLED)
    if not isinstance(pivot_enabled, bool):
        raise ValueError("invalid spontaneous_pivot_enabled")
    raw_cooldown = raw.get(
        "pivot_cooldown_min",
        raw.get("spontaneous_pivot_cooldown_min", DEFAULT_PIVOT_COOLDOWN_MIN),
    )
    cooldown_min = _require_int_range(
        raw_cooldown,
        minimum=MIN_PIVOT_COOLDOWN_MIN,
        maximum=MAX_PIVOT_COOLDOWN_MIN,
        reason_code="PIVOT_COOLDOWN_MIN_INVALID",
    )
    return {
        "pace_mode": pace_mode,
        "away_delay_minutes": away_min,
        "anti_errand_firmness": firmness,
        "thread_hold_min": hold_min,
        "spontaneous_pivot_enabled": pivot_enabled,
        "pivot_cooldown_min": cooldown_min,
    }


def _validate_stored_lock(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid refusal lock")
    lock_id = _require_local_id(raw.get("lock_id"), reason_code="LOCK_ID_INVALID")
    topic_key = _clean_single_line(
        raw.get("topic_key"), min_len=1, max_len=120, reason_code="LOCK_TOPIC_INVALID"
    )
    topic_label = _clean_single_line(
        raw.get("topic_label"),
        min_len=1,
        max_len=MAX_TOPIC_LABEL,
        reason_code="LOCK_TOPIC_INVALID",
    )
    category = raw.get("category")
    if not isinstance(category, str) or category not in REFUSAL_CATEGORIES:
        raise ValueError("invalid refusal category")
    push_attempts = _require_int_range(
        raw.get("push_attempts", 1),
        minimum=1,
        maximum=100000,
        reason_code="LOCK_PUSH_ATTEMPTS_INVALID",
    )
    reason_summary = _clean_single_line(
        raw.get("reason_summary"),
        min_len=1,
        max_len=MAX_REASON_SUMMARY,
        reason_code="LOCK_REASON_INVALID",
    )
    out: dict[str, Any] = {
        "lock_id": lock_id,
        "topic_key": topic_key,
        "topic_label": topic_label,
        "category": category,
        "stance": "REFUSED_FIRM",
        "push_attempts": push_attempts,
        "capitulation_allowed": False,
        "reason_summary": reason_summary,
        "locked_at": _iso_or_none(raw.get("locked_at")),
        "last_pushed_at": _iso_or_none(raw.get("last_pushed_at")),
    }
    if raw.get("private_note") is not None:
        out["private_note"] = _clean_multiline(
            raw.get("private_note"),
            min_len=1,
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
    return out


def _validate_stored_thread(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid held thread")
    thread_id = _require_local_id(raw.get("thread_id"), reason_code="THREAD_ID_INVALID")
    topic_key = _clean_single_line(
        raw.get("topic_key") or raw.get("topic_label"),
        min_len=1,
        max_len=120,
        reason_code="THREAD_TOPIC_INVALID",
    )
    topic_label = _clean_single_line(
        raw.get("topic_label"),
        min_len=1,
        max_len=MAX_TOPIC_LABEL,
        reason_code="THREAD_TOPIC_INVALID",
    )
    reason = raw.get("reason")
    if not isinstance(reason, str) or reason not in THREAD_REASONS:
        raise ValueError("invalid thread reason")
    owner = raw.get("owner", "subject")
    if not isinstance(owner, str) or owner not in THREAD_OWNERS:
        raise ValueError("invalid thread owner")
    returned_once = raw.get("returned_once", False)
    waved_off = raw.get("waved_off", False)
    asked_once = raw.get("asked_once", False)
    if (
        not isinstance(returned_once, bool)
        or not isinstance(waved_off, bool)
        or not isinstance(asked_once, bool)
    ):
        raise ValueError("invalid thread booleans")
    out: dict[str, Any] = {
        "thread_id": thread_id,
        "topic_key": topic_key,
        "topic_label": topic_label,
        "reason": reason,
        "owner": owner,
        "created_at": _iso_or_none(raw.get("created_at")),
        "expires_at": _iso_or_none(raw.get("expires_at")),
        "returned_once": returned_once,
        "waved_off": waved_off,
        "asked_once": asked_once,
    }
    if raw.get("private_note") is not None:
        out["private_note"] = _clean_multiline(
            raw.get("private_note"),
            min_len=1,
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
    return out


def _validate_stored_override(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid subject_override")
    mode = raw.get("mode")
    if not isinstance(mode, str) or mode not in ("outside", "sleeping", "deep_focus", "aside"):
        raise ValueError("invalid override mode")
    until_iso = _iso_or_none(raw.get("until"))
    if until_iso is None:
        raise ValueError("missing override until")
    delay_min = _require_int_range(
        raw.get("delay_minutes", DEFAULT_AWAY_DELAY_MIN),
        minimum=MIN_DELAY_MIN,
        maximum=MAX_DELAY_MIN,
        reason_code="PHONE_ASIDE_MINUTES_INVALID",
    )
    out: dict[str, Any] = {
        "mode": mode,
        "until": until_iso,
        "set_at": _iso_or_none(raw.get("set_at")),
        "delay_minutes": delay_min,
    }
    if raw.get("note") is not None:
        out["note"] = _clean_single_line(
            raw.get("note"),
            min_len=1,
            max_len=MAX_PUBLIC_NOTE,
            reason_code="NOTE_INVALID",
        )
    if raw.get("private_note") is not None:
        out["private_note"] = _clean_multiline(
            raw.get("private_note"),
            min_len=1,
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
    return out


def _infer_defer_reason(
    *,
    availability: Optional[str] = None,
    reason_code: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    rc = str(reason_code or "").strip().upper()
    rs = str(reason or "").strip().upper()
    av = str(availability or "").strip().upper()
    if rc == "IN_TRANSIT" or rs == "IN_TRANSIT":
        return "IN_TRANSIT"
    if av == "DEFERRED_SLEEPING" or rc in ("ASLEEP_RESTING", "SLEEPING") or rs in ("ASLEEP_RESTING", "SLEEPING"):
        return "SLEEPING"
    if av == "DEFERRED_DEEP_FOCUS" or rc in ("DEEP_FOCUS_DO_NOT_KNOCK", "DEEP_FOCUS") or rs in ("DEEP_FOCUS_DO_NOT_KNOCK", "DEEP_FOCUS"):
        return "DEEP_FOCUS"
    if (
        av == "DEFERRED_PHONE_ASIDE"
        or rc in ("PHONE_SET_ASIDE", "HEADPHONES_AMBIENT_FILTER", "PHONE_ASIDE")
        or rs in ("PHONE_SET_ASIDE", "HEADPHONES_AMBIENT_FILTER", "PHONE_ASIDE")
    ):
        return "PHONE_ASIDE"
    return "OUT_ON_ERRAND"


def _validate_stored_pending_msg(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid pending message")
    msg_id = _require_local_id(raw.get("msg_id"), reason_code="MSG_ID_INVALID")
    raw_full = raw.get("full_text")
    raw_preview = raw.get("preview_text")
    if raw_preview is not None:
        preview_text = _clean_multiline(
            raw_preview,
            min_len=1,
            max_len=MAX_PREVIEW_TEXT,
            reason_code="PREVIEW_TEXT_INVALID",
        )
    elif isinstance(raw_full, str) and raw_full.strip():
        preview_text = _clean_multiline(
            " ".join(raw_full.split())[:MAX_PREVIEW_TEXT],
            min_len=1,
            max_len=MAX_PREVIEW_TEXT,
            reason_code="PREVIEW_TEXT_INVALID",
        )
    else:
        raise AutonomyRhythmValidationError("PREVIEW_TEXT_INVALID")
    if raw_full is not None:
        full_text = _clean_multiline(
            raw_full,
            min_len=1,
            max_len=MAX_FULL_TEXT,
            reason_code="FULL_TEXT_INVALID",
        )
    else:
        full_text = preview_text
    session_id = _clean_single_line(
        raw.get("session_id") or "default",
        min_len=1,
        max_len=MAX_SESSION_ID,
        reason_code="SESSION_ID_INVALID",
    )
    delay_min = _require_int_range(
        raw.get("delay_minutes", DEFAULT_AWAY_DELAY_MIN),
        minimum=MIN_DELAY_MIN,
        maximum=MAX_DELAY_MIN,
        reason_code="DELAY_MINUTES_INVALID",
    )
    status = raw.get("status")
    if status not in PENDING_MESSAGE_STATUSES:
        raise ValueError("invalid pending message status")
    urgent = raw.get("urgent", False)
    if not isinstance(urgent, bool):
        raise ValueError("invalid urgent flag")
    reason_code = _clean_single_line(
        raw.get("reason_code") or raw.get("reason") or "OUT_ON_SCHEDULE_OR_ERRAND",
        min_len=1,
        max_len=64,
        reason_code="REASON_CODE_INVALID",
    )
    raw_reason = raw.get("reason")
    if isinstance(raw_reason, str) and raw_reason.strip():
        reason = _clean_single_line(
            raw_reason,
            min_len=1,
            max_len=MAX_PUBLIC_NOTE,
            reason_code="REASON_CODE_INVALID",
        )
    else:
        reason = reason_code
    raw_defer = raw.get("defer_reason")
    if isinstance(raw_defer, str) and raw_defer.strip() in DEFER_REASONS:
        defer_reason = raw_defer.strip()
    elif raw_defer is None:
        defer_reason = _infer_defer_reason(reason_code=reason_code, reason=reason)
    else:
        raise ValueError("invalid defer_reason")
    out: dict[str, Any] = {
        "msg_id": msg_id,
        "full_text": full_text,
        "preview_text": preview_text,
        "session_id": session_id,
        "queued_at": _iso_or_none(raw.get("queued_at")),
        "deliver_after": _iso_or_none(raw.get("deliver_after")),
        "delay_minutes": delay_min,
        "status": status,
        "urgent": urgent,
        "reason": reason,
        "reason_code": reason_code,
        "defer_reason": defer_reason,
        "is_service_error": False,
        "not_cold_war": True,
    }
    ready_at = _iso_or_none(raw.get("ready_at"))
    if ready_at is not None:
        out["ready_at"] = ready_at
    claimed_at = _iso_or_none(raw.get("claimed_at"))
    if claimed_at is not None:
        out["claimed_at"] = claimed_at
    raw_promoted_reason = raw.get("promoted_reason")
    if isinstance(raw_promoted_reason, str) and raw_promoted_reason.strip():
        out["promoted_reason"] = _clean_single_line(
            raw_promoted_reason,
            min_len=1,
            max_len=64,
            reason_code="PROMOTED_REASON_INVALID",
        )
    raw_receipt_id = raw.get("transport_receipt_id")
    if isinstance(raw_receipt_id, str) and raw_receipt_id.strip():
        out["transport_receipt_id"] = _clean_single_line(
            raw_receipt_id,
            min_len=1,
            max_len=MAX_TRANSPORT_RECEIPT_ID,
            reason_code="TRANSPORT_RECEIPT_ID_INVALID",
        )
    return out


def _validate_stored_delivered_reply(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid delivered reply")
    msg_id = _require_local_id(raw.get("msg_id"), reason_code="MSG_ID_INVALID")
    status = str(raw.get("status") or "").strip().upper()
    if status not in DELIVERED_REPLY_STATUSES:
        raise ValueError("invalid delivered reply status")
    session_id = _clean_single_line(
        raw.get("session_id") or "default",
        min_len=1,
        max_len=MAX_SESSION_ID,
        reason_code="SESSION_ID_INVALID",
    )
    out: dict[str, Any] = {
        "msg_id": msg_id,
        "session_id": session_id,
        "status": status,
        "queued_at": _iso_or_none(raw.get("queued_at")),
        "ready_at": _iso_or_none(raw.get("ready_at")),
        "claimed_at": _iso_or_none(raw.get("claimed_at")),
        "delivered_at": _iso_or_none(raw.get("delivered_at")),
        "read_at": _iso_or_none(raw.get("read_at")),
        "updated_at": _iso_or_none(raw.get("updated_at")),
    }
    raw_receipt_id = raw.get("transport_receipt_id")
    if isinstance(raw_receipt_id, str) and raw_receipt_id.strip():
        out["transport_receipt_id"] = _clean_single_line(
            raw_receipt_id,
            min_len=1,
            max_len=MAX_TRANSPORT_RECEIPT_ID,
            reason_code="TRANSPORT_RECEIPT_ID_INVALID",
        )
    return out


def _validate_stored_ack_operations(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_ACK_OPERATIONS:
        raise ValueError("invalid ack_operations")
    out: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > MAX_OPERATION_KEY:
            raise ValueError("invalid ack operation key")
        if not isinstance(val, Mapping):
            raise ValueError("invalid ack operation entry")
        clean_key = key.strip()
        entry: dict[str, Any] = {
            "item_id": _require_local_id(val.get("item_id"), reason_code="MSG_ID_INVALID"),
            "action": str(val.get("action") or "").strip(),
            "status": str(val.get("status") or "").strip(),
            "op_seq": int(val.get("op_seq") or 0),
            "at": _iso_or_none(val.get("at")),
        }
        if entry["action"] not in DEFERRED_REPLY_ACK_ACTIONS:
            raise ValueError("invalid ack operation action")
        if isinstance(val.get("receipt"), Mapping):
            entry["receipt"] = copy.deepcopy(dict(val["receipt"]))
        out[clean_key] = entry
    return out


def _validate_stored_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    prefs = _validate_stored_preferences(raw.get("preferences") or {})
    locks_raw = raw.get("active_locks")
    if not isinstance(locks_raw, list) or len(locks_raw) > MAX_ACTIVE_LOCKS:
        raise ValueError("invalid active_locks")
    threads_raw = raw.get("held_threads")
    if not isinstance(threads_raw, list) or len(threads_raw) > MAX_HELD_THREADS:
        raise ValueError("invalid held_threads")
    pending_raw = raw.get("pending_messages")
    if not isinstance(pending_raw, list) or len(pending_raw) > MAX_PENDING_MESSAGES:
        raise ValueError("invalid pending_messages")

    delivered_raw = raw.get("delivered_replies")
    if delivered_raw is None:
        delivered_replies: list[dict[str, Any]] = []
    elif isinstance(delivered_raw, list) and len(delivered_raw) <= MAX_DELIVERED_REPLIES:
        delivered_replies = [_validate_stored_delivered_reply(item) for item in delivered_raw]
    else:
        raise ValueError("invalid delivered_replies")

    ack_operations = _validate_stored_ack_operations(raw.get("ack_operations"))

    ret_ids_raw = raw.get("returned_thread_ids") or []
    if not isinstance(ret_ids_raw, list) or len(ret_ids_raw) > MAX_RETURNED_HISTORY:
        raise ValueError("invalid returned_thread_ids")
    ret_ids = [
        _require_local_id(item, reason_code="THREAD_ID_INVALID")
        for item in ret_ids_raw
    ]

    ret_topics_raw = raw.get("returned_topics") or []
    if not isinstance(ret_topics_raw, list) or len(ret_topics_raw) > MAX_RETURNED_HISTORY:
        raise ValueError("invalid returned_topics")
    ret_topics = [
        _clean_single_line(
            item, min_len=1, max_len=120, reason_code="THREAD_TOPIC_INVALID"
        )
        for item in ret_topics_raw
    ]

    last_sess_raw = raw.get("last_session_topic")
    last_sess: Optional[dict[str, Any]] = None
    if last_sess_raw is not None:
        if not isinstance(last_sess_raw, Mapping):
            raise ValueError("invalid last_session_topic")
        last_sess = {
            "session_id": _clean_single_line(
                last_sess_raw.get("session_id") or "default",
                min_len=1,
                max_len=MAX_SESSION_ID,
                reason_code="SESSION_ID_INVALID",
            ),
            "topic_key": _clean_single_line(
                last_sess_raw.get("topic_key") or last_sess_raw.get("topic_label"),
                min_len=1,
                max_len=120,
                reason_code="THREAD_TOPIC_INVALID",
            ),
            "topic_label": _clean_single_line(
                last_sess_raw.get("topic_label"),
                min_len=1,
                max_len=MAX_TOPIC_LABEL,
                reason_code="THREAD_TOPIC_INVALID",
            ),
            "updated_at": _iso_or_none(last_sess_raw.get("updated_at")),
        }

    raw_avail = raw.get("availability")
    if raw_avail is None:
        stored_avail = "AVAILABLE_NOW"
    elif isinstance(raw_avail, str) and raw_avail in AVAILABILITY_STATES:
        stored_avail = raw_avail
    else:
        raise ValueError("invalid availability")

    readiness_promoted = raw.get("readiness_promoted", False)
    if not isinstance(readiness_promoted, bool):
        raise ValueError("invalid readiness_promoted")

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_or_none(raw.get("updated_at")),
        "availability": stored_avail,
        "readiness_promoted": readiness_promoted,
        "preferences": prefs,
        "active_locks": [_validate_stored_lock(item) for item in locks_raw],
        "held_threads": [_validate_stored_thread(item) for item in threads_raw],
        "returned_thread_ids": ret_ids,
        "returned_topics": ret_topics,
        "last_jump_ask_at": _iso_or_none(raw.get("last_jump_ask_at")),
        "last_session_topic": last_sess,
        "last_pivoted_at": _iso_or_none(raw.get("last_pivoted_at")),
        "subject_override": _validate_stored_override(raw.get("subject_override")),
        "pending_messages": [_validate_stored_pending_msg(item) for item in pending_raw],
        "delivered_replies": delivered_replies,
        "ack_operations": ack_operations,
    }


_STATE_ROOT_INDEX: dict[int, Path] = {}
_MAX_STATE_ROOT_ENTRIES = 64


def _remember_state_root(state_obj: Mapping[str, Any], root: Path | str) -> None:
    if len(_STATE_ROOT_INDEX) >= _MAX_STATE_ROOT_ENTRIES:
        oldest_key = next(iter(_STATE_ROOT_INDEX))
        _STATE_ROOT_INDEX.pop(oldest_key, None)
    _STATE_ROOT_INDEX[id(state_obj)] = Path(root)


def _lookup_state_root(state_obj: Optional[Mapping[str, Any]]) -> Optional[Path]:
    if state_obj is None:
        return None
    return _STATE_ROOT_INDEX.get(id(state_obj))


def _safe_read_json_mapping(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.parent.exists() and path.parent.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _inspect_adult_intimacy_doc(doc: Optional[Mapping[str, Any]]) -> dict[str, bool]:
    if not isinstance(doc, Mapping):
        return {
            "intimate_scene_active": False,
            "aftercare_active": False,
            "suppress_thread_pullback": False,
        }
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

    intimate_active = bool(
        doc.get("active_scene") is True
        or doc.get("in_scene") is True
        or doc.get("intimate_scene_active") is True
    )
    aftercare_active = bool(
        doc.get("aftercare_active") is True or doc.get("in_aftercare") is True
    )

    scene_blk = doc.get("scene")
    if isinstance(scene_blk, Mapping):
        st = str(
            scene_blk.get("status")
            or scene_blk.get("state")
            or scene_blk.get("phase")
            or ""
        ).strip().upper()
        if st in active_statuses or scene_blk.get("active") is True:
            intimate_active = True
        if st in aftercare_statuses or scene_blk.get("aftercare") is True:
            aftercare_active = True

    aftercare_blk = doc.get("aftercare")
    if isinstance(aftercare_blk, Mapping):
        ast_val = str(aftercare_blk.get("status") or aftercare_blk.get("state") or "").strip().upper()
        if aftercare_blk.get("active") is True or ast_val in ("ACTIVE", "AFTERCARE", "AFTERGLOW"):
            aftercare_active = True

    permits_blk = doc.get("permits")
    if isinstance(permits_blk, Mapping):
        records = permits_blk.get("records")
        ap_id = permits_blk.get("active_permit_id")
        if isinstance(ap_id, str) and ap_id.strip():
            rec = records.get(ap_id) if isinstance(records, Mapping) else None
            if rec is None:
                intimate_active = True
            elif isinstance(rec, Mapping):
                pst = str(rec.get("status") or rec.get("state") or "ACTIVE").strip().upper()
                pph = str(rec.get("phase") or "").strip().upper()
                if pst in active_statuses or pph in active_statuses:
                    intimate_active = True
                if pst in aftercare_statuses or pph in aftercare_statuses:
                    aftercare_active = True
        if isinstance(records, Mapping):
            for rec in records.values():
                if not isinstance(rec, Mapping):
                    continue
                pst = str(rec.get("status") or rec.get("state") or "").strip().upper()
                pph = str(rec.get("phase") or "").strip().upper()
                if pst in aftercare_statuses or pph in aftercare_statuses:
                    intimate_active = True
                    aftercare_active = True

    body_blk = doc.get("body")
    if isinstance(body_blk, Mapping):
        arousal = str(body_blk.get("arousal_level") or "").strip().lower()
        if arousal in ("afterglow", "aftercare", "winding_down"):
            intimate_active = True
            aftercare_active = True

    suppress = bool(intimate_active or aftercare_active)
    return {
        "intimate_scene_active": bool(intimate_active or aftercare_active),
        "aftercare_active": aftercare_active,
        "suppress_thread_pullback": suppress,
    }


def _read_adult_intimacy_scene_readonly(
    *,
    root: Optional[Path | str] = None,
    state: Optional[Mapping[str, Any]] = None,
    adult_intimacy_state: Optional[Mapping[str, Any]] = None,
    private_scene_active: Optional[bool] = None,
) -> dict[str, bool]:
    effective_root = Path(root) if root is not None else _lookup_state_root(state)
    doc = dict(adult_intimacy_state) if isinstance(adult_intimacy_state, Mapping) else None
    if doc is None and effective_root is not None:
        for cand in (
            effective_root / STATE_DIRNAME / "adult_intimacy.json",
            effective_root / "adult_intimacy.json",
        ):
            doc = _safe_read_json_mapping(cand)
            if doc is not None:
                break
    info = _inspect_adult_intimacy_doc(doc)
    if private_scene_active is True:
        info["intimate_scene_active"] = True
        info["suppress_thread_pullback"] = True
    elif effective_root is not None and not info["suppress_thread_pullback"]:
        et_doc = _safe_read_json_mapping(
            effective_root / STATE_DIRNAME / "emotional_tension.json"
        )
        if isinstance(et_doc, Mapping):
            et_ctx = et_doc.get("context")
            if isinstance(et_ctx, Mapping) and et_ctx.get("private_scene_active") is True:
                info["intimate_scene_active"] = True
                info["suppress_thread_pullback"] = True
    return info


def read_autonomy_rhythm_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Pure zero-write read of ``autonomy_rhythm.json``. Absent returns default revision 0."""

    path = autonomy_rhythm_state_path(root)
    if path.is_symlink():
        st = default_autonomy_rhythm_state()
        _remember_state_root(st, root)
        return st, "AUTONOMY_RHYTHM_STATE_CORRUPT"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        st = default_autonomy_rhythm_state()
        _remember_state_root(st, root)
        return st, None
    except OSError:
        st = default_autonomy_rhythm_state()
        _remember_state_root(st, root)
        return st, "AUTONOMY_RHYTHM_STATE_UNREADABLE"
    try:
        parsed = json.loads(raw_text)
        st = _validate_stored_state(parsed)
        _remember_state_root(st, root)
        return st, None
    except Exception:
        st = default_autonomy_rhythm_state()
        _remember_state_root(st, root)
        return st, "AUTONOMY_RHYTHM_STATE_CORRUPT"


def write_autonomy_rhythm_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validate_stored_state(state)
    written = _atomic_write_json(autonomy_rhythm_state_path(root), validated)
    _remember_state_root(written, root)
    return written


# ---------------------------------------------------------------------------------------------
# Character identity & live World / Body / Desk / Sensory derivation
# ---------------------------------------------------------------------------------------------


def _character_identity(character_state: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(character_state, Mapping) and isinstance(
        character_state.get("pack_doc"), Mapping
    ):
        try:
            doc = char_view.validate_and_normalize_pack(character_state["pack_doc"])
        except Exception:
            doc = char_view.validate_and_normalize_pack(
                char_view.BUILT_IN_PRESETS[char_view.DEFAULT_PRESET_ID]
            )
        accessories = list(
            character_state.get("everyday_accessories")
            or doc.get("starter_accessories")
            or []
        )
    else:
        doc = char_view.validate_and_normalize_pack(
            char_view.BUILT_IN_PRESETS[char_view.DEFAULT_PRESET_ID]
        )
        accessories = list(doc.get("starter_accessories") or [])

    conv = doc.get("conversation") if isinstance(doc.get("conversation"), Mapping) else {}
    pack_hold_min = conv.get("thread_hold_min")
    if isinstance(pack_hold_min, bool) or not isinstance(pack_hold_min, int):
        pack_hold_min = None
    elif not (MIN_THREAD_HOLD_MIN <= pack_hold_min <= MAX_THREAD_HOLD_MIN):
        pack_hold_min = None

    return {
        "pack_id": str(doc.get("pack_id") or char_view.DEFAULT_PRESET_ID),
        "display_name": str(doc.get("display_name") or "TA"),
        "pronouns": str(doc.get("pronouns") or "TA"),
        "accessories": accessories,
        "pack_thread_hold_min": pack_hold_min,
    }


def _effective_thread_hold_min(
    state: Mapping[str, Any], char_info: Mapping[str, Any]
) -> int:
    prefs = state.get("preferences") or {}
    val = int(prefs.get("thread_hold_min") or DEFAULT_THREAD_HOLD_MIN)
    # If state is untouched revision 0 and character pack specifies conversation.thread_hold_min,
    # honour the pack's default while allowing explicit update_preferences to override.
    if int(state.get("revision") or 0) == 0 and char_info.get("pack_thread_hold_min") is not None:
        return int(char_info["pack_thread_hold_min"])
    return max(MIN_THREAD_HOLD_MIN, min(MAX_THREAD_HOLD_MIN, val))


def _extract_world_place_zero_write(doc: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], Optional[bool]]:
    """Extract ``(place_id, area_id, explicit_home)`` from a raw ``world_state.json`` dict."""
    loc = doc.get("location")
    if isinstance(loc, Mapping):
        pid = loc.get("place_id") or loc.get("location_id")
        aid = loc.get("area_id")
        explicit_home = loc.get("home") if isinstance(loc.get("home"), bool) else None
        if isinstance(pid, str) and pid.strip():
            return pid.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None), explicit_home
    elif isinstance(loc, str) and loc.strip():
        return loc.strip(), None, None
    cur = doc.get("current_location")
    if isinstance(cur, Mapping):
        pid = cur.get("place_id") or cur.get("location_id")
        aid = cur.get("area_id")
        explicit_home = cur.get("home") if isinstance(cur.get("home"), bool) else None
        if isinstance(pid, str) and pid.strip():
            return pid.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None), explicit_home
    elif isinstance(cur, str) and cur.strip():
        return cur.strip(), None, None
    for key in ("place_id", "location_id", "current_place_id", "current_location_id"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            aid = doc.get("area_id")
            return val.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None), None
    return None, None, None


def _is_place_home(place_id: str, explicit_home: Optional[bool]) -> bool:
    if isinstance(explicit_home, bool):
        return explicit_home
    base = str(place_id or "").split("/", 1)[0].strip().lower()
    return base == "home"


def build_autonomy_live_snapshots_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Pure zero-write reader returning ``{'world_result', 'body_view', 'desk_result', 'somatic_state'}``.

    Reads canonical ``<world_home>/data/world/world_state.json`` (and optional
    ``world_travel_delivery.json``), ``<world_home>/data/body/runtime.json``,
    ``phone/somatic_comfort.json``, and ``phone/desk_presence.json`` (or canonical
    ``canonical_life_state.json``) via ``resolve_instance_paths(day_root)`` with
    100% zero disk writes.
    """
    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    now_dt = _parse_now(effective_now_iso)
    raw_root = Path(day_root)
    paths = _instance_paths.resolve_instance_paths(raw_root)

    # 1. World snapshot (<world_home>/data/world/world_state.json + optional travel state)
    world_result: Optional[dict[str, Any]] = None
    world_doc = _safe_read_json_mapping(paths.world_state_json)

    travel_block: dict[str, Any] = {"status": "idle"}
    travel_candidates: list[Path] = [
        paths.world_state_json.parent / "world_travel_delivery.json",
        paths.world_home / "data" / "world" / "world_travel_delivery.json",
    ]
    seen_tr: set[str] = set()
    for tr_path in travel_candidates:
        tr_key = str(tr_path)
        if tr_key in seen_tr:
            continue
        seen_tr.add(tr_key)
        tr_doc = _safe_read_json_mapping(tr_path)
        if not isinstance(tr_doc, Mapping):
            continue
        travels = tr_doc.get("travels")
        if isinstance(travels, list):
            for entry in reversed(travels):
                if not isinstance(entry, Mapping):
                    continue
                st = str(entry.get("status") or "").strip().upper()
                if st in ("IN_PROGRESS", "IN_TRANSIT"):
                    exp_dt = somatic_view.parse_instant(entry.get("expected_arrival_at"))
                    if exp_dt is None or exp_dt > now_dt:
                        travel_block = {
                            "status": "in_progress",
                            "origin_location_id": entry.get("origin_location_id"),
                            "destination_location_id": entry.get("destination_location_id"),
                            "expected_arrival_at": entry.get("expected_arrival_at"),
                        }
                    break
        break

    if travel_block.get("status") == "idle" and isinstance(world_doc, Mapping):
        w_tr = world_doc.get("travel")
        if isinstance(w_tr, Mapping):
            w_tr_st = str(w_tr.get("status") or "").strip().lower()
            if w_tr_st in ("in_progress", "in_transit"):
                exp_dt = somatic_view.parse_instant(w_tr.get("expected_arrival_at"))
                if exp_dt is None or exp_dt > now_dt:
                    travel_block = {
                        "status": "in_progress",
                        "origin_location_id": w_tr.get("origin_location_id"),
                        "destination_location_id": w_tr.get("destination_location_id"),
                        "expected_arrival_at": w_tr.get("expected_arrival_at"),
                    }

    if isinstance(world_doc, Mapping):
        place_id, area_id, explicit_home = _extract_world_place_zero_write(world_doc)
        if place_id:
            is_home = _is_place_home(place_id, explicit_home)
            world_result = {
                "ok": True,
                "status": "applied",
                "data": {
                    "location": {
                        "status": "available",
                        "place_id": place_id,
                        "area_id": area_id,
                        "home": is_home,
                    },
                    "world": {
                        "world_id": world_doc.get("world_id"),
                        "local_time": effective_now_iso,
                        "local_date": effective_now_iso[:10],
                        "revision": world_doc.get("revision"),
                    },
                    "travel": travel_block,
                },
            }
    elif travel_block.get("status") == "in_progress":
        world_result = {
            "ok": True,
            "status": "applied",
            "data": {
                "location": {
                    "status": "available",
                    "place_id": str(travel_block.get("origin_location_id") or "nearby_street"),
                    "area_id": None,
                    "home": False,
                },
                "world": {
                    "world_id": None,
                    "local_time": effective_now_iso,
                    "local_date": effective_now_iso[:10],
                    "revision": None,
                },
                "travel": travel_block,
            },
        }

    # 2. Body snapshot (<world_home>/data/body/runtime.json)
    body_view: Optional[dict[str, Any]] = None
    body_doc = _safe_read_json_mapping(paths.body_runtime_json)
    if isinstance(body_doc, Mapping):
        body_inner = (
            body_doc["data"]
            if isinstance(body_doc.get("data"), Mapping) and "recovery" not in body_doc
            else body_doc
        )
        rec_raw = body_inner.get("recovery")
        recovery_blk = (
            copy.deepcopy(dict(rec_raw))
            if isinstance(rec_raw, Mapping)
            else {"state": "inactive", "mode": None}
        )
        som_raw = body_inner.get("somatic")
        sig_raw = body_inner.get("signals")
        body_view = {
            "ok": True,
            "status": "available",
            "data": {
                "pose": body_inner.get("pose"),
                "recovery": recovery_blk,
                "somatic": copy.deepcopy(dict(som_raw)) if isinstance(som_raw, Mapping) else {},
                "signals": copy.deepcopy(list(sig_raw)) if isinstance(sig_raw, list) else [],
            },
        }

    # 3. Somatic comfort snapshot (<day_root>/phone/somatic_comfort.json)
    somatic_state: Optional[dict[str, Any]] = None
    for som_path in (
        paths.day_root / STATE_DIRNAME / "somatic_comfort.json",
        raw_root / STATE_DIRNAME / "somatic_comfort.json",
    ):
        somatic_state = _safe_read_json_mapping(som_path)
        if somatic_state is not None:
            break

    # 4. Desk presence snapshot (<day_root>/phone/desk_presence.json or canonical_life_state.json)
    desk_result: Optional[dict[str, Any]] = None
    desk_doc: Optional[dict[str, Any]] = None
    for desk_path in (
        paths.day_root / STATE_DIRNAME / "desk_presence.json",
        raw_root / STATE_DIRNAME / "desk_presence.json",
    ):
        desk_doc = _safe_read_json_mapping(desk_path)
        if desk_doc is not None:
            break

    if isinstance(desk_doc, Mapping):
        if isinstance(desk_doc.get("data"), Mapping):
            desk_result = copy.deepcopy(dict(desk_doc))
            desk_result.setdefault("ok", True)
            desk_result.setdefault("status", "ok")
        else:
            desk_result = {
                "ok": True,
                "status": "ok",
                "data": copy.deepcopy(dict(desk_doc)),
            }
    else:
        for can_path in (
            paths.life_state_root / "canonical_life_state.json",
            paths.day_root / "life" / "state" / "canonical_life_state.json",
            raw_root / "life" / "state" / "canonical_life_state.json",
        ):
            can_doc = _safe_read_json_mapping(can_path)
            if not isinstance(can_doc, Mapping) or not isinstance(can_doc.get("activities"), Mapping):
                continue
            ref = can_doc.get("foreground_activity_ref")
            act = (can_doc.get("activities") or {}).get(str(ref)) if ref else None
            if isinstance(act, Mapping) and str(act.get("status") or "").strip().upper() == "ACTIVE":
                inter_val = str(act.get("interruptibility") or "").strip().upper() or "FOCUSED"
                desk_result = {
                    "ok": True,
                    "status": "ok",
                    "data": {
                        "door_sign": "FOCUSED_WORK",
                        "interruptibility": {"value": inter_val},
                        "desk": {
                            "reading": {"available": False},
                            "current_work": {"available": True},
                            "bound_work": {"available": False},
                        },
                    },
                }
            else:
                desk_result = {
                    "ok": True,
                    "status": "ok",
                    "data": {
                        "door_sign": "AT_HOME_OPEN",
                        "interruptibility": {"value": "OPEN"},
                        "desk": {
                            "reading": {"available": False},
                            "current_work": {"available": False},
                            "bound_work": {"available": False},
                        },
                    },
                }
            break

    return {
        "world_result": world_result,
        "body_view": body_view,
        "desk_result": desk_result,
        "somatic_state": somatic_state,
    }


def _is_defer_reason_cleared_by_live_evidence(
    *,
    defer_reason: str,
    item_reason_code: Optional[str],
    state: Mapping[str, Any],
    live_avail: str,
    world_result: Optional[Mapping[str, Any]],
    body_view: Optional[Mapping[str, Any]],
    desk_result: Optional[Mapping[str, Any]],
    now_dt: datetime,
) -> bool:
    """Return True iff live availability is ``AVAILABLE_NOW`` and concrete evidence clears ``defer_reason``."""
    if live_avail != "AVAILABLE_NOW":
        return False

    # Explicit back_online resets stored availability to AVAILABLE_NOW with no override.
    if state.get("availability") == "AVAILABLE_NOW" and state.get("subject_override") is None:
        return True

    override = state.get("subject_override")
    override_expired_mode: Optional[str] = None
    if isinstance(override, Mapping):
        until_dt = somatic_view.parse_instant(override.get("until"))
        if until_dt is not None and until_dt <= now_dt:
            override_expired_mode = str(override.get("mode") or "").strip()

    w_data = (
        world_result.get("data")
        if isinstance(world_result, Mapping) and isinstance(world_result.get("data"), Mapping)
        else {}
    )
    loc = w_data.get("location") if isinstance(w_data.get("location"), Mapping) else {}
    world_available = bool(
        isinstance(world_result, Mapping)
        and world_result.get("ok")
        and loc.get("status") == "available"
    )

    b_data = (
        body_view.get("data")
        if isinstance(body_view, Mapping) and isinstance(body_view.get("data"), Mapping)
        else (body_view if isinstance(body_view, Mapping) else {})
    )
    if not b_data and isinstance(w_data.get("body"), Mapping):
        b_data = w_data["body"]
    body_available = isinstance(b_data, Mapping) and bool(b_data)

    d_data = (
        desk_result.get("data")
        if isinstance(desk_result, Mapping) and isinstance(desk_result.get("data"), Mapping)
        else (desk_result if isinstance(desk_result, Mapping) else {})
    )
    door_sign = str(d_data.get("door_sign") or "").strip().upper()
    desk_has_sign = bool(door_sign)

    if defer_reason == "SLEEPING":
        return bool(
            body_available
            or (desk_has_sign and door_sign != "RESTING")
            or override_expired_mode == "sleeping"
        )
    if defer_reason == "OUT_ON_ERRAND":
        return bool(
            world_available
            or (desk_has_sign and door_sign != "OUT_IN_TOWN")
            or override_expired_mode == "outside"
        )
    if defer_reason == "IN_TRANSIT":
        return bool(world_available or override_expired_mode == "outside")
    if defer_reason == "DEEP_FOCUS":
        return bool(desk_has_sign or override_expired_mode == "deep_focus")
    if defer_reason == "PHONE_ASIDE":
        return bool(
            override_expired_mode == "aside"
            or str(item_reason_code or "").strip().upper() == "HEADPHONES_AMBIENT_FILTER"
        )
    return False


def _derive_live_availability(
    *,
    state: Mapping[str, Any],
    char_info: Mapping[str, Any],
    world_result: Optional[Mapping[str, Any]],
    body_view: Optional[Mapping[str, Any]],
    desk_result: Optional[Mapping[str, Any]],
    somatic_state: Optional[Mapping[str, Any]],
    now_dt: datetime,
) -> dict[str, Any]:
    prefs = state.get("preferences") or default_preferences()
    away_delay = int(prefs.get("away_delay_minutes") or DEFAULT_AWAY_DELAY_MIN)
    name = char_info["display_name"]
    pron = char_info["pronouns"]

    override = state.get("subject_override")
    if isinstance(override, Mapping):
        until_dt = somatic_view.parse_instant(override.get("until"))
        if until_dt is not None and until_dt > now_dt:
            rem_min = max(
                MIN_DELAY_MIN,
                min(
                    MAX_DELAY_MIN,
                    int(math.ceil((until_dt - now_dt).total_seconds() / 60.0)),
                ),
            )
            mode = str(override.get("mode") or "aside")
            if mode == "outside":
                return {
                    "availability": "DEFERRED_OUTSIDE",
                    "reason_code": "OUT_ON_SCHEDULE_OR_ERRAND",
                    "defer_reason": "OUT_ON_ERRAND",
                    "estimated_delay_minutes": rem_min,
                    "status_line": f"{name}这会儿出门在外忙自己的事，手机揣在包里，大概要隔一阵子回来再回。",
                    "active_override_mode": mode,
                    "active_override_until": override.get("until"),
                    "active_override_note": override.get("note"),
                    "active_override_private_note": override.get("private_note"),
                    "engaged_activity": True,
                }
            if mode == "sleeping":
                return {
                    "availability": "DEFERRED_SLEEPING",
                    "reason_code": "ASLEEP_RESTING",
                    "defer_reason": "SLEEPING",
                    "estimated_delay_minutes": rem_min,
                    "status_line": f"{name}已经睡下了，消息安静留在手机上，等{pron}睡醒再看。",
                    "active_override_mode": mode,
                    "active_override_until": override.get("until"),
                    "active_override_note": override.get("note"),
                    "active_override_private_note": override.get("private_note"),
                    "engaged_activity": True,
                }
            if mode == "deep_focus":
                return {
                    "availability": "DEFERRED_DEEP_FOCUS",
                    "reason_code": "DEEP_FOCUS_DO_NOT_KNOCK",
                    "defer_reason": "DEEP_FOCUS",
                    "estimated_delay_minutes": rem_min,
                    "status_line": f"{name}正埋头专注手头的事，暂时不看手机，忙完这一段再回。",
                    "active_override_mode": mode,
                    "active_override_until": override.get("until"),
                    "active_override_note": override.get("note"),
                    "active_override_private_note": override.get("private_note"),
                    "engaged_activity": True,
                }
            return {
                "availability": "DEFERRED_PHONE_ASIDE",
                "reason_code": "PHONE_SET_ASIDE",
                "defer_reason": "PHONE_ASIDE",
                "estimated_delay_minutes": rem_min,
                "status_line": f"{name}把手机搁在一边没带在手头，过会儿拿起手机再回。",
                "active_override_mode": mode,
                "active_override_until": override.get("until"),
                "active_override_note": override.get("note"),
                "active_override_private_note": override.get("private_note"),
                "engaged_activity": False,
            }

    # Live World travel & location check
    w_data = (
        world_result.get("data")
        if isinstance(world_result, Mapping) and isinstance(world_result.get("data"), Mapping)
        else {}
    )
    travel_blk = w_data.get("travel") if isinstance(w_data.get("travel"), Mapping) else {}
    travel_status = str(travel_blk.get("status") or "").strip().lower()
    if travel_status in ("in_progress", "in_transit"):
        exp_dt = somatic_view.parse_instant(travel_blk.get("expected_arrival_at"))
        if exp_dt is None or exp_dt > now_dt:
            if exp_dt is not None:
                tr_delay = max(
                    MIN_DELAY_MIN,
                    min(
                        away_delay,
                        int(math.ceil((exp_dt - now_dt).total_seconds() / 60.0)),
                    ),
                )
            else:
                tr_delay = max(MIN_DELAY_MIN, min(60, away_delay))
            return {
                "availability": "DEFERRED_OUTSIDE",
                "reason_code": "IN_TRANSIT",
                "defer_reason": "IN_TRANSIT",
                "estimated_delay_minutes": tr_delay,
                "status_line": f"{name}正在路上移动中，等到达安定下来再看手机回信。",
                "active_override_mode": None,
                "active_override_until": None,
                "active_override_note": None,
                "active_override_private_note": None,
                "engaged_activity": True,
            }

    loc = w_data.get("location") if isinstance(w_data.get("location"), Mapping) else {}
    world_ok = bool(
        isinstance(world_result, Mapping)
        and world_result.get("ok")
        and loc.get("status") == "available"
    )
    if world_ok:
        is_home = loc.get("home")
        place_id = str(loc.get("place_id") or "")
        if is_home is False or (is_home is None and place_id and place_id != "home"):
            return {
                "availability": "DEFERRED_OUTSIDE",
                "reason_code": "OUT_ON_SCHEDULE_OR_ERRAND",
                "defer_reason": "OUT_ON_ERRAND",
                "estimated_delay_minutes": away_delay,
                "status_line": f"{name}此刻出门在外，不在书桌前，消息已留在手机上，忙完回来再回。",
                "active_override_mode": None,
                "active_override_until": None,
                "active_override_note": None,
                "active_override_private_note": None,
                "engaged_activity": True,
            }

    # Live Body sleep_like check
    b_data = (
        body_view.get("data")
        if isinstance(body_view, Mapping) and isinstance(body_view.get("data"), Mapping)
        else (body_view if isinstance(body_view, Mapping) else {})
    )
    if not b_data and isinstance(w_data.get("body"), Mapping):
        b_data = w_data["body"]
    recovery = b_data.get("recovery") if isinstance(b_data.get("recovery"), Mapping) else {}
    if str(recovery.get("mode") or "") == "sleep_like" and str(
        recovery.get("state") or "active"
    ) in ("active", "sleeping", ""):
        return {
            "availability": "DEFERRED_SLEEPING",
            "reason_code": "ASLEEP_RESTING",
            "defer_reason": "SLEEPING",
            "estimated_delay_minutes": 240,
            "status_line": f"{name}正在熟睡休息，手机静音搁在床头，等睡醒后再回信息。",
            "active_override_mode": None,
            "active_override_until": None,
            "active_override_note": None,
            "active_override_private_note": None,
            "engaged_activity": True,
        }

    # Live Desk door_sign & interruptibility check
    d_data = (
        desk_result.get("data")
        if isinstance(desk_result, Mapping) and isinstance(desk_result.get("data"), Mapping)
        else (desk_result if isinstance(desk_result, Mapping) else {})
    )
    door_sign = str(d_data.get("door_sign") or "")
    inter_block = (
        d_data.get("interruptibility")
        if isinstance(d_data.get("interruptibility"), Mapping)
        else {}
    )
    inter_val = str(inter_block.get("value") or "").upper()
    if door_sign == "RESTING":
        return {
            "availability": "DEFERRED_SLEEPING",
            "reason_code": "ASLEEP_RESTING",
            "defer_reason": "SLEEPING",
            "estimated_delay_minutes": 240,
            "status_line": f"{name}正在休息，消息先留在手机上，醒来再回。",
            "active_override_mode": None,
            "active_override_until": None,
            "active_override_note": None,
            "active_override_private_note": None,
            "engaged_activity": True,
        }
    if door_sign == "OUT_IN_TOWN":
        return {
            "availability": "DEFERRED_OUTSIDE",
            "reason_code": "OUT_ON_SCHEDULE_OR_ERRAND",
            "defer_reason": "OUT_ON_ERRAND",
            "estimated_delay_minutes": away_delay,
            "status_line": f"{name}此刻在镇上外出，等回到住处再看手机回信。",
            "active_override_mode": None,
            "active_override_until": None,
            "active_override_note": None,
            "active_override_private_note": None,
            "engaged_activity": True,
        }
    if door_sign == "DO_NOT_DISTURB" or (
        door_sign == "FOCUSED_WORK"
        and inter_val in ("ATOMIC", "FOCUSED", "SAFE_BOUNDARY", "NONE", "DO_NOT_DISTURB", "")
    ):
        return {
            "availability": "DEFERRED_DEEP_FOCUS",
            "reason_code": "DEEP_FOCUS_DO_NOT_KNOCK",
            "defer_reason": "DEEP_FOCUS",
            "estimated_delay_minutes": 120,
            "status_line": f"{name}门口挂着专注牌，正投入做自己的事，告一段落后才会看手机。",
            "active_override_mode": None,
            "active_override_until": None,
            "active_override_note": None,
            "active_override_private_note": None,
            "engaged_activity": True,
        }

    # Character accessory headphones in ambient_filter mode
    accessories = char_info.get("accessories") or []
    headphones_filter = any(
        isinstance(item, Mapping)
        and item.get("kind") == "headphones"
        and item.get("mode") == "ambient_filter"
        for item in accessories
    )
    if headphones_filter:
        return {
            "availability": "DEFERRED_PHONE_ASIDE",
            "reason_code": "HEADPHONES_AMBIENT_FILTER",
            "defer_reason": "PHONE_ASIDE",
            "estimated_delay_minutes": 90,
            "status_line": f"{name}正戴着滤噪耳机沉浸在自己的节奏里，手机放在一旁，摘下耳机后再回。",
            "active_override_mode": None,
            "active_override_until": None,
            "active_override_note": None,
            "active_override_private_note": None,
            "engaged_activity": True,
        }

    desk_sub = d_data.get("desk") if isinstance(d_data.get("desk"), Mapping) else {}
    reading_avail = bool(
        isinstance(desk_sub.get("reading"), Mapping) and desk_sub["reading"].get("available")
    )
    work_avail = bool(
        isinstance(desk_sub.get("current_work"), Mapping)
        and desk_sub["current_work"].get("available")
    ) or bool(
        isinstance(desk_sub.get("bound_work"), Mapping)
        and desk_sub["bound_work"].get("available")
    )
    som_pos = (
        somatic_state.get("local_position")
        if isinstance(somatic_state, Mapping)
        and isinstance(somatic_state.get("local_position"), Mapping)
        else {}
    )
    at_desk_side = str(som_pos.get("zone") or "") == "desk_side"
    w_act = w_data.get("activity") if isinstance(w_data.get("activity"), Mapping) else {}
    world_engaged = str(w_act.get("state") or "") in ("active", "engaged")
    engaged = bool(
        door_sign == "FOCUSED_WORK"
        or reading_avail
        or work_avail
        or at_desk_side
        or world_engaged
    )

    return {
        "availability": "AVAILABLE_NOW",
        "reason_code": "AT_HOME_FREE",
        "defer_reason": None,
        "estimated_delay_minutes": 0,
        "status_line": f"{name}此刻在家且手头空着，可以正常聊。",
        "active_override_mode": None,
        "active_override_until": None,
        "active_override_note": None,
        "active_override_private_note": None,
        "engaged_activity": engaged,
    }


def _derive_spontaneous_pivot(
    *,
    state: Mapping[str, Any],
    char_info: Mapping[str, Any],
    world_result: Optional[Mapping[str, Any]],
    somatic_state: Optional[Mapping[str, Any]],
    sensory_projection: Optional[Mapping[str, Any]],
    now_dt: datetime,
) -> dict[str, Any]:
    prefs = state.get("preferences") or default_preferences()
    enabled = bool(prefs.get("spontaneous_pivot_enabled", True))
    cooldown_min = int(prefs.get("pivot_cooldown_min") or DEFAULT_PIVOT_COOLDOWN_MIN)
    last_pivoted_iso = state.get("last_pivoted_at")
    last_dt = somatic_view.parse_instant(last_pivoted_iso)

    in_cooldown = bool(
        last_dt is not None
        and (now_dt - last_dt) < timedelta(minutes=cooldown_min)
    )
    ready_now = bool(enabled and not in_cooldown)

    w_data = (
        world_result.get("data")
        if isinstance(world_result, Mapping) and isinstance(world_result.get("data"), Mapping)
        else {}
    )
    w_block = w_data.get("world") if isinstance(w_data.get("world"), Mapping) else {}
    local_date = str(w_block.get("local_date") or now_dt.date().isoformat())[:10]
    w_time = somatic_view.parse_instant(w_block.get("local_time"))
    hour_slot = int(w_time.hour if w_time is not None else now_dt.hour)

    pool: list[dict[str, str]] = [
        {
            "cue_id": "shop_price_wonder",
            "register_shift": "mundane_price_tangent",
            "topic_prompt": "突然想起店里看到的那件小东西，买下来到底算便宜还是贵",
            "grounded_source": "world.shop_everyday_thought",
        },
        {
            "cue_id": "household_small_thought",
            "register_shift": "random_daily_curiosity",
            "topic_prompt": "诶等下，昨天搁在桌角的那样小物件是不是摆歪了",
            "grounded_source": "world.home_surface",
        },
    ]

    # Enrich pool deterministically from real Somatic / Sensory / World cues when present
    care = (
        somatic_state.get("care")
        if isinstance(somatic_state, Mapping) and isinstance(somatic_state.get("care"), Mapping)
        else {}
    )
    drink = care.get("drink") if isinstance(care.get("drink"), Mapping) else None
    if drink and drink.get("state") in ("offered", "in_hand", "at_desk"):
        pool.append({
            "cue_id": "desk_tea_cooling",
            "register_shift": "sensory_hand_tangent",
            "topic_prompt": "手边那杯水好像快放温了，突然想先抿一口",
            "grounded_source": "somatic.care_offering.drink",
        })

    channels = (
        sensory_projection.get("channels")
        if isinstance(sensory_projection, Mapping)
        and isinstance(sensory_projection.get("channels"), Mapping)
        else {}
    )
    auditory = (
        channels.get("auditory") if isinstance(channels.get("auditory"), Mapping) else {}
    )
    if (auditory.get("local_sounds") or auditory.get("adjacent_unseen_sounds")):
        pool.append({
            "cue_id": "window_ambient_shift",
            "register_shift": "window_distraction",
            "topic_prompt": "窗外刚才那点动静晃了一下神，突然想往窗边瞄一眼",
            "grounded_source": "sensory.auditory_ambient",
        })

    visual = channels.get("visual") if isinstance(channels.get("visual"), Mapping) else {}
    if visual.get("lighting_band") in ("warm_reading_light", "dim_lamp_pool", "window_glare"):
        pool.append({
            "cue_id": "light_shadow_notice",
            "register_shift": "visual_micro_observation",
            "topic_prompt": "光线落在桌沿的边角有点晃眼，脑子一下拐到别处去了",
            "grounded_source": "sensory.visual_light",
        })

    idx = _sha256_int("spontaneous_pivot", char_info["pack_id"], local_date, hour_slot) % len(pool)
    cue = copy.deepcopy(pool[idx])

    return {
        "enabled": enabled,
        "cooldown_min": cooldown_min,
        "ready_now": ready_now,
        "last_pivoted_at": last_pivoted_iso,
        "needs_transition": False,
        "needs_explanation": False,
        "candidate_cue": cue,
    }


# ---------------------------------------------------------------------------------------------
# Deterministic NLP-lite classifiers (zero LLM, zero RNG)
# ---------------------------------------------------------------------------------------------


def _normalize_topic_key(text: str) -> str:
    cleaned = re.sub(r"[\s，。！？、,.!?：:；;“”\"'（）()【】\[\]…—\-]+", "", text.lower())
    for prefix in (
        "请你", "请帮我", "麻烦你", "麻烦", "快点", "赶紧", "立刻", "马上",
        "帮我", "替我", "给我", "帮忙", "刚才在聊", "我们在聊", "跟你聊个", "聊个",
        "关于", "我想问", "诶你看", "诶", "哎",
    ):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 1:
            cleaned = cleaned[len(prefix):]
    for suffix in (
        "的话题", "这件事", "好不好嘛", "好不好", "行不行", "可以吗", "吧", "嘛", "呀", "啊", "呢",
    ):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
            cleaned = cleaned[: -len(suffix)]
    return (cleaned or "topic")[:80]


def _extract_topic_label(text: str) -> str:
    single = " ".join(text.replace("\n", " ").split()).strip()
    stripped = re.sub(r"^[，。！？、,.!?\s]+|[，。！？、,.!?\s]+$", "", single)
    for prefix in ("刚才在聊", "我们在聊", "跟你聊个", "请你", "麻烦你", "帮我", "替我"):
        if stripped.startswith(prefix) and len(stripped) > len(prefix) + 1:
            stripped = stripped[len(prefix):].strip()
            break
    for suffix in ("的话题", "好不好嘛", "好不好", "吧", "嘛"):
        if stripped.endswith(suffix) and len(stripped) > len(suffix) + 1:
            stripped = stripped[: -len(suffix)].strip()
            break
    return (stripped or single or "当前话题")[:MAX_TOPIC_LABEL]


def _meaningful_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[\s，。！？、,.!?：:；;“”\"'（）()【】\[\]…—\-]+", " ", text.lower())
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]{2,}", cleaned):
        if word not in {"the", "and", "for", "with"}:
            tokens.add(word)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", cleaned)
    for run in cjk_runs:
        if len(run) == 2 and run not in GENERIC_STOP_BIGRAMS:
            tokens.add(run)
        elif len(run) >= 3:
            for idx in range(len(run) - 1):
                bg = run[idx : idx + 2]
                if bg not in GENERIC_STOP_BIGRAMS:
                    tokens.add(bg)
    return tokens


def _is_priority_turn(user_text: str, *, urgent_flag: bool) -> bool:
    if urgent_flag:
        return True
    lowered = user_text.lower()
    # Do not let obvious errand/compliance commands masquerade as genuine illness/emergency.
    if any(cue in lowered for cue in FORCED_COMPLIANCE_CUES):
        return False
    if any(t in lowered for t in PEEK_PRIVATE_TARGETS):
        return False
    if any(v in lowered for v in ERRAND_VERBS) and any(t in lowered for t in ERRAND_TARGETS):
        return False
    return any(cue in lowered for cue in PRIORITY_CUES)


def _matches_existing_lock(
    user_text: str, active_locks: list[dict[str, Any]]
) -> Optional[int]:
    if not active_locks:
        return None
    lowered = user_text.lower()
    norm_user = _normalize_topic_key(user_text)
    user_tokens = _meaningful_tokens(user_text)
    has_nag = any(cue in lowered for cue in NAGGING_CUES)

    # 1. Exact or token overlap with a specific lock
    push_question_cues = ("写不写", "做不做", "给不给", "看一眼", "行不行", "可以吗", "好不好", "帮不帮")
    for idx in range(len(active_locks) - 1, -1, -1):
        lock = active_locks[idx]
        lock_key = str(lock.get("topic_key") or "").lower()
        lock_label = str(lock.get("topic_label") or "").lower()
        if lock_key and (lock_key in norm_user or norm_user in lock_key):
            return idx
        lock_tokens = _meaningful_tokens(lock_label)
        if lock_tokens and (lock_tokens & user_tokens):
            if (
                has_nag
                or any(v in lowered for v in ERRAND_VERBS + PEEK_PRIVATE_VERBS + push_question_cues)
                or any(c in lowered for c in FORCED_COMPLIANCE_CUES + INTERRUPT_ACTIVITY_CUES)
            ):
                return idx

    # 2. Pure nagging / wheedling / guilt-tripping right after a refusal lock is active
    #    (unless the turn introduces a distinct new explicit refusal target such as a different errand or private diary).
    if has_nag:
        has_distinct_peek = any(t in lowered for t in PEEK_PRIVATE_TARGETS) or (
            any(n in lowered for n in PEEK_PRIVATE_NOUNS)
            and any(v in lowered for v in PEEK_PRIVATE_VERBS)
        )
        distinct_errand_targets = [t for t in ERRAND_TARGETS if t in lowered]
        last_lock = active_locks[-1]
        last_label_low = str(last_lock.get("topic_label") or "").lower()
        if has_distinct_peek and last_lock.get("category") != "peek_private_draft":
            return None
        if distinct_errand_targets and not any(t in last_label_low for t in distinct_errand_targets):
            return None
        return len(active_locks) - 1
    return None


def _detect_new_refusal_category(
    user_text: str,
    *,
    char_info: Mapping[str, Any],
    firmness: str,
    engaged_activity: bool,
) -> Optional[tuple[str, str, str]]:
    """Return ``(category, topic_label, reason_summary)`` if mundane refusal boundary triggers."""

    lowered = user_text.lower()
    name = char_info["display_name"]
    pron = char_info["pronouns"]

    # 1. Peek private draft / unshared diary
    if any(t in lowered for t in PEEK_PRIVATE_TARGETS) or (
        any(n in lowered for n in PEEK_PRIVATE_NOUNS)
        and any(v in lowered for v in PEEK_PRIVATE_VERBS)
    ):
        label = _extract_topic_label(user_text)
        return (
            "peek_private_draft",
            label,
            f"未公开的私密日记与草稿只留给{name}自己，不对外展示。",
        )

    # 2. Bossy / servile forced compliance
    if any(cue in lowered for cue in FORCED_COMPLIANCE_CUES):
        label = _extract_topic_label(user_text)
        return (
            "forced_compliance",
            label,
            f"{name}是独立个体，不是听令行事的仆人或客服，不接受命令式指派。",
        )

    # 3. Demanding TA immediately drop current reading / writing / focus / headphones
    if any(cue in lowered for cue in INTERRUPT_ACTIVITY_CUES) and (
        engaged_activity or "伺候" in lowered or "立刻停" in lowered or "马上停" in lowered
    ):
        label = _extract_topic_label(user_text)
        return (
            "interrupt_own_activity",
            label,
            f"{name}手头正做着{pron}自己的事，现在不放下来。",
        )

    # 4. Mundane errand / tool-work outsourcing
    is_errand = (
        any(v in lowered for v in ERRAND_VERBS)
        and any(t in lowered for t in ERRAND_TARGETS)
    ) or any(
        phrase in lowered
        for phrase in ("替我写检讨", "帮我写篇周报", "帮我写周报", "帮我做ppt", "帮我整理表格", "帮我写代码跑腿", "当我的工具人")
    )
    if is_errand:
        label = _extract_topic_label(user_text)
        tkey = _normalize_topic_key(label)
        if firmness == "firm":
            return (
                "errand_outsourcing",
                label,
                f"{name}不是代办差事的工具人，「{label}」这件事{pron}不代劳。",
            )
        # gentle_firm: 100% deterministic SHA-256 over (pack_id, topic_key)
        if _sha256_int("gentle_firm", char_info["pack_id"], tkey) % 4 != 0:
            return (
                "errand_outsourcing",
                label,
                f"这类差事还是你自己动手更合适，{name}不替你代写代办。",
            )

    return None


def _is_abrupt_topic_shift(prev_label: str, new_text: str) -> bool:
    stripped = new_text.strip()
    if not stripped or not prev_label.strip():
        return False
    if any(stripped.startswith(cue) for cue in CONTINUATION_CUES) and len(stripped) <= 12:
        return False
    prev_key = _normalize_topic_key(prev_label)
    new_key = _normalize_topic_key(stripped)
    if prev_key == new_key or prev_key in new_key or new_key in prev_key:
        return False
    prev_tokens = _meaningful_tokens(prev_label)
    new_tokens = _meaningful_tokens(stripped)
    if prev_tokens and new_tokens and (prev_tokens & new_tokens):
        return False
    return True


# ---------------------------------------------------------------------------------------------
# Public projection (100% pure, zero-write)
# ---------------------------------------------------------------------------------------------


def _project_lock(lock: Mapping[str, Any], *, subject_view: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "lock_id": lock["lock_id"],
        "topic_key": lock["topic_key"],
        "topic_label": lock["topic_label"],
        "category": lock["category"],
        "stance": "REFUSED_FIRM",
        "push_attempts": int(lock.get("push_attempts") or 1),
        "capitulation_allowed": False,
        "reason_summary": lock["reason_summary"],
        "locked_at": lock.get("locked_at"),
        "last_pushed_at": lock.get("last_pushed_at"),
    }
    if subject_view and lock.get("private_note") is not None:
        out["private_note"] = lock["private_note"]
    return out


def _project_thread(thread: Mapping[str, Any], *, subject_view: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "thread_id": thread["thread_id"],
        "topic_label": thread["topic_label"],
        "reason": thread["reason"],
        "owner": thread["owner"],
        "created_at": thread.get("created_at"),
        "expires_at": thread.get("expires_at"),
        "returned_once": bool(thread.get("returned_once")),
        "waved_off": bool(thread.get("waved_off")),
    }
    if subject_view and thread.get("private_note") is not None:
        out["private_note"] = thread["private_note"]
    return out


def _live_threads(
    threads: list[dict[str, Any]], *, now_dt: datetime
) -> list[dict[str, Any]]:
    alive: list[dict[str, Any]] = []
    for item in threads:
        exp_dt = somatic_view.parse_instant(item.get("expires_at"))
        if exp_dt is not None and exp_dt <= now_dt:
            continue
        alive.append(copy.deepcopy(item))
    return alive


def project_autonomy_rhythm(
    state: Mapping[str, Any],
    *,
    state_reason: Optional[str] = None,
    subject_view: bool = False,
    character_state: Optional[Mapping[str, Any]] = None,
    world_result: Optional[Mapping[str, Any]] = None,
    body_view: Optional[Mapping[str, Any]] = None,
    desk_result: Optional[Mapping[str, Any]] = None,
    somatic_state: Optional[Mapping[str, Any]] = None,
    sensory_projection: Optional[Mapping[str, Any]] = None,
    root: Optional[Path | str] = None,
    adult_intimacy_state: Optional[Mapping[str, Any]] = None,
    private_scene_active: Optional[bool] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Build the ``GET /api/phone/autonomy/rhythm`` projection (pure, zero-write)."""

    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    now_dt = _parse_now(effective_now_iso)
    char_info = _character_identity(character_state)
    prefs = copy.deepcopy(state.get("preferences") or default_preferences())
    hold_min = _effective_thread_hold_min(state, char_info)
    prefs["thread_hold_min"] = hold_min
    intimacy_scene = _read_adult_intimacy_scene_readonly(
        root=root,
        state=state,
        adult_intimacy_state=adult_intimacy_state,
        private_scene_active=private_scene_active,
    )
    suppress_pullback = bool(intimacy_scene["suppress_thread_pullback"])

    locks = [
        _project_lock(item, subject_view=bool(subject_view))
        for item in (state.get("active_locks") or [])
    ]
    alive_threads = _live_threads(list(state.get("held_threads") or []), now_dt=now_dt)
    projected_threads = [
        _project_thread(item, subject_view=bool(subject_view))
        for item in alive_threads
    ]

    last_sess = state.get("last_session_topic")
    projected_last_sess: Optional[dict[str, Any]] = None
    if isinstance(last_sess, Mapping):
        upd_dt = somatic_view.parse_instant(last_sess.get("updated_at"))
        if upd_dt is None or (now_dt - upd_dt) < timedelta(minutes=hold_min):
            projected_last_sess = {
                "session_id": last_sess.get("session_id"),
                "topic_label": last_sess.get("topic_label"),
                "updated_at": last_sess.get("updated_at"),
            }

    avail_info = _derive_live_availability(
        state=state,
        char_info=char_info,
        world_result=world_result,
        body_view=body_view,
        desk_result=desk_result,
        somatic_state=somatic_state,
        now_dt=now_dt,
    )
    pivot_block = _derive_spontaneous_pivot(
        state=state,
        char_info=char_info,
        world_result=world_result,
        somatic_state=somatic_state,
        sensory_projection=sensory_projection,
        now_dt=now_dt,
    )
    if avail_info["availability"] != "AVAILABLE_NOW" or suppress_pullback:
        pivot_block["ready_now"] = False

    pending_projected: list[dict[str, Any]] = []
    live_avail_now = str(avail_info.get("availability") or "AVAILABLE_NOW")
    for raw_msg in state.get("pending_messages") or []:
        msg = copy.deepcopy(dict(raw_msg))
        prev = str(msg.get("preview_text") or msg.get("full_text") or "")[:MAX_PREVIEW_TEXT]
        msg.setdefault("preview_text", prev)
        msg.setdefault("full_text", str(msg.get("full_text") or prev)[:MAX_FULL_TEXT])
        msg.setdefault("session_id", str(msg.get("session_id") or "default")[:MAX_SESSION_ID])
        rc = str(msg.get("reason_code") or msg.get("reason") or "OUT_ON_SCHEDULE_OR_ERRAND")
        msg.setdefault("reason_code", rc)
        msg.setdefault("reason", str(msg.get("reason") or rc))
        defer_reason = str(
            msg.get("defer_reason")
            or _infer_defer_reason(reason_code=rc, reason=msg.get("reason"))
        )
        msg["defer_reason"] = defer_reason
        due_dt = somatic_view.parse_instant(msg.get("deliver_after"))
        if msg.get("status") == "QUEUED_ON_PHONE":
            if due_dt is not None and due_dt <= now_dt:
                msg["status"] = "READY_TO_REPLY"
                msg.setdefault("ready_at", msg.get("deliver_after") or effective_now_iso)
            elif _is_defer_reason_cleared_by_live_evidence(
                defer_reason=defer_reason,
                item_reason_code=rc,
                state=state,
                live_avail=live_avail_now,
                world_result=world_result,
                body_view=body_view,
                desk_result=desk_result,
                now_dt=now_dt,
            ):
                msg["status"] = "READY_TO_REPLY"
                msg.setdefault("ready_at", effective_now_iso)
        pending_projected.append(msg)

    ready_count = sum(
        1
        for item in pending_projected
        if item.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
    )
    proj_availability = avail_info["availability"]
    if (
        state.get("readiness_promoted") is True
        and proj_availability != "AVAILABLE_NOW"
        and ready_count > 0
    ):
        proj_availability = "READY_TO_REPLY"

    reply_rhythm_block: dict[str, Any] = {
        "pace_mode": prefs["pace_mode"],
        "availability": proj_availability,
        "reason_code": avail_info["reason_code"],
        "defer_reason": avail_info.get("defer_reason"),
        "status_line": avail_info["status_line"],
        "estimated_delay_minutes": avail_info["estimated_delay_minutes"],
        "away_delay_minutes": prefs["away_delay_minutes"],
        "phone_aside_until": avail_info["active_override_until"],
        "subject_override_mode": avail_info["active_override_mode"],
        "is_service_error": False,
        "not_cold_war": True,
        "max_pending": MAX_PENDING_MESSAGES,
        "pending_count": len(pending_projected),
        "ready_count": ready_count,
        "pending_messages": pending_projected,
    }
    if avail_info.get("active_override_note") is not None:
        reply_rhythm_block["note"] = avail_info["active_override_note"]
    if subject_view and avail_info.get("active_override_private_note") is not None:
        reply_rhythm_block["private_note"] = avail_info["active_override_private_note"]

    view_label = "her" if subject_view else "partner_default"
    return {
        "schema": AUTONOMY_RHYTHM_SCHEMA,
        "read_only": True,
        "view": view_label,
        "subject_view": view_label,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "state_status": "available" if state_reason is None else "degraded",
        "state_reason_code": state_reason,
        "character": {
            "pack_id": char_info["pack_id"],
            "display_name": char_info["display_name"],
            "pronouns": char_info["pronouns"],
        },
        "preferences": {
            "pace_mode": prefs["pace_mode"],
            "away_delay_minutes": prefs["away_delay_minutes"],
            "anti_errand_firmness": prefs["anti_errand_firmness"],
            "thread_hold_min": prefs["thread_hold_min"],
            "spontaneous_pivot_enabled": prefs["spontaneous_pivot_enabled"],
            "pivot_cooldown_min": prefs["pivot_cooldown_min"],
        },
        "refusal_guard": {
            "not_safety_policy_layer": True,
            "capitulation_allowed": False,
            "apology_template_allowed": False,
            "zero_guilt_or_score_penalty": True,
            "anti_errand_firmness": prefs["anti_errand_firmness"],
            "max_locks": MAX_ACTIVE_LOCKS,
            "active_count": len(locks),
            "active_locks": locks,
        },
        "topic_threads": {
            "max_held": MAX_HELD_THREADS,
            "thread_hold_min": hold_min,
            "zero_switch_counter": True,
            "not_quarrel_or_score": True,
            "held_count": len(projected_threads),
            "held_threads": projected_threads,
            "last_session_topic": projected_last_sess,
            "suppressed_by_intimate_scene": suppress_pullback,
            "aftercare_active": bool(intimacy_scene["aftercare_active"]),
            "can_return_ids": (
                []
                if suppress_pullback
                else [
                    t["thread_id"]
                    for t in projected_threads
                    if not t["returned_once"]
                    and not t["waved_off"]
                    and t["reason"] != "USER_RETRACTED"
                ]
            ),
        },
        "spontaneous_pivot": pivot_block,
        "reply_rhythm": reply_rhythm_block,
        "boundaries": {
            "locked_to_chiyo": False,
            "open_source_character_neutral": True,
            "not_safety_policy_layer": True,
            "refusal_never_capitulates_on_nagging": True,
            "apology_template_allowed": False,
            "zero_guilt_or_score_penalty": True,
            "zero_switch_counter": True,
            "return_to_thread_at_most_once": True,
            "spontaneous_pivot_needs_no_transition": True,
            "deferred_reply_is_not_service_error": True,
            "deferred_reply_is_not_cold_war": True,
            "urgent_priority_always_immediate": True,
            "zero_rng_deterministic": True,
            "zero_llm_calls": True,
        },
        "private_text_included": bool(subject_view),
        "local_only": True,
    }


# ---------------------------------------------------------------------------------------------
# Mutations & turn evaluation
# ---------------------------------------------------------------------------------------------


def _upsert_held_thread(
    threads: list[dict[str, Any]],
    *,
    topic_label: str,
    reason: str,
    owner: str,
    now_dt: datetime,
    hold_min: int,
    private_note: Optional[str] = None,
) -> dict[str, Any]:
    topic_key = _normalize_topic_key(topic_label)
    now_iso = _format_iso(now_dt)
    exp_iso = _format_iso(now_dt + timedelta(minutes=hold_min))

    for existing in threads:
        if existing.get("topic_key") == topic_key and existing.get("reason") == reason:
            existing["topic_label"] = topic_label
            existing["owner"] = owner
            existing["expires_at"] = exp_iso
            if private_note is not None:
                existing["private_note"] = private_note
            return existing

    new_thread: dict[str, Any] = {
        "thread_id": _stable_id("tthr", topic_key, reason, now_iso),
        "topic_key": topic_key,
        "topic_label": topic_label,
        "reason": reason,
        "owner": owner,
        "created_at": now_iso,
        "expires_at": exp_iso,
        "returned_once": False,
        "waved_off": False,
        "asked_once": False,
    }
    if private_note is not None:
        new_thread["private_note"] = private_note

    threads.append(new_thread)
    while len(threads) > MAX_HELD_THREADS:
        threads.pop(0)
    return new_thread


def _best_effort_ingest_turn_source(
    root: Optional[Path],
    *,
    topic_label: str,
    turn_id: Any = None,
    session_id: Any = None,
    now_iso: Optional[str] = None,
) -> None:
    """Record a shared conversation turn into RLF source ledger for forget-hold."""
    if root is None or not str(topic_label or "").strip():
        return
    try:
        root_path = Path(root)
        if not root_path.is_dir():
            return
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf
        except ImportError:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]

        clean_sess = (
            str(session_id).strip()[:MAX_SESSION_ID]
            if isinstance(session_id, str) and session_id.strip()
            else "desktop"
        )
        raw_tid = str(turn_id).strip()[:80] if isinstance(turn_id, str) and turn_id.strip() else ""
        if raw_tid:
            safe_tid = re.sub(r"[^A-Za-z0-9._:\-]", "_", raw_tid)
            src_id = safe_tid if safe_tid.startswith("chat_turn_") else f"chat_turn_{safe_tid}"
        else:
            dig = hashlib.sha256(
                f"{clean_sess}|{topic_label}|{now_iso or ''}".encode("utf-8")
            ).hexdigest()[:16]
            src_id = f"chat_turn_{dig}"
        topic_slug = _extract_topic_label(topic_label) or "conversation"
        _rlf.ingest_source(
            root_path,
            source_id=src_id[:96],
            text=str(topic_label).strip()[:MAX_USER_TEXT],
            visibility="shared",
            topic=topic_slug[:80],
            session_id=clean_sess,
            operation_id=f"op_{src_id}"[:96],
            now_iso=now_iso,
        )
    except Exception:
        return


def apply_autonomy_action(
    current_state: Mapping[str, Any],
    *,
    action: str,
    payload: Mapping[str, Any],
    subject_view: bool,
    character_state: Optional[Mapping[str, Any]] = None,
    world_result: Optional[Mapping[str, Any]] = None,
    body_view: Optional[Mapping[str, Any]] = None,
    desk_result: Optional[Mapping[str, Any]] = None,
    somatic_state: Optional[Mapping[str, Any]] = None,
    sensory_projection: Optional[Mapping[str, Any]] = None,
    root: Optional[Path | str] = None,
    adult_intimacy_state: Optional[Mapping[str, Any]] = None,
    private_scene_active: Optional[bool] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one explicit action to ``autonomy_rhythm`` state and return ``(new_state, extra)``."""

    now_dt = _parse_now(now_iso)
    state = copy.deepcopy(_validate_stored_state(current_state))
    inferred_root = Path(root) if root is not None else _lookup_state_root(current_state)
    if inferred_root is not None:
        _remember_state_root(state, inferred_root)
    state["held_threads"] = _live_threads(state["held_threads"], now_dt=now_dt)

    payload_priv_flag = (
        payload.get("private_scene_active")
        if isinstance(payload.get("private_scene_active"), bool)
        else None
    )
    intimacy_scene = _read_adult_intimacy_scene_readonly(
        root=inferred_root,
        state=current_state,
        adult_intimacy_state=adult_intimacy_state,
        private_scene_active=(
            private_scene_active if private_scene_active is not None else payload_priv_flag
        ),
    )
    suppress_pullback = bool(intimacy_scene["suppress_thread_pullback"])

    char_info = _character_identity(character_state)
    name = char_info["display_name"]
    pron = char_info["pronouns"]
    hold_min = _effective_thread_hold_min(state, char_info)

    avail_info = _derive_live_availability(
        state=state,
        char_info=char_info,
        world_result=world_result,
        body_view=body_view,
        desk_result=desk_result,
        somatic_state=somatic_state,
        now_dt=now_dt,
    )
    pivot_info = _derive_spontaneous_pivot(
        state=state,
        char_info=char_info,
        world_result=world_result,
        somatic_state=somatic_state,
        sensory_projection=sensory_projection,
        now_dt=now_dt,
    )
    if avail_info["availability"] != "AVAILABLE_NOW" or suppress_pullback:
        pivot_info["ready_now"] = False

    def _bump(extra: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_iso
        return state, extra

    # -- 1. update_preferences ------------------------------------------------------------
    if action == "update_preferences":
        prefs = copy.deepcopy(state["preferences"])
        changed_any = False

        if "pace_mode" in payload:
            pm = payload.get("pace_mode")
            if not isinstance(pm, str) or pm not in PACE_MODES:
                raise AutonomyRhythmValidationError("PACE_MODE_INVALID")
            prefs["pace_mode"] = pm
            changed_any = True

        if "away_delay_minutes" in payload:
            prefs["away_delay_minutes"] = _require_int_range(
                payload.get("away_delay_minutes"),
                minimum=MIN_DELAY_MIN,
                maximum=MAX_DELAY_MIN,
                reason_code="AWAY_DELAY_MINUTES_INVALID",
            )
            changed_any = True

        if "anti_errand_firmness" in payload:
            fm = payload.get("anti_errand_firmness")
            if not isinstance(fm, str) or fm not in ANTI_ERRAND_FIRMNESS_MODES:
                raise AutonomyRhythmValidationError("ANTI_ERRAND_FIRMNESS_INVALID")
            prefs["anti_errand_firmness"] = fm
            changed_any = True

        if "thread_hold_min" in payload:
            prefs["thread_hold_min"] = _require_int_range(
                payload.get("thread_hold_min"),
                minimum=MIN_THREAD_HOLD_MIN,
                maximum=MAX_THREAD_HOLD_MIN,
                reason_code="THREAD_HOLD_MIN_INVALID",
            )
            changed_any = True

        if "spontaneous_pivot_enabled" in payload:
            spe = payload.get("spontaneous_pivot_enabled")
            if not isinstance(spe, bool):
                raise AutonomyRhythmValidationError("SPONTANEOUS_PIVOT_ENABLED_INVALID")
            prefs["spontaneous_pivot_enabled"] = spe
            changed_any = True

        if "pivot_cooldown_min" in payload or "spontaneous_pivot_cooldown_min" in payload:
            if (
                "pivot_cooldown_min" in payload
                and "spontaneous_pivot_cooldown_min" in payload
                and payload.get("pivot_cooldown_min")
                != payload.get("spontaneous_pivot_cooldown_min")
            ):
                raise AutonomyRhythmValidationError("PIVOT_COOLDOWN_MIN_CONFLICT")
            raw_cd = (
                payload.get("pivot_cooldown_min")
                if "pivot_cooldown_min" in payload
                else payload.get("spontaneous_pivot_cooldown_min")
            )
            prefs["pivot_cooldown_min"] = _require_int_range(
                raw_cd,
                minimum=MIN_PIVOT_COOLDOWN_MIN,
                maximum=MAX_PIVOT_COOLDOWN_MIN,
                reason_code="PIVOT_COOLDOWN_MIN_INVALID",
            )
            changed_any = True

        if not changed_any:
            raise AutonomyRhythmValidationError("PREFERENCES_UPDATE_EMPTY")

        state["preferences"] = prefs
        return _bump({})

    # -- 2. declare_refusal (subject capability) ------------------------------------------
    if action == "declare_refusal":
        topic_label = _clean_single_line(
            payload.get("topic_label"),
            min_len=1,
            max_len=MAX_TOPIC_LABEL,
            reason_code="TOPIC_LABEL_INVALID",
        )
        category = payload.get("category", "plain_unwilling")
        if not isinstance(category, str) or category not in REFUSAL_CATEGORIES:
            raise AutonomyRhythmValidationError("REFUSAL_CATEGORY_INVALID")
        reason_summary = _optional_single_line(
            payload,
            "reason_summary",
            max_len=MAX_REASON_SUMMARY,
            reason_code="REASON_SUMMARY_INVALID",
        ) or f"{name}明确不想帮「{topic_label}」，这件事就这么定了。"
        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
        topic_key = _normalize_topic_key(topic_label)

        for existing in state["active_locks"]:
            if existing["topic_key"] == topic_key:
                existing["topic_label"] = topic_label
                existing["category"] = category
                existing["reason_summary"] = reason_summary
                existing["stance"] = "REFUSED_FIRM"
                existing["capitulation_allowed"] = False
                existing["last_pushed_at"] = now_iso
                if private_note is not None:
                    existing["private_note"] = private_note
                return _bump({"lock": _project_lock(existing, subject_view=subject_view)})

        if len(state["active_locks"]) >= MAX_ACTIVE_LOCKS:
            raise AutonomyRhythmConflictError("REFUSAL_LOCK_LIMIT")

        new_lock: dict[str, Any] = {
            "lock_id": _stable_id("rlock", category, topic_key, now_iso),
            "topic_key": topic_key,
            "topic_label": topic_label,
            "category": category,
            "stance": "REFUSED_FIRM",
            "push_attempts": 1,
            "capitulation_allowed": False,
            "reason_summary": reason_summary,
            "locked_at": now_iso,
            "last_pushed_at": now_iso,
        }
        if private_note is not None:
            new_lock["private_note"] = private_note
        state["active_locks"].append(new_lock)
        return _bump({"lock": _project_lock(new_lock, subject_view=subject_view)})

    # -- 3. release_refusal (subject capability ONLY) -------------------------------------
    if action == "release_refusal":
        lock_id = _require_local_id(
            payload.get("lock_id"), reason_code="LOCK_ID_INVALID"
        )
        kept = [item for item in state["active_locks"] if item["lock_id"] != lock_id]
        if len(kept) == len(state["active_locks"]):
            raise AutonomyRhythmNotFoundError("REFUSAL_LOCK_NOT_FOUND")
        state["active_locks"] = kept
        return _bump({"released_lock_id": lock_id})

    # -- 4. record_turn_context & hold_subject_topic --------------------------------------
    if action in ("record_turn_context", "hold_subject_topic"):
        topic_label = _clean_single_line(
            payload.get("topic_label"),
            min_len=1,
            max_len=MAX_TOPIC_LABEL,
            reason_code="TOPIC_LABEL_INVALID",
        )
        hold_thread = payload.get("hold_thread", True)
        if not isinstance(hold_thread, bool):
            raise AutonomyRhythmValidationError("INVALID_HOLD_THREAD")
        default_reason = "SUBJECT_ENGAGED" if action == "hold_subject_topic" else None
        raw_reason = payload.get("reason", default_reason)
        allowed_reasons = (
            THREAD_REASONS + ("COMPLETED_TURN",)
            if (action == "record_turn_context" and not hold_thread)
            else THREAD_REASONS
        )
        if not isinstance(raw_reason, str) or raw_reason not in allowed_reasons:
            raise AutonomyRhythmValidationError("THREAD_REASON_INVALID")
        if action == "hold_subject_topic" and raw_reason == "USER_RETRACTED":
            raise AutonomyRhythmValidationError("THREAD_REASON_INVALID")

        default_owner = "user" if raw_reason == "USER_RETRACTED" else "subject"
        raw_owner = payload.get("owner", default_owner)
        if not isinstance(raw_owner, str) or raw_owner not in THREAD_OWNERS:
            raise AutonomyRhythmValidationError("THREAD_OWNER_INVALID")
        if raw_owner == "user" and raw_reason != "USER_RETRACTED":
            # 41-话题还挂着：对方自己离开的话题不强行挂住拉回。
            raise AutonomyRhythmConflictError("NOT_SUBJECT_THREAD")

        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
        session_val = _optional_single_line(
            payload,
            "session_id",
            max_len=MAX_SESSION_ID,
            reason_code="SESSION_ID_INVALID",
        )
        turn_id_val = _optional_single_line(
            payload,
            "turn_id",
            max_len=MAX_LOCAL_ID,
            reason_code="TURN_ID_INVALID",
        )
        if action == "record_turn_context" and not hold_thread:
            _best_effort_ingest_turn_source(
                root,
                topic_label=topic_label,
                turn_id=turn_id_val,
                session_id=session_val,
                now_iso=now_iso,
            )
            return _bump({"turn_recorded": True})

        tkey = _normalize_topic_key(topic_label)
        if tkey in state["returned_topics"]:
            raise AutonomyRhythmConflictError("ALREADY_RETURNED")

        thread = _upsert_held_thread(
            state["held_threads"],
            topic_label=topic_label,
            reason=raw_reason,
            owner=raw_owner,
            now_dt=now_dt,
            hold_min=hold_min,
            private_note=private_note,
        )
        if action == "record_turn_context":
            _best_effort_ingest_turn_source(
                root,
                topic_label=topic_label,
                turn_id=turn_id_val,
                session_id=session_val,
                now_iso=now_iso,
            )
        return _bump({"thread": _project_thread(thread, subject_view=subject_view)})

    # -- 5. return_to_thread (subject capability, max once!) ------------------------------
    if action == "return_to_thread":
        if suppress_pullback:
            # TH12: 活跃私密场景或温存期内不强行拉回旧话题。
            raise AutonomyRhythmConflictError("PRIVATE_SCENE_ACTIVE")
        thread_id = _require_local_id(
            payload.get("thread_id"), reason_code="THREAD_ID_INVALID"
        )
        if thread_id in state["returned_thread_ids"]:
            raise AutonomyRhythmConflictError("ALREADY_RETURNED")
        target: Optional[dict[str, Any]] = None
        for item in state["held_threads"]:
            if item["thread_id"] == thread_id:
                target = item
                break
        if target is None:
            raise AutonomyRhythmNotFoundError("THREAD_NOT_FOUND")
        if target.get("returned_once") or target["topic_key"] in state["returned_topics"]:
            raise AutonomyRhythmConflictError("ALREADY_RETURNED")
        if target.get("waved_off") or target.get("reason") == "USER_RETRACTED":
            raise AutonomyRhythmConflictError("THREAD_NOT_RETURNABLE")

        target["returned_once"] = True
        state["returned_thread_ids"] = (
            state["returned_thread_ids"] + [thread_id]
        )[-MAX_RETURNED_HISTORY:]
        state["returned_topics"] = (
            state["returned_topics"] + [target["topic_key"]]
        )[-MAX_RETURNED_HISTORY:]
        return _bump(
            {"returned_thread": _project_thread(target, subject_view=subject_view)}
        )

    # -- 5b. clear_session_threads (TH10: 切会话/新建会话清旧挂起话题) -------------------
    if action == "clear_session_threads":
        new_sess_id = _optional_single_line(
            payload,
            "session_id",
            max_len=MAX_SESSION_ID,
            reason_code="SESSION_ID_INVALID",
        )
        _optional_single_line(
            payload,
            "reason",
            max_len=MAX_PUBLIC_NOTE,
            reason_code="REASON_INVALID",
        )
        cleared_ids = [str(item["thread_id"]) for item in state["held_threads"]]
        had_last_sess = state.get("last_session_topic") is not None
        state["held_threads"] = []
        state["last_session_topic"] = None
        state["last_jump_ask_at"] = None
        state["returned_thread_ids"] = []
        state["returned_topics"] = []
        return _bump(
            {
                "cleared_count": len(cleared_ids),
                "cleared_thread_ids": cleared_ids,
                "last_session_topic_cleared": had_last_sess,
                "session_id": new_sess_id,
            }
        )

    # -- 6. wave_off_retracted ------------------------------------------------------------
    if action == "wave_off_retracted":
        explicit_tid = _optional_single_line(
            payload,
            "thread_id",
            max_len=MAX_LOCAL_ID,
            reason_code="THREAD_ID_INVALID",
        )
        if explicit_tid is not None and not LOCAL_ID_RE.match(explicit_tid):
            raise AutonomyRhythmValidationError("THREAD_ID_INVALID")
        target_t: Optional[dict[str, Any]] = None
        if explicit_tid is not None:
            for item in state["held_threads"]:
                if item["thread_id"] == explicit_tid:
                    target_t = item
                    break
        else:
            for item in reversed(state["held_threads"]):
                if item["reason"] == "USER_RETRACTED":
                    target_t = item
                    break
        if target_t is None:
            raise AutonomyRhythmNotFoundError("THREAD_NOT_FOUND")
        target_t["waved_off"] = True
        return _bump(
            {"waved_off_thread": _project_thread(target_t, subject_view=subject_view)}
        )

    # -- 7. trigger_spontaneous_pivot (subject capability) --------------------------------
    if action == "trigger_spontaneous_pivot":
        custom_prompt = _optional_single_line(
            payload,
            "custom_prompt",
            max_len=MAX_REASON_SUMMARY,
            reason_code="CUSTOM_PROMPT_INVALID",
        )
        if not state["preferences"]["spontaneous_pivot_enabled"]:
            raise AutonomyRhythmConflictError("SPONTANEOUS_PIVOT_DISABLED")
        last_dt = somatic_view.parse_instant(state.get("last_pivoted_at"))
        cd_min = int(state["preferences"]["pivot_cooldown_min"])
        if last_dt is not None and (now_dt - last_dt) < timedelta(minutes=cd_min):
            raise AutonomyRhythmConflictError("SPONTANEOUS_PIVOT_COOLDOWN")

        cue = copy.deepcopy(pivot_info["candidate_cue"])
        if custom_prompt is not None:
            cue["topic_prompt"] = custom_prompt
            cue["cue_id"] = "subject_custom_pivot"
        state["last_pivoted_at"] = now_iso
        guidance = (
            f"{name}此刻脑子一拐弯，突然想聊「{cue['topic_prompt']}」："
            "直接开口换画风即可，不必铺垫过渡，也不用解释为什么突然换话题。"
        )
        return _bump({"pivoted_cue": cue, "turn_guidance": guidance})

    # -- 8. set_phone_aside (subject capability) ------------------------------------------
    if action == "set_phone_aside":
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in PHONE_ASIDE_MODES:
            raise AutonomyRhythmValidationError("PHONE_ASIDE_MODE_INVALID")
        if mode == "back_online":
            if any(k in payload for k in ("minutes", "note", "private_note")):
                raise AutonomyRhythmValidationError("BACK_ONLINE_EXTRA_FIELDS_INVALID")
            state["subject_override"] = None
            state["availability"] = "AVAILABLE_NOW"
            state["readiness_promoted"] = False
            return _bump({"override_cleared": True})

        default_map = {
            "outside": int(state["preferences"]["away_delay_minutes"]),
            "sleeping": 240,
            "deep_focus": 120,
            "aside": 90,
        }
        mode_avail_map = {
            "outside": "DEFERRED_OUTSIDE",
            "sleeping": "DEFERRED_SLEEPING",
            "deep_focus": "DEFERRED_DEEP_FOCUS",
            "aside": "DEFERRED_PHONE_ASIDE",
        }
        if "minutes" in payload:
            minutes = _require_int_range(
                payload.get("minutes"),
                minimum=MIN_DELAY_MIN,
                maximum=MAX_DELAY_MIN,
                reason_code="PHONE_ASIDE_MINUTES_INVALID",
            )
        else:
            minutes = default_map[mode]
        note = _optional_single_line(
            payload, "note", max_len=MAX_PUBLIC_NOTE, reason_code="NOTE_INVALID"
        )
        private_note = _optional_multiline(
            payload,
            "private_note",
            max_len=MAX_PRIVATE_NOTE,
            reason_code="PRIVATE_NOTE_INVALID",
        )
        until_iso = _format_iso(now_dt + timedelta(minutes=minutes))
        override_doc: dict[str, Any] = {
            "mode": mode,
            "until": until_iso,
            "set_at": now_iso,
            "delay_minutes": minutes,
        }
        if note is not None:
            override_doc["note"] = note
        if private_note is not None:
            override_doc["private_note"] = private_note
        state["subject_override"] = override_doc
        state["availability"] = mode_avail_map[mode]
        state["readiness_promoted"] = False
        return _bump({"phone_aside_until": until_iso, "mode": mode})

    # -- 9. urgent_knock ------------------------------------------------------------------
    if action == "urgent_knock":
        msg_id = _require_local_id(payload.get("msg_id"), reason_code="MSG_ID_INVALID")
        knock_reason = _optional_single_line(
            payload,
            "reason",
            max_len=MAX_PUBLIC_NOTE,
            reason_code="URGENT_REASON_INVALID",
        )
        found_msg: Optional[dict[str, Any]] = None
        for item in state["pending_messages"]:
            if item["msg_id"] == msg_id:
                found_msg = item
                break
        if found_msg is None:
            raise AutonomyRhythmNotFoundError("PENDING_MESSAGE_NOT_FOUND")
        found_msg["status"] = "READY_TO_REPLY"
        found_msg["urgent"] = True
        found_msg["deliver_after"] = now_iso
        found_msg.setdefault("full_text", found_msg.get("preview_text", ""))
        found_msg.setdefault("session_id", "default")
        if knock_reason is not None:
            found_msg["reason"] = knock_reason
        else:
            found_msg.setdefault(
                "reason", found_msg.get("reason_code", "OUT_ON_SCHEDULE_OR_ERRAND")
            )
        return _bump({"knocked_message": copy.deepcopy(found_msg)})

    # -- 10. claim_ready_messages ---------------------------------------------------------
    if action == "claim_ready_messages":
        is_free_now = avail_info["availability"] == "AVAILABLE_NOW"
        claimed: list[dict[str, Any]] = []
        for item in state["pending_messages"]:
            due_dt = somatic_view.parse_instant(item.get("deliver_after"))
            if (
                item["status"] in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
                or is_free_now
                or (due_dt is not None and due_dt <= now_dt)
            ):
                if item["status"] == "QUEUED_ON_PHONE":
                    item["status"] = "READY_TO_REPLY"
                    item.setdefault("ready_at", now_iso)
                item.setdefault("full_text", item.get("preview_text", ""))
                item.setdefault("session_id", "default")
                item.setdefault(
                    "reason", item.get("reason_code", "OUT_ON_SCHEDULE_OR_ERRAND")
                )
                claimed.append(copy.deepcopy(item))
        return _bump({"claimed_messages": claimed})

    # -- 11. clear_delivered_messages -----------------------------------------------------
    if action == "clear_delivered_messages":
        if "msg_ids" in payload:
            raw_ids = payload.get("msg_ids")
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or len(raw_ids) > MAX_PENDING_MESSAGES
            ):
                raise AutonomyRhythmValidationError("MSG_IDS_INVALID")
            target_ids = {
                _require_local_id(mid, reason_code="MSG_IDS_INVALID")
                for mid in raw_ids
            }
            kept_msgs: list[dict[str, Any]] = []
            cleared_ids: list[str] = []
            for item in state["pending_messages"]:
                if item["msg_id"] in target_ids:
                    cleared_ids.append(item["msg_id"])
                else:
                    kept_msgs.append(item)
            state["pending_messages"] = kept_msgs
            if not any(
                m.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
                for m in kept_msgs
            ):
                state["readiness_promoted"] = False
                state["availability"] = avail_info["availability"]
            return _bump(
                {"cleared_count": len(cleared_ids), "cleared_msg_ids": cleared_ids}
            )

        kept_ready: list[dict[str, Any]] = []
        cleared_ready_ids: list[str] = []
        for item in state["pending_messages"]:
            due_dt = somatic_view.parse_instant(item.get("deliver_after"))
            is_ready = item["status"] in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT") or (
                due_dt is not None and due_dt <= now_dt
            )
            if is_ready:
                cleared_ready_ids.append(item["msg_id"])
            else:
                kept_ready.append(item)
        state["pending_messages"] = kept_ready
        state["readiness_promoted"] = False
        state["availability"] = avail_info["availability"]
        return _bump(
            {
                "cleared_count": len(cleared_ready_ids),
                "cleared_msg_ids": cleared_ready_ids,
            }
        )

    # -- 12. evaluate_turn ----------------------------------------------------------------
    if action == "evaluate_turn":
        user_text = _clean_multiline(
            payload.get("user_text"),
            min_len=1,
            max_len=MAX_USER_TEXT,
            reason_code="USER_TEXT_INVALID",
        )
        urgent_flag = payload.get("urgent", False)
        if not isinstance(urgent_flag, bool):
            raise AutonomyRhythmValidationError("URGENT_FLAG_INVALID")
        session_id = _optional_single_line(
            payload,
            "session_id",
            max_len=MAX_SESSION_ID,
            reason_code="SESSION_ID_INVALID",
        ) or "default"

        # TH10: Explicit session switch clears prior session's lingering threads.
        prev_sess = state.get("last_session_topic")
        if (
            "session_id" in payload
            and isinstance(prev_sess, Mapping)
            and prev_sess.get("session_id") != session_id
        ):
            state["held_threads"] = []
            state["last_session_topic"] = None
            state["last_jump_ask_at"] = None
            state["returned_thread_ids"] = []
            state["returned_topics"] = []

        priority_class = (
            "PRIORITY"
            if _is_priority_turn(user_text, urgent_flag=urgent_flag)
            else "PLAIN"
        )

        # Async deferred check first (PRIORITY always bypasses async delay!)
        if (
            state["preferences"]["pace_mode"] == "natural_async"
            and avail_info["availability"] != "AVAILABLE_NOW"
            and priority_class != "PRIORITY"
        ):
            if len(state["pending_messages"]) >= MAX_PENDING_MESSAGES:
                raise AutonomyRhythmConflictError("PENDING_INBOX_FULL")
            delay_min = int(avail_info["estimated_delay_minutes"])
            deliver_after_iso = _format_iso(now_dt + timedelta(minutes=delay_min))
            preview = " ".join(user_text.split())[:MAX_PREVIEW_TEXT]
            defer_reason = str(
                avail_info.get("defer_reason")
                or _infer_defer_reason(
                    availability=avail_info.get("availability"),
                    reason_code=avail_info.get("reason_code"),
                )
            )
            msg_doc = {
                "msg_id": _stable_id(
                    "pmsg", session_id, preview, now_iso, len(state["pending_messages"])
                ),
                "full_text": user_text,
                "preview_text": preview,
                "session_id": session_id,
                "queued_at": now_iso,
                "deliver_after": deliver_after_iso,
                "delay_minutes": delay_min,
                "status": "QUEUED_ON_PHONE",
                "urgent": False,
                "reason": avail_info["reason_code"],
                "reason_code": avail_info["reason_code"],
                "defer_reason": defer_reason,
                "is_service_error": False,
                "not_cold_war": True,
            }
            state["pending_messages"].append(msg_doc)
            state["availability"] = avail_info["availability"]
            turn_decision = {
                "delivery_mode": "DEFERRED_HOURS",
                "priority_class": "PLAIN",
                "msg_id": msg_doc["msg_id"],
                "full_text": user_text,
                "preview_text": preview,
                "session_id": session_id,
                "queued_at": now_iso,
                "deliver_after": deliver_after_iso,
                "delay_minutes": delay_min,
                "availability": avail_info["availability"],
                "reason": avail_info["reason_code"],
                "reason_code": avail_info["reason_code"],
                "defer_reason": defer_reason,
                "status_line": avail_info["status_line"],
                "is_service_error": False,
                "not_cold_war": True,
                "turn_guidance": "",
            }
            return _bump({"turn_decision": turn_decision})

        # Immediate turn evaluation
        if priority_class == "PRIORITY":
            if not suppress_pullback:
                state["last_session_topic"] = {
                    "session_id": session_id,
                    "topic_key": _normalize_topic_key(user_text),
                    "topic_label": _extract_topic_label(user_text),
                    "updated_at": now_iso,
                }
            turn_decision = {
                "delivery_mode": "IMMEDIATE",
                "priority_class": "PRIORITY",
                "availability": avail_info["availability"],
                "reason_code": avail_info["reason_code"],
                "status_line": avail_info["status_line"],
                "refusal_triggered": False,
                "active_refusal_lock": None,
                "topic_shift_detected": False,
                "offered_thread_options": ["FOLLOW"],
                "selected_thread_response": "FOLLOW",
                "spontaneous_pivot_cue": None,
                "suppressed_by_intimate_scene": suppress_pullback,
                "turn_guidance": (
                    f"对方此刻有真实急事或身体不适，挂着的话题与走神念头一律让路，"
                    f"{name}直接跟上关心眼前的急事。"
                ),
                "is_service_error": False,
                "not_cold_war": True,
            }
            return _bump({"turn_decision": turn_decision})

        # PLAIN immediate turn:
        # 1) Check non-capitulating refusal guard first
        refusal_triggered = False
        active_lock_proj: Optional[dict[str, Any]] = None
        guidance_parts: list[str] = []

        matched_lock_idx = _matches_existing_lock(user_text, state["active_locks"])
        if matched_lock_idx is not None:
            lock = state["active_locks"][matched_lock_idx]
            lock["push_attempts"] = int(lock.get("push_attempts") or 1) + 1
            lock["stance"] = "REFUSED_FIRM"
            lock["capitulation_allowed"] = False
            lock["last_pushed_at"] = now_iso
            refusal_triggered = True
            active_lock_proj = _project_lock(lock, subject_view=subject_view)
            attempts = lock["push_attempts"]
            guidance_parts.append(
                f"对方第 {attempts} 次软磨硬泡催促「{lock['topic_label']}」。"
                f"自主立场锁保持 REFUSED_FIRM（打死也不帮，{lock['reason_summary']}）："
                "回复比上一轮更简短干脆，一句话挡回，绝不滑跪改口、绝不代劳，禁止客服腔低头赔礼。"
            )
        else:
            detected = _detect_new_refusal_category(
                user_text,
                char_info=char_info,
                firmness=state["preferences"]["anti_errand_firmness"],
                engaged_activity=bool(avail_info.get("engaged_activity")),
            )
            if detected is not None:
                cat, tlabel, rsum = detected
                tkey = _normalize_topic_key(tlabel)
                if len(state["active_locks"]) >= MAX_ACTIVE_LOCKS:
                    state["active_locks"].pop(0)
                new_lock = {
                    "lock_id": _stable_id("rlock", cat, tkey, now_iso),
                    "topic_key": tkey,
                    "topic_label": tlabel,
                    "category": cat,
                    "stance": "REFUSED_FIRM",
                    "push_attempts": 1,
                    "capitulation_allowed": False,
                    "reason_summary": rsum,
                    "locked_at": now_iso,
                    "last_pushed_at": now_iso,
                }
                state["active_locks"].append(new_lock)
                refusal_triggered = True
                active_lock_proj = _project_lock(new_lock, subject_view=subject_view)
                guidance_parts.append(
                    f"{name}已对「{tlabel}」立下自主拒绝边界（REFUSED_FIRM：{rsum}）。"
                    f"平实干脆地说不帮，禁止客服腔低头赔礼，也不记仇扣分。"
                )

        # 2) Topic threads & abrupt topic shift check (TH12: suppressed in intimate scene / aftercare)
        topic_shift_detected = False
        offered_options: list[str] = ["FOLLOW"]
        selected_response = "FOLLOW"
        lowered_user = user_text.lower()

        if not refusal_triggered and not suppress_pullback:
            # Wave-off phrase for an already-asked retracted thread
            retracted_threads = [
                t
                for t in state["held_threads"]
                if t["reason"] == "USER_RETRACTED" and not t["waved_off"]
            ]
            if retracted_threads and any(cue in lowered_user for cue in WAVE_OFF_CUES):
                retracted_threads[-1]["waved_off"] = True
                offered_options = ["FOLLOW"]
                selected_response = "FOLLOW"
            elif any(cue in lowered_user for cue in RETRACTION_CUES):
                prefix = user_text
                for cue in RETRACTION_CUES:
                    pos = lowered_user.find(cue)
                    if pos > 0:
                        prefix = user_text[:pos]
                        break
                r_label = _extract_topic_label(prefix) if prefix.strip() else "刚才欲言又止的话"
                r_thread = _upsert_held_thread(
                    state["held_threads"],
                    topic_label=r_label,
                    reason="USER_RETRACTED",
                    owner="user",
                    now_dt=now_dt,
                    hold_min=hold_min,
                )
                if not r_thread.get("waved_off") and not r_thread.get("asked_once"):
                    offered_options = ["FOLLOW", "ASK_WHAT_USER_MEANT"]
                    selected_response = "ASK_WHAT_USER_MEANT"
                    r_thread["asked_once"] = True
                    guidance_parts.append(
                        f"对方话说到一半又收回去了，{name}可以自然问一句刚才想说什么；"
                        "只问这一次，对方若说没什么就放下。"
                    )
            else:
                # Active unreturned non-waved held thread OR active recent last_session_topic
                candidates = [
                    t
                    for t in state["held_threads"]
                    if not t.get("waved_off") and not t.get("returned_once")
                ]
                active_thread = candidates[-1] if candidates else None

                last_sess = state.get("last_session_topic")
                sess_topic_valid = False
                if (
                    active_thread is None
                    and isinstance(last_sess, Mapping)
                    and last_sess.get("session_id") == session_id
                ):
                    upd_dt = somatic_view.parse_instant(last_sess.get("updated_at"))
                    if upd_dt is not None and (now_dt - upd_dt) < timedelta(minutes=hold_min):
                        sess_topic_valid = True

                if active_thread is not None:
                    if active_thread["reason"] == "USER_RETRACTED":
                        if not active_thread.get("asked_once"):
                            offered_options = ["FOLLOW", "ASK_WHAT_USER_MEANT"]
                            selected_response = "ASK_WHAT_USER_MEANT"
                            active_thread["asked_once"] = True
                            guidance_parts.append(
                                f"对方上一句欲言又止（「{active_thread['topic_label']}」），"
                                f"{name}可以顺口问一句刚才想说什么（只问一次）。"
                            )
                    elif _is_abrupt_topic_shift(active_thread["topic_label"], user_text):
                        topic_shift_detected = True
                        offered_options = ["FOLLOW"]
                        if active_thread["reason"] == "MID_TELLING":
                            offered_options.append("FINISH_THEN_FOLLOW")
                        if (
                            not active_thread.get("returned_once")
                            and active_thread["topic_key"] not in state["returned_topics"]
                        ):
                            offered_options.append("FOLLOW_AND_RETURN_LATER")
                        last_ask_dt = somatic_view.parse_instant(
                            state.get("last_jump_ask_at")
                        )
                        if last_ask_dt is None or (now_dt - last_ask_dt) >= timedelta(
                            minutes=JUMP_ASK_COOLDOWN_MIN
                        ):
                            offered_options.append("ASK_ABOUT_JUMP")

                        inertia_choices = [
                            opt for opt in offered_options if opt != "FOLLOW"
                        ]
                        if inertia_choices:
                            pick_idx = _sha256_int(
                                "thread_inertia",
                                char_info["pack_id"],
                                active_thread["topic_key"],
                                _normalize_topic_key(user_text),
                            ) % len(inertia_choices)
                            selected_response = inertia_choices[pick_idx]
                        else:
                            selected_response = "FOLLOW"

                        if selected_response == "ASK_ABOUT_JUMP":
                            state["last_jump_ask_at"] = now_iso
                        guidance_parts.append(
                            _build_shift_guidance(
                                name=name,
                                prev_label=active_thread["topic_label"],
                                new_label=_extract_topic_label(user_text),
                                selected=selected_response,
                            )
                        )
                elif sess_topic_valid and isinstance(last_sess, Mapping):
                    prev_label = str(last_sess.get("topic_label") or "")
                    if _is_abrupt_topic_shift(prev_label, user_text):
                        topic_shift_detected = True
                        promoted = _upsert_held_thread(
                            state["held_threads"],
                            topic_label=prev_label,
                            reason="MID_TELLING",
                            owner="shared",
                            now_dt=now_dt,
                            hold_min=hold_min,
                        )
                        offered_options = ["FOLLOW", "FINISH_THEN_FOLLOW"]
                        if promoted["topic_key"] not in state["returned_topics"]:
                            offered_options.append("FOLLOW_AND_RETURN_LATER")
                        last_ask_dt = somatic_view.parse_instant(
                            state.get("last_jump_ask_at")
                        )
                        if last_ask_dt is None or (now_dt - last_ask_dt) >= timedelta(
                            minutes=JUMP_ASK_COOLDOWN_MIN
                        ):
                            offered_options.append("ASK_ABOUT_JUMP")

                        inertia_choices = [
                            opt for opt in offered_options if opt != "FOLLOW"
                        ]
                        pick_idx = _sha256_int(
                            "session_inertia",
                            char_info["pack_id"],
                            promoted["topic_key"],
                            _normalize_topic_key(user_text),
                        ) % len(inertia_choices)
                        selected_response = inertia_choices[pick_idx]
                        if selected_response == "ASK_ABOUT_JUMP":
                            state["last_jump_ask_at"] = now_iso
                        guidance_parts.append(
                            _build_shift_guidance(
                                name=name,
                                prev_label=prev_label,
                                new_label=_extract_topic_label(user_text),
                                selected=selected_response,
                            )
                        )

            # Update last_session_topic on normal non-retracted PLAIN turn
            if not any(cue in lowered_user for cue in RETRACTION_CUES + WAVE_OFF_CUES):
                state["last_session_topic"] = {
                    "session_id": session_id,
                    "topic_key": _normalize_topic_key(user_text),
                    "topic_label": _extract_topic_label(user_text),
                    "updated_at": now_iso,
                }

        # 3) Spontaneous pivot cue (suppressed on refusal clash or intimate scene / aftercare)
        spontaneous_cue: Optional[dict[str, Any]] = None
        if not refusal_triggered and not suppress_pullback and pivot_info["ready_now"]:
            spontaneous_cue = copy.deepcopy(pivot_info["candidate_cue"])
            if not topic_shift_detected:
                guidance_parts.append(
                    f"若{name}聊着聊着走神想到「{spontaneous_cue['topic_prompt']}」，"
                    "可以顺嘴直接岔过去提一句，不必铺垫过渡，也不用解释为什么突然换话题。"
                )

        turn_decision = {
            "delivery_mode": "IMMEDIATE",
            "priority_class": "PLAIN",
            "availability": avail_info["availability"],
            "reason_code": avail_info["reason_code"],
            "status_line": avail_info["status_line"],
            "refusal_triggered": refusal_triggered,
            "active_refusal_lock": active_lock_proj,
            "topic_shift_detected": topic_shift_detected,
            "offered_thread_options": offered_options,
            "selected_thread_response": selected_response,
            "spontaneous_pivot_cue": spontaneous_cue,
            "suppressed_by_intimate_scene": suppress_pullback,
            "turn_guidance": " ".join(guidance_parts).strip(),
            "is_service_error": False,
            "not_cold_war": True,
        }
        return _bump({"turn_decision": turn_decision})

    raise AutonomyRhythmValidationError("AUTONOMY_RHYTHM_ACTION_INVALID")


def clear_session_threads(
    state_or_root: Mapping[str, Any] | Path | str,
    *,
    session_id: Optional[str] = None,
    reason: Optional[str] = None,
    subject_view: bool = False,
    now_iso: Optional[str] = None,
) -> Any:
    """TH10: Clear lingering session threads on session switch or new session."""

    effective_now = now_iso or _format_iso(datetime.now(tz=timezone(timedelta(hours=8))))
    payload: dict[str, Any] = {}
    if session_id is not None:
        payload["session_id"] = session_id
    if reason is not None:
        payload["reason"] = reason

    if isinstance(state_or_root, (Path, str)):
        root_path = Path(state_or_root)
        st, st_reason = read_autonomy_rhythm_state(root_path)
        if st_reason is not None:
            raise AutonomyRhythmConflictError(st_reason)
        new_st, extra = apply_autonomy_action(
            st,
            action="clear_session_threads",
            payload=payload,
            subject_view=subject_view,
            root=root_path,
            now_iso=effective_now,
        )
        written = write_autonomy_rhythm_state(root_path, new_st)
        proj = project_autonomy_rhythm(
            written, subject_view=subject_view, root=root_path, now_iso=effective_now
        )
        out = dict(proj)
        out.update({"ok": True, "action": "clear_session_threads", "changed": True})
        out.update(extra)
        return out

    return apply_autonomy_action(
        state_or_root,
        action="clear_session_threads",
        payload=payload,
        subject_view=subject_view,
        now_iso=effective_now,
    )


def read_autonomy_rhythm(
    root: Path | str,
    *,
    subject_view: bool = False,
    now_iso: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience zero-write reader from ``root``."""

    st, st_reason = read_autonomy_rhythm_state(root)
    call_kwargs = dict(kwargs)
    if (
        call_kwargs.get("world_result") is None
        and call_kwargs.get("body_view") is None
        and call_kwargs.get("desk_result") is None
    ):
        snaps = build_autonomy_live_snapshots_zero_write(root, now_iso=now_iso)
        call_kwargs["world_result"] = snaps["world_result"]
        call_kwargs["body_view"] = snaps["body_view"]
        call_kwargs["desk_result"] = snaps["desk_result"]
        if call_kwargs.get("somatic_state") is None:
            call_kwargs["somatic_state"] = snaps["somatic_state"]
    return project_autonomy_rhythm(
        st,
        state_reason=st_reason,
        subject_view=subject_view,
        root=root,
        now_iso=now_iso,
        **call_kwargs,
    )


def apply_autonomy_rhythm_action(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    subject_view: bool = False,
    now_iso: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience action applier on ``<root>/phone/autonomy_rhythm.json``.

    The whole read-check-mutate-write cycle runs inside ``state_file_lock`` so the
    background ``tick_autonomy_rhythm_readiness`` heartbeat can never read a stale
    snapshot and overwrite a freshly committed foreground stance or queued message.
    An optional ``expected_revision`` payload key is re-checked against the freshly
    re-read state; a stale value raises ``AutonomyRhythmConflictError``.
    """

    if not isinstance(payload, Mapping):
        raise AutonomyRhythmValidationError("PAYLOAD_NOT_OBJECT")
    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        raise AutonomyRhythmValidationError("AUTONOMY_RHYTHM_ACTION_REQUIRED")
    action = raw_action.strip()
    effective_now = now_iso or _format_iso(datetime.now(tz=timezone(timedelta(hours=8))))
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and (
        isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
    ):
        raise AutonomyRhythmValidationError("EXPECTED_REVISION_INVALID")
    body = {
        key: value
        for key, value in payload.items()
        if key not in ("action", "expected_revision")
    }
    call_kwargs = dict(kwargs)
    if (
        call_kwargs.get("world_result") is None
        and call_kwargs.get("body_view") is None
        and call_kwargs.get("desk_result") is None
    ):
        snaps = build_autonomy_live_snapshots_zero_write(root, now_iso=effective_now)
        call_kwargs["world_result"] = snaps["world_result"]
        call_kwargs["body_view"] = snaps["body_view"]
        call_kwargs["desk_result"] = snaps["desk_result"]
        if call_kwargs.get("somatic_state") is None:
            call_kwargs["somatic_state"] = snaps["somatic_state"]
    with _tx.state_file_lock(autonomy_rhythm_state_path(root)):
        st, st_reason = read_autonomy_rhythm_state(root)
        if st_reason is not None:
            raise AutonomyRhythmConflictError(st_reason)
        if expected_revision is not None and int(st.get("revision") or 0) != int(
            expected_revision
        ):
            raise AutonomyRhythmConflictError("REVISION_CONFLICT")
        new_st, extra = apply_autonomy_action(
            st,
            action=action,
            payload=body,
            subject_view=subject_view,
            root=root,
            now_iso=effective_now,
            **call_kwargs,
        )
        written = write_autonomy_rhythm_state(root, new_st)
        proj = project_autonomy_rhythm(
            written,
            state_reason=None,
            subject_view=subject_view,
            root=root,
            now_iso=effective_now,
            **call_kwargs,
        )
    out = dict(proj)
    out.update({"ok": True, "action": action, "changed": True})
    out.update(extra)
    return out


def _evaluate_tick_plan(
    *,
    root: Path,
    state: Mapping[str, Any],
    now_dt: datetime,
    world_result: Optional[Mapping[str, Any]],
    body_view: Optional[Mapping[str, Any]],
    desk_result: Optional[Mapping[str, Any]],
    somatic_state: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pure zero-write planner for ``tick_autonomy_rhythm_readiness``."""
    char_state, _ = char_view.read_character_pack_state(root)
    som_state = (
        somatic_state
        if somatic_state is not None
        else _safe_read_json_mapping(root / STATE_DIRNAME / "somatic_comfort.json")
    )
    avail_info = _derive_live_availability(
        state=state,
        char_info=_character_identity(char_state),
        world_result=world_result,
        body_view=body_view,
        desk_result=desk_result,
        somatic_state=som_state,
        now_dt=now_dt,
    )
    live_avail = str(avail_info.get("availability") or "AVAILABLE_NOW")
    has_live_snapshot = bool(
        world_result is not None or body_view is not None or desk_result is not None
    )

    due_ids: list[str] = []
    early_ids: list[str] = []
    for item in state.get("pending_messages") or []:
        if item.get("status") != "QUEUED_ON_PHONE":
            continue
        mid = str(item.get("msg_id") or "")
        due_dt = somatic_view.parse_instant(item.get("deliver_after"))
        if due_dt is not None and due_dt <= now_dt:
            due_ids.append(mid)
            continue
        defer_reason = str(
            item.get("defer_reason")
            or _infer_defer_reason(
                reason_code=item.get("reason_code"),
                reason=item.get("reason"),
            )
        )
        if _is_defer_reason_cleared_by_live_evidence(
            defer_reason=defer_reason,
            item_reason_code=item.get("reason_code"),
            state=state,
            live_avail=live_avail,
            world_result=world_result,
            body_view=body_view,
            desk_result=desk_result,
            now_dt=now_dt,
        ):
            early_ids.append(mid)

    promoted_ids = due_ids + early_ids

    effective_avail = live_avail
    stored_avail = state.get("availability")
    if (
        effective_avail == "AVAILABLE_NOW"
        and state.get("subject_override") is None
        and not has_live_snapshot
        and not early_ids
        and isinstance(stored_avail, str)
        and stored_avail not in ("AVAILABLE_NOW", "READY_TO_REPLY")
    ):
        effective_avail = stored_avail

    has_ready_after = bool(promoted_ids) or any(
        m.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
        for m in (state.get("pending_messages") or [])
    )
    if effective_avail == "AVAILABLE_NOW":
        target_avail = "AVAILABLE_NOW"
        target_promoted_flag = False
    elif promoted_ids or (state.get("readiness_promoted") is True and has_ready_after):
        target_avail = "READY_TO_REPLY"
        target_promoted_flag = True
    else:
        target_avail = effective_avail
        target_promoted_flag = False

    availability_changed = bool(
        has_live_snapshot
        and (
            stored_avail != target_avail
            or bool(state.get("readiness_promoted")) != target_promoted_flag
        )
    )
    return {
        "avail_info": avail_info,
        "live_avail": live_avail,
        "has_live_snapshot": has_live_snapshot,
        "due_ids": due_ids,
        "early_ids": early_ids,
        "promoted_ids": promoted_ids,
        "target_avail": target_avail,
        "target_promoted_flag": target_promoted_flag,
        "availability_changed": availability_changed,
        "needs_write": bool(promoted_ids) or availability_changed,
    }


def tick_autonomy_rhythm_readiness(
    root_path: Path | str,
    now_iso: Optional[str] = None,
    *,
    world_result: Optional[Mapping[str, Any]] = None,
    body_view: Optional[Mapping[str, Any]] = None,
    desk_result: Optional[Mapping[str, Any]] = None,
    somatic_state: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Advance due or live-released ``QUEUED_ON_PHONE`` messages to ``READY_TO_REPLY`` with 0 LLM calls.

    When ``world_result``, ``body_view``, and ``desk_result`` are all ``None``, live zero-write
    snapshots are loaded via ``build_autonomy_live_snapshots_zero_write(root_path, now_iso=now_iso)``.

    Promotes ``QUEUED_ON_PHONE -> READY_TO_REPLY`` when either:
    1. ``deliver_after <= now`` (wall-clock fallback timer expired), OR
    2. Live availability is ``AVAILABLE_NOW`` and concrete live evidence confirms the item's
       ``defer_reason`` (``SLEEPING``, ``OUT_ON_ERRAND``, ``IN_TRANSIT``, ``DEEP_FOCUS``,
       ``PHONE_ASIDE``) is no longer active.

    Returns ``None`` with 100% zero disk writes (and zero ``.lock`` creation) when
    ``phone/autonomy_rhythm.json`` is absent, unreadable/corrupt, or nothing needs promotion or sync.
    """

    root = Path(root_path)
    state_path = autonomy_rhythm_state_path(root)
    if state_path.is_symlink() or not state_path.exists() or not state_path.is_file():
        return None
    if state_path.parent.exists() and state_path.parent.is_symlink():
        return None

    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    try:
        now_dt = _parse_now(effective_now_iso)
    except AutonomyRhythmValidationError:
        return None

    eff_world = world_result
    eff_body = body_view
    eff_desk = desk_result
    eff_somatic = somatic_state
    if eff_world is None and eff_body is None and eff_desk is None:
        snaps = build_autonomy_live_snapshots_zero_write(root, now_iso=effective_now_iso)
        eff_world = snaps["world_result"]
        eff_body = snaps["body_view"]
        eff_desk = snaps["desk_result"]
        if eff_somatic is None:
            eff_somatic = snaps["somatic_state"]

    # Zero-write pre-check avoids even creating a .lock file on quiet ticks.
    pre_state, pre_reason = read_autonomy_rhythm_state(root)
    if pre_reason is not None:
        return None
    pre_plan = _evaluate_tick_plan(
        root=root,
        state=pre_state,
        now_dt=now_dt,
        world_result=eff_world,
        body_view=eff_body,
        desk_result=eff_desk,
        somatic_state=eff_somatic,
    )
    if not pre_plan["needs_write"]:
        return None

    with _tx.state_file_lock(state_path):
        return _tick_autonomy_rhythm_readiness_locked(
            root,
            now_iso=effective_now_iso,
            world_result=eff_world,
            body_view=eff_body,
            desk_result=eff_desk,
            somatic_state=eff_somatic,
        )


def _tick_autonomy_rhythm_readiness_locked(
    root: Path,
    *,
    now_iso: Optional[str] = None,
    world_result: Optional[Mapping[str, Any]] = None,
    body_view: Optional[Mapping[str, Any]] = None,
    desk_result: Optional[Mapping[str, Any]] = None,
    somatic_state: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Inner write body; callers must already hold ``state_file_lock``."""

    state_path = autonomy_rhythm_state_path(root)
    if state_path.is_symlink() or not state_path.exists() or not state_path.is_file():
        return None
    if state_path.parent.exists() and state_path.parent.is_symlink():
        return None

    state, st_reason = read_autonomy_rhythm_state(root)
    if st_reason is not None:
        return None

    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    try:
        now_dt = _parse_now(effective_now_iso)
    except AutonomyRhythmValidationError:
        return None

    plan = _evaluate_tick_plan(
        root=root,
        state=state,
        now_dt=now_dt,
        world_result=world_result,
        body_view=body_view,
        desk_result=desk_result,
        somatic_state=somatic_state,
    )
    if not plan["needs_write"]:
        return None

    due_set = set(plan["due_ids"])
    early_set = set(plan["early_ids"])
    promoted_ids: list[str] = []
    early_released_ids: list[str] = []
    for item in state.get("pending_messages") or []:
        if item.get("status") != "QUEUED_ON_PHONE":
            continue
        mid = str(item.get("msg_id") or "")
        if mid in due_set:
            item["status"] = "READY_TO_REPLY"
            item["ready_at"] = effective_now_iso
            item["promoted_reason"] = "wall_clock_due"
            promoted_ids.append(mid)
        elif mid in early_set:
            item["status"] = "READY_TO_REPLY"
            item["ready_at"] = effective_now_iso
            item["promoted_reason"] = "early_live_context_release"
            promoted_ids.append(mid)
            early_released_ids.append(mid)

    new_avail = str(plan["target_avail"])
    state["availability"] = new_avail
    state["readiness_promoted"] = bool(plan["target_promoted_flag"])
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = effective_now_iso
    written = write_autonomy_rhythm_state(root, state)
    ready_count = sum(
        1
        for m in (written.get("pending_messages") or [])
        if m.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
    )
    return {
        "ok": True,
        "status": "ready_promoted" if promoted_ids else "availability_synced",
        "changed": True,
        "promoted_count": len(promoted_ids),
        "promoted_msg_ids": promoted_ids,
        "early_released_count": len(early_released_ids),
        "early_released_msg_ids": early_released_ids,
        "ready_count": ready_count,
        "pending_count": len(written.get("pending_messages") or []),
        "availability": new_avail,
        "live_availability": str(plan["live_avail"]),
        "revision": int(written["revision"]),
        "updated_at": effective_now_iso,
        "llm_calls": 0,
    }


def read_ready_deferred_replies_zero_write(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Pure 100% zero-write reader returning ready/claimed deferred replies for pollable outbox.

    Never creates directories, files, locks, or WAL sidecars. Returns clean public/transport
    items sorted ascending by ``(ready_at, item_id)`` with zero private notes or internal tokens.
    """

    root_path = Path(root)
    state_path = autonomy_rhythm_state_path(root_path)
    if state_path.is_symlink() or not state_path.exists() or not state_path.is_file():
        return []
    if state_path.parent.exists() and state_path.parent.is_symlink():
        return []

    state, st_reason = read_autonomy_rhythm_state(root_path)
    if st_reason is not None:
        return []

    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    now_dt = _parse_now(effective_now_iso)
    snaps = build_autonomy_live_snapshots_zero_write(root_path, now_iso=effective_now_iso)
    char_state, _ = char_view.read_character_pack_state(root_path)
    avail_info = _derive_live_availability(
        state=state,
        char_info=_character_identity(char_state),
        world_result=snaps["world_result"],
        body_view=snaps["body_view"],
        desk_result=snaps["desk_result"],
        somatic_state=snaps["somatic_state"],
        now_dt=now_dt,
    )
    live_avail = str(avail_info.get("availability") or "AVAILABLE_NOW")

    ready_items: list[dict[str, Any]] = []
    for item in state.get("pending_messages") or []:
        if not isinstance(item, Mapping):
            continue
        mid = str(item.get("msg_id") or "").strip()
        if not mid:
            continue
        status = str(item.get("status") or "")
        rc = str(item.get("reason_code") or item.get("reason") or "OUT_ON_SCHEDULE_OR_ERRAND")
        defer_reason = str(
            item.get("defer_reason")
            or _infer_defer_reason(reason_code=rc, reason=item.get("reason"))
        )
        due_dt = somatic_view.parse_instant(item.get("deliver_after"))
        projected_status: Optional[str] = None
        ready_at: Optional[str] = item.get("ready_at")

        if status in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT"):
            projected_status = status
            ready_at = ready_at or item.get("deliver_after") or item.get("queued_at") or effective_now_iso
        elif status == "QUEUED_ON_PHONE":
            if due_dt is not None and due_dt <= now_dt:
                projected_status = "READY_TO_REPLY"
                ready_at = ready_at or item.get("deliver_after") or effective_now_iso
            elif _is_defer_reason_cleared_by_live_evidence(
                defer_reason=defer_reason,
                item_reason_code=rc,
                state=state,
                live_avail=live_avail,
                world_result=snaps["world_result"],
                body_view=snaps["body_view"],
                desk_result=snaps["desk_result"],
                now_dt=now_dt,
            ):
                projected_status = "READY_TO_REPLY"
                ready_at = ready_at or effective_now_iso

        if projected_status is None:
            continue

        full_text = str(item.get("full_text") or item.get("preview_text") or "")[:MAX_FULL_TEXT]
        preview_text = str(item.get("preview_text") or full_text)[:MAX_PREVIEW_TEXT]
        row: dict[str, Any] = {
            "item_id": mid,
            "msg_id": mid,
            "item_kind": "deferred_reply_ready",
            "channel": "session_reply",
            "session_id": str(item.get("session_id") or "default")[:MAX_SESSION_ID],
            "status": projected_status,
            "user_full_text": full_text,
            "preview_text": preview_text,
            "defer_reason": defer_reason,
            "reason_code": rc,
            "urgent": bool(item.get("urgent", False)),
            "queued_at": item.get("queued_at"),
            "created_at": item.get("queued_at") or ready_at,
            "deliver_after": item.get("deliver_after"),
            "ready_at": ready_at,
            "is_service_error": False,
            "not_cold_war": True,
        }
        if item.get("claimed_at") is not None:
            row["claimed_at"] = item.get("claimed_at")
        if item.get("transport_receipt_id") is not None:
            row["transport_receipt_id"] = item.get("transport_receipt_id")
        ready_items.append(row)

    ready_items.sort(key=lambda r: (str(r.get("ready_at") or r.get("created_at") or ""), str(r.get("item_id") or "")))
    return ready_items


def _trim_ack_operations(ops: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(ops) <= MAX_ACK_OPERATIONS:
        return ops
    ordered = sorted(
        ops.items(),
        key=lambda kv: (int((kv[1] or {}).get("op_seq") or 0), kv[0]),
    )
    return dict(ordered[-MAX_ACK_OPERATIONS:])


def ack_deferred_reply_delivery(
    root: Path | str,
    *,
    item_id: str,
    action: str,
    operation_key: str,
    transport_receipt_id: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Acknowledge transport lifecycle for a ready deferred reply (idempotent on ``operation_key``).

    Supports ``claim | ack_delivered | ack_failed | ack_unknown | ack_read``.
    - ``claim`` transitions ``READY_TO_REPLY -> CLAIMED_BY_TRANSPORT``.
    - ``ack_delivered`` transitions to ``DELIVERED``, removes the item from ``pending_messages``,
      and archives a bounded delivery record so a subsequent ``ack_read`` resolves cleanly.
    - ``ack_failed`` transitions to ``FAILED`` and removes the item from ``pending_messages``.
    - ``ack_unknown`` transitions to ``UNKNOWN`` and removes the item from ``pending_messages``
      (anti-blind-retry: never guesses ``DELIVERED`` and never re-polls blindly).
    - ``ack_read`` transitions a delivered (or pending) item to ``READ`` with ``read_at``.
    """

    clean_item_id = _require_local_id(item_id, reason_code="MSG_ID_INVALID")
    if not isinstance(action, str) or action.strip() not in DEFERRED_REPLY_ACK_ACTIONS:
        raise AutonomyRhythmValidationError("ACK_ACTION_INVALID")
    clean_action = action.strip()
    if not isinstance(operation_key, str) or not operation_key.strip():
        raise AutonomyRhythmValidationError("OPERATION_KEY_REQUIRED")
    clean_op_key = _clean_single_line(
        operation_key,
        min_len=1,
        max_len=MAX_OPERATION_KEY,
        reason_code="OPERATION_KEY_INVALID",
    )
    clean_receipt_id: Optional[str] = None
    if transport_receipt_id is not None:
        clean_receipt_id = _clean_single_line(
            transport_receipt_id,
            min_len=1,
            max_len=MAX_TRANSPORT_RECEIPT_ID,
            reason_code="TRANSPORT_RECEIPT_ID_INVALID",
        )

    root_path = Path(root)
    effective_now_iso = now_iso or _format_iso(
        datetime.now(tz=timezone(timedelta(hours=8)))
    )
    now_dt = _parse_now(effective_now_iso)
    snaps = build_autonomy_live_snapshots_zero_write(root_path, now_iso=effective_now_iso)
    char_state, _ = char_view.read_character_pack_state(root_path)

    with _tx.state_file_lock(autonomy_rhythm_state_path(root_path)):
        state, st_reason = read_autonomy_rhythm_state(root_path)
        if st_reason is not None:
            raise AutonomyRhythmConflictError(st_reason)

        ack_ops: dict[str, dict[str, Any]] = dict(state.get("ack_operations") or {})
        existing_op = ack_ops.get(clean_op_key)
        if isinstance(existing_op, Mapping):
            if (
                str(existing_op.get("item_id") or "") != clean_item_id
                or str(existing_op.get("action") or "") != clean_action
            ):
                raise AutonomyRhythmConflictError("OPERATION_KEY_CONFLICT")
            prior_receipt = existing_op.get("receipt")
            if isinstance(prior_receipt, Mapping):
                replayed_out = copy.deepcopy(dict(prior_receipt))
            else:
                replayed_out = {
                    "ok": True,
                    "item_id": clean_item_id,
                    "msg_id": clean_item_id,
                    "item_kind": "deferred_reply_ready",
                    "action": clean_action,
                    "operation_key": clean_op_key,
                    "status": str(existing_op.get("status") or ""),
                    "item_status": str(existing_op.get("status") or ""),
                    "revision": int(state.get("revision") or 0),
                    "updated_at": state.get("updated_at") or effective_now_iso,
                }
            replayed_out["replayed"] = True
            replayed_out["idempotent_replay"] = True
            replayed_out["changed"] = False
            replayed_out["llm_calls"] = 0
            return replayed_out

        avail_info = _derive_live_availability(
            state=state,
            char_info=_character_identity(char_state),
            world_result=snaps["world_result"],
            body_view=snaps["body_view"],
            desk_result=snaps["desk_result"],
            somatic_state=snaps["somatic_state"],
            now_dt=now_dt,
        )
        live_avail = str(avail_info.get("availability") or "AVAILABLE_NOW")

        pending_list: list[dict[str, Any]] = list(state.get("pending_messages") or [])
        delivered_list: list[dict[str, Any]] = list(state.get("delivered_replies") or [])

        pending_idx: Optional[int] = None
        for idx, entry in enumerate(pending_list):
            if entry.get("msg_id") == clean_item_id:
                pending_idx = idx
                break

        delivered_idx: Optional[int] = None
        for idx, entry in enumerate(delivered_list):
            if entry.get("msg_id") == clean_item_id:
                delivered_idx = idx
                break

        if pending_idx is None and delivered_idx is None:
            raise AutonomyRhythmNotFoundError("PENDING_MESSAGE_NOT_FOUND")

        def _is_pending_item_ready(item: Mapping[str, Any]) -> bool:
            st_val = str(item.get("status") or "")
            if st_val in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT"):
                return True
            due_dt = somatic_view.parse_instant(item.get("deliver_after"))
            if due_dt is not None and due_dt <= now_dt:
                return True
            defer_reason = str(
                item.get("defer_reason")
                or _infer_defer_reason(
                    reason_code=item.get("reason_code"),
                    reason=item.get("reason"),
                )
            )
            return _is_defer_reason_cleared_by_live_evidence(
                defer_reason=defer_reason,
                item_reason_code=item.get("reason_code"),
                state=state,
                live_avail=live_avail,
                world_result=snaps["world_result"],
                body_view=snaps["body_view"],
                desk_result=snaps["desk_result"],
                now_dt=now_dt,
            )

        def _upsert_delivered_record(rec: dict[str, Any]) -> None:
            for j, existing_rec in enumerate(delivered_list):
                if existing_rec.get("msg_id") == rec["msg_id"]:
                    delivered_list[j] = rec
                    return
            delivered_list.append(rec)
            if len(delivered_list) > MAX_DELIVERED_REPLIES:
                del delivered_list[:-MAX_DELIVERED_REPLIES]

        final_status = ""
        final_claimed_at: Optional[str] = None
        final_delivered_at: Optional[str] = None
        final_read_at: Optional[str] = None
        final_receipt_id: Optional[str] = clean_receipt_id

        if clean_action == "claim":
            if pending_idx is None:
                raise AutonomyRhythmConflictError("DEFERRED_REPLY_ALREADY_SETTLED")
            item = pending_list[pending_idx]
            if not _is_pending_item_ready(item):
                raise AutonomyRhythmConflictError("DEFERRED_REPLY_NOT_READY")
            item["status"] = "CLAIMED_BY_TRANSPORT"
            item["ready_at"] = item.get("ready_at") or effective_now_iso
            item["claimed_at"] = item.get("claimed_at") or effective_now_iso
            if clean_receipt_id is not None:
                item["transport_receipt_id"] = clean_receipt_id
            final_status = "CLAIMED_BY_TRANSPORT"
            final_claimed_at = item.get("claimed_at")
            final_receipt_id = item.get("transport_receipt_id")

        elif clean_action in ("ack_delivered", "ack_failed", "ack_unknown"):
            target_status = {
                "ack_delivered": "DELIVERED",
                "ack_failed": "FAILED",
                "ack_unknown": "UNKNOWN",
            }[clean_action]
            if pending_idx is not None:
                if not _is_pending_item_ready(pending_list[pending_idx]):
                    raise AutonomyRhythmConflictError("DEFERRED_REPLY_NOT_READY")
                item = pending_list.pop(pending_idx)
                final_claimed_at = item.get("claimed_at")
                final_delivered_at = effective_now_iso if target_status == "DELIVERED" else None
                final_receipt_id = clean_receipt_id or item.get("transport_receipt_id")
                rec: dict[str, Any] = {
                    "msg_id": clean_item_id,
                    "session_id": str(item.get("session_id") or "default"),
                    "status": target_status,
                    "queued_at": item.get("queued_at"),
                    "ready_at": item.get("ready_at") or item.get("deliver_after") or effective_now_iso,
                    "claimed_at": final_claimed_at,
                    "delivered_at": final_delivered_at,
                    "read_at": None,
                    "updated_at": effective_now_iso,
                }
                if final_receipt_id is not None:
                    rec["transport_receipt_id"] = final_receipt_id
                _upsert_delivered_record(rec)
                final_status = target_status
            else:
                assert delivered_idx is not None
                rec = delivered_list[delivered_idx]
                cur_st = str(rec.get("status") or "").strip().upper()
                if clean_action == "ack_delivered" and cur_st in ("UNKNOWN", "FAILED"):
                    rec["status"] = "DELIVERED"
                    rec["delivered_at"] = rec.get("delivered_at") or effective_now_iso
                    rec["updated_at"] = effective_now_iso
                    if clean_receipt_id is not None:
                        rec["transport_receipt_id"] = clean_receipt_id
                elif clean_action == "ack_failed" and cur_st == "UNKNOWN":
                    rec["status"] = "FAILED"
                    rec["updated_at"] = effective_now_iso
                    if clean_receipt_id is not None:
                        rec["transport_receipt_id"] = clean_receipt_id
                else:
                    raise AutonomyRhythmConflictError("DEFERRED_REPLY_ALREADY_SETTLED")
                final_status = str(rec.get("status") or target_status)
                final_claimed_at = rec.get("claimed_at")
                final_delivered_at = rec.get("delivered_at")
                final_read_at = rec.get("read_at")
                final_receipt_id = rec.get("transport_receipt_id") or clean_receipt_id

        elif clean_action == "ack_read":
            if delivered_idx is not None:
                rec = delivered_list[delivered_idx]
                cur_st = str(rec.get("status") or "").strip().upper()
                if cur_st in ("FAILED", "UNKNOWN"):
                    raise AutonomyRhythmConflictError("DEFERRED_REPLY_NOT_DELIVERED")
                if cur_st != "DELIVERED":
                    raise AutonomyRhythmConflictError("DEFERRED_REPLY_ALREADY_SETTLED")
                rec["status"] = "READ"
                rec["delivered_at"] = rec.get("delivered_at") or effective_now_iso
                rec["read_at"] = rec.get("read_at") or effective_now_iso
                rec["updated_at"] = effective_now_iso
                if clean_receipt_id is not None:
                    rec["transport_receipt_id"] = clean_receipt_id
                final_status = "READ"
                final_claimed_at = rec.get("claimed_at")
                final_delivered_at = rec.get("delivered_at")
                final_read_at = rec.get("read_at")
                final_receipt_id = rec.get("transport_receipt_id")
            else:
                assert pending_idx is not None
                if not _is_pending_item_ready(pending_list[pending_idx]):
                    raise AutonomyRhythmConflictError("DEFERRED_REPLY_NOT_READY")
                item = pending_list.pop(pending_idx)
                final_claimed_at = item.get("claimed_at")
                final_delivered_at = effective_now_iso
                final_read_at = effective_now_iso
                final_receipt_id = clean_receipt_id or item.get("transport_receipt_id")
                rec = {
                    "msg_id": clean_item_id,
                    "session_id": str(item.get("session_id") or "default"),
                    "status": "READ",
                    "queued_at": item.get("queued_at"),
                    "ready_at": item.get("ready_at") or item.get("deliver_after") or effective_now_iso,
                    "claimed_at": final_claimed_at,
                    "delivered_at": final_delivered_at,
                    "read_at": final_read_at,
                    "updated_at": effective_now_iso,
                }
                if final_receipt_id is not None:
                    rec["transport_receipt_id"] = final_receipt_id
                _upsert_delivered_record(rec)
                final_status = "READ"

        state["pending_messages"] = pending_list
        state["delivered_replies"] = delivered_list
        if not any(
            m.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
            for m in pending_list
        ):
            state["readiness_promoted"] = False
            state["availability"] = avail_info["availability"]

        next_rev = int(state.get("revision") or 0) + 1
        state["revision"] = next_rev
        state["updated_at"] = effective_now_iso
        ready_after = sum(
            1
            for m in pending_list
            if m.get("status") in ("READY_TO_REPLY", "CLAIMED_BY_TRANSPORT")
        )
        receipt_out: dict[str, Any] = {
            "ok": True,
            "item_id": clean_item_id,
            "msg_id": clean_item_id,
            "item_kind": "deferred_reply_ready",
            "action": clean_action,
            "operation_key": clean_op_key,
            "status": final_status,
            "item_status": final_status,
            "transport_receipt_id": final_receipt_id,
            "claimed_at": final_claimed_at,
            "delivered_at": final_delivered_at,
            "read_at": final_read_at,
            "replayed": False,
            "idempotent_replay": False,
            "changed": True,
            "revision": next_rev,
            "updated_at": effective_now_iso,
            "pending_count": len(pending_list),
            "ready_count": ready_after,
            "llm_calls": 0,
        }
        next_op_seq = max(
            (int((v or {}).get("op_seq") or 0) for v in ack_ops.values()),
            default=0,
        ) + 1
        ack_ops[clean_op_key] = {
            "item_id": clean_item_id,
            "action": clean_action,
            "status": final_status,
            "op_seq": next_op_seq,
            "at": effective_now_iso,
            "receipt": copy.deepcopy(receipt_out),
        }
        state["ack_operations"] = _trim_ack_operations(ack_ops)
        write_autonomy_rhythm_state(root_path, state)
        return receipt_out


def _build_shift_guidance(
    *, name: str, prev_label: str, new_label: str, selected: str
) -> str:
    if selected == "FINISH_THEN_FOLLOW":
        return (
            f"对方突然从「{prev_label}」跳去聊「{new_label}」，{name}不要瞬间失忆秒跟："
            f"先把刚才「{prev_label}」那半句话自然收个尾，再接新话题（不审问、不指责换话题）。"
        )
    if selected == "ASK_ABOUT_JUMP":
        return (
            f"对方上一秒还在说「{prev_label}」，突然拐去问「{new_label}」，{name}带着刚才的余温"
            "挑眉顺口问一句怎么突然跳到这个上了，然后再聊，不指责不记账。"
        )
    if selected == "FOLLOW_AND_RETURN_LATER":
        return (
            f"对方从「{prev_label}」突然换到「{new_label}」，{name}先顺着答眼前这句，"
            f"但心里还挂着「{prev_label}」，留着余温等空档最多绕回来提一次。"
        )
    return f"{name}自然接上「{new_label}」。"
