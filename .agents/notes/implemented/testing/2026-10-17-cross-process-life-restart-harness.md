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
**抛错并 dump 全部候选**，不回落。每次请求的真实候选与选择都写进 worker 报告
（`cognition_seen` / `cognition_chosen`），复验者不必猜。

## 负控按收紧后的矩阵改判（2026-10-26）

AG-1 在 `foreground_holds_occupancy`（前台引用还在、且不是 WAITING）时**不再提供**必被拒的 START
（`.agents/notes/implemented/architecture/2026-10-26-v04-day-life-decision-legality.md`）。于是本用例
第二轮那个窗口的 offer 只剩一件能走下去的形状：工作产物的 `CONSIDER_PERSONAL_OPPORTUNITY` 只剩
`DEFER`、阅读产物的 `CONSIDER_PERSONAL_OPPORTUNITY` 只剩 `DEFER`、地点的
`CONSIDER_WORLD_OPPORTUNITY` 只剩 `DEFER`，只有当前活动的 `CONTINUE_CURRENT` 还有
`DEFER/CONTINUE/PAUSE/ABANDON`。

旧写法因此自己变红：父级 `scratch/day-life-final-exact-gen1.log`（`tests/test_life_v1_restart.py:388`）
要求"窗口里仍有一个 START"，而该窗口四条候选里唯一带非 DEFER 的只有那条 CONTINUE_CURRENT；旧的
`StartControlLoopback` 也过时——它只挑"AG-1 真的允许 START 的候选"，收紧后的窗口里根本不存在这种
候选。两处都在测试侧改，产品源码未动：

- 正文（父进程断言）：ACTIVE 同窗口**没有任何候选合法 START**（逐条断言，不是"某处有一个"），并单独
  精确断言原 work 产物的 personal 候选 `legal` 里没有 START；合法续写的是**同一个 Activity 的新版本**
  （CONTINUE → `owner committed`、head 出新版本、Activity 仍 ACTIVE）。
- 负控（worker 里的 `StartControlLoopback`）：从本轮**真实候选**里取 `CONSIDER_PERSONAL_OPPORTUNITY`
  且提到原 work 的那一条（按身份挑，不按顺序），先断言它的 `legal_decisions` 里**没有** START
  （`start_offered=False`；若仍在提供就抛错并打印整份 window），再把 `decision=START` 强行塞进
  `raw_output`。
- 精确拒绝层级：现在拒它的是 **AG-1**，`outcome=DECISION_FAILED`、
  `detail=INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`（`FAILURE_CLASS_ILLEGAL_DECISION_MATRIX`）、
  `decision=None`、`adoption_status=None`、`llm_calls=1`。**不再**写死 AG-2 的
  `REJECTED_STALE`/`CANNOT_START_WHILE_FOREGROUND_ACTIVE`——那是收紧前的层级，理由作为历史保留在上面。
  断言只接受这一种具体失败类，不接受"任意失败"。
- 硬断言逐条保留：跨进程同 run、预算 reserve 跨进程延续、暂停轮 0 calls、同一 Activity、新 head、
  非法 START 之后 head 与 Activity revision 都不变、书签仍在、空档期 Memory 不写。
- 挑不到原 work 的 personal 候选就抛错并 dump 全部候选，**不回落**到别的候选（回落会把"没找到"
  变成一次伪选择，正是本笔记开头那类根因）。

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

## Verification

- 唯一 exact ID：`tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`。
  单跑 `Ran 1 test ... OK`、`EXIT=0`（18.084s），日志 `scratch/day-life-final-restart-gen2.log`（末尾带真实
  returncode）。按指示**只跑这一条**，没有重跑前三绿，也没有跑整组。
- 红证保留：`scratch/day-life-final-exact-gen1.log`（旧断言在 `tests/test_life_v1_restart.py:388` 红）
  原样未删未改；`day-life-final-consume-exact-gen1.log` 同样未动。
- 产品源码零改动（只改 `tests/test_life_v1_restart.py` 与本笔记）；`PYTHONDONTWRITEBYTECODE=1`、
  `TMPDIR=/tmp/nyaw05r.jdK3q`、`PYTHONPATH=.../v03-retain-hooks`（保留 fixture / 事务退役 rename / socket
  保留）；无清理、无 `py_compile`、无全库或全树 SHA。
- 未验证的部分（具名）：worker 仍不是 resident 循环；真实模型、24h、外网、发布/commit 都没有涉及。
