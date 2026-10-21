# Agent Note: 世界屏复用购买前的旧快照——跨 app 的物件变化必须让 /world 快照失效

Status: implemented

## Problem

父级在同一 live 实例（`/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，serve 4615，
`http://127.0.0.1:50519/`）用真实 Edge 页面按顺序做：世界屏（读到「这个位置没有摆着的东西」）→
商店买一包点心（钱包 10000 → 9760，一笔）→ 回世界屏。

结果：世界屏仍是购买前的事实 —— 手边只有床、没有新物件、没有「拿起」按钮。
同一个实例的 `GET /api/phone/world` 已经是对的（owner 报 `here` 里
`item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c`、`portable=true`，
`PICK_UP available=true`），只有界面在骗人。父级证据：`rc1-world-browser-proof.json`（真实 FAIL）。

根因在 `components/phone/web/app.js`（U 写集）的收尾函数 `refreshAfter(scope)`：
`purchase` 分支只把 `shop / inventory / wallet` 置空，**没有**让 `store.data.world` 失效；
`use`（CONSUME）分支同样只置空 `inventory`。而 `ensureLoaded(screen)` 的判据是
`if (!store.data.world) loadResource('world')` —— 缓存还在就一律复用。于是「买了一件东西」
这种真实改变了世界物件的操作，在读世界这条路上完全不可见，直到整页刷新。

## Decision

**只要跨 app 的写入可能改变这世界里的物件，就作废 `store.data.world` 这一份快照；
下一次进入世界/身体屏必然重读一次 `/world`。**

1. 新增唯一的失效口 `invalidateWorldSnapshot()`：`store.data.world = null`；
   如果人此刻正停在 `world` / `body` 屏上，就地补一次 `loadResource('world')`。
2. `purchase` 与 `use`（CONSUME）分支都调用它 —— 购买产生新物件、消耗减少物件，
   两者都改手持与摆放。
3. `world` 分支（MOVE/PICK_UP/PLACE/POSE/REST 收尾）改为复用它，语义与原来一致
   （原来就是置空 + 在 world/body 屏上重读），只是不再有三份重复逻辑。
4. 不做「进屏就无条件重读」：人不在那个屏上时不发请求，缓存只在事实可能变了时失效。
5. 不排第二条请求：`loadResource()` 先把 `store.data[resource]` 写成
   `{phase:'loading'}`（真值），`ensureLoaded` 的判据因此在整个 in-flight 期间为假，
   同一资源不会被并发读两次。
6. `GET /api/phone/world` 仍然只读、零写入（契约 §6 不变）；identity、权限、
   `operation_key` 与 pending/unknown 的保留规则一个字没动。

## Alternatives considered

- **每次进入世界屏都重读 `/world`（onenter 无条件刷新）**：最省事，也确实能治好这一条；
  但世界上没有任何变化时也在打请求，窄屏切来切去会连着打，且把「谁该失效」这个知识
  从写入侧偷偷搬到了导航侧 —— 其余八个 app 的 `ensureLoaded` 判据会立刻不一致。
  否决原因不是请求贵，而是它掩盖了「改动没有通知读取方」这个真问题。
- **定时轮询 / 长连（SSE）世界状态**：能让界面更"实时"，但会把 owner 的只读快照变成
  持续负载，并把 UI 的 freshness 建立在时间窗上（还是一样的窗口期内会骗人）。
  本轮只做有界修复，不引入新的后台循环。
- **在购买回执里回写世界物件（POST 返回啥就往 `store.data.world` 里塞）**：看起来最省请求，
  但那是让界面自己拼世界事实 —— 新物件的摆放、可拿性、左右手容量都由 owner 判定，
  界面拼出来的必然是猜的。契约 §2.6 的 `portable` 三态正因为如此才必须来自服务。
- **只把 `store.data.world` 置空、永不主动重读（连 world 分支的重读也去掉）**：
  等价于"回到屏上再看"，但人在世界屏上做 MOVE/PICK_UP 后屏上会停住不动，
  属于把新鲜性问题推给用户手动刷新。保留「在屏上就补读一次」。

## Consequences

- **收益**：父级那条失败路径（世界旧快照 → 购买 → 回世界）现在会在回屏时重读一次
  `/world`，新物件与「拿起到左手/右手」按钮都按 owner 的新事实出现；世界屏、身体屏、
  手边、技术详情用的是同一份快照，一起变新。
- **代价**：购买/消耗之后首次回到世界或身体屏会多一次 `GET /world`（人不在那两个屏上时不发）；
  界面不缓存跨 app 事实这件事从此是硬约定 —— 以后任何改物件的新写入面（包括 P 侧将来的
  新端点）都必须在这里补一条失效，否则又会静默显示旧世界。
- **上限**：这是**界面侧的 freshness 修复**，不是实时同步：在 world 屏停留期间 owner 侧
  因 tick 推进发生的变化（到达、恢复推进）仍要靠屏上的「刷新」按钮或重新进屏才看得到，
  本轮不引入自动轮询。

## 测试（真实命令、真实退出码）

- `node --check components/phone/web/app.js` → exit 0。
- `node --test tests/test_phone_world_ui.mjs` → `# tests 24 / # pass 24 / # fail 0`，exit 0
  （原有 23 条不变，新增 1 条回归）。
- `node --test tests/test_phone_ui.mjs` → `# tests 34 / # pass 34 / # fail 0`，exit 0（旧七入口未回归）。
- 新增回归 `（stub DOM 冒烟）世界有旧缓存时购买，回世界必须重读 /world 并出现新物件与拿起按钮`：
  先点进世界读空物件快照 → 主屏 → 商店 → 商品详情 → 真点「确认购买」（一条
  `POST /purchases`，`catalog_item_id` 原字节、`operation_key` 复用同一次意图的键）→
  把 `/world` 的 stub 换成"已含新物件"的新快照 → 回主屏再进世界：断言 `GET /world`
  正好 2 次（不多不少）、旧文案「这个位置没有摆着的东西」消失、新物件标签出现、
  「拿起到」按钮出现。**负控已做**：把 `purchase` 分支的 `invalidateWorldSnapshot()`
  临时去掉后同一套件为 `23 pass / 1 fail`，失败信息正是「回到世界要重读一次 /world」。
- `git diff --check` → exit 0。
- **这不是浏览器验收**：stub DOM 只证明缓存失效与重读接线，布局、滚动、真实点击仍由父级
  在同一实例里做（`rc1-world-browser-proof.json` 的原 FAIL 保留，不覆盖）。

## 检索与分类（写本篇前的当场审计）

按 `refreshAfter` / `缓存` / `invalidate` / `store.data` 检索 `.agents/notes/`：

- `implemented/feature/2026-10-17-v04-phone-web-ui-consumer.md`：**相关但不冲突** ——
  它规定 sessionStorage 只放键与导航、余额/正文/价格不落盘；本篇讲的是进程内存里的
  `/world` 快照失效，没有把任何事实写进会话存储，反而与"界面不持有事实"同向。
- `implemented/architecture/2026-10-17-world-consume-economy-facts-failclosed.md` 与
  `...-placement-receipt.md`：**无关** —— 它们管 CONSUME 的账本与摆放回执（owner 侧），
  本篇只管读侧的新鲜性，未改任何写入语义。
- `implemented/testing/2026-10-17-cross-process-life-restart-harness.md`：**无关**（进程内残留状态）。
- `implemented/feature/2026-10-21-v04-world-body-ui-consumer.md` 与
  `docs/engineering/v04-world-ui-result.md`：**被本篇修订的部分** —— 它们把命令链路写成
  `→ refreshAfter('world') 重读 /world`，但没有写"购买/消耗也要让世界快照失效"；
  本篇补上这一条，字段与文案契约（`v04-world-body-contract.md`）未改。
