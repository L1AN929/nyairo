# Agent Note: G2 不改规则路线的同一轮双合法探针

Status: implemented

## Problem

G2 预登记要求同一冻结场景里「H 与另一件不同作品都真可执行、且 NO_ACTION 合法」，否则记
`SCENARIO_NOT_IDENTIFIABLE`。此前所有离线样本都卡在 H 占前台、另一件不可 START。规则选项文档
（`g2-rule-options-20261031.md`）给出的 A 路线依赖三件未被实核的事：本 H 能绑哪条 PAUSE 条件、
该条件有没有独立于 H 自身 CONTINUE 的生产者、以及满足后同一轮能不能真出现双合法集。
不可以用推理代替结构门实测，也不可以为凑场景改任何生产规则。

## Decision

新增 `scripts/g2_natural_dual_choice_probe.py`（零参数、离线、`http.client` 硬断、scratch 保留）。
它直接复用现成件：`history_choice_natural_v2._open_scratch` / `_advance_from_runner` /
`freeze_and_check`，与真 Supply owner、正式 runtime/runner/AG-1/AG-2/LR-2/LR-4。脚本 Decision
只做两件事：第 1 次 START 一个可执行 artifact 候选（抄本轮 prompt 里真实的 candidate_id），
第 2 次抄本轮 `situation_context.pause_condition.condition_ref` 选 PAUSE 并给
`later_item.park_current=true` + `in_minutes=5`。之后用显式 `observed_at`（+6 分钟夹具）调
`cycle.maintain_waiting`，让 LR-4 自己判 `TIME_AT_OR_AFTER` 满足；最后在同一 runtime 上调
`freeze_and_check`，核对最终请求里 H 的 RESUME、另一件的 START 与 NO_ACTION。

实测（父级首交与收尾各一次，均 exit 0；worker 自测另列）：offer 为 `PARK_UNTIL_CHOSEN_TIME`；PAUSE 后
`PAUSED` 且前台引用为空；绑定条件 `TIME_AT_OR_AFTER` PENDING → SATISFIED、
`emitted_eligibilities=1`、H 拿到 OPEN 资格、`auto_resumed=false`；结构门 2 候选 2 可执行，
`legal_decision_enums = DEFER/NO_ACTION/RESUME/START`。

输出侧按白名单收紧：预算只投影 `run_window_calls/utc_day_calls/run_window_max/utc_day_max/state`
（不再带 `recent_tokens`/账本路径/历史），setup/gate 不再透传 detail，异常只留 type 名与固定 code。
收尾仍调用 `release_runtime`/`supply.stop`，但只是**执行了关闭调用，结果未独立确认**，不宣称已 drain。

## Alternatives considered

- **继续只做静态核对**：最省事，但第三条问题（同一轮双合法）本来就是「结构门实核，不是推理」；
  静态只能证明代码路径存在，证明不了候选集与合法集在同一结构里真的同时出现，否决。
- **让脚本 Decision 手填候选或合法集/手造 OPEN**：能立刻得到绿，但那就是造场景；探针只抄本轮
  真实渲染出来的 ref 与 condition_ref，OPEN 由 LR-4 自己判，否决。
- **用 `VirtualTime` 伪装时间流逝**：v2 的虚拟时钟只驱动控制器循环，不驱动业务时钟；本探针改为
  显式 `observed_at` 夹具并在文档里标注「不是自然经过」，避免把夹具当时钟改造，否决假装快进。

## Consequences

本样本的三件已核：这型 H（本样本状态 `ARTIFACT_STEP_AVAILABLE`）的 PAUSE 只实测到 park
（body 顶替只是静态路径、本次未触发），work-head 条件因 `WORK_NO_CHANGE_CONFIRMED` 前提在本样本绑不上；
时间生产者走 LR-4 自己的 due 扫描、PAUSED host 也算 live，资格由 owner 判、不自动恢复；满足后
同一冻结结构里 RESUME H、START 另一件、NO_ACTION 同时合法。以上均为本样本限定，不推其他 NO_CHANGE
轮次、其他 H 或真实等待/控制器整批时钟。
本样本剩下的是**自然发生**：真实模型自己选有条件 PAUSE 并命名时刻。但这不等于生产里只差这一件事：
完整 G2 仍需自然 H 采集、预算内场景形成、真实影子比较与承接，均未过。Decision/内容仍是脚本替身，
时间与结构门都是离线夹具，不构成 G2 通过或可跑授权。
