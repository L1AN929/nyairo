# v0.4 C：等待恢复接线 + 作品收尾对接（核心结果）

> 施工单：`D:\NYAIRO-v0.4-等待恢复与作品收尾-施工单.md` C 包。唯一树 `D:\nyairo-rc1-body-backend`，HEAD `41e818f`，历史 dirty 全部保留；本包只动 C 的独占写集。
> 本文件是**接口契约 + 结果**，写于 W 的 `artifact_closeout.py` 已落盘之后（按实际签名对接，未照抄 recon）。
> **没有宣称「已经真实活过一天」**：真实模型 tick、原实例行为由父级在全部停写后执行。

## 1. 落地了什么（可指认的事实）

| # | 事实 | 位置 |
|---|---|---|
| 1 | 唯一真实 WAIT 基础（两种来源，只读） | `components/life/scripts/life_wait_basis.py`（新，319→330 行） |
| 2 | 决策轮把 `known_waiting_basis_ref` + `waiting_spec` 交给 AG-1/AG-2 | `autonomous_life_cycle.py:1638`（`wait_basis`）、`:3219-3231`（每轮算 basis）、`:3562-3567`（传参） |
| 3 | 有界、零模型的在跑到期维护（不是新 timer） | `autonomous_life_cycle.py:1731`（`maintain_waiting`）、由 `autonomous_life_runner.py:wake` 第 3b 步调用（`maintain()`） |
| 4 | `scan_due_agenda` 窄增进等待 port 白名单（已有方法，非新方法） | `life_runtime_production.py:550-560` |
| 5 | 修「已绑定作品不可继续」主路径缺陷 | `autonomous_life_cycle.py:1583-1602`（`NOT_RESUMABLE`） |
| 6 | 收尾候选（PERSONAL_OPPORTUNITY 的第二个 item）+ 独立身份 | `artifact_continuation_source.py:_closeout_item_for / discover / materialize / refresh` |
| 7 | 收尾的 AG-2 形态与 AR-0 步骤由 W 的纯计划给出，C 只做候选与路由 | `autonomous_life_cycle.py:1856`（`_closeout_point`）、`:1916`（`_closeout_adoption_spec`）、`:1934`（`_closeout_step_spec`）、`:3274-3320`（adoption 路由）、`:3395-3415`（step 路由）、`_RoutedAdoptionSpec` |
| 8 | **gen1 真链回归修复**：`_decision_chose_closeout` 改为实例方法，经 `self._selected_candidate` 读候选（原 `@staticmethod` 直接调类属性 `AutonomousLifeCycle._selected_candidate(record)` → `TypeError: missing 1 required positional argument: 'decision_record'`，整轮 WORK 在路由前 `DECISION_FAILED`） | `autonomous_life_cycle.py:1964-1978`；回归：`tests/test_life_closeout_cycle_v04.py::CloseoutSeamTest::test_the_routed_adoption_spec_resolves_the_closeout_through_the_bound_predicate` |
| 9 | **tick 顺序修复（父级 gen1 只读发现）**：既有 `runner_tick` 现在「先一次零模型有界 maintain → 再 poll feeds → 再 due」，因此 NOT_DUE 且无事件的 tick 也会扫到期、重读依赖；同一 tick 内 LR-4 新开的 OPEN 资格对资格 feed 可见。`wake/run_due/wake_on_event` 增显式 `maintain=`/`maintained=` 参数 seam，同一 tick 只维护一次；`run_due` 在 due gate **之前**维护，故 `run_once`/控制 CLI 也覆盖 NOT_DUE | `autonomous_life_assembly.py:674-691`（tick 顺序）、`autonomous_life_runner.py:339-395`（wake seam）、`:460-487`（run_due）、`:489-495`（wake_on_event）；回归：`tests/test_life_wait_resume_v04.py::WaitTickMaintenanceTest::test_the_tick_maintains_before_it_polls_...`、`...::test_a_closed_window_still_maintains_without_spending_a_request_or_a_round` |

## 2. WAIT 合同（实际参数，非照抄 recon）

**两种来源，仅此两种**（`life_wait_basis.BASIS_SOURCES`）：

| source | 条件 | baseline | 理由 |
|---|---|---|---|
| `WORK_HEAD_AFTER_NO_CHANGE` | 前台 Activity 的 readiness 为 `False` 且 reason = `WORK_NO_CHANGE_CONFIRMED` | owner 当前 head（`StepReadiness.source_revision`，由 `_readiness_revision` 真读 owner） | 「这份作品会有下一版」 |
| `READING_MATERIAL_NEXT_VERSION` | readiness reason = `READING_EXHAUSTED` | 材料当前 revision（owner 读） | 「这份材料会有下一版」 |

**明确不给等待基础的**（全部有测试）：可执行步骤仍在（`ARTIFACT_STEP_AVAILABLE`）、无前台/前台非 ACTIVE、World 步骤未接线（`WORLD_STEP_NOT_WIRED`）、目标命名空间未知、`READING_SOURCE_REVOKED` / `MATERIAL_NOT_ACTIVE` / `MATERIAL_EMPTY` / `POSITION_OUT_OF_RANGE` / `PRIVATE_ITEMS_UNAVAILABLE` / `OWNER_UNAVAILABLE` / `OWNER_REFUSED` / `UNKNOWN`、以及**没有 owner revision 的基线**。「以后也许会变」永远不是连接；timer/自定 deadline 一律不做。

**等待引用怎么拼**：LR-2 会按 `waiting_ref_vocab.VALID_WAITING_REF_PREFIXES` 校验 `waiting_on_ref`，**`artifact:` 不在表里**（它是 LR-5 结果/动作目标命名空间），所以条件与事件统一拼成 `wait_dep:artifact.<id16>.<baseline16>`（依赖族），并且 baseline 进 ref——换了 head 就是另一个依赖，旧 ref 无法满足新条件。测试用 `waiting_ref_vocab.parse_waiting_ref` 把这个钉子钉住。

**SATISFIED 不是 C 决定的**：`head_change_event` 只在 owner 当前 head **真的不同于基线**时产出一个 `EXTERNAL_RESULT_EVENT`；相同 head / 没有 head / owner 拒绝读 → `None`（条件只能保持 PENDING）。之后由 LR-4 `evaluate_event` 判 SATISFIED 并产出 OPEN eligibility；C 不 resume、不复放 Decision、不清 PAUSED 前台。

**代价**：WAIT 依旧耗**一次**决策请求（它是一次选择，不是免费挂起），不产内容。`waiting_spec` 里没有 `condition_expires_at`、没有 `schedule_agenda`，`release_foreground=False`（前台默认不凭空释放，别的活动能否上位仍由 owner 合同决定）。

**PAUSED**：前台 PAUSED 不产生任何新 WAIT 基础（`wait_basis` 要求 ACTIVE），也不被暗清指针；它自己的 RESUME 问题由 LR-4 eligibility 决定。

## 3. 收尾合同（与 W 的实际接口）

W 交付（`artifact_closeout.py`，本包只读、按实际签名调用）：

- `closeout_target(owner_artifact_id) -> str`，目标 = `closeout:artifact:<owner id 原样>`，真实形态 `closeout:artifact:artifact:<uuid>`；
- `parse_closeout_target(ref) -> dict`（`owner_artifact_id` 原样返回）；
- `build_closeout_adoption_spec(owner_artifact_id, *, title=None)`；
- `build_closeout_step(owner_artifact_id, expected_revision, *, activity_ref, correlation_id=None, grant_id=None)`。

C 的接线：

1. **候选资格**（`_closeout_point`）：`artifact_closeout` 可导入且四个函数都在；`CHIYO_LIFE_WORK_ARTIFACT_ID` 明确绑定的作品（**不按新旧挑**）；前台空闲（有 Activity 占用时**不切走**）；owner 报 `ACTIVE` 且有 `revision>=1`；目标与 `parse_closeout_target` 双向往返一致。材料 / PDI / 别的作品不在此列（不在绑定 work 上）。
2. **候选形态**：仍然只用既有的 `PERSONAL_OPPORTUNITY` / `CONSIDER_PERSONAL_OPPORTUNITY`，不新增 kind。收尾 item 与续写 item 同源同对象，但候选 id / semantic_identity / idempotency_key 各自独立；续写候选的身份**逐字节未变**（回归钉子：`cand:sha256("PERSONAL_OPPORTUNITY:<owner id>:v3")`）。
3. **决策后路由**：`_RoutedAdoptionSpec` 增加 closeout 一条，只有在**committed Decision 自己的候选** `target_refs` 里带 `closeout:artifact:` 时才用它；step 同理（`_routed_step_builder` 的 `CLOSEOUT` 分支与 `CLOSEOUT_AR0` 路径）。closeout 的 START 仍走 `START → AR-0(FILE/archive_artifact) → AR-1 settlement → LR-5`，完成判据仍是既有 `ACTION_RESULT_MATCH`（`required_predicate=artifact_written_verified`、`expected_artifact_ref=<owner id>`、证据 `SETTLED_RESULT_EVENT`+`ARTIFACT_CANONICAL_STATE`），**没有新增 decision 枚举、没有新增 criteria 种类**。
4. 计划缺失时一切照旧：`closeout_state()` 报 `NOT_AVAILABLE / ARTIFACT_CLOSEOUT_NOT_DELIVERED`，不产候选、不导入半写模块（`closeout_module()` 惰性、四函数齐备才接受）。

## 4. 测试与真实退出码（本机 WSL，`PYTHONDONTWRITEBYTECODE=1`）

| 组 | 精确 ID 数 | 结果 | 退出码 |
|---|---|---|---|
| `tests.test_life_wait_resume_v04`（11 纯 + 9 cycle + 2 tick） | 22 | 全绿 | `WAIT_EXIT=0`（11+9）、`TICK_ORDER_EXIT=0`（2，单独跑） |
| `tests.test_life_closeout_cycle_v04`（7） | 7 | 全绿 | `CLOSEOUT_EXIT=0`（6）、`GEN1FIX_EXIT=0`（1，单独跑） |

说明：施工单给的是 8–14 + 4–6；两文件各条都是不同主张、无重复绿、无继承扩组（每个 test 都在各自类 `__dict__` 里），总数 22/7 略超是因为把「撤权/UNKNOWN 边界」「foreign condition 不重判」「NOT_RESUMABLE 修复」「tick 顺序」「NOT_DUE 不花钱」「gen1 路由谓词」各自单列。夹具在 `/tmp/nyairo-v04-c-fixtures` **保留**（无 TemporaryDirectory、无 unlink、无物理删除）。

## 5. 明确未证 / 剩余限制

1. **未证真实一天**：没有跑原实例真实模型 tick；「模型明确说 WAIT → 真实新 head → 真 RESUME」这条链只有机制级证据（fake owner + 真 readiness/真 ledger/真 basis）。
2. **收尾从未真实归档过**：真实 Supply `archive_artifact`、grant/CAS、receipt 回读、LR-5 完成，属于 W 的套件与父级有限真链；本包只证明候选资格、形态与路由。
3. **reconsideration 抑制仍在**：某个「无步可做」状态若**已经被拒过一次**，同一 fingerprint 仍会被抑制（TTL 6h，`life_step_readiness.py`，非本包写集），这期间不会为它花钱再问一次，也就不提供 WAIT。首次出现 no_change 的那一轮 fingerprint 里 `step_reason` 不同，因此 WAIT 可达。
4. **WAIT 被采纳时 wake 记 `PAUSED`（`OUTCOME_PAUSED`）**，这是既有语义（`_report_chosen_exit`），决策字段仍是 `WAIT`；`runner.OUTCOME_CLASS` 未改。
5. `runner.py` 原有的 `_event_is_fresh` 命名与其 docstring 相反（`dedupe=True` 路径实际只在 `wake_on_event` 未被调用时生效），本包未改，属既有行为。
6. 未改：AG-0/AG-1/LR-2/LR-4/LR-5 语义、`MANIFEST.json`（0 diff）、两个 move 保护脚本（0 diff）、World/Phone/native/Supply store、发布与配置。
7. **报告口径小洞（既有，本包未改）**：`evaluate_once` 末尾的兜底 `except Exception` 一律以 `llm_calls=0` 报 `DECISION_FAILED`，所以「决策请求已花掉、之后才抛异常」的轮会被记成 0。gen1 那次例外（脚本替身决策，实际请求数就是 0，`requests.utc_day_calls=0` 可证）；要修属于另一处口径，不在本次最小修复范围。
8. **tick 顺序只在夹具层证明**：两条新测试固定的是「先 maintain → 再 poll → 再 due」「NOT_DUE 也维护、不花 request/round」「同一 tick 的 OPEN 资格对 feed 可见」「同一 tick 只维护一次（`scan_due_agenda` 调用数 == 1）」。真实 resident/runner 进程里的同一性质仍要父级用一条 NOT_DUE tick + 一次真实 head 变化来复证；`EventFeedPoller.wake` 仍保留在 `autonomous_life_events.py`（现无生产调用者），tick 走的是逐事件 `wake_on_event(maintain=False, maintained=upkeep)`。

## 6. 父级要跑的两条链（停写后）

1. 等待链：同一实例，**先跑一条 NOT_DUE tick**（窗口未开、无事件）→ 断言 `tick.waiting.checked>=1`、`evaluate_event` 未被无谓调用、`llm_calls=0`、`spent_today` 不变；再让真实 owner 里作品的 head 变化一次 → 下一次 tick 的 `tick.waiting.emitted_eligibilities` 非空且**同一 tick** 的 `events` 里出现 `RESUME_ELIGIBILITY_OPENED` → 模型明确 RESUME → 同一 Activity 继续；断言不自动 resume、不重放 Decision、PAUSED 前台不被清、一个 tick 只调一次 `scan_due_agenda`。
2. 收尾链：绑定 work ACTIVE、前台空闲 → 模型选收尾 → 真实 archive → settlement → LR-5 完成 → 新进程不再出现该候选，材料/PDI/其他作品历史不变。
