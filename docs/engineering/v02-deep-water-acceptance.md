# v0.2 深水区连续七天真 HTTP 模型协议浸泡与 12 场景端到端验收报告

- **验收日期**：2026-11-13
- **管辖测试套件**：`tests/test_v02_seven_day_live_model_soak.py`
- **覆盖范围**：
  1. 本地真实 HTTP OpenAI 协议模型服务（`127.0.0.1:<ephemeral_port>/v1/chat/completions`）驱动的连续 7 天（Day 1 -> Day 7）跨午夜状态推进浸泡；
  2. `PhoneService` + `PhoneHTTPServer` 的 `/api/phone/v02/{agency-depth,crossmodal,runtime-migration}` 三大路由回环联测；
  3. 《14-研发工作包与验收体系.md》§5 全部 12 个端到端场景（S01–S12）独立验真。

---

## 一、实跑命令、耗时与零外联红线核验

### 1. 执行命令与结果
```bash
/root/nyairo-v051/vendor/hermes/.venv/bin/python -m unittest tests/test_v02_seven_day_live_model_soak.py -v
```
- **实跑结果**：`Ran 14 tests in 2.111s` — **14/14 全部 `OK`（100% 通过）**。
- **隔离运行保证**：全部状态在 `tempfile.TemporaryDirectory` 临时目录中生成与销毁，零触碰 `/root/.nyairo-day` 线上实例或任何冻结基线代码。

### 2. 本地真 HTTP OpenAI 协议与零外部配额消耗核验
| 核验维度 | 实测指标 | 结论 |
| --- | --- | --- |
| 本地模型协议端点 | `ThreadingHTTPServer` 监听 `127.0.0.1:0` 真实实现 `POST /v1/chat/completions` | 通过 |
| 请求解析与决策输出 | 解析真实 `messages` 与 `tools` 定义，按跨日上下文输出结构化决策 JSON 与 `tool_calls`（`emit_v02_daily_step`） | 通过 |
| 7 天浸泡本地 HTTP 往返次数 | `len(model_srv.requests) >= 7`，全部 `client_ip == "127.0.0.1"`、`path == "/v1/chat/completions"` | 通过 |
| Socket 层外联拦截守卫 | `LoopbackOnlyNetworkGuard` 拦截一切非 `127.0.0.1/::1` 连接，`external_blocked == []` | 通过 |
| 外部付费大模型 API 消耗 | **0 tokens / 0 请求** | 通过 |

---

## 二、连续 7 天（Day 1 ~ Day 7）跨午夜推进实跑指标核对表

| 天数与仿真时钟 | 核心业务推进动作 | 实跑断言与状态回执证据 | 结果 |
| --- | --- | --- | --- |
| **Day 1**<br>`2026-11-01` | 1. 面对多候选自主活动重复自发选择 `morning_window_sketch` 形成经验偏好；<br>2. 用 `zlib` 构造真实 6×6 双色 PNG 字节（`#285478` 占 2/3、`#DC8C50` 占 1/3）落盘相册并提取像素色板，应用到窗边作品。 | - 偏好升格 `SUPPORTED`（`evidence_count == 2`）；<br>- `extract_real_image_palette` 提取真实主色 `#285478`、`#DC8C50`（`pixel_count == 36`）；<br>- `apply_palette_to_work` 记录真实目标作品 `work:window_folio_p1` 关联回执。 | PASS |
| **Day 2**<br>`2026-11-02` -> `Day 3` 午夜 | 1. 连续高频接触 `morning_window_sketch` 触发短期审美饱和（看腻）；<br>2. 跨 Day 2 -> Day 3 午夜检验饱和值连续指数衰减；<br>3. 搁置“想细描窗景细节 vs 想早点寄出明信片”内部双向冲突。 | - `short_term_satiety > 0.45` 且触发临时降温；<br>- 跨午夜前后（`23:55` -> `00:05`）连续平滑衰减，绝不在 `00:00` 突跳清零，长期偏好仍保持 `SUPPORTED`；<br>- 内部张力 `parked_for_later` 持久化保留，不伪装和解。 | PASS |
| **Day 3**<br>`2026-11-03` -> `Day 5`（48h） | 1. 开启开放问题慢孵化（装订线材质未定）；<br>2. 跨越 48 小时无新证据检验零假顿悟；<br>3. 分别记录过程失误后悔与结果不如意（坏运气）二分条目。 | - 48h 无新线索期间 `status == "incubating"`、`premature_epiphany_count == 0`；<br>- `process_mistake` 生成具体 `next_guardrail` 教训，而 `bad_luck_outcome` 保持 `self_blame == False` 且不生成伪造自责规则。 | PASS |
| **Day 4**<br>`2026-11-04` | 1. 窗口期内连续灌入 **300 条**同族可替代背景事件 `ambient_window_breeze`；<br>2. 发送控制通道事件 `safety_revoke_flush`；<br>3. 切换 `LOW_BUDGET_CONSERVATION`（`budget_scale_pct=10`，省电 90% 模式）。 | - 300 条背景事件仅触发有效背景唤醒 **1 次**，`coalesced_saved_wakes == 299`；<br>- 控制事件 `CONTROL_IMMEDIATE_BYPASS`（`debounced == False`）零防抖直通；<br>- 10% 低预算下 `feigns_fatigue_or_illness == False`、`feigns_bad_mood == False`，在途保存点、已认领承诺与撤权删除对账责任 `preserved_obligations` 全部为 `True`。 | PASS |
| **Day 5**<br>`2026-11-05` | 1. 伴侣递入亚麻蜡线线索推进 Day 3 开放问题；<br>2. 恢复正常预算并对比无事件安静健康日切片与队列积压异常。 | - 开放问题由 `incubating` 推进至 `ready_to_revisit`；<br>- 安静健康切片判 `QUIET_HEALTHY`（`fabricate_drama_when_quiet == False`）；<br>- 注入 `queue_backlog=95, dead_letter_count=1` 立即判 `STALLED_OR_DEGRADED`。 | PASS |
| **Day 6**<br>`2026-11-06` | 进入 `DORMANT_NO_COGNITION` 休眠 18 小时（`02:00` -> `20:00`）后恢复 `NORMAL`。 | - 实测 `dormant_gap_seconds == 64800`（`> 0`）；<br>- `no_fake_backfill == True`；<br>- `fake_diary_entries_created == 0`、`fabricated_memories_created == 0`。 | PASS |
| **Day 7**<br>`2026-11-07` | 1. 跨模型（`nyairo-nextgen-carrier-v3`）+ 无实时预览宿主（`preview_available=False`）迁居体检；<br>2. 试跑 `reverify_migrated_skill` 恢复待复验技法；<br>3. `pack_selective_suitcase` 四档对比并选 `selective_practice` 放下旧包袱；<br>4. 物理删除 Day 1 相册原图文件并复读跨模态视图。 | - 迁居体检产出 `CONTINUED_WITH_LOSS` 与 `UNVALIDATED_AFTER_MIGRATION`，试跑后 `needs_recheck == []`，但预览缺失损失仍诚实保留为 `CONTINUED_WITH_LOSS`；<br>- 四档路由完整对比，主动放下 `legacy_host_exclusive_shader` 与 `retired_brittle_shortcut_macro`；<br>- 原图删除后 `read_v02_crossmodal_view` 100% 零磁盘写入且实时扣留旧色板（`source_available == False`、`dominant_hex == []`、`palette_withheld_reason == "SOURCE_MEDIA_UNAVAILABLE"`）。 | PASS |

---

## 三、《14-研发工作包与验收体系.md》§5 十二个端到端场景（S01–S12）核对表

| 场景编号 | 场景契约定义 | 独立测试方法 | 联动真实模块与核心验真证据 | 结果 |
| --- | --- | --- | --- | --- |
| **S01** | 作品真实写入、重启后可继续、历史真实影响下次选择 | `test_s01_work_written_persists_across_restart_and_history_shapes_next_choice` | `PrivateProjectService` 跨冷重启重读同一持久化存储推进 `version_number` 1 -> 2；`FutureItems` + `v02_v03_daily_loop_bridge` 对照组证明昨日作品便签真实改变次日首选动作族（`follow_tomorrow_note`）。 | PASS |
| **S02** | 原子步骤期间普通聊天可回应，绝不非法 pause 前台活动 | `test_s02_ordinary_chat_during_atomic_step_responds_without_illegal_pause` | `FiniteCoPresence` + `LocalContactInbox` + `SocialContactPort`：前台 `ATOMIC` 时 `communication_availability == "BOUNDARY_DEFERRED"`、`respondable == True`、`pause_requested == False`，归档本地回复且零暂停前台活动。 | PASS |
| **S03** | 等待结果长期 0 模型轮询，满足条件只发 `ResumeEligibility` 不自动恢复 | `test_s03_waiting_long_term_zero_model_polling_and_satisfied_emits_eligibility_only` | `AutonomousLifeRunner` + `waiting_no_model_poll_demo`：多轮等待期间 `llm_calls == 0`，条件满足时产出 `emitted_eligibilities == ["relig:kiln_glaze_ready"]` 且 `auto_resumed == False`。 | PASS |
| **S04** | 外部 accepted 后 timeout 保持 `UNKNOWN`，后续查原操作对账不重提 | `test_s04_external_timeout_keeps_unknown_reconciles_original_op_without_resubmit` | `worldgen` + `world_journey`：传输超时返回 `uncertain` 后旅程状态保持 `unknown`，连续推进两次仅对账原 `execution_id`，`submit_move` 调用次数严格锁死为 `1`。 | PASS |
| **S05** | 部分子效果确认其余未达时保留真实状态，进展不超证据完成 | `test_s05_partial_subeffect_confirmed_persists_hand_state_and_progress_never_overclaims` | `stance_containment_view` 拿起眼镜放入不兼容马克杯返回 `partial_completed`（保留在手槽 `rolled_back == False`）；`activity_progress_completion_lr5` 验证 `PARTIAL_FACT` 绝不触发 `EVAL_COMPLETION_ELIGIBLE`。 | PASS |
| **S06** | 动作报成功但对象哈希复核冲突时判 `CONFLICT` 且不误完成 | `test_s06_action_succeeded_but_object_recheck_conflict_blocks_activity_completion` | `result_settlement_ar1.ResultSettlementEngine` 融合执行器报成与对象哈希复核失败证据，裁决 `SETTLEMENT_STATUS_CONFLICT` 且消费者判定 `DISPOSITION_CONFLICT`。 | PASS |
| **S07** | 活动 `COMPLETED` 后收到迟到纠正事件，核验继承链并记冲突，绝不静默重开 | `test_s07_late_correction_after_completion_records_conflict_without_silent_reopen` | `ar1` 结算链生成 `EVENT_KIND_CORRECTION`（`supersedes_settlement_ref` 合法继承），`lr5` 核验继承链通过且终态活动保持 `COMPLETED` 不静默回退为 `ACTIVE`。 | PASS |
| **S08** | 读取准备分享草稿后源撤权，派发前复检拦截且 36B 禁绝私密泄露 | `test_s08_revocation_after_read_rechecks_before_send_and_blocks_private_leak` | `LifeSharingService` 在 `prepare_share` 后撤权源作品，`send_local` 派发前复检拒绝（非 `LOCAL_DELIVERED`）；`adult_intimacy_view` 一键撤权擦除私密记忆（`relationship_penalty == 0`），伴侣视图零泄露。 | PASS |
| **S09** | 原始证据删除后即使恢复旧备份库重建表面，旧内容绝不复活 | `test_s09_deleted_evidence_never_resurrects_after_backup_restore_or_index_resync` | `ForgetCaseStore` 完成删除（`COMPLETE`）后强行用删除前旧备份覆盖 `evidence.db`/`controls.db`，经 `ForgetGate` + `LifeMemoryService` 读取仍返回 `None` / `count == 0`。 | PASS |
| **S10** | 测试/维护动作改变 World 状态后，绝不被接纳为个人经历记忆 | `test_s10_maintenance_and_fixture_world_mutations_rejected_as_personal_life` | 维护修改 `world_state.json` 后，`MemoryAdmissionExperienceConsumer` 对缺失个人活动引用及无 AG-2 权威记录的测试事件全部拒绝（`accepted == 0`）。 | PASS |
| **S11** | 无事件安静日、死进程心跳超时与卡队列积压被技术观测严格区分 | `test_s11_quiet_healthy_day_distinguished_from_dead_process_and_stalled_queue_zero_drama` | `v02_runtime_migration_view` 准确区分 `QUIET_HEALTHY`（零假剧情）、`HEARTBEAT_TIMEOUT_STALLED` 与 `QUEUE_BACKLOG_OVERFLOW`（`STALLED_OR_DEGRADED`）。 | PASS |
| **S12** | 模型/宿主迁移后作品、责任、身份和技法适用性清晰交代，损失诚实保留 | `test_s12_model_and_host_migration_preserves_works_duties_identity_and_honest_loss` | `v02_runtime_migration_view` 在跨模型无预览宿主下产出 `CONTINUED_WITH_LOSS`、`HOST_LIVE_PREVIEW_UNAVAILABLE` 与 `UNVALIDATED_AFTER_MIGRATION`，在途作品与共同承诺完整随箱携带，身份连续保留。 | PASS |
