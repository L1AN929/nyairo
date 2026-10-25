# 探针退出码、真实阅读材料与同实例第二段 · 父级验收

日期：2026-10-22。结论：退出码修复与材料安装通过；真实模型已选读新材料并留下读后记；随后两次认知输出截断，整段不能宣称业务全绿。未做24h验证，不继续盲跑。

## 实际变更与写者

唯一业务/工具writer为 `dispatch_worker.py` 派出的 `day-probe-reading-fix`，模型 `workbuddy-wg/cn:deepseek-v4.1-flash`，日志 `.agents/worker-runs/20261022-203550-day-probe-reading-fix.log`，worker exit0。父级仅管理、独立验收、执行已授权工具、写报告/NOW/检查点，无stage/commit/push/deploy。

worker写集：`scripts/day_capability_probe.py`、`tests/test_day_capability_probe.py`、新 `scripts/day_reading_material.py`、新 `tests/test_day_reading_material.py`、新process note和worker结果报告。原有dirty保留。

`_exit_code` 保留成功0、原样透传非零，缺失/None失败关闭。新增材料工具通过既有真实Supply owner创建新artifact，仅更新手机/nested实例三处阅读绑定元数据，写前备份；不原地换旧文，不重建实例、不清历史/预算，不强制选读。

## 父级独立复跑

- WSL `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_day_capability_probe tests.test_day_reading_material tests.test_live_day_runtime`：44例，33.898秒，OK，exit0。
- 仓库根全局write-notes三脚本：tree84、format84、archive0，均exit0。
- 写集 `git diff --check` 无错误；六个新/改文件 `git diff --no-index --check /dev/null <file>` 无空白问题（有差异时code1不是检查失败）；未stage。
- 主实例安装前 `day_capability_probe.py status`：实际shell exit0，READY，run:one-day、6次请求、lease FREE；不开服务、不发模型。

## 材料真实安装与回读

20:50:57主实例 `/root/nyairo-day-20261022` 执行 `day_reading_material.py install`，INSTALLED / exit0。新件：

- 标题：陶渊明《桃花源记》开篇，65字原文节选，公版、operator提供，非AI创作，非全文。
- 新artifact：`artifact:cf1428ff31ba4a59a2d20584e54dcbe9`；v1：`artifact-version:7a3a53063f1444a598b483dd24bbcb81`。
- 来源：[维基文库](https://zh.wikisource.org/zh-hans/%E6%A1%83%E8%8A%B1%E6%BA%90%E8%A8%98)。WebSearch核对了开篇；WebFetch网络受限，不宣称抓取全文。source URL仅在工具回执/报告，artifact只有作者标题与正文，没有来源列。
- 原合成件 `artifact:7d39dcca9d824c6583267b5141892199` 原文及v1保留，旧阅读凭据未改写。
- 手机字段、nested字段、nested环境三处均指向新件。两份instance文件写前有备份。
- install前后同run:one-day，上限96/96、请求账本6不变；自己Supply服务已停，lease FREE。

20:51:19 `verify`：VERIFIED / exit0，14项checks全true；真实owner回读新旧材料、绑定、版本、预算。20:55:33续跑后再verify同样全true，账本为10；20:55:39 status exit0、running_recorded=false、lease FREE。verify会短暂启动自己Supply读服务后停掉，writes=false指没有业务/实例/预算写入，不意味着没有服务日志。

## 同root/run第二段实际结果

只执行一次 `day_capability_probe.py run --root /root/nyairo-day-20261022 --duration-seconds 180 --max-new-requests 6`，没有重试/prepare/新run/注入唤醒。

- 时间20:51:53–20:55:02，本段上限180秒（包括收尾总墙钟约189秒）。READY / ok=true / shell exit0只证明有界运行正常收尾。
- 请求6→10，新增4次（认知3、读后回应1），未用满cap6；同run窗口，账本不重置。主实例累计10次；旧预检另1次，共同累计11/96，剩85次。本实例本身96上限不是给共同上限另加额度。
- wakes：ACTED1、BLOCKED2、NOT_DUE1；两次BLOCKED均 `DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED`。
- ticks_recorded=130是同实例累计（上一段66），不可说本段新增130个tick。
- 自有owner父PID3822，Supply3825/World3826，自起自停；lease FREE。

### 真实选择与作品落盘

`agency_decision_journal.jsonl` 20:52:10的 `DECISION_COMMITTED`：START / COGNITIVE_REQUIRED、`mrun:real:36abe70de3d87dfb`。动作 `actn:0c9c3982296cf484` SUCCEEDED / ACTED。

Supply只读回读私人事项artifact `artifact:00ee668a60014d8491fc0d1b62260ac1` 的v4（`artifact-version:4430a97f70b944e2a14e8513bdac44a4`）包含：

- 新材料source acquisition，source id/version均指向新公版节选，excerpt为65字原文，page1真实read receipt。
- reading_position=1，understanding=UNKNOWN，不能因有笔记就声称已证明理解。
- 模型生成memo，标题「这一页停在“欲穷其林”」，开头：「只有六十五个字，收在“欲穷其林”上——还没有进去，还没有遇见人，只有溪、林、落花和一个要走到尽头的念头。」

这些证明本段实际选读且读后记落入真实owner，不只是候选配置或fixture响应；不证明完整自主生活、长期记忆闭环或一天已过。

### 截断失败

20:52:19、20:52:32两次真实认知请求分别于20:52:32、20:52:46失败：安全诊断均 `PROVIDER_OUTPUT_TRUNCATED`、response_received=true；AG1失败事件各自有具名真实mrun。不是网络超时的已证据分类，不把模型失败抹成安静。

成功那次诊断记录 `max_output_tokens_sent=2048`、finish_reason=stop、content_chars199、reasoning_chars7760。失败诊断没有相同详细输出token字段，因此**不能据成功那次2048直接断言失败根因就是token限额**；需后续定向诊断，不在本轮擅改provider参数/重试策略。

最后next check window为2026-10-22 21:22:32 CST。这只是记录的下次检查时间，不是后台服务仍在运行，也不是已预约下一段。当前无继续跑授权动作执行中。

## 总纲验收边界

本工单不是总纲14章V-A～V-E的逐项完整复验，不能为未执行的项目补PASS；本轮不重开已结束的总纲核查。

- 同run真实短段、自有服务收尾、真实选读与owner读后记有证据，只限上述链，不升级成G1或整日生活。
- V-C原跨备份删除恢复FAIL保留；新旧材料未改写不证明跨备份恢复安全。
- 账本6→10、段cap<=6、同run不重置已验；不证明长期低预算接续。
- Memory UNBOUND、理解UNKNOWN、24h NOT_RUN_BY_USER_SCOPE，不宣布长期生活验收通过。

## 证据入口与未验范围

本机 `.agents/worker-runs/`：
- `20261022-day-segment1-before-reading-change.json`：原第一段报告保留。
- `20261022-day-reading-pre-status.json`、`20261022-day-reading-install.json`、`20261022-day-reading-pre-verify.json`。
- `20261022-day-reading-segment2.json`：第二段原始报告。
- `20261022-day-reading-post-verify.json`、`20261022-day-reading-post-status.json`。
- `20261022-day-reading-segment2-evidence.json`：已授权验收范围内从诊断/决策journal和owner库只读提取的新材料凭据与memo。

未跑全库、未开浏览器（本轮是验收脚本/实例数据，非UI改动）、未做24h、未改模型策略。不能称全绿；后续应先定向处理输出截断，再考虑下一段，不自动消耗剩余85次。
