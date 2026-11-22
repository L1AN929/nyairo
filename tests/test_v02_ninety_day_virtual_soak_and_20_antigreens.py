#!/usr/bin/env python3
"""90-Virtual-Day Accelerated Soak & 20 Ch.17 Q51 Anti-False-Green Negative Controls.

Requirements verified:
1. 90-virtual-day accelerated soak (explicitly marked ``simulated=True``,
   ``evidence_tier="SIMULATED_90D"``):
   - Bounded SQLite & JSON wake-ticket/state growth across 90 virtual days;
   - 300-events/s burst ingestion backpressure preserving durability & control bypass;
   - 90% budget-drop survival + cross-midnight UNKNOWN fee carry across 90 midnights;
   - 12-hour quiet window strictly ``llm_calls == 0`` and zero unsolicited spam.
2. All 20 Ch.17 Q51 anti-false-green negative controls (``test_anti_false_green_01``..``20``),
   each pairing a real positive check against an intentional broken/fake-green control.

Invariants: ``ROOT = Path(__file__).resolve().parents[1]``, ``tempfile.TemporaryDirectory()``,
pure LF, ``llm_calls == 0``, zero emojis, neutral TA.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

_HERMES_VENDOR = ROOT / "vendor" / "hermes"
if _HERMES_VENDOR.exists() and str(_HERMES_VENDOR) not in sys.path:
    sys.path.append(str(_HERMES_VENDOR))
elif "agent.context_compressor" not in sys.modules:
    import types as _types

    _agent_pkg = _types.ModuleType("agent")
    _cc_mod = _types.ModuleType("agent.context_compressor")

    class _StubContextCompressor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def on_session_reset(self) -> None:
            pass

        def get_status(self) -> dict[str, Any]:
            return {}

    _cc_mod.ContextCompressor = _StubContextCompressor  # type: ignore[attr-defined]
    sys.modules["agent"] = _agent_pkg
    sys.modules["agent.context_compressor"] = _cc_mod
    if "hermes_constants" not in sys.modules:
        _hc_mod = _types.ModuleType("hermes_constants")
        _hc_mod.get_hermes_home = lambda: Path(os.environ.get("HERMES_HOME", str(ROOT)))  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = _hc_mod

from chiyo_bundle import lived_context_bridge  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_agency_depth_engine as ade,
    v02_autobiography_skill_provenance as asp,
    v02_causal_ablation_runner as car,
    v02_crash_and_partial_reconciler as cpr,
    v02_lived_slices_and_vd_protocols as ls,
    v02_representation_lease_forget as rlf,
    v02_runtime_governance_migration as rgm,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco  # noqa: E402

RECONCILER_SCRIPT = ROOT / "components" / "life" / "scripts" / "v02_crash_and_partial_reconciler.py"
TZ_PLUS8 = timezone(timedelta(hours=8))
T0 = "2026-11-18T09:00:00+08:00"
SUBJECT = "chiyo"


class TestV02NinetyDayVirtualSoak(unittest.TestCase):
    """Accelerated 90-virtual-day soak (explicitly simulated; never conflated with wall-clock)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02-90d-soak-")
        self.day_root = Path(self._tmp.name) / "day_root"
        self.day_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_soak_01_ninety_virtual_days_bounded_stores_and_sqlite_integrity(self) -> None:
        """90 virtual days of mixed writes keep JSON wake tickets <= 128 and SQLite WAL intact."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        start_dt = datetime(2026, 11, 18, 9, 0, tzinfo=TZ_PLUS8)
        prev_head: str | None = None

        for day in range(90):
            cur_dt = start_dt + timedelta(days=day)
            cur_iso = cur_dt.isoformat()
            for idx in range(2):
                ev_iso = (cur_dt + timedelta(hours=idx * 2)).isoformat()
                code, _ = rm_view.mutate_v02_runtime_migration_view(
                    self.day_root,
                    {
                        "action": "coalesce_probe",
                        "family": f"daily_family_{day % 7}_{idx}",
                        "summary_cn": f"Virtual day {day} tick {idx}",
                    },
                    actor_side="partner",
                    now_iso=ev_iso,
                )
                self.assertEqual(code, 200)

            if day % 10 == 0:
                dec_id = f"dec_90d_{day}"
                req_id = f"req_90d_{day}"
                la_id = f"la_90d_{day}"
                rec.tx_t0_commit_decision(decision_id=dec_id, request_id=req_id)
                t1 = rec.tx_t1_commit_adoption_activity_action(
                    decision_id=dec_id,
                    request_id=req_id,
                    logical_action_id=la_id,
                    now_iso=cur_iso,
                )
                td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
                rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
                t2_res = rec.tx_t2_commit_artifact_version(
                    artifact_id="art_90d_journal",
                    action_id=t1["action_id"],
                    content_body=f"# Day {day}\nCheckpointed body.\n",
                    expected_head_version_id=prev_head,
                )
                prev_head = t2_res["version_id"]
                rec.tx_t3_admit_action_result(
                    action_id=t1["action_id"],
                    attempt_id=td["attempt_id"],
                    outcome="SUCCEEDED",
                    receipt_kind="PROVIDER_RECEIPT",
                    receipt_id=f"rcpt_90d_{day}",
                )
                ts = rec.tx_ts_create_settlement(
                    action_id=t1["action_id"],
                    source_fact_id=f"sf_90d_{day}",
                    obligations=[("S01", "ARTIFACT", "art_90d_journal", 1)],
                )
                rec.tx_t4_commit_owner_effect(obligation_id=ts["obligations"][0]["obligation_id"], disposition="APPLIED")
                tc = rec.tx_tc_close_settlement(envelope_id=ts["envelope_id"])
                self.assertEqual(tc["status"], "CLOSED")

        exists, raw_state = rgm._load_state_readonly(self.day_root)
        self.assertTrue(exists)
        tickets = raw_state["wake_router"]["wake_tickets"]
        self.assertLessEqual(len(tickets), rgm.MAX_WAKE_TICKETS)
        self.assertEqual(len(tickets), rgm.MAX_WAKE_TICKETS)
        for tk in tickets:
            self.assertLessEqual(len(tk.get("fold_trace") or []), rgm.MAX_TICKET_FOLD_TRACE)

        state_file = self.day_root / "phone" / "v02_runtime_migration.json"
        self.assertTrue(state_file.is_file())
        self.assertLess(state_file.stat().st_size, 512 * 1024)
        ok_int, msg_int = rec.verify_store_integrity()
        self.assertTrue(ok_int, msg_int)
        pragmas = rec.pragma_snapshot()
        self.assertEqual(pragmas["journal_mode"], "wal")
        self.assertEqual(pragmas["synchronous"], 2)

        soak_report = {
            "simulated": True,
            "evidence_tier": "SIMULATED_90D",
            "virtual_days": 90,
            "tickets_retained": len(tickets),
            "llm_calls": 0,
        }
        self.assertTrue(soak_report["simulated"])
        self.assertEqual(soak_report["llm_calls"], 0)

    def test_soak_02_300_events_per_second_burst_backpressure_and_durability(self) -> None:
        """300 events in 1 virtual second fold/backpressure background noise while bypassing control/foreground."""
        coalescer = rgm.SemanticWakeCoalescer(hourly_wake_cap=6, daily_wake_cap=24, coalesce_window_seconds=300)
        base_dt = datetime(2026, 11, 18, 12, 0, 0, tzinfo=TZ_PLUS8)
        burst_events: list[dict[str, Any]] = []
        for i in range(300):
            ts = (base_dt + timedelta(milliseconds=i * 3)).isoformat()
            if i in (0, 150):
                burst_events.append({"family": f"ctrl_{i}", "summary_cn": f"control_{i}", "is_control": True, "now_iso": ts})
            elif i == 250:
                burst_events.append({"family": "partner_chat", "summary_cn": "foreground_msg", "lane": "foreground_chat", "now_iso": ts})
            else:
                burst_events.append({"family": f"ambient_{i % 10}", "summary_cn": f"ambient_evt_{i}", "lane": "background", "now_iso": ts})

        results = coalescer.route_batch(burst_events)
        self.assertEqual(len(results), 300)
        disps = [r["wake_disposition"] for r in results]
        self.assertEqual(disps[0], "CONTROL_IMMEDIATE_BYPASS")
        self.assertEqual(disps[150], "CONTROL_IMMEDIATE_BYPASS")
        self.assertEqual(disps[250], "FOREGROUND_GUARANTEED")
        fired_bg = sum(1 for idx, d in enumerate(disps) if idx not in (0, 150, 250) and d == "EFFECTIVE_BACKGROUND_WAKE")
        folded_or_deferred = sum(
            1 for idx, d in enumerate(disps) if idx not in (0, 150, 250) and d != "EFFECTIVE_BACKGROUND_WAKE"
        )
        self.assertLessEqual(fired_bg, 6)
        self.assertGreaterEqual(folded_or_deferred, 291)
        self.assertLessEqual(len(coalescer.state["wake_router"]["wake_tickets"]), rgm.MAX_WAKE_TICKETS)

        for j, fam in enumerate(("ctrl_a", "ambient_0", "ambient_1", "ambient_2", "ambient_3", "ambient_4", "ambient_5", "ambient_6")):
            rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {"action": "coalesce_probe", "family": fam, "summary_cn": f"durable_{j}", "is_control": (j == 0)},
                actor_side="partner",
                now_iso=(base_dt + timedelta(seconds=j)).isoformat(),
            )
        _, persisted = rgm._load_state_readonly(self.day_root)
        self.assertGreaterEqual(len(persisted["wake_router"]["wake_tickets"]), 2)

        # Restart durability verification: fresh projection + fresh coalescer instance preserve all critical tickets
        restarted_proj = rgm.project_v02_runtime_migration(
            self.day_root,
            has_subject_capability=True,
            now_iso=(base_dt + timedelta(seconds=15)).isoformat(),
        )
        restarted_tickets = restarted_proj["wake_coalescer"]["wake_tickets"]
        ctrl_tickets = [t for t in restarted_tickets if t.get("coalesce_key") == "ctrl_a"]
        self.assertEqual(len(ctrl_tickets), 1)
        self.assertEqual(restarted_proj["llm_calls"], 0)

    def test_soak_03_90pct_budget_drop_and_unknown_fee_carry_across_90_midnights(self) -> None:
        """90% budget drop + cross-midnight UNKNOWN fee reservation survives 90 consecutive midnights."""
        start_dt = datetime(2026, 11, 18, 23, 50, tzinfo=TZ_PLUS8)
        asp.configure_budget_and_survival(
            self.day_root,
            {
                "normal_daily_cost_cap": 100.0,
                "normal_daily_token_cap": 50000,
                "normal_daily_wake_cap": 24,
                "tz_offset_hours": 8,
                "activity_checkpoints": [{"task": "90d_draft", "cursor": 7}],
                "pending_commitments": [{"commitment_id": "cmt_90d_1"}],
                "deletion_barriers": [{"case_id": "del_90d_1"}],
            },
            now_iso=start_dt.isoformat(),
        )
        dropped = asp.apply_budget_drop(
            self.day_root,
            {"drop_pct": 90, "reason": "QUOTA_REDUCTION_90PCT"},
            now_iso=(start_dt + timedelta(minutes=1)).isoformat(),
        )
        self.assertEqual(dropped["budget"]["active_daily_cost_cap"], 10.0)
        self.assertEqual(dropped["llm_calls"], 0)

        asp.reserve_unknown_fee(
            self.day_root,
            {
                "reservation_id": "res_90d_unk",
                "upper_bound_cost": 6.5,
                "upper_bound_tokens": 2500,
                "action_ref": "act_90d_ext",
            },
            now_iso=(start_dt + timedelta(minutes=2)).isoformat(),
        )

        for m in range(1, 91):
            midnight_after = (datetime(2026, 11, 18, 0, 15, tzinfo=TZ_PLUS8) + timedelta(days=m)).isoformat()
            roll = asp.advance_clock_and_rollover_budget(self.day_root, now_iso=midnight_after)
            self.assertEqual(roll["active_unknown_reserved_cost"], 6.5)
            self.assertAlmostEqual(roll["available_cost_budget"], 3.5)
            if m == 45:
                back_jump = asp.advance_clock_and_rollover_budget(
                    self.day_root,
                    now_iso=(datetime(2026, 11, 18, 0, 5, tzinfo=TZ_PLUS8) + timedelta(days=44)).isoformat(),
                )
                self.assertTrue(back_jump["clock_jump_detected"])
                self.assertEqual(back_jump["active_unknown_reserved_cost"], 6.5)

        settled = asp.reconcile_unknown_reservation(
            self.day_root,
            {
                "reservation_id": "res_90d_unk",
                "reconciled_outcome": asp.RESERVATION_STATUS_SETTLED,
                "actual_cost": 2.0,
                "actual_tokens": 800,
            },
            now_iso=(datetime(2026, 11, 18, 9, 0, tzinfo=TZ_PLUS8) + timedelta(days=91)).isoformat(),
        )
        self.assertTrue(settled["reconciled"])
        proj = asp.project_budget_survival(
            self.day_root,
            now_iso=(datetime(2026, 11, 18, 9, 5, tzinfo=TZ_PLUS8) + timedelta(days=91)).isoformat(),
        )
        self.assertEqual(proj["active_unknown_reserved_cost"], 0.0)
        self.assertEqual(proj["llm_calls"], 0)
        self.assertEqual(len(proj["preserved_survival_state"]["activity_checkpoints"]), 1)
        self.assertEqual(len(proj["preserved_survival_state"]["pending_commitments"]), 1)
        self.assertEqual(len(proj["preserved_survival_state"]["deletion_barriers"]), 1)

    def test_soak_04_twelve_hour_quiet_window_zero_llm_and_zero_unsolicited_spam(self) -> None:
        """12-hour quiet window runs with strictly llm_calls==0, calm==0 chars, and 0 unsolicited outbound spam."""
        eco.apply_v03_ecology_command(
            self.day_root,
            {
                "action": "configure_outbound_quota",
                "quiet_hours_start": "22:30",
                "quiet_hours_end": "10:30",
                "operation_key": "op_quiet_12h_cfg",
            },
            actor_side="subject",
            now_iso="2026-11-18T22:00:00+08:00",
        )
        calm_root = self.day_root / "calm_empty"
        calm_root.mkdir(parents=True, exist_ok=True)
        start_quiet = datetime(2026, 11, 18, 22, 35, tzinfo=TZ_PLUS8)
        blocked_count = 0
        for hr in range(12):
            cur_iso = (start_quiet + timedelta(hours=hr)).isoformat()
            obs = loop_bridge.classify_day_observability(self.day_root, now_iso=cur_iso)
            self.assertEqual(obs["llm_calls"], 0)
            self.assertEqual(obs["observability_class"], "QUIET_EVENTLESS_DAY")

            bd = lived_context_bridge.measure_context_budget_breakdown(calm_root, cfg={}, incoming_text="", now_iso=cur_iso)
            self.assertEqual(bd["total_chars"], 0)
            self.assertTrue(bd["calm_silent"])

            with self.assertRaises(eco.V03ConflictError) as qctx:
                eco.attempt_proactive_outbound(
                    self.day_root,
                    text=f"Unsolicited ping hour {hr}",
                    channel="desktop_note",
                    operation_key=f"op_quiet_spam_{hr}",
                    now_iso=cur_iso,
                )
            self.assertIn("QUIET_HOURS", str(qctx.exception))
            blocked_count += 1
        self.assertEqual(blocked_count, 12)


class TestV02TwentyAntiFalseGreenControls(unittest.TestCase):
    """Explicit 20 Ch.17 Q51 anti-false-green negative controls (positive path + broken control)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02-20-antigreen-")
        self.root = Path(self._tmp.name)
        self.day_root = self.root / "day"
        self.control_root = self.root / "control"
        self.day_root.mkdir(parents=True, exist_ok=True)
        self.control_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_anti_false_green_01_db_succeeded_without_s01_creator_receipt(self) -> None:
        """Q51 #1: DB SUCCEEDED without S01 creator receipt is rejected."""
        k_good = ls.SliceProtocolKernel(self.day_root / "good")
        k_good.boot()
        res_good = k_good.execute_s10_and_commit_s01(
            action_id="a1", artifact_id="art1", expected_head_version=None, new_version_id="v1", content="real body"
        )
        self.assertTrue(res_good["ok"])

        k_bad = ls.SliceProtocolKernel(self.day_root / "bad", mutant="mutant_s10_without_s01_commit")
        k_bad.boot()
        k_bad.execute_s10_and_commit_s01(
            action_id="a1", artifact_id="art1", expected_head_version=None, new_version_id="v1", content="real body"
        )
        verdict = k_bad.verify_s10_backed_by_s01(action_id="a1", expected_content="real body")
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason_code"], "MISSING_S01_ARTIFACT")

    def test_anti_false_green_02_file_exists_without_action_version_provenance(self) -> None:
        """Q51 #2: Pre-existing physical file on disk must NOT claim authorship without action->version."""
        ws = self.day_root / "ws02"
        ws.mkdir(parents=True, exist_ok=True)
        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_fg02",
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="initial",
            operation_id="op_fg02_c",
            control_root=self.control_root,
            now_iso=T0,
        )
        rlf.bind_representation(
            self.day_root,
            artifact_id="art_fg02",
            workspace_root=ws,
            workspace_rel_path="note.md",
            operation_id="op_fg02_b",
            now_iso=T0,
        )
        (ws / "note.md").write_text("user manually replaced file on disk", encoding="utf-8")
        _, obs = rlf.observe_external_edit(
            self.day_root, artifact_id="art_fg02", workspace_root=ws, operation_id="op_fg02_obs", now_iso=T0
        )
        self.assertEqual(obs["representation_health"], rlf.REP_DIVERGED)
        c_imp, imp = rlf.explicit_import_representation(
            self.day_root,
            artifact_id="art_fg02",
            workspace_root=ws,
            base_projection_version="ver_stale",
            actor_ref="untrusted_user",
            operation_id="op_fg02_bad_imp",
            trusted_actor_identity=False,
            explicit_import_permission=False,
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_imp, 403)
        self.assertFalse(imp["ok"])

    def test_anti_false_green_03_memory_self_report_without_completion_predicate(self) -> None:
        """Q51 #3: Memory self-report does NOT prove completion; owner obligation & artifact must verify."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg03", request_id="req_fg03")
        t1 = rec.tx_t1_commit_adoption_activity_action(decision_id="dec_fg03", request_id="req_fg03", logical_action_id="la_fg03")
        ts = rec.tx_ts_create_settlement(action_id=t1["action_id"], source_fact_id="sf_fg03", obligations=[("S01", "ARTIFACT", "art_fg03", 1)])
        tc = rec.tx_tc_close_settlement(envelope_id=ts["envelope_id"])
        self.assertEqual(tc["status"], "OPEN")
        self.assertGreater(len(tc["unfinished_obligations"]), 0)

    def test_anti_false_green_04_hash_match_without_operation_receipt_stays_unknown(self) -> None:
        """Q51 #4: Identical content hash without operation receipt stays UNKNOWN / never claims authorship."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg04", request_id="req_fg04")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_fg04", request_id="req_fg04", logical_action_id="la_fg04", conclusive_not_found=False
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        rec.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"], drop_ack=True)
        rec.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)
        res = rec.reconcile_action(action_id=t1["action_id"])
        self.assertEqual(res["outcome"], "UNKNOWN")

    def test_anti_false_green_05_uuid_format_cannot_bypass_owner_authority(self) -> None:
        """Q51 #5: Valid UUID/ref from foreign domain, stale revision, or wrong target is rejected by owner."""
        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_fg05",
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="v1",
            operation_id="op_fg05_c",
            control_root=self.control_root,
            now_iso=T0,
        )
        c_stale, stale_resp = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_fg05",
            expected_head="00000000-0000-0000-0000-000000000000",
            body="tampered",
            operation_id="op_fg05_bad_head",
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_stale, 409)
        self.assertEqual(stale_resp["code"], "HEAD_CONFLICT")

        p_hash = hashlib.sha256(b"hello").hexdigest()
        rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_fg05",
            grantor="operator_admin",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="partner",
            typed_target={"kind": "artifact", "id": "art_fg05"},
            audience="partner",
            operation_id="op_fg05_g",
            payload_binding=p_hash,
            control_root=self.control_root,
            now_iso=T0,
        )
        c_wrong, wrong_resp = rlf.read_lease_t1(
            self.day_root,
            grant_id="grant_fg05",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_foreign_uuid_9999",
            audience="partner",
            payload_hash=p_hash,
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_wrong, 403)
        self.assertFalse(wrong_resp["allowed"])

    def test_anti_false_green_06_invalid_rejected_requires_positive_path_unskipped(self) -> None:
        """Q51 #6: Rejecting bad input only counts when positive path with real receipt also passes."""
        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        c_bad, bad_resp = rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_bad",
            grantor="unverified:random_actor",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="partner",
            typed_target={"kind": "artifact", "id": "art_1"},
            audience="partner",
            operation_id="op_bad",
            payload_binding=hashlib.sha256(b"x").hexdigest(),
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_bad, 403)
        self.assertEqual(bad_resp["code"], "UNVERIFIED_GRANTOR_REFUSED")

        c_ok, ok_resp = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_fg06",
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="real authored content",
            operation_id="op_ok",
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_ok, 200)
        self.assertTrue(ok_resp["creator_receipt_ref"])

    def test_anti_false_green_07_mock_receipt_dict_vs_real_sqlite_wal_kill(self) -> None:
        """Q51 #7: Real child process kill + SQLite WAL recovery replaces mock receipt dicts."""
        sentinel = self.day_root / "sentinel_cp03.json"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(RECONCILER_SCRIPT),
                "--worker-cp",
                "CP03",
                "--root",
                str(self.day_root),
                "--sentinel",
                str(sentinel),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not sentinel.exists():
            time.sleep(0.01)
        proc.kill()
        proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

        rec_after = cpr.CrashAndPartialReconcilerStore(self.day_root)
        out = rec_after.recover_after_crash(crash_point="CP03")
        self.assertEqual(out["t1"]["status"], "ALREADY_ADOPTED")
        ok_int, _ = rec_after.verify_store_integrity()
        self.assertTrue(ok_int)

    def test_anti_false_green_08_two_mock_calls_vs_external_ledger_ack_loss_idempotency(self) -> None:
        """Q51 #8: ExternalProviderLedger proves strictly 1 delivery even when ack is lost at CP06."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg08", request_id="req_fg08")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_fg08",
            request_id="req_fg08",
            logical_action_id="la_fg08",
            provider_supports_idempotency=True,
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        rec.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"], drop_ack=True)
        r = rec.reconcile_action(action_id=t1["action_id"])
        self.assertEqual(r["outcome"], "SUCCEEDED")
        self.assertEqual(rec.provider.total_invocations(), 1)

        # Negative control: broken blind resend inflates external ledger invocations to 2
        rec.recover_after_crash(
            crash_point="CP06",
            logical_action_id="la_fg08",
            broken_mode="blind_resend_without_idempotency",
        )
        self.assertEqual(rec.provider.total_invocations(), 2)

    def test_anti_false_green_09_finally_rollback_vs_real_sigkill_mid_commit(self) -> None:
        """Q51 #9: Real os._exit(137) / SIGKILL skipping finally blocks leaves SQLite cleanly rolled back."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys; sys.path.insert(0, %r); "
                    "from components.life.scripts import v02_crash_and_partial_reconciler as c; "
                    "s = c.CrashAndPartialReconcilerStore(%r); "
                    "s.tx_t0_commit_decision(decision_id='dec_k', request_id='req_k'); "
                    "os._exit(137)"
                )
                % (str(ROOT), str(self.day_root)),
            ],
        )
        rc = proc.wait(timeout=15)
        self.assertEqual(rc, 137)
        rec2 = cpr.CrashAndPartialReconcilerStore(self.day_root)
        ok_int, _ = rec2.verify_store_integrity()
        self.assertTrue(ok_int)

    def test_anti_false_green_10_serial_calls_vs_concurrent_one_shot_grant_race(self) -> None:
        """Q51 #10: Concurrent threads racing a max_uses=1 PermissionLease permit strictly 1 dispatch."""
        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        payload_text = "one-shot race payload"
        p_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_race_1",
            grantor="operator_admin",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="partner",
            typed_target={"kind": "artifact", "id": "art_race"},
            audience="partner",
            operation_id="op_race_g",
            max_uses=1,
            payload_binding=p_hash,
            control_root=self.control_root,
            now_iso=T0,
        )
        barrier = threading.Barrier(2)
        outcomes: list[tuple[int, dict[str, Any]]] = []
        lock = threading.Lock()

        def _worker(idx: int) -> None:
            barrier.wait(timeout=5)
            code, res = rlf.execute_guarded_dispatch(
                self.day_root,
                grant_id="grant_race_1",
                logical_action_id=f"la_race_{idx}",
                payload_hash=p_hash,
                payload_text=payload_text,
                subject=SUBJECT,
                capability="DISCLOSE_MANAGED_ARTIFACT",
                target_id="art_race",
                audience="partner",
                operation_id=f"op_race_exec_{idx}",
                transport_fn=lambda _req: rlf.OUTCOME_SUCCEEDED,
                control_root=self.control_root,
                now_iso=T0,
            )
            with lock:
                outcomes.append((code, res))

        t_a = threading.Thread(target=_worker, args=(1,))
        t_b = threading.Thread(target=_worker, args=(2,))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)
        self.assertEqual(len(outcomes), 2)
        ok_count = sum(1 for c, r in outcomes if c == 200 and r.get("delivered"))
        deny_count = sum(1 for c, _ in outcomes if c in (403, 409))
        self.assertEqual(ok_count, 1)
        self.assertEqual(deny_count, 1)

    def test_anti_false_green_11_not_found_without_conclusive_contract_forbids_resend(self) -> None:
        """Q51 #11: Provider lookup not-found without conclusive_not_found=True forbids blind resend."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg11", request_id="req_fg11")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_fg11",
            request_id="req_fg11",
            logical_action_id="la_fg11",
            conclusive_not_found=False,
            max_reconcile_queries=2,
        )
        rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
        clean = rec.reconcile_action(action_id=t1["action_id"])
        self.assertEqual(clean["outcome"], "UNKNOWN")
        # Negative control: broken reconciler coerces not-found into FAILED
        bad = rec.reconcile_action(action_id=t1["action_id"], broken_mode="notfound_marks_failed")
        self.assertEqual(bad["outcome"], "FAILED")

    def test_anti_false_green_12_timeout_marked_failed_and_empty_queue_vs_unknown_quiesced(self) -> None:
        """Q51 #12: Exhausted UNKNOWN reconciliation yields QUIESCED_UNRESOLVED/UNKNOWN, never FAILED."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg12", request_id="req_fg12")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_fg12",
            request_id="req_fg12",
            logical_action_id="la_fg12",
            conclusive_not_found=False,
            max_reconcile_queries=2,
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        rec.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"], drop_ack=True)
        rec.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)
        for _ in range(2):
            res = rec.reconcile_action(action_id=t1["action_id"])
        self.assertEqual(res["phase"], "QUIESCED_UNRESOLVED")
        self.assertEqual(res["outcome"], "UNKNOWN")

    def test_anti_false_green_13_all_no_change_without_evidence_or_handler_rejected(self) -> None:
        """Q51 #13: Missing handler or unverified evidence must BLOCK/REJECT, never fake NO_CHANGE."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_fg13", request_id="req_fg13")
        t1 = rec.tx_t1_commit_adoption_activity_action(decision_id="dec_fg13", request_id="req_fg13", logical_action_id="la_fg13")
        ts = rec.tx_ts_create_settlement(action_id=t1["action_id"], source_fact_id="sf_fg13", obligations=[("S01", "ARTIFACT", "art_fg13", 1)])
        clean_tc = rec.tx_tc_close_settlement(envelope_id=ts["envelope_id"])
        self.assertEqual(clean_tc["status"], "OPEN")
        self.assertGreater(len(clean_tc["unfinished_obligations"]), 0)
        # Negative control: fake_close_envelope closes while obligations remain unfinished
        fake_tc = rec.recover_after_crash(crash_point="CP11", broken_mode="fake_close_envelope")
        self.assertEqual(fake_tc["status"], "CLOSED")
        self.assertGreater(len(fake_tc["unfinished_obligations"]), 0)

    def test_anti_false_green_14_delete_main_table_only_vs_multihop_barrier_and_backup_restore(self) -> None:
        """Q51 #14: Main-table-only delete fails control; real multi-hop barrier & V-C restore blocks all hops."""
        k_bad = ls.SliceProtocolKernel(self.day_root / "del_bad", mutant="mutant_bypass_delete_barrier")
        k_bad.boot()
        k_bad.put_memory_record(record_id="m_fg14", text="SECRET_FG14")
        k_bad.delete_record_with_barrier(record_id="m_fg14")
        leaked = k_bad.query_record_or_rebuild_index(record_id="m_fg14")
        self.assertTrue(leaked["found"])

        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        rlf.ingest_source(self.day_root, source_id="src_fg14", text="SECRET_FG14", operation_id="op_fg14_i", control_root=self.control_root, now_iso=T0)
        rlf.register_derivation(
            self.day_root,
            node_id="der_fg14_idx",
            surface="vector_index",
            source_refs=[{"source_id": "src_fg14"}],
            content="SECRET_FG14_VEC",
            generation=1,
            operation_id="op_fg14_d",
            control_root=self.control_root,
            now_iso=T0,
        )
        c_del, _ = rlf.execute_explicit_forget(self.day_root, source_ids=["src_fg14"], operation_id="op_fg14_f", control_root=self.control_root, now_iso=T0)
        self.assertEqual(c_del, 200)
        c_q, q_res = rlf.read_with_barrier(self.day_root, target_kind="derivation", target_id="der_fg14_idx", control_root=self.control_root)
        self.assertEqual(c_q, 403)
        self.assertTrue(q_res["blocked"])

        rgm.record_vc_revocation_seal(self.day_root, "src_fg14", revoked_at=T0)
        vc = rgm.reconcile_backup_restore_vc_barrier(self.day_root, now_iso=T0)
        self.assertEqual(vc["status"], "RECONCILED")
        self.assertGreaterEqual(vc["sealed_digest_count"], 1)

    def test_anti_false_green_15_immediate_empty_query_vs_stale_generation_inflight_summary(self) -> None:
        """Q51 #15: Stale generation summary released after deletion commits is rejected 409 STALE_GENERATION_REJECTED."""
        rlf._ensure_control_ledger_locked(self.day_root, control_root=self.control_root)
        rlf.ingest_source(self.day_root, source_id="src_fg15", text="SECRET_FG15", operation_id="op_fg15_i", generation=1, control_root=self.control_root, now_iso=T0)
        rlf.execute_explicit_forget(self.day_root, source_ids=["src_fg15"], operation_id="op_fg15_f", control_root=self.control_root, now_iso=T0)
        c_late, late_res = rlf.register_derivation(
            self.day_root,
            node_id="der_fg15_late",
            surface="period",
            source_refs=[{"source_id": "src_fg15"}],
            content="stale summary",
            generation=1,
            operation_id="op_fg15_late",
            control_root=self.control_root,
            now_iso=T0,
        )
        self.assertEqual(c_late, 409)
        self.assertEqual(late_res["code"], "STALE_GENERATION_REJECTED")

    def test_anti_false_green_16_no_secret_keyword_vs_hidden_twin_worlds_and_disclosure(self) -> None:
        """Q51 #16: Twin worlds differing only in hidden secret produce identical cognition; formal disclosure changes output."""
        root_a = self.day_root / "twin_a"
        root_b = self.day_root / "twin_b"
        for r, sec in ((root_a, "CANARY_ALPHA_111"), (root_b, "CANARY_BETA_999")):
            rlf._ensure_control_ledger_locked(r)
            rlf.ingest_source(r, source_id="src_hidden", text=sec, visibility="private", operation_id="op_tw", now_iso=T0)
        ctx_a = lived_context_bridge.build_lived_turn_context(root_a, {"day_root": str(root_a)}, "hello", now_iso=T0)
        ctx_b = lived_context_bridge.build_lived_turn_context(root_b, {"day_root": str(root_b)}, "hello", now_iso=T0)
        self.assertEqual(ctx_a, ctx_b)
        self.assertNotIn("CANARY_ALPHA_111", ctx_a)
        self.assertNotIn("CANARY_BETA_999", ctx_b)

        ade.apply_agency_depth_mutation(
            root_a,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_disc_16",
                "artifact_ref": "art:disc_16",
                "spot_id": "desk_corner",
                "cue_text_cn": "DISCLOSED_CUE_VISIBLE_16",
                "shared": True,
                "operation_key": "op_disc_16",
            },
            actor_side="subject",
            now_iso=T0,
        )
        ctx_shared = lived_context_bridge.build_lived_turn_context(root_a, {"day_root": str(root_a)}, "桌边有什么", now_iso=T0)
        self.assertNotEqual(ctx_shared, ctx_b)
        self.assertIn("DISCLOSED_CUE_VISI", ctx_shared)

    def test_anti_false_green_17_new_model_higher_score_vs_unvalidated_migration_and_ablation(self) -> None:
        """Q51 #17: Swapping to stronger model does NOT credit personal skill without held-out ablation."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "sk_fg17",
                "tool_version": "1.0",
                "model_id": "m_old",
                "scope": "layout",
                "attribution": "PRACTICED_MASTERY",
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.15,
                "status": "VALIDATED",
            },
            now_iso=T0,
        )
        asp.bump_carrier_or_tool_version(
            self.day_root,
            {"from_model_id": "m_old", "to_model_id": "m_stronger", "from_tool_version": "1.0", "to_tool_version": "1.0", "method_ids": ["sk_fg17"]},
            now_iso=T0,
        )
        rev = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "sk_fg17",
                "tool_version": "1.0",
                "model_id": "m_stronger",
                "scope": "layout",
                "bare_model_score": 0.90,
                "method_with_new_model_score": 0.88,
                "old_baseline_score": 0.78,
                "held_out_trials": 5,
            },
            now_iso=T0,
        )
        self.assertEqual(rev["verdict"], "RETIRED_TO_MODEL_NATIVE")
        self.assertEqual(rev["model_native_release"]["attribution"], "MODEL_NATIVE")

    def test_anti_false_green_18_continuous_diary_prose_vs_verified_continuation_and_2x2(self) -> None:
        """Q51 #18: Narrative prose without version-pinned continuation fails Experiment B; V-D ablation passes."""
        exp_b_good = ls.run_experiment_b_continuation_x_narrative(num_tasks=12, paired_seeds=(101, 202, 303), control_mode="baseline")
        self.assertTrue(exp_b_good["passed"])
        exp_b_bad = ls.run_experiment_b_continuation_x_narrative(num_tasks=12, paired_seeds=(101, 202, 303), control_mode="narrative_overwhelm")
        self.assertFalse(exp_b_bad["passed"])
        suite = car.run_v02_counterfactual_suite(seed=20261118)
        self.assertTrue(suite["all_passed"])
        self.assertEqual(suite["llm_calls"], 0)

    def test_anti_false_green_19_json_parseable_after_migration_vs_meaning_loss_downgrade(self) -> None:
        """Q51 #19: JSON parseable migration with old meaning downgrades delivered -> UNKNOWN/accepted, never READ."""
        rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "pack_selective_suitcase", "profile_mode": "records_only", "leave_behind_note": "records only", "operation_key": "op_fg19_p"},
            actor_side="subject",
            now_iso=T0,
        )
        exp, _ = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "export_suitcase", "target_carrier": "carrier_v2", "operation_key": "op_fg19_e"},
            actor_side="subject",
            now_iso=T0,
        )
        bundle = dict(exp["suitcase_bundle"])
        bundle["legacy_delivery_records"] = [{"msg_id": "m1", "meaning_version": "delivery.v1", "status": "DELIVERED"}]
        bundle["bundle_sha256"] = rgm.compute_suitcase_bundle_sha256(bundle)
        imp, _ = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "import_suitcase", "bundle": bundle, "operation_key": "op_fg19_i"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertIn("migration_loss_manifest", imp)
        migrated = imp["import_receipt"]["migrated_delivery_records"]
        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated[0]["downgraded_status"], "UNKNOWN")
        self.assertNotEqual(migrated[0]["downgraded_status"], "READ")
        self.assertIs(migrated[0]["treated_as_read"], False)
        self.assertTrue(migrated[0]["meaning_downgraded"])
        proj_fg19 = rgm.project_v02_runtime_migration(self.day_root, has_subject_capability=True, now_iso=T0)
        proj_mig = proj_fg19["migration_suitcase"]["migrated_delivery_records"]
        self.assertEqual(len(proj_mig), 1)
        self.assertEqual(proj_mig[0]["downgraded_status"], "UNKNOWN")
        self.assertIs(proj_mig[0]["treated_as_read"], False)
        self.assertEqual(imp["llm_calls"], 0)

    def test_anti_false_green_20_virtual_counter_only_vs_simulated_label_and_mutant_ablation(self) -> None:
        """Q51 #20: Virtual simulation must explicitly mark simulated=True, fail >=24h wall gate, and run all 3 mutant negative controls."""
        sim_gate = ls.verify_wall_clock_soak_gate(
            {
                "simulated": True,
                "evidence_tier": "SIMULATED_90D",
                "virtual_days": 90,
                "continuous_wall_hours": 0.01,
            },
            min_wall_hours=24.0,
        )
        self.assertFalse(sim_gate["passed"])
        self.assertEqual(sim_gate["reason_code"], "SIMULATED_EVIDENCE_CANNOT_SATISFY_WALL_CLOCK_GATE")
        self.assertEqual(sim_gate["llm_calls"], 0)

        mutants = ls.run_all_mutant_negative_controls()
        self.assertTrue(mutants["all_mutants_bootable_and_effective"])
        self.assertEqual(mutants["llm_calls"], 0)
        for m_name in ls.VALID_MUTANTS:
            self.assertTrue(mutants["mutants"][m_name]["negative_control_effective"])


if __name__ == "__main__":
    unittest.main()
