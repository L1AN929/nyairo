# Agent Note: v0.4 阶段 B · 个人实践工作台（回顾 / 做法）与手机面

Status: implemented

## Problem

主体此前能"读自己的资料、起一份研究、续写一件作品"，但没有任何一处能回答：
**我到底经历过什么、我从哪次真实结果里学到一条做法、这条做法后来被验证过还是被打过脸？**
具体的空缺：

- **经历读不回来**。A 这一阶段才把实例 Memory 变成可显式启用、可检索的东西；在它之前，
  任何"回顾"都只能写成一篇文章，没法指出"我当时看到的是哪一次结果"。
- **没有做法的位置**。一条"先写三行再展开"的经验，要么写进普通项目（下次读的时候没有任何
  状态、范围、反例），要么另建一张表（阶段单明令禁止第二 store / 第二 Memory / 新数据库）。
- **草案容易被当成结论**。如果做法就是一件普通工件，"试了三次都有用"很容易在下一轮被
  读成"已验证"。本阶段明确不要 `SUPPORTED`，也不要人格 / 稳定偏好。
- **治理没有传播**。经历被撤回 / 删除 / 停止召回之后，如果回顾与做法还把当时那段正文
  留在自己正文里，等于从派生文档把被治理挡下的内容漏回来。
- **手机上没有一处能真的操作**：经历检索、回顾、做法、试用、修订、退休全都没有面。

## Decision

只在本轮 B 的写集内实施（新增 `components/life/scripts/personal_practice_service.py`、
`components/phone/{service.py,server.py,web/**}`、新测试、docs、本笔记）；不动
`private_project_service.py`、`research_workspace_service.py`、A 的 Memory 文件、C 的
Life 文件、父级阶段单与进度表。

1. **回顾与做法都是既有的 `ManagedArtifact`**。服务只经 `PrivateProjectService`
   （它自己只经 `LifeArtifactPort`）做 `list_artifacts / read_artifact / create_artifact /
   commit_version`。没有新表、没有新 JSON 库、没有第二份 Memory、没有新 `AG0` source_kind。
2. **第一行标记是 B 的唯一定义**：`<!-- nyairo-practice:v1 {JSON} -->`，JSON 里 `kind`
   为 `review` 或 `method`。`parse_practice_content()` / `is_practice_content()` /
   `practice_kind()` / `build_practice_content()` 是公开 helper，A/C 不必复制第二套定义。
   只认第一行；标记之后的正文**逐字原样**返回 —— 元数据不会挤掉正文。
3. **回顾必须引用真实经历**。`create_review` 先经 A 的 `read_experience` 逐条核对；任何一条
   读不回来就 `rejected / PRACTICE_EVIDENCE_UNREADABLE`，一件工件都不建。没有 Memory 时
   允许建一份**明确没有经历基础**的回顾（`experience_basis=none`）：它是计划，不是已证经验。
4. **做法草案天生 TENTATIVE**：具体做法 + 适用条件（缺就标 `scope_missing`）+ 未知
   （默认"暂定，尚未验证"）。状态机只有四档：
   `HELPED → TRIAL`、`NOT_HELPED → CONTESTED`、`UNKNOWN → 不变`、修订 `→ TENTATIVE`、
   退休 `→ RETIRED`。没有 `SUPPORTED`，也没有"试够三次自动升级"。
5. **试用评价要有证据**：`HELPED` / `NOT_HELPED` 必须带一条**经 A 治理仍可读**的经历，否则
   `rejected / PRACTICE_EVIDENCE_REQUIRED`；没有证据只能记 `UNKNOWN`。记录是评价，不改原事实；
   `NOT_HELPED` 的反例保存在标记里，修订之后一条不少。
6. **修订保旧版本、退休不删除**：`revise_method` 只改元数据（做法 / 范围 / 未知）并把状态
   打回 TENTATIVE，正文不动，旧版本留在 owner 的不可变历史里；`retire_method` 只写一个
   `RETIRED` 版本，文档、正文与反例全部保留，也没有任何删除入口。
7. **治理递归传播**：读一份回顾时会逐条重读它引用的经历；读一条做法时会重读经历**和**被它
   引用的回顾（有深度上限与环检测）。只要有一项读不回来，这一份**任意嵌套字段**都不再带受治理的
   内容：`body`、`content`、`data.project.content` 一律为空、`content_available=false`、
   `governance.blocked=true`；回顾的 `question`、做法的 `instruction` / `scope` / `uncertainty`
   也一并置空；`trials` 保留 `outcome` / `version_id` / `evidence_usable`（状态），但 `detail`
   （依赖受治理内容的说明）与 `revised_from` 之类的旧指令不再发出。元数据（title / head /
   revision / method_state）与 `checks` 照实给。
   列表行走**同一道治理**：被挡的行 `governance_blocked=true` 且 `question` / `instruction` /
   `scope` / `uncertainty` 为空，检索用的 `_haystack` 只由标题构成（不会因为受治理的词被搜到）。
   被挡住的文档也不接受新的试用 / 修订（`PRACTICE_EVIDENCE_UNAVAILABLE`）；退休仍可执行。
8. **正文续写只写正文**：`update_document` 用已存的元数据重建第一行，只换正文；调用方若把
   整段内容（含标记）交回来，标记被剥掉（`marker_stripped=true`），不会被写第二遍，也改不了
   元数据。没有 `expected_head` 一律拒绝。
9. **手机是真正能用的工作台**：新增 `GET/POST /api/phone/memory`、`GET .../memory/experiences`、
   `GET .../memory/experiences/<id>`、`POST .../memory/enable`，以及 `GET /api/phone/practice`、
   `GET .../practice/reviews/<id>`、`POST .../practice/reviews`、`GET .../practice/methods`、
   `GET .../practice/methods/<id>`、`POST .../practice/methods`、
   `POST .../{reviews|methods}/<id>/{documents|trials|revisions|retire}`。保留既有全部闸门
  （loopback / Host / Origin / Sec-Fetch-Site / CSRF / 字段白名单 / `CLIENT_IDENTITY_REFUSED`）；
  客户端不能声明 owner、Memory 库路径、workspace、grant、artifact_id、`method_state`。
  界面新增 `practice / practice-review / practice-review-new / practice-review-edit /
   practice-method / practice-method-new / practice-method-revise / practice-experience` 八屏
   + 主屏一个行内入口（**不加 tile，不动 `APP_ORDER` / `HOME_ORDER`**）；草案 / 试过 /
   有反例 / 已退休用四种 chip 分开，证据读不回来时明写"正文已停止透出"。
10. **普通项目屏不能绕过实践标记**：`GET /api/phone/projects` 的行把回顾 / 做法标成
    `artifact_type=review|method` 且 `continuable=false`；从项目屏 commit 会 409
    `PROJECT_IS_A_PRACTICE_DOCUMENT`（否则会改写第一行标记、丢掉引用）。`GET
    /api/phone/projects/<id>` 对这两类也不发正文（`content=""`、
    `content_available=false`、`private_text_included=false`，并给出 `practice_surface`
    指向实践面）—— 正文只有逐条核对证据的实践面才发得出。
11. **Memory 启用只走显式 POST**：`PhoneService.enable_memory` 调用 A 的
    `phone.life.enable_instance_memory(root, owner=…, operation_key=…)`；owner 来自实例配置 /
    环境 / `MEMORY_DEFAULT_OWNER`，请求体里出现 `owner` 直接 409。返回里只报告
    `restart_required`，服务不会重启任何东西。GET / preflight 绝不初始化。

## Alternatives considered

- **把回顾 / 做法写进普通项目，不加标记**。否决：普通项目屏的"修改正文"交上去的是整段文本，
  会覆盖第一行；没有标记就无法区分"这是回顾 / 做法"与"这是她的另一页作品"，也就没法让
  试用 / 修订 / 退休这些动作只作用在正确的那一类上（这正是上一轮研究工件踩过的坑）。
- **给做法加 `SUPPORTED` 或"试够 N 次自动升级"**。否决：阶段单与本轮红线都明确不要"已验证"。
  次数不是证据；`HELPED` 只到 `TRIAL`，`NOT_HELPED` 直接 `CONTESTED` —— 能不能算稳定成立，
  留给后续有证据的判断，而不是一个计数器。
- **把反例写进正文之外的第二份列表（另存一个 JSON / 新表）**。否决：第二 store 是明文禁止的；
  反例写进标记的 `trials` 里，跟着文档的每个版本一起被 owner 保留，修订也抹不掉。
- **经历读不回来时只清空顶层 `body` / `content`、其余字段照发**。否决：父级只读核对实测到
  `data.project.content` 仍带原始文档、`instruction` / `question` / `trials[].detail` / 旧指令
  仍是受治理内容 —— 这等于从派生文档把被撤回 / 删除的内容漏回来（回归测试：
  `GovernanceLeakTests` 与 `PracticeGovernanceLeakApiTest` 用独有 sentinel 扫
  `json.dumps(response)`）。正确做法是**整份腾空所有依赖字段**并保留元数据与核对结果，
  这样"她曾经写过这么一份"仍然可解释，而受治理的正文不再出现。
- **把 Memory 启用做成 GET 或"进屏就自动启用"**。否决：GET 必须零写；建库 / 迁移 / chmod 是
  真实副作用，只在一次显式 POST 上发生，而且绝不替操作者重启服务。
- **手机端解析标记来判断类型**。否决：标记只有一处定义（Python 服务），界面只用服务给的
  `kind` / `method_state` / `checks`；前端再解析一次就是把同一个契约实现两遍。
- **给手机加第九 / 第十一个 app tile**。否决：`APP_ORDER` / `HOME_ORDER` 是既有生活链的排序，
  本轮只加行内入口（与研究 / 项目一致），不动 tile 表。

## Consequences

- 收益：主体第一次能把"一次真实结果"变成一份**可追溯的回顾**，再把回顾里的做法写成一条
  **有范围、有未知、默认未验证**的草案；试用有证据才记有用 / 没用，有反例就明确标出来；
  修订保住旧版本与反例，退休只是状态。手机上八屏能真的走完这条链，治理撤回会在同一屏上
  显示"正文已停止透出"。
- 代价 / 已知限制：
  - 回顾与做法都是**私人文档**，不是 Memory、不是稳定信念、不是可执行 Skill；
    没有任何一处会执行做法代码。
  - 每次读详情都会逐条重读证据（一次 read 一次 owner / Memory 调用），列表按 `MAX_SCAN=200`
    有界扫描，`truncated` 照实报；不是全库索引。
  - `trials` 有界保留（最多 12 条，先丢最旧的非反例条目），更早的评价只在 owner 的版本历史里。
  - 真实 Supply owner + 真实 M0 记忆库的集成测试**只在 POSIX 跑**（AF_UNIX + `fcntl`），
    原生 Windows 上显式 skip；本机 WSL 上已经跑过并通过（见结果文档 §5）。
  - Memory 是否可用完全取决于实例的显式绑定；未绑定时可读状态、可写计划，但不能声称经历。
- 不变量：无新表 / 新库 / 新 Memory；无迁移；无删除（只有归档与逻辑退休）；GET 零写；
  写必须 CSRF + CAS；`private_project_service.py`、`research_workspace_service.py` 与 A/C 的
  文件一字未改；`APP_ORDER` / `HOME_ORDER` 未动。
- 回归（父级只读核对发现的真实漏洞，已修）：治理挡下后，`json.dumps(response)` 里不再有
  回顾 / 做法的正文、问句、指令、范围、未知、试用说明与旧指令的独有 sentinel；
  直接引用、回顾引用传播、列表行、`/api/phone/practice` 与 `/api/phone/projects/<id>` 六条路都覆盖
  （red: 7 例失败 + 项目面 1 例失败 → 修后绿）。
- 给 A / C 的接缝：`PersonalPracticeService(projects, memory_service=None)` 的九个方法、
  `practice_from_env(port, env)`（缺一件 env 返回 `None`）、`parse_practice_content` /
  `is_practice_content` / `practice_kind` / `build_practice_content`；详见
  `docs/engineering/v04-practice-workspace-result.md`。
