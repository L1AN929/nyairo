# v0.4 A 结果：身体参与与有分寸分享（身体处境进决定 / 分享走真实 owner）

Status: implemented（A 侧；模型真实调用由父级统一做）

写集：`components/life/scripts/autonomous_life_cycle.py`、
`autonomous_life_assembly.py`、`agency_cognition_adapter.py`、`agency_decision_ag1.py`，
新增 `life_body_context.py`、`life_sharing_context.py`；测试
`tests/test_body_context_v04.py`、`tests/test_v04_sharing_cycle.py`；决策笔记
`.agents/notes/implemented/architecture/2026-11-14-v04-body-participation-and-bounded-sharing.md`。
没有修改 B 的文件、AG-2、Phone、alpha contact、config/MANIFEST、两个 fix 脚本、`current.json`。

---

## 1. 精确接口

### 1.1 `life_body_context`（新，A 自己的辅助）

```python
build(*, body_column: Optional[Mapping], rest: Optional[Mapping],
      observed_at: Optional[str] = None) -> dict
empty_context(reason_code: str, *, detail: str = "") -> dict

SCHEMA = "chiyo.life.body_context.v1"
STATUS_OBSERVED / STATUS_PARTIAL / STATUS_UNKNOWN = "observed"/"partial"/"unknown"
RECOVERY_SESSION_OPEN / _CLOSED / _UNKNOWN = "OPEN"/"CLOSED"/"UNKNOWN"
RECOVERY_CLAIM_NOT_ASSERTED = "NOT_ASSERTED"   # 恒为此值
RECOVERY_PROGRESS_NOT_READ  = "NOT_READ"
EASING_SIGNAL_TYPES        # 只有 recovery_underway / tension_releasing / activation_recovering
REASON_{OK_OBSERVED,OK_PARTIAL,OK_UNKNOWN,BODY_COLUMN_ABSENT,BODY_COLUMN_INVALID,BODY_VIEW_UNBOUND}
UNKNOWN_{BURDEN_IS_NOT_HEALTH,RECOVERY_SESSION,PROGRESS,REST_CAPABILITY}
```

返回（有界、定性、无数字）：

```python
{"schema","status","reason_code","detail","source","observed_at","body_id","revision",
 "freshness","capacity","capacity_ref",
 "burden":[{"signal","band"}...<=4],            # 只从 BURDEN_SIGNAL_TYPES
 "easing":[{"signal","band"}...<=3],            # 只 trend；两个热感信号不带
 "recovery":{"session":"OPEN|CLOSED|UNKNOWN","state":<owner word>|None,
             "claim":"NOT_ASSERTED","progress":"NOT_READ"},
 "rest":{"can_start":bool|None,"can_stop":bool|None,"sleep_like":bool|None,
         "why_not":<owner blocked reason>|None,"pose":...,"rest_surface":...,"travelling":...},
 "unknowns":[...],"limits":{"reads_performed":0,"raw_values_exposed":False,...},
 "not_authority":{"candidate":False,"action":False,"grant":False,"instruction":False}}
```

输入形状两种都接受且**不重新推导**：`life_situation_view` 的 body 列（`state` / `ref`）与原始
`body_capacity_source` 视图（`body_capacity_state` / `body_capacity_ref`）。
`body_column=None` → `BODY_SITUATION_NOT_READ`；无 `state` → `BODY_SITUATION_COLUMN_INVALID`。
`recovery.session` 只来自 World owner 的 `REST.current.recovery_state`
（`active`→OPEN；`inactive|completed|interrupted`→CLOSED；其它→UNKNOWN）。

### 1.2 cycle 新增的读取面

```python
AutonomousLifeCycle(..., sharing_service=None)
cycle.body_decision_context(situation=None, *, observed_at=None) -> dict     # 同一次读派生
cycle.shareable_sources(*, limit=3) -> [{"artifact_id","kind"}]              # current_work / reading_material
cycle.sharing_decision_context(*, limit=5) -> dict                          # 见 1.3
cycle._apply_sharing_action(decision_record) -> dict|None                   # 见 1.4
cycle.last_body_context / last_sharing_block / last_sharing
```

`decision_situation_context()` 现在多两块：`["body"]`（1.1）与 `["sharing"]`（1.3）。
`run_round`（`evaluate_once`）在 Decision 提交后、本轮 step 之前调用一次
`_apply_sharing_action(record)`，结果进 `round["sharing"]`；**不改变本轮路由**。

### 1.3 `life_sharing_context`（新，A 到 B 的窄缝）

```python
service_from_env(environment) -> (service|None, reason|None)      # 走 B 的 sharing_from_env
prompt_block(service, *, limit=5) -> dict                         # 一次 list_shares(include_text=False)
read_draft(service, share_id) -> dict                             # read_share(include_text=False)
prepare_draft(service, *, source_artifact_id, share_text=None, excerpt_chars=None,
              title=None, expires_after_s=None, operation_key=None) -> dict
apply_request(service, *, share_id, action, expected_head,
              defer_condition=None, opportunity_ref=None, reason=None, operation_key=None) -> dict
report(answer, *, share_id=None) -> dict
is_share_title(title) -> bool        # B 的标记，A 用来过滤"分享件不是作品"
open_share_ids(block) -> [share_id]
MAX_PROMPT_SHARES = 5; REASON_NOT_WIRED = "SHARING_SERVICE_NOT_WIRED"
```

`prompt_block` 的输出：`{schema,status,reason_code,channel,audience,shares[{share_id,state,title,
head_version_id,updated_at,delivered,source_artifact_id}],counts,inbox{delivered_count,local_only},
truncated,unknowns,note}`；cycle 再补 `sources`、`allowed_share_ids`、`how`。
`report()`：`{ok,status,reason_code,share_id,state,previous_state,head_version_id,share_revision,
delivered,state_delivered,delivery_count,receipt{...,local_only,external_channel_sent,
external_delivery_confirmed,read_by_audience,understood},deferred,dropped,reconciled,retryable}`；
`delivered=True` 只在**这一次**真的落地（`ok` + `applied|already_applied` + `LOCAL_DELIVERED`），
`state_delivered` 是分享自身的状态事实。

### 1.4 AG-1 的输出扩展

```python
validate_sharing_action(raw) -> {"op": "prepare", "source_artifact_id", "share_text",
                                 "excerpt_chars", "title", "expires_after_s"}
                              | {"op": "send_local|defer|drop", "share_id",
                                 "expected_head", ["defer_condition"|"opportunity_ref"|"reason"]}
DecisionRecord.sharing_action: Optional[dict]        # 随记录 to_dict/from_dict
```

拒收：非 `prepare/send_local/defer/drop` 的 op、未知键、`audience/channel/recipient/to/email/url/
telegram/api`、`share_id` 非 `artifact:`、`expected_head` 非 `artifact-version:`、
`prepare` 既无 `share_text` 又无 `excerpt_chars`、`excerpt_chars` 不在 1..400、
`defer` 两个条件都没写。`validate_decision_output` 的允许键集合加入 `"sharing"`。

### 1.5 assembly

```python
resolve_sharing_service(environment) -> {"service", "status": "READY|UNAVAILABLE",
                                         "reason", "service_type"}
assemble_life(...) 返回 dict 里多 "sharing": {status, reason, service_type}
```

`READY` 就是 B 的 `sharing_from_env(env)` 建出来的对象；缺 workspace/grant/socket 一律
`UNAVAILABLE` + 具名 reason，没有替身收件箱。

---

## 2. 真实链（这一包真正做了什么）

1. **身体**：`read_situation()` 一次真 `WorldBodyReadPort.body()` → `life_situation_view`
   body 列 → `life_body_context.build(..., rest=<gateway REST 条目>)`。决定模型因此看到
   `capacity/burden/recovery.session/rest.why_not`；`recovery.claim` 恒 `NOT_ASSERTED`。
   姿态/休息仍然是 World 的 `POSE`/`REST`（`bodyact:` + AR-0 + Resolver，rc1 既有），A 没改。
   `REST` stop 后 owner 回 `completed`，身体处境照实报"没有开着的会话"，**不**报恢复。
2. **分享**：每轮 decision 读一次 `list_shares`（本地筛 DRAFT/DEFERRED，`sources` 来自本轮
   真实持有物）；Decision 可另带一条 `sharing`；提交后一次 owner 调用
   （`prepare_share` 或 `apply_action`），成员资格先在本轮 offer 里查
   （`SHARING_SOURCE_NOT_OFFERED` / `SHARING_DRAFT_NOT_OFFERED`，不碰 owner），其余由 owner 判
   （`conflict` / `stale_source` / `SHARE_ALREADY_DELIVERED` / `SHARE_TERMINAL_STATE`）。
   `LOCAL_DELIVERED` 只当本地投递一条 receipt；没有外发，`Production RefusingMessageProvider`
   / PROACTIVE kill switch 一行没碰。

---

## 3. 证据（命令、exit）

全部在 WSL（`python3` 3.12，`PYTHONDONTWRITEBYTECODE=1`），夹具根只建不删。

| 命令 | 结果 | exit |
|---|---|---|
| `python3 tests/test_body_context_v04.py` | `Ran 18 tests ... OK` | 0 |
| `python3 tests/test_v04_sharing_cycle.py` | `Ran 15 tests ... OK` | 0 |
| `python3 tests/test_lived_day_core_v04.py`（相邻，prompt 裁剪/现场） | `Ran 26 tests ... OK` | 0 |
| `python3 tests/test_v04_research_life_stage.py`（相邻，决定处境） | `Ran 17 tests ... OK` | 0 |
| `python3 tests/test_v04_practice_life_stage.py`（相邻） | 19 tests，**1 FAIL**（形状见 §5） | 1 |

`tests/test_body_context_v04.py` 覆盖：定性投影（档位/会话/无恢复保证/无预算无原始值）、
无信号不是健康、读不到不是健康、`REST` 缺失是具名 unknown、stop 后 CLOSED 仍 `NOT_ASSERTED`、
热感不冒充 easing、超宽输入被 adapter 有界压回、身体块在结构化裁剪里与现场一起留到最后、
系统提示三句话、cycle 用**同一次读**同时给 capacity 与决定处境（fake source 调用计数 = 1）、
无身体读时具名 UNKNOWN、暂停的 Activity 与 due 的稍后事项仍可见（重新认领的输入）；
真链（真实 `WorldBodyService`+gateway+Resolver，夹具一次 `seed_body(fatigue=0.60)` 明示是脚本
选择）：`POSE seated` → `REST start`（owner 自己开门，`RECOVERY_ALREADY_ACTIVE`，会话 `active`）
→ 真读回 body 信号与 REST 条目 → 身体处境 `session=OPEN`、`claim=NOT_ASSERTED`、
capacity 不是 `AVAILABLE`、`fatigue_noticeable` 真在信号里 → `REST stop`（`{"state":"stop"}`）
→ owner 回 `completed`、处境 `CLOSED` 且**仍然** `NOT_ASSERTED`；另有一条低疲劳
（0.05）确认 owner 说 `RECOVERY_NOT_NEEDED` 时处境不假装健康。

`tests/test_v04_sharing_cycle.py` 覆盖：AG-1 形状（含 `audience/channel` 拒收、
`expected_head` 前缀、`excerpt_chars` 边界、`defer` 条件、未知键）+
`validate_decision_output` 真入口带 `sharing` 并在 `sharing_action` 里落地 + 自由收件人被拒；
prompt（草稿 id/state/CAS head/sources 进 payload；正文永不进；分享块可被整块丢弃而选择仍可能）；
真 Supply `ManagedArtifactStore` 链：`prepare`→DRAFT（≠已发）→下一轮仍能看到同一 head →
`defer(opportunity_ref)`→旧 head `send_local` 得 owner 的 `conflict`→用新 head `send_local`
得 `LOCAL_DELIVERED`、`delivery_count=1`、receipt `local_only=true` /
`external_channel_sent=false` / `external_delivery_confirmed=false` / `read_by_audience=false` →
再读同一条 `delivery_id`、`understood=false` → 重复发送被拒（`SHARE_ALREADY_DELIVERED` /
`SHARE_TERMINAL_STATE`）且 `delivered=false` → 收件箱 `delivered_count=1`、`allowed_share_ids=[]`；
`drop` 后 `DEFERRED` 复活被拒（`SHARE_TERMINAL_STATE`）；来源换版后发送得
`stale_source`；未 offer 的 source/share_id 到不了 owner；无 owner 时 block 具名不可用、
动作报 `SHARING_SERVICE_NOT_WIRED` 且 `delivered=false`。

---

## 4. 父级使用办法（真实模型一次）

- 装配：`assemble_life(env)`（或你们现用的入口）→ 返回 dict 的 `"sharing"` 字段说明是否
  wired；`cycle.sharing_decision_context()` 单独可读，无需模型。
- 模型路由仍是 `custom/cn:deepseek-v4.1-flash`（父级统一调用，本切片**没有**任何 HTTP）。
- 一次真实验收建议：同一个实例上让模型在一轮里回答带 `sharing` 的对象（例如
  `{"op":"prepare","source_artifact_id":"<本轮 sources 里的那个>","share_text":"..."}`），
  然后查 `round["sharing"]`：`status=applied`、`state=DRAFT`、`delivered=false`；
  下一轮用 `read_share`/`sharing_decision_context()` 看同一 `head_version_id`；再一轮
  `send_local` 后 `round["sharing"].receipt.local_only=true` 且 `delivery_count=1`。
  模型答 `NO_ACTION`（不带 `sharing`）同样合法，`round` 里不会出现 `sharing` 键。
- 判据不要用"她说了发送"；只看 `round["sharing"]` 里 owner 的 `status/state/receipt`。

---

## 5. 未完成 / 失败形状（照实写）

1. **没有真实模型调用**（协议：模型由父级统一做），因此"模型会不会真的用这套字段"未验证；
   本包只证明形状、prompt 内容与 owner 往返。
2. **`tests/test_v04_practice_life_stage.py` 1 个 FAIL，我没有负控，不归因。**
   形状：`test_a_scoped_method_is_saved_then_really_used_and_evaluated` 在
   `assert_created_method` 断言 `created["artifact_id"]` 为真失败；该轮 `material.practice.error
   = MEMORY_DB_MISSING`、`experiences: []`，于是本轮的 `project_options` 只有研究选项，
   脚本决策走成了研究件的创建。与本包改动的交集只有 `decision_situation_context` 多了
   `body`/`sharing` 两块（该夹具没有 sharing 数据、body 是 `UNKNOWN`），但我**没有**在等价树上
   回退本改动复跑，所以只登记形状与位置，不作"既有失败"归因。
3. `recovery.progress` 恒 `NOT_READ`：Body gateway 有 `progress_band`，但
   `life_situation_view` 的 body 列没带它，A 不为此新增第二次读。
4. 分享 `sources` 只有"当前 work artifact + 正在读的材料"；更多来源要在后续阶段成为本轮对象。
5. 没有起 `life_supply` socket 端到端、没有走 Governance 预留（真实 owner 用例直接构造
   `ManagedArtifactStore`，与 B 的 supply-owner 用例同一做法；中间只有运输替身 `SupplyStorePort`）。
6. 没有外发能力，也不宣称：`channel` 只有 `local_inbox`，`read_by_audience` 恒 false。
7. 没有删除任何夹具；没有 `py_compile`（只用 `ast.parse` 校验语法）；没有 commit/push/部署。

---

## 6. 阶段收口（本轮定点补充；**取代** §3/§5 中与之冲突的说法）

### 6.1 主链证据改为正式链路（不再用 helper 冒充）

新增 `tests/test_v04_sharing_life_chain.py`（2 项，**exit 0**）：组合 D 的**真实实例**（真实 Supply
socket、真实 AR-0/AG-2/runtime、真实 `assemble_autonomous_life`），决策是只回放**原始 JSON** 的
脚本，每轮都 `cycle.evaluate_once(trigger_ref=…, observed_at=…, act=True)`；**没有**调用
`_apply_sharing_action`，也**没有** `SimpleNamespace` 决策当整链。
链：源作品（实例自己的 work artifact）→ 正式 Decision `sharing.prepare` → owner `DRAFT`
（`delivered=false`）→ 正式 `sharing.defer(opportunity_ref="quiet-evening")` → 下一轮 prompt
里带着 `state=DEFERRED` + 草稿正文 + 来源版本/是否仍当前 + 她的等待条件 → **声明的机会到了**
（脚本声明，不是世界自动给的）→ 正式 `sharing.send_local` → `LOCAL_DELIVERED`、
`delivery_count=1`、receipt `local_only=true` / 三个 external 全 false；再读同一 `delivery_id`。
同时断言：原作品 head 全程未变、分享件不在 `project_options`、`step_work_target` 不是分享件；
读一眼不产生版本（DEFERRED 不被每 tick 写）。

**NO_ACTION 不再静默掩盖副作用**：`DecisionValidator` 通过、decision 是 `NO_ACTION` 但 sharing
真的落地时，该轮 `outcome = "SHARE_ACTED"` 并带 `sharing_outcome_note`；被 owner 拒
（`conflict` 等）时**不**这样报，仍是原 outcome（`tests/test_v04_sharing_life_chain.py::`
`test_a_refused_sharing_request_is_not_reported_as_a_side_effect`）。

### 6.2 prompt 现在带"要分享的那句话"与"她在等什么"

`life_sharing_context.prompt_block` 改为 **1 + N 次只读**：一次 `list_shares(include_text=False)`
（拿到 owner 自己的 counts/inbox）+ 每件 open share 一次 `read_share(include_text=True)`。
每件 share 进 prompt 的字段：`share_id / state / title / head_version_id`（CAS）、
`text_excerpt`（≤160 字符，草稿自己的话；来源正文永不读）、`text_chars`、
`source{artifact_id, version_id}`、`source_current{readable, stale, matches_recorded, reason_code}`、
`deferred{condition, opportunity_ref}`、`expires_at`、`detail_readable/detail_reason`。
`EXPIRED / DROPPED / LOCAL_DELIVERED` 不再出现在可操作列表里；来源换版/不可读有具名原因。
系统提示同步写明"草稿正文有界可读、来源换了要重新准备、等待条件必须写"。

### 6.3 身体链走正式轮（姿态 / 休息拒绝 / 原 Activity 承接）

新增 `tests/test_v04_body_life_chain.py`（2 项，**exit 0**），同样是 donor 真实实例 + 真实
World/Body socket + 原始 JSON 决策 + `evaluate_once`：
* 正式 Decision 选中 `bodyact:pose:…` → 轮次 `ACTED`，**owner 自己的 snapshot** 真的换了姿势；
* 之后同一 `body_decision_context`（重新做一次真实 situation 读）显示 owner 的拒绝理由从
  `REST_REQUIRES_RESTING_POSE` 变成 `RECOVERY_NOT_NEEDED`，且同一个 socket 的 REST 条目
  `start_available=false`、`stop_available=false`、`recovery_state=inactive`；
* `recovery.progress` / `trend` 现在读的是 owner 自己的 `data.recovery`（`progress_band` /
  `trend`），不再是恒 `NOT_READ`；实现方式是 `cycle._body_recovery_facts()` 在**同一个 body
  port** 上多做一次只读（`WorldBodyReadPort.body()`），失败即 `None`→`NOT_READ`。代价与边界写在
  该方法的 docstring 里，没有新变量/阈值，没有改 owner。
* `recovery.session=CLOSED`、`recovery.claim=NOT_ASSERTED` 同时成立：没有会话就没有恢复；
* 另一项：读材料由正式 Decision `START`，随后同一 Activity 由 `CONTINUE` 推进（原活动承接）。

**限制**：donor 实例的 Body runtime 在 gateway 启动时载入，**不能**在启动后种疲劳，所以"开着的
恢复会话 + 会话内进度"仍只在隔离 body 夹具里证明（`tests/test_body_context_v04.py::`
`RealBodyChainTests`，那里明确声明 `seed_body(fatigue=0.60)`）；本文件不重复宣称。

### 6.4 装配修复（之前 sharing 在 assembly 里一直 UNAVAILABLE）

`resolve_sharing_service(environment, *, subject_id, grant_id)`：B 的 `sharing_from_env` 需要
owner 自己的三个拼写（subject / grant / socket / workspace）。实例 env 里缺 subject/grant 时，
用本实例自己的 `supply["subject_id"]` / `supply["artifact_grant_id"]` 按 **owner 自己的 env 名**
补齐（不发明新名字）。现在 `assemble_autonomous_life(...)["sharing"]["status"] == "READY"`，
真实 socket 上可用。

### 6.5 `tests/test_v04_practice_life_stage.py` 那个 FAIL 的结论（不归因）

只复现了那一个方法（`PracticeLifeChainTest.test_a_scoped_method_is_saved_then_really_used_and_evaluated`，
单跑仍 FAIL，`practice.workspace.memory='BOUND'`、`practice.error='MEMORY_DB_MISSING'`、
`experiences: []`）。来源是 `components/native/memory_runtime_v1/life_experience_port.py:293-296`
的**文件存在性**检查：`evidence_db` 路径在首次读取前不存在；practice seam 的 memory 来自
`service.memory_service`（B 的服务），我的写集不构造 Memory 也不建该文件。**没有负控**，因此
只登记形状与位置，不称"既有失败"；修它属于 Memory/测试夹具，不在本写集，未改。

### 6.6 父级真实国内模型测试怎么复用

* 夹具/装配：照 `tests/test_v04_sharing_life_chain.py::SharingLifeChainTest.setUp` 抄——
  `donor.DScenarioBase` + `prepare_scenario(keep_fixture_world=True)` + `gateway()` +
  `instance_env()` + `WorldMoveOpportunityProvider(WorldBodyClient(env), …)` +
  `runtime_with_memory(...)` + `base.assemble(runtime, env, observation=provider)`。
  `asc` 里的 `SharingCognition` 换成真实 `ProductionAgencyCognitionAdapter`
  （父级 transport 在 `$env:PI_SCRATCH_DIR\body_sharing_cn_transport.py`，注入到
  `runtime_with_memory(cognition=…)` 或在 assemble 前替换 `runtime.cognition_adapter`）。
* 一轮：`out = cycle.evaluate_once(trigger_ref="model:share:1", observed_at=clock.iso(), act=True)`。
* 读证据：`out["decision"]`、`out["outcome"]`（分享落地是 `SHARE_ACTED`）、`out["sharing"]`
  （owner 的 `status/state/receipt/delivery_count`）；下一轮看 `cycle.sharing_decision_context()`
  的同一 `head_version_id`；prompt 里能看到 `text_excerpt` 与 `deferred.condition`。
* 断言不要看模型措辞，只看 owner 的 answer；`NO_ACTION` 且 `out["sharing"]` 缺失也合法。
* 模型调用预算与路由由父级持有（本切片零 HTTP、零密钥）。

### 6.7 本轮命令与 exit

| 命令（WSL，`PYTHONDONTWRITEBYTECODE=1`） | 结果 | exit |
|---|---|---|
| `python3 tests/test_v04_sharing_life_chain.py` | `Ran 2 tests … OK` | 0 |
| `python3 tests/test_v04_body_life_chain.py` | `Ran 2 tests … OK` | 0 |
| `python3 tests/test_body_context_v04.py`（回归，改动后） | `Ran 18 tests … OK` | 0 |
| `python3 tests/test_v04_sharing_cycle.py`（回归，改动后） | `Ran 15 tests … OK` | 0 |
| `python3 tests/test_v04_practice_life_stage.py PracticeLifeChainTest.test_a_scoped_method_is_saved_then_really_used_and_evaluated` | 单方法复现 1 FAIL（§6.5） | 1 |

未重跑其它绿套件；未删除夹具；未 commit/push/部署。
