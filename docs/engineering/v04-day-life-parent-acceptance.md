# 0.4 一天生活连续性：父级有限链验收

## 当前结论

**限定真实owner两进程连续链已通过；未证明正常自主活一天。** 当前唯一开发树为 `D:\nyairo-rc1-body-backend`、rc1底座 `41e818faaa24115fbf43db2977b15585c04144af`，此前全部0.4本地改动保留。用户要求是正常连续生活，不是手机页面或零散单测拼盘。

DeepSeek负责产品实现与正式测试，父级负责任务边界、scratch探针和独立真实owner链。没有产品模型请求、没有启动24h常驻、没有改provider/查找密钥/绑定Memory，没有提交或发布。

## 独立链与实测代次

使用会话scratch `day_life_accept_owner.py` / `day_life_accept_worker.py` / `day_life_fixture_retention.py`。隔离Supply socket + WorldGateway + ProductionLifeRuntime + AG1/AG2/AR0，决策和内容明确为脚本替身。每代根 `/tmp/day-life-parent-<pid>-<time_ns>`，失败和夹具不删，事务临时文件仅在该根中改名退役留证；不全局no-op unlink。

| 代次 | 结果与归因 | 日志/JSON（会话scratch） |
|---|---|---|
| gen1 | exit1，父级harness误用同轮动作前阅读缓存判断读尽。改为直接owner读、下一轮再刷新，不改产品 | `day-life-parent-launch-gen1.log`；first `1792332123303504037` |
| gen2 | exit1，产品实错：POSE真提交SUCCEEDED，cycle却用动作前standing snapshot，报STEP_UNSETTLED。B改成动作后fresh owner回读，失败保持unknown | `day-life-parent-launch-gen2.log`；first `1792332184957194160`；定点 `b-gen5-bodyfix.log` 三例exit0 |
| gen3 | first进程exit0、second exit1。阅读→ABANDON→真POSE→ABANDON→作品no_change已通过；重启仍重复NO_ACTION请求。查明no_change选中head缺失/目标来源错误/恒存世界机会使抑制失效 | `day-life-parent-launch-gen3.log`；first/second `1792332458187633914`；定点 `b-gen8-parentfix.log` 八例exit0 |
| gen4 | first进程exit0、second exit1。no_change已绑定真实owner head，但首进程target双artifact前缀、新进程candidate缺失导致target空，处境指纹变化 | `day-life-parent-launch-gen4.log`；first/second `1792333439525094898`；字段差异 `day-life-parent-gen4-fingerprint-delta.log` |
| gen5 | 两子进程exit0，父级exit0，8项检查全部true：独立进程、lease释放、同run、owner heads/Activity历史/阅读停点保留、重启零请求、轮数不重置 | `day-life-parent-launch-gen5.log`；first/second/result `1792334389704106572` |

gen4差异只有target_ref/target_revision/step_reason，世界机会集合、地点、姿态、阅读与作品head相同；修正优先从Activity持久owner记录读取目标并统一domain包装身份后，gen5同条件通过。零请求安静断言保留，未隐藏世界机会洗绿。

## 已获得的有限事实

- 阅读真实提交一页取得/停点/可选批注，材料读尽后由明确决策ABANDON释放前台，未假报读懂或完成。
- POSE经过原IntentExecutor/gateway/Resolver，父级gen3真实canonical观察lying/south，动作后回读标 `re_read=true`。动作成功标签本身不是事实。
- PAUSE在现有owner保留foreground引用，不等于自由切换；A矩阵已按真实occupancy收紧。未清指针、未强制结束、新建平行owner。
- REST主体入口已窄接start/stop+mode=rest；sleep_like在编码和提交边界均拒。低负荷时gateway按owner availability拒NOT_EXPOSED，Body preflight为RECOVERY_NOT_NEEDED；没有制造高负荷或声称真实恢复。
- R把wall跨度、单调在线测量、停机间隔、未知段分离。父级重叠探针发现continuous误报后，定点修成time_ambiguity、continuous=false，unknown区间不双算。代码恒NOT_VERIFIED，不自动签发一天生活。
- 非法退出旧负控曾误兜底合法ABANDON；DeepSeek仅修候选构造，保留拒绝/前台不变/关店不完成断言，单条 `b-gen4-affected-stay-exit-illegal.log` exit0。B早先“All green”开头不成立，纠正已写入其note。
- gen5真实夹具 `/tmp/day-life-parent-2137-1792334389704106572`，worker PID2148/2259，run `run:day-life-parent`。首进程READ→ABANDON→POSE lying/south→ABANDON→WORK no_change→NO_ACTION；次进程QUIET/llm_calls=0，世界机会仍可见。轮数6→6→6，脚本调用首进程decision/work/reading=6/1/1、次进程0/0/0；PDI版本1→2，作品和材料head不变。脚本决策没有真实模型费用，request账本的0不代表模型预算准入已由此链验证。
- 首进程最终作品Activity `actv_ae0ca6403250a1b7` 仍ACTIVE、readiness=WORK_NO_CHANGE_CONFIRMED；没有假完成，安静不是自动退出。两次明确ABANDON的原对象与PDI历史都留存。

## 最终检查与剩余阻塞

最终两进程链已通过。补证五个精确合同中三个合法性/到达用例通过；消费真实owner单例通过（`day-life-final-consume-exact-gen1.log`，exit0）。历史 `day-life-final-exact-gen1.log` 的旧重启负控曾红：要求ACTIVE前台仍提供START，与本批收紧的矩阵冲突。原红日志保留，不倒写成通过。

后续补证：DeepSeek已仅修 `tests/test_life_v1_restart.py` 与对应测试笔记，按真实原work候选证明START不合法后强塞START，要求精确 `DECISION_FAILED / INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`、无Decision/Adoption、head及Activity revision不变，并保留跨进程同Activity续写/run/预算/暂停/书签/Memory空档断言。唯一 exact ID `tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity` 单跑18.084s、exit0；证据 `scratch/day-life-final-restart-gen2.log`。产品源码零改动，未重跑前三绿。旧负控不再是当前阻塞；新批施工与整日目标仍未完成。

最终停写后从仓库根执行notes tree与format：各53篇、exit0；`git diff --check` exit0。13个相关产品模块AST解析exit0，保护文件 `MANIFEST.json`/两个world_move修复脚本与HEAD无差异，index空、tracked删除空，HEAD仍41e818f。这些不证明整个文件系统零删除或全库回归通过。

## 整日运行边界

短时脚本选择只证明机制。历史本批结束时原实例无provider；后续用户已指定 `https://gw.929711.xyz/v1` / `cn:deepseek-v4.1-flash`，安全绑定并单次连通smoke成功，但这不等于Life决策/预算准入通过。Memory仍未绑定（可选降级）；原2860虚拟JPY/四购买/房间/PDI历史保留。真正模型选择、长时间Body后果、整日在线覆盖、故障与安静选择的真实分布仍未验证。无真实24h授权或证据前，不称“已能正常活一天”。
