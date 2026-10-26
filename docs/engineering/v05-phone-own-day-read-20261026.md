# v0.5 手机只读面：她今天的安排与可证执行状态

更新：2026-10-26。范围是 **只读增量**，不改 `GET /api/phone/today` 的既有实现/形状，
不新增写协议、结果 store、日终顺延、MISSED 或前端。

## 入口

`GET /api/phone/today/arrangements`

- 与 `/today` 同一套 host / Origin / `Sec-Fetch-Site` 守护（loopback-only，私人事实面）；
- 只接受路径，任何 query 都以 `UNKNOWN_QUERY`（400）拒绝，不做静默回退；
- 纯读：只读既有两个 Life 文档、canonical Activity、AR-0/AG-1 读口与 owner 回执；
  不启动服务、不调用模型、不写结果表。宿主/来源读不到时是具名 `unavailable`（503），
  不是空列表。

## 返回（`phone.today_arrangements.v1`）

- `day` / `day_basis` / `timezone` / `clock`：日期取实例自己的业务时钟
  （`fastday/fastday_clock.json`，+08:00）。时钟缺失/损坏才按既有规则退回系统时钟，
  并在 `clock.source` / `clock.reason_code` 具名；读取本身不写时钟。
- `plan`：`{entries, count, status, reason_code}`。每条是
  `plan_ref` / `item_ref` / `definition_status` / `due` / `execution`。
  `definition_status=PLANNED` 只表示「排进了今天」，不是「还没开始」。
- `obligations`：`{entries, count, truncated, total, status, reason_code}`。每条复用
  `life_day_plan.obligation_result_view` 的 `result`（`PENDING` / `DONE` / `UNKNOWN`）；
  `PENDING` 是义务域既有语义「还没有 owner 回执」，不是失败，也不是未开始。
  超过 50 条时按既有排序截断并 `truncated=true`，不静默丢。
- `unplanned_activities`：今天确实发生、且能证明与任何安排无关的 Activity。
- `unclassified_activities`：有安排、但 Activity 的 Decision/Action 关联不足，标
  `association=UNKNOWN` + `reason_code`，**不硬分**成「未排」。
- `sources`：`plan` / `obligations` / `activities` / `execution` 各自的状态与具名原因。
- `read_only: true`。

## 执行状态从哪里来（不新增权威）

- **义务 `DONE`**：直接复用 `life_day_plan.persisted_obligation_evidence`。要求 Action
  目标同一、`decision_ref` 解析出的 Decision 是 `START`、其 `selected_candidate_ref`
  正是该义务确定性预留位候选、Action `SUCCEEDED` 且带 owner 版本回执、且有
  `activity_ref`。同作品的另一个版本、另一个 Decision 的动作、另一个目标都不认领。
- **自选计划**：计划文档只存 `fitem:` 引用；执行状态按同一套持久链证明（目标同一 +
  `START` + 该计划确定性 `popt:` 候选 + owner 回执）。没有目标/没有证据/关联不足一律
  `UNKNOWN`，绝不因为同作品有新版本就报完成。
  `popt:` 拼法在 `life_day_plan.reserved_options` 内部，手机模块按同一公式重算并在测试里
  用真实预留位钉住；公式漂移只会落回 `UNKNOWN`，不会误报 `DONE`。
- **Activity 关联**：只认 AR-0 Action 上的 `decision_ref`（以及 Activity 自己的
  `decision_ref`，若 owner 写了）解析出的 `START` Decision 的
  `selected_candidate_ref`。真实 canonical Activity 的 `decision_ref` 可能为空（START
  也可由 adoption_ref 满足），所以标题、前台指针、单纯同目标都不算关联。

## 实际可达性与权限

HTTP 读面已经按周围合同可达：任何同源本机客户端（手机页/测试/CLI）都能读。首轮交付时
**AI 侧的 `chiyo_phone` 工具没有对应 action**（`READ_ACTIONS` 是固定表），当时记为具名缺口，
未擅自改全局 tool schema。该缺口已在同一文档的后续增量里补上：

- `chiyo_bundle/phone_tool.py` 的 `READ_ACTIONS` 现登记 `today` → `/api/phone/today` 与
  `today_arrangements` → `/api/phone/today/arrangements`，两个动作都只读、只接受 `action`
  字段、不加写协议；
- 两者都在 `PRIVATE_ARTIFACT_ACTIONS` 里：活动/作品标题、计划与义务标题都是她的私人事实，
  受限记忆（`memory` 打开且策略不是 `unrestricted`）下仍需 profile 显式
  `phone.private_artifacts: true`；这是沿用既有第二把钥匙，没有放宽默认；
- 工具返回服务原始 envelope（`result` 内不出现 `csrf_token`），并明确告知模型：
  `PLANNED` 只表示排进了今天、义务 `PENDING` 只表示还没有 owner 回执、执行 `UNKNOWN`
  表示没有可证证据，都不等于「还没开始」或「失败了」；
- 不新增第二个协议，也不把手机状态塞进系统提示。

验证（2026-10-26 晚）：`scripts/phone_own_day_read_smoke.py` 在同一台真实隔离服务上同时走
HTTP 与真实 `chiyo_phone.phone_handler`，13 项检查全过（含受限记忆拒绝、工具结果与 HTTP
逐字段一致、计划 `PLANNED`+执行 `UNKNOWN` 与义务 `DONE` 分层）。工具侧最小用例见
`tests/test_phone_tool.py`。

## 验证

WSL `/mnt/d/nyairo-rc1-body-backend`，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`：

- `-m unittest -q tests.test_phone_today tests.test_phone_own_day_read` → 16 例 OK
  （原 today 5 例保持，新 11 例）。
- `scripts/phone_own_day_read_smoke.py` → `SUMMARY checks=10 failed=0`，`SMOKE_OK`。
  复用 `scripts/v05_own_day_smoke.py` 的真 Supply 隔离链（不改原文件）：正式 Decision
  计划 + operator 义务 + 真实 Action/回执；HTTP 读到的义务 `DONE` 的
  Decision/Activity/version 与 AR-0 持久 Action、owner 回执一致；计划无执行证据为
  `UNKNOWN`；另一件作品的普通续作 Activity 单列在 `unplanned`；同一作品被另一个
  Decision 写出新版本后计划仍不是 `DONE`；两次 GET 前后计划/义务/Action/Decision 文件
  与作品 head 逐字节不变；坏计划来源 503 具名、恢复后可读；空日诚实为空；服务经
  shutdown/drain/socket guard 正规关闭。

## 未验证 / 边界

- 没有真实产品模型运行；Decision/内容仍是脚本替身，不证明自然采用。
- 没有跑 resident 长循环、crash/强杀/半写、跨 PID；只读结果沿既有持久链重读规则。
- `chiyo_phone` 的两个 today action 只在隔离真 Supply 服务上验证过；没有在主实例或真实
  resident 循环里经模型调用，也没有新增手机页面/前端入口。
- `ActionStore.load_state` 在状态与 journal 不一致时会走它自己的恢复写；正常一致状态
  下测试已验证零写，但不把「读操作在任何损坏状态下都零写」当保证。
- `PENDING` 不自动变成 `MISSED`，没有日终顺延；空日就是空日。
- AI 工具层未接入（见上节具名缺口）。
