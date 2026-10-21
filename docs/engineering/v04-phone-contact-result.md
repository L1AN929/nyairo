# v0.4 手机：来访与共同时间

Status: implemented；父级接续已中断的C半成品，完成必要测试、真实浏览器使用与UUID操作键修复。未部署。

## 产品与接口

主屏“来访与共同时间”提供普通来话、带范围的明确邀请、针对邀请的显式结束。点击只投递本地transport事件，不替主体接纳，不启动Life daemon，不修改Activity。

`components/phone/contact.py::PhoneContact(*, state_root, subject, home_ref, ...)`复用`life_local_contact.LocalContactInbox`。summary/listing/event/operation为只读面；submit接受Mapping。身份与Life state root来自实例，home绑定缺失/账本损坏具名失败，不伪造空收件箱。会话只读同一state root下`co_presence.json`的有限标签。

HTTP：
- GET `/api/phone/contact`：计数、无正文事件列表、有限会话状态。
- GET `/api/phone/contact/events`：有界列表，不含正文。
- GET `/api/phone/contact/events/<event_ref>`：明确详情读取。
- GET `/api/phone/contact/operations/<operation_key>`：原键对账。
- POST `/api/phone/contact/events`：三类事件提交，字段白名单、Host/Origin/Sec-Fetch/CSRF约束；客户端不能指定处理结果、外部地址、主体或任意路径。

LOCAL_MESSAGE永不从文字自动升级成邀请。PENDING/queued不是ACCEPTED，不是用户已收到。正文仅在内存草稿和明确详情中；列表/会话摘要不披露私人作品与认知正文。页面显式刷新/重入读取，没有无限轮询。

## 验证与退出码

| 检查 | 实际结果 |
|---|---|
| Windows `py -3.13 -B -X utf8 -m unittest -q tests.test_v04_phone_contact_service tests.test_v04_phone_contact_http` | 69项，OK，exit0；夹具保留 |
| `node --test tests/test_v04_phone_contact_ui.mjs` | 9项DOM/纯函数，pass9/fail0/skip0，exit0 |
| 父级Edge真实主屏点击与HTTP使用 | 7checks全真，exit0，页面异常0，390px无溢出 |

真实浏览器fixture `/tmp/day-life-parent-h-060031`，HTTP端口58399；普通来话与明确邀请各落一条PENDING事件，重载后仍为同两条。Life未运行，真实会话ABSENT，页面不冒充已共同生活。截图与完整证据在session scratch `social-browser.png`、`social-browser-1792498271613.json`。

## 真实浏览器发现与修复

1. 首跑等待脚本假定aria-label“来访与共同时间：可用”，实际入口文字存在但标签不同。0事件、0页面异常；修脚本按真实入口点击，不改产品。
2. 第二跑发现真实产品拒绝：浏览器`crypto.randomUUID()`生成裸UUID，A要求结构化操作键。两次提交均被拒绝，账本ABSENT/0事件，非已排队。
3. 修为`createOpStore.ensure`创建新键时支持可选keyPrefix，仅contact提交传`contact_op:`。前缀在生成时写入原键，重试/对账保持同字节；不在发送时临时转换，不改其他应用默认键。
4. DOM回归改用真实UUID形状并断言`^contact_op:`：修前8绿1红/exit1（`social-phone-uuid-red.log`），修后9绿/exit0；随后真实浏览器7checks通过。旧失败现场保留，未重复已落盘事件。

自有服务报告`social-phone-browser-server.json`为own_resources_closed=true；PID10875/10893/10894经/proc逐个检查均不存在。独立Edge已Browser.close，夹具、事件和profile保留。无真实模型请求、无外发。

## 限制

手机浏览器验证的是本地入账与真实排队，不是自然模型接纳。正式共处/结束/新Decision续读由核心真实owner链另验，见`v04-social-life-core-result.md`。没有外部聊天账户、关系总库或并行双前台；当前仍本地0.4未提交开发。
