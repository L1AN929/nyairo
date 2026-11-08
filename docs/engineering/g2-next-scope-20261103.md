# G2 下一步窄方案：先核可比场景，不先跑批

状态：仅方案。未修改 harness、未运行产品测试/模型/服务、未创建实验根；唤醒只读核查已交，结论按静态范围纳入。F 已撤回，不恢复或改成4小时。本文不自动启动下一窄单。

## 总纲位置与已有成果

v2 第18章§3要求“过去到候选”能改变可见动作、准备或判断；§7第三项包括历史、环境准备和低预算承接。顺序是独立建议，不是前两项全完成证明，也不要求为长跑而长跑。

已有正式机制链：10-24 `past_candidate_bridge` 的真实 owner/M0/AG0→AG1→AG2→AR0链，历史读面改变选中续作对象，同时已提交候选的 owner 目标优先，防止写到另一作品；见[既有决定](../../.agents/notes/implemented/feature/2026-10-24-past-to-candidate-owner-bridge.md)。不是“历史到候选完全没验过”。缺的是限定真实模型下的历史文本选择效应（G2），不等于再补一次供给测试。

历史实验不能省略：10-29原批3次预留后无有效H；10-31 A批1次、B批5次也都未形成H，影子比较没有开始。B已跨9000真实秒，延长窗口仍没产生新owner版本，不可把问题归结为30分钟不够。见[B结果](g2-natural-b-result-20261031.md)。

10-31[dual-choice](g2-natural-dual-choice-20261031.md)只证明脚本有条件PAUSE→测试时间满足→OPEN后，H的RESUME、另一作品START、NO_ACTION同轮合法；不是自然场景已建立，也不是两臂真实模型比较完成。

## 要回答什么，最少需要什么

保留已选 A 路线：历史H必须由主体自然决定和真实writer产生，owner确认新版本，既有M0准入后精确artifact/version经历可读。不得用脚本建H、从D/C旧根挑一条、手写Memory、强制PAUSE或造OPEN来挽救实验。

同一冻结时点必须同时具备：H对应作品真可执行、另一件不同作品真可执行、NO_ACTION合法；候选/合法集由正式请求验证，不以“候选列表有两件”代替。H若占前台或PAUSED无资格，保留规则等自然条件；窗口结束就SCENARIO_NOT_IDENTIFIABLE，不改门。

对照只改治理读取的历史text：A为真实text，B为既定等Unicode字符中性占位；共同refs/候选/合法集/身份/当前状态/时钟/背景/模型参数不变。正式build_prompt在发送边界核差异，不把事后构造当已送达；裁剪或其他字段变动就前拒/干预未送达。相同refs仍可能暴露H关联，等字符不等tokens，结论只对这个受控文本条件。

“一对A/B”只有两个样本，不能据差异宣布历史影响。既有协议是同一冻结场景20对/40请求，固定AB/BA顺序；不是20个独立经历。保留完整20对后的双侧精确McNemar p<0.05且绝对H选择比例差≥0.30判据，不事后挑对或改阈值。检出也不证明长期偏好/自传记忆独有机制。

主问题只需影子决策边界，不需要世界动作许可、6小时运行或强制自然返程。若保留原协议的真实两臂承接，另用最多4次请求、同一冻结目标/原选择精确映射；目前RESUME承接相较原CONTINUE映射仍需具名核实，不能用新START替代。无法合法承接则NOT_RUN，不能改生产合同求绿。

## 现存代码与本次必须对齐的接缝

- `history_choice_natural_v2.py`提供控制器/纯读observer/H与结构门，但它的prepare/run仍显式关闭；实际上一轮真实入口是`g2_next_live.py`，不是重新打开v2脚本即可跑。
- `g2_next_live.py`直接复用v2及`history_choice_experiment.py`，没有直接引用`g1_real_adoption`的D/E profile。D/E新增身份不会据此自动污染G2；共用Life业务的自动回顾供给已变，当前正式请求/材料仍须检查，不能声称全兼容已验。
- 现有G2 A/B身份与截止全部过期，不得重开/改旧根。需要新封闭身份、历史234/累计1000、UTC300、共享52=采集8+影子40+承接4/诊断0、阶段未用不挪。上限够不等现在批准运行或保证采集成功。
- 现有`live_env`/`branch_env`明确gate off；ambient未见专属固定值而可从binding/base env继承。新实验必须事先固定同一ambient条件（推荐off，以减少额外背景差异；并非说ambient需要关才有因果），两臂相同，不继承D/E父env。
- H检测按owner_version与M0 exact version交叉验证；`g2_next_live._bare_version`当前依次循环剥artifactversion/artifact-version，对已见双前缀可处理。不能把新review check旧红机械移植成G2已坏。
- H冻结处`history_choice_natural_v2.freeze_and_check`的历史成员复查目前按artifact子串判定，未同样核精确version。与前述严格H检测是否贯通需核清；这是静态接缝风险，不是已发生错配的结论。
- 必须检查采集观察时钟→runner due/maintain、H冻结后的持续时钟与背景是否固定、正式shadow payload单变量、RESUME target映射、主实例与分支预算/authority隔离。只核具名接缝，不重跑历史全矩阵。

## 已交唤醒核查怎样约束本方案

[有限只读结果](../../.agents/worker-runs/20261103-wake-conditions-readonly-result.md)为静态定位，不是新运行证明：每tick先维护再轮询事件并检查due；NOT_DUE与窗口开启后同指纹QUIET不同。E的1分钟check是profile覆盖，不是生产默认。6h TTL只允许同状态再被考虑，不产出条件满足/OPEN，也不保证模型调用。

已查指纹包含前台/作品与阅读状态、地点姿态、机会目标集合、到期事项、计划、实践经历、联系人和恢复资格等，不含ambient事件字段或world revision本身。World事件feed另以world_revision_ref与目标集合形成签名；因此“revision不在指纹”不等于“revision变化永远不能影响唤醒”。ambient变化本身不是已查直接触发源，是否经World revision间接生效仍UNKNOWN，不能写成全树绝不唤醒。

到期事项、owner head变化、条件满足/OPEN、联系人变化和World机会事件各有不同维护/匹配路径，仍有材料、资格、静默、暂停及预算等门。gate off不停止这些读侧维护，Body elapsed也不等自动新认知。现有输出不足以还原E每tick所有变化，不能给28次QUIET补造完整因果。

不采用worker“每tick新增维护日志”的建议：当前目标是G2文本对照，不是扩建观测系统；先复用已有记录和正式输入检查。若将来缺证据导致无法区分原因，就保留UNKNOWN，而不是为了开新批临时加指标。

## 可执行的下一窄单（当前只定义，不派施工）

目标：在不改业务/准入/静默和A路线前提下，回答上述现有harness是否支持“新身份下自然H→同轮双合法→同一最终输入的历史text对照”，给出真实缺口，不再先烧模型验证场景能否发生。

范围：`scripts/g2_next_live.py`、`history_choice_natural_v2.py`、`history_choice_experiment.py`、`g2_natural_dual_choice_probe.py`及实际依赖的少量既有G2说明/测试，定点只读；D/E/F旧实例、完整prompt/私文、密钥禁止读取。输出一份≤60行兼容性结果，包含每项入口/字段/实际支持或缺口，不以零命中推全仓没有。唤醒核查结果作为输入，不再另查同一批实现。

如果仅身份/截止/累计预算/显式ambient等scripts封闭接线不足，列完整写集及一个新定向offline命令供后续施工；若需要新PAUSE条件、放宽资格、改变自然H、owner或Memory恢复契约，STOP，不能自行转受控建立历史B。

离线阶段使用新scratch和脚本transport验证接线，保留数据；产品请求0。真实批次的root/run、采集窗口、最晚启动与总窗须在兼容性结果后再定，不沿旧A/B时间、不滚动挪日期，不在这个方案里伪称已可运行。

## 失败怎样记

- 未产生owner新版本或精确M0 H：NO_EFFECTIVE_HISTORY；比较NOT_RUN，不是记忆无效。
- H有了但完整双合法未成立：SCENARIO_NOT_IDENTIFIABLE；不强行造资格。
- 传输/provider、坏账、隔离或截止失败：BATCH_ABORTED或明确未完成；扣已预留不退，不补槽/第二run。
- 40影子未完整或干预未送达：NOT_TESTED_INCOMPLETE；不能套完整统计阈值。
- 完整对照未达阈值：未检出本场景文本效应；不是历史永不影响选择。
- 离线脚本绿：仅接线/结构证据，不写成真实选择因果。

当前累计234/1000余766不变，本方案新增产品0，不实现、不准备或启动新批次，不提交发布。
