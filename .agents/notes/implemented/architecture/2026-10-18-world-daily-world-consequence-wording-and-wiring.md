# Agent Note: World 后果在 M0 用中性叙述，产品装配本来就把 M0 接进决策输入

Status: implemented

## Problem

I-r2 留下两个不准的地方，父级窄收口要求在本轮清掉：

1. **一条 World 后果被写成了作品腔**。真实 M0 行（`artifact_ref=null`，`action_ref` 是本次消费的 AR-0
   动作，`owner_result` 是 AR-1 结算 `rset:`）的正文是“完成了一次真实修改，作品所有者确认了新版本，
   作品 …”。结构化引用是对的，叙述是假的：那次消费/停留根本没碰任何作品。
2. **r2 的 `experience_provider` 判断错在产品侧**。r2 回执与笔记都写“`assemble_autonomous_life()`
   没把 `experience_provider` 接上，所以产品里模型从来收不到 `prior_experiences`”。这只对
   `tests/test_life_v1_integration.py` 的测试基类 `assemble()` 成立；产品入口
   （`autonomous_life_resident`）是接了的。
3. **“记忆影响下一步”之前只在“行被带上了”这一层被证明**，没有证明同一条固定规则在同一次输入下
   因为这条行而选了另一个**合法**候选。

## Decision

### 按证据分流，而不是按 kind 分流

`life_experience_memory_consumer._text()`：`world_refs` 非空且没有 `artifact_ref` → 中性 World 句；
否则老的作品句原样保留（一个字都不动）。理由是证据性的：一次消费/停留的 handoff 本来
`artifact_refs=[]`，而旧模板对**任何** `SIGNIFICANT_PROGRESS` 都宣称“作品…新版本”。

中性句只有三样东西：`在真实世界里发生了一件已确证的事`（构造上保证——`_emit_world_experience()`
只在 owner 回执/观察为真时才发 handoff）+ owner 自己的 `世界事实 world_fact:…` / `动作 actn:…` /
`所有者回执 rset:…`。`_refs()` 在 artifact 之后补 `world` 来源（最多 2 条），`settlement→owner_result`
照旧，**不新增**任何 ref 种类。显式携带未决结论（`outcome_kind`/`settlement_outcome` 或 world ref
末段为 `UNKNOWN_EFFECT`/`UNKNOWN`/`PENDING`/`OPEN`）时换成
`在真实世界里做过一件事，结果还没有确定`——absence 永远不升级成确定成功。

### 模型读到的是 `prompt_view`，所以规则认的是行文里的 owner token

产品给模型的是 `memory.port.prompt_view()`：`experience_id/occurred_at/text/activity_ref/
artifact_ref/version_ref/owner_result`，**没有** `action_ref`。所以固定替身规则只能读行文里 owner
自己的结构化事实 token `world_fact:object:<id>:consumed`，不能按 `action_ref` 索骥，也不能读任意散文。

规则的边界写死在替身 docstring 里：记忆只允许说“那件事已经发生”（这一份已经用掉了，不再出门找同一份），
永远不能顶替位置、权限、身体状态或人格事实。弱 cue、空行、作品行都不产生偏向。

### 更正 r2，而不是留一条假话

`assemble_autonomous_life()` 确实只是**接受** `experience_provider` 再原样交给
`AutonomousLifeCycle(...)`；把它接上的是 resident：`_memory_ports()` 用配置的 Memory DB 造
`MemoryAdmissionExperienceConsumer` + `lambda: port.prompt_view()`，没有配置时返回
`(None, None, None)`，`assemble(..., experience_provider=experience_provider, ...)` 一路传到 cycle，
cycle 的 `consideration_context()` 就是 `prior_experiences`。这条链条用两个最小用例钉住
（配了 → 请求里有；没配/读失败 → 请求为空）。

## Consequences

- 一条 World 后果不再宣称“真实修改/新版本/作品”，但保留了全部真实来源：world facts、动作、结算回执。
- 作品 handoff 的文案与来源**完全不变**；`CONFLICT`/`COMPLETED`/`ABANDONED` 也照旧。
- 未决后果（`UNKNOWN_EFFECT` 等）现在会被写成“结果还没有确定”，重复投递仍然只有一行（`lexp:` 去重）。
- 主链里这条行真的改变了一次合法选择：同一个“还想吃”的意图、同一次输入、`place:street_front` 仍然合法，
  读到 owner 的 consumed 事实后选了就地停留（真的 START、有 `planned_seconds`、到点 END），而之后
  RESUME 的请求里带着这行、CONTINUE 真的落成新 Supply 版本。
- 演练里为什么要显式 `cycle.experience_provider = …`：测试基类 `assemble()` 没传这个参数。产品路径不需要。

## Alternatives considered

- **只改文案不管来源**：不行。World 后果的可审计性正来自 `world`/`action`/`owner_result` 三类 ref。
- **给 World 经历单开一张表/一个 "world history" owner**：不需要，既有 handoff 已能承载 `world_refs`；
  再造就是第二套 manager。
- **把 `action_ref` 塞进 `prompt_view` 好让测试按它认行**：为了测试方便改 Memory 的视图形状，不行。
- **在 Life 里按经验写“吃过就回家”的路由**：那就是固定的强制脚本，父级明确禁止；规则活在确定性替身里，
  只表达“这件事已经发生”。
- **为了对照再跑一整次 episode**：同一候选集 + 同一规则的“有行/无行”对照用真实 M0（consumer + port +
  prompt_view）在小模块里做，主链只跑一次。

## Evidence

- `tests/test_world_daily_life_world_memory_text.py`（4 例，真实 `EvidenceStore` + 控制账本 +
  `LifeExperiencePort` + consumer）：World-only 中性文本与来源、artifact 文案不变、
  `UNKNOWN_EFFECT` 不升级且重复投递 `DUPLICATE`、同规则下只有被准入的 World 行改变选择。
- `tests/test_world_daily_life_experience_wiring.py`（2 例）：`AutonomousLifeResident._memory_ports()`
  + 真实 `AutonomousLifeCycle(experience_provider=…)` 的 `consideration_context()`——配置了就有，
  没配置/读失败就是空。
- `tests/test_world_daily_life_continuous.py`：步骤 8（世界行文本中性、来源与回执齐全）、步骤 8b
  （`world_consumed==['i-food-riceball-01']` → `stay:home:…` 而非出门）、步骤 9（RESUME 请求带这行，
  随后 CONTINUE 落新版本）。
- `tests/test_world_daily_life_plan_change.py`：同一规则在 CLOSED 停留、OPEN 出发、到店才读。
- 回执：`docs/engineering/world-daily-i-r3-result.md`。
