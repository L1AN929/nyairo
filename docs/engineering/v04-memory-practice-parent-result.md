# v0.4 跨日记忆、自省与个人实践：父级阶段结果

## 产品结果

按 `D:\NYAIRO-v0.4-跨日记忆与个人实践-大阶段单.md`，国内 DeepSeek 4.1F 三路完成本地初版。设计依据为总体设计ZIP的15 §5.1–5.4与18：过去进入今天、结果进入具体做法，不建人格总库。

1. **生活记忆产品化**：实例可显式启用自己的Memory（配置先备份，不自动启动服务、不重置run/预算）；经历库可检索较旧相关内容、读详情、核对来源与治理。GET不建库。写入Memory人格取绑定port.subject，不从Supply主体字符串猜归属。
2. **回顾和个人做法工作台**：手机经历查询/回顾创建编辑/有范围做法/试用评价/修订/退休，文档仍由Supply ManagedArtifact持有。状态TENTATIVE/TRIAL/CONTESTED/RETIRED；没有次数自动晋升SUPPORTED，UNKNOWN不变成功，反例和旧版本保留。
3. **自主主链接线**：可读经历形成回顾选项，明确Decision选择后真实创建/AR0续写；committed Decision可保存做法提案。后续决策与写作读到具体instruction/scope/uncertainty，method_ref经校验并关联方法版本及实际后果refs。评价是有证据的独立判断，不是“写出版本所以有效”。
4. **治理派生传播**：受治理经历不再供回顾/做法上下文消费，依赖它的正文、指令和试用说明在返回投影中收起。不是物理删除作品或历史。

详细回执：[Memory](v04-memory-product-result.md)、[工作台](v04-practice-workspace-result.md)、[自主主链](v04-practice-life-result.md)。

## 必要验证

| 范围 | 实际证据 |
|---|---|
| A新增Memory测试 | WSL35项、1平台跳过，exit0；Windows35项、31平台跳过，exit0；精准文本4项/预检20项通过 |
| B工作台和Phone | 最新Python32项服务（Windows1平台跳过）+23项HTTP通过；15项Node纯函数/源码检查通过；真实Supply+M0集成在WSL通过；不是浏览器交互证据 |
| C新增主链 | WSL19项全部通过，14.698s，`/tmp/practice-new.log`；Windows13项实际执行、6平台跳过；决策/内容回答为脚本替身，owner是真实的 |
| C精准受影响组 | 60项，11.692s，`/tmp/practice-affected.log`，exit0；没有跑全库 |
| 父级独立使用 | 同一隔离实例7项全true，Python工具exit0，`ok=true`；服务已停止 |

父级独立链：显式启用Memory并备份配置→真实作品修改→consumer保存并检索经历（Memory人格与Supply主体不同）→建立并编辑回顾→建立暂定做法→明确NOT_HELPED评价使其CONTESTED→修订回TENTATIVE→退休保留文档，原阅读/PDI头不变。试用评价由验收明确提供，不伪称模型自然判断；consumer handoff由验收构造并以实际owner读回的作品版本作依据，不代替C的真实AG2链证明。

成功夹具 `/tmp/day-life-parent-practice-stage-1792411799586470733`，证据为会话scratch `practice-stage-smoke-1792411799586470733.json`。第一次脚本误取实例顶层run_id，KeyError/exit1；改为比较除三个Memory字段之外整个原environment不变后通过。失败证据 `practice-stage-smoke-1792411749883506039.json` 保留，启用本身在首轮已成功，不归咎产品。

### 本轮发现并修复的实际问题

B初版治理只清顶层content/body，嵌套project.content、instruction/question、试用detail和列表仍可能带受治理内容。父级读到后交同B作者窄修；独有sentinel对完整JSON响应断言，先7项失败，再追加普通项目详情时8项中1失败，最终8项全部通过。修复覆盖实践详情/列表及普通项目详情绕行，不以“顶层为空”冒充治理完成。原红日志与夹具保留，未再开独立审计队。

## 限制与后续边界

- 原实例未启用Memory、未启动，也未改模型/请求窗口/预算/历史。本轮无真实Gemini调用；软件仍本地0.4开发，未提交/发布/部署。
- 跨日指持久化承接能力与真实接口链，不是实际跨过多天的自主观察。19项主链中的模型回答明确为脚本替身，不能证明自然主动自省、稳定偏好或24小时生活。
- Memory正文检索是词包含，方法匹配是子串加一次有界兜底；不是语义搜索。原生Windows的真实Memory依赖fcntl不可用，WSL验证通过，Windows具名拒绝而非假成功。
- 做法是私人实践文档/提案，不是可任意执行的Skill、人格或稳定信念；C链的HELPED由Decision明示，NOT_HELPED反例用显式窄评价动作验证，不冒称第三轮自主Decision已通过。
- 新界面有HTTP与纯函数检查，未做浏览器视觉/DOM交互验收。不是UI所有状态均已实测。
- 其他当前测试失败（旧项目ID前缀、WAIT park-basis预期、连续性helper签名）沿上轮记录，本轮未修；不声称全库绿或已证明其历史来源。
- 保护MANIFEST及两个历史fix脚本，保留所有既有dirty；没有物理清理夹具。原实例钱包2860JPY/4交易为此前观测，本轮未复测余额。

最终门禁：65篇notes tree/format均exit0、git diff --check exit0；MANIFEST及两个fix脚本diff为空、跟踪删除与暂存列表为空，HEAD仍41e818faaa24115fbf43db2977b15585c04144af。证据为会话scratch `practice-final-gates.json` / `practice-final-gates.log`。门禁不替代产品行为证据。
