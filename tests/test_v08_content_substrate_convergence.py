#!/usr/bin/env python3
"""Wave E1b: converge ``v08_emergent_echoes`` / ``v03_epistemic_encounters``.

Coverage:
* both modules route visibility / freeze / lifecycle checks through
  ``scoped_content_record`` and ``v03`` routes its RMW through ``state_tx_guard``;
* ``WITHDRAWN``, ``RECEIVED_UNOPENED`` and private rows are filtered *before*
  concern matching, so an exact keyword hit can never promote them;
* the frozen original fact of a memory reinterpretation is preserved across a
  re-submission, and an encounter's original observation title is refused cleanly;
* ``lingering_threads`` / ``future_self_questions`` keep ``expires_at`` and
  ``consider_after`` byte-identical across reads (``overdue=False``,
  ``debt_score=0``);
* the ``v03`` writer serializes with ``state_file_lock``, stamps ``op_seq``
  receipts, trims by submission order (not alphabetically), replays post-eviction
  keys idempotently and expires ancient retries with ``RETRY_WINDOW_EXPIRED``.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_content_substrate_convergence``
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import scoped_content_record as scoped  # noqa: E402
from components.life.scripts import state_tx_guard as guard  # noqa: E402
from components.life.scripts import v03_epistemic_encounters as encounters  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-16T12:00:00+08:00"
T2 = "2026-11-17T09:00:00+08:00"


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


def _counting_gate(results: list[dict[str, Any]]):
    original = scoped.check_read_and_attention_gate

    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = original(*args, **kwargs)
        results.append(outcome)
        return outcome

    return wrapper


class _V08Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="e1b-v08-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mutate(self, payload: dict[str, Any], *, actor_side: str = "subject",
               now_iso: str = T0) -> dict[str, Any]:
        projected, _status = echoes.apply_emergent_echoes_mutation(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )
        return projected

    def state_path(self) -> Path:
        return self.root / "phone" / "v08_emergent_echoes.json"

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path().read_text(encoding="utf-8"))


class DelegationTest(_V08Base):
    def test_01_shared_substrate_is_wired_into_both_modules(self) -> None:
        self.assertIs(echoes._scr, scoped)
        self.assertIs(encounters._scr, scoped)
        self.assertIs(encounters._tx, guard)

    def test_02_v08_projection_and_matcher_call_the_shared_gate(self) -> None:
        self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框打磨完要不要上蜡",
            "anchor_kind": "work",
            "anchor_ref": "work:window_frame",
            "keywords": ["窗框"],
            "visibility": "shared",
        })
        self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框背后藏着的私事",
            "anchor_kind": "work",
            "anchor_ref": "work:window_secret",
            "keywords": ["窗框"],
            "visibility": "private",
        })

        results: list[dict[str, Any]] = []
        with mock.patch.object(
            scoped, "check_read_and_attention_gate", side_effect=_counting_gate(results)
        ) as gate:
            partner = echoes.read_emergent_echoes_zero_write(
                self.root, has_subject_capability=False, now_iso=T0
            )
            matched = echoes.match_situational_echoes_zero_write(
                self.root, incoming_text="窗框", now_iso=T0
            )

        self.assertGreaterEqual(gate.call_count, 2)
        self.assertEqual(len(partner["future_self_questions"]), 1)
        self.assertEqual(
            [row["question_cn"] for row in matched["matched_questions"]],
            ["窗框打磨完要不要上蜡"],
        )
        for outcome in results:
            self.assertFalse(outcome["overdue"])
            self.assertEqual(outcome["debt_score"], 0)

    def test_03_v03_lens_calls_the_shared_gate_and_ranker(self) -> None:
        encounters.apply_encounters_mutation(
            self.root,
            {
                "action": "record_first_glance",
                "target_ref": "reading:frame",
                "target_title_cn": "窗框速写练习册",
                "seen_pages": [1],
                "total_pages": 4,
                "current_impression_cn": "线条很松",
            },
            actor_side="subject",
            now_iso=T0,
        )
        results: list[dict[str, Any]] = []
        original_rank = scoped.rank_authorized_by_concern
        with mock.patch.object(
            scoped, "check_read_and_attention_gate", side_effect=_counting_gate(results)
        ) as gate, mock.patch.object(
            scoped, "rank_authorized_by_concern", wraps=original_rank
        ) as rank:
            cues = encounters.project_concern_driven_attention_zero_write(
                self.root, concerns=["窗框"]
            )

        self.assertGreaterEqual(gate.call_count, 1)
        self.assertGreaterEqual(rank.call_count, 1)
        self.assertEqual(cues[0]["title_cn"], "窗框速写练习册")
        for outcome in results:
            self.assertFalse(outcome["overdue"])
            self.assertEqual(outcome["debt_score"], 0)


class V08AuthorizationFilteringTest(_V08Base):
    def test_01_private_rows_never_reach_partner_view_or_matching(self) -> None:
        self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框的秘密问题",
            "anchor_kind": "work",
            "anchor_ref": "work:secret_frame",
            "keywords": ["窗框", "秘密"],
            "visibility": "private",
        })
        shared = self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框的公开问题",
            "anchor_kind": "work",
            "anchor_ref": "work:shared_frame",
            "keywords": ["窗框"],
            "visibility": "shared",
        })["future_self_questions"][-1]
        self.mutate({
            "action": "record_memory_reinterpretation",
            "episode_ref": "episode:secret_frame",
            "episode_title_cn": "窗框背后的旧事",
            "original_fact_cn": "那年窗框刷了三遍漆",
            "new_view_cn": "现在觉得第三遍才是关键",
            "trigger_summary_cn": "路过旧房子",
            "shared_with_partner": False,
        })
        self.mutate({
            "action": "record_cross_domain_spark",
            "source_domain": "木工",
            "source_observation_cn": "打磨时先摸纹路",
            "target_domain": "窗框",
            "try_method_cn": "先用细砂纸试一块",
            "familiarity": "new",
            "craft_fit": "workable",
            "mood_pull_today": "curious",
            "visibility": "private",
        })

        partner = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(
            [row["question_id"] for row in partner["future_self_questions"]],
            [shared["question_id"]],
        )
        self.assertEqual(partner["memory_reinterpretations"], [])
        self.assertEqual(partner["style_and_interest_sparks"], [])
        self.assertNotIn("秘密问题", json.dumps(partner, ensure_ascii=False))

        public_match = echoes.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框 秘密 那年窗框刷了三遍漆", now_iso=T0
        )
        self.assertEqual(
            [row["question_id"] for row in public_match["matched_questions"]],
            [shared["question_id"]],
        )
        self.assertEqual(public_match["matched_reinterpretations"], [])
        self.assertEqual(public_match["matched_sparks"], [])

        private_match = echoes.match_situational_echoes_zero_write(
            self.root,
            incoming_text="窗框 秘密 那年窗框刷了三遍漆",
            include_private=True,
            now_iso=T0,
        )
        self.assertEqual(len(private_match["matched_questions"]), 2)
        self.assertEqual(len(private_match["matched_reinterpretations"]), 1)
        self.assertEqual(len(private_match["matched_sparks"]), 1)

    def test_02_dormant_question_never_matches_and_is_not_rewritten(self) -> None:
        self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框的晚一点再想的问题",
            "anchor_kind": "work",
            "anchor_ref": "work:later_frame",
            "keywords": ["窗框"],
            "visibility": "shared",
        })
        state = self.read_state()
        row = state["future_self_questions"][0]
        row["content_envelope"]["consider_after"] = "2026-11-16T18:00:00+08:00"
        self.state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        matched = echoes.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框", now_iso=T0
        )
        self.assertEqual(matched["matched_questions"], [])

        subject = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(len(subject["future_self_questions"]), 1)
        # Reading never renews or clears the consideration gate.
        self.assertEqual(
            self.read_state()["future_self_questions"][0]["content_envelope"]["consider_after"],
            "2026-11-16T18:00:00+08:00",
        )
        outcome = scoped.check_read_and_attention_gate(
            self.read_state()["future_self_questions"][0],
            actor_side="subject",
            now_iso=T0,
        )
        self.assertTrue(outcome["dormant"])
        self.assertFalse(outcome["overdue"])
        self.assertEqual(outcome["debt_score"], 0)

    def test_03_expired_question_never_matches_and_never_resets(self) -> None:
        self.mutate({
            "action": "plant_future_question",
            "question_cn": "窗框已经过时的问题",
            "anchor_kind": "work",
            "anchor_ref": "work:expired_frame",
            "keywords": ["窗框"],
            "visibility": "shared",
        })
        state = self.read_state()
        row = state["future_self_questions"][0]
        row["content_envelope"]["expires_at"] = "2026-11-16T09:30:00+08:00"
        self.state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        matched = echoes.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框", now_iso=T1
        )
        self.assertEqual(matched["matched_questions"], [])
        subject = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(len(subject["future_self_questions"]), 1)
        self.assertEqual(
            self.read_state()["future_self_questions"][0]["content_envelope"]["expires_at"],
            "2026-11-16T09:30:00+08:00",
        )
        outcome = scoped.check_read_and_attention_gate(
            self.read_state()["future_self_questions"][0],
            actor_side="subject",
            now_iso=T1,
        )
        self.assertTrue(outcome["expired"])
        self.assertFalse(outcome["overdue"])
        self.assertEqual(outcome["debt_score"], 0)

    def test_04_lingering_thread_never_renews_or_matches_after_expiry(self) -> None:
        self.mutate({
            "action": "note_lingering_thread",
            "summary_cn": "窗框还剩最后一道缝要补",
            "scope_kind": "work",
            "scope_ref": "work:window_frame",
            "ttl_minutes": 30,
            "visibility": "shared",
        })
        first = self.read_state()["lingering_threads"][0]["expires_at"]

        for now in (T0, T1, T2):
            echoes.read_emergent_echoes_zero_write(
                self.root, has_subject_capability=True, now_iso=now
            )
            echoes.match_situational_echoes_zero_write(
                self.root, incoming_text="窗框", now_iso=now
            )

        self.assertEqual(self.read_state()["lingering_threads"][0]["expires_at"], first)
        matched = echoes.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框", now_iso=T1
        )
        self.assertEqual(matched["matched_lingering_threads"], [])

        projected = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True, now_iso=T1
        )
        row = projected["lingering_threads"][0]
        self.assertFalse(row["active_now"])
        self.assertIn("过期后只是停止自动注入", row["expiry_note_cn"])
        self.assertEqual(row["expires_at"], first)
        outcome = scoped.check_read_and_attention_gate(
            self.read_state()["lingering_threads"][0],
            actor_side="subject",
            now_iso=T1,
        )
        self.assertTrue(outcome["expired"])
        self.assertFalse(outcome["overdue"])
        self.assertEqual(outcome["debt_score"], 0)


class V08FrozenFactTest(_V08Base):
    def _record(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "record_memory_reinterpretation",
            "episode_ref": "episode:seawall",
            "episode_title_cn": "那年夏天的海堤",
            "original_fact_cn": "我们坐在海堤上等退潮",
            "prior_view_cn": "当时只觉得风很大",
            "new_view_cn": "现在觉得那是很安静的陪伴",
            "trigger_summary_cn": "整理旧照片",
            "shared_with_partner": True,
        }
        payload.update(overrides)
        return self.mutate(payload)

    def test_01_frozen_original_fact_never_overwritten(self) -> None:
        first = self._record()["memory_reinterpretations"][0]
        revision = self.read_state()["revision"]

        results: list[Any] = []
        original = scoped.enforce_frozen_fact_immutability

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            results.append((args, kwargs))
            return original(*args, **kwargs)

        with mock.patch.object(
            scoped, "enforce_frozen_fact_immutability", side_effect=wrapper
        ) as frozen:
            second = self._record(
                original_fact_cn="一条完全不同的新事实",
                new_view_cn="后来又是另一种理解",
                trigger_summary_cn="路过同一段海堤",
            )["memory_reinterpretations"][0]

        self.assertGreaterEqual(frozen.call_count, 1)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(second["original_fact_cn"], first["original_fact_cn"])
        self.assertEqual(len(second["revisions"]), 2)
        self.assertEqual(second["content_envelope"]["frozen_fact"], first["original_fact_cn"])
        self.assertEqual(self.read_state()["revision"], revision + 1)

        with self.assertRaises(ValueError):
            scoped.enforce_frozen_fact_immutability(
                {"original_fact_cn": first["original_fact_cn"]},
                {"original_fact_cn": "另一条事实"},
            )


class V03FilteringAndFreezeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="e1b-v03-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mutate(self, payload: dict[str, Any], *, actor_side: str = "subject") -> dict[str, Any]:
        projected, _status = encounters.apply_encounters_mutation(
            self.root, payload, actor_side=actor_side, now_iso=T0
        )
        return projected

    def test_01_sealed_body_and_withdrawn_never_rank(self) -> None:
        dropped = self.mutate(
            {
                "action": "drop_shared_material",
                "title_cn": "旧城散步笔记",
                "excerpt_cn": "内页反复比较螺钿反光与路灯的关系",
                "topic_tag": "散步",
                "page_count": 3,
            },
            actor_side="partner",
        )
        material_id = dropped["shared_materials"][0]["material_id"]
        self.assertEqual(dropped["shared_materials"][0]["status"], encounters.MATERIAL_RECEIVED)

        # The sealed body never enters concern matching, even on an exact keyword.
        sealed_body = encounters.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿反光"]
        )
        self.assertNotIn(material_id, [cue["ref"] for cue in sealed_body])

        # Metadata (title/topic) is readable existence information, not the body.
        sealed_title = encounters.project_concern_driven_attention_zero_write(
            self.root, concerns=["旧城"]
        )
        self.assertIn(material_id, [cue["ref"] for cue in sealed_title])

        self.mutate(
            {
                "action": "open_or_use_shared_material",
                "material_id": material_id,
                "mode": "open_partial",
                "opened_pages": [1],
            }
        )
        opened_body = encounters.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿反光"]
        )
        self.assertIn(material_id, [cue["ref"] for cue in opened_body])

        self.mutate(
            {"action": "withdraw_shared_material", "material_id": material_id},
            actor_side="partner",
        )
        withdrawn = encounters.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿反光", "旧城"]
        )
        self.assertNotIn(material_id, [cue["ref"] for cue in withdrawn])

        state = json.loads(
            (self.root / "phone" / "v03_encounters.json").read_text(encoding="utf-8")
        )
        row = state["shared_materials"][0]
        self.assertEqual(row["status"], encounters.MATERIAL_WITHDRAWN)
        self.assertEqual(row["excerpt_cn"], "")

        sealed = {
            "author_side": "partner",
            "visibility": "shared",
            "status": encounters.MATERIAL_RECEIVED,
            "text_cn": "螺钿反光",
            "marker": "sealed",
        }
        withdrawn_row = {
            "author_side": "partner",
            "visibility": "shared",
            "withdrawn": True,
            "text_cn": "螺钿反光",
            "marker": "withdrawn",
        }
        hits = scoped.rank_authorized_by_concern(
            [sealed, withdrawn_row],
            concern_text="螺钿反光",
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(hits, [])

    def test_02_frozen_encounter_title_is_refused(self) -> None:
        first = self.mutate(
            {
                "action": "record_first_glance",
                "target_ref": "reading:seawall_plants",
                "target_title_cn": "海边植物图鉴",
                "seen_pages": [1],
                "total_pages": 6,
                "current_impression_cn": "只翻了几页",
            }
        )
        encounter_id = first["encounters"][0]["encounter_id"]
        revision = json.loads(
            (self.root / "phone" / "v03_encounters.json").read_text(encoding="utf-8")
        )["revision"]

        with self.assertRaises(encounters.V03ConflictError) as ctx:
            self.mutate({
                "action": "revisit_with_question",
                "encounter_id": encounter_id,
                "target_title_cn": "被改写过的书名",
                "newly_seen_pages": [2],
                "active_question_cn": "为什么叶形不同",
                "noticed_detail_cn": "第 2 页角落有小字",
            })
        self.assertEqual(ctx.exception.reason_code, "FROZEN_FACT_IMMUTABLE")
        self.assertEqual(ctx.exception.status_code, 409)
        state = json.loads(
            (self.root / "phone" / "v03_encounters.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["revision"], revision)
        self.assertEqual(state["encounters"][0]["target_title_cn"], "海边植物图鉴")

        with mock.patch.object(
            scoped, "enforce_frozen_fact_immutability", wraps=scoped.enforce_frozen_fact_immutability
        ) as frozen:
            updated = self.mutate({
                "action": "revisit_with_question",
                "encounter_id": encounter_id,
                "target_title_cn": "海边植物图鉴",
                "newly_seen_pages": [2],
                "active_question_cn": "为什么叶形不同",
                "noticed_detail_cn": "第 2 页角落有小字",
            })
        self.assertGreaterEqual(frozen.call_count, 1)
        self.assertEqual(updated["encounters"][0]["stage"], "QUESTION_DRIVEN_REVISIT")

        with self.assertRaises(encounters.V03ConflictError):
            self.mutate({
                "action": "record_nuanced_recognition",
                "encounter_id": encounter_id,
                "target_title_cn": "另一个书名",
                "current_impression_cn": "画得细，但配色偏艳",
                "partial_disagreement_kept": "保留对配色的意见",
            })


class V03TransactionGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="e1b-v03-tx-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def state_path(self) -> Path:
        return self.root / "phone" / "v03_encounters.json"

    def mutate(self, payload: dict[str, Any], *, now_iso: str = T0) -> tuple[dict[str, Any], str]:
        return encounters.apply_encounters_mutation(
            self.root, payload, actor_side="subject", now_iso=now_iso
        )

    @staticmethod
    def glance(ref: str, *, key: str | None = None,
               expected_revision: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "record_first_glance",
            "target_ref": ref,
            "target_title_cn": f"册子-{ref}",
            "seen_pages": [1],
            "total_pages": 3,
            "current_impression_cn": "只看了一页",
        }
        if key is not None:
            payload["operation_key"] = key
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        return payload

    def test_01_lock_receipt_shape_and_cas(self) -> None:
        projected, status = self.mutate(self.glance("reading:first", key="op_first"))
        self.assertEqual(status, "APPLIED")
        self.assertEqual(len(projected["encounters"]), 1)
        doc = json.loads(self.state_path().read_text(encoding="utf-8"))
        receipt = doc["operation_ledger"]["op_first"]
        self.assertGreaterEqual(int(receipt["op_seq"]), 1)
        self.assertTrue(receipt.get("payload_hash"))
        self.assertEqual(receipt["revision"], doc["revision"])
        self.assertTrue(Path(str(self.state_path()) + ".lock").exists())
        current_revision = int(doc["revision"])

        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, str]] = []
        guard_lock = threading.Lock()

        def attempt(ref: str, key: str) -> None:
            barrier.wait(timeout=10)
            try:
                _projected, verdict = self.mutate(
                    self.glance(ref, key=key, expected_revision=current_revision)
                )
                result = ("ok", verdict)
            except encounters.V03EncountersError as exc:
                result = ("err", exc.reason_code)
            with guard_lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=attempt, args=("reading:cas_a", "op_cas_a")),
            threading.Thread(target=attempt, args=("reading:cas_b", "op_cas_b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())

        self.assertEqual(sum(1 for kind, _ in outcomes if kind == "ok"), 1, outcomes)
        self.assertEqual(
            [code for kind, code in outcomes if kind == "err"],
            ["REVISION_CONFLICT"],
            outcomes,
        )

    def test_02_ledger_trims_by_submission_order_not_alphabetically(self) -> None:
        state: dict[str, Any] = {
            "schema_version": encounters.STATE_SCHEMA_VERSION,
            "revision": 1000,
            "updated_at": T0,
            "next_seq": 1,
            "encounters": [],
            "shared_materials": [],
            "npc_errands_and_hearsay": [],
            "operation_ledger": {},
        }
        # Oldest submission gets a lexicographically last key; newest submission
        # gets a lexicographically first key.  Alphabetical trimming would delete
        # the newest and keep the oldest -- the opposite of submission order.
        guard.stamp_receipt(
            state, "zzz_oldest", {"status": "applied"},
            revision=1001, dict_key="operation_ledger", payload_hash="hash-oldest",
            committed_at="2026-11-16T00:00:01Z",
        )
        for index in range(255):
            guard.stamp_receipt(
                state, f"mmm_{index:03d}", {"status": "applied"},
                revision=1002 + index, dict_key="operation_ledger",
                payload_hash=f"hash-{index}",
                committed_at="2026-11-16T00:00:30Z",
            )
        guard.stamp_receipt(
            state, "aaa_newest", {"status": "applied"},
            revision=2000, dict_key="operation_ledger", payload_hash="hash-newest",
            committed_at="2026-11-16T00:01:00Z",
        )
        self.assertEqual(len(state["operation_ledger"]), 257)
        # ``sort_keys=True`` makes the on-disk order alphabetical, so insertion
        # order after the next load is NOT submission order.
        guard.atomic_write_json(self.state_path(), state)

        _projected, status = self.mutate(self.glance("reading:trim", key="ooo_new"))
        self.assertEqual(status, "APPLIED")
        ledger = json.loads(self.state_path().read_text(encoding="utf-8"))["operation_ledger"]
        self.assertEqual(len(ledger), encounters.MAX_OPERATION_LEDGER)
        self.assertNotIn("zzz_oldest", ledger)
        self.assertIn("aaa_newest", ledger)
        self.assertIn("ooo_new", ledger)

    def test_03_post_eviction_replay_conflict_and_expiry(self) -> None:
        payload = self.glance("reading:replay", key="op_replay")
        projected, status = self.mutate(payload)
        self.assertEqual(status, "APPLIED")
        revision_before = projected["revision"]
        count_before = len(projected["encounters"])

        # Push the receipt out of the active ledger into the bounded commit ring.
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        for index in range(300):
            guard.stamp_receipt(
                state, f"bulk_{index}", {"status": "applied", "ok": True},
                revision=100 + index, dict_key="operation_ledger",
                payload_hash=f"bulk-hash-{index}",
                committed_at=f"2026-11-16T00:00:{index % 60:02d}Z",
            )
            guard.trim_receipts_by_op_seq(
                state, dict_key="operation_ledger", max_keep=encounters.MAX_OPERATION_LEDGER
            )
        guard.atomic_write_json(self.state_path(), state)
        self.assertNotIn(
            "op_replay",
            json.loads(self.state_path().read_text(encoding="utf-8"))["operation_ledger"],
        )

        retry = dict(payload, expected_revision=revision_before)
        replayed, status2 = self.mutate(retry)
        self.assertEqual(status2, "DUPLICATE")
        self.assertEqual(replayed["revision"], revision_before)
        self.assertEqual(len(replayed["encounters"]), count_before)

        changed = dict(payload, target_title_cn="换了标题")
        with self.assertRaises(encounters.V03ConflictError) as ctx:
            self.mutate(changed)
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        ancient = self.glance("reading:ancient", key="op_ancient", expected_revision=0)
        with self.assertRaises(encounters.V03ConflictError) as ctx2:
            self.mutate(ancient)
        self.assertEqual(ctx2.exception.reason_code, "RETRY_WINDOW_EXPIRED")

    def test_04_zero_write_reads_stay_lock_free(self) -> None:
        before = _snapshot(self.root)
        projected = encounters.project_v03_encounters(
            self.root, has_subject_capability=True, now_iso=T0
        )
        encounters.project_concern_driven_attention_zero_write(self.root, concerns=["窗框"])
        after = _snapshot(self.root)
        self.assertEqual(before, after)
        self.assertEqual(projected["encounters"], [])
        self.assertFalse(Path(str(self.state_path()) + ".lock").exists())


if __name__ == "__main__":
    unittest.main()
