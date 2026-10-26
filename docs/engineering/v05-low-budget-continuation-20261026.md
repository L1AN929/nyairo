# v0.5 EP17：低预算诚实接续——既有预算门与停点的正式整链

2026-10-26。范围是**验证既有预算门在低额度下怎样拒绝与接续**，不是新增降级策略。
严格反例只出现一处：Decision 请求真的发出并已采纳、随后内容在自己的预留处被拒的那一轮，
wake 记成 `llm_calls=0`，与既有"按模型决策轮计数"的口径不一致（那一轮确实由模型服务）。
修复只落在 `autonomous_life_cycle` 的预算拒绝出口：按"本轮是否已物化 Decision-bound
步骤"映射到 0/1，其它仍由既有 `life_request_budget`、`agency_cognition_budget` 与
runner/resident 接线表达。本单据此新增测试、冒烟与这份事实文档；没有新增预算规则、
对外字段、重试策略或公开 clock 形参。

零产品模型、零外网、零旧实例、零外发；四个隔离根各自从创建时就用合法的小额度配置，
之后不清账、不加额、不换 run、不直改 ledger。Decision 与内容回答是脚本替身（明示），
其余是真实 Supply owner、M0 Memory、ProductionLifeRuntime、AG-0/AG-1/AG-2/AR-0 与正式
runner。

## 两层预算，各管什么

| 层 | 代码 | 管什么 | 本次实测 |
| --- | --- | --- | --- |
| 请求账本 | `life_request_budget.LifeRequestBudget` | 每次 HTTP 尝试前 `reserve(kind)`；真实 UTC 日 + 持久 run 窗口双上限；无退款 API；坏账 fail-closed | 认知/内容各一次预留；日或窗口用尽前拒绝；重开不续杯 |
| 回合账本 | `autonomous_life_cycle.RoundBudget` | 回合/认知/动作计数；接上请求账本后 `cognition_used_today` 直接读 `utc_day_calls`；坏账按上限处理（剩余 0，不是 0 次免费） | 剩余 0 时 `may_start_wake()` 返回 `(False, "COGNITION_BUDGET_EXHAUSTED")` |
| 实验上限 | `demo_business_clock.CappedRequestBudget + ExperimentRequestCap` | 每次预留前再查一次实验硬上限；高水位持久；重开 `load()` 校验 | 用尽 2/2 后同根重开仍是 2/2、`allowed=false` |

`LifeRequestBudget._active_day` 取 `max(stored, today)`：真实 UTC 日滚动会把**当日**计数
换到新的一天，但 `windows[run_id].calls` 保留；`ensure_window(allow_new=False)` 对陌生
run 身份直接拒绝，所以普通重开不会铸造新的 24 小时额度。

`agency_cognition_budget.CognitionBudget`（小时/日调用与 token 上限）是**另一层**：
本链的 `model_cognition` 没有给它传 budget，所以本单的拒绝不是它在起作用，也不把
它的状态当作请求账本；两者没有互相冒充。

## 四个隔离根与结果

全部经正式 runner 的 check-window（`run_due`）进入 cycle；内容请求走
`BudgetedModelClient`，认知请求走 `BudgetedCognitionAdapter`，预留都发生在发送之前。

**a（日 2 / 窗口 2 / 认知 2 / 实验上限 2）**

- 低但足够：Decision + 内容各一次预留，owner 写出一版，`ACTED`；`Activity` 仍是
  `ACTIVE`，`ACTED` 只是"走了一步"，没有被报成整件完成。
- 剩余 0：下一窗口 `BLOCKED / blocked / outcome=None / llm_calls=0`，detail 具名
  `COGNITION_BUDGET_EXHAUSTED`；cognition 与 content 调用数都不变，账本、版本、Activity、
  receipt、Memory 证据行数都不变。它不是 `QUIET`、`NO_ACTION` 或 `ACTED`。
- 报告面：`cycle.budget.snapshot()["requests"]` 能看到 `state=ok`、
  `utc_day_calls=2/2`、`run_window_calls=2/2` 与 `refund_policy`；拒绝原因在 wake detail。
- 同根同进程重装配：账本、run 窗口、Activity（revision/前台/状态/停点）、作品 head/版本、
  Memory 行数全部保持；实验上限仍 2/2；新 run 身份 `allow_new=False` 被
  `REQUEST_BUDGET_RUN_WINDOW_NOT_AUTHORIZED` 拒；带 24h 业务时钟的重开依然被同一个预算门
  挡住（业务时钟不刷新真实 UTC 日）。
- 最后才注入坏账（`{not json`）：`view()` 报 `state=corrupt`、`utc_day_calls=None`，
  `cognition_used_today()` 按上限返回、剩余 0；窗口 `BLOCKED` 且零请求、状态不变。

**b（日 5 / 窗口 5 / 认知 5）**

- `NO_ACTION` 只用一次 decision 预留（tokens 为 `decision, content, decision`），
  **不发**内容请求，版本不变，剩余额度仍是 2——低预算不强迫把余量花掉。
- 同一状态在到期窗口（+121 分钟，仍在既有 6 小时静默 TTL 内）得到 `QUIET / silence /
  llm_calls=0`，零请求、零写入。

**c（日 3 / 窗口 6 / 认知 3）**

- 剩余 1：Decision 真的发出并通过 AG-1/AG-2（决策库新增一条 `dec:`，集成日志新增一条
  `LIFE_INTEGRATION_ADOPTED` 指向它）；随后内容请求在自己的预留处被
  `REQUEST_BUDGET_UTC_DAY_LIMIT` 拒。
- 真实阶段：Decision 已提交、已采纳、**没有** Action 行、没有新版本、没有 receipt、
  `pending_unknown_actions=[]`。账本停 3/3，已用请求不退。
- 下一窗口 `BLOCKED`（预算）且不自动重放：决策调用数、内容调用数、账本与版本都不变。
- 报告面：wake 是 `BLOCKED / BUDGET_EXHAUSTED`，detail 具名拒绝码；同一 wake 的
  `extra.budget.requests.utc_day_calls=3` 与账本 token 说明那一次 Decision 确实付费。
  这使"没发"（runner 门，`outcome=None`）与"发后内容被拒"（cycle 的
  `BUDGET_EXHAUSTED`）在既有字段上可区分。
- **计数修正**：该轮 `llm_calls` 精确为 `1`，不是 0/2/3。口径来自既有
  [按 route 计数的修复](../../.agents/notes/implemented/bug-fix/2026-10-23-observation-count-rule-noise.md)——
  `wake.llm_calls` 是**模型决策轮/尝试**，不是 HTTP 次数、不是当日累计。Decision 请求
  真的发出（planner 调用 +1、账本多一枚 `decision` token）且已采纳；内容请求 0 次发送。
  0 会把已付费的决策抹掉，2 会把被拒绝的内容混进认知数，3 会把当日累计当本轮。
  原实现里这条异常落进"预算拒绝 = 零请求窗口"的映射，闸门是对的，但计数说错了。
- **断步后续**：只在测试内替换账本自己的 `_utc_day` 读口模拟下一天（`mock.patch` 退出
  即恢复），日计数归 0、`run_window_calls` 仍 3/6。随后新 Decision 依法 CONTINUE 同一
  Activity / 同一作品：owner 恰好新增一版、恰好新增一个 Action（与首轮 Action 不同），
  旧已采纳 Decision 只出现一次、没有重放，旧成功 Action 也没被重跑。窗口累计 3+2=5/6，
  当日 2，run 身份不变，没有加额、清账或换 run。

**d（日 2 / 窗口 6 / 认知 2）**

- 日额度用尽后窗口拒绝（窗口还有 4 次余量）。
- 模拟日界：账本**没有公开 clock 形参**，`reserve(now=...)` 只写 token 时间戳；本次只在
  测试内替换账本自己的 `_utc_day` 读口。日计数归 0、`run_window_calls` 仍 2。
- 新 Decision 依法 CONTINUE 同一 Activity / 同一作品：owner 恰好新增一版（版本 +1、
  head 变化、正文含新写的句子、Action 与首轮不同），旧成功没有重放；窗口累计 4/6，
  总额度未增加。
- 注入结束后真实 UTC 读口恢复；账本按单调规则仍停在模拟的那一天（`2` 次），
  不会因为时钟回退而重置。

## 可操作结果

```bash
# A：一个 case，内部跑四个隔离根（40 项 check）
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    -m unittest -q tests.test_v05_low_budget_continuation
# → Ran 1 test in 19.041s / OK / exit 0

# B：同一冒烟直接运行
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    scripts/v05_low_budget_continuation_smoke.py
# → checks: 40, failed: 0 / RESULT: PASS / exit 0
```

夹具根保留在 `/tmp/nyairo-result-method-fixtures/*-v05-low-budget-{a,b,c,d}`；自有 Supply
owner 全部按 `shutdown → server_close → drain → prepare_socket_path → service.close` 停掉，
没有裸删 socket。

## 边界（未验证、不宣称）

- 不是真实模型：Decision 与内容都是脚本；不宣称自然采用、理解、偏好或"她自愿休息"，
  也不称模型主动向用户交代了什么——只有既有 runtime 报告里的记录。模型没收到请求时，
  报告只说预算拒绝，不说她选择了安静。
- 不是 resident 循环或 24h；不碰产品 300 次共同额度（夹具账本独立，产品 94/300 不变）。
- "重装"是同进程内关闭 runtime/assembly 后在**同一隔离根**重新装配，不是跨 PID；
  没有 crash/kill 矩阵。
- 日界只在测试内替换账本自己的 `_utc_day` 读口（`mock.patch` 退出即恢复），不是系统
  时钟跨日，也不是业务时钟快进，更没有新增公开 clock 形参；真实自然跨日仍需另一次
  有界运行。
- fixture c 的断步后续与 fixture d 的日界续写是**同一 harness 的两次不同运行**（不同
  上限、不同起点失败：c 先是内容被拒，d 先是窗口整体拒绝），不是两套独立策略，也都
  不证明产品里存在某种低预算降级路线。
- 没有新增 soft threshold、自动 PAUSE、低预算强制小作品、提醒话术或自动重试；"被付费
  却内容被拒"之后，本单只证明原 run 还有窗口余额时，新 Decision 可以依法 CONTINUE
  同一 Activity/作品，且旧已采纳 Decision 与旧成功 Action 都不自动重放。
- Memory 只核证据行数不增，不是准入/治理/删除测试；Activity/作品读取均为只读快照。
- 未新增对外字段或报告格式。`llm_calls` 仍按既有"模型决策轮/尝试"口径：零发送/规则空
  候选/QUIET 为 0，"已发认知 + 内容被拒"为 1；真实请求数仍看 `extra.budget.requests`
  与账本 token。若将来需要轮级"哪一次预留对应哪一步"的单一字段，属于新报告契约，
  需另行批准。
