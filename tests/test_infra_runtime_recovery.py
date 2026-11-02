"""Read-only recovery review: classification, CLI exit codes, and "writes nothing".

These tests never build a runtime and never touch an owner socket.  They construct the exact
files the owners leave behind (instance.json, authority marker, WAL, AR-0 snapshot) and check
that the reader classifies them and changes nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import runtime_recovery_review as review  # noqa: E402

CLI = ROOT / "scripts" / "infra_runtime_recovery_cli.py"


def _instance(root: Path, *, schema: str = "life.one_day.v1") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "instance.json").write_text(json.dumps({
        "schema_version": schema,
        "subject_id": "test:recovery",
        "environment": {"NYA02_MODEL_BASE_URL": "http://127.0.0.1:1/v1",
                        "NYA02_MODEL_NAME": "stub",
                        "NYA02_MODEL_API_KEY": "SECRET-KEY-VALUE"},
    }), encoding="utf-8")
    (root / "life" / "state").mkdir(parents=True, exist_ok=True)
    return root


def _state(root: Path) -> Path:
    return root / "life" / "state"


def _snapshot(root: Path) -> list[tuple[str, int]]:
    rows = []
    for path in sorted(root.rglob("*")):
        rows.append((str(path.relative_to(root)), path.stat().st_size if path.is_file() else -1))
    return rows


class RecoveryReviewTest(unittest.TestCase):
    def test_fresh_root_is_assemblable_and_reads_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            before = _snapshot(root)
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_ASSEMBLABLE, result)
            self.assertFalse(result["flock_taken"])
            self.assertEqual(_snapshot(root), before)
            self.assertIn("WRITER_NO_AUTHORITY_MARKER", result["reasons"])

    def test_invalid_instance_is_read_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst", schema="wrong.schema")
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_READ_BLOCKED, result)
            self.assertEqual(result["reason"], "INSTANCE_SCHEMA_MISMATCH")

    def test_live_authority_marker_is_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            ( _state(root) / "run").mkdir(parents=True)
            (_state(root) / "run" / "life_authority.json").write_text(json.dumps({
                "schema_version": "x", "instance_id": "life-authority:%d:abc" % os.getpid(),
                "pid": os.getpid(), "store_root": str(root), "acquired_at": "now",
                "marker_is_truth": False}), encoding="utf-8")
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_MANUAL, result)
            self.assertEqual(result["writer"]["reason"], "LEASE_MARKED_HELD_BY_LIVE_PROCESS")

    def test_open_submission_fence_needs_existing_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            (_state(root) / "action_reality_state.json").write_text(json.dumps({
                "revision": 4,
                "actions": {"actn:1": {
                    "action_id": "actn:1", "status": "PREPARED",
                    "idempotency_key": "idem:1", "last_attempt_ref": "aatt:1",
                    "reconciliation_refs": []}},
                "open_submission_intents": {"actn:1": {
                    "action_id": "actn:1", "attempt_id": "aatt:1",
                    "idempotency_key": "idem:1"}},
            }), encoding="utf-8")
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_NEEDS_RECOVERY, result)
            self.assertTrue(result["actions"]["unresolved"])
            # The operation mapping is the existing adapter's, not a second invention.
            self.assertTrue(result["actions"]["unresolved"][0]["idempotency_key"] == "idem:1")

    def test_pending_decision_wal_needs_existing_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            journal = _state(root) / "agency_decision_journal.jsonl"
            journal.write_text('{"revision": 1}\n', encoding="utf-8")
            (_state(root) / "commit_intent.json").write_text(json.dumps({
                "schema_version": "chiyo.agency.decision_commit_intent.v1",
                "store_revision": 2,
                "journal_entry": {"store_revision": 2},
            }), encoding="utf-8")
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_NEEDS_RECOVERY, result)
            row = result["wal"]["rows"][0]
            self.assertEqual((row["store"], row["status"]), ("agency_decision", "PENDING_COMMIT"))
            self.assertEqual(row["journal_entries"], 1)

    def test_unknown_wal_schema_is_unknown_not_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            (_state(root) / "commit_intent.json").write_text(json.dumps({
                "schema_version": "someone.else.v1", "store_revision": 2,
            }), encoding="utf-8")
            result = review.review(root)
            self.assertEqual(result["class"], review.CLASS_UNKNOWN, result)
            self.assertTrue(result["wal"]["unknown"])

    def test_review_never_echoes_the_model_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = review.review(_instance(Path(tmp) / "inst"))
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("SECRET-KEY-VALUE", serialized)
            self.assertTrue(result["config"]["api_key_present"])

    def test_review_reports_backup_and_migration_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = review.review(_instance(Path(tmp) / "inst"))
            self.assertEqual(result["gates"]["backup_restore"]["status"], "NOT_IMPLEMENTED")
            self.assertEqual(result["gates"]["migration"]["status"], "NOT_IMPLEMENTED")


class RecoveryCliTest(unittest.TestCase):
    def _run(self, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(CLI), "--instance", str(root),
                               "--json"], capture_output=True, text=True, timeout=60,
                              cwd=str(ROOT))

    def test_cli_exit_zero_for_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            done = self._run(_instance(Path(tmp) / "inst"))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["class"], review.CLASS_ASSEMBLABLE)

    def test_cli_exit_three_for_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _instance(Path(tmp) / "inst")
            (_state(root) / "run").mkdir(parents=True)
            (_state(root) / "run" / "life_authority.json").write_text(json.dumps({
                "pid": os.getpid(), "instance_id": "life-authority:x"}), encoding="utf-8")
            done = self._run(root)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertEqual(json.loads(done.stdout)["class"], review.CLASS_MANUAL)

    def test_cli_refuses_missing_root_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            done = self._run(missing)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
