# Agent Note: v0.6 C 三小时窗口的离线入口与局部参数贯通

Status: implemented

## Problem

[C 预登记](../../../../docs/engineering/v06-world-natural-c-prereg-20261102.md)（方案篇见
[proposed note](../../proposed/testing/2026-11-02-v06-natural-c-long-window.md)）要求在 B 之后
另开一次更长的真实自然观察：新身份 `v06-natural-c`、192/300 历史、20 请求、
**10800 真实秒**、20:00 最晚启动/23:05 绝对截止、单 natural phase、临时 canonical gate。
B 的 1800 秒窗口、900 次 tick 上限和 4000 行 trace 容量都按 B 的半小时设计；直接沿用会
在第 900 轮或第 4000 行提前截断，而全局改大会同时改掉旧 a～f 与 B 的封闭值。需要一层
只作用于 C 的薄入口和最小 profile 参数，让离线能证明 10800 窗口真正贯穿
prepare/prereg、窗口落盘、发送前拒绝、phase 循环、audit 与父级超时，同时不运行真实模型。

## Decision

* 新增 `scripts/v06_world_natural_c.py`：`offline-check` / `prepare` / `audit` / `run` 四命令。
  真实命令固定读写 `C_PROFILE.root`，没有 `--root`；`run` 仍要求 `--execute-approved`。
  真实 base URL 只从运行期 `NYA02_MODEL_BASE_URL` 取；缺失、`.invalid` 占位、非 http(s)、
  URL 自带凭据都在写任何东西之前拒绝。本轮只实现，未执行 prepare/run。
* `BatchProfile` 增加三个可选字段 `window_seconds` / `max_ticks` / `trace_max_records`，
  旧批一律 `None`。`_profile_window_seconds` / `_profile_max_ticks` /
  `_profile_trace_max_records` 按当前身份解析，缺省退回原模块常量 1800/900/4000。
* `C_PROFILE` 注册 `/tmp/nyairo-v06-natural-20261102-c`、独立 authority、
  `run:v06-world-natural-20261102-c`、`v06:world-natural:20261102-c`、192、
  20:00 最晚启动、23:05 截止、窗口 10800、tick 3000、trace 6000；**不**加入
  `BATCH_PROFILES` / `BATCH_CHOICES` / `CLI_BATCH_CHOICES` / `NATURAL_PROFILES`。
  `_is_natural` 额外认 C；`_world_intent_gate` 只在 C/e/B 返回 canonical。
* 同一 profile 窗口贯穿各处：`prepare` 的 prereg caps 写 10800（C 另写 max_ticks/trace）、
  `_write_window` 的默认参数改为运行时解析（不再在导入时绑定 `WINDOW_SECONDS`）、
  phase 的 tick 循环读 `_profile_max_ticks()`、`CandidateTrace` 在构造时取本批行数上限、
  `_d_trace_observation` 的读取截断也用同一值、`_natural_phase_timeout` 对 C 用 60 秒收尾、
  `audit` 对 C 只读回显 window/max_ticks/trace 三项。
* C 继承 B 已验收的全部局部机制：坏账 fail-closed 身份分支、父/phase/两个 owner 的离线
  网络守卫、真实子进程 canonical 映射与严格 world_home 隔离、60 秒收尾界限；
  旧 a～f 与 B 的值、默认和 CLI 集合一个不改。`offline_reference` 对带 latest_start 的批
  额外夹到启动线之前（C 是 19:55），否则 C 的离线 start_run 会被自己的 20:00 线拒绝。
* 新增 `tests/test_v06_world_natural_c.py`：C 身份与 10800/3000/6000、旧值与 B 值不变、
  窗口默认按身份绑定、离线参考时刻早于启动线、runtime/phase 子环境同值且根内、
  20→21 与坏账发送前拒、最晚启动/截止前拒、错根/缺 latest_start 前拒、60 秒界限。
  不跑旧批套件。

worker离线检查实测（最终自测，非父级验收）：固定入口 `v06_world_natural_c.py offline-check` exit 0，
44 项全 PASS，18.0 秒。真实 phase 子进程起标准 Supply/World/runtime，World READY、
gate 与 child env 都是 canonical，World home/registry/lifecycle 全在本根；脚本 Decision
的 START 产生一条正式 POSE Action（SUCCEEDED、1 attempt、1 owner receipt、同一
execution 的 lifecycle APPLIED）；HTTP 与产品模型请求为零；两个 owner 子进程与父进程的
网络自检都是四拒一允；runtime closed、world/supply `socket_live_after=false`；repo 默认
`data/world/run/action_lifecycle.jsonl` 前后零新增；真实 root/authority 仍 ABSENT。
首轮同一入口 exit 3，唯一红项是检查代码把 `deadline_basis` 的结尾多算了一个右括号，
修正断言后重跑通过；实现本身未改。证据
`.agents/worker-runs/20261102-v06-natural-c-offline-out.json` 与同前缀 `.log`，
scratch `/tmp/v06-natural-c-offline-off1kod_`、首轮 `/tmp/v06-natural-c-offline-ujmepixe` 保留。

父级随后独立运行同一固定入口，实际exit0、44项全真、26.941秒，新根`/tmp/v06-natural-c-offline-t1hi0ztu`保留。真实phase脚本POSE单Action/attempt/回执及同execution APPLIED、父与两个owner网络四拒一允、20→21/坏账/时间/重跑拒绝均通过；runtime closed、两个owner exit0/listenerfalse，但graceful行未见，不冒drain/lease已独立证明。60秒仍仅参数及0.152秒受控超时负控，不是整树挂起60秒实测。worker首红和18秒自测另列，不倒写一次全绿。

## Alternatives considered

* **直接调大模块常量 `WINDOW_SECONDS` / `MAX_TICKS` / `CANDIDATE_TRACE_MAX_RECORDS`**：
  最省事，一处生效。但会同时改旧 a～f 与 B 的窗口、循环与观测容量，违反“旧值不动”，
  也会让 B 的封闭历史无法复现。否决。
* **在 C 脚本里传显式参数绕过模块常量**：能避开全局改动，但 `prepare` 的 caps、
  `_write_window` 默认、phase 循环与 trace 构造分散在多层，容易漏接；预登记明确要求
  同一窗口贯穿 prepare/写窗口/发送前/phase/audit/父级超时。否决，改为按 profile 解析。
* **让 C 复用 B 的 profile 只改 deadline**：代码最少，但会把 10800 窗口塞进 B 的身份，
  改坏 B 已验收的 191/1800 封闭值与结果。否决。
* **改 30 分钟检查间隔以减少轮数**：省 tick，但同时改变 B 的节奏，无法判断“只延长窗口”
  的影响；预登记已选保留 1 分钟。否决。
* **注入身体变化或改指纹以制造第二次请求**：最容易得到样本，但会改变待观察对象和
  生产触发规则。否决。

## Consequences

* 只证明离线接线与参数贯通。沿用B已核的既有endpoint，本次未重新读取或探测；真实 `prepare/run` 未获批准、未执行；
  C 的 10800/20 与 20:00/23:05 仍是待批建议，不是可支用授权。
* 自然是否选 MOVE、是否出门/返程、完整 G1、理解与 24h 都未验证；离线 POSE 不等于自然
  选择或 World 往返。一次请求或多次请求都不能单独归因身体变化。
* C 的 tick 上限 3000 覆盖 2160 次 5 秒 tick，但 3 小时真实运行、极端收尾与
  2160次左右服务 tick 的现场行为未在本轮验证；60 秒收尾只有纯函数与受控短进程负控。
* 旧 a～f 与 B 的窗口/循环/trace/收尾值和 CLI 枚举都保持原样；C 只由新脚本经
  `_use_batch` 进入，不扩旧公开面。shared helper 的改动都按身份短路，旧批行为不变。
* 数据与 scratch 全部保留；临时 gate 退出不撤销所有票、不回滚效果，也不代表
  registry 全量撤销。
