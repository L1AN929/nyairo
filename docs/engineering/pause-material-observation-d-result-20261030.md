# d 材料变更观测：四项原始记录

2026-10-30 22:32:44.243—22:39:46.384 CST，约422.141真实秒；run/audit均exit0，实际7/20预留，PROVIDER_OR_TRANSPORT_FAILURE停批。失败不退、不续跑、不用余13；保守148/300余152，非全旧实例现场总账。非G1/完成验收。

1. **暂停选择点**：r9/28/47记录当前Activity=ACTIVE，CURRENT_ACTIVITY合法集均为DEFER/WAIT/PAUSE/ABANDON（无CONTINUE）；共3次出现暂停选项。脚本result=OBSERVED仅指预登记选择机会出现，不是文案成功。
2. **实际暂停与绑定**：PAUSE=0，故没有已绑定条件或同作关系可核。三轮只提供PARK_UNTIL_CHOSEN_TIME；condition_ref被既有脱敏白名单记UNKNOWN，不把offer当绑定，不推同作品依赖。
3. **放弃可选与使用**：上述三轮均有ABANDON，并记录正式决定ABANDON、wake状态/结果ABANDONED，Activity依次为actv_cd8c6f53bbcf48f6、actv_3701fa2d2c5c5bd5、actv_02e9466ef37c6c3c。此为原安全运行记录，不另称完成作品。
4. **同次最终结构的固定文本布尔**：7次请求轮号1/9/19/28/37/47/56，fact_sentence_present全部true；tail_present依次false/true/false/true/false/true/false。send_status全部UNKNOWN、sent=null；只证同次最终结构含固定文本，不证逐请求网络送达或理解。

后续本d元数据补核：真AG1 store有3次START和3次ABANDON，采纳均APPLIED。r19/37的wake虽Decision为空/DECISION_FAILED（llm_calls0），各自已有真实START并创建第二/第三Activity，不能称没开始；r56没有提交Decision，末次失败原因记录DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT。脚本以PROVIDER_OR_TRANSPORT_FAILURE停批，具体HTTP未核。3件均BODY_ACTION_AT_OBSERVED_PLACE，3次START同选cand:06c45ed75ea7e485 / wopp:pose:home:r1:lying（home躺下）；不等于只提供一个候选，每请求实际4个World候选。

初始执行环境不完整：三件initial_executable_step_spec=null，integration executable_step_refs/action_refs空、last_action_ref=null。只读worker具名定位d脚本向runtime传world_action_adapter=None，world_feed仅接回感知provider；cycle要求二者非空才构造world step。静态路径与现场元数据一致，首步在Action/gate之前缺失，不是已证gateoff拒绝；后续BODY_SINGLE_ACTION_NO_FURTHER_STEP仍是已批准的单步规则，不证明首步曾成功。未另跑复现实验，本次不修不重跑。

受该装配缺口限制，不能把三次ABANDON当完整可执行环境下的行为效果；选择与终结记录真实存在，不抹掉。有3次PAUSE机会但未选择，实际PAUSE绑定及后续未覆盖，不推没有意愿或害怕失去退路；新增事实句+恢复规则尾段及静态底稿同时变，行为是否改变/为什么变UNKNOWN。

trace133行，dropped_records=0/error=null；每份请求4个World候选，World READY、gate off。operator_material/control/injector均未创建。runtime closed，Supply/World exit0、socket无listener；graceful_stop_line.seen均false，不冒有明确drain日志。PID688/689/691均已不存在，相关产品Python扫描空，lease未独立确证，数据保留。无源码新改、无旧root操作，不再派工或重跑。

后续有限只读补充：本d `candidate_audit_journal.jsonl` 的既有 source_ref 对上另外三个候选：`cand:32887d04770d926f` → `wopp:stay:home:r1:REST`（原地REST）、`cand:6ad994b4fc5ddd67` → `wopp:pose:home:r1:seated`（坐下）、`cand:aefd79d4d5c04adb` → `wopp:pose:home:r1:transitioning`（保留原姿态枚举，不擅释为站起或移动）；四件source_revision均1。这是已有来源引用，不是重建候选或证明旧环境可执行。上述“不再派工”是观测批结束时的停点；后续监护已批准仅scripts d动作adapter修复与离线首步/GATE_OFF验证，唯一brye71d7d后续已交，父级独立固定 `tests.test_pause_observe_d` 21例51.809秒OK/exit0。正式离线脚本Decision链产生真实step/action refs与1个Action，attempt为PRE_SUBMIT_FAILED/GATE_OFF，outcome STEP_REFUSED，零回执、身体姿态未变；不冒真实模型采用。该World路径通过step_spec_override构造首步，adoption spec的initial_executable_step_spec仍None；首次字段非空断言失败史保留，不改components去填字段。因此该字段null单独不能证明无首步；原d漏接判断还有空step/action refs及实际脚本传None共同支持。修后证据不倒写原d现场，不证候选池变宽。今晚仍不重跑产品，148不变。

父级摘录：[七次请求安全记录](../../.agents/worker-runs/20261030-pause-observe-d-parent-summary.json)（本地ignored记录，不作为公开发行附件）。
