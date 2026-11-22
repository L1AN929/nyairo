#!/usr/bin/env python3
"""v0.8: the health candidate cap really travels lsv -> cycle -> evaluate_opportunity.

Two ends of one wire, no stand-in for either side of the body path:

1. a real ``life_situation_view.read_life_situation`` read whose body column
   carries the adapter's ``body_candidate_cap``; the real
   ``AutonomousLifeCycle.capacity_kwargs`` must return it, and the real
   ``DecisionService.evaluate_opportunity`` signature must accept the expanded
   call (that is the exact call site, ``**capacity_kwargs``).
2. the real v03 illness state file (rain / late nights) read through
   ``WorldBodyService.health_record`` and surfaced on the gateway as ACUTE with
   a narrowed cap -- one illness course, not a second island.

Run:  python3 tests/test_health_cap_wiring.py
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import body_health_projection as bhp  # noqa: E402
import body_runtime as br  # noqa: E402
import life_situation_view as lsv  # noqa: E402
from world_body_gateway_api import GatewaySurface  # noqa: E402
from world_body_service import WorldBodyService  # noqa: E402

MOMENT = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def adapter_view(cap):
    view = {
        "schema_version": "body.capacity.view.v1",
        "kind": "body_capacity_view",
        "source": "world_body_read_port",
        "subject_id": "chiyo.global",
        "home_ref": "home:test",
        "source_outcome": "OK",
        "degraded": False,
        "freshness": "fresh",
        "observed_at": MOMENT.isoformat(),
        "observed_body_id": "chiyo_body",
        "observed_revision": "bodyrev:9",
        "observed_recovery_state": "inactive",
        "identity_bound": True,
        "revision_bound": True,
        "observation_time_bound": True,
        "body_capacity_state": "LIMITED",
        "body_capacity_ref": "bodycap:chiyo_body:bodyrev:9",
        "reason_code": "OK_LIMITED_BY_BODY_SIGNAL",
        "evidence": ["health"],
        "qualitative_only": True,
        "raw_values_exposed": False,
        "read_only": True,
        "reads_performed": 1,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }
    if cap is not None:
        view["body_candidate_cap"] = cap
        view["body_max_plan_steps"] = 2
        view["body_creative_block"] = True
    return view


def budget_view():
    return {
        "state": "AVAILABLE",
        "ref": "requestbudget:2026-11-15",
        "source": "life_request_budget",
        "reason_code": "OK",
        "derived_by": "life_request_budget view()",
        "owner_asserted": False,
        "read_only": True,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
    }


def situation(cap):
    home = tempfile.mkdtemp(prefix="lsw-")
    return lsv.read_life_situation(
        home=home,
        subject_id="chiyo.global",
        now=MOMENT.isoformat(),
        body=adapter_view(cap),
        budget=budget_view(),
    )


class LsvCarriesTheCapTest(unittest.TestCase):
    def test_body_column_keeps_the_cap(self):
        view = situation(3)
        self.assertEqual(view["body"]["candidate_cap"], 3)
        self.assertEqual(
            lsv.ag1_body_limit_kwargs(view),
            {"body_candidate_cap": 3, "body_max_plan_steps": 2, "body_creative_block": True},
        )

    def test_no_cap_adds_nothing(self):
        view = situation(None)
        self.assertIsNone(view["body"]["candidate_cap"])
        self.assertEqual(lsv.ag1_body_limit_kwargs(view), {})

    def test_cycle_capacity_kwargs_carry_it_and_the_real_decision_accepts_it(self):
        view = situation(3)
        cycle = alc.AutonomousLifeCycle(
            NS(state_root=Path(tempfile.mkdtemp(prefix="cyc-"))), enabled=True, env={}
        )
        kwargs = cycle.capacity_kwargs(view)
        self.assertEqual(kwargs["body_candidate_cap"], 3)
        self.assertEqual(kwargs["body_max_plan_steps"], 2)
        self.assertTrue(kwargs["body_creative_block"])
        self.assertEqual(kwargs["body_capacity_state"], "LIMITED")

        # The decision call site expands exactly this dict into the real service.
        signature = inspect.signature(ag1.AgencyDecisionService.evaluate_opportunity)
        signature.bind_partial(**kwargs)  # TypeError if the key is not accepted

    def test_step_cap_truncates_only_multi_step_specs(self):
        import life_integration_ag2 as ag2

        multi = {"steps": [{"n": 1}, {"n": 2}, {"n": 3}], "step_kind": "x"}
        capped = ag2.cap_step_spec(multi, 2)
        self.assertEqual(len(capped["steps"]), 2)
        self.assertTrue(capped["steps_capped_by_body"])
        single = {"step_kind": "file", "payload_spec": {}}
        self.assertEqual(ag2.cap_step_spec(single, 1), single)
        self.assertEqual(ag2.cap_step_spec(multi, None), multi)
        with self.assertRaises(ValueError):
            ag2.cap_step_spec(multi, -1)

    def test_illegal_cap_is_refused_at_the_view(self):
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.ag1_body_limit_kwargs({"dimensions": {}, "body": {"candidate_cap": -1}})


class V03IllnessIsTheRealEntryTest(unittest.TestCase):
    """The v03 file lives at the *day root*; ``world_home`` sits below it.

    The real layout is ``<day_root>/one-day/world-home`` and the illness file is
    ``<day_root>/phone/v03_ecology.json``, so the read has to walk up.  These
    tests build that exact hierarchy.
    """

    def _service(self, world_home, *, health_state=None):
        state = br.initial_runtime(at=MOMENT.isoformat())
        shim = NS(
            body_state=state,
            _now=MOMENT,
            _clock=None,
            config=NS(world_home=Path(world_home)),
            health_state=health_state,
        )
        shim._moment = types.MethodType(WorldBodyService._moment, shim)
        shim.health_record = types.MethodType(WorldBodyService.health_record, shim)
        shim.body_summary = types.MethodType(WorldBodyService.body_summary, shim)
        return shim

    def _day_root(self, phase, severity):
        """A day root like /root/.nyairo-day, with the World's own home below it.

        The illness block carries the same identity/time fields the real v03
        owner writes (``episode_id`` / ``episode_opened_at`` /
        ``acute_entered_at`` / ``phase_entered_at``); a course with no source
        instant is a named unknown, not a synthetic onset.
        """

        root = Path(tempfile.mkdtemp(prefix="v03-"))
        (root / "phone").mkdir(parents=True, exist_ok=True)
        (root / "one-day" / "world-home").mkdir(parents=True, exist_ok=True)
        active = phase != "healthy"
        illness = {
            "phase": phase,
            "severity": severity,
            "episode_id": "ep:test-1" if active else None,
            "episode_opened_at": "2026-11-14T20:00:00+00:00" if active else None,
            "acute_entered_at": "2026-11-14T21:00:00+00:00" if active else None,
            "phase_entered_at": "2026-11-15T06:00:00+00:00" if active else None,
        }
        (root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": illness}), encoding="utf8"
        )
        return root, root / "one-day" / "world-home"

    def test_acute_illness_file_becomes_an_acute_health_record(self):
        root, world_home = self._day_root("acute_illness", 0.6)
        shim = self._service(world_home)
        record = shim.health_record()
        self.assertEqual(record.status.value, "ACUTE_ILLNESS")
        self.assertTrue(record.episode.open)
        self.assertEqual(record.episode.origin, "world_exposure")

        payload = GatewaySurface(shim).handle("get_body_health")["result"]
        self.assertEqual(payload["health"]["status"], "ACUTE_ILLNESS")
        self.assertTrue(payload["capacity"]["creative_block"])
        self.assertLessEqual(payload["capacity"]["candidate_cap"], 6)

    def test_healthy_phase_keeps_the_persisted_record(self):
        """A healthy v03 file must not mask the persisted chronic load/debt."""

        root, world_home = self._day_root("healthy", 0.0)
        persisted = bhp.initial_record(
            body_id="chiyo_body", subject_id="self", home_ref="", at=MOMENT
        )
        persisted = bhp.replace(
            persisted,
            revision=4,
            status=bhp.HealthStatus.STRAINED,
            fatigue=bhp.FatigueAccumulator(acute=0.3, chronic=0.55, debt=0.4),
        )
        record = self._service(world_home, health_state=persisted).health_record()
        self.assertEqual(record.status.value, "STRAINED")
        self.assertAlmostEqual(record.fatigue.chronic, 0.55)
        self.assertAlmostEqual(record.fatigue.debt, 0.4)
        self.assertIsNone(record.episode)

    def test_flat_layout_world_home_is_the_day_root(self):
        root, _ = self._day_root("acute_illness", 0.55)
        record = self._service(root).health_record()
        self.assertEqual(record.status.value, "ACUTE_ILLNESS")

    def test_convalescence_keeps_the_course_open(self):
        root, world_home = self._day_root("convalescence", 0.2)
        record = self._service(world_home).health_record()
        self.assertEqual(record.status.value, "CONVALESCENT")
        self.assertTrue(record.episode.open)
        self.assertIsNotNone(record.episode.left_acute_at)


class PhoneHealthViewTest(unittest.TestCase):
    """The desktop reads one bounded view; it never computes the envelope."""

    def _shim(self, root, world_home):
        phone_service = self._service_module()
        shim = NS(world_home=world_home, root=root,
                  _somatic_now_iso=lambda: MOMENT.isoformat())
        shim.body_health = types.MethodType(phone_service.PhoneService.body_health, shim)
        shim._load_body_health_module = phone_service.PhoneService._load_body_health_module
        return shim

    @staticmethod
    def _service_module():
        import importlib.util

        root = ROOT / "components" / "phone" / "service.py"
        spec = importlib.util.spec_from_file_location("phone_service_under_test", root)
        module = importlib.util.module_from_spec(spec)
        sys.modules["phone_service_under_test"] = module
        spec.loader.exec_module(module)
        return module

    def test_acute_file_shows_acute_and_a_narrow_range(self):
        root, world_home = V03IllnessIsTheRealEntryTest()._day_root("acute_illness", 0.6)
        code, body = self._shim(root, world_home).body_health()
        self.assertEqual(code, 200)
        self.assertEqual(body["health_status"], "ACUTE_ILLNESS")
        self.assertEqual(body["capacity"]["candidate_cap"], 4)
        self.assertTrue(body["capacity"]["creative_block"])
        self.assertEqual(body["reads_performed"] if "reads_performed" in body else 0, 0)

    def test_healthy_day_without_runtime_never_claims_health(self):
        """No body runtime is no evidence: UNATTESTED, with a named reason."""

        root, world_home = V03IllnessIsTheRealEntryTest()._day_root("healthy", 0.0)
        code, body = self._shim(root, world_home).body_health()
        self.assertEqual(code, 200)
        self.assertEqual(body["health_status"], "UNATTESTED")
        self.assertFalse(body["attested"])
        self.assertEqual(body["reason_code"], "RUNTIME_UNAVAILABLE")
        self.assertIsNone(body["energy"])
        # An unattested body gives no health opinion: no creative refusal and
        # no zero cap before the first real reading.
        self.assertFalse(body["capacity"]["creative_block"])
        self.assertIsNone(body["capacity"]["candidate_cap"])


class CreativeBlockTest(unittest.TestCase):
    """Acute illness stops main-road creative work, not ordinary life."""

    TS = "2026-11-15T09:00:00+00:00"

    def _cand(self, cid, *, kind, source_kind, targets=(), semantic=None, **extra):
        import candidate_sources_ag0 as ag0

        anchor = cid.split(":", 1)[-1]
        return ag0.CandidateRecord(
            candidate_id=cid,
            candidate_kind=kind,
            source_kind=source_kind,
            source_ref="src:%s" % anchor,
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=self.TS,
            observed_at=self.TS,
            recorded_at=self.TS,
            idempotency_key="idem:%s" % anchor,
            target_refs=list(targets),
            semantic_identity=semantic,
            causal_parent_refs=["src:%s" % anchor],
            provenance_refs=["src:%s" % anchor],
            **extra,
        )

    def _three(self):
        import candidate_sources_ag0 as ag0

        booklet = self._cand(
            "cand:booklet", kind=ag1.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY, semantic="WORKBENCH_UNFINISHED_TRAY",
            targets=["artifact:booklet-1"])
        water = self._cand(
            "cand:water", kind=ag1.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
            source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
            world_opportunity_ref="wopp:water", perception_ref="percept:water",
            world_revision_ref="wrev:1")
        rest = self._cand(
            "cand:rest", kind=ag1.CANDIDATE_KIND_CONTINUE_CURRENT,
            source_kind=ag0.SOURCE_CURRENT_ACTIVITY, activity_ref="activity:1")
        return booklet, water, rest

    def test_creative_work_is_blocked_and_ordinary_options_survive(self):
        booklet, water, rest = self._three()
        kept, rejected = ag1.apply_creative_block(
            [booklet, water, rest],
            context_snapshot_id="cs:1", evaluated_at=self.TS,
        )
        self.assertEqual([c.candidate_id for c in kept], ["cand:water", "cand:rest"])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].candidate_ref, "cand:booklet")
        self.assertEqual(rejected[0].status, "REJECTED")
        self.assertEqual(rejected[0].reason_codes, ["HARD_REJECT:BODY_CREATIVE_BLOCK"])

    def test_chinese_creative_identity_is_blocked(self):
        import candidate_sources_ag0 as ag0

        cand = self._cand(
            "cand:draft", kind=ag1.CANDIDATE_KIND_PURSUE_GOAL_STEP,
            source_kind=ag0.SOURCE_EXPLICIT_GOAL, semantic="续写研究稿",
            goal_ref="goal:research", goal_step_ref="gstep:1")
        kept, rejected = ag1.apply_creative_block(
            [cand], context_snapshot_id="cs:1", evaluated_at=self.TS)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)

    def test_reading_is_not_creative_work(self):
        import candidate_sources_ag0 as ag0

        reading = self._cand(
            "cand:read", kind=ag1.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
            targets=["book:novel-1"], semantic="读一会儿书")
        kept, rejected = ag1.apply_creative_block(
            [reading], context_snapshot_id="cs:1", evaluated_at=self.TS)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, [])

    def test_reading_with_an_artifact_identity_survives(self):
        """Reading clues really do carry an ``artifact:`` target."""

        import candidate_sources_ag0 as ag0

        reading = self._cand(
            "cand:reading-art", kind=ag1.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
            targets=["artifact:novel-1"], semantic="READING_CLUE_OPEN")
        kept, rejected = ag1.apply_creative_block(
            [reading], context_snapshot_id="cs:1", evaluated_at=self.TS)
        self.assertEqual([c.candidate_id for c in kept], ["cand:reading-art"])
        self.assertEqual(rejected, [])

    def test_rest_behind_four_creatives_survives_a_cap_of_four(self):
        """Creative rejection happens before the cap, so the cap cannot eat it."""

        import candidate_sources_ag0 as ag0

        creatives = [
            self._cand(
                "cand:work-%d" % i,
                kind=ag1.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
                semantic="WORKBENCH_UNFINISHED_TRAY",
                targets=["artifact:work-%d" % i],
            )
            for i in range(4)
        ]
        rest = self._cand(
            "cand:rest", kind=ag1.CANDIDATE_KIND_CONTINUE_CURRENT,
            source_kind=ag0.SOURCE_CURRENT_ACTIVITY, activity_ref="activity:1")
        kept, rejected = ag1.apply_creative_block(
            creatives + [rest], context_snapshot_id="cs:1", evaluated_at=self.TS)
        self.assertEqual(len(rejected), 4)
        capped = ag1.apply_candidate_cap(kept, 4)
        self.assertEqual([c.candidate_id for c in capped], ["cand:rest"])


class EnvelopeAgreesWithV03Test(unittest.TestCase):
    """One illness course, one set of numbers: the envelope and the v03 gate.

    ``v02_v03_daily_loop_bridge`` filters strenuous outings and creative
    intensives from ``v03_economy_illness_bridge._capability_for_phase``.  The
    health envelope must say the same thing for the same phase, or the two
    layers would narrow differently.
    """

    def test_acute_and_convalescence_match_v03(self):
        from components.world.scripts import v03_economy_illness_bridge as v03

        for phase, severity in (("acute_illness", 0.6), ("convalescence", 0.2)):
            record = bhp.record_from_illness(
                {
                    "phase": phase,
                    "severity": severity,
                    "episode_id": "ep:match-v03",
                    "episode_opened_at": "2026-11-14T20:00:00+00:00",
                    "phase_entered_at": "2026-11-15T06:00:00+00:00",
                },
                body_id="chiyo_body", subject_id="self", home_ref="", at=MOMENT,
            )
            _, envelope = bhp.project(record, None, at=MOMENT, attested=True)
            expected = v03._capability_for_phase(phase, severity)
            self.assertEqual(envelope.candidate_cap, expected["candidate_cap"], phase)
            self.assertEqual(envelope.creative_block, expected["creative_block"], phase)
            self.assertAlmostEqual(
                envelope.capacity_index, expected["capacity_index"], places=2, msg=phase)


if __name__ == "__main__":
    unittest.main()
