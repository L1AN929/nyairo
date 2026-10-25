# Agent Note: 同 head 的后台 PAUSE 阅读，NOT_DUE 仍复查但不发资格（正式 runner 负控）

Status: implemented

## Problem

“新 head”组已经证明：真实 owner 换 head 后，正式 `AutonomousLifeRunner` 的下一个 NOT_DUE
窗口仍跑既有维护，LR-4 自己把条件判成 SATISFIED 并发出一条 OPEN 资格。但那条链只走了
“变化”一侧；同一机制的负控——owner head **没变**时，NOT_DUE 维护是否仍会真实读取 owner、
是否会把“没变化”误发成资格、是否会顺手恢复活动——没有证据。l 短日的历史同样只剩
`INCONCLUSIVE_HISTORY`，不能拿它替这组负控。缺这一侧，任何“条件只在真变化时才满足”的
说法都只是读代码的推断。

本组只回答一个问题，且必须由正式入口回答：条件 PAUSE 后 A 的 owner head 保持 baseline，
连续两个正式 NOT_DUE 窗口是否各自真实读一次 owner head、报 `checked=1 / changed=0 /
unchanged=1`、零事件、条件仍 PENDING、零资格、零认知、且不自动恢复 A。

## Decision

新增 `scripts/reading_condition_same_head_runner_smoke.py` 与
`tests/test_reading_condition_same_head_runner.py`，**复用“新 head”组已验证的正式夹具**
（`reading_condition_new_head_runner_smoke` 的阅读文本与 helper 直接 import，不修改那个模块），
在全新 `/tmp` 隔离根、真实 Supply owner、正式 `build_production_life_runtime` +
`assemble_autonomous_life` 上跑一条链，Decision/内容/阅读响应是脚本替身：

1. `one_day_life.prepare` 建真实三页阅读材料与真实私人事项文档；同一 owner 建另一件作品 B。
2. 正式 START 阅读 A → 两次 CONTINUE 读到 `EXHAUSTED`。脚本 Decision 使用本轮 offered 的
   `READING_MATERIAL_NEXT_VERSION` 条件 PAUSE：真 LR-2 释放前台，真 LR-4 绑定条件且 PENDING。
3. 脚本 Decision 正式 START B；B ACTIVE 持前台，A 仍 PAUSED，资格为空。
4. **本组不提交 A 的新版本**：owner head 保持 PAUSE 时的 baseline，版本总数不变。
5. 正式 `runner.run_due(now=T0)` 先 warm 一次既有窗口；随后 `T0+60s`、`T0+120s` 连续两个
   窗口断言 `NOT_DUE`，并读取 runner 自己记录的 `extra["waiting"]`：
   `checked=1 / changed=0 / unchanged=1 / evaluated=0`、`satisfied` 与
   `emitted_eligibilities` 为空、`errors` 为空、`llm_calls=0`、`auto_resumed=false`，
   认知替身计数每个窗口前后不变。
6. 一个**窄 spy**只包 `cycle.artifact_port.read_head`（内部仍是真 `LifeArtifactPort`，
   NOT_DUE 不 begin round 所以每次都是真 socket 读）：每个窗口恰好一次针对 A 的真实 head
   读取，读回的 `version_id` 就是 baseline。spy 不改写返回值——读到 `None` 会由维护报
   `OWNER_HEAD_UNREADABLE`（`errors` 非空），**不会**被当成 `unchanged`。
7. 每个窗口之后复核：条件仍 PENDING、资格存储整体为空（没有 OPEN，也没有
   `RESUME_ELIGIBLE` 来源）、A 仍 PAUSED、B 仍 ACTIVE 且是唯一前台；最后再读一次
   owner head 与版本历史，确认两者都与 baseline 一致。

## 三组分工与验收边界

三组互相扣合、互不替代：

- [新 head 正控](2026-10-25-reading-condition-new-head-runner.md)：head 真变 → SATISFIED 并发出 OPEN，
  且前台冲突不抢占、释放后才回到候选；
- 本组（同 head 负控）：head 未变 → 每窗仍真实读一次，`unchanged=1`，零事件、零资格；
- [读失败负控](2026-10-25-reading-condition-read-failure-runner.md)：head 读不到 → 明确
  `OWNER_HEAD_UNREADABLE`，既不当作没变，也不当作已变。

本组单独回答“没变化时会不会误发资格”，正控不能替代它：正控只证明变化路径通了，无法证明
维护在没有变化时不会凭旧值或空值发资格。反过来，本组全绿也不能证明变化路径可达。

本组验的是 **Condition 仍 PENDING / 零 OPEN**，不是“Condition 判成 SATISFIED 但没有资格”。
若把这两个状态混在一起，就会误以为“复查过”等于“条件已满足”。

坚持正式 `AutonomousLifeRunner.run_due` 而不是 `cycle.maintain_waiting`：只有正式窗口能证明
维护接在既有 due 窗口上、NOT_DUE 时仍执行、且不花认知。手造 condition/eligibility 则会把结论
变成对手造数据的断言。`read_head` 返回 `None` 也**不**计入 unchanged——生产维护会报
`OWNER_HEAD_UNREADABLE`，这是读失败组的断言，本组要求 `errors == []` 与 spy 读到非空
`version_id` 同时成立。

父级独立复跑本组：`tests.test_reading_condition_same_head_runner` 6 例 4.259 秒 OK（A_EXIT=0），
`scripts/reading_condition_same_head_runner_smoke.py` 输出 `SMOKE_OK`（B_EXIT=0），
日志 `20261025-reading-same-head-parent-ab.log`。

边界：Decision/内容/阅读响应是脚本替身；只证隔离机制里的候选层与维护计数，不证真模型自然
选回，也不能补造 l 当时是否复查或换 head 的历史——l 仍是 `INCONCLUSIVE_HISTORY`。

## Alternatives considered

**直接调 `cycle.maintain_waiting`。** 能让 `checked/unchanged` 出现，但绕过了本组真正要验的
接缝：维护是否接在正式 runner 的**既有窗口**上、窗口关闭时是否仍执行、以及是否花认知。
因此只用 `AutonomousLifeRunner.run_due` 并在 NOT_DUE 分支读取它自己的记录。

**把读不到 head（`None`）也当成 unchanged。** 看起来能让负控更容易绿，但语义是错的：
`_owner_head` 对不可读 owner 返回 `None`，维护会记 `OWNER_HEAD_UNREADABLE` 而不是
“没变化”。本组坚持 `errors == []` 且 spy 捕获非空 `version_id`，两者互相不能顶替。

**手造 condition / eligibility，或直接改 JSON 让“没变化”看起来被复查。** 会把结论变成对
手造数据的断言。本组只认真实的锁定条件与真 LR-4 记录。

**和新 head 组合并一单。** 此前三组合单已经超时 124 且零产物；本组只做同 head 这一侧，
保留独立的有界证据，读失败组另单处理。

## Consequences

- 本组证明了：owner head 未变时，正式 runner 的连续两个 NOT_DUE 窗口仍各自真实读取一次
  owner head、报告 unchanged 而不产生变更事件；条件保持 PENDING、零资格、零认知、零自动恢复，
  A 仍 PAUSED、B 仍 ACTIVE 且唯一前台，owner head 与版本总数全程不变。
- **没有**证明：owner 读取失败（不可读/拒读/撤权）时的分类；前台空后 A 的自然返回；真模型
  自然做出这些选择；G1；24h；l 历史的具体因果（l 仍是 INCONCLUSIVE）；跨进程重启。
- Decision/内容/阅读响应都是脚本替身；阅读材料、私人事项与作品 B 是真实 Supply owner 状态，
  种子文本是 operator fixture。时间只用注入的 Life 时刻保持既有窗口关闭，不睡墙钟、不改
  生产窗口配置、不碰任何旧实例或产品预算。
- 未改生产代码、既有测试、条件词表或字段；新增文件只有本 note、上述 smoke 与测试。
- 机制来源：[显式条件 PAUSE](../feature/2026-10-24-paused-explicit-resume-condition.md)、
  [PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)、
  [有效搁置候选供给](../feature/2026-10-24-eligible-parked-candidate-supply.md)。
