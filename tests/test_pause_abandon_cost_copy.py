#!/usr/bin/env python3
"""PAUSE 代价材料：PAUSED 期间正式 ABANDON 不可用，且这句事实能过正式投影。

只钉两件事：

* ``AutonomousLifeCycle.pause_condition_view`` 的既有描述文本写明「PAUSED 期间正式 ABANDON
  不可用，要放弃须在 ACTIVE 时选」，并经 ``agency_cognition_adapter._bounded_pause_condition``
  的白名单投影后仍然保留；无 basis 的视图保持原有 ``available: false`` / ``why_not``。
* 正式 AG-1 ``compute_legal_decisions_for_candidate`` 对最小 PAUSED 前台形状仍不给 ABANDON，
  ACTIVE 对照仍给 ABANDON —— 证明这是判据差异，不是恒假。

不涉及资格/条件/状态/TTL/排序/owner；也不是最终 prompt 已发送或模型已理解的证据。

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_pause_abandon_cost_copy
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(NATIVE),
    str(NATIVE / "src"),
    str(NATIVE / "memory_runtime_v1"),
]

import agency_cognition_adapter as adapter  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_wait_basis as lwb  # noqa: E402

STAMP = "2026-10-30T12:00:00+00:00"
ACT = "actv:pause_cost"
BASIS = {
    "basis_ref": "waitbasis:pause_cost",
    "source": lwb.SOURCE_BODY_REST,
    "activity_ref": ACT,
    "waiting_on_ref": "body:pause_cost",
}


def _candidate() -> ag0.CandidateRecord:
    return ag0.CandidateRecord(
        candidate_id="cand:pause_cost",
        candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
        source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
        source_ref="src:pause_cost",
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=STAMP,
        observed_at=STAMP,
        recorded_at=STAMP,
        idempotency_key="idem:pause_cost",
        activity_ref=ACT,
        causal_parent_refs=["src:pause_cost"],
        provenance_refs=["src:pause_cost"],
    )


def _context(status: str) -> ag1.DecisionContextSnapshot:
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:pause_cost",
        opportunity_ref="dopp:pause_cost",
        candidate_set_ref="cset:pause_cost",
        life_frame_ref="lframe:pause_cost",
        captured_at=STAMP,
        source_revisions={"snapshot": 1},
        snapshot_fingerprint="sfp:pause_cost",
        current_activity_ref=ACT,
        current_activity_status=status,
        current_activity_revision=3,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
    )


def _capacity(candidate: ag0.CandidateRecord) -> ag1.AgencyCapacitySnapshot:
    return ag1.AgencyCapacitySnapshot(
        capacity_snapshot_id="cap:pause_cost",
        captured_at=STAMP,
        source_revisions=["snapshot:pause_cost"],
        candidate_executability={candidate.candidate_id: {"executability": ag1.EXEC_NOW}},
    )


def _legal(candidate, status: str) -> list[str]:
    return list(ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
        candidate, context_snapshot=_context(status), capacity_snapshot=_capacity(candidate)))


class PauseAbandonCostCopyTest(unittest.TestCase):
    def setUp(self):
        self.cycle = alc.AutonomousLifeCycle(
            SimpleNamespace(state_root=tempfile.mkdtemp(prefix="nyairo-pause-abandon-cost-")),
            enabled=False)

    def test_paused_abandon_cost_survives_projection_and_formal_set_still_withholds_it(self):
        projected = adapter._bounded_pause_condition(self.cycle.pause_condition_view(BASIS))
        self.assertIn("ABANDON is not available through the formal decision path",
                      projected["how"])
        self.assertIn("while it is still ACTIVE", projected["how"])
        self.assertIs(projected["keeps_foreground"], False)

        without_basis = adapter._bounded_pause_condition(self.cycle.pause_condition_view(None))
        self.assertFalse(without_basis["available"])
        self.assertEqual(without_basis["why_not"], "NO_OFFERED_CONDITION")
        self.assertNotIn("condition_ref", without_basis)

        candidate = _candidate()
        self.assertNotIn(ag1.DECISION_ABANDON, _legal(candidate, ag1.STATUS_PAUSED))
        self.assertIn(ag1.DECISION_ABANDON, _legal(candidate, ag1.STATUS_ACTIVE))


if __name__ == "__main__":
    unittest.main()
