# F 包回执：最小正规 CONSUME —— 一份物件、一条 owner 回执、一次合法后果

- 工作区：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），branch `work/v03-w01-navigation`，HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）
- 状态：**UNCOMMITTED**。未 reset / clean / checkout / recreate / stage / commit / push；W01–R1c 与 A/B/C/D 的未提交改动原样保留。
- 写集：只动本包独占文件（见 §1）。未碰 `world_living_skeleton.py`、`world_daily_opportunities.py`、`daily_neighborhood_v1` 场景包、任何 Life cycle/assembly/resident/one_day_life/旅程 ledger/Memory/Body Runtime 输入类与变量、economy strict schema、看板、他人笔记。
- 未做：真实模型、外网、真实支付/余额/消费、生产部署/profile/凭据/provider、24h、删除任何用户文件、commit/push、子委派。

---

## 1. owned delta

| 文件 | 改动（行号为当前文件） |
|---|---|
| `components/world/scripts/world_consumable_contract.py` | **新增**（491 行）。World-owned 消费合同：`consumption_verdict()`（纯判定）、`load_consumable_declarations()`（只读 `state/world_object_capabilities.json#consumables`）、`build_receipt()` / `verify_receipt()`（回执字段唯一真源）、`place_open_state()`（只读 `world_daily_rhythm`）。无 store、无 scheduler、import 无副作用。 |
| `components/world/scripts/world_body_substrate.py` | schema `v1.1 → v1.2`（55）；`MAX_CONSUMPTIONS=256`（74）；`validate_consumption_receipt()`（232）；`consumptions` 进 `_STATE_KEYS` 与 `validate_substrate()`（113/318-333）；`migrate_substrate()` v1/v1.1 → v1.2 只补空 `consumptions`，placement 原样带过（372-408）；`initial_substrate()` 加空块（451-465）；`consumption_receipt()` / `receipts_for_object()` / `consume_object()`（720-800，**一次状态变更同时删 placement 并落回执**）；`read_model()` 增加 `consumption_count`（917）。 |
| `components/world/scripts/world_action_resolver.py` | `ACTIONS` 加 `CONSUME`（166）；新增 `CODE_NOT_CONSUMABLE` 及 provenance（76、145-149）；`_CONSUME_VERDICT_CODES`（523）；可选 `now=` 时钟（530-548）；dispatch（704）；`_consume_replay()`（1409，回执校验的幂等/错载荷拒绝）；`_resolve_consume()`（1443，gate → 新鲜度 → 回执/placement → 声明/经济/姿态/营业 → prepared → 一次原子变更 → **回执校验后置条件** → committed）；`reconcile()` 增 `consumption_receipt` + `_consumption_receipt_for()`（1681）。 |
| `components/world/scripts/body_consequence.py` | `SUPPORTED_ACTIONS += CONSUME`（56）；`CONSUME_OPERATION_SECONDS = 2.0` 与 `CONSUME_LOAD_NOTE`（83-89）；`PICK_UP/PLACE/CONSUME` 共用 2 s light `PHYSICAL_RESULT`，CONSUME 记 `resource_ref` 与显式「非营养」note（226-242）；docstring 增「No intake class」（29-35）。无新输入类、无变量。 |
| `components/world/service/world_body_service.py` | Resolver 注入 `now=self._moment`（130-136）；`_live_substrate()`（324）；`reconcile_now()` 传 `substrate=`（353）；`act()` / `apply_result()` 把结果里的 `object_id` 传进后果（410、443）。 |
| `components/world/service/world_body_reconcile.py` | 新增 `consume_evidence()`（77，execution+payload+subject+物理结果四项对齐）；`reconcile_startup(..., substrate=None)`（143）；CONSUME 执行必须先过回执证据，否则记 `unresolved`（`reasons`）且**不给 Body**（166-193）；heal 时把 receipt 的 `object_id` 传给后果（252）。 |
| `components/world/service/world_body_gateway_api.py` | `_economy_document()`（304）、`_consume_availability()`（315，只读投影：live placement + 声明块 + 营业状态，不写状态/不发 ticket/不碰 Body）；`get_available_actions()` 追加 CONSUME 候选（640-660）；`get_action_result()` 增 `consumption_receipt` / `receipt_present`（684-691）；模块 docstring 增消费入口说明（28-35）。 |
| `tests/test_world_consume.py` | **新增** 20 例：成功 1 份耗尽、网关/服务路径重放不重复、第二个 execution 拒绝、候选投影增减、持有食物、闭店/未声明/不可食/不免费/远处/姿态/经济物件/无授权/错 verb 授权/错参数授权/陈旧依赖/无可消费候选 的拒绝。 |
| `tests/test_world_consume_faults.py` | **新增** 6 例：prepared 前后与 after-mutation-before-commit 两个崩溃窗口、Crash-A 补偿一次并重启回读、**无回执时「物件不见了」不归因**、同一 execution 换 payload 拒绝、v1.1 平台迁移后仍可消费。 |
| `docs/engineering/world-daily-f-result.md` | 本回执。 |
| `.agents/notes/implemented/architecture/2026-10-17-world-consume-placement-receipt.md`、`.agents/notes/implemented/feature/2026-10-17-world-consume-non-nutrition-body.md` | 本包专属决策笔记（`world-consume*`）。 |

`world_action_authorization.py` / `service/bridge/world_object_capability.py` **未改**：一次性授权本来就是 `verb + args_digest + execution_id + actor + decision` 绑定，`{"object_id": ...}` 的 CONSUME 直接复用（错 verb → `VERB_MISMATCH`，错参数 → `ARGS_MISMATCH`，均已实测）；M15 六键 `capability_of()` 未被消费声明污染（声明走 `consumables` 独立块），故无需扩。

## 2. 迁移与向后兼容

- 唯一 schema 升级点仍是 `migrate_substrate()`：`world.body.substrate.v1` / `v1.1` → `v1.2`，只新增 `consumptions: {}`；`placements` / `reservations` / `body_id` / `world_entity_id` 一律原样带过，不重算。
- 读入过程**不改写**磁盘文件：`load_with_migration()` 纯读（测试断言读完后文件仍是 `v1.1`）；第一次真正的 owner 写入才落 `v1.2`。
- 旧 home 没有回执即「没有消费记录」——不补历史、不猜、不回填。未知 schema 仍 fail closed。
- 旧 placement 结构一字未改；`validate_substrate` 仍要求精确字段集，因此坏文件/多字段文件照旧拒绝。

## 3. 正规入口（供 I 接线）

唯一正式写路径仍是 **gateway.act → service.act → resolver.resolve**（不新增旁路，也不新增 consumer 专用入口）：

- 提交：`GatewaySurface.submit_action({action:"CONSUME", execution_id, params:{object_id}, authorization_id, decision_ref?})`；授权的 verb/args 由 Intent Executor 侧 `AuthorizationRegistry.issue(..., verb="CONSUME", params={"object_id": ...})` 发放。
- 候选/读：`GatewaySurface.get_available_actions()["actions"]` 里的 `CONSUME` 条目（下方真实样例）；`get_action_result(execution_id)` 回读 verdict + 回执。
- 进程内（服务自己 / 测试）：`WorldBodyService.act(proposal)`；纯 resolver：`WorldResolver.resolve(proposal)`（`{"action":"CONSUME","params":{"object_id":...}}`）。
- 传输层：`components/native/integration/world_body_client.py` 是 verb 无关透传，无需改；I 侧只需把 `CONSUME` 当作和 `PICK_UP` 同级的 verb 提交。
- import：`components/world/scripts/world_consumable_contract.py`（判定/回执/声明读取）、`world_body_substrate.py:720-800`（回执与原子变更）。**I 不需要自己判「能不能吃」**：候选列表已经是 owner 判定结果，resolver 在提交时重读同一事实。

真实候选样例（`tests` 同款 home，`python /…/f_consume_sample.py`，exit 0）：

```json
{
 "available": true, "blocked_reason": null, "verb": "CONSUME",
 "params": {"object_id": "string"},
 "requires": ["world.location", "body.revision", "body.pose"],
 "candidates": [{"object_id": "f-food-onigiri-01", "label": "f-food-onigiri-01",
                 "source": "place_supply", "placement_kind": "room"}],
 "contract": {"one_instance_per_consumption": true,
              "declaration": "state/world_object_capabilities.json#consumables (world-owned; consumable + free_to_consume)",
              "placement_is_live_source": true,
              "held_food_ignores_opening_hours": true,
              "body_effect": "consume_operation_load_model:2.0s manipulation only; no nutrition, satiety, hunger or health"}
}
```

## 4. 回执字段 / UNKNOWN 查询 / Body once / 无营养声明

**同一次原子保存**（`consume_object()` 返回一个 state，`SubstrateStore.save()` 写一次）里同时：该 instance 的 placement 消失 + `consumptions[execution_id]` 落一条回执。

真实回执（同一 home，`get_action_result` 里字段一致）：

```json
{"kind": "world_consumption_receipt", "schema_version": "world.consumption.receipt.v1",
 "receipt_id": "world-consumption:d22c1824dc4ec6fd5d4c98ba55b9a8d3",
 "execution_id": "world-exec:f-sample-01", "object_id": "f-food-onigiri-01",
 "body_id": "chiyo_body", "actor_id": "chiyo",
 "proposal_fingerprint": "sha256:3a69dd…",
 "before_revision": 1, "consumed_at": "2026-09-27T12:00:00+00:00",
 "source": "place_supply",
 "placement_before": {"kind": "room", "location_id": "convenience_store", "area_id": "convenience_store"}}
```

- **归因规则**：`reconcile` 只认「execution_id + payload（ledger 自己 committed 的 `placement.<id>=consumed` 引用）+ subject（body_id）」四项都对齐的 owner 回执，且要求 placement 确实已不在；缺回执 → `unresolved` + `reasons:["receipt_missing"]`，Body 一步都不动（`tests/test_world_consume_faults.py:test_04`：ledger 说 SUCCESS、物件确实没了、回执被拿掉 → 不归因、不补 Body）。
- **UNKNOWN 查询**：`get_action_result()` 同时给 `verdict:"UNKNOWN"` 与 `receipt_present:true` + 回执本体（prepared / after-mutation 崩溃窗口），因此「还没判定」与「东西真没了」不会被混为一谈；resolver 的 `reconcile()` 也带同一字段。
- **幂等**：同 execution 重放 → `already_applied`（服务路径实测）或网关 `NOT_EXPOSED`（候选已空，单据也一次性），两种都不会二次扣食物；同 execution 换 `object_id` → `INVALID_PROPOSAL` + `EXECUTION_PAYLOAD_MISMATCH`，不报 ALREADY_APPLIED；不同 execution 再吃同一份 → `NOT_ALLOWED: ALREADY_CONSUMED …`。
- **Body once**：后果仍是既有 `body-consequence:<execution_id/action/body>` 去重；本研究里 `2.0 s light` 的 `PHYSICAL_RESULT` 常常真的产生一次有界平移（实测 `body_version 0→1`，`body_transition_ref` 一条），**但 no-change 也合法**——判定标准是「该 execution 的 consequence 记录恰好一条 + 至多一条 transition ref」，不是「数值必须变」。
- **无营养声明**：`inputs` 只有一个 `PHYSICAL_RESULT`，`notes` 是 `consume_operation_load_model:2.0s manipulation only; not nutrition, not satiety, not hunger, not health, not recovery`；无 `INTAKE` 类、无 hunger/nutrition/satiety 变量、不映射 fatigue 恢复/comfort 奖励、不开 RECOVERY。`tests/test_world_consume.py:test_02` 断言除该免责 note 外记录里不出现这些词。
- **不复活**：吃完后候选列表空、placement 无；静态 `consumables` 声明仍在，但只是「可食声明」，不是剩余份量（`test_06`）。
- **营业条件**：只约束「场地供给」（非持有）。持有食物在原店闭店时照吃（`test_08`）；场地供给在闭店时 `NOT_ALLOWED: LOCATION_CLOSED`（`test_15`），并按 `world_daily_rhythm.location_availability` 在提交时重读。MOVE 语义未改（可以走到闭店门口）。
- **经济物件**：`world_property_economy` 里有 instance 的一律 `NOT_CONSUMABLE: ECONOMY_OBJECT`，即使声明 `consumable/free_to_consume` 都是 true、价格是 0（`test_16`）——本版没有支付/所有权消费权威，不把价格当免费。
- **回执上界**：`MAX_CONSUMPTIONS=256`；到上限**拒绝新的消费**（`LIMIT: … never pruned`），不清旧回执，不到上限不乱裁。

## 5. 测试命令与真实 exit

venv：`/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`，一律 `PYTHONDONTWRITEBYTECODE=1`，repo cwd，`unittest`（该 venv 无 pytest）。

| 命令 | exit | 结果 |
|---|---|---|
| `python -m unittest tests.test_world_consume` | **0** | `Ran 20 tests … OK` |
| `python -m unittest tests.test_world_consume_faults` | **0** | `Ran 6 tests … OK` |
| `cd components/world/service && PYTHONPATH=.:../scripts python -m unittest test_object_action_m15 test_world_move_candidates test_world_object_capability test_world_body_service test_world_body_bridge test_world_action_authorization test_authorization_hardening test_world_action_lifecycle` | **0** | `Ran 155 tests … OK` |

现场：全部用真实本地 `WorldBodyService`（真单一写者 lease）+ 真 gateway 授权注册表 + 真 substrate 文件 + 真 execution ledger + 真 Body runtime/consequence store；每个用例一个唯一 home，落在原生 `/tmp/nyaw05r.jdK3q/f-daily-*`，**创建即保留**（不 unlink、不 rm、不 rmtree、不重建固定根），本轮累计 126 个（含样例 home）。没有删除、没有整仓 copy、没有 socket 复用（全部进程内真实对象；socket/桥接相关既有用例在 155 例内自起自停）。

覆盖的正/负/恢复（对应 §1 的两个测试文件）：正例 = 1 份耗尽 + 回执 + Body 一次；负例 = 未声明/不可食/不免费/远处/姿态/闭店/经济物件/无授权/错 verb/错参数/陈旧依赖/无可消费候选，全部断言 substrate 字节不变且无 Body 记录；恢复 = prepared 前后、after-mutation-before-commit、Crash-A 补偿一次并重启回读、回执缺失不归因。

## 6. 已做 vs 未做（Life 接线不在本包）

已做：正规 verb/授权/新鲜度/ledger/崩溃窗口、一份 instance 耗尽、同原子保存的回执、按回执的归因与对账查询、Body 一次且非营养、候选/读口、向后兼容迁移、专项测试与回执。

未做（按分工留给 I）：Life 侧的机会/决策/旅程接线、把 `world_daily_rhythm` 之外的 W 新机会口（`location_opportunity_catalog` / `reachable_opportunities`）并入候选、回家/私人事项/经历接续、一次同实例连续演练。

已知限制（**未顺手改**）：

1. 网关 `submit_action` 的暴露策略对所有 verb 一致：消费完成后候选为空，重放会被判 `NOT_EXPOSED`（零变更），要读结果请用 `get_action_result`；这是既有 surface 语义，不在本包改动。
2. `world_body_reconcile` 的 Crash-A 补偿路径沿用既有行为：保存 runtime 并返回 transition id，但不写 `transitions.jsonl`（这是本包之前就存在的缺口，对所有 verb 一致）；因此本包只断言「consequence 一条 + transition ref 至多一条」，不声称该 ref 一定已落盘。
3. 回执不建立份量/库存模型：一个 instance 就是一份，吃完即耗尽，不补货；上界满则 fail closed。
4. 免费供给只看「当前位置 + 该 instance 的世界声明 `free_to_consume`」，没有引入地点级免费清单；W 的机会目录进来后若要收紧，应在 `world_consumable_contract.consumption_verdict` 单点扩展。
5. 本包未起真实 UDS 服务进程做跨进程演练（155 例中既有 socket/桥接用例未改），跨进程行为与既有 verb 同构。

---

## 7. 更正 r1：经济事实不可读必须 fail closed（父级探针复现的本包缺陷）

### 7.1 缺陷与复现

父级用本包的真实 `ConsumeBase` 复现：`setUp` 之后把**同一 home** 的 `state/world_property_economy.json` 写成截断 JSON `{"items":`，此时

```text
CORRUPT_ECONOMY_CANDIDATES= … "available": true, "candidates": [{"object_id": "f-food-onigiri-01", …}]
CORRUPT_ECONOMY_RESULT= … "status": "applied", "reason_code": "OK", "applied_to_body": true
PARENT_F_EXIT=1
```

即：候选照常给出，`submit CONSUME` 真的消费并写了 Body，substrate 被改。旧失败现场保留在 `/tmp/nyaw05r.jdK3q/f-daily-consume-2e43a98fe82c`，父级日志 `/tmp/nyaw05r.jdK3q/parent-world-daily-f-first.log` 未动。

### 7.2 根因

两条路径都把「读不到」洗成了「没有经济」：

- `world_body_gateway_api._economy_document()`：`except Exception: return {}` → `economy_instances({})` 为空 → 「不是经济物件」成立；
- `world_action_resolver._resolve_consume()`：`world_body_binding.load_economy()` 的 `_read_json` 把 `OSError`/`ValueError` 统一 `return None` → 同样当成空经济。

### 7.3 修复（单点、只读、有界、复用既有 validator）

`world_consumable_contract.economy_facts(hermes_home)` 返回三态，网关与 resolver 都只调用它：

| status | 判定条件 | 本次 CONSUME |
|---|---|---|
| `absent` | `os.stat` → `FileNotFoundError` / `NotADirectoryError` | 没有经济 owner，「非经济」可证 → **保持既有行为** |
| `present` | 文件存在且 `world_property_economy.validate_state()` 接受 | 只有这里才可能出现 `ECONOMY_OBJECT` |
| `unreadable` | 其他 `OSError`（权限等）、非普通文件、`st_size > MAX_ECONOMY_BYTES(4 MiB)`、读中超界、非 UTF-8、JSON 损坏、root 非对象、schema 拒绝 | `ECONOMY_FACTS_UNAVAILABLE` → **拒绝**，候选与提交都拒，substrate/回执/Body 不变 |

配套：

- 网关 `_economy_document()` → `_economy_facts()`；坏经济时 `available=False`、`blocked_reason="ECONOMY_FACTS_UNAVAILABLE"`、`detail` 写明原因、`candidates=[]`，且该码是候选阻塞原因的最高优先级。
- resolver 用 `contract.economy_facts(self.hermes_home)`；`consumption_verdict` 在**声明之前**先判经济事实（`economy_view()` 归一化：facts/映射/`None`，其他一律 unreadable），`_CONSUME_VERDICT_CODES` 增 `ECONOMY_FACTS_UNAVAILABLE → NOT_CONSUMABLE`（`legacy_reason_code` 前缀可分辨）。
- 既有的 `world_property_economy.validate_state()` 被直接复用；**未**改 economy strict schema、**未**改 `world_property_economy`/`world_body_binding`、**未**引入第二份经济真相。
- 持有时不豁免：手里的食物同样要过这条判定（否则「先拾起再吃」就是后门）。
- 范围只到 CONSUME；MOVE/PICK_UP/PLACE/POSE 与经济自身读写未改。

### 7.4 修复后的真实输出（父级探针，现成负控转正）

```text
test_corrupt_economy_cannot_authorize_consumption … ok
PARENT_F_EXIT=0
CORRUPT_ECONOMY_CANDIDATES= … "available": false, "blocked_reason": "ECONOMY_FACTS_UNAVAILABLE",
                              "detail": "economy state is corrupt JSON", "candidates": []
CORRUPT_ECONOMY_RESULT= … "status": "rejected", "reason_code": "NOT_EXPOSED",
                          "world_unchanged": true, "body_unchanged": true
```

### 7.5 修改文件与命令（真实 exit）

| 文件 | 改动 |
|---|---|
| `components/world/scripts/world_consumable_contract.py` | 新增 `ECONOMY_RELATIVE` / `MAX_ECONOMY_BYTES` / `ECONOMY_ABSENT|PRESENT|UNREADABLE` / `ECONOMY_FACTS_UNAVAILABLE` / `economy_path()` / `economy_facts()`（有界只读三态）/ `economy_view()`；`consumption_verdict()` 在声明前判经济事实，经济物件判定改为只在 `present` 时进行 |
| `components/world/scripts/world_action_resolver.py` | `_resolve_consume()` 用 `economy_facts()` 取代 `load_economy()`+`except→None`；`_CONSUME_VERDICT_CODES` 增 `ECONOMY_FACTS_UNAVAILABLE`；`cross_authority_refs` 只在 `present` 时计算 |
| `components/world/service/world_body_gateway_api.py` | `_economy_document()` → `_economy_facts()`；`_consume_availability()` 坏经济直接返回 `ECONOMY_FACTS_UNAVAILABLE`（不与 `{}` 混同）；候选阻塞原因优先级加入该码 |
| `tests/test_world_consume.py` | `valid_economy()`（由经济 owner 自己的 `_empty_state` + `validate_state` 构建，保证「合法经济」夹具不会悄悄变成非法）；`test_16` 改用合法经济；新增 `ConsumeEconomyFactsTest`（test_30–test_40） |
| `tests/test_world_consume_faults.py` | 新增 test_07：坏经济拒绝且零残留，owner 文件修好后同一 instance 仍能恰好消费一次 |
| `.agents/notes/implemented/architecture/2026-10-17-world-consume-economy-facts-failclosed.md` | 本决策笔记 |

```text
$ python -m unittest tests.test_world_consume tests.test_world_consume_faults      → exit 0  (Ran 38 … OK)
$ cd components/world/service && PYTHONPATH=.:../scripts \
  python -m unittest test_object_action_m15 test_world_body_service test_world_move_candidates
                                                                                   → exit 0  (Ran 67 … OK)
$ python .../parent_world_daily_f.py                                               → exit 0  (PARENT_F_EXIT=0, Ran 3 … OK)
$ node --experimental-strip-types …/verify-agent-note-tree.ts                      → exit 0
$ node --experimental-strip-types …/verify-agent-note-format.ts                    → exit 0
```

### 7.6 新增回归与「能捕捉旧行为」的证据

- `test_30`（截断 `{"items":`）就是父级探针的 owned 版本；旧实现下它必然失败（候选 true + `applied`），新实现下通过。
- `test_40` 直接打 `economy_facts()`：`absent`（`economy=None`、`reason_code=None`）/ `unreadable`（`reason_code=ECONOMY_FACTS_UNAVAILABLE`、`detail` 含 `corrupt JSON`）/ `present`（`economy["items"] == []`）三态各有断言——若有人把 `except: {}` 加回来，这个用例立刻红。
- 定向覆盖：非对象 root（`[]`/`12`/`"economy"`/`null`）、合法 JSON 但结构非法、`os.stat` 抛 `PermissionError`、路径是目录、超 `MAX_ECONOMY_BYTES`、持有食物 + 坏经济。
- 行为不变的两侧也各有一例：`test_38`（文件真的不存在 → 照旧 `applied`）、`test_39`（合法空经济 → 照旧 `applied` + 一条 Body 后果）。
- 负控沿用父级现存失败日志（上面 7.1 的原文），未整仓 copy、未 checkout。

### 7.7 未变更数据与范围声明

- 未改动任何既有 world/body/economy 数据文件；测试全部在各自唯一 home（`/tmp/nyaw05r.jdK3q/f-daily-*`，创建即保留）里进行，本轮未删除任何文件（test_07 的「修复」是把合法空经济**写回**同一个夹具文件），未 unlink / rm / rmtree / 整仓 copy。
- 未改：`world_property_economy.py`（strict schema 与 `load_state` 语义原样）、`world_body_binding.py`、Life / W / 看板 / 他人笔记；未 commit/push，HEAD 仍 `7f09d26df64e054faf51ba35ad85c64f73a5a837`，全部 **UNCOMMITTED**。
- 残留（如实记录）：`_labels()` 仍对同一文件 `except → {}`（纯展示，不参与许可）；`world_object_capabilities.json` 声明块损坏仍按「无声明 / `NOT_DECLARED`」拒绝（本包未改，方向同为 fail closed）。
