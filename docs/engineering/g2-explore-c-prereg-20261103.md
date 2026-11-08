# G2 explore-c 预登记（2026-11-03）

状态：接线已实现并离线通过；真实 prepare/run **未执行**，由父级另批。本批只做一次
EXPLORATORY_ONLY 的影子选择对照，不承诺统计通过。

## 身份与根

| 项 | 值 |
| --- | --- |
| root | `/tmp/nyairo-g2-explore-20261103-c` |
| main | `root/main` |
| authority | `root/memory-authority` |
| branches | `root/branches`（本轮不建分支，无承接） |
| run | `run:g2-explore-20261103-c` |
| domain | `g2:explore:20261103-c` |
| 历史 | 234 / 累计 1000 / 单根 UTC 300 |
| 新增 cap | 30 = 采集 8 + 影子 20（10 对）+ 保留 2（不用） |

旧 a/b 身份、旧根、D/E/F 实例一律不动；不恢复 F。

## 窗口

- 总窗 7200 真秒（采集最多 5400），自然观察 60 秒/次，`check_interval` 保持 30 分钟。
- 最晚启动 2026-11-03 19:30 CST；绝对 21:45 CST 停新请求；60 秒正规收尾。
- 过最晚线不启动，不滚动延时、不缩窗。
- ambient 显式 0、gate off，只在新实验进程/子环境；父 env 与默认不动。

## H 与对照

- H = 自然 Decision 选中作品 → 真实 writer 成功 → owner 新版本 → 既有准入 M0 精确
  artifact/version 可读；不得脚本建 H、复制旧经历、手写 Memory、强制 PAUSE 或造 OPEN。
- 冻结时同一场景必须同时有：H 可执行、另一件不同作品可执行、NO_ACTION 合法。
- 两臂只改 `prior_experiences[*].text`（中性等字符占位），refs/候选/合法集/观察时刻/
  背景/参数相同；正式 `build_prompt` 发送边界核单变量，历史被裁剪或第二变量出现即前拒。
- 10 对沿用既有 `PAIR_ORDER` 前 10 个固定顺序；只报配对选择计数、比例差与
  `EXPLORATORY_ONLY`；不计算 McNemar、不套 20 对成功判据，不宣布 G2 通过。

## 不做的事

本轮不做两臂执行承接（最低 4 请求与 cap 不相容），停在影子选择边界；不拿保留的 2 个
额度去凑承接。不改 Memory/AG2/AR1/owner 状态机/默认 gate/安全策略/旧实例/私文读面。

## 失败出口

- 未产生 owner 新版本或精确 M0 H：`NO_EFFECTIVE_HISTORY`，比较 NOT_RUN。
- H 有了但同轮双合法不成立：`SCENARIO_NOT_IDENTIFIABLE`，不放宽规则。
- 传输/坏账/隔离/截止失败：`BATCH_ABORTED` 或明确未完成，已用不退、不补槽、不二 run。
- 影子未满 10 对：`NOT_TESTED_INCOMPLETE`，不补样。
- 完整 10 对：`EXPLORATORY_ONLY`（计数 + 比例差），不是历史效应结论。

## 离线已验 / 未验

已验（`scripts/g2_explore_c.py offline-check`，exit 0）：15 例本功能测试、维护读面字段、
World revision（只有真实前后值才比较，否则 `UNKNOWN`）与另名签名三态、精确 H、两臂单变量、
10 对 20 请求、20→21 与 30→31 前拒、真实 Supply/M0/runtime 短链上的结构门与影子机械计数；
observer on/off 两条短链控制流轨迹等价；父进程与 owner 子进程都实际拒
AF_INET/AF_INET6/DNS/`create_connection`，AF_UNIX 可用。

未验：真实模型自然 H、真实同轮双合法、真实发送边界的历史 text 送达、10 对的选择差异。
离线脚本 Decision/内容是替身，不能写成模型采用或因果；worker 自验不代替父级验收。
