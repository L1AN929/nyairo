# G1 同一作品恢复（2026-10-28）

可重复用法：`scripts/g1_same_work_recovery_demo.py`。父进程只建隔离根；P1/P2 两个不同 PID
各自装配真实 runtime，全部操作落在同一件作品 A 上（B 只做不串目标对照）。

链上依次发生的真事件：

1. P1 经真实 Supply owner 写出第一版与正式 Activity；operator pause 中断；正规释放 lease、
   停 owner 后退出。
2. P2 同根新 runtime：暂停轮 0 请求零写；正式恢复后新 Decision 继续同一 Activity 出新版本。
3. 适配器丢弃回执（真实写入已落地）→ AR-0 `UNKNOWN` → 只用 owner operation 对账
   `CONFIRMED_SUCCEEDED`，无裸重发，版本只增一次。
4. 一轮正式 `evaluate_once`（脚本 Decision `CONTINUE`）把 A 的当前 head 冻结进 AR-0 payload；
   submission fence 落盘后，demo 层把这一次 `adapter.submit` 包一层（限定 A、只用一次），
   让真实 owner 的另一个 writer 先推进 head；旧 `expected_head` 的正式提交因此被 owner 以
   `CONFLICT` 拒（Action `FAILED` / `DEFINITE_PROVIDER_REJECTION`）：零版本、零 evidence、
   Activity 不动、B 不动；之后新 Decision 读当前 head 继续 A。
5. `NO_ACTION` 一轮零写；之后仍能继续 A 出新版本。

运行（WSL）：

```
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_same_work_recovery_demo.py
```

本次结果（2026-10-29 重跑）：28 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；PID 16645 →
16874；A 最终 7 版，B 未动。CAS 一段现在是正式链：AG-1 提交 `CONTINUE`、AG-2 绑定同一
Activity，AR-0 提交被 owner 以 `CONFLICT` 拒，`G1_PHASE` 只打印白名单字段。

边界：Decision/内容是脚本替身；真实件是各 owner、ProductionLifeRuntime、AG-0/1/2、AR-0。
这是正常有序停启加有界故障注入，不是 crash 矩阵，也不代表真实模型自然采用或完整 G1。

相关：`.agents/notes/implemented/testing/2026-10-28-g1-same-work-recovery.md`、
`tests/test_g1_same_work_recovery.py`、`.agents/notes/implemented/testing/2026-10-17-cross-process-life-restart-harness.md`。
