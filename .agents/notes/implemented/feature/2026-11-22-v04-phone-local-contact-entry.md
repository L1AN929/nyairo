# Agent Note: 手机本地来访投递与原键对账

Status: implemented

## Problem

本地来访transport与正常Life共处链已经有owner，但手机需要可用的真实入口。收到、排队、愿意和实际共处不能混为一谈；手机也不能成为第二Life writer。真实Edge暴露裸UUID操作键被transport拒绝，DOM桩只检查存在键而未覆盖合法格式。

## Decision

PhoneContact复用实例绑定的LocalContactInbox，只投递三类事件、读取有限处理结果与co_presence会话标签。主屏入口区分普通来话、明确范围邀请和显式结束；GET纯读，POST沿用Host/Origin/Sec-Fetch/CSRF和字段白名单。不建消息/关系owner、不启动Life，不从提交推导接纳。

新操作键在createOpStore.ensure首次生成时支持可选keyPrefix，仅contact传contact_op:。保存、发送、重试和查询使用同一原键，已经生成的键不在发送时改写。其他应用默认生成行为不变。草稿不进storage；操作键元数据不包含正文。

测试覆盖service/HTTP真实账本、完整app DOM和Edge真实主屏点击。DOM桩现在给出真实UUID形状并验证前缀。无Life运行的浏览器夹具保持PENDING，重载仍读同两条，不伪造共处。

## Related decisions

- [本地transport边界](2026-11-20-v04-life-local-contact-inbox.md)：部分重叠，仍由该模块负责事件与幂等；本笔记只负责手机消费面。
- [决定先行与采纳内交接](2026-11-21-v04-social-life-local-contact-and-co-presence.md)：部分重叠，Activity与意愿归Life，本页不替代它。
- [分享与身体入口](2026-11-15-v04-phone-sharing-and-body-entry.md)：复用真实主屏浏览器验收的教训，不改变分享语义。

## Alternatives considered

- **放宽A操作键接受裸UUID**：最强理由是各手机应用可统一生成键，无需前端扩展。否决：A已发布结构化引用契约，手机应匹配owner，不能为一个消费端放宽共同边界。
- **发送时临时加前缀**：最强理由是只需一行payload变换，不改操作表。否决：保存键与发送键不同，会使UNKNOWN重试和原键查询错位；必须在生成时确定。
- **手机直接开始/结束Activity**：最强理由是点击后立刻反馈、无需等待Life。否决：会用用户点击替主体决定并建立第二writer，违反本阶段意愿和单前台归属。

## Consequences

收益：真实浏览器提交可入账，普通来话不会被文字升级，排队与接纳分开，幂等原键可对账；摘要无正文。代价：Life停着时事件只排队，不提供假实时聊天；刷新为显式操作，不无限轮询。

验证：Python service/HTTP69项exit0；DOM9项exit0。UUID回归修前8绿1红、修后9绿。Edge7checks全真、页面异常0、390px无溢出；真实两条PENDING重载不增。服务自有3个PID已退出，数据保留。详见docs/engineering/v04-phone-contact-result.md。
