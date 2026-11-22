#!/usr/bin/env python3
"""v0.8 Wave 2 emergent echoes -> 0.2/0.3 daily loop bridge.

Covers, against the real ``v02_v03_daily_loop_bridge`` and the real
``v08_emergent_echoes`` engine (0 product-model calls):

* an open ``future_self_question`` and an untried, genuinely-pulled
  ``style_and_interest_spark`` each surface as one calm ``v08_echo:*`` candidate;
  a ``mood_pull_today == "low"`` spark is never proposed;
* the scan is 100% zero-write and ``llm_calls == 0``;
* a selected echo candidate builds the right event
  (``REVISIT_FUTURE_QUESTION`` / ``SETTLE_SPARK_ATTEMPT``) and settles through the
  real consequence ledger with idempotent replay;
* a real ``v08_matter:`` cook that creates something new plants at most one
  bounded future-self question, never a duplicate, and never once 3 questions are
  already open;
* no hardcoded gendered pronoun or individual character name in the new files or
  helpers.

Run (WSL): ``python -m unittest tests.test_v08_echoes_daily_loop -v``.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v08_emergent_echoes as echoes  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
STATE_SCHEMA = "phone.v08.emergent_echoes.v1"
ECHO_SLICE = "phone.v08_emergent_echoes"

#: Characters that must never be hard-coded in the new engine/bridge/test code.
#: Built from code points so this test file itself stays free of the literal glyphs.
FORBIDDEN_PRONOUNS = (chr(0x5979), chr(0x5343) + chr(0x4EE3))  # "she" / "Chiyo"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _question(
    question_id: str = "fq_test0001",
    *,
    question_cn: str = "番茄汤下次要不要少放点盐？",
    anchor_ref: str = "object:tomato_01",
    keywords: tuple[str, ...] = ("番茄", "汤"),
    status: str = "open",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question_cn": question_cn,
        "anchor_kind": "topic",
        "anchor_ref": anchor_ref,
        "keywords": list(keywords),
        "visibility": "private",
        "status": status,
        "created_at": T0,
        "revisited_at": None,
        "resolution": None,
        "note_cn": "",
    }


def _spark(
    spark_id: str = "spark_test0001",
    *,
    mood_pull_today: str = "curious",
    status: str = "untried",
) -> dict[str, Any]:
    return {
        "spark_id": spark_id,
        "source_domain": "读书",
        "source_observation_cn": "读游记时留意到旧纸手作的温度",
        "target_domain": "手作",
        "try_method_cn": "用旧纸做一张小卡片",
        "familiarity": "new",
        "craft_fit": "workable",
        "mood_pull_today": mood_pull_today,
        "visibility": "private",
        "status": status,
        "method_validated": False,
        "latest_outcome": None,
        "choice_trace_cn": "",
        "created_at": T0,
        "updated_at": T0,
    }


class _EchoesDailyBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-echoes-daily-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- seeding ---------------------------------------------------------
    def state_path(self) -> Path:
        return self.root / "phone" / "v08_emergent_echoes.json"

    def write_state(
        self,
        *,
        questions: list[dict[str, Any]] | None = None,
        sparks: list[dict[str, Any]] | None = None,
    ) -> None:
        doc = {
            "schema_version": STATE_SCHEMA,
            "revision": 1,
            "updated_at": T0,
            "next_seq": 100,
            "future_self_questions": list(questions or []),
            "shared_idioms": [],
            "memory_reinterpretations": [],
            "style_and_interest_sparks": list(sparks or []),
            "divergent_notices": [],
            "operation_ledger": {},
        }
        self.state_path().write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def projection(self) -> dict[str, Any]:
        return echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True
        )

    # -- collection / event / settlement ---------------------------------
    def collect(self, *, now: str = T0) -> dict[str, Any]:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now)

    @staticmethod
    def descriptors(collected: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(desc)
            for desc in (collected.get("descriptors") or {}).values()
            if isinstance(desc, dict)
        ]

    def echo_refs(self, collected: dict[str, Any], kind: str) -> list[str]:
        return [
            str(desc.get("source_ref") or "")
            for desc in self.descriptors(collected)
            if str(desc.get("source_ref") or "").startswith(f"v08_echo:{kind}:")
        ]

    def event(self, collected: dict[str, Any], source_ref: str, *, decision_id: str) -> dict[str, Any]:
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id=decision_id
        )
        self.assertIsInstance(event, dict, source_ref)
        return event

    def settle(self, event: dict[str, Any], *, now: str = T0) -> dict[str, Any]:
        return bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=now)

    # -- matter seeding (for the emergent bonus) -------------------------
    def introduce_fridge_tomato(self, object_id: str = "tomato_01") -> None:
        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "往冰箱放一颗番茄",
                "steps": [{
                    "op": "INTRODUCE_ITEM",
                    "object_id": object_id,
                    "label_cn": "番茄",
                    "item_kind": "ingredient",
                    "target_parent_id": "home_fridge",
                    "cook_state": "raw",
                    "portions": 1,
                    "shelf_life_hours": 48,
                }],
            },
            now_iso=T0,
        )


class EchoesDailyLoopTest(_EchoesDailyBase):
    def test_01_scan_is_zero_write_and_finds_both_echo_kinds(self) -> None:
        self.write_state(questions=[_question()], sparks=[_spark()])
        before = _snapshot(self.root)
        collected = self.collect()
        after = _snapshot(self.root)
        self.assertEqual(after, before, "the echo scan must stay 100% zero-write")
        self.assertEqual(collected.get("llm_calls"), 0)

        question_refs = self.echo_refs(collected, "revisit_question")
        spark_refs = self.echo_refs(collected, "try_spark")
        self.assertEqual(question_refs, ["v08_echo:revisit_question:fq_test0001"])
        self.assertEqual(spark_refs, ["v08_echo:try_spark:spark_test0001"])

        by_ref = {
            str(desc.get("source_ref") or ""): desc
            for desc in self.descriptors(collected)
        }
        q_desc = by_ref["v08_echo:revisit_question:fq_test0001"]
        self.assertEqual(q_desc["availability_basis"], "V08_FUTURE_QUESTION_REENCOUNTER")
        self.assertEqual(q_desc["resolution"], "still_curious")
        self.assertFalse(q_desc["strenuous_outing"])
        s_desc = by_ref["v08_echo:try_spark:spark_test0001"]
        self.assertEqual(s_desc["availability_basis"], "V08_CROSS_DOMAIN_SPARK")
        self.assertEqual(s_desc["outcome"], "helped")

    def test_02_low_mood_spark_is_never_proposed(self) -> None:
        self.write_state(sparks=[_spark(mood_pull_today="low")])
        collected = self.collect()
        self.assertEqual(self.echo_refs(collected, "try_spark"), [])
        # A low pull also invents no question out of nowhere.
        self.assertEqual(self.echo_refs(collected, "revisit_question"), [])

    def test_03_revisit_question_settles_and_replays_idempotently(self) -> None:
        self.write_state(questions=[_question()])
        collected = self.collect()
        ref = self.echo_refs(collected, "revisit_question")[0]
        event = self.event(collected, ref, decision_id="echo-question-1")
        self.assertEqual(event["action_kind"], "REVISIT_FUTURE_QUESTION")
        self.assertEqual(event["question_id"], "fq_test0001")
        self.assertEqual(event["resolution"], "still_curious")

        settled = self.settle(event)
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn(ECHO_SLICE, settled["slices_updated"])
        self.assertEqual(settled["llm_calls"], 0)
        self.assertEqual(
            settled["details"]["v08_emergent_echo"]["status"], "APPLIED"
        )

        revisited = self.projection()["future_self_questions"][0]
        self.assertEqual(revisited["status"], "open")
        self.assertEqual(revisited["resolution"], "still_curious")
        self.assertIsNotNone(revisited["revisited_at"])

        bytes_before = self.state_path().read_bytes()
        replay = self.settle(copy.deepcopy(event))
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(self.state_path().read_bytes(), bytes_before)

    def test_04_spark_attempt_settles_and_validates_only_when_helped(self) -> None:
        self.write_state(sparks=[_spark()])
        collected = self.collect()
        ref = self.echo_refs(collected, "try_spark")[0]
        event = self.event(collected, ref, decision_id="echo-spark-1")
        self.assertEqual(event["action_kind"], "SETTLE_SPARK_ATTEMPT")
        self.assertEqual(event["spark_id"], "spark_test0001")
        self.assertEqual(event["outcome"], "helped")
        self.assertTrue(event["choice_trace_cn"])

        settled = self.settle(event)
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn(ECHO_SLICE, settled["slices_updated"])
        spark = self.projection()["style_and_interest_sparks"][0]
        self.assertEqual(spark["status"], "helped")
        self.assertTrue(spark["method_validated"])
        self.assertEqual(spark["latest_outcome"], "helped")

        bytes_before = self.state_path().read_bytes()
        replay = self.settle(copy.deepcopy(event))
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(self.state_path().read_bytes(), bytes_before)

    def test_05_created_dish_plants_exactly_one_future_question(self) -> None:
        self.introduce_fridge_tomato()
        collected = self.collect()
        cook_event = self.event(collected, "v08_matter:cook", decision_id="echo-cook-1")
        cooked = self.settle(cook_event)
        self.assertEqual(cooked["status"], "APPLIED", cooked)
        self.assertIn(ECHO_SLICE, cooked["slices_updated"])
        bonus = cooked["details"].get("v08_emergent_bonus")
        self.assertIsInstance(bonus, dict, cooked["details"])
        output_id = bonus["output_object_id"]
        self.assertIn(output_id, engine._read_state_zero_write(self.root)["matter_objects"])

        questions = self.projection()["future_self_questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["anchor_ref"], output_id)
        self.assertEqual(questions[0]["anchor_kind"], "object")
        self.assertIn("火候", questions[0]["question_cn"])
        self.assertEqual(questions[0]["status"], "open")

        replay = self.settle(copy.deepcopy(cook_event))
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(len(self.projection()["future_self_questions"]), 1)

    def test_06_three_open_questions_block_the_emergent_bonus(self) -> None:
        self.write_state(
            questions=[
                _question("fq_a", question_cn="第一个问题？"),
                _question("fq_b", question_cn="第二个问题？"),
                _question("fq_c", question_cn="第三个问题？"),
            ]
        )
        self.introduce_fridge_tomato()
        collected = self.collect()
        cook_event = self.event(collected, "v08_matter:cook", decision_id="echo-cook-2")
        cooked = self.settle(cook_event)
        self.assertEqual(cooked["status"], "APPLIED", cooked)
        self.assertNotIn("v08_emergent_bonus", cooked["details"])
        self.assertEqual(len(self.projection()["future_self_questions"]), 3)

    def test_07_echo_code_hardcodes_no_pronoun(self) -> None:
        sources = [
            ROOT / "components" / "life" / "scripts" / "v08_emergent_echoes.py",
            Path(__file__),
        ]
        for fn in (
            bridge._emergent_echoes_module,
            bridge._read_emergent_echoes_projection_zero_write,
            bridge._call_emergent_echoes_action,
            bridge._plant_v08_echo_future_question,
            bridge._settle_v08_echo_action,
        ):
            sources.append(inspect.getsource(fn))
        for source in sources:
            text = source.read_text("utf-8") if isinstance(source, Path) else source
            for forbidden in FORBIDDEN_PRONOUNS:
                self.assertNotIn(forbidden, text, f"hardcoded pronoun in {source}")

    def test_08_no_echo_candidates_when_engine_sees_no_state(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="v08-echoes-empty-"))
        try:
            before = _snapshot(empty)
            collected = bridge.collect_v02_v03_loop_candidates(empty, now_iso=T0)
            self.assertEqual(_snapshot(empty), before)
            refs = [
                str(desc.get("source_ref") or "")
                for desc in (collected.get("descriptors") or {}).values()
                if isinstance(desc, dict)
            ]
            self.assertFalse([r for r in refs if r.startswith("v08_echo:")])
        finally:
            import shutil

            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
