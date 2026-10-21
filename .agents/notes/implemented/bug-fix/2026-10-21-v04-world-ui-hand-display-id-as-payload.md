# Agent Note: 手上的展示串被当成协议 ID——PLACE 必须发 owner 的 69 字原字节

Status: implemented

## Problem

父级在同一实例（`/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，serve 4615）继续真实浏览器链：
先在房间里把点心拿起到左手（`PICK_UP` 成功），再点「放下」时世界拒绝。Core 只读取到的
`args_digest` 证明这次 `PLACE` 的**真实参数**是：

```
{"object_id":"item-[已隐藏的长标识]","target":{"kind":"room","location_id":"home","area_id":"bedroom"}}
```

`target` 完全正确，`object_id` 却是屏幕上的**展示串**。真正的手持物件是
`item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c`（69 字），
它此刻就在该实例的 `left_hand` 上（本轮只读引用，不操作实例）。

读侧把两类值混成了一个：`worldObjectsView()` 里 `here` 每件物件走
`protocolId(entry.object_id, WORLD_OBJECT_MAX)`，原字节存在 `id`、屏幕上另用 `idLabel`
（所以从地上拿起是好的）；但 `hands.left_hand/right_hand` 与 `held` 直接跑
`safeLabel(...)`，而那正是会遮罩的通道——`sanitizeLine → maskSensitive` 把
`\b[A-Fa-f0-9]{40,}\b` 换成 `[已隐藏的长标识]`，于是 `item-<64 hex>` 变成
`item-[已隐藏的长标识]`。

写侧随即把这个显示串当键：`worldHandRow()` 只拿到一个字符串，却调
`worldPlaceOption(view, {id: objectId, idOk: true})`——`idOk: true` 是**伪造**的，
等于自己给自己发了"这是合法协议 ID"的通行证——再交给 `confirmPlace()` 原样塞进
`PLACE{object_id}`。所以同一次现实里：地上的物件拿得起来（原字节），拿起来之后反而放不下
（遮罩串），owner 只能按"没有这件东西"拒绝。

## Decision

**手持与手上的物件跟 `here` 一样分成两个字段：协议原值只进 payload，展示标签只上屏。**

1. 新增 `worldHandView(raw)`：返回 `{present, id, idOk, idMessage, idLabel}`。
   `id` 来自 `protocolId(raw, WORLD_OBJECT_MAX)` 的**原字节**，`idLabel` 走
   `safeLabel(..., 40)`（可以遮罩、可以省略，永不进请求体）。非字符串 / 空 / 超长 /
   含控制字符 → `idOk=false` 且带 `idMessage`，界面严格拒绝，不拿展示文字冒名提交。
2. `worldObjectsView()` 新增 `handSlots{left_hand,right_hand}` 与 `heldObjects[]`
   （每项都是 `worldHandView` 的结构）；`hands` / `held` 保留为**展示那一份**
   （`present ? idLabel : ''`），空着仍是假值，旧消费点按真值判断手是否空着的语义不变。
3. `worldPickupOptions()` 判断"哪只手空着"只认 `handSlots[slot].present`，
   `hands` 那份展示串不再参与任何判定。
4. `worldHandRow(view, slot, hand)` 收 `handSlots[slot]`：屏幕写 `hand.idLabel`；
   `worldPlaceOption` 与 `confirmPlace` 只用 `hand.id`；`idOk=false` 时「放下」直接
   `disabled` 并用 `idMessage` 说清原因（不发请求）。
5. `confirmPlace(view, hand)` 的弹层行写 `hand.idLabel`，`PLACE` 的 `object_id`
   只发 `hand.id`（69 字原字节），`target` 仍是 `{kind:'room', location_id, area_id}`。
6. 不碰 owner 侧、不动 `PICK_UP` 的既有参数、不改 `protocolId` 的校验规则，也不放宽
   `safeLabel` 的遮罩：屏幕上依旧看不到长标识原文，只是它不再冒充协议 ID。

## Alternatives considered

- **给 `safeLabel()` 加一个"协议 id 不遮罩"的开关**：一处改动看着最省事，但它把
  "哪个值是协议 ID"这个知识交给了调用方的心情，而这次的 bug 恰恰是调用方记错了；
  同时会把 69 字原文直接打到屏幕上，与既有脱敏规则和测试冲突。否决原因不是麻烦，而是
  遮罩与协议值必须由**结构**分开，不能靠传参约定。
- **在 payload 组装处把遮罩串"还原"回原 ID**：`maskSensitive` 是有损替换
  （`[已隐藏的长标识]` 里没有原字节），只能靠猜或再查一次 owner，等于把不可逆的损失
  推到最后一刻。做不到就不做。
- **在 `confirmPlace` 里拿展示串去 `view.objects.here` 反查原 id**：手持的物件可能已经
  不在 `here`（正是本例），而且展示串不是唯一键（两件同前缀物件会被遮罩成同一个串），
  反查会静默取错件。这是把显示键当数据库用。
- **只把 69 字 ID 原样显示在屏幕上，不做遮罩**：能顺手"修好"这条链，但会让世界屏直接
  泄露长标识，违反既有脱敏断言；显示与协议分离才是正解。
- **给「放下」加一次只读预检（先 GET 再 POST）**：owner 的拒绝本来就是权威答案，
  多发一次读既不改变被判死的 id，也拉长交互；本轮只修字段串线。

## Consequences

- **收益**：手持物件与地上物件走同一套"原值 + 展示"规则，「拿起 → 放下」在同一次现实里
  闭合；`PLACE` 的 `object_id` 是 owner 认得的 69 字原字节，`here` 那条已经没有的第二份
  隐式规则消失了。
- **代价**：`worldObjectsView()` 的输出多两组字段（`handSlots` / `heldObjects`），
  `hands` / `held` 变成纯展示值；任何未来新增的"手上物件"消费点都必须读
  `handSlots`/`heldObjects`，读展示串会再犯同样的错——这条约定写在这里。
- **上限**：标识本身不可用（非字符串/超长/控制字符）时界面只会禁用「放下」并给原因，
  不会替用户猜一个 id；屏幕上仍然看不到原值，父级核对时看的是遮罩后的标签。

## 测试（真实命令、真实退出码）

- `node --check components/phone/web/app.js` → exit 0。
- `node --test tests/test_phone_world_ui.mjs` → `# tests 25 / # pass 25 / # fail 0`，exit 0
  （原 24 条不变，新增 1 条）。
- `node --test tests/test_phone_ui.mjs` → `# tests 34 / # pass 34 / # fail 0`，exit 0。
- 新增回归 `（stub DOM）手持长69字真实 id：世界屏点放下，PLACE 必须发原字节而不是遮罩后的展示串`：
  用**同一个 live 实例的真实 69 字 `left_hand` id** 造快照 → 先断言字段分开
  （`handSlots.left_hand.id` 是原字节、`idLabel` 已遮罩且与 id 不同、`heldObjects[].id`
  保留原值、左手占着时只能拿起到右手）→ 再走真实点击链：进世界 → 左手行点「放下」→
  弹层「放下手里的东西」→ 确认 → 断言只发一条 `POST /world/actions`，
  `action=PLACE`、`params` 只有 `object_id` 与 `target`、`object_id === 原 69 字`、
  `target` 仍是 `home/bedroom`，且请求体里没有「已隐藏」字样；屏幕与弹层都不回显原值。
- **负控已做**：把 `confirmPlace` 里的 `hand.id` 临时换成 `hand.idLabel`（即旧 bug），
  同一套件变成 `# pass 24 / # fail 1`，失败信息就是
  `actual: 'item-[已隐藏的长标识]'`，与 Core 现场一致。还原后 SHA256 回到
  `C696393625B851C6F0DEBC85A65453C5A66DEF2C74A6135BEA512E28165AC0A2`。
- **这不是浏览器验收**：stub DOM 只证明字段串线与请求体，真实点击与 owner 判决由父级在
  同一实例里做（`rc1-world-browser-continuation.json` 的原 FAIL 保留，不覆盖）。

## 检索与分类（写本篇前的当场审计）

按 `hands` / `held` / `safeLabel` / `protocolId` / `PLACE` 检索 `.agents/notes/`：

- [2026-10-21-v04-world-ui-stale-snapshot-after-purchase.md](2026-10-21-v04-world-ui-stale-snapshot-after-purchase.md)：
  **同一条真实链的前一段**（购买后世界缓存过期），修的是"读什么"，本篇修的是"发什么"；
  两篇互不覆盖，都指向同一个原则——界面不持有事实，也不改写协议值。
- `implemented/feature/2026-10-17-v04-phone-web-ui-consumer.md`：**相邻规则** ——
  它已经写明余额/正文/价格不落盘、协议 id 原字节回传；本篇是把同一条规则补到"手上物件"
  这个漏掉的字段上，未推翻任何既有决定。
- `implemented/bug-fix/2026-10-17-world-journey-ambiguous-submission.md`：**无关** ——
  那边是 owner 判决丢失后的对账，不涉及展示值与协议值的混用。
