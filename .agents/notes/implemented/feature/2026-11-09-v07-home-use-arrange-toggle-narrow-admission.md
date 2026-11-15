# Agent Note: 0.7 回家使用链——ARRANGE / TOGGLE_FIXTURE 窄准入与 owner tick 落地

Status: implemented

## Problem

0.7 的“店买 → 回家 → 真正用上自己买的东西”断在两处：

1. Life 到 World 的唯一正式授权链（AR-0 → `WorldBridgeResolver` → Intent Executor →
   gateway → Resolver）在 `components/world/service/bridge/action_intent.py` 的
   `EXPOSED_VERBS` / `ACTION_ALLOWLIST` 里没有 `ARRANGE` / `TOGGLE_FIXTURE`，所以 Resolver
   明明已实现 `_resolve_arrange` / `_resolve_toggle_fixture`，从真实 Decision 也永远到不了。
   上一轮按“禁止改 allowlist”的旧限制停在这里，未改业务代码。
2. 即使准入打通，人在 `home_goods_store` 时默认移动上限 7 加上“相邻街道 + 花店 + 营业经济店”
   的保留项会把 `home` 挤出候选，`home_goods_store → street_1 → home` 走不回去；买下的灯
   也只在经济 owner 的粗位置 `home/bedroom`，还没进 substrate placement，因此既摆不了也开不了。

2026-11-09 本人直接批准只对 `ARRANGE` / `TOGGLE_FIXTURE` 这两个**既有**动词做窄准入，并明确
可以同步 Life 侧 AR-0 verb 集与 bridge 适配器；其它权限、外部协议、持久 schema、owner、两个
anchor（`autonomous_life_assembly.py` / `life_sharing_service.py`）仍冻结。

## Decision

**准入是窄的，owner 权限语义一个字没动。** `EXPOSED_VERBS` / `ACTION_ALLOWLIST` 增加这两个
动词，`validate_verb_params` 按 PICK_UP/PLACE 的既有风格把它们钉死：`ARRANGE` 必须
`{object_id, target}`，`target` 只允许 `room`（`kind/location_id/area_id`）或 `surface`
（`kind/location_id/surface_id`）的精确键集与有界字段，多一个价格/身份/授权字段都拒绝；
`TOGGLE_FIXTURE` 必须 `{object_id, state}`，`state` 只接受字面 `on`/`off`——没有盲切、不做
大小写折叠、`True` 也被拒。OWNED / 已到货 / relocatable / 本宅 / 当前 area / fixture 声明
仍由 Resolver 与 gateway 自己重新判定，桥只是不把越权参数放过去。准入不等于发授权：AR-0 的
一次性授权、gateway 的 exposure gate、Resolver 的提交都照旧。

**Life 侧只做同合同接线。** `life_daily_action_targets` 把两个动词并入同一 `dailyact:` 命名
空间（`arrange:<room|surface>:...` 与 `toggle_fixture:<on|off>:<object>`），`parse`/`completion`
`/build step` 全部由它拥有，因此 AR-0 与 bridge 的单 target 契约不变；
`world_move_opportunity_source` 只从 gateway `get_available_actions` 自己的 ARRANGE / TOGGLE
候选翻译机会（人在家具所在 place 才出 ARRANGE；TOGGLE 只出与当前记录**相反**的绝对状态，
未知 power 先命令确定的 `off`，绝不把 UNKNOWN 当 off）；`action_reality_ledger` 的 AR-0 verb 集、
`world_action_bridge_adapter` 的 `SUPPORTED_VERBS`（`EXTRA_VERBS`/`DELEGATED_VERBS` 已跟随
`DAILY_ACTION_VERBS`）与 `autonomous_life_cycle` 的 daily-action 分类分支同步。完成判据：ARRANGE
读 owner 自己 `get_available_actions` 里该实例的当前 placement（room 的 area 或 surface），TOGGLE
读该实例记录的绝对 power；同 state 由 Resolver 返回零写 `noop`，不翻转。

**落地复用既有 owner tick，不造第二个 writer。** 买的灯由 `WorldBodyService.tick()` →
`_settle_deliveries` → 既有 `world_phone_shopping.materialize_owned_item` 落到 `home/bedroom`
（同 key 重放 `already_materialized`，不重置已摆放）。移动名额内新增一条：当她不在家、而
owner 的确在 home 给出了 ARRANGE 候选时，把该 home 作为保留项放回 7 条上限内（只决定名额内
留谁，不增加候选总数、不凭空造 MOVE）。新增 ARRANGE/TOGGLE 机会各有小上限；一次观察的
World 机会总数封顶 12（个人/续段候选另有 4 个名额），超出时只裁最大弹性类别的尾部、每个
类别至少留一条，避免再把整轮顶成 `DECISION_FAILED:CANDIDATE_EXPLOSION`。

## Alternatives considered

- **继续不改 allowlist，另找旁路把 ARRANGE/TOGGLE 直接送 Resolver。** 最强理由：不用碰
  “production allowlist”这条敏感线。否决：那等于绕开 Intent Executor 的一次性授权与 gateway
  exposure gate，是真正的扩权/伪整链，也正是上一轮 STOP 的理由。
- **把 `target` 当自由 mapping 透传、只在 Resolver 校验。** 最强理由：改动最小。否决：桥的
  既有风格就是“准入即钉参数”；自由 mapping 会给模型一条可塞身份/价格的通道。
- **TOGGLE 接受 `toggle` 或大小写变体、由 Resolver `lower()` 兜底。** 最强理由：少一次拒绝。
  否决：盲切会破坏幂等重放（重试会把灯翻回去），大小写折叠让“绝对状态”不再绝对。
- **为把灯放进当前区域而在 Life 里只给“当前 area”一个 ARRANGE 目标。** 最强理由：候选更少、
  一定能在正确区域开灯。否决：无谓地隐藏 owner 允许的其它已声明区域；改为给全部合法区域、
  由脚本 Decision 自己选当前区域，再用总数封顶保证不爆炸。
- **把 ARRANGE/TOGGLE 当 Life 内部步骤绕过 World bridge。** 最强理由：像购买那样简单。
  否决：它们本来就是既有 World 动词与 Resolver 提交，绕开就等于不复用既有授权/回执/对账。

## Consequences

- 真实链跑通并可见：店买 `bedside_lamp` → owner tick 落地 `home/bedroom` → 正式 MOVE 回家
  （`home_goods_store → street_1 → home`，home 保留在 7 条内）→ 正式 `ARRANGE` 把灯挪到当前
  区域 `living`（substrate placement 真变）→ 正式 `TOGGLE_FIXTURE` 先 `off` 再 `on`（substrate
  fixtures 真变，同 state 为 Resolver 零写 noop）；三个 Activity 都按 owner 事实 LR-5
  COMPLETED 且前台释放。
- 边界（owner 层最小 case）：非法 ARRANGE/TOGGLE 参数在桥边界被拒；未拥有 / 非 fixture /
  跨宅目标被 owner 拒；已购实例的重复落地是 `already_materialized` 且不新增实例；纯观察
  （provider.observe）不写 substrate，不复活已摆放/已消费。陈旧候选被 owner 拒后的
  `BLOCKED` 前台释放沿用既有单步拒绝收尾（同一 `_classify_daily_action` 路径，
  [PLACE 台面与拒绝收尾](../bug-fix/2026-11-08-v07-place-surface-and-refused-step.md)）。
- 未验证：真实模型是否自然选择这两个候选（demo 的 Decision 是脚本替身）；真实两小时交付
  时序（demo 用 owner 的即时到家模式）；浏览器呈现。产品模型请求 0。
- 相关既有决定仍有效，本笔记只记录这次授权翻转，不重写旧“禁止改 allowlist”的历史：
  [日常连贯](2026-11-08-daily-life-continuity.md)、
  [房间/店/摆放/灯光](2026-10-22-rc1-room-living-store-arrange-light.md)、
  [PLACE 台面真相对齐与拒绝收尾](../bug-fix/2026-11-08-v07-place-surface-and-refused-step.md)。
