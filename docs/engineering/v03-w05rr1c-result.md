# W05R-R1c 回执：可分离 send/response 的传输 —— 发送阶段持确认锁

- 候选：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），**UNCOMMITTED**
- HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）｜branch：`work/v03-w01-navigation`（未移动）｜`git diff --check` exit 0
- 范围：只关「pause 确认之后，真实发送仍会漏出 1 次请求」这一个缺陷（原单 `D:\NYAIRO-v0.3-W05R-R1-独立验收定点返修.md` 的暂停发送边界）。不新功能、不重开架构审查、不动 AG-1 决策政策、不造新 owner；R1-B（真实到达 → 合法完成 → 另一分支）成果保留。
- 本轮性质：**收尾复核**。产品/测试代码自 R1c 收尾后未再改（见 §3 时间对照），除新增本回执外**未改任何文件**；唯一新增运行是 §4 的 13 例定向复跑。
- 未做：真实模型、外网验收、生产 profile/凭据/provider 改动、24h、部署、服务改动、删除、commit/push、子委派。

---

## 1. 缺陷与 R1c 语义

R1b 的 `admission` 是**检查**：在持久预留之后、transport 调用之前读一次位。真正的发送在更后面，所以「确认落在检查之后」的那一次请求仍会被写出去（父级旧 probe 复现：确认后 1 次实收）。R1c 把「许可」从一次检查换成**跨真实写出持有的共享确认锁**：

- `life_pause.RequestSendGuard.sending(kind)`（`life_pause.py:295-306`）：取共享确认锁 → **持锁**重读位（命中或争用即 `PauseConfirmedError`，fail closed，`stage="before_<kind>_send"`）→ 退出 `with` 块释放；
- `OpenAICompatibleTransport.complete()`（`profile_model_transport.py:219-238`）：配置了 guard 时**只**把 `begin_send`（connect+headers+body，`profile_model_transport.py:133-166`）包进 `guard.sending("model")`；`finish_send`（`168-217`）在任何 pause/shared/control 锁之外；
- 不能分离 send/response 的 inner → 在**任何发送之前** `RequestNotSentError`（`REQUEST_NOT_SENT_UNCOORDINATED_TRANSPORT`），已落盘的预留不返还。

关键语义变化：生产路径上交给包装器的 `admission` 现在是 `RequestSendGuard` 实例（**不可调用**），于是 `require_coordinated_send` 只做「能力」拒绝，**许可来自 transport 跨写出持有的那把锁**，不再来自任何一次读位。

---

## 2. 相对父级封存 R1b 的精确增量

只读对比（脚本 `$S/w05rr1c_diff_vs_r1b.sh` → `w05rr1c_diff_vs_r1b.log`，SEALED=`/mnt/d/NyAiro-v2-工程管理/证据/W05RR1b-parent-20261016/product`）：

| 文件 | R1c 改动（行号为当前文件） |
|---|---|
| `components/life/scripts/life_pause.py` | `254-306` 新增 `REASON_REQUEST_NOT_SENT` / `RequestNotSentError` / `RequestSendGuard.sending()`；`516-518` `__all__` 同步 |
| `components/life/scripts/life_request_budget.py` | `48` `+import life_pause`；`383-405` 新增 `require_coordinated_send()`；`437-438`、`468` 两个包装器把 R1b 的 `self.admission(kind, attempt)` 换成 `require_coordinated_send(...)` |
| `components/life/scripts/profile_model_transport.py` | `76` `send_split_capable = True`；`133-166` `begin_send()`（连接/headers/body，受 `timeout_s` 有界）；`168-217` `finish_send()`（getresponse/read，无锁）；`219-238` `complete()` 持锁只包 send（**不在 R1b 封存清单**，mtime 20:16） |
| `components/life/scripts/agency_model_client.py` | `206-215` `send_split_capable`；`217-229` `begin_send()`；`231-238` `finish_send()`；`243-248` 注入 transport 时 `complete()` 走 `transport.complete`（**不在 R1b 封存清单**，mtime 20:16） |
| `components/life/scripts/agency_cognition_adapter.py` | `141-151` `send_split_capable` 透传 client；`329-335` 把 `PauseConfirmedError`/`RequestNotSentError` 直接 re-raise（不当 provider 失败、不当决策）（**不在 R1b 封存清单**，mtime 20:34） |
| `components/life/scripts/autonomous_life_assembly.py` | `239-250` `pause_request_admission()` 改为返回 `RequestSendGuard`；`253-255`/`288-290` `+pause_guard=`；`273-276`/`308-311` transport 带 `pause_guard`；`390-394` assemble 用**同一个对象**同时作 `admission` 与 `pause_guard` |
| `components/life/scripts/autonomous_life_resident.py` | `341-343` `model_cognition(..., admission=admission, pause_guard=admission)` |
| `components/life/scripts/autonomous_life_cycle.py` | `1773-1779` `_commit_next_version` 对两个 pause 异常 re-raise（不再记成 `CONTENT_FAILED`） |
| `tests/test_one_day_life_w05r.py` | 新增 `LoopbackModel`（真 loopback chat-completions，记录每次实收与 purpose）与 `_RealCognitionInner`；R1b 的 `test_every_transport_attempt_is_admitted_and_the_reservation_is_kept` 被替换为 3 个 R1c 用例（R1b 10 → R1c 13） |

R1c 自有改动的字节数对照（R1b 封存 → 当前）：`life_pause.py` 19991→22580、`life_request_budget.py` 20566→21679、`autonomous_life_cycle.py` 97384→97731、`autonomous_life_assembly.py` 23777→23696、`autonomous_life_resident.py` 39206→39274、`tests/test_one_day_life_w05r.py` 63985→74791。

---

## 3. 现有证据（复用，未重跑整套）

| 证据 | 结果 |
|---|---|
| `w05rr1c_rsb.log` | `Ran 13 tests in 85.939s … OK`，`EXIT=0`（真传输发送前 pause 零接收、发送阶段持锁、response 挂起可 pause、opaque transport fail-closed、父级两探针树内回归、有限 owner 竞态） |
| `w05rr1c_w05r_full.log` | `Ran 86 tests in 574.099s … OK`（R1b 同组 83 → R1c 86，差 +3 即 §2 的新用例） |
| `w05rr1c_cli_run.txt` | 真 CLI 子进程 + 真 Supply socket + 真 World gateway + loopback 替身：**46 checks、`"failed": []`、`"ok": true`、`EXIT=0`、220.105s** |
| `w05rr1c_send_probe.log` | 3 例定向探针 OK：真传输确认在发送前 → 三种 kind 各 0 实收 / reserved 1；opaque → 0 实收 + `REQUEST_NOT_SENT_UNCOORDINATED_TRANSPORT`；响应挂起时 pause 仍返回（`pause_returned_while_response_pending: true`，二次尝试实收仍为 1） |
| `w05rr1c_neg_control.txt` | 负控（见 §5） |
| `w05rr1c_parent_send_probe_rerun.log` | 旧父级 probe 协议不兼容（见 §6） |

CLI 关键 check 摘录：`paused_window_would_have_been_due`/`paused_window_held`/`paused_window_zero_model_calls`/`paused_window_zero_new_requests`/`paused_window_zero_commit`（`PAUSED_BY_OPERATOR`、`llm_calls=0`、`pause_boundary=null`）；`round1_owner_version_advanced` 作品 1→2 且 `content_changed: true`；`resumed_round_acted_on_the_owner` `CONTINUE` 2→3；`world_round_departed_not_arrived` + `world_round_travel_has_a_real_due_time`（`IN_PROGRESS`、`expected_arrival_at`）；`arrival_settled_by_the_engines_own_path`（等 122.3s，`JOURNEY_COMPLETED`/`arrived`）；`world_activity_legally_completed`（`COMPLETED`，`completion_evidence_ref=world_fact:place:street_1:r2`）；`per_kind_requests_reconcile_with_the_ledger`（A`{decision:2,content:2,retry:0}` + B`{3,2,0}` = loopback`{5,4}`）；`report_is_not_a_premature_pass` detail=`NOT_PASS`（即报告不得提前判通过，符合预期）。

**证据时间晚于最终产品代码**：产品最后一次改动 `autonomous_life_assembly.py` 20:44:06，测试文件 20:57:07；CLI 20:48:11、rsb 20:58:46、w05r_full 21:08:37、neg 21:13:54。故上述结果全部对应当前树，未因后续改动失效。

---

## 4. 本轮定向复跑（唯一新增运行）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend/tests && PYTHONDONTWRITEBYTECODE=1 \
  $S/w04venv/bin/python -m unittest -v test_one_day_life_w05r.RequestStartBoundaryTest
```

结果：**`Ran 13 tests in 87.837s` / `OK`**，逐条测试名与结果同 `w05rr1c_rsb.log`。未跑全仓、未跑 World/Supply 大套、未重跑 CLI（CLI 输出完整且晚于最终代码，按指令不为「新的时间」再跑）。

---

## 5. 负控的意义（`w05rr1c_neg_control.txt`；脚本 `w05rr1c_neg.sh` **不得再执行**）

树 = 当前候选 + 父级封存 R1b 产品覆盖（封存目录里**没有** `profile_model_transport.py` / `agency_model_client.py`，那两件因此是当前版本）。结果：

```
{"tree": ".../w05rr1c_neg_tree", "kind": "content",
 "http_requests_after_confirmed_pause": 1, "reserved": 1, "leak_present": true}
NEG_EXIT=0
```

意义：驱动的是 **R1b 的 `BudgetedModelClient`**（`life_request_budget.py` 封存 SHA `9c597da1…`）——它的 admission 只是检查、transport 未接 guard，于是确认之后仍真实写出 1 次 HTTP；这就是本轮关闭的那个缺陷。R1c 侧由 `test_the_wrapper_refuses_a_transport_that_cannot_coordinate_the_send`（opaque 在发送前被拒、零发送、预留不返还）与 `test_the_real_transport_sends_nothing_after_the_pause_is_confirmed`（真 transport + loopback、0 实收、`stage=before_model_send`）覆盖。

诚实边界：该负控证明的是「R1b 包装器 + 未接 guard 的传输会漏」，不是「R1b 产品在自身接线下一模一样」——R1b 从来没有把 guard 接到 transport（那时没有这个接口）。脚本含 `rm -rf` 与整树复制，已完成，不再执行；旧脚本/日志保持原样。

---

## 6. 旧父级 send probe 的协议变化（不得称其 OK）

父级 `parent_r1b_send_probe.py`（封存目录，未修改未覆盖）的 fake `Inner` 没有 `send_split_capable`，且假定包装器会调用 `Inner` 之后再断言实收为 0。R1c 对「不能分离 send/response 的 inner」的答案是**在任何发送之前拒绝**，所以该探针在调用处收到 `RequestNotSentError: REQUEST_NOT_SENT_UNCOORDINATED_TRANSPORT`（`w05rr1c_parent_send_probe_rerun.log`：`Ran 1 test … FAILED (errors=1)`）。

结论：**协议不兼容，但零发送**；探针「确认之后 0 次实收」的意图已由树内等价用例 + 真 loopback 证据覆盖（§3、§4）。不把父级 probe 记作通过。

---

## 7. 定向复核结论（对应验收清单）

| 要求 | 证据 |
|---|---|
| 实际 HTTP send 在 pause 边界中 | 真锁跨真实写出：`profile_model_transport.py:158` `connection.send(payload)` 位于 `guard.sending("model")` 块内；loopback 用例断言 0 实收 |
| 连接/发送有有限 timeout | `profile_model_transport.py:144-146` 连接带 `timeout_s`；`159-164` 超时/连接错误分类；`finish_send` 同样有界（`181-184`） |
| response 读取不持控制锁 | `finish_send` 在 guard 之外；响应挂起时独立进程的 `pause` 能返回、随后第二次发送被拦（`test_a_pause_confirmed_while_the_real_response_hangs_blocks_the_next_step`） |
| 预算 decision/content/schema-retry 前持久且不返还 | `reserve()` 在 `require_coordinated_send` 之前（`life_request_budget.py:437-438`、`468`）；无 release API（`1069` 断言 `not hasattr(budget, "release")`）；被拦的 attempt 各 +1 |
| fail closed 未支持 transport | `RequestNotSentError`（零发送），不是「照发再报错」 |
| 不是 claim 布尔冒充保护 | 保护是 `flock` 跨真实 socket 写出；`send_split_capable` 只是「能力」闸门 |

**残余（已知边界，本轮不修）**：`require_coordinated_send` 查的是能力标志，不查 guard 是否真的接上。所有产品调用点都用同一个对象同时作 `admission` 与 `pause_guard`（`autonomous_life_assembly.py:390-394`、`autonomous_life_resident.py:341-343`），所以当前不可达；将来若出现只传其中一个的调用点，该闸门不会拒绝，会退化成 R1b 行为。若父级要收口，最小改法是让 transport 的 `send_split_capable` 在 `pause_guard is None` 时为 False（约一行 + 一处测试），代价是必须重跑 13 + 86 + CLI 并重冻证据 —— 本轮按「不扩范围、保持已封存证据有效」未做。

---

## 8. 已知不归本轮 / 未验边界

- `components/check_life_v02.py` 的 3 项基线 fail（Memory 消费者/交付未接线的环境缺口）：R1b 回执已记录其在**父级封存 R1b 产品上逐行相同**（本轮未复跑该检查），故不归本轮；**不据此报「全系统通过」**。
- 未改、未接、不计入 R1c：`components/life/scripts/body_capacity_source.py`、`tests/test_body_capacity_source.py`、`docs/engineering/life-seed-body-capacity.md`（Body 代理遗留三件）。
- 未验：真 provider/外网行为（只用 loopback）、Windows（无 `fcntl` → 无跨进程声明，POSIX only）、24h/部署。
- 既有残余（R1 已记，本轮未修）：AG-0 机会秒边界竞态（`candidate_sources_ag0.py:2297`）；AG-1 与 AG-2 对 START 的一致性。
- `commit_boundary` 仍包住 `settle_world_journey` 的整段 `advance_journey`（`autonomous_life_cycle.py:1440-1451`）：那是 World owner 的有限调用，不是模型/内容网络等待，与 R1 语义一致，本轮未改。

---

## 9. 文件与提交状态

- 本轮唯一新增文件：`docs/engineering/v03-w05rr1c-result.md`（本文件）。
- R1c 自有改动：见 §2 的 9 个文件，**全部 UNCOMMITTED、未 stage、未 commit**（共享工作树，禁止 reset/checkout/clean/删除/commit/push）。
- 完整 `git status` 仍含 W01–W05R 前序轮次的共享改动，本轮未 stage 任何文件。
- 7 个历史回执（`v03-w01/02/03/04/05-result.md`、`v03-w05r-result.md`、`v03-w05rr1-result.md`、`v03-w05rr1b-result.md`）未修改。

---

## 10. 复现命令

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
# 13 例定向（本轮唯一新增运行）
cd /mnt/d/nyairo-v03-backend/tests && PYTHONDONTWRITEBYTECODE=1 \
  $S/w04venv/bin/python -m unittest -v test_one_day_life_w05r.RequestStartBoundaryTest
# 只读：当前候选 vs 父级封存 R1b 的 R1c 增量
bash $S/w05rr1c_diff_vs_r1b.sh
```

（`$S/w05rr1c_neg.sh` 不得再执行：含 `rm -rf` 与整树复制；负控结果已封存在 `$S/w05rr1c_neg_control.txt`。）
