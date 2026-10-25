#!/usr/bin/env python3
"""一条零网络的真实 owner 端到端冒烟：观测计数 / 规则空候选 / 终点标签 / 候选供给。

它做什么（全部在本机、0 产品模型、0 网络）：

1. 起一个**真实 Life Supply owner**（``LifeSupplyService`` + 自己的 Unix socket），在全新
   ``/tmp`` 根里按 operator 权限建工作区与 grant，写入一件**明确的测试材料**（不是她的日记），
   再用真实 ``ArtifactContinuationSource`` 材料化候选——证明"真实 owner 来源 → 候选"通着；
   显式排除该作品时同一 producer 合法给出 0 条。
2. 用真实的 ``AutonomousLifeCycle`` 跑两个回合：一个 ``RULE_RESOLVED``（空候选）和一个
   ``COGNITIVE_REQUIRED``，打印模型决策轮次 1、规则窗口 1、以及规则回合的
   ``route`` / ``reason_codes`` 标注。
3. AG-0 的窗口逻辑负控：手造一条机会，活着 -> 1 条候选，过期 -> 0 条（只证窗口，不证来源）。
4. **真实 World owner 整链**：真实 ``WorldBodyService``（自己的 gateway socket、真实
   world home 与 ``daily_neighborhood_v1`` 世界包）+ 真实 ``LifeSupplyService`` 建实例，
   ``resolve_world_provider(clock=业务钟)`` 得到正式 ``WorldMoveOpportunityProvider``；
   真实 ``AutonomousLifeCycle`` 刷新 → 真实 ``WorldOpportunityCandidateSource`` →
   生产 runtime 的候选读口。业务锚点先于准备结束（与 b 同构），随后快进超过真实+300 秒，
   并演示旧混钟负控、重复刷新不重放、撤权后目标缺席、无 World 配置具名拒绝、自有 owner 收尾。
5. 用 ``scripts/fast_day.py`` 的 clock-only 路径跑到逻辑 horizon，打印时钟状态必须是
   ``STOPPED``；另起一个未到 horizon 的窗口保持 ``RUNNING``（可续语义未改）。

所有自有进程/线程在 finally 里收尾；夹具根保留在唯一的 ``/tmp`` 子目录，便于复查。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import artifact_continuation_source as acs  # noqa: E402
import autonomous_life_assembly as ala  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import demo_business_clock as dbc  # noqa: E402
import fast_day  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
OBSERVED_AT = NOW.isoformat()
SUBJECT = ag0.GLOBAL_SUBJECT_ID

#: 真实 World 整链夹具：有限街区包 + 服务自己的 mutation gate（与 I 包测试同一套）。
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
WORLD_BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
WORLD_GATE = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}
INSTANCE_GATES = {"REAL_WORKSPACE_PROVISION": "ON", "REAL_ARTIFACT_ACTION": "ON"}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def _fresh_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="nyairo-obs-smoke-", dir="/tmp"))


def _call(socket_path: Path, request: dict) -> dict:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(30)
    client.connect(str(socket_path))
    try:
        client.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        line = client.makefile("rb").readline(1 << 22)
    finally:
        client.close()
    if not line:
        raise RuntimeError("owner closed without an answer")
    return json.loads(line.decode("utf-8"))


class _Owner:
    """A real Life Supply owner on its own socket.  It is stopped in ``finally``."""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="observation-candidate-smoke")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, request: dict) -> dict:
        return _call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


def _issue(owner: _Owner, *, capability: str, scope: str, target: str,
           authority_scope: str, use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise RuntimeError("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def _workspace(owner: _Owner) -> str:
    grant = _issue(owner, capability="PROVISION_PERSONAL_WORKSPACE",
                   scope="workspace:personal", target="workspace:%s" % SUBJECT,
                   authority_scope="life-supply.workspace.v1", use_mode="ONE_SHOT",
                   operation_id="smoke:workspace:%s" % uuid.uuid4().hex[:8])
    answer = owner.call({"op": "provision_workspace", "grant_id": grant,
                         "operation_key": "smoke:workspace:%s" % uuid.uuid4().hex[:8],
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise RuntimeError("workspace refused: %r" % (answer,))
    return str(answer["workspace"]["workspace_id"])


def _owner_supply() -> dict:
    """Real owner -> real candidate producer.  Returns a report, and closes the owner."""

    root = _fresh_root()
    owner = _Owner(root)
    try:
        workspace = _workspace(owner)
        grant = _issue(owner, capability="COMMIT_MANAGED_ARTIFACT",
                       scope="artifact:personal", target="workspace:%s" % workspace,
                       authority_scope="life-supply.artifact.v1", use_mode="REUSABLE",
                       operation_id="smoke:artifact:%s" % uuid.uuid4().hex[:8])
        port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        created = port.create_artifact(
            workspace_id=workspace,
            title="冒烟测试材料（operator 提供，不是她的日记）",
            content="这是一段用于验证候选供给接线的测试文字，不是产品内容。",
            grant_id=grant,
            operation_key=lap.operation_key_for("smoke", "fixture", "supply", SUBJECT))
        if not created.applied:
            raise RuntimeError("artifact refused: %r" % (created.to_dict(),))
        artifact_id = str(created.artifact_id)

        source = acs.ArtifactContinuationSource(port, subject_id=SUBJECT)
        candidates, snapshot_ref, meta = source.materialize(observed_at=OBSERVED_AT)
        excluded = acs.ArtifactContinuationSource(
            port, subject_id=SUBJECT, exclude_artifact_ids=[artifact_id])
        none, _snap, empty_meta = excluded.materialize(observed_at=OBSERVED_AT)
        return {
            "owner": "RUNNING",
            "artifact": artifact_id,
            "candidates": len(candidates),
            "candidate_kind": (candidates[0].candidate_kind if candidates else None),
            "candidate_source": (candidates[0].source_kind if candidates else None),
            "snapshot_ref": snapshot_ref,
            "producer_meta": meta,
            "excluded_candidates": len(none),
            "excluded_meta": empty_meta,
        }
    finally:
        owner.stop()
        print("  owner: stopped (own socket closed)")


class _Runtime:
    """The cycle's owner reads, with no model and no network."""

    def __init__(self, *, state_root: Path, route: str) -> None:
        self.state_root = Path(state_root)
        self.foreground = None
        self.activity_read = SimpleNamespace(
            get_current_life_view=lambda: {"foreground_activity_ref": None},
            get_activity=lambda _ref: None,
            list_open_activities=lambda: [])
        self.candidate_read = SimpleNamespace(get_candidate=lambda _ref: None)
        self.action_read = SimpleNamespace(list_receipts=lambda _id: [], list_actions=lambda **_: [])
        self.candidate_registry = SimpleNamespace(list_adapters=lambda: [])
        self.world_opportunity_source = SimpleNamespace(
            record_observed_opportunity=lambda item: None)
        self.artifact_action_adapter = None
        self.world_action_adapter = None
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})
        self.coordinator = _Coordinator(route)

    def canonical_state(self):
        return {"foreground_activity_ref": None}


class _Coordinator:
    def __init__(self, route: str) -> None:
        self.route = route

    def run_decision_and_adoption_cycle(self, **_kwargs):
        reason_codes = ([] if self.route == ag1.ROUTE_COGNITIVE_REQUIRED
                        else [ag1.REASON_NO_LEGAL_CANDIDATE])
        record = SimpleNamespace(decision=ag1.DECISION_NO_ACTION,
                                 decision_route=self.route,
                                 selected_candidate_ref=None,
                                 reason_codes=reason_codes,
                                 short_rationale="smoke scripted")
        return {"correlation_id": "corr:smoke",
                "decision_result": {"decision_record": record},
                "adoption_result": {"adoption_record": SimpleNamespace(
                    adoption_status="APPLIED", rejection_reason_code=None)},
                "integration_record": SimpleNamespace(activity_ref=None,
                                                      integration_id="integ:smoke"),
                "step_execution_result": {}}

    def emit_experience_handoff(self, **_kwargs):
        return {"dispatch_result": {"deliveries": []}, "handoff_event": None}


class _Provider:
    """One observed place, stamped with the instant the cycle hands it."""

    def __init__(self) -> None:
        self.last_observation = {"location": {"place_id": "home"}}
        self.last_opportunity_view: list = []
        self.last_catalog = None
        self.last_observed_at = None
        self.seen: list[str] = []
        self._calls = 0

    def observe(self, *, occurred_at=None):
        if not occurred_at:
            raise RuntimeError("observe() without the round instant")
        self.seen.append(str(occurred_at))
        self._calls += 1
        start = datetime.fromisoformat(str(occurred_at))
        self.last_observed_at = start.isoformat()
        return [{
            "opportunity_id": "wopp:move:home:r1:street_%d" % self._calls,
            "perception_ref": "percept:gateway-snapshot:home:r1",
            "world_revision_ref": "wrev:world:r1",
            "target_refs": ["place:street_%d" % self._calls],
            "valid_from": start.isoformat(),
            "valid_until": (start + timedelta(seconds=300)).isoformat(),
            "occurred_at": start.isoformat(),
            "observed_at": start.isoformat(),
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": SUBJECT,
            "location_ref": "place:home",
        }]


def _round(route: str) -> dict:
    root = _fresh_root()
    runtime = _Runtime(state_root=root, route=route)
    cycle = alc.AutonomousLifeCycle(runtime, enabled=True, max_rounds_per_day=48, env={})
    cycle.world_opportunity_provider = _Provider()
    out = cycle.evaluate_once(trigger_ref="evt:smoke:%s" % route.lower(),
                              observed_at=OBSERVED_AT)
    return {"outcome": out["outcome"], "llm_calls": out["llm_calls"],
            "detail": out.get("detail"), "route": out.get("decision_route"),
            "reason_codes": out.get("reason_codes")}


def _world_supply() -> dict:
    source = ag0.WorldOpportunityCandidateSource()
    start = NOW
    item = {
        "opportunity_id": "wopp:move:home:r1:street_1",
        "perception_ref": "percept:gateway-snapshot:home:r1",
        "world_revision_ref": "wrev:world:r1",
        "target_refs": ["place:street_1"],
        "valid_from": start.isoformat(),
        "valid_until": (start + timedelta(seconds=60)).isoformat(),
        "occurred_at": start.isoformat(),
        "observed_at": start.isoformat(),
        "observed": True,
        "status": "AVAILABLE",
        "revision": 1,
        "subject_id": SUBJECT,
    }
    source.record_observed_opportunity(item)
    live, _snap, _meta = source.materialize(observed_at=OBSERVED_AT)
    expired, _snap2, _meta2 = source.materialize(
        observed_at=(start + timedelta(minutes=10)).isoformat())
    return {"live_candidates": len(live), "expired_candidates": len(expired)}


class _WorldOwner:
    """真实 ``WorldBodyService``：自己的 world home、gateway socket、真实 Resolver。

    这不是替身：它就是 World 包自己的服务循环（与 I 包夹具同一装配），只把后台 tick
    放慢，避免在冒烟窗口里顺手改世界状态。存在 ``finally`` 里关闭。
    """

    def __init__(self, world_home: Path, *, world_id: str, environment: dict) -> None:
        import world_body_config as wcfg
        import world_body_service as wbsvc

        self.config = wcfg.load_config(
            dict(environment), runtime_root=ROOT / "components" / "world",
            world_home=Path(world_home), runtime_mode="canonical",
            authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0, instance_id="obs-smoke-%d" % os.getpid())
        self.startup = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self.thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 900.0}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10.0
        while not self.socket_path.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("world gateway socket never appeared: %s"
                                   % self.socket_path)
            time.sleep(0.02)

    def environment(self) -> dict:
        return {"WORLD_BODY_SOCKET": str(self.socket_path),
                "WORLD_BODY_INTEGRATION_MODE": "canonical",
                "NYAIRO_WORLD_BRIDGE_DIR": str(WORLD_BRIDGE_DIR)}

    def stop(self) -> None:
        self.service.shutdown()
        self.thread.join(timeout=5)


def _real_world_chain() -> dict:
    """真实 World owner → 正式 provider/assembly 时钟 → 真实 cycle → 真实 AG-0/读口。

    场景与 b 同构：业务锚点在 ``prepare`` **之前**取，首轮刷新时业务时刻已落后墙钟
    数分钟；随后再快进到真实+400 秒。全程 0 产品模型、仅本机 owner socket。
    """

    logical = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0)
    root = _fresh_root()
    supply = _Owner(root)
    report: dict = {"owners_stopped": False}
    runtime = world = None
    gate = mock.patch.dict(os.environ, WORLD_GATE)
    gate.start()
    try:
        with mock.patch.dict(os.environ, INSTANCE_GATES):
            odl.prepare(instance=root / "instance",
                        supply_socket=supply.socket_path,
                        governance_socket=supply.socket_path,
                        subject="smoke:real-world", title="真实 World 冒烟夹具",
                        world_package=WORLD_PACKAGE)
        instance = root / "instance"
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        env = odl.instance_environment(instance)
        world = _WorldOwner(world_home, world_id=data["world_id"], environment=env)
        env = odl.instance_environment(instance)
        env.update(world.environment())

        resolved = ala.resolve_world_provider(env, clock=lambda: logical)
        provider = resolved["provider"]
        provider_type = type(provider).__name__ if provider is not None else None
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches,
            install_root=instance / "life")
        cycle = alc.AutonomousLifeCycle(
            runtime, enabled=True, max_rounds_per_day=48, env=env,
            world_home=str(world_home), clock=lambda: logical)
        cycle.world_opportunity_provider = provider
        items = cycle._refresh_world_opportunities(observed_at=logical.isoformat())
        targets = sorted({ref for item in items for ref in item["target_refs"]})
        move = next((item for item in items
                     if "place:street_front" in item["target_refs"]), None)
        validity = ({"valid_from": move["valid_from"], "valid_until": move["valid_until"],
                     "observed_at": move["observed_at"],
                     "same_domain": (_utc(move["valid_from"]) == _utc(logical)
                                     and _utc(move["observed_at"]) == _utc(logical)
                                     and _utc(move["valid_until"])
                                     == _utc(logical) + timedelta(seconds=300))}
                    if move else None)
        runtime.candidate_read.build_candidate_set(observed_at=logical.isoformat())
        world_candidates = [
            c for c in runtime.candidate_read.list_open_candidates(
                observed_at=logical.isoformat())
            if c["candidate_kind"] == ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY]
        candidate_targets = sorted({ref for c in world_candidates
                                    for ref in c["target_refs"]})

        # 旧混钟负控：同一个真实 provider，但没有业务钟注入、cycle 裸调（丢 occurred_at）。
        legacy_provider = ala.resolve_world_provider(env)["provider"]
        legacy_items = legacy_provider.observe()
        legacy_source = ag0.WorldOpportunityCandidateSource()
        for item in legacy_items:
            legacy_source.record_observed_opportunity(item)
        legacy_reasons = sorted({
            legacy_source.validate_source(dict(item), observed_at=logical.isoformat())[1]
            for item in legacy_items})
        legacy_candidates, _snap, _meta = legacy_source.materialize(
            observed_at=logical.isoformat())

        # 快进到真实+400 秒：真实 owner 观察照样生成业务域有效机会。
        fast = (datetime.now(timezone.utc) + timedelta(seconds=400)).replace(microsecond=0)
        cycle.world_opportunity_provider = ala.resolve_world_provider(
            env, clock=lambda: fast)["provider"]
        fast_items = cycle._refresh_world_opportunities(observed_at=fast.isoformat())
        runtime.candidate_read.build_candidate_set(observed_at=fast.isoformat())
        fast_ids = sorted(c["candidate_id"] for c in
                          runtime.candidate_read.list_open_candidates(
                              observed_at=fast.isoformat())
                          if c["candidate_kind"]
                          == ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY)
        fast_targets = sorted({ref for item in fast_items for ref in item["target_refs"]})
        # 同一时刻重复刷新不重放：机会 id 与候选 id 都不变。
        again = cycle._refresh_world_opportunities(observed_at=fast.isoformat())
        runtime.candidate_read.build_candidate_set(observed_at=fast.isoformat())
        again_ids = sorted(c["candidate_id"] for c in
                           runtime.candidate_read.list_open_candidates(
                               observed_at=fast.isoformat())
                           if c["candidate_kind"]
                           == ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY)
        # 撤权：source 自己的撤销口，之后该目标不再出现在候选里。
        move_id = next(item["opportunity_id"] for item in fast_items
                       if "place:street_front" in item["target_refs"])
        runtime.world_opportunity_source.expire_opportunity(
            move_id, observed_at=fast.isoformat())
        runtime.candidate_read.build_candidate_set(observed_at=fast.isoformat())
        after_expire = sorted({ref for c in runtime.candidate_read.list_open_candidates(
            observed_at=fast.isoformat())
            if c["candidate_kind"] == ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY
            for ref in c["target_refs"]})

        unreadable = ala.resolve_world_provider({})
        status = provider.client.get_status() if provider is not None else None
        report.update({
            "owner_ready": bool(status is not None and (status.data or {}).get("ready")),
            "provider_status": resolved["status"],
            "provider_type": provider_type,
            "bridge_dir": resolved.get("bridge_dir"),
            "logical": logical.isoformat(),
            "lag_seconds": round((datetime.now(timezone.utc) - logical).total_seconds(), 1),
            "opportunities": len(items),
            "targets": targets,
            "validity": validity,
            "candidates": len(world_candidates),
            "candidate_targets": candidate_targets,
            "legacy_reasons": legacy_reasons,
            "legacy_candidates": len(legacy_candidates),
            "fast_logical": fast.isoformat(),
            "fast_targets": fast_targets,
            "fast_candidates": len(fast_ids),
            "replay_stable": fast_ids == again_ids,
            "after_expire_targets": after_expire,
            "withdrawn_absent": "place:street_front" not in after_expire,
            "unreadable_status": unreadable["status"],
            "unreadable_reason": unreadable["reason"],
        })
        return report
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        if world is not None:
            try:
                world.stop()
            except Exception:
                pass
        try:
            supply.stop()
        except Exception:
            pass
        gate.stop()
        report["owners_stopped"] = True


def _endpoint() -> dict:
    report = {}
    # (a) horizon reached -> STOPPED
    root = _fresh_root()
    prepared = fast_day.prepare(root, run_id="smoke-horizon", real_seconds=1.0,
                                logical_seconds=24.0, max_new_requests=1)
    if not prepared.get("ok"):
        raise RuntimeError("prepare refused: %r" % (prepared,))
    supervisor = fast_day.FastDaySupervisor(root, poll_seconds=0.1, clock_only=True)
    result = supervisor.start(duration_seconds=5)
    clock = json.loads(dbc.clock_path(root / "state").read_text(encoding="utf-8"))
    report["horizon_reached"] = bool((result.get("ran") or {}).get("horizon_reached"))
    report["state_at_horizon"] = clock.get("state")
    # (b) a short window before horizon keeps RUNNING (resumable)
    root2 = _fresh_root()
    prepared2 = fast_day.prepare(root2, run_id="smoke-short", real_seconds=7200.0,
                                 logical_seconds=86400.0, max_new_requests=1)
    if not prepared2.get("ok"):
        raise RuntimeError("prepare refused: %r" % (prepared2,))
    supervisor2 = fast_day.FastDaySupervisor(root2, poll_seconds=0.1, clock_only=True)
    result2 = supervisor2.start(duration_seconds=1)
    clock2 = json.loads(dbc.clock_path(root2 / "state").read_text(encoding="utf-8"))
    report["short_horizon_reached"] = bool((result2.get("ran") or {}).get("horizon_reached"))
    report["state_short_window"] = clock2.get("state")
    return report


def main() -> int:
    print("== 1. 真实 owner 来源 -> 候选（ArtifactContinuationSource）")
    supply = _owner_supply()
    print("  owner artifact:", supply["artifact"])
    print("  candidates:", supply["candidates"], "kind:", supply["candidate_kind"],
          "source:", supply["candidate_source"])
    print("  snapshot:", supply["snapshot_ref"])
    print("  显式排除同一作品 ->", supply["excluded_candidates"], "条（合法过滤）")

    print("== 2. 观测计数与规则空候选标注（真实 cycle 两回合）")
    rule = _round(ag1.ROUTE_RULE_RESOLVED)
    cognitive = _round(ag1.ROUTE_COGNITIVE_REQUIRED)
    print("  rule round:", rule)
    print("  cognitive round:", cognitive)
    print("  合计: 决策轮次 2；模型决策轮次 %d；规则窗口 %d；请求预留 0（本冒烟不碰账本）"
          % (rule["llm_calls"] + cognitive["llm_calls"],
             1 if rule["llm_calls"] == 0 else 0))

    print("== 3. AG-0 World 窗口逻辑（手造条目，仅窗口负控）")
    world = _world_supply()
    print("  live candidates:", world["live_candidates"],
          "expired candidates:", world["expired_candidates"])

    print("== 4. 真实 World owner 整链（0 产品模型 / 仅本地 socket）")
    chain = _real_world_chain()
    print("  world owner ready:", chain["owner_ready"], "socket/provider:",
          chain["provider_status"], chain["provider_type"])
    print("  assembly bridge:", chain["bridge_dir"])
    print("  业务回合:", chain["logical"], "落后墙钟", chain["lag_seconds"], "秒")
    print("  机会:", chain["opportunities"], "targets:", chain["targets"])
    print("  MOVE 有效期:", chain["validity"])
    print("  AG-0 实际候选:", chain["candidates"], "targets:", chain["candidate_targets"])
    print("  旧混钟负控:", chain["legacy_reasons"], "-> 候选", chain["legacy_candidates"])
    print("  快进 +400s:", chain["fast_logical"], "候选", chain["fast_candidates"],
          "targets:", chain["fast_targets"], "重复刷新不重放:", chain["replay_stable"])
    print("  撤销 street_front 后 targets:", chain["after_expire_targets"],
          "已撤权目标缺席:", chain["withdrawn_absent"])
    print("  无 World 配置:", chain["unreadable_status"], chain["unreadable_reason"])
    print("  自有 owner 收尾:", chain["owners_stopped"])

    print("== 5. 终点标签")
    endpoint = _endpoint()
    print("  horizon reached:", endpoint["horizon_reached"], "state:", endpoint["state_at_horizon"])
    print("  short window horizon reached:", endpoint["short_horizon_reached"],
          "state:", endpoint["state_short_window"])

    ok = (supply["candidates"] == 1 and supply["excluded_candidates"] == 0
          and rule["llm_calls"] == 0 and cognitive["llm_calls"] == 1
          and rule["route"] == ag1.ROUTE_RULE_RESOLVED
          and rule["reason_codes"] == [ag1.REASON_NO_LEGAL_CANDIDATE]
          and world["live_candidates"] == 1 and world["expired_candidates"] == 0
          and chain["owner_ready"] and chain["provider_status"] == "READY"
          and chain["provider_type"] == "WorldMoveOpportunityProvider"
          and "place:street_front" in chain["targets"]
          and bool(chain["validity"] and chain["validity"]["same_domain"])
          and chain["legacy_reasons"] == ["OPPORTUNITY_NOT_YET_OPEN"]
          and chain["legacy_candidates"] == 0
          and "place:street_front" in chain["fast_targets"]
          and chain["fast_candidates"] >= 1
          and chain["replay_stable"] and chain["withdrawn_absent"]
          and chain["owners_stopped"]
          and chain["unreadable_status"] == "UNAVAILABLE"
          and endpoint["state_at_horizon"] == dbc.STATE_STOPPED
          and endpoint["state_short_window"] == dbc.STATE_RUNNING)
    print("== RESULT:", "OK" if ok else "FAIL", "(0 产品网络调用)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
