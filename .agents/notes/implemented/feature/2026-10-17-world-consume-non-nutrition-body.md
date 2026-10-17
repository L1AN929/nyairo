# Agent Note: CONSUME 是正规 verb：一份食物 = 一个物件 instance，Body 只记动作负载、不造营养

Status: implemented

## Problem

「在虚拟世界里吃一份东西」在这套系统里有三个容易走偏的方向：

1. **当成库存/经济问题**。一旦引入 servings（一份能吃几口）、余额或价格，就必须有支付与所有权消费的 owner；没有这些 owner 时，任何「免费」判断都是编的。真实世界里「价格是 0」也不等于「free_to_consume」。
2. **当成生理问题**。Body 现在只有 `fatigue / activation / comfort / sensory_load / tension / warmth` 与 `ENVIRONMENT / PHYSICAL_RESULT / RECOVERY` 三类输入，没有 `INTAKE`、没有饥饿、没有营养。把「吃」硬映射成疲劳恢复或舒适奖励，等于凭空造一套第二身体；反过来因为不能造营养就干脆什么都不做，又等于承认这个动作没有任何身体事实。
3. **当成旁路**。给 Life 或场景层开一个「直接吃」的入口，会绕开唯一提交权威（Resolver）与一次性授权，于是既没有执行身份、没有新鲜度检查，也无法重放对账。

在动手之前，仓库里已有一篇笔记写着「Resolver 支持进食与消耗道具」，而当时的 `ACTIONS` 只有 `MOVE / PICK_UP / PLACE / POSE` —— 那句话是意向而不是事实。这篇笔记记录的是把它落成事实时做出的取舍（旧笔记本身按规矩不改写）。

## Decision

`CONSUME {object_id}` 是 Resolver 的正规 verb（`ACTIONS = (…, "CONSUME")`），走与 `PICK_UP / PLACE` 完全相同的骨架：`_substrate_gate`（feature / substrate / world binding）→ 参数规范化 → **回执校验的重放门** → 依赖新鲜度 → 世界声明与实时 placement → `prepared` → 一次原子 owner 变更 → `committed`。网关是唯一正式写路径：`submit_action` 依旧要求一次性授权（verb + args digest 绑定），`get_available_actions` 追加 `CONSUME` 候选；候选的构建是纯读（live placement + 声明块 + 营业状态），不写状态、不发 ticket、不碰 Body。

语义边界被压到最小、且都落在已有的 owner 上：

- **一份 = 一个物件 instance。** 没有 servings、没有余额、没有价格。吃完这个 instance 就消失，不补货、不从静态声明复活（声明只表示「可食/可免费取用」，不是剩余份量）。
- **可食是 World 的声明，不是推断。** `state/world_object_capabilities.json` 里新增独立的 `consumables` 块（`{"consumable": true, "free_to_consume": true}`）。缺失 / 未知 / 不是映射 / 值为假 / 只 `free` 不可食 → 全部 fail closed。M15 的六键 `capability_of()` 一字未动，`takeable` 不等于 `consumable`（一把可以拿起的椅子仍然不能吃）。
- **持有 或 当前地点供给，二选一。** 手里拿着的那份，闭店也照样吃（不因原店关门被拒）；摆在场地的免费供给则必须「在当前地点 + 可接近 + 该地点按 `world_daily_rhythm.location_availability` 此刻是 OPEN」，否则 `NOT_ALLOWED: LOCATION_CLOSED`。MOVE 语义没动：可以走到闭店门口，只是进不去吃不到。
- **经济物件一律拒。** 只要 instance 出现在 `world_property_economy`（任何 disposition、任何价格），就 `NOT_CONSUMABLE: ECONOMY_OBJECT`。本版没有支付/所有权消费权威，所以宁可拒绝，也不把价格当免费。
- **取得 ≠ 消费。** `PICK_UP` 只改 placement；真正吃掉是另一条 `CONSUME` execution，有自己的身份与回执。
- **Body 只有动作本身。** `body_consequence` 把 `CONSUME` 归到与 `PICK_UP / PLACE` 同级的 `PHYSICAL_RESULT`：`movement_duration_seconds = 2.0`、`effort = light`，并显式记录 `consume_operation_load_model:2.0s manipulation only; not nutrition, not satiety, not hunger, not health, not recovery`。没有新输入类、没有新变量、不映射疲劳恢复或舒适奖励、不开 RECOVERY。没有实测时长时，2 秒是**版本化最小动作假设**（与 `PICK_UP/PLACE` 同量级的手部动作），不是「坐着吃完 120 秒」，也不伪造时间流逝。
- **一次执行一次后果。** 复用既有 `body-consequence:<execution_id/action/body_id>` 身份与 `ConsequenceLedger` 去重：同一 execution 至多一条 consequence；`no-change` 也合法（判据是「记录恰好一条、transition ref 至多一条」，不是「数值必须变」）。

## Alternatives considered

- **先加 `servings`/`remaining_portions` 或直接建一个食物库存表**：这是最「完整」的模型，也是今后做多口吃、买多份所必需的。但现在没有任何支付或所有权消费 owner，份量从哪来只能编（「一盒饼干 8 片」这种数字没有来源）；而份量一旦落盘，就必须同时解决「吃了半份之后 placement 表示什么」。否决：在没有 owner 的地方造库存，等于给未来的经济模型埋一个假真相。等到真有支付/所有权消费时再按 Note `2026-10-17-world-consume-placement-receipt` 的升级触发条件扩字段。
- **给 Body 加 `hunger` / `INTAKE`，让吃有意义**：这会让「吃饭」看起来真的重要，也是许多陪伴类产品走的路。但它与既有铁律冲突：不做强制人类式吃睡打卡、不用负面状态惩罚制造真实性；而且加一个没有生理来源的 hunger，就必然要配一套「不吃会怎样」的惩罚规则，正是被明确否掉的那套机制。否决：本版不声称营养。
- **把吃折成 `RECOVERY`（补体力）**：比加 hunger 温和，且不需要新变量，看起来「有后果」。但 `RECOVERY` 在 `body_runtime` 里的语义是一段真实恢复过程（时长、趋势、上限），用它来表达「吃了一块饭团」既没有时长来源，也会把虚拟身体变成「吃饭回血」的游戏化装置。否决。
- **把 `consumable` 塞进 M15 的六键 capability 块**（例如 `capability.consumable = true`）：复用现成 join，改动最小。但 `world_object_capability.capability_of()` 的六个键是已冻结的合同，`pick_up_verdict/place_verdict` 都按它判定；往同一个块里加语义会诱使后来者把「可食」和「可拿」互相推导。否决：另开 `consumables` 宽松块，单点解析，M15 语义不动。
- **在场景/Life 层做「看到食物就吃」的捷径**：接 W 的机会目录会更快出效果。但那就绕过了授权、新鲜度与执行身份，也和「唯一正式写路径」冲突。否决：候选由网关投影，提交必须走 Resolver。

## Consequences

- **收益**：`CONSUME` 现在是真的：有身份、有授权、有回执、有崩溃语义、有对账查询，并且没有任何一处假装营养。任何人（包括之后做生活整合的 I）都可以只用 `get_available_actions` 的候选 + `submit_action` + `get_action_result` 三个调用完成「吃一份」，不需要自己判能不能吃。
- **代价与已知上限**：没有份量、没有购买、没有多地点免费清单；经济物件在本版彻底不可食（包括理论上「已经买下」的东西）。免费供给只看「当前位置 + 该 instance 的声明」，如果 W 的机会目录之后要收紧口径，应改 `world_consumable_contract.consumption_verdict` 一处。
- **与旧笔记的关系**：`2026-10-16-non-coercive-life-actions` 里「Resolver 支持进食与消耗道具」这句话在此之前与代码不符；本包把它落成事实，但**没有改那篇笔记**。那句「进食是可选行动、不做定时打卡」的裁定依然有效，且 CONSUME 没有引入任何定时器或强制触发器。
- **升级触发条件**：出现真实份量/支付/所有权消费时；或 Body 真的拿到测量得到的动作时长时（届时用实测时长替换 2 秒假设，并保留 note 里的「非营养」措辞）。
