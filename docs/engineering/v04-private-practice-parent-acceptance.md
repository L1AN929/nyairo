# 0.4 私人读写实践：父级限定验收

## 结论

本批**本地实现与机制验收通过**：主体读取真实页后可留下至多一条私人笔记、一个疑问或不记录；取得/停点/可选批注合成同一PDI版本，跨进程续接。作品writer不再被迫“至少改几个字”，显式no_change在owner提交前停，不产生新作品版本/进度/经历/自动完成。记忆预检明确未绑定、健康已证或不可用，而不是把所有安静都说成选择。

这不证明真实模型自主选择/理解/私人心得，也不是生产启用或发布。`understanding`仍UNKNOWN。未新增用户控制页面，自己的手机不要求用户查看和操作。

设计标准为 `D:\CHIYO-总体设计-v2.0-final.zip`；底座仍已下载的GitHub `v0.3.0-rc1`，当前唯一树 `D:\nyairo-rc1-body-backend` / `work/rc1-phone-world-body` / HEAD `41e818faaa24115fbf43db2977b15585c04144af`，叠加全部已接受的本地0.4成果。没有commit/push/tag/package/site/deploy。

关联：[设计对照进度](v04-design-progress.md)、[响应与纯计划](v04-reading-response-result.md)、[主体核心](v04-private-practice-core-result.md)、[记忆预检](v04-memory-preflight-result.md)、[前轮主体续接](rc1-subject-materials-parent-acceptance.md)。施工单 `D:\NYAIRO-v0.4-私人读写实践-施工单.md`。

## 对照设计与施工分工

父级重看00/01/02/04/05/06/07/18/19相关总纲，两路国内DeepSeek精读15自主记忆、16生活生态/13私人工具及绑定来源，再对照当前代码。00/01/15/16/18/19解压文件直接字节比较与ZIP成员一致，没有重新全树SHA或声称逐个附件再通读。

已完成与未完成分列于进度表：房间/购买/Body/阅读/主体续接已有真实链；完整未来认领、相册/日历/音乐、明确作品完成合同、偏好/关系/个人技法、长期记忆因果、多模态迁居仍未作为完整产品交付。Memory/Agenda/作品owner已有底座，不报成不存在，也不凭搜索不到名称宣告全域空白。

产品由WorkBuddy DeepSeek实现：A独占PDI/内容解析，B独占cycle/assembly/作品writer，P独占phone Life预检。国内池503/no_healthy_account中断B/P后，国际版接续已保存代码定点修复，不重做，父级未接管产品实现。父级仅施工/进度/验收文档、scratch脚本和接口核实；无递归委派或额外泛化review。

## 实现范围

- `subject_reading_response.ModelReadingResponse.respond` 只接受有限当前页上下文，返回note/unclear/no_change；无可用输出不制造心得，正文不进入通用decision facts或运行返回。
- `private_daily_items.plan_page_annotation` 是纯计划，核对正确item类型/source/revision/page覆盖/撤权，完整意图幂等，沿用memo/unclear_marker/reading_anchor/reading_operations，不另建库。
- 现有Life阅读活动调用可选内容writer，经原AG2/AR0提交合并PDI。未配writer保持NOT_CONFIGURED旧行为；空白页SKIPPED_EMPTY仍可保存真实停点；请求失败/换版/撤权零半条写入。材料与PDI是独立版本边界，重读和PDI CAS不被夸称全域ACID。
- 作品显式no_change报本轮NO_ACTION，不提交同文本伪版本。作品Activity仍按原合同保持，未新增自动收尾。
- `phone.life.check_memory` 使用已存在runtime环境绑定、SQLite mode=ro和原账本只读投影。UNBOUND是可选降级；READY才代表本次健康检查成立；显式绑定但损坏或CONFIGURED/UNKNOWN未证实在resident构造前拒绝。Memory persona与Supply subject是不同域，不强制字符串相等，不擅自拉取别处记忆。

## 父级独立同实例、两个进程链

父级实际执行 `v04_practice_accept.py`，shell退出0；隔离根：

`/tmp/v04-private-parent-28093-1792322047362497620`

只起一份真实Supply owner（真实Governance/Workspace/ManagedArtifact），两个独立Python工作进程复用同一Life state、run_id、Activity、PDI。决策是明确ScriptedCognition，内容是注入client，但内容解析器是本批真实ModelReadingResponse，PDI规划和Life/AG2/AR0执行是真实现。没有provider网络请求/真实支付/Memory消费者/用户消息/浏览器写入。

1. 第一个进程读取当前第1页，真实取得+停点+一条note同一PDI版本，owner版本1→2。
2. 第一个runtime关闭并释放lease后，新进程读取原Activity，继续第2页，取得+停点+一条unclear同一版，2→3。
3. 同第二进程继续第3页，内容回答no_change，保存取得+停点但不加批注，3→4。
4. 两次工作进程退出均0，最后owner正常停止。fixture/socket/证据均保留，没有回滚。

`v04-practice-parent-result.json` **12项全部true**：独立PID、同Activity跨重启、同请求窗口、页1→3、恰一笔记一疑问、PDI恰+3、两lease释放、正文只进入内容请求不进decision请求、作品不变、材料不变、每页恰一内容请求、运行返回不含私人payload。各进程另断言PDI内部owner_version_number与真实owner版本一致。

最终Activity `actv_3afb7dc1cef4f0d8`，PDI版本4，停点3/needs_reread=false，understanding未改变。没有把重新装配同进程称重启。

隐私证据边界：运行返回和decision请求检查不含测试正文/笔记/疑问；本地原始诊断的before_heads/after_heads由owner read_head返回，自带非秘密机制测试正文，留在scratch不发布。不能将此报告概括为“所有诊断日志都无正文”。

## 实际缺陷与定点修复

- A最初按同版第一条acquisition校验，连续第2页会失败，且可能借已撤页；现按当前页真实可用取得筛选，另外钉住同key已记笔记不能变no_change。三个新反例+一个必要旧例通过，未重复40全组。
- B最初把解析器返回的来源元数据直接交给只收语义字段的planner，真实响应必拒；现核对来源后只交四个语义键。
- 两纯计划各增内部版本导致owner+2；现合并只保留owner真实下一号+1。父级连续链独立确认。
- 请求期间材料/PDI变化或原取得撤权需零写；已补重读与反例。
- 直连内容失败曾落入重复except而UnboundLocalError；现恢复CONTENT_FAILED真实拒绝，no_change仍独立处理。
- P最初跨域强制Memory owner=Supply subject及健康未证实继续运行，均定点纠正；只有未绑定属于自愿降级。
- 父级scratch曾用错ITEM_UNCLEAR常量，执行前更正为ITEM_UNCLEAR_MARKER，未制造业务失败；一次Edit旧式参数被工具拒，未写入，随后按行号成功。文档里的整文件checkout建议已移除，不允许抹去先前0.4dirty成果。

## 定向验证与证据

| 部分 | 实际记录 | 边界 |
|---|---|---|
| A响应/纯计划 | 新增40 exact IDs，exit0；窄修4项exit0 | 纯内存，不声称owner集成 |
| B核心 | 新增14集成+6 writer，直接受影响阅读9项，分别exit0 | __dict__选本类，无继承discover旧整库 |
| P预检 | 原Windows17(skip1)+旧绑定32(skip5)为修正前快照；最终WSL六条定点exit0 | 未重跑全20，不将旧错误身份断言沿用 |
| 父级连续链 | 两进程exit0，12项true，父shell0 | 真实owner/AR0，明确替身内容/选择 |
| 当前实例预检 | 函数exit_code1，blocked仅model，memory UNBOUND | 父级探针shell0是成功取得“启动阻塞”报告，不是run已启用 |
| 工程门 | 49篇note tree/format及git diff --check均0；保护文件差异空、索引空、tracked deletion空 | 无全树SHA/全量回归，无全文件系统零删除证明 |

最终源码时间说明：父级三页链完成后，核心作者另改一行条件，使“未配writer+空白页”由SKIPPED_EMPTY回到NOT_CONFIGURED；三页链有writer且非空白，不走该分支。作者在此后重跑14/9组并覆盖同名旧日志，这是重复运行/未保留每代日志的流程偏差，不据当前日志声称前一代原件仍在。父级不重复整链，另跑 `v04_final_delta_check.py` 仅一条真实owner组合检查（无writer+空白页），8.428秒、shell退出0，独立日志 `v04-final-delta-check.log`。因此跨进程链证据与最终字节差异明确限定，没有冒称整链在最终编辑后又运行。

证据均在会话scratch：`v04-gap-autonomy-memory.md`、`v04-gap-life-tools.md`、`a-new-tests.log`、`a-plan-fix.log`、`b-new-cycle.log`、`b-new-writer.log`、`b-affected-reading.log`、`v04-practice-parent-result.json`、`v04-practice-worker-first.json`、`v04-practice-worker-second.json`、`v04-final-preflight.json`。产品用例结果与真实退出来自原施工/收口报告，父级读取日志核对，不重复跑数。没有本轮旧源码负控，不把新反例称旧码负控。

## 剩余条件与停止点

本批机制开发完成，所有worker结束，无新自主常驻。当前原接受实例仅做只读preflight，没有改其钱包/房间/作品/材料/PDI历史，没有重建/恢复备份。当前实例无可用模型配置且Memory未绑定；未启真实自主读写，不消费产品模型额度。需真实运行时另行明确模型配置/消费授权及所需记忆绑定。

不宣称24h、理解、稳定偏好、完整记忆因果或全手机。真实lost-reply/UNKNOWN沿用已有AR0机制，本批未新增对当前读后批注链的丢回包故障实测；有限直接分支只做半边调用验证，production无AR0时该整轮路径不可达。保留这些边界，不靠加测试计数消除。

本轮停止在0.4已完成切片；不自动恢复先前暂停的Future/Agenda施工单，不扩大到新owner或发布。下一批按进度表剩余项重新定范围。
