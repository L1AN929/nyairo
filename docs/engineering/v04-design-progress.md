# NyAiro 0.4：对照总体设计的实际进度

## 基准与读法

设计标准为 `D:\CHIYO-总体设计-v2.0-final\CHIYO-总体设计-v2-最终版`（此前来源ZIP同名），软件仍在开发 **0.4**；设计2.0不等于软件2.0。唯一开发树 `D:\nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`，底座 `41e818faaa24115fbf43db2977b15585c04144af`。0.4改动为本地未提交开发，不代表发布或完整生产验收。

本次父级重读00/01/02/04/05/06/07/18/19的相关内容，国内WorkBuddy DeepSeek分路精读15自主记忆、16生活生态及其绑定来源并核对真实代码。00/01/15/16/18/19这六份解压文件与ZIP对应成员直接字节比较一致；不是重新计算全树SHA，也不是宣称重新语义读完全部附件。

标记区分：**已验链条**（有此前真实owner/进程证据）、**已有底座/部分接线**（不能说全做完）、**未在当前版本交付**、**研究/后续条件**。不将功能清单数量换算成完成百分比，不将压缩包2026-10-03历史状态当今天代码事实。

## 已实现与尚缺

| 设计方向 | 当前实际成果 | 仍未完成或证据边界 |
|---|---|---|
| S01房间/物件/产权 | 已验移动、手持、放置、姿态；真实虚拟购买→产权→送达/实体化→家具布置，沿用World/economy owner | 非完整开放世界；OWNED不等于送达/可达；没有NPC社会、全面物理模拟 |
| S02身体与环境 | 六项既有Body变量，真实行动时间/后果；灯具位置/电源来源支持光输入，已验65秒实际暴露 | 不含营养/饥饿/强制吃睡；非24小时证据，REST正向只到隔离机制证据 |
| 私人手机 | 真实笔记/收藏/钱包/购物/World/阅读/项目/资料研究，加经历检索、显式Memory启用及回顾/做法/试用/修订/退休工作台 | 相册/联系人/日历/消息/应用商店未完整交付；历史暂仅当前head；新实践UI验证为HTTP/纯函数，非浏览器交互 |
| 真实材料与阅读 | 当前文本/分页/版本、真实取得、显式停点、锚定笔记疑问；新增来源声明文本导入检索，研究引用CURRENT/STALE/UNAVAILABLE | 不联网抓取；origin不是独立认证；收藏不等于取得，停点不等于理解；历史正文任意版本读取未交付；understanding仍UNKNOWN |
| 主体阅读续接 | Life候选读取当前版本/PDI新鲜性；阅读前台CONTINUE不误写作品；同实例跨进程读1→2→3，读尽零写 | 选择驱动为明确测试替身，不是真模型自主选择；读尽不自动等于Activity完成 |
| 私人作品与读后实践 | 多项目创建/选择/续写/归档，疑问与真实材料形成研究选项；真实Supply/AG1/AG2/AR0研究创建续写链通过 | 动作选择为测试替身；Gemini真实合法NO_ACTION是上阶段证据，本轮未证自主研究/理解 |
| 历史到后来 | 已有经历消费/召回/continuity；新增实例Memory显式启用、相关旧经历检索、经历→回顾→做法提案→后续实际prompt/后果引用→有证据评价，真实owner链通过 | 选择与内容为脚本替身；原实例未启用Memory；非稳定偏好/认知成长已证，文档不自动晋升信念 |
| S08未来组织 | 稍后事项真实保存/到期重考/取消/改期；明确park_current经WAIT释放前台，期间实际做别事，到期明确RESUME原Activity；两条真实owner阶段链通过 | 初版有界稍后事项，不是完整承诺/日历系统；通用PAUSE语义不变，实际采用显式时间WAIT |
| Activity/Action治理 | 当前单前台、真实owner端口、pause/lease/预算、幂等/UNKNOWN/reconcile底座 | 保存Decision不是重放权限；action成功不等于活动完成；未声明全部故障/撤权/删除链均本轮复验 |
| 偏好/关系/个人技法 | 新增有范围暂定做法、试用/反例/修订/退休及版本保留，做法参考进入决策与写作 | 不是已验证可执行Skill；不宣称跨情境偏好/关系/成熟个人技法，不设全局成长分/人格总库 |
| 多模态/迁居/社会 | 保留设计路线 | 当前0.4未交付完整感知相册、声音/动作表现、多载体迁居/fork/社会身份；研究不因未实现而删除 |

主要依据：设计01 §3/§5/§8、02责任地图、05 P1/P2、16生活生态、18 §3/§7、19总约束；当前具体运行事实见下列父级报告，而不是本表重新签发所有旧验收。

- [手机底座迁入rc1](rc1-phone-baseline-transfer.md)
- [World/Body](rc1-world-body-parent-acceptance.md)
- [购物、房间与灯](rc1-room-parent-acceptance.md)
- [阅读工作台](rc1-reading-parent-acceptance.md)
- [主体私人材料续接](rc1-subject-materials-parent-acceptance.md)

## 上一批：私人读写实践

本批已完成并通过限定验收，见[父级验收](v04-private-practice-parent-acceptance.md)。国内DeepSeek实现A/B/P，国内池503中断后国际WorkBuddy DeepSeek接续B/P定点收口。取得/停点/可选批注合成同一PDI版本，不建平行本子/记忆库，不增加用户控制页面；作品合法no_change、记忆参与只读预检均落地。父级独立两个进程同实例读三页，12项检查通过。仍为本地0.4未提交开发，不是生产自主运行。

专题核查确认：当前有M0唯一写口/MemoryControl/AG2经历消费者、有限召回与continuity、LR4 Agenda/Waiting/ResumeEligibility；不得报成缺模块。当前接受实例未绑定Memory，前轮机制链明确没有Memory消费者，不能从阅读续接推导记忆参与。新的preflight只使这项状态明确，不擅自绑定数据库。

后续缺口分层：近期可接“具体私人作品/读后整理、明确未来安排、过去到候选的可追溯连接”；中期为有界求知、自省/方法版本、偏好及关系反例验证；远期为多模态、迁居/fork与有限社会。它们都有设计资产，未因这批暂缓而删除。另发现Commitment候选源当前组装READY与空canonical供给口径需专包核实，本轮不越写集修改。

两份DeepSeek精读报告留在会话scratch：`v04-gap-autonomy-memory.md`、`v04-gap-life-tools.md`。父级不照搬其中过强判断：没有专门memory候选源不等于过去不能影响现有候选选择；已有ManagedArtifact作品也不能说主体完全没有可续写对象。缺口应描述为具体接线或产品生命周期缺失，而非用grep零命中证明整个领域不存在。

## 上一批：一天生活连续性

用户再次明确“正常活一天，目前仍达不到”。本批按 `D:\NYAIRO-v0.4-一天生活连续性-施工单.md` 修复合法决策与owner守卫不一致、读尽/无步骤仍继续、同状态重复询问、在线覆盖混入停机和Life姿态/休息接线。**父级有限真实owner两进程链已通过，但不是正常自主一天的证明。** 见[本批父级回执](v04-day-life-parent-acceptance.md)：真实读页→明确退出→POSE→作品no_change→保留一次退出考虑→新进程同状态QUIET且零请求，轮数6→6→6，历史保留。选择与内容驱动明确为测试替身。

已查明PAUSE保留foreground引用；它不是可任意切换的释放操作。有限链用明确ABANDON释放前台、保留材料/作品/PDI历史，不通过清指针或新Activity绕过owner。旧跨进程重启负控后续已由DeepSeek按现行AG1矩阵修复，唯一受影响ID单跑18.084s、exit0，证据 `scratch/day-life-final-restart-gen2.log`；原红日志保留。真实Life模型选择与24小时运行仍缺证。

## 历史运行与发布边界（最新结果见文末连续生活阶段）

当前原实例模型已按用户选择切为 `gemini-3.8-flash-high`，URL仍 `https://gw.929711.xyz/v1`，私有model_env保持0600且已备份。Gemini真实Life新增1请求，合法NO_ACTION，原run账本2→3；DeepSeek此前的INVALID_JSON/OUTPUT_TRUNCATED保留为历史，不再列为当前模型阻塞。最终补线后一次tick为QUIET/0新请求，不能说真实模型已自主使用新功能。Memory仍UNBOUND可选降级。

本轮允许较大批本地开发；不自动提交/push/tag/打包/改官网/部署。现有钱包2860虚拟JPY、四购买、房间物件、作品、材料/PDI与Activity历史均保留。必要新功能用隔离夹具及有限真实owner链验证，不用旧备份回滚已发生历史。

## 上一批：等待恢复、作品收尾、统一覆盖报告

上一批C/W/R及父级两进程链已通过，见[父级回执](v04-wait-closeout-parent-acceptance.md)，不在当前重复qualification。其末期DeepSeek模型截断现已通过用户指定Gemini换模解开合法选择路径；完整自主生活仍需继续发展。

最新只读精读报告为会话scratch `v04-day-life-normal-day-audit.md`（00/01/05/16/18/19）和 `explorer-memory-closeout-report.md`（01/02/15/16/18/19）。它们是施工前快照，不是当前完成证据；其中“WAIT零调用”“改历史MANIFEST”“整个Memory缺失”等过强建议不采纳：WAIT仍花明确决策请求、release清单本地不改、Memory底座已有而因果桥/实例参与有限。

阶段边界：当前已交付具体私人项目、稍后事项、改期取消、合法park换事与接续的可运行初版；不是全ZIP完成。Memory过去→新候选因果桥、稳定偏好/关系/个人技法、多模态/迁居/社会仍待后续。真实整日生活与长时Body后果未验证，不用短链冒充。

R核实纠正：精读审计把runner合并的 `autonomous_wakes.jsonl` 与resident每tick的 `resident_journal.jsonl` 混同，“安静夜必然被算停机”过强。实际已修缺陷是墙钟跨度/长tick间隔不能签发在线覆盖，证据见 `v04-life-report-coverage-result.md`。只凭测量可得报告PASS，`soak_verified`仍NOT_VERIFIED；不是现实自主一天证据。

原实例所有本轮有限服务均已停止，钱包2860JPY/4交易保留。最新Gemini合法选择证据 `scratch/original-life-bounded-1792394831392955957.json`，最终代码QUIET证据 `scratch/original-life-bounded-1792401397092131418.json`；模型回滚仅覆盖model_env备份，不回滚业务历史。

## 上一批：自主日常与私人多项目初版

按用户“先做完整阶段、再沿实际问题修”的要求，两位DeepSeek直接实现主循环/未来事项与项目owner/手机工作台，不再另派精读或审计队。初版主循环曾漏模型提示与park调用，已同一writer补通：模型现在可见有界处境/候选语义、新字段和已有稍后事项；真实AG1/AG2/LR4/Supply测试验证park→做另一项目→明确回原项目，以及到期选项→B创建→续写第二作品。选择/内容在该链为脚本替身。

主循环18项（含2条真实owner链）、B22项Python+6项Node通过；父级真实项目创建两份/修改/归档通过。最终notes tree/format各60篇及diff-check exit0。无需重跑旧全套/SHA巡检。完整结果见[阶段回执](v04-autonomous-projects-parent-result.md)；项目历史仅head、真实模型主动采用新功能尚未实测，作为初版限制，不阻塞本阶段交付。

## 上一批：自主求知与经验回用初版

国内DeepSeek两路直接实现资料/研究工作台与手机，以及自主循环/写作上下文，不另派读设计或审计队。疑问→真实材料→明确选择研究→owner建件/AR0续写→后来参考研究正文已通。新测试A17项（含3条真实owner链）、B31项Python+9项Node通过；父级独立真实资料导入/研究编辑/CAS/来源换版/归档六项检查通过，旧作品/阅读/PDI头未变。

父级发现全文头截断只带回机器标记、漏掉真实发现，已同A writer按增强断言先红后绿修为正文头尾摘要；现在后续决策和普通作品写作明确含实际新写入的发现。见[阶段父级结果](v04-research-parent-result.md)。本轮未调用Gemini/启动原实例，不声称自然自主研究或24小时完成；本地资料内求知，不联网，不升级Memory/稳定信念。子代理遇到的3条其他当前测试失败如实留在结果中，本轮未修也未证明其历史来源。

## 上一批：跨日记忆、自省与个人实践初版

国内DeepSeek三路完成Memory产品化、实践工作台/手机和自主主链：显式为实例启用Memory（备份、不启动、不重置run预算）；治理后检索经历；明确选择回顾并真实续写；提交有范围做法；后续参考具体指令并关联后果；试用评价、反例、修订和退休。全部沿用M0/Supply，没有第二记忆库或人格总库。

A新增35项（WSL1平台跳过）、B最新服务32项+HTTP23项（Windows服务1平台跳过）及Node15项通过，C19项WSL全通过；父级同实例启用→经历→回顾→做法→反例→修订→退休七项检查通过。父级发现嵌套返回仍漏受治理内容，已同B作者按完整JSON sentinel先红后绿修复。详细证据和限制见[本阶段父级回执](v04-memory-practice-parent-result.md)。

本轮未启用原实例Memory、未调用Gemini、未部署或发布。所谓跨日是持久承接能力，不是已观察真实多天；模型行为由脚本替身驱动，尚未证明自然自省/稳定偏好或24小时生活。下一批仍应面向ZIP中未实现的大能力，而不把这批接口测试继续拆成多轮资格审计。

## 上一批：连续日常生活——她如何连续过一天

按用户纠偏，主线不再堆工作台小功能，而是同一实例的现场→认领→准备/拿放→外出/返回→旧事续接。依据直接目录16 §1–6：普通日子没有产出配额，允许安静，世界变化/真实后果保留；不写早中晚剧本、不强制吃睡、不另建LifeManager。

三路国内DeepSeek完成具名现场进入真实决定prompt、JSON结构化裁剪、dailyact正式主链、真实拿放owner适配与 `live_day` 有限run/status/stop入口。最终同实例真实owner链通过：阅读3页到停点→WAIT park→PICK_UP→PLACE（各自正式完成）→出门→返回→停留→材料换版→明确RESUME同一Activity→读新版。选择为脚本JSON、旅程/停留时钟注入，不能称自主一天。

原实例已实际运行一次600秒上限段，Gemini新增1请求、原run账本3→4；发生 `DECISION_FAILED / MODEL_PROVIDER_ERROR`，没有动作。后续NOT_DUE不是选择休息。服务已停、lease FREE，未清quiet/重开预算，原Memory仍UNBOUND。外层exit0不等于模型成功，当前真实自主生活仍受提供方故障阻塞。

最后补通正式World工厂传入实例env/home，新增两项正式工厂接线回归与AST通过。原实例完整绑定的只读目录为available，能读“临海的住居”和真实街区地点；未增加模型请求或重启服务，修复后live summary仍未重新验证。完整证据、失败与边界见 [本阶段父级结果](v04-lived-day-parent-result.md)。不宣称全库绿、24小时/多天覆盖、稳定人格或全设计完成；后续建设继续围绕生活连续性，不回到孤立功能清单。

## 上一批：身体参与与有分寸分享

按用户最新要求，生活能力不以“必须过完一天”为硬指标，施工和真实模型验证统一国内DeepSeek。身体负荷/缓解定性信号、休息会话/进度与可用性进入正式决定prompt；不混模型预算，不从无信号推出健康，不把REST成功当恢复。正常循环真实姿态改变后，owner休息判定确实改变；原阅读可START→CONTINUE同一Activity。高负荷休息后恢复再接旧事的完整链仍未证明。

分享复用Supply ManagedArtifact版本，明确草稿→延后/放弃/过期→本地投递，与正常assembly/AG1/evaluate_once接通；模型可读有界草稿、来源版本和延后条件。父级实际Supply socket使用7项通过，正式脚本Decision分享链通过；来源撤权后依赖正文泄漏已按完整JSON sentinel先红后绿修复。唯一渠道local_inbox，外部proactive发送仍关闭，不称已能主动联系真实用户。

真实国内 `cn:deepseek-v4.1-flash` 在新隔离实例进行了2次调用（账本0→2，HTTP200），正式选择START阅读→CONTINUE同一Activity并实际读页；模型看到body与草稿，但没有选择分享，草稿仍DRAFT/投递0，不追加调用逼分享。原实例/模型配置/Memory未动，没有用此次成功覆盖旧Gemini失败。详见 [本阶段父级结果](v04-body-sharing-parent-result.md)。当前实践链单方法仍因MEMORY_DB_MISSING失败，未基线归因；不宣称全库绿、自然主动分享或真实一天完成。

## 上一批：有松有紧的生活——放下、缓过来、重新接上

身体条件WAIT让正在做的事保留Activity/材料/书页并释放前台，真实POSE/REST经正式AG1/AG2/AR0→World/Body→LR5完成；更晚的新鲜owner证据确有缓解时才交LR4开放资格，维护零模型且不自动恢复旧意愿。后来新决定RESUME原Activity推进下一页，或NO_ACTION继续停着。A18项直接测试及含等待维护20项通过，父级独立同实例六轮6checks全真。负荷为启动前seed0.60、clock可控、决定是原始JSON脚本，非自主疲劳/真实数小时/自然一天。

手机分享接通真实服务与界面：有界草稿、来源、延后条件、本地收件箱和收据，来源拒读扣留正文；不新增库、无外发。父级真实socket+Edge草稿→延后→本地receipt1通过；发现主屏直达身体未读world导致无控件，窄修后6DOM回归/变异负控通过，真实浏览器坐下/receipt不重复/移动端布局5checks通过，服务已关。相同prepare key随时间可能CAS_CONFLICT仍如实保留。

国内DeepSeek真实有限验证使用正式生产cognition/AG1，新隔离实例3次请求与账本0→3一致：前两轮CONTINUE同Activity页1→2→3，看到身体/断点但没选休息；第三轮PROVIDER_OUTPUT_TRUNCATED→DECISION_FAILED，整体exit2。已结束不重试、不调高额度；不能称自然休息整链通过。相邻wait basis单项仍FAIL，无等价基线不归因为历史无关；实践MEMORY_DB_MISSING亦未解决。详见[本阶段父级结果](v04-life-pacing-parent-result.md)，无全库绿/24h/主动外部联系/发布声明。

## 上一交付：有人来，也有自己的生活

普通本地来话只观察，不抢私人活动；明确邀请先经过新鲜决定，拒绝/稍后/失败不改变原活动。
接受后采纳内经owner CAS交接，原阅读WAITING保留同Activity与checkpoint，joint占唯一前台；
显式结束由LR4开放资格、LR5完成joint，再由正常事件窗口的新Decision RESUME并实际读第二页。
手机主屏提供普通来话、明确范围邀请、显式结束和真实处理状态，不把排队冒充接纳。

最终核心WSL14项、单前台合法性14项、父级真实owner正常入口9checks/page2通过；手机service/HTTP69项、
DOM9项、直接受影响通用手机34项与Edge真实点击7checks通过。生产预算重复包装/同tick二次决定、
无Decision误写拒绝、真实浏览器裸UUID键拒绝均已按具体证据修复，失败现场保留。notes tree/format76篇通过。

必要真实国内DeepSeek仅1请求：HTTP200/stop但AG1 INVALID_SCHEMA，无committed Decision，不能称自然拒绝或接纳。
预算已结束、无追加调用；原阅读仍ACTIVE/page1、断点保留、session NONE。原实例Gemini/Memory/外发未动，
未提交部署，未证明完整社交、自然长期共处或总体设计大半完成。详见[本阶段父级结果](v04-social-life-parent-result.md)。

## 当前结果：有内容的相处与可延续共同作品（机制通过，自然模型验证失败）

共同实践复用既有 ManagedArtifact，不另建作品库；明确材料邀请由新鲜 AG1 接纳后开启，双方输入、
主体可见回应与贡献、作品正文 CAS 版本实际落盘。只有当前真实 ACTIVE joint 与对应作品持久绑定时
普通来话才可触发一步内容请求；候选/暂停/结束/无 joint 不自动发话，每次请求共用实例持久预算。
手机正式工厂只写用户侧意图到同实例 inbox，不替她回应、接纳或完成 Activity。显式 END 由 Life
完成 joint、开放原私人续作资格，再由正常事件窗口 fresh RESUME 同 Activity 实际续读 page2。

父级独立真 Supply/World/正常 runner + 正式 PhoneService 11checks通过；真实 Edge 主屏选材→3轮→
共同作品→END→page2→重启读回12checks通过，0页面异常、390px无溢出、业务正文不入storage。
这些行为选择和内容为严格原始JSON替身，不冒充自然模型选择。父级修复已选阅读source被错误excluded
导致派生回应全遮空、END从错误字段查不到持久绑定两处真实产品缺口；相关回归与治理保持通过。
最终contact/phone67 + life seam/真Supply43 + UI11通过，79篇笔记tree/format与diff-check通过。

真实国内DeepSeek仅1attempt，HTTP200/stop，但AG1 `DECISION_ATTEMPT_FAILED:INVALID_SCHEMA`，
无committed Decision，来访UNKNOWN/DECISION_NOT_COMMITTED；原阅读ACTIVE/page1/checkpoint未变。
没有自然三轮/作品/恢复证据，整体自然行为验证exit2；12attempts/900秒窗口已ENDED_NO_FURTHER_CALLS，
1次桥记录与实例ledger0→1一致，不重试刷接纳。原实例Gemini/Memory/外发未改，无发布/提交。

保留限制：正式端口历史head-only；客户端选材版本不跨邀请排队锁定；长整合模块56例中7fail+1error
为作者实测，未有等价baseline、不称历史无关；本次未修其AR0 closeout/continuity分支。
详见[父级结果](v04-shared-practice-parent-result.md)。不宣称整个目标自然实现、全库绿或完整下午/一天。

## 当前结果：旧作重访与有来历的续改（机制/真实owner/手机HTTP通过，界面与自然模型未验）

Supply 原 owner 新增两条纯读协议 list_versions（只回元数据 + 游标 + owner 自己计数）与
ead_version（具名版本正文 + 全量 content_sha256，越界回前缀且 content_truncated=true），
归属/tombstone/artifact 绑定由 owner 自己判；LifeArtifactPort 与 PrivateProjectService
接上后历史 complete=True 可翻页，能读回某一版、与当前 head 做有界比较（owner hash 为准，
同正文不同版本判 machine_head_only），并以"新 artifact 单 parent + 首版结构化来源行"另开一件
有来源的新作；来源永远从不可变第 1 版读回。手机 4 条 GET + 1 条 POST projects/derived 全走既有
Host/Origin/Sec-Fetch/CSRF/白名单/operation 记账。

真实 Supply owner + 真 PhoneService + 真 loopback HTTP 12 tests 通过（含别的 artifact 版本号、
别的 subject、tombstone 扣留、资料当项目、分页/正文越界、并发 CAS 冲突、答复丢失 UNKNOWN 同 key
对账只 +1 版本、GET 纯读前后 head/revision/版本数/未决操作不变、同实例新 runtime 读回无重放）。
受影响旧组 140 例与 Supply 协议 31 例全绿；	ests/test_v04_private_projects_stage.py:472 既有
陈旧期望仍红（未改文件中，非本段引入，保留不修）。

未完成：手机界面（app.js）未接新路由、未做真实 Edge/CDP 点击，正常 Life 循环内的旧正文进入实际
写作上下文（sentinel 进 actual model writing context）未实现。真实产品模型 calls 0，固定路由与
新一轮共同额度留待父级核验后另开。前阶段自然共作未通过与三个旧 restart 红例的限制不变。
详见[本阶段施工结果](v04-work-revisit-result.md)。

父级 bounded review A–F 已修并定点反例：来源资格覆盖所有派生正文/比较/来源 note（源 tombstone 后扣留、sentinel 不出现、零写）、
派生创建 key 含完整意图且 taken_at 取源版本 owner 时间（跨新 runtime/换时钟仍只建一件，异 purpose 409）、比较剥离标记后分判 body/header/hash、
来源标记如实标为 SELF_DECLARED（未认证、不授权读）、既有红更正为本轮 22 项中 1 红且未做等价归因。实测：work-revisit 16 绿、受影响手机/共享实践 98 绿。
手机界面、真实 Edge 点击、正常 Life 循环写作上下文、Life runtime 重启仍**未做**，模型 calls 0。
