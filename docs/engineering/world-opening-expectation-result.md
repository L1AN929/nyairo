# W 包回执：World 有来源的有限未来营业预期（单地点、只读）

日期：2026-10-19｜分支 `work/v03-w01-navigation`｜HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`｜**全部 UNCOMMITTED**
（未 stage/commit/push，未 reset/clean/checkout，未删除任何文件）。
范围：F-096/F-097 的 **World 侧前置只读 owner 包**。本包不接 Life、不建 Agenda、不改任何写路径，
**不宣称 F-096/F-097 完成**。

## 1 自有 delta（只碰了这些文件）

| 文件 | 内容 |
|---|---|
| `components/world/scripts/world_daily_rhythm.py`（改，+383/−1） | 新增 `location_opening_expectation`（L370）与常量 `OPENING_KIND`(L205)/`OPENING_SCHEMA_VERSION`(L206)/`MAX_EXPECTATION_HORIZON_DAYS=8`(L208)/`RULE_SOURCE_PACKAGE`·`RULE_SOURCE_BUILTIN`(L210-211)/`_FINGERPRINT_SCOPE`(L213)/`_OPENING_NOTE`(L217)；内部 `_aware_instant`(L223)/`_opening_source`(L235)/`_canonical_opening_rule`(L240)/`_rule_fingerprint`(L265)/`_opening_unavailable`(L285)/`_hourly_window`(L325)。导入加 `hashlib`/`json`/`timedelta`；`__all__` 增 6 项。**既有 `location_availability`/`affordances_for_location`/`daily_rhythm` 一行未动。** |
| `components/world/scripts/world_daily_opportunities.py`（改；该文件本身仍未跟踪） | 模块 docstring 增第 4 条（L11-16）；新增 `_opening_expectation`(L145)；目录条目新增独立字段 `"opening_expectation"`(L330)；`compact_projection` docstring 写明**故意不进短视图**（L392-395）；目录 docstring 增一句字段说明（L282-284）。`availability`/`validity`/`observed`/`affordances`/`action_grant` 均未变。 |
| `tests/test_world_opening_expectation.py`（新，486 行，12 用例） | 见 §4。 |
| `docs/engineering/world-opening-expectation-usage.md`（新） | 给 L（Agenda）接线的短用法与边界。 |
| `docs/engineering/world-opening-expectation-result.md`（新，本文件） | 本回执。 |
| `.agents/notes/implemented/architecture/2026-10-19-world-opening-expectation.md`（新） | 决策笔记（先 `proposed` 记录意图/替代/代价，实现后同步为 `implemented`，同一文件，未留两份）。 |

未碰：Life 全部文件、`chiyo_bundle/*`、World 写路径（`world_living_skeleton` 等）、世界包/MANIFEST、
旧测试、他人 note 与 docs。

## 2 真实 API

```python
import world_daily_rhythm as wdr
result = wdr.location_opening_expectation(
    "cafe",                 # 地点 id（`place/area` 区域节点会委托到地点，返回地点 id）
    now,                    # 必须 aware datetime；naive → DailyRhythmError
    timezone_name=None,     # 省略 = 活动世界时区；不同则 timezone_source=caller_override
    environment=env,        # 与 active_world() 同一个 env 字典
)
```

`kind = "world_location_opening_expectation"`，`schema_version = "world.location.opening_expectation.v1"`。

**available 字段**：`status`、`world_id`、`location_id`、`source`（`world_package_schedule` /
`world_daily_rhythm_builtin`）、`as_of`/`as_of_utc`、`timezone`/`world_timezone`/`timezone_source`、
`horizon_days`(≤8)/`horizon_end`、`rule{open_hour,close_hour,weekdays_only}`、`rule_kind`
（`hourly_window`/`always_open`）、`window_state`（`open_now`/`opens_later`/`always_open`）、
`always_open`、`expected_open_at`(+`_utc`)、`window_open_at`、`window_end`(+`_utc`)、
`rule_fingerprint`/`rule_fingerprint_scope`/`rule_fingerprint_purpose`、
`requires_recheck: true`、`action_grant: false`、`note`。

**unavailable 字段**：`status`、`reason_code`、`horizon_days`、`expected_open_at/window_open_at/window_end = null`、
`always_open: false`、`window_state: null`、`requires_recheck: true`、`action_grant: false`、`note`，
另外按已知程度尽力给 `world_id/location_id/source/timezone/as_of/rule_kind`。`reason_code` 取值：
`WORLD_PACKAGE_UNREADABLE`、`WORLD_DATA_UNAVAILABLE`、`LOCATION_UNKNOWN`、`LOCATION_RULE_UNREADABLE`、
`TIMEZONE_UNKNOWN`、`OPENING_RULE_UNSUPPORTED`（`rule_kind`=`cross_midnight` / `missing_close_hour`）、
`NO_OPENING_WINDOW`（`rule_kind`=`empty_window`）、`NO_WINDOW_WITHIN_HORIZON`。
目录里调用包了最后一层兜底：任何异常 → `reason_code: "OPENING_EXPECTATION_UNAVAILABLE"` 的同形骨架。

**真实样例（内建汐见市，公园 6–22，`now = 2026-10-19T05:59+09:00`，跑了 runner 的原始输出节选）**：

```
kind=world_location_opening_expectation  status=available  source=world_daily_rhythm_builtin
world_id=shiomi_city  location_id=park  timezone=Asia/Tokyo  timezone_source=world
as_of=2026-10-19T05:59:00+09:00          as_of_utc=2026-10-18T20:59:00+00:00
rule={open_hour:6, close_hour:22, weekdays_only:false}   rule_kind=hourly_window
window_state=opens_later  expected_open_at=2026-10-19T06:00:00+09:00
window_open_at=2026-10-19T06:00:00+09:00  window_end=2026-10-19T22:00:00+09:00
horizon_days=8  horizon_end=2026-10-27T05:59:00+09:00
rule_fingerprint=sha256:07abfd00dbddf76705ac45b41a0c8ba030915103580ba6cb55b823bcba385db5
rule_fingerprint_purpose=source_change_detection_only  requires_recheck=true  action_grant=false
```

**真实样例（学校 8–18 `weekdays_only`，`now = 2026-10-17T13:00+09:00` 周六）**：
`window_state=opens_later`、`expected_open_at=2026-10-19T08:00+09:00`、
`window_end=2026-10-19T18:00+09:00`，同一时刻 `location_availability` 为 `CLOSED/WEEKEND_CLOSED`。

**真实样例（home 常开，`now = 2026-10-19T03:15+09:00`）**：`rule_kind=always_open`、
`window_state=always_open`、`always_open=true`、`expected_open_at = as_of`、`window_open_at=null`、
`window_end=null`。

**真实样例（不可读世界包）**：`status=unavailable`、`reason_code=WORLD_PACKAGE_UNREADABLE`、
`expected_open_at=null`、`action_grant=false`，且不带 `world_id`（连世界身份都没读到就不编）。

**目录条目里的真样子（真实世界包 `daily_neighborhood_v1` + 真实 fixture 实例，周六 13:00，
实例在 home/bedroom）**：

```
catalog status=available scope=NOW_ONLY future=not_promised schedule_source=world_package_schedule
cafe.availability  = CLOSED / WEEKEND_CLOSED / world_package_schedule
cafe.observed      = not_observed / OUT_OF_VIEW / world_state          # 远处内景仍不可见
cafe.opening_expectation: status=available  window_state=opens_later
    expected_open_at=2026-10-19T08:00:00+09:00  window_end=2026-10-19T18:00:00+09:00
    rule={open_hour:8, close_hour:18, weekdays_only:true}  source=world_package_schedule
    world_id=daily_neighborhood_v1  requires_recheck=true  action_grant=false
    rule_fingerprint=sha256:26d8bfce40a9f81f484602deb80206d69d08c2dd85004b87f0291707882b64dd
compact 每项键（无 opening_expectation）: action_grant, affordance_ids, availability,
    availability_reason, here, kind, location_id, name, observed, reach, walk_seconds
```

## 3 时钟与规则来源范围

- **时钟**：`now` 必须 aware（`tzinfo` 且 `utcoffset()` 非空），naive 直接 `DailyRhythmError`——
  不沿用旧 `_local()` 的“naive 当 UTC”宽容路径（那条路径没改，旧调用方行为不变）。
  `as_of` 是换算到生效时区的本地时刻，另给 `as_of_utc`；窗口两端同时给本地与 UTC。
- **规则来源**：唯一来源是 `world_runtime_data.active_world().location_rule()`，即世界包
  `rules.json` 的 `location_rules`（自定义世界）或汐见市 `_RULES`（内建）。
  `source` 与 `world_daily_opportunities.schedule_source` 同一套词汇。
- **判断语义**：与 `location_availability` 逐字一致 —— `open_hour is None` → 常开；
  `weekdays_only` 且周末 → 整天闭店；否则 `open_hour <= hour < close_hour` 为开。
  窗口按**小时**精度搜索（规则本身只有整点），范围 `offset ∈ [0, 8]` 天，
  上限由 `MAX_EXPECTATION_HORIZON_DAYS=8` 与 `horizon_end` 双重限定。
- **schema 表达不了的规则不另造事实**：`close_hour is None`（现有 owner 视为整天关）→
  `OPENING_RULE_UNSUPPORTED/missing_close_hour`；`close_hour < open_hour`（跨午夜）→
  `OPENING_RULE_UNSUPPORTED/cross_midnight`；`open_hour == close_hour` → `NO_OPENING_WINDOW/empty_window`。
  窗口因此永远落在同一本地日期内，不发明“跨午夜营业”。
- **指纹**：`sha256:` + sha256(紧凑 JSON of `{world_id, location_id, timezone, rule{3 字段}}`（sort_keys）)。
  只做源变更识别；字段名与 `rule_fingerprint_purpose=source_change_detection_only` 都写明不是签名、不是权限。

## 4 测试与真实返回码

WSL Ubuntu｜`/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`｜
`PYTHONDONTWRITEBYTECODE=1`｜cwd = `/mnt/d/nyairo-v03-backend`。日志保留在会话 scratch `logs/`：
`opening-expectation-t1.log`（语法红灯，历史保留）、`opening-expectation-t2.log`（2 errors，历史保留）、
`opening-expectation-t3.log`（全绿）、`opportunities-regression.log`、`sample-opening.log`、`sample-catalog.log`。

| 命令 | 真实结果 |
|---|---|
| `python -m unittest tests.test_world_opening_expectation -v` | **Ran 12, OK, explicit exit 0**（`logs/opening-expectation-t3.log`） |
| `python -m unittest tests.test_world_daily_opportunities` | **Ran 10, OK, explicit exit 0**（同一目录的直接受影响旧包，`logs/opportunities-regression.log`） |
| 合跑：`python -m unittest tests.test_world_opening_expectation tests.test_world_daily_opportunities` | **Ran 22, OK, explicit exit 0**（`logs/final-targeted.log`，最终状态） |

12 个精确用例 ID（`tests.test_world_opening_expectation`）：

1. `OpeningExpectationRuleTest.test_open_close_boundaries_match_the_availability_owner`（5 个时刻逐例核对：
   05:59 → 06:00 开门、06:00/12:00/21:59 为 `open_now`、22:00 → 次日 06:00；每例都与
   `location_availability` 同 `now` 交叉核对）
2. `…test_weekdays_only_skips_the_weekend_and_crosses_the_week`（周六→周一、周五 18:00→周一 08:00、
   周一 09:00 `open_now`、`horizon_end − now == 8 天`）
3. `…test_always_open_locations_are_explicit_and_not_bounded`（`always_open`、`window_end=None`）
4. `…test_cross_day_boundary_moves_the_window_to_the_next_local_date`（22:59 `open_now` → 23:59
   次日 07:00，`as_of` 与 `expected_open_at` 不同日期）
5. `…test_naive_now_is_rejected_instead_of_guessed`（naive datetime 与字符串都抛 `DailyRhythmError`）
6. `…test_unknown_timezone_fails_closed_and_override_is_declared`（`TIMEZONE_UNKNOWN`；UTC 覆盖时
   `timezone_source=caller_override`、`world_timezone=Asia/Tokyo`、窗口按 UTC 时钟算到次日 06:00Z）
7. `…test_unknown_world_and_location_fail_closed`（`WORLD_PACKAGE_UNREADABLE`；
   `"nowhere"`/`""`/`"   "`/`None` → `LOCATION_UNKNOWN`）
8. `…test_unreadable_and_unsupported_rules_do_not_invent_opening_facts`（`"8"` 字符串、`weekdays_only="no"`、
   24/30 越界、非 mapping → `LOCATION_RULE_UNREADABLE` 且无 `rule_fingerprint`；20–4 → `cross_midnight`；
   缺 `close_hour` → `missing_close_hour`；9–9 → `NO_OPENING_WINDOW/empty_window`）
9. `…test_horizon_is_bounded_and_reports_when_no_window_fits`（`MAX_EXPECTATION_HORIZON_DAYS==8`；
   把上界临时改为 0 → `NO_WINDOW_WITHIN_HORIZON`，常开地点仍 available）
10. `…test_rule_fingerprint_is_change_detection_only`（同一规则不同 `now` 同指纹；换地点/换规则变指纹；
    `sha256:`+64 hex；结果里没有 `signature/permission/granted/allowed` 键）
11. `OpeningExpectationCatalogTest.test_catalog_entry_adds_window_without_upgrading_validity`（真实包 +
    真实 fixture 实例：目录 `validity` 仍 `NOW_ONLY`/`not_promised`、`observed=not_observed`、
    条目字段与直接调用 owner **完全相等**、短视图无该字段、**调用前后实例目录 sha256 快照一致（零写入）**）
12. `…test_weekend_closed_store_reports_next_weekday_window_in_catalog`（周六 `cafe` 闭店、
    `affordances == []`、`opening_expectation` 指向周一 08:00–18:00）

夹具：`tests/test_world_opening_expectation.py` 用 `tempfile.mkdtemp(prefix="fa-world-opening-",
dir="/tmp/nyaw05r.jdK3q")`，本轮真实留下 `/tmp/nyaw05r.jdK3q/fa-world-opening-j937rc72` 与
`…-zso2_6hk`（**未清理**；`/tmp/nyaw05r.jdK3q` 现已 7779 个条目，其中其余为其它包所有）。
纯 rhythm 用例不落盘；catalog 用例走真实 owner store 播种（与 W 包同一 `seed_world_state` 手法），
不 import Body 基座。

## 5 门禁与工作区状态

| 门禁 | 结果 |
|---|---|
| `node --experimental-strip-types <skill>/scripts/verify-agent-note-tree.ts`（repo cwd） | `ok: 26 note(s) tree and relative links verified`，**exit 0** |
| `node --experimental-strip-types <skill>/scripts/verify-agent-note-format.ts`（repo cwd） | `ok: 26 note(s) verified`，**exit 0** |
| `git diff --check` | 见 §6（本包文件干净） |
| `git diff --cached --name-only` | 空（未 stage） |
| `git rev-parse HEAD` | `7f09d26df64e054faf51ba35ad85c64f73a5a837`（未变） |

## 6 已知缺口 / 未测（本包边界）

1. **跨午夜规则完全不支持**：`location_availability` 本身就不认 `close_hour < open_hour`，
   本包按裁决只报 `OPENING_RULE_UNSUPPORTED`。要支持得先改营业 owner 的小时语义（另立任务）。
2. **只有整点精度**：规则无分钟字段；`start/end` 一律 `mm:ss = 0`。
3. **DST 边界未单测**：以 `Asia/Tokyo`（无夏令时）为唯一真实世界；在有 DST 的时区里
   “某天 `open_hour` 不存在”的极端情况未定义行为，代码按名义本地时刻构造。
4. **8 天上界是常量**：`NO_WINDOW_WITHIN_HORIZON` 在真实世界规则下不可达（任何 `open<close` 规则
   一周内必有一次），只用 `mock.patch` 把上界收紧到 0 天验证了分支；这是一个防御分支，不是常态。
5. **未接 Life/Agenda**：本包不创建 Agenda、不落任何持久化、不发通知；`requires_recheck` 目前只是
   契约字段，由后续 L 包真正执行“到点重读”。目录字段也只被本包测试消费，`observe_world` 的短视图
   不含它（有意为之）。
6. **未跑**：全仓/World 全套、`tests.test_world_neighborhood_owner_chain`（他人写集，正在返修）、
   Life 集成测试。按派工只跑了新增 12 个用例 + 直接受影响的 `tests.test_world_daily_opportunities`（10 个）。

## 7 本包相关 git 状态（全部 UNCOMMITTED）

```
 M components/world/scripts/world_daily_rhythm.py          # +383/−1
?? components/world/scripts/world_daily_opportunities.py   # 本包在其上新增字段（文件本身仍是 W 包未跟踪文件）
?? tests/test_world_opening_expectation.py
?? docs/engineering/world-opening-expectation-result.md
?? docs/engineering/world-opening-expectation-usage.md
?? .agents/notes/implemented/architecture/2026-10-19-world-opening-expectation.md
```

同仓其它 `M`/`??`（`components/life/*`、`components/world/scripts/world_living_skeleton.py`、
`world_runtime_data.py`、`world_travel_delivery.py`、`world_body_substrate.py`、
`world_action_resolver.py`、`body_consequence.py`、`components/world/service/*`、`chiyo_bundle/*`、
旧 tests、其他 docs）都不是本包的，未触碰、未回滚。

**工具副作用（需父级知情，未自行清理）**：把 note 从 `proposed/` 改到 `implemented/` 时，编辑器的
`MV` 目标按工作区根（`D:\`）解析，note 一度落到 `D:\.agents\notes\implemented\architecture\`；
随后用 `MV nyairo-v03-backend/.agents/...` 把它移回仓库正确路径（内容 100 行完整、`Status: implemented`）。
`D:\.agents\notes\implemented\architecture\` 现在只剩**空目录**（无任何文件）。按本包“禁止物理删除/
rmdir”的纪律没有删，留给父级决定是否清理。
