# v0.4 B 结果：正常 Life 入口里的共同实践内容半边 + 整链（父级可复用夹具）

> 施工者：B（生命侧单 writer）。写集：`components/life/scripts/shared_practice_life.py`、
> `life_social_contacts.py`、`life_co_presence.py`、`autonomous_life_assembly.py`、
> `tests/test_v04_shared_practice_life_seam.py`、`tests/test_v04_shared_practice_life_chain.py`、
> 本文件、own note。**没有**碰 A 的 `shared_practice_service.py`/service tests、
> `private_project_service.py`、`life_sharing_service.py`、`phone/**`、父级 checkpoint。

## 1. 本回合修的三个真缺口（都在 B 的写集里）

### 1.1 「任一 OPEN 作品」不再等于「可以继续说话」

上一版 `SocialContactPort._practice_step` 只要 `practice.find_open()` 非空就发内容请求：手机侧刚
开出来的**候选**、被 pause 的作品、joint 已结束的作品都会引出一次她的回应。现在有一条具名的内容门
`SocialContactPort._practice_gate()`，一条来话只有**同时**满足下面全部才算「继续这一场 joint」：

| 条件 | 读法 | 不满足时的名字（item 的 `practice_gate.reason_code`） |
|---|---|---|
| co-presence 会话现在 `OPEN` | `protocol.read_session()` | `CO_PRESENCE_NOT_OPEN`（`NONE`/`CLOSED`/`PARKED_NO_JOINT`） |
| 它点名了 joint Activity | 同上 `joint_activity_ref` | `CO_PRESENCE_NO_JOINT_ACTIVITY` |
| 那件 joint 在本实例 owner 里真的在跑 | `runtime.activity_read.get_activity()` | `JOINT_ACTIVITY_NOT_ACTIVE`（含 `COMPLETED`/`UNVERIFIABLE`） |
| 有一件 open 作品 | `practice.find_open()` | `SHARED_PRACTICE_NO_OPEN_SESSION` |
| 会话绑定了作品 | `session["practice_session_ref"]` | `SHARED_PRACTICE_NOT_BOUND`（例如重启/旧实例没有这条绑定） |
| 绑定的就是这一件 | 对比两个 id | `SHARED_PRACTICE_BOUND_TO_ANOTHER_SESSION` |
| 作品自身 `OPEN` | `find_open()["state"]` | `SHARED_PRACTICE_NOT_OPEN`（`PAUSED`） |

门不通过 → 仍然是 `OBSERVED` + `OBSERVED_ONLY`、`llm_calls=0`、不写 A、不 pause、不起 Activity；
`record_result.detail` 里带上具名原因（只写原因名，不写正文）。`shared_practice_life` 侧另有
`find_content_session()`：`step_message` 不再用「paused 也算 open」的 `find_open()`，`PAUSED`/`ENDED`
直接 `REASON_NO_OPEN_PRACTICE`（detail 说明「paused/ended 不再产生内容」）。

### 1.2 绑定写在 joint 自己的存档里（重启仍可读、无重放）

`life_co_presence.FiniteCoPresence.bind_practice(invitation_id=…, practice_session_ref=…, event_ref=…)`
把「这一场 joint 开的是哪一件作品」写进 co-presence 会话记录：`practice_session_ref`、
`practice_bound_at`、`practice_invitation_event_ref`。只有 `state=OPEN` 且邀请对得上才写，否则
`PRACTICE_BIND_REQUIRES_OPEN_SESSION` / `PRACTICE_BIND_FOR_ANOTHER_SESSION` /
`PRACTICE_BIND_REQUIRES_SESSION_REF`。`_invitation` 在 ACCEPT + 开作品成功后立刻调用它，结果以
`base["practice_bound"]` 报告（没绑上就是 `false`，不是沉默）。

作品开场也绑到**这条邀请事件**：`_practice_open` 现在传 `basis={"event_ref": <邀请事件>}`
（A 用本实例读面核对）+ `operation_key="practice_open:<邀请事件>"`（重试＝重放，不分叉）。

### 1.3 装配层：A 的工厂真的能配起来，且与 AG-1 同一本账、发送前 admission

* `resolve_shared_practice(..., admission=None, requests=None)`：先用本实例 Supply port 已经在用的
  owner 事实填 A 的**名字**再交给工厂 —— `NYAIRO_LIFE_SUBJECT`（= 实例 subject）、
  `NYAIRO_ARTIFACT_GRANT`（= 实例 grant）、`NYAIRO_LIFE_SUPPLY_SOCKET`、
  `NYAIRO_LIFE_WORKSPACE_ID`（= 实例 workspace）。修之前真实 profile 把主体写成
  `NYA02_SUBJECT`/`CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT`，A 一个都不认，于是**每个真实实例**上
  内容半边都静默 `SHARED_PRACTICE_NOT_CONFIGURED`（等于没有）。
* `resolve_local_contacts(..., admission=None, requests=None)` → 透传给 `resolve_shared_practice`；
  `_practice_responder(env, requests=…, admission=…, pause_guard=admission)`。
  账本取 `cycle.budget.requests`（AG-1 用的同一对象），kind=`content`，发送前过实例自己的
  `pause_admission`；`assemble_autonomous_life` 在 `resolve_local_contacts(...)` 里传 `admission`
  与 `requests`。缺配置仍返回具名 `UNAVAILABLE`，不给半工作替身。

## 2. 父级可复用的整链夹具（真实正常入口）

`tests/test_v04_shared_practice_life_chain.py` 里的 **`SharedPracticeChainFixture(DScenarioBase)`**：
真 Supply/World/runtime/assembly/`runner_tick`，只有模型是本地脚本原始 JSON。父级可以
`from tests.test_v04_shared_practice_life_chain import SharedPracticeChainFixture` 后
`class MyProbe(SharedPracticeChainFixture)` 直接用（用前先 `del` 继承来的 `test_*`，见文件末尾 3 行注释）。

| API | 精确签名 | 语义 |
|---|---|---|
| `setup` | `setup(*, cognition=None, answers=None) -> SimpleNamespace` | `prepare_scenario()` → `runtime_with_memory` → `assemble` → 建 `LocalContactInbox`；把 `contact_port.practice.responder` 换成 `spl.ModelPracticeResponder(BudgetedModelClient(RawJSONClient(answers), cycle.budget.requests, admission=admit))`。返回的 fixture 带 `runtime/port/data/memory/env/cognition/cycle/assembled/contacts/inbox/contact_port/practice/protocol/content_client/content_ledger/content_admission` |
| `read` | `read(fixture) -> dict` | 从 owner 读回：`co_presence`（会话记录，含 `practice_session_ref`/`private_checkpoint_ref`）、`practice`（`read_back()`：state/joint_state/counts/distinct_seqs/contributions/responses/head_version_id/title/material_count/text_chars）、`open_practice`、`joint_activity`、`private_activity`、`foreground`、`pending`（inbox 里待处理条数）、`eligibilities`、`reading_page`、`content_calls`、`requests`（请求账本视图） |
| `step` | `step(fixture, payload: dict, *, cognition=None) -> dict` | `inbox.submit(payload)` 投一条真实 transport 事件，再跑一次 `assembly.runner_tick(assembled, act=True)`；返回 `{event_ref, event, tick, contacts}` |
| `tick` | `tick(fixture, cognition=None) -> dict` | 只跑一次正常 resident step（可选换 AG-1 认知分支） |
| `close` | `close(fixture) -> None` | 只关实例资源（`runtime.close()`：flush + 释放 lease），**不删数据** |
| `restart` | `restart(fixture) -> SimpleNamespace` | 同一个实例、同一数据根：先 `close`，再建**新** runtime + 新 assembly + 新 inbox/protocol/practice，并挂一个**空脚本**的 RawJSONClient（任何内容请求都会成为可见失败，所以「无重放」用 0 次调用证明） |

辅助：`_reading(fixture)`（读第 1 页，返回 `(activity_ref, row)`）、`_reading_page(fixture)`、
`_state(fixture, ref)`、`_open_window(fixture)`、`_co_presence_cognition("START")`、
`_no_action_cognition()`、`_resume_cognition()`（继承自 `DScenarioBase`）、`RawJSONClient`。

夹具与清理：`tempfile.tempdir` 在 `setUp` 里指向 `/tmp`，临时根一律 `day-life-parent-*`
（Unix socket 路径要短），`tearDown` **只关资源**（不调用会 `shutil.rmtree` 的 donor `tearDown`，
也没有全局 no-op 删除守卫）；跑完的实例树保留给父级核对。

链里各步的精确入口：

1. 读页 1：`cycle.evaluate_once(trigger_ref=…, act=True)`（AG-1 用 `_reading_cognition(data)`）；
2. 邀请：`inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": …, "text": …, "material_refs": [reading_artifact_id], "operation_key": …})`；
3. 每条用户话：`inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": …, "operation_key": …})`；
4. 结束：`inbox.submit({"event_kind": "CO_PRESENCE_END", "invitation_id": …, "explicit_end": True, "operation_key": …})`；
5. 每次提交后 `assembly.runner_tick(assembled, act=True)`（正常 resident step，含 maintain + contacts + feeds + due）。

## 3. 传输字段（C 只读消费，本回合未变，只补了 `practice_bound` 报告位）

| 方向 | 字段 |
|---|---|
| 邀请 payload（允许） | `event_kind`, `invitation_id`, `source_ref`, `occurred_at`, `scope`, `requires_engagement`, **`material_refs`**（≤6、去重、`^[A-Za-z][A-Za-z0-9_\-]{1,31}:\S{1,200}$`） |
| 普通来话 payload（允许） | `text`, `operation_key`, `source_ref`, `occurred_at` —— 带 `material_refs` 一律 `CONTACT_FIELD_NOT_ALLOWED_FOR_KIND` |
| 结束 payload（允许） | `invitation_id`, `occurred_at`, `explicit_end` |
| 结果（`inbox.record_result`） | `outcome` ∈ `OBSERVED/ACCEPTED/DEFERRED/DECLINED/ENDED/UNKNOWN`；`reason_code`：`OBSERVED_ONLY`、`SHARED_PRACTICE_CONTENT_RECORDED`、`CO_PRESENCE_ACCEPTED`、`CO_PRESENCE_DEFERRED`、`CO_PRESENCE_DECLINED`、`CO_PRESENCE_ENDED`、`CO_PRESENCE_END_JOINT_NOT_TERMINAL`、以及 UNKNOWN 族（`SHARED_PRACTICE_MODEL_FAILED/_MODEL_TRUNCATED/_INVALID_SCHEMA/_OWNER_REFUSED/_REQUEST_BUDGET_REFUSED/_RESPONDER_MISSING`） |
| accept 项上的报告位 | `session_ref`、`decision_record_ref`、`joint_activity_ref`、`private_activity_ref`、`park_applied`、`shared_practice{session_id,state,joint_state,reused,bound}`、`practice_session_ref`、`practice_state`、`practice_joint_state`、**`practice_bound`**、`handoff` |
| 来话项上的报告位 | `outcome=OBSERVED`、`llm_calls`、`practice{stage,detail,response_text,contribution_seq,work_version_id,counts,llm_calls}`（仅当真的走内容步骤）、**`practice_gate{allowed,reason_code,session_id,joint_activity_ref,...}`**（有 practice 半边时总在） |
| 结束项上的报告位 | `joint_completed`、`joint_activity_status`、`new_eligibility_refs`、`private_activity`、`shared_practice{session_id,state,joint_state,counts,head_version_id}`、`practice_state`、`practice_ended` |
| 永远为假 | `delivered_to_user`、`external_channel_sent`、`private_text_included`、`owns_activity/owns_relationship/owns_memory` |

C 的正式 serve 绑定与「手机输入 → inbox」不在本报告里（C 独占）；B 只保证上面这些字段与语义。

## 4. 测试（真实 exit / skip，WSL `/mnt/d/nyairo-rc1-body-backend`，`PYTHONDONTWRITEBYTECODE=1`）

| 命令 | 结果 |
|---|---|
| `python3 -m unittest tests.test_v04_shared_practice_life_seam -q` | **Ran 29, OK, exit 0**（0 skip） |
| `python3 -m unittest tests.test_v04_shared_practice_life_chain -q` | **Ran 3, OK, exit 0**（0 skip，真 POSIX socket） |
| `python3 -m unittest tests.test_v04_social_life_core -q` | **Ran 14, OK, exit 0** |
| `python3 -m unittest tests.test_v04_shared_practice_service_supply -q`（A 的真 Supply 用例，只读参照） | **Ran 14, OK, exit 0** |
| `python3 -m unittest tests.test_life_v1_integration -q`（donor 套件） | Ran 56，**7 fail + 1 error**，与 B 无关（见 §5.2） |
| `node --experimental-strip-types …/verify-agent-note-tree.ts`（repo 根） | `ok: 79 note(s) tree and relative links verified`，exit 0 |
| `node --experimental-strip-types …/verify-agent-note-format.ts`（repo 根） | `ok: 79 note(s) verified`，exit 0 |

Windows（native）上整链模块具名 skip（`posix_only`，需要 `fcntl`+AF_UNIX），**不是**通过；
seam 的分派/schema/端口用例如常在 Windows 跑。

`test_v04_shared_practice_life_chain.py` 三个用例：

1. `test_read_invite_three_real_steps_then_end_and_resume_page_two`：读页 1（书签/checkpoint 真实）→
   材料邀请（`material_refs=[reading_artifact_id]`，真 Supply artifact id 通过 `bound_material_refs`）→
   AG-1 `START` 接受（private `WAITING` + checkpoint 不变、joint `ACTIVE` 成唯一前台、作品 `CANDIDATE`
   + 绑定）→ 3 条真实用户文本，每条**恰好 1 次**内容请求：
   * 每次请求的 prompt 里是真实材料（`artifact_id` + 摘录）、当前作品正文、最近条目、这条新消息；
   * `reply/contribution/work_text` 三键严格 JSON；每次 `work_version_id` 都变（3 个不同版本），
     owner 字节里最终是第三版全文；
   * `read_back()`：3 回应 / 6 贡献 / `distinct_seqs=[1..9]` / `joint_state=JOINT`；
     用户那三条与脚本三条逐字相同，她三条回应逐字相同；
   * 每次账本 `utc_day_calls` **+1**（对象就是 `cycle.budget.requests`），admission 恰好 3 次、kind=`content`；
   * 私人 Activity 全程 `WAITING`、checkpoint 不变、仍在第 1 页；
   然后显式结束 → joint `COMPLETED`、私人资格 OPEN、作品 `ENDED`，**同一 tick** 里正常资格事件驱动
   fresh `RESUME`（`decision=RESUME,status=ACTED`）→ 同一 Activity 回到 `ACTIVE`、读**第 2 页**。
2. `test_a_restart_of_the_same_instance_reads_the_work_and_replays_nothing`：跑完 1 的链后
   `close` + `restart`（新 runtime/新 assembly/空脚本客户端）：作品 `ENDED`、6 贡献/3 回应、
   seq 1..9、`head_version_id`/标题/材料数同一份、贡献的归属顺序一致、回应逐字一致、
   会话 `CLOSED` 且 `practice_session_ref` 还是同一件、joint `COMPLETED`、私人 Activity 同一 id
   `ACTIVE` 且 `private_checkpoint_ref` 仍是当初 park 的原断点、第 2 页仍在；
   `pending=0`、事件结果仍是 `SHARED_PRACTICE_CONTENT_RECORDED`/`PROCESSED`、静默 tick
   `processed=[]`+`llm_calls=0`+**0 次内容调用**；重启后新来话 → `practice_gate=CO_PRESENCE_NOT_OPEN`、
   0 调用、seq 不变、账本不再涨。
3. `test_a_message_before_any_joint_time_is_observed_and_asks_nothing`：读页 1 之后、任何邀请之前
   来一条普通话 → `OBSERVED`、`llm_calls=0`、`practice=None`、`practice_gate=CO_PRESENCE_NOT_OPEN`、
   0 次内容调用、私人 Activity 状态/checkpoint 逐字段不变、会话里没有 `practice_session_ref`、
   `practice.find_open()` 为 None。

`test_v04_shared_practice_life_seam.py` 新增 5 条内容门用例（候选作品/无 joint、绑定属于另一会话、
绑定读不到、joint 已 `COMPLETED`、作品 `PAUSED` 以及结束后的 `CLOSED`），并给端口用例加了
owner 形状的 Activity 读替身（`FakeRuntime`/`FakeActivityRead`）。

## 5. 未完成 / 阻塞（如实）

1. **真实模型验证没开始**：本阶段 0 次真实联网调用，12 attempts/900 秒那一窗由父级开。链里的模型
   是脚本原始 JSON，不是自然模型行为。
2. **`tests.test_life_v1_integration` 的 8 个红不在 B 的写集里**：`DAutonomousReadingTest`/
   `DContinuityIntegrationTest`/`DRestartAndQuietTest`/`DRoomAndBodyTest`/`DScenarioBase` 的
   `test_artifact_choice_commits_a_real_supply_version`、`test_the_work_branch_…`、
   `test_a_weak_cue_…` 全部是 `ALREADY_DONE != ACTED`（`step_path=AR0_*_CLOSEOUT_WORLD_ROUTED`，
   owner 回 `OK` 但没建新版本），加上 `DCoPresenceClosureTest` 两条（`_start_joint_time` 用的
   同一条 work 分支、以及 `test_status_and_reads_never_consume_an_eligibility` 的 KeyError）
   与 `test_a_real_owner_result_names_the_next_continue_point…`（`continue_from_ref` 期望 `rset:…`）。
   这些代码路径（AR-0 closeout / continuity continue object）B 没改过，属他人写集，未修。
3. **`tests/test_v04_local_contact_port.py::CoPresenceCodecBridgeTest::test_invitation_block_is_accepted_by_the_co_presence_codec`
   红（1 fail）**：它断言 `set(parse_invitation(...))` 是固定集合，而上一轮 B 的传输扩展给
   `parse_invitation` 加了 `material_refs`（第 348 行缺该键）。不是本回合引入（本回合没动
   `life_local_contact`/`parse_invitation`），也不在本阶段给 B 的写集（`tests/test_v04_shared_practice_life*.py`）
   里，故未改；一行修法是把 `material_refs` 加进该断言集合。父级若要收口，请指派给对应 writer。
4. **上一阶段真实 `INVALID_SCHEMA` 只作历史**：没重开旧窗、没输出原 prompt/回答、没放宽 strict 解析。
