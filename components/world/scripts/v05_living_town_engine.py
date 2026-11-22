#!/usr/bin/env python3
"""0.5 Living Town & Dual-Map Engine (Ch. 117-129, zero-LLM, zero-write-on-read).

Wires the pure physics and narrative primitives in ``v05_unified_world_fabric.py``
into a persistent, deterministic living-town state machine stored at
``<day_root>/world/v05_living_town.json``:

1. Elevation map, slope walk cost, mossy shortcuts & overlook sightlines (Ch. 117)
2. Compact time strata (puddle / snowbank / blank grove), growing alley kitten &
   honest rare spectacles (Ch. 119, 122)
3. Zero-purchase third spaces (coin laundromat spin with pocket finds, hillside
   overlook telescope + offline lunar phase, river pier ferry/breeze) (Ch. 120)
4. 5 schedule-driven town regulars with 30-day deduped 3-part living scenes and
   non-numeric qualitative familiarity (Ch. 123, 128)
5. Upstream chalkboard, milk bottle packaging, rainy-night half-price surplus
   bags & warm non-monetary town courtesies (Ch. 121, 129)
6. Dual-embodied map presence, strict agency-respecting walk invitations &
   unplanned midway umbrella reunion (Ch. 127)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORLD_SCRIPTS_DIR.parents[2]
for _p in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from components.world.scripts import v05_unified_world_fabric as fabric
except ImportError:  # pragma: no cover
    import v05_unified_world_fabric as fabric  # type: ignore[no-redef]

try:
    from components.world.scripts import v05_season_solar_pulse as season_pulse
except ImportError:  # pragma: no cover
    import v05_season_solar_pulse as season_pulse  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover
    _LIFE_SCRIPTS_DIR = _REPO_ROOT / "components" / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

_SEASON_SNOW_SHADE_FACTOR: dict[str, float] = {
    "winter": 0.55,
    "autumn": 0.35,
    "spring": 0.28,
    "summer": 0.20,
}


STATE_SCHEMA = "world.v05.living_town.v1"
STATE_REL_PATH: tuple[str, ...] = ("world", "v05_living_town.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_THIRD_SPACE_VISITS = 16
MAX_GROVE_VISITS = 24
MAX_FAVOR_LOGS = 24
MAX_FAMILIARITY_NOTES = 8
MAX_SCENE_RING = 30
MAX_DRIFTING_BOOKS = 16
MAX_BOOK_MARGIN_NOTES = 12
MAX_BULLETIN_NOTICES = 16
MAX_BULLETIN_FOLLOWUPS = 6
DEFAULT_BULLETIN_NOTICE_ID = "notice_alley_cat"

RAINY_WEATHERS: frozenset[str] = frozenset({
    "rain",
    "heavy_rain",
    "shower",
    "drizzle",
    "humid_plum_rain",
    "sea_gale",
    "thunder_blackout",
})

MISTY_WEATHERS: frozenset[str] = frozenset({
    "coastal_mist",
    "fog",
    "breezy_cool",
})

VALID_THEMES: tuple[str, ...] = ("coastal", "mountain", "residential", "rural", "urban")

CORE_SPOT_IDS: tuple[str, ...] = (
    "home",
    "bakery_corner",
    "old_bookshop",
    "coin_laundromat",
    "hillside_overlook",
    "river_pier",
    "blank_grove",
)
VALID_SPOT_IDS: frozenset[str] = frozenset(CORE_SPOT_IDS)
PARTNER_VALID_LOCATIONS: frozenset[str] = VALID_SPOT_IDS | frozenset({"away"})

THIRD_SPACE_SPOT_IDS: tuple[str, ...] = (
    "coin_laundromat",
    "hillside_overlook",
    "river_pier",
    "blank_grove",
)

SPOT_ELEVATION_SPECS: dict[str, dict[str, Any]] = {
    "home": {
        "elevation_m": 12,
        "slope_style": "gentle",
        "grade_pct": 3,
        "base_seconds_from_home": 60,
        "kind": "home",
    },
    "bakery_corner": {
        "elevation_m": 16,
        "slope_style": "gentle",
        "grade_pct": 4,
        "base_seconds_from_home": 140,
        "kind": "shop",
    },
    "old_bookshop": {
        "elevation_m": 20,
        "slope_style": "gentle",
        "grade_pct": 5,
        "base_seconds_from_home": 185,
        "kind": "shop",
    },
    "coin_laundromat": {
        "elevation_m": 14,
        "slope_style": "gentle",
        "grade_pct": 2,
        "base_seconds_from_home": 150,
        "kind": "third_space",
    },
    "hillside_overlook": {
        "elevation_m": 42,
        "slope_style": "stepped",
        "grade_pct": 12,
        "base_seconds_from_home": 300,
        "kind": "third_space",
    },
    "river_pier": {
        "elevation_m": 0,
        "slope_style": "stepped",
        "grade_pct": 8,
        "base_seconds_from_home": 220,
        "kind": "third_space",
    },
    "blank_grove": {
        "elevation_m": 26,
        "slope_style": "gentle",
        "grade_pct": 6,
        "base_seconds_from_home": 205,
        "kind": "third_space",
    },
}

THEME_SPOT_NAMES: dict[str, dict[str, str]] = {
    "coastal": {
        "home": "海坡小屋",
        "bakery_corner": "拐角海盐面包铺",
        "old_bookshop": "潮汐旧书铺",
        "coin_laundromat": "夜灯自助烘衣房",
        "hillside_overlook": "灯塔上坡眺望台",
        "river_pier": "老防波堤渡口",
        "blank_grove": "老榕树下留白空坪",
    },
    "mountain": {
        "home": "山腰木檐小屋",
        "bakery_corner": "拐角窑烤面包铺",
        "old_bookshop": "松阴旧书铺",
        "coin_laundromat": "山街暖风烘衣房",
        "hillside_overlook": "云岭石阶眺望台",
        "river_pier": "溪谷木栈渡口",
        "blank_grove": "老杉树下留白空坪",
    },
    "residential": {
        "home": "安静巷尾小屋",
        "bakery_corner": "拐角热炉面包铺",
        "old_bookshop": "檐下旧书铺",
        "coin_laundromat": "街角自助烘衣房",
        "hillside_overlook": "公园坡顶眺望台",
        "river_pier": "运河石阶水岸",
        "blank_grove": "老槐树下留白空坪",
    },
    "rural": {
        "home": "田埂瓦檐小屋",
        "bakery_corner": "村口麦香面包铺",
        "old_bookshop": "仓房旧书铺",
        "coin_laundromat": "镇口自助烘衣房",
        "hillside_overlook": "茶坡高岗眺望台",
        "river_pier": "小河木桥渡口",
        "blank_grove": "老樟树下留白空坪",
    },
    "urban": {
        "home": "老洋楼顶层小屋",
        "bakery_corner": "巷口手作面包铺",
        "old_bookshop": "转角独立旧书铺",
        "coin_laundromat": "深巷自助烘衣房",
        "hillside_overlook": "高架天台眺望台",
        "river_pier": "滨水老码头",
        "blank_grove": "老梧桐下留白空坪",
    },
}

_LOCATION_ALIASES: dict[str, str] = {
    "home": "home",
    "porch": "home",
    "yard": "home",
    "room": "home",
    "bakery": "bakery_corner",
    "bakery_corner": "bakery_corner",
    "street_corner": "bakery_corner",
    "bookstore": "old_bookshop",
    "bookshop": "old_bookshop",
    "old_bookshop": "old_bookshop",
    "laundromat": "coin_laundromat",
    "coin_laundromat": "coin_laundromat",
    "overlook": "hillside_overlook",
    "hillside_overlook": "hillside_overlook",
    "pier": "river_pier",
    "riverside": "river_pier",
    "river_pier": "river_pier",
    "grove": "blank_grove",
    "blank_grove": "blank_grove",
}

ADJACENT_EDGE_SPECS: dict[tuple[str, str], dict[str, Any]] = {
    ("bakery_corner", "home"): {
        "base_seconds": 140,
        "slope_style": "gentle",
        "shortcut_id": "moss_home_bakery",
        "shortcut_name_cn": "墙根青苔近道（家与面包铺之间）",
    },
    ("coin_laundromat", "home"): {
        "base_seconds": 150,
        "slope_style": "gentle",
        "shortcut_id": "moss_home_laundromat",
        "shortcut_name_cn": "旧石板排水沟捷径（家与烘衣房之间）",
    },
    ("bakery_corner", "old_bookshop"): {
        "base_seconds": 135,
        "slope_style": "gentle",
        "shortcut_id": "moss_bakery_bookshop",
        "shortcut_name_cn": "窄檐串巷近路（面包铺与旧书铺之间）",
    },
    ("blank_grove", "old_bookshop"): {
        "base_seconds": 165,
        "slope_style": "gentle",
        "shortcut_id": "moss_bookshop_grove",
        "shortcut_name_cn": "砖墙野蕨小径（旧书铺与留白空坪之间）",
    },
    ("blank_grove", "hillside_overlook"): {
        "base_seconds": 210,
        "slope_style": "stepped",
        "shortcut_id": "moss_grove_overlook",
        "shortcut_name_cn": "半山青苔石阶（留白空坪与眺望台之间）",
    },
    ("coin_laundromat", "river_pier"): {
        "base_seconds": 190,
        "slope_style": "stepped",
        "shortcut_id": "moss_laundromat_pier",
        "shortcut_name_cn": "旧石坎斜坡近道（烘衣房与渡口之间）",
    },
    ("bakery_corner", "river_pier"): {
        "base_seconds": 205,
        "slope_style": "stepped",
        "shortcut_id": "moss_bakery_pier",
        "shortcut_name_cn": "邮筒旁下坡捷径（面包铺与渡口之间）",
    },
    ("hillside_overlook", "old_bookshop"): {
        "base_seconds": 240,
        "slope_style": "stepped",
        "shortcut_id": "moss_bookshop_overlook",
        "shortcut_name_cn": "老电线杆后山石径（旧书铺与眺望台之间）",
    },
}

_LAUNDROMAT_POCKET_POOL: tuple[Optional[dict[str, Any]], ...] = (
    {
        "item_id": "receipt_stub",
        "name_cn": "旧书铺泛黄小票",
        "paper_like": True,
        "note_cn": "口袋深处摸出半张旧书铺小票，被烘衣机余温烤得微卷。",
    },
    {
        "item_id": "spare_button",
        "name_cn": "棕色备用木扣",
        "paper_like": False,
        "note_cn": "一颗磨圆了边角的木扣安静躺在口袋底，摸起来温温的。",
    },
    None,
    {
        "item_id": "dry_leaf",
        "name_cn": "烘脆的梧桐干叶",
        "paper_like": True,
        "note_cn": "不知哪天顺手塞进兜里的树叶，烘完后带着干爽的草木味。",
    },
    {
        "item_id": "ten_yen_coin",
        "name_cn": "十日元旧硬币",
        "paper_like": False,
        "note_cn": "滚筒停稳后在口袋缝里摸到一枚旧硬币，刚好够下次再多烘几分钟。",
    },
)

FAVOR_KIND_LABELS: dict[str, str] = {
    "return_umbrella": "归还借来的旧伞",
    "watch_stall": "帮忙看顾一会儿铺面",
    "bring_ginger_tea": "顺路带去一杯热姜茶",
    "share_spare_book": "顺手分享一本闲置旧书",
    "return_milk_bottle": "把洗净的玻璃奶瓶放回木箱",
    "help_carry_flour": "搭手拎进一袋面粉",
    "clean_cat_bowl": "顺手替巷口换了干净猫水碗",
}

REGULAR_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "regular_id": "auntie_lin",
        "name_cn": "拐角面包铺林姨",
        "role_cn": "拐角面包铺店主",
        "home_spot": "bakery_corner",
        "courtesy_cn": "面包铺多留一只刚出炉的盐卷",
        "causes": (
            "清晨炉火刚升稳",
            "檐口挂起雨帘前",
            "面粉袋刚拆开半口",
            "傍晚风里带了点凉意",
        ),
        "actions": (
            "林姨把烤盘轻轻叩在木案上",
            "林姨擦净玻璃柜上的水雾",
            "林姨在门边挂好牛皮纸余粮袋",
            "林姨顺手翻了翻门外小黑板",
        ),
        "tails": (
            "巷口飘出一阵暖烘烘的麦香",
            "路过的人都会下意识放慢半步",
            "纸袋口折得整整齐齐",
            "炉子边留着一点不刺眼的暖光",
        ),
    },
    {
        "regular_id": "uncle_zhou",
        "name_cn": "旧书铺周叔",
        "role_cn": "旧书铺店主",
        "home_spot": "old_bookshop",
        "courtesy_cn": "旧书铺帮你把要找的画册先压在柜台下",
        "causes": (
            "午后光影斜过木书架",
            "刚收来一捆旧杂志",
            "傍晚合上第一扇木门板前",
            "巷子刚安静下来",
        ),
        "actions": (
            "周叔用棉布掸去书脊浮灰",
            "周叔给旧画册夹上泛黄纸签",
            "周叔提着搪瓷壶替巷侧猫碗添水",
            "周叔戴着老花镜誊抄借阅目录",
        ),
        "tails": (
            "门口风铃偶尔轻响一声",
            "屋里满是晒过太阳的旧纸气味",
            "墙根那只小碗始终干干净净",
            "柜台角压着几张整齐的书单",
        ),
    },
    {
        "regular_id": "student_chen",
        "name_cn": "夜班烘衣房小陈",
        "role_cn": "夜班烘衣房常客",
        "home_spot": "coin_laundromat",
        "courtesy_cn": "烘衣房角落多留一只暖烘烘的折叠凳",
        "causes": (
            "滚筒刚转到慢速散热档",
            "晚班收工后的深巷风凉",
            "玻璃门外路灯亮成一排",
            "等衣服烘干的这二十分钟",
        ),
        "actions": (
            "小陈靠在暖气口边速写窗外屋脊",
            "小陈把帆布包垫在膝头改草图",
            "小陈听着衣物翻滚声发了一会儿呆",
            "小陈顺手把散落的杂志码回长凳角",
        ),
        "tails": (
            "铅笔沙沙声混在低沉机器声里",
            "屋里弥漫着温和的皂粉气息",
            "谁进来都不打扰彼此的安静",
            "窗玻璃上浮着一层柔软白雾",
        ),
    },
    {
        "regular_id": "feeder_su",
        "name_cn": "巷口喂猫的苏姐",
        "role_cn": "巷口熟脸街坊",
        "home_spot": "blank_grove",
        "courtesy_cn": "巷口顺手帮你留意那只橘猫常晒太阳的墙头",
        "causes": (
            "天刚蒙蒙亮起风时",
            "傍晚炊烟散在坡巷上空",
            "雨点变小刚停的间隙",
            "老树影子拉长到石阶边",
        ),
        "actions": (
            "苏姐蹲在树根旁收起吃空的纸碟",
            "苏姐轻轻敲了敲塑料盒边缘",
            "苏姐往避雨瓦片下放了一小把干粮",
            "苏姐拿干布抹了抹石阶上的积水",
        ),
        "tails": (
            "矮墙后立刻探出两只警觉的猫耳",
            "巷口地面收拾得不留一点碎屑",
            "熟人经过时会自然点个头",
            "风吹过树梢落下一两片干叶",
        ),
    },
    {
        "regular_id": "keeper_he",
        "name_cn": "渡口茶亭何叔",
        "role_cn": "渡口茶亭看亭人",
        "home_spot": "river_pier",
        "courtesy_cn": "雨天路过渡口时茶亭多温着半杯姜茶",
        "causes": (
            "清晨风面还算平整",
            "渡船汽笛刚在远岸响过",
            "雨丝斜斜打湿木亭栏杆",
            "水色在暮色里沉下来",
        ),
        "actions": (
            "何叔把粗瓷茶壶挪到小炉边保温",
            "何叔擦净木牌上的下一班船时刻",
            "何叔搬出两只干爽竹凳放在檐下",
            "何叔望了望坡顶方向的风色",
        ),
        "tails": (
            "水汽里混着淡淡老姜和焙茶香",
            "等船的人不必买什么也能歇脚",
            "岸边缆绳随水波轻晃",
            "亭角那盏暖黄灯泡很早就亮着",
        ),
    },
)

REGULAR_BY_ID: dict[str, dict[str, Any]] = {
    row["regular_id"]: row for row in REGULAR_BLUEPRINTS
}


class V05LivingTownError(Exception):
    """Domain/validation error carrying HTTP status code and machine reason code."""

    def __init__(self, status_code: int, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.message = self.detail


def _strip_emojis_and_ctrl(text: str, max_len: int = 120) -> str:
    cleaned_chars: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars).strip()[:max_len]


def _validate_clean_text(
    raw: Any,
    *,
    field_name: str,
    min_len: int,
    max_len: int,
) -> str:
    if raw is None:
        if min_len > 0:
            raise V05LivingTownError(400, "INVALID_FIELDS", f"Field {field_name!r} is required")
        return ""
    if not isinstance(raw, str):
        raise V05LivingTownError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned_chars: list[str] = []
    for ch in raw:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars).strip()
    if len(cleaned) < min_len:
        raise V05LivingTownError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at least {min_len} characters",
        )
    if len(cleaned) > max_len:
        raise V05LivingTownError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at most {max_len} characters",
        )
    return cleaned


def _validate_operation_key(operation_key: Any) -> Optional[str]:
    if operation_key is None:
        return None
    if not isinstance(operation_key, str):
        raise V05LivingTownError(400, "INVALID_FIELDS", "Field 'operation_key' must be a string")
    cleaned = _strip_emojis_and_ctrl(operation_key, 128)
    if len(operation_key.strip()) > 128:
        raise V05LivingTownError(400, "INVALID_FIELDS", "Field 'operation_key' is too long")
    return cleaned or None


def _normalize_author_side(actor_side: Any) -> str:
    norm = str(actor_side or "partner").strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        raise V05LivingTownError(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _parse_now(now_iso: Optional[str]) -> datetime:
    return season_pulse.parse_iso_to_default_tz(now_iso)


def _format_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _read_json_safe(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _assert_no_symlink_chain(day_root: Path, target_file: Path) -> None:
    if day_root.exists() and day_root.is_symlink():
        raise V05LivingTownError(400, "UNSAFE_SYMLINK", "day_root must not be a symlink")
    parent = target_file.parent
    if parent.exists() and parent.is_symlink():
        raise V05LivingTownError(400, "UNSAFE_SYMLINK", "world directory must not be a symlink")
    if target_file.exists() and target_file.is_symlink():
        raise V05LivingTownError(400, "UNSAFE_SYMLINK", "state file must not be a symlink")


def _atomic_write_json(day_root: Path, target_file: Path, payload: Mapping[str, Any]) -> None:
    _assert_no_symlink_chain(day_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target_file)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _normalize_theme(raw: Any) -> str:
    if isinstance(raw, str):
        low = raw.strip().lower()
        for t in VALID_THEMES:
            if low == t or low.startswith(f"{t}_") or low.endswith(f"_{t}"):
                return t
        if "coast" in low or "sea" in low or "harbor" in low:
            return "coastal"
        if "mount" in low or "hill" in low or "valley" in low:
            return "mountain"
        if "rural" in low or "village" in low or "farm" in low:
            return "rural"
        if "urban" in low or "city" in low or "metro" in low:
            return "urban"
        if "resid" in low or "suburb" in low or "alley" in low:
            return "residential"
    return "coastal"


def _normalize_spot_id(raw: Any, *, allow_away: bool = False, default: str = "home") -> str:
    area_hint: Optional[str] = None
    if isinstance(raw, Mapping):
        raw_area = raw.get("area_id")
        if isinstance(raw_area, str) and raw_area.strip():
            area_hint = raw_area.strip().lower()
        for k in ("location_id", "place_id", "spot_id", "current_place_id", "id"):
            if raw.get(k):
                raw = raw.get(k)
                break
    if isinstance(raw, str):
        key = raw.strip().lower()
        if allow_away and key == "away":
            return "away"
        if key in _LOCATION_ALIASES:
            return _LOCATION_ALIASES[key]
        if key in VALID_SPOT_IDS:
            return key
        bridge_key = f"{key}/{area_hint}" if area_hint else key
        bridge_entry = (
            _instance_paths.CANONICAL_LOCATION_BRIDGE.get(bridge_key)
            or _instance_paths.CANONICAL_LOCATION_BRIDGE.get(key)
        )
        if bridge_entry is not None:
            mapped_spot = str(bridge_entry.get("v05_spot_id") or "")
            if mapped_spot in VALID_SPOT_IDS:
                return mapped_spot
    return default


def _spot_name_cn(theme: str, spot_id: str) -> str:
    if spot_id == "away":
        return "暂时外出不在镇上"
    table = THEME_SPOT_NAMES.get(theme, THEME_SPOT_NAMES["coastal"])
    return table.get(spot_id, spot_id)


def _canonical_edge(spot_a: str, spot_b: str) -> tuple[str, str]:
    return (spot_a, spot_b) if spot_a <= spot_b else (spot_b, spot_a)


def _edge_key(spot_a: str, spot_b: str) -> str:
    a, b = _canonical_edge(spot_a, spot_b)
    return f"{a}__{b}"


def _adjacent_spots_for(spot_id: str) -> list[str]:
    out: list[str] = []
    for a, b in ADJACENT_EDGE_SPECS:
        if a == spot_id:
            out.append(b)
        elif b == spot_id:
            out.append(a)
    return sorted(out)


def _is_rainy(weather: str) -> bool:
    return str(weather).strip().lower() in RAINY_WEATHERS


def _extract_explicit_v04_season(env: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(env, Mapping):
        return None
    norm = season_pulse.normalize_season(env.get("season"))
    if norm is None:
        return None
    if env.get("season_explicit") is True:
        return norm
    if env.get("updated_at") is not None:
        return norm
    if "season_explicit" not in env:
        return norm
    return None


def _extract_world_context_zero_write(
    day_root: Path,
    now_dt: datetime,
) -> dict[str, Any]:
    """Read-only inspection of canonical World state, v04 weather & refusal/body files."""
    paths = _instance_paths.resolve_instance_paths(day_root)
    canonical_root = paths.day_root
    world_doc = (
        _read_json_safe(paths.world_state_json)
        or _read_json_safe(canonical_root / "world" / "world_state.json")
        or _read_json_safe(canonical_root / "data" / "world" / "world_state.json")
        or _read_json_safe(canonical_root / "world_state.json")
    )
    nb_doc = _read_json_safe(canonical_root / "world" / "v04_neighborhood_state.json")
    canonical_loc = _instance_paths.read_canonical_world_location_zero_write(canonical_root)

    weather = "clear"
    ground_state: Optional[str] = None
    explicit_season: Optional[str] = None
    theme = "coastal"
    theme_resolved = False
    ta_location_id = (
        str(canonical_loc["v05_spot_id"])
        if canonical_loc.get("found") and canonical_loc.get("v05_spot_id") in VALID_SPOT_IDS
        else "home"
    )
    days_lived = 0
    yesterday_gale = False
    rainy_streak_days = 0
    ws_resting = False
    ws_sick = False
    ws_refusal_lock = False
    ws_solo_lock = False
    ws_route_origin: Optional[str] = None
    ws_route_target: Optional[str] = None
    ws_progress_ratio: Optional[float] = None

    if isinstance(nb_doc, Mapping):
        env = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
        if env.get("weather"):
            weather = str(env["weather"]).strip().lower()
        for gkey in ("ground_state", "ground_wetness", "ground"):
            if isinstance(env.get(gkey), str) and str(env.get(gkey)).strip():
                ground_state = str(env.get(gkey)).strip().lower()
                break
        if ground_state is None and isinstance(env.get("ground_by_place"), Mapping):
            vals = [
                str(v or "").strip().lower()
                for v in env["ground_by_place"].values()
                if str(v or "").strip()
            ]
            wet_vals = [v for v in vals if v not in ("dry", "normal")]
            if wet_vals:
                ground_state = wet_vals[0]
            elif vals:
                ground_state = "dry"
        if ground_state is None and env.get("yard_bench_damp") is True:
            ground_state = "damp"
        nb_season = _extract_explicit_v04_season(env)
        if nb_season is not None:
            explicit_season = nb_season

    if isinstance(world_doc, Mapping):
        env_w = world_doc.get("environment") if isinstance(world_doc.get("environment"), Mapping) else {}
        for cand in (world_doc.get("weather"), env_w.get("weather")):
            if isinstance(cand, str) and cand.strip():
                weather = cand.strip().lower()
                break
        for cand in (world_doc.get("season"), env_w.get("season")):
            norm_s = season_pulse.normalize_season(cand)
            if norm_s is not None:
                explicit_season = norm_s
                break
        for cand in (
            world_doc.get("world_theme"),
            world_doc.get("theme"),
            world_doc.get("town_theme"),
            world_doc.get("setting_theme"),
            env_w.get("theme"),
        ):
            if isinstance(cand, str) and cand.strip():
                theme = _normalize_theme(cand)
                theme_resolved = True
                break
        if not canonical_loc.get("found"):
            for cand in (
                world_doc.get("location_id"),
                world_doc.get("current_location_id"),
                world_doc.get("location"),
            ):
                if cand is not None:
                    ta_location_id = _normalize_spot_id(cand, default="home")
                    break
        timeline = world_doc.get("timeline") if isinstance(world_doc.get("timeline"), Mapping) else {}
        for cand in (
            world_doc.get("days_lived"),
            world_doc.get("living_days"),
            world_doc.get("day_index"),
            timeline.get("days_lived"),
        ):
            if isinstance(cand, (int, float)) and not isinstance(cand, bool):
                days_lived = max(0, int(cand))
                break
        if isinstance(world_doc.get("yesterday_gale"), bool):
            yesterday_gale = bool(world_doc["yesterday_gale"])
        if isinstance(world_doc.get("rainy_streak_days"), int) and not isinstance(world_doc.get("rainy_streak_days"), bool):
            rainy_streak_days = max(0, int(world_doc["rainy_streak_days"]))

        ws_refusal_lock = bool(
            world_doc.get("refusal_lock_active")
            or world_doc.get("refusal_lock")
        )
        ws_solo_lock = bool(world_doc.get("solo_walk_lock"))
        ws_resting = bool(world_doc.get("resting") or world_doc.get("is_resting"))
        ws_sick = bool(world_doc.get("sick") or world_doc.get("is_sick"))

        act = str(world_doc.get("activity") or world_doc.get("status") or "").strip().lower()
        if act in ("rest", "resting", "sleep", "sleeping", "nap", "quiet_rest", "sick_rest"):
            ws_resting = True
        if act in ("sick", "ill", "fever", "sick_rest"):
            ws_sick = True

        body = world_doc.get("body") if isinstance(world_doc.get("body"), Mapping) else {}
        health = world_doc.get("health") if isinstance(world_doc.get("health"), Mapping) else {}
        for section in (body, health):
            st = str(section.get("status") or section.get("state") or "").strip().lower()
            if st in ("resting", "rest", "sleep", "exhausted", "fatigued"):
                ws_resting = True
            if st in ("sick", "ill", "fever", "unwell", "recovering"):
                ws_sick = True
            if section.get("sick") or section.get("is_sick") or section.get("illness"):
                ws_sick = True
            if section.get("resting") or section.get("needs_rest"):
                ws_resting = True

        route = world_doc.get("route") if isinstance(world_doc.get("route"), Mapping) else {}
        if route.get("origin") and route.get("target"):
            ws_route_origin = _normalize_spot_id(route.get("origin"), default=ta_location_id)
            ws_route_target = _normalize_spot_id(route.get("target"), default="home")
            if isinstance(route.get("progress_ratio"), (int, float)):
                ws_progress_ratio = max(0.0, min(1.0, float(route["progress_ratio"])))

    if not theme_resolved and paths.world_package_dir is not None:
        pkg_world = _read_json_safe(paths.world_package_dir / "world.json")
        if isinstance(pkg_world, Mapping):
            raw_pkg_theme = pkg_world.get("theme") or pkg_world.get("world_theme")
            if isinstance(raw_pkg_theme, str) and raw_pkg_theme.strip():
                theme = _normalize_theme(raw_pkg_theme)

    # Read-only scan of common refusal/body sidecars if present
    for rel in (
        ("state", "refusal_lock.json"),
        ("world", "refusal_lock.json"),
        ("phone", "refusal_lock.json"),
        ("state", "v02_refusal_state.json"),
        ("world", "body_state.json"),
        ("state", "body_state.json"),
    ):
        doc = _read_json_safe(canonical_root.joinpath(*rel))
        if not isinstance(doc, Mapping):
            continue
        if doc.get("refusal_lock_active") or doc.get("active") is True or doc.get("locked") is True:
            if "refusal" in rel[-1]:
                ws_refusal_lock = True
        if doc.get("refusal_lock_active"):
            ws_refusal_lock = True
        if doc.get("solo_walk_lock"):
            ws_solo_lock = True
        if doc.get("resting") or doc.get("is_resting"):
            ws_resting = True
        if doc.get("sick") or doc.get("is_sick") or doc.get("illness_active"):
            ws_sick = True
        st = str(doc.get("status") or doc.get("state") or "").strip().lower()
        if st in ("resting", "rest", "sleep", "exhausted"):
            ws_resting = True
        if st in ("sick", "ill", "fever", "unwell"):
            ws_sick = True

    if rainy_streak_days == 0 and _is_rainy(weather):
        rainy_streak_days = 1

    pulse = season_pulse.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=explicit_season,
        weather=weather,
    )
    season = str(pulse["season"])

    eff_ground_state = ground_state or ("wet" if _is_rainy(weather) else "dry")

    return {
        "world_state_found": world_doc is not None,
        "theme": theme,
        "weather": weather,
        "ground_state": eff_ground_state,
        "season": season,
        "season_solar": pulse,
        "hour": int(now_dt.hour),
        "minute": int(now_dt.minute),
        "date_str": now_dt.strftime("%Y-%m-%d"),
        "day_of_month": int(now_dt.day),
        "unix_seconds": float(now_dt.timestamp()),
        "ta_location_id": ta_location_id,
        "days_lived": days_lived,
        "yesterday_gale": yesterday_gale,
        "rainy_streak_days": rainy_streak_days,
        "external_resting": ws_resting,
        "external_sick": ws_sick,
        "external_refusal_lock": ws_refusal_lock,
        "external_solo_walk_lock": ws_solo_lock,
        "external_route_origin": ws_route_origin,
        "external_route_target": ws_route_target,
        "external_progress_ratio": ws_progress_ratio,
    }


def _default_state(ctx: Mapping[str, Any]) -> dict[str, Any]:
    regulars_state: dict[str, dict[str, Any]] = {}
    for bp in REGULAR_BLUEPRINTS:
        regulars_state[bp["regular_id"]] = {
            "familiarity_notes": [],
            "favor_count": 0,
            "unlocked_courtesy_cn": None,
            "last_scene_cn": "",
            "last_encounter_at": None,
        }

    return {
        "schema_version": STATE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "edge_walk_counts": {},
        "discovered_shortcuts": [],
        "ground_strata": {
            "puddle_depth": 0.65 if _is_rainy(str(ctx["weather"])) else 0.0,
            "snowbank_depth": 18.0 if str(ctx["weather"]) == "snow" else 0.0,
            "hours_since_stop": 0.0 if (_is_rainy(str(ctx["weather"])) or str(ctx["weather"]) == "snow") else 4.0,
            "puddle_reflects_lantern": _is_rainy(str(ctx["weather"])),
            "snowbank_crust_mm": 18.0 if str(ctx["weather"]) == "snow" else 0.0,
            "grove_stage": fabric.evolve_blank_grove_lot(int(ctx["days_lived"]), []),
            "grove_visits": [],
        },
        "alley_kitten": {
            "feeding_count": 0,
            "greeting_count": 0,
            "nickname": None,
            "last_seen_spot": "blank_grove",
            "last_seen_note": "常在老树根和矮墙影子里打盹。",
            "last_interaction_at": None,
        },
        "third_spaces": {
            "laundromat_spin_count": 0,
            "last_pocket_find": None,
            "recent_third_space_visits": [],
        },
        "regulars": regulars_state,
        "scene_ring_30d": [],
        "economy_and_favors": {
            "yesterday_gale": bool(ctx["yesterday_gale"]),
            "rainy_streak_days": int(ctx["rainy_streak_days"]),
            "bottles_returned_this_week": 0,
            "carrying_empty_bottle": False,
            "surplus_bag_claimed_date": None,
            "surplus_bag_claim_count": 0,
            "recent_favors": [],
        },
        "partner_presence": {
            "location_id": "home",
            "status": "home",
            "bringing_umbrella": False,
            "companion_note": "在屋里慢慢待着。",
            "last_walk_seconds": 0,
            "last_body_tag": "steady",
            "updated_at": None,
        },
        "ta_state": {
            "location_override": None,
            "solo_walk_lock": False,
            "resting": False,
            "sick": False,
            "refusal_lock_active": False,
            "returning_home_progress": None,
            "companion_note": "",
            "updated_at": None,
        },
        "last_umbrella_reunion": {
            "met_midway": False,
            "meet_node": None,
            "remaining_ratio": 1.0,
            "note_cn": "",
            "occurred_at": None,
        },
        "drifting_bookbox": _starter_drifting_bookbox(),
        "bulletin_followups": _starter_bulletin_followups(),
    }


def _starter_drifting_bookbox() -> list[dict[str, Any]]:
    return [
        {
            "book_id": "book_starter_tide_notes",
            "book_title": "小镇潮汐与四季草木笔记",
            "spot_id": "old_bookshop",
            "status": "IN_BOX",
            "left_by_side": "town",
            "slip_note_cn": "旧书铺木箱里常放着的一本旧册子，扉页夹着半张手绘散步路线，翻完放回箱子就好。",
            "margin_notes": [
                {
                    "note_id": "mn_starter_1",
                    "author_side": "town",
                    "op": "return_with_note",
                    "note_cn": "周叔在页边记了一行字：落雨下午泡杯热茶翻第十八页最安稳。",
                    "created_at": "2026-10-01T10:00:00+08:00",
                }
            ],
            "created_at": "2026-10-01T10:00:00+08:00",
        }
    ]


def _starter_bulletin_followups() -> list[dict[str, Any]]:
    return [
        {
            "notice_id": DEFAULT_BULLETIN_NOTICE_ID,
            "title_cn": "巷口橘猫的小铜铃找回来啦",
            "created_at": "2026-10-01T09:00:00+08:00",
            "followups": [
                {
                    "followup_id": "fu_starter_1",
                    "author_side": "town",
                    "followup_cn": "苏姐昨晚在老树根旁捡到小铜铃，已暂放旧书铺柜台角，主人路过随时能领走。",
                    "created_at": "2026-10-01T18:00:00+08:00",
                }
            ],
        }
    ]


def _load_state_zero_write(day_root: Path, ctx: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    path = _state_path(day_root)
    raw = _read_json_safe(path)
    base = _default_state(ctx)
    if not isinstance(raw, Mapping):
        return base, False

    merged = copy.deepcopy(base)
    merged["revision"] = int(raw.get("revision") or 0)
    merged["updated_at"] = raw.get("updated_at")
    if isinstance(raw.get("edge_walk_counts"), Mapping):
        merged["edge_walk_counts"] = {
            str(k): int(v) for k, v in raw["edge_walk_counts"].items() if isinstance(v, int)
        }
    if isinstance(raw.get("discovered_shortcuts"), list):
        merged["discovered_shortcuts"] = copy.deepcopy(raw["discovered_shortcuts"][:16])

    if isinstance(raw.get("ground_strata"), Mapping):
        gs = raw["ground_strata"]
        for k in ("puddle_depth", "snowbank_depth", "hours_since_stop", "snowbank_crust_mm"):
            if isinstance(gs.get(k), (int, float)):
                merged["ground_strata"][k] = float(gs[k])
        if isinstance(gs.get("puddle_reflects_lantern"), bool):
            merged["ground_strata"]["puddle_reflects_lantern"] = gs["puddle_reflects_lantern"]
        if isinstance(gs.get("grove_stage"), str) and gs["grove_stage"].strip():
            merged["ground_strata"]["grove_stage"] = gs["grove_stage"].strip()
        if isinstance(gs.get("grove_visits"), list):
            merged["ground_strata"]["grove_visits"] = copy.deepcopy(gs["grove_visits"][-MAX_GROVE_VISITS:])

    if isinstance(raw.get("alley_kitten"), Mapping):
        ak = raw["alley_kitten"]
        for k in ("feeding_count", "greeting_count"):
            if isinstance(ak.get(k), int):
                merged["alley_kitten"][k] = max(0, int(ak[k]))
        for k in ("nickname", "last_seen_spot", "last_seen_note", "last_interaction_at"):
            if k in ak:
                merged["alley_kitten"][k] = ak[k]

    if isinstance(raw.get("third_spaces"), Mapping):
        ts = raw["third_spaces"]
        if isinstance(ts.get("laundromat_spin_count"), int):
            merged["third_spaces"]["laundromat_spin_count"] = max(0, int(ts["laundromat_spin_count"]))
        if "last_pocket_find" in ts:
            merged["third_spaces"]["last_pocket_find"] = copy.deepcopy(ts["last_pocket_find"])
        if isinstance(ts.get("recent_third_space_visits"), list):
            merged["third_spaces"]["recent_third_space_visits"] = copy.deepcopy(
                ts["recent_third_space_visits"][-MAX_THIRD_SPACE_VISITS:]
            )

    if isinstance(raw.get("regulars"), Mapping):
        for rid, default_row in merged["regulars"].items():
            saved_row = raw["regulars"].get(rid)
            if isinstance(saved_row, Mapping):
                if isinstance(saved_row.get("familiarity_notes"), list):
                    default_row["familiarity_notes"] = list(saved_row["familiarity_notes"][-MAX_FAMILIARITY_NOTES:])
                if isinstance(saved_row.get("favor_count"), int):
                    default_row["favor_count"] = max(0, int(saved_row["favor_count"]))
                if "unlocked_courtesy_cn" in saved_row:
                    default_row["unlocked_courtesy_cn"] = saved_row["unlocked_courtesy_cn"]
                if isinstance(saved_row.get("last_scene_cn"), str):
                    default_row["last_scene_cn"] = saved_row["last_scene_cn"]
                if "last_encounter_at" in saved_row:
                    default_row["last_encounter_at"] = saved_row["last_encounter_at"]

    if isinstance(raw.get("scene_ring_30d"), list):
        merged["scene_ring_30d"] = [str(x) for x in raw["scene_ring_30d"][-MAX_SCENE_RING:]]

    if isinstance(raw.get("economy_and_favors"), Mapping):
        ef = raw["economy_and_favors"]
        for k in ("yesterday_gale", "carrying_empty_bottle"):
            if isinstance(ef.get(k), bool):
                merged["economy_and_favors"][k] = ef[k]
        for k in ("rainy_streak_days", "bottles_returned_this_week", "surplus_bag_claim_count"):
            if isinstance(ef.get(k), int):
                merged["economy_and_favors"][k] = max(0, int(ef[k]))
        if "surplus_bag_claimed_date" in ef:
            merged["economy_and_favors"]["surplus_bag_claimed_date"] = ef["surplus_bag_claimed_date"]
        if isinstance(ef.get("recent_favors"), list):
            merged["economy_and_favors"]["recent_favors"] = copy.deepcopy(ef["recent_favors"][-MAX_FAVOR_LOGS:])

    if isinstance(raw.get("partner_presence"), Mapping):
        pp = raw["partner_presence"]
        for k in ("location_id", "status", "bringing_umbrella", "companion_note", "last_walk_seconds", "last_body_tag", "updated_at"):
            if k in pp:
                merged["partner_presence"][k] = pp[k]

    if isinstance(raw.get("ta_state"), Mapping):
        ts_ta = raw["ta_state"]
        for k in (
            "location_override",
            "solo_walk_lock",
            "resting",
            "sick",
            "refusal_lock_active",
            "returning_home_progress",
            "companion_note",
            "updated_at",
        ):
            if k in ts_ta:
                merged["ta_state"][k] = ts_ta[k]

    if isinstance(raw.get("last_umbrella_reunion"), Mapping):
        merged["last_umbrella_reunion"].update(copy.deepcopy(raw["last_umbrella_reunion"]))

    if isinstance(raw.get("drifting_bookbox"), list):
        books_out: list[dict[str, Any]] = []
        for row in raw["drifting_bookbox"][-MAX_DRIFTING_BOOKS:]:
            if isinstance(row, Mapping) and row.get("book_id") and row.get("book_title"):
                item = copy.deepcopy(dict(row))
                notes = item.get("margin_notes")
                item["margin_notes"] = (
                    list(notes[-MAX_BOOK_MARGIN_NOTES:]) if isinstance(notes, list) else []
                )
                books_out.append(item)
        merged["drifting_bookbox"] = books_out

    if isinstance(raw.get("bulletin_followups"), list):
        notices_out: list[dict[str, Any]] = []
        for row in raw["bulletin_followups"][-MAX_BULLETIN_NOTICES:]:
            if isinstance(row, Mapping) and row.get("notice_id"):
                item = copy.deepcopy(dict(row))
                fus = item.get("followups")
                item["followups"] = (
                    list(fus[-MAX_BULLETIN_FOLLOWUPS:]) if isinstance(fus, list) else []
                )
                notices_out.append(item)
        merged["bulletin_followups"] = notices_out

    return merged, True


def _deterministic_seed(tag: str) -> int:
    digest = hashlib.sha256(tag.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _compute_walk_between(
    state: Mapping[str, Any],
    from_spot: str,
    to_spot: str,
    *,
    carried_heavy_count: int = 0,
) -> dict[str, Any]:
    if from_spot == "away" or to_spot == "away":
        return {
            "from_spot": from_spot,
            "to_spot": to_spot,
            "walk_seconds": 0,
            "base_walk_seconds": 0,
            "elevation_delta": 0,
            "body_tag": "off_town_transition",
            "is_adjacent": False,
            "shortcut_applied": False,
            "shortcut_id": None,
        }

    elev_from = int(SPOT_ELEVATION_SPECS[from_spot]["elevation_m"])
    elev_to = int(SPOT_ELEVATION_SPECS[to_spot]["elevation_m"])
    if from_spot == to_spot:
        return {
            "from_spot": from_spot,
            "to_spot": to_spot,
            "walk_seconds": 0,
            "base_walk_seconds": 0,
            "elevation_delta": 0,
            "body_tag": "steady",
            "is_adjacent": False,
            "shortcut_applied": False,
            "shortcut_id": None,
        }

    edge = _canonical_edge(from_spot, to_spot)
    edge_spec = ADJACENT_EDGE_SPECS.get(edge)
    is_adjacent = edge_spec is not None
    if edge_spec is not None:
        base_sec = int(edge_spec["base_seconds"])
        slope_style = str(edge_spec["slope_style"])
    else:
        base_sec = max(
            180,
            abs(
                int(SPOT_ELEVATION_SPECS[from_spot]["base_seconds_from_home"])
                - int(SPOT_ELEVATION_SPECS[to_spot]["base_seconds_from_home"])
            )
            + 120,
        )
        slope_style = (
            "stepped"
            if (
                SPOT_ELEVATION_SPECS[from_spot]["slope_style"] == "stepped"
                or SPOT_ELEVATION_SPECS[to_spot]["slope_style"] == "stepped"
            )
            else "gentle"
        )

    raw_walk = fabric.compute_slope_walk_seconds(
        base_sec,
        elev_from,
        elev_to,
        slope_style=slope_style,
        carried_heavy_count=max(0, int(carried_heavy_count)),
    )
    walk_seconds = int(raw_walk["walk_seconds"])
    shortcut_applied = False
    shortcut_id: Optional[str] = None
    if edge_spec is not None:
        ekey = _edge_key(from_spot, to_spot)
        if int(state.get("edge_walk_counts", {}).get(ekey, 0)) >= 2:
            shortcut_applied = True
            shortcut_id = str(edge_spec["shortcut_id"])
            walk_seconds = max(45, int(round(walk_seconds * 0.85)))

    return {
        "from_spot": from_spot,
        "to_spot": to_spot,
        "walk_seconds": walk_seconds,
        "base_walk_seconds": int(raw_walk["walk_seconds"]),
        "elevation_delta": int(raw_walk["elevation_delta"]),
        "body_tag": str(raw_walk["body_tag"]),
        "is_adjacent": is_adjacent,
        "shortcut_applied": shortcut_applied,
        "shortcut_id": shortcut_id,
    }


def _record_edge_walk_and_shortcut(
    state: dict[str, Any],
    from_spot: str,
    to_spot: str,
    now_iso: str,
) -> Optional[dict[str, Any]]:
    if from_spot == to_spot or from_spot not in VALID_SPOT_IDS or to_spot not in VALID_SPOT_IDS:
        return None
    edge = _canonical_edge(from_spot, to_spot)
    edge_spec = ADJACENT_EDGE_SPECS.get(edge)
    if edge_spec is None:
        return None

    ekey = _edge_key(from_spot, to_spot)
    counts = state.setdefault("edge_walk_counts", {})
    counts[ekey] = int(counts.get(ekey, 0)) + 1

    existing_ids = {str(item.get("shortcut_id")) for item in state.get("discovered_shortcuts", [])}
    if counts[ekey] >= 2 and edge_spec["shortcut_id"] not in existing_ids:
        entry = {
            "shortcut_id": str(edge_spec["shortcut_id"]),
            "edge": [edge[0], edge[1]],
            "name_cn": str(edge_spec["shortcut_name_cn"]),
            "walk_count": counts[ekey],
            "unlocked_at": now_iso,
        }
        state.setdefault("discovered_shortcuts", []).append(entry)
        return entry
    for item in state.get("discovered_shortcuts", []):
        if item.get("shortcut_id") == edge_spec["shortcut_id"]:
            item["walk_count"] = counts[ekey]
            break
    return None


def _resolve_kitten_view(state: Mapping[str, Any], days_lived: int, theme: str) -> dict[str, Any]:
    ak = state["alley_kitten"]
    if days_lived <= 6:
        stage = "tiny_kitten"
        stage_label_cn = "巴掌大奶猫"
        appearance_cn = "缩在纸箱阴影里的巴掌大奶猫，尾巴还立不稳。"
    elif days_lived <= 29:
        stage = "curious_juvenile"
        stage_label_cn = "满巷蹿的小半大猫"
        appearance_cn = "耳朵支棱、满巷蹿的小半大猫，听见脚步会先躲到花盆后头偷看。"
    else:
        stage = "sunlit_regular"
        stage_label_cn = "蹲墙头晒太阳的熟脸橘猫"
        appearance_cn = "蹲在矮墙头晒太阳的熟脸橘猫，看见熟人走过会慢悠悠甩两下尾巴。"

    last_spot = _normalize_spot_id(ak.get("last_seen_spot"), default="blank_grove")
    return {
        "days_lived": int(days_lived),
        "stage": stage,
        "stage_label_cn": stage_label_cn,
        "appearance_cn": appearance_cn,
        "feeding_count": int(ak.get("feeding_count", 0)),
        "greeting_count": int(ak.get("greeting_count", 0)),
        "nickname": ak.get("nickname"),
        "last_seen_spot": last_spot,
        "last_seen_spot_name_cn": _spot_name_cn(theme, last_spot),
        "last_seen_note": str(ak.get("last_seen_note") or "在老树影子里晒太阳。"),
        "last_interaction_at": ak.get("last_interaction_at"),
    }


def _project_ground_strata(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    gs = state["ground_strata"]
    days_lived = int(ctx["days_lived"])
    grove_visits = list(gs.get("grove_visits", []))
    grove_stage = fabric.evolve_blank_grove_lot(days_lived, grove_visits)
    season = str(ctx.get("season") or "autumn")
    weather = str(ctx.get("weather") or "clear")
    shade_factor = 0.35 if weather == "snow" else _SEASON_SNOW_SHADE_FACTOR.get(season, 0.35)
    pulse = ctx.get("season_solar") if isinstance(ctx.get("season_solar"), Mapping) else {}
    micro = pulse.get("microclimate") if isinstance(pulse.get("microclimate"), Mapping) else {}
    seasonal_tone_cn = str(
        micro.get("blank_grove_tone_cn")
        or season_pulse.SEASON_MICROCLIMATE_PROFILES.get(season, season_pulse.SEASON_MICROCLIMATE_PROFILES["autumn"])["blank_grove_tone_cn"]
    )

    settled = fabric.settle_ground_puddle_and_snowbank(
        hours_since_stop=float(gs.get("hours_since_stop", 4.0)),
        initial_water=float(gs.get("puddle_depth", 0.0)),
        initial_snow_mm=float(gs.get("snowbank_depth", 0.0)),
        shade_factor=shade_factor,
    )
    compact_core = {
        "p": round(float(gs.get("puddle_depth", 0.0)), 2),
        "s": round(float(gs.get("snowbank_depth", 0.0)), 1),
        "h": round(float(gs.get("hours_since_stop", 4.0)), 1),
        "l": bool(settled["puddle_reflects_lantern"]),
        "c": float(settled["snowbank_crust_mm"]),
        "g": grove_stage,
        "n": len(grove_visits),
    }
    compact_bytes = len(json.dumps(compact_core, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    return {
        "puddle_depth": round(float(gs.get("puddle_depth", 0.0)), 2),
        "snowbank_depth": round(float(gs.get("snowbank_depth", 0.0)), 1),
        "hours_since_stop": round(float(gs.get("hours_since_stop", 4.0)), 1),
        "puddle_reflects_lantern": bool(settled["puddle_reflects_lantern"]),
        "snowbank_crust_mm": float(settled["snowbank_crust_mm"]),
        "grove_stage": grove_stage,
        "seasonal_tone_cn": seasonal_tone_cn,
        "grove_visit_count": len(grove_visits),
        "grove_visits": copy.deepcopy(grove_visits),
        "compact_strata_bytes": compact_bytes,
    }


def _compute_rare_spectacle(
    ctx: Mapping[str, Any],
    strata: Mapping[str, Any],
    lunar: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    weather = str(ctx["weather"])
    hour = int(ctx["hour"])
    day_of_month = int(ctx["day_of_month"])

    if weather == "snow" and (hour >= 20 or hour <= 7 or float(strata["snowbank_depth"]) <= 20.0):
        return {
            "spectacle_id": "first_snow_hush",
            "title_cn": "初雪静巷",
            "spot_id": "blank_grove",
            "elevation_m": SPOT_ELEVATION_SPECS["blank_grove"]["elevation_m"],
            "description_cn": "细雪压住整条巷子的脚步声，旧木檐下只剩一片安安静静的白。",
        }

    if weather in MISTY_WEATHERS and 5 <= hour <= 8:
        return {
            "spectacle_id": "hillside_sea_of_clouds",
            "title_cn": "坡顶晨雾云海",
            "spot_id": "hillside_overlook",
            "elevation_m": SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"],
            "description_cn": "山坡下方全被薄白晨雾盖住，站在高处石阶上只看见几片屋脊浮在雾面。",
        }

    if weather in ("clear", "breezy_cool", "drizzle") and 20 <= hour <= 22 and (
        day_of_month % 5 == 0 or bool(lunar.get("is_full_moon_window"))
    ):
        return {
            "spectacle_id": "missed_ferry_pier_glow",
            "title_cn": "末班渡口水光",
            "spot_id": "river_pier",
            "elevation_m": SPOT_ELEVATION_SPECS["river_pier"]["elevation_m"],
            "description_cn": "末班船刚离岸不久，浮标灯和月色在水面拉出一道晃动的长光带。",
        }

    if weather == "clear" and 17 <= hour <= 18 and (
        day_of_month % 4 == 0 or bool(strata.get("puddle_reflects_lantern"))
    ):
        return {
            "spectacle_id": "sunset_lantern_cascade",
            "title_cn": "晚霞斜坡灯影",
            "spot_id": "hillside_overlook",
            "elevation_m": SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"],
            "description_cn": "夕阳贴着坡道屋檐斜切下来，石阶和水洼顺着地势亮成一串暖色。",
        }

    season = str(ctx.get("season") or "autumn")
    pulse = ctx.get("season_solar") if isinstance(ctx.get("season_solar"), Mapping) else {}
    solar_term = pulse.get("solar_term") if isinstance(pulse.get("solar_term"), Mapping) else {}
    is_term_day = bool(solar_term.get("is_term_day", False))
    fair_sky = weather in ("clear", "breezy_cool", "cold_clear")

    if fair_sky:
        if season == "spring" and (is_term_day or (9 <= hour <= 17 and day_of_month % 7 == 0)):
            return {
                "spectacle_id": "spring_petal_drift",
                "title_cn": "春巷落瓣微风",
                "spot_id": "blank_grove",
                "elevation_m": SPOT_ELEVATION_SPECS["blank_grove"]["elevation_m"],
                "description_cn": "春风斜过老树枝梢，零星浅色花瓣慢慢落进石阶缝里，整条巷子显得格外松弛。",
            }
        if season == "summer" and (is_term_day or (19 <= hour <= 22 and day_of_month % 7 == 0)):
            return {
                "spectacle_id": "summer_firefly_pier_breeze",
                "title_cn": "夏夜渡口流萤晚风",
                "spot_id": "river_pier",
                "elevation_m": SPOT_ELEVATION_SPECS["river_pier"]["elevation_m"],
                "description_cn": "夏夜退热后的水岸微风贴着栏杆吹过，草丛与木桩边偶有零星流萤轻轻一闪。",
            }
        if season == "autumn" and (is_term_day or (15 <= hour <= 17 and day_of_month % 7 == 0)):
            return {
                "spectacle_id": "autumn_ginkgo_golden_steps",
                "title_cn": "深秋石阶金叶斜阳",
                "spot_id": "hillside_overlook",
                "elevation_m": SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"],
                "description_cn": "低斜的秋阳照在半山石阶上，风一吹过，金黄银杏叶顺着台阶边缘铺出一道亮色。",
            }
        if season == "winter" and (is_term_day or (6 <= hour <= 9 and day_of_month % 7 == 0)):
            return {
                "spectacle_id": "winter_rime_roof_glint",
                "title_cn": "冬晨屋脊薄霜晴光",
                "spot_id": "hillside_overlook",
                "elevation_m": SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"],
                "description_cn": "寒晨瓦檐与坡道木栏覆着一层极细的白霜，头一缕晴光落下来时泛起清亮微光。",
            }

    return None


def _regular_schedule_state(regular_id: str, hour: int, weather: str) -> tuple[str, bool, str]:
    rainy = _is_rainy(weather)
    if regular_id == "auntie_lin":
        if 6 <= hour <= 11:
            return "bakery_corner", True, "早炉刚开，柜台边排满热盐卷。"
        if 12 <= hour <= 17:
            return "bakery_corner", True, "午后揉面补货，顺手照看门外小黑板。"
        if 18 <= hour <= 21:
            note = "雨夜七点后把未卖完的面包扎成半价余粮袋。" if rainy else "傍晚擦净玻璃柜，准备慢慢收炉。"
            return "bakery_corner", True, note
        return "bakery_corner", False, "铺子熄了招牌灯，回楼上歇息了。"

    if regular_id == "uncle_zhou":
        if 10 <= hour <= 11:
            return "old_bookshop", True, "上午刚卸下木门板，在店里整理旧书目录与画册。"
        if 12 <= hour <= 18:
            return "old_bookshop", True, "下午在灯下整理旧书目录与画册。"
        if 19 <= hour <= 20:
            if weather == "heavy_rain":
                return "old_bookshop", True, "雨势太大，在门廊内侧替巷口猫碗留了干粮。"
            return "blank_grove", True, "傍晚提着水壶出来替巷侧猫碗添水。"
        return "old_bookshop", False, "木门板已合上，店里只留一盏小台灯。"

    if regular_id == "student_chen":
        if hour >= 19 or hour <= 2:
            return "coin_laundromat", True, "晚班后来烘衣房等滚筒停稳，顺手画速写。"
        if 14 <= hour <= 17:
            return "old_bookshop", True, "下午在旧书铺翻看老画报和设计集。"
        return "coin_laundromat", False, "白天补觉或忙功课，还没出门。"

    if regular_id == "feeder_su":
        if (6 <= hour <= 8) or (17 <= hour <= 19):
            spot = "bakery_corner" if weather == "heavy_rain" else "blank_grove"
            note = "躲在面包铺屋檐下唤巷口的小猫。" if weather == "heavy_rain" else "一早一晚准点来老树下照看巷口小猫。"
            return spot, True, note
        if hour == 10:
            return "bakery_corner", True, "上午顺路买两只吐司，和林姨聊两句天色。"
        return "blank_grove", False, "忙自家事务去了，傍晚还会绕来巷口。"

    # keeper_he
    if 8 <= hour <= 12:
        if weather in ("clear", "breezy_cool"):
            return "hillside_overlook", True, "上午上坡晾茶叶顺便看海风走势。"
        return "river_pier", True, "雨天守在渡口茶亭里温着一壶老姜茶。"
    if 13 <= hour <= 20:
        note = "雨丝飘进亭角，炉上始终温着半壶热姜茶。" if rainy else "守在渡口木亭里看船期，偶尔给熟脸倒杯热茶。"
        return "river_pier", True, note
    return "river_pier", False, "末班船过后收好竹凳，回屋歇着了。"


def _qualitative_familiarity(favor_count: int) -> tuple[str, str]:
    if favor_count <= 0:
        return "点头之交", "平时碰面会温和点个头"
    if favor_count <= 2:
        return "常打招呼", "顺路遇上会互相搭把手、聊两句天气"
    return "会互相留一盏灯", "常来常往的熟脸街坊，彼此记挂着生活小事"


_REGULAR_CLOSED_SCENES: dict[str, dict[str, tuple[str, ...]]] = {
    "auntie_lin": {
        "causes": (
            "夜里招牌灯刚熄下",
            "打烊后的铺子静悄悄的",
            "清晨开炉前的微凉空巷",
        ),
        "actions": (
            "林姨把木案上的烤盘收整归位",
            "林姨隔着玻璃柜检查了明早的面团",
            "林姨在屋里备好明早要用的盐与黄油",
        ),
        "tails": (
            "玻璃门里只剩一点安稳余温",
            "铺前石阶清清爽爽",
            "明早推门时又会飘出新炉麦香",
        ),
    },
    "uncle_zhou": {
        "causes": (
            "上午开铺前木门板还虚掩着",
            "夜里木门板已经合拢",
            "巷子刚安静下来",
        ),
        "actions": (
            "周叔在柜台后静静理着待上架的旧画册",
            "周叔坐在小台灯下誊抄借阅目录",
            "周叔用棉布先掸去书脊浮灰",
        ),
        "tails": (
            "屋里满是晒过太阳的旧纸气味",
            "门板外的小巷清亮安静",
            "柜台角压着几张整齐的书单",
        ),
    },
    "student_chen": {
        "causes": (
            "上午窗帘还拉着半幅",
            "白天补觉后的屋里静悄悄的",
            "傍晚出发前整理着画具",
        ),
        "actions": (
            "小陈把速写本和铅笔留在桌角歇着",
            "小陈在屋里慢慢修补昨天画到一半的草图",
            "小陈还没背上帆布包出门",
        ),
        "tails": (
            "烘衣房角落此刻安安静静空着折凳",
            "要等下午逛书铺或晚班后才会在巷口碰见",
            "桌边搁着一只洗净的旧马克杯",
        ),
    },
    "feeder_su": {
        "causes": (
            "上午刚忙完一轮喂猫",
            "白天巷口老树下风轻日净",
            "夜里巷子彻底静了下来",
        ),
        "actions": (
            "苏姐刚收拾完猫碟回屋忙家事去了",
            "苏姐留在矮墙根的猫水碗干干净净",
            "苏姐提前替墙根纸箱理平了旧毛巾",
        ),
        "tails": (
            "石阶干爽整洁，橘猫在墙头眯眼打盹",
            "巷口地面收拾得不留一点碎屑",
            "等傍晚苏姐忙完还会顺路绕过来看猫",
        ),
    },
    "keeper_he": {
        "causes": (
            "夜里末班船过后水面彻底静下来",
            "清晨开亭前水岸还带着凉气",
        ),
        "actions": (
            "何叔收好檐下竹凳回屋歇着了",
            "何叔把粗瓷茶壶洗净倒扣在木架上",
        ),
        "tails": (
            "岸边缆绳随水波轻晃",
            "渡口木亭安安静静守着水岸",
        ),
    },
}

_REGULAR_EXTRA_COHERENT_POOLS: dict[str, dict[str, tuple[str, ...]]] = {
    "auntie_lin": {
        "causes": (
            "上午阳光照进面包铺",
            "午后巷口风面平缓",
            "烤箱计时器刚轻响一声",
        ),
        "actions": (
            "林姨把刚出炉的盐卷码进藤篮",
            "林姨拿干布拭亮木柜边角",
        ),
        "tails": (
            "柜台边整齐摆着温热面包",
        ),
    },
    "uncle_zhou": {
        "causes": (
            "上午刚卸下木门板",
            "窗外天光匀净照进旧书铺",
        ),
        "actions": (
            "周叔顺手把门边木箱里的画册理平",
        ),
        "tails": (),
    },
    "student_chen": {
        "causes": (
            "午后靠窗书架边格外安静",
            "下午刚从巷口逛进旧书铺",
        ),
        "actions": (
            "小陈站在木架旁翻看老画报和设计集",
        ),
        "tails": (
            "屋里弥漫着晒过太阳的旧纸气味",
        ),
    },
    "feeder_su": {
        "causes": (
            "上午老树叶影晃在石阶边",
            "晴天风里带着干爽草木气",
            "午后巷口难得安静",
        ),
        "actions": (
            "苏姐把干净猫水碗摆回矮墙阴凉处",
            "苏姐顺手拾起树根旁飘落的几片干叶",
        ),
        "tails": (),
    },
    "keeper_he": {
        "causes": (
            "上午坡顶天光清亮",
            "午后水岸风纹平展",
        ),
        "actions": (
            "何叔在坡顶晾开一筛茶叶顺便看海风",
            "何叔顺手替茶亭木桌抹去细灰",
        ),
        "tails": (
            "远处渡口水面风纹清透",
            "山风里带着淡淡干茶香",
        ),
    },
}

_SCENE_RAIN_ONLY_TOKENS: tuple[str, ...] = ("雨帘", "雨点", "雨丝", "雨夜", "避雨", "落雨")
_SCENE_PUDDLE_ONLY_TOKENS: tuple[str, ...] = ("积水", "水洼")
_SCENE_MORNING_TOKENS: tuple[str, ...] = ("清晨", "早炉", "晨间", "上午", "开铺前")
_SCENE_EARLY_DAWN_TOKENS: tuple[str, ...] = ("天刚蒙蒙亮",)
_SCENE_AFTERNOON_TOKENS: tuple[str, ...] = ("午后", "下午")
_SCENE_DAYTIME_TOKENS: tuple[str, ...] = ("白天", "晴天", "阳光", "天光")
_SCENE_EVENING_TOKENS: tuple[str, ...] = ("傍晚", "暮色", "老树影子拉长", "合上第一扇木门板")
_SCENE_NIGHT_TOKENS: tuple[str, ...] = ("晚班", "路灯亮成一排", "深夜", "夜里", "末班船过后", "打烊后")
_SCENE_LAUNDROMAT_TOKENS: tuple[str, ...] = ("滚筒", "烘干", "烘衣房", "皂粉", "暖气口", "白雾")
_SCENE_PIER_TOKENS: tuple[str, ...] = ("渡船", "下一班船", "木亭", "缆绳", "茶亭", "亭角", "水色", "水岸")
_SCENE_OVERLOOK_TOKENS: tuple[str, ...] = ("坡顶", "晾开一筛茶叶", "山风")


def _fragment_matches_live_context(
    text: str,
    *,
    hour: int,
    rainy: bool,
    wet_ground: bool,
    spot_id: str,
    is_out_now: bool,
) -> bool:
    if any(tok in text for tok in _SCENE_RAIN_ONLY_TOKENS) and not rainy:
        return False
    if "余粮袋" in text and not (rainy and hour >= 18):
        return False
    if any(tok in text for tok in _SCENE_PUDDLE_ONLY_TOKENS) and not wet_ground:
        return False
    if any(tok in text for tok in _SCENE_EARLY_DAWN_TOKENS) and not (5 <= hour <= 8):
        return False
    if any(tok in text for tok in _SCENE_MORNING_TOKENS) and not (5 <= hour <= 11):
        return False
    if any(tok in text for tok in _SCENE_AFTERNOON_TOKENS) and not (12 <= hour <= 17):
        return False
    if any(tok in text for tok in _SCENE_DAYTIME_TOKENS) and not (6 <= hour <= 17):
        return False
    if any(tok in text for tok in _SCENE_EVENING_TOKENS) and not (16 <= hour <= 20):
        return False
    if any(tok in text for tok in _SCENE_NIGHT_TOKENS) and not (hour >= 18 or hour <= 5):
        return False
    if "很早就亮着" in text and not (hour >= 16 or hour <= 6 or rainy):
        return False
    if "干爽" in text and rainy:
        return False
    if is_out_now and spot_id == "old_bookshop" and any(tok in text for tok in _SCENE_LAUNDROMAT_TOKENS):
        return False
    if is_out_now and spot_id == "hillside_overlook" and any(tok in text for tok in _SCENE_PIER_TOKENS):
        return False
    if is_out_now and spot_id == "river_pier" and any(tok in text for tok in _SCENE_OVERLOOK_TOKENS):
        return False
    return True


def _compose_coherent_regular_scene(
    bp: Mapping[str, Any],
    *,
    hour: int,
    weather: str,
    is_out_now: bool,
    spot_id: str,
    puddle_depth: float,
    ground_state: str,
    ring: list[str],
    seed_idx: int,
) -> str:
    rid = str(bp["regular_id"])
    rainy = _is_rainy(weather)
    wet_ground = rainy or float(puddle_depth) > 0.0 or str(ground_state or "dry").strip().lower() not in ("dry", "normal", "")

    if not is_out_now and rid in _REGULAR_CLOSED_SCENES:
        closed_pool = _REGULAR_CLOSED_SCENES[rid]
        raw_causes: Sequence[str] = closed_pool["causes"]
        raw_actions: Sequence[str] = closed_pool["actions"]
        raw_tails: Sequence[str] = closed_pool["tails"]
    else:
        extra = _REGULAR_EXTRA_COHERENT_POOLS.get(rid, {})
        raw_causes = tuple(bp["causes"]) + tuple(extra.get("causes", ()))
        raw_actions = tuple(bp["actions"]) + tuple(extra.get("actions", ()))
        raw_tails = tuple(bp["tails"]) + tuple(extra.get("tails", ()))

    def _filter_pool(items: Sequence[str], fallback_text: str) -> list[str]:
        kept = [
            item
            for item in items
            if _fragment_matches_live_context(
                item,
                hour=hour,
                rainy=rainy,
                wet_ground=wet_ground,
                spot_id=spot_id,
                is_out_now=is_out_now,
            )
        ]
        if kept:
            return kept
        # Relax time-only cues while strictly keeping weather/puddle safety
        safe = [
            item
            for item in items
            if not (not rainy and any(tok in item for tok in _SCENE_RAIN_ONLY_TOKENS))
            and not (not wet_ground and any(tok in item for tok in _SCENE_PUDDLE_ONLY_TOKENS))
            and not ("余粮袋" in item and not (rainy and hour >= 18))
        ]
        return safe or [fallback_text]

    causes = _filter_pool(raw_causes, "小镇巷口此刻安静平缓")
    actions = _filter_pool(raw_actions, f"{bp['name_cn']}按自己的步调打理着手边小事")
    tails = _filter_pool(raw_tails, "空气里透着安稳的日常节奏")
    return fabric.compose_deduped_three_part_scene(
        causes,
        actions,
        tails,
        ring,
        seed_idx=seed_idx,
    )


def _project_regulars_zero_write(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
    *,
    strata: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    hour = int(ctx["hour"])
    weather = str(ctx["weather"])
    theme = str(ctx["theme"])
    date_str = str(ctx["date_str"])
    season = str(ctx.get("season") or "autumn")
    ground_state = str(ctx.get("ground_state") or ("wet" if _is_rainy(weather) else "dry"))
    gs = strata if isinstance(strata, Mapping) else state.get("ground_strata", {})
    puddle_depth = float(gs.get("puddle_depth", 0.0)) if isinstance(gs, Mapping) else 0.0
    pulse = ctx.get("season_solar") if isinstance(ctx.get("season_solar"), Mapping) else {}
    tweaks = pulse.get("regular_seasonal_tweaks") if isinstance(pulse.get("regular_seasonal_tweaks"), Mapping) else {}

    # Work on a throwaway copy of the ring so read path is strictly zero-write.
    ring_copy = list(state.get("scene_ring_30d", []))
    out: list[dict[str, Any]] = []
    for bp in REGULAR_BLUEPRINTS:
        rid = bp["regular_id"]
        saved = state["regulars"][rid]
        cur_spot, is_out_now, schedule_note_cn = _regular_schedule_state(rid, hour, weather)
        seed_idx = _deterministic_seed(f"{date_str}|{hour}|{weather}|{rid}")
        scene_cn = _compose_coherent_regular_scene(
            bp,
            hour=hour,
            weather=weather,
            is_out_now=is_out_now,
            spot_id=cur_spot,
            puddle_depth=puddle_depth,
            ground_state=ground_state,
            ring=ring_copy,
            seed_idx=seed_idx,
        )
        fam_status, favor_balance_cn = _qualitative_familiarity(int(saved.get("favor_count", 0)))
        seasonal_item_cn = str(
            tweaks.get(rid)
            or season_pulse.REGULAR_SEASONAL_TWEAKS.get(season, season_pulse.REGULAR_SEASONAL_TWEAKS["autumn"]).get(rid, "")
        )
        out.append({
            "regular_id": rid,
            "name_cn": bp["name_cn"],
            "role_cn": bp["role_cn"],
            "current_spot": cur_spot,
            "current_spot_name_cn": _spot_name_cn(theme, cur_spot),
            "is_out_now": is_out_now,
            "schedule_note_cn": schedule_note_cn,
            "season": season,
            "seasonal_item_cn": seasonal_item_cn,
            "living_scene_cn": scene_cn,
            "last_persisted_scene_cn": str(saved.get("last_scene_cn") or ""),
            "familiarity_status": fam_status,
            "favor_balance": favor_balance_cn,
            "familiarity_notes": list(saved.get("familiarity_notes", [])),
            "unlocked_courtesy_cn": saved.get("unlocked_courtesy_cn"),
            "last_encounter_at": saved.get("last_encounter_at"),
        })
    return out


def _build_elevation_map_and_sightlines(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
    strata: Mapping[str, Any],
    lunar: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    theme = str(ctx["theme"])
    weather = str(ctx["weather"])
    hour = int(ctx["hour"])
    rainy = _is_rainy(weather)

    macro_map = {
        "river_pier": "渡口木亭亮起暖灯，水面停着小渡船" if hour >= 18 else "渡口水面风纹平展，几只水鸟贴岸掠过",
        "bakery_corner": "拐角面包铺雨檐下飘出暖黄灯光" if rainy else "拐角面包铺烟囱升起一缕淡白麦香",
        "blank_grove": f"{strata['grove_stage']}下偶尔有熟脸街坊慢步停驻",
        "old_bookshop": "旧书铺门口摊开两箱晒太阳的旧画册" if not rainy else "旧书铺玻璃窗内透出安静阅读灯",
        "coin_laundromat": "烘衣房玻璃门泛着温热雾气，滚筒缓缓转动",
        "home": "自家小屋窗檐在坡道中段安安静静立着",
        "hillside_overlook": "坡顶石阶风声清透，可以俯看整片小镇屋脊",
    }

    spots_list: list[dict[str, Any]] = []
    for sid in CORE_SPOT_IDS:
        spec = SPOT_ELEVATION_SPECS[sid]
        walk_home = _compute_walk_between(state, "home", sid)
        spots_list.append({
            "spot_id": sid,
            "name_cn": _spot_name_cn(theme, sid),
            "elevation_m": int(spec["elevation_m"]),
            "slope_style": str(spec["slope_style"]),
            "grade_pct": int(spec["grade_pct"]),
            "kind": str(spec["kind"]),
            "adjacent_spots": _adjacent_spots_for(sid),
            "walk_from_home_seconds": int(walk_home["walk_seconds"]),
            "walk_from_home_body_tag": str(walk_home["body_tag"]),
            "macro_activity_cn": macro_map[sid],
        })

    overlook_elev = int(SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"])
    sightline_targets = [
        {
            "spot_id": "river_pier",
            "elevation": SPOT_ELEVATION_SPECS["river_pier"]["elevation_m"],
            "macro_activity_cn": macro_map["river_pier"],
        },
        {
            "spot_id": "bakery_corner",
            "elevation": SPOT_ELEVATION_SPECS["bakery_corner"]["elevation_m"],
            "macro_activity_cn": macro_map["bakery_corner"],
        },
        {
            "spot_id": "blank_grove",
            "elevation": SPOT_ELEVATION_SPECS["blank_grove"]["elevation_m"],
            "macro_activity_cn": macro_map["blank_grove"],
        },
    ]
    visible_landmarks = fabric.resolve_overlook_sightlines(
        overlook_elev,
        weather,
        sightline_targets,
    )
    if visible_landmarks:
        sight_note = f"站在{_spot_name_cn(theme, 'hillside_overlook')}往下望，能清楚看见 {len(visible_landmarks)} 处小镇动静。"
    else:
        sight_note = "水汽和雨雾遮住了低坡屋脊，远处只剩几团柔化的轮廓。"

    elevation_map = {
        "theme": theme,
        "min_elevation_m": 0,
        "max_elevation_m": 42,
        "spots": spots_list,
        "discovered_shortcuts": copy.deepcopy(state.get("discovered_shortcuts", [])),
        "edge_walk_counts": copy.deepcopy(state.get("edge_walk_counts", {})),
    }
    overlook_sightlines = {
        "observer_spot_id": "hillside_overlook",
        "observer_spot_name_cn": _spot_name_cn(theme, "hillside_overlook"),
        "observer_elevation_m": overlook_elev,
        "weather": weather,
        "visibility_clear": len(visible_landmarks) > 0,
        "visible_landmarks": visible_landmarks,
        "lunar_phase": lunar,
        "note_cn": sight_note,
    }
    return elevation_map, overlook_sightlines


def _project_third_spaces(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
    sightlines: Mapping[str, Any],
    strata: Mapping[str, Any],
    lunar: Mapping[str, Any],
) -> dict[str, Any]:
    theme = str(ctx["theme"])
    weather = str(ctx["weather"])
    hour = int(ctx["hour"])
    minute = int(ctx["minute"])
    rainy = _is_rainy(weather)

    next_ferry_in_min = (30 - (minute % 30)) % 30
    if next_ferry_in_min == 0:
        next_ferry_in_min = 30
    ferry_active = 6 <= hour <= 22
    pier_bench_note = (
        "暮色后渡口长椅有晚风，适合安静坐一会儿看水面晃光。"
        if hour >= 18
        else "靠岸长椅空着大半，风带着水汽吹过，不用消费也能歇脚。"
    )
    if rainy:
        pier_bench_note = "细雨敲打亭檐，木长椅往里挪了半尺，听水声格外安稳。"

    ts = state["third_spaces"]
    return {
        "coin_laundromat": {
            "spot_id": "coin_laundromat",
            "name_cn": _spot_name_cn(theme, "coin_laundromat"),
            "elevation_m": SPOT_ELEVATION_SPECS["coin_laundromat"]["elevation_m"],
            "spin_count": int(ts.get("laundromat_spin_count", 0)),
            "ambiance_cn": "滚筒低声转着，玻璃门凝着柔雾，角落长凳一直暖烘烘的。",
            "last_pocket_find": copy.deepcopy(ts.get("last_pocket_find")),
        },
        "hillside_overlook": {
            "spot_id": "hillside_overlook",
            "name_cn": _spot_name_cn(theme, "hillside_overlook"),
            "elevation_m": SPOT_ELEVATION_SPECS["hillside_overlook"]["elevation_m"],
            "telescope_clear": bool(sightlines["visibility_clear"]),
            "visible_landmarks": list(sightlines["visible_landmarks"]),
            "lunar_phase": copy.deepcopy(lunar),
            "ambiance_cn": (
                "免费投币望远镜拆掉了锁扣，晴朗时能把渡口和面包铺屋檐尽收眼底。"
                if sightlines["visibility_clear"]
                else "镜片蒙着薄雾，只能辨出山下零星几盏暖灯，吹吹风也很舒服。"
            ),
        },
        "river_pier": {
            "spot_id": "river_pier",
            "name_cn": _spot_name_cn(theme, "river_pier"),
            "elevation_m": SPOT_ELEVATION_SPECS["river_pier"]["elevation_m"],
            "ferry_running": ferry_active,
            "next_ferry_minutes": next_ferry_in_min if ferry_active else None,
            "sparkler_bench_open": (18 <= hour <= 22 and not rainy),
            "ambiance_cn": pier_bench_note,
        },
        "blank_grove": {
            "spot_id": "blank_grove",
            "name_cn": _spot_name_cn(theme, "blank_grove"),
            "elevation_m": SPOT_ELEVATION_SPECS["blank_grove"]["elevation_m"],
            "grove_stage": str(strata["grove_stage"]),
            "seasonal_tone_cn": str(strata.get("seasonal_tone_cn") or ""),
            "ambiance_cn": f"这片空地如今慢慢长成了「{strata['grove_stage']}」，谁来都能安安静静歇脚。",
        },
        "recent_third_space_visits": copy.deepcopy(ts.get("recent_third_space_visits", [])),
    }


def _project_chalkboard_and_favors(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    ef = state["economy_and_favors"]
    weather = str(ctx["weather"])
    hour = int(ctx["hour"])
    date_str = str(ctx["date_str"])

    morning_weather = "rain" if _is_rainy(weather) else weather
    rainy_streak = max(int(ef.get("rainy_streak_days", 0)), int(ctx["rainy_streak_days"]))
    yesterday_gale = bool(ef.get("yesterday_gale", False) or ctx["yesterday_gale"])
    bottles_returned = int(ef.get("bottles_returned_this_week", 0))
    carrying_empty = bool(ef.get("carrying_empty_bottle", False))

    upstream = fabric.apply_upstream_blackboard_and_milk_bottles(
        morning_weather=morning_weather,
        yesterday_gale=yesterday_gale,
        rainy_streak_days=rainy_streak,
        bottles_returned_this_week=bottles_returned,
        carrying_empty_bottle=carrying_empty,
    )

    already_claimed_today = (ef.get("surplus_bag_claimed_date") == date_str)
    surplus_available = _is_rainy(weather) and (hour >= 19) and (not already_claimed_today)
    if surplus_available:
        surplus_note = "雨夜七点后，林姨把未卖完的面包扎成半价余粮袋挂在门边，顺路就能拎走。"
    elif already_claimed_today:
        surplus_note = "今晚的雨夜半价余粮袋已经领走了，林姨说改天落雨再留。"
    else:
        surplus_note = "只有在下雨天的傍晚七点以后，面包铺才会有半价余粮袋。"

    active_courtesies = [
        {
            "regular_id": bp["regular_id"],
            "name_cn": bp["name_cn"],
            "courtesy_cn": state["regulars"][bp["regular_id"]]["unlocked_courtesy_cn"],
        }
        for bp in REGULAR_BLUEPRINTS
        if state["regulars"][bp["regular_id"]].get("unlocked_courtesy_cn")
    ]

    pulse = ctx.get("season_solar") if isinstance(ctx.get("season_solar"), Mapping) else {}
    micro = pulse.get("microclimate") if isinstance(pulse.get("microclimate"), Mapping) else {}
    solar_term = pulse.get("solar_term") if isinstance(pulse.get("solar_term"), Mapping) else {}

    return {
        "diner_special": upstream["diner_special"],
        "seasonal_special_cn": str(micro.get("diner_seasonal_special_cn") or ""),
        "solar_term_name_cn": str(solar_term.get("term_name_cn") or ""),
        "chalkboard_crossed_out": upstream["chalkboard_crossed_out"],
        "bakery_extra": upstream["bakery_extra"],
        "milk_packaging": upstream["milk_packaging"],
        "milk_price_jpy": int(upstream["milk_price_jpy"]),
        "bottles_returned_this_week": bottles_returned,
        "rainy_streak_days": rainy_streak,
        "yesterday_gale": yesterday_gale,
        "rainy_half_price_bag_available": surplus_available,
        "surplus_bag_already_claimed_today": already_claimed_today,
        "surplus_bag_claim_count": int(ef.get("surplus_bag_claim_count", 0)),
        "surplus_bag_note_cn": surplus_note,
        "active_courtesies": active_courtesies,
        "recent_favors": copy.deepcopy(ef.get("recent_favors", [])),
    }


def _resolve_ta_presence(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    ta_s = state["ta_state"]
    theme = str(ctx["theme"])
    loc = ta_s.get("location_override") or ctx["ta_location_id"] or "home"
    loc = _normalize_spot_id(loc, default="home")

    solo_lock = bool(ta_s.get("solo_walk_lock") or ctx["external_solo_walk_lock"])
    resting = bool(ta_s.get("resting") or ctx["external_resting"])
    sick = bool(ta_s.get("sick") or ctx["external_sick"])
    refusal_lock = bool(ta_s.get("refusal_lock_active") or ctx["external_refusal_lock"])

    progress = ta_s.get("returning_home_progress")
    if progress is None:
        progress = ctx["external_progress_ratio"]

    if sick:
        status = "sick_rest"
    elif resting:
        status = "resting"
    elif refusal_lock:
        status = "quiet_boundary"
    elif solo_lock:
        status = "solo_walk"
    elif state["partner_presence"].get("status") == "walking_together" and state["partner_presence"].get("location_id") == loc:
        status = "walking_together"
    elif loc == "home":
        status = "home"
    else:
        status = "in_town"

    return {
        "location_id": loc,
        "location_name_cn": _spot_name_cn(theme, loc),
        "status": status,
        "solo_walk_lock": solo_lock,
        "resting": resting,
        "sick": sick,
        "refusal_lock_active": refusal_lock,
        "returning_home_progress": progress,
        "route_origin": ctx["external_route_origin"],
        "route_target": ctx["external_route_target"],
        "companion_note": str(ta_s.get("companion_note") or ""),
        "updated_at": ta_s.get("updated_at"),
    }


def _resolve_partner_presence(
    state: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    pp = state["partner_presence"]
    theme = str(ctx["theme"])
    loc = _normalize_spot_id(pp.get("location_id"), allow_away=True, default="home")
    status = str(pp.get("status") or ("home" if loc == "home" else ("away" if loc == "away" else "in_town")))
    if status not in ("home", "in_town", "walking_together", "away"):
        status = "home" if loc == "home" else "in_town"
    return {
        "location_id": loc,
        "location_name_cn": _spot_name_cn(theme, loc),
        "status": status,
        "bringing_umbrella": bool(pp.get("bringing_umbrella", False)),
        "companion_note": str(pp.get("companion_note") or ""),
        "last_walk_seconds": int(pp.get("last_walk_seconds", 0)),
        "last_body_tag": str(pp.get("last_body_tag") or "steady"),
        "updated_at": pp.get("updated_at"),
    }


_SPOT_SEASON_SCENT: dict[str, dict[str, str]] = {
    "home": {
        "spring": "窗台绿植嫩叶与温热清茶香",
        "summer": "晾晒棉布气息和微凉麦茶味",
        "autumn": "旧木书桌与刚冲开的焙茶暖香",
        "winter": "暖灯烘出的木质气与杯口姜茶蒸汽",
    },
    "bakery_corner": {
        "spring": "刚出炉的艾草盐卷与黄油麦香",
        "summer": "柠檬海盐面包皮与微甜酵香",
        "autumn": "肉桂苹果卷与烤栗子吐司焦香",
        "winter": "热红豆奶油包与厚切烤面包的暖甜味",
    },
    "old_bookshop": {
        "spring": "晒过暖阳的泛黄纸页与淡淡松木架香",
        "summer": "竹帘透进的微风混着旧油墨和棉线装帧味",
        "autumn": "夹着干银杏叶的老画册与微涩纸香",
        "winter": "厚棉帘里拢住的旧书墨香与小暖器微燥味",
    },
    "coin_laundromat": {
        "spring": "干净皂粉与烘干棉麻衣物的轻软暖气",
        "summer": "滚筒散出的微热洗衣液香与街角草木味",
        "autumn": "厚外套烘透后的蓬松棉香和淡淡柔顺剂味",
        "winter": "热乎乎的毛衣羊毛气与白雾里的暖皂香",
    },
    "hillside_overlook": {
        "spring": "坡道青草新芽与远处飘来的淡花香",
        "summer": "晒热的石阶气息混着山坡高处清爽草风",
        "autumn": "干爽松针、落叶与低斜秋阳里的微凉风味",
        "winter": "清冽寒风里的薄霜味与远处屋顶淡淡烟火气",
    },
    "river_pier": {
        "spring": "水岸湿润芦苇气与茶亭新茶清香",
        "summer": "凉爽水汽、旧木桩味与壶里冰镇麦茶香",
        "autumn": "平缓水波带起的微凉水气与老炉焙茶香",
        "winter": "寒水雾气里飘来的老姜茶与木亭炭火暖味",
    },
    "blank_grove": {
        "spring": "老树嫩叶、湿润泥土与碎瓣清香",
        "summer": "浓荫底下的青苔凉气与晒暖树皮味",
        "autumn": "脚边干脆落叶与树根处的泥土草木气",
        "winter": "清瘦枯枝、冷石阶与冬日晴光里的干冷空气",
    },
}

_SPOT_TIME_SOUND: dict[str, dict[str, str]] = {
    "home": {
        "morning": "清晨烧水壶轻响与窗外零星早起鸟鸣",
        "midday": "午后钟表慢走声与远处巷口偶有车轮滚过",
        "evening": "傍晚厨台轻碰瓷碗声和楼下街坊归家脚步",
        "night": "深夜台灯极轻电流声与屋檐外安稳夜风",
    },
    "bakery_corner": {
        "morning": "烤盘叩在木案的清脆声与炉门开合暖响",
        "midday": "揉面声、纸袋摩挲声和路人简短寒暄",
        "evening": "收炉擦拭玻璃柜声与门外小黑板轻晃声",
        "night": "铺子熄灯后的安静巷口只剩远处路灯嗡鸣",
    },
    "old_bookshop": {
        "morning": "木门板逐扇卸下的轻响与翻书沙沙声",
        "midday": "门口铜风铃偶尔轻晃和纸页翻动的慢节奏",
        "evening": "周叔收拾旧画册的轻响与檐下细碎归鸟声",
        "night": "木门合拢后的深巷寂静与偶尔掠过的晚风",
    },
    "coin_laundromat": {
        "morning": "清晨空旷滚筒低沉均匀转动的白噪音",
        "midday": "几台烘衣机错落运转声和硬币落槽脆响",
        "evening": "下班后烘衣房稳定的低频嗡鸣与玻璃门开关声",
        "night": "深夜温暖滚筒翻滚衣物声伴着笔尖轻划纸面",
    },
    "hillside_overlook": {
        "morning": "山坡清晨开阔风声与山下初醒的零星人声",
        "midday": "高处穿风掠过护栏声和远处小镇隐约动静",
        "evening": "暮色风声放缓，山下偶尔传来晚炊与车铃",
        "night": "夜风贴着石阶掠过，整座坡下显得格外安静",
    },
    "river_pier": {
        "morning": "早班渡船轻靠木桩声与水面柔和拍岸声",
        "midday": "岸边缆绳随波轻晃和茶亭竹凳挪动声",
        "evening": "暮色渡船远岸汽笛与水面细密波纹拍打声",
        "night": "停航后缓慢的潮水拍岸声与木亭风声",
    },
    "blank_grove": {
        "morning": "树冠晨风沙沙作响与矮墙边小猫轻巧落脚声",
        "midday": "枝叶在日光里轻晃和石阶边偶尔经过的慢步声",
        "evening": "傍晚树影拉长时的低缓风声与街坊轻声招呼",
        "night": "老树叶隙漏下的夜风声与墙根安安静静的虫息",
    },
}

_WEATHER_SENSORY_MOD: dict[str, tuple[str, str, str]] = {
    "rain": (
        "混着雨滴打湿青石板的清润土气",
        "夹着雨丝敲打檐口与积水的沙沙声",
        "空气湿润微凉，衣领边沾着细软水汽",
    ),
    "snow": (
        "带着细雪落地的冷冽干净气息",
        "雪意把远处杂音压得格外低沉轻软",
        "冷空气清脆沉实，呼气在半空聚成一小团白雾",
    ),
    "mist": (
        "裹着薄雾里柔和的水汽与草木回甘",
        "雾气让远处声响都隔了一层软纱般轻缓",
        "风面柔湿微凉，指尖能感到空气里薄薄一层水意",
    ),
    "clear": (
        "被干爽天光晒得格外通透明净",
        "四周动静清亮分明，听着很舒展",
        "空气干爽利落，拂在脸上节奏刚好",
    ),
}

_SEASON_AIR_BASE: dict[str, str] = {
    "spring": "春日微风松软不扎人",
    "summer": "夏时气朗偏暖，树荫与水边才添一丝凉意",
    "autumn": "秋风干爽清脆，贴着袖口带点微凉",
    "winter": "冬意冷冽紧实，进屋靠近暖源格外熨帖",
}


def _time_bucket(hour: int) -> str:
    if 5 <= hour <= 10:
        return "morning"
    if 11 <= hour <= 16:
        return "midday"
    if 17 <= hour <= 20:
        return "evening"
    return "night"


def _weather_bucket(weather: str) -> str:
    low = str(weather or "clear").strip().lower()
    if low == "snow":
        return "snow"
    if _is_rainy(low):
        return "rain"
    if low in MISTY_WEATHERS:
        return "mist"
    return "clear"


def _project_sound_scent_map(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ch.97 zero-write sensory map (scent, sound, tactile air) for all 7 core town spots."""
    theme = str(ctx["theme"])
    season = str(ctx.get("season") or "autumn")
    if season not in season_pulse.VALID_SEASONS:
        season = "autumn"
    weather = str(ctx.get("weather") or "clear")
    hour = int(ctx.get("hour", 10))
    tb = _time_bucket(hour)
    wb = _weather_bucket(weather)
    pulse = ctx.get("season_solar") if isinstance(ctx.get("season_solar"), Mapping) else {}
    solar_term = pulse.get("solar_term") if isinstance(pulse.get("solar_term"), Mapping) else {}
    term_name = str(solar_term.get("term_name_cn") or "")
    season_air = _SEASON_AIR_BASE.get(season, _SEASON_AIR_BASE["autumn"])
    w_scent, w_sound, w_air = _WEATHER_SENSORY_MOD[wb]

    spot_air_nuance: dict[str, str] = {
        "home": "屋里比屋外更安稳暖和" if wb in ("rain", "snow") or season == "winter" else "窗边风流柔和",
        "bakery_corner": "铺门附近始终烘着一层烤炉暖流",
        "old_bookshop": "木书架之间干爽静定",
        "coin_laundromat": "烘衣机排出的热风把屋角烘得暖融融的",
        "hillside_overlook": "坡顶地势高，风速比巷底明显更开阔",
        "river_pier": "贴近水面，风里始终多带三分水凉",
        "blank_grove": "老树冠挡住直射风日，石阶旁最透气",
    }

    out: list[dict[str, Any]] = []
    for sid in CORE_SPOT_IDS:
        base_scent = _SPOT_SEASON_SCENT[sid][season]
        base_sound = _SPOT_TIME_SOUND[sid][tb]
        term_prefix = f"{term_name}时节，" if term_name else ""
        scent_cn = f"{term_prefix}{base_scent}，{w_scent}。"
        sound_cn = f"{base_sound}，{w_sound}。"
        tactile_air_cn = f"{season_air}；{spot_air_nuance[sid]}，{w_air}。"
        out.append({
            "spot_id": sid,
            "name_cn": _spot_name_cn(theme, sid),
            "scent_cn": scent_cn,
            "sound_cn": sound_cn,
            "tactile_air_cn": tactile_air_cn,
            "season": season,
            "weather": weather,
        })
    return out


def read_living_town_snapshot(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write read projection of the 0.5 Living Town state."""
    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    ctx = _extract_world_context_zero_write(root, now_dt)
    state, state_exists = _load_state_zero_write(root, ctx)

    lunar = fabric.compute_offline_lunar_phase(ctx["unix_seconds"], weather=str(ctx["weather"]))
    strata = _project_ground_strata(state, ctx)
    elevation_map, overlook_sightlines = _build_elevation_map_and_sightlines(state, ctx, strata, lunar)
    alley_kitten = _resolve_kitten_view(state, int(ctx["days_lived"]), str(ctx["theme"]))
    rare_spectacle = _compute_rare_spectacle(ctx, strata, lunar)
    third_spaces = _project_third_spaces(state, ctx, overlook_sightlines, strata, lunar)
    regulars = _project_regulars_zero_write(state, ctx, strata=strata)
    chalkboard_and_favors = _project_chalkboard_and_favors(state, ctx)
    partner_presence = _resolve_partner_presence(state, ctx)
    ta_presence = _resolve_ta_presence(state, ctx)
    sound_scent_map = _project_sound_scent_map(ctx)

    return {
        "schema_version": STATE_SCHEMA,
        "state_exists": state_exists,
        "revision": int(state.get("revision", 0)),
        "observed_at": _format_iso(now_dt),
        "world_context": {
            "theme": ctx["theme"],
            "weather": ctx["weather"],
            "season": ctx["season"],
            "hour": ctx["hour"],
            "date": ctx["date_str"],
            "days_lived": ctx["days_lived"],
            "world_state_found": ctx["world_state_found"],
        },
        "season_solar": copy.deepcopy(ctx["season_solar"]),
        "elevation_map": elevation_map,
        "overlook_sightlines": overlook_sightlines,
        "ground_strata": strata,
        "alley_kitten": alley_kitten,
        "rare_spectacle": rare_spectacle,
        "third_spaces": third_spaces,
        "regulars": regulars,
        "chalkboard_and_favors": chalkboard_and_favors,
        "partner_presence": partner_presence,
        "ta_presence": ta_presence,
        "umbrella_reunion": copy.deepcopy(state["last_umbrella_reunion"]),
        "sound_scent_map": sound_scent_map,
        "drifting_bookbox": copy.deepcopy(state["drifting_bookbox"]),
        "bulletin_followups": copy.deepcopy(state["bulletin_followups"]),
        "llm_calls": 0,
        "zero_write": True,
    }


def _commit_state(day_root: Path, state: dict[str, Any], now_iso: str) -> None:
    with _tx.state_file_lock(_state_path(day_root)):
        state["revision"] = int(state.get("revision", 0)) + 1
        state["updated_at"] = now_iso
        _atomic_write_json(day_root, _state_path(day_root), state)


def _emit_town_lived_episode(
    day_root: Path,
    *,
    episode_key: str,
    topic: str,
    exact_detail: str,
    gist_summary: str,
    occurred_at: str,
    emotional_salience: float = 0.48,
    affect_kind: Optional[str] = None,
    affect_intensity: int = 38,
    shared: bool = True,
    source_refs: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Best-effort canonical memory sink hook (strictly write-path only, zero LLM)."""
    try:
        try:
            from components.life.scripts import lived_memory_sink as _lms
        except ImportError:
            _life_dir = str(_REPO_ROOT / "components" / "life" / "scripts")
            if _life_dir not in sys.path:
                sys.path.insert(0, _life_dir)
            import lived_memory_sink as _lms  # type: ignore[no-redef]
        ev = _lms.LivedEpisodeEvent(
            episode_key=episode_key,
            domain="v05_town",
            topic=topic,
            exact_detail=exact_detail,
            gist_summary=gist_summary,
            occurred_at=occurred_at,
            emotional_salience=emotional_salience,
            affect_kind=affect_kind,
            affect_intensity=affect_intensity,
            shared=shared,
            source_refs=source_refs,
        )
        return _lms.emit_lived_episode_safe(day_root, ev, now_iso=occurred_at)
    except Exception as exc:
        return {"ok": False, "recorded": False, "reason": f"SINK_IMPORT:{type(exc).__name__}", "llm_calls": 0}


def _advance_strata_in_place(
    state: dict[str, Any],
    *,
    weather: str,
    days_lived: int,
    elapsed_hours: float = 1.0,
    season: str = "autumn",
) -> dict[str, Any]:
    gs = state["ground_strata"]
    water = float(gs.get("puddle_depth", 0.0))
    snow = float(gs.get("snowbank_depth", 0.0))
    hrs = float(gs.get("hours_since_stop", 0.0))

    if _is_rainy(weather):
        water = min(3.0, round(water + 0.45 * max(0.5, elapsed_hours), 2))
        hrs = 0.0
    elif weather == "snow":
        snow = min(120.0, round(snow + 6.0 * max(0.5, elapsed_hours), 1))
        hrs = 0.0
    else:
        hrs = round(hrs + max(0.25, elapsed_hours), 2)

    shade_factor = 0.35 if weather == "snow" else _SEASON_SNOW_SHADE_FACTOR.get(season, 0.35)
    settled = fabric.settle_ground_puddle_and_snowbank(
        hours_since_stop=hrs,
        initial_water=water,
        initial_snow_mm=snow,
        shade_factor=shade_factor,
    )
    if not _is_rainy(weather):
        water = round(max(0.0, water - hrs * 0.12), 2)
    if weather != "snow":
        snow = float(settled["snowbank_crust_mm"])

    gs["puddle_depth"] = water
    gs["snowbank_depth"] = snow
    gs["hours_since_stop"] = hrs
    gs["puddle_reflects_lantern"] = bool(settled["puddle_reflects_lantern"])
    gs["snowbank_crust_mm"] = float(settled["snowbank_crust_mm"])
    gs["grove_stage"] = fabric.evolve_blank_grove_lot(days_lived, gs.get("grove_visits", []))
    return copy.deepcopy(gs)


def _append_third_space_trace(
    state: dict[str, Any],
    *,
    spot_id: str,
    theme: str,
    visitor_side: str,
    note_cn: str,
    ambiance_cn: str,
    pocket_find: Optional[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    ts = state["third_spaces"]
    visits = ts.setdefault("recent_third_space_visits", [])
    visit_entry = {
        "visit_id": f"ts_{int(state.get('revision', 0)) + 1}_{len(visits) + 1}",
        "spot_id": spot_id,
        "spot_name_cn": _spot_name_cn(theme, spot_id),
        "visitor_side": visitor_side,
        "note_cn": note_cn,
        "ambiance_cn": ambiance_cn,
        "pocket_find": copy.deepcopy(pocket_find),
        "visited_at": now_iso,
    }
    visits.append(visit_entry)
    if len(visits) > MAX_THIRD_SPACE_VISITS:
        del visits[:-MAX_THIRD_SPACE_VISITS]
    return visit_entry


def visit_third_space(
    day_root: Path | str,
    *,
    spot_id: str,
    visitor_side: str = "partner",
    garment_name_cn: str = "棉布外套",
    carried_kind: str = "empty",
    note_cn: str = "",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Visit a zero-purchase third space and leave a bounded shared trace."""
    norm_spot = _normalize_spot_id(spot_id, default="")
    if norm_spot not in THIRD_SPACE_SPOT_IDS:
        raise V05LivingTownError(
            400,
            "INVALID_THIRD_SPACE",
            f"spot_id must be one of {THIRD_SPACE_SPOT_IDS}, got {spot_id!r}",
        )

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)
    theme = str(ctx["theme"])
    weather = str(ctx["weather"])
    clean_note = _strip_emojis_and_ctrl(str(note_cn or ""), 80)

    pocket_find_out: Optional[dict[str, Any]] = None
    spun_garments: list[dict[str, Any]] = []
    telescope_view: Optional[dict[str, Any]] = None
    ferry_pulse: Optional[dict[str, Any]] = None
    ambiance_cn = ""

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)

        if norm_spot == "coin_laundromat":
            spin_idx = int(state["third_spaces"].get("laundromat_spin_count", 0))
            candidate = _LAUNDROMAT_POCKET_POOL[spin_idx % len(_LAUNDROMAT_POCKET_POOL)]
            clean_garment = _strip_emojis_and_ctrl(str(garment_name_cn or "棉布外套"), 24) or "棉布外套"
            pocket_in: list[dict[str, Any]] = []
            if candidate is not None:
                pocket_in.append({
                    "item_id": candidate["item_id"],
                    "name_cn": candidate["name_cn"],
                    "paper_like": bool(candidate["paper_like"]),
                    "moisture": "damp",
                    "thermal": "cool",
                })
            spun_garments = fabric.settle_laundromat_spin_with_pockets([
                {
                    "garment_name_cn": clean_garment,
                    "moisture": "damp",
                    "thermal": "cool",
                    "pocket_items": pocket_in,
                }
            ])
            state["third_spaces"]["laundromat_spin_count"] = spin_idx + 1
            if spun_garments and spun_garments[0].get("pocket_items"):
                settled_item = dict(spun_garments[0]["pocket_items"][0])
                settled_item["note_cn"] = candidate["note_cn"] if candidate else ""
                pocket_find_out = settled_item
                state["third_spaces"]["last_pocket_find"] = copy.deepcopy(settled_item)
                ambiance_cn = f"滚筒停稳后，{clean_garment}暖烘烘的，口袋里摸出了{settled_item['name_cn']}。"
            else:
                ambiance_cn = f"滚筒停稳后，{clean_garment}彻底烘干带点皂粉余温，口袋空空但很舒服。"
            if not clean_note:
                clean_note = ambiance_cn

        elif norm_spot == "hillside_overlook":
            lunar = fabric.compute_offline_lunar_phase(ctx["unix_seconds"], weather=weather)
            strata = _project_ground_strata(state, ctx)
            _, sightlines = _build_elevation_map_and_sightlines(state, ctx, strata, lunar)
            telescope_view = {
                "visibility_clear": sightlines["visibility_clear"],
                "visible_landmarks": sightlines["visible_landmarks"],
                "lunar_phase": lunar,
                "note_cn": sightlines["note_cn"],
            }
            ambiance_cn = sightlines["note_cn"]
            if not clean_note:
                clean_note = "在坡顶望远镜旁吹了一会儿风。"

        elif norm_spot == "river_pier":
            hour = int(ctx["hour"])
            minute = int(ctx["minute"])
            next_min = (30 - (minute % 30)) % 30 or 30
            ferry_running = 6 <= hour <= 22
            ferry_pulse = {
                "ferry_running": ferry_running,
                "next_ferry_minutes": next_min if ferry_running else None,
                "sparkler_bench_open": (18 <= hour <= 22 and not _is_rainy(weather)),
            }
            ambiance_cn = (
                f"在渡口长椅听了一会儿水声，距下一班船大约 {next_min} 分钟。"
                if ferry_running
                else "渡口已经停航，水面安静映着岸边路灯。"
            )
            if not clean_note:
                clean_note = ambiance_cn

        else:  # blank_grove
            clean_kind = str(carried_kind or "empty").strip().lower()
            if clean_kind not in ("book", "stationery", "food", "drink", "ingredient", "empty"):
                clean_kind = "empty"
            visits = state["ground_strata"].setdefault("grove_visits", [])
            visits.append({
                "carried_kind": clean_kind,
                "actor": visitor_side,
                "visited_at": now_str,
            })
            if len(visits) > MAX_GROVE_VISITS:
                del visits[:-MAX_GROVE_VISITS]
            new_stage = fabric.evolve_blank_grove_lot(int(ctx["days_lived"]), visits)
            state["ground_strata"]["grove_stage"] = new_stage
            ambiance_cn = f"在「{new_stage}」停了一会儿，落叶轻轻挨着鞋边。"
            if not clean_note:
                clean_note = ambiance_cn

        _advance_strata_in_place(
            state,
            weather=weather,
            days_lived=int(ctx["days_lived"]),
            elapsed_hours=0.5,
            season=str(ctx["season"]),
        )
        trace = _append_third_space_trace(
            state,
            spot_id=norm_spot,
            theme=theme,
            visitor_side=visitor_side,
            note_cn=clean_note,
            ambiance_cn=ambiance_cn,
            pocket_find=pocket_find_out,
            now_iso=now_str,
        )
        _commit_state(root, state, now_str)
        grove_stage_out = state["ground_strata"]["grove_stage"]
    spot_label = _spot_name_cn(theme, norm_spot)
    visit_id = str(trace.get("visit_id") or f"ts_{now_str}")
    sink_receipt = _emit_town_lived_episode(
        root,
        episode_key=f"v05_town:visit:{visit_id}",
        topic=f"{spot_label}小停留",
        exact_detail=f"在{spot_label}停留：{ambiance_cn}" + (f"（{clean_note}）" if clean_note and clean_note != ambiance_cn else ""),
        gist_summary=f"去了{spot_label}，{ambiance_cn[:90]}",
        occurred_at=now_str,
        emotional_salience=0.52 if pocket_find_out else 0.44,
        affect_kind="affect_pulse",
        affect_intensity=42 if pocket_find_out else 34,
        shared=True,
        source_refs=(("encounter_id", visit_id), ("spot_id", norm_spot)),
    )

    return {
        "ok": True,
        "spot_id": norm_spot,
        "spot_name_cn": spot_label,
        "ambiance_cn": ambiance_cn,
        "pocket_find": pocket_find_out,
        "spun_garments": spun_garments,
        "telescope_view": telescope_view,
        "ferry_pulse": ferry_pulse,
        "grove_stage": grove_stage_out,
        "visit_trace": trace,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def log_neighbor_favor(
    day_root: Path | str,
    *,
    regular_id: str,
    favor_kind: str = "return_umbrella",
    note_cn: str = "",
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record a warm non-monetary town favor & unlock a grounded town courtesy."""
    rid = str(regular_id or "").strip()
    if rid not in REGULAR_BY_ID:
        raise V05LivingTownError(
            400,
            "UNKNOWN_REGULAR",
            f"regular_id must be one of {sorted(REGULAR_BY_ID)}, got {regular_id!r}",
        )
    fkind = str(favor_kind or "").strip().lower()
    if fkind not in FAVOR_KIND_LABELS:
        raise V05LivingTownError(
            400,
            "INVALID_FAVOR_KIND",
            f"favor_kind must be one of {sorted(FAVOR_KIND_LABELS)}, got {favor_kind!r}",
        )

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)
    bp = REGULAR_BY_ID[rid]
    clean_note = _strip_emojis_and_ctrl(str(note_cn or ""), 80) or FAVOR_KIND_LABELS[fkind]
    hour = int(ctx["hour"])
    weather = str(ctx["weather"])
    ground_state = str(ctx.get("ground_state") or ("wet" if _is_rainy(weather) else "dry"))
    cur_spot, is_out_now, _ = _regular_schedule_state(rid, hour, weather)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        reg = state["regulars"][rid]
        reg["favor_count"] = int(reg.get("favor_count", 0)) + 1
        reg["last_encounter_at"] = now_str

        notes = reg.setdefault("familiarity_notes", [])
        notes.append(f"{ctx['date_str']}：{clean_note}")
        if len(notes) > MAX_FAMILIARITY_NOTES:
            del notes[:-MAX_FAMILIARITY_NOTES]

        newly_unlocked = reg.get("unlocked_courtesy_cn") is None
        reg["unlocked_courtesy_cn"] = str(bp["courtesy_cn"])

        seed_idx = _deterministic_seed(f"{now_str}|{rid}|{fkind}|{reg['favor_count']}")
        puddle_depth = float(state.get("ground_strata", {}).get("puddle_depth", 0.0))
        scene_cn = _compose_coherent_regular_scene(
            bp,
            hour=hour,
            weather=weather,
            is_out_now=is_out_now,
            spot_id=cur_spot,
            puddle_depth=puddle_depth,
            ground_state=ground_state,
            ring=state.setdefault("scene_ring_30d", []),
            seed_idx=seed_idx,
        )
        reg["last_scene_cn"] = scene_cn

        if fkind == "return_milk_bottle":
            state["economy_and_favors"]["bottles_returned_this_week"] = (
                int(state["economy_and_favors"].get("bottles_returned_this_week", 0)) + 1
            )
            state["economy_and_favors"]["carrying_empty_bottle"] = False

        favor_record = {
            "favor_id": f"fav_{int(state.get('revision', 0)) + 1}",
            "regular_id": rid,
            "regular_name_cn": bp["name_cn"],
            "favor_kind": fkind,
            "favor_label_cn": FAVOR_KIND_LABELS[fkind],
            "note_cn": clean_note,
            "actor_side": actor_side,
            "courtesy_cn": reg["unlocked_courtesy_cn"],
            "recorded_at": now_str,
        }
        favors = state["economy_and_favors"].setdefault("recent_favors", [])
        favors.append(favor_record)
        if len(favors) > MAX_FAVOR_LOGS:
            del favors[:-MAX_FAVOR_LOGS]

        _commit_state(root, state, now_str)
        favor_count_out = int(reg["favor_count"])
        unlocked_courtesy_out = reg["unlocked_courtesy_cn"]
    favor_id = str(favor_record["favor_id"])
    sink_receipt = _emit_town_lived_episode(
        root,
        episode_key=f"v05_town:favor:{favor_id}",
        topic=f"小镇邻里照应：{bp['name_cn']}",
        exact_detail=f"与{bp['name_cn']}（{FAVOR_KIND_LABELS[fkind]}）：{clean_note}。{scene_cn}",
        gist_summary=f"和{bp['name_cn']}有了邻里互动（{FAVOR_KIND_LABELS[fkind]}）：{clean_note}",
        occurred_at=now_str,
        emotional_salience=0.58 if newly_unlocked else 0.48,
        affect_kind="mood_shift" if newly_unlocked else "affect_pulse",
        affect_intensity=46 if newly_unlocked else 38,
        shared=True,
        source_refs=(("encounter_id", favor_id), ("regular_id", rid)),
    )
    fam_status, favor_balance_cn = _qualitative_familiarity(favor_count_out)
    return {
        "ok": True,
        "regular_id": rid,
        "regular_name_cn": bp["name_cn"],
        "favor_kind": fkind,
        "note_cn": clean_note,
        "familiarity_status": fam_status,
        "favor_balance": favor_balance_cn,
        "unlocked_courtesy_cn": unlocked_courtesy_out,
        "courtesy_newly_unlocked": newly_unlocked,
        "living_scene_cn": scene_cn,
        "favor_record": favor_record,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def claim_surplus_bag(
    day_root: Path | str,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Claim Auntie Lin's rainy-night half-price surplus bag when genuinely available."""
    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    weather = str(ctx["weather"])
    hour = int(ctx["hour"])
    date_str = str(ctx["date_str"])
    ground_state = str(ctx.get("ground_state") or ("wet" if _is_rainy(weather) else "dry"))

    if not (_is_rainy(weather) and hour >= 19):
        raise V05LivingTownError(
            409,
            "SURPLUS_BAG_UNAVAILABLE",
            "当前不是下雨天傍晚七点以后，面包铺暂未挂出雨夜半价余粮袋。",
        )

    pre_state, _ = _load_state_zero_write(root, ctx)
    if pre_state["economy_and_favors"].get("surplus_bag_claimed_date") == date_str:
        raise V05LivingTownError(
            409,
            "SURPLUS_BAG_ALREADY_CLAIMED",
            "今晚的雨夜半价余粮袋已经领过了，不再重复领取。",
        )

    bp = REGULAR_BY_ID["auntie_lin"]
    cur_spot, is_out_now, _ = _regular_schedule_state("auntie_lin", hour, weather)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        ef = state["economy_and_favors"]
        if ef.get("surplus_bag_claimed_date") == date_str:
            raise V05LivingTownError(
                409,
                "SURPLUS_BAG_ALREADY_CLAIMED",
                "今晚的雨夜半价余粮袋已经领过了，不再重复领取。",
            )

        ef["surplus_bag_claimed_date"] = date_str
        ef["surplus_bag_claim_count"] = int(ef.get("surplus_bag_claim_count", 0)) + 1

        puddle_depth = float(state.get("ground_strata", {}).get("puddle_depth", 0.0))
        scene_cn = _compose_coherent_regular_scene(
            bp,
            hour=hour,
            weather=weather,
            is_out_now=is_out_now,
            spot_id=cur_spot,
            puddle_depth=puddle_depth,
            ground_state=ground_state,
            ring=state.setdefault("scene_ring_30d", []),
            seed_idx=_deterministic_seed(f"{now_str}|surplus_bag"),
        )
        state["regulars"]["auntie_lin"]["last_scene_cn"] = scene_cn
        state["regulars"]["auntie_lin"]["last_encounter_at"] = now_str

        _commit_state(root, state, now_str)
        claim_count_out = ef["surplus_bag_claim_count"]
    surplus_enc_id = f"surplus_bag_{date_str}"
    sink_receipt = _emit_town_lived_episode(
        root,
        episode_key=f"v05_town:surplus_bag:{date_str}",
        topic="雨夜面包铺余粮袋",
        exact_detail=f"在雨夜从林阿姨面包铺领了半价余粮袋：牛皮纸袋里装了一只盐卷、半条核桃吐司和一只苹果小餐包。{scene_cn}",
        gist_summary="雨夜去面包铺领了一袋温热的半价余粮面包",
        occurred_at=now_str,
        emotional_salience=0.54,
        affect_kind="affect_pulse",
        affect_intensity=42,
        shared=True,
        source_refs=(("encounter_id", surplus_enc_id), ("regular_id", "auntie_lin")),
    )
    return {
        "ok": True,
        "claimed": True,
        "claimed_date": date_str,
        "actor_side": actor_side,
        "bag_contents_cn": "牛皮纸袋里装了一只盐卷、半条核桃吐司和一只苹果小餐包。",
        "living_scene_cn": scene_cn,
        "claim_count": claim_count_out,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def greet_or_feed_kitten(
    day_root: Path | str,
    *,
    interaction_kind: str = "feed",
    spot_id: Optional[str] = None,
    nickname: Optional[str] = None,
    note_cn: str = "",
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Greet or feed the growing alley kitten and persist its sighting note."""
    kind = str(interaction_kind or "feed").strip().lower()
    if kind not in ("greet", "feed"):
        raise V05LivingTownError(400, "INVALID_INTERACTION_KIND", "interaction_kind must be 'greet' or 'feed'")

    norm_spot: Optional[str] = None
    if spot_id is not None:
        norm_spot = _normalize_spot_id(spot_id, default="")
        if norm_spot not in VALID_SPOT_IDS:
            raise V05LivingTownError(400, "INVALID_SPOT_ID", f"Unknown spot_id: {spot_id!r}")

    clean_nick: Optional[str] = None
    if nickname is not None:
        clean_nick = _strip_emojis_and_ctrl(str(nickname), 12)
        if not clean_nick:
            raise V05LivingTownError(400, "INVALID_NICKNAME", "nickname must not be empty")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        ak = state["alley_kitten"]
        if norm_spot is not None:
            ak["last_seen_spot"] = norm_spot
        if clean_nick is not None:
            ak["nickname"] = clean_nick

        if kind == "feed":
            ak["feeding_count"] = int(ak.get("feeding_count", 0)) + 1
        else:
            ak["greeting_count"] = int(ak.get("greeting_count", 0)) + 1

        clean_note = _strip_emojis_and_ctrl(str(note_cn or ""), 80)
        if not clean_note:
            spot_label = _spot_name_cn(str(ctx["theme"]), ak["last_seen_spot"])
            clean_note = (
                f"在{spot_label}旁放了一小把干粮，它凑近嗅了两下就安心吃起来。"
                if kind == "feed"
                else f"在{spot_label}墙头冲它轻唤了一声，它慢悠悠眨了眨眼。"
            )
        ak["last_seen_note"] = clean_note
        ak["last_interaction_at"] = now_str

        _commit_state(root, state, now_str)
        kitten_view = _resolve_kitten_view(state, int(ctx["days_lived"]), str(ctx["theme"]))
        k_seq = int(ak["feeding_count"] if kind == "feed" else ak["greeting_count"])
        k_nick = ak.get("nickname") or "小橘猫"
        k_spot = str(ak["last_seen_spot"])
    k_enc_id = f"kitten_{kind}_{ctx['date_str']}_{k_seq}"
    sink_receipt = _emit_town_lived_episode(
        root,
        episode_key=f"v05_town:kitten:{k_enc_id}",
        topic=f"巷口小猫照面（{k_nick}）",
        exact_detail=clean_note,
        gist_summary=f"在巷口遇见并{'喂了' if kind == 'feed' else '招呼了'}小猫：{clean_note[:80]}",
        occurred_at=now_str,
        emotional_salience=0.46,
        affect_kind="affect_pulse",
        affect_intensity=36,
        shared=True,
        source_refs=(("encounter_id", k_enc_id), ("spot_id", k_spot)),
    )
    return {
        "ok": True,
        "interaction_kind": kind,
        "actor_side": actor_side,
        "alley_kitten": kitten_view,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def move_partner_to_spot(
    day_root: Path | str,
    *,
    target_spot_id: str,
    bringing_umbrella: bool = False,
    carried_heavy_count: int = 0,
    companion_note: str = "",
    carried_kind: str = "empty",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Move partner across the town elevation map, track shortcuts & evaluate midway umbrella reunion."""
    norm_target = _normalize_spot_id(target_spot_id, allow_away=True, default="")
    if norm_target not in PARTNER_VALID_LOCATIONS:
        raise V05LivingTownError(
            400,
            "INVALID_SPOT_ID",
            f"target_spot_id must be a valid town spot or 'away', got {target_spot_id!r}",
        )
    if isinstance(carried_heavy_count, bool) or not isinstance(carried_heavy_count, int) or carried_heavy_count < 0:
        raise V05LivingTownError(400, "INVALID_FIELDS", "carried_heavy_count must be a non-negative integer")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)

        pp = state["partner_presence"]
        prev_spot = _normalize_spot_id(pp.get("location_id"), allow_away=True, default="home")
        was_together = str(pp.get("status") or "") == "walking_together"

        walk_info = _compute_walk_between(
            state,
            prev_spot,
            norm_target,
            carried_heavy_count=carried_heavy_count,
        )
        shortcut_unlocked = _record_edge_walk_and_shortcut(state, prev_spot, norm_target, now_str)

        if norm_target == "blank_grove" and prev_spot != "blank_grove":
            clean_kind = str(carried_kind or "empty").strip().lower()
            if clean_kind not in ("book", "stationery", "food", "drink", "ingredient", "empty"):
                clean_kind = "empty"
            visits = state["ground_strata"].setdefault("grove_visits", [])
            visits.append({"carried_kind": clean_kind, "actor": "partner", "visited_at": now_str})
            if len(visits) > MAX_GROVE_VISITS:
                del visits[:-MAX_GROVE_VISITS]
            state["ground_strata"]["grove_stage"] = fabric.evolve_blank_grove_lot(
                int(ctx["days_lived"]),
                visits,
            )

        ta_presence = _resolve_ta_presence(state, ctx)
        ta_loc = str(ta_presence["location_id"])
        rainy = _is_rainy(str(ctx["weather"]))
        departing_from_home = (prev_spot == "home" and norm_target in VALID_SPOT_IDS and norm_target != "home")

        ta_out_nearby = (
            ta_loc in VALID_SPOT_IDS
            and ta_loc != "home"
            and (
                ta_loc == norm_target
                or ta_loc in _adjacent_spots_for(norm_target)
                or ta_loc in _adjacent_spots_for("home")
                or ta_presence.get("route_target") == "home"
            )
        )
        if rainy and bool(bringing_umbrella) and departing_from_home and ta_out_nearby:
            progress = ta_presence.get("returning_home_progress")
            if progress is None:
                progress = 0.45
            reunion_raw = fabric.check_midway_umbrella_reunion(
                partner_departing_from_home=True,
                subject_route=(ta_loc, "home"),
                subject_progress_ratio=float(progress),
                pre_agreed_together=was_together,
            )
        else:
            reunion_raw = fabric.check_midway_umbrella_reunion(
                partner_departing_from_home=False,
                subject_route=None,
                subject_progress_ratio=0.0,
                pre_agreed_together=was_together,
            )

        met_midway = bool(reunion_raw["met_midway"])
        reunion_note = (
            "雨中半路巷口正好迎面撞上，伞往中间偏了一点，剩下半段路并肩慢慢走回家。"
            if met_midway
            else ""
        )
        reunion_record = {
            "met_midway": met_midway,
            "meet_node": reunion_raw["meet_node"],
            "remaining_ratio": float(reunion_raw["remaining_ratio"]),
            "ta_from_spot": ta_loc if met_midway else None,
            "partner_target_spot": norm_target if met_midway else None,
            "note_cn": reunion_note,
            "occurred_at": now_str if met_midway else None,
        }
        state["last_umbrella_reunion"] = reunion_record

        new_status = "home" if norm_target == "home" else ("away" if norm_target == "away" else "in_town")
        clean_note = _strip_emojis_and_ctrl(str(companion_note or ""), 80)
        if not clean_note:
            if met_midway:
                clean_note = reunion_note
            elif norm_target == "home":
                clean_note = "顺坡走回小屋里了。"
            elif norm_target == "away":
                clean_note = "暂时外出办事，晚些再回镇上。"
            else:
                clean_note = f"带着伞往{_spot_name_cn(str(ctx['theme']), norm_target)}走去。" if bringing_umbrella else f"慢慢走到{_spot_name_cn(str(ctx['theme']), norm_target)}。"

        pp["location_id"] = norm_target
        pp["status"] = new_status
        pp["bringing_umbrella"] = bool(bringing_umbrella)
        pp["companion_note"] = clean_note
        pp["last_walk_seconds"] = int(walk_info["walk_seconds"])
        pp["last_body_tag"] = str(walk_info["body_tag"])
        pp["updated_at"] = now_str

        _advance_strata_in_place(
            state,
            weather=str(ctx["weather"]),
            days_lived=int(ctx["days_lived"]),
            elapsed_hours=0.25,
            season=str(ctx["season"]),
        )
        _commit_state(root, state, now_str)
        partner_presence_out = _resolve_partner_presence(state, ctx)

    return {
        "ok": True,
        "from_spot": prev_spot,
        "target_spot_id": norm_target,
        "target_spot_name_cn": _spot_name_cn(str(ctx["theme"]), norm_target),
        "walk": walk_info,
        "shortcut_unlocked": shortcut_unlocked,
        "umbrella_reunion": reunion_record,
        "partner_presence": partner_presence_out,
        "llm_calls": 0,
    }


def invite_walk_together(
    day_root: Path | str,
    *,
    target_spot_id: str,
    bringing_umbrella: bool = False,
    carried_heavy_count: int = 0,
    companion_note: str = "",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Invite TA to walk together, strictly respecting rest/illness/refusal/solo-walk agency."""
    norm_target = _normalize_spot_id(target_spot_id, allow_away=False, default="")
    if norm_target not in VALID_SPOT_IDS:
        raise V05LivingTownError(
            400,
            "INVALID_SPOT_ID",
            f"target_spot_id must be one of {CORE_SPOT_IDS}, got {target_spot_id!r}",
        )
    if isinstance(carried_heavy_count, bool) or not isinstance(carried_heavy_count, int) or carried_heavy_count < 0:
        raise V05LivingTownError(400, "INVALID_FIELDS", "carried_heavy_count must be a non-negative integer")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)
    theme = str(ctx["theme"])

    def _decline_payload(cur_state: Mapping[str, Any], cur_ta: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        if not (
            cur_ta["sick"]
            or cur_ta["resting"]
            or cur_ta["refusal_lock_active"]
            or cur_ta["solo_walk_lock"]
        ):
            return None
        if cur_ta["sick"]:
            reason = "sick"
            reply_hint = "TA 这会儿身子不太舒服，正安安静静歇着，先不约出门走坡道，回头再慢慢逛。"
        elif cur_ta["resting"]:
            reason = "resting"
            reply_hint = "TA 正在屋里歇息养神，先不打扰 TA 的节奏，等睡醒缓过来再说。"
        elif cur_ta["refusal_lock_active"]:
            reason = "refusal_lock"
            reply_hint = "TA 眼下想先静一静、保留一点独处空间，把决定权留给 TA 就好。"
        else:
            reason = "solo_walk_lock"
            reply_hint = "TA 今天想一个人按自己的步速慢慢走一段，顺路碰见打个招呼就好。"

        # Zero-write decline: NEVER force TA or mutate presence on refusal.
        return {
            "ok": True,
            "accepted": False,
            "outcome": "TA_PREFERS_SOLO_OR_REST",
            "decline_reason": reason,
            "reply_hint": reply_hint,
            "ta_moved": False,
            "target_spot_id": norm_target,
            "ta_presence": dict(cur_ta),
            "partner_presence": _resolve_partner_presence(cur_state, ctx),
            "llm_calls": 0,
            "zero_write": True,
        }

    # Pre-lock zero-write check so a declined invite never creates a .lock file on disk.
    pre_state, _ = _load_state_zero_write(root, ctx)
    pre_decline = _decline_payload(pre_state, _resolve_ta_presence(pre_state, ctx))
    if pre_decline is not None:
        return pre_decline

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        ta_presence = _resolve_ta_presence(state, ctx)
        in_lock_decline = _decline_payload(state, ta_presence)
        if in_lock_decline is not None:
            return in_lock_decline

        prev_spot = str(ta_presence["location_id"])
        walk_info = _compute_walk_between(
            state,
            prev_spot,
            norm_target,
            carried_heavy_count=carried_heavy_count,
        )
        shortcut_unlocked = _record_edge_walk_and_shortcut(state, prev_spot, norm_target, now_str)

        clean_note = _strip_emojis_and_ctrl(str(companion_note or ""), 80) or (
            f"和 TA 顺路一起往{_spot_name_cn(theme, norm_target)}慢慢走去。"
        )
        pp = state["partner_presence"]
        pp["location_id"] = norm_target
        pp["status"] = "walking_together"
        pp["bringing_umbrella"] = bool(bringing_umbrella)
        pp["companion_note"] = clean_note
        pp["last_walk_seconds"] = int(walk_info["walk_seconds"])
        pp["last_body_tag"] = str(walk_info["body_tag"])
        pp["updated_at"] = now_str

        state["ta_state"]["location_override"] = norm_target
        state["ta_state"]["companion_note"] = clean_note
        state["ta_state"]["updated_at"] = now_str

        _advance_strata_in_place(
            state,
            weather=str(ctx["weather"]),
            days_lived=int(ctx["days_lived"]),
            elapsed_hours=0.25,
            season=str(ctx["season"]),
        )
        _commit_state(root, state, now_str)
        partner_presence_out = _resolve_partner_presence(state, ctx)
        ta_presence_out = _resolve_ta_presence(state, ctx)

    return {
        "ok": True,
        "accepted": True,
        "outcome": "WALKING_TOGETHER",
        "reply_hint": f"TA 点点头应了下来，两个人一起往{_spot_name_cn(theme, norm_target)}走去。",
        "ta_moved": True,
        "from_spot": prev_spot,
        "target_spot_id": norm_target,
        "target_spot_name_cn": _spot_name_cn(theme, norm_target),
        "walk": walk_info,
        "shortcut_unlocked": shortcut_unlocked,
        "partner_presence": partner_presence_out,
        "ta_presence": ta_presence_out,
        "llm_calls": 0,
        "zero_write": False,
    }


def set_subject_town_state(
    day_root: Path | str,
    *,
    ta_location_id: Optional[str] = None,
    solo_walk_lock: Optional[bool] = None,
    resting: Optional[bool] = None,
    sick: Optional[bool] = None,
    refusal_lock_active: Optional[bool] = None,
    returning_home_progress: Optional[float] = None,
    companion_note: Optional[str] = None,
    neighbor_encounter: Optional[Mapping[str, Any]] = None,
    third_space_trace: Optional[Mapping[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Subject-only state mutation: TA sets own location, solo-walk/rest boundaries & traces."""
    if all(
        v is None
        for v in (
            ta_location_id,
            solo_walk_lock,
            resting,
            sick,
            refusal_lock_active,
            returning_home_progress,
            companion_note,
            neighbor_encounter,
            third_space_trace,
        )
    ):
        raise V05LivingTownError(400, "INVALID_FIELDS", "At least one subject town state field is required")

    norm_loc: Optional[str] = None
    if ta_location_id is not None:
        norm_loc = _normalize_spot_id(ta_location_id, default="")
        if norm_loc not in VALID_SPOT_IDS:
            raise V05LivingTownError(400, "INVALID_SPOT_ID", f"Unknown ta_location_id: {ta_location_id!r}")

    for flag_name, flag_val in (
        ("solo_walk_lock", solo_walk_lock),
        ("resting", resting),
        ("sick", sick),
        ("refusal_lock_active", refusal_lock_active),
    ):
        if flag_val is not None and not isinstance(flag_val, bool):
            raise V05LivingTownError(400, "INVALID_FIELDS", f"Field {flag_name!r} must be a boolean")

    norm_progress: Optional[float] = None
    if returning_home_progress is not None:
        if isinstance(returning_home_progress, bool) or not isinstance(returning_home_progress, (int, float)):
            raise V05LivingTownError(400, "INVALID_FIELDS", "returning_home_progress must be a number in [0, 1]")
        val = float(returning_home_progress)
        if val < 0.0 or val > 1.0:
            raise V05LivingTownError(400, "INVALID_FIELDS", "returning_home_progress must be in [0.0, 1.0]")
        norm_progress = round(val, 2)

    enc_rid: Optional[str] = None
    enc_note = ""
    if neighbor_encounter is not None:
        if not isinstance(neighbor_encounter, Mapping):
            raise V05LivingTownError(400, "INVALID_FIELDS", "neighbor_encounter must be an object")
        enc_rid = str(neighbor_encounter.get("regular_id") or "").strip()
        if enc_rid not in REGULAR_BY_ID:
            raise V05LivingTownError(400, "UNKNOWN_REGULAR", f"Unknown regular_id: {enc_rid!r}")
        enc_note = _strip_emojis_and_ctrl(str(neighbor_encounter.get("note_cn") or "路过时点了点头，聊了两句天色。"), 80)

    t_spot: Optional[str] = None
    if third_space_trace is not None:
        if not isinstance(third_space_trace, Mapping):
            raise V05LivingTownError(400, "INVALID_FIELDS", "third_space_trace must be an object")
        t_spot = _normalize_spot_id(third_space_trace.get("spot_id"), default="")
        if t_spot not in THIRD_SPACE_SPOT_IDS:
            raise V05LivingTownError(400, "INVALID_THIRD_SPACE", f"spot_id must be one of {THIRD_SPACE_SPOT_IDS}")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)
    theme = str(ctx["theme"])
    hour = int(ctx["hour"])
    weather = str(ctx["weather"])
    ground_state = str(ctx.get("ground_state") or ("wet" if _is_rainy(weather) else "dry"))

    walk_info: Optional[dict[str, Any]] = None
    shortcut_unlocked: Optional[dict[str, Any]] = None
    encounter_out: Optional[dict[str, Any]] = None
    trace_out: Optional[dict[str, Any]] = None

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        ta_s = state["ta_state"]
        prev_loc = str(_resolve_ta_presence(state, ctx)["location_id"])

        if norm_loc is not None:
            walk_info = _compute_walk_between(state, prev_loc, norm_loc)
            shortcut_unlocked = _record_edge_walk_and_shortcut(state, prev_loc, norm_loc, now_str)
            ta_s["location_override"] = norm_loc

        for flag_name, flag_val in (
            ("solo_walk_lock", solo_walk_lock),
            ("resting", resting),
            ("sick", sick),
            ("refusal_lock_active", refusal_lock_active),
        ):
            if flag_val is not None:
                ta_s[flag_name] = flag_val

        if norm_progress is not None:
            ta_s["returning_home_progress"] = norm_progress

        if companion_note is not None:
            ta_s["companion_note"] = _strip_emojis_and_ctrl(str(companion_note), 80)

        # If TA enables solo lock / rest / sick / refusal, gently release partner's walking_together status.
        if ta_s["solo_walk_lock"] or ta_s["resting"] or ta_s["sick"] or ta_s["refusal_lock_active"]:
            if state["partner_presence"].get("status") == "walking_together":
                p_loc = str(state["partner_presence"].get("location_id") or "home")
                state["partner_presence"]["status"] = "home" if p_loc == "home" else "in_town"

        if enc_rid is not None:
            bp = REGULAR_BY_ID[enc_rid]
            reg = state["regulars"][enc_rid]
            cur_spot, is_out_now, _ = _regular_schedule_state(enc_rid, hour, weather)
            puddle_depth = float(state.get("ground_strata", {}).get("puddle_depth", 0.0))
            scene_cn = _compose_coherent_regular_scene(
                bp,
                hour=hour,
                weather=weather,
                is_out_now=is_out_now,
                spot_id=cur_spot,
                puddle_depth=puddle_depth,
                ground_state=ground_state,
                ring=state.setdefault("scene_ring_30d", []),
                seed_idx=_deterministic_seed(f"{now_str}|subject|{enc_rid}"),
            )
            reg["last_scene_cn"] = scene_cn
            reg["last_encounter_at"] = now_str
            notes = reg.setdefault("familiarity_notes", [])
            notes.append(f"{ctx['date_str']}：{enc_note}")
            if len(notes) > MAX_FAMILIARITY_NOTES:
                del notes[:-MAX_FAMILIARITY_NOTES]
            encounter_out = {
                "regular_id": enc_rid,
                "regular_name_cn": bp["name_cn"],
                "note_cn": enc_note,
                "living_scene_cn": scene_cn,
            }

        if third_space_trace is not None and t_spot is not None:
            t_note = _strip_emojis_and_ctrl(
                str(third_space_trace.get("note_cn") or f"在{_spot_name_cn(theme, t_spot)}静静坐了一会儿。"),
                80,
            )
            if t_spot == "blank_grove":
                ck = str(third_space_trace.get("carried_kind") or "empty").strip().lower()
                if ck not in ("book", "stationery", "food", "drink", "ingredient", "empty"):
                    ck = "empty"
                g_visits = state["ground_strata"].setdefault("grove_visits", [])
                g_visits.append({"carried_kind": ck, "actor": "subject", "visited_at": now_str})
                if len(g_visits) > MAX_GROVE_VISITS:
                    del g_visits[:-MAX_GROVE_VISITS]
                state["ground_strata"]["grove_stage"] = fabric.evolve_blank_grove_lot(int(ctx["days_lived"]), g_visits)
            trace_out = _append_third_space_trace(
                state,
                spot_id=t_spot,
                theme=theme,
                visitor_side="subject",
                note_cn=t_note,
                ambiance_cn=t_note,
                pocket_find=None,
                now_iso=now_str,
            )

        ta_s["updated_at"] = now_str
        _commit_state(root, state, now_str)
        ta_presence_out = _resolve_ta_presence(state, ctx)

    return {
        "ok": True,
        "ta_presence": ta_presence_out,
        "walk": walk_info,
        "shortcut_unlocked": shortcut_unlocked,
        "neighbor_encounter": encounter_out,
        "third_space_trace": trace_out,
        "llm_calls": 0,
    }


def advance_town_pulse(
    day_root: Path | str,
    *,
    elapsed_hours: float = 1.0,
    weather_override: Optional[str] = None,
    days_lived_override: Optional[int] = None,
    yesterday_gale: Optional[bool] = None,
    rainy_streak_days: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Subject / daily-loop write helper advancing strata, kitten snapshot & regular sighting."""
    if isinstance(elapsed_hours, bool) or not isinstance(elapsed_hours, (int, float)) or elapsed_hours <= 0:
        raise V05LivingTownError(400, "INVALID_FIELDS", "elapsed_hours must be a positive number")
    eff_hours = min(48.0, float(elapsed_hours))

    if days_lived_override is not None:
        if isinstance(days_lived_override, bool) or not isinstance(days_lived_override, int) or days_lived_override < 0:
            raise V05LivingTownError(400, "INVALID_FIELDS", "days_lived_override must be a non-negative integer")
    if yesterday_gale is not None and not isinstance(yesterday_gale, bool):
        raise V05LivingTownError(400, "INVALID_FIELDS", "yesterday_gale must be a boolean")
    if rainy_streak_days is not None:
        if isinstance(rainy_streak_days, bool) or not isinstance(rainy_streak_days, int) or rainy_streak_days < 0:
            raise V05LivingTownError(400, "INVALID_FIELDS", "rainy_streak_days must be a non-negative integer")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    weather = str(weather_override).strip().lower() if isinstance(weather_override, str) and weather_override.strip() else str(ctx["weather"])
    days_lived = int(days_lived_override) if days_lived_override is not None else int(ctx["days_lived"])
    ground_state = "wet" if _is_rainy(weather) else str(ctx.get("ground_state") or "dry")
    hour = int(ctx["hour"])

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)

        if yesterday_gale is not None:
            state["economy_and_favors"]["yesterday_gale"] = yesterday_gale
        if rainy_streak_days is not None:
            state["economy_and_favors"]["rainy_streak_days"] = rainy_streak_days

        strata_after = _advance_strata_in_place(
            state,
            weather=weather,
            days_lived=days_lived,
            elapsed_hours=eff_hours,
            season=str(ctx["season"]),
        )

        # Pick one currently-out regular deterministically to record a non-repeating sighting
        out_regulars = [
            bp for bp in REGULAR_BLUEPRINTS
            if _regular_schedule_state(bp["regular_id"], hour, weather)[1]
        ]
        sighting_out: Optional[dict[str, Any]] = None
        if out_regulars:
            pick_idx = _deterministic_seed(f"{now_str}|{weather}|{state.get('revision', 0)}") % len(out_regulars)
            chosen = out_regulars[pick_idx]
            rid = chosen["regular_id"]
            cur_spot, is_out_now, _ = _regular_schedule_state(rid, hour, weather)
            puddle_depth = float(strata_after.get("puddle_depth", 0.0))
            scene_cn = _compose_coherent_regular_scene(
                chosen,
                hour=hour,
                weather=weather,
                is_out_now=is_out_now,
                spot_id=cur_spot,
                puddle_depth=puddle_depth,
                ground_state=ground_state,
                ring=state.setdefault("scene_ring_30d", []),
                seed_idx=_deterministic_seed(f"{now_str}|pulse|{rid}|{state.get('revision', 0)}"),
            )
            state["regulars"][rid]["last_scene_cn"] = scene_cn
            state["regulars"][rid]["last_encounter_at"] = now_str
            sighting_out = {
                "regular_id": rid,
                "regular_name_cn": chosen["name_cn"],
                "spot_id": cur_spot,
                "living_scene_cn": scene_cn,
            }

        kitten_snapshot = _resolve_kitten_view(state, days_lived, str(ctx["theme"]))
        _commit_state(root, state, now_str)
        scene_ring_size_out = len(state.get("scene_ring_30d", []))

    sink_receipts: list[dict[str, Any]] = []
    pulse_ctx = dict(ctx)
    pulse_ctx["weather"] = weather
    pulse_lunar = fabric.compute_offline_lunar_phase(pulse_ctx["unix_seconds"], weather=weather)
    rare_spec = _compute_rare_spectacle(pulse_ctx, strata_after, pulse_lunar)
    if isinstance(rare_spec, Mapping) and rare_spec.get("spectacle_id"):
        spec_id = str(rare_spec["spectacle_id"])
        spec_enc = f"spectacle_{spec_id}_{ctx['date_str']}"
        sink_receipts.append(
            _emit_town_lived_episode(
                root,
                episode_key=f"v05_town:spectacle:{spec_enc}",
                topic=f"难得镇景：{rare_spec.get('title_cn') or spec_id}",
                exact_detail=str(rare_spec.get("description_cn") or rare_spec.get("title_cn") or spec_id),
                gist_summary=f"在镇上撞见了{rare_spec.get('title_cn') or spec_id}",
                occurred_at=now_str,
                emotional_salience=0.72,
                affect_kind="mood_shift",
                affect_intensity=56,
                shared=True,
                source_refs=(
                    ("encounter_id", spec_enc),
                    ("spectacle_id", spec_id),
                    ("spot_id", str(rare_spec.get("spot_id") or "home")),
                ),
            )
        )

    return {
        "ok": True,
        "elapsed_hours": eff_hours,
        "weather": weather,
        "ground_strata": strata_after,
        "alley_kitten": kitten_snapshot,
        "regular_sighting": sighting_out,
        "rare_spectacle": rare_spec,
        "scene_ring_size": scene_ring_size_out,
        "memory_sinks": sink_receipts,
        "llm_calls": 0,
    }


def leave_drifting_book(
    day_root: Path | str,
    *,
    book_title: str,
    slip_note_cn: str,
    spot_id: Optional[str] = "old_bookshop",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.101: Place a book with a quiet slip note into the town drifting bookbox."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_title = _validate_clean_text(book_title, field_name="book_title", min_len=1, max_len=60)
    clean_slip = _validate_clean_text(slip_note_cn, field_name="slip_note_cn", min_len=1, max_len=160)
    raw_spot = "old_bookshop" if spot_id is None or str(spot_id).strip() == "" else spot_id
    norm_spot = _normalize_spot_id(raw_spot, default="")
    if norm_spot not in VALID_SPOT_IDS:
        raise V05LivingTownError(400, "INVALID_SPOT_ID", f"Unknown spot_id: {spot_id!r}")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        rev_next = int(state.get("revision", 0)) + 1
        entry = {
            "book_id": f"book_{rev_next}_{secrets.token_hex(3)}",
            "book_title": clean_title,
            "spot_id": norm_spot,
            "status": "IN_BOX",
            "left_by_side": side,
            "slip_note_cn": clean_slip,
            "margin_notes": [],
            "created_at": now_str,
        }
        box = state.setdefault("drifting_bookbox", [])
        box.append(entry)
        if len(box) > MAX_DRIFTING_BOOKS:
            del box[:-MAX_DRIFTING_BOOKS]

        _commit_state(root, state, now_str)
        box_count_out = len(box)
    return {
        "ok": True,
        "book": copy.deepcopy(entry),
        "bookbox_count": box_count_out,
        "llm_calls": 0,
    }


def borrow_or_annotate_drifting_book(
    day_root: Path | str,
    *,
    book_id: str,
    op: str,
    margin_note_cn: Optional[str] = "",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.101: Borrow a drifting book or return it with a quiet reader margin note."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_book_id = _validate_clean_text(book_id, field_name="book_id", min_len=1, max_len=64)
    if not isinstance(op, str) or op.strip() not in ("borrow", "return_with_note"):
        raise V05LivingTownError(
            400,
            "INVALID_FIELDS",
            "Field 'op' must be 'borrow' or 'return_with_note'",
        )
    norm_op = op.strip()
    clean_margin = _validate_clean_text(margin_note_cn, field_name="margin_note_cn", min_len=0, max_len=160)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    if not _state_path(root).is_file():
        starter_ids = {row.get("book_id") for row in _starter_drifting_bookbox()}
        if clean_book_id not in starter_ids:
            raise V05LivingTownError(404, "BOOK_NOT_FOUND", f"Drifting book not found: {clean_book_id!r}")

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        target: Optional[dict[str, Any]] = None
        for row in state.get("drifting_bookbox", []):
            if isinstance(row, dict) and row.get("book_id") == clean_book_id:
                target = row
                break
        if target is None:
            raise V05LivingTownError(404, "BOOK_NOT_FOUND", f"Drifting book not found: {clean_book_id!r}")

        target["status"] = "BORROWED" if norm_op == "borrow" else "IN_BOX"
        note_entry: Optional[dict[str, Any]] = None
        if clean_margin:
            notes = target.setdefault("margin_notes", [])
            note_entry = {
                "note_id": f"mn_{int(state.get('revision', 0)) + 1}_{len(notes) + 1}",
                "author_side": side,
                "op": norm_op,
                "note_cn": clean_margin,
                "created_at": now_str,
            }
            notes.append(note_entry)
            if len(notes) > MAX_BOOK_MARGIN_NOTES:
                del notes[:-MAX_BOOK_MARGIN_NOTES]

        _commit_state(root, state, now_str)
        target_copy = copy.deepcopy(target)
    return {
        "ok": True,
        "book": target_copy,
        "op": norm_op,
        "margin_note": note_entry,
        "author_side": side,
        "llm_calls": 0,
    }


def post_bulletin_followup(
    day_root: Path | str,
    *,
    notice_id: Optional[str] = None,
    title_cn: Optional[str] = None,
    followup_cn: str = "",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.101: Append a warm continuity follow-up to the town bulletin board."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_followup = _validate_clean_text(followup_cn, field_name="followup_cn", min_len=1, max_len=180)
    clean_title = _validate_clean_text(title_cn, field_name="title_cn", min_len=0, max_len=60)
    clean_notice_id = _validate_clean_text(notice_id, field_name="notice_id", min_len=0, max_len=64)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    with _tx.state_file_lock(_state_path(root)):
        state, _ = _load_state_zero_write(root, ctx)
        notices = state.setdefault("bulletin_followups", [])
        rev_next = int(state.get("revision", 0)) + 1
        target: Optional[dict[str, Any]] = None

        if clean_notice_id:
            for row in notices:
                if isinstance(row, dict) and row.get("notice_id") == clean_notice_id:
                    target = row
                    break
            if target is None:
                target = {
                    "notice_id": clean_notice_id,
                    "title_cn": clean_title or "小镇布告栏新消息",
                    "created_at": now_str,
                    "followups": [],
                }
                notices.append(target)
        elif clean_title:
            for row in notices:
                if isinstance(row, dict) and row.get("title_cn") == clean_title:
                    target = row
                    break
            if target is None:
                target = {
                    "notice_id": f"notice_{rev_next}_{secrets.token_hex(3)}",
                    "title_cn": clean_title,
                    "created_at": now_str,
                    "followups": [],
                }
                notices.append(target)
        else:
            for row in notices:
                if isinstance(row, dict) and row.get("notice_id") == DEFAULT_BULLETIN_NOTICE_ID:
                    target = row
                    break
            if target is None:
                target = {
                    "notice_id": DEFAULT_BULLETIN_NOTICE_ID,
                    "title_cn": "巷口橘猫的小铜铃找回来啦",
                    "created_at": now_str,
                    "followups": [],
                }
                notices.append(target)

        fus = target.setdefault("followups", [])
        fu_entry = {
            "followup_id": f"fu_{rev_next}_{len(fus) + 1}",
            "author_side": side,
            "followup_cn": clean_followup,
            "created_at": now_str,
        }
        fus.append(fu_entry)
        if len(fus) > MAX_BULLETIN_FOLLOWUPS:
            del fus[:-MAX_BULLETIN_FOLLOWUPS]
        if len(notices) > MAX_BULLETIN_NOTICES:
            del notices[:-MAX_BULLETIN_NOTICES]

        _commit_state(root, state, now_str)
        target_copy = copy.deepcopy(target)
        notice_count_out = len(notices)
    notice_id_final = str(target_copy["notice_id"])
    followup_id_final = str(fu_entry["followup_id"])
    sink_receipt = _emit_town_lived_episode(
        root,
        episode_key=f"v05_town:notice:{notice_id_final}:{followup_id_final}",
        topic=f"小镇布告栏：{target_copy['title_cn']}",
        exact_detail=f"布告栏「{target_copy['title_cn']}」添了新后续：{clean_followup}",
        gist_summary=f"留意到小镇布告栏关于「{target_copy['title_cn']}」的后续消息",
        occurred_at=now_str,
        emotional_salience=0.46,
        affect_kind="affect_pulse",
        affect_intensity=36,
        shared=True,
        source_refs=(("notice_id", notice_id_final), ("encounter_id", followup_id_final)),
    )
    return {
        "ok": True,
        "notice": target_copy,
        "followup": fu_entry,
        "notice_count": notice_count_out,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


__all__ = [
    "ADJACENT_EDGE_SPECS",
    "CORE_SPOT_IDS",
    "DEFAULT_BULLETIN_NOTICE_ID",
    "FAVOR_KIND_LABELS",
    "MAX_BOOK_MARGIN_NOTES",
    "MAX_BULLETIN_FOLLOWUPS",
    "MAX_BULLETIN_NOTICES",
    "MAX_DRIFTING_BOOKS",
    "PARTNER_VALID_LOCATIONS",
    "REGULAR_BLUEPRINTS",
    "REGULAR_BY_ID",
    "SPOT_ELEVATION_SPECS",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "THEME_SPOT_NAMES",
    "THIRD_SPACE_SPOT_IDS",
    "VALID_SPOT_IDS",
    "VALID_THEMES",
    "V05LivingTownError",
    "advance_town_pulse",
    "borrow_or_annotate_drifting_book",
    "claim_surplus_bag",
    "greet_or_feed_kitten",
    "invite_walk_together",
    "leave_drifting_book",
    "log_neighbor_favor",
    "move_partner_to_spot",
    "post_bulletin_followup",
    "read_living_town_snapshot",
    "set_subject_town_state",
    "visit_third_space",
]
