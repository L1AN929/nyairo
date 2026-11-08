# 对照 v2.0 总纲的现状复核（2026-11-02，只读）

- 现场：HEAD `563aa3d`，分支 `work/rc1-phone-world-body`，软件版本 v0.5.1。
- 方法：读 v2.0 的 00/01/02/04/05/07/12/14/18，v3.0 的 22 篇标题与前 30 行，仓库最近提交、NOW.md 顶部节点、`docs/engineering/` 既有结果，并核了引用到的测试/报告文件名。没有跑测试、服务、模型，没有改业务代码。
- 读法：下文「已做」只指有真实 owner/进程或真实 HTTP 证据的限定链；「半做」指机制或隔离链已通、但缺自然采用、完整故障面或整场景闭合；「没做」指没有实现链；「已排除」指用户已明确划出本轮范围。
- 一句话结论：项目比 NOW.md 底部「0.4」的旧描述走得远，已经到 v0.5.1；但 G1 仍未闭合，卡点不是「再跑一次」，而是缺一条 gate on、同一目标链、含故障项和真实模型采用证据的完整端到端。0.6 的方向（gate on + G1 闭合）是对的，顺序也该先机械链后自然批。

## 一、EP-01～22 现状表

| 编号 | v2.0 要求（一句） | 当前状态 | 证据 |
|---|---|---|---|
| EP-01 | 绑定当前宿主、版本、加载路径、flags 与状态 | 半做。有一条命令起实例、DAY_START 文档和发版现场记录，但没有持续维护的宿主事实表 | `docs/DAY_START.zh-CN.md`、`tests/test_nyairo_day_entry.py`、`docs/engineering/release-readiness-20261101.md` |
| EP-02 | 规范、ADR 与历史冲突的协调 | 半做。AGENTS/notes/NOW 在用，但 v2 EP 与新改动没有系统映射 | `AGENTS.md`、`.agents/notes/`、`docs/engineering/design-progress-20261030.md` |
| EP-03 | 真实 owner 与遗留 writer 的边界、端口图、可达写面 | 半做。有运行健康、参与者账本、文件系统安全，但完整端口图与遗留写面未收口 | `scripts/runtime_health_cli.py`、`tests/test_participant_registry.py`、`tests/test_filesystem_security.py` |
| EP-04 | 观察、信念、来源类型与纠正/过期生命周期 | 半做。有来源主张聚合、处境视图、短期评价读面；完整信念生命周期和自然采用未证 | `tests/test_life_situation_view.py`、`tests/test_short_appraisal_view.py`、`tests/test_infra_source_binding.py` |
| EP-05 | 有限材料、合法来源、候选生产、裁剪与 NO_ACTION | 半做（较强）。候选供给、成本优化、挂起供给、今日预留位与 NO_ACTION 都有链 | `tests/test_observation_candidate_supply.py`、`tests/test_parked_candidate_supply.py`、`docs/engineering/v05-own-day-20261026.md` |
| EP-06 | 决定接纳、活动承接、中断与 waiting/resume | 半做（较强）。START/CONTINUE/PAUSE/WAIT/RESUME、单前台、跨 PID 同作续接已通 | `tests/test_life_v1_integration.py`、`tests/test_life_v1_restart.py`、`docs/engineering/v05-restart-continuation-20261025.md` |
| EP-07 | 请求、尝试、幂等、查询、部分效果与永久未决 | 半做（较强）。attempt/receipt/UNKNOWN/reconcile 有链；部分子效果的结果向量没做 | `tests/test_life_unresolved_recovery.py`、`tests/test_decision_schema_refusal_diagnostics.py`；十二场景第 5 条 |
| EP-08 | 后果结算、分域消费、receipt 闭合 | 半做。AR0→AR1→LR5、结果级交接身份、来源核验已接；完整后果编译合同仍缺 | `tests/test_life_recovery_handoff.py`、`docs/engineering/v05-progress-result-identity-20261025.md` |
| EP-09 | 记忆准入、有限召回、删除屏障与恢复 | 半做，V-C 整体未过。M0 唯一写口、显式启用、有限召回、删除底账已有；整机回滚仍可复活 | `tests/test_memory_authority_recovery.py`、`docs/engineering/v05-vc-control-authority-plan-20261026.md`、`docs/engineering/v04-memory-vc-20261021-result.md` |
| EP-10 | World 对象、Artifact 版本、谱系、投影与隐私删除 | 半做（较强）。移动/拿放/购物/布置、版本与派生、街区早晚与阅读角内容已有 | `tests/test_world_daily_opportunities.py`、`tests/test_neighborhood_time_shops_content.py`、`docs/engineering/v04-work-revisit-result.md` |
| EP-11 | Body 负荷、姿态约束、恢复与容量 | 半做。姿态 elapsed、负荷/休息、饥渴如厕疲劳（v0.5）已有；无疾病/营养经济/24h | `tests/test_body_posture_elapsed.py`、`tests/test_body_lived_needs.py`、`tests/test_body_capacity_source.py` |
| EP-12 | 未来事项、计划、承诺与到期重考 | 半做。later_item、到期重考、取消改期、今日计划与 operator 义务已通；真实承诺协商未做 | `tests/test_v05_own_day.py`、`docs/engineering/v05-own-day-20261026.md`、`docs/engineering/v05-self-authored-future-audit-20261026.md` |
| EP-13 | 关系立场、共在、披露与对象隔离 | 半做。本地来话、共同实践、可撤销披露、参与者账本已有；外发仍关，完整关系未证 | `tests/test_atomic_chat_boundary.py`、`tests/test_participant_registry.py`、`docs/engineering/revocable-disclosure-contract-20261029.md` |
| EP-14 | 条件性偏好、习惯与短期情绪 | 半做。短期 appraisal 与处境上下文已有；稳定偏好和独立评价未证 | `tests/test_life_v1_appraisal_context.py`、`tests/test_short_appraisal_view.py` |
| EP-15 | 方法版本、适用性、独立评估与退休 | 半做。提案/试用/反例/修订/退休/再用已隔离贯通；自然形成长期技法未证 | `tests/test_loss_continuation_behavior.py`、`docs/engineering/v05-practice-counterexample-20261026.md` |
| EP-16 | 房间、手机与作品的端到端体验 | 半做（较强）。相册、日记、相机、今天页、项目/阅读/研究/实践入口已有；通讯录/日历/消息/商店未做，自主拍照未跑通 | `tests/test_phone_camera.py`、`tests/test_phone_day_timeline.py`、`docs/engineering/v04-album-20261022-parent-acceptance.md` |
| EP-17 | 唤醒、调度、公平、背压、静默与低预算 | 半做。resident/runner、QUIET 分类、运行健康、低预算接续有链；完整背压/公平未证 | `tests/test_day_life_quiet_runner.py`、`docs/engineering/v05-low-budget-continuation-20261026.md`、`docs/engineering/runtime-health-distinction-20261029.md` |
| EP-18 | crash/reconcile、升级、删除高水位与迁移 | 半做。CAS、同根/跨 PID 同作、交接恢复有证据；完整 crash 矩阵、迁移和撤权高水位未做 | `tests/test_life_v1_restart.py`、`tests/test_life_recovery_handoff.py`、`docs/engineering/v05-restart-continuation-20261025.md` |
| EP-19 | 模型迁移与因果反证 | 没做（实验受阻）。历史干预批没有形成有效历史，比较未开始；模型迁移未做 | `docs/engineering/history-choice-result-20261029.md`、`docs/engineering/g2-natural-a-result-20261031.md`、`docs/engineering/g2-natural-b-result-20261031.md` |
| EP-20 | 独立安装、配置、端到端、故障与启用证据 | 半做。v0.5.1 预发布、官网、隔离联网安装已验；普通用户/无 Python/升级回滚/卸载/24h 未验 | `docs/engineering/release-readiness-20261101.md`、`docs/engineering/20261101-release-offline-final-guard-result.md` |
| EP-21 | 数字原生世界与探索 | 没做。只有世界包与有限街区内容，没有收藏/游戏/异步外部机会 | 未找到实现报告；`docs/engineering/v06-isolated-gate-g1-proposal-20261102.md` 只把世界动作列入 0.6 |
| EP-22 | 长期社会、fork 与供给 | 已排除。设计保留，本轮不做 | v2 05/18 保留为 P5；NOW.md 记录用户 2026-10-22 拍了 v2 路线 |

补一句版本口径：NOW.md 底部「软件在 0.4」是 2026-10-22 的旧文本，当前以 git 和发行记录为准，已是 v0.5.1。EP 表沿用旧编号，不代表软件还停在 0.4。

## 二、十二个必备场景

| # | 场景 | 现状 | 未闭合边界 |
|---|---|---|---|
| 1 | 作品写入、重启后继续、历史影响下一次选择 | 隔离切片 + 真实短日片段 | 中断/重启/丢答/版本冲突后的同作续接有隔离证据；j/k/m 有过阅读、暂停、换事，但没串成完整链；「历史影响选择」是 G2 门项，未证 |
| 2 | 原子步骤期间普通聊天可回应、不非法 pause | 隔离切片 | 原子在途本地回复与单次投递有链；真实回复质量、完整 resident 在途链未验 |
| 3 | 长期等待 0 模型轮询；条件满足只发资格、不自动恢复 | 隔离切片 | 等待维护零模型、只发 OPEN、由新 Decision RESUME 已验；没有自然墙钟长期运行证明 |
| 4 | 外部 accepted 后 timeout，保持 UNKNOWN、查询原操作、不重复提交 | 隔离切片 | 真 owner 写后丢回执、跨进程查询原操作、不重复写已验；G1 f 批 UNKNOWN/CAS 注入为 0，其它 accepted/timeout 形状未覆盖 |
| 5 | 部分子效果确认、其余未知、进展不超出证据 | 只有机制，关键部分没做 | 分得清 Action/attempt/receipt/UNKNOWN，但没有完整「部分子效果结果向量」 |
| 6 | 动作成功但对象核对冲突，活动不被误完成 | 隔离切片 | 完成前对象冲突不误报完成已验；复核观测由夹具提供 |
| 7 | 完成后迟到纠正，记录冲突，不 silent reopen | 隔离切片 | 已有完成合同下的迟到纠正已验；其它完成形状未全覆盖 |
| 8 | 读取后撤权、派发前再检查、私人内容不外发 | 隔离切片 | 许可绑定来源/版本、发送前拒绝、普通读取不受影响已验；外发仍关，撤权是注入不是真实治理撤权 |
| 9 | 删除原始证据、重建索引/恢复备份，旧内容不复活 | 隔离切片，整项未过 | 当前正文擦除、已登记派生扣留、限定恢复有链；旧双库整体回滚仍复活，域外权威未部署 |
| 10 | 测试/维护动作真实改变 World，但不被当成她的个人生活 | 隔离切片 | 维护动作真实改身体/世界、伪造来源被拒已验；排除规则只覆盖已查入口 |
| 11 | 安静日、死进程、卡队列可被技术观测区分，不补剧情 | 隔离切片 | 能看本机匹配进程、旧 running 记录、AR0 待处理；没有「多久没动就是死锁」的可靠判据 |
| 12 | 模型/宿主迁移后作品、责任、身份、技法可交代，损失保留 | 只有机制，整项未交付 | 有作品/身份/方法/恢复读面，没有迁移后的责任、适用性与损失清单；G4 未开始 |

十二场景没有一项能无保留标成「真机完整跑通」。真实自然行为确实见过：阅读并存批注、主动 PAUSE 释放、换第二件到采纳层、真实坐下完成、单段和多段移动；但这些没有和上述完整故障条件组合成一条链。

## 三、四个出口门

- **G1**：卡在「同一条目标链的完整端到端 + 限定真实模型采用」。已有分散证据：中断、两 PID 重启、UNKNOWN 原操作对账、正式版本冲突、NO_ACTION 后续同作，都有隔离或真 owner 链。但 f 批只做到「跨 PID 同作品续写」的限定覆盖，UNKNOWN、真实版本冲突、明确 NO_ACTION 的后续作不在同一批里。gate 默认仍是 off（`components/world/service/action_intent.py:431` 读环境变量，缺省 `off`），e 批自然运行里 MOVE 在候选和最终请求中出现了，但 5 次合法 START 都选了坐下或私人机会，没有移动。缺的不是再跑一次，而是：gate on 的正式出门/返程机制链（A），加一条把中断、重启、UNKNOWN、版本冲突、NO_ACTION 串起来的完整 G1 工单。
- **G2**：卡在「没有形成有效历史 H」。history-choice v1 用了 3 个预留就停；g2-natural a 只有 1 次决定；b 批跑了 9003 秒、5 个预留，但运行期没有新增 owner 版本，H 为空，影子和承接都是 null。所以不是「比较了没差异」，是「还没有可比的材料」，比较本身没开始。
- **V-C**：卡在「域外控制权威」。删除决定已经能先写业务目录外的底账，再应用本地 controls；同机换目录、下限交接、底账不可得时阻断读取都做了。但整机回滚、底账与业务库一起回滚、未知派生面仍不在保证内。缺的是独立部署的控制权威和可信「够新」证明，不是再补一个测试。
- **24h**：没做。目前最长的真实运行是 m 的 7200 秒压缩日（86400 逻辑秒）和若干 1800 秒短日；24h soak 是最终 Exit 的项目，不是 G1 的前置，别把它塞进 0.6。

## 四、v3.0 的 22 篇怎么放

v3.0 目录里 01/02/03 各有「短篇/总纲」和「完整设计」两版，按 22 个主题看。结论先给：**没有一篇需要现在整篇落地**；真正和 v2 路线冲突的是 18/19 两篇「0.4 最小切片」路线，而这条路线用户在 2026-10-22 已经明确否掉，继续走 v2 的 EP 线。

### 与 v2 重复或已有对应

- **01 非数值关系与羁绊沉淀**：v2 的 S11 和 ADR-016 已经禁掉总亲密分，v3 只是把「分领域信任账本、锚点物件、rupture 修复」写细，方向和 v2 一致。
- **07 宏观驱动引擎与事件循环**：对应 EP-17 的唤醒、合并、背压、QUIET vs STALLED、预算断路器。QUIET 分类、运行健康、低预算接续已经做了；剩下的 L0/L1 分层和 WakeTicket 是加深，不是新领域。
- **09 现实桥梁认知边界**：对应 EP-13 的 sent≠delivered≠read≠understood，现有 local receipt 边界已经在做，v3 是更细的五级状态机。
- **20/21 无意义权利与生活毛刺**：v2 第 16 章「普通日子没有最低配额」已经给了合法性；v3 是产品表达上的强化，不是新机制。

### 新增，但可以延后

- **02 慢变量账本与世界生态流**：v2 只有有限世界包和事件；v3 要慢变量账本、因果边、阈值显形。方向可接，但属于 P5 世界生态，不是 0.6。
- **03 工程契约与代码落地（DWELL 等）**：DWELL 是「发呆不产出」的具体落地，v2 的原则已经有了。可以做小切口，但不服务 G1。
- **04 金钱重量与自立消费**：现有虚拟钱包和购买只到「花钱」，v3 要收入、渴望潮汐、清贫日子。新增，延后。
- **05 身体负荷与生病自愈**：v2 只到负荷/恢复，v0.5 刚加了饥渴如厕疲劳；疾病慢变量、14 天病程是下一层。延后，但要和现有 `body_lived_needs` 对齐。
- **06 三时钟体系与自转动力学**：现有 wall clock、业务 clock、Asia/Tokyo 日界并存，v3 要做统一三时钟。这里有一小块和 G1/G2 有关（日界统一、静默怎么算），可以在 G2 前做最小统一，但整篇不急。
- **08 主观时间感与主体自立哲学**：哲学层，延后。
- **12 双人书桌共处与 B7**：ADR-036 的「共在从真实协调开始」v2 已有，现有 contact/joint practice 是窄版；书桌共在是新增，延后。
- **13 餐桌厨房与饮食消耗**：新增，和 05/身体需求衔接，延后。
- **14 共在状态机与消耗品物理接口**：新增，延后。
- **22 架构功利主义反思与蒸发通道**：这是对「一切都要回执、因果、解释」的架构批判。它和当前 G1/V-C 的重证据有张力，但 v2 也允许 NO_CHANGE、不要求每段经历都入记忆。现在不改，留着当防过度制度化的提醒。

### 现在不做，或需要产品裁定

- **10 现实触点生态与反骚扰**、**11 现实网关架构与安全沙盒**：都是「向现实世界出站」。当前边界是外发保持关闭，v2 也没有批准主动外发。这是范围未授权，不是技术延后，必须先由本人决定产品承诺。
- **15 亲密心理学与防御瓦解**、**16 用户具身形态与亲密生活物理学**、**17 亲密接触状态机与双手互锁**：产品哲学和关系承诺问题，v2 ADR-016/017 仍标 OPEN。需要本人裁定关系路线，不能由工程默认。
- **18 0.4 最小生活切片 MVP**：**与 v2 路线直接冲突**。它把 0.4 收成「一个房间 + 一条老街 + 一间旧书店 + 一个同桌」，而用户在 2026-10-22 已拍板继续 v2 的 EP-01～22，不切 18 号。这条已排除，不再作为任务来源。
- **19 0.4 代码矛盾清理与最小写集**：同样属于被否掉的 0.4 路线，整篇不采用。但它点名的几个缺陷值得单独核，不按它的写集施工：三个日界冲突（UTC/本地/Asia-Tokyo）、处境指纹不含身体与对方在场、定时器别名伪装。这些如果当前代码仍在，会直接影响「出门、守时、安静」的自然行为；本轮没有核当前是否仍存在，建议放进 0.6 的顺手检查，而不是按 v3 改造。

## 五、下一步三个建议（按顺序）

### 1. 先批准并做 A：零模型隔离世界往返（gate off 对照 + gate on 出门/返程）

做什么：按 `v06-isolated-gate-g1-proposal-20261102.md` 的 A 块，建一个新隔离根，脚本明确选择 MOVE，从现场 owner 读合法路径，正式 Decision → 逐动作 gateway → 出门 → 新 Decision 返程。先用另一新根做 gate off 负控，确认明确拒绝、零新副作用；再用 gate on 根验正式出发、在途、到达、返程。600 真实秒上限，零产品请求。

为什么现在做：gate 默认是 off，e 批在临时 canonical 下虽然供给和最终请求里都有 MOVE，但 5 次合法 START 全没选移动。也就是说，现在连「门打开后，正式票据和返程到底接不接得上」都没有机械证据。A 是 0.6 里最小、最便宜、最能定位问题的一步，不烧模型预算。

谁拍板：本人。v06 明确写了方案待确认，且建议先只批 A；B 的预算和时间另批。

### 2. A 通过后，批准唯一一次 B：真实模型自然短日（gate on，不强制回家）

做什么：新 root/run/authority，gate on，硬 cap 20、1800 真实秒、失败不退、不续跑、不第二次抽样。不加「必须回家」提示，不造保底任务。没选出门、没到达、没回来都记 NOT_COVERED。

为什么现在做：只有真实模型能回答「她会不会自己选择出门再回来」。但必须在 A 证明了机械链之后，否则 B 的结果无法归因——分不清是票/返程没接上，还是她不想动。当前历史保守 191/300，最坏 211/300，还有余量，但这是建议不是现成额度。

谁拍板：本人。需要事前定死预算、时间窗和最新启动线，不能跑完再追加。

### 3. 单独圈一条完整 G1 工单，把故障项串成同一条链

做什么：用同一目标链覆盖中断、跨 PID 重启、UNKNOWN 原操作对账、真实版本冲突、明确 NO_ACTION 后的继续真实作品。脚本故障干预和真实模型行为分层记录，不把分散的 f/c/m 证据拼成 G1。

为什么现在做：0.6 的定位是「gate on + G1 闭合」，A/B 只解决世界动作，不解决 G1。现有 G1 证据是分散的：f 批有跨 PID 同作品续写但 UNKNOWN/CAS 为 0；c 批有版本冲突；m 批有自然阅读和 PAUSE 但没有UNKNOWN对账。缺的是一条把这些条件放进同一目标链的工单。

谁拍板：本人和监工一起定场景边界与预算。建议排在 A/B 之后，避免把世界验证和全部治理重造搅在一起。

### 不建议现在做的

- 不按 v3 的 18/19 号切 0.4 路线。
- 不把 gate on 改成产品默认；它只应是实验进程树的运行参数。
- 不跑 24h soak 来「证明 G1」，24h 是 Exit 项，不是 G1 前置。
- 不在 V-C 没有域外权威之前宣称删除/恢复已闭合。

## 本次的边界

- 没有跑任何测试、服务、模型，也没有访问旧实例 root。所有「已验」都来自既有报告和测试文件名，不是本轮重新执行。
- v3.0 只读了各篇标题和前 30 行，没有逐篇精读；判断的是主题和路线，不是逐条技术细节。
- v2.0 的 15/16/17 三份完整设计只通过 05/14/18 的引用和标题定位，没有通读全文；需要具体条文时另开只读核查。
- 第 19 号点名的日界、处境指纹、定时器别名三个缺陷，本轮没有核当前代码是否仍存在，只作为 0.6 的待查项。
