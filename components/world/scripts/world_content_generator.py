#!/usr/bin/env python3
"""Scoped World Content Generator & Anti-Spoil Firewall (v0.9 Pillar B).

Generates store-scoped seasonal/epoch content batches either:
1. Deterministically offline (``llm_proposal_fn is None``, ``llm_calls == 0``)
   using SHA-256 combinators over ``(instance_seed, theme, store_id, season,
   trigger_epoch)``, OR
2. Via an injected scoped proposal callback (``llm_proposal_fn``) strictly
   gated by:
   - ``anti_spoil_guard(raw_context)`` BEFORE cooldown, budget, or callback,
   - per-store cooldown (``MIN_GENERATION_COOLDOWN_HOURS = 12``),
   - ``budget.reserve(REQUEST_KIND_CONTENT, est_tokens=..., now=...)`` BEFORE
     invoking ``llm_proposal_fn``,
   - canonical validation through ``world_content_catalog``.

Zero private desires, chat transcripts, diaries, or hidden stances may ever
enter the generator context.
"""
from __future__ import annotations

import copy
import hashlib
import json
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
except ImportError:  # pragma: no cover
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.world.scripts import world_content_catalog as _catalog
except ImportError:  # pragma: no cover
    import world_content_catalog as _catalog  # type: ignore[no-redef]

try:
    from components.life.scripts import life_request_budget as _budget_mod
except ImportError:  # pragma: no cover
    import life_request_budget as _budget_mod  # type: ignore[no-redef]


DEFAULT_TZ = timezone(timedelta(hours=8))
MIN_GENERATION_COOLDOWN_HOURS = 12
DEFAULT_EST_TOKENS = 1200

REQUEST_KIND_CONTENT: str = getattr(_budget_mod, "REQUEST_KIND_CONTENT", "content")
RequestBudgetError = getattr(_budget_mod, "RequestBudgetError", RuntimeError)


def _is_request_budget_error(exc: BaseException) -> bool:
    if isinstance(exc, RequestBudgetError):
        return True
    return type(exc).__name__ == "RequestBudgetError" and hasattr(exc, "reason_code")


class AntiSpoilViolationError(ValueError):
    """Raised when private desires, chat logs, diaries, or hidden stances leak into world generation."""

    def __init__(self, reason_code: str, offending_key: str = "") -> None:
        self.reason_code = str(reason_code or "ANTI_SPOIL_PRIVATE_KEY_FORBIDDEN")
        self.offending_key = str(offending_key or "")
        detail = (
            f"{self.reason_code}: forbidden private context key {self.offending_key!r}"
            if self.offending_key
            else self.reason_code
        )
        super().__init__(detail)


FORBIDDEN_PRIVATE_SPOIL_KEYS: frozenset[str] = frozenset(
    {
        "private_desires",
        "everyday_desires",
        "unexpressed_wishes",
        "retained_concerns",
        "turn_only_buffer",
        "chat_transcript",
        "recent_messages",
        "partner_chat",
        "diary",
        "diary_entries",
        "private_note",
        "private_drafts",
        "stance_containment",
        "v09_true_stance",
        "subject_last_seen_place",
        "solo_slot",
        "solo_ledger",
        "incoming_user_text",
        "ephemeral_turn_hint",
    }
)

PUBLIC_WORLD_CONTEXT_WHITELIST: frozenset[str] = frozenset(
    {
        "world_id",
        "theme",
        "seed",
        "season",
        "solar_term",
        "weather",
        "store_id",
        "store_name",
        "allowed_templates",
        "existing_item_names",
        "public_placed_orders_only",
    }
)

_PUBLIC_ORDER_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "order_id",
        "commitment_id",
        "venue_id",
        "store_id",
        "item_title_cn",
        "template_id",
        "status",
        "due_at",
    }
)


def _strip_emojis_and_ctrl(text: str) -> str:
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        out.append(ch)
    return " ".join("".join(out).split()).strip()


def _clean_str(raw: Any, *, max_len: int = 80, default: str = "") -> str:
    if not isinstance(raw, str):
        return default
    cleaned = _strip_emojis_and_ctrl(raw)
    if not cleaned:
        return default
    return cleaned[:max_len]


def _scan_forbidden_private_keys(obj: Any, *, _depth: int = 0) -> Optional[str]:
    if _depth > 20:
        raise AntiSpoilViolationError("ANTI_SPOIL_CONTEXT_TOO_DEEP")
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            key_str = str(key).strip()
            key_norm = key_str.lower()
            if key_norm in FORBIDDEN_PRIVATE_SPOIL_KEYS:
                return key_str
            nested = _scan_forbidden_private_keys(value, _depth=_depth + 1)
            if nested is not None:
                return nested
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for elem in obj:
            nested = _scan_forbidden_private_keys(elem, _depth=_depth + 1)
            if nested is not None:
                return nested
    return None


def anti_spoil_guard(raw_context: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively verify zero private desire/chat/diary keys exist and return a strict
    public-world-only whitelist dict (``world_id``, ``theme``, ``seed``, ``season``,
    ``solar_term``, ``weather``, ``store_id``, ``store_name``, ``allowed_templates``,
    ``existing_item_names``, ``public_placed_orders_only``).

    Raises ``AntiSpoilViolationError`` on any smuggled private key at any nesting depth.
    """

    if not isinstance(raw_context, Mapping):
        raise AntiSpoilViolationError("INVALID_WORLD_CONTEXT")

    offending = _scan_forbidden_private_keys(raw_context)
    if offending is not None:
        raise AntiSpoilViolationError(
            "ANTI_SPOIL_PRIVATE_KEY_FORBIDDEN", offending_key=offending
        )

    clean: dict[str, Any] = {}
    if "world_id" in raw_context:
        clean["world_id"] = _clean_str(raw_context.get("world_id"), max_len=64, default="shiomi_city")
    if "theme" in raw_context:
        clean["theme"] = _clean_str(raw_context.get("theme"), max_len=32, default="coastal")
    if "seed" in raw_context:
        raw_seed = raw_context.get("seed")
        if isinstance(raw_seed, int) and not isinstance(raw_seed, bool):
            clean["seed"] = str(raw_seed)
        else:
            clean["seed"] = _clean_str(raw_seed, max_len=64, default="default")
    if "season" in raw_context:
        clean["season"] = _clean_str(raw_context.get("season"), max_len=24, default="spring").lower()
    if "solar_term" in raw_context:
        clean["solar_term"] = _clean_str(raw_context.get("solar_term"), max_len=32, default="")
    if "weather" in raw_context:
        clean["weather"] = _clean_str(raw_context.get("weather"), max_len=32, default="clear").lower()
    if "store_id" in raw_context:
        clean["store_id"] = _clean_str(raw_context.get("store_id"), max_len=64, default="")
    if "store_name" in raw_context:
        clean["store_name"] = _clean_str(raw_context.get("store_name"), max_len=64, default="")
    if "allowed_templates" in raw_context:
        raw_tpls = raw_context.get("allowed_templates")
        if isinstance(raw_tpls, Sequence) and not isinstance(raw_tpls, (str, bytes)):
            clean["allowed_templates"] = [
                str(t).strip()
                for t in raw_tpls
                if isinstance(t, str) and str(t).strip() in _catalog.ITEM_CAPABILITY_TEMPLATES
            ]
        else:
            clean["allowed_templates"] = []
    if "existing_item_names" in raw_context:
        raw_names = raw_context.get("existing_item_names")
        if isinstance(raw_names, Sequence) and not isinstance(raw_names, (str, bytes)):
            clean["existing_item_names"] = [
                _clean_str(n, max_len=48)
                for n in list(raw_names)[:64]
                if isinstance(n, str) and _clean_str(n, max_len=48)
            ]
        else:
            clean["existing_item_names"] = []
    if "public_placed_orders_only" in raw_context:
        raw_orders = raw_context.get("public_placed_orders_only")
        sanitized_orders: list[dict[str, Any]] = []
        if isinstance(raw_orders, Sequence) and not isinstance(raw_orders, (str, bytes)):
            for row in list(raw_orders)[:24]:
                if not isinstance(row, Mapping):
                    continue
                sanitized_row: dict[str, Any] = {}
                for ok_key in _PUBLIC_ORDER_ALLOWED_KEYS:
                    if ok_key in row and row[ok_key] is not None:
                        val = row[ok_key]
                        if isinstance(val, str):
                            sanitized_row[ok_key] = _clean_str(val, max_len=80)
                        elif isinstance(val, int) and not isinstance(val, bool):
                            sanitized_row[ok_key] = int(val)
                if sanitized_row:
                    sanitized_orders.append(sanitized_row)
        clean["public_placed_orders_only"] = sanitized_orders

    return clean


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _default_templates_for_store(store_id: str, store_name: str = "") -> list[str]:
    hint = f"{store_id} {store_name}".lower()
    if any(k in hint for k in ("book", "书")):
        return ["reading_booklet", "desk_vessel", "decor_fixture"]
    if any(k in hint for k in ("cafe", "tea", "coffee", "bakery", "茶", "咖", "面包", "甜")):
        return ["drink_warm", "snack_portable", "meal_light", "desk_vessel"]
    if any(k in hint for k in ("home_goods", "furnish", "craft", "家居", "杂货", "器皿", "手作")):
        return ["decor_fixture", "desk_vessel", "snack_portable"]
    return ["drink_warm", "snack_portable", "meal_light", "desk_vessel", "reading_booklet"]


def _build_public_world_context_zero_write(
    day_root: Path | str,
    *,
    store_id: str,
    now_dt: datetime,
) -> dict[str, Any]:
    paths = _instance_paths.resolve_instance_paths(day_root)
    canonical_root = paths.day_root
    world_doc = (
        _read_json_zero_write(paths.world_state_json)
        or _read_json_zero_write(canonical_root / "world" / "world_state.json")
        or _read_json_zero_write(canonical_root / "data" / "world" / "world_state.json")
        or {}
    )
    inst_doc = (
        _read_json_zero_write(paths.instance_json) if paths.instance_json is not None else None
    ) or _read_json_zero_write(canonical_root / "run" / "nyairo-day.instance.json") or {}
    nb_doc = _read_json_zero_write(canonical_root / "world" / "v04_neighborhood_state.json") or {}

    world_id = str(world_doc.get("world_id") or inst_doc.get("world_id") or "shiomi_city")
    seed = str(
        inst_doc.get("world_seed")
        or inst_doc.get("seed")
        or world_doc.get("world_seed")
        or world_id
    )
    theme = str(inst_doc.get("theme") or world_doc.get("theme") or "coastal")
    env_nb = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
    env_w = world_doc.get("environment") if isinstance(world_doc.get("environment"), Mapping) else {}

    month = now_dt.month
    inferred_season = (
        "spring"
        if month in (3, 4, 5)
        else ("summer" if month in (6, 7, 8) else ("autumn" if month in (9, 10, 11) else "winter"))
    )
    season = str(env_nb.get("season") or env_w.get("season") or inferred_season).strip().lower()
    solar_term = str(env_nb.get("solar_term") or env_w.get("solar_term") or "").strip()
    weather = str(env_nb.get("weather") or env_w.get("weather") or "clear").strip().lower()

    store_name = store_id
    if paths.world_package_dir is not None:
        topo = _read_json_zero_write(paths.world_package_dir / "topology.json") or {}
        for loc in topo.get("locations") or []:
            if isinstance(loc, Mapping) and str(loc.get("location_id") or "") == store_id:
                store_name = str(loc.get("name") or store_id)
                break

    existing_snap = _catalog.read_dynamic_catalog_zero_write(
        canonical_root, store_id=store_id, include_out_of_stock=True
    )
    existing_names = [
        str(row.get("name_cn") or row.get("name") or "")
        for row in existing_snap.get("items") or []
        if isinstance(row, Mapping) and (row.get("name_cn") or row.get("name"))
    ]

    return {
        "world_id": world_id,
        "theme": theme,
        "seed": seed,
        "season": season,
        "solar_term": solar_term,
        "weather": weather,
        "store_id": store_id,
        "store_name": store_name,
        "allowed_templates": _default_templates_for_store(store_id, store_name),
        "existing_item_names": existing_names,
        "public_placed_orders_only": [],
    }


def _sha_idx(material: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


_SEASON_DRINK_POOL: dict[str, tuple[tuple[str, str, str, str, str], ...]] = {
    "spring": (
        ("雨前明前绿茶", "budget", "清浅豆香", "回甘利落微涩", "当季头茬细嫩茶叶现冲的热茶"),
        ("焙火春乌龙", "standard", "浅兰花焙香", "茶汤微醇不甜腻", "轻焙火春茶，温热饮用"),
        ("柚花白茶热饮", "standard", "淡柚花香", "入口柔和带微苦回甘", "干柚花与寿眉拼配的热泡茶"),
    ),
    "summer": (
        ("炭焙麦茶热杯", "budget", "炒麦焦香", "微苦爽口无糖", "粗麦现煮的热麦茶，解腻清爽"),
        ("青梅陈皮热饮", "standard", "微酸陈皮香", "酸涩中带淡淡回甜", "青梅干配老陈皮慢泡的温饮"),
        ("姜丝玄米茶", "standard", "炒米焦香", "米香明显略带辛感", "玄米配少量嫩姜丝冲泡的热茶"),
    ),
    "autumn": (
        ("慢焙桂花乌龙", "standard", "干桂花与炭焙香", "汤色琥珀微苦回甘", "秋日现窨干桂花配重焙乌龙"),
        ("陈皮熟普热杯", "standard", "木质陈香", "口感厚实略带药香", "小火焖泡的陈皮熟普热饮"),
        ("红枣焙茶", "budget", "微甜枣香与焙火香", "余味温和不齁甜", "无额外加糖的烘青焙茶配干枣片"),
    ),
    "winter": (
        ("炭焙黑豆茶", "budget", "烘烤黑豆香", "汤感温厚带微焦味", "现炒黑豆冲泡的冬日无咖啡因热饮"),
        ("肉桂烤苹果热饮", "standard", "肉桂与焦糖果香", "微酸温热偏浓郁", "烤苹果片配整根肉桂小火煮制"),
        ("重焙茎茶", "standard", "焦糖木质焙香", "微苦后段清甜", "低温慢焙的冬令茎茶热杯"),
    ),
}

_SEASON_SNACK_POOL: dict[str, tuple[tuple[str, str, str, dict[str, Any]], ...]] = {
    "spring": (
        (
            "海盐黄油小饼干铁盒",
            "standard",
            "酥松微咸，黄油与粗盐颗粒分明",
            {
                "container_kind": "cookie_tin",
                "label_cn": "浅青色铁皮饼干盒",
                "capacity_slots": 4,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
        (
            "艾草米香酥片盒",
            "budget",
            "草本微苦伴米香，口感偏干脆",
            {
                "container_kind": "cookie_tin",
                "label_cn": "小号方形点心铁盒",
                "capacity_slots": 3,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
    ),
    "summer": (
        (
            "柠檬皮脆饼铁罐",
            "standard",
            "微酸带柑橘皮苦香，质地偏脆",
            {
                "container_kind": "cookie_tin",
                "label_cn": "圆筒点心铁罐",
                "capacity_slots": 4,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
        (
            "白芝麻薄脆饼盒",
            "budget",
            "烘烤芝麻香气明显，微咸低糖",
            {
                "container_kind": "cookie_tin",
                "label_cn": "扁平点心铁盒",
                "capacity_slots": 4,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
    ),
    "autumn": (
        (
            "栗子荞麦曲奇铁盒",
            "standard",
            "粗粮微糙带烤栗仁香，甜度克制",
            {
                "container_kind": "cookie_tin",
                "label_cn": "深棕铁皮饼干盒",
                "capacity_slots": 5,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
        (
            "桂花核桃酥盒",
            "standard",
            "酥皮微油伴核桃焙香，甜味偏淡",
            {
                "container_kind": "cookie_tin",
                "label_cn": "八角铁皮点心盒",
                "capacity_slots": 4,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
    ),
    "winter": (
        (
            "姜味燕麦饼干铁盒",
            "standard",
            "辛辣姜粉与烤燕麦交织，越嚼越硬实",
            {
                "container_kind": "cookie_tin",
                "label_cn": "厚壁铁皮饼干盒",
                "capacity_slots": 5,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
        (
            "黑可可海盐脆片盒",
            "budget",
            "微苦高可可味，尾段带海盐咸感",
            {
                "container_kind": "cookie_tin",
                "label_cn": "哑光小铁盒",
                "capacity_slots": 3,
                "drinkware": False,
                "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                "material_cn": "马口铁",
            },
        ),
    ),
}

_MEAL_POOL: tuple[tuple[str, str, str, str], ...] = (
    ("烤时蔬厚蛋三明治", "budget", "微焦吐司夹软嫩厚蛋与烤菜，盐味适中", "现烤简餐三明治"),
    ("菌菇杂粮温热饭团", "budget", "微咸菌菇酱拌入粗粮米饭，紧实耐饱", "街角热柜手作饭团"),
    ("味噌烤豆腐便当", "standard", "豆香浓重伴微咸赤味噌，配两道清炒小菜", "午间限量轻食便当"),
    ("土豆培根热浓汤杯", "standard", "质地偏稠，胡椒味明显，温热顶饱", "配烤面包边的浓汤简餐"),
)

_VESSEL_POOL: tuple[tuple[str, str, int, bool, str, str], ...] = (
    ("粗陶宽口马克杯", "standard", 3, True, "粗陶", "釉面略带手工颗粒的日常宽口杯"),
    ("素白玻璃冷水杯", "budget", 2, True, "硼硅玻璃", "壁厚均匀的透明案头水杯"),
    ("原木分格笔筒", "budget", 5, False, "榉木", "适合收纳文具与纸条的案头木筒"),
    ("青灰釉小茶杯", "standard", 2, True, "瓷土", "杯壁略厚的青灰釉面品茶杯"),
)

_FIXTURE_POOL: tuple[tuple[str, str, tuple[str, ...], Optional[str], str, str], ...] = (
    ("磨砂玻璃小台灯", "budget", (), "dim", "磨砂玻璃与木座", "光线柔和偏暗的桌角小台灯"),
    ("折纸罩暖光工作灯", "standard", (), "moderate", "厚纸罩与铁艺", "适合桌面阅读的中等亮度台灯"),
    ("素木窗边矮几", "standard", ("desk_surface",), None, "原木", "靠窗可放杯碟与小册子的素木矮几"),
    ("黄铜罩立式阅读灯", "hearty", (), "bright", "黄铜与亚麻", "照明清晰的立式阅读灯具"),
)

_BOOKLET_POOL: tuple[tuple[str, str, int, list[str], str, str], ...] = (
    (
        "潮汐与码头气象札记",
        "budget",
        16,
        ["气象", "海岸", "地方记录"],
        "记录风向与潮位变化时，连续对照三日气压比单日经验更可靠。",
        "十月中旬西北风起前，水面常先出现细碎短浪，再伴随气压缓慢下沉。",
    ),
    (
        "器物修补手记小册",
        "standard",
        24,
        ["手作", "修补", "日常物"],
        "保留修补痕迹并非缺陷，而是器物继续参与日常使用的证明。",
        "裂口沿釉面延伸未透底时，清理缝隙后以生漆薄涂，干透仍可盛放干物。",
    ),
    (
        "街区旧木窗测绘册",
        "standard",
        20,
        ["建筑", "街巷", "观察"],
        "老街区采光差异多来自檐口伸出比例与窗棂开合方式，而非朝向本身。",
        "同样朝南的二层木窗，若外檐伸出超过六十厘米，正午直射光会明显退缩。",
    ),
    (
        "烘焙火候对照简册",
        "budget",
        12,
        ["茶饮", "火候", "饮食"],
        "焙火程度决定香气走向，轻焙留花香，重焙出木质焦韵与微苦。",
        "相同茶青在低温延长二十分钟复焙后，青味退去，汤感更沉但鲜爽度下降。",
    ),
)


def _deterministic_propose_batch(
    clean_ctx: Mapping[str, Any],
    *,
    store_id: str,
    trigger_reason: str,
    now_dt: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seed = str(clean_ctx.get("seed") or clean_ctx.get("world_id") or "default")
    theme = str(clean_ctx.get("theme") or "coastal")
    season = str(clean_ctx.get("season") or "spring").lower()
    if season not in _SEASON_DRINK_POOL:
        season = "spring"
    trigger_epoch = f"{now_dt.year}-{now_dt.month:02d}-{(now_dt.day - 1) // 3}:{trigger_reason}"
    base_key = f"{seed}|{theme}|{store_id}|{season}|{trigger_epoch}"

    allowed = list(clean_ctx.get("allowed_templates") or _default_templates_for_store(store_id))
    allowed = [t for t in allowed if t in _catalog.ITEM_CAPABILITY_TEMPLATES]
    if not allowed:
        allowed = ["drink_warm", "snack_portable"]

    existing_names = {str(n) for n in (clean_ctx.get("existing_item_names") or ())}
    item_count = 1 + _sha_idx(base_key + ":count", min(3, len(allowed)))  # 1..3 items
    chosen_templates: list[str] = []
    start_offset = _sha_idx(base_key + ":tpl_start", len(allowed))
    for i in range(item_count):
        tpl = allowed[(start_offset + i) % len(allowed)]
        if tpl not in chosen_templates:
            chosen_templates.append(tpl)

    items: list[dict[str, Any]] = []
    for idx, tpl in enumerate(chosen_templates):
        salt = f"{base_key}:item:{idx}:{tpl}"
        stock_val = 2 + _sha_idx(salt + ":stock", 4)  # 2..5 finite stock
        if tpl == "drink_warm":
            pool = _SEASON_DRINK_POOL[season]
            pick = pool[_sha_idx(salt, len(pool))]
            name_cn, tier, aroma, taste, desc = pick
            if name_cn in existing_names:
                pick = pool[(_sha_idx(salt, len(pool)) + 1) % len(pool)]
                name_cn, tier, aroma, taste, desc = pick
            items.append(
                {
                    "template_id": "drink_warm",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": desc,
                    "price_tier": tier,
                    "stock_initial": stock_val,
                    "portions": 1,
                    "shelf_life_hours": 12,
                    "aroma_cn": aroma,
                    "taste_note_cn": taste,
                    "season": season,
                }
            )
        elif tpl == "snack_portable":
            pool_s = _SEASON_SNACK_POOL[season]
            pick_s = pool_s[_sha_idx(salt, len(pool_s))]
            name_cn, tier, taste, container_spec = pick_s
            if name_cn in existing_names:
                pick_s = pool_s[(_sha_idx(salt, len(pool_s)) + 1) % len(pool_s)]
                name_cn, tier, taste, container_spec = pick_s
            items.append(
                {
                    "template_id": "snack_portable",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": f"便携分装的{name_cn}，空盒可留作案头收纳",
                    "price_tier": tier,
                    "stock_initial": stock_val,
                    "portions": 3,
                    "shelf_life_hours": 72,
                    "taste_note_cn": taste,
                    "reusable_container_spec": copy.deepcopy(container_spec),
                    "season": season,
                }
            )
        elif tpl == "meal_light":
            pick_m = _MEAL_POOL[_sha_idx(salt, len(_MEAL_POOL))]
            name_cn, tier, taste, desc = pick_m
            items.append(
                {
                    "template_id": "meal_light",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": desc,
                    "price_tier": tier,
                    "stock_initial": stock_val,
                    "portions": 1,
                    "shelf_life_hours": 24,
                    "taste_note_cn": taste,
                    "season": season,
                }
            )
        elif tpl == "desk_vessel":
            pick_v = _VESSEL_POOL[_sha_idx(salt, len(_VESSEL_POOL))]
            name_cn, tier, slots, is_drinkware, mat, desc = pick_v
            items.append(
                {
                    "template_id": "desk_vessel",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": desc,
                    "price_tier": tier,
                    "stock_initial": stock_val,
                    "capacity_slots": slots,
                    "drinkware": is_drinkware,
                    "material_cn": mat,
                    "season": season,
                }
            )
        elif tpl == "decor_fixture":
            pick_f = _FIXTURE_POOL[_sha_idx(salt, len(_FIXTURE_POOL))]
            name_cn, tier, surfs, band, mat, desc = pick_f
            items.append(
                {
                    "template_id": "decor_fixture",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": desc,
                    "price_tier": tier,
                    "stock_initial": min(stock_val, 3),
                    "surfaces": list(surfs),
                    "light_band": band,
                    "material_cn": mat,
                    "season": season,
                }
            )
        elif tpl == "reading_booklet":
            pick_b = _BOOKLET_POOL[_sha_idx(salt, len(_BOOKLET_POOL))]
            name_cn, tier, pages, tags, thesis, excerpt = pick_b
            items.append(
                {
                    "template_id": "reading_booklet",
                    "store_id": store_id,
                    "name_cn": name_cn,
                    "description_cn": f"共{pages}页的小开本主题册子",
                    "price_tier": tier,
                    "stock_initial": stock_val,
                    "pages_count": pages,
                    "topic_tags": list(tags),
                    "argument_thesis_cn": thesis,
                    "sample_excerpt_cn": excerpt,
                    "season": season,
                }
            )

    events: list[dict[str, Any]] = []
    # Emit 0-1 deterministic seasonal venue event
    if _sha_idx(base_key + ":emit_event", 2) == 1 and items:
        start_dt = now_dt + timedelta(hours=2)
        end_dt = start_dt + timedelta(hours=4)
        first_item = items[0]
        ok_first, norm_first, _err = _catalog.validate_candidate_item(first_item)
        first_cid = norm_first["catalog_item_id"] if (ok_first and norm_first) else ""
        if "reading_booklet" in chosen_templates:
            events.append(
                {
                    "template_id": "book_swap",
                    "venue_id": store_id,
                    "title_cn": "当季旧书交换台",
                    "swap_titles": [first_item["name_cn"], "街区风物笔记"],
                    "start_at": start_dt.isoformat(timespec="seconds"),
                    "end_at": end_dt.isoformat(timespec="seconds"),
                    "season": season,
                }
            )
        elif first_item.get("template_id") == "drink_warm" and first_cid:
            events.append(
                {
                    "template_id": "tea_tasting",
                    "venue_id": store_id,
                    "title_cn": "新茶小杯试饮",
                    "featured_catalog_item_id": first_cid,
                    "sample_stock": 6,
                    "start_at": start_dt.isoformat(timespec="seconds"),
                    "end_at": end_dt.isoformat(timespec="seconds"),
                    "season": season,
                }
            )
        elif first_cid:
            events.append(
                {
                    "template_id": "porch_market",
                    "venue_id": store_id,
                    "title_cn": "檐下小摊上新",
                    "featured_item_ids": [first_cid],
                    "start_at": start_dt.isoformat(timespec="seconds"),
                    "end_at": end_dt.isoformat(timespec="seconds"),
                    "season": season,
                }
            )

    return items, events


def _parse_proposal_output(raw_output: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw_output, Mapping):
        return dict(raw_output)
    if isinstance(raw_output, str):
        text = raw_output.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def _compute_deterministic_batch_fingerprint_zero_write(
    *,
    clean_ctx: Mapping[str, Any],
    existing_op: Mapping[str, Any],
    explicit_existing_names: bool,
    store_id: str,
    trigger_reason: str,
    source_kind: str,
    now_dt: datetime,
    now_was_explicit: bool,
) -> Optional[str]:
    """Recompute register_catalog_batch payload_hash zero-write to verify idempotent replay vs conflict."""

    pre_op_ctx = copy.deepcopy(dict(clean_ctx))
    if not explicit_existing_names:
        created_names = {
            str(row.get("name_cn") or "")
            for row in (existing_op.get("registered_items") or ())
            if isinstance(row, Mapping) and row.get("name_cn")
        }
        if created_names:
            pre_op_ctx["existing_item_names"] = [
                n for n in (pre_op_ctx.get("existing_item_names") or ()) if str(n) not in created_names
            ]

    eff_dt = now_dt
    if not now_was_explicit:
        prev_dt = _catalog._parse_iso(existing_op.get("updated_at"))
        if prev_dt is not None:
            prev_epoch = f"{prev_dt.year}-{prev_dt.month:02d}-{(prev_dt.day - 1) // 3}:{trigger_reason}"
            cur_epoch = f"{now_dt.year}-{now_dt.month:02d}-{(now_dt.day - 1) // 3}:{trigger_reason}"
            if prev_epoch == cur_epoch:
                eff_dt = prev_dt

    cand_items, cand_events = _deterministic_propose_batch(
        pre_op_ctx,
        store_id=store_id,
        trigger_reason=trigger_reason,
        now_dt=eff_dt,
    )
    valid_items: list[dict[str, Any]] = []
    for raw_item in cand_items:
        ok_it, norm_it, _err = _catalog.validate_candidate_item(raw_item, allowed_store_ids=(store_id,))
        if ok_it and norm_it is not None:
            valid_items.append(norm_it)
    valid_events: list[dict[str, Any]] = []
    for raw_ev in cand_events:
        ok_ev, norm_ev, _err = _catalog.validate_candidate_event(raw_ev, allowed_venue_ids=(store_id,))
        if ok_ev and norm_ev is not None:
            valid_events.append(norm_ev)
    if not valid_items and not valid_events:
        return None

    clean_source = _catalog._clean_text(source_kind, max_len=48) or "deterministic_seed"
    clean_trigger = _catalog._clean_text(trigger_reason, max_len=64) or "SCHEDULED_RESTOCK"
    return _catalog._tx.payload_fingerprint(
        {
            "op": "register_catalog_batch",
            "items": [
                {
                    "catalog_item_id": row["catalog_item_id"],
                    "template_id": row["template_id"],
                    "store_id": row["store_id"],
                    "price": row["price"],
                    "stock_initial": row["stock_initial"],
                }
                for row in valid_items
            ],
            "events": [
                {
                    "event_id": ev["event_id"],
                    "template_id": ev["template_id"],
                    "venue_id": ev["venue_id"],
                }
                for ev in valid_events
            ],
            "source_kind": clean_source,
            "trigger_reason": clean_trigger,
        }
    )


def generate_world_content_batch(
    day_root: Path | str,
    *,
    store_id: str,
    trigger_reason: str = "SEASON_SHIFT",
    world_context: Optional[Mapping[str, Any]] = None,
    llm_proposal_fn: Optional[Callable[[dict[str, Any]], Any]] = None,
    budget: Optional[Any] = None,
    est_tokens: int = DEFAULT_EST_TOKENS,
    force_cooldown_bypass: bool = False,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Generate and register a store-scoped world content batch (offline or budgeted LLM)."""

    now_dt, now_str = _catalog._format_now(now_iso)
    clean_store_id = str(store_id or "").strip()
    if not clean_store_id or not _catalog._SAFE_ID_RE.fullmatch(clean_store_id):
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "INVALID_STORE_ID",
            "store_id": clean_store_id,
            "llm_calls": 0,
            "registered_items": [],
            "registered_events": [],
            "rejected_candidates": [],
        }

    # 1. Anti-spoil guard MUST run BEFORE cooldown, budget reservation, or callback!
    if world_context is not None:
        clean_ctx = anti_spoil_guard(world_context)
    else:
        clean_ctx = {}

    public_defaults = _build_public_world_context_zero_write(
        day_root, store_id=clean_store_id, now_dt=now_dt
    )
    merged_ctx = dict(public_defaults)
    merged_ctx.update(clean_ctx)
    merged_ctx["store_id"] = clean_store_id
    if not merged_ctx.get("allowed_templates"):
        merged_ctx["allowed_templates"] = _default_templates_for_store(
            clean_store_id, str(merged_ctx.get("store_name") or "")
        )
    clean_ctx = anti_spoil_guard(merged_ctx)

    # 2. Zero-write idempotency pre-check so duplicate retries never burn budget,
    #    while reusing an operation_key with a different payload returns 409 OPERATION_KEY_CONFLICT.
    clean_op_key = str(operation_key).strip() if operation_key else ""
    if clean_op_key:
        existing_op = _catalog._check_committed_operation_zero_write(day_root, clean_op_key)
        if existing_op is not None:
            expected_source = (
                "deterministic_generation" if llm_proposal_fn is None else "llm_scoped_generation"
            )
            expected_trigger = _catalog._clean_text(trigger_reason, max_len=64) or "SCHEDULED_RESTOCK"
            recorded_source = str(existing_op.get("source_kind") or "")
            recorded_trigger = str(existing_op.get("trigger_reason") or "")
            recorded_stores: set[str] = set()
            for row in existing_op.get("registered_items") or ():
                if isinstance(row, Mapping) and row.get("store_id"):
                    recorded_stores.add(str(row["store_id"]))
            for ev in existing_op.get("registered_events") or ():
                if isinstance(ev, Mapping) and ev.get("venue_id"):
                    recorded_stores.add(str(ev["venue_id"]))
            if existing_op.get("store_id"):
                recorded_stores.add(str(existing_op["store_id"]))

            conflict = False
            if recorded_source and recorded_source != expected_source:
                conflict = True
            elif recorded_trigger and recorded_trigger != expected_trigger:
                conflict = True
            elif recorded_stores and clean_store_id not in recorded_stores:
                conflict = True
            elif llm_proposal_fn is None:
                stored_hash = str(existing_op.get("payload_hash") or "")
                if stored_hash:
                    cand_fp = _compute_deterministic_batch_fingerprint_zero_write(
                        clean_ctx=clean_ctx,
                        existing_op=existing_op,
                        explicit_existing_names=bool(
                            isinstance(world_context, Mapping) and "existing_item_names" in world_context
                        ),
                        store_id=clean_store_id,
                        trigger_reason=trigger_reason,
                        source_kind=expected_source,
                        now_dt=now_dt,
                        now_was_explicit=now_iso is not None,
                    )
                    if cand_fp and cand_fp != stored_hash:
                        conflict = True
            else:
                if isinstance(world_context, Mapping) and world_context.get("season"):
                    req_season = str(world_context.get("season") or "").strip().lower()
                    recorded_seasons = {
                        str(row.get("season") or "").strip().lower()
                        for row in (existing_op.get("registered_items") or ())
                        if isinstance(row, Mapping) and row.get("season")
                    }
                    if req_season and recorded_seasons and req_season not in recorded_seasons:
                        conflict = True

            if conflict:
                return {
                    "ok": False,
                    "status": "rejected",
                    "status_code": 409,
                    "reason_code": "OPERATION_KEY_CONFLICT",
                    "detail": f"operation_key {clean_op_key!r} already committed with a different payload",
                    "operation_key": clean_op_key,
                    "store_id": clean_store_id,
                    "llm_calls": 0,
                    "registered_items": [],
                    "registered_events": [],
                    "rejected_candidates": [],
                }

            replay = copy.deepcopy(existing_op)
            replay["ok"] = True
            replay["status"] = "already_applied"
            replay["idempotent_replay"] = True
            replay["llm_calls"] = 0
            return replay

    # 3. Per-store cooldown check (zero-write; backward clock elapsed_s < 0 also stays blocked!)
    snap = _catalog.read_dynamic_catalog_zero_write(day_root, store_id=clean_store_id)
    if snap.get("barrier_fail_closed") and snap.get("reason_code") == "UNSAFE_SYMLINK":
        raise _catalog.WorldCatalogError("UNSAFE_SYMLINK", "catalog path contains symlink")

    cooldowns = snap.get("store_cooldowns") or {}
    if not force_cooldown_bypass and clean_store_id in cooldowns:
        last_dt = _catalog._parse_iso(cooldowns[clean_store_id])
        if last_dt is not None:
            elapsed_s = (now_dt - last_dt).total_seconds()
            if elapsed_s < (MIN_GENERATION_COOLDOWN_HOURS * 3600):
                return {
                    "ok": False,
                    "status": "cooldown_active",
                    "reason_code": "GENERATION_COOLDOWN_ACTIVE",
                    "store_id": clean_store_id,
                    "last_generated_at": cooldowns[clean_store_id],
                    "cooldown_hours": MIN_GENERATION_COOLDOWN_HOURS,
                    "llm_calls": 0,
                    "registered_items": [],
                    "registered_events": [],
                    "rejected_candidates": [],
                }

    # 4A. Deterministic offline mode (llm_calls == 0)
    if llm_proposal_fn is None:
        proposed_items, proposed_events = _deterministic_propose_batch(
            clean_ctx,
            store_id=clean_store_id,
            trigger_reason=trigger_reason,
            now_dt=now_dt,
        )
        reg = _catalog.register_catalog_batch(
            day_root,
            items=proposed_items,
            events=proposed_events,
            source_kind="deterministic_generation",
            trigger_reason=trigger_reason,
            operation_key=operation_key,
            now_iso=now_str,
            allowed_store_ids=(clean_store_id,),
            allowed_venue_ids=(clean_store_id,),
        )
        reg["mode"] = "deterministic"
        reg["store_id"] = clean_store_id
        reg["llm_calls"] = 0
        reg["clean_context"] = clean_ctx
        return reg

    # 4B. Scoped budgeted proposal mode
    reservation: Optional[dict[str, Any]] = None
    if budget is not None:
        try:
            reservation = budget.reserve(
                REQUEST_KIND_CONTENT,
                est_tokens=int(est_tokens),
                now=now_dt,
            )
        except Exception as exc:
            if not _is_request_budget_error(exc):
                raise
            return {
                "ok": False,
                "status": "budget_refused",
                "reason_code": str(getattr(exc, "reason_code", "") or "REQUEST_BUDGET_REFUSED"),
                "detail": str(getattr(exc, "detail", "") or str(exc)),
                "store_id": clean_store_id,
                "mode": "llm_scoped",
                "llm_calls": 0,
                "registered_items": [],
                "registered_events": [],
                "rejected_candidates": [],
            }

    try:
        raw_proposal = llm_proposal_fn(copy.deepcopy(clean_ctx))
    except Exception as exc:
        if reservation is not None:
            try:
                setattr(exc, "budget_reservation", reservation)
            except Exception:
                pass
        raise
    parsed = _parse_proposal_output(raw_proposal)
    if parsed is None:
        return {
            "ok": False,
            "status": "invalid_proposal",
            "reason_code": "INVALID_PROPOSAL_FORMAT",
            "store_id": clean_store_id,
            "mode": "llm_scoped",
            "llm_calls": 1,
            "budget_reservation": reservation,
            "registered_items": [],
            "registered_events": [],
            "rejected_candidates": [],
        }

    raw_items = parsed.get("items") if isinstance(parsed.get("items"), Sequence) and not isinstance(parsed.get("items"), (str, bytes)) else []
    raw_events = parsed.get("events") if isinstance(parsed.get("events"), Sequence) and not isinstance(parsed.get("events"), (str, bytes)) else []

    scoped_items: list[dict[str, Any]] = []
    for row in raw_items:
        if isinstance(row, Mapping):
            copy_row = dict(row)
            if "store_id" not in copy_row or copy_row.get("store_id") is None:
                copy_row["store_id"] = clean_store_id
            scoped_items.append(copy_row)
        else:
            scoped_items.append(row)  # type: ignore[arg-type]

    scoped_events: list[dict[str, Any]] = []
    for ev in raw_events:
        if isinstance(ev, Mapping):
            copy_ev = dict(ev)
            if "venue_id" not in copy_ev and "store_id" not in copy_ev:
                copy_ev["venue_id"] = clean_store_id
            scoped_events.append(copy_ev)
        else:
            scoped_events.append(ev)  # type: ignore[arg-type]

    reg = _catalog.register_catalog_batch(
        day_root,
        items=scoped_items,
        events=scoped_events,
        source_kind="llm_scoped_generation",
        trigger_reason=trigger_reason,
        operation_key=operation_key,
        now_iso=now_str,
        allowed_store_ids=(clean_store_id,),
        allowed_venue_ids=(clean_store_id,),
    )
    reg["mode"] = "llm_scoped"
    reg["store_id"] = clean_store_id
    reg["llm_calls"] = 1
    reg["clean_context"] = clean_ctx
    if reservation is not None:
        reg["budget_reservation"] = reservation
    return reg


__all__ = [
    "AntiSpoilViolationError",
    "DEFAULT_EST_TOKENS",
    "FORBIDDEN_PRIVATE_SPOIL_KEYS",
    "MIN_GENERATION_COOLDOWN_HOURS",
    "PUBLIC_WORLD_CONTEXT_WHITELIST",
    "REQUEST_KIND_CONTENT",
    "anti_spoil_guard",
    "generate_world_content_batch",
]
