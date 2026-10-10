# v0.2 B6 / E08 勘察：一次真实 World MOVE 的调用链与两个真实阻断

**性质：只读勘察记录。** 分支 v0.2-autonomous-life，基线 HEAD 4de4f5e。行号来自本次实际读取。
本文件是 B6 的起点：接线尚未实现，两个阻断必须先处理。

---

# B6 勘察报告：自主 Life 轮次 → 真实 World MOVE 的完整调用链

## 0. 环境硬约束（先看这条）
- `fcntl` 在三个关键模块是**模块级无条件 import**：`world_action_authorization.py:15`、`world_body_substrate.py:41`、`world_action_resolver.py:38`。→ 整条 MOVE 链在 Windows 上无法运行，真实验证必须在 Linux/WSL 上做。（代码里看到的）

## 1. 隔离世界实例如何启动
- **只读包装（发布包里实际在跑的）**：`chiyo_bundle/world_read_service.py`
  - 初始化 `initialize(home)` :37-55；要求 home **不存在**（:43-44）、且在源码树之外（:40-41）；写 `data/world/world_state.json`（:47-51）、`world_timeline.json`（:52-54）、`data/life_execution.jsonl`（:55）。
  - 运行 `serve(home)` :58；`load_config({}, runtime_root=ROOT/'components/world', world_home=..., runtime_mode='canonical', authority='production')` :62-63；`WorldBodyService(config, tick_seconds=1)` :64；只 bind `config.run_dir/'read.sock'` :65、`chmod 0600` :99；对端 uid 必须 `== os.getuid()` :20-27,:111；只转发 `GATEWAY_READ_COMMANDS` :30-34。
  - CLI :145-150：`python -m chiyo_bundle.world_read_service init|run --home <ABS>`
  - **它没有 gateway.sock、没有 submit_action → 不能 MOVE。**
- **能 MOVE 的进程是 `world_body_service.py` 本身**（`_cli` :836-896，command ∈ run|check|status :839，参数 `--runtime-root/--world-home/--tick-seconds/--max-seconds` :841-844）。
  - 它 bind 两个 socket：`control.sock` + `gateway.sock`（`serve()` :764-765），gateway 面才含 `submit_action`。
  - 可运行样例（POSIX，`world_home` 用绝对路径）：
    ```
    python components/world/service/world_body_service.py run \
      --runtime-root "$PWD/components/world" --world-home "$HOME/.chiyo-world-v1" --tick-seconds 60
    ```
    等价环境变量（`world_body_config.py:31-43`）：`CHIYO_WORLD_RUNTIME`、`CHIYO_WORLD_HOME`、`BODY_RUNTIME_MODE=canonical`、`CHIYO_WORLD_BODY_AUTHORITY=production`、`CHIYO_BODY_READER_MODE=canonical`、`CHIYO_BODY_RUNTIME_ENABLED=1`、`CHIYO_WORLD_BODY_SUBSTRATE_ENABLED=1`、`CHIYO_WORLD_LIVING_SKELETON_ENABLED=1`。canonical 强制要求 authority=production（:263-267）；`apply_process_environment` :284-297 在 startup 第 1 步 :178 执行，`owned_environment()` :166-183 把 `HERMES_HOME` 指向 world_home。
  - 数据目录表达式：world_state `<home>/data/world/world_state.json`（:137-138）、ledger `<home>/data/life_execution.jsonl`（:145-146）、body `<home>/data/body`（:149-150）、run `<home>/run`（:153-154）、substrate `<home>/data/world/body_substrate.json`（`world_body_substrate.py:58`）。
  - 拒绝：home 不得等于 `LIVE_GATEWAY_HOME`(=`<repo>/data/persona`) 或代码树（:49-50, :250-261）。
- 生产模板（真实命令 + 全 shadow 环境）：`components/world/deploy/chiyo-world-body.service:11-20`。

## 2. 真实地点目录（`world_living_skeleton.py:53-102`，共 6 个 place）
| id | name | kind | area_id | reachable |
|---|---|---|---|---|
| `home` | 千代的家 / 房间（:56 转义） | home | bedroom | `["nearby_street"]` :60 |
| `nearby_street` | 家附近街道 :64 | street | nearby_street | `[home, school, convenience_store, park, home_goods_store]` :68 |
| `school` | 学校 :72 | school | school | `["nearby_street"]` :76 |
| `convenience_store` | 便利店 :80 | store | convenience_store | `["nearby_street"]` :84 |
| `park` | 公园 :88 | park | park | `["nearby_street"]` :92 |
| `home_goods_store` | 家居用品店 :96（唯一非转义中文） | store | home_goods_store | `["nearby_street"]` :100 |
- 初始位置：`home`（`world_read_service.py:50` place_id='home'/area='bedroom'；测试种子同 `test_world_body_service.py:39`）；`_HOME_LOCATION_ID="home"` :51。
- **没有「书桌」类 place**。只有对象 `simple_desk`（`world_property_economy.py:99-101` catalog item）和事实名 `bedroom.desk_present`（`world_home_space.py:68`）——都不是 place。

## 3. 一次 MOVE 的完整参数形状
- `WorldResolver.resolve(proposal, *, crash_after=None)` `world_action_resolver.py:634`。
- 允许键 `_PROPOSAL_KEYS` :286-297 = `action, execution_id, actor_id, body_id, authority, confirm_apply, observed_dependencies, params`；未知键直接 `ResolverError` :306-308。必需 `action, execution_id` :309-311。
- `action` ∈ `ACTIONS=("MOVE","PICK_UP","PLACE","POSE")` :166,:314；MOVE 的 params **只准** `{destination_location_id}`（`_require_params` :341-344，调用点 :793）。
- 授权硬门槛：`authority == "verified_main_self_tool"`（:53,:661-665），且 `confirm_apply` 为真（:666-670）。
- `observed_dependencies` 是**子集即可**（`_stale_keys` 只遍历 observed 的键 :620-630）；`_dependencies()` 产生的全集 :596-618 = `world.revision, world.location, body.pose, body.slot.left_hand, body.slot.right_hand`。
- 真实可用样例（抄自 `test_object_action_m15.py:107-115`）：
  ```python
  service.resolver.resolve({
      "action": "MOVE",
      "execution_id": "world-exec:m15-move-1",
      "actor_id": "chiyo", "body_id": "chiyo_body",
      "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,   # "verified_main_self_tool"
      "confirm_apply": True,
      "observed_dependencies": service.resolver._dependencies(),
      "params": {"destination_location_id": "nearby_street"},
  })
  ```
  另一份最小样例：`test_world_body_service.py:536-540`（`destination_location_id: "nowhere_at_all"`，只断言不是 FEATURE_DISABLED）。MOVE **不走 `_substrate_gate`**（只有 PICK_UP/PLACE/POSE 走 :910-934）——这是最便宜的真实变更。

## 4. 一次性授权：签发与消费
- `AuthorizationRegistry.__init__(run_dir, *, ttl_seconds=DEFAULT_TTL_SECONDS=120.0)` :57-59；文件 `<run_dir>/action_authorizations.json` :28，写 `0600` :78，保留最后 256 条 :140-141。
- `issue(*, decision_id: Optional[str], intent_id: str, intent_fingerprint: str, execution_id: str, actor: str, verb: str, params: Mapping, authority_generation: Any, ttl_seconds: Optional[float]=None, now: Optional[float]=None) -> dict` :99-112。
- `verify_and_consume(*, authorization_id: str, execution_id: str, verb: str, params: Mapping, actor: str="chiyo", decision_id: Optional[str]=None, now: Optional[float]=None) -> dict` :145-155。失败原因（全大写，`AuthorizationError`）：`AUTHORIZATION_NOT_FOUND / ALREADY_CONSUMED / EXPIRED / EXECUTION_ID_MISMATCH / VERB_MISMATCH / ACTOR_MISMATCH / DECISION_MISMATCH / ARGS_MISMATCH` :167-186。
- **issue 唯一调用点**：`bridge/intent_executor.py:258-267`（注释 :230-231 明说「唯一创建处」）。run_dir 解析 :242-256：`WORLD_ACTION_AUTH_REGISTRY_DIR` → 否则 `CHIYO_WORLD_HOME`/`HERMES_HOME` + `/run`；不可解析则不提交（:248-255）。
- **verify_and_consume 唯一调用点**：`world_body_gateway_api.py:559-568`（服务侧）。
- 副本差异：`bridge/world_action_authorization.py` 与 `service/world_action_authorization.py` 只差 `_iso()`（bridge 用 UTC ISO，service 用 `+08:00`）；`authorization_id` 摘要由签发方算一次、消费方只比字符串，故无功能影响。`bridge/action_intent.py` 与 `service/action_intent.py` 字节相同。

## 5. `GatewaySurface.submit_action(request)` 的 request 形状与盖章
- 键：`action`（或 `verb`）:531、`execution_id` :532、`params` :533、`observed_dependencies` :536、`authorization_id` :552、`decision_ref` :567。
- **拒绝自带 authority**：请求里出现 `"authority"` 或 `"confirm_apply"` → 直接 `GatewayAPIError`（:541-544）。
- 服务盖章（:575-584）：`authority="verified_main_self_tool"`（`GATEWAY_AUTHORITY` :73）、`actor_id="chiyo"`（:74）、`body_id` 取 `service.body_state["body_id"]`、`confirm_apply=True`、`observed_dependencies` 排序后透传。
- 校验顺序：registry 不存在 → `AUTHORIZATION_REGISTRY_UNAVAILABLE` :547-551；无 authorization_id → `AUTHORIZATION_REQUIRED` :552-557；然后 `verify_and_consume`（带 `decision_id=request.get("decision_ref")`）:559-568 → 失败返回 `_reject_authorization` :623-639（`world_unchanged/body_unchanged=True`）；再查 exposure :588-600（`NOT_EXPOSED`）；最后 `self.service.act(proposal)` :602。
- **SO_PEERCRED 要求的 uid**：模块 docstring 写 `0`（:88-90），但**真实执行**是 `GATEWAY_ALLOWED_PEER_UID = os.geteuid()`（`world_body_service.py:73`）+ `gateway_peer_allowed(uid)` :76-84，在 serve 循环里检查 :786-808。即「与服务进程同一 uid」（部署里是 root）。拒绝回包：`{"ok": false, "error": "unauthorized_peer", "detail": "gateway surface requires uid 0"}` :799-803。socket 权限 0600（`_bind_socket` :656-689）。

## 6. 从 Life 侧打到 world owner 的最窄路径
- **Life 的 AR-0 路径是死的**：`life_runtime_production.py:354` 装配 `ag2.WorldActionAdapter(RefusingWorldResolver())`，`RefusingWorldResolver.resolve_action` :115-118 直接抛 `ProductionExecutionRefused("LPC0A_WORLD_ACTION_EXECUTION_DISABLED…")`；AG-2 的调度在 `life_integration_ag2.py:5861`（`ACTION_KIND_WORLD: self.world_adapter`），AR-0 的 `WorldActionAdapter.submit` :1767-1784 调 `resolver.resolve_action(idempotency_key, verb, target, expected_dependencies, fault_hook)`——**与 `WorldResolver.resolve(proposal)` 签名不同**，插真实 resolver 需要一层翻译（`ActionCommandService.submit_action` :2675-2685 是唯一入口）。
- **最窄真实路径（两个进程、一个 socket）**：
  1. 进程 A（gateway/插件侧）：`intent_executor.execute_intent(intent, client=WorldBodyClient())` `bridge/intent_executor.py:148` → gate `action_intent.resolve_gate()` :325 → 读 `get_status`/`get_snapshot` :189-203 → `ai.build_submission` :206 → `registry.issue(...)` :258 → `client.submit_action(action=, execution_id=, params=, observed_dependencies=, authorization_id=, decision_ref=)` :279-286。
  2. `WorldBodyClient.submit_action` `bridge/world_body_client.py:265-330` → `_call_once` :173-206 → `AF_UNIX` 连 `WORLD_BODY_SOCKET`（默认 `./data/world/run/gateway.sock` :34,:64-66），发送 `{"command":"submit_action","argument":payload}`。
  3. 进程 B：`WorldBodyService.serve` :748 → `_serve_connection(surface="gateway")` :709-712 → `GatewaySurface.handle("submit_action", arg)` :669 → `submit_action` :519 → `service.act` :360 → `WorldResolver.resolve` :369/:634 → `world_living_skeleton.plan_move_to` :574 / `apply_move_to` :704。
- 需要的开关（名 / 默认值 / 读取位置）：
  - `WORLD_BODY_INTEGRATION_MODE`（默认 `off`，`world_body_client.py:28,54-61`；客户端 `enabled` :169-171）
  - `WORLD_BODY_SOCKET`（默认 `./data/world/run/gateway.sock`，:29,:34,:64-66）
  - `WORLD_BODY_INTENT_MUTATION_GATE`（默认 `off`，必须 `canonical` 才提交；`action_intent.py:72-76,325-332`）
  - `WORLD_ACTION_AUTH_REGISTRY_DIR`（可选；缺省回落 `CHIYO_WORLD_HOME|HERMES_HOME` + `/run`，`intent_executor.py:242-247`）
  - `WORLD_ACTION_LIFECYCLE_PATH`（默认 `<CHIYO_WORLD_HOME|HERMES_HOME>/run/action_lifecycle.jsonl`；**写不进就 NO_PROVENANCE 不提交**，`world_action_lifecycle.py:62,76-87`，`intent_executor.py:160-172`）
  - `CHIYO_WORLD_HOME`（签发侧定位 run 目录用）

## 7. 三个开关的真实语义
| 开关 | 默认 | 读取处 | 语义 |
|---|---|---|---|
| `WORLD_BODY_MUTATION_MODE` | `off`（非法值也回落 off） | 定义 `bridge_init.py:59`；读 `_mutation_mode()` :129-131；强制 :274-277（工具返回 `INVALID/mutation_disabled`）与 `_check_world_body_action` :290-292 | 只决定 LLM 工具 `world_body_action` 是否暴露（`bridge_init.py:21-24, 313-316`）。**不影响 intent 链**。 |
| `WORLD_BODY_INTENT_MUTATION_GATE` | `off`；值域 `off/shadow/canonical`（非法值抛 `ActionIntentError`） | `action_intent.py:72-76`，`resolve_gate` :325-332；消费点 `intent_executor.py:47-52,176-182,211-228`、`decision_trigger.py:378`、`action_intent.build_action_space:151-154,197-198` | off=拒绝且零校验；shadow=全量校验+构造 submission 但**不提交**（返回 `SHADOW_NO_COMMIT`）；canonical=真提交。 |
| `WORLD_BODY_PRODUCTION_EVENTS` | `off`，**只有字面 `on` 为开** | 定义 `decision_trigger.py:68-71`；读 `production_events_enabled()` :191-198；使用 :276-277 | 允许由身体自身运行时事件（`self_maintenance_policy.observe` :288-289 → `pose:lying` :295）产生 Action 候选；仍需 gate=canonical。 |

## 8. 持久化文件与重启恢复
- world：`<world_home>/data/world/world_state.json`（`world_body_config.py:137-138`；`world_action_resolver.py:535-540`；`world_living_skeleton.py:146-147`）
- substrate：`<world_home>/data/world/body_substrate.json`（`world_body_substrate.py:58,284-290`）
- ledger：`<world_home>/data/life_execution.jsonl`（`world_body_config.py:145-146`）
- body：`<world_home>/data/body/`（`world_body_service.py:121-123`）
- run：`<world_home>/run/`（lease marker、`control.sock`、`gateway.sock`、`action_authorizations.json`、`action_lifecycle.jsonl`）
- 恢复函数：`WorldBodyService.startup()` `world_body_service.py:164-315` —— 读 world_state（:209-229）→ substrate+binding 校验（:232-242）→ ledger verify（:244-250）→ body load 或首次 bootstrap（:252-270）→ `bct.migrate_all`（:273）→ `reconcile_now(persist=True)`（:281 → :320-340 `world_body_reconcile.reconcile_startup`）→ bounded catch-up（:288）。MOVE 本身无内存态，重启后即从 world_state.json 读回。

## 9. 不可达 / 条件不满足时谁拒绝、返回什么
- 网关 exposure：`{"status":"rejected","reason_code":"NOT_EXPOSED"}` `world_body_gateway_api.py:590-600`。
- 授权防火墙：`:623-639`，`reason_code` 为第 4 节列出的那 10 个字符串。
- Resolver（`_resolve_move`）真实状态串：
  - 不可达 `:831-836` → `status="rejected"`, `reason_code="LOCATION_NOT_REACHABLE"`, `code="NOT_REACHABLE"`（`CODE_NOT_REACHABLE` :69）
  - 未知地点 `plan` :592 → `reason_code="UNKNOWN_LOCATION"` → `CODE_UNKNOWN_TARGET`
  - 世界特性关：plan `status="disabled"` → :820-824 `CODE_FEATURE_DISABLED`
  - 已在目标：plan `status="noop"` → :825-830 `status="noop"`, `CODE_NOOP`
  - 依赖过期：:809-814 `status="rejected"`, `code=CODE_STALE_STATE`, `reason_code="stale_dependency"`，附 `stale_dependencies`
  - 世界未初始化/不可读：:831-836 → `CODE_WORLD_UNAVAILABLE`（reason_code `WORLD_NOT_INITIALIZED`）
  - 授权缺失：:661-670 `CODE_AUTHORITY_REQUIRED`（`verified_main_self_required` / `confirmation_required`）
  - 状态字面量集合见 `TERMINAL_SUCCESS/FAILURE/IN_FLIGHT` :168-170（`applied/noop`、`rejected/failed/disabled`、`prepared/uncertain`）
- 客户端映射 `world_body_client.py:314-330`：`applied|noop|already_applied→SUCCESS`；`reason_code=="STALE_STATE"→STALE`；`rejected|failed|disabled→FAILURE`；其余→`UNKNOWN`。
- intent 映射 `action_intent.py:400-411`：`SUCCESS→APPLIED`、`FAILURE→REJECTED`、`STALE→STALE`、`GATE_OFF/SHADOW_NO_COMMIT→REJECTED`、其他→`UNKNOWN`。

## 10. 可直接照抄的端到端测试（按参考价值排序）
1. `components/world/service/test_world_action_authorization.py:165-275` `GatewayFirewallTests` —— **最接近 B6**：临时 home 种子 :31-52、`make_config` :55-64、真 `WorldBodyService` + 真 `GatewaySurface` + 真 registry :168-181；`test_authorized_submission_reaches_the_resolver` :258-272 断言 `source=="resolver"`；:202-256 覆盖 5 种拒绝并逐字节比对 ledger/world 未变。
2. `components/world/service/test_intent_executor.py:153-197` `test_gate_canonical_submits_through_the_bridge` —— 真 `execute_intent` + 真 registry + `FakeSocketServer`（:59-100）；断言调用序列 `["get_status","get_snapshot","submit_action"]` 且 payload 带 `authorization_id`。
3. `components/world/service/test_object_action_m15.py:100-145` —— 真 `WorldResolver.resolve` 的 proposal 形状（:107-115）与 `applied/rejected` 断言；配套 `test_world_body_service.py:532-542`（canonical 不得 FEATURE_DISABLED）、`:569-579`（`act` 走 resolver）；`test_decision_trigger.py:206-233`（Decision→Intent→Executor 全链 provenance）。

## 疑点 / 必须补的最窄缺口
1. **时区不一致（真实阻断）**：`chiyo_bundle/world_read_service.py:49` 初始化写入 `'timezone':'Asia/Shanghai'`，而 `world_living_skeleton._load_state` :187 要求 `timezone == WORLD_TIMEZONE == "Asia/Tokyo"`（:42）——不匹配即返回 `None` → `plan_move_to` 返回 `unavailable/WORLD_NOT_INITIALIZED` → **每一次 MOVE 都被拒**。测试种子用的是 `Asia/Tokyo`（`test_world_body_service.py:43`）。B6 必须用 Asia/Tokyo 的 world_state，或先修这一处。（代码里看到的）
2. **bridge 不可达（真实阻断）**：`components/world/service/bridge/` 只有 11 个 .py、**无 `__init__.py`**；全仓无 `bridge_init.register` 的调用方；`chiyo_bundle/instance.py:69-71` 只设 `WORLD_BODY_INTEGRATION_MODE`/`WORLD_BODY_SOCKET`，从不设 `WORLD_BODY_INTENT_MUTATION_GATE` 或 `WORLD_BODY_MUTATION_MODE`。→ 签发授权的那一侧在发布包里根本没有装配点，「Life 轮次 → 真实 MOVE」缺的正是这一环（不是 Resolver，也不是授权表）。（代码里看到的）
3. `gateway.sock` 的 peer 要求是**服务进程自身 euid**（`world_body_service.py:73`），docstring 与日志文案写的却是 `uid 0`（`world_body_gateway_api.py:88-90`、:799-803）；`world_read_service` 用 `os.getuid()`（:111）。三处口径需统一。
4. AR-0 → 真 Resolver 的签名翻译层不存在（`resolve_action(idempotency_key, verb, target, expected_dependencies, fault_hook)` vs `resolve(proposal)`）；若要保留 AR-0 账本，这是第二处必须新写的窄适配。
5. 未验证（未运行代码，只静态读）：`plan_move_to` 在 `CHIYO_WORLD_DAILY_RHYTHM_ENABLED` 打开时会因 `LOCATION_CLOSED` 拒绝（:615-641）；默认关，故样例应保持关闭。