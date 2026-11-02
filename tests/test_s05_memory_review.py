"""S05 只读复核模块的最小用例：身份/准入/当前召回分开，派生层不冒充。

不跑真实 M0/SQLite；真实链由 ``scripts/s05_memory_reliability_demo.py`` 验。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_memory_review as review  # noqa: E402

HANDOFF_ID = "lexp:result:0123456789abcdef"
EVENT_ID = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
DESCRIPTION = "PRIVATE-CONFIRMED-DESCRIPTION-MUST-NOT-LEAK"


class _FakeService:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def list_experiences(self, *, query="", limit=20):
        self.queries.append(query)
        return {"ok": True, "status": "READY",
                "data": {"experiences": list(self.rows), "count": len(self.rows)}}


class _UnavailableService:
    """The shape a named read failure takes: ``experiences`` is None, not an empty list."""

    def list_experiences(self, *, query="", limit=20):
        return {"ok": False, "status": "UNAVAILABLE", "reason": "MEMORY_READ_FAILED",
                "detail": "RuntimeError: injected source read failure",
                "data": {"experiences": None, "count": None}}


def _state(delivery_disposition="ACCEPT"):
    return {
        "handoff_events_by_id": {
            HANDOFF_ID: {
                "handoff_id": HANDOFF_ID,
                "activity_ref": "actv:demo",
                "activity_revision": 3,
                "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
                "occurred_at": "2026-10-27T10:00:00+00:00",
                "action_refs": ["action:demo"],
                "settlement_refs": ["settlement:demo"],
                "artifact_refs": ["artifact:demo", "artifactversion:v2"],
                "owner_confirmed_context": {
                    "artifact_id": "artifact:demo",
                    "version_ref": "artifactversion:v2",
                    "title": "T",
                    "result_description": DESCRIPTION,
                    "outcome_confirmed": True,
                },
            }
        },
        "handoff_deliveries_by_id": {
            HANDOFF_ID: {
                "handoff_id": HANDOFF_ID,
                "attempt": 1,
                "status": "DELIVERED",
                "consumer_disposition": delivery_disposition,
                "last_error": None,
                "delivered_at": "2026-10-27T10:00:01+00:00",
            }
        },
    }


def _row(text="PRIVATE BODY TEXT"):
    return {
        "experience_id": EVENT_ID,
        "event_id": EVENT_ID,
        "occurred_at": "2026-10-27T10:00:00+00:00",
        "source_ref": HANDOFF_ID,
        "activity_ref": "actv:demo",
        "action_ref": "action:demo",
        "artifact_ref": "artifact:demo",
        "version_ref": "artifactversion:v2",
        "owner_result": "settlement:demo",
        "outcome_kind": "SIGNIFICANT_PROGRESS",
        "text": text,
    }


class MemoryReviewTest(unittest.TestCase):

    def test_identity_and_receipt_are_separate_from_current_recall(self):
        service = _FakeService([_row()])
        payload = review.review_handoff(_state(), HANDOFF_ID, service)
        self.assertEqual(payload["handoff"]["identity_namespace"], review.IDENTITY_RESULT)
        self.assertEqual(payload["handoff"]["owner_confirmed"]["version_ref"],
                         "artifactversion:v2")
        self.assertTrue(payload["handoff"]["owner_confirmed"]["description_present"])
        self.assertEqual(payload["handoff"]["admission_receipt"]["consumer_disposition"], "ACCEPT")
        self.assertEqual(payload["current_recall"]["status"], review.RECALL_AVAILABLE)
        self.assertTrue(payload["separation"]["handoff_succeeded"])
        self.assertTrue(payload["separation"]["memory_recallable"])

    def test_a_withheld_record_keeps_the_historical_receipt_but_is_not_recallable(self):
        service = _FakeService([])
        payload = review.review_handoff(_state(), HANDOFF_ID, service)
        self.assertEqual(payload["current_recall"]["status"], review.RECALL_NOT_RECALLABLE)
        self.assertTrue(payload["separation"]["handoff_succeeded"])
        self.assertFalse(payload["separation"]["memory_recallable"])
        self.assertEqual(payload["layers"]["accepted_memory_record"]["status"],
                         review.RECALL_NOT_RECALLABLE)

    def test_a_failed_read_is_named_not_turned_into_an_empty_recall(self):
        payload = review.review_handoff(_state(), HANDOFF_ID, _UnavailableService())
        self.assertEqual(payload["current_recall"]["status"], "UNAVAILABLE")
        self.assertIsNone(payload["current_recall"]["records"])
        self.assertFalse(payload["separation"]["memory_recallable"])
        self.assertEqual(payload["layers"]["accepted_memory_record"]["status"],
                         "UNAVAILABLE")

    def test_derived_layers_are_named_not_invented(self):
        payload = review.review_handoff(_state(), HANDOFF_ID, _FakeService([_row()]))
        layers = payload["layers"]
        self.assertEqual(layers["source_evidence"]["status"], "AVAILABLE")
        self.assertEqual(layers["accepted_memory_record"]["status"], "AVAILABLE")
        for name in ("episode_grouping", "episode_summary", "embedding_index",
                     "derived_invalidation"):
            self.assertEqual(layers[name]["status"], review.NOT_IMPLEMENTED, name)

    def test_duplicate_is_a_storage_answer_not_a_semantic_do_not_admit(self):
        payload = review.review_handoff(_state(delivery_disposition="DUPLICATE"),
                                        HANDOFF_ID, _FakeService([_row()]))
        self.assertEqual(payload["no_admission"]["storage_level"],
                         "NO_CHANGE_AT_M0_IDEMPOTENT_ADMISSION")
        self.assertTrue(payload["no_admission"]["zero_new_record"])
        self.assertEqual(payload["no_admission"]["semantic_do_not_admit"],
                         review.NOT_IMPLEMENTED)

    def test_no_body_text_reaches_the_review_output(self):
        payload = review.review_handoff(_state(), HANDOFF_ID, _FakeService([_row()]))
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PRIVATE BODY TEXT", encoded)
        self.assertNotIn(DESCRIPTION, encoded)
        self.assertNotIn("text", payload["current_recall"]["records"][0])

    def test_list_handoffs_is_content_free(self):
        rows = review.list_handoffs(_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["handoff_id"], HANDOFF_ID)
        self.assertNotIn(DESCRIPTION, json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
