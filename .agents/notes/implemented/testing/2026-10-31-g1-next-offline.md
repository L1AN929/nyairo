# Agent Note: G1 下一次限定采用的离线判据，不再拿“又写了一版”冒充继续

Status: implemented

## Problem

`2026-10-30-g1-limited-real-adoption` 已经把同作品闭环接上真实 owner/runtime，并且
`scripts/g1_same_work_recovery_demo.py` 的隔离链能跑通“中断 → 跨 PID → UNKNOWN → CAS →
NO_ACTION → 继续”。但这份 demo 的报告里，成功的 `CONTINUE` 只写了 outcome/Activity/版本号，
没有写出这次续写绑定的 **Action id 与目标 Artifact**；而下一批要回答的恰恰是更窄的一条：
首个真实模型自选且 owner 成功的 A/actA 是不可替换锚，重启后的新 Decision 是否**精确**回到
同一件作品、同一个 Activity，并产生新 Action/新 owner 版本。只比较“版本 +1”或“outcome=ACTED”
会把同源换作品、换 Activity、甚至重放旧成功混过去。

同时，旧 `g1_real_adoption.py` 的批次身份把截止时间写死在 profile 里，a/b/c 的截止都已过去；
为了跑离线预检去加一个批次，等于改动冻结脚本并放宽旧批身份。需要一个新的、**只离线**的入口：
真实夹具 + 真实 owner/runtime/正式 Decision 链，但 Decision/内容是脚本替身，网络硬拒，
并且 `--execute-approved` 本身不能变成授权。

## Decision

新增 `scripts/g1_next_offline.py` 与 `tests/test_g1_next_offline.py`，预登记见
[g1-next-prereg-20261031](../../../../docs/engineering/g1-next-prereg-20261031.md)：

- 唯一可用入口是 `--offline-check`；`--prepare`/`--run`/`--execute-approved` 一律返回
  `OFFLINE_ONLY_NO_REAL_ENTRY`（exit 2）且 `files_written=[]`。没有真实 prepare/run 路径。
- 父进程用 `tempfile.mkdtemp` 建隔离根，再复用现成夹具 `prepare_fixture(root=...)`；P1/P2
  各自是 `python -B this_file --phase p1|p2` 的独立进程，**真实不同 PID**，同根、同 run。
  父进程与两个 phase 子进程都在入口处安装 `_http_forbidden()`：HTTP(S) connect 与普通
  `socket.connect` 直接 `OFFLINE_HTTP_FORBIDDEN`，只有 AF_UNIX 的 owner socket 放行；
  同时把 `NYA02_MODEL_*` 指向 `offline.invalid`，误构造的真实 client 也不可能发出去。
- P1 走正式 `evaluate_once`：首个真实 owner 写入产出 A/actA，记录 `first_action_id`；
  另建作品 B 只做对照；随后写既有 operator pause，正规关闭 runtime/owner。
- P2 新 PID 重开同一根：暂停轮纯读零请求；正式解除后由脚本 Decision `CONTINUE`，只从本轮
  真实请求里精确选 A 的 CONTINUE candidate；记录这次 `action_id`、`artifact_ref`、`actA`
  与 owner 新版本。UNKNOWN/CAS/NO_ACTION 复用既有正式 API 串在同一条链上。
- 判据 `_evaluate_next_g1` 只认证据：A/actA 锚、跨 PID、同 run、两进程间零写、暂停轮零请求、
  新 Decision=CONTINUE、目标=A、Activity=actA、Action 非空且不是 P1 的那个、owner 新版本
  且版本数恰好 +1。任一不满足即 `NOT_COVERED`。离线核心即使全过，`real_g1_evidence` 仍
  固定 `NOT_COVERED`，reason `OFFLINE_SCRIPTED_STANDIN`。
- 判据反例在纯数据层做，不重跑链：把目标改成 B、把 Activity 改成别的、把版本改回不增长
  （重放旧成功）、把 outcome 改成 NO_ACTION，四种都必须 `NOT_COVERED`。
- 最小负控（复用现成组件，不铺矩阵）：两个子进程共用一本持久账本 reserve 12+8，第 21 次由
  既有 `G1CognitionGate` 在发送前拒（`REQUEST_CAP_REACHED`，内层 0 调用）；坏账本用
  `LifeRequestBudget.check()` 在 send 前拒（`REQUEST_BUDGET_STATE_CORRUPT`，内层 0 调用）；
  同一根第二次写启动窗口被既有 `_write_window` 拒（`RUN_ALREADY_STARTED`）。
- 数据保留：不使用 `TemporaryDirectory`/`rmtree`/裸 unlink；停服务走既有
  `_stop_services`（shutdown → server_close → drain → `prepare_socket_path` 守卫）。

## Alternatives considered

- **直接复用 `g1_real_adoption.py offline-check` 作为本单成果**：最强的理由是它已经覆盖六个
  场景、有完整的发送门/预算/身份检查，几乎零新代码。否决原因：它的 P2 报告不记录成功
  `CONTINUE` 的 Action id/目标 Artifact，无法支撑“同 actA/新 Action/不串 B”的判据；而批次
  profile 的截止都在过去，要跑就得改冻结脚本加批次，等于顺手改旧批身份。
- **在本机直接跑真实模型，拿一次真实采用当证据**：最强的理由是这才是 G1 真正缺的那一步，
  离线判据再细也不能代替真实模型。否决原因：本窗口没有 run 授权、没有分配新预算；把离线
  预检偷偷升级成真实批次，会让“预登记”变成既成事实，也绕开本人对真实启动的直接批准。
- **另建一套覆盖六个场景的完整仪器矩阵**：最强的理由是覆盖最全、以后读起来最省事。否决原因：
  旧 `offline-check` 已有同作品六场景机制证据，本单要补的是“判据真实性”，铺矩阵既重复又
  把时间烧在仪式上；最小链 + 四个反例就够证明判据会拒绝错答案。
- **把判据写成“版本数 +1 且 outcome=ACTED”**：最强的理由是字段现成、代码最短。否决原因：
  同源另建的 B、别的 Activity、重放旧成功都可能让这两个条件成立；不绑 Action id 与目标
  Artifact，就无法区分“继续 A”与“又写了一件”。

## Consequences

- 离线判据可重复：`--offline-check` 27 项全 PASS，含 12 项 next-G1 判据、4 个反例、预算/
  坏账/重复启动/HTTP 守卫；最小单测 5 例 OK。
- 真实入口被明确拒绝，`--execute-approved` 不构成授权；本单不建 G1 正式根、不做 G1 正式
  prepare/run、不加载密钥、不发 HTTP、不新建预算（夹具的 `odl.prepare` 只在 mkdtemp 内）。
- 离线全绿**不等于** G1 通过：Decision/内容是脚本替身，不证明自然采用或 EL4；真实批仍需本人
  直接批准，且批准后要重新核对 history 是否为 168（本单只记最坏 188/余 112）。
- 已知边界：P1/P2 是“有序停启 + 有界故障注入”，不是 crash 矩阵；World 默认 off，本链不测
  世界动作；`g1_real_adoption.py` 的 `_ledger_calls` 对 `STATE_CORRUPT`（小写）不敏感这一
  既有差异未改，本单的坏账负控改用 `LifeRequestBudget.check()` 的 fail-closed 语义。
- 隔离根保留在 `/tmp/g1-next-offline-*`，不清理；没有任何旧实例根被访问或改写。

## 后续：f 的独立 live 入口（2026-10-31，批准后的启动适配）

本人批准三项决策单后，新增 [g1_next_live.py](../../../../scripts/g1_next_live.py) 作为 f
批的 prepare/audit/run 薄包装，并在 `g1_real_adoption` 里只做 f 的封闭注册：

- `F_PROFILE` 固定 `/root/nyairo-g1-real-20261031-f`、`run:g1-real-20261031-f`、
  `g1:real:20261031-f`、history 168、`2026-11-01T01:00:00+08:00`；只进 `MODE_PROFILES`，
  旧 `BATCH_PROFILES`/`BATCH_CHOICES`/`CLI_BATCH_CHOICES` 一个值都没动（f 由
  `g1_next_live` 的 `_use_batch` 进入；只有内部 `_reserve` 子命令多认 f）。
- f 沿用 a/b/c 的 p1/p2 真实链，但 World gate 固定 `off`，`_runtime_env`/`_phase_child_env`/
  `start_run`/`cmd_phase` 复用 e 的 strict `world_home` 守卫与逐项 env 校验；**不启动**
  World 服务。p1 新增 `first_action_id`，p1/p2 在 f 下新增只读 `world_gate`/`isolation`
  安全字段；旧批报告形状不变。
- 判据在入口层而不是状态机里：`verdict` 要求 A/actA 锚、跨 PID、同 run、暂停零请求、
  同 A/actA 的新 Action（≠ P1 首个 Action）、owner 新版本且版本增长；五段各自报标签，
  `real_g1_evidence` 固定 `NOT_COVERED`，不因锚续写成功宣称 G1 通过。
- 离线验收走**实际 CLI 子命令**：`tests.test_g1_batch_f` 18 例，在 `mkdtemp` 根上
  prepare×2 + run + audit，HTTP 硬拒、脚本 transport、真 owner p1/p2 链；另核共享 20
  账本第 21 次拒、`budget.check()` 坏账 fail-closed、schema retry 拒、失败停因跨进程持久、
  父 env 不变与 repo 默认 World 台账零新增。
- `--offline-fixture` 只接受 `g1-live-f-*` 的临时根，是离线验收入口，不是授权；
  真实根未创建、未 source 密钥、未发 HTTP、未产品运行。
- 已知边界：离线用注入器沿既有接缝各触发一次 UNKNOWN/CAS，属人为故障；真实模型自然采用
  仍未发生。`_ledger_calls` 对坏账小写 `corrupt` 的既有差异未改，坏账负控走
  `budget.check()`。
