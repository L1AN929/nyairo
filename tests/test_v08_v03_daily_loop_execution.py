#!/usr/bin/env python3
"""v0.8 Wave 5B: 0.3 daily-loop execution over the real owners (0 product-model calls).

Covers, against the real ``v02_v03_daily_loop_bridge`` / ecology / clock / intimacy
owners:

* illness gentle recovery: ``v03_care:bed_rest_block`` / ``v03_care:warm_hydration``
  selected, settled, idempotent on replay and repeatable under a new ``decision_id``;
* the legal meaningless idle ``v03_idle:daydream_window`` (35B §9.1): zero productivity
  penalty, no artifact / share / forced summary;
* independent work ``v03_job:manuscript_fee`` / ``v03_job:proofreading_shift``: one
  credit per ``decision_id``, replay idempotent, a new decision really earns again;
* affordable purchases (``purchase_cue:*`` / ``market_wish:*``): debit + deliberation
  receipt, over-budget cues filtered / refused, never a negative balance;
* 36A §11.1 / 36B-4 §9 adult-intimacy entry checkpoint: saved, degraded by STOP and by
  IDLE_TIMEOUT, discovered by the daily collector, consumed once, replay idempotent;
* the real ``AutonomousLifeCycle.evaluate_once`` + ``AutonomousLifeResident.tick`` seam
  with a scripted (zero-LLM) cognition for care / work / intimacy-resume.

Run:  python3 tests/test_v08_v03_daily_loop_execution.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.phone import adult_intimacy_view as intimacy_view  # noqa: E402
from components.phone import v03_ecology_view as ecology_view  # noqa: E402
from components.phone.outing_places_view import write_routes_market_state  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco_engine  # noqa: E402

T0 = "2026-11-15T09:00:00+08:00"
ECO_SCHEMA = "v03.ecology.bridge.v1"


def _eco_doc(*, phase: str = "healthy", severity: float = 0.0) -> dict:
    return {
        "schema": ECO_SCHEMA,
        "schema_version": 1,
        "revision": 0,
        "illness": {
            "phase": phase,
            "severity": severity,
            "strain_load": 0.5 if phase != "healthy" else 0.0,
            "rest_active": False,
        },
    }


def _read_eco_state(root: Path) -> dict:
    state, reason = eco_engine.read_stored_ecology_zero_write(root)
    assert reason is None, reason
    return state


def _economy(root: Path, now_iso: str = T0) -> dict:
    proj = eco_engine.project_v03_ecology(
        root, has_subject_capability=True, now_iso=now_iso
    )
    return dict(proj["data"]["economy"])


def _illness(root: Path, now_iso: str = T0) -> dict:
    proj = eco_engine.project_v03_ecology(
        root, has_subject_capability=True, now_iso=now_iso
    )
    return dict(proj["data"]["illness"])


class _V03DailyBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-v03-daily-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- seeding ------------------------------------------------------------------
    def write_ecology(self, *, phase: str = "healthy", severity: float = 0.0) -> None:
        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps(_eco_doc(phase=phase, severity=severity)), encoding="utf8"
        )

    def write_purchase_cues(self, items: list[dict]) -> None:
        (self.root / "phone" / "v03_purchase_cues.json").write_text(
            json.dumps({"items": items}, ensure_ascii=False), encoding="utf8"
        )

    def set_savings_goal(self, *, goal_id: str = "goal_camera") -> None:
        code, body = ecology_view.mutate_v03_ecology_view(
            self.root,
            {
                "action": "set_savings_goal",
                "goal_id": goal_id,
                "item_title": "修好的旧相机",
                "target_cents": 20_000,
                "operation_key": f"seed_{goal_id}",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("ok"), body)

    # -- collection / settlement ---------------------------------------------------
    def collect(self, now_iso: str = T0) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    @staticmethod
    def descriptor(collected: dict, source_ref: str) -> dict:
        for desc in collected.get("descriptors", {}).values():
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    @staticmethod
    def source_refs(collected: dict) -> set[str]:
        return {
            str(desc.get("source_ref") or "")
            for desc in collected.get("descriptors", {}).values()
        }

    @staticmethod
    def event_for(collected: dict, source_ref: str, *, decision_id: str) -> dict:
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id=decision_id
        )
        assert isinstance(event, dict), source_ref
        return event

    def settle(self, event: dict, now_iso: str = T0) -> dict:
        return bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=now_iso
        )


class CareAndIdleTest(_V03DailyBase):
    def test_01_illness_care_settles_replays_and_repeats_across_decisions(self) -> None:
        self.write_ecology(phase="acute_illness", severity=0.6)
        collected = self.collect()
        refs = self.source_refs(collected)
        self.assertIn("v03_care:bed_rest_block", refs)
        self.assertIn("v03_care:warm_hydration", refs)

        bed = self.descriptor(collected, "v03_care:bed_rest_block")
        self.assertEqual(bed["action_family"], "illness_rest_recovery")
        self.assertFalse(bed["requires_outing"])
        self.assertFalse(bed["creative_intensive"])
        self.assertEqual(bed["care_event_kind"], "bed_rest_block")

        before = _illness(self.root)
        first = self.settle(
            self.event_for(collected, "v03_care:bed_rest_block", decision_id="care-1")
        )
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("v03_ecology.illness", first["slices_updated"])
        self.assertIn("rest_event", first["details"])
        self.assertIn("illness_recovery", first["details"])
        after = _illness(self.root)
        self.assertTrue(after["rest_active"])
        self.assertLess(after["strain_load"], before["strain_load"])

        # The same Decision replayed is a cached no-op (no second recovery write).
        revision_after_first = _read_eco_state(self.root)["revision"]
        replay = self.settle(
            self.event_for(collected, "v03_care:bed_rest_block", decision_id="care-1")
        )
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(_read_eco_state(self.root)["revision"], revision_after_first)

        # A new Decision is a new logical care act and really settles again.
        second = self.settle(
            self.event_for(collected, "v03_care:bed_rest_block", decision_id="care-2"),
            now_iso="2026-11-15T09:30:00+08:00",
        )
        self.assertEqual(second["status"], "APPLIED", second)
        self.assertGreater(
            _read_eco_state(self.root)["revision"], revision_after_first
        )

        # Warm hydration is the second gentle-recovery command and records hydration.
        hydration = self.settle(
            self.event_for(
                collected, "v03_care:warm_hydration", decision_id="care-water-1"
            ),
            now_iso="2026-11-15T10:00:00+08:00",
        )
        self.assertEqual(hydration["status"], "APPLIED", hydration)
        self.assertIsNotNone(_illness(self.root, "2026-11-15T10:00:00+08:00")[
            "last_hydration_at"
        ])
        self.assertEqual(hydration.get("llm_calls", 0), 0)

    def test_02_meaningless_idle_is_legal_zero_penalty_and_repeatable(self) -> None:
        # A single real 0.3 slice is enough to open a legal idle window.
        self.write_ecology()
        collected = self.collect()
        refs = self.source_refs(collected)
        self.assertIn("v03_idle:daydream_window", refs)
        idle = self.descriptor(collected, "v03_idle:daydream_window")
        self.assertEqual(idle["idle_kind"], "daydream_window")
        self.assertTrue(idle["zero_productivity_penalty"])
        self.assertFalse(idle["creates_artifact"])
        self.assertFalse(idle["creates_share"])
        self.assertFalse(idle["requires_outing"])

        first = self.settle(
            self.event_for(collected, "v03_idle:daydream_window", decision_id="idle-1")
        )
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("v03_clock.meaningless_idle", first["slices_updated"])
        idle_receipt = first["details"]["meaningless_idle"]
        self.assertEqual(idle_receipt["idle_kind"], "daydream_window")
        self.assertEqual(idle_receipt["productivity_penalty"], 0)
        self.assertEqual(idle_receipt["growth_score_delta"], 0)
        self.assertFalse(idle_receipt["created_artifact"])
        self.assertFalse(idle_receipt["created_share"])
        self.assertFalse(idle_receipt["forced_summary_generated"])
        self.assertFalse(idle_receipt["justification_required"])
        clock_state, exists = _read_clock_doc(self.root)
        self.assertTrue(exists)
        self.assertTrue(clock_state)

        replay = self.settle(
            self.event_for(collected, "v03_idle:daydream_window", decision_id="idle-1")
        )
        self.assertTrue(replay.get("already_applied"), replay)
        again = self.settle(
            self.event_for(collected, "v03_idle:daydream_window", decision_id="idle-2"),
            now_iso="2026-11-15T10:15:00+08:00",
        )
        self.assertEqual(again["status"], "APPLIED", again)
        self.assertNotEqual(
            again["details"]["meaningless_idle"]["idle_id"],
            idle_receipt["idle_id"],
        )


def _read_clock_doc(root: Path) -> tuple[dict, bool]:
    path = root / "phone" / "v03_clock_state.json"
    if not path.is_file():
        return {}, False
    return json.loads(path.read_text(encoding="utf8")), True


class IndependentWorkTest(_V03DailyBase):
    def test_01_work_credits_once_per_decision_and_repeats_across_decisions(self) -> None:
        self.set_savings_goal()
        collected = self.collect()
        refs = self.source_refs(collected)
        self.assertIn("v03_job:manuscript_fee", refs)
        self.assertIn("v03_job:proofreading_shift", refs)
        manuscript = self.descriptor(collected, "v03_job:manuscript_fee")
        self.assertEqual(manuscript["action_family"], "independent_work")
        self.assertTrue(manuscript["creative_intensive"])
        self.assertFalse(manuscript["requires_outing"])
        payout = int(manuscript["payout_cents"])
        self.assertGreater(payout, 0)

        start_balance = _economy(self.root)["available_balance_cents"]
        first = self.settle(
            self.event_for(collected, "v03_job:manuscript_fee", decision_id="job-1")
        )
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("v03_ecology.economy", first["slices_updated"])
        work = first["details"]["independent_work"]
        self.assertEqual(work["available_balance_cents"], start_balance + payout)
        credits = [
            row
            for row in _read_eco_state(self.root)["economy"]["ledger_entries"]
            if row["entry_kind"] == "INDEPENDENT_WORK_CREDIT"
        ]
        self.assertEqual(len(credits), 1)

        # Same decision replayed: cached, no second credit.
        replay = self.settle(
            self.event_for(collected, "v03_job:manuscript_fee", decision_id="job-1")
        )
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(
            len([
                row
                for row in _read_eco_state(self.root)["economy"]["ledger_entries"]
                if row["entry_kind"] == "INDEPENDENT_WORK_CREDIT"
            ]),
            1,
        )
        self.assertEqual(_economy(self.root)["available_balance_cents"], start_balance + payout)

        # A fresh round / day with a new Decision really earns again.
        second = self.settle(
            self.event_for(collected, "v03_job:manuscript_fee", decision_id="job-2"),
            now_iso="2026-11-16T09:00:00+08:00",
        )
        self.assertEqual(second["status"], "APPLIED", second)
        self.assertEqual(
            _economy(self.root, "2026-11-16T09:00:00+08:00")["available_balance_cents"],
            start_balance + payout * 2,
        )

        # The routine proofreading shift is its own job candidate and settles too.
        shift = self.settle(
            self.event_for(
                collected, "v03_job:proofreading_shift", decision_id="job-3"
            ),
            now_iso="2026-11-16T13:00:00+08:00",
        )
        self.assertEqual(shift["status"], "APPLIED", shift)
        shift_payout = int(
            self.descriptor(collected, "v03_job:proofreading_shift")["payout_cents"]
        )
        self.assertEqual(
            _economy(self.root, "2026-11-16T13:00:00+08:00")["available_balance_cents"],
            start_balance + payout * 2 + shift_payout,
        )
        self.assertFalse(shift["details"]["independent_work"]["receipt"] is None)
        self.assertEqual(shift.get("llm_calls", 0), 0)


class AffordablePurchaseTest(_V03DailyBase):
    def test_01_purchase_cue_debits_affordable_and_filters_over_budget(self) -> None:
        self.write_ecology()
        self.write_purchase_cues([
            {"cue_id": "tin_mug", "item_title": "搪瓷小杯", "price_cents": 1200},
            {"cue_id": "too_pricey", "item_title": "太贵的摆件", "price_cents": 9000},
        ])
        collected = self.collect()
        refs = self.source_refs(collected)
        self.assertIn("purchase_cue:tin_mug", refs)
        self.assertNotIn("purchase_cue:too_pricey", refs)
        filtered = [
            row
            for row in collected["gating"]["filtered_candidates"]
            if row.get("source_ref") == "purchase_cue:too_pricey"
        ]
        self.assertTrue(filtered)
        self.assertEqual(filtered[0]["reason_code"], "BUDGET_TIGHT_IMPULSE_FILTERED")
        self.assertEqual(filtered[0]["verdict"], "put_back_tight_budget")

        cue = self.descriptor(collected, "purchase_cue:tin_mug")
        self.assertEqual(cue["purchase_verdict"], "afford_independently")
        start_balance = _economy(self.root)["available_balance_cents"]

        bought = self.settle(
            self.event_for(collected, "purchase_cue:tin_mug", decision_id="buy-1")
        )
        self.assertEqual(bought["status"], "APPLIED", bought)
        self.assertIn("v03_ecology.economy", bought["slices_updated"])
        receipt = bought["details"]["purchase"]
        self.assertEqual(receipt["verdict"], "afford_independently")
        self.assertFalse(receipt["put_back_on_shelf"])
        self.assertTrue(receipt.get("purchase_transaction_id"))
        self.assertEqual(
            _economy(self.root)["available_balance_cents"], start_balance - 1200
        )
        state = _read_eco_state(self.root)
        debits = [
            row
            for row in state["economy"]["ledger_entries"]
            if row["entry_kind"] == "INDEPENDENT_PURCHASE_DEBIT"
        ]
        self.assertEqual(len(debits), 1)
        self.assertEqual(debits[0]["item_title"], "搪瓷小杯")
        self.assertEqual(len(state["economy"]["budget_deliberations"]), 1)
        self.assertTrue(state["economy"]["budget_deliberations"][0]["purchased_independently"])

        # Replay is a cached no-op: still exactly one debit.
        replay = self.settle(
            self.event_for(collected, "purchase_cue:tin_mug", decision_id="buy-1")
        )
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(
            len([
                row
                for row in _read_eco_state(self.root)["economy"]["ledger_entries"]
                if row["entry_kind"] == "INDEPENDENT_PURCHASE_DEBIT"
            ]),
            1,
        )

        # An unaffordable command is refused by the real owner, never overdrawing.
        forged = {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "source_ref": "purchase_cue:too_pricey",
            "receipt_ref": "v02v03:test:overbudget",
            "decision_id": "buy-bad",
            "action_kind": "COMMIT_AFFORDABLE_PURCHASE",
            "item_title": "太贵的摆件",
            "price_cents": 9000,
            "verdict": "afford_independently",
            "commit_spend": True,
            "item_id": "too_pricey",
            "operation_key": "test_overbudget_purchase",
        }
        refused = self.settle(forged)
        self.assertEqual(refused["status"], "REFUSED", refused)
        self.assertEqual(refused["reason_code"], "INSUFFICIENT_INDEPENDENT_FUNDS")
        self.assertEqual(refused["optimistic_writes"], 0)
        self.assertEqual(
            len([
                row
                for row in _read_eco_state(self.root)["economy"]["ledger_entries"]
                if row["entry_kind"] == "INDEPENDENT_PURCHASE_DEBIT"
            ]),
            1,
        )
        self.assertGreaterEqual(_economy(self.root)["available_balance_cents"], 0)
        self.assertEqual(refused.get("llm_calls", 0), 0)

    def test_02_market_wish_is_an_affordable_purchase_candidate(self) -> None:
        self.write_ecology()
        write_routes_market_state(self.root, {
            "schema_version": 1,
            "revision": 0,
            "updated_at": None,
            "routes": [],
            "market_wishes": [{
                "wish_id": "wish_card",
                "stall_code": "sketch_postcard_rack",
                "stall_title": "水彩风景明信片",
                "memo": "想收一张河岸明信片",
                "budget_cap_jpy": 600,
                "shared": False,
                "updated_at": None,
            }],
            "partner_market_tip": None,
        })
        collected = self.collect()
        self.assertIn("market_wish:wish_card", self.source_refs(collected))
        wish = self.descriptor(collected, "market_wish:wish_card")
        self.assertEqual(wish["price_cents"], 600)
        self.assertEqual(wish["item_title"], "水彩风景明信片")

        start_balance = _economy(self.root)["available_balance_cents"]
        bought = self.settle(
            self.event_for(collected, "market_wish:wish_card", decision_id="wish-1")
        )
        self.assertEqual(bought["status"], "APPLIED", bought)
        self.assertEqual(
            _economy(self.root)["available_balance_cents"], start_balance - 600
        )
        state = _read_eco_state(self.root)
        self.assertEqual(len(state["economy"]["budget_deliberations"]), 1)
        delib = state["economy"]["budget_deliberations"][0]
        self.assertTrue(delib["purchased_independently"])
        self.assertTrue(delib["purchase_transaction_id"])
        self.assertEqual(bought.get("llm_calls", 0), 0)


# ---------------------------------------------------------------------------
# Adult-intimacy entry checkpoint (36A §11.1 / 36B-4 §9)
# ---------------------------------------------------------------------------

INTIMACY_T0 = datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc)


def _open_ready_intimacy_env(root: Path) -> None:
    from tests.test_adult_intimacy_view import _open_ready_env

    _open_ready_env(root, now=INTIMACY_T0)


def _open_active_scene(root: Path, *, tag: str) -> str:
    proposed = intimacy_view.apply_adult_intimacy_action(
        root,
        {
            "action": "propose_scene",
            "operation_key": f"{tag}_proposal",
            "proposer": "partner",
            "categories": ["KISSING"],
        },
        now=INTIMACY_T0,
    )
    permit_id = proposed["action_receipt"]["permit_id"]
    intimacy_view.apply_adult_intimacy_action(
        root,
        {
            "action": "respond_scene",
            "permit_id": permit_id,
            "responder": "subject",
            "decision": "ACCEPT",
        },
        now=INTIMACY_T0,
    )
    return str(permit_id)


def _permit_rec(root: Path, permit_id: str) -> dict:
    state = json.loads(
        intimacy_view.adult_intimacy_state_path(root).read_text(encoding="utf8")
    )
    return state["permits"]["records"][permit_id]


class IntimacyCheckpointTest(_V03DailyBase):
    def test_01_saved_stop_degrades_then_daily_loop_consumes_once(self) -> None:
        _open_ready_intimacy_env(self.root)
        permit_id = _open_active_scene(self.root, tag="stop")
        # respond_scene's default behaviour stays compatible: no activity ref yet.
        self.assertFalse(_permit_rec(self.root, permit_id)["checkpoint"]["resumable"])
        self.assertFalse(_permit_rec(self.root, permit_id)["checkpoint"]["saved"])

        intimacy_view.apply_adult_intimacy_action(
            self.root,
            {
                "action": "save_activity_checkpoint",
                "permit_id": permit_id,
                "activity_ref": "life_activity:reading_nook",
                "step_summary": "读到一半",
                "activity_title": "窗边读书",
            },
            now=INTIMACY_T0,
        )
        checkpoint = _permit_rec(self.root, permit_id)["checkpoint"]
        self.assertTrue(checkpoint["saved"])
        self.assertEqual(checkpoint["activity_ref"], "life_activity:reading_nook")
        self.assertFalse(checkpoint["resumable"])

        # A real stop degrades the permit; the saved entry checkpoint becomes resumable.
        intimacy_view.apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": permit_id, "by": "subject"},
            now=INTIMACY_T0 + timedelta(seconds=30),
        )
        self.assertTrue(_permit_rec(self.root, permit_id)["checkpoint"]["resumable"])
        self.assertTrue(bridge.has_v02_v03_active_state(self.root))

        collected = bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso=INTIMACY_T0.isoformat()
        )
        src = f"v03_intimacy_resume:{permit_id}"
        resume = self.descriptor(collected, src)
        self.assertEqual(resume["permit_id"], permit_id)
        self.assertEqual(resume["activity_ref"], "life_activity:reading_nook")
        self.assertEqual(resume["action_family"], "RESUME_INTIMACY_CHECKPOINT")
        self.assertFalse(resume["requires_outing"])

        event = self.event_for(collected, src, decision_id="resume-1")
        self.assertEqual(event["action_kind"], "RESUME_INTIMACY_CHECKPOINT")
        settled = self.settle(event)
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn("phone.adult_intimacy", settled["slices_updated"])
        resumed = settled["details"]["intimacy_resumes"]
        self.assertEqual(resumed["resumed_activity_ref"], "life_activity:reading_nook")
        self.assertFalse(resumed["already_consumed"])
        self.assertEqual(resumed["decision_id"], "resume-1")
        final = _permit_rec(self.root, permit_id)["checkpoint"]
        self.assertTrue(final["consumed"])
        self.assertFalse(final["resumable"])
        self.assertEqual(final["resumed_by_decision_id"], "resume-1")

        # Once consumed the checkpoint is never offered as a candidate again.
        again = bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso=INTIMACY_T0.isoformat()
        )
        self.assertNotIn(src, self.source_refs(again))

        # Replay (same or new decision) is an idempotent no-op, never a second consume.
        replay = self.settle(event)
        self.assertTrue(replay.get("already_applied"), replay)
        other_decision = self.settle(
            bridge.v02_v03_round_event_for_selected_candidate(
                collected, src, decision_id="resume-2"
            )
        )
        self.assertTrue(other_decision.get("already_applied"), other_decision)
        self.assertEqual(
            _permit_rec(self.root, permit_id)["checkpoint"]["resumed_by_decision_id"],
            "resume-1",
        )
        self.assertEqual(settled.get("llm_calls", 0), 0)

    def test_02_idle_timeout_degrades_scene_and_checkpoint_becomes_resumable(self) -> None:
        _open_ready_intimacy_env(self.root)
        permit_id = _open_active_scene(self.root, tag="idle")
        intimacy_view.apply_adult_intimacy_action(
            self.root,
            {
                "action": "save_activity_checkpoint",
                "permit_id": permit_id,
                "activity_ref": "life_activity:window_tea",
            },
            now=INTIMACY_T0,
        )

        # 600s idle window elapses: the next beat degrades the permit to PAUSED/IDLE_TIMEOUT.
        with self.assertRaises(intimacy_view.AdultIntimacyConflictError):
            intimacy_view.apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": permit_id,
                    "operation_key": "idle_beat",
                    "categories": ["KISSING"],
                },
                now=INTIMACY_T0 + timedelta(seconds=605),
            )
        rec = _permit_rec(self.root, permit_id)
        self.assertEqual(rec["state"], "PAUSED")
        self.assertEqual(rec["pause_reason"], "IDLE_TIMEOUT")
        self.assertTrue(rec["checkpoint"]["resumable"])
        self.assertEqual(rec["checkpoint"]["activity_ref"], "life_activity:window_tea")

        collected = bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso=INTIMACY_T0.isoformat()
        )
        src = f"v03_intimacy_resume:{permit_id}"
        self.assertIn(src, self.source_refs(collected))
        settled = self.settle(self.event_for(collected, src, decision_id="idle-resume"))
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertTrue(_permit_rec(self.root, permit_id)["checkpoint"]["consumed"])
        self.assertFalse(_permit_rec(self.root, permit_id)["checkpoint"]["resumable"])


# ---------------------------------------------------------------------------
# Real cycle -> resident seam (POSIX only: the fixture uses real Supply/World
# owner sockets, exactly like ``test_one_day_life``).
# ---------------------------------------------------------------------------

try:  # pragma: no cover - platform gate only
    from test_one_day_life import CycleTest as _CycleTest, StubWriter, posix_only
    import agency_decision_ag1 as ag1
    import autonomous_life_assembly as assembly
    import autonomous_life_cycle as alc
    import autonomous_life_resident as resident
    import autonomous_life_runner as life_runner
    import one_day_life as odl

    _FIXTURE_ERROR = None
except Exception as _exc:  # noqa: BLE001 - native Windows has no fcntl / AF_UNIX
    _FIXTURE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
    _CycleTest = unittest.TestCase
    posix_only = unittest.skipIf(True, "real owner fixture needs POSIX")

needs_fixture = unittest.skipIf(
    globals().get("_FIXTURE_ERROR") is not None,
    "real owner fixture unavailable: %s" % globals().get("_FIXTURE_ERROR", ""),
)


class _ScriptedCognition:
    """Zero-product-model decision: only ever selects a candidate it was shown."""

    def __init__(self, *, select_prefix: str) -> None:
        self.select_prefix = select_prefix
        self.call_count = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        for cand in candidates:
            targets = [str(ref) for ref in (cand.get("target_refs") or [])]
            if any(ref.startswith(self.select_prefix) for ref in targets):
                chosen = cand
                break
        if chosen is not None:
            raw = {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": chosen.get("candidate_id"),
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 顺着这件真实的日常小事走一小步。",
                "deferred_candidate_ids": [],
            }
        else:
            raw = {
                "decision": ag1.DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 今天先不动这一件。",
                "deferred_candidate_ids": [],
            }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count,
            model_id="stub:v08-wave5b",
            model_version="v08-wave5b-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class CycleResidentDailySeamTest(_CycleTest):
    """Real cycle + real resident tick, scripted cognition, real 0.3 owner writes."""

    def _build(self, cognition):
        data = self._prepared_instance()
        env = self.instance_env()
        writer = StubWriter()
        runtime, port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=data["subject_id"],
            writer=writer, enabled=True, env=env,
        )
        return data, env, cycle

    def _instance_root(self) -> Path:
        return Path(self.instance)

    def _tick_built(self, env, cycle, now_iso: str) -> dict:
        fixed = datetime.fromisoformat(now_iso)
        runner_obj = life_runner.AutonomousLifeRunner(
            cycle, state_root=odl.life_state_root(self.instance), env=env,
            clock=lambda: fixed, interval_minutes=0,
        )
        ticked = resident.AutonomousLifeResident(
            state_root=odl.life_state_root(self.instance), environment=env, act=True,
            clock=lambda: fixed, sleeper=lambda _seconds: None, monotonic=lambda: 0.0,
        )
        ticked.assembled = {
            "state": assembly.READY, "runner": runner_obj, "cycle": cycle,
        }
        try:
            return ticked.tick()
        finally:
            ticked.stop()

    def test_a_care_is_selected_and_settled_by_the_resident(self) -> None:
        now_iso = "2026-11-15T09:00:00+00:00"
        cognition = _ScriptedCognition(select_prefix="v03_care:bed_rest_block")
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        (inst / "phone" / "v03_ecology.json").write_text(
            json.dumps(_eco_doc(phase="acute_illness", severity=0.6)), encoding="utf8"
        )
        before = _illness(inst, now_iso)

        record = self._tick_built(env, cycle, now_iso)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)
        self.assertEqual(cognition.call_count, 1)
        after = _illness(inst, now_iso)
        self.assertTrue(after["rest_active"])
        self.assertLess(after["strain_load"], before["strain_load"])
        bridge_view = record.get("v02_v03_bridge") or {}
        settlements = bridge_view.get("settlements") or []
        self.assertTrue(settlements, bridge_view)
        self.assertTrue(
            any("v03_ecology.illness" in (row.get("slices_updated") or [])
                or row.get("already_applied")
                for row in settlements),
            settlements,
        )

    def test_b_work_is_selected_credited_and_replayed_idempotently(self) -> None:
        now_iso = "2026-11-15T09:00:00+00:00"
        cognition = _ScriptedCognition(select_prefix="v03_job:manuscript_fee")
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        code, body = ecology_view.mutate_v03_ecology_view(
            inst,
            {
                "action": "set_savings_goal",
                "goal_id": "seam_goal",
                "item_title": "修好的旧相机",
                "target_cents": 20_000,
                "operation_key": "seam_goal_seed",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        start_balance = _economy(inst, now_iso)["available_balance_cents"]

        record = self._tick_built(env, cycle, now_iso)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)
        extra = due.get("extra") or {}
        decision_id = str(extra.get("decision_id") or "")
        self.assertTrue(decision_id, due)
        after_balance = _economy(inst, now_iso)["available_balance_cents"]
        self.assertGreater(after_balance, start_balance)
        credited = [
            row
            for row in _read_eco_state(inst)["economy"]["ledger_entries"]
            if row["entry_kind"] == "INDEPENDENT_WORK_CREDIT"
        ]
        self.assertEqual(len(credited), 1)
        payout = int(credited[0]["amount_cents"])

        # The Resident's own replay shares the Cycle's operation key: one credit only.
        collected = bridge.collect_v02_v03_loop_candidates(inst, now_iso=now_iso)
        replay = bridge.settle_v02_v03_round_consequences(
            inst,
            bridge.v02_v03_round_event_for_selected_candidate(
                collected, "v03_job:manuscript_fee", decision_id=decision_id
            ),
            now_iso=now_iso,
        )
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(_economy(inst, now_iso)["available_balance_cents"], after_balance)

        # A new Decision really earns again.
        second = bridge.settle_v02_v03_round_consequences(
            inst,
            bridge.v02_v03_round_event_for_selected_candidate(
                collected, "v03_job:manuscript_fee", decision_id="seam_job_2"
            ),
            now_iso=now_iso,
        )
        self.assertEqual(second["status"], "APPLIED", second)
        self.assertEqual(
            _economy(inst, now_iso)["available_balance_cents"], after_balance + payout
        )

    def test_c_intimacy_checkpoint_is_discovered_and_consumed_by_the_resident(self) -> None:
        from test_adult_intimacy_view import T0 as intimacy_now

        now_iso = intimacy_now.isoformat()
        cognition = _ScriptedCognition(select_prefix="v03_intimacy_resume:")
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        _open_ready_intimacy_env(inst)
        permit_id = _open_active_scene(inst, tag="seam")
        intimacy_view.apply_adult_intimacy_action(
            inst,
            {
                "action": "save_activity_checkpoint",
                "permit_id": permit_id,
                "activity_ref": "life_activity:reading_nook",
            },
            now=intimacy_now,
        )
        intimacy_view.apply_adult_intimacy_action(
            inst,
            {"action": "stop_scene", "permit_id": permit_id, "by": "subject"},
            now=intimacy_now + timedelta(seconds=30),
        )
        self.assertTrue(bridge.has_v02_v03_active_state(inst))

        record = self._tick_built(env, cycle, now_iso)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)
        checkpoint = _permit_rec(inst, permit_id)["checkpoint"]
        self.assertTrue(checkpoint["consumed"])
        self.assertFalse(checkpoint["resumable"])
        self.assertEqual(checkpoint["resumed_by_decision_id"], (due.get("extra") or {}).get("decision_id"))
        bridge_view = record.get("v02_v03_bridge") or {}
        self.assertTrue(
            any("phone.adult_intimacy" in (row.get("slices_updated") or [])
                for row in (bridge_view.get("settlements") or [])),
            bridge_view,
        )


if __name__ == "__main__":
    unittest.main()
