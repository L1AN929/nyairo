# 单World候选匹配 · 父级独立验收

日期：2026-10-22。结论：本工单的最小防误送修复通过离线验收。非World候选在单WORLD分支的World活动形状/step构建前被既有拒绝机制挡住；合法World候选仍可承接。不代表私人机会具备承接路径、不证明历史故障全部因果已定，也不代表G1或完整生活通过。

## 修改和边界

唯一业务writer由dispatch_worker.py直接派出，single-world-guard，日志 `.agents/worker-runs/20261022-224512-single-world-guard.log`，后台bknxtbjjo exit0。父级未通读components实现、未直接写业务代码。

worker交付：
- `components/life/scripts/autonomous_life_cycle.py` 窄改+35/-3：新增_require_world_candidate，复用_selected_candidate/_candidate_is_world五类目标前缀；单WORLD step与adoption分别用_world_step_for_decision/_world_adoption_for_decision先匹配再构建。非匹配/不可读沿用AutonomousLifeError及既有拒绝文本，不重决策、不虚构目标、不写回Decision。
- 新 `tests/test_single_world_candidate_guard.py` 6例离线正负控。
- 新bug-fix note `2026-10-22-single-world-candidate-guard.md`；既有路由feature note仅+3行事实同步/互链，原决定保留。
- worker结果报告与限定测试日志。

worker明确未改多路径_routed_step_builder/_RoutedAdoptionSpec，避免复用它们的私人内容fallback而扩大工单范围。合法World候选预检多读一次；这是防误送门，不是新的候选可执行性权威或承接功能。未改provider/预算/调度/Body/Memory/服务端契约，未启动真实主实例或重放旧START。既有dirty保留，不stage/commit/push/deploy。

## 父级实际复跑

WSL仓库执行，未source产品密钥：

`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_single_world_candidate_guard tests.test_world_daily_opportunities tests.test_world_daily_life_consume tests.test_world_body_life`

**49例，46.699秒，OK，shell exit0。** 原始输出 `.agents/worker-runs/20261022-single-world-guard-parent-tests.log`。包含新6例及既有3模块43例，父级一次通过。

具名覆盖：
- 正式evaluate_once唯一WORLD场景选私人候选，DECISION_FAILED、World step/adoption和私人作品/阅读/项目builder零调用，决定不被改写、不重试、无提交。
- candidate不可读时诚实拒绝，不取第一个世界目标兜底。
- 合法World得到place:park/MOVE与GO_TO_OBSERVED_PLACE形状；合法World extras按原谓词接受，不只看candidate_kind。
- 五类目标命名空间兼容；纯artifact/dopp/空refs拒绝。
- 多路径仍AR0_WORLD_ROUTED并正常构建World步骤。

父级从仓库根独立运行write-notes三门禁：tree86、format86、archive0，全exit0。读取新note核对：implemented现在时、备选与代价、只防误送的未验证边界齐备。旧feature note互链由tree门禁核对。

写集tracked `git diff --check` exit0；新测试/笔记/worker报告尾随空白检查无错误。diff --stat显示本轮新增的tracked业务范围为cycle和指定旧note，暂存区空；既有AGENTS/RUNBOOK/memsearch/NOW等其他会话dirty保留，不用范围相同证明逐字节不变。

父级短Python直接只读主实例原账本，不装配runtime/不开服务，实际exit0，run:one-day.calls仍12，证据 `.agents/worker-runs/20261022-single-world-guard-parent-ledger.json`。加历史旧预检1，共同13/96余83。本轮产品请求新增0；施工worker调用与产品预算不同，不称免费。

## 失败历史

worker报告首次既有43例整组出现consume用例一次NO_ACTION / ABANDON_NONTERMINAL_CHOSEN，发生在多路径AG1决策阶段；随后当前版单例、HEAD单文件临时隔离对照以及两次整组均通过。父级此次49例一次全过。原红灯保留，不能据未复现称已定位波动根因或完全排除回归。

## 当前停点与后续路线

本工单结束，不追加真实模型、主实例运行、全库、浏览器或24h。测试不是浏览器可验证变更，本轮不启动preview服务。历史START的完整target_refs/step_path仍未知，修复不证明历史选择正确，不扩私人机会承接。

收尾重读最新NOW和检查点，保留另一会话加入的design_audit与路线裁定：0.4继续v2.0 EP-01～22，v3.0封存；不再以“跑更久/调用更多”当验收目标，按记忆绑定→G1逐场景证据推进。该路线来自现有磁盘状态，本工单没有实施记忆绑定或G1新工程。

Memory UNBOUND、understanding UNKNOWN、EP09跨备份恢复FAIL、未做24h等边界不变。没有新的实跑预约；下一步按最新NOW及检查点接续，不自动开下一工单，不再询问“要不要继续”。
