# PAUSED 已有恢复资格路径对齐（B1）· worker 交付报告（2026-10-23）

## 结论先说

B1 已按监工批准的同文件同语义范围完成：`components/life/scripts/activity_waiting_agenda.py`
中**两处同源状态谓词**从「目标必须是 `WAITING`」放宽为「`WAITING` 或 `PAUSED`」——
显式恢复入口 `explicit_resume_from_eligibility` 的状态检查，以及
`_collect_stale_and_terminal_updates` 的 **eligibility 分支**。其余判据（OPEN、过期、
revision、episode、CAS、幂等、前台冲突、终态/ACTIVE 目标、condition/agenda 分支）一条未动。

新测试 12 例在当前工作树全过；把两处谓词还原成旧写法后同一套测试 3 失败 1 错误。
**本修复不制造资格，不能解遗留实例，不是 G1 通过，未调用产品模型。**

## 上一单的受阻与本次的关系（保留）

上一单的前提「只改入口谓词」不成立：入口先 `reconcile_with_canonical_activities`
（原第 3021 行）、再读 eligibility（3023 行），而 reconcile 的
`_collect_stale_and_terminal_updates` eligibility 分支把任何非 `WAITING` 活动上的 OPEN
eligibility 直接改写成 `STALE`。实测入口报的是
`EligibilityNotOpenError: ... is not OPEN (status='STALE')`，入口自己的状态断言根本轮不到。
上一单按停止条件零改动收尾，只留下这次复现：

```
START -> ACTIVE rev 1
PAUSE -> PAUSED rev 2 episode None
seeded eligibility OPEN activity_revision 2
RESUME FAILED: EligibilityNotOpenError eligibility_id='relig:pausedprobe1' is not OPEN (status='STALE')
eligibility after: STALE STALE_ACTIVITY_REVISION
activity after: PAUSED
```

当时在仓库外临时副本（`C:\Users\28556\AppData\Local\Temp\probe-scripts-69b19d82\`）里两处都
改后跑通，**那只是阻塞点证明，不是交付**：没有仓库内改动、没有正式测试、没有 note。
本次所有验收都基于当前唯一工作树的正式改动，不以临时副本导入充当修复版验收。

## 本次实际改动（写集内，零越界）

- `components/life/scripts/activity_waiting_agenda.py`
  - `_collect_stale_and_terminal_updates` eligibility 分支：
    `act.get("status") not in (STATUS_WAITING, STATUS_PAUSED)`，替换原 `!= STATUS_WAITING`；
    注释写明依据（设计 2.0 分章 SRC-0719 第 10–13 行，PAUSED 满足自有恢复条件可回 READY，
    LR-2 本就允许 PAUSED→ACTIVE）。
  - `explicit_resume_from_eligibility`：同一状态谓词改为 `WAITING` 或 `PAUSED`，错误文案
    改为「is not in WAITING or PAUSED status」。`STATUS_PAUSED` 原已导入，无新增 import。
  - 未改 revision / episode / OPEN / 过期 / CAS / 幂等 / 前台冲突 / 终态判据；
    未动 condition 分支、agenda 分支；未新增资格生成、未回填旧实例、未改落盘格式或接口。
- `tests/test_paused_eligible_alignment.py`（新，12 例）
- `.agents/notes/implemented/bug-fix/2026-10-23-paused-resume-eligibility-alignment.md`（新）
- 本报告

## 测试构造与真假边界

- **真 owner**：`ActivityAuthorityLease`、`CanonicalActivityStore`、`ActivityCommandService`
  （LR-2）、`WaitingCoordinator` 与其三个 store（LR-4），隔离临时根
  `tempfile.mkdtemp(prefix="paused-eligible-b1-")`，创建后保留不删。
- **测试层替身**：只有 AG-1 Decision 读端口与 AG-2 Adoption 读端口两个 dict 返回器，提供
  `ProvenanceVerifier` 实际读取的字段；`AdmissionIssuer` 的 command-scoped grant 校验、
  activity scope 校验、fingerprint 校验全部真实执行。即：谁是真 owner 是 LR-2/LR-4，
  谁是替身是这两条 provenance 读端口。
- **合成状态**：OPEN eligibility 由测试用 LR-4 自己的 `ResumeEligibility` +
  `commit_records` 写入，匹配 PAUSED 活动当时的 revision；没有公开接口会给 PAUSED 发资格，
  故明确是人工构造，不冒充自然生命周期。
- **AG-1 合法集**：`DecisionOutputValidator.compute_legal_decisions_for_candidate` 是真实调用，
  但 `CandidateRecord`、`DecisionContextSnapshot`、`AgencyCapacitySnapshot` 是测试层值。
  它证明的是「AG-1 本来就会给 PAUSED + OPEN eligibility 出 `RESUME`」，
  **不覆盖** AG-2 整链、不覆盖自然生成 qualification、不覆盖候选集装配。

## 正负控清单（每项独立临时根）

| 场景 | 断言 |
|---|---|
| PAUSED + OPEN + revision 匹配 | reconcile `invalidated_eligibilities=0`、仍 `OPEN`；AG-1 合法集含 `RESUME`；入口恢复为 `ACTIVE`、资格 `CONSUMED` / `CONSUMED_BY_EXPLICIT_RESUME` |
| WAITING 对照 | reconcile 0、仍 `OPEN`；入口恢复 `ACTIVE`、资格 `CONSUMED` |
| PAUSED + stale revision | reconcile 标 `STALE` / `STALE_ACTIVITY_REVISION`；入口 `EligibilityNotOpenError`；活动仍 PAUSED |
| ACTIVE 目标 | reconcile 标 `STALE`；入口拒绝 |
| terminal（COMPLETED）目标 | reconcile 标 `CANCELLED` / `TERMINAL_ACTIVITY_INVALIDATED`；入口拒绝 |
| WAITING + episode 不匹配 | reconcile 标 `STALE`；入口拒绝（PAUSED 下 canonical `pause` 会清空 episode，该谓词在 PAUSED 不可触发，测试里如实断言 `waiting_episode_id is None`） |
| 过期资格（PAUSED） | reconcile 不检查过期、仍 `OPEN`；入口以 expired 拒绝；活动仍 PAUSED |
| `CONSUMED` 资格 | reconcile 不复活、仍 `CONSUMED`；入口拒绝 |
| 未知 reference | 入口 `not found` 拒绝 |
| 前台被另一活动占用 | `ForegroundConflictError`；两活动状态与资格均不变 |
| 重复相同 idempotency key | 第一次恢复后资格 `CONSUMED`；第二次在资格门被拒，活动 revision 不再变化 |
| PAUSED 上的 condition / agenda | 同一 reconcile 内 condition `STALE`、agenda `CANCELLED`，eligibility 仍 `OPEN` |

## 修复前反例（仓库外，不改业务树）

- 把当前模块复制到 `/tmp/b1-baseline/activity_waiting_agenda.py`，仅把两处
  `not in (STATUS_WAITING, STATUS_PAUSED)` 还原为 `!= STATUS_WAITING`（共 2 处），
  用同一探针（`C:\Users\28556\AppData\Local\Temp\b1_baseline_probe.py`）：
  - 旧谓词：`reconcile invalidated_eligibilities 1 -> STALE STALE_ACTIVITY_REVISION`，
    `RESUME REFUSED: EligibilityNotOpenError ... (status='STALE')`。
  - 当前树：`reconcile invalidated_eligibilities 0 -> OPEN`，`RESUME -> ACTIVE`，
    `eligibility after: CONSUMED CONSUMED_BY_EXPLICIT_RESUME`。
- 把同一套测试复制到 `/tmp/b1-baseline-test/` 并以 `B1_BASELINE=1` 让旧模块优先：
  `Ran 12 tests ... FAILED (failures=3, errors=1)`，全落在 PAUSED 正控与两处 PAUSED 负控上。
  当前树同名 12 例 `OK`。

## 实际命令与结果（当前树）

1. `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_paused_eligible_alignment`
   → `Ran 12 tests ... OK`，shell 0。
2. 旧谓词副本探针（`--baseline`）→ 上述 STALE/拒绝；当前树探针 → ACTIVE/CONSUMED，均 shell 0。
3. 旧谓词副本 + 同一测试 → `FAILED (failures=3, errors=1)`，shell 1（预期红，用于证明覆盖）。
4. 笔记门禁：
   - `verify-agent-note-tree.ts` → `ok: 88 note(s) tree and relative links verified`，shell 0。
   - `verify-agent-note-format.ts` → `ok: 88 note(s) verified`，shell 0。
   - `verify-archived-agent-notes.ts` → `ok: 0 archived note(s) verified, 0 seal(s) in manifest`，shell 0。
5. 空白检查：`git diff --check -- components/life/scripts/activity_waiting_agenda.py` → shell 0；
   另用 Python 逐文件精确检查新测试、新 note、本报告与业务文件的尾部空白、末尾换行与行尾：
   四个文件均 LF、无尾部空白、末尾有换行。未用 `git diff` 对未跟踪文件冒充结果。

上述命令的完整输出已存 `.agents/worker-runs/20261023-083739-paused-eligible-alignment-followup.log`
（`*.log` 不入库）。

## 未验证 / 不声称

- 未验证 AG-2 自然生成 PAUSED 资格，未验证真实模型自然选择这条 `RESUME`。
- 现场遗留实例（`actv_91acbd742551228b`）无资格、无有效 World 目标、无完成契约，
  **本修复不能解它**，未对其做任何写操作。
- 不是 G1 通过；V-C 恢复仍 FAIL；understanding 仍 UNKNOWN；24h 未做。
- 未运行旧整模块、全量套件、产品模型、浏览器、主实例 probe；未提交/推送/部署。
- 历史账本 ledger13 / 共同 14/96 余 82、本功能 cap5 余 4 均为交接记录，本单未现场复核，
  本单产品请求 0。
