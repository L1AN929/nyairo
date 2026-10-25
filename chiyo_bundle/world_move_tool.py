"""Explicit model movement using the existing World and real-time travel writers."""
from __future__ import annotations

import importlib
import json
import math
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

MOVE_TOOL_SCHEMA = {
    "name": "world_move",
    "description": (
        "在当前活动世界的地图中移动到指定地点或室内区域。"
        "目标可用标准 location_id、室内节点 place/area，或当前世界里唯一的地点/区域名称。"
        "非邻接目标按世界导航图自动选择耗时最短的路线并逐段中转，不会瞬移；"
        "启用真实步行计时时每次只出发当前一段，到达中转点后需再次调用最终目的地才会继续；"
        "在途中调用只返回当前行程状态，不会改向。"
    ),
    "parameters": {
        "type": "object",
        "properties": {"location": {"type": "string", "minLength": 1, "maxLength": 96,
                                    "description": "目的地点名称（如便利店）或标准 location_id"}},
        "required": ["location"],
        "additionalProperties": False,
    },
}

_ALIASES = {
    "家": "home", "房间": "home", "家里": "home", "千代的家": "home",
    "千代的家 / 房间": "home", "街道": "nearby_street", "街上": "nearby_street",
    "附近街道": "nearby_street", "家附近街道": "nearby_street",
    "便利店": "convenience_store", "附近的便利店": "convenience_store",
    "公园": "park", "学校": "school", "家居店": "home_goods_store",
    "家居用品店": "home_goods_store",
}

_LEGACY_LABELS = {
    "home": "家", "nearby_street": "街道", "convenience_store": "便利店",
    "park": "公园", "school": "学校", "home_goods_store": "家居店",
}


def _world_modules():
    # These legacy scripts use sibling imports. Install only our bundled script path,
    # never a path supplied by the caller, and do not import them at plugin startup.
    scripts = str(Path(__file__).resolve().parents[1] / "components/world/scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    return (importlib.import_module("world_living_skeleton"),
            importlib.import_module("world_travel_delivery"))


def _absolute_home(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("world home must be a nonempty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("world home must be absolute")
    return path


def resolve_world_home(environment=None, *, socket_path=None):
    """Explicit world home, canonical socket's owner home, then active profile."""
    env = os.environ if environment is None else environment
    if env.get("CHIYO_WORLD_HOME") is not None:
        return _absolute_home(env["CHIYO_WORLD_HOME"])
    socket_value = socket_path or env.get("WORLD_BODY_SOCKET")
    if not socket_value:
        if env.get("HERMES_HOME"):
            profile = _absolute_home(env["HERMES_HOME"])
        else:
            try:
                from hermes_constants import get_hermes_home
            except ImportError:
                profile = Path.home() / ".hermes"
            else:
                profile = _absolute_home(get_hermes_home())
        config = profile / "chiyo/config.json"
        if config.exists():
            settings = json.loads(config.read_text(encoding="utf-8"))
            socket_value = settings.get("world_body_socket")
            if settings.get("world_home"):
                return _absolute_home(settings["world_home"])
        if not socket_value:
            return profile
    socket = _absolute_home(socket_value)
    if tuple(socket.parts[-4:-1]) == ("data", "world", "run"):
        return socket.parents[3]
    if len(socket.parents) >= 2 and socket.parents[0].name == "run":
        return socket.parents[1]
    raise ValueError("world socket must belong to a canonical run directory")


def _minutes(travel, now):
    arrival = datetime.fromisoformat(travel["expected_arrival_at"].replace("Z", "+00:00"))
    return max(0, math.ceil((arrival - now).total_seconds() / 60))


def _label(location_id, world=None):
    """显示名：自定义世界用真实名称；只有默认汐见市保留旧短名兼容。"""
    name = None
    if world is not None:
        try:
            item = world.location(location_id)
        except Exception:
            item = None
        if isinstance(item, Mapping) and item.get("name"):
            name = str(item["name"])
    if name is not None and getattr(world, "is_custom", False):
        return name
    legacy = _LEGACY_LABELS.get(location_id)
    if legacy:
        return legacy
    return name if name is not None else location_id


def travel_context(environment=None, *, socket_path=None):
    """Optional read-only observation; never realizes arrivals in a chat read."""
    env = os.environ if environment is None else environment
    if str(env.get("CHIYO_WORLD_TRAVEL_TIME_ENABLED", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return ""
    _, wtd = _world_modules()
    now = datetime.now(timezone.utc)
    travel = wtd.observe_travel(hermes_home=resolve_world_home(env, socket_path=socket_path),
                                environment=env, now=now)
    if travel.get("status") != "in_progress":
        return ""
    destination = _label(travel["destination_location_id"], _active_world(env))
    return f"出行状态=正在步行前往{destination}的途中(剩余约{_minutes(travel, now)}分钟到达)"


def _runtime_data():
    """世界运行时数据模块（活动世界解析的唯一入口）。"""
    _world_modules()
    return importlib.import_module("world_runtime_data")


def _active_world(env):
    """本次请求环境的活动世界；只用于展示标签，失败时返回 None。"""
    try:
        return _runtime_data().active_world(env)
    except Exception:
        return None


def _target_index(world):
    """当前世界的"名称 → 节点"索引（地点名、室内节点名、区域名/区域 ID）。"""
    names = {}
    for place_id in world.locations:
        place = world.location(place_id)
        place_name = place.get("name") if isinstance(place, Mapping) else None
        if place_name:
            names.setdefault(str(place_name), []).append(place_id)
        for area in world.areas_of(place_id):
            node = world.state_node(place_id, area["area_id"])
            if not node:
                continue
            candidates = [area["area_id"]]
            area_name = area.get("name")
            if area_name:
                candidates.append(str(area_name))
                if place_name:
                    candidates.append(f"{place_name}·{area_name}")
            for candidate in candidates:
                if candidate:
                    names.setdefault(str(candidate), []).append(node)
    return {name: sorted(set(nodes)) for name, nodes in names.items()}


def _resolve_target(raw, world):
    """把模型给的地点文本解析为当前世界唯一的导航节点。

    解析顺序：精确节点 ID → 大小写不敏感的节点 ID → 世界内唯一名称
    （地点名 / 区域名 / 区域 ID）→ 汐见市旧中文别名。旧别名只作最后回退，
    在自定义世界里不会压过同名真实目标；歧义与未知都明确拒绝，不取第一个。
    """
    text = raw.strip()
    nodes = world.all_nodes()
    # 标准节点 ID（含 place/area；入口区 place/entrance 规范化为 place）。
    canonical = world.canonical_node(text)
    if canonical is not None and canonical in nodes:
        return canonical, None
    lowered = text.lower()
    by_id = [node for node in nodes if node.lower() == lowered]
    if len(by_id) == 1:
        return by_id[0], None
    if len(by_id) > 1:
        return None, "AMBIGUOUS_LOCATION"
    names = _target_index(world)
    matched = names.get(text)
    if matched is None:
        matched = sorted({node for name, group in names.items()
                          if name.lower() == lowered for node in group})
    if len(matched) == 1:
        return matched[0], None
    if len(matched) > 1:
        return None, "AMBIGUOUS_LOCATION"
    legacy = _ALIASES.get(text)
    if legacy is not None and legacy in nodes:
        return legacy, None
    return None, "UNKNOWN_LOCATION"


def _move_failed(moved, requested, current, committed):
    """一段被 owner 拒绝：立刻停止，如实报告最后确认位置，不做补偿写入。"""
    return _result(False, moved.get("status", "failed"),
                   "世界未确认本段移动成功；请重新观察当前位置。",
                   reason_code=moved.get("reason_code", "MOVE_FAILED"),
                   requested_location_id=requested,
                   location_id=committed[-1] if committed else current,
                   committed_route=list(committed))


def _journey_module():
    """持久旅程引擎（与工具同包；懒加载，不在插件启动时导入世界脚本）。"""
    _world_modules()
    return importlib.import_module("chiyo_bundle.world_journey")


def _submit_port(env):
    """World 自己的正式授权入口；不可达时返回 None，让引擎失败关闭。

    工具不会退回进程内直写：每一段都是新的 Action，各自需要一次性授权。
    """

    try:
        module = importlib.import_module("chiyo_bundle.journey_authorization")
    except ImportError:  # pragma: no cover - 直接以脚本路径导入时的回退
        module = importlib.import_module("journey_authorization")
    try:
        return module.default_submit_port(environment=env)
    except Exception:
        return None



def _control_module():
    """持久暂停控制状态模块（懒加载）。"""
    _world_modules()
    return importlib.import_module("chiyo_bundle.world_control")


def _continuation_note(home, env, target, world):
    """中转文案：只有观测到健康且未暂停的后台推进器时才承诺自动续行。

    只读探测（零创建）；探测失败一律按"没有已确认在线的推进器"如实表达。
    """
    report = {}
    try:
        runner = importlib.import_module("chiyo_bundle.world_runner")
        report = runner.runner_status(home, environment=env)
    except Exception:
        report = {}
    state = report.get("runner") or {}
    control = report.get("control") or {}
    healthy = (
        state.get("classification") == "running"
        and state.get("health") == "healthy"
        and control.get("paused") is not True
    )
    if healthy:
        return (f"这是前往{_label(target, world)}的中转；后台推进器正在运行，"
                f"到达后会自动继续前往{_label(target, world)}。")
    return (f"这是前往{_label(target, world)}的中转；旅程已持久保存，"
            "但没有已确认在线的后台推进器，需要后续推进（run/once）才会继续。")

def _journey_result(projection, world, requested, home=None, env=None):
    """把旅程投影映射为既有 JSON 结构（字段兼容，新增 journey_id/status）。"""
    status = projection.get("status")
    target = projection.get("target_location_id") or requested
    common = {"journey_id": projection.get("journey_id"),
              "requested_location_id": requested,
              "target_location_id": target,
              "route": projection.get("route")}
    if status == "already_there":
        return _result(True, "already_there", f"已经在{_label(target, world)}了。",
                       location_id=projection.get("location_id") or target, **common)
    if status == "completed":
        return _result(True, "arrived", f"已到达{_label(target, world)}。",
                       location_id=projection.get("location_id") or target,
                       segments_confirmed=projection.get("segments_confirmed"), **common)
    if status == "started":
        destination = projection.get("destination_location_id")
        minutes = projection.get("estimated_minutes")
        message = f"已出发，正在步行前往{_label(destination, world)}，预计{minutes}分钟到达。"
        if destination != target:
            message += _continuation_note(home, env, target, world)
        return _result(True, "started", message, destination_location_id=destination,
                       estimated_minutes=minutes, **common)
    if status == "in_transit":
        destination = projection.get("destination_location_id")
        remaining = projection.get("remaining_minutes")
        return _result(True, "in_progress",
                       f"正在步行前往{_label(destination, world)}，剩余约{remaining}分钟到达。",
                       destination_location_id=destination, remaining_minutes=remaining, **common)
    if status == "pending":
        # 旅程已耐久保存但当前段没有在途记录：只能靠后续显式推进继续。
        return _result(True, "pending", "旅程已保存，后续推进可继续。",
                       location_id=projection.get("location_id"), **common)
    if status == "disabled":
        return _result(False, "disabled", "真实步行计时未启用，未创建旅程。",
                       reason_code="TRAVEL_TIME_DISABLED", **common)
    if status == "paused":
        return _result(False, "paused",
                       "世界处于持久暂停：不会接受新的移动或启动新段；"
                       "已出发的一段仍会按真实时间到达并确认。",
                       reason_code=projection.get("reason_code") or "JOURNEY_PAUSED",
                       location_id=projection.get("location_id"), **common)
    if status == "control_blocked":
        return _result(False, "control_blocked",
                       "暂停控制状态无法读取：已按 fail closed 拒绝新的移动与新的段。",
                       reason_code=projection.get("reason_code") or "CONTROL_STATE_CORRUPT",
                       location_id=projection.get("location_id"), **common)
    if status in {"feature_disabled", "settle_only"}:
        return _result(False, "disabled" if status == "feature_disabled" else "pending",
                       ("真实步行计时未启用，旅程未推进。"
                        if status == "feature_disabled"
                        else "本次只结算已出发的段，未发起下一段。"),
                       reason_code=projection.get("reason_code"),
                       location_id=projection.get("location_id"),
                       destination_location_id=projection.get("destination_location_id"),
                       **common)
    return _result(False, status or "blocked",
                   "旅程未继续：目标与记录已保留，请先处理阻塞原因。",
                   reason_code=projection.get("reason_code") or "JOURNEY_UNAVAILABLE",
                   location_id=projection.get("location_id"),
                   destination_location_id=projection.get("destination_location_id"),
                   **common)


def _result(ok, status, message, **data):
    return json.dumps({"ok": ok, "status": status, "message": message, **data}, ensure_ascii=False)


def move_handler(args, **kwargs) -> str:
    """Fail closed on input, feature gates, corrupt stores and writer failures."""
    try:
        if not isinstance(args, Mapping) or set(args) != {"location"}:
            return _result(False, "rejected", "需要且仅接受 location 字符串。", reason_code="INVALID_ARGUMENTS")
        raw = args["location"]
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 96:
            return _result(False, "rejected", "location 必须为有效地点名称。", reason_code="INVALID_LOCATION")
        env = dict(os.environ if kwargs.get("environment") is None else kwargs["environment"])
        home = resolve_world_home(env)
        wls, wtd = _world_modules()
        if not wls.feature_enabled(env):
            return _result(False, "disabled", "世界移动功能尚未启用。", reason_code="FEATURE_DISABLED")
        # 活动世界必须来自本次请求环境；目标解析与后续提交共用同一个世界。
        world = _runtime_data().active_world(env)
        target, reason = _resolve_target(raw, world)
        if target is None:
            if reason == "AMBIGUOUS_LOCATION":
                return _result(False, "rejected", "该名称在当前世界对应多个地点，请改用标准地点 ID。",
                               reason_code=reason)
            return _result(False, "rejected", "未知地点，请使用当前世界地图中的地点 ID 或名称。",
                           reason_code=reason)
        # 持久暂停闸门：工具、accept、advance、once 与即时模式都不能绕过。
        gate = _control_module().gate(home)
        if gate["blocked"]:
            paused = gate["status"] == "paused"
            return _result(
                False,
                "paused" if paused else "control_blocked",
                ("世界处于持久暂停：不会接受新的移动或启动新段；"
                 "已出发的一段仍会按真实时间到达并确认。"
                 if paused else
                 "暂停控制状态无法读取：已按 fail closed 拒绝新的移动与新的段。"),
                reason_code=gate["reason_code"],
                paused=paused,
                requested_location_id=target)
        now = kwargs.get("now") or datetime.now(timezone.utc)
        request_key = kwargs.get("request_key")
        options = {"hermes_home": home, "environment": env, "now": now}
        due = wtd.realize_due_travel(**options)
        if due.get("status") not in {"disabled", "none", "in_progress", "arrived"}:
            return _result(False, "unavailable", "无法确认出行状态，未发起新的移动。",
                           reason_code=due.get("reason_code", "TRAVEL_UNAVAILABLE"))
        # 已有计时旅程时：不接受新目标，也不允许切到即时模式绕过它瞬移。
        journey = _journey_module()
        observed = journey.observe_journey(hermes_home=home, environment=env, now=now)
        observed_status = observed.get("status")
        if observed_status == "unavailable":
            return _result(False, "unavailable", "旅程记录无法读取，未移动。",
                           reason_code=observed.get("reason_code") or "JOURNEY_STATE_CORRUPT")
        if observed_status in {"pending", "in_transit", "unknown"}:
            active_target = observed.get("target_location_id")
            if not wtd.travel_enabled(env):
                return _result(False, "blocked",
                               "已有未完成的计时旅程；关闭真实步行计时时不会绕过它移动。",
                               reason_code="JOURNEY_ACTIVE_TRAVEL_DISABLED",
                               journey_id=observed.get("journey_id"),
                               active_target_location_id=active_target,
                               destination_location_id=observed.get("destination_location_id"),
                               requested_location_id=target)
            if active_target != target:
                return _result(False, "in_progress",
                               f"正在前往{_label(active_target, world)}的旅程中；本次请求不会覆盖它。",
                               reason_code="JOURNEY_TARGET_LOCKED",
                               journey_id=observed.get("journey_id"),
                               active_target_location_id=active_target,
                               destination_location_id=observed.get("destination_location_id"),
                               remaining_minutes=observed.get("remaining_minutes"),
                               requested_location_id=target)
            advanced = journey.advance_journey(
                hermes_home=home, environment=env, now=now,
                submit_port=_submit_port(env))
            return _journey_result(advanced, world, target, home, env)
        if observed_status == "blocked":
            # W03 统一语义：工具不会自动替换被阻塞的旅程；只有低层显式
            # accept_journey 才会归档旧旅程并接受新目标。
            return _result(False, "blocked",
                           "上一段旅程被阻塞，本次不移动；目标与记录已保留。"
                           "工具不会自动替换它——只有低层显式 accept_journey 能归档旧旅程并开始新目标。",
                           reason_code=observed.get("reason_code") or "JOURNEY_BLOCKED",
                           journey_id=observed.get("journey_id"),
                           active_target_location_id=observed.get("target_location_id"),
                           requested_location_id=target)
        travel = wtd.observe_travel(**options)
        if travel.get("status") == "in_progress":
            destination = travel["destination_location_id"]
            minutes = _minutes(travel, now)
            return _result(True, "in_progress",
                           f"正在步行前往{_label(destination, world)}，剩余约{minutes}分钟到达。",
                           destination_location_id=destination, remaining_minutes=minutes)
        state = wls.read_move_state(**options)
        if state is None:
            return _result(False, "unavailable", "世界尚未初始化，未移动。", reason_code="WORLD_NOT_INITIALIZED")
        current = state["world_location_id"]
        if current == target:
            return _result(True, "already_there", f"已经在{_label(target, world)}了。", location_id=target)
        route = world.route_to(current, target)
        if not route:
            return _result(False, "rejected", "地图中没有可用路线。", reason_code="LOCATION_NOT_REACHABLE",
                           requested_location_id=target, from_location_id=current)
        if wtd.travel_enabled(env):
            # 计时旅程：耐久意图 + 单段 owner 提交，由旅程引擎负责推进与恢复。
            accepted = journey.accept_journey(target=target, hermes_home=home, environment=env,
                                              now=now, request_key=request_key,
                                              submit_port=_submit_port(env))
            return _journey_result(accepted, world, target, home, env)
        committed = []
        for step in route:
            # 即时段提交边界：与 set_pause 共用同一把 journey 锁，并在锁内重校验持久
            # 暂停闸门。锁序仍是 journey → world → travel（不持 owner 锁取旅程锁），
            # 这里也不调用 accept/advance，避免非重入锁递归死锁。因此 pause 至多等待
            # “当前这一段”提交结束；pause 确认后绝不再提交下一段。
            with journey.journey_lock(home):
                gate = _control_module().gate(home)
                if gate["blocked"]:
                    paused = gate["status"] == "paused"
                    return _result(
                        False,
                        "paused" if paused else "control_blocked",
                        ("世界处于持久暂停：本段之后不再提交新的段，已提交的段保留。"
                         if paused else
                         "暂停控制状态无法读取：已按 fail closed 停止后续移动。"),
                        reason_code=gate["reason_code"],
                        paused=paused,
                        requested_location_id=target,
                        location_id=committed[-1] if committed else current,
                        committed_route=list(committed),
                        route=route)
                moved = wls.apply_move_to(step, **options)
            if moved.get("status") != "applied":
                return _move_failed(moved, target, current, committed)
            if moved.get("travel"):
                started = moved["travel"]
                minutes = math.ceil(int(started["route_duration_seconds"]) / 60)
                message = f"已出发，正在步行前往{_label(step, world)}，预计{minutes}分钟到达。"
                if step != target:
                    message += _continuation_note(home, env, target, world)
                return _result(True, "started", message, destination_location_id=step,
                               requested_location_id=target, estimated_minutes=minutes, route=route)
            committed.append(step)
        return _result(True, "arrived", f"已到达{_label(target, world)}。", location_id=target, route=route)
    except Exception as exc:
        # Do not leak filesystem paths, configuration or raw exception messages.
        return _result(False, "failed", "世界移动状态无法确认，未继续移动；请检查世界服务。",
                       reason_code="WORLD_MOVE_ERROR", error_type=type(exc).__name__)
