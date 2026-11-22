#!/usr/bin/env python3
"""Round C2a: quiet-tick quota removal, 4-way micro-intent soft landing, phone parity.

Covers, against the real bridge/engine (0 product-model calls):

* a quiet root with reachable objects but no trace/opportunity no longer emits a
  synthetic ``home_window:touch_object`` puppet candidate;
* a real partner trace still yields the honest response candidate;
* the thin deterministic classifier reaches all four outcomes:
  ``direct_supported`` (coat drape / brush-in-vase / leaf on page 42),
  ``compositional_supported`` (take -> tuck on page -> note),
  ``coarse_approximation`` (warm mug, with explicit non-guarantees) and
  ``unsupported_capability`` (origami / paperweight wind lock);
* executing an unsupported intent soft-lands, appends one compact
  ``unsupported_intents.jsonl`` line, records a cooldown that suppresses the
  intent until ``expires_at``, and touches zero world/completion state;
* the phone surface count stays exactly 37 and its guidance stays <= 1200 chars.

Run (WSL): ``python -B -m unittest tests.test_v08_daily_loop_intent_landing -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
from components.world.scripts import v08_open_affordance as open_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from plugins import chiyo as plugin_mod  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1H = "2026-11-16T10:00:00+08:00"
T5H = "2026-11-16T14:00:00+08:00"


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


def _write_containment(root: Path) -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "stance_containment.json").write_text(
        json.dumps({
            "schema_version": 1,
            "revision": 1,
            "containment_domain": {
                "containers": [{
                    "container_id": "pen_cup",
                    "label": "木质笔筒",
                    "place_id": "study_desk",
                }],
                "items": [
                    {"item_id": "fountain_pen", "label": "墨水钢笔", "item_class": "writing_tool", "place_id": "study_desk"},
                    {"item_id": "sketch_pencil", "label": "木杆铅笔", "item_class": "writing_tool", "place_id": "study_desk"},
                ],
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class _IntentBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-intent-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def introduce(self, object_id: str, label: str, kind: str, target: str, **extra: Any) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return engine.compose_open_act(
            self.root, {"intent_cn": f"放入{label}", "steps": [step]}, now_iso=T0
        )

    def classify(self, intent: dict, *, now: str = T0) -> dict:
        return bridge.classify_and_normalize_micro_intent(self.root, intent, now_iso=now)


class QuietTickQuotaTest(_IntentBase):
    def test_01_quiet_root_emits_no_forced_open_act_puppet(self) -> None:
        _write_containment(self.root)
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        self.assertEqual(collected["llm_calls"], 0)
        descriptors = [
            dict(desc)
            for desc in (collected.get("descriptors") or {}).values()
            if isinstance(desc, dict)
        ]
        open_refs = [
            str(desc.get("source_ref") or "")
            for desc in descriptors
            if str(desc.get("source_ref") or "").startswith("v08_open_act:")
        ]
        self.assertEqual(open_refs, [], "a quiet root must not fabricate a touch-object puppet")
        titles = " ".join(str(desc.get("title_hint") or "") for desc in descriptors)
        self.assertNotIn("随手摆弄", titles)

    def test_02_partner_trace_still_yields_honest_response_candidate(self) -> None:
        _write_containment(self.root)
        open_engine.leave_partner_trace(
            self.root,
            {
                "object_id": "home_desk",
                "gesture_cn": "放下杯垫",
                "state_sticker_cn": "杯垫压着一张小票",
                "fade_mode": "until_touched",
            },
            now_iso=T0,
        )
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        refs = {
            str(desc.get("source_ref") or "")
            for desc in (collected.get("descriptors") or {}).values()
            if isinstance(desc, dict)
        }
        self.assertIn("v08_open_act:home_desk:respond_partner_trace", refs)


class FourWayClassifierTest(_IntentBase):
    def setUp(self) -> None:
        super().setUp()
        self.introduce("coat_01", "外套", "everyday", "home_staging_corner")
        self.introduce("vase_01", "花瓶", "vessel", "dining_table", capacity_slots=3)
        self.introduce("brush_01", "画笔", "tool", "kitchen_counter")
        self.introduce(
            "book_01", "旧书", "paper", "home_staging_corner",
            paper_like=True, capacity_slots=1,
        )
        self.introduce("leaf_01", "干叶", "keepsake", "home_staging_corner")
        self.introduce("mug_01", "热茶杯", "drink", "dining_table", thermal="hot")

    def test_01_direct_supported_coat_drape(self) -> None:
        out = self.classify({
            "intent_cn": "把外套搭在床尾",
            "object_id": "coat_01",
            "target_parent_id": "home_staging_corner",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["classification"], "direct_supported")
        self.assertEqual(out["llm_calls"], 0)
        self.assertEqual(len(out["steps"]), 1)
        step = out["steps"][0]
        self.assertEqual(step["op"], "MOVE_OBJECT")
        self.assertEqual(step["placement_relation"], "draped_on")
        self.assertEqual(step["posture_tag"], "casual_drape")

    def test_02_direct_supported_brush_in_vase(self) -> None:
        out = self.classify({
            "intent_cn": "把画笔插进花瓶",
            "object_id": "brush_01",
            "target_parent_id": "vase_01",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["classification"], "direct_supported")
        self.assertEqual(out["steps"][0]["op"], "PLACE_INSIDE")
        self.assertEqual(out["steps"][0]["placement_relation"], "inside")

    def test_03_direct_supported_leaf_on_page_42(self) -> None:
        out = self.classify({
            "intent_cn": "将干叶夹在书里第42页",
            "object_id": "leaf_01",
            "target_parent_id": "book_01",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["classification"], "direct_supported")
        step = out["steps"][0]
        self.assertEqual(step["op"], "PLACE_INSIDE")
        self.assertEqual(step["placement_relation"], "tucked_in")
        self.assertEqual(step["anchor_page"], 42)

    def test_04_compositional_supported_take_tuck_note(self) -> None:
        out = self.classify({
            "intent_cn": "把干叶夹在书里第42页，再写一句",
            "object_id": "leaf_01",
            "target_parent_id": "book_01",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["classification"], "compositional_supported")
        ops = [step["op"] for step in out["steps"]]
        self.assertEqual(ops, ["TAKE_OBJECT", "PLACE_INSIDE", "NOTE_ASSOCIATION"])
        self.assertEqual(out["steps"][1]["anchor_page"], 42)
        self.assertEqual(out["steps"][1]["placement_relation"], "tucked_in")

    def test_05_coarse_approximation_warm_mug_has_explicit_non_guarantee(self) -> None:
        out = self.classify({
            "intent_cn": "贴着热杯暖手",
            "object_id": "mug_01",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["classification"], "coarse_approximation")
        self.assertTrue(out["approximation"])
        self.assertTrue(out["what_is_approximated"])
        self.assertIn("温度", out["cannot_guarantee"])
        self.assertNotIn("℃", json.dumps(out, ensure_ascii=False))
        ops = [step["op"] for step in out["steps"]]
        self.assertIn("TAKE_OBJECT", ops)
        self.assertIn("NOTE_ASSOCIATION", ops)

    def test_06_unsupported_origami_and_paperweight(self) -> None:
        for text in ("把这张纸折成一只纸鹤", "用镇纸压住被风吹起的纸"):
            out = self.classify({"intent_cn": text})
            self.assertFalse(out["ok"], out)
            self.assertEqual(out["classification"], "unsupported_capability")
            self.assertEqual(out["reason_code"], "UNSUPPORTED_CAPABILITY")
            self.assertEqual(out["soft_landing"], "defer_and_switch")
            self.assertEqual(out["steps"], [])

    def test_07_supported_execution_really_places_brush(self) -> None:
        out = bridge.execute_normalized_micro_intent(
            self.root,
            {
                "intent_cn": "把画笔插进花瓶",
                "object_id": "brush_01",
                "target_parent_id": "vase_01",
            },
            now_iso=T0,
            operation_key="intent-brush-1",
        )
        self.assertTrue(out["settled"], out)
        state = engine._read_state_zero_write(self.root)
        brush = state["matter_objects"]["brush_01"]
        self.assertEqual(brush["parent_id"], "vase_01")
        self.assertEqual(brush["placement_relation"], "inside")


class UnsupportedSoftLandingTest(_IntentBase):
    def test_01_unsupported_soft_lands_logs_and_cools_down(self) -> None:
        _write_containment(self.root)
        world_before = _snapshot(self.root / "world")
        intent = {"intent_cn": "把这张纸折成一只纸鹤", "zone_id": "home"}

        out = bridge.execute_normalized_micro_intent(self.root, intent, now_iso=T0)
        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["status"], "SOFT_LANDED")
        self.assertEqual(out["reason_code"], "UNSUPPORTED_CAPABILITY")

        # One compact line, no stack trace / long blob.
        log_path = self.root / "notes" / "unsupported_intents.jsonl"
        self.assertTrue(log_path.is_file())
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        for key in ("ts", "intent_key", "category", "summary", "zone_id", "reason_code"):
            self.assertIn(key, row)
        self.assertEqual(row["reason_code"], "UNSUPPORTED_CAPABILITY")

        # Cooldown persisted with a 4h horizon.
        cooldown_path = self.root / "life" / "v02_v03_intent_cooldowns.json"
        self.assertTrue(cooldown_path.is_file())
        cooldown_doc = json.loads(cooldown_path.read_text(encoding="utf-8"))
        cooldowns = cooldown_doc["cooldowns"]
        self.assertEqual(len(cooldowns), 1)
        entry = next(iter(cooldowns.values()))
        self.assertEqual(entry["category"], "ORIGAMI_FOLD_GEOMETRY")
        recorded = datetime.fromisoformat(entry["recorded_at"])
        expires = datetime.fromisoformat(entry["expires_at"])
        self.assertEqual(expires - recorded, timedelta(hours=4))

        # Cooldown suppresses the exact same intent until expiry.
        self.assertEqual(
            bridge.filter_cooled_down_intents([intent], root=self.root, now_iso=T1H),
            [],
        )
        self.assertEqual(
            len(bridge.filter_cooled_down_intents([intent], root=self.root, now_iso=T5H)),
            1,
        )

        # Zero world state / completion records changed.
        self.assertEqual(_snapshot(self.root / "world"), world_before)
        self.assertFalse(engine.state_path(self.root).exists())
        ledger = self.root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
        self.assertFalse(ledger.exists())

    def test_02_settlement_path_soft_lands_unsupported_intent(self) -> None:
        event = {
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "action_kind": "MICRO_INTENT",
            "v04_action": "micro_intent",
            "receipt_ref": "owner-receipt:micro-1",
            "decision_id": "micro-1",
            "micro_intent": {"intent_cn": "用镇纸压住被风吹起的纸", "zone_id": "home"},
        }
        out = bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T0)
        self.assertFalse(out["settled"])
        self.assertTrue(out["soft_landed"])
        self.assertEqual(out["reason_code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(out["optimistic_writes"], 0)
        self.assertEqual(
            out["llm_calls"], 0, "the settlement must never call a product model"
        )

    def test_03_settlement_path_runs_supported_intent(self) -> None:
        self.introduce("brush_02", "画笔", "tool", "kitchen_counter")
        self.introduce("vase_02", "花瓶", "vessel", "dining_table", capacity_slots=3)
        event = {
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "action_kind": "MICRO_INTENT",
            "v04_action": "micro_intent",
            "receipt_ref": "owner-receipt:micro-2",
            "decision_id": "micro-2",
            "micro_intent": {
                "intent_cn": "把画笔插进花瓶",
                "object_id": "brush_02",
                "target_parent_id": "vase_02",
            },
        }
        out = bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T0)
        self.assertTrue(out["settled"], out)
        self.assertIn("world.v08_matter_space", out["slices_updated"])
        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["matter_objects"]["brush_02"]["parent_id"], "vase_02")


class PhoneSurfaceParityTest(unittest.TestCase):
    def test_01_surface_count_is_exactly_37(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_02_guidance_stays_within_budget(self) -> None:
        self.assertLessEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1200)

    def test_03_new_typed_ops_are_advertised(self) -> None:
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        step_ops = hint["step_ops"]
        for op in (
            "MOVE_OBJECT", "PLACE_INSIDE", "ATTACH_NOTE", "TAKE_OBJECT",
            "ARRANGE_OBJECTS", "NOTE_ASSOCIATION",
        ):
            self.assertIn(op, step_ops)
        # The original eleven keep their exact order (phone contract stability).
        self.assertEqual(
            tuple(step_ops[:11]),
            (
                "PLACE_OR_MOVE", "INTRODUCE_ITEM", "TRANSFORM_CREATE",
                "CONSUME_PORTION", "CLEAN_SURFACE_OR_VESSEL", "DISCARD_ITEM",
                "OBSERVE_OBJECT",
                "SPLIT_MATERIAL", "ASSEMBLE_OR_DETACH_PART",
                "TRANSCRIBE_TO_PAPER", "PATCH_OR_REPAIR",
            ),
        )
        self.assertEqual(hint["core_fields"]["compose_open_act"], ["intent_cn", "steps"])


if __name__ == "__main__":
    unittest.main()
