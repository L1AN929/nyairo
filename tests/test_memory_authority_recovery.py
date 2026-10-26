"""Retained deletion authority: a deletion must survive an older evidence/control pair."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "components" / "life" / "scripts",
              ROOT / "components" / "native",
              ROOT / "components" / "native" / "src",
              ROOT / "components" / "native" / "memory_runtime_v1"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.evidence import EvidenceStore  # noqa: E402
from life_experience_port import (  # noqa: E402
    LifeExperiencePort,
    MemoryReadUnavailable,
    build_outcome_event,
    life_conversation_id,
)
from memory_authority import MemoryAuthorityUnavailable, MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)


class AuthorityRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="nyairo-authority-test-"))
        self.evidence = self.base / "evidence.db"
        self.controls = self.base / "controls.db"
        self.authority = self.base / "authority.db"
        self.store = EvidenceStore(self.evidence)
        removed = build_outcome_event(subject=OWNER, source_ref="run:delete",
                                      text="将被删除的经历",
                                      occurred_at=datetime.now(timezone.utc).isoformat())
        self.store.append(removed)
        self.removed_id = removed.event_id
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=CONVERSATION).initialize()
        MemoryDeletionAuthority(self.authority, owner=OWNER,
                                conversation=CONVERSATION, minimum_seq=0).initialize()
        shutil.copy2(self.evidence, self.base / "evidence.before.db")
        shutil.copy2(self.controls, self.base / "controls.before.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _ledger(self, authority=None):
        return MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                   conversation=CONVERSATION,
                                   authority=str(self.authority) if authority is None else authority,
                                   authority_min_seq=0)

    def _port(self):
        return LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db=self.authority, authority_min_seq=0)

    def _delete(self, target=None, operation_id="delete-1"):
        return self._ledger().apply(operation_id=operation_id, kind="DELETE",
                                    target=target or self.removed_id, actor=OWNER)

    def _append_ordinary(self) -> str:
        # A deletion epoch hides *older* self-history under the original rule, so the ordinary
        # record that must stay readable is genuinely written after the deletion.
        event = build_outcome_event(subject=OWNER, source_ref="run:keep", text="普通经历",
                                    occurred_at=datetime.now(timezone.utc).isoformat())
        self.store.append(event)
        return event.event_id

    def _texts(self):
        return [record.text for record in self._port().search(query="", limit=20)]

    def test_only_evidence_rolled_back_still_withholds_the_deletion(self) -> None:
        self.assertEqual("APPLIED", self._delete())
        shutil.copy2(self.base / "evidence.before.db", self.evidence)
        kept = self._append_ordinary()
        self.assertEqual(["普通经历"], self._texts())
        self.assertIsNotNone(self._port().read(kept))

    def test_evidence_and_controls_rolled_back_together_still_withhold(self) -> None:
        self.assertEqual("APPLIED", self._delete())
        shutil.copy2(self.base / "evidence.before.db", self.evidence)
        shutil.copy2(self.base / "controls.before.db", self.controls)
        kept = self._append_ordinary()
        self.assertEqual(["普通经历"], self._texts())
        self.assertIsNotNone(self._port().read(kept))

    def test_missing_authority_blocks_reads_and_refuses_delete(self) -> None:
        self._delete()
        kept = self._append_ordinary()
        shutil.move(str(self.authority), str(self.base / "moved.db"))
        with self.assertRaises(MemoryReadUnavailable) as caught:
            self._port().search(query="")
        self.assertTrue(caught.exception.code.startswith("MEMORY_AUTHORITY_"))
        with self.assertRaises(MemoryAuthorityUnavailable):
            self._delete(kept, operation_id="delete-2")
        rows = sqlite3.connect(self.controls).execute(
            "SELECT count(*) FROM memory_controls").fetchone()[0]
        self.assertEqual(1, rows)

    def test_an_older_chain_valid_copy_is_refused(self) -> None:
        shutil.copy2(self.authority, self.base / "authority.cold.db")
        self._delete()
        cold = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db=self.base / "authority.cold.db",
                                  authority_min_seq=1)
        with self.assertRaises(MemoryReadUnavailable) as caught:
            cold.search(query="")
        self.assertEqual("MEMORY_AUTHORITY_OLDER_THAN_TRUSTED", caught.exception.code)

    def test_configuration_comes_only_from_the_two_explicit_names(self) -> None:
        # No sibling convention: a file beside the ledger is never adopted, and the configured
        # path plus the handed-off floor are what the port and the ledger resolve.
        with mock.patch.dict(os.environ, {
                "NYA02_MEMORY_AUTHORITY_DB": str(self.authority),
                "NYA02_MEMORY_AUTHORITY_MIN_SEQ": "0"}, clear=False):
            port = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER)
            ledger = MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                         conversation=CONVERSATION)
        self.assertEqual(str(self.authority), str(port.authority_db))
        self.assertEqual("APPLIED", ledger.apply(operation_id="resident-delete", kind="DELETE",
                                                 target=self.removed_id, actor=OWNER))
        shutil.copy2(self.base / "evidence.before.db", self.evidence)
        shutil.copy2(self.base / "controls.before.db", self.controls)
        self._append_ordinary()
        self.assertEqual(["普通经历"],
                         [record.text for record in port.search(query="", limit=20)])

    def test_a_file_beside_the_ledger_is_not_adopted(self) -> None:
        port = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_min_seq=0)
        ledger = MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority_min_seq=0)
        self.assertEqual("APPLIED", ledger.apply(operation_id="legacy-delete", kind="DELETE",
                                                 target=self.removed_id, actor=OWNER))
        self.assertIsNone(port.authority_db)
        self.assertIsNone(ledger.authority)

    def test_an_unconfigured_instance_keeps_the_legacy_path(self) -> None:
        # No authority file at all: the old behaviour stands and nothing is adopted.
        self.authority.unlink()
        legacy = MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority="")
        self.assertEqual("APPLIED", legacy.apply(operation_id="legacy-delete", kind="DELETE",
                                                 target=self.removed_id, actor=OWNER))
        self._append_ordinary()
        port = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db="")
        self.assertEqual(["普通经历"],
                         [record.text for record in port.search(query="", limit=20)])

    def test_normal_delete_advances_the_retained_floor_without_a_manual_number(self) -> None:
        # No operator floor, no hand-set 1: a brand-new empty base gets floor 0 at initialization
        # and the normal DELETE flow advances the sibling handoff file itself.
        floor = Path(str(self.authority) + ".floor.json")
        cold = self.base / "authority.predelete.db"
        shutil.copy2(self.authority, cold)
        port = LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                                  authority_db=self.authority)
        ledger = MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                                     conversation=CONVERSATION, authority=self.authority)
        self.assertEqual(["将被删除的经历"], [record.text for record in port.search(query="")])
        self.assertEqual("APPLIED", ledger.apply(operation_id="auto-floor-delete", kind="DELETE",
                                                 target=self.removed_id, actor=OWNER))
        self.assertEqual(1, json.loads(floor.read_text(encoding="utf-8"))["seq"])
        # The long-lived port re-reads the floor on every projection, so the deletion still holds.
        self.assertEqual([], [record.text for record in port.search(query="")])
        # An old (chain-valid) database put back into the retained path is below that floor,
        # with no number passed by the caller.
        current = self.base / "authority.current.db"
        shutil.copy2(self.authority, current)
        shutil.copy2(cold, self.authority)
        with self.assertRaises(MemoryReadUnavailable) as caught:
            LifeExperiencePort(self.evidence, controls_db=self.controls, subject=OWNER,
                               authority_db=self.authority).search(query="")
        self.assertEqual("MEMORY_AUTHORITY_OLDER_THAN_TRUSTED", caught.exception.code)
        shutil.copy2(current, self.authority)
        state = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                        conversation=CONVERSATION).projection()
        self.assertTrue(state.healthy, state.reason)
        self.assertEqual(1, state.seq)
        self.assertEqual(1, state.trusted_floor)

    def test_a_malformed_handoff_blocks_instead_of_being_ignored(self) -> None:
        floor = Path(str(self.authority) + ".floor.json")
        floor.write_text("{not json", encoding="utf-8")
        state = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                        conversation=CONVERSATION).projection()
        self.assertFalse(state.healthy)
        self.assertEqual("MEMORY_AUTHORITY_HANDOFF_INVALID", state.reason)

    def test_an_existing_empty_authority_without_a_floor_is_not_blessed(self) -> None:
        orphan = self.base / "orphan"
        orphan.mkdir()
        existing = orphan / "authority.db"
        sqlite3.connect(existing).close()          # an existing (empty) file, no handoff
        MemoryDeletionAuthority(existing, owner=OWNER, conversation=CONVERSATION).initialize()
        self.assertFalse(Path(str(existing) + ".floor.json").exists())
        state = MemoryDeletionAuthority(existing, owner=OWNER,
                                        conversation=CONVERSATION).projection()
        self.assertFalse(state.healthy)
        self.assertEqual("MEMORY_AUTHORITY_CURRENTNESS_UNPROVEN", state.reason)

    def test_a_tampered_chain_is_unhealthy(self) -> None:
        self._delete()
        connection = sqlite3.connect(self.authority)
        try:
            connection.execute("DROP TRIGGER authority_no_update")
            connection.execute("UPDATE authority_records SET digest='0'*64 WHERE seq=1")
            connection.commit()
        finally:
            connection.close()
        state = MemoryDeletionAuthority(self.authority, owner=OWNER,
                                        conversation=CONVERSATION,
                                        minimum_seq=0).projection()
        self.assertFalse(state.healthy)
        self.assertEqual("MEMORY_AUTHORITY_CHAIN_INVALID", state.reason)


if __name__ == "__main__":
    unittest.main()
