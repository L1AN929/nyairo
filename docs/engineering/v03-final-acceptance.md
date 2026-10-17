# NyAiro v0.3「后端连续生活版」最终限定验收

## 1. 结论与停止状态

**PASS：本地候选在下述机制/集成范围内通过最终验收；功能施工停止。** 本版不再接Future/Agenda生活接线、UI或完整设计ZIP。这里的v0.3是有界版本定位，不是已创建的GitHub标签、release或部署。

候选：`D:\nyairo-v03-backend`，分支 `work/v03-w01-navigation`，HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`。功能代码和新文件仍UNCOMMITTED；cached为空，无commit/tag/push/部署。对比基线及更新见 [v0.3 对比 v0.2](v03-vs-v02.md)。

**环境**：WSL Ubuntu、现有Python3.12 venv、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONHASHSEED=12345`。真实本地Life/World/Body/Supply/Memory owners；模型为确定性替身或127.0.0.1 loopback服务，不调用真实产品模型。Life/营业事实显式注入时钟；正式World travel回执仍使用owner墙钟，旅程测试按owner公布的due结算，**不是所有owner统一注入时钟，更不是真实过完一天**。

## 2. 冻结对象

产品writer与验证返修writer均结束后执行最终矩阵。以rc3发布commit到工作树的变更路径加untracked新路径生成有界清单，不复制仓库、不全树SHA：

- 清单57个成员：包含Python产品/辅助入口与`daily_neighborhood_v1`九个包成员，也包含3个组件内测试（`components/world/service/test_action_intent.py`、`test_world_body_bridge.py`、`components/worldgen/tests/test_edited_world_runtime.py`）。即54个产品/数据成员 + 3个组件测试，不将全部57称为产品源文件。
- scratch `v03-final/product-freeze-before.json` 与 `product-freeze-after.json` 相同；受运行器影响的两组复验前后也与同一before清单相同：`PRODUCT_FREEZE_STABLE=True`。
- 不包含全部测试/笔记/文档/vendor/website的全树身份，不能当仓库release封印。最终文档增补不改已测产品字节。

## 3. 最终矩阵：51项通过，0失败，0错误，0跳过

每组独立WSL子进程，以`subprocess.run(...).returncode`记录，不通过tail/管道洗绿。表内为unittest计时，不含WSL启动成本。用例精确名称完整保留在matrix/retest JSON；下列不把测试数当能力完成百分比。

| 分组 | 数量 | 用例执行秒 | 子进程exit | 证明的核心边界 |
|---|---:|---:|---:|---|
| navigation | 6 | 6.701 | 0 | 动态目标/室内入口、计时只发首段、歧义拒绝、持久化失败不发、world身份不匹配拒绝、status零结算 |
| send-budget | 8 | 22.625 | 0 | 真loopback发送在暂停确认后为0、响应等待锁外、无协调transport拒绝、decision/content/retry逐次预算、重启/跨日/回拨不洗额度、伪授权拒绝 |
| private-context | 8 | 4.608 | 0 | Body≠请求预算、过时身体不冒健康、评价纠正/语气不造情绪、撤权拒写、收藏不取文、页码不当理解、弱自由文本不强改下一步 |
| consume-recovery | 5 | 1.958 | 0 | 真gateway消费、缺授权拒、坏经济JSON拒绝、已提交消费重启Body恰一次、物件缺席但无receipt不可归因 |
| opening-expectation | 12 | 4.878 | 0 | 原营业规则的开闭/周末/跨日/常开、aware时间/不可读failclosed、有限窗口/源指纹、目录NOW_ONLY不升级/零World写 |
| owner-recovery | 2 | 32.925 | 0 | 在途pause后真新PID/新socket同data root恢复，已发可到/新段不发；丢回执提交一次、落账后同execution认领 |
| continuous-life（修正runner后） | 3 | 158.830 | 0 | 一次同实例10条正时长边、多地点生活/回家续原作品，停留自然/提前退出分离，不合法退出/关门续坐拒绝 |
| memory-restart（修正runner后） | 7 | 23.225 | 0 | World记忆中性且UNKNOWN不假成功、真实准入行改变合法选择、配置经验真进请求、两个新runtime/PID续同Activity/预算/书签 |

### 关键连续链实际发生了什么

同实例从安静0模型开始，私人作品真实出新版本；有限共同时间沿合法safe-boundary把原工作置WAITING并保留checkpoint。随后home→front→corner→park→corner→cafe→corner→front→reading_point→front→home，共10条真实正秒单跳，声明走时1320s但不是实睡22分钟。

- 第一段注入丢答复，同Action/execution对账，不发第二个START。
- 公园停留START→未到时ONGOING→自然END，真实Activity完成；咖啡店关闭时合法ABANDON，实际180s/计划300s/剩余120s，`completed:false`，不发待满checkpoint；拒绝不合法退出和关门强CONTINUE。
- 到阅读点才出现READ_HERE机会；本链不宣称已经读懂书或执行了阅读点的阅读活动。
- 回家RESUME原资格和checkpoint，CONTINUE落真实Supply新版本；公园World经历经原handoff准入M0并进入后续请求。另有记忆因果负控证明只有真正准入的World行改变下一合法选择，空/弱/作品对照不冒充。
- 消费在单独真实owner组验：一实例一次、原子耗尽+可归因receipt、Body只是2秒轻操作，不是营养/饱足/恢复。

## 4. 红灯与运行器更正（保留，不覆盖历史）

### 街区owner丢回执首轮

父级首轮6项255.130s，5绿1红、真实exit1（`parent-neighborhood-acceptance.log`）。旧测试只等客户端超时便放行屏障并立即要求owner已经提交；真实提交约0.9s，比客户端0.4s慢。另一个错误是假设accept当次即可认领，而提交未落账时正确状态是非终态UNKNOWN。

原作者仅修测试：屏障同时等客户端确已放弃及owner确已落账，分提交故障/唯一提交/随后推进认领三阶段断言，未放松owner identity/次数/位置断言。故障精确ID和1.5s慢提交压力通过，作者同命令6项194.551s通过；父级最终owner-recovery组已独立再证真实exit0。产品未因此修改。详见 [owner链结果](world-neighborhood-owner-chain-result.md)。

### 最终矩阵首轮：父级运行器错误

首轮前6组41项通过；continuous-life 3项和memory-restart类收集13项出现失败，最终矩阵exit1，原日志与JSON均保留。父级为“禁物理删除”把所有unlink都变成no-op，错误地让正常提交后应退出活动路径的`commit_intent.json`继续留在原名，触发AG2 `RecoveryRequiredError/INVALID_COMMIT_INTENT_SCHEMA`。这是测试环境破坏持久提交语义，不是产品新缺陷。

更正只在scratch：继承tearDown的rmtree继续no-op，socket仍留原位；隔离fixture内正常退出活动路径的普通文件通过唯一名rename保留，原路径/保留路径/PID写入`retained-retirements.jsonl`（本次946条），不删除字节、不改断言。用`PYTHONPATH`的sitecustomize让真正新Python子进程也遵守留证规则。重启改选精确新增ID，避免TestCase继承顺带多收旧测试。

只重验两组：continuous-life 3项158.830s、memory-restart 7项23.225s，均exit0/零跳过；同一产品清单未变。这不是正常物理清理行为验证，也不是把首轮exit1改写成exit0。分组最终结果为51个不同选定用例通过。

## 5. 证据位置与复查命令

会话scratch根：`C:\Users\<user>\.pi-desktop\scratch\966ed2b0-27ad-4c2c-a4b3-9021a8121e37`。

- `v03-final/matrix-results.json`：首轮八组原始结果；`retest-results.json`：仅两组更正运行器后的结果。
- `navigation.log`、`send-budget.log`、`private-context.log`、`consume-recovery.log`、`opening-expectation.log`、`owner-recovery.log`；最终另两组用`continuous-life-runner-fix.log`和`memory-restart-runner-fix.log`。原同名首轮红日志保留。
- `product-freeze-before.json`、`product-freeze-after.json`、`retained-retirements.jsonl`、`gates.json`。
- `v03_final_matrix.py`、`v03_final_test_runner.py`为首次运行代码；`v03_retest_two.py`、`v03_final_retest_runner.py`、`v03-retain-hooks/sitecustomize.py`为更正后复验入口。不要把前者全局no-op unlink当可复用正确runner。
- fixture全部在`/tmp/nyaw05r.jdK3q`独立子目录保留，新进程恢复使用新socket而不是清理旧socket。

已执行：`py -3.13 "$env:PI_SCRATCH_DIR\v03_final_matrix.py"`首轮exit1；`py -3.13 "$env:PI_SCRATCH_DIR\v03_retest_two.py"`两组复验exit0。

## 6. 门禁与剩余边界

最终矩阵时27篇notes树/格式各exit0、`git diff --check`0、cached空、HEAD未变；最终交付文档后再次核验结果见管理入口。未stage/commit/tag/push/release/打包或部署。

**没有本版范围内未解决的测试阻塞**。以下是明确不纳入成熟声明的缺口：

- 新链路未用真实产品模型验证；没有真实24h/整天soak；长时间行为质量未知。
- 不交付统一Web生活/模型配置台；不新增真实外发、购买、NPC自主社会、多前台并发、营养生理或完整ZIP。
- Body正向AVAILABLE事实和生产S07来源仍未具备；短期评价只是可撤回消费者。
- 营业预期只是只读API，未接Life Agenda；没有到点自动想起/重新决策完整链，`requires_recheck`是消费方契约，不是已经执行的重查。
- 停留PAUSE不提供“暂停区间扣除后恢复计时”，area粒度未证；旧blocked journey不自动迁移。
- 正式World travel回执墙钟耦合、跨run目录授权注册表需对齐、新socket恢复范围均按owner回执如实保留；不宣称生产resident整服务重启已全面验证。
- 安装/打包/线上迁移、真实部署配置与安全运维仍需要独立授权和验收。

完成此报告与更新说明后停止施工；不自动进入v0.4。
