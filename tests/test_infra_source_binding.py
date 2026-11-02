#!/usr/bin/env python3
"""INFRA F-167: the AR-1 source identity check used before LR-5 binds a settled event.

These are unit checks of the identity predicate only; the real owner/runtime chain is
exercised by ``scripts/infra_source_binding_demo.py``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_progress_completion_lr5 as lr5  # noqa: E402


class _Read:
    def __init__(self, *, event=None, settlement=None):
        self._event = event
        self._settlement = settlement

    def get_outbox_event(self, event_id):
        return dict(self._event) if self._event else None

    def get_settlement_by_id(self, settlement_id):
        return dict(self._settlement) if self._settlement else None


def _coordinator(read):
    coordinator = object.__new__(lr5.ActivityProgressCoordinator)
    coordinator.settlement_read = read
    return coordinator


EVENT = {"event_id": "srev:aa", "settlement_ref": "rset:aa",
         "action_id": "actn:aa", "activity_ref": "actv_aa"}
RECORD = {"settlement_id": "rset:aa", "action_id": "actn:aa"}


class SourceBindingIdentityTest(unittest.TestCase):
    def test_matching_identity_is_accepted(self):
        c = _coordinator(_Read(event=EVENT, settlement=RECORD))
        self.assertIsNone(c._authoritative_settled_event_rejection(dict(EVENT)))

    def test_another_activity_is_refused(self):
        c = _coordinator(_Read(event=EVENT, settlement=RECORD))
        doctored = dict(EVENT, activity_ref="actv_other", target_ref="artifact:aa")
        self.assertEqual(c._authoritative_settled_event_rejection(doctored),
                         "SETTLEMENT_ACTIVITY_MISMATCH")

    def test_another_action_is_refused(self):
        c = _coordinator(_Read(event=EVENT, settlement=RECORD))
        doctored = dict(EVENT, action_id="actn:other")
        self.assertEqual(c._authoritative_settled_event_rejection(doctored),
                         "SETTLEMENT_ACTION_MISMATCH")

    def test_event_never_emitted_is_refused(self):
        c = _coordinator(_Read(event=None, settlement=RECORD))
        self.assertEqual(c._authoritative_settled_event_rejection(dict(EVENT)),
                         "SETTLEMENT_EVENT_NOT_FOUND_IN_AUTHORITY")

    def test_receipt_must_name_the_same_action(self):
        c = _coordinator(_Read(event=EVENT, settlement={"settlement_id": "rset:aa",
                                                        "action_id": "actn:other"}))
        self.assertEqual(c._authoritative_settled_event_rejection(dict(EVENT)),
                         "SETTLEMENT_ACTION_MISMATCH")

    def test_no_authority_port_keeps_legacy_behaviour(self):
        c = _coordinator(None)
        self.assertIsNone(c._authoritative_settled_event_rejection(dict(EVENT)))


if __name__ == "__main__":
    unittest.main()
