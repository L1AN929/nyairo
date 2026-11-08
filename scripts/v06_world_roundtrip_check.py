#!/usr/bin/env python3
"""0.6 A：零模型、隔离世界往返的正式链检查（off 负控 + on 往返）。

做什么
======
每次 ``check`` 在 ``/tmp/nyairo-v06-roundtrip-*`` 下建一个全新根，再各建一个独立子根：

1. ``off``：同一世界/同一地点/同一业务起始时刻，用 **gate=off** 走完整正式链
   （正式 Decision → AG-1/AG-2/AR-0 → World gateway → Resolver）。必须得到
   明确的预提交拒绝 ``GATE_OFF``：Action 仍 PREPARED、attempt PRE_SUBMIT_FAILED、
   零回执、零票、零 travel、世界位置不变。
2. ``on``：另一子根，**gate=canonical**，脚本 Decision 明确选一个现有合法邻近室外
   地点出门；到达由 World owner 自己的位置结算，Activity 经既有完成合同释放；
   然后用 **新的** 正式 Decision 返回原地点。两段各有独立 execution、一次性票
   （CONSUMED）、travel 记录与到达结算。

不做什么
========
不调用任何产品模型、不起 HTTP(S)、不读模型密钥、不访问任何既有实例或旧批次根、
不改默认 gate/配置/生产规则、不删数据。Decision/内容/观察是脚本替身；位置、票、
旅程与结算都由正式 owner 产生。运动时间用显式注入的测试业务钟推进，不真实等待。

输出
====
只输出固定白名单安全元数据（id/枚举/布尔/计数/本新根路径），不输出 prompt、正文、
reasoning、token、header、env 全文或异常任意正文。未执行断言不得 PASS；失败非零。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for _entry in (str(ROOT), str(ROOT / "components"),
               str(ROOT / "components" / "life" / "scripts"),
               str(ROOT / "components" / "world" / "scripts"),
               str(ROOT / "components" / "world" / "service"),
               str(ROOT / "components" / "world" / "service" / "bridge"),
               str(ROOT / "components" / "supply")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
WORLD_RUNTIME_ROOT = ROOT / "components" / "world"
WORLD_BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
REGISTRY_NAME = "action_authorizations.json"
JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
STATE_RELATIVE = ("data", "world", "world_state.json")
SUMMARY_NAME = "v06-phase-summary.json"
FIXTURE_PREFIX = "nyairo-v06-roundtrip-"
MAX_WALL_SECONDS = 600.0
WORLD_SEED = 929711
WORLD_THEME = "coastal_town"
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

#: 可能把写面/身份带出本新根的环境键。缺失或越界都在写前拒绝。
PATH_GUARDED_KEYS = (
    "CHIYO_WORLD_HOME", "HERMES_HOME", "CHIYO_WORLD_PACKAGE",
    "CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_STATE", "LIFE_SUPPLY_SOCKET",
    "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "WORLD_BODY_SOCKET",
    "WORLD_ACTION_LIFECYCLE_PATH", "WORLD_ACTION_AUTH_REGISTRY_DIR",
    "WORLD_ACTIVITY_STATE", "WORLD_EXPERIENCE_SPOOL",
    "WORLD_SELF_MAINTENANCE_STATE", "HOME", "NYA02_PROFILE_HOME",
    "CHIYO_PROFILE_HOME",
)
#: 显式传给子进程的白名单（不继承宿主 NYA02/CHIYO/密钥环境）。
CHILD_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ")


class PhaseRefused(RuntimeError):
    """A named refusal; the caller reports the code, not arbitrary exception text."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)


class NetworkForbidden(RuntimeError):
    """An AF_INET/AF_INET6 socket was attempted inside an offline phase."""


# ---------------------------------------------------------------------------------------------
# 安全输出与路径守卫
# ---------------------------------------------------------------------------------------------


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False,
                                indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return int(code)


def _is_scratch_root(path: Any) -> bool:
    """路径必须挂在 ``mkdtemp(prefix="nyairo-v06-roundtrip-")`` 的根下。

    子进程的 ``TMPDIR`` 指向本子根内部，``tempfile.gettempdir()`` 因此在子根里；
    这里固定接受 ``/tmp``（验收命令的根）或宿主临时目录，并要求带前缀的祖先
    直接挂在其下，避免把 TMPDIR 自身当基准。
    """

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


def _assert_env_paths_under(root: Path, environment: Mapping[str, Any]) -> None:
    """所有已知写的路径键必须存在且落在本子根内；否则写前拒绝。"""

    root_resolved = Path(root).resolve()
    for key in PATH_GUARDED_KEYS:
        value = str(environment.get(key) or "").strip()
        if not value:
            raise PhaseRefused("ENV_PATH_MISSING:%s" % key)
        candidate = Path(value).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            raise PhaseRefused("ENV_PATH_UNRESOLVABLE:%s" % key)
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise PhaseRefused("ENV_PATH_OUTSIDE_ROOT:%s" % key)


def _forbid_model_env(environment: Mapping[str, Any]) -> None:
    for key in environment:
        if str(key).startswith("NYA02_MODEL_") or str(key).startswith("CHIYO_MODEL_"):
            raise PhaseRefused("MODEL_ENV_PRESENT")


def _safe_reason(exc: BaseException) -> str:
    if isinstance(exc, PhaseRefused):
        return exc.reason_code
    if isinstance(exc, NetworkForbidden):
        return "NETWORK_FORBIDDEN"
    return "ERROR:%s" % type(exc).__name__


# ---------------------------------------------------------------------------------------------
# 离线网络守卫：只允许本根 AF_UNIX 正式 owner 通讯
# ---------------------------------------------------------------------------------------------


class _NetworkGuard:
    def __init__(self) -> None:
        self.attempts = 0
        self._socket_module: Any = None
        self._real_socket: Any = None
        self._real_create_connection: Any = None
        self._real_getaddrinfo: Any = None

    def __enter__(self) -> "_NetworkGuard":
        module = socket
        guard = self
        real = module.socket

        class _GuardedSocket(real):  # type: ignore[misc, valid-type]
            def __init__(self, family: int = -1, type: int = -1, proto: int = -1,
                         fileno: Optional[int] = None) -> None:
                resolved_family = module.AF_INET if family in (-1, None) else family
                if resolved_family in (module.AF_INET, module.AF_INET6):
                    guard.attempts += 1
                    raise NetworkForbidden(
                        "offline phase forbids AF_INET/AF_INET6 sockets")
                if fileno is None:
                    super().__init__(family, type, proto)
                else:
                    super().__init__(family, type, proto, fileno)

        def _deny(*_args: Any, **_kwargs: Any) -> Any:
            guard.attempts += 1
            raise NetworkForbidden("offline phase forbids outbound network calls")

        self._socket_module = module
        self._real_socket = real
        self._real_create_connection = module.create_connection
        self._real_getaddrinfo = module.getaddrinfo
        module.socket = _GuardedSocket
        module.create_connection = _deny
        module.getaddrinfo = _deny
        return self

    def __exit__(self, *_exc: Any) -> None:
        module = self._socket_module
        if module is None:
            return
        module.socket = self._real_socket
        module.create_connection = self._real_create_connection
        module.getaddrinfo = self._real_getaddrinfo


# ---------------------------------------------------------------------------------------------
# 正式 owner 的薄夹具（都落在本子根内）
# ---------------------------------------------------------------------------------------------


class _SupplyOwner:
    """真实 Life Supply owner（真 UNIX socket；数据只落本子根）。"""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        root.mkdir(parents=True, exist_ok=True)
        uid = os.geteuid()
        self.socket_path = root / "life-supply.sock"
        self.service = LifeSupplyService(
            root / "data", operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(),
            writer_instance="v06-roundtrip:%d" % os.getpid())
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._stopped = False

    def stop(self) -> dict[str, Any]:
        if self._stopped:
            return {"supply_shutdown": True}
        self._stopped = True
        try:
            self.server.shutdown()
        finally:
            try:
                self.server.server_close()
            finally:
                self.service.close()
        return {"supply_shutdown": True}


class _WorldGateway:
    """真实 World 服务：真 socket / 真 registry / 真 Resolver；业务钟显式注入。"""

    def __init__(self, world_home: Path, *, world_id: str,
                 environment: Mapping[str, Any], clock_base: str) -> None:
        import world_body_config as wcfg
        import world_body_service as wbsvc

        self.world_home = Path(world_home)
        self._now = _parse(clock_base)
        self.config = wcfg.load_config(
            dict(environment), runtime_root=WORLD_RUNTIME_ROOT,
            world_home=self.world_home, runtime_mode="canonical",
            authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0, clock=lambda: self._now,
            instance_id="v06-roundtrip-%d" % os.getpid())
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 1800.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not Path(self.socket_path).exists():
            if time.monotonic() > deadline:
                raise PhaseRefused("WORLD_SOCKET_NOT_READY")
            time.sleep(0.02)

    # -- 业务时钟 ---------------------------------------------------------
    def now_iso(self) -> str:
        return _iso(self._now)

    def set_now(self, moment: Any) -> None:
        self._now = _parse(moment)

    # -- 调用方环境 -------------------------------------------------------
    def environment(self) -> dict[str, str]:
        return {"WORLD_BODY_SOCKET": str(self.socket_path),
                "WORLD_BODY_INTEGRATION_MODE": "canonical",
                "NYAIRO_WORLD_BRIDGE_DIR": str(WORLD_BRIDGE_DIR)}

    @property
    def registry_path(self) -> Path:
        return self.world_home / "run" / REGISTRY_NAME

    def authorizations(self) -> list[dict[str, Any]]:
        path = self.registry_path
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [dict(row) for row in (data.get("records") or [])]

    # -- 收尾 -------------------------------------------------------------
    def stop(self) -> dict[str, Any]:
        report: dict[str, Any] = {}
        try:
            self.service._stop_requested = True  # 唤醒 selector，让 serve 自己退出
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(2.0)
                    probe.connect(str(self.service.socket_path))
                    probe.sendall(b'{"command": "status"}\n')
                    probe.recv(65536)
            except OSError:
                pass
            self._thread.join(timeout=10)
            report = dict(self.service.shutdown() or {})
        except Exception:  # noqa: BLE001 - 收尾失败只报未知，不编结果
            report = {}
        return {
            "lease_released": report.get("lease_released"),
            "runtime_persisted": report.get("runtime_persisted"),
            "shutdown_at": report.get("shutdown_at"),
        }


# ---------------------------------------------------------------------------------------------
# 观察与脚本 Decision（都只使用正式链提供的候选）
# ---------------------------------------------------------------------------------------------


class _MoveObservation:
    """位置事实来自 owner 的 ``world_state.json``；候选来自真实的 action space。"""

    def __init__(self, environment: Mapping[str, Any], clock: _WorldGateway) -> None:
        self.environment = dict(environment)
        self.clock = clock
        self.last_observation: Optional[dict[str, Any]] = None
        self.last_action_space: Optional[dict[str, Any]] = None
        self._frozen: Optional[list[dict[str, Any]]] = None

    def freeze(self, occurred_at: str) -> list[dict[str, Any]]:
        """gate off 负控用：先在 canonical 下取真实候选，再冻结给本轮用。

        这是既有 ``world_refusal_result_shape`` 冒烟的同一条接法：gate 关闭时
        ``action_intent.build_action_space`` 本来就不提供 MOVE，负控要测的是
        **正式提交**被 ``GATE_OFF`` 明确拒绝，不是"没有候选所以零执行"。
        """

        items = self.observe(occurred_at=occurred_at)
        self._frozen = [dict(item) for item in items]
        return [dict(item) for item in self._frozen]

    def observe(self, *, occurred_at: Optional[str] = None) -> list[dict[str, Any]]:
        if self._frozen is not None:
            return [dict(item) for item in self._frozen]
        import action_intent
        import world_foundation as wf
        import world_living_skeleton as wls
        import world_move_opportunity_source as wmos

        home = Path(self.environment["CHIYO_WORLD_HOME"])
        state = dict(wf.WorldStateStore(home.joinpath(*STATE_RELATIVE)).load() or {})
        state.setdefault("world_revision", state.get("revision"))
        state.setdefault("pose", "standing")
        state.setdefault("visible_objects", [])
        self.last_observation = dict(state)
        place = (state.get("location") or {}).get("place_id")
        catalog = {item["location_id"]: item
                   for item in wls.location_catalog(self.environment)}
        current = catalog.get(str(place)) or {}
        offered = [{"location_id": neighbor, "label": neighbor}
                   for neighbor in (current.get("reachable") or [])]
        space = action_intent.build_action_space(
            state,
            available_actions={"actions": [{"verb": "MOVE", "available": True,
                                            "candidates": offered}]})
        visible = action_intent.redact_action_space_for_selector(space)
        self.last_action_space = visible
        moment = str(occurred_at or self.clock.now_iso())
        return wmos.observed_move_opportunities(
            state, action_space=visible, occurred_at=moment, observed_at=moment)

    def __call__(self) -> list[dict[str, Any]]:
        return self.observe()


class _ScriptedCognition:
    """按预设目标选 START 的脚本 Decision；只从 AG-1 真正给的候选里挑。"""

    model_id = "stub:v06-roundtrip"
    model_version = "v06-roundtrip-stub-v1"

    def __init__(self, targets: Sequence[str]) -> None:
        self._targets = [str(item) for item in targets]
        self.calls = 0
        self.offered: list[list[str]] = []
        self.reasons: list[str] = []

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        import agency_decision_ag1 as ag1

        self.calls += 1
        candidates = [dict(item) for item in (getattr(request, "candidates", []) or [])]
        self.offered.append([str(item.get("candidate_kind") or "")
                             for item in candidates])
        allowed = [str(code) for code in
                   (getattr(request, "allowed_reason_codes", []) or [])]
        wanted = ("place:%s" % self._targets[0]) if self._targets else ""
        chosen: Optional[dict[str, Any]] = None
        for candidate in candidates:
            if str(candidate.get("candidate_kind") or "") != "CONSIDER_WORLD_OPPORTUNITY":
                continue
            refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
            if wanted and wanted in refs:
                chosen = candidate
                break
        if chosen is None:
            self.reasons.append("TARGET_NOT_OFFERED")
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": allowed[:1],
                   "short_rationale": "scripted roundtrip target not offered",
                   "deferred_candidate_ids": []}
        else:
            self._targets.pop(0)
            self.reasons.append("START")
            raw = {"decision": "START",
                   "selected_candidate_id": chosen.get("candidate_id"),
                   "reason_codes": allowed[:1],
                   "short_rationale": "scripted roundtrip move",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:v06:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _RefusingWriter:
    """内容写入器替身：本检查不写任何正文，真被调用即失败。"""

    def next_content(self, *_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("content writer must not be used by the roundtrip check")


# ---------------------------------------------------------------------------------------------
# 读数
# ---------------------------------------------------------------------------------------------


def _position(world_home: Path) -> Optional[str]:
    path = world_home.joinpath(*STATE_RELATIVE)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    return str((state.get("location") or {}).get("place_id") or "") or None


def _world_state_bytes(world_home: Path) -> bytes:
    return world_home.joinpath(*STATE_RELATIVE).read_bytes()


def _travels(world_home: Path) -> list[dict[str, Any]]:
    path = world_home.joinpath(*TRAVEL_RELATIVE)
    if not path.exists():
        return []
    return [dict(row) for row in
            (json.loads(path.read_text(encoding="utf-8")).get("travels") or [])]


def _journey_state(world_home: Path) -> dict[str, Any]:
    path = world_home.joinpath(*JOURNEY_RELATIVE)
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _catalog_entry(environment: Mapping[str, Any], place: str) -> dict[str, Any]:
    import world_living_skeleton as wls

    for item in wls.location_catalog(environment):
        if str(item.get("location_id")) == str(place):
            return dict(item)
    return {}


def _pick_outdoor_target(environment: Mapping[str, Any], origin: str) -> str:
    """现有默认图里 origin 的合法邻近室外地点（不造路、不额外 seed）。"""

    entry = _catalog_entry(environment, origin)
    reachable = [str(item) for item in (entry.get("reachable") or [])]
    for place in reachable:
        kind = str(_catalog_entry(environment, place).get("kind") or "")
        if kind and kind != "home":
            return place
    raise PhaseRefused("NO_LEGAL_OUTDOOR_NEIGHBOR")


def _world_isolation_env(world_home: Path) -> dict[str, str]:
    """把 bridge 的五个写面显式钉到本世界家的 ``run/`` 下。

    顺序是「显式 env → CHIYO_WORLD_HOME/HERMES_HOME → 仓库默认」；只给 home 时，
    其余写面靠目录探测，仍可能回落到仓库默认。这里和既有 e 批的
    ``_world_isolation_env`` 一样全部显式给出，父进程的环境不参与。
    """

    home = Path(world_home)
    run_dir = home / "run"
    return {
        "CHIYO_WORLD_HOME": str(home),
        "HERMES_HOME": str(home),
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(run_dir),
        "WORLD_ACTION_LIFECYCLE_PATH": str(run_dir / "action_lifecycle.jsonl"),
        "WORLD_ACTIVITY_STATE": str(run_dir / "activity_state.json"),
        "WORLD_EXPERIENCE_SPOOL": str(run_dir / "experience_candidates.jsonl"),
        "WORLD_SELF_MAINTENANCE_STATE": str(run_dir / "self_maintenance_state.json"),
    }


def _attempts(runtime: Any, action_id: str) -> list[dict[str, Any]]:
    if not action_id:
        return []
    return [dict(row) for row in runtime.action_read.list_attempts(action_id)]


def _receipts(runtime: Any, action_id: str) -> list[dict[str, Any]]:
    if not action_id:
        return []
    return [dict(row) for row in runtime.action_read.list_receipts(action_id)]


def _round_view(runtime: Any, out: Mapping[str, Any]) -> dict[str, Any]:
    action_id = str(out.get("action_id") or "")
    attempts = _attempts(runtime, action_id)
    receipts = _receipts(runtime, action_id)
    journey = dict(out.get("journey") or {})
    return {
        "outcome": out.get("outcome"),
        "step_path": out.get("step_path"),
        "action_id": action_id or None,
        "action_status": out.get("action_status"),
        "activity_ref": out.get("activity_ref"),
        "pre_submit": dict(out.get("pre_submit") or {}),
        "attempt_status": (attempts[0].get("status") if attempts else None),
        "attempt_error_class": (attempts[0].get("error_class") if attempts else None),
        "attempt_transport": (attempts[0].get("transport_result") if attempts else None),
        "attempts": len(attempts),
        "receipts": len(receipts),
        "journey": {key: journey.get(key) for key in (
            "journey_id", "journey_status", "origin_location_id",
            "destination_location_id", "location_id", "expected_arrival_at",
            "segment_status", "segments_confirmed", "arrived", "route")},
    }


def _settle_view(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in (
        "ok", "journey_id", "journey_status", "reason_code", "location_id",
        "target_location_id", "current_segment", "segments_confirmed",
        "segment_status", "expected_arrival_at", "arrived",
        "observed_location_id", "arrival_refused", "progress_state",
        "checkpoint_ref", "settle_only")}


def _foreground(runtime: Any) -> Optional[str]:
    state = dict(runtime.canonical_state() or {})
    ref = state.get("foreground_activity_ref")
    return str(ref) if ref else None


def _activity_status(runtime: Any, activity_ref: Any) -> Optional[str]:
    """owner 自己记录的活动状态；取不到就是 None，不猜。"""

    if not activity_ref:
        return None
    try:
        record = runtime.activity_read.get_activity(str(activity_ref)) or {}
    except Exception:  # noqa: BLE001
        return None
    return str(record.get("status") or "") or None


def _ticket_view(gateway: _WorldGateway) -> list[dict[str, Any]]:
    return [{"execution_id": row.get("execution_id"),
             "state": str(row.get("state") or "").upper()}
            for row in gateway.authorizations()]


def _travel_view(world_home: Path) -> list[dict[str, Any]]:
    return [{"execution_id": row.get("execution_id"), "status": row.get("status")}
            for row in _travels(world_home)]


# ---------------------------------------------------------------------------------------------
# phase：一个子进程内的完整 off/on 场景
# ---------------------------------------------------------------------------------------------


def _run_phase(root: Path, gate: str, clock_base: str, wall_seconds: float,
               guard: "_NetworkGuard") -> dict[str, Any]:
    import components.worldgen.schema as worldgen_schema
    from components.worldgen.generator import generate_world

    import one_day_life as odl

    deadline = time.monotonic() + max(10.0, float(wall_seconds))
    root = Path(root)
    phase_name = "off" if gate == "off" else "on"
    summary: dict[str, Any] = {
        "ok": False, "phase": phase_name, "gate": gate,
        "clock_basis": "TEST_LOGICAL", "clock_base": clock_base,
        "root": str(root), "pid": os.getpid(), "stage": "start",
        "reason_code": None, "error_class": None,
        "rounds": [], "settlements": [], "foreground_after_arrivals": [],
        "tickets": [], "travels": [], "final_position": None,
        "checks": {}, "network_attempts": 0, "product_requests": 0,
        "listener_before_close": False, "listener_after_close": None,
        "drain": {}, "home_isolated": False, "services_closed": False,
    }
    gateway: Optional[_WorldGateway] = None
    supply: Optional[_SupplyOwner] = None
    runtime: Any = None
    world_home: Optional[Path] = None
    initial_state: Optional[bytes] = None
    try:
        os.environ["REAL_WORKSPACE_PROVISION"] = "ON"
        os.environ["REAL_ARTIFACT_ACTION"] = "ON"
        os.environ[GATE_ENV] = gate
        os.environ["WORLD_BODY_INTEGRATION_MODE"] = "canonical"
        os.environ["NYAIRO_WORLD_BRIDGE_DIR"] = str(WORLD_BRIDGE_DIR)
        os.environ["HOME"] = str(root / "home")
        os.environ["NYA02_PROFILE_HOME"] = str(root / "profile")
        os.environ["CHIYO_PROFILE_HOME"] = str(root / "profile")
        for key in list(os.environ):
            if key.startswith("NYA02_MODEL_") or key.startswith("CHIYO_MODEL_"):
                os.environ.pop(key, None)
        for name in ("home", "profile", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)

        summary["stage"] = "generate_world"
        package_dir = root / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        worldgen_schema.write_package(
            package_dir, generate_world(seed=WORLD_SEED, theme=WORLD_THEME))

        summary["stage"] = "start_supply"
        supply = _SupplyOwner(root / "services")

        summary["stage"] = "prepare_instance"
        instance = root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=supply.socket_path,
            governance_socket=supply.socket_path,
            subject="v06:roundtrip", title="V06 world roundtrip",
            world_package=package_dir, create_work_artifact=False)
        if not prepared.get("ok"):
            raise PhaseRefused("INSTANCE_PREPARE_REFUSED")
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        summary["world_home"] = str(world_home)
        summary["world_id"] = str(data["world_id"])

        summary["stage"] = "align_environment"
        env = odl.instance_environment(instance)
        _forbid_model_env(env)
        for key in list(env):
            if key.startswith("NYA02_MODEL_") or key.startswith("CHIYO_MODEL_"):
                env.pop(key, None)
        for key in odl.OWNED_ENV_NAMES:
            if key in env:
                os.environ[key] = str(env[key])
        isolation = _world_isolation_env(world_home)
        for key, value in isolation.items():
            os.environ[key] = value
            env[key] = value
        os.environ[GATE_ENV] = gate
        os.environ["WORLD_BODY_INTEGRATION_MODE"] = "canonical"
        os.environ["NYAIRO_WORLD_BRIDGE_DIR"] = str(WORLD_BRIDGE_DIR)
        os.environ["HOME"] = str(root / "home")

        summary["stage"] = "start_world"
        gateway = _WorldGateway(world_home, world_id=str(data["world_id"]),
                                environment=env, clock_base=clock_base)
        env.update(gateway.environment())
        env.update(isolation)
        env[GATE_ENV] = gate
        env["HOME"] = str(root / "home")
        env["NYA02_PROFILE_HOME"] = str(root / "profile")
        env["CHIYO_PROFILE_HOME"] = str(root / "profile")
        summary["gateway_socket"] = str(gateway.socket_path)
        summary["listener_before_close"] = bool(Path(gateway.socket_path).exists())
        if not summary["listener_before_close"]:
            raise PhaseRefused("WORLD_LISTENER_ABSENT")
        _assert_env_paths_under(root, {**env, **os.environ})
        summary["home_isolated"] = str(
            Path(os.path.expanduser("~")).resolve()).startswith(str(root.resolve()))
        if not summary["home_isolated"]:
            raise PhaseRefused("HOME_NOT_ISOLATED")

        summary["stage"] = "build_runtime"
        # 重模块在环境对齐之后导入：模块级 INSTALL_ROOT 等只读一次。
        import autonomous_life_assembly as assembly
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (
            JourneyWorldResolver, RealJourneyWorldActionAdapter)

        origin = _position(world_home)
        if not origin:
            raise PhaseRefused("START_POSITION_UNKNOWN")
        target = _pick_outdoor_target(env, origin)
        summary["start_location"] = origin
        summary["outdoor_target"] = target
        summary["return_target"] = origin
        summary["start_kind"] = _catalog_entry(env, origin).get("kind")
        summary["target_kind"] = _catalog_entry(env, target).get("kind")

        state_root = odl.life_state_root(instance)
        port = lap.LifeArtifactPort(str(supply.socket_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"],
            workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        targets = [target, origin] if gate == "canonical" else [target]
        cognition = _ScriptedCognition(targets)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        observation = _MoveObservation(env, gateway)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=observation, writer=_RefusingWriter(), enabled=True)
        if assembled.get("state") != assembly.READY:
            raise PhaseRefused("ASSEMBLY_NOT_READY")
        cycle = assembled["cycle"]
        initial_state = _world_state_bytes(world_home)

        summary["stage"] = "outbound"
        if time.monotonic() >= deadline:
            raise PhaseRefused("WALL_LIMIT_BEFORE_OUTBOUND")
        if gate == "off":
            # 只读地取一次真实候选（canonical），随后把 gate 关掉再走正式提交。
            os.environ[GATE_ENV] = "canonical"
            env[GATE_ENV] = "canonical"
            summary["materialized_under"] = "canonical"
            observation.freeze(gateway.now_iso())
            os.environ[GATE_ENV] = "off"
            env[GATE_ENV] = "off"
            summary["submit_gate"] = "off"
        else:
            summary["submit_gate"] = "canonical"
        outbound = cycle.evaluate_once(
            trigger_ref="evt:v06:outbound", observed_at=gateway.now_iso(), act=True)
        view = _round_view(runtime, outbound)
        view["position_after"] = _position(world_home)
        summary["rounds"].append(view)
        if gate == "off":
            summary["stage"] = "done"
            return summary
        if str(outbound.get("outcome")) != "IN_TRANSIT":
            raise PhaseRefused("OUTBOUND_NOT_IN_TRANSIT")
        arrival = str((outbound.get("journey") or {}).get("expected_arrival_at") or "")
        if not arrival:
            raise PhaseRefused("OUTBOUND_ARRIVAL_TIME_UNKNOWN")
        if time.monotonic() >= deadline:
            raise PhaseRefused("WALL_LIMIT_BEFORE_RETURN")

        summary["stage"] = "outbound_settle"
        gateway.set_now(arrival)
        settled = cycle.settle_world_journey(observed_at=arrival)
        settled_view = _settle_view(settled)
        settled_view["activity_status"] = _activity_status(
            runtime, settled.get("activity_ref"))
        travels_now = _travels(world_home)
        settled_view["execution_id"] = (str(travels_now[-1].get("execution_id"))
                                        if travels_now else None)
        summary["settlements"].append(settled_view)
        summary["foreground_after_arrivals"].append(_foreground(runtime))
        if settled.get("arrived") is not True:
            raise PhaseRefused("OUTBOUND_NOT_ARRIVED")
        if summary["foreground_after_arrivals"][-1] is not None:
            raise PhaseRefused("FOREGROUND_NOT_RELEASED")

        summary["stage"] = "return"
        if time.monotonic() >= deadline:
            raise PhaseRefused("WALL_LIMIT_BEFORE_RETURN")
        back = cycle.evaluate_once(
            trigger_ref="evt:v06:return", observed_at=gateway.now_iso(), act=True)
        view2 = _round_view(runtime, back)
        view2["position_after"] = _position(world_home)
        summary["rounds"].append(view2)
        if str(back.get("outcome")) != "IN_TRANSIT":
            raise PhaseRefused("RETURN_NOT_IN_TRANSIT")
        arrival2 = str((back.get("journey") or {}).get("expected_arrival_at") or "")
        if not arrival2:
            raise PhaseRefused("RETURN_ARRIVAL_TIME_UNKNOWN")

        summary["stage"] = "return_settle"
        gateway.set_now(arrival2)
        settled2 = cycle.settle_world_journey(observed_at=arrival2)
        settled2_view = _settle_view(settled2)
        settled2_view["activity_status"] = _activity_status(
            runtime, settled2.get("activity_ref"))
        travels_now = _travels(world_home)
        settled2_view["execution_id"] = (str(travels_now[-1].get("execution_id"))
                                         if travels_now else None)
        summary["settlements"].append(settled2_view)
        summary["foreground_after_arrivals"].append(_foreground(runtime))
        if settled2.get("arrived") is not True:
            raise PhaseRefused("RETURN_NOT_ARRIVED")

        summary["stage"] = "done"
        return summary
    except PhaseRefused as exc:
        summary["reason_code"] = exc.reason_code
    except NetworkForbidden:
        summary["reason_code"] = "NETWORK_FORBIDDEN"
    except BaseException as exc:  # noqa: BLE001 - 固定分类，不输出任意正文
        summary["reason_code"] = _safe_reason(exc)
        summary["error_class"] = type(exc).__name__
    finally:
        summary["network_attempts"] = int(getattr(guard, "attempts", 0))
        if world_home is not None:
            try:
                summary["final_position"] = _position(world_home)
            except Exception:  # noqa: BLE001
                summary["final_position"] = None
            if gateway is not None:
                try:
                    summary["tickets"] = _ticket_view(gateway)
                except Exception:  # noqa: BLE001
                    summary["tickets"] = []
            try:
                summary["travels"] = _travel_view(world_home)
            except Exception:  # noqa: BLE001
                summary["travels"] = []
            if initial_state is not None:
                try:
                    summary["world_state_unchanged"] = (
                        _world_state_bytes(world_home) == initial_state)
                except Exception:  # noqa: BLE001
                    summary["world_state_unchanged"] = None
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


# ---------------------------------------------------------------------------------------------
# 子进程入口
# ---------------------------------------------------------------------------------------------


def cmd_phase(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_roundtrip_check phase")
    parser.add_argument("--root", required=True)
    parser.add_argument("--gate", required=True, choices=("off", "canonical"))
    parser.add_argument("--clock-base", required=True)
    parser.add_argument("--wall-seconds", type=float, default=300.0)
    args = parser.parse_args(list(argv))
    root = Path(args.root)
    if not _is_scratch_root(root):
        return _emit({"ok": False, "reason_code": "SCRATCH_ROOT_REQUIRED",
                      "root": str(root)}, code=EXIT_REFUSED)
    root.mkdir(parents=True, exist_ok=True)
    guard = _NetworkGuard()
    with guard:
        summary = _run_phase(root, args.gate, args.clock_base,
                             args.wall_seconds, guard)
    summary["network_attempts"] = int(guard.attempts)
    summary["product_requests"] = 0
    summary["checks"] = (_off_checks(summary) if args.gate == "off"
                         else _on_checks(summary))
    summary["ok"] = bool(summary.get("reason_code") is None
                         and summary["checks"]
                         and all(bool(value) for value in summary["checks"].values()))
    (root / SUMMARY_NAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
    return EXIT_OK if summary["ok"] else EXIT_FAILED


# ---------------------------------------------------------------------------------------------
# check：父进程只编排、不 patch 自己的环境
# ---------------------------------------------------------------------------------------------


def _child_environment(root: Path, gate: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in CHILD_ENV_PASSTHROUGH:
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
        GATE_ENV: gate,
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
    })
    return env


def _run_child(phase_root: Path, gate: str, clock_base: str,
               remaining: float) -> dict[str, Any]:
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "phase",
               "--root", str(phase_root), "--gate", gate,
               "--clock-base", clock_base, "--wall-seconds", "%.1f" % remaining]
    try:
        completed = subprocess.run(
            command, env=_child_environment(phase_root, gate),
            capture_output=True, text=True, timeout=max(5.0, remaining))
        exit_code: Optional[int] = int(completed.returncode)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": None,
                "summary": {"ok": False, "reason_code": "PHASE_TIMEOUT",
                            "phase": "off" if gate == "off" else "on",
                            "gate": gate, "root": str(phase_root)}}
    except OSError as exc:
        return {"ok": False, "exit": None,
                "summary": {"ok": False, "reason_code": "PHASE_SPAWN_FAILED",
                            "error_class": type(exc).__name__,
                            "phase": "off" if gate == "off" else "on",
                            "gate": gate, "root": str(phase_root)}}
    path = phase_root / SUMMARY_NAME
    if not path.exists():
        return {"ok": False, "exit": exit_code,
                "summary": {"ok": False, "reason_code": "PHASE_SUMMARY_MISSING",
                            "phase": "off" if gate == "off" else "on",
                            "gate": gate, "root": str(phase_root)}}
    summary = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": bool(summary.get("ok")) and exit_code == EXIT_OK,
            "exit": exit_code, "summary": summary}


# -- 阶段判据（纯函数，测试直接喂合成 payload） --------------------------------------------


def _off_checks(summary: Mapping[str, Any]) -> dict[str, bool]:
    rounds = list(summary.get("rounds") or [])
    first = dict(rounds[0]) if rounds else {}
    pre = dict(first.get("pre_submit") or {})
    return {
        "gate_off": str(summary.get("gate")) == "off",
        "submit_gate_off": str(summary.get("submit_gate")) == "off",
        "one_round": len(rounds) == 1,
        "explicit_gate_off_refusal": (
            str(first.get("outcome")) == "STEP_REFUSED"
            and str(pre.get("reason_code")) == "GATE_OFF"),
        "action_prepared": str(first.get("action_status")) == "PREPARED",
        "attempt_pre_submit_failed": (
            str(first.get("attempt_status")) == "PRE_SUBMIT_FAILED"
            and str(first.get("attempt_error_class")) == "GATE_OFF"),
        "zero_receipts": int(first.get("receipts") or 0) == 0,
        "zero_tickets": len(list(summary.get("tickets") or [])) == 0,
        "zero_travels": len(list(summary.get("travels") or [])) == 0,
        "start_unchanged": (
            bool(summary.get("start_location"))
            and str(summary.get("final_position")) == str(summary.get("start_location"))),
        "world_state_unchanged": summary.get("world_state_unchanged") is True,
        "no_network": int(summary.get("network_attempts") or 0) == 0,
        "zero_product_requests": int(summary.get("product_requests") or 0) == 0,
        "isolated_home": summary.get("home_isolated") is True,
        "listener_closed": summary.get("listener_after_close") is False,
        "world_closed": bool((summary.get("drain") or {}).get("runtime_closed")),
        "world_lease_released": bool(
            ((summary.get("drain") or {}).get("world") or {}).get("lease_released")),
        "supply_closed": bool((summary.get("drain") or {}).get("supply_shutdown")),
    }


def _on_checks(summary: Mapping[str, Any]) -> dict[str, bool]:
    rounds = list(summary.get("rounds") or [])
    arrivals = list(summary.get("settlements") or [])
    tickets = list(summary.get("tickets") or [])
    travels = list(summary.get("travels") or [])
    first = dict(rounds[0]) if len(rounds) > 0 else {}
    second = dict(rounds[1]) if len(rounds) > 1 else {}
    arrive1 = dict(arrivals[0]) if len(arrivals) > 0 else {}
    arrive2 = dict(arrivals[1]) if len(arrivals) > 1 else {}
    foreground = list(summary.get("foreground_after_arrivals") or [])
    execution_ids = [str(row.get("execution_id") or "") for row in tickets]
    travel_ids = [str(row.get("execution_id") or "") for row in travels]
    ticket_states = [str(row.get("state") or "").upper() for row in tickets]
    origin = str(summary.get("start_location") or "")
    outdoor = str(summary.get("outdoor_target") or "")
    return {
        "gate_canonical": str(summary.get("gate")) == "canonical",
        "submit_gate_canonical": str(summary.get("submit_gate")) == "canonical",
        "outdoor_target_is_outdoor": (
            bool(summary.get("target_kind"))
            and str(summary.get("target_kind")) != "home"),
        "two_rounds": len(rounds) == 2,
        "outbound_departed": (
            str(first.get("outcome")) == "IN_TRANSIT"
            and str(first.get("step_path")) == "WORLD_AR0"
            and str(first.get("action_status")) == "SUCCEEDED"),
        "outbound_not_arrived_early": (
            str(first.get("position_after")) == origin
            and (first.get("journey") or {}).get("arrived") is not True),
        "outbound_settled_by_owner": (
            arrive1.get("arrived") is True
            and str(arrive1.get("journey_status")) == "completed"
            and str(arrive1.get("observed_location_id")) == outdoor),
        "outbound_activity_completed": (
            str(arrive1.get("activity_status")) == "COMPLETED"),
        "foreground_released": bool(foreground) and foreground[0] is None,
        "return_departed": (
            str(second.get("outcome")) == "IN_TRANSIT"
            and str(second.get("step_path")) == "WORLD_AR0"
            and str(second.get("action_status")) == "SUCCEEDED"),
        "return_not_arrived_early": str(second.get("position_after")) == outdoor,
        "return_settled_by_owner": (
            arrive2.get("arrived") is True
            and str(arrive2.get("journey_status")) == "completed"
            and str(arrive2.get("observed_location_id")) == origin),
        "return_activity_completed": (
            str(arrive2.get("activity_status")) == "COMPLETED"),
        "return_target_is_origin": str(summary.get("return_target")) == origin,
        "distinct_executions": (
            len(execution_ids) == 2 and all(execution_ids)
            and execution_ids[0] != execution_ids[1]
            and travel_ids == execution_ids),
        "settlement_execution_links": (
            len(execution_ids) == 2
            and str(arrive1.get("execution_id")) == execution_ids[0]
            and str(arrive2.get("execution_id")) == execution_ids[1]),
        "two_tickets_consumed": (
            len(tickets) == 2 and ticket_states == ["CONSUMED", "CONSUMED"]),
        "two_travels": len(travels) == 2,
        "final_position_origin": str(summary.get("final_position")) == origin,
        "no_network": int(summary.get("network_attempts") or 0) == 0,
        "zero_product_requests": int(summary.get("product_requests") or 0) == 0,
        "isolated_home": summary.get("home_isolated") is True,
        "listener_closed": summary.get("listener_after_close") is False,
        "world_closed": bool((summary.get("drain") or {}).get("runtime_closed")),
        "world_lease_released": bool(
            ((summary.get("drain") or {}).get("world") or {}).get("lease_released")),
        "supply_closed": bool((summary.get("drain") or {}).get("supply_shutdown")),
    }


def _audit_root(root: Path) -> dict[str, Any]:
    """只读本子根：位置 / 旅程 / travel / 票；不带正文。"""

    instance = root / "instance" / "instance.json"
    audit: dict[str, Any] = {"root": str(root), "ok": False,
                             "reason_code": None, "position": None,
                             "tickets": [], "travels": [], "journey_history": 0,
                             "active_journey_status": None,
                             "foreground_activity_ref": None, "activities": []}
    try:
        data = json.loads(instance.read_text(encoding="utf-8"))
        world_home = Path(str(data["world_home"]))
        audit["world_home"] = str(world_home)
        audit["position"] = _position(world_home)
        registry = world_home / "run" / REGISTRY_NAME
        if registry.exists():
            records = json.loads(registry.read_text(encoding="utf-8")).get("records") or []
            audit["tickets"] = [{"execution_id": row.get("execution_id"),
                                 "state": str(row.get("state") or "").upper()}
                                for row in records]
        audit["travels"] = _travel_view(world_home)
        state = _journey_state(world_home)
        audit["journey_history"] = len(state.get("history") or [])
        active = state.get("active") or {}
        audit["active_journey_status"] = active.get("status")
        life_state_path = root / "instance" / "life" / "state" / "canonical_life_state.json"
        if life_state_path.exists():
            life_state = json.loads(life_state_path.read_text(encoding="utf-8"))
            audit["foreground_activity_ref"] = life_state.get("foreground_activity_ref")
            table = (life_state.get("activities")
                     or life_state.get("activities_by_id") or {})
            if isinstance(table, dict):
                audit["activities"] = [
                    {"activity_id": activity_id,
                     "status": str((record or {}).get("status") or "")}
                    for activity_id, record in table.items()
                    if isinstance(record, dict)]
        audit["ok"] = True
    except FileNotFoundError:
        audit["reason_code"] = "AUDIT_FILES_MISSING"
    except (OSError, ValueError, KeyError) as exc:
        audit["reason_code"] = "AUDIT_UNREADABLE:%s" % type(exc).__name__
    return audit


def _repo_default_lifecycle_stat() -> Optional[tuple[int, int]]:
    path = ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl"
    try:
        info = path.stat()
    except OSError:
        return None
    return (int(info.st_size), int(info.st_mtime_ns))


def cmd_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_roundtrip_check check")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--max-wall-seconds", type=float, default=MAX_WALL_SECONDS)
    args = parser.parse_args(list(argv))
    if not args.offline:
        return _emit({"ok": False, "reason_code": "OFFLINE_FLAG_REQUIRED"},
                     code=EXIT_REFUSED)
    if not (0 < float(args.max_wall_seconds) <= MAX_WALL_SECONDS):
        return _emit({"ok": False, "reason_code": "MAX_WALL_SECONDS_OUT_OF_RANGE",
                      "max_wall_seconds": MAX_WALL_SECONDS}, code=EXIT_REFUSED)

    started = time.monotonic()
    budget = float(args.max_wall_seconds)
    repo_before = _repo_default_lifecycle_stat()
    root = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX))
    clock_base = _iso()
    payload: dict[str, Any] = {
        "ok": False, "root": str(root), "clock_basis": "TEST_LOGICAL",
        "clock_base": clock_base, "parent_pid": os.getpid(),
        "phases": {}, "audit": {}, "checks": {},
    }
    results: dict[str, dict[str, Any]] = {}
    for name, gate in (("off", "off"), ("on", "canonical")):
        remaining = budget - (time.monotonic() - started) - 15.0
        if remaining <= 10.0:
            payload["checks"]["wall_budget_for_%s" % name] = False
            results[name] = {"ok": False, "summary": {
                "ok": False, "reason_code": "WALL_BUDGET_EXHAUSTED"}}
            break
        phase_root = root / ("%s-%d" % (name, os.getpid()))
        phase_root.mkdir(parents=True, exist_ok=True)
        result = _run_child(phase_root, gate, clock_base, remaining)
        results[name] = result
        payload["phases"][name] = result.get("summary") or {}
        if not result.get("ok"):
            payload["checks"]["%s_phase_ran" % name] = False
            break
        payload["checks"]["%s_phase_ran" % name] = True

    if results.get("off", {}).get("ok"):
        payload["checks"].update(_off_checks(results["off"]["summary"]))
    if results.get("on", {}).get("ok"):
        payload["checks"].update(_on_checks(results["on"]["summary"]))

    off_summary = results.get("off", {}).get("summary") or {}
    on_summary = results.get("on", {}).get("summary") or {}
    payload["checks"]["same_scenario_and_clock"] = bool(
        off_summary.get("world_id")
        and off_summary.get("world_id") == on_summary.get("world_id")
        and off_summary.get("start_location") == on_summary.get("start_location")
        and off_summary.get("outdoor_target") == on_summary.get("outdoor_target")
        and off_summary.get("clock_base") == on_summary.get("clock_base"))
    payload["checks"]["off_materialization_disclosed"] = (
        off_summary.get("materialized_under") == "canonical")

    # 父级独立只读审计：重读本子根，不信子进程自报。
    off_root = Path(str(results.get("off", {}).get("summary", {}).get("root") or ""))
    on_root = Path(str(results.get("on", {}).get("summary", {}).get("root") or ""))
    audit: dict[str, Any] = {}
    if str(off_root) and off_root.exists():
        audit["off"] = _audit_root(off_root)
        payload["checks"]["off_audit_tickets_zero"] = not audit["off"]["tickets"]
        payload["checks"]["off_audit_travels_zero"] = not audit["off"]["travels"]
        payload["checks"]["off_audit_position_unchanged"] = (
            audit["off"]["position"] is not None
            and audit["off"]["position"] == payload["phases"].get(
                "off", {}).get("start_location"))
    if str(on_root) and on_root.exists():
        audit["on"] = _audit_root(on_root)
        tickets = audit["on"]["tickets"]
        travels = audit["on"]["travels"]
        payload["checks"]["on_audit_two_consumed_tickets"] = (
            len(tickets) == 2
            and [row["state"] for row in tickets] == ["CONSUMED", "CONSUMED"])
        payload["checks"]["on_audit_two_travels"] = len(travels) == 2
        payload["checks"]["on_audit_distinct_executions"] = (
            len(tickets) == 2
            and tickets[0]["execution_id"] != tickets[1]["execution_id"]
            and [row["execution_id"] for row in travels]
                == [row["execution_id"] for row in tickets])
        payload["checks"]["on_audit_final_home"] = (
            audit["on"]["position"] is not None
            and audit["on"]["position"] == payload["phases"].get(
                "on", {}).get("start_location"))
        activities = audit["on"]["activities"]
        payload["checks"]["on_audit_two_completed_activities"] = (
            len(activities) == 2
            and all(row["status"] == "COMPLETED" for row in activities)
            and audit["on"]["foreground_activity_ref"] is None)
    payload["audit"] = audit

    repo_after = _repo_default_lifecycle_stat()
    payload["checks"]["repo_default_lifecycle_untouched"] = repo_before == repo_after
    payload["checks"]["both_phases_ok"] = bool(
        results.get("off", {}).get("ok") and results.get("on", {}).get("ok"))
    payload["ok"] = bool(payload["checks"]) and all(
        bool(value) for value in payload["checks"].values())
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    payload["wall_limit_seconds"] = budget
    return _emit(payload, code=EXIT_OK if payload["ok"] else EXIT_FAILED)


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
