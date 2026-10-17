# W05R 回执：自主生活首轮未闭合缺口的收口修正

- 候选树：`D:\nyairo-v03-backend`，分支 `work/v03-w01-navigation`，HEAD
  `7f09d26df64e054faf51ba35ad85c64f73a5a837`（**未移动、未 commit、未 push**）。
- 开工基线：`w05r_start_dirty_sha256.txt`（33 项 dirty，逐项 SHA256）。收口时用
  `w05r_hist2.py` 复核：**28 项字节未动**，只有本单真正改的 5 个文件不同；
  历史回执 `v03-w01..w05-result.md` **全部字节不变**。
- 本单**不启动真实 24h、不接真实模型、不 commit/push/部署、不装服务**。世界移动用的是本仓库
  自己的 `world_body_service`（真 socket、真 gateway、真授权），模型是 loopback 替身。
- 本文件是**新**回执；`v03-w05-result.md` 及其首轮日志一字未改。

## 1. W05 首轮被父代理点名的缺口：根因与修正

首轮 CLI 的 `run_once_outcome=BLOCKED` 首轮未定位到根因。本单定位到**两个**具体原因，
都在"替身不守合同"，不在产品：

| # | 具体 reason | 位置 | 为什么产品是对的 |
| --- | --- | --- | --- |
| 1 | `INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE` | `agency_decision_ag1.py:498-502`（`validate_codes(..., allow_empty=False)`） | AG-1 的合同要求 `reason_codes` 至少含一个**已注册**码；首轮替身返回 `[]`，被正确拒绝 |
| 2 | `MODEL_EDIT_KIND_REFUSED:append` | `model_continuation_writer.py:39,202-203`（`ALLOWED_KINDS={append_paragraph,revise_tail}`） | 内容写入器的合同只认这两种 kind；首轮替身返回 `append` |

第三个（本轮才发现）是替身不看 `legal_decision_enums` 与候选的 `executable_now`，
于是给出被决策矩阵正确拒绝的组合：`INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`
（`agency_decision_ag1.py:2237-2245`，START 只对五类候选合法）。

修正全部落在**测试替身**（`tests/test_one_day_life.py::StubModel`），产品一行没让：
替身现在从**真模型看得到的同一份** `allowed_reason_codes` 里挑码、返回合同内的
`append_paragraph`、并尊重 `legal_decision_enums` / `executable_now` / 候选类别。
对应回归：`tests/test_one_day_life_w05r.py::DecisionContractTest`（10 项）。

## 2. 修改范围（本单新增/改动，全部未提交）

本单**真正改动**的 5 个文件：

| 文件 | 改动 |
| --- | --- |
| `components/world/scripts/world_action_resolver.py` | 新增 `_departure_evidence()` 与 MOVE 的**计时出发后置条件**：travel time 打开时，`apply_move_to` 提交的是"出发"，位置要到期才变，原来的"位置已等于目的地"后置条件永远不成立，会把一次真实已授权的出发报成 `uncertain/POSTCONDITION_FAILED`。新分支要求 owner 自己的在途记录存在且 execution_id/起点/终点一致，才记 `committed`（`reason_code=move_departed`） |
| `chiyo_bundle/world_journey.py` | `_submit_segment` / `_step` / `accept_journey` / `advance_journey` 新增可选 `submit_port`：给了就走调用方提供的**正式授权入口**，没给则保持 W04 原样（所以 W04 的 28 项旅程测试一字未动） |
| `components/life/scripts/world_journey_action_adapter.py` | 新增 `FormalOwnerMoveSubmitPort`（复用 `WorldBridgeResolver.build_intent` + `Intent Executor` + `gateway.sock`）；`JourneyWorldResolver` 默认走正式入口（`submission_route=formal`），`formal_submission=False` 才回退；新增 `DEFINITE_PRE_SUBMIT_REASONS`，把 `GATE_OFF`/`SHADOW_NO_COMMIT`/`INVALID` 等**提交前**拒绝如实报成 definite，而不是含糊的 `uncertain` |
| `components/life/scripts/one_day_life.py` | `prepare` 一定写入两个**总请求**上限 + `CHIYO_AUTONOMOUS_RUN_ID`（并可按 `--world-body-socket` 写入 World 传输）；`run` 新增 `new_run_window`；新增 `_report_request_budget()` 读持久账本；**修掉一个真缺陷**：Supply 的 operation key 原来不按 subject 区分，同一个 owner 上准备第二个实例必然 `CONFLICT operation key payload mismatch` |
| `tests/test_one_day_life.py` | 夹具新增**真实 World gateway**（`world_body_service` 在实例自己的 world home 上，真 socket/真 Resolver/真授权租约）；替身按合同修正（§1）；`instance_env()` 带上 World 传输 |

新增：`tests/test_one_day_life_w05r.py`（63 项）。
scratch 交付：`w05r_cli_rehearsal.py` / `w05r_cli_rehearsal.json`、`w05r_final_regression.sh`、
`w05r_final_run.sh`、`w05r_manifest.sh`、`w05r_neg_control.sh`、`w05r_hist2.py` 等。

W05 已有的 `life_pause.py`（含 `commit_boundary`/`pause_lock`）、`life_request_budget.py`、
`autonomous_life_cycle.py` 的路由（`_RoutedAdoptionSpec` / `_DecisionBoundStepSpec` /
`_routed_step_builder`）**由中断前的施工留下**，本单只补测试与接线（预算 env、报告读取），
未重置重做。

## 3. 逐项 PASS / NOT_PASS

### §1 真正 CLI 未执行 —— **PASS**

证据：`w05r_cli_rehearsal.json`，`ok=true`，35/35 必验项，12 个真 CLI 子进程，
用时 156s，**exit 0**；任一必验失败 exit 非零（本轮多次实际观察到 exit 1）。

- **作品**：真 CLI `run --once` 后 owner 版本 **v1 → v2**，`read_artifact` 读回**真实内容差异**
  （种子文本 + 替身写的段落），不是只比 hash。
- **世界**：真 CLI `run --once` → `IN_TRANSIT`；在途记录
  `travel-a48253…`（`route_id=home->street_1`、`route_duration_seconds=120`、
  `expected_arrival_at` 真实到期时刻）；出发后**位置仍在 home**；按引擎自己记录的到期时间结算后
  位置才变成 `street_1`（真实 remaining ≈119s，未用假等待）；随后**再发生一次决定**。
- **授权**：World home 的 `run/` 里留下 `action_authorizations.json`、`action_lifecycle.jsonl`、
  `authority.json` —— 正式入口真的被走过了。
- **暂停**：检查间隔设为 0（**每个 tick 都是检查窗**），暂停窗 `PAUSED_BY_OPERATOR`、
  `llm_calls=0`、版本与在途记录零变化 —— 暂停挡住的是一个**本来会发生的**决策，不是 NOT_DUE。
- **restart/resume**：新进程继续同一个 run window（`run_window_calls` 2 → 3），没有 mint 新额度。
- **对账**：loopback 服务真实收到 **5** 次请求 == 两个实例账本 3 + 2。
- **报告**：`verdict=NOT_PASS`（窗口不足），没有提前 PASS。

### §2「自己选」被作品固定优先架空 —— **PASS**

证据：`tests/test_one_day_life_w05r.py::RoutingTest`（4 项，全绿）。

- **同一实例、同一次候选集**里同时给出可续作品与已观察可达地点（`offered_ids` 长度 2），
  `step_path=AR0_WORLD_ROUTED`；替身选作品 → 真走 Supply owner（版本 +1、无旅程、无在途记录）；
  替身选地点 → 真走 World owner（旅程 + 在途记录、作品版本不变、位置仍在原处）。
- **真实历史改变下一次输入（因果，不是字符串）**：真实到达后位置变了，
  下一轮 AG-1 真正收到的 `candidate_id` 集合与上一轮**不同**；真实提交后
  AG-0 材料里的 `head_version_id` 变了，且等于 owner 当时报的 head。
- **前台合同未被违反**：已有前台活动时 AG-2 报
  `CANNOT_START_WHILE_FOREGROUND_ACTIVE:<activity_ref>`（CLI 演练里两次），
  没有强启第二个前台活动，也没有无限续作阻断其他候选（路由由 committed Decision 决定）。

### §3 World 正式授权不能被新 resolver 跳过 —— **PASS**

证据：`tests/test_one_day_life_w05r.py::FormalAuthorizationTest`（4 项）+ CLI 演练。

- `JourneyWorldResolver.describe()["submission_route"] == "formal"`；intent 的
  `execution_id` 与旅程段的 `execution_id` **相同**，`last_result.intent_status=APPLIED`，
  `submit_port.submissions == 1`；在途记录里正是那个 execution_id。
- **提交时关闸**：`WORLD_BODY_INTENT_MUTATION_GATE=off` → `status=rejected`、`code=GATE_OFF`、
  `transport_result=DEFINITE_PRE_SUBMIT`、`submissions == 0`，位置/world_state 字节/在途记录零变化；
  旅程账本里只有提交前写下的那次**耐久意图**（`status=blocked`、段 `pending`、`started_at=null`）。
- **负控（绕过授权）**：直接拿假 `authorization_id` 走 `gateway.sock` →
  `AUTHORIZATION_NOT_FOUND` + `world_unchanged=true`，世界字节不变 —— gateway 的授权防火墙是真的。
- **UNKNOWN 对账**：同一 request key / 同一 execution identity 对账，`SUCCEEDED` 但
  `arrived=false`、`segments_confirmed=0`；"有意图"从不被当成"已到达"。

### §4 pause 只是读位 —— **PASS**

证据：`tests/test_one_day_life_w05r.py::PauseBoundaryTest`（12 项，连续 3 轮全绿）。

- **跨进程锁 + 有界尝试**：`commit_boundary` 拿不到共享锁时 fail closed
  （`blocked=true, acquired=false, PAUSE_STATE_CONTENDED`）。
- **事件屏障（确定性）**：把一轮按在模型调用里（此时提交边界必然握着共享锁），
  确认中的 pause 在 1.5s 有界等待内**不可能**返回；放行后该轮真的完成提交（版本前进），
  pause 才确认。之后新决策 0 次模型调用、0 次提交。
- **原子持久化 + 并发修订不丢**：8 个并发 pause/resume 拿到 8 个**互不相同**的 revision，
  最终 revision = 最大值；独立临时名 + fsync + `os.replace` + 目录 fsync。
- **坏状态 fail closed**：暂停文件损坏 → gate 与 commit boundary 都 `blocked`
  （`PAUSE_STATE_CORRUPT`）。
- **不误触 World 的暂停**：`pause`/`resume` 前后 `<world home>/data/world/world_control.json`
  始终不存在。
- **锁序**：`pause_lock` 与 `journey → world → travel` 无交集、无递归；唯一的等待是
  "已开始的一轮 + 它的一次 owner 调用"，不跨无限网络等待。

### §5 模型总调用预算不封顶 —— **PASS**

证据：`tests/test_one_day_life_w05r.py::RequestBudgetTest`（9 项）。

- 决策 / 内容 / 重试**每个实际 HTTP attempt 在发出前**原子持久预留；预留后重新打开进程
  计数仍在（**不返还**，且没有 release API）；每个 attempt 有自己的 token。
- 拒绝时**一个请求都不发**（`inner` 一次没被调用），`reason_code=REQUEST_BUDGET_UTC_DAY_LIMIT`。
- 坏账本 fail closed（`check().allowed=false`、`reserve` 抛 `REQUEST_BUDGET_STATE_CORRUPT`），
  不会读成"还没花过"。
- 跨 UTC 日：新的一天有额度且**历史保留**（`2026-10-16=2`、`2026-10-17=1`）；
  墙钟回拨到前一天**不清已花计数**、不另开一天。
- 普通重启继续同一窗口（`existing`）；换 identity 开新窗口必须显式 `allow_new`，
  否则 `REQUEST_BUDGET_RUN_WINDOW_NOT_AUTHORIZED`。
- 单次窗口上限低于日上限时，第二条请求被 `REQUEST_BUDGET_RUN_WINDOW_LIMIT` 拦下。
- 一日入口一定写入两个上限（`prepare` 无 World socket 时不发明传输）。
- CLI 级逐项对账：loopback 实收 5 == 账本 3 + 2（见 §1）。

## 4. 命令 / 退出码 / 计数（最终冻结树实测）

最终回归：`w05r_final_run.txt`（`TMPDIR=/tmp/nyaw05r.jdK3q`，Python 3.12.3，WSL）：

```
EXIT=0  01 world_journey        Ran  28 tests OK
EXIT=0  02 world_move_tool      Ran  16 tests OK
EXIT=0  03 move_tool_navigation Ran  14 tests OK
EXIT=0  04 world_runner         Ran  26 tests OK
EXIT=0  05 move_pause_race      Ran   3 tests OK
EXIT=0  06 w05_one_day          Ran  17 tests OK
EXIT=0  07 w05r_new             Ran  63 tests OK
EXIT=0  08 worldgen_3_modules   Ran  24 tests OK
EXIT=0  09 world_service        Ran 259 tests OK
EXIT=0  10 whole_repo_tests     Ran 241 tests OK
EXIT=0  11 supply_owner         Ran 122 tests OK
EXIT=5  12 life_discover        Ran   0 tests in 0.000s   （目录里没有 test_*.py）
CLI_REHEARSAL_EXIT=0
```

- `whole_repo_tests` 由 W05 的 178 → **241**：W05R 新增 63 项（`test_one_day_life_w05r.py`），
  W05 的 17 项不变。不是 setUp/subTest 展开。
- `world_service 259` 全绿：本轮一度出现 1 项失败
  （`test_gate_off_refuses_before_any_contact`），根因是**我的 scratch 环境脚本**
  全局导出了 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`，而该测试断言的是闸门**默认（关）**
  行为；去掉全局导出、改为按运行 `--env` 传入后全绿。这是环境问题，不是产品缺陷。
- Life 侧既有测试：`components/life` 下 `discover -s . -p 'test_*.py'` = **0 项**（EXIT=5）。
  既有 `components/life/tests/v02/` 是**外部实例 harness**（`b8_*` / `t20_*` / `run_*` 脚本），
  需要活着的 Supply owner + 世界实例（`NYA02_SUPPLY_SOCKET` / `NYA02_SUBJECT` /
  `NYA02_ARTIFACT_GRANT` / `NYA02_LIFE_STATE`），本单未跑；替代覆盖是
  `test_one_day_life_w05r.py` + `test_one_day_life.py`，两者在**进程内**建真
  `ProductionLifeRuntime` + 真 Supply owner + 真 World gateway + 真授权链，覆盖同一批
  AG-0/AG-1/AG-2/AR-0/预算/暂停面。

## 5. 负对照（W04 原字节必须失败）

`w05r_neg_control.txt`：把 `components/world/scripts/world_action_resolver.py` 换回 **HEAD 字节**、
其余保持新树 →

- 新用例 `test_the_timed_segment_is_submitted_through_the_formal_entry`
  **FAIL**：`AssertionError: 'uncertain' != 'committed'`
  （HEAD 把计时出发判成 `POSTCONDITION_FAILED` → `uncertain/AMBIGUOUS_POST_SUBMISSION_FAILURE`）；
- 同一用例对新树 **OK**；
- 还原后 SHA256 与冻结 manifest **逐字节相同**（`RESTORE_EXACT=yes`）。

即：这是真实的行为差异，不是缺模块/导入错误冒充。

## 6. manifest 冻结与核对

- **冻结**：`w05r_manifest_frozen.txt`（产品+测试 27 项，逐项 SHA256；文档不在内，
  因为回执/手册是冻结后才写的交付物）。
- **冻结时机**：最终回归与最终 CLI 演练**之前**。
- **核对**：`w05r_manifest_after.txt`，27/27 行哈希**完全一致**
  （`w05r_mcheck.sh` → `HASH_LINES_IDENTICAL=yes`）。
- HEAD / 分支未动：`7f09d26…` / `work/v03-w01-navigation`。

## 7. 偏差 / 未完成 / 边界

1. **真实模型、真实 24h、部署、systemd 自启：一律未做**（未授权）。
   本单的模型一律是 loopback 替身；世界服务是本仓库自己的 `world_body_service`。
2. **CLI 演练用两个实例**（一个跑作品/暂停/预算/重启，一个跑世界计时移动），
   原因是**原合同**：一次前台活动存在时不得再启第二个活动。同一实例、同一候选集里两条路
   都由 AG-1 决定的证明在 `RoutingTest`，不是用两个实例冒充。
3. **"下一次决定"的落点是 ADOPTION_REJECTED**
   （`CANNOT_START_WHILE_FOREGROUND_ACTIVE:<activity>`）：决定本身是合同内的真实决定
   （`llm_calls=1`、模型输出未被判无效），是 AG-2 依前台合同拒绝了第二个前台活动。
   "到达之后换一个地方"这种更强的情节需要前台活动先合法结束，本单没有为了好看去绕它。
4. **`submit_move` 无法从 executor 结果里恢复 owner 的 `noop`**：executor 把
   applied/noop 都折叠成 `SUCCESS/APPLIED`。真实路径上不会出现（`accept_journey` 已先拒绝
   `already_there`），万一竞态出现，旅程会报 `SEGMENT_EVIDENCE_MISSING`（UNKNOWN，需对账），
   **不会**被写成到达。
5. `components/life/tests/v02/` 的 `b8_*`/`t20_*` 外部 harness 未跑（需要活实例），
   理由与替代覆盖见 §4。
6. 本单**未**新开功能域、未做 W06、未重写 World 权限系统；只修 MOVE 接线、
   pause 边界、预算账本与入口接线。

## 8. 停止线

修正单 §1–§5 的每一个具体缺口都已按上表 PASS，最终回归与最终 CLI 演练在冻结树上全绿，
manifest 核对一致。**本单到此停止，等父级独立核验**（父级可自行读日志、比对哈希与 git）。
真实模型运行与 24h 实跑仍未授权，未启动。
