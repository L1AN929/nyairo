#!/usr/bin/env python3
"""F-154 resumed final gap: the recovered controls copy is really read and bound.

``cmd_recover`` declared ``--recovered-controls`` and never opened it; the sweep wrote an
activation marker while a missing, corrupt or *another owner's* control ledger sat in the copy
being opened.  These cases drive the real operator CLI entry over a brand-new synthetic root:

* every unreadable/wrong-identity/foreign controls copy is refused by name, and the refusal
  happens before the activation marker exists (nothing was swept under an unproven barrier);
* a copy that is verified is named in the marker together with its digest, and a service that
  opens a *different* controls/evidence/supply file under that marker is blocked.

The fixture root is new per run and kept.  No product model, no network, no real instance.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import forget_request_cli as cli  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from forget_request import (  # noqa: E402
    RECOVERY_STATE_FILE,
    RecoveryBlocked,
    RecoveryGate,
)
from life_experience_port import (  # noqa: E402
    build_outcome_event,
    life_conversation_id,
)
from life_memory_service import memory_service_from_env  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)


def _root() -> Path:
    base = (Path(os.environ.get("NYAIRO_F154_GAPS_ROOT")
                 or (ROOT / ".agents" / "worker-runs" / "f154-gaps")) / str(uuid.uuid4()))
    for name in ("instance", "memory", "retained", "forget"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


class RecoveredControlsTest(unittest.TestCase):

    def setUp(self) -> None:
        self.base = _root()
        self.instance = self.base / "instance"
        self.evidence = self.base / "memory" / "evidence.db"
        self.controls = self.base / "memory" / "controls.db"
        self.authority = self.base / "retained" / "authority.db"
        self.cases = self.base / "forget" / "cases.db"
        EvidenceStore(self.evidence)
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        MemoryDeletionAuthority(self.authority, owner=OWNER,
                                conversation=CONVERSATION).initialize()
        # A real M0 event to name in the governed control row.
        self.controlled = self._append_to(self.evidence, "controlled")
        environment = {
            "NYA02_MEMORY_DB": str(self.evidence),
            "NYA02_MEMORY_CONTROLS_DB": str(self.controls),
            "NYA02_MEMORY_AUTHORITY_DB": str(self.authority),
            "NYA02_MEMORY_SUBJECT": OWNER,
            "NYA02_FORGET_CASE_DB": str(self.cases),
        }
        nested = self.instance / "one-day"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "instance.json").write_text(
            json.dumps({"schema_version": "life.one_day.v1", "environment": environment}),
            encoding="utf-8")
        self.recovery = self.base / ("recovered-%d" % time.time_ns())
        self.recovery.mkdir(parents=True, exist_ok=True)
        self.recovered_evidence = self.recovery / "evidence.db"
        self.recovered_controls = self.recovery / "controls.db"
        shutil.copy2(self.evidence, self.recovered_evidence)
        shutil.copy2(self.controls, self.recovered_controls)

    # -- helpers ---------------------------------------------------------------------------

    def _marker(self) -> Path:
        return self.recovery / RECOVERY_STATE_FILE

    def _recover(self, controls) -> tuple[int, dict]:
        argv = ["--instance", str(self.instance), "recover",
                "--recovery-dir", str(self.recovery),
                "--recovered-evidence", str(self.recovered_evidence)]
        if controls is not None:
            argv += ["--recovered-controls", str(controls)]
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return int(code), json.loads(buffer.getvalue() or "{}")

    def _assert_refused(self, controls, *, reasons: set[str], exit_code: int) -> None:
        code, answer = self._recover(controls)
        self.assertEqual(code, exit_code, answer)
        self.assertFalse(answer.get("ok"), answer)
        self.assertIn(answer.get("reason"), reasons, answer)
        self.assertFalse(self._marker().exists(),
                         "no marker may be written when the barrier copy is unproven")

    def _foreign_controls(self, *, owner: str = "intruder") -> Path:
        path = self.recovery / ("controls-%s.db" % owner)
        shutil.copy2(self.controls, path)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "INSERT INTO memory_controls VALUES(?,?,?,?,?,?,?,?)",
                ("operation:%s" % owner, owner, CONVERSATION, "DELETE", "event:%s" % owner,
                 None, "fingerprint", "2026-01-01T00:00:00+00:00"))
            connection.commit()
        finally:
            connection.close()
        return path

    @staticmethod
    def _new_event(label: str):
        return build_outcome_event(
            subject=OWNER, source_ref="probe:%s:%d" % (label, time.time_ns()),
            text="probe event %s" % label,
            occurred_at=datetime.now(timezone.utc).isoformat())

    def _append_to(self, evidence: Path, label: str):
        event = self._new_event(label)
        EvidenceStore(evidence).append(event)
        return event

    def _governed_controls(self) -> Path:
        """A controls copy with one real governance row, created by the ledger's own API."""

        path = self.recovery / "controls-governed.db"
        ledger = MemoryControlLedger(path, self.recovered_evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority="")
        ledger.initialize()
        self.assertEqual(ledger.apply(operation_id="probe:control:governed", kind="DELETE",
                                      target=self.controlled.event_id, actor=OWNER), "APPLIED")
        # Created after the control row: the existing self-history epoch suppresses *older*
        # narration, so an unrelated row must be newer than the control to be genuinely readable.
        self.kept = self._append_to(self.recovered_evidence, "kept")
        return path

    def _service(self, controls: Path):
        environment = {
            "NYA02_MEMORY_DB": str(self.recovered_evidence),
            "NYA02_MEMORY_CONTROLS_DB": str(controls),
            "NYA02_MEMORY_AUTHORITY_DB": str(self.authority),
            "NYA02_MEMORY_SUBJECT": OWNER,
            "NYA02_FORGET_CASE_DB": str(self.cases),
            "NYA02_F154_RECOVERY_DIR": str(self.recovery),
        }
        return memory_service_from_env(environment)

    # -- cases -----------------------------------------------------------------------------

    def test_a_missing_controls_copy_is_refused_before_the_marker(self):
        self._assert_refused(self.recovery / "absent.db",
                             reasons={"RECOVERED_CONTROLS_MISSING"}, exit_code=3)

    def test_a_file_that_is_not_a_control_ledger_is_refused(self):
        broken = self.recovery / "not-a-ledger.db"
        broken.write_bytes(b"this is not sqlite")
        self._assert_refused(broken, reasons={"RECOVERED_CONTROLS_UNREADABLE"}, exit_code=3)

    def test_another_owners_ledger_is_refused(self):
        self._assert_refused(self._foreign_controls(),
                             reasons={"RECOVERED_CONTROLS_OWNER_MISMATCH"}, exit_code=2)

    def test_a_symlinked_controls_copy_is_refused(self):
        link = self.recovery / "linked.db"
        os.symlink(self.recovered_controls, link)
        self._assert_refused(link, reasons={"RECOVERED_CONTROLS_SYMLINK"}, exit_code=2)

    def test_the_live_instances_own_ledger_is_refused(self):
        self._assert_refused(self.controls, reasons={"RECOVERED_CONTROLS_OUTSIDE_COPY",
                                                     "RECOVERED_CONTROLS_LIVE_PATH"},
                             exit_code=2)

    def test_missing_controls_is_refused_and_no_marker_is_written(self):
        """The formal CLI used to activate on evidence alone; that entry is now fail-closed."""

        code, answer = self._recover(None)
        self.assertEqual(code, 2, answer)
        self.assertFalse(answer.get("ok"), answer)
        self.assertEqual(answer.get("reason"), "RECOVERED_CONTROLS_REQUIRED", answer)
        self.assertFalse(self._marker().exists())

    def test_non_empty_controls_are_read_bound_and_still_enforced(self):
        governed = self._governed_controls()
        code, answer = self._recover(governed)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer.get("status"), "RECOVERED_OPEN", answer)
        binding = dict(answer.get("recovered_controls") or {})
        self.assertEqual(binding.get("identity"), "VERIFIED", answer)
        self.assertEqual(int(binding.get("rows") or 0), 1, answer)
        marker = json.loads(self._marker().read_text(encoding="utf-8"))
        self.assertEqual(marker.get("status"), "OPEN", marker)
        self.assertEqual(marker.get("recovered_controls_identity"), "VERIFIED", marker)
        self.assertEqual(Path(marker["recovered_controls"]).resolve(),
                         governed.resolve(), marker)
        self.assertEqual(len(str(marker.get("recovered_controls_digest") or "")), 64, marker)

        service = self._service(governed)
        self.assertEqual(service.status().get("status"), "READY", service.status())
        self.assertIsNone(service.port.read(self.controlled.event_id))
        self.assertIsNotNone(service.port.read(self.kept.event_id))
        fresh = self._new_event("fresh")
        written = service.port.append_outcome(fresh)
        self.assertIn(written.get("status"), ("inserted", "appended", "APPLIED", "OK"), written)
        self.assertIsNotNone(service.port.read(fresh.event_id))

        # A normal governance write changes the bound file's digest, not its identity: it is the
        # same copy, so the gate must not report a false RECOVERY_COPY_MISMATCH.
        ledger = MemoryControlLedger(governed, self.recovered_evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority="")
        self.assertEqual(ledger.apply(operation_id="probe:control:after", kind="DELETE",
                                      target=fresh.event_id, actor=OWNER), "APPLIED")
        reopened = self._service(governed)
        self.assertEqual(reopened.status().get("status"), "READY", reopened.status())
        self.assertIsNone(reopened.port.read(fresh.event_id))

    def test_a_verified_copy_is_named_in_the_marker_and_a_swap_is_blocked(self):
        code, answer = self._recover(self.recovered_controls)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer.get("status"), "RECOVERED_OPEN", answer)
        self.assertEqual(answer.get("recovered_controls", {}).get("identity"), "EMPTY", answer)
        marker = json.loads(self._marker().read_text(encoding="utf-8"))
        self.assertEqual(marker.get("status"), "OPEN", marker)
        self.assertEqual(Path(marker["recovered_controls"]).resolve(),
                         self.recovered_controls.resolve(), marker)
        self.assertTrue(marker.get("recovered_controls_digest"), marker)
        gate = RecoveryGate(self._marker(), owner=OWNER, conversation=CONVERSATION,
                            authority_db=self.authority)
        gate.verify_copy(controls=self.recovered_controls)  # the bound file is accepted
        other = self.recovery / "swapped.db"
        shutil.copy2(self.recovered_controls, other)
        with self.assertRaises(RecoveryBlocked) as caught:
            gate.verify_copy(controls=other)
        self.assertEqual(caught.exception.code, "RECOVERY_COPY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
