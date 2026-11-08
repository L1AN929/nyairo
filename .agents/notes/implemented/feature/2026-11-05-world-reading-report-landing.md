# Agent Note: 批注的结果报告终于落到日志上

Status: implemented

## Problem

2026-11-05 的 Y 批是这个项目第一次在真实模型、真实世界里走完"出门 → 进书店 → 选 `READ_HERE`
→ 读满 → 写批注"。批注**确实写成功了**：supply 侧 `doc_revision` 到 3，里面有新的取得
`acq:9eb5e51c1b54c905`、停点 `pos:e28ca11ee4fbe722`、批注 `memo:17f2b040c6854e76`，
`source_id=world:bookstore`，回执 `read:world:bookstore:5e16a8cedb9e0fc2`。

但从日志上看不见。`_annotate_world_reading` 把结果挂进 `base["world_reading"]`，`_finish` 也原样
返回了，可 `autonomous_life_runner._extra_for()` 落盘时只搬一份固定白名单里的键，这个字段不在里面。
结果就是**功能成功和功能静默失败，在日志上长得一模一样**——当时从 journal 里搜不到任何
`world_reading`，也搜不到 `NO_READING_HERE` / `NOT_CONFIGURED` / `NO_PRIVATE_ITEMS` 这些失败形状，
一度误判成"整条链没触发"。

## Decision

`_extra_for()` 末尾加九行：只有 `out.get("world_reading")` 是非空 Mapping 时才把它的副本搬进落盘记录，
字段原样带出（`state` / `place` / `descriptor` / `item_type` / `version_number` / `reason` /
`version_created`）。没有这一项的轮次，记录形状与改之前逐字节相同，不多一个 `null`、不多一个空对象。

不新增 schema、`WakeRecord` 未动、未加校验。同样没落盘的 `stay` / `place_availability` /
`checkpoint_update_ref` 明确没碰——那是另一次判断，不在本单。

## Alternatives considered

- **给落盘记录加一个兜底：把 base 里所有没命中的键都搬过去。** 最强立论：一劳永逸，以后新增字段
  不会再静默丢失。否决原因：落盘记录是证据，不是调试转储；无差别搬运会把内部中间量漏进证据面，
  以后每加一个内部字段都要复核它该不该被看见。宁可按名字逐个放行。
- **只写一行摘要字符串（"读了书店的书，成功"）。** 最强立论：够人看懂，改动最小。否决原因：
  失败时要能区分"这地方没文字""没配 writer""没有私人文档""owner 拒绝""写入未知"五种情形，
  压成一句话就再也分不开了，而这正是要修的病。

## Consequences

- 成功与五种失败形状，现在都能在 `autonomous_wakes.jsonl` / `resident_journal.jsonl` 里看出来。
- 原文与批注正文一律不进日志，只有形状和版本号。
- Z 批真实运行验证：`world_reading` 出现一条
  `{"descriptor":"READ_HERE","item_type":"memo","place":"bookstore","reason":null,"state":"APPLIED","version_created":true,"version_number":3}`。
- 父级复跑：`test_world_reading_annotation` 7 条 OK，`test_lived_day_core_v04` +
  `test_day_life_quiet_runner` 31 条 OK。
