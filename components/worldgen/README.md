# NyAiro WorldGen v0.1

把一个随机种子变成一座**可被 rc9 World/Body 运行时真实加载、行走、观察、持久化**的小镇。

本组件基于 `v0.1.0-rc9` 的 `feature/worldgen-rc9` 分支开发。生成器只依赖 Python 标准库，
可以独立于 Hermes 使用；运行时接线层复用 rc9 既有的全部状态权威，不引入第二套状态。

---

## 1. 快速开始

```bash
# 生成（无 API Key 时自动使用本地模板内容，完整可运行）
python -m components.worldgen generate \
  --seed 929711 --theme coastal_town --out ./worlds/nagiura

# 校验（格式 + 图连通性 + 规则覆盖 + 清单 SHA256）
python -m components.worldgen validate ./worlds/nagiura

# 地图预览（单文件 HTML，浏览器打开）
python -m components.worldgen preview ./worlds/nagiura

# 初始化一个隔离 World/Body 实例（在 WSL/Linux 上执行）
python -m components.worldgen instance \
  --package ./worlds/nagiura --home ~/.nyairo-worlds/nagiura-inst

# 隔离运行演示：世界层行走/营业时间/持久化 + World/Body 服务链（授权/账本/身体后果）
python -m components.worldgen demo full \
  --package ./worlds/nagiura --home ~/.nyairo-worlds/nagiura-inst
```

主题：`coastal_town`（海滨小镇）、`residential_town`（普通生活小镇）。

> **平台提示**：生成 / 校验 / 预览在原生 Windows 也能运行（纯标准库）；
> `instance` 与 `demo` 需要 POSIX 文件锁（`fcntl`），请在 WSL 或 Linux 上执行。
> rc9 的 World Runtime 本身就是 POSIX 优先的实现，这不是 WorldGen 引入的限制。

### 接入 GLM 内容（可选）

```bash
export WORLDGEN_LLM_API_KEY=你的Key
export WORLDGEN_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # 默认值
export WORLDGEN_LLM_MODEL=glm-5.3-flash                             # 默认值

python -m components.worldgen generate --seed 929711 --theme coastal_town \
  --out ./worlds/nagiura --llm
```

LLM **只**生成文本内容（地点介绍、氛围细节、NPC 公开背景、节庆候选），输出经结构校验后
固化进 `content.json`；它不决定拓扑、营业时间等程序权威数据，加载世界时也**不会**再调用模型。
生成失败可回退本地模板内容（`--llm-no-fallback` 可禁止回退）。

---

## 2. 世界包格式

一个世界包是一个目录，全部 JSON、UTF-8，`MANIFEST.json` 保存各文件的 SHA256。
运行时加载会核对清单：**文件被改动过的世界包会被明确拒绝，而不是静默替换世界。**

### `world.json` —— 世界信息

| 字段 | 说明 |
|---|---|
| `schema_version` | 固定 `worldgen.world.v1` |
| `world_id` | 世界唯一 ID（ASCII slug，如 `coastal_town_929711`）；实例目录、世界状态、移动记录都以它绑定 |
| `name` | 世界名（中文，如 `凪浦`） |
| `theme` | 模板主题 ID |
| `seed` | 随机种子（0..2^31-1） |
| `timezone` | IANA 时区（默认 `Asia/Tokyo`） |
| `generator_version` | 生成器版本；与运行环境不一致时**拒绝加载** |
| `description` | 一句话描述 |

### `topology.json` —— 空间信息

* `locations[]`：`location_id`、`name`、`kind`（home/street/school/store/park/beach/cafe/library/shrine/market/station/public）、`area_id`（室内区域，如住宅的 `bedroom`）、`description`、`reachable[]`（相邻地点）、`map{x,y}`（地图坐标）、`zone_id`（区域）。
* `edges[]`：`{from, to, walk_seconds}` —— 道路与步行耗时（路线权威）。
* 约定：住宅固定为 `home`（室内区域 `bedroom`）、学校固定为 `school`——这是 rc9 世界模块
  （家空间 / 学校 / 物品投递）依赖的规范 ID；其余 ID 由模板与种子决定。
* 结构保证：从 `home` 出发可到达全部地点（不存在"出不了门"），`reachable` 与 `edges` 严格一致。

### `rules.json` —— 地点规则与环境

* `location_rules{}`：每个地点的 `open_hour`/`close_hour`（null 表示全天开放）、`weekdays_only`（周末休息）、`affordances[]`（允许活动，如 `BROWSE_STORE`）。
* `weather_candidates[]`：天气候选（`condition` + 中文描述）。

### `npcs.json` —— 公共角色

`npcs[]`：`npc_id`、`display_name`、`age_classification`（minor/adult）、`public_role`、`home_base_area`、`public_description`、`windows[]`（出场时间窗：`weekdays`、`start_minute`、`end_minute`、`location_id`）。
**只有公开身份与出场时间**——NPC 的私人记忆、意愿、关系不由世界包定义（那属于角色自己的系统）。

### `ambient.json` —— 环境事件模板

`templates[]`：`event_type`、`location_id`、`summary`、`start_hour`/`end_hour`。
运行时按"世界本地日期"确定性物化为当日环境事实（普通日子可以没有戏剧事件）。

### `objects.json` —— 初始对象目录（声明性）

`objects[]`：`object_id`、`name`、`kind`（furniture/item）、`location_id`、`note`。
v0.1 中这是**声明性目录**（预览与未来经济/家具系统使用）；运行时物品权威仍在
rc9 的 `body_substrate` / 物品经济中，世界包不直接生成可拾取物。

### `content.json` —— 文本内容

`source`（`local` / `glm`）、`model`、`entries{}`：以地点 ID / NPC ID / `world` 为键的
介绍、氛围、公开备注、节庆候选。GLM 内容生成后固化于此。

---

## 3. 与 rc9 World Runtime 的接线方式

```
CHIYO_WORLD_PACKAGE=世界包目录 ──▶ world_runtime_data（components/world/scripts）
                                     │  解析 + 校验 + 缓存
                                     ▼
   world_living_skeleton   地点/可达/观察/移动（_LOCATIONS → 活动世界）
   world_travel_delivery   路线权威（WALKING_DURATIONS → 世界包 edges）
   world_daily_rhythm      营业规则（_RULES → rules.json）
   world_social_presence   公共 NPC（_NPCS → npcs.json）
   world_ambient_events    环境事件（_TEMPLATES → ambient.json）
   world_timeline          时间线 world_id 白名单（+ 活动世界）
   world_home_space        world_id 校验（活动世界）
                                     │
                                     ▼
        World/Body 服务、Resolver、授权、账本、身体 —— 引擎层零改动
```

* 未设置 `CHIYO_WORLD_PACKAGE` 时，一切行为与 rc9 完全一致（汐见市默认世界）。
* 新世界使用独立的 `CHIYO_WORLD_HOME`（实例目录）与 `CHIYO_WORLD_ID`，与汐见市存档互不可见。
* 移动仍走 `plan_move_to` / `apply_move_to`（旅行模式按 `walk_seconds` 计时，到达由
  `realize_due_travel` 落地）；提交仍走 Action Authorization（M14A 一次性授权）与
  Resolver（执行账本回执），WorldGen 不直接写任何运行时 JSON。

### 环境变量速查

| 变量 | 说明 |
|---|---|
| `CHIYO_WORLD_PACKAGE` | 世界包目录（绝对路径）；未设置 = 汐见市 |
| `CHIYO_WORLD_ID` | 运行时期望的 world_id（服务启动校验用） |
| `CHIYO_WORLD_HOME` | World/Body 实例数据目录（`HERMES_HOME` 同值） |
| `WORLDGEN_LLM_API_KEY` / `_BASE_URL` / `_MODEL` | GLM 内容生成配置 |

---

## 4. 明确的边界（诚实清单）

以下能力**未适配自定义世界**，相关开关在生成世界的演示中被明确关闭（返回
`FEATURE_DISABLED`，不伪造成功）；它们只对汐见市生效，行为不变：

* 学校系列模块（`world_school_*`：课程、事件、作业、连续性）——内部绑定汐见市 NPC 与
  `SCHOOL_LOCATION_ID` 语义。
* 物品经济（`world_property_economy`：购买/投递）——商店与投递地址硬编码为
  `home_goods_store` / `home/bedroom`。
* `world_body_substrate.SURFACES`（床面）与 M13A1 测试物——固定的汐见市家具声明。
* 公共存在上限（`MAX_PRESENCE_COUNT=3`）、环境事件每日上限（3 条/天）等骨架边界沿用。

其他已知边界：

* rc9 的移动是**相邻边粒度**的（一次移动走一条路）；跨镇路径由多段移动组成。
* `preview.html` 是静态快照，不含运行时状态；`--instance` 可叠加显示当前选中实例的位置。
* 世界包的 `objects.json` 是声明性的（见上）。
* 时区校验：有 IANA tz 数据库的平台上严格校验；无 tz 数据库的原生 Windows 下降级为
  格式校验（实例初始化与运行时仍会严格校验）。

---

## 5. 世界编辑器与双向桥接（v0.2）

仓库内置一个**独立的室内空间编辑器**（v0.4，Apache-2.0）：以区域为单位绘制
室内布局（墙/门/窗/地面）、导入家具图片、配置可执行交互，并用本地测试居民
实际走一遍。它有自己的世界包格式（`nyairo.world-package.v3`）和本地测试
运行时（`nyairo.editor-runtime.v3`），**与 rc9 World/Body 无关、不读写
运行时状态**，仅监听 127.0.0.1。

### 完整工作流（生成 → 编辑 → 回写 → rc9 运行）

```bash
# ① 生成
python -m components.worldgen generate --seed 929711 --theme coastal_town --out ./worlds/nagiura

# ② 转换为编辑器场景并导入
python -m components.worldgen editor-export ./worlds/nagiura
cd components/world-editor && python server.py --open   # http://127.0.0.1:8766
# 浏览器「导入世界包」选择 ./worlds/nagiura/editor-world.json

# ③ 在编辑器中修改：改名/描述、营业时间、增删道路、新建区域、
#    画墙/门/地板、摆放家具……然后点「导出 NyAiro 世界包 ⤴」，
#    输入原始世界包目录（如 C:\...\worlds\nagiura）。
#    编辑器以原始包为基础差量回写到 ./worlds/nagiura-edited。

# ③' 也可以用 CLI 回写（效果相同）
python -m components.worldgen editor-import \
  ./worlds/nagiura ./edited-world.json --out ./worlds/nagiura-edited

# ④ rc9 加载编辑后的世界并真实行动
python -m components.worldgen instance \
  --package ./worlds/nagiura-edited --home ~/.nyairo-worlds/nagiura-inst
python -m components.worldgen demo full \
  --package ./worlds/nagiura-edited --home ~/.nyairo-worlds/nagiura-inst
```

### 回写语义：原始包 + 编辑器修改 = 新包

`editor-import` 把编辑器导出的 v3 与"原始包的正向投影（期望视图）"做差量，
**只回写用户实际修改的字段**。因此编辑器不管理的信息——NPC 排班、环境事件、
节庆文本、seed/时区/生成元数据、休息日（weekdays_only）——原样保留，
不做名称匹配。ID 对应由"正向转换可重推"决定（回写时用原始包重算映射再求逆），
编辑器新建的实体直接沿用编辑器 ID，跨多轮编辑保持稳定。

各字段的支持情况：

| 编辑器操作 | 回写行为 |
|---|---|
| 修改地点名称/描述 | 更新地点与 content 介绍 |
| 修改营业时间 | 更新地点规则（**跨夜时段无法表达，明确拒绝**） |
| 增删道路连接 | 更新拓扑道路；新建连接的耗时按地图几何确定性推导 |
| 新建区域 | 创建新地点（kind：room→public / shop→store / outdoor→park）+ 规则 + 内容 |
| 删除区域 | 删除地点与相关道路/规则/内容；**被 NPC/环境事件/对象引用时拒绝并逐条列出** |
| 移动/放置/删除家具 | 更新 `objects.json` 与场景摆位；删除被引用地点时同样报告 |
| 画墙/门/地板 | 存入 `scene.json`（编辑器场景数据），再次导入时完整恢复 |
| 导入图片素材 | 被室内/家具引用的素材内嵌进 `scene.json` |
| 道路耗时 | 编辑器无此概念：既有耗时不可在编辑器中修改，请直接编辑世界包 |
| 区域 kind（room/shop/outdoor） | 仅对新建地点生效；已有地点保留原 kind（映射有损） |

`scene.json`（`worldgen.scene.v1`，可选文件，纳入 MANIFEST 覆盖）保存编辑器
场景数据：室内空间、物件摆位、图片素材。**它只服务于"再次进入编辑器"；
rc9 不消费这些几何，没有墙体碰撞**——rc9 的移动语义仍是地点图 +
相邻边粒度步行耗时。家具的交互用途（休息/阅读等）属于编辑器本地试运行
能力，`objects.json` 保持声明性目录，rc9 不据此伪造动作结果。

编辑器侧改动清单见 `components/world-editor/NYAIRO-BRIDGE-NOTES.md`。

---

## 6. 测试

```bash
# worldgen 套件（WSL/Linux 全量；Windows 原生自动跳过 POSIX 用例）
python3 -m unittest discover -s components/worldgen/tests -t .

# 编辑器自带套件
cd components/world-editor && python3 -m unittest discover -s tests -q

# rc9 world/service 回归（改动后必须保持 251 通过）
cd components && PYTHONPATH=world:world/service:world/service/bridge:world/scripts \
  python3 -m unittest discover -s world/service -q
```

覆盖：确定性（同种子同布局）、20 种子批量有效性、图连通性、错误场景（重复 ID /
悬空道路 / 断开图 / 版本不匹配 / 清单篡改）、多世界隔离、按路线耗时移动、
营业时间随包规则变化、NPC 与环境事件来自世界包、持久化（重载后位置与移动记录保留）、
GLM 请求/解析/合并链路（本地 mock 服务器）、**双向桥接（零差量往返 / 全部支持
的修改 / 删除与引用冲突 / 双世界隔离 / 场景持久化 / 编辑后世界的真实运行时行为）**。

验收执行记录与真实兼容边界见 `docs/WORLDGEN.zh-CN.md`。

## 7. v0.3：AI 内容与室内空间自动生成

### 自动室内布局（程序几何，无需 API Key）

```bash
# 全城镇：为所有室内建筑生成空间与家具（写入 scene.json + objects.json）
python -m components.worldgen interior ./worlds/nagiura

# 只生成一栋；--seed 覆盖种子（默认由世界种子+地点 ID 派生，可复现）
python -m components.worldgen interior ./worlds/nagiura --location bookstore --seed 271828
```

支持的建筑：住宅（卧室/客厅/厨房）、书店（书架/阅读区/收银台）、咖啡馆
（吧台/咖啡机/座位）、便利店（货架/收银台/冷藏柜）、教室（课桌椅面向讲台/
黑板/教具柜）、图书馆/市场/商铺/公共建筑。程序负责全部几何与合法性：

* 墙门成房间、入口可达每扇门两侧（复用编辑器 path/blocked 做通行性检查）；
* 家具不重叠、不堵门与入口邻域；书可叠放桌/柜上（视觉堆叠）；
* 家具朝向符合用途（教室座椅 rotation 180 面向讲台）；
* 同种子逐字节复现；不同种子产生分区/门窗/家具数量的实际差异
  （9 类建筑 × 100 种子稳健性 900/900）。

生成的室内在 `preview.html` 中以"室内布局"卡片可视化（编辑器同款家具素材）。

### GLM 语义设计与内容丰富化（需 API Key）

```bash
# 真实连通性自检（最小调用 + Token 用量打印）
WORLDGEN_LLM_API_KEY=sk-... python -m components.worldgen llm-check

# 丰富世界内容：地点介绍 / NPC 公开背景 / 环境事件候选（固化进包）
WORLDGEN_LLM_API_KEY=sk-... python -m components.worldgen enrich ./worlds/nagiura

# 室内生成叠加 GLM 语义设计（失败自动回退程序化并记录警告）
WORLDGEN_LLM_API_KEY=sk-... python -m components.worldgen interior ./worlds/nagiura --glm
```

GLM 只回答语义问题（风格、功能分区、家具组合建议、可观察细节、日常活动
候选），几何与合法性完全由程序决定；模型输出经过受限词汇校验——发明
家具种类/坐标/ID 的内容会被丢弃。每次调用记录模型与 Token 用量到世界包
目录的 `glm-usage.jsonl`（日志文件，不纳入 MANIFEST）。生成内容固化保存，
加载世界时不调用模型。

### 编辑器「AI 生成室内布局」

选中区域 → 右侧检查器「AI 生成室内布局」：选建筑用途（可自动推荐）、
设种子 → 生成 → 确认应用。结果进入编辑器撤销栈（Ctrl+Z 可找回），保存
草稿后随世界包回写。已有场景不会被无提示覆盖（应用前双重确认）。

### 边界（如实声明）

* 室内几何与家具交互是编辑器本地能力：**rc9 不消费墙体/地板，没有墙体
  碰撞与家具动作**；生成的家具不冒充角色已拥有或使用过的物品；
* 黑板用一段深色短墙表达（素材库暂无黑板物件）；书架/货架/柜台由
  cabinet/table 类家具承担（不新增家具种类）；
* GLM 真实调用在本轮验收环境中未验证（可用 Key 全部 401 过期，见交付
  报告）；`llm-check` 供 Key 可用时一键验证。

## 8. v0.4：可探索的生活世界（室内导航与局部感知）

### 室内导航（离散空间，复用既有 World/Body）

世界包 `scene.json` 的 `navigation` 声明每栋建筑的功能区域与连接：

```json
{
  "navigation": {
    "bookstore": {
      "areas": [{"area_id": "shopfront", "name": "店前区", "kind": "entrance"},
                 {"area_id": "reading_area", "name": "阅读区", "kind": "reading"}],
      "connections": [{"from": "shopfront", "to": "reading_area"}],
      "entrance": "shopfront",
      "furniture_map": {"shopfront": ["bookstore_shelf01"], "reading_area": ["bookstore_table07"]}
    }
  }
}
```

导航语义（全部复用既有 place 图 / travel / 授权 / 账本，无第二套位置存储）：

* 建筑内部区域以**复合节点 ID**（`place/area`）并入既有步行耗时表；
* place 节点即建筑入口；**非入口区域不能直达街道**（必须先回到入口区）；
* `world_state.location` 写拆分后的 `{place_id, area_id}`；移动目标写
  `place/area`（入口区可写 `place` 或 `place/entrance`，二者归一化同义）；
* 移动仍走 `plan_move_to / apply_move_to`（travel 模式按区域边 30 秒计时），
  提交仍走 Action Authorization → Resolver → 执行账本；
* 无 `navigation` 的世界包（v0.3 及汐见市）自动退化为单区域，行为不变。

```bash
# 室内导航体验（隔离实例）
python -m components.worldgen instance --package ./worlds/nagiura --home ./inst
python -m components.worldgen demo full --package ./worlds/nagiura --home ./inst
# 世界层场景会沿节点图寻路：住宅→客厅→街道→书店→阅读区→……
```

### 局部环境感知

`observe_world` 新增 `local_environment` 块（进入有导航的建筑后）：
当前建筑、功能区域、可见家具（≤8 件，来自 scene 摆位与 objects.json 名称）、
可通行的相邻区域。全部来自真实世界包，不含想象内容；家具交互用途标注为
编辑器本地试运行能力，rc9 不执行家具动作。

独立适配器 `components/worldgen/perception.py` 的 `observe_local` 提供
**三层信息**（静态世界包数据 / 允许观察的信息 / 需运行时状态的信息），
供编辑器「AI 视角预览」与测试使用；提供隔离实例目录时读取真实
`world_state.json`（只读），并在结果中标注来源。

### 编辑器

* 「AI 视角预览」：房间检查器内查看该位置的三层感知信息（可关联隔离实例
  读取真实运行状态）；结果不冒充 World/Body 观察回执；
* 地图 ↔ 室内切换为编辑器 v0.4 既有能力（objects/space 域切换 + 室内画布
  工具栏），本轮验证并记录，未做重复开发。

### GLM 增强

* `propose_world`（世界设计提案，两阶段生成第一步）：模型提出地区风格、
  槽位加权、公共活动、区域生活特点；程序据此调整生成（未知槽位丢弃，
  提案不承担拓扑权威）。`generate --proposal` 与 `batch --review` 已接通；
* NPC 公共日程扩展：工作窗口对齐营业时间（店员不会打烊上班）+ 下班后/
  休息日的公共活动窗口（公园/街道/咖啡馆）；
* 用量记录：模型名（含网关回报名）、Token、purpose、时间戳 → `glm-usage.jsonl`。

### 批量世界实验（4 主题 × 10 种子）

```bash
python -m components.worldgen batch --out ./experiments   [--consistency-days 7] [--review]
```

结构质检（连通性/导航完整性/NPC 营业一致性）+ 长期一致性模拟（7 天 ×
3 时刻：事件物化确定性、营业规则稳定、event_id 不重复）+ 内容规则检查
（跨主题重复率）。三轮改进记录（36→8→0 issues + GLM 评审反馈消除风味句
复用）见交付报告；`examples/batch/batch-report-*.json` 为实测报告。
