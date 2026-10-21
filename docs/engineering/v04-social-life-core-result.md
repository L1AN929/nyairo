# v0.4 阶段 B 结果（修订版）：来访与共处后续 —— Decision 先，交接在 adoption 内

Status: implemented；父级修正验收时间轴与最新阅读位置取值后，WSL 新增14项通过，独立正常入口链9项通过（真实模型另计）。

本轮针对父级四条缺陷返修。写集：`life_co_presence.py`、`life_social_contacts.py`（新）、
`autonomous_life_assembly.py`、`autonomous_life_cycle.py`、`agency_cognition_adapter.py`、
`life_integration_ag2.py`（窄契约）、`agency_decision_ag1.py`（窄契约）、
`life_step_readiness.py`（必要 Life 辅助：指纹白名单）、`tests/test_v04_social_life_core.py`、
本文件与 own note。A 的 `life_local_contact.py`、`life_sharing_service.py`、`phone/**`、
`MANIFEST.json`、两个 `fix_*_world_move.py` 未改。

## 1. 缺陷 1：决定优先，拒绝/稍后/失败零变化

顺序（`FiniteCoPresence.begin_from_decision`）：

1. `consider()`（外主体/外 home/过期/operator pause/ATOMIC/预算；空闲 ACCEPT）；
2. **一次** AG-1 Decision（预算门 + 发送前扣账，见 §2）；此时**没有任何 Activity 被碰过**；
3. Decision=START 时 AG-2 采纳；**交接是 adoption 自己的第一条命令**：
   `life_integration_ag2._handoff_foreground()` 用 LR-4 `enter_waiting_with_condition(
   WAITING + EVENT_MATCH(end) + release_foreground=True, condition_expires_at=…)` 把当前前台
   交出去（对 owner 传入的 revision 做 CAS、拒绝 ATOMIC、拒绝非 ACTIVE），之后才 `start_activity`，
   且用交接后的最新 revision 做 START 的 CAS（不覆盖 Decision snapshot、不清 foreground）；
4. 交接成功而 START 失败 → `ForegroundHandoffStranded` 携带交接事实 → 协议用 LR-4 自己的
   `evaluate_event` 释放该条件（具名、可恢复），落 `UNKNOWN` 并保留 `PARKED_NO_JOINT` 供 end 对账。

窄契约（唯一放行点，均以 `handoff_spec` 存在为前提）：

* AG-1：仅 `RESPOND_USER_REQUEST` + `SOURCE_USER_REQUEST` + target refs 以 `co_presence:` 起头
  + 存在 ACTIVE 前台 + 非 ATOMIC 时，START 合法；其余规则不变；
* AG-2：`validate_decision_for_adoption(..., allow_foreground_handoff=bool(handoff_spec))`
  只对带交接的这次采纳放开 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`；freshness 其余检查、
  CAS、operation key、UNKNOWN 语义均未改。

实测证据（`test_a_refused_invitation_never_touches_the_running_activity`）：阅读 ACTIVE 时
NO_ACTION / DEFER / 模型抛异常三条邀请之后，逐一断言
`status/revision/checkpoint_ref/resume_condition_ref/foreground` **完全相等**、
`co_presence.json` 不存在、`read_session()["state"]=="NONE"`、无 `FINITE_CO_PRESENCE` Activity；
DEFER 还断言下一 tick `processed==[]`、`llm_calls==0`；重开实例后 `pending()==[]` 且再 tick 不接纳。

## 2. 缺陷 2：正常预算 admission 与发送前扣账

* 决策前：读实例自己的 `cycle.budget.requests.check()`；`allowed is not True` → 直接
  `DEFER`，`llm_calls=0`，**不发送、不碰 Activity**；
* 发送前：用 `life_request_budget.BudgetedCognitionAdapter` 包住当前 cognition adapter，
  `reserve(kind)` 先落盘再 `require_coordinated_send(..., admission, ...)`；admission（
  `life_pause.admit_request`）**只在 adapter 声明 `send_split_capable` 时才给**（不给空头承诺）；
* 结束时 `finally` 还原原 adapter。

实测证据：`test_the_ledger_is_charged_once_and_a_spent_ledger_costs_no_decision`
——正常一次邀请后 `utc_day_calls` **恰好 +1** 且 `llm_calls==1`；用 ledger 自己的
`reserve()` 打到耗尽后，再来一条邀请 → `outcome=DEFERRED`、`llm_calls=0`、
Activity 状态逐字段不变。

父级补充生产预包装负控：原代码一次tick请求账本+3（邀请双包装+随后普通决定），
`/tmp/day-life-parent-t-050418`单项exit1。修为同一ledger对象的BudgetedCognitionAdapter复用、
本次attempt增量计数，且邀请已经决定的tick不再消费feed/普通窗口（留给下次tick）。
修后`/tmp/day-life-parent-t-656361`单项exit0；最终`/tmp/day-life-parent-t-616731`
新增14项/0skip/exit0。不改预算上限、不退款，不把多扣当成模型多发成功。

## 3. 缺陷 3：end 后正常窗口能看到新资格（不再同状态 QUIET）

`life_step_readiness.SITUATION_KEYS` 增加 `resume_eligibility_refs`（必要 Life 辅助），
`cycle._situation_parts` 用 LR-4 自己的 store 提供 `open_resume_refs()` 的有序 id。
于是"资格刚开"是一个**新处境**，`reconsideration` 不会再把那一轮判成
`SAME_STATE_ALREADY_CONSIDERED`（指纹里可见 `resume_eligibility_refs: relig:…`）。

## 4. end → 新 Decision RESUME → 实际第二页（父级闭合）

原作者最后一次报告为 WSL 14项中13绿1红，曾把空 `new_eligibility_refs` 归因于 LR-4。
父级真实owner复现表明该归因不成立，未修改 LR-4 产品规则：

- 原用例 `_open_window` 把 runner 单独推到未来，而来访/end仍使用实际时间；维护可先处理条件，
  因而不能只看 end 返回的新资格列表就宣称 owner 从未发过资格。
- end后的正常事件窗口已经会问一次新决定；先给 NO_ACTION、再立即对同一状态硬问 RESUME，
  会合法进入 QUIET，不能通过清除安静闸门来测试。
- `_reading_page` 原取 `reading_position` 集合第一条，集合保留多个历史位置，不能代表当前进度。
  现在复用 `subject_private_materials.read_reading_situation` 的当前来源版本最新有效位置。

正式用例现在从正常 `runner_tick` 消费 end，由资格feed打开新的事件窗口；在 cognition调用的
准确边界读回 joint COMPLETED、私人Activity仍WAITING、同活动OPEN资格1条、阅读仍page1，
然后才返回新鲜RESUME，读回原Activity ACTIVE、同checkpoint、实际位置page2。
不多加一轮CONTINUE，不强制自动恢复，不放宽freshness/CAS。

父级独立脚本 `session scratch/social_parent_smoke.py`：fixture
`/tmp/day-life-parent-c-804150`，9checks全真、page2、exit0、own_resources_closed=true、
real_model_calls=0。此前脚本导出名/operation结构/重复读页错误现场保留，未当产品失败。

## 5. 命令与真实退出码

`PYTHONDONTWRITEBYTECODE=1` 全程；语法检查 `ast.parse`（未用 `py_compile`）；临时脚本在会话 scratch。

| 命令 | 平台 | 退出码 | 结果 |
|---|---|---|---|
| `py -3.13 -B -m unittest -q tests.test_v04_social_life_core.SocialContactPortDispatchTest` | Windows 3.13 | **0** | `Ran 9 tests … OK` |
| `py -3.13 -B -m unittest -q tests.test_v04_social_life_core` | Windows | **0** | `Ran 9 tests … OK`（整链 5 项具名 skip：夹具需 POSIX owner/`fcntl`） |
| `python3 -B -m unittest -q tests.test_v04_social_life_core`（原作者最后一次） | WSL | **1** | 14项13绿1红；保留历史，归因见§4 |
| `python3 session-scratch/social_run_tests.py`（父级保留式runner） | WSL真socket | **0** | 14项全绿、0skip，fixture `/tmp/day-life-parent-t-950379`；关闭资源、不删目录 |
| `python3 session-scratch/social_parent_smoke.py` | WSL真owner正常runner | **0** | 9checks全真、实际page2、资源关闭 |

直接受影响的既有测试另由父级窄验；未跑全库、未做全树SHA。

## 6. 给父级/C 的对接事实（不变）

`assembled["contacts"]` 提供 `subject_id`/`home_ref`/`ledger_path`；C 用同一对值构造
`LocalContactInbox`，只 `submit`。父级夹具复用
`SocialLifeChainTest._social_fixture/_tick/_submit/_detail/_co_presence_cognition/
_no_action_cognition/_broken_cognition/_resume_cognition/_reading/_reading_page/_open_window`。

父级直接受影响单前台规则：`tests.test_day_life_decision_legality`，WSL14项OK、捕获Python退出码0。
外层工具曾因unittest stderr显示exit1，报告以显式`LEGALITY_EXIT=0`区分，未宣称全库通过。

## 7. 必要真实模型验证：一次请求，严格schema失败

隔离fixture `/tmp/day-life-parent-v-485681`，原始初始阅读由操作者脚本种下，之后只发送一次
国内 `cn:deepseek-v4.1-flash` 邀请决定。HTTP200、finish_reason=stop、8.12s，
实例请求账本0→1；但AG1 attempt为FAILED、`DECISION_ATTEMPT_FAILED:INVALID_SCHEMA`，
无committed Decision。不能称合法拒绝、自然共处或真实模型链通过。

现场旧投影把无Decision记为DECLINED/DECISION_UNKNOWN，父级发现并修为
UNKNOWN/DECISION_NOT_COMMITTED；历史事件不改写。补合法JSON但schema错误的回归，
`test_a_refused_invitation_never_touches_the_running_activity`通过，WSL单项exit0，
fixture `/tmp/day-life-parent-t-380515`。私人Activity ACTIVE、page1、checkpoint保留、session NONE。

验收脚本原exit0/ok:true仅检查transport与投影，不能作为验收结论；纠正证据
`session scratch/social-real-model-correction.json`明确FAILED_STRICT_AG1_SCHEMA。
社会阶段预算`social-model-budget.json`已ENDED_NO_FURTHER_CALLS，实际1attempt，无重试、无加额、
自有资源关闭。模型失败后不补调用刷绿；真实自主接纳/拒绝仍未证明。
