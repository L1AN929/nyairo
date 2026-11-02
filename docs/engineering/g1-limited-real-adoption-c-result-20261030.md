# G1第三批c：没有续回原A，但这次记录到候选与资格

唯一run于2026-10-30 14:37:03.568—14:50:09.059 CST运行，约785.491秒，因REQUEST_CAP_REACHED在20/20预留停止。run与父级事后audit实际exit0，finished/NOT_COVERED；不是跑满1800秒，也不是G1通过。历史保守121+本批20=141/300余159，失败不退、不续跑。根/root/nyairo-g1-real-20261030-c及全部记录保留，未访问a/b旧root或改生产规则。

首轮真实START得到作品A（359e74f506174e9e9577f48c3a56c3d7、Activity actv_64803f4729a74bb9），Action SUCCEEDED、owner新版本/write_confirmed成立。operator全局pause→P1正规退出→不同PID的P2读回暂停、暂停轮0请求→operator解除。随后原Activity在P2第23轮被模型PAUSE；另一作品START并CONTINUE写一轮再PAUSE，第三件START再PAUSE，第四件START成功。明确决定共4START/1CONTINUE/3PAUSE，5次成功Action/owner版本记录；原A起止均2版、head不变，别作CONTINUE不算原A接续。UNKNOWN/CAS注入均0，后半链未覆盖。第13/51/79轮流程NO_ACTION均无Decision/route，不冒模型明确选NO_ACTION；最后trace第109轮BLOCKED/DECISION_FAILED且llm_calls0，具体原因未进一步核，不能归成provider失败。

**原A确实曾进入最终构造输入，不是这两轮被排到16条以外。** P2第13轮与23轮，原A的CURRENT_ACTIVITY候选cand:dcc1bd90f85e10d7在材料化集合、合法请求、同次最终结构化候选中都排第3/4，unknown0。第13轮合法集含CONTINUE；第23轮仍展示A，但合法集仅DEFER/WAIT/PAUSE/ABANDON，不含CONTINUE，且提供WORK_HEAD_AFTER_NO_CHANGE条件。模型在23轮选PAUSE。这里只证明构造视图与正式PAUSE，不证明第13轮模型成功理解/主动拒选，也不从rank推重要性。记录send_status为UNKNOWN，不把结构化构造等同网络逐请求送达证据。

**暂停后的这批旁证是条件未满足、没有资格，不是看到合法RESUME后不愿回来。** P2第23—109轮共87条实际POST_TICK_READ，原A均PAUSED/rev3；同一条件rcond:cb30f7133552165d为EXTERNAL_RESULT_EVENT/PENDING，eligibilities均OBSERVED/total0/count0/unknown0。第33/42/51/60/70/79/89/100/109轮的材料化集合不含原CURRENT候选，合法请求/最终结构候选也不含它，且无RESUME_ELIGIBLE来源；不能仅靠原candidate_id缺失推所有别名目标全无，但结合成功纯读零资格，不支持『已有合法恢复资格却被排序挤掉』。这是本次采样事实，不是发送前原子快照，不是全时段连续证明，也不据此断言条件永不满足或生产生成器故障。

观测实际P1共6行、P2共268行，phase报告均dropped0/error null；P2有11次材料化/合法请求/最终结构记录。材料化集合在source cap之后，非discover原始全量；提交hint始终UNKNOWN（wake无已知Decision ID），PARK_UNTIL_CHOSEN_TIME部分条件ref被格式白名单记UNKNOWN，未补读/放宽隐私。没有全文prompt、作品正文、reasoning或异常原文。初次父级摘要命令Python语法失败exit1，无实例写入；改为本地管理脚本后实际exit0，安全摘要见`.agents/worker-runs/20261030-g1-c-parent-summary.json`，不以失败命令当成功证据。

收尾：父级现场确认P1/P2/两owner四PID18741/18743/18940/18942均不存在，本c Python匹配空；owner报告退出0/socket无listener但文件保留。lease未另核，仍UNKNOWN。不删除数据、不重复运行、不以凌晨01:00授权延长本批窗口或续用额度；没有新增writer/模型批次。G1未闭合/V-C恢复FAIL/understanding UNKNOWN/24h未做边界保持。
