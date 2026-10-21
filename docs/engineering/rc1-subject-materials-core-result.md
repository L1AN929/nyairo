# rc1 主体私人材料 —— C 侧交付结果（版本敏感处境 + 同一 Activity 阅读续接）

> 三层表述（不合并）：**机制已验证**（真实 Supply/World owner + 真实 runtime + 脚本化决策替身）；
> **真实模型未调用**（0 次 provider 调用）；**尚未启用常驻**（没有在真实手机实例上跑过一天）。
> 本轮不合格的说法：`她自己读了一天`、`她自己选择了读书`。

- 仓库：`D:\nyairo-rc1-body-backend`（分支 `work/rc1-phone-world-body`，HEAD `41e818f`，dirty 成果保留，未 add/commit/reset）
- 契约：`docs/engineering/rc1-subject-materials-contract.md`
- 决策记录：`.agents/notes/implemented/feature/2026-10-24-rc1-subject-materials-version-aware-reading.md`
- P 面入口未动：`docs/engineering/rc1-subject-materials-binding-result.md`、`rc1-subject-materials-runbook.md`

---

## 1. 产品写集（实际 diff，C 唯一写者）

```
 .../life/scripts/artifact_continuation_source.py   | 132 ++++++++-
 components/life/scripts/autonomous_life_cycle.py   | 309 +++++++++++++++++++--
 .../life/scripts/autonomous_life_resident.py       |  20 +-
 components/life/scripts/private_daily_items.py     | 190 +++++++++++-
 4 files changed, 600 insertions(+), 51 deletions(-)
新增：components/life/scripts/subject_private_materials.py（461 行，纯读）
新增测试：tests/test_subject_private_materials.py（258 行）、tests/test_subject_reading_cycle.py（498 行）
```

未触碰：`components/phone/life.py`、`scripts/phone.py`、`components/life/scripts/one_day_life.py`（CLI）、
`components/life/scripts/phone_reading.py`、`components/world/**`、`components/supply/**`、`web/**`、
`life_artifact_port.py` 的写入语义（只存在只读 `operation_row()`，来自同树阅读/手机面）。

---

## 2. 真实测试结果（只跑新增类的 exact IDs）

方法与运行器：`$env:PI_SCRATCH_DIR\c_run_new_exact.py`（WSL，`PYTHONDONTWRITEBYTECODE=1`，repo cwd）。
它**只**从被测类的 `__dict__` 收集 `test_*` 并逐个构造用例，**不用** `discover`、
**不用** `loadTestsFromTestCase`/类继承，因此不会带出旧 one_day 套件。

```bash
# 材料处境（11 个）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && export PYTHONDONTWRITEBYTECODE=1 && \
  python3 <scratch>/c_run_new_exact.py test_subject_private_materials'
# 阅读循环（9 个）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && export PYTHONDONTWRITEBYTECODE=1 && \
  python3 <scratch>/c_run_new_exact.py test_subject_reading_cycle'
```

| 文件 / 类 | exact IDs | 结果 | 每例耗时 | 运行器退出码 | 原日志 |
| --- | --- | --- | --- | --- | --- |
| `tests/test_subject_private_materials.py::ReadingSituationTest` | 11 | 11/11 `OK` | ~0.8s | `0` | `scratch/c-new-materials.log` |
| `tests/test_subject_reading_cycle.py::SubjectReadingCycleTest` | 9 | 9/9 `OK` | 6.0–8.3s | `0` | `scratch/c-new-cycle.log` |

20/20 通过，`SUMMARY ids=20 failures=0`；两段日志末尾都是 `OK` + `SUMMARY … failures=0`，
shell 侧 `EXIT=0`。

### 2.1 exact IDs（逐条）

`test_subject_private_materials.ReadingSituationTest`
1. `test_an_unbound_instance_reports_reading_not_bound`
2. `test_a_fresh_material_has_no_stop_and_starts_at_page_one`
3. `test_the_latest_explicit_stop_wins_over_the_highest_page`
4. `test_an_old_revision_stop_is_a_situation_not_a_page`
5. `test_a_finished_material_is_exhausted_not_an_error`
6. `test_a_revoked_source_is_reported_and_not_reused`
7. `test_an_out_of_range_stop_is_reported_and_not_repaired`
8. `test_an_unreachable_owner_is_reported_not_guessed`
9. `test_the_situation_counts_notes_and_questions_without_their_text`
10. `test_the_fact_keys_are_bounded_ascii_and_carry_the_numbers`
11. `test_the_merged_facts_stay_inside_the_prompt_budget`

`test_subject_reading_cycle.SubjectReadingCycleTest`
1. `test_start_then_continue_reads_one_then_two_then_a_fresh_assembly_reads_three`（8.3s）
2. `test_finished_material_is_not_an_executable_opportunity_and_never_becomes_work`（6.7s）
3. `test_an_explicit_earlier_stop_is_the_stop_not_the_highest_page`（6.1s）
4. `test_a_new_material_revision_is_claimed_from_its_own_entry`（6.2s）
5. `test_a_foreign_grant_is_refused_by_the_owner_and_writes_nothing`（8.1s）
6. `test_paused_life_considers_nothing`（6.2s）
7. `test_the_reading_step_is_planned_under_one_deterministic_operation_key`（6.3s）
8. `test_the_situation_reaches_the_production_prompt_without_material_text`（8.2s）
9. `test_a_work_foreground_is_never_treated_as_a_reading_continuation`（6.0s）

### 2.2 这些用例真的钉住了什么（每条都有断言，不是"跑过就算"）

- `START 读第 1 页 → 同一 Activity CONTINUE 读第 2 页 → 同 state_root 重新装配后读第 3 页`：
  `activity_ref` 三次相等；三轮作品 head 不变；私人事项文档头每轮都变（真 CAS 版本）。
- 读尽：`state=EXHAUSTED`、`available=False`、`next_page=None`、`reading_available()=False`、
  候选源不再给出这条阅读机会；下一轮 `NO_ACTION` 且**一个字节都不写**（文档 head 相等）。
- 回前页：先读 3 再读 1，处境 `page=1 / next_page=2`（不是 `max=3`），同版本回前页不算旧版。
- 换版：旧版 1,2,3 页不搬，`needs_reread=True`、`prior_revision_pages=[1,2,3]`、从第 1 页重新认领，
  新停点的 `source_revision` 等于当前 head。
- 缺 grant：foreign grant 由 owner 拒绝，`outcome != ACTED`，两件物件 head 都不动。
- 暂停：`PAUSED_BY_OPERATOR` 且 `cognition.call_count == 0`（连决策都不问）。
- UNKNOWN 原键：`_reading_step_spec` 的 `idempotency_key` 稳定、`expected_head` 钉在 owner 当前 head；
  `UNKNOWN` 走 `STEP_UNKNOWN` + 「reconcile the original operation」，不重试新键、不改文档。
- prompt 通道：`reading_state_AVAILABLE` / `reading_next_page_1` / `reading_pages_3` 出现在
  `observed_fact_keys`，`<48` 字符、ASCII、`<=24` 键；正文特征串 `窗外是一条街`、`第一页` 与
  `source_acquisition` 都不在 prompt 文本里；下一轮事实跟着停点前移（不是冻结快照）。
- 反例：前台是作品时不会被当成阅读续接，`_foreground_reading_activity()` 返回 `None`，
  私人事项文档 `items == {}`（一个版本都没有）。

---

## 3. 失败与偏差（原日志照抄，不美化）

### 3.1 本轮新增用例：0 失败

`c-new-materials.log` / `c-new-cycle.log` 里没有 `FAILED`、没有 `ERROR`、没有 traceback；
`failures=0`，`EXIT=0`。因此**没有触发**「定点修 + 重跑受影响」这条分支。

### 3.2 前一轮 C（`ad5e15b2`）的测试扩大偏差（父级叫停的原因，已核对原日志）

`scratch/c_run_affected.sh` 并不是"精确受影响"，它按顺序跑了 7 个既有文件：

| 段 | 原日志行 | 结论 |
| --- | --- | --- |
| `tests/test_private_daily_items.py` | `Ran 14 tests in 11.901s` / `OK` / `EXIT_CODE=0` | 通过（超出本轮范围） |
| `tests/test_life_v1_integration.py` | `Ran 56 tests in 429.540s` / `OK` / `EXIT_CODE=0` | 通过但耗时 429s，严重超范围 |
| `tests/test_life_stay_early_exit.py` | `Ran 2 tests in 41.781s` / `OK` / `EXIT_CODE=0` | 通过（超范围） |
| `tests/test_one_day_life.py` | 仅 `EXIT_CODE=0`，**没有 `Ran`/`OK` 行** | 不作为任何证据（其自身 runner 未打印汇总） |
| `tests/test_one_day_life_w05r.py` | `.................................`（33 个点后被终止） | **未跑完**，不构成通过 |

### 3.3 我对那次运行做的唯一处置（只停那一次测试）

- 现场核对 argv/PID：WSL 内 `23166` = `bash /mnt/c/…/scratch/c_run_affected.sh`，
  其子进程 `24919` = `python3 tests/test_one_day_life_w05r.py`（旧套件，正是父级叫停的对象）；
  Windows 侧只有一个 `pwsh -Command "wsl -e bash …/c_run_affected.sh"` 包装进程。
- 处置：`kill -TERM 23166 24919`，复检两者 `GONE`；WSL 内再无 `pytest|test_|c_run` 进程。
- **没有碰**：当前 phone `serve`（`python.exe .venv\Scripts\python.exe cli.py serve`，PID 9920 / 8384）
  与任何 Supply/World 服务；没有删除任何 fixture/pyc/socket/临时目录；没有 `git add/reset/commit`。

---

## 4. 残余缺口（交给父级验收，不假装已经完成）

1. **真实模型从未被调用**：prompt 侧只证明"事实键确实进 `build_prompt` 的
   `observed_fact_keys`"（`SimpleNamespace(provider="stub", model="no-model-called")`），
   决策来自 `ScriptedCognition`。所以"她自己在读"仍不成立。
2. **只在 WSL 跑过**：两个新文件都是 `@posix_only`（要 `fcntl` + `AF_UNIX`），原生 Windows 会 skip。
   本轮真实 owner 证据全部来自 Linux 侧。
3. **读尽后不自动完成 Activity**：按施工单允许停在合法 `NO_ACTION`（`reading_continued=False`），
   Activity 仍 `ACTIVE` 直到合法 `PAUSE`/`ABANDON`。是否复用既有完成合同收尾，留给父级决定。
4. **旧套件回归不在本轮证据里**：`test_life_v1_integration`（56）等是被叫停的扩大运行，
   没有重跑；整体门禁仍由父级负责。（下文 §6 的定点修复那次也只跑 4 个 exact ID。）
5. **没在真实手机实例上跑**：C 侧只在测试隔离实例（真实 Supply/World socket）上验证；
   同实例钱包/四购买/家具灯的真实实例验收由父级做，我没有操作它。
6. **归属说明**：`private_daily_items.py` 的窄 API（`record_page_read` / `reading_operation` /
   `expected_head` / `marker_id`）与 `life_artifact_port.operation_row()` 是同树阅读/手机面
   （`components/life/scripts/phone_reading.py:841` 等）与 C 侧处境读共用的接口；C 侧只读它们。
   两侧共用同一份文档版本，没有第二本账。

---

## 5. 复现清单

```bash
# 处境纯读（11） / 阅读循环（9），WSL，repo cwd，PYTHONDONTWRITEBYTECODE=1
python3 $PI_SCRATCH_DIR/c_run_new_exact.py test_subject_private_materials
python3 $PI_SCRATCH_DIR/c_run_new_exact.py test_subject_reading_cycle

# 决策笔记门禁（repo cwd）
node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-tree.ts
node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-format.ts
```

---

## 6. 追加定点修复：`needs_reread` 语义 + acquisition 的 source 校验

父级在同一真实实例上跑通「first START1/CONTINUE2 → second sameActivity CONTINUE3 → exhausted
NO_ACTION 零写、work 未改」之后，只读复核发现两处具体缺陷（都在 `subject_private_materials.py` 里）：

1. **旧版页把当前停点永久判成过期**（原 L284）：`needs_reread = bool(prior_pages) or any(old
   needs_reread)`，只要历史旧版页还在就恒 `True` + `SOURCE_REVISION_ADVANCED` —— 即使最新版本上
   已经有真实 acquisition + 有效停点。下一轮认知于是收到「你还没重看」的矛盾事实。
2. **只比 revision、没比 source**（原 L268-274）：position 指向的条目只校验「存在 + revision 相同 +
   未撤权」，没有校验它自己的 `source_id` 就是这份材料、也没有校验它确实是
   `ITEM_SOURCE_ACQUISITION`。别家的取得（同 revision）会被当成这份材料的停点。

修复（只改这一个文件，未动 cycle / source / resident / PDI / phone / 实例数据）：

- 有效停点判据从 4 条升到 5 条：`source_id` 命中 + 指向条目存在且 `item_type` 是
  `ITEM_SOURCE_ACQUISITION` 且其 `source_id` 同样命中 + entry/position 的 `source_revision` 都等于
  当前 head + 该 acquisition 未撤权（position 本身已按 `source_id` 过滤）。
- `needs_reread` 只在**没有当前有效停点**时才看历史：有当前停点时由这条停点自己的记录决定
  （刚落的当前停点即 `False`、`needs_reread_reason=""`）；没有当前停点且有旧版页 → `True` +
  `SOURCE_REVISION_ADVANCED`。`prior_revision_pages` 与 `stale_note`/`stale_unclear` 照旧如实报告，
  旧版 needs_reread 用例的断言未改。

新增两个最小用例（旧用例一条未改、一条未删）：

| exact ID | 结果 | 耗时 |
| --- | --- | --- |
| `test_subject_private_materials.ReadingSituationTest.test_a_current_stop_is_not_made_stale_by_the_previous_revisions_pages` | `PASS` | 1.7s |
| `test_subject_private_materials.ReadingSituationTest.test_a_stop_that_points_at_another_sources_acquisition_is_not_a_stop` | `PASS` | 0.8s |
| 确切受影响旧纯读用例 `…ReadingSituationTest.test_an_old_revision_stop_is_a_situation_not_a_page` | `PASS` | 0.9s |
| 确切受影响旧纯读用例 `…ReadingSituationTest.test_a_finished_material_is_exhausted_not_an_error` | `PASS` | 0.9s |

`SUMMARY ids=4 failures=0`，运行器退出码 `0`，原日志 `scratch/c-fix-needs-reread.log`
（4 段 `Ran 1 test … OK`）。**没有**重跑 20 个全组、没有重跑任何旧套件、没有碰真实实例/owner 数据。
反例是刻意手写文档构造的（PDI 门面 `_available_acquisition` 本来就拒绝跨 source 配对），
恰好说明「只读处境不能相信旧数据/被改过的文档形状」。
