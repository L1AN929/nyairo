# World目标最短路由 · 父级只读验收

日期：2026-10-22。结论：确认单WORLD执行分支缺少候选类型预检；历史故障有较强分派误送线索，但当时完整target_refs和step_path缺失，因果链未完全闭合。不是代码修复交付，不把worker的CONFIRMED直接作为根因定论。

## 工单与结果

用户将窄范围诊断上限明确调到300轮，禁止产品模型和改代码。父级直接dispatch：

`python -X utf8 scripts/dispatch_worker.py --name world-target-narrow --task-file .agents/worker-runs/20261022-world-target-narrow-task.md --max-turns 300 --timeout 2400 --fallback ''`

后台bvwbetupx exit0，日志20261022-223436-world-target-narrow.log。worker交付报告、evidence.json和53行acceptance.log；有交付可核验，不同于前次60轮无报告。未记录实际用了多少轮，不声称用满或用了某个数。worker称早期落PARTIAL，父级未取得完整工具轨迹，不确认具体落盘轮次。

## 父级独立复跑

严格复跑worker报告第6节同一条WSL纯读命令，exit0，输出保存 `.agents/worker-runs/20261022-world-target-narrow-parent-acceptance.log`。仅输出具名函数/分支短摘录与白名单实例元数据，无服务/runtime/密钥/模型请求。已确认：

1. `autonomous_life_cycle.py:5263-5265` 将world_step绑定为 `_world_step_spec`，`:5310-5312` 在唯一WORLD路径时直接使用它，未做已选候选World类型预检。
2. 多路径分支`:5295-5298` 使用路由builder；具名`:6343`调用 `_candidate_is_world`。与单路径确实不同，存在防误送缺口。
3. `_world_step_spec` 在`:6388-6399`扫描目标前缀place/object/stay/bodyact/dailyact，无匹配则抛本次相同错误。
4. 持久决策`dec:f1ca026164e400f0`为START，选择`cand:a077fcadc6750011`，处置记录为CONSIDER_PERSONAL_OPPORTUNITY / PERSONAL_OPPORTUNITY，source_ref为artifact:artifact:7d39dcca9d824c6583267b5141892199，EXECUTABLE_NOW。上下文observed_world_refs=[]。

随后父级短Python直接只读原 `life_request_budget.json`，实际exit0，run:one-day.calls仍12，证据parent-ledger.json；历史旧预检另1，共同13/96，余83。无新增产品请求；不source密钥、不启动产品脚本或服务。当前lease没有重新测，沿用第四段历史收尾，不冒称新测实时FREE。

## 不采纳的过强结论

worker称已完全确定历史根因、候选不可能有World目标、模型选择正确，证据不足。候选构造为固定source_ref/opportunity_id加合法extras，前两项非World不能排除extras含World目标；历史完整target_refs未知。step_path没有当时日志直证，单WORLD误送是有根据的推断而不是现场闭合证据。

同样，纯内存get/deepcopy不足以排除所有读取异常/并发变化；没有历史直接证据就不能称except一定未触发、多路径绝不可能报错。候选类型是私人机会，不意味着模型选择一定适合当时可执行路径，也不能证明它有合法内容承接路线。父级已在worker报告开头与evidence.parent_review标注这些边界，原分析保留但被批注限定；未另派工、未修源码。

## 最小建议与未验证

推荐修复方向（未执行）：让单WORLD分支也验证已提交决定与World候选相符，沿用既有路由/拒绝机制，不虚构目标、不兜底改模型决定；离线补“仅WORLD可用但选私人候选不进入World builder”和“合法World候选仍可承接”的正负控。

这只能防误送和给出诚实拒绝，不能承诺私人事项能成功继续。私人机会是否缺少合法执行路线、为何世界机会非空但决策上下文World引用为空，仍未诊断；不把这两个问题偷塞进本次只读工单。下一步修复需用户确认，不运行第五段。

本轮无业务源码/测试/notes施工，无离线套件/浏览器/产品验证，只有只读命令和文档/安全证据。git diff --stat与暂存区核对：没有新的tracked业务文件范围，暂存区空；既有dirty保留，不做逐字节完整性保证。Memory UNBOUND、理解UNKNOWN、24h未做、EP09恢复FAIL保留。
