# v0.4 阶段 A 结果：生活循环——可以放下，也可以重新认领

Status: implemented（A 独占写集内完成并自测。真模型验证由父级统一做；本文件不给"她自然累了"这种结论）

写集（未碰 `life_sharing_service.py`、`components/phone/**`、B 的测试、父级检查点/设计进度）：

| 文件 | 性质 |
|---|---|
| `components/life/scripts/life_wait_basis.py` | 身体条件那一半：`SOURCE_BODY_REST` / `body_capacity_basis` / `body_waiting_spec` / `body_easing_event` / `is_body_capacity_condition` / `body_basis_from_condition`（本阶段中断前已落，本会话未改） |
| `components/life/scripts/autonomous_life_cycle.py` | `body_wait_basis`、`body_breakpoint_view`、`_fresh_body_easing_read`、`maintain_waiting` 的身体分支、轮内断点接线（中断前已落，本会话未改） |
| `components/life/scripts/agency_cognition_adapter.py` | `situation_context.breakpoint` 有界投影、裁剪保留、系统提示措辞（中断前已落，本会话未改） |
| `components/life/scripts/world_action_bridge_adapter.py` | REST 提交后的 `body_observed` 与 `command_success_is_not_recovery` 标注（中断前已落，本会话未改） |
| `components/life/scripts/autonomous_life_runner.py` | **本会话唯一代码修补**：`maintain()` 把 `body_checked` / `body_changed` / `auto_resumed` / `llm_calls` 透传进 tick/wake 报告（见 §5） |
| `tests/test_v04_life_pacing_basis.py` | 14 项：身体基线的来源/证据/不变量 + 断点进真 prompt（中断前已落，本会话未改） |
| `tests/test_v04_life_pacing_chain.py` | 4 项：同实例正常轮整链 + 未变不放资格 + 满足后仍要她自己 RESUME + 非 LIMITED 不提供断点（中断前已落，本会话未改） |
| `docs/engineering/v04-life-pacing-core-result.md` | 本文件 |
| `.agents/notes/implemented/feature/2026-11-15-v04-life-pacing-breakpoint-and-resume.md` | 决策笔记 |

---

## 1. 命令与真实退出码

全部在 WSL Ubuntu、`/mnt/d/nyairo-rc1-body-backend`、`PYTHONDONTWRITEBYTECODE=1` 下执行（AST 静态检查，不 `py_compile`）。退出码取 shell 的 `$?`，不是"看起来通过"。

| 命令 | 结果 |
|---|---|
| `python3 -X utf8 -m unittest tests.test_v04_life_pacing_basis -v` | `Ran 14 tests` / `OK` / **exit 0** |
| `python3 -X utf8 -m unittest tests.test_v04_life_pacing_chain -v` | `Ran 4 tests in 41.871s` / `OK` / **exit 0** |
| 两者同进程串跑（`-m unittest tests.test_v04_life_pacing_basis tests.test_v04_life_pacing_chain`） | `OK` / **exit 0** |
| 修补后复跑 basis + chain + `tests.test_life_wait_resume_v04.WaitTickMaintenanceTest` | `14 OK` / `4 OK in 53.604s` / `2 OK in 1.553s` / **overall exit 0** |
| `python3 -X utf8 -m unittest tests.test_life_wait_resume_v04 -v` | `Ran 22 tests` / `FAILED (failures=1)` / **exit 1**（见 §6；没有等价基线，不归因为历史无关） |

`Chain` 的 4 项对真实 socket/owner 说话（每项 10–15s），没有 mock 掉 Supply/World/AG1/AG2/LR4/LR5。

## 2. 整链：每一步走的都是正式入口

`tests/test_v04_life_pacing_chain.py::PacingChainTest::test_the_whole_chain_returns_to_the_same_page_read`
一次 fixture 内连续 6 轮 `cycle.evaluate_once(act=True)`，没有任何一步直接调用 `_apply*` helper、
没有改 owner 状态跳过 Decision、没有用 ABANDON 冒充释放：

1. **`read` → START**：真 Supply 里的私人材料，读到 `page=1`；Activity `ACTIVE`。
2. **`wait` → WAIT**：断点由这一轮自己的身体读给出（`BODY_CAPACITY_RESTED`，`wait_dep:body.<id16>.<baseline16>`），
   owner 把同一 Activity 置 `WAITING` 并写 `resume_condition_ref`，物料/页码不动，前台被
   `release_foreground=True` 放掉（`get_current_life_view().foreground_activity_ref is None`）。
   LR-4 里只有**一个** `PENDING`、`EXTERNAL_RESULT_EVENT` 条件，`parameters` 里带
   `basis_source=BODY_CAPACITY_RESTED` 与基线（body/capacity/burden/progress/trend/observed_at），
   **没有** `condition_expires_at`。
3. **`pose` → START**：`bodyact:pose:*` 真的被 World 接受，快照 `pose ∈ {seated, lying}`，
   `start_available=True`。
4. **`rest_start` → START**：REST Activity 由 LR-5 完成（盘上 `COMPLETED`），不占前台；
   owner 自己的 `recovery_state=active`，`stop_available=True`。
5. **`owner_tick(4)`**：同一台 `WorldBodyService` 被可控 clock 推进 4×600s，恢复
   `progress_band` 真的换了档、`trend=improving`（从 owner 读回来，不是写死的）。
6. **`maintain()`**：`body_checked=1`、`body_changed=1`、`satisfied` 非空、
   `emitted_eligibilities` 恰好 1 条、`auto_resumed=False`、`llm_calls=0`；条件转 `SATISFIED`，
   资格 `OPEN` 且 `activity_id` 是原阅读 Activity；Activity 仍 `WAITING`，仍第 1 页。
7. **`rest_stop` → START**：会话真被 owner 关掉（`recovery_state ∈ {completed, inactive, interrupted}`，
   `stop_available=False`），回读的 `body.recovery.state` 与 World 自己的词一致——只认会话，不称"恢复了"。
8. **`resume` → RESUME**：同一个 Activity 被重新认领（`activity_ref` 不变），`waiting_on_ref` 清空、
   重新占前台，读到**第 2 页**（`next_page=3`），`material_version_id`/`title` 与第 1 页一致。

边界项（同级直接用例，不重开新阶段）：

* `test_a_breakpoint_with_no_body_change_releases_nothing`：没有 owner tick 时
  `body_changed=0`、`evaluated=0`、`emitted_eligibilities=[]`、条件仍 `PENDING`、仍第 1 页。
* `test_a_satisfied_breakpoint_still_needs_her_own_resume`：条件已满足、资格已 `OPEN`，
  这一轮她选 `NO_ACTION` → 就什么都不做（Activity 仍 `WAITING`、仍第 1 页、资格仍只有 1 条）。
* `QuietBodyPacingTest`（`fatigue=0.05`）：`body_breakpoint.available=False` +
  `why_not=BODY_NOT_LIMITED`，提示里同样是具名原因；`wait_basis.source` 绝不会被冒充成
  `BODY_CAPACITY_RESTED`。
* `tests/test_v04_life_pacing_basis.py` 的纯函数段另外钉住：UNKNOWN/AVAILABLE 容量、无负荷信号、
  无 body 身份、无 revision、无观测时刻都**没有**基础；错身份、`STALE`、版本未变、观测时刻不比基线新、
  证据没动一律不产 LR-4 事件；容量 `LIMITED→AVAILABLE` 但证据没动不算。

## 3. 机制（可以在 prompt 里被读到，不是私有协议）

* 断点在轮内先于处境被算出（`autonomous_life_cycle.py:5241-5264`），处境里带着它
  （`:3357`），prompt 侧有界投影在 `agency_cognition_adapter.py:316-...`、`:1008-1018`，
  裁剪的最后一步只留现场+身体+断点（`:837`/`:898`，`dropped` 里能看到
  `situation_except_world_and_body`）。断点带 `keeps`（同一 Activity/同一材料/同一页）、
  `condition`（依赖与基线、`releases_foreground=True`）、`how`（回来的唯一方式是后来的 RESUME）、
  `not_recovery`。它**不是** candidate，也不含 `grant`。
* 提示词正面写明：断点"frees the slot"、回来是"RESUME she has to choose herself"、
  缓过来"不是 claim that she recovered"、没有断点时是 `available: false` 加具名 `why_not`
  （`agency_cognition_adapter.py:127-140`）。
* SATISFIED 只有 LR-4 能判：`life_wait_basis.body_easing_event` 只在"同一 body、FRESH、
  版本变了、观测时刻更晚、证据真的动了"时产一个 `EXTERNAL_RESULT_EVENT`（`BODY_CAPACITY_EASED`），
  交给 LR-4；`maintain_waiting` 本身 `auto_resumed=False`、`llm_calls=0`，不 replay Decision。

## 4. 限制（照实声明）

* **负荷是夹具种的**：`PacingChainFixture(fatigue=0.60)` 在 owner 启动**之前**把
  `somatic.fatigue=0.60` 写进该实例自己的 Body store（`body_runtime.initial_runtime` +
  `BodyStore.save_runtime`）。这是脚本选择，**不是**她自然累的；0.60 高于 owner 自己的
  `RECOVERY_OPEN_FATIGUE`，所以 REST start 在 owner 看来合法。
* **时间是可控 clock**：`ClockedGateway` 只覆写 `WorldBodyService._moment`，`owner_tick(n)` 每次
  `clock.advance(600s)` 后调真实 `service.tick()`。恢复档位/趋势是 owner 按自己规则算出来的，
  但**没有真实度过几小时**，也没有真实时间流逝。
* **决策是脚本**：`PacingScript` 只回放原始 JSON，且只挑 prompt 真给出的候选 id；
  本轮 0 次真模型调用、0 次外发（父级统一管真模型与预算）。
* 夹具目录只建不删：`PacingChainBase.setUp` 起 `PacingChainFixture`，`tearDown` 故意**不**调
  donor 的 `WorldGenTestCase.tearDown`（它会 rmtree 整个实例根），只关自己的 runtime/memory/gateway
  并把实例根留在 `/tmp/w05inst-*/instance`（跑完会打印 `fixture kept: ...`）。
* chain 测试是 POSIX-only（真 AF_UNIX socket + fcntl）；basis 测试的纯函数段与 prompt 段在无 POSIX 时
  仍然跑，只有 cycle 段 skip。

## 5. 本会话修的具体接线缺陷

`AutonomousLifeRunner.maintain()`（`autonomous_life_runner.py:412-435`）原来只回传
`state/checked/unchanged/changed/evaluated/satisfied/emitted_eligibilities/errors`，
A 新加的 `body_checked` / `body_changed` 与既有的 `auto_resumed` / `llm_calls` 全被丢掉。
后果：`assembly.runner_tick` 的 `report["waiting"]` 与 `wake.extra["waiting"]`（同文件 `:386`、
`:486`）里看不到身体那条 upkeep 跑过，产品报告无法把"身体复查"与"作品 head 复查"分开，
也看不到"零模型、没自动恢复"。现在这四个键一起透传（纯增量，不改任何判定）。

验证（scratch 探针，非仓库交付物
`$PI_SCRATCH_DIR/probe_runner_tick.py`，exit 0）：用同一 `PacingChainFixture` 走完 read/wait/pose/rest_start
+ `owner_tick(4)` + `maintain` 后直接调**产品** `runner.maintain()`，回传键集合为
`{auto_resumed, body_changed, body_checked, changed, checked, emitted_eligibilities, errors,
evaluated, llm_calls, satisfied, state, unchanged}`；同一探针里条件状态 `SATISFIED`、资格仍只有 1 条
`OPEN`、Activity 仍 `WAITING`、仍第 1 页——满足条件不会自动续作。

## 6. 当前失败与未验证（不遮掩）

* `tests/test_life_wait_resume_v04.py::WaitBasisCycleTest::test_a_round_without_a_dependency_sends_no_basis_at_all`
  **红**：`AssertionError: 'parkbasis:a6a49d34fe9657a2' is not None`。
  当前断言拿到的是 park 自选时间 basis，而该用例要求没有依赖时不发任何 basis；这是当前实现与断言的冲突。
  仅凭调用位置或代码阅读不足以证明它与本阶段无关，本轮没有构造等价基线/负控，故不作历史归因。
  该项保持 FAIL，未扩大本阶段范围修复，也不据此宣称全库通过。
* 未验证：真模型（父级统一，本会话 0 调用）；`fatigue=0.60` 之外的负荷档；多条件同时 pending；
  跨进程重启后的恢复（本阶段不做）；手机屏（B 的写集）。
* 未做：任何 reset/clean/stash/delete/commit/push/deploy；没有删任何 fixture（新增 `/tmp/w05inst-*`
  实例根全部保留）；`MANIFEST.json`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py`
  未触碰；`components/phone/**` 与 `life_sharing_service.py` 未触碰。

## 7. 父级怎么复用夹具（脚本机制链与真实模型分开）

```python
import test_v04_life_pacing_chain as chain
fx = chain.PacingChainFixture(fatigue=0.60)      # 建实例、种负荷、起真 owner、接真 Supply/Memory/AG2/runtime
# 以下保持fixture默认的原始JSON脚本，证明机制；不是指定真模型必须这样选择。
fx.turn("read", label="read")                     # 一轮正式 cycle.evaluate_once(act=True)
fx.turn("wait", label="wait")                     # 断点；读 fx.rounds[-1]["wait_basis"]
fx.turn("pose", label="pose"); fx.turn("rest_start", label="rest-start")
fx.owner_tick(4)                                  # 4×600s 可控 clock，真 owner tick
fx.maintain()                                     # 零模型 upkeep（= runner.maintain 调的同一个方法）
fx.turn("rest_stop", label="rest-stop"); fx.turn("resume", label="resume")
fx.reading()["page"]                              # 应为 2
fx.close()                                        # 关 runtime/memory/gateway，目录保留
```

* 真模型应替换 `runtime.decision_service.cognition_adapter`（并保留请求预算包装）；仅赋值 `runtime.cognition_adapter` 不证明正式决策入口已换模。`fx.turn` 的 plan 只供脚本 adapter 使用，真模型验收直接调用 `fx.cycle.evaluate_once`；具体父级有限使用与失败见 [父级结果](v04-life-pacing-parent-result.md)。
* 证据读口：`fx.reading()` / `fx.activity(ref)` / `fx.foreground()` / `fx.rest_entry()` /
  `fx.recovery()` / `fx.conditions()` / `fx.eligibilities()` / `fx.rounds`。
* 预算/账本由父级持有；这套夹具自身不发任何外发请求，也不读凭据。

父级真实模型使用另接 `BudgetedCognitionAdapter(real_cognition, fx.assembled.budget)` 到 `runtime.decision_service.cognition_adapter`，再直接 `cycle.evaluate_once`。本阶段已结束3请求，前两次CONTINUE实际读页、第三次PROVIDER_OUTPUT_TRUNCATED；没有自然休息成功结论，不能照脚本计划重放真模型动作。
