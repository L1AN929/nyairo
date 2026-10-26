# m真实日：候选/机会与提案字段呈现（只读核对）

只读核对 `fastday-m` 已保存的安全记录，回答两个问题：那 8 个认知回合到底给了她哪些可选机会；`later_item` 这类提案入口有没有真正呈现。不读 prompt/completion/reasoning/正文，不访问原 root，不跑脚本、测试、服务或产品模型。所有结论只对已存安全记录成立。

已读来源：[父级安全摘录](../../.agents/worker-runs/20261026-fastday-m-parent-evidence.json)、[父级收口](../../.agents/worker-runs/20261026-fastday-m-parent-result.md)、[运行结果](../../docs/engineering/v05-fastday-m-20261026-result.md)、[最终 status](../../.agents/worker-runs/20261026-fastday-m-final-status.json)、[统计文本](../../.agents/worker-runs/20261026-fastday-m-final-stats.json)、[启动/状态日志](../../.agents/worker-runs/20261026-fastday-m-start.log)。`Glob *.agents/worker-runs/*fastday-m*` 未发现上述之外的安全 JSON；两份失败 audit 日志与 `20261026-fastday-m-safe-audit.py` 未打开。

## 一、能确认的事实

- 8 次认知发送，每次提供候选数 `[6, 6, 5, 5, 5, 5, 5, 5]`；全局 unique=8、audit_rows=11、`open_at_end=5`、重复 id=0、同来源=0。这些是计数，不是名单。
- 8 次尝试、7 条已提交 Decision（各有真实 mrun），规则决定 `rule=0`：4 次 NO_ACTION 是模型在有候选的情况下选的，不是规则空集。
- 预算账本 9 个 token（8 decision + 1 content），只按顺序存在；安全记录没有把 token 映射到具体回合，本报告不逐轮编时间或 id。
- 决策顺序与选中目标：START 阅读（`actv_bd664339b5333293`，`actn:ad0002c9bab49e2e` FILE_ACTION SUCCEEDED，写入 `artifact:b2255719636c438b81e5154c3de3cb9b` 第 2 版）→ PAUSE（同 Activity，`foreground_released=true`，`EXTERNAL_RESULT_EVENT` PENDING、`satisfied_at=null`）→ NO_ACTION → 截断失败（未提交）→ START 坐下（`bodyact:pose:seated:south`，`actn:7d43d534c53caf68`，`wauth:4c60baffb4b7f0258ab1a8b2c4e11c61` CONSUMED，standing→seated、rev2→3，`COMPLETED_WITH_EVIDENCE`）→ NO_ACTION ×3。
- `explicit_decision_field_counts` 只采集了四个字段，且全为 0：`method_proposal`、`method_trial`、`method_ref`、`sharing_action`。

## 二、逐轮矩阵

| 轮 | 决定 | 已提交 | 已知选中目标 | 当轮候选集合是否留存 | 合法决策集是否留知 | 项目 popt / 到期元数据 | `later_item` / `future_item_actions` / `actions` |
|---|---|---|---|---|---|---|---|
| 1 | START | 是 | 阅读Activity；`artifact:b225…` v2是保存读后记录的私人事项结果工件，不是已证选中的阅读材料候选ref | 否，只有计数 6 | 否 | 未采集 | 未采集（`actions` 仅 owner 侧 FILE_ACTION 可见） |
| 2 | PAUSE | 是 | 同阅读 Activity，释放前台 | 否，只有计数 6 | 否 | 未采集（PENDING 条件属活动状态，不是候选） | 未采集 |
| 3 | NO_ACTION | 是 | 无 | 否，只有计数 5 | 否 | 未采集 | 未采集 |
| 4 | OUTPUT_TRUNCATED | 否 | 无（长度失败，`response_received=true`） | 否，只有计数 5 | 否 | 未采集 | 未采集 |
| 5 | START | 是 | 身体 `bodyact:pose:seated:south` | 否，只有计数 5 | 否 | 未采集 | 未采集（`actions` 仅 owner 侧 POSE 可见） |
| 6 | NO_ACTION | 是 | 无 | 否，计数 5 | 否 | 未采集 | 未采集 |
| 7 | NO_ACTION | 是 | 无 | 否，计数 5 | 否 | 未采集 | 未采集 |
| 8 | NO_ACTION | 是 | 无 | 否，计数 5 | 否 | 未采集 | 未采集 |

表里的「未采集」是记录没有这一项，不等于 0；「只有计数」表示知道给了 5–6 个候选，但不知道它们是什么类型、什么 ref。

## 三、问 1：当轮确有哪类可选机会

能确认的是两个被采纳的活动类别：私人材料阅读和身体姿态。阅读的`artifact:b225…`第2版是读后记录写入的结果，不是阅读来源候选身份；本安全摘录未保存当轮被选阅读candidate/source ref。身体目标`bodyact:pose:seated:south`有真World效果与票据证据。所选活动/效果不证明其余候选是什么，也不证明全集里有无`popt:`项目选项或到期项。

另有运行末态的 `work_selection` 快照：`artifact_id=null`、`bound_work_artifact_id=null`、`selected_by=CONTINUITY_ARTIFACT_EXCLUDED`，`latest_resumable_skipped_non_work` 列出 `artifact:bf3b62cf…` 与 `artifact:b2255719…`。这是收尾时的选择上下文，不是逐轮候选名单，不能倒推每轮给了什么。

## 四、问 2：`later_item` 能力是否真正呈现

UNKNOWN。

安全记录里没有任何逐轮候选 metadata schema、也没有「当轮是否含 `later_items` / `due_item_refs` / `project_options` 块」的摘要。父级摘录的 `explicit_decision_field_counts` 只列四项，`later_item`、`future_item_actions`、`park_current`、`popt:` 都不在其中——这是**没有采集**，不是采集到 0。

当前树确实有这条入口（[自创未来事项核对](v05-self-authored-future-audit-20261026.md)：`later_item` 是 Decision 的可选提案键，项目走 `popt:`，提示层对 `later_items` 有 6→3→整块丢弃的裁剪），但那是今天现树的静态说明，不是 m 启动时的源码副本，更没有逐轮 prompt 存在性证据。因此不能拿它证明 m 那 8 轮里模型看见过这个键，也不能反过来说它一定没被给。

## 五、问 3：两分法能否成立

INCONCLUSIVE_HISTORY。

- `presented_not_used`：提案字段需当轮最终输出规范/能力说明已发送的证据，并另核输出是否使用；项目选项则需最终请求中该选项的结构证据。两者不能互相代替，现存安全记录不足。
- `not_presented`：需证明相应能力说明或项目选项未进入最终请求；`later_item`不是候选，其候选列表无同名项不算证据。现存记录同样不足。
- 能确认的只有：8 轮各给了 5–6 个候选、4 次 NO_ACTION 不是规则空集、四个显式提案字段（不含 `later_item`）为 0。

所以既不能说「没给」，也不能说「给了没用」。顺带说明：即使将来证实是「给了没选」，那也只是模型行为，NO_ACTION 本身不是故障，不建议为此换模型或改 prompt 造欲望。

## 六、若要判定的最小安全诊断（仅建议，未采集、未实施）

1. 如以后另获准，可考虑复用诊断接缝保存最终发送阶段的候选种类/source种类/项目选项计数，以及三个处境块裁剪前后存在性，不含标题正文。候选id与source/target命名空间分开统计，不把未核前缀当正式协议。
2. `later_item`能力说明/输出字段规范在最终请求中是否存在，与Decision输出有没有该字段分别记录，不能只靠`later_items`处境块或输出布尔反推输入能力暴露。优先查已有安全诊断能否回答，不默认改Decision落盘schema。
3. 这里只指出缺哪类证据，不授权新增采集、字段、候选、资格或真实运行。现有快照不能补造历史。

## 七、边界

- 只读安全记录；未打开 prompt/正文/私文/密钥，未运行任何脚本，未访问数据库或 WSL 实例，未发产品请求，未改业务/配置/git。
- 结论不外推为「全系统机会缺失」，也不外推为自然采用或因果；m 的 G1/V-C/understanding/24h 边界不变。
