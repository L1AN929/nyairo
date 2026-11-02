"""Consequences of the same explicit recovery: life consumes, policy domains refuse, M0 unchanged.

These are pinpoint checks, not the full recovery matrix.  They run one real isolated chain
(real Supply/World/Activity/Memory owners, ``ProductionLifeRuntime``, AG-0/1/2, AR-0/1,
LR-2/LR-5) with scripted Decision/content, then ask the real read seams what each consequence
domain actually did.  No product model, no network, no old instance.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
               str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
               str(ROOT / "components" / "supply"),
               str(ROOT / "components" / "world" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import result_method_behavior_smoke as harness  # noqa: E402
import v05_restart_continuation_smoke as base  # noqa: E402


class LifeRecoveryConsequencesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = base.prepare_fixture()
        cls.chain = base.open_chain(cls.root, "recovery-consequences")
        seed = harness.seed_method_after_first_write(cls.chain)
        out = seed.round_out
        cls.action_id = str(out.get("action_id") or "")
        receipt = out.get("step_receipt") or {}
        cls.activity_ref = str(receipt.get("activity_ref") or out.get("activity_ref") or "")
        if not cls.action_id or not cls.activity_ref:
            raise AssertionError("the seeded real write did not expose action/activity ids: %r"
                                 % ({key: out.get(key) for key in
                                     ("outcome", "action_id", "activity_ref")},))

    @classmethod
    def tearDownClass(cls) -> None:
        base.close_chain(cls.chain)

    def test_production_registers_four_distinct_settlement_domains(self):
        consumers = self.chain.runtime.settlement_service.consumers
        self.assertEqual(set(consumers), {"life", "memory", "goal", "world"})
        for domain in ("memory", "goal", "world"):
            consumer = consumers[domain]
            self.assertEqual(consumer.consumer_domain, domain)
            with self.assertRaises(Exception) as raised:
                consumer.receive_settled_event(
                    {"event_id": "evt:tamper", "action_id": "actn:tamper",
                     "settlement_ref": "rset:tamper"})
            self.assertIn("LPC0A_%s_WRITE_DISABLED" % domain.upper(),
                          str(raised.exception))

    def test_life_consumed_the_original_action_settlement_and_activity(self):
        settlement_read = self.chain.runtime.settlement_read
        settlement = settlement_read.get_settlement(self.action_id)
        self.assertIsNotNone(settlement)
        settlement_id = str(settlement["settlement_id"])
        events = settlement_read.list_outbox_events(action_id=self.action_id)
        self.assertTrue(events)
        event = events[-1]
        self.assertEqual(str(event.get("settlement_ref")), settlement_id)
        self.assertEqual(str(event.get("activity_ref")), self.activity_ref)

        consumed = self.chain.runtime.progress_read.list_consumed_settlement_refs(
            self.activity_ref)
        self.assertIn(settlement_id, consumed)

        deliveries = {str(row.get("consumer_domain")): row
                      for row in settlement_read.get_delivery_status(event["event_id"])}
        self.assertEqual(deliveries["life"].get("status"), "DELIVERED_TO_CONSUMER")
        self.assertEqual(deliveries["life"].get("consumer_disposition"), "ACCEPT")
        for domain in ("memory", "goal", "world"):
            self.assertEqual(deliveries[domain].get("status"), "DELIVERY_FAILED")
            self.assertIn("ProductionExecutionRefused",
                          str(deliveries[domain].get("last_error_ref")))

    def test_non_authoritative_event_is_ignored_without_progress_writes(self):
        settlement_read = self.chain.runtime.settlement_read
        event = dict(settlement_read.list_outbox_events(action_id=self.action_id)[-1])
        coordinator = self.chain.runtime.progress_coordinator
        self.assertTrue(coordinator.settlement_read is not None,
                        "LR-5 has no settlement read port wired in the production runtime")

        # The identity predicate itself rejects a relabelled Activity even though the event
        # id and settlement still name a real record (the strict F-167 rule is kept as-is).
        relabelled = dict(event)
        relabelled["activity_ref"] = "actv_not_the_original_one"
        self.assertEqual(
            coordinator._authoritative_settled_event_rejection(relabelled),
            "SETTLEMENT_ACTIVITY_MISMATCH")

        # The consumer must not accept an event that does not exist in the AR-1 authority,
        # even when it carries a valid-looking settlement/action pair.
        forged = dict(event)
        forged["event_id"] = str(event.get("event_id") or "") + ":forged"
        progress_read = self.chain.runtime.progress_read

        before_checkpoints = len(progress_read.list_checkpoint_updates(self.activity_ref))
        before_consumed = list(progress_read.list_consumed_settlement_refs(self.activity_ref))
        before_activity = dict(self.chain.runtime.activity_read.get_activity(
            self.activity_ref) or {})

        result = coordinator.receive_settled_event(forged)
        self.assertEqual(result.disposition, "IGNORE")

        self.assertEqual(len(progress_read.list_checkpoint_updates(self.activity_ref)),
                         before_checkpoints)
        self.assertEqual(progress_read.list_consumed_settlement_refs(self.activity_ref),
                         before_consumed)
        after_activity = dict(self.chain.runtime.activity_read.get_activity(
            self.activity_ref) or {})
        self.assertEqual(after_activity.get("revision"), before_activity.get("revision"))
        self.assertEqual(after_activity.get("status"), before_activity.get("status"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
