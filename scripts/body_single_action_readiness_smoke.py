#!/usr/bin/env python3
"""零模型冒烟：BODY 单动作在前台的"下一步"被如实撤回，既有的 PAUSE 出口照旧。

做什么
======
在全新的 ``/tmp`` 根里，用**真实** Supply owner、**真实** World/Body gateway 和**真实**
``build_production_life_runtime`` + ``assemble_autonomous_life`` 装配 Life cycle。Decision 与
内容是脚本替身，但两轮都走正式 ``cycle.evaluate_once``：

1. **round 1（gate=off）**：真实 World 机会源给出一个 ``bodyact:pose:`` 候选，脚本 Decision
   按正式 AG-1 合法集 START 它。动作在正式链上被 pre-submit 拒绝，按已交付的分类报
   ``STEP_REFUSED``：Action ``PREPARED``、attempt ``PRE_SUBMIT_FAILED``/``GATE_OFF``、零回执、
   身体基底不变、Activity 仍是前台 ``ACTIVE``。这一轮还没有"当前 Activity"，所以模型请求里
   **没有** ``current_step_unavailable_true``（正控：事实不是无条件塞进去的）。
2. **round 2（仍 gate=off）**：同一个 BODY Activity 占前台。真实的 readiness 判定它对这个世界
   已经没有可执行步骤，于是本次**实际模型请求**里 ``current_step_unavailable_true`` 出现；
   当前 Activity 的候选不再提供 ``CONTINUE``，但 ``PAUSE``/``ABANDON``/``DEFER`` 仍在。脚本
   Decision 选 ``PAUSE``，由正式 AG-2 采纳、真实 LR-2 释放前台。
3. **round 2 之后**：Activity ``PAUSED`` 且 ``foreground_released=True``、canonical 前台为空；
   仍然只有一个 Action、一次 attempt、零回执（没有重发、没有新 Action），也没有新资格。

不做什么
========
不调用产品模型、不起 HTTP/浏览器、不读写任何既有实例、不改全局默认 gate（只在脚本自己的
进程/环境字典里置 off 并在 finally 恢复）、不删数据。Decision/内容替身；真实 owner 是 Supply
与 World/Body gateway，gate/AG-0/AG-1/AG-2/LR-2/AR-0 都是正式代码。临时根保留。

退出码：0 = 全部断言成立；1 = 任一断言失败或服务起不来。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    JourneyWorldResolver, RealJourneyWorldActionAdapter)

import world_move_opportunity_source as wmos  # noqa: E402
from tests.test_one_day_life import Services, StubWriter, WorldGateway  # noqa: E402
from tests.test_world_daily_life_continuous import (  # noqa: E402
    WORLD_PACKAGE, Clock, episode_instant)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
FACT_KEY = "current_step_unavailable_true"
SCRATCH = Path(os.environ.get("NYAIRO_BODY_SINGLE_ACTION_SMOKE_ROOT")
               or "/tmp/nyairo-body-single-action-smoke")


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

    model_id = "stub:body-single-action-smoke"
    model_version = "body-single-action-smoke-v1"

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
        observed_keys = [str(key) for key in (getattr(request, "observed_fact_keys", None) or [])]
        self.requests.append({
            "policy": self.policy,
            "observed_fact_keys": observed_keys,
            "step_unavailable_in_request": FACT_KEY in observed_keys,
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "candidate_kind": row.get("candidate_kind"),
                            "source_kind": row.get("source_kind"),
                            "activity_ref": row.get("activity_ref"),
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
                       and any(ref.startswith("bodyact:pose:") for ref in self._targets(row))]
            if matches:
                raw = {"decision": "START",
                       "selected_candidate_id": matches[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: one body action",
                       "deferred_candidate_ids": []}
        elif self.policy == "pause_body":
            matches = [row for row in candidates
                       if "PAUSE" in (row.get("legal_decisions") or [])
                       and row.get("candidate_kind") == ag0.CANDIDATE_KIND_CONTINUE_CURRENT]
            if matches:
                raw = {"decision": "PAUSE",
                       "selected_candidate_id": matches[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: set the body Activity down",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:body-single-action-smoke:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def _canonical(runtime) -> dict:
    state, _journal = runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _eligibilities(runtime) -> dict:
    coordinator = getattr(runtime, "waiting_coordinator", None)
    store = getattr(coordinator, "eligibility_store", None)
    if store is None:
        return {}
    return dict((store.load_state() or {}).get("eligibilities") or {})


def _body_pose(client) -> dict:
    payload = client.get_snapshot().data or {}
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
    return {"pose": body.get("pose"), "orientation": body.get("orientation")}


def _envelope(out: dict) -> dict:
    return {key: out.get(key) for key in (
        "outcome", "decision", "decision_route", "adoption_status",
        "selected_candidate_ref", "activity_ref", "action_id", "action_status",
        "detail", "error", "step_readiness", "step_readiness_facts")}


def main() -> int:
    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    run_root = SCRATCH / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    saved_env = {GATE_ENV: os.environ.get(GATE_ENV)}
    services = Services(run_root / "services")
    gateway = None
    runtime = None
    summary: dict = {"root": str(run_root), "checks": {}, "limitations": [
        "the Decision and content writer are scripted doubles; no product model was called",
        "the World/Body gateway and Supply owner are real; the world package is an operator fixture",
        "the gate is off only inside this script's own process/environment dict; the default is unchanged",
        "no natural model adoption: this is not G1, not a 24h run and not proof of model behaviour",
    ]}
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:body-single-action",
            title="Body single action readiness smoke",
            seed_content="验收夹：这一句只用于准备一个真实作品。\n",
            world_package=WORLD_PACKAGE)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=odl.instance_environment(instance))
        env = odl.instance_environment(instance)
        env.update(gateway.environment())
        # The round really runs with the mutation gate off; nothing submits to the World owner.
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
        body_before = _body_pose(client)

        # -- round 1: formal START of one body action; the gate refuses the submission ---
        first = cycle.evaluate_once(trigger_ref="evt:body-single:1",
                                    observed_at=clock.iso(), act=True)
        _check(first.get("decision") == "START" and first.get("adoption_status") == "APPLIED",
               "round 1 did not START a body candidate",
               {"envelope": _envelope(first), "requests": cognition.requests})
        activity_ref = str(first.get("activity_ref") or "")
        _check(bool(activity_ref), "round 1 named no Activity", _envelope(first))
        action_id = str(first.get("action_id") or "")
        _check(bool(action_id), "round 1 created no Action", _envelope(first))
        _check(first.get("outcome") == alc.OUTCOME_STEP_REFUSED,
               "round 1 was not the definite refusal the classification owns",
               _envelope(first))
        _check(first.get("action_status") == "PREPARED",
               "round 1 did not keep the Action PREPARED", _envelope(first))
        actions = [dict(row) for row in runtime.action_read.list_actions()]
        attempts = [dict(row) for row in runtime.action_read.list_attempts(action_id)]
        receipts = [dict(row) for row in runtime.action_read.list_receipts(action_id)]
        _check(len(actions) == 1, "round 1 did not leave exactly one Action", actions)
        _check(len(attempts) == 1 and attempts[0].get("status") == "PRE_SUBMIT_FAILED",
               "round 1 attempt is not a single pre-submit failure", attempts)
        _check(attempts[0].get("error_class") == "GATE_OFF",
               "round 1 attempt lost GATE_OFF", attempts)
        _check(receipts == [], "round 1 produced a receipt", receipts)
        _check(_body_pose(client) == body_before, "round 1 changed the body substrate",
               {"before": body_before, "after": _body_pose(client)})
        record = _activity(runtime, activity_ref)
        _check(record.get("status") == "ACTIVE", "round 1 Activity is not ACTIVE", record)
        _check(record.get("activity_kind") == alc.BODY_ACTION_ACTIVITY_KIND,
               "round 1 Activity is not the body Activity shape", record)
        state = _canonical(runtime)
        _check(state.get("foreground_activity_ref") == activity_ref,
               "round 1 Activity does not hold the foreground", state.get("foreground_activity_ref"))
        _check(cognition.requests and not cognition.requests[0]["step_unavailable_in_request"],
               "the no-step fact was present before this Activity had no step",
               cognition.requests[:1])
        summary["round1"] = {
            "outcome": first.get("outcome"), "action_status": first.get("action_status"),
            "attempt_status": attempts[0].get("status"),
            "attempt_error_class": attempts[0].get("error_class"),
            "activity_kind": record.get("activity_kind"),
            "body_unchanged": True,
            "step_unavailable_before": cognition.requests[0]["step_unavailable_in_request"],
        }

        # -- round 2: the real request no longer offers CONTINUE; PAUSE is taken ----------
        cognition.policy = "pause_body"
        second = cycle.evaluate_once(trigger_ref="evt:body-single:2",
                                     observed_at=clock.iso(), act=True)
        request = cognition.requests[-1]
        _check(request["step_unavailable_in_request"],
               "the actual model request did not carry the no-step fact", request)
        current = [row for row in request["candidates"]
                   if row.get("candidate_kind") == ag0.CANDIDATE_KIND_CONTINUE_CURRENT
                   and row.get("source_kind") == ag0.SOURCE_CURRENT_ACTIVITY]
        _check(len(current) == 1,
               "the request did not offer exactly one candidate for this Activity", request)
        legal = list(current[0]["legal_decisions"])
        _check("CONTINUE" not in legal, "the body Activity was still offered CONTINUE", legal)
        _check("PAUSE" in legal, "the existing PAUSE exit was withdrawn too", legal)
        _check("ABANDON" in legal and "DEFER" in legal,
               "the other existing exits did not survive", legal)
        readiness = second.get("step_readiness") or {}
        _check(readiness.get("available") is False,
               "readiness did not report the body Activity as having no step", readiness)
        _check(readiness.get("reason_code") == "BODY_SINGLE_ACTION_NO_FURTHER_STEP",
               "readiness used an unexpected reason", readiness)
        _check(readiness.get("bound") is True and readiness.get("activity_ref") == activity_ref
               and str(readiness.get("target_ref") or "").startswith("bodyact:"),
               "readiness was not bound to this Activity and its own body target", readiness)
        _check(list(second.get("step_readiness_facts") or []) == [FACT_KEY],
               "the round did not project the fact AG-1 consumes",
               second.get("step_readiness_facts"))
        _check(second.get("decision") == "PAUSE" and second.get("adoption_status") == "APPLIED",
               "round 2 did not adopt the PAUSE", _envelope(second))
        paused = _activity(runtime, activity_ref)
        _check(paused.get("status") == "PAUSED", "the body Activity is not PAUSED", paused)
        _check(paused.get("foreground_released") is True,
               "the PAUSE did not release the foreground", paused)
        _check(_canonical(runtime).get("foreground_activity_ref") is None,
               "the foreground is still held after the PAUSE", _canonical(runtime))
        # no retry, no new Action, no receipt, no eligibility was invented
        _check([dict(row) for row in runtime.action_read.list_actions()] == actions,
               "round 2 created or changed an Action",
               [dict(row) for row in runtime.action_read.list_actions()])
        _check([dict(row) for row in runtime.action_read.list_attempts(action_id)] == attempts,
               "round 2 re-submitted the refused action",
               [dict(row) for row in runtime.action_read.list_attempts(action_id)])
        _check([dict(row) for row in runtime.action_read.list_receipts(action_id)] == receipts,
               "round 2 produced a receipt for the refused action")
        _check(_eligibilities(runtime) == {}, "round 2 created a resume eligibility",
               _eligibilities(runtime))
        summary["round2"] = {
            "outcome": second.get("outcome"), "decision": second.get("decision"),
            "adoption_status": second.get("adoption_status"),
            "legal_decisions": legal,
            "reason_code": readiness.get("reason_code"),
            "step_readiness_facts": list(second.get("step_readiness_facts") or []),
            "activity_status": paused.get("status"),
            "foreground_released": paused.get("foreground_released"),
            "foreground": _canonical(runtime).get("foreground_activity_ref"),
            "actions": len(actions),
            "attempts": len(attempts),
            "receipts": len(receipts),
            "eligibilities": len(_eligibilities(runtime)),
        }
        summary["checks"] = {
            "round1_start_executed": first.get("decision") == "START",
            "round1_definite_refusal": first.get("outcome") == alc.OUTCOME_STEP_REFUSED,
            "round1_action_prepared": first.get("action_status") == "PREPARED",
            "round1_gate_off_no_receipt": not receipts,
            "round1_body_unchanged": True,
            "fact_absent_before": not cognition.requests[0]["step_unavailable_in_request"],
            "fact_present_in_real_request": request["step_unavailable_in_request"],
            "continue_withdrawn": "CONTINUE" not in legal,
            "pause_abandon_defer_kept": {"PAUSE", "ABANDON", "DEFER"} <= set(legal),
            "pause_adopted": second.get("decision") == "PAUSE",
            "foreground_released": paused.get("foreground_released") is True,
            "no_resubmission": True,
            "no_eligibility": _eligibilities(runtime) == {},
        }
        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        summary["model_calls"] = cognition.calls
        print("SMOKE_OK" if passed else "SMOKE_FAIL")
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
        return 0 if passed else 1
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed smoke, never silent
        print("SMOKE_FAIL: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
