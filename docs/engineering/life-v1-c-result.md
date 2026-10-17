# C 包回执：经历 → 下一次选择 / 未完事项 / 等待（MEM-GATE · CHOICE · FUTURE；CT09/12/19）

- 候选：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），**UNCOMMITTED**
- HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）｜branch：`work/v03-w01-navigation`（未移动）
- 自有文件 `git diff --check`：exit 0；新增文件无行尾空白（`grep -nE ' +$'` 无命中）。未 stage/commit/push，未 reset/clean/checkout，未整仓复制。
- 未做：真实产品模型、外网、外发、24h、部署/自启、profile/provider/密钥改动、删除用户数据或旧日志、子委派。
- 边界：未碰 A 的 Body/处境/评价、B 的 `private_daily_items`/`life_artifact_port`，未写 `cycle/assembly/resident/one_day_life/AG0/AG1`、既有 `test_one_day_life*`、Memory control ledger/schema、Agenda owner。

---

## 1. `check_life_v02.py` 三项失败：逐项根因（先定位，后修）

三项失败（封存 R1 与 R1b 同样失败，非 R1c 回归；见 `$S/w05rr1b_check_life.txt`）全部在第 12 节「重启后投递 pending handoff」：

| # | 断言 | 实际 | 根因 |
|---|---|---|---|
| 1 | `M0 holds exactly one row for that source :: 0` | 0 行 | **owner 身份不匹配**，不是投递失败 |
| 2 | `the delivered row is the verified one :: None` | rows 空 → None | 同 1（同一查询） |
| 3 | `no duplicate memory experience :: 0` | 0 行 | 同 1（同一查询） |

定位证据（`$S/life-v1-c/run_check_v02.py` 复跑真实隔离 Supply owner，跑完直接 dump 该检查自己的 M0 库）：

```
"m0_rows": [ { "memory_owner": "chiyo", "source_origin": "LIFE_VERIFIED_OUTCOME",
               "delivery_status": "VERIFIED", "speaker": "chiyo",
               "conversation_id": "life:chiyo",
               "primary_source_ref_id": "lexp:6b7d9681ae7ac142", ... } ]
```

即：**handoff 真的投递成功了，M0 真的有且只有一行**；而第 12 节用 `memory_owner=subject`（`NYA02_SUBJECT` = 隔离 Supply 工作区 subject，如 `test:w05rr1bv02`）去问 M0，自然 0 行。

- 写侧 owner：`life_experience_port.build_outcome_event()` 的 `memory_owner=subject`，consumer 传的是 handoff 的 `subject_id`；AG-2 `GLOBAL_SUBJECT_ID = "chiyo.global"`（`activity_continuity.py:54`），`life_experience_memory_consumer.py:89` 做 `split(".")[0]` → `chiyo`；`app/evidence.py:95-96` 对 `LIFE_ORIGIN` 强制 provenance 三元组 `(VERIFIED, LIFE_ROLE, speaker="chiyo")`。
- 读侧 owner：`LifeExperiencePort.subject` / `EvidenceStore.recent_verified_outcomes(memory_owner=...)`。生产/resident 传 `MEMORY_SUBJECT_ENV or "chiyo"`（`autonomous_life_resident.py:273`），与写侧一致；`check_life_memory_v02.py:51` 也用 `subject = "chiyo"`，所以那个检查一直 PASS。
- 结论：**配置 vs 接线**都已核对过（consumer 确实被装配进 runtime，`dispatch_experience_handoffs` 的 `DELIVERED` 包含 `IGNORE`，但本次 disposition 是 `ACCEPT`，M0 行就是证据）；**真正的问题是 owner 结果**——检查拿「Supply 工作区 subject」当 M0 记忆 owner。

**修复（只改检查 fixture，未改产品 seam）** `components/check_life_v02.py`：

- `:32-39` docstring 补 `NYA02_MEMORY_OWNER` 与「`NYA02_SUBJECT` 拥有的是 Supply 工作区，不是 M0 owner」；
- `:65-66` 新增 `DEFAULT_MEMORY_OWNER = "chiyo"`；
- `:544-549` 第 12 节的 `MemoryControlLedger` / `LifeExperiencePort` 用 `memory_owner`；
- `:554-556`、`:563`、`:595-597` 三处 `recent_verified_outcomes(memory_owner=...)` 换成 `memory_owner`；
- `:568-574` **新增一条断言**（不是放松）：`recent_verified_outcomes(memory_owner=subject) == [] and memory_owner != subject`，把「工作区 subject 不是 M0 owner」变成显式不变量。

未改 `life_experience_memory_consumer.py` / `life_experience_port.py`：**不需要**。已复现的缺口只有「检查问错 owner」；生产两侧 owner 本来就一致，改产品会把生产写入 owner 也一起改掉，属于超出本包的无谓改动。

**非放松证明（负控，实跑）**：同一检查、同一真实隔离 owner，只把 `NYA02_MEMORY_OWNER` 强行设成工作区 subject（即恢复旧行为）：

- 正跑：`V02_CHECK: PASS`，检查自身 exit 0（runner JSON `"exit": 0`）；
- 负跑：`V02_CHECK: FAIL (4 assertion(s))`，检查自身 exit 1，失败项 = 原三项 + 新增那条 owner 不变量；
- 两次的 `m0_rows` 都显示 `memory_owner="chiyo"`, `VERIFIED` 一行。

即断言仍咬得住，改动只是把查询换到真正的 owner 上。

---

## 2. 新接口：`components/life/scripts/life_continuity_inputs.py`（771 行，新文件）

只读、可注入、有来源；不新建引擎、不建库、不签权限。

### 2.1 注入面（duck-typed，已与真实 owner 对齐）

```python
LifeContinuityInputs(*, binding, past_source=None, activity_source=None,
                     agenda_source=None, governance_probe=None,
                     max_past=5, max_text_chars=400)
```

| 参数 | 期望读法 | 真实实现 |
|---|---|---|
| `binding` | `ContinuityBinding(subject, home, now, owner_refs)` | 必填；subject/home 非空、now 必须 ISO、owner_refs 键值非空，否则 `ContinuityInputsError` |
| `past_source` | `recent(limit=...)`（可选 `.subject`） | `life_experience_port.LifeExperiencePort` |
| `activity_source` | `list_open_activities()` / `get_activity(id)` | `activity_continuity.ActivityReadService` |
| `agenda_source` | `list_agenda_items(only_open=True)` / `list_eligibilities(only_open=True)` / `get_eligibility(id)` | `activity_waiting_agenda.WaitingCoordinator` |
| `governance_probe` | `() -> {"healthy": bool, ...}` | `MemoryControlLedger(...).projection().healthy` |

主入口：`read(*, known_eligibility_refs=(), since=None) -> ContinuityInputs`。

### 2.2 输出字段

`ContinuityInputs.to_dict()`：
`schema_version`(`chiyo.life.continuity_inputs.v1`)、`binding`、`past_state`、`past[]`、`evidence_roots`、
`unfinished[]`、`waiting[]`、`choices[]`、`next_step`、`observed_eligibility_refs`、`next_recheck_at`、
`source_states{memory,activity,agenda}`、`refusals[]`、
`llm_calls=0`、`requires_model_call=False`、`authority=None`、`location_authority=None`、`grants=[]`、
`candidate=None`、`decision=None`、`adoption=None`、`may_start_activity=False`。

- `past[]`（每个 item 带 `owner=memory`、`subject`、`home`、`bound_at`）：`experience_id`、`source_ref`、`occurred_at`、`activity_ref`、`action_ref`、`artifact_ref`、`version_ref`、`owner_result`、`outcome_kind`、`text`（截断）。
- `unfinished[]`：`kind`(`ACTIVITY`/`AGENDA_ITEM`)、`owner`、`item_ref`、`status`、`title`、`revision`、`updated_at`、`checkpoint_ref`、`waiting_on_ref`、`resume_condition_ref`、`resumable`、`resume_eligibility_ref`、`continue_from_ref`、`continue_from_reason`；议程项另有 `due_at`、`trigger_kind`、`source_refs`。**不发明 Activity 没有的字段**（如 `artifact_refs` 已从实现中移除）。
- `waiting[]`：`activity_ref`、`waiting_on_ref`、`resume_condition_ref`、`waiting_since`、`resume_eligibility`（**只有新 eligibility 才非 None**）、`resume_refused_reason`。
- `choices[]`：`choice_id`(`cont:<ref>`/`cont:no_action`)、`kind`(`CONTINUE_UNFINISHED`/`RESUME_WAITING`/`NO_ACTION`)、`owner`、`subject_ref`、`activity_ref`、`reason_code`、`evidence_refs`、`preferred`、`authority=None`。`NO_ACTION` 恒在且合法（安静不是失败）。
- `next_step`：`kind`/`reason_code` ∈ `RESUME_WAITING_NEW_ELIGIBILITY`｜`CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`｜`CONTINUE_FROM_CHECKPOINT`｜`NO_ACTION_NOTHING_OPEN`，另有 `item_ref`、`object_ref`、`continue_from_ref`、`evidence_refs`、`authority=None`、`requires_model_call=False`。

### 2.3 语义与拒绝（fail closed）

| 场景 | 行为 |
|---|---|
| 控制账本失读 / `governance_probe` 抛异常 | `past_state="CONTROL_LEDGER_UNREADABLE"`、`past=[]`、`refusals=[MEMORY_GOVERNANCE_NOT_ESTABLISHED]`，**不**当作安静日；`next_step` 不会变成「按 owner 确认对象继续」 |
| past source 的 `subject` 与 binding 不符 | `past_state="WRONG_SUBJECT"`、`past=[]`、`refusals=[PAST_SOURCE_SUBJECT_MISMATCH]` |
| 单行 `subject` 不符（source 未声明 subject 时） | 丢弃该行 + `refusals=[PAST_ROW_SUBJECT_MISMATCH]` |
| source 抛异常 | `past_state="CONTROL_LEDGER_UNREADABLE"`、`refusals=[SOURCE_READ_FAILED]` |
| 未接线 | `past_state="PAST_SOURCE_NOT_WIRED"`（与 `NO_ADMITTED_HISTORY` 严格区分）；agenda/activity 未接线 → `source_states[...]="NOT_WIRED"` + refusal |
| agenda 读失败 | `source_states[agenda]="UNREADABLE"`、`unfinished` 该项为空 + refusal（不谎报「没有未完事项」） |
| 同根重复 | 先按 `source_ref` 去重，再按 root `(activity_ref, version_ref|owner_result, outcome_kind)` 去重；`evidence_roots == len(past)`，重复不增证据 |
| WAIT 恢复资格 | 仅当存在 **新** eligibility（id 不在 `known_eligibility_refs`，且 `eligible_at > since`）才给 `resume_eligibility`，否则 `resume_refused_reason="NO_NEW_ELIGIBILITY"` |
| 零模型轮询 | 接口无模型依赖：`llm_calls=0`、`requires_model_call=False`、`next_recheck_at` 来自 agenda `due_at`（重查是时钟问题） |
| 权限 | `candidate_from_recall()` / `location_authority_from_recall()` / `grant_from_recall()`（payload 与 reader 上都有）抛 `ContinuityRefused`；payload 内任何 authority/grant/capability/permission/location/position 键恒为空值 |
| 离线经历 | 只回读 M0 实存行；时间跨度再大也不合成条目 |

### 2.4 历史如何改变下一步（A/B）

同一 `binding`、同一 open 集合，只改历史：

| | 无历史（或弱线索） | 有准入后果 |
|---|---|---|
| `next_step.reason_code` | `CONTINUE_FROM_CHECKPOINT` | `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` |
| `next_step.item_ref` | `actv_a`（最近更新者） | `actv_b`（**有历史者，压过 recency**） |
| `next_step.continue_from_ref` | `chk:a:9`（checkpoint） | `settle:b-confirmed`（owner 自己的结算/版本 ref） |
| `next_step.evidence_refs` | `["actv_a"]` | `["lexp:histb", "<event_id>"]` |
| `unfinished[actv_b].continue_from_ref` | `None` | `settle:b-confirmed` + `continue_from_reason=CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` |
| 首选 choice | `cont:actv_a` | `cont:actv_b`（`reason_code=ADMITTED_CONSEQUENCE_FOR_THIS_ITEM`） |

弱线索负控（都必须与「无历史」完全相同）：真实准入但无确认对象的 `CONFLICT` handoff；`activity_ref` 不属于任何 open 项的后果；只有自由文本、无 `activity_ref` 的记录。

### 2.5 D 侧消费例子（不落库、不签权）

```python
from life_continuity_inputs import LifeContinuityInputs, ContinuityBinding
from life_experience_port import LifeExperiencePort
from activity_waiting_agenda import WaitingCoordinator   # 或 AG-2 的 waiting 读端口
from memory_control import MemoryControlLedger

ledger = MemoryControlLedger(controls_db, evidence_db, owner="chiyo",
                             conversation=life_conversation_id("chiyo"))
reader = LifeContinuityInputs(
    binding=ContinuityBinding(subject="chiyo", home=str(state_root), now=now_iso,
                              owner_refs={"activity": "life:activity",
                                          "artifact": "life:artifact",
                                          "agenda": "life:agenda"}),
    past_source=LifeExperiencePort(evidence_db, controls_db=controls_db, subject="chiyo"),
    activity_source=runtime.activity_read,
    agenda_source=runtime.waiting_coordinator,
    governance_probe=lambda: {"healthy": ledger.projection().healthy})

view = reader.read(known_eligibility_refs=state.get("seen_eligibility_refs") or [],
                   since=state.get("eligibility_watermark"))
state["seen_eligibility_refs"] = view.observed_eligibility_refs      # 由 D 持久化水位
if view.next_step["reason_code"] == "RESUME_WAITING_NEW_ELIGIBILITY":
    ...   # 走既有 AG-1 Decision + AG-2 Adoption + LR-2 恢复；本接口不给任何权限
elif view.next_step["reason_code"] == "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT":
    ...   # 从 view.next_step["continue_from_ref"] 这个 owner 已确认对象继续（非 checkpoint）
else:
    ...   # CONTINUE_FROM_CHECKPOINT / NO_ACTION_NOTHING_OPEN（安静合法）
```

D 只需读 `view.to_dict()`；`view.authority`/`location_authority`/`grants`/`candidate`/`decision`/`adoption` 恒空，`may_start_activity=False`。

---

## 3. 精确自有写集

| 文件 | 状态 | 改动 |
|---|---|---|
| `components/life/scripts/life_continuity_inputs.py` | 新增（771 行） | 本包唯一新接口 |
| `tests/test_life_continuity_inputs.py` | 新增（894 行，36 用例） | 定向正/负/去重/失败恢复/权限测试 |
| `components/check_life_v02.py` | 修改（+31/-9，见 §1） | 只修 owner 查询 + 一条新增不变量断言 |
| `docs/engineering/life-v1-c-result.md` | 新增 | 本回执 |

未新增/修改其它任何产品文件；`life_experience_memory_consumer.py`、`life_experience_port.py` 保持原样（无需修改，见 §1）。

---

## 4. 实跑命令与结果（WSL 隔离 python，`PYTHONDONTWRITEBYTECODE=1`）

```
PY=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
```

| 命令 | exit | 结果 |
|---|---|---|
| `$PY tests/test_life_continuity_inputs.py` | 0 | `Ran 36 tests … OK` |
| `$PY components/check_life_memory_v02.py` | 0 | `MEMORY_CHECK: PASS`（Memory seam 未被本包影响） |
| `$PY $S/life-v1-c/run_check_v02.py`（真实隔离 Supply owner；`NYA02_MEMORY_OWNER` 默认） | runner 0；检查自身 exit 0 | `V02_CHECK: PASS`（12 节全过，含新增 owner 不变量） |
| `C_OWNER_NEG=1 $PY $S/life-v1-c/run_check_v02.py`（负控：强制旧行为） | runner 0；检查自身 exit 1 | `V02_CHECK: FAIL (4 assertion(s))`：原三项 + owner 不变量 |

隔离根（唯一、未清理共享根）：`/tmp/nyaw05r.jdK3q/c-lifev1c-*`、`/tmp/nyaw05r.jdK3q/c-lifev1csvc-*`、`/tmp/nyaw05r.jdK3q/c-continuity-memory-*`。
未跑：全仓回归、全 Memory 套件、旧 W05R 套件、CLI、`w05rr1c_neg.sh`（按单要求不跑）。

---

## 5. 机制已证 / 未接线 / 未验（如实区分）

**已证（本地、可复现）**
- 真实 Activity + AR-0 动作 + owner 回执 → handoff → M0 准入 → 重启后投递 → 读回：由修好的 `check_life_v02.py` 第 11/12 节真实跑通（真实 Supply owner、真实 AR-0/AR-1、真实 M0）。
- 真实 M0 上：同 handoff 二次投递为 `DUPLICATE`、同根不同 revision 不增 `evidence_roots`、不同根增。
- 新接口：真实 `LifeExperiencePort` 读回真实准入后果；`past_state` 区分 OK / NO_ADMITTED_HISTORY / CONTROL_LEDGER_UNREADABLE / WRONG_SUBJECT / PAST_SOURCE_NOT_WIRED；控制账本失读与错 subject 均 fail closed；同根去重；WAIT 只在新 eligibility 给恢复资格（含 `since` 水位）；零模型轮询（重复读同一状态 payload 完全一致、`llm_calls=0`）；recall 不签 Candidate/位置/授权；只调用读方法（source 包装器对非白名单方法直接报错）。
- 历史改变下一步：受控 A/B（真实 M0 里的准入后果 vs 空历史）在 `next_step`、可继续对象、首选 choice 三项上都变；三条弱线索负控不变。

**未接线（等 D）**
- 新接口尚未接进 `cycle/assembly/resident/one_day_life`：D 需要注入 `past_source`/`activity_source`/`agenda_source`/`governance_probe`，并把 `observed_eligibility_refs` 当水位持久化。
- 未完事项/等待列的 fixture 是**读夹具**：`AgendaItem`/`ResumeEligibility` 用 owner 自己的 dataclass 构造（自带校验），Activity 记录用 `ActivityCommandService.start_activity` 的键集并有漂移守卫；**没有**伪造 Activity 写。真实 WAIT/eligibility 由 LR-4 owner 产生，本包只读。
- `version_ref` 目前只有接口层支持（优先于 `owner_result`）；真实 consumer 路径只带 `artifact`/`owner_result`（AG-2 handoff 无 version ref），因此真实链路上 `continue_from_ref` 是 owner 的结算 ref。

**未验（不在本包授权内）**
- 真实模型会不会因历史改变**选择**（本包只证「输入/可继续对象/下一步变了」，不冒称真实模型因果实验）。
- 24h/长跑、部署、生产 profile、外发、Web/3D、跨会话/多实例。

**真实剩余原因/缺口**
- `dispatch_experience_handoffs` 把 consumer 的 `IGNORE` 也算 `DELIVERED`（`life_integration_ag2.py:4484`）。本次失败与此无关（disposition 是 `ACCEPT`，M0 有行），但若 consumer 未接线/被禁用，pending 会被清空而不留痕迹。**未改**（不在本包缺口内，且会动 AG-2 语义）——交给 D/后续按需裁定。
- 拒绝型 production 默认（`RefusingExperienceConsumer`）与 resident 配置（`MemoryAdmissionExperienceConsumer(enabled=True)`）不同：本包未改任何默认；默认装配下 handoff 仍被拒绝，M0 无行，此时接口会如实报 `NO_ADMITTED_HISTORY`（而不是把它当失败）。
