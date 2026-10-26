# v0.5 EP-12「稍后」到点重新考虑：正式 runner 串联与改期时钟修复

范围：只复用已定义的 `later_item` / `future_item_actions`、park WAIT、LR-4 到期资格与
RESUME，以及既有的检查窗/处境指纹。没有新增候选种类、调度器、日历、默认计时器或合同。

## 改了什么

- `components/life/scripts/life_future_items.py`：**改期的相对延迟按本回合的 `observed_at`
  解析**，不再在校验时用真实墙钟解析。`validate_actions` 回显 `in_minutes` 与解析出的
  `due_at`（重放同一回合同形状），`apply_actions(now=...)` 用回合时钟重算。与 `later_item`
  提案的同一条语义对齐；越界/未知字段/取消带 due 时间的拒绝规则不变。
- 新增 `tests/test_v05_due_reconsideration.py`（5 例）与
  `scripts/v05_due_reconsideration_smoke.py`（44 项 check）。

## 真实跑到了什么

`scripts/v05_due_reconsideration_smoke.py`：真实 Supply owner、ProductionLifeRuntime（lease）、
真实 AG-1/AG-2/LR-2/LR-4/AR-0 与 `FutureItems` 持久存储；每个窗口都走正式
`AutonomousLifeRunner.run_due`（注入业务时钟与 1 逻辑分钟检查窗）。Decision 与内容回答是脚本
替身，0 产品模型。

- 到期前一个窗口 `QUIET`（0 请求、0 内容写）；到点窗口里带作品对象的到期项进入材料、处境指纹
  与正式 `FUTURE_ITEM_DUE` 候选；同一到期集合的下一窗口再次 `QUIET`，不重复事项/不每窗自激。
- 无对象到期项不产生候选，只出现在既有项目选项里；选中后由真实 owner 创建新作品并跑起
  「别事」Activity，事项本身没有被自动执行。
- 真实 WAIT（`TIME_AT_OR_AFTER` + 释放前台 + owner 排的 agenda）到点后由 LR-4 自己发 OPEN；
  「别事」占前台时没有 RESUME 候选、资格不被消费，`NO_ACTION` 也不消费；把「别事」也放下后，
  新 Decision 显式 RESUME 同一个 Activity，资格恰好消费一次。
- 取消/改期后下一窗口移除/延后，不复活；关闭 runtime 释放 lease 后同根重装配，事项与已消费资格
  原样读回，不丢不重放。

`tests/test_v05_due_reconsideration.py` 走同一真 owner 最小链（8 个窗口）覆盖到期前安静、到点
进入指纹/候选、同一集合安静、改期按回合时钟延后；纯域用例覆盖改期幂等/越界/未知字段，以及坏存储
如实 `READ_FAILED` 且候选读取失败关闭。

## 没有什么

- 没有真实产品模型调用、没有新真实短日、没有外发、没有改 Memory 门或默认 gate/预算。
- 不证明模型会自然提出事项或自然采用；G1 / V-C / 理解 / 24h 边界不变。
- 检查窗仍是既有的有界间隔，不是新的后台计时器；到期项本身不直接唤醒。

## 读到的边界（本次未施工、未裁定）

- `times_considered` 只有存储上的 `note_considered` 会写，日循环不调用：它是报告字段，不是
  「考虑次数」配额；静默由处境指纹负责。
- `future_item_actions` 只有取消/改期；`close()` 在存储上存在但没有 Decision 动作路径。
- 带作品的到期项若指向一个 WAITING/PAUSED 活动的工作，现有额外候选路径没有套用
  「停着的目标只能 RESUME」的过滤；本块没有跑到这个交叉场景，也没有据此改动候选供给。
