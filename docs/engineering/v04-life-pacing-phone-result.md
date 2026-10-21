# v0.4 阶段 B 结果：手机上的分享与身体入口（真正可用，不只静态卡片）

Status: implemented（B 独占写集内完成并自测；浏览器使用核对与真实模型由父级统一做。
2026-11-15 窄修「主屏直达身体不读 world / 失败后永久堵死」已修好并回归，见 §6）

写集（全部在这几个文件里，未碰 A 的 life/world、`life_sharing_service.py`、其它 tests）：

| 文件 | 性质 |
|---|---|
| `components/phone/sharing.py` | **新增**：P 的分享窄门面（只调 B 的四个文档化方法） |
| `components/phone/service.py` | 新增 `shares/share/share_sources/share_prepare/share_action/share_operation`，`APPS` 加 `shares` |
| `components/phone/server.py` | 新增 5 条路由 + 字段白名单 + query 校验 + `stale_source → 409` |
| `components/phone/web/app.js` | 新增分享纯函数面、三屏与主屏入口；身体屏接上 POSE；`stale_source` 状态 |
| `components/phone/web/style.css` | `.share-text` / `.share-note` / `.share-receipt` |
| `tests/test_v04_phone_sharing_service.py` | 新增：分享门面 20 项（内存 owner 替身） |
| `tests/test_v04_phone_sharing_http.py` | 新增：HTTP 面 18 项（真实 loopback + 真实 `PhoneService`） |
| `tests/test_v04_phone_sharing_supply.py` | 新增：真实 Supply `ManagedArtifactStore` 短链 5 项（POSIX/WSL） |
| `tests/phone_sharing_ui.mjs` | 新增：界面数据层 9 项（`node --test`） |
| `tests/test_v04_phone_sharing_body_entry.mjs` | **窄修新增**：主屏直达身体的真实 DOM 路径回归 6 项（见 §6.3） |

`index.html` 未改（三屏都是动态渲染，静态文件白名单与 CSP 保持原样）。

---

## 1. 产品 API（给父级与原作者的精确契约）

所有回答都是同一个信封 `{ok, status, data, reason_code, detail}`；HTTP 码沿用既有映射
（`applied|already_applied`=200、`rejected`=409、`conflict`=409、`stale_source`=409、
`unknown`=202、`not_found`=404、`unavailable`=503、请求本身不成立=400、超大=413）。

| 方法与路径 | 作用 | 纯读？ |
|---|---|---|
| `GET /api/phone/shares?states=DRAFT,DEFERRED&limit=N` | 草稿 / 延后 / 本地收件箱的**列表**；每行带 `state`、`source_state`、`source_reason_code`、`text_withheld`、`text_available`、`has_receipt`、`delivered`、`sendable`、`actions`；**永远不带正文** | 是 |
| `GET /api/phone/shares/<share_id>` | 一条分享：状态、来源当前性、等待理由、收据、以及**来源仍可读时**的有界正文 | 是 |
| `GET /api/phone/shares/sources` | 这台实例现在**真的读得到**的可选来源（作品 + 绑定的阅读材料），带 `readable` / `reason_code` | 是 |
| `POST /api/phone/shares` | 显式短文 `prepare`：`{source_artifact_id, share_text 或 excerpt_chars, title?, expires_after_s?, operation_key}` | 否（CSRF/CAS/幂等） |
| `POST /api/phone/shares/<share_id>/actions` | `{action: send_local|defer|drop, expected_head, operation_key, defer_condition?/opportunity_ref?/reason?}` | 否 |
| `GET /api/phone/shares/operations/<key>` | **原 key** 的只读对账：手机 `operations.jsonl` 里这条键的记录 + 每件分享的当前状态 | 是 |
| `GET /api/phone/state` | `apps.shares = {status, data:{counts, inbox, withheld_count, local_only, note}}`（**不含正文**） | 是 |

绑定与来源规则（不是客户端能改的）：

* 服务由 **`PhoneService` 实例自己的** port / `subject` / `workspace_id` / `artifact_grant_id`
  构造 `PrivateProjectService` → `LifeSharingService`；**不读进程全局 env**，**不建新 store**
  （`describe()` 明写 `owns_no_store=True`、`external_send=False`）。
* `audience`/`channel` 是 P 自己的常量 `local:owner` / `local_inbox`；请求里出现
  `audience|channel|recipient|to|email|url|uri|endpoint|webhook|telegram|chat_id|username|account|
  address|transport|provider|smtp|sms|notify|send_to|external` 任一 → **409
  `SHARE_ADDRESSING_REFUSED:<字段>`**（按名拒绝，不静默忽略）。
* `source_artifact_id` 只接受 owner 的 `artifact:<hex>`（或裸 owner id）；带 `/ \ ? @ 空白`
  的一律 400 `SOURCE_ID_INVALID`；即使形状对、但不在本实例现在的来源列表里 → 409
  `SHARE_SOURCE_NOT_OFFERED`；来源读不到 → 409 具名 `SOURCE_*`。**任意路径 / DB / URL 都不可能成为来源。**
* 必须显式 `share_text`（≤2000）**或** `excerpt_chars`（1..400）之一；两者都给 → 400
  `SHARE_TEXT_EXCLUSIVE`，都不给 → 400 `SHARE_TEXT_REQUIRED`（绝不默认把整篇私人材料当分享文本）。
* `share_text`+`excerpt_chars` 之外，`expires_after_s` 只在 60..2592000 之间；标题 ≤80。
* CAS 与幂等全部由 owner 判：陈旧 `expected_head` → 409 `status=conflict` +
  `data.current_head_version_id`；同一 `operation_key` 重放由 owner 回原结果 / 具名 `CAS_CONFLICT`，
  **两种情况都不会有第二条草稿**；已投递再发 → 409 `SHARE_ALREADY_DELIVERED`（附同一条 receipt）；
  `DROPPED`/`EXPIRED` → 409 `SHARE_TERMINAL_STATE`（终态不重发）。
* 来源换版 / 归档 / 读被拒绝 → **409 `status=stale_source`**（`SOURCE_REVISION_CHANGED` /
  `SOURCE_ARCHIVED` / `SOURCE_GOVERNED`），一行都不写；**不当成 conflict，也不当成「被删了」**。
* 正文投影只有一条规则：来源**现在**仍可读时才给正文；`GOVERNED`/`NOT_FOUND`/`UNAVAILABLE`
  时整份 JSON（列表与详情）都不含那段正文，`text_withheld=true`、`private_text_included=false`，
  元数据（id/state/head/来源绑定/受众/渠道/收据/长度）照旧保留。
* 每一次读/写/收据都带 `note = "本实例本地存档，未向外部发送、未证明用户已读"` 与
  `local_only=true`、`external_send=false`；收据 `read_by_audience=false`、`external_channel_sent=false`。

## 2. 手机界面（真的能操作，不是静态卡片）

* **主屏**：`分享与收件箱` 行内入口（`APP_ORDER`/`HOME_ORDER` 一个字都没动，仍只有七个 + world）。
* **分享列表**：草稿 / 已延后 / 本地收件箱 / 已放弃 / 已过期计数、收件箱条数、醒目的本地存档声明、
  被扣留条数提醒；每行状态、来源当前性（仍可读 / 换版 / 归档 / 拒读 / 不存在）、正文长度、更新时间。
* **分享详情**：有界正文（或明确的「正文已扣留 + 原因」）、来源绑定与来源当前性、等待理由
  （`condition` / `opportunity_ref`）、放弃/过期理由、本地收据（投递标识/时间/渠道/受众/外发/已读）、
  以及三个动作。终态与已投递**没有**动作按钮。
* **写一条分享**：来源只能从服务给的列表里选（读不到的单独列出来并说明为什么不能选），
  短文与前缀长度二选一，标题与过期窗口可选；草稿形成后直接进详情。
* **失败 / CAS / UNKNOWN 都可操作**：动作各自一个 scope（`share:<action>:<share_id>`，同一个动作永远
  同一个键）；`conflict` 显示当前 head 并保留草稿；`stale_source` 显示来源换版并提示重新准备；
  `unknown` 留在「未确认的操作」里，用 **原 key** 走 `GET /shares/operations/<key>`（只读）对账，
  只有手机真的记录了确定答复才释放键，否则保留原键、不换键重发。
* **身体屏**：接上世界既有的 `POSE` 面板（站起/坐下/躺下 + 朝向）与既有的 `REST` 开始/结束/睡着式，
  显示会话状态、方式、档位、趋势、休息条件、信号来源；并明确写「会话结束了不等于恢复」
  「未知不等于休息好了」「命令被接受不等于身体跟上」。没有任何强制的吃/睡入口。
* 文本一律 `textContent`；不写 `localStorage`；业务数据不进 sessionStorage（只有未决键与导航）。

## 3. 怎么跑（精确命令与结果）

Windows（本次实际执行）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
cd D:\nyairo-rc1-body-backend
python -c "import ast; ast.parse(open('components/phone/sharing.py',encoding='utf-8').read())"   # AST OK
python -m unittest tests.test_v04_phone_sharing_service tests.test_v04_phone_sharing_http
#   Ran 38 tests … OK   （exit 0）
python -m unittest tests.test_v04_phone_sharing_supply
#   Ran 5 tests … OK (skipped=5)   （POSIX-only，照实跳过，exit 0）
node --check components/phone/web/app.js                                                        # exit 0
node --test tests/phone_sharing_ui.mjs                                                          # pass 9 / fail 0
```

WSL Ubuntu（真实 Supply `ManagedArtifactStore`，真 sqlite）：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 NYAIRO_SHARE_FIXTURE_ROOT=<scratch>/nyairo-phone-share-b \
  python3 -m unittest tests.test_v04_phone_sharing_supply -v
#   Ran 5 tests … OK   (≈16s)
python3 -m unittest tests.test_phone_projects_api      # Ran 7 tests … OK（share 面没碰项目面）
python3 -m unittest tests.test_phone_api               # Ran 13 tests … OK（含 /state apps == APPS）
```

既有 Node 界面测试（未改动，作为回归）：`test_phone_ui` 34 / `test_phone_world_ui` 25 /
`test_phone_projects_ui` 6 / `test_phone_practice_ui` 15 / `test_phone_reading_ui` 25 /
`test_phone_research_ui` 9 / `test_room_living_ui` 21 —— 全绿。

### 3.1 隔离 phone fixture（父级做浏览器核对时用这个）

```powershell
# 1) 一个全新的绝对路径根（已存在会拒绝，不会覆盖任何旧实例）
python scripts/phone.py prepare-demo --root D:\scratch\phone-v04b-<时间戳> --with-world
# 2) 自己起、自己停两个 owner 子进程；只绑 127.0.0.1；端口在第一行 JSON
python scripts/phone.py serve --instance D:\scratch\phone-v04b-<时间戳> --port 0 --tick-seconds 5 --with-room-living
# 3) 浏览器打开 http://127.0.0.1:<port>/ ；主屏 → 分享与收件箱 / 世界 → 身体
```

该实例的 `instance.json` 里已经有 `subject` / `workspace_id` / `artifact_grant_id` /
`private_items_artifact_id` / `reading_artifact_id`，分享面就是按这些绑定构造的（不读全局 env）。
没有被删除的夹具：sqlite 与 instance 根全部保留，重复运行用新时间戳。

## 4. 测了什么（对应到具体用例）

`tests/test_v04_phone_sharing_service.py`（20 项，`FakePort` 内存 owner）：

`describe` 无 store/无外发/固定受众渠道；来源只列 owner 真读得到的（作品 + 阅读绑定，分享件自己不是来源）；
显式短文与显式前缀两种草稿；缺文本 `SHARE_TEXT_REQUIRED` 且零写；外来来源 `SHARE_SOURCE_NOT_OFFERED` 且零写；
列表无 `text` 键、计数/inbox/本地存档声明；同一 key 只有一份草稿；`defer` 无依据被拒、有 `opportunity_ref`
成功且反复读不写新版本；陈旧 head → `conflict` + 当前 head、草稿仍在；`send_local` 的 receipt 六条诚实字段、
`delivery_count=1`、再发 `SHARE_ALREADY_DELIVERED` 且 receipt 不变；`drop` 终态 + 再发 `SHARE_TERMINAL_STATE`
且 head 不变；来源换版 → `stale_source` + `SOURCE_REVISION_CHANGED` 且零写、正文仍投影；UNKNOWN 保留原 key
且同 key 重试只产生一条投递；来源读被拒绝时详情/列表/发送/准备**整份 JSON** 无 sentinel；未知分享 id `not_found`；
`expire` 不被接受；owner 坏掉 → `unavailable` + `SHARE_PORT_ERROR`（不是空收件箱）。

`tests/test_v04_phone_sharing_http.py`（18 项，真实 loopback + 真实 `PhoneService`）：
`/state` 带 `shares` app 且不含正文；列表/来源/详情是纯读（owner 零写）；未知 id 404；query 白名单
（`UNKNOWN_QUERY` / `SHARE_STATE_INVALID` / `LIMIT_INVALID`）；无 CSRF 403；缺短文 400；同时给两样 400；
`audience/channel/recipient/url/telegram` → 409 `SHARE_ADDRESSING_REFUSED`；`artifact_id` 等身份字段 409
`CLIENT_IDENTITY_REFUSED`；`/etc/passwd`、`https://…`、`sqlite:…`、`file:…`、邮箱 → 400 `SOURCE_ID_INVALID`；
外来 artifact id → 409 `SHARE_SOURCE_NOT_OFFERED`；prepare 写进 `operations.jsonl`（actor/key/ok）；
defer→send_local 全链 + 再发 409 `SHARE_ALREADY_DELIVERED`；陈旧 head 409 `conflict`；来源换版 409
`stale_source` 且版本数不变、列表行 `source_state=REVISION_CHANGED`；`drop` 终态；UNKNOWN 202 后
`GET /shares/operations/<原key>` 只读对账（记录 unknown、键不变、owner 零写）；Host/Origin/Sec-Fetch 三闸门。

`tests/test_v04_phone_sharing_supply.py`（5 项，真实 sqlite owner，WSL）：
`sources` 里来源真的可读；`prepare→defer→send_local` 版本数 1→2→3、receipt 齐全；**真重开**（新 store +
新 projects + 新门面）后仍 `LOCAL_DELIVERED`、receipt 相同、`inbox.delivered_count=1`、版本数不变；
同一 prepare key 不产生第二条草稿；第二次 send 被拒且版本数不变；来源换版 → `stale_source` 且零写，
重新准备后能发（两条分享在 owner 里各自一件）；来源归档 → `stale_source` + `SOURCE_ARCHIVED`，草稿与正文仍在。

`tests/phone_sharing_ui.mjs`（9 项）：列表视图无 `text` 字段、计数/inbox/扣留数；详情视图的正文/来源当前性/
收据/终态；来源视图的 kind 与 `readable`；草稿校验（缺文本、二选一、超长、坏前缀、缺来源）；两个请求体的
精确字段（**永不出现 audience/channel**）；scope 与路径（一个动作一个键、怪 id 不拼路径、手机不做 `expire`）；
原 key 对账的人话；主屏入口与 `.share-text`/`.share-note` 样式存在，且 `APP_ORDER`/`HOME_ORDER` 未变、
没有 `localStorage.*`、没有 `innerHTML`。

## 5. 未完成 / 不能宣称

1. **没有真实外发**：`channel` 只有 `local_inbox`，`LOCAL_DELIVERED` 只是本实例本地存档；
   `read_by_audience=false`、`external_channel_sent=false`。界面每次都说这件事。
2. **没有起 `life_supply` socket 的端到端链**：真实 owner 用例直接构造 `ManagedArtifactStore`（真 sqlite、
   真 CAS）；去 socket 与 Governance 那两段由 Supply 自己的套件与父级真实生活链覆盖。
   HTTP 用例用内存 owner + 空占位 socket 文件。
3. **没有在真实浏览器里手工点过**（父级负责最终浏览器核对）。本轮只有 Node 纯函数面测试与真实 HTTP 用例。
4. **没有真实模型调用**（父级统一控制）。
5. 已知限制：真实时钟下用同一 prepare key 重放，owner 会以 `CAS_CONFLICT`（`operation key reused with
   different request`）具名拒绝——信封带时间戳，逐字节不同；**仍然只有一条草稿**，测试对这一种结局也做了断言。
6. 分享件在 owner 里就是 artifact，`GET /projects` 仍会列出它（P 的 projects 面照旧标 `artifact_type`）；
   本轮没有把它从项目列表里排除（那是「分享件 vs 作品」的产品决定，不在本写集里偷偷改）。
7. 门禁脚本**位置纠正**（原写「不在树里」是错的）：决策树与格式校验脚本不在本仓库 `scripts/` 下，
   而在全局 skills 目录 `C:/Users/28556/.agents/skills/write-notes-like-deepseek/scripts/`
   （`verify-agent-note-tree.ts` / `verify-agent-note-format.ts`）。以仓库为 cwd 可以跑：

   ```powershell
   cd D:\nyairo-rc1-body-backend
   node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-tree.ts
   #   ok: 72 note(s) tree and relative links verified           exit 0
   node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-format.ts
   #   ok: 72 note(s) verified                                    exit 0
   ```

---

## 6. 窄修（2026-11-15）：主屏直达身体时不读 world，导致没有 POSE/REST 按钮

### 6.1 现象与根因

父级在 `/tmp/day-life-parent-p-934242` 用真实 Supply / World socket + Edge 浏览器核对时遇到：
**全新打开的手机页面**，主屏直接点「身体」只渲染 `/api/phone/state` 的 body 投影，
`GET /api/phone/world` **一次都没有发出**，因此身体屏没有姿势（站起/坐下/躺下）与休息
（开始/结束/睡着式）按钮；而且 `/world` 只要读过一次失败，缓存里留着 `error`/`offline` 条目，
之后从主屏再点身体也**永远不会重发**（永久堵死）。

根因在 `components/phone/web/app.js` 的导航读取判断（`components/phone/` 在本仓库是**未跟踪文件**、
没有提交基线，所以「修前形态」是按父级描述复原的，并用 §6.4 的变异检查验证了删掉这两处就会复现）：

1. `ensureLoaded()` 里**没有 `body` 分支**，`body` 只落到 `SCREEN_RESOURCE` 查表（表里没有 `body`）后直接 `return`，
   所以点身体时从不请求 `/world`；
2. 世界快照的「要不要读」判据用的是「缓存里有没有条目」（`!entry`），
   一次失败留下的 `{phase:'error'}` 条目会让后面的进入都不再请求。

### 6.2 改了什么（`components/phone/web/app.js`）

| 位置 | 改动 |
|---|---|
| `ensureLoaded()` 的 `body` 分支（约 4786–4792 行） | 进身体屏先看世界快照是否可重试，需要就 `loadResource('world')`；**不再依赖先去世界屏绕一圈** |
| `worldSnapshotNeedsLoad(entry)`（约 7464–7473 行，已导出给纯函数面） | 只有 `phase==='loading'` 与 `phase==='ready'` 不重发；没有条目 / `error` / `offline` / 没有 `phase` 一律要读 |
| `renderBody()` 的兜底（约 8662–8675 行） | 快照没到时不假装有世界：`loading` 说明在等什么，`error/offline` 给出**真的会重发**的「重新读取世界快照」按钮 |
| `go()` 同屏再点（约 4669–4676 行） | 再点同一屏也走一遍 `ensureLoaded()`，身体/世界这种「上次没读到」的屏可以就地重试 |

写集只碰 `components/phone/**`（主要是 `app.js`）与本文件、本轮的 note 与新增测试；
**没有**改 `components/life/**`、`components/world/**`、既有其它 tests、`life_sharing_service.py`。

### 6.3 回归：真实 DOM 路径，不是纯函数

新增 `tests/test_v04_phone_sharing_body_entry.mjs`（6 项，`node --test`）：用最小 DOM 桩把整份
`app.js` 跑起来（它会自己 `boot()`、自己发 `GET /state`），`fetch` 换成记录器，
然后**从渲染出来的 DOM 树里找主屏的「身体」磁贴并真的点它的 click 监听**，只看真实发出的请求与真实子节点：

1. 全新页面 → 只发 `GET /state`；点身体 → **真的发一次 `GET /world`**，屏上标「来自世界快照」，
   `姿势` 面板在 DOM 里，「坐着」可点、「开始休息」可点、「躺下睡一会」「结束休息」都在；
   并且**world 读到之后再进身体屏不会重复请求**（重试只针对失败）。
2. `/world` 第一次回 503（`error` 条目）→ 没有 POSE/REST 按钮、照实说「世界快照这次没读到」；
   退回主屏再点身体 → **第二次 `GET /world` 真的发出**，成功后按钮出现。
3. `/world` 第一次连不上（`offline` 条目）→ 屏上的「重新读取世界快照」按钮点下去**真的重发**。
4. `/state` 没报告 `world` 这个 app（老实例）→ 仍然读一次 `/world`，由服务自己回答。
5. 浏览器直达 / 刷新后 nav 恢复在身体屏（sessionStorage `nyairo.phone.nav.v1`）→ 直接读 `/world` 且只读一次。
6. 纯函数面：`worldSnapshotNeedsLoad` 对 `undefined/null/error/offline/{}/loading/ready` 的判据。

### 6.4 实际命令与真实退码

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
cd D:\nyairo-rc1-body-backend
node --check components/phone/web/app.js                                            # exit 0
node --test tests/test_v04_phone_sharing_body_entry.mjs                             # 6 pass / 0 fail, exit 0
node --test tests/phone_sharing_ui.mjs        # 9 pass  / 0 fail, exit 0（既有分享界面面未回归）
node --test tests/test_phone_world_ui.mjs     # 25 pass / 0 fail, exit 0（世界/身体界面未回归）
node --test tests/test_phone_ui.mjs           # 34 pass / 0 fail, exit 0
```

**变异检查**（证明这 6 项真的咬住这两个缺陷，不是写死绿灯）：把 `app.js` 复制到会话 scratch，
分别 (a) 删掉 `ensureLoaded()` 的 `body` 分支 → 上表第 1–5 项全红（5 fail），
(b) 把判据退回 `!entry` → 第 2 项与第 6 项红（2 fail）。仓库里的 `app.js` 未被变异（scratch 副本清零）。

### 6.5 仍然不能宣称 / 已知代价

1. 本轮**没有起服务、没有开浏览器**：DOM 桩覆盖「事件 → 请求 → 渲染」这条真实路径，
   但真实 Edge 里的手工核对（含真正坐下）仍由父级在原 phone 实例上做。
2. 世界读不到时，**每进一次身体屏都会再试一次**（这是有意的重试语义，不是缓存）；实例若根本没开世界，
   身体屏会一直显示「世界快照这次没读到 + 原因」，并在 `world` app 被禁用的实例上照旧不给按钮——
   界面不会编造世界能力。
3. `world` 读成功后再进身体屏不重读（只用缓存快照）——要看更新的姿势就点身体屏的「刷新」或世界屏的「刷新」，
   本轮没有加自动轮询。
4. 分享面本轮**一行未改**（同 prepare key 时间戳导致 `CAS_CONFLICT` 的既有局限照旧，见 §5.5）；
   没有开启外发，没有开放 `sleep_like` 之外的新能力（`sleep_like` 也仍然只在世界既有 REST 面板里）。
