# v0.4 作品收尾 · W 包结果：owner 通路 + 纯收尾计划（给 C 的接口契约）

结论：**一件私有作品可以由她自己"收尾"了** —— 走 Supply owner **已有**的 `archive_artifact`，
owner 真的变 `ARCHIVED`（内容、全部版本、head、兄弟作品一条不动，无物理删除、无 tombstone），
AR-0 用原有 `FILE_ACTION` 种类 + 原 `idempotency_key` 提交，UNKNOWN 用**原键**对账，
完成证据由「owner 自己的 operation 行 + effect receipt + 回读」三者共同构成，
最后落在**既有** AR-1 `FILE_ACTION` 判定与**既有** LR-5 `ACTION_RESULT_MATCH` 上 ——
没有新增 criteria 种类，没有改 LR-5 evaluator，没有改 Supply store，没有第二个 owner。

C 的唯一集成点（cycle 侧）**不在本包写集内**，本文件第 2 节就是给 C 的固定接口。

## 1. 实际改动（写集，全部结果见 §8）

| 文件 | 改动 |
|---|---|
| `components/life/scripts/life_artifact_port.py` | 改：`COMMAND_OPS` 增 `archive_artifact`（+ 注释说明为何不暴露 `tombstone`）；新增 `archive_artifact()`；`ArtifactOutcome` 增 `lifecycle`/`revision` 两个**有默认值**的字段并在 `_outcome` 里照 owner 答复填（不猜） |
| `components/life/scripts/managed_artifact_action_adapter.py` | 改：新增 `OP_ARCHIVE_ARTIFACT` + `SUPPORTED_OPS`；`validate_prepare` 收完整 archive 意图；`submit` 增 `_do_archive`（OK/NO_CHANGE/CONFLICT/REFUSED/UNKNOWN 五路分开）；`observed_result_for_action` 增 `_observed_archive_result`（三份 owner 证据 → `artifact_archived_verified`）；`_success` 增 `receipt_kind` 关键字（默认值不变） |
| `components/life/scripts/result_settlement_ar1.py` | 改：**仅** `ACTION_KIND_FILE.supported_predicates` 追加 `artifact_archived_verified`（纯声明，evaluator 未动，一行 + 注释） |
| `components/life/scripts/artifact_closeout.py` | 新增：纯模块，`parse_closeout_target` / `closeout_target` / `build_closeout_adoption_spec` / `build_closeout_step`（+ `is_closeout_target` / `is_resumable_selection` / `archive_idempotency_key`）。不读不写任何 owner，无状态 |
| `tests/test_artifact_closeout_v04.py` | 新增：14 条 exact 用例（13 条真实 owner + 1 条纯计划） |
| `docs/engineering/v04-work-closeout-owner-result.md` | 新增（本文件） |
| `.agents/notes/implemented/feature/2026-10-27-v04-work-closeout-owner.md` | 新增（专属决策笔记） |

未动（逐项核实 0 diff）：`autonomous_life_cycle.py` / `autonomous_life_runner.py` /
`autonomous_life_events.py` / `autonomous_life_assembly.py` / `artifact_continuation_source.py` /
`one_day_life.py` / `autonomous_life_resident.py` / `life_runtime_coverage.py` /
`life_runtime_production.py`、`components/supply/**`（含 `artifact/store.py`、`service/life_supply.py`）、
`components/phone/**`、`components/world/**`、`components/native/**`、`MANIFEST.json`、任何
`instance.json` / 配置 / profile / `data/`。

## 2. 给 C 的固定接口（签名就是契约）

```python
# components/life/scripts/artifact_closeout.py  —— 纯模块，只依赖 stdlib
NAMESPACE = "closeout:artifact:"
ACTIVITY_KIND = "CLOSE_PERSONAL_ARTIFACT"
CLOSEOUT_PREDICATE = "artifact_archived_verified"          # 适配器形成的 attestation
CLOSEOUT_SETTLED_PREDICATE = "artifact_written_verified"   # AR-1 真正会出的 FILE_ACTION 谓词
PROGRESS_POLICY_VERSION = "chiyo.life.progress.artifact.v1"
ARCHIVE_OP = "archive_artifact"

closeout_target(owner_artifact_id: Any) -> str
parse_closeout_target(ref: Any) -> dict[str, Any]
is_closeout_target(ref: Any) -> bool
is_resumable_selection(point: Any) -> bool
archive_idempotency_key(owner_artifact_id: Any, expected_revision: Any,
                        activity_ref: Any) -> str
build_closeout_adoption_spec(owner_artifact_id: Any, *, title: Optional[str] = None) -> dict[str, Any]
build_closeout_step(owner_artifact_id: Any, expected_revision: Any, *,
                    activity_ref: Any, correlation_id: Any = None,
                    grant_id: Optional[str] = None) -> dict[str, Any]
class CloseoutPlanError(ValueError)      # 规格/意图不完整
class CloseoutTargetError(CloseoutPlanError)  # target 不是本命名空间
```

### 2.1 owner artifact ID 的编码（**最容易踩的坑，读三遍**）

Supply owner 自己发出来的 `artifact_id` 就带前缀：`artifact/store.py:217` → `aid = "artifact:" + uuid4.hex`，
即真实 id 形如 `artifact:8d8e1ff9489d43d196ce014e1f62c99d`。

因此：

| 名字 | 真实取值 |
|---|---|
| owner artifact id（port/adapter/AR-0 payload/evidence `artifact_ref`） | `artifact:<hex>` |
| closeout target（`closeout_target()` 的返回） | `closeout:artifact:artifact:<hex>` |
| `parse_closeout_target(t)["owner_artifact_id"]` | `artifact:<hex>`（**全字节**，逐字节相同） |

* **不剥内层 `artifact:`**，**不补**任何东西：`parse(closeout_target(x))["owner_artifact_id"] == x`。
  cycle 里那套 `_ref("artifact", ...)` 显示用改名（`autonomous_life_cycle.py:5282-5291`）只适合渲染，
  不要拿它构造收尾 target —— 真实 id 以 `artifact:` 开头时会被静默改写成另一件作品。
* 空串 / 前后空白 / 内嵌空白 / 非本命名空间 → `CloseoutTargetError`，**不猜、不补默认值**。
* `closeout:artifact:artifact:` 是**合法** target（work id 就是 `artifact:`），本模块不因为"看起来像半个前缀"拒绝它。

### 2.2 适配器（AR-0 侧）的 op 与 payload

```python
# components/life/scripts/managed_artifact_action_adapter.py
OP_ARCHIVE_ARTIFACT = "archive_artifact"     # 与 service 真实 op 同名
ARCHIVE_OPERATION_KIND = "ARCHIVED"          # owner operation 行里记的 kind
SUPPORTED_OPS = frozenset({OP_READ_ARTIFACT, OP_COMMIT_VERSION, OP_ARCHIVE_ARTIFACT})
```

`validate_prepare` 冻进 `frozen_payload` 的**完整意图**恰好三个键：

```python
{"op": "archive_artifact",
 "artifact_id": "<owner artifact id 全字节>",   # 例：artifact:8d8e...
 "expected_revision": <int >= 1>}              # owner 的 artifact revision，决策前读到的那个
```

* 缺 `expected_revision` / 非 int / `bool` / `<1` / op 不在 `SUPPORTED_OPS` → `ContractViolationError`，**根本到不了 owner**。
* Supply `operation_key` **不在** payload 里：它由 `SupplyManagedArtifactActionAdapter.operation_key_for(idempotency_key)`
  从 AR-0 的 `idempotency_key` 推导，一个逻辑动作一个键，终身不变。
* 适配器**不自己换 revision**：CONFLICT 就报错，不重读后偷偷用新 revision 再交一次。

### 2.3 调用例子（C 侧照抄即可）

**(a) 候选/契约：** `point` 是 `LifeArtifactPort.resume_point(artifact_id)` 的返回值。

```python
import artifact_closeout as ac

if not ac.is_resumable_selection(point):        # ARCHIVED/TOMBSTONED/无 head → False
    selected = {"selected_by": "NOT_RESUMABLE", "lifecycle": point.lifecycle}
    return (None, selected)                     # 已收尾的作品不再当可续候选

spec = ac.build_closeout_adoption_spec(point.artifact_id, title=f"收尾《{point.title}》")
activity_spec = ag2.ActivityAdoptionSpec.from_dict(spec)   # 只含 7 个真实字段名，其余走 dataclass 默认
```

`spec` 的键与值（每一条都已被 AG-2 实测接受，见测试 #14）：

```python
{"activity_kind": "CLOSE_PERSONAL_ARTIFACT",
 "title": "...",
 "target_refs": ["closeout:artifact:artifact:<hex>", "artifact:<hex>"],   # 两个，都是 AG-2 合法结构化引用
 "origin_ref": "closeout:artifact:artifact:<hex>",
 "progress_contract_spec": {"progress_kind": "ARTIFACT_EDIT",
                            "checkpoint_semantics": "LATEST_ARTIFACT_VERSION",
                            "target_refs": [...], "policy_version": "chiyo.life.progress.artifact.v1"},
 "completion_contract_spec": {"criteria_kind": "ACTION_RESULT_MATCH",
                              "criteria": {"required_predicate": "artifact_written_verified",
                                           "expected_artifact_ref": "artifact:<hex>"},
                              "target_refs": [...],
                              "required_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
                              "required_authority_domains": ["result_settlement_authority", "artifact_authority"],
                              "policy_version": "chiyo.life.progress.artifact.v1"},
 "result_binding_spec": {"target_refs": [...],
                         "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
                         "authority_domains": ["result_settlement_authority", "artifact_authority"],
                         "policy_version": "chiyo.life.progress.artifact.v1"}}
```

**(b) 步骤：** 只返回 cycle 里 `_ar0_step_spec` 同形的 7 个键，`step_spec_id`/`activity_ref`/
`activity_revision`/`decision_ref`/`adoption_ref`/`requires_waiting`/`waiting_condition_spec`/
`created_at` 由 C 自己补（身份字段归 C）。

```python
step = ac.build_closeout_step(
    point.artifact_id,
    point.revision,                 # ← owner 的 artifact revision，从 point 读，不要自己猜
    activity_ref=activity_ref,
    correlation_id=correlation_id,
    grant_id=self.artifact_grant_id)
# step == {"step_kind": "FILE_ACTION", "action_kind": "FILE_ACTION",
#          "target_ref": "closeout:artifact:artifact:<hex>",
#          "payload_spec": {"op": "archive_artifact", "artifact_id": "artifact:<hex>",
#                           "expected_revision": <int>, "grant_id": "<grant>"},
#          "expected_effect_predicates": ["artifact_archived_verified"],
#          "post_submit_life_behavior": "KEEP_ACTIVITY_ACTIVE",
#          "idempotency_key": "idem:v04:closeout:artifact:<hex>:<rev>:<activity_ref>"}
```

`idempotency_key` **不含** `correlation_id`：同一活动、同一 revision 的同一意图，跨轮次还是同一个动作
（所以 UNKNOWN 能在下一轮用原键对账）；换 revision 或换 activity 才是另一件事。

**(c) 提交与结算：** C 沿用既有 AR-0 流程即可（`prepare_action` → `submit` → receipt/`result_evidence`
→ AR-1 settle → LR-5 完成评估）。**唯一要求**：把适配器放进 `runtime.artifact_action_adapter` 的那条
AR-0 注册链（`ACTION_KIND_FILE` 一个 kind，不新增 kind），并把 `result_evidence` 原样交给 AR-1。
`observed_result_for_action` 已经替 C 做了回读，C 不要自己造谓词、不要把 `ARCHIVED` 字符串当证据。

**(d) 收尾后的候选：** 归档成功 → `resume_point(...).resumable == False`、`latest_resumable()` 不再返回它，
因此下一轮不会为它构造步骤（owner 也不会被反复拒）。C 侧仍应保留 `_read_resume_point` 的
`selected_by="NOT_RESUMABLE"` 具名理由（`ac.is_resumable_selection` 就是给这条用的）。

## 3. archive 动作的真实身份（核实过，不是照抄 recon）

| 项 | 真实值 | 出处 |
|---|---|---|
| service op 名 | `archive_artifact`（另一个生命周期动词是 `tombstone`） | `lifesupply/service/life_supply.py:230-233` |
| 请求字段 | `{"op","grant_id","operation_key","artifact":{subject_id,artifact_id,expected_revision}}` | `life_supply.py:302,312` + `_NESTED_ARTIFACT_LIFECYCLE` `:260` |
| grant | `COMMIT_MANAGED_ARTIFACT` / scope `artifact:personal` / target `workspace:<ws>` / authority `life-supply.artifact.v1`，`reserve_use`→`begin_execution`→`consume_use` | `life_supply.py:1179-1208` |
| **caller 的 CAS** | **`expected_revision` = artifact 行自己的 `revision`**（不是 head version id，不是预检后换新值）：`if row["revision"] != expected_revision: CONFLICT, revision=row["revision"]` | `artifact/store.py:472-473` |
| 成功效果 | `lifecycle=ARCHIVED`、`revision+1`；版本行/head/content 全留 | `store.py:482-491` |
| 幂等 | `artifact_operations` 唯一键 = `operation_key`；同键同 digest → 回放原结果（不二次改）；同键**不同** digest → CONFLICT | `store.py:466-468,243-244` |
| 已归档 + 新键 | `NO_CHANGE`（只写 operation 行，**不写 effect receipt**） | `store.py:474-479` |
| 终态 | tombstone → `BLOCKED terminal tombstone` | `store.py:480-481` |
| 跨库事务 | `coordinated=false`（`CROSS_OWNER_COMMANDS` 里没有生命周期命令），调用方被告知 | `life_supply.py:1212-1218` |

port 侧 `EffectState` 映射：`OK→APPLIED`、`NO_CHANGE→NO_CHANGE`、`CONFLICT→CONFLICT`（带 owner 当前 revision）、
`BLOCKED/DENY` 或 `reason=GOVERNANCE_REFUSED→REFUSED`（治理层在碰 store 之前就拒了，所以是确定拒绝）、
其余（含 service 自己的 `UNKNOWN`）→ `UNKNOWN`（不得当成功也不得当失败）。

## 4. UNKNOWN：用原键对账，不新键盲重试

* 适配器把"答复丢了"报成 `TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT`（`error_class=SUPPLY_RESPONSE_LOST`），
  **不出 receipt、不出证据**。
* 对账走 `query_status_for_reconcile` → `port.resolve_operation(operation_key)`：
  先 `read_effect_receipt`（owner 的持久效果凭证），失败再 `lookup_operation`；
  `receipt 有 → APPLIED(EFFECT_RECEIPT_FOUND)`、仅 operation 行 → APPLIED(OPERATION_ROW_FOUND)、
  两者都 NOT_FOUND → `NO_CHANGE(=NOT_SUBMITTED，可安全用同一键重试)`、其它 → 仍然 UNKNOWN。
* 同一键重交只会回放 owner 的**原** operation（`store._lookup`），不会第二次改 lifecycle；
  测试 #7 用真实 owner 验过：注入一次丢答复后 revision 只 +1，再次提交仍是同一 revision。

## 5. 归属：三份 owner 记录同时成立才叫"本动作归档了它"

`artifact_archived_verified` 只在下面**全部**成立时形成（`_observed_archive_result`）：

1. `lookup_operation(<本动作推导出的原 operation_key>)` 存在，`operation_kind == "ARCHIVED"`，
   `artifact_id` 是本作品，`result_json` 里 `status == "OK"` 且 `lifecycle == "ARCHIVED"`；
2. `read_effect_receipt(<同一个 key>)` 存在，`status == "APPLIED"`、`effect_kind == "ARCHIVED"`、
   `artifact_id` 一致；
3. `read_artifact()` 现在读回来 `lifecycle == "ARCHIVED"`，且 `revision` **正好等于**该 operation 记录的
   `revision`（= 意图里的 `expected_revision + 1`）。

于是：

* **光看 `ARCHIVED` 不算**：别人（另一个 operation key）归档的，本键只会拿到 `NO_CHANGE` 且没有 receipt，
  断言 1/2 直接不成立 → 不出证据（测试 #8：动作 `SUCCEEDED`，`result_evidence is None`）；
* **别人的键不能借归属**：操作行是按 key 精确查的，没有"同一个 artifact 就行"的放宽；
* **回放也不能冒充新证据**：revision 必须与记录一致，之后任何生命周期变更都会让它对不上。

## 6. 真实 AR-1 / LR-5 完成（没有扩大 evaluator、没有新 criteria 种类）

* AR-1：新增的 evidence 是 `observer_kind="artifact_observer"`、`verified=True`、
  `artifact_ref=<owner id>`，进入既有 `ACTION_KIND_FILE` 判定 → `SETTLED / SUCCESS_EFFECT`，
  claim 是**既有**谓词 `artifact_written_verified`，`object_ref` = 本作品（`result_settlement_ar1.py:1786-1810` 原样）。
  `supported_predicates` 里追加的 `artifact_archived_verified` 只是把这套 attestation 的词汇**登记**在
  FILE 种类的策略表里（该字段当前不被 evaluator 消费，纯声明）。
* LR-5：完成合同用**既有** `ACTION_RESULT_MATCH`。`required_predicate` 取
  `artifact_written_verified`，**不是** `artifact_archived_verified` —— 因为 LR-5 的 `predicates`
  只来自 AR-1 的 claim（`activity_progress_completion_lr5.py:787`），而 AR-1 的 FILE evaluator 只出
  `artifact_written_verified`（`:1790`）。要改为 `artifact_archived_verified` 就必须改 AR-1 evaluator
  （本包明令禁止），所以本包**不**那么做，并把"归档性"交给两件更硬的事实：
  ① 这个活动唯一的步骤就是 archive；② 这条证据只有在三份 owner 记录都对上时才存在（§5）。
  另加 `expected_artifact_ref=<owner id>` 与 `required_evidence_kinds=[SETTLED_RESULT_EVENT,
  ARTIFACT_CANONICAL_STATE]` / `required_authority_domains=[result_settlement_authority, artifact_authority]`。
  测试 #14 实测：这份合同被 `DecisionAdoptionCoordinator.build_completion_contract` 接受，
  且 LR-5 的 `_eval_criterion_node` 用真实 owner 结算出来的证据判为 `ACTION_RESULT_MATCH_SATISFIED`；
  换一个 `expected_artifact_ref` 立刻不满足（证据有归属）。
* **Action SUCCEEDED ≠ 活动完成**：`post_submit_life_behavior=KEEP_ACTIVITY_ACTIVE`，
  完成必须经过结算证据；没有任何结算证据时（§5 任一条件不成立）活动保持打开。

## 7. 保留与不做的

保留：作品对象、全部 `artifact_versions`（含 content 与 `content_sha256`）、head、兄弟作品、
grant/治理记录、operation 行与 effect receipt、Activity 历史。**无物理删除、无 tombstone、无移动**。

不做/不宣称：
* 不碰 cycle/runner/events/assembly/source/one_day_life/resident/coverage（C 唯一集成）。
* 不改 Supply store / service / Phone / World / native / MANIFEST / 配置 / 实例 / 数据。
* 不新增 decision 枚举、不新增 criteria 种类、不改 AG-1/AG-2/LR-5 语义；不写 Memory、不写自我信念、不晋升。
* 不做"归档材料 / PDI 文档 / 别的对象"的收尾：候选资格由 C 的绑定规则保证（只对绑定 work 且 ACTIVE、
  无占用冲突者开收尾）；本模块只提供计划，不判断"该不该收尾"。
* 不宣称"模型自己决定收尾"：本包的决策替身/脚本都不算模型自主；真实模型链由父级独立跑。
* `list_artifacts` 仍会返回 `ARCHIVED` 对象（owner 只过滤 TOMBSTONED）：收尾不是隐藏，是**不再作为可续候选**
  （`resumable` / `latest_resumable` / `is_resumable_selection` 三处一致）。
* AR-1 的 `supported_predicates` 字段目前无消费者，所以 §6 的声明是"词汇登记"，不是新判定路径。

## 8. 验证（真实退出码）

```
# 1) 本包 14 条 exact 用例（WSL Ubuntu，Python 3.12.3，AF_UNIX 真实 owner）
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_artifact_closeout_v04 -v
→ Ran 14 tests in 10.825s / OK                    exit 0

# 2) 受影响面回归（既有 port 使用方，只跑这一个文件，不是全回归）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_private_daily_items
→ Ran 14 tests in 12.576s / OK                    exit 0

# 3) 原生 Windows（Python 3.13）：owner/结算栈顶层 `import fcntl` 不可用，
#    文件显式降级而不是导入失败；13 条 owner 用例 skipped 且原因写进 skip 消息，
#    纯计划用例照跑并通过。
python -m unittest tests.test_artifact_closeout_v04
→ Ran 14 tests / OK (skipped=13)                  exit 0
```

跳过/注入的边界写在测试文件头：owner 是真的；只注入一处"答复丢失"（UNKNOWN 对账那条）；
只替身了 AR-0 自己的 `executor_capability.verify()` 租约。

14 条 exact 用例（#1–#13 真实 owner，#14 纯计划）：

| # | 用例 | 钉住的事实 |
|---|---|---|
| 1 | `test_port_archive_moves_the_lifecycle_and_keeps_every_version` | 合法归档：lifecycle→ARCHIVED、revision+1、head/版本号/content 全留 |
| 2 | `test_archive_without_a_usable_grant_is_refused_and_changes_nothing` | 缺 grant / 已撤销 → REFUSED，零写入，无 operation 行 |
| 3 | `test_archive_with_a_stale_expected_revision_conflicts_and_stays_active` | CAS：旧 revision → CONFLICT + owner 当前 revision，仍 ACTIVE |
| 4 | `test_archive_replays_the_original_operation_key_without_a_second_change` | 原键重放：只改一次，operation 行 outcome=OK |
| 5 | `test_the_same_key_with_other_parameters_is_a_conflict_not_a_replay` | 同键换参数 → CONFLICT，不回放、不改状态 |
| 6 | `test_adapter_archive_reports_the_owner_answer_and_attests_from_owner_records` | 适配器 receipt + 三份 owner 证据一致 + `artifact_archived_verified` |
| 7 | `test_adapter_unknown_archive_is_reconciled_with_the_original_key` | 丢答复 → AMBIGUOUS；原键对账 CONFIRMED_SUCCEEDED + 证据；不二次变更 |
| 8 | `test_archive_by_another_operation_is_not_attributed_to_this_action` | 别人的归档 → NO_CHANGE、无证据（SUCCEEDED≠完成） |
| 9 | `test_adapter_refuses_an_archive_intent_without_the_full_owner_revision` | 不完整意图在 prepare 就被拒，零写入 |
| 10 | `test_a_refused_archive_writes_nothing_to_the_work_or_its_sibling` | 治理拒绝 → 目标与兄弟作品零写入 |
| 11 | `test_sibling_active_work_survives_the_closeout` | 兄弟 ACTIVE、内容/版本不变，`latest_resumable` 仍是它 |
| 12 | `test_closed_work_is_not_resumable_and_is_not_offered_as_a_candidate` | 收尾后不可续、不再是候选（三处一致） |
| 13 | `test_settled_archive_evidence_satisfies_the_existing_lr5_contract` | AR-1 真实结算 + LR-5 既有判据满足，且证据有归属 |
| 14 | `test_closeout_plan_names_one_work_byte_for_byte` | 全字节 target、拒绝非法 ref、AG-2 接受 spec/step、键由意图决定 |

夹具：`/tmp/nyairo-v04-closeout`（`NYAIRO_V04_CLOSEOUT_ROOT` 可覆盖），每次 `mkdtemp` 唯一目录，
**保留不清理**；不用 `TemporaryDirectory`、不删任何东西。原生 Windows 自动跳过 13 条 owner 用例
（AF_UNIX + `os.geteuid`），第 14 条照跑。

未跑（按纪律不跑）：全树 SHA、`py_compile`、旧全套回归、真实模型、24h 常驻、发布。

## 9. 剩余限制（父级据此判读）

1. **cycle 还没接**：`_read_resume_point` 的 `NOT_RESUMABLE`、收尾路由、WORK 分支走 `build_closeout_step`、
   结算证据入 LR-5 都是 C 的活。当前"她自己收尾"只在 owner/适配器/结算层成立，日常一轮还不会发生收尾决策。
2. 完成合同的 `required_predicate` 是 `artifact_written_verified` 而非 `artifact_archived_verified`（§6 的理由）。
   若将来允许改 AR-1 evaluator，把它换成专用谓词更干净 —— 但那不在本包授权内。
3. `artifact_archived_verified` 只是 attestation 名字：它存在于 evidence `details` 与 receipt `details`
   （`attestation` / `predicate` 两键）以及 AR-1 策略表的声明里，不参与 LR-5 匹配。
4. 收尾候选的**资格**（绑定 work、ACTIVE、无占用冲突、不是材料/PDI/别的对象）完全在 C 侧；
   本包不提供候选过滤函数，只提供 `is_resumable_selection` 这一条 owner 事实。
5. NO_CHANGE 的路径（已归档 + 新键）返回动作 SUCCEEDED 但不带证据，所以那种活动会被 C 判成"已完成但无证据"
   而保持打开 —— 需要 C 决定用什么具名结果收尾（例如记 `ALREADY_CLOSED_BY_OTHER_OPERATION`）。
6. 真实模型链、长时间运行、发布、外发、真实支付均未做、未宣称。

## 10. 停写声明

W 包**已停写**：上述 7 个文件之外没有再改任何路径（含 cycle/runner/events/assembly/source/one_day_life/
resident/coverage、Supply、Phone、World、native、MANIFEST、配置、数据）。父级可就此做独立 owner 链验证。
