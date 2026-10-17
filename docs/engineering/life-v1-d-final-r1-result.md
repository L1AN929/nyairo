# D-final-R1 回执：父级复验红的真实根因与最小修复（重启 harness 的决策分支）

- 候选树 `D:\nyairo-v03-backend`｜branch `work/v03-w01-navigation`｜HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（均未移动）
- **全部 UNCOMMITTED**：未 `stage`/`commit`/`reset`/`clean`/`checkout`，未删除/迁移任何文件，未整仓复制，
  未改 provider/凭据，未跑真实模型/外网/长跑；python 一律 `PYTHONDONTWRITEBYTECODE=1`，fixture 根保留
- 旧 `life-v1-d-final-result.md`、`life-v1-d-r1b-result.md`、`life-v1-d-r1-result.md` 等**保持原文**；本文件只记本次修复
- 本次**零产品代码改动**：只改 `tests/test_life_v1_restart.py` 与本人的 restart 决策笔记

---

## 1. 根因（父级日志 `/tmp/nyaw05r.jdK3q/d-r1-recovery-parent-final-four.log`）

失败现场（worker B）：

```
round={'outcome': 'ADOPTION_REJECTED', 'decision': 'START', 'adoption_status': 'REJECTED_STALE',
       'activity_ref': None}, head_at_start == head_while_paused == head_after_round
```

暂停、计数器、书签确实接上了（`state_at_start=PAUSED`、暂停轮 `llm_calls=0`、`utc_day_calls=1` 延续），
**只有"续同一 Activity"这一步没发生**。把 worker 的真实候选窗口记录下来之后，事实是：

| # | 候选 | 合法决策 | 指向 |
|---|---|---|---|
| 1 | `CONSIDER_PERSONAL_OPPORTUNITY`（工作产物） | `DEFER, START` | `artifact:artifact:…` / 工作产物 |
| 2 | `CONTINUE_CURRENT`（当前活动） | `DEFER, CONTINUE, PAUSE, ABANDON` | 同一个 Activity |
| 3 | `CONSIDER_PERSONAL_OPPORTUNITY`（阅读产物） | `DEFER, START` | 阅读材料 |
| 4 | `CONSIDER_WORLD_OPPORTUNITY`（地点） | `DEFER, START` | `place:street_1` |

根因是**测试侧的分支**，不是产品：

1. `WorkLoopback` 原来**按候选列表顺序**取"提到该产物且 START/CONTINUE 合法"的第一个候选；工作产物的
   `START` 候选（#1）排在 `CONTINUE_CURRENT`（#2）之前 → 选了 `START`。
2. 而前台 Activity 是 `ACTIVE` 时，`START` **本来就不合法**：AG-2 `validate_decision_for_adoption`
   （`life_integration_ag2.py:2637-2641`）返回 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`，AG-2 以
   `ADOPTION_STATUS_REJECTED_STALE`（`:2967-2995`）拒绝且**不写任何东西**（head、Activity revision 都不变）。
3. 更糟的隐患：原分支一个都没匹配上时会**静默回落**到通用 `StubCognition`（它也取 `START`），把"没找到合法候选"
   伪装成一次正常选择——父级看到的 `decision=START` 正是这类伪选择。

即：产品行为正确，harness 的决策分支违反了自己要证的那条规则（"恢复后应 **CONTINUE** 同一活动"）。

## 2. 最小修复（只动测试 + 本人笔记）

`tests/test_life_v1_restart.py`：

1. **按合法性选，不按顺序**：提到该产物且 `CONTINUE` 合法 → `CONTINUE`；否则 `START` 合法 → `START`；
   两者都没有 → **抛错并 dump 全部候选**（不再回落通用 stub）。
2. **把真相写进报告**：每次请求的候选（kind / source / legal / refs / source_ref）与最终选择进
   `report["cognition_seen"]` / `report["cognition_chosen"]`；轮次里加 `reason_codes` / `detail`（拒绝原因可直接看到）。
3. **新增精确负控（同一窗口）**：临换 `StubCognition("artifact")` 强制拿 `START`，断言
   `outcome=ADOPTION_REJECTED`、`adoption_status=REJECTED_STALE`、`detail` 含 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`，
   且 head 与 Activity revision **不变**。这把根因钉成回归测试，而不是靠"重跑到绿"。
4. **加强正例断言**（不放松）：worker A 必须 `START`；worker B 必须 `CONTINUE`（且其合法集合里确实含 CONTINUE、
   同一窗口确实也提供过 START 选项）；`adoption_status` 不含 `REJECTED`；`detail` 以 `owner committed` 开头；
   续上的是**同一个** `activity_ref`、owner 真出新版本、书签与 run identity/计数器/上限延续。

无 `sleep`、无时间戳对齐、无重试；同步点是 owner 的读回（head / Activity revision / 预算视图）。

## 3. 实际命令 / 真实 exit / 耗时

| 命令 | exit | 结果 |
|---|---|---|
| `run_tests.py r1c-restart-fix3 …test_a_real_restart_keeps_run_identity_and_continues_the_same_activity` | **0** | `Ran 1 test in 10.850s` / `OK` |
| `run_tests.py r1c-restart-fix4 …（同一条，第二次稳定性）` | **0** | `Ran 1 test in 10.799s` / `OK` |
| `node --experimental-strip-types …\verify-agent-note-tree.ts`（repo cwd） | **0** | `ok: 12 note(s) tree and relative links verified` |
| `node --experimental-strip-types …\verify-agent-note-format.ts`（repo cwd） | **0** | `ok: 12 note(s) verified` |

过程如实记录（全部日志保留）：
- `r1c-restart-diag`（10.9s，绿）——加了候选记录后第一次跑通，证明 CONTINUE 分支存在；
- `r1c-restart-fix1` / `fix2`（12.887s / 11.072s，**红**）——修复本身已生效（两轮都是 `decision=CONTINUE`、`ACTED`、
  真新版本），红的是我新加的一条**错误期望** `assertIsNone(round["detail"])`：ACTED 轮次的 `detail` 本意是
  `owner committed artifact-version:…`，不是拒绝原因。改成"不含 REJECTED + detail 以 owner committed 开头"后
  连跑两次绿（fix3/fix4）。
- 这两次红**不是**回归，也不是靠重跑掩盖：失败点与断言都已如实保留在上述日志里。

门禁说明：父级先前看到 `tree=1` 是**并行 writer** 的 `action-reality-ledger-settlement.md` 指向当时尚不存在的
`../feature/2026-10-16-room-interior-furniture-perception.md`；该 writer 补上后本次复跑为 `12 notes / exit 0`。
我**没有**改动他人笔记、没有绕过校验脚本、没有为过门禁删改任何文件。

## 4. 本次 diff（自有部分）

| 文件 | 状态 | 改动 |
|---|---|---|
| `tests/test_life_v1_restart.py` | 改 | `WorkLoopback` 改为按合法性选择 + 无合法候选即抛错；记录 `cognition_seen`/`cognition_chosen`；轮次记录 `reason_codes`/`detail`；worker B 增加"强制 START 被拒且无写入"的精确负控；父级断言加强（START/CONTINUE、无 REJECTED、同 `activity_ref`、负控拒绝码与不变性） |
| `.agents/notes/implemented/testing/2026-10-17-cross-process-life-restart-harness.md` | 改 | 新增"决策分支必须按合法性选，且失败要大声"一节：根因、四件真实候选、负控、报告字段 |
| `docs/engineering/life-v1-d-final-r1-result.md` | 新增 | 本回执 |

产品代码（`components/**`）本轮**一字未改**；`tests/test_life_v1_appraisal_context.py` 未改、未重跑（父级已单独 PASS）。

## 5. 已证 / 未验

**已证（本次，真实 exit 0，连跑两次）**：两个独立进程各自装配真实 runtime；worker B 以暂停态 0 调用启动，
resume 后**合法 CONTINUE** 同一个 Activity 并让 owner 产生真实新版本；run identity/计数器/上限/书签接续；
空档期无写入；同一窗口的 `START` 会被 owner 明确拒绝且无副作用（负控）。

**仍未验（不夸称）**：真实产品模型行为、24h/真实时钟、完整 P2、真并发、生产 World 包、S07 affect owner、
驻留进程整体重启（worker 为单次 `evaluate_once`，入口状态如实 `STOPPED`）。父级此前的 continuous drill 与
R1b 两专项 PASS 未受影响（本轮未改其相关代码与断言，未重跑）。

## 6. 一行结论

父级复验的红不是产品拒绝恢复，而是 harness 的决策分支按列表顺序拿了**不合法**的 `START`（AG-2 依规拒绝，
`CANNOT_START_WHILE_FOREGROUND_ACTIVE`）。现在分支按合法集合选 `CONTINUE`、找不到就大声失败，并把"强制 START
必被拒且无写入"变成回归负控；同一用例连跑两次 `10.850s` / `10.799s` 全绿，产品代码未改，全部 UNCOMMITTED。
