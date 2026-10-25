# Agent Note: 姿态负荷从固定 60 秒改为按已记账 elapsed 连续积分

Status: implemented

## Problem

`body_consequence.build_consequence` 对 `POSE`/`MOVE` 每次写死
`posture_seconds=60.0`（改前 317-322 行）。后果是：

1. 坐了三小时和刚坐下 60 秒在身体上是同一件事；
2. 同姿态下反复动作会重复记 60 秒，而两次动作之间的真实时长完全没入账；
3. `MOVE` 还会额外叠一段姿态负荷，和 `movement_duration_seconds` 的测量重叠。

`2026-10-21-v04-world-body-resident-and-rest.md` 当时明确拒绝加 substrate `pose_since`：
不改 substrate schema、也不拿 `last_advanced_at` 猜"姿态从哪一刻开始"。这条限制仍然成立，
本次**没有**推翻它，也没有新增任何持久字段或迁移。

## Decision

只替换固定 60 秒，六变量、系数、阈值、恢复、movement 测量、权限/预算/时钟语义全部不动。

1. **区间来源**：`apply_for_execution` 用 Body 已持久化的
   `runtime_state.last_advanced_at` 与本结算时刻 `at` 相减，得到"身体上一次已记账时刻到现在的
   elapsed"，传进 `build_consequence(elapsed_seconds=...)`。这和 `apply_consequence` 推进的
   `from_time` 是同一个游标：一个区间只归一个 owner，不引入第二个时钟。
2. **归属姿态**：区间归到结算动作的 `before.pose`（World 自己的测量模型）。`POSE` 改变姿态发生
   在 `at`，所以刚结束的区间属于旧姿态；新姿态从 `at` 起，由下一次结算归账。不是"猜测姿态起点"，
   而是按每个已记账区间的末尾状态归属。
3. **首次/旧档未知不回填**：`last_advanced_at` 缺失、不可解析或在未来时 elapsed 为 `None/0`，
   姿态负荷为 0，只建立游标。首次 POSE 仍会写一条 `posture_seconds=0` 的物理输入，让游标落盘。
4. **不再固定 60**：`POSE` 的区间输入从 60 改成 elapsed；`PICK_UP/PLACE/CONSUME` 的既有微小操作
   负荷项带上同一段 elapsed 姿态；`MOVE` 记 movement（测得的或路线估计）并只把
   `elapsed - movement` 的静态余量记姿态，既不让出发前久坐整段丢失，也不把走路秒数计两次；
   出发仍然是 pending，不预扣未走完的路。
4b. **每一次游标推进都要结算同一区间**：环境灯 tick 也会推进 `last_advanced_at`。现在
   `advance_environment(posture=...)` 用 World substrate 的实测姿态（owner 唯一姿态来源，不是猜的历史）
   结算 `[last_advanced_at, now]`；`WorldBodyService._settle_environment` 传入该姿态。未知 gap
   （>15 分钟无进程）不记姿态；REST active 期间不记姿态（恢复区间归 REST，被动姿态不得被读成打断
   恢复的物理负荷）。纯 idle tick（无环境、无 REST）不推进游标，负荷留给下一次真实结算。
4c. **MOVE 在途时环境采样照常，姿态区间在出发时刻截断**：`_settle_environment` 不再因在途 MOVE
   让位；它把 `pending_movements` 里既有 pending 记录的 `applied_at`（出发时刻，manifest 事实，
   不靠动作名猜）作为 `movement_in_flight_since` 传给 `advance_environment`。灯光输入按原 anchor
   正常落账（跨 15 分钟 gap 的扣留语义不变）；姿态只记 `[last_advanced_at, departure]` 的出发前静态，
   游标前移到 now。到达结算看到的 elapsed 已不含走路段，movement 按测量/路线秒数记一次，静态余量为 0，
   既不重复也不丢出发前久坐。同一段在途的后续 tick 因游标已在出发之后而记 0 姿态。
   账单/在途记录不可读时返回 `movement_state_unreadable`，不写也不主张。
4d. **UNKNOWN 的 POSE/MOVE 不再把整段推给结束姿态**：这两类结果在 `apply_for_execution` 里仍进
   `pending_body_consequence`（不应用、不早算），但同时做一次**无输入**的游标推进（与未知环境 gap
   同一既有写法），把"姿态是否在未知时刻改变不清楚"的窗口就地关闭；pending 记录带
   `posture_interval_unknown:no_attribution`。下一次成功结算只计未知时刻之后的区间，并按那时的
   World 实测 `before.pose` 归属；事后 reconcile 成 SUCCESS 时因该 note 以 `elapsed_seconds=0`
   重建，永远不补记未知窗口。首次/旧档无 anchor 或 anchor 不可读/在未来时不做这次推进。
   其他动作（`PICK_UP/PLACE/CONSUME/REST`）的 UNKNOWN 不改姿态归属，保持既有 pending 语义。
5. **防重复**：沿用既有 `consequence_id(execution_id, action, body_id)` 与
   `apply_for_execution` 的 duplicate 判定；游标随 runtime 一起落盘，重启后同一 execution 重放返回
   `duplicate_execution_id`，下一段从持久游标起算，不双记。
6. **纯 GET 零写**：姿态积分只在合法 writer 的结算接缝发生（`apply_for_execution`），读口不变；
   `body_summary()` 仍只出定性信号，`raw_values_exposed=False`。

证据：`tests/test_body_posture_elapsed.py`（17 例：含 1 例真 `WorldBodyService`+真 substrate 姿态+真灯具
的环境 tick 负控，以及 UNKNOWN POSE 边界、UNKNOWN MOVE 边界、MOVE 在途环境 tick 三组新例）、`scripts/body_posture_elapsed_smoke.py`（真实 `init_instance` + 真实
`WorldBodyService`/Resolver，0→2h→3h→11h→15h 缩放业务钟，依次得到 0 / 7200s seated /
3600s standing / 28800s seated / 14400s standing；中间 3 次合法 idle tick 全部返回 None 且游标不变，
证明 tick 不吞区间也不重复；重复只读前后 17 个文件逐字节不变，lease 自己释放、无 gateway socket）。
同一 smoke 的定性信号始终为空：真实 schema 是 `body.signal.v1` 的 `signals[].signal_type/
qualitative_band`，解析无误；smoke 终值 comfort≈0.694、tension≈0.154、latch 全 false，是既有阈值
未触发，不是解析失败，也不改阈值凑档位。

## Alternatives considered

- **给 substrate 加 `pose_since` 并迁移**：能精确知道姿态起点，但正是旧 note 拒绝的第二真相与旧档迁移写；
  本次没有授权改落盘 schema，且现有 `before.pose` 已能按区间归属，不需要它。
- **用 `last_advanced_at` 当姿态起点**：旧 note 已否决，本次也不这么做。elapsed 只说明"多久没结算"，
  姿态归属来自 World 的 `before.pose`；两者不是同一个假设。
- **把姿态负荷平均摊到区间内部（积分进 advance）**：更接近连续物理，但会改 `advance` 的负荷/衰减
  数学，超出"只替换固定 60 秒"。拆段与一次等长 advance 的姿态 strain/load 相同，终值差异只来自既有
  "负荷在段末入账"的衰减先后，不作为新语义引入。
- **MOVE 到达也记姿态**：与 `measured_travel` 的 movement 分钟重叠，同一段走路被计两次，否决。

## Consequences

- 收益：不同 elapsed 不再都记 60 秒；姿态切换前后各归各的区间；零 elapsed 无负荷；拆段与一次等长
  积分的姿态量一致；重启按持久游标续算不双记；旧档缺 anchor 不编历史；REST 的恢复门与中断规则未动。
- 代价/已知上限：UNKNOWN 的 POSE/MOVE 窗口不记任何姿态（宁缺不猜），事后再成功也不补；该窗口之后
  的区间按下一次结算时 World 的实测 `before.pose` 归属，如果 World 把未知姿态拖到更晚才落账，
  边界到落账之间的归属仍取决于那一刻的实测姿态，不做第二真相。进程停机超过环境 gap 上限的区间
  不记姿态（沿用既有 gap 语义，不事后补量）。MOVE 在途期间环境采样照常，但灯光输入与姿态使用
  同一次 tick 的 `now`/出发截断，tick 密度决定采样粒度。极长区间受
  `posture_seconds<=86400` 与 `MAX_HOURS_PER_ADVANCE` 既有上限约束；
- 格式：canonical `body.runtime.v1` schema 未变、无迁移、无新增 sidecar；consequence 记录也没有新增
  字段，姿态证据只来自既有 `PHYSICAL_RESULT.posture_seconds/pose` 与 notes，环境结算的
  `posture_interval_seconds/pose` 放在既有开放 `transition.input_summary` 诊断字典里；旧的
  `2026-10-21-v04-world-body-resident-and-rest.md` 中"姿势时长仍是 60s 假设"已被本 note 取代。
- 未验证：真实产品模型在自然一日中如何选择姿态、长时间真实运行下的档位演化、24h/压缩日均未跑；
  本单 0 产品模型调用，`tests/test_body_posture_elapsed.py` 的 clock 只验机制，不代表真实生活。
