# v0.4 一天生活 · W 包（主体姿态与真实休息）结果

只做**主体侧的世界身体动词**：把 `POSE` 与 `REST start/stop` 从「手机操作者才能按」变成
「主体自己的一条真实 owner 动作」，并且让「她该不该坐/该不该休息」永远只由 World owner 的
`get_available_actions` + canonical snapshot 回答。

不做：`ARRANGE` / `TOGGLE_FIXTURE` / `PICK_UP` / `PLACE` / `sleep_like`（本轮不接）、
不改 cycle/runner/resident/assembly/phone/supply/economy、不改任何 Body 阈值/速率/初始负荷、
不生造恢复需求、不做 24h 运行、不 commit/push/deploy。

两轮：**W**（Life 侧编码/机会/AR-0/bridge/journey）与 **B6E1**（唯一追加授权：World bridge 的
`action_intent.py` 暴露清单窄接 `REST`）。B6E1 的理由是 W 实测发现真实 REST 仍停在上游
`NOT_EXPOSED`/`INVALID`，施工单明确"不可用'不在原写集'将已知断链当完成"。

写集（本轮实际改动，其余一律未动）：

| 文件 | 改动 |
|---|---|
| `components/life/scripts/life_body_action_targets.py` | 新增（编码 / step / 观测事实，唯一编码处） |
| `components/life/scripts/world_move_opportunity_source.py` | 修改（body 机会 + 视图识别 `bodyact:`） |
| `components/life/scripts/world_action_bridge_adapter.py` | 修改（`POSE`/`REST` 准入 + 解码 + 回读） |
| `components/life/scripts/world_journey_action_adapter.py` | 修改（`POSE`/`REST` 委托正式链） |
| `components/life/scripts/action_reality_ledger.py` | 修改（`validate_prepare` 窄增 `REST`，1 行 + 注释） |
| `tests/test_day_life_body_actions.py` | 新增（W 12 条 + B6E1 新增/改写 3 条 = 14 个 exact 用例） |
| `components/world/service/bridge/action_intent.py` | 修改（B6E1：`EXPOSED_VERBS`/`ACTION_ALLOWLIST` 窄增 `REST` + `validate_verb_params`） |
| `.agents/notes/implemented/feature/2026-10-26-v04-day-life-body-actions.md` | 新增（专属决策笔记） |
| `docs/engineering/v04-day-life-body-actions-result.md` | 新增（本文件） |

未动：`components/world/**` 除 B6E1 那一个 `bridge/action_intent.py`（owner/resolver/gateway/
`world_body_service.py`/service 侧 `action_intent.py` 副本一个字没改）、`components/phone/**`、
`autonomous_life_cycle.py`、`autonomous_life_runner.py`、`autonomous_life_resident.py`、
`autonomous_life_assembly.py`、supply/economy 各件。没有新增 schema、版本号、policy version、
候选种类、ledger 或写入域；授权票/一次性租约/CAS/Resolver 守卫全部沿用原样。

## 1. 给 B 的固定接口（签名就是契约，不随实现变）

```python
# components/life/scripts/life_body_action_targets.py
parse_body_action_target(ref: Any) -> Optional[dict[str, Any]]      # :159
build_body_action_step(ref: Any, *, place: Any = None,
                       revision: Any = None) -> dict[str, Any]       # :230
body_action_observed_fact(parsed: Mapping[str, Any], snapshot: Any, *,
                          rest_availability: Any = None) -> dict[str, Any]   # :290
```

### 1.1 `parse_body_action_target`

* 不是 `bodyact:` 前缀 → 返回 `None`（别人的命名空间不归本模块管）；
* 是 `bodyact:` 但不完整 / 多字段 / 未知动作 / 越界枚举 → 抛 `BodyActionTargetError`
  （`ValueError` 子类）。**不猜、不补默认值**；
* 返回**只有五个键**：`verb`（`POSE`|`REST`）、`target`（编码原串，AR-0 的单 target 身份）、
  `params`（owner 自己的参数映射，一个字段都不多加）、`fact_key`、`expected_value`。

| 编码 | `params` | `fact_key` | `expected_value` |
|---|---|---|---|
| `bodyact:pose:<pose>:<orientation>` | `{"pose": p, "orientation": o}` | `world_body_pose_ref` | `world_fact:body:pose:<p>:<o>` |
| `bodyact:rest:start:rest` | `{"state": "start", "mode": "rest"}` | `world_body_recovery_ref` | `world_fact:body:recovery:active` |
| `bodyact:rest:stop:rest` | `{"state": "stop", "mode": "rest"}` | `world_body_recovery_ref` | `world_fact:body:recovery:inactive` |

`fact_key` / `expected_value` 的语义（B 形成 `WORLD_FACT` 后走原 LR-5 `CRITERIA_WORLD_FACT_MATCH`）：

* **POSE**：owner 自己确认的姿态 + 朝向。只有 `snapshot.pose` 与 `snapshot.orientation`
  **同时**等于期望值才算满足；缺一个就是 `value=None`、不满足；
* **REST**：只看该 recovery **会话**状态（`get_available_actions` 的 `REST.current.recovery_state`）。
  `active` ↔ start、`inactive` ↔ stop。**命令成功不等于身体恢复** —— 事实里没有疲劳/恢复量/健康字段，
  也不许把 `start` 的成功写成「她恢复了」。

### 1.2 `build_body_action_step`

返回原 AG-2 world step spec（形状与 `_world_step_spec` 的 `place:` / `object:` 分支一致）：

```python
{"step_kind": "WORLD_ACTION", "action_kind": "WORLD_ACTION",
 "target_ref": "<编码原串>",
 "payload_spec": {"verb": "POSE"|"REST", "target": "<编码原串>",
                  "expected_dependencies": {}},        # 仍是单 target，没有 params
 "expected_effect_predicates": ["body_pose_changed" | "recovery_session_started"
                                | "recovery_session_stopped"],
 "post_submit_life_behavior": "KEEP_ACTIVITY_ACTIVE",
 "idempotency_key": "idem_step:v02:world:bodyact:<place>:r<revision>:<编码原串>"}
```

key 绑定**全部参数**（在编码里）+ **前置 revision**：参数不同或 revision 不同 → key 不同（用例逐项比对）；
`place=None` 记 `unknown`，与既有分支同规矩。

### 1.3 `body_action_observed_fact`

纯函数，返回六键：`fact_key` / `expected_value` / `value` / `satisfied` / `source` / `observed`。
`value` 在 owner 答不出来时是 `None`（所以永远不会意外满足），`source` 明确是
`world_gateway.get_snapshot` 还是 `world_gateway.get_available_actions`。

## 2. 机会：只来自 owner，且只出真实可做的

`world_move_opportunity_source.observed_body_action_opportunities`（`:247`）：

* **POSE**：枚举 owner `get_available_actions` 里 `POSE.params.pose` 那一串（`:311-328`），
  朝向取 canonical snapshot 的当前朝向（换姿态不换朝向）。**排除她现在的姿态**（owner 会回
  `already_in_pose`，无变化不是机会）；snapshot 没有朝向 → 一条 POSE 机会都不出（第二参数不猜）。
* **REST**：只看 `start_available` / `stop_available`（`:330-339`），**只出 `rest` 模式**。
  `sleep_like_available` 为真也不出 —— 而且 `sleep_like` 在编码/解析层就被拒（见 §3），
  所以「发射器没提供」不是唯一防线。
* 两向都不可用（典型：身体不需要恢复）→ 一条 REST 机会都没有。**没有任何地方判断她累不累**。
* 机会记录仍是 AG-0 `ObservedWorldOpportunity` 的既有字段（`wopp:pose|rest:<place>:r<rev>:<a>`），
  没有新增候选种类。

`opportunity_view`（`:475`）识别 `bodyact:`：报告 `kind`/`verb`、解码后的 `params`、
`pose`/`orientation`/`rest_state`/`rest_mode`、`fact_key`/`expected_value`，
以及 owner **按方向**的 `owner_blocked_reason`（`start_blocked_reason`/`stop_blocked_reason`，
POSE 用 `blocked_reason`；`_body_blocked_reason` `:597`）。坏编码不被丢掉：`kind` 记为
`INVALID_BODY_ACTION` 并把解析原因原样报出。`action_grant` 恒为 `False`。

`WorldMoveOpportunityProvider.observe`（`:731`）把 body 机会并入返回列表与视图。

## 3. 执行链：AR-0 → bridge → IntentExecutor → gateway → Resolver

| 位置 | 事实 |
|---|---|
| `action_reality_ledger.py:1766` | `validate_prepare` 白名单 `{MOVE, POSE, CONSUME, REST}`（`POSE` 原本就有，本轮只窄增 `REST`）。仍要求非空单 `target`；**没有**给任何动词开自由 `params` |
| `world_action_bridge_adapter.py:67` | `SUPPORTED_VERBS = ("MOVE","CONSUME","POSE","REST")` |
| `:78` | `resolver_params(verb, target)`：单参数动词照旧（`MOVE`→`destination_location_id`、`CONSUME`→`object_id`），body 动词从编码解出 owner 参数 |
| `:223` | `build_intent` 用 `resolver_params(...)` 组 `params` |
| `:267-280` | 编码不全 → `BODY_ACTION_TARGET_INVALID` + `DEFINITE_PRE_SUBMISSION_FAILURE`，**授权未签发、owner 未被联系** |
| `:400-415` | 成功回读 owner 快照的 `pose`/`orientation`；`REST` 额外标 `command_success_is_not_recovery: True` |
| `world_journey_action_adapter.py:73` | `DELEGATED_VERBS = ("CONSUME","POSE","REST")`，`SUPPORTED_VERBS` 仍是 `("MOVE",)`：`POSE`/`REST` 不是旅程，交给同一正式链 |
| `service/bridge/action_intent.py:76`/`:80` | B6E1：`EXPOSED_VERBS`/`ACTION_ALLOWLIST` 窄增 `REST`（其余清单原样） |
| `:86-88` | 准入集就是窄的：`REST_STATES=("start","stop")`、`REST_MODES=("rest",)`、`REST_PARAMS=("mode","state")` |
| `:132-172` | `validate_verb_params(verb, params)`：只对 `REST` 生效，缺字段/多字段/越界 state/`sleep_like` 一律 `ActionIntentError`；其他动词原样返回（不开自由 params） |
| `:447-451` | `build_submission` 在 allowlist 之后立刻调它，并用它的结果当提交参数：授权票就是按这套精确参数签发的 |

UNKNOWN 原键/参数绑定不变（`execution_id_for` 仍由 AR-0 idempotency key + verb + target 派生），
没有新 ledger，不从手机 owner 代签主体。

## 4. 验证（每一代日志唯一、失败保留）

| 组 | 命令 | 结果 | 日志（scratch，永久保留） |
|---|---|---|---|
| 新用例（POSIX/WSL，全 12 条） | `python3 tests/test_day_life_body_actions.py -v` | **exit 0**，`Ran 12 tests … OK`（3.2s） | `w-bodyact-wsl-20261018-213844.log` |
| 新用例（原生 Windows） | `python tests/test_day_life_body_actions.py -v` | **exit 0**，2 过 / 10 skip（`No module named 'fcntl'`，与既有 Life 用例同一限制） | `w-bodyact-win-20261018-213857.log` |
| 直接受影响：机会源真网关组 | `python3 tests/test_world_daily_life_play.py` | **exit 0**，4 tests OK | `w-bodyact-affected-play-20261018-213906.log` |
| 直接受影响：bridge/journey 真链组 | `python3 tests/test_world_neighborhood_owner_chain.py` | **exit 0**，3 tests OK（53.5s，真 socket/真子进程） | `w-bodyact-affected-bridge-20261018-213929.log` |
| 直接受影响：`test_world_daily_life_consume.py` | 同上 | **红，且与本包无关**：`autonomous_life_cycle.py:5252 NameError: OUTCOME_STEP_UNSETTLED` —— B 正在改的 cycle 里该常量被引用 9 次但尚未定义，导入期即失败，我的代码还没被执行到 | `w-bodyact-affected-consume-20261018-213913.log` |

| **B6E1 第 1 代**（受影响 3 条 exact ID） | `python3 tests/test_day_life_body_actions.py BodyActionContractTests.test_rest_param_contract_is_narrow_at_the_submission_boundary RealWorldBodyActionChainTests.test_rest_clears_the_intent_allowlist_and_stops_at_the_owners_availability RealWorldBodyActionChainTests.test_sleep_like_never_reaches_the_owner -v` | **exit 0，2 过 1 红**：红的是我自己写错的 lifecycle 期望（少了 `SUBMITTED` 一段），不是产品行为；日志保留 | `w-bodyact-b6e1-20261018-215722.log` |
| **B6E1 第 2 代**（同上 3 条，修断言后） | 同上 | **exit 0**，`Ran 3 tests … OK`（1.9s） | `w-bodyact-b6e1-fix-20261018-215750.log` |
未跑：旧整套、`discover` 继承、全树 SHA、24h、任何需要 provider/密钥的运行。


## 5. 具体限制与具名阻塞（不粉饰）

1. **REST 的上游 allowlist 已闭合（B6E1），但"到 Resolver 后 `RECOVERY_NOT_NEEDED`"在本架构里不可能。**
   背景：`service/bridge/action_intent.py:65`/`:69` 的 `EXPOSED_VERBS`/`ACTION_ALLOWLIST` 原本没有
   `REST`，于是 `build_submission` 抛 `ActionIntentError` → `REJECTED / INVALID`，Life 的新接线
   停在上游。B6E1 之后这两处窄增 `REST`，并新增 `validate_verb_params`（`:132-172`）把参数钉成
   `state ∈ {start, stop}` + `mode = rest`。
   真实链证据（`test_rest_clears_the_intent_allowlist_and_stops_at_the_owners_availability`）：
   lifecycle 走完 `CREATED → AUTHORIZED（wauth:… 票 + target={"state":"start","mode":"rest"}）
   → SUBMITTING → SUBMITTED → REJECTED(NOT_EXPOSED)` —— 请求**真的**到了 owner 手上，
   拒绝理由不再提 allowlist。
   剩下的那道门是 owner 自己的可用性，而且它是**故意**在 Resolver 之前的：
   `world_body_gateway_api.py:1261-1273` 先按 `get_available_actions` 的 `available` 集合拒绝
   （`NOT_EXPOSED`），`world_body_service.py:495-498` 的 `_rest_body_gate`（`:402-453`）才产出
   `RECOVERY_NOT_NEEDED`，而它同样在 `resolver.resolve` 之前。两者逻辑互补：
   `RECOVERY_NOT_NEEDED` 成立 ⟺ `needs == False` ⟺ `start_available == False`
   ⟺ `REST.available == False` ⟺ gateway 先答 `NOT_EXPOSED`。
   所以"低负荷请求在 Resolver 之后拿到 `RECOVERY_NOT_NEEDED`"需要同时放宽曝光策略和把身体门槛
   搬进 Resolver —— 前者会废掉"不能绕过 get_available_actions"，后者违反"阈值留在 Body owner"
   的既有裁定（`2026-10-21-v04-world-body-resident-and-rest`）。本包两件都没做，
   也没有靠改 fatigue/阈值/时钟去凑。可验证的最强事实就是：**intent 层已放行并提交，
   拒绝来自 owner 的可用性；Body preflight 对同一条命令的回答是 `RECOVERY_NOT_NEEDED`**（见第 3 条）。
2. **REST 正向（真实 start/stop）本轮无法真实建立。**
   Body 的门槛是 `RECOVERY_OPEN_FATIGUE = 0.30`（`body_runtime.py:221`），基线 `0.15`，
   负荷按 `load_units * 0.35 / 小时` 涨。短时间内让她真的累到 0.30 需要高负荷折磨，
   而「直改 fatigue / 阈值 / 时钟」被明令禁止 —— 所以本包**没有**任何"真实恢复正例"，
   也不声称有。REST 的 start/stop **命令参数**（编码 → `params` → intent）由第 8 条契约用例
   在隔离层固定；**机制成立，真实恢复未验证**。
3. **REST 的诚实拒绝现在有三层真实路径（都验证了，零副作用）**：
   intent 参数契约 → `INVALID`（缺字段/多字段/越界 state/`sleep_like`，授权未铸、owner 未联系）；
   gateway 曝光面 → `NOT_EXPOSED`（owner 的 `available` 裁决，`world_unchanged`/`body_unchanged`）；
   Body preflight → `RECOVERY_NOT_NEEDED`（`source: body_preflight`，`applied_to_body: False`）。
   `stop` 在没有开会话时：链上 `NOT_EXPOSED`，owner 自己的 preflight 答 `NO_OPEN_RECOVERY`。
4. **`noop` 与 `committed` 的既有粗糙度（World client，不是本包）。**
   同一姿态重复 POSE，owner 回 `noop`，但 `world_body_client.submit_action` 把
   `applied/noop/already_applied` 都归成 `SUCCESS`，bridge 因此报 `committed`。
   所以第 9 条用例的判据是**基底 revision 不再前进**，不是 bridge 的标签。
   机会源那一侧则从源头避免这种空转：当前姿态根本不出候选。
5. **B 已接线并经父级真实链验证 POSE。** `bodyact:` 的候选、step、LR-5 判据已接入cycle，复用本包三条接口。父级gen2发现cycle用动作前snapshot判定的实错（action SUCCEEDED但STEP_UNSETTLED）；B定点改为动作后fresh owner回读，`b-gen5-bodyfix.log` 三条精确用例exit0。父级gen3 `day-life-worker-first-1792332458187633914.json` 中POSE返回ACTED，canonical观察为lying/south、`re_read=true`、world_revision=1。整条两进程链仍在后续安静抑制处失败，不能据此宣称正常一天。
6. **枚举是副本。** `POSES`/`ORIENTATIONS`/`REST_STATES` 是 owner 公开词表的拷贝，只用于提交前
   拒绝不完整编码；owner 扩词表时这里必须同批扩，否则新词会被 fail-closed 拒掉（这是有意的方向）。
7. **`transitioning`**：owner 的 `POSES` 里有它，机会源严格照 owner 枚举出（除当前姿态外），
   没有额外过滤 —— 是否要把它从可选项里去掉，属于 owner 的词表问题。
8. **allowlist 有两份同内容副本，本轮只改了 bridge 那份。**
   `components/world/service/action_intent.py` 与 `components/world/service/bridge/action_intent.py`
   原本字节相同；真实链只经 **bridge** 那份（`bridge/intent_executor.py:50/75/156`、
   `bridge/decision_trigger.py:235/272/326` 都是 `import action_intent`，而 bridge 目录在
   `sys.path` 前），service 那份只被 `components/world/service/test_action_intent.py` 与
   `test_world_move_candidates.py` 自己导入。写集只授权了 bridge 那份，所以两份现在**不同**
   （service 副本仍无 `REST`，它自己的单测因此仍然绿）。新用例用
   `ai.__file__.endswith("components/world/service/bridge/action_intent.py")` 把这条事实钉住，
   避免"改了看不见的副本"。要不要合并/删除副本属于 World owner 的决定，不在本包。
