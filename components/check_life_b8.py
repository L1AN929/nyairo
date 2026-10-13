"""v0.2 B8 acceptance (B8-1 event producers + B8-5 experiment C).

Deterministic; no model is called.  What is verified with real code is the three event
producers and the timer-honesty experiment.  Everything else in B8 is reported in
``docs/engineering/v02-autonomous-life-implementation.md`` with its own status.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _path in (str(ROOT / "life" / "scripts"), str(ROOT / "native"), str(ROOT / "native" / "src"),
              str(ROOT / "native" / "memory_runtime_v1"), str(ROOT.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label}: {detail}")


class _Eligibility:
    """LR-4 as the feed sees it: an open set that shrinks when things are consumed."""

    def __init__(self) -> None:
        self.open: list[dict] = []

    def list_eligibilities(self, *, activity_id=None, only_open=False):
        return [dict(row) for row in self.open] if only_open else [dict(row) for row in self.open]


class _Work:
    def __init__(self, rows, *, refuse=False) -> None:
        self.rows = rows
        self.refuse = refuse
        self.head_reads = 0

    def list_artifacts(self):
        if self.refuse:
            raise PermissionError("grant revoked")
        return [dict(row) for row in self.rows]

    def read_head(self, artifact_id):
        self.head_reads += 1
        if self.refuse:
            raise PermissionError("grant revoked")
        row = next((r for r in self.rows if r["artifact_id"] == artifact_id), None)
        return {"version_id": (row or {}).get("head_version_id")} if row else None


def main() -> int:
    import autonomous_life_events as ev

    with tempfile.TemporaryDirectory(prefix="v02-b8-") as raw:
        tmp = Path(raw)

        # ---- B8-1.1 LR-4 resume eligibility --------------------------------------
        print("[1] a newly OPEN resume eligibility wakes once")
        coordinator = _Eligibility()
        feed = ev.ResumeEligibilityFeed(coordinator, tmp / "a")
        check(feed.poll() == [], "nothing open -> no event")
        coordinator.open = [{"eligibility_id": "relig:1", "activity_id": "actv_1",
                             "eligible_at": "2026-10-11T09:00:00+00:00"}]
        first = feed.poll()
        check([e["kind"] for e in first] == [ev.EVENT_RESUME_ELIGIBILITY_OPENED],
              "a new open eligibility is one event", first)
        check(feed.poll() == [], "the same eligibility does not fire again")
        coordinator.open = []
        check(feed.poll() == [], "a consumed/expired eligibility fires nothing")
        coordinator.open = [{"eligibility_id": "relig:2", "activity_id": "actv_1"}]
        check(len(feed.poll()) == 1, "a different eligibility is a new event")
        # Restart: a new feed over the same state root remembers what it consumed.
        restarted = ev.ResumeEligibilityFeed(coordinator, tmp / "a")
        check(restarted.poll() == [],
              "after a restart the already-notified eligibility is not re-notified")

        # ---- B8-1.2 artifact head -------------------------------------------------
        print("[2] a head that moved without us wakes; our own edit does not")
        work = _Work([{"artifact_id": "artifact:a", "lifecycle": "ACTIVE",
                       "head_version_id": "artifact-version:v1"}])
        artifacts = ev.ArtifactChangeFeed(work, tmp / "b")
        check(artifacts.poll() == [], "the first sighting is not news")
        work.rows[0]["head_version_id"] = "artifact-version:v2"
        changed = artifacts.poll()
        check([e["kind"] for e in changed] == [ev.EVENT_ARTIFACT_CHANGED],
              "another writer's head change is one event", changed)
        check(artifacts.poll() == [], "the same head observed again is not an event")
        artifacts.note_own_write("artifact:a", "artifact-version:v3")
        work.rows[0]["head_version_id"] = "artifact-version:v3"
        check(artifacts.poll() == [],
              "our own write is recorded as ours and does not wake us again")
        revoked = _Work([{"artifact_id": "artifact:a", "lifecycle": "ACTIVE",
                          "head_version_id": "artifact-version:v3"}], refuse=True)
        revoked_feed = ev.ArtifactChangeFeed(revoked, tmp / "b2")
        revoked_feed.poll()
        errors = revoked_feed.poll()
        check([e["kind"] for e in errors] == ["FEED_UNAVAILABLE"],
              "a revoked grant is a feed error, never a change", errors)
        check("PermissionError" in errors[0]["detail"],
              "and it says why", errors[0]["detail"])

        # ---- B8-1.3 world opportunity --------------------------------------------
        print("[3] only a change in what the World offers is an event")
        state = {"rows": [{"world_revision_ref": "wrev:shiomi_city:r1",
                           "target_refs": ["place:nearby_street"]}]}
        world = ev.WorldOpportunityFeed(lambda: list(state["rows"]), tmp / "c")
        check(world.poll() == [], "the first observation is not news")
        check(world.poll() == [], "the same reachable set is not an event")
        state["rows"] = [{"world_revision_ref": "wrev:shiomi_city:r2",
                          "target_refs": ["place:home", "place:school"]}]
        changed = world.poll()
        check([e["kind"] for e in changed] == [ev.EVENT_WORLD_OPPORTUNITY],
              "a changed reachable set is one event", changed)

        # ---- B8-1.4 the feeds reach the runner -----------------------------------
        print("[4] the feeds reach the runner, and a repeat still does not double-fire")
        import autonomous_life_cycle as alc
        import autonomous_life_runner as alr
        import life_integration_ag2 as ag2

        class _Registry:
            def list_adapters(self):
                return []

        class _Runtime:
            def __init__(self, state_root):
                self.state_root = state_root
                self.kill_switches = ag2.LifeRuntimeKillSwitches(
                    life_runtime_enabled=True, agency_enabled=True,
                    action_execution_enabled=True, proactive_enabled=False)
                self.candidate_registry = _Registry()
                self.action_read = type("R", (), {"list_actions": lambda self, status=None: []})()
                self.action_command = type("C", (), {"reconcile_action": lambda self, **k: {}})()

            def canonical_state(self):
                return {"foreground_activity_ref": None, "activities": {}}

        root = tmp / "runner"
        root.mkdir(parents=True, exist_ok=True)
        cycle = alc.AutonomousLifeCycle(_Runtime(root), enabled=True, max_rounds_per_day=8,
                                       env={"CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY": "12"})
        runner = alr.AutonomousLifeRunner(cycle, state_root=root, interval_minutes=120)
        poller = ev.EventFeedPoller([feed])
        coordinator.open = [{"eligibility_id": "relig:3", "activity_id": "actv_1"}]
        records = poller.wake(runner)
        check(len(records) == 1 and records[0].status == alr.WAKE_IDLE_NO_CANDIDATE,
              "a real eligibility event opens exactly one window", [r.status for r in records])
        check(poller.wake(runner) == [],
              "polling again with nothing new opens no window")

        # ---- B8-5 experiment C: a timer must not fake proactivity -----------------
        print("[5] experiment C: empty check windows fabricate nothing")
        budget_before = cycle.budget.snapshot()
        empty_runtime = _Runtime(tmp / "timer")
        (tmp / "timer").mkdir(parents=True, exist_ok=True)
        timer_cycle = alc.AutonomousLifeCycle(empty_runtime, enabled=True,
                                              max_rounds_per_day=8)
        timer_runner = alr.AutonomousLifeRunner(timer_cycle, state_root=tmp / "timer",
                                                interval_minutes=120)
        statuses = []
        clock = datetime.now(timezone.utc)
        for index in range(4):
            statuses.append(timer_runner.run_due(
                now=clock + timedelta(minutes=121 * index)).status)
        check(all(s == alr.WAKE_IDLE_NO_CANDIDATE for s in statuses),
              "four windows all report silence", statuses)
        check(timer_cycle.budget.cognition_used_today() == 0,
              "no model call was made or charged", timer_cycle.budget.cognition_used_today())
        check(timer_cycle.budget.actions_used_today() == 0,
              "no action was spent", timer_cycle.budget.actions_used_today())
        check(not (tmp / "timer" / "life_step_journal.jsonl").exists(),
              "no artifact version and no step were produced",
              sorted(p.name for p in (tmp / "timer").iterdir()))
        check(not (tmp / "timer" / "autonomous_event_feeds.json").exists()
              or "experience" not in (tmp / "timer" / "autonomous_event_feeds.json").read_text(),
              "no fabricated experience was recorded")
        check(budget_before == cycle.budget.snapshot(),
              "the earlier window's budget was not disturbed")

    print()
    if FAILURES:
        print(f"B8_CHECK: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
