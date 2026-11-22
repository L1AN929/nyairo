# v0.8 Wave 8 · 0.2 / 0.3 / 0.4 七天连续常驻全栈验证

> 批次：`v08-wave8-seven-day-resident`（2026-11-15 / 16）
> 性质：**纯跨周期多域连续验证波次，只写测试与文档，不改任何 `components/` 生产代码。**
> 原则：同一隔离临时实例、真实 `AutonomousLifeCycle` + `AutonomousLifeResident`、零产品大模型调用
> （`llm_calls == 0`）。

## 1. 本波解决什么

Wave 5（0.3 全量日常候选）、Wave 6（0.2 全量 7 类日常候选）、Wave 7（P12 只读叙事 /
有界 Inquiry / Ch.16 22 类衰减矩阵 / Ch.07 §4 SlotQuota、DailyEnvelope）此前各自单点验通，
但没有一条证据证明它们能在**同一个实例上连续跑满一天到七天**，且跨模块状态不互相踩踏、
受控重启后逐字保真、全周期产品模型调用为零。本波补上这条端到端证据链。

## 2. 场景设计（同一临时根，第 1–7 天）

新增 `tests/test_v08_v02_v03_seven_day_resident_verification.py`，两条测试路径：

- `SevenDayOwnerTruthChainTest`：owner 级、平台无关、秒级。逐域精确断言 + Day 7 只读投影。
- `SevenDayResidentContinuityTest`：真实 `AutonomousLifeCycle` + `AutonomousLifeResident`
  （POSIX：真实 Life Supply / World owner socket），七天连续 tick，Day 5 受控重启并消费
  成人亲密断点；脚本化 0-LLM 认知。

七天剧本：

| 天 | 内容 | 关键断言 |
|----|------|----------|
| Day 1 | 0.3 起手 + 0.2 工作台/便笺 + SlotQuota | 紧手生活费（余额 6000 / 生活底线 5400 → 可支配 600 ≤ 800）驱动 `v03_job:manuscript_fee` 真实入账 2400；`tomorrow_note:*` 一次关闭；`artifact:*` 托盘推进且不伪造版本；同 `decision_id` 重放零二次入账；`LifeRequestBudget` 的 `DAYTIME_ACTIVE=2` / `EVENING_WIND_DOWN=1` / `NIGHT_REST=0` 槽位配额与 `DailyEnvelope`（日硬上限 8、分类 CONTINUATION 5）计数准确。 |
| Day 2 | 0.2 探究立项 + 0.4 院子/植物 + 共同背景 | `open_bounded_inquiry` 建 `OPEN`；日常循环推进到 `INVESTIGATING`（1 条证据）；`common_ground:*` 离开待澄清队列；`v04_courtyard_hang:*` 把湿布巾真挂到院子 `yard_line`；三域状态同一实例互不踩踏。 |
| Day 3 | 生病照护与门控 + 计划/远足拒止 + 发呆合法性 | 受凉转入 `acute_illness`（`blocks_strenuous_outing=True`，真实 `candidate_cap=4`）；选中 `v03_care:bed_rest_block` / `warm_hydration`；同日 `v03_idle:daydream_window` 合法零惩罚（`productivity_penalty=0`、不产作品、不强制总结）；同一病期下 `strenuous_outing_walk` 与强耗能 `adopt_tentative_plan` 被真实门控 `ILLNESS_BLOCKS_STRENUOUS_OUTING` 拒绝，零写盘，照料恢复不被阻断。 |
| Day 4 | 病愈恢复 + 购买/预算护栏 + 成人亲密断点保存与挂起 | 把身体标回 `healthy`（只改 `illness` 子文档，账本逐字节保留）；可负担购买 `purchase_cue:*` 真实扣款 800；超预算时 `INSUFFICIENT_INDEPENDENT_FUNDS` 拒绝、零负余额；`save_activity_checkpoint` 保存真实 `activity_ref`，`stop_scene` 降级使 `checkpoint.resumable == True`。 |
| Day 5 | 受控重启隔离实例 → 断点自动发现与消费 | 丢弃内存 `Cycle` / `Resident` / 缓存，指向**同一磁盘目录**重建新 epoch；重建本身不改写任何既有域事实；Day 1–4 余额、探究、晒衣进度与待恢复断点逐字保留、零虚构补写；重启后首轮日常循环发现并消费 `v03_intimacy_resume:<permit_id>`；再次采集不再产出该断点。 |
| Day 6 | 0.2 收尾：计划 / 书签 / 植物 / 告示 + 夜间零额度 | `adopt_tentative_plan`（`home_hearth_view` `DROPPED` 终态 + 账本 `ADOPTED`）；`follow_bookstore_bookmark`、`observe_plant_journal`；`v04_public_notice:*` 真读一次；把 Day 2 探究收敛到 `CONCLUDED`；夜间 `NIGHT_REST` 槽位 `SlotQuota == 0` 精确拦截，而默认 `can_spend` 预算调用不被破坏。 |
| Day 7 | 只读全局视角：P12 叙事 + 22 类衰减矩阵 + 隐私 + 零写盘 | `project_p12_autobiographical_narrative(..., has_subject_capability=True/False)`：主体视角看到 9 月/11 月两章、转折与母题，伴侣视角严格剥离 `PRIVATE_MEMORY_*` / `PRIVATE_GROUND_*` 与 `recall:mem_private`；`project_annual_decay_matrix_22` 22 类不重不漏、反映前 6 天真实活跃文件；只读投影前后四个域目录快照逐字节不变（零脏写）。 |

## 3. 验证

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_v02_v03_seven_day_resident_verification \
  tests.test_v08_v03_daily_loop_execution \
  tests.test_v08_v02_daily_loop_execution \
  tests.test_v08_v02_v03_design_debt_closure -v
```

结果见 §5；新模块两条用例：owner 链（秒级、平台无关）与 resident 链（POSIX，约 13 秒，
真实 socket + 两次 epoch 重建）。全周期所有 settle 与只读投影逐条回报 `llm_calls == 0`。

## 4. 施工期发现的真实边界（不改生产代码，写进测试与文档）

1. **急性生病候选上限 `candidate_cap=4`**：`v03_economy_illness_bridge._capability_for_phase`
   对 `acute_illness` 给出 `candidate_cap=4`。因此“合法发呆”候选（追加在候选池末尾）只有在
   当日真实候选总数 ≤ 4 时才留在池内。为使 Day 3 的发呆可被真实选中，Day 1 用**紧手生活费**
   （而非开放储蓄目标）触发赚取动机：赚完之后 `budget_tight=False` 且无未完成储蓄目标，
   第 3 天不再产生工单候选，急性病期真实候选恰为「卧床 / 温水 / 探究 / 发呆」四项，全部在上限内。
   这是真实上限语义的如实呈现，不是对生产代码的削弱。
2. **急性病写穿成人亲密**：`record_strain_or_rest` 进入急症时会同步
   `adult_intimacy.json` 的 `acute_illness=True / discomfort="strong"`（即使该状态尚未配置
   gate 也会被创建）。Day 4 病愈时除恢复生态 `illness` 外，还必须用真实 owner action
   `update_body_context` 清除 36B 身体上下文，否则开场会被正确地判成 `SUBJECT_UNAVAILABLE`。
3. **多日病程曲线**：真实恢复需约 +24h 进入恢复期、+48h 才 `recovered`；为了让 Day 4 的
   “病愈”落在一天之内，测试把 `phone/v03_ecology.json` 的 `illness` 子文档直接标回 `healthy`
   （只改这一个子文档，`economy` / `ledger_entries` / 回执全部保留）。这是**注入的测试前置**，
   不是生产路径，文档在此明示。
4. **购买闸门**：无可支配余量或存在开放储蓄目标时，非必需冲动购买会被
   `BUDGET_TIGHT_IMPULSE_FILTERED` / `SAVINGS_GOAL_IMPULSE_FILTERED` 过滤；本波用紧手生活费
   在赚取后自然打开可支配余量，使非必需可负担购买走真实 `afford_independently` 扣款路径。

## 5. 验证结果与已知边界

- 结果：`Ran 75 tests ... OK`（WSL `/tmp/rc104-venv`，指定 4 套件；见 worker 交付记录）。
- 只写测试与文档：未改任何 `components/` 文件；三个冻结文件
  （`autonomous_life_assembly.py` / `life_sharing_service.py` / `readonly_recall_adapter.py`）零改动。
- 未改对外 HTTP / 数据格式；未新增依赖；未 `git commit` / `git push`。
- Day 7 的 P12 / 衰减矩阵是**只读投影**，只聚合既有磁盘事实；本波不新建任何叙事或矩阵 store。
- Day 5 的“受控重启”在 owner 级测试里表现为“无内存对象可丢、纯磁盘重读”；在 resident 级测试里
  是真实关闭旧 runtime、重建新 `Cycle` / `Resident`，并断言重建本身不改写六个具名域文件。
