# 短用法：单地点有限未来营业预期（给 Future/Agenda 接线）

> 事实 owner 不变：营业规则仍只有 `world_daily_rhythm` 一份（包世界 = `rules.json` 的
> `location_rules`，内建 = 汐见市 `_RULES`）。这个投影**只是预计**：
> `requires_recheck: true`、`action_grant: false`，不保证到点真的开门。

## 1 直接问一个地点

```python
import sys
sys.path.insert(0, "components/world/scripts")     # 与 observe_world 同一 sys.path 习惯
import world_daily_rhythm as wdr

exp = wdr.location_opening_expectation(
    "cafe",          # 地点 id；"cafe/main" 这类区域节点会委托到 cafe
    now,             # 必须 aware datetime（naive 抛 DailyRhythmError）
    environment=env, # 与 active_world() 同一个 env（含 CHIYO_WORLD_PACKAGE / CHIYO_WORLD_HOME）
)

if exp["status"] == "available":
    exp["window_state"]        # "open_now" | "opens_later" | "always_open"
    exp["expected_open_at"]    # 从这个时刻起预计可以进去（现在开着 == as_of）
    exp["window_open_at"]      # 该窗口的名义开始（常开为 None）
    exp["window_end"]          # 窗口结束（不含）；常开为 None
    exp["rule"]                # {"open_hour", "close_hour", "weekdays_only"}（真实规则原文）
    exp["rule_fingerprint"]    # 规则源变更识别；不是签名、不是权限
    exp["horizon_days"]        # <= 8
else:
    exp["reason_code"]         # 见下表；不要退化成“也许开着”
```

`exp` 还带 `world_id`、`location_id`、`source`（`world_package_schedule` / `world_daily_rhythm_builtin`）、
`as_of`(+`_utc`)、`timezone`/`world_timezone`/`timezone_source`、`horizon_end`、
`rule_kind`、`always_open`、`note`。

## 2 从机会目录拿（已经算好）

```python
import world_daily_opportunities as wdo
catalog = wdo.location_opportunity_catalog(env, now, hermes_home=world_home)
for item in catalog["opportunities"]:
    item["availability"]        # 仍是 NOW_ONLY 的“此刻开没开”
    item["opening_expectation"] # 本接口的结果（同一次调用、同一份规则）
```

- 顶层 `validity` 仍是 `{"scope": "NOW_ONLY", "future": "not_promised"}`——**加字段不等于升级承诺**。
- `observed` 仍只对当前位置 `visible_here`，别处是 `not_observed/OUT_OF_VIEW`：未来营业不附带“看过店内”。
- `compact_projection`（也就是 `observe_world.reachable_opportunities`）**故意不带**
  `opening_expectation`（16KB 预算下会把整块当前事实挤掉）。要未来窗口就调第 1 节或全量目录。

## 3 Agenda 侧该怎么用（L 包）

```python
exp = wdr.location_opening_expectation(location_id, now, environment=env)
if exp["status"] != "available":
    return None                      # 规则读不到/不支持：不做未来认领，不猜
candidate_at = datetime.fromisoformat(exp["expected_open_at"])
# 认领时保存：location_id、candidate_at、window_end、rule_fingerprint、world_id、timezone
```

到点后（真正的重新考虑）：

1. **重读**同一个 `location_opening_expectation`（`requires_recheck`），比对 `rule_fingerprint`；
   规则变了就按新窗口重新判断，**不拿旧预测行动**。
2. 再读 `location_availability` / 目录确认此刻真的开着，以及目标/授权/预算/暂停等其它条件。
3. 预测过期、规则不匹配、世界不可读 → 明确“这次不做”，不自动无限改期。
4. `expected_open_at` 只是“可以考虑的起点”，不是“必须去做”，也不是动作授权。

## 4 读不懂的时候会怎样（fail closed）

| `reason_code` | 含义 | `rule_kind` |
|---|---|---|
| `WORLD_PACKAGE_UNREADABLE` / `WORLD_DATA_UNAVAILABLE` | 活动世界都读不到 | — |
| `LOCATION_UNKNOWN` | 地点 id 不在世界里（含空串/非字符串） | — |
| `LOCATION_RULE_UNREADABLE` | 规则字段形状不对（类型/范围） | — |
| `TIMEZONE_UNKNOWN` | 时区名不是合法 IANA | — |
| `OPENING_RULE_UNSUPPORTED` | schema 表达不了：跨午夜或缺关门小时 | `cross_midnight` / `missing_close_hour` |
| `NO_OPENING_WINDOW` | `open_hour == close_hour`，永远没有窗口 | `empty_window` |
| `NO_WINDOW_WITHIN_HORIZON` | 8 天内找不到窗口（防御分支） | 原 `rule_kind` |
| `OPENING_EXPECTATION_UNAVAILABLE` | 只在目录里出现：owner 抛异常的兜底骨架 | — |

这些情况下 `expected_open_at`/`window_end` 一律 `null`，`action_grant` 一律 `false`。

## 5 不要做

- 不要按地点名或常识补营业时间；也不要缓存预测跨过 `as_of` 继续用。
- 不要把 `rule_fingerprint` 当签名/授权/“规则被批准”的证据——它只回答“源变了吗”。
- 不要把 `window_end` 当“保证开到那时”；闭店仍可到门口（MOVE 与营业解耦，见 W 包笔记）。
- 不要为了“跨午夜便利店”自己加规则：现在 owner 不支持 `close_hour < open_hour`，
  要么报 `unsupported`，要么先改营业 owner 再谈。
