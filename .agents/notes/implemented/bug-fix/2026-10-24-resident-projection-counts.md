# Agent Note: resident wake/arrival 按写入与段身份计数，不按读面投影累加

Status: implemented

## Problem

fastday-e 结束后，`resident_summary.counters` 写着 `wakes=327`、`journey_arrivals=292`，
但同一实例的 `autonomous_wakes.jsonl` 只有 17 条记录，真实旅程只有 3 段已确认。差额来自
读面重复投影：

- `runner_tick` 每个 tick 都返回一条 `due` 投影。检查窗没到时是 `NOT_DUE`，但 runner 的
  wake journal 只在连续静默的第一条写盘；resident 的 `_count` 却每条投影都 `wakes += 1`。
- `settle_journey` 每个 tick 都重读已完成的旅程投影。`arrived` 是布尔值，完成之后每次读
  都是 `true`，于是同一个到达被计成每个 tick 一次。

这两个数字会进 summary、`fast_day.py` 与行为统计脚本。它们不是生活量，也不能反过来驱动
唤醒、预算或动作。

## Decision

- 新增 `components/life/scripts/resident_projection_counts.py`，把口径放在 resident 之外，
  实时 tick 与 `_replay_journal` 共用：
  - **wake**：只计 runner 真写进 `autonomous_wakes.jsonl` 的记录。非静默记录全写；连续
    `NOT_DUE`/`QUIET` 只写第一条。合成 tick dict（例如邀请已用掉本 tick 决策时的
    `CONTACT_DECISION_HANDLED`）没有走 `_record`，不算 wake。没有按 `at` 去重，所以同一
    时间戳的不同事件仍各计一次。
  - **arrival**：按 owner 旅程记录的 `journey_id` + 单调 `segments_confirmed` 增量计数。
    完成投影读一百遍增量是 0；同一旅程第二段增量是 1；另一旅程各计各的。缺
    `journey_id`、`segments_confirmed` 非整数/为负或记录 `ok=False` 时不计数。
  - 内存里只保留最近 32 个旅程、64 条拒绝键做**加速缓存，不是权威**。缓存未命中时
    `ProjectionCounter` 通过 `history(journey_id, before_tick)` 回读既有 resident
    journal 的**先前行**，取该旅程历史最大 `segments_confirmed` 与已记过的拒绝键。
    40 个不同旅程各确认一次后重读最早的 `journey:0`，额外到达为 0；同根重放同样为 0。
  - 边界是当前 journal 行的 `tick`：只读 `tick < before_tick` 的行。tick 先落盘、
    后 `_accumulate`，所以当前行不会被提前当成过去事件，重放也不会扫描未来行。
    乱序 `segments_confirmed` 只抬高水位、不降低；拒绝键同样按持久证据恢复。
  - 持久源明确不可读（`history` 返回 `None`）时按未知处理、不计数，不拿 0 冒充
    “没来过”；`FileNotFoundError` 视为“此前没有行”，不是读失败。没有 `history`
    的纯 helper 仍按有界缓存工作，不承诺无限历史精确。
- `AutonomousLifeRunner._journal` 改为返回是否真的写盘成功；写失败时在内存记录
  `extra["journal_write_failed"]`（wake journal 没有这一行；resident journal 的
  due 投影会带上该标记，重放同样不计，wake journal 本身格式不变），
  `wake_is_recorded` 不把它算成一条 wake。`model_calls` 仍按真实请求累计。
- `AutonomousLifeResident._count` 先累计 `llm_calls`（真实请求不因跳过一条 wake 丢失），
  再按写盘口径决定是否计 wake 及其分类/结果/动作。`_accumulate` 先处理 `record.journey`
  的段增量，再按 runner 的真实顺序处理 events、最后 due。
- `settle_journey` 仍每个 tick 递增 `journey_settlements`（真实循环次数），但不再增加
  `journey_arrivals`/`journey_arrival_refusals`；两者统一由 `_accumulate` 计数，避免实时
  与重放双计。
- 未改 `runner`、tick 调度、`next_wake`、唤醒源、预算、动作提交、到达结算或候选；计数
  不读 owner、不写 World。`scripts/wake_behavior_stats.py` 无需改：旧 e 的 327/292 与
  17/3 仍会触发 `READ_SURFACE_WAKE_COUNT` / `ARRIVAL_PROJECTION_REPEATED` 警告，那是历史
  实例的真实记录，不倒写。

## Alternatives considered

- **在 `_count` 里直接按 `status != NOT_DUE/QUIET` 过滤**：最简单，但会漏掉每一段连续
  静默真正写进 journal 的第一条，计数与 wake 记录对不上。否决。
- **给 `WakeRecord` 加 `wake_id`/`journaled` 字段**：确定性更强，但会改 wake journal 的
  落盘格式并影响旧读者；状态机已能按既有写盘语义重建。否决。
- **只按 `journey_id` 计一次**：会把同一旅程的两段吞成一次，与“段到达”不符。否决。
- **只把 32/64 调大或改用无界字典**：更大窗口只是把反例推迟，仍会在长 journal 上重计；
  无界字典则是内存里复制一份 journal 索引，重启后仍无权威。否决。
- **只在进程内 set 去重**：重启后无法解释同一完成投影，且会与 journal 重放冲突；改为
  内存状态由既有 journal 重建。否决。
- **用 `arrived` 布尔值在 cycle 里改成只报一次**：那是结算语义，不是计数问题；本轮只改
  report/count 路径。否决。

## Consequences

- e 的旧 summary 保留 327/292 作为历史读面；由修正后代码写出的实例，`counters.wakes`
  与 `autonomous_wakes.jsonl` 条数一致，`journey_arrivals` 与 owner 已确认段数一致。
- 新增 `tests/test_resident_projection_counts.py`（22 例）：重复 100 次完成/静默投影、
  同旅程两段+另一旅程、坏身份、同 timestamp 不同事件、重载/重启、旧口径与新口径的
  tick 调用轨迹逐步相同；以及 journal 恢复组：40 个旅程后重读被淘汰的 `journey:0`、
  同根重放的淘汰恢复、乱序 confirmed 不降水位的、70 条拒绝键淘汰后重读、runner 写盘
  失败不虚增 wake（含连续静默首条写失败）、合成 `CONTACT_DECISION_HANDLED` 不计。
- 新增 `scripts/resident_projection_counts_smoke.py`：真实 `world_journey` 两段旅程 +
  真实 `AutonomousLifeRunner._record` 的 17 条 wake 记录 + 327 tick 重复投影，验证
  summary 与权威记录对应、同根重启不重计；另加 40 个旅程的 journal 淘汰恢复，
  实时/重读/重放均为 40。夹具是确定性零模型场景，不是自然生活。
- 性能代价：`history` 只在缓存未命中时触发，每次读一遍当前 journal 并按 tick 过滤；
  稳态下命中缓存不读盘。大量不同旅程首次出现或反复淘汰时，代价是 O(行数)/次，
  不是每 tick 固定开销，也不保证在超大 journal 上无感。没有持久源的纯 helper 不做
  这项恢复，因此不承诺无限历史精确。
- 与 [[2026-10-23-observation-count-rule-noise]] 一致：模型轮次仍认 Decision route、
  请求数仍认持久 ledger；本修只处理读面重复。与 [[2026-10-24-wake-candidate-cost]] 的
  回合缓存互不重叠，没有改候选读取。
