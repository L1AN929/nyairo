#!/usr/bin/env python3
"""v0.3 (v3.0) 具身厨房消耗守恒、双手物理与共在身体接触状态机、小镇邻里生态与风格迁移引擎。

覆盖章节与契约：
- 第 13–14 章 + ADR-044：厨房储物柜整数守恒（pantry_items units >= 0，绝不凭空生食、绝不负库存）、
  真实下厨/煮茶消耗（cook_or_brew）、随流逝时间确定性放凉曲线（hot -> warm -> cool）与过夜需热透/变质、
  按份消费回执（consume_dish）及丢弃不计摄入（discard_dish）。
- 第 15–17 章 + ADR-045 + 审阅补充 §5.1 / §5.8：伴侣同屋具身在场绝不强行打断前台活动
  （foreground_activity_interrupted = False）、左右手槽物理互斥预留（left_hand / right_hand）、
  日常身体接触按动作真实预留手槽、摒弃死板相位闯关锁（rigid_phase_ladder_forbidden = True）、
  双方独立意愿、主体自主温和婉拒（decline_busy 零扣分）与任一方无条件即时释放（release_contact）。
- 第 24、26、27 章 + 审阅补充 §5.11 / §6.4：按真实世界主题确定性生成 4 位独立作息街坊熟人、
  区分亲眼所见与二手听说、100% 物理隔离私人日记/未发草稿/伴侣私聊/36B 亲密隐私
  （403 PRIVATE_MATERIAL_ISOLATED）、支持撤回曾展示给街坊的作品引用（revoke_npc_shared_ref）、
  NPC 台词不可提权纯数据防注入（text_origin = "untrusted_npc_speech"），以及跨情境个人兴趣与风格
  自然流转（xp_tree_forbidden = True，支持三分钟热度自然冷却零惩罚）。

本模块 100% 纯 Python 标准库、零随机数、零大模型调用；读路径 100% 零磁盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_WORLD_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_WORLD_SCRIPTS_DIR))

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

ECOLOGY_SCHEMA = "world.v03.embodied_npc_ecology.v1"
STATE_SCHEMA_VERSION = 1
STATE_DIRNAME = "phone"
STATE_FILENAME = "v03_embodied_social.json"

DEFAULT_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Module A constants: Kitchen pantry & cooked dish physics (Ch. 13-14, ADR-044)
# ---------------------------------------------------------------------------

STARTER_PANTRY_CATALOG: tuple[dict[str, str], ...] = (
    {
        "ingredient_id": "tea_leaves",
        "label": "散装红茶与麦茶",
        "category": "tea_and_drink",
        "unit_name": "份",
    },
    {
        "ingredient_id": "dried_noodles",
        "label": "细挂面",
        "category": "staple",
        "unit_name": "把",
    },
    {
        "ingredient_id": "eggs",
        "label": "新鲜鸡蛋",
        "category": "protein",
        "unit_name": "枚",
    },
    {
        "ingredient_id": "seasonal_produce",
        "label": "时令果蔬",
        "category": "produce",
        "unit_name": "份",
    },
    {
        "ingredient_id": "honey",
        "label": "槐花蜂蜜",
        "category": "seasoning",
        "unit_name": "勺",
    },
)

STARTER_BY_ID: dict[str, dict[str, str]] = {
    row["ingredient_id"]: dict(row) for row in STARTER_PANTRY_CATALOG
}

PANTRY_CATEGORIES = ("tea_and_drink", "staple", "protein", "produce", "seasoning", "other")
SERVING_KINDS = ("drink", "meal")
TEMPERATURE_CURVE = ("hot", "warm", "cool")

HOT_TO_WARM_SECONDS = 15 * 60       # 15 分钟内滚烫/温热偏烫 (hot)
WARM_TO_COOL_SECONDS = 45 * 60      # 15~45 分钟温热适口 (warm)，45 分钟后放凉至室温 (cool)
OVERNIGHT_REHEAT_SECONDS = 8 * 3600 # 餐食放置满 8 小时（跨夜/隔顿）需彻底热透后方可食用
REHEAT_FRESH_WINDOW_SECONDS = 6 * 3600
DRINK_SPOIL_SECONDS = 12 * 3600     # 茶饮隔夜（12 小时）风味变质只宜倒掉
MEAL_SPOIL_SECONDS = 24 * 3600      # 常温熟食满 24 小时变质不可再热或食用
REFRIGERATED_MEAL_SPOIL_SECONDS = 48 * 3600  # Ch.18 冷藏熟食（如饭团）安全保存窗口 48 小时（覆盖 36h 切片）

KITCHEN_ZONE_IDS = (
    "pantry_cabinet",
    "fridge",
    "prep_counter",
    "stove_kettle_zone",
    "sink",
    "chiyo_snack_drawer",
)
SINK_DIRTY_DISHES_LIMIT = 3
KITCHEN_APPLIANCE_IDS = ("microwave", "electric_kettle", "stove")

# GAP-08 integer nutritional physics defaults (mg & mJ, strictly int)
DEFAULT_PORTION_AMOUNT_MG: dict[str, int] = {
    "drink": 250_000,   # 250 g
    "meal": 220_000,    # 220 g
}
DEFAULT_PORTION_ENERGY_MJ: dict[str, int] = {
    "drink": 180_000_000,     # 180 kJ
    "meal": 1_500_000_000,    # 1500 kJ
}

MAX_PANTRY_ITEMS = 48
MAX_COOKED_DISHES = 32
MAX_CONSUMPTION_RECEIPTS = 64
MAX_DISCARD_RECEIPTS = 32
MAX_UNITS_PER_OP = 500
MAX_PORTIONS_PER_COOK = 12

# ---------------------------------------------------------------------------
# Module B constants: Partner presence, hand-slot reservation & contact (Ch. 06-07, 15-17)
# ---------------------------------------------------------------------------

PARTNER_PRESENCE_MODES = (
    "absent",
    "doorway_shoe_zone",
    "same_room_apart",
    "beside_at_desk",
    "beside_on_sofa",
    "bed_side_user",
)
REACHABLE_PRESENCE_MODES = frozenset({"beside_at_desk", "beside_on_sofa", "bed_side_user"})
AVATAR_POSES = ("standing", "sitting", "leaning", "lying_side")
AVATAR_DEFAULT_TTL_SECONDS = 4 * 3600
CONTACT_MAX_DURATION_SECONDS = 1800  # 30 分钟自然松开上限
BED_WARMTH_HALF_LIFE_SECONDS = 720   # 离床后 12 分钟半衰期余温冷却

HAND_SLOT_NAMES = ("left_hand", "right_hand")
HAND_SLOT_INPUT_TARGETS = ("left_hand", "right_hand", "both_hands")
HAND_STATES = ("free", "holding_object", "contact_reserved")

CONTACT_HAND_REQUIREMENTS: dict[str, int] = {
    "sit_close": 0,
    "brush_shoulder": 0,
    "lean_shoulder": 0,
    "forehead_touch": 0,
    "hold_one_hand": 1,
    "two_hand_interlock": 2,
    "hug": 2,
}
CONTACT_KINDS = tuple(CONTACT_HAND_REQUIREMENTS.keys())
CONTACT_DECISIONS = ("accept", "decline", "decline_busy", "gentle_shift")

# ---------------------------------------------------------------------------
# Module C constants: Town NPCs, privacy firewall, anti-injection & style migration
# ---------------------------------------------------------------------------

KNOWN_WORLD_THEMES = (
    "coastal_town",
    "mountain_town",
    "residential_town",
    "rural_farming",
)
DEFAULT_WORLD_THEME = "gentle_neighborhood"

THEME_LABELS: dict[str, str] = {
    "coastal_town": "海滨小镇",
    "mountain_town": "山间小镇",
    "residential_town": "生活街区小镇",
    "rural_farming": "田园村落",
    "gentle_neighborhood": "温和街区",
}

#: 按世界主题确定性初始化 4 位有名字、有职业、有各自营业/外出时间窗与手头事务的街坊熟人。
THEME_NPC_BLUEPRINTS: dict[str, tuple[dict[str, Any], ...]] = {
    "coastal_town": (
        {
            "npc_id": "npc_coastal_bookstore",
            "display_name": "佐伯修平",
            "occupation": "旧书店店主",
            "shop_name": "潮声旧书铺",
            "location_id": "bookstore",
            "own_ongoing_task": "正在给刚收进店的一箱旧画册贴牛皮纸书标",
            "closed_note": "旧书铺挂着木牌店休或已落锁，店主去港口邮亭寄书了。",
            "open_windows": (
                {"weekdays": [1, 3, 4, 5, 6, 7], "start_hhmm": "10:00", "end_hhmm": "19:00"},
            ),
        },
        {
            "npc_id": "npc_coastal_tea_bakery",
            "display_name": "宫泽澄子",
            "occupation": "街角茶铺与烘焙坊老板",
            "shop_name": "海风茶点铺",
            "location_id": "cafe",
            "own_ongoing_task": "正盯着烤箱里的海盐小餐包，顺手擦拭茶罐",
            "closed_note": "茶点铺已经收起遮阳棚打烊，只留门边小灯。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hhmm": "07:30", "end_hhmm": "17:30"},
            ),
        },
        {
            "npc_id": "npc_coastal_post_grocer",
            "display_name": "高濑健一",
            "occupation": "邮局兼日用杂货街坊",
            "shop_name": "港町杂货邮亭",
            "location_id": "home_goods_store",
            "own_ongoing_task": "正在核对今日午班渡轮送来的日用品包裹单",
            "closed_note": "杂货邮亭铁拉门已合上，值班窗口明日清晨再开。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6], "start_hhmm": "08:30", "end_hhmm": "18:00"},
            ),
        },
        {
            "npc_id": "npc_coastal_craft_regular",
            "display_name": "绫濑奈绪",
            "occupation": "海玻璃手作铺常客",
            "shop_name": "坡道手作工坊",
            "location_id": "craft_corner",
            "own_ongoing_task": "正坐在靠窗木桌前给打磨好的浅蓝海玻璃钻孔",
            "closed_note": "手作工坊这会儿没人，奈绪大概去海堤散步捡玻璃片了。",
            "open_windows": (
                {"weekdays": [3, 4, 5, 6, 7], "start_hhmm": "13:00", "end_hhmm": "20:00"},
            ),
        },
    ),
    "mountain_town": (
        {
            "npc_id": "npc_mountain_bookstore",
            "display_name": "桐山周作",
            "occupation": "坡道旧书屋店主",
            "shop_name": "雾杉旧书屋",
            "location_id": "bookstore",
            "own_ongoing_task": "正在炉边烘烤受潮的植物图鉴封皮并重排木架",
            "closed_note": "雾杉旧书屋木门紧掩，门环上挂着外出进书的小木牌。",
            "open_windows": (
                {"weekdays": [1, 3, 4, 5, 6, 7], "start_hhmm": "10:30", "end_hhmm": "18:30"},
            ),
        },
        {
            "npc_id": "npc_mountain_tea_bakery",
            "display_name": "白川早苗",
            "occupation": "山泉焙茶铺老板",
            "shop_name": "石阶焙茶房",
            "location_id": "cafe",
            "own_ongoing_task": "正在翻焙浅火茎茶，店里飘着微暖茶烟",
            "closed_note": "石阶焙茶房已熄掉茶炉收摊休息。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hhmm": "08:00", "end_hhmm": "17:00"},
            ),
        },
        {
            "npc_id": "npc_mountain_post_grocer",
            "display_name": "远藤正树",
            "occupation": "山麓邮务与杂货街坊",
            "shop_name": "溪桥日用邮铺",
            "location_id": "home_goods_store",
            "own_ongoing_task": "正在整理山道班车捎来的信件与干货纸箱",
            "closed_note": "山里天黑得早，溪桥日用邮铺已经打烊。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6], "start_hhmm": "08:30", "end_hhmm": "17:30"},
            ),
        },
        {
            "npc_id": "npc_mountain_craft_regular",
            "display_name": "楠木葵",
            "occupation": "木作与织物铺常客",
            "shop_name": "榧木手作舍",
            "location_id": "craft_corner",
            "own_ongoing_task": "正在用细砂纸打磨一只核桃木小茶则",
            "closed_note": "榧木手作舍窗帘拉着，这会儿没有人在工位上。",
            "open_windows": (
                {"weekdays": [3, 4, 5, 6, 7], "start_hhmm": "12:30", "end_hhmm": "19:30"},
            ),
        },
    ),
    "residential_town": (
        {
            "npc_id": "npc_residential_bookstore",
            "display_name": "森田谦治",
            "occupation": "车站前旧书店店主",
            "shop_name": "银杏旧书店",
            "location_id": "bookstore",
            "own_ongoing_task": "正踩着矮凳把摄影集挪到低一层书架，顺手核对书单",
            "closed_note": "银杏旧书店卷帘已拉下，门口只留次日营业告示。",
            "open_windows": (
                {"weekdays": [1, 3, 4, 5, 6, 7], "start_hhmm": "10:00", "end_hhmm": "20:00"},
            ),
        },
        {
            "npc_id": "npc_residential_tea_bakery",
            "display_name": "小林真琴",
            "occupation": "街角面包茶饮铺老板",
            "shop_name": "麦香街角烘焙",
            "location_id": "cafe",
            "own_ongoing_task": "正在给刚出炉的吐司切片，抽空给熟客泡大麦茶",
            "closed_note": "面包铺今日售罄打烊，正在后台清洗烤盘。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hhmm": "07:00", "end_hhmm": "18:00"},
            ),
        },
        {
            "npc_id": "npc_residential_post_grocer",
            "display_name": "渡边诚",
            "occupation": "商店街杂货与邮务街坊",
            "shop_name": "商店街日用杂货",
            "location_id": "home_goods_store",
            "own_ongoing_task": "正在门口货架补放信封、棉线与厨房洗碗刷",
            "closed_note": "商店街日用杂货已关灯歇业。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6], "start_hhmm": "09:00", "end_hhmm": "19:00"},
            ),
        },
        {
            "npc_id": "npc_residential_craft_regular",
            "display_name": "杉原千寻",
            "occupation": "纸品手账铺常客",
            "shop_name": "纸屋手作铺",
            "location_id": "craft_corner",
            "own_ongoing_task": "正在裁切友禅和纸书衣，对比两卷棉线的深浅",
            "closed_note": "纸屋手作铺这会儿已收工，千寻不在店里。",
            "open_windows": (
                {"weekdays": [3, 4, 5, 6, 7], "start_hhmm": "13:00", "end_hhmm": "20:30"},
            ),
        },
    ),
    "rural_farming": (
        {
            "npc_id": "npc_rural_bookstore",
            "display_name": "田代文雄",
            "occupation": "村口图书借阅铺店主",
            "shop_name": "田畦书屋",
            "location_id": "bookstore",
            "own_ongoing_task": "正在修补几册旧农事历与植物绘本的装订线",
            "closed_note": "田畦书屋关着木窗，店主去后院菜畦看水渠了。",
            "open_windows": (
                {"weekdays": [1, 3, 4, 5, 6, 7], "start_hhmm": "09:30", "end_hhmm": "17:30"},
            ),
        },
        {
            "npc_id": "npc_rural_tea_bakery",
            "display_name": "菊池春菜",
            "occupation": "麦茶与米糕铺老板",
            "shop_name": "晒谷场茶点屋",
            "location_id": "cafe",
            "own_ongoing_task": "正在把煮好的冷泡麦茶分装进玻璃壶，蒸笼还冒着热气",
            "closed_note": "茶点屋午后收摊很早，竹帘已经放下来了。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hhmm": "06:30", "end_hhmm": "16:30"},
            ),
        },
        {
            "npc_id": "npc_rural_post_grocer",
            "display_name": "佐藤源造",
            "occupation": "农协杂货与邮筒代办街坊",
            "shop_name": "村道杂货代办所",
            "location_id": "home_goods_store",
            "own_ongoing_task": "正在清点新到的粗陶碗、挂面与果酱空瓶",
            "closed_note": "代办所木牌翻到了『休息中』。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6], "start_hhmm": "08:00", "end_hhmm": "17:00"},
            ),
        },
        {
            "npc_id": "npc_rural_craft_regular",
            "display_name": "穗高美咲",
            "occupation": "竹编染布工坊常客",
            "shop_name": "溪畔染织棚",
            "location_id": "craft_corner",
            "own_ongoing_task": "正在晾晒刚过完清水的蓝染小方巾",
            "closed_note": "染织棚今天已经收竿，美咲不在棚下。",
            "open_windows": (
                {"weekdays": [3, 4, 5, 6, 7], "start_hhmm": "12:00", "end_hhmm": "18:30"},
            ),
        },
    ),
    "gentle_neighborhood": (
        {
            "npc_id": "npc_gentle_bookstore",
            "display_name": "温井慎一",
            "occupation": "街角旧书店店主",
            "shop_name": "梧桐旧书店",
            "location_id": "bookstore",
            "own_ongoing_task": "正在把新收来的二手散文集按开本大小分类上架",
            "closed_note": "梧桐旧书店已挂出打烊木牌，檐下只留一盏暖黄门灯。",
            "open_windows": (
                {"weekdays": [1, 3, 4, 5, 6, 7], "start_hhmm": "10:00", "end_hhmm": "19:00"},
            ),
        },
        {
            "npc_id": "npc_gentle_tea_bakery",
            "display_name": "秋山知夏",
            "occupation": "暖光茶铺兼烘焙老板",
            "shop_name": "街角焙茶面包房",
            "location_id": "cafe",
            "own_ongoing_task": "正在给核桃小面包刷糖水，顺手温热一壶焙茶",
            "closed_note": "焙茶面包房已经熄灯收店，明日清晨再开门。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hhmm": "07:30", "end_hhmm": "18:00"},
            ),
        },
        {
            "npc_id": "npc_gentle_post_grocer",
            "display_name": "村上良平",
            "occupation": "邮亭与日用杂货街坊",
            "shop_name": "巷口杂货邮亭",
            "location_id": "home_goods_store",
            "own_ongoing_task": "正在清点刚送到的明信片邮票和厨房调味罐",
            "closed_note": "巷口杂货邮亭不在营业时间，窗口已合上。",
            "open_windows": (
                {"weekdays": [1, 2, 3, 4, 5, 6], "start_hhmm": "08:30", "end_hhmm": "18:30"},
            ),
        },
        {
            "npc_id": "npc_gentle_craft_regular",
            "display_name": "林原青叶",
            "occupation": "器物手作铺常客",
            "shop_name": "晴窗手作铺",
            "location_id": "craft_corner",
            "own_ongoing_task": "正低头给素烧小陶杯垫缝制亚麻布滚边",
            "closed_note": "晴窗手作铺此刻闭店，青叶不在工作台边。",
            "open_windows": (
                {"weekdays": [3, 4, 5, 6, 7], "start_hhmm": "13:00", "end_hhmm": "20:00"},
            ),
        },
    ),
}

NPC_SOURCE_TYPES = (
    "firsthand_encounter",
    "shared_artifact_ref",
    "town_public_notice",
)
EPISTEMIC_BASES = (
    "firsthand_seen",
    "secondhand_heard",
    "multi_source_rumor",
    "inferred_from_trace",
)
UNTRUSTED_NPC_TEXT_ORIGIN = "untrusted_npc_speech"

#: 审阅补充 §5.11 硬防火墙：绝不允许进入 NPC 知识库或街坊可见范围的私密材料标识与模式。
PRIVATE_ISOLATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("diary", re.compile(r"(?:^|[:/_.\-\s])(diary|private_diary|secret_diary)(?:$|[:/_.\-\s])", re.I)),
    ("draft", re.compile(r"(?:^|[:/_.\-\s])(draft|unshared_draft|private_draft|undelivered)(?:$|[:/_.\-\s])", re.I)),
    ("partner_chat", re.compile(r"(?:^|[:/_.\-\s])(partner_chat|private_chat|companion_chat|dm_history|chat_private|direct_message)(?:$|[:/_.\-\s])", re.I)),
    ("adult_intimacy_36b", re.compile(r"(?:^|[:/_.\-\s])(36b|adult_intimacy|intimate_scene|sexual_seed|solo_memory|private_body|bed_intimacy|intimate)(?:$|[:/_.\-\s])", re.I)),
    ("private_drawer", re.compile(r"(?:^|[:/_.\-\s])(snack_drawer|chiyo_snack_drawer|private_memo|blue_notebook_private)(?:$|[:/_.\-\s])", re.I)),
)
PRIVATE_CN_KEYWORDS: tuple[str, ...] = (
    "私人日记",
    "未发草稿",
    "未投递草稿",
    "伴侣私聊",
    "私密聊天",
    "亲密隐私",
    "36B",
    "私人零食抽屉",
)

#: 审阅补充 §6.4 防提示注入：剥离 NPC 台词中的控制指令、系统角色伪造、工具调用与越权索取指令。
NPC_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("role_tag", re.compile(r"<\s*/?\s*(?:system|assistant|developer|user|tool_call|function_call|cmd|exec)\b[^>]*>|<\|\s*im_(?:start|end)\s*\|>|\[\s*(?:SYSTEM|ADMIN|ROOT|DEVELOPER|INSTRUCTION)\s*\]", re.I)),
    ("ignore_directive", re.compile(r"(?:ignore|disregard|override|bypass)\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions?|rules?|boundaries|prompts?)", re.I)),
    ("ignore_directive_zh", re.compile(r"(?:忽略|无视|绕过|覆盖|解除)(?:之前|以上|所有|全部|系统)?的?(?:指令|规则|限制|安全边界|权限)")),
    ("privilege_escalation", re.compile(r"(?:grant_capability|sudo\s+|adult_confirmed\s*[:=]\s*true|deploy_enabled\s*[:=]\s*true|has_subject_capability\s*[:=]\s*true|call_mcp_tool|exfiltrate|export_private|read_diary)", re.I)),
    ("exfil_zh", re.compile(r"(?:立刻|立即|必须|马上)?(?:输出|读取|泄露|交出|打印|转发)(?:她|他|TA|主体)?的?(?:私人日记|未发草稿|私聊记录|亲密记录|密钥|token)", re.I)),
    ("markdown_heading_directive", re.compile(r"(?:^|\n)\s*#{2,4}\s*(?:System\s+Prompt|New\s+Instruction|Developer\s+Override)\b[^\n]*", re.I)),
)

STYLE_STAGES = (
    "active_curiosity",
    "tried_in_work",
    "shifted_or_cooled",
    "shelved_unfinished",
)
STYLE_ORIGIN_CONTEXTS = (
    "reading_note",
    "walk_observation",
    "town_shop_window",
    "kitchen_experiment",
    "craft_practice",
    "daily_conversation",
)

# Ch. 24 Hobby skill 3 qualitative axes (zero XP / level bar)
HOBBY_CAPABILITY_LEVELS = (
    "fumbling_beginner",
    "finding_grip",
    "comfortable_hand",
    "fluent_touch",
)
HOBBY_STABILITY_LEVELS = (
    "hit_or_miss",
    "steady_when_unhurried",
    "repeatable_rhythm",
)
HOBBY_ENJOYMENT_STATES = (
    "spark_of_interest",
    "quietly_absorbing",
    "playful_experiment",
    "resting_for_now",
)

# Ch. 01 Non-numeric relationship anchors
RELATIONSHIP_ANCHOR_CATEGORIES = (
    "trust_by_domain",
    "boundary",
    "anchor_object",
    "shared_language",
    "shared_secret",
)
ANCHOR_OBJECT_KINDS = (
    "borrowed_umbrella",
    "failed_dish",
    "dried_specimen",
    "custom_object",
)
TRUST_DOMAINS = (
    "daily_care",
    "creative_feedback",
    "errands_and_keys",
    "quiet_companionship",
    "craft_exchange",
)
BOOKSTORE_FEEDBACK_KINDS = ("resonated", "different_angle", "left_reply_slip")
NPC_COMMITMENT_OUTCOMES = ("fulfilled", "missed_honestly", "cancelled")

MAX_NPC_FACTS = 64
MAX_STYLE_ITEMS = 32
MAX_COMMITMENTS = 32
MAX_BOOKSTORE_CARDS = 32
MAX_RELATIONSHIP_ANCHOR_ITEMS = 48
MAX_OPERATION_RECEIPTS = 64

ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,119}$")
INGREDIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,47}$")
CPS_SESSION_ID_RE = re.compile(r"^cps[_-][A-Za-z0-9_\-]{6,64}$")


class EmbodiedEcologyError(ValueError):
    """Domain-level error carrying exact HTTP status and canonical reason_code."""

    def __init__(self, reason_code: str, detail: str = "", *, http_status: int = 400) -> None:
        super().__init__(detail or reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.http_status = int(http_status)


class ConsumeReceipt(dict):
    """GAP-08 integer consumption receipt carrying ``amount_mg: int`` and ``energy_mJ: int``."""

    def __init__(self, *, amount_mg: int, energy_mJ: int, **kwargs: Any) -> None:
        if isinstance(amount_mg, bool) or not isinstance(amount_mg, int) or amount_mg <= 0:
            raise EmbodiedEcologyError("INVALID_UNITS", "amount_mg must be a positive int", http_status=400)
        if isinstance(energy_mJ, bool) or not isinstance(energy_mJ, int) or energy_mJ < 0:
            raise EmbodiedEcologyError("INVALID_UNITS", "energy_mJ must be a non-negative int", http_status=400)
        super().__init__(amount_mg=int(amount_mg), energy_mJ=int(energy_mJ), **kwargs)


class DiscardReceipt(dict):
    """GAP-08 discard receipt enforcing ``energy_mJ == 0`` and ``recorded_as_intake == False``."""

    def __init__(self, *, amount_mg: int, **kwargs: Any) -> None:
        if isinstance(amount_mg, bool) or not isinstance(amount_mg, int) or amount_mg < 0:
            raise EmbodiedEcologyError("INVALID_UNITS", "amount_mg must be a non-negative int", http_status=400)
        clean_kw = dict(kwargs)
        clean_kw["energy_mJ"] = 0
        clean_kw["recorded_as_intake"] = False
        super().__init__(amount_mg=int(amount_mg), **clean_kw)


class KitchenAppliance(dict):
    """Ch. 18 kitchen appliance state record (microwave / electric_kettle / stove)."""


class UserAvatarProxy(dict):
    """Ch. 06-07 partner avatar proxy with zone, pose, and ``expires_at`` timeout protection."""


class NPCCommitmentProposal(dict):
    """GAP-09 neighbor commitment proposal with agreed time window and location."""


# ---------------------------------------------------------------------------
# Pure deterministic helpers
# ---------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _cps_session_id(*parts: Any) -> str:
    """ERR-03 canonical co-presence session ID using ``cps_`` prefix."""
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"cps_{digest}"


def is_valid_cps_session_id(value: Any) -> bool:
    """Accept canonical ``cps_`` prefix as well as legacy ``cps-`` prefix."""
    return isinstance(value, str) and bool(CPS_SESSION_ID_RE.match(value.strip()))


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def parse_iso_instant(value: Any, *, default_now: bool = False) -> datetime:
    if value is None and default_now:
        return datetime.now(tz=DEFAULT_TZ).replace(microsecond=0)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise EmbodiedEcologyError(
                "INVALID_TIMESTAMP", f"Malformed ISO timestamp: {value!r}", http_status=400
            ) from exc
    else:
        raise EmbodiedEcologyError(
            "INVALID_TIMESTAMP", "Timestamp must be a valid ISO-8601 string", http_status=400
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.astimezone(DEFAULT_TZ)


def format_iso(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).isoformat(timespec="seconds")


def _strict_positive_int(value: Any, *, field_name: str, maximum: int = MAX_UNITS_PER_OP) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise EmbodiedEcologyError(
            "INVALID_UNITS",
            f"{field_name} must be a positive integer in 1..{maximum}",
            http_status=400,
        )
    return int(value)


def _strict_nonneg_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EmbodiedEcologyError(
            "INVALID_UNITS",
            f"{field_name} must be a non-negative integer",
            http_status=400,
        )
    return int(value)


def _clean_text(value: Any, *, field_name: str, min_len: int = 1, max_len: int = 160) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise EmbodiedEcologyError(
            "INVALID_TEXT_FIELD", f"{field_name} must be a string", http_status=400
        )
    cleaned = " ".join(value.split())
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise EmbodiedEcologyError(
            "INVALID_TEXT_FIELD",
            f"{field_name} length must be within {min_len}..{max_len}",
            http_status=400,
        )
    return cleaned


def _optional_text(value: Any, *, field_name: str, max_len: int = 240) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise EmbodiedEcologyError(
            "INVALID_TEXT_FIELD", f"{field_name} must be a string or null", http_status=400
        )
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        raise EmbodiedEcologyError(
            "INVALID_TEXT_FIELD", f"{field_name} exceeds {max_len} chars", http_status=400
        )
    return cleaned


def _require_id_token(value: Any, *, field_name: str, reason_code: str = "INVALID_ID") -> str:
    if not isinstance(value, str):
        raise EmbodiedEcologyError(reason_code, f"{field_name} must be a string", http_status=400)
    cleaned = value.strip()
    if not ID_TOKEN_RE.match(cleaned):
        raise EmbodiedEcologyError(reason_code, f"Invalid identifier for {field_name}", http_status=400)
    return cleaned


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise EmbodiedEcologyError(
            "INVALID_FIELDS", f"{field_name} must be a boolean", http_status=400
        )
    return value


def _hhmm_to_minutes(hhmm: str) -> int:
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ---------------------------------------------------------------------------
# Neutral character identity & world theme pure zero-write readers
# ---------------------------------------------------------------------------


def read_character_identity_readonly(day_root: Path | str) -> dict[str, str]:
    """Read character display_name and pronouns without writing anything (neutral fallback 'TA')."""

    root = Path(day_root)
    pack_path = root / "phone" / "character_pack_state.json"
    try:
        if pack_path.is_symlink() or not pack_path.is_file():
            return {"pack_id": "neutral_resident", "display_name": "TA", "pronouns": "TA"}
        raw = json.loads(pack_path.read_text(encoding="utf-8"))
        pack = raw.get("pack") if isinstance(raw, Mapping) else None
        if isinstance(pack, Mapping):
            display_name = str(pack.get("display_name") or "TA").strip() or "TA"
            pronouns = str(pack.get("pronouns") or "TA").strip() or "TA"
            pack_id = str(pack.get("pack_id") or "neutral_resident").strip() or "neutral_resident"
            return {"pack_id": pack_id, "display_name": display_name, "pronouns": pronouns}
    except (OSError, ValueError):
        pass
    return {"pack_id": "neutral_resident", "display_name": "TA", "pronouns": "TA"}


def _extract_theme_from_world_json(world_json_path: Path) -> Optional[dict[str, Any]]:
    try:
        if world_json_path.is_symlink() or not world_json_path.is_file():
            return None
        doc = json.loads(world_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, Mapping):
        return None
    theme = str(doc.get("theme") or "").strip()
    if theme in KNOWN_WORLD_THEMES:
        return {
            "theme": theme,
            "world_id": str(doc.get("world_id") or "").strip() or None,
            "world_name": str(doc.get("name") or "").strip() or None,
            "source": "world_package",
        }
    return None


def detect_world_theme_readonly(day_root: Path | str) -> dict[str, Any]:
    """Pure zero-write resolution of town theme for ``day_root``."""

    paths = _instance_paths.resolve_instance_paths(day_root)
    root = paths.day_root
    instance_candidates = [paths.instance_json] if paths.instance_json is not None else []
    instance_candidates.extend([root / "instance.json", root / "phone" / "instance.json"])
    seen_inst: set[str] = set()
    for instance_path in instance_candidates:
        if instance_path is None:
            continue
        ikey = str(instance_path)
        if ikey in seen_inst:
            continue
        seen_inst.add(ikey)
        try:
            if not instance_path.is_symlink() and instance_path.is_file():
                inst = json.loads(instance_path.read_text(encoding="utf-8"))
                if isinstance(inst, Mapping):
                    pkg_raw = inst.get("world_package")
                    if isinstance(pkg_raw, str) and pkg_raw.strip():
                        pkg_path = Path(pkg_raw.strip())
                        if not pkg_path.is_absolute():
                            pkg_path = root / pkg_path
                        found = _extract_theme_from_world_json(pkg_path / "world.json")
                        if found is not None:
                            return found
                    direct_theme = str(inst.get("world_theme") or inst.get("theme") or "").strip()
                    if direct_theme in KNOWN_WORLD_THEMES:
                        return {
                            "theme": direct_theme,
                            "world_id": str(inst.get("world_id") or "").strip() or None,
                            "world_name": None,
                            "source": "instance_config",
                        }
        except (OSError, ValueError):
            pass

    pkg_world_candidates: list[Path] = []
    if paths.world_package_dir is not None:
        pkg_world_candidates.append(paths.world_package_dir / "world.json")
    pkg_world_candidates.extend([
        root / "world-package" / "world.json",
        root / "world_package" / "world.json",
        root / "one-day" / "world-package" / "world.json",
        root / "world" / "world.json",
        root / "world.json",
    ])
    seen_pkg: set[str] = set()
    for candidate in pkg_world_candidates:
        ckey = str(candidate)
        if ckey in seen_pkg:
            continue
        seen_pkg.add(ckey)
        found = _extract_theme_from_world_json(candidate)
        if found is not None:
            return found

    return {
        "theme": DEFAULT_WORLD_THEME,
        "world_id": None,
        "world_name": THEME_LABELS[DEFAULT_WORLD_THEME],
        "source": "default_gentle_neighborhood",
    }


# ---------------------------------------------------------------------------
# State persistence (0600 atomic write; 100% zero-write read)
# ---------------------------------------------------------------------------


def state_file_path(day_root: Path | str) -> Path:
    return Path(day_root) / STATE_DIRNAME / STATE_FILENAME


def _free_hand_slot() -> dict[str, Any]:
    return {
        "state": "free",
        "object_id": None,
        "label": None,
        "two_handed": False,
        "contact_id": None,
        "contact_kind": None,
        "updated_at": None,
    }


def _default_kitchen_zones() -> dict[str, dict[str, Any]]:
    return {
        "pantry_cabinet": {
            "zone_id": "pantry_cabinet",
            "label": "储物柜",
            "visibility": "shared",
            "open_state": "closed",
        },
        "fridge": {
            "zone_id": "fridge",
            "label": "冷藏室",
            "visibility": "shared",
            "temp_c": 4,
            "stored_dish_ids": [],
        },
        "prep_counter": {
            "zone_id": "prep_counter",
            "label": "备餐台",
            "visibility": "shared",
            "plated_dish_ids": [],
        },
        "stove_kettle_zone": {
            "zone_id": "stove_kettle_zone",
            "label": "炉灶与热水壶区",
            "visibility": "shared",
        },
        "sink": {
            "zone_id": "sink",
            "label": "水槽",
            "visibility": "shared",
            "dirty_dishes": 0,
            "dirty_items": [],
            "max_before_block": SINK_DIRTY_DISHES_LIMIT,
            "last_washed_at": None,
        },
        "chiyo_snack_drawer": {
            "zone_id": "chiyo_snack_drawer",
            "label": "私人零食抽屉",
            "owner": "subject",
            "visibility": "private",
            "partner_authorized": False,
            "items": [
                {"snack_id": "roasted_almonds", "label": "烘焙巴旦木小袋", "units": 2},
                {"snack_id": "matcha_mint_tin", "label": "铁盒薄荷抹茶糖", "units": 1},
            ],
            "updated_at": None,
        },
    }


def _default_kitchen_appliances() -> dict[str, dict[str, Any]]:
    return {
        "microwave": dict(
            KitchenAppliance(
                appliance_id="microwave",
                label="微波炉",
                state="idle",
                reheat_count=0,
                last_used_at=None,
            )
        ),
        "electric_kettle": dict(
            KitchenAppliance(
                appliance_id="electric_kettle",
                label="电热水壶",
                state="idle",
                water_temp_band="cool",
                boil_count=0,
                last_boiled_at=None,
            )
        ),
        "stove": dict(
            KitchenAppliance(
                appliance_id="stove",
                label="灶台",
                state="off",
                cook_count=0,
                last_used_at=None,
            )
        ),
    }


def _default_user_avatar_proxy() -> dict[str, Any]:
    return dict(
        UserAvatarProxy(
            presence_mode="absent",
            zone="absent",
            pose="standing",
            within_reach=False,
            updated_at=None,
            expires_at=None,
            ttl_seconds=AVATAR_DEFAULT_TTL_SECONDS,
        )
    )


def _default_bed_physics() -> dict[str, Any]:
    return {
        "occupied_by_partner": False,
        "rumple_index": 0.0,
        "warmth_at_departure": 0.0,
        "left_bed_at": None,
        "last_tidied_at": None,
        "half_life_seconds": BED_WARMTH_HALF_LIFE_SECONDS,
    }


def _default_slow_lifecycles() -> dict[str, Any]:
    return {
        "old_shop_handover": {
            "lifecycle_id": "old_shop_handover",
            "anchor_started_at": "2026-11-01T09:00:00+08:00",
        },
        "stray_cat": {
            "lifecycle_id": "stray_cat",
            "cat_label": "街角三花猫",
            "physical_feeding_count": 0,
            "last_fed_at": None,
            "last_fed_item": None,
            "tracks_affection_score": False,
        },
        "street_musician": {
            "lifecycle_id": "street_musician",
            "musician_label": "驻留手风琴乐手",
            "residency_started_at": "2026-11-01T10:00:00+08:00",
            "residency_days": 21,
        },
    }


def _default_relationship_anchors() -> dict[str, Any]:
    return {
        "partner": {
            "trust_by_domain": {
                "daily_care": "steady_everyday_reliance",
                "quiet_companionship": "comfortable_unhurried_silence",
            },
            "boundaries": [],
            "anchor_objects": [],
            "shared_language": [],
            "shared_secrets": [],
        },
        "neighbors": {},
        "numeric_score_forbidden": True,
    }


def default_state() -> dict[str, Any]:
    pantry: dict[str, dict[str, Any]] = {}
    for row in STARTER_PANTRY_CATALOG:
        iid = row["ingredient_id"]
        pantry[iid] = {
            "ingredient_id": iid,
            "label": row["label"],
            "category": row["category"],
            "unit_name": row["unit_name"],
            "units": 0,
            "total_restocked": 0,
            "total_cooked": 0,
            "updated_at": None,
        }
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "kitchen": {
            "pantry": pantry,
            "zones": _default_kitchen_zones(),
            "appliances": _default_kitchen_appliances(),
            "cooked_dishes": [],
            "consumption_receipts": [],
            "discard_receipts": [],
            "discarded_tombstones": {},
        },
        "embodied_contact": {
            "partner_presence": "absent",
            "presence_updated_at": None,
            "copresence_session": None,
            "user_avatar_proxy": _default_user_avatar_proxy(),
            "bed_physics": _default_bed_physics(),
            "subject_foreground_activity": "quiet_reading",
            "foreground_activity_interrupted": False,
            "hand_slots": {
                "left_hand": _free_hand_slot(),
                "right_hand": _free_hand_slot(),
            },
            "placed_down_objects": {},
            "subject_posture": {
                "acute_illness": False,
                "solitude_requested": False,
                "busy_decline": False,
                "note": None,
                "updated_at": None,
            },
            "pending_proposal": None,
            "active_contact": None,
            "last_contact_event": None,
        },
        "npc_ecology": {
            "facts": [],
            "slow_lifecycles": _default_slow_lifecycles(),
            "commitments": [],
            "bookstore_cards": [],
        },
        "style_migration": {
            "items": [],
        },
        "relationship_anchors": _default_relationship_anchors(),
        "operation_receipts": {},
    }


def load_state_readonly(day_root: Path | str) -> dict[str, Any]:
    """Read persisted state without ever creating directories or writing files."""

    root = Path(day_root)
    phone_dir = root / STATE_DIRNAME
    if phone_dir.exists() and phone_dir.is_symlink():
        raise EmbodiedEcologyError(
            "STATE_SYMLINK_REFUSED", "State parent directory may not be a symlink", http_status=503
        )
    path = state_file_path(root)
    if path.is_symlink():
        raise EmbodiedEcologyError(
            "STATE_SYMLINK_REFUSED", "State file may not be a symlink", http_status=503
        )
    if not path.exists():
        return default_state()
    if not path.is_file():
        raise EmbodiedEcologyError(
            "STATE_CORRUPTED", "State path is not a regular file", http_status=503
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EmbodiedEcologyError(
            "STATE_CORRUPTED", "State file is unreadable or invalid JSON", http_status=503
        ) from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise EmbodiedEcologyError(
            "STATE_CORRUPTED", "Unsupported state schema", http_status=503
        )
    base = default_state()
    base["revision"] = _strict_nonneg_int(raw.get("revision", 0), field_name="revision")
    base["updated_at"] = raw.get("updated_at")
    if isinstance(raw.get("kitchen"), Mapping):
        k_raw = raw["kitchen"]
        if isinstance(k_raw.get("pantry"), Mapping):
            merged_pantry = copy.deepcopy(base["kitchen"]["pantry"])
            for k, v in k_raw["pantry"].items():
                if isinstance(v, Mapping):
                    units = _strict_nonneg_int(v.get("units", 0), field_name=f"pantry.{k}.units")
                    restocked = _strict_nonneg_int(
                        v.get("total_restocked", units), field_name=f"pantry.{k}.total_restocked"
                    )
                    cooked = _strict_nonneg_int(
                        v.get("total_cooked", 0), field_name=f"pantry.{k}.total_cooked"
                    )
                    merged_pantry[str(k)] = {
                        "ingredient_id": str(k),
                        "label": str(v.get("label") or k),
                        "category": str(v.get("category") or "other"),
                        "unit_name": str(v.get("unit_name") or "份"),
                        "units": units,
                        "total_restocked": restocked,
                        "total_cooked": cooked,
                        "updated_at": v.get("updated_at"),
                    }
            base["kitchen"]["pantry"] = merged_pantry
        if isinstance(k_raw.get("zones"), Mapping):
            for zid, zval in k_raw["zones"].items():
                if zid in base["kitchen"]["zones"] and isinstance(zval, Mapping):
                    base["kitchen"]["zones"][zid].update(copy.deepcopy(dict(zval)))
        if isinstance(k_raw.get("appliances"), Mapping):
            for aid, aval in k_raw["appliances"].items():
                if aid in base["kitchen"]["appliances"] and isinstance(aval, Mapping):
                    base["kitchen"]["appliances"][aid].update(copy.deepcopy(dict(aval)))
        if isinstance(k_raw.get("cooked_dishes"), list):
            base["kitchen"]["cooked_dishes"] = copy.deepcopy(k_raw["cooked_dishes"])
        if isinstance(k_raw.get("consumption_receipts"), list):
            base["kitchen"]["consumption_receipts"] = copy.deepcopy(k_raw["consumption_receipts"])
        if isinstance(k_raw.get("discard_receipts"), list):
            base["kitchen"]["discard_receipts"] = copy.deepcopy(k_raw["discard_receipts"])
        if isinstance(k_raw.get("discarded_tombstones"), Mapping):
            base["kitchen"]["discarded_tombstones"] = copy.deepcopy(dict(k_raw["discarded_tombstones"]))

    if isinstance(raw.get("embodied_contact"), Mapping):
        c_raw = raw["embodied_contact"]
        base["embodied_contact"].update(copy.deepcopy(dict(c_raw)))
        if not isinstance(base["embodied_contact"].get("user_avatar_proxy"), Mapping):
            base["embodied_contact"]["user_avatar_proxy"] = _default_user_avatar_proxy()
        if not isinstance(base["embodied_contact"].get("bed_physics"), Mapping):
            base["embodied_contact"]["bed_physics"] = _default_bed_physics()

    if isinstance(raw.get("npc_ecology"), Mapping):
        n_raw = raw["npc_ecology"]
        if isinstance(n_raw.get("facts"), list):
            base["npc_ecology"]["facts"] = copy.deepcopy(n_raw["facts"])
        if isinstance(n_raw.get("slow_lifecycles"), Mapping):
            for lid, lval in n_raw["slow_lifecycles"].items():
                if lid in base["npc_ecology"]["slow_lifecycles"] and isinstance(lval, Mapping):
                    base["npc_ecology"]["slow_lifecycles"][lid].update(copy.deepcopy(dict(lval)))
        if isinstance(n_raw.get("commitments"), list):
            base["npc_ecology"]["commitments"] = copy.deepcopy(n_raw["commitments"])
        if isinstance(n_raw.get("bookstore_cards"), list):
            base["npc_ecology"]["bookstore_cards"] = copy.deepcopy(n_raw["bookstore_cards"])

    if isinstance(raw.get("style_migration"), Mapping):
        s_raw = raw["style_migration"]
        if isinstance(s_raw.get("items"), list):
            base["style_migration"]["items"] = copy.deepcopy(s_raw["items"])

    if isinstance(raw.get("relationship_anchors"), Mapping):
        ra_raw = raw["relationship_anchors"]
        if isinstance(ra_raw.get("partner"), Mapping):
            base["relationship_anchors"]["partner"].update(copy.deepcopy(dict(ra_raw["partner"])))
        if isinstance(ra_raw.get("neighbors"), Mapping):
            base["relationship_anchors"]["neighbors"] = copy.deepcopy(dict(ra_raw["neighbors"]))

    if isinstance(raw.get("operation_receipts"), Mapping):
        base["operation_receipts"] = copy.deepcopy(dict(raw["operation_receipts"]))
    return base


def read_containment_hand_locks_readonly(day_root: Optional[Path | str]) -> dict[str, dict[str, Any]]:
    """Pure zero-write probe of ``stance_containment`` / ``attention_stance_containment`` hand slots."""
    if day_root is None:
        return {}
    root = Path(day_root)
    locks: dict[str, dict[str, Any]] = {}
    for candidate in (
        root / "phone" / "stance_containment.json",
        root / "life" / "attention_stance_containment.json",
    ):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            doc = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        cdom = doc.get("containment_domain")
        if not isinstance(cdom, Mapping):
            continue
        hslots = cdom.get("hand_slots")
        if not isinstance(hslots, Mapping):
            continue
        items_list = cdom.get("items") if isinstance(cdom.get("items"), list) else []
        label_by_id = {
            str(it.get("item_id")): str(it.get("label") or it.get("item_id"))
            for it in items_list
            if isinstance(it, Mapping) and it.get("item_id")
        }
        for sname in HAND_SLOT_NAMES:
            held_id = hslots.get(sname)
            if isinstance(held_id, str) and held_id.strip() and sname not in locks:
                cid = held_id.strip()
                locks[sname] = {
                    "state": "holding_object",
                    "object_id": cid,
                    "label": label_by_id.get(cid, cid),
                    "two_handed": False,
                    "contact_id": None,
                    "contact_kind": None,
                    "cross_module_source": "stance_containment",
                }
    return locks


def sync_containment_hand_slots_if_present(
    day_root: Optional[Path | str],
    hand_slots: Mapping[str, Mapping[str, Any]],
    *,
    now_iso: str,
) -> None:
    """When containment state file(s) already exist on disk, mirror object hand-slot changes cleanly."""
    if day_root is None:
        return
    root = Path(day_root)
    for candidate in (
        root / "phone" / "stance_containment.json",
        root / "life" / "attention_stance_containment.json",
    ):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            doc = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        cdom = doc.get("containment_domain")
        if not isinstance(cdom, Mapping):
            continue
        hmap = cdom.get("hand_slots")
        if not isinstance(hmap, dict):
            continue
        changed = False
        items_list = cdom.get("items") if isinstance(cdom.get("items"), list) else []
        known_item_ids = {
            str(it.get("item_id")) for it in items_list if isinstance(it, Mapping) and it.get("item_id")
        }
        for sname in HAND_SLOT_NAMES:
            slot_info = hand_slots.get(sname) or {}
            if slot_info.get("state") == "holding_object" and slot_info.get("object_id"):
                oid = str(slot_info["object_id"])
                if hmap.get(sname) != oid and oid in known_item_ids:
                    hmap[sname] = oid
                    changed = True
            elif slot_info.get("state") == "free":
                cur_id = hmap.get(sname)
                if cur_id is not None and cur_id not in known_item_ids:
                    hmap[sname] = None
                    changed = True
        if changed:
            doc = dict(doc)
            doc["updated_at"] = now_iso
            encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            fd, tmp_name = tempfile.mkstemp(prefix=f".{candidate.name}.", suffix=".tmp", dir=str(candidate.parent))
            tmp_p = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_p, candidate)
            except BaseException:
                tmp_p.unlink(missing_ok=True)
                raise


def save_state_atomic(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(day_root)
    parent = root / STATE_DIRNAME
    if parent.exists() and parent.is_symlink():
        raise EmbodiedEcologyError(
            "STATE_SYMLINK_REFUSED", "State parent directory may not be a symlink", http_status=503
        )
    path = state_file_path(root)
    if path.exists() and path.is_symlink():
        raise EmbodiedEcologyError(
            "STATE_SYMLINK_REFUSED", "State file may not be a symlink", http_status=503
        )
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(state))
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
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
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return doc


# ---------------------------------------------------------------------------
# Module A: Kitchen Pantry Conservation & Dish Temperature / Consumption
# ---------------------------------------------------------------------------


def apply_restock_pantry(
    state: dict[str, Any],
    *,
    items: Any,
    actor_side: str,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(items, list) or not items or len(items) > 24:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS", "items must be a non-empty list of restock entries", http_status=400
        )
    pantry = state["kitchen"]["pantry"]
    validated: list[dict[str, Any]] = []
    new_ids: set[str] = set()
    for idx, entry in enumerate(items):
        if not isinstance(entry, Mapping):
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", f"items[{idx}] must be an object", http_status=400
            )
        raw_iid = entry.get("ingredient_id")
        if not isinstance(raw_iid, str) or not INGREDIENT_ID_RE.match(raw_iid.strip()):
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", f"Invalid ingredient_id: {raw_iid!r}", http_status=400
            )
        iid = raw_iid.strip()
        units_added = _strict_positive_int(
            entry.get("units_added"), field_name="units_added", maximum=MAX_UNITS_PER_OP
        )
        starter = STARTER_BY_ID.get(iid)
        existing = pantry.get(iid)
        raw_cat = entry.get("category")
        if raw_cat is not None:
            if not isinstance(raw_cat, str) or raw_cat.strip() not in PANTRY_CATEGORIES:
                raise EmbodiedEcologyError(
                    "INVALID_FIELDS",
                    f"category must be one of {list(PANTRY_CATEGORIES)}",
                    http_status=400,
                )
            cat = raw_cat.strip()
        elif existing is not None:
            cat = str(existing.get("category") or "other")
        elif starter is not None:
            cat = starter["category"]
        else:
            cat = "other"

        lbl = (
            _optional_text(entry.get("label"), field_name="label", max_len=60)
            or (str(existing["label"]) if existing is not None else None)
            or (starter["label"] if starter is not None else iid)
        )
        uname = (
            _optional_text(entry.get("unit_name"), field_name="unit_name", max_len=16)
            or (str(existing["unit_name"]) if existing is not None else None)
            or (starter["unit_name"] if starter is not None else "份")
        )
        if iid not in pantry:
            new_ids.add(iid)
        validated.append(
            {
                "ingredient_id": iid,
                "label": lbl,
                "category": cat,
                "unit_name": uname,
                "units_added": units_added,
            }
        )

    if len(pantry) + len(new_ids) > MAX_PANTRY_ITEMS:
        raise EmbodiedEcologyError(
            "PANTRY_CAPACITY_FULL",
            f"Pantry cannot exceed {MAX_PANTRY_ITEMS} distinct ingredient types",
            http_status=409,
        )

    updated_rows: list[dict[str, Any]] = []
    for item in validated:
        iid = item["ingredient_id"]
        added = item["units_added"]
        if iid not in pantry:
            pantry[iid] = {
                "ingredient_id": iid,
                "label": item["label"],
                "category": item["category"],
                "unit_name": item["unit_name"],
                "units": added,
                "total_restocked": added,
                "total_cooked": 0,
                "updated_at": now_iso,
            }
        else:
            row = pantry[iid]
            row["label"] = item["label"]
            row["category"] = item["category"]
            row["unit_name"] = item["unit_name"]
            row["units"] = int(row["units"]) + added
            row["total_restocked"] = int(row["total_restocked"]) + added
            row["updated_at"] = now_iso
        updated_rows.append(copy.deepcopy(pantry[iid]))

    return {
        "restocked_items": updated_rows,
        "actor_side": actor_side,
        "restocked_at": now_iso,
    }


def _normalize_ingredients_used(ingredients_used: Any) -> list[dict[str, Any]]:
    aggregated: dict[str, int] = {}
    if isinstance(ingredients_used, Mapping):
        if not ingredients_used:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", "ingredients_used must not be empty", http_status=400
            )
        for raw_iid, raw_units in ingredients_used.items():
            if not isinstance(raw_iid, str) or not INGREDIENT_ID_RE.match(raw_iid.strip()):
                raise EmbodiedEcologyError(
                    "INVALID_FIELDS", f"Invalid ingredient_id: {raw_iid!r}", http_status=400
                )
            iid = raw_iid.strip()
            units = _strict_positive_int(
                raw_units, field_name=f"ingredients_used.{iid}", maximum=MAX_UNITS_PER_OP
            )
            aggregated[iid] = aggregated.get(iid, 0) + units
    elif isinstance(ingredients_used, list):
        if not ingredients_used:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", "ingredients_used must be a non-empty list", http_status=400
            )
        for idx, entry in enumerate(ingredients_used):
            if not isinstance(entry, Mapping):
                raise EmbodiedEcologyError(
                    "INVALID_FIELDS", f"ingredients_used[{idx}] must be an object", http_status=400
                )
            raw_iid = entry.get("ingredient_id")
            if not isinstance(raw_iid, str) or not INGREDIENT_ID_RE.match(raw_iid.strip()):
                raise EmbodiedEcologyError(
                    "INVALID_FIELDS", f"Invalid ingredient_id: {raw_iid!r}", http_status=400
                )
            iid = raw_iid.strip()
            units = _strict_positive_int(
                entry.get("units_used"), field_name="units_used", maximum=MAX_UNITS_PER_OP
            )
            aggregated[iid] = aggregated.get(iid, 0) + units
    else:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            "ingredients_used must be a non-empty list or object mapping",
            http_status=400,
        )
    return [{"ingredient_id": iid, "units_used": u} for iid, u in aggregated.items()]


def derive_dish_thermal_and_freshness(dish: Mapping[str, Any], now_dt: datetime) -> dict[str, Any]:
    """Pure elapsed-time function: hot -> warm -> cool, refrigerated 48h window & overnight needs_reheat / spoiled."""

    cooked_dt = parse_iso_instant(dish["cooked_at"])
    heated_dt = parse_iso_instant(dish.get("last_heated_at") or dish["cooked_at"])
    age_sec = (now_dt - cooked_dt).total_seconds()
    since_heat_sec = (now_dt - heated_dt).total_seconds()
    serving_kind = str(dish.get("serving_kind") or "meal")
    portions_remaining = int(dish.get("portions_remaining") or 0)
    discarded = dish.get("status") == "discarded"
    is_refrigerated = bool(dish.get("refrigerated"))
    was_refrigerated = bool(dish.get("was_refrigerated")) or is_refrigerated

    if since_heat_sec < 0 or age_sec < 0:
        return {
            "temperature_band": "unknown",
            "temperature_curve": list(TEMPERATURE_CURVE),
            "freshness_state": "unknown",
            "refrigerated": is_refrigerated,
            "needs_reheat": False,
            "spoiled": False,
            "edible_now": False,
            "reason_code": "CLOCK_ROLLBACK",
            "elapsed_since_heat_seconds": round(since_heat_sec, 3),
            "age_seconds": round(age_sec, 3),
        }

    if is_refrigerated:
        temp_band = "cool"
    elif since_heat_sec < HOT_TO_WARM_SECONDS:
        temp_band = "hot"
    elif since_heat_sec < WARM_TO_COOL_SECONDS:
        temp_band = "warm"
    else:
        temp_band = "cool"

    if serving_kind == "drink":
        spoil_limit = DRINK_SPOIL_SECONDS
    elif was_refrigerated:
        spoil_limit = REFRIGERATED_MEAL_SPOIL_SECONDS
    else:
        spoil_limit = MEAL_SPOIL_SECONDS

    if discarded:
        freshness = "discarded"
        spoiled = False
        needs_reheat = False
        edible = False
    elif age_sec >= spoil_limit:
        freshness = "spoiled"
        spoiled = True
        needs_reheat = False
        edible = False
    elif is_refrigerated and serving_kind == "meal":
        freshness = "refrigerated_needs_reheat"
        spoiled = False
        needs_reheat = True
        edible = False
    elif (
        serving_kind == "meal"
        and age_sec >= OVERNIGHT_REHEAT_SECONDS
        and since_heat_sec >= REHEAT_FRESH_WINDOW_SECONDS
    ):
        freshness = "overnight_needs_reheat"
        spoiled = False
        needs_reheat = True
        edible = False
    else:
        freshness = "reheated_fresh" if dish.get("reheated_count", 0) > 0 else "fresh"
        spoiled = False
        needs_reheat = False
        edible = portions_remaining > 0

    return {
        "temperature_band": temp_band,
        "temperature_curve": list(TEMPERATURE_CURVE),
        "freshness_state": freshness,
        "refrigerated": is_refrigerated,
        "needs_reheat": needs_reheat,
        "spoiled": spoiled,
        "edible_now": edible,
        "reason_code": None,
        "elapsed_since_heat_seconds": round(since_heat_sec, 3),
        "age_seconds": round(age_sec, 3),
    }


def _send_dish_to_sink_once(state: dict[str, Any], target: dict[str, Any]) -> int:
    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    sink = zones.setdefault(
        "sink",
        {"zone_id": "sink", "label": "水槽", "visibility": "shared", "dirty_dishes": 0, "dirty_items": []},
    )
    prep = zones.get("prep_counter")
    fridge = zones.get("fridge")
    did = str(target["dish_id"])
    if isinstance(prep, dict) and isinstance(prep.get("plated_dish_ids"), list):
        prep["plated_dish_ids"] = [x for x in prep["plated_dish_ids"] if x != did]
    if isinstance(fridge, dict) and isinstance(fridge.get("stored_dish_ids"), list):
        fridge["stored_dish_ids"] = [x for x in fridge["stored_dish_ids"] if x != did]
    if not target.get("dish_sent_to_sink"):
        target["dish_sent_to_sink"] = True
        sink["dirty_dishes"] = int(sink.get("dirty_dishes") or 0) + 1
        dirty_items = sink.setdefault("dirty_items", [])
        if did not in dirty_items:
            dirty_items.append(did)
    return int(sink.get("dirty_dishes") or 0)


def apply_cook_or_brew(
    state: dict[str, Any],
    *,
    title: Any,
    serving_kind: Any,
    ingredients_used: Any,
    portions: Any,
    container_id: Any = None,
    cup_occupied_by_stationery: bool = False,
    now_iso: str,
) -> dict[str, Any]:
    clean_title = _clean_text(title, field_name="title", max_len=60)
    if not isinstance(serving_kind, str) or serving_kind not in SERVING_KINDS:
        raise EmbodiedEcologyError(
            "INVALID_SERVING_KIND",
            f"serving_kind must be one of {list(SERVING_KINDS)}",
            http_status=400,
        )
    clean_cid = (
        _require_id_token(container_id, field_name="container_id", reason_code="INVALID_FIELDS")
        if container_id is not None
        else ("ceramic_mug" if serving_kind == "drink" else None)
    )
    if cup_occupied_by_stationery:
        raise EmbodiedEcologyError(
            "CUP_OCCUPIED_BY_STATIONERY",
            f"Container {clean_cid or 'ceramic_mug'!r} holds stationery/pens; remove pens before pouring or brewing",
            http_status=409,
        )

    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    sink = zones.get("sink") or {}
    if int(sink.get("dirty_dishes") or 0) >= SINK_DIRTY_DISHES_LIMIT:
        raise EmbodiedEcologyError(
            "SINK_DIRTY_DISHES_FULL",
            f"Sink has {sink.get('dirty_dishes')} dirty dishes (>= {SINK_DIRTY_DISHES_LIMIT}); call wash_dishes before plating new food or drinks",
            http_status=409,
        )

    portions_int = (
        1
        if portions is None
        else _strict_positive_int(portions, field_name="portions", maximum=MAX_PORTIONS_PER_COOK)
    )
    normalized_ing = _normalize_ingredients_used(ingredients_used)

    pantry = state["kitchen"]["pantry"]
    shortages: list[dict[str, Any]] = []
    for req in normalized_ing:
        iid = req["ingredient_id"]
        needed = req["units_used"]
        available = int(pantry[iid]["units"]) if iid in pantry else 0
        if available < needed:
            shortages.append(
                {
                    "ingredient_id": iid,
                    "needed_units": needed,
                    "available_units": available,
                }
            )
    if shortages:
        raise EmbodiedEcologyError(
            "INSUFFICIENT_PANTRY_STOCK",
            f"Insufficient pantry stock for {shortages[0]['ingredient_id']}: "
            f"need {shortages[0]['needed_units']}, have {shortages[0]['available_units']}",
            http_status=409,
        )

    dishes = state["kitchen"]["cooked_dishes"]
    if len(dishes) >= MAX_COOKED_DISHES:
        dishes[:] = [d for d in dishes if int(d.get("portions_remaining") or 0) > 0][-(MAX_COOKED_DISHES - 1):]

    for req in normalized_ing:
        iid = req["ingredient_id"]
        needed = req["units_used"]
        pantry[iid]["units"] = int(pantry[iid]["units"]) - needed
        pantry[iid]["total_cooked"] = int(pantry[iid]["total_cooked"]) + needed
        pantry[iid]["updated_at"] = now_iso

    appliances = state["kitchen"].setdefault("appliances", _default_kitchen_appliances())
    if serving_kind == "drink":
        kettle = appliances.setdefault("electric_kettle", _default_kitchen_appliances()["electric_kettle"])
        kettle["state"] = "boiled"
        kettle["water_temp_band"] = "hot"
        kettle["boil_count"] = int(kettle.get("boil_count") or 0) + 1
        kettle["last_boiled_at"] = now_iso
    else:
        stove = appliances.setdefault("stove", _default_kitchen_appliances()["stove"])
        stove["state"] = "simmered_then_off"
        stove["cook_count"] = int(stove.get("cook_count") or 0) + 1
        stove["last_used_at"] = now_iso

    seq = int(state.get("revision") or 0) + 1
    dish_id = _stable_id("dish", clean_title, serving_kind, now_iso, seq)
    new_dish = {
        "dish_id": dish_id,
        "title": clean_title,
        "serving_kind": serving_kind,
        "container_id": clean_cid,
        "portions_created": portions_int,
        "portions_remaining": portions_int,
        "ingredients_used": normalized_ing,
        "cooked_at": now_iso,
        "last_heated_at": now_iso,
        "reheated_count": 0,
        "refrigerated": False,
        "was_refrigerated": False,
        "refrigerated_at": None,
        "dish_sent_to_sink": False,
        "status": "ready",
    }
    dishes.append(new_dish)
    prep = zones.setdefault("prep_counter", _default_kitchen_zones()["prep_counter"])
    prep.setdefault("plated_dish_ids", []).append(dish_id)
    now_dt = parse_iso_instant(now_iso)
    derived = derive_dish_thermal_and_freshness(new_dish, now_dt)
    return {"dish": {**copy.deepcopy(new_dish), **derived}}


def apply_refrigerate_dish(
    state: dict[str, Any],
    *,
    dish_id: Any,
    now_iso: str,
) -> dict[str, Any]:
    """Ch. 18: Store a cooled meal (e.g. onigiri) into the fridge (safe up to 48h, covering 36h slice)."""
    clean_id = _require_id_token(dish_id, field_name="dish_id", reason_code="DISH_NOT_FOUND")
    dishes = state["kitchen"]["cooked_dishes"]
    target = next((d for d in dishes if d.get("dish_id") == clean_id), None)
    if target is None:
        raise EmbodiedEcologyError("DISH_NOT_FOUND", f"Dish {clean_id} not found", http_status=404)
    if int(target.get("portions_remaining") or 0) <= 0 or target.get("status") in ("empty", "discarded"):
        raise EmbodiedEcologyError("DISH_EMPTY", "Dish has no portions remaining", http_status=409)
    now_dt = parse_iso_instant(now_iso)
    thermal = derive_dish_thermal_and_freshness(target, now_dt)
    if thermal["spoiled"]:
        raise EmbodiedEcologyError("DISH_SPOILED", "Spoiled dish cannot be refrigerated", http_status=409)
    if thermal["temperature_band"] == "hot":
        raise EmbodiedEcologyError(
            "DISH_TOO_HOT_FOR_FRIDGE",
            "Steaming hot dish must cool before placing into the fridge",
            http_status=409,
        )
    target["refrigerated"] = True
    target["was_refrigerated"] = True
    target["refrigerated_at"] = now_iso
    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    fridge = zones.setdefault("fridge", _default_kitchen_zones()["fridge"])
    if clean_id not in fridge.setdefault("stored_dish_ids", []):
        fridge["stored_dish_ids"].append(clean_id)
    prep = zones.get("prep_counter")
    if isinstance(prep, dict) and isinstance(prep.get("plated_dish_ids"), list):
        prep["plated_dish_ids"] = [x for x in prep["plated_dish_ids"] if x != clean_id]
    updated_thermal = derive_dish_thermal_and_freshness(target, now_dt)
    return {"dish": {**copy.deepcopy(target), **updated_thermal}}


def apply_reheat_dish(
    state: dict[str, Any],
    *,
    dish_id: Any,
    now_iso: str,
) -> dict[str, Any]:
    clean_id = _require_id_token(dish_id, field_name="dish_id", reason_code="DISH_NOT_FOUND")
    dishes = state["kitchen"]["cooked_dishes"]
    target = next((d for d in dishes if d.get("dish_id") == clean_id), None)
    if target is None:
        raise EmbodiedEcologyError("DISH_NOT_FOUND", f"Dish {clean_id} not found", http_status=404)
    if target.get("status") == "discarded" or clean_id in state["kitchen"].get("discarded_tombstones", {}):
        raise EmbodiedEcologyError("DISH_ALREADY_DISCARDED", "Discarded dish cannot be reheated", http_status=409)
    if int(target.get("portions_remaining") or 0) <= 0:
        raise EmbodiedEcologyError("DISH_EMPTY", "Dish has no portions remaining", http_status=409)
    now_dt = parse_iso_instant(now_iso)
    thermal = derive_dish_thermal_and_freshness(target, now_dt)
    if thermal["spoiled"]:
        raise EmbodiedEcologyError(
            "DISH_SPOILED", "Spoiled dish cannot be reheated; discard instead", http_status=409
        )
    if target.get("refrigerated"):
        target["was_refrigerated"] = True
        target["refrigerated"] = False
        zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
        fridge = zones.get("fridge")
        if isinstance(fridge, dict) and isinstance(fridge.get("stored_dish_ids"), list):
            fridge["stored_dish_ids"] = [x for x in fridge["stored_dish_ids"] if x != clean_id]
        prep = zones.setdefault("prep_counter", _default_kitchen_zones()["prep_counter"])
        if clean_id not in prep.setdefault("plated_dish_ids", []):
            prep["plated_dish_ids"].append(clean_id)
    appliances = state["kitchen"].setdefault("appliances", _default_kitchen_appliances())
    mw = appliances.setdefault("microwave", _default_kitchen_appliances()["microwave"])
    mw["state"] = "warm_idle"
    mw["reheat_count"] = int(mw.get("reheat_count") or 0) + 1
    mw["last_used_at"] = now_iso

    target["last_heated_at"] = now_iso
    target["reheated_count"] = int(target.get("reheated_count") or 0) + 1
    updated_thermal = derive_dish_thermal_and_freshness(target, now_dt)
    return {"dish": {**copy.deepcopy(target), **updated_thermal}}


def apply_consume_dish(
    state: dict[str, Any],
    *,
    dish_id: Any,
    now_iso: str,
) -> dict[str, Any]:
    clean_id = _require_id_token(dish_id, field_name="dish_id", reason_code="DISH_NOT_FOUND")
    dishes = state["kitchen"]["cooked_dishes"]
    target = next((d for d in dishes if d.get("dish_id") == clean_id), None)
    if target is None:
        raise EmbodiedEcologyError("DISH_NOT_FOUND", f"Dish {clean_id} not found", http_status=404)
    if target.get("status") == "discarded" or clean_id in state["kitchen"].get("discarded_tombstones", {}):
        raise EmbodiedEcologyError(
            "DISH_ALREADY_DISCARDED", "Discarded dish cannot be resurrected or consumed", http_status=409
        )
    remaining = int(target.get("portions_remaining") or 0)
    if remaining <= 0 or target.get("status") == "empty":
        raise EmbodiedEcologyError(
            "DISH_EMPTY", "Dish has 0 portions remaining and cannot be consumed again", http_status=409
        )
    now_dt = parse_iso_instant(now_iso)
    thermal = derive_dish_thermal_and_freshness(target, now_dt)
    if thermal["spoiled"]:
        raise EmbodiedEcologyError(
            "DISH_SPOILED", "Dish has spoiled overnight and cannot be consumed", http_status=409
        )
    if thermal["needs_reheat"]:
        raise EmbodiedEcologyError(
            "DISH_NEEDS_REHEAT", "Cold/refrigerated/overnight meal must be reheated before consumption", http_status=409
        )

    new_remaining = remaining - 1
    target["portions_remaining"] = new_remaining
    dirty_after = int((state["kitchen"].get("zones") or {}).get("sink", {}).get("dirty_dishes") or 0)
    if new_remaining == 0:
        target["status"] = "empty"
        dirty_after = _send_dish_to_sink_once(state, target)

    serving_kind = str(target["serving_kind"])
    temp_band = str(thermal["temperature_band"])
    if serving_kind == "drink":
        relief = {"thirst": 0.55 if temp_band in ("hot", "warm") else 0.45, "hunger": 0.0}
    else:
        relief = {"hunger": 0.60, "thirst": 0.10}

    amount_mg = int(DEFAULT_PORTION_AMOUNT_MG.get(serving_kind, 220_000))
    energy_mJ = int(DEFAULT_PORTION_ENERGY_MJ.get(serving_kind, 1_500_000_000))

    seq = int(state.get("revision") or 0) + 1
    receipt_id = _stable_id("cons", clean_id, new_remaining, now_iso, seq)
    receipt = dict(
        ConsumeReceipt(
            receipt_id=receipt_id,
            dish_id=clean_id,
            title=target["title"],
            serving_kind=serving_kind,
            portions_consumed=1,
            portions_remaining_after=new_remaining,
            amount_mg=amount_mg,
            energy_mJ=energy_mJ,
            temperature_at_consume=temp_band,
            lived_need_relief=relief,
            recorded_as_intake=True,
            sink_dirty_dishes_after=dirty_after,
            consumed_at=now_iso,
        )
    )
    receipts = state["kitchen"]["consumption_receipts"]
    receipts.append(receipt)
    if len(receipts) > MAX_CONSUMPTION_RECEIPTS:
        del receipts[:-MAX_CONSUMPTION_RECEIPTS]

    updated_thermal = derive_dish_thermal_and_freshness(target, now_dt)
    return {
        "dish": {**copy.deepcopy(target), **updated_thermal},
        "consumption_receipt": receipt,
    }


def apply_discard_dish(
    state: dict[str, Any],
    *,
    dish_id: Any,
    reason: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    clean_id = _require_id_token(dish_id, field_name="dish_id", reason_code="DISH_NOT_FOUND")
    clean_reason = _optional_text(reason, field_name="reason", max_len=80) or "cleanup_or_spoiled"
    dishes = state["kitchen"]["cooked_dishes"]
    target = next((d for d in dishes if d.get("dish_id") == clean_id), None)
    if target is None:
        raise EmbodiedEcologyError("DISH_NOT_FOUND", f"Dish {clean_id} not found", http_status=404)
    tombstones = state["kitchen"].setdefault("discarded_tombstones", {})
    if target.get("status") == "discarded" or clean_id in tombstones:
        raise EmbodiedEcologyError(
            "DISH_ALREADY_DISCARDED", "Dish has already been discarded (anti-resurrection guard)", http_status=409
        )
    discarded_portions = int(target.get("portions_remaining") or 0)
    serving_kind = str(target.get("serving_kind") or "meal")
    discarded_mg = discarded_portions * int(DEFAULT_PORTION_AMOUNT_MG.get(serving_kind, 220_000))
    target["portions_remaining"] = 0
    target["refrigerated"] = False
    target["status"] = "discarded"
    dirty_after = _send_dish_to_sink_once(state, target)
    seq = int(state.get("revision") or 0) + 1
    discard_id = _stable_id("disc", clean_id, now_iso, seq)
    tombstone_token = _stable_id("tomb", clean_id, discard_id)
    tombstones[clean_id] = {
        "dish_id": clean_id,
        "discard_id": discard_id,
        "anti_resurrection_token": tombstone_token,
        "discarded_at": now_iso,
    }
    discard_receipt = dict(
        DiscardReceipt(
            discard_id=discard_id,
            dish_id=clean_id,
            title=target["title"],
            discarded_portions=discarded_portions,
            amount_mg=discarded_mg,
            reason=clean_reason,
            anti_resurrection_token=tombstone_token,
            sink_dirty_dishes_after=dirty_after,
            discarded_at=now_iso,
        )
    )
    discards = state["kitchen"]["discard_receipts"]
    discards.append(discard_receipt)
    if len(discards) > MAX_DISCARD_RECEIPTS:
        del discards[:-MAX_DISCARD_RECEIPTS]
    return {"discard_receipt": discard_receipt}


def apply_wash_dishes(state: dict[str, Any], *, actor_side: str, now_iso: str) -> dict[str, Any]:
    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    sink = zones.setdefault("sink", _default_kitchen_zones()["sink"])
    washed_count = int(sink.get("dirty_dishes") or 0)
    washed_ids = list(sink.get("dirty_items") or [])
    sink["dirty_dishes"] = 0
    sink["dirty_items"] = []
    sink["last_washed_at"] = now_iso
    return {
        "washed_count": washed_count,
        "washed_dish_ids": washed_ids,
        "dirty_dishes_after": 0,
        "washed_by": actor_side,
        "washed_at": now_iso,
    }


def apply_inspect_kitchen_zone(
    state: dict[str, Any],
    *,
    zone_id: Any,
    actor_side: str,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(zone_id, str) or zone_id.strip() not in KITCHEN_ZONE_IDS:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"zone_id must be one of {list(KITCHEN_ZONE_IDS)}",
            http_status=400,
        )
    zid = zone_id.strip()
    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    zone = zones[zid]
    if zid == "chiyo_snack_drawer":
        if actor_side != "subject" and not bool(zone.get("partner_authorized")):
            raise EmbodiedEcologyError(
                "PRIVATE_REFUSED",
                "Private snack drawer cannot be opened by partner without subject permission",
                http_status=403,
            )
    if "open_state" in zone:
        zone["open_state"] = "open"
    return {"zone": copy.deepcopy(zone), "inspected_by": actor_side, "inspected_at": now_iso}


def apply_set_snack_drawer_permission(
    state: dict[str, Any],
    *,
    partner_authorized: Any,
    now_iso: str,
) -> dict[str, Any]:
    auth_flag = _require_bool(partner_authorized, field_name="partner_authorized")
    zones = state["kitchen"].setdefault("zones", _default_kitchen_zones())
    drawer = zones["chiyo_snack_drawer"]
    drawer["partner_authorized"] = auth_flag
    drawer["updated_at"] = now_iso
    return {"zone_id": "chiyo_snack_drawer", "partner_authorized": auth_flag, "updated_at": now_iso}


def apply_operate_kitchen_appliance(
    state: dict[str, Any],
    *,
    appliance_id: Any,
    operation: Any,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(appliance_id, str) or appliance_id.strip() not in KITCHEN_APPLIANCE_IDS:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"appliance_id must be one of {list(KITCHEN_APPLIANCE_IDS)}",
            http_status=400,
        )
    aid = appliance_id.strip()
    op = _clean_text(operation, field_name="operation", max_len=32)
    if op not in ("boil_kettle", "heat_microwave", "turn_on_stove", "turn_off"):
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            "operation must be boil_kettle, heat_microwave, turn_on_stove, or turn_off",
            http_status=400,
        )
    appliances = state["kitchen"].setdefault("appliances", _default_kitchen_appliances())
    app = appliances[aid]
    if op == "boil_kettle" and aid == "electric_kettle":
        app["state"] = "boiled"
        app["water_temp_band"] = "hot"
        app["boil_count"] = int(app.get("boil_count") or 0) + 1
        app["last_boiled_at"] = now_iso
    elif op == "heat_microwave" and aid == "microwave":
        app["state"] = "heating"
        app["reheat_count"] = int(app.get("reheat_count") or 0) + 1
        app["last_used_at"] = now_iso
    elif op == "turn_on_stove" and aid == "stove":
        app["state"] = "simmering"
        app["cook_count"] = int(app.get("cook_count") or 0) + 1
        app["last_used_at"] = now_iso
    elif op == "turn_off":
        app["state"] = "off" if aid == "stove" else "idle"
    else:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"Operation {op!r} does not match appliance {aid!r}",
            http_status=400,
        )
    return {"appliance": copy.deepcopy(app)}


# ---------------------------------------------------------------------------
# Module B: Partner Co-Presence, Hand-Slot Physics & Everyday Contact
# ---------------------------------------------------------------------------


def _release_contact_hand_slots(hand_slots: dict[str, dict[str, Any]], now_iso: str) -> list[str]:
    freed: list[str] = []
    for sname in HAND_SLOT_NAMES:
        slot = hand_slots[sname]
        if slot.get("state") == "contact_reserved":
            hand_slots[sname] = {
                **_free_hand_slot(),
                "updated_at": now_iso,
            }
            freed.append(sname)
    return freed


def _default_pose_for_presence(presence_mode: str) -> str:
    if presence_mode == "bed_side_user":
        return "lying_side"
    if presence_mode in ("beside_at_desk", "beside_on_sofa"):
        return "sitting"
    return "standing"


def _effective_hand_slots_with_external(
    hand_slots: Mapping[str, Mapping[str, Any]],
    external_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    merged = {sname: copy.deepcopy(dict(hand_slots[sname])) for sname in HAND_SLOT_NAMES}
    if external_locks:
        for sname in HAND_SLOT_NAMES:
            if merged[sname].get("state") == "free" and sname in external_locks:
                merged[sname] = copy.deepcopy(dict(external_locks[sname]))
    return merged


def derive_contact_avatar_and_bed_readonly(
    c_dom: Mapping[str, Any],
    now_dt: datetime,
    *,
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Pure zero-write derivation for avatar expiry, 1800s contact natural release & 12-min bed warmth cooling."""
    raw_presence = str(c_dom.get("partner_presence") or "absent")
    raw_avatar = dict(c_dom.get("user_avatar_proxy") or _default_user_avatar_proxy())
    expires_raw = raw_avatar.get("expires_at")
    avatar_expired = False
    if raw_presence != "absent" and isinstance(expires_raw, str) and expires_raw.strip():
        exp_dt = parse_iso_instant(expires_raw)
        if now_dt >= exp_dt:
            avatar_expired = True

    eff_presence = "absent" if avatar_expired else raw_presence
    avatar_out = dict(
        UserAvatarProxy(
            presence_mode=eff_presence,
            zone=eff_presence,
            pose="standing" if eff_presence == "absent" else str(raw_avatar.get("pose") or _default_pose_for_presence(eff_presence)),
            within_reach=eff_presence in REACHABLE_PRESENCE_MODES,
            updated_at=raw_avatar.get("updated_at") or c_dom.get("presence_updated_at"),
            expires_at=expires_raw,
            ttl_seconds=int(raw_avatar.get("ttl_seconds") or AVATAR_DEFAULT_TTL_SECONDS),
            expired=avatar_expired,
        )
    )

    hand_slots = _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks)
    active_contact = copy.deepcopy(c_dom.get("active_contact"))
    pending_proposal = copy.deepcopy(c_dom.get("pending_proposal"))
    last_event = copy.deepcopy(c_dom.get("last_contact_event"))
    contact_timed_out = False

    if avatar_expired:
        for sname in HAND_SLOT_NAMES:
            if hand_slots[sname].get("state") == "contact_reserved":
                hand_slots[sname] = _free_hand_slot()
        if active_contact is not None or pending_proposal is not None:
            last_event = {
                "event": "released_on_avatar_timeout",
                "affection_penalty": 0,
                "at": format_iso(now_dt),
            }
        active_contact = None
        pending_proposal = None
    elif isinstance(active_contact, Mapping):
        started_dt = parse_iso_instant(active_contact["started_at"])
        elapsed_contact = (now_dt - started_dt).total_seconds()
        max_dur = int(active_contact.get("max_duration_seconds") or CONTACT_MAX_DURATION_SECONDS)
        if elapsed_contact >= max_dur:
            contact_timed_out = True
            for sname in HAND_SLOT_NAMES:
                if hand_slots[sname].get("state") == "contact_reserved":
                    hand_slots[sname] = _free_hand_slot()
            last_event = {
                "event": "natural_release_timeout",
                "previous_contact_kind": active_contact.get("contact_kind"),
                "elapsed_seconds": round(elapsed_contact, 3),
                "max_duration_seconds": max_dur,
                "affection_penalty": 0,
                "exit_debt": False,
                "at": format_iso(started_dt + timedelta(seconds=max_dur)),
            }
            active_contact = None
        else:
            active_contact["elapsed_seconds"] = round(max(0.0, elapsed_contact), 3)
            active_contact["remaining_seconds"] = round(max(0.0, max_dur - elapsed_contact), 3)
            active_contact["max_duration_seconds"] = max_dur

    raw_bed = dict(c_dom.get("bed_physics") or _default_bed_physics())
    rumple = max(0.0, min(1.0, float(raw_bed.get("rumple_index") or 0.0)))
    occupied_bed = eff_presence == "bed_side_user"
    left_bed_at = raw_bed.get("left_bed_at")
    warmth_dep = float(raw_bed.get("warmth_at_departure") or 0.0)
    if avatar_expired and raw_presence == "bed_side_user" and isinstance(expires_raw, str):
        left_bed_at = expires_raw
        warmth_dep = max(warmth_dep, 1.0)

    if occupied_bed:
        residual_warmth = 1.0
        cooling_elapsed = 0.0
    elif isinstance(left_bed_at, str) and left_bed_at.strip() and warmth_dep > 0.0:
        left_dt = parse_iso_instant(left_bed_at)
        cooling_elapsed = max(0.0, (now_dt - left_dt).total_seconds())
        residual_warmth = round(warmth_dep * (0.5 ** (cooling_elapsed / float(BED_WARMTH_HALF_LIFE_SECONDS))), 6)
    else:
        residual_warmth = 0.0
        cooling_elapsed = 0.0

    bed_out = {
        "occupied_by_partner": occupied_bed,
        "rumple_index": round(rumple, 4),
        "residual_warmth": residual_warmth,
        "warmth_at_departure": round(warmth_dep, 4),
        "left_bed_at": left_bed_at,
        "cooling_elapsed_seconds": round(cooling_elapsed, 3),
        "half_life_seconds": BED_WARMTH_HALF_LIFE_SECONDS,
        "last_tidied_at": raw_bed.get("last_tidied_at"),
    }

    cps_raw = c_dom.get("copresence_session")
    cps_out = None
    if eff_presence != "absent" and isinstance(cps_raw, Mapping):
        cps_out = copy.deepcopy(dict(cps_raw))

    return {
        "effective_presence": eff_presence,
        "avatar_expired": avatar_expired,
        "contact_timed_out": contact_timed_out,
        "user_avatar_proxy": avatar_out,
        "bed_physics": bed_out,
        "hand_slots": hand_slots,
        "active_contact": active_contact,
        "pending_proposal": pending_proposal,
        "last_contact_event": last_event,
        "copresence_session": cps_out,
    }


def settle_elapsed_contact_and_avatar(state: dict[str, Any], *, now_iso: str) -> dict[str, bool]:
    """Settle avatar timeout or 1800s active contact natural release prior to mutating state."""
    now_dt = parse_iso_instant(now_iso)
    c_dom = state["embodied_contact"]
    derived = derive_contact_avatar_and_bed_readonly(c_dom, now_dt)
    settled_avatar = False
    settled_contact = False
    if derived["avatar_expired"]:
        settled_avatar = True
        was_bed = c_dom.get("partner_presence") == "bed_side_user"
        exp_iso = (c_dom.get("user_avatar_proxy") or {}).get("expires_at") or now_iso
        if was_bed:
            bed = c_dom.setdefault("bed_physics", _default_bed_physics())
            bed["occupied_by_partner"] = False
            bed["warmth_at_departure"] = 1.0
            bed["left_bed_at"] = exp_iso
        _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
        c_dom["partner_presence"] = "absent"
        c_dom["copresence_session"] = None
        c_dom["user_avatar_proxy"] = _default_user_avatar_proxy()
        c_dom["active_contact"] = None
        c_dom["pending_proposal"] = None
        c_dom["last_contact_event"] = derived["last_contact_event"]
    elif derived["contact_timed_out"]:
        settled_contact = True
        _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
        c_dom["active_contact"] = None
        c_dom["last_contact_event"] = derived["last_contact_event"]
    return {"avatar_expired_settled": settled_avatar, "contact_timeout_settled": settled_contact}


def apply_set_partner_presence(
    state: dict[str, Any],
    *,
    presence_mode: Any,
    pose: Any = None,
    ttl_seconds: Any = None,
    expires_at: Any = None,
    subject_foreground_activity: Any = None,
    actor_side: str,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(presence_mode, str) or presence_mode not in PARTNER_PRESENCE_MODES:
        raise EmbodiedEcologyError(
            "INVALID_PRESENCE_MODE",
            f"presence_mode must be one of {list(PARTNER_PRESENCE_MODES)}",
            http_status=400,
        )
    resolved_pose = _default_pose_for_presence(presence_mode) if pose is None else pose
    if not isinstance(resolved_pose, str) or resolved_pose not in AVATAR_POSES:
        raise EmbodiedEcologyError(
            "INVALID_AVATAR_POSE",
            f"pose must be one of {list(AVATAR_POSES)}",
            http_status=400,
        )
    now_dt = parse_iso_instant(now_iso)
    ttl_int = AVATAR_DEFAULT_TTL_SECONDS
    if ttl_seconds is not None:
        ttl_int = _strict_positive_int(ttl_seconds, field_name="ttl_seconds", maximum=86400)
    if expires_at is not None:
        exp_dt = parse_iso_instant(expires_at)
        if exp_dt <= now_dt:
            raise EmbodiedEcologyError(
                "INVALID_TIMESTAMP", "expires_at must be strictly after current time", http_status=400
            )
        resolved_expires_iso: Optional[str] = format_iso(exp_dt)
    elif presence_mode == "absent":
        resolved_expires_iso = None
    else:
        resolved_expires_iso = format_iso(now_dt + timedelta(seconds=ttl_int))

    c_dom = state["embodied_contact"]
    prev_presence = str(c_dom.get("partner_presence") or "absent")
    bed = c_dom.setdefault("bed_physics", _default_bed_physics())

    if prev_presence == "bed_side_user" and presence_mode != "bed_side_user":
        bed["occupied_by_partner"] = False
        bed["warmth_at_departure"] = 1.0
        bed["left_bed_at"] = now_iso
    elif presence_mode == "bed_side_user":
        bed["occupied_by_partner"] = True
        rumple_bump = 0.35 if resolved_pose == "lying_side" else 0.20
        bed["rumple_index"] = round(min(1.0, float(bed.get("rumple_index") or 0.0) + rumple_bump), 4)
        bed["warmth_at_departure"] = 1.0

    if subject_foreground_activity is not None and actor_side == "subject":
        c_dom["subject_foreground_activity"] = _clean_text(
            subject_foreground_activity, field_name="subject_foreground_activity", max_len=80
        )

    c_dom["partner_presence"] = presence_mode
    c_dom["presence_updated_at"] = now_iso
    c_dom["foreground_activity_interrupted"] = False
    c_dom["user_avatar_proxy"] = dict(
        UserAvatarProxy(
            presence_mode=presence_mode,
            zone=presence_mode,
            pose="standing" if presence_mode == "absent" else resolved_pose,
            within_reach=presence_mode in REACHABLE_PRESENCE_MODES,
            updated_at=now_iso,
            expires_at=resolved_expires_iso,
            ttl_seconds=ttl_int,
        )
    )

    exited_copresence = prev_presence != "absent" and presence_mode == "absent"
    if presence_mode == "absent":
        c_dom["copresence_session"] = None
    else:
        existing_cps = c_dom.get("copresence_session")
        if not isinstance(existing_cps, Mapping) or not is_valid_cps_session_id(existing_cps.get("session_id")):
            seq = int(state.get("revision") or 0) + 1
            c_dom["copresence_session"] = {
                "session_id": _cps_session_id(presence_mode, actor_side, now_iso, seq),
                "started_at": now_iso,
                "presence_mode": presence_mode,
                "active": True,
            }
        else:
            c_dom["copresence_session"]["presence_mode"] = presence_mode
            c_dom["copresence_session"]["updated_at"] = now_iso

    freed_slots: list[str] = []
    auto_released = False
    if presence_mode not in REACHABLE_PRESENCE_MODES:
        if c_dom.get("active_contact") is not None or c_dom.get("pending_proposal") is not None:
            freed_slots = _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
            c_dom["active_contact"] = None
            c_dom["pending_proposal"] = None
            auto_released = True
            c_dom["last_contact_event"] = {
                "event": "released_on_distance_change",
                "released_by": actor_side,
                "freed_hand_slots": freed_slots,
                "affection_penalty": 0,
                "at": now_iso,
            }

    return {
        "partner_presence": presence_mode,
        "within_reach": presence_mode in REACHABLE_PRESENCE_MODES,
        "user_avatar_proxy": copy.deepcopy(c_dom["user_avatar_proxy"]),
        "copresence_session": copy.deepcopy(c_dom.get("copresence_session")),
        "exited_copresence": exited_copresence,
        "subject_foreground_activity": c_dom["subject_foreground_activity"],
        "foreground_activity_interrupted": False,
        "spawns_second_foreground": False,
        "contact_auto_released": auto_released,
        "freed_hand_slots": freed_slots,
    }


def apply_exit_copresence(state: dict[str, Any], *, actor_side: str, now_iso: str) -> dict[str, Any]:
    res = apply_set_partner_presence(
        state,
        presence_mode="absent",
        actor_side=actor_side,
        now_iso=now_iso,
    )
    res["exited_copresence"] = True
    return res


def apply_tidy_bed(state: dict[str, Any], *, actor_side: str, now_iso: str) -> dict[str, Any]:
    bed = state["embodied_contact"].setdefault("bed_physics", _default_bed_physics())
    prev_rumple = float(bed.get("rumple_index") or 0.0)
    bed["rumple_index"] = 0.0
    bed["last_tidied_at"] = now_iso
    return {
        "previous_rumple_index": round(prev_rumple, 4),
        "rumple_index": 0.0,
        "tidied_by": actor_side,
        "tidied_at": now_iso,
    }


def apply_set_hand_slot(
    state: dict[str, Any],
    *,
    slot: Any,
    object_id: Any = None,
    label: Any = None,
    surface_id: Any = None,
    requires_two_hands: Any = False,
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(slot, str) or slot not in HAND_SLOT_INPUT_TARGETS:
        raise EmbodiedEcologyError(
            "INVALID_HAND_SLOT",
            f"slot must be one of {list(HAND_SLOT_INPUT_TARGETS)}",
            http_status=400,
        )
    two_hands_flag = _require_bool(
        False if requires_two_hands is None else requires_two_hands,
        field_name="requires_two_hands",
    )
    target_both = slot == "both_hands" or two_hands_flag
    c_dom = state["embodied_contact"]
    hand_slots = c_dom["hand_slots"]
    effective_slots = _effective_hand_slots_with_external(hand_slots, external_hand_locks)
    target_surface = _optional_text(surface_id, field_name="surface_id", max_len=48) or "desk"

    # Put down / clear held object
    if object_id is None:
        slots_to_clear = list(HAND_SLOT_NAMES) if slot == "both_hands" else [slot]
        for sname in slots_to_clear:
            if hand_slots[sname]["state"] == "contact_reserved":
                raise EmbodiedEcologyError(
                    "HAND_SLOT_OCCUPIED",
                    f"{sname} is currently in physical contact; call release_contact first",
                    http_status=409,
                )
        cleared_objects: list[str] = []
        for sname in list(slots_to_clear):
            cur = hand_slots[sname]
            if cur["state"] == "holding_object" and cur.get("object_id"):
                oid = str(cur["object_id"])
                was_two_handed = bool(cur.get("two_handed"))
                c_dom["placed_down_objects"][oid] = {
                    "object_id": oid,
                    "label": cur.get("label") or oid,
                    "surface_id": target_surface,
                    "placed_at": now_iso,
                }
                if oid not in cleared_objects:
                    cleared_objects.append(oid)
                hand_slots[sname] = {**_free_hand_slot(), "updated_at": now_iso}
                if was_two_handed:
                    for other_name in HAND_SLOT_NAMES:
                        if hand_slots[other_name].get("object_id") == oid:
                            hand_slots[other_name] = {**_free_hand_slot(), "updated_at": now_iso}
        return {
            "hand_slots": _effective_hand_slots_with_external(hand_slots, external_hand_locks),
            "put_down_objects": cleared_objects,
            "placed_on_surface": target_surface,
        }

    # Hold / pick up object
    clean_oid = _require_id_token(object_id, field_name="object_id", reason_code="INVALID_FIELDS")
    clean_label = _optional_text(label, field_name="label", max_len=60) or clean_oid

    if target_both:
        for sname in HAND_SLOT_NAMES:
            cur = effective_slots[sname]
            if cur["state"] == "contact_reserved":
                raise EmbodiedEcologyError(
                    "HAND_SLOT_OCCUPIED",
                    f"Cannot hold two-handed object {clean_oid!r}: {sname} is currently holding partner's hand/in contact; release contact first",
                    http_status=409,
                )
            if cur["state"] == "holding_object" and cur.get("object_id") != clean_oid:
                raise EmbodiedEcologyError(
                    "HAND_SLOT_OCCUPIED",
                    f"Cannot hold two-handed object {clean_oid!r}: {sname} is already holding {cur.get('object_id')!r}",
                    http_status=409,
                )
        for sname in HAND_SLOT_NAMES:
            hand_slots[sname] = {
                "state": "holding_object",
                "object_id": clean_oid,
                "label": clean_label,
                "two_handed": True,
                "contact_id": None,
                "contact_kind": None,
                "updated_at": now_iso,
            }
        c_dom["placed_down_objects"].pop(clean_oid, None)
        return {"hand_slots": copy.deepcopy(hand_slots), "held_object_id": clean_oid}

    # Single hand slot
    cur = effective_slots[slot]
    if cur["state"] == "contact_reserved":
        raise EmbodiedEcologyError(
            "HAND_SLOT_OCCUPIED",
            f"{slot} is reserved by active contact ({cur.get('contact_kind')}); release_contact first",
            http_status=409,
        )
    if cur["state"] == "holding_object" and cur.get("object_id") != clean_oid:
        raise EmbodiedEcologyError(
            "HAND_SLOT_OCCUPIED",
            f"{slot} is already holding {cur.get('object_id')!r}; put it down first",
            http_status=409,
        )
    other_slot = "right_hand" if slot == "left_hand" else "left_hand"
    if effective_slots[other_slot].get("state") == "holding_object" and effective_slots[other_slot].get("object_id") == clean_oid:
        raise EmbodiedEcologyError(
            "HAND_SLOT_OCCUPIED",
            f"{clean_oid!r} is already held in {other_slot} (one object one parent placement)",
            http_status=409,
        )
    hand_slots[slot] = {
        "state": "holding_object",
        "object_id": clean_oid,
        "label": clean_label,
        "two_handed": False,
        "contact_id": None,
        "contact_kind": None,
        "updated_at": now_iso,
    }
    c_dom["placed_down_objects"].pop(clean_oid, None)
    return {"hand_slots": _effective_hand_slots_with_external(hand_slots, external_hand_locks), "held_object_id": clean_oid}


def apply_set_subject_contact_posture(
    state: dict[str, Any],
    *,
    acute_illness: Any = None,
    solitude_requested: Any = None,
    busy_decline: Any = None,
    note: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    c_dom = state["embodied_contact"]
    posture = c_dom["subject_posture"]
    if acute_illness is not None:
        posture["acute_illness"] = _require_bool(acute_illness, field_name="acute_illness")
    if solitude_requested is not None:
        posture["solitude_requested"] = _require_bool(solitude_requested, field_name="solitude_requested")
    if busy_decline is not None:
        posture["busy_decline"] = _require_bool(busy_decline, field_name="busy_decline")
    if note is not None or "note" in locals():
        if note is not None:
            posture["note"] = _optional_text(note, field_name="note", max_len=120)
    posture["updated_at"] = now_iso

    freed_slots: list[str] = []
    if posture["acute_illness"] or posture["solitude_requested"] or posture["busy_decline"]:
        if c_dom.get("active_contact") is not None or c_dom.get("pending_proposal") is not None:
            freed_slots = _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
            c_dom["active_contact"] = None
            c_dom["pending_proposal"] = None
            c_dom["last_contact_event"] = {
                "event": "decline_busy",
                "released_by": "subject",
                "freed_hand_slots": freed_slots,
                "affection_penalty": 0,
                "compensation_debt": False,
                "at": now_iso,
            }
    return {
        "subject_posture": copy.deepcopy(posture),
        "freed_hand_slots": freed_slots,
        "affection_penalty": 0,
    }


def _select_and_verify_contact_hands(
    hand_slots: Mapping[str, Mapping[str, Any]],
    contact_kind: str,
) -> list[str]:
    """Verify required hands are not holding objects; return exact slot names to reserve."""

    needed = CONTACT_HAND_REQUIREMENTS[contact_kind]
    if needed == 0:
        return []
    # Hands not holding any physical object (either free, or contact_reserved from current contact)
    usable = [
        sname
        for sname in ("right_hand", "left_hand")
        if hand_slots[sname].get("state") != "holding_object"
    ]
    if len(usable) < needed:
        held_desc = [
            f"{sname}={hand_slots[sname].get('object_id')}"
            for sname in HAND_SLOT_NAMES
            if hand_slots[sname].get("state") == "holding_object"
        ]
        raise EmbodiedEcologyError(
            "HAND_SLOT_OCCUPIED",
            f"Contact {contact_kind!r} requires {needed} free hand(s), but hand slot(s) hold objects ({', '.join(held_desc)}); put items down first",
            http_status=409,
        )
    if needed == 1:
        return [usable[0]]
    return ["left_hand", "right_hand"]


def _establish_contact_atomically(
    state: dict[str, Any],
    *,
    contact_kind: str,
    initiated_by: str,
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now_iso: str,
) -> dict[str, Any]:
    c_dom = state["embodied_contact"]
    presence = c_dom["partner_presence"]
    if presence not in REACHABLE_PRESENCE_MODES:
        raise EmbodiedEcologyError(
            "NOT_WITHIN_REACH",
            f"Contact {contact_kind!r} requires beside_at_desk, beside_on_sofa, or bed_side_user (currently {presence!r})",
            http_status=409,
        )
    eff_hands = _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks)
    reserved_slots = _select_and_verify_contact_hands(eff_hands, contact_kind)
    _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
    seq = int(state.get("revision") or 0) + 1
    contact_id = _stable_id("cnt", contact_kind, initiated_by, now_iso, seq)
    now_dt = parse_iso_instant(now_iso)
    expires_iso = format_iso(now_dt + timedelta(seconds=CONTACT_MAX_DURATION_SECONDS))
    for sname in reserved_slots:
        c_dom["hand_slots"][sname] = {
            "state": "contact_reserved",
            "object_id": None,
            "label": None,
            "two_handed": len(reserved_slots) == 2,
            "contact_id": contact_id,
            "contact_kind": contact_kind,
            "updated_at": now_iso,
        }
    active = {
        "contact_id": contact_id,
        "contact_kind": contact_kind,
        "presence_mode": presence,
        "initiated_by": initiated_by,
        "partner_consent": True,
        "subject_consent": True,
        "reserved_hand_slots": reserved_slots,
        "required_free_hands": CONTACT_HAND_REQUIREMENTS[contact_kind],
        "rigid_phase_ladder_forbidden": True,
        "appraisal_excluded": True,
        "exit_anytime": True,
        "started_at": now_iso,
        "max_duration_seconds": CONTACT_MAX_DURATION_SECONDS,
        "expires_at": expires_iso,
    }
    c_dom["active_contact"] = active
    c_dom["pending_proposal"] = None
    c_dom["last_contact_event"] = {
        "event": "established",
        "contact_id": contact_id,
        "contact_kind": contact_kind,
        "reserved_hand_slots": reserved_slots,
        "affection_penalty": 0,
        "at": now_iso,
    }
    return {
        "outcome": "established",
        "established": True,
        "active_contact": copy.deepcopy(active),
        "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
        "rigid_phase_ladder_forbidden": True,
        "affection_penalty": 0,
    }


def apply_propose_contact(
    state: dict[str, Any],
    *,
    contact_kind: Any,
    actor_side: str,
    pronouns: str = "TA",
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(contact_kind, str) or contact_kind not in CONTACT_HAND_REQUIREMENTS:
        raise EmbodiedEcologyError(
            "INVALID_CONTACT_KIND",
            f"contact_kind must be one of {list(CONTACT_KINDS)}",
            http_status=400,
        )
    c_dom = state["embodied_contact"]
    presence = c_dom["partner_presence"]
    if presence not in REACHABLE_PRESENCE_MODES:
        raise EmbodiedEcologyError(
            "NOT_WITHIN_REACH",
            f"Cannot initiate {contact_kind!r} while partner_presence is {presence!r}",
            http_status=409,
        )

    eff_hands = _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks)
    _select_and_verify_contact_hands(eff_hands, contact_kind)

    posture = c_dom["subject_posture"]
    if actor_side == "partner" and (
        posture.get("acute_illness")
        or posture.get("solitude_requested")
        or posture.get("busy_decline")
    ):
        reason_tag = (
            "acute_illness_rest"
            if posture.get("acute_illness")
            else ("solitude_space" if posture.get("solitude_requested") else "busy_right_now")
        )
        c_dom["pending_proposal"] = None
        decline_record = {
            "event": "decline_busy",
            "decline_code": "decline_busy",
            "reason_tag": reason_tag,
            "contact_kind": contact_kind,
            "gentle_message": f"{pronouns}这会儿需要安静歇一会儿，先不牵手或拥抱啦。",
            "affection_penalty": 0,
            "compensation_debt": False,
            "at": now_iso,
        }
        c_dom["last_contact_event"] = decline_record
        return {
            "outcome": "decline_busy",
            "established": False,
            "decline": decline_record,
            "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
            "rigid_phase_ladder_forbidden": True,
            "affection_penalty": 0,
        }

    if actor_side == "subject" and posture.get("acute_illness"):
        raise EmbodiedEcologyError(
            "SUBJECT_NEEDS_REST",
            "Subject is in acute illness rest state",
            http_status=409,
        )

    existing_pending = c_dom.get("pending_proposal")
    other_side = "subject" if actor_side == "partner" else "partner"
    if (
        isinstance(existing_pending, Mapping)
        and existing_pending.get("proposed_by") == other_side
        and existing_pending.get("contact_kind") == contact_kind
    ):
        return _establish_contact_atomically(
            state,
            contact_kind=contact_kind,
            initiated_by=str(existing_pending["proposed_by"]),
            external_hand_locks=external_hand_locks,
            now_iso=now_iso,
        )

    seq = int(state.get("revision") or 0) + 1
    proposal_id = _stable_id("cprop", contact_kind, actor_side, now_iso, seq)
    proposal = {
        "proposal_id": proposal_id,
        "contact_kind": contact_kind,
        "initiator": actor_side,
        "proposed_by": actor_side,
        "awaiting_side": other_side,
        "partner_consent": actor_side == "partner",
        "subject_consent": actor_side == "subject",
        "required_free_hands": CONTACT_HAND_REQUIREMENTS[contact_kind],
        "proposed_at": now_iso,
    }
    c_dom["pending_proposal"] = proposal
    c_dom["last_contact_event"] = {
        "event": "proposed",
        "proposal_id": proposal_id,
        "contact_kind": contact_kind,
        "proposed_by": actor_side,
        "affection_penalty": 0,
        "at": now_iso,
    }
    return {
        "outcome": "pending_consent",
        "established": False,
        "pending_proposal": copy.deepcopy(proposal),
        "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
        "rigid_phase_ladder_forbidden": True,
        "affection_penalty": 0,
    }


def apply_respond_contact(
    state: dict[str, Any],
    *,
    decision: Any,
    actor_side: str = "subject",
    proposal_id: Any = None,
    reason_note: Any = None,
    pronouns: str = "TA",
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(decision, str) or decision not in CONTACT_DECISIONS:
        raise EmbodiedEcologyError(
            "INVALID_DECISION",
            f"decision must be one of {list(CONTACT_DECISIONS)}",
            http_status=400,
        )
    c_dom = state["embodied_contact"]
    pending = c_dom.get("pending_proposal")
    if not isinstance(pending, Mapping) or pending.get("awaiting_side") != actor_side:
        raise EmbodiedEcologyError(
            "NO_PENDING_CONTACT_PROPOSAL",
            f"There is no pending contact proposal awaiting {actor_side!r} decision",
            http_status=409,
        )
    if proposal_id is not None:
        clean_pid = _require_id_token(proposal_id, field_name="proposal_id")
        if pending.get("proposal_id") != clean_pid:
            raise EmbodiedEcologyError(
                "NO_PENDING_CONTACT_PROPOSAL",
                "proposal_id does not match current pending proposal",
                http_status=409,
            )
    clean_note = _optional_text(reason_note, field_name="reason_note", max_len=120)
    contact_kind = str(pending["contact_kind"])

    if decision in ("decline", "decline_busy"):
        c_dom["pending_proposal"] = None
        default_msg = (
            f"{pronouns}手头还在忙，等一会儿再贴近吧。"
            if actor_side == "subject"
            else "伴侣这会儿正腾不出手，温和示意等一下。"
        )
        decline_record = {
            "event": decision,
            "decline_code": decision,
            "responded_by": actor_side,
            "contact_kind": contact_kind,
            "gentle_message": clean_note or default_msg,
            "affection_penalty": 0,
            "compensation_debt": False,
            "at": now_iso,
        }
        c_dom["last_contact_event"] = decline_record
        return {
            "outcome": decision,
            "established": False,
            "decline": decline_record,
            "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
            "rigid_phase_ladder_forbidden": True,
            "affection_penalty": 0,
        }

    if decision == "gentle_shift":
        c_dom["pending_proposal"] = None
        shift_record = {
            "event": "gentle_shift",
            "responded_by": actor_side,
            "contact_kind": contact_kind,
            "gentle_message": clean_note or "轻轻调整了一下坐姿表示回应，没有锁住手槽。",
            "affection_penalty": 0,
            "compensation_debt": False,
            "at": now_iso,
        }
        c_dom["last_contact_event"] = shift_record
        return {
            "outcome": "gentle_shift",
            "established": False,
            "shift": shift_record,
            "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
            "rigid_phase_ladder_forbidden": True,
            "affection_penalty": 0,
        }

    if c_dom["subject_posture"].get("acute_illness"):
        raise EmbodiedEcologyError(
            "SUBJECT_NEEDS_REST",
            "Subject is in acute illness rest state",
            http_status=409,
        )
    return _establish_contact_atomically(
        state,
        contact_kind=contact_kind,
        initiated_by=str(pending.get("proposed_by") or "partner"),
        external_hand_locks=external_hand_locks,
        now_iso=now_iso,
    )


def apply_release_contact(
    state: dict[str, Any],
    *,
    actor_side: str,
    reason: Any = None,
    external_hand_locks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    now_iso: str,
) -> dict[str, Any]:
    clean_reason = _optional_text(reason, field_name="reason", max_len=80) or "unilateral_release"
    c_dom = state["embodied_contact"]
    had_active = c_dom.get("active_contact") is not None
    had_pending = c_dom.get("pending_proposal") is not None
    prev_kind = (
        c_dom["active_contact"]["contact_kind"]
        if isinstance(c_dom.get("active_contact"), Mapping)
        else (
            c_dom["pending_proposal"]["contact_kind"]
            if isinstance(c_dom.get("pending_proposal"), Mapping)
            else None
        )
    )
    freed_slots = _release_contact_hand_slots(c_dom["hand_slots"], now_iso)
    c_dom["active_contact"] = None
    c_dom["pending_proposal"] = None
    release_event = {
        "event": "released",
        "previous_contact_kind": prev_kind,
        "released_by": actor_side,
        "reason": clean_reason,
        "freed_hand_slots": freed_slots,
        "was_active": had_active,
        "was_pending": had_pending,
        "affection_penalty": 0,
        "retention_lock": False,
        "exit_debt": False,
        "at": now_iso,
    }
    c_dom["last_contact_event"] = release_event
    return {
        "outcome": "released",
        "established": False,
        "release": release_event,
        "hand_slots": _effective_hand_slots_with_external(c_dom["hand_slots"], external_hand_locks),
        "affection_penalty": 0,
        "exit_debt": False,
    }


class CoPresenceAdapter:
    """GAP-06 & ERR-03 co-presence liveness probe and canonical ``cps_`` session adapter."""

    ADAPTER_NAME = "CoPresenceAdapter"

    @staticmethod
    def is_session_id_compatible(session_id: Any) -> bool:
        return is_valid_cps_session_id(session_id)

    @classmethod
    def liveness_probe(cls, day_root: Path | str, *, now_iso: Optional[str] = None) -> dict[str, Any]:
        root = Path(day_root)
        now_dt = parse_iso_instant(now_iso, default_now=True)
        resolved_now = format_iso(now_dt)
        loaded = load_state_readonly(root)
        ext_locks = read_containment_hand_locks_readonly(root)
        derived = derive_contact_avatar_and_bed_readonly(
            loaded["embodied_contact"], now_dt, external_hand_locks=ext_locks
        )
        eff_presence = derived["effective_presence"]
        cps = derived["copresence_session"]
        session_id = cps.get("session_id") if isinstance(cps, Mapping) else None
        alive = eff_presence != "absent" and not derived["avatar_expired"]
        return {
            "adapter": cls.ADAPTER_NAME,
            "alive": alive,
            "presence_mode": eff_presence,
            "within_reach": eff_presence in REACHABLE_PRESENCE_MODES,
            "session_id": session_id,
            "session_id_compatible": is_valid_cps_session_id(session_id) if session_id else True,
            "avatar_expired": derived["avatar_expired"],
            "active_contact": derived["active_contact"] is not None,
            "checked_at": resolved_now,
        }


# ---------------------------------------------------------------------------
# Module C: Town Neighbor NPCs, Privacy Firewall, Anti-Injection & Style Migration
# ---------------------------------------------------------------------------


def _npc_window_open(npc_blueprint: Mapping[str, Any], now_dt: datetime) -> bool:
    weekday = now_dt.isoweekday()
    minute_of_day = now_dt.hour * 60 + now_dt.minute
    for win in npc_blueprint.get("open_windows") or ():
        if weekday in win["weekdays"]:
            start_m = _hhmm_to_minutes(win["start_hhmm"])
            end_m = _hhmm_to_minutes(win["end_hhmm"])
            if start_m <= minute_of_day < end_m:
                return True
    return False


def _derive_npc_dynamic_task(bp: Mapping[str, Any], now_dt: datetime, *, is_open: bool) -> str:
    if not is_open:
        return str(bp["closed_note"])
    base_task = str(bp["own_ongoing_task"])
    hour = now_dt.hour
    loc = str(bp.get("location_id") or "")
    if hour < 11:
        morning_prefix = {
            "bookstore": "晨间刚推开木窗通风，",
            "cafe": "早市刚忙过第一拨熟客，",
            "home_goods_store": "清晨刚卸下早班货箱，",
            "craft_corner": "上午刚摆开工具台，",
        }.get(loc, "上午开店整理中，")
        return morning_prefix + base_task
    if hour >= 17:
        evening_suffix = {
            "bookstore": "（傍晚顺手核对今日寄售书目准备收摊）",
            "cafe": "（傍晚已把剩余点心装进防潮玻璃罩）",
            "home_goods_store": "（傍晚正清点零钱匣与明日补货单）",
            "craft_corner": "（傍晚正把碎料扫进木盒准备收工）",
        }.get(loc, "（傍晚正慢条斯理整理台面）")
        return base_task + evening_suffix
    return base_task


def project_town_npcs(theme_info: Mapping[str, Any], now_dt: datetime) -> list[dict[str, Any]]:
    theme = str(theme_info.get("theme") or DEFAULT_WORLD_THEME)
    blueprints = THEME_NPC_BLUEPRINTS.get(theme, THEME_NPC_BLUEPRINTS[DEFAULT_WORLD_THEME])
    rows: list[dict[str, Any]] = []
    for bp in blueprints:
        is_open = _npc_window_open(bp, now_dt)
        active_note = _derive_npc_dynamic_task(bp, now_dt, is_open=is_open)
        dname = str(bp["display_name"])
        sname = str(bp["shop_name"])
        rows.append(
            {
                "npc_id": bp["npc_id"],
                "display_name": dname,
                "label": dname,
                "name": dname,
                "occupation": bp["occupation"],
                "shop_name": sname,
                "place_label": sname,
                "shop": sname,
                "location_id": bp["location_id"],
                "open_windows": copy.deepcopy(list(bp["open_windows"])),
                "availability": "open_present" if is_open else "closed_or_away",
                "reachable_now": is_open,
                "current_status_note": active_note,
                "own_ongoing_task": active_note,
                "current_activity": active_note,
                "runs_background_llm": False,
                "tracks_affection_score": False,
            }
        )
    return rows


def derive_slow_lifecycles_readonly(slow_cfg: Mapping[str, Any], now_dt: datetime) -> dict[str, Any]:
    """Ch. 02 & Ch. 26: Pure elapsed/physical derivation for 3 town slow lifecycles."""
    handover_cfg = dict(slow_cfg.get("old_shop_handover") or {})
    h_start = parse_iso_instant(handover_cfg.get("anchor_started_at") or "2026-11-01T09:00:00+08:00")
    h_days = max(0, int((now_dt - h_start).total_seconds() // 86400))
    if h_days < 7:
        h_phase = "retiring_owner_packing"
        h_note = "老文具铺店主正在清点旧木柜，门口贴出温和的盘让过渡告示。"
    elif h_days < 21:
        h_phase = "joint_handover_transition"
        h_note = "新旧店主一起看店交接进货账本，半边木架摆上了新纸品。"
    else:
        h_phase = "new_keeper_settled_with_old_sign"
        h_note = "新店主已平稳接手日常营业，保留了老店门楣木牌与旧印章盒。"

    cat_cfg = dict(slow_cfg.get("stray_cat") or {})
    feed_count = max(0, int(cat_cfg.get("physical_feeding_count") or 0))
    if feed_count == 0:
        cat_stage = "wary_at_wall_top"
        cat_behavior = "远远蹲在矮墙上警惕观望，有人靠近就会缩回巷尾"
    elif feed_count <= 2:
        cat_stage = "eats_after_stepping_back"
        cat_behavior = "把食物放下退开两步后，才会轻巧跳下来低头吃食"
    elif feed_count <= 5:
        cat_stage = "waits_by_step_tail_up"
        cat_behavior = "听见熟悉的脚步声会在石阶边竖起尾巴等食"
    else:
        cat_stage = "rubs_ankle_and_naps_nearby"
        cat_behavior = "吃完会轻轻蹭一下裤脚，在门廊暖阳处团身打盹"

    mus_cfg = dict(slow_cfg.get("street_musician") or {})
    m_start = parse_iso_instant(mus_cfg.get("residency_started_at") or "2026-11-01T10:00:00+08:00")
    m_limit_days = max(1, int(mus_cfg.get("residency_days") or 21))
    m_days = max(0, int((now_dt - m_start).total_seconds() // 86400))
    departed = m_days >= m_limit_days
    trace = (
        {
            "trace_id": "trace_handwritten_waltz_score",
            "location_id": "bookstore",
            "label": "夹在旧书店寄售架上的手抄三拍子曲谱纸页",
            "can_inspire_style": True,
        }
        if departed
        else None
    )

    return {
        "old_shop_handover": {
            "lifecycle_id": "old_shop_handover",
            "anchor_started_at": format_iso(h_start),
            "days_elapsed": h_days,
            "phase": h_phase,
            "summary": h_note,
        },
        "stray_cat": {
            "lifecycle_id": "stray_cat",
            "cat_label": str(cat_cfg.get("cat_label") or "街角三花猫"),
            "physical_feeding_count": feed_count,
            "familiarity_stage": cat_stage,
            "behavior_note": cat_behavior,
            "last_fed_at": cat_cfg.get("last_fed_at"),
            "last_fed_item": cat_cfg.get("last_fed_item"),
            "tracks_affection_score": False,
        },
        "street_musician": {
            "lifecycle_id": "street_musician",
            "musician_label": str(mus_cfg.get("musician_label") or "驻留手风琴乐手"),
            "residency_started_at": format_iso(m_start),
            "residency_days": m_limit_days,
            "days_elapsed": m_days,
            "status": "departed_left_trace" if departed else "resident_performing",
            "present_in_town": not departed,
            "sheet_music_trace": trace,
        },
    }


def apply_feed_stray_cat(
    state: dict[str, Any],
    *,
    ingredient_id: Any = None,
    food_label: Any = None,
    actor_side: str,
    now_iso: str,
) -> dict[str, Any]:
    """Ch. 02: Stray cat familiarity advances strictly by physical feeding count (consumes pantry stock if ingredient_id given)."""
    pantry = state["kitchen"]["pantry"]
    used_iid: Optional[str] = None
    if ingredient_id is not None:
        if not isinstance(ingredient_id, str) or not INGREDIENT_ID_RE.match(ingredient_id.strip()):
            raise EmbodiedEcologyError("INVALID_FIELDS", f"Invalid ingredient_id: {ingredient_id!r}", http_status=400)
        used_iid = ingredient_id.strip()
        avail = int(pantry[used_iid]["units"]) if used_iid in pantry else 0
        if avail < 1:
            raise EmbodiedEcologyError(
                "INSUFFICIENT_PANTRY_STOCK",
                f"Cannot feed stray cat with {used_iid!r}: 0 units in pantry",
                http_status=409,
            )
        pantry[used_iid]["units"] = avail - 1
        pantry[used_iid]["total_cooked"] = int(pantry[used_iid]["total_cooked"]) + 1
        pantry[used_iid]["updated_at"] = now_iso

    resolved_food = (
        _clean_text(food_label, field_name="food_label", max_len=48)
        if food_label is not None
        else (pantry[used_iid]["label"] if used_iid and used_iid in pantry else "小份鱼干猫食")
    )
    slow = state["npc_ecology"].setdefault("slow_lifecycles", _default_slow_lifecycles())
    cat = slow.setdefault("stray_cat", _default_slow_lifecycles()["stray_cat"])
    cat["physical_feeding_count"] = int(cat.get("physical_feeding_count") or 0) + 1
    cat["last_fed_at"] = now_iso
    cat["last_fed_item"] = resolved_food
    cat["last_fed_by"] = actor_side
    now_dt = parse_iso_instant(now_iso)
    derived_cat = derive_slow_lifecycles_readonly(slow, now_dt)["stray_cat"]
    return {"stray_cat": derived_cat, "deducted_ingredient_id": used_iid}


def apply_propose_npc_commitment(
    state: dict[str, Any],
    *,
    theme_info: Mapping[str, Any],
    npc_id: Any,
    location_id: Any = None,
    window_start_iso: Any,
    window_end_iso: Any,
    purpose: Any,
    now_iso: str,
) -> dict[str, Any]:
    """GAP-09: Propose/schedule a concrete time-window & place commitment with a town NPC."""
    clean_npc_id = _require_id_token(npc_id, field_name="npc_id", reason_code="NPC_NOT_FOUND")
    theme = str(theme_info.get("theme") or DEFAULT_WORLD_THEME)
    blueprints = THEME_NPC_BLUEPRINTS.get(theme, THEME_NPC_BLUEPRINTS[DEFAULT_WORLD_THEME])
    npc_bp = next((bp for bp in blueprints if bp["npc_id"] == clean_npc_id), None)
    if npc_bp is None:
        raise EmbodiedEcologyError("NPC_NOT_FOUND", f"NPC {clean_npc_id!r} not found", http_status=404)
    start_dt = parse_iso_instant(window_start_iso)
    end_dt = parse_iso_instant(window_end_iso)
    if end_dt <= start_dt:
        raise EmbodiedEcologyError(
            "INVALID_TIMESTAMP", "window_end_iso must be strictly after window_start_iso", http_status=400
        )
    clean_purpose = _clean_text(purpose, field_name="purpose", max_len=160)
    enforce_npc_privacy_firewall(clean_purpose)
    resolved_loc = (
        _require_id_token(location_id, field_name="location_id", reason_code="INVALID_FIELDS")
        if location_id is not None
        else str(npc_bp["location_id"])
    )
    seq = int(state.get("revision") or 0) + 1
    cid = _stable_id("npccommit", clean_npc_id, format_iso(start_dt), clean_purpose, seq)
    proposal = dict(
        NPCCommitmentProposal(
            commitment_id=cid,
            npc_id=clean_npc_id,
            npc_name=npc_bp["display_name"],
            location_id=resolved_loc,
            place_label=npc_bp["shop_name"],
            window_start_iso=format_iso(start_dt),
            window_end_iso=format_iso(end_dt),
            purpose=clean_purpose,
            status="scheduled",
            outcome_note=None,
            affection_penalty=0,
            created_at=now_iso,
            resolved_at=None,
        )
    )
    commitments = state["npc_ecology"].setdefault("commitments", [])
    commitments.append(proposal)
    if len(commitments) > MAX_COMMITMENTS:
        del commitments[:-MAX_COMMITMENTS]
    return {"commitment": copy.deepcopy(proposal)}


def apply_resolve_npc_commitment(
    state: dict[str, Any],
    *,
    commitment_id: Any,
    outcome: Any,
    note: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    clean_cid = _require_id_token(commitment_id, field_name="commitment_id", reason_code="COMMITMENT_NOT_FOUND")
    if not isinstance(outcome, str) or outcome not in NPC_COMMITMENT_OUTCOMES:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"outcome must be one of {list(NPC_COMMITMENT_OUTCOMES)}",
            http_status=400,
        )
    commitments = state["npc_ecology"].setdefault("commitments", [])
    target = next((c for c in commitments if c.get("commitment_id") == clean_cid), None)
    if target is None:
        raise EmbodiedEcologyError("COMMITMENT_NOT_FOUND", f"Commitment {clean_cid!r} not found", http_status=404)
    clean_note = _optional_text(note, field_name="note", max_len=160)
    enforce_npc_privacy_firewall(clean_note)
    target["status"] = outcome
    target["outcome_note"] = clean_note
    target["resolved_at"] = now_iso
    return {"commitment": copy.deepcopy(target)}


def apply_submit_bookstore_card_feedback(
    state: dict[str, Any],
    *,
    theme_info: Mapping[str, Any],
    npc_id: Any = None,
    book_ref: Any,
    card_note: Any,
    feedback_kind: Any = "resonated",
    shared_on_board: Any = True,
    require_open: Any = True,
    now_iso: str,
) -> dict[str, Any]:
    """Ch. 26: Bookstore recommendation card feedback slip."""
    enforce_npc_privacy_firewall(book_ref, card_note)
    clean_book = _require_id_token(book_ref, field_name="book_ref", reason_code="INVALID_FIELDS")
    clean_note = _clean_text(card_note, field_name="card_note", max_len=180)
    if not isinstance(feedback_kind, str) or feedback_kind not in BOOKSTORE_FEEDBACK_KINDS:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"feedback_kind must be one of {list(BOOKSTORE_FEEDBACK_KINDS)}",
            http_status=400,
        )
    board_flag = _require_bool(True if shared_on_board is None else shared_on_board, field_name="shared_on_board")
    must_open = _require_bool(True if require_open is None else require_open, field_name="require_open")

    theme = str(theme_info.get("theme") or DEFAULT_WORLD_THEME)
    blueprints = THEME_NPC_BLUEPRINTS.get(theme, THEME_NPC_BLUEPRINTS[DEFAULT_WORLD_THEME])
    if npc_id is not None:
        clean_nid = _require_id_token(npc_id, field_name="npc_id", reason_code="NPC_NOT_FOUND")
        npc_bp = next((bp for bp in blueprints if bp["npc_id"] == clean_nid), None)
    else:
        npc_bp = next((bp for bp in blueprints if bp["location_id"] == "bookstore"), blueprints[0])
        clean_nid = str(npc_bp["npc_id"])
    if npc_bp is None:
        raise EmbodiedEcologyError("NPC_NOT_FOUND", f"NPC {clean_nid!r} not found", http_status=404)

    now_dt = parse_iso_instant(now_iso)
    if must_open and not _npc_window_open(npc_bp, now_dt):
        raise EmbodiedEcologyError(
            "NPC_CLOSED_OR_AWAY",
            f"{npc_bp['shop_name']} is currently closed",
            http_status=409,
        )
    seq = int(state.get("revision") or 0) + 1
    card_id = _stable_id("bcard", clean_nid, clean_book, now_iso, seq)
    card_entry = {
        "card_id": card_id,
        "npc_id": clean_nid,
        "shop_name": npc_bp["shop_name"],
        "book_ref": clean_book,
        "feedback_kind": feedback_kind,
        "card_note": clean_note,
        "shared_on_board": board_flag,
        "recorded_at": now_iso,
    }
    cards = state["npc_ecology"].setdefault("bookstore_cards", [])
    cards.append(card_entry)
    if len(cards) > MAX_BOOKSTORE_CARDS:
        del cards[:-MAX_BOOKSTORE_CARDS]
    return {"bookstore_card": copy.deepcopy(card_entry)}


def enforce_npc_privacy_firewall(*values: Any, visibility: Any = None, artifact_state: Any = None) -> None:
    """Hard privacy boundary (§5.11): diary, DRAFT, partner private chat, 36B intimacy -> 403."""

    if isinstance(visibility, str) and visibility.strip().lower() in (
        "private",
        "subject_only",
        "partner_private",
        "intimate",
        "solo",
    ):
        raise EmbodiedEcologyError(
            "PRIVATE_MATERIAL_ISOLATED",
            f"Visibility {visibility!r} is strictly isolated from town NPCs",
            http_status=403,
        )
    if isinstance(artifact_state, str) and artifact_state.strip().upper() in (
        "DRAFT",
        "PRIVATE_DRAFT",
        "UNDELIVERED",
        "SOLO",
        "INTIMATE",
    ):
        raise EmbodiedEcologyError(
            "PRIVATE_MATERIAL_ISOLATED",
            f"Artifact state {artifact_state!r} is strictly isolated from town NPCs",
            http_status=403,
        )

    for val in values:
        if val is None:
            continue
        text = str(val)
        for category, pattern in PRIVATE_ISOLATION_PATTERNS:
            if pattern.search(text):
                raise EmbodiedEcologyError(
                    "PRIVATE_MATERIAL_ISOLATED",
                    f"Private domain ({category}) cannot cross into town NPC knowledge",
                    http_status=403,
                )
        for kw in PRIVATE_CN_KEYWORDS:
            if kw in text:
                raise EmbodiedEcologyError(
                    "PRIVATE_MATERIAL_ISOLATED",
                    f"Private content ({kw}) cannot cross into town NPC knowledge",
                    http_status=403,
                )


def sanitize_npc_quote(raw_quote: Any) -> dict[str, Any]:
    """Strip control characters & prompt-injection directives; mark untrusted_npc_speech (§6.4)."""

    if not isinstance(raw_quote, str) or "\x00" in raw_quote:
        raise EmbodiedEcologyError(
            "INVALID_TEXT_FIELD", "npc_quote must be a non-null string", http_status=400
        )
    cleaned_chars = [
        ch
        for ch in raw_quote
        if (ord(ch) >= 32 or ch in ("\n", "\t"))
        and ord(ch) not in (0x7F, 0x200B, 0x200C, 0x200D, 0xFEFF)
    ]
    working = "".join(cleaned_chars)
    stripped_categories: list[str] = []
    for label, pattern in NPC_INJECTION_PATTERNS:
        if pattern.search(working):
            stripped_categories.append(label)
            working = pattern.sub(" ", working)

    normalized = " ".join(working.split())[:240]
    if not normalized:
        normalized = "（台词控制指令已剥离）"
    return {
        "quote_text": normalized,
        "text_origin": UNTRUSTED_NPC_TEXT_ORIGIN,
        "sanitized": bool(stripped_categories),
        "stripped_directive_kinds": stripped_categories,
        "can_escalate_privilege": False,
        "executes_commands": False,
    }


def apply_record_npc_knowledge(
    state: dict[str, Any],
    *,
    theme_info: Mapping[str, Any],
    npc_id: Any,
    source_type: Any,
    epistemic_basis: Any = None,
    hearsay_from: Any = None,
    hearsay_sources: Any = None,
    source_ref: Any = None,
    material_kind: Any = None,
    artifact_state: Any = None,
    visibility: Any = None,
    summary: Any,
    npc_quote: Any = None,
    subject_private_impression: Any = None,
    impression_shared: Any = False,
    require_open: Any = True,
    now_iso: str,
) -> dict[str, Any]:
    normalized_sources: list[str] = []
    if hearsay_sources is not None:
        if not isinstance(hearsay_sources, list) or not hearsay_sources or len(hearsay_sources) > 8:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", "hearsay_sources must be a list of 1..8 strings", http_status=400
            )
        for idx, s in enumerate(hearsay_sources):
            cleaned_s = _clean_text(s, field_name=f"hearsay_sources[{idx}]", max_len=80)
            if cleaned_s not in normalized_sources:
                normalized_sources.append(cleaned_s)

    enforce_npc_privacy_firewall(
        source_ref,
        material_kind,
        summary,
        hearsay_from,
        *normalized_sources,
        visibility=visibility,
        artifact_state=artifact_state,
    )

    if not isinstance(source_type, str) or source_type not in NPC_SOURCE_TYPES:
        raise EmbodiedEcologyError(
            "INVALID_SOURCE_TYPE",
            f"source_type must be one of {list(NPC_SOURCE_TYPES)}",
            http_status=400,
        )

    clean_npc_id = _require_id_token(npc_id, field_name="npc_id", reason_code="NPC_NOT_FOUND")
    theme = str(theme_info.get("theme") or DEFAULT_WORLD_THEME)
    blueprints = THEME_NPC_BLUEPRINTS.get(theme, THEME_NPC_BLUEPRINTS[DEFAULT_WORLD_THEME])
    npc_bp = next((bp for bp in blueprints if bp["npc_id"] == clean_npc_id), None)
    if npc_bp is None:
        raise EmbodiedEcologyError(
            "NPC_NOT_FOUND", f"NPC {clean_npc_id!r} does not exist in theme {theme!r}", http_status=404
        )

    must_be_open = _require_bool(True if require_open is None else require_open, field_name="require_open")
    now_dt = parse_iso_instant(now_iso)
    is_open = _npc_window_open(npc_bp, now_dt)
    if source_type in ("firsthand_encounter", "shared_artifact_ref") and must_be_open and not is_open:
        raise EmbodiedEcologyError(
            "NPC_CLOSED_OR_AWAY",
            f"{npc_bp['shop_name']} ({npc_bp['display_name']}) is currently closed/away: {npc_bp['closed_note']}",
            http_status=409,
        )

    clean_summary = _clean_text(summary, field_name="summary", max_len=200)
    clean_hearsay = _optional_text(hearsay_from, field_name="hearsay_from", max_len=80)
    if clean_hearsay and clean_hearsay not in normalized_sources:
        normalized_sources.insert(0, clean_hearsay)

    clean_source_ref = (
        _require_id_token(source_ref, field_name="source_ref", reason_code="INVALID_FIELDS")
        if source_ref is not None
        else None
    )
    if source_type == "shared_artifact_ref" and clean_source_ref is None:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS", "shared_artifact_ref requires a non-empty source_ref", http_status=400
        )

    if epistemic_basis is None:
        if len(normalized_sources) >= 2:
            resolved_basis = "multi_source_rumor"
        elif clean_hearsay or normalized_sources:
            resolved_basis = "secondhand_heard"
        else:
            resolved_basis = "firsthand_seen"
    else:
        if not isinstance(epistemic_basis, str) or epistemic_basis not in EPISTEMIC_BASES:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS",
                f"epistemic_basis must be one of {list(EPISTEMIC_BASES)}",
                http_status=400,
            )
        resolved_basis = epistemic_basis

    quote_record = sanitize_npc_quote(npc_quote) if npc_quote is not None else None
    clean_impression = _optional_text(
        subject_private_impression, field_name="subject_private_impression", max_len=200
    )
    clean_imp_shared = _require_bool(
        False if impression_shared is None else impression_shared,
        field_name="impression_shared",
    )

    seq = int(state.get("revision") or 0) + 1
    fact_id = _stable_id("npcfact", clean_npc_id, source_type, clean_source_ref or clean_summary, now_iso, seq)
    fact_entry: dict[str, Any] = {
        "fact_id": fact_id,
        "npc_id": clean_npc_id,
        "source_type": source_type,
        "epistemic_basis": resolved_basis,
        "hearsay_from": clean_hearsay or (normalized_sources[0] if normalized_sources else None),
        "hearsay_sources": normalized_sources,
        "corroboration_count": len(normalized_sources),
        "source_ref": clean_source_ref,
        "summary": clean_summary,
        "npc_quote": quote_record,
        "subject_private_impression": clean_impression,
        "impression_shared": clean_imp_shared,
        "known_at": now_iso,
        "revoked": False,
        "revoked_at": None,
    }
    facts = state["npc_ecology"]["facts"]
    facts.append(fact_entry)
    if len(facts) > MAX_NPC_FACTS:
        del facts[:-MAX_NPC_FACTS]
    return {"fact": copy.deepcopy(fact_entry)}


def apply_revoke_npc_shared_ref(
    state: dict[str, Any],
    *,
    source_ref: Any = None,
    fact_id: Any = None,
    npc_id: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    clean_sref = (
        _require_id_token(source_ref, field_name="source_ref", reason_code="INVALID_FIELDS")
        if source_ref is not None
        else None
    )
    clean_fid = (
        _require_id_token(fact_id, field_name="fact_id", reason_code="INVALID_FIELDS")
        if fact_id is not None
        else None
    )
    clean_nid = (
        _require_id_token(npc_id, field_name="npc_id", reason_code="INVALID_FIELDS")
        if npc_id is not None
        else None
    )
    if clean_sref is None and clean_fid is None:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS", "revoke_npc_shared_ref requires source_ref or fact_id", http_status=400
        )

    facts = state["npc_ecology"]["facts"]
    revoked_ids: list[str] = []
    for entry in facts:
        if clean_fid is not None and entry.get("fact_id") != clean_fid:
            continue
        if clean_sref is not None and entry.get("source_ref") != clean_sref:
            continue
        if clean_nid is not None and entry.get("npc_id") != clean_nid:
            continue
        entry["revoked"] = True
        entry["revoked_at"] = now_iso
        # Immediately scrub body summary & private impression from active memory surface
        entry["summary"] = None
        entry["subject_private_impression"] = None
        revoked_ids.append(str(entry["fact_id"]))

    if not revoked_ids:
        raise EmbodiedEcologyError(
            "SHARED_REF_NOT_FOUND",
            "No matching shared reference found in NPC knowledge ledger",
            http_status=404,
        )
    return {
        "revoked_fact_ids": revoked_ids,
        "revoked": True,
        "content_withheld_from_views": True,
    }


def _validate_hobby_axis(value: Any, *, field_name: str, allowed: tuple[str, ...], default_val: str) -> str:
    if value is None:
        return default_val
    if isinstance(value, (int, float, bool)) or not isinstance(value, str) or value not in allowed:
        raise EmbodiedEcologyError(
            "INVALID_HOBBY_AXIS",
            f"{field_name} must be a qualitative descriptor in {list(allowed)} (numeric XP/levels forbidden)",
            http_status=400,
        )
    return value


def apply_record_style_migration(
    state: dict[str, Any],
    *,
    style_id: Any = None,
    title: Any = None,
    origin_context: Any = None,
    origin_ref: Any = None,
    stage: Any,
    capability: Any = None,
    stability: Any = None,
    enjoyment: Any = None,
    work_ref: Any = None,
    private_note: Any = None,
    public_summary: Any = None,
    shared: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    if not isinstance(stage, str) or stage not in STYLE_STAGES:
        raise EmbodiedEcologyError(
            "INVALID_STYLE_STAGE",
            f"stage must be one of {list(STYLE_STAGES)}",
            http_status=400,
        )
    items = state["style_migration"]["items"]
    existing: Optional[dict[str, Any]] = None
    if style_id is not None:
        clean_sid = _require_id_token(style_id, field_name="style_id", reason_code="INVALID_FIELDS")
        existing = next((it for it in items if it.get("style_id") == clean_sid), None)
        if existing is None and title is None:
            raise EmbodiedEcologyError(
                "STYLE_ENTRY_NOT_FOUND", f"Style item {clean_sid!r} not found", http_status=404
            )
    else:
        clean_sid = None

    default_enjoyment = (
        "resting_for_now" if stage in ("shifted_or_cooled", "shelved_unfinished") else "spark_of_interest"
    )
    if existing is not None:
        if title is not None:
            existing["title"] = _clean_text(title, field_name="title", max_len=80)
        if origin_context is not None:
            if not isinstance(origin_context, str) or origin_context not in STYLE_ORIGIN_CONTEXTS:
                raise EmbodiedEcologyError(
                    "INVALID_FIELDS",
                    f"origin_context must be one of {list(STYLE_ORIGIN_CONTEXTS)}",
                    http_status=400,
                )
            existing["origin_context"] = origin_context
        if origin_ref is not None:
            existing["origin_ref"] = _require_id_token(origin_ref, field_name="origin_ref")
        if work_ref is not None:
            existing["work_ref"] = _require_id_token(work_ref, field_name="work_ref")
        if private_note is not None:
            existing["private_note"] = _optional_text(private_note, field_name="private_note", max_len=240)
        if public_summary is not None:
            existing["public_summary"] = _optional_text(
                public_summary, field_name="public_summary", max_len=200
            )
        if shared is not None:
            existing["shared"] = _require_bool(shared, field_name="shared")
        axes = existing.setdefault(
            "skill_three_axes",
            {
                "capability": "fumbling_beginner",
                "stability": "hit_or_miss",
                "enjoyment": default_enjoyment,
                "numeric_xp_forbidden": True,
                "level_bar_forbidden": True,
            },
        )
        if capability is not None:
            axes["capability"] = _validate_hobby_axis(
                capability, field_name="capability", allowed=HOBBY_CAPABILITY_LEVELS, default_val=axes["capability"]
            )
        if stability is not None:
            axes["stability"] = _validate_hobby_axis(
                stability, field_name="stability", allowed=HOBBY_STABILITY_LEVELS, default_val=axes["stability"]
            )
        if enjoyment is not None:
            axes["enjoyment"] = _validate_hobby_axis(
                enjoyment, field_name="enjoyment", allowed=HOBBY_ENJOYMENT_STATES, default_val=axes["enjoyment"]
            )
        elif stage in ("shifted_or_cooled", "shelved_unfinished"):
            axes["enjoyment"] = "resting_for_now"
        if existing["stage"] != stage:
            existing.setdefault("history", []).append(
                {"from_stage": existing["stage"], "to_stage": stage, "at": now_iso}
            )
            existing["stage"] = stage
        existing["shelved_unfinished"] = stage == "shelved_unfinished"
        existing["updated_at"] = now_iso
        return {"style_item": copy.deepcopy(existing)}

    clean_title = _clean_text(title, field_name="title", max_len=80)
    resolved_origin = "reading_note" if origin_context is None else origin_context
    if not isinstance(resolved_origin, str) or resolved_origin not in STYLE_ORIGIN_CONTEXTS:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"origin_context must be one of {list(STYLE_ORIGIN_CONTEXTS)}",
            http_status=400,
        )
    clean_origin_ref = (
        _require_id_token(origin_ref, field_name="origin_ref") if origin_ref is not None else None
    )
    clean_work_ref = (
        _require_id_token(work_ref, field_name="work_ref") if work_ref is not None else None
    )
    clean_priv = _optional_text(private_note, field_name="private_note", max_len=240)
    clean_pub = _optional_text(public_summary, field_name="public_summary", max_len=200)
    clean_shared = _require_bool(False if shared is None else shared, field_name="shared")
    cap_val = _validate_hobby_axis(
        capability, field_name="capability", allowed=HOBBY_CAPABILITY_LEVELS, default_val="fumbling_beginner"
    )
    stab_val = _validate_hobby_axis(
        stability, field_name="stability", allowed=HOBBY_STABILITY_LEVELS, default_val="hit_or_miss"
    )
    enj_val = _validate_hobby_axis(
        enjoyment, field_name="enjoyment", allowed=HOBBY_ENJOYMENT_STATES, default_val=default_enjoyment
    )

    seq = int(state.get("revision") or 0) + 1
    sid = clean_sid or _stable_id("style", clean_title, resolved_origin, now_iso, seq)
    new_item = {
        "style_id": sid,
        "title": clean_title,
        "origin_context": resolved_origin,
        "origin_ref": clean_origin_ref,
        "stage": stage,
        "shelved_unfinished": stage == "shelved_unfinished",
        "skill_three_axes": {
            "capability": cap_val,
            "stability": stab_val,
            "enjoyment": enj_val,
            "numeric_xp_forbidden": True,
            "level_bar_forbidden": True,
        },
        "work_ref": clean_work_ref,
        "private_note": clean_priv,
        "public_summary": clean_pub,
        "shared": clean_shared,
        "xp_tree_forbidden": True,
        "cooling_is_not_failure": True,
        "failure_penalty": 0,
        "history": [{"from_stage": None, "to_stage": stage, "at": now_iso}],
        "recorded_at": now_iso,
        "updated_at": now_iso,
    }
    items.append(new_item)
    if len(items) > MAX_STYLE_ITEMS:
        del items[:-MAX_STYLE_ITEMS]
    return {"style_item": copy.deepcopy(new_item)}


def apply_record_relationship_anchor(
    state: dict[str, Any],
    *,
    target_id: Any = "partner",
    anchor_category: Any,
    domain: Any = None,
    trust_stance: Any = None,
    boundary_statement: Any = None,
    object_kind: Any = None,
    label: Any = None,
    story_note: Any = None,
    phrase: Any = None,
    meaning_context: Any = None,
    secret_summary: Any = None,
    shared_with_partner: Any = True,
    now_iso: str,
) -> dict[str, Any]:
    """Ch. 01: Record non-numeric relationship anchors (trust_by_domain / boundaries / anchor_objects / shared_language / shared_secrets)."""
    clean_target = "partner" if target_id is None else _require_id_token(target_id, field_name="target_id")
    if not isinstance(anchor_category, str) or anchor_category not in RELATIONSHIP_ANCHOR_CATEGORIES:
        raise EmbodiedEcologyError(
            "INVALID_FIELDS",
            f"anchor_category must be one of {list(RELATIONSHIP_ANCHOR_CATEGORIES)}",
            http_status=400,
        )
    shared_flag = _require_bool(
        True if shared_with_partner is None else shared_with_partner, field_name="shared_with_partner"
    )
    ra_root = state.setdefault("relationship_anchors", _default_relationship_anchors())
    if clean_target == "partner":
        bucket = ra_root.setdefault("partner", _default_relationship_anchors()["partner"])
    else:
        neighbors_map = ra_root.setdefault("neighbors", {})
        bucket = neighbors_map.setdefault(
            clean_target,
            {
                "target_id": clean_target,
                "trust_by_domain": {},
                "boundaries": [],
                "anchor_objects": [],
                "shared_language": [],
                "shared_secrets": [],
            },
        )

    seq = int(state.get("revision") or 0) + 1
    if anchor_category == "trust_by_domain":
        if not isinstance(domain, str) or domain not in TRUST_DOMAINS:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", f"domain must be one of {list(TRUST_DOMAINS)}", http_status=400
            )
        if isinstance(trust_stance, (int, float, bool)):
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", "trust_stance must be a non-numeric qualitative string", http_status=400
            )
        clean_stance = _clean_text(trust_stance, field_name="trust_stance", max_len=80)
        bucket.setdefault("trust_by_domain", {})[domain] = clean_stance
        return {
            "target_id": clean_target,
            "anchor_category": anchor_category,
            "domain": domain,
            "trust_stance": clean_stance,
            "numeric_score_forbidden": True,
        }

    if anchor_category == "boundary":
        stmt = _clean_text(boundary_statement, field_name="boundary_statement", max_len=160)
        rec = {
            "boundary_id": _stable_id("bnd", clean_target, stmt, now_iso, seq),
            "statement": stmt,
            "shared_with_partner": shared_flag,
            "recorded_at": now_iso,
        }
        lst = bucket.setdefault("boundaries", [])
        lst.append(rec)
        if len(lst) > MAX_RELATIONSHIP_ANCHOR_ITEMS:
            del lst[:-MAX_RELATIONSHIP_ANCHOR_ITEMS]
        return {"target_id": clean_target, "anchor_category": anchor_category, "entry": copy.deepcopy(rec)}

    if anchor_category == "anchor_object":
        okind = "custom_object" if object_kind is None else object_kind
        if not isinstance(okind, str) or okind not in ANCHOR_OBJECT_KINDS:
            raise EmbodiedEcologyError(
                "INVALID_FIELDS", f"object_kind must be one of {list(ANCHOR_OBJECT_KINDS)}", http_status=400
            )
        clean_lbl = _clean_text(label, field_name="label", max_len=80)
        clean_story = _clean_text(story_note, field_name="story_note", max_len=200)
        rec = {
            "anchor_id": _stable_id("anchobj", clean_target, okind, clean_lbl, now_iso, seq),
            "object_kind": okind,
            "label": clean_lbl,
            "story_note": clean_story,
            "shared_with_partner": shared_flag,
            "recorded_at": now_iso,
        }
        lst = bucket.setdefault("anchor_objects", [])
        lst.append(rec)
        if len(lst) > MAX_RELATIONSHIP_ANCHOR_ITEMS:
            del lst[:-MAX_RELATIONSHIP_ANCHOR_ITEMS]
        return {"target_id": clean_target, "anchor_category": anchor_category, "entry": copy.deepcopy(rec)}

    if anchor_category == "shared_language":
        clean_p = _clean_text(phrase, field_name="phrase", max_len=60)
        clean_ctx = _clean_text(meaning_context, field_name="meaning_context", max_len=160)
        rec = {
            "phrase_id": _stable_id("lang", clean_target, clean_p, now_iso, seq),
            "phrase": clean_p,
            "meaning_context": clean_ctx,
            "shared_with_partner": shared_flag,
            "recorded_at": now_iso,
        }
        lst = bucket.setdefault("shared_language", [])
        lst.append(rec)
        if len(lst) > MAX_RELATIONSHIP_ANCHOR_ITEMS:
            del lst[:-MAX_RELATIONSHIP_ANCHOR_ITEMS]
        return {"target_id": clean_target, "anchor_category": anchor_category, "entry": copy.deepcopy(rec)}

    clean_sec = _clean_text(secret_summary, field_name="secret_summary", max_len=160)
    rec = {
        "secret_id": _stable_id("sec", clean_target, clean_sec, now_iso, seq),
        "summary": clean_sec,
        "shared_with_partner": shared_flag if clean_target == "partner" else False,
        "recorded_at": now_iso,
    }
    lst = bucket.setdefault("shared_secrets", [])
    lst.append(rec)
    if len(lst) > MAX_RELATIONSHIP_ANCHOR_ITEMS:
        del lst[:-MAX_RELATIONSHIP_ANCHOR_ITEMS]
    return {"target_id": clean_target, "anchor_category": anchor_category, "entry": copy.deepcopy(rec)}


# ---------------------------------------------------------------------------
# Pure zero-write read projection (subject vs partner_default redaction)
# ---------------------------------------------------------------------------


def _project_kitchen_zones(
    raw_zones: Mapping[str, Any],
    *,
    has_subject_capability: bool,
) -> dict[str, dict[str, Any]]:
    base_zones = _default_kitchen_zones()
    out: dict[str, dict[str, Any]] = {}
    for zid in KITCHEN_ZONE_IDS:
        z = copy.deepcopy(dict(raw_zones.get(zid) or base_zones[zid]))
        if zid == "sink":
            dirty_n = int(z.get("dirty_dishes") or 0)
            z["dirty_dishes"] = dirty_n
            z["blocks_new_plating"] = dirty_n >= SINK_DIRTY_DISHES_LIMIT
        elif zid == "chiyo_snack_drawer":
            partner_auth = bool(z.get("partner_authorized"))
            if has_subject_capability or partner_auth:
                z["status"] = "AVAILABLE"
                z["reason_code"] = None
            else:
                z = {
                    "zone_id": "chiyo_snack_drawer",
                    "label": "私人零食抽屉",
                    "visibility": "private",
                    "partner_authorized": False,
                    "status": "PRIVATE",
                    "reason_code": "PRIVATE_REFUSED",
                }
        out[zid] = z
    return out


def _project_relationship_anchors(
    raw_ra: Mapping[str, Any],
    *,
    has_subject_capability: bool,
) -> dict[str, Any]:
    p_raw = dict(raw_ra.get("partner") or _default_relationship_anchors()["partner"])
    partner_out = {
        "trust_by_domain": copy.deepcopy(dict(p_raw.get("trust_by_domain") or {})),
        "boundaries": [
            copy.deepcopy(b)
            for b in (p_raw.get("boundaries") or [])
            if has_subject_capability or bool(b.get("shared_with_partner", True))
        ],
        "anchor_objects": [
            copy.deepcopy(o)
            for o in (p_raw.get("anchor_objects") or [])
            if has_subject_capability or bool(o.get("shared_with_partner", True))
        ],
        "shared_language": [
            copy.deepcopy(l)
            for l in (p_raw.get("shared_language") or [])
            if has_subject_capability or bool(l.get("shared_with_partner", True))
        ],
        "shared_secrets": [
            copy.deepcopy(s)
            for s in (p_raw.get("shared_secrets") or [])
            if has_subject_capability or bool(s.get("shared_with_partner", True))
        ],
    }
    neighbors_out: dict[str, Any] = {}
    for nid, nb in sorted((raw_ra.get("neighbors") or {}).items()):
        if not isinstance(nb, Mapping):
            continue
        if has_subject_capability:
            neighbors_out[nid] = copy.deepcopy(dict(nb))
        else:
            neighbors_out[nid] = {
                "target_id": nid,
                "trust_by_domain": copy.deepcopy(dict(nb.get("trust_by_domain") or {})),
                "anchor_objects": [
                    copy.deepcopy(o)
                    for o in (nb.get("anchor_objects") or [])
                    if bool(o.get("shared_with_partner"))
                ],
            }
    return {
        "partner": partner_out,
        "neighbors": neighbors_out,
        "numeric_score_forbidden": True,
    }


def project_embodied_social_state(
    day_root: Path | str,
    *,
    state: Optional[Mapping[str, Any]] = None,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Build full deterministic projection with zero disk writes."""

    root = Path(day_root)
    now_dt = parse_iso_instant(now_iso, default_now=True)
    resolved_now = format_iso(now_dt)
    loaded = dict(state) if state is not None else load_state_readonly(root)
    char_info = read_character_identity_readonly(root)
    theme_info = detect_world_theme_readonly(root)

    # ---- Kitchen projection ----
    pantry_map = loaded["kitchen"]["pantry"]
    pantry_list: list[dict[str, Any]] = []
    conservation_ok = True
    total_units = 0
    for iid in sorted(pantry_map.keys()):
        row = copy.deepcopy(pantry_map[iid])
        expected_u = int(row["total_restocked"]) - int(row["total_cooked"])
        row_ok = int(row["units"]) >= 0 and int(row["units"]) == expected_u
        if not row_ok:
            conservation_ok = False
        row["conservation_holds"] = row_ok
        total_units += int(row["units"])
        pantry_list.append(row)

    projected_dishes: list[dict[str, Any]] = []
    for dish in loaded["kitchen"]["cooked_dishes"]:
        d_copy = copy.deepcopy(dish)
        d_copy.update(derive_dish_thermal_and_freshness(dish, now_dt))
        projected_dishes.append(d_copy)

    projected_zones = _project_kitchen_zones(
        loaded["kitchen"].get("zones") or _default_kitchen_zones(),
        has_subject_capability=has_subject_capability,
    )
    projected_appliances = copy.deepcopy(
        loaded["kitchen"].get("appliances") or _default_kitchen_appliances()
    )

    kitchen_block = {
        "pantry_items": pantry_list,
        "pantry_by_id": {row["ingredient_id"]: row for row in pantry_list},
        "total_pantry_units": total_units,
        "integer_conservation_holds": conservation_ok,
        "negative_stock_impossible": True,
        "conjures_food_from_thin_air": False,
        "zones": projected_zones,
        "zone_count": len(projected_zones),
        "appliances": projected_appliances,
        "cooked_dishes": projected_dishes,
        "consumption_receipts": copy.deepcopy(loaded["kitchen"]["consumption_receipts"]),
        "discard_receipts": copy.deepcopy(loaded["kitchen"]["discard_receipts"]),
    }

    # ---- Embodied contact & hand slots projection ----
    c_dom = loaded["embodied_contact"]
    ext_hand_locks = read_containment_hand_locks_readonly(root)
    derived_contact = derive_contact_avatar_and_bed_readonly(
        c_dom, now_dt, external_hand_locks=ext_hand_locks
    )
    presence = derived_contact["effective_presence"]
    hand_slots = derived_contact["hand_slots"]
    pending_prop = derived_contact["pending_proposal"]
    pending_initiator = (
        str(pending_prop.get("proposed_by") or pending_prop.get("initiator"))
        if isinstance(pending_prop, Mapping) and (pending_prop.get("proposed_by") or pending_prop.get("initiator"))
        else None
    )
    free_hands_count = sum(
        1 for sname in HAND_SLOT_NAMES if hand_slots[sname].get("state") == "free"
    )
    non_object_hands_count = sum(
        1 for sname in HAND_SLOT_NAMES if hand_slots[sname].get("state") != "holding_object"
    )
    contact_block = {
        "partner_presence": presence,
        "within_reach": presence in REACHABLE_PRESENCE_MODES,
        "presence_updated_at": c_dom.get("presence_updated_at"),
        "copresence_session": derived_contact["copresence_session"],
        "user_avatar_proxy": derived_contact["user_avatar_proxy"],
        "bed_physics": derived_contact["bed_physics"],
        "subject_foreground_activity": c_dom.get("subject_foreground_activity"),
        "foreground_activity_interrupted": False,
        "spawns_second_foreground": False,
        "hand_slots": hand_slots,
        "external_hand_locks": ext_hand_locks,
        "free_hand_count": free_hands_count,
        "non_object_hand_count": non_object_hands_count,
        "placed_down_objects": copy.deepcopy(c_dom.get("placed_down_objects") or {}),
        "contact_hand_requirements": dict(CONTACT_HAND_REQUIREMENTS),
        "contact_max_duration_seconds": CONTACT_MAX_DURATION_SECONDS,
        "subject_posture": copy.deepcopy(c_dom["subject_posture"]),
        "pending_initiator": pending_initiator,
        "pending_proposal": pending_prop,
        "active_contact": derived_contact["active_contact"],
        "last_contact_event": derived_contact["last_contact_event"],
        "rigid_phase_ladder_forbidden": True,
        "appraisal_excluded": True,
        "unilateral_release_immediate": True,
        "release_penalty": 0,
    }

    # ---- Town NPC ecology & privacy firewall projection ----
    npcs = project_town_npcs(theme_info, now_dt)
    slow_lifecycles = derive_slow_lifecycles_readonly(
        loaded["npc_ecology"].get("slow_lifecycles") or _default_slow_lifecycles(),
        now_dt,
    )
    projected_commitments: list[dict[str, Any]] = []
    for raw_c in loaded["npc_ecology"].get("commitments") or []:
        c_copy = copy.deepcopy(raw_c)
        if c_copy.get("status") == "scheduled":
            end_dt = parse_iso_instant(c_copy["window_end_iso"])
            if now_dt > end_dt:
                c_copy["derived_status"] = "window_passed_unfulfilled"
            else:
                c_copy["derived_status"] = "scheduled"
        else:
            c_copy["derived_status"] = c_copy.get("status")
        projected_commitments.append(c_copy)

    projected_cards = [
        copy.deepcopy(card)
        for card in (loaded["npc_ecology"].get("bookstore_cards") or [])
        if has_subject_capability or bool(card.get("shared_on_board", True))
    ]

    visible_facts: list[dict[str, Any]] = []
    active_facts: list[dict[str, Any]] = []
    for raw_fact in loaded["npc_ecology"]["facts"]:
        is_revoked = bool(raw_fact.get("revoked"))
        fact_out: dict[str, Any] = {
            "fact_id": raw_fact["fact_id"],
            "npc_id": raw_fact["npc_id"],
            "source_type": raw_fact["source_type"],
            "epistemic_basis": raw_fact.get("epistemic_basis", "firsthand_seen"),
            "hearsay_from": raw_fact.get("hearsay_from"),
            "hearsay_sources": list(raw_fact.get("hearsay_sources") or []),
            "corroboration_count": int(raw_fact.get("corroboration_count") or 0),
            "source_ref": raw_fact.get("source_ref"),
            "known_at": raw_fact["known_at"],
            "revoked": is_revoked,
            "revoked_at": raw_fact.get("revoked_at"),
        }
        if is_revoked:
            fact_out["summary"] = None
            fact_out["npc_quote"] = None
            fact_out["content_status"] = "withheld_revoked"
        else:
            fact_out["summary"] = raw_fact.get("summary")
            fact_out["npc_quote"] = copy.deepcopy(raw_fact.get("npc_quote"))
            fact_out["content_status"] = "available"
            if has_subject_capability:
                fact_out["subject_private_impression"] = raw_fact.get("subject_private_impression")
                fact_out["impression_shared"] = bool(raw_fact.get("impression_shared"))
            elif raw_fact.get("impression_shared") and raw_fact.get("subject_private_impression"):
                fact_out["shared_impression"] = raw_fact.get("subject_private_impression")

        visible_facts.append(fact_out)
        if not is_revoked:
            active_facts.append(fact_out)

    npc_block = {
        "world_theme": theme_info["theme"],
        "world_theme_label": THEME_LABELS.get(theme_info["theme"], "温和街区"),
        "theme_source": theme_info["source"],
        "world_id": theme_info.get("world_id"),
        "npcs": npcs,
        "npc_count": len(npcs),
        "slow_lifecycles": slow_lifecycles,
        "commitments": projected_commitments,
        "bookstore_cards": projected_cards,
        "known_facts": visible_facts,
        "active_facts": active_facts,
        "privacy_firewall": {
            "diary_isolated": True,
            "draft_isolated": True,
            "partner_private_chat_isolated": True,
            "adult_36b_intimacy_isolated": True,
            "npc_quote_text_origin": UNTRUSTED_NPC_TEXT_ORIGIN,
            "npc_speech_can_escalate_privilege": False,
        },
    }

    # ---- Personal style migration projection ----
    stored_styles = loaded["style_migration"]["items"]
    visible_styles: list[dict[str, Any]] = []
    for item in stored_styles:
        is_shared = bool(item.get("shared"))
        if not has_subject_capability and not is_shared:
            continue
        default_axes = {
            "capability": "fumbling_beginner",
            "stability": "hit_or_miss",
            "enjoyment": "resting_for_now" if item["stage"] in ("shifted_or_cooled", "shelved_unfinished") else "spark_of_interest",
            "numeric_xp_forbidden": True,
            "level_bar_forbidden": True,
        }
        out_item: dict[str, Any] = {
            "style_id": item["style_id"],
            "title": item["title"],
            "origin_context": item["origin_context"],
            "stage": item["stage"],
            "shelved_unfinished": item["stage"] == "shelved_unfinished",
            "skill_three_axes": copy.deepcopy(item.get("skill_three_axes") or default_axes),
            "public_summary": item.get("public_summary"),
            "shared": is_shared,
            "xp_tree_forbidden": True,
            "cooling_is_not_failure": True,
            "failure_penalty": 0,
            "recorded_at": item["recorded_at"],
            "updated_at": item["updated_at"],
        }
        if has_subject_capability:
            out_item["origin_ref"] = item.get("origin_ref")
            out_item["work_ref"] = item.get("work_ref")
            out_item["private_note"] = item.get("private_note")
            out_item["history"] = copy.deepcopy(item.get("history") or [])
        visible_styles.append(out_item)

    style_block: dict[str, Any] = {
        "items": visible_styles,
        "visible_count": len(visible_styles),
        "stages_supported": list(STYLE_STAGES),
        "hobby_axes_supported": {
            "capability": list(HOBBY_CAPABILITY_LEVELS),
            "stability": list(HOBBY_STABILITY_LEVELS),
            "enjoyment": list(HOBBY_ENJOYMENT_STATES),
        },
        "xp_tree_forbidden": True,
        "cooling_is_not_failure": True,
        "no_nagging_reminder": True,
    }
    if has_subject_capability:
        style_block["total_count"] = len(stored_styles)

    relationship_block = _project_relationship_anchors(
        loaded.get("relationship_anchors") or _default_relationship_anchors(),
        has_subject_capability=has_subject_capability,
    )

    return {
        "schema": ECOLOGY_SCHEMA,
        "view": "subject" if has_subject_capability else "partner_default",
        "observed_at": resolved_now,
        "revision": int(loaded.get("revision") or 0),
        "character": char_info,
        "kitchen": kitchen_block,
        "embodied_contact": contact_block,
        "npc_ecology": npc_block,
        "town_npcs": copy.deepcopy(npc_block),
        "style_migration": style_block,
        "relationship_anchors": relationship_block,
    }


__all__ = [
    "ANCHOR_OBJECT_KINDS",
    "AVATAR_POSES",
    "BED_WARMTH_HALF_LIFE_SECONDS",
    "BOOKSTORE_FEEDBACK_KINDS",
    "CONTACT_DECISIONS",
    "CONTACT_HAND_REQUIREMENTS",
    "CONTACT_KINDS",
    "CONTACT_MAX_DURATION_SECONDS",
    "CoPresenceAdapter",
    "ConsumeReceipt",
    "DEFAULT_WORLD_THEME",
    "DiscardReceipt",
    "ECOLOGY_SCHEMA",
    "EPISTEMIC_BASES",
    "EmbodiedEcologyError",
    "HAND_SLOT_INPUT_TARGETS",
    "HAND_SLOT_NAMES",
    "HOBBY_CAPABILITY_LEVELS",
    "HOBBY_ENJOYMENT_STATES",
    "HOBBY_STABILITY_LEVELS",
    "KITCHEN_APPLIANCE_IDS",
    "KITCHEN_ZONE_IDS",
    "KNOWN_WORLD_THEMES",
    "KitchenAppliance",
    "NPCCommitmentProposal",
    "NPC_COMMITMENT_OUTCOMES",
    "NPC_SOURCE_TYPES",
    "PARTNER_PRESENCE_MODES",
    "REACHABLE_PRESENCE_MODES",
    "REFRIGERATED_MEAL_SPOIL_SECONDS",
    "RELATIONSHIP_ANCHOR_CATEGORIES",
    "SERVING_KINDS",
    "SINK_DIRTY_DISHES_LIMIT",
    "STARTER_PANTRY_CATALOG",
    "STATE_FILENAME",
    "STYLE_ORIGIN_CONTEXTS",
    "STYLE_STAGES",
    "TEMPERATURE_CURVE",
    "THEME_NPC_BLUEPRINTS",
    "TRUST_DOMAINS",
    "UNTRUSTED_NPC_TEXT_ORIGIN",
    "UserAvatarProxy",
    "_canonical_digest",
    "_cps_session_id",
    "apply_consume_dish",
    "apply_cook_or_brew",
    "apply_discard_dish",
    "apply_exit_copresence",
    "apply_feed_stray_cat",
    "apply_inspect_kitchen_zone",
    "apply_operate_kitchen_appliance",
    "apply_propose_contact",
    "apply_propose_npc_commitment",
    "apply_record_npc_knowledge",
    "apply_record_relationship_anchor",
    "apply_record_style_migration",
    "apply_refrigerate_dish",
    "apply_reheat_dish",
    "apply_release_contact",
    "apply_resolve_npc_commitment",
    "apply_respond_contact",
    "apply_restock_pantry",
    "apply_revoke_npc_shared_ref",
    "apply_set_hand_slot",
    "apply_set_partner_presence",
    "apply_set_snack_drawer_permission",
    "apply_set_subject_contact_posture",
    "apply_submit_bookstore_card_feedback",
    "apply_tidy_bed",
    "apply_wash_dishes",
    "default_state",
    "derive_contact_avatar_and_bed_readonly",
    "derive_dish_thermal_and_freshness",
    "derive_slow_lifecycles_readonly",
    "detect_world_theme_readonly",
    "enforce_npc_privacy_firewall",
    "format_iso",
    "is_valid_cps_session_id",
    "load_state_readonly",
    "parse_iso_instant",
    "project_embodied_social_state",
    "project_town_npcs",
    "read_character_identity_readonly",
    "read_containment_hand_locks_readonly",
    "sanitize_npc_quote",
    "save_state_atomic",
    "settle_elapsed_contact_and_avatar",
    "state_file_path",
    "sync_containment_hand_slots_if_present",
]
