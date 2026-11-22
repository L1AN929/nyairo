# v0.8 Wave 3：街道 / 告示 / 小册 / 展陈进入日常桥接，并接上 Cycle → Resident 真结算

## Problem

到 Wave 2 为止，院子晾晒、盆栽、纸盒手作、街角借还是“收集得到、结算得动”，但：
一是 Ch.46 批次 B/E/F 的途中推进、公共告示、地点小册、书店展陈四类真实日常根本不在
候选里；二是 AG-1 即便选中桥接候选（候选被映射为 `fitem:v02v03-...`），
`_named_work_ref()` 认不出它、`_work_step_spec_for_decision()` 既没有对应步骤也没有作品可
回退，于是落在 `DECISION_NAMED_NO_WORK` / `OUTCOME_NO_STEP_AVAILABLE`；三是
`materials()` 不把桥接候选当材料，只有院子的实例会先被 `NO_MATERIAL` 挡在决策之前。
Resident 侧更严重：只要 `extra.selected_candidate_ref` 有值就结算，`WAIT` / 被拒绝的轮次
也会改世界。

## Decision

1. `v02_v03_daily_loop_bridge.py`
   - `has_v02_v03_active_state()` 保持零写入，并把真实的
     `world/v04_neighborhood_state.json` 也算作活动切片。
   - `_collect_v04_neighborhood_candidates()` 新增四类候选（B 途中推进、E 告示阅读、
     F1 小册记录、F2 书店展陈），各自带 `v04_action` / `v04_payload` / `v04_action_kind`；
     沿用既有 sick/strenuous 门控与“不预知未触发路障”。
   - 新增两个纯函数 `v02_v03_mapped_ref()` 与
     `v02_v03_descriptor_for_selected_ref()`，把 `fitem:v02v03-...` 映射回原始
     `v04_*` 描述符。
   - `V04_ACTION_FAMILIES_SELECTABLE` 纳入新的四类 family（顺带补上 Wave 2 抢收用的
     `courtyard_care`）。
2. `autonomous_life_cycle.py`
   - `materials()` 把桥接候选计入 `has_material`（未初始化实例仍是 0 候选、零写入）。
   - 决策绑定：`_decision_chose_v02_v03_daily()` / `_v02_v03_adoption_spec()` /
     `_v02_v03_step_spec()`；步骤是既有
     `INTERNAL_CHECKPOINT_STEP`（`action_kind=None`），分类器
     `_classify_v02_v03_daily_step()` 在同一有界步骤内通过桥接结算 v0.4 命令，只有
     结算成功的才 `ACTED` 并向 LR-5 提交完成事实。
   - 路由：`_work_adoption_spec_for_decision()` / `_work_step_spec_for_decision()` 前置拦截；
     `_RoutedAdoptionSpec` 与 `_routed_step_builder` 增加 `V02V03` 分支；单路径时也提供。
   - 结算事件由 `v04_round_event_for_selected_candidate(collected, raw_ref)` 生成，与
     Resident 钩子使用同一 operation key，重复结算是账本重放。
3. `autonomous_life_resident.py`
   - v0.4 结算只在 `decision ∈ {START, CONTINUE}` 且
     `status/status_class == ACTED` 时发生；原始引用按
     `origin_source_ref → target_refs → source_ref` 解析。

## Alternatives considered

**A. 让 Cycle 选中桥接候选时直接复用“作品续写”步骤（把 `fitem:v02v03-` 当作伪作品）**
这最省事：采纳形状、步骤和分类器都不用改。否决原因：它会让桥接线索真的落回某个写作
artifact（或新建一个），把“街道上读一张告示”写成一篇作品，污染作品列表与 Supply 版本，
且 `_named_work_ref` 的“决策命名了什么就只有什么”铁律会被打破。

**B. 只改 Resident，Cycle 侧仍允许 `DECISION_NAMED_NO_WORK` 后回退到别的作品**
这是 Wave 2 的实际行为。否决原因：用户可见的事实是“她选了告示，却续写了昨天的稿子”，
因果不成立；而且没有作品时整轮无法完成。

**C. 由 Cycle 自己直接调用 `apply_v04_neighborhood_command` 并自造结算回执**
否决原因：V-A 铁律要求回执来自真实 owner。这里采用的做法是 Cycle 调既有
`settle_v02_v03_round_consequences()`，由它带着真实 `operation_key` 去调
`apply_v04_neighborhood_command(actor_side="subject")`，回执由 v0.4 owner 产生；
被拒绝时步骤不完成。

**D. Resident 只信任 `selected_candidate_ref`，不校验 decision/status（现状）**
否决原因：`WAIT` / `HARD_REJECT` / `STEP_REFUSED` 也会改世界，是乐观落账。改为
`START/CONTINUE + ACTED` 双条件。

## Consequences

- 四类新日常从真实 v0.4 状态出发、经真实桥接结算落账；告示正文在阅读前不泄露，私人小册
  对伴侣保持 `withheld`。
- 只有院子的实例也能进入 AG-0/AG-1（`has_material` 不再为假）。
- Cycle 的完成事实来自真实 v0.4 owner；命令被拒绝时 Activity 不完成，且 Resident 不会越权
  结算。
- 代价：同一原始线索的结算 operation key 仍是“按线索而非按决策”的
  `resident_v04_<raw>`（沿用 Wave 2），因此同一个告示只会被读一次、同一个展陈只会提交一次；
  需要反复推进的旅程在后续轮次会先撞账本重放，这是已知的、与 Wave 2 一致的限制。
- 未改动任何 v0.4 对外协议、冻结文件或依赖。
