# Agent Note: 可选虚拟身体需求（饿/渴/如厕）——显式档案、有限漂移、真实动作回执

Status: implemented

## Problem

旧裁定（[CONSUME 不造营养](2026-10-17-world-consume-non-nutrition-body.md)、
[不做强制吃睡打卡](2026-10-16-non-coercive-life-actions.md)）留下了两个同时成立、
但彼此没有接口的事实：

1. Body 只有六个 somatic 变量，`CONSUME` 只记 2 秒操作负荷，吃一份东西在身体上
   等于什么都没发生；
2. 产品又需要一种**可选的**“她白天会渴、会饿、需要如厕”的虚拟状态，让真实
   World 动作（喝水、吃、用设施）有可见的后果，而不是只改显示数字。

直接加一个全局 hunger 会撞上旧铁律：没有“不吃会怎样”的惩罚规则，饥饿就只是一个
装饰数；加上惩罚，又回到被否决的生存打卡。另建一个平行身体库则会制造第二真相，
并与 Body 的持久游标/恢复状态脱节。

## Decision

1. **显式 opt-in，旧实例零变化。** 只有 `data/body/lived_needs_profile.json`
   存在且 `enabled: true` 时，实例才从 `body.runtime.v1` 升到
   `body.runtime.v2`，多一个 `lived_needs` 块。没有 profile 的实例仍只有 v1
   的精确字段；`LIVED_NEED_RELIEF` 输入在 v1 上直接拒绝。升级由
   `body_runtime.upgrade_to_v2` 完成：somatic/recovery/version/游标原样携带，
   只加新块。
2. **初值只来自操作者参数。** `scripts/body_lived_needs_enable.py --hunger
   --thirst --toilet` 写入显式初值；不读过去经历、不按种子负荷伪造“她已经很累”。
   旧疲劳是既有六变量，演示夹具里的 `fatigue=0.45` 明说是合成初值。
3. **漂移只认活窗口，不追罚。** 漂移不再从 `last_advanced_at → now` 的墙钟差
   推断。`body_lived_needs.advance_live_window` 只在服务自己见证过的两拍之间
   累积：启动时把 `last_live_at` 锚定在启动时刻，之后每次 tick 的间隔若不超过
   `LIVE_WINDOW_MAX_GAP_SECONDS = 120` 秒才计入；更长的间隔（停机、系统挂起、
   重启、时钟回退）一律记入 `offline_skipped_seconds` 并跳过，绝不补算。
   `body_runtime.advance` 只应用 relief，不承担任何时间漂移；因此停机十小时再
   启动，需求水平与停服前完全一致；正常关闭/重开也不退款已发生的需求。
   没有午夜清零、没有 timer、没有到点自动行动。漂移率随状态一起持久化
   （`rates`），一次结算仍是纯函数，不需要第二次读 profile。
4. **回血只来自 World 的显式声明。**
   - `CONSUME`：`state/world_object_capabilities.json` 的
     `consumables[*].lived_need_effects`（如 `{"thirst": 0.8}`）。声明缺失或值
     越界 → 没有 relief，旧“CONSUME 不造营养”的默认行为保持不变。
   - `TOILET {facility_id}`：同一文件的新块 `lived_need_facilities`，要求
     `facility_kind=toilet`、`location_id` 等于当前地点、该地点按世界自己的
     rhythm 是 OPEN、并且显式写了效果。它不是消费（对象不消失）、不产生库存/
     支付、不写任何隐私正文，也不宣称人体生理。
   - 两条都走既有 Resolver 的 prepared→committed 账本、一次性授权、新鲜度/
     CAS/幂等；结果里的 `lived_need_effects` 由 `body_consequence` 转成
     `LIVED_NEED_RELIEF` 输入，只在该 Body 有 v2 块时生效。
5. **公开面只有档位。** `body_signal.produce_signals` 在 v2 上附带
   `needs: {hunger/thirst/toilet: none|mild|noticeable|strong}`；`get_snapshot`
   与手机的 body 块只带这个映射，不暴露 0..1 数值。`life_live_context_view` /
   `life_body_context` 把同一档位带进决策材料。
6. **候选仍由 owner 给。** `observed_toilet_opportunities` 只翻译网关自己的
   `TOILET` 条目（该条目只在有 v2 profile 且 Body 报出非 none 的 toilet 档时
   出现）；`bodyact:toilet:use:<facility>` 的完成事实读的是**使用后**的 owner
   档位（`world_fact:body:need:toilet:relieved`），不是提交的 target。吃/喝仍
   走既有 `object:` 候选。
7. **REST 语义不变。** 恢复会话仍是命令，不是“已恢复”；本包没有把 session
   completed 说成 recovery，也没有改六变量阈值/积分。

## Alternatives considered

- **全局加 hunger 变量 + 不吃惩罚**：实现直白、模型天然理解。否决——正是旧
  note 明确拒绝的生存打卡；没有惩罚的 hunger 又只是装饰。
- **定时器/固定钟点自动变饿或自动去吃**：最省事，但会让暂停/断流/长耗时一次
  雪崩，违反“时间不是身体测量”。否决：漂移只按服务活窗口，离线间隔跳过，
  动作仍由 Decision 选。
- **保留 `last_advanced_at → now` 差并按 15 分钟封顶**：改动最小，但停机十小时
  仍会得到 15 分钟补算，和“不按墙钟补饿”矛盾；封顶只是缩小了错误量，不是
  不补算。否决：改为活窗口见证，没有证据的区间明确 skipped。
- **另建 `lived_needs.json` 平行 store**：改动面小，但会与 Body 的锁、revision、
  游标和恢复状态分成两个真相。否决：扩到 `body.runtime.v2`（版本字段 + 向后
  兼容读取），旧 v1 原样支持。
- **把如厕写成 CONSUME 一个“设施对象”**：复用现成回执最省事。否决——那会让
  设施消失、把一次使用说成一次消费，还要编库存；新增 `TOILET` 更诚实。
- **让所有 CONSUME 都回血**：体验最“有反应”。否决——旧声明没有效果就是没有
  效果；只有显式 `lived_need_effects` 才生效，也避免“吃椅子补体力”这类误推。
- **把需求数值直接进 prompt 或让模型自己填 relief**：否决——数值不是模型可写
  的事实，relief 只能来自 World 声明并由 Body owner 执行。

## Consequences

- **收益**：一条真实链在同一实例里成立——profile 显式启用 → 下一轮决策材料看到
  渴/饿/如厕档位 → Decision 选喝/吃/用设施 → Resolver 提交并落回执 →
  `body_consequence` 写 `LIVED_NEED_RELIEF` → Body v2 的档位变化 → 再下一轮
  请求材料的档位确实不同。NO_ACTION 合法且不改需求；REST 仍只是会话。
- **代价与已知上限**：漂移是虚拟倾向，不是人体测量；只在服务连续运行时增长，
  进程不在的时间不补，因此长时间停机后的数值不会跳变。窗口阈值
  `LIVE_WINDOW_MAX_GAP_SECONDS=120` 是保守判断：超过它的间隔即便实际仍活着也
  记 skipped（宁可少算，不猜）。`TOILET` 的完成事实依赖 owner 报出的 toilet
  档位，档位未降回 `none` 时该步保持 unsettled（有意的 fail-closed）。旧实例、
  旧声明、旧 REST/六变量语义不变；没有迁移旧 root，也没有默认打开任何 profile。
- **验证边界**：`tests/test_body_lived_needs.py` 覆盖 v1 不变、活窗口锚点/
  累积/离线跳过/时钟回退、旧 state 兼容、relief 夹取、声明效果、TOILET 编码/
  事实/设施判定。`scripts/body_lived_needs_demo.py` 在全新隔离 root 上用真实
  Supply/World/Body owner 与脚本 Decision 跑完喝水→如厕→吃→坐下→REST
  start/stop→NO_ACTION，并同根停服重读；另用真实 `WorldBodyService` 生命周期
  证明“连续 tick 增长→正规停服→注入时钟跨 10 小时重开不增长（记 skipped）→
  再连续 tick 又增长”（时钟是机械模拟，不是真停 10 小时）；0 产品模型请求。
  它不证明真模型自然采用、不是 G1/24h/V-C，也不把 REST 命令当恢复。
- **入口供给（2026-10-27）**：`scripts/nyairo_day.py init/start --lived-needs` 是正常启用路径；
  它在实例已有的 `state/world_object_capabilities.json` 里写入一组固定的家用声明
  （水瓶解渴、饭团解饿、家里洗手间如厕），并用 substrate owner 把水和饭团放到
  `home/bedroom`。没有声明的东西（例如杯子）不会回血。世界包格式没有因此新增
  consumable/facility schema；旧实例和未启用 profile 的实例一个字段都不变。
  见 [日常入口](2026-10-27-living-entry-neighborhood-and-body.md)。
- **升级触发条件**：若需要“吃半份/多口”或真实份量，应先做份量/支付 owner，
  不应把 `lived_need_effects` 扩成库存；若要把需求接入更多动作，应继续由 World
  声明 + Body owner 执行，不在 Life 层直接改档位。
