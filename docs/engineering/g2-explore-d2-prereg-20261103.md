# G2 explore-d2 预登记（2026-11-03）

状态：接线已实现，父级唯一 `offline-check` 实际 exit 0；真实 prepare/run **NOT_RUN**。交付时已过固定 19:00 最晚启动线，不延期、不缩窗或自动次日运行。本批是 C 的故障后继
（C 在 2026-11-03 17:02 以 `BATCH_ABORTED / PROVIDER_OR_TRANSPORT_FAILURE` 停批，失败不退），
**不是补跑**，只做一次 `EXPLORATORY_ONLY` 的影子选择对照，不承诺统计通过。

## 身份与根

| 项 | 值 |
| --- | --- |
| root | `/tmp/nyairo-g2-explore-20261103-d2` |
| main | `root/main` |
| authority | `root/memory-authority` |
| branches | `root/branches`（本轮不建分支，无承接） |
| run | `run:g2-explore-20261103-d2` |
| domain | `g2:explore:20261103-d2` |
| 历史 | 239 / 跨批累计 1000 / 单根 UTC 300 |
| 新增 cap | 30 = 采集 8 + 影子 20（10 对）+ 保留 2（不用） |

跨批累计 1000 只做账目区分；单根每 UTC 日 300 的保险丝仍由 `LifeRequestBudget` 执行，
不改全局常量。旧 a/b/C 身份、旧根与 D/E/F 实例一律不动。

## 窗口与节拍

- 总窗 7200 真秒（采集最多 5400），自然观察 60 秒/次，最多 91 次。
- **检查节拍 10 分钟**：`CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=10` 必须同时写进
  **实例持久配置**（prepare 时更新嵌套 Life 实例）与 **实际 runtime env**（`live_env`），
  audit 会同时报告配置值与持久值；夹具环境仍为 0。C/a/b 保持既有 30 分钟不变。
  这只增加 90 分钟采集窗内的定时检查机会，**不证明网关抗抖动**，也不改变「首次
  provider/timeout 即持久停锁」；机会数不是保证请求数，8 不是 8 次决策目标。
- 最晚启动 2026-11-03 19:00 CST；绝对 21:00 CST 停新请求；60 秒正规收尾，21:30 前收口。
  过最晚线不启动，不滚动延时、不缩窗。
- ambient 显式 0、gate off，只在新实验进程/子环境；父 env 与默认不动。

## H 与对照

- H = 自然 Decision 选中作品 → 真实 writer 成功 → owner 新版本 → 既有准入 M0 精确
  artifact/version 可读；不得脚本建 H、复制旧经历、手写 Memory、强制 PAUSE 或造 OPEN。
- 冻结时同一场景必须同时有：H 可执行、另一件不同作品可执行、NO_ACTION 合法。
- 两臂只改 `prior_experiences[*].text`（等长中性占位），refs/候选/合法集/观察时刻/背景/
  参数相同；正式 `build_prompt` 发送边界核单变量，历史被裁剪或第二变量出现即前拒。
- 10 对沿用既有 `PAIR_ORDER` 前 10 个固定顺序；只报配对选择计数、比例差与
  `EXPLORATORY_ONLY`；不计算 McNemar、不套 20 对成功判据，不宣布 G2 通过。

## 不做的事

不做两臂执行承接（最低 4 请求与 cap 不相容），停在影子选择边界；不拿保留的 2 个额度
去凑承接。不改 Memory/AG2/AR1/owner 状态机/默认 gate/安全策略/旧实例/私文读面。

## 失败出口

- 未产生 owner 新版本或精确 M0 H：`NO_EFFECTIVE_HISTORY`，比较 NOT_RUN。
- H 有了但同轮双合法不成立：`SCENARIO_NOT_IDENTIFIABLE`，不放宽规则。
- 传输/坏账/隔离/截止失败：`BATCH_ABORTED` 或明确未完成，已用不退、不补槽、不二 run；
  今晚不再重试。
- 影子未满 10 对：`NOT_TESTED_INCOMPLETE`，不补样。
- 完整 10 对：`EXPLORATORY_ONLY`（计数 + 比例差），不是历史效应结论。

## 离线已验 / 未验

已验（`scripts/g2_explore_d2.py offline-check`）：本功能单元、一条共享短正式链（真实
Supply/M0/runtime + tick 维护 trace + 10 对影子 + 预算 20→21/30→31 前拒）、父进程与
owner 子进程的四类出站硬拒与 AF_UNIX 可用。

未验：真实模型自然 H、真实同轮双合法、真实发送边界的历史 text 送达、10 对的选择差异。
离线脚本 Decision/内容是替身，不能写成模型采用或因果；worker 自验不代替父级验收。
