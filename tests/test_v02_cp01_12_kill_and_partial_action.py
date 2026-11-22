#!/usr/bin/env python3
"""Comprehensive OS crash-kill, WAL recovery, DAG partial, and contract tests for v02_crash_and_partial_reconciler."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "components" / "life" / "scripts"
RECONCILER_SCRIPT = SCRIPTS / "v02_crash_and_partial_reconciler.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from v02_crash_and_partial_reconciler import (  # noqa: E402
    CRASH_POINTS,
    REQUIRED_SQLITE_TABLES,
    CrashAndPartialReconcilerStore,
    ReconcilerContractError,
    compute_idempotency_key,
)


class TestV02CP01To12AndPartialReconciler(unittest.TestCase):
    """Real subprocess kill + WAL recovery + Compound DAG + Bounded Reconcile tests."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_dir.name)
        self.store = CrashAndPartialReconcilerStore(self.tmp)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _wait_for_sentinel(self, sentinel: Path, proc: subprocess.Popen[str], timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sentinel.is_file() and sentinel.stat().st_size > 0:
                return
            if proc.poll() is not None:
                stdout, stderr = proc.communicate(timeout=2)
                self.fail(
                    f"Worker exited early (rc={proc.returncode}) before sentinel {sentinel}:\n"
                    f"stdout={stdout}\nstderr={stderr}"
                )
            time.sleep(0.01)
        proc.kill()
        proc.wait(timeout=5)
        self.fail(f"Timed out waiting for crash hook sentinel {sentinel}")

    def _spawn_and_kill_at_cp(
        self,
        cp: str,
        *,
        extra_args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        sentinel_name: str | None = None,
    ) -> int:
        sentinel = self.tmp / (sentinel_name or f"sentinel_{cp}.json")
        cmd = [
            sys.executable,
            str(RECONCILER_SCRIPT),
            "--worker-cp",
            cp,
            "--root",
            str(self.tmp),
            "--sentinel",
            str(sentinel),
        ]
        if extra_args:
            cmd.extend(extra_args)
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._wait_for_sentinel(sentinel, proc)
        proc.kill()
        rc = proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        self.assertNotEqual(rc, 0, f"{cp} worker must be killed mid-flight")
        return rc

    def _assert_sqlite_pragmas_and_integrity(self, store: CrashAndPartialReconcilerStore) -> None:
        pragmas = store.pragma_snapshot()
        self.assertEqual(pragmas["journal_mode"], "wal")
        self.assertEqual(pragmas["synchronous"], 2)  # FULL
        self.assertEqual(pragmas["foreign_keys"], 1)
        ok, reason = store.verify_store_integrity()
        self.assertTrue(ok, f"Store integrity failed: {reason}")
        self.assertEqual(reason, "OK")
        self.assertEqual(store.llm_calls, 0)

    def test_00_hygiene_lf_neutrality_and_zero_llm_calls(self) -> None:
        for path in (RECONCILER_SCRIPT, Path(__file__).resolve()):
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"Pure LF required in {path}")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u5343\u4ee3", text)
            self.assertNotIn("\u5979", text)
        self.store.initialize()
        self._assert_sqlite_pragmas_and_integrity(self.store)

    def test_01_cp01_kill_after_t0_and_idempotent_recovery(self) -> None:
        self._spawn_and_kill_at_cp("CP01")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["decisions"]), 1)
        self.assertEqual(len(snap_before["adoptions"]), 0)
        self.assertEqual(len(snap_before["outbox"]), 1)

        rec1 = restarted.recover_after_crash(crash_point="CP01")
        self.assertEqual(rec1["status"], "ADOPTED")
        rec2 = restarted.recover_after_crash(crash_point="CP01")
        self.assertEqual(rec2["status"], "ALREADY_ADOPTED")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["adoptions"]), 1)
        self.assertEqual(len(snap_after["activities"]), 1)
        self.assertEqual(len(snap_after["actions"]), 1)
        self.assertEqual(len(snap_after["outbox"]), 2)
        self._assert_sqlite_pragmas_and_integrity(restarted)

    def test_02_cp02_kill_before_t1_and_idempotent_recovery(self) -> None:
        self._spawn_and_kill_at_cp("CP02")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["decisions"]), 1)
        self.assertEqual(len(snap_before["adoptions"]), 0)
        self.assertEqual(len(snap_before["activities"]), 0)

        rec1 = restarted.recover_after_crash(crash_point="CP02")
        self.assertEqual(rec1["status"], "ADOPTED")
        rec2 = restarted.recover_after_crash(crash_point="CP02")
        self.assertEqual(rec2["status"], "ALREADY_ADOPTED")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["adoptions"]), 1)
        self.assertEqual(len(snap_after["activities"]), 1)
        self.assertEqual(len(snap_after["actions"]), 1)

    def test_03_cp03_kill_after_t1_no_duplicate_activity_or_dispatch(self) -> None:
        self._spawn_and_kill_at_cp("CP03")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["activities"]), 1)
        self.assertEqual(len(snap_before["action_attempts"]), 0)

        rec1 = restarted.recover_after_crash(crash_point="CP03")
        self.assertEqual(rec1["t1"]["status"], "ALREADY_ADOPTED")
        self.assertEqual(rec1["td"]["attempt_seq"], 1)

        rec2 = restarted.recover_after_crash(crash_point="CP03")
        self.assertEqual(rec2["t1"]["status"], "ALREADY_ADOPTED")
        self.assertTrue(rec2["td"].get("idempotent_replay"))

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["adoptions"]), 1)
        self.assertEqual(len(snap_after["activities"]), 1)
        self.assertEqual(len(snap_after["actions"]), 1)
        self.assertEqual(len(snap_after["action_attempts"]), 1)
        self.assertEqual(snap_after["action_attempts"][0]["state"], "PREPARED")

    def test_04_cp04_kill_mid_t1_rolls_back_cleanly_zero_torn_records(self) -> None:
        self._spawn_and_kill_at_cp("CP04")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        # BEGIN IMMEDIATE must have rolled back partial adoptions/activities
        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["adoptions"]), 0)
        self.assertEqual(len(snap_before["activities"]), 0)
        self.assertEqual(len(snap_before["actions"]), 0)

        rec1 = restarted.recover_after_crash(crash_point="CP04")
        self.assertEqual(rec1["t1"]["status"], "ADOPTED")
        self.assertEqual(rec1["td"]["attempt_seq"], 1)

        rec2 = restarted.recover_after_crash(crash_point="CP04")
        self.assertEqual(rec2["t1"]["status"], "ALREADY_ADOPTED")
        self.assertTrue(rec2["td"].get("idempotent_replay"))

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["adoptions"]), 1)
        self.assertEqual(len(snap_after["activities"]), 1)
        self.assertEqual(len(snap_after["actions"]), 1)
        self.assertEqual(len(snap_after["action_attempts"]), 1)

    def test_05_cp05_kill_before_outbox_ack_inbox_deduplicates(self) -> None:
        self._spawn_and_kill_at_cp("CP05")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        rec1 = restarted.recover_after_crash(crash_point="CP05")
        self.assertGreaterEqual(len(rec1["recovered_outbox"]), 1)
        for item in rec1["recovered_outbox"]:
            self.assertFalse(item["deduplicated"])
            # Redelivering the exact same event_id must deduplicate via inbox
            dup = restarted.consume_outbox_to_inbox(
                consumer=item["consumer"],
                event_id=item["event_id"],
                result_ref=item["result_ref"],
            )
            self.assertTrue(dup["deduplicated"])

        rec2 = restarted.recover_after_crash(crash_point="CP05")
        self.assertEqual(rec2["recovered_outbox"], [])
        snap = restarted.snapshot_db()
        self.assertTrue(all(o["state"] == "ACKED" for o in snap["outbox"]))

    def test_06_cp06_kill_after_provider_submit_drop_ack_zero_duplicate_dispatch(self) -> None:
        self._spawn_and_kill_at_cp("CP06", extra_args=["--drop-ack"])
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        self.assertEqual(restarted.provider.total_invocations(), 1)
        rec1 = restarted.recover_after_crash(crash_point="CP06")
        self.assertEqual(rec1["outcome"], "SUCCEEDED")
        self.assertEqual(rec1["phase"], "CLOSED")
        self.assertEqual(restarted.provider.total_invocations(), 1)

        rec2 = restarted.recover_after_crash(crash_point="CP06")
        self.assertEqual(rec2["outcome"], "SUCCEEDED")
        self.assertEqual(restarted.provider.total_invocations(), 1)

        snap = restarted.snapshot_db()
        self.assertEqual(len(snap["action_receipts"]), 1)
        self.assertEqual(snap["action_receipts"][0]["receipt_kind"], "PROVIDER_RECEIPT")

    def test_07_cp07_kill_after_t2_operation_mapping_before_t3(self) -> None:
        self._spawn_and_kill_at_cp("CP07")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["operation_mappings"]), 1)
        self.assertEqual(snap_before["actions"][0]["outcome"], "PENDING")

        rec1 = restarted.recover_after_crash(crash_point="CP07")
        self.assertEqual(rec1["outcome"], "SUCCEEDED")
        self.assertEqual(rec1["phase"], "CLOSED")
        rec2 = restarted.recover_after_crash(crash_point="CP07")
        self.assertEqual(rec2["outcome"], "SUCCEEDED")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["action_receipts"]), 1)
        self.assertEqual(snap_after["action_receipts"][0]["receipt_kind"], "OWNER_OPERATION_RECEIPT")

    def test_08_cp08_kill_after_t3_before_ts_settlement(self) -> None:
        self._spawn_and_kill_at_cp("CP08")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(snap_before["actions"][0]["outcome"], "SUCCEEDED")
        self.assertEqual(len(snap_before["consequence_envelopes"]), 0)

        rec1 = restarted.recover_after_crash(crash_point="CP08")
        self.assertEqual(rec1["status"], "CREATED")
        self.assertEqual(len(rec1["obligations"]), 3)

        rec2 = restarted.recover_after_crash(crash_point="CP08")
        self.assertEqual(rec2["status"], "EXISTING_ENVELOPE")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["consequence_envelopes"]), 1)
        self.assertEqual(len(snap_after["consequence_obligations"]), 3)

    def test_09_cp09_kill_mid_t2_rolls_back_before_commit_then_recovers(self) -> None:
        self._spawn_and_kill_at_cp("CP09")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        # BEGIN IMMEDIATE must have rolled back T2 writes cleanly
        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["artifacts"]), 0)
        self.assertEqual(len(snap_before["artifact_versions"]), 0)
        self.assertEqual(len(snap_before["operation_mappings"]), 0)

        proj_path = self.tmp / "art_1.md"
        rec1 = restarted.recover_after_crash(crash_point="CP09", projection_path=proj_path)
        self.assertEqual(rec1["status"], "COMMITTED")
        rec2 = restarted.recover_after_crash(crash_point="CP09", projection_path=proj_path)
        self.assertEqual(rec2["status"], "IDEMPOTENT_REPLAY")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["artifacts"]), 1)
        self.assertEqual(len(snap_after["artifact_versions"]), 1)
        self.assertEqual(len(snap_after["operation_mappings"]), 1)

    def test_10_cp10_kill_after_t2_before_md_projection_no_duplicate_version(self) -> None:
        proj_path = self.tmp / "art_1.md"
        self._spawn_and_kill_at_cp("CP10", extra_args=["--projection-path", str(proj_path)])
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        self.assertFalse(proj_path.exists())
        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["artifact_versions"]), 1)
        self.assertEqual(snap_before["artifact_projections"][0]["health"], "MISSING")

        rec1 = restarted.recover_after_crash(crash_point="CP10", projection_path=proj_path)
        self.assertEqual(rec1["status"], "MATERIALIZED")
        self.assertEqual(rec1["health"], "CURRENT")
        self.assertTrue(proj_path.is_file())

        rec2 = restarted.recover_after_crash(crash_point="CP10", projection_path=proj_path)
        self.assertEqual(rec2["status"], "MATERIALIZED")

        snap_after = restarted.snapshot_db()
        self.assertEqual(len(snap_after["artifact_versions"]), 1)
        self.assertEqual(snap_after["artifact_projections"][0]["health"], "CURRENT")

    def test_11_cp11_kill_during_ts_t4_fanout_completes_only_pending_and_closes_tc(self) -> None:
        self._spawn_and_kill_at_cp("CP11")
        restarted = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted)

        snap_before = restarted.snapshot_db()
        self.assertEqual(len(snap_before["owner_receipts"]), 2)
        pending = [o for o in snap_before["consequence_obligations"] if o["status"] == "PENDING"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["target_owner"], "S05")

        rec1 = restarted.recover_after_crash(crash_point="CP11")
        self.assertEqual(rec1["status"], "CLOSED")
        rec2 = restarted.recover_after_crash(crash_point="CP11")
        self.assertEqual(rec2["status"], "CLOSED")
        self.assertTrue(rec2.get("already_closed"))

        snap_after = restarted.snapshot_db()
        self.assertEqual(snap_after["consequence_envelopes"][0]["phase"], "CLOSED")
        self.assertEqual(len(snap_after["owner_receipts"]), 3)
        self.assertEqual(len(snap_after["memory_records"]), 1)

    def test_12_cp12_kill_before_and_after_s05_commit_and_os_exit_137(self) -> None:
        # 12a: CP12 before S05 COMMIT -> rollback, then recovery commits once & closes TC
        self._spawn_and_kill_at_cp(
            "CP12",
            extra_env={"NYAIRO_CP12_PHASE": "before_commit"},
            sentinel_name="sentinel_cp12_before.json",
        )
        restarted_a = CrashAndPartialReconcilerStore(self.tmp)
        self._assert_sqlite_pragmas_and_integrity(restarted_a)
        snap_a_before = restarted_a.snapshot_db()
        self.assertEqual(len(snap_a_before["memory_records"]), 0)
        self.assertEqual(len(snap_a_before["owner_receipts"]), 2)

        rec_a1 = restarted_a.recover_after_crash(crash_point="CP12")
        self.assertEqual(rec_a1["status"], "CLOSED")
        rec_a2 = restarted_a.recover_after_crash(crash_point="CP12")
        self.assertEqual(rec_a2["status"], "CLOSED")
        snap_a_after = restarted_a.snapshot_db()
        self.assertEqual(len(snap_a_after["memory_records"]), 1)
        self.assertEqual(len(snap_a_after["owner_receipts"]), 3)
        self.assertEqual(snap_a_after["consequence_envelopes"][0]["phase"], "CLOSED")

        # 12b: CP12 after S05 COMMIT in fresh temp root -> zero duplicate memory/receipt
        with tempfile.TemporaryDirectory() as d2:
            root2 = Path(d2)
            sentinel2 = root2 / "sentinel_cp12_after.json"
            env2 = os.environ.copy()
            env2["NYAIRO_CP12_PHASE"] = "after_commit"
            proc2 = subprocess.Popen(
                [
                    sys.executable,
                    str(RECONCILER_SCRIPT),
                    "--worker-cp",
                    "CP12",
                    "--root",
                    str(root2),
                    "--sentinel",
                    str(sentinel2),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env2,
            )
            self._wait_for_sentinel(sentinel2, proc2)
            proc2.kill()
            proc2.wait(timeout=5)
            if proc2.stdout is not None:
                proc2.stdout.close()
            if proc2.stderr is not None:
                proc2.stderr.close()

            restarted_b = CrashAndPartialReconcilerStore(root2)
            self._assert_sqlite_pragmas_and_integrity(restarted_b)
            self.assertEqual(len(restarted_b.snapshot_db()["memory_records"]), 1)
            rec_b = restarted_b.recover_after_crash(crash_point="CP12")
            self.assertEqual(rec_b["status"], "CLOSED")
            snap_b = restarted_b.snapshot_db()
            self.assertEqual(len(snap_b["memory_records"]), 1)
            self.assertEqual(len(snap_b["owner_receipts"]), 3)

        # 12c: Explicit os._exit(137) crash hook inside child process during CP09 -> clean WAL rollback
        with tempfile.TemporaryDirectory() as d3:
            root3 = Path(d3)
            driver = (
                "import os, sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
                "import v02_crash_and_partial_reconciler as m\n"
                "class Exit137Hook(m.CrashHookController):\n"
                "    def hit(self, cp_name, metadata=None):\n"
                "        self.triggered_hooks.append(cp_name)\n"
                "        if cp_name == self.target_cp:\n"
                "            os._exit(137)\n"
                "store = m.CrashAndPartialReconcilerStore(Path(sys.argv[1]), crash_hook=Exit137Hook(target_cp='CP09'))\n"
                "store.initialize()\n"
                "store.tx_t0_commit_decision(decision_id='dec_1', request_id='req_1')\n"
                "t1 = store.tx_t1_commit_adoption_activity_action(decision_id='dec_1', request_id='req_1', logical_action_id='la_1')\n"
                "td = store.tx_td_prepare_dispatch(action_id=t1['action_id'])\n"
                "store.tx_ta_start_attempt(attempt_id=td['attempt_id'])\n"
                "store.tx_t2_commit_artifact_version(artifact_id='art_1', action_id=t1['action_id'], content_body='x', trigger_cp09=True)\n"
            )
            proc3 = subprocess.run(
                [sys.executable, "-c", driver, str(root3)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc3.returncode, 137)
            restarted_3 = CrashAndPartialReconcilerStore(root3)
            self._assert_sqlite_pragmas_and_integrity(restarted_3)
            self.assertEqual(len(restarted_3.snapshot_db()["artifact_versions"]), 0)
            rec3 = restarted_3.recover_after_crash(crash_point="CP09")
            self.assertEqual(rec3["status"], "COMMITTED")
            self.assertEqual(len(restarted_3.snapshot_db()["artifact_versions"]), 1)

    def test_13_full_t0_to_tc_transitions_and_fencing(self) -> None:
        self.store.initialize()
        self.store.register_memory_source(source_id="src_1", generation=1)

        t0 = self.store.tx_t0_commit_decision(decision_id="dec_full", request_id="req_full")
        t1 = self.store.tx_t1_commit_adoption_activity_action(
            decision_id=t0["decision_id"],
            request_id=t0["request_id"],
            logical_action_id="la_full",
        )
        td = self.store.tx_td_prepare_dispatch(action_id=t1["action_id"], run_id="run_full")
        ta = self.store.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        self.assertEqual(ta["state"], "STARTED")

        proj = self.tmp / "full.md"
        t2 = self.store.tx_t2_commit_artifact_version(
            artifact_id="art_full",
            action_id=t1["action_id"],
            content_body="# Full Lifecycle\n",
            projection_path=proj,
        )
        mat = self.store.materialize_artifact_projection(artifact_id="art_full", file_path=proj)
        self.assertEqual(mat["health"], "CURRENT")

        t3 = self.store.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id=td["attempt_id"],
            outcome="SUCCEEDED",
            receipt_kind="OWNER_OPERATION_RECEIPT",
            receipt_id=t2["creator_receipt_id"],
            actor_id="S01",
        )
        ts = self.store.tx_ts_create_settlement(
            action_id=t1["action_id"],
            source_fact_id=t3["source_fact_id"],
        )
        for ob in ts["obligations"]:
            if ob["target_owner"] == "S05":
                self.store.tx_t4_commit_owner_effect(
                    obligation_id=ob["obligation_id"],
                    memory_source_id="src_1",
                    expected_source_generation=1,
                    memory_content="full settlement",
                )
            else:
                self.store.tx_t4_commit_owner_effect(obligation_id=ob["obligation_id"])

        tc = self.store.tx_tc_close_settlement(envelope_id=ts["envelope_id"])
        self.assertEqual(tc["status"], "CLOSED")

        # Fenced old writer epoch must be rejected
        self.store.control.allocate_new_epoch(fence_previous=True)
        with self.assertRaises(ReconcilerContractError) as ctx:
            self.store.tx_t0_commit_decision(
                decision_id="dec_stale",
                request_id="req_stale",
                writer_epoch=1,
            )
        self.assertEqual(ctx.exception.code, "WRITER_EPOCH_FENCED")

    def test_14_compound_child_dag_partial_never_resends_succeeded(self) -> None:
        self.store.initialize()
        comp = self.store.create_compound_action(
            parent_logical_action_id="comp_dag_1",
            child_specs=[
                {"logical_action_id": "dag_c1", "provider_account": "acct_1", "payload": {"n": 1}},
                {"logical_action_id": "dag_c2", "provider_account": "acct_2", "payload": {"n": 2}},
                {"logical_action_id": "dag_c3", "provider_account": "acct_3", "payload": {"n": 3}},
            ],
        )
        c1, c2, c3 = comp["children"]

        # Idempotency keys are independent of run/epoch/retry/wall-time and bind payload
        k1 = compute_idempotency_key(
            effect_namespace="compound.batch",
            provider_account="acct_1",
            subject_id="subj_primary",
            origin_branch_id="branch_main",
            logical_action_id="dag_c1",
        )
        self.assertEqual(k1, c1["idempotency_key"])
        with self.assertRaises(ReconcilerContractError) as ctx:
            self.store.check_idempotency_payload_binding(idempotency_key=k1, payload={"n": 999})
        self.assertEqual(ctx.exception.code, "REJECT_IDEMPOTENCY_CONFLICT")

        # Dispatch and settle c1 & c2 as SUCCEEDED
        for child, rcpt_id in ((c1, "rcpt_dag_c1"), (c2, "rcpt_dag_c2")):
            td = self.store.tx_td_prepare_dispatch(action_id=child["action_id"])
            self.store.tx_ta_start_attempt(attempt_id=td["attempt_id"])
            self.store.execute_external_attempt(action_id=child["action_id"], attempt_id=td["attempt_id"])
            self.store.tx_t3_admit_action_result(
                action_id=child["action_id"],
                attempt_id=td["attempt_id"],
                outcome="SUCCEEDED",
                receipt_kind="PROVIDER_RECEIPT",
                receipt_id=rcpt_id,
            )

        # c3 executes at provider with dropped ack and is initially UNKNOWN
        td3 = self.store.tx_td_prepare_dispatch(action_id=c3["action_id"])
        self.store.tx_ta_start_attempt(attempt_id=td3["attempt_id"])
        exec3 = self.store.execute_external_attempt(
            action_id=c3["action_id"],
            attempt_id=td3["attempt_id"],
            drop_ack=True,
        )
        self.assertTrue(exec3["ack_lost"])
        self.store.tx_t3_admit_action_result(
            action_id=c3["action_id"],
            attempt_id=td3["attempt_id"],
            outcome="UNKNOWN",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_unknown_c3",
        )

        partial_view = self.store.get_compound_action_view(comp["parent_action_id"])
        self.assertEqual(partial_view["compound_status"], "PARTIAL")
        self.assertEqual([v["outcome"] for v in partial_view["child_vector"]], ["SUCCEEDED", "SUCCEEDED", "UNKNOWN"])

        # Reconcile retries ONLY the UNKNOWN child c3; c1 & c2 remain send_count == 1
        rec = self.store.retry_or_reconcile_compound_action(parent_action_id=comp["parent_action_id"])
        self.assertEqual(rec["skipped_succeeded_children"], [c1["action_id"], c2["action_id"]])
        self.assertEqual(len(rec["reconciled_children"]), 1)
        self.assertEqual(rec["reconciled_children"][0]["action_id"], c3["action_id"])
        self.assertEqual(rec["reconciled_children"][0]["outcome"], "SUCCEEDED")
        self.assertEqual(rec["after_view"]["compound_status"], "ALL_SUCCEEDED")

        self.assertEqual(self.store.provider.count_for_logical_action("dag_c1"), 1)
        self.assertEqual(self.store.provider.count_for_logical_action("dag_c2"), 1)
        self.assertEqual(self.store.provider.count_for_logical_action("dag_c3"), 1)

    def test_15_bounded_reconcile_exhaustion_quiesces_unknown_and_non_interference(self) -> None:
        self.store.initialize()
        self.store.tx_t0_commit_decision(decision_id="dec_unk", request_id="req_unk")
        t1 = self.store.tx_t1_commit_adoption_activity_action(
            decision_id="dec_unk",
            request_id="req_unk",
            logical_action_id="la_unk",
            max_reconcile_queries=5,
            conclusive_not_found=False,
        )
        td = self.store.tx_td_prepare_dispatch(action_id=t1["action_id"])
        self.store.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        self.store.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)

        # Fence old epoch, yet lookup is unavailable & attempt already STARTED -> stays UNKNOWN
        ctrl = self.store.control.allocate_new_epoch(fence_previous=True)
        epoch = int(ctrl["active_writer_epoch"])

        for q in range(1, 5):
            res = self.store.reconcile_action(action_id=t1["action_id"], writer_epoch=epoch)
            self.assertEqual(res["phase"], "RECONCILING")
            self.assertEqual(res["outcome"], "UNKNOWN")
            self.assertEqual(res["reconcile_query_count"], q)

        res5 = self.store.reconcile_action(action_id=t1["action_id"], writer_epoch=epoch)
        self.assertEqual(res5["phase"], "QUIESCED_UNRESOLVED")
        self.assertEqual(res5["outcome"], "UNKNOWN")
        self.assertEqual(res5["reconcile_query_count"], 5)

        # Further reconcile calls stop polling and NEVER coerce UNKNOWN to FAILED/SUCCEEDED
        res6 = self.store.reconcile_action(action_id=t1["action_id"], writer_epoch=epoch)
        self.assertEqual(res6["phase"], "QUIESCED_UNRESOLVED")
        self.assertEqual(res6["outcome"], "UNKNOWN")
        self.assertEqual(res6["reconcile_query_count"], 5)
        self.assertTrue(res6.get("auto_polling_stopped"))

        # Block only dependent activity; unrelated activity progresses cleanly
        self.store.block_activity_on_unknown_action(
            activity_id=t1["activity_id"],
            action_id=t1["action_id"],
        )
        with self.assertRaises(ReconcilerContractError) as blk_ctx:
            self.store.advance_unrelated_activity(activity_id=t1["activity_id"])
        self.assertEqual(blk_ctx.exception.code, "ACTIVITY_IS_BLOCKED")

        unrel = self.store.create_unrelated_activity(
            decision_id="dec_unrel",
            request_id="req_unrel",
            writer_epoch=epoch,
        )
        done_unrel = self.store.advance_unrelated_activity(activity_id=unrel["activity_id"])
        self.assertEqual(done_unrel["status"], "COMPLETED")

    def test_16_human_attestation_vs_provider_receipt_and_not_executed_proof(self) -> None:
        self.store.initialize()
        self.store.tx_t0_commit_decision(decision_id="dec_att", request_id="req_att")
        t1 = self.store.tx_t1_commit_adoption_activity_action(
            decision_id="dec_att",
            request_id="req_att",
            logical_action_id="la_att",
            conclusive_not_found=True,
        )
        td = self.store.tx_td_prepare_dispatch(action_id=t1["action_id"])
        self.store.tx_ta_start_attempt(attempt_id=td["attempt_id"])

        # HUMAN_ATTESTED_EXPERIENCE cannot be submitted through machine T3 result admission
        with self.assertRaises(ReconcilerContractError) as ctx:
            self.store.tx_t3_admit_action_result(
                action_id=t1["action_id"],
                attempt_id=td["attempt_id"],
                outcome="SUCCEEDED",
                receipt_kind="HUMAN_ATTESTED_EXPERIENCE",
                receipt_id="rcpt_bad_human",
            )
        self.assertEqual(ctx.exception.code, "HUMAN_ATTESTATION_CANNOT_SETTLE_MACHINE_OUTCOME")

        # Mark UNKNOWN first, then attach human attestation -> keeps outcome UNKNOWN
        self.store.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)
        self.store.reconcile_action(action_id=t1["action_id"], writer_epoch=1)
        hatt = self.store.record_human_attestation(
            action_id=t1["action_id"],
            actor_id="partner_1",
            statement="observed message delivery",
            stop_investigation=True,
        )
        self.assertEqual(hatt["receipt_kind"], "HUMAN_ATTESTED_EXPERIENCE")
        self.assertFalse(hatt["authoritative_for_machine_success"])
        self.assertEqual(hatt["outcome_remains"], "UNKNOWN")

        snap = self.store.snapshot_db()
        act_row = [a for a in snap["actions"] if a["action_id"] == t1["action_id"]][0]
        self.assertEqual(act_row["outcome"], "UNKNOWN")
        self.assertEqual(act_row["phase"], "QUIESCED_UNRESOLVED")

        # Authoritative PROVIDER_RECEIPT can settle it afterward
        settled = self.store.record_provider_receipt(
            action_id=t1["action_id"],
            attempt_id=td["attempt_id"],
            provider_receipt_id="prov_rcpt_auth_1",
            claimed_outcome="SUCCEEDED",
        )
        self.assertEqual(settled["outcome"], "SUCCEEDED")
        self.assertEqual(settled["phase"], "CLOSED")

        # NOT_EXECUTED requires BOTH writer fence AND store integrity proof
        self.store.tx_t0_commit_decision(decision_id="dec_ne1", request_id="req_ne1")
        t1_ne = self.store.tx_t1_commit_adoption_activity_action(
            decision_id="dec_ne1",
            request_id="req_ne1",
            logical_action_id="la_ne1",
            conclusive_not_found=True,
        )
        self.store.tx_td_prepare_dispatch(action_id=t1_ne["action_id"])
        self.store.provider.configure_lookup(lookup_enabled=True, conclusive_not_found=True)

        # 1) Unfenced old writer (fence_previous=False) -> CANNOT return NOT_EXECUTED
        ctrl_unfenced = self.store.control.allocate_new_epoch(fence_previous=False)
        epoch_2 = int(ctrl_unfenced["active_writer_epoch"])
        res_unfenced = self.store.reconcile_action(
            action_id=t1_ne["action_id"],
            writer_epoch=epoch_2,
        )
        self.assertEqual(res_unfenced["outcome"], "UNKNOWN")
        self.assertFalse(res_unfenced["old_epoch_fenced"])

        # 2) Create action at epoch 2, then fence epoch 2; broken integrity flag -> CANNOT return NOT_EXECUTED
        self.store.tx_t0_commit_decision(decision_id="dec_ne2", request_id="req_ne2", writer_epoch=epoch_2)
        t1_ne2 = self.store.tx_t1_commit_adoption_activity_action(
            decision_id="dec_ne2",
            request_id="req_ne2",
            logical_action_id="la_ne2",
            conclusive_not_found=True,
            writer_epoch=epoch_2,
        )
        self.store.tx_td_prepare_dispatch(action_id=t1_ne2["action_id"], writer_epoch=epoch_2)
        ctrl_fenced = self.store.control.allocate_new_epoch(fence_previous=True)
        epoch_fenced = int(ctrl_fenced["active_writer_epoch"])
        self.store.control.set_store_integrity_flag(False)
        res_bad_int = self.store.reconcile_action(
            action_id=t1_ne2["action_id"],
            writer_epoch=epoch_fenced,
        )
        self.assertEqual(res_bad_int["outcome"], "UNKNOWN")
        self.assertEqual(res_bad_int["store_integrity"], "CONTROL_STORE_INTEGRITY_FAILED")

        # 3) Both writer fence AND store integrity OK -> proves NOT_EXECUTED
        self.store.control.set_store_integrity_flag(True)
        res_ne = self.store.reconcile_action(
            action_id=t1_ne2["action_id"],
            writer_epoch=epoch_fenced,
        )
        self.assertEqual(res_ne["outcome"], "NOT_EXECUTED")
        self.assertEqual(res_ne["phase"], "CLOSED")

    def test_17_negative_controls_detect_broken_reconcilers(self) -> None:
        # 1. CP06 blind_resend_without_idempotency duplicates external provider invocation
        with tempfile.TemporaryDirectory() as d1:
            s1 = CrashAndPartialReconcilerStore(d1)
            s1.initialize()
            s1.tx_t0_commit_decision(decision_id="d1", request_id="r1")
            t1 = s1.tx_t1_commit_adoption_activity_action(
                decision_id="d1", request_id="r1", logical_action_id="la1"
            )
            td = s1.tx_td_prepare_dispatch(action_id=t1["action_id"])
            s1.tx_ta_start_attempt(attempt_id=td["attempt_id"])
            s1.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"], drop_ack=True)
            s1.recover_after_crash(
                crash_point="CP06",
                logical_action_id="la1",
                broken_mode="blind_resend_without_idempotency",
            )
            self.assertEqual(s1.provider.total_invocations(), 2)

        # 2. Compound naive_resend_all_children resends already SUCCEEDED children
        with tempfile.TemporaryDirectory() as d2:
            s2 = CrashAndPartialReconcilerStore(d2)
            s2.initialize()
            comp = s2.create_compound_action(
                parent_logical_action_id="comp_bad",
                child_specs=[
                    {"logical_action_id": "bad_c1"},
                    {"logical_action_id": "bad_c2"},
                ],
            )
            c1 = comp["children"][0]
            td1 = s2.tx_td_prepare_dispatch(action_id=c1["action_id"])
            s2.tx_ta_start_attempt(attempt_id=td1["attempt_id"])
            s2.execute_external_attempt(action_id=c1["action_id"], attempt_id=td1["attempt_id"])
            s2.tx_t3_admit_action_result(
                action_id=c1["action_id"],
                attempt_id=td1["attempt_id"],
                outcome="SUCCEEDED",
                receipt_kind="PROVIDER_RECEIPT",
                receipt_id="rcpt_bad_c1",
            )
            s2.retry_or_reconcile_compound_action(
                parent_action_id=comp["parent_action_id"],
                broken_mode="naive_resend_all_children",
            )
            self.assertEqual(s2.provider.count_for_logical_action("bad_c1"), 2)

        # 3. notfound_marks_failed coerces UNKNOWN to FAILED
        with tempfile.TemporaryDirectory() as d3:
            s3 = CrashAndPartialReconcilerStore(d3)
            s3.initialize()
            s3.tx_t0_commit_decision(decision_id="d3", request_id="r3")
            t1 = s3.tx_t1_commit_adoption_activity_action(
                decision_id="d3", request_id="r3", logical_action_id="la3"
            )
            res = s3.reconcile_action(action_id=t1["action_id"], broken_mode="notfound_marks_failed")
            self.assertEqual(res["outcome"], "FAILED")

        # 4. CP03 recreate_activity_on_missing_ack duplicates activities; CP05 rotate_event_id bypasses inbox
        with tempfile.TemporaryDirectory() as d4:
            s4 = CrashAndPartialReconcilerStore(d4)
            s4.initialize()
            s4.tx_t0_commit_decision(decision_id="d4", request_id="r4")
            s4.tx_t1_commit_adoption_activity_action(
                decision_id="d4", request_id="r4", logical_action_id="la4"
            )
            s4.recover_after_crash(
                crash_point="CP03",
                decision_id="d4",
                request_id="r4",
                logical_action_id="la4",
                broken_mode="recreate_activity_on_missing_ack",
            )
            self.assertEqual(len(s4.snapshot_db()["activities"]), 2)

            s4.recover_after_crash(crash_point="CP05", broken_mode="rotate_event_id_bypass_inbox")
            inbox_rows = s4.snapshot_db()["inbox"]
            self.assertTrue(any("_mutated_" in row["event_id"] for row in inbox_rows))

        # 5. CP11 fake_close_envelope closes envelope while obligations are still PENDING
        with tempfile.TemporaryDirectory() as d5:
            s5 = CrashAndPartialReconcilerStore(d5)
            s5.initialize()
            s5.tx_t0_commit_decision(decision_id="d5", request_id="r5")
            t1 = s5.tx_t1_commit_adoption_activity_action(
                decision_id="d5", request_id="r5", logical_action_id="la5"
            )
            ts = s5.tx_ts_create_settlement(action_id=t1["action_id"], source_fact_id="sf_d5")
            res_fake = s5.recover_after_crash(crash_point="CP11", broken_mode="fake_close_envelope")
            self.assertEqual(res_fake["status"], "CLOSED")
            self.assertGreater(len(res_fake["unfinished_obligations"]), 0)
            self.assertEqual(len(s5.snapshot_db()["owner_receipts"]), 0)

        # 6. CP10 overwrite_diverged overwrites user-edited .md instead of blocking
        with tempfile.TemporaryDirectory() as d6:
            s6 = CrashAndPartialReconcilerStore(d6)
            s6.initialize()
            s6.tx_t0_commit_decision(decision_id="d6", request_id="r6")
            t1 = s6.tx_t1_commit_adoption_activity_action(
                decision_id="d6", request_id="r6", logical_action_id="la6"
            )
            proj = Path(d6) / "div.md"
            s6.tx_t2_commit_artifact_version(
                artifact_id="art_div",
                action_id=t1["action_id"],
                content_body="# Canonical\n",
                projection_path=proj,
            )
            s6.materialize_artifact_projection(artifact_id="art_div", file_path=proj)
            proj.write_text("# User Edit\n", encoding="utf-8")
            blocked = s6.materialize_artifact_projection(artifact_id="art_div", file_path=proj)
            self.assertEqual(blocked["status"], "BLOCKED_DIVERGED")
            self.assertEqual(proj.read_text(encoding="utf-8"), "# User Edit\n")
            forced = s6.materialize_artifact_projection(
                artifact_id="art_div",
                file_path=proj,
                broken_mode="overwrite_diverged",
            )
            self.assertEqual(forced["status"], "MATERIALIZED")
            self.assertEqual(proj.read_text(encoding="utf-8"), "# Canonical\n")

        # 7. CP12 resurrect_deleted_source violates deletion fence
        with tempfile.TemporaryDirectory() as d7:
            s7 = CrashAndPartialReconcilerStore(d7)
            s7.initialize()
            s7.register_memory_source(source_id="src_del", generation=1)
            s7.delete_memory_source(source_id="src_del")
            s7.tx_t0_commit_decision(decision_id="d7", request_id="r7")
            t1 = s7.tx_t1_commit_adoption_activity_action(
                decision_id="d7", request_id="r7", logical_action_id="la7"
            )
            ts = s7.tx_ts_create_settlement(action_id=t1["action_id"], source_fact_id="sf_d7")
            s05_ob = [o for o in ts["obligations"] if o["target_owner"] == "S05"][0]
            with self.assertRaises(ReconcilerContractError) as del_ctx:
                s7.tx_t4_commit_owner_effect(
                    obligation_id=s05_ob["obligation_id"],
                    memory_source_id="src_del",
                    expected_source_generation=1,
                    memory_content="should fail",
                )
            self.assertEqual(del_ctx.exception.code, "REJECTED_DELETED_SOURCE")
            res_bad = s7.tx_t4_commit_owner_effect(
                obligation_id=s05_ob["obligation_id"],
                memory_source_id="src_del",
                expected_source_generation=1,
                memory_content="resurrected",
                broken_mode="resurrect_deleted_source",
            )
            self.assertEqual(res_bad["status"], "COMMITTED")

        # 8. Corrupted SQLite file or missing table fails verify_store_integrity
        with tempfile.TemporaryDirectory() as d8:
            s8 = CrashAndPartialReconcilerStore(d8)
            s8.initialize()
            conn = sqlite3.connect(str(s8.db_path))
            conn.execute("DROP TABLE owner_receipts")
            conn.commit()
            conn.close()
            ok, reason = s8.verify_store_integrity()
            self.assertFalse(ok)
            self.assertIn("MISSING_TABLES:owner_receipts", reason)


if __name__ == "__main__":
    unittest.main()
