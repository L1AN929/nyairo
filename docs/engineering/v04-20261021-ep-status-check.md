# 2026-10-21 第〇步 EP 状态核对

这次是按 NOW 第〇步做的分组只读核对，不是全库审计，也不是新一轮测试通过声明。四组均经 scripts/dispatch_worker.py，模型 workbuddy-wg/cn:deepseek-v4.1-flash；父级只整理和核对具名分歧，无业务改动。

## 执行与证据

A/B/C 首轮均 exit1（Reached max turns 55），没有终稿，不算完成；D 首轮 exit0。A/B/C 各补一次最多15次读取的有界任务，均 exit0。原文在忽略目录 .agents/worker-runs/：

- A：20261021-224540-audit-a-bounded.log，EP-01～03/05～08。
- B：20261021-224558-audit-b-bounded.log，EP-09～12。
- C：20261021-224529-audit-c-bounded.log，EP-13～18。
- D：20261021-223634-audit-d.log，EP-04/19～22 与 G1/G2。

任务要求只读。--allowedTools 是审批白名单，不是工具能力隔离；首轮 B 轨迹实际使用过 Bash，不声称由工具强制实现只读隔离。本次 git 观察未发现业务文件变动。memsearch 插件自行更新的 dirty 保留，不由本轮接管。

## 核对结论

- EP-01/02/03/05/07/08：半做。真实加载路径/flags 现场表和完整 writer 端口图未找到；候选、动作/UNKNOWN、结算已有局部真 owner 证据，不代表所有故障面或真模型采用已证。A 引用旧 T20 的 GOVERNANCE_REFUSED→UNKNOWN 是历史报告，不作为本次现树故障认证。核心参考 docs/engineering/v04-wait-closeout-parent-acceptance.md:19-33、v04-day-life-legality-result.md、v04-work-revisit-result.md；测试 test_day_life_decision_legality.py、test_v04_work_revisit_history.py::test_commit_keeps_cas_and_a_lost_answer_creates_no_second_version。
- EP-06：有限真实 owner 链。WAIT 释放前台、明确 RESUME 同 Activity、archive→AR1→LR5 完成有记录；该验收链的决定来自脚本替身，不能说真模型自主生活。参考 v04-wait-closeout-parent-acceptance.md:19-29。共同实践续接见 tests/test_v04_shared_practice_life_chain.py::test_read_invite_three_real_steps_then_end_and_resume_page_two（非本次复跑）。
- EP-09：半做。tests/test_v04_memory_product.py:390/403 的 test_a_deleted_experience_is_not_recallable_any_more / test_the_deleted_row_does_not_lend_its_text_to_a_neighbour 使用隔离真实 M0/controls，覆盖当前 read/list/context 不泄正文；不等于旧业务库/controls/索引/摘要恢复防复活。原实例仍 UNBOUND。B 对“旧 controls 恢复会复活”的表述仅代码推断，本次没有恢复实验，不作为已实测故障。
- EP-10：历史/对比/派生有真实 Supply owner + loopback HTTP 测试；手机 app.js 已接，旧报告“未接”是旧快照。tests/test_v04_work_revisit_history.py:345/402/418/609/696/835 分别覆盖版本清单、tombstone、HTTP纯读、丢答对账、来源扣留、服务重建幂等。移动/购物面只沿用报告，未全量复验。
- EP-11：半做。v04-design-progress.md S02 记录六变量、灯具暴露、姿态和 REST 机制；本次未读全 Body/未跑长期，不能推成24h或强制吃睡方案。
- EP-12：有界初版有真实链证据。B 首次按 *later* 文件名找不到不构成降档理由；父级具名补核 tests/test_v04_autonomous_day_stage.py:83/109/119（落盘、取消、改期）、:753 test_the_stage_chain_parks_the_running_activity_and_comes_back_by_explicit_resume（真 owner WAIT/RESUME）；v04-autonomous-projects-parent-result.md 与 v04-design-progress.md:71-73 是历史结果。
- EP-13：半做，本地来话/披露/共同实践存在，local_inbox 不是外发；自然共同生活未证。tests/test_v04_shared_practice_life_chain.py:512/578/645 有具名链；真模型旧 INVALID_SCHEMA 与后续合法 CONTINUE 都不能升级为完整自主共作。
- EP-14/15：半做。做法生命周期与实际 prompt 可达；tests/test_v04_practice_life_stage.py::test_practice_context_rides_into_the_prompt_beside_research_context 等。稳定偏好、跨情境技法、独立评估未证。
- EP-16：半做。app.js:4803-4861/4962-5014/7426-7441 已接旧作重访。相册/通讯录/日历/消息/商店在所核界面与路由未找到；contact 是本地来话不是通讯录。真实 owner 的浏览器链未认证。
- EP-17：半做。quiet runner 与中断边界合并有局部证据；tests/test_day_life_quiet_runner.py::test_a_quiet_round_is_silence_and_does_not_count_as_a_wake / test_repeated_not_due_polls_do_not_fill_the_wake_journal。不能扩大为调度层 backpressure、公平性、低预算诚实接续全通过。
- EP-18：半做。一般 Life 两进程同根重启已有历史绿（v04-day-life-parent-acceptance.md:19/38），旧作重访目前只证 PhoneService 重建，不等于实际 Life runtime 重启。
- EP-04/19～22：本轮不施工。EP-04 缺一般来源/信念生命周期，但 short_appraisal_view.py 已有更正/过期失效投影，生产 appraisal_source=None；EP-20 限定0.4未发行，不能抹掉0.3发行和独立安装历史。

## 旧作重访写作证据的准确层次

父级补核 tests/test_v04_work_revisit_writer_chain.py:69-151：真 Supply owner→continuation_excerpt→真实 ModelContinuationWriter→RecordingClient 请求，手动传 context；:213-253 是 _next_content 接线测试，但 project service 是替身。两段有意义，但尚不是完整候选→AG1→AG2→AR0→真 owner 历史→writer 的一条运行链。本次未重跑，不把“21绿”扩大成自然模型采用。

## G1/G2 与本轮范围

设计05:33-34要求完整端到端含中断、重启、UNKNOWN、冲突、NO_ACTION，EL3+限定EL4。五项目前有分散局部证据，未找到完整一条链和限定真实模型采用证据，不能写“G1只差同根重启”。Edge 是 EP-16 产品验收，不是 G1 指定前置。G2 的历史干预影响选择、低预算接续仍未证；私人项目/静默有机制链但不能宣称EL4。

本轮不据此加开 G1/EP-19；保留 NOW 第五节1→2→3顺序。第4条按用户最新指令排除。若后续发现需要改控制契约/回滚域外权威才能补 V-C，先问；手机具体选项仍由用户决定。

## 本次实际验证

只读核对没有新增产品模型请求、没有启动新产品服务、没有运行旧绿业务套件。父级实跑现有笔记 tree/format 各81篇 exit0，archive 0篇 exit0；WSL Python3.12.3/Node24 可用，/tmp/rc104-venv已有测试依赖。不是产品验收通过声明。
