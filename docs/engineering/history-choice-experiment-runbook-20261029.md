# 历史影响选择实验：运行手册（2026-10-29）

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

本文件只说**怎么跑**和**它会拒绝什么**。方案本身（参数、判据、预算分配）以
[预登记](history-choice-preregistration-20261029.md)为准，两者冲突时以预登记为准。

## 它是什么

`scripts/history_choice_experiment.py` 把预登记写成一个 CLI：在一个全新的隔离实例里
真实采集最多 8 次请求，冻结同一份候选/情境/历史，然后用**正式 AG-1 校验**做 20 对
影子选择（A=真实准入历史，B=等长中性占位文本），最后在第一对样本上做一次限定的真实
承接。它不新增业务能力：Decision/采纳/Action/内容/Memory 全部走既有 owner 与正式
adapter，实验只多出「阶段额度门」「请求身份门」「实验记录」三样东西。

## 命令

```bash
# 1. 离线检查（0 产品调用；建的是 /tmp 下的隔离 scratch，不是正式根）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && \
  PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
  scripts/history_choice_experiment.py offline-check'

# 2. 建正式实验实例（0 产品调用；/root 只在 WSL 里，所以同样走 wsl）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 \
  /tmp/rc104-venv/bin/python -B scripts/history_choice_experiment.py \
  prepare --root /root/nyairo-histchoice-20261029'

# 3. 真实运行（唯一会发真实请求的入口，且只能用一次；模型变量由 operator 在同一 shell 里 source）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && set -a && . /root/.nyairo-model.env && set +a && \
  PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/history_choice_experiment.py \
  run --root /root/nyairo-histchoice-20261029 --execute-approved'

# 4. 事后审计（纯读，无正文）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 \
  /tmp/rc104-venv/bin/python -B scripts/history_choice_experiment.py \
  audit --root /root/nyairo-histchoice-20261029'
```

退出码：`0` 通过/跑完（包括「未建立有效历史」「没有差异」这类阴性结果）；
`2` 具名拒绝（前置条件、额度、参数、截止时间）；`1` 未预期错误（带安全 detail）。

## 真实运行前必须由操作者提供的东西

`run` 只在下面这些**进程环境变量**里取模型配置；harness 自己不读密钥文件、不打印、
不落库：

| 变量 | 说明 |
| --- | --- |
| `NYA02_MODEL_PROVIDER` | 预登记用 `custom` |
| `NYA02_MODEL_BASE_URL` | 预登记网关 `https://gw.example.invalid/v1` |
| `NYA02_MODEL_NAME` | `cn:deepseek-v4.1-flash` |
| `NYA02_MODEL_API_KEY` | 只来自 `source /root/.nyairo-model.env`，不写进任何文件 |

`CHIYO_AUTONOMOUS_RUN_ID`、`CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_*` 等是**实例自己**
记录的绑定，由 `prepare` 写进实例文件；不要在 shell 里另外导出（`run` 会拒绝 run 身份
不一致的绑定）。`CHIYO_AUTONOMOUS_NEW_RUN_WINDOW` 永远不设置。

## 上限与拒绝条件

- 总请求（决策+内容+任何重试）≤ 60，写在新实例自己的持久请求账本里；重启/换 run 不刷新。
- 阶段：采集 ≤ 8、影子 40（20 对）、真实承接 ≤ 8（常规只用 4：每臂 1 决策 + 1 内容）；
  诊断预留 4 **本 harness 没有任何路径会自动使用**。
- 每次发送前都读同一本账本；阶段上限还会**写进账本本身的 ceiling**，所以一个回合的
  决策+内容也不可能越过阶段上限。
- `run` 会拒绝：根不是预登记的绝对路径、根已被占用、不是 `prepared` 状态、缺
  `--execute-approved`、已过 `2026-10-30T01:00:00+08:00`、本次已启动过（含崩溃后重来）、
  账本非 0 或不可读、模型参数与预登记不符、阶段/总数超限。

## 它会写出什么

都在实验根（正式根是 `/root/nyairo-histchoice-20261029`）里：

- `experiment/histchoice.json`：材料、两件作品、`caps`、顺序表、模型参数、截止时间（0600）。
- `experiment/histchoice_batch.json` + `attempts.jsonl`：阶段起点、逐样本审计行、冻结摘要、
  停止原因。只含 ref/计数/布尔。
- `experiment/shadow-state/`：影子 AG-1 的 DecisionRecord（**实验记录**，不是正式决定）。
- `experiment/capture-state/`：结构门那一遍的 AG-1 记录。

另外有两个**在实验根之外**、但属于本实验保留物的路径：

- `/root/nyairo-histchoice-20261029-memory-authority/authority.db`：既有规则要求删除底账
  放在业务根之外；它只是一个删除下限账本，不含经历正文，本实验不执行任何 DELETE。
- `/root/nyairo-histchoice-20261029-branch-shadow-b/`：真实承接时 B 臂的同层分支副本
  （复制时刻的实例状态），路径重写与隔离检查见 `run` 的返回字段。

## 真实运行前的一次性检查（不发请求）

1. `offline-check` 四项全绿：单元测试、参数实测（决策 4096 / 内容 2048 / 温度 0.0 /
   stream=false，由**真实 adapter/writer 渲染的实际 payload** 读出）、真实 owner + 正式
   runtime 的结构门（候选 2 件都可执行 + NO_ACTION）、整条流程的脚本 dry-run。
2. 现场确认没有并发产品实验；正式根不存在。
3. 由本人确认预登记。

## 已知边界（不要读成结论）

- 离线检查与 dry-run 里的「模型」全部是替身，**不构成任何真实模型证据**；dry-run 的结论
  字段固定写成 `DRY_RUN_SCRIPTED_NOT_A_RESULT`。
- 影子只证明「该模型、该场景下历史文本的条件效应」，不排除一般信息提示/显著性效应，
  不证明自传记忆独有机制、稳定偏好、G1 或 EL5；20 对共享同一个历史场景。
- 温度 0 不保证确定性；两臂实际 token 数可能不同（只保证字符等长）。
- 采集可能 8 次内建立不起有效历史，或建立后仍只有单一可执行选择——这两种都是预登记的
  合法出口，报告原结论，不补样、不换根。

## 2026-10-29 safety-finish 后的发送边界（读这段再决定要不要 prepare）

- 结构门不传 transport 也走本模块离线替身；`offline-check` 的 structural 门现在**故意不传**
  transport，并报 `requests_sent=0`。
- 决策与内容两个发送口前都有 `SendLatch`：截止到点、采集遇到传输/供应商失败、影子里任何 stop，
  都会把后续请求关掉；影子里一 stop，承接 A/B 直接 NOT_RUN。
- 承接改成**先 clone 分支再跑 A**（活 sqlite 用 sqlite3 backup 一致性快照）；clone 或隔离审计
  不过就两臂都不跑。每臂 >2 即停。
- **每臂上限是发送前的硬门**：`continue_round` 在调用 `evaluate_once` 之前，把两臂共用的那本
  持久账本的 `run_window_max` 临时压到 `min(原总/阶段 ceiling, 本臂起点 + 2)`，回合结束或抛异常
  都在 `finally` 里恢复原值；开局不重置账本、不退计数。所以第 3 次发送（不论决策还是内容）会在
  `LifeRequestBudget.reserve()` 处被拒，根本到不了 transport。A、B 各自取自己回合开始时的计数作
  起点，互不借用额度。`run_continuation` 里 `requests > 2` 的检查保留为事后断言，不作为硬门。

- 影子两臂同一 trigger，每样本一个独立 shadow store；发送前用渲染文本+payload 双指纹和历史
  形状拒裁剪/第二变量；`schema_retry_index>0` 当场拒绝（一次尝试一次计费）。
- 结构门的「两个可执行候选」必须是**不同作品**（H 与另一件），同件的 START/closeout 不算。
