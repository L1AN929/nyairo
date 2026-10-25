# Agent Note: 拿放从 Life 决策到 World owner 的唯一通路，以及有界生活现场（v0.4 B）

Status: implemented

## Problem

World owner 早就有两个真实的"手"动作：`PICK_UP {object_id, slot_id}`
（`world_action_resolver:1198`）与 `PLACE {object_id, target}`（`world_action_resolver:1334`），
`action_intent.EXPOSED_VERBS` / `ACTION_ALLOWLIST` 也早就登记了它们。但 Life 侧三处缺口让
"她真的把杯子拿起来放到桌上"无法发生：

1. **没有 target 拼写**。AR-0 一次动作只有一个 `target` 字符串，`place:`/`object:`/`stay:`/
   `bodyact:` 已经各自占用命名空间；这两个 verb 一个要两个参数、另一个要一整个 placement
   映射，没有任何合法写法。于是"拿起"这条路只能靠测试直调 resolver（正是被禁止的旁路）。
2. **provider 不翻译它们**。`world_move_opportunity_source` 只翻 MOVE/CONSUME/STAY/POSE/REST，
   所以 AG-0 的机会里根本没有拿放；`get_available_actions` 里 owner 已经给出的
   `PICK_UP.candidates[{object_id, slots}]` 与 `PLACE.available` 没被用上。
3. **现场视图不是给角色看的**。`last_observation`/`last_catalog`/`last_opportunity_view`
   只服务于报告：地点是 id 而不是名字，没有"我现在手上有什么、旁边有什么能拿、能放哪"，
   模型无法知道"拿什么放哪里"，只能看见 opaque ref。

## Decision

### 一、`life_daily_action_targets.py`（新模块）：一个 target 装下全部参数

```
dailyact:pick_up:<slot_id>:<object_id>
dailyact:place:room:<location_id>:<area_id>:<object_id>
dailyact:place:surface:<location_id>:<surface_id>:<object_id>
```

- 每个字段 `urllib.parse.quote(value, safe="")`：真实 surface id 形如
  `desk_surface:<item>`，含分隔符，必须编码才能确定性往返。
- 只接受 owner 自己的参数白名单，且**必须完整**：`PICK_UP {object_id, slot_id}`、
  `PLACE {object_id, target}`；`target` 只允许 `room`/`surface`（键集与
  `world_body_substrate.validate_placement_target` 一致）。多一个键、少一个键、未知 verb、
  `container`/`hand`/`in_transit` 目的地、非白名单手槽一律抛 `DailyActionTargetError`。
- 公开四个函数（A 的写集只读它们，不再自己拼字符串）：
  `encode_daily_action_target(verb, params) -> str`、
  `parse_daily_action_target(target) -> {verb, params, target_ref, object_id} | None`、
  `build_daily_action_step(target, *, origin_ref=None) -> AG-2 step`（与
  `life_body_action_targets.build_body_action_step` 同形：`payload_spec` 仍只有
  `verb`/`target`/`expected_dependencies`）、
  `daily_action_completion(target) -> {fact_key, expected_value}`。
- 完成事实只有一处来源：`get_snapshot`。`daily_action_observed_fact(parsed, snapshot)` 给出
  `{value, satisfied, observed, unknowns}`；读不到就 `value=None`、`satisfied=False`。
  room 目的地的"哪间房"由身体当前所在推得（owner 的 destination 规则只允许当前区域），
  这一条在 `unknowns` 里标为 `room_placement_location_from_snapshot_location`，不当成直接读到的字段。

### 二、`provider.observe` 扩两 verb，`daily_life_context` 是给角色的现场

- `observed_daily_action_opportunities(...)`：`PICK_UP` 每条机会 = owner 候选里的一个物件 ×
  owner 给的一个空闲手槽（按 `HAND_SLOTS` 顺序取第一个）；`PLACE` 每条机会 = 快照 `hands`
  里真拿着的物件 × 真实目的地。目的地只有两处来源：当前 `location.place_id/area_id`，
  以及只读 gateway 命令 `get_environment` 里 `surfaces_here` 投影出的**本地点**台面。
  owner 说不 available / 没有候选 / 没有空手 / 没拿东西 → 零条机会。AG-0 record 形状不变。
- `daily_life_context(limit=8)`：只读本轮已发生的那次观测，返回 `location`（名字/区域名/营业/
  场景/当地时间，全部来自 World 自己的目录）、`body`（姿势/朝向/真拿着的手/空手/能否动手）、
  `nearby`（目录自己列的地方 + 路程 + 营业）、`nearby_objects`（真的伸手可及：手上的 +
  owner 说此地可取的）、`opportunities`（每条带名字、一句描述、时长与来源、准备事实）、
  `unknowns`（短码）+ `unknown_details`。**没有**打分、排序偏好、默认选择：排列只是展示分组
  （手边动作 → 待着 → 用掉 → 去哪里 → 姿势），组内保持观测自身顺序，`order_basis` 明说。
- 不知道就说不知道：远处没亲眼看到的地点只给时间表事实、不塞内部物件细节；快照不暴露位置的
  物件不进 `nearby_objects`；容器目的地这版 owner 不接受，所以不给。

### 三、Bridge adapter：两 verb 走真实 Resolver，成功后读回真实 placement

- `SUPPORTED_VERBS = ("MOVE","CONSUME","POSE","REST","PICK_UP","PLACE")`；`resolver_params`
  对两 verb 走 `parse_daily_action_target`，解不出是**提交前**确定拒绝
  （`DAILY_ACTION_TARGET_INVALID` + `DEFINITE_PRE_SUBMIT`），没铸授权、没碰 owner。
- 成功后 `details = {observation_source, post_state_only: True,
  post_state_proves_this_execution: False, daily_placement, placement_fact}`：
  可以核对"现在的 placement/hands 是否等于完成事实"，但**不**宣称后置状态证明了这次执行；
  归属仍只在 AR-0 回执与世界执行记录。`read_daily_placement(object_id)` 给 A 一个只读查询口。
- 幂等仍按**原 key** 缓存，UNKNOWN 仍按同一 `execution_id` 对账；四 verb 不退化。
- `RealWorldActionAdapter.validate_prepare` 只对这两个 verb 放宽（payload 仍是同样的三个键）。

## Alternatives considered

- **复用 `bodyact:` 命名空间装拿放** — 少一个前缀，A 的命名空间分流只改一处。否决：`bodyact:`
  的语义是"身体姿势/恢复会话"，把物件 id 塞进去会让"这个 target 是身体动作还是物件动作"变成
  靠位置猜；两套语义混在一个命名空间，正是本仓反复避免的"一种拼写两种意思"。
- **给 AR-0 加自由 `params` 映射** — 最省事，`PLACE` 的嵌套 destination 直接透传。否决：
  AR-0 的合同就是"一次动作一个 target"，一旦开放 params，调用方可以填任意参数，等于绕过
  `action_intent` 的准入与 fingerprint；而且"模型/调用方自己拼参数"正是要禁止的旁路。
- **让 provider 枚举所有容器/台面当目的地** — 会立刻显得"能放的地方很多"。否决：owner V1 明确
  拒绝 `container` 目的地，快照也不暴露 placement 的位置；枚举出来的列表里会有一半提交时被拒，
  等于用"看起来能选"换真实。改成只用 owner 自己投影的本地点台面 + 当前房间。
- **把报告视图（`last_opportunity_view`）直接当角色 context** — 不用新代码。否决：那份视图是
  报告用的，地点是 id、没有"手上有什么/能放哪/要多久"、还有 `world_revision_ref` 这类内部字段；
  直接塞进 prompt 既不可读也越界。现在的 `daily_life_context` 复用同一次观测，但只投影角色该知道的。
- **让 Life 侧直接读 substrate/世界库判断能不能拿** — 判断最准。否决：那是第二份 World 真相，
  `takeable`/手占用/姿势/目的地全是 owner 的判定；Life 只翻译 owner 已经给出的候选。
- **成功后把"物件在手/在地上"当成本次操作成功的证据** — 最省事。否决：后置状态无法区分"这次放的"
  和"本来就在那"；所以 details 里同时给 `satisfied` 与 `post_state_proves_this_execution: False`。

## Consequences

- **收益**：`PICK_UP`/`PLACE` 第一次能从一次真实观测出发走完整条正式链：AG-0 机会 →
  `dailyact:` target → AG-2 步骤（同形）→ AR-0 → Intent Executor 铸一次性授权 → `gateway.sock`
  → Resolver 提交 → 从 substrate/快照读回真的 placement/hands。`tests/test_world_daily_hands.py`
  用真实 `WorldBodyService`（真 lease、真授权注册表、真 substrate）证明拿起与放下都真的改变了世界。
- **收益**：角色侧第一次拿到"名字 + 手上 + 旁边 + 能放哪 + 要多久 + 未知清单"，而不是一串 id；
  未知（远处未观测、容器目的地、台面读不到、姿势动不了手）都显式列出，模型要装知道反而更难。
- **代价与已知上限**：白名单是显式的，加一个 verb 仍要动 AR-0 的 verb 集合与
  `world_journey_action_adapter.DELEGATED_VERBS`（这两处不在本包写集，回执 §5 写明归属 A）。
- **代价与已知上限**：PICK_UP 只取 owner 手槽顺序里的**第一个**空闲手，因此"双手都空时用左手"
  是确定性行为而不是模型选择；`hand` 目的地（换手）不编码，因为那不是"放下"。
- **代价与已知上限**：`HAND_MANIPULATION_SECONDS=2.0` / `POSTURE_SECONDS=5.0` 是
  `body_consequence` 的副本（该模块把 PICK_UP/PLACE 的 2.0s 直接写在分支里），owner 一改这两个
  副本要跟着改；`REST` 不给 duration，因为它是会话不是时长。
- **代价与已知上限**：`get_environment` 是每次观测多一次只读 round trip；它读不到时只损失台面
  目的地（房间仍然可用），并在 `unknowns` 里说明。

## Verification

- `tests/test_world_daily_hands.py`（新，12 用例，WSL `/root/nya-venv/bin/python -m unittest`）：
  6 个编码契约用例（确定性/可逆/拒绝越界/step 同形/完成事实只认读到的）+ 6 个真实 owner 用例
  （真拿起→读回 hands、真放下→读回 room placement、同 key 重试不改世界、拒绝与未知不伪装成功、
  现场视图名字/营业/未知边界、A 的 `life_live_context_view` 直接消费）。
  命令 exit 0。
- 同批定向回归：`tests.test_day_life_body_actions`（14 用例）与本包合跑 26/26 exit 0；
  `tests.test_world_daily_life_consume` 6/6 exit 0。`tests.test_world_daily_life_continuous` 的
  `ALREADY_DONE != ACTED` 红灯在本包三个源码文件被 `git stash` 摘掉后同样复现，属既有的
  artifact-closeout 路径问题（失败那一轮没有任何 dailyact 目标）。
- 回执与接口细节：`docs/engineering/v04-lived-day-world-result.md`。

## 后续（2026-10-24）：动作到准备条件的两个真实漏接

### Problem

第二段要的是"真实动作留下可再次使用的环境布局/材料/工具状态"。按本笔记已交付的拿放链实测，
owner 那边的准备事实并没有完整到达 Life：

1. **台面永远进不了候选。** owner 的 `get_environment -> surfaces_here` 用
   `{"surface_id", "surface_kind", "location_id"}` 说话，而翻译层
   `_daily_destinations` 按 `item.get("kind") == "surface"` 过滤。`surface_kind` 是家具种类
   （`desk_surface`），`surface` 是本编码的目标种类，两者不是一套词汇，于是每一块 owner 真的
   投影出来的台面都被丢掉，只剩"这间房"这一个放法。`tests/test_action_preparation_behavior.py`
   的真实 owner 用例复现：`get_environment` 明明返回 `desk_surface:i-desk-01`，PLACE 候选里
   却没有它。
2. **就算发出来，也过不了 AG-0 的 ref 合同。** 编码用
   `urllib.parse.quote(value, safe="")` 把 surface id 里的 `:` 写成 `%3A`；AG-0 的
   `ObservedWorldOpportunity` 对 `target_refs`/`opportunity_id` 使用
   `[A-Za-z0-9_.:/-]` 字符集，`%` 非法。只修第 1 处会得到
   `MissingCandidateProvenanceError`，机会被如实拒绝而不是变成候选。
3. **同一目标在后续状态里被当成一次旧执行。** dailyact 的 step origin 只有 `place:<place>`，
   AR-0 的 action key 完全由 step 决定。于是"把杯子放到桌上 → 重新观测 → 再把杯子拿起来"
   在同一个地点重建了第一次拿起的那把 key，AR-0 直接返回上一次的 SUCCEEDED，世界一点没动。
   准备条件在 owner 那里是真的，在 Life 这一侧却不可再用。
   （`world_revision` 不能当这个区分：PICK_UP/PLACE 只改 substrate placement，不改
   `world_state.revision`，实测两轮都是 r1。）
   后续复核又确认：只用观测状态哈希仍不是动作身份。ABA 两个完整循环后可见事实逐字节
   相同（实测两次放回后 `_daily_action_state_token` 相同），第二次 PICK_UP/PLACE 会重建
   第一次的 key 并被 AR-0 缓存回放；而 owner 已执行、客户端丢答时观测会变，同一 Decision
   反而会换 key 重发。因此最终以 `decision_id` 作为 dailyact 动作身份，观测状态哈希只保留
   为无 Decision 身份时的 fallback。

### Decision

只修这三处接缝，不新增动词、owner、资格或 source kind：

- `world_move_opportunity_source._daily_destinations`：以 owner 投影里真实存在的
  `surface_id` + `location_id` 为准，不再按 `surface_kind` 去匹配本编码的 `kind`。只保留当前
  地点的台面；缺 id、别处、重复一律丢弃。`kind: "surface"` 仍由本编码自己拼，owner 的
  `surface_kind` 只用于识别"这是一块 owner 真的投影出来的台面"。
- `life_daily_action_targets`：surface 字段改为 ref-safe token（`/` 转义：`//` 表示字面 `/`，
  其余非字面字符按 UTF-8 逐字节写成 `/xHH`），结果只含 `[A-Za-z0-9_./-]`，既保留 `:` 作字段
  分隔，又能原样通过 AG-0 的 ref 字符集。转义符必须是 `/` 而不是 `-`：旧拼写
  `quote(value, safe="")` 会把 `/` 写成 `%2F`，因此旧字段里不可能出现字面 `/`；而 `-` 在旧
  拼写里是普通字面字符，用 `-xHH` 就会把旧目标 `desk-x3Asurface` 误读成 `desk:surface`。
  解析端无需版本号即可消歧：含 `%` 的 token 走旧 percent 分支；含 `/` 的只能是新拼写；
  两者都不含的 token 在两种拼写里都是字面值，逐字返回。旧 `%XX` 拼写、旧的裸 `-`/`--`/
  `-xHH` 字面以及旧非 ASCII percent 仍可解析，room/pick-up 的目标一字未改。
- `world_move_opportunity_source._daily_opportunity_suffix`：机会 id 用解码后的后缀；仍不合
  ref 字符集时用目标串的稳定 sha256 前 32 位，而不是把 owner 事实丢掉。
- `autonomous_life_cycle._world_step_spec`（仅 dailyact 分支）：origin 从 `place:<place>` 改为
  `place:<place>@<decision_id>`，即动作身份来自已提交的 Decision，而不是观测。没有 Decision
  身份的调用（手工构造的 step）仍退回 `place:<place>@<state-token>`。
  观测哈希不能表达动作身份：PICK_UP/PLACE 不改 `world_revision`，两次完整拿放循环后可见事实
  逐字节相同，纯状态 hash 会重建第一次的 key，AR-0 直接回答旧 SUCCEEDED；反过来，owner 已执行
  而客户端丢答时观测会变，纯状态 hash 又会让同一个 Decision 变成新动作。Decision 身份同时
  满足两件事：同一 Decision 的 step 重试/UNKNOWN 对账保持原 execution，新的 Decision 即使在
  相同观测下也是新的逻辑动作。资格门、QUIET、TTL、预算、默认 gate、模型偏好均未改。

### Alternatives considered

- **改 owner 的 `surfaces_here` 输出加一个 `kind: "surface"`**：翻译层一行不用改。否决：
  那是 World owner 的投影合同，写集与权限都不在本单；而且会让"家具种类"和"放置目标种类"
  在 owner 侧混成一个字段，正是本仓避免的一种拼写两种意思。
- **放宽 AG-0 的 structured-ref 字符集允许 `%`**：最省事。否决：那是共享的证据链合同，
  不是本接缝能改的；为一块台面放宽全链 id 语义，爆炸半径远大于收益。
- **用 `observed_at` 或每轮时间戳做 dailyact origin**：能保证"每次都新"。否决：同一状态的
  重试会拿到新 key，UNKNOWN 对账就会变成第二次真实动作，直接削弱一次性执行语义。
- **只加 world_revision 到 origin**：看起来符合"place + revision"的既有写法。否决（实测）：
  PICK_UP/PLACE 不改 `world_state.revision`，两轮都是 r1，等于没加。
- **用完整观测状态哈希做 origin**：比 revision 细，能区分"拿在手里"和"放在桌上"，第一次
  修复看起来够用。否决（ABA 实测）：两次完整拿放循环回到逐字节相同的观测，第二循环的
  PICK_UP/PLACE 重建第一循环的 key 并被 AR-0 缓存回放；同时 owner 已执行、客户端丢答时
  观测变化会让同一 Decision 换 key 重发。观测哈希只能在无 Decision 身份时作 fallback。
- **在 Life 侧按 object_id + 当前 placement 手写一小份"准备状态"**：会更直白。否决：
  `daily_action_observed_fact` 已经给了 `value`/`satisfied`/`unknowns` 的三态判据，再建一份
  就是第二 World 真相；本单只把 owner 的事实接通，不复制判据。

### Consequences

- **收益**：真实 owner 的台面第一次成为 PLACE 候选；把杯子放到桌上后，重启同根 owner，杯子仍由
  owner 报告在 `desk_surface:i-desk-01` 上，`get_environment.surfaces[].items` 仍含它，
  `dailyact:pick_up` 又能成为真实候选并经 AR-0 再次提交成功。准备条件不是 prompt 里的一句话，
  而是 owner placement → 候选 → 正式 step 的真实路径。
- **收益**：同一个 Decision/step 的重试和 UNKNOWN 对账保持同一把 key、同一个 execution；
  新的 Decision 即使观测状态完全相同也是新的逻辑动作，ABA 循环不会被旧 SUCCEEDED 缓存
  吃掉。观察事实（列表顺序、无关物件移动、姿态变化）不再自动改变动作身份。未读到的
  snapshot 仍是 `value=None`/unknown，不会因为"手里没了"被推断成"放在某块台面上"。
- **代价与已知上限**：surface token 把 `:` 等字符转义为 `/xHH`，非 ASCII 按 UTF-8 字节展开，
  目标串比原来的 `%XX` 略长；极端长的实例 id 仍可能撞上 AG-0 的 161 字符 id 上限，此时该
  台面不会被提供（fail closed），本单没有为它发明压缩格式。
- **代价与已知上限**：`decision_id` 只在 AG-1 已提交的 Decision 上存在；没有它的调用退回
  观测状态哈希，仍受上面的 ABA/UNKNOWN 限制。Decision 身份不是持久化的第二状态，也不授权
  任何动作；AR-0 的 fence/reconcile 仍是原 execution 的唯一对账路径。
- **未改**：真实模型自然采用未证；`NO_ACTION` 仍合法；没有强制拿放/休息配额；旧实例与历史
  账本未触碰。
- **2026-10-24 本人批准（只对本地现树）**：接受 surface 字段的 `/xHH` 转义拼写；新版读端继续
  兼容旧 `%XX` 与字面 ID，**旧读端不得处理新目标**，不声称双向兼容。schema、owner 合同、
  AG-0 字符集与长度上限都不变；这不是发行、部署或旧实例迁移。保留偏差：施工者先实现了这一
  格式变化、未按原工单先停报，父级没有把"无新字段/无版本号"当成"未改契约"，而是单独取得
  上述局部批准后才接收。

### Verification

- `tests/test_action_preparation_behavior.py`（20 例）：owner 台面投影 → surface 目标、
  别处/畸形/重复丢弃、动作前后目标集合变化、UNKNOWN/absent 不假报、同观测确定且无授权、
  同状态同 key/placement 变化新 key；surface token 对字面 `-x3A`、`--`、`:`、`%`、非 ASCII、
  `/` 的往返，旧 percent/裸 `-`/`-xHH`/非 ASCII percent 逐字解析，以及"旧字面
  `desk-x3Asurface` 不等于新冒号目标"的歧义负控；真实 owner 用例证明 gate off 零副作用、重复观测不改
  substrate、surface placement 同根重启后仍在；真实 owner ABA 五步（pick/place×2+pick）
  四个 execution 各自提交且两次放回后观测相同、key 不同；真实 AR-0 丢答用例证明同一
  Decision 的 key 在无关物件移动后不变、裸重试被拒、原 execution 经 World ledger 对账
  为 SUCCEEDED、未决解决后新 Decision 可以新动作。
- `scripts/action_preparation_behavior_smoke.py`：真实 gateway/socket/lease/Resolver/AR-0，
  PICK_UP → PLACE 到 desk surface → 停 owner 同根重启 → AG-0 重新物化 → 再 PICK_UP；再到
  真实 owner 的 ABA 两循环（place/pick ×2，四个 execution 唯一、两次 pick 后观测相同而 key
  不同）和旧/新 surface token 兼容检查（旧字面 `-x3A`/`--`/裸 `-` 与非 ASCII percent 逐字读回、
  新 token 往返无 `%`、旧字面 `desk-x3Asurface` 不串成冒号目标），全部 check 通过，
  `RESULT: PASS`，scratch 保留。
- 定向回归：`tests.test_world_daily_hands` + `tests.test_lived_day_core_v04` 38 例 OK；
  `tests.test_lived_day_chain_v04` 真实 Chain 仍 OK。
