# Agent Note: 主体自己的姿态与休息——一个 target 装两个参数，机会只来自 owner

Status: implemented

两轮：W（Life 侧编码/机会/AR-0/bridge/journey）+ B6E1（追加授权：World bridge 的
`components/world/service/bridge/action_intent.py` 暴露清单窄接 `REST`）。B6E1 是 W 的真实链用例
逼出来的：Life 侧全接通后，真实 REST 仍停在 intent 层的 `INVALID / not in the production allowlist`。

## Problem

0.4 一天生活里，「坐下 / 躺下 / 歇一会儿」在产线上**只存在于手机操作者通道**。符号级的三道门：

1. `world_move_opportunity_source.py` 只翻译 `MOVE` / `CONSUME`（另加 Life 侧 `STAY`），所以
   `POSE`/`REST` 从来不会成为主体能考虑的机会；
2. `action_reality_ledger.WorldActionAdapter.validate_prepare` 的动词白名单是
   `{MOVE, POSE, CONSUME}`（`REST` 不在），且**要求单个非空 `target`**；
3. `world_action_bridge_adapter` 的 `SUPPORTED_VERBS=("MOVE","CONSUME")` 与
   `world_journey_action_adapter` 的 `SUPPORTED_VERBS=("MOVE",)` / `DELEGATED_VERBS=("CONSUME",)`
   把其余动词一律 `UNSUPPORTED_VERB_*` 拒掉。

根因不是「没有授权」，而是**编码缺口**：AR-0 的一个动作只有一个 `target` 字符串，而
`POSE` 需要 `pose`+`orientation`（`world_action_resolver:1489`），`REST` 需要 `state`+`mode`
（`:1638`）。一个字符串装不下两个参数，于是这两个 owner 动词在 Life 侧根本无从表达。

顺带一个因果坑：`RESTING_POSES=("seated","lying")`，主体没有 `POSE` 通道时即使接上 REST 也永远
start 不了 —— 所以 `POSE` 与 `REST` 必须同批接。

第四个门是 W 实测才暴露的（B6E1 的原因）：`components/world/service/bridge/action_intent.py` 的
`EXPOSED_VERBS`/`ACTION_ALLOWLIST` 是显式生产清单（`MOVE/POSE/PICK_UP/PLACE/CONSUME`），
`build_submission` 对不在清单里的 verb 一律 `INVALID`。Life 侧再怎么接线，REST 都到不了
`gateway.sock`；"加进 Resolver 不等于获得许可"正是这份清单立下的规矩。

## Decision

按写集 W（`world_move_opportunity_source.py` / `world_action_bridge_adapter.py` /
`world_journey_action_adapter.py` / `action_reality_ledger.py` + 新 `life_body_action_targets.py`）
把这条链接通，全部只走既有 owner：

1. **一个 target 装全部参数**（`life_body_action_targets.py`）：
   `bodyact:pose:<pose>:<orientation>` / `bodyact:rest:<start|stop>:rest`。
   `parse_body_action_target(ref)` 不是 `bodyact:` 就返回 `None`；是但不完整/多字段/未知动作/
   越界枚举就抛 `BodyActionTargetError`。返回**只有五键**：`verb`、`target`（编码原串）、
   `params`（owner 参数，一个字段不多）、`fact_key`、`expected_value`。
   `build_body_action_step(ref, *, place, revision, decision_ref=None)` 出原 AG-2 world step spec，
   `payload_spec` 仍是 `{verb, target, expected_dependencies}`（**没有 `params` 键**）。
   身份按调用方是否带着已提交的 Decision 分两路：正式 cycle 的 BODY 分支传
   `decision_ref=<decision_id>`，key = `idem_step:v02:world:bodyact:decision:<quoted decision>:<编码原串>`
   （绑定 Decision + 全部参数，不含观测坐标）；没有 Decision 的手工调用保留
   `idem_step:v02:world:bodyact:<place>:r<revision>:<编码原串>`。后者只是兼容路径，不是正式安全保证。
2. **AR-0 只窄增 `REST`**（`action_reality_ledger.py:1766`）：`{MOVE, POSE, CONSUME, REST}`。
   `POSE` 原本就在白名单里，本轮**没有**把 payload 改成任意 `params`，也没有给任何动词开自由参数口。
3. **bridge 解码、journey 委托**：`SUPPORTED_VERBS=("MOVE","CONSUME","POSE","REST")`；
   `resolver_params(verb, target)` 让单参数动词保持原样（`destination_location_id` / `object_id`），
   body 动词从编码解出 owner 参数；解不全 → `BODY_ACTION_TARGET_INVALID` +
   `DEFINITE_PRE_SUBMISSION_FAILURE`（授权未签发、owner 未被联系）。
   `DELEGATED_VERBS=("CONSUME","POSE","REST")`，`SUPPORTED_VERBS` 仍是 `("MOVE",)`：
   `POSE`/`REST` 不是旅程，走同一条正式链（IntentExecutor → gateway.sock → Resolver）。
4. **机会只来自 owner**（`observed_body_action_opportunities`）：`POSE` 枚举
   `get_available_actions` 的 `params.pose`，朝向取 canonical snapshot 的当前朝向，**排除当前姿态**
   （owner 会回 `already_in_pose`，无变化不是机会）；snapshot 没朝向就不出机会（第二参数不猜）。
   `REST` 只看 `start_available` / `stop_available`，两向都没有就一条都不出。
   **没有任何地方判断她累不累、该不该坐。**
5. **`rest` 模式窄准入**：owner 词表里有 `rest` 与 `sleep_like`（`OWNER_REST_MODES` 记录这个事实），
   但本轮 `REST_MODES=("rest",)` —— 编码、解析、step、bridge 全部拒 `sleep_like`。
   于是「发射器没提供」不是唯一防线：手写一个 `bodyact:rest:start:sleep_like` 也执行不了。
6. **事实契约（给 B）**：`fact_key` / `expected_value` 在解析结果里一起返回 ——
   POSE → `world_body_pose_ref` / `world_fact:body:pose:<pose>:<orientation>`；
   REST → `world_body_recovery_ref` / `world_fact:body:recovery:active|inactive`。
   `body_action_observed_fact(parsed, snapshot, *, rest_availability=None)` 是纯函数，owner 答不出来时
   `value=None`（永不意外满足）。**REST 的事实是会话状态，不是身体恢复**；bridge 的成功回读对 REST
   显式标 `command_success_is_not_recovery: True`。B 用真实 receipt + canonical snapshot 形成
   `WORLD_FACT` 后走原 LR-5（不新增 policy version）。
7. **B6E1：把 REST 接进 World bridge 的显式清单，并当场钉住参数。**
   `service/bridge/action_intent.py`：`EXPOSED_VERBS`/`ACTION_ALLOWLIST` 各加 `REST`，
   并新增 `validate_verb_params(verb, params)`（只在 `build_submission` 里调一次，紧接 allowlist
   之后）：`REST` 的 params 必须恰好是 `{state, mode}`，`state ∈ {start, stop}`、`mode = rest`；
   缺字段 / 多字段 / 越界 / `sleep_like` 一律 `ActionIntentError`（→ `REJECTED / INVALID`，
   在铸授权之前）。其他动词原样返回，**没有**开自由 `params`。授权票就是按这套（已验证的）参数签发，
   一次性租约、CAS、`gateway.sock` 与 Resolver 守卫一个字没动；Body 阈值/速率/初始负荷也没动。

## Alternatives considered

* **把 AR-0 payload 改成任意 `params` 映射**：最省事，能让所有多参数动词一次到位。否决：那等于让
  AR-0 变成"参数口袋"，`target` 不再是执行身份的一部分，UNKNOWN 对账与幂等键都会失去绑定，
  而且给了上层一个自由填参的入口 —— 与本轮"严格枚举、完整字段"的要求正相反。
* **在 cycle 里按 `verb` 分支自己拼 `params`**：会让同一份编码逻辑散落两处（B 一处、bridge 一处），
  必然漂移。否决：编码只放在 `life_body_action_targets.py` 一处，B 只调函数。
* **顺手把 `ARRANGE` / `TOGGLE_FIXTURE` 也接上**（recon 的 P1 曾建议四个动词一起）：否决 ——
  本轮施工单只授权 `POSE` + `REST start/stop`，多接两个动词会让"最小可验收面"变模糊，而且
  `ARRANGE`/`TOGGLE_FIXTURE` 各有自己的前置（home-only、绝对 on/off）需要单独论证。
* **只在 Life 侧拒 `sleep_like`、不动 World bridge 清单**：能过测试，但真实 REST 仍然到不了 owner
  （`INVALID / not in the production allowlist`），等于把已知断链留在原地。否决 —— 施工单明确
  "不可用'不在原写集'将已知断链当完成"，于是走 B6E1 的窄接。
* **放宽 gateway 曝光策略，让低负荷 REST 也 `available`**：这样请求就能一路到 Resolver。
  否决 —— 那会废掉"不能绕过 `get_available_actions`"这条明确守卫（ticket 18-20），
  让一个 owner 说不该做的动作变得可提交。
* **把 Body 的恢复门槛搬进 Resolver，好让低负荷请求"到 Resolver 后 RECOVERY_NOT_NEEDED"**：
  否决 —— 阈值属于 Body owner（`_rest_body_gate`，`world_body_service.py:402-453`），
  搬进 Resolver 就是让世界层替身体做判断，违反 2026-10-21 的裁定。
* **把 service 侧那份同名 `action_intent.py` 副本也一起改**：写集只授权了 bridge 那份，
  而且真实链只经 bridge 副本（`intent_executor`/`decision_trigger` 的 `import action_intent`）。
  否决（越权）；改为在用例里用 `ai.__file__` 钉住"用的是哪一份"，并把副本分叉作为已知限制写出。
* **为了造一个 REST 正例，直接把 body 的 fatigue 写成 ≥ 0.30**：否决 —— 那是伪造恢复需求，
  等于把"她累了"变成一个测试夹具的谎言。改为只验证命令参数（隔离层）并明确写"机制成立、
  真实恢复未验证"。
* **在机会源里过滤 `transitioning`**：否决 —— owner 的 `POSES` 里有它，Life 侧自行删词表就是第二真相。
  是否要从 owner 词表里去掉属于 World 侧的决定。

## Consequences

* 主体第一次可以自己「坐下/躺下」：真实 `POSE` 经 AR-0 → bridge → IntentExecutor → gateway.sock →
  Resolver 提交，基底 `pose`/`orientation` 真的变，回读的是 owner 自己的快照（真实链用例第 9 条）。
* 同一个姿态重复提交不会二次落盘（判据是基底 revision 不前进）；机会源那一侧根本不把当前姿态当候选。
* `REST` 的上游断链已闭合（B6E1）：请求通过 allowlist 与参数契约、拿到一次性授权、被提交到
  gateway（lifecycle 逐段 `CREATED → AUTHORIZED → SUBMITTING → SUBMITTED → REJECTED`）。
  剩下的拒绝来自 **owner 自己的可用性**（`NOT_EXPOSED`）——那是正确行为，不是缺件。
* **"到 Resolver 之后拿到 `RECOVERY_NOT_NEEDED`"在本架构里不可能，而且不该被当成目标**：
  `RECOVERY_NOT_NEEDED` 由 Body preflight 在 `resolver.resolve` **之前**产出
  （`world_body_service.py:495-498` / `:402-453`），而 gateway 的曝光策略更早一步按
  `available` 拒绝；两者逻辑互补（`needs == False` ⟺ `start_available == False` ⟺ 未暴露）。
  所以低负荷的诚实答案是 `NOT_EXPOSED`，Body preflight 对同一条命令的答案是
  `RECOVERY_NOT_NEEDED`，两条都零副作用。要真正走到 Resolver 的 REST，需要一个**真的开着**的
  恢复会话（即真实高负荷），本轮不伪造。
* 枚举是 owner 词表的拷贝：owner 扩词表时 `life_body_action_targets.py` 必须同批扩，否则新词被
  fail-closed 拒掉（有意方向：宁可拒，不可猜）。
* B 还需要接线（cycle 的候选识别 / step 分支 / 进度事实），本包只提供三条固定接口；
  `bodyact:` 候选在 B 接线前会被 cycle 的既有前缀过滤掉，不会半途生效。
  （接线已于后续完成；本条保留当时事实，不倒写。）

## 2026-10-25 续步边界：BODY Activity 没有可放行的"下一步"（STOP，未施工）

核查结论：现有 owner 合同**没有定义**一个已创建 BODY Activity 的后续步骤，所以前台
`has_foreground` 不是这次 k 的 `CONTINUE -> NO_STEP_AVAILABLE` 的可修根因。依据：

* 一个 `bodyact:` target 就是一次动作：`build_body_action_step` 只产出一份 payload，
  `POST_SUBMIT_KEEP_ACTIVE`，幂等键绑定 place+revision+target，`REST` 的 start/stop 是两个
  独立编码而非两阶段；成功判据是单一 `WORLD_FACT_MATCH`，`_classify_body_action` 满足后立即
  ingest LR-5。`ACTIVE` 只说明这条 Activity 还没被关闭、完成判据还没被确认：它**不能**反推
  动作一定没成功——提交、结算与 LR-5 完成是三层证据，k 那次只是恰好停在明确未提交。
* cycle 的 `world_step` 只在 `not has_foreground` 时构建；阅读与 stay 各有前台特例，BODY 没有。
* 即使解除该条件也不够：adoption 把目标写成 `world_fact:bodyact:...`，而
  `_candidate_is_world`/`_world_step_spec` 只认裸 `bodyact:` 前缀，CONTINUE 候选会被判成非 World。

能补上的只有两条，均需新合同：(a) 预提交失败的同 Action id 重试政策（谁触发、上限、gate off
是否重试）；(b) 把重复同一 body 动作当新进度（与机会源排除当前姿态、owner `already_in_pose`、
LR-5 单事实匹配冲突）。另有 (c) 裁定 BODY Activity 无下一步并撤回 CONTINUE 候选，属用户可见
候选面变化。三条都必须先拍板，本包不代选、不施工。它不改变 `REST` 事实是会话状态、
姿态/恢复仍归 Body owner、机会只来自 owner 这些既有边界。

## 2026-10-25 裁定与实现：选 (c)，BODY 无下一步就撤回 CONTINUE

用户当天直接裁定接受"BODY 活动现为单动作/单完成条件"，撤掉不可执行的 CONTINUE、保留既有
退出，不为凑多步新增动作语义；多步身体活动另开设计，不在本轮。上面 (a)/(b) 两条新合同
都没有实施，本包的 STOP 历史保留。

落地就是 `life_step_readiness` 对已绑定的
`BODY_ACTION_AT_OBSERVED_PLACE` / `bodyact:` 前台答
`BODY_SINGLE_ACTION_NO_FURTHER_STEP`，经既有 `current_step_unavailable_true` 通道让 AG-1
只撤当前 CONTINUE；`PAUSE`/`ABANDON`/`DEFER`/`NO_ACTION` 与 park 提供的 `WAIT` 不动，
Action 结算、World step builder、`world_fact:` 编码、默认 gate 全不改。理由、被否的备选与
边界见 [BODY 单动作 readiness](../bug-fix/2026-10-25-body-single-action-readiness.md)。

## 2026-10-25 动作身份纠正：bodyact step 属于已提交 Decision

只读定位发现：`_world_step_spec` 的 BODY 分支此前只把 place/revision/target 交给 builder，
因此两个不同 Decision、两个不同 Activity 在同 place/revision/target 下会落到同一个
step/Action，旧 Activity/Decision/授权被冻结进第一次创建。现已在同一接缝把 committed
`decision_id` 传给 `build_body_action_step`；no-Decision 的旧 key 保留为兼容 fallback。
AR-0 幂等索引、AG-2 step 语义、readiness 撤 CONTINUE、World/Body owner 与默认 gate 均未改，
也没有新增自动重试或多步 BODY。问题、取舍与离线证据见
[BODY 动作身份对齐已提交 Decision](../bug-fix/2026-10-25-body-action-decision-identity.md)；
dailyact 侧同一原则见 [v04 一天生活与世界日常动作](../architecture/2026-11-13-v04-lived-day-world-daily-actions.md)。
