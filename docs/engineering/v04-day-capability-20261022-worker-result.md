# v0.4 日常能力：正式认知诊断接线 + 有限验收探针（worker 交付）

日期：2026-10-22。写集与边界按 `20261022-day-capability` 任务书执行；本次施工**没有实际调用模型**，
没有运行 24h，没有 stage/commit/push/deploy，没有改产品契约或 schema。

## 1. 改了什么

### 1.1 正式 resident 接线

- `components/life/scripts/autonomous_life_assembly.py`
  - `model_cognition(..., observer=None)` 新增可选参数，原样传给
    `ProductionAgencyCognitionAdapter`。不传时与改前完全一致（adapter 不发出任何遥测，
    send marker 也不是闸门）。
- `components/life/scripts/autonomous_life_resident.py`
  - `start()` 把 `self._cognition_diagnostic` 作为 observer 传给 `assembly.model_cognition`。
  - 新增 `safe_cognition_diagnostic(record)`：逐值重建 adapter 遥测。只接受已知 event；错误类、
    failure class、parse state、finish reason、预算 scope 用封闭词表，未知值变 `UNKNOWN` 或丢弃；
    状态码/索引/延迟/字符数走有界整数；布尔必须是真布尔；adapter 自己生成的 token 才保留。
    `output_keys` 只记数量；`provider`/`model`/`decision_kind_if_valid`/
    `selected_candidate_id_if_valid`/prompt/completion/reasoning/异常 message/headers/token 均不落盘。
  - 新增 `_cognition_diagnostic`：写 resident 已有的 `resident_journal.jsonl`，行带
    `kind: "cognition_diagnostic"` 与可信时间。写失败向上抛，adapter 的 required send marker 因此
    在“边界记不下来”时拒绝发请求；optional 记录写失败由 adapter 自己吞掉。
  - `_accumulate` 跳过诊断 kind，`_replay_journal` 的 tick 计数与 wake 计数不被诊断行污染。

### 1.2 必要越界（具名）

- `components/phone/live_day.py`：`_recorded_ticks` 和 `segment_summary` 的 ticks 读取跳过
  `kind == "cognition_diagnostic"`。原因：既有 summary 把 resident journal 的**每一行**都当 tick
  读；不跳过时，一次正常段落里三条诊断会把 2 个 tick 报成 5 个，`tests.test_live_day_runtime`
  实跑红。该修改只增加一个可跳过的 kind，不改变任何既有 tick 行的语义。任务书写集未列此文件，
  在此具名说明。

### 1.3 父级验收探针

- `scripts/day_capability_probe.py`（新）：`prepare` / `status` / `run`。
  - `prepare`：只在不存在的新 root 调 `demo.prepare_demo(with_world=True)`；阅读材料明确标注为
    验收合成材料；工作正文保持 `seed_content=None`；之后用既有
    `autonomous_life_control.set_enabled` 显式打开该新实例的自主开关；不调用模型、不写模型决定/
    理由/经历。
  - `run`：只调既有 `phone.live_day.run_segment`。默认 180 秒、poll 2 秒、每段最多新增 6 次请求；
    硬拒绝 >600 秒、poll <2 秒、cap >6。不设 `CHIYO_AUTONOMOUS_NEW_RUN_WINDOW`、不 reset、
    不清 quiet、不制造事件。
  - `status`：只调既有纯读 `phone.live_day.status`，不建目录、不写报告、不发模型。
  - 模型配置只从进程环境 `NYA02_MODEL_*` 解析；报告只写 model/provider/scheme+host，不写密钥；
    密钥不写实例、`model_env`、配置或报告。24h 固定 `NOT_RUN_BY_USER_SCOPE`，不伪算时长。
  - `prepare` / `run` 的报告写到新 root 的 `probe/`，只是验收证据，不是产品第二真相。

### 1.4 测试与笔记

- `tests/test_v04_day_model_diagnostics.py`（新，10 例）
- `tests/test_day_capability_probe.py`（新，11 例）
- `.agents/notes/implemented/bug-fix/2026-10-22-live-model-diagnostics-binding.md`（新）

## 2. 实跑命令与结果

全部在 WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`，仓库根
`/mnt/d/nyairo-rc1-body-backend`。

| 命令 | 结果 |
|---|---|
| `python -m unittest tests.test_v04_day_model_diagnostics -v` | 10 例 OK，exit 0 |
| `python -m unittest tests.test_day_capability_probe -v` | 11 例 OK，exit 0 |
| `python -m unittest tests.test_live_day_runtime -v` | 16 例 OK，exit 0（第一次实跑发现 ticks 5 != 2，定位为诊断行被当 tick，修 `live_day.py` 两处后复跑通过） |
| `python -m unittest tests.test_one_day_life_w05r.RequestBudgetTest -v` | 9 例 OK，exit 0（只跑该 class） |
| `python -m unittest tests.test_day_life_runtime_coverage -v` | 14 例 OK，exit 0 |
| `python -m unittest tests.test_cognition_thinking_response -v` | 7 例 OK，exit 0 |
| `python -m unittest tests.test_decision_schema_refusal_diagnostics -v` | 24 例 OK，exit 0 |

新诊断测试只使用本机 loopback HTTP 替身和真实 assembly seam，不连真实网关；probe 测试全部离线，
`prepare`/`run` 的真实 owner 路径用注入替身验证边界与报告结构，没有创建真实 Supply/World owner。

## 3. 安全正负控（新测试断言）

- 正常响应：send_started / response_received / result 三类诊断落 journal；`output_key_count` 正确。
- HTTP 503：`error_category=PROVIDER_SERVER_ERROR`、`response_received=true`；transport 未在异常上
  携带状态码时 `response_status` 缺失而不是编造。
- timeout：`error_category=TRANSPORT_TIMEOUT`、`response_received=false`。
- 恶意 record：模型写的 `provider`/`model`/`output_keys`/`decision_kind_if_valid`/任意值时，
  journal 只留安全重建后的字段与 `output_key_count`，私文 marker 在整份 journal 中不出现。
- required sink 失败：`cognition_send_started` 写失败时 loopback `hits == 0`，请求没有发出。
- optional sink 失败：`cognition_result` 写失败只产生 adapter 警告，合法决定仍返回。
- 无 observer：旧用法照常发请求，不写诊断。
- 预算：同一请求在有/无 observer 下 `utc_day_calls == run_window_calls == 1`。
- journal replay：一条 tick + 一条诊断重放后 `ticks == 1`、`wakes == 1`，没有幻影 wake。

## 4. 父级如何用同一个新 root

```bash
# 1) 只读确认（不写、不发模型）
python scripts/day_capability_probe.py status --root <new-root>

# 2) 准备一个全新 root（不存在才执行）；明文密钥只从当前进程环境继承
export NYA02_MODEL_BASE_URL=...
export NYA02_MODEL_NAME=workbuddy-wg/cn:deepseek-v4.1-flash
export NYA02_MODEL_API_KEY=...          # 不写进任何文件
python scripts/day_capability_probe.py prepare --root <new-root>

# 3) 有限段；最多 96 次请求由实例自己的既有 ceiling 与 run window 共同约束
python scripts/day_capability_probe.py run --root <new-root> \
    --duration-seconds 180 --poll-seconds 2 --max-new-requests 6
```

`prepare` 报告在 `<new-root>/probe/prepare-report.json`，`run` 报告在
`<new-root>/probe/run-report.json`。父级应把报告里的 `requests.before/after/delta`、
`model.calls_this_segment`、`doing`、`changed.owner_version_refs`、`stop_point`、`next`、
`pause_quiet_fault` 与 journal 里的 `cognition_diagnostic` 行并排阅读。`status` 不写报告。

## 5. 未验证 / 边界

- 没有实际调用任何真实模型；真实段的 503/timeout/截断分类由 loopback 替身证明，不等于对国内端点
  的实测。
- 没有创建真实实例、没有运行 `prepare`/`run`；真实 Supply/World owner 启动路径由既有
  `test_live_day_runtime` 覆盖，但本轮 probe 自身的 prepare/run 成功路径未实跑。
- 没有运行 24h，不宣称一天能力通过；`NOT_RUN_BY_USER_SCOPE` 是唯一结论。
- Memory 仍 `UNBOUND`，短段不当作完整 Memory 闭环。
- 没有跑全库 `scripts/test.py`；以上是任务书写明的受影响套件与具名 adapter 用例。
- `components/phone/live_day.py` 是任务书写集外的必要窄修，原因见 1.2，请父级复核。
