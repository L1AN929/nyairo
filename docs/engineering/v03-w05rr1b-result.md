# W05R-R1b 回执：关闭请求启动竞态（R1-A 补完）+ 最终证据重做

- 候选：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），**UNCOMMITTED**
- HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）｜branch：`work/v03-w01-navigation`（未移动）｜`git diff --check` exit 0
- 本回执是新文件 `docs/engineering/v03-w05rr1b-result.md`；`docs/engineering/v03-w05rr1-result.md`（R1 回执）与 W01–W05R 全部回执**保持字节**（SHA 见 §8）。
- 范围：只补父级复现的**两个请求启动竞态**，不重开架构审查、不动 AG-1 决策政策、不造新 owner。R1-B（到达→合法完成→另一分支）**保留**，其 CLI 证据本轮重跑（新输出/新时间）。
- 未做：真实模型、24h、外网验收、生产 profile/凭据/provider 改动、部署、删除、commit/push、子委派。

---

## 1. 父级复现的两个窗口（本轮直接跑父级脚本，未改其脚本/日志）

父级脚本/日志：`parent_r1_pause_probe.py` / `.sh` / `.log`（scratch，未覆盖）。父级结果：**16.417s，2 FAIL** —— 两轮 `outcome=PAUSED_BY_OPERATOR`、owner head 不变，但"确认之后仍有 1 次新请求"：

| 探针 | 屏障位置 | 复现结果 |
|---|---|---|
| 1 | `_pause_hold()` 返回（锁已释放）之后确认 pause | `cognition_calls_after_confirmation=1` |
| 2 | `_pause_gate_hook()` 检查返回之后确认 pause | `content_calls_after_confirmation=1` |

根因（R1 交付的源码）：确认点只是"读位"，而真正的发送在更后面 —— 源码 769/777（`_pause_hold` → `coordinator.run_decision_and_adoption_cycle`）与 1287（`_pause_gate_hook` 返回）之后，lazy content / 认知请求仍会启动。R1 只在**请求返回后**重新校验，没有在**请求启动边界**协调。

**本轮复跑父级脚本（未修改，新日志 `w05rr1b_parent_probe_rerun.log`）：`Ran 2 tests … OK`，EXIT=0**

```
{"probe": "pause_after_final_check_before_decision", "pause_confirmed": true,
 "cognition_calls_after_confirmation": 0, "outcome": "PAUSED_BY_OPERATOR",
 "detail": "life was held before_decision_request: LIFE_PAUSED", "owner_head_unchanged": true}
{"probe": "pause_after_adoption_check_before_content", "pause_confirmed": true,
 "content_calls_after_confirmation": 0, "outcome": "PAUSED_BY_OPERATOR", "owner_head_unchanged": true}
```

---

## 2. 产品改动（最小，只关这两个窗口）

| 文件 | 改动 | 理由 |
|---|---|---|
| `components/life/scripts/life_pause.py` | 新增 `admit_request(state_root, *, stage)`：**请求启动握手** —— 取共享确认锁 → **持锁**重读位（命中或争用即 `PauseConfirmedError`，fail closed）→ **释放锁**后返回；`__all__` 同步 | 把"确认"与"请求启动"在发送边界线性化；响应等待期不持任何 pause/shared/control 锁 |
| `components/life/scripts/autonomous_life_cycle.py` | ①`_admit_request(kind)` ②`_pause_fault_hook` 处理 AG-1 既有 `before_cognition`/**`during_cognition`**（每次 attempt，含 schema retry）→ 决策启动握手 ③新增 `_PauseAdmittedWriter`：内容请求在 `writer.next_content` 之前过握手（内容启动边界） | 决策与内容各自在**实际发送前**过握手，不再靠上层读位 |
| `components/life/scripts/life_request_budget.py` | `BudgetedCognitionAdapter` / `BudgetedModelClient` 增加可选 `admission`，在**持久预留之后、transport 调用之前**执行 | 真实 transport 的**每 attempt** 发送边界（决策/retry/内容）；预留仍"发送前落盘、不返还" |
| `components/life/scripts/autonomous_life_assembly.py` | 新增 `pause_request_admission(state_root)`；`_model_writer`/`model_cognition`/`assemble_autonomous_life` 接受并默认构造它（从 Life state root，即操作者 `pause` 写的同一个位） | 复用既有 transport 注入点，不新增第二套 coordinator/writer |
| `components/life/scripts/autonomous_life_resident.py` | 一行：`model_cognition(..., admission=assembly.pause_request_admission(self.state_root))` | **写集最小扩写的精确原因**：真实认知 adapter 由 resident 构造（`autonomous_life_resident.py:339`），assembly 拿不到它；不给它传 admission，则 CLI/常驻路径的决策请求仍只在 AG-1 hook 处协调、无法落到 transport 包装器。除此之外未动 resident 任何逻辑 |

**语义（写进代码注释与 runbook）**：握手是请求启动的线性化点。确认在握手**之前**返回 ⇒ 该请求不启动（父级两探针即此）；确认在握手之后 ⇒ 该请求算"已开始"，允许结束，但其结果仍必须过 R1 的请求后校验（决策后 / 内容后 / owner 前）才能落到任何 owner；owner 调用仍在有限租约内（`BoundaryLease`），网络**响应**等待从不持 pause/shared/control 锁。`set_paused` 的独占锁保证"已返回的确认"不会被任何后续握手漏掉。仍保持：fail closed、单 writer、真实 owner、预算每 attempt 发送前持久预留不返还（`admit_request` 在 `reserve` 之后，被拦的 attempt 保留自己的预留）。

---

## 3. 定向测试（新增，进精准回归）

`tests/test_one_day_life_w05r.py::RequestStartBoundaryTest`（10 tests OK；4 新 + 6 继承）：

| 测试 | 断言 |
|---|---|
| `test_a_pause_confirmed_after_the_final_check_starts_no_decision_request` | 父级探针 1 树内化：确认后 `cognition.call_count == 0`、`PAUSED_BY_OPERATOR`、head 不变 |
| `test_a_pause_confirmed_after_the_adoption_check_starts_no_content_request` | 父级探针 2 树内化：确认后 `writer.call_count == 0`、`PAUSED_BY_OPERATOR`、head 不变 |
| `test_every_transport_attempt_is_admitted_and_the_reservation_is_kept` | 传输包装器：无暂停时 decision/retry/content 三次**实际发送**（`["decision:0","decision:1","content"]`）；确认后三次都被拦（stage 分别为 `before_decision_request`/`before_retry_request`/`before_content_request`）、**实收 0 次**、账本 `utc_day_calls` 各 +1 且无 `release`（不返还） |
| `test_a_pause_confirmed_while_a_finite_owner_call_runs_waits_for_it` | 有限提交竞态：真实 AR-0 owner adapter 的 `submit` 阻塞时，独立进程的 pause **不能**返回（共享锁被有限提交持有）→ 放行后该轮 `ACTED`、版本 +1 一次、pause 返回；其后零新决策/零内容/零提交（"pause-resume 不让旧在途结果无检查提交"） |

原有 R1 三例（决策/内容挂住时独立进程可确认、入口 gate 后确认）与 R1-B 两例继续通过（见 §4 组 07 = 83 OK）。

**隔离旧树负控**（`w05rr1b_neg_control.txt`）：把**新测试**放到**父级封存的 R1 产品**（`W05RR1-parent-20261016/product`，SHA 复核一致：`life_pause.py` `52658bf1…`、`autonomous_life_cycle.py` `8195a145…`、`life_request_budget.py` `bdbfdd51…`）上跑 `RequestStartBoundaryTest` → **EXIT=1，Ran 10，FAILED (failures=2, errors=1)**：两例探针断言 FAIL、传输准入例 ERROR（封存 R1 无 `admission` 参数），其余 7 例通过 —— 证明这两个窗口在 R1 交付里真实存在，且只被本轮产品改动关闭。

---

## 4. 回归（同冻结树，真实退出码，逐组完整 stdout/stderr）

`w05rr1b_reg.sh`：**不再用管道后 `$?`**（R1 的 `| tail | tr` 会把 FAILED 记成 0，是错的）；每组输出进 `w05rr1b_reg_<组>.log`，退出码直接取自 `unittest`。

| 组 | EXIT | 结果 |
|---|---|---|
| 01 world_journey | 0 | Ran 28 OK |
| 02 world_move_tool | 0 | Ran 16 OK |
| 03 move_tool_navigation | 0 | Ran 14 OK |
| 04 world_runner | 0 | Ran 26 OK |
| 05 move_pause_race | 0 | Ran 3 OK |
| 06 w05_one_day | 0 | Ran 17 OK |
| 07 w05r_new | 0 | **Ran 83 OK**（基线 63 + R1 10 + R1b 10） |
| 08 worldgen_3_modules | 0 | Ran 24 OK |
| 09 world_service | 0 | Ran 259 OK |
| 10 whole_repo_tests | 0 | **Ran 310 OK** |
| 11 supply_owner | 0 | Ran 122 OK |
| 12 life_discover | **5** | Ran 0 —— `components/life` 目录 discover 为空，**不当作 PASS**（与基线一致） |
| neg_demo（harness 非零演示） | **1** | 封存 R1 产品上跑 `RequestStartBoundaryTest` → `FAILED (failures=2, errors=1)` |

**测试计数分解（精确）**：父级冻结的 W05R 组 10 = 241 → R1 加 10（R1 测试类）= 251 → R1b 加 10（R1b 测试类）= 261 → 另有 **49** 项来自 `tests/test_body_capacity_source.py`（Body 代理留下的新文件，**非 R1/R1b 自有**，本轮未改未集成）= 310。组 07：63 → 73 → 83。

**精准 Life 离线入口（非空 discover 替代）**：`w05rr1b_check_life.py` 为每个 `components/check_life*.py` 起独立 Supply owner + 独立实例（真实 socket），逐项判 PASS/SKIP/FAIL（`w05rr1b_check_life.txt`/`.json`）：

| 检查 | 结果 |
|---|---|
| `check_life.py` | **PASS**（shadow wiring/restart dedup/second writer denied/lease released 等 7 项 ok） |
| `check_life_b7.py` / `check_life_b8.py` / `check_life_b8_6a.py` / `check_life_b8_causal.py` | **PASS**（各自结尾 `*_CHECK: PASS`） |
| `check_life_memory_v02.py` | **PASS**（`MEMORY_CHECK: PASS`） |
| `check_life_v02.py` | **FAIL**（exit 1）：`V02_CHECK: FAIL (3 assertion(s))`，失败项为 `M0 holds exactly one row for that source` / `the delivered row is the verified one` / `no duplicate memory experience` —— 即 **Memory 消费者/交付未接线的环境缺口**。同一检查、同一准备、**父级封存 R1 产品**上得到**逐行相同**的 3 项失败（`w05rr1b_check_life_neg.txt`），故**不是本轮产品改动引入**，也不谎报 PASS。 |

---

## 5. 真 CLI 演练（新输出、新时间，功能沿用 R1 成功项）

`w05rr1_cli_rehearsal.py` → **新输出 `w05rr1b_cli_rehearsal.json`**（真 CLI 子进程 + 真 Supply socket + 真 World gateway + loopback 替身）：

**46 checks 全 OK，EXIT=0，198.3s**（R1 为 44 checks/196.1s，本轮 +2 为逐 kind 对账）：

- 暂停窗**本来应到期** → `PAUSED_BY_OPERATOR`、`llm_calls=0`、loopback 实收不增、head 不变；
- 恢复 → 合同内真实决定 `CONTINUE`/`ACTED`，owner 版本 2→3；
- 世界分支：`IN_TRANSIT`（真 gateway travel 记录、`expected_arrival_at`），实等 **122.0s** 到期后由引擎自身 `tick→settle_journey` 判定到达（home→street_1），该 Activity **COMPLETED**（`completion_evidence_ref=world_fact:place:street_1:r2`）；
- 同一 tick 的考虑窗合法 `START` 作品分支并 `ACTED`（1→2），再一轮 `CONTINUE` 2→3；**没有任何预期生活轮次是 BLOCKED/ADOPTION_REJECTED/DECISION_FAILED**；
- 预算：A 账本 4 + B 账本 5 = loopback 实收 9；**逐 kind 对账** `instance_a {decision:2,content:2,retry:0}`、`instance_b {decision:3,content:2,retry:0}`、`loopback {decision:5,content:4}` —— 5=2+3、4=2+2（retry 如实为 0：替身不制造非法输出，不编数字）。

---

## 6. 冻结与前后核对

- 最终 product/test manifest：`w05rr1b_manifest_frozen.txt`，**生成 UTC `2026-10-16T11:07:37Z`**，含 28 个产品/测试/runbook 文件的 sha256 + bytes + mtime + HEAD + branch + diff-check。生成顺序：**先冻结，后跑** §4 回归与 §5 CLI。
- 收尾复算 `w05rr1b_manifest_after.txt`（生成 UTC `2026-10-16T11:57:13Z`）：**文件清单逐行相同**（唯一差异是 `generated_utc` 行）→ 回归与 CLI 期间产品/测试文件零改动。若今后改动测试/产品，必须重冻并重跑（本回执之后不再改）。
- 历史回执：`v03-w01/02/03/04/05-result.md`、`v03-w05r-result.md`、`v03-w05rr1-result.md` 当前 SHA 与父级封存值**逐字节相同**（`w05rr1b_history_check.txt`）。
- 本轮改动文件（SHA256 / bytes）：
  - `life_pause.py` `…`（见 manifest）｜`autonomous_life_cycle.py`｜`life_request_budget.py`｜`autonomous_life_assembly.py`｜`autonomous_life_resident.py`｜`tests/test_one_day_life_w05r.py`｜`docs/engineering/v03-one-day-runbook.md`（追加 R1b 边界说明）
  - 未触碰（SHA 仍等于封存值）：`life_integration_ag2.py`、`one_day_life.py`、`action_reality_ledger.py`、`autonomous_life_runner.py`、`world_journey_action_adapter.py`、`chiyo_bundle/*`、`components/world/*`、`scripts/life_heartbeat.py`、`tests/test_one_day_life.py`（本轮未改）
- 非本轮自有、保持不动的文件：`components/life/scripts/body_capacity_source.py`、`tests/test_body_capacity_source.py`、`docs/engineering/life-seed-body-capacity.md`（Body 代理遗留，未集成；组 10 的 +49 项计数属它们，不计入 R1/R1b）。

---

## 7. 如实残余

1. **握手与 send 之间的进程内窗口**：`admit_request` 释放共享锁到 transport 调用之间是同一线程的几微秒本地代码，不可再缩小而不把网络等待放进锁里。语义上握手即"请求已启动"的线性化点：确认在此之前 ⇒ 不启动（父级两探针即此）；在此之后 ⇒ 已启动，其结果仍受请求后校验拦截。R1 已把这个语义写进代码与 runbook，不再宣称"任何检查之后都算已启动"。
2. **AG-0 机会窗口秒边界竞态（既有，未修，超写集）**：`candidate_sources_ag0.py:2297` 的 `obs_dt < valid_from` 会让同一轮刚记录的机会偶尔 `OPPORTUNITY_NOT_YET_OPEN`；R1-B 用例显式先观察一次规避，本回执照实保留该说明。
3. **AG-1 与 AG-2 对 START 的一致性（既有）**：前台 ACTIVE 时 AG-1 仍可能为创建型候选提供 START、AG-2 §6 正确拒绝；CLI 演练把 `CANNOT_START_WHILE_FOREGROUND_ACTIVE` 明确列为**不得当通过**的结果，替身按 `legal_decision_enums` 选合法 CONTINUE。
4. **未跑**：外部 owner harness（需外部环境）、真实模型、24h、外网、生产 profile —— 未获授权。`components/life` discover 0 项与 `check_life_v02.py` 的 Memory 环境缺口都按上文如实区分，不算 PASS。

- 本轮改动文件（SHA256 / bytes，取自冻结 manifest）：
  - `components/life/scripts/life_pause.py` `ebfe552e37cacc2e2f2bddd932dd09195bf8c9431618ec946000c5a3bb82fe05` (19991B)
  - `components/life/scripts/autonomous_life_cycle.py` `4bf0ae8a04be696a353af72672476a974e7e6fb67f06776514df3a456b2dbf5d` (97384B)
  - `components/life/scripts/life_request_budget.py` `9c597da1b5b6351c6c112276b19a5a4176867688b9a3eaa34743ec2730fcf895` (20566B)
  - `components/life/scripts/autonomous_life_assembly.py` `a0971956ecfe00950c6932d224604e75ef0384c23262a51f00863b5a4188e011` (23777B)
  - `components/life/scripts/autonomous_life_resident.py` `7f378497797b62c54dc1c350d1e05eece3f50fb472b81ef53c187e569c4abb43` (39206B)
  - `tests/test_one_day_life_w05r.py` `fcb678658c40270cba733b74939fd167db594444925fe3355713e54f4b30b161` (63985B)
  - `docs/engineering/v03-one-day-runbook.md` 保持 R1 字节 `e1c823659a91da777443df9cd71bb6b265583f5f6caff2d1d57b9b4859245ab6` —— 本轮**未改**（其暂停确认边界一节描述"每个外部动作之前重新读位"，对发送边界握手同样成立；为不改动冻结清单，未在冻结后再编辑文档）。

---

## 8. 复现命令（scratch 新脚本）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
bash $S/w05rr1b_parent_probe.sh      # 父级两探针（未改其脚本）→ OK
bash $S/w05rr1b_neg.sh               # 新测试 × 封存 R1 产品 → EXIT=1（2 FAIL/1 ERROR）
bash $S/w05rr1b_manifest.sh          # 冻结 manifest（含 UTC）
bash $S/w05rr1b_reg.sh               # 12 组回归 + harness 非零演示（真实退出码）
bash $S/w05rr1_py.sh $S/w05rr1b_check_life.py   # 精准 check_life*.py 入口
bash $S/w05rr1_py.sh $S/w05rr1_cli_rehearsal.py # 真 CLI → w05rr1b_cli_rehearsal.json
bash $S/w05rr1b_final_checks.sh      # manifest 前后 + 历史回执 + HEAD/branch
```
