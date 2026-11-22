#!/usr/bin/env python3
"""零预设种子驱动小镇与住居合成器（#rm-random-world）。

仅凭 ``seed: int``（无需人工挑选预设主题），确定性地组合出一座在地理微气候、
路网拓扑骨架、商铺与地标构成、以及多分区住宅室内结构上都具备真实差异的小镇蓝图：

* ``biome_mix``：按种子混合两种互补地理风貌（海岬坡道、运河水巷、山麓溪谷、
  铁道驿町、果园台地、林间湖畔），同步派生命名词根、微气候天气、环境声景与风味句；
* ``street_layout_style``：按种子在四种连通道路拓扑（``star_hub`` / ``ring_loop`` /
  ``linear_promenade`` / ``forked_avenue``）中选择；
* 商铺与地标混编：保底 ``home``、``school`` 与至少 3 家不同业态商铺，搭配地标设施，
  所有商铺均携带合理营业时段、``affordances``、可售 ``sale_spec`` 物件与物质档；
* 多分区住宅布局：除规范主区域 ``bedroom``（床、书桌、带木栓私密抽屉、书架）外，
  按种子组合 1–2 个附加起居分区（``living`` / ``veranda`` / ``kitchenette`` /
  ``study_nook``），配套冰箱（冷藏）、煮水壶（加热）、洗手池（水源）与九键物质档物件。

全程纯标准库确定性计算，零 LLM 调用。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Optional

from . import GENERATOR_VERSION

PROCEDURAL_THEME = "procedural"
PROCEDURAL_THEME_ALIASES = ("procedural", "freeform", "auto")

STREET_LAYOUT_STYLES = (
    "star_hub",
    "ring_loop",
    "linear_promenade",
    "forked_avenue",
)

COMMERCIAL_VENUE_IDS = (
    "convenience_store",
    "cafe",
    "bookstore",
    "bakery",
    "flower_shop",
    "fish_market",
    "craft_shop",
    "home_goods_store",
)

EXTRA_HOME_AREA_IDS = (
    "living",
    "veranda",
    "kitchenette",
    "study_nook",
)

_EXTRA_HOME_COMBINATIONS: tuple[tuple[str, ...], ...] = (
    ("living",),
    ("veranda",),
    ("kitchenette",),
    ("study_nook",),
    ("living", "kitchenette"),
    ("living", "veranda"),
    ("kitchenette", "veranda"),
    ("living", "study_nook"),
    ("study_nook", "veranda"),
    ("kitchenette", "study_nook"),
)

_HOME_AREA_CATALOG: dict[str, dict[str, str]] = {
    "living": {
        "name": "起居室",
        "description": "铺着木地板的起居室，矮桌旁留着晒得到午后日光的靠椅。",
    },
    "veranda": {
        "name": "缘侧晒台",
        "description": "半敞开的木质缘侧晒台，檐下挂着风铃，栏杆边摆着耐阴绿植。",
    },
    "kitchenette": {
        "name": "小茶水厨间",
        "description": "紧凑整洁的料理茶水角，小冰箱、煮水壶与洗手水槽一字排开。",
    },
    "study_nook": {
        "name": "书斋角",
        "description": "靠内墙围出的安静书角，木格书架上整齐码着地方札记与手作纸匣。",
    },
}

#: 六种互补地理微气候特征。
BIOME_TRAITS: dict[str, dict[str, Any]] = {
    "headland_slope": {
        "id": "headland_slope",
        "label": "海岬坡道",
        "prefixes": ["汐", "凪", "鸥", "潮", "青岬", "白浪"],
        "suffixes": ["岬", "坂", "浦", "滨町", "望台"],
        "streets": ["望潮坂", "防波堤通", "海风石阶", "灯影小路", "白鸥坡"],
        "zone": {"zone_id": "waterfront", "name": "海岬岸区", "color": "#6ba2bf"},
        "weather": [
            {"condition": "sea_breeze", "description": "带着咸味的海风晴天"},
            {"condition": "coastal_mist", "description": "清晨漫过坡道的薄海雾"},
        ],
        "ambient": [
            {
                "event_type": "cape_foghorn_echo",
                "slot": "home",
                "start_hour": 6,
                "end_hour": 10,
                "summaries": [
                    "清晨海岬方向传来低沉悠远的航标鸣笛",
                    "窗缝里吹进微咸的早潮空气",
                ],
            },
            {
                "event_type": "slope_breeze_chime",
                "slot": "street",
                "start_hour": 15,
                "end_hour": 19,
                "summaries": [
                    "傍晚海风沿着石板坡道吹响檐下风铃",
                    "坡顶栏杆旁聚集了看晚潮归船的居民",
                ],
            },
        ],
        "intro_clauses": [
            "石板坡道层层叠向海岬，拐角处总能望见远处的防波堤与航标灯",
            "受海湾暖流影响，冬日少雪而晨间常有轻雾漫过屋檐",
        ],
        "festivals": ["仲夏海岬灯标夜", "秋分潮汐市集"],
        "street_flavor": [
            "石阶转角的铸铁栏杆被海风吹出温润的暗色",
            "坡道中段的凸面镜映出远处的海平面与云影",
            "路旁矮墙上嵌着记录历年潮位线的铜牌",
        ],
        "zone_flavor": [
            "防波堤外侧浪花拍岸的节拍在安静的午后格外清晰",
            "坡道边的邮筒顶上停着歇脚的海鸟",
            "石墙缝里开着耐盐风的滨昼颜小花",
            "朝南的屋檐下挂着晒干的海藻标本框",
            "黄昏时海面反光会把整条坡道染成琥珀色",
            "巷口温度计旁贴着今日涨退潮时刻便签",
            "台阶边缘被往来行人的鞋底磨得圆滑",
            "微咸的晚风里混着远处港湾修船木料的松香",
        ],
        "home_names": ["望岬坡的住处", "听潮石阶旁的住居"],
        "home_vista": "推窗能看见坡道尽头发亮的海湾水面",
        "shop_weights": {"fish_market": 4, "cafe": 3, "flower_shop": 2},
        "landmarks": [
            {
                "id": "beach",
                "slot": "beach",
                "kind": "beach",
                "zone": "waterfront",
                "weight": 3,
                "open_hour": 5,
                "close_hour": 21,
                "weekdays_only": False,
                "names": ["{world_name}砾石滩", "月牙浅湾", "潮音海滨"],
                "descriptions": ["退潮后会露出平整砂砾的海湾浅滩，适合傍晚散步听浪。"],
                "affordances": [
                    ["WALK", "沿着海岸线慢慢走一段", {"title": "沿浅湾散步", "suggested_seconds": 600}],
                    ["SIT", "在防浪石阶上坐一会儿", {"title": "在海边石阶小坐", "suggested_seconds": 300}],
                    ["LOOK_AROUND", "眺望海岬外侧的航道"],
                ],
            },
            {
                "id": "cape_lighthouse",
                "slot": "cape_lighthouse",
                "kind": "public",
                "zone": "waterfront",
                "weight": 2,
                "open_hour": 8,
                "close_hour": 18,
                "weekdays_only": False,
                "names": ["{world_name}白塔", "望潮灯塔"],
                "descriptions": ["伫立在海岬高处的白色航标塔，塔下小院对外开放。"],
                "affordances": [
                    ["LOOK_AROUND", "凭栏俯瞰整片海湾与坡道屋顶"],
                    ["SIT", "在灯塔背风的长椅上歇脚"],
                ],
            },
        ],
    },
    "canal_waterway": {
        "id": "canal_waterway",
        "label": "运河水巷",
        "prefixes": ["澪", "柳", "澄川", "水桥", "绫", "清渠"],
        "suffixes": ["津", "水町", "桥里", "川畔", "渡"],
        "streets": ["石桥水廊", "柳荫河沿", "船坞通", "水闸小路", "拱桥东街"],
        "zone": {"zone_id": "waterfront", "name": "运河水岸区", "color": "#5ea8a7"},
        "weather": [
            {"condition": "canal_haze", "description": "水面泛起柔光薄霭"},
            {"condition": "gentle_rain", "description": "在水巷石板激起涟漪的细雨"},
        ],
        "ambient": [
            {
                "event_type": "canal_sluice_flow",
                "slot": "street",
                "start_hour": 7,
                "end_hour": 11,
                "summaries": [
                    "早间水闸缓缓开启，运河水位泛起轻柔水声",
                    "平底运货小船穿过石拱桥洞",
                ],
            },
            {
                "event_type": "waterway_lantern_reflection",
                "slot": "home",
                "start_hour": 18,
                "end_hour": 23,
                "summaries": [
                    "窗外水巷点亮岸灯，水面晃动着碎金似的倒影",
                    "夜风送来河岸柳叶与水草的清凉气息",
                ],
            },
        ],
        "intro_clauses": [
            "古老的石砌运河穿镇而过，几座低矮石拱桥把两岸街巷连成一体",
            "水巷两侧保留着木格窗商铺与亲水埠头，空气常年湿润清朗",
        ],
        "festivals": ["初夏运河放灯夜", "秋日水岸古书市"],
        "street_flavor": [
            "石拱桥栏杆上留着绳缆长年摩挲出的浅槽",
            "水巷岸壁每隔十步就有一处系船铁环与青苔石阶",
            "柳枝低垂到离运河水面只有一掌高的地方",
        ],
        "zone_flavor": [
            "清澈的渠水里能数清顺流游过的小溪鱼",
            "亲水石埠旁摆着居民浇花用的长柄木杓",
            "桥洞下回荡着清脆而缓慢的桨声与水滴声",
            "沿岸咖啡座的遮阳篷倒映在微波起伏的河面上",
            "老式水位标尺立在桥墩旁，刻度字迹依旧清晰",
            "雨后石板路很快沥干，水珠顺着暗渠汇入运河",
            "水闸看守小屋窗前挂着晴雨风向标",
            "傍晚归巢的白鹭偶尔掠过低矮的木桥栏杆",
        ],
        "home_names": ["临渠石桥畔的住处", "水巷柳荫下的住居"],
        "home_vista": "窗外不远处就是缓缓流淌的石砌运河与小拱桥",
        "shop_weights": {"cafe": 4, "bookstore": 3, "flower_shop": 3, "fish_market": 2},
        "landmarks": [
            {
                "id": "boat_landing",
                "slot": "boat_landing",
                "kind": "public",
                "zone": "waterfront",
                "weight": 3,
                "open_hour": 6,
                "close_hour": 21,
                "weekdays_only": False,
                "names": ["{world_name}石埠头", "旧船渠亲水台"],
                "descriptions": ["贴近运河水面的宽阔石砌埠头，岸边设有木长椅。"],
                "affordances": [
                    ["SIT", "坐在石埠木椅上看水流经过", {"title": "在水岸石埠小坐", "suggested_seconds": 300}],
                    ["LOOK_AROUND", "观察运河对岸的街景与水鸟"],
                ],
            },
            {
                "id": "arched_bridge_garden",
                "slot": "arched_bridge_garden",
                "kind": "park",
                "zone": "waterfront",
                "weight": 2,
                "open_hour": 6,
                "close_hour": 22,
                "weekdays_only": False,
                "names": ["双桥水岸绿地", "{world_name}河心小苑"],
                "descriptions": ["两座石桥合围出的亲水小绿地，种着菖蒲与垂柳。"],
                "affordances": [
                    ["WALK", "绕着河岸步道散步"],
                    ["PEOPLE_WATCH", "看桥上来往的行人与单车"],
                ],
            },
        ],
    },
    "foothill_valley": {
        "id": "foothill_valley",
        "label": "山麓溪谷",
        "prefixes": ["翠", "岚", "溪", "枫", "云泽", "青岭"],
        "suffixes": ["谷", "泽町", "山麓", "溪里", "汤坂"],
        "streets": ["溪声步道", "杉影坂", "山麓本通", "苔石小径", "清泉街"],
        "zone": {"zone_id": "highland", "name": "山麓溪谷区", "color": "#6f9e7b"},
        "weather": [
            {"condition": "valley_breeze", "description": "夹带杉木清香的凉爽山风"},
            {"condition": "mountain_shower", "description": "来去干脆的山麓阵雨"},
        ],
        "ambient": [
            {
                "event_type": "valley_creek_murmur",
                "slot": "home",
                "start_hour": 0,
                "end_hour": 24,
                "summaries": [
                    "安静时屋里能听见山麓清溪越过卵石的潺潺水声",
                    "山风拂过屋后杉林发出沙沙低响",
                ],
            },
            {
                "event_type": "footbath_steam_rise",
                "slot": "street",
                "start_hour": 7,
                "end_hour": 12,
                "summaries": [
                    "街角引泉水渠在晨凉中升起淡淡白气",
                    "背着竹篓的居民沿着石阶下山赶早集",
                ],
            },
        ],
        "intro_clauses": [
            "小镇依偎在苍翠山麓与清浅溪谷之间，木造屋舍顺着缓坡错落展开",
            "山间清泉顺着街边明渠流淌，盛夏午后也带着几分沁凉",
        ],
        "festivals": ["新绿山麓徒步祭", "深秋红叶灯笼夜"],
        "street_flavor": [
            "街边明渠里引来了清冽山泉，石槽旁放着竹水瓢",
            "路灯柱采用温润的杉木包边，顶着防雨小铜笠",
            "坡道拐角立着标示山道海拔与步数的木桩",
        ],
        "zone_flavor": [
            "杉木林间漏下的光斑随着山风在路面上轻轻晃动",
            "溪谷石桥下水色清透，卵石间偶有溪蟹爬过",
            "杂货铺屋檐下成串挂着风干的松果与山草束",
            "清晨山雾散去后，远近山脊的轮廓像水墨一样分明",
            "手工作坊门口堆着散发清香的桧木刨花",
            "微凉的夜气里能闻到泥土、苔藓与落叶的干净气味",
            "公共足汤亭旁整齐摆着供路人擦干用的小木凳",
            "午后山谷里偶尔回荡着林间啄木鸟的笃笃声",
        ],
        "home_names": ["山麓听溪的住处", "杉影坡下的住居"],
        "home_vista": "窗外正对层叠的翠绿山麓与清浅溪流",
        "shop_weights": {"craft_shop": 4, "bookstore": 3, "home_goods_store": 3, "bakery": 2},
        "landmarks": [
            {
                "id": "spring_pavilion",
                "slot": "spring_pavilion",
                "kind": "public",
                "zone": "highland",
                "weight": 3,
                "open_hour": 7,
                "close_hour": 21,
                "weekdays_only": False,
                "names": ["{world_name}足汤亭", "清溪暖泉廊"],
                "descriptions": ["引山麓微温泉水建成的半露天木亭，旅人与邻里常在此歇脚。"],
                "affordances": [
                    ["REST", "在暖泉木亭边放松歇息", {"title": "在泉亭歇脚", "suggested_seconds": 420, "execution": {"mechanism": "stay_at_place"}}],
                    ["SIT", "坐在木廊边听溪水声"],
                ],
            },
            {
                "id": "cedar_trail",
                "slot": "cedar_trail",
                "kind": "park",
                "zone": "highland",
                "weight": 2,
                "open_hour": 6,
                "close_hour": 19,
                "weekdays_only": False,
                "names": ["杉林溪谷步道", "{world_name}山麓径"],
                "descriptions": ["沿溪流铺设的平缓木栈散步道，两侧高大杉木遮出清凉绿荫。"],
                "affordances": [
                    ["WALK", "沿着杉林木栈道散步", {"title": "走一段山麓步道", "suggested_seconds": 600}],
                    ["LOOK_AROUND", "在观溪平台驻足张望"],
                ],
            },
        ],
    },
    "rail_junction": {
        "id": "rail_junction",
        "label": "铁道驿町",
        "prefixes": ["铃", "星轨", "朝霞", "钟台", "若叶", "霞"],
        "suffixes": ["驿町", "本町", "通", "站前", "交叉点"],
        "streets": ["站前拱廊街", "铁道北通", "踏切横丁", "钟塔通", "砖墙小路"],
        "zone": {"zone_id": "railside", "name": "铁道驿前区", "color": "#8b86b5"},
        "weather": [
            {"condition": "crisp_clear", "description": "能望见远方电车架线的晴空"},
            {"condition": "evening_glow", "description": "把铁轨染成金红色的晚霞多云"},
        ],
        "ambient": [
            {
                "event_type": "level_crossing_bell",
                "slot": "street",
                "start_hour": 7,
                "end_hour": 21,
                "summaries": [
                    "远处平交道口响起清脆规律的叮当提示音，两节编组电车缓缓驶过",
                    "站前钟塔整点报时，拱廊下行人步履从容",
                ],
            },
            {
                "event_type": "distant_train_rhythm",
                "slot": "home",
                "start_hour": 6,
                "end_hour": 22,
                "summaries": [
                    "每隔半小时能隐约听见支线小电车掠过铁桥的轻快节拍",
                    "傍晚放学与归家的人流让巷口热闹了一阵又归于安静",
                ],
            },
        ],
        "intro_clauses": [
            "单线地方铁道在镇心划出柔和弧线，复古砖造拱廊与平交道口构成了日常街景",
            "每逢两节车厢的慢车靠站，站前小广场便漾起恰到好处的生活气息",
        ],
        "festivals": ["铁道纪念日夜市", "岁末拱廊星灯会"],
        "street_flavor": [
            "玻璃顶拱廊下挂着报时挂钟与手绘商户导览图",
            "平交道口涂着醒目的黄黑条纹，栏杆起落干净利落",
            "旧枕木围成的路边花箱里开满当季矮牵牛",
        ],
        "zone_flavor": [
            "单线电车的架空电线在夕阳下划出细长的金色线条",
            "站前报刊亭旁立着复古圆筒邮筒与长途时刻牌",
            "红砖高架拱洞下改造成了避雨的社区布告廊",
            "面包房出炉时间总与早班电车到站只差五分钟",
            "旧信号灯被保留在街角小花园里当作纪念摆设",
            "自行车停放架排列得整整齐齐，车铃偶尔轻响一声",
            "拱廊地砖在雨天依然干爽，脚步声清脆回响",
            "夜班慢车驶过之后，镇子便进入格外安稳的静谧时刻",
        ],
        "home_names": ["铁道拱廊旁的住处", "钟塔街角的住居"],
        "home_vista": "从阳台能望见不远处单线电车缓缓滑过绿篱的身影",
        "shop_weights": {"convenience_store": 4, "bakery": 4, "bookstore": 3, "cafe": 3},
        "landmarks": [
            {
                "id": "crossing_terrace",
                "slot": "crossing_terrace",
                "kind": "public",
                "zone": "railside",
                "weight": 3,
                "open_hour": 6,
                "close_hour": 22,
                "weekdays_only": False,
                "names": ["踏切观景小台", "{world_name}铁桥望台"],
                "descriptions": ["紧邻单线铁道弯道的木造小看台，能近距离看慢车驶过。"],
                "affordances": [
                    ["LOOK_AROUND", "看两节编组的地方电车经过弯道"],
                    ["PEOPLE_WATCH", "坐在长椅上看站前广场来往行人"],
                ],
            },
            {
                "id": "arcade_plaza",
                "slot": "arcade_plaza",
                "kind": "public",
                "zone": "railside",
                "weight": 2,
                "open_hour": 6,
                "close_hour": 23,
                "weekdays_only": False,
                "names": ["{world_name}钟塔广场", "红砖拱廊中庭"],
                "descriptions": ["拱廊街交汇处的半室内小广场，中央立着老式四面钟。"],
                "affordances": [
                    ["SIT", "在钟塔下的木长椅小坐", {"title": "在钟塔下小坐", "suggested_seconds": 240}],
                    ["WALK", "穿过红砖拱廊散步"],
                ],
            },
        ],
    },
    "orchard_terrace": {
        "id": "orchard_terrace",
        "label": "果园台地",
        "prefixes": ["柑", "晴", "穗", "杏", "向阳", "蜜丘"],
        "suffixes": ["台", "丘町", "果里", "原", "阳坡"],
        "streets": ["向阳果园坡", "石垣横道", "风车台通", "晴丘本街", "蜜柑小路"],
        "zone": {"zone_id": "terrace", "name": "向阳果园区", "color": "#c99b5b"},
        "weather": [
            {"condition": "sunny_terrace", "description": "日照充足、微风干爽的晴天"},
            {"condition": "warm_cloud", "description": "云影缓缓掠过台地的柔和多云"},
        ],
        "ambient": [
            {
                "event_type": "orchard_sweet_wind",
                "slot": "home",
                "start_hour": 9,
                "end_hour": 17,
                "summaries": [
                    "向阳坡吹来的暖风里带着果树枝叶与成熟果实的清甜气味",
                    "午后阳光斜照在台地矮石墙上，暖意融融",
                ],
            },
            {
                "event_type": "terrace_cart_passing",
                "slot": "street",
                "start_hour": 8,
                "end_hour": 14,
                "summaries": [
                    "装着木箱鲜果的小推车沿着缓坡石道经过",
                    "果园防风林边传来修剪枝叶的清脆剪刀声",
                ],
            },
        ],
        "intro_clauses": [
            "小镇铺展在朝南的缓坡台地上，干垒矮石墙围出一片片四季轮替的果园",
            "得益于充足日照与温和坡风，街巷间总飘着烘焙麦香与花果气息",
        ],
        "festivals": ["春日果花野餐会", "金秋台地果实收获市"],
        "street_flavor": [
            "干垒石垣缝隙里种着迷迭香与百里香，擦身而过便有清香",
            "坡道休息处设有木棚长椅与无人自助鲜果小木箱",
            "路标木牌顶端雕着小巧的柑橘与麦穗纹样",
        ],
        "zone_flavor": [
            "朝南台地的日照格外慷慨，午后连石阶都带着暖意",
            "防风林梢头的测风小木轮随着微风轻快旋转",
            "果园木棚外整齐码着印有镇名烙印的杉木果箱",
            "烘焙店与果酱作坊熬煮糖渍鲜果的甜香飘过半条街",
            "石墙边的蓄水陶缸映着蓝天与流云",
            "黄昏时整片台地视野开阔，晚霞久久不散",
            "篱笆边开着成簇的小黄花，蜜蜂嗡嗡忙碌",
            "晴朗夜晚在台地边缘抬头就能看见清晰星群",
        ],
        "home_names": ["向阳台地的住处", "果园石垣边的住居"],
        "home_vista": "朝南窗户正对洒满阳光的层叠果园与矮石墙",
        "shop_weights": {"bakery": 4, "flower_shop": 4, "home_goods_store": 3, "cafe": 2},
        "landmarks": [
            {
                "id": "terrace_meadow",
                "slot": "terrace_meadow",
                "kind": "park",
                "zone": "terrace",
                "weight": 3,
                "open_hour": 6,
                "close_hour": 21,
                "weekdays_only": False,
                "names": ["{world_name}向阳草坡", "晴风果园绿地"],
                "descriptions": ["果园台地顶端的开阔草坡，设有野餐木桌与遮阳凉亭。"],
                "affordances": [
                    ["WALK", "在向阳草坡与果树小径间散步"],
                    ["SIT", "在木凉亭下吹风小坐", {"title": "在台地凉亭小坐", "suggested_seconds": 300}],
                ],
            },
            {
                "id": "harvest_shed",
                "slot": "harvest_shed",
                "kind": "public",
                "zone": "terrace",
                "weight": 2,
                "open_hour": 8,
                "close_hour": 18,
                "weekdays_only": False,
                "names": ["台地风物陈列屋", "{world_name}果农集会所"],
                "descriptions": ["木结构的果园公共休息屋，墙上挂着四季物候图谱。"],
                "affordances": [
                    ["LOOK_AROUND", "看看墙上的果园物候历与标本"],
                    ["REST", "在木屋长凳上歇一会儿", {"title": "在木屋歇脚", "suggested_seconds": 240, "execution": {"mechanism": "stay_at_place"}}],
                ],
            },
        ],
    },
    "woodland_lakeside": {
        "id": "woodland_lakeside",
        "label": "林间湖畔",
        "prefixes": ["镜", "桦", "澄湖", "松风", "星渚", "静林"],
        "suffixes": ["湖畔", "林町", "渚", "森里", "澄台"],
        "streets": ["白桦湖岸通", "松针步道", "栈桥前街", "静水小路", "林荫本通"],
        "zone": {"zone_id": "lakeside", "name": "林间湖岸区", "color": "#5d8fa3"},
        "weather": [
            {"condition": "lake_mirror", "description": "湖面如镜的无风晴朗天"},
            {"condition": "forest_mist", "description": "白桦林与湖面交织的晨雾"},
        ],
        "ambient": [
            {
                "event_type": "lakeside_lap_water",
                "slot": "home",
                "start_hour": 5,
                "end_hour": 11,
                "summaries": [
                    "清晨湖水轻拍木栈桥的声音顺着林间微风传进窗内",
                    "白桦林间响起清脆婉转的晨鸟鸣声",
                ],
            },
            {
                "event_type": "boardwalk_pine_scent",
                "slot": "street",
                "start_hour": 13,
                "end_hour": 18,
                "summaries": [
                    "午后阳光晒暖松针与木栈道，空气里满是清冽树脂香",
                    "湖岸写生与散步的居民三三两两经过林荫街角",
                ],
            },
        ],
        "intro_clauses": [
            "平静澄澈的内陆湖泊与白桦松林环抱着小镇，木栈道把街巷引向开阔水面",
            "林间空气清透安静，湖面随着晨昏天光变换出层次丰富的蓝绿色泽",
        ],
        "festivals": ["夏夜湖畔音乐灯会", "初冬白桦书香市"],
        "street_flavor": [
            "人行道旁保留着高挑的白桦树，树皮在阳光下泛着银白光泽",
            "街角指路牌用耐水雪松木制成，标注着通往湖岸栈桥的距离",
            "路边长椅朝向湖面开口处，坐下就能望见粼粼水光",
        ],
        "zone_flavor": [
            "湖岸浅水区的芦苇丛随微风整齐起伏",
            "木栈桥尽头系着两艘漆成墨绿色的木桨小船",
            "松果偶尔掉落在木瓦屋顶上发出轻巧的一声响",
            "图书馆与书店的读者常把刚买的文库本带去湖边长椅翻阅",
            "雨后林间小路浮起清新的松针与湿木头香气",
            "傍晚无风时，整座远山与云朵完整映在镜面般的湖水中",
            "手工木器店窗台陈列着用风倒桦木车削出的杯皿",
            "入夜后湖畔路灯采用低眩光暖黄灯罩，守护静谧夜色",
        ],
        "home_names": ["白桦湖畔的住处", "松风林荫里的住居"],
        "home_vista": "从窗口穿过疏朗白桦林便能看见平静如镜的湖面",
        "shop_weights": {"bookstore": 4, "cafe": 4, "craft_shop": 3, "fish_market": 2},
        "landmarks": [
            {
                "id": "lakeside_pier",
                "slot": "lakeside_pier",
                "kind": "public",
                "zone": "lakeside",
                "weight": 3,
                "open_hour": 5,
                "close_hour": 21,
                "weekdays_only": False,
                "names": ["{world_name}木栈桥", "镜湖观水台"],
                "descriptions": ["伸入澄澈湖面的雪松木栈桥，尽头设有宽敞的观水坐席。"],
                "affordances": [
                    ["SIT", "坐在木栈桥边看湖面波光", {"title": "在湖畔栈桥小坐", "suggested_seconds": 360}],
                    ["LOOK_AROUND", "远眺对岸白桦林与山影"],
                ],
            },
            {
                "id": "reed_boardwalk",
                "slot": "reed_boardwalk",
                "kind": "park",
                "zone": "lakeside",
                "weight": 2,
                "open_hour": 6,
                "close_hour": 20,
                "weekdays_only": False,
                "names": ["白桦林荫木道", "{world_name}湖湾湿地园"],
                "descriptions": ["连接树林与浅水芦苇湾的架空木步道，四季景致分明。"],
                "affordances": [
                    ["WALK", "沿着林荫木道漫步", {"title": "漫步湖湾木道", "suggested_seconds": 600}],
                    ["LOOK_AROUND", "观察浅水湾里的水草与水鸟"],
                ],
            },
        ],
    },
}

_BIOME_KEYS: tuple[str, ...] = tuple(BIOME_TRAITS.keys())
_BIOME_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (a, b) for a in _BIOME_KEYS for b in _BIOME_KEYS if a != b
)


def is_procedural_theme(theme_id: Optional[str]) -> bool:
    """判断传入主题标识是否指向零预设程序化合成模式。"""
    if theme_id is None:
        return True
    if not isinstance(theme_id, str):
        return False
    cleaned = theme_id.strip().lower()
    return not cleaned or cleaned in PROCEDURAL_THEME_ALIASES


def _commercial_catalog(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """构建 8 家候选商业场所槽位定义（融入当前地理风貌组合）。"""
    spec_zone = primary["zone"]["zone_id"]
    p_label = primary["label"]
    s_label = secondary["label"]
    return {
        "convenience_store": {
            "id": "convenience_store",
            "slot": "convenience_store",
            "kind": "store",
            "zone": "town",
            "open_hour": [6, 7],
            "close_hour": [22, 23],
            "weekdays_only": False,
            "names": ["{world_name}便利店", "{prefix}生活便利店", "朝星便利店"],
            "descriptions": [
                "灯光明亮的街角便利店，收银台旁摆着热腾腾的关东煮与饭团，冷藏柜里排满茶饮与汽水。"
            ],
            "affordances": [
                ["BROWSE_STORE", "在便利店货架间逛逛", {"title": "逛逛便利店货架", "suggested_seconds": 240}],
                ["LOOK_AROUND", "看看收银台旁的新品告示"],
            ],
        },
        "cafe": {
            "id": "cafe",
            "slot": "cafe",
            "kind": "cafe",
            "zone": "town",
            "open_hour": [8, 10],
            "close_hour": [19, 21],
            "weekdays_only": False,
            "names": ["{prefix}珈琲焙煎所", "{world_name}喫茶室", "木漏日咖啡馆"],
            "descriptions": [
                f"飘着手冲咖啡与黄油吐司香气的安静咖啡馆，靠窗座位能感受{p_label}的日常光线，供应热可可与手作蛋糕。"
            ],
            "affordances": [
                ["SIT", "在咖啡馆靠窗位坐一会儿", {"title": "在咖啡馆小坐", "suggested_seconds": 420, "execution": {"mechanism": "stay_at_place"}}],
                ["READ", "伴着咖啡香翻几页书"],
            ],
        },
        "bookstore": {
            "id": "bookstore",
            "slot": "bookstore",
            "kind": "store",
            "zone": "town",
            "open_hour": [9, 10],
            "close_hour": [19, 21],
            "weekdays_only": False,
            "names": ["{world_name}书房", "{prefix}文库堂", "灯下独立书店"],
            "descriptions": [
                "木架到顶的独立书店，收纳着不少地方风物志、诗集、文库本与手账信纸，深处留有安静的阅读桌。"
            ],
            "reading": (
                f"翻开柜台旁的地方小志《{p_label}与{s_label}风物帖》：「这座小镇由两重地貌交汇而成，"
                "老街沿着水脉与坡脊自然生长。搬来此处的人常说，只要走完一圈街巷，就能听懂四季风声的走向。」"
            ),
            "affordances": [
                ["READ_HERE", "在书店阅读角读一本书", {"title": "在书店静读", "suggested_seconds": 600, "execution": {"mechanism": "stay_at_place"}}],
                ["BROWSE_STORE", "沿着木书架挑书", {"title": "浏览书架选书", "suggested_seconds": 300}],
            ],
        },
        "bakery": {
            "id": "bakery",
            "slot": "bakery",
            "kind": "store",
            "zone": "town",
            "open_hour": [7, 8],
            "close_hour": [18, 19],
            "weekdays_only": False,
            "names": ["{world_name}麦香面包房", "{prefix}石窑烘焙坊", "朝露面包店"],
            "descriptions": [
                "清晨便飘出麦香的石窑面包房，木盘上摆满现烤红豆面包、盐可颂与果酱司康，也卖冰牛奶。"
            ],
            "affordances": [
                ["BROWSE_STORE", "在面包柜前挑选刚出炉的面点", {"title": "挑选现烤面包", "suggested_seconds": 240}],
                ["LOOK_AROUND", "看看黑板上的出炉时刻表"],
            ],
        },
        "flower_shop": {
            "id": "flower_shop",
            "slot": "flower_shop",
            "kind": "store",
            "zone": "town",
            "open_hour": [9, 10],
            "close_hour": [18, 19],
            "weekdays_only": False,
            "names": ["{world_name}花屋", "{prefix}草木花房", "晴枝花店"],
            "descriptions": [
                "门口木桶插满当季鲜切花与尤加利绿枝的花店，架上摆着小盆栽、干花束与玻璃水培瓶。"
            ],
            "affordances": [
                ["BROWSE_STORE", "在花桶与盆栽架间挑选植物", {"title": "看看今日花草", "suggested_seconds": 240}],
                ["LOOK_AROUND", "闻一闻店门口清新的草木水气"],
            ],
        },
        "fish_market": {
            "id": "fish_market",
            "slot": "fish_market",
            "kind": "market",
            "zone": spec_zone,
            "open_hour": [6, 7],
            "close_hour": [14, 16],
            "weekdays_only": False,
            "names": ["{world_name}朝市鲜鱼铺", "{prefix}生鲜集市", "早潮水产市"],
            "descriptions": [
                "清晨最热闹的生鲜市集，碎冰上整齐码着鲜鱼、海产与时令蔬果，旁侧熟食摊供应热鱼丸汤。"
            ],
            "affordances": [
                ["BROWSE_STORE", "在生鲜摊位间挑选食材", {"title": "逛逛生鲜朝市", "suggested_seconds": 300}],
                ["LOOK_AROUND", "感受市集清晨热闹的叫卖与水气"],
            ],
        },
        "craft_shop": {
            "id": "craft_shop",
            "slot": "craft_shop",
            "kind": "store",
            "zone": spec_zone,
            "open_hour": [10, 11],
            "close_hour": [18, 20],
            "weekdays_only": False,
            "names": ["{prefix}手工作坊店", "{world_name}器物工坊", "木石手作铺"],
            "descriptions": [
                "前店后坊的手作器物铺，木柜陈列着手工陶器、木雕茶托、竹编篮与黄铜小台灯。"
            ],
            "affordances": [
                ["BROWSE_STORE", "细看架上的手工陶木器皿", {"title": "欣赏手作器物", "suggested_seconds": 300}],
                ["LOOK_AROUND", "透过玻璃窗看里屋工作台上的工具"],
            ],
        },
        "home_goods_store": {
            "id": "home_goods_store",
            "slot": "home_goods_store",
            "kind": "store",
            "zone": "town",
            "open_hour": [9, 10],
            "close_hour": [19, 20],
            "weekdays_only": False,
            "names": ["{world_name}生活杂货店", "{prefix}日用器皿铺", "暮光家居杂货"],
            "descriptions": [
                "收纳得井井有条的家居日用店，售卖搪瓷水壶、陶瓷碗盘、亚麻坐垫、木椅与小书架。"
            ],
            "affordances": [
                ["BROWSE_STORE", "挑选居家器皿与小家具", {"title": "挑选日用家居", "suggested_seconds": 300}],
                ["LOOK_AROUND", "看看门口陈列的当季生活杂货"],
            ],
        },
    }


def _landmark_catalog(primary: dict[str, Any], secondary: dict[str, Any]) -> list[dict[str, Any]]:
    """构建核心公共地标池 + 双风貌特色地标。"""
    spec_zone = primary["zone"]["zone_id"]
    common = [
        {
            "id": "park",
            "slot": "park",
            "kind": "park",
            "zone": "residential",
            "weight": 3,
            "open_hour": 6,
            "close_hour": 22,
            "weekdays_only": False,
            "names": ["{world_name}中央公园", "{prefix}绿荫公园", "风铃小公园"],
            "descriptions": ["种满高大落叶树与四季花木的社区公园，步道旁设有宽木长椅与饮水台。"],
            "affordances": [
                ["WALK", "沿着公园树荫步道散步", {"title": "在公园散步", "suggested_seconds": 480}],
                ["SIT", "坐在长椅上吹风歇息", {"title": "在公园长椅小坐", "suggested_seconds": 300}],
                ["PEOPLE_WATCH", "看附近散步与遛弯的居民"],
            ],
        },
        {
            "id": "station",
            "slot": "station",
            "kind": "station",
            "zone": "town",
            "weight": 3,
            "open_hour": None,
            "close_hour": None,
            "weekdays_only": False,
            "names": ["{world_name}站", "{prefix}驿"],
            "descriptions": ["保留木质候车长椅与复古挂钟的地方小站，站前设有时刻表与自动售票机。"],
            "affordances": [
                ["LOOK_AROUND", "看看候车室墙上的路线图与时刻表"],
                ["WALK", "在站前檐廊下走走"],
            ],
        },
        {
            "id": "observatory",
            "slot": "observatory",
            "kind": "public",
            "zone": spec_zone,
            "weight": 2,
            "open_hour": 8,
            "close_hour": 21,
            "weekdays_only": False,
            "names": ["{world_name}展望台", "{prefix}高台望楼", "星风观景台"],
            "descriptions": ["建在高处的木石观景平台，白天能俯瞰全镇屋顶与远景，晴夜适合看星。"],
            "affordances": [
                ["LOOK_AROUND", "凭栏俯瞰整座小镇与远方天际线"],
                ["SIT", "在观景台长凳上静坐片刻", {"title": "在展望台静坐", "suggested_seconds": 360}],
            ],
        },
        {
            "id": "shrine",
            "slot": "shrine",
            "kind": "shrine",
            "zone": "residential",
            "weight": 2,
            "open_hour": 6,
            "close_hour": 20,
            "weekdays_only": False,
            "names": ["{world_name}神社", "{prefix}守宫", "青苔石阶神社"],
            "descriptions": ["掩映在古树下的清幽小神社，手水舍竹筒流水潺潺，绘马架随风轻响。"],
            "affordances": [
                ["WALK", "穿过鸟居在石板院内慢步"],
                ["PRAY", "在拜殿前静静合掌", {"title": "在殿前祈愿", "suggested_seconds": 180, "execution": {"mechanism": "stay_at_place"}}],
            ],
        },
        {
            "id": "canal_walk",
            "slot": "canal_walk",
            "kind": "park",
            "zone": spec_zone,
            "weight": 2,
            "open_hour": None,
            "close_hour": None,
            "weekdays_only": False,
            "names": ["{prefix}亲水散步道", "{world_name}水岸绿道"],
            "descriptions": ["顺着水脉与缓坡修筑的无车漫步道，沿途设有石灯与观景木栏。"],
            "affordances": [
                ["WALK", "沿着亲水绿道慢慢散步", {"title": "漫步亲水绿道", "suggested_seconds": 600}],
                ["LOOK_AROUND", "停下脚步看看水面与两岸草木"],
            ],
        },
        {
            "id": "library",
            "slot": "library",
            "kind": "library",
            "zone": "education",
            "weight": 3,
            "open_hour": 9,
            "close_hour": 19,
            "weekdays_only": False,
            "names": ["{world_name}町立图书馆", "{prefix}纪念文库"],
            "descriptions": ["采光通透的二层町立图书馆，靠窗设有一排带绿罩台灯的木质阅览桌。"],
            "affordances": [
                ["READ", "在阅览室挑一本书安静阅读", {"title": "在图书馆阅读", "suggested_seconds": 600, "execution": {"mechanism": "stay_at_place"}}],
                ["SELF_STUDY", "在靠窗自习席整理笔记", {"title": "在图书馆自习", "suggested_seconds": 600, "execution": {"mechanism": "stay_at_place"}}],
            ],
        },
    ]
    seen_ids = {item["id"] for item in common}
    for landmark in list(primary["landmarks"]) + list(secondary["landmarks"]):
        if landmark["id"] not in seen_ids:
            seen_ids.add(landmark["id"])
            common.append(dict(landmark))
    return common


def _build_home_objects(extra_areas: tuple[str, ...], rng: random.Random) -> list[dict[str, Any]]:
    """构建住处初始物件池（覆盖冷/热/水/框架/插入件与完整九键物质档）。"""
    herb_names = ("押花香草标本册", "蜜蜡茶包折纸匣", "植物染色书签材料包")
    chosen_kit = rng.choice(herb_names)
    items: list[dict[str, Any]] = [
        {
            "object_id": "bed_home",
            "name": "木架单人床",
            "kind": "bed",
            "location": "home",
            "note": "铺着干爽棉麻床单的单人床，靠在采光柔和的窗边。",
            "affordance_traits": ["movable", "usable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "everyday",
                "paper_like": False,
            },
        },
        {
            "object_id": "desk_home",
            "name": "靠窗书桌",
            "kind": "table",
            "location": "home",
            "note": "桌面宽敞的实木书桌，留着摊开笔记本与台灯的位置。",
            "affordance_traits": ["container", "combinable", "inscribable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 6,
                "item_kind": "everyday",
                "paper_like": False,
                "assembly_role": "host_frame",
            },
        },
        {
            "object_id": "drawer_home",
            "name": "带木栓私密抽屉柜",
            "kind": "cabinet",
            "location": "home",
            "capacity": 4,
            "note": "书桌下带木栓的私密抽屉柜，用来妥善收存私人信件与纪念小物。",
            "affordance_traits": ["container", "combinable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 4,
                "item_kind": "keepsake",
                "paper_like": False,
                "assembly_role": "host_frame",
            },
        },
        {
            "object_id": "shelf_home",
            "name": "立式木书架",
            "kind": "cabinet",
            "location": "home",
            "capacity": 6,
            "note": "可调节隔板高度的立式木书架。",
            "affordance_traits": ["container", "combinable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 6,
                "item_kind": "everyday",
                "paper_like": False,
                "assembly_role": "host_frame",
            },
        },
        {
            "object_id": "lamp_home",
            "name": "暖光台灯",
            "kind": "lamp",
            "location": "home",
            "note": "黄铜灯臂的暖色台灯，夜间阅读光线柔和。",
            "affordance_traits": ["switchable", "usable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "everyday",
                "paper_like": False,
            },
        },
        {
            "object_id": "fridge_home",
            "name": "双门小冰箱",
            "kind": "cabinet",
            "location": "home",
            "capacity": 8,
            "note": "安静运转的双门冷藏小冰箱，适合存放鲜奶、果酱与食材。",
            "affordance_traits": ["container", "combinable"],
            "matter_profile": {
                "ambient_zone": "cold",
                "water_source": False,
                "capacity_slots": 8,
                "item_kind": "everyday",
                "paper_like": False,
            },
        },
        {
            "object_id": "kettle_home",
            "name": "温控煮水壶",
            "kind": "dispenser",
            "location": "home",
            "note": "放在隔热垫上的温控电煮水壶，随时可以烧水泡茶或手冲咖啡。",
            "affordance_traits": ["switchable", "usable", "thermal_or_damp"],
            "matter_profile": {
                "ambient_zone": "heating",
                "water_source": False,
                "capacity_slots": 3,
                "item_kind": "everyday",
                "paper_like": False,
            },
        },
        {
            "object_id": "sink_home",
            "name": "陶瓷洗手水槽",
            "kind": "dispenser",
            "location": "home",
            "note": "带净水龙头的陶瓷洗手水槽，可接清水冲洗杯皿与蔬果。",
            "affordance_traits": ["switchable", "usable", "thermal_or_damp"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": True,
                "capacity_slots": 4,
                "item_kind": "everyday",
                "paper_like": False,
            },
        },
        {
            "object_id": "specimen_kit_home",
            "name": chosen_kit,
            "kind": "book",
            "location": "home",
            "portable": True,
            "note": "内含干花香草压片与可嵌入书架展示框的棉纸卡页。",
            "affordance_traits": ["inscribable", "insertable", "movable", "combinable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 2,
                "item_kind": "paper",
                "paper_like": True,
                "shelf_life_hours": 720,
                "portions": 4,
                "material_units": 6,
                "assembly_role": "insert_part",
            },
        },
    ]
    if "living" in extra_areas:
        items.append(
            {
                "object_id": "tea_cup_home",
                "name": "粗陶马克杯",
                "kind": "cup",
                "location": "home",
                "portable": True,
                "note": "起居室矮桌上常用的粗陶茶杯。",
                "affordance_traits": ["usable", "thermal_or_damp", "movable"],
                "matter_profile": {
                    "ambient_zone": "room",
                    "water_source": False,
                    "capacity_slots": 2,
                    "item_kind": "vessel",
                    "paper_like": False,
                    "portions": 1,
                },
            }
        )
    if "veranda" in extra_areas:
        items.append(
            {
                "object_id": "veranda_plant_home",
                "name": "缘侧迷迭香盆栽",
                "kind": "plant",
                "location": "home",
                "portable": True,
                "note": "养在缘侧晒台木栏边的陶盆迷迭香。",
                "affordance_traits": ["movable", "usable"],
                "matter_profile": {
                    "ambient_zone": "room",
                    "water_source": False,
                    "capacity_slots": 0,
                    "item_kind": "everyday",
                    "paper_like": False,
                },
            }
        )
    if "study_nook" in extra_areas:
        items.append(
            {
                "object_id": "postcard_stack_home",
                "name": "一叠空白风景明信片",
                "kind": "book",
                "location": "home",
                "portable": True,
                "note": "书斋角木盒里码放整齐的厚卡纸明信片。",
                "affordance_traits": ["inscribable", "insertable", "movable"],
                "matter_profile": {
                    "ambient_zone": "room",
                    "water_source": False,
                    "capacity_slots": 0,
                    "item_kind": "paper",
                    "paper_like": True,
                    "material_units": 8,
                },
            }
        )
    if "kitchenette" in extra_areas:
        items.append(
            {
                "object_id": "tea_canister_home",
                "name": "烘青绿茶罐",
                "kind": "cup",
                "location": "home",
                "portable": True,
                "note": "茶水间搁板上的密封茶叶罐，余量充足。",
                "affordance_traits": ["usable", "thermal_or_damp", "movable"],
                "matter_profile": {
                    "ambient_zone": "room",
                    "water_source": False,
                    "capacity_slots": 0,
                    "item_kind": "drink",
                    "paper_like": False,
                    "shelf_life_hours": 1440,
                    "portions": 8,
                },
            }
        )
    return items


def _build_shop_sale_objects(rng: random.Random, world_prefix: str) -> list[dict[str, Any]]:
    """为 8 类商业场所生成带显式 ``sale_spec`` 的镇内招牌陈列商品（仅当该店入选地图时落盘）。"""
    return [
        {
            "object_id": "sale_conv_bento",
            "name": f"{world_prefix}热关东煮便当",
            "kind": "cup",
            "location": "convenience_store",
            "location_only": True,
            "portable": True,
            "note": "便利店保温柜里现盛的热汤关东煮配菜便当。",
            "affordance_traits": ["usable", "thermal_or_damp", "movable"],
            "matter_profile": {
                "ambient_zone": "heating",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "prepared_food",
                "paper_like": False,
                "shelf_life_hours": 12,
                "portions": 1,
            },
            "sale_spec": {
                "category": "food",
                "price_tier": "standard",
                "price": rng.choice((460, 490, 520)),
            },
        },
        {
            "object_id": "sale_cafe_blend",
            "name": f"{world_prefix}手冲拼配热咖啡",
            "kind": "cup",
            "location": "cafe",
            "location_only": True,
            "portable": True,
            "note": "咖啡馆自家浅中烘焙的招牌手冲咖啡。",
            "affordance_traits": ["usable", "thermal_or_damp", "movable"],
            "matter_profile": {
                "ambient_zone": "heating",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "drink",
                "paper_like": False,
                "shelf_life_hours": 8,
                "portions": 1,
            },
            "sale_spec": {
                "category": "drink",
                "price_tier": "standard",
                "price": rng.choice((380, 420, 450)),
            },
        },
        {
            "object_id": "sale_book_anthology",
            "name": f"{world_prefix}地方风物诗集",
            "kind": "book",
            "location": "bookstore",
            "location_only": True,
            "portable": True,
            "note": "独立书店柜台重点陈列的本地散文与短诗合集。",
            "affordance_traits": ["inscribable", "insertable", "movable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "paper",
                "paper_like": True,
            },
            "sale_spec": {
                "category": "reading",
                "price_tier": "standard",
                "price": rng.choice((620, 680, 740)),
            },
        },
        {
            "object_id": "sale_bakery_loaf",
            "name": f"{world_prefix}石窑红豆面包",
            "kind": "cup",
            "location": "bakery",
            "location_only": True,
            "portable": True,
            "note": "刚出炉微温的石窑烘烤红豆餐包。",
            "affordance_traits": ["usable", "thermal_or_damp", "movable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "prepared_food",
                "paper_like": False,
                "shelf_life_hours": 24,
                "portions": 2,
            },
            "sale_spec": {
                "category": "food",
                "price_tier": "budget",
                "price": rng.choice((210, 240, 270)),
            },
        },
        {
            "object_id": "sale_flower_bouquet",
            "name": "当季鲜切花束",
            "kind": "plant",
            "location": "flower_shop",
            "location_only": True,
            "portable": True,
            "note": "用牛皮纸包好的当季鲜切花与绿叶小束。",
            "affordance_traits": ["movable", "usable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "everyday",
                "paper_like": False,
                "shelf_life_hours": 96,
            },
            "sale_spec": {
                "category": "daily",
                "price_tier": "standard",
                "price": rng.choice((520, 580, 640)),
            },
        },
        {
            "object_id": "sale_market_fresh",
            "name": "朝市冰鲜竹荚鱼",
            "kind": "cup",
            "location": "fish_market",
            "location_only": True,
            "portable": True,
            "note": "清晨刚整理好装盒的冰鲜竹荚鱼食材。",
            "affordance_traits": ["usable", "thermal_or_damp", "movable"],
            "matter_profile": {
                "ambient_zone": "cold",
                "water_source": False,
                "capacity_slots": 0,
                "item_kind": "ingredient",
                "paper_like": False,
                "shelf_life_hours": 36,
                "portions": 2,
            },
            "sale_spec": {
                "category": "ingredient",
                "price_tier": "standard",
                "price": rng.choice((420, 480, 540)),
            },
        },
        {
            "object_id": "sale_craft_mug",
            "name": f"{world_prefix}手拉坯粗陶茶杯",
            "kind": "cup",
            "location": "craft_shop",
            "location_only": True,
            "portable": True,
            "note": "工坊匠人烧制的灰釉粗陶茶杯，手感温厚。",
            "affordance_traits": ["usable", "thermal_or_damp", "movable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 2,
                "item_kind": "vessel",
                "paper_like": False,
                "portions": 1,
            },
            "sale_spec": {
                "category": "daily",
                "price_tier": "hearty",
                "price": rng.choice((980, 1120, 1280)),
            },
        },
        {
            "object_id": "sale_home_shelf",
            "name": "胡桃木桌面小书架",
            "kind": "cabinet",
            "location": "home_goods_store",
            "location_only": True,
            "portable": False,
            "capacity": 4,
            "note": "家居杂货铺在售的榫卯结构胡桃木小书架。",
            "affordance_traits": ["container", "combinable"],
            "matter_profile": {
                "ambient_zone": "room",
                "water_source": False,
                "capacity_slots": 4,
                "item_kind": "everyday",
                "paper_like": False,
                "assembly_role": "host_frame",
            },
            "sale_spec": {
                "category": "furnish",
                "price_tier": "standard",
                "price": rng.choice((1680, 1880, 2180)),
            },
        },
    ]


def synthesize_procedural_template(seed: int) -> dict[str, Any]:
    """仅凭整数 ``seed`` 确定性合成完整的世界模板字典（兼容 ``generate_world``）。"""
    seed_int = int(seed)
    digest = hashlib.sha256(
        f"nyairo-procedural|{GENERATOR_VERSION}|{seed_int}".encode("utf-8")
    ).digest()
    rng = random.Random(int.from_bytes(digest, "big"))

    # 1) 道路拓扑风格（4 选 1，哈希字节直投保证小样本均匀覆盖）。
    street_layout_style = STREET_LAYOUT_STYLES[digest[0] % len(STREET_LAYOUT_STYLES)]

    # 2) 双地理风貌混合（30 种有序互补配对）。
    pair_index = int.from_bytes(digest[1:3], "big") % len(_BIOME_PAIRS)
    primary_key, secondary_key = _BIOME_PAIRS[pair_index]
    primary = BIOME_TRAITS[primary_key]
    secondary = BIOME_TRAITS[secondary_key]

    # 3) 多分区住宅布局：bedroom + 1~2 个附加室内区域（10 种组合）。
    combo_index = int.from_bytes(digest[3:5], "big") % len(_EXTRA_HOME_COMBINATIONS)
    extra_home_areas = _EXTRA_HOME_COMBINATIONS[combo_index]
    home_rooms = [
        {
            "room_id": "bedroom",
            "name": "自己的房间",
            "description": f"采光安静的主卧室，摆着床、书桌、木书架与私密抽屉，{primary['home_vista']}。",
        }
    ]
    for area_id in extra_home_areas:
        meta = _HOME_AREA_CATALOG[area_id]
        home_rooms.append(
            {
                "room_id": area_id,
                "name": meta["name"],
                "description": meta["description"],
            }
        )

    # 4) 命名词根、街道名、分区、天气与风味池合成。
    prefixes = list(dict.fromkeys(list(primary["prefixes"]) + list(secondary["prefixes"])))
    suffixes = list(dict.fromkeys(list(primary["suffixes"]) + list(secondary["suffixes"])))
    street_names = list(dict.fromkeys(list(primary["streets"]) + list(secondary["streets"])))

    spec_zone_primary = dict(primary["zone"])
    zones = [
        {"zone_id": "residential", "name": "生活住宅区", "color": "#7fae7a"},
        spec_zone_primary,
        {"zone_id": "town", "name": "街市商区", "color": "#c7a76f"},
        {"zone_id": "education", "name": "文教学区", "color": "#a98fc7"},
    ]
    if secondary["zone"]["zone_id"] != spec_zone_primary["zone_id"]:
        # 保持 4 区精简清晰，同时把副风貌意象融入街市或水岸名称。
        zones[2] = {
            "zone_id": "town",
            "name": f"{secondary['label']}街市区",
            "color": "#c7a76f",
        }

    weather = [
        {"condition": "clear", "description": "晴朗通透"},
        {"condition": "cloudy", "description": "柔和多云"},
        *primary["weather"],
        *secondary["weather"],
    ]
    dedup_weather: list[dict[str, str]] = []
    seen_cond: set[str] = set()
    for w in weather:
        if w["condition"] not in seen_cond:
            seen_cond.add(w["condition"])
            dedup_weather.append(dict(w))

    # 5) 住处与学校固定槽位 + 按风貌亲和度抽取 3~4 家保底商业场所。
    extra_room_labels = "、".join(_HOME_AREA_CATALOG[a]["name"] for a in extra_home_areas)
    home_slot = {
        "id": "home",
        "slot": "home",
        "kind": "home",
        "zone": "residential",
        "open_hour": None,
        "close_hour": None,
        "weekdays_only": False,
        "names": list(primary["home_names"]) + list(secondary["home_names"]),
        "descriptions": [
            f"{primary['home_vista']}的住处，除卧室外还连着{extra_room_labels}。"
        ],
        "rooms": home_rooms,
        "affordances": [
            [
                "REST",
                "在房间里安静休息",
                {
                    "title": "在房间歇一会儿",
                    "suggested_seconds": 300,
                    "execution": {"mechanism": "stay_at_place"},
                },
            ],
            [
                "DESK_PAUSE",
                "在书桌前坐一会儿整理手边物件",
                {"title": "在书桌边静坐", "suggested_seconds": 300},
            ],
            [
                "WINDOW_PAUSE",
                "靠在窗边看外面的天色与街景",
                {"title": "在窗边待一会儿", "suggested_seconds": 240},
            ],
            ["READ", "在屋里翻阅手头的读物"],
            ["USE_PHONE", "在屋里查看手机"],
            ["DAZE", "放松发会儿呆"],
            ["POTTER", "在各个房间里随便转转"],
        ],
    }
    school_slot = {
        "id": "school",
        "slot": "school",
        "kind": "school",
        "zone": "education",
        "open_hour": 8,
        "close_hour": 18,
        "weekdays_only": True,
        "names": ["{world_name}学园", "{prefix}丘学校", "{prefix}町立中学"],
        "descriptions": [
            f"坐落在文教学区高处的学校，从走廊窗口能望见{primary['label']}与{secondary['label']}交汇的街景。"
        ],
        "affordances": [
            ["ATTEND_CLASS", "在教室上课"],
            ["SELF_STUDY", "放学后在教室留堂自习"],
            ["STAY_AT_SCHOOL", "在校园里停留片刻"],
        ],
    }

    shops_by_id = _commercial_catalog(primary, secondary)
    shop_candidates: list[dict[str, Any]] = []
    for shop_id in COMMERCIAL_VENUE_IDS:
        entry = dict(shops_by_id[shop_id])
        weight = (
            2
            + int(primary["shop_weights"].get(shop_id, 0))
            + int(secondary["shop_weights"].get(shop_id, 0))
        )
        entry["weight"] = weight
        shop_candidates.append(entry)

    guaranteed_shop_count = rng.choice((3, 4))
    remaining_shops = list(shop_candidates)
    guaranteed_shops: list[dict[str, Any]] = []
    while remaining_shops and len(guaranteed_shops) < guaranteed_shop_count:
        total_w = sum(item["weight"] for item in remaining_shops)
        needle = rng.uniform(0.0, float(total_w))
        acc = 0.0
        for idx, item in enumerate(remaining_shops):
            acc += item["weight"]
            if needle <= acc:
                guaranteed_shops.append(remaining_shops.pop(idx))
                break
        else:  # pragma: no cover
            guaranteed_shops.append(remaining_shops.pop())

    landmarks = _landmark_catalog(primary, secondary)
    # 其余商铺与全部地标共同进入可选抽取池，赋予不同种子更丰富的业态与景观组合。
    slot_pool = [*remaining_shops, *landmarks]
    fixed_slots = [home_slot, school_slot, *guaranteed_shops]
    core_location_ids = ["home", "school", *(s["id"] for s in guaranteed_shops)]

    # 6) 环境事件池（双风貌声景 + 商铺/地标日常动态）。
    ambient_pool = [
        *primary["ambient"],
        *secondary["ambient"],
        {
            "event_type": "school_bulletin_update",
            "slot": "school",
            "start_hour": 7,
            "end_hour": 18,
            "summaries": [
                "校门口宣传栏换上了新一期社团活动与季节行事历",
                "午休时操场方向传来练习吹奏乐的断续音色",
            ],
        },
        {
            "event_type": "convenience_evening_markdown",
            "slot": "convenience_store",
            "start_hour": 18,
            "end_hour": 22,
            "summaries": [
                "便利店熟食柜贴出了晚间饭团与便当的折扣标签",
                "补货小车停在便利店侧门卸下刚到的冷饮箱",
            ],
        },
        {
            "event_type": "bakery_morning_oven",
            "slot": "bakery",
            "start_hour": 7,
            "end_hour": 11,
            "summaries": [
                "面包房第一炉盐可颂与红豆包出炉，麦香顺着街风飘散",
                "烘焙师在门口小黑板上写下今日限定果酱司康",
            ],
        },
        {
            "event_type": "cafe_roast_aroma",
            "slot": "cafe",
            "start_hour": 13,
            "end_hour": 18,
            "summaries": [
                "午后咖啡馆正在小批烘焙咖啡豆，整条小巷带着焦糖坚果香",
                "咖啡馆檐下黑板换上了今日手冲豆单",
            ],
        },
        {
            "event_type": "park_afternoon_breeze",
            "slot": "park",
            "start_hour": 14,
            "end_hour": 19,
            "summaries": [
                "公园树荫长椅旁聚集了散步乘凉的邻里",
                "园丁刚给花坛浇过水，泥土与青草气息清新",
            ],
        },
    ]

    # 7) 公共角色池（严格无特定角色名与性别代词）。
    npc_name_pool = {
        "family": [
            "白石", "藤原", "森下", "长谷", "青木", "水野",
            "秋山", "栗原", "早川", "星野", "楠木", "泽田",
        ],
        "given_adult": [
            "直树", "美绪", "修平", "绫乃", "健司", "由纪",
            "达也", "澄子", "宏明", "理沙", "拓真", "晴香",
        ],
        "given_minor": [
            "悠斗", "凛", "结衣", "葵", "凑", "阳菜", "陆", "澪", "飒太", "诗织",
        ],
    }
    npc_role_pool = [
        {"age": "adult", "slot": "convenience_store", "role": "便利店值班店员", "start_hour": 9, "end_hour": 21, "weekdays": [1, 2, 3, 4, 5, 6, 7]},
        {"age": "adult", "slot": "cafe", "role": "咖啡馆烘焙师", "start_hour": 10, "end_hour": 19, "weekdays": [1, 2, 3, 4, 5, 6]},
        {"age": "adult", "slot": "bookstore", "role": "独立书店选书人", "start_hour": 10, "end_hour": 19, "weekdays": [2, 3, 4, 5, 6, 7]},
        {"age": "adult", "slot": "bakery", "role": "石窑面包师傅", "start_hour": 8, "end_hour": 17, "weekdays": [1, 2, 3, 4, 5, 6]},
        {"age": "adult", "slot": "flower_shop", "role": "花屋园艺师", "start_hour": 10, "end_hour": 18, "weekdays": [1, 2, 3, 4, 5, 6]},
        {"age": "adult", "slot": "fish_market", "role": "朝市水产摊主", "start_hour": 7, "end_hour": 14, "weekdays": [1, 2, 3, 4, 5, 6]},
        {"age": "adult", "slot": "craft_shop", "role": "器物工坊匠人", "start_hour": 11, "end_hour": 18, "weekdays": [2, 3, 4, 5, 6, 7]},
        {"age": "adult", "slot": "home_goods_store", "role": "生活杂货店主理人", "start_hour": 10, "end_hour": 19, "weekdays": [1, 2, 3, 4, 5, 6]},
        {"age": "adult", "slot": "library", "role": "町立图书馆司书", "start_hour": 9, "end_hour": 18, "weekdays": [2, 3, 4, 5, 6, 7]},
        {"age": "adult", "slot": "shrine", "role": "神社看护人", "start_hour": 7, "end_hour": 17, "weekdays": [1, 2, 3, 4, 5, 6, 7]},
        {"age": "adult", "slot": "station", "role": "小站站务员", "start_hour": 7, "end_hour": 20, "weekdays": [1, 2, 3, 4, 5, 6, 7]},
        {"age": "minor", "slot": "school", "role": "同校高年级学生", "start_hour": 8, "end_hour": 16, "weekdays": [1, 2, 3, 4, 5]},
        {"age": "minor", "slot": "school", "role": "美术社学生", "start_hour": 9, "end_hour": 17, "weekdays": [1, 2, 3, 4, 5]},
    ]

    # 8) 世界总体介绍与风味文本池。
    world_intro = [
        f"{c1}；{c2}。"
        for c1 in primary["intro_clauses"]
        for c2 in secondary["intro_clauses"]
    ]
    festivals = list(dict.fromkeys(list(primary["festivals"]) + list(secondary["festivals"])))
    street_flavor = list(dict.fromkeys(list(primary["street_flavor"]) + list(secondary["street_flavor"])))
    zone_flavor = list(dict.fromkeys(list(primary["zone_flavor"]) + list(secondary["zone_flavor"])))

    signature_prefix = rng.choice(prefixes)
    object_pool = [
        *_build_home_objects(extra_home_areas, rng),
        *_build_shop_sale_objects(rng, signature_prefix),
    ]

    return {
        "procedural_mode": True,
        "theme_id": PROCEDURAL_THEME,
        "theme_name": f"{primary['label']}·{secondary['label']}",
        "biome_mix": [primary_key, secondary_key],
        "biome_labels": [primary["label"], secondary["label"]],
        "street_layout_style": street_layout_style,
        "home_extra_areas": list(extra_home_areas),
        "core_location_ids": core_location_ids,
        "venue_budget_range": [6, 9],
        "world_name_fragments": {
            "prefix": prefixes,
            "suffix": suffixes,
        },
        "street_names": street_names,
        "street_slot": {
            "slot": "street",
            "kind": "street",
            "affordances": [
                ["WALK", "顺着街巷慢慢散步"],
                ["LOOK_AROUND", "停下脚步打量街边风物"],
                ["PEOPLE_WATCH", "站在街角看往来行人"],
            ],
        },
        "zones": zones,
        "weather": dedup_weather,
        "fixed_slots": fixed_slots,
        "slot_pool": slot_pool,
        "ambient_pool": ambient_pool,
        "npc_name_pool": npc_name_pool,
        "npc_role_pool": npc_role_pool,
        "object_pool": object_pool,
        "flavor_pool": {
            "world_intro": world_intro,
            "festival_candidates": festivals,
            "street_flavor": street_flavor,
            "zone_flavor": zone_flavor,
        },
    }


__all__ = [
    "BIOME_TRAITS",
    "COMMERCIAL_VENUE_IDS",
    "EXTRA_HOME_AREA_IDS",
    "PROCEDURAL_THEME",
    "PROCEDURAL_THEME_ALIASES",
    "STREET_LAYOUT_STYLES",
    "is_procedural_theme",
    "synthesize_procedural_template",
]
