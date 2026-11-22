# v0.8 Wave 4：第 46 篇 §五 8 类故障矩阵 & §六 七天连续生活验收

## Problem

Wave 1–3 已把院子晾晒、盆栽、手作、街角借还、途中推进、公共告示、地点小册、书店展陈
接进真实 owner 与 Resident 日常。但第 46 篇要求的不是“单条 happy path”，而是：

1. §五 的 8 类故障：移动中携带物、响应丢失重放、同一支撑位竞争、半干遇雨、
   停机重启、频繁刷新、权限收回、后果重复投递；
2. §六 的同一隔离实例上第 1–7 天连续生活，含真实 `AutonomousLifeCycle` +
   `AutonomousLifeResident.tick()`，且全程零产品大模型调用。

此前的测试各覆盖一段（Wave 2 的院子、Wave 3 的街道），没有把 8 类故障逐项钉死，
也没有跨七天的连续实例验收。

## Decision

新增 `tests/test_v08_ch46_seven_day_verification.py`，只读驱动真实 owner：

1. `TestCh46EightFaultScenarios` 用 8 条测试精确对齐 §五 的 8 个场景，断言基于
   `v04_neighborhood_living` 的真实返回结构与错误码（`ROADBLOCK_ENCOUNTERED`、
   `OPERATION_KEY_CONFLICT`、`already_applied`、`draft_withheld` 等）。
2. `TestCh46SevenDayContinuousLife` 在同一临时实例根目录上连续跑 Day 1–7：
   Day 1–4 共用一个 Epoch（真实 cycle/runner/resident，脚本化 0-LLM cognition），
   Day 5 真的 `stop()` + `runtime.close()` 后从磁盘重建 Epoch B，
   Day 6–7 继续用 B；每天都落到真实 v0.4 owner 的正式入口。
3. 如实记录两条既有边界：
   - **同线索 operation_key 去重**：Resident 自带 key 是 `resident_v04_<raw_ref>`，
     同一线索跨天的第二次物理动作会成 `already_applied`；Day 2 的重挂改走带独立
     `operation_key` 的正式 `settle_*_round_consequences`。
   - **支撑位无物理容量**：`CLOTH_SUPPORTS` 没有容量字段，故障 3 不伪造
     `SUPPORT_AT_CAPACITY`；真实的“第二笔被拒”是同一 key 不同意图的 `409`。

## Alternatives considered

**A. 只直接调 `apply_v04_neighborhood_command` 写死 8 个断言**
最省时间、最不容易受真实夹具影响。否决原因：这样绕开了 phone view 的字段/视角闸门与
bridge 的后果账本，也绕开 §六 明确要求的真实 Cycle/Resident；故障 2、7、8 的“谁在什么
层拒绝/重放”就无从证明。

**B. 为故障 3 临时给 `porch_hook` 造一个容量语义并断言 `SUPPORT_AT_CAPACITY`**
这是最贴合题面“容量冲突”的写法。否决原因：引擎没有任何容量字段，测试若自造 409 会把
不存在的契约写成事实，后续会话会按假契约施工；因此只验证真实存在的两层语义（多件共存 +
key 级冲突），并在文档写明未覆盖的物理容量语义。

**C. 让七天全部用 Resident 自带 key 跑，接受第二天的重挂被去重吃掉**
最简单，不用额外代码。否决原因：Day 2 的物理事实会变成“wet 之后还挂在 home_rack”，
与“再次挂出后转雨抢收”的观察不符；测试会变成假绿。采用带独立 operation_key 的正式
owner 入口，既保留真实结算，又不改动 Wave 3 的按线索去重。

**D. 七天测试继承 `CycleTest` 复用 `_runtime`**
能少写 25 行。否决原因：会连带继承 `CycleTest` 自带的 6 条世界/作品测试，同一模块重复跑，
且与本验证无关；改为继承 `OneDayTestCase` 并原样复制 `_runtime` 装配。

## Consequences

- 新增 9 条测试（8 故障 + 1 七天）；验收命令 62 条全绿（约 118 秒，WSL）。
- 七天实例收尾：`revision=25`、真实 owner 回执 7 条、布巾 1 条、纸张 `8+0+0+0==8`、
  书店 3 本 / 共享架 2 件不变、产品大模型调用 0 次。
- 已知未覆盖：真实大模型 24h 在线长跑、桌面端 UI 联动、支撑位物理容量语义、
  中段暂停时投影位置回退 `origin_place_id` 的既有行为。
- 没有改任何 `components/` 业务代码；测试只读消费既有接口。
