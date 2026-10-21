# Agent Note: v0.4 阶段 B · 私人多项目 owner 服务、提案器与手机工作台

Status: implemented

## Problem

主体此前只能续写「预置的那一件作品」：能写的只有启动时绑定的那个 artifact，没有「我有几份作品、
各自到哪一步、我想再开一份新的」这件事。具体的空缺：

- **没有项目视图**。Supply 里本来就有 `ManagedArtifact`（旧作品、阅读材料、私人事项文档都在里面），
  但没有任何一层能回答「她现在有几份作品」，也没有一处让「阅读 → 笔记/疑问 → 立项 → 修改 → 收尾」
  这条链真的落地。手机屏上更是只有笔记与阅读，作品只能被动等主循环去续写。
- **分类不清楚**。阅读材料与私人事项文档也是 artifact；如果直接「列出所有 artifact 当作品」，
  材料会被当成她写的东西，私人事项 JSON 会被当成一篇文章。必须由调用方**点名**哪些不是项目。
- **提案与创建容易混为一谈**。如果「模型提了一个方向」就等于「建了一个项目」，那她从来没有选择过，
  而且一个没读过材料就冒出来的标题会被写成她的话。
- **版本历史是半可读的**：owner 里版本不可变、都在，但现有 `LifeArtifactPort` 只保证 head。

## Decision

只在本轮 B 的写集内实施（`private_project_service.py`、`private_project_cognition.py`、
`components/phone/{service.py,server.py,web/**}`、新测试、docs）；不动 Life 主循环/assembly/runner/
既有 port（需要 port 新方法的地方写进 result 交给 A）。

1. **项目就是 ManagedArtifact**，没有任何第二作品库、钱包或第二 Memory。服务只经窄 port 调用
   `list_artifacts / read_artifact / create_artifact / commit_version / archive_artifact`，
   并在构造时先检查这几个方法在不在（缺一个就 `ProjectUnavailable`，不留半可用对象）。
2. **分类靠调用方点名**：`PrivateProjectService(port, workspace_id, grant_id,
   excluded_artifact_ids=(...))`。手机把实例里的 `reading_artifact_id` 与
   `private_items_artifact_id` 传进去；直接 `read_project` 一件材料会诚实回答
   `PROJECT_ARTIFACT_EXCLUDED`（404），而不是假装不存在。**旧 artifact 不迁移、不重建、不打标记**，
   列表里照样出现。
3. **写就是 owner 的词**：创建 = `create_artifact`（owner 自己提交第一版）、修改 = `commit_version`
   （必须带 `expected_head` 做 CAS）、收尾 = `archive_artifact`（`expected_revision`，归档不是删除）。
   缺 `expected_head` / `expected_revision` 一律在本地拒（`ProjectContractRefused`），不发出去。
4. **owner 的答复永不抛异常**：`applied / already_applied / conflict / not_found / rejected /
   unknown / unavailable` 七个状态都是 dict。UNKNOWN 先用 `resolve_operation` 对账：确认已落地就
   报 applied+reconciled；owner 明确说没落地就报 unknown+retryable（同 key 可重试）；
   无法确认就 unknown+不可重试。
5. **历史照实说**：port 有 `list_versions` 就 `complete=True` 列全；没有就 `complete=False` +
   `reason_code=PORT_LACKS_LIST_VERSIONS`，`entries` 只放 owner 刚给的 head（号码/父版本/字数）。
   不给 A 的 port 加方法，而是留可选项让 A 自己接。
6. **提案永远不写 owner**：`private_project_cognition` 不 import 服务、不持 port。它只把
   「材料状态 + 她自己的疑问」压成有界 seed，产出 ≤3 条纯结构提案（`requires_selection: True`）；
   创建前必须 `select_proposal(...)` 明确选一条，再 `chosen_project_payload(...)` 交给
   `create_project`。模型失败是 `ProjectProposalError`/`model_error`，绝不半解析成一条提案。
7. **给 A 的适配器**：`LifeProjectOwner` 提供 A 文档里那一套
   （`list_projects() -> {"status": "OK", ...}`、`create_project(title=, seed_text=, source_refs=,
   operation_key=, workspace_id=, subject_id=)`），`private_projects_from_env(...)` 是 A 的
   `resolve_gateway` 会去找的工厂；环境不全会抛，A 那边就答
   `PRIVATE_PROJECT_OWNER_UNAVAILABLE`，不造假。
8. **手机是可用的工作台**：`/state` 新增 `projects` app；`GET /api/phone/projects`（列表，不含正文）、
   `GET /api/phone/projects/<id>`（正文 + head + 历史）、`POST /api/phone/projects`、
   `POST .../commits`、`POST .../close`。GET 是纯读（宿主/来源闸门，零写）；写保留 CSRF、
   请求体白名单、真实 CAS 与 `phone/operations.jsonl` 归因。界面新增 `projects / project /
   project-edit` 三屏 + 主屏「私人项目」行内入口；**不加第九个 tile、不动 `HOME_ORDER`**，
   正文用 `pre-wrap` 呈现，历史不全时屏幕上照实写「只有当前版本是确定的」。

## Alternatives considered

- **给项目建一张自己的表 / 一个 JSON 文档**（像 `private_daily_items` 那样）。否决：作品必须仍是
  Supply 的 ManagedArtifact（阶段单明令禁止平行作品库）；一旦另存，旧的三个 artifact 就要迁移，
  违背「旧数据继续可见不迁移重建」。
- **给 `LifeArtifactPort` 直接加 `list_versions` 并顺手改 port**。否决：port 是 A 的写集，B 改了就是
  两个代理同时写同一个文件；改成「可选能力 + 结果里点名要 A 接」更安全，A 一接上，
  `history.complete` 自动变 True，B 一行都不用再改。
- **把「提议 → 创建」做成一步**（提案即项目）。否决：那是替主体做选择，而且会把她没读过的东西写成
  她的话。保留 `select_proposal` 这一步，是这条链唯一诚实的形状。
- **手机端自己拼项目列表**（用 `GET /reading` 之类现成页拼出来）。否决：那就是在界面上重算 owner 事实；
  而且列表会混进材料。
- **标题也能在手机上改**。否决：owner 的 `commit_markdown` 没有改名动词，硬做只能「新建一件 + 归档旧的」，
  会把版本历史切断；界面因此把标题在编辑态显示为只读，并说明「创建后不改名」。

## Consequences

- 收益：主体第一次能拥有**多份**作品，且每一份都能被列出、读回、改版、归档；阅读材料与私人事项
  被明确排除；创建必须显式选择；提案永不落盘；手机上有真正能操作的三屏。
- 代价 / 已知限制：
  - 版本历史目前只有 head 一条（`complete=False`）。要列全，A 需要在 `LifeArtifactPort` 上加
    `list_versions(artifact_id)`（映射 Supply 的 `artifact_versions`，`store.read_version` 已在）。
  - A 的 `life_project_options._artifact_id_of` 会剥掉 `artifact:` 前缀；为了让 A 拿到的 display ref
    仍能回查，服务把「裸十六进制 id」规范回 `artifact:<hex>`（只认 8–64 位十六进制，其它拼写原样保留）。
  - 项目没有「标题重命名」「删除」这两个动作：owner 没有对应动词，B 不假装有。
- 不变量：无新表/新库/新钱包/新 Memory；无迁移；无删除（只有 archive）；GET 零写；
  写必须 CSRF + CAS；`APP_ORDER` 与 `HOME_ORDER` 一字未改。
