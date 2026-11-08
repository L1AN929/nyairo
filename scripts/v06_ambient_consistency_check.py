#!/usr/bin/env python3
"""0.6 ambient 背景双读一致性：真实墙钟、零模型、单次正式链检查。

做什么
======
每次 ``check`` 在 ``/tmp/nyairo-v06-ambient-consistency-*`` 下建一个全新根，复制 C 实际
加载的 ``world-package`` 字节（只读源，不导入/不启动旧根程序），再建一个 phase 子根：

1. 启真实 Supply 与真实 World gateway（本根 AF_UNIX），先做一次受控 AF_INET 负控。
2. 同一真实墙钟（非冻结）下取两次读：
   * **正式 World 投影** ``world_living_skeleton.observe_world``（与 Life 背景同一条读口，
     旅行兑现关闭：背景读不得写 World state）；
   * **网关 socket** ``get_snapshot`` / ``get_observation``（位置与动作空间原本的读口）。
3. 走正式 ``resolve_world_provider`` → ``assemble_autonomous_life`` → 正式 cycle
   ``evaluate_once``，实际到达生产 ``build_prompt``；脚本 Decision 明确返回
   ``NO_ACTION``，不构造产品 client、不执行 owner Action。
4. 逐层只核**已有明确事实不互相矛盾**：world_id、place/area、revision、
   observed_at / 有效区间、事件 ID 与公开 summary 精确成员布尔。缺失字段如实标
   ``UNKNOWN``，不补假原子性，不发明毫秒阈值。

不做什么
========
不声称同一原子快照；不调用产品模型、不读模型密钥、不起 HTTP(S)、不访问 C 根除静态包
外的任何路径、不写旧根、不移动角色、不执行动作、不改默认/生产规则/世界包内容、不新增
协议字段/持久格式/一致性策略。父进程不 patch 自己的环境；ambient=1/gate off 只出现在
phase 子进程。

输出
====
只输出固定白名单安全元数据（id/枚举/布尔/计数/字段路径/有效区间/本新根路径），不输出
prompt、completion、reasoning、私文、token、header、env 全文或任意异常正文。未执行断言
不得 PASS；失败非零；正常链未到达正式 ``build_prompt`` 即失败。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "components" / "worldgen"),
    str(SCRIPT_DIR),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: 复用既有的离线守卫、真实 Supply/World AF_UNIX owner 夹具与路径守卫。
import v06_world_roundtrip_check as rt  # noqa: E402

AMBIENT_ENV = "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"
TRAVEL_ENV = "CHIYO_WORLD_TRAVEL_TIME_ENABLED"
GATE_ENV = rt.GATE_ENV
FIXTURE_PREFIX = "nyairo-v06-ambient-consistency-"
SOURCE_PACKAGE = Path("/tmp/nyairo-v06-natural-20261102-c/one-day/world-package")
SUMMARY_NAME = "v06-ambient-consistency-summary.json"
EXPECTED_WORLD_ID = "coastal_town_929711"
EXPECTED_TIMEZONE = "Asia/Tokyo"
EXPECTED_LOCATIONS = 17
EXPECTED_AMBIENT_SCHEMA = "worldgen.ambient.v1"
EXPECTED_TEMPLATES = (
    ("shrine_sweeping", "shrine", 8, 12),
    ("harbor_fish_unload", "fish_market", 5, 10),
    ("store_evening_sale", "convenience_store", 18, 22),
    ("home_window_sound", "home", 0, 24),
    ("street_construction", "street_3", 8, 18),
    ("beach_tide_low", "beach", 6, 11),
)
MAX_WALL_SECONDS = 300.0
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

#: 每条读取值来自哪条正式读口（报告用，不参与行为）。
SOURCE_BASIS = {
    "world_id": "gateway_socket.get_snapshot.world_id + world_living_skeleton.observe_world.world_id",
    "place_area": "gateway_socket.get_snapshot.location + observe_world.current_location",
    "revision": "gateway_socket.get_snapshot.world_revision + observe_world.world_revision",
    "observed_at": "observe_world.local_time (copied into the Life ambient block)",
    "valid_interval": "observe_world.ambient_events.valid_from/valid_until",
    "life_background": "cycle.last_live_context.ambient + production build_prompt",
}


# ---------------------------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False,
                                indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return int(code)


def _real_now() -> datetime:
    """非冻结的真实墙钟（同一时间源，两个读口都用它）。"""

    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _is_scratch_root(path: Any) -> bool:
    """路径必须挂在 ``mkdtemp(prefix="nyairo-v06-ambient-consistency-")`` 的根下。"""

    if not path:
        return False
    try:
        resolved = Path(str(path)).resolve()
        roots = {Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    except OSError:
        return False
    marker = FIXTURE_PREFIX.strip("-")
    anchor = next((candidate for candidate in (resolved,) + tuple(resolved.parents)
                   if marker in candidate.name), None)
    if anchor is None:
        return False
    for root in roots:
        if anchor.parent == root or root in anchor.parent.parents:
            return True
    return False


def _key_list(obj: Any) -> list[str]:
    return sorted(str(key) for key in obj) if isinstance(obj, Mapping) else []


def _error_class(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    head = str(value).split(":", 1)[0].strip()
    if not head:
        return None
    return head[:64] if head.replace("_", "").isalnum() else "ERROR"


def _place_of_node(node: Any) -> Optional[str]:
    text = str(node or "").strip()
    return text.split("/", 1)[0] if text else None


def _area_of_node(node: Any) -> Optional[str]:
    text = str(node or "").strip()
    if "/" not in text:
        return None
    return text.split("/", 1)[1] or None


def _safe_events(events: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        rows.append({key: event.get(key) for key in (
            "event_id", "event_type", "location_id", "local_date",
            "valid_from", "valid_until")})
    return rows


def _event_members(events: Any) -> list[dict[str, str]]:
    """公开 ``(event_id, summary, location_id)`` 三元组，只留在内存里做精确成员判断。"""

    members: list[dict[str, str]] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("event_id") or "").strip()
        payload = event.get("payload")
        summary = str(
            (payload.get("summary") if isinstance(payload, Mapping) else "") or "").strip()
        members.append({"event_id": event_id, "summary": summary,
                        "location_id": str(event.get("location_id") or "")})
    return members


def _window_contains(observed_at: Any, event: Mapping[str, Any]) -> Optional[bool]:
    """该事件的有效区间是否覆盖这次观察时刻；任一侧未知就返回 ``None``（UNKNOWN）。"""

    moment = _parse(observed_at)
    start = _parse(event.get("valid_from"))
    end = _parse(event.get("valid_until"))
    if moment is None or start is None or end is None:
        return None
    return bool(start <= moment < end)


def _membership_paths(obj: Any, needles: Sequence[str], *, path: str = "$",
                      limit: int = 6) -> dict[str, list[str]]:
    wanted = [str(item) for item in needles if str(item)]
    found: dict[str, list[str]] = {item: [] for item in wanted}

    def _add(text: str, where: str) -> None:
        for needle in wanted:
            if needle in text and len(found[needle]) < limit:
                found[needle].append(where)

    def _walk(value: Any, where: str, depth: int) -> None:
        if depth > 12:
            return
        if isinstance(value, str):
            _add(value, where)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                _add(key_text, "%s.%s<key>" % (where, key_text))
                _walk(item, "%s.%s" % (where, key_text), depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value[:64]):
                _walk(item, "%s[%d]" % (where, index), depth + 1)
            return

    _walk(obj, path, 0)
    return found


def _safe_candidates(request: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in list(getattr(request, "candidates", []) or []):
        if not isinstance(candidate, Mapping):
            continue
        rows.append({
            "candidate_id": candidate.get("candidate_id"),
            "candidate_kind": candidate.get("candidate_kind"),
            "source_kind": candidate.get("source_kind"),
            "target_refs": [str(ref)[:96] for ref in
                            (candidate.get("target_refs") or [])][:4],
        })
    return rows


def _ambient_block(obj: Any) -> Optional[dict[str, Any]]:
    """从 world 背景块里取 ``ambient``（只读）；不是 mapping 就当不存在。"""

    if not isinstance(obj, Mapping):
        return None
    block = obj.get("ambient")
    return dict(block) if isinstance(block, Mapping) else None


def _repo_default_lifecycle_stat() -> Optional[tuple[int, int]]:
    path = ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl"
    try:
        info = path.stat()
    except OSError:
        return None
    return (int(info.st_size), int(info.st_mtime_ns))


# ---------------------------------------------------------------------------------------------
# 包校验（父进程只读，不 patch 自己的环境）
# ---------------------------------------------------------------------------------------------


def _package_identity(package: Path) -> dict[str, Any]:
    from components.worldgen import loader

    loaded = loader.load(package, verify=True)
    templates = list(loaded.ambient_templates)
    actual = tuple((str(item.get("event_type")), str(item.get("location_id")),
                    item.get("start_hour"), item.get("end_hour"))
                   for item in templates)
    schema_version = "UNKNOWN"
    try:
        ambient_doc = json.loads((package / "ambient.json").read_text(encoding="utf-8"))
        schema_version = str(ambient_doc.get("schema_version") or "UNKNOWN")
    except (OSError, ValueError):
        schema_version = "UNREADABLE"
    return {
        "world_id": loaded.world_id,
        "timezone": loaded.timezone_name,
        "locations": len(loaded.locations),
        "ambient_schema": schema_version,
        "template_count": len(templates),
        "six_templates_match": actual == EXPECTED_TEMPLATES,
    }


def _copy_source_package(root: Path) -> tuple[Optional[Path], Optional[str]]:
    if not SOURCE_PACKAGE.is_dir():
        return None, "SOURCE_PACKAGE_MISSING"
    target = root / "package"
    shutil.copytree(SOURCE_PACKAGE, target)
    return target, None


# ---------------------------------------------------------------------------------------------
# phase：一个子进程内的正常正式链
# ---------------------------------------------------------------------------------------------


class _CapturingNoActionCognition:
    """接收正式 Decision 请求（脚本替身），明确返回合法 ``NO_ACTION``，不调用模型。"""

    model_id = "stub:v06-ambient-consistency"
    model_version = "v06-ambient-consistency-stub-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[Any] = []

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        import agency_decision_ag1 as ag1

        self.calls += 1
        self.requests.append(request)
        allowed = [str(code) for code in
                   (getattr(request, "allowed_reason_codes", []) or [])]
        raw = {
            "decision": "NO_ACTION",
            "selected_candidate_id": None,
            "reason_codes": allowed[:1],
            "short_rationale": "scripted ambient consistency check, no action",
            "deferred_candidate_ids": [],
        }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:v06-ambient-consistency:%d" % self.calls,
            model_id=self.model_id,
            model_version=self.model_version,
            raw_output=raw,
            latency_ms=1,
        )


class _RefusingWriter:
    """内容写入器替身：本检查不写任何正文，真被调用即失败。"""

    def next_content(self, *_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("content writer must not be used by the consistency check")


def _node_place(node: Any) -> Optional[str]:
    return _place_of_node(node)


def _projection_view(observation: Any) -> dict[str, Any]:
    """正式 World 投影里本检查需要的有限事实（不含正文）。"""

    if not isinstance(observation, Mapping):
        return {"status": None, "world_id": None, "world_revision": None,
                "local_time": None, "local_date": None, "current_location": None,
                "ambient_key_present": False, "ambient_count": 0, "events": [],
                "location_ids": []}
    current = observation.get("current_location")
    current = dict(current) if isinstance(current, Mapping) else None
    events = list(observation.get("ambient_events") or []) \
        if isinstance(observation.get("ambient_events"), list) else []
    return {
        "status": observation.get("status"),
        "world_id": observation.get("world_id"),
        "world_revision": observation.get("world_revision"),
        "local_time": observation.get("local_time"),
        "local_date": observation.get("local_date"),
        "current_location": current,
        "ambient_key_present": isinstance(observation.get("ambient_events"), list),
        "ambient_count": len(events),
        "events": _safe_events(events),
        "location_ids": [str(item.get("location_id")) for item in events
                         if isinstance(item, Mapping)],
    }


def _life_ambient_view(block: Any) -> dict[str, Any]:
    """Life 背景里 ``ambient`` 块的有限事实；事件 summary 只转成布尔。"""

    if not isinstance(block, Mapping):
        return {"present": False, "status": None, "reason_code": None,
                "observed_at": None, "event_count": 0, "events": []}
    raw = list(block.get("events") or []) if isinstance(block.get("events"), list) else []
    events: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        events.append({
            "event_id": item.get("event_id"),
            "event_type": item.get("event_type"),
            "location_id": item.get("location_id"),
            "local_date": item.get("local_date"),
            "valid_from": item.get("valid_from"),
            "valid_until": item.get("valid_until"),
        })
    return {
        "present": True,
        "status": block.get("status"),
        "reason_code": block.get("reason_code"),
        "observed_at": block.get("observed_at"),
        "event_count": len(events),
        "events": events,
    }


def _run_phase(root: Path, ambient: str, clock_base: str, wall_seconds: float,
               guard: "rt._NetworkGuard", package: Path) -> dict[str, Any]:
    import one_day_life as odl

    root = Path(root)
    wall = float(wall_seconds)
    summary: dict[str, Any] = {
        "ok": False,
        "phase": "on" if str(ambient) == "1" else "off",
        "ambient_declared": str(ambient),
        "ambient_child_env": os.environ.get(AMBIENT_ENV),
        "clock_basis": "REAL_WALL_CLOCK_SAME_SOURCE",
        "clock_base": clock_base,
        "root": str(root),
        "pid": os.getpid(),
        "stage": "start",
        "reason_code": None,
        "error_class": None,
        "instance_world_id": None,
        "start_location": None,
        "scene_id": None,
        "world_observe": {},
        "gateway_socket": {},
        "life": {},
        "consistency": {},
        "observe_calls": [],
        "checks": {},
        "network_attempts": 0,
        "network_negative_control_refused": False,
        "product_requests": 0,
        "home_isolated": False,
        "listener_before_close": False,
        "listener_after_close": None,
        "final_position": None,
        "drain": {},
        "services_closed": False,
    }
    gateway: Optional[Any] = None
    supply: Optional[Any] = None
    runtime: Any = None
    world_home: Optional[Path] = None
    cognition = _CapturingNoActionCognition()
    observe_calls: list[dict[str, Any]] = []
    marking: dict[str, Any] = {"direct": False}
    try:
        summary["stage"] = "copy_package"
        package_copy = root / "package"
        if not package_copy.exists():
            shutil.copytree(package, package_copy)
        package = package_copy

        os.environ["REAL_WORKSPACE_PROVISION"] = "ON"
        os.environ["REAL_ARTIFACT_ACTION"] = "ON"
        os.environ[GATE_ENV] = "off"
        os.environ["WORLD_BODY_INTEGRATION_MODE"] = "canonical"
        os.environ["NYAIRO_WORLD_BRIDGE_DIR"] = str(rt.WORLD_BRIDGE_DIR)
        os.environ[AMBIENT_ENV] = str(ambient)
        os.environ["HOME"] = str(root / "home")
        os.environ["NYA02_PROFILE_HOME"] = str(root / "profile")
        os.environ["CHIYO_PROFILE_HOME"] = str(root / "profile")
        for key in list(os.environ):
            if key.startswith("NYA02_MODEL_") or key.startswith("CHIYO_MODEL_"):
                os.environ.pop(key, None)
        for name in ("home", "profile", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        summary["ambient_child_env"] = os.environ.get(AMBIENT_ENV)

        summary["stage"] = "negative_control"
        import socket
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.01)
        except rt.NetworkForbidden:
            summary["network_negative_control_refused"] = True
        guard.attempts = 0

        summary["stage"] = "start_supply"
        supply = rt._SupplyOwner(root / "services")

        summary["stage"] = "prepare_instance"
        instance = root / "instance"
        prepared = odl.prepare(
            instance=instance,
            supply_socket=supply.socket_path,
            governance_socket=supply.socket_path,
            subject="v06:ambient-consistency",
            title="V06 ambient consistency",
            world_package=package,
            create_work_artifact=False,
        )
        if not prepared.get("ok"):
            raise rt.PhaseRefused("INSTANCE_PREPARE_REFUSED")
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        summary["world_home"] = str(world_home)
        summary["instance_world_id"] = str(data["world_id"])
        state = json.loads((world_home / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        summary["start_location"] = str((state.get("location") or {}).get("place_id"))
        summary["scene_id"] = str((state.get("scene") or {}).get("scene_id"))

        summary["stage"] = "align_environment"
        env = odl.instance_environment(instance)
        rt._forbid_model_env(env)
        for key in list(env):
            if key.startswith("NYA02_MODEL_") or key.startswith("CHIYO_MODEL_"):
                env.pop(key, None)
        for key in odl.OWNED_ENV_NAMES:
            if key in env:
                os.environ[key] = str(env[key])
        isolation = rt._world_isolation_env(world_home)
        for key, value in isolation.items():
            os.environ[key] = value
            env[key] = value
        os.environ[GATE_ENV] = "off"
        os.environ["WORLD_BODY_INTEGRATION_MODE"] = "canonical"
        os.environ["NYAIRO_WORLD_BRIDGE_DIR"] = str(rt.WORLD_BRIDGE_DIR)
        os.environ[AMBIENT_ENV] = str(ambient)
        os.environ["HOME"] = str(root / "home")
        env[AMBIENT_ENV] = str(ambient)
        env[GATE_ENV] = "off"
        env["HOME"] = str(root / "home")
        env["NYA02_PROFILE_HOME"] = str(root / "profile")
        env["CHIYO_PROFILE_HOME"] = str(root / "profile")

        summary["stage"] = "start_world"
        gateway = rt._WorldGateway(world_home, world_id=str(data["world_id"]),
                                   environment=env, clock_base=clock_base)
        env.update(gateway.environment())
        env.update(isolation)
        env[GATE_ENV] = "off"
        env[AMBIENT_ENV] = str(ambient)
        env["HOME"] = str(root / "home")
        env["NYA02_PROFILE_HOME"] = str(root / "profile")
        env["CHIYO_PROFILE_HOME"] = str(root / "profile")
        summary["gateway_socket_path"] = str(gateway.socket_path)
        summary["gate"] = os.environ.get(GATE_ENV)
        summary["listener_before_close"] = bool(Path(gateway.socket_path).exists())
        if not summary["listener_before_close"]:
            raise rt.PhaseRefused("WORLD_LISTENER_ABSENT")
        rt._assert_env_paths_under(root, {**env, **os.environ})
        summary["home_isolated"] = str(
            Path(os.path.expanduser("~")).resolve()).startswith(str(root.resolve()))
        if not summary["home_isolated"]:
            raise rt.PhaseRefused("HOME_NOT_ISOLATED")

        # ---- 读口 1：正式 World 投影（与 Life 背景同一条读口，旅行关闭） ----
        summary["stage"] = "world_projection"
        import world_living_skeleton as wls

        _original_observe = wls.observe_world

        def _recording_observe(*args: Any, **kwargs: Any) -> Any:
            read_env = kwargs.get("environment")
            observe_calls.append({
                "direct": bool(marking.get("direct")),
                "travel_env": (dict(read_env).get(TRAVEL_ENV)
                               if isinstance(read_env, Mapping) else None),
                "ambient_env": (dict(read_env).get(AMBIENT_ENV)
                                if isinstance(read_env, Mapping) else None),
                "now_is_datetime": isinstance(kwargs.get("now"), datetime),
            })
            return _original_observe(*args, **kwargs)

        wls.observe_world = _recording_observe
        read_env = dict(env)
        read_env[TRAVEL_ENV] = "0"
        marking["direct"] = True
        readiness = _original_observe(
            hermes_home=world_home, environment=read_env, now=_real_now())
        marking["direct"] = False
        summary["world_observe_readiness"] = (
            readiness.get("status") if isinstance(readiness, Mapping) else None)
        if not isinstance(readiness, Mapping) \
                or str(readiness.get("status")) != "available":
            raise rt.PhaseRefused("WORLD_OBSERVE_UNAVAILABLE")

        # ---- 正式 Life 装配：provider 的 now 也是同一真实墙钟 ----
        summary["stage"] = "build_runtime"
        import autonomous_life_assembly as assembly
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (
            JourneyWorldResolver, RealJourneyWorldActionAdapter)

        state_root = odl.life_state_root(instance)
        port = lap.LifeArtifactPort(str(supply.socket_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"],
            workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        world_seam = assembly.resolve_world_provider(env, clock=_real_now)
        summary["life"]["world_provider_status"] = world_seam.get("status")
        summary["life"]["world_provider_reason"] = world_seam.get("reason")
        world_provider = world_seam.get("provider")
        if world_provider is None:
            raise rt.PhaseRefused("WORLD_PROVIDER_UNAVAILABLE")
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=world_provider,
            writer=_RefusingWriter(), enabled=True, clock=_real_now)
        summary["life"]["assembled"] = assembled.get("state")
        summary["life"]["assembly_reason"] = assembled.get("reason")
        if assembled.get("state") != assembly.READY:
            raise rt.PhaseRefused("ASSEMBLY_NOT_READY")

        # ---- 双读参照：紧贴本轮 Life 读取之前取，尽量缩小两个读口的时间差 ----
        summary["stage"] = "double_read"
        marking["direct"] = True
        direct_projection = _original_observe(
            hermes_home=world_home, environment=read_env, now=_real_now())
        marking["direct"] = False
        summary["world_observe"] = _projection_view(direct_projection)
        if not isinstance(direct_projection, Mapping) \
                or str(direct_projection.get("status")) != "available":
            raise rt.PhaseRefused("WORLD_OBSERVE_UNAVAILABLE")
        from world_action_bridge_adapter import WorldBridgeResolver

        client = WorldBridgeResolver(
            environment=dict(env), bridge_dir=str(rt.WORLD_BRIDGE_DIR)).client
        probe: dict[str, Any] = {}
        for name, call in (("get_snapshot", "get_snapshot"),
                           ("get_observation", "get_observation")):
            try:
                result = getattr(client, call)()
                ok = getattr(result, "outcome", None) == _wbc_ok()
                data_view = result.data if ok else None
            except Exception:  # noqa: BLE001 - 失败的探测按失败报，不猜
                data_view = None
            location = (data_view or {}).get("location") \
                if isinstance(data_view, Mapping) else None
            probe[name] = {
                "ok": data_view is not None,
                "world_id": (data_view or {}).get("world_id")
                            if isinstance(data_view, Mapping) else None,
                "world_revision": (data_view or {}).get("world_revision")
                                  if isinstance(data_view, Mapping) else None,
                "location": dict(location) if isinstance(location, Mapping) else None,
            }
        summary["gateway_socket"] = probe

        summary["stage"] = "evaluate_once"
        cycle = assembled["cycle"]
        gateway.set_now(_real_now().isoformat())
        round_instant = gateway.now_iso()
        summary["round_observed_at"] = round_instant
        out = cycle.evaluate_once(
            trigger_ref="evt:v06:ambient-consistency", observed_at=round_instant, act=True)
        summary["life"]["evaluate_outcome"] = out.get("outcome")

        # ---- 收集 Life 背景与正式 prompt ----
        summary["stage"] = "collect_layers"
        request = cognition.requests[-1] if cognition.requests else None
        situation_context = dict(getattr(request, "situation_context", {}) or {}) \
            if request is not None else {}
        world_block = situation_context.get("world")
        live_context = getattr(cycle, "last_live_context", None)
        seam_snapshot = getattr(cycle, "last_world_observation", None)
        provider = getattr(cycle, "world_opportunity_provider", None)
        candidates = _safe_candidates(request) if request is not None else []
        prompt_payload: Any = None
        prompt_text = ""
        if request is not None:
            from agency_cognition_adapter import ProductionAgencyCognitionAdapter

            build_adapter = ProductionAgencyCognitionAdapter(client=None)
            prompt_text, prompt_payload = build_adapter.build_prompt(request)
        prompt_world: Any = None
        if isinstance(prompt_payload, Mapping):
            prompt_situation = prompt_payload.get("situation_context")
            if isinstance(prompt_situation, Mapping):
                prompt_world = prompt_situation.get("world")

        projection = summary["world_observe"]
        world_block_ambient = _ambient_block(world_block)
        live_ambient = _ambient_block(live_context)
        prompt_ambient = _ambient_block(prompt_world)
        life_blocks = {
            "world_block": _life_ambient_view(world_block_ambient),
            "live_context": _life_ambient_view(live_ambient),
            "prompt": _life_ambient_view(prompt_ambient),
        }
        projection_members = _event_members(direct_projection.get("ambient_events"))
        projection_ids = [member["event_id"] for member in projection_members]
        prompt_text_ids = {member["event_id"]: member["event_id"] in prompt_text
                           for member in projection_members}
        prompt_text_summaries = {member["event_id"]: (member["summary"] in prompt_text)
                                 for member in projection_members}
        summary["life"].update({
            "request_captured": request is not None,
            "request_calls": cognition.calls,
            "candidate_ids": [row.get("candidate_id") for row in candidates],
            "situation_context_keys": _key_list(situation_context),
            "world_block_keys": _key_list(world_block),
            "world_block_status": (world_block or {}).get("status")
                                  if isinstance(world_block, Mapping) else None,
            "live_context_keys": _key_list(live_context),
            "seam_snapshot_keys": _key_list(seam_snapshot),
            "prompt_keys": _key_list(prompt_payload),
            "prompt_text_chars": len(prompt_text),
            "provider_last_reason_class": _error_class(
                getattr(provider, "last_reason", None)),
            "provider_observation_keys": _key_list(
                getattr(provider, "last_observation", None)),
            "provider_last_observed_at": getattr(provider, "last_observed_at", None),
            "ambient_blocks": {
                name: {
                    "present": view["present"],
                    "status": view["status"],
                    "event_count": view["event_count"],
                } for name, view in life_blocks.items()
            },
            "evidences": {
                "provider_call_seen": any(not row["direct"] for row in observe_calls),
                "provider_call_travel_off": all(
                    row["travel_env"] in ("0", 0)
                    for row in observe_calls if not row["direct"]),
                "provider_call_real_clock": all(
                    row["now_is_datetime"]
                    for row in observe_calls if not row["direct"]),
            },
        })
        summary["observe_calls"] = list(observe_calls)
        summary["life"]["prompt_text_membership"] = {
            "ids_present": prompt_text_ids,
            "summaries_present": prompt_text_summaries,
        }

        # ---- 一致性事实（只核已有明确事实不互相矛盾） ----
        snapshot = probe.get("get_snapshot") or {}
        snapshot_location = snapshot.get("location") or {}
        current = projection.get("current_location") or {}
        proj_node = current.get("location_id")
        proj_place = _node_place(proj_node)
        # A place node carries the place's default area, not the state's current area;
        # only an explicit area node is honestly comparable with the gateway snapshot.
        proj_area = _area_of_node(proj_node)
        snap_place = snapshot_location.get("place_id")
        snap_area = snapshot_location.get("area_id")
        world_ids = {
            "instance": summary.get("instance_world_id"),
            "world_projection": projection.get("world_id"),
            "gateway_snapshot": snapshot.get("world_id"),
        }
        revisions = {
            "world_projection": projection.get("world_revision"),
            "gateway_snapshot": snapshot.get("world_revision"),
        }
        known_ids = {key: str(value) for key, value in world_ids.items() if value}
        known_revs = {key: str(value) for key, value in revisions.items()
                      if value not in (None, "")}
        projection_window = {
            "observed_at": projection.get("local_time"),
            "events": [{
                "event_id": event.get("event_id"),
                "contains_observed_at": _window_contains(
                    projection.get("local_time"), event),
            } for event in (projection.get("events") or [])],
        }
        life_window = {}
        for name, view in life_blocks.items():
            life_window[name] = {
                "observed_at": view.get("observed_at"),
                "events": [{
                    "event_id": event.get("event_id"),
                    "valid_from": event.get("valid_from"),
                    "valid_until": event.get("valid_until"),
                    "contains_observed_at": _window_contains(
                        view.get("observed_at"), event),
                } for event in view.get("events") or []],
            }
        life_members = {
            name: [{"event_id": event.get("event_id"),
                    "location_id": event.get("location_id")}
                   for event in (view.get("events") or [])]
            for name, view in life_blocks.items()
        }
        # 事件摘要只在内存里做精确成员判断：拿到块里的原文，不落盘。
        raw_blocks = {
            "world_block": world_block_ambient,
            "live_context": live_ambient,
            "prompt": prompt_ambient,
        }
        summary_membership = {
            name: _summary_membership(block, projection_members)
            for name, block in raw_blocks.items()
        }
        summary["consistency"] = {
            "source_basis": SOURCE_BASIS,
            "world_ids": world_ids,
            "world_id_agrees": len(set(known_ids.values())) <= 1 if known_ids else None,
            "revisions": revisions,
            "revision_agrees": (len(set(known_revs.values())) <= 1
                                if known_revs else None),
            "place": {
                "projection": proj_place,
                "gateway_snapshot": snap_place,
                "agrees": (proj_place == snap_place
                           if proj_place and snap_place else None),
            },
            "area": {
                "projection": proj_area,
                "gateway_snapshot": snap_area,
                "agrees": (proj_area == snap_area
                           if proj_area and snap_area else None),
            },
            "projection_window": projection_window,
            "life_window": life_window,
            "event_ids": {
                "world_projection": projection_ids,
                "world_block": [row["event_id"] for row in life_members["world_block"]],
                "live_context": [row["event_id"] for row in life_members["live_context"]],
                "prompt": [row["event_id"] for row in life_members["prompt"]],
            },
            "life_matches_world": {
                name: ([row["event_id"] for row in members] == projection_ids
                       and [row["location_id"] for row in members]
                       == [member["location_id"] for member in projection_members])
                for name, members in life_members.items()
            },
            "summary_membership": summary_membership,
        }
        summary["stage"] = "done"
    except rt.PhaseRefused as exc:
        summary["reason_code"] = exc.reason_code
    except rt.NetworkForbidden:
        summary["reason_code"] = "NETWORK_FORBIDDEN"
    except BaseException as exc:  # noqa: BLE001 - 固定分类，不输出任意正文
        summary["reason_code"] = rt._safe_reason(exc)
        summary["error_class"] = type(exc).__name__
    finally:
        summary["network_attempts"] = int(getattr(guard, "attempts", 0))
        summary["ambient_child_env"] = os.environ.get(AMBIENT_ENV)
        if world_home is not None:
            try:
                state = json.loads(
                    (world_home / "data" / "world" / "world_state.json")
                    .read_text(encoding="utf-8"))
                summary["final_position"] = str(
                    (state.get("location") or {}).get("place_id"))
            except Exception:  # noqa: BLE001
                summary["final_position"] = None
        if runtime is not None:
            try:
                runtime.close()
                summary["drain"]["runtime_closed"] = True
            except Exception:  # noqa: BLE001
                summary["drain"]["runtime_closed"] = False
        if gateway is not None:
            summary["drain"]["world"] = gateway.stop()
            summary["listener_after_close"] = bool(Path(gateway.socket_path).exists())
        if supply is not None:
            try:
                summary["drain"].update(supply.stop())
                summary["services_closed"] = True
            except Exception:  # noqa: BLE001
                summary["drain"]["supply_shutdown"] = None
    return summary


def _summary_membership(block: Any, projection_members: Sequence[Mapping[str, str]]
                        ) -> dict[str, bool]:
    """Life 块里每个公开事件摘要是否与 World 投影逐字一致（只转布尔）。"""

    by_id: dict[str, str] = {}
    if isinstance(block, Mapping):
        for item in block.get("events") or []:
            if not isinstance(item, Mapping):
                continue
            event_id = str(item.get("event_id") or "").strip()
            if event_id:
                by_id[event_id] = str(item.get("summary") or "").strip()
    return {str(member["event_id"]): (by_id.get(str(member["event_id"]))
                                      == str(member["summary"]))
            for member in projection_members}


def _wbc_ok() -> Any:
    import world_body_client as wbc

    return wbc.OK


# ---------------------------------------------------------------------------------------------
# phase 子进程入口
# ---------------------------------------------------------------------------------------------


def cmd_phase(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_ambient_consistency_check phase")
    parser.add_argument("--root", required=True)
    parser.add_argument("--ambient", required=True, choices=("0", "1"))
    parser.add_argument("--clock-base", required=True)
    parser.add_argument("--wall-seconds", type=float, default=300.0)
    parser.add_argument("--package", required=True)
    args = parser.parse_args(list(argv))
    root = Path(args.root)
    if not _is_scratch_root(root):
        return _emit({"ok": False, "reason_code": "SCRATCH_ROOT_REQUIRED",
                      "root": str(root)}, code=EXIT_REFUSED)
    package = Path(args.package)
    if not _is_scratch_root(package):
        return _emit({"ok": False, "reason_code": "PACKAGE_NOT_IN_SCRATCH",
                      "root": str(root)}, code=EXIT_REFUSED)
    root.mkdir(parents=True, exist_ok=True)
    guard = rt._NetworkGuard()
    with guard:
        summary = _run_phase(root, args.ambient, args.clock_base,
                             args.wall_seconds, guard, package)
    summary["network_attempts"] = int(guard.attempts)
    summary["product_requests"] = 0
    summary["checks"] = _phase_checks(summary)
    summary["ok"] = bool(summary.get("reason_code") is None
                         and summary["checks"]
                         and all(bool(value) for value in summary["checks"].values()))
    (root / SUMMARY_NAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
    return EXIT_OK if summary["ok"] else EXIT_FAILED


# ---------------------------------------------------------------------------------------------
# phase 判据（纯函数）
# ---------------------------------------------------------------------------------------------


def _windows_ok(window: Mapping[str, Any], *, require_known: bool) -> bool:
    events = list(window.get("events") or [])
    if not events:
        return True
    result: list[bool] = []
    for event in events:
        value = event.get("contains_observed_at")
        if value is None:
            result.append(not require_known)
        else:
            result.append(bool(value))
    return all(result)


def _life_background_complete(summary: Mapping[str, Any]) -> bool:
    """Life 三层背景与 World 投影的 ID 集合一致；空事件也算一致。"""

    consistency = dict(summary.get("consistency") or {})
    matches = dict(consistency.get("life_matches_world") or {})
    if not matches:
        return False
    return all(bool(value) for value in matches.values())


def _summary_membership_ok(summary: Mapping[str, Any]) -> bool:
    consistency = dict(summary.get("consistency") or {})
    rows = dict(consistency.get("summary_membership") or {})
    if not rows:
        return True
    return all(all(bool(value) for value in row.values()) for row in rows.values())


def _phase_checks(summary: Mapping[str, Any]) -> dict[str, bool]:
    observe = dict(summary.get("world_observe") or {})
    life = dict(summary.get("life") or {})
    consistency = dict(summary.get("consistency") or {})
    evidences = dict(life.get("evidences") or {})
    drain = dict(summary.get("drain") or {})
    declared = str(summary.get("ambient_declared"))
    projection_window = dict(consistency.get("projection_window") or {})
    world_ids = dict(consistency.get("world_ids") or {})
    known_ids = [value for value in world_ids.values() if value]
    place = dict(consistency.get("place") or {})
    area = dict(consistency.get("area") or {})
    blocks = dict(life.get("ambient_blocks") or {})
    prompt_membership = dict(life.get("prompt_text_membership") or {})
    checks = {
        "ambient_declared_is_0_or_1": declared in ("0", "1"),
        "ambient_child_env_matches_declared": (
            str(summary.get("ambient_child_env")) == declared),
        "gate_off": str(summary.get("gate")) == "off",
        "negative_control_refused": bool(
            summary.get("network_negative_control_refused")),
        "no_network": int(summary.get("network_attempts") or 0) == 0,
        "zero_product_requests": int(summary.get("product_requests") or 0) == 0,
        "world_observe_available": str(observe.get("status")) == "available",
        "gateway_snapshot_ok": bool(
            (summary.get("gateway_socket") or {}).get("get_snapshot", {}).get("ok")),
        "gateway_observation_ok": bool(
            (summary.get("gateway_socket") or {}).get("get_observation", {}).get("ok")),
        "life_assembled_ready": str(life.get("assembled")) == "READY",
        "life_request_captured": bool(life.get("request_captured")),
        "formal_prompt_built": bool(life.get("prompt_keys")),
        "home_isolated": bool(summary.get("home_isolated")),
        "listener_closed": summary.get("listener_after_close") is False,
        "runtime_closed": bool(drain.get("runtime_closed")),
        "world_closed": bool((drain.get("world") or {}).get("lease_released")),
        "supply_closed": bool(drain.get("supply_shutdown")),
        "no_movement": (
            bool(summary.get("start_location"))
            and str(summary.get("final_position")) == str(summary.get("start_location"))),
        "world_id_agrees": (consistency.get("world_id_agrees") is True
                            if known_ids else False),
        "revision_agrees": (consistency.get("revision_agrees") is True
                            if (consistency.get("revisions") or {}) else False),
        "place_agrees": place.get("agrees") is True,
        "area_agrees": (area.get("agrees") is True
                        if area.get("projection") and area.get("gateway_snapshot")
                        else True),
        "projection_events_current": _windows_ok(projection_window,
                                                 require_known=True),
        # The Life ambient block carries the event windows but not its own observation
        # instant (the World projection does): an unknown side is UNKNOWN, not a
        # contradiction, and is reported as such in ``life_window``.
        "life_events_current": all(
            _windows_ok(window, require_known=False)
            for window in (consistency.get("life_window") or {}).values()),
        "life_event_windows_reported": all(
            all(event.get("valid_from") and event.get("valid_until")
                for event in (window.get("events") or []))
            for window in (consistency.get("life_window") or {}).values()),
        "life_background_matches_world": _life_background_complete(summary),
        "life_summary_exact": _summary_membership_ok(summary),
        "provider_read_uses_same_clock": bool(evidences.get("provider_call_seen")
                                              and evidences.get("provider_call_real_clock")),
        "provider_read_travel_off": bool(evidences.get("provider_call_travel_off")),
    }
    projection_ids = list((consistency.get("event_ids") or {})
                          .get("world_projection") or [])
    if declared == "1":
        checks["life_background_present"] = bool(
            blocks.get("world_block", {}).get("present")
            and blocks.get("live_context", {}).get("present")
            and blocks.get("prompt", {}).get("present"))
        checks["prompt_text_carries_events"] = all(
            bool(prompt_membership.get("ids_present", {}).get(event_id))
            and bool(prompt_membership.get("summaries_present", {}).get(event_id))
            for event_id in projection_ids)
        checks["ambient_is_not_a_candidate"] = not any(
            event_id in str(candidate)
            for event_id in projection_ids for candidate in
            (life.get("candidate_ids") or []))
    else:
        checks["off_has_no_ambient_background"] = not any(
            blocks.get(name, {}).get("present")
            for name in ("world_block", "live_context", "prompt"))
    return checks


# ---------------------------------------------------------------------------------------------
# check：父进程只编排、不 patch 自己的环境
# ---------------------------------------------------------------------------------------------


def _child_environment(root: Path, ambient: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in rt.CHILD_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value:
            env[key] = value
    for name in ("home", "tmp", "profile"):
        (root / name).mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        GATE_ENV: "off",
        AMBIENT_ENV: str(ambient),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        "NYAIRO_WORLD_BRIDGE_DIR": str(rt.WORLD_BRIDGE_DIR),
    })
    return env


def _run_child(phase_root: Path, ambient: str, clock_base: str,
               remaining: float, package: Path) -> dict[str, Any]:
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "phase",
               "--root", str(phase_root), "--ambient", str(ambient),
               "--clock-base", clock_base,
               "--wall-seconds", "%.1f" % remaining,
               "--package", str(package)]
    try:
        completed = subprocess.run(
            command, env=_child_environment(phase_root, ambient),
            capture_output=True, text=True, timeout=max(5.0, remaining))
        exit_code: Optional[int] = int(completed.returncode)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": None,
                "summary": {"ok": False, "reason_code": "PHASE_TIMEOUT",
                            "phase": "on" if ambient == "1" else "off",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    except OSError as exc:
        return {"ok": False, "exit": None,
                "summary": {"ok": False, "reason_code": "PHASE_SPAWN_FAILED",
                            "error_class": type(exc).__name__,
                            "phase": "on" if ambient == "1" else "off",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    path = phase_root / SUMMARY_NAME
    if not path.exists():
        return {"ok": False, "exit": exit_code,
                "summary": {"ok": False, "reason_code": "PHASE_SUMMARY_MISSING",
                            "phase": "on" if ambient == "1" else "off",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    summary = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": bool(summary.get("ok")) and exit_code == EXIT_OK,
            "exit": exit_code, "summary": summary}


def _run_unit_tests() -> dict[str, Any]:
    """真实收集本单新增的 4–6 个正负控；0 例或纯 return True 不算绿。"""

    import unittest

    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(ROOT / "tests"), pattern="test_v06_ambient_consistency.py")
    except Exception as exc:  # noqa: BLE001 - 收集失败按失败报
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc),
                "run": 0}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    count = int(result.testsRun)
    return {
        "ok": bool(result.wasSuccessful()),
        "run": count,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(getattr(result, "skipped", []) or []),
    }


def cmd_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_ambient_consistency_check check")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--max-wall-seconds", type=float, default=MAX_WALL_SECONDS)
    args = parser.parse_args(list(argv))
    if not args.offline:
        return _emit({"ok": False, "reason_code": "OFFLINE_FLAG_REQUIRED"},
                     code=EXIT_REFUSED)
    if not (0 < float(args.max_wall_seconds) <= MAX_WALL_SECONDS):
        return _emit({"ok": False, "reason_code": "MAX_WALL_SECONDS_OUT_OF_RANGE",
                      "max_wall_seconds": MAX_WALL_SECONDS}, code=EXIT_REFUSED)
    guard = rt._NetworkGuard()
    with guard:
        return _check_body(args, guard)


def _check_body(args: argparse.Namespace, guard: "rt._NetworkGuard") -> int:
    started = time.monotonic()

    def _finish(payload: dict[str, Any], code: int) -> int:
        payload.setdefault("parent", {})["network_attempts"] = int(guard.attempts)
        payload.setdefault("checks", {})["parent_no_network"] = int(guard.attempts) == 0
        if "phase_ran" in payload.get("checks", {}):
            payload["ok"] = bool(payload["checks"]) and all(
                bool(value) for value in payload["checks"].values())
            payload["result"] = "PASS" if payload["ok"] else "FAIL"
        payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return _emit(payload, code=code)

    budget = float(args.max_wall_seconds)
    clock_base = _real_now().isoformat()
    parent_ambient_before = os.environ.get(AMBIENT_ENV)
    repo_before = _repo_default_lifecycle_stat()
    root = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX))
    payload: dict[str, Any] = {
        "ok": False,
        "result": "FAIL",
        "root": str(root),
        "clock_basis": "REAL_WALL_CLOCK_SAME_SOURCE",
        "clock_base_utc": clock_base,
        "source_package": str(SOURCE_PACKAGE),
        "package_identity": {},
        "phase": {},
        "checks": {},
        "parent": {"ambient_env_before": parent_ambient_before,
                   "ambient_env_after": parent_ambient_before,
                   "product_requests": 0},
    }
    package, package_error = _copy_source_package(root)
    if package_error or package is None:
        payload["result"] = "STOP"
        payload["checks"]["source_package_found"] = False
        payload["reason_code"] = package_error or "SOURCE_PACKAGE_MISSING"
        return _finish(payload, code=EXIT_REFUSED)
    try:
        identity = _package_identity(package)
    except Exception as exc:  # noqa: BLE001 - 包不可读即 STOP，不换包
        payload["result"] = "STOP"
        payload["checks"]["source_package_found"] = True
        payload["checks"]["package_identity_ok"] = False
        payload["reason_code"] = "PACKAGE_LOAD_FAILED"
        payload["error_class"] = type(exc).__name__
        return _finish(payload, code=EXIT_REFUSED)
    payload["package_identity"] = identity
    identity_ok = bool(
        identity.get("world_id") == EXPECTED_WORLD_ID
        and identity.get("timezone") == EXPECTED_TIMEZONE
        and int(identity.get("locations") or 0) == EXPECTED_LOCATIONS
        and identity.get("ambient_schema") == EXPECTED_AMBIENT_SCHEMA
        and int(identity.get("template_count") or 0) == len(EXPECTED_TEMPLATES)
        and identity.get("six_templates_match") is True)
    payload["checks"]["source_package_found"] = True
    payload["checks"]["package_identity_ok"] = identity_ok
    if not identity_ok:
        payload["result"] = "STOP"
        payload["reason_code"] = "PACKAGE_IDENTITY_MISMATCH"
        return _finish(payload, code=EXIT_REFUSED)

    # 新 4–6 例正负控：真实收集并运行，0 例不算绿。
    tests = _run_unit_tests()
    payload["tests"] = tests
    payload["checks"]["unit_tests_ok"] = bool(tests.get("ok"))
    payload["checks"]["unit_tests_count_4_to_6"] = 4 <= int(tests.get("run") or 0) <= 6

    remaining = budget - (time.monotonic() - started) - 15.0
    if remaining <= 10.0:
        payload["checks"]["wall_budget"] = False
        payload["result"] = "STOP"
        payload["reason_code"] = "WALL_BUDGET_EXHAUSTED"
        return _finish(payload, code=EXIT_REFUSED)
    phase_root = root / ("phase-%d" % os.getpid())
    phase_root.mkdir(parents=True, exist_ok=False)
    result = _run_child(phase_root, "1", clock_base, remaining, package)
    payload["phase"] = result.get("summary") or {}
    payload["checks"]["phase_ran"] = bool(result.get("ok"))
    payload["checks"]["phase_exit_zero"] = result.get("exit") == EXIT_OK
    if not result.get("ok"):
        payload["result"] = "FAIL"
        return _finish(payload, code=EXIT_FAILED)
    payload["checks"].update(dict(payload["phase"].get("checks") or {}))
    repo_after = _repo_default_lifecycle_stat()
    payload["checks"]["repo_default_lifecycle_untouched"] = repo_before == repo_after
    payload["parent"]["ambient_env_after"] = os.environ.get(AMBIENT_ENV)
    payload["checks"]["parent_env_not_patched"] = (
        os.environ.get(AMBIENT_ENV) == parent_ambient_before)
    payload["wall_limit_seconds"] = budget
    return _finish(payload, code=EXIT_OK)


COMMANDS = {"check": cmd_check, "phase": cmd_phase}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
