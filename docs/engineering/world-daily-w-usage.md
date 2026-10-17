# 短用法：只读机会目录（F/I 接线）

> 事实 owner 不变：可达性=世界包走道图，营业/可做事项=`world_daily_rhythm`，可见物件=世界包场景。
> 这个投影**不是动作授权**，每条目 `action_grant: false`。

## 1. 直接读目录（完整形状）

```python
import sys
sys.path.insert(0, "components/world/scripts")   # 与 observe_world 同一 sys.path 习惯
import world_daily_opportunities as wdo

catalog = wdo.location_opportunity_catalog(
    environment,             # 本次请求的 env（含 CHIYO_WORLD_PACKAGE / CHIYO_WORLD_HOME）
    now,                     # aware datetime；None = 现在
    hermes_home=world_home,  # 与 observe_world 用的同一个世界 home
)
# catalog["status"] == "available" | "unavailable"（带 reason_code）
# catalog["schedule_source"] == "world_package_schedule" | "world_daily_rhythm_builtin"
# catalog["validity"] == {"as_of", "scope": "NOW_ONLY", "future": "not_promised"}

for item in catalog["opportunities"]:
    item["location_id"]            # 世界节点 id（place 级；室内区域在 item["indoor_areas"]）
    item["here"]                   # 就在这儿
    item["reachability"]           # {"status": "here"|"reachable", "route": [...], "walk_seconds": int, "source": "world_topology"}
    item["availability"]           # {"status": "OPEN"|"CLOSED"|"UNKNOWN", "reason_code", "source"}
    item["affordances"]            # [{"affordance_id","description","source","action_grant": false}]，闭店恒为 []
    item["observed"]               # {"status": "visible_here"|"not_observed", ...}，别处只有公开时间表
```

## 2. 从 `observe_world` 拿短视图

```python
observation = wls.observe_world(hermes_home=world_home, now=now, environment=env)
projection = observation.get("reachable_opportunities")   # 可能因 16KB 预算被省略
# 每项只有：location_id/name/kind/here/walk_seconds/reach/availability/availability_reason/
#          affordance_ids/observed/action_grant
```

要全量（route、description、validity、indoor_areas）就调第 1 节的目录，不要依赖短视图。

## 3. I：接 candidate 的过滤建议

```python
def open_opportunities(catalog, max_walk_seconds=900):
    if catalog.get("status") != "available":
        return []                                   # 原因看 catalog["reason_code"]
    return [
        item for item in catalog["opportunities"]
        if item["availability"]["status"] == "OPEN"
        and item["affordances"]                        # 闭店/未知一律没有店内动作
        and item["reachability"]["status"] in {"here", "reachable"}
        and item["reachability"]["walk_seconds"] <= max_walk_seconds
    ]
```

- 每个决策回合重算一次（`NOW_ONLY`，不缓存到下一次 `now`）。
- CLOSED 的地点仍可出现在“去哪儿等开门/到门口折返”的候选里：可达性独立于营业。
- 不要从 affordance 推出“已获授权”；真要做事仍走 Resolver/owner 与既有 candidate/Adoption。

## 4. F：gateway 侧调用

`observe_world` 已经带 `reachable_opportunities`，不需要 gateway 自己算；要单独取就地调
`location_opportunity_catalog(...)`。饮食消费仍是饮食 owner 的事：
本投影里 `BUY_DRINK` 只表示“店开着、时间表上有这件事”，不代表有库存、有余额、能吃。

## 5. 走：仍是既有 MOVE

```python
wls.plan_move_to("cafe", hermes_home=world_home, environment=env, now=now)
# 闭店也返回 ready；结果里的 destination_availability 只是提示（CLOSED/OPEN/None）
```

单段 MOVE 只走到相邻节点；多段行程用 `chiyo_bundle/world_move_tool.py` 按 `reachability.route` 逐段提交。
