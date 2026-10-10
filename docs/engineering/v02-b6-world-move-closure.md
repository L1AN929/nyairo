# v0.2 B6 / E08 收口：一次真实 World MOVE 的完整闭环

**性质：交付记录。** 分支 `v0.2-autonomous-life`，起点 `bbd108a`（B6 勘察）。本文只记录
实际改了什么、实际跑了什么、实际看到什么；没有跑的一律写明 NOT_RUN。

---

## 1. 交付的链路

```
合法观察 → AG-0 候选 → AG-1 Decision → AG-2 Adoption → AR-0（提案/围栏/尝试/回执）
  → World Intent Executor → 一次性授权 → World Gateway socket → World Resolver
  → World owner 状态 → AR-1 settlement / LR-5 progress
```

没有任何一环是新建的：每一步都是对既有生产模块的调用。

## 2. 实际改动

| 文件 | 改动 | 为什么 |
|---|---|---|
| `chiyo_bundle/world_read_service.py` | 初始化时区 `Asia/Shanghai` → `Asia/Tokyo` | 骨架 `_load_state` 要求 `WORLD_TIMEZONE`；不匹配时**每一次 MOVE** 都是 `WORLD_NOT_INITIALIZED` |
| `components/world/service/world_body_gateway_api.py` | 新增 `_reachable_move_candidates()`；`get_available_actions()` 的 MOVE 条目增加 `candidates` | 可达地点取自世界自己的位置目录（`world_living_skeleton.location_catalog()`），不由网关或模型编造 |
| `components/world/service/action_intent.py` + `bridge/action_intent.py` | `build_action_space()` 把 MOVE 候选投影成 `move:<place>`，params 用 Resolver 自己的参数名 | 两份副本保持逐字节一致 |
| `components/life/scripts/world_action_bridge_adapter.py`（新） | `WorldBridgeResolver` + `RealWorldActionAdapter` | AR-0 的适配器槽位原本接 `RefusingWorldResolver`；这是第一个真实实现 |
| `components/life/scripts/world_move_opportunity_source.py`（新） | 观测 → `ObservedWorldOpportunity` 的翻译 | AG-0 的 `WorldOpportunityCandidateSource` 只能被外部喂；此前全仓无人喂过 |
| `components/life/scripts/autonomous_life_cycle.py` | `world_opportunity_provider` 注入；World 步（决策绑定）与 World 采用规格 | 让自主轮次自己走 World 路径，不新建平行编排器 |
| `components/life/scripts/life_integration_ag2.py` | `step_spec_override` 可以是「已提交 Decision 的可调用」 | MOVE 的形状取决于选了哪个候选；Mapping 不是 callable，既有契约不变 |
| `components/life/scripts/life_runtime_production.py` | 可选注入 `world_action_adapter` | 默认仍是 fail-closed 的 `RefusingWorldResolver` |
| `components/world/service/test_world_move_candidates.py`（新） | 8 项 B6-1/B6-3 回归 | 进 `world` 套件，长期覆盖 |
| `components/life/tests/v02/run_world_move_e2e.sh` + `world_move_e2e.py`（新） | 隔离实例 + 真实闭环验收 | 见 §4 |
| `components/check_life_v02.py` | 新增 [11][12] 两节（B5-2） | 见 §5 |
| `components/life/tests/v02/run_model_e2e.py` | 新增 `ab` 模式（B5-1） | 见 §5 |

### 边界（刻意不做的事）

- Life **不签发** World authority：网关拒绝调用方自带的 `authority`/`confirm_apply`，服务自己盖章。
- 不直接写 `world_state.json`：Resolver 是唯一提交者。
- 不开 socket：传输由 `WorldBodyClient` 拥有，只连既有的可写 `gateway.sock`；只读 `read.sock` 保持只读。
- 不把 `bridge_init.register()` 当作 Life 必经步骤：那是聊天插件 LLM 工具的接线。
- 不新建第二套 World Action Ledger：AR-0 的 store 仍是 Life 侧唯一账本，World 的 resolver ledger 仍是唯一世界真相。
- `expected_dependencies` 不进入 Resolver 提案：桥的冻结规则是依赖证据由**权限侧**在提交时现读。

## 3. B6-1 时区修复：修前 / 修后（真实结果）

| | 修前 | 修后 |
|---|---|---|
| 新建隔离实例的 `world_state.timezone` | `Asia/Shanghai` | `Asia/Tokyo` |
| `world_living_skeleton.plan_move_to("nearby_street")` | `status=unavailable`, `reason_code=WORLD_NOT_INITIALIZED` | `status=ready`, `reason_code=REACHABLE` |
| `observe_world()` | 无 `current_location` / `reachable_locations` | `home` + `["nearby_street"]` |
| 服务启动 | 能起，但 MOVE 永远被拒 | READY，MOVE 可提交 |

历史实例未批量改写（按裁定）；错误时区仍 fail-closed 被拒，这条有测试钉住。

## 4. B6-5 / B6-6 验收（`components/life/tests/v02/run_world_move_e2e.sh`）

隔离实例、两个真实非 root 身份之外的**同一 uid**：World 服务与 Life 客户端都以 `nyasvc` 运行，
因为网关只放行服务进程自己的 euid（`GATEWAY_ALLOWED_PEER_UID = os.geteuid()`）。**没有**放宽
socket 权限，也**没有**取消认证。B6-4 的六个开关（`BODY_RUNTIME_MODE=canonical`、
`CHIYO_WORLD_BODY_AUTHORITY=production`、`CHIYO_BODY_READER_MODE=canonical`、
`CHIYO_BODY_RUNTIME_ENABLED=1`、`CHIYO_WORLD_BODY_SUBSTRATE_ENABLED=1`、
`CHIYO_WORLD_LIVING_SKELETON_ENABLED=1`）在 `env.sh` 里显式打开。

`WORLD_MOVE_ACCEPTANCE: PASS`，逐项：

1. **真实合法决策触发 MOVE**：`step_path=WORLD_AR0`、`decision=START`、`adoption_status=APPLIED`、
   `action_status=SUCCEEDED`、`outcome=ACTED`，回执带 World-owner 证据。
2. **World owner 把位置从 `home` 改成 `nearby_street`**：`world_state.location.place_id` 实测
   `nearby_street`，`revision` 1 → 2。
3. **再次合法观察读到新位置**：重启前 `get_snapshot()` 报 `nearby_street`，且街道的真实邻居
   （含回家的路）成为新候选。
4. **重启后位置保持**：停掉再起 World 服务，`world_state` 与网关快照都仍是 `nearby_street`。
5. **不可达动作被拒，不虚报进度**：`home → school` 经真实链路被 `DEFINITE_PROVIDER_REJECTION`
   拒绝，`world_state` 与 revision 完全不变，世界自己的账本也不声称成功。
6. **UNKNOWN 用原 execution_id 查真实结果**：注入「提交答案丢失 + 立刻查询也失败」后，AR-0
   动作进入 UNKNOWN、**没有任何进度被声称**；世界其实已经提交（revision +1）。随后用**原
   execution_id** 查询，世界账本答 `SUCCESS`，AR-0 据此把 UNKNOWN 解决为 SUCCEEDED，且**没有
   第二次移动**。同一意图重放复用原 execution_id，revision 不变。

**B6-6 位置对照**：`home` 下合法 MOVE 候选只有 `nearby_street`；`nearby_street` 下是
`home / school / convenience_store / park / home_goods_store`。AG-0 实际 materialize 出的候选
与世界的可达集一致——差异来自真实世界状态，不是模型编造的地点。

## 5. B5 尾项

### B5-1 真实模型 A/B（`run_model_e2e.py ab`）

同一作品、**同一 head**、**同一候选集**（两半各用独立 Life state root，否则前一半的采纳会留下
foreground Activity 从而改变候选集——这是第一轮发现的混淆，已修正）。同一份受控经历只出现在 A。

- A：`decision=START`，`reason_codes=["WORLD_OPPORTUNITY_OBSERVED"]`，请求里带 `lexp:ab:1`
- B：`decision=START`，`reason_codes=["WORLD_OPPORTUNITY_OBSERVED"]`，请求里没有经历
- **决策无差异**——如实记录，不强迫模型作出不同选择。
- 内容生成半边（同一 resume point）：经历只进 A 的提示词，A/B 产出的文本**不同**。

调用：第一次 `ab` 4 次 + 修正后 `ab --skip-content` 2 次 = **6 次**，正好等于本批上限。

### B5-2 中断与恢复（`check_life_v02.py` [11][12]，真实 Supply owner）

1. **作品已提交 + Memory 写入失败**：轮次仍然 `ACTED`，作品版本号 +1（**不回滚**），canonical
   Activity 仍 `ACTIVE`，经历保持 pending 等确定性重试，且这是 disposition 不是 error。
2. **handoff 持久化后中断**：重启（新 runtime，同一 state root）后
   `dispatch_experience_handoffs()` 把它投递出去，pending 清零，M0 里**只有一行**对应证据。

`CORRECT` 的既有约束保留：不伪造用户消息；原生生活经历的纠正功能仍作为独立缺口记录。

## 6. 回归

| 套件 | 结果 |
|---|---|
| `world`（含新增 8 项） | 基线 251 OK / 改动后 **259 OK** |
| `closeout` `native` `memory` `supply` `life` `life-memory-v02` | `FAILED_SUITES: none` |
| 隔离验收 `check_life_v02.py`（真实 Supply owner） | `V02_CHECK: PASS` / `ISOLATED ACCEPTANCE: PASS` |
| `check_life_memory_v02.py` | `MEMORY_CHECK: PASS`（8 节） |
| `memory_runtime_v1` | 11 OK |

## 7. NOT_RUN / 剩余欠账

- **未跑**：任何线上生产 World 实例。本次全部在 `/srv/nya02-world` 隔离实例内完成。
- **未跑**：`WORLD_BODY_MUTATION_MODE`（LLM 工具暴露）——它是聊天插件的开关，不是 intent 链的；
  本次没有打开，Hermes 的通用 World mutation tool 保持关闭。
- **未做**：历史（已存在的）World 实例的时区兼容迁移。按裁定只修新建隔离实例。
- **未做**：`POSE` / `PICK_UP` / `PLACE` 经 AR-0 的真实映射。AR-0 只带一个 `target`，而
  Resolver 的 POSE 需要 `pose`+`orientation` 两个参数，无法在不编造的前提下表达，因此适配器
  对 POSE 明确返回「提交前失败」而不是猜。
- **未做**：把 World 候选接到「基于位置可及性的活动机会」（总纲允许但不要求）。
- **B6-2 的既有缺口**：`components/world/service/bridge/` 仍无 `__init__.py`。本次不依赖包导入
  （适配器按目录加入 `sys.path`），所以不需要改它；但如果将来要把它当包用，这是一个独立问题。
- **未跑**：`v0.2 B6` 的独立第二意见评审。
