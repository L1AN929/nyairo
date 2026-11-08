# Agent Note: v0.6 ambient 隔离 off/on 分层检查

Status: implemented

## Problem

C 实际加载的是 `worldgen.ambient.v1` 的 6 条单日模板（`coastal_town_929711`，
Asia/Tokyo，17 地点），而 C 的历史运行只留下「配置值 0」和「候选/最终 trace 无
ambient 字段」两类证据。缺的是一个同等隔离初始状态、同一 home/bedroom、同一冻结业务时刻下
的 off/on 对照：World 正式观察到底能不能看到事件，事件又是在哪一层从 Life 的背景
材料、AG-0 候选和正式 prompt 里消失。这个问题不能用 `materialize_ambient_events`
单独回答——那只证明规则函数能算出事件，不证明正式观察与 Life 装配实际接上了。

## Decision

新增 `scripts/v06_ambient_isolated_check.py`，只做零模型、隔离、分层的实际检查：

1. 父级每次 `check` 建全新 `/tmp/nyairo-v06-ambient-offline-*` 根，只从 C 旧根静态
   复制 `one-day/world-package` 的原字节到新根；先按 world_id/时区/17 地点/6 条 v1
   模板逐项核对，不一致即 STOP，不换包、不重造。旧根不启动、不导入、不写。
2. 取一次当前 UTC 时刻并转 Asia/Tokyo 冻结，off/on 两个独立 phase 子进程共用这个
   时刻、同一份包副本和同一 home/bedroom rev1 初始状态；两边的差异只有
   `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=0/1` 与各自隔离路径/进程身份。父进程不 patch
   自己的环境。
3. 每个 phase 起真实 Supply 与真实 World gateway（本根 AF_UNIX），先做一次受控
   AF_INET 负控确认硬拒，再走正式 `world_living_skeleton.observe_world`；读取实际
   `ambient_events` 的 ID/类型/地点/时间窗/count，并核 phase 子进程与观察环境里的
   ambient 实值。
4. 用正式 `resolve_world_provider` → `assemble_autonomous_life` → 正式 cycle
   `evaluate_once`；脚本 cognition 只接收正式 Decision 请求并返回合法 `NO_ACTION`，
   不执行 owner Action。对 `daily_life_context`、`situation_context.world`、AG-0
   候选和正式 `build_prompt` 只做已知事件 ID 的内存成员判断，输出布尔与字段路径，
   不输出 prompt/正文。
5. 判据分两层：装配/隔离/禁网/服务关闭是 checks；「World 有差而 Life 无差」是
   comparison 里的 `OBSERVED_GAP` 和最早丢失层，不当作机制已通。

实际运行（2026-11-02，一次最终入口，16.657 秒）：

- off：World 观察无 `ambient_events` 键、0 条；Life READY，4 个
  `WORLD_OPPORTUNITY` 候选，脚本 `NO_ACTION`，正式 prompt 8127 字符未裁剪。
- on：World 观察有 `ambient_events` 键、1 条
  `ambient-2026-11-02-home_window_sound`（home，00:00–24:00）；Life 仍是同一组 4 个
  候选、同一 prompt 结构（8127 字符）。
- 事件 ID 只出现在 World 观察的 `$.ambient_events[0].event_id`；在网关
  `get_snapshot`/`get_observation`、Life provider 快照、`situation_context.world`、
  `last_live_context`、AG-0 候选和正式 prompt 中均无命中。最早丢失层是
  `gateway_socket`，具名接缝是 `world_body_gateway_api.get_snapshot/get_observation`
  不投影 `ambient_events`。
- 当日轮转从索引 2 起，纯函数附属查询显示 11-02 选中
  `store_evening_sale/home_window_sound/street_construction`，11-03 选中
  `home_window_sound/street_construction/beach_tide_low`；home/bedroom 的地点过滤只
  保留 `{home}`，当前 22:44 JST 只有 `home_window_sound` 在时间窗内。这是解释层，
  不替代正式观察；旧 24 条有限事件 11-01 到期的结论不适用于这 6 条 v1 模板。

## Alternatives considered

- **只调 `materialize_ambient_events` 做对照**：最省、无需起 World。否决：任务要证明
  正式观察和 Life 装配是否接上，纯函数结果会把「规则算得出来」误当成「机制已通」。
- **复用仓库 `examples/worlds/nagiura` 或重新按 seed 生成包**：省去复制旧根。否决：
  它们与 C 实际包已不同（同 world_id 但模板/内容不同），换包就会把被测对象换掉。
- **`assemble_autonomous_life(..., world_provider=None)` 让装配内部解析**：调用最短。
  本次实际发现该入口只把解析结果交给 World feed，先构造的 cycle 仍拿到 `None`，导致
  `NO_MATERIAL`、没有任何候选或 prompt。否决：按 resident 的正式用法先
  `resolve_world_provider`，再把 provider 同时交给装配。
- **调用真实模型验证 ambient 是否改变选择**：最接近自然行为。否决：本单零模型、
  零产品请求；脚本 Decision 明确 `NO_ACTION` 只用于到达正式结构。

## Consequences

- 收益：第一次在同一隔离状态、同一冻结时刻下把「World 正式观察 → 网关 socket →
  Life 背景材料 → AG-0 → 正式 prompt」逐层分开取证；`home_window_sound` 在 World
  层真实可见，而 Life 各层完全没有它的 ID，断层被定位到网关投影而不是「ambient
  没生效」。
- 收益：同一 home/bedroom/rev1/时刻下 off/on 的候选 ID、来源和 prompt 结构一致；
  非空事件不是验收前提，纯函数附属查询能解释轮转/时段/地点过滤各自的作用。
- 代价：只覆盖 C 的 `worldgen.ambient.v1` 6 条单日模板，不覆盖有限跨天事件、包外
  其它世界、真实模型自然采用、G1 或 24h。旧 C 根只被读取静态包字节。
- 边界：本单不修生产接缝。`OBSERVED_GAP` 的具名层是
  `world_body_gateway_api.get_snapshot/get_observation`；是否把 ambient 投影进
  Life 背景属于产品/契约决定，另行批准。
- 失败史：入口先有三轮具名失败——包复制到父根后不满足 phase 根内路径守卫；以及
  `world_provider=None` 时 cycle 未拿到 provider 导致 `NO_MATERIAL`。前者把包改为
  每 phase 自复制，后者改为先解析 provider；第四轮最终入口 PASS。所有 scratch 保留。
- 预算/状态：产品请求 0，212/300 余 88 沿历史不变；未提交、未推送、未部署。

## 父级独立验收与结论限制

父级固定入口另跑一次实际 exit0/PASS，19.539秒，新根 `/tmp/nyairo-v06-ambient-offline-v3jc53rc`，冻结时刻2026-11-02 22:47:17 JST；off0/on1、同四候选、两边8127字符未裁剪，结果与worker同方向。PASS只接收本次检查装配执行，不代表ambient已贯通Life。

直接World观察是 `observe_world` 正式函数读口，gateway两个socket读口是另一条实际读取；不能把它们描述成事件已从前者发送给后者、再被逐跳丢弃。socket两响应均成功且没有ambient键；提取器没有保留完整响应进行内容语义核对。各层只检查事件ID，未检查模板summary精确成员，更未检查同义描述，因此只能证明“本次接口无ambient字段、下游未见该事件ID”，不能证明Life完全没有这条环境信息。`gateway_socket`是本次首个缺少专属字段的被测接口，生产漏接根因仍以worker静态接缝说明为线索，不作为父级独立源码定位。

正式Decision请求由脚本cognition捕获，之后调用生产 `build_prompt` 构造最终结构；未真正发往模型。World/Supply在phase内以线程运行，不是两个独立owner子进程；phase ambient值和观察env已核，未另取World独立子env。正常路径runtime close、World shutdown报告lease_released=true、Supply shutdown及World socket路径消失已观测，不扩大为所有listener/线程join/drain完整证明。600秒极端超时收尾没有注入验证；产品请求0字段为固定声明，结合脚本替身路径和局部禁网负控限定采用，不当独立网络逐请求审计。

相关：[世界包 ambient v2 与有限跨天事件](../architecture/2026-10-25-world-ambient-v2-finite-events.md)、
[世界环境事件目录扩充](../feature/2026-10-25-world-ambient-event-expansion.md)。

## 后续修复（2026-11-02，历史边界）

本文记录的「World 有、Life 无」是**修复前的真实断层**；原三轮装配失败史、父级限制和
「只查 ID 不证 summary/同义信息」的边界全部保留，不修改、不倒写。后续限定补接
[既有 ambient 事实进入 Life 背景](../bug-fix/2026-11-02-v06-ambient-life-background.md)
已在 `daily_life_context`、`life_live_context_view` 和正式 `build_prompt` 接上有界背景块，
并在 driver 里增加 event id 与公开 summary 的精确成员检查。本文的 off0/on1、四候选相同、
8127 字符和 `OBSERVED_GAP` 都是修复前证据，不能当作修复后的当前状态。
