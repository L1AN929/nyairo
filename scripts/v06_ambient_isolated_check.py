#!/usr/bin/env python3
"""0.6 新隔离 ambient：零模型 off/on 分层验证。

做什么
======
每次 ``check`` 在 ``/tmp/nyairo-v06-ambient-offline-*`` 下建一个全新根，复制 C 实际
加载的 ``world-package`` 字节（只读源，不导入/不启动旧根程序），再各建一个独立子根：

1. ``off``：同一份世界包、同一个 home/bedroom 初始状态、同一个冻结业务时刻，
   ``CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=0``。走正式 World 观察
   （``world_living_skeleton.observe_world``，与 World 服务同一条读口）与正式 Life
   装配（provider → AG-0 → 正式 ``build_prompt``），脚本 Decision 明确返回
   ``NO_ACTION``。
2. ``on``：另一子根，除 ambient=1 外全部相同；同样走正式 World 观察与正式 Life
   装配，脚本 Decision 返回 ``NO_ACTION``。

分层取证
========
* World 观察：实际 ``ambient_events``（event_id/type/地点/时间窗/count），以及当前
  日期轮转、当前时段、地点过滤分别作用在哪一层；
* 网关 socket 观察：``get_observation``/``get_snapshot`` 是否带 ambient 键；
* Life 背景材料：``daily_life_context``/``situation_context.world``；
* AG-0 候选与正式 ``build_prompt`` 结构。

不做什么
========
不调用任何产品模型、不读模型密钥/endpoint、不起 HTTP(S)、不访问 C 根除静态包外的
任何路径、不写旧根、不移动角色、不执行动作、不改默认/生产规则/世界包内容。Decision
是脚本替身；位置、世界状态、候选与材料都来自正式读口与正式装配。父进程不 patch 自己
的环境；ambient=0/1 只出现在 phase 子进程及其自有 World 子环境里。

输出
====
只输出固定白名单安全元数据（id/枚举/布尔/计数/字段路径/本新根路径），不输出 prompt、
completion、reasoning、私文、token、header、env 全文或任意异常正文。未执行断言不得
PASS；验证装配失败非零；生产既有的「World 有、Life 无」断层如实报 ``OBSERVED_GAP``，
不把机制说通。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

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

#: 复用既有的离线守卫、真实 Supply/World 夹具与路径守卫（不复制、不改写）。
import v06_world_roundtrip_check as rt  # noqa: E402

AMBIENT_ENV = "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"
GATE_ENV = rt.GATE_ENV
FIXTURE_PREFIX = "nyairo-v06-ambient-offline-"
SOURCE_PACKAGE = Path("/tmp/nyairo-v06-natural-20261102-c/one-day/world-package")
SUMMARY_NAME = "v06-ambient-phase-summary.json"
WORLD_TZ = ZoneInfo("Asia/Tokyo")
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
MAX_WALL_SECONDS = 600.0
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

#: 分层顺序：World 正式观察 → 网关 socket → Life 装配读口 → 背景材料 → AG-0 → prompt。
LAYER_ORDER = (
    "world_observation",
    "gateway_socket",
    "life_world_seam_snapshot",
    "life_situation_world",
    "life_live_context",
    "ag0_candidates",
    "formal_prompt",
)
LAYER_SEAM = {
    "world_observation": "world_living_skeleton.observe_world (official World projection)",
    "gateway_socket": (
        "world_body_gateway_api.get_snapshot/get_observation do not project ambient_events "
        "(a different read, not the background carrier)"),
    "life_world_seam_snapshot": (
        "WorldMoveOpportunityProvider.observe reads the gateway snapshot; ambient background "
        "is read separately from the official observe_world projection"),
    "life_situation_world": (
        "world_move_opportunity_source.WorldMoveOpportunityProvider.daily_life_context "
        "adds a bounded ambient background only when the feature is enabled"),
    "life_live_context": (
        "life_live_context_view passes the bounded ambient background unchanged"),
    "ag0_candidates": (
        "candidate_sources_ag0.WorldOpportunityCandidateSource is unchanged; ambient is "
        "background, never a candidate"),
    "formal_prompt": (
        "agency_cognition_adapter.ProductionAgencyCognitionAdapter carries the bounded world "
        "background into the real prompt"),
}
#: Layers that must carry the background when the feature is enabled.  The gateway socket and
#: the gateway-snapshot seam are *different reads* on purpose and are not carriers.
AMBIENT_CARRIER_LAYERS = ("life_situation_world", "life_live_context", "formal_prompt")


# ---------------------------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False,
                                indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return int(code)


def _parse(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _is_scratch_root(path: Any) -> bool:
    """路径必须挂在 ``mkdtemp(prefix="nyairo-v06-ambient-offline-")`` 的根下。"""

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


def _membership_paths(obj: Any, needles: Sequence[str], *, path: str = "$",
                      limit: int = 6) -> dict[str, list[str]]:
    """精确已知字符串在结构中的内存成员判断；只返回字段路径，不返回正文。"""

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


def _present_ids(obj: Any, needles: Sequence[str]) -> list[str]:
    if obj is None:
        return []
    found = _membership_paths(obj, needles)
    return [needle for needle in needles if found.get(needle)]


def _safe_candidates(request: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in list(getattr(request, "candidates", []) or []):
        if not isinstance(candidate, Mapping):
            continue
        rows.append({
            "candidate_id": candidate.get("candidate_id"),
            "candidate_kind": candidate.get("candidate_kind"),
            "source_kind": candidate.get("source_kind"),
            "source_ref": candidate.get("source_ref"),
            "target_refs": [str(ref)[:96] for ref in
                            (candidate.get("target_refs") or [])][:4],
            "legal_decisions": [str(item) for item in
                                (candidate.get("legal_decisions") or [])][:8],
            "executable_now": candidate.get("executability_now",
                                           candidate.get("executable_now")),
        })
    return rows


def _safe_events(events: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        rows.append({key: event.get(key) for key in (
            "event_id", "event_type", "location_id", "local_date",
            "valid_from", "valid_until", "provenance")})
    return rows


def _ambient_key_present(obj: Any) -> bool:
    """Is this a world block that carries an ``ambient`` background key?"""

    return isinstance(obj, Mapping) and isinstance(obj.get("ambient"), Mapping)


def _ambient_members(events: Any) -> list[dict[str, str]]:
    """The public ``(event_id, summary)`` pairs to look for, held only in memory.

    The summaries are public World template text; they are used for exact membership
    checks and are **never** written into the phase summary or any log.
    """

    members: list[dict[str, str]] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("event_id") or "").strip()
        payload = event.get("payload")
        summary = str(
            (payload.get("summary") if isinstance(payload, Mapping) else "") or "").strip()
        if event_id and summary:
            members.append({"event_id": event_id, "summary": summary})
    return members


def _ambient_membership_ok(layers: Mapping[str, Any],
                           prompt_text_ids: Mapping[str, bool],
                           prompt_text_summaries: Mapping[str, bool]) -> bool:
    """Every current World event id **and** its public summary reaches every carrier layer.

    No events today (``CURRENT_HOME_NO_EVENT``) is a valid state and is trivially satisfied;
    it is never satisfied by substituting a stale instant.
    """

    event_ids = list(layers.get("event_ids_from_world") or [])
    if not event_ids:
        return True
    rows = layers.get("layers") or {}
    for name in AMBIENT_CARRIER_LAYERS:
        row = rows.get(name) or {}
        paths = row.get("paths") or {}
        summary_present = row.get("summary_present") or {}
        if not all(paths.get(event_id) for event_id in event_ids):
            return False
        if not all(bool(summary_present.get(event_id)) for event_id in event_ids):
            return False
    return (all(bool(prompt_text_ids.get(event_id)) for event_id in event_ids)
            and all(bool(prompt_text_summaries.get(event_id)) for event_id in event_ids))


def _key_list(obj: Any) -> list[str]:
    return sorted(str(key) for key in obj) if isinstance(obj, Mapping) else []


def _error_class(value: Any) -> Optional[str]:
    """只保留异常/原因的类名，不把任意正文带出去。"""

    if value is None or value == "":
        return None
    text = str(value)
    head = text.split(":", 1)[0].strip()
    if not head:
        return None
    return head[:64] if head.replace("_", "").isalnum() else "ERROR"


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
    rows = [{
        "event_type": str(item.get("event_type")),
        "location_id": str(item.get("location_id")),
        "start_hour": item.get("start_hour"),
        "end_hour": item.get("end_hour"),
        "fixed_date": bool(item.get("kind") == "finite" or item.get("finite")),
    } for item in templates]
    actual = tuple((row["event_type"], row["location_id"],
                    row["start_hour"], row["end_hour"]) for row in rows)
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
        "template_count": len(rows),
        "templates": rows,
        "six_templates_match": actual == EXPECTED_TEMPLATES,
    }


def _copy_source_package(root: Path) -> tuple[Optional[Path], Optional[str]]:
    if not SOURCE_PACKAGE.is_dir():
        return None, "SOURCE_PACKAGE_MISSING"
    target = root / "package"
    shutil.copytree(SOURCE_PACKAGE, target)
    return target, None


# ---------------------------------------------------------------------------------------------
# phase：一个子进程内的 off/on 分层场景
# ---------------------------------------------------------------------------------------------


class _CapturingNoActionCognition:
    """接收正式 Decision 请求（脚本替身），明确返回合法 NO_ACTION，不调用模型。"""

    model_id = "stub:v06-ambient"
    model_version = "v06-ambient-stub-v1"

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
            "short_rationale": "scripted ambient check, no action",
            "deferred_candidate_ids": [],
        }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:v06-ambient:%d" % self.calls,
            model_id=self.model_id,
            model_version=self.model_version,
            raw_output=raw,
            latency_ms=1,
        )


class _RefusingWriter:
    """内容写入器替身：本检查不写任何正文，真被调用即失败。"""

    def next_content(self, *_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("content writer must not be used by the ambient check")


def _template_analysis(env: Mapping[str, str], world_home: Path,
                       local_date: date, local_now: datetime) -> dict[str, Any]:
    import world_ambient_events as wae
    import world_foundation as wf
    import world_runtime_data as wrd

    world = wrd.active_world(dict(env))
    catalog = wae.ambient_template_catalog(dict(env))
    rows = [{
        "event_type": row["event_type"],
        "location_id": row["location_id"],
        "start_hour": row["start_hour"],
        "end_hour": row["end_hour"],
        "fixed_date": bool(row["finite"]),
    } for row in catalog["templates"]]

    state = wf.WorldStateStore(
        world_home / "data" / "world" / "world_state.json").load() or {}
    location = state.get("location") or {}
    current_node = world.state_node(str(location.get("place_id") or ""),
                                    location.get("area_id"))
    relevant = set()
    if current_node:
        relevant = {world.node_place(node)
                    for node in {current_node, *world.reachable_of(current_node)}}

    selected = wae.materialize_ambient_events(
        local_date, timezone_name=world.timezone_name, environment=dict(env))
    selected_rows: list[dict[str, Any]] = []
    for event in selected:
        valid_from = datetime.fromisoformat(event["valid_from"])
        valid_until = datetime.fromisoformat(event["valid_until"])
        in_window = valid_from <= local_now < valid_until
        relevant_here = str(event["location_id"]) in relevant
        selected_rows.append({
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "location_id": event.get("location_id"),
            "local_date": event.get("local_date"),
            "start_hour": next((row["start_hour"] for row in rows
                                if row["event_type"] == event.get("event_type")), None),
            "end_hour": next((row["end_hour"] for row in rows
                              if row["event_type"] == event.get("event_type")), None),
            "in_current_window": bool(in_window),
            "location_relevant": bool(relevant_here),
            "survives_filters": bool(in_window and relevant_here),
        })

    daily = [row for row in rows if not row["fixed_date"]]
    rotation = {
        "daily_template_count": len(daily),
        "start_index": (local_date.toordinal() % len(daily)) if daily else None,
    }
    return {
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "catalog_total": catalog["total"],
        "cross_day": catalog["categories"][wae.CATEGORY_CROSS_DAY],
        "max_events_per_day": catalog["max_events_per_day"],
        "templates": rows,
        "rotation": rotation,
        "current_location_node": current_node,
        "relevant_place_ids": sorted(relevant),
        "selected_for_date": selected_rows,
    }


def _aux_date_query(env: Mapping[str, str], day: date) -> dict[str, Any]:
    import world_ambient_events as wae
    import world_runtime_data as wrd

    world = wrd.active_world(dict(env))
    events = wae.materialize_ambient_events(
        day, timezone_name=world.timezone_name, environment=dict(env))
    daily_count = len([item for item in world.ambient_templates
                       if not item.get("finite")]) or 1
    return {
        "local_date": day.isoformat(),
        "rotation_start_index": (day.toordinal() % daily_count),
        "pure_function_query": True,
        "events": [{
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "location_id": event.get("location_id"),
            "local_date": event.get("local_date"),
        } for event in events],
    }


def _run_phase(root: Path, ambient: str, clock_base: str, wall_seconds: float,
               guard: "rt._NetworkGuard", package: Path) -> dict[str, Any]:
    import one_day_life as odl

    root = Path(root)
    frozen = _parse(clock_base)
    local_now = frozen.astimezone(WORLD_TZ)
    summary: dict[str, Any] = {
        "ok": False,
        "phase": "off" if str(ambient) == "0" else "on",
        "ambient_declared": str(ambient),
        "ambient_child_env": os.environ.get(AMBIENT_ENV),
        "ambient_observe_env": None,
        "clock_base": clock_base,
        "clock_local": local_now.isoformat(),
        "local_date": local_now.date().isoformat(),
        "root": str(root),
        "pid": os.getpid(),
        "stage": "start",
        "reason_code": None,
        "error_class": None,
        "world_id": None,
        "start_location": None,
        "scene_id": None,
        "world_revision": None,
        "world_observe": {},
        "gateway_socket": {},
        "templates": {},
        "aux_dates": {},
        "life": {},
        "layers": {},
        "checks": {},
        "network_attempts": 0,
        "network_negative_control_refused": False,
        "product_requests": 0,
        "listener_before_close": False,
        "listener_after_close": None,
        "final_position": None,
        "drain": {},
        "home_isolated": False,
        "services_closed": False,
    }
    gateway: Optional[Any] = None
    supply: Optional[Any] = None
    runtime: Any = None
    world_home: Optional[Path] = None
    cognition = _CapturingNoActionCognition()
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
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.01)
        except rt.NetworkForbidden:
            summary["network_negative_control_refused"] = True
        # 负控不是一次真实出网：清零后只统计正式观察/装配期间的尝试。
        guard.attempts = 0

        summary["stage"] = "start_supply"
        supply = rt._SupplyOwner(root / "services")

        summary["stage"] = "prepare_instance"
        instance = root / "instance"
        prepared = odl.prepare(
            instance=instance,
            supply_socket=supply.socket_path,
            governance_socket=supply.socket_path,
            subject="v06:ambient",
            title="V06 ambient isolated",
            world_package=package,
            create_work_artifact=False,
        )
        if not prepared.get("ok"):
            raise rt.PhaseRefused("INSTANCE_PREPARE_REFUSED")
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        summary["world_home"] = str(world_home)
        summary["world_id"] = str(data["world_id"])
        state = json.loads((world_home / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        summary["start_location"] = str((state.get("location") or {}).get("place_id"))
        summary["scene_id"] = str((state.get("scene") or {}).get("scene_id"))
        summary["world_revision"] = state.get("revision")

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
        summary["ambient_observe_env"] = env.get(AMBIENT_ENV)

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

        summary["stage"] = "world_observe"
        import world_ambient_events as wae
        import world_living_skeleton as wls

        observation = wls.observe_world(
            hermes_home=world_home, now=frozen, environment=dict(env))
        observe_ok = (isinstance(observation, Mapping)
                      and str(observation.get("status")) == "available")
        ambient_events = list((observation or {}).get("ambient_events") or [])
        summary["world_observe"] = {
            "status": (observation or {}).get("status"),
            "ambient_key_present": "ambient_events" in (observation or {}),
            "ambient_count": len(ambient_events),
            "events": _safe_events(ambient_events),
            "event_ids": [str(item.get("event_id")) for item in ambient_events
                          if isinstance(item, Mapping)],
            "world_id": (observation or {}).get("world_id"),
            "local_time": (observation or {}).get("local_time"),
            "local_date": (observation or {}).get("local_date"),
            "revision": (observation or {}).get("world_revision"),
            "current_location": {
                key: ((observation or {}).get("current_location") or {}).get(key)
                for key in ("location_id", "area_id", "kind")},
            "feature_enabled": wae.feature_enabled(dict(env)),
        }
        if not observe_ok:
            raise rt.PhaseRefused("WORLD_OBSERVE_UNAVAILABLE")

        summary["stage"] = "template_analysis"
        summary["templates"] = _template_analysis(
            env, world_home, local_now.date(), local_now)
        summary["aux_dates"] = {
            "2026-11-02": _aux_date_query(env, date(2026, 11, 2)),
            "2026-11-03": _aux_date_query(env, date(2026, 11, 3)),
        }

        summary["stage"] = "gateway_socket"
        from world_action_bridge_adapter import WorldBridgeResolver

        client = WorldBridgeResolver(
            environment=dict(env), bridge_dir=str(rt.WORLD_BRIDGE_DIR)).client
        probe: dict[str, Any] = {}
        for name, call in (("get_observation", "get_observation"),
                           ("get_snapshot", "get_snapshot")):
            try:
                result = getattr(client, call)()
                data_view = (result.data if getattr(result, "outcome", None) ==
                             _wbc_ok() else None)
            except Exception:  # noqa: BLE001 - a failed probe is reported, not guessed
                data_view = None
            probe[name] = {
                "ok": data_view is not None,
                "keys": _key_list(data_view),
                "ambient_key_present": bool(
                    isinstance(data_view, Mapping) and "ambient_events" in data_view),
                "world_id": (data_view or {}).get("world_id")
                            if isinstance(data_view, Mapping) else None,
                "location": (data_view or {}).get("location")
                            if isinstance(data_view, Mapping) else None,
            }
        summary["gateway_socket"] = probe

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
        # 与 resident 相同的调用形状：先把正式 World provider 解析出来，再交给装配；
        # 只传 ``world_provider=None`` 会让 cycle 拿不到 provider（装配只把解析结果交给 feed）。
        world_seam = assembly.resolve_world_provider(
            env, clock=lambda: _parse(clock_base))
        summary["life"]["world_provider_status"] = world_seam.get("status")
        summary["life"]["world_provider_reason"] = world_seam.get("reason")
        world_provider = world_seam.get("provider")
        if world_provider is None:
            raise rt.PhaseRefused("WORLD_PROVIDER_UNAVAILABLE")
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=world_provider,
            writer=_RefusingWriter(), enabled=True,
            clock=lambda: _parse(clock_base))
        summary["life"]["assembled"] = assembled.get("state")
        summary["life"]["assembly_reason"] = assembled.get("reason")
        if assembled.get("state") != assembly.READY:
            raise rt.PhaseRefused("ASSEMBLY_NOT_READY")

        summary["stage"] = "evaluate_once"
        cycle = assembled["cycle"]
        out = cycle.evaluate_once(
            trigger_ref="evt:v06:ambient", observed_at=gateway.now_iso(), act=True)
        summary["life"]["evaluate_outcome"] = out.get("outcome")

        summary["stage"] = "collect_layers"
        request = cognition.requests[-1] if cognition.requests else None
        situation_context = dict(getattr(request, "situation_context", {}) or {}) \
            if request is not None else {}
        world_block = situation_context.get("world")
        live_context = getattr(cycle, "last_live_context", None)
        seam_snapshot = getattr(cycle, "last_world_observation", None)
        candidates = _safe_candidates(request) if request is not None else []
        prompt_payload: Any = None
        prompt_text = ""
        prompt_trim: Any = {}
        if request is not None:
            from agency_cognition_adapter import ProductionAgencyCognitionAdapter

            build_adapter = ProductionAgencyCognitionAdapter(client=None)
            prompt_text, prompt_payload = build_adapter.build_prompt(request)
            prompt_trim = dict(getattr(build_adapter, "last_prompt_trim", {}) or {})
        prompt_world: Any = None
        if isinstance(prompt_payload, Mapping):
            prompt_situation = prompt_payload.get("situation_context")
            if isinstance(prompt_situation, Mapping):
                prompt_world = prompt_situation.get("world")

        ambient_members = _ambient_members((observation or {}).get("ambient_events"))
        world_ids = [member["event_id"] for member in ambient_members]
        layer_objects = {
            "world_observation": observation,
            "gateway_socket": probe,
            "life_world_seam_snapshot": seam_snapshot,
            "life_situation_world": world_block,
            "life_live_context": live_context,
            "ag0_candidates": candidates,
            "formal_prompt": prompt_payload,
        }
        layers: dict[str, Any] = {}
        for name in LAYER_ORDER:
            obj = layer_objects[name]
            if name == "formal_prompt" and prompt_payload is None:
                layers[name] = {"status": "NOT_REACHED", "present_ids": [],
                                "paths": {}, "summary_present": {},
                                "summary_paths": {}}
                continue
            if obj is None:
                layers[name] = {"status": "NOT_AVAILABLE", "present_ids": [],
                                "paths": {}, "summary_present": {},
                                "summary_paths": {}}
                continue
            paths = _membership_paths(obj, world_ids) if world_ids else {}
            summary_paths: dict[str, list[str]] = {}
            summary_present: dict[str, bool] = {}
            for member in ambient_members:
                found = _membership_paths(obj, [member["summary"]])
                summary_paths[member["event_id"]] = found.get(member["summary"], [])
                summary_present[member["event_id"]] = bool(found.get(member["summary"]))
            layers[name] = {
                "status": "OBSERVED",
                "present_ids": [item for item in world_ids if paths.get(item)],
                "paths": {item: paths.get(item, []) for item in world_ids},
                "summary_present": summary_present,
                "summary_paths": summary_paths,
            }
        earliest_lost = None
        if world_ids and layers["world_observation"]["present_ids"]:
            for name in AMBIENT_CARRIER_LAYERS:
                row = layers[name]
                if (not row["present_ids"]
                        or not all(row["summary_present"].values())):
                    earliest_lost = name
                    break
        prompt_text_ids = {member["event_id"]: (member["event_id"] in prompt_text)
                           for member in ambient_members}
        prompt_text_summaries = {member["event_id"]: (member["summary"] in prompt_text)
                                 for member in ambient_members}
        summary["layers"] = {
            "event_ids_from_world": world_ids,
            "carrier_layers": list(AMBIENT_CARRIER_LAYERS),
            "layers": layers,
            "earliest_lost_layer": earliest_lost,
            "earliest_lost_seam": (LAYER_SEAM.get(earliest_lost)
                                   if earliest_lost else None),
            "prompt_text": {
                "chars": len(prompt_text),
                "ids_present": prompt_text_ids,
                "summaries_present": prompt_text_summaries,
            },
            "ambient_membership_ok": _ambient_membership_ok(
                layers, prompt_text_ids, prompt_text_summaries),
        }
        provider = getattr(cycle, "world_opportunity_provider", None)
        material = dict(out.get("material") or {}) if isinstance(out, Mapping) else {}
        summary["life"].update({
            "request_captured": request is not None,
            "request_calls": cognition.calls,
            "candidate_ids": [row.get("candidate_id") for row in candidates],
            "candidates": candidates,
            "situation_context_keys": _key_list(situation_context),
            "world_block_keys": _key_list(world_block),
            "world_block_status": (world_block or {}).get("status")
                                  if isinstance(world_block, Mapping) else None,
            "live_context_keys": _key_list(live_context),
            "seam_snapshot_keys": _key_list(seam_snapshot),
            "prompt_keys": _key_list(prompt_payload),
            "prompt_trim": prompt_trim,
            "world_block_ambient_key": _ambient_key_present(world_block),
            "live_context_ambient_key": _ambient_key_present(live_context),
            "prompt_ambient_key": _ambient_key_present(prompt_world),
            "prompt_text_chars": len(prompt_text),
            "ambient_membership_ok": bool(
                (summary.get("layers") or {}).get("ambient_membership_ok")),
            "observed_fact_keys": [str(item) for item in
                                   (getattr(request, "observed_fact_keys", []) or [])][:24]
                                  if request is not None else [],
            "world_error_class": _error_class(
                getattr(cycle, "last_world_error", None)),
            "world_opportunity_ids": [str(item.get("opportunity_id"))
                                      for item in
                                      (getattr(cycle, "last_world_opportunities", []) or [])][:16],
            "world_catalog_status": (
                (getattr(cycle, "last_world_catalog", None) or {}).get("status")),
            "provider_last_reason_class": _error_class(
                getattr(provider, "last_reason", None)),
            "provider_opportunity_view_count": len(
                getattr(provider, "last_opportunity_view", []) or []),
            "provider_observation_keys": _key_list(
                getattr(provider, "last_observation", None)),
            "material_world_error_class": _error_class(material.get("world_error")),
            "material_world_opportunity_count": len(
                material.get("world_opportunities") or []),
            "has_material": bool(material.get("has_material")),
        })
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


def _wbc_ok() -> Any:
    import world_body_client as wbc

    return wbc.OK


# ---------------------------------------------------------------------------------------------
# phase 子进程入口
# ---------------------------------------------------------------------------------------------


def cmd_phase(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_ambient_isolated_check phase")
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


def _phase_checks(summary: Mapping[str, Any]) -> dict[str, bool]:
    observe = dict(summary.get("world_observe") or {})
    life = dict(summary.get("life") or {})
    drain = dict(summary.get("drain") or {})
    declared = str(summary.get("ambient_declared"))
    layers = dict(summary.get("layers") or {})
    layer_rows = dict(layers.get("layers") or {})
    events_present = int(observe.get("ambient_count") or 0) > 0
    ambient_candidates = list((layer_rows.get("ag0_candidates") or {})
                              .get("present_ids") or [])
    ambient_members_complete = (
        (not events_present)
        or len(list(layers.get("event_ids_from_world") or []))
        == int(observe.get("ambient_count") or 0))
    if declared == "1":
        ambient_background_ok = bool(
            life.get("world_block_ambient_key")
            and life.get("live_context_ambient_key")
            and life.get("prompt_ambient_key")
            and life.get("ambient_membership_ok"))
    else:
        ambient_background_ok = not (
            life.get("world_block_ambient_key")
            or life.get("live_context_ambient_key")
            or life.get("prompt_ambient_key"))
    return {
        "ambient_declared_is_0_or_1": declared in ("0", "1"),
        "ambient_child_env_matches_declared": (
            str(summary.get("ambient_child_env")) == declared),
        "ambient_observe_env_matches_declared": (
            str(summary.get("ambient_observe_env")) == declared),
        "negative_control_refused": bool(
            summary.get("network_negative_control_refused")),
        "world_observe_available": str(observe.get("status")) == "available",
        "world_observe_feature_flag_matches": (
            bool(observe.get("feature_enabled")) == (declared == "1")),
        "world_observe_events_have_public_members": ambient_members_complete,
        "life_ambient_background_matches_declared": ambient_background_ok,
        "ambient_is_not_a_candidate": not ambient_candidates,
        "life_assembled_ready": str(life.get("assembled")) == "READY",
        "life_request_captured": bool(life.get("request_captured")),
        "formal_prompt_built": bool(life.get("prompt_keys")),
        "home_isolated": bool(summary.get("home_isolated")),
        "no_network": int(summary.get("network_attempts") or 0) == 0,
        "zero_product_requests": int(summary.get("product_requests") or 0) == 0,
        "listener_closed": summary.get("listener_after_close") is False,
        "runtime_closed": bool(drain.get("runtime_closed")),
        "world_closed": bool((drain.get("world") or {}).get("lease_released")),
        "supply_closed": bool(drain.get("supply_shutdown")),
    }


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
                            "phase": "off" if ambient == "0" else "on",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    except OSError as exc:
        return {"ok": False, "exit": None,
                "summary": {"ok": False, "reason_code": "PHASE_SPAWN_FAILED",
                            "error_class": type(exc).__name__,
                            "phase": "off" if ambient == "0" else "on",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    path = phase_root / SUMMARY_NAME
    if not path.exists():
        return {"ok": False, "exit": exit_code,
                "summary": {"ok": False, "reason_code": "PHASE_SUMMARY_MISSING",
                            "phase": "off" if ambient == "0" else "on",
                            "ambient_declared": ambient, "root": str(phase_root)}}
    summary = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": bool(summary.get("ok")) and exit_code == EXIT_OK,
            "exit": exit_code, "summary": summary}


def _comparison(off: Mapping[str, Any], on: Mapping[str, Any]) -> dict[str, Any]:
    off_observe = dict(off.get("world_observe") or {})
    on_observe = dict(on.get("world_observe") or {})
    world_ids = [str(item) for item in (on_observe.get("event_ids") or [])]
    off_ids = [str(item) for item in (off_observe.get("event_ids") or [])]
    off_life = dict(off.get("life") or {})
    on_life = dict(on.get("life") or {})
    off_layers = dict(dict(off.get("layers") or {}).get("layers") or {})
    on_layers = dict(dict(on.get("layers") or {}).get("layers") or {})
    return {
        "world_observation": {
            "off": {"ambient_key_present": bool(off_observe.get("ambient_key_present")),
                    "ambient_count": int(off_observe.get("ambient_count") or 0),
                    "event_ids": off_ids},
            "on": {"ambient_key_present": bool(on_observe.get("ambient_key_present")),
                   "ambient_count": int(on_observe.get("ambient_count") or 0),
                   "event_ids": world_ids},
            "differs": (bool(off_observe.get("ambient_key_present")),
                        int(off_observe.get("ambient_count") or 0),
                        off_ids) != (bool(on_observe.get("ambient_key_present")),
                                     int(on_observe.get("ambient_count") or 0),
                                     world_ids),
            "conclusion": ("CURRENT_HOME_EVENT_VISIBLE" if world_ids
                           else "CURRENT_HOME_NO_EVENT"),
        },
        "same_world_and_start": bool(
            off.get("world_id") and off.get("world_id") == on.get("world_id")
            and off.get("start_location") == on.get("start_location")
            and off.get("scene_id") == on.get("scene_id")
            and off.get("world_revision") == on.get("world_revision")
            and off.get("clock_base") == on.get("clock_base")),
        "candidate_ids_equal": (
            [row.get("candidate_id") for row in (off_life.get("candidates") or [])]
            == [row.get("candidate_id") for row in (on_life.get("candidates") or [])]),
        "prompt_keys_equal": (
            list(off_life.get("prompt_keys") or []) == list(on_life.get("prompt_keys") or [])),
        "ambient_background": {
            "off_world_block_key": bool(off_life.get("world_block_ambient_key")),
            "on_world_block_key": bool(on_life.get("world_block_ambient_key")),
            "on_live_context_key": bool(on_life.get("live_context_ambient_key")),
            "on_prompt_key": bool(on_life.get("prompt_ambient_key")),
            "on_membership_ok": bool(on_life.get("ambient_membership_ok")),
            "on_prompt_text_chars": int(on_life.get("prompt_text_chars") or 0),
        },
        "candidates": {
            "off": off_life.get("candidates") or [],
            "on": on_life.get("candidates") or [],
        },
        "layers_on": on_layers,
        "layers_off": off_layers,
        "earliest_lost_layer": (on.get("layers") or {}).get("earliest_lost_layer"),
        "earliest_lost_seam": (on.get("layers") or {}).get("earliest_lost_seam"),
        "gap": ("OBSERVED_GAP" if (world_ids and (on.get("layers") or {})
                                   .get("earliest_lost_layer")) else "NONE"),
    }


def cmd_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_ambient_isolated_check check")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--max-wall-seconds", type=float, default=MAX_WALL_SECONDS)
    args = parser.parse_args(list(argv))
    if not args.offline:
        return _emit({"ok": False, "reason_code": "OFFLINE_FLAG_REQUIRED"},
                     code=EXIT_REFUSED)
    if not (0 < float(args.max_wall_seconds) <= MAX_WALL_SECONDS):
        return _emit({"ok": False, "reason_code": "MAX_WALL_SECONDS_OUT_OF_RANGE",
                      "max_wall_seconds": MAX_WALL_SECONDS}, code=EXIT_REFUSED)
    # 父进程也装同一套硬拒守卫：本检查只需要本机文件与子进程管道。
    guard = rt._NetworkGuard()
    with guard:
        return _check_body(args, guard)


def _check_body(args: argparse.Namespace, guard: "rt._NetworkGuard") -> int:
    started = time.monotonic()

    def _finish(payload: dict[str, Any], code: int) -> int:
        payload.setdefault("parent", {})["network_attempts"] = int(guard.attempts)
        payload.setdefault("checks", {})["parent_no_network"] = int(guard.attempts) == 0
        if "both_phases_ok" in payload.get("checks", {}):
            payload["ok"] = bool(payload["checks"]) and all(
                bool(value) for value in payload["checks"].values())
            payload["result"] = "PASS" if payload["ok"] else "FAIL"
        payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return _emit(payload, code=code)


    budget = float(args.max_wall_seconds)
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(WORLD_TZ)
    clock_base = now_utc.isoformat()
    parent_ambient_before = os.environ.get(AMBIENT_ENV)
    repo_before = _repo_default_lifecycle_stat()
    root = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX))
    payload: dict[str, Any] = {
        "ok": False,
        "result": "FAIL",
        "root": str(root),
        "clock_basis": "FROZEN_UTC_TO_ASIA_TOKYO",
        "clock_base_utc": clock_base,
        "clock_local_tokyo": local_now.isoformat(),
        "local_date": local_now.date().isoformat(),
        "source_package": str(SOURCE_PACKAGE),
        "package_identity": {},
        "phases": {},
        "comparison": {},
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

    results: dict[str, dict[str, Any]] = {}
    for name, ambient in (("off", "0"), ("on", "1")):
        remaining = budget - (time.monotonic() - started) - 15.0
        if remaining <= 10.0:
            payload["checks"]["wall_budget_for_%s" % name] = False
            results[name] = {"ok": False, "summary": {
                "ok": False, "reason_code": "WALL_BUDGET_EXHAUSTED",
                "phase": name, "ambient_declared": ambient}}
            break
        phase_root = root / ("%s-%d" % (name, os.getpid()))
        phase_root.mkdir(parents=True, exist_ok=False)
        result = _run_child(phase_root, ambient, clock_base, remaining, package)
        results[name] = result
        payload["phases"][name] = result.get("summary") or {}
        payload["checks"]["%s_phase_ran" % name] = bool(result.get("ok"))
        if not result.get("ok"):
            break

    off = results.get("off", {}).get("summary") or {}
    on = results.get("on", {}).get("summary") or {}
    payload["comparison"] = _comparison(off, on)
    payload["checks"]["same_world_and_start"] = bool(
        payload["comparison"].get("same_world_and_start"))
    payload["checks"]["clock_matches_frozen"] = bool(
        off.get("clock_base") == clock_base and on.get("clock_base") == clock_base)
    payload["checks"]["off_ambient_zero"] = str(off.get("ambient_declared")) == "0"
    payload["checks"]["on_ambient_one"] = str(on.get("ambient_declared")) == "1"
    payload["checks"]["off_ambient_child_env_zero"] = (
        str(off.get("ambient_child_env")) == "0")
    payload["checks"]["on_ambient_child_env_one"] = (
        str(on.get("ambient_child_env")) == "1")
    payload["checks"]["gate_off_both"] = bool(
        off.get("gate") == "off" and on.get("gate") == "off")
    payload["checks"]["no_network_both"] = bool(
        int(off.get("network_attempts") or 0) == 0
        and int(on.get("network_attempts") or 0) == 0
        and off.get("network_negative_control_refused") is True
        and on.get("network_negative_control_refused") is True)
    payload["checks"]["zero_product_requests_both"] = bool(
        int(off.get("product_requests") or 0) == 0
        and int(on.get("product_requests") or 0) == 0)
    payload["checks"]["both_phases_ok"] = bool(
        results.get("off", {}).get("ok") and results.get("on", {}).get("ok"))
    payload["checks"]["life_request_captured_both"] = bool(
        (off.get("life") or {}).get("request_captured") is True
        and (on.get("life") or {}).get("request_captured") is True)
    ambient_background = dict(
        payload["comparison"].get("ambient_background") or {})
    payload["checks"]["off_no_ambient_background"] = not bool(
        ambient_background.get("off_world_block_key"))
    payload["checks"]["on_ambient_background_wired"] = bool(
        ambient_background.get("on_world_block_key")
        and ambient_background.get("on_live_context_key")
        and ambient_background.get("on_prompt_key")
        and ambient_background.get("on_membership_ok"))
    on_candidates = ((((on.get("layers") or {}).get("layers") or {})
                      .get("ag0_candidates") or {}).get("present_ids") or [])
    payload["checks"]["ambient_is_not_a_candidate"] = not list(on_candidates)
    payload["checks"]["candidate_ids_equal"] = bool(
        payload["comparison"].get("candidate_ids_equal"))
    repo_after = _repo_default_lifecycle_stat()
    payload["checks"]["repo_default_lifecycle_untouched"] = repo_before == repo_after
    payload["parent"]["ambient_env_after"] = os.environ.get(AMBIENT_ENV)
    payload["checks"]["parent_env_not_patched"] = (
        os.environ.get(AMBIENT_ENV) == parent_ambient_before)
    payload["checks"]["parent_no_network"] = int(guard.attempts) == 0
    payload["ok"] = bool(payload["checks"]) and all(
        bool(value) for value in payload["checks"].values())
    payload["result"] = "PASS" if payload["ok"] else "FAIL"
    payload["wall_limit_seconds"] = budget
    return _finish(payload, code=EXIT_OK if payload["ok"] else EXIT_FAILED)


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
