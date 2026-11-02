# 新隔离 e：世界动作临时启用的一次自然观测

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

本人2026-10-31在本窗口直接确认：“新隔离批次、只在新进程树临时 canonical、20 请求 / 1800 真秒 / 不续跑 / 失败不退”。承接前一完整申请：允许必要实验scripts新入口适配及离线检查，不动components或生产默认。通用协调授权截止2026-11-01 01:00 CST，真实窗口仍只1800秒，不因截止较晚而延长。

## 固定身份与参数

- 批次e（与10月24日fastday-e非同一实例）：root `/root/nyairo-world-observe-20261031-e`；独立authority `/root/nyairo-world-observe-20261031-e-memory-authority`（authority.db）。
- run `run:world-observe-20261031-e`；域 `world-observe:20261031-e`，历史保守占额148/300、新增持久硬cap20，最坏168/300。失败不退；余132不自动支用。
- 只一个natural phase，1800真实秒与01:00截止取更早，真实业务时钟不快进。首次started后不得再次run，不退款/重置/换根补覆盖；失败即停，本单不补第二次。
- `WORLD_BODY_INTENT_MUTATION_GATE=canonical` 只写入此批启动子进程env映射、runtime环境映射和自有World child环境；不得从父shell export，不写.env/实例默认/用户设置。d及旧默认维持off/原值。具体传递须离线验证后才运行。
- 沿现有custom provider `https://gw.example.invalid/v1`、`cn:deepseek-v4.1-flash`、temperature0、非stream、决策cap4096/内容cap2048；密钥只在启动shell source既有项目外文件，不显示、不存日志或仓库，不新增供应商。

## 本次看什么

候选空间与执行结果分开记：实际候选类型/选中目标；是否MOVE提交与到达、到达何处；是否碰物件；是否真实留下作品版本或阅读；停下时是正式PAUSE/ABANDON/NO_ACTION、规则静默还是失败。状态标签不替代效果，Action成功不等Activity完成，未到选择点就是未覆盖。无源/无合法选项不是模型明确选择；零调用或没有移动也是合法结果。沿v2第16章“普通日子没有最低配额”，不强制造活动。

位置与候选按实际阶段记录：MOVE正式提交或Action成功不单独等于已经到达，出发/在途/到达须分别以owner状态或回执确认；若真实到达新地点，后续观察和候选随新位置变化是允许结果，不是异常，也不据此声称模型偏好或行为因环境之外的因素改变。不得预写必到street_1或强造到达。

零额外材料、零事项/控制作品/故障注入，不构造SubmissionInjector、不operator暂停重启、不强制动作或选择，不移动角色初始位置，沿标准空实例bootstrap。标准World本有对象/目录不冒额外注入。本次不测G1或能否做完作品，不设出门/阅读/成果最低配额。

## 真实后果与不许推的结论

正式许可成功的动作会真实改变新实例位置/姿态等状态、消耗票据、写入Action台账；运行结束不回滚。进程env消失不等效果回滚、不等grant撤销，也不承诺全机没有别的许可。只隔离新实例，不碰旧root。UNKNOWN仅按原execution对账，不裸重发。

不据单次观测推全天、不推动机，不与不同环境的前批比较“她变了”。不写“所有前批都关闭”——历史有临时canonical先例。d的漏接与修后gateoff差额不倒写abc现场；abc历史同一性仍未证。不把17处地图目录当17处机会实测；catalog12/17截断与其他地点机会UNKNOWN限制保留，不把物件原始拒因全部归gate。候选可见不等可执行，最终结构不等送达理解。此观测不是单因素因果实验。

## 执行顺序与停止

唯一worker适配新profile/传递/必要最小离线模块；父级仅同新模块一次。通过后重核root与authority新鲜（含symlink）、无并发产品及占额，再首次prepare/audit：prepared、started null、window absent、ledger0与身份/模型/参数一致。立即报实际创建，再唯一run。期间源码冻结，不并行writer。

结束只读本新根安全元数据、账本、候选/Decision/Action/receipt与owner状态，按真实记录核效果；不可取字段UNKNOWN，不读私文/prompt/reasoning/headers/env全文。自有runtime/World/Supply正规stop/drain/现成socket guard，只处理自有PID，不删根/裸unlink/数据清理；收尾失败记清，不冒lease FREE。保存所有数据/失败史，不自动再跑。未通过离线、身份/新鲜/额度异常、需扩components或绕安全门即停；不stage/commit/push/deploy。

当前状态：预登记已写，真实root新鲜尚待现场核对；未prepare/source/run，尚无本批产品新增。实现与验收结果另按事实追加，不修改本节事前判据。
