#!/usr/bin/env python3
"""v0.4 C: the cycle side of the closeout integration (W's plan, C's candidacy and route).

What this pins down (exactly this)
----------------------------------
W delivers ``artifact_closeout`` -- a *pure* plan: the target namespace, the AG-2 adoption spec and
the one AR-0 archive step.  C owns everything else: whether a work may be offered at all, which
candidate represents it, and which Activity/step shape is registered when the Decision picks it.

The cases below are the seam, not the owner:

1. with no plan module, **nothing** is offered (no candidate, no target, a named state);
2. with the real plan, the target/adoption spec are W's own (``closeout:artifact:artifact:<uuid>``,
   ``CLOSE_PERSONAL_ARTIFACT``, an ``ACTION_RESULT_MATCH`` completion contract scoped to the work);
3. the step is one ``archive_artifact`` that keeps the Activity open, and the same intent gives the
   same idempotency key (an UNKNOWN is reconciled with it, never re-keyed);
4. an occupied foreground is never switched to a closeout, a non-ACTIVE work is not offered, and an
   unreadable owner offers nothing;
5. the admitted PERSONAL_OPPORTUNITY source emits the wrap-up as its **own** candidate (distinct
   candidate id / semantic identity / idempotency key), while the continuation candidate's
   identity is byte-for-byte what it was before this seam existed.

The owner itself (grant, CAS, replay, read-back) is W's suite's business, and the parent's limited
real chain is the only thing that can show a real archive.  Nothing here calls a model, a socket or
a Supply service.

Fixtures live under ``/tmp/nyairo-v04-c-fixtures`` and are kept.

Run (POSIX host)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_life_closeout_cycle_v04
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")
OWNER_ID = "artifact:7a1b2c3d4e5f60718293a4b5c6d7e8f9"
HEAD_V3 = "artifact-version:3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c"
TS = "2026-10-28T06:00:00+00:00"
FIXTURE_ROOT = Path(os.environ.get("NYAIRO_C_FIXTURE_ROOT") or "/tmp/nyairo-v04-c-fixtures")


def fixture_dir(name: str) -> Path:
    path = FIXTURE_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-closeout-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Port:
    """Fake ManagedArtifact read port for candidacy only (never asked to archive here)."""

    def __init__(self, *, lifecycle="ACTIVE", revision=3, refuse=None):
        self.lifecycle = lifecycle
        self.revision = revision
        self.refuse = refuse

    def resume_point(self, artifact_id):
        from life_artifact_port import ArtifactResumePoint
        if self.refuse is not None:
            raise RuntimeError(self.refuse)
        return ArtifactResumePoint(artifact_id=OWNER_ID, title="她的草稿",
                                   head_version_id=HEAD_V3, version_number=3,
                                   content="一段", lifecycle=self.lifecycle,
                                   revision=self.revision)

    def list_artifacts(self):
        return [{"artifact_id": OWNER_ID, "lifecycle": self.lifecycle,
                 "revision": self.revision, "title": "她的草稿"}]

    def available(self):
        return True


class _Runtime:
    def __init__(self, state_root, *, foreground=None):
        self.state_root = str(state_root)
        self._foreground = foreground
        self.candidate_registry = self
        self.candidate_read = None

    def canonical_state(self):
        return {"foreground_activity_ref": self._foreground}

    def register_adapter(self, adapter):
        """The cycle registers its candidate source on construction; nothing else uses it here."""

        self.registered = adapter


def _cycle(*, foreground=None, port=None, plan="real", name="seam"):
    import autonomous_life_cycle as alc

    root = fixture_dir(name)
    runtime = _Runtime(root, foreground=foreground)
    cycle = alc.AutonomousLifeCycle(runtime, artifact_port=port or _Port(), enabled=True,
                                    subject_id="subject:test", artifact_grant_id="grant:test",
                                    work_artifact_id=OWNER_ID,
                                    env={"CHIYA_TEST": "1"}, pause_state_root=root,
                                    continuity_state_root=root)
    if plan == "real":
        import artifact_closeout
        cycle.closeout_plan_module = artifact_closeout
    elif plan == "absent":
        # Exactly the state of the tree before W delivered the plan: nothing is importable.
        alc.closeout_module = lambda: None
        cycle.closeout_plan_module = None
    return cycle, runtime


@POSIX_ONLY
class CloseoutSeamTest(unittest.TestCase):
    def test_without_the_plan_nothing_is_offered_at_all(self):
        cycle, _runtime = _cycle(plan="absent", name="absent")
        self.assertEqual(cycle.closeout_state()["state"], "NOT_AVAILABLE")
        self.assertEqual(cycle.closeout_state()["reason"], "ARTIFACT_CLOSEOUT_NOT_DELIVERED")
        self.assertEqual(cycle.closeout_target_ids(), set())
        self.assertIsNone(cycle._closeout_point())

    def test_the_target_and_adoption_spec_are_w_the_plans_own(self):
        cycle, _runtime = _cycle(name="plan")
        point = cycle._closeout_point()
        self.assertIsNotNone(point)
        self.assertEqual(point["state"], "READY")
        # The owner id is kept verbatim: a real target therefore reads
        # ``closeout:artifact:artifact:<uuid>`` and parses back to the same bytes.
        self.assertEqual(point["target_ref"], "closeout:artifact:" + OWNER_ID)
        self.assertEqual(point["expected_revision"], 3)
        spec = cycle._closeout_adoption_spec(point)
        self.assertEqual(spec["activity_kind"], "CLOSE_PERSONAL_ARTIFACT")
        self.assertIn(point["target_ref"], spec["target_refs"])
        contract = spec["completion_contract_spec"]
        self.assertEqual(contract["criteria_kind"], "ACTION_RESULT_MATCH")
        self.assertEqual(contract["criteria"]["expected_artifact_ref"], OWNER_ID)
        # The completion predicate is the one AR-1 really settles for an artifact effect -- the
        # closeout never invents a criteria kind of its own.
        self.assertEqual(contract["criteria"]["required_predicate"], "artifact_written_verified")
        self.assertEqual(contract["required_evidence_kinds"],
                         ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"])

    def test_the_step_is_one_archive_that_keeps_the_activity_open(self):
        cycle, _runtime = _cycle(name="step")
        point = cycle._closeout_point()
        decision = type("D", (), {"selected_candidate_ref": "cand:closeouttest",
                                  "target_activity_ref": None})()
        step = cycle._closeout_step_spec(point, decision_record=decision, observed_at=TS)
        self.assertEqual(step["step_kind"], "FILE_ACTION")
        self.assertEqual(step["target_ref"], point["target_ref"])
        self.assertEqual(step["payload_spec"]["op"], "archive_artifact")
        self.assertEqual(step["payload_spec"]["artifact_id"], OWNER_ID)
        self.assertEqual(step["payload_spec"]["expected_revision"], 3)
        self.assertEqual(step["post_submit_life_behavior"], "KEEP_ACTIVITY_ACTIVE")
        self.assertEqual(step["expected_effect_predicates"], ["artifact_archived_verified"])
        # Same intent -> same key: an UNKNOWN is resolved with the original key, never re-keyed.
        again = cycle._closeout_step_spec(point, decision_record=decision, observed_at=TS)
        self.assertEqual(step["idempotency_key"], again["idempotency_key"])
        # A different owner revision is a different intent, not a silent re-target.
        other, _ = _cycle(name="step-other")
        other_point = dict(point, expected_revision=4)
        other_step = other._closeout_step_spec(other_point, decision_record=decision,
                                               observed_at=TS)
        self.assertNotEqual(step["idempotency_key"], other_step["idempotency_key"])

    def test_an_occupied_foreground_is_never_switched_to_a_closeout(self):
        cycle, _runtime = _cycle(foreground="actv:running", name="occupied")
        self.assertIsNone(cycle._closeout_point())
        self.assertEqual(cycle.closeout_target_ids(), set())

    def test_only_an_active_work_is_offered(self):
        archived, _runtime = _cycle(port=_Port(lifecycle="ARCHIVED"), name="archived")
        # Reported by name, not offered: a closed work must never be turned into a step the owner
        # will refuse (and never into a second archive).
        self.assertEqual(archived._closeout_point()["state"], "NOT_ACTIVE")
        self.assertEqual(archived.closeout_target_ids(), set())
        state = archived.closeout_state()
        self.assertEqual(state["state"], "READY")  # the plan is there; the *work* is not offered
        refused, _runtime = _cycle(port=_Port(refuse="LIFE_SUPPLY_UNREACHABLE"), name="refused")
        self.assertIsNone(refused._closeout_point())
        self.assertIn("LIFE_SUPPLY_UNREACHABLE", str(refused.last_error))

    def test_the_routed_adoption_spec_resolves_the_closeout_through_the_bound_predicate(self):
        """gen1 real chain regression: the router must resolve the *committed* Decision's candidate.

        The round built ``_RoutedAdoptionSpec(is_closeout=self._decision_chose_closeout)`` and AG-2
        invoked it with the committed record.  With the predicate written as a ``@staticmethod``
        that called ``AutonomousLifeCycle._selected_candidate(record)``, the record went to ``self``
        and the router raised ``TypeError: missing 1 required positional argument:
        'decision_record'`` -- the whole WORK round died as ``DECISION_FAILED`` before any step.
        This test pins the *bound* callable and the resolved shape.
        """

        import autonomous_life_cycle as alc

        cycle, runtime = _cycle(name="routed-spec")
        point = cycle._closeout_point()
        closeout_ref = point["target_ref"]

        class _CandidateRead:
            def get_candidate(self, candidate_ref):
                if candidate_ref != "cand:closeout:routed":
                    # A different selection is a different candidate: no closeout target on it.
                    return {"candidate_id": candidate_ref, "target_refs": ["artifact:" + OWNER_ID]}
                return {"candidate_id": candidate_ref,
                        "target_refs": [closeout_ref, "artifact:" + OWNER_ID]}

        runtime.candidate_read = _CandidateRead()
        decision = type("D", (), {"selected_candidate_ref": "cand:closeout:routed",
                                  "target_activity_ref": None})()

        # The exact expression the round uses: a *bound* predicate, not a class attribute.
        self.assertTrue(cycle._decision_chose_closeout(decision))
        routed = alc._RoutedAdoptionSpec(
            artifact={"activity_kind": "CONTINUE_PERSONAL_ARTIFACT"},
            world=lambda _decision: {"activity_kind": "WORLD_MOVE"},
            is_world=cycle._decision_chose_world,
            reading=lambda _decision: {"activity_kind": "READ_PERSONAL_MATERIAL"},
            is_reading=cycle._decision_chose_reading,
            closeout=lambda _decision: cycle._closeout_adoption_spec(point),
            is_closeout=cycle._decision_chose_closeout)
        self.assertEqual(routed(decision)["activity_kind"], "CLOSE_PERSONAL_ARTIFACT")
        # And a Decision that named something else never gets the wrap-up shape.  A fresh router is
        # used because one router instance answers for exactly one round (it caches its shape).
        other = type("D", (), {"selected_candidate_ref": "cand:other",
                               "target_activity_ref": None})()
        self.assertFalse(cycle._decision_chose_closeout(other))
        routed_other = alc._RoutedAdoptionSpec(
            artifact={"activity_kind": "CONTINUE_PERSONAL_ARTIFACT"},
            world=lambda _decision: {"activity_kind": "WORLD_MOVE"},
            is_world=cycle._decision_chose_world,
            reading=lambda _decision: {"activity_kind": "READ_PERSONAL_MATERIAL"},
            is_reading=cycle._decision_chose_reading,
            closeout=lambda _decision: cycle._closeout_adoption_spec(point),
            is_closeout=cycle._decision_chose_closeout)
        self.assertEqual(routed_other(other)["activity_kind"], "CONTINUE_PERSONAL_ARTIFACT")

    def test_the_source_offers_the_wrap_up_as_its_own_candidate(self):
        from artifact_continuation_source import ArtifactContinuationSource
        from candidate_sources_ag0 import sha256_hex

        bare = OWNER_ID.split(":", 1)[1]
        plain = ArtifactContinuationSource(_Port(), subject_id="subject:test")
        plain_items, _snap, plain_meta = plain.discover(observed_at=TS)
        plain_cands, _snap2, _meta = plain.materialize(observed_at=TS)

        source = ArtifactContinuationSource(_Port(), subject_id="subject:test",
                                            closeout_target_provider=lambda: {bare})
        items, _snap3, _meta3 = source.discover(observed_at=TS)
        cands, _snap4, _meta4 = source.materialize(observed_at=TS)

        self.assertEqual(len(plain_items), 1)
        self.assertEqual(len(items), 2, items)
        wrap = [row for row in items if (row.get("detail") or {}).get("closeout_target")]
        self.assertEqual(len(wrap), 1)
        self.assertEqual(wrap[0]["detail"]["closeout_target"], "closeout:artifact:" + OWNER_ID)
        self.assertTrue(source.validate_source(wrap[0], observed_at=TS)[0])

        # The continuation candidate is unchanged, byte for byte (a regression pin).
        expected = "cand:" + sha256_hex("PERSONAL_OPPORTUNITY:%s:v3" % OWNER_ID)[:16]
        self.assertEqual(plain_cands[0].candidate_id, expected)
        self.assertEqual(cands[0].candidate_id, expected)
        self.assertEqual(len(cands), 2)
        closeout_cand = [c for c in cands if c.candidate_id != expected][0]
        self.assertNotEqual(closeout_cand.semantic_identity, plain_cands[0].semantic_identity)
        self.assertNotEqual(closeout_cand.idempotency_key, plain_cands[0].idempotency_key)
        self.assertIn("closeout:artifact:" + OWNER_ID, list(closeout_cand.target_refs))
        self.assertNotIn("closeout:artifact:" + OWNER_ID, list(plain_cands[0].target_refs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
