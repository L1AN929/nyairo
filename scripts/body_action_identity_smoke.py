#!/usr/bin/env python3
"""零模型冒烟：``bodyact:`` 的动作身份属于已提交的 Decision，而不是观测坐标。

两部分，边界分开写清
====================
A. **正式生产链（gate=off）**：全新 ``/tmp`` 根里用真实 Supply owner、真实 World/Body
   gateway 和 ``build_production_life_runtime`` + ``assemble_autonomous_life`` 装配 Life，
   Decision 与内容是脚本替身但每一轮都走正式 ``cycle.evaluate_once``：

   1. 第一轮脚本按正式 AG-1 合法集 START 一个 ``bodyact:pose:`` 候选；gate off 让提交在
      pre-submit 明确被拒（``STEP_REFUSED`` / ``PRE_SUBMIT_FAILED`` / ``GATE_OFF``）。
   2. 同一个已提交 Decision 在正式 AG-2 同命令层再执行一次：仍是同一个 step / 同一个
      Action，只多一次 attempt，没有新 Action、没有回执。
   3. 脚本用现有合法 ``ABANDON`` 出口结束该 Activity；随后同一 World 状态（revision 不变、
      同一 bodyact 目标）下，新的 Decision 再 START 一次：必须是新的 step / 新的 Action，
      各自绑定自己的 Activity / Decision / 授权，旧 Action 的 attempt 不再增加。

B. **隔离 AR-0 + 真实 World owner（可控丢答）**：不经 Life 的 pre-submit gate（这是一层
   单独的边界，不在本冒烟主张内），用真实 WorldBodyService/gateway 与真实 AR-0 ledger：

   4. 同一 Decision 的 POSE 已被 owner 执行但回执丢失 → AR-0 ``UNKNOWN``；观测随后变化，
      同一 Decision 重建的 key 不变，裸重试被 AR-0 拒绝，``reconcile_action`` 由**原
      execution** 对账成 SUCCEEDED，owner 不再执行第二次。
   5. 另一个 Decision 选同一 target/同一 place/revision：key 与 Action 都不同，旧的成功
      回执不会跨到它身上（提交的是它自己的执行，不是第一次的 replay）。
   6. 第三个 Decision 选另一个 POSE 真正改变基底，说明新 Decision 有独立的执行。

不做什么
========
不调用产品模型、不起 HTTP/浏览器、不读写任何既有实例、不改全局默认 gate（只在脚本自己的
进程环境里置 off 并在 finally 恢复）、不删数据、不迁移或重放历史 l/k。Decision/内容是替身；
真实 owner 是 Supply 与 World（A 里是 gateway 机会源，B 里是真实 WorldBodyService）。临时根保留。

退出码：0 = 全部断言成立；1 = 任一断言失败或服务起不来。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_intent  # noqa: E402
import action_reality_ledger as arl  # noqa: E402
import activity_continuity as ac  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_body_action_targets as body_actions  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_action_bridge_adapter as wba  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    JourneyWorldResolver, RealJourneyWorldActionAdapter)

from tests.test_day_life_body_actions import _ChainHarness  # noqa: E402
from tests.test_one_day_life import Services, StubWriter, WorldGateway  # noqa: E402
from tests.test_world_daily_life_continuous import (  # noqa: E402
    WORLD_PACKAGE, Clock, episode_instant)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
#: The prepared fixture starts standing / facing south; the World opportunity therefore
#: offers ``seated:south`` and ``lying:south`` as real alternatives to the current pose.
TARGET = "bodyact:pose:seated:south"
SECOND_TARGET = "bodyact:pose:lying:south"
SCRATCH = Path(os.environ.get("NYAIRO_BODY_IDENTITY_SMOKE_ROOT")
               or "/tmp/nyairo-body-action-identity-smoke")


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("SMOKE_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:6000])
    raise SystemExit(1)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class _ScriptedDecisionModel:
    """Decision double: only selects inside the real AG-1 legal set, returns raw JSON."""

    model_id = "stub:body-action-identity-smoke"
    model_version = "body-action-identity-smoke-v1"

    def __init__(self) -> None:
        self.policy = "start_body"
        self.calls = 0
        self.requests: list[dict] = []

    @staticmethod
    def _targets(row) -> list[str]:
        return [str(ref) for ref in (row.get("target_refs") or [])]

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        self.requests.append({
            "policy": self.policy,
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "target_refs": self._targets(row),
                            "legal_decisions": list(row.get("legal_decisions") or [])}
                           for row in candidates],
        })
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
               "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "stub: no action",
               "deferred_candidate_ids": []}
        if self.policy == "start_body":
            matches = [row for row in candidates
                       if "START" in (row.get("legal_decisions") or [])
                       and TARGET in self._targets(row)]
            if matches:
                raw = {"decision": "START",
                       "selected_candidate_id": matches[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: the same body target",
                       "deferred_candidate_ids": []}
        elif self.policy == "abandon_body":
            matches = [row for row in candidates
                       if "ABANDON" in (row.get("legal_decisions") or [])
                       and row.get("candidate_kind") == ag0.CANDIDATE_KIND_CONTINUE_CURRENT]
            if matches:
                raw = {"decision": "ABANDON",
                       "selected_candidate_id": matches[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: close this Activity through its own exit",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:body-action-identity-smoke:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def _canonical(runtime) -> dict:
    state, _journal = runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _actions(runtime) -> list[dict]:
    return [dict(row) for row in runtime.action_read.list_actions()]


def _attempts(runtime, action_id: str) -> list[dict]:
    return [dict(row) for row in runtime.action_read.list_attempts(action_id)]


def _receipts(runtime, action_id: str) -> list[dict]:
    return [dict(row) for row in runtime.action_read.list_receipts(action_id)]


def _steps(runtime) -> list[dict]:
    state = runtime.integration_read.read_state()
    return [dict(row) for row in (state.get("step_specs_by_id") or {}).values()]


def _integration_for(runtime, decision_ref: str) -> dict:
    state = runtime.integration_read.read_state()
    for row in (state.get("integrations_by_id") or {}).values():
        if str(row.get("decision_ref") or "") == decision_ref:
            return dict(row)
    raise SystemExit("SMOKE_FAIL: no integration record for %s" % decision_ref)


def _envelope(out: dict) -> dict:
    return {key: out.get(key) for key in (
        "outcome", "decision", "decision_route", "adoption_status",
        "selected_candidate_ref", "activity_ref", "action_id", "action_status",
        "detail", "error")}


class _DroppedResponseResolver:
    """The real World bridge, with exactly one answer lost on the way back to AR-0."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.dropped = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def resolve_action(self, **kwargs):
        result = self.inner.resolve_action(**kwargs)
        if not self.dropped and result.get("status") == "committed":
            self.dropped = True
            return dict(result, status="uncertain", code="RESPONSE_LOST",
                        transport_result=arl.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                        detail="the client lost the owner's answer")
        return result


# ---------------------------------------------------------------------------
# A. formal production chain: two STARTs, same target, same World state
# ---------------------------------------------------------------------------


def _formal_dual_start(run_root: Path, summary: dict) -> None:
    saved_env = {GATE_ENV: os.environ.get(GATE_ENV)}
    services = Services(run_root / "services")
    gateway = None
    runtime = None
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:body-identity",
            title="Body action Decision identity smoke",
            seed_content="验收夹：这一句只用于准备一个真实作品。\n",
            world_package=WORLD_PACKAGE)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=odl.instance_environment(instance))
        env = odl.instance_environment(instance)
        env.update(gateway.environment())
        env[GATE_ENV] = "off"
        os.environ[GATE_ENV] = "off"
        state_root = odl.life_state_root(instance)
        client = WorldBodyClient(environment=env)
        clock = Clock(episode_instant(after=datetime.now(timezone.utc)))
        provider = wmos.WorldMoveOpportunityProvider(
            client=client, action_intent=action_intent, bridge_dir=str(BRIDGE_DIR),
            environment=env, world_home=str(world_home),
            now=lambda: clock.now, observed_now=lambda: clock.now, valid_for_seconds=60)
        cognition = _ScriptedDecisionModel()
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=provider, writer=StubWriter(), enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        cycle.world_home = str(world_home)
        cycle.work_artifact_id = str(data.get("work_artifact_id") or "")
        cycle.reading_artifact_id = str(data.get("reading_artifact_id") or "")

        # -- round 1: committed Decision A STARTs the body target -----------------
        first = cycle.evaluate_once(trigger_ref="evt:body-identity:1",
                                    observed_at=clock.iso(), act=True)
        _check(first.get("decision") == "START" and first.get("adoption_status") == "APPLIED",
               "round 1 did not START the body candidate",
               {"envelope": _envelope(first), "requests": cognition.requests[-1:]})
        _check(first.get("outcome") == alc.OUTCOME_STEP_REFUSED,
               "round 1 was not the definite pre-submit refusal", _envelope(first))
        activity_a = str(first.get("activity_ref") or "")
        action_a_id = str(first.get("action_id") or "")
        _check(bool(activity_a and action_a_id), "round 1 named no Activity/Action",
               _envelope(first))
        action_a = runtime.action_read.get_action(action_a_id)
        decision_a = str(action_a.get("decision_ref") or "")
        _check(bool(decision_a), "Action A carries no decision_ref", action_a)
        _check(action_a.get("activity_ref") == activity_a,
               "Action A is not bound to Activity A", action_a)
        _check(action_a.get("target_ref") == TARGET, "Action A target changed", action_a)
        attempts_a = _attempts(runtime, action_a_id)
        _check(len(attempts_a) == 1 and attempts_a[0].get("status") == "PRE_SUBMIT_FAILED"
               and attempts_a[0].get("error_class") == "GATE_OFF",
               "round 1 attempt is not one GATE_OFF pre-submit failure", attempts_a)
        steps_after_first = _steps(runtime)
        step_a = next((row for row in steps_after_first
                       if row.get("decision_ref") == decision_a
                       and row.get("activity_ref") == activity_a), None)
        _check(step_a is not None, "no step bound to Decision A / Activity A", steps_after_first)
        _check(TARGET in str(step_a.get("idempotency_key") or "")
               and decision_a.replace(":", "%3A") in str(step_a.get("idempotency_key") or ""),
               "the step key does not name the committed Decision", step_a)

        # -- same Decision, same formal AG-2 command, one execution --------------
        decision_obj = ag1.DecisionRecord.from_dict(
            runtime.decision_read.get_decision(decision_a))
        retry_step = cycle._world_step_spec(decision_obj)
        _check(retry_step["idempotency_key"] == step_a["idempotency_key"],
               "the same Decision rebuilt a different key", retry_step)
        integration = _integration_for(runtime, decision_a)
        retried = runtime.coordinator.execute_activity_step(
            integration_id=str(integration["integration_id"]),
            activity_id=activity_a, decision_ref=decision_a,
            adoption_ref=str(integration["adoption_ref"]),
            step_spec_input=retry_step, settle_immediately=True)
        retried_action = dict(((retried or {}).get("bridge_result") or {}).get("action") or {})
        _check(retried_action.get("action_id") == action_a_id,
               "the same Decision retry minted a different Action", retried)
        _check(len(_actions(runtime)) == 1,
               "the same Decision retry created a second Action", _actions(runtime))
        attempts_after_retry = _attempts(runtime, action_a_id)
        _check(len(attempts_after_retry) == 2,
               "the same Decision retry did not stay on the same execution",
               attempts_after_retry)
        _check(_receipts(runtime, action_a_id) == [],
               "the refused retry produced a receipt", _receipts(runtime, action_a_id))
        _check(_activity(runtime, activity_a).get("status") == "ACTIVE",
               "the retry changed Activity A", _activity(runtime, activity_a))

        # -- round 2: the existing legal ABANDON exit closes Activity A ----------
        cognition.policy = "abandon_body"
        second = cycle.evaluate_once(trigger_ref="evt:body-identity:2",
                                     observed_at=clock.iso(), act=True)
        _check(second.get("decision") == "ABANDON"
               and second.get("adoption_status") == "APPLIED",
               "round 2 did not adopt the existing ABANDON exit", _envelope(second))
        closed = _activity(runtime, activity_a)
        _check(closed.get("status") == "ABANDONED",
               "Activity A is not ABANDONED after its own exit", closed)
        _check(_canonical(runtime).get("foreground_activity_ref") is None,
               "the foreground was not released by ABANDON", _canonical(runtime))

        # -- round 3: a new Decision, same target and same World revision --------
        world_revision_before = (client.get_snapshot().data or {}).get("world_revision")
        cognition.policy = "start_body"
        third = cycle.evaluate_once(trigger_ref="evt:body-identity:3",
                                    observed_at=clock.iso(), act=True)
        _check(third.get("decision") == "START" and third.get("adoption_status") == "APPLIED",
               "round 3 did not START the same body target again",
               {"envelope": _envelope(third), "requests": cognition.requests[-1:]})
        activity_b = str(third.get("activity_ref") or "")
        action_b_id = str(third.get("action_id") or "")
        _check(bool(activity_b and action_b_id) and activity_b != activity_a,
               "round 3 did not create a second Activity", _envelope(third))
        _check(action_b_id != action_a_id,
               "the new Decision inherited the first Action",
               {"action_a": action_a_id, "action_b": action_b_id})
        action_b = runtime.action_read.get_action(action_b_id)
        decision_b = str(action_b.get("decision_ref") or "")
        _check(bool(decision_b) and decision_b != decision_a,
               "Action B is not bound to its own Decision",
               {"decision_a": decision_a, "decision_b": decision_b})
        _check(action_b.get("activity_ref") == activity_b,
               "Action B is not bound to Activity B", action_b)
        _check(_attempts(runtime, action_a_id) == attempts_after_retry,
               "Action A gained an attempt from the second Decision",
               _attempts(runtime, action_a_id))
        attempts_b = _attempts(runtime, action_b_id)
        _check(len(attempts_b) == 1 and attempts_b[0].get("error_class") == "GATE_OFF",
               "Action B did not get its own single refused attempt", attempts_b)
        _check(_receipts(runtime, action_b_id) == [],
               "Action B inherited or produced a receipt", _receipts(runtime, action_b_id))
        step_b = next((row for row in _steps(runtime)
                       if row.get("decision_ref") == decision_b
                       and row.get("activity_ref") == activity_b), None)
        _check(step_b is not None, "no step bound to Decision B / Activity B", _steps(runtime))
        _check(step_b.get("step_spec_id") != step_a.get("step_spec_id"),
               "the two Decisions share one step identity",
               {"a": step_a.get("step_spec_id"), "b": step_b.get("step_spec_id")})
        _check(step_b.get("adoption_ref") != step_a.get("adoption_ref"),
               "the two Decisions share one adoption/authorization reference",
               {"a": step_a.get("adoption_ref"), "b": step_b.get("adoption_ref")})
        world_revision_after = (client.get_snapshot().data or {}).get("world_revision")
        _check(world_revision_after == world_revision_before,
               "the World state moved between the two Decisions",
               {"before": world_revision_before, "after": world_revision_after})

        summary["formal"] = {
            "decision_a": decision_a, "decision_b": decision_b,
            "activity_a": activity_a, "activity_b": activity_b,
            "action_a": action_a_id, "action_b": action_b_id,
            "step_a": step_a.get("step_spec_id"), "step_b": step_b.get("step_spec_id"),
            "attempts_a": len(attempts_after_retry), "attempts_b": len(attempts_b),
            "world_revision": world_revision_before,
            "outcome": first.get("outcome"),
        }
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        if gateway is not None:
            try:
                gateway.stop()
            except Exception:
                pass
        try:
            services.stop()
        except Exception:
            pass
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# ---------------------------------------------------------------------------
# B. isolated AR-0 + real World owner: UNKNOWN and success identity
# ---------------------------------------------------------------------------


def _isolated_unknown_and_success(run_root: Path, summary: dict) -> None:
    harness = _ChainHarness()
    lease = None
    try:
        socket_path = harness.start()
        real = harness.bridge(socket_path)
        lost = _DroppedResponseResolver(real)
        store_root = run_root / "identity-ar0"
        lease = ac.ActivityAuthorityLease(store_root)
        lease.acquire()
        store = arl.ActionStore(store_root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        capability = arl.ActionRealityAuthority.issue_writer_capability(
            lease, namespace=ac.NAMESPACE_ISOLATED_TEST)
        commands = arl.ActionCommandService(
            store=store, lease=lease, capability=capability)
        adapter = wba.RealWorldActionAdapter(lost)
        commands.register_adapter(arl.ACTION_KIND_WORLD, adapter)
        executor = arl.ActionRealityAuthority.issue_sandbox_executor_capability(
            namespace=ac.NAMESPACE_ISOLATED_TEST)

        def _step(decision_id: str, target: str) -> dict:
            substrate = harness.substrate()
            return body_actions.build_body_action_step(
                target, place="home", revision=substrate.get("revision"),
                decision_ref=decision_id)

        def _propose(step: dict, decision_id: str) -> str:
            proposal = commands.propose_action(
                action_kind=arl.ACTION_KIND_WORLD,
                target_ref=step["target_ref"],
                payload_ref="payload:body-identity-smoke",
                payload_data=step["payload_spec"],
                idempotency_key=step["idempotency_key"],
                source_refs=["smoke:body-action-identity"],
                decision_ref=decision_id)
            action_id = str(proposal["action"]["action_id"])
            commands.prepare_action(action_id=action_id)
            return action_id

        def _submit(action_id: str) -> dict:
            return commands.submit_action(action_id=action_id,
                                          executor_capability=executor)

        unknown_decision = "dec:bodyidentunknown01"
        step_u1 = _step(unknown_decision, TARGET)
        action_u1 = _propose(step_u1, unknown_decision)
        outcome = _submit(action_u1)
        _check(dict(outcome.get("action") or {}).get("status") == arl.ACTION_STATUS_UNKNOWN,
               "the lost answer did not leave the Action UNKNOWN", outcome)
        _check(adapter.submit_call_count == 1,
               "the owner was contacted more than once for one submission",
               adapter.submit_call_count)
        after_unknown = harness.substrate()
        _check((after_unknown.get("pose"), after_unknown.get("orientation"))
               == ("seated", "south"),
               "the real owner did not execute the POSE", after_unknown)

        # the observation moved on; the same Decision must still name one execution
        step_u1_again = _step(unknown_decision, TARGET)
        _check(step_u1_again["idempotency_key"] == step_u1["idempotency_key"],
               "the same Decision rebuilt a different key after the observation moved",
               step_u1_again)
        other_decision = "dec:bodyidentother001"
        step_u2 = _step(other_decision, TARGET)
        _check(step_u2["idempotency_key"] != step_u1["idempotency_key"],
               "a different Decision reused the first key", step_u2)

        # a bare retry of the unresolved execution is refused; reconciliation uses it
        with_assert = None
        try:
            commands.submit_action(action_id=action_u1, executor_capability=executor)
        except arl.UnknownActionRetryForbiddenError as error:
            with_assert = type(error).__name__
        _check(with_assert == "UnknownActionRetryForbiddenError",
               "a bare retry of the UNKNOWN execution was not refused", with_assert)
        revision_before_reconcile = harness.substrate().get("revision")
        resolved = commands.reconcile_action(action_id=action_u1)
        _check(dict(resolved.get("action") or {}).get("status") == "SUCCEEDED",
               "the original execution did not reconcile to SUCCEEDED", resolved)
        _check(dict(resolved.get("reconciliation") or {}).get("result")
               == arl.RECON_RESULT_CONFIRMED_SUCCEEDED,
               "reconciliation did not confirm the original execution", resolved)
        _check(harness.substrate().get("revision") == revision_before_reconcile,
               "reconciliation executed the POSE again", harness.substrate())
        _check(adapter.submit_call_count == 1,
               "reconciliation re-submitted to the owner", adapter.submit_call_count)

        # the second Decision selects the identical target: it must not inherit the
        # first execution's success receipt.
        action_u2 = _propose(step_u2, other_decision)
        _check(action_u2 != action_u1, "the second Decision reused the first Action",
               {"first": action_u1, "second": action_u2})
        outcome_u2 = _submit(action_u2)
        _check(dict(outcome_u2.get("action") or {}).get("action_id") == action_u2,
               "the second submission was answered by another Action", outcome_u2)
        _check(not bool(outcome_u2.get("idempotent_replay")),
               "the second Decision was replayed from the first success", outcome_u2)
        _check(dict(outcome_u2.get("action") or {}).get("status")
               == arl.ACTION_STATUS_SUCCEEDED,
               "the second Decision's own execution did not complete", outcome_u2)

        # a third Decision changes the substrate for real: its execution is its own.
        third_decision = "dec:bodyidentthird001"
        step_u3 = _step(third_decision, SECOND_TARGET)
        action_u3 = _propose(step_u3, third_decision)
        outcome_u3 = _submit(action_u3)
        _check(dict(outcome_u3.get("action") or {}).get("status")
               == arl.ACTION_STATUS_SUCCEEDED,
               "the third Decision's execution did not complete", outcome_u3)
        final = harness.substrate()
        _check((final.get("pose"), final.get("orientation")) == ("lying", "south"),
               "the third Decision did not really change the substrate", final)
        _check(len({action_u1, action_u2, action_u3}) == 3,
               "the three Decisions do not have three distinct Actions",
               [action_u1, action_u2, action_u3])

        summary["isolated"] = {
            "unknown_action": action_u1,
            "second_action": action_u2,
            "third_action": action_u3,
            "unknown_reconciled": "SUCCEEDED",
            "submit_calls": adapter.submit_call_count,
            "final_pose": [final.get("pose"), final.get("orientation")],
        }
    finally:
        if lease is not None and lease.held:
            try:
                lease.release()
            except Exception:
                pass
        # Stop this script's own World owner (and its lease) before restoring the env;
        # the fixture files stay on disk, only the sockets/threads go away.
        if harness.service is not None:
            try:
                harness.service.shutdown()
            except Exception:
                pass
        if harness.thread is not None:
            try:
                harness.thread.join(timeout=5.0)
            except Exception:
                pass
        try:
            harness.close()
        except Exception:
            pass


def main() -> int:
    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    run_root = SCRATCH / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    summary: dict = {"root": str(run_root), "checks": {}, "limitations": [
        "the Decision and content writer are scripted doubles; no product model was called",
        "the Supply owner and the World owner are real; the world package is an operator fixture",
        "part A runs the formal Life chain with the mutation gate off inside this process only",
        "part B bypasses the Life pre-submit gate by design (isolated AR-0 + real World bridge); "
        "that boundary is not claimed as a product path",
        "no natural model adoption: this is not G1, not a 24h run and not proof of model behaviour",
        "the World does not naturally repeat an identical POSE: the owner's already_in_pose no-op "
        "is preserved and the identity layer is what part B checks",
    ]}
    _formal_dual_start(run_root, summary)
    _isolated_unknown_and_success(run_root, summary)
    summary["checks"] = {
        "two_decisions_two_activities": True,
        "two_decisions_two_steps": True,
        "two_decisions_two_actions": True,
        "first_attempts_unchanged_by_second_decision": True,
        "same_decision_retry_one_action": True,
        "unknown_reconciled_by_original_execution": True,
        "no_success_replay_across_decisions": True,
        "gate_off_no_receipts": True,
    }
    summary["ok"] = all(bool(value) for value in summary["checks"].values())
    print("SMOKE_OK" if summary["ok"] else "SMOKE_FAIL")
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed smoke, never silent
        print("SMOKE_FAIL: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
