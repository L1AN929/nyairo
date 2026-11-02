# Agent Note: 同一作品在中断、重启、UNKNOWN、CAS 冲突与 NO_ACTION 之后还能继续

Status: implemented

## Problem

跨进程重启（`2026-10-17-cross-process-life-restart-harness`）已经证明了"两个独立进程各装配
真实 runtime、暂停轮零请求、恢复后继续同一 Activity"。但它只覆盖**正常有序停启**：一次丢回执
的 UNKNOWN、一次被并发写者抢走的 CAS 冲突、以及冲突后怎样重新读 head 再续，都还散在不同
机制里，没有一条连续用法把它们接在**同一件真实作品、同一个隔离根**上。分散的 PASS 拼不出
"中断之后她还能接着做这件事"。

## Decision

新增 `scripts/g1_same_work_recovery_demo.py`：父进程只建隔离根，两个不同 PID 的子进程各自
装配真实 `ProductionLifeRuntime`。每一步都读上一步真实落盘的 owner 状态：

1. **P1**：正式 `evaluate_once` 经真实 Supply owner 写出作品 A 的第一版和正式 Activity，另建
   作品 B 作对照；用既有 `operator pause` 接口中断（不伪造 LR-4 资格），关闭 runtime 释放
   canonical lease，停掉自己的 owner 后退出。
2. **P2**（不同 PID、同根、新 runtime/预算）：暂停轮 `PAUSED_BY_OPERATOR`、0 请求、head/版本/
   M0 都不变；经正式解除接口恢复后，新 Decision 继续**同一个** Activity，owner 真出新版本。
3. **UNKNOWN**：直接用适配器已有的测试故障注入 `fault_injection_drop_response_after_write`
   ——owner 真实写入成功、回执丢失，AR-0 保持 `UNKNOWN`；随后只调 `reconcile_action`（内部走
   owner 自己的 `resolve_operation`）确认为 `CONFIRMED_SUCCEEDED` 并观察到 effect。零裸重发，
   A 的版本只增加那一次。
4. **CAS 冲突**：走一轮完整 `evaluate_once`，脚本 Decision 选 `CONTINUE`，AG-1 提交、AG-2
   把 step 绑定到同一 Activity、AR-0 读出 A 的当前 head 并冻结进 payload；在 submission
   fence 落盘后、adapter 调 owner 之前，demo 层把这一次 `adapter.submit` 包一层（限定 A、
   只用一次），让同一个 Supply owner 的另一个真实 writer 先推进 head。正式链于是带着过期的
   `expected_head` 过闸：owner 用 `CONFLICT` 拒，Action 记 `FAILED` / `OWNER_CONFLICT` /
   `DEFINITE_PROVIDER_REJECTION`，零新版本、零 result evidence、Activity 状态与 revision
   不变、B 不动。之后新 Decision 读当前 head 再沿现规则续 A。
5. **NO_ACTION**：合法无附带写的一轮，head/版本/B/M0 都不变；再下一轮仍能继续 A 出新版本。

6. **报告面**：`G1_PHASE` 只打印白名单（PID/root/run/artifact/activity 安全 ID、各步
   outcome/refusal/action/owner 版本计数、close/drained），完整 phase JSON 落在新根里；
   错误只打印截断的最后一行，完整 stderr 也落在新根，不 `repr` 整份 out 或 owner 对象。

生产源码零改动：adapters 的故障注入、`resolve_operation`/`reconcile_action`、CAS
`expected_head` 都是既有协议。Decision/内容仍是脚本替身；真实件是 Supply/World/Activity/
Memory owner、`ProductionLifeRuntime`、AG-0/AG-1/AG-2/AR-0。

## Alternatives considered

- **用五套互不相干的小 fixture 各证一段，再拼成一次 PASS**：最省事，也是现有零散证据的形态；
  但每一步之间没有真实 persisted state 接力，无法回答"同一件作品中断后还能不能接着做"，
  正是本轮要排除的拼凑。
- **自造一个新的 UNKNOWN 查询/重试合同**：能让演示更"整齐"；但既有 `resolve_operation` 与
  `reconcile_action` 已经定义了"先对账、禁止裸重发"，新增合同会把一个已验证的协议换成未经
  审查的新协议，超出本单范围（触发即停）。
- **用 World 动作或换一件作品来绕开 Artifact 的 CAS/UNKNOWN**：会掩盖真正的断链，也破坏
  "同一作品"这个前提，不采用。
- **在 demo 里直接 propose/prepare/submit 一个 AR-0 动作（首版做法）**：它最强的理由是
  时序完全可控——先提交意图、再让另一 writer 抢 head、再 submit，能稳定造出 CAS 拒绝。
  但它绕过了正式决定链：没有 AG-1 提交的 Decision，也没有 AG-2 的 Activity 绑定，"owner 会
  拒过期 head"这个结论就落不到"她的一轮真实决定撞上并发写"上。因此改为正式
  `evaluate_once` + 一次性 `adapter.submit` 包装，只在既有接缝注入并发写。

## Consequences

- **收益**：中断、重启、UNKNOWN、CAS 冲突、NO_ACTION 现在有一条可重复执行的连续用法，全部
  落在同一作品 A 上；每段都有 owner 侧可核对的事实（版本数、head、operation 对账、receipt）。
- **代价与上限**：仍是正常有序停启 + 有界故障注入，不是 crash/强杀矩阵；operator pause 是
  "整个生活暂停"，不是自主有条件 PAUSE；Decision/内容为脚本替身，不能据此宣称真实模型自然
  采用、完整 G1、EL4 或 24h。Activity 在所有轮次保持同一 revision 是现有规则（CONTINUE 不改
  Activity revision），不是本演示刻意凑出的状态。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_same_work_recovery_demo.py`
  → 28 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；两个阶段 PID 16645/16874，A 最终 7 版，
  B 全程未动；CAS 轮 `STEP_REFUSED`、`decision_id` 非空、Action `FAILED` /
  `OWNER_CONFLICT`、Activity 状态与 revision 不变。
- 回归入口：`tests/test_g1_same_work_recovery.py`（POSIX）起一次该演示并断言
  `RESULT: PASS` 与六条关键 CHECK（含正式 CAS 链与一次性注入）。
- 未跑全库、未碰旧实例、未调用任何产品模型。
