# Agent Note: 邻居拓扑的物理 owner 链只能靠"真服务 + 正式授权端口"证明，且 owner 回执时钟不可注入

Status: implemented

## Problem

W04/W05 的日常链一直有两个盲点：

1. 世界移动要么走 `world_living_skeleton.apply_move_to`（引擎直接调 owner 函数，跳过授权链），要么只测单次提交；
   没有任何测试在**真实世界包的真实拓扑**上，经 `world_journey.accept_journey/advance_journey` 连续走
   8-12 条正时长边，并逐段证明"提交成功＝出发，不是到达；到期才真到达"。
2. 两条恢复边界没有物理证据：①在途段期间暂停 + 服务进程真的重启（新 PID、新 UDS 路径、同数据根），
   ②owner 真的提交了但回执丢失时，旅程只能靠**同 execution id 的只读对账**认领，不能重复提交。

做这件事时还撞到一个必须写下来的边界：**正式 submit 接口内部的 owner 回执时钟不可注入**。
`FormalOwnerMoveSubmitPort` 只把 `now` 交给意图，`world_action_resolver` 提交时调
`world_living_skeleton.apply_move_to(...)`（`world_action_resolver.py:942-947`）**不传 `now`**，
所以 travel 的 `started_at/expected_arrival_at` 永远是 owner 进程的墙钟；给 `WorldBodyService(now=...)`
注入一个冻结时刻也只能影响它自己的服务戳与开门时间规则，改不动回执。于是"journey 侧与 owner 侧共用同一个
注入时钟"这件事在现有产品里做不到，任何"统一时钟"的说法都会变成两套时间线互相打架。

## Decision

新增 `tests/test_world_neighborhood_owner_chain.py`（3 条精确用例，一次运行约 60 秒），只写测试与证据，
不动任何产品文件：

1. **主链（10 条真实边）**：`examples/worlds/daily_neighborhood_v1` 真 `init_instance` + 真
   `WorldBodyService`（真 `gateway.sock`、真授权租约、真 Resolver、真 travel 记录）+ 真
   `FormalOwnerMoveSubmitPort`，一个数据根上串 4 段旅程
   `home→street_front→street_corner→park→street_corner→cafe→street_corner→street_front→reading_point→street_front→home`。
   每段断言：提交回执 `TRAVEL_STARTED`、位置仍在段起点、owner travel `IN_PROGRESS`、旅程账本的
   `travel_id/started_at/expected_arrival_at` 全部等于 owner 记录；到期前（owner 公布的 `due-1s`，只读）
   位置不变、旅程投影仍 `in_transit`；到期后 `segments_confirmed+1`、盘上位置真的变成段终点、travel `ARRIVED`；
   10 条边 10 次提交 10 条 travel 10 个不同 execution id，`life_execution.jsonl` 每条 execution 只有
   1 条 `committed/move_departed`；旅程 completed 之后再推进不产生任何新提交。
2. **暂停 + 真重启**：worker 1（子进程，真 PID）经正式端口出发一段 → World 控制位 `world_control.set_paused`
   暂停（不是伪 Life operator pause）→ 在途段照旧 `in_transit`、下一段不出发；SIGTERM 让 worker 1
   `serve()` 退出并在 `finally` 里**只释放租约**（刻意不调 `service.shutdown()`：它只 unlink 自己绑过的
   socket，而本测试要求原 socket 留在盘上）→ 同数据根起 worker 2（真新 PID、新 `run_dir`
   ⇒ 新 UDS 绝对路径），旧 socket 的 `st_dev/st_ino` 前后一致；重启后 journey / travel / world_state
   **字节未变**，execution id、travel id、`committed` 条数都不变；暂停中到期只确认这一段（新 socket 上
   `submissions==0`），resume 后只发一段合法新段（新 execution id、起点=刚到达的地点）。
3. **回执丢失**：gateway surface 上的事件屏障在**真实提交落账之后**挂住回执，读查询在同一传输层注入失败
   （故障只在 test 的传输层，不绕过授权、不换 execution id）。屏障等**两条事实**才放行：
   `commit_done`（真实提交已落账）**且** `client.metrics.submitted >= 1`（客户端确实等满自己的 deadline
   才放弃）——只等后者会提前放行，把"提交是否已发生"变成竞速（父级同进程验收里就是这么红过一次的，
   见下文"屏障的时序"）。用例分三步且与提交快慢无关：① 故障窗口期只断言端口 `uncertain` 与旅程**非终态**
   （`unknown` 或已认领，禁止 `blocked`/`completed`）；② 有界等待提交落账后断言提交恰好一次
   （端口 1 次、gateway 1 次、账本 `attempts=1`、travel 恰好 1 条、位置不动）；③ 再推进一次，用同 execution
   的 owner 证据认领为 `started`（不是到达），`travel_id/started_at/expected_arrival_at` 逐字等于 owner 记录，
   `submissions` 恒 1；此后到到期前再推进仍只对账。

### 屏障的时序（父级同进程验收里红过一次）

父级把 Life + Stay + 本包 3 条放进**同一个 unittest 进程**，`LostReplyAfterRealCommitTest` 红在
`assertTrue(self.commit_done.is_set())`。红的那次 fixture 自身就是反证：`SUBMITTED → UNKNOWN`
（`transport_fault:TIMEOUT`）落在 09:10:57Z，而执行账本 `prepared 09:10:57.366Z → committed 09:10:58.263Z`
——owner **真的提交了且只有 1 次**，travel 1 条 `IN_PROGRESS`，位置仍在 `home`；没有提交拒绝、没有异常、
没有 mock 泄漏。真实 Resolver 的提交在 5 条用例的残留负载下要 ≈0.9s，比客户端 0.4s 预算慢，而第一版屏障
只等"客户端已放弃"就放行，于是断言跑在提交落账之前。同一根因的第二处：旅程在 `accept` 内只对账一次，
提交慢于客户端时那一刻还没有 travel 记录，旅程合法地停在 `unknown`（非终态），认领只能发生在**下一次推进**，
第一版却断言在 `accept` 的返回值里。修法是把时序写成事实链（等两条事实、分三步断言、有界等待提交落账），
断言一条没放松，还新增 `client.metrics.submitted == 1` 与 `timeouts >= 1` 证明故障窗口真实存在。

时钟纪律（写进测试 docstring 与结果文档）：journey 侧一律显式传 aware `now`；取值**只**由 owner 公布的
`expected_arrival_at` 推出（`due-1s` 只读、`due+1s` 推进），不真睡 1320 秒，也不宣称 owner 与 journey
共用同一个注入时钟。多段加速后 journey 侧时钟会领先 owner 回执（实测尾部约 +2 分钟，上界是一段最长走时），
因此"到期前"只用只读读取观察，绝不用倒退的时刻写账本。

夹具全部落在 `/tmp/nyaw05r.jdK3q/nb-owner-*`（含子进程源码 `worker.py`、`workers.jsonl` 里的真 PID），
永久保留：不 rmtree、不 unlink、不 TemporaryDirectory 清理、不删 pyc。

## Alternatives considered

- **直接调 `world_living_skeleton.apply_move_to` 或 Resolver 的 commit 函数** — 最省事，也是旧 `test_world_journey`
  的做法；但它绕过意图执行器与一次性授权，证明不了"位置权威在 owner 手里、授权链真的被消费"，正是 W05R 要补的洞。
- **给 `WorldBodyService(now=...)` 注入冻结时钟，宣称统一时钟** — 看起来最干净：服务戳、开门时间规则、
  `_moment()` 全都跟着走。但 travel 回执是 `apply_move_to` 不传 `now` 时用墙钟生成的，注入后回执仍在墙钟上，
  于是"统一时钟已证"是假的；要真统一就必须改产品（给 resolver 传 `now`），本批无权改。
- **把世界包复制一份把 `walk_seconds` 缩到 1-2 秒，然后用真墙钟跑** — 这样确实只有一条时间线、也能每段都看到
  到期前窗口；但 `schema` 强制 `walk_seconds >= 30`（`worldgen/schema.py:277`）且 `MANIFEST.json` 校验每个文件的
  SHA256（`schema.py:749`），改包会被 loader 直接拒绝；要绕过只能改世界包格式或关掉校验，等于改产品。
- **用 `service.shutdown()` 优雅停机** — 它顺手 `unlink` 自己绑过的 socket（`world_body_service.py:750-765`），
  正好抹掉"进程重启没重置路径"这条证据；所以子进程只在 `finally` 里释放租约，让旧 socket 留在盘上。
- **让 worker 2 复用 `world_home/run` 当 run 目录** — gateway socket 路径由 `config.run_dir` 决定
  （`world_body_service.py:143`），复用就必然要 `_bind_socket` 先 unlink 旧 socket；本批要求"新 UDS 绝对路径 +
  旧 socket 原样保留"，所以 worker 2 用一个只覆盖 `run_dir` 的委托配置（世界数据根不变）。
- **靠改账本把旧的 `blocked` 改写成 `unknown` 来"制造通过"** — 已在前一条笔记里被否决：没有模糊标记就分不清
  真拒绝与回执丢失。本批不迁移任何旧夹具，只在新夹具上做确定性注入。

## Consequences

- **收益**：邻居世界第一次有"10 条真实边 + 真服务 + 正式授权端口"的连续证据；暂停→真重启→resume 与
  "回执丢失只对账一次"两条恢复边界都有可复跑的精确用例（3 条、约 60 秒），并且旧 socket、旧数据根的字节
  前后可比。
- **代价与已知上限**：①因为回执时钟不可注入，加速跑时 journey 时钟会领先 owner 回执，段内"到期前"只能用
  只读读取表示，不能用 `advance_journey` 在到期前写账本；②worker 2 的 `run_dir` 由测试侧委托配置重定向，
  顺带把授权注册表换到新 run 目录（`world_body_gateway_api.py:169-174` 用 `config.run_dir` 找注册表），
  否则回执会是 `AUTHORIZATION_NOT_FOUND`——这是产品耦合，不是缺陷，但"跨 run 目录迁移授权"没有产品支持；
  ③只证明 World owner 层的物理链，不证明 Life 自主、6 地点内景/消费、全天节奏，也不含真模型/外网。
- **门禁**：`git diff --check` 干净；note 树与格式脚本通过（见 `docs/engineering/world-neighborhood-owner-chain-result.md`）。
