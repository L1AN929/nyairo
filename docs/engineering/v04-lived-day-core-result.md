# v0.4 主线 A —— 连续日常生活（core）：现场 context / prompt 容量 / 拿放路由 结果

日期：2026-11-13　施工：A（主生活循环）　唯一开发树：`D:\nyairo-rc1-body-backend`
HEAD：`41e818faaa24115fbf43db2977b15585c04144af`（保留全部既有 dirty，未 commit / 未部署）

---

## 1. 实际能力（这一版她能做到什么）

| 能力 | 现在真实行为 | 证据 |
| --- | --- | --- |
| 看见现场 | 决定 prompt 里的 `situation_context.world` 给出：她在哪（**名字**+区域+当地时间）、手上拿着什么（含名字）、能走去的每个地方（**秒数**+营业状态+店内在不在视野内）、这轮真能做的具名清单（拿/放/待一会/姿态/用掉）、以及读不到的部分（具名） | `tests/test_lived_day_core_v04.py::LiveScenePromptTest`、`LiveSceneContractTest`、`LiveSceneFallbackTest` |
| 未知不冒充 | 她不在的地方保留 owner 自己的 `observed=not_observed`，店内细节一概不复制，并记 `OPPORTUNITY_DETAIL_NOT_READ`；读不到的地点名/手/营业/路程各自具名 | `test_a_place_she_is_not_in_never_borrows_details_from_inside_it`、`test_an_unread_catalog_names_the_unknown_instead_of_inventing_a_place` |
| prompt 容量 | 超限时按声明顺序**整块**丢弃并重新序列化，结果永远是完整可 `json.loads` 的对象；candidate id / `legal_decisions` / `target_refs` / `legal_decision_enums` / 现场都保留；报告只有计数 | `PromptCapacityTest`（4 条） |
| 拿放准备 | `dailyact:` 从 AG-1 选中 → AG-2 step（B 的 `build_daily_action_step`）→ AR-0（单 target，`PICK_UP`/`PLACE` 窄准入）→ bridge/Resolver；adoption 的完成事实取 B 的 `daily_action_completion` | `test_a_dailyact_candidate_is_routed_to_the_targets_own_step_builder`、`test_the_adoption_contract_names_the_targets_own_completion_fact` |
| 完成核对 | 执行后**重新观测一次**，由 B 的 `daily_action_observed_fact`（缺席则 fresh snapshot 的 `hands`）核对；一致才给 LR-5 提交 `world_authority` 进度证据；不一致/读不到 → `STEP_UNSETTLED` 且不写进度 | `test_a_pick_up_counts_only_when_the_fresh_snapshot_shows_it_in_hand`、`test_a_pick_up_the_owner_does_not_show_is_unsettled_and_writes_nothing`、`test_a_place_is_judged_the_other_way_round`、`test_an_unreadable_world_after_the_action_is_unsettled_not_a_success` |
| 步骤 ≠ 完成 | 步骤成功只写 LR-5 进度；Activity 完成仍只看 LR-5 的 `WORLD_FACT_MATCH` | 同上（`_record_daily_action_progress` 不写完成事实之外的东西） |
| 步骤就绪 | `dailyact:` 是 Life 认识的 target 命名空间（不再被判 `TARGET_NAMESPACE_UNKNOWN`） | `life_step_readiness.TARGET_DAILY_ACTION` + `life_integration_ag2` 链路 |

**不做 / 未做**：没有新增 owner、没有新 schema、没有日程/配额/饥饿/人格分；`WAIT` park 与显式
`RESUME` 的 owner 语义一行未改（仍由 LR-2/LR-4 拥有）；真实旅程/停留的按时推进在 C 的
resident 里，A 这边每 poll 不做新 Decision 的行为未变。

## 2. 接口与落点

| 文件 | 变动 |
| --- | --- |
| `components/life/scripts/life_live_context_view.py`（新） | `read_live_context(provider, *, limit=8, observed_at=None)`：契约优先（`provider.daily_life_context`），否则同一观测的只读投影（`source="provider.observation_fallback"`）；`bound_for_prompt(context, *, limit=8)`；`empty_context(reason)`；常量 `SCHEMA/STATUS_*/REASON_*/UNKNOWN_*` |
| `components/life/scripts/autonomous_life_cycle.py` | `daily_action_targets_module()`；`DAILYACT_REF_PREFIX`/`PICK_UP_VERB`/`PLACE_VERB`/`DAILY_ACTION_ACTIVITY_KIND`；`_world_ref_token()`；`world_live_context()`；`self.last_live_context`/`last_world_observed_at`；`_refresh_world_opportunities()` 末尾读现场；`decision_situation_context()["world"]`；`_candidate_is_world`/`_chosen_target_ref`/`_readiness_revision`/`_world_step_spec`/`_world_adoption_spec` 认识 `dailyact:`；`_daily_action_adoption_spec()`、`_classify_daily_action()`、`_daily_action_observed_state()`、`_record_daily_action_progress()` |
| `components/life/scripts/agency_cognition_adapter.py` | `_fit_prompt()` + `_prompt_trim_ladder()`（结构化裁剪，替换 `text[:N]+...(truncated)`）；`last_prompt_trim`（只有计数）；`_bounded_live_context()`；`_bounded_situation()` 输出 `world`；`SYSTEM_PROMPT` 先读现场 + 写明"连续的一天" + `unknown ≠ 没得做`；`MIN_CANDIDATES_IN_PROMPT`、`MAX_LIVE_CONTEXT_ENTRIES` |
| `components/life/scripts/life_step_readiness.py` | `TARGET_DAILY_ACTION="dailyact:"`，进入 `namespace_of` / `canonical_target_ref` / `__all__` |
| `components/life/scripts/action_reality_ledger.py` | **一行**：`validate_prepare` 的动词窄准入加入 `PICK_UP`/`PLACE`（见 §5 越界说明） |
| `components/life/scripts/world_journey_action_adapter.py` | **一行**：`DELEGATED_VERBS` 追加 B 的 `life_daily_action_targets.DAILY_ACTION_VERBS`（import 常量，不复制），正式生产 resolver 才真的把拿放交给 World 正式入口（见 §8.3a） |
| `tests/test_lived_day_core_v04.py`（新） | 26 个 exact 用例（见 §4） |
| `.agents/notes/implemented/architecture/2026-11-13-v04-lived-day-core-continuity.md`（新） | 决策 note |

与 B / C 的接缝（本包不改）：provider 的 `daily_life_context(limit=8)`、`life_daily_action_targets`
的 `encode/parse/build_daily_action_step/daily_action_completion/daily_action_observed_fact`、
resident/runner/live_day 入口。B 已落地（本包按**真实签名**接线并跑通）：`build_daily_action_step(target, *, origin_ref=None)`、
`daily_action_completion(target) -> {fact_key:"world_daily_placement_ref", expected_value:"world_fact:placement:..."}`、
`daily_action_observed_fact(parsed, snapshot)`；hand 槽是 `left_hand`/`right_hand`。

## 3. 修掉的真实问题（沿真实问题修，不铺审计）

1. **`build_prompt` 的伪 JSON**：`text[:N] + ' ...(truncated)"}'` 会在字符串中间切断并伪造括号，
   `json.loads` 必失败 → 记成模型失败。改为 `_fit_prompt` 的结构化裁剪（17 步声明顺序）。
2. **`dailyact:` 是死路**：cycle 不认识这个命名空间 → 有候选也永不执行。现在 step/adoption/
   完成核对整条接通，且 `life_step_readiness` 不再把一条 `dailyact:` 的 ACTIVE Activity
   判成"命名空间未知"。
3. **`source_ref` 被 B 的编码里的 `>` 卡住**：LR-2 的结构化引用只接受
   `[A-Za-z0-9_.:/-]`，`dailyact:place:...>surface` 直接抛 `ContractViolationError`。
   A 侧用 `_world_ref_token()` 只替换分隔符（不动 object/verb/surface 本身），已实测通过。
4. **B 的解析器会抛而不是返回 None**：`parse_daily_action_target` 对畸形 `dailyact:` 抛
   `DailyActionTargetError`。cycle 现在把它转成具名 `STEP_REFUSED`（执行侧）/
   `AutonomousLifeError`（adoption 侧），不会把一轮决策打成崩溃。

## 4. 测试命令与退出码

```
# 本包（26 个 exact 用例）
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/d/nyairo-rc1-body-backend && \
  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_lived_day_core_v04.py"
→ Ran 26 tests ... OK        exit 0

# 直接受影响的既有真实链（AR-0 四动词未退化）
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_day_life_body_actions.py
→ Ran 14 tests ... OK        exit 0   (8.1s)
```

用例清单（26）：`LiveSceneContractTest`(3) `LiveSceneFallbackTest`(7) `LiveScenePromptTest`(2)
`PromptCapacityTest`(4) `LivedDayRouteTest`(10)。原生 Windows 上 live-view/adapter 两类照跑，
`LivedDayRouteTest` 因 `fcntl` 自动 skip（与本仓既有 Life 用例同一限制）。

**同批跑的其它文件（如实登记，未归因）**

| 文件 | 结果 | 失败形状与位置 |
| --- | --- | --- |
| `tests/test_day_life_continuity.py` | 21 pass / 1 error | `_world_cycle() got an unexpected keyword argument 'snapshot'`（该测试文件自身 helper 签名；该文件在本轮开工前就已是 dirty，且本轮 22 条里含别人新增用例） |
| `tests/test_life_v1_appraisal_context.py` | 1 error / 1 fail | error：`KeyError: 'appraisal'`（需要 appraisal source 的断言）；fail：`test_one_day_life.DScenarioBase.test_artifact_choice_commits_a_real_supply_version` 期望 `ACTED` 得到 `ALREADY_DONE` |
| `tests/test_subject_reading_cycle.py` | 1 fail | `ALREADY_DONE != ACTED`，`step_path=AR0_READING_CLOSEOUT_WORLD_ROUTED`，`owner_status=OK / effect_state=NO_CHANGE` |

后两条是同一形状：closeout 步骤由确定性 writer 得出 `NO_CHANGE`，round 报 `ALREADY_DONE`。
该路径（closeout + writer）本包**没有**改动，但**我没有**建立负控（没有在等价树上回退本包改动复跑），
所以**不作"既有失败"归因** —— 只登记形状，留给父级带负控判定。

## 5. 越界说明（必须让父级知道）

另有两处与本包写集相邻的必要改动，一并说明：
`components/life/scripts/world_journey_action_adapter.py` 的 `DELEGATED_VERBS`（追加 B 的常量条目）
与 `tests/test_day_life_body_actions.py` 里那条"委派动词恰好是 CONSUME/POSE/REST"的旧断言
（改为引用 B 的 `DAILY_ACTION_VERBS`，集合仍是精确比较）。理由都是同一条：拿放要真的到 owner。

`components/life/scripts/action_reality_ledger.py::WorldActionAdapter.validate_prepare` 的动词白名单
从 `{MOVE, POSE, CONSUME, REST}` 改为 `{…, PICK_UP, PLACE}`，并加注释。**这不在 A 声明的写集里**。
理由：B 的 `dailyact:` 一步在 AR-0 就会被 `unsupported WORLD_ACTION verb` 拒掉，而 AR-0 的动词
白名单全仓只有这一处；重复一份在 A 侧必然漂移。改动仅是窄准入（单 target、无自由 `params`），
owner 侧 allowlist / gateway 曝光 / Resolver 一个字没动，`test_day_life_body_actions.py` 14/14 通过
证明四动词未退化。

## 6. 限制与残留

1. **拿放链路现在真的到底了**：`tests/test_lived_day_chain_v04.py` 在真实 World service 上跑通了
   `PICK_UP` 与 `PLACE`（见 §8），两者都走正式生产 resolver 的委派，并且都改了 owner 自己的
   placement。
2. **一条链的边界**：这条链用注入时钟（把 Life 时刻推到 owner 公布的到达时刻）走完，含
   `PICK_UP` 和 `PLACE` 两项日常动作；不是 24h 验收，也不是 Gemini 自主一天（模型回放原始 JSON）。
3. **拿放 Activity 现在会由 LR-5 自己的完成契约收尾**（已修，见 §8.3b）：原因是进度证据绑不上
   Activity 的 target refs，不是完成含义有问题。修完实测 `progress_state=COMPLETION_ELIGIBLE`、
   Activity `COMPLETED`；本链不再用 ABANDON 代劳，也没有新 evaluator。
4. 回退分支（契约缺席）里 `preparation` 恒为未知（`PREPARATION_NOT_KNOWN`）。
5. `over_budget=True`（极端小上限）只记 warning，不阻断；此时从尾部整条丢候选，
   `MIN_CANDIDATES_IN_PROMPT=1`。
6. 现场只从**这一次**观测派生：不重观测、不读文件/SQLite、不排名、不自动选择。
7. 这条链暴露并修掉了一个真 gate（见 §8.3）：`has_foreground` 曾把 `WAITING` 当"还在忙"，
   于是 park 之后**任何** World/阅读/收尾步骤都不会被提供，只剩 work 步骤可走。

## 7. 现场纪律

`PYTHONDONTWRITEBYTECODE=1` 全程；语法用 `ast.parse`（无 `py_compile`）；夹具
`/tmp/nyairov04a-livedday-*` 与 `/tmp/nyairov04*` 全部保留（失败也留）；未删除/未移动任何文件；
未改 MANIFEST / 历史 fix 脚本 / 密钥 / 原实例配置 / Memory binding；未 commit、未部署。

## 8. 一个实例的一条连续日常链（真实 owner，正式生产 seam）

`tests/test_lived_day_chain_v04.py::LivedDayChainTest::test_one_instance_reads_parks_picks_up_goes_out_and_resumes_the_same_activity`

夹具复用既有真实世界 harness（`tests/test_world_daily_life_continuous.py::EpisodeFixture` /
`tests/test_life_v1_integration.py::DScenarioBase`：真实 Life supply owner、真实 `WorldBodyService`
（真 lease + 真 gateway.sock + 真 Resolver + 真基底）、真实 `ProductionLifeRuntime`、真实 AR-0 适配器，
以及**这个实例自己装配的** `RealJourneyWorldActionAdapter(JourneyWorldResolver(...))`）。**没有测试
router、没有替换 resolver**，唯一不是产品代码的是决策脚本（回放原始 JSON 的 `ChainCognition`）。

链上每一步都读 owner 自己的答案（`Ran 1 test in 34.1s ... OK`，exit 0）：

1. `read` / `continue` ×3：真实材料按页读到 `LAST_PAGE_3_OF_3`（`step_readiness.available=False`，
   `reason=READING_EXHAUSTED`；AG-1 的合法集里 `CONTINUE` 已撤、`WAIT` 在）。
2. `park`（WAIT）：`adoption=APPLIED`、`exit.owner_status=WAITING`，LR-2 记录是 `WAITING`
   （不是 PAUSED），前台指针仍指着同一条 Activity —— 这是这个 owner 的 park 语义。
3. `pick_up`（`dailyact:pick_up:left_hand:lived-bowl-01`）：AG-1 → AG-2 → AR-0 →（正式）
   `JourneyWorldResolver` → **委派** `WorldBridgeResolver` → Intent Executor → gateway → **真
   Resolver**；基底 placement 变成 `kind=hand / slot_id=left_hand`，完成判据来自动作后 fresh 观测
   （`re_read=True`），进度 `COMPLETION_ELIGIBLE`，Activity 由 LR-5 自己的完成契约判为 **COMPLETED**。
4. `place_down`（`dailyact:place:room:home:bedroom:lived-bowl-01`）：同一条正式链，owner 的 placement
   真的从手里变成 `kind=room / location_id=home / area_id=bedroom`，Activity 同样 **COMPLETED**。
5. `move_out` → `IN_TRANSIT`（位置仍是 `home`）→ 时钟推到 owner 公布的到达时刻 →
   `settle_world_journey`：`arrived=True / location=street_front`。
6. `move_home` → 同上回到 `home`。
7. `stay`（`stay:home:REST:300`）→ 时钟推 305s → 下一轮 `phase=END` 由 LR-5 记 checkpoint。
8. owner 真发布材料下一版（真实 `commit_version`）→ `maintain_waiting`：`checked=1 changed=1
   satisfied=[rcond:…] eligibilities=[relig:…] auto_resumed=False` → 下一轮 `RESUME` 候选出现 →
   `decision=RESUME / adoption=APPLIED`，**同一条** `activity_ref` 回到 `ACTIVE` → `CONTINUE`
   读新版第 1 页（`state=AVAILABLE next_page=2`）。

### 8.1 命令与退出码

```
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/d/nyairo-rc1-body-backend && \
  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_lived_day_chain_v04.py"
→ Ran 1 test in 34.1s ... OK      exit 0
```

### 8.2 明示

注入时钟推进（推到 owner 自己公布的到达时刻 / 停留截止），不是真实一天；决策是原始 JSON 回放，
不是 Gemini 自主；没有 24h 常驻。

### 8.3 这条链修掉的两个真问题

**(a) 正式生产 seam 根本不委派拿放。** `world_journey_action_adapter.DELEGATED_VERBS` 只有
`CONSUME/POSE/REST`，`JourneyWorldResolver.resolve_action` 对别的动词直接回
`UNSUPPORTED_VERB_<verb>`（definite pre-submit 拒绝）。也就是说：**真实 run 走的就是这条 resolver，
在修之前拿放永远不会到 owner**。修法：`DELEGATED_VERBS` 追加 B 自己的
`life_daily_action_targets.DAILY_ACTION_VERBS`（import 常量，不复制语义），原来的 idempotency key、
UNKNOWN 语义、reconcile 路径（走 `resolver.delegate.reconcile_execution`）一律不变。

**(b) 拿放 Activity 永远不会完成，因为证据绑不上。** 实测：`_record_daily_action_progress` 提交了
owner 自己的 placement fact，但 LR-5 回 `progress_state=UNKNOWN`、`completion_status=NOT_COMPLETE`
——LR-5 是把进度证据**绑定到 Activity 自己的 target_refs** 上的，而拿放 Activity 的 target refs 只有
机会本身的 `world_fact:dailyact:…`，没有 B 的完成事实 `world_fact:placement:<obj>:hand:<slot>`。
修法（`_daily_action_adoption_spec`）：把 B `daily_action_completion()` 给的 `expected_value` 也放进
这条 Activity 的 target refs。完成含义、判据、evaluator 一个字没改；修完同一条链上
`progress_state=COMPLETION_ELIGIBLE`、Activity 由 LR-5 判 `COMPLETED`，**不再需要 ABANDON 代劳**。

**(c) `has_foreground` 的边界回到"只有 WAITING 才让出前台"。** 前一版把它收成"只有 ACTIVE 才算忙"，
那会让 `PAUSED` 看起来空闲（PAUSE 仍占前台，resume 才是她唯一的合法动作）。现在是：
`WAITING` → 让出；`ACTIVE`/`PAUSED` → 占用；状态读不到 → 保持旧的指针规则。

### 8.4 同一轮相邻验证（只跑受影响的）

| 命令 | 结果 |
| --- | --- |
| `tests/test_lived_day_chain_v04.py` | `Ran 1 test ... OK`，exit 0 |
| `tests/test_lived_day_core_v04.py` | `Ran 26 tests ... OK`，exit 0 |
| `tests/test_day_life_body_actions.py` | `Ran 14 tests ... OK`，exit 0（该文件里"委派动词恰好是 CONSUME/POSE/REST"的旧断言已按 B 的常量更新为含拿放；其余 13 条未动） |
| `tests/test_world_daily_life_consume.py` | `Ran 6 tests ... OK`，exit 0（CONSUME 委派未退化） |

未归因（无负控，只登记）：`tests/test_day_life_continuity.py` 21 pass / 1 error（该文件自身 helper
`_world_cycle(snapshot=…)` 签名不匹配）；`tests/test_world_daily_life_continuous.py` 1 fail
（continuity binding 的 `continue_from_ref` != `owner_result`）。

## 9. 父级收口：正式工厂保留实例世界绑定

`autonomous_life_assembly.resolve_world_provider` 现在把当前实例的 `environment` 与
`_first(env, WORLD_HOME_ENV)` 传入真实 `WorldMoveOpportunityProvider`，其中
`WORLD_HOME_ENV=("CHIYO_WORLD_HOME", "HERMES_HOME")`。不再退回进程默认目录。
新增 `tests/test_world_provider_instance_binding.py` 两项通过（WSL，Python exit 0，AST PASS）：
通过正式工厂构造真实 provider，再核对目录 reader 收到指定非默认实例目录；覆盖显式 world home
优先及 profile home 回退。仅 transport readiness/catalog reader 为替身，不冒充 live World 观测。
证据：会话 scratch `lived-day-binding-check.json`、`lived-day-binding-tests.log`。
原实例 Gemini 实跑和目录核对的完整边界以 [父级结果](v04-lived-day-parent-result.md) 为准。
