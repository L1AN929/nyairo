# v0.4 自主求知与经验回用：父级阶段结果

## 结果

国内 DeepSeek 4.1F 两路完成本地可用初版：资料库/研究工作台与手机入口；自主疑问→实际资料→选择研究→真实创建/续写→后续文档回用。没有新知识/Memory/作品数据库，全部沿用 Supply ManagedArtifact。

- 手机可导入有来源声明的文本、检索/查看资料、选择来源创建研究、编辑范围/未知/正文、归档研究。
- Life 从自己的 unclear 疑问产生研究选项；只有明确 Decision 选中才创建作品，随后走既有 AG1/AG2/AR0。材料正文与实际版本进入研究写作上下文；资料不混为可续写作品。
- 后续决策和普通作品写作可读到 owner 当前研究正文的有界片段，带问题/范围/未知/来源状态。研究不是 Memory、信念或稳定人格。
- 来源换版为 STALE、归档/不可读为 UNAVAILABLE；关闭研究保留正文和历史。没有自动执行或强制产出。

实现细节与测试：[Life 回执](v04-research-life-result.md)、[工作台回执](v04-research-workspace-result.md)。阶段单 `D:\NYAIRO-v0.4-自主求知与经验回用-阶段单.md`。

## 必要验证

| 验证 | 实际结果 |
|---|---|
| A 新测试 | 17项通过，12.048s；含3条真实 Supply socket/AG1/AG2/AR0 链，模型回答为明确脚本替身；`/tmp/research-new.log` |
| B 新测试 | Python服务22+Phone API9通过；Node UI纯函数9通过（不是浏览器视觉验收） |
| 直接受影响检查 | A43项通过，8.002s；B精确Phone组合51项、13条件跳过及Node49项通过；不相加为新增总数 |
| 父级独立使用 | 实际导入检索→创建研究→编辑保留来源→旧head冲突→资料换版标STALE→归档保留正文；六项检查全true |

父级成功夹具 `/tmp/day-life-parent-research-stage-1792405187870000443`，证据 `scratch/research-stage-smoke-1792405187870000443.json`，`ok=true`，Python无异常且工具exit0，服务已停止。原夹具的普通作品/阅读/PDI头均未变。首次脚本在更新来源时误把 keyword-only artifact_id 当位置参数，TypeError/exit1；仅修验收脚本调用，失败记录 `research-stage-smoke-1792405147888960853.json` 保留，不归咎产品。

### 一处具体产品缺口已修

父级读到回用摘要截取全文开头240字，长JSON头使片段只有机器标记、没有新增发现。原A writer加严格断言后，真实主链在旧实现红：独有的实际写入发现不在后续excerpt（`/tmp/research-red.log`，1项/9.782s/exit1）。修为B parser给出的正文小头+尾部有界片段；同一真实链+2项摘要用例通过（`/tmp/research-fix-methods.log`，3项/7.446s/exit0）。之后整个新模块17项通过，未重跑43项已绿组合。现在断言同时覆盖 owner真写入的发现、后续决策excerpt、普通作品prior_research及研究写作上下文，不能以“非空”代替成果回用。

## 边界与遗留

- 本阶段不联网抓取，origin仅来源声明；提供的是已有材料内求知，不是全网研究或独立真实性认证。
- 本轮未调用真实Gemini、未启动原实例、未改变模型配置/预算/run/Memory绑定。不能据脚本选择链声称Gemini自然主动研究，不能声称24小时自主生活完成。
- 工作台列表有界扫描；项目版本历史仍有限；研究成果是暂定文档，不代表理解、稳定方法或信念已形成。
- 子代理额外遇到三条当前失败：旧项目ID前缀断言、WAIT basis与park预期、连续性测试helper签名。它们所在测试文件本次未改，本轮未修；父级未做前版本负控，故不声称已证明历史来源或完全无关。新阶段主链不依赖放宽这些断言。
- 原实例及其业务历史未启动/改写；钱包2860虚拟JPY/4交易是上次观测，本轮未复测该余额。所有本阶段验收用隔离夹具，保留不删。
- 本地未提交/发布/部署；未重写MANIFEST和两个历史修复脚本。

最终仓库门禁：notes tree/format各62篇通过、git diff --check exit0；保护文件diff/跟踪删除/暂存列表均为空，HEAD仍41e818faaa24115fbf43db2977b15585c04144af。证据 `scratch/research-final-gates.json` / `research-final-gates.log`。不把门禁绿当自主生活证明。
