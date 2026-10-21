# Agent Note: v0.4 阶段 A · 生活的"放下—缓过来—重新接上"以身体条件为唯一可核对依赖

Status: implemented

## Problem

[2026-10-28-v04-wait-closeout-core.md](2026-10-28-v04-wait-closeout-core.md) 让 WAIT 有了两种真实依赖（作品 head 变化、材料下一版），
但它们都只覆盖"对象会不会变"。真实生活里更常见的一句是**"我现在身体这样，先把这件事放下"**，
这句话当时无处分文：

1. **没有可核对的基础**。身体容量/恢复在 `life_body_context` 里每轮都是真读，可 WAIT 只认
   artifact head 依赖。于是"读到一半坐下歇会儿"只能退化成 PAUSED / NO_ACTION / ABANDON 之一，
   而 ABANDON 会丢位置、PAUSED 不清前台。
2. **最容易造假的那条路最顺手**："定个二十分钟，到点就算缓过来了"。时间不是身体测量，
   这样写等于让代码替 owner 宣布恢复；UNKNOWN 也会被当成"没信号=健康"。
3. **REST 会话完成容易被读成"她恢复了"**，而且 REST Activity 一旦无法完成就会一直占前台，
   把"歇一会儿"变成第二个卡死点。
4. 模型侧没有有界的断点描述，只能猜私有协议：要留下什么、等什么、怎么回来，全靠它自己想。

## Decision

**1. 身体基线的唯一来源是这一轮自己的身体读。** `life_wait_basis.body_capacity_basis`
（`components/life/scripts/life_wait_basis.py:407`）只接受 owner 自己报的
`capacity=LIMITED` + 至少一个具名负荷信号 + body 身份 + body revision + 观测时刻。
UNKNOWN / AVAILABLE / 无信号 / 无身份 / 无 revision / 无观测时刻**一律 `None`**：
"没信号不是健康"，也不是等待理由。基线随条件一起落盘，maintain 不记任何东西。

**2. 依赖拼在等待域自己的词表里，基线进 ref。** 条件是
`wait_dep:body.<body_id16>.<baseline16>`（`life_wait_basis.py:400`），baseline token 由
身份+版本+容量+负荷串+恢复档位+趋势算成（`:383`），所以"同一个人、上一版证据"的旧 ref
不可能满足"这一版证据"的新条件。`condition_kind = EXTERNAL_RESULT_EVENT`，
`parameters` 带全部基线（`body_ref` / `baseline_body_revision` / `baseline_burden` /
`baseline_progress` / `baseline_trend` / `baseline_observed_at`），**没有** `condition_expires_at`，
**没有** `schedule_agenda`；`release_foreground=True`（`:475`）——位置和页码留住，前台放掉。

**3. 只有一个东西能判"缓过来"：更新的一次身体读。** `body_easing_event`（`:546`）要求同一 body、
`FRESH`、revision 变了、观测时刻晚于基线，并且证据真的动了一格：恢复档位升高、具名负荷不再报或变轻、
或趋势转 `improving`。任一不满足就是 `None`，条件只能留在 PENDING。它产出的
`BODY_CAPACITY_EASED` 只是交给 LR-4 的事件，SATISFIED 与 OPEN 资格仍由 LR-4 判。

**4. 容量 token 本身不做判据。** 实测同一台 owner 在一次会话里会在
`LIMITED → UNKNOWN → LIMITED` 之间跳，留下的负荷还可能是另一个信号；拿容量当"好没好"的度量
既会漏掉真缓过来，也会把 UNKNOWN 读成健康。因此 `observed_capacity` 只作为事件属性被记录。

**5. upkeep 挂在既有 tick 上，一次读服务所有 pending 身体条件。**
`AutonomousLifeCycle.maintain_waiting` 的身体分支（`autonomous_life_cycle.py:2129-2160`）
通过 `_fresh_body_easing_read`（`:2057`）走这一轮同一个处境读，零模型、`auto_resumed=False`，
`body_checked` / `body_changed` 与 artifact-head 的 `checked` / `changed` 分开计数。
`AutonomousLifeRunner.maintain`（`autonomous_life_runner.py:412`）把这四个字段
（连同 `auto_resumed`、`llm_calls`）一起透传进 `runner_tick` 的 `report["waiting"]` 与
`wake.extra["waiting"]`，所以产品报告能把"身体复查"和"作品 head 复查"分开、并证明这次 upkeep
没花请求、没自动恢复。

**6. 断点是 prompt 里的有界事实，不是候选也不是许可。**
`body_breakpoint_view`（`autonomous_life_cycle.py:1933`）给出 `keeps`（同一 Activity / 同一材料 /
同一页）、`condition`（依赖+基线+`releases_foreground`）、`how`（回来的唯一方式是后来的 RESUME）、
`not_recovery`；没有断点时是 `available: false` + 具名 `why_not`
（`NO_RUNNING_ACTIVITY` / `BODY_NOT_READ` / `BODY_NOT_LIMITED` / `BODY_HAS_NO_NAMED_BURDEN`）。
它经 `decision_situation_context`（`:3357`）进处境，`agency_cognition_adapter.py`
（`:316`、`:1008-1018`）有界投影进真 prompt，裁剪最后一步（`:837` / `:898`）只留现场+身体+断点；
系统提示（`:127-140`）写明"frees the slot"、"RESUME she has to choose herself"、
"不是 claim that she recovered"。

**7. 资格不是意愿，会话不是恢复。** 条件 SATISFIED、资格 OPEN 之后，Activity 仍 `WAITING`、
仍在同一页；要推进只能靠一轮新鲜 Decision 自己选 `RESUME`，选 `NO_ACTION` 就什么都不发生。
REST start/stop 走正式 assembly/evaluate_once → AG1/AG2 → AR0 → World/Body → LR-5，
REST Activity 由 LR-5 完成、不占前台；提交成功只标 `body_observed` 与
`command_success_is_not_recovery`（`world_action_bridge_adapter.py:431-443`），
owner 自己的词是 `completed`，不是"恢复了"。

## Alternatives considered

- **用计时器/到期时间（"歇二十分钟就算好"）** — 最强理由：实现最省、模型天然懂、报告好读，
  也不需要新的依赖种类。否决：时间不是身体测量，这条路等于让代码替 owner 宣布恢复；
  自选时间已经由 park（`life_future_items.PARK_SOURCE`）承担，两者混在一起会让"她自己挑的时间"
  与"身体真的缓过来"无法区分。
- **直接用 capacity 字段（`LIMITED → AVAILABLE` 就满足）** — 最强理由：单一字段、语义直觉、
  测试好写，也最贴近"容量"这个名字。否决：真 owner 在一次会话里会在 `LIMITED`/`UNKNOWN` 间来回跳，
  容量变化既可能只是信号重算，也可能是 UNKNOWN 冒充健康；按容量判会同时产生漏判和误判。
- **新建生活调度器/后台线程按 tick 追问模型** — 最强理由：实现直白，"休息期间无变化也该问一句"
  听起来更主动。否决：本阶段明确沿用既有 quiet / feed / budget，不另设调度器；每 tick 追问还会
  把身体没变化这件事变成请求消耗。
- **满足即自动恢复或重放旧 Decision** — 最强理由：用户体验最顺，"条件一到就接着读"少一次犹豫。
  否决：Saved Decision 不是重放授权，自动续作等于替她做意愿，且会让"她要自己 RESUME"这条产品线
  彻底失真。
- **另加气力/心情等新身体变量做判据** — 最强理由：比容量+负荷更丰富，能表达更多状态。
  否决：不改 Body 六变量/阈值是本阶段红线，加变量等于把身体契约在生活侧偷偷改掉。

## Consequences

- **收益**：WAIT 第一次有了"身体"这一档可核对依赖——基线来自同一轮真读，满足只能来自更新的一次真读，
  未变/UNKNOWN/错身份读都不放资格；断点以同一份有界事实同时给模型和报告，模型不必猜私有协议；
  upkeep 零模型、`auto_resumed=False`，且这份证据现在能出现在产品 tick/wake 报告里；
  REST 会话完成不再冒充恢复，REST Activity 也不再占着前台。
- **代价与已知上限**：判断"缓过来"依赖 owner 的恢复档位/趋势/负荷词表，owner 侧改词表就要同步
  `PROGRESS_BAND_RANK`、`EASING_TRENDS`（`life_wait_basis.py:351-353`）；一处 body 读服务所有
  pending 条件，所以同一 tick 里多个身体条件共享一次观测（这是有意的，但意味着 tick 粒度就是证据粒度）；
  验收夹具用 `fatigue=0.60` 种负荷、可控 clock 推进 4×600s，**不代表**她自然累了或真的过了几小时。
  出现下列信号必须重访本决定：owner 恢复模型改成输出数值而非档位词；出现第二个"身体来源"
  （例如手机手动上报）；LR-4 增加新的条件 kind 语义。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -X utf8 -m unittest tests.test_v04_life_pacing_basis tests.test_v04_life_pacing_chain -v`
  → `14 + 4` 项全绿，exit 0（WSL Ubuntu，`/mnt/d/nyairo-rc1-body-backend`）。
- 整链（`tests/test_v04_life_pacing_chain.py`）：真 Supply 读到第 1 页 → WAIT 放前台保页码 →
  pose → REST start（LR-5 完成）→ 4 次真 owner tick（档位/趋势真的变了）→ maintain 只开一条资格 →
  REST stop → 新鲜 RESUME 同一 Activity 读到第 2 页；同级另有"未变不放资格""满足后仍要她自己
  RESUME（NO_ACTION 就什么都不做）""非 LIMITED 不提供断点"三项。
- 承载证据：`docs/engineering/v04-life-pacing-core-result.md`（命令、退出码、seed/clock 限制、
  父级夹具调用方法）。
- 已知红灯（不属于本决定）：`tests/test_life_wait_resume_v04.py::test_a_round_without_a_dependency_sends_no_basis_at_all`
  因 park 在"无依赖但有 ACTIVE 前台"时会发一个自选时间 basis 而失败，该代码路径（`autonomous_life_cycle.py:4952`）
  早于本节改动，未在本阶段修正。
