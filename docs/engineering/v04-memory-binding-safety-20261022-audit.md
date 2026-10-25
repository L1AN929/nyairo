# Memory 绑定设计与验收窄核对（2026-10-22，只读）

范围：承接 rev93，独立核对 v2.0 EP-09 记忆绑定的承重墙、验收边界，给出可派工的小任务范围。
不是重做 Memory，不修跨备份防复活，不是 G1 全量施工。v3.0 未读未引用。
本次未运行任何测试、实例、服务或真实产品模型；所有"既有证据"均为文档/笔记转述，不是本次复跑。

## 结论

1. **绑定 READY 只证明接线成立**：实例自己的 `evidence.db` + 独立 `controls.db` 已建、可读、健康，
   三个环境名已写入该实例的 `instance.json`，且没有启动服务、没有动 run/预算。它不证明记忆被召回、
   不证明记忆影响选择、不证明 G1、更不证明跨备份恢复安全。四件事必须分开报告。
2. **原实例当前仍是 `UNBOUND`**（NOW 与 `2026-10-27-v04-memory-product-service.md` 两处一致），
   本次未连实例复核，按文档事实引用。绑定是一次可显式执行的操作，不是已发生的事实。
3. **旧 V-C 的 FAIL 不能被新绑定洗白**：恢复"删除前 evidence + 删除前 controls"后正文复活，
   是当前库+当前账本范围内已成立的实测事实；设计 17 号把"restore 不复活"列为 Stage0 退出十硬门之一，
   属于独立契约问题，绑定既不修复也不遮蔽它。
4. **推荐方案 A**：复用既有显式入口 `phone.life.enable_instance_memory`，用 0 产品调用的操作验收覆盖
   绑定本身；只有在验收中撞到具名缺口时才走方案 B 补最小有界工具。不新增恢复权威、不新增 owner。

## 设计与历史约束

原文（设计 2.0，仅读到以下命中段）：

- `14-研发工作包与验收体系.md:23`：EP-09「记忆准入、回忆与删除」，依赖 S05/S03/S14，验收口径为
  「分层记忆、有限召回、同根识别、删除屏障」。这是 EP-09 在设计里唯一的任务级条目，没有更细的
  绑定步骤或"同实例启用"字样。
- `15-产品记忆与自主机制完整设计.md` §5.1（ORIGINAL L362–L394、L1674–L1730、L5075–L5106）：
  记忆不是聊天全文复述；**现行状态不能由旧 memory 签发**（位置看 S01、新信念 S03、当前活动 S09、
  承诺 S08、偏好 S06）；admission 走 raw event→episode→有证据 experience 的层级，不要求逐级晋升。
- 同文件 §5.3（ORIGINAL L1778–L1812、L5188–L5221、L5249–L5272）：删除、衰减、降权、压缩、归档、
  不主动召回、Timeline 索引保留是**七种不同退出**；删除立即 tombstone 阻检索，再传播到附件、摘要、
  Timeline、Reflection、作品引用、派生 belief 和**备份恢复**；不可因自传连续性私留残影。
- `17-工程契约与证据完整设计.md:102`（ORIGINAL L10942–11020、L15155–15241）：Stage0 退出十硬门包含
  「无源 Memory 拒」「无 owner participant 拒」「派生删除全传播」「**restore 不复活**」「无 writer 旁路」
  「区分 subject/run/branch/epoch」等，证据等级至少 EL3；历史状态为 STAGE0_PARTIAL/NOT_PASS。
- `05-阶段性发展路线图.md:33-34`：出口门 G1 是「完整端到端含中断、重启、UNKNOWN、版本冲突和 NO_ACTION；
  下一次可以继续真实作品」，证据要求 EL3 + 限定范围 EL4，参考 unit PASS 不足。

既有证据（本仓库，非本次复跑）：

- `2026-10-27-v04-memory-product-service.md`：`enable_instance_memory(root, owner, operation_key)` 在
  `<install>/memory/` 建 `evidence.db` + `controls.db`，先备份 `instance.json`，原子替换后只写
  `NYA02_MEMORY_DB/_CONTROLS_DB/_SUBJECT`；幂等、不覆盖已有显式绑定、不重建坏库、不接管半套文件、
  不动 `CHIYO_AUTONOMOUS_*`、不启动服务。读路径全部 `sqlite3 mode=ro` + `quick_check` + controls 投影；
  非 READY 一律 `experiences=None` + 具名原因码，不返回空数组。consumer 的 owner 只取 `port.subject`。
- `2026-10-24-v04-phone-life-memory-preflight.md` 与对应用例：记忆绑定只从**嵌套实例**的
  `instance.json` 读出，操作者 shell 里 export `NYA02_MEMORY_*` 不能绑定；预检只读、不建库。
- `v04-memory-vc-20261021-result.md`：DELETE 后 read/list/context 三处扣留（A 变体：只恢复旧 evidence、
  保留最新 controls，仍扣留）；B 变体（evidence + controls 一起回滚）正文复活，脚本真实 exit 1，
  `V_C_RESTORE=FAIL`；controls 缺失/损坏为具名 BLOCKED，不是空库。

当前推断（明确标为推断，未经本次验证）：

- **"M0 唯一写口"是本仓库实现对 evidence store + control ledger 的命名，不是设计原文术语**。
  设计原文对应的是"记忆准入"和"分层记忆"；代码里体现为 consumer 只经 port 写入、读路径只经 port 读。
  不能拿"设计里有 M0"当论据。
- 绑定与"记忆参与认知"之间没有自动通路证据：绑定只让库可读，候选/AG1 侧是否消费记忆、
  是否改变选择，本次没有找到限定证据。
- "同实例接续"在绑定语境下只被间接约束：用例钉住绑定不重置 `CHIYO_AUTONOMOUS_RUN_ID` 和 96 次预算，
  但绑定之后的真实 resident 续接未发生，不能由离线用例外推。

## 验收范围

### 1. 三笔账分开记

| 事项 | 由什么证明 | 现状 |
| --- | --- | --- |
| 绑定 READY | enable 回执 + status=READY + 三环境名 + 二次幂等 | 离线用例有，真实实例未执行 |
| 记忆影响选择 | 候选/AG1 侧带来源的真实链证据 | 未找到 |
| G1 | 05 号出口门，EL3+限定 EL4 | 未闭合（NOW 第六节） |
| 跨备份恢复安全 | 17 号 Stage0 exit「restore 不复活」 | FAIL（V-C B 变体复活） |

绑定 READY 不能写在其余三行的"通过"栏里；V-C FAIL 也不能因为"现在还没绑定"被写成"已规避"。

### 2. 既有隔离离线用例覆盖的前置（只列 ID，本次未运行）

注意：`EnableInstanceMemoryTests` 与真实读写用例带 `@owner_bound_only`，Windows 下按门禁 skip，
须 POSIX 环境才有意义。以下均未复跑，仅登记为"可覆盖的前置"。

- 未绑定/健康状态：`tests.test_v04_memory_product.StatusTests.test_unbound_is_named_and_creates_nothing`、
  `test_ready_status_reports_the_bound_owner`、`test_a_missing_database_is_a_named_failure_not_an_empty_library`、
  `test_a_ledger_defaulting_to_m0_is_named`、`test_a_corrupt_ledger_is_named_not_silently_empty`。
- 零写读：`ReadOnlyReadTests.test_reads_create_nothing_at_all`；预检侧
  `tests.test_phone_life_memory_preflight.MemoryHealthTests.test_preflight_opens_memory_read_only_and_creates_nothing`、
  `test_the_check_only_reads_the_names_the_resident_reads`。
- owner 分域：`OwnerDomainTests.test_another_owner_never_sees_this_stream`；
  `ConsumerOwnerTests.test_the_owner_is_the_ports_subject_never_the_handoff_spelling`、
  `test_a_port_without_an_owner_defers_instead_of_guessing`、
  `test_a_persona_port_writes_under_its_own_owner_even_when_the_handoff_says_chiyo`。
- 删除扣留（当前库内）：`GovernanceTests.test_a_deleted_experience_is_not_recallable_any_more`、
  `test_the_deleted_row_does_not_lend_its_text_to_a_neighbour`。
- 初始化与幂等：`EnableInstanceMemoryTests.test_enable_creates_a_backed_up_binding_and_starts_nothing`、
  `test_enable_writes_only_the_three_memory_names_and_keeps_run_and_budget`、
  `test_enable_is_idempotent_and_rewrites_nothing_on_a_second_call`、
  `test_enable_never_overwrites_an_existing_explicit_binding`、
  `test_enable_reports_an_unhealthy_existing_binding_without_rebuilding_it`、
  `test_enable_refuses_a_missing_nested_instance_and_creates_nothing`、
  `test_enable_requires_an_explicit_operation_key`、
  `test_enable_refuses_half_created_files_it_did_not_bind`、
  `test_enable_without_a_posix_store_is_a_named_refusal`、
  `test_memory_health_after_enabling_is_read_only`。
- 坏库/半绑定拒绝与预检：`MemoryHealthTests.test_a_present_database_alone_is_not_health_because_the_ledger_defaults_to_it`、
  `test_missing_database_file_is_blocked_not_unbound`、`test_a_non_sqlite_file_is_blocked`、
  `test_a_directory_in_place_of_the_database_is_blocked`、
  `test_a_different_memory_persona_is_legal_not_cross_bound`、
  `test_an_operator_shell_export_cannot_bind_memory`、
  `test_a_damaged_ledger_is_distinguishable_from_a_missing_one`、
  `test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one`、
  `test_a_genuine_m0_store_database_is_ready`；
  `RunMemoryGateTests.test_run_refuses_when_memory_is_bound_but_unusable`、
  `test_run_refuses_when_memory_health_is_unverified`、
  `test_run_refuses_an_unresolved_memory_binding_before_a_resident_exists`、
  `test_run_records_a_ready_memory_without_claiming_a_model_call`。
- 这些用例覆盖的是"前置与拒绝面"，**不覆盖**绑定后召回是否进入候选、是否影响选择、是否同实例续接。

### 3. 若在同实例启用，最小验收证据

可以 **0 产品调用** 完成：

1. 绑定前快照：`status` 为 `UNBOUND`、`instance.json` 原文与备份基线、`CHIYO_AUTONOMOUS_RUN_ID`
   与 `MAX_TOTAL_REQUESTS_PER_UTC_DAY` 读数、memory 目录文件清单、整树 mode/size/mtime 快照
   （允许变化的路径预先写死：`memory/` 与 `instance.json` 及其 `-backup-<UTC>`）。
2. 带显式 `operation_key` 执行一次 `enable-instance-memory`；记录回执字段
   `ok / created / reused / status=READY / restart_required / service_started=false / processes_started=0`。
3. 绑定后核对：三个名字只新增到嵌套实例 `instance.json`（备份保留原文）；Memory owner 按既有绑定或 resident 身份优先级确定，不能要求等于手机 workspace subject；
   run/预算逐值不变、无 `-wal/-shm/-journal`、无进程被拉起。
4. 二次调用幂等：`reused=true`、`created=false`、整树快照不变。
5. 只读面：`status` 仍 READY；对该空库调用 list/read/context 不崩、不建库，列表/上下文可返回健康空结果，
   不存在的 id 应具名 NOT_FOUND。空库只表示这个新绑定内没有可召回记录，不能说“她没有过去”。这一步只验形状，不验内容。
6. 预检：`life_preflight` 的 memory check 为 READY 且整树零写。

**不能** 用以下方式凑证据：灌合成私文、把旧经历倒灌进新库、为了"有内容"跑更长时间、
把 fixture 里的经历当成她的私人经历。

必须留到后续（需要用户新授权）：绑定后的真实 resident 续接、记忆进入候选并改变选择、
G1 五场景、跨备份恢复。绑定动作本身不应顺手启动这些。

## 方案

**方案 A（推荐）：复用既有显式入口 + 操作验收。**
直接用 `phone.life.enable_instance_memory(root, owner, operation_key)`，按上一节 0 调用清单验收，
不改业务代码。收益：幂等、备份、不覆盖、不重建坏库、不动 run/预算这些语义已被用例钉住，
复用即继承；绑定不产生内容，不会伪造记忆。代价：绑定后是一个空库，"记忆影响选择"仍然无证据，
必须如实写成"接线完成、因果未证"。

**方案 B：只有具名缺口才补最小有界工具。**
例如验收中发现"CLI 无法在保持 run/预算不变的前提下完成""无法只读查询绑定状态"这类
**具名、可复现**的缺口，才补一个只做这一件的窄工具。收益：不提前造架构。否决前置条件：
缺口没有具名复现就不施工；一旦需要改 `instance.json` 的覆盖顺序、`build_binding` 契约或
新增写口，立即停下问，不自行扩权。

**方案 C（不推荐，且超出本工单）：恢复契约/高水位。**
把"恢复旧备份后重放最后一次 controls 权威"做成契约，可以真正闭合 V-C。但这是 17 号 Stage0
exit 的独立硬门，需要新恢复契约与产品决定，不是绑定工单能顺手带的。现在做等于改承重墙。

**推荐 A 的理由**：当前问题不是"没有入口"，而是"入口没有被真实执行过、且执行后的证据边界没写清"。
先花 0 产品调用把绑定这一步做实并留下可复算的回执，比再造一层工具更省、更不容易分叉。
只有 A 撞到具名缺口，才升级到 B；C 单独立项。

## 验收命令

本报告只交付文档，验收命令为：

```bash
python -X utf8 -c "from pathlib import Path; p=Path('docs/engineering/v04-memory-binding-safety-20261022-audit.md'); s=p.read_text(encoding='utf-8'); assert len(s)>800; assert all(x in s for x in ['## 结论','## 设计与历史约束','## 验收范围','## 方案','## 验收命令','## 未验证']); print('SAFETY_AUDIT_OK')"
git diff --check -- docs/engineering/v04-memory-binding-safety-20261022-audit.md
```

实际执行结果见文末。

## 未验证

- 未运行任何测试、实例、服务、探针或真实产品模型；文中所有用例 ID 均为登记，不是本次绿灯。
- 未连接主实例，"原实例仍 UNBOUND"来自 NOW 与既有笔记，不是本次实测。
- Windows 下 `@owner_bound_only` 用例按门禁 skip，绑定与真实读写只在 POSIX 有覆盖；
  本机不能证明启用动作的实际行为。
- 未验证绑定后的真实召回、记忆进入候选、影响选择、同实例续接。
- 未验证跨备份恢复；V-C 的 `restore=FAIL` 按既有报告引用，未复跑。
- 设计 G1 原文在 05 号，EP-09 在 14 号只有一行任务级条目，没有更细的绑定验收条目；
  "绑定 READY 该看哪些字段"是本报告从实现回执归纳的，不是设计原文规定。
- 未读 v3.0 任何文件，未引用其 18 号。
