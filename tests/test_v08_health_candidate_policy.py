#!/usr/bin/env python3
"""v0.8: the acute-body candidate policy, proven on the real producers.

Three things are pinned here, all against real ``ArtifactContinuationSource``
output rather than hand-built guesses:

* the creative gate reads the *structured* identity the producers write
  (``step="signal"`` entries on ``provenance_chain``), so a real work
  continuation / closeout / manuscript fee / project option is refused while
  reading, rest, water and the idle window survive;
* the candidate cap seats one real rest, one warm drink and the reading
  continuation before anything else -- ``daydream_window`` never occupies the
  rest seat, and a random ``cand:<sha256>`` never decides who is dropped;
* the same inputs with different random artifact UUIDs and different input
  order leave the same *roles* in front of her.

Run:  python3 tests/test_v08_health_candidate_policy.py
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "tests"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import artifact_continuation_source as acs  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

NOW = "2026-11-15T09:00:00+00:00"
READING_TEXT = "第一页。\n%%PAGE%%\n第二页。\n"


class _FakeArtifactPort:
    """The read-only slice of the Supply owner the source really uses."""

    def __init__(self, *, work_id: str, reading_id: str) -> None:
        self.work_id = work_id
        self.reading_id = reading_id
        self._points = {
            work_id: ArtifactResumePoint(
                artifact_id=work_id, title="未完的小册子",
                head_version_id="artifact-version:work-1",
                version_number=1, content="写到一半的正文", lifecycle="ACTIVE",
                revision=3),
            reading_id: ArtifactResumePoint(
                artifact_id=reading_id, title="在读的材料",
                head_version_id="artifact-version:read-1",
                version_number=1, content=READING_TEXT, lifecycle="ACTIVE",
                revision=2),
        }

    def available(self) -> bool:
        return True

    def list_artifacts(self):
        return [{"artifact_id": aid, "lifecycle": point.lifecycle,
                 "title": point.title}
                for aid, point in self._points.items()]

    def resume_point(self, artifact_id: str) -> ArtifactResumePoint:
        return self._points[artifact_id]


def _extra_rows() -> list[dict]:
    """What ``AutonomousLifeCycle._extra_opportunities`` really hands the source."""

    return [
        {
            "source_ref": "fitem:v02v03-v03_care-bed_rest_block",
            "origin_source_ref": "v03_care:bed_rest_block",
            "target_refs": ["v03_care:bed_rest_block"],
            "title": "卧床静养恢复体力", "kind": "illness_rest_recovery",
            "action_family": "illness_rest_recovery",
            "creative_intensive": False, "care_event_kind": "bed_rest_block",
            "revision": 1,
        },
        {
            "source_ref": "fitem:v02v03-v03_care-warm_hydration",
            "origin_source_ref": "v03_care:warm_hydration",
            "target_refs": ["v03_care:warm_hydration"],
            "title": "捧一杯温水慢慢喝下静养", "kind": "illness_rest_recovery",
            "action_family": "illness_rest_recovery",
            "creative_intensive": False, "care_event_kind": "warm_hydration",
            "revision": 1,
        },
        {
            "source_ref": "fitem:v02v03-v03_idle-daydream_window",
            "origin_source_ref": "v03_idle:daydream_window",
            "target_refs": ["v03_idle:daydream_window"],
            "title": "靠在窗边发一会儿呆", "kind": "meaningless_idle",
            "action_family": "meaningless_idle",
            "creative_intensive": False, "idle_kind": "daydream_window",
            "revision": 1,
        },
        {
            "source_ref": "fitem:v02v03-v02_plan_take_a_walk",
            "origin_source_ref": "plan:take_a_walk",
            "target_refs": ["place:street_1"],
            "title": "出门走一小段", "kind": "adopt_tentative_plan",
            "action_family": "adopt_tentative_plan",
            "creative_intensive": False, "revision": 1,
        },
        {
            "source_ref": "popt:project_seed_1",
            "origin_source_ref": "popt:project_seed_1",
            "target_refs": ["popt:project_seed_1"],
            "title": "把这批素材写成一篇新稿", "kind": "TODAY_SELF_PLANNED",
            "action_family": "advance_project",
            "creative_intensive": True, "revision": 1,
        },
    ]


def _materialize(*, work_uuid: str, reading_uuid: str):
    work_id = "artifact:%s" % work_uuid
    reading_id = "artifact:%s" % reading_uuid
    source = acs.ArtifactContinuationSource(
        _FakeArtifactPort(work_id=work_id, reading_id=reading_id),
        subject_id=ag0.GLOBAL_SUBJECT_ID,
        reading_artifact_id=reading_id,
        reading_state_provider=lambda: {
            "state": "AVAILABLE", "available": True, "exhausted": False,
            "material": {"version_id": "artifact-version:read-1", "pages": 2},
            "position": {"page": None, "next_page": 1},
            "annotations": {"note": 0, "unclear": 0},
        },
        extra_opportunity_provider=_extra_rows,
    )
    candidates, _snap, _meta = source.materialize(observed_at=NOW)
    return candidates, work_id, reading_id


def _candidate_role(candidate, *, work_id: str, reading_id: str) -> str:
    refs = [str(candidate.source_ref or "")]
    refs.extend(str(ref) for ref in (candidate.target_refs or ()))
    joined = " ".join(refs)
    if "bed_rest_block" in joined:
        return "rest"
    if "warm_hydration" in joined:
        return "hydration"
    if "daydream_window" in joined:
        return "idle"
    if "popt:" in joined or str(candidate.source_ref or "").startswith("popt:"):
        return "project"
    if any(ref.startswith("closeout:") for ref in refs):
        return "closeout"
    if reading_id in joined:
        return "reading"
    if work_id in joined:
        return "work"
    if "plan:" in joined or "place:" in joined:
        return "everyday"
    return "other:%s" % (str(candidate.source_ref or "?"))


def _roles(candidates, *, work_id: str, reading_id: str) -> list[str]:
    return sorted(_candidate_role(cand, work_id=work_id, reading_id=reading_id)
                  for cand in candidates)


class CreativeGateOverRealSourceTest(unittest.TestCase):
    def test_real_work_is_creative_reading_and_care_are_not(self):
        candidates, work_id, reading_id = _materialize(
            work_uuid="0b7f6c1e9a5d4f3c8b2a1d0e9f8c7b6a",
            reading_uuid="5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d")
        by_role = {_candidate_role(c, work_id=work_id, reading_id=reading_id): c
                   for c in candidates}
        self.assertTrue(ag1.candidate_is_creative_work(by_role["work"]))
        self.assertFalse(ag1.candidate_is_creative_work(by_role["reading"]))
        self.assertFalse(ag1.candidate_is_creative_work(by_role["rest"]))
        self.assertFalse(ag1.candidate_is_creative_work(by_role["hydration"]))
        self.assertFalse(ag1.candidate_is_creative_work(by_role["idle"]))
        self.assertFalse(ag1.candidate_is_creative_work(by_role["everyday"]))
        # A real project option is creative work, and the gate names it.
        self.assertTrue(ag1.candidate_is_creative_work(by_role["project"]))

        kept, rejected = ag1.apply_creative_block(
            candidates, context_snapshot_id="cs:v08", evaluated_at=NOW)
        kept_roles = _roles(kept, work_id=work_id, reading_id=reading_id)
        rejected_roles = sorted(
            _candidate_role(c, work_id=work_id, reading_id=reading_id)
            for c in candidates if c.candidate_id in
            {row.candidate_ref for row in rejected})
        self.assertNotIn("work", kept_roles)
        self.assertNotIn("project", kept_roles)
        self.assertIn("reading", kept_roles)
        self.assertIn("rest", kept_roles)
        self.assertIn("hydration", kept_roles)
        self.assertIn("idle", kept_roles)
        self.assertEqual(rejected_roles, ["project", "work"])


class CandidateCapSeatingTest(unittest.TestCase):
    def _decide(self, candidates, *, work_id, reading_id, order_seed):
        ordered = list(candidates)
        random.Random(order_seed).shuffle(ordered)
        kept, _rejected = ag1.apply_creative_block(
            ordered, context_snapshot_id="cs:v08", evaluated_at=NOW)
        capped = ag1.apply_candidate_cap(
            kept, 4, body_creative_block=True, body_health_state="LIMITED")
        return capped, _roles(capped, work_id=work_id, reading_id=reading_id)

    def test_four_slots_keep_rest_water_reading_and_drop_the_idle_lottery(self):
        for seed, (work_uuid, reading_uuid) in enumerate((
            ("11111111aaaa4bbb8ccc9ddd0eee1fff", "22222222aaaa4bbb8ccc9ddd0eee2fff"),
            ("ffffeeee111144442222333355556666", "aaaabbbbcccc4ddd8eee999900001111"),
            ("deadbeef00004aaa8bbbcccc11112222", "cafebabe99994aaa8bbbcccc33334444"),
        )):
            candidates, work_id, reading_id = _materialize(
                work_uuid=work_uuid, reading_uuid=reading_uuid)
            capped, roles = self._decide(
                candidates, work_id=work_id, reading_id=reading_id,
                order_seed=seed)
            self.assertEqual(len(capped), 4, roles)
            self.assertEqual(
                roles, ["everyday", "hydration", "reading", "rest"],
                "same real roles decided despite UUIDs %s/%s" % (work_uuid, reading_uuid))

    def test_daydream_never_takes_the_rest_seat(self):
        candidates, work_id, reading_id = _materialize(
            work_uuid="33333333aaaa4bbb8ccc9ddd0eee3fff",
            reading_uuid="44444444aaaa4bbb8ccc9ddd0eee4fff")
        by_role = {_candidate_role(c, work_id=work_id, reading_id=reading_id): c
                   for c in candidates}
        capped = ag1.apply_candidate_cap(
            [by_role["idle"], by_role["rest"]], 1,
            body_creative_block=True, body_health_state="ACUTE_ILLNESS")
        self.assertEqual(len(capped), 1)
        self.assertEqual(
            _candidate_role(capped[0], work_id=work_id, reading_id=reading_id),
            "rest")

    def test_cap_can_only_remove_never_add(self):
        candidates, work_id, reading_id = _materialize(
            work_uuid="55555555aaaa4bbb8ccc9ddd0eee5fff",
            reading_uuid="66666666aaaa4bbb8ccc9ddd0eee6fff")
        capped = ag1.apply_candidate_cap(
            candidates, 2, body_creative_block=True, body_health_state="LIMITED")
        self.assertEqual(len(capped), 2)
        allowed = {cand.candidate_id for cand in candidates}
        self.assertTrue({cand.candidate_id for cand in capped} <= allowed)


class ContinueCurrentTest(unittest.TestCase):
    def _candidate(self, activity_ref: str):
        return ag0.CandidateRecord(
            candidate_id="cand:continue_%s" % activity_ref.split(":", 1)[-1],
            candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
            source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
            source_ref=activity_ref,
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=NOW, observed_at=NOW, recorded_at=NOW,
            idempotency_key="idem:%s" % activity_ref,
            activity_ref=activity_ref,
            target_refs=["artifact:artifact:writing-1"],
            causal_parent_refs=[activity_ref],
            provenance_refs=[activity_ref],
        )

    def test_writing_activity_is_creative_and_reading_activity_is_not(self):
        writing = self._candidate("activity:writing")
        reading = self._candidate("activity:reading")
        writing_state = {"foreground_activity": {
            "activity_id": "activity:writing",
            "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
            "target_ref": "artifact:artifact:writing-1",
        }}
        reading_state = {"foreground_activity": {
            "activity_id": "activity:reading",
            "activity_kind": "READ_PERSONAL_MATERIAL",
            "target_ref": "artifact:artifact:book-1",
        }}
        self.assertTrue(ag1.candidate_is_creative_work(writing, writing_state))
        self.assertFalse(ag1.candidate_is_creative_work(reading, reading_state))

        kept, rejected = ag1.apply_creative_block(
            [writing, reading], context_snapshot_id="cs:v08", evaluated_at=NOW,
            current_state=writing_state)
        # The writing continuation is refused; the reading one is untouched.
        self.assertEqual([row.candidate_ref for row in rejected],
                         [writing.candidate_id])
        self.assertEqual([c.candidate_id for c in kept], [reading.candidate_id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
