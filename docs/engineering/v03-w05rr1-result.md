# W05R-R1 回执：两项定点返修（暂停锁跨模型等待 / 合法生活后续被永久前台挡住）

- 候选：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），**UNCOMMITTED**
- HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）｜branch：`work/v03-w01-navigation`（未移动）
- `git diff --check`：exit 0（本轮多次复核，见 manifest 头）
- 本回执是**新文件**：`docs/engineering/v03-w05rr1-result.md`。原 `v03-w05r-result.md` 与 W01–W05 全部回执保持字节未动。
- 施工模型/真实模型：本轮**没有**调用真实模型、没有 24h 长跑、没有外网验收、没有生产 profile/凭据、没有部署/装服务、没有删除、没有 commit/push。CLI 演练里的"模型"是 loopback 替身。

---

## 0. 开工核对与备份（先核后改）

| 项 | 值 |
|---|---|
| HEAD / branch | `7f09d26df64e054faf51ba35ad85c64f73a5a837` / `work/v03-w01-navigation` |
| `git diff --check` | exit 0 |
| 无人并发 | 无新增文件、无他人 writer；`git status --porcelain` 与父级封存记录逐行一致（无 R1 回执、无新增日志） |
| 拟改文件开工 SHA256（= 父级封存值，逐字节相同） | `life_pause.py` `f7fed0bd255fc6d5987ae92962e95a3f33db74c91fc3ede3162dd4ff283b561b`；`autonomous_life_cycle.py` `de247589d3a5c2daf5c1b58acad9ee0687be8c235376632fc65ba3896847fb12`；`life_integration_ag2.py` `eef81821bc51eeff29d5f9c956ff68767200e8058f282bff442c62f1d04b1824`；`tests/test_one_day_life_w05r.py` `ee0cd6173e68b21833c804c7975e883023ae76e46a480b16c007463197dc6463` |
| 备份 | 上一步骤的 `w05rr1-backup-20261016-140456`（scratch）保留未动，SHA 与上表一致，本轮未覆写 |
| 隔离旧树 | `/tmp/nyaw05r.jdK3q/w05rr1_neg_tree`（会话授权唯一 WSL 短根内），由父级封存 `product/` 27 项按仓内路径铺回，冻结 SHA 复核一致 |

---

## 1. R1-A：暂停确认边界不再跨模型等待 —— **PASS**

### 1.1 确定证据（原缺陷）
- 旧 `autonomous_life_cycle.py:713–733` 用 `life_pause.commit_boundary` **包住整轮** `run_decision_and_adoption_cycle`（其内部 `life_integration_ag2.py:4625` 调认知、`4831` 才物化内容 step），因此决策/内容网络等待期间共享锁一直被持有，操作者 `set_paused` 的独占 flock（`life_pause.py:149–150`）被无限期排队；旧 `tests/test_one_day_life_w05r.py:513–557` 反而把"模型卡住时 pause 不能确认、内容/owner 提交完成后才确认"断言成 PASS。

### 1.2 改了什么（最小拆分，不移动大锁）
- `life_pause.py`：新增 `PauseConfirmedError`（带 `reason_code`/`stage`）与 `BoundaryLease`/`boundary_lease()` —— **显式**取得/释放共享确认锁，用于"只跨一次有限 owner 调用"；模块文档写明哪类等待可以进边界、哪类不可以。
- `autonomous_life_cycle.py`：
  - 删除包住整轮的 `commit_boundary`；改为**每个外部动作之前**重新读位：①请求启动前（`_pause_hold()`，有界共享锁、失败即 fail closed，报 `pause_boundary.held_after="before_request"`）；②决策提交后、采纳/内容之前（`_pause_gate_hook` 经 AG-2 既有 `pre_adoption_mutation_hook`，stage `after_decision_before_adoption`）；③内容请求返回后、AR-0 propose 之前（既有 `fault_hook("after_step_spec_before_action_propose")`，stage `after_content_before_propose`）；④owner 调用之前（既有 `fault_hook("before_adapter_call")`）——此处用 `BoundaryLease` **把共享锁跨过这一次有限 owner 调用**，`after_adapter_call` 释放，`evaluate_once` 的 `finally` 兜底释放（任何抛出都不会把操作者锁死）。
  - 已开始的请求允许结束，但结果不得落到任何 owner：命中边界 → 该轮报 `PAUSED_BY_OPERATOR`，`llm_calls=1`，并带 `pause_boundary`。
  - PORT_DIRECT 路径（`_commit_next_version`）同样：内容请求不持锁，owner `commit_version` 在租约内（失败即不写）。
  - `settle_world_journey` 的段提交保持原有边界（段间/下一段仍被同一确认语义覆盖）。
  - 预算不变：决策/内容/重试每个实际 attempt 仍在**发送前**持久预留、不返还（`life_request_budget`，未改）。

### 1.3 必验（事件屏障，独立进程确认，不用 sleep 猜）
`tests/test_one_day_life_w05r.py::PauseBoundaryTest`（14 tests OK）新增/改写：
- `test_a_pause_confirmed_while_the_decision_request_is_in_flight_stops_the_round`：把这一轮按在**决策请求**里 → **另一个进程**（`subprocess` 跑 `life_pause.set_paused`，退出码即证据）在 `release` 之前 join 完成 → 放行后该轮 `PAUSED_BY_OPERATOR`、`held_after=after_decision_before_adoption`、**内容写入器 0 次调用**、作品 head 未动、无 journey/travel 文件；确认后再跑一轮仍零新决策。
- `test_a_pause_confirmed_while_the_content_request_is_in_flight_writes_nothing`：按在**内容请求**里 → 同样独立进程先确认 → 放行后 `held_after=after_content_before_propose`、内容请求确实发生过 1 次、**head 未动、无 action_id、无 owner 提交**。
- `test_a_pause_confirmed_after_the_entry_gate_starts_no_request`：入口 gate 已过、观察还在读时确认 → `held_after=before_request`、`llm_calls=0`、**模型调用 0 次**、head 未动。
- 旧"模型卡住时 pause 不能确认"的断言已删除（原单要求改成原合同的正向断言）。

### 1.4 隔离旧树负控（证明这些断言不是空跑）
`w05rr1_neg_control.txt`：把**新测试**放到冻结产品树上跑 `PauseBoundaryTest`+`LegalLifecycleTest` → **5 FAIL / 17 继承用例 OK**，失败原因逐条正确：
`the round finished before its request was released`（决策/内容两例）、`None != 'before_request'`（入口 gate 例，旧产品只有 `life is held at the commit boundary` 且无 `pause_boundary`）。

### 1.5 真 CLI 证据（R1-A）
`w05rr1_cli_rehearsal.json`（44 项 checks 全 OK，exit 0，196s；真 CLI 子进程 + 真 Supply socket + 真 World gateway + loopback 替身）：
- 暂停窗**本来就应该到期**（`status=PAUSED_BY_OPERATOR`，非 NOT_DUE），`llm_calls=0`，**loopback 实收请求数不增**、作品 head 不变；
- 恢复后是**合同内的真实决定**：`decision=CONTINUE`、`outcome=ACTED`、owner 版本 2→3（不是 `BLOCKED`/`ADOPTION_REJECTED`）；
- restart 继续同一 run window（`run:w05rr1-A`），loopback 实收 9 次 = A 账本 4 + B 账本 5（逐项对账）；report 判读 `NOT_PASS`。

---

## 2. R1-B：合法生活后续不再被永久前台挡住 —— **PASS**

### 2.1 确定证据（原缺陷）
- 世界 Activity 的 `ActivityAdoptionSpec` 只有 `progress_contract_spec`/`result_binding_spec`，**没有完成合同**：到达只推进 checkpoint，Activity 永远 ACTIVE，前台永不释放；CLI `resumed_round`/`next_round` 因此都落到 `BLOCKED/ADOPTION_REJECTED/CANNOT_START_WHILE_FOREGROUND_ACTIVE:actv_*`，而原 35/35 harness 只要求"又一次决策"就放过了。

### 2.2 改了什么（只接既有合同，不造新语义）
- `autonomous_life_cycle.py::_world_adoption_spec(opportunities, *, decision=None)`：补上 LR-5 既有的**完成合同** —— `criteria_kind=CRITERIA_WORLD_FACT_MATCH`、`fact_key=world_place_ref`、`expected_value=world_fact:place:<决定真正选中的地点>`、`required_evidence_kinds=["WORLD_CANONICAL_OBSERVATION"]`、`required_authority_domains=["world_authority"]`、`policy_version=POLICY_WORLD_FACT_V1`。目标地点来自**已提交 Decision 自己的候选记录**（`_chosen_place`，与 `_world_step_spec` 同一判别），不是模型再说一遍；没有 Decision 可指名时不发明合同（保持旧行为）。
- `_record_world_progress`：证据里增加 `world_place_ref=world_fact:place:<place>`（不带 revision —— 采纳时还不知道确认到达那次的 revision）。到达 → LR-5 `COMPLETION_ELIGIBLE` → `auto_apply_natural_completion`（生产 runtime 已开）→ 经既有 ActivityCommandService 把 Activity **COMPLETED**，前台释放。
- 采纳形状改为**决策绑定**（`_RoutedAdoptionSpec` 的 world 分支改为 `Callable[[Decision], ...]`，单路世界分支用新的 `_DecisionBoundAdoptedSpec`）：AG-2 本来就接受可调用 `adoption_spec`，没有第二套 coordinator/writer。
- **没有**放宽任何校验：AG-2 §6 `CANNOT_START_WHILE_FOREGROUND_ACTIVE` 保持；没有自动清前台；没有"Action 成功即 Activity 完成"；没有"出发即到达"；NO_ACTION/DEFER/rest 仍合法。

### 2.3 必验（同实例两条分支 + owner 证据）
`tests/test_one_day_life_w05r.py::LegalLifecycleTest`（8 tests OK，含 6 个继承用例）：
- `test_a_real_arrival_legally_completes_the_world_activity_and_frees_the_foreground`：同一实例同一候选集（作品 + 已观察地点）→ 世界分支真实出发（`AR0_WORLD_ROUTED`/`IN_TRANSIT`，位置仍原处、作品未动）→ 到期 settle → **世界 Activity `COMPLETED` 且有 `completion_evidence_ref`**、`foreground_activity_ref=None` → 下一轮合法 `START` 作品分支并**真的写出新版本**（head 变化）。
- `test_an_illegal_second_foreground_start_is_still_refused`：前台还 ACTIVE 时坚持 START 必须被拒（AG-1 逐候选矩阵 `DECISION_CANDIDATE_INCOMPATIBLE` 或 AG-2 §6 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`），且**作品没有第二个版本**。

### 2.4 真 CLI 证据（R1-B，同实例）
`w05rr1_cli_rehearsal.json`（同一实例 B）：
- 出发：`IN_TRANSIT`，`travel` 记录来自真 gateway，`expected_arrival_at` 存在，位置仍 `home`（出发≠到达）；
- 到达：真等到期（实等 **122.4s**）后由引擎自己的 `tick -> settle_journey` 判定 `arrived=true`、位置 `home→street_1`、`progress_state=COMPLETION_ELIGIBLE`；canonical life state 里世界活动 `actv_bf6650d0f842dad6` **status=COMPLETED**、`completion_evidence_ref=world_fact:place:street_1:r2`；
- 到达之后的合法后续：**同一个 tick** 的考虑窗合法 `START` 作品分支，`ACTED`，owner 版本 1→2（detail `owner committed artifact-version:...`）；再走一轮 `CONTINUE` → `ACTED` 2→3。**没有任何一轮是 BLOCKED/ADOPTION_REJECTED/DECISION_FAILED**（脚本对预期的生活轮次显式拒绝这三类结果，不把 exit0 当业务成功）。

### 2.5 隔离旧树负控
同一份新测试在冻结产品树上：`'ACTIVE' != 'COMPLETED'`（R1-B 主用例）、`'DECISION_FAILED' != 'ADOPTION_REJECTED'`（反例）—— 即旧产品确实永远不释放前台。

---

## 3. 原五项保留 / 重验范围

| 原项 | 状态 | 本轮证据 |
|---|---|---|
| §1 真正 CLI 未执行 | **保留 + 重验** | 新 `w05rr1_cli_rehearsal.json`：真 CLI 子进程、真 Supply/World gateway、版本+1 且读回内容差异、计时出发/到达、下一次选择、到期 pause 零提交、restart/resume、实收请求与账本对账；并**新增**对 BLOCKED/ADOPTION_REJECTED/DECISION_FAILED 的显式拒绝 |
| §2 作品固定优先架空 | **保留**（本轮未改，回归覆盖） | `RoutingTest`（含同候选集两分支、真实历史改变下一轮输入）随 73 项 W05R 套件 exit 0；新增 R1-B 用例把"世界分支→合法完成→作品分支"接成同一实例的一条链 |
| §3 World 正式授权 | **保留**（本轮未改 `world_journey_action_adapter.py`/gateway） | 同上套件 exit 0；CLI 演练里 MOVE 仍经正式入口（travel 记录 + `expected_arrival_at`），出发/到达/位置证据与 W05R 一致 |
| §4 pause 确认边界 | **本轮重做**（见 §1） | 旧断言删除，新三例（决策/内容/入口 gate）+ 独立进程确认 + 旧树负控 |
| §5 模型总调用预算 | **保留**（本轮未改） | `RequestBudgetTest`（11 例）随 73 项 exit 0；CLI 演练 restart 同一 `run_id`、账本=实收请求数 |

---

## 4. 回归与冻结（同一冻结树）

| 组 | 命令（`$PY` = scratch `w04venv/bin/python`，3.12.3） | 结果 |
|---|---|---|
| 01 world_journey | `tests: unittest discover -p test_world_journey.py` | 28 tests OK |
| 02 world_move_tool | 同目录 | 16 tests OK |
| 03 move_tool_navigation | 同目录 | 14 tests OK |
| 04 world_runner | 同目录 | 26 tests OK |
| 05 move_pause_race | 同目录 | 3 tests OK |
| 06 w05_one_day | 同目录 | 17 tests OK |
| 07 w05r_new | 同目录 | **73 tests OK**（438.9s；基线 63，新增 10） |
| 08 worldgen_3_modules | `components.worldgen.tests.test_v04_navigation / test_edited_world_runtime / test_runtime_wiring` | 24 tests OK |
| 09 world_service | `components/world/service` discover | 259 tests OK |
| 10 whole_repo_tests | `tests` discover `test_*.py` | **251 tests OK**（592.8s） |
| 11 supply_owner | `components/supply` discover `-s lifesupply/tests -t lifesupply/tests` | 122 tests OK |
| 12 life_discover | `components/life` discover | Ran 0（与基线一致；**不当作 PASS**） |

- Life 精准离线入口（非空目录 discover 的替代覆盖）：`components/check_life_v02.py` 等既有入口本轮未改产品代码所依赖的接口；受影响面由 06/07/10/11 组与真 CLI 演练覆盖。需要真实服务/实例才能跑的项（外部 owner harness）本轮**未跑**，替代覆盖 = `tests/test_one_day_life_w05r.py` + 真 CLI 演练（两者都跑真 Supply/World owner）。
- 日志：`w05rr1_final_regression.txt`（12 组）、`w05rr1_rerun_after_stubfix.txt`（07/10 在替身合同修正后的复跑）、`w05rr1_w05r_rerun_1.log`/`_2.log`（07 全量日志）、`w05rr1_rerun_test_one_day_life_w05r_py.log`、`w05rr1_rerun_test____py.log`、`w05rr1_neg_control.txt`、`w05rr1_cli_run.txt`、`w05rr1_cli_rehearsal.json`、`w05rr1_manifest_frozen.txt`、`w05rr1_manifest_after.txt`、`w05rr1_history_check.txt`。
- 冻结 manifest：`w05rr1_manifest_frozen.txt`（28 项，含 sha256/bytes/mtime/HEAD/branch/diff-check）。收尾复核（`w05rr1_history_check.txt`）：`manifest_frozen == manifest_after` **逐行相同**；HEAD/branch/diff-check 未变；W01–W05 与 `v03-w05r-result.md` 六个历史回执 SHA 与父级封存值**逐字节相同**；本轮未触碰的封存产品文件（`life_integration_ag2.py`/`one_day_life.py`/`action_reality_ledger.py`/`life_request_budget.py`/`autonomous_life_assembly.py`/`autonomous_life_resident.py`/`world_journey_action_adapter.py`）SHA 仍等于封存值。本回执是冻结后新增的文档（不在 manifest 列表内，其 SHA 见 `w05rr1_history_check.txt`）。

### 4.1 本轮改动文件（相对开工）

| 文件 | 变化 |
|---|---|
| `components/life/scripts/life_pause.py` | 文档重写边界语义；新增 `PauseConfirmedError`、`BoundaryLease`、`boundary_lease`、`__all__` 更新（`f7fed0bd…` → `52658bf155c5d92504fcdd8a4d26d36d61072d2ea9c9a8766a55b27b1cab4684`，18520B） |
| `components/life/scripts/autonomous_life_cycle.py` | R1-A 边界拆分 + 三个 hook + 租约；R1-B 完成合同 + `world_place_ref` + 决策绑定采纳形状（`de247589…` → `8195a14518e1a9d426c6590ce907fa6249cd71ab5bb330af46884f272f2dd57b`，94791B） |
| `tests/test_one_day_life_w05r.py` | 旧 pause 断言改为正向三例 + `LegalLifecycleTest` 两例 + `BlockingWriter`/`BlockingObservation`/独立进程确认助手（`ee0cd617…` → `63a2a8d314bee569538508ce633c140a2da492ad3a5eb4f240dff875a8c4ddb1`，54592B） |
| `tests/test_one_day_life.py` | 夹具修：`tearDown` 在实例树被删前 `runtime.close()`；loopback 替身改为**只发该候选自己允许的决定**（读逐候选 `legal_decisions`）（`7373c5f7808ef6dcbeedfd8f0566d50fd126674a1cf7450b68885549ea51cbba`，47773B） |
| `docs/engineering/v03-one-day-runbook.md` | 暂停确认边界与世界活动收尾两段（`e1c823659a91da777443df9cd71bb6b265583f5f6caff2d1d57b9b4859245ab6`，9073B） |
| `docs/engineering/v03-w05rr1-result.md` | 本回执（新） |

未改（与父级封存值逐字节相同，可复核）：`life_integration_ag2.py` `eef81821…`、`one_day_life.py` `0176b9ed…`、`action_reality_ledger.py` `46c09c46…`、`life_request_budget.py` `bdbfdd51…`、`autonomous_life_assembly.py` `7eb6ad29…`、`autonomous_life_resident.py` `e5ce3525…`、`world_journey_action_adapter.py` `da67337b…`、`chiyo_bundle/*`、`components/world/*`、`scripts/life_heartbeat.py`。

---

## 5. 如实报告的残余与未做项

1. **AG-0 机会窗口的秒边界竞态（既有，未修，不在写集）**：`candidate_sources_ag0.py:2297` 用 `obs_dt < valid_from` 判 `OPPORTUNITY_NOT_YET_OPEN`，而观察缝记录的 `valid_from` 是"读世界的那一刻"，可能比这一轮自己更早算出的 `observed_at` 晚不到一秒 → 同一轮里刚记录的机会偶尔不成为候选。生产里 resident 每 tick 先 `settle_journey`（含观察）再开窗，影响面小；本轮**没有**改它（属于扩域），只在 R1-B 用例里显式先记录一次观察，并把该竞态写进测试注释与本节。
2. **AG-1 与 AG-2 对 START 的一致性问题（既有，未修）**：前台 ACTIVE 时 AG-1 仍可能为某些创建型候选提供 START，而 AG-2 §6 会正确拒绝 —— 于是"坚持 START 的模型"会得到 `ADOPTION_REJECTED`。这是**合同行为**（拒绝、零副作用），不是被映射成成功；R1-B 的 CLI 演练因此使用"继续手上的事"的替身模型（读 `legal_decision_enums` 后选 CONTINUE），并把 `CANNOT_START_WHILE_FOREGROUND_ACTIVE` 明确列为**不得当通过**的结果。
3. **测试夹具两处修正（属测试，已列在上表）**：①`OneDayTestCase.tearDown` 里先关 runtime 再让基类删实例树（否则 R1-B 让进度存储变脏后，`close()` 会写向已删除目录而报错 —— 该现象已在冻结树上复核为"基线不触发、本轮触发"，属夹具顺序问题）；②loopback 替身改为逐候选守合同（原实现只看整次请求的 union，会硬塞非法 START，正是原单"harness 误放过"的同源问题）。
4. **未跑项**：外部 owner harness、真实模型、24h、外网、生产 profile、部署 —— 全部未跑，理由同上（未获授权）。`components/life` 目录 discover 仍为 0 项，**不当作 PASS**。
5. 本回执写完后**停止**，等父级核验；没有 commit/push。

---

## 6. 复现命令（scratch 脚本，均为新文件）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
bash $S/w05rr1_neg_tree.sh          # 铺冻结旧树（父级封存 product/ 27 项）
bash $S/w05rr1_neg2.sh              # 负控：新测试 × 冻结产品 → 5 FAIL / 17 OK
bash $S/w05rr1_reg.sh               # 12 组回归（w05rr1_final_regression.txt）
bash $S/w05rr1_rerun2.sh            # 07/10 组在替身修正后的复跑（全绿）
bash $S/w05rr1_py.sh $S/w05rr1_cli_rehearsal.py   # 真 CLI 演练（w05rr1_cli_rehearsal.json）
bash $S/w05rr1_manifest.sh          # 冻结 manifest
```
