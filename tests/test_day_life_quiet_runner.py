"""v0.4 一天生活 B（runner 侧）：no-step 是 blocked 而不是 ACTED、安静不花请求、
owner 证据只透传不发明、轮询不再刷屏、以及"关于这个对象的事件才解除抑制"。

只证明机制：``_Cycle`` 是脚本化替身，不调用模型、不写 owner、不联网。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_runner as runner  # noqa: E402

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


class _Budget:
    def __init__(self) -> None:
        self.spent = 0

    def may_start_wake(self):
        return True, "ALLOWED"

    def snapshot(self):
        return {"spent_today": self.spent}


class _Cycle:
    """A scripted cycle: it answers with one fixed outcome and records what it was asked."""

    def __init__(self, answer=None, *, state_root=None) -> None:
        self.state_root = Path(state_root)
        self.enabled = True
        self.budget = _Budget()
        self.answer = dict(answer or {})
        self.calls: list[dict] = []
        self.release_calls: list[dict] = []
        self.release_result = {"released": ["fp:1"], "reason": "EVENT"}

    def evaluate_once(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.answer)

    def release_reconsideration(self, *, refs=(), reason=""):
        self.release_calls.append({"refs": list(refs), "reason": reason})
        return dict(self.release_result)

    def status(self):
        return {"budget": self.budget.snapshot()}


def _clock_at(moment: datetime):
    return lambda: moment


class DayLifeQuietRunnerTest(unittest.TestCase):
    """The runner's classification, its extra block, and the two narrow log/release rules."""

    def _runner(self, answer=None, *, moment=NOW, interval_minutes=60):
        state_root = tempfile.mkdtemp(prefix="nyairov04b-runner-", dir="/tmp")
        cycle = _Cycle(answer, state_root=state_root)
        return runner.AutonomousLifeRunner(
            cycle, state_root=state_root, env={}, interval_minutes=interval_minutes,
            clock=_clock_at(moment)), cycle

    def _journal_rows(self, wake_runner) -> list[dict]:
        path = wake_runner.wake_log
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    # -- 1. no step is a technical refusal, never an action -------------------------

    def test_a_no_step_outcome_is_blocked_and_never_acted(self):
        self.assertEqual(runner.OUTCOME_CLASS[alc.OUTCOME_NO_STEP_AVAILABLE],
                         (runner.WAKE_NO_STEP, "blocked"))
        self.assertNotEqual(runner.WAKE_NO_STEP, runner.WAKE_ACTED)
        # The contrast that matters: "she chose nothing" is still a choice, not a refusal.
        self.assertEqual(runner.OUTCOME_CLASS[alc.OUTCOME_NO_ACTION][1], "choice")

        wake_runner, cycle = self._runner({"outcome": alc.OUTCOME_NO_STEP_AVAILABLE,
                                           "detail": "no page left", "llm_calls": 1})
        record = wake_runner.wake(kind=runner.WAKE_KIND_CHECK_WINDOW, reason="check-window",
                                  trigger_ref="evt:test", now=NOW)

        self.assertEqual(record.status, runner.WAKE_NO_STEP, record.to_dict())
        self.assertEqual(record.status_class, "blocked", record.to_dict())
        self.assertEqual(record.llm_calls, 1, record.to_dict())
        self.assertEqual(wake_runner.wakes_run, 1)
        self.assertEqual(len(cycle.calls), 1)

    # -- 2. a quiet round is silence ------------------------------------------------

    def test_a_quiet_round_is_silence_and_does_not_count_as_a_wake(self):
        wake_runner, _cycle = self._runner({"outcome": alc.OUTCOME_QUIET, "llm_calls": 0,
                                            "detail": "same state already considered"})
        record = wake_runner.wake(kind=runner.WAKE_KIND_CHECK_WINDOW, reason="check-window",
                                  trigger_ref="evt:test", now=NOW)

        self.assertEqual(record.status, runner.WAKE_QUIET, record.to_dict())
        self.assertEqual(record.status_class, "silence", record.to_dict())
        self.assertEqual(record.llm_calls, 0)
        # A window that considered nothing is not a wake: counting it would turn polls into living.
        self.assertEqual(wake_runner.wakes_run, 0)
        self.assertNotEqual(record.status, runner.WAKE_NO_ACTION)

    # -- 3. owner evidence is passed through, never invented -----------------------

    def test_the_owner_evidence_is_passed_through_and_never_invented(self):
        wake_runner, _cycle = self._runner({
            "outcome": alc.OUTCOME_ACTED, "llm_calls": 1, "step_path": "AR0",
            "owner_evidence": {"owner_version_created": True,
                               "owner_version_id": "artifactversion:v7", "step_path": "AR0"}})
        record = wake_runner.wake(kind=runner.WAKE_KIND_EVENT, reason="ARTIFACT_CHANGED",
                                  trigger_ref="artifact:work-1", now=NOW)

        self.assertIs(record.extra["owner_version_created"], True, record.extra)
        self.assertEqual(record.extra["owner_version_id"], "artifactversion:v7", record.extra)
        self.assertEqual(record.extra["step_path"], "AR0", record.extra)

        # A round that produced no owner record says so with ``None`` -- it never guesses one from
        # ``ACTED``, and it never reports ``0`` as if it were a healthy count.
        bare_runner, _bare = self._runner({"outcome": alc.OUTCOME_STEP_UNKNOWN, "llm_calls": 1})
        bare = bare_runner.wake(kind=runner.WAKE_KIND_EVENT, reason="ARTIFACT_CHANGED",
                                trigger_ref="artifact:work-1", now=NOW)
        self.assertIsNone(bare.extra["owner_version_created"], bare.extra)
        self.assertIsNone(bare.extra["owner_version_id"], bare.extra)
        self.assertIsNone(bare.extra["step_path"], bare.extra)

    # -- 4. a poll loop must not bury the real records -----------------------------

    def test_repeated_not_due_polls_do_not_fill_the_wake_journal(self):
        wake_runner, _cycle = self._runner({"outcome": alc.OUTCOME_NO_ACTION, "llm_calls": 1})
        wake_runner.run_due(now=NOW)  # first window: due, and it really considered
        self.assertEqual(len(self._journal_rows(wake_runner)), 1)

        later = NOW + timedelta(minutes=5)
        for _ in range(3):
            record = wake_runner.run_due(now=later)
            self.assertEqual(record.status, runner.WAKE_NOT_DUE, record.to_dict())
        rows = self._journal_rows(wake_runner)
        self.assertEqual(len(rows), 2, rows)  # the real wake + the first NOT_DUE of the streak
        self.assertEqual(rows[-1]["status"], runner.WAKE_NOT_DUE)
        # The in-process view still knows about every window.
        self.assertEqual(wake_runner.last_wake.status, runner.WAKE_NOT_DUE)
        self.assertEqual(wake_runner.wakes_run, 1)

        # A real window after the streak is journalled again.
        due_again = NOW + timedelta(minutes=120)
        wake_runner.run_due(now=due_again)
        self.assertEqual(len(self._journal_rows(wake_runner)), 3)

    # -- 5. only an event about this object lifts the suppression ------------------

    def test_an_event_about_the_quiet_object_releases_the_suppression(self):
        wake_runner, cycle = self._runner({"outcome": alc.OUTCOME_NO_ACTION, "llm_calls": 1})
        record = wake_runner.wake_on_event(
            {"kind": runner.EVENT_ARTIFACT_CHANGED, "ref": "artifact:work-1",
             "occurred_at": NOW.isoformat()}, now=NOW)

        self.assertEqual(cycle.release_calls,
                         [{"refs": ["artifact:work-1"],
                           "reason": "EVENT:%s" % runner.EVENT_ARTIFACT_CHANGED}],
                         cycle.release_calls)
        self.assertEqual(record.extra["reconsideration_released"], ["fp:1"], record.extra)

        # An event this runner does not know is not a reason to spend a request again.
        cycle.release_calls.clear()
        wake_runner.wake_on_event({"kind": "SOMETHING_ELSE", "ref": "artifact:work-1",
                                   "occurred_at": (NOW + timedelta(minutes=1)).isoformat()},
                                  now=NOW + timedelta(minutes=1))
        self.assertEqual(cycle.release_calls, [])


if __name__ == "__main__":
    unittest.main()
