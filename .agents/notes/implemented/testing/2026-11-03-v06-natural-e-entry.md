# Agent Note: v0.6 E 自然观察薄入口（复用 D 的安全投影）

Status: implemented

## Problem

回顾反馈供给的边界修复已经父级通过（30 checks / 11 unit），但那条验收只证明**离线状态下的
供给边界**：自动回顾种子不再取 owner 已标记为回顾的作品的确证写作结果，普通经历仍出种子。
要回答「修后代码在真实 run 里机会/选择/确证结果是否正常留存和收尾」，需要一次带真实窗口、
预算和自然 phase 的新封闭身份，同时不重开 D、不改默认/旧根/Memory 规则。

D 的入口已经接全了 ambient 开关、off gate、坏账 fail-closed、父/phase/两 owner 的离线段
网络硬拒、路径隔离、1800 秒窗口/900 tick/4000 trace、60 秒收尾；E 只需要换一个封闭身份，
并避免把 D 的整套离线矩阵复制一遍。

## Decision

1. 新增 `g1_real_adoption.V06_E_PROFILE`（`v06-natural-e`）：root
   `/tmp/nyairo-v06-natural-20261103-e`、独立 authority、`run:v06-world-natural-20261103-e`、
   `v06:world-natural:20261103-e`、history 232、1800 秒窗口、900 tick、4000 trace、
   最晚启动 2026-11-04 00:15 CST、绝对截止 00:50 CST。D 的 key/history 212 原样保留；
   `BATCH_CHOICES`/`CLI_BATCH_CHOICES`/`NATURAL_PROFILES` 一个值都不扩，E 只经
   `scripts/v06_world_natural_e.py` 的 `_use_batch` 进入。
2. 把原本逐个 `key == V06_D_PROFILE.key` 的行为判定收敛成 `V06_NATURAL_PROFILES = {D, E}`
   与 `_is_v06_natural()`：ambient 开关、off gate、坏账 fail-closed、累计 1000 局部记账、
   60 秒收尾、离线 owner 守卫、严格 world_home、phase 的 world_before/after 基线等全部按
   这个集合生效。旧的 a–f/B/C 判定一条未改。
3. 新入口 `scripts/v06_world_natural_e.py` 只做 prepare/audit/run/offline-check 的薄包装，
   直接复用 D 已验收的 `_safe_result`/`_safe_natural`/`_artifact_view`/`_gate_off_refusal`/
   `_endpoint_refusal`，不再复制 D 的 55 项离线矩阵。E 的 offline-check 只验：E 的封闭身份、
   D 未被覆盖、旧批不变、20 请求共享 cap、坏账/到期/发送前拒绝、232+20≤1000 且累计门在
   第 768 次触发、一次正常短 natural phase（真实 Supply/World/runtime，脚本 Decision/内容）、
   第二 run 拒绝、audit 只读身份/窗口、仓库默认写面零新增、真实 root/authority 始终 ABSENT。
4. E 不新增 prompt/正文/关键词捕获：D 的 client 边界捕获仍只在 D 离线开启；E 只看
   CandidateTrace/AG-1/AG-2/AR-0/M0/Supply 已有结构。遇到 review 结果才谈自动种子过滤被
   覆盖，没有即 `NOT_COVERED`。

实测（2026-11-03，固定入口 `offline-check`）：46 项检查全 PASS，15.283 秒，新 scratch
`/tmp/v06-natural-e-offline-z6tl90pp`；新单测 10 例 0 skip。正式 phase 中 World READY/
gate off/child_env_ambient=1，两 owner 网络自检四拒一允，脚本 START 被正式拒绝
（`STEP_REFUSED`、Action `PREPARED`、attempt `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执、
世界位置与 Body 姿态不变），runtime/两 owner 正常关闭，仓库默认 lifecycle 零新增。

## Alternatives considered

- **复制 D 的 1000 行入口、只换身份**：最省心，行为最像 D。否决原因：两个文件各自维护同一套
  矩阵，后续任一侧修安全漏洞都可能只修一半；D 的 55 项主要针对 ambient client 边界，E 的
  目的不是再证一次 ambient 透传，照抄只增加维护面。
- **把 E 直接加成 `--batch` 的一个值并扩 `NATURAL_PROFILES`**：能让旧 CLI 直接跑。否决原因：
  旧枚举断言和旧批路由钉着 `("a","b","c","d")`/`{"d","e"}`；E 与 B/C/D 一样应是只由薄入口
  进入的封闭身份，扩全局元组会让其它入口也能选到它。
- **把 D 的 profile 改日期/环境复用**：零新 profile。否决原因：那会覆盖 D 已结束批次的
  封闭身份与 history 212，无法再从代码上区分两次运行，也违反「不把 D 换个日期来跑 E」。
- **按「回顾：」标题或 Memory 文本扫描判断是否覆盖**：能直接回答“有没有回顾”。否决原因：
  文本不是结构证据，标题可被普通作品使用也可被真回顾改名绕过；本单明确不加关键词/正文
  仪器，没遇到结构证据就记 `NOT_COVERED`。

## Consequences

- 收益：E 用一份短入口继承了 D 的全部安全边界，D 的离线矩阵不必重跑；行为判定收敛到
  D/E 集合后，ambient/gate/坏账/收尾等只有一处来源，减少两批漂移。
- 收益：E 的离线检查只覆盖本批新增的封闭值与一次正常短 phase，固定入口 46 项/约 16 秒，
  符合「不为小功能重复矩阵」的验收约定。
- 代价：E 的 offline-check 依赖 D 模块的安全投影 helper；若以后 D 的 helper 改签名，E 需要
  同步。二者同属 v0.6 自然批，这种耦合比复制整份矩阵更可控。
- 边界：离线只证明接线、隔离与一次正常收尾，不证明真实模型的回顾选择、自动种子过滤被
  自然覆盖、G1/G2/V-C 或 24h；真实 E root/authority 在 worker 阶段始终 ABSENT，真实
  prepare/run 由父级唯一执行。

相关：[D 自然观察入口](2026-11-03-v06-natural-d-entry.md)、
[自动回顾候选来源反馈边界](../feature/2026-11-03-review-feedback-supply-boundary.md)。
