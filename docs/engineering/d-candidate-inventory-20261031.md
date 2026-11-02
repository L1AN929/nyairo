# 修后 d 候选清单：直接记录与事后旁证分开

父级独立运行约定离线driver，exit0、status OBSERVED，产品模型0。新隔离根保留；fixture observed_at为2026-10-31T15:27:41+08:00（当次墙钟+400分钟，不是真实观测时刻），place:home，World READY、gate off。仅一轮正式evaluate_once、脚本NO_ACTION替身，没有World动作执行。

| 层 | 实际取得的材料 | 结果与限制 |
|---|---|---|
| ① source供给 | 本轮结束后，以同一observed_at再次调用各adapter.discover | 8源中World返回4条，其余7源返回空。这是事后重读，不是该请求原供给快照；同次原供给及pre-cap全量UNKNOWN。 |
| ② AG0保留 | 同轮trace记录的materialized CandidateSet IDs；详情按ID事后回查 | 4个ID，global_truncated=false、truncated_sources={}。详情不是独立的发前原子快照。 |
| ③ 最终请求 | 同次build_prompt trace直接记录的候选ID、source_kind、candidate_kind、legal_decisions和顺序 | 仍4个ID、顺序与②一致，均WORLD_OPPORTUNITY、DEFER/START。source_ref与target_refs来自按ID回查AG0，不能冒最终payload直接记录。executable_now未记录。 |

②、③直接记录的顺序及事后查到的目标如下。分类仅描述目标引用命名空间，不证明执行能力。

| candidate_id | 回查source_ref | 回查target_ref | 引用类别 |
|---|---|---|---|
| cand:06c45ed75ea7e485 | wopp:pose:home:r1:lying | bodyact:pose:lying:south | POSE |
| cand:32887d04770d926f | wopp:stay:home:r1:REST | stay:home:REST:300 | STAY（REST descriptor） |
| cand:6ad994b4fc5ddd67 | wopp:pose:home:r1:seated | bodyact:pose:seated:south | POSE |
| cand:aefd79d4d5c04adb | wopp:pose:home:r1:transitioning | bodyact:pose:transitioning:south | POSE，保留原枚举 |

source事后重读顺序是lying/seated/transitioning/STAY；回查的4个source_ref均能匹配该次重读，最终4个ID均属于同轮AG0集合。**匹配4/4不是同次source快照证明。**此点未列出物件、移动或阅读类目标，不据此断言所有时刻/地点没有这些机会，也不从这些名字推断“能读能碰”。REST不归姿态；此STAY不等bodyact:rest动作。

首版worker把类别按source ID误判、地点误记UNKNOWN，修正后复跑；失败与修正史保留。worker最终观察时刻15:24:31、cset:d8f2d5d61ac2cf4e；父级是新的隔离观察，时刻15:27:41、cset:34c3f89aca008970，不拼成同一运行。原21例不重跑，未修改components、生产scripts/tests或候选策略，没有额外seed/CONTROL/事项；无旧root访问。

父级收尾：runtime closed；Supply/World exit0、socket_live_after均false，graceful_stop_line.seen均false，不冒明确drain日志。隔离账本的1次脚本Decision预留不计产品请求，历史148/300不变。send_status UNKNOWN不证送达理解，脚本NO_ACTION不证自然选择。清单到此，不作供给缺陷、动机或最低活动配额结论。

复现命令由新局部driver提供：[driver](../../.agents/worker-runs/20261031-d-candidate-inventory.py)；[结构化输出](../../.agents/worker-runs/20261031-d-candidate-inventory.json)会被同命令的新隔离观察覆盖，引用时保留各自时刻。这些是本地ignored工作记录，不作为公开发行附件。
