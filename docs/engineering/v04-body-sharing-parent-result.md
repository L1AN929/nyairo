# NyAiro 0.4：身体参与与有分寸分享——父级阶段结果

## 结论

本阶段按用户要求从连续生活推进到“身体参与选择”和“有内容、有分寸的分享”，不以必须过完一天为验收条件。**身体处境、分享生命周期及正常循环接线已本地交付；国内DeepSeek真实决定并执行了开始阅读→继续同一活动。** 真模型未选择分享，对外主动联系未启用，高负荷休息后恢复并接回原事的完整链仍未证明。

设计依据：`D:\CHIYO-总体设计-v2.0-final\CHIYO-总体设计-v2-最终版` 的15 §2.4/§6、16 §1–6、18/19相关约束。施工单：`D:\NYAIRO-v0.4-身体参与与有分寸分享-大阶段单.md`。不增加人类吃睡打卡、人格总库、LifeManager或第二作品/记忆库。

## 实际交付

### 身体不再只是一档容量

真实Body负荷定性信号、缓解趋势、恢复会话/进度档位、姿态和能否开始/停止休息进入决定模型的当前处境。Body与模型调用预算分开；无信号不等于健康，开了REST会话不等于已经恢复。沿用现有六变量与阈值，不让Body直接签发AG0候选，不开放sleep_like。

正式 `evaluate_once` 通过真实World/Body gateway、AG1/AG2/AR0执行姿态改变；owner自己的休息判定由 `REST_REQUIRES_RESTING_POSE` 变为 `RECOVERY_NOT_NEEDED`，恢复progress/trend读回不再恒为NOT_READ。另有正式阅读START→CONTINUE同一Activity。高负荷隔离夹具验证REST会话及信号，但**不把这两份证据拼成已完成“高负荷→实际恢复→续接旧事”**。

### 分享有真实草稿与后果，不是每轮发动态

`LifeSharingService` 用既有Supply ManagedArtifact保存草稿与版本，支持明确有界文本/显式短摘录、来源版本绑定、DRAFT、DEFERRED、DROPPED、EXPIRED和LOCAL_DELIVERED。准备草稿、是否愿意、等待什么、投递、对方是否已读分别处理。读不改版本，终态不靠旧timer复活，CAS/幂等/UNKNOWN沿用owner。

正常assembly/cycle已接服务，AG1校验sharing字段和引用，正式Decision提交后执行一次owner操作；有分享副作用的轮次明确 `SHARE_ACTED`，不静默报成NO_ACTION。模型读到有界草稿正文、来源版本/当前性、延后条件；不是只看到id。分享件不作为普通作品续写。

本轮唯一渠道为 **local_inbox，本实例自己的本地收件箱**。receipt明确 `local_only=true`，external sent/delivery/read/understood均不宣称。原 `RefusingMessageProvider` 与外部proactive kill switch未放开，不能称已能主动联系真实用户。

## 必要验证

| 证据 | 真实范围 | 结果 |
|---|---|---|
| B服务23项＋真实Supply store 8项 | 生命周期、CAS/幂等、sqlite重开、来源换版、终态 | exit0；Windows真实store跳过不当通过 |
| A身体18项、分享helper/contract15项 | 有界处境进真实prompt、真实owner边界 | exit0；明确非正式整链 |
| A正式分享链2项 | 真Supply socket、正式assembly/evaluate_once、原始JSON脚本Decision | exit0；prepare→defer→local delivery一次，原作品head不变 |
| A正式身体链2项 | 真World/Body与正式Decision动作、原阅读承接 | exit0；不宣称高负荷恢复整链 |
| 父级实际Supply socket使用 | 草稿→延后→投递→重建→重复请求→来源变更/放弃 | 7项检查全真，exit0 |
| 来源治理回归9项/受影响服务组32项 | 整份JSON sentinel，含列表/详情/已投递receipt | 先红exit1→绿exit0；父级原探针复测无正文 |

父级实际使用根 `/tmp/day-life-parent-body-sharing-1792427263603874539`；证据为会话scratch `body-sharing-parent-smoke-1792427263603874539.json`。原阅读/PDI头不变，服务已停。这不是自然模型行为证明。

父级发现并同原作者修复：①原测试直接调用helper，未证正式链；②模型看不到草稿文字/等待条件；③正式装配漏subject/grant，sharing恒不可用；④来源被拒读后详情和列表仍漏依赖正文。修后保持身份/状态/receipt元数据，正文按来源可读性扣留，具名报告，不删草稿或倒改历史。红/绿日志路径见B结果。父级probe的最终文件为green，早先red事实保留在本报告及B red日志，不称其文件仍为red。

## 国内DeepSeek真实模型验证

施工和验证均使用国内 `custom/cn:deepseek-v4.1-flash`；API请求 `cn:deepseek-v4.1-flash`，响应名 `deepseek-v4.1-flash`。本机网关元数据listed=true，不换用国际或Gemini测试。

独立隔离实例 `/tmp/day-life-parent-m-179014`，真实World/Supply/runtime、正式assembly/evaluate_once和生产 `ProductionAgencyCognitionAdapter`。仅传输由父级Windows网关桥承载，公开fixture材料，没有改原实例配置。桥**不具备生产暂停握手，仅隔离验收使用**，不声称生产cutover。

- 操作者先放入一条公开草稿，**不是模型自己创建**；不指定模型必须分享。
- 两次真实请求均HTTP200/finish_reason=stop，约12.6秒、5.8秒；输出仍经真实AG1严格校验，不提升reasoning为答案、不补脚本回答。
- 第1轮START，`ACTED / READING_AR0 / adoption APPLIED`；第2轮CONTINUE，`ACTED / READING_AR0`，同一Activity `actv_3057f148dbddfa6e`。
- 两轮都实际收到body与draft text处境；**未提出sharing动作**，草稿仍DRAFT，投递数0。保留此自然选择，不追加调用逼它分享。
- 原作品head未变；没有内容续写调用，2次均为决定。隔离 `run:instance` 账本calls=2与桥预留attempts=2一致（0→2）。共同上限6次/360秒未重开，本阶段验证已结束，余量不是自动重试许可。
- own resources closed，后续只读进程检查未发现隔离根或原实例匹配Python进程。无外部消息。

证据：scratch `body-sharing-real-model-1792428858105179014.json`、`body-sharing-model-budget.json`、`body-sharing-model-attempt-1.json`/`-2.json`。不保存prompt/回答正文/凭据。

验证初始化有两次0模型调用的父级脚本失败：先被保留文件守卫拒绝Python外部临时探测，再因AF_UNIX路径超长；失败报告 `body-sharing-real-model-1792428761214224500.json`、`1792428804565364605.json`保留。修为显式临时目录+短隔离根后通过，未删除失败夹具或放宽全局删除权限。

## 剩余限制与失败

- 自然DeepSeek主动分享未验证成功；本轮它选择读书，不以脚本local投递冒充真模型主动联系。
- 完整高负荷恢复后接回旧事未证明；目前只有真实会话/信号边界、姿态改变与阅读承接分项证据。
- 当前相邻实践测试 `PracticeLifeChainTest.test_a_scoped_method_is_saved_then_really_used_and_evaluated` 仍FAIL/exit1：`MEMORY_DB_MISSING`、experiences空，走到研究选项。只单方法复现，未建立等价负控，不称历史无关，不宣称全库绿。
- 之前记录的其他测试失败也未被本轮自动解决；无全库回归/24h soak/稳定人格证明。
- 外部渠道关闭，固定local受众；不是完整联系人/社会/共同知识系统。原实例Memory未启用，原Gemini失败仍是历史事实，不能用本轮新实例成功覆盖。

## 交付与证据入口

- [核心结果](v04-body-sharing-core-result.md)（§6为最终接线/正式链说明）
- [分享服务结果](v04-sharing-service-result.md)（含撤权red→green）
- [设计进度](v04-design-progress.md)

唯一树 `D:\nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`，HEAD `41e818faaa24115fbf43db2977b15585c04144af` 未变。本地未提交，保留既有dirty、原实例和历史。未commit/push/deploy、未改保护文件、未擅自切原实例模型/启Memory。施工曾遇国内池503/no_healthy_account，各同模型续接一次后完成，无换模隐瞒。

最终检查：71篇notes tree/relative links与format各exit0，`git diff --check` exit0；保护文件、暂存、tracked deletions、stash均空，HEAD/分支未变。证据为scratch `body-sharing-final-gates.json`/`.log`。未把这项管理检查当全库测试通过。
