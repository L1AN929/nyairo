#!/usr/bin/env python3
"""v0.4 Ch.49 开放的一日方向 + v3.0 Ch.23/30 关切驱动的注意力透镜。

Coverage:
* ``sketch_day_direction`` only sketches: ``status == "direction_only"``,
  ``executed_in_world is False``, ``failure_judgment is None`` and no memory row
  is ever written by a sketch;
* ``advance_or_interrupt_direction`` transitions (start / pause / complete / fade);
  ``mark_completed_with_ref`` refuses a missing ``evidence_ref`` on both the engine
  and the HTTP gate; ``pause_at_breakpoint`` keeps its note without erasing progress;
* partner projection strips ``visibility == "private"`` directions and a partner
  call to a subject-only direction action is refused with 403;
* the concern-driven attention lens flips the top cue on the same shelf when the
  active concern changes (窗框 vs 夜景);
* a ``RECEIVED_UNOPENED`` material never leaks its sealed ``excerpt_cn`` into
  attention until it is really opened; a ``WITHDRAWN`` material never matches;
* every lens read is byte- and mtime-identical (zero write), ``llm_calls == 0``,
  and modules carry no hard-coded name or gendered pronoun.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_open_direction_and_concern_lens -v``
"""

from __future__ import annotations

import hashlib
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
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v03_epistemic_encounters as enc_engine  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes_engine  # noqa: E402
from components.phone import v03_encounters_view as enc_view  # noqa: E402
from components.phone import v08_emergent_echoes_view as echoes_view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-16T12:00:00+08:00"
T2 = "2026-11-16T18:00:00+08:00"


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


def _sketch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "sketch_day_direction",
        "horizon_note_cn": "今天想把窗台上的木框打磨到能上蜡",
        "next_decision_boundary_cn": "打磨完再决定要不要换一块木料",
        "bound_ref": "work:window_frame",
        "visibility": "private",
    }
    payload.update(overrides)
    return payload


class OpenDayDirectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-direction-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_matter_receipt(
        self,
        operation_key: str,
        *,
        status: str = "applied",
        actor_side: str = "subject",
        object_id: str = "window_frame",
        action: str = "sand_and_wax",
    ) -> str:
        world = self.root / "world"
        world.mkdir(parents=True, exist_ok=True)
        (world / "v08_matter_space.json").write_text(
            json.dumps({
                "operations": {
                    operation_key: {
                        "fingerprint": "fp_" + operation_key,
                        "actor_side": actor_side,
                        "receipt": {
                            "status": status,
                            "action": action,
                            "object_id": object_id,
                            "at": T2,
                        },
                    }
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        return operation_key

    def _mutate(
        self,
        payload: dict[str, Any],
        *,
        actor_side: str = "subject",
        now_iso: str = T0,
    ) -> dict[str, Any]:
        projected, _status = echoes_engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )
        return projected

    def test_01_sketch_is_direction_only_and_never_claims_execution(self) -> None:
        projected = self._mutate(_sketch_payload())
        row = projected["day_directions"][0]
        self.assertTrue(str(row["direction_id"]).startswith("dd_"))
        self.assertEqual(row["status"], "direction_only")
        self.assertFalse(row["executed_in_world"])
        self.assertIsNone(row["evidence_ref"])
        self.assertIsNone(row["failure_judgment"])
        self.assertEqual(row["breakpoint_note_cn"], "")
        self.assertTrue(row["zero_guilt"])
        self.assertEqual(row["bound_ref"], "work:window_frame")

        # Ch.49 §四: a sketched direction never writes memory and never judges failure.
        self.assertEqual(projected["memory_reinterpretations"], [])

        with self.assertRaises(echoes_engine.V08ValidationError):
            self._mutate(_sketch_payload(horizon_note_cn="短"))
        with self.assertRaises(echoes_engine.V08ValidationError):
            self._mutate(_sketch_payload(visibility="public"))

    def test_02_transitions_keep_progress_and_completion_needs_evidence(self) -> None:
        direction_id = self._mutate(_sketch_payload())["day_directions"][0]["direction_id"]

        started = self._mutate({
            "action": "advance_or_interrupt_direction",
            "direction_id": direction_id,
            "transition": "mark_started",
        }, now_iso=T1)["day_directions"][0]
        self.assertEqual(started["status"], "in_progress")
        # Starting is motion, not a completed world act: it never claims execution.
        self.assertFalse(started["executed_in_world"])

        paused = self._mutate({
            "action": "advance_or_interrupt_direction",
            "direction_id": direction_id,
            "transition": "pause_at_breakpoint",
            "breakpoint_note_cn": "砂纸用完，先停在这里",
        }, now_iso=T2)["day_directions"][0]
        self.assertEqual(paused["status"], "paused_at_breakpoint")
        self.assertEqual(paused["breakpoint_note_cn"], "砂纸用完，先停在这里")
        self.assertFalse(paused["executed_in_world"])
        self.assertIsNone(paused["failure_judgment"])

        # Hard honesty rule: completion without a real artifact/action ref is refused.
        with self.assertRaises(echoes_engine.V08ValidationError) as ctx:
            self._mutate({
                "action": "advance_or_interrupt_direction",
                "direction_id": direction_id,
                "transition": "mark_completed_with_ref",
            }, now_iso=T2)
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_REF_REQUIRED")

        receipt_key = self._seed_matter_receipt("op_window_frame_v3")
        completed = self._mutate({
            "action": "advance_or_interrupt_direction",
            "direction_id": direction_id,
            "transition": "mark_completed_with_ref",
            "evidence_ref": receipt_key,
            "linked_object_ids": ["window_frame"],
        }, now_iso=T2)["day_directions"][0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["evidence_ref"], receipt_key)
        self.assertTrue(completed["executed_in_world"])
        self.assertTrue(completed["verified"])

        faded_id = self._mutate(_sketch_payload(
            horizon_note_cn="傍晚也许会去码头走一小段",
            next_decision_boundary_cn="出门前再决定",
        ))["day_directions"][-1]["direction_id"]
        faded = self._mutate({
            "action": "advance_or_interrupt_direction",
            "direction_id": faded_id,
            "transition": "let_fade",
        }, now_iso=T2)["day_directions"][-1]
        self.assertEqual(faded["status"], "faded")
        self.assertIsNone(faded["failure_judgment"])

        with self.assertRaises(echoes_engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "advance_or_interrupt_direction",
                "direction_id": "dd_missing",
                "transition": "mark_started",
            })
        self.assertEqual(ctx.exception.reason_code, "DIRECTION_NOT_FOUND")

    def test_03_partner_view_strips_private_and_blocks_subject_actions(self) -> None:
        self._mutate(_sketch_payload(visibility="private"))
        shared = self._mutate(_sketch_payload(
            horizon_note_cn="想一起把阳台那盆植物挪个位置",
            bound_ref="place:balcony",
            visibility="shared",
        ))["day_directions"][-1]

        partner = echoes_engine.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(
            [row["direction_id"] for row in partner["day_directions"]],
            [shared["direction_id"]],
        )
        self.assertNotIn("窗台上", json.dumps(partner, ensure_ascii=False))

        subject = echoes_engine.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(len(subject["day_directions"]), 2)

        # Partner door must not sketch or advance a subject direction.
        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root, _sketch_payload(), actor_side="partner", now_iso=T0
        )
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")
        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "advance_or_interrupt_direction",
                "direction_id": shared["direction_id"],
                "transition": "mark_started",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(code, 403)

    def test_04_http_gate_refuses_completion_without_evidence(self) -> None:
        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root, _sketch_payload(), actor_side="subject", now_iso=T0
        )
        self.assertEqual(code, 200, body)
        direction_id = body["data"]["day_directions"][0]["direction_id"]

        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "advance_or_interrupt_direction",
                "direction_id": direction_id,
                "transition": "mark_completed_with_ref",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 400, body)
        self.assertEqual(body["reason_code"], "EVIDENCE_REF_REQUIRED")

        # The bounded situational matcher exposes up to 3 active non-faded directions.
        self._mutate(_sketch_payload(
            horizon_note_cn="晚上想给窗框上一层薄蜡",
            visibility="shared",
        ))
        matched = echoes_engine.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框", include_private=True
        )
        self.assertEqual(len(matched["matched_directions"]), 2)
        self.assertEqual(matched["llm_calls"], 0)

        self._mutate({
            "action": "advance_or_interrupt_direction",
            "direction_id": direction_id,
            "transition": "let_fade",
        }, now_iso=T2)
        after_fade = echoes_engine.match_situational_echoes_zero_write(
            self.root, incoming_text="窗框", include_private=True
        )
        self.assertEqual(len(after_fade["matched_directions"]), 1)
        self.assertNotIn(
            direction_id,
            [row["direction_id"] for row in after_fade["matched_directions"]],
        )


class ConcernDrivenAttentionLensTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v03-lens-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record_encounter(self, target_ref: str, title_cn: str, impression_cn: str) -> None:
        enc_engine.apply_encounters_mutation(
            self.root,
            {
                "action": "record_first_glance",
                "target_ref": target_ref,
                "target_title_cn": title_cn,
                "seen_pages": [1],
                "total_pages": 4,
                "current_impression_cn": impression_cn,
            },
            actor_side="subject",
            now_iso=T0,
        )

    def test_01_same_shelf_flips_with_the_active_concern(self) -> None:
        self._record_encounter("reading:frame_sketch", "窗框速写练习册", "线条很松，翻着舒服")
        self._record_encounter("reading:night_zine", "夜景照明摄影志", "光比很大，看着安静")

        frame_cues = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["窗框"]
        )
        self.assertTrue(frame_cues)
        self.assertEqual(frame_cues[0]["title_cn"], "窗框速写练习册")
        self.assertEqual(frame_cues[0]["source_kind"], "encounter")
        self.assertEqual(frame_cues[0]["matched_concern_cn"], "窗框")
        self.assertGreater(frame_cues[0]["overlap_score"], 0)
        self.assertTrue(str(frame_cues[0]["ref"]).startswith("enc_"))

        night_cues = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["夜景"]
        )
        self.assertTrue(night_cues)
        self.assertEqual(night_cues[0]["title_cn"], "夜景照明摄影志")
        self.assertEqual(night_cues[0]["matched_concern_cn"], "夜景")
        self.assertNotIn(
            "窗框速写练习册", [cue["title_cn"] for cue in night_cues]
        )

        no_concern = enc_engine.project_concern_driven_attention_zero_write(self.root)
        self.assertEqual(no_concern, [])

    def test_02_lens_reads_active_question_from_state(self) -> None:
        self._record_encounter("reading:frame_sketch", "窗框速写练习册", "线条很松")
        self._record_encounter("reading:night_zine", "夜景照明摄影志", "光比很大")
        encounter_id = enc_engine.project_v03_encounters(
            self.root, has_subject_capability=True, now_iso=T0
        )["encounters"][0]["encounter_id"]

        # Driving the lens from the file: an active question about night lighting.
        enc_engine.apply_encounters_mutation(
            self.root,
            {
                "action": "revisit_with_question",
                "encounter_id": encounter_id,
                "newly_seen_pages": [2],
                "active_question_cn": "夜景里的灯怎么留？",
                "noticed_detail_cn": "长曝光那页的亮度分布很讲究",
            },
            actor_side="subject",
            now_iso=T1,
        )
        cues = enc_engine.project_concern_driven_attention_zero_write(self.root)
        self.assertEqual(cues[0]["title_cn"], "夜景照明摄影志")

        projected = enc_engine.project_v03_encounters(
            self.root, has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(projected["attention_cues"], cues)

    def test_03_sealed_excerpt_never_matches_until_opened(self) -> None:
        partner_projection, _ = enc_engine.apply_encounters_mutation(
            self.root,
            {
                "action": "drop_shared_material",
                "title_cn": "旧城散步笔记",
                "excerpt_cn": "内页反复比较螺钿反光与路灯的关系",
                "topic_tag": "散步",
                "page_count": 3,
            },
            actor_side="partner",
            now_iso=T0,
        )
        material = partner_projection["shared_materials"][0]
        material_id = material["material_id"]
        self.assertEqual(material["status"], enc_engine.MATERIAL_RECEIVED)

        sealed = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿"]
        )
        self.assertNotIn(material_id, [cue["ref"] for cue in sealed])

        # Title/topic are visible even before opening, and they still match.
        by_title = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["旧城"]
        )
        self.assertIn(material_id, [cue["ref"] for cue in by_title])

        enc_engine.apply_encounters_mutation(
            self.root,
            {
                "action": "open_or_use_shared_material",
                "material_id": material_id,
                "mode": "open_partial",
                "opened_pages": [1],
            },
            actor_side="subject",
            now_iso=T1,
        )
        opened = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿"]
        )
        self.assertIn(material_id, [cue["ref"] for cue in opened])

    def test_04_withdrawn_material_never_matches(self) -> None:
        projection, _ = enc_engine.apply_encounters_mutation(
            self.root,
            {
                "action": "drop_shared_material",
                "title_cn": "螺钿工艺笔记",
                "excerpt_cn": "螺钿镶片要顺着光走",
                "topic_tag": "工艺",
                "page_count": 2,
            },
            actor_side="partner",
            now_iso=T0,
        )
        material_id = projection["shared_materials"][0]["material_id"]

        before = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿"]
        )
        self.assertIn(material_id, [cue["ref"] for cue in before])

        enc_engine.apply_encounters_mutation(
            self.root,
            {"action": "withdraw_shared_material", "material_id": material_id},
            actor_side="partner",
            now_iso=T1,
        )
        after = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["螺钿"]
        )
        self.assertNotIn(material_id, [cue["ref"] for cue in after])

    def test_05_lens_is_zero_write_and_partner_view_omits_it(self) -> None:
        self._record_encounter("reading:frame_sketch", "窗框速写练习册", "线条很松")

        before = _snapshot(self.root)
        cues = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["窗框"]
        )
        subject_projection = enc_engine.project_v03_encounters(
            self.root, has_subject_capability=True, now_iso=T0
        )
        code, body = enc_view.read_v03_encounters_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        after = _snapshot(self.root)
        self.assertEqual(before, after, "the lens must never touch bytes or mtimes")
        self.assertTrue(cues)
        self.assertIn("attention_cues", subject_projection)
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["attention_cues"], subject_projection["attention_cues"])

        partner = enc_engine.project_v03_encounters(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertNotIn("attention_cues", partner)
        _code, partner_body = enc_view.read_v03_encounters_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertNotIn("attention_cues", partner_body["data"])

    def test_06_up_to_six_bounded_cues(self) -> None:
        for index in range(8):
            self._record_encounter(
                f"reading:shelf_{index}",
                f"窗框图册第{index}册",
                "同一面架子上的不同册",
            )
        cues = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["窗框"]
        )
        self.assertEqual(len(cues), 6)
        refs = [cue["ref"] for cue in cues]
        self.assertEqual(len(refs), len(set(refs)))
        # Deterministic stable ordering: shelf order, no reshuffle between calls.
        again = enc_engine.project_concern_driven_attention_zero_write(
            self.root, concerns=["窗框"]
        )
        self.assertEqual(refs, [cue["ref"] for cue in again])


class PersonNeutralCopyTest(unittest.TestCase):
    def test_modules_have_no_hardcoded_identity(self) -> None:
        for rel in (
            "components/life/scripts/v08_emergent_echoes.py",
            "components/phone/v08_emergent_echoes_view.py",
            "components/life/scripts/v03_epistemic_encounters.py",
            "components/phone/v03_encounters_view.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("千代", text, f"{rel} hard-codes a name")
            # U+5979 is the feminine third-person pronoun; the copy uses TA instead.
            self.assertNotIn("她", text, f"{rel} hard-codes the feminine pronoun")


if __name__ == "__main__":
    unittest.main()
