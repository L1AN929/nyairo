# v0.4 父级结果：有人来，也有自己的生活

## 结论

本阶段本地来访端口、正常Life决定/前台交接/共处结束/原事续接、手机真实入口已实现并通过限定机制验收。**不是自然模型社交行为全部通过**：必要国内DeepSeek验证实际1请求，HTTP200/stop但严格AG1 INVALID_SCHEMA，无committed Decision；失败后已停止，不追加调用。

唯一开发树`D:/nyairo-rc1-body-backend`，branch `work/rc1-phone-world-body`，HEAD `41e818faaa24115fbf43db2977b15585c04144af`。保留全部既有dirty，未提交、部署、发布、删除或清理夹具。用户明确本阶段范围不因任务量反馈改变。

## 可实际使用的能力

- 手机从主屏进入“来访与共同时间”，投普通来话、明确范围邀请、显式结束，并读真实处理状态/延后条件/原键对账。
- 普通来话只有OBSERVED，不抢私人活动。明确邀请先经过新鲜AG1决定；拒绝/稍后/失败/预算耗尽不改原Activity、revision、checkpoint或foreground。
- 仅接受时在AG2采纳内部经owner CAS交接：私人活动WAITING保留断点、joint占唯一前台；ATOMIC不能强抢。
- 显式结束经LR4开放资格、LR5完成joint。资格不是自动恢复意愿，正常事件窗口的新Decision才RESUME原Activity并实际读下一页。
- 一次邀请决策只扣一次同实例请求预算；已有生产预算包装复用，邀请已决定的tick不再紧接普通窗口问第二次。失败不退款。
- 没有committed Decision时记UNKNOWN，不能把schema/provider失败投影成拒绝。

本地事件与session不是关系总库，不提供真正并行双前台。没有真实外发；local_operator/local_inbox不是远端用户已收到，原RefusingMessageProvider/proactive kill switch保持关闭。

## 验证（不重复合计为全库通过）

| 项目 | 结果 |
|---|---|
| A本地端口 | 已交付38单进程+4跨进程测试，Windows/WSL exit0；本次续接不重复旧绿 |
| 核心新增 | 最终WSL真socket14项、0skip、exit0，fixture `/tmp/day-life-parent-t-937146` |
| 单前台合法性 | WSL14项OK，显式捕获Python exit0；外层stderr标记与Python码区分 |
| 父级独立正常入口链 | 最终9checks全真、原Activity实际page2、exit0、资源关闭，fixture `/tmp/day-life-parent-c-203350` |
| 手机service/HTTP | Windows69项OK、exit0，保留式夹具 |
| 手机DOM | 9项pass、0fail/skip、exit0，含真实UUID形状回归 |
| 通用手机受影响规则 | `tests/test_phone_ui.mjs`34项pass、exit0 |
| Edge真实使用 | 7checks全真、异常0、390px无横向溢出、exit0 |

父级机制链：阅读page1→普通来话OBSERVED且原活动revision不变→邀请一次决定→原Activity同checkpoint WAITING/joint ACTIVE→显式end→在新决定调用前读回原活动仍WAITING/page1且资格OPEN→新RESUME→原Activity ACTIVE/同checkpoint/最新有效阅读位置page2。

Edge实例`/tmp/day-life-parent-h-060031`，真实提交两条事件（普通来话、明确邀请），均PENDING，重载仍同两条。Life未运行，因此未伪造接纳。证据`session scratch/social-browser-1792498271613.json`及`social-browser.png`。服务报告own_resources_closed=true，PID10875/10893/10894经/proc检查不存在。独立Edge已关闭，数据保留。

## 过程中发现并闭合的具体问题

1. 初版先park再决定、邀请预算漏记、资格指纹缺失：原作者窄修为决定先行、采纳内交接、正式预算与资格id进指纹。
2. 原作者13绿1红曾归因LR4不匹配；父级证明测试混用未来runner时钟与当前事件，且取历史reading_position首条。正式测试改为正常事件窗口与产品最新有效位置，不修改LR4规则，不降级page2断言。原红结果保留。
3. 生产预包装负控一次tick账本+3为红；修为同ledger包装复用与该tick不再二次决定，单项红转绿、最终14项通过。
4. 真实模型无Decision却投影DECLINED：修为UNKNOWN/DECISION_NOT_COMMITTED，补错误schema回归，原真实事件不改写。
5. Edge裸UUID被A拒绝：修为创建操作键时仅contact加`contact_op:`，保存/发送/重试/查询一致；DOM负控8绿1红→9绿，Edge最终真实入账。首跑aria-label猜错为脚本错误，保留现场，未当产品缺陷。

## 真实模型：明确失败，窗口已结束

fixture `/tmp/day-life-parent-v-485681`。初始阅读由操作者脚本建立；随后一次真实国内`cn:deepseek-v4.1-flash`请求，模型确见contact处境。HTTP200、finish_reason stop、latency8120ms、实例请求账本0→1，但attempt FAILED，`DECISION_ATTEMPT_FAILED:INVALID_SCHEMA`。无committed Decision，不能称自然拒绝/接纳已证明。

原脚本仅检查传输和错误投影而exit0/ok:true，**不采信为验收通过**。纠正证据`session scratch/social-real-model-correction.json`给出FAILED_STRICT_AG1_SCHEMA。私人Activity仍ACTIVE/page1、checkpoint保留、session NONE。账本`social-model-budget.json`已ENDED_NO_FURTHER_CALLS，实际1attempt，无重试/加额/退款，资源已关闭、匹配/proc为空。

## 边界与剩余

- 原实例Gemini配置、Memory UNBOUND和耗尽窗口未改；未启用外发或新daemon。
- 机制能力不等于自然模型愿意共处、成熟关系、实际多天生活；模型schema失败仍是当前行为验证缺口。
- 既有practice MEMORY_DB_MISSING、wait-basis测试失败等未在本阶段重跑或归因为无关；不宣称全库绿。
- 当前仍是本地0.4开发，不是完整总体设计、生产切换或Release。

细节：[端口](v04-local-contact-port-result.md)、[核心](v04-social-life-core-result.md)、[手机](v04-phone-contact-result.md)。最终notes/tree/format/diff/保护与恢复检查记录于会话`social-final-gates.json`及`.agents/continuity/current.json`。
