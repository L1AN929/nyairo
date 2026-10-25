# 认知输出截断 · 父级独立离线验收

日期：2026-10-22。用户本轮授权：定向诊断并修复，先别继续跑真实模型。产品真实调用新增0；没有启动/修改主实例，没有重发原失败请求，没有stage/commit/push/deploy。

## 已交付行为及证据限度

worker `output-truncation-fix`（国内workbuddy-wg/cn:deepseek-v4.1-flash）exit0，日志 `.agents/worker-runs/20261022-210553-output-truncation-fix.log`；代码写集为transport/client/adapter/resident窄接缝，新增13例测试、note及worker结果。监工未通读components实现、未写业务代码。

- transport的截断异常携带安全形状元数据；adapter失败分支传实际输出cap与白名单形状；resident安全sink接收有界token计数。未知字段/值、正文、推理文本、异常message不得进入诊断journal。
- `finish_reason=length`在空正文、部分JSON、恰好完整JSON时都拒绝，不解析reasoning补决定，不拼接截断正文。
- adapter的决策输出下限由2048调为4096；显式更高配置保留；无自动重试、倍增或deadline修改。若请求写满，上限内completion token可能为旧值两倍；请求数上限与预留/失败不退款不变。

真实失败已经证实供应方返回length及正文为空：这是原代码中PROVIDER_OUTPUT_TRUNCATED的产生分支与已保存journal的对应关系。**失败的原始usage未保存，具体token量与cap是否耗满未知。** 成功样本的7760字符推理、199字符正文不能换算成失败token量或剩余预算。因此4096只是一项有界预防余量调整，不能说真实截断已消失、4096已被网关接受或已证明足够。

## 父级实际验收

在仓库根WSL执行：

`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_cognition_thinking_response tests.test_v04_day_model_diagnostics tests.test_output_truncation_diagnostics tests.test_decision_schema_refusal_diagnostics tests.test_one_day_life_w05r.RequestBudgetTest`

63例，13.309秒，OK，shell exit0。原始输出 `.agents/worker-runs/20261022-output-truncation-parent-tests.log`。

覆盖的具名边界：
- length空/部分/完整JSON均拒，reasoning-only与真正坏JSON不混类。
- 发出payload的cap4096可断言，显式8192保留，单次调用无增发。
- 失败形状实际写入resident journal，未知/恶意值不携私文落盘，缺失计数不冒写0。
- 无observer兼容；required send marker写失败零请求；late结果不进入epoch；截断失败只计一次且不退款。
- AG1原严格schema与理由长度边界仍通过；请求日/窗口上限不变。

两个observer失败警告是测试主动注入的预期负控，不冒称日志全空。worker新模块首轮曾因socket超时先于adapter deadline而失败，改用确定性adapter替身后通过；这是worker报告的失败历史，父级本轮63例全部一次通过。

父级notes三脚本：tree85、format85、archive0，均exit0。写集tracked diff --check无错误，三个新文件no-index --check无空白问题（exit1只表示文件与/dev/null有差异）。相对派工前status范围新增项落在任务书允许文件；memsearch index.pid自动删除保留、不归本轮接管。已dirty的resident/旧note由worker具名报告最小改动，不拿status相同声称逐字节未越界，也不额外做全库hash审计。

## 文档因果表述返修

初版新note标题及两句把字符长度说成“2048余量已不够/可离线证明”，不受现有证据支持。父级在代码测试通过后发仅note返修工单 `.agents/worker-runs/20261022-output-truncation-note-correction-task.md`，要求保留旧决策理由、说明4096是预防性选择。返修worker已exit0（日志20261022-211456-output-truncation-note-correction.log），只改该note；父级实际读取核对，过强标题与因果断言均已纠正。父级再次复跑notes tree85/format85/archive0，均exit0，note空白检查无错误。未重复运行已绿63例，未改代码或新增产品模型调用。本轮离线修复与文档验收至此完成，真实网关效果仍未验证。

## 状态与未验证

真实产品请求仍为历史主实例10次+旧预检1次，共同11/96、余85。不是本轮实时跑模型读得的计数；本轮工具没有接触主实例或密钥，数值沿用上一段已验账本。

未跑全库/浏览器/真实provider，未做24h。原两次OUTPUT_TRUNCATED不倒写成成功，Memory UNBOUND、理解UNKNOWN、EP09恢复FAIL不变。没有后台resident或新的实跑预约；真实网关效果必须另经用户授权才可验证。
