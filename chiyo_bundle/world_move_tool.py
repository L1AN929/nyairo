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
        "在汐见市移动到指定地点。支持家、街道、便利店、公园、学校、家居店及地点 ID。"
        "非邻接地点自动经街道中转；启用真实步行计时时不会瞬移。"
        "在途时返回剩余时间；若先步行到中转街道，到达后再次调用最终目的地以继续。"
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


def _label(location_id):
    return {"home": "家", "nearby_street": "街道", "convenience_store": "便利店",
            "park": "公园", "school": "学校", "home_goods_store": "家居店"}.get(location_id, location_id)


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
    destination = _label(travel["destination_location_id"])
    return f"出行状态=正在步行前往{destination}的途中(剩余约{_minutes(travel, now)}分钟到达)"


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
        target = _ALIASES.get(raw.strip(), raw.strip().lower())
        catalog = {item["location_id"]: item for item in wls.location_catalog()}
        if target not in catalog:
            return _result(False, "rejected", "未知地点，请使用地图中的地点名称。", reason_code="UNKNOWN_LOCATION")
        now = kwargs.get("now") or datetime.now(timezone.utc)
        options = {"hermes_home": home, "environment": env, "now": now}
        due = wtd.realize_due_travel(**options)
        if due.get("status") not in {"disabled", "none", "in_progress", "arrived"}:
            return _result(False, "unavailable", "无法确认出行状态，未发起新的移动。",
                           reason_code=due.get("reason_code", "TRAVEL_UNAVAILABLE"))
        travel = wtd.observe_travel(**options)
        if travel.get("status") == "in_progress":
            destination = travel["destination_location_id"]
            minutes = _minutes(travel, now)
            return _result(True, "in_progress", f"正在步行前往{_label(destination)}，剩余约{minutes}分钟到达。",
                           destination_location_id=destination, remaining_minutes=minutes)
        state = wls.read_move_state(**options)
        if state is None:
            return _result(False, "unavailable", "世界尚未初始化，未移动。", reason_code="WORLD_NOT_INITIALIZED")
        current = state["world_location_id"]
        if current == target:
            return _result(True, "already_there", f"已经在{_label(target)}了。", location_id=target)
        route = [target]
        if target not in catalog.get(current, {}).get("reachable", []):
            street = "nearby_street"
            if street not in catalog.get(current, {}).get("reachable", []) or target not in catalog.get(street, {}).get("reachable", []):
                return _result(False, "rejected", "地图中没有可用路线。", reason_code="LOCATION_NOT_REACHABLE")
            route = [street, target]
        for step in route:
            moved = wls.apply_move_to(step, **options)
            if moved.get("status") != "applied":
                return _result(False, moved.get("status", "failed"), "世界未确认本段移动成功；请重新观察当前位置。",
                               reason_code=moved.get("reason_code", "MOVE_FAILED"), requested_location_id=target)
            if moved.get("travel"):
                started = moved["travel"]
                minutes = math.ceil(started["route_duration_seconds"] / 60)
                message = f"已出发，正在步行前往{_label(step)}，预计{minutes}分钟到达。"
                if step != target:
                    message += f"这是前往{_label(target)}的中转；到达街道后会继续前往{_label(target)}。"
                    try:
                        (home / "data/world/intended_target.json").write_text(
                            json.dumps({"target": target, "set_at": now.isoformat()}, ensure_ascii=False),
                            encoding="utf-8"
                        )
                    except Exception:
                        pass
                else:
                    try:
                        (home / "data/world/intended_target.json").unlink(missing_ok=True)
                    except Exception:
                        pass
                return _result(True, "started", message, destination_location_id=step,
                               requested_location_id=target, estimated_minutes=minutes, route=route)
        return _result(True, "arrived", f"已到达{_label(target)}。", location_id=target, route=route)
    except Exception as exc:
        # Do not leak filesystem paths, configuration or raw exception messages.
        return _result(False, "failed", "世界移动状态无法确认，未继续移动；请检查世界服务。",
                       reason_code="WORLD_MOVE_ERROR", error_type=type(exc).__name__)
