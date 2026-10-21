# NyAiro 0.4：有松有紧的生活——父级阶段结果

## 结论

本阶段本地实现并验证了“正在做事→留住断点→身体条件等待→坐下/实际REST→owner报告缓解→重新考虑→明确RESUME原事”的完整产品机制，以及手机可使用的本地分享与身体入口。**不是完整自主一天，也未证明真模型自然选择休息或主动联系。**

施工与必要模型验证统一国内 `custom/cn:deepseek-v4.1-flash`。真实模型前两轮合法CONTINUE，原活动从第1页推进到第3页；第3次请求 `PROVIDER_OUTPUT_TRUNCATED`，整体有限模型验证exit2，停止后不增加调用、不提升额度。实现/脚本整链与自然模型行为分别报告。

设计依据：`D:/CHIYO-总体设计-v2.0-final/CHIYO-总体设计-v2-最终版` 15身体/自主/披露段、16 §1–6、18/19约束。阶段单 `D:/NYAIRO-v0.4-有松有紧的生活-大阶段单.md`。事实保留不等于旧意愿自动恢复；安静、未分享、不续作都可以合法。

## 实际交付

### 能放下，也能重新接上

- 真实ACTIVE活动在本轮Body owner确有LIMITED、具名负荷、身份/版本/观察时间时，可选择身体条件WAIT；保留原Activity/材料版本/书页，释放前台去执行真实POSE/REST，而非ABANDON、清指针或重新创建旧活动。
- 身体等待条件复用LR4与现有存储。后续同一body、新鲜且更晚的版本读必须确有有界负荷/进度/趋势证据变化，才提交条件事件；不以过了多久、UNKNOWN或单一容量词冒充恢复。
- 条件满足只开放恢复资格，零模型维护不自动RESUME。后来新鲜Decision才认领原事；NO_ACTION会保持旧活动WAITING、书页不动。
- REST start/stop走正式AG1/AG2/AR0→World/Body→LR5，命令Activity能够完成，不再占死前台；成功仅证明会话。恢复趋势/档位另取owner观察，不称恢复了身体健康。
- 决策prompt有界保留断点、身体、当前世界及合法候选。runner报告透传body_checked/body_changed/auto_resumed/llm_calls，能看出真实身体维护、零调用与未自动续作。

没有新LifeManager、身体变量/阈值/速率、强制吃睡或第二记忆/作品库，REST自主模式仍只 `rest`。沿用现有quiet/feed/request budget。

### 手机上能实际使用

PhoneService用本实例subject/workspace/grant构造现有LifeSharingService，新增分享列表、来源、详情、显式短文草稿、延后/放弃/本地投递、原key只读查询，状态汇总不带私文。版本/CAS/UNKNOWN沿用owner；来源拒读时正文扣留，外来地址/路径/身份字段拒绝，GET不写不结算。

手机有草稿、延后、本地收件箱三屏和主屏入口。延后条件、来源当前性、收据与终态准确呈现，文本用textContent、业务数据不写localStorage，保留Host/Origin/Sec-Fetch/CSRF。固定 `local:owner / local_inbox`，醒目标明“本实例本地存档，未向外部发送、未证明用户已读”。

身体主屏直达会读取世界快照以展示POSE/REST；首次error/offline可重试，成功后不自动轮询。身体屏诚实区分无信号、未知、会话、趋势、恢复；低负荷owner拒绝REST不当界面故障。

## 必要验证与证据

| 检查 | 实际结果 | 范围 |
|---|---|---|
| A新增身体基础/正式链 | 14+4项exit0；含WaitTickMaintenance最终20项exit0、0skip | 真Supply/World socket、正式assembly/evaluate_once/AG1/AG2/AR0/LR4/LR5 |
| B服务/HTTP | 38项exit0 | 内存owner+真实HTTP；不是socket整链 |
| B真实SQLite | WSL5项exit0；Windows5skip不算执行通过 | prepare→defer→send、重开保留、同key不重复、来源换版/归档阻断 |
| B界面 | 分享9项；身体入口6项DOM回归exit0 | 整份app.js事件→请求→渲染；去body分支5fail、退旧加载判据2fail的副本变异负控 |
| 父级实际分享使用 | 5项真，页面异常0 | 真Supply/World子进程与Edge：草稿→defer→local receipt1，无终态重发按钮 |
| 父级身体浏览器复测 | 5项全真、exit0 | 主屏直达→真实owner坐下，原receipt仍1，390px无横向溢出，页面异常0 |
| 父级独立生活使用 | 6项全真、exit0 | 6个正常决定轮：START→WAIT→POSE→REST start→REST stop→RESUME原Activity下一页 |

父级生活使用根 `/tmp/day-life-parent-s-442058`，原Activity `actv_360c0a0005964d30`。书页1→WAIT停1→RESUME页2，同一材料版本；未变维护不发资格，owner tick档位变化及improving，满足条件后仍WAITING直到明确RESUME。资源已关。

**机制限制**：夹具启动前显式seed fatigue=0.60，可控clock驱动4×600s真实owner tick；决定是原始JSON脚本，不是自然疲劳、不是真实过去40分钟、不是真模型自主休息。它证明接线与后果，不证明内在体验。

会话scratch证据：
- `pacing-parent-smoke-1792480379417442058.json`：父级六项检查全真、exit0。
- `pacing-browser-1792460544471.json`：分享五项通过；后续导航脚本错用按钮可见文字而非aria名称，整脚本exit1，不伪称整份绿。
- `pacing-body-browser-1792460654293.json`：修前直达body无world，实际缺陷；修后DOM/浏览器验证在后两项。
- `pacing-body-browser-1792479708406.json`：父级脚本以提示文案当加载完成，过早点击失败，现场仍“正在读取”；改为等world/控件，不改产品绕过。
- `pacing-body-browser-1792479766274.json`：五项全真、exit0；截图 `pacing-body-browser.png`。
- `pacing-phone-browser-server.json` 与 `pacing-phone-resume-*.json`：自己的服务已关，夹具 `/tmp/day-life-parent-p-934242` 数据保留，分享未重放。

内置浏览器因会话非前台未能显示，实际使用检查由独立headless Edge完成；不是宣称内置面板手工验收。

## 国内DeepSeek真实模型验证

全新隔离根 `/tmp/day-life-parent-r-798264`，真World/Supply/runtime及正式 `ProductionAgencyCognitionAdapter` / AG1。仅传输使用Windows网关内存桥，不具备生产暂停握手，**仅隔离验收，不是生产cutover**。原实例Gemini未动。

初始公开夹具由脚本START阅读至第1页，明确不是模型自主创建的活动；随后真正替换 `runtime.decision_service.cognition_adapter`，保留BudgetedCognitionAdapter；内容writer也接同一国内模型和预算，不挂一个无效runtime同名属性冒充换模。

| 次数 | 结果 | owner后果 |
|---|---|---|
| 1 | HTTP200，响应deepseek-v4.1-flash，finish_reason stop，CONTINUE | ACTED/READING_AR0，同Activity读至页2 |
| 2 | HTTP200，finish_reason stop，CONTINUE | ACTED/READING_AR0，同Activity读至页3 |
| 3 | `PROVIDER_OUTPUT_TRUNCATED` | DECISION_FAILED，页3不动，没有伪造动作 |

前两轮真实模型收到body与available breakpoint，但没有选择WAIT/REST。保留其继续阅读的自然选择，不逼休息或分享。第3轮不能当安静或成功；未重试、不增加输出额度，有限模型脚本整体exit2。

共同上限6HTTP attempts/360秒、发送前持久扣账，实际3次；隔离 `run:instance` ledger0→3一致，无退款。`pacing-model-budget.json` 已 `ENDED_NO_FURTHER_CALLS`，资源关闭，随后/proc只读检查无匹配本阶段/手机/原实例Python进程。凭据、prompt、response正文均未落盘。

证据 `pacing-real-model-1792480468713798264.json`、`pacing-model-budget.json`、`pacing-model-attempt-{1,2,3}.json`；零调用预检 `pacing-model-preflight.json`仅证明model listed/AST，不冒充可回答。

## 当前失败、未做与边界

- 真模型自然休息/恢复后续接未证明；第3次输出截断仍是当前验证失败，未修传输/输出预算，不追加调用。
- 相邻 `WaitBasisCycleTest.test_a_round_without_a_dependency_sends_no_basis_at_all` 当前FAIL：拿到parkbasis而断言None。无等价基线/负控，不称历史无关。此前实践链MEMORY_DB_MISSING及其它已记录失败未被本阶段解决，无全库绿声明。
- 分享prepare相同key在真实时间变化后可能具名CAS_CONFLICT，不生成第二条草稿；本阶段未改既有服务幂等契约。分享件仍会在普通projects列表出现。
- 未实现真实外部主动联系、完整联系人/社会共同知识；local receipt不是用户收到/已读。原proactive外发关闭，Memory未擅自启用。
- 未做跨进程恢复、真实全天/多日soak、稳定人格/偏好、全设计完成或公开发布。没有将前述脚本与不同夹具拼成自然一天。

## 交付位置与保护

[核心结果](v04-life-pacing-core-result.md)、[手机结果](v04-life-pacing-phone-result.md)、[设计进度](v04-design-progress.md)。唯一开发树 `D:/nyairo-rc1-body-backend`，branch `work/rc1-phone-world-body`，HEAD `41e818faaa24115fbf43db2977b15585c04144af`。所有既有dirty保留，不stage/commit/push/deploy，不reset/clean/删除夹具，不改原实例模型/启Memory。

最终73篇notes tree/format各exit0，`git diff --check` exit0；保护文件、暂存、tracked deletions、stash均空，branch/HEAD未变，记录在 `pacing-final-gates.json`。这是管理门禁，不是全库或自然模型整链通过。
