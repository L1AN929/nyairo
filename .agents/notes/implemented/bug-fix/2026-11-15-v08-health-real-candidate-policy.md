# 2026-11-15 · v0.8 急性病真实候选门与确定性分席

## Problem

`creative_block=true` 时，AG-1 的 `candidate_is_creative_work` 只认字面 token 与两个
`candidate_kind`；真实生产者写出的候选 `semantic_identity` 一律是 `semcand:<16-hex>`，因此
`artifact_continuation_source` 的作品续写/收口、`v03_job:manuscript_fee`、创作类 `popt:`
以及正在写作品时的 `CONTINUE_CURRENT` 全部漏拦（实链 `creative_rejections == 0`）。
cap 又按 `cand:<sha256>` 哈希序截断，病中 4 个席位是否留下阅读/休息/温水取决于实例 UUID。
相邻两处：`somatic_comfort_view` 的 `ajar` 分支传了非法 `salience="mild"` 直接抛
`ValueError`；`body_health_projection._unknown_health_view` 把“尚无实证读数”译成
`creative_block=true` / `candidate_cap=0`，前端误报“暂不安排高强度创作”。

## Decision

1. 生产者在 `CandidateRecord.provenance_chain` 末尾追加 `step="signal"` 轻量标签
   （`domain` / `creative_intensive` / `source_ref` / `action_family`）。标签在候选 id 与
   `semcand` 身份算完之后才加，dataclass 字段与既有去重哈希不动；`CandidateRecord.from_dict`
   原样保留链上的 dict。
2. `candidate_is_creative_work(candidate, current_state=None)` 先豁免
   `NO_ACTION`/`REST`/`SUSTENANCE`/`RELEASE_COMMITMENT`、阅读延续（标签、`reading_artifact_id`
   与 `reading:reading_ar0:`）、`v03_care:*`、`v03_idle:daydream_window`；再按标签 domain、
   `creative_intensive=1`、`v03_job:manuscript_fee`、`popt:`、非阅读的 artifact 身份、
   创作类 kind、以及写作品 Activity 的 `CONTINUE_CURRENT` 判定为创作。字面 token 只作兜底。
3. AG-1 顺序固定为硬约束 → 创作门（`HARD_REJECT:BODY_CREATIVE_BLOCK`）→ cap；被拒候选不占席位。
   `apply_candidate_cap` 在健康收窄激活时按语义分席：先 1 项真实休息（`daydream_window` 不算）、
   1 项温水、1 项在读材料，其余按 `(tier, candidate_kind, target_ref, source_ref)` 确定性补齐，
   `cand:<sha256>` 不参与排序。非病中仍按原顺序截断。
4. `ajar` 的 salience 改为本模块词表内的 `"low"`（词表是 `none/low/medium/high`，不是
   `background/noticeable/strong`），并补单测。
5. 手机读面对 `UNATTESTED` 返回 `candidate_cap=null`、`creative_block=false`、
   `state=UNKNOWN`、`attested=false`；内部容量信封的保守常量保留给决策层。

## Alternatives considered

- **把标签塞进 `semantic_identity` / `target_refs`**：最省事、无需新增载体。但会改变
  `semcand` 与既有去重/幂等哈希，破坏“同一机会一个身份”，且 target_refs 被当作真实目标校验；
  否决。
- **给 `CandidateRecord` 新增 `source_signals` 字段**：语义最干净。但该 dataclass 与
  `from_dict` 的未知字段拒绝位于 `candidate_sources_ag0.py`，不在本单写集，且新增字段会让所有
  生产者一起改；用既有 `provenance_chain` 的 signal step 等价表达，否决。
- **cap 仍按候选 id 哈希截断，只在测试里关掉 v02/v03 源**：能过测试，但留下真实生产缺陷，
  正是本次要删的作弊禁源；否决。
- **把所有 `fitem:` 一律当创作**：会误伤普通明日便签与日常待办；改为读
  `creative_intensive` / 目标是否 artifact。

## Consequences

`tests/test_v08_health_decision_chain.py` 的实根链路在全部真实候选源开启下：病中
`creative_rejections=2`、cap 裁掉 ≥1 项、4 席含卧床/温水/阅读，阅读书签推进而作品 head 不变；
同一实根经 `settle_v02_v03_round_consequences` 的 `REST`/`WARM_HYDRATION`/`ADVANCE_CLOCK`
恢复为 `HEALTHY`（`creative_block=false`、`candidate_cap=null`）后，作品续写重新被采纳并把
版本从 1 推进到 2。新增 `tests/test_v08_health_candidate_policy.py` 用不同 UUID 与乱序输入
证明分席不随随机数漂移。

代价：候选携带 1–4 个 signal dict，`provenance_chain` 略长；AG-1 的分类逻辑比字面匹配复杂，
新增/修改候选生产者时必须写 domain 标签，否则只能落到 artifact/kind/token 兜底。关联
[[2026-11-15-v08-health-identity-gate]]、[[2026-11-15-v08-body-health-envelope]]。
