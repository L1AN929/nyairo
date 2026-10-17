# life-v1 能力状态表（D 包收口时点）

图例：**已接**=有真实 owner 接点且有本包实跑证据；**部分**=接点存在但有一条边未通/未验；
**未接**=owner 侧语义或数据不具备，本包只如实报缺口，不伪造。

| 能力 | 状态 | 接点 / owner | 证据或缺口 |
|---|---|---|---|
| 同一实例的统一入口（prepare/run/status/pause/resume/timeline/report） | 已接 | `one_day_life.py` → 既有 resident/cycle | 集成套件全过；**真实跨进程重启已证**（`tests/test_life_v1_restart.py`：worker A 装配真实 runtime 跑一步并 `close()` 释放 lease；worker B 新 PID 同 root 新 runtime/新预算，暂停态 0 调用 → resume 续同一 Activity 出真实新版本；run identity/计数器/上限不变、书签保持）。旧"另一 PID 只读 probe"（`test_life_v1_integration.py:1103`）保留为历史，不再称 full restart |
| 处境读：Body / 资源预算 / 知识时效三列 | 已接 | `life_situation_view.LifeSituationView` ← `WorldBodyReadPort.body()` + `LifeRequestBudget.check()/view()` | 两列并存、body 列无预算词汇、四键进 AG-1 |
| 真实身体正向事实（AVAILABLE） | 未接（owner 缺口） | Body owner 的 `get_body_signals` 无正向能力声明 | 真实身体只有 LIMITED/UNKNOWN；本包不改合同 |
| 预算与身体不互相冒充 | 已接 | 容量四键来自 situation view；`execution_possible` 单列 | 删除了 round-budget→capacity 推导 |
| 短期评价（S07 缺失下的可撤回投影） | 已接（消费者）/ 生产无源 | `short_appraisal_view.py`（A 交付）→ `cycle.appraisal_source` → `appraisal_context()` → 该轮 `prior_experiences` | D-final：有效项真的进 AG-1 上下文（带 `appraisal_ref`/`source_refs`/`valid_until`，`authority=None`/`candidate=False`/`stable_fact=False`）；纠正/过期/来源丢失→空且 0 额外调用；空评价不开一轮。树里无 S07 owner，resident 传 `None` 并报 `appraisal_wired:false`（显式实验输入只用于测试），不生成自称情绪 |
| 作品（Supply ManagedArtifact）继续 | 已接 | `LifeArtifactPort.resume_point(work_artifact_id)` | 显式绑定优先，`latest_resumable` 仅兜底 |
| 私人事项 typed 文档（备忘/收藏/位置/未懂/收件） | 已接 | `private_daily_items.PrivateDailyItems` + `LifeArtifactPort` | 备忘正文默认不回；收藏不取全文/不置已读 |
| 真实阅读一页（取得范围→书签→未懂） | 已接 | `read_page()` → `read_artifact` + `record_source_acquisition` + `set_reading_position` + `mark_unclear` | 版本号与 owner head 一致；页码越界拒绝；旧书签 `needs_reread` |
| 房间物件观察 | 已接（世界需声明局部环境） | `one_day_life.observe_room()`（`one_day_life.py:946`）→ `world_living_skeleton.observe_world` | D-R1 连续演练：声明了区域/家具的场景包里真实读到 `objects=['desk_home','bed_home']`；生产包若未声明仍如实 `WORLD_DECLARES_NO_LOCAL_ENVIRONMENT`（不伪造） |
| 房间物件与私人条目建立"观察链接" | 已接 | `one_day_life.link_item_to_room_object()`（`one_day_life.py:959`）→ `pdi.note_item_at_observed_object` | 演练：`memo:bf131b2e…` → `desk_home`，`write.applied=true`；只接受本次真实观察到的 object_id |
| 有来源过去进入后续选择（C 读提示） | 已接 | `cycle.read_continuity()`（`autonomous_life_cycle.py:745`）→ `life_continuity_inputs`；`continuity_continue_object()`（`:902`）→ `_read_resume_point()`（`:1002`，`history_state` 在 `:1058-1075`） | D-R1b 真实证据（同一实例、真实 M0 行、无手写 handoff）：`CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` → 行内的 `owner_result`；版本取自**该行 Action 的 owner 回执**；`selected_by=CONTINUITY_HISTORY`、`owner_verified`、`history_state=OK` 时 pin == 该版本 == owner 当前 head；落后一版则 `STALE_HEAD` 且 pin 仍是 owner head（不硬写），对象不被 retarget。测试：`test_a_real_owner_result_names_the_next_continue_point_and_the_step_lands` |
| C 读水位持久化 | 已接（条件性） | `<life state root>/continuity_watermark.json` | 只在配了 `NYA02_MEMORY_DB` 时生成；未接线时如实报 `PAST_SOURCE_NOT_WIRED`。`seen`＝已提交 Decision 考虑过；`adopted` **只**记被 LR-4 标为 CONSUMED 的那一条（D-R1b 修） |
| Memory 边界（admission consumer + 经验视图） | 已接（既有） | resident `_memory_ports()` | D 只把 port 一并交回给 C 读口；未改准入/账本 |
| 普通入站消息只观察 | 已接 | `FiniteCoPresence.observe()` | `pause_requested=false`、`activity_started=false`、`owners_written=0` |
| 显式邀请 → 接受/推迟/拒绝 | 已接 | `consider()` 真实读 LR-3 life view/暂停位/预算/前台 | ACCEPT / DEFER / DECLINE + 真实 reason code |
| 原子/不可中断前台不强切 | 已接 | `consider()` 的 ATOMIC 分支 | `FOREGROUND_ATOMIC_NOT_INTERRUPTIBLE` |
| operator 全局暂停 ≠ 社交中断 | 已接 | `consider()` 暂停位分支；协议从不写暂停位 | 暂停时邀请被拒；暂停位事后仍未暂停 |
| 有限共同时间的合法中断 | 已接 | LR-3 `assess_events(..., apply_immediate_pause=True)` | 实测 `INTERRUPT_NOW` / `pause_applied=true` |
| 单前台可中断恢复（本单必做） | 已接 | LR-4 `enter_waiting_with_condition(EVENT_MATCH, release_foreground=True)` → AG-1/AG-2 | 实测：WAITING 保存点 → `START`/`APPLIED` 有限共同活动，`single_foreground=true` |
| 共同时间结束 → 新 resume eligibility | 已接 | LR-4 `evaluate_event` | 实测 `relig:d6c043…` OPEN，`llm_calls=0`，`auto_resumed=false` |
| 从 eligibility 恢复私人工作 | 已接 | AG-0→AG-1 Decision→AG-2 Adoption（LR-4 `explicit_resume_from_eligibility`，`life_integration_ag2.py:3382-3452`）；协议侧 `life_co_presence.end()` 只产出资格、不自作 resume | D-R1 连续演练：`decision=RESUME`、`adoption_status=APPLIED`、原 `checkpoint_ref` 原样保留，随后同一实例真的续步 `ACTED`；被打断的另一条 eligibility 仍 OPEN（status/read 不消费） |
| 共同活动的完成证据 ingest | 已接 | `life_co_presence._complete_joint_activity()`（`life_co_presence.py:781`）→ LR-5 `ingest_progress_evidence(apply_natural_completion=True)`（`:822`） | D-R1 连续演练：`joint_activity.status=COMPLETED`、`completed_transition=true`、`reason_code=JOINT_ACTIVITY_COMPLETED`；重复 `end` 由 LR-5 幂等与协议侧会话状态共同处理 |
| 真正并发（超出单前台合同） | 未接（明确延期） | — | 会话与返回值都标 `DEFERRED_BEYOND_CURRENT_CONTRACT`，未伪造 |
| 安静段零模型 / 0 动作 | 已接 | `evaluate_once` NO_MATERIAL/NO_ACTION 分支 | 安静轮 writer 调用 0；`NO_MATERIAL` 不花请求预算 |
| status 技术状态区分 idle/waiting/paused/blocked | 已接 | `classify_state()` | STOPPED/PAUSED/RUNNING/BLOCKED/WAITING/IDLE + basis + source |
| 事件时间线（只读、不补停机） | 已接 | resident journal + cycle step journal + control actions | 缺口不补；未跑 resident 时 `ticks=null` |
| 单写者 / 单前台 / 请求预算 / 响应锁外 / 暂停确认 | 已接（未改） | 既有 `life_pause`/`RequestSendGuard`/`LifeRequestBudget`/AG-2 | 本包未动这些路径；第二 writer `EXIT_LEASE_HELD` |
| 真实产品模型 / 24h / 外发 / 部署 / 多实例 | 未验 | — | 均不在本包授权内 |

## D-R1 增量更新（同一实例连续演练落地后）

本表最初是 D 包收口时点快照。以下行由 D-R1 的**一条同实例连续演练**（`life-v1-d-r1-result.md` §3，
`12.419s` / `PYTHON_EXIT=0`）真正证明后改写：房间物件观察、观察链接、C 读进入后续选择、
从 eligibility 恢复私人工作、共同活动完成证据 ingest。

仍未变（不得因上表而误读）：

- **真实产品模型 / 24h / 完整 P2 未验**：演练用的是 loopback 确定性决策与内容替身；C 的候选是真实 owner 读。
- **Body 正向事实仍缺**：只有 `LIMITED`/`UNKNOWN`；`NO_BURDEN_EVIDENCE` 是 owner 缺口。S07（affect owner）仍缺——短期评价只有 A 的投影 + D 的消费者（见下）。
- **生产 World 包未复验**：演练用的是夹具声明式场景包；生产包未声明局部环境时仍如实报"没有物件"。
- **restart 语义**：只读 probe 仍是 probe；**真实跨进程重启**已由 `tests/test_life_v1_restart.py` 证明（两个 worker 各自装配真实 runtime、释放/重取 lease、暂停态 0 调用后 resume 续同一 Activity）。仍没有"驻留进程整体重启"的证据。
- **`adopted_eligibility_refs` 过度声明已修（D-R1b）**：`autonomous_life_cycle.py` 现在只写**真正被 LR-4 标为 CONSUMED 且与本次 Adoption 的 Activity 匹配**的那一条；不可归因时写空并记录 `continuity_adoption_unresolved`。证据：`DCoPresenceClosureTest.test_only_the_consumed_eligibility_is_recorded_as_adopted`（两条同时 OPEN、DEFER 不消费、恰好一条 adopted、另一条仍 OPEN 且仍可经 AG-0 恢复）。

## D-final（整版收尾，本条为最后一处增量）

- **真实跨进程重启**：`tests/test_life_v1_restart.py::RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`（`Ran 1 test in 13.359s` / exit 0）。worker A 装配真实 `ProductionLifeRuntime` + `assemble_autonomous_life`（含 Memory 边界、实例自己的预算对象）真的跑一步并 `close()` 释放 canonical lease；worker B 新 PID、同 root、新 runtime/新装配/新预算对象，暂停态 0 调用 → resume 后合法续**同一** Activity 出真实新版本；run identity/计数器/上限不变、书签保持、空档期无写入。
- **短期评价消费者**：`tests/test_life_v1_appraisal_context.py`（3 项，`Ran 3 tests in 21.319s` / exit 0）——有效项真的进 AG-1 上下文；纠正/过期/来源丢失 → 空且 0 额外调用；空评价不开一轮；没有 Candidate/authority/stable fact。生产侧仍无源（无 S07 owner），resident 报 `appraisal_wired:false`，不生成自称情绪。
- **仍未验（不夸称）**：真实产品模型行为、24h/真实时钟、完整 P2、真并发、生产 World 包、S07 affect owner；见 `life-v1-d-final-result.md`。

## I（世界日常，2026-10-17，本表新增行）

本批新增的是**世界日常**这一条链的接线：机会来自真实观察，消费与移动各走对的 owner。旧行与旧回执保留不改。

| 能力 | 状态 | 接点 / owner | 证据或缺口 |
|---|---|---|---|
| 观察机会同时含地点与可消费物件 | 已接 | `world_move_opportunity_source.observed_move_opportunities` / `observed_consume_opportunities` ← 真 `WorldBodyClient.get_available_actions` + selector 可见 action space | `tests/test_world_daily_life_consume.py::test_one_observation_offers_competing_kinds_with_sources_and_validity`：同一实例同时给出 `place:street_front`(90s) 与 `object:i-food-riceball-01`，每条带 subject/来源/时间窗/有效期 |
| W 目录作为**地点事实**接入（不是授权） | 已接 | `read_location_catalog` → `world_daily_opportunities.location_opportunity_catalog`（`now` 必须 aware datetime） | 同一用例：`status=available`、`schedule_source=world_package_schedule`、`action_grant=false`、`walk_seconds>0`；目录不可读只降级为 `NOT_READ` |
| CONSUME 从真实 Decision 走完整正式链 | 已接 | AG-0 机会 → AG-1 Decision → AG-2 Adoption → AR-0 → Intent Executor（真授权）→ `gateway.sock` → Resolver | `test_a_consume_decision_really_uses_one_instance_through_the_owner_gateway`：一份 instance 真的被用掉、同一次原子保存落 owner 回执、`body_id=chiyo_body`、`placement_before=home` |
| 消费 ≠ 到达（各自完成判据） | 已接 | `_classify_world_consume`（只认 owner 回执）+ `_record_world_consume_progress`（LR-5 `world_fact:object:<id>:consumed`） | 消费轮 `journey` 不出现、位置不变；`progress_state=COMPLETION_ELIGIBLE`、`foreground_activity_ref=null`（Activity 真的结束）；MOVE 轮仍是 `IN_TRANSIT` |
| Body 一次且非营养 | 已接（F 的能力，本包只接线） | `body_consequence` 2.0s light `PHYSICAL_RESULT` | 该 execution 恰好一条后果，无 nutrition/satiety/hunger/health/recovery 声明 |
| 不复活 / 不补货 | 已接 | gateway 候选 + substrate placement | 吃完后再观察不再出现 `object:` 机会；回执仍恰好 1 条；后果条数不变 |
| 安静 0 调用 | 已接（沿用既有） | `evaluate_once` NO_MATERIAL 分支 | `test_a_quiet_instance_makes_no_model_call`：0 模型调用、0 收据、0 后果 |
| 游玩/休息（非产出停留） | **已接（I-r1）** | `world_move_opportunity_source.observed_stay_opportunities()` → AG-0/AG-1 START → AG-2 `STAY_AT_OBSERVED_PLACE`（`INTERNAL_CHECKPOINT_STEP`，`action_kind=None`，**无 AR-0 Action**）→ LR-5 进度/完成 | 真实起止：`stay` 的 `started_at` 取自 LR-5 自己的第一个 checkpoint；提前一轮（120s<180s）不完成、Activity 仍 ACTIVE；到点（240s≥180s）才 `COMPLETION_ELIGIBLE`、`checkpoint=world_fact:stay:street_front:LOOK_AROUND:180`、前台释放。没有假 REST/SIT，也没有 time skip 当"玩过了" |
| 闭店改计划 / 等开门 / 折返 | **已接（I-r2：模型读事实后改主意）** | `autonomous_life_cycle.world_observation_facts()` → AG-1 `observed_facts`/`observed_fact_keys`（`world_place_<place>_availability_<STATUS>`，来源是 W 目录在**同一注入时刻**的读取）；候选仍由 AG-0/AG-1 给，"选哪个"由模型那一侧的同一条规则决定 | 主链：门前街那一轮 DecisionRequest 的 `observed_fact_keys` 里确有 `world_place_reading_point_availability_CLOSED`，模型据此**没**选 `place:reading_point`（它仍然走得到，所以缺席不是拓扑），而选了就地停留并真的 START。对照（`tests/test_world_daily_life_plan_change.py`，同地点/同意图/同规则）：07:00 `CLOSED` → 停留；当天 10:30 `OPEN` → 真的走过去，到店里 `stay:reading_point:READ_HERE:600` 才出现并被选中。"等开门"仍无 owner 侧 due-time 机制（未接） |
| 出门 → 回家 → 恢复原私人保存点并续步（一次演练） | **已接（I-r2，同一实例，短程）** | `FiniteCoPresence.begin/end`（合法 safe-boundary 打断）→ 真出行（`UNKNOWN` 走 `cycle.reconcile_unknown_step`：同一 action/execution 对账 + 原 Activity settle）→ 暂停/到达 → `RESUME`（owner 资格）→ `CONTINUE` | `tests/test_world_daily_life_continuous.py`：作品新版本 → 打断（`foreground_released=true`、`wait_dep:` 记在 owner）→ 出门（**注入一次答复丢失 → `STEP_UNKNOWN` → 对账 `RECONCILED_SUCCEEDED`，没有第二次 START，`list_actions(status="UNKNOWN")==[]`**）→ 门前街停留 → 回家（暂停轮 0 调用、已出发段照常到）→ 消费 → `RESUME` 后 `checkpoint_ref` 与打断前相同并 CONTINUE 出新版本 |
| 真实经历经 M0 准入后改变下一步 | **已接（I-r2，本次 World 后果；I-r1 只证过作品行）** | 真 owner 回执/World 事实 → AG-2 `emit_experience_handoff(world_refs=…)` → 真实 Memory 准入（`experience_consumer`）→ `cycle.experience_provider`（= `memory.port.recent`）→ AG-1 `prior_experiences` | 演练：本次消费的 `action_ref=actn:…` + AR-1 `owner_result=rset:…` 在真实 M0 里查得到（`artifact_ref=null`，不是作品行）；下一次真 DecisionRequest 的 `prior_experiences` 里带着这一行的 `action_ref`，紧接着的 `RESUME`/`CONTINUE` 真的执行并出新版本。作品历史那句仍是 `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`（`selected_by=CONTINUITY_HISTORY`），弱线索 → `BOUND_WORK` |
| verb 白名单最小登记（CONSUME） | 已接（写集外，父级授权） | AR-0 `validate_prepare`、桥 `EXPOSED_VERBS`/`ACTION_ALLOWLIST`、`DELEGATED_VERBS` | `components.world.service.test_action_intent` 24 项 exit 0；World 服务 155 项 exit 0 |

回执：`docs/engineering/world-daily-i-result.md`（I 首轮，历史保留）、
`docs/engineering/world-daily-i-r1-result.md`（I-r1，历史保留；其"连续一天/两个时刻"表述由 r2 更正）、
`docs/engineering/world-daily-i-r2-result.md`（I-r2，本批）；
运行说明：`world-daily-i-runbook.md`（§6 连续用法、§7 r2 增量）。
决策笔记：`.agents/notes/implemented/architecture/2026-10-17-world-daily-life-consume-owner-path.md`、
`.agents/notes/implemented/feature/2026-10-17-world-daily-life-opportunity-routing.md`、
`.agents/notes/implemented/architecture/2026-10-17-world-daily-continuous-stay-and-plan-change.md`。
`.agents/notes/implemented/architecture/2026-10-17-world-daily-continuous-stay-and-plan-change.md`、
`.agents/notes/implemented/architecture/2026-10-18-world-daily-unknown-closed-loop-and-fact-read.md`。
本批发现的既有红灯（与本批无关，未顺手改）：W 计时出行用例与 W05R 跨日预算用例的墙钟依赖、
D-final 重启负控的哈希种子依赖（见 I 首轮回执 §7），以及 **W/F owner 侧计时出行偶发
`UNKNOWN`**（同一墙钟秒内连续多次出发；见 I-r1 回执 §6.1/§6.2）。多段单跳长链因此不在本批
稳定范围内，需 owner 定点修。

## v0.3最终父级增补（旧时点保留，以下为最新结论）

上文第85–88行是I-r1历史误诊，**不再是当前阻塞**：三条墙钟/候选顺序测试已定点修；真实UNKNOWN根因是客户端2s超时丢回执后journey误归blocked，不是“同墙钟秒”。客户端读2s/写15s、同execution一次提交后查询和journey非终态对账已修；旧blocked数据不自动迁移。真慢owner及随后认领证据见`world-daily-owner-unknown-result.md`和`world-neighborhood-owner-chain-result.md`。

本版最终机制/集成已限定接收：多地点10边同实例链、公园自然停留与咖啡店合法提前ABANDON（不假待满）、回家RESUME原checkpoint/CONTINUE出Supply版本、World经历进M0与实际后续请求；owner真新PID/新socket同根恢复、发送/预算/消费权限/Memory因果等最终8组51项全部通过，零跳过。世界营业预期只读API也已定向通过，不等于Agenda到期生活接线；停留PAUSE不提供恢复计时。完整范围、时钟和失败历史见[v0.3最终验收](v03-final-acceptance.md)与[对比0.2](v03-vs-v02.md)。

功能施工已停止；无本版测试阻塞。新链真实模型/24h/生产/Web配置台/S07来源/Body正向AVAILABLE/完整193功能等限制不变，全部UNCOMMITTED、未打标签推送部署。
