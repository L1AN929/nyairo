# Agent Note: 生产认知装配没有 observer —— 真实段的模型失败只剩一句 MODEL_PROVIDER_ERROR

Status: implemented

## Problem

`autonomous_life_assembly.model_cognition` 构造真实 `ProductionAgencyCognitionAdapter` 时没有传
`observer`（改前 :638-639）。adapter 的 `_observe` 在 `observer is None` 时直接返回，于是它已经算好的
`cognition_send_started` / `cognition_send_failed` / `cognition_send_response_received` /
`cognition_result` 全部被静默丢弃。一次真实日常段失败后，resident journal 只剩
`MODEL_PROVIDER_ERROR` 这类映射后的类名，无法回答：是 HTTP 503、连接超时、输出被截断、还是 JSON/schema
拒绝？同一个端点、同一个模型，换一种失败就换一个修法，而现场没有可区分的事实。

`components/life/scripts/autonomous_life_worker.py` 的影子路径装了 observer，但那是测试/影子 harness；
把它接成产品主线等于让影子运行时拥有生产写口。resident 自己已经拥有 `_journal` 和
`_replay_journal`，缺的只是一个安全的诊断回调。

本仓库上一篇相关笔记
[2026-10-21-v04-decision-invalid-schema-no-field-detail.md](2026-10-21-v04-decision-invalid-schema-no-field-detail.md)
在当时明确否决“给 adapter 装文件 sink”：那要新增存储面、配置项和一个新写者，超出定点修复边界。
本轮是独立的“能真正度过一天”准备阶段，问题不再只是 schema 字段，而是整条真实段的失败不可定位；
选择随之改变，但旧笔记的决定仍成立（不新增 sink/store），本篇只补上既有 journal 的绑定。

## Decision

1. **装配层透传 observer，默认不改变旧行为。** `model_cognition(..., observer=None)` 把 observer 原样
   交给 `ProductionAgencyCognitionAdapter`；不传时 adapter 与改前逐字一致，不写任何诊断，也不把
   send marker 当闸门。
2. **sink 是 resident 已经拥有的 journal。** `AutonomousLifeResident._cognition_diagnostic` 调
   `safe_cognition_diagnostic(record)` 后写 `resident_journal.jsonl`，记录带
   `kind: "cognition_diagnostic"`。不新建 debug 账本、不借别的 owner 写口、不导入影子 runtime。
3. **逐值重建，绝不整条落盘。** 只接受已知 event；`error_category`、`failure_class`、`parse_state`、
   `finish_reason` 用封闭词表，未知值记 `UNKNOWN` 或丢弃；状态码、`schema_retry_index`、延迟、字符数、
   `max_output_tokens_sent` 等走有界整数；`late`、`response_verifiable`、`response_received` 等必须是
   真布尔；`transport_attempt_id` / `model_run_ref` / `opportunity_id` / `started_at` 只接受 adapter
   自己生成的短 token 形态。`output_keys` 只记 **数量**；`provider`、`model`、
   `decision_kind_if_valid`、`selected_candidate_id_if_valid`、prompt、completion、reasoning、异常
   message、headers、token 一律不落盘。不为诊断额外计算哈希。
4. **required 边界仍然失败关闭。** adapter 的 `cognition_send_started`、发送失败与
   `cognition_send_response_received` 在 observer 抛异常时照旧 `raise`，所以“边界写不进 journal 就
   不发请求”。`cognition_result` 等 optional 记录写失败由 adapter 自己吞掉，合法决定不受影响。
   回调不发 HTTP、不 reserve 预算、不改变 retry/迟到结果/UNKNOWN 语义。
5. **诊断不是 tick。** `_accumulate` 跳过 `kind == "cognition_diagnostic"` 的行，`_replay_journal`
   因而不会把每个 provider 事件算成一次 wake；`components/phone/live_day.py` 的两处 journal 读取
   （`_recorded_ticks` 与 `segment_summary` 的 ticks）也跳过同一 kind。这是任务书写集之外的窄修：
   不跳过的话，三行诊断会把一次段落的 2 个 tick 报成 5 个，既有 `test_live_day_runtime` 直接红。
6. 不改 `agency_cognition_adapter` 的事件形状、AG-1 的 schema/规则、`LifeRequestBudget`、resident
   pause handshake 或任何产品契约。

## Alternatives considered

- **新增一个诊断文件 sink/store**：立论是隔离最彻底，诊断写坏了也不碰 resident journal，读取方不用
  认识新 kind。否决：这正是旧笔记已经淘汰的方案——多一个存储面、配置项和一个写者，而 resident 的
  journal 本来就是这次失败唯一需要并排阅读的记录；在既有 journal 里加一个可跳过的 kind，代价更小，
  也不需要新的清理/轮转契约。
- **把 adapter 的 record 原样写进 journal**：立论是信息最全，连 `output_keys` 和
  `decision_kind_if_valid` 都在。否决：键名、决定枚举、provider/model 字符串都可能由模型写出或夹带
  私文；原样落盘等于把“诊断”变成第二个私文副本。`output_keys` 只记数量是这条方案被否决后的直接结果。
- **接影子 worker 的 observer**：立论是现成、测试里已验证。否决：影子 harness 是测试与对照路径，不是
  产品运行时；把它接进 resident 会让影子拥有生产写口，也绕开“一个写集一个 writer”的边界。
- **只依赖 AG-1 的 `schema_refusal`**：立论是不新增任何写面。否决：它只在 schema 拒绝时有值；HTTP
  503、连接超时、输出截断、reasoning-only 都在到达 AG-1 之前就失败了，这些正是本轮要区分的情形。

## Consequences

- **收益**：下一次真实段无论成功还是失败，resident journal 都有
  `event/error_category/parse_state/finish_reason/response_status(若有)/latency_ms/字符数/
  output_key_count/schema_retry_index`，能把 503、timeout、截断、schema 拒绝分开；安全过滤器有正负控
  测试（恶意 key/value 私文不落盘、required 写失败零请求、无 observer 旧用法兼容、预算计数不变）。
- **代价**：resident journal 会多出若干诊断行；所有按行读取该文件的代码必须跳过
  `kind == "cognition_diagnostic"`。本轮改了 resident 的 `_accumulate` 与 phone/live_day 的两处读取，
  后者越出任务书写集，已在此具名。
- **上限**：诊断只回答“这次请求发生了什么”，不证明模型会采用某个决定，也不把短段升级成 24h 结论；
  它不记录正文，因此不能替代对模型内容的审查。
- **后续（2026-10-22）**：失败路径原先缺少 finish_reason/字符数/usage 计数，`output_shape`
  白名单随后补上失败行的形状与整数 token 计数，见
  [2026-10-22-cognition-output-truncation.md](2026-10-22-cognition-output-truncation.md)。
  逐值重建、required 失败关闭等本条决定继续成立。
