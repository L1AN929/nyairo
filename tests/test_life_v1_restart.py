#!/usr/bin/env python3
"""D-final：真实跨进程重启（两个独立 worker，各自装配真实 runtime 并真的跑一轮）。

Why this file exists
--------------------
The earlier "restart" evidence was a **read-only probe** in another PID: it proved the files were
readable from disk, not that a fresh process could re-assemble the real runtime and continue the
same life.  This harness closes exactly that gap, with the two facts kept apart:

* worker 1 (PID A) builds the real ``ProductionLifeRuntime`` + ``assemble_autonomous_life`` with
  the real Memory boundary and the instance's own budget object, runs one real local step
  (owner receipt, new version), confirms the operator pause and the request-budget counter, then
  ``close()``s the runtime so the canonical lease is really released;
* worker 2 (PID B) builds a *new* runtime, a *new* assembly and a *new* budget object on the same
  data root, proves it starts paused with **0** model calls, then resumes and legitimately
  continues the same Activity into a new owner version -- run identity, counters and ceilings
  unchanged, bookmark kept, nothing written during the gap.

Every owner here is the real local one (Supply / World / Memory / Activity / LR-4).  The only
stand-ins are the deterministic decision/content branches, and no real product model, external
network, long run or extra UNKNOWN injection is involved.

Run these tests by id (a bare module run also picks up the inherited W05 trio of the shared base):

    python -m unittest tests.test_life_v1_restart.RealLifeRestartTest.<test>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "integration"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import one_day_life as odl  # noqa: E402
from test_life_v1_integration import DScenarioBase  # noqa: E402
from test_one_day_life import posix_only  # noqa: E402

#: One worker: build the real runtime/assembly on this data root, run its phase, release, exit.
#: It is *not* a probe -- both phases really call ``cycle.evaluate_once``.
WORKER = textwrap.dedent(
    '''
    import json, os, sys
    sys.path[:0] = [{tests!r}, {scripts!r}]

    import one_day_life as odl
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    from life_experience_port import LifeExperiencePort
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from world_journey_action_adapter import JourneyWorldResolver, RealJourneyWorldActionAdapter
    from test_one_day_life import StubCognition, StubObservation, StubWriter

    INSTANCE = {instance!r}
    PHASE = {phase!r}
    WORK = {work!r}

    env = odl.instance_environment(INSTANCE)
    state_root = odl.life_state_root(INSTANCE)
    data = odl.read_instance(INSTANCE)
    supply = assembly.resolve_supply_configuration(env)
    report = {{"phase": PHASE, "pid": os.getpid(), "run_id": env.get(odl.RUN_ID_ENV)}}


    class WorkLoopback(StubCognition):
        """Deterministic branch over the *legal* set: CONTINUE the running work, else START it.

        It never falls back to the generic stub: a window that offers no legal candidate about
        this work is a real failure of the state this harness asserts on, so it raises with the
        candidate dump instead of taking an unrelated START.
        """

        def __init__(inner):
            super().__init__("artifact")
            inner.seen = []
            inner.chosen = []

        def invoke(inner, request, *, schema_retry_index=0):
            allowed = [str(c) for c in (getattr(request, "allowed_reason_codes", []) or [])]
            candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
            inner.seen.append([{{"kind": c.get("candidate_kind"),
                                "source": c.get("source_kind"),
                                "legal": [str(x) for x in (c.get("legal_decisions") or [])],
                                "refs": [str(x) for x in (c.get("target_refs") or [])],
                                "source_ref": c.get("source_ref")}}
                               for c in candidates])
            about_work = [
                c for c in candidates
                if any(WORK in ref for ref in
                       ([str(x) for x in (c.get("target_refs") or [])]
                        + [str(c.get("source_ref") or "")]))]
            continue_legal = [c for c in about_work
                              if "CONTINUE" in [str(x) for x in (c.get("legal_decisions") or [])]]
            start_legal = [c for c in about_work
                           if "START" in [str(x) for x in (c.get("legal_decisions") or [])]]
            chosen = (continue_legal or start_legal or [None])[0]
            if chosen is None:
                raise AssertionError(
                    "no legal candidate about the work artifact in this window: %r"
                    % (inner.seen,))
            per = [str(x) for x in (chosen.get("legal_decisions") or [])]
            decision = "CONTINUE" if "CONTINUE" in per else "START"
            inner.chosen.append({{"decision": decision,
                                 "candidate_id": chosen.get("candidate_id"),
                                 "kind": chosen.get("candidate_kind"),
                                 "legal": per}})
            inner.call_count += 1
            import agency_decision_ag1 as ag1
            return ag1.CognitiveDecisionResponse(
                model_run_ref="model_run:stub:worker", model_id=inner.model_id,
                model_version=inner.model_version,
                raw_output={{"decision": decision,
                            "selected_candidate_id": chosen["candidate_id"],
                            "reason_codes": allowed[:1],
                            "short_rationale": "carry on with the running work",
                            "deferred_candidate_ids": []}},
                latency_ms=1)

    class StartControlLoopback(StubCognition):
        """负控：只挑 AG-1 真的允许 START 的候选，并优先挑**关于原 artifact** 的那个。

        候选 id 每次运行都不同、候选表顺序不稳定；靠“第一个非世界候选”会挑到只许
        CONTINUE/PAUSE 的当前活动候选，AG-1 直接 DECISION_CANDIDATE_INCOMPATIBLE，
        负控就测不到 AG-2 在同一窗口的拒绝。挑不到合法 START 就当场失败，不静默降级。
        """

        def __init__(inner, work):
            super().__init__("artifact")
            inner.work = work
            inner.control = None

        def invoke(inner, request, *, schema_retry_index=0):
            import agency_decision_ag1 as ag1

            inner.call_count += 1
            allowed = [str(code) for code in
                       (getattr(request, "allowed_reason_codes", []) or [])]
            candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
            window = [{{"candidate_id": c.get("candidate_id"),
                       "kind": c.get("candidate_kind"),
                       "source": c.get("source_kind"),
                       "legal": [str(x) for x in (c.get("legal_decisions") or [])],
                       "refs": [str(x) for x in (c.get("target_refs") or [])],
                       "source_ref": c.get("source_ref")}}
                      for c in candidates]
            about_ids = {{str(c.get("candidate_id")) for c in candidates
                         if any(inner.work in ref for ref in
                                ([str(x) for x in (c.get("target_refs") or [])]
                                 + [str(c.get("source_ref") or "")]))}}
            start_legal = [c for c in candidates
                           if "START" in [str(x) for x in (c.get("legal_decisions") or [])]]
            chosen = (next((c for c in start_legal
                            if str(c.get("candidate_id")) in about_ids), None)
                      or (start_legal or [None])[0])
            if chosen is None:
                raise AssertionError(
                    "no candidate is AG-1 legal for START in this window: %r" % (window,))
            inner.control = {{"candidate_id": chosen.get("candidate_id"),
                              "kind": chosen.get("candidate_kind"),
                              "legal": [str(x) for x in (chosen.get("legal_decisions") or [])],
                              "about_work": str(chosen.get("candidate_id")) in about_ids,
                              "window": window}}
            return ag1.CognitiveDecisionResponse(
                model_run_ref="model_run:stub:start-control", model_id=inner.model_id,
                model_version=inner.model_version,
                raw_output={{"decision": "START",
                            "selected_candidate_id": chosen["candidate_id"],
                            "reason_codes": allowed[:1],
                            "short_rationale": "deliberately re-start the running work",
                            "deferred_candidate_ids": []}},
                latency_ms=1)


    cognition = WorkLoopback()
    memory_db = (env.get("NYA02_MEMORY_DB") or str(INSTANCE / "memory" / "evidence.sqlite"))
    controls = env.get("NYA02_MEMORY_CONTROLS_DB") or memory_db
    subject = env.get("NYA02_MEMORY_SUBJECT") or "chiyo"
    consumer = MemoryAdmissionExperienceConsumer(
        port=LifeExperiencePort(memory_db, controls_db=controls, subject=subject), enabled=True)

    port = lap.LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=str(INSTANCE), artifact_action_adapter=adapter,
        world_action_adapter=RealJourneyWorldActionAdapter(
            JourneyWorldResolver(environment=env)),
        experience_consumer=consumer)
    budget = assembly.request_budget(env, state_root)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root, world_provider=StubObservation(env),
        writer=StubWriter(), enabled=True, requests=budget)


    def head():
        return odl.supply_call(supply["supply_socket"], {{
            "op": "read_head", "subject": data["subject_id"],
            "artifact_id": WORK}})["head"]["version_id"]


    report["assembled_state"] = assembled.get("state")
    cycle = assembled["cycle"]
    report["budget_at_start"] = budget.view()
    report["pause_at_start"] = odl.status(INSTANCE)["pause"]
    report["state_at_start"] = odl.status(INSTANCE)["state"]["state_class"]
    report["head_at_start"] = head()
    report["reading_at_start"] = odl.reading_view(INSTANCE)

    if PHASE == "first":
        out = cycle.evaluate_once(trigger_ref="worker1:work", act=True)
        report["round"] = {{"outcome": out.get("outcome"),
                           "decision": out.get("decision"),
                           "adoption_status": out.get("adoption_status"),
                           "llm_calls": out.get("llm_calls"),
                           "owner_receipt": bool(out.get("owner_receipt")),
                           "owner_version_created": out.get("owner_version_created"),
                           "reason_codes": list(out.get("reason_codes") or []),
                           "detail": out.get("detail"),
                           "activity_ref": out.get("activity_ref")}}
        report["cognition_calls"] = cognition.call_count
        report["head_after_round"] = head()
        budget.reserve("decision")
        report["budget_after_reserve"] = budget.view()
        odl.set_pause(INSTANCE, True, reason="final:restart")
        report["pause_set"] = odl.status(INSTANCE)["pause"]
    else:
        paused = cycle.evaluate_once(trigger_ref="worker2:paused", act=True)
        report["paused_round"] = {{"outcome": paused.get("outcome"),
                                  "llm_calls": paused.get("llm_calls")}}
        report["cognition_calls_while_paused"] = cognition.call_count
        report["head_while_paused"] = head()
        odl.set_pause(INSTANCE, False, reason="final:restart")
        resumed = cycle.evaluate_once(trigger_ref="worker2:resume", act=True)
        report["round"] = {{"outcome": resumed.get("outcome"),
                           "decision": resumed.get("decision"),
                           "adoption_status": resumed.get("adoption_status"),
                           "llm_calls": resumed.get("llm_calls"),
                           "owner_receipt": bool(resumed.get("owner_receipt")),
                           "owner_version_created": resumed.get("owner_version_created"),
                           "reason_codes": list(resumed.get("reason_codes") or []),
                           "detail": resumed.get("detail"),
                           "activity_ref": resumed.get("activity_ref")}}
        report["cognition_calls"] = cognition.call_count
        report["head_after_round"] = head()
        activity_ref = resumed.get("activity_ref")
        report["activity_after"] = (dict(runtime.activity_read.get_activity(activity_ref))
                                    if activity_ref else None)
        # precise negative control for the root cause this harness hit: in the same window a
        # branch that deliberately takes a START AG-1 really allows (about this very artifact
        # when the window offers one) is refused by AG-2 while a foreground Activity is ACTIVE,
        # and it changes nothing.  That is the failure mode the restart of an unfinished
        # Activity must never regress into.  The branch is order-free on purpose: candidate ids
        # change every run, so "the first non-world candidate" sometimes only allows
        # CONTINUE/PAUSE and AG-1 then rejects the START before AG-2 can be exercised.
        report["head_before_start_control"] = head()
        control_cognition = StartControlLoopback(WORK)
        runtime.decision_service.cognition_adapter = control_cognition
        refused = cycle.evaluate_once(trigger_ref="worker2:start-control", act=True)
        report["start_control_candidate"] = control_cognition.control
        report["start_control"] = {{"outcome": refused.get("outcome"),
                                   "decision": refused.get("decision"),
                                   "adoption_status": refused.get("adoption_status"),
                                   "detail": refused.get("detail"),
                                   "llm_calls": refused.get("llm_calls")}}
        report["head_after_start_control"] = head()
        report["activity_after_control"] = (dict(runtime.activity_read.get_activity(activity_ref))
                                           if activity_ref else None)
        budget.reserve("decision")
        report["budget_after_reserve"] = budget.view()
        report["status_at_end"] = odl.status(INSTANCE)["state"]["state_class"]

    report["reading_at_end"] = odl.reading_view(INSTANCE)
    # what AG-1 really offered in this window, and what this branch picked from it
    report["cognition_seen"] = cognition.seen
    report["cognition_chosen"] = cognition.chosen
    runtime.close()
    report["closed"] = not getattr(runtime.lease, "held", True)
    print("WORKER_REPORT " + json.dumps(report, ensure_ascii=False, default=str))
    '''
)


@posix_only
class RealLifeRestartTest(DScenarioBase):
    """Two independent worker processes, one data root, one continuous life."""

    def _worker(self, phase: str, work_artifact_id: str) -> dict:
        code = WORKER.format(tests=str(ROOT / "tests"),
                             scripts=str(ROOT / "components" / "life" / "scripts"),
                             instance=str(self.instance), phase=phase, work=work_artifact_id)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=600,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, (done.returncode, done.stdout[-1500:],
                                              done.stderr[-3000:]))
        lines = [line for line in done.stdout.splitlines()
                 if line.startswith("WORKER_REPORT ")]
        self.assertTrue(lines, done.stdout[-2000:])
        return json.loads(lines[-1].split(" ", 1)[1])

    def test_a_real_restart_keeps_run_identity_and_continues_the_same_activity(self):
        # The *parent* process builds no runtime at all: the canonical lease must belong to the
        # workers, one at a time, or the second real assembly could never be proven.
        prepared = self.prepare_scenario()
        data = prepared.data
        work = data["work_artifact_id"]
        bookmark = odl.read_page(self.instance, page=1)
        self.assertTrue(bookmark["ok"], bookmark)
        positioned = odl.reading_view(self.instance)
        self.assertTrue(positioned["positions"], positioned)
        head_before = self.head(work)["version_id"]

        # ---- worker 1 (PID A): real runtime, one real step, pause + counter, then release ----
        first = self._worker("first", work)
        self.assertNotEqual(first["pid"], os.getpid(), first)
        self.assertEqual(first["assembled_state"], "READY", first)
        self.assertEqual(first["round"]["outcome"], "ACTED", first)
        self.assertTrue(first["round"]["owner_receipt"], first)
        self.assertIsNot(first["round"]["owner_version_created"], False, first)
        self.assertEqual(first["cognition_calls"], 1, first)
        head_after_first = self.head(work)["version_id"]
        self.assertNotEqual(head_after_first, head_before, first)
        self.assertEqual(first["head_after_round"], head_after_first, first)
        self.assertTrue(first["pause_set"]["paused"], first["pause_set"])
        calls_after_first = int(first["budget_after_reserve"]["utc_day_calls"])
        self.assertEqual(calls_after_first,
                         int(first["budget_at_start"]["utc_day_calls"]) + 1, first)
        self.assertTrue(first["closed"], first)

        # ---- nothing happens during the gap: no offline experience is invented ---------------
        gap_rows = len(prepared.memory.port.recent(limit=20))
        self.assertEqual(self.head(work)["version_id"], head_after_first)
        self.assertEqual(len(prepared.memory.port.recent(limit=20)), gap_rows)

        # ---- worker 2 (PID B): new runtime/new budget on the same root, 0 calls while paused --
        second = self._worker("second", work)
        self.assertNotEqual(second["pid"], first["pid"], (second, first))
        self.assertNotEqual(second["pid"], os.getpid(), second)
        self.assertEqual(second["assembled_state"], "READY", second)
        self.assertTrue(second["closed"], second)
        # same run identity and the same persisted ledger, not a reset one
        self.assertEqual(second["run_id"], first["run_id"], (second, first))
        self.assertEqual(int(second["budget_at_start"]["utc_day_calls"]), calls_after_first,
                         second["budget_at_start"])
        self.assertEqual(int(second["budget_at_start"]["utc_day_max"]),
                         int(first["budget_after_reserve"]["utc_day_max"]), second)
        self.assertEqual(int(second["budget_at_start"]["run_window_max"]),
                         int(first["budget_after_reserve"]["run_window_max"]), second)
        # it starts in the pause the operator confirmed before the restart, and asks nobody
        self.assertTrue(second["pause_at_start"]["paused"], second["pause_at_start"])
        self.assertEqual(second["state_at_start"], odl.STATE_PAUSED, second)
        self.assertEqual(second["paused_round"]["outcome"], "PAUSED_BY_OPERATOR", second)
        self.assertEqual(second["paused_round"]["llm_calls"], 0, second)
        self.assertEqual(second["cognition_calls_while_paused"], 0, second)
        self.assertEqual(second["head_while_paused"], head_after_first, second)
        # ... and after the resume it really continues the *same* Activity into a new version
        self.assertEqual(second["round"]["outcome"], "ACTED", second)
        self.assertTrue(second["round"]["owner_receipt"], second)
        self.assertEqual(second["round"]["activity_ref"], first["round"]["activity_ref"], second)
        self.assertEqual(second["cognition_calls"], 1, second)
        # the branch really *continued* the running work instead of re-starting it, and it chose
        # from a legal set that also offered a START about the same artifact (which AG-2 refuses
        # while a foreground Activity is ACTIVE) -- so this is not a generic stub's pick
        self.assertEqual(first["cognition_chosen"][-1]["decision"], "START", first)
        self.assertEqual(second["cognition_chosen"][-1]["decision"], "CONTINUE", second)
        self.assertIn("CONTINUE", second["cognition_chosen"][-1]["legal"], second)
        self.assertTrue(any("START" in entry["legal"]
                            for entry in second["cognition_seen"][-1]),
                        second["cognition_seen"][-1])
        # no adoption rejection: the continuation was accepted, and the step really ran
        self.assertEqual(second["round"]["outcome"], "ACTED", second)
        self.assertNotIn("REJECTED", str(second["round"]["adoption_status"] or ""), second)
        self.assertTrue(str(second["round"]["detail"] or "").startswith("owner committed"),
                        second["round"])
        # ... and the *same* window would have refused a START re-entry (the failure this harness
        # was born from): an ACTIVE foreground refuses START, and nothing changes
        control = second["start_control"]
        self.assertEqual(control["outcome"], "ADOPTION_REJECTED", control)
        self.assertEqual(control["adoption_status"], "REJECTED_STALE", control)
        self.assertIn("CANNOT_START_WHILE_FOREGROUND_ACTIVE", str(control["detail"]), control)
        # the control really chose from the AG-1 legal set instead of trusting candidate order:
        # a START-legal candidate about this very artifact, so AG-2 (not AG-1) is what refuses
        control_candidate = second["start_control_candidate"]
        self.assertIn("START", control_candidate["legal"], control_candidate)
        self.assertTrue(control_candidate["about_work"], control_candidate)
        self.assertEqual(control["decision"], "START", control)
        self.assertEqual(second["head_after_start_control"], second["head_after_round"], second)
        self.assertEqual(second["activity_after_control"]["status"], "ACTIVE", second)
        self.assertEqual(second["activity_after_control"]["revision"],
                         second["activity_after"]["revision"], second)
        head_after_second = self.head(work)["version_id"]
        self.assertNotEqual(head_after_second, head_after_first, second)
        self.assertEqual(second["head_after_round"], head_after_second, second)
        self.assertEqual(second["activity_after"]["status"], "ACTIVE", second["activity_after"])
        self.assertEqual(int(second["budget_after_reserve"]["utc_day_calls"]),
                         calls_after_first + 1, second)
        # a step worker is not a resident loop, so the entry honestly reports STOPPED (no
        # resident heartbeat exists) instead of inventing an IDLE life
        self.assertEqual(second["status_at_end"], odl.STATE_STOPPED, second)
        # the bookmark the first phase left is still there (nothing was re-read or reset)
        self.assertTrue(second["reading_at_end"]["positions"], second["reading_at_end"])
        self.assertEqual(second["reading_at_end"]["positions"][0]["position"],
                         positioned["positions"][0]["position"], second["reading_at_end"])


if __name__ == "__main__":
    unittest.main()
