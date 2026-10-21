# v0.4 虚拟手机前端（U 写集）交付说明

写集：`components/phone/web/{index.html,app.js,style.css}`（新增），`tests/test_phone_ui.mjs`（新增），本说明。
未改 `server/service/World/Life`、其他 worker 文件、全局配置、`manifest/release`。没有 commit / stage / push。

## 1. 现在是什么样

一部开通即用的中文手机，不是示意图：

- 桌面居中手机外框（`--bezel` 深棕、44px 圆角），右侧是可读的实例侧栏：名称 / 主体 / 模式 / 接口 / 可写状态、连接详情、未确认操作、最近 12 条真实请求记录。
- ≤900px 时侧栏隐藏，手机占满 `100dvh`（含 safe-area），即"窄屏全屏"。
- 状态栏只有真实时钟（`Intl`-free 手写 `HH:MM`，每 10 秒对齐一次，另有 `datetime` 属性）和一个连接点；**没有假电池、假信号、假通知**。
- 主屏 7 个真实 app：笔记、收藏、阅读、钱包、商店、物品、身体。每个磁贴的状态只来自 `GET /api/phone/state` 的 `apps[key].status`；`available` 之外显示"不可用 / 已禁用 / 未报告"，点进去也是可读原因页，不是死图标。
- 底部 dock 两个按钮：主屏、返回（栈式返回；空栈返回主屏）。用 `Escape` 关掉确认弹层。

每屏能做的真实动作：

| 屏 | 读 | 写 / 交互 |
|---|---|---|
| 笔记 | 列表、`head`、条目 id、修订号、更新时间 | 新建 / 修订（标题 80 字、正文 4000 字上限）；修订带 `note_id` + `expected_head` |
| 收藏 | 标签、`source_id @ source_revision`、已读、内容是否取得 | 添加指针（只有三个字段，不抓取外网，不渲染外链） |
| 阅读 | 真实取得材料与阅读位置，理解状态不推断 | 只读 |
| 钱包 | `currency` + `balance` + 流水（金额 / 说明 / 时间 / 交易号） | 只读 |
| 商店 | 目录卡片、详情、价格、可用用途、`purchase_available` / `blocked_reason` | 详情页"确认购买"→ 弹层二次确认 → `POST /purchases` |
| 物品 | 归属、位置、配送、`usable`、`blocked_reason` | "使用"→ 弹层确认 → `POST /items/use`，显示回执字段 |
| 身体 | `apps.body.data` 的只读字段 | 无写入口（没有调试开关） |

所有数字（余额、价格、交易额）只来自响应；缺字段时显示"未提供 / 币种或余额未提供 / 价格未提供"，界面不补 0、不猜单位、不换算。

## 2. 浏览器接线（给 P 和验收用）

前端假设**同源**：

- 静态：`components/phone/web/`（index.html / app.js / style.css）
- 接口：`/api/phone/...`

也就是 P 的服务把 web 目录挂在 `/`（或任意路径）并在同一 origin 暴露 `/api/phone/*`，前端不需要配置。`index.html` 自带 CSP：`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'none'`，所以不要从 CDN 注水，也不要改端口跨域。

只做视觉/结构预览时（没有后端，界面会如实显示"读取失败 · 服务状态不可用"，这是预期）：

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory components\phone\web
# 浏览器打开 http://127.0.0.1:8765/index.html
```

## 3. 接口接线细节与疑点（需要 P 确认）

1. **CSRF 令牌位置**：施工单的 `GET /api/phone/state` 响应里没写 `csrf_token` 字段，但要求 mutation 带 `X-CSRF-Token`。前端按以下顺序读取，取到第一个合法字符串：`state.csrf_token` → `state.csrfToken` → `state.data.csrf_token` → `state.instance.csrf_token`。
   - 一个都取不到 → 整机只读（`canWrite()` 返回 `CSRF_MISSING`，提示"服务没有提供 CSRF 令牌，界面不会绕过"），所有写入按钮被本地闸门拦下，不会发无令牌请求。
   - 已核对 `components/phone/server.py`（P 写集，只读）：`GET /state` 在**顶层**返回 `csrf_token`，与上面的首选顺序一致；若 P 以后改名，只需改 `csrfFromState()` 一处。
2. **`/state` 的 `read_only`**：`true` → 只读；缺失按 `false` 处理，但因为还要求 CSRF，所以缺 token 时同样只读，不会误开写。
3. **笔记修订带条目 id**：实际 payload 为 `{note_id?, title, body, operation_key, expected_head?}`。新建不传 `note_id`；修订传 `note_id = editor.noteId`（点开列表项时记下的 `id`/`note_id` 原值），并在有 `head` 时带 `expected_head` 做真实 CAS——标题与正文照旧提交原文，**不按标题猜是哪一条**。若处于修订态却拿不到条目 id，界面直接挡住（不发 POST，提示回列表重新打开），避免服务把修订当成新建。`GET /notes` 无 `head` → 明确显示"服务没有提供 head：这次写入没有冲突保护"。
   - 同处已核对 P 的 `NOTES_FIELDS = {title, body, operation_key, expected_head, note_id}`，`write_note(..., note_id=...)` 接收字符串 id；本前端修订 payload 与之一致，新建不发该字段。
4. **`GET /api/phone/operations/<key>` 的状态取值**：前端接受 `applied / already_applied / rejected / unknown / conflict`，并把 `status: 'not_found'` 当成"服务没有这个操作键的记录"。其它形状不会猜，显示"服务返回了界面不认识的查询状态"。
5. **字段名按施工单为准**；个别列表项只给了 `{ok, items:[...]}`（阅读、收藏），所以：
   - 收藏读 `label / source_id / source_revision / read / content_fetched`；
   - 阅读主标题依次尝试 `label / title / name / source_id`，位置尝试 `position / progress / page / offset / locator`，其余标量字段以"字段名：值"小标签显示（自动剔除 token/secret/path/home 一类键名和值）。
   - 钱包流水金额尝试 `amount / delta / value`，说明 `description / memo / note / reason_text / label / kind`，时间 `created_at / at / time / updated_at / timestamp`，交易号 `transaction_id / id / entry_id`。**如果 P 用别的字段名，UI 会显示"金额未提供"而不是错的数**——请按此复核或告知我。
6. **商店不可购买时**：仍然展示 `catalog` 与 `blocked_reason` / `reason_code`，但用 warn 色（不是绿色成功块），"确认购买"禁用并写清原因。`purchase_available !== true`、价格缺失、无 CSRF 任一成立都不会下单。
7. **`OWNED ≠ 送达 ≠ 可用`**：物品页同时显示 `ownership / location / delivery_status / usable / blocked_reason`；`usable` 必须是布尔 `true` 才亮"可以使用"，字符串 `"yes"` 一律当不可用。

## 4. 状态语义与幂等

| 情形 | 界面 |
|---|---|
| 读取中 | 虚线"正在读取…"面板，不用骨架屏假装有数据 |
| 空 | "还没有笔记 / 收藏 / 流水 / 物品"，并给出下一步 |
| offline / error | "未连接"或"服务状态不可用"+ HTTP 码 + 重试；不显示示例数据 |
| pending | "提交中…"，按钮禁用，操作键可见 |
| applied | 绿色"已生效"+ 服务返回字段（版本、交易号、物件实例、当前余额、回执） |
| already_applied | 绿色"早已生效：这个操作键已经生效过，服务没有重复执行" |
| rejected | 红色"已拒绝"+ 原因码中文（未知原因码原文显示） |
| conflict | 黄色"版本冲突，这次没有写入，草稿还在"，草稿不丢，可重新读取列表 |
| unknown | 黄色"结果未知…不要重复点击"，保留操作键，附"查询这次操作" |

幂等实现：

- 每个意图一个 scope（`note:create`、`note:write`、`reference:add:<source>@<rev>`、`purchase:<catalog_item_id>`、`use:<item_instance_id>`）；同一 scope 复用同一个 UUID（`crypto.randomUUID()`，无 crypto 时退化为 `op-<时间>-<随机>`）。
- 终态（applied / already_applied / rejected / conflict / blocked）释放键；**未知（网络断、超时、5xx、无法解析、2xx 但没有 status）保留键并进入"未确认的操作"**，不自动换键重发。
- 查询走 `GET /operations/<key>`（只读），拿到终态才释放。
- `2xx` 但没有 `status` 字段**绝不当成功**。

存储边界：**没有 localStorage**。`sessionStorage` 只有三个键，且只存操作键与导航：

- `nyairo.phone.pending.v1`：`{ "<scope>": {key, at, path} }`
- `nyairo.phone.unresolved.v1`：`[{scope, key, path, at}]`
- `nyairo.phone.nav.v1`：`{screen, detail}`

业务数据（余额、笔记正文、价格、流水）一律不缓存：刷新/重启后重新向服务读。

安全：全部 DOM 文本走 `textContent` / `createTextNode`，`app.js` 里没有 `innerHTML / outerHTML / insertAdjacentHTML / document.write / eval / new Function`；后端字符串先经 `sanitizeLine/sanitizeText`（去控制字符、遮 `C:\Users\…`、`/home/…`、`Bearer …`、40+ hex）再显示；只读字段渲染会跳过 `token/secret/csrf/password/cookie/session/home/profile/path` 一类键；表单都有 `maxlength` 与前置校验；侧栏只显示接口路径 `/api/phone`，不显示本机路径。

**协议标识与展示标签是两条通道**（返修要点）：`item_instance_id / catalog_item_id / note_id / expected_head / operation_key` 是服务里的真实键，`protocolId()` 只校验类型、长度（id ≤200、head ≤256、operation_key ≤128，与后端 `MAX_OPERATION_KEY=128` 对齐）和控制字符，通过后把**原字节**交给网络 payload、`findShopItem()` 查找、`ops` 的 scope 与导航 `detail`；**不 trim、不截断、不脱敏**。屏幕上另用 `idLabel / catalogLabel / safeLabel()`（内部就是 `sanitizeLine`）以 `textContent` 写入，展示标签永不回流成业务 id。校验不过时 `buildUsePayload()` / `buildPurchasePayload()` 返回 `ok:false`，界面写"标识不可用、没有发出请求"，不会"改一改再提交"。

## 5. 验证（只跑本次新增，不做整套回归）

```powershell
node --check components/phone/web/app.js
node --test tests/test_phone_ui.mjs
```

`tests/test_phone_ui.mjs`：34 条 node 检查全部通过（`# pass 34 / # fail 0`）。覆盖：静态资源与 CSP、无内联脚本/事件、无 `innerHTML` 类调用、无 localStorage + sessionStorage 键白名单、操作键复用与未知保留、损坏会话数据自愈、导航只存屏幕名、CSRF 闸门（无 token 的 POST 直接抛 `CSRF_MISSING`，GET 不带头）、state→只读判定、缺失 app 标"未报告"、mutation 结果绝不默认成功、原因码可读、金额不造假、路径/凭据遮罩、只读字段剔除敏感键、笔记/收藏输入上限、**修订 payload 带 `note_id` 且新建不带（同标题同 `head` 的两则备忘录按 id 区分，不靠标题猜）**、商店不可购买保持阻塞、`usable` 严格布尔、`head` 缺失说明、真实时钟格式、JS 引用的 DOM id 都存在、窄屏断点与减少动效、无伪数据/无本机绝对路径。

返修新增的 7 条是真实 schema ID 负控（不再只测短示例 id）：`item-` + 64 hex 的库存 view id 与 `POST /items/use` payload 必须与服务原字节完全一致、展示另用脱敏标签；120 字的 `catalog_id` 在购买 payload 与导航 `detail` 里都不被截断；`artifact-version:` + 32 hex 的 head 与 `note:` 开头的 note_id 原样进 payload；控制字符 / 超长 / 非字符串标识一律拒绝且**不发请求**；两个 64hex 物件的 scope 与 operation_key 不会因为脱敏而撞在一起；读取信封里的 `applied / already_applied` 算成功读取而 POST 的 unknown 语义不变；再加一条源码级检查确认协议标识没有经过 `sanitizeLine()`。

浏览器冒烟（scratch 里的一次性 harness，stub 只活在 scratch，不进仓库，不是产品 mock）：headless Edge `--dump-dom`，用 `python -m http.server` 起静态页并注入固定响应，结果：

- 正常链：`is-online`、7 个磁贴、7 屏全部渲染；笔记保存 → `已生效`（版本 3 / 更新时间 / 操作键）；收藏提交 → `已生效`；钱包 `42.50 CNY`；商店详情显示价格与余额 → 弹层"确认购买 / 小台灯 / 价格：18.00 CNY / 当前余额：42.50 CNY" → 购买 `已生效`（交易号 t-1、物件实例 i3、当前余额 24.50 CNY）；物品"使用" → `已生效` + 回执 `灯亮了`；`window.onerror` 为空。
- 异常链：购买返回 500 → `结果未知` + 侧栏"未确认的操作"；等 900ms 后 `POST /purchases` 仍只有 1 次（不自动重发）；点"查询这次操作"→ 同一个键查回 `已生效`，未确认列表清空，POST 次数仍是 1；再次购买返回 402 → `已拒绝 / 余额不足，未扣款`，结果卡不是绿色；`onerror` 为空。
- 无服务：以普通静态服务器打开 `index.html`，`/api/phone/state` 返回 404 HTML → 主屏显示"读取失败 / 服务状态不可用 / 响应不是 JSON（HTTP 404）"、7 个磁贴全部"未报告"、侧栏"服务状态异常"，没有任何示例数据。
- 布局量测（同一 harness，headless 视口 1414×760 与 492×704）：桌面手机 392×668 居中、侧栏 336px 在右侧、4 列磁贴、`screen-body` 横向溢出 0、dock 在屏内、购买弹层在手机框内且内容可见；窄屏手机铺满 492×704（x/y 均为 0）、侧栏隐藏、仍 4 列、横向溢出 0。

冒烟日志与导出 DOM 留在会话 scratch（`phone-smoke/`、`smoke-out3.html`、`smoke-unknown-out.html`、`offline-out.html`），未做物理删除。

## 6. 明确没做 / 未实现

- 没有真实模型、联网、外发、真实支付/充值；没有头像、动画小人、通知中心。
- **她本人主动访问手机事实 / 提交合法操作的认知或活动接线未实现**（属于 P 与 Life/cognition 的写集）。本交付只是"用户发起的操作面板 + 真实状态读取"，不冒充自治 Decision 或 MainSelf；用户操作在界面上是明确的人为指令。
- 版本冲突只在纯函数与状态机层面验证过（`deriveMutationOutcome` 409 → conflict，`resultCard` 给"重新读取最新列表"），没有真实后端的 409 端到端会话（后端未落地前无法造，不拿假的当通过）。
- 没跑整套 Python 回归、没重算全树 SHA、没改 manifest/release。

## 7. 返修记录（真实浏览器链复验后，2026-10-17）

**实锤缺陷**：`inventoryView()` 把 `item_instance_id` 送进了 `sanitizeLine()`；真实 World 物件 id 是 `item-a5149580…94b0`（`item-` + 64 hex，69 字），`maskSensitive()` 的 40+ hex 规则把它遮成 `item-[已隐藏的长标识]`。于是物品页"使用"发出的 `POST /api/phone/items/use` 里 `item_instance_id` 是假值，World 回 `NOT_AN_ECONOMY_INSTANCE`——已买到手的那份点心永远用不掉（P 实例 `/tmp/nyaw05r.jdK3q/v04-phone-p-accept1` 里余额扣过、物件 OWNED 且未送达消费）。

**改法**：协议值不再过展示处理。新增纯函数 `protocolId(value, maxLen)`（只校验类型/长度/控制字符，通过后原字节返回）与 `safeLabel()`（展示专用脱敏标签），视图里同时给 `id`（原字节）与 `idLabel`（脱敏）。`shopView` 同理给 `id`/`idLabel`，`inventoryView` 另给 `catalogId`/`catalogLabel`。新增 `buildUsePayload()` / `buildPurchasePayload()` 作为真实 payload 的唯一点（`ok:false` 就不发请求）；`buildNotePayload()` 的 `note_id` / `expected_head` / `operation_key` 也改走 `protocolId()`（不再 trim、不再剔字符）。`createOpStore()` 的 scope 与 `createNavStore()` 的 `detail` 改为原字符串（否则两个 64hex 物件会遮成同一个 scope、复用同一个操作键；刷新后商品详情也找不回那一件）。`readStatusWarning()` 让钱包/商店/物品不再把读取信封的 `status: applied` 当异常，`deriveReadOutcome()` 认 `applied / already_applied` 为成功读取；`deriveMutationOutcome()` 一字未改。

**验证**：`node --check components/phone/web/app.js` 通过；`node --test tests/test_phone_ui.mjs` → `# pass 34 / # fail 0`（原 27 + 7 条真实 schema ID 负控）。真实浏览器再消费由父级执行，本轮不重复购买、不重启实例。
