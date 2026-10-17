# A 包回执：真实处境与有限感受（BODY-CAP / PERCEPT / AFFECT；CT04/11/14）

状态：**已实施、定向验证通过、未接线**（D 接线由父级另派）。本包不碰 cycle/assembly/resident/
runner/one_day_life/AG0/AG1/预算/pause，不碰 B 的 `private_daily_items.py`/`life_artifact_port.py`，
不碰 C 的 `life_continuity_inputs.py` 与 Memory seam，不改任何 owner。全部改动 **UNCOMMITTED**。

- 代码候选：`D:\nyairo-v03-backend`，分支 `work/v03-w01-navigation`，
  HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（开工时与交工时两次核对一致；未 reset/clean/
  checkout/recreate/stage/commit/push）。
- 平台：WSL Ubuntu 隔离 venv
  `/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`
  （3.12.3），`PYTHONDONTWRITEBYTECODE=1`，未改全局环境；临时夹具在
  `/tmp/nyaw05r.jdK3q/a-*` 与本会话 scratch，未删任何既有目录。

## 1. 自有文件清单（全部 UNCOMMITTED）

| 文件 | 状态 | 内容 |
| --- | --- | --- |
| `components/life/scripts/body_capacity_source.py` | 已存在（原未跟踪）→ 复用完善 | AG-1 UNKNOWN 策略说明与镜像常量、`ag1_capacity_kwargs(..., unknown_capacity_policy=)`、5 个可复用公开助手；映射规则与读路径**未改** |
| `components/life/scripts/life_situation_view.py` | 新 | 可注入处境读视图（Body/预算/知识分列） |
| `components/life/scripts/short_appraisal_view.py` | 新 | 短期评价最小协议（纯读、可重建投影） |
| `tests/test_body_capacity_source.py` | 已存在（原未跟踪）→ 加 5 项 | `Ag1UnknownPolicy`：UNKNOWN 不因诚实而永久 DEFER |
| `tests/test_life_situation_view.py` | 新 | 29 项定向测试 |
| `tests/test_short_appraisal_view.py` | 新 | 29 项定向测试 |
| `docs/engineering/life-seed-body-capacity.md` | 已存在（本包自有说明）→ 更新 | 增补 AG-1 策略节、公开助手、实测 54 项 |
| `docs/engineering/life-v1-a-result.md` | 新（本文件） | 本回执 |

未新建：任何 owner、任何持久库、任何 CLI/入口、任何 Candidate 源、任何测试共享夹具模块。

## 2. Body 正向事实：**实际不存在**（本轮最重要的结论）

- 现有 Body 合同（`components/world/service/world_body_gateway_api.py:390-418` → `body_signal.py:46-91`）
  只有 **13 条偏离规则**，没有一条表示「能做到」。因此：
  - `AVAILABLE` **不可达**：无信号集只能给 `UNKNOWN`/`NO_BURDEN_EVIDENCE`
    （`CHIYO-施工规划/05-身体与情绪现状勘察.md` §1「不能无信号就推出身体健康」）；
  - `UNAVAILABLE` **不可达**：封闭词表没有「做不到」，新鲜度不是能力事实；来源宕机一律 `UNKNOWN`（fail closed）。
- 精确所需读字段（**须由 Body owner 补，本包不越写集**）：`get_body_signals` 顶层缺
  `body_id`、`occurred_at`、body 版本（`source_body_version` 只在每条 signal 内）、`last_advanced_at`；
  另需一条**正向能力声明**（当前合同不存在）。现有读口：`WorldBodyReadPort.body()` →
  命令 `get_body_signals`（顶层 `available`/`recovery`/`latch`/`signals[]`）；`get_status` 只有
  `body_id`，补不上版本与观察时刻，所以适配器坚持**一次读**、不猜阈值。
- AG-1 如何处理 Unknown/有限任务（已核清，未盲接）：`agency_decision_ag1.py:1971` 默认
  `unknown_capacity_policy="ALLOW_WITH_UNKNOWN"` → `LIMITED` 得 `EXECUTABLE_WITH_LIMITS`（:2044-2045）；
  `UNKNOWN` 得 `EXEC_UNKNOWN`（:2050-2051），候选仍 `candidate_remains_open`（:2060），**不是 DEFER**；
  只有显式 `BLOCK_ON_UNKNOWN` 才 `EXEC_NOT_NOW` + `CAPACITY_UNKNOWN_DEFERRED`（:2047-2049）。
  因此本包在 `ag1_capacity_kwargs` 里把该策略设为**只在调用者显式给出时**才附带，并用测试锁定
  「诚实 UNKNOWN 默认不 DEFER」。

## 3. 可供 D 接线的 import / API / 字段

### 3.1 处境视图 `life_situation_view.py`

```python
import life_situation_view as lsv

source = lsv.LifeSituationView(home="/srv/nyairo/profiles/<p>/world-home",
                               body_port=WorldBodyReadPort(client),   # 可选：注入端口则读一次
                               expected_body_id="chiyo_body")         # 装配期校验，非法即抛
view = source.build(
    now="2026-10-16T06:00:00+00:00",          # 必须由调用者注入；内部不读墙上时钟
    budget={"source": "life_request_budget.check",   # 调用者已经做过的读，A 只投影
            "allowed": True, "reason_code": None, "state": "ok",
            "path": str(budget.path), "utc_day": "2026-10-16", "run_id": "run:x",
            "utc_day_calls": 3, "utc_day_max": 8,
            "run_window_calls": 3, "run_window_max": 8},
    observations=[{"observation_ref": "world:observation:1",
                   "source_ref": "world_body_read_port.get_observation",
                   "observed_at": "2026-10-16T05:00:00+00:00",
                   "valid_until": "2026-10-16T07:00:00+00:00",   # 由来源声明，A 不自定阈值
                   "subject_id": "chiyo.global",                 # 可选；给了就必须一致
                   "declared_scope": "...", "uncertainty": "..."}],  # 仅回显，不解释
)
kwargs = lsv.ag1_capacity_kwargs(view)   # 四个键：body/resource 的 state+ref，成对
snapshot = ag1.CapacityEvaluator.evaluate_capacity(..., **kwargs)
```

返回（`schema_version = "chiyo.life.situation_view.v1"`，非 Canonical）：

| 字段 | 语义 |
| --- | --- |
| `subject_id` / `home_ref` / `observed_at` / `identity_bound` / `identity_reason_code` | 主体（经 `activity_continuity.validate_subject_id`）、`home:<sha256前12>`（不含路径）、注入的参考时刻、身份是否绑定 |
| `body` | 逐字复制 `body_capacity_source` 的答案：`state`/`ref`/`source`/`reason_code`/`freshness`/`observed_at`/`observed_body_id`/`observed_revision`/`evidence`/三个 `*_bound`；**不重算** |
| `resource_budget` | `state`/`ref`/`source`/`reason_code`/`owner_reason_code`/`ledger_state`/`utc_day`/`run_id`/`utc_day_calls`/`utc_day_max`/`run_window_calls`/`run_window_max`/`observed_at`/`bound`/`owner_asserted` |
| `knowledge` | `observations[]`（每项 `observation_ref`/`source_ref`/`subject_id`/`observed_at`/`valid_until`/`validity`/`reason_code`/`declared_scope`/`uncertainty`/`echoed_only`）、`current_refs`、`not_current`、`counts`、`declared_bound_only=true`、`local_threshold_used=false`、`knowledge_is_not_a_capacity_dimension=true` |
| `dimensions` / `limiting_dimensions` / `blocking_dimensions` / `unknown_dimensions` / `unknowns` | 仅两个容量维度（`body`、`resource_budget`）；`LIMITED`→limiting、`UNAVAILABLE`→blocking、`UNKNOWN`→unknown（含各自 `reason_code`）；**knowledge 不是容量维度** |
| `source_revisions` | `home`/`body`/`resource_budget` 三者的 ref（可重建、可溯源） |
| `read_only`/`reads_performed`/`writes_performed`/`mutations_attempted`/`candidates_emitted`/`actions_authorized`/`persistent_store`/`cache_held`/`rebuildable` | `true`/0 或 1/0/0/0/0/`null`/`false`/`true` |

拒绝语义（**抛** = 调用者/接线错误，`LifeSituationViewError`）：同时传 `body` 与 `body_port`、
`body_port` 没有 `body()`、`now` 不可解析、`expected_revision` 非结构化 ref、非法 subject、home 不是目录。
其余一切世界侧问题都是**字段**（fail closed）：`BODY_SOURCE_NOT_INJECTED`（未注入 Body，绝不
AVAILABLE）、`BODY_VIEW_INVALID`、`HOME_UNBOUND`/`SUBJECT_OWNER_UNAVAILABLE`（此时两列同为 UNKNOWN、
`identity_bound=false`，且**不读端口**）、`RESOURCE_BUDGET_ALLOWED`/`_REFUSED`/`_VERDICT_MISSING`
（缺 owner 判定）/`_READ_UNUSABLE`（ledger 损坏/不可读 → UNKNOWN，**不是**耗尽）/`_NOT_INJECTED`/
`_SOURCE_MISSING`、知识项 `OK_OBSERVATION_CURRENT`/`OBSERVATION_VALIDITY_UNVERIFIED`/`..._EXPIRED`/
`..._INVALID`/`..._MISSING`/`..._TIME_MISSING`/`..._TIME_INVALID`/`..._TIME_AHEAD`/`..._SUBJECT_MISMATCH`/
`..._REF_MISSING`/`..._DUPLICATE_REF`/`..._AUTHORITY_FIELD_LEAK`/`NO_REFERENCE_INSTANT`。
`resource_budget.ref` 只由 owner 读里真实出现的 `path`（摘要，不落路径）+`utc_day`+`run_id`+计数派生；
缺 `utc_day` 时给 `null` 且 `bound=false`，**不编 ref**。

### 3.2 短期评价投影 `short_appraisal_view.py`（等价字段名，见下）

```python
import short_appraisal_view as sav

view = sav.build_short_appraisal_view(
    home="/srv/nyairo/profiles/<p>/world-home",
    now="2026-10-16T06:00:00+00:00",                     # 必须注入
    appraisals=[{"appraisal_ref": "appraisal:1",
                 "source_refs": ["world:read:1"],        # 至少一个结构化来源
                 "observed_at": "2026-10-16T05:00:00+00:00",
                 "valid_until": "2026-10-16T07:00:00+00:00",   # 必填：短期评价必须自带到期
                 "subject_id": "chiyo.global",           # 可选；给了就必须一致
                 "uncertainty": "LOW",                   # 可选，仅回显
                 "intensity": "mild",                    # 可选，来源自报，本视图不合并
                 "correction_refs": ["correction:9"]}],  # 可选：自我声明已被更正
    corrections=[{"correction_ref": "correction:1", "corrects_refs": ["appraisal:1"]}],
    lost_source_refs=["world:read:2"],                   # 已失读来源
)
items = sav.valid_appraisals(view)                       # 只剩仍成立的项；空列表=没有有效评价
```

返回（`schema_version = "chiyo.life.short_appraisal_view.v1"`）：`subject_id`/`home_ref`/`observed_at`/
`identity_bound`、`appraisals[]`（`appraisal_ref`/`subject_id`/`source_refs`（去重排序）/
`duplicate_source_refs`/`observed_at`/`valid_until`/`declared_scope`/`uncertainty`/`uncertainty_source`
（`declared` 或 `UNKNOWN`）/`intensity`/`correction_refs`/`corrected_source_refs`/`lost_source_refs`/
`status`/`reason_code`）、`valid_appraisal_refs`、`invalid_appraisals`、`has_valid_appraisal`、
`empty_reason_code`、`corrections`/`invalid_corrections`、`lost_source_refs`、`source_intensity`、
`conflicting_source_refs`、`intensity_aggregation="none"`、`protocol{...}`、`read_only/reads_performed=0/
writes_performed=0/mutations_attempted=0/candidates_emitted=0/actions_authorized=0`。

等价字段名对照（与父级裁定的最小协议）：`source_refs`＝来源 refs；`subject_id`＝主体；
`observed_at`/`valid_until`＝时间与到期；`uncertainty`（+`uncertainty_source`）＝不确定度；
`correction_refs`/`corrected_source_refs`/`corrections`/`invalid_corrections`＝更正 refs；
`status`+`reason_code`＝有效/无效与原因；`source_intensity`＝按来源取值（不堆强度）。

拒绝语义：`APPRAISAL_VALID_UNTIL_MISSING`（无到期 → 不成立，短期评价不能无期限）、`..._VALIDITY_INVERTED`、
`..._SOURCE_REFS_MISSING`、`..._REF_MISSING`、`..._DUPLICATE_REF`、`..._NOT_A_MAPPING`、
`..._SUBJECT_MISMATCH`、`..._OBSERVED_AT_MISSING/_INVALID/_AHEAD`、`..._NO_REFERENCE_INSTANT`（未注入
`now` → 无法判定是否仍成立 → 不成立）、`..._EXPIRED`、`..._CORRECTED`（自身声明更正，或被有效更正
指向本体或其来源）、`..._SOURCE_LOST`、`..._TONE_INFERENCE_REFUSED`（`tone`/`sentiment`/`mood`/
`emotion`/`affect`/`feeling`/`text`/`message`/`transcript`/`utterance`/`reply`/`words`）、
`..._STABLE_FACT_REFUSED`（`stable_emotion`/`relationship`/`preference`/`personality`/`self_belief`/
`identity`）、`..._BODY_FACT_REFUSED`（`body_capacity_state`/`body_capacity_ref`/`fatigue`/`health`）；
更正侧：`CORRECTION_REF_MISSING`、`CORRECTION_TARGETS_MISSING`（匿名/无目标的更正被拒，**不能**抹掉评价）。
抛错（`ShortAppraisalViewError`）：非法 subject、home 不是目录、`now` 不可解析、`valid_appraisals()`
收到非视图。

### 3.3 零写权 / 零 Candidate（有测试锁定）

- 两个新模块都**不 import** AG0/AG1、不调用 `reserve`/`ensure_window`、代码段内无 `open(`/
  `write_text`/`json.dump`/`sqlite3`/`shutil`/`mkdir`；`persistent_store=null`、`cache_held=false`。
- 处境视图：3 次 `build` 后 home 目录清单（含 size/mtime/sha256）字节级不变；线上命令恰为
  `get_body_signals` ×N；`candidates_emitted=0`、`actions_authorized=0`。
- 评价投影：3 次 `build` 后 home 目录不变；`reads_performed=0`（只投影调用者给的材料）；
  项字段集被测试固定，**没有**任何可放稳定情绪/关系/偏好/身体事实的字段。

## 4. 已做 vs 未接线

- **已做（本轮，自证）**：Body 读→容量（复用既有适配器）+ AG-1 策略语义澄清；可注入处境视图
  （Body/独立预算/知识时效三列 + 身份/时间/版本/来源/未知）；短期评价最小协议（纯读、可重建、
  有来源、有到期、可更正、失读/过期/更正后无有效评价、重复来源不堆强度、不自签稳定事实、不从语气推断）。
- **未接线**：没有任何 cycle/assembly/resident/runner/one_day_life 引用这些模块；AG-1 调用点仍是
  旧的「round budget 当 capacity」（`autonomous_life_cycle.py:743-753`，本包未动）。D 只需按 3.1/3.2
  注入并调用 `**ag1_capacity_kwargs(view)`（**不要**配 `BLOCK_ON_UNKNOWN`）。
- **未做/不冒称**：真实产品模型、外网、外发、24h/长跑、生产 profile/部署/自启；未在真实部署的
  gateway socket 上跑过；`AVAILABLE`/`UNAVAILABLE` 未由当前合同导出（结论相反）；短期评价没有
  真实模型消费者（只有协议与投影）。

## 5. 命令与实际结果

```bash
$ cd /mnt/d/nyairo-v03-backend
$ PYTHONDONTWRITEBYTECODE=1 <w04venv>/bin/python tests/test_body_capacity_source.py
Ran 54 tests in 9.433s
OK                                    # exit=0（原 49 项，本包 +5）
$ PYTHONDONTWRITEBYTECODE=1 <w04venv>/bin/python tests/test_life_situation_view.py
Ran 29 tests in 3.102s
OK                                    # exit=0
$ PYTHONDONTWRITEBYTECODE=1 <w04venv>/bin/python tests/test_short_appraisal_view.py
Ran 29 tests in 0.177s
OK                                    # exit=0
$ git diff --check -- <本包 6 个代码/测试文件>
（无输出）                            # exit=0；另以 grep -P "[ \t]+(\r)?$" 复核无行尾空白
```

覆盖要点（不重复计数）：同 Body 改预算**不改变** body 列（两列逐字比较 + 负控：body 列内不出现
budget/quota/token/exhausted 词汇）；有据不同 Body 约束不同（burdened→`LIMITED`/limiting，
resting→`UNKNOWN`/unknown，两者都不是 `AVAILABLE`）；陈旧/未注入 Body 不说健康；知识列只含
实际声明范围（声明到期→`CURRENT`/过期→`EXPIRED`/无声明→`UNVERIFIED`，无本地阈值）；预算列用真实
`LifeRequestBudget.view()/check()`（临时 ledger 夹具，未花预算、未用 `reserve`）：允许→`AVAILABLE`、
耗尽→`UNAVAILABLE`+owner 原码、损坏 ledger→`UNKNOWN`；AG-1 真实 `CapacityEvaluator` 接入（资源
维度真的到 AG-1：`REASON_RESOURCE_LIMIT`；UNKNOWN 为软 UNKNOWN）；评价的来源/更正/到期/去重/失读；
零写权与零 Candidate。

## 6. 真实剩余阻塞

1. **Body 无正向事实**：要 `AVAILABLE` 必须由 Body owner 在 `get_body_signals` 顶层补正向能力声明
   + 观察时刻 + body 版本（本包不越写集）。在补之前，真实身体条件对决策只有 `LIMITED`/`UNKNOWN` 两态。
2. **无 S07 affect owner**：短期评价只能保持「实验性、可重建投影」。若产品需要**持久权威**的短期
   评价（跨重启保存、被其他域当作事实读），必须先由总监工裁定新 owner，本包未造。
3. **预算读的形状是约定**：D 需把 `LifeRequestBudget.view()`/`check()` 的结果按 3.1 的键注入
   （尤其 `path`/`utc_day`/`run_id`）；缺 `utc_day` 时 ref 为 `null`（AG-1 仍拿到显式 state，不会伪造）。
4. **knowledge 标签是本投影自有**（树里没有 S03 时效 owner）：`CURRENT/EXPIRED/UNVERIFIED/INVALID`
   带 `owner_asserted=false`；若将来有 S03 owner，应改为读 owner 而非保留本投影词表。
5. 未在真实部署 gateway socket / 真实 profile 上跑过；未做 24h、真实模型行为、并发/多前台。
