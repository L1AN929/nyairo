# Agent Note: v0.4 阶段 B · 资料库、研究工作台与手机面

Status: implemented

## Problem

主体此前能续写「自己那一件作品」，但没有任何一处能回答：**这个问题我是从哪份材料里看出来的？
那份材料现在还是我当时看到的那个版本吗？** 具体的空缺：

- **没有资料视图**。Supply 里本来就有 `ManagedArtifact`（阅读材料、私人事项、作品都在里面），
  但没有任何一层能"检索一份材料、看它的来源声明和版本"。手机只能从阅读屏一页页翻。
- **来源版本会悄悄过期**。材料被更新或撤回之后，旧笔记/旧结论没有任何地方标出来；界面也无从
  区分"这是当时那个版本"和"现在是另一个版本"。
- **没有一个可以合法续写的"研究作品"**。要么写进普通项目（引用与问题无处记录），要么另建一张表
  （阶段单明令禁止第二 store / 第二 Memory / 新数据库）。
- **资料容易被误当作品**。如果把带资料标记的 artifact 混进项目列表，"修改正文"会改写第一行标记，
  把一份材料悄悄变成一件作品，或者把研究的引用抹掉。
- **诚实边界**：不能联网抓取，不能声称"读过每一页"，研究内容不能被说成 Memory 或稳定信念。

## Decision

只在本轮 B 的写集内实施（新增 `components/life/scripts/research_workspace_service.py`、
`components/phone/{service.py,server.py,web/**}`、新测试、docs、本笔记）；不动
`private_project_service.py`、`phone_reading.py`、A 的 Life 源文件、父级进度/阶段单。

1. **资料与研究都仍是同一 owner 的 `ManagedArtifact`**。服务只经 `PrivateProjectService`
   （它自己只经 `LifeArtifactPort`）做 `list_artifacts / read_artifact / create_artifact /
   commit_version / archive_artifact`。没有新表、没有新 JSON 库、没有第二份 Memory。
2. **类型靠第一行公开标记**：资料 `<!-- nyairo-material:v1 {JSON} -->`，研究
   `<!-- nyairo-research:v1 {JSON} -->`（question / source_refs / scope / uncertainty）。标记只认
   第一行、只在第一行；`is_material_content()` / `is_research_content()` /
   `parse_research_content()` / `build_research_content()` 是给 A 用的公开 helper，A 不必复制
   第二套 marker 定义。
3. **导入只新建，不覆盖**：`import_material` 只有一个 `create_artifact`（一 key 一件）。
   既有 `reading_artifact_id` 作为**只读资料**出现在列表里（`source_kind=reading_material`,
   `read_only=true`），可检索、可引用，但导入永不改写它。`source_kind` 只有
   `user_supplied` / `fictional`，`origin` 是**声明**；本模块没有 http 客户端，不联网抓取。
4. **来源核对是创建的前置条件**：`create_research` 先逐个读来源工件，得出
   `CURRENT / STALE / UNAVAILABLE`。任一 `UNAVAILABLE`（读不到 / 不是资料 / 已归档撤回）就
   `rejected + RESEARCH_SOURCE_UNAVAILABLE`，**一件工件都不建**，绝不虚构"已取得内容"。
   `STALE` 允许创建：引用的是调用方点名的那个版本，界面与详情照实标 STALE。
5. **续写保留 marker 与引用**：`update_research` 用 owner 里那份 marker 重建第一行（question 与
   source_refs 不被 writer 改写），正文用调用方交来的那段；CAS 必须带 `expected_head`，缺就抛
   `ResearchContractRefused`，陈旧 head 由 owner 答 `conflict`。关闭走 `archive`
   （`expected_revision`），正文与每个版本全留；关闭后再写 → `rejected / RESEARCH_ARCHIVED`。
6. **答复永不抛**：owner 说了什么都是带 `status` 的 dict（与 `private_project_service` 同一词表
   `ok/applied/already_applied/conflict/not_found/rejected/unknown/unavailable`）；UNKNOWN 由项目
   服务按**原 key** `resolve_operation` 对账后才谈重试，本模块只透传 `reconciled` / `retryable`。
7. **手机是可用的工作台**：新增 `GET/POST /api/phone/materials`、`GET /api/phone/materials/<id>`、
   `GET/POST /api/phone/research`、`GET /api/phone/research/<id>`、`POST .../updates`、
   `POST .../close`。保留既有全部闸门（loopback / Host / Origin / Sec-Fetch-Site / CSRF / 白名单 /
   `CLIENT_IDENTITY_REFUSED`）；`source_refs` 是唯一允许出现 artifact id 的嵌套位置，每项只收两个键；
   GET 一律纯读（测试盯着 owner 侧零写）。界面新增 `materials / material / material-import /
   research / research-item / research-create / research-edit` 七屏 + 主屏两个行内入口
   （**不加 tile，不动 `APP_ORDER` / `HOME_ORDER`**）；来源状态三档 chip，`sources_current=false`
   时屏上明写"旧结果不等于当前已核实"，研究详情常驻"不是 Memory，也不是稳定信念"。
8. **普通项目列表只显示类型，不改旧服务**：`GET /api/phone/projects` 的行加
   `artifact_type ∈ project|material|research|unknown` 与 `continuable`（类型只来自第一行标记，
   最多核对 50 行）；`POST .../projects/<id>/commits` 对资料/研究回 409
   `PROJECT_IS_A_MATERIAL` / `PROJECT_IS_A_RESEARCH`，避免从项目屏改写 marker。

## Alternatives considered

- **给资料/研究另建一张表或一个 JSON 文档**（像 `private_daily_items` 那样）。否决：阶段单明令
  不建第二 store/数据库；一旦另存，既有材料与旧作品就要迁移重建，违背"旧数据继续可见不迁移"。
  真正需要的是"同一个 owner 上的两种视图"，不是第二个库。
- **把类型写进工件标题或另存元数据列**。否决：标题会被 A 的 display ref 截断/改写，且"哪件是资料"
  要能被任何一层第一次读就判断出来；第一行公开标记是可检测、可读、可被 A 独立解析的，而隐式元数据
  需要新的读写通路（新表/新字段 = 第二种真相）。
- **把类型标记放文末，让正文从第一行开始**。否决：阶段单要求 marker 在第一行、writer 只能往下写；
  放文末等于要求 writer"刚好不覆盖最后一行"，那是把契约交给最容易出错的写者。放第一行 + 由服务在
  每次提交时重建，才是 writer 无法破坏的形状。
- **导入时允许覆盖已有材料（就地"更新资料"）**。否决：`reading_artifact_id` 是 C 的阅读工作台正在用的
  材料，覆盖它就是替别人改正文；而且"资料更新"在这套 owner 里本来就该是"新版本/新工件 + 引用版本号"。
  所以导入永远新建，旧材料只读。
- **来源 STALE 时禁止创建研究**。否决：STALE 是"我引用的那个版本已经过时"，不是"我没有读不到"。
  禁止它会让"当时读到的东西"无法被立项，也会逼调用方把版本号改成当前的（那才是编造）。正确做法是
  允许创建并**照实标 STALE**。
- **手机 `/state` 里加 materials / research 两个 app 项**。否决：`/state` 是每次连接都跑的轻量面，
  加进去就等于每次 state 都做一遍有界 owner 扫描；屏本身已经有"服务不可用/未连接"的状态面板，
  不需要在 state 里重算一遍。
- **让研究屏也能用项目屏的"修改正文"**。否决：那条写路径交上去的是"整段正文"，会覆盖第一行 marker
  （引用与问题就此消失）。研究有 `updates` 自己的路（服务重建 marker），项目写路径对研究**具名拒绝**。

## Consequences

- 收益：主体第一次能"检索自己手里的材料、看它的来源声明与版本"，并把一个具体问题 + 具体来源立成
  一件**可续写、可关闭、带引用**的研究作品；来源更新/撤回会在读视图里标 STALE / UNAVAILABLE；
  手机上有七屏真正能操作的界面（导入、检索、起点选择、续写、关闭），且资料不会被误当作品。
- 代价 / 已知限制：
  - 不联网抓取，`origin` 只是声明；研究正文只写"问题 / 范围 / 引用声明 / 暂定发现 / 未知"。
  - 列表每件工件读一次 head（`MAX_SCAN=200`），不是全库索引；`truncated` 照实报。
  - 类型识别看第一行标记：手写一件恰好以同样标记开头的普通作品会被当成研究（公开标记的代价）。
  - 研究不能改名、不能删除（owner 没有改名动词；关闭即归档）。
  - 来源变化只标记，不自动重读：要不要重读、要不要改写，由 A 的自主路径或操作者决定。
- 不变量：无新表/新库/新 Memory；无迁移；无删除（只有 archive）；GET 零写；写必须 CSRF + CAS；
  `private_project_service.py` 与 `phone_reading.py` 一字未改；`APP_ORDER` / `HOME_ORDER` 未动。
- 给 A 的接缝：`ResearchWorkspaceService` 固定构造与九个方法、`research_from_env(port, env)`
  （缺一件 env 返回 `None`）、`parse_research_content` / `build_research_content` /
  `is_material_content` 三个纯 helper；详见 `docs/engineering/v04-research-workspace-result.md`。
