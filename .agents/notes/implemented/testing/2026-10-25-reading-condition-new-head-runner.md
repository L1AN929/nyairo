# Agent Note: 阅读材料换新 head 后，A 只回到 RESUME_ELIGIBLE 候选（正式 runner NOT_DUE 维护）

Status: implemented

## Problem

2026-10-25 的 l 短日里，阅读 Activity 在读完当前版本后显式条件 PAUSE、条件一直是 PENDING，
后来有第二件（BODY）Activity 占用前台；那一轮只读复盘只能得出 `INCONCLUSIVE_HISTORY`——
既没有“条件由 owner 新 head 满足”的现场记录，也没有“前台空后 A 回到候选”的证据。把这些
缺口留给下一条真机短日，等于让一次昂贵的自然运行去赌机制接线；而把三组机制塞进一个长
worker 的合单已经超时 124 且没有任何产物（stderr 另有 `claude-code:unrecognized_model`）。

本组只回答一个问题，且必须由**正式入口**回答：阅读材料换新 head 之后，正式
`AutonomousLifeRunner` 的**下一个 NOT_DUE 窗口**会不会仍跑既有维护、让 LR-4 自己把条件判成
SATISFIED 并发出 OPEN 资格；在另一件 Activity 持前台时 A 的 RESUME 是否真的不可执行；前台
释放后 A 是否以 `RESUME_ELIGIBLE` 回到候选（而不是被自动恢复）。

## Decision

新增 `scripts/reading_condition_new_head_runner_smoke.py` 与
`tests/test_reading_condition_new_head_runner.py`，在全新 `/tmp` 隔离根、真实 Supply owner、
正式 `build_production_life_runtime` + `assemble_autonomous_life` 上跑一条链，Decision/内容/
阅读响应是脚本替身：

1. `one_day_life.prepare` 建真实阅读材料（三页 `%%PAGE%%`）与真实私人事项文档；再用同一
   owner 建另一件作品 B。**不 seed 作品**，第一次 START 就是真实阅读。
2. 正式 START 阅读 A → 两次 CONTINUE 读到 `EXHAUSTED`（真实停点）。脚本 Decision 使用本轮
   offered 的暂停条件（`source == READING_MATERIAL_NEXT_VERSION`）选择 PAUSE；真 LR-2 释放
   前台，真 LR-4 绑定条件并保持 PENDING。
3. 脚本 Decision 正式 START B（不同 Artifact）；B ACTIVE，A 仍 PAUSED。
4. 先跑一次正式 `runner.run_due(now=T0)` 让既有窗口落一次 `last_check_at`；随后真 Supply
   owner 正式 `commit_version` 换 A 的 head，B 仍 ACTIVE。
5. 正式 `runner.run_due(now=T0+60s)`：本窗 **NOT_DUE**，维护仍执行；捕获 runner 记录里的
   `extra["waiting"]`，断言 `state=OK`、`changed=1`、`satisfied` 恰好是绑定的那一个条件、
   `emitted_eligibilities` 恰好一条、`llm_calls=0`、`auto_resumed=false`，且认知替身调用
   计数前后不变。条件 SATISFIED、恰好一条 OPEN 资格属于 A，A 仍 PAUSED。
6. B 执行的一轮经正式 `cycle.evaluate_once` 候选面：断言 offered 中**没有**可执行的 A RESUME
   候选，Decision 选择 CONTINUE 让 B 真实前进一步；A/资格不消费、B 不被打断。
7. 脚本 Decision 合法 PAUSE B 释放前台；下一个合法 due 轮经正式 runner 候选面读到 A 的
   `RESUME_ACTIVITY` / `RESUME_ELIGIBLE` 候选（目标指回 A）。脚本 Decision 选 NO_ACTION，
   A 保持 PAUSED、资格保持 OPEN——**本组答案是回到候选，不是自动恢复**。

证据由 `tests/test_reading_condition_new_head_runner.py` 一次调用 `run_smoke()` 并逐条断言本组
要证明的事实；它不继承旧 fixture TestClass，也不顺带跑整套旧测试。

## 三组分工与两个状态的区分

三组是同一机制的三侧，**成功与失败不能互相代替**：

- [同 head 负控](2026-10-25-reading-condition-same-head-runner.md)：owner head 未变时，窗口仍真实读一次
  并报 `unchanged=1`，零事件；
- [读失败负控](2026-10-25-reading-condition-read-failure-runner.md)：head 读不到时报
  `OWNER_HEAD_UNREADABLE`，不猜“没变”，也不猜“已变”；
- 本组（正控）：head 真变后条件 SATISFIED 并发出恰好一条 OPEN。

只有三组同时成立，才能说“条件只在可观测的真实变化后才满足”。任何一组绿都不能替代另外两组。

两个容易混的状态必须分开断言：

- **Condition SATISFIED** 是维护对绑定条件的判定结果；
- **eligibility OPEN** 是随后由 owner 写入的一条资格记录，有自己的 id 与来源。
本组要求二者各自恰好一次。条件满足不等于资格已发，资格存在也不能倒推条件判定正确。

坚持正式 `AutonomousLifeRunner.run_due` 的 NOT_DUE，而不是 `cycle.maintain_waiting` 或手造 OPEN：
维护是否真的接在既有窗口上、窗口关闭时是否仍执行、是否花认知，只有走正式 entry 才看得见；
helper 与手造资格会把这些接缝全部跳过。B 持前台时 A 的不可执行也不是直接在测试里调 LR-4/LR-2
造冲突，而是看正式 `evaluate_once` 的 offered 候选里没有可执行 RESUME；B 合法 PAUSE 释放前台后
A 才以 `RESUME_ELIGIBLE` 回到候选，Decision 选 NO_ACTION 也不会自动恢复——**回到候选不等于
自动恢复**。

## 父级独立验收

父级复跑本组：`tests.test_reading_condition_new_head_runner` 5 例 4.915 秒 OK（A_EXIT=0），
`scripts/reading_condition_new_head_runner_smoke.py` 输出 `SMOKE_OK`（B_EXIT=0），
日志 `20261025-reading-new-head-parent-ab.log`。worker 自述不替代这次复跑。

## Alternatives considered

**直接用 `cycle.maintain_waiting`。** 最省事，也能让条件 SATISFIED，但它绕过了本组真正要验的
接缝：维护是否接在正式 runner 的既有窗口上、NOT_DUE 时是否仍执行、且不花认知。因此只用
`AutonomousLifeRunner.run_due` 并在 NOT_DUE 分支读取它自己的记录。

**在 B 持前台时直接调 LR-4/LR-2 的补偿入口做负控。** 既有 smoke 用过这种写法，但那种调用是
测试自己构造的冲突，不是候选面真的没有可执行 RESUME。本组改看正式 `evaluate_once` 的 offered
候选：没有 RESUME 候选、Decision 也不能 RESUME，才说明前台冲突真的挡住了这条路径。

**手造 OPEN 资格或手工改 eligibility JSON。** 能让候选立刻出现，但把结论变成对手造数据的断言。
本组只认真实 owner 换 head → 真 LR-4 → 真资格记录这条链。

**把三组（新 head / 同 head / 读失败）合成一单一次跑。** 已经发生过一次超时 124 且零产物。
本组只做新 head 组；同 head 与读失败组留待各自有界单，避免一个失败吞掉全部证据。

## Consequences

- 本组证明了：阅读条件由真实 owner 新 head 满足、正式 runner 的 NOT_DUE 窗口仍维护且不花认知、
  条件 SATISFIED 与一条 OPEN 资格由 LR-4 自己写入、B 持前台时 A 的 RESUME 不可执行、前台释放后
  A 以 `RESUME_ELIGIBLE` 回到候选而 NO_ACTION 保持其 OPEN。
- **没有**证明：真模型自然做出这些选择；G1；24h；l 历史的具体因果（l 仍是 INCONCLUSIVE）；
  同 head 保持 PENDING、读失败明确报错这两组；也没有证明其它来源撤权或跨进程重启。
- Decision/内容/阅读响应都是脚本替身；阅读材料、私人事项、作品 B 与 A 的新 head 是真实 Supply
  owner 状态，种子文本是 operator fixture。时间只用注入的 Life 时刻让既有窗口到期，不睡墙钟、
  不改生产窗口配置、不碰任何旧实例或产品预算。
- 未改生产代码、既有测试、条件词表或字段；新增文件只有本 note、上述 smoke 与测试。
- 机制来源：[显式条件 PAUSE](../feature/2026-10-24-paused-explicit-resume-condition.md)、
  [PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)、
  [有效搁置候选供给](../feature/2026-10-24-eligible-parked-candidate-supply.md)。
