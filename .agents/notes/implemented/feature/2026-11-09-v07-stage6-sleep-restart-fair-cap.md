# 0.7 第六阶段：公平候选、正式 sleep_like、同目录冷重启与低预算书签

Status: implemented

## Problem

三处边界各自都对，但接在一起会互相拆台：

1. **World 多产源挤掉连续性。** AG-1 每轮只有 16 条认知候选，`CandidateMaterializer`
   在 `deduped > global_cap`（生产里 `global_cap = ag1.MAX_COGNITIVE_CANDIDATES = 16`）
   时按 `candidate_id` 直接截前 16 条。World 一次能产出十几条 home 机会，于是
   `RESUME_ACTIVITY` / 主体自己的作品可能被整个挤掉——第五阶段的共处恢复用例只能
   走到候选更少的书店才能稳定跑。
2. **`sleep_like` 在产线上不可达。** `/2026-10-26-v04-day-life-body-actions` 当时明确
   只准入 `rest`，编码、解析、bridge 都拒 `sleep_like`。World owner 早就能执行
   `sleep_like`（躺姿 + 本处真实休息面），但 Life 侧没有任何合法拼写，机会源也从不发它。
3. **“命令成功”不等于“睡了一夜”，也不能拿同一条绝对事实完成两个 Activity。**
   REST 完成事实是恢复**会话**状态；LR-5 又把一条绝对世界事实（例如
   `world_fact:fixture:<lamp>:off`）绑定到**一个** Activity 的完成证据
   （`CONSEQUENCE_COMPLETION_EVIDENCE_BELONGS_TO_OTHER_ACTIVITY`）。
4. **冷重启没有被证明过。** 之前每个演示都只跑一个进程内实例；“关掉旧 owner、从同一
   持久实例重开、逐项核对灯/册子/inbox/future/预算/未完成 Activity”没有证据。

## Decision

1. **公平轮询截断**（`candidate_sources_ag0.CandidateMaterializer.bounded_candidates_by_source`）：
   只在 `deduped > global_cap` 时启用。按 `candidate_source_round_order()`（就是
   `VALID_CANDIDATE_SOURCE_KINDS` 的稳定读序，连续性/主体性来源在前）逐来源轮转，
   每轮每来源取一条直到 cap；最终仍按 `candidate_id` 排序输出。不提高 16、不评分、
   不随机化、不改来源合法性/去重/截止/资格；不存在的来源不造候选；来源数超过 cap 时
   是确定性有界退化，**不冒充**每个来源都保底。名额与输出顺序分开：保底是“能进入本轮
   候选”，不是“强制采用”，也不发资格。
   2026-11-09 定点补：来源**内部**也按动作类别公平（`category_fair_order`）——先给每个非空
   类别留一份（类别由 WORLD 候选自己的 target 命名空间识别：`stay:`/`bodyact:pose:`/
   `place:`/`dailyact:toggle_fixture:` …），多余份再按“最可替代者先让”次序（沿用 World
   自身上限 `_bound_world_opportunities` 的 stay→pose→move→arrange→toggle 让位次序）排在尾部。
   修复的具名故障：晨起 home 轮池子 17 条（World 12 + PERSONAL 4 + USER_REQUEST 1），旧实现
   在 World 桶内按 `candidate_id` 取前 11，哈希排最后的那条（父级实跑里正是那盏灯**唯一**的
   `toggle_fixture:on`）被整条截掉，晨起开灯只剩 `NO_ACTION`。现在单份类别永不被截，
   被截的只是 `stay:` 之类的多余停留；跨来源公平不等于同一 World 的每个类别都可见，
   两次公平各自独立、都是名额分配而非评分。
2. **`sleep_like` 按 owner 合同准入**：`life_body_action_targets.REST_MODES` 与两份
   `action_intent.REST_MODES` 扩为 `rest`/`sleep_like`；`start` 可带任一 mode，`stop`
   只可 `rest`——编码、解析、两份 `validate_verb_params` 都在铸授权前拒绝
   `stop+sleep_like`，缺字段/多字段/非法枚举照旧拒绝。`observed_body_action_opportunities`
   只在 gateway `start_available is True` **且** `sleep_like_available is True` 时追加
   `bodyact:rest:start:sleep_like`，普通 `rest` 保留。gateway 的 REST `current` 只读多暴露
   `recovery_mode`。`body_action_observed_fact` 对 `sleep_like` start 要求会话
   `active` **且** `mode=="sleep_like"`，普通 rest 的 active 不再冒充睡眠。
3. **重复绝对状态按 execution 归属完成证据**
   （`activity_progress_completion_lr5.NaturalCompletionEvaluator.evaluate_completion`）：
   completion eligibility 的首个 authoritative ref 先取**本次 execution 自己**的
   `progress_evidence_id`（`pevid:`，按 `event_id`+`settlement_ref` 唯一），其后才是
   `world_fact:...` 等状态值。同一盏灯连续 off→on→off→on 时，第二、第四次同值动作因此
   有自己的证据，不再被 `CONSEQUENCE_COMPLETION_EVIDENCE_BELONGS_TO_OTHER_ACTIVITY`
   误拒；跨 Activity 冒用的检查没有放松——别人的状态值仍在别人的 auth refs 里，`pevid`
   本身也不会共享。演示恢复原链：买灯 → ARRANGE → 正式 off → 正式 on → 20:00 灯真的 on
   时写小册子/本地分享/共处 → 睡前正式 off → 次日晨正式 on；四次 TOGGLE 各一个独立
   Activity、各一个 completion evidence。`sleep_like` 前的真实负荷仍由正式 MOVE 街区步行
   + owner tick 结算达到，不写 fatigue、不改阈值。
4. **同目录冷重启**（`scripts/v07_daily_life_demo.py::Demo.reopen_existing`）：先关 Life
   runtime，再停旧 World 服务并 join 线程；然后按 Supply owner 自己的
   `server.shutdown` / `server.server_close` / `drain` / `service.close` 正常关停旧 Supply
   服务端（test 夹具 `Services.stop` 补上线程跟踪，并用 owner 自带的
   `prepare_socket_path` guard 回收自己已关闭的 socket 路径——不是裸 unlink），随后在同一
   数据目录/socket 路径重建**新的** Supply server + service、**新的** World 服务、同一
   evidence/controls SQLite 文件上的**新的** Memory 句柄、**新的** Life runtime 与装配。
   重启前后逐项相等：灯 placement/power、册子 head/version、本地 inbox 计数、future
   OPEN/due、未完成 Activity id/status/checkpoint、预算账；重开后 fresh Decision 接续同一
   Activity。这是同目录正常关闭/重开模拟，不冒充 kill/crash/跨机器恢复。
5. **低预算书签用 unit 证明**：`LifeRequestBudget(utc_day_max=1, run_window_max=1)` 用掉
   唯一名额后，`BudgetedCognitionAdapter` 在 reserve 处拒绝，adapter 零调用、checkpoint
   字节不变；同目录重新打开同一 run_id 不补额。

## Alternatives considered

* **先给 AG-1 的 16 条上限提额**：最强理由是“一行解决 World 挤占”。否决——16 是既有认知
  请求硬边界（承重墙），提高会让每轮提示词无界增长；本轮只改名额分配，不改总量。
* **在 World 机会源内部按价值/随机挑选**：否决——那是价值排序，正是 AG-0 禁止的东西；
  公平必须只按来源与 id 确定性轮转。
* **把 `MAX_WORLD_OPPORTUNITIES` 从 12 降到 11 硬凑 17→16**：最强理由是“改一个数字，
  池子正好装下”。否决——非 World 候选数本身随轮次变化（PERSONAL 最多 4，
  USER_REQUEST/RESUME/CURRENT 视情境增减），固定降 World 仍会溢出，而且会永久少给她
  一条真实的 World 机会；正确层是 cap 分配，不是供给上限。
* **把 `RESUME` 单独塞进保底名单、允许超过 16**：否决——同样是放宽总量；且会给一类来源
  特权，违背“名额 vs 顺序分离”。
* **在候选里直接伪造一个 `sleep_like` 或由 Life 按时间决定她该睡**：否决——机会只能来自
  owner 的 `get_available_actions`；疲劳门槛、躺姿、床面都在 Body/World owner 手里。
* **把 on/off 两个绝对状态挪到睡前/次日晨来避开重复**：最强理由是“同一条绝对事实属于一个
  Activity，重复即为跨 Activity 冒用”。否决——这等于让证据身份只由**状态值**决定，而状态值
  无法区分两次真实执行；正确做法是让完成证据绑到本次 execution 自己的 `pevid`，状态值仍
  留在 auth refs 里供匹配。跨 Activity 冒用照样被拒，同时同一天连续 off→on→off→on 不再被
  误伤。
* **冷重启时 kill Supply 服务并在同路径重开**：否决——进程被 kill 时没有正常生命周期清理，
  只能靠裸 unlink 陈旧 socket。改为走 owner 自己的 `shutdown`/`server_close`/`drain`/
  `service.close` 正常关停，再用 owner 自带的 `prepare_socket_path` guard 回收本进程已关闭的
  socket 路径，然后在同数据目录重建服务端对象。

## Consequences

* 现在同实例链能证明：拥挤 home/living 下 `RESUME` 仍在 16 条内可见；同一盏灯连续
  off→on→off→on 四次真实 TOGGLE 各一个独立 Activity、各一个 completion evidence（`pevid`），
  证据互不冒用；`bodyact:rest:start:sleep_like` 经正式 REST 端口开启 owner 的
  `recovery active / mode=sleep_like`，停止后 POSE standing；当晚 due fitem 次日晨由正常
  PERSONAL_OPPORTUNITY 候选 START 并真的续写一版；同目录冷重启把旧 World 与旧 Supply 服务端
  都正常关停、在同数据目录重建新对象后，各持久事实逐项相等、fresh Decision 接续同一 Activity。
* 已知边界：业务钟跨夜跳跃只推进业务时间，不冒充整夜身体恢复；恢复证据是会话
  `active`/`mode=sleep_like`，不是疲劳/健康数值；`sleep_like` 的 RESUME/ATOMIC 等通用规则不变。
* 本演示比之前长，只对**本 demo 进程**放宽了 profile 的每日轮次/动作计数
  （`CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY`/`..._ACTIONS_PER_DAY`）；产品请求上限与产品行为未改。
* 两个 anchor `components/life/scripts/autonomous_life_assembly.py` 与
  `components/life/scripts/life_sharing_service.py` 未编辑。旧的 `sleep_like` 禁令由本单明确取代
  （/2026-10-26-v04-day-life-body-actions、/2026-10-21-v04-world-body-resident-and-rest 的事实
  仍保留，仅互链）；共处恢复同环境用例见 /2026-11-09-v07-stage5-sharing-presence。
