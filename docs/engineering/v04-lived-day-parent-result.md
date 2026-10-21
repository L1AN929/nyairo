# NyAiro 0.4：连续日常生活阶段父级结果

## 结论

本阶段从孤立工作台转到“她如何连续过一天”：现场进入决定、日常动作影响真实世界、暂放旧事后外出返回、明确接回同一活动，以及有限运行/停止/续跑。**连续生活机制链通过；原实例自然模型生活尚未证明。** 不把短链或脚本选择称为自主一天，不以零动作掩盖提供方故障。

设计依据为 `D:\CHIYO-总体设计-v2.0-final\CHIYO-总体设计-v2-最终版` 的15、16、18、19相关约束，尤其16 §1–6：历史与环境承重、普通日子无配额、等待/休息/缺测分开、无强制人类作息、无中央LifeManager。软件仍为0.4。

## 她现在可以怎样连续生活

- 看见当前地点名字、姿态、手上物品、附近可达地点/路程/营业，以及本轮实际可做的事；未知不伪装成没有。
- 可以继续阅读或作品，也可以明确WAIT并park旧活动，顺手拿起/放下物品，出门、到达、返回、停留，再明确RESUME旧活动；PAUSE仍保留前台，不被偷换成释放。
- 拿放经过正式Journey adapter、AR0、gateway与真实Resolver。动作后重新观测，进度与活动完成由原LR5分别判定，不用ABANDON冒充做完。
- 当前环境优先于研究/实践材料。大prompt按完整结构块裁剪，仍是合法JSON；极小上限可能over_budget，如实报告。
- 操作者可用 `scripts/live_day.py run|status|stop` 运行有限段，沿用原实例/run/账本，限制只能更严，不清quiet、不强制唤醒、不重开预算。结束停止本段自己的服务并释放lease。

## 核心连续链：同一实例、真实后果

`tests/test_lived_day_chain_v04.py::LivedDayChainTest::test_one_instance_reads_parks_picks_up_goes_out_and_resumes_the_same_activity`

真实Supply/WorldBodyService/Resolver/AG1/AG2/AR0/LR4/LR5跑通：

1. 读完3页，到真实 `READING_EXHAUSTED` 停点。
2. 明确WAIT park，Activity为WAITING。
3. PICK_UP到真实手槽，再PLACE到当前房间；两条Activity均经 `COMPLETION_ELIGIBLE` 到 `COMPLETED`。
4. 真外出旅程到 `street_front`、返回家，再结束300秒停留。
5. owner发布材料下一版，LR4只开放恢复资格，不自动恢复。
6. 明确RESUME同一Activity，再读新版第1页。

最终正式链 `Ran 1 test in 34.1s ... OK / exit 0`；没有特殊测试router，没有替换正式resolver。**决定为原始JSON脚本，旅程/停留时钟注入，不是Gemini自主或现实一天。**

过程中修掉三个承重问题：WAITING误判占用导致选拿放却写作品；正式Journey resolver没委派拿放；完成证据未绑到Activity的target_refs。没有新增owner或放宽UNKNOWN/幂等语义。

## 原实例实际Gemini运行：失败，不是主动安静

证据：会话scratch `original-live-day-1792420720821833725.json`。

- 原实例 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，真实resident，运行上限600秒、至多6次新增请求，受既有更低预算约束。
- 包含启停的实测墙钟612.26秒；本阶段实际新增1请求，原 `run:one-day` 账本3→4，未重置/重开窗口。
- 模型保持 `gemini-3.8-flash-high`，端点未换。
- wake为 `DECISION_FAILED / DECISION_ATTEMPT_FAILED:MODEL_PROVIDER_ERROR`，没有合法Decision和动作；之后NOT_DUE不是她主动休息。
- 外层READY/exit0只证明有限入口结束，不代表认知成功。Supply/World子进程停止、lease FREE。
- 正式装配未传cognition observer，历史日志没有更细HTTP错误元数据；不能指定是鉴权/超时/503。未额外重跑600秒制造通过。
- 原实例Memory仍UNBOUND，未擅自启用。

## 最终正式接线与只读核对

原实跑summary地点名为null，查明正式 `resolve_world_provider` 未传实例environment/world_home。现在传入当前env和 `WORLD_HOME_ENV=(CHIYO_WORLD_HOME,HERMES_HOME)`，优先显式世界目录。

新增 `tests/test_world_provider_instance_binding.py` 两项：正式factory构建真实provider，并验证非默认实例env/home抵达catalog reader；覆盖world home优先、profile回退。transport readiness/catalog reader是替身。Python/WSL退出码均0，AST PASS；证据 `lived-day-binding-check.json`、`lived-day-binding-tests.log`。

随后不启服务、不调用模型，使用产品 `load_phone_instance/read_nested_life/map_life_environment` 读取原实例完整绑定，再读真实目录：`available`，当前位置home，名字“临海的住居”，另有浪打道、松原公园、文浜堂书店等12处，exit0。证据 `lived-day-real-catalog.json`。前一次只带目录、不带世界包的探测返回WORLD_NOT_INITIALIZED，记录保留，不作为原世界损坏证据。**工厂接线回归+真实目录可读，不等于修复后live summary已重新验证。**

## 必要验证与未完成项

施工记录：核心26项、真实拿放12项、有限入口16项通过；最终链相邻Body14项、消费6项通过，详见三份施工报告。父级只补最终接线2项与AST、只读目录、笔记tree/format和diff检查，不重复全库/SHA巡检。

当前其他测试失败仍保留于施工报告（包括continuity helper签名、continue_from_ref绑定、旧项目/WAIT断言等）；未建立等价基线，不归因为“历史无关”，不宣称全库绿。

主要剩余限制：真实模型提供方失败未解除；修复后live现场显示未重跑；真实24小时/多日覆盖、自然生活选择与长期形态尚未验证。没有NPC社会/多模态/迁居交付。既有单前台是当前底座限制，不宣称实现设计所有并行参与形态。

## 文件与交付边界

- [核心结果](v04-lived-day-core-result.md)
- [World日常结果](v04-lived-day-world-result.md)
- [有限入口结果](v04-lived-day-runtime-result.md)
- [设计进度](v04-design-progress.md)

本地未提交开发；HEAD保持 `41e818faaa24115fbf43db2977b15585c04144af`。不发布、不部署、不清理原实例、不退款/重置账本、不改产品模型、不擅自绑定Memory。原业务历史保留；没有重新核验旧钱包余额，不把旧值当本轮测量。

最终门禁实测：68篇notes tree/relative links与format均exit0，`git diff --check` exit0；
保护文件状态空、暂存空、tracked deletions空、stash空，HEAD/分支未变。
证据：会话scratch `lived-day-final-gates.json` 和 `lived-day-final-gates.log`。
