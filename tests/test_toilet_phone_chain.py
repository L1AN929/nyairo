#!/usr/bin/env python3
"""TOILET: the declared ``facility_id`` survives the phone facade (real owner chain).

Why this file exists
====================
``PhoneWorld`` has advertised ``TOILET {facility_id}`` (``VERBS`` / ``PARAM_SPEC``)
and the phone HTTP layers already whitelist it, but ``_normalize_params`` had no
``TOILET`` branch: the request reached the owner with ``params == {}`` and was
refused for a missing facility id.  These tests pin the closed hole end to end,
against the *real* stack the owner path really uses:

* a fresh, retained home under ``/tmp`` (``PhoneWorldBase``), with the real
  ``WorldBodyService`` on its real gateway socket, the real Resolver/ledger and
  the real one-shot authorization registry;
* a fixture-declared toilet in ``state/world_object_capabilities.json`` and an
  explicit fixture lived-needs profile (synthetic values, written here -- no past
  experience is read or imported);
* ``PhoneWorld.act("TOILET", {"facility_id": ...})`` as the only write path.

What is asserted
----------------
* the exact facility id reaches the Resolver (ledger mutation ref + the consumed
  ticket's args digest), and the Body really reports the relieved band;
* a missing id is refused inside the facade, before any ticket/ledger/World write;
* an undeclared facility and a facility declared at another place are the World
  owner's own refusals (``NOT_DECLARED`` / ``NOT_HERE``), never a silent success;
* with the local-owner gate unbound the gateway refuses, nothing is written and
  no receipt is invented.

Not asserted here: any human physiology, a natural model decision, G1 or 24h.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
for entry in (str(ROOT), str(TESTS_DIR), str(WORLD_SCRIPTS), str(WORLD_SERVICE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import body_lived_needs as bln  # noqa: E402
import world_action_authorization as waa  # noqa: E402
import world_execution_ledger as ledger  # noqa: E402

from test_phone_world_owner import ENVIRONMENT, PhoneWorldBase  # noqa: E402

FACILITY = "home-toilet-01"
ELSEWHERE_FACILITY = "store-toilet-01"


def declare_facility(
    home: Path,
    facility_id: str,
    *,
    location: str = "home",
    effects: dict | None = None,
) -> None:
    """Add one explicit World declaration; nothing else in the document changes."""

    path = home / "state" / "world_object_capabilities.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.setdefault("lived_need_facilities", {})[facility_id] = {
        "facility_kind": "toilet",
        "location_id": location,
        "lived_need_effects": dict(effects or {"toilet": 0.9}),
        "label": "fixture: declared toilet",
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def enable_lived_needs(home: Path, *, toilet: float = 0.66) -> None:
    """Explicit fixture profile: the service upgrades to body.runtime.v2 on startup."""

    profile = bln.build_profile(
        hunger=0.2,
        thirst=0.2,
        toilet=toilet,
        note="fixture: explicit synthetic needs, not a human measurement",
    )
    bln.save_profile(home / "data" / "body", profile)


class ToiletChainBase(PhoneWorldBase):
    def setUp(self) -> None:
        super().setUp()
        declare_facility(self.home, FACILITY)
        enable_lived_needs(self.home)

    def registry_records(self) -> list[dict]:
        path = self.home / "run" / "action_authorizations.json"
        if not path.exists():
            return []
        return list(
            json.loads(path.read_text(encoding="utf-8")).get("records") or []
        )

    def ledger_frame(self, execution_id: str) -> dict:
        return ledger.ExecutionLedger(ledger.ledger_path(self.home)).frame(
            execution_id
        )

    def toilet_band(self, phone) -> str:
        return str(phone.snapshot()["data"]["body"]["needs"]["toilet"])


class ToiletChainTests(ToiletChainBase):
    def test_a_declared_facility_is_used_through_the_real_owner_chain(self):
        self.start_server()
        phone = self.phone()
        self.assertEqual(self.toilet_band(phone), "mild")

        envelope = phone.act("TOILET", {"facility_id": FACILITY}, "k-toilet-use-1")
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["status"], "applied")
        self.assertEqual(envelope["data"]["action"], "TOILET")
        execution_id = envelope["data"]["execution_id"]

        # The *exact* facility id reached the Resolver: the owner's own ledger
        # names it, and the one-shot ticket was minted for exactly these args.
        frame = self.ledger_frame(execution_id)
        self.assertEqual(frame["state"], "committed")
        raw = (frame["records"][-1] or {}).get("raw") or {}
        self.assertIn(
            "facility:%s=used" % FACILITY, raw.get("world_mutation_refs") or []
        )
        self.assertIn(
            "lived_need:toilet:%s" % FACILITY,
            raw.get("body_consequence_refs") or [],
        )
        tickets = [
            record
            for record in self.registry_records()
            if record.get("execution_id") == execution_id
        ]
        self.assertEqual(len(tickets), 1, tickets)
        self.assertEqual(tickets[0]["verb"], "TOILET")
        self.assertEqual(tickets[0]["state"], "CONSUMED")
        self.assertEqual(
            tickets[0]["args_digest"],
            waa.canonical_args_digest({"facility_id": FACILITY}),
        )

        # The Body owner really applied the declared relief (not just an echo).
        consequences = self.consequences_for(execution_id)
        relief = [
            item
            for record in consequences
            for item in record.get("inputs") or []
            if item.get("class") == "LIVED_NEED_RELIEF"
        ]
        self.assertTrue(relief, consequences)
        self.assertEqual(relief[0]["need"], "toilet")
        self.assertGreater(relief[0]["amount"], 0.0)
        self.assertEqual(self.toilet_band(phone), "none")

    def test_a_missing_facility_id_is_refused_before_any_submission(self):
        self.start_server()
        phone = self.phone()
        before = phone.snapshot()["data"]["body"]["needs"]

        envelope = phone.act("TOILET", {}, "k-toilet-missing")
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(envelope["reason_code"], "INVALID_PARAMS")

        # No ticket, no ledger entry, no relief: the facade refused first.
        self.assertEqual(self.registry_records(), [])
        self.assertEqual(
            ledger.ExecutionLedger(ledger.ledger_path(self.home)).read_all(), []
        )
        self.assertEqual(phone.snapshot()["data"]["body"]["needs"], before)

    def test_an_undeclared_facility_is_the_world_owners_refusal(self):
        self.start_server()
        phone = self.phone()
        before = phone.snapshot()["data"]["body"]["needs"]

        envelope = phone.act(
            "TOILET", {"facility_id": "never-declared"}, "k-toilet-unknown"
        )
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(envelope["reason_code"], "NOT_DECLARED")
        self.assertEqual(phone.snapshot()["data"]["body"]["needs"], before)

    def test_a_facility_declared_at_another_place_is_not_here(self):
        declare_facility(self.home, ELSEWHERE_FACILITY, location="convenience_store")
        self.start_server()
        phone = self.phone()
        before = phone.snapshot()["data"]["body"]["needs"]

        envelope = phone.act(
            "TOILET", {"facility_id": ELSEWHERE_FACILITY}, "k-toilet-elsewhere"
        )
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(envelope["reason_code"], "NOT_HERE")
        self.assertEqual(phone.snapshot()["data"]["body"]["needs"], before)


class NoNeedExposureTests(ToiletChainBase):
    """The owner's own exposure gate: no reported need means no submission."""

    def setUp(self) -> None:
        super().setUp()
        enable_lived_needs(self.home, toilet=0.0)

    def test_a_body_without_a_reported_need_is_refused_by_the_exposure_gate(self):
        self.start_server()
        phone = self.phone()
        self.assertEqual(self.toilet_band(phone), "none")

        envelope = phone.act("TOILET", {"facility_id": FACILITY}, "k-toilet-not-needed")
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(envelope["reason_code"], "TOILET_NOT_NEEDED")
        # Refused before the Resolver: no ledger entry and no Body change.
        self.assertEqual(
            ledger.ExecutionLedger(ledger.ledger_path(self.home)).read_all(), []
        )
        self.assertEqual(self.toilet_band(phone), "none")


class UnboundOwnerGateTests(ToiletChainBase):
    """The local-owner gate is server-side: unbound means refuse, never guess."""

    def test_an_unbound_local_owner_gate_refuses_and_writes_nothing(self):
        os.environ.pop("CHIYO_WORLD_LOCAL_OWNER_PHONE", None)
        self.start_server()
        phone = self.phone()
        before = phone.snapshot()["data"]["body"]["needs"]

        envelope = phone.act("TOILET", {"facility_id": FACILITY}, "k-toilet-gate-off")
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(envelope["reason_code"], "LOCAL_OWNER_PHONE_NOT_BOUND")

        # Nothing committed, no relief, no Body write.
        execution_id = envelope.get("data", {}).get("execution_id")
        if execution_id:
            self.assertNotEqual(self.ledger_frame(execution_id)["state"], "committed")
        self.assertEqual(phone.snapshot()["data"]["body"]["needs"], before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
