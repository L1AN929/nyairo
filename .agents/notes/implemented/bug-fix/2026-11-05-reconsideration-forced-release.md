# Agent Note: reconsideration 门禁连续拦截后强制放行一轮（不新增持久化字段）

Status: implemented

## Problem

压缩日里她不动、世界状态也不动，于是生活侧的 reconsideration 门禁把一整天的请求全吃掉。
链条是这样的：候选 id 是内容寻址的（`wopp:<verb>:<place>:r<world_revision>:<suffix>`），
世界 revision 不变就没有 `is_new=true` 的候选可物化；而门禁的判据是**整段情形的指纹**
（`life_step_readiness.situation_fingerprint`，只取地点/姿势/revision/机会集合等 owner 事实，
刻意不含时间），同一个情形被模型答过一次 `NO_ACTION` 之后，之后每一轮都命中
`SAME_STATE_ALREADY_CONSIDERED` 在发请求之前返回 `OUTCOME_QUIET`。能打破这个循环的只有两件事：
世界自己变（她不动就不会变），或者门禁的 21600 秒 ttl 自然到期。实测 2026-11-05 的 w 批：
47 个 check window 里 42 个在发请求之前被拦，一整天只花掉 11 个请求（额度 20），一整天按 ttl
只自然放行 3 次，那 3 次模型都选了 NO_ACTION。

结论：门禁省下来的那 9 个请求，恰好就是她本来可以用来改变状态的请求。这不是候选生成的问题，
也不是预算问题——预算明明还有余量，是门禁在没有新事实的情况下不许问。

## Decision

1. **连续拦截有上界，上界按次数而不是只按时间**。新增模块级常量
   `MAX_CONSECUTIVE_SAME_STATE_BLOCKS = 3`：同一个指纹连续被门禁拦到 3 次之后，下一轮**强制放行**，
   让她真的被问一次。
2. **计数复用台账已有的 `count` 字段，不新增字段、不新增文件**
   （`life_reconsideration.json` 的 `situations[].count` 本来就是「这条情形被记下过几次」）。
   门禁每一次「省掉」都写回同一行（`record_situation`，outcome 保持该行原本的裁决词，
   不是伪造一次新的模型裁决），于是 `count = 1（那次真的问过的 NO_ACTION）+ 连续被拦次数`，
   判定用的连续次数是 `count - 1`。
3. **放行＝把这一行撤掉，计数从 1 重新开始**。用台账既有的 `release(key=…)`，不写 0、不改行结构。
   放行只买一次机会：下一轮若模型仍答 NO_ACTION，记录重新从 `count=1` 起算，还要再连续被拦 3 次
   才会再次放行。放行不会留下永久免疫。
4. **放行原因是具名的，和 ttl 到期分得开**。放行时 `reason_code =
   CONSECUTIVE_SAME_STATE_BLOCKS_FORCED_RELEASE`（常量 `REASON_FORCED_RELEASE`），
   原来的 `SAME_STATE_ALREADY_CONSIDERED` 存进 `suppressed_reason_code` 保留可审计；
   ttl 自然到期仍是 `STATE_NOT_CONSIDERED_YET`，两者永不同名。回合报告和 wake record 的
   `reconsideration` 块里带 `forced_release` / `consecutive_blocks`，一眼能看出这是
   「连续拦截触发」而不是「自然过期」。
5. **判定收在一个纯函数里**：`autonomous_life_cycle.reconsideration_gate(ledger, parts, at)`
   包住 `ledger.block_situation`，`reconsideration_block` 只调它。不碰预算、暂停门禁、额度上限、
   候选生成和任何 owner：被放行的这一轮按普通回合付费。
6. 写集只有三处：`autonomous_life_cycle.py`、新测试
   `tests/test_reconsideration_forced_release.py`、本笔记。台账模块
   `life_step_readiness.py` 一行没动。

## Alternatives considered

* **计数只放内存（cycle 上的 `dict[fingerprint] → streak`）**。最强立论：零持久化、零台账写入、
  完全不碰 ttl 语义，代码最短，也不会因为写盘失败而影响回合。否决原因：resident 进程一重启
  计数就归零，死锁在重启之后原地复活，而且没有任何痕迹能说明「这里刚被放过一次」；
  台账里现成就有 `count` 这个字段和 `release` 这个动作，安全网不该比进程活得短。
* **在 `life_reconsideration.json` 里加 `blocked_streak` 字段或一个顶层 `streaks` 表**。
  最强立论：字段名自己就把话说清了，不用对 `count` 做加减，读代码的人不必推理 `-1`。
  否决原因：这就是新增持久化 schema/新字段（工单的停止条件之一），而且写入路径在
  `life_step_readiness.py`（写集之外）。既有 `count` 加既有 `release` 已经够用，没必要扩存储。
* **只把 ttl 调短，或让它变成按轮数到期**。最强立论：一个常量就完事，没有新逻辑，也不用管计数。
  否决原因：问题不在时间轴。短 ttl 只是把「6 小时放行一次」变成「1 小时放行一次」，没有表达
  「被拦 N 次之后必须问一次」这件事；压缩日里 47 个窗口挤在 24 逻辑小时，按时间放行仍然会把
  大部分窗口挡掉，而这正是本单要修的现象。
* **干脆去掉门禁（每轮都问）**。最强立论：最简单，永远不会冻住，也不用维护任何计数。
  否决原因：门禁存在的理由就是「同一状态不要反复花钱问」，去掉它等于把重复请求全放回来，
  20 个请求的额度会在几十分钟里烧完；工单也明确不许动额度与预算。

## Consequences

* 死锁被限定在「最多连续 N=3 轮安静」之内；一整天 42 次被拦的情形，现在每 3 次就会真的问一次。
* 每次放行恰好花掉一个请求，并且和其他回合一样走 `_finish` 计费；不绕过 request budget、
  不跳过暂停门禁、不改任何额度默认值。
* 计数落在文件里，resident 重启不丢；ttl 自然到期那条路仍然独立存在，且具名可辨。
* 已知代价一：被拦的那几轮现在会写一次台账（小 JSON 重写），并把该行 `last_at` 刷新，
  因此 ttl 自然到期在「一直在被拦」的状态下不再触发——但连续次数上界严格比 ttl 更早放行，
  所以不会出现比以前更长的沉默。
* 已知代价二：连续次数由 `count - 1` 推出，如果同一情形在 ttl 到期后被重新问过、又答了
  NO_ACTION，`count` 会多加 1，推导出的连续次数略微偏大，效果是**更早**放行一次，
  偏差方向只会让她更常被问，不会让她更久不被问。
* 未验证：没有跑真实 24 小时批次，也没有验证真实模型在被放行的那一轮里真的会动；
  本单只证明门禁不再无限期沉默，以及这条逻辑接在回合门禁上（`tests/test_reconsideration_forced_release.py`）。
