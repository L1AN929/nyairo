"""F-154 scenario 9: an activation binds incarnations, not only paths."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "components" / "life" / "scripts"))
sys.path.insert(0, str(ROOT / "components" / "native" / "src"))
sys.path.insert(0, str(ROOT / "components" / "native"))

from forget_request import (  # noqa: E402
    RECOVERY_STATE_FILE, RecoveryBlocked, RecoveryGate, read_incarnation,
)
from app.evidence import EvidenceStore  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = "life:chiyo"


class _Projection:
    seq = 3
    head_digest = "digest-3"
    healthy = True


class RecoveryIncarnationTest(unittest.TestCase):
    _counter = itertools.count()

    def setUp(self) -> None:
        # The synthetic root is kept on disk (no TemporaryDirectory, no rmtree).
        base = ROOT / ".agents" / "worker-runs" / (
            "f154-same-path-test-%d-%d" % (os.getpid(), next(self._counter)))
        self.root = base / "recovery"
        self.root.mkdir(parents=True)
        self.evidence = self.root / "evidence.db"
        self.controls = self.root / "controls.db"
        EvidenceStore(self.evidence)
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        self.marker = self.root / RECOVERY_STATE_FILE
        self.gate = RecoveryGate(self.marker, owner=OWNER, conversation=CONVERSATION)

    def _stamp(self, evidence_generation: str = "gen-evidence",
               controls_generation: str = "gen-controls") -> dict:
        EvidenceStore(self.evidence).stamp_recovery_incarnation(
            owner=OWNER, conversation=CONVERSATION, authority_seq=3,
            authority_digest="digest-3", generation=evidence_generation)
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).stamp_recovery_incarnation(
            authority_seq=3, authority_digest="digest-3", generation=controls_generation)
        return {"evidence": evidence_generation, "controls": controls_generation}

    def _activate(self, incarnations: dict | None) -> None:
        self.gate.activate(
            owner=OWNER, conversation=CONVERSATION, projection=_Projection(),
            recovered_evidence=self.evidence,
            recovered_controls={"path": str(self.controls), "digest": "d", "identity": "EMPTY"},
            incarnations=incarnations)

    def test_activation_without_incarnations_is_refused(self) -> None:
        with self.assertRaises(RecoveryBlocked) as caught:
            self._activate(None)
        self.assertEqual(caught.exception.code, "RECOVERY_INCARNATION_REQUIRED")
        self.assertFalse(self.marker.exists())

    def test_same_path_copy_of_an_older_database_is_refused(self) -> None:
        self._activate(self._stamp())
        self.gate.verify_copy(evidence=self.evidence, controls=self.controls)
        older = self.root / "evidence.older.db"
        connection = sqlite3.connect(older)
        try:
            connection.execute("CREATE TABLE evidence_events(event_id TEXT)")
            connection.commit()
        finally:
            connection.close()
        shutil.copy2(older, self.evidence)    # the *same path*, a database with no stamp
        with self.assertRaises(RecoveryBlocked) as caught:
            self.gate.verify_copy(evidence=self.evidence)
        self.assertEqual(caught.exception.code, "RECOVERY_INCARNATION_MISSING")

    def test_a_different_generation_at_the_same_path_is_refused(self) -> None:
        self._activate(self._stamp())
        self._stamp(evidence_generation="gen-something-else")
        with self.assertRaises(RecoveryBlocked) as caught:
            self.gate.verify_copy(evidence=self.evidence)
        self.assertEqual(caught.exception.code, "RECOVERY_INCARNATION_MISMATCH")
        self.assertEqual(
            read_incarnation(self.evidence, "evidence", owner=OWNER,
                             conversation=CONVERSATION)["generation"],
            "gen-something-else")

    def test_an_old_path_only_marker_fails_closed(self) -> None:
        self._activate(self._stamp())
        state = json.loads(self.marker.read_text(encoding="utf-8"))
        state.pop("incarnations")
        self.marker.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(RecoveryBlocked) as caught:
            self.gate.verify_copy(evidence=self.evidence)
        self.assertEqual(caught.exception.code, "RECOVERY_INCARNATION_UNBOUND")


if __name__ == "__main__":
    unittest.main()
