#!/usr/bin/env python3
"""Worker 10B: Ch.42 cross-ring borrow/return reaches phone hints, lived context
and the daily loop without adding a surface or any debt/relationship penalty.

The suite is resilient alongside Worker 10A: the engine scan that produces
``v08_matter:return_loan:*`` may or may not have landed, so the daily-loop test
builds the descriptor directly and mocks the engine settle call.  The phone-hint
and lived-context invariants always run.

Run (WSL): ``python -B -m unittest tests.test_v08_ch42_loop_and_context -v``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
LOAN_STEP_OPS = (
    "BORROW_NEIGHBORHOOD_ITEM",
    "RETURN_BORROWED_ITEM",
    "DELIVER_CARD_TO_PUBLIC_BOARD",
)
DEBT_WORDS = ("欠", "债", "愧疚", "内疚", "罚", "扣分", "好感")


def _doc(objects, zones=None, traces=None, routines=None):
    return {
        "perspective": "subject",
        "active_traces": traces or [],
        "routines": routines or [],
        "matter_hints": [],
        "matter_space": {"zones": zones or [], "objects": objects},
    }


class PhoneHintInvariants(unittest.TestCase):
    """The surface count stays frozen and the loan hints are present."""

    def test_surface_count_stays_37(self):
        self.assertIn("v08_living_traces", phone_tool.LIFE_UPDATE_SURFACES)
        # ``test_hermes_hot_reconnect_and_surfaces`` pins this exact 37-key set.
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_request_loan_extension_is_a_subject_action(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        self.assertIn("request_loan_extension", hint["subject_actions"])
        self.assertEqual(
            hint["core_fields"]["request_loan_extension"],
            ["object_id", "extra_hours", "note_cn"],
        )
        # It sits alongside compose_open_act, which keeps its own contract.
        self.assertEqual(hint["core_fields"]["compose_open_act"], ["intent_cn", "steps"])

    def test_step_ops_name_cross_ring_actions(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        for op in LOAN_STEP_OPS:
            self.assertIn(op, hint["step_ops"])
        # The original eleven primitives keep their order.
        self.assertEqual(
            tuple(hint["step_ops"][:11]),
            (
                "PLACE_OR_MOVE", "INTRODUCE_ITEM", "TRANSFORM_CREATE",
                "CONSUME_PORTION", "CLEAN_SURFACE_OR_VESSEL", "DISCARD_ITEM",
                "OBSERVE_OBJECT",
                "SPLIT_MATERIAL", "ASSEMBLE_OR_DETACH_PART",
                "TRANSCRIBE_TO_PAPER", "PATCH_OR_REPAIR",
            ),
        )

    def test_loan_extension_is_registered_for_opkey_and_capability(self):
        # Even if the surface later narrows, the named set keeps the new action explicit.
        self.assertIn(
            "request_loan_extension", phone_tool._V08_LIVING_TRACES_OPKEY_SUBACTIONS
        )
        self.assertIn(
            "compose_open_act", phone_tool._V08_LIVING_TRACES_OPKEY_SUBACTIONS
        )
        self.assertIn(
            "request_loan_extension",
            phone_tool._V08_LIVING_TRACES_CAPABILITY_SUBACTIONS,
        )


class LivedContextLoanCue(unittest.TestCase):
    """At most one neutral loan mention, bounded by 160/1800 chars."""

    def test_max_lived_context_chars_is_unchanged(self):
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

    def test_borrowed_item_is_mentioned_neutrally(self):
        doc = _doc([{
            "object_id": "nb_shared_shelf_ruler",
            "label_cn": "木尺",
            "parent_id": "home_desk",
            "loan_meta": {"loan_status": "borrowed", "source_ring": "shared_shelf"},
        }])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertTrue(line.startswith("- 屋里动态："))
        self.assertIn("手边有借来的「木尺」待归还", line)
        self.assertLessEqual(len(line), 160)
        self.assertLessEqual(len(line), lcb.MAX_LIVED_CONTEXT_CHARS)
        for word in DEBT_WORDS:
            self.assertNotIn(word, line)

    def test_only_one_loan_mention_even_with_several(self):
        doc = _doc([
            {"object_id": "ruler", "label_cn": "木尺", "parent_id": "home_desk",
             "loan_meta": {"loan_status": "borrowed"}},
            {"object_id": "book", "label_cn": "旧书", "parent_id": "home_desk",
             "loan_meta": {"loan_status": "borrowed"}},
        ])
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("木尺", line)
        self.assertNotIn("旧书", line)

    def test_returned_or_absent_loan_meta_is_silent(self):
        doc = _doc([{
            "object_id": "ruler",
            "label_cn": "木尺",
            "parent_id": "home_desk",
            "loan_meta": {"loan_status": "returned_received"},
        }])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=True), ""
        )

    def test_inactive_object_is_ignored(self):
        doc = _doc([{
            "object_id": "ruler",
            "label_cn": "木尺",
            "parent_id": "home_desk",
            "loan_meta": {"loan_status": "borrowed"},
            "active": False,
        }])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=True), ""
        )

    def test_private_object_never_leaks_without_capability(self):
        doc = _doc([{
            "object_id": "ruler",
            "label_cn": "木尺",
            "parent_id": "home_desk",
            "visibility": "private",
            "loan_meta": {"loan_status": "borrowed"},
        }], zones=[{"zone_id": "home_desk", "visibility": "shared"}])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=False), ""
        )
        self.assertIn(
            "木尺",
            lcb._format_v08_living_traces_micro(doc, include_private=True),
        )

    def test_object_in_private_zone_never_leaks_without_capability(self):
        doc = _doc([{
            "object_id": "ruler",
            "label_cn": "木尺",
            "parent_id": "subject_private_drawer",
            "loan_meta": {"loan_status": "borrowed"},
        }], zones=[{"zone_id": "subject_private_drawer", "visibility": "private"}])
        self.assertEqual(
            lcb._format_v08_living_traces_micro(doc, include_private=False), ""
        )
        self.assertIn(
            "木尺",
            lcb._format_v08_living_traces_micro(doc, include_private=True),
        )

    def test_line_stays_capped_with_traces_and_loan(self):
        doc = _doc(
            [{
                "object_id": "ruler",
                "label_cn": "木尺",
                "parent_id": "home_desk",
                "loan_meta": {"loan_status": "borrowed"},
            }],
            traces=[
                {"object_label_cn": "窗台", "state_sticker_cn": "留着一点暖意",
                 "author_side": "partner", "fade_mode": "timed", "remaining_minutes": 30},
                {"object_label_cn": "书桌", "state_sticker_cn": "摊着没写完的纸",
                 "author_side": "subject", "fade_mode": "until_touched"},
            ],
            routines=[{"title_cn": "睡前把杯子收到水池"}],
        )
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertLessEqual(len(line), 160)


class DailyLoopReturnPassThrough(unittest.TestCase):
    """``v08_matter:return_loan:*`` rides ``OPEN_MATTER_ACT`` with no penalty."""

    def test_event_builder_maps_return_loan_payload(self):
        payload = {
            "intent_cn": "顺手把借来的木尺拿去还",
            "steps": [{"op": "RETURN_BORROWED_ITEM", "object_id": "nb_shared_shelf_ruler"}],
        }
        event = bridge._v02v03_event_from_descriptor(
            "cand:v08_matter_return_loan",
            {
                "source_ref": "v08_matter:return_loan:nb_shared_shelf_ruler",
                "action_family": "compose_open_act",
                "open_act_payload": payload,
            },
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["action_kind"], "OPEN_MATTER_ACT")
        self.assertEqual(
            event["open_act_payload"]["steps"][0]["op"], "RETURN_BORROWED_ITEM"
        )
        self.assertIsNot(event["open_act_payload"], payload)
        for key in ("affection_penalty", "debt", "guilt", "relationship_penalty"):
            self.assertNotIn(key, event)

    def test_settle_forwards_return_step_and_names_slice(self):
        captured = {}

        def fake_compose(root, composed, *, actor_side, now_iso):
            captured["payload"] = dict(composed)
            captured["actor_side"] = actor_side
            return {
                "status": "applied",
                "intent_cn": "顺手把借来的木尺拿去还",
                "steps_total": 1,
                "steps_applied": 1,
            }

        event = {
            "open_act_payload": {
                "intent_cn": "顺手把借来的木尺拿去还",
                "steps": [{"op": "RETURN_BORROWED_ITEM", "object_id": "nb_shared_shelf_ruler"}],
            },
            "matter_target_ref": "v08_matter:return_loan:nb_shared_shelf_ruler",
            "operation_key": "op-return-1",
            "decision_id": "dec-return-1",
        }
        slices: list[str] = []
        details: dict = {}
        inst_root = Path(tempfile.mkdtemp(prefix="v08ch42-inst-"))
        life_root = Path(tempfile.mkdtemp(prefix="v08ch42-life-"))
        with mock.patch.object(bridge.v08_matter, "compose_open_act", fake_compose):
            out = bridge._settle_v08_matter_open_act(
                inst_root,
                life_root,
                event,
                now_iso=T0,
                slices_updated=slices,
                details=details,
            )
        self.assertIsNone(out)
        self.assertEqual(captured["actor_side"], "subject")
        self.assertEqual(
            captured["payload"]["steps"][0]["op"], "RETURN_BORROWED_ITEM"
        )
        self.assertTrue(captured["payload"]["operation_key"].startswith("op-return-1"))
        self.assertIn("world.v08_matter_space", slices)
        self.assertEqual(details["v08_matter_act"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
