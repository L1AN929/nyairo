# I-r2 回执：UNKNOWN 闭环 / 营业事实读取 / 本次 World 后果进 M0

- 工作区：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），branch
  `work/v03-w01-navigation`，HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）。
- 状态：**UNCOMMITTED**。未 stage / commit / push / reset / clean / checkout / 整仓 copy；
  未删除任何文件或夹具（`/tmp/nyaw05r.jdK3q/*` 全部保留，含父级验收那次 `w05inst-fgon6ibm`）。
- 本回执只接**父级一次必要验收失败的三点**（UNKNOWN 未闭环 / 闭店改主意证据缺失 / M0 要求未完成），
  外加标题、时钟、预算三处"说了过头话"的更正。`docs/engineering/world-daily-i-r1-result.md` 原样
  保留作为失败现场；它里面"连续一天""两个显式时刻"的表述**由本回执更正**，见 §5。

## 1. owned delta

| 文件 | 改动 |
|---|---|
| `components/life/scripts/autonomous_life_cycle.py` | 新增 `reconcile_unknown_step()`（同 action/execution 对账 + 原 Activity settle）、`world_observation_facts()`（W 目录营业事实 → AG-1 `observed_facts`）、`_fact_token()`、`_emit_world_experience()`（World 后果经 AG-2 既有 outbox 进 Memory）；`evaluate_once` 把 `observed_facts` 与 `integration_id` 带进本轮；`settle_world_journey` 的记录补 `segment_status`/`expected_arrival_at`；`_record_world_progress`/`_record_world_consume_progress`/`_record_world_stay_progress(complete=True)` 各发一次 World 经历 handoff |
| `tests/test_world_daily_life_continuous.py` | 重写为**短程连续 episode**（INJECTED_CLOCK 单时刻）：确定性模型改成"一条固定规则读请求"（candidates + `observed_fact_keys` + `prior_experiences` + 当前所在地），不再由调用者逐轮 `policy` 指定；`move()` 走 `reconcile_unknown_step`（不 sleep、不重发 START）；新增"答复丢失"注入（`LostAnswerResolver`）与 §8 的 World→M0→下一次请求证据；`EpisodeFixture` 拆出供对照用例复用 |
| `tests/test_world_daily_life_plan_change.py`（新） | 同地点/同意图/同规则的 CLOSED↔OPEN 对照（07:00 关门 → 就地停留；10:30 开门 → 真的走过去，到店后 `stay:reading_point:READ_HERE:600` 才出现并被选中并 START） |
| `docs/engineering/world-daily-i-r2-result.md`（本文件）、`world-daily-i-runbook.md`（§6 时钟更正 + §7 r2 增量）、`life-v1-capability-status.md`（三行现状重写） | 回执与说明 |
| `.agents/notes/implemented/architecture/2026-10-18-world-daily-unknown-closed-loop-and-fact-read.md`（新） | 本批决策笔记 |

未改：W/F 的 World/Body/Memory/Supply owner 文件、AG-0/AG-1/AG-2 的授权与单前台语义、
`life_integration_ag2.py`、`action_reality_ledger.py`、`activity_progress_completion_lr5.py`、
`activity_waiting_agenda.py`、别人的笔记/AGENTS、管理 00/03/方案文档，
以及第三位 writer 的三条旧测试（`tests/test_world_daily_opportunities.py`、
`tests/test_one_day_life_w05r.py`、`tests/test_life_v1_restart.py`）。

## 2. 修复 1：`UNKNOWN` 真的闭环了

代码（`autonomous_life_cycle.reconcile_unknown_step(action_id=…, occurred_at=…, reason=…)`）按
owner 自己的顺序做三件事，**没有一件是重试**：

1. `settle_world_journey(observed_at=…, settle_only=True)` —— journey 账本按 owner 真实
   travel 记录回读（`_reconcile` → `in_flight`/`arrived`），`settle_only` 保证不会发起新段；
2. `runtime.action_command.reconcile_action(action_id=同一条, occurred_at=…)` —— AR-0 的
   World-ledger lookup（`reconcile_request` 按 action 自己的 idempotency key 找同一 request），
   同一 action id、同一 execution id、同一 submission fence；
3. 解析出来以后 `runtime.coordinator.settle_and_propagate_action_result(action_id=同一条)` ——
   结算**原来那个** Activity（LR-5 / 完成契约看到的与同步答复完全一致）。

返回 `{reconciled, action_status, journey{…}, reconciliation{strategy,result,evidence_refs,world_query_ref}, settlement{…}}`；
`STILL_UNKNOWN` 时只报 `reason_code=STILL_UNKNOWN`，不声明成功、不结算、也不发起新尝试。

证据（注入的是"世界真的出发了、答复丢了"，即 AR-0 自己的 `TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT`
故障形状，`tests/test_world_daily_life_continuous.py` 的 `LostAnswerResolver`，只丢一次）：

```
R2 drill:move:street_front  outcome=STEP_UNKNOWN decision=START chose=place:street_front
R2 MOVE-UNKNOWN street_front action=actn:e89425cbad007a62 reconcile=RECONCILED_SUCCEEDED/SUCCEEDED place=street_front
```

断言：`reconciled=True`、`action_status=SUCCEEDED`、`reason_code=RECONCILED_SUCCEEDED`、
journey 由 owner 账本给出（`in_transit` 或 `completed`，二者都出现在不同 run 里）、
`runtime.action_read.list_actions(status="UNKNOWN")==[]`、**没有第二次 START**（本轮只有一次
decision START，AG-2 也没有 `CANNOT_START_WHILE_FOREGROUND_ACTIVE` 那种复跑）。
`tests/test_world_daily_life_plan_change.py` 的第一段也是同一条路径（两次独立 run 都
`RECONCILED_SUCCEEDED`）。整个流程里没有 `sleep`、没有"多跑几次碰到绿的"、没有测试直写
journey/Activity（journey 的写入仍然只发生在 owner 的 `accept_journey`/`advance_journey` 内）。

**仍然是真 blocker 的部分**（不是 Life 侧能修的，也没绕过）：

- 为什么 owner 偶尔会把一次**真的提交过**的出发答成 `UNKNOWN`（World intent/gateway 提交路径），
  由父级 explorer 定位；本批不改 W/F。
- 对账后 AR-1 对这条无回执的动作只能给 `settlement_status=OPEN`、
  `outcome_kind=UNKNOWN_EFFECT`：**没有 owner 回执就不假装结算成功**。真正的"到达/后果"由随后
  的 owner 观察（`settle_world_journey` → LR-5 进度）确认。

## 3. 修复 2：闭店改主意现在是"读了事实才改"

- 事实通道：`cycle.world_observation_facts()` 只把**这一轮真能选到的地点**（她所在地 + 观察给出的
  单跳 `place:` 候选）的营业状态从 W 目录（`last_world_catalog`，与 Life 同一注入时刻读取）写进
  AG-1 的 `observed_facts`：`world_place_<place>_availability_<STATUS>`、
  `world_place_<place>_unavailable_because_<REASON>`、`world_here_place_<place>`。键是
  ASCII+下划线，所以生产 prompt 的 `observed_fact_keys`（`agency_cognition_adapter.py:203`）会保留；
  它不产生候选、不带 grant。
- 主链（门前街，工作日 07:00）：
  `R2 PLAN-CHANGE competing=['place:home','place:reading_point','place:street_corner']
  facts={"home":"OPEN","reading_point":"CLOSED","street_corner":"OPEN","street_front":"OPEN"}
  chose=stay:street_front:LOOK_AROUND:180
  reason=world_place_reading_point_availability_CLOSED: not going there, waiting nearby`。
  模型读到的就是 `world_place_reading_point_availability_CLOSED`（断言在 `read["fact_keys"]` 里），
  而 `place:reading_point` **仍在候选里**（走得到，所以"没去"不能归因于拓扑），它选了另一个合法
  分支并真的 START 了停留。
- 对照（`tests/test_world_daily_life_plan_change.py`，同一地点、同一意图、同一条规则，只让 owner
  的营业事实变一次）：
  ```
  R2 CONTRAST closed chosen=stay:street_front:LOOK_AROUND:180 facts={... 'reading_point': 'CLOSED' ...}
  R2 CONTRAST open   chosen=place:reading_point         facts={... 'reading_point': 'OPEN' ...}
  R2 CONTRAST reading stay=... planned=600
  ```
  CLOSED 时店里没有任何 `stay:reading_point:*`；OPEN 走过去、到店后
  `stay:reading_point:READ_HERE:600` 才出现在候选里、被选中、真的 START（`affordance=READ_HERE`、
  `planned_seconds=600`）。
- 更正 I-r1 的错误归因：I-r1 用"`place:cafe` 从 `street_front` 不出现"当闭店证据，那是**拓扑**
  造成的，不是闭店；咖啡店在街口才相邻。本轮把"闭店驱动"的证据换到真正相邻的书店
  （`reading_point`，就在门前街边上）与店内的 `READ_HERE` 上。
- Life 仍然没有"闭店必去 X"的路由：事实由 World 给、候选由 AG-0/AG-1 给，选哪个是模型那条
  固定规则（同一规则在两种事实下给出两个不同答案）。
- 未接（诚实标注）：**"等开门"没有 owner 侧 due-time 机制**——她只能"换个合法动作/回家"，
  不会在关着的门口按时间自动重试。

## 4. 修复 3：本次 World 后果真的进了 M0，并且真的影响下一步

- 准入路径用的是既有 AG-2 handoff + 真实 Memory consumer，没有新表、没有直接 INSERT：
  `_record_world_consume_progress` / `_record_world_progress` /
  `_record_world_stay_progress(complete=True)` → `emit_experience_handoff(world_refs=[…])`；
  `cycle.experience_provider = memory.port.recent` → AG-1 `prior_experiences`。
- 证据（`ir2-continuous-5.log`）：
  ```
  R2 CONSUME receipt=world-consumption:b2a9… execution=world-exec:2658… body=1
  R2 M0-WORLD row=lexp:02a2365114423f8c action=actn:8e35b11af61b9011 execution=world-exec:2658… owner_result=rset:e4a7f889c88e59c5
  R2 M0-INTO-REQUEST experiences=4 carries=actn:8e35b11af61b9011
  ```
  逐条对应父级三点要求：
  1. **真实 M0 里查得到**：`memory.port.recent()` 里这一行的 `action_ref` 就是本次消费的 AR-0
     动作，`owner_result` 是 AR-1 结算 `rset:…`，`artifact_ref=null`（**不是**作品行，也不是
     测试写进去的行）。
  2. **下一次 DecisionRequest/context 明确引用**：`RecordingCognition` 记下真实 RESUME 轮收到的
     `prior_experiences`，其中带着 `action_ref=actn:8e35…` 这一行（同一次请求里也带着来源营业事实
     `world_place_*`）。
  3. **后续 Action 真执行**：RESUME `adoption_status=APPLIED`、原 `checkpoint_ref` 不变，
     CONTINUE 真的落成新 Supply 版本（`artifact-version:0409… → 5ba0…`）。
- Memory owner 仍是 `chiyo`，Activity 仍是 `chiyo.global`，Supply 独立：本批没动 schema、没加
  通用 history owner、没把作品行拿来顶替。
- 诚实标注（留给父级定合同的两点）：
  - 真实 M0 行的**文案模板仍是作品腔**（"完成了一次真实修改，作品所有者确认了新版本…"），因为
    consumer 的 `build_outcome_event` 是围绕 owner_result 生成的；这对一条消费/停留后果并不准确。
    结构化引用（action/owner_result/outcome_kind）是对的，叙述不对——这是 Memory 侧模板的事，
    本批不改。
  - `experience_provider` 在生产装配里**没有**被 `assemble_autonomous_life()` 接上（它接受该参数，
    但 test/base 的 `assemble` 没传），本批在演练里显式接 `memory.port.recent`。若要把
    "M0 → 决策输入"变成产品默认行为，需要父级给一个装配点（本批不擅自改装配语义）。

## 5. 标题 / 时钟 / 预算的更正

- 标题与 test id：`test_one_instance_lives_the_whole_day_through_real_owners` →
  `test_a_short_continuous_episode_runs_through_the_real_owners`，模块 docstring 明说
  "不是一天，也不假装是一天"。旧 id 作为历史记在方法注释里。
- 时钟：**只有一个注入时刻** `Clock.now`（下一个工作日 07:00 JST）。每轮 `observed_at`、每次出行、
  每一份营业事实的读取（provider 的 `now=` 与 `observed_now=` 同一个值）都取它；旧 `Clock.schedule`
  已删，文件里还有一条 `assertFalse(hasattr(self.clock, "schedule"))` 防止再分裂。
  handler 侧也不再"周六目录 + 真实墙钟"混用，因此不存在两个互相矛盾的 now。
- 预算：暂停轮的 `budget=0` 只证明**暂停不重置**计数（`utc_day_calls`/`run_window_calls`/`run_id`
  不变），**不是**端到端计费。非零 reserve 需要真实 loopback/budget wrapper，本批没动——
  按父级要求精准标注为"既有 R1c 合同，不在本次独立验证"，不虚报 budget。

## 6. 复跑（真实退出码）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/nyaw05r.jdK3q
$S/w04venv/bin/python $S/i_daily_runner.py \
  tests.test_world_daily_life_play tests.test_world_daily_life_consume \
  tests.test_world_daily_life_continuous tests.test_world_daily_life_plan_change
```

| 命令 | 结果 |
|---|---|
| `tests.test_world_daily_life_play` | `Ran 4 … OK`，`PY_EXIT 0`（`ir2-play-1.log`） |
| `tests.test_world_daily_life_consume` | `Ran 6 … OK`，`PY_EXIT 0`（`ir2-consume-1.log`） |
| `tests.test_world_daily_life_continuous` | `Ran 1 … OK`，`PY_EXIT 0`（`ir2-continuous-5.log`，26.5s） |
| `tests.test_world_daily_life_plan_change` | `Ran 1 … OK`，`PY_EXIT 0`（`ir2-plan-1.log`，19.0s） |
| 四个模块一次跑（同一进程） | `Ran 12 tests … OK`，`FINAL_EXIT=0`（`ir2-final-1.log`，91.2s） |
| 父级验收那三条旧测试（回归保险，`PYTHONHASHSEED=12345`） | `Ran 109 tests … OK`，`PARENT3_EXIT=0`（`ir2-parent3-1.log`，680s） |
| 两个新模块用父级同一个种子再跑一遍 | `Ran 2 tests … OK`，`SEED_EXIT=0`（`ir2-seed-1.log`，43.2s），随后 `git diff --check` 0 |

未跑旧 17/56/155/179/184 suite（父级未授权）；未跑真实模型、未联网、未部署。
（那三条旧测试是父级验收自己用的 4 项里的另外 3 项，本批改的是同一个 cycle 文件，所以跑一次确认
没有回归；不是"顺手跑旧 suite"。）

## 7. 现在还剩什么（真话）

1. **owner 侧 `UNKNOWN` 的根因**：本批只保证 Life 侧闭环（§2）。根因在 World intent/gateway
   提交路径，等父级 explorer 的结论；修好之前，多段长链依然不该被当成稳定证据。
2. **无回执动作的结算**：`settlement_status=OPEN` / `UNKNOWN_EFFECT` 是 AR-1 的诚实答案。
3. **Memory 叙述模板是作品腔**（§4 末），需要 Memory 侧决定是否给 World 后果另一套叙述。
4. **"等开门"无 due-time**：没有 owner 侧的"到期再来"机制，本批不造。
5. **`experience_provider` 的产品装配点**（§4 末）需要父级指定，本批只在演练里接线。

## 8. 未删除 / 未触碰

- 夹具全部保留：`/tmp/nyaw05r.jdK3q/ir1-*`、`/tmp/nyaw05r.jdK3q/ir2-*`、`/tmp/nyaw05r.jdK3q/w05inst-*`
  （含父级验收失败那一个 `w05inst-fgon6ibm`）。
- 没有任何 `rm`/`unlink`/`rmtree`/`TemporaryDirectory` 清理、没有 pyc 删除、没有 socket unlink、
  没有全局安装、没有 provider/部署改动。

## 9. 门禁

- 笔记树：`verify-agent-note-tree.ts` → `ok: 22 note(s) tree and relative links verified`，exit 0。
- 笔记格式：`verify-agent-note-format.ts` → `ok: 22 note(s) verified`，exit 0。
- `git diff --check` → exit 0（无输出）。
- `git status`：本批只改/加上表所列文件（`M components/life/scripts/autonomous_life_cycle.py` +
  新测试/新文档/新笔记），`git diff --cached` 为空（没有 stage）；`HEAD` 仍是
  `7f09d26df64e054faf51ba35ad85c64f73a5a837`。
