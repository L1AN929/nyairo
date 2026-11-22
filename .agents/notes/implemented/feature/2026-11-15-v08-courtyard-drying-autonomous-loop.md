# v0.8 Wave 2：院子晾晒、门廊盆栽、书桌纸盒与街角借还在自主日常循环中贯通

## Problem

`v04_neighborhood_living`（3,650 行）与 `v04_neighborhood_view` 早已具备分段干燥、盆栽需水、
纸盒/卡片守恒与借还回执，但自主日常循环只在旧投影形状上瞥见一个 `courtyard.drying_rack`
半成品分支，真实的 `world/v04_neighborhood_state.json` 从未进入候选、结算与时钟推进。
结果是"决策选了但没有世界动作"：布巾不会真的被挂出去、淋雨、收回；盆栽不会渴；纸盒不会前进；
借出的书和工具也不会被归还。Wave 1 已把身体门控做好，本单负责让它有真实生活可门控。

## Decision

- `collect_v02_v03_loop_candidates` 新增 6 类真实候选，全部从 `load_state_readonly` 的原始状态
  按实际条件触发：`v04_courtyard_hang`（室内湿布巾→晴天露天绳/微雨门廊）、
  `v04_courtyard_cloth`（露天遇雨抢收→门廊）、`v04_courtyard_collect`（干透收进屋）、
  `v04_porch_plant`（缺水浇水 / 院子遇雨搬回）、`v04_home_craft`（纸盒下一步或誊写卡片草稿）、
  `v04_errand_return`（共享架工具或书店图书归还；闭店时 `via_return_box=True`）。
  每个描述符携带 `v04_action` + `v04_payload` + `v04_action_kind`，以及
  `requires_outing` / `creative_intensive` 的真实负荷标注。
- 零写入保护：`<day_root>/world/v04_neighborhood_state.json` 不存在时直接返回 `[]`，
  不建目录、不建文件、不改 mtime；旧投影形状的 `drying_rack` 分支保留为只读向后兼容。
- `settle_v02_v03_round_consequences` 新增 v0.4 分支：接受 `v04_action+v04_payload`、
  字面 `action="v04_..."` 或六种 `action_kind`，统一经
  `mutate_v04_neighborhood_view(..., actor_side="subject")` 落账；拒绝即不结算，
  确定性 `operation_key` 让重放返回 `already_applied`，物品/纸张/图书严格守恒。
- `ADVANCE_CLOCK` / `CROSS_NIGHT_ADVANCE` 与 `run_v02_v03_daily_tick` 在状态文件存在时调用
  `advance_v04_neighborhood_processes(now_iso=target)`，晴天蒸发、雨天暂停/淋湿、盆栽失水按真实
  分段延续；未初始化时零写入。
- `autonomous_life_resident._run_v02_v03_bridge_hook` 在本轮 Decision 选中 v0.4 候选时，
  从真实 candidate payload 反查描述符并自动结算；`run_v02_v03_daily_tick` 选中的 v0.4 机会
  同时带上 `v04_round_event`，供调用方按同一确定性动作结算。
- 手作候选 `creative_intensive=True`，外跑腿候选 `requires_outing=True`：Wave 1 的
  `HARD_REJECT:BODY_CREATIVE_BLOCK` 与 `ILLNESS_BLOCKS_STRENUOUS_OUTING` 直接生效；
  家门口抢收 `requires_outing=False`，重病时仍可执行。

## Alternatives considered

1. **让桥接层直接读写 `v04_neighborhood_state.json`**：最强理由是少一层视图校验、实现最短。
   否决原因：绕过 `ACTION_ALLOWED_FIELDS`、actor_side 与 `operation_key` 回执，等于把已经
   收敛的契约重新拆开；所有结算统一走 `mutate_v04_neighborhood_view` 才能保住幂等与隐私分级。
2. **在采集阶段顺手 settle 时间推进并落盘**：最强理由是候选永远最新。否决原因：采集必须
   100% 只读，否则每个 tick 都改 mtime、破坏零写入测试；推进只在显式时钟节点由
   `advance_v04_neighborhood_processes` 执行。
3. **改 `autonomous_life_cycle.py` 让 AG-1 直接执行 v0.4 动作**：最强理由是执行链更短。
   否决原因：会触碰 Wave 1 刚验收的决策核心；resident 的候选回调已能拿到选中 ref 与真实
   payload，不需要改决策器。

## Consequences

- 晴天挂出 → 分段半干 → 重启保留 → 遇雨抢收 → 放晴干透 → 收回的完整链路可端到端跑通，
  重复结算返回 `already_applied` 且布巾数量不变。
- 急性发烧时纸盒手作在真实 `ArtifactContinuationSource.materialize` + AG-1 决策里被
  `HARD_REJECT:BODY_CREATIVE_BLOCK` 拒绝，外跑腿被候选门过滤，家门口抢收不受影响。
- 未初始化实例零 v0.4 文件；partner 视角严格看不到私人卡片草稿，HTTP 路由同一分级。
- 代价：`v02_v03_daily_loop_bridge.py` 增大约 900 行（含既有未提交改动基线）；采集每次多一次
  只读状态解析（文件不存在即返回），无产品 LLM 调用。
