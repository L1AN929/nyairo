# Agent Note: 单地点的有限未来营业预期（World owner 侧）

Status: implemented

## Problem

Future/Agenda（F-096/F-097）要能认领“等店开门再去”这类未来事项，但 World 侧现在只有两样东西：

1. `world_daily_rhythm.location_availability` 只回答“此刻开没开”（`OPEN`/`CLOSED` + reason）；
2. `world_daily_opportunities.location_opportunity_catalog` 的每条都明确 `validity.scope = NOW_ONLY`、
   `future = not_promised`。

于是下游只有两种坏选择：要么把“现在关着”当成“永远不会开”，要么让 Life 按店名/常识自己猜营业时间
——那就是第二套营业事实，和 `rules.location_rules` 一改就两边打架。需要的是：**同一份世界规则算出的、
有限范围的、明确“只是预计”的下次开放窗口**，而且它不能顺手变成营业保证或动作授权。

## Decision

营业规则的唯一 owner `components/world/scripts/world_daily_rhythm.py` 现在提供只读投影
`location_opening_expectation(location_id, now, *, timezone_name=None, environment=None)`：

- 规则来源只有 `world_runtime_data.active_world().location_rule()`（包世界即 `rules.json` 的
  `location_rules`，未配置包时是汐见市内建 `_RULES`）。Life 与目录都不持有第二套营业时间。
- “现在开没开”与 `location_availability` 完全相同的小时语义（`open_hour <= hour < close_hour`、
  `weekdays_only` 周末整天闭店、`open_hour is None` 常开），所以同一个 `now` 上两者不会互相矛盾。
- 输出 `kind = world_location_opening_expectation`、`schema_version = world.location.opening_expectation.v1`、
  `status: available|unavailable`。available 时给 `expected_open_at`（现在开着就等于 `as_of`，作为
  “可考虑的起点”）、`window_open_at`、`window_end`（常开为 `None`）、`window_state`
  （`open_now`/`opens_later`/`always_open`），并附 `world_id/location_id/source/as_of(+_utc)/
  timezone/world_timezone/timezone_source/horizon_days(<=8)/horizon_end/rule/rule_kind/
  rule_fingerprint/rule_fingerprint_scope/rule_fingerprint_purpose/requires_recheck:true/action_grant:false/note`。
- `source` 取值与 `world_daily_opportunities.schedule_source` 同一套词汇：
  `world_package_schedule` / `world_daily_rhythm_builtin`。
- 时钟严格 aware：naive `now` 抛 `DailyRhythmError`；旧的 `_local()`（naive 当 UTC）保持原样不动，
  只服务既有调用方。
- fail closed 的 `reason_code`：`WORLD_PACKAGE_UNREADABLE`、`WORLD_DATA_UNAVAILABLE`、`LOCATION_UNKNOWN`、
  `LOCATION_RULE_UNREADABLE`、`TIMEZONE_UNKNOWN`、`OPENING_RULE_UNSUPPORTED`（`rule_kind` =
  `cross_midnight`（`close_hour < open_hour`）/ `missing_close_hour`）、`NO_OPENING_WINDOW`
  （`rule_kind = empty_window`，即 `open_hour == close_hour`）、`NO_WINDOW_WITHIN_HORIZON`。
  不可用时 `expected_open_at`/`window_open_at`/`window_end` 全为 `null`，`action_grant` 仍为 `false`。
- 窗口搜索按小时精度、`offset ∈ [0, MAX_EXPECTATION_HORIZON_DAYS]`（常量 8 天），并再受
  `horizon_end = as_of + 8 天` 约束；跨午夜规则不解释，直接 `unsupported`，窗口因此永远落在同一本地日期内。
- `rule_fingerprint = "sha256:" + sha256(紧凑 JSON of {world_id, location_id, timezone,
  rule{open_hour, close_hour, weekdays_only}}，sort_keys)`，`rule_fingerprint_purpose =
  source_change_detection_only`：只做源变更识别，不是签名、不是权限。
- `components/world/scripts/world_daily_opportunities.py` 只做投影接线：`location_opportunity_catalog`
  每条机会新增独立字段 `opening_expectation`（直接调上面的 owner），`availability`、顶层
  `validity{scope: NOW_ONLY, future: not_promised}`、`observed`、`affordances`、`action_grant`
  全部不变；`compact_projection` 有意不带该字段（`observe_world` 的 16KB 预算超限会整块省略
  `reachable_opportunities`），要未来窗口就调 owner 或全量目录。

## Alternatives considered

- **在 Life/Agenda 侧按地点名或常识补营业时间**：最容易让“明天再去”立刻跑起来，也不需要动 World；
  但那等于第二个营业真相，世界包一改（例如咖啡店从 8–18 改成 9–17 或加周末休息）Life 会继续按旧表
  劝人去，且没人能证明它来自哪条规则。否决。
- **把 `location_availability` 的返回值直接扩成“未来有效”**：调用方一行都不用改；但那会把
  `NOW_ONLY` 承诺悄悄升级成未来保证，既有 `validity.future = not_promised` 与闭店仍可到门口等
  语义全部失真。否决，改为新增独立字段、旧字段语义零改动。
- **在 `world_daily_opportunities` 里算窗口**：目录已经有营业、可达、可见物件三层，多一层顺手；
  但窗口必须与 `location_availability` 同一套小时语义，写在目录里迟早分叉。否决，算法留在 rhythm
  owner，目录只调它。
- **给跨午夜规则（`close_hour < open_hour`，如 20–4）造一套“跨日本地窗口”**：现实里便利店/酒吧
  确实这样营业，看起来比报 `unsupported` 更有用；但 `location_availability` 现在根本不支持这种
  规则（它对 `open_hour <= hour < close_hour` 成立才判 OPEN，跨午夜规则在它眼里是整天关门），
  预期一旦自己解释成“开着”，就与营业 owner 正面冲突。否决：报 `OPENING_RULE_UNSUPPORTED` +
  `rule_kind: cross_midnight`，要支持得先改营业 owner 的小时语义。
- **把窗口塞进 `observe_world` 的短视图**：Agenda 接线能少一次调用；但 16KB 预算下多几个字段就可能
  把整个 `reachable_opportunities` 挤掉，代价是“当前事实”消失。否决，短视图保持原样。
- **在指纹里加时间戳/调用者/模型签名**：看起来更“可信”；但指纹只服务于“规则源变了没有”，
  加时间戳会让同一个规则每次指纹都变，反而失去变更识别能力，也容易被误读成授权。否决。

## Consequences

- **收益**：Agenda 的“等店开门”第一次有可复核的 World 事实：来源（包规则/内建规则）、`as_of`、
  时区、真实规则原文、窗口两端、上界与指纹都能追到同一份 `location_rules`；规则一变指纹就变，
  下游可以拒用旧预测，不需要全局 hash 或重读所有包。
- **收益**：`expected_open_at` 与 `location_availability` 在同一 `now` 上互相印证（测试逐例交叉核对），
  因此不会出现“门开着但预期说要等”这类分裂。
- **收益**：所有读不懂的输入（世界/地点/时区/规则形状/跨午夜/空窗口/超出上界）都给出明确
  `reason_code`，且 `expected_open_at` 为 `null`——没有“也许开着”的默认值，也没有把远处内景当观察、
  把预期当授权。
- **代价与已知上限**：上界 8 天与整点精度都是常量/规则精度决定的，长承诺与分钟级规则要另立契约；
  跨午夜规则被明确挡在门外（`OPENING_RULE_UNSUPPORTED`），有 DST 的时区里“某天 `open_hour` 不存在”
  的极端行为未定义；`requires_recheck` 目前只是契约字段，真正“到点重读”由后续 L 包执行。

## Verification

- `tests/test_world_opening_expectation.py`（新，12 用例，WSL `w04venv/bin/python -m unittest`）：
  开关边界（05:59/06:00/12:00/21:59/22:00，每例与 `location_availability` 交叉核对）、
  `weekdays_only` 周末→周一与周五 18:00→周一 08:00、跨日本地日期翻页（22:59 → 次日 07:00）、
  常开语义、naive 时钟拒绝、未知时区与 UTC 覆盖、未知世界/地点、规则形状错误、跨午夜与空窗口、
  上界收紧到 0 天的 `NO_WINDOW_WITHIN_HORIZON`、指纹只随源变化、目录字段与直接调用相等且
  实例目录 sha256 快照前后一致（零写入）、短视图无该字段。`Ran 12, OK, exit 0`。
- 直接受影响的旧包 `tests.test_world_daily_opportunities`：`Ran 10, OK, exit 0`（加字段未改原语义）。
- 门禁：`verify-agent-note-tree.ts` 与 `verify-agent-note-format.ts`（repo cwd，`node
  --experimental-strip-types`）都 exit 0（26 notes）；`git diff --check` 干净；
  `git diff --cached` 空；HEAD 仍为 `7f09d26df64e054faf51ba35ad85c64f73a5a837`。
- 细节、真实样例、日志路径与未测项见 `docs/engineering/world-opening-expectation-result.md`；
  给 L 的用法见 `docs/engineering/world-opening-expectation-usage.md`。
