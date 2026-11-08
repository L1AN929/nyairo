# Agent Note: World 到达已落盘时，旅程只补写账本，不再误报 ARRIVAL_REALIZATION_FAILED

Status: implemented

## Problem

idle2 唯一真实批（`run:fastday-idle2-20261106`）的同一时刻留下三份互相矛盾的证据：

* `world_travel_delivery.json` 的 `travel-3dbcc4e...` 已是 `ARRIVED`，`arrived_at`
  `2026-11-06T00:18:34Z`；
* `world_state.json` 的位置已在 `street_1`（revision 3）；
* `world_journey.json` 的活动旅程却是 `blocked`，`error.reason_code` 为
  `ARRIVAL_REALIZATION_FAILED`，`updated_at` 同为 `00:18:34Z`。

`chiyo_bundle/world_journey.py` 自己的「故障窗口③」写明：到达结算/段确认前，若同
`execution_id` 的 travel 记录已 `ARRIVED` 且位置在段终点，只能**补写账本**，不再移动。
现行 `_reconcile` 却只在 realizer 返回 `arrived` 时重读证据；其它返回值一律走
`else`，直接写终态 `blocked`。

具名根因（严格复现，不是报告转述）：到达可能先被 World owner 的另一条结算路径落盘
（owner tick 的 `_settle_arrivals`、观察缝 `world_living_skeleton.observe` 的 lazy
realize、`world_move_tool`、或并发的另一个推进器）。此时本进程的
`realize_due_travel` 会看到 `active=None` 并返回 `{"status": "none"}`；若它读到的是
本段在途记录之后、落盘之前的快照，`_reconcile` 就把这一次 `none` 当成 `blocked`，
默认原因正是 `ARRIVAL_REALIZATION_FAILED`。修前反例在隔离 scratch 里用真实 world 包、
真实 `apply_move_to`、真实旅程引擎和真实 realizer 复现：2/5 用例红，`blocked` /
`ARRIVAL_REALIZATION_FAILED`，而 travel 已 `ARRIVED`、位置已在 `street_1`，与 idle2
的三份盘上证据同形。原批具体是哪一次 `none` 还是 `failed` 未留存，标 UNKNOWN；修复
不依赖这个二选一，二者都按落盘证据重新对账。

## Decision

1. `_reconcile` 在 realizer 返回 `in_progress` / `disabled` / `unavailable` 以外的任何
   结果时，**不再相信返回值**，而是重读同一 `execution_id` 的 travel 记录和当前位置。
2. 只有该记录确实 `ARRIVED` 且位置在段终点，才落入既有的 `arrived` 分支（故障窗口③，
   由 `_step` 补写段确认账本）；若到达由别的路径先写，这就是同一次到达的补账，不产生
   新的位移、travel 票或回执。
3. 记录未 `ARRIVED`（含 `none`、`failed`、`rejected`）时维持原判：写 `blocked`，原因
   沿用 realizer 的 `reason_code`（缺省 `ARRIVAL_REALIZATION_FAILED`）。**位置相同本身
   不是到达确证**，不能用来伪成功。
4. 不改 `realize_due_travel` 的返回值契约、不改 `blocked` 是终态的政策、不自动解冻
   已 blocked 的旅程、不重发 MOVE、不新增重试/恢复协议、不新增状态或持久格式。

## Alternatives considered

* **把 realizer 的 `failed`/`none` 改成非终态 `unknown`，等下一次推进再对账**：最强
  理由是瞬时失败不应永久锁死旅程。否决：这次到达已经落盘，下一次推进时 travel 已
  `ARRIVED`、没有在途记录，`_reconcile` 会直接走 `ARRIVED` 分支；但若只把终态改
  `unknown` 而不重读证据，本段仍要靠后续调用才可能被确认，且 `none` 的语义会被误当
  成“未知提交”。它也没有回答“已写盘算不算到达”这个核心问题，等于把终态政策改了一半。
* **让 `realize_due_travel` 在找不到在途记录时自己回读 `ARRIVED` 记录并返回
  `arrived`**：最强理由是修复集中在一个函数、所有调用者受益。否决：该函数对
  “没有在途 travel”的现有契约就是 `status: none`（`world_move_tool` 等调用者依赖它
  判断“没有计时移动”）；把历史 `ARRIVED` 记录重新报成一次新到达会改变所有调用者的
  语义，仍不如让旅程按 owner 证据对账直接。
* **在 `blocked` 时直接按 World 位置补完成 Activity**：最强理由是引擎故障窗口③的
  文字确实允许“只补写账本”。否决：`blocked` 是已定案的终态，解冻它属于旅程终态与
  部分写盘恢复合同，必须单独授权；本单只修“正常到达被误判失败”的接缝，不碰已
  blocked 的恢复。

## Consequences

* 正常到达接缝修好：owner 已写 `ARRIVED`、旅程这一次只读到 `none`/`failed` 时，段被
  确认、旅程完成，不再写 `blocked`/`ARRIVAL_REALIZATION_FAILED`。
* 边界保持不变：没有 `ARRIVED` 记录时（真实落盘前拒绝，或位置被写但 travel 未到达）
  仍不冒成功；`blocked` 仍是终态，已 blocked 的旧旅程**不会**被本单解冻或补完成。
* 多段旅程仍逐段提交：补写上一段后，下一段照旧经 `submit_port` 铸新 execution id，
  不瞬移、不跳过、不复用票。
* **首交与本次收尾要分开看**：首交的 5 个聚合用例里，提交端是测试替身
  `_RealOwnerSubmitPort`，它直接调 `world_living_skeleton.apply_move_to`，**没有**走
  一次性许可 / `gateway.sock` / registry；因此首交报告里“多段逐段许可已验”的措辞
  不成立，已收窄为“多段逐段**提交**已验，授权链未验”。
* **本次正式收尾**（同一模块，真实 gateway 链）：测试在各自 scratch 内起真实 World
  服务线程（真 `AF_UNIX` `gateway.sock` + 真 registry），每个新段都经正式
  `intent gate -> fresh read -> 一次性授权 -> gateway 消费 -> Resolver` 提交。盘上
  `run/action_authorizations.json` 每段恰好一条 `CONSUMED` 记录，`execution_id` 与
  该段一致；单段 1 条、多段 2 条且互不相同。重复确认不新增授权记录、不新增
  `run/action_lifecycle.jsonl` 回执、不改 world revision / travel 数量 / 三份状态
  文件字节。负控（落盘前失败、位置已改但 travel 未 `ARRIVED`）仍走 `blocked`，不冒
  成功。票证证据取自盘上 registry 与生命周期回执，不拿端口计数器自报等价。
* 时钟分域：一次性许可的签发/消费/过期是真实墙钟；旅程与 travel 的 `now` 由真实
  travel 记录声明，测试只读该记录的 `expected_arrival_at` 选取结算时刻，不把逻辑
  测试钟塞进生产授权链，也不为时钟错位改生产或旧测试。
* 验证：`tests/test_world_arrival_realization_boundary.py` 5 个聚合用例（正常单段到达、
  owner 已写到达、重复确认幂等、落盘前/落盘后无确证、多段逐段正式提交），用真实
  world 包、真实 World 服务/gateway/一次性授权、真实旅程引擎与真实 realizer；固定命令
  `python -B -m unittest tests.test_world_arrival_realization_boundary` 修前 exit=1（2 红，
  首交 direct 提交端下复现）→ 首交 exit=0 → 本次正式链连续两轮 exit=0（各 5 例、0 skip）。
* 未证：真实模型自然采用；idle2 原异常具体是 `none` 还是 `failed`（UNKNOWN）；已
  `blocked` 旅程的恢复（未授权，终态语义未动）；多段长旅程的全程；相邻
  `test_world_journey` 的两例红与本次改动是否无关（首交只声称做过单文件回退复跑，
  未留操作细节，本单未重放，不能据此声称已证明无关）。scratch 全部保留在 `/tmp`，
  未提交/推送。

关联：[idle2 无第二 Life step](2026-11-06-world-move-no-further-step.md)、
[World 旅程逐段授权](2026-10-23-world-journey-segment-authorization.md)。
