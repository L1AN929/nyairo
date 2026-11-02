#!/usr/bin/env python3
"""S08 F-093..F-097：未来组织只读面的最小域名用例（Windows 可跑，不需要 socket）。

只守三件事：

1. 空 root 与读失败是两件事：缺 store 是合法空表，坏 store 是具名 ``UNAVAILABLE``；
2. 未来事项的归属可以由**已提交 Decision 的 proposal** 重算出来，没有 Decision 就如实报
   ``NO_DECISION_RECORD_FOUND``，绝不贴到相邻事项上；
3. operator 义务不是她能改的对象：``future_item_actions`` 指向 ``oblig:`` 被域名按名拒绝；
   并且 S08 的承诺域仍报 ``NOT_IMPLEMENTED``，不把 fitem/oblig 改叫承诺。

真 owner 链（正式 evaluate_once + 重启）由 ``scripts/s08_future_organization_demo.py`` 承担。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_day_plan as ldp  # noqa: E402
import life_future_items as lfi  # noqa: E402
import life_future_organization_review as review  # noqa: E402

NOW = "2026-10-27T09:00:00+08:00"


class EmptyRootTest(unittest.TestCase):
    def test_absent_stores_are_a_real_empty_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            envelope = review.build_future_organization_view(
                state_root=Path(tmp), subject_id="subject:test", now=NOW)
            data = envelope["data"]
            # 缺 store 是合法空表；LR-4 的读面在 Windows 上缺 fcntl，另有具名不可用，
            # 这里只对三个 S08 store 断言"空"。
            self.assertEqual(data["future_items"]["status"], "OK")
            self.assertEqual(data["future_items"]["open_count"], 0)
            self.assertEqual(data["day_plan"]["count"], 0)
            self.assertEqual(data["obligations"]["count"], 0)
            self.assertFalse(data["obligations"]["writable_by_subject"])

    def test_no_commitment_owner_is_named_not_faked(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = review.build_future_organization_view(
                state_root=Path(tmp), subject_id="subject:test", now=NOW)["data"]
            self.assertEqual(data["commitment"]["state"], review.COMMITMENT_STATE_ABSENT)
            self.assertEqual(data["commitment"]["reason_code"], review.COMMITMENT_REASON)
            self.assertIsNone(data["commitment"]["owner"])
            self.assertEqual(data["renegotiation"]["state"], review.COMMITMENT_STATE_ABSENT)

    def test_a_corrupt_item_store_is_unavailable_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / lfi.STATE_FILENAME).write_text("{not json", encoding="utf-8")
            envelope = review.build_future_organization_view(
                state_root=root, subject_id="subject:test", now=NOW)
            self.assertFalse(envelope["ok"])
            self.assertEqual(envelope["status"], "UNAVAILABLE")
            self.assertEqual(envelope["data"]["future_items"]["status"], "UNAVAILABLE")
            self.assertEqual(
                envelope["data"]["sources"]["future_items"]["reason_code"],
                "FUTURE_ITEM_STATE_CORRUPT")


class ClaimAttributionTest(unittest.TestCase):
    def test_the_public_intent_key_is_the_one_the_store_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = lfi.FutureItems(Path(tmp), subject_id="subject:test")
            store.propose(title="晚上再看这一段", in_minutes=30, now=NOW)
            item = store.open_items()[0]
            expected = lfi.proposal_intent_key(
                title="晚上再看这一段", due_at=item.due_at,
                source_refs=item.source_refs, origin=item.origin)
            self.assertEqual(item.idempotency_key, expected)

    def test_a_committed_decision_is_attributed_by_recomputation(self):
        state = {"decisions_by_id": {"dec:1": {
            "decision_id": "dec:1", "created_at": NOW,
            "later_item_proposal": {"title": "晚上再看这一段", "in_minutes": 30,
                                    "due_at": "2026-10-27T01:30:00+00:00",
                                    "source_refs": [], "park_current": False}}}}
        claims = review._decision_claims(state, lfi)
        key = lfi.proposal_intent_key(
            title="晚上再看这一段", due_at="2026-10-27T01:30:00+00:00",
            source_refs=[], origin=lfi.ORIGIN_MODEL_PROPOSAL)
        self.assertEqual(claims[key]["decision_id"], "dec:1")

    def test_an_item_without_a_decision_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = lfi.FutureItems(Path(tmp), subject_id="subject:test")
            store.propose(title="没人认领的想法", in_minutes=30, now=NOW)
            data = review.build_future_organization_view(
                state_root=Path(tmp), subject_id="subject:test", now=NOW)["data"]
            claims = data["future_items"]["claims"]
            self.assertEqual(len(claims), 1)
            self.assertIsNone(claims[0]["decision_ref"])
            self.assertEqual(claims[0]["attribution"], "NO_DECISION_RECORD_FOUND")

    def test_a_cancelled_item_keeps_its_claim_but_leaves_the_open_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = lfi.FutureItems(root, subject_id="subject:test")
            store.propose(title="做完就取消", in_minutes=30, now=NOW)
            ref = store.open_items()[0].item_ref
            store.cancel(ref, now=NOW)
            data = review.build_future_organization_view(
                state_root=root, subject_id="subject:test", now=NOW)["data"]
            self.assertEqual(data["future_items"]["open_count"], 0)
            self.assertEqual([row["item_ref"] for row in data["future_items"]["claims"]], [ref])
            self.assertEqual(data["future_items"]["claims"][0]["status"], "CANCELLED")


class ScopeTest(unittest.TestCase):
    def test_future_item_actions_cannot_touch_an_operator_obligation(self):
        with self.assertRaises(lfi.FutureItemError) as ctx:
            lfi.validate_actions([{"item_ref": "oblig:abc123", "op": "cancel"}])
        self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_ACTION_REF_NOT_AN_ITEM")

    def test_the_read_surface_carries_no_promise_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lfi.FutureItems(root, subject_id="subject:test").propose(
                title="一件以后的事", in_minutes=30, now=NOW)
            data = review.build_future_organization_view(
                state_root=root, subject_id="subject:test", now=NOW)["data"]
            for row in data["future_items"]["entries"]:
                self.assertFalse(row["is_commitment"])
                self.assertNotIn("promised", row)
            self.assertNotIn('"promised"', json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
