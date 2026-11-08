# D ambient自然观察结果（2026-11-03）

08:58:07.453—09:11:02.569 CST，至停批标记775.116真实秒；唯一run和父级audit均exit0，因REQUEST_CAP_REACHED结束，非跑满1800秒或provider故障。20次预留=13decision+7content，累计232/1000余768，不退款、不续跑。
13个正式决定都有model_run_ref，认知attempt均SUCCEEDED：5 START、4 CONTINUE、3 PAUSE、1 ABANDON。流程3条NO_ACTION的wake没有Decision，不能冒模型明确选择安静；另1条DECISION_FAILED不抹掉已持久的CONTINUE，细因本次未核。118轮中105 NOT_DUE不是105次模型调用。
4条FILE_ACTION成功，4个owner版本引用与4回执；3件Activity PAUSED且释放前台/无恢复条件、1件ACTIVE。另1件BODY Activity的World Action因PRE_SUBMIT_FAILED/GATE_OFF明确未提交，后来ABANDONED；无UNKNOWN或未决提交。不同版本不在本报告冒不同类型生活或完整完成。
World子进程ambient=1/gate off；起止均home/bedroom/revision1、standing/south/Body revision2。未出门、姿态未变，不把世界动作拒绝当ambient影响或功能失败。
离线父级55项/22.616秒通过，脚本client实际输入有同事件ID及公开summary；真实run的ambient_client为脚本专用统计，NO_REQUEST/0不代表真实零请求。本批真实输入中的ambient成员、送达/理解/反应均UNKNOWN，不看reasoning/私文，不补关键词仪器或事后构造冒现场。
settlement向life有4 DELIVERED_TO_CONSUMER；memory/goal/world各4 DELIVERY_FAILED，12条实际记录均为attempt1、err:{domain}:ProductionExecutionRefused、consumer_disposition为空，与既有三域RefusingSettlementConsumer拒写策略一致，不按漏接缺陷处理。原始失败状态保留，不冒写入成功；详见[既有拒写决定](../../.agents/notes/implemented/bug-fix/2026-10-29-refusing-settlement-consumer-domain.md)。本次未修改消费者或Memory准入，也不证明完整G1/G2/V-C/24h通过。
正常runtime closed，两owner exit0/socket_live_after=false；PID1850/1851/1853均消失、本根产品进程空。graceful行未见、lease/drain未独立确认。275条trace/drop0；所有数据与失败史保留，默认/旧根/Memory规则未改、未提交发布，本批结束不补第二run。
