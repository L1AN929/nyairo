# Agent Note: G2 A 唯一真实批的封闭启动适配

Status: implemented

## Problem

本人已直接批准 `docs/engineering/g2-natural-live-proposal-20261031.md` 的范围，但现成仪器仍
**不是现成可跑**：`g2_next_offline.py` 的真实入口一律拒绝；`history_choice_natural_v2.py`
有自然 controller/只读观察器/H 检测/冻结与结构门，却没有统一持久身份、52 账本、40 影子、
承接与发送锁；`history_choice_experiment.py` 有影子/统计/承接和 `clone_branch`，但
`runtime_env` 把分支身份硬绑在 v1 `RUN_ID`，`clone_branch` 也把共享 authority 写死成同层
`*-memory-authority`。新批的 authority 在 `<root>/run/memory-authority`，旧接缝无法直接使用。
直接复制第三套循环、直接改旧默认或直接跑正式根都不被允许。

## Decision

新增 `scripts/g2_next_live.py`、`tests/test_g2_live_a.py` 与预登记
`docs/engineering/g2-natural-live-prereg-20261031.md`。它是薄封闭入口，不复制控制器：

1. **固定新身份**：`run:g2-natural-20261031-a` / `g2:natural:20261031-a`，
   root `/root/nyairo-g2-natural-20261031-a`，main `<root>/run/main`，
   authority `<root>/run/memory-authority/authority.db`，branches `<root>/run/branches`，
   history 185 / 总 300 / 新 cap 52。
2. **prepare/audit/run**：prepare 只在全新根建实例/两件中性作品/Memory/52 窗口；audit 纯读
   （直接读账本 JSON，不取 `LifeRequestBudget.view()` 的锁，因此不建目录/库）；run 只在
   `--execute-approved` 下执行，首次写 `g2_window.json` + `started_at`，之后第二次/重启/重放拒绝。
3. **一本 52 账本**：`LiveStageLedger` 记录阶段起点并在每次发送前按同一本
   `LifeRequestBudget` 已用数判 8/40/4 与总量 52；`reserving=0` 的发送前检查按 1 格算，
   第 9/41/53 次在 `reserve` 前拒。每臂沿用 `arm_ceiling` 的 2 格硬门。
4. **自然采集**：直接复用 `NaturalObservationController` / `ReadOnlyObserver` /
   `_advance_from_runner` / `freeze_and_check`；只把 run 身份、窗口、deadline 与真实/夹具
   transport 接进来。H 用新的 `ExactHistoryDetector`：owner 版本与正式 M0 经历版本归一后
   必须精确相等，不相等不算 H。
5. **影子与承接**：影子直接复用 `run_shadow` 的 20 对固定顺序、中性替换和发送前单变量门；
   承接在 A 任何写之前用 `clone_branch` 复制 B，两臂共享同一本预算。影子选择到冻结
   artifact/Activity 精确映射；CONTINUE 要求前台就是冻结 Activity，RESUME 要求 PAUSED +
   OPEN 资格；错目标/NO_ACTION 一律 `NOT_RUN`，不 fallback。真实承接是新的真实 Decision，
   与影子选择不一致记 `MISMATCH_NOT_ACCEPTED`，不强制模型抄答案。若 `clone_branch`/隔离
   合同没建立，承接两臂记 `NOT_RUN`（`R_BRANCH_ISOLATION`），影子统计独立成立，不新增
   生产合同求绿。
6. **持久停批**：`PersistentSendLatch` 在第一次停止时写 `g2_stop.json`；采集的 runner
   detail、影子的 failure_class、承接的 cycle detail 三处都识别
   `MODEL_CONNECTION` 等 transport marker，任一阶段首次失败即整批 `BATCH_ABORTED`，后续
   阶段不再预留/发送。
7. **隔离**：`live_env`/`branch_env` 显式 gate off，并把 World 的 7 个写面、Memory DB/controls
   钉在实例根内、authority 钉在本批共享路径；任何缺/相对/根外/符号链接逃逸在起服务与写窗口
   之前拒绝。夹具路径只接 tempdir 新 scratch，正式路径拒绝夹具替身，夹具路径硬断 HTTP。

只对旧 harness 加两个**可选**接缝：`runtime_env(..., expected_run_id=None)` 与
`clone_branch(..., expected_run_id=None, shared_authority=None)`；默认值保持 v1 原行为。
`components/`、旧 `tests/`、旧批 root/run、默认 gate、配置与 git 均未改。

## Alternatives considered

- **直接扩 `g2_next_offline.py` 的 `--offline-check`**：最省文件，但那个驱动明确没有真实
  prepare/run，且其承接检查用合成起点；继续堆会让「离线准备」与「真实批入口」共用同一命令，
  容易误跑，否决。
- **复制一份 v2 controller / run_shadow**：能绕开旧接缝，但会产生第三套预算、统计与影子逻辑，
  违背「复用既有仪器」，否决。
- **patch `history_choice_experiment.RUN_ID` 模块全局**：能在不改签名的情况下让分支通过身份门，
  但会污染同进程里的旧 harness 行为，且在真实分支写旧 run，否决。
- **把 authority 改成旧布局 `<main 同层>-memory-authority`**：能直接复用 `clone_branch`，
  但违反本批「authority 在 run 内」的隔离要求，等于临场换布局，否决。
- **承接遇 RESUME 就改写成 CONTINUE**：影子已验 CONTINUE 不能冒 RESUME；本次实现正式的
  RESUME 映射（PAUSED + OPEN 资格 + 同 Activity），并由夹具正控实跑，否决改写。
- **用虚拟时间/清 quiet 让承接发得出去**：真实路径会变成快进或造资格；只在夹具里用
  `observed_at` 推到既有 TTL 之后（`_target_observed_at`），真实路径用墙钟且不追赶，否决
  把夹具时间带进 real。

## Consequences

- 收益：自然采集→冻结→40 影子→隔离双分支承接被一个固定身份、一本 52 账本、一把持久发送锁
  串起来；prepare/audit 无产品调用、audit 纯读；三种阶段失败都能持久停批并在第二次 run 前拒；
  真实子进程验证 gate off、写面在根内、仓库默认写面零新增、父环境不变。
- 代价/残留风险：夹具正控用脚本 Decision/内容与虚拟时钟，统计结论不能当真实模型结果；
  RESUME 承接虽已离线走通，真实模型是否选 RESUME、是否同目标仍需真实批验证。
- 未验证：真实 provider 下的失败分类与抖动、8 次采集能否自然建起 H、1800 秒内能否形成
  双合法场景、真实影子 20 对的功效。离线绿不等于 G2 通过，G1 仍 NOT_COVERED。
- 固定离线验收：`tests.test_g2_live_a`（36 例，约 100 秒，0 产品请求、0 正式根）。
  相关：`2026-10-31-g2-next-offline`、`2026-10-31-g2-natural-dual-choice-probe`、
  `2026-10-29-history-choice-harness`、`docs/engineering/g2-natural-live-prereg-20261031.md`。

## 追加：b 批 profile（2026-10-31）

同一个 `g2_next_live.py` 里新增第二份封闭 profile，`--batch b` 显式选择；a 仍是缺省，
a 的常量、截止、started 锁与账本语义不变。

- **身份**：root `/root/nyairo-g2-natural-20261031-b`，`run:g2-natural-20261031-b` /
  `g2:natural:20261031-b`，main/authority/branches 仍在 `<root>/run/` 内。
- **参数**：history 186、cap 52（8+40+4）、采集 9000 真秒（2.5 小时）、观察 60 秒、
  纯读上限 151、整批 11400、绝对截止 `2026-11-01T02:00:00+08:00`、启动线
  `2026-10-31T22:30:00+08:00`。（本人 2026-10-31 21:54 直接批准；旧时段 10800/181/14400/
  截止 00:38:24/启动线 21:08:24 已过期且从未真实运行，历史保留。）
- **启动线**：`_run_impl` 在 `--execute-approved` 之后、读 prereg 之前判 `latest_start`；
  到点及之后一律 `LATEST_START_PASSED`，不写 window/started、不发请求。a 的 `latest_start`
  为 `None`，原路径不变。audit 不受启动线影响，正常收尾也不受影响。
- **贯穿**：`_resolve_profile`/`prepare`/`audit`/`run`/`phase_probe`/clone 都按 profile 走，
  不 patch 全局常量；`latest_start` 写进 prereg、caps 与 audit 输出（白名单新增该安全时间键）。
  prereg 身份核对要求 b 的 `latest_start` 与 profile 一致。
- **夹具**：a/b 两个正式根都拒绝夹具运行；夹具只接 tempdir 新 scratch，测试用机械注入
  `latest_start` 的前后时刻验证正负控，不改系统时钟。
- **边界**：追写时（旧时段）启动线已过且 b 从未真实运行；新时段 22:30 启动线由本人直接批准后，
  真实入口仍须父级现场核 fresh 再首次 prepare/audit/run。离线绿不等于 G2 通过，
  真实 provider、自然 H、双合法场景与影子功效仍未验证。
- 固定离线验收：`tests.test_g2_live_b`（18 例，约 23 秒，0 产品请求、不碰任何正式根）。
