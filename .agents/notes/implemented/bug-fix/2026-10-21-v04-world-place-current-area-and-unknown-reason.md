# Agent Note: 放下（PLACE）把当前房间当成任意房间，并把真原因压成 UNKNOWN

Status: implemented

## Problem

rc1 live 实例（`/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，父级 serve 4615 / world 4617，
`http://127.0.0.1:50519/`）里真实浏览器按顺序做：余额 9760、买到一件
`item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c`（materialized 于
`home/bedroom`）→「拿起到左手」`applied`（`phone-exec:fd5e8a90628dfee49ab4774f878755b8`）→
「放下」。界面提交的目标就是当前房间 `{kind:'room', location_id:'home', area_id:'bedroom'}`，
服务却回 **409 rejected，reason_code 只有一个 `UNKNOWN`**，手仍持有
（失败 `phone-exec:4a28fc68899ae7b90ba3b450cb4a3d42` / op `0c0a26da-00f6-4272-b2d4-f290592df3a6`）。
原红保留在 scratch `rc1-world-browser-continuation.json`，未覆盖。

只读取证（live 一个字节没动，全部在 `/tmp/nyaw05r.jdK3q/rc1-place-*` 副本上复算）：

1. **那次 PLACE 真正提交的参数**：`run/action_authorizations.json` 记着 PLACE 的
   `args_digest=d6395706257f48216db442af785dcece01da295bda4c765adf8cbf5f83f803c7`。用
   `world_action_authorization.canonical_args_digest` 复算，PICK_UP（`ec8eb7f4…`）作为控制组
   命中，PLACE 命中的参数是
   `{"object_id": "item-[已隐藏的长标识]", "target": {"kind":"room","location_id":"home","area_id":"bedroom"}}`。
   即界面把**手持项的显示标签**当 object_id 回传：`objects.hands.left_hand` 走
   `safeLabel(id, 80)`（`app.js:1172`），`maskSensitive` 把 64 位 hex 抹成 `[已隐藏的长标识]`
   （`app.js:251`），「放下」再把这个**展示串**交给 `confirmPlace` → `submitWorldAction`
   （`app.js:3525-3541`、`3757-3773`）。这是 U/P 写集，本轮未改。
2. **合法目标本身是通的**：在真实 world-home 副本上用真 Resolver 提交
   `{kind:'room', location_id:'home', area_id:'bedroom'}` → `applied` / `place_committed` /
   ledger `committed`。所以 409 不是 area 规则拒的。
3. **真缺陷一（C 侧）**：同一个副本上把目标换成同楼另一间 `home/kitchen` → 一样 `applied`，
   revision 4→5。room 目标根本没有 area 规则：`_m15_destination_verdict` 对 `kind=='room'`
   只查「地点在世界里 + 从当前地点可达」，从不比对身体所在的 area。
4. **真缺陷二（C 侧）**：被拒时的原始响应是 `reason_code=UNKNOWN_TARGET`、
   `legacy_reason_code="UNKNOWN: item-… has no placement"`；`PhoneWorld._reason_of` 按 `":"`
   取前缀 → `UNKNOWN`，正是界面显示的那个码。真实原因（这个 id 没有摆放）被吞掉了。

## Decision

**PLACE 的房间目标只能是世界状态声明的「当前地点 + 当前 area」；被拒时给出的必须是可执行的码，
不是 `UNKNOWN`。**

1. `world_action_resolver._resolve_place`：读 `world_state.location` 的 `place_id`/`area_id`，
   `kind=='room'` 且目标 `(location_id, area_id)` 与之不符时，在写 `prepared` 之前拒绝：
   canonical `NOT_REACHABLE`，特定拼写
   `NOT_CURRENT_AREA: <place>/<area> is not the body's current area <place>/<area>`。
   拒绝发生在账本与 substrate 之前 → 零写入、物件仍在手、`operation(key)` 查无此执行。
   只在世界**真的声明了**当前 area 时才断言：世界没给的事实不凭空造拒绝，此时拓扑
   （可达性）判定仍是唯一权威。`LEGACY_CODE_MAP` 增补 `NOT_CURRENT_AREA → NOT_REACHABLE`。
2. 不再出现裸 `UNKNOWN`：resolver 三处「没有摆放 / 没有活着的摆放」的原因改用真实拼写
   `UNKNOWN_TARGET:`；`PhoneWorld._reason_of` 把 legacy 前缀 `UNKNOWN` 当占位符处理，
   回落到 canonical `reason_code`，而具体拼写（如 `REST_WHILE_TRAVELLING`）依旧优先。
3. 一个字没动：能力门（takeable/portable/placeable）、所有权、跨**地点**的既有目的地判定
   （`_m15_destination_verdict` 的语义与签名不变）、未知目标、手的槽位占用、六指标/速率/
   `POSE_STRAIN`/`RECOVERY_OPEN_FATIGUE`、REST 的姿势与阈值、`world_body_substrate` schema。

## Alternatives considered

- **把 area 规则塞进 `_m15_destination_verdict`**：它算的是「世界拓扑」——入参是 `world_place`
  而不是身体位置，而且 `tests/test_world_runner.py` 已断言它对「另一地点但仍可达的 room 目标」
  返回 `(None, None)`。把身体的当前 area 拼进去会改掉那条既有契约，也把「命令的物理前提」
  伪装成「世界拓扑事实」。否决。
- **直接修 `app.js`（真 bug 的源头：展示串当协议键）**：这是 U/P 的写集，本轮不能动；
  C 侧能做且该做的是「别把它说成 UNKNOWN」。已在报告里点明，留给 U 修。
- **让 owner 接受被抹过的 id，或用标签/前缀反查实例**：等于猜，直接违反「未知目标不放开」。
  否决。
- **世界状态没有 area 时也拒绝 room 目标**：看起来更 fail-closed，但那是拿「世界没声明」
  去造一条新拒绝，会给旧 fixture 和 substrate-only 场景凭空加限制；保留既有拓扑判定更诚实。
- **把 legacy 整段文本（`UNKNOWN: … has no placement`）回给界面**：信息最全，但破坏电话契约
  「窄码 + detail 分开」的承诺，也把 detail 混进码位。否决；改为修拼写本身。

## Consequences

- **收益**：同楼另一个房间不再能「隔空放下」（本轮之前它真的会写进 substrate）；合法当前
  房间的放下照旧 `applied`（真实 world-home 副本复核过）；被拒时界面拿到
  `NOT_CURRENT_AREA` / `UNKNOWN_TARGET` 这类可执行事实，不再是 `UNKNOWN`。
- **代价**：新增一个 legacy 拼写与 canonical 的映射；任何按旧文本 `"UNKNOWN: …"` 解析的下游
  要改成 `UNKNOWN_TARGET`（仓库内没有这样的解析，已 grep 确认）。
- **上限 / 剩余**：界面仍然把显示标签当 object_id（`app.js`，U/P 写集）。父级下一次真实浏览器
  会被 C 侧如实拒成 `UNKNOWN_TARGET`（不再是 `UNKNOWN`），但要看到「放下成功」必须等 U 修那一处；
  本轮没有重启、没有重买、没有重置、没有碰 live 数据，父级自己核 pid 停 serve 再重启加载修复后，
  原 `left_hand` 里那件物品可以直接放下，不必重新拿起。

## 测试（真实命令、真实退出码）

- `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_world_body_life.py` → `Ran 27 tests … OK`（exit 0）。
  原 20 条不变，新增 7 条 `ResolverPlaceAreaTests`：当前房间放下成功；同楼另一 area 拒绝且
  substrate 字节与账本行数都不变、物件仍在手；没有 area 的 room 目标在**任何写入之前**被拒；
  规则读的是世界状态（把 area 改成 kitchen 后旧房间被拒、kitchen 被接受）；被抹过的 id 是
  `UNKNOWN_TARGET`；`_reason_of` 不再把真原因变成 `UNKNOWN` 且具体拼写仍优先；同一 execution
  只放一次（prepared + committed 两条，重放不新增记录且字节不变）。
- `python3 tests/test_phone_world_owner.py` → `Ran 20 tests in 2.562s … OK`（exit 0）。
  原 16 条不变，新增 4 条 `PlaceAreaTests`（真 service + 真 gateway socket + 真 Resolver/账本/授权）：
  手持物件放回**快照报的**当前地点/area 成功且 substrate revision +1；`home/kitchen` 被拒
  （`NOT_CURRENT_AREA`，不是 `UNKNOWN`），world/账本/body 字节不变、手仍持有、
  `operation()` 为该 key 返回 `OPERATION_NOT_FOUND`；被抹过的 id 被拒成 `UNKNOWN_TARGET`；
  同 key 重放 `already_applied` + `replay_evidence=matched` 且零写入，换目标
  `OPERATION_TARGET_CONFLICT`。
- `python3 -m unittest tests.test_phone_world_api` → `Ran 9 tests in 97.385s … OK`（exit 0，HTTP 面直接受影响）。
- `python3 tests/test_phone_shopping_owner.py` → `Ran 21 tests in 0.953s … OK`（exit 0）。
- `cd components/world/service && python3 test_object_action_m15.py` → `Ran 20 tests in 0.528s … OK`（exit 0，M15 PLACE 电池未回归）。
- `git diff --check` → exit 0。
- 只读取证脚本在 scratch：`rc1-place-digest.py` / `rc1-place-digest-brute.py` /
  `rc1-place-digest-masked.py` / `rc1-place-evidence.py` / `rc1-place-probe.py`；
  夹具 `/tmp/nyaw05r.jdK3q/rc1-place-*` 唯一创建、**保留**，不删。
- **这不是浏览器验收**：父级原红 `rc1-world-browser-continuation.json` 保留未覆盖，
  真实 Edge 串链仍由父级重启后自己跑。

## 检索与分类（写本篇前的当场审计）

按 `PLACE` / `area` / `UNKNOWN` / `摆放` 检索 `.agents/notes/`：

- `implemented/architecture/2026-10-17-world-consume-placement-receipt.md`：**相关**——它管 CONSUME
  的摆放回执与账；本篇只改 PLACE 的 room 目的地判定与原因拼写，没碰 receipt 与消耗语义。
- `implemented/architecture/2026-10-17-world-consume-economy-facts-failclosed.md`：**同向不冲突**
  ——「无法证明就拒绝」和本篇的「世界没声明就不造判定」是同一条纪律。
- `implemented/bug-fix/2026-10-21-v04-world-ui-stale-snapshot-after-purchase.md`：**相邻但不同缺陷**
  ——那条是读侧快照新鲜性，本篇是写侧目的地与原因码。
- `implemented/feature/2026-10-21-v04-world-body-resident-and-rest.md` 与
  `docs/engineering/v04-world-body-contract.md`：**被本篇补边界**——它们把 PLACE 写成
  「真实能力门 + 目的地判定」，没有写「房间必须是当前房间」；字段与动词表未变。
