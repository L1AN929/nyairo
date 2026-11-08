# Agent Note: 暂停作品的正式回来链，需要在两个 PID 上跑完整链才算证据

Status: implemented

## Problem

既有三份决定已经把条件 PAUSE 的规则写清楚：本轮 offered basis + Decision 显式
`reevaluation_hint_ref`、owner 满足后才发 OPEN 资格、PAUSE 释放执行槽、后台 RESUME
不抢占、回到同一 Activity 仍由新鲜 Decision 自己选。相关决定见
[PAUSED 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)、
[显式条件 PAUSE](../feature/2026-10-24-paused-explicit-resume-condition.md) 和
[PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)。

但已有证据是分开的：`tests/test_paused_explicit_condition.py` 用真实 LR-2/LR-4 owner
证明绑定与资格，却不经过正式 `ProductionLifeRuntime`、AG-1/AG-2 与 runner；
`scripts/paused_explicit_condition_smoke.py` 走正式 assembly，但只有一个 PID，且直接调
`cycle.maintain_waiting`；`tests/test_restart_writer_input_chain.py` 证明双 PID 续写，
却不覆盖条件 PAUSE 和 RESUME。没有人把「真 START -> 合法 no_change -> 条件 PAUSE ->
进程退出 -> 同根重装配 -> runner maintain 发 OPEN -> 候选材料化 -> 新鲜 RESUME ->
同 Activity 续写」这一整条正式链一次跑完。

本单先核对实现，未发现同契约的内部不一致：offered hint、AG-1 allow-list、AG-2 绑定、
LR-4 宿主谓词、资格消费、前台冲突拒绝、候选材料化都能按既定规则接上。因此按任务书只补
正式整链证据，生产代码 0 改。

## Decision

新增 `tests/test_paused_work_resume_chain.py`，四个聚合 case，全部复用既有真实 owner 与
现有离线硬禁网：

1. **双 PID 正链。** P1 子进程用正式 runtime/assembly/runner 做真 START（真写一版）、
   合法 CONTINUE + 内容 `no_change`、条件 PAUSE；断言同一 Activity、pause revision、
   `foreground_released`、condition 绑定 revision 与 PENDING 状态。P1 正规关闭后，P2
   （unittest 进程，另一个 PID）在同一 scratch 根重装配；条件未满足时无 OPEN 资格、无
   RESUME 合法集、无多余 content；真实 Supply owner 提交新 head 后，`runner.maintain`
   零模型发出一个 OPEN 资格；正式 `CandidateMaterializer` 给出匹配的 RESUME candidate；
   新鲜 Decision 选 RESUME 回到同一 Activity；RESUME 同轮未写时由下一轮正常 CONTINUE
   写出新版本，owner version/head 确证。
2. **负控。** 无条件 PAUSE 不绑定条件、不发资格；Decision 点名未 offered 的 hint 被正式
   validator 拒绝且 Activity 保持 ACTIVE；owner 不可读时 `maintain` 只报
   `OWNER_HEAD_UNREADABLE`，条件保持 PENDING，不发伪 OPEN。
3. **占槽冲突。** A 的条件满足且资格 OPEN 后，正式 START 另一件真实作品 B；B ACTIVE 时
   候选材料化与 AG-1 合法集都不给 A 的 RESUME，B 不被抢占，A 资格仍 OPEN；正式暂停 B
   后不会自动回 A；新鲜 NO_ACTION 零写合法；再新鲜 RESUME 才消费 A 的资格，B 仍 PAUSED。
4. **返回后的 no_change 与幂等。** 回到同 A 后合法 CONTINUE + 内容 `no_change` 零 owner
   新提交、Decision 记录仍在；重复同一条件事件不重发资格；同一恢复操作再次调用被
   `EligibilityNotOpenError` 拒绝，Activity revision 与资格消费状态不变。

Decision 与内容都由明确标注的脚本 transport 回答；真实件是真实 Supply AF_UNIX owner、
`ProductionLifeRuntime`、正式 assembly/runner、AG-0/AG-1/AG-2/LR-2/LR-4、
`ModelContinuationWriter` 与 `AgencyModelClient`。父进程与 P1 子进程各自安装并自检既有
`g1_real_adoption._offline_network_guard`（AF_INET/AF_INET6/DNS/create_connection 拒绝，
AF_UNIX 放行），`prepare_fixture` 也在同一守卫内自检。scratch 用 `mkdtemp` 保留，不
`rmtree`/`unlink`；owner 停止走既有 `shutdown -> server_close -> drain ->
prepare_socket_path -> close` 顺序。

## Alternatives considered

1. **直接拿 `scripts/paused_explicit_condition_smoke.py` 当整链证据。**
   最强理由：它已经是真实 owner + 正式 runtime/assembly，改动最小。
   否决：单 PID，且直接调 `cycle.maintain_waiting`，没有验证进程退出后的同根重装配、
   runner maintain 接缝和正式 `CandidateMaterializer` 的 RESUME 供给；把既有 smoke
   说成完整链会扩大它的证据范围。
2. **只加 LR-4/AG-2 的单元用例。**
   最强理由：跑得快、定位准，条件、资格、RESUME 的每个谓词都能单独钉住。
   否决：任务明确不把私有 resume 函数 unit test 冒充完整链；单元用例无法证明 offered
   hint 是从正式 Decision 一路到 LR-4、也无法证明 runner/候选/同作续接。
3. **为了“像 bug”去改生产接线。**
   最强理由：任务书预留了“确证内部接线修复”的写集，改了就能交一个更重的单。
   否决：核对后没有发现同契约不一致；为了让工单显得有产出而改生产代码，会制造没有
   依据的行为变化，违反“不能为了继续施工造 bug”。
4. **把 Supply owner 也拆成独立子进程。**
   最强理由：可以让“owner 子进程”与 phase 子进程各自装守卫，边界更干净。
   否决：既有整链测试（`tests/test_one_day_life.py` 的 `Services`）就是在 phase 进程内
   起真实 `LifeSupplySocketServer`；本单要证的是 P1 退出后的同根重装配与返回链，不是
   owner 的进程托管方式。把 owner 再拆一层会显著扩大夹具，却不增加本单要验的接缝。
   边界已在下方写明：owner 是真实 AF_UNIX server，但不在独立 OS 进程里。

## Consequences

- 证据：`MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend &&
  /tmp/rc104-venv/bin/python -B -m unittest tests.test_paused_work_resume_chain'` 在本单
  施工侧跑出 `Ran 4 tests ... OK`、exit 0；父级独立复跑以父级自己的记录为准，本笔记不代
  它宣称。
- 生产过程代码 0 改；没有新增字段、condition kind、owner、权限、默认 gate、TTL、预算或
  调度。产品请求 0（脚本 transport 不联网、不调真实 provider），累计 557/1000 不变。
- 真实件：Supply AF_UNIX owner、`ProductionLifeRuntime`、正式 assembly/runner、
  AG-0/AG-1/AG-2/LR-2/LR-4、`ModelContinuationWriter`、`AgencyModelClient`。替身只有
  Decision 回答与内容回答，且都在代码和报告里明确标注为脚本。
- 未覆盖：真实模型是否会自然选择条件 PAUSE/RESUME、心理动机、24h、G1 真实 EL4、
  崩溃恢复（本单是正常有序停启，不是 crash 矩阵）、旧 PAUSED 指针迁移、真实撤权与非
  Artifact 来源撤权。Supply owner 由既有 `Services` 在同一进程线程内提供真实 socket
  server；P1/P2 是不同 OS PID，但 owner 本身不是独立子进程。
- 出现以下信号应重访：正式链再次出现“条件满足但没有 RESUME 候选”；双 PID 重装配后
  runner maintain 不能接上 LR-4；或 RESUME 同轮写入/下一轮 CONTINUE 的合同发生变化。
