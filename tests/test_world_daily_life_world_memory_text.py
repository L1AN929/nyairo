#!/usr/bin/env python3
"""I-r3: a **World** consequence in M0 -- what it may say, and what it may move.

Three precise cases about the Life-side admission consumer
(``components/life/scripts/life_experience_memory_consumer.py``), plus one small contrast
about the admitted row's *effect*:

1. a World-only handoff (World facts + action + settlement, **no** artifact ref) is written
   with the neutral World sentence and keeps the owner's own refs -- it no longer claims
   "完成了一次真实修改，作品所有者确认了新版本" for something that did not touch a work;
2. an artifact handoff keeps exactly the wording it had (this test would fail if the World
   branch leaked into it);
3. an outcome that arrives *unsettled* is never upgraded to a success, and delivering the
   same handoff twice does not write a second row;
4. the same candidate set and the same fixed rule: only the admitted World row (the model
   facing ``prompt_view`` shape) moves the choice; an artifact row, an empty row or no row at
   all does not.

Everything runs on the real M0 owner (real ``EvidenceStore`` + real control ledger + real
``LifeExperiencePort`` + the real consumer) with the real file paths; no Memory INSERT is
faked, no ref is invented, and the fixture directories are kept.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_life_continuity_inputs import MemoryFixture  # noqa: E402
from test_world_daily_life_continuous import ScriptedWorldCognition  # noqa: E402

FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
FOOD = "i-food-riceball-01"
CONSUMED_FACT = "world_fact:object:%s:consumed" % FOOD
OCCURRED_AT = "2026-10-18T07:40:00+00:00"


def _fixture_dir(prefix: str) -> Path:
    """A fresh directory under the session fixture root.  Nothing is ever removed."""

    base = FIXTURE_ROOT if FIXTURE_ROOT.is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(base)))


def world_handoff(**overrides) -> dict:
    """The shape AG-2 really hands over for a World consequence (no artifact ref)."""

    event = {
        "handoff_id": "lexp:0123456789abcdef",
        "activity_ref": "act:drill-home-stay",
        "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
        "action_refs": ["actn:drill-consume-1"],
        "settlement_refs": ["rset:drill-consume-1"],
        "artifact_refs": [],
        "world_refs": [CONSUMED_FACT, "world:object:%s" % FOOD],
        "fact_refs": [],
        "occurred_at": OCCURRED_AT,
        "subject_id": "chiyo.global",
    }
    event.update(overrides)
    return event


class WorldMemoryTextTest(unittest.TestCase):
    """What an admitted World consequence is allowed to say."""

    def setUp(self) -> None:
        self.memory = MemoryFixture(_fixture_dir("ir3-world-text-"))

    def admitted_row(self, event: dict) -> dict:
        result = self.memory.admit(event)
        self.assertEqual(result.get("disposition"), "ACCEPT", result)
        rows = self.memory.rows(limit=5)
        self.assertTrue(rows, "the real owner must have written one M0 row")
        return rows[0]

    def test_a_world_only_outcome_is_not_written_as_a_work_modification(self):
        row = self.admitted_row(world_handoff())
        text = str(row["content"])
        self.assertTrue(text.startswith("在真实世界里发生了一件已确证的事"), text)
        self.assertIn("世界事实 " + CONSUMED_FACT, text)
        self.assertIn("动作 actn:drill-consume-1", text)
        self.assertIn("所有者回执 rset:drill-consume-1", text)
        # The old template said this for *any* SIGNIFICANT_PROGRESS handoff; a World-only
        # consequence may not claim a modified work or a new version.
        for claim in ("真实修改", "新版本", "作品"):
            self.assertNotIn(claim, text)
        kinds = {str(ref.get("kind")): str(ref.get("id")) for ref in row["source_refs"]}
        # every World ref the owner sent survives, in the owner's own order and spelling.
        world_ids = [str(ref.get("id")) for ref in row["source_refs"]
                     if str(ref.get("kind")) == "world"]
        self.assertEqual(world_ids[:2], [CONSUMED_FACT, "world:object:%s" % FOOD], world_ids)
        self.assertEqual(kinds.get("action"), "actn:drill-consume-1")
        self.assertEqual(kinds.get("owner_result"), "rset:drill-consume-1")
        self.assertNotIn("artifact", kinds)
        # ... and this is the row a model is really handed (the product's own view).
        view = self.memory.port.prompt_view()
        self.assertTrue([item for item in view if CONSUMED_FACT in str(item.get("text"))], view)
        self.assertNotIn("真实修改", str(view))

    def test_an_artifact_outcome_keeps_its_own_wording(self):
        row = self.admitted_row(world_handoff(
            handoff_id="lexp:fedcba9876543210", world_refs=[],
            artifact_refs=["artifact:drill-work-note"],
            settlement_refs=["rset:drill-work-1"]))
        text = str(row["content"])
        self.assertTrue(text.startswith("完成了一次真实修改，作品所有者确认了新版本"), text)
        self.assertIn("作品 artifact:drill-work-note", text)
        self.assertIn("所有者回执 rset:drill-work-1", text)
        self.assertNotIn("世界事实", text)
        kinds = {str(ref.get("kind")): str(ref.get("id")) for ref in row["source_refs"]}
        self.assertEqual(kinds.get("artifact"), "artifact:drill-work-note")

    def test_an_unsettled_outcome_is_never_written_as_a_success(self):
        event = world_handoff(handoff_id="lexp:aaaabbbbccccdddd", settlement_refs=[],
                              outcome_kind="UNKNOWN_EFFECT")
        row = self.admitted_row(event)
        text = str(row["content"])
        self.assertIn("结果还没有确定", text)
        self.assertIn("世界事实 " + CONSUMED_FACT, text)
        for claim in ("已确证", "完成", "新版本", "作品"):
            self.assertNotIn(claim, text)
        # The same handoff delivered again is a duplicate, never a second experience.
        again = self.memory.admit(event)
        self.assertEqual(again.get("disposition"), "DUPLICATE", again)
        self.assertEqual(len(self.memory.rows(limit=10)), 1, self.memory.rows(limit=10))


class _Request:
    """The minimum a decision request must carry for the stub rule to read it."""

    def __init__(self, candidates: list[dict], experiences: list[dict]) -> None:
        self.candidates = candidates
        self.prior_experiences = experiences
        self.observed_fact_keys = ["world_here_place_home"]
        self.allowed_reason_codes = ["SELF_DIRECTED_DAY"]


class WorldRowMovesALegalChoiceTest(unittest.TestCase):
    """One candidate set, one rule: only the admitted World row moves the choice.

    This is the contrast the main drill cannot run twice without a second episode: the same
    two legal candidates are offered to the same fixed rule with (a) the real admitted World
    row as ``prompt_view`` hands it over, (b) the real admitted *artifact* row, (c) an empty
    row and (d) no history at all.
    """

    CANDIDATES = [
        {"candidate_id": "cand:out", "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
         "target_refs": ["place:street_front"], "legal_decisions": ["START", "DEFER"]},
        {"candidate_id": "cand:stay", "candidate_kind": "CONSIDER_PERSONAL_OPPORTUNITY",
         "target_refs": ["stay:home:LOOK_AROUND:180"], "legal_decisions": ["START", "DEFER"]},
    ]

    def setUp(self) -> None:
        self.memory = MemoryFixture(_fixture_dir("ir3-world-choice-"))
        self.cognition = ScriptedWorldCognition()
        self.cognition.goal = "eat:any"

    def chosen(self, experiences: list[dict]) -> str:
        self.cognition.invoke(_Request([dict(item) for item in self.CANDIDATES],
                                       experiences))
        return str(self.cognition.reads[-1].get("chose") or "")

    def test_only_the_admitted_world_row_moves_the_same_choice(self):
        # (a) the real World row, through the consumer and the product's own view
        self.assertEqual(self.memory.admit(world_handoff())["disposition"], "ACCEPT")
        world_view = self.memory.port.prompt_view()
        self.assertTrue([item for item in world_view if CONSUMED_FACT in item["text"]],
                        world_view)
        self.assertEqual(self.chosen(world_view), "stay:home:LOOK_AROUND:180",
                         self.cognition.reads[-1])
        # (b) the real *artifact* row: same owner, same view, no World fact -> no bias
        self.assertEqual(self.memory.admit(world_handoff(
            handoff_id="lexp:1122334455667788", world_refs=[],
            artifact_refs=["artifact:drill-work-note"],
            settlement_refs=["rset:drill-work-2"]))["disposition"], "ACCEPT")
        artifact_view = [item for item in self.memory.port.prompt_view()
                         if item.get("artifact_ref") == "artifact:drill-work-note"]
        self.assertTrue(artifact_view, self.memory.port.prompt_view())
        self.assertEqual(self.chosen(artifact_view), "place:street_front",
                         self.cognition.reads[-1])
        # (c) an empty row (nothing remembered) and (d) no history at all
        self.assertEqual(self.chosen([{"experience_id": "lexp:empty", "text": ""}]),
                         "place:street_front", self.cognition.reads[-1])
        self.assertEqual(self.chosen([]), "place:street_front", self.cognition.reads[-1])
        # the bias never invents a position, a permission or a fact about the body
        last = self.cognition.reads[-1]
        self.assertEqual(last.get("world_consumed"), [])
        self.assertEqual(last.get("here"), "home")


if __name__ == "__main__":
    unittest.main()
