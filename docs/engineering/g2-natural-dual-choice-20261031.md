# G2 不改规则路线：三条剩余接缝（2026-10-31）

状态：**离线探针已实跑、exit 0；零产品请求，185/300 不变，未改任何生产规则。**
Decision/内容是脚本替身（走正式 AG-1 校验与 AG-2 采纳），时钟是显式夹具；真 Supply owner、
正式 runtime/runner、现成 `freeze_and_check` 结构门、`http.client` 硬断。
探针：`scripts/g2_natural_dual_choice_probe.py`（零参数）；
父级命令同任务书：`wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g2_natural_dual_choice_probe.py'`。

## Q1 本 H 的 PAUSE 实际可绑条件：PASS（带具名限定）

H 以 G2 A 离线样本的实际类型取：真 writer 产生的普通作品续写（练习甲），ACTIVE 且本步可执行。
`pause_condition_view` 的单一 offer 优先级是 readiness 依赖 > body > park
（`autonomous_life_cycle.py:5965-5984`、`6288-6300`）：

- **work-head 依赖绑不上（本样本）**：`basis_from_readiness` 只在 readiness `available=False` 且
  `WORK_NO_CHANGE_CONFIRMED`（同一 revision 上 writer 报过无变化）时生成；本次 H 处于
  `ARTIFACT_STEP_AVAILABLE`，所以这件 H 在这个状态下拿不到该条件。不因此推出以后的 NO_CHANGE 轮次
  或其他 H 也永远绑不上。
- 本次实测 offer 是 **PARK_UNTIL_CHOSEN_TIME**；代码里若身体 LIMITED 且有具名 burden，
  **BODY_CAPACITY_RESTED** 会顶替 park——这只是本样本扫描中的静态路径，本次未触发。
- 三层区分：view 给出 `available=true/condition_ref`；Decision 必须把该 ref 原样抄进
  `reevaluation_hint_ref`（AG-1 对 `known_waiting_basis_ref` 校验），park 还须在同一条 Decision
  里给 `later_item.park_current=true` + 时刻；owner 在 PAUSE 产生的 revision 上
  `bind_pause_condition`。本样本里不写 ref = 无条件 PAUSE，本次不建条件、不出资格。
- 实测：`source=PARK_UNTIL_CHOSEN_TIME`，PAUSE 后 `status=PAUSED`、`foreground_activity_ref=null`，
  绑定一条 `TIME_AT_OR_AFTER`/PENDING 条件。

## Q2 独立于 H 自身 CONTINUE 的生产者：PASS（时间，夹具）/ 静态可达（身体，未触发）/ NOT_FOUND（外部 writer）

- **时间**：`maintain_waiting` 每 tick 调 LR-4 `scan_due_agenda`；PAUSE-bound 条件在
  `condition_host_is_live` 里以 `PAUSED@bind revision` 为 live host，`TIME_AT_OR_AFTER` 由 owner
  评估（`activity_waiting_agenda.py:200-230`、`3209-3240`）。实测 PENDING→SATISFIED、
  `emitted_eligibilities=1`、H 获得 OPEN 资格、`auto_resumed=false`。
  限定：时刻写在同一批暂停它的 Decision 里（无默认 timer）；本次 `observed_at` 是本 probe 传的
  夹具，**不代表真实等待已发生**，也不代表控制器整批时钟接线已经验过。
- **身体**：`_pending_wait_conditions` 含 body 条件，`_fresh_body_easing_read` 每 tick 真读 body，
  只在真的 easing 时发 `BODY_CAPACITY_EASED` 给 LR-4。**仅静态可达**；本次未触发身体分支，
  自然身体 easing 未验。
- **外部 writer**：因本样本里 work-head 条件绑不上（Q1），对这件普通续写 H 这一状态不存在——
  不是“没有 writer”，是这个条件在本样本根本绑不到这种 H。有限 `NOT_FOUND_IN_SCOPE`。

## Q3 同一轮双合法：PASS（结构门实核）

现成 `history_choice_natural_v2.freeze_and_check` 在同一冻结结构里构造正式 AG-1 请求：
2 个候选、2 个可执行——H 的 **RESUME**、另一件（练习乙）的 **START**，
`legal_decision_enums = DEFER / NO_ACTION / RESUME / START`。候选集、上下文、合法集全部由真实
runtime 与真实 owner 产出，未手填、未手造 OPEN、未直接调 resolver。
这是**同轮正式结构门**的结果：它不等于真实网络最终 `build_prompt` 已送达，也不等于完整 G2 通过。

## 最窄停点

本样本里剩下的依赖是**自然发生**：真实模型自己在 ACTIVE 的 H 上选有条件 PAUSE 并命名时刻
（或身体 LIMITED 时选 body 条件）；其后的条件满足、资格 OPEN、同一轮双合法都是既有正式链，
本单已离线走通。这只说明本样本如此，不等于生产里只差这一件事——完整 G2 仍需要真实自然 H 采集、
预算内场景形成、真实影子比较与承接，全部未过。
不宣称 G2 已可跑、不申请真实预算、不动规则；外部 writer 那条在本样本状态下不走。

父级最终复跑同一探针 exit 0，9 checks 全真，新 scratch `/tmp/g2-dual-choice-e8_h7e_2` 保留；
预算输出仅五个调用数/上限/state 字段，无 recent_tokens，关闭调用返回 ok、`result_verified=false`。
本功能父级共运行两次（首交一次、收尾一次），不含 worker 自测；不扩大为所有失败路径均已注入验证。

探针结束时会调用 `release_runtime` / `supply.stop` 做关闭；**本次只执行了关闭调用，结果未独立
确认**，不宣称已正常 drain 或服务已确定停止。
