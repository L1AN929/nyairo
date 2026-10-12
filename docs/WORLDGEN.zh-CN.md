# NyAiro WorldGen v0.1 交付报告

分支：`feature/worldgen-rc9`（基于 `v0.1.0-rc9`）
日期：2026-10-11
使用说明与世界包格式：见 [components/worldgen/README.md](../components/worldgen/README.md)

## 一、交付物清单

1. **开发分支源码** —— `components/worldgen/`（生成器，纯标准库）+
   `components/world/scripts/world_runtime_data.py`（运行时接线层）+ 七个世界模块的最小适配。
2. **世界包格式与中文字段说明** —— `components/worldgen/README.md` 第 2 节。
3. **两个示例世界**（不同种子、不同主题，均通过校验与完整运行演示）：
   * `examples/worlds/nagiura` —— 海滨小镇 `凪浦`（seed 929711，17 地点 / 18 道路 / 3 NPC）
   * `examples/worlds/wakaba` —— 生活小镇 `若叶`（seed 20261014，16 地点 / 17 道路 / 6 NPC）
   * 各自包含 `preview.html` 本地地图。
4. **中文使用说明与运行命令** —— `components/worldgen/README.md`。
5. **本地地图预览** —— `python -m components.worldgen preview ...`（HTML/SVG，区域/道路/耗时/
   营业时间/活动/NPC/实例当前位置）。
6. **隔离 World/Body 运行演示** —— `python -m components.worldgen demo full ...`。
7. **测试结果与真实兼容边界** —— 本文第三节、第四节。
8. **世界编辑器**（v0.4 并入）—— `components/world-editor/` + 桥接命令
   `editor-export`（见第五节）。
9. **v0.2/v2 迁移建议** —— 本文第六节。

## 二、架构与关键决策

* **世界包（静态定义）与运行时状态（权威）严格分离**：角色当前位置、持有物、
  已发生事件、身体状态仍由 rc9 既有模块管理；世界包只描述"世界是什么样的"。
* **引擎层零改动**：Resolver、Gateway、授权、执行账本、身体基座、body runtime、
  时间线存储全部原样使用。适配集中在数据层：
  `world_runtime_data.active_world()` 成为五个世界模块的数据入口，
  环境变量 `CHIYO_WORLD_PACKAGE` 指向世界包；未设置时返回内建汐见市数据
  （即原模块常量），因此默认路径零行为变化。
* **确定性生成**：`random.Random("nyairo-worldgen|<版本>|<主题>|<种子>")`；
  同种子 + 主题 + 生成器版本产生逐字节一致的拓扑/规则/NPC/环境事件。
  LLM 只产出 `content.json` 且固化进包，加载不调用模型。
* **规范 ID 约定**：住宅 `home`（区域 `bedroom`）、学校 `school` 固定；
  其余地点 ID 由模板决定。这是家空间 / 学校 / 物品投递等既有模块的依赖前提，
  验证器将强制检查（不满足即拒绝）。
* **版本与完整性**：世界包带 `generator_version` 与 SHA256 清单；版本不匹配或
  文件被改动时拒绝加载（明确报错，不静默替换世界）。

## 三、验收记录（施工单第七节）

执行环境：WSL2 Ubuntu 22.04，Python 3.12.3（仓库位于 `/mnt/c`）；
生成/校验/预览另在原生 Windows Python 3.13 验证。

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| A | 原世界兼容 | **通过** | `world/service` 回归 **251/251 通过**（接线前后各跑一次）；`test_shiomi_observe_and_move_unchanged` 验证汐见市观察/移动/路线（home→nearby_street=300s）与常量一致 |
| B | 随机性 | **通过** | `test_20_seeds_valid_and_traversable`：20 个种子 × 双主题全部通过结构校验、全对可达、耗时边界合法 |
| C | 可复现性 | **通过** | 同种子双次生成逐字段一致（`test_same_seed_same_layout`）；GLM/本地内容固化进包，清单校验保证重读一致（`test_manifest_tamper_detected` 反向验证） |
| D | 世界加载 | **通过** | 两个示例世界分别初始化隔离实例；`test_worlds_do_not_cross_contaminate` + `demo full`：world_id / 地点 / 规则互不渗透 |
| E | 实际移动 | **通过** | `demo world`：home→street_1（120s）→bookstore（两段移动、按路线耗时到达、`realize_due_travel` 落地、revision 1→3、travels=2）；服务链 `submit_action` MOVE `applied` |
| F | 时间变化 | **通过** | 同一地点 10:30 `OPEN`+有活动，23:00 `CLOSED`+无活动（`demo world` 步骤与 `test_opening_hours_follow_package_rules`）；即时移动模式下进入打烊店铺被拒 `LOCATION_CLOSED` |
| G | 持久化 | **通过** | `demo restart`（新进程）：world_id / 位置 / 移动记录全部保留，包未重新随机；单元级 `test_move_with_route_duration_and_persistence` |
| H | 错误场景 | **通过** | 重复地点 ID、悬空道路、断开图、reachable 与边不一致、版本不匹配、缺营业规则、home 区域违规、重复 NPC、无效时区、非法 ID 字符、清单篡改——全部明确中文报错（`test_validator_errors` 12 项），CLI `validate` 退出码非 0 |
| I | 现有测试 | **通过（如实记录）** | `scripts/test.py` 全量：closeout/native/memory/supply/**world**/life PASS；host-boundaries FAIL——**该失败在 rc9 原始标签上逐项一致（7 failures/9 errors/21 skips），为 rc9 预存问题，与本次改动无关**；cognition-faults/Hermes 因环境无 pytest SKIP |

演示输出实例（`examples/worlds/nagiura`，实例 `/tmp/wg-accept-nagiura`）：

```json
{"phase": "world", "final": {"location": {"area_id": "bookstore", "place_id": "bookstore"}, "revision": 3, "travels": 2}, "steps_ok": true}
{"phase": "restart", "checks": {"world_id_stable": true, "location_preserved": true, "travels_preserved": true, "package_not_rerandomized": true}}
{"move": {"from": "bookstore", "to": "street_1", "status": "applied", "reason_code": "OK",
  "authorization": "CONSUMED", "applied_to_body": true}, "ledger_state": "committed",
 "rejected_without_authorization": "AUTHORIZATION_REQUIRED",
 "rejected_unreachable": {"status": "rejected", "reason_code": "NOT_REACHABLE", "target": "beach"}}
```

`demo service` 走的是完整 rc9 服务栈：`load_config(canonical/production)` →
`WorldBodyService.startup()`（权威租期 / 世界绑定 / 账本校验 / 身体引导 / 对账）→
`GatewaySurface` → `AuthorizationRegistry.issue` → `submit_action`（一次性授权消费）→
Resolver → 执行账本 `committed` → 身体后果落地。

## 四、真实兼容边界（未做的事与阻碍点）

1. **学校系列 / 物品经济未适配自定义世界**（演示中明确关闭）：
   * `world_school_events.py:458` 绑定汐见市 NPC `npc:shiomi_npc_mio_shiraishi`；
   * `world_property_economy.py:58-60` 硬编码 `STORE_LOCATION_ID="home_goods_store"`、
     `HOME_ITEM_LOCATION="home/bedroom"`。
   最小后续修改点：把这两处常量改为从 `world_runtime_data` 读取（世界包已具备
   所需数据——地点规则、NPC、对象目录），工作量约等于本次五个模块的适配。
2. **`chiyo_bundle/world_read_service.py:47-54` 的 `initialize()` 仍写死汐见市**：
   它是现有用户的官方初始化入口，为不干扰既有路径未改动；生成世界改用
   `worldgen instance`（复用同样的规范存储写入器）。
3. **原生 Windows 不能运行 World/Body 运行时**：`fcntl`/`AF_UNIX` 为 POSIX 依赖
   （`world_body_substrate.py:41` 无条件 `import fcntl`）。这是 rc9 既有约束；
   本项目未做全库可移植性改造（超出范围），演示在 WSL 完成。
4. **移动是相邻边粒度**：rc9 `route_duration_seconds` 是单边查表，无跨图寻路。
   世界包因此把道路建模为"地点-街道"图，一次移动走一条边。
5. **`objects.json` 为声明性目录**：运行时物体权威在 substrate/经济；
   让世界包直接生成可拾取物需要接经济目录（见第五节建议 3）。
6. **GLM 真实调用未在交付环境执行**（无可用 API Key）；请求构造/响应解析/
   结构校验/合并拒绝幻觉 ID 的完整链路由本地 mock 服务器端到端测试覆盖
   （`test_glm_mock_server`）。设置 `WORLDGEN_LLM_API_KEY` 后即可用真实 GLM-5.3-Flash。

## 五、世界编辑器合并记录（v0.4 追加并入）

应用户要求，把独立开发的 **nyairo 世界编辑器 v0.4**（Windows 本地独立版，
世界包 v3 / 编辑器运行时 v3）完整并入本分支：

* **组件位置**：`components/world-editor/`（源码、web 前端、示例、测试、
  文档与截图随包保留；代码 Apache-2.0，自制示例素材 CC0-1.0）。
* **定位**：室内空间编辑器（区域/墙/门/窗/地面/家具/交互）+ 本地测试运行时，
  仅监听 127.0.0.1，无模型/账号/网络依赖；**与 rc9 World/Body 无关**，
  也不读写任何运行时状态（其 INTEGRATION.md 明确声明）。
* **与 worldgen 的桥接**（`components/worldgen/editor_bridge.py` + CLI
  `editor-export`）：worldgen 城镇包 → 编辑器 v3 场景。地点→区域（17 地点 →
  17 区域）、道路→区域连接、营业时间→`hours`、`objects.json` 家居→
  床/桌/柜/灯物件（按 object_id 前缀映射）；室内空间留空待用户绘制；
  转换结果经**编辑器自带的 `validate_package`** 校验（0 警告）。桥接单向，
  编辑器导出不回转为世界包。
* **验证**：
  * 编辑器自带 Python 测试套件 **39/39 通过**（仓库副本原样运行）；
    Node 开发测试未运行（环境无 Node，如实记录）；
  * worldgen 套件增至 **51 项**（含 8 项桥接测试）全部通过；
  * 服务器端到端冒烟（WSL，127.0.0.1）：HTTP 导入转换包 → 校验 0 警告 →
    创建试运行 → 床上休息（精力 65→80）→ 起身 → home→浪打道→海鸥咖啡
    两段区域移动 → 同执行 ID 重放幂等 → 跨区域操作灯被正确拒绝（422
    均为运行时规则的正确执行）。

## 六、接入 NyAiro v0.2/v2 的迁移建议

1. **世界包格式即迁移边界**：`worldgen.package.v1` 的七个 JSON 自包含、无 Hermes
   依赖（schema 校验/生成器为纯标准库）。v0.2 无论换聊天框架还是脱离 Hermes，
   只要实现"读取世界包 → 喂给世界模块"一个适配器即可，已生成的世界不会失效。
2. **把 `world_runtime_data` 升格为正式模块**：v0.2 建议将"活动世界"概念放入
   World Foundation（`world_state.json` 增加 `world_package_ref` 或独立注册表），
   替代环境变量注入；这是唯一建议的后续结构改动点。
3. **经济/家具接入路径**：世界包 `objects.json` → 经济 catalog 的映射器；
   `STORE_LOCATION_ID` 等常量参数化后，购买/投递即可在世界包地点上工作。
4. **多实例并发**：每个实例一个 `CHIYO_WORLD_HOME` 的模型可直接映射为 v0.2 的
   "存档槽位"；`instance.json` 已记录世界包路径与 world_id，可作为槽位元数据。
5. **LLM 内容升级**：`content.json` 的键空间（地点/NPC/world）是稳定的；
   换模型只需换 provider，建议未来为"世界人口音/节庆日历"增加独立键，
   而不是把叙事塞进地点介绍。

## 七、发行清单差异说明

本分支是开发分支，**不构成新的 rc9 发行版本**。根目录 `MANIFEST.json`
（`chiyo/source-release@1`，file_count 12232）保持 rc9 发行时的原始状态，
未为新增源码重新生成——由此它描述的是 rc9 发行树，而非本分支的工作树。
相对 rc9 发行清单的差异如实如下：

* **修改 8 个文件**：`components/world/scripts/` 下 7 个世界模块
  （living_skeleton / travel_delivery / daily_rhythm / social_presence /
  ambient_events / timeline / home_space）+ 新增接线层
  `components/world/scripts/world_runtime_data.py`；
* **新增目录**：`components/worldgen/`（生成器 + 模板 + 测试）、
  `examples/worlds/`（两个示例世界包）、`docs/WORLDGEN.zh-CN.md`；
* 其余文件（含 vendor/、native/、个人数据目录）未触碰；
* 本分支没有重打发行包，也没有修改 rc9 标签。

## 八、v0.2 交付：世界双向编辑与运行时闭环

基线 `bd0c2b7`（WorldGen v0.1 + 编辑器并入）。目标流程：
**生成 → 编辑器导入 → 用户修改 → 导出 → 回写 → rc9 加载 → 真实行动**。

### 交付物

1. **双向桥接**（`components/worldgen/editor_bridge.py`）：
   * 正向 `editor-export`：城镇包 → 编辑器 v3；带 `scene.json` 时恢复室内空间、
     物件摆位与素材（已编辑的世界再次进入编辑器不退回空白房间）；
   * 反向 `editor-import`：以**原始世界包为基础**做期望视图差量回写——
     只回写用户实际修改的字段，NPC/环境事件/内容/元数据原样保留；
     ID 映射由正向转换可重推（不做名称匹配），编辑器新建实体沿用编辑器 ID；
   * 删除地点时检查 NPC/环境事件/对象引用，有引用即拒绝并逐条列出；
   * 新增可选世界包文件 `scene.json`（`worldgen.scene.v1`，纳入 MANIFEST）。
2. **编辑器导出入口**（`components/world-editor`，改动清单见其
   `NYAIRO-BRIDGE-NOTES.md`）：`POST /api/nyairo/export` 端点 +
   「导出 NyAiro 世界包 ⤴」按钮；原始包不可用/不对应时明确拒绝；
   导出结果显示地点/道路/家具数量、校验结果、能否交 rc9 加载。
3. **示例世界对**：`examples/worlds/nagiura`（原始）与
   `examples/worlds/nagiura-edited`（经编辑器 HTTP 导出流程真实回写：
   书店改名"鸥岬书房"并改营业 12–18、删除 bookstore↔street_1 道路改接 park、
   新建 coffee_stand、书店内画墙开门、放置读书榻、移动台灯）。
4. **CLI**：`python -m components.worldgen editor-import <原包> <v3> [--out] [--force]`。

### v0.2 验收记录

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| A | 双向转换 | **通过** | 新鲜正向投影直接回写：收据零差量，七份核心文档逐字节一致，无 scene 文件；连续三轮往返保持一致（`test_roundtrip_is_byte_identical` / `test_repeated_roundtrips_stay_identical`） |
| B | 真实修改 | **通过** | 改名+描述、营业 12–18、删路改接、新建 coffee_stand、放置床、移动桌子、画墙——全部回写并通过校验；再次进入编辑器完整恢复（含墙与摆位）；原始包逐字节未动（`test_all_supported_modifications` / `test_original_package_untouched`） |
| C | 删除与引用 | **通过** | 删除被 NPC 引用的书店 → 拒绝并列出 `coastal_npc_bookstore_01`（不自动搬迁）；删除无引用的车站 → 成功并同步清理拓扑/规则/内容；删除书店唯一道路且不接回主图 → "道路图不连通"明确失败 |
| D | 世界隔离 | **通过** | 两个种子分别编辑回写：world_id 不同、修改互不影响、NPC 前缀不交叉（`WorldIsolationTest`） |
| E | rc9 真实执行 | **通过** | `test_edited_world_runtime`（4 项，使用提交的 nagiura-edited）：被删除的道路从街道出发不可直达（`LOCATION_NOT_REACHABLE`）；书店 12–18 在 11 点 CLOSED / 13 点 OPEN / 20 点 CLOSED，且即时移动在 11 点被 `LOCATION_CLOSED` 拒绝；coffee_stand 进入活动世界（8–14 规则 + BROWSE_STORE）；移动+重载后位置/移动记录/revision 保留。服务级：隔离实例 `demo full`（world 阶段沿新拓扑 3 段到达书店、revision 4、travels 3；service 阶段全新实例正向授权移动 `applied`+账本 `committed`；东京 22:01 从书店出发相邻公园已关闭 → 授权移动被正确拒绝——营业时间在服务层真实生效） |
| F | 回归 | **通过** | worldgen 套件 **65/65**（含 10 项往返 + 4 项运行时）；编辑器自带套件 **39/39**（含被本地改动的 server/web——其服务端行为由 HTTP 冒烟验证）；world/service **251/251**（汐见市行为不变）。Node `.mjs` 测试未运行（环境无 Node，如实记录） |

### v0.2 尚未适配的字段与能力（如实清单）

1. **道路耗时**：编辑器 v3 没有耗时概念——新建道路的耗时按地图几何确定性推导；
   修改既有耗时需直接编辑世界包（或用生成器）。
2. **区域 kind**：编辑器 kind（room/shop/outdoor）映射有损，仅对新建地点生效；
   已有地点的 kind 保留原值。
3. **跨夜营业时段**（如 22–6）：世界包规则无法表达，回写时明确拒绝。
4. **室内几何与墙体碰撞**：`scene.json` 仅用于再次进入编辑器；rc9 不消费墙体/
   地板，没有精细室内寻路。"到达地点"与"进入营业场所"沿用 rc9 现有地点模型
   （无独立出入口系统）。
5. **家具交互**：编辑器的休息/阅读/饮水等用途属于编辑器本地试运行能力；
   `objects.json` 是声明性目录，rc9 侧物体动作仍由既有经济/基座机制管理
   （自定义世界当前未接经济——v0.1 既有边界）。
6. **NPC/环境事件编辑**：编辑器没有对应概念，因此不能在编辑器中修改 NPC
   排班或环境事件；被引用地点的删除会以冲突形式拒绝。
7. **世界 ID**：编辑版本与其原始世界共享同一 world_id（它仍是同一座小镇）；
   运行隔离靠不同的实例目录（`CHIYO_WORLD_HOME`）。基于旧版本的运行实例
   切换到编辑版时，地点集合按新世界定义解析，角色既有状态不被重置。

## 九、v0.2 复现命令

```bash
# 双向桥接往返
python3 -m components.worldgen editor-export examples/worlds/nagiura
python3 -m components.worldgen editor-import examples/worlds/nagiura   examples/worlds/nagiura/editor-world.json --out /tmp/nagiura-rt
# 编辑后世界的隔离运行
python3 -m components.worldgen instance --package examples/worlds/nagiura-edited --home /tmp/inst-edited
python3 -m components.worldgen demo full --package examples/worlds/nagiura-edited --home /tmp/inst-edited
# 编辑器导出入口（HTTP）
cd components/world-editor && python3 server.py --port 8766
# 浏览器：导入 examples/worlds/nagiura/editor-world.json → 修改 →
# 「导出 NyAiro 世界包」→ 输入 examples/worlds/nagiura 的绝对路径
```

## 十、v0.4 交付：可探索的生活世界（室内导航 + 局部感知）

基线 08e9f08。核心能力断点：WorldGen 能创造室内空间，但 rc9 只能理解
地点级信息。本轮让 rc9 的 AI 真正走进建筑、在房间间移动并感知周围。

### 架构决策（最小修改方案）

室内导航 = 复合节点（place/area）并入既有导航图，不新增任何状态权威：

- world_state.location 的 {place_id, area_id} 结构本来就是 rc9 契约，
  v0.4 把 area_id 升级为真实导航节点（scene.navigation 显式声明区域与
  连接；无声明的旧世界包退化为单区域，行为不变）；
- place 节点即建筑入口；非入口区域不能直达街道（须先回到入口区）；
- 区域边（30 秒）与 place 边一起注入 travel 路线权威——移动/旅行计时/
  幂等/授权/账本全部复用，world_state 写拆分后的 {place_id, area_id}；
- 入口区与 place 节点同义（place/entrance 归一化为 place），三处
  （skeleton plan/apply、resolver 后置条件、travel realize）统一处理；
- World/Body Substrate、Body Runtime、Gateway、Foundation 零改动。

### 交付物

1. 室内导航：scene.json 新增 navigation 声明（区域/连接/入口/家具归属，
   纳入校验）；interior.py 全部模板显式声明区域结构；WorldData 节点 API
   （node_exists / node_projection / reachable_of / split_node / state_node /
   furniture_in_area）；skeleton/travel 接线。
2. 局部感知：observe_world 新增 local_environment（建筑/区域/可见家具<=8/
   相邻区域，全部来自真实世界包）；独立适配器 perception.observe_local
   （三层信息：静态世界包 / 允许观察 / 需运行时；可关联隔离实例读取真实状态）。
3. 编辑器：「AI 视角预览」端点与前端面板（三层信息区分、标注数据来源、
   不冒充 World/Body 回执）；地图与室内切换为 v0.4 既有域切换能力（验证记录）。
4. GLM 增强：propose_world 世界设计提案（两阶段生成第一步，受限词汇，
   未知槽位丢弃）+ generator 落实（槽位加权/描述/公共活动/区域生活）；
   NPC 公共日程扩展（工作窗口对齐营业时间 + 下班后公共活动窗口）；
   generate --proposal、batch --review 接通。
5. 批量世界实验：batch 命令（4 主题 x 10 种子 = 40 世界，含自动室内）
   + quality.py（结构质检 / 7 天一致性模拟 / 内容规则检查 / GLM 抽样评审）；
   新增主题模板 mountain_town（山间温泉）与 rural_farming（农田乡村）。
6. 示例：onsawa（GLM 提案生成的温泉小镇，含室内导航与固化内容）、
   inobe（GLM 提案生成的农田乡村）、mountain_town-demo / rural_farming-demo
   （新主题程序化示例）、nagiura/wakaba 室内升级为带导航。

### 验收记录

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| A | 室内真实导航 | 通过 | test_full_indoor_journey（住宅/客厅-街道-书店-阅读区-回入口-离开，全部 travel 计时+落地）；授权链版本经 demo full（授权 CONSUMED + 账本 committed） |
| B | 局部感知 | 通过 | 进入阅读区后 local_environment 给出建筑/区域/家具（阅读桌/阅读椅/阅读灯/蕨类盆栽）/相邻区域；离开建筑后该块消失；感知边界：家具全部来自本建筑 bookstore_* 摆位 |
| C | 室内编辑生效 | 通过 | 断开 shopfront-reading_area 连接回写后 reachable_of(bookstore) 不再含 reading_area（test_navigation_connection_edit_changes_reachability） |
| D | 持久化 | 通过 | 阅读区位置保存+重载保留（test_persistence_after_indoor_moves，4 段 travel 记录）；demo restart 四项检查全过 |
| E | GLM 真实调用 | 通过 | propose_world 真实生成两座世界（onsawa 1755 / inobe 1833 tokens，网关回报名 gemini-3.8-flash-n），提案落实后校验通过；batch --review 抽样评审真实调用（约 1000 tokens/世界） |
| F | 世界质量 | 通过 | 40 世界（4 主题x10 种子）三轮改进：基线 36 issues（内容重复率 100%）- 风味句注入 8 issues - 组合风味+质检规则合理化（跨主题 fail/同主题报告）0 issues - GLM 评审指出风味句世界内复用 - 洗牌去重分配+扩池+概率附加 - GLM 复评"无明显不合理之处"。报告：examples/batch/batch-report-v0.4.json |
| G | 旧版兼容 | 通过 | 汐见市 251 测试不变；v0.3 无导航场景退化单区域正常加载（test_legacy_scene_without_navigation）；双向桥接往返室内零丢失 |
| H | 回归测试 | 通过 | worldgen 91/91（+11 导航/感知/质检测试）；编辑器 Python 39/39 + Node 25/25；world/service 251/251 |

### v0.4 边界与未适配（如实清单）

1. 离散空间导航：区域是导航节点（30 秒/边），不做格级寻路、连续碰撞
   或完整物理；室内几何（墙/门）只服务于编辑器绘制与试运行，rc9 消费的是
   区域连接声明而非几何。
2. 导航连接是显式声明：编辑器修改墙壁几何不会自动重推导区域连接；
   用户可直接编辑 scene.navigation（或用生成器重新生成）。
   几何到连接的自动推导器列为后续工作。
3. 家具动作不接入 rc9：床/椅/柜等只出现在感知中（含编辑器用途标注），
   不接入 rc9 动作链——只有真正通过授权与执行链的交互才会声明为可执行
   （本轮为零，全部"仅供观察"）；公共家具不登记为角色个人财产。
4. 感知范围：local_environment 提供本建筑本区域的家具（<=8 条截断）；
   跨建筑信息只在静态层可见，不进入角色允许观察。
5. NPC：日程为公开世界设定（工作+公共窗口），不是自主 Agent；
   长期记忆、私人生活不生成。
6. 一致性模拟：7 天 x 3 时刻的可控时间推进（事件物化确定性/营业规则
   稳定/event_id 唯一），非 30 天真实运行。

## 十一、v0.4 复现命令

```bash
python3 -m components.worldgen instance --package examples/worlds/nagiura --home /tmp/inst
python3 -m components.worldgen demo full --package examples/worlds/nagiura --home /tmp/inst
WORLDGEN_LLM_* python3 -m components.worldgen generate --seed 700001 --theme mountain_town --out ./worlds/proposal-test --proposal
python3 -m components.worldgen batch --out ./experiments --review
python3 -m unittest discover -s components/worldgen/tests -t .
```

## 十二、v0.1-v0.3 历史章节（存档）

基线 `a54854a`（v0.2 双向桥接）。核心：**程序化空间布局 + GLM 语义设计**，
让生成的小镇拥有可直接编辑的、有生活气息的建筑内部。

### 交付物

1. **程序化室内生成器**（`components/worldgen/interior.py`）：九类建筑模板
   （住宅/书店/咖啡馆/便利店/教室/图书馆/市场/商铺/公共建筑），输出编辑器
   v3 原生 space（墙/门/窗/地面）与家具（只用编辑器九类家具与既有动作模板，
   不创造第二套格式）；建筑类型由 kind → 精确 ID → 名称关键词三级推断；
   同种子逐字节复现，跨种子实际差异。
2. **合法性校验**：编辑器 `validate_space` 全量校验 + 复用编辑器
   `path/blocked` 的通行性检查（入口可达每扇门两侧、自由格可达率、家具
   不占门格与入口邻域、家具间距≥1 格、书/杯允许在桌/柜上叠放）。
3. **CLI**：`interior`（单栋/全城镇，写 scene.json + objects.json）、
   `enrich`（GLM 内容丰富化）、`llm-check`（真实 API 自检 + 用量）。
4. **GLM 语义设计**（`providers/glm.py` 扩展）：`design_interior` 只接受
   受限词汇（九类家具、数量 1–6、无坐标无 ID）；`enrich` 增加环境事件候选
   （kind_hint 白名单）；所有调用记录模型与 Token 用量至 `glm-usage.jsonl`；
   GLM 失败自动回退程序化并记录警告，绝不阻断生成。
5. **编辑器「AI 生成室内布局」**：房间检查器内选择用途/种子 → 生成 →
   双重确认应用 → 进入撤销栈 → 保存后随世界包回写；端点
   `POST /api/nyairo/interior`（物件 ID 用 hashlib 派生，跨进程确定）。
6. **室内可视化**：`preview.html` 新增"室内布局"卡片（编辑器同款家具
   SVG 素材、墙门地板、入口标记）。
7. **示例**：`examples/worlds/nagiura` 与 `wakaba` 各含 12 处自动生成的
   室内场景（含教室课桌椅、咖啡馆吧台、书店书架阅读区、便利店货架等），
   预览与编辑器导出同步刷新。

### 验收记录

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| A | 真实 GLM 调用 | **通过（真实调用）** | 用户提供 OpenAI 兼容网关 `https://gw.929711.xyz/v1` + 模型 `gemini-3.8-flash-high`（网关路由回报 `gemini-3.8-flash-n`）。真实调用记录：① `llm-check` 连通性自检 594 tokens；② `enrich` 两座示例小镇（凪浦 4,426 / 若叶 5,411 tokens）：生成中文地点介绍、节庆候选、NPC 公开背景（3 处同步进 npcs.json）与环境事件候选（4 条/镇追加进 ambient.json，均为"定期发生的普通事实"，未宣称已发生）；③ `interior --glm` 书店语义设计 1,474 tokens：模型建议被采纳为"海滨画册书架×5、待售图书×4、麦茶保温桶×1、粗陶杯×2"等，家具 10→23 件，通行性保持 100%。用量全程记录于各包 `glm-usage.jsonl`；生成内容固化进包，重载/校验/预览不再调用 API。真实调用暴露并修复 4 个问题（见下） |
| B | 建筑生成 | **通过** | 住宅/书店/咖啡馆/便利店/教室五类（+图书馆/市场/商铺/公共建筑）全部生成有效空间：墙门成房间、入口可达每扇门两侧、自由格可达率 100%、家具不重叠不堵门；教室课桌椅面向讲台、黑板以深色短墙表达 |
| C | 多样性 | **通过** | 8 种子 × 建筑类型的家具布局去重 ≥6 种；9 类建筑 × 100 种子稳健性 900/900；同种子逐字节复现 |
| D | 编辑器联动 | **通过** | HTTP 端到端：AI 生成书店室内 → 模拟用户移椅/门偏移 +5/换地板色 → v0.2 导出回写 → 重新打开：门 offset 85、地板色、椅位全部保留；NPC/环境事件/12 处场景完整。过程中编辑器校验正确拦截了越界修改（门推出墙外 → 422） |
| E | 世界往返 | **通过** | 室内生成 → 正向桥接（编辑器场景含全部室内）→ 无修改回写 → scene 空间/摆位逐字节保留、NPC/环境事件零丢失（`test_interiors_survive_bridge_roundtrip`） |
| F | rc9 回归 | **通过** | 带室内的世界包加载至隔离实例 `demo full`：3 段移动（revision 3/travels 2）、重启持久化、服务链授权移动 `applied`+账本 `committed`+无授权拒绝——室内数据对 rc9 透明，不影响既有运行能力 |
| G | 自动化测试 | **通过** | worldgen **80/80**（含 15 项室内测试）；编辑器 Python **39/39**；Node `.mjs` **25/25**（WSL Node v22.19.0，v0.2 时漏检环境、本轮补跑）+ 4 个 JS 文件 `node --check` 通过；world/service **251/251**（汐见市不变） |

### v0.3 未适配与边界（如实清单）

1. **GLM 真实调用已完成**（用户提供的 OpenAI 兼容网关）；配置
   `WORLDGEN_LLM_BASE_URL` / `WORLDGEN_LLM_API_KEY` / `WORLDGEN_LLM_MODEL`
   三个环境变量即可复现；GLM 失败永不阻断程序化生成。
   真实调用暴露并已修复的问题：
   * 网关 Cloudflare 防护拦截 Python 默认 UA（403/1010）→ 请求增加
     `User-Agent` 头；
   * 模型用地点中文名称而非 id 作 entries 键 → prompt 强化 +
     `name→id` 宽容重映射（仅精确名称匹配，不构成发明；未知键仍拒绝）；
   * `design_interior` 误用 enrich 的输出校验（要求 entries 键）→ 拆分
     通用 JSON 提取与 enrich 校验，并兼容模型在 JSON 前后附带的说明文字；
   * `enrich` 首次运行在"写盘后的用量日志"步骤崩溃导致重复追加环境
     事件候选 → 已人工去重（凪浦 ambient 整理为 14 条），用量日志崩溃
     点本身已随 Path 导入修复消除。
2. **黑板**以深色短墙表达（素材库无黑板物件）；书架/货架/柜台由
   cabinet/table 承担，未新增家具种类或 SVG 素材（施工单允许扩充素材库，
   本轮优先复用既有素材）。
3. **室内几何与家具交互是编辑器本地能力**：rc9 不消费墙体/地板，无墙体
   碰撞、无室内寻路、无家具动作；生成的家具是声明性目录 + 场景摆位，
   不冒充角色的个人物品或已发生的经历。
4. **GLM 用量成本**记录 Token 数（模型名 + prompt/completion/total），
   未折算金额（单价随服务商变化，可后续按用量日志计算）。
5. **编辑器 AI 入口的 GLM 开关**：默认程序化；设置 `localStorage['nyairo-glm']`
   后走 GLM 语义设计（前端暂无独立开关 UI，CLI `--glm` 完整可用）。

## 十三、v0.3 复现命令

```bash
# 室内生成（全城镇 / 单栋）+ 预览
python3 -m components.worldgen interior examples/worlds/nagiura
python3 -m components.worldgen preview examples/worlds/nagiura
# GLM 自检 / 内容丰富化 / 语义设计（需有效 Key）
WORLDGEN_LLM_API_KEY=sk-... python3 -m components.worldgen llm-check
WORLDGEN_LLM_API_KEY=sk-... python3 -m components.worldgen enrich examples/worlds/nagiura
python3 -m components.worldgen interior examples/worlds/nagiura --glm
# 编辑器 AI 入口
cd components/world-editor && python3 server.py --open
# 测试三套件 + Node
python3 -m unittest discover -s components/worldgen/tests -t .
cd components/world-editor && python3 -m unittest discover -s tests -q && node --test tests/*.mjs
```

## 十四、v0.1/v0.2 历史复现命令（存档）

```bash
# 1) worldgen 测试套件
python3 -m unittest discover -s components/worldgen/tests -t .
# 2) world/service 回归
cd components && PYTHONPATH=world:world/service:world/service/bridge:world/scripts \
  python3 -m unittest discover -s world/service -q
# 3) 两个示例世界的完整演示
python3 -m components.worldgen instance --package examples/worlds/nagiura --home /tmp/inst-nagiura
python3 -m components.worldgen demo full --package examples/worlds/nagiura --home /tmp/inst-nagiura
python3 -m components.worldgen instance --package examples/worlds/wakaba --home /tmp/inst-wakaba
python3 -m components.worldgen demo world --package examples/worlds/wakaba --home /tmp/inst-wakaba
```
