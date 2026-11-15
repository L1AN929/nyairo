# Agent Note: 0.7 唯一真实短日 A 的薄封闭入口

Status: implemented

## Problem

2026-11-08 本人直接批准唯一一次 0.7 真实短日 Live Run（到 11-09 01:00 CST 收口）。
需要一条固定身份、固定预算、一次性、可离线验收的入口，把已有的正式日常 runtime
（`fast_day` 的新隔离实例、持久业务时钟、`ExperimentRequestCap` 每次预留前的硬上限、
`bounded_failstop`、真时间 watchdog 与正规 stop-close）接到这一根上；不允许复制 G1
多 phase、另造生活总脑，或改动 0.7 生活状态机/World owner/Memory 准入/权限/模型
cap/超时/重试。

## Decision

新增 `scripts/v07_live_a.py`（offline-check / prepare / audit / status / run）与
`tests/test_v07_live_a.py`：

- 身份封闭在代码里，没有 `--root`：root `/tmp/nyairo-v07-live-20261108-a`、独立
  authority、run `run:v07-live:20261108-a`、域 `v07:live:20261108-a`、history 557、
  本 run 新增 20、UTC 日 300、共同总限 1000（最坏 557+20=577）、逻辑起点
  `2026-11-09T11:00:00+09:00`、1:1（1800 逻辑秒 = 1800 真实秒，rate=1.0）、最晚
  00:15 CST 启动、00:50 CST 绝对停。
- `prepare`/`run` 薄包装 `scripts/fast_day.py`；`fast_day` 只加两处默认不变的接缝：
  `prepare(domain_id=...)` 与 `FastDaySupervisor(room_living=..., ambient_events=...)`
  （默认值保持旧行为）。room_living 通过既有 `demo.effective_features` /
  `demo._serve_feature_overrides` 同时到达 World 子进程与 resident 环境，canonical
  走既有进程环境开关，economy 仍是既有 owner 开关。
- 一次性：`run` 在拉起 supervisor 之前用 `O_CREAT|O_EXCL` 写持久消费标记；没有删除
  路径，重复启动/进程中断都不复活。预算仍由既有 `CappedRequestBudget` 在**每次**
  Decision/content 预留前强制 20；失败预留照扣。到 20 次时只写既有的
  `fastday_control.json` stop 位，让 supervisor 下一轮按自己的 stop-close 收尾。
- `audit`/`status` 直接读盘上的时钟/实验额度/账本/记录文件，不调用会建锁或推高水位
  的 `LifeRequestBudget.view()` / `ExperimentRequestCap.view()`；缺账只在本实例实验
  闸门明确记 0 时报 0，否则 `UNKNOWN`。输出只含固定枚举、布尔、计数、安全 id、时间、
  地点/Activity 状态，不抄 prompt/completion/reasoning/正文/env/token/异常原文。
- 离线验收只跑 `offline-check`：新测试收集运行 + 一条最短正式零模型 scratch 装配
  （真实 Supply/World/resident + 注入一次脚本 provider 失败）；父进程与 owner 子进程
  都硬拒 AF_INET/AF_INET6/DNS/create_connection，AF_UNIX 放行。

## Alternatives considered

- **直接用 `phone.live_day.run_segment`。** 最强理由：它就是 `nyairo_day run-day` 的
  正式日常 runtime，改动最少。否决：它不持有 `BusinessClockAuthority`，业务时钟会
  冻结在起点；要 1:1 推进就得另写时钟线程，等于新总脑，也接不上既有 experiment cap。
- **复制 G1 的 p1/p2 多 phase 与注入器。** 最强理由：已有一条验收过的真实链。否决：
  本单要的是自然日常，不是脚本 Decision/writer；任务也明确不复制多 phase。
- **为「到 20 就停」改 `AutonomousLifeCycle` 或 `FastDaySupervisor._loop`。** 最强理由：
  停止语义最直接。否决：前者碰 0.7 状态机，后者改变旧 fastday profile 的行为；只用
  既有 control stop 位在薄入口里收口，旧 profile 一个字节不变。
- **把这次失败写成日常永久 failstop/整实例锁死。** 最强理由：防止误重跑最简单。否决：
  任务限定首故障只属于本次有界实验；一次性由独立消费标记保证，`bounded_failstop.json`
  仍只在本 state root 内生效。
- **audit 复用 `fast._status_payload`。** 最强理由：现成、字段全。否决：它会调用
  `ExperimentRequestCap.view()` 推高水位并建账本锁，违反「纯读」。

## Consequences

- 收益：唯一真实 run 有了封闭、一次性、可审计的入口；prepare 零模型，run 首次
  provider/内容/runtime 故障即停，预算 20/history 557/UTC 300/total 1000 在发送边界
  生效，失败预留不退。
- 代价与边界：业务时钟沿用既有 `COMPRESSED_DEMO` 文件外壳，但 `rate=1.0`、逻辑秒
  等于真实秒，`audit.clock.no_compression=true` 明确报告不压缩；到 20 次时通过既有
  control stop 收口，停止原因会显示为 operator-stop 类而不是故障。真实模型是否自然
  出门/阅读/购买仍由唯一 run 决定，离线只证明装配、边界与安全投影。
- 未验证：没有 prepare/run 正式根，没有 source 密钥；没有跑旧 24 例、旧实验套件、
  notes 树、SHA、浏览器或真实 provider probe；产品新增调用 0。相关既有决定仍有效：
  [[2026-11-08-daily-life-continuity]]、[[2026-11-07-g1-integrated-i]]、
  [[2026-11-06-idle2-bounded-observation]]。
- 后续（2026-11-08 真跑后修正，不改写上面的判断）：唯一真实短日 A 的 PLACE 拒绝与
  “动作拒绝不触发首故障停止”已修；offline-check 的正式根断言改为“本次调用没有改动
  既有正式根”（真跑后根必然存在）。见
  [[2026-11-08-v07-place-surface-and-refused-step]]。
