#!/usr/bin/env python3
"""观测计数 / 规则空候选标注 / 终点标签 / 真实来源候选供给（本轮合并修复的行为验收）。

只证明这四件事，不冒充产品模型或真实一天：

1. **调用计数按 route 计**：AG-1 的 ``RULE_RESOLVED``（空候选/全硬拒）按契约是 0 次 LLM，
   不再按 outcome 名称记成 1。四轮真决策 + 四轮本地规则必须合计 4 次模型决策轮，
   而不是 8 次；resident 汇总把规则窗口单独计数，账本权威仍在既有 request ledger。
2. **规则空候选标明来源**：wake 的 ``extra`` 带 ``decision_route`` / ``reason_codes`` /
   ``selected_candidate_ref``，``detail`` 写明 route 与 reason，不再伪装成"模型选择安静"。
3. **horizon 终点复用 STOPPED**：正常到达逻辑 horizon 的 run 落 ``STOPPED``；未到 horizon 的
   短窗口保持原 ``checkpoint``/``RUNNING`` 语义（可重启）。见 ``tests/test_fastday_runtime.py``。
4. **真实来源到候选**：World provider 的机会有效期窗口以"本轮自己的时刻"为锚（cycle 把
   ``observed_at`` 交给 ``observe(occurred_at=...)``）；活着的机会经真实 AG-0
   ``WorldOpportunityCandidateSource`` 变成候选，过期/撤销/无源仍然为 0（负控）。

所有 owner/决策路径是真实模块；只有 World 传输与"模型"是确定性替身，0 网络、0 产品调用。
根目录都在唯一 ``/tmp`` 子目录里，跑完保留。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

if os.name != "posix":  # AG-1 imports fcntl; this module is a POSIX-only acceptance
    raise unittest.SkipTest("agency_decision_ag1 needs a POSIX host (fcntl)")

from unittest import mock  # noqa: E402

from test_day_life_continuity import _Runtime  # noqa: E402  (real cycle scaffolding)
from test_one_day_life import Services, WorldGateway  # noqa: E402  (real Supply/World owners)

import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as ala  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_resident as resident  # noqa: E402
import autonomous_life_runner as alr  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_body_client as wbc  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

OBSERVED_AT = "2026-10-26T08:00:00+00:00"
SUBJECT = ag0.GLOBAL_SUBJECT_ID

#: 真实 World 夹具：有限街区包 + 服务自己的 mutation gate（与 I 包测试同一套）。
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
WORLD_GATE = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}
INSTANCE_GATES = {"REAL_WORKSPACE_PROVISION": "ON", "REAL_ARTIFACT_ACTION": "ON"}


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="nyairo-obs-supply-", dir="/tmp"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


class _RouteCoordinator:
    """A scripted Decision/Adoption answer: not a model, and it says which route it stands for."""

    def __init__(self) -> None:
        self.route = ag1.ROUTE_RULE_RESOLVED
        self.reason_codes: list[str] = [ag1.REASON_NO_LEGAL_CANDIDATE]
        self.decision = ag1.DECISION_NO_ACTION
        self.calls: list[dict] = []
        self.handoffs: list[dict] = []

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.calls.append(kwargs)
        # Same shape as the real ``evaluate_opportunity``: the route is a field of the committed
        # DecisionRecord, not a second top-level key on the result dict.
        record = SimpleNamespace(
            decision=self.decision,
            decision_route=self.route,
            selected_candidate_ref=None,
            reason_codes=list(self.reason_codes),
            short_rationale="scripted")
        adoption = SimpleNamespace(adoption_status="APPLIED", rejection_reason_code=None)
        return {"correlation_id": "corr:1",
                "decision_result": {"decision_record": record},
                "adoption_result": {"adoption_record": adoption},
                "integration_record": SimpleNamespace(activity_ref=None,
                                                      integration_id="integ:1"),
                "step_execution_result": {}}

    def emit_experience_handoff(self, **kwargs):
        self.handoffs.append(kwargs)
        return {"dispatch_result": {"deliveries": []}, "handoff_event": None}


class _ClockProvider:
    """The World observation seam: it must be told the round's own instant.

    Each call offers one place; the target rotates per call so two rounds are genuinely
    different situations (the reconsideration ledger then has nothing to suppress).
    """

    def __init__(self, *, valid_for: int = 300) -> None:
        self.valid_for = int(valid_for)
        self.seen_occurred_at: list[str] = []
        self.calls = 0
        self.last_observation: dict = {"location": {"place_id": "home"}}
        self.last_opportunity_view: list = []
        self.last_catalog = None
        self.last_observed_at = None

    def __call__(self):
        raise AssertionError("the cycle must pass the round instant, not call the provider bare")

    def observe(self, *, occurred_at=None):
        if not occurred_at:
            raise AssertionError("observe() was called without the round instant")
        self.seen_occurred_at.append(str(occurred_at))
        self.calls += 1
        start = datetime.fromisoformat(str(occurred_at))
        target = "street_%d" % self.calls
        item = {
            "opportunity_id": "wopp:move:home:r1:%s" % target,
            "perception_ref": "percept:gateway-snapshot:home:r1",
            "world_revision_ref": "wrev:world:r1",
            "target_refs": ["place:%s" % target],
            "valid_from": _iso(start),
            "valid_until": _iso(start + timedelta(seconds=self.valid_for)),
            "occurred_at": _iso(start),
            "observed_at": _iso(start),
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": SUBJECT,
            "location_ref": "place:home",
        }
        self.last_observed_at = _iso(start)
        return [item]


def _cycle(*, coordinator: _RouteCoordinator, provider: _ClockProvider,
           state_root: Path | None = None) -> alc.AutonomousLifeCycle:
    root = state_root or _root()
    runtime = _Runtime(state_root=root, coordinator=coordinator)
    cycle = alc.AutonomousLifeCycle(runtime, enabled=True, max_rounds_per_day=48, env={},
                                    artifact_port=None, work_artifact_id=None)
    cycle.world_opportunity_provider = provider
    return cycle


class ObservationCountingTest(unittest.TestCase):
    """4 real decision rounds + 4 local rule rounds == 4 model decision rounds (not 8)."""

    def _eight_rounds(self):
        provider = _ClockProvider()
        cycle = _cycle(coordinator=_RouteCoordinator(), provider=provider)
        runner = alr.AutonomousLifeRunner(cycle, state_root=cycle.state_root, env={},
                                          interval_minutes=10,
                                          clock=lambda: datetime.fromisoformat(OBSERVED_AT))
        records = []
        for index in range(8):
            cognitive = index < 4
            cycle.runtime.coordinator.route = (ag1.ROUTE_COGNITIVE_REQUIRED if cognitive
                                               else ag1.ROUTE_RULE_RESOLVED)
            cycle.runtime.coordinator.reason_codes = ([] if cognitive
                                                     else [ag1.REASON_NO_LEGAL_CANDIDATE])
            moment = datetime.fromisoformat(OBSERVED_AT) + timedelta(minutes=11 * index)
            record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="check-window",
                                 trigger_ref="evt:obs:%d" % index, now=moment)
            records.append(record)
        return records, provider

    def test_four_rule_resolved_rounds_spend_no_model_call(self):
        records, provider = self._eight_rounds()
        self.assertEqual(len(provider.seen_occurred_at), 8)
        model_rounds = sum(int(record.llm_calls) for record in records)
        self.assertEqual(model_rounds, 4, [record.to_dict() for record in records])
        rule = [r for r in records if r.extra.get("decision_route") == ag1.ROUTE_RULE_RESOLVED]
        cognitive = [r for r in records if r.extra.get("decision_route")
                     == ag1.ROUTE_COGNITIVE_REQUIRED]
        self.assertEqual((len(rule), len(cognitive)), (4, 4))
        self.assertTrue(all(record.llm_calls == 0 for record in rule))
        self.assertTrue(all(record.llm_calls == 1 for record in cognitive))

        # The rule windows name their own basis instead of looking like a quiet model choice.
        for record in rule:
            self.assertEqual(record.outcome, alc.OUTCOME_NO_ACTION)
            self.assertEqual(record.extra["reason_codes"], [ag1.REASON_NO_LEGAL_CANDIDATE])
            self.assertIsNone(record.extra["selected_candidate_ref"])
            self.assertIn("route=RULE_RESOLVED", record.detail)
            self.assertIn("reason=NO_LEGAL_CANDIDATE", record.detail)

        # The resident summary counts the same records once, and keeps the ledger as the
        # request authority: decision rounds are not re-labelled as requests.
        instance = resident.AutonomousLifeResident.__new__(resident.AutonomousLifeResident)
        instance.counters = {"wakes": 0, "by_class": {}, "outcomes": {}, "model_calls": 0,
                             "check_windows": 0, "event_wakes": 0, "real_events": 0,
                             "windows_with_material": 0, "no_action": 0, "defer": 0,
                             "blocked_reasons": {}, "actions": 0,
                             "owner_confirmed_versions": 0, "owner_version_unknown": 0,
                             "owner_version_refs": {}, "unknown_settlements": 0,
                             "reconciliations": 0, "pending_unknown_last": 0,
                             "pending_unknown_max": 0, "journey_settlements": 0,
                             "journey_arrivals": 0, "journey_arrival_refusals": 0,
                             "paused_windows": 0, "rule_resolved_windows": 0}
        instance.assembled = {"runner": SimpleNamespace(pending_unknown_actions=lambda: [])}
        for record in records:
            instance._count(record.to_dict())
        self.assertEqual(instance.counters["model_calls"], 4)
        self.assertEqual(instance.counters["rule_resolved_windows"], 4)
        self.assertEqual(instance.counters["no_action"], 8)

    def test_the_route_not_the_outcome_name_decides_the_count(self):
        """NO_ACTION / DEFER 各有规则与模型两条来源，只有 route 能区分。"""

        cases = (
            (ag1.ROUTE_COGNITIVE_REQUIRED, ag1.DECISION_NO_ACTION, 1),
            (ag1.ROUTE_COGNITIVE_REQUIRED, ag1.DECISION_DEFER, 1),
            (ag1.ROUTE_RULE_RESOLVED, ag1.DECISION_DEFER, 0),
            (ag1.ROUTE_RULE_RESOLVED, ag1.DECISION_NO_ACTION, 0),
        )
        for route, decision, expected in cases:
            with self.subTest(route=route, decision=decision):
                coordinator = _RouteCoordinator()
                coordinator.route = route
                coordinator.decision = decision
                coordinator.reason_codes = ([] if route == ag1.ROUTE_COGNITIVE_REQUIRED
                                            else [ag1.REASON_NO_LEGAL_CANDIDATE])
                cycle = _cycle(coordinator=coordinator, provider=_ClockProvider())
                out = cycle.evaluate_once(trigger_ref="evt:count", observed_at=OBSERVED_AT)
                self.assertEqual(out["decision_route"], route, out)
                self.assertEqual(out["llm_calls"], expected, out)
                if route == ag1.ROUTE_RULE_RESOLVED:
                    self.assertEqual(out["outcome"],
                                     alc.OUTCOME_NO_ACTION if decision == ag1.DECISION_NO_ACTION
                                     else alc.OUTCOME_DEFERRED, out)

    def test_a_failed_decision_is_one_attempt_not_zero(self):
        """请求失败不等于本地规则：没有 Decision 的失败路径仍是一次模型决策轮。"""

        class _FailingCoordinator(_RouteCoordinator):
            def run_decision_and_adoption_cycle(self, **kwargs):
                self.calls.append(kwargs)
                return {"correlation_id": "corr:fail",
                        "decision_result": {"failure_class": "PROVIDER_TIMEOUT"}}

        cycle = _cycle(coordinator=_FailingCoordinator(), provider=_ClockProvider())
        out = cycle.evaluate_once(trigger_ref="evt:fail", observed_at=OBSERVED_AT)
        self.assertEqual(out["outcome"], alc.OUTCOME_DECISION_FAILED, out)
        self.assertEqual(out["llm_calls"], 1, out)

    def test_the_same_event_is_not_replayed_as_a_second_round(self):
        cycle = _cycle(coordinator=_RouteCoordinator(), provider=_ClockProvider())
        moment = datetime.fromisoformat(OBSERVED_AT)
        runner = alr.AutonomousLifeRunner(cycle, state_root=cycle.state_root, env={},
                                          interval_minutes=10, clock=lambda: moment)
        event = {"kind": alr.EVENT_WORLD_OPPORTUNITY, "ref": "world:reachable:smoke"}
        first = runner.wake_on_event(dict(event), now=moment)
        second = runner.wake_on_event(dict(event), now=moment + timedelta(seconds=1))
        self.assertNotEqual(first.status, alr.WAKE_DUPLICATE_EVENT, first.to_dict())
        self.assertEqual(second.status, alr.WAKE_DUPLICATE_EVENT, second.to_dict())
        # The duplicate spent no second consideration; the cycle really ran once.
        self.assertEqual(len(cycle.runtime.coordinator.calls), 1)


class WorldClockSupplyTest(unittest.TestCase):
    """The real AG-0 World source: a live observation becomes a candidate, dead ones do not."""

    def _opportunity(self, *, start: datetime, valid_for: int = 300,
                     target: str = "street_1") -> dict:
        return {
            "opportunity_id": "wopp:move:home:r1:%s" % target,
            "perception_ref": "percept:gateway-snapshot:home:r1",
            "world_revision_ref": "wrev:world:r1",
            "target_refs": ["place:%s" % target],
            "valid_from": _iso(start),
            "valid_until": _iso(start + timedelta(seconds=valid_for)),
            "occurred_at": _iso(start),
            "observed_at": _iso(start),
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": SUBJECT,
            "location_ref": "place:home",
        }

    def test_the_cycle_stamps_the_observation_with_the_round_instant(self):
        cycle = _cycle(coordinator=_RouteCoordinator(), provider=_ClockProvider())
        out = cycle.evaluate_once(trigger_ref="evt:supply:1", observed_at=OBSERVED_AT)
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION)
        self.assertEqual(cycle.world_opportunity_provider.seen_occurred_at, [OBSERVED_AT])
        self.assertEqual(cycle.last_world_opportunities[0]["valid_from"], OBSERVED_AT)

    def test_a_live_observation_becomes_one_candidate(self):
        source = ag0.WorldOpportunityCandidateSource()
        start = datetime.fromisoformat(OBSERVED_AT)
        source.record_observed_opportunity(self._opportunity(start=start))
        candidates, snapshot_ref, meta = source.materialize(observed_at=OBSERVED_AT)
        self.assertEqual(len(candidates), 1, meta)
        self.assertEqual(candidates[0].target_refs, ["place:street_1"])
        self.assertTrue(snapshot_ref.startswith("snap:WORLD_OPPORTUNITY"))

    def test_an_expired_window_is_still_refused(self):
        source = ag0.WorldOpportunityCandidateSource()
        start = datetime.fromisoformat(OBSERVED_AT)
        source.record_observed_opportunity(self._opportunity(start=start, valid_for=60))
        later = _iso(start + timedelta(minutes=10))
        candidates, _snapshot, meta = source.materialize(observed_at=later)
        self.assertEqual(candidates, [])
        self.assertEqual(meta["discovered"], 0)

    def test_a_withdrawn_or_missing_opportunity_is_refused(self):
        source = ag0.WorldOpportunityCandidateSource()
        start = datetime.fromisoformat(OBSERVED_AT)
        item = self._opportunity(start=start)
        source.record_observed_opportunity(item)
        source.expire_opportunity(item["opportunity_id"], observed_at=OBSERVED_AT)
        candidates, _snapshot, meta = source.materialize(observed_at=OBSERVED_AT)
        self.assertEqual(candidates, [], meta)

    def test_no_source_is_still_no_legal_candidate(self):
        source = ag0.WorldOpportunityCandidateSource()
        candidates, _snapshot, _meta = source.materialize(observed_at=OBSERVED_AT)
        self.assertEqual(candidates, [])
        router = ag1.DecisionPolicyRouter()
        opportunity = ag1.DecisionOpportunity.create(
            trigger_kind=ag1.TRIGGER_EXPLICIT_REEVALUATION,
            trigger_ref="evt:supply:none",
            candidate_set=ag0.CandidateSet(
                candidate_set_id="cset:empty", subject_id=SUBJECT, candidate_refs=[],
                source_snapshot_refs=[], observed_at=OBSERVED_AT, built_at=OBSERVED_AT),
            observed_at=OBSERVED_AT)
        capacity = ag1.AgencyCapacitySnapshot(
            capacity_snapshot_id="cap:test", subject_id=SUBJECT,
            captured_at=OBSERVED_AT, source_revisions=["ctx:test"],
            body_capacity_state=ag1.CAPACITY_AVAILABLE,
            resource_availability_state=ag1.CAPACITY_AVAILABLE,
            runtime_load_state=ag1.CAPACITY_AVAILABLE,
            privacy_availability_state=ag1.CAPACITY_UNKNOWN,
            device_availability_state=ag1.CAPACITY_UNKNOWN,
            overall_capacity_state=ag1.CAPACITY_AVAILABLE,
            interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE)
        context = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:empty", subject_id=SUBJECT,
            opportunity_ref=opportunity.opportunity_id,
            candidate_set_ref="cset:empty", life_frame_ref="lframe:test",
            captured_at=OBSERVED_AT, source_revisions={},
            snapshot_fingerprint="sfp:test")
        routed = router.route_opportunity(
            opportunity=opportunity, allowed_candidates=[],
            context_snapshot=context, capacity_snapshot=capacity)
        self.assertEqual(routed["route"], ag1.ROUTE_RULE_RESOLVED)
        self.assertEqual(routed["rule_output"]["decision"], ag1.DECISION_NO_ACTION)
        self.assertEqual(routed["rule_output"]["reason_codes"],
                         [ag1.REASON_NO_LEGAL_CANDIDATE])


class RealWorldOwnerChainTest(unittest.TestCase):
    """真实 World owner → 正式 provider/assembly 时钟 → 真实 cycle → 真实 AG-0/registry 读口。

    这里不手造机会、不 monkeypatch 候选源：夹具是真实 ``WorldBodyService``（自己的
    gateway socket、自己的 world home、真实世界包 ``daily_neighborhood_v1``）与真实
    ``LifeSupplyService``；观测走 ``WorldMoveOpportunityProvider`` 自己的
    ``WorldBodyClient`` 读，机会由它翻译；候选由真实 ``WorldOpportunityCandidateSource``
    记录并经生产 runtime 的 ``CandidateReadService`` 读回。模型完全不参与。

    时钟场景与 b 同构：``logical_anchor`` 在 ``prepare`` **之前**取，所以准备结束、
    首轮刷新时业务时刻已经落后真实墙钟数分钟。修复前 provider 按墙钟给机会盖有效期，
    AG-0 在业务时刻读到 ``OPPORTUNITY_NOT_YET_OPEN``；修复后 cycle 把本轮业务时刻交给
    ``observe(occurred_at=...)``、assembly 把业务钟注入 provider，同一场景得到合法候选。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._gate = mock.patch.dict(os.environ, WORLD_GATE)
        cls._gate.start()
        cls.root = _root()
        # 秒级精度：provider 自己的口径就是秒（与 b 的 ISO 记录一致），比较时不引入
        # 微秒噪声 —— 断言的仍是两个时刻是不是同一个域，而不是打印出来的非零值。
        cls.logical_anchor = (datetime.now(timezone.utc) - timedelta(minutes=5)
                              ).replace(microsecond=0)
        cls.services = Services(cls.root / "supply")
        try:
            with mock.patch.dict(os.environ, INSTANCE_GATES):
                odl.prepare(instance=cls.root / "instance",
                            supply_socket=cls.services.supply_path,
                            governance_socket=cls.services.gov_path,
                            subject="test:obs-real-world", title="真实 World 观测夹具",
                            world_package=WORLD_PACKAGE)
            cls.data = odl.read_instance(cls.root / "instance")
            cls.instance = cls.root / "instance"
            cls.world_home = Path(str(cls.data["world_home"]))
            env = odl.instance_environment(cls.instance)
            cls.gateway = WorldGateway(cls.world_home, world_id=cls.data["world_id"],
                                       environment=env)
            cls.env = odl.instance_environment(cls.instance)
            cls.env.update(cls.gateway.environment())
            switches = ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=True, proactive_enabled=False)
            cls.runtime = lrp.build_production_life_runtime(
                odl.life_state_root(cls.instance), kill_switches=switches,
                install_root=cls.instance / "life")
            cls.cycle = alc.AutonomousLifeCycle(
                cls.runtime, enabled=True, max_rounds_per_day=48, env=cls.env,
                world_home=str(cls.world_home), clock=lambda: cls.logical_anchor)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        for step in (getattr(cls, "runtime", None),):
            if step is not None:
                try:
                    step.close()
                except Exception:
                    pass
        for step in (getattr(cls, "gateway", None), getattr(cls, "services", None)):
            if step is not None:
                try:
                    step.stop()
                except Exception:
                    pass
        if getattr(cls, "_gate", None) is not None:
            cls._gate.stop()

    # -- helpers -------------------------------------------------------------

    def provider(self, *, clock=None):
        resolved = ala.resolve_world_provider(self.env, clock=clock)
        self.assertEqual(resolved["status"], "READY", resolved)
        return resolved["provider"]

    def refresh(self, when: datetime, *, provider=None):
        self.cycle.world_opportunity_provider = provider or self.provider(clock=lambda: when)
        return self.cycle._refresh_world_opportunities(observed_at=when.isoformat())

    def world_candidates(self, when: datetime) -> list[dict]:
        # 生产 runtime 的候选读口就是 AG-1/AG-2 每轮用的那个：先按本轮时刻材料化，
        # 再列出真实 OPEN 候选；不从测试缓存里拼。
        self.runtime.candidate_read.build_candidate_set(observed_at=when.isoformat())
        return [c for c in self.runtime.candidate_read.list_open_candidates(
                    observed_at=when.isoformat())
                if c["candidate_kind"] == ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY]

    # -- owner / assembly -----------------------------------------------------

    def test_the_ready_owner_builds_the_production_provider_with_the_round_clock(self):
        resolved = ala.resolve_world_provider(self.env, clock=lambda: self.logical_anchor)
        self.assertEqual(resolved["status"], "READY", resolved)
        self.assertEqual(resolved["reason"], "")
        provider = resolved["provider"]
        self.assertIsInstance(provider, wmos.WorldMoveOpportunityProvider)
        status = provider.client.get_status()
        self.assertEqual(status.outcome, wbc.OK)
        self.assertTrue((status.data or {}).get("ready"))
        self.assertEqual(str(resolved["bridge_dir"]),
                         str(ROOT / "components" / "world" / "service" / "bridge"))

        # 没有 World 配置时，assembly 不造替身 provider，而是具名拒绝。
        missing = ala.resolve_world_provider({})
        self.assertEqual(missing["status"], "UNAVAILABLE")
        self.assertIsNone(missing["provider"])
        self.assertEqual(missing["reason"], "WORLD_NOT_CONFIGURED")

    def test_first_round_behind_the_wall_clock_is_not_refused(self):
        when = self.logical_anchor
        real_before = datetime.now(timezone.utc)
        items = self.refresh(when)
        real_after = datetime.now(timezone.utc)
        # b 的现场：准备结束、首轮刷新时业务时刻已落后墙钟数分钟。
        self.assertGreaterEqual((real_before - when).total_seconds(), 300)

        self.assertTrue(items, "真实 World 观察必须至少给出一个机会")
        targets = {ref for item in items for ref in item["target_refs"]}
        self.assertIn("place:street_front", targets, targets)

        source = self.runtime.world_opportunity_source
        for item in items:
            ok, reason = source.validate_source(dict(item), observed_at=when.isoformat())
            self.assertTrue(ok, (item["opportunity_id"], reason))
            # valid_from / valid_until / observed_at 与本轮时刻同域。
            self.assertEqual(_utc(item["valid_from"]), _utc(when))
            self.assertEqual(_utc(item["observed_at"]), _utc(when))
            self.assertEqual(_utc(item["valid_until"]), _utc(when) + timedelta(seconds=300))
            # 而且确实不是墙钟：本轮时刻比观察时刻早。
            self.assertLess(_utc(item["valid_from"]), _utc(real_after))
        self.assertEqual(_utc(self.cycle.world_opportunity_provider.last_observed_at),
                         _utc(when))
        self.assertEqual(_utc(self.cycle.last_world_observed_at), _utc(when))

        candidates = self.world_candidates(when)
        self.assertTrue(any("place:street_front" in c["target_refs"] for c in candidates),
                        candidates)
        cset = self.runtime.candidate_read.build_candidate_set(observed_at=when.isoformat())
        self.assertTrue(cset["candidate_refs"])

    def test_the_old_real_wall_stamp_is_still_refused_in_the_same_scenario(self):
        when = self.logical_anchor
        # 历史调用形态：cycle 裸调 provider，provider 没有业务钟，只能按墙钟盖章。
        # 这里不改业务源码，只用一个明确“使用真实盖章”的测试替身包住**同一个真实
        # provider**，丢掉的只有 occurred_at。
        legacy = _LegacyRealStampProvider(self.provider())
        items = legacy.observe(occurred_at=when.isoformat())
        self.assertTrue(items)

        source = ag0.WorldOpportunityCandidateSource()
        for item in items:
            source.record_observed_opportunity(item)
        reasons = [source.validate_source(dict(item), observed_at=when.isoformat())[1]
                   for item in items]
        self.assertEqual(set(reasons), {"OPPORTUNITY_NOT_YET_OPEN"}, reasons)
        candidates, _snapshot, meta = source.materialize(observed_at=when.isoformat())
        self.assertEqual(candidates, [])
        self.assertEqual(meta["discovered"], 0)
        # 旧窗口确实在另一个时刻域：它不是本轮时刻，而是观察时的墙钟。
        self.assertNotEqual(_utc(items[0]["valid_from"]), _utc(when))

    def test_a_round_past_real_plus_300s_still_gets_a_live_candidate(self):
        real = datetime.now(timezone.utc)
        when = (real + timedelta(seconds=400)).replace(microsecond=0)  # 快进超过真实+300 秒
        items = self.refresh(when)
        targets = {ref for item in items for ref in item["target_refs"]}
        self.assertIn("place:street_front", targets, targets)
        for item in items:
            self.assertEqual(_utc(item["valid_from"]), _utc(when))
            self.assertEqual(_utc(item["valid_until"]), _utc(when) + timedelta(seconds=300))
        candidates = self.world_candidates(when)
        self.assertTrue(any("place:street_front" in c["target_refs"] for c in candidates),
                        candidates)

        # 同一业务时刻，旧的真实盖章窗口已经过期 —— 这正是 b 后半段的 EXPIRED。
        legacy = _LegacyRealStampProvider(self.provider())
        legacy_items = legacy.observe(occurred_at=when.isoformat())
        source = ag0.WorldOpportunityCandidateSource()
        for item in legacy_items:
            source.record_observed_opportunity(item)
        reasons = [source.validate_source(dict(item), observed_at=when.isoformat())[1]
                   for item in legacy_items]
        self.assertEqual(set(reasons), {"OPPORTUNITY_WINDOW_EXPIRED"}, reasons)
        self.assertEqual(source.materialize(observed_at=when.isoformat())[0], [])

    def test_the_same_observation_is_not_replayed_as_duplicate_candidates(self):
        when = self.logical_anchor + timedelta(minutes=1)
        first = self.refresh(when)
        first_ids = sorted(c["candidate_id"] for c in self.world_candidates(when))
        second = self.refresh(when)
        second_ids = sorted(c["candidate_id"] for c in self.world_candidates(when))
        self.assertGreaterEqual(len(first_ids), 1)
        self.assertEqual(first_ids, second_ids)
        self.assertEqual([item["opportunity_id"] for item in first],
                         [item["opportunity_id"] for item in second])

    def test_a_withdrawn_real_opportunity_leaves_no_candidate(self):
        when = self.logical_anchor + timedelta(minutes=2)
        items = self.refresh(when)
        move = next(item for item in items
                    if "place:street_front" in item["target_refs"])
        self.runtime.world_opportunity_source.expire_opportunity(
            move["opportunity_id"], observed_at=when.isoformat())
        candidates = self.world_candidates(when)
        self.assertFalse(any("place:street_front" in c["target_refs"] for c in candidates),
                         candidates)


class _LegacyRealStampProvider:
    """修复前的调用形态：cycle 裸调 provider，provider 按真实墙钟盖有效期。

    它只丢 ``occurred_at``，其余每一次读都委托给**真实** ``WorldMoveOpportunityProvider``；
    机会本身不是手造的。用于负控证明旧路径在同一场景仍会被 AG-0 拒绝。
    """

    def __init__(self, provider) -> None:
        self.provider = provider

    def __call__(self) -> list[dict]:
        return list(self.provider.observe() or [])

    def observe(self, *, occurred_at=None) -> list[dict]:
        return self.__call__()


class ResidentDetailSemanticsTest(unittest.TestCase):
    """The summary separates decision rounds, rule windows and the ledger request total."""

    def test_detail_does_not_relabel_rounds_as_requests(self):
        instance = resident.AutonomousLifeResident.__new__(resident.AutonomousLifeResident)
        instance.counters = {"model_calls": 4, "rule_resolved_windows": 4}
        instance.assembled = {"cycle": SimpleNamespace(
            budget=SimpleNamespace(requests=None),
            writer=None, reading_response_writer=None)}
        detail = instance._model_calls_detail()
        self.assertEqual(detail["decision_rounds"], 4)
        self.assertEqual(detail["rule_resolved_windows"], 4)
        self.assertIn("total_requests", detail)
        self.assertIsNone(detail["total_requests"])
        self.assertIn("count_semantics", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
