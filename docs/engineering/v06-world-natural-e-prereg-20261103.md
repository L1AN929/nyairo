# v0.6 E 预登记：回顾反馈供给修后的一次新自然观察（2026-11-03）

本文件是 E 批的封闭预登记。范围来自本人本窗口的直接授权：至 2026-11-04 01:00 CST
内按总纲由父级和指定监护自行决定操作/必要真实模型消耗。E 是**新的、独立的**一次观察，
不重开 D，不改默认配置、旧根、Memory 权威或发行物。

## 身份与参数（封闭值）

| 项 | 值 |
| --- | --- |
| key | `v06-natural-e` |
| root | `/tmp/nyairo-v06-natural-20261103-e` |
| authority | `/tmp/nyairo-v06-natural-20261103-e-memory-authority/authority.db` |
| run | `run:v06-world-natural-20261103-e` |
| domain | `v06:world-natural:20261103-e` |
| history | 232（211 确认 + 相机未知 1 + D 本批 20） |
| 新增共享 cap | 20（decision / content / 失败预留都算，不退） |
| 跨批累计记账 | 232 + 20 = 252，最坏仍在本人在批准范围内的 1000 以内；单根 UTC 日 300 保险丝不动 |
| 真实窗口 | 1800 秒；检查 1 分钟；tick 5 秒 |
| 上限 | max_ticks 900；trace 4000 行 |
| 最晚启动 | 2026-11-04 00:15 CST |
| 绝对截止 | 2026-11-04 00:50 CST |
| 收尾冗余 | 窗口后最多 60 秒 |

窗口在 `started + 1800` 与绝对截止中取较早者；过线不缩短窗口、不移动日期、不补第二次 run。
准备好即可运行，不必等夜间；失败不退、不续、不跑第二 run。

## 本批要看什么、不看什么

回顾反馈供给的边界修复已由父级 `bm3dfacgb` 通过（30 checks / 11 unit / 0 skip）：自动回顾
种子不再取「owner 已标记为回顾的作品的确证写作结果」，普通经历与手动回顾不受影响。E 只用
**修后代码**正常跑一次新实例，观察实际机会/选择/确证结果是否正常留存、是否正常收尾。

- 判据只用**结构证据**：实际候选、正式 Decision、owner 版本/回执、AR-0/AR-1、M0 记录与
  批次收尾。**不读 prompt/正文/理由/标题**，不按「回顾：」关键词判类型，不新增扫描仪器。
- **遇到 review 结果**才可以说自动种子过滤被真实覆盖；**没遇到就是 `NOT_COVERED`**。
  不保证本次自然选择一定出现回顾，也不为出现回顾注入材料或提示。
- 不把离线自检当自然过滤覆盖，也不把 Memory 里没有嵌套标题当成通过证据（文本不是结构证据）。

## 与 D 相同的接线（不新增）

E 只在自己的 phase / runtime / World 子进程环境里显式设置：

- `WORLD_BODY_INTENT_MUTATION_GATE=off`（自然观察；真实世界动作走既有 gate-off 预提交拒绝链）
- `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=1`（v0.6 D/E 才设；旧批不设）

父 shell、实例默认、`.env`、旧根都不改。ambient 仍是背景：不新增候选、不授权动作、不强制
MOVE、不制造事件，也不会自动变成个人任务。本地时刻没有 ambient 事件时按真实 0 报告。

## 安全与隔离

- 新根正常 `prepare` 现有 world/package，不复制旧 D/C 状态，不读旧根；不平移日期、不造内容。
- 所有 World 写面 / world_home / registry / lifecycle、HOME / Memory / Supply / 预算 / socket
  都在新根或独立 authority 内；symlink、根外、错 phase 映射在写入前拒绝。
- 继承 D/B/C 的坏账 fail-closed、父进程与两个 owner 子进程的 AF_INET/AF_INET6/DNS/
  `create_connection` 硬拒（AF_UNIX 保留）、正式授权链、正规 close/shutdown。
- gate off 的正式 START 必须走既有预提交拒绝：`STEP_REFUSED`、Action `PREPARED`、
  attempt `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执、无 APPLIED、票不消费、世界位置与
  Body 姿态不变、`pending_unknown` 0。
- 没有 CONTROL / operator 材料 / 注入器 / 强制暂停重启 / UNKNOWN·CAS 注入 / 清静默 /
  TTL 跳时 / 账本 release。
- 缺真实 endpoint 在写前拒绝；不提供默认 `.invalid` 占位，不读/打印密钥。

## 验收与边界

- 唯一固定离线入口：`scripts/v06_world_natural_e.py offline-check`。它只验 E 自己的身份/
  预算/时间/隔离与一次正常短 natural phase；不重跑 D 的 55 项或供给边界 30 项。
- 真实 prepare/run 只由父级唯一执行；worker 阶段真实 E root/authority 始终 ABSENT。
- 本批不证明真实模型的回顾选择、长期行为、G1、G2、V-C 或 24h；没观察到的项如实记未覆盖。
