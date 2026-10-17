# World 日常：owner 已提交却被判成 UNKNOWN（模糊提交的收尾）

状态：已落地（定向修复，未改 Life/其它 owner）。
范围：`chiyo_bundle/world_journey.py`、`components/world/service/bridge/world_body_client.py`
（及其逐字节双份 `components/native/integration/world_body_client.py`）、
`components/world/service/test_world_body_bridge.py`、两个新增定向测试文件。

## 1 实证根因（一条链，不是"同墙钟秒"）

原始 fixture `/tmp/nyaw05r.jdK3q/w05inst-fgon6ibm`（**只读，未改动任何字节**）留下的证据：

| 事实 | 位置 |
|---|---|
| 授权已签发且**已消费**：`issued 07:00:13.254Z` / `consumed 07:00:13.262Z` | `run/action_authorizations.json` (`wauth:f4c7496822ee4c219877cbd739484aaa`) |
| owner 账本 **committed / move_departed**，`world_mutation_refs=[world.travel=travel-2254b135…]` | `data/life_execution.jsonl` |
| travel `IN_PROGRESS`，street_front→home，`started_at 07:00:15Z` | `data/world/world_travel_delivery.json` |
| 客户端在 ≈`07:00:15.26` 读超时 → `UNKNOWN / failure_class=transport_fault:TIMEOUT` | `run/action_lifecycle.jsonl` |
| 旅程被写成**终态** `blocked`，`reason_code=UNKNOWN`，段仍 `pending`、`travel_id=null` | `data/world/world_journey.json` |

根因三段：

1. `world_body_client.submit_action` 用**读**超时（2.0s）做唯一一次提交（`retries=0`），
   `connect+sendall` 已经完成、owner 正在提交时客户端就放弃，把"回执丢了"报成
   `transport_fault:TIMEOUT`；此后**从不回读** owner 的判决（`get_action_result` 早就存在）。
   2.0s 比 owner 自己的 `_commit_lock`（`world_action_resolver.py:615`，10s 预算）还短。
2. 端口（`world_journey_action_adapter.py:608`）把 UNKNOWN 如实报成 `uncertain`。
3. `world_journey._submit_segment` 把 `owner_status != "applied"` 一律
   `_fail(..., STATUS_BLOCKED, ...)`（原 954-961 行）：`uncertain` 与 `rejected` 同等对待，
   且**没有**走自己 docstring 里写好的"按 execution_id 认领 travel 记录"那条路
   （`_reconcile` 早就能正确恢复，见 scratch `unk-probe/probe_recover.py` 的反事实）。
   `blocked` 是 `TERMINAL_STATUSES`，`advance_journey` 从此直接返回投影，永不自我修复。

## 2 owned delta

### 2.1 `components/world/service/bridge/world_body_client.py`（+ native 双份逐字节同步）

| 位置 | 改动 |
|---|---|
| `DEFAULT_READ_TIMEOUT = 2.0` / `DEFAULT_MUTATION_TIMEOUT = 15.0` | 读与提交分开计时：提交预算覆盖 owner 自己的 10s commit lock；读保持 2s，不盲目放大 |
| `WorldBodyClient(timeout=2.0, mutation_timeout=15.0)` | 新增构造参数；`self.timeout` 仍只是读预算 |
| `_call_once(..., timeout=None)` / `call(..., timeout=None)` | 每次调用可显式给 deadline；读重试仍带同一 deadline |
| `submit_action` | 用 `mutation_timeout` 提交；`retries=0` 不变；transport fault 后**一次**只读 `get_action_result(execution_id)` |
| `_resolve_by_owner_verdict(execution_id)` | 新增：一次 `get_action_result`（`retries=0`，读预算），只有 `known=true` 且 `verdict∈{SUCCESS,FAILURE}` 才把疑问升级为判决；`SUCCESS→SUCCESS`、`FAILURE→FAILURE`；`UNKNOWN/NOT_FOUND/不可读/超时→仍是 UNKNOWN` |
| `ClientResult` | 新增 `failure_class` / `detail` 两个可选字段（并入 `as_dict()`）：`transport_fault:TIMEOUT` 这类归因不再只能塞在 `reason` 里 |
| 其它 verb | 行为不变：`get_status/snapshot/observation/body_signals/available_actions/action_result/ready` 仍走 `_read` + 有界重试 |

`components/native/integration/world_body_client.py` 修复前与 bridge 版 **SHA256 完全相同**
（`5BCA5864…`），是同一合同的实际双份，故按同一内容整体同步（同步后两份均为 `C40503C9…`）。

### 2.2 `chiyo_bundle/world_journey.py`

| 位置 | 改动 |
|---|---|
| `AMBIGUOUS_SUBMIT_PREFIX="SEGMENT_SUBMIT_UNCERTAIN"`、`NOT_SENT_SUBMIT_PREFIX`、`PRE_SUBMIT_REASONS` | 模糊提交的持久标记与"没越过边界"的原因集合 |
| `_reason_with_detail(prefix, detail)` | 把端口的 failure detail 清洗后拼进 `reason_code`（仍满足 `_validate_journey` 的 `[A-Za-z0-9._:/-]`、≤120）。账本里现在写着 `SEGMENT_SUBMIT_UNCERTAIN:transport_fault:TIMEOUT`，不是又一个笼统的 UNKNOWN |
| `_submission_pending_reconcile(journey)` | 只对 `status==unknown` 且带模糊标记的旅程为真 |
| `_submit_segment` 的 `owner_status=="uncertain"` 分支 | 不再是 `blocked`；转交 `_uncertain_submission` |
| `_uncertain_submission(...)` | ① 先 `_reconcile`：同 execution_id、origin/destination 都匹配的 `IN_PROGRESS` 记录 → 认领为 `started`（travel_id/started_at/expected_arrival 全部取自 owner 记录），旅程 `in_transit`，**绝不直接认到达**；② `arrived` → 交回 `_step` 去确认该段；③ 证据为 `blocked`（外来在途/结算失败）→ 仍然 `blocked`；④ 无记录/读不到/只有别人的在途 → 旅程 `unknown`（非终态）+ 标记，**本次调用内不再提交** |
| `_step`（`not_started` 之后、过暂停闸门之前） | 带模糊标记的段只对账，不提交 |
| 模块 docstring 故障窗口 | 增补 ⑤：回执丢失时保持非终态、只对账、绝不重发、绝不换 execution id |

`INTENT_BUILD_FAILED` / `INTENT_SUBMIT_UNAVAILABLE`（端口证明没越过 owner 边界）也走
`uncertain` 入口，但标记是 `SEGMENT_SUBMIT_NOT_SENT`，不阻断下一次推进；`rejected` /
`disabled` / `failed` / `noop` 仍按原语义进 `blocked` / `unknown`。

## 3 合同（现在时）

- **提交只发一次**：每次模糊提交，客户端最多 1 次 `submit_action` + 1 次只读
  `get_action_result`（测试断言 `server.count("submit_action")==1`、
  `client.metrics.submitted==1`、owner 账本 `attempts==1`）。
- **超时默认**：读 `2.0s`（`DEFAULT_READ_TIMEOUT`），提交 `15.0s`
  （`DEFAULT_MUTATION_TIMEOUT`，> owner 的 10s `_commit_lock`）。两者可分别覆盖。
- **归因规则**（世界引擎侧）：

| 端口 `status` | 归因 | 旅程状态 |
|---|---|---|
| `applied` | 提交成功 | 段 `started`，旅程 `in_transit` |
| `uncertain`（可能越过边界） | 先对账；有匹配证据 → 认领，否则保持疑虑 | `started/in_transit` 或 `unknown`（非终态） |
| `uncertain` + `INTENT_BUILD_FAILED` / `INTENT_SUBMIT_UNAVAILABLE` | 没越过边界 | `unknown`，下一次推进可重试 |
| `rejected` / `disabled` / `failed` | 真拒绝 | `blocked`（终态，不再命令 owner） |
| `noop` | 位置已变 | `unknown`（`SEGMENT_POSITION_CHANGED`） |

- **不迁移旧账本**：已经写成 `blocked` 的旧 fixture 不自动解冻（没有模糊标记就无法区分
  "真拒绝"和"回执丢了"）；本批**没有**改动真实旧 fixture 的任何字节，所有新夹具都落在
  `/tmp/nyaw05r.jdK3q/owner-unk-*`、`/tmp/w05u-neg-*`、`/tmp/w02-*`（全部保留，未删除）。

## 4 模糊恢复与 pause（实测行为）

| 场景 | 结果 | 证据 |
|---|---|---|
| 端口 uncertain + 无任何证据 | 旅程 `unknown`，`reason_code=SEGMENT_SUBMIT_UNCERTAIN:transport_fault:TIMEOUT`，位置不动、无 travel 记录 | `test_ambiguous_submission_without_evidence_is_unknown_never_blocked` |
| 之后再推进 2 次 | 仍 `unknown`，**submit 调用数恒为 1**，execution_id 不变 | `test_repeated_advances_reconcile_only_and_never_resubmit` |
| 同 execution_id 的 travel 出现 | 认领为 `started`，`travel_id/started_at/expected_arrival` 全部来自 owner；位置仍在起点（不认到达）；submit 数 1 | `test_an_uncertain_submit_claims_the_same_execution_travel_record` |
| 别的 execution 在途 | `blocked` / `EXTERNAL_TRAVEL_IN_PROGRESS`（拒绝，不挪用） | `test_a_mismatched_execution_id_is_never_claimed` |
| 同 execution_id 但 origin/destination 不符 | 不认领，保持 `unknown`，submit 数 1 | `test_a_same_id_record_with_the_wrong_origin_is_not_claimed` |
| 真 rejected / disabled / INTENT_BUILD_FAILED | `blocked`，终态后不再命令 owner | `test_an_explicit_rejection_still_blocks`、`test_a_disabled_or_pre_submit_failure_still_blocks` |
| pause 后迟到证据出现 | 允许认领已出发的段（`in_transit`），**不**启动新段（后续推进 `paused`），submit 数恒为 1 | `test_a_paused_journey_claims_a_departed_segment_but_starts_nothing` |
| 真实本地服务、真实 owner 提交 + 迟响应 | 客户端 0.3s 放弃等待；一次只读查询拿到 `SUCCESS`；gateway 只收到 1 次提交，账本 `attempts=1`，travel 恰好 1 条 | `tests/test_world_body_slow_owner_verdict.py` |

真实服务那条用的是 `WorldBodyService` 自己的循环（真 `gateway.sock`、真授权租约、真
Resolver、真 travel 记录），响应由**事件屏障**挂住：先让真实提交与账本 `committed` 落盘，
再由 watchdog 在客户端放弃之后释放屏障 —— 不是 sleep 竞速，也不联网、不用真实模型。

## 5 命令与真实 returncode

runner：`$PI_SCRATCH_DIR/world-daily-owner-unknown/run_tests.sh`（每条用独立进程，
打印真实 `returncode`，不串管道）：

```
journey_ambiguous  rc=0  Ran 8 tests  OK
slow_owner_verdict rc=0  Ran 2 tests  OK
bridge             rc=0  Ran 41 tests OK   # 含 4 条新增，旧 TIMEOUT 用例按新语义精准更新
intent_executor    rc=0  Ran 7 tests  OK
journey_regression rc=0  Ran 28 tests OK   # tests/test_world_journey.py 原样全绿
move_tool          rc=0  Ran 16 tests OK
```

已知未跑：全部 World/Life 套件、旧 155/179/184 用例（本批范围外，按施工单只跑定向）。

## 6 原误诊更正（保留历史，不删旧文）

`docs/engineering/world-daily-i-r1-result.md` §6.1/§6.2 把这条 UNKNOWN 归因为
"若干次出发落在同一个墙钟秒内、而结算时钟在它们前面"。**这个归因不成立**：

- fixture 里那一次失败与"同时多段"无关 —— 时间线是 `auth 13.254 → consumed 13.262 →
  client timeout 15.26 → owner 提交 15Z`，全程只有**一次**提交、一条 travel 记录，
  同一个 execution_id；失败点是客户端 2.0s 预算赢不过 owner 的提交耗时，而不是账本撞秒。
- 计数旁证：遗留 118 次提交里 APPLIED 111 / UNKNOWN 6，其中 5 次是
  `transport_fault:TIMEOUT`、1 次 `POSTCONDITION_FAILED`；带 UNKNOWN 的 fixture 都留下
  blocked/pending 的旅程 —— 与"回执丢失被当作拒绝"完全一致，与"撞秒"没有必然关系。
- 旧文保留作为历史记录；本条更正取代其结论。§6.2 的"多段长链不稳定"是同一缺陷在不同
  段数上的重复表现，不是独立问题。

## 7 未做 / 边界（不夸称）

- 没有改 `components/life/scripts/world_journey_action_adapter.py`（另一位 writer 的独占文件）。
  端口的 `uncertain` 语义已由本批的"模糊提交"分支接住；`failure_class/detail` 现在从
  `ClientResult` 一路到 `reason_code`。
- 没有实现旧 `blocked` 账本的自动迁移：没有模糊标记就无法把它和真拒绝分开。恢复路径只有
  "owner 自身证据"（本批的反事实实验见 scratch `unk-probe/probe_recover.py`）。
- 没有改动真实旧 fixture（`/tmp/nyaw05r.jdK3q/w05inst-*` 等）的任何字节，也没有删除任何
  夹具；scratch runner 不做 cleanup。
- 墙钟依赖（adapter 调 `accept_journey/advance_journey` 不传 `now`）仍是独立问题，本批未碰。
