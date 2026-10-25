# Agent Note: owner head 读失败时，正式 runner 的 NOT_DUE 维护不猜“没变/已变”

Status: implemented

## Problem

同 head 负控已证明：owner head 保持 baseline 时，正式 `AutonomousLifeRunner` 的 NOT_DUE
窗口仍真实复查、报 `unchanged=1`、零事件、零资格。但它只覆盖“读得到且没变”。若 owner
读不到 head（不可用/拒读/无 head），维护会不会把 `None` 或异常吞成 `unchanged`（从而让
条件永远 PENDING 且没有任何错误痕迹），或反过来误判 `changed` 并发资格，此前没有正式入口
的证据。l 短日的历史是 `INCONCLUSIVE_HISTORY`，不能拿来替代这组负控。

本组只回答一个问题，且必须由正式入口回答：条件 PAUSE 后 A 的 owner head 读取失败时，
两个正式 NOT_DUE 窗口是否各自报 `checked=1 / changed=0 / unchanged=0`、`errors` 明确含
`OWNER_HEAD_UNREADABLE`、零事件、零资格、零认知、不自动恢复，且故障撤除后真实读回 baseline。

## Decision

新增 `scripts/reading_condition_read_failure_runner_smoke.py` 与
`tests/test_reading_condition_read_failure_runner.py`，**复用“新 head / 同 head”组已验证的
正式夹具**（`reading_condition_new_head_runner_smoke` 的阅读文本与 helper 直接 import，
不修改那个模块），在全新 `/tmp` 隔离根、真实 Supply owner、正式
`build_production_life_runtime` + `assemble_autonomous_life` 上跑一条链，
Decision/内容/阅读响应是脚本替身：

1. `one_day_life.prepare` 建真实三页阅读材料与真实私人事项文档；同一 owner 建另一件作品 B。
2. 正式 START 阅读 A → 两次 CONTINUE 读到 `EXHAUSTED`；脚本 Decision 使用本轮 offered 的
   `READING_MATERIAL_NEXT_VERSION` 条件 PAUSE：真 LR-2 释放前台，真 LR-4 绑定条件且 PENDING。
3. 脚本 Decision 正式 START B；B ACTIVE 持前台，A 仍 PAUSED，资格为空。
4. 正式 `runner.run_due(now=T0)` 先 warm 一次既有窗口。
5. **窄故障注入**（`_OwnerHeadFault`，只对 A 的 artifact 生效，其它请求透传真
   `LifeArtifactPort`，不替换条件/资格 store）：
   - `T0+60s`，`none` 形态：`read_head` 返回 `None`；生产 `_owner_head` 会退到
     `resume_point`，真实不可读 owner 在那里同样答不出，因此该回退也抛真
     `ArtifactOwnerRefused`（真实端口拒读用的同一个异常类型）；
   - `T0+120s`，`oserror` 形态：`read_head` 抛 `OSError`（隔离注入，不是真实撤权、
     不是停服务、不是跨备份）。
6. 两个窗口各自断言：`status=NOT_DUE`、`checked=1 / changed=0 / unchanged=0 /
   evaluated=0`、`satisfied` 与 `emitted_eligibilities` 为空、
   `errors` 含 `OWNER_HEAD_UNREADABLE:<artifact>`、`llm_calls=0`、`auto_resumed=false`、
   认知替身计数不增；条件仍 PENDING、资格存储整体为空、A 仍 PAUSED、B 仍 ACTIVE 且唯一前台。
   外层 `waiting["state"]` 仍报 `OK`——本组把它和 `errors` 一起记录，**不**把外层 OK 读成
   “没有错误”；也不在手造 fixture 里补伪 errors。
7. 恢复负控（`T0+180s`）：换回真 owner 端口，一个只读透传 spy 记录读取：
   `errors=[]`、`checked=1 / unchanged=1 / changed=0 / evaluated=0`、条件仍 PENDING、
   无资格、A/B 状态与前台不变；读回的 `version_id` 等于 baseline，且版本总数不变。

## 三组分工与失败语义

三组各自守一侧，任何一组绿都不能代替另外两组：

- [新 head 正控](2026-10-25-reading-condition-new-head-runner.md)：head 真变 → SATISFIED 并发出 OPEN；
- [同 head 负控](2026-10-25-reading-condition-same-head-runner.md)：head 未变 → `unchanged=1`，零事件；
- 本组（读失败负控）：head 读不到 → `OWNER_HEAD_UNREADABLE`，`changed=0 / unchanged=0`。

本组不可被同 head 组替代的关键点：读失败的语义不是“没变化”。生产 `_owner_head` 对不可读
owner 返回 `None` 而后退到 `resume_point`；如果把这个 `None` 或异常吞成 `unchanged`，条件会
永远 PENDING 且没有任何错误痕迹；如果误判成 `changed`，则会发出没有依据的资格。两种错法都
被本组的两条注入形态挡住。

**外层 `state=OK` 不等于没有错误。** 读失败时维护外层仍可能报 OK，必须同时看 `errors` 才
能判断；本组要求 `state=OK` 与 `errors` 含 `OWNER_HEAD_UNREADABLE` 并存，且 `unchanged=0`。
这是既有字段语义，本单不改合同。

**Condition PENDING / SATISFIED** 是维护对条件的判定，**eligibility OPEN** 是随后写入的资格
记录；本组要求前者保持 PENDING、后者整体为空，两者都不因故障被改写。

父级独立复跑本组：`tests.test_reading_condition_read_failure_runner` 8 例 4.592 秒 OK
（A_EXIT=0），`scripts/reading_condition_read_failure_runner_smoke.py` 输出 `SMOKE_OK`
（B_EXIT=0），日志 `20261025-reading-read-failure-parent-ab.log`。

边界：两种故障都是测试注入——`none` 形态复用真实拒读的异常类型，`oserror` 形态是隔离注入，
**不等于**真实 owner 撤权、断服或跨备份恢复；只证隔离机制里的分类与扣留，不证真模型自然
行为、G1、24h，也不能补造 l 的历史（l 仍 `INCONCLUSIVE_HISTORY`）。

## Alternatives considered

**只用 `None` 覆盖，不包 `resume_point`。** 生产 `_owner_head` 对 `read_head` 返回 `None`
会退到 `resume_point`；若回退透传到仍可读的真 owner，就会读回真实 head 并报 `unchanged`，
这模拟的不是“owner 读不到”，而是“同一 owner 两个读口答案互相矛盾”。真实拒读时两个读口
一起失败，所以 `none` 形态一并注入回退读口；`oserror` 形态只碰 `read_head`。

**把 `None` 当成 `unchanged` 或手工写 errors。** 都会把结论变成对注入数据的断言。
本组要求生产维护自己产出 `OWNER_HEAD_UNREADABLE`，fixture 不补伪 errors。

**直接把整个端口替换成假 store，或手造 condition/eligibility。** 会绕开真 LR-4 与真
Supply owner，证明力归零。本组只包 A 的 head 读取方法。

**在测试里把外层 `state=OK` 当作通过条件。** 读失败时外层仍可能报 OK；只有 `errors`
才能区分。本组两者同时断言，且要求 `unchanged=0`、`changed=0`。

## Consequences

- 已证明：两种 owner head 读失败形态下，正式 NOT_DUE 维护都不把失败计成 `unchanged` 或
  `changed`，而是报 `OWNER_HEAD_UNREADABLE`；无事件、无 SATISFIED、无资格、零认知、
  无自动恢复，A 仍 PAUSED、B 仍 ACTIVE 且唯一前台；故障撤除后下一 NOT_DUE 窗口真实读回
  baseline，`errors` 空、`unchanged=1`，owner head 与版本数未被注入改动。
- **没有**证明：真实权限撤销/跨备份恢复下的行为；真实 owner 不可用（断服）时的分类；
  真模型自然做出这些选择；G1；24h；l 历史的因果。
- Decision/内容/阅读响应是脚本替身；阅读材料、私人事项与作品 B 是真实 Supply owner 状态，
  种子文本是 operator fixture。时间只用注入的 Life 时刻保持既有窗口关闭，不睡墙钟，
  不改生产窗口配置、不碰任何旧实例或产品预算。
- 未改生产代码、既有测试、条件词表或字段；新增文件只有本 note、上述 smoke 与测试。
- 机制来源：[显式条件 PAUSE](../feature/2026-10-24-paused-explicit-resume-condition.md)、
  [PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)。
