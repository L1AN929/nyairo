# 具身冷暖/干湿/坐姿/触觉投影、窗扇与外衣、桌边照顾

Status: implemented

## Problem

设计 33/34/37 要求把“世界环境物件 → Body 姿态负荷 → 有界感觉投影 → 认知解释选择”四层
分开，但手机侧当时只有整块 World/Body 快照与零散的居家读面：没有四类模态的有限投影，
没有窗开合/外衣干湿/局部位置这类局部条件，也没有“伴侣放下温饮/靠垫”与“本人拿起/使用”
的分离。缺的三件事都不能靠编事实补：

- 没有温度/气象权威，不能把卧室名字当窗边，也不能说“全家冷”；
- 没有衣物 owner，不能从 ambient 雨声推出淋湿；
- 生产目录没有可饮用的可消费实例，不能本地减 thirst 或伪造 CONSUME 回执。

## Decision

新增 `components/phone/somatic_comfort_view.py`（纯投影 + 一份实例 JSON），只在
`service.py` / `server.py` 做窄接线；owner 仍是 World、Body、`PhoneShopping`。

1. **`GET /api/phone/body/somatic`**：真实 World 快照 + 真实 Body 投影，加本实例仿真条件。
   固定 4 类 `thermal/moisture/posture/tactile`，每类有限 `quality/salience/region/source_refs`；
   没有证据就 `unknown/none`。窗侧 `cool` 需要本人显式 `local_position.zone=window_side`、
   绑定同一真实 place/area、且窗 `open/ajar`；关窗同地变 `none`。posture 不带持续时长
   （`NO_POSTURE_DURATION_EVIDENCE`）也不诊断疼痛；tactile 先 `reachable_warm_object`，
   本人 `take` 后才 `held_warm_contact`。`temporal_alignment` 永远标 `partial`。
2. **`GET/POST /api/phone/body/garment-window`**：窗 `open/ajar/closed`、局部位置、外衣
   `wear/hang` + `dry/damp/unknown`。穿挂互斥、单一 state；新实例 unknown。微潮只来自本人
   显式申报（`self_declared_simulation`）。挂起 damp 后保存 `started_at` 与固定 6 小时速率，
   GET 按 elapsed 派生、不落盘；同意图不重置、不升 revision；时钟回退 unknown。窗/衣物写口
   只属于本人（CSRF+cap），不开放伴侣替她调整。
3. **`GET/POST /api/phone/body/care-offering`**：1 饮品槽 + 1 靠垫槽。伴侣可 `offer`（CSRF），
   位置由服务端现读真实 place/area 绑定；`desk_side` 还要求当前区域真有 table/桌面。
   温饮有持久 `placed_at` 与 30 分钟冷却曲线，GET 按 elapsed 派生；重复同内容不重置。
   本人 `take/drink/use/stow` 要 CSRF+cap；伴侣只能 `withdraw` 自己尚未被动过的 offer。
   靠垫放下只 `available`，本人 seated 且仍在绑定区才 `support_active`，坐姿/区域变化后
   GET 重核为 false。`offer/take/stow` 只写本实例 JSON，不改 needs/关系/Activity/阅读断点。
   三个 GET 允许无凭据的默认只读；一旦显式携带 `Authorization: Bearer` 或
   `X-Phone-Capability`，就必须按本次 serve 的本地 capability 常量时间校验，错误/不可用
   一律 403，绝不降级回默认读。
4. **真实饮用**：只有 offer 绑定了真实、当前可用、声明了 thirst 效果的实例，才调用既有
   `PhoneShopping.use`（key 由 offer 派生，重放不二次摄入）；否则返回
   `DRINK_UNSUPPORTED` / `NO_THIRST_RELIEVING_CONSUMABLE`，本地不标 consumed、不编回执。

新增 `tests/test_somatic_comfort_view.py`、`scripts/somatic_comfort_fixture.py`（复用居家四弹
真实 owner 构造 + 显式 lived-needs profile）、`.claude/launch.json` 的 `somatic-comfort`（3857）、
`docs/engineering/somatic-comfort-api.md`。

## Alternatives considered

**把窗/衣物/温饮写进 World 或 Body，做“第二套真值”。** 最强理由：一次读取就能像世界测量
一样稳定，不必区分仿真与权威。否决：没有这些 owner 字段；扩大 World/Body 协议会制造第二
真相并与既有 substrate/needs 失配；本单只允许实例内小 JSON，且必须明写 `phone_slice_simulation`。

**用卧室名字或 ambient 雨声直接给窗侧 cool / 湿外套。** 最强理由：实现最短，用户“看起来”
立刻有冷暖反应。否决：34/37 明确区域差异必须有真实来源；把卧室等同窗边、把模板声景当气象，
都是在编事实。改为显式局部位置 + 真实 place/area 绑定；没有就 unknown。

**drink 时本地直接减 thirst / 造 receipt。** 最强理由：生产目录没有饮品，本地点一下就能让
“喝过”有可见后果。否决：那会绕过 World 声明、Body owner、执行账本与精确一次；任务书明确
禁止。改为只转发既有 `PhoneShopping.use`，不支持就具名拒绝并如实报告未覆盖。

**给爽约/冷却/干燥加定时器或后台 tick。** 最强理由：到点自动变化，产品感强。否决：本切片
不做后台自动活动；冷却和干燥只按 GET 时 elapsed 派生，重开同 root 连续，时钟回退保守 unknown。

## Consequences

- 收益：同一实例里可以真实看到“开窗且本人在窗边 → cool；关窗同地消失；挂潮衣按 elapsed
  变干；伴侣放下温饮/靠垫，本人拿起/使用；越权 403、夹带字段 400、重复动作不涨 revision”。
- 代价与已知上限：窗/衣物/照顾是局部仿真，不是世界测量；没有天气权威、没有姿态持续时长、
  生产目录没有饮品。fixture 的 `drink` 只验证具名 `DRINK_UNSUPPORTED` 负控，**未验证真实
  饮用 receipt/exactly-once**；支持分支保留但不声称已通。
- 验证：新专项 24 例（纯投影 + 真实 World/Body/PhoneService loopback HTTP），覆盖零写、
  窗侧同位置开关、干燥跨重开、伴侣 offer/本人 take/未动撤回、靠垫站立拒绝 + 真实 POSE
  坐姿正向支撑 + 再站立重核、drink 负控、字段存在性、三 GET 在两种凭据形式下的错误拒绝
  与合法读、POST cap/CSRF。父级初次24例7.629s绿后发现GET错cap200，定点修复后父级同专项一次24例12.330s OK；3857新根三个GET默认/有效200、两种错凭据403。初次HTTP已验证开关窗cool消退、挂衣幂等、offer/take/撤回权限与9条空值夹带400。靠垫通过真实POSE坐下使用支撑、再站起失效；干燥跨重建由注入业务钟证明。两冻结字节不变，预览已停、新根保留，设计目录/旧根未改，产品0新增/573余427，无commit/push。用户于2026-11-11明确接受当前局部体感与照顾范围并要求封存收口，不扩世界商店饮品协议；该批准范围已完成，真实饮用仍未实现，作为保留能力边界而非自动待办。短结果 `.agents/worker-runs/20261111-somatic-comfort-parent-result.md`。不证明自然采用/24h/G1。
