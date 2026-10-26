# Agent Note: v0.4 A 身体参与与有分寸分享——身体处境进决定、分享走真实 owner

Status: implemented

## Problem

主线 A 要交付两条**进入正常生活入口**的链，动手前实测到三个真问题：

1. **决定模型看不见自己的身体。** `life_situation_view` 已经给出 `body` capacity（`LIMITED` /
   `UNKNOWN`）并进了 AG-1 的 capacity 维度，但 `decision_situation_context` 里只有 `world`
   （位置/手/可达/机会）。模型无法区分"身体没被读到"和"身体没事"，也看不到是谁在报负荷、
   报的是哪个档位、**恢复会话是否开着**、以及 owner 说"现在还不能休息"的具体原因。
   这条缝的后果很具体：`NO_ACTION` 看起来像"她想安静"，而不是"她的身体这一轮只有一个档位"。
2. **会话被当成恢复。** Body 的 `REST` start/stop 是 World 的会话命令，`body_runtime` 的
   `recovery.states` 是 `inactive/active/interrupted/completed`；其中**没有**"recovered"这个
   事实。旧链只要命令 SUCCEEDED，就很容易被读成"她休息好了"。需要一处明确、可引用的说法。
3. **分享没有生活侧入口。** B 交付了 `LifeSharingService`（DRAFT/DEFERRED/DROPPED/EXPIRED/
   LOCAL_DELIVERED，本地收件箱，固定 `local:owner`/`local_inbox`），但 A 的决定看不到自己的
   草稿、没有"准备/延后/发送/放弃"的合法输出通道，也没有任何一处把"B 说落地了"当成事实。

## Decision

写集 A：`autonomous_life_cycle.py`、`autonomous_life_assembly.py`、
`agency_cognition_adapter.py`、`agency_decision_ag1.py`，加两个自己的辅助
`life_body_context.py`、`life_sharing_context.py`，测试
`tests/test_body_context_v04.py`、`tests/test_v04_sharing_cycle.py`。
不新增 owner、不改阈值、不加变量、不建第二个作品库或 LifeManager。

1. **身体处境是"同一次读"的投影（`life_body_context`）。** 输入只有两样：`life_situation_view`
   的 `body` 列（已经是一次真实 `WorldBodyReadPort.body()`）和 World owner 自己的 `REST` 条目
   （`world_body_gateway_api._rest_availability`）。输出有界、定性：`capacity`、`burden[]`
   （signal + band）、`easing[]`（只有三个 trend；热感两个信号**不带**，它们不说明能力）、
   `recovery{session, state, claim, progress}`、`rest{can_start, can_stop, sleep_like, why_not,
   pose}`、`unknowns[]`、`limits{reads_performed: 0}`、`not_authority{}`。
   两条硬规则写进模块与测试：**没有负荷信号不是健康**（`NO_BURDEN_SIGNAL_IS_NOT_HEALTH`，
   capacity 仍是 `UNKNOWN`）；**会话不是恢复**（`recovery.claim` 恒为 `NOT_ASSERTED`，
   `progress` 恒为 `NOT_READ`）。它不读预算、不出 Candidate、不授权任何动词。
2. **进真实 prompt。** `cycle.decision_situation_context()["body"]` 由
   `read_situation()` 的同一次读派生（`body_decision_context`），adapter 的 `_bounded_situation`
   用白名单再压一次（`_bounded_body_context`，负荷 4 条 / easing 3 条）。结构化裁剪把身体块与
   现场一起留到最后（step 名从 `situation_except_world` 改为
   `situation_except_world_and_body`）。`SYSTEM_PROMPT` 明确三件事：会话不是恢复（`NOT_ASSERTED`）、
   没信号不是健康、休息是能力事实不是义务（没人逼她睡或吃）。
3. **能选择休息/继续，后果仍由 owner 说。** 姿态与休息本来就是 World 的 `POSE`/`REST`（`bodyact:`
   命名空间，AR-0 准入 + Resolver 判据已在 rc1 里），A 侧一个字没改；本包只是让决定看得见
   `can_start/can_stop/why_not` 与已开会话。`REST` stop 后 owner 自己回 `completed`，所以
   `life_body_context` 把 `completed/interrupted/inactive` 都映射成"没有开着的会话"，
   **不等于**恢复；`recovery.state` 原样回显 owner 自己的词，不重命名。
4. **分享是窄的生活侧效应（`life_sharing_context`）。** 只调用 B 文档化的四个方法：
   `list_shares(include_text=False)`（取全部再本地筛 DRAFT/DEFERRED，因为 B 的 counts/inbox
   是按返回行统计的，按状态过滤会把本地投递计数藏掉）、`read_share(include_text=False)`、
   `prepare_share(**documented kwargs)`、`apply_action(**documented kwargs)`。所有返回都压成
   有界的 `{ok,status,reason_code,share_id,state,head_version_id,delivery_count,receipt,deferred,
   dropped,reconciled,retryable}`；`delivered` 只在"这一次真的落地"时为真
   （`ok` + `applied/already_applied` + `LOCAL_DELIVERED`），另有 `state_delivered` 表达状态事实。
5. **正式入口接入。** assembly 新增 `resolve_sharing_service(env)`（B 的 `sharing_from_env`，
   缺绑定就是具名 `UNAVAILABLE`），把 service 交给 cycle；cycle 每轮 decision 读一次
   `sharing_decision_context()`（`shares` + `sources`：当前 work artifact 与正在读的材料，
   不列作品库）+ `allowed_share_ids`；决定提交后、本轮 step 之前
   `_apply_sharing_action(record)` 调一次 owner —— 不改变本轮路由，也不把"模型说发送"当发送。
   `project_options` 用 B 自己的 `is_share_title()` 过滤分享件，避免草稿变成"可继续的作品"。
6. **AG-1 只验形状。** `validate_sharing_action`：op ∈ `prepare/send_local/defer/drop`；`share_id`
   必须是 `artifact:`、`expected_head` 必须是 `artifact-version:`（CAS 由 owner 判）；
   `prepare` 必须有显式 `share_text` 或显式 `excerpt_chars`（1..400）；`defer` 必须写
   `defer_condition` 或 `opportunity_ref`；`audience/channel/recipient/to/email/url/telegram/api`
   这类字段**一律拒**（B 固定 `local:owner`/`local_inbox`）。`DecisionRecord.sharing_action`
   随记录落盘可重建；`validate_decision_output` 的允许键集合加入 `sharing`。

## Alternatives considered

* **让 cycle 自己读 body port 拼一份"身体 prompt"。** 最强立论：能顺手拿到 `progress_band` 等
  更多字段。否决：那就是每轮第二次身体读，两读之间可能不一致，且把 owner 的原始字段搬进
  prompt。改为"同一列的投影"，`progress` 就老实写 `NOT_READ`。
* **把 `REST` 的 `recovery_state` 直接映射成 recovered/not。** 最强立论：模型只要一个布尔。
  否决：owner 的词表里没有"恢复了"，`completed` 只表示会话结束；把它当恢复就是我们自己在
  造身体事实。保留原词 + 恒定 `NOT_ASSERTED`。
* **让 AG-1 也校验 share/source 是否在本轮 offer 里。** 最强立论：引用校验集中一处。否决：
  AG-1 的输入里没有 owner 的读取结果，把 offer 列表塞进 AG-1 会造第二个 allow-list 真相；
  于是形状在 AG-1、成员资格在 cycle（`SHARING_SOURCE_NOT_OFFERED` /
  `SHARING_DRAFT_NOT_OFFERED`），存在性仍由 owner 判 `not_found`。
* **分享用独立 decision 字段 `decision=SHARE`。** 否决：那会把一次生活侧副作用变成新的决策
  词表，绕过候选/采用的既有语义。改为**附加请求**（与 `later_item`/`method_proposal` 同一层），
  可与 `NO_ACTION` 同时出现。
* **分享前自己再读一次 `read_share` 校验 head。** 最强立论：少一次 owner 冲突。否决：CAS 是
  owner 的事，A 读到的 head 也可能过期；直接用 block 里的 head，让 owner 回 `conflict`。

## Consequences

* 决定模型现在看到的是：`capacity: LIMITED`、`burden:[{fatigue_high, strong}]`、
  `recovery:{session: OPEN, state: active, claim: NOT_ASSERTED}`、
  `rest:{can_start: false, can_stop: true, why_not: RECOVERY_ALREADY_ACTIVE}`，
  和同一份 `world`（在哪/手上/能去哪）。"什么都没读到"是一条具名 `unknowns`。
* 已知代价：
  1. 每个 decision 轮多一次 `list_shares` owner 读（不花 LLM，但确实是 owner 调用）；
  2. `progress_band` 不进 prompt（`NOT_READ`），因为 capacity 列没带它；
  3. 分享的 `sources` 只有"当前 work artifact + 正在读的材料"，其它作品要先成为本轮对象；
  4. prompt 超限时分享块会被整块丢弃（选择仍可能），这是刻意顺序；
  5. `life_sharing_context.report()` 的 `delivered` 语义是"这一次落地"，与 `state_delivered`
     分开，读的人不能把"被拒的重复发送"当投递。
* 未做/不能宣称：没有外发（`channel` 只有 `local_inbox`，生产 kill switch 一行没碰）；
  没有跑 `life_supply` socket 端到端；没有真实模型调用（父级统一做）；没有删除任何夹具。

## Verification

见 `docs/engineering/v04-body-sharing-core-result.md` §4（命令、exit code、证据与失败形状）。

## 收口补充（本轮定点修正，取代本 note 中与之冲突的说法）

1. **主链不再用 helper 冒充**：新增 `tests/test_v04_sharing_life_chain.py`，走真实
   `assemble_autonomous_life` + 原始 JSON 决策 + `evaluate_once`（prepare→DRAFT→defer→
   send_local→1 receipt；原作品 head 不变；分享件不进 project_options）。原先只调用
   `_apply_sharing_action` 的用例降级为 owner 层边界用例（drop/换版/未 offer/CAS），并在文件里
   注明主链在别处。
2. **有副作用的 NO_ACTION 不再静默**：分享真的落地时该轮 `outcome="SHARE_ACTED"` 并带
   `sharing_outcome_note`；被拒时保持原 outcome。
3. **prompt 带"那句话"与"在等什么"**：`prompt_block` 改为 1 + N 次只读（`list_shares` +
   每件 open share 一次 `read_share(include_text=True)`），每件带 <=160 字符的草稿正文、
   `source.version_id`、`source_current{readable,stale,matches_recorded,reason_code}`、
   `deferred{condition,opportunity_ref}`；终态不再 offer。
4. **progress 不再是死状态**：`life_body_context.build(..., recovery=...)` 接受 owner 自己的
   `data.recovery`（`state/trend/progress_band`），cycle 用 `_body_recovery_facts()` 在同一个 body
   port 上多做一次只读取到它（文档里写明代价）；`claim` 仍恒 `NOT_ASSERTED`。
5. **装配修复**：`resolve_sharing_service(env, subject_id=..., grant_id=...)` 用实例自己的
   subject/grant 按 owner 的 env 名补齐，`assemble_autonomous_life(...)["sharing"]["status"]`
   现在真的 `READY`（此前恒 `SUBJECT_NOT_CONFIGURED`）。
6. **身体链走正式轮**：`tests/test_v04_body_life_chain.py` 用真实实例证明姿态经 owner 真的改变、
   owner 的 REST 拒绝理由随之从 `REST_REQUIRES_RESTING_POSE` 变成 `RECOVERY_NOT_NEEDED`、
   原读材料 Activity 由 START→CONTINUE 承接；"开着的会话"仍只在隔离 body 夹具（声明 seed）里证明。
7. `tests/test_v04_practice_life_stage.py` 的 FAIL 来自 Memory port 的 `MEMORY_DB_MISSING` 文件检查
   （`life_experience_port.py:293-296`），本写集不构造 Memory，无负控 → 不归因、未改。
8. **被撤源/不可读草稿的正文扣留进入表达视图（2026-10-24）**：B 的 `read_share` 既定返回
   `text_withheld` / `text_available` / `withheld_reason_code`（来源读被拒或不可达时整份不投影
   正文），A 的 `_share_detail` 此前只把 `ok` 当"详情可读"，把扣留正文投影成空
   `text_excerpt`，看起来像空草稿。现在这三个事实原样进入 `prompt_block` / `read_draft`
   与 adapter 白名单（`_bounded_sharing`）；`missing` 字段保持 `None`，不猜。发送仍由 owner
   以 `stale_source` 拒且零写，未新增字段/合同/外发。
   **helper 与正式链的边界**：最初的冒烟直接 `validate_sharing_action` → `_apply_sharing_action`，
   并手塞 `last_sharing_block`、`candidate_read=None`，那只证明 helper 可达，不能证明扣留事实
   经过正式回合。补充 `tests/test_v04_sharing_life_chain.py` 的正式链用例（真实
   `assemble_autonomous_life` + `evaluate_once` + 原始 JSON 经 AG-1）与改写后的冒烟：
   来源拒读后本轮 Decision 视图带扣留原因，脚本仍请求发送时 owner 拒、本地零增量；
   正常 prepare→defer→send_local 只有一条本地 receipt。Decision 仍是脚本替身，撤权仍是
   source-only 读拒绝，不称真实 Memory 治理或用户收到。证据：
   `tests/test_situation_expression_behavior.py`、`tests/test_v04_sharing_life_chain.py`、
   `scripts/situation_expression_behavior_smoke.py`。
9. **来源名字与"列表被截断"进入表达视图（2026-10-25，v0.5 第一块）**：`shareable_sources` 的
   每一行原来只有 `artifact_id` / `kind`，最终 prompt 里来源没有可读名字；`prompt_block` 的
   `truncated` 又被 adapter 白名单丢掉，模型看不出展示列表不完整。现在只有**同一次已实际读取**
   能配对时才带 `title`：作品名来自本轮 resume point（标题与 artifact id 同一次返回），阅读材料名
   来自本轮 `private_material_situation` 的缓存（`reading_artifact_id` 必须对得上）；缺标题或身份
   不一致就省略，为凑名字再加 owner 读一律不做。`truncated` 真值经 `_bounded_sharing` 透传，
   只描述这份 sharing 列表；整块仍会被既有 prompt 预算整块裁掉，不宣称最终 payload 保留完整。
   标题不是版本、不是披露权，不新增受众/通道/披露规则。证据：
   `tests/test_v05_results_expression_views.py`、`scripts/v05_results_expression_smoke.py`。
   **两层截断各自如实上报（2026-10-25 收尾）**：此前 `list_shares` 只用自己收到的行数与
   `MAX_LIST`（同为 100）重算，丢掉 owner `list_projects` 已有的 `truncated`；`prompt_block`
   又只透传这一层，按 `MAX_PROMPT_SHARES` 截掉 OPEN 草稿时仍报 `False`。现在两处都只修
   既有字段的准确投影：owner 列表被截 → 分享列表 `truncated=True`（101 条真草稿返回 100 行，
   `counts.total` 仍是"本答案返回的行"）；6 条 OPEN 草稿按 prompt bound 展示 5 条 →
   `prompt_block.truncated=True`。缺 `truncated` 的 owner 答案保持 `False`，不发明截断；
   上限、过滤、排序、读次数与写口未变。证据：同一测试模块的 `RealOwnerShareTruncationTest`
   （30 秒内真 Supply owner 建 6/101 条草稿）与 B 的独立截断链 7 项 check。
10. **真实结果换版后的后续选择（2026-10-25，v0.5 第三条）**：正式链把"真实作品版本 →
    草稿精确绑定那一版 → 作品续写到下一版 → 下一轮请求如实报 `source_current.stale` /
    `matches_recorded=false`（旧正文仍可读，不伪称删除）→ 主动发送被 owner 拒
    `stale_source`（零写）→ 新 prepare 绑定新版本 → 只有一条本地 receipt"整条跑通；
    "来源不可读"（`SOURCE_GOVERNED`，不是 `NOT_FOUND`，sentinel 不出现在请求/答案里）与
    "草稿被另一个合法 writer 改过"（`conflict`，原因码不是 `SOURCE_*`）是两条独立负控。
    **零业务源码改动**：现有 owner 合同与既有投影已经覆盖，本轮只补正式证据、正负控与
    文档。见 [v0.5 第三条](../feature/2026-10-25-v05-result-sharing-continuation.md)
    与 `docs/engineering/v05-result-sharing-continuation-20261025.md`。
