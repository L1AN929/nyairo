# NyAiro 0.4：等待恢复、作品收尾与原实例模型有限运行

## 当前结论

以 `D:\CHIYO-总体设计-v2.0-final.zip` 为标准，软件仍为0.4，唯一开发树为 `D:\nyairo-rc1-body-backend`，沿用rc1底座41e818f与全部此前未提交改动。没有重新下载/覆盖、提交、发布或部署。

等待/收尾的父级隔离真实owner两进程链通过；最终gen3还覆盖每tick先维护、NOT_DUE不花钱、同tick资格可见。原实例已用用户指定 `https://gw.929711.xyz/v1` / `cn:deepseek-v4.1-flash` 真正进入Life请求链：首轮INVALID_JSON保留，输出分类修复后自然窗口新增一次请求，仍为OUTPUT_TRUNCATED，未形成有效决策。有效真实决策是当前具名阻塞，不因外层exit0、合成诊断JSON通过而称成功；不继续刷调用或自动抬上限。

## 分包与精确证据

- C：`v04-wait-closeout-core-result.md`。WAIT仅对当前ACTIVE前台的作品明确no_change或当前材料READING_EXHAUSTED提供真实head依赖；撤权/UNKNOWN/技术不可用不制造WAIT。既有runner维护零模型扫描到期与owner依赖，OPEN eligibility不是自动RESUME。20条waiting+6条closeout机制测试通过；父级gen1又发现真实路由缺参，DeepSeek最小修复并新增1条精确回归通过（`/tmp/c-gen1-fix.log`），未刷旧26绿。
- W：`v04-work-closeout-owner-result.md`。真实Supply已有archive、caller artifact revision CAS、原operation key、三份owner归属证据；14条新增用例在WSL通过，Windows13条owner用例明确skip。AR1现有FILE evaluator结算谓词仍为artifact_written_verified，专用archive attestation必须先经operation/receipt/readback验证，不仅看ARCHIVED。
- R：`v04-life-report-coverage-result.md`。report复用resident_coverage；12条新测试、3条精准旧测试及CLI探针通过。具名旧report夹具补真实闭合账本后，唯一ID单跑1.711s exit0，见 `scratch/r7-one-day-report-ledger-20261027.log`。不再把journal墙钟跨度签成覆盖；计时阈值不等于SOAK_VERIFIED。

上述测试由DeepSeek执行，父级不重复整组；父级独立链如下。测试替身选择/内容只证明机制，绝非自主选择。

## 父级真实owner链与失败 chronology

| 代次 | 实际结果 | 证据 |
|---|---|---|
| gen1 | 首步WORK在真实路由报 `_selected_candidate() missing decision_record`，未进入WAIT；原红证保留。DeepSeek将误写staticmethod改为绑定实例方法，并补精确回归 | `scratch/wait-close-first-1792372495284357085.log` / `wait-close-worker-first-1792372495284357085.json` |
| gen2 | 父进程及两个worker exit0，8checks true，owners停止；fixture保留 | `/tmp/day-life-parent-wait-close-2375-1792372681532234709`；`scratch/wait-close-result-1792372681532234709.json` |

- worker PID2384/2542，同 `run:wait-close-parent`。
- 明确START作品→writer no_change→明确WAIT；owner Activity `actv_fe48d996943c9869` 变WAITING（兼容outcome仍叫PAUSED，不能混同真实PAUSE）。同head维护 checked1/unchanged1/evaluated0，无eligibility，无模型请求。
- 父级通过真实owner CAS提交新版本后，维护 changed1/evaluated1，条件 `rcond:e25da07f78af95cd` 满足、资格 `relig:ac365ccafeff1cd8` OPEN；Activity仍WAITING，maintenance llm_calls0/auto_resumed false。
- 新明确RESUME，恢复同一Activity并真正续写；明确ABANDON释放前台后，新明确START收尾。动作 `actn:672780b26da5397f` 走AR0→真实archive→AR1→LR5，收尾Activity `actv_93d7a02676afdd8d` 为COMPLETED，terminal_reason_code=COMPLETED_WITH_EVIDENCE，completion_evidence_ref=`aevid:4db2255f3f8038bc`。
- archive没有产生新内容版本，材料/PDI head不变、兄弟作品完整保留。第二进程canonical/head历史相同，已归档bound work返回NOT_RESUMABLE且不进入候选。
- round budget 0→5，第二进程5→6（第二进程的明确NO_ACTION占一次轮次）；不是“重启零调用”断言。选择序列START/WAIT/RESUME/ABANDON/START，均脚本替身。

## 原实例有限真实模型运行：首代失败，历史保留

原root `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，没有prepare/reset。启动同root既有Supply/World和phone，清单先备份为 `instance.json-backup-1792372735731635671`，只更新serve代次；不把备份覆盖后来历史。Life使用真实resident，injected_factory=false、owners_verified=true、同 `run:one-day` 与原state root/预算。

安全证据 `scratch/original-life-bounded-1792372735731635671.json`：preflight READY、blocked[]、Memory UNBOUND可选降级；真实model_calls1，但DECISION_FAILED/INVALID_JSON，actions0、owner_confirmed_versions0。持续约19.5秒，不是正常一天。请求账本days[2026-10-19]=1、windows[run:one-day].calls=1，失败没有退回额度或新建run window。

钱包前后2860JPY/4交易、所在地home/living、双手空、物件substrate revision21及摆放保留。本父级reading HTTP摘要取错层，字段为null，不能以该摘要证明阅读状态；不扩大为重写原owner数据。原先正文/批注不进运行报告，Memory未绑定。四个本次PID2576/2578/2579/2587已通过/proc不存在确认退出。

## 验证边界与纪律记录

- 不将短链、注入时钟、模型连通或process exit0当正常自主一天。通用PAUSED前台恢复限制、完整未来认领/改期/取消、Memory因果参与、正向实际REST恢复及真实24h仍未交付/验证。
- 没有全树SHA或旧全量回归，没有发布/外发/真实支付。fixture/失败/旧socket保留。
- 报告旧测试代理自报执行过 `rm -rf /tmp/nyaw05r.jdK3q/r7-pass-probe`，称该自命名探针目录当时尚不存在；该命令违反明确禁删指令。父级未见既有内容删除的证据，但不能据此签发“整个文件系统零删除”。保留此纪律例外，不隐去。
- 本轮最终门禁按末节记录；此前53篇/15模块检查只属于原观察时点，不充当最后tick返修的证据。

## 模型解析定点补证（等待自然窗口复验）

DeepSeek最小修改cognition adapter/model client/transport，新增7条精准回归通过；旧行为对照5条FAIL/ERROR，受影响cognition reliability 82checks通过、零外网请求。严格AG1枚举/候选/理由码与每epoch付费边界不改；reasoning只取长度、永不当答案；截断/仅reasoning/空输出/请求拒绝不再统称INVALID_JSON。决策输出线上上限由512至少2048，原请求次数预算/run/timeout未扩大。详 `rc1-thinking-decision-invalid-json-result.md`。

父级两次真实结构诊断（合成候选、无owner动作、单列诊断调用，不计作Life原账本准入）：`scratch/rc1-thinking-shape-1792373842086106530.json`。同模型下2048：HTTP200/stop/content330chars/reasoning2433chars、completion700含reasoning610、JSON对象true；512：HTTP200/length/content0/reasoning2061chars、completion512全为reasoning512、JSON对象false。复现并支持输出上限耗尽路径；历史首轮响应未保留，不能由此宣称从历史原响应直接证明唯一原因。

原实例修后gen2一次tick约12.6秒，安全报告 `scratch/original-life-bounded-1792373861935015615.json`：无新增请求，累计model_calls仍1、账本仍1，只有silence计数增加。不是有效决策复验，不把累计计数当本次新调用。本次PID2821/2823/2824/2832全部退出，钱包/物件仍相同。

下一自然检查由原last_check_at=2026-10-19T01:19:05.120856Z加原30分钟间隔决定，父级仅等待到原窗口后一次tick，最大等待900秒；不改间隔、不清事件/预算、不造新机会，等待期间不起World/Life。最终结果另记。

窄结构检查：notes tree58篇exit0；format首轮因W note缺Alternatives/Consequences规范节而exit1，父级补已有取舍与标准标题后format58篇exit0；本批15产品模块AST exit0；diff-check0、保护文件0diff、index空、tracked删除空，HEAD仍41e818f。后续仅管理文档与scratch改动不要求重复旧测试。DeepSeek另自报import探针留下26个忽略的pyc，未物理清理；不签发全文件系统零生成/零删除。

## Tick接线定点补正（已闭合）

父级在等待自然检查窗口时发现：C初版仅在runner.wake内maintain，assembly.runner_tick先poll feeds后run_due，导致NOT_DUE且无其它事件时不维护，资格feed又早于维护。手动cycle.maintain_waiting的gen2真链不覆盖这条自动接线。已按精确cmdline停止父级自己的自然窗口等待PID2862，避免半写启动原实例；产品owner服务没有因此启动。C定点接“每现有tick先维护→feed→due”，新增精准负控，禁止新timer/自动恢复；交付后再有限复验，不把初版机制链扩大成自动tick已验。

## 最终gen3：真实tick接线通过，真实模型有效决策仍阻塞

C已接每现有tick先一次维护→poll feeds→due，并以maintain/maintained显式接口避免同tick重复维护。两条新回归 `WaitTickMaintenanceTest` 在 `/tmp/c-tick-order.log` 为Ran2/OK（0.791s），没有重跑旧27条。

父级最终链 `scratch/wait-close-result-1792391058305466433.json`，fixture `/tmp/day-life-parent-wait-close-155-1792391058305466433`，worker PID164/322，两个worker及父进程exit0、8checks true、owners停、夹具保留。资格feed在本探针只保留真正ResumeEligibilityFeed，以隔离本条性质；非伪造资格。same-head正式runner_tick：due=NOT_DUE、checked1/unchanged1/evaluated0、无events/无eligibility、cognition和round不变。真实owner CAS推进head后下一正式tick：changed1/evaluated1、条件 `rcond:893124734fdd2316` 满足、资格 `relig:ed863cb33bbac8f6` OPEN，同tick事件RESUME_ELIGIBILITY_OPENED驱动一次明确RESUME，恢复同一Activity `actv_a4d60c694c8999cb`。两个tick共scan_due_agenda2次。归档收尾Activity `actv_83f3d557ca48b06c` 为COMPLETED/COMPLETED_WITH_EVIDENCE；材料/PDI/兄弟作品及跨进程历史不变。预算0→5→5→6，选择为测试替身，不是自主一天。

原实例自然窗口已到后，第三次有限tick证据 `scratch/original-life-bounded-1792390937392473043.json`：约25.572秒，preflight READY，新增请求1（before1/after2），真实结果DECISION_FAILED/DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED。2048在合成诊断上够用，不代表原实例实际上下文也够；本次实际链仍截断，未决策/未动作/未新owner版本。不加上限、不改模型、不清窗口继续刷。原run:one-day账本days=2/windows.calls=2/token_count2，opened_at仍2026-10-18T09:41:48Z、closed_at null，失败没有退额度或重开窗口。

本次清单备份 `instance.json-backup-1792390937392473043`；钱包前后2860JPY/4交易、物件完全相同、原房间状态保留。PID110/112/113/121均/proc不存在，服务停止。原reading摘要此前取错层的局限不变，不从null字段外推owner历史不变。安全最终字段汇总 `scratch/v04-wait-closeout-final-facts.json`。Memory仍UNBOUND可选降级，真实24h/正向实际REST恢复/完整未来组织/通用PAUSED恢复均未完成。

## 最终窄门禁

最终tick返修停写及gen3完成后，从repo根实跑notes tree/format：各58篇exit0；assembly/runner/新tick测试3文件AST exit0；git diff --check exit0。MANIFEST/两个move保护脚本0diff，index空、tracked删除空，HEAD仍 `41e818faaa24115fbf43db2977b15585c04144af`。JSON为 `scratch/v04-wait-closeout-final-gates.json`。没有旧全套回归/全树SHA/发布，不以这些窄门禁证明全库无缺陷。当前停止在真实模型OUTPUT_TRUNCATED这一具名阻塞，未继续加预算/刷调用。
