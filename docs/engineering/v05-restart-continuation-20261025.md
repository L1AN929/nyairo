# v0.5：同作品实践与本地表达的两进程同根承接（2026-10-25）

## 这一块解决什么

前几块分别证明了：结果级身份（含同根新进程不重复交接）、真实 owner 方法评价、作品来源
换版后的草稿拒发、显式新草稿与一条本地回执。它们**不是都在同一个进程里**跑完——结果身份
那块已有跨进程去重证据，其余各块在单进程内跑通。这一块补的是**组合**缺口：把方法评价、
作品版本、延后草稿一起放进**真正不同的 PID**，在同一个新根上重开真实 owner 存储与
AG-2 store，它们还成立吗？

不是重做整套功能，也不新增持久 usage、Memory 门、owner 合同或披露规则。业务源码零改动。

## 链怎么跑

父调度器只建一个隔离夹具根（世界包、`odl.prepare` 的实例、空 M0 Memory），然后：

- **P1（PID A）**：自己起真实 Supply owner；正式 `evaluate_once` → AG-1 原始 JSON
  脚本 Decision → AG-2 adoption → AR-0 真实写作。第一轮 START 写出作品 A 的下一版，
  得到 owner 结果身份与 handoff；第二轮在同一 Activity 内 CONTINUE A 再写一版，
  并用真实 owner 经历记一条 HELPED 评价，方法进入 TRIAL。随后正式 prepare 草稿 D1
  绑定当前 head、合法 defer；另建作品 B 作不串目标对照。P1 关闭 runtime（释放 lease）
  并关停自己的 owner 后退出。
- **P2（PID B，与 P1 不同）**：重开同一个根的 Supply canonical db、M0 Memory 与 AG-2
  store，不复制内存状态。第一次正式请求纯读：owner head、版本数、handoff 数、
  receipt 数都不变，请求里能看到 D1（DEFERRED）、TRIAL 方法与有界最近一条显式评价，
  但不自动续作或分享。
  随后合法 CONTINUE 同一 Activity 续写 A，B 不动；新结果得到新身份，不复用旧事件。
  用既有 `emit_experience_handoff` 重投 P1 的同一确证结果：同 id、事件/经历不增、
  不重放 Decision/Action/writer。旧稿 D1 在请求里如实报 stale，发送被 owner 以
  `stale_source` 拒绝且零写。显式 prepare D2 绑定新版本，`send_local` 只产生一条
  本地回执（外部/已读/确认/理解全 false），重复发送仍是同一条；同一 prepare 意图重放
  仍按既有合同只留一份草稿（第二次 `create` 被 owner 如实拒，不是第二份草稿）。最后真实 owner CAS
  退休方法：旧 `method_ref` 被 AG-1 以 `INVENTED_METHOD_REF` 拒绝且不提交 Decision，
  不带旧 ref 的 NO_ACTION 仍合法，writer 视图不再带退休指令。
- **P3（PID C，纯读）**：再开一次同根 runtime，只核对 A/B 的 head 与版本数、D1/D2
  的状态与 receipt、handoff 与经历计数稳定；不跑 Decision、不写版本、不投递。

父进程最后自己再起一次只读 owner，从 canonical db 直接读回 A/B 与 D1/D2，不只信子进程报告。

### 版本号说明

夹具里 A 的初版是 prepare 时创建的 v1。P1 第一轮写 v2（结果身份），第二轮在同一
Activity 写 v3 并完成方法评价，D1 绑定 v3；P2 续写 v4。任务书里的“v2/v3”是相对序号，
实际落盘是 v1→v2→v3→v4，顺序与语义一致。

## 什么是真的，什么是替身

真的：Supply / Practice / Sharing owner（AF_UNIX + 落盘 canonical db）、M0 Memory、
ProductionLifeRuntime、AG-0/AG-1/AG-2/AR-0、两个/三个不同 PID、每阶段自己起停的 owner
（关停顺序见“恢复边界”：排空后由 owner 自己的 socket 守卫清理自有路径）、关闭后释放的
runtime lease。

替身：Decision 回答与内容回答都是脚本形状（原始 JSON / 固定 edit），不调用真实产品模型；
观察时刻由夹具按既有 6 小时 TTL 之外步进，不是自然日。没有外发、没有访问旧实例或密钥。

## 恢复边界

这是**正常有序停启**：每阶段先 `runtime.close()` 释放 lease，再由本脚本的 `_stop_services`
关停自己进程内的 owner，然后退出；下一阶段在同一路径重新 bind。关停顺序对齐生产
`run_service`：`server.shutdown()`（停止接新连接、`serve_forever` 退出）→
`server.server_close()` → `server.drain(10.0)`（有界等待在途请求结束）→
`prepare_socket_path()`（owner 自己的守卫：仅当路径是本 uid 的 socket 且无活实例监听时
才删除；非 socket、他人 uid、仍在监听一律原样报错并保留路径）→ `service.close()`。

**不再由脚本裸 `unlink`。** 夹具 `Services.stop()` 本身没有 `drain()` 也不删 socket，
所以 `_stop_services` 局部使用它已公开的 `server` / `service` 属性按上述顺序关停，
不改共享夹具实现。该 helper 只接受本单新建隔离根路径
（`<services.root>/life-supply.sock`），其他路径在动手前就报错。

这是一次安全返修：上一版 `_stop_services` 先 `Services.stop()` 再
`socket_path.unlink(missing_ok=True)`，被父级按原工单“不手删 socket”退回；本次保留完整
主链，只补正规排空与守卫。

顺序上仍是先停后删，不是运行中删除；“有序关闭”不因此升级为 crash 恢复。不是 crash 矩阵，
不是断电/半写恢复，也不是完整 resident 循环恢复。跨备份防复活、撤权治理、真实模型自然采用、
G1、24h 都没有被这一块覆盖。

## 实际验收

以下是 socket 安全返修后**施工方的本地实跑结果**
（WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`）。
父级随后独立复跑修后同一A/B：A `Ran 2 tests in 23.723s ... OK`、`EXIT_A=0`；B 36项全部PASS、`EXIT_B=0`。三个阶段均记录 `drained=true` / `STALE_SOCKET_REMOVED`，限定验收通过。日志：`.agents/worker-runs/20261025-v05-restart-final-parent-a.log`、`20261025-v05-restart-final-parent-b.log`。以下保留施工方先前实跑值，不混同父级结果：

- A `-m unittest -q tests.test_v05_restart_continuation`：`Ran 2 tests in 36.841s`，`OK`，
  exit 0（同一改动此前一次 25.321s 也 OK）。新增负控确认：live socket 被守卫以
  `REFUSING_TO_UNLINK_LIVE_SOCKET` 拒绝且保留、普通文件以
  `REFUSING_TO_UNLINK_NON_SOCKET` 拒绝且保留、已停的自有 socket 在 `drained=True` 后
  由同一守卫以 `STALE_SOCKET_REMOVED` 清理。
- B `scripts/v05_restart_continuation_smoke.py`：36 项 check 全 PASS，`RESULT: PASS`，
  exit 0；三阶段均记录 `service_drained=True` 与 `socket_handling=STALE_SOCKET_REMOVED`。
  安全表仍列出三个不同 PID、同一 root、A/B 版本数、D1/D2 与 receipt/handoff 计数，
  并新增三阶段 `shutdown` 元数据；其中 `receipt_counts` 是草稿状态计数，不是投递次数
  （本地投递只有 D2 的一条）。

失败史保留：上一版 A（1 test/24.968s）与 B（35 checks）虽绿，但因 `_stop_services` 裸
`unlink` 被父级按原工单“不手删 socket”退回；本次只修关停安全，不重做主链。

两条命令在脚本内部都会启动不同 PID 的子进程，并读回真实落盘；脚本里的“父进程最后再起一次
只读 owner”是脚本自身行为，不等于监工侧独立验收。

## 没验什么

- Decision 与内容仍是脚本替身：不证明真实模型自然采用这些事实。
- 只测正常停启，不证明崩溃、强制终止、半写或并发重启下的恢复。
- 本地回执不等于用户收到或已读；外发仍关闭。
- 不重跑已绿的四块（A43/B37、A44/B36、A67/B24、A15/B38），也不代表 0.5 整版完成。

证据：`tests/test_v05_restart_continuation.py`、
`scripts/v05_restart_continuation_smoke.py`。
