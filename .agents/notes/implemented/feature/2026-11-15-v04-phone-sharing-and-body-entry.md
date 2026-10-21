# Agent Note: v0.4 阶段 B · 手机上的分享与身体入口（真正能用，不只静态卡片）

Status: implemented

## Problem

B 已经有 `LifeSharingService`（草稿 / 延后 / 放弃 / 本地投递，固定 `local:owner` + `local_inbox`），
A 也已经把身体与分享接进决定。但**手机**这两块都还是看客：

1. **分享没有手机入口**。`/api/phone/*` 里一条分享路由都没有；主屏只有磁贴与一排行内按钮，
   点不到「我写了一段想给她看的话」。B 的四个方法（`prepare_share` / `read_share` /
   `list_shares` / `apply_action`）在手机侧无人接线。
2. **手机最容易在这里变成第二个真相源**。分享状态是 owner 的 artifact 版本；
   如果手机自己记一份「已发出」，重启后两份数据就会分叉，而且「发进本地收件箱」会被读成
   「用户收到了」。同理，来源一旦允许客户端手填路径/URL/DB，`/etc/passwd` 也能被当成「她的作品」。
3. **来源会变**：草稿形成后她改了那件作品、或 owner 拒绝再读那份来源。手机如果照旧把那段正文
   吐出来，就是把已经收回去的内容从派生件里泄出去。
4. **身体屏只有卡片**：它能显示姿势/信号/恢复，但「坐下」「开始/结束休息」要从世界屏绕过去；
   而且 `session completed` 很容易被读成「她恢复好了」。

## Decision

只在 B 的写集里实施：新增 `components/phone/sharing.py`（P 的分享窄门面），改
`service.py` / `server.py` / `web/app.js` / `web/style.css`，新增三个测试文件与一个 Node 测试、
本文档与结果文档。**未改** A 的 life/world、`life_sharing_service.py`、既有其它 tests、
`MANIFEST.json`、两个 fix 脚本；`index.html` 无需改动。

1. **一个商店都不新开**：`PhoneSharing` 内部只持有 `LifeSharingService(projects)`，
   `projects` 是 `PhoneService` 用**实例自己的** port / `subject` / `workspace_id` /
   `artifact_grant_id` 现构造的 `PrivateProjectService`（不读进程全局 env，不建表，不迁移）。
   版本即状态、CAS、`operation_key` 幂等、UNKNOWN 对账全部是 owner 的，手机只转发。
2. **来源只能是这台实例现在真的读得到的**：`sources()` 用 `list_projects()` 列她的作品，
   阅读材料按自己的 `kind=reading` 单独列出（`private_items` 这份簿记文档不是来源，分享件自己也不是），
   每一行都用 `read_project` 现场问一次 owner 是否可读。`prepare` 先在这张表里核一遍：
   不在表里 → `SHARE_SOURCE_NOT_OFFERED`；读不到 → 具名 `SOURCE_*`；一件 artifact 都不建。
3. **正文投影只有一条规则**：列表 `/shares` 请 owner 用 `include_text=True` 只为拿到
   `text_withheld` / `text_available` / `source_state` / `withheld_count` 这几个**当前性**字段，
   然后把正文从每一行丢掉——列表答案里真的没有正文；详情 `/shares/<id>` 是唯一投影有界正文的地方，
   且只在来源现在仍可读时。来源被拒绝（`GOVERNED`/`NOT_FOUND`/`UNAVAILABLE`）时整份 JSON
   （列表与详情）都不含那段正文，`private_text_included=false`，身份/状态/head/来源绑定/受众/
   渠道/收据/长度照旧保留。
4. **受众与渠道是常量，不是字段**：server 的 `_read_body(..., refused=SHARE_ADDRESSING_FIELDS)`
   把 `audience|channel|recipient|to|email|url|endpoint|webhook|telegram|...` 按名拒成
   409 `SHARE_ADDRESSING_REFUSED:<字段>`（不静默忽略）；`source_artifact_id` 只接受
   `artifact:<hex>`（带 `/ \ ? @ 空白` 一律 400 `SOURCE_ID_INVALID`）。`expire` 不做——它是 owner
   侧的事实（读面推导 + A 明确落盘），手机不替她过期。
5. **`stale_source` 保留 B 的原词**：`http_status_for` 额外映射到 409；前端把它当**确定的拒绝**
   （terminal，键释放），文案说「来源换版/被拒，这次没有发出，要重新准备」，绝不折叠成 `conflict`
   或 `unknown`。CAS 冲突仍是 `conflict`（带 `current_head_version_id`），UNKNOWN 仍 202 且保留原键。
6. **原 key 对账是纯读**：`GET /shares/operations/<key>` 读两样真实的东西——手机自己的
   `phone/operations.jsonl`（`share:prepare` / `share:send_local|defer|drop` + 原 key + 结果），
   以及每件分享的当前状态；它不写、不结算时钟、不换键。前端只有「手机记录了确定答复」才释放键，
   否则原键留在「未确认的操作」里。
7. **界面三屏 + 主屏入口**：`shares`（草稿/延后/本地收件箱计数、来源当前性、扣留提醒）、
   `share`（正文或扣留原因、来源当前性、等待理由、收据、三个动作；终态与已投递没有动作按钮）、
   `share-new`（来源只能从服务列表里选、短文与前缀二选一）。主屏是**行内按钮**，`APP_ORDER`
   与 `HOME_ORDER` 一个字都没动（旧检查按字面量对齐）。每次列表/详情/收据都醒目写
   「本实例本地存档，未向外部发送、未证明用户已读」。
8. **身体屏复用世界的入口**：直接挂上既有的 `worldPosePanel`（站起/坐下/躺下 + 朝向）与既有
   `REST` 开始/结束/睡着式，并补三句话：会话结束不等于恢复、未知不等于休息好了、
   命令被接受不等于身体跟上。没有新增任何强制吃/睡入口，也没有新的身体变量。
9. **窄修（2026-11-15，父级真实浏览器核对暴露）**：身体屏要用世界的 `body` 块与 `actions`
   （POSE/REST 能力），所以「点身体时到底读不读世界」是这条入口的命门。原实现在
   `ensureLoaded()` 里**没有 `body` 分支**（`body` 落到 `SCREEN_RESOURCE` 查表查不到就 `return`），
   全新页面主屏点身体只渲染 `/state` 投影，`GET /api/phone/world` 一次都不发，于是没有坐下/休息按钮；
   而世界快照的判据又是「缓存里有没有条目」，一次失败留下的 `error`/`offline` 条目会让之后
   每次进入都不再请求（永久堵死）。现在：`ensureLoaded('body')` 走一条具名分支，
   判据收进导出的 `worldSnapshotNeedsLoad(entry)`——只有 `phase === 'loading'` 与 `phase === 'ready'`
   不重发，其余（没有条目 / `error` / `offline` / 没有 `phase`）一律重读；
   `renderBody()` 在快照没到时给一个**真的会重发**的「重新读取世界快照」按钮，并照实说「世界快照这次没读到」；
   `go()` 再点同一屏也走 `ensureLoaded()`，所以身体/世界这种「上次没读到」的屏可以就地重试。
   分享面**一行未改**。

## Alternatives considered

* **给分享另建一张手机表 / 一个 `shares.json`（最快对齐 UI 字段）**。最强立论：列表与详情能一次读齐，
  不必对每件分享回问 owner。否决：那就是第二个真相源，CAS、不可变版本、`operation_key` 幂等与
  重启存活全部要重做一遍，而且「手机说已发出、owner 说还是草稿」时没有任何权威能裁决。
* **列表直接带正文（`include_text=True` 原样转发）**。最强立论：一次请求就能在列表里预览草稿，
  UI 更省事。否决：那会把「来源被拒绝之后还能读到正文」这条已经修过的漏洞搬到手机上；现在列表
  只借那次调用拿当前性字段，正文一律丢掉，详情是唯一投影点。
* **让客户端传 `audience` / `channel`（"反正服务会校验"）**。否决：字段一旦可传，就会被当成能力；
  固定常量 + 按名拒绝（409）比「传了但不生效」诚实得多，也和 A 的 AG-1 `validate_sharing_action` 对齐。
* **`stale_source` 折成 `conflict`（少一个状态）**。最强立论：前端状态机只需五档。否决：两者含义完全不同
  ——conflict 是「草稿版本被别人改了」（重读 head 再提交就行），stale_source 是「来源已经不是当时那份」
  （要重新准备一条）。合并会让界面给出错误的下一步动作。
* **UNKNOWN 时让手机自己再发一次（换新键更快）**。否决：那正是双投递的来源。原 key 只读对账 +
  同一 key 重试是 B 已经定死的语义，手机只照做。
* **分享绕开 `/projects`，另开一个隐藏工作区**。否决：B 的分享件本来就是同一 owner 里的 artifact，
  换工作区只会让「她说过什么」劈成两处；代价（分享件会出现在项目列表）照实写进了结果文档。
* **身体屏干脆自己按需预取世界（进了身体屏就无条件再发一次 `/world`）**。最强立论：一次就能
  保证按钮一定出现，不用判断缓存。否决：那就变成每进一次身体屏就重读一次世界，
  把「读到过的事实」当不存在，也让「刷新」失去意义；判据按 `phase` 分档（失败才重试、`loading` 不叠加、
  `ready` 不重复）比无条件重发更诚实，测试也把「读到之后不重复请求」写成了断言。
* **身体屏不动，改在 `/state` 里塞一份世界能力（POSE/REST 的可用位）**。否决：那是让 P 的 `/state`
  复制 A 的 world 能力判定，两处会分叉；世界能力只有 `/world` 的 `actions` 块是权威。

## Consequences

* 手机上第一次能：从**真实可读的来源**写一条有界短文草稿 → 显式延后（写清等什么机会）→
  在明确机会里 `send_local` 进本实例本地收件箱 → 看到带 `delivery_id` 的本地收据 →
  重启后仍然是 `LOCAL_DELIVERED` 且只有一条投递；放弃/过期是终态，没有重发入口。
* 代价与已知限制：
  1. `GET /shares` 会为每件候选分享多问一次 owner 来源（B 的既有代价，`MAX_LIST=100` 有界）；
     `apps.shares` 让 `/state` 多一次 `list_shares`（不花 LLM，但确实是 owner 调用）。
  2. 真实时钟下同一 prepare key 重放，owner 会以 `CAS_CONFLICT`（`operation key reused with
     different request`）具名拒绝（信封含时间戳）；**仍然只有一条草稿**，测试对这种结局也断言。
  3. 分享件在 owner 里就是 artifact，`GET /projects` 仍列出它（未在本轮偷偷排除）。
  4. 没有外发（`channel` 只有 `local_inbox`）；`LOCAL_DELIVERED ≠ 已送达 ≠ 已读`，
     `read_by_audience` 恒 false，界面每次都写这句。
  5. 没有起 `life_supply` socket 的端到端链，也没有真实浏览器手工点过（父级统一核对）。
* 窄修（2026-11-15）之后手机侧的新事实：全新页面主屏点「身体」会**真的**发一次 `GET /api/phone/world`，
  世界读失败（`error`/`offline`）之后从主屏再点身体、或点屏上的「重新读取世界快照」，都会**真的重发**，
  不是被缓存永久堵死；世界读到之后不重复请求。代价与边界：
  1. 世界读不到时每进一次身体屏都会再试一次（有意重试，不是缓存风暴：`loading` 期间不叠加）；
  2. 世界读成功后不自动轮询，要看更新的姿势得点「刷新」；
  3. 实例若本来就没开世界，身体屏会一直照实显示「世界快照这次没读到 + 原因」，界面不编造世界能力；
  4. 本轮验证走的是真实 DOM 路径（最小 DOM 桩跑整份 `app.js`，点渲染出来的「身体」磁贴），
     **没有**起服务、没有真开浏览器、没有调用模型；Edge 里的手工核对（含真正坐下）由父级做。
* 验证：`tests/test_v04_phone_sharing_service.py`（20）、`tests/test_v04_phone_sharing_http.py`（18）、
  `tests/test_v04_phone_sharing_supply.py`（5，WSL 真 sqlite）、`tests/phone_sharing_ui.mjs`（9）、
  窄修 `tests/test_v04_phone_sharing_body_entry.mjs`（6，真实 DOM 路径）；既有 Node 界面套件
  （`test_phone_ui` 34 / `test_phone_world_ui` 25）与 `test_phone_api` / `test_phone_projects_api` 仍全绿。
  精确命令、退码与失败形状见 `docs/engineering/v04-life-pacing-phone-result.md` §3 与 §6.4。
* 门禁脚本**位置纠正**：决策树与格式校验不在本仓库 `scripts/` 下，而在全局 skills 目录
  `C:/Users/28556/.agents/skills/write-notes-like-deepseek/scripts/`。以仓库为 cwd 可以直接跑
  `node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-tree.ts`
  （`ok: 72 note(s) tree and relative links verified`，exit 0）与同目录的 `verify-agent-note-format.ts`
  （`ok: 72 note(s) verified`，exit 0）。本 note 与结果文档 §5.7 已按这一点改正。
