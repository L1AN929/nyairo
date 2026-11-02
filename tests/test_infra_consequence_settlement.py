"""INFRA F155-160：三轴/分域只读复核的投影规则与只读边界。

这些是投影层测试：不给 store 写真数据，用最小的读面替身证明
“ACK 不是 owner receipt”“失败/挂起不被改写成成功”“envelope 不存在就说 NOT_IMPLEMENTED”。
完整的真实 owner 链在 ``scripts/infra_consequence_settlement_demo.py``。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import infra_settlement_review as review  # noqa: E402


SETTLEMENT_ID = "rset:0123456789abcdef"
ACTION_ID = "actn:0123456789abcdef"
ACTIVITY_ID = "actv_0123456789abcdef"


class FakeActionRead:
    def __init__(self, action=None):
        self.action = action

    def get_action(self, action_id):
        return dict(self.action) if self.action else None

    def list_actions(self):
        return [dict(self.action)] if self.action else []

    def list_attempts(self, action_id):
        return [{"attempt_id": "aatm:1", "status": "SUBMITTED",
                 "started_at": "2026-10-27T00:00:00+00:00"}]

    def list_receipts(self, action_id):
        return []

    def list_result_evidences(self, action_id):
        return []

    def list_reconciliations(self, action_id):
        return []


class FakeSettlementRead:
    def __init__(self, *, settle=True, deliveries=None):
        self.settle = settle
        self.deliveries = deliveries or []

    def trace_result_lineage(self, action_id):
        if not self.settle:
            return {"latest_settlement": None, "settlement_history": [],
                    "settled_result_events": [], "consumer_deliveries": []}
        record = {
            "settlement_id": SETTLEMENT_ID,
            "revision": 1,
            "settlement_status": "SETTLED",
            "outcome_kind": "SUCCESS_EFFECT",
            "policy_version": "chiyo.result_settlement.policy.v1",
            "effect_claims": [{
                "claim_id": "eclm:1", "domain": "artifact",
                "predicate": "artifact_written_verified",
                "epistemic_status": "CONFIRMED",
                "authority_grade": "OBSERVER_VERIFIED",
                "authority_domain": "artifact_authority",
                "object_ref": "artifact:abc"}],
            "confirmed_effect_refs": ["eclm:1"], "missing_effect_refs": [],
            "failed_effect_refs": [], "unresolved_claims": [], "conflict_refs": [],
        }
        event = {"event_id": "srev:1", "settlement_ref": SETTLEMENT_ID,
                 "activity_ref": ACTIVITY_ID, "settlement_revision": 1}
        return {"latest_settlement": record, "settlement_history": [record],
                "settled_result_events": [event], "consumer_deliveries": list(self.deliveries)}


class FakeIntegrationRead:
    def __init__(self, state=None):
        self.state = state or {}

    def read_state(self):
        return dict(self.state)


class FakeActivityRead:
    def __init__(self, status="ACTIVE"):
        self.status = status

    def get_activity(self, activity_id):
        return {"activity_id": activity_id, "status": self.status, "revision": 3,
                "completion_evidence_ref": None}


class FakeProgressRead:
    def __init__(self, *, seen=True):
        self.seen = seen

    def get_progress_state(self, activity_id):
        return {"progress_state": "UNCHANGED", "completion_status": "NOT_COMPLETE",
                "checkpoint_ref": "artifact:abc"}

    def list_checkpoint_updates(self, activity_id):
        if not self.seen:
            return []
        return [{"update_id": "chkup:1", "progress_state": "ADVANCED",
                 "settlement_refs": [SETTLEMENT_ID]}]


def readers(*, settle=True, deliveries=None, integration_state=None,
            activity_status="ACTIVE", lr5_seen=True):
    return {
        "action_read": FakeActionRead({
            "action_id": ACTION_ID, "status": "SUCCEEDED", "revision": 5,
            "action_kind": "FILE_ACTION", "activity_ref": ACTIVITY_ID,
            "target_ref": "artifact:abc", "created_at": "2026-10-27T00:00:00+00:00",
        }),
        "settlement_read": FakeSettlementRead(settle=settle, deliveries=deliveries),
        "integration_read": FakeIntegrationRead(integration_state),
        "activity_read": FakeActivityRead(activity_status),
        "progress_read": FakeProgressRead(seen=lr5_seen),
    }


DELIVERY_OK = {
    "delivery_id": "sdel:1", "event_id": "srev:1", "consumer_domain": "life",
    "attempt": 1, "status": "DELIVERED_TO_CONSUMER", "consumer_disposition": "ACCEPT",
    "downstream_proposal_ref": "life_prog:1", "last_error_ref": None,
}
DELIVERY_FAILED = {
    "delivery_id": "sdel:2", "event_id": "srev:1", "consumer_domain": "memory",
    "attempt": 1, "status": "DELIVERY_FAILED", "consumer_disposition": None,
    "downstream_proposal_ref": None, "last_error_ref": "err:memory:ProductionExecutionRefused",
}
DELIVERY_DEFER = {
    "delivery_id": "sdel:3", "event_id": "srev:1", "consumer_domain": "goal",
    "attempt": 2, "status": "DELIVERED_TO_CONSUMER", "consumer_disposition": "DEFER",
    "downstream_proposal_ref": None, "last_error_ref": None,
}


class DomainRowTests(unittest.TestCase):
    def test_status_mapping_never_upgrades_failure(self):
        rows = review.domain_rows([DELIVERY_OK, DELIVERY_FAILED, DELIVERY_DEFER],
                                  [{"event_id": "srev:1", "settlement_revision": 1}])
        by_domain = {row["domain"]: row for row in rows}
        self.assertEqual(by_domain["life"]["status"], review.DELIVERY_ACKNOWLEDGED)
        self.assertEqual(by_domain["memory"]["status"], review.DELIVERY_FAILED)
        self.assertEqual(by_domain["goal"]["status"], review.DELIVERY_DEFERRED)
        for row in rows:
            self.assertFalse(row["owner_receipt_verified"])
            self.assertEqual(row["receipt_kind"], "AR1_CONSUMER_ACK")

    def test_ignore_is_not_success(self):
        ignored = dict(DELIVERY_OK, consumer_domain="world",
                       consumer_disposition="IGNORE")
        rows = review.domain_rows([ignored], [{"event_id": "srev:1"}])
        self.assertEqual(rows[0]["status"], review.DELIVERY_IGNORED)


class ReviewActionTests(unittest.TestCase):
    def test_three_axes_are_separate_and_envelope_is_not_implemented(self):
        row = review.review_action(action_id=ACTION_ID, readers=readers())
        axes = row["axes"]
        self.assertTrue(axes[review.AXIS_ACTION]["settlement"]["closed"])
        self.assertEqual(axes[review.AXIS_ACTIVITY]["status"], "ACTIVE")
        self.assertFalse(axes[review.AXIS_ACTIVITY]["closed"])
        self.assertEqual(axes[review.AXIS_ENVELOPE]["status"], review.NOT_IMPLEMENTED)
        self.assertFalse(axes[review.AXIS_ENVELOPE]["closure_declarable"])
        self.assertFalse(row["closure"]["envelope_closure_declarable"])

    def test_life_is_verified_only_by_lr5_settlement_ref(self):
        ok = review.review_action(action_id=ACTION_ID,
                                  readers=readers(deliveries=[DELIVERY_OK]))
        life = next(r for r in ok["domains"] if r["domain"] == "life")
        self.assertEqual(life["owner_read"]["status"], "VERIFIED_BY_OWNER_READ")
        unseen = review.review_action(action_id=ACTION_ID,
                                      readers=readers(deliveries=[DELIVERY_OK], lr5_seen=False))
        life2 = next(r for r in unseen["domains"] if r["domain"] == "life")
        self.assertEqual(life2["owner_read"]["status"], "NOT_CONFIRMED_BY_LR5")

    def test_failed_notification_is_not_the_same_as_failed_consequence(self):
        row = review.review_action(action_id=ACTION_ID,
                                   readers=readers(deliveries=[DELIVERY_OK, DELIVERY_FAILED]))
        closure = row["closure"]
        self.assertEqual(closure["action_outcome"], "CLOSED")
        # AR-1's memory notification failed by policy, but it is not Memory's consequence.
        self.assertFalse(closure["ar1_notification_delivery_complete"])
        self.assertIn("memory", closure["ar1_blocked_or_pending_notifications"])
        # No handoff was recorded for a SUCCESS_EFFECT action -> Memory has no record.
        self.assertEqual(closure["consequence_domains"]["memory"]["status"], review.NOT_RECORDED)
        self.assertFalse(closure["consequence_domains_processed"])
        self.assertEqual(closure["required_obligations"], review.NOT_IMPLEMENTED)
        self.assertFalse(closure["envelope_closure_declarable"])

    def test_pending_handoff_blocks_consequence_claim(self):
        state = {
            "handoff_events_by_id": {
                "lexp:result:1": {"handoff_id": "lexp:result:1",
                                  "activity_ref": ACTIVITY_ID,
                                  "action_refs": [ACTION_ID],
                                  "settlement_refs": [SETTLEMENT_ID]}},
            "handoff_deliveries_by_id": {
                "lexp:result:1": {"handoff_id": "lexp:result:1", "attempt": 1,
                                  "status": "FAILED_PENDING_RETRY",
                                  "consumer_disposition": None}},
            "pending_handoff_ids": ["lexp:result:1"],
        }
        row = review.review_action(action_id=ACTION_ID,
                                   readers=readers(deliveries=[DELIVERY_OK],
                                                   integration_state=state))
        memory = row["closure"]["consequence_domains"]["memory"]
        self.assertEqual(memory["status"], review.DOMAIN_PENDING)
        self.assertFalse(row["closure"]["consequence_domains_processed"])
        self.assertIn("lexp:result:1", row["closure"]["pending_handoff_ids"])

    def test_handoff_pending_is_reported_and_memory_unverified_without_binding(self):
        state = {
            "handoff_events_by_id": {
                "lexp:result:1": {"handoff_id": "lexp:result:1",
                                  "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
                                  "activity_ref": ACTIVITY_ID, "activity_revision": 2,
                                  "action_refs": [ACTION_ID],
                                  "settlement_refs": [SETTLEMENT_ID],
                                  "artifact_refs": ["artifact:abc"]}},
            "handoff_deliveries_by_id": {
                "lexp:result:1": {"handoff_id": "lexp:result:1", "attempt": 1,
                                  "status": "FAILED_PENDING_RETRY",
                                  "consumer_disposition": None,
                                  "last_error": "RuntimeError:down"}},
            "pending_handoff_ids": ["lexp:result:1"],
        }
        row = review.review_action(action_id=ACTION_ID,
                                   readers=readers(deliveries=[DELIVERY_OK],
                                                   integration_state=state))
        handoff = row["handoffs"][0]
        self.assertEqual(handoff["match_basis"], "ACTION_REF")
        self.assertEqual(handoff["memory_record"]["status"], review.UNVERIFIED)
        self.assertIn("lexp:result:1", row["closure"]["pending_handoff_ids"])
        self.assertFalse(row["closure"]["consequence_domains_processed"])

    def test_no_settlement_is_not_reported_as_no_action(self):
        row = review.review_action(action_id=ACTION_ID, readers=readers(settle=False))
        settlement = row["axes"][review.AXIS_ACTION]["settlement"]
        self.assertEqual(settlement["status"], review.NOT_RECORDED)
        self.assertIn("不等于动作没有发生", settlement["detail"])
        self.assertFalse(row["closure"]["envelope_closure_declarable"])

    def test_content_is_never_rendered(self):
        row = review.review_action(action_id=ACTION_ID, readers=readers())
        text = review.render_text(row)
        self.assertNotIn("payload", text.lower())
        self.assertIn("axis envelope", text)


class ReadBoundaryTests(unittest.TestCase):
    def test_pending_wal_refuses_instead_of_replaying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "result_settlement.wal").write_text("{}", encoding="utf-8")
            envelope = review.build_settlement_review(root)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["reason"], "COMMIT_WAL_PENDING_RECOVERY")

    def test_missing_state_root_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            envelope = review.build_settlement_review(missing)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["reason"], "STATE_ROOT_NOT_FOUND")

    def test_named_gaps_include_compiler_and_receipts(self):
        gaps = review.named_gaps()
        self.assertIn("obligation_compiler", gaps)
        self.assertIn("target_owner_receipt", gaps)
        self.assertIn("no_change_owner_receipt", gaps)


if __name__ == "__main__":
    unittest.main()
