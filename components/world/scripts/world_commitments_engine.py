#!/usr/bin/env python3
"""v0.9 Pillar C -- Zero-Idle-Spin Commitment-Driven NPC & Event Engine.

Architecture & Contracts (Master Blueprint §3.1-3.3):
- State file: ``<day_root>/world/npc_commitments_state.json``
  (``SCHEMA = "world.v09.npc_commitments.v1"``), rooted strictly via
  ``instance_paths.resolve_instance_paths(day_root).day_root``.
- Instance-Bound Persistent NPC Unification:
  Binds 4 canonical neighborhood roles (``bookstore_keeper``, ``tea_bakery_owner``,
  ``grocer_post_clerk``, ``craft_regular``) to instance-seeded persistent identities
  by adopting WorldGen ``npcs.json`` when present or deterministically deriving
  instance-specific identities from
  ``sha256(f"{instance_seed}|{world_id}|{theme}|{role_slot}")`` while mapping
  v0.3 (``npc_<theme>_*``) and v0.5 (``auntie_lin``, ``uncle_zhou``, etc.)
  ``legacy_alias_ids`` cleanly to one unified NPC record.
- Zero-Idle-Spin & O(1) Heartbeat Skip:
  ``check_heartbeat_needed_zero_write`` returns ``False`` immediately (0 writes)
  when the state file is absent OR when no commitment is due and weather has not
  materially affected an open weather-sensitive commitment.
  ``derive_npc_presence_zero_write`` computes deterministic presence from weekly
  windows and active commitments with 0 disk writes and 0 LLM calls.
- Three Durable Commitment Kinds:
  1. ``BOOK_OR_ITEM_RESERVATION``: ``ORDERED`` -> ``ARRIVED_HOLDING`` ->
     ``PICKED_UP`` | ``HOLD_EXPIRED`` (optionally releasing item to
     ``world_content_catalog.register_catalog_batch`` with honest notice
     ``"到货保留期满未取，已转上架"``) | ``DELAYED_RESCHEDULED``.
  2. ``SCHEDULED_APPOINTMENT``: ``SCHEDULED`` -> ``ACTIVE_WINDOW`` ->
     ``FULFILLED`` | ``MISSED_BY_SUBJECT`` | ``CANCELLED_WITH_NOTICE`` |
     ``RESCHEDULED_INDOOR``.
  3. ``INDEPENDENT_NPC_PLAN``: ``PLANNED`` -> ``IN_PROGRESS`` ->
     ``COMPLETED`` | ``CANCELLED_BY_WEATHER`` with ``effect_kind`` in
     ``("TEMPORARY_CLOSE", "NEW_TOPIC_READY", "LIMITED_BATCH_ARRIVAL", "OUTDOOR_STALL")``.
- Three Wake Triggers:
  1. ``DUE_TIME`` -> ``advance_due_commitments``
  2. ``CONDITION_CHANGED`` -> ``notify_world_condition_changed``
  3. ``DIRECT_CONTACT`` -> ``contact_npc_or_venue`` (enforces
     ``400 RETROACTIVE_FABRICATION_FORBIDDEN`` against smuggled mood/desire/chat
     keys, fast-forwards strictly from persisted commitments, and updates
     epistemic ``subject_observed_venues``).
- Zero-Write Read View (``read_world_commitments_zero_write``):
  Enforces ``load_unified_forget_barrier_readonly(day_root)`` (fail-closed) and
  epistemic ``subject_observed_venues`` (``observed_by_subject: bool``), with 0
  disk writes and 0 LLM calls.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORLD_SCRIPTS_DIR.parents[2]
_LIFE_SCRIPTS_DIR = _REPO_ROOT / "components" / "life" / "scripts"
for _p in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR), str(_LIFE_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat script import fallback
    import state_tx_guard as _tx  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat script import fallback
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Public Schema, Constants & Error Type
# ---------------------------------------------------------------------------

SCHEMA = "world.v09.npc_commitments.v1"
STATE_SCHEMA = SCHEMA
STATE_REL_PATH: tuple[str, ...] = ("world", "npc_commitments_state.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_COMMITMENTS = 64
MAX_VENUE_NOTICES = 24
MAX_OPERATION_RECEIPTS = 200
MAX_HISTORY_PER_COMMITMENT = 24
DEFAULT_RESERVATION_HOLD_HOURS = 72
DEFAULT_DELAY_RESCHEDULE_HOURS = 24
DEFAULT_APPOINTMENT_DURATION_MINUTES = 60
DEFAULT_PLAN_DURATION_HOURS = 4
DEFAULT_DECISION_EST_TOKENS = 600

HOLD_EXPIRED_NOTICE_CN = "到货保留期满未取，已转上架"

CANONICAL_ROLE_SLOTS: tuple[str, ...] = (
    "bookstore_keeper",
    "tea_bakery_owner",
    "grocer_post_clerk",
    "craft_regular",
)

COMMITMENT_KINDS: tuple[str, ...] = (
    "BOOK_OR_ITEM_RESERVATION",
    "SCHEDULED_APPOINTMENT",
    "INDEPENDENT_NPC_PLAN",
)

INDEPENDENT_PLAN_EFFECT_KINDS: tuple[str, ...] = (
    "TEMPORARY_CLOSE",
    "NEW_TOPIC_READY",
    "LIMITED_BATCH_ARRIVAL",
    "OUTDOOR_STALL",
)

CONTACT_ACTIONS: tuple[str, ...] = (
    "observe_or_greet",
    "pickup_reservation",
    "fulfill_appointment",
)

RESERVATION_OPEN_STATES: frozenset[str] = frozenset({
    "ORDERED",
    "DELAYED_RESCHEDULED",
    "ARRIVED_HOLDING",
})
RESERVATION_TERMINAL_STATES: frozenset[str] = frozenset({
    "PICKED_UP",
    "HOLD_EXPIRED",
    "CANCELLED_WITH_NOTICE",
})

APPOINTMENT_OPEN_STATES: frozenset[str] = frozenset({
    "SCHEDULED",
    "ACTIVE_WINDOW",
    "RESCHEDULED_INDOOR",
})
APPOINTMENT_TERMINAL_STATES: frozenset[str] = frozenset({
    "FULFILLED",
    "MISSED_BY_SUBJECT",
    "CANCELLED_WITH_NOTICE",
})

PLAN_OPEN_STATES: frozenset[str] = frozenset({
    "PLANNED",
    "IN_PROGRESS",
})
PLAN_TERMINAL_STATES: frozenset[str] = frozenset({
    "COMPLETED",
    "CANCELLED_BY_WEATHER",
})

TERMINAL_COMMITMENT_STATES: frozenset[str] = (
    RESERVATION_TERMINAL_STATES | APPOINTMENT_TERMINAL_STATES | PLAN_TERMINAL_STATES
)

RAIN_AND_SEVERE_WEATHERS: frozenset[str] = frozenset({
    "rain",
    "heavy_rain",
    "rainy",
    "shower",
    "drizzle",
    "storm",
    "thunderstorm",
    "thunder_blackout",
    "sea_gale",
    "typhoon",
    "snowstorm",
    "humid_plum_rain",
})

FORBIDDEN_RETROACTIVE_KEYS: frozenset[str] = frozenset({
    "subject_mood",
    "private_desire",
    "private_desires",
    "recent_chat_topic",
    "recent_chat_topics",
    "fabricate_backstory",
    "retroactive_event",
    "retroactive_events",
})

_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")

_VENUE_CANONICAL_MAP: dict[str, str] = {
    "bookstore": "bookstore",
    "old_bookshop": "bookstore",
    "bookshop": "bookstore",
    "cafe": "cafe",
    "bakery_corner": "cafe",
    "bakery": "cafe",
    "tea_shop": "cafe",
    "tea_bakery": "cafe",
    "home_goods_store": "home_goods_store",
    "convenience_store": "home_goods_store",
    "post_office": "home_goods_store",
    "street_corner": "home_goods_store",
    "craft_corner": "craft_corner",
    "coin_laundromat": "craft_corner",
    "blank_grove": "craft_corner",
    "porch_market": "porch_market",
}

_VENUE_EQUIV_GROUPS: dict[str, tuple[str, ...]] = {
    "bookstore": ("bookstore", "old_bookshop", "bookshop"),
    "cafe": ("cafe", "bakery_corner", "bakery", "tea_shop"),
    "home_goods_store": ("home_goods_store", "convenience_store", "post_office", "street_corner"),
    "craft_corner": ("craft_corner", "coin_laundromat", "blank_grove"),
    "porch_market": ("porch_market",),
}

# Neutral Chinese surname & given-name pools (zero forbidden characters, zero emojis).
_SURNAMES: tuple[str, ...] = (
    "林", "周", "沈", "苏", "陆", "温", "顾", "许",
    "唐", "谢", "韩", "宋", "梁", "程", "叶", "白",
    "江", "方", "秦", "贺", "严", "高", "罗", "夏",
)

_GIVEN_NAMES_BY_ROLE: dict[str, tuple[str, ...]] = {
    "bookstore_keeper": (
        "知远", "松柏", "砚清", "守一", "文澜", "书衡", "明川", "敬之",
        "怀安", "景行", "竹青", "序白",
    ),
    "tea_bakery_owner": (
        "澄心", "麦禾", "知夏", "暖云", "早苗", "采薇", "青禾", "秋实",
        "疏影", "清和", "素兰", "桂枝",
    ),
    "grocer_post_clerk": (
        "致远", "正明", "平川", "安澜", "稳舟", "诚之", "良平", "德厚",
        "康宁", "润生", "常青", "长风",
    ),
    "craft_regular": (
        "青叶", "奈绪", "木棉", "星野", "素秋", "砚舟", "浅川", "微澜",
        "拾光", "晴岚", "桑榆", "栖迟",
    ),
}

_ROLE_SPECS: dict[str, dict[str, Any]] = {
    "bookstore_keeper": {
        "venue_id": "bookstore",
        "venue_aliases": ("bookstore", "old_bookshop", "bookshop"),
        "legacy_alias_ids": (
            "uncle_zhou",
            "npc_coastal_bookstore",
            "npc_mountain_bookstore",
            "npc_residential_bookstore",
            "npc_rural_bookstore",
            "npc_gentle_bookstore",
        ),
        "occupations": ("旧书店店主", "街角书铺掌柜", "二手书屋店主"),
        "shop_prefixes": {
            "coastal": ("潮声", "港湾", "灯塔下", "海风"),
            "mountain": ("雾杉", "松阴", "石阶", "云岭"),
            "residential": ("银杏", "檐下", "梧桐", "巷口"),
            "rural": ("田畦", "仓房", "竹篱", "溪畔"),
            "urban": ("转角", "深巷", "红砖", "晴窗"),
            "gentle": ("梧桐", "暖灯", "旧檐", "青苔"),
        },
        "shop_suffix": "旧书铺",
        "projects": (
            "整理刚收进店的一箱旧画册并重贴牛皮纸书标",
            "修补几册绝版地方志的装订线",
            "按开本重排窗边散文与图鉴书架",
            "誊抄本月新到旧书的柜台目录簿",
        ),
        "closed_notes": (
            "旧书铺木牌翻到了店休，店主外出收书或去邮亭寄件了。",
            "书铺已落锁，门环上挂着次日开门的小木牌。",
        ),
        "base_open_min": 10 * 60,
        "base_close_min": 19 * 60,
        "default_rest_weekday": 2,
        "keywords": ("bookstore", "old_bookshop", "bookshop", "书店", "书铺", "书屋", "图书"),
    },
    "tea_bakery_owner": {
        "venue_id": "cafe",
        "venue_aliases": ("cafe", "bakery_corner", "bakery", "tea_shop"),
        "legacy_alias_ids": (
            "auntie_lin",
            "npc_coastal_tea_bakery",
            "npc_mountain_tea_bakery",
            "npc_residential_tea_bakery",
            "npc_rural_tea_bakery",
            "npc_gentle_tea_bakery",
        ),
        "occupations": ("街角茶铺与烘焙坊店主", "暖炉面包与焙茶店主", "手工茶点铺老板"),
        "shop_prefixes": {
            "coastal": ("海风", "盐粒", "防波堤", "白浪"),
            "mountain": ("石阶", "山泉", "杉叶", "云雾"),
            "residential": ("麦香", "拐角", "晨光", "暖炉"),
            "rural": ("晒谷场", "麦穗", "稻花", "青石"),
            "urban": ("巷口", "慢火", "微光", "原麦"),
            "gentle": ("街角", "暖光", "素瓷", "清茶"),
        },
        "shop_suffix": "茶点烘焙铺",
        "projects": (
            "试烤新一批低糖核桃吐司并校准茶炉火候",
            "翻焙当季浅火茎茶并分装牛皮纸小袋",
            "记录不同湿度下面团发酵时间的变化",
            "给常客预订的季节茶罐手写批次日期签",
        ),
        "closed_notes": (
            "茶点铺已经收起遮阳棚打烊，只留柜台边一盏暖黄小灯。",
            "面包与焙茶今日售罄收店，明日清晨重新生炉。",
        ),
        "base_open_min": 7 * 60 + 30,
        "base_close_min": 17 * 60 + 30,
        "default_rest_weekday": None,
        "keywords": ("cafe", "bakery", "tea", "面包", "烘焙", "茶铺", "茶点", "咖啡"),
    },
    "grocer_post_clerk": {
        "venue_id": "home_goods_store",
        "venue_aliases": ("home_goods_store", "convenience_store", "post_office", "street_corner"),
        "legacy_alias_ids": (
            "keeper_he",
            "feeder_su",
            "npc_coastal_post_grocer",
            "npc_mountain_post_grocer",
            "npc_residential_post_grocer",
            "npc_rural_post_grocer",
            "npc_gentle_post_grocer",
        ),
        "occupations": ("邮局兼日用杂货街坊", "街角杂货与邮务员", "社区日用邮亭掌柜"),
        "shop_prefixes": {
            "coastal": ("港町", "渡口", "礁石口", "白帆"),
            "mountain": ("溪桥", "山麓", "松路口", "石桥"),
            "residential": ("商店街", "巷口", "北街", "转角"),
            "rural": ("村道", "镇口", "樟树下", "河畔"),
            "urban": ("老街", "东口", "里弄", "车站边"),
            "gentle": ("巷口", "青石", "邮铃", "常青"),
        },
        "shop_suffix": "杂货邮亭",
        "projects": (
            "核对午班送达的日用杂货与街坊包裹单",
            "清点新进粗陶杯、棉线与牛皮纸信封库存",
            "整理柜台下代收的平信与明信片邮戳",
            "给门口遮雨棚重新绑紧防风绳并擦拭货架",
        ),
        "closed_notes": (
            "杂货邮亭铁拉门已合上，值班窗口明日清晨再开。",
            "邮亭已过营业时间，门边只留自助投递信箱。",
        ),
        "base_open_min": 8 * 60 + 30,
        "base_close_min": 18 * 60,
        "default_rest_weekday": 7,
        "keywords": ("home_goods_store", "convenience_store", "post", "grocer", "杂货", "邮", "便利", "日用"),
    },
    "craft_regular": {
        "venue_id": "craft_corner",
        "venue_aliases": ("craft_corner", "coin_laundromat", "blank_grove", "porch_market"),
        "legacy_alias_ids": (
            "student_chen",
            "npc_coastal_craft_regular",
            "npc_mountain_craft_regular",
            "npc_residential_craft_regular",
            "npc_rural_craft_regular",
            "npc_gentle_craft_regular",
        ),
        "occupations": ("手作工坊常客", "器物与纸品手作人", "街角市集手作常客"),
        "shop_prefixes": {
            "coastal": ("坡道", "海玻璃", "贝壳", "晴风"),
            "mountain": ("榧木", "山风", "林间", "苔石"),
            "residential": ("纸屋", "素布", "晴窗", "木案"),
            "rural": ("溪畔", "竹编", "蓝染", "原木"),
            "urban": ("阁楼", "拾物", "安静", "印记"),
            "gentle": ("晴窗", "素陶", "亚麻", "拾光"),
        },
        "shop_suffix": "手作工坊",
        "projects": (
            "给打磨好的小物配齐亚麻绳与素色纸签",
            "测试不同粗细砂纸在旧木茶则上的手感",
            "裁切和纸书衣并准备周末露台小物交换桌",
            "修补一只旧陶杯垫的棉线滚边",
        ),
        "closed_notes": (
            "手作工坊这会儿拉着布帘，常客大概外出散步采风了。",
            "工位上整整齐齐摆着工具，此刻没有人在工坊里。",
        ),
        "base_open_min": 13 * 60,
        "base_close_min": 20 * 60,
        "default_rest_weekday": 1,
        "keywords": ("craft", "laundromat", "park", "market", "手作", "工坊", "常客", "市集"),
    },
}


class WorldCommitmentsError(ValueError):
    """Domain or validation error carrying HTTP status code and machine reason code."""

    def __init__(self, status_code: int, reason_code: str, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.message = self.detail
        super().__init__(self.detail)


# ---------------------------------------------------------------------------
# Internal Helpers: Time, Paths, Hygiene, Atomic I/O & Symlink Guard
# ---------------------------------------------------------------------------


def _strip_emojis_and_ctrl(text: str, max_len: int = 240) -> str:
    cleaned: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip()[:max_len]


def _clean_text_field(
    raw: Any,
    *,
    field_name: str,
    min_len: int = 0,
    max_len: int = 240,
) -> str:
    if raw is None:
        if min_len > 0:
            raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Field {field_name!r} is required")
        return ""
    if not isinstance(raw, str):
        raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned = _strip_emojis_and_ctrl(raw, max_len=max_len)
    if len(cleaned) < min_len:
        raise WorldCommitmentsError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at least {min_len} characters",
        )
    return cleaned


def _validate_operation_key(operation_key: Any) -> Optional[str]:
    if operation_key is None:
        return None
    if not isinstance(operation_key, str):
        raise WorldCommitmentsError(400, "INVALID_FIELDS", "Field 'operation_key' must be a string")
    raw = operation_key.strip()
    if len(raw) > 128:
        raise WorldCommitmentsError(400, "INVALID_FIELDS", "Field 'operation_key' is too long")
    cleaned = _strip_emojis_and_ctrl(raw, max_len=128)
    return cleaned or None


def _normalize_actor_side(actor_side: Any) -> str:
    norm = str(actor_side or "partner").strip().lower()
    if norm not in ("subject", "partner"):
        raise WorldCommitmentsError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )
    return norm


def _parse_dt(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_TZ)
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).isoformat(timespec="seconds")


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    if now_iso is not None:
        dt = _parse_dt(now_iso)
        if dt is None:
            raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Invalid ISO timestamp: {now_iso!r}")
        return dt, _format_iso(dt)
    dt = datetime.now(tz=DEFAULT_TZ)
    return dt, _format_iso(dt)


def _normalize_weather(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return _strip_emojis_and_ctrl(raw, max_len=48).lower().replace(" ", "_")


def _is_regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _is_real_dir(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_dir()
    except OSError:
        return False


def _read_json_readonly(path: Path) -> Optional[dict[str, Any]]:
    if not _is_regular_file(path):
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        return dict(doc) if isinstance(doc, Mapping) else None
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None


def _resolve_day_root(day_root: Path | str) -> Path:
    return _instance_paths.resolve_instance_paths(day_root).day_root


def commitments_state_path(day_root: Path | str) -> Path:
    """Return canonical ``<day_root>/world/npc_commitments_state.json`` path (zero-write)."""
    return _resolve_day_root(day_root).joinpath(*STATE_REL_PATH)


def _assert_no_symlink_chain(raw_input: Path | str, resolved_day_root: Path, target_file: Path) -> None:
    raw_path = Path(str(raw_input)).expanduser()
    lock_path = Path(str(target_file) + ".lock")
    for label, cand in (
        ("input_path", raw_path),
        ("day_root", resolved_day_root),
        ("world_dir", target_file.parent),
        ("state_file", target_file),
        ("lock_file", lock_path),
    ):
        try:
            if cand.is_symlink():
                raise WorldCommitmentsError(400, "UNSAFE_SYMLINK", f"{label} must not be a symlink")
        except OSError as exc:
            raise WorldCommitmentsError(400, "UNSAFE_SYMLINK", f"Cannot inspect {label}: {exc}") from exc


def _atomic_write_json_0600(
    raw_input: Path | str,
    resolved_day_root: Path,
    target_file: Path,
    payload: Mapping[str, Any],
) -> None:
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    try:
        if os.name == "posix":
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(str(tmp_path), str(target_file))
    except Exception:
        try:
            if tmp_path.exists() or tmp_path.is_symlink():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _canonical_venue(venue_id: Optional[str]) -> str:
    raw = _strip_emojis_and_ctrl(str(venue_id or ""), max_len=64).strip()
    if not raw:
        return ""
    low = raw.lower()
    return _VENUE_CANONICAL_MAP.get(low, raw)


def _venue_aliases_for(venue_id: str) -> tuple[str, ...]:
    canon = _canonical_venue(venue_id)
    if canon in _VENUE_EQUIV_GROUPS:
        return _VENUE_EQUIV_GROUPS[canon]
    return (canon,) if canon else ()


def _venues_match(venue_a: str, venue_b: str) -> bool:
    if not venue_a or not venue_b:
        return False
    return _canonical_venue(venue_a) == _canonical_venue(venue_b)


def _minutes_to_hhmm(minutes: int) -> str:
    clamped = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{clamped // 60:02d}:{clamped % 60:02d}"


def _hhmm_to_minutes(hhmm: str) -> Optional[int]:
    if not isinstance(hhmm, str) or ":" not in hhmm:
        return None
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    if h == 24 and m == 0:
        return 24 * 60
    return None


def _normalize_theme_family(raw_theme: str) -> str:
    low = str(raw_theme or "").strip().lower()
    if "coast" in low or "sea" in low or "harbor" in low:
        return "coastal"
    if "mount" in low or "hill" in low or "valley" in low:
        return "mountain"
    if "rural" in low or "farm" in low or "village" in low:
        return "rural"
    if "urban" in low or "city" in low or "apart" in low:
        return "urban"
    if "resid" in low or "suburb" in low:
        return "residential"
    return "gentle"


def _scan_forbidden_retroactive_keys(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key_str = str(raw_key).strip()
            low_key = key_str.lower()
            full_path = f"{path}.{key_str}" if path else key_str
            if low_key in FORBIDDEN_RETROACTIVE_KEYS:
                return full_path
            found = _scan_forbidden_retroactive_keys(nested, full_path)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            found = _scan_forbidden_retroactive_keys(item, f"{path}[{idx}]")
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Instance Metadata & Persistent NPC Unification (100% Zero-Write)
# ---------------------------------------------------------------------------


def _find_world_package_dir_zero_write(paths: _instance_paths.InstancePaths) -> Optional[Path]:
    candidates: list[Path] = []
    if paths.world_package_dir is not None:
        candidates.append(paths.world_package_dir)
    day_root = paths.day_root
    candidates.extend([
        day_root / "one-day" / "world-package",
        day_root / "world-package",
        day_root / "one-day" / "world_package",
        day_root / "world_package",
        paths.world_home / "world-package",
        paths.world_home / "world_package",
    ])
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _is_real_dir(cand) and (
            _is_regular_file(cand / "world.json") or _is_regular_file(cand / "npcs.json")
        ):
            return cand
    return paths.world_package_dir if (paths.world_package_dir and _is_real_dir(paths.world_package_dir)) else None


def _read_instance_binding_zero_write(day_root: Path | str) -> dict[str, Any]:
    paths = _instance_paths.resolve_instance_paths(day_root)
    root = paths.day_root
    pkg_dir = _find_world_package_dir_zero_write(paths)

    managed_doc = _read_json_readonly(root / "run" / "nyairo-day.instance.json") or {}
    inst_doc = _read_json_readonly(paths.instance_json) if paths.instance_json is not None else None
    if inst_doc is None:
        inst_doc = (
            _read_json_readonly(root / "instance.json")
            or _read_json_readonly(root / "phone" / "instance.json")
            or {}
        )
    nested_inst_doc = _read_json_readonly(root / "one-day" / "instance.json") or {}
    world_doc = _read_json_readonly(pkg_dir / "world.json") if pkg_dir is not None else None
    world_doc = world_doc or {}
    world_state_doc = _read_json_readonly(paths.world_state_json) or {}

    managed_pkg = managed_doc.get("world_package")
    managed_pkg_map: Mapping[str, Any] = managed_pkg if isinstance(managed_pkg, Mapping) else {}
    inst_pkg = inst_doc.get("world_package")
    inst_pkg_map: Mapping[str, Any] = inst_pkg if isinstance(inst_pkg, Mapping) else {}

    def _first_nonempty(*vals: Any) -> str:
        for v in vals:
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return str(v)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    world_id = _first_nonempty(
        world_doc.get("world_id"),
        managed_pkg_map.get("world_id"),
        inst_pkg_map.get("world_id"),
        inst_doc.get("world_id"),
        nested_inst_doc.get("world_id"),
        world_state_doc.get("world_id"),
        "default_town",
    )
    theme = _first_nonempty(
        world_doc.get("theme"),
        managed_pkg_map.get("theme"),
        inst_pkg_map.get("theme"),
        managed_doc.get("theme"),
        inst_doc.get("theme"),
        nested_inst_doc.get("theme"),
        world_state_doc.get("theme"),
        "gentle_neighborhood",
    )
    instance_seed = _first_nonempty(
        managed_doc.get("instance_seed"),
        inst_doc.get("instance_seed"),
        nested_inst_doc.get("instance_seed"),
        world_doc.get("instance_seed"),
        world_doc.get("seed"),
        managed_pkg_map.get("seed"),
        inst_pkg_map.get("seed"),
        managed_doc.get("seed"),
        inst_doc.get("seed"),
        nested_inst_doc.get("seed"),
        world_state_doc.get("seed"),
        managed_doc.get("instance_id"),
        inst_doc.get("instance_id"),
        nested_inst_doc.get("instance_id"),
        managed_doc.get("run_id"),
        inst_doc.get("run_id"),
        nested_inst_doc.get("run_id"),
        world_id if world_id != "default_town" else "",
        "default_seed",
    )
    seed_fingerprint = hashlib.sha256(
        f"{instance_seed}|{world_id}|{theme}".encode("utf-8")
    ).hexdigest()[:16]

    return {
        "day_root": str(root),
        "world_package_dir": str(pkg_dir) if pkg_dir is not None else None,
        "instance_seed": instance_seed,
        "instance_seed_fingerprint": seed_fingerprint,
        "world_id": world_id,
        "theme": theme,
        "theme_family": _normalize_theme_family(theme),
    }


def _match_worldgen_npc_to_role(raw_npc: Mapping[str, Any], taken_roles: set[str]) -> Optional[str]:
    home_area = str(raw_npc.get("home_base_area") or "").strip().lower()
    role_text = str(raw_npc.get("public_role") or raw_npc.get("occupation") or "").strip().lower()
    desc_text = str(raw_npc.get("public_description") or "").strip().lower()
    window_locs: list[str] = []
    raw_windows = raw_npc.get("windows")
    if isinstance(raw_windows, Sequence) and not isinstance(raw_windows, (str, bytes)):
        for win in raw_windows:
            if isinstance(win, Mapping):
                loc = str(win.get("location_id") or "").strip().lower()
                if loc:
                    window_locs.append(loc)
    haystack = " ".join([home_area, role_text, desc_text, *window_locs])

    for role_slot in CANONICAL_ROLE_SLOTS:
        if role_slot in taken_roles:
            continue
        spec = _ROLE_SPECS[role_slot]
        if any(kw in haystack for kw in spec["keywords"]):
            return role_slot
    return None


def _convert_worldgen_windows(raw_windows: Any, default_venue_id: str) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if isinstance(raw_windows, Sequence) and not isinstance(raw_windows, (str, bytes)):
        for win in raw_windows:
            if not isinstance(win, Mapping):
                continue
            raw_days = win.get("weekdays")
            weekdays = sorted({
                int(d)
                for d in (raw_days or [1, 2, 3, 4, 5, 6, 7])
                if isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 7
            })
            if not weekdays:
                weekdays = [1, 2, 3, 4, 5, 6, 7]
            if isinstance(win.get("start_hhmm"), str) and isinstance(win.get("end_hhmm"), str):
                start_hhmm = win["start_hhmm"]
                end_hhmm = win["end_hhmm"]
            else:
                try:
                    raw_sm = win.get("start_minute")
                    start_m = 9 * 60 if raw_sm is None or isinstance(raw_sm, bool) else int(raw_sm)
                except (TypeError, ValueError):
                    start_m = 9 * 60
                try:
                    raw_em = win.get("end_minute")
                    end_m = 18 * 60 if raw_em is None or isinstance(raw_em, bool) else int(raw_em)
                except (TypeError, ValueError):
                    end_m = 18 * 60
                start_hhmm = _minutes_to_hhmm(start_m)
                end_hhmm = _minutes_to_hhmm(end_m)
            loc = _strip_emojis_and_ctrl(str(win.get("location_id") or default_venue_id), max_len=64)
            converted.append({
                "weekdays": weekdays,
                "start_hhmm": start_hhmm,
                "end_hhmm": end_hhmm,
                "venue_id": loc or default_venue_id,
            })
    return converted


def _derive_seeded_npc_for_role(
    *,
    role_slot: str,
    instance_seed: str,
    world_id: str,
    theme: str,
    theme_family: str,
    used_names: set[str],
) -> dict[str, Any]:
    spec = _ROLE_SPECS[role_slot]
    seed_material = f"{instance_seed}|{world_id}|{theme}|{role_slot}"
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    h0 = int(digest[0:8], 16)
    h1 = int(digest[8:16], 16)
    h2 = int(digest[16:24], 16)
    h3 = int(digest[24:32], 16)

    given_pool = _GIVEN_NAMES_BY_ROLE[role_slot]
    surname_idx = h0 % len(_SURNAMES)
    given_idx = h1 % len(given_pool)
    display_name = f"{_SURNAMES[surname_idx]}{given_pool[given_idx]}"
    bump = 1
    while display_name in used_names and bump <= len(_SURNAMES):
        display_name = f"{_SURNAMES[(surname_idx + bump) % len(_SURNAMES)]}{given_pool[(given_idx + bump) % len(given_pool)]}"
        bump += 1
    used_names.add(display_name)

    occupations = spec["occupations"]
    occupation = occupations[h2 % len(occupations)]

    prefixes_by_theme = spec["shop_prefixes"]
    prefixes = prefixes_by_theme.get(theme_family, prefixes_by_theme["gentle"])
    shop_name = f"{prefixes[h1 % len(prefixes)]}{spec['shop_suffix']}"

    projects = spec["projects"]
    personal_project = projects[h3 % len(projects)]
    closed_notes = spec["closed_notes"]
    closed_note = closed_notes[(h2 >> 3) % len(closed_notes)]

    # Slight deterministic schedule offsets (+/- 0, 15, 30, 45 minutes) so instances vary organically.
    offset_choices = (-30, -15, 0, 15, 30)
    open_offset = offset_choices[h0 % len(offset_choices)]
    close_offset = offset_choices[(h1 >> 4) % len(offset_choices)]
    open_min = max(6 * 60, spec["base_open_min"] + open_offset)
    close_min = min(22 * 60, max(open_min + 4 * 60, spec["base_close_min"] + close_offset))

    rest_day = spec["default_rest_weekday"]
    if rest_day is not None:
        rest_choices = (1, 2, 3, 7)
        rest_day = rest_choices[(h3 >> 5) % len(rest_choices)]
        weekdays = [d for d in range(1, 8) if d != rest_day]
    else:
        weekdays = [1, 2, 3, 4, 5, 6, 7]

    venue_id = str(spec["venue_id"])
    npc_id = f"npc_{role_slot}_{digest[:8]}"
    legacy_aliases = list(dict.fromkeys([*spec["legacy_alias_ids"], role_slot]))

    return {
        "npc_id": npc_id,
        "role_slot": role_slot,
        "display_name": display_name,
        "occupation": occupation,
        "shop_name": shop_name,
        "venue_id": venue_id,
        "venue_aliases": list(spec["venue_aliases"]),
        "legacy_alias_ids": legacy_aliases,
        "open_windows": [
            {
                "weekdays": weekdays,
                "start_hhmm": _minutes_to_hhmm(open_min),
                "end_hhmm": _minutes_to_hhmm(close_min),
                "venue_id": venue_id,
            }
        ],
        "personal_project_cn": personal_project,
        "closed_note_cn": closed_note,
        "source_kind": "instance_seeded",
        "seed_fingerprint": digest[:16],
    }


def resolve_instance_npc_roster_zero_write(day_root: Path | str) -> list[dict[str, Any]]:
    """Resolve the 4 canonical instance-bound persistent NPCs (100% zero-write, 0 LLM calls).

    Reads ``nyairo-day.instance.json`` / ``instance.json`` and
    ``<day_root>/one-day/world-package/world.json`` + ``npcs.json`` (with
    ``<day_root>/world-package/...`` fallback). Adopts matching WorldGen NPCs when
    present; otherwise deterministically derives distinct instance-specific
    identities from ``sha256(f"{instance_seed}|{world_id}|{theme}|{role_slot}")``.
    """
    binding = _read_instance_binding_zero_write(day_root)
    pkg_dir_str = binding.get("world_package_dir")
    npcs_doc = _read_json_readonly(Path(pkg_dir_str) / "npcs.json") if pkg_dir_str else None
    raw_wg_npcs = npcs_doc.get("npcs") if isinstance(npcs_doc, Mapping) else None

    adopted_by_role: dict[str, dict[str, Any]] = {}
    used_names: set[str] = set()
    taken_roles: set[str] = set()

    if isinstance(raw_wg_npcs, Sequence) and not isinstance(raw_wg_npcs, (str, bytes)):
        for item in raw_wg_npcs:
            if not isinstance(item, Mapping):
                continue
            role_slot = _match_worldgen_npc_to_role(item, taken_roles)
            if role_slot is None:
                continue
            taken_roles.add(role_slot)
            spec = _ROLE_SPECS[role_slot]
            wg_npc_id = _strip_emojis_and_ctrl(str(item.get("npc_id") or ""), max_len=64)
            display_name = _strip_emojis_and_ctrl(str(item.get("display_name") or ""), max_len=48)
            if not display_name:
                continue
            used_names.add(display_name)
            raw_venue = _strip_emojis_and_ctrl(
                str(item.get("home_base_area") or spec["venue_id"]),
                max_len=64,
            )
            venue_id = raw_venue or str(spec["venue_id"])
            converted_windows = _convert_worldgen_windows(item.get("windows"), venue_id)
            if not converted_windows:
                converted_windows = [
                    {
                        "weekdays": [1, 2, 3, 4, 5, 6, 7],
                        "start_hhmm": _minutes_to_hhmm(spec["base_open_min"]),
                        "end_hhmm": _minutes_to_hhmm(spec["base_close_min"]),
                        "venue_id": venue_id,
                    }
                ]
            digest = hashlib.sha256(
                f"{binding['instance_seed']}|{binding['world_id']}|{binding['theme']}|{role_slot}".encode("utf-8")
            ).hexdigest()
            occupation = _strip_emojis_and_ctrl(
                str(item.get("public_role") or item.get("occupation") or spec["occupations"][0]),
                max_len=64,
            )
            desc = _strip_emojis_and_ctrl(
                str(item.get("public_description") or spec["projects"][int(digest[:8], 16) % len(spec["projects"])]),
                max_len=160,
            )
            shop_name = _strip_emojis_and_ctrl(
                str(item.get("shop_name") or f"{display_name}的{spec['shop_suffix']}"),
                max_len=64,
            )
            npc_id = wg_npc_id or f"npc_{role_slot}_{digest[:8]}"
            aliases = list(dict.fromkeys([
                *spec["legacy_alias_ids"],
                role_slot,
                *([] if not wg_npc_id or wg_npc_id == npc_id else [wg_npc_id]),
            ]))
            venue_aliases = list(dict.fromkeys([venue_id, *spec["venue_aliases"]]))
            adopted_by_role[role_slot] = {
                "npc_id": npc_id,
                "role_slot": role_slot,
                "display_name": display_name,
                "occupation": occupation,
                "shop_name": shop_name,
                "venue_id": venue_id,
                "venue_aliases": venue_aliases,
                "legacy_alias_ids": aliases,
                "open_windows": converted_windows,
                "personal_project_cn": desc,
                "closed_note_cn": spec["closed_notes"][0],
                "source_kind": "worldgen_adopted",
                "seed_fingerprint": digest[:16],
            }

    roster: list[dict[str, Any]] = []
    for role_slot in CANONICAL_ROLE_SLOTS:
        if role_slot in adopted_by_role:
            roster.append(adopted_by_role[role_slot])
        else:
            roster.append(
                _derive_seeded_npc_for_role(
                    role_slot=role_slot,
                    instance_seed=str(binding["instance_seed"]),
                    world_id=str(binding["world_id"]),
                    theme=str(binding["theme"]),
                    theme_family=str(binding["theme_family"]),
                    used_names=used_names,
                )
            )
    return roster


def _find_npc_in_roster(
    roster: Sequence[Mapping[str, Any]],
    *,
    npc_id: Optional[str] = None,
    venue_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if npc_id:
        target = str(npc_id).strip()
        target_low = target.lower()
        for npc in roster:
            if str(npc.get("npc_id") or "").strip().lower() == target_low:
                return dict(npc)
            if str(npc.get("role_slot") or "").strip().lower() == target_low:
                return dict(npc)
            aliases = npc.get("legacy_alias_ids") or ()
            for alias in aliases:
                if str(alias).strip().lower() == target_low:
                    return dict(npc)
    if venue_id:
        canon_target = _canonical_venue(venue_id)
        target_low = str(venue_id).strip().lower()
        for npc in roster:
            npc_venue = str(npc.get("venue_id") or "")
            if _venues_match(npc_venue, canon_target):
                return dict(npc)
            for va in npc.get("venue_aliases") or ():
                if str(va).strip().lower() == target_low or _venues_match(str(va), canon_target):
                    return dict(npc)
    return None


def resolve_npc_by_id_or_alias_zero_write(
    day_root: Path | str,
    npc_identifier: str,
) -> Optional[dict[str, Any]]:
    """Resolve an NPC by canonical ``npc_id``, ``role_slot``, or ``legacy_alias_ids`` (zero-write)."""
    roster = resolve_instance_npc_roster_zero_write(day_root)
    return _find_npc_in_roster(roster, npc_id=npc_identifier)


def _window_open_at(open_windows: Sequence[Mapping[str, Any]], now_dt: datetime) -> tuple[bool, Optional[str]]:
    weekday = now_dt.isoweekday()
    cur_min = now_dt.hour * 60 + now_dt.minute
    for win in open_windows:
        if not isinstance(win, Mapping):
            continue
        days = win.get("weekdays") or ()
        if weekday not in days:
            continue
        start_m = _hhmm_to_minutes(str(win.get("start_hhmm") or ""))
        end_m = _hhmm_to_minutes(str(win.get("end_hhmm") or ""))
        if start_m is None or end_m is None:
            continue
        if start_m <= cur_min < end_m:
            return True, str(win.get("venue_id") or "")
    return False, None


def _commitment_matches_npc(commitment: Mapping[str, Any], npc: Mapping[str, Any]) -> bool:
    cid_npc = str(commitment.get("npc_id") or "").strip().lower()
    npc_id = str(npc.get("npc_id") or "").strip().lower()
    if cid_npc and npc_id and cid_npc == npc_id:
        return True
    role_slot = str(npc.get("role_slot") or "").strip().lower()
    if cid_npc and role_slot and cid_npc == role_slot:
        return True
    aliases = {str(a).strip().lower() for a in (npc.get("legacy_alias_ids") or ())}
    if cid_npc and cid_npc in aliases:
        return True
    c_venue = str(commitment.get("venue_id") or "")
    npc_venue = str(npc.get("venue_id") or "")
    if not cid_npc and c_venue and npc_venue and _venues_match(c_venue, npc_venue):
        return True
    return False


def derive_npc_presence_zero_write(
    npc: Mapping[str, Any],
    now_dt: datetime | str,
    active_commitments: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Pure zero-write derivation of an NPC's presence, open state & current activity.

    Performs 0 disk reads/writes and 0 LLM calls.
    """
    dt = _parse_dt(now_dt)
    if dt is None:
        raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Invalid now_dt: {now_dt!r}")

    local_dt = dt.astimezone(DEFAULT_TZ)
    base_venue = str(npc.get("venue_id") or "")
    windows = npc.get("open_windows") or ()
    sched_open, sched_venue = _window_open_at(windows, local_dt)

    temp_close_plan: Optional[Mapping[str, Any]] = None
    outdoor_stall_plan: Optional[Mapping[str, Any]] = None
    active_appt: Optional[Mapping[str, Any]] = None
    holding_count = 0
    ready_topics: list[str] = []

    for row in active_commitments or ():
        if not isinstance(row, Mapping):
            continue
        if not _commitment_matches_npc(row, npc):
            continue
        kind = str(row.get("kind") or "")
        st = str(row.get("state") or "")
        if kind == "BOOK_OR_ITEM_RESERVATION" and st == "ARRIVED_HOLDING":
            holding_count += 1
        elif kind == "SCHEDULED_APPOINTMENT" and st == "ACTIVE_WINDOW":
            active_appt = row
        elif kind == "INDEPENDENT_NPC_PLAN":
            eff = str(row.get("effect_kind") or "")
            start_dt = _parse_dt(row.get("start_at"))
            end_dt = _parse_dt(row.get("end_at"))
            in_time_window = (
                start_dt is not None
                and end_dt is not None
                and start_dt <= local_dt < end_dt
            )
            if eff == "TEMPORARY_CLOSE" and (
                st == "IN_PROGRESS" or (st == "PLANNED" and in_time_window)
            ):
                temp_close_plan = row
            elif eff == "OUTDOOR_STALL" and (
                st == "IN_PROGRESS" or (st == "PLANNED" and in_time_window)
            ):
                outdoor_stall_plan = row
            elif eff == "NEW_TOPIC_READY" and st == "COMPLETED":
                topic = str(row.get("topic_summary_cn") or row.get("title_cn") or "").strip()
                if topic and topic not in ready_topics:
                    ready_topics.append(topic)

    is_open = bool(sched_open)
    presence_state = "AT_VENUE" if is_open else "OFF_HOURS"
    current_venue_id = sched_venue or base_venue
    current_activity_cn = str(npc.get("personal_project_cn") or "") if is_open else ""
    away_reason_cn = "" if is_open else str(npc.get("closed_note_cn") or "当前不在营业时间。")
    blocking_commitment_id: Optional[str] = None

    if temp_close_plan is not None:
        is_open = False
        presence_state = "TEMPORARILY_CLOSED"
        current_venue_id = str(temp_close_plan.get("venue_id") or base_venue)
        current_activity_cn = ""
        away_reason_cn = str(
            temp_close_plan.get("door_notice_cn")
            or temp_close_plan.get("title_cn")
            or "临时外出闭店中"
        )
        blocking_commitment_id = str(temp_close_plan.get("commitment_id") or "") or None
    elif outdoor_stall_plan is not None:
        is_open = True
        presence_state = "OUTDOOR_STALL_ACTIVE"
        current_venue_id = str(outdoor_stall_plan.get("venue_id") or base_venue)
        current_activity_cn = str(outdoor_stall_plan.get("title_cn") or npc.get("personal_project_cn") or "")
        away_reason_cn = ""
    elif active_appt is not None:
        is_open = True
        presence_state = "AWAITING_APPOINTMENT"
        current_venue_id = str(active_appt.get("venue_id") or base_venue)
        current_activity_cn = str(active_appt.get("title_cn") or npc.get("personal_project_cn") or "")
        away_reason_cn = ""

    return {
        "npc_id": str(npc.get("npc_id") or ""),
        "role_slot": str(npc.get("role_slot") or ""),
        "display_name": str(npc.get("display_name") or ""),
        "venue_id": base_venue,
        "current_venue_id": current_venue_id,
        "is_open": is_open,
        "presence_state": presence_state,
        "current_activity_cn": current_activity_cn,
        "away_reason_cn": away_reason_cn,
        "holding_reservations_count": holding_count,
        "ready_topics": ready_topics,
        "blocking_commitment_id": blocking_commitment_id,
        "checked_at": _format_iso(local_dt),
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Internal State Shape, Min-Due Computation & Catalog Release Helper
# ---------------------------------------------------------------------------


def _empty_state(day_root: Path | str, now_str: str) -> dict[str, Any]:
    binding = _read_instance_binding_zero_write(day_root)
    roster = resolve_instance_npc_roster_zero_write(day_root)
    return {
        "schema": SCHEMA,
        "revision": 0,
        "updated_at": now_str,
        "instance_binding": {
            "instance_seed_fingerprint": binding["instance_seed_fingerprint"],
            "world_id": binding["world_id"],
            "theme": binding["theme"],
        },
        "min_next_due_at": None,
        "last_weather_seen": None,
        "next_commitment_seq": 0,
        "next_notice_seq": 0,
        "op_seq": 0,
        "next_op_seq": 0,
        "npcs": {npc["npc_id"]: copy.deepcopy(npc) for npc in roster},
        "commitments": {},
        "venue_notices": [],
        "subject_observed_venues": {},
        "operation_receipts": {},
        "committed_op_ring": [],
    }


def _ensure_state_hydrated(state: dict[str, Any], day_root: Path | str, now_str: str) -> dict[str, Any]:
    if not isinstance(state, dict):
        return _empty_state(day_root, now_str)
    base = _empty_state(day_root, now_str)
    for key, default_val in base.items():
        if key not in state or state[key] is None:
            if key in ("min_next_due_at", "last_weather_seen"):
                state.setdefault(key, None)
            else:
                state[key] = copy.deepcopy(default_val)
    if not isinstance(state.get("npcs"), dict) or not state["npcs"]:
        state["npcs"] = base["npcs"]
    if not isinstance(state.get("commitments"), dict):
        state["commitments"] = {}
    if not isinstance(state.get("venue_notices"), list):
        state["venue_notices"] = []
    if not isinstance(state.get("subject_observed_venues"), dict):
        state["subject_observed_venues"] = {}
    if not isinstance(state.get("operation_receipts"), dict):
        state["operation_receipts"] = {}
    if not isinstance(state.get("committed_op_ring"), list):
        state["committed_op_ring"] = []
    state["schema"] = SCHEMA
    return state


def _is_commitment_terminal(commitment: Mapping[str, Any]) -> bool:
    return str(commitment.get("state") or "") in TERMINAL_COMMITMENT_STATES


def _recompute_min_next_due_at(state: dict[str, Any]) -> Optional[str]:
    earliest_dt: Optional[datetime] = None
    earliest_iso: Optional[str] = None
    commitments = state.get("commitments")
    if isinstance(commitments, Mapping):
        for row in commitments.values():
            if not isinstance(row, Mapping):
                continue
            if _is_commitment_terminal(row):
                continue
            due_iso = row.get("next_due_at")
            due_dt = _parse_dt(due_iso)
            if due_dt is None:
                continue
            if earliest_dt is None or due_dt < earliest_dt:
                earliest_dt = due_dt
                earliest_iso = _format_iso(due_dt)
    state["min_next_due_at"] = earliest_iso
    return earliest_iso


def _has_open_weather_sensitive_commitment(state: Mapping[str, Any]) -> bool:
    commitments = state.get("commitments")
    if not isinstance(commitments, Mapping):
        return False
    for row in commitments.values():
        if not isinstance(row, Mapping):
            continue
        if _is_commitment_terminal(row):
            continue
        if bool(row.get("weather_sensitive")):
            return True
        if str(row.get("kind") or "") == "INDEPENDENT_NPC_PLAN" and str(row.get("effect_kind") or "") == "OUTDOOR_STALL":
            return True
        if _canonical_venue(str(row.get("venue_id") or "")) == "porch_market":
            return True
    return False


def _append_history(commitment: dict[str, Any], *, at_iso: str, from_state: str, to_state: str, note_cn: str) -> None:
    history = commitment.get("history")
    if not isinstance(history, list):
        history = []
        commitment["history"] = history
    history.append({
        "at": at_iso,
        "from_state": from_state,
        "to_state": to_state,
        "note_cn": _strip_emojis_and_ctrl(note_cn, max_len=200),
    })
    if len(history) > MAX_HISTORY_PER_COMMITMENT:
        del history[: len(history) - MAX_HISTORY_PER_COMMITMENT]


def _post_venue_notice(
    state: dict[str, Any],
    *,
    venue_id: str,
    npc_id: str,
    notice_kind: str,
    text_cn: str,
    posted_at: str,
    commitment_id: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> dict[str, Any]:
    notices = state.setdefault("venue_notices", [])
    clean_text = _strip_emojis_and_ctrl(text_cn, max_len=240)
    # Deduplicate identical active notice for same commitment + notice_kind.
    if commitment_id:
        for existing in notices:
            if (
                isinstance(existing, Mapping)
                and existing.get("commitment_id") == commitment_id
                and existing.get("notice_kind") == notice_kind
                and existing.get("venue_id") == venue_id
            ):
                return dict(existing)

    seq = int(state.get("next_notice_seq") or 0) + 1
    state["next_notice_seq"] = seq
    notice = {
        "notice_id": f"ntc_{seq:04d}_{secrets.token_hex(3)}",
        "venue_id": venue_id,
        "npc_id": npc_id,
        "commitment_id": commitment_id,
        "notice_kind": notice_kind,
        "text_cn": clean_text,
        "posted_at": posted_at,
        "valid_until": valid_until,
    }
    notices.append(notice)
    if len(notices) > MAX_VENUE_NOTICES:
        del notices[: len(notices) - MAX_VENUE_NOTICES]
    return notice


def _prune_commitments_ring(state: dict[str, Any]) -> None:
    commitments = state.get("commitments")
    if not isinstance(commitments, dict) or len(commitments) <= MAX_COMMITMENTS:
        return
    terminal_items = [
        (cid, row)
        for cid, row in commitments.items()
        if isinstance(row, Mapping) and _is_commitment_terminal(row)
    ]
    terminal_items.sort(key=lambda pair: (str(pair[1].get("updated_at") or ""), str(pair[0])))
    while len(commitments) > MAX_COMMITMENTS and terminal_items:
        cid, _ = terminal_items.pop(0)
        commitments.pop(cid, None)


def _try_release_item_to_catalog(
    day_root: Path,
    *,
    commitment_id: str,
    venue_id: str,
    item_spec: Mapping[str, Any],
    source_kind: str,
    trigger_reason: str,
    now_str: str,
) -> dict[str, Any]:
    spec_copy = copy.deepcopy(dict(item_spec))
    if not spec_copy.get("store_id"):
        spec_copy["store_id"] = venue_id
    try:
        from components.world.scripts import world_content_catalog as _wcc
    except ImportError:
        try:
            import world_content_catalog as _wcc  # type: ignore[no-redef]
        except ImportError:
            return {
                "attempted": True,
                "catalog_available": False,
                "released": False,
                "reason": "CATALOG_MODULE_UNAVAILABLE",
                "item_spec": spec_copy,
            }

    reg_fn = getattr(_wcc, "register_catalog_batch", None)
    if not callable(reg_fn):
        return {
            "attempted": True,
            "catalog_available": False,
            "released": False,
            "reason": "REGISTER_BATCH_UNAVAILABLE",
            "item_spec": spec_copy,
        }

    try:
        res = reg_fn(
            day_root,
            items=[spec_copy],
            events=(),
            source_kind=source_kind,
            trigger_reason=trigger_reason,
            operation_key=f"{trigger_reason.lower()}:{commitment_id}",
            now_iso=now_str,
        )
        reg_items = res.get("registered_items") if isinstance(res, Mapping) else None
        ok = bool(
            isinstance(res, Mapping)
            and res.get("ok", True)
            and (reg_items is None or len(reg_items) > 0)
        )
        released_ids = [
            str(r.get("catalog_item_id"))
            for r in (reg_items or [])
            if isinstance(r, Mapping) and r.get("catalog_item_id")
        ]
        return {
            "attempted": True,
            "catalog_available": True,
            "released": ok,
            "released_catalog_item_ids": released_ids,
            "result": copy.deepcopy(dict(res)) if isinstance(res, Mapping) else {"raw": str(res)},
        }
    except Exception as exc:  # pragma: no cover - defensive integration isolation
        return {
            "attempted": True,
            "catalog_available": True,
            "released": False,
            "reason": str(exc),
        }


def _try_reserve_decision_budget(budget: Any, now_dt: datetime) -> tuple[bool, str]:
    if budget is None:
        return True, ""
    reserve_fn = getattr(budget, "reserve", None)
    if not callable(reserve_fn):
        return True, ""
    try:
        reserve_fn("decision", est_tokens=DEFAULT_DECISION_EST_TOKENS, now=now_dt)
        return True, ""
    except Exception as exc:
        reason = str(getattr(exc, "reason_code", "") or exc)
        return False, reason


# ---------------------------------------------------------------------------
# Pure In-Lock Advancement Primitives (Shared by DUE_TIME, CONDITION & CONTACT)
# ---------------------------------------------------------------------------


def _apply_weather_change_in_state(
    state: dict[str, Any],
    *,
    new_weather: str,
    now_str: str,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    norm_weather = _normalize_weather(new_weather)
    if not norm_weather:
        return False, [], []
    changed = False
    if state.get("last_weather_seen") != norm_weather:
        state["last_weather_seen"] = norm_weather
        changed = True

    affected: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    if norm_weather not in RAIN_AND_SEVERE_WEATHERS:
        return changed, affected, notices

    commitments = state.get("commitments")
    if not isinstance(commitments, dict):
        return changed, affected, notices

    for cid in sorted(commitments.keys()):
        row = commitments[cid]
        if not isinstance(row, dict) or _is_commitment_terminal(row):
            continue
        kind = str(row.get("kind") or "")
        is_sensitive = bool(row.get("weather_sensitive")) or (
            kind == "INDEPENDENT_NPC_PLAN" and str(row.get("effect_kind") or "") == "OUTDOOR_STALL"
        ) or (_canonical_venue(str(row.get("venue_id") or "")) == "porch_market")
        if not is_sensitive:
            continue

        prev_state = str(row.get("state") or "")
        venue_id = str(row.get("venue_id") or "")
        npc_id = str(row.get("npc_id") or "")
        title_cn = str(row.get("title_cn") or "户外安排")

        if kind == "INDEPENDENT_NPC_PLAN" and prev_state in ("PLANNED", "IN_PROGRESS"):
            row["state"] = "CANCELLED_BY_WEATHER"
            row["next_due_at"] = None
            row["cancelled_at"] = now_str
            row["cancel_reason"] = f"weather:{norm_weather}"
            row["updated_at"] = now_str
            notice_text = f"因天气转雨（{norm_weather}），{title_cn}已取消并收摊。"
            _append_history(
                row,
                at_iso=now_str,
                from_state=prev_state,
                to_state="CANCELLED_BY_WEATHER",
                note_cn=notice_text,
            )
            ntc = _post_venue_notice(
                state,
                venue_id=venue_id,
                npc_id=npc_id,
                notice_kind="WEATHER_CANCELLED",
                text_cn=notice_text,
                posted_at=now_str,
                commitment_id=cid,
            )
            notices.append(ntc)
            affected.append({
                "commitment_id": cid,
                "kind": kind,
                "from_state": prev_state,
                "to_state": "CANCELLED_BY_WEATHER",
                "venue_id": venue_id,
            })
            changed = True

        elif kind == "SCHEDULED_APPOINTMENT" and prev_state in ("SCHEDULED", "ACTIVE_WINDOW"):
            fallback_venue = str(row.get("indoor_fallback_venue_id") or "").strip()
            if fallback_venue:
                orig_venue = venue_id
                row["state"] = "RESCHEDULED_INDOOR"
                row["venue_id"] = fallback_venue
                row["weather_sensitive"] = False
                row["updated_at"] = now_str
                notice_text = f"因天气转雨（{norm_weather}），{title_cn}已改至室内场地（{fallback_venue}）。"
                _append_history(
                    row,
                    at_iso=now_str,
                    from_state=prev_state,
                    to_state="RESCHEDULED_INDOOR",
                    note_cn=notice_text,
                )
                ntc = _post_venue_notice(
                    state,
                    venue_id=orig_venue,
                    npc_id=npc_id,
                    notice_kind="WEATHER_RESCHEDULED_INDOOR",
                    text_cn=notice_text,
                    posted_at=now_str,
                    commitment_id=cid,
                )
                notices.append(ntc)
                affected.append({
                    "commitment_id": cid,
                    "kind": kind,
                    "from_state": prev_state,
                    "to_state": "RESCHEDULED_INDOOR",
                    "venue_id": fallback_venue,
                })
                changed = True
            else:
                row["state"] = "CANCELLED_WITH_NOTICE"
                row["next_due_at"] = None
                row["cancelled_at"] = now_str
                row["cancel_reason"] = f"weather:{norm_weather}"
                row["updated_at"] = now_str
                notice_text = f"因天气转雨（{norm_weather}），户外约定「{title_cn}」已取消。"
                _append_history(
                    row,
                    at_iso=now_str,
                    from_state=prev_state,
                    to_state="CANCELLED_WITH_NOTICE",
                    note_cn=notice_text,
                )
                ntc = _post_venue_notice(
                    state,
                    venue_id=venue_id,
                    npc_id=npc_id,
                    notice_kind="WEATHER_CANCELLED",
                    text_cn=notice_text,
                    posted_at=now_str,
                    commitment_id=cid,
                )
                notices.append(ntc)
                affected.append({
                    "commitment_id": cid,
                    "kind": kind,
                    "from_state": prev_state,
                    "to_state": "CANCELLED_WITH_NOTICE",
                    "venue_id": venue_id,
                })
                changed = True

        elif kind == "BOOK_OR_ITEM_RESERVATION" and prev_state == "ORDERED":
            orig_due = _parse_dt(row.get("arrival_due_at")) or _parse_dt(now_str)
            assert orig_due is not None
            new_due = _format_iso(orig_due + timedelta(hours=DEFAULT_DELAY_RESCHEDULE_HOURS))
            row["state"] = "DELAYED_RESCHEDULED"
            row["arrival_due_at"] = new_due
            row["next_due_at"] = new_due
            row["weather_sensitive"] = False
            row["updated_at"] = now_str
            notice_text = f"因雨天运输延误，{title_cn}预计顺延至 {new_due} 到店。"
            _append_history(
                row,
                at_iso=now_str,
                from_state=prev_state,
                to_state="DELAYED_RESCHEDULED",
                note_cn=notice_text,
            )
            ntc = _post_venue_notice(
                state,
                venue_id=venue_id,
                npc_id=npc_id,
                notice_kind="RESERVATION_DELAYED",
                text_cn=notice_text,
                posted_at=now_str,
                commitment_id=cid,
            )
            notices.append(ntc)
            affected.append({
                "commitment_id": cid,
                "kind": kind,
                "from_state": prev_state,
                "to_state": "DELAYED_RESCHEDULED",
                "venue_id": venue_id,
            })
            changed = True

    return changed, affected, notices


def _advance_due_in_state(
    day_root: Path,
    state: dict[str, Any],
    *,
    now_dt: datetime,
    now_str: str,
    decision_callback: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    budget: Optional[Any] = None,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    """Deterministically advance all commitments due at or before ``now_dt``."""
    commitments = state.get("commitments")
    if not isinstance(commitments, dict):
        return False, [], [], [], 0

    changed = False
    transitions: list[dict[str, Any]] = []
    notices_posted: list[dict[str, Any]] = []
    shelf_releases: list[dict[str, Any]] = []
    llm_calls = 0

    # Deterministic order: earliest next_due_at first, then commitment_id.
    ordered_ids = sorted(
        commitments.keys(),
        key=lambda cid: (str(commitments[cid].get("next_due_at") or "9999"), cid),
    )

    for cid in ordered_ids:
        row = commitments[cid]
        if not isinstance(row, dict) or _is_commitment_terminal(row):
            continue
        kind = str(row.get("kind") or "")
        venue_id = str(row.get("venue_id") or "")
        npc_id = str(row.get("npc_id") or "")
        title_cn = str(row.get("title_cn") or "约定事项")

        if kind == "BOOK_OR_ITEM_RESERVATION":
            # Step 1: ORDERED / DELAYED_RESCHEDULED -> ARRIVED_HOLDING or DELAYED_RESCHEDULED
            if row.get("state") in ("ORDERED", "DELAYED_RESCHEDULED"):
                arr_dt = _parse_dt(row.get("arrival_due_at") or row.get("next_due_at"))
                if arr_dt is not None and now_dt >= arr_dt:
                    prev = str(row["state"])
                    if bool(row.get("supply_delayed")) and prev == "ORDERED":
                        delay_hours = DEFAULT_DELAY_RESCHEDULE_HOURS
                        new_due_dt = arr_dt + timedelta(hours=delay_hours)
                        delay_reason = str(row.get("delay_reason_cn") or "供货通道稍有延误")
                        notice_text = f"预订的「{title_cn}」{delay_reason}，预计顺延到店。"
                        budget_refused = False
                        budget_reason = ""
                        if decision_callback is not None:
                            allowed, b_reason = _try_reserve_decision_budget(budget, now_dt)
                            if allowed:
                                npc_map = state.get("npcs") or {}
                                npc_info = npc_map.get(npc_id) if isinstance(npc_map, Mapping) else {}
                                cb_ctx = {
                                    "commitment_id": cid,
                                    "kind": kind,
                                    "npc_id": npc_id,
                                    "npc_name": str((npc_info or {}).get("display_name") or ""),
                                    "venue_id": venue_id,
                                    "title_cn": title_cn,
                                    "delay_reason_cn": delay_reason,
                                    "original_arrival_due_at": _format_iso(arr_dt),
                                    "now_iso": now_str,
                                }
                                decision = decision_callback(cb_ctx) or {}
                                llm_calls += 1
                                if isinstance(decision, Mapping):
                                    cand_due = _parse_dt(decision.get("new_arrival_due_at"))
                                    if cand_due is not None and cand_due > now_dt:
                                        new_due_dt = cand_due
                                    elif isinstance(decision.get("delay_hours"), (int, float)) and not isinstance(decision.get("delay_hours"), bool):
                                        dh = max(1.0, min(168.0, float(decision["delay_hours"])))
                                        new_due_dt = max(now_dt, arr_dt) + timedelta(hours=dh)
                                    custom_notice = _strip_emojis_and_ctrl(str(decision.get("notice_cn") or ""), max_len=200)
                                    if custom_notice:
                                        notice_text = custom_notice
                            else:
                                budget_refused = True
                                budget_reason = b_reason
                        new_due_iso = _format_iso(new_due_dt)
                        row["state"] = "DELAYED_RESCHEDULED"
                        row["supply_delayed"] = False
                        row["arrival_due_at"] = new_due_iso
                        row["next_due_at"] = new_due_iso
                        row["updated_at"] = now_str
                        if budget_refused:
                            row["decision_budget_refused"] = True
                            row["decision_budget_reason"] = budget_reason
                        _append_history(
                            row,
                            at_iso=now_str,
                            from_state=prev,
                            to_state="DELAYED_RESCHEDULED",
                            note_cn=notice_text,
                        )
                        ntc = _post_venue_notice(
                            state,
                            venue_id=venue_id,
                            npc_id=npc_id,
                            notice_kind="RESERVATION_DELAYED",
                            text_cn=notice_text,
                            posted_at=now_str,
                            commitment_id=cid,
                            valid_until=new_due_iso,
                        )
                        notices_posted.append(ntc)
                        transitions.append({
                            "commitment_id": cid,
                            "kind": kind,
                            "from_state": prev,
                            "to_state": "DELAYED_RESCHEDULED",
                            "budget_refused": budget_refused,
                        })
                        changed = True
                    else:
                        hold_hours = int(row.get("hold_hours") or DEFAULT_RESERVATION_HOLD_HOURS)
                        preset_hold_dt = _parse_dt(row.get("hold_until_at"))
                        hold_dt = preset_hold_dt if (preset_hold_dt is not None and preset_hold_dt > arr_dt) else (arr_dt + timedelta(hours=hold_hours))
                        hold_iso = _format_iso(hold_dt)
                        arrived_iso = _format_iso(arr_dt)
                        row["state"] = "ARRIVED_HOLDING"
                        row["arrived_at"] = arrived_iso
                        row["hold_until_at"] = hold_iso
                        row["next_due_at"] = hold_iso
                        row["updated_at"] = now_str
                        arr_note = f"预订的「{title_cn}」已到店，将为您保留至 {hold_iso}。"
                        _append_history(
                            row,
                            at_iso=arrived_iso,
                            from_state=prev,
                            to_state="ARRIVED_HOLDING",
                            note_cn=arr_note,
                        )
                        ntc = _post_venue_notice(
                            state,
                            venue_id=venue_id,
                            npc_id=npc_id,
                            notice_kind="RESERVATION_ARRIVED",
                            text_cn=arr_note,
                            posted_at=arrived_iso,
                            commitment_id=cid,
                            valid_until=hold_iso,
                        )
                        notices_posted.append(ntc)
                        transitions.append({
                            "commitment_id": cid,
                            "kind": kind,
                            "from_state": prev,
                            "to_state": "ARRIVED_HOLDING",
                        })
                        changed = True

            # Step 2: ARRIVED_HOLDING -> HOLD_EXPIRED (also handles fast-forward past arrival + 72h!)
            if row.get("state") == "ARRIVED_HOLDING":
                hold_dt = _parse_dt(row.get("hold_until_at") or row.get("next_due_at"))
                if hold_dt is not None and now_dt >= hold_dt:
                    prev = "ARRIVED_HOLDING"
                    row["state"] = "HOLD_EXPIRED"
                    row["expired_at"] = now_str
                    row["next_due_at"] = None
                    row["updated_at"] = now_str
                    exp_note = f"「{title_cn}」{HOLD_EXPIRED_NOTICE_CN}"
                    _append_history(
                        row,
                        at_iso=now_str,
                        from_state=prev,
                        to_state="HOLD_EXPIRED",
                        note_cn=exp_note,
                    )
                    ntc = _post_venue_notice(
                        state,
                        venue_id=venue_id,
                        npc_id=npc_id,
                        notice_kind="RESERVATION_HOLD_EXPIRED",
                        text_cn=exp_note,
                        posted_at=now_str,
                        commitment_id=cid,
                    )
                    notices_posted.append(ntc)
                    item_spec = row.get("catalog_item_spec")
                    if isinstance(item_spec, Mapping) and item_spec:
                        release_info = _try_release_item_to_catalog(
                            day_root,
                            commitment_id=cid,
                            venue_id=venue_id,
                            item_spec=item_spec,
                            source_kind="reservation_hold_expired",
                            trigger_reason="HOLD_EXPIRED_RELEASE",
                            now_str=now_str,
                        )
                        row["shelf_release"] = release_info
                        row["released_to_shelf"] = bool(release_info.get("released"))
                        shelf_releases.append({"commitment_id": cid, **release_info})
                    else:
                        row["released_to_shelf"] = False
                    transitions.append({
                        "commitment_id": cid,
                        "kind": kind,
                        "from_state": prev,
                        "to_state": "HOLD_EXPIRED",
                        "released_to_shelf": bool(row.get("released_to_shelf")),
                    })
                    changed = True

        elif kind == "SCHEDULED_APPOINTMENT":
            if row.get("state") in ("SCHEDULED", "RESCHEDULED_INDOOR"):
                ws_dt = _parse_dt(row.get("window_start_at") or row.get("next_due_at"))
                we_dt = _parse_dt(row.get("window_end_at"))
                if ws_dt is not None and now_dt >= ws_dt:
                    prev = str(row["state"])
                    row["state"] = "ACTIVE_WINDOW"
                    row["next_due_at"] = _format_iso(we_dt) if we_dt is not None else None
                    row["updated_at"] = now_str
                    _append_history(
                        row,
                        at_iso=_format_iso(ws_dt),
                        from_state=prev,
                        to_state="ACTIVE_WINDOW",
                        note_cn=f"约定「{title_cn}」进入可赴约时间窗。",
                    )
                    transitions.append({
                        "commitment_id": cid,
                        "kind": kind,
                        "from_state": prev,
                        "to_state": "ACTIVE_WINDOW",
                    })
                    changed = True

            if row.get("state") == "ACTIVE_WINDOW":
                we_dt = _parse_dt(row.get("window_end_at") or row.get("next_due_at"))
                if we_dt is not None and now_dt >= we_dt:
                    prev = "ACTIVE_WINDOW"
                    row["state"] = "MISSED_BY_SUBJECT"
                    row["missed_at"] = now_str
                    row["next_due_at"] = None
                    row["updated_at"] = now_str
                    miss_note = f"约定「{title_cn}」时间窗已过，本次未能到场。"
                    _append_history(
                        row,
                        at_iso=now_str,
                        from_state=prev,
                        to_state="MISSED_BY_SUBJECT",
                        note_cn=miss_note,
                    )
                    ntc = _post_venue_notice(
                        state,
                        venue_id=venue_id,
                        npc_id=npc_id,
                        notice_kind="APPOINTMENT_MISSED",
                        text_cn=miss_note,
                        posted_at=now_str,
                        commitment_id=cid,
                    )
                    notices_posted.append(ntc)
                    transitions.append({
                        "commitment_id": cid,
                        "kind": kind,
                        "from_state": prev,
                        "to_state": "MISSED_BY_SUBJECT",
                    })
                    changed = True

        elif kind == "INDEPENDENT_NPC_PLAN":
            eff = str(row.get("effect_kind") or "")
            start_dt = _parse_dt(row.get("start_at") or row.get("next_due_at"))
            end_dt = _parse_dt(row.get("end_at"))
            if row.get("state") == "PLANNED" and start_dt is not None and now_dt >= start_dt:
                prev = "PLANNED"
                start_iso = _format_iso(start_dt)
                end_iso = _format_iso(end_dt) if end_dt is not None else None
                row["state"] = "IN_PROGRESS"
                row["started_at"] = start_iso
                row["next_due_at"] = end_iso
                row["updated_at"] = now_str
                _append_history(
                    row,
                    at_iso=start_iso,
                    from_state=prev,
                    to_state="IN_PROGRESS",
                    note_cn=f"街坊自主安排「{title_cn}」已开始。",
                )
                if eff == "TEMPORARY_CLOSE":
                    door_text = str(row.get("door_notice_cn") or f"因{title_cn}，本店临时休业至 {end_iso or '稍晚'}。")
                    row["door_notice_posted"] = True
                    ntc = _post_venue_notice(
                        state,
                        venue_id=venue_id,
                        npc_id=npc_id,
                        notice_kind="TEMPORARY_CLOSE",
                        text_cn=door_text,
                        posted_at=start_iso,
                        commitment_id=cid,
                        valid_until=end_iso,
                    )
                    notices_posted.append(ntc)
                elif eff == "OUTDOOR_STALL":
                    stall_text = str(row.get("door_notice_cn") or f"露天摊位「{title_cn}」现已出摊。")
                    ntc = _post_venue_notice(
                        state,
                        venue_id=venue_id,
                        npc_id=npc_id,
                        notice_kind="OUTDOOR_STALL_OPEN",
                        text_cn=stall_text,
                        posted_at=start_iso,
                        commitment_id=cid,
                        valid_until=end_iso,
                    )
                    notices_posted.append(ntc)
                elif eff == "LIMITED_BATCH_ARRIVAL":
                    batch_text = str(row.get("door_notice_cn") or f"限定批次「{title_cn}」已到店上架。")
                    ntc = _post_venue_notice(
                        state,
                        venue_id=venue_id,
                        npc_id=npc_id,
                        notice_kind="LIMITED_BATCH_ARRIVAL",
                        text_cn=batch_text,
                        posted_at=start_iso,
                        commitment_id=cid,
                        valid_until=end_iso,
                    )
                    notices_posted.append(ntc)
                    item_spec = row.get("catalog_item_spec")
                    if isinstance(item_spec, Mapping) and item_spec:
                        release_info = _try_release_item_to_catalog(
                            day_root,
                            commitment_id=cid,
                            venue_id=venue_id,
                            item_spec=item_spec,
                            source_kind="npc_plan_limited_batch",
                            trigger_reason="LIMITED_BATCH_ARRIVAL",
                            now_str=start_iso,
                        )
                        row["shelf_release"] = release_info
                        shelf_releases.append({"commitment_id": cid, **release_info})
                transitions.append({
                    "commitment_id": cid,
                    "kind": kind,
                    "effect_kind": eff,
                    "from_state": prev,
                    "to_state": "IN_PROGRESS",
                })
                changed = True

            if row.get("state") == "IN_PROGRESS" and end_dt is not None and now_dt >= end_dt:
                prev = "IN_PROGRESS"
                end_iso = _format_iso(end_dt)
                row["state"] = "COMPLETED"
                row["completed_at"] = end_iso
                row["next_due_at"] = None
                row["updated_at"] = now_str
                done_note = f"街坊自主安排「{title_cn}」已结束。"
                if eff == "NEW_TOPIC_READY":
                    topic = str(row.get("topic_summary_cn") or title_cn)
                    row["ready_topic_cn"] = topic
                    done_note = f"已完成「{title_cn}」，下次见面时有了可聊的新见闻：{topic}"
                _append_history(
                    row,
                    at_iso=end_iso,
                    from_state=prev,
                    to_state="COMPLETED",
                    note_cn=done_note,
                )
                transitions.append({
                    "commitment_id": cid,
                    "kind": kind,
                    "effect_kind": eff,
                    "from_state": prev,
                    "to_state": "COMPLETED",
                })
                changed = True

    return changed, transitions, notices_posted, shelf_releases, llm_calls


# ---------------------------------------------------------------------------
# Public API: O(1) Idle Skip & Three Wake Triggers
# ---------------------------------------------------------------------------


def check_heartbeat_needed_zero_write(
    day_root: Path | str,
    *,
    now_iso: str,
    current_weather: Optional[str] = None,
) -> bool:
    """O(1) zero-write check for ``nyairo_day.py`` 60s heartbeat.

    Returns ``False`` immediately (with 0 writes) if the state file is absent,
    OR if ``now < min_next_due_at`` (or no open due timestamp) AND weather has
    not changed into a condition affecting an open weather-sensitive commitment.
    """
    state_path = commitments_state_path(day_root)
    if not _is_regular_file(state_path):
        return False

    state = _read_json_readonly(state_path)
    if state is None:
        return False

    now_dt = _parse_dt(now_iso)
    if now_dt is None:
        return False

    min_due_dt = _parse_dt(state.get("min_next_due_at"))
    if min_due_dt is not None and now_dt >= min_due_dt:
        return True

    norm_weather = _normalize_weather(current_weather)
    last_weather = _normalize_weather(state.get("last_weather_seen"))
    if norm_weather and norm_weather != last_weather:
        if _has_open_weather_sensitive_commitment(state):
            if norm_weather in RAIN_AND_SEVERE_WEATHERS or bool(last_weather):
                return True

    return False


def create_commitment(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Create a durable NPC commitment (reservation, appointment, or independent plan)."""
    if not isinstance(payload, Mapping):
        raise WorldCommitmentsError(400, "INVALID_FIELDS", "payload must be a JSON object")

    offending = _scan_forbidden_retroactive_keys(payload)
    if offending is not None:
        raise WorldCommitmentsError(
            400,
            "RETROACTIVE_FABRICATION_FORBIDDEN",
            f"Retroactive fabrication or private smuggling key forbidden: {offending}",
        )

    side = _normalize_actor_side(actor_side)
    eff_op_key = _validate_operation_key(
        operation_key if operation_key is not None else payload.get("operation_key")
    )
    now_dt, now_str = _resolve_now(now_iso)

    kind = str(payload.get("kind") or "").strip().upper()
    if kind not in COMMITMENT_KINDS:
        raise WorldCommitmentsError(
            400,
            "INVALID_FIELDS",
            f"Unsupported commitment kind {kind!r}; expected one of {COMMITMENT_KINDS}",
        )

    title_raw = payload.get("title_cn") or payload.get("item_name_cn") or payload.get("title")
    title_cn = _clean_text_field(title_raw, field_name="title_cn", min_len=1, max_len=120)

    req_npc_id = _clean_text_field(payload.get("npc_id"), field_name="npc_id", min_len=0, max_len=64)
    req_venue_id = _clean_text_field(payload.get("venue_id"), field_name="venue_id", min_len=0, max_len=64)

    requested_by = str(payload.get("requested_by") or side).strip().lower()
    if requested_by not in ("subject", "partner", "npc"):
        raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Invalid requested_by: {requested_by!r}")

    explicit_cid = _clean_text_field(payload.get("commitment_id"), field_name="commitment_id", min_len=0, max_len=96)
    if explicit_cid and not _ID_TOKEN_RE.match(explicit_cid):
        raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Invalid commitment_id format: {explicit_cid!r}")

    source_ref = _clean_text_field(payload.get("source_ref"), field_name="source_ref", min_len=0, max_len=128)
    item_id = _clean_text_field(payload.get("item_id"), field_name="item_id", min_len=0, max_len=128)
    raw_weather = _normalize_weather(payload.get("current_weather") or payload.get("weather"))

    # Kind-specific validation BEFORE acquiring lock
    kind_fields: dict[str, Any] = {}
    if kind == "BOOK_OR_ITEM_RESERVATION":
        arr_raw = payload.get("arrival_due_at") or payload.get("due_at")
        if arr_raw is not None:
            arr_dt = _parse_dt(arr_raw)
            if arr_dt is None:
                raise WorldCommitmentsError(400, "INVALID_FIELDS", f"Invalid arrival_due_at: {arr_raw!r}")
            if arr_dt < now_dt:
                raise WorldCommitmentsError(
                    400,
                    "RETROACTIVE_FABRICATION_FORBIDDEN",
                    f"Backdated arrival_due_at is forbidden: {_format_iso(arr_dt)} < {now_str}",
                )
        elif payload.get("arrival_in_hours") is not None:
            raw_h = payload.get("arrival_in_hours")
            if isinstance(raw_h, bool) or not isinstance(raw_h, (int, float)) or raw_h <= 0:
                raise WorldCommitmentsError(400, "INVALID_FIELDS", "arrival_in_hours must be > 0")
            arr_dt = now_dt + timedelta(hours=float(raw_h))
        else:
            arr_dt = now_dt + timedelta(hours=24)

        hold_hours_raw = payload.get("hold_hours", DEFAULT_RESERVATION_HOLD_HOURS)
        if isinstance(hold_hours_raw, bool) or not isinstance(hold_hours_raw, (int, float)) or hold_hours_raw < 1:
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "hold_hours must be >= 1")
        hold_hours = max(1, min(720, int(hold_hours_raw)))

        hold_until_iso: Optional[str] = None
        if payload.get("hold_until_at") is not None:
            hold_dt = _parse_dt(payload.get("hold_until_at"))
            if hold_dt is None or hold_dt <= arr_dt:
                raise WorldCommitmentsError(400, "INVALID_FIELDS", "hold_until_at must be valid ISO after arrival_due_at")
            hold_until_iso = _format_iso(hold_dt)

        arr_iso = _format_iso(arr_dt)
        spec_raw = payload.get("catalog_item_spec")
        if spec_raw is not None and not isinstance(spec_raw, Mapping):
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "catalog_item_spec must be an object")

        kind_fields = {
            "state": "ORDERED",
            "arrival_due_at": arr_iso,
            "hold_hours": hold_hours,
            "hold_until_at": hold_until_iso,
            "next_due_at": arr_iso,
            "supply_delayed": bool(payload.get("supply_delayed", False)),
            "delay_reason_cn": _clean_text_field(payload.get("delay_reason_cn"), field_name="delay_reason_cn", max_len=160),
            "weather_sensitive": bool(payload.get("weather_sensitive", False)),
            "catalog_item_spec": copy.deepcopy(dict(spec_raw)) if isinstance(spec_raw, Mapping) else None,
            "released_to_shelf": False,
        }

    elif kind == "SCHEDULED_APPOINTMENT":
        ws_raw = payload.get("window_start_at") or payload.get("start_at")
        ws_dt = _parse_dt(ws_raw)
        if ws_dt is None:
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "window_start_at is required for SCHEDULED_APPOINTMENT")
        if ws_dt < now_dt:
            raise WorldCommitmentsError(
                400,
                "RETROACTIVE_FABRICATION_FORBIDDEN",
                f"Backdated window_start_at is forbidden: {_format_iso(ws_dt)} < {now_str}",
            )
        we_raw = payload.get("window_end_at") or payload.get("end_at")
        if we_raw is not None:
            we_dt = _parse_dt(we_raw)
            if we_dt is None or we_dt <= ws_dt:
                raise WorldCommitmentsError(400, "INVALID_FIELDS", "window_end_at must be after window_start_at")
        else:
            we_dt = ws_dt + timedelta(minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES)

        indoor_fallback = _clean_text_field(
            payload.get("indoor_fallback_venue_id"),
            field_name="indoor_fallback_venue_id",
            max_len=64,
        )
        ws_iso = _format_iso(ws_dt)
        we_iso = _format_iso(we_dt)
        kind_fields = {
            "state": "SCHEDULED",
            "window_start_at": ws_iso,
            "window_end_at": we_iso,
            "next_due_at": ws_iso,
            "weather_sensitive": bool(payload.get("weather_sensitive", False)),
            "indoor_fallback_venue_id": indoor_fallback or None,
        }

    elif kind == "INDEPENDENT_NPC_PLAN":
        effect_kind = str(payload.get("effect_kind") or "").strip().upper()
        if effect_kind not in INDEPENDENT_PLAN_EFFECT_KINDS:
            raise WorldCommitmentsError(
                400,
                "INVALID_FIELDS",
                f"Unsupported effect_kind {effect_kind!r}; expected one of {INDEPENDENT_PLAN_EFFECT_KINDS}",
            )
        st_raw = payload.get("start_at")
        st_dt = _parse_dt(st_raw)
        if st_dt is None:
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "start_at is required for INDEPENDENT_NPC_PLAN")
        if st_dt < now_dt:
            raise WorldCommitmentsError(
                400,
                "RETROACTIVE_FABRICATION_FORBIDDEN",
                f"Backdated start_at is forbidden: {_format_iso(st_dt)} < {now_str}",
            )
        en_raw = payload.get("end_at")
        if en_raw is not None:
            en_dt = _parse_dt(en_raw)
            if en_dt is None or en_dt <= st_dt:
                raise WorldCommitmentsError(400, "INVALID_FIELDS", "end_at must be after start_at")
        else:
            en_dt = st_dt + timedelta(hours=DEFAULT_PLAN_DURATION_HOURS)

        default_ws = effect_kind == "OUTDOOR_STALL"
        spec_raw = payload.get("catalog_item_spec")
        if spec_raw is not None and not isinstance(spec_raw, Mapping):
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "catalog_item_spec must be an object")

        st_iso = _format_iso(st_dt)
        en_iso = _format_iso(en_dt)
        kind_fields = {
            "state": "PLANNED",
            "effect_kind": effect_kind,
            "start_at": st_iso,
            "end_at": en_iso,
            "next_due_at": st_iso,
            "door_notice_cn": _clean_text_field(payload.get("door_notice_cn"), field_name="door_notice_cn", max_len=200),
            "topic_summary_cn": _clean_text_field(payload.get("topic_summary_cn"), field_name="topic_summary_cn", max_len=200),
            "weather_sensitive": bool(payload.get("weather_sensitive", default_ws)),
            "catalog_item_spec": copy.deepcopy(dict(spec_raw)) if isinstance(spec_raw, Mapping) else None,
        }
        requested_by = "npc"

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "create_commitment",
        "actor_side": side,
        "kind": kind,
        "title_cn": title_cn,
        "npc_id": req_npc_id,
        "venue_id": req_venue_id,
        "requested_by": requested_by,
        "explicit_cid": explicit_cid,
        "kind_fields": kind_fields,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        existing_doc = _read_json_readonly(state_path)
        state = _ensure_state_hydrated(
            existing_doc if existing_doc is not None else _empty_state(resolved_root, now_str),
            resolved_root,
            now_str,
        )

        if eff_op_key:
            reconcile_kind, hit = _tx.reconcile_evicted_or_active_op(
                resolved_root,
                state,
                eff_op_key,
                dict_key="operation_receipts",
                payload_hash=payload_hash,
            )
            if reconcile_kind == "conflict":
                raise WorldCommitmentsError(409, "OPERATION_KEY_CONFLICT", f"operation_key conflict: {eff_op_key}")
            if reconcile_kind == "expired":
                raise WorldCommitmentsError(409, "RETRY_WINDOW_EXPIRED", f"operation_key expired: {eff_op_key}")
            if reconcile_kind in ("hit_full", "hit_evicted") and isinstance(hit, Mapping):
                replay = copy.deepcopy(dict(hit))
                replay["ok"] = True
                replay["already_applied"] = True
                replay["status"] = "already_applied"
                replay["command_status"] = "DUPLICATE"
                replay["llm_calls"] = 0
                return replay

        roster = list(state["npcs"].values())
        matched_npc = _find_npc_in_roster(
            roster,
            npc_id=req_npc_id or None,
            venue_id=req_venue_id or None,
        )
        if matched_npc is None:
            if req_npc_id:
                raise WorldCommitmentsError(404, "NPC_NOT_FOUND", f"Unknown npc_id: {req_npc_id!r}")
            # Default host NPC based on kind if neither matched
            default_role = "bookstore_keeper" if kind == "BOOK_OR_ITEM_RESERVATION" else "craft_regular"
            matched_npc = _find_npc_in_roster(roster, npc_id=default_role) or roster[0]

        resolved_npc_id = str(matched_npc["npc_id"])
        # Preserve explicit outdoor venue like "porch_market" rather than overwriting it.
        if req_venue_id:
            resolved_venue_id = req_venue_id if req_venue_id == "porch_market" else _canonical_venue(req_venue_id)
        else:
            resolved_venue_id = str(matched_npc["venue_id"])

        seq = int(state.get("next_commitment_seq") or 0) + 1
        state["next_commitment_seq"] = seq
        kind_prefix = {
            "BOOK_OR_ITEM_RESERVATION": "rsv",
            "SCHEDULED_APPOINTMENT": "apt",
            "INDEPENDENT_NPC_PLAN": "pln",
        }[kind]
        commitment_id = explicit_cid or f"cmt_{kind_prefix}_{seq:04d}_{secrets.token_hex(3)}"
        if commitment_id in state["commitments"]:
            raise WorldCommitmentsError(409, "COMMITMENT_ALREADY_EXISTS", f"Duplicate commitment_id: {commitment_id}")

        commitment: dict[str, Any] = {
            "commitment_id": commitment_id,
            "kind": kind,
            "npc_id": resolved_npc_id,
            "npc_display_name": str(matched_npc.get("display_name") or ""),
            "npc_role_slot": str(matched_npc.get("role_slot") or ""),
            "venue_id": resolved_venue_id,
            "title_cn": title_cn,
            "requested_by": requested_by,
            "created_by_side": side,
            "created_at": now_str,
            "updated_at": now_str,
            "source_ref": source_ref or None,
            "item_id": item_id or None,
            **kind_fields,
            "history": [
                {
                    "at": now_str,
                    "from_state": "",
                    "to_state": kind_fields["state"],
                    "note_cn": f"已登记{title_cn}。",
                }
            ],
        }

        state["commitments"][commitment_id] = commitment
        if raw_weather and not state.get("last_weather_seen"):
            state["last_weather_seen"] = raw_weather

        _prune_commitments_ring(state)
        min_due = _recompute_min_next_due_at(state)
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "commitment_id": commitment_id,
            "commitment": copy.deepcopy(commitment),
            "min_next_due_at": min_due,
            "revision": state["revision"],
            "llm_calls": 0,
        }

        if eff_op_key:
            _tx.stamp_receipt(
                state,
                eff_op_key,
                result,
                revision=state["revision"],
                dict_key="operation_receipts",
                payload_hash=payload_hash,
                committed_at=now_str,
            )
            _tx.trim_receipts_by_op_seq(
                state,
                dict_key="operation_receipts",
                max_keep=MAX_OPERATION_RECEIPTS,
            )
            state["op_seq"] = int(state.get("next_op_seq") or 0)

        _atomic_write_json_0600(day_root, resolved_root, state_path, state)
        return result


def advance_due_commitments(
    day_root: Path | str,
    *,
    now_iso: str,
    current_weather: Optional[str] = None,
    decision_callback: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    budget: Optional[Any] = None,
) -> dict[str, Any]:
    """Trigger 1 (``DUE_TIME``): advance due commitments deterministically up to ``now_iso``.

    Performs 0 disk writes and 0 LLM calls when the state file is absent or no
    commitment is due (and weather has not affected any weather-sensitive plan).
    """
    now_dt, now_str = _resolve_now(now_iso)
    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)

    # O(1) zero-write fast path when idle or absent.
    if not _is_regular_file(state_path):
        return {
            "ok": True,
            "status": "noop_absent",
            "advanced_count": 0,
            "transitions": [],
            "notices_posted": [],
            "shelf_releases": [],
            "min_next_due_at": None,
            "disk_written": False,
            "llm_calls": 0,
        }

    if not check_heartbeat_needed_zero_write(resolved_root, now_iso=now_str, current_weather=current_weather):
        ro = _read_json_readonly(state_path) or {}
        return {
            "ok": True,
            "status": "noop_idle",
            "advanced_count": 0,
            "transitions": [],
            "notices_posted": [],
            "shelf_releases": [],
            "min_next_due_at": ro.get("min_next_due_at"),
            "revision": int(ro.get("revision") or 0),
            "disk_written": False,
            "llm_calls": 0,
        }

    _assert_no_symlink_chain(day_root, resolved_root, state_path)
    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        existing_doc = _read_json_readonly(state_path)
        if existing_doc is None:
            return {
                "ok": True,
                "status": "noop_absent",
                "advanced_count": 0,
                "transitions": [],
                "notices_posted": [],
                "shelf_releases": [],
                "min_next_due_at": None,
                "disk_written": False,
                "llm_calls": 0,
            }
        state = _ensure_state_hydrated(existing_doc, resolved_root, now_str)

        changed = False
        all_transitions: list[dict[str, Any]] = []
        all_notices: list[dict[str, Any]] = []

        if current_weather is not None and _normalize_weather(current_weather):
            w_changed, w_affected, w_notices = _apply_weather_change_in_state(
                state,
                new_weather=current_weather,
                now_str=now_str,
            )
            if w_changed:
                changed = True
            all_transitions.extend(w_affected)
            all_notices.extend(w_notices)

        d_changed, d_transitions, d_notices, shelf_releases, llm_calls = _advance_due_in_state(
            resolved_root,
            state,
            now_dt=now_dt,
            now_str=now_str,
            decision_callback=decision_callback,
            budget=budget,
        )
        if d_changed:
            changed = True
        all_transitions.extend(d_transitions)
        all_notices.extend(d_notices)

        min_due = _recompute_min_next_due_at(state)
        if changed:
            _prune_commitments_ring(state)
            state["revision"] = int(state.get("revision") or 0) + 1
            state["updated_at"] = now_str
            _atomic_write_json_0600(day_root, resolved_root, state_path, state)

        return {
            "ok": True,
            "status": "advanced" if all_transitions else ("weather_synced" if changed else "noop_idle"),
            "advanced_count": len(all_transitions),
            "transitions": all_transitions,
            "notices_posted": all_notices,
            "shelf_releases": shelf_releases,
            "min_next_due_at": min_due,
            "revision": int(state.get("revision") or 0),
            "disk_written": changed,
            "llm_calls": llm_calls,
        }


def notify_world_condition_changed(
    day_root: Path | str,
    *,
    condition_kind: str,
    new_value: str,
    now_iso: str,
) -> dict[str, Any]:
    """Trigger 2 (``CONDITION_CHANGED``): react to live world condition changes (e.g. weather -> rain)."""
    clean_kind = _clean_text_field(condition_kind, field_name="condition_kind", min_len=1, max_len=48).lower()
    clean_val = _clean_text_field(new_value, field_name="new_value", min_len=1, max_len=64)
    _now_dt, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)

    # Zero-write when no commitment state exists yet.
    if not _is_regular_file(state_path):
        return {
            "ok": True,
            "status": "noop_absent",
            "condition_kind": clean_kind,
            "new_value": clean_val,
            "affected_count": 0,
            "affected_commitments": [],
            "notices_posted": [],
            "min_next_due_at": None,
            "disk_written": False,
            "llm_calls": 0,
        }

    _assert_no_symlink_chain(day_root, resolved_root, state_path)
    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        existing_doc = _read_json_readonly(state_path)
        if existing_doc is None:
            return {
                "ok": True,
                "status": "noop_absent",
                "condition_kind": clean_kind,
                "new_value": clean_val,
                "affected_count": 0,
                "affected_commitments": [],
                "notices_posted": [],
                "min_next_due_at": None,
                "disk_written": False,
                "llm_calls": 0,
            }
        state = _ensure_state_hydrated(existing_doc, resolved_root, now_str)

        changed = False
        affected: list[dict[str, Any]] = []
        notices: list[dict[str, Any]] = []

        if clean_kind == "weather":
            changed, affected, notices = _apply_weather_change_in_state(
                state,
                new_weather=clean_val,
                now_str=now_str,
            )

        min_due = _recompute_min_next_due_at(state)
        if changed:
            state["revision"] = int(state.get("revision") or 0) + 1
            state["updated_at"] = now_str
            _atomic_write_json_0600(day_root, resolved_root, state_path, state)

        return {
            "ok": True,
            "status": "applied" if changed else "noop",
            "condition_kind": clean_kind,
            "new_value": clean_val,
            "affected_count": len(affected),
            "affected_commitments": affected,
            "notices_posted": notices,
            "min_next_due_at": min_due,
            "revision": int(state.get("revision") or 0),
            "disk_written": changed,
            "llm_calls": 0,
        }


def contact_npc_or_venue(
    day_root: Path | str,
    *,
    venue_id: str,
    npc_id: Optional[str] = None,
    contact_action: str = "observe_or_greet",
    commitment_id: Optional[str] = None,
    actor_side: str = "subject",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
    extra_params: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Trigger 3 (``DIRECT_CONTACT``): visit a venue / meet an NPC / settle pickup or appointment.

    Enforces ``400 RETROACTIVE_FABRICATION_FORBIDDEN`` before touching disk if
    ``extra_params`` attempts to smuggle mood, private desire, chat topics, or
    backstory fabrication. Fast-forwards due commitments strictly from persisted
    state up to ``now_iso``, updates epistemic ``subject_observed_venues``, and
    executes the requested contact action.
    """
    # Hard anti-retroactive-fabrication firewall (0 writes on refusal!)
    if extra_params is not None:
        if not isinstance(extra_params, Mapping):
            raise WorldCommitmentsError(400, "INVALID_FIELDS", "extra_params must be a mapping")
        offending = _scan_forbidden_retroactive_keys(extra_params)
        if offending is not None:
            raise WorldCommitmentsError(
                400,
                "RETROACTIVE_FABRICATION_FORBIDDEN",
                f"Retroactive fabrication or private smuggling key forbidden: {offending}",
            )

    side = _normalize_actor_side(actor_side)
    action = str(contact_action or "observe_or_greet").strip().lower()
    if action not in CONTACT_ACTIONS:
        raise WorldCommitmentsError(
            400,
            "INVALID_FIELDS",
            f"Unsupported contact_action {action!r}; expected one of {CONTACT_ACTIONS}",
        )

    clean_venue_raw = _clean_text_field(venue_id, field_name="venue_id", min_len=1, max_len=64)
    clean_npc_id = _clean_text_field(npc_id, field_name="npc_id", min_len=0, max_len=64)
    clean_cid = _clean_text_field(commitment_id, field_name="commitment_id", min_len=0, max_len=96)
    eff_op_key = _validate_operation_key(operation_key)
    now_dt, now_str = _resolve_now(now_iso)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    payload_hash = _tx.payload_fingerprint({
        "op": "contact_npc_or_venue",
        "actor_side": side,
        "venue_id": clean_venue_raw,
        "npc_id": clean_npc_id,
        "contact_action": action,
        "commitment_id": clean_cid,
    })

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        existing_doc = _read_json_readonly(state_path)
        state = _ensure_state_hydrated(
            existing_doc if existing_doc is not None else _empty_state(resolved_root, now_str),
            resolved_root,
            now_str,
        )

        if eff_op_key:
            reconcile_kind, hit = _tx.reconcile_evicted_or_active_op(
                resolved_root,
                state,
                eff_op_key,
                dict_key="operation_receipts",
                payload_hash=payload_hash,
            )
            if reconcile_kind == "conflict":
                raise WorldCommitmentsError(409, "OPERATION_KEY_CONFLICT", f"operation_key conflict: {eff_op_key}")
            if reconcile_kind == "expired":
                raise WorldCommitmentsError(409, "RETRY_WINDOW_EXPIRED", f"operation_key expired: {eff_op_key}")
            if reconcile_kind in ("hit_full", "hit_evicted") and isinstance(hit, Mapping):
                replay = copy.deepcopy(dict(hit))
                replay["ok"] = True
                replay["already_applied"] = True
                replay["status"] = "already_applied"
                replay["command_status"] = "DUPLICATE"
                replay["llm_calls"] = 0
                return replay

        barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
        if bool(barrier.get("fail_closed")):
            raise WorldCommitmentsError(
                503,
                "FORGET_BARRIER_FAIL_CLOSED",
                "Unified forget barrier failed closed",
            )

        # Step 1: Strictly fast-forward prior persisted due commitments up to now_dt (0 LLM calls).
        _ff_changed, ff_transitions, ff_notices, ff_releases, _ = _advance_due_in_state(
            resolved_root,
            state,
            now_dt=now_dt,
            now_str=now_str,
            decision_callback=None,
            budget=None,
        )

        def _cmt_blocked(row: Mapping[str, Any], cid_key: Optional[str] = None) -> bool:
            return _is_blocked_with_ids(
                row,
                (
                    cid_key if cid_key is not None else row.get("commitment_id"),
                    row.get("commitment_id"),
                    row.get("npc_id"),
                    row.get("venue_id"),
                    row.get("source_ref"),
                    row.get("item_id"),
                ),
                barrier,
            )

        def _notice_blocked(ntc: Mapping[str, Any]) -> bool:
            return _is_blocked_with_ids(
                ntc,
                (
                    ntc.get("notice_id"),
                    ntc.get("commitment_id"),
                    ntc.get("npc_id"),
                    ntc.get("venue_id"),
                ),
                barrier,
            )

        roster = [
            npc
            for npc in state["npcs"].values()
            if isinstance(npc, Mapping)
            and not _is_blocked_with_ids(
                npc,
                (npc.get("npc_id"), npc.get("role_slot"), npc.get("legacy_alias_ids")),
                barrier,
            )
        ]
        matched_npc = _find_npc_in_roster(
            roster,
            npc_id=clean_npc_id or None,
            venue_id=clean_venue_raw,
        )
        canonical_venue = clean_venue_raw if clean_venue_raw == "porch_market" else _canonical_venue(clean_venue_raw)

        # Step 2: Update epistemic subject_observed_venues on subject direct contact.
        subject_observed_at: Optional[str] = None
        if side == "subject":
            obs_map = state.setdefault("subject_observed_venues", {})
            obs_map[canonical_venue] = now_str
            if clean_venue_raw != canonical_venue:
                obs_map[clean_venue_raw] = now_str
            subject_observed_at = now_str

        # Step 3: Derive live presence strictly from schedule + persisted non-blocked commitments.
        all_commitments = [
            row
            for cid_k, row in state["commitments"].items()
            if isinstance(row, Mapping) and not _cmt_blocked(row, cid_k)
        ]
        presence: Optional[dict[str, Any]] = None
        if matched_npc is not None:
            presence = derive_npc_presence_zero_write(matched_npc, now_dt, all_commitments)

        # Check temporary close at this venue even if matched_npc is None
        temp_close_blocking: Optional[dict[str, Any]] = None
        for row in all_commitments:
            if (
                str(row.get("kind") or "") == "INDEPENDENT_NPC_PLAN"
                and str(row.get("effect_kind") or "") == "TEMPORARY_CLOSE"
                and str(row.get("state") or "") == "IN_PROGRESS"
                and _venues_match(str(row.get("venue_id") or ""), canonical_venue)
            ):
                temp_close_blocking = dict(row)
                break

        venue_open = bool(presence["is_open"]) if presence is not None else (temp_close_blocking is None)
        if temp_close_blocking is not None:
            venue_open = False

        # Collect notices at this venue (now observed if actor_side == "subject"), enforcing forget barrier.
        venue_notices_here: list[dict[str, Any]] = []
        for ntc in state.get("venue_notices") or []:
            if not isinstance(ntc, Mapping):
                continue
            if _notice_blocked(ntc):
                continue
            if _venues_match(str(ntc.get("venue_id") or ""), canonical_venue):
                enriched = copy.deepcopy(dict(ntc))
                enriched["observed_by_subject"] = True if side == "subject" else False
                venue_notices_here.append(enriched)

        affected_commitment: Optional[dict[str, Any]] = None

        if action == "pickup_reservation":
            target: Optional[dict[str, Any]] = None
            if clean_cid:
                cand = state["commitments"].get(clean_cid)
                if (
                    isinstance(cand, dict)
                    and str(cand.get("kind") or "") == "BOOK_OR_ITEM_RESERVATION"
                    and _venues_match(str(cand.get("venue_id") or ""), canonical_venue)
                    and not _cmt_blocked(cand, clean_cid)
                ):
                    target = cand
            else:
                for cid in sorted(state["commitments"].keys()):
                    cand = state["commitments"][cid]
                    if not isinstance(cand, dict):
                        continue
                    if str(cand.get("kind") or "") != "BOOK_OR_ITEM_RESERVATION":
                        continue
                    if _cmt_blocked(cand, cid):
                        continue
                    if not _venues_match(str(cand.get("venue_id") or ""), canonical_venue):
                        continue
                    if str(cand.get("state") or "") == "ARRIVED_HOLDING":
                        target = cand
                        break

            if temp_close_blocking is not None:
                # TA walked to the shop and saw the closed door notice -- persist observation before refusing!
                _recompute_min_next_due_at(state)
                state["revision"] = int(state.get("revision") or 0) + 1
                state["updated_at"] = now_str
                _atomic_write_json_0600(day_root, resolved_root, state_path, state)
                raise WorldCommitmentsError(
                    409,
                    "STORE_CLOSED",
                    str(temp_close_blocking.get("door_notice_cn") or "店铺临时闭店中"),
                )

            if target is None:
                raise WorldCommitmentsError(404, "COMMITMENT_NOT_FOUND", "No matching reservation found")

            cur_st = str(target.get("state") or "")
            if cur_st == "HOLD_EXPIRED":
                _recompute_min_next_due_at(state)
                state["revision"] = int(state.get("revision") or 0) + 1
                state["updated_at"] = now_str
                _atomic_write_json_0600(day_root, resolved_root, state_path, state)
                raise WorldCommitmentsError(409, "HOLD_EXPIRED", HOLD_EXPIRED_NOTICE_CN)
            if cur_st == "PICKED_UP":
                raise WorldCommitmentsError(409, "ALREADY_PICKED_UP", "该预订物品已经取回")
            if cur_st != "ARRIVED_HOLDING":
                raise WorldCommitmentsError(
                    409,
                    "RESERVATION_NOT_ARRIVED_YET",
                    f"预订当前状态为 {cur_st}，尚未到店保留",
                )

            target["state"] = "PICKED_UP"
            target["picked_up_at"] = now_str
            target["picked_up_by_side"] = side
            target["next_due_at"] = None
            target["updated_at"] = now_str
            _append_history(
                target,
                at_iso=now_str,
                from_state="ARRIVED_HOLDING",
                to_state="PICKED_UP",
                note_cn=f"已到店取回「{target.get('title_cn', '预订物品')}」。",
            )
            affected_commitment = copy.deepcopy(target)

        elif action == "fulfill_appointment":
            target = None
            if clean_cid:
                cand = state["commitments"].get(clean_cid)
                if (
                    isinstance(cand, dict)
                    and str(cand.get("kind") or "") == "SCHEDULED_APPOINTMENT"
                    and _venues_match(str(cand.get("venue_id") or ""), canonical_venue)
                    and not _cmt_blocked(cand, clean_cid)
                ):
                    target = cand
            else:
                for cid in sorted(state["commitments"].keys()):
                    cand = state["commitments"][cid]
                    if not isinstance(cand, dict):
                        continue
                    if str(cand.get("kind") or "") != "SCHEDULED_APPOINTMENT":
                        continue
                    if _cmt_blocked(cand, cid):
                        continue
                    if not _venues_match(str(cand.get("venue_id") or ""), canonical_venue):
                        continue
                    if str(cand.get("state") or "") in ("ACTIVE_WINDOW", "RESCHEDULED_INDOOR"):
                        target = cand
                        break

            if target is None:
                raise WorldCommitmentsError(404, "COMMITMENT_NOT_FOUND", "No matching appointment found")

            cur_st = str(target.get("state") or "")
            if cur_st == "MISSED_BY_SUBJECT":
                _recompute_min_next_due_at(state)
                state["revision"] = int(state.get("revision") or 0) + 1
                state["updated_at"] = now_str
                _atomic_write_json_0600(day_root, resolved_root, state_path, state)
                raise WorldCommitmentsError(409, "APPOINTMENT_MISSED", "约定时间窗已过")
            if cur_st == "CANCELLED_WITH_NOTICE":
                raise WorldCommitmentsError(409, "WEATHER_CANCELLED", "该约定已取消")
            if cur_st == "FULFILLED":
                raise WorldCommitmentsError(409, "ALREADY_FULFILLED", "该约定已经完成")
            if cur_st != "ACTIVE_WINDOW":
                raise WorldCommitmentsError(
                    409,
                    "APPOINTMENT_NOT_IN_WINDOW",
                    f"约定当前状态为 {cur_st}，尚未到达约定时间窗",
                )

            target["state"] = "FULFILLED"
            target["fulfilled_at"] = now_str
            target["fulfilled_by_side"] = side
            target["next_due_at"] = None
            target["updated_at"] = now_str
            _append_history(
                target,
                at_iso=now_str,
                from_state="ACTIVE_WINDOW",
                to_state="FULFILLED",
                note_cn=f"已按约完成「{target.get('title_cn', '约定')}」。",
            )
            affected_commitment = copy.deepcopy(target)

        # Recompute presence after possible state change (non-blocked commitments only)
        all_commitments_post = [
            row
            for cid_k, row in state["commitments"].items()
            if isinstance(row, Mapping) and not _cmt_blocked(row, cid_k)
        ]
        if matched_npc is not None:
            presence = derive_npc_presence_zero_write(
                matched_npc,
                now_dt,
                all_commitments_post,
            )

        min_due = _recompute_min_next_due_at(state)
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        visible_ff_notices = [
            ntc for ntc in ff_notices
            if isinstance(ntc, Mapping) and not _notice_blocked(ntc)
        ]

        result: dict[str, Any] = {
            "ok": True,
            "status": "applied",
            "command_status": "APPLIED",
            "already_applied": False,
            "contact_action": action,
            "venue_id": canonical_venue,
            "npc": copy.deepcopy(matched_npc) if matched_npc is not None else None,
            "presence": presence,
            "venue_open": venue_open,
            "fast_forwarded_transitions": ff_transitions,
            "fast_forwarded_notices": visible_ff_notices,
            "shelf_releases": ff_releases,
            "commitment": affected_commitment,
            "observed_notices": venue_notices_here,
            "subject_observed_at": subject_observed_at,
            "min_next_due_at": min_due,
            "revision": state["revision"],
            "llm_calls": 0,
        }

        if eff_op_key:
            _tx.stamp_receipt(
                state,
                eff_op_key,
                result,
                revision=state["revision"],
                dict_key="operation_receipts",
                payload_hash=payload_hash,
                committed_at=now_str,
            )
            _tx.trim_receipts_by_op_seq(
                state,
                dict_key="operation_receipts",
                max_keep=MAX_OPERATION_RECEIPTS,
            )
            state["op_seq"] = int(state.get("next_op_seq") or 0)

        _atomic_write_json_0600(day_root, resolved_root, state_path, state)
        return result


# ---------------------------------------------------------------------------
# Zero-Write Read View with Unified Forget Barrier & Epistemic Boundary
# ---------------------------------------------------------------------------


def _is_blocked_with_ids(entity: Mapping[str, Any], extra_ids: Sequence[Any], barrier: Mapping[str, Any]) -> bool:
    if _rlf.is_entity_forget_blocked(entity, barrier):
        return True
    for raw_id in extra_ids:
        if isinstance(raw_id, str) and raw_id.strip():
            if _rlf.is_entity_forget_blocked(raw_id.strip(), barrier):
                return True
        elif isinstance(raw_id, Sequence) and not isinstance(raw_id, (str, bytes)):
            for elem in raw_id:
                if isinstance(elem, str) and elem.strip():
                    if _rlf.is_entity_forget_blocked(elem.strip(), barrier):
                        return True
    return False


def _subject_observed_at_for_venue(
    observed_map: Mapping[str, Any],
    venue_id: str,
) -> Optional[str]:
    if not isinstance(observed_map, Mapping) or not venue_id:
        return None
    best_dt: Optional[datetime] = None
    best_iso: Optional[str] = None
    candidates = set(_venue_aliases_for(venue_id))
    candidates.add(venue_id)
    for key, val in observed_map.items():
        if key in candidates or _venues_match(str(key), venue_id):
            dt = _parse_dt(val)
            if dt is not None and (best_dt is None or dt > best_dt):
                best_dt = dt
                best_iso = _format_iso(dt)
    return best_iso


def read_world_commitments_zero_write(
    day_root: Path | str,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write read projection of persistent NPCs, commitments & venue notices.

    Enforces:
    1. Unified Forget Barrier via ``load_unified_forget_barrier_readonly(day_root)``
       (filtering blocked ``commitment_id`` / ``notice_id`` / ``npc_id`` and failing
       closed cleanly when ``barrier["fail_closed"]`` is ``True``).
    2. Epistemic boundary via ``subject_observed_venues``: every notice carries
       ``observed_by_subject: bool``; in ``actor_side == "subject"`` view,
       unobserved door notices are withheld from ``venue_notices`` until TA visits
       that venue (reporting only ``unobserved_notice_count`` without leaking text).
    3. Strictly 0 disk writes, 0 ``.lock`` creation, and 0 LLM calls.
    """
    side = _normalize_actor_side(actor_side)
    now_dt, now_str = _resolve_now(now_iso)
    resolved_root = _resolve_day_root(day_root)
    binding = _read_instance_binding_zero_write(resolved_root)

    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    if bool(barrier.get("fail_closed")):
        return {
            "ok": True,
            "schema": SCHEMA,
            "state_exists": _is_regular_file(resolved_root.joinpath(*STATE_REL_PATH)),
            "revision": 0,
            "updated_at": None,
            "observed_at": now_str,
            "actor_side": side,
            "instance_binding": {
                "instance_seed_fingerprint": binding["instance_seed_fingerprint"],
                "world_id": binding["world_id"],
                "theme": binding["theme"],
            },
            "min_next_due_at": None,
            "last_weather_seen": None,
            "npcs": [],
            "commitments": [],
            "active_commitments": [],
            "venue_notices": [],
            "observed_venue_notices": [],
            "unobserved_notice_count": 0,
            "subject_observed_venues": {},
            "forget_barrier_active": True,
            "forget_barrier_fail_closed": True,
            "zero_write": True,
            "llm_calls": 0,
        }

    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    raw_state = _read_json_readonly(state_path)
    state_exists = raw_state is not None
    state = raw_state if isinstance(raw_state, Mapping) else {}

    roster = resolve_instance_npc_roster_zero_write(resolved_root)
    persisted_npcs = state.get("npcs")
    if isinstance(persisted_npcs, Mapping) and persisted_npcs:
        merged_roster: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for npc in roster:
            nid = str(npc["npc_id"])
            if nid in persisted_npcs and isinstance(persisted_npcs[nid], Mapping):
                merged = copy.deepcopy(dict(npc))
                merged.update(copy.deepcopy(dict(persisted_npcs[nid])))
                merged_roster.append(merged)
                seen_ids.add(nid)
            else:
                merged_roster.append(copy.deepcopy(npc))
                seen_ids.add(nid)
        roster = merged_roster

    raw_commitments = state.get("commitments")
    commitments_list: list[dict[str, Any]] = []
    if isinstance(raw_commitments, Mapping):
        for cid in sorted(raw_commitments.keys()):
            row = raw_commitments[cid]
            if not isinstance(row, Mapping):
                continue
            if _is_blocked_with_ids(
                row,
                (
                    cid,
                    row.get("commitment_id"),
                    row.get("npc_id"),
                    row.get("venue_id"),
                    row.get("source_ref"),
                    row.get("item_id"),
                ),
                barrier,
            ):
                continue
            commitments_list.append(copy.deepcopy(dict(row)))

    observed_map = state.get("subject_observed_venues")
    observed_venues: dict[str, str] = (
        {str(k): str(v) for k, v in observed_map.items() if isinstance(v, str)}
        if isinstance(observed_map, Mapping)
        else {}
    )

    projected_commitments: list[dict[str, Any]] = []
    for row in commitments_list:
        venue = str(row.get("venue_id") or "")
        obs_iso = _subject_observed_at_for_venue(observed_venues, venue)
        obs_dt = _parse_dt(obs_iso)
        ref_dt = _parse_dt(row.get("updated_at") or row.get("created_at"))
        obs_by_subject = bool(obs_dt is not None and (ref_dt is None or obs_dt >= ref_dt))
        row["is_terminal"] = _is_commitment_terminal(row)
        row["observed_by_subject"] = obs_by_subject
        # Epistemic protection for unobserved independent NPC plans in subject view:
        # hide unobserved door notice details until TA visits that venue.
        if (
            side == "subject"
            and str(row.get("kind") or "") == "INDEPENDENT_NPC_PLAN"
            and not obs_by_subject
        ):
            continue
        projected_commitments.append(row)

    presence_commitments = projected_commitments if side == "subject" else commitments_list
    projected_npcs: list[dict[str, Any]] = []
    for npc in roster:
        if _is_blocked_with_ids(
            npc,
            (npc.get("npc_id"), npc.get("role_slot"), npc.get("legacy_alias_ids")),
            barrier,
        ):
            continue
        npc_copy = copy.deepcopy(dict(npc))
        npc_copy["presence"] = derive_npc_presence_zero_write(npc_copy, now_dt, presence_commitments)
        projected_npcs.append(npc_copy)

    all_notices_projected: list[dict[str, Any]] = []
    raw_notices = state.get("venue_notices")
    if isinstance(raw_notices, Sequence) and not isinstance(raw_notices, (str, bytes)):
        for ntc in raw_notices:
            if not isinstance(ntc, Mapping):
                continue
            if _is_blocked_with_ids(
                ntc,
                (
                    ntc.get("notice_id"),
                    ntc.get("commitment_id"),
                    ntc.get("npc_id"),
                    ntc.get("venue_id"),
                ),
                barrier,
            ):
                continue
            ntc_copy = copy.deepcopy(dict(ntc))
            venue = str(ntc_copy.get("venue_id") or "")
            obs_iso = _subject_observed_at_for_venue(observed_venues, venue)
            obs_dt = _parse_dt(obs_iso)
            posted_dt = _parse_dt(ntc_copy.get("posted_at"))
            obs_by_subject = bool(obs_dt is not None and (posted_dt is None or obs_dt >= posted_dt))
            ntc_copy["observed_by_subject"] = obs_by_subject
            ntc_copy["subject_observed_at"] = obs_iso if obs_by_subject else None
            valid_until_dt = _parse_dt(ntc_copy.get("valid_until"))
            ntc_copy["is_active"] = bool(valid_until_dt is None or now_dt < valid_until_dt)
            all_notices_projected.append(ntc_copy)

    observed_notices = [n for n in all_notices_projected if n["observed_by_subject"]]
    unobserved_count = len(all_notices_projected) - len(observed_notices)
    visible_notices = observed_notices if side == "subject" else all_notices_projected

    return {
        "ok": True,
        "schema": SCHEMA,
        "state_exists": state_exists,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "observed_at": now_str,
        "actor_side": side,
        "instance_binding": (
            copy.deepcopy(dict(state["instance_binding"]))
            if isinstance(state.get("instance_binding"), Mapping)
            else {
                "instance_seed_fingerprint": binding["instance_seed_fingerprint"],
                "world_id": binding["world_id"],
                "theme": binding["theme"],
            }
        ),
        "min_next_due_at": state.get("min_next_due_at"),
        "last_weather_seen": state.get("last_weather_seen"),
        "npcs": projected_npcs,
        "commitments": projected_commitments,
        "active_commitments": [c for c in projected_commitments if not c["is_terminal"]],
        "venue_notices": visible_notices,
        "observed_venue_notices": observed_notices,
        "unobserved_notice_count": unobserved_count,
        "subject_observed_venues": observed_venues,
        "forget_barrier_active": bool(barrier.get("barrier_active")),
        "forget_barrier_fail_closed": False,
        "zero_write": True,
        "llm_calls": 0,
    }


__all__ = [
    "APPOINTMENT_OPEN_STATES",
    "APPOINTMENT_TERMINAL_STATES",
    "CANONICAL_ROLE_SLOTS",
    "COMMITMENT_KINDS",
    "CONTACT_ACTIONS",
    "DEFAULT_RESERVATION_HOLD_HOURS",
    "FORBIDDEN_RETROACTIVE_KEYS",
    "HOLD_EXPIRED_NOTICE_CN",
    "INDEPENDENT_PLAN_EFFECT_KINDS",
    "MAX_COMMITMENTS",
    "MAX_VENUE_NOTICES",
    "PLAN_OPEN_STATES",
    "PLAN_TERMINAL_STATES",
    "RAIN_AND_SEVERE_WEATHERS",
    "RESERVATION_OPEN_STATES",
    "RESERVATION_TERMINAL_STATES",
    "SCHEMA",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "TERMINAL_COMMITMENT_STATES",
    "WorldCommitmentsError",
    "advance_due_commitments",
    "check_heartbeat_needed_zero_write",
    "commitments_state_path",
    "contact_npc_or_venue",
    "create_commitment",
    "derive_npc_presence_zero_write",
    "notify_world_condition_changed",
    "read_world_commitments_zero_write",
    "resolve_instance_npc_roster_zero_write",
    "resolve_npc_by_id_or_alias_zero_write",
]
