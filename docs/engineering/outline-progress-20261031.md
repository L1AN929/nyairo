# 对照 v2.0 总纲的进度复核（2026-10-31）

本文只读复核：读了总纲 01/05/07/14/18 五章和仓库 `NOW.md`、`design-progress-20261030.md`、
`world-observe-e-result-20261031.md`、`current.json`，没有跑测试、起服务、调模型、访问 `/root` 实例。
设计文档版本是 2.0-final，软件实际是 v0.5.x 分支 `work/rc1-phone-world-body`，两个版本号不能互相换算。

## 一、一句话现状

她现在能在一次有界运行里连着做几件事：坐下来、读书、把批注存成作品的新版本、主动暂停再去碰另一件、
在街区里走动；遇到明确的拒绝、丢回执、版本冲突也能不重复写、不误报完成。但她还不能自己过完整的一天，
也还没有证据证明过去的经历真的改变了下一次选择；出门要靠默认关闭的 gate，记忆删除一旦连 controls 一起回滚，
正文仍会复活。一句话：**机制串得越来越长，自然发生的生活还很短，因果和治理都还没有过关。**

## 二、EP-01～EP-22 逐条

**EP-01 当前运行链与文档基线｜半做。** 有基线迁移记录（`rc1-phone-baseline-transfer.md`）、
`RUNBOOK.md`/`PROJECT_INDEX.md` 路由、`task_resume.py` 检查点和运行健康入口（`runtime-health-distinction-20261029.md`）；
缺一份把当前 host/version/loader/flags/state 钉在一起的现场事实表，旧记录和新结论还靠人读不一致。

**EP-02 规范与历史冲突协调｜半做。** `AGENTS.md`、`.agents/notes/` 和总纲 18 章的裁定在用；
新改动到总纲条目的映射没有系统维护，冲突靠每次人工比对。

**EP-03 真实 owner 与遗留 writer 边界｜半做。** Supply/AG1/AG2/AR0/LR4/LR5 有接线，
参与者登记与空闲写者边界有测试（`test_participant_registry.py`、`test_idle_writer.py`、`v02-b5-memory-admission.md`）；
完整端口图和「当前可达写面」没有找到成体系的清单。

**EP-04 观察、信念和来源生命周期｜半做（10-30 已从「没做」上调）。** 已有来源/状态/更正/过期/未知的主张聚合
和正式请求、工具读面（`design-progress-20261030.md` 的 EP04 节）；但没有 observed/reported/inferred/imagined
的完整信念生命周期，short_appraisal 的更正/过期投影在生产上也没有自然来源。

**EP-05 认知工作区与候选生产｜半做。** Supply/World/研究/实践/私有项目的候选、裁剪与 NO_ACTION 都有，
同回合取数成本、有效挂起供给、条件 PAUSE 已做（`test_parked_candidate_supply.py`、`test_wake_candidate_cost.py`）；
真模型采用新候选、以及「记忆→候选」的因果仍未证。

**EP-06 决定接纳与活动承接｜有真实链证据（限定）。** 真 owner 上跑通过 WAIT 释放前台、显式 RESUME 同 Activity、
PAUSE 释放前台与多件挂起单执行（`test_life_wait_resume_v04.py`、`test_paused_background_single_execution.py`、
`v04-wait-closeout-parent-acceptance.md`）；但决定多为脚本替身，真模型自然采用未证。

**EP-07 真实动作与 UNKNOWN｜半做。** 多段旅程每段过正式许可、明确拒绝与 UNKNOWN 分型、丢答后查询原操作
（`test_world_journey_authorization.py`、`test_world_dispatch_block.py`、`world-daily-owner-unknown-result.md`）；
部分子效果的结果向量、永久未决状态的完整覆盖仍缺。

**EP-08 后果结算与分域消费｜半做。** AR0/AR1、LR5、receipt 闭合、结果级交接身份、完成前冲突与迟到纠正都有
（`v05-progress-result-identity-20261025.md`、`test_activity_outcome.py`、`test_activity_late_correction.py`）；
消费者没有全覆盖，按动作类型编译必需后果的合同还没做。

**EP-09 记忆准入、回忆与删除｜半做，整项未过。** M0 唯一写口、绑定、运行期读取和真运行准入有证；
物理擦除、登记派生扣留、限定恢复入口已有交付（`test_f154_final_gaps.py`、`v05-restore-authority-options-20261026.md`）；
V-C 仍 FAIL：旧 evidence 和旧 controls 一起回滚时正文复活，另有一条 fsync 红未清。

**EP-10 World 对象与 Artifact｜有真实链证据（限定）。** 稳定 ID/版本/历史/对比/派生有真 Supply owner 加
Edge 155 点验（`test_v04_work_revisit_history.py`）；10-31 的 e 批次里真产品模型向四个不同 Artifact
各提交了新版本，回执全 OK（`world-observe-e-result-20261031.md`）；删除、投影导入和隐私删除的全链未验。

**EP-11 Body 与资源容量｜半做。** 六变量、elapsed 姿态积分、环境取样和 REST 有实现与测试
（`test_body_posture_elapsed.py`、`life-seed-body-capacity.md`），e 批次真实完成 seated/south 的 POSE；
24 小时以上的恢复、营养/饥饿生态未做，也不打算做强制吃喝睡打卡。

**EP-12 未来事项与承诺｜半做。** 落盘、到期、取消/改期、park→WAIT→做别事→显式 RESUME、到期重考都有
（`v05-due-reconsideration-20261026.md`、`v05-own-day-20261026.md`）；真实承诺和重新协商的合同没实现。

**EP-13 关系、共在与披露｜半做。** 本地来话、共同实践、草稿/延后/本地投递、可撤销本地披露（绑定来源和精确版本）
已有（`revocable-disclosure-contract-20261029.md`、`atomic-chat-boundary-20261029.md`）；
local receipt 不等于用户收到，外发保持关闭，完整关系和自然共处未证。

**EP-14 偏好、习惯和短期情绪｜半做，证据薄弱。** 现有材料集中在方法评价（那是 EP-15）和短期 appraisal 上下文
（`test_life_v1_appraisal_context.py`）；稳定偏好、习惯形成和条件性情绪本身没有独立的长链证据。

**EP-15 技法、练习和针对性自省｜半做。** 方法提案、真实结果引用、多情境评价/反例/修订/退休、后续可用性隔离
已经贯通（`v05-practice-continuation-20261025.md`、`v05-practice-counterexample-20261026.md`）；
长期稳定偏好、自然形成的方法和独立评估未证，评价用的仍是脚本替身。

**EP-16 房间、手机与作品体验｜有真实链证据（限定）。** 房间六区、手机笔记/收藏/钱包/购物/阅读/项目/研究/
共同实践都有入口，私人相册真实导入、保存、刷新和停服重启都点过（`v04-album-20261022-parent-acceptance.md`），
日记/相机/当天读面也有；通讯录、日历、消息、商店没做，contact 是本地来话不是通讯录。

**EP-17 唤醒、调度和低预算｜半做。** resident/runner、账本、静默分类、长跨度等待 0 模型轮询
（`waiting-no-model-poll-20261029.md`）、低预算断步后的诚实接续（`v05-low-budget-continuation-20261026.md`）
都有隔离证据；背压、公平性和低预算下的真实日常未证。

**EP-18 持久化、恢复和迁移｜半做。** CAS、crash/reconcile、两 PID 同根续同一个作品、显式未决恢复
（`test_v04_work_revisit_runtime.py`、`v05-restart-continuation-20261025.md`）通过；
删除撤权高水位、完整 crash 矩阵和模型/宿主迁移没做。

**EP-19 模型迁移与因果反证｜没做（实验未成）。** 历史文本干预 v1 只用了 3 次预留就采集停滞
（`history-choice-result-20261029.md`，stop_reason=ACQUISITION_STALLED_NO_REQUEST），没有进入比较；
v2 只有离线仪器，真实入口仍然关闭。

**EP-20 发行、普通账号体验和运行验收｜没做／本轮未复核。** 仓库有 v0.5.1 的版本号和清单提交，
但独立安装、配置、端到端启用和故障验收没有本次证据；`07-现状与证据边界.md` 里那批 PASS 是 10-03 的
历史 REPORTED 快照，不能当现行发行验收。

**EP-21 数字原生世界与探索｜没做（只有环境内容切片）。** 街区有早晚、阅读角有可读内容、有限街区事件包都有，
但收藏/游戏/异步项目/有限外部机会作为「探索」没有交付；这些内容目前只是 World 环境事实，不构成数字原生探索。

**EP-22 长期社会、fork 和供给｜没做。** 只有设计与储备；关系路线和 fork 意义在总纲 18 章里仍是 OPEN，
没有被实际需求迫选。

## 三、出口门

**G1（第一条真实私人生活闭环）未通过。** 五个场景分开看：
中断——真 owner 加脚本决定的隔离链已经通了，b 批次在真实产品模型上做过 operator 中断、P1 退出、P2 零请求重启
（`g1-limited-real-adoption-b-result-20261030.md`）；重启——两 PID 同根续同作在隔离环境验过
（`test_v04_work_revisit_runtime.py`），真实 G1 批次里的跨重启没覆盖；UNKNOWN——隔离链有 owner 写入、
回执丢失、保留 UNKNOWN、查询原操作不重写（`test_world_dispatch_block.py`），真实批次 UNKNOWN 注入 0 次；
版本冲突——正式 Decision 链的版本冲突隔离验过（`g1-limited-real-adoption-c-result-20261030.md`），真实批次
CAS 注入 0 次；NO_ACTION——b/c/e 都出现过，但 e 的四条流程同时 decision=null，不能算模型明确选择。
最后一项「下一次可以继续真实作品」最接近：e 里真模型向四件不同 Artifact 各提交了 v2 并存成 PAUSED/ACTIVE，
但没有一次 CONTINUE/RESUME/ABANDON 记录（`world-observe-e-result-20261031.md`）。
所以 G1 缺的不是机制，而是**一条同一作品、连续、由真实模型自己接下去的现场链**。

**G2（历史影响选择）没有证据。** v1 采集停滞，v2 只有离线控制器；「过去到候选」这段连接在真实运行里
没有被观察过。G2 另外三项里，私人项目不依赖用户回复、静默可区分故障、低预算诚实接续都只有隔离切片
（`waiting-no-model-poll-20261029.md`、`v05-low-budget-continuation-20261026.md`）。

**十二个必备场景：十个有隔离切片，两个只有机制，没有一个能无保留标成「真机完整跑通过」。**
有切片的是 1（作品写入并重启后续，历史因果未验）、2（原子动作期间的本地回复）、3（等待 0 模型轮询、
条件满足只产生资格）、4（回执丢失保持 UNKNOWN、查原操作）、6（动作成功但对象复核冲突不误完成）、
7（完成后迟到纠正留冲突、不静默重开）、8（读取后撤权、发送前拒绝）、9（删除/登记派生/限定恢复，
但旧双库回滚仍复活）、10（维护动作真改世界却不写成她的生活）、11（进程在不在、旧 running 残留、
AR0 待处理能区分，但没有可靠的死锁判据）。只有机制的是 5（子效果向量没实现）和 12（迁移整链没交）。
需要强调的是「隔离切片」指机制通、对照有、真 owner 或真 runtime 在跑，不等于自然发生；真实自然行为目前
见到的只有阅读并存批注、主动 PAUSE、换第二件到采纳层、真实坐下、单段和多段移动，它们没有拼成上述完整场景。

**与既有结论的出入，两边都写在这里：** `NOW.md` 10-22 那一节把 EP-04 记为「没做」，`design-progress-20261030.md`
已把它上调为半做并给了具名读面——本文按 10-30 的较新结论写，但注明上调的依据是「主张聚合与正式读面」，
不是完整信念生命周期。另外 `07-现状与证据边界.md` 的业务状态是 10-03 快照，里面多数条目本身标的就是
REPORTED（二手、未复跑），不能和 NOW 里 10 月底的现场结论互相替代，也不能拿它判当前代码没做。

## 四、下一步建议

**第一，补 G1 的最后一公里：一次有界、同一作品、由模型自己接续的真实批次。** 选它是因机制已经齐了，
缺的只是「同一条链上真模型自己 CONTINUE/RESUME」这一段的现场证据；再堆新机制边际收益很低。
需要本人批准产品预算和一次运行窗口（当前授权到 2026-11-01 01:00，但 e 已结束、不自动续跑），
并且开跑前把「成功/失败出口」和「不补第二次」写进预登记。

**第二，把「历史影响选择」做成一次真因果实验（G2 第一项）。** 选它是因为 G1 之后总纲唯一明确的下一道门
就是它，而现在 v2 只有离线仪器、真实入口关着；先修入口、再按预登记判据跑对照，不做「跑更久」。
需要本人批准新批次预算，并且明确历史文本 v1 的失败不算 G2 结论。

**第三，V-C 恢复权威需要本人直接裁定，不能靠工程侧顺手补。** 选它是因为它是唯一还压着 EP-09 升绿的红线：
旧 evidence 加旧 controls 一起回滚正文复活，属于恢复域的权威问题，不是测试问题。无论选「保留风险边界」、
「引入可信新 controls 恢复合同」还是「建立域外权威/fence」，都属于新增合同，必须本人拍板；
在此之前对外只能说「当前擦除和登记派生有界可用，跨备份防复活未过」。

其余方向（出门 MOVE 的默认 gate、手机补齐通讯录/日历/消息/商店、24h soak）本轮不建议排进下三步：
前两个是产品决定，24h 是最终 Exit 项、不是 G1 前置，且当前没有可靠的长时判据。
理解（understanding）仍是 UNKNOWN；模型/宿主迁移（场景 12）没有任何前端证据，本文不为它编结论。
