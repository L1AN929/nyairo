# 路线 B：no_change 判断过期后的单次重新考虑

日期：2026-10-30。状态：规则草案，经指定监护审阅后部分否决；不是施工批准。

审阅修订优先于下文原提议：v1 禁止新增 `reconsideration_opportunities` 或其他持久格式，只能用既有能力，不足停报；「只继续等」首版被否决，唯一拟增真实出口改为显式后台 ABANDON，「换做法/改条件」移出本单。先只读核清现有未决副作用及承诺安全门，再交独立放弃定义；实现及最小离线验证均尚未批准。下文保留原提议与取舍，不再作为可直接施工方案。

本轮只写本文件与管理检查点，不派 writer、不改业务、不运行测试/服务/产品模型、不访问旧 root。占额沿用 141/300。指定监护转述本人选择路线 B，本文件据此定义方向，不把转述扩大成具体实现、数据格式或新 Decision 合同已经获批。

## 规则一：前提失效到底是什么

只认一条可定位的 `WORK_NO_CHANGE_CONFIRMED` 内部判断记录自身的有效期已过：必须能读到其规范 target、owner revision、原始记录时刻和该记录的 6 小时期限，并把它与原 PAUSE 的 Activity/episode/condition 及目标基线对应起来；使用该记录原有时间域判断，不能混用业务时间与真实权限/预算时间。所谓失效是「这条暂时不用再问的判断已过期」，不是作品已变化、原依赖已满足，也不是任意 tick/暂停满 6 小时。缺记录、被缓存淘汰、读失败、时钟域不明或关联不唯一都记 UNKNOWN，不从缺失推过期；不得用当前时间补造历史记录。`life_step_readiness.py` 的既有 6h TTL 必须称为判断失效，不称条件满足；它当前不再命中旧记录的效果不能直接作为本规则的执行许可。若记录属于 situations 而非精确 no_change entry，不能借本规则释放整个处境。

## 规则二：谁证明、标记长什么样、放在哪里

证明者是 Life 内部持有该判断的 `ReconsiderationLedger`，不是 Artifact owner，也不是 LR-4。本草案建议独立来源 `source_kind=AGENCY_NO_CHANGE_RECORD_EXPIRED`、用途 `purpose=RECONSIDER_ONLY`；标记包含 `record_key`（规范 target、原 revision、原 recorded_at 与 expires_at 的精确组合）、`activity_ref`、宿主 revision/episode、`condition_ref`、`observed_at` 和机会消费状态，不含正文、理由、模型回答。只允许 Life 的台账维护路径从实际记录产生，模型、工具调用者和候选生产者不能自报；候选源只投影和复查。建议写入同 state root 的既有 `life_reconsideration.json` 中独立的 `reconsideration_opportunities` 元数据区，不进入 owner 事件、condition 或 eligibility store；这是**拟议的落盘扩展，尚未核实兼容性，也未批准实施**。原 entry 过期/淘汰后仍须能持久证明该机会是否已消费；若既有台账无法保留所需原记录身份和去重事实，必须停报格式/容量方案，不能靠进程内布尔或普通 reason_code 顶替。绝不生成 `EXTERNAL_RESULT_EVENT`；`activity_waiting_agenda.py:1748-1755` 所指外部事件匹配与 PENDING→SATISFIED 路径冻结，行号仅沿前次定位，本轮未读实现复核。

## 规则三：作废后只产出什么

产出是原 Activity 的一个**非执行重考材料/候选**，建议角色名 `PAUSED_RECONSIDERATION`，必须显式呈现 `execution_allowed=false`、`resume_eligibility_ref` 缺省、原条件仍 PENDING；它不是 `RESUME_ELIGIBLE`，不创建第二个 Activity、不恢复前台、不构造 Step/Action、不调用 writer、不发资格。它可以进入正常有界候选构造并受原排序裁剪，不强行置顶或保证模型看到；未进入最终结构化请求不算已考虑。主体可以表达继续等、调整做法或放弃的意图，但**不能据此宣称现有正式链已经支持这些采纳**：已有笔记记载后台 PAUSED 无 WAIT/进度口，正式 ABANDON 仍受安全证明门扣留。保守首版建议只允许保留原等待的重考结论；调整条件或放弃只有在另行明确并获批的正式 Decision/owner 合同下才可应用，否则具名拒绝，不自动转译为 RESUME、START 或终态。当前无法同时承诺「已有可执行的三种选择」与「不改任何采纳合同」，这是施工前必须裁定的交界。原 no_change 结果和精确 owner head 证据保留：仅凭过期不能让同版本 CONTINUE 合法；其他既有合法证据如何重新确立可执行性，仍由原 readiness/采纳门决定。本规则不声称只要 head 相同就对所有活动永久禁写，也不把新 head 当自动恢复。

## 规则四：去重、失效与预算

「同一件事只作废一次」在此精确定义为同一 Activity/暂停 episode 对应的同一条原始 no_change 判断只产生、只消费一个重考机会；重启、重复维护、重复候选构造都不刷新 recorded_at 或再发机会。机会只有在最终请求确实包含它、关联的**新 Decision 正式提交并被相应重考采纳路径确认**后才标已考虑；构造 prompt、被裁剪、流程 NO_ACTION 且无 Decision、失败或结果 UNKNOWN 都不能记成成功消费，更不能自动重发模型。保留原等候的结论如需再设窗口，必须由这一新 Decision 显式确认，并从实际采纳时刻给足新 6h 窗口，产生独立 generation；不滚动延长旧记录，不在维护 tick 中自动续窗，也不伪造一次新的 writer no_change。新窗口属于重考抑制元数据，不是复活旧 no_change entry、不是恢复条件；其到期不能自动连锁花钱。宿主终态、目标/episode/revision 不匹配、来源不可读等使机会失效或扣留，不影响 LR-4 原条件生命周期；另一个前台暂时冲突只扣留，不冒已消费。默认本轮及未来零模型维护只生产/投影材料，不增加调度触发、不通过改 fingerprint 或清 QUIET 偷带一次模型调用；产品真调用必须另报范围、次数、去重与失败处理后获准，沿原持久预算失败不退。本草案本身 0 产品调用，不是以后额外调用不扣账的特权。

## 规则五：与既有边界的分界

no_change 不削：历史结果没有被抹去，TTL 只撤掉旧判断的时效性，不能把「没有新证据」包装成有步骤；PAUSED 同目标新 START 的防绕门也不动。单前台不破：有其他 ACTIVE 时不让后台重考材料抢占执行，也不自动暂停任何活动；最保守首版在前台空闲时才供这类候选，暂时冲突不消费机会。恢复不自动：原 Activity 仍 PAUSED，原条件仍按其实际事实维护；本标记不能让它 SATISFIED、不能产生 OPEN、不能消费已有资格，更不能开始执行。只有原合法条件真实满足后产生的有效资格，才可能由另一轮新 Decision 走原 RESUME。2026-10-24 已否决的「暂停 N 分钟就满足」保持否决；本方案只让内部临时判断失效可见，不以计时器推动 Activity。body/park 与 head 的短路优先序**本次明确不改**，既有 PENDING 不自动换绑 body/park，也不顺手修未验证的 body 残留疑点。

## 拟议写集与施工门

以下是职责清单，不是已批准或已核实可直接落地的写集；监工本轮不打开业务实现补审，也不先派 writer。

- `components/life/scripts/life_step_readiness.py`：暴露原 no_change 记录的精确到期事实、保留历史与机会消费状态；分开「判断失效」「执行步骤可用」。不改 6h 常量，不批量 release。
- `components/life/scripts/autonomous_life_cycle.py`：零模型维护、精确关联、后台材料和正式 Decision 消费关联；不重放旧决定、不调用 writer 解锁。
- `components/life/scripts/candidate_sources_ag0.py`、`agency_cognition_adapter.py`、`agency_decision_ag1.py`：如现有通道不足，才需新增非执行候选角色、受限合法输出与结构化提示；其角色/字段/合法集属于**新合同待批**，不能冒充已有 CURRENT_ACTIVITY 或 RESUME 候选。
- `components/life/scripts/life_integration_ag2.py`：只有批准重考结论的正式采纳合同后才可动；只记重考/显式新窗口，不转场、不新建活动、不写执行进度。未知效果沿原操作对账，不裸重发。
- `components/life/scripts/life_wait_basis.py`：只在需要透传原判断身份时才列入；不得修改 offered head/body/park 顺序，不把过期标记铸成等待 basis 或恢复条件。
- `tests/test_paused_no_change_reconsideration.py`（拟新增）：一个最小离线模块，脚本 Decision、禁网 transport，时间注入只驱动业务记录有效期。
- 本 doc 与既有相关 notes：实施批准后按真实落地范围同步；本轮不把 proposed 写成 implemented。

明确排除 `activity_waiting_agenda.py`、LR-2 状态机、Artifact owner、Memory/controls、默认 gate/预算/权限配置、旧 root 和所有已结束 a/b/c 批次。既有 `life_reconsideration.json` 扩展的读写兼容/去重容量必须先解释清楚，必要时另批格式方案；不能以「放在旧文件里」声称没有格式变化。

## 停止条件

- 要把过期变成条件满足/资格、伪造 owner 事件、放开 RESUME/同对象 START、抢占或后台进度：停止，不作为 B 的实现。
- 想让换做法/放弃成为真实可执行选项，却没有正式 Decision 和 owner 安全采纳规则：停止交具体合同，不能只提示可选。
- 台账缺原 recorded_at/expires_at 或精确关联、已被淘汰、需要新持久格式/去重保留策略：UNKNOWN/停报，不补造或删除旧数据。
- 当前 TTL 路径让同 head 自动获得 CONTINUE，而无法在不改变既有语义的情况下满足本草案：保留反例，先裁定「重考与执行 readiness」的独立门，不能趁机取消 no_change。
- 要改 EXTERNAL_RESULT_EVENT evaluator、body/park 优先序、默认定时器、调度触发、模型 prompt/候选/采纳新合同或实际产品调用：分别列清并获批，不按方向授权自行施工。
- 同一测试连续两轮失败且原因不明，或触及删除/安全拒绝/旧 root/产品预算/外发/git 发布：按原门停止；不削断言、不换入口绕过。

## 最小离线验证建议（本轮未运行）

若规则与写集获批，只交付上述一个新模块，不重跑旧 suite、不造 A/B 矩阵或长时实验。建议一个主链和少量直接负控放在同一模块：

- 隔离真实 LR-2/LR-4 与台账，脚本建立 no_change 和条件 PAUSE。到期前没有机会；到期后一个内部过期标记/非执行候选，但 owner head/版本数、Activity PAUSED、PENDING、资格数 0、Action 数均不变，同 head CONTINUE 与同目标 START 仍拒；禁网 transport 确认零真实请求。
- 原记录不存在、只是 tick、读失败、不同 target/head/episode、已终态、另一 ACTIVE 前台均不冒有效机会；真正 owner 新 head 仍走原外部事件路径，不和内部标记混同。
- 重复维护/同根重装配不再生成；候选裁剪/无 Decision 不消费；脚本新 Decision 经拟批准重考采纳后只消费一次。显式再次等候才建立从采纳时刻算的新完整窗口，不自动调用模型，不改原 condition。

拟固定命令（须先由 writer 实际建立该模块并交付，当前不执行）：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_paused_no_change_reconsideration'
```

所有临时数据用保留型 mkdtemp，不自动清理。Decision/时间/transport 替身不冒自然采用、真实 6h、跨 PID 或 G1 通过；新候选/提示/采纳变化之后的任何真跑与 a/b/c 不再是同策略输入。

## 依据与取舍

本文件补充而不改写 [原三路线方案](paused-return-options-20261030.md)，其中 B 曾把 OPEN 或重考并列，现在本草案只保留非执行重考；其早期「哈希无法确认对象」已由 [c 三问](g1-c-self-dependency-20261030.md) 的正向匹配更新，不沿用旧 UNKNOWN。

相关旧决定已读：[核心连续性](../../.agents/notes/implemented/architecture/2026-10-26-v04-day-life-core-continuity.md)（6h 判断、no_change 与同目标防绕门）、[显式 PAUSE 条件](../../.agents/notes/implemented/feature/2026-10-24-paused-explicit-resume-condition.md)（不把时间当条件）、[单执行前台](../../.agents/notes/implemented/feature/2026-10-24-paused-background-single-execution.md)（后台不抢占及终端安全门）。它们均部分相关、没有被本草案整体取代，不删除、不归档、不改 implemented 决定。

保留原依赖的最强理由是权限和因果最清楚，代价是临时 no_change 判断消失后旧 PAUSE 仍可能长期等待。TTL 直接 OPEN 的最强理由是路径短且能用原 RESUME，但它把内部记录过期伪造成恢复资格，否决。只在 prompt 写「再想想」最省代码，但没有正式采纳和去重路径会再次出现假选项，否决。推荐先批清非执行重考合同及保守输出，再施工；它可能只改善可见性、仍不能续原作，这是必须接受的范围，而不是许诺下一批就过 G1。
