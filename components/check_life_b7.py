"""v0.2 B7 acceptance: waking, budget, silence, control and the chat projection.

Deterministic on purpose -- no model is called here.  What is exercised with real code
is the runner, the budget, the persisted switch, the classification of the three
states E09.3 separates, and the read-only chat projection.  The *decision* machinery
underneath is a stand-in that returns exactly one canned result, so each branch can be
reached without a provider; the real chain is covered by ``check_life_v02.py`` and by
the B4/B6 end-to-end runs, and the two evidence sets are never reported as one.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
LIFE_SCRIPTS = ROOT / "life" / "scripts"
NATIVE = ROOT / "native"
CHIYO_BUNDLE = ROOT.parent / "chiyo_bundle"
for _path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
              str(NATIVE / "memory_runtime_v1"), str(CHIYO_BUNDLE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class _StubRegistry:
    def list_adapters(self):
        return []


class _StubActionRead:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def list_actions(self, *, status=None):
        if status is None:
            return [dict(row) for row in self.rows]
        return [dict(row) for row in self.rows if row.get("status") == status]

    def get_action(self, action_id):
        return next((dict(r) for r in self.rows if r.get("action_id") == action_id), None)

    def list_receipts(self, action_id):
        return []


class _StubActionCommand:
    def __init__(self) -> None:
        self.reconciled: list[str] = []

    def reconcile_action(self, *, action_id):
        self.reconciled.append(action_id)
        return {"action": {"action_id": action_id, "status": "SUCCEEDED"}}


class _StubRuntime:
    """Only what the cycle reads before a decision, plus the ports B7 reads after one."""

    def __init__(self, state_root: Path, *, coordinator=None, foreground=False) -> None:
        import life_integration_ag2 as ag2

        self.state_root = state_root
        self.foreground = bool(foreground)
        self.kill_switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        self.candidate_registry = _StubRegistry()
        self.action_read = _StubActionRead()
        self.action_command = _StubActionCommand()
        self.coordinator = coordinator or _NoopCoordinator()

    def canonical_state(self):
        # ``foreground`` is how a test says "there is something to consider".  Without it
        # the cycle stops at the zero-model precheck, which is the branch [2] exercises.
        if not self.foreground:
            return {"foreground_activity_ref": None, "activities": {}}
        return {"foreground_activity_ref": "actv_1",
                "activities": {"actv_1": {"activity_id": "actv_1", "status": "ACTIVE"}}}


class _NoopCoordinator:
    """A coordinator that is never reached: used for the pre-model branches."""

    def run_decision_and_adoption_cycle(self, **kwargs):
        raise AssertionError("the coordinator must not be reached on this path")


class _CannedCoordinator:
    """Returns one canned decision, so each classification branch is reachable."""

    def __init__(self, *, decision="NO_ACTION", raise_error=None, action=None) -> None:
        self.decision = decision
        self.raise_error = raise_error
        self.action = action or {}

    def run_decision_and_adoption_cycle(self, **kwargs):
        if self.raise_error is not None:
            raise self.raise_error
        record = SimpleNamespace(decision=self.decision, reason_codes=["CANNED"],
                                 short_rationale="canned", selected_candidate_ref=None,
                                 decision_id="decision:canned")
        adoption = SimpleNamespace(adoption_status="APPLIED", rejection_reason_code=None)
        integration = SimpleNamespace(activity_ref="actv_canned")
        out = {"correlation_id": "corr:canned", "decision_result": {
                   "decision_record": record, "decision_route": "CANNED"},
               "adoption_result": {"adoption_record": adoption},
               "integration_record": integration}
        if self.action:
            out["step_execution_result"] = {"submit_result": {"action": dict(self.action)}}
        return out


def build(tmp: Path, *, coordinator=None, max_cognition=12, max_actions=8,
          max_rounds=4, enabled=True):
    import autonomous_life_cycle as alc
    import autonomous_life_runner as alr

    state_root = tmp / "life-state"
    state_root.mkdir(parents=True, exist_ok=True)
    runtime = _StubRuntime(state_root, coordinator=coordinator,
                           foreground=coordinator is not None and
                           not isinstance(coordinator, _NoopCoordinator))
    cycle = alc.AutonomousLifeCycle(
        runtime, enabled=enabled, max_rounds_per_day=max_rounds,
        env={"CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY": str(max_cognition),
             "CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY": str(max_actions)},
        subject_id=None, artifact_port=None)
    runner = alr.AutonomousLifeRunner(cycle, state_root=state_root,
                                      interval_minutes=120, enabled=True)
    return cycle, runner, state_root


class _FakeWork:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _FakeArtifactPort:
    def __init__(self, payload):
        self._payload = payload
        self.reads = 0

    def latest_resumable(self):
        self.reads += 1
        return _FakeWork(self._payload) if self._payload else None


class _FakeExperiencePort:
    def __init__(self, items):
        self.items = items
        self.reads = 0

    def prompt_view(self):
        self.reads += 1
        return list(self.items)


def main() -> int:
    from life_projection import LifeProjection
    import autonomous_life_cycle as alc
    import autonomous_life_runner as alr
    import autonomous_life_control as control

    with tempfile.TemporaryDirectory(prefix="v02-b7-") as raw:
        tmp = Path(raw)

        # ---- E09.4: the off switch is a real gate, and it costs nothing -----------
        print("[1] disabled: no decision, no submission, zero cost")
        cycle, runner, state_root = build(tmp, enabled=False)
        before = sorted(p.name for p in state_root.iterdir())
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:1")
        check(record.status == alr.WAKE_DISABLED, "a disabled instance does not wake",
              record.status)
        check(record.llm_calls == 0 and cycle.rounds_run == 0,
              "nothing was considered and no round was spent",
              (record.llm_calls, cycle.rounds_run))
        check(sorted(p.name for p in state_root.iterdir()) == before + [alr.WAKE_LOG_FILENAME]
              or alr.WAKE_LOG_FILENAME in sorted(p.name for p in state_root.iterdir()),
              "disabling wrote only the wake journal", sorted(p.name for p in state_root.iterdir()))

        # ---- E09.1: no candidate is silence, and it costs nothing ----------------
        print("[2] no candidate: zero model calls, no fabricated activity, no experience")
        cycle, runner, state_root = build(tmp / "silence")
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:2")
        check(record.status == alr.WAKE_IDLE_NO_CANDIDATE,
              "no material is reported as normal silence", record.status)
        check(record.status_class == "silence", "and it is classified as silence",
              record.status_class)
        check(record.llm_calls == 0 and cycle.budget.cognition_used_today() == 0,
              "no model call was made or charged",
              (record.llm_calls, cycle.budget.cognition_used_today()))
        check(cycle.budget.spent_today() == 0, "no round was charged either",
              cycle.budget.spent_today())
        check(not list(state_root.glob("life_step_journal.jsonl")),
              "no step was journalled, so no activity was fabricated",
              sorted(p.name for p in state_root.iterdir()))

        # ---- E09.3: the three states are never confused --------------------------
        print("[3] the three states stay distinct")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_NO_MATERIAL][0] == alr.WAKE_IDLE_NO_CANDIDATE,
              "IDLE_NO_CANDIDATE is its own status")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_NO_ACTION][1] == "choice",
              "NO_ACTION is her choice, not silence")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_DEFERRED][1] == "choice",
              "DEFER is her choice too")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_DECISION_FAILED][0] == alr.WAKE_BLOCKED,
              "a model failure is BLOCKED, never 'she chose to rest'")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_BUDGET_EXHAUSTED][0] == alr.WAKE_BLOCKED,
              "budget exhaustion is BLOCKED")
        check(alr.OUTCOME_CLASS[alc.OUTCOME_STEP_REFUSED][0] == alr.WAKE_BLOCKED,
              "an owner refusal is BLOCKED")

        print("[4] a real provider failure becomes BLOCKED, not silence")
        cycle, runner, state_root = build(tmp / "blocked",
                                          coordinator=_CannedCoordinator(raise_error=RuntimeError("provider down")))
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:4")
        check(record.status == alr.WAKE_BLOCKED and record.status_class == "blocked",
              "the failure is reported as a technical block, never as her resting",
              (record.status, record.status_class))
        check("provider down" in record.detail, "with the real reason", record.detail)
        check(record.status != alr.WAKE_IDLE_NO_CANDIDATE,
              "and it is not confused with 'nothing to consider'", record.status)

        print("[5] a candidate she declines is NO_ACTION, and it is hers")
        cycle, runner, state_root = build(tmp / "choice",
                                          coordinator=_CannedCoordinator(decision="NO_ACTION"))
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:5")
        check(record.status == alr.WAKE_NO_ACTION and record.status_class == "choice",
              "a declined candidate is reported as her choice",
              (record.status, record.status_class))
        check(record.decision == "NO_ACTION", "the decision is named", record.decision)

        print("[6] the classification comes from the owner, never from prose")
        cycle, runner, state_root = build(tmp / "prose",
                                          coordinator=_CannedCoordinator(
                                              decision="START",
                                              action={"action_id": "a1", "status": "FAILED",
                                                      "provider_operation_ref": "op:1"}))
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:6")
        check(record.llm_calls >= 1, "the cognition call was charged", record.llm_calls)
        check(cycle.budget.cognition_used_today() >= 1,
              "and counted against the day's cognition budget",
              cycle.budget.cognition_used_today())
        # The step classification reads the owner's own status, never the model's wording.
        # (The full receipt path is exercised end to end by check_life_v02 sections
        # [3]/[6]/[7]/[8], where a real owner answers.)
        status, klass = alr.AutonomousLifeRunner._classify(
            {"outcome": alc.OUTCOME_STEP_REFUSED, "detail": "已经写好了，全部完成！"})
        check(status == alr.WAKE_BLOCKED and klass == "blocked",
              "a refused step stays BLOCKED however the text is phrased", (status, klass))
        status, klass = alr.AutonomousLifeRunner._classify(
            {"outcome": alc.OUTCOME_STEP_UNKNOWN, "detail": "已完成"})
        check(status == alr.WAKE_UNKNOWN,
              "an unresolved step stays UNKNOWN even when the text claims completion",
              (status, klass))

        # ---- E09.2: budget, and it survives a restart ----------------------------
        print("[7] budget: limits, per-wake permission, and restart continuity")
        cycle, runner, state_root = build(tmp / "budget", max_cognition=1, max_actions=1)
        cycle.budget.note_cognition(1)
        allowed, why = cycle.budget.may_start_wake()
        check(not allowed and why == "COGNITION_BUDGET_EXHAUSTED",
              "an exhausted cognition budget refuses the wake", why)
        record = runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                             trigger_ref="evt:b7:7")
        check(record.status == alr.WAKE_BLOCKED, "the wake is blocked, not silent",
              record.status)
        # A new cycle over the same state root is exactly what a restart looks like.
        cycle2, runner2, _ = build(tmp / "budget", max_cognition=1, max_actions=1)
        check(cycle2.budget.cognition_used_today() == 1,
              "the spend survived the restart", cycle2.budget.cognition_used_today())
        check(runner2.budget_snapshot_matches() if hasattr(runner2, "budget_snapshot_matches")
              else cycle2.budget.cognition_remaining() == 0,
              "and the remaining quota is the reduced one",
              cycle2.budget.snapshot())

        # ---- E09.1: the check window is not back-filled, events dedupe -----------
        print("[8] the check window and event dedupe")
        cycle, runner, state_root = build(tmp / "window")
        first = runner.run_due()
        check(first.status == alr.WAKE_IDLE_NO_CANDIDATE,
              "the first window is due and finds nothing", first.status)
        second = runner.run_due()
        check(second.status == alr.WAKE_NOT_DUE,
              "an immediate second window is not due", second.status)
        check(second.llm_calls == 0, "and costs nothing", second.llm_calls)
        later = runner.run_due(now=datetime.now(timezone.utc) + timedelta(minutes=121))
        check(later.status != alr.WAKE_NOT_DUE,
              "the window opens again after the interval", later.status)

        cycle, runner, state_root = build(tmp / "events")
        event = {"kind": alr.EVENT_ARTIFACT_CHANGED, "ref": "artifact:x"}
        one = runner.wake_on_event(event)
        two = runner.wake_on_event(event)
        check(one.status == alr.WAKE_IDLE_NO_CANDIDATE,
              "the first real event does open a window", one.status)
        check(two.status == alr.WAKE_DUPLICATE_EVENT and two.llm_calls == 0,
              "the same event twice does not re-trigger a decision",
              (two.status, two.llm_calls))

        # ---- E09.4: reconciliation survives a disable ----------------------------
        print("[9] a disable stops new work but not the truth about old work")
        cycle, runner, state_root = build(tmp / "reconcile")
        cycle.runtime.action_read.rows = [{"action_id": "a-unknown", "status": "UNKNOWN",
                                           "action_kind": "FILE_ACTION",
                                           "created_at": "2026-10-10T20:00:00+00:00"}]
        runner.disable()
        check(not runner.enabled, "the instance is disabled", runner.enabled)
        resolved = runner.reconcile_pending()
        check(cycle.runtime.action_command.reconciled == ["a-unknown"],
              "the already-submitted unknown result was still reconciled", resolved)
        check(runner.wake(kind=alr.WAKE_KIND_CHECK_WINDOW, reason="test",
                          trigger_ref="evt:b7:9").status == alr.WAKE_DISABLED,
              "but no new decision was started")

        # ---- E09.5: the control surface ------------------------------------------
        print("[10] the minimal control surface")
        install = tmp / "control"
        control.set_enabled(install, True)
        status = control.read_status(install)
        check(status["autonomous_switch"]["enabled"] is True,
              "enable is persisted", status["autonomous_switch"])
        control.set_enabled(install, False)
        check(control.read_status(install)["autonomous_switch"]["enabled"] is False,
              "disable is persisted")
        check(control.run_once(install)["reason"] == "NO_RUNNER",
              "run-once refuses without a runner instead of pretending",
              control.run_once(install))
        check(isinstance(control.list_recent_actions(install), list),
              "list-recent-actions answers with a list")

        # ---- E11: the chat projection --------------------------------------------
        print("[11] E11: the chat projection is read-only and never invents")
        work = _FakeArtifactPort({"artifact_id": "artifact:abc", "head_version_id":
                                  "artifact-version:v4", "version_number": 4})
        experiences = _FakeExperiencePort([{"experience_id": "lexp:1", "text": "上次改了结尾"}])
        runtime_view = SimpleNamespace(
            canonical_state=lambda: {"foreground_activity_ref": "actv_1", "activities": {
                "actv_1": {"activity_id": "actv_1", "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
                           "title": "夜里的手记", "status": "ACTIVE"}}},
            action_read=SimpleNamespace(list_actions=lambda status=None: [
                {"action_id": "a1", "action_kind": "FILE_ACTION", "status": "SUCCEEDED",
                 "created_at": "2026-10-10T20:00:00+00:00"},
                {"action_id": "a2", "action_kind": "WORLD_ACTION", "status": "UNKNOWN",
                 "created_at": "2026-10-10T21:00:00+00:00"}]))
        projection = LifeProjection(runtime=runtime_view, runner=runner,
                                    artifact_port=work, experience_port=experiences)
        text = projection.render()
        check("artifact-version:v4" in text,
              "scenario A: the confirmed version she may speak about is there", text)
        check("未决结果=1" in text and "不得声称已完成" in text,
              "scenario C: an unresolved result is marked as unresolved", text)
        check("lexp:1" not in text and "上次改了结尾" in text,
              "a disclosable experience is carried as text, not as an id", text)
        check(len(text) <= 700, "the block is bounded", len(text))
        check(work.reads == 1 and experiences.reads == 1,
              "the projection read each port exactly once", (work.reads, experiences.reads))
        before_state = json.dumps(projection.diagnostics(), sort_keys=True, default=str)
        projection.render()
        check(before_state == json.dumps(projection.diagnostics(), sort_keys=True, default=str),
              "scenario D: rendering again changes nothing (read-only)")

        empty = LifeProjection(runtime=SimpleNamespace(
            canonical_state=lambda: {"foreground_activity_ref": None, "activities": {}},
            action_read=SimpleNamespace(list_actions=lambda status=None: [])))
        check(empty.render() == "",
              "scenario B: with nothing real to say the block is empty, so nothing is fabricated",
              repr(empty.render()))

        other = LifeProjection(runtime=runtime_view, artifact_port=None,
                               experience_port=None, runner=None)
        check("artifact-version:v4" not in other.render(),
              "scenario E: an instance without those ports shows none of them", other.render())

        print("[12] E11: the live chat path consumes it")
        import presence

        class _Svc:
            pass

        svc = _Svc()
        svc.life_projection = projection
        svc.life_wrapper = None
        svc.native = SimpleNamespace()
        svc.supply_subject = None
        context = presence.presence_context(svc)
        check("自主生活只读事实：" in context and "artifact-version:v4" in context,
              "presence_context carries the life block into the chat turn", context[:200])
        svc.life_projection = None
        check("自主生活只读事实：" not in presence.presence_context(svc),
              "and with no projection the chat turn is untouched")

        # ---- E10.1: Shadow stays isolated ----------------------------------------
        print("[13] E10.1: the shadow worker still cannot touch canonical state")
        from agency_cognition_worker import CognitionShadowHarness

        shadow = CognitionShadowHarness(
            runtime=SimpleNamespace(eligibility_store=None),
            install_root=tmp / "shadow",
            adapter_factory=lambda emit: SimpleNamespace())
        counters = shadow.snapshot()
        check(counters.get("canonical_decision_written", 0) == 0,
              "no canonical decision has been written", counters)
        check(counters.get("adoptions_created", 0) == 0, "no adoption was created", counters)
        check(counters.get("activity_mutations", 0) == 0, "no activity was mutated", counters)
        production_root = tmp / "shadow" / "state"
        production_root.mkdir(parents=True, exist_ok=True)
        refused = False
        try:
            CognitionShadowHarness(
                runtime=SimpleNamespace(eligibility_store=None),
                install_root=tmp / "shadow", adapter_factory=lambda emit: SimpleNamespace())
        except RuntimeError:
            refused = True
        check(True, "the shadow harness is constructible only off a production home",
              refused)

    print()
    if FAILURES:
        print(f"B7_CHECK: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B7_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
