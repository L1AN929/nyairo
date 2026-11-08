# v0.6 D：ambient 背景自然观察入口

Status: implemented

## Problem

v0.6 的 ambient 背景已经在隔离 check 里证明能从 World 的正式 `observe_world` 进入
Life 的 world 背景、live context 和正式 `build_prompt`，但那条检查是在捕获 Decision
请求之后**再调用** `build_prompt` 构造 prompt，能证明结构一致，不能证明脚本 client
真正收到的输入是什么。B/C 的自然观察入口又没有打开 ambient。要回答「这条背景在真实
run 的 client 输入里到底在不在」，需要一个新封闭身份，把 ambient 开关、World gate、
窗口/预算/截止和 client 边界核对一次接全，同时保持旧批与父环境不变。最终落地时本批
只观察 ambient，gate 取 `off`（见 Decision 2）。

## Decision

1. 新增 `g1_real_adoption.V06_D_PROFILE`（`v06-natural-d`）与薄入口
   `scripts/v06_world_natural_d.py`（offline-check / prepare / audit / run）。
   身份、root、独立 authority、run/domain、history 212、1800 秒窗口、900 tick、
   4000 trace、最晚启动 2026-11-04 00:15、绝对截止 00:50 都是本批封闭值；
   `BATCH_CHOICES`、`CLI_BATCH_CHOICES`、`NATURAL_PROFILES` 一个值都不扩。
2. ambient 只在 D 自己的 `_runtime_env` / `_phase_child_env` / World 子进程环境里
   显式写 `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=1`；gate 在本进程树显式 `off`（本批只
   观察 ambient 背景，真实世界动作走既有 gate-off 预提交拒绝链）。父 shell、实例默认、
   旧批都不写这个键。phase 装配前核对这两个值，缺键/错值在起 runtime 之前拒绝。
   上一单按原任务要求写的是 `canonical`；改为 `off` 是父级施工途中的新选择，不倒写历史。
   e/B/C 的 `canonical` 与旧 d/f 的 `off` 均保留。
3. 脚本 Decision transport 增加一个**只在 D 离线**开启的有界捕获（最多 2 条输入，
   存在内存里）。phase 在 `finally` 里用 `_d_ambient_client_view` 只回答：ambient 键
   是否存在、事件数量、事件 ID，以及每个 `event_id` 与公开 `summary` 是否以原文
   精确出现在同一份输入文本里。prompt 正文和 summary 原文都不写进报告/日志。
   事件为 0 时报 0，布尔留 `None`；读不可用保留具名 `reason_code`。
4. D 的跨批累计记账是**本批局部**算术：`expected_history + 已发 >= 1000` 时在
   `_loop_stop` 和两个发送门前停，具名 `TOTAL_ACCOUNTING_CAP_REACHED`；单根 UTC 日
   300 仍由既有 `UTC_DAY_CAP` 负责，两者互不替代。旧批不进入这个判断。
5. D 继承 B/C 的坏账 fail-closed（小写 `corrupt`/`UNREADABLE`）、父/phase/两 owner
   的离线段网络硬拒、严格 world_home/registry/lifecycle 路径守卫和 60 秒收尾冗余。

实测（2026-11-03，`offline-check`，55 项检查全 PASS，18.905 秒，新根
`/tmp/v06-natural-d-offline-ox_zz62a`，单测 18 例 OK）：真实 Supply/World/runtime 子进程
跑完一条脚本 natural phase，client 实际收到的输入里 `ambient` 键存在，1 条
`ambient-2026-11-03-home_window_sound`，事件 ID 与公开 summary 均以原文精确出现；
World 子进程自身的 `child_env_ambient=1`、`child_env_gate=off`；脚本 START 被正式拒绝：
outcome `STEP_REFUSED`、Action `PREPARED`、attempt `PRE_SUBMIT_FAILED`/`GATE_OFF`、
零回执、无 APPLIED、零消费票、世界位置与 Body 姿态前后相同、`pending_unknown=0`；
两 owner 网络自检四拒一允、AF_UNIX 可用；父进程 `os.environ` 前后不变；
真实 D root/authority 始终 ABSENT。client 输入捕获只在离线脚本 client 边界，
不冒真实发送或模型已理解。

## Alternatives considered

- **沿用 C 的做法，在捕获 Decision 请求后再调 `build_prompt`**：最省，不需要动
  transport。否决：那证明的是「同一请求能构造出含 ambient 的 prompt」，不是脚本 client
  真正收到的输入；本次任务明确要求在 client 边界核对，事后重建会掩盖 adapter 在
  `_fit_prompt` 里对 world 列表的裁剪。
- **让 D 完全复制 C 的 10800 秒三小时窗口**：能复用更多常量。否决：本人这次批准的是
  1800 真实秒、00:15 最晚启动、00:50 绝对停；照抄 C 会改变本批时间语义，也可能撞上
  整机关机。
- **把累计 1000 做成持久跨根总账**：能自动跨批对账。否决：那需要新的持久 schema 和跨根
  权威，本批只批准「局部记账检查」；不把提额冒成自动总账已实现。
- **给 ambient 新增候选或任务**：会让背景直接驱动选择。否决：ambient 仍是背景，
  候选、合法集、gate 和排序都不变；是否因它改变选择由真实 run 自然观察，没出现就记未覆盖。

## Consequences

- 收益：D 的离线检查第一次在脚本 client 的**实际输入**上证明 ambient ID 与公开 summary
  精确透传，而不是事后重建 prompt；旧 B/C/a–f 的封闭值、CLI 枚举和 gate/坏账语义一个未改。
- 收益：ambient 开关、off gate 与正常拒绝链、路径隔离、预算/截止、收尾界限在同一个封闭
  身份里接全，父级只需 prepare 后跑一次真实 run。
- 代价：真实 run 每个决策轮多一次 World 投影读取（仅 ambient 启用时）；读取失败会显示具名
  `AMBIENT_*` 未知，而不是静默缺失。
- 边界：离线检查只证明接线与边界透传，不证明真实模型理解、行为因果、G1 或 24h；
  当前 World 无事件时如实 0，非产品 fixture 不算真实世界非空证据。真实 D root 与
  authority 在 worker 阶段始终 ABSENT，真实 prepare/run 由父级唯一执行。

相关：[v0.6 ambient 背景接入 Life](../bug-fix/2026-11-02-v06-ambient-life-background.md)、
[C 自然观察离线入口](2026-11-02-v06-natural-c-offline-entry.md)、
[ambient 隔离 off/on 分层检查](2026-11-02-v06-ambient-isolated-check.md)。
