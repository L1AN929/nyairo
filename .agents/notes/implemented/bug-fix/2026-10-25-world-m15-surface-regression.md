# Agent Note: M15 测试夹具缺少裸 surface 声明

Status: implemented

## Problem

`world.service.test_object_action_m15` 的 `PlaceBattery` / `SequenceBattery` 有三处红：`test_09_held_object_to_valid_surface_succeeds`、`test_15_replay_creates_no_duplicate_placement`、`test_sequence_pickup_restart_place_restart`，全部返回 `UNKNOWN_TARGET`，legacy 原因是 `surface table is not declared by the world`。

## Decision

`world_body_substrate.surface_declared()` 当前规则是：表面 id 去掉 `:instance` 后缀得到 kind；裸 id（没有实例后缀）只有声明里 `legacy_bare` 为真时才算已声明；这不表示任意实例表面都合法，仍须满足原有种类、归属等校验。测试夹具在 `declare_fixtures()` 里往 `wbs.SURFACES`（与 `SURFACE_KINDS` 是同一个 dict）塞入裸 `table`，但没有 `legacy_bare`，因此按现行规则确实未声明。

生产规则本身是对的，问题在夹具不自洽。修复只给夹具声明补 `"legacy_bare": True`，让裸 `table` 在测试进程内成为合法表面；放置成功、幂等重放、两进程重启的原断言一字未改，生产 `SURFACE_KINDS`、resolver 校验、owner 合同与 v1/v2 事件语义均未动。

## Alternatives considered

- 放宽 `surface_declared()`，让任意非 `legacy_bare` 裸 id 也通过：这会撤销"两个书桌不能共享一个全局表面"的来源约束，属于放宽生产合同换测试绿，否决。
- 把夹具改成实例表面 `table:<id>` 并同步所有断言：语义更接近生产，但会在本次时限内改动放置/幂等/重启三组断言以外的夹具结构，风险大于收益，否决。

## Consequences

M15 电池 20 例恢复全绿，`world/service` 全套 263 例 OK。该修复只保证夹具与其声明规则一致，不证明真实世界中存在 `table` 表面，也不改变任何生产可见行为。
