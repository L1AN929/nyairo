# Agent Note: 生活实例的"重启"必须由两个独立进程各装配真实 runtime 才能算证

Status: implemented

## Problem

D 首轮把"在同一进程里关掉旧 runtime 再重建"当成重启证据，D-R1 又退成"另一个 PID 只读读盘 probe"
（读 run identity、计数器、pause、书签）。两者都没有回答真正的问题：**同一个生活实例，在一个进程真的
退出、canonical writer lease 真的释放之后，新进程能不能重新装配出真实的 runtime、预算对象与 Memory
接线，并从暂停态继续原来那件 Activity**。只读 probe 只能证明文件读得到；同进程重建连 PID 都没换。

## Decision

`tests/test_life_v1_restart.py` 用两个独立 worker 子进程证明这件事，且**父进程不建任何 runtime**
（否则 lease 一直在父进程手里，第二个真实装配根本起不来）：

1. **worker A（PID A）**：从实例环境装配真实 `ProductionLifeRuntime`（真实 Supply/World/Activity/Memory 边界、
   AR-0 适配器、真实 `MemoryAdmissionExperienceConsumer`）+ `assemble_autonomous_life`（实例自己的
   `LifeRequestBudget`），真跑一步合法本地行动（owner receipt + 新版本），写入 operator pause 与一次请求
   预算保留，然后 `runtime.close()` **释放** lease，正常退出。
2. **worker B（PID B）**：同一 data root，**新** runtime、**新** assembly、**新**预算对象，先证明
   `state_class == PAUSED` 且这一轮 `llm_calls == 0`、决策分支调用 0 次、owner head 未变；随后 resume 并
   合法继续**同一个** Activity，owner 产生真实新版本。
3. 断言：两个 PID 与父进程互不相同；run identity 相同；`utc_day_calls`/`utc_day_max`/`run_window_max`
   原样延续（不是重置）；同一 `activity_ref` 续上；书签仍在；两次 worker 之间的空档期没有任何写入
   （head 与 M0 行数不变）。

worker 里的决策与内容仍是确定性替身（`StubCognition`/`StubWriter`），owner 全是真实本地服务；不含真实产品
模型、外网、长跑。旧的只读 probe 保留为更弱的一层证据，不再被称为 full restart。

## 决策分支必须按合法性选，且失败要大声

父级独立复验第一次是红的：worker B 的一轮出现 `decision=START` → `adoption_status=REJECTED_STALE`
（拒绝原因是 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`）。真实窗口里的候选就四件——工作产物的
`CONSIDER_PERSONAL_OPPORTUNITY`(START)、当前活动的 `CONTINUE_CURRENT`(CONTINUE)、阅读产物的
`CONSIDER_PERSONAL_OPPORTUNITY`(START)、地点的 `CONSIDER_WORLD_OPPORTUNITY`(START)。

根因在 harness 自己的分支，不在产品：`WorkLoopback` 原来**按候选列表顺序**取第一个"提到该产物且 START/CONTINUE 合法"的候选，
而工作产物的 START 候选排在 CONTINUE 之前；START 在有 ACTIVE 前台时本来就不合法，AG-2 拒绝它是正确行为。
更糟的是，一个都没匹配上时会**静默回落**到通用 `StubCognition`（它也取 START），把"没找到"变成一次伪选择。

现在的规则：① 提到该产物且 `CONTINUE` 合法 → CONTINUE；② 否则 `START` 合法 → START；③ 两者都没有 →
**抛错并 dump 全部候选**，不回落。同一窗口另加一条精确负控：强制 START → 必须被拒
（`REJECTED_STALE` / `CANNOT_START_WHILE_FOREGROUND_ACTIVE`）且 head 与 Activity revision 不变。
每次请求的真实候选与选择都写进 worker 报告（`cognition_seen` / `cognition_chosen`），复验者不必猜。

## Alternatives considered

- **父进程持有 runtime，只起一个子进程读盘**（旧做法） — 最省事、也是既有的 `DRestartAndQuietTest`；但
  lease 没释放过，第二个真实 runtime 根本不会被尝试，所以它证明不了"重启后还能生活"，只能证明"文件还在"。
- **在同一个进程里 `close()` 旧 runtime 再 `build_production_life_runtime()` 重建** — 能覆盖"装配可重入"，
  但 PID 没变，进程内残留状态（模块级缓存、已打开的连接、旧 cycle 对象）都可能让恢复看起来比实际更容易；
  D 首轮就是这么做的，父级明确不接受它作为重启证据。
- **让 worker 常驻 resident 循环** — 更接近生产形态；但会把一次验收变成一次短跑加超时治理，且 resident 自己的
  循环/时钟不在本轮范围。用"一次 `evaluate_once`"就足以证明装配与恢复，不需要引入循环。

## Consequences

- **收益**：重启从"读盘 probe / 同进程重建"升级为"两个进程各自装配真实 runtime 并续上同一 Activity"，
  证据强度与原单第 6 条对齐；`close()` 必须真的释放 lease 这一点也被测试钉住。
- **代价与已知上限**：一条用例多两个子进程（约 13 秒）；worker 不是 resident 循环，所以入口状态如实报
  `STOPPED` 而不是虚构 `IDLE`；父进程刻意不建 runtime，因此这条用例不能复用"父进程先跑一轮"的写法。
