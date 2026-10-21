# Agent Note: v0.4 虚拟手机前端只读消费 /api/phone，未知写入留键可查

Status: implemented

## Problem

v0.4 施工单要求"一部能打开、能实际操作的手机"，而仓库里此前只有后端 owner 与 CLI，没有任何浏览器界面；用户看到的后台能力无法变成可感知的日常入口。与此同时，最容易走样的地方是界面：只要前端悄悄 mock 一份余额、把 2xx 当成功、或者用新的幂等键把超时的购买重发一遍，验收就会在一屏假数据上通过，真实 owner 的扣款与获物语义反而被掩盖。手机必须只呈现已有 World / Supply owner 的事实，不能变成第二套账本，也不能变成新的 Phone authority。

## Decision

`components/phone/web/{index.html,app.js,style.css}` 作为纯前端消费层落地，规则如下，并由 `tests/test_phone_ui.mjs` 逐条钉住：

1. **同源单入口**：只请求 `/api/phone/*`，静态资源只有这三个本地文件；`index.html` 自带 `default-src 'self'` 级 CSP，没有 CDN、没有构建步骤、没有前端框架。
2. **没有服务就显示未连接**：state 请求失败显示"未连接 / 服务状态不可用 + HTTP 码 + 重试"，磁贴全部"未报告"。不提供任何示例数据分支，"mock 一下先看效果"在本写集里是禁止项。
3. **CSRF 从 state 取，取不到就整机只读**：`csrfFromState()` 依次读 `state.csrf_token / csrfToken / data.csrf_token / instance.csrf_token`；`buildRequestInit()` 对写请求强制要求令牌，缺失直接抛 `CSRF_MISSING`，界面写清"不会绕过"，不发无令牌请求。读请求不带该头。
4. **mutation 只认服务状态**：`deriveMutationOutcome()` 接受 `applied / already_applied / rejected / unknown / conflict`；409 → conflict；400/422/`ok:false` → rejected；5xx、网络断、超时、无法解析、**2xx 但没有 status** → unknown。没有 status 的 2xx 绝不当成功。读取侧相反：`deriveReadOutcome()` 明确把 `ok:true`、以及没有 `ok` 字段但 `status` 是 `applied / already_applied` 的读取信封算成功（钱包/商店/物品端点正是这样报"这次读取成立"），其它状态才算读取失败；这条放宽只作用于读取，`deriveMutationOutcome()` 一字未改。
5. **一个意图一个键，未知留键**：scope = `note:create` / `note:write` / `reference:add:<source>@<rev>` / `purchase:<catalog_item_id>` / `use:<item_instance_id>`；同 scope 复用同一 UUID（`crypto.randomUUID()`，无 crypto 时 `op-<时间>-<随机>`）。scope 与导航 `detail` 都保留服务原字符串（不脱敏、不截断），否则两个 64hex 物件会撞成同一个键、刷新后也找不回那一件。终态释放键，未知保留并进"未确认的操作"，用 `GET /operations/<key>` 对账；不自动换键重发。
6. **存储只放键与导航**：不用 localStorage；sessionStorage 只有 `nyairo.phone.pending.v1 / unresolved.v1 / nav.v1`，条目只有 `{key, at, path}` 与 `{screen, detail}`。余额、笔记正文、价格、流水一律不缓存。
7. **文本即文本**：所有后端字符串先经 `sanitizeLine/sanitizeText`（去控制字符、遮 `C:\Users\…`、`/home/…`、`Bearer …`、40+ hex），再用 `textContent` / `createTextNode` 写入；`app.js` 内没有 `innerHTML / outerHTML / insertAdjacentHTML / document.write / eval / new Function`；只读字段渲染跳过 `token/secret/csrf/password/cookie/session/home/profile/path` 一类键。
8. **协议标识与展示标签彻底分家**：`item_instance_id / catalog_item_id / note_id / expected_head / operation_key` 是服务里的真实键，`protocolId()` 只做类型、长度（id 与 note_id 200、head 256、operation_key 128，与后端 `MAX_OPERATION_KEY=128` 对齐）、控制字符校验，通过就把**原字节**交给网络 payload 与查找/导航 key；不 trim、不截断、不脱敏。屏幕上另走 `safeLabel()`（= `sanitizeLine`，可遮罩可截断），永远用 `textContent` 写入，禁止把展示标签当业务 id 用。校验失败就不发请求（`buildUsePayload()` / `buildPurchasePayload()` 返回 `ok:false` 并进"未发出"结果卡），绝不"改一改再提交"。
9. **不造钱**：`formatMoney(currency, amount)` 缺少币种或数值即返回 null，界面写"未提供/价格未提供"，不补 0、不猜单位、不换算；`purchase_available !== true`、价格缺失、无 CSRF 任一成立都不允许下单；商店不可购买时仍展示 catalog 与 `blocked_reason`，但用 warn 色而不是绿色成功块。
10. **OWNED ≠ 送达 ≠ 可用**：物品页同时显示 `ownership / location / delivery_status / usable / blocked_reason`，`usable` 必须是布尔 true；字符串 `"yes"` 当不可用。
11. **界面是人的操作入口**：不冒充自主 Decision / MainSelf，不提供调试开关或直接改状态的入口；身体视图只读。
12. **修订必须带条目 id**：`POST /notes` 的 payload 为 `{note_id?, title, body, operation_key, expected_head?}`；新建不传 `note_id`，修订传打开列表项时记下的 `id`/`note_id` **原字节**，标题与正文按原文提交、靠 `expected_head`（真实形如 `artifact-version:<32hex>`）做 CAS，**不按标题猜是哪一条**。条目 id 或 head 校验不过就直接挡住，不 trim、不截断、不脱敏后提交。

## Alternatives considered

- **先写一个 mock API / 前端内置示例数据把界面做"满"，再接真后端** — 立论：能最快看到成品观感，评审不必等 P。否决：施工单明确"没服务就显示未连接、不默默切 mock"，示例余额一旦进入验收就等于伪造 World 事实；渲染观感可以靠 scratch 里一次性 harness 验证，不必污染产品代码。
- **用 Vue/React + 打包器** — 立论：组件化更好维护、状态管理成熟。否决：仓库当前没有前端构建链，CSP 自足和"直接用浏览器打开就能跑"更重要；本交付只有一个屏幕族，原生 DOM 足够，多一层构建反而让 P 的静态挂载变复杂。
- **未决 operation 键存 localStorage，跨标签页共享** — 立论：用户关浏览器后仍能对账，体验更连续。否决：localStorage 是长期、跨会话的业务性存储，会把"前端也在记账"变成事实；sessionStorage 只保存未决键与导航，服务端才是账本。
- **2xx 就当作成功，失败才亮红** — 立论：合同里成功占多数，代码更短。否决：施工单要求 unknown 可见且可查；没有 status 的 2xx 可能正是"服务已扣款但回执丢失"的情形，误判成成功会让用户重复购买。
- **前端缺币种时默认 CNY、缺价格时显示 0** — 立论：界面不会出现空白格，观感统一。否决：那是在屏幕上造不存在的钱；"未提供"虽然不好看，但它诚实。
- **先把 id 脱敏再当业务 id 用（反正屏幕上看起来一样）** — 立论：一处 `sanitizeLine` 同时解决"不露长标识"和"少一个字段"，代码最短、单一数据源。否决：真实事故就是这么来的 —— 手机 P 实例里 `item-a514…94b0`（`item-` + 64 hex）被遮成 `item-[已隐藏的长标识]`，`POST /api/phone/items/use` 发出的是假 id，World 回 `NOT_AN_ECONOMY_INSTANCE`，已购的那份点心永远用不掉；同理两个 64hex 物件的 scope 会遮成同一个键，操作键互相顶替。脱敏只属于展示，协议值必须是服务原字节。
- **遇到不合法 id 就 trim/截断后照发** — 立论：让请求"总能发出去"，用户少一步。否决：改过的字节对应的是另一件或根本不存在的物件，等于把本地猜测当成服务事实；现在宁可显示"标识不可用、没有发出请求"。

## Consequences

- **收益**：界面与 owner 之间只有读和显式命令两条通道；未知结果有键可查、不会自动重发；缺字段和缺令牌都会变成可读状态而不是假成功。P 只要按 `/api/phone` 合同挂载，前端不改一行即可接通。
- **修掉的真实缺陷（2026-10-17 版遗留）**：`inventoryView()` 曾把 `item_instance_id` 送进 `sanitizeLine()`，`item-<64hex>` 被 40+hex 规则遮成 `item-[已隐藏的长标识]`，于是"使用"发出的 payload 与 World 里的物件实例不是同一件（后端 `NOT_AN_ECONOMY_INSTANCE`）。现在视图与 payload 都走 `protocolId()` 的原字节，展示另用 `idLabel`/`catalogLabel`/`safeLabel()`；`shopView()` 的 `catalog_item_id`、`noteListView()` 的 `id`/`head`、`createOpStore()` 的 scope、`createNavStore()` 的 `detail` 同样按原字符串处理。
- **读取状态语义收窄**：钱包/商店/物品的 GET 信封用 `status: applied` 表示"这次读取成立"，旧渲染把它当异常，屏上出现"需要注意 · 服务报告…已生效"的误导黄条。现在 `deriveReadOutcome()` 认 `ok:true` 与无 `ok` 但有 `applied/already_applied` 的读取信封为成功，渲染层用 `readStatusWarning()` 只对真正的异常状态提醒；POST 的 unknown/rejected 判定没有改动。
- **代价与已知上限**：CSRF 字段名是前端按合同缺省推断的（state 文档没写），最终字段名需 P 明确；不在 `csrfFromState()` 里就只读。阅读/流水等列表项字段名是按合同与常见命名做的候选匹配，字段名漂移时会显示"未提供"而不是错值。版本冲突只做了状态机与渲染层的定向验证，没有真实后端 409 的端到端会话。"她本人主动访问手机事实"的认知/活动接线不在本写集，明确未实现。

## Verification

- `node --check components/phone/web/app.js`
- `node --test tests/test_phone_ui.mjs` → `# pass 34 / # fail 0`（2026-10-17 返修后本地执行：原 27 条 + 7 条真实 schema ID 负控 —— `item-`+64hex 的库存 view id 与 `POST /items/use` payload 原字节不变、展示标签脱敏；120 字长 `catalog_id` 在购买 payload 与导航 detail 里不被截断；`artifact-version:`+32hex 的 head 与 `note:` 开头的 note_id 原样进 payload；控制字符/超长/非字符串一律拒绝且不发请求；两个 64hex 物件的 scope 不撞键；读取信封 `applied/already_applied` 算成功而 POST 未知语义不变）
- headless Edge `--dump-dom` 冒烟（scratch 一次性 harness，stub 不落仓库，本节为上一轮已验证记录，返修未重跑；真实浏览器链路由父级复验）：7 屏 + 笔记保存 + 收藏 + 钱包 + 购买（含二次确认弹层）+ 使用回执全部真实渲染且 `window.onerror` 为空；500 购买 → 结果未知、900ms 内 `POST /purchases` 仍 1 次、查询同键后 `已生效` 且未确认列表清空；402 → 已拒绝且结果卡不是绿色；无服务静态页 → "服务状态不可用 + HTTP 404"、全部磁贴"未报告"。
- 结果说明：`docs/engineering/v04-phone-ui-result.md`。
