# G2 A唯一真实批次结果｜2026年10月31日

- 结论：**NO_EFFECTIVE_HISTORY**。唯一run与父级audit均exit0；19:42:23.156启动，20:12:26.414停批标记，约1803.258真实秒（含窗口结束与收尾标记差），停止原因为OBSERVATION_WINDOW_ELAPSED，不是供应商/传输失败。
- 实际预留 **1/52，Decision 1、content 0**；185历史＋1＝保守 **186/300，余114**（185确认＋相机历史未知1）。采集未用7、影子40及承接4均不续用、不挪额、不退款、不第二次run。
- 正式AG1记录：首轮真实 `DEFER`，Decision `dec:86ae16691b68e56c`、`mrun:real:aeb4ee4b41e819c6`，attempt SUCCEEDED/failure_class null。不从reason摘要推模型完整动机或认定capacity故障。
- 观察文件为30条observation＋1条summary，不是31次认知：首条推进DEFER，后29条NOT_DUE/零请求，约30分钟窗口结束；不是29次模型主动选择安静。下方已只读补核到期判据，不清quiet强问。
- H引用为空，未形成owner新版本＋对应可读M0经历的合格历史；本根canonical_life_state/action_reality_state文件未生成。影子、统计、承接均null，B分支未创建；**没有检验历史是否影响选择，不是检出无差异或记忆无效**，完整G2未过。
- root `/root/nyairo-g2-natural-20261031-a`、run `run:g2-natural-20261031-a`、domain `g2:natural:20261031-a`；gate off，authority位于本根run内。既有夹具双合法/40影子/RESUME映射离线绿不拿来补本批自然覆盖。
- 父级20:15:08只读核run PID28267不存在、实际Python本根匹配空、根内Unix listener0、父gate UNSET；未独立确认lease/drain，均不冒已证。初次扫描PID29533后核已不存在，不推它曾是本批服务。顶层finished与嵌套run.status仍running并存，后者是陈旧标签，不据此重启。
- 仓库默认lifecycle仍18584 bytes、mtime_ns1793412031178025500，未新增；不由单一台账推所有默认写面均已现场复核。数据/socket/原失败史保留，不重启、重放、删数据或改规则；本人这次唯一run授权已使用完毕，01:00通用截止不授权补跑。

## 29条NOT_DUE的限定核对（仅只读，无新增调用）

- 实例 `run/main/one-day/instance.json` 的持久 `CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=30`；`state/autonomous_events.json` 的 `last_check_at=2026-10-31T11:42:26.410177+00:00`（19:42:26.410177 CST）。按该配置推算下一检查点为 **20:12:26.410177 CST**，不是把每60秒观察当作每60秒认知。
- 29条NOT_DUE的记录时间为19:43:26.455至20:11:26.443，逐条均早于推算到期点；最后一条还差 **59.967177秒**。wake文件只保留DEFER与首条NOT_DUE两行，逐次状态来自同批observations，不把日志去重当推进没发生。
- 控制器summary写于 **20:12:26.359 CST**，比推算到期点早约 **0.051177秒**，没有到期后advance记录。现成控制器按monotonic窗口到期先停止、恰好终点也只允许纯读，不额外追赶。本次只观察到正常检查节奏先挡住；这是持久配置＋真实时序的重建，不冒每轮保存了完整due计算快照。
- 全部观察均0开放Activity、0恢复资格，pause状态文件未生成，没有PAUSED/WAITING长等待Activity的证据。确有一条reconsideration情境记录、TTL21600秒；但本批后续29条是NOT_DUE而非QUIET，未到期后实际触发静默判据，不能断言该记录没影响，也不能断言DEFER会强制睡6小时。
- **判定：第一种解释有本批记录支持，第二种不是本批已观察到的直接阻断。** 延长窗口可能跨过下一个检查点，却不保证会发第二次请求或形成H（到期后是否QUIET未验）；8是Decision＋内容请求上限，不是8次唤醒目标。新窗口/新真实run或改变节奏仍需本人批准，不据这次核对自动实施。

