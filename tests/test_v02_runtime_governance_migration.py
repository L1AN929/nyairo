#!/usr/bin/env python3
"""Unit & contract tests for v0.2 semantic wake coalescing, conservation/dormancy, and migration suitcase."""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class V02RuntimeGovernanceMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-rt-mig-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1-4. Contract signatures, LF line endings, character neutrality & 100% zero-write GET
    # ------------------------------------------------------------------

    def test_01_constants_signatures_lf_and_character_neutrality(self) -> None:
        self.assertEqual(rm_view.ENDPOINT_PATH, "/api/phone/v02/runtime-migration")
        self.assertEqual(rm_view.VIEW_SCHEMA, "nyairo.v02.runtime_migration.v1")
        self.assertEqual(
            rm_view.PARTNER_ALLOWED_ACTIONS,
            frozenset({
                "set_budget_mode",
                "record_carrier_check",
                "pack_selective_suitcase",
                "coalesce_probe",
            }),
        )
        self.assertEqual(
            rm_view.SUBJECT_ALLOWED_ACTIONS,
            frozenset({
                "set_budget_mode",
                "record_carrier_check",
                "pack_selective_suitcase",
                "coalesce_probe",
                "reverify_migrated_skill",
                "record_workspace_audit",
            }),
        )

        read_sig = inspect.signature(rm_view.read_v02_runtime_migration_view)
        self.assertIn("day_root", read_sig.parameters)
        self.assertIn("has_subject_capability", read_sig.parameters)
        self.assertIn("now_iso", read_sig.parameters)

        mut_sig = inspect.signature(rm_view.mutate_v02_runtime_migration_view)
        self.assertIn("day_root", mut_sig.parameters)
        self.assertIn("payload", mut_sig.parameters)
        self.assertIn("actor_side", mut_sig.parameters)
        self.assertIn("now_iso", mut_sig.parameters)

        sum_sig = inspect.signature(rm_view.summarize_runtime_migration_for_context)
        self.assertIn("day_root", sum_sig.parameters)

        owned_files = (
            ROOT / "components" / "life" / "scripts" / "v02_runtime_governance_migration.py",
            ROOT / "components" / "phone" / "v02_runtime_migration_view.py",
            ROOT / "tests" / "test_v02_runtime_governance_migration.py",
        )
        for fpath in owned_files:
            raw_bytes = fpath.read_bytes()
            self.assertNotIn(b"\r\n", raw_bytes, f"File must keep strict LF line endings: {fpath}")

        for prod_path in owned_files[:2]:
            text = prod_path.read_text(encoding="utf-8")
            self.assertNotIn("千代", text, f"Production module must stay character-neutral: {prod_path}")

    def test_02_get_and_context_summary_100pct_zero_writes_on_missing_root(self) -> None:
        before = _snapshot_tree(self.tmp)
        code, body = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T09:00:00+08:00",
        )
        ctx = rm_view.summarize_runtime_migration_for_context(self.day_root)
        after = _snapshot_tree(self.tmp)

        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["state_exists"])
        self.assertEqual(body["revision"], 0)
        self.assertEqual(body["llm_calls"], 0)
        self.assertEqual(ctx["llm_calls"], 0)
        self.assertFalse(self.day_root.exists())
        self.assertEqual(before, after, "GET and summarize_runtime_migration_for_context must do zero writes")

    def test_03_get_100pct_zero_writes_on_materialized_state_across_time(self) -> None:
        c1, _ = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "coalesce_probe",
                "family": "window_sound",
                "summary_cn": "窗外风吹树叶响了一下",
            },
            actor_side="partner",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(c1, 200)
        state_file = self.day_root / "phone" / "v02_runtime_migration.json"
        self.assertTrue(state_file.is_file())

        before = _snapshot_tree(self.tmp)
        c_p, _ = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T12:00:00+08:00",
        )
        c_s, _ = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-14T09:00:00+08:00",
        )
        _ = rm_view.summarize_runtime_migration_for_context(self.day_root)
        after = _snapshot_tree(self.tmp)

        self.assertEqual(c_p, 200)
        self.assertEqual(c_s, 200)
        self.assertEqual(before, after, "Materialized GET must not touch mtime or create temp files")

    def test_04_envelope_top_level_and_data_mirror_completeness(self) -> None:
        code, env = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T09:30:00+08:00",
        )
        self.assertEqual(code, 200)
        required_keys = (
            "runtime_mode",
            "wake_coalescer",
            "today_capability_card",
            "migration_suitcase",
            "source_freshness",
            "workspace_audits",
            "llm_calls",
        )
        for key in required_keys:
            self.assertIn(key, env)
            self.assertIn(key, env["data"])
            self.assertEqual(env[key], env["data"][key])
        self.assertEqual(env["llm_calls"], 0)
        self.assertEqual(env["schema"], "nyairo.v02.runtime_migration.v1")
        self.assertEqual(env["endpoint"], "/api/phone/v02/runtime-migration")

    # ------------------------------------------------------------------
    # 5-10. Semantic Wake Coalescer, 300-event storm, quota & control bypass
    # ------------------------------------------------------------------

    def test_05_storm_300_same_family_events_coalesced_to_single_wake(self) -> None:
        for i in range(300):
            sec = i % 60
            minute = i // 60  # spans 00..04 minutes (< 300s coalesce window)
            ts = f"2026-11-13T10:{minute:02d}:{sec:02d}+08:00"
            code, _ = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "coalesce_probe",
                    "family": "ambient_weather",
                    "summary_cn": f"窗外细雨微风变化 #{i + 1}",
                },
                actor_side="partner",
                now_iso=ts,
            )
            self.assertEqual(code, 200)

        _, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T10:04:59+08:00",
        )
        wc = view["wake_coalescer"]
        self.assertEqual(wc["effective_wakes_total"], 1)
        self.assertEqual(wc["background_wakes_hour"], 1)
        self.assertEqual(wc["background_wakes_day"], 1)
        self.assertEqual(wc["coalesced_saved_wakes"], 299)
        self.assertEqual(wc["quota_blocked_count"], 0)
        self.assertLessEqual(wc["background_wakes_hour"], wc["hourly_wake_cap"])
        self.assertEqual(
            wc["active_family_windows"]["ambient_weather"]["latest_summary_cn"],
            "窗外细雨微风变化 #300",
        )

    def test_06_control_lane_zero_delay_bypass_during_300_event_storm(self) -> None:
        control_events = [
            (10, "revoke_permission", "撤销外部共享读权限"),
            (95, "stop_word", "触发立即停止词"),
            (180, "urgent_knock", "伴侣急事敲门"),
            (260, "low_disk", "磁盘剩余空间低于安全水位"),
        ]
        ctrl_by_idx = {idx: (fam, msg) for idx, fam, msg in control_events}

        for i in range(300):
            minute = i // 60
            sec = i % 60
            ts = f"2026-11-13T11:{minute:02d}:{sec:02d}+08:00"
            rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "coalesce_probe",
                    "family": "shelf_glance",
                    "summary_cn": f"书架光影微移 #{i}",
                    "is_control": False,
                },
                actor_side="partner",
                now_iso=ts,
            )
            if i in ctrl_by_idx:
                c_fam, c_msg = ctrl_by_idx[i]
                c_code, c_body = rm_view.mutate_v02_runtime_migration_view(
                    self.day_root,
                    {
                        "action": "coalesce_probe",
                        "family": c_fam,
                        "summary_cn": c_msg,
                        "is_control": True,
                    },
                    actor_side="partner",
                    now_iso=ts,
                )
                self.assertEqual(c_code, 200)
                probe = c_body["wake_coalescer"]["last_probe_result"]
                self.assertTrue(probe["effective_wake"])
                self.assertFalse(probe["debounced"])
                self.assertEqual(probe["wake_disposition"], "CONTROL_IMMEDIATE_BYPASS")

        _, final_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T11:04:59+08:00",
        )
        wc = final_view["wake_coalescer"]
        self.assertEqual(wc["coalesced_saved_wakes"], 299)
        self.assertEqual(wc["control_lane_bypasses"], 4)
        self.assertEqual(wc["control_wakes_total"], 4)
        self.assertEqual(wc["background_wakes_hour"], 1)
        self.assertEqual(wc["effective_wakes_total"], 5)

    def test_07_hourly_wake_cap_6_blocks_excess_background_while_control_passes(self) -> None:
        families = [
            "ambient_weather",
            "window_sound",
            "shelf_glance",
            "kettle_hum",
            "curtain_shadow",
            "corridor_step",
            "raindrop_tap",
            "distant_bell",
        ]
        for idx, fam in enumerate(families):
            code, body = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "coalesce_probe",
                    "family": fam,
                    "summary_cn": f"环境线索 {fam}",
                },
                actor_side="partner",
                now_iso=f"2026-11-13T14:{idx:02d}:00+08:00",
            )
            self.assertEqual(code, 200)
            probe = body["wake_coalescer"]["last_probe_result"]
            if idx < 6:
                self.assertTrue(probe["effective_wake"])
                self.assertEqual(probe["wake_disposition"], "EFFECTIVE_BACKGROUND_WAKE")
            else:
                self.assertFalse(probe["effective_wake"])
                self.assertTrue(probe["quota_blocked"])
                self.assertEqual(probe["wake_disposition"], "QUOTA_BACKPRESSURE_DEFERRED")

        c_ctrl, b_ctrl = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "coalesce_probe",
                "family": "urgent_knock",
                "summary_cn": "急事敲门直通",
                "is_control": True,
            },
            actor_side="partner",
            now_iso="2026-11-13T14:15:00+08:00",
        )
        self.assertEqual(c_ctrl, 200)
        wc = b_ctrl["wake_coalescer"]
        self.assertEqual(wc["hourly_wake_cap"], 6)
        self.assertEqual(wc["daily_wake_cap"], 24)
        self.assertEqual(wc["background_wakes_hour"], 6)
        self.assertEqual(wc["quota_blocked_count"], 2)
        self.assertEqual(wc["control_lane_bypasses"], 1)
        self.assertTrue(wc["last_probe_result"]["effective_wake"])

    def test_08_daily_wake_cap_24_and_foreground_chat_guarantee(self) -> None:
        coalescer = rm_engine.SemanticWakeCoalescer(hourly_wake_cap=6, daily_wake_cap=24)
        for hour_idx in range(4):
            h = 8 + hour_idx * 2
            for f_idx in range(6):
                res = coalescer.route_event(
                    family=f"fam_{hour_idx}_{f_idx}",
                    summary_cn="日常环境机会",
                    is_control=False,
                    now_iso=f"2026-11-13T{h:02d}:{f_idx * 6:02d}:00+08:00",
                )
                self.assertTrue(res["effective_wake"])

        blocked = coalescer.route_event(
            family="fam_25th",
            summary_cn="第25次后台机会",
            is_control=False,
            now_iso="2026-11-13T18:00:00+08:00",
        )
        self.assertFalse(blocked["effective_wake"])
        self.assertTrue(blocked["quota_blocked"])
        self.assertEqual(blocked["wake_disposition"], "QUOTA_BACKPRESSURE_DEFERRED")

        fg = coalescer.route_event(
            family="partner_dialog",
            summary_cn="前台直接对话",
            lane="foreground_chat",
            now_iso="2026-11-13T18:01:00+08:00",
        )
        self.assertTrue(fg["effective_wake"])
        self.assertEqual(fg["wake_disposition"], "FOREGROUND_GUARANTEED")

        ctrl = coalescer.route_event(
            family="revoke_lease",
            summary_cn="撤回临时租约",
            is_control=True,
            now_iso="2026-11-13T18:02:00+08:00",
        )
        self.assertTrue(ctrl["effective_wake"])
        self.assertEqual(ctrl["wake_disposition"], "CONTROL_IMMEDIATE_BYPASS")

    def test_09_coalesce_window_expiry_opens_fresh_wake(self) -> None:
        _, r1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "第一次雨声"},
            now_iso="2026-11-13T09:00:00+08:00",
        )
        self.assertTrue(r1["wake_coalescer"]["last_probe_result"]["effective_wake"])

        _, r2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "窗口期内第二次雨声"},
            now_iso="2026-11-13T09:02:00+08:00",
        )
        self.assertFalse(r2["wake_coalescer"]["last_probe_result"]["effective_wake"])
        self.assertTrue(r2["wake_coalescer"]["last_probe_result"]["coalesced"])

        _, r3 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "六分钟后新的风声"},
            now_iso="2026-11-13T09:06:00+08:00",
        )
        self.assertTrue(r3["wake_coalescer"]["last_probe_result"]["effective_wake"])
        self.assertEqual(r3["wake_coalescer"]["effective_wakes_total"], 2)
        self.assertEqual(r3["wake_coalescer"]["coalesced_saved_wakes"], 1)

    def test_10_hidden_and_heartbeat_signals_isolated_zero_cognition(self) -> None:
        resp = {}
        for fam in ("hidden_weather", "heartbeat"):
            code, resp = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {"action": "coalesce_probe", "family": fam, "summary_cn": "静默探测信号"},
                now_iso="2026-11-13T09:10:00+08:00",
            )
            self.assertEqual(code, 200)
            probe = resp["wake_coalescer"]["last_probe_result"]
            self.assertFalse(probe["effective_wake"])
            self.assertEqual(probe["wake_disposition"], "ISOLATED_NON_COGNITIVE_SIGNAL")
        self.assertEqual(resp["wake_coalescer"]["effective_wakes_total"], 0)
        self.assertEqual(resp["llm_calls"], 0)

    # ------------------------------------------------------------------
    # 11-18. Quiet vs Stall, 90% Budget Drop Conservation, Dormant Zero Fake Backfill
    # ------------------------------------------------------------------

    def test_11_quiet_healthy_vs_active_healthy_strict_progression(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        (self.day_root / "instance.json").write_text(
            json.dumps({"schema": "phone.instance.v1", "status": "OK"}),
            encoding="utf-8",
        )
        (self.day_root / "ready.json").write_text(
            json.dumps({"ready": True, "queue_backlog": 0, "heartbeat_at": "2026-11-13T09:00:00+08:00"}),
            encoding="utf-8",
        )

        code, quiet_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T09:00:10+08:00",
        )
        self.assertEqual(code, 200)
        rm = quiet_view["runtime_mode"]
        self.assertEqual(rm["health_state"], "QUIET_HEALTHY")
        self.assertFalse(rm["is_technical_fault"])
        self.assertFalse(rm["fabricate_drama_when_quiet"])
        self.assertEqual(rm["effective_wakes_today"], 0)
        self.assertEqual(rm["llm_calls"], 0)

        _, active_view = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "shelf_glance", "summary_cn": "路过书架看了一眼"},
            now_iso="2026-11-13T09:05:00+08:00",
        )
        self.assertEqual(active_view["runtime_mode"]["health_state"], "ACTIVE_HEALTHY")
        self.assertEqual(active_view["runtime_mode"]["effective_wakes_today"], 1)

    def test_12_stalled_ready_false_never_disguised_as_quiet_day(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        (self.day_root / "ready.json").write_text(
            json.dumps({"ready": False, "status": "STALLED", "error": "world_socket_connection_refused"}),
            encoding="utf-8",
        )
        code, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        rm = view["runtime_mode"]
        self.assertEqual(rm["health_state"], "STALLED_OR_DEGRADED")
        self.assertTrue(rm["is_technical_fault"])
        self.assertFalse(rm["disguised_as_quiet"])
        self.assertGreaterEqual(len(rm["fault_reasons"]), 1)
        self.assertIn("world_socket_connection_refused", json.dumps(rm, ensure_ascii=False))

    def test_13_stalled_queue_backlog_overflow_and_dead_letter_detection(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        (self.day_root / "queue.json").write_text(
            json.dumps({
                "queue_backlog": 135,
                "dead_letter_count": 3,
                "oldest_unacked_seconds": 1200,
            }),
            encoding="utf-8",
        )
        _, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T10:30:00+08:00",
        )
        rm = view["runtime_mode"]
        self.assertEqual(rm["health_state"], "STALLED_OR_DEGRADED")
        codes = {f["code"] for f in rm["fault_reasons"]}
        self.assertIn("QUEUE_BACKLOG_OVERFLOW", codes)
        self.assertIn("QUEUE_DEAD_LETTER_PRESENT", codes)
        self.assertIn("UNACKED_MESSAGE_STALE_15M", codes)

    def test_14_stalled_heartbeat_timeout_and_corrupt_instance_json(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        (self.day_root / "instance.json").write_text("{corrupt-json", encoding="utf-8")
        (self.day_root / "ready.json").write_text(
            json.dumps({
                "ready": True,
                "heartbeat_at": "2026-11-13T08:00:00+08:00",
                "heartbeat_timeout_seconds": 60,
            }),
            encoding="utf-8",
        )
        _, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T09:00:00+08:00",
        )
        rm = view["runtime_mode"]
        self.assertEqual(rm["health_state"], "STALLED_OR_DEGRADED")
        codes = {f["code"] for f in rm["fault_reasons"]}
        self.assertIn("CORRUPT_INSTANCE_JSON", codes)
        self.assertIn("HEARTBEAT_TIMEOUT_STALLED", codes)

    def test_15_budget_drop_90pct_conservation_preserves_duties_and_never_pretends_tired(self) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "set_budget_mode",
                "mode": "LOW_BUDGET",
                "budget_scale_pct": 10,
                "reason_cn": "月底算力额度缩减 90%",
                "operation_key": "op-low-budget-10",
            },
            actor_side="partner",
            now_iso="2026-11-13T12:00:00+08:00",
        )
        self.assertEqual(code, 200)
        rm = body["runtime_mode"]
        self.assertEqual(rm["mode"], "LOW_BUDGET")
        self.assertEqual(rm["budget_scale_pct"], 10)
        self.assertEqual(rm["health_state"], "LOW_BUDGET_CONSERVATION")
        self.assertEqual(rm["allowed_work_scope"], "local_small_revision_only")
        self.assertIn("ambient_roaming_exploration", rm["paused_activities"])
        self.assertIn("new_large_project_initiation", rm["paused_activities"])
        self.assertEqual(
            rm["preserved_obligations"],
            {
                "in_progress_artifact_checkpoints": True,
                "accepted_commitments": True,
                "deletion_and_reconciliation_duties": True,
                "existing_local_macros_usable": True,
            },
        )
        self.assertFalse(rm["pretend_body_tired_or_bad_mood"])
        self.assertFalse(rm["fake_equal_intimacy_template"])
        self.assertIn("不是身体累了或心情不好", rm["summary_cn"])

    def test_16_low_budget_tightens_background_wake_caps_while_preserving_control(self) -> None:
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": 10},
            now_iso="2026-11-13T13:00:00+08:00",
        )
        _, first = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "shelf_glance", "summary_cn": "看一眼在途手稿"},
            now_iso="2026-11-13T13:05:00+08:00",
        )
        self.assertTrue(first["wake_coalescer"]["last_probe_result"]["effective_wake"])
        self.assertEqual(first["wake_coalescer"]["effective_hourly_wake_cap"], 1)

        _, second = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "另一族环境响动"},
            now_iso="2026-11-13T13:10:00+08:00",
        )
        self.assertFalse(second["wake_coalescer"]["last_probe_result"]["effective_wake"])
        self.assertTrue(second["wake_coalescer"]["last_probe_result"]["quota_blocked"])

        _, ctrl = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "revoke_grant", "summary_cn": "撤权指令", "is_control": True},
            now_iso="2026-11-13T13:11:00+08:00",
        )
        self.assertTrue(ctrl["wake_coalescer"]["last_probe_result"]["effective_wake"])

    def test_17_dormant_mode_suspends_background_cognition_and_records_dormant_since(self) -> None:
        code, dormant = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "set_budget_mode",
                "mode": "DORMANT",
                "reason_cn": "仅保留存储托管，暂停推理",
            },
            now_iso="2026-11-10T20:00:00+08:00",
        )
        self.assertEqual(code, 200)
        rm = dormant["runtime_mode"]
        self.assertEqual(rm["health_state"], "DORMANT_NO_COGNITION")
        self.assertEqual(rm["dormant_since"], "2026-11-10T20:00:00+08:00")
        self.assertTrue(rm["no_fake_backfill"])

        _, probe_resp = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "ambient_weather", "summary_cn": "休眠期间起风"},
            now_iso="2026-11-11T10:00:00+08:00",
        )
        self.assertFalse(probe_resp["wake_coalescer"]["last_probe_result"]["effective_wake"])
        self.assertEqual(
            probe_resp["wake_coalescer"]["last_probe_result"]["wake_disposition"],
            "SUPPRESSED_BY_DORMANT_MODE",
        )

    def test_18_dormant_wakeup_across_days_computes_exact_gap_and_zero_fake_backfill(self) -> None:
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "DORMANT", "reason_cn": "外出停机休眠"},
            now_iso="2026-11-10T22:00:00+08:00",
        )
        code, woke = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "NORMAL", "reason_cn": "恢复日常供给"},
            now_iso="2026-11-13T22:00:00+08:00",
        )
        self.assertEqual(code, 200)
        rm = woke["runtime_mode"]
        self.assertEqual(rm["mode"], "NORMAL")
        self.assertIsNone(rm["dormant_since"])
        self.assertEqual(rm["dormant_gap_seconds"], 259200)
        self.assertTrue(rm["no_fake_backfill"])
        self.assertEqual(rm["fake_diary_backfill_count"], 0)

        rec = rm["last_dormant_recovery"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["dormant_since"], "2026-11-10T22:00:00+08:00")
        self.assertEqual(rec["woke_at"], "2026-11-13T22:00:00+08:00")
        self.assertEqual(rec["dormant_gap_seconds"], 259200)
        self.assertTrue(rec["no_fake_backfill"])
        self.assertEqual(rec["fake_diary_entries_created"], 0)
        self.assertEqual(rec["fabricated_memories_created"], 0)

    # ------------------------------------------------------------------
    # 19-25. Carrier Migration Check, Today Capability Card, Suitcase & Source Freshness
    # ------------------------------------------------------------------

    def test_19_carrier_check_unchanged_fingerprint_keeps_continued_verdict(self) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "nyairo-default-carrier-v1",
                "carrier_name": "local_phone_host",
                "preview_available": True,
            },
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        card = body["today_capability_card"]
        self.assertEqual(card["last_migration_verdict"], "CONTINUED")
        self.assertEqual(body["last_migration_verdict"], "CONTINUED")
        self.assertEqual(card["needs_recheck"], [])
        self.assertEqual(card["paused_by_budget_or_carrier"], [])
        self.assertEqual(len(card["reliable_now"]), 5)

    def test_20_carrier_check_model_switch_marks_complex_skills_unvalidated_keeping_importance(self) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "new-reasoning-model-v2",
                "carrier_name": "local_phone_host",
                "preview_available": True,
            },
            now_iso="2026-11-13T11:00:00+08:00",
        )
        self.assertEqual(code, 200)
        card = body["today_capability_card"]
        self.assertEqual(card["last_migration_verdict"], "UNVERIFIED")
        self.assertTrue(card["personal_importance_preserved"])

        recheck_ids = {s["skill_id"] for s in card["needs_recheck"]}
        self.assertEqual(recheck_ids, {"nuanced_draft_stitching", "window_sketch_tone_tuning"})
        for item in card["needs_recheck"]:
            self.assertEqual(item["status"], "UNVALIDATED_AFTER_MIGRATION")
            self.assertTrue(item["importance_preserved"])
            self.assertTrue(bool(item["personal_importance"]))

        reliable_ids = {s["skill_id"] for s in card["reliable_now"]}
        self.assertIn("markdown_local_checkpoint", reliable_ids)
        self.assertIn("shared_promise_and_delete_ledger", reliable_ids)

    def test_21_carrier_check_missing_preview_reports_continued_with_loss(self) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "new-reasoning-model-v2",
                "carrier_name": "headless_minimal_box",
                "preview_available": False,
            },
            now_iso="2026-11-13T11:30:00+08:00",
        )
        self.assertEqual(code, 200)
        card = body["today_capability_card"]
        self.assertEqual(card["last_migration_verdict"], "CONTINUED_WITH_LOSS")
        paused_ids = {s["skill_id"] for s in card["paused_by_budget_or_carrier"]}
        self.assertIn("interactive_layout_live_preview", paused_ids)
        self.assertIn("预览", card["headline_cn"])
        loss_codes = {e["loss_code"] for e in card["loss_report"]}
        self.assertIn("HOST_LIVE_PREVIEW_UNAVAILABLE", loss_codes)

    def test_22_subject_reverify_migrated_skill_pass_and_loss_paths(self) -> None:
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "migrated-model-v3",
                "preview_available": True,
            },
            now_iso="2026-11-13T12:00:00+08:00",
        )
        c1, b1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "reverify_migrated_skill",
                "skill_id": "nuanced_draft_stitching",
                "passed": True,
                "note_cn": "拿两页旧手稿试拼了一次，衔接自然可用",
            },
            actor_side="subject",
            now_iso="2026-11-13T12:10:00+08:00",
        )
        self.assertEqual(c1, 200)
        self.assertEqual(b1["last_migration_verdict"], "UNVERIFIED")

        c2, b2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "reverify_migrated_skill",
                "skill_id": "window_sketch_tone_tuning",
                "passed": False,
                "note_cn": "新模型分镜节奏偏生硬，暂时挂起旧法",
            },
            actor_side="subject",
            now_iso="2026-11-13T12:20:00+08:00",
        )
        self.assertEqual(c2, 200)
        self.assertEqual(b2["last_migration_verdict"], "CONTINUED_WITH_LOSS")
        self.assertEqual(b2["today_capability_card"]["needs_recheck"], [])
        paused_ids = {s["skill_id"] for s in b2["today_capability_card"]["paused_by_budget_or_carrier"]}
        self.assertIn("window_sketch_tone_tuning", paused_ids)

        c3, b3 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "reverify_migrated_skill",
                "skill_id": "window_sketch_tone_tuning",
                "passed": True,
                "note_cn": "调整提示拆步后恢复稳定",
            },
            actor_side="subject",
            now_iso="2026-11-13T12:40:00+08:00",
        )
        self.assertEqual(c3, 200)
        self.assertEqual(b3["last_migration_verdict"], "CONTINUED")

    def test_23_selective_suitcase_four_routes_and_carried_vs_left_behind(self) -> None:
        _, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T10:00:00+08:00",
        )
        suit = view["migration_suitcase"]
        self.assertEqual(suit["profile_mode"], "selective_practice")
        self.assertEqual(suit["recommended_mode"], "selective_practice")
        self.assertTrue(suit["not_full_raw_dump_export"])

        routes = suit["route_comparison"]
        self.assertEqual(
            set(routes.keys()),
            {"records_only", "records_and_objects", "full_practice", "selective_practice"},
        )
        self.assertLess(
            routes["records_only"]["carried_count"],
            routes["records_and_objects"]["carried_count"],
        )
        self.assertLess(
            routes["selective_practice"]["carried_count"],
            routes["full_practice"]["carried_count"],
        )
        self.assertEqual(routes["full_practice"]["compatibility_debt_level"], "HIGH")
        self.assertEqual(routes["selective_practice"]["compatibility_debt_level"], "LOW_BALANCED")

        carried_ids = {x["item_id"] for x in suit["carried_items"]}
        left_ids = {x["item_id"] for x in suit["left_behind_items"]}
        self.assertIn("in_progress_markdown_works", carried_ids)
        self.assertIn("markdown_local_checkpoint", carried_ids)
        self.assertIn("active_shared_commitments", carried_ids)
        self.assertIn("legacy_host_exclusive_shader", left_ids)
        self.assertIn("retired_brittle_shortcut_macro", left_ids)
        self.assertIn("old_community_exclusive_doorplate", left_ids)

    def test_24_selective_suitcase_custom_keep_skill_ids_filtering(self) -> None:
        code, body = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "pack_selective_suitcase",
                "profile_mode": "selective_practice",
                "keep_skill_ids": ["nuanced_draft_stitching"],
                "leave_behind_note": "旧分镜微调和宿主预览先留在老房间归档，轻装去新环境",
            },
            actor_side="partner",
            now_iso="2026-11-13T15:00:00+08:00",
        )
        self.assertEqual(code, 200)
        suit = body["migration_suitcase"]
        carried_ids = {x["item_id"] for x in suit["carried_items"]}
        left_ids = {x["item_id"] for x in suit["left_behind_items"]}
        self.assertIn("nuanced_draft_stitching", carried_ids)
        self.assertNotIn("window_sketch_tone_tuning", carried_ids)
        self.assertIn("window_sketch_tone_tuning", left_ids)
        self.assertIn("interactive_layout_live_preview", left_ids)
        self.assertEqual(
            suit["leave_behind_note"],
            "旧分镜微调和宿主预览先留在老房间归档，轻装去新环境",
        )

    def test_25_source_freshness_summarizes_five_household_surfaces(self) -> None:
        phone_dir = self.day_root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        (phone_dir / "reunion_anchor.json").write_text(
            json.dumps({"captured_at": "2026-11-13T08:10:00+08:00"}),
            encoding="utf-8",
        )
        (phone_dir / "workbench_curio.json").write_text(
            json.dumps({"updated_at": "2026-11-13T08:20:00+08:00", "items": []}),
            encoding="utf-8",
        )
        (phone_dir / "sensory_cards.json").write_text(
            json.dumps({"updated_at": "2026-11-13T08:30:00+08:00"}),
            encoding="utf-8",
        )
        (phone_dir / "autonomy_rhythm.json").write_text(
            json.dumps({"updated_at": "2026-11-13T08:40:00+08:00"}),
            encoding="utf-8",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "record_carrier_check", "model_id": "nyairo-default-carrier-v1"},
            now_iso="2026-11-13T08:50:00+08:00",
        )

        _, view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            now_iso="2026-11-13T09:00:00+08:00",
        )
        sf = view["source_freshness"]
        for surf in ("room", "desk", "sensory", "rhythm", "migration_check"):
            self.assertIn(surf, sf)
            self.assertTrue(sf[surf]["source_exists"])
            self.assertEqual(sf[surf]["freshness_status"], "SYNCED")
            self.assertIsNotNone(sf[surf]["last_synced_at"])
        self.assertEqual(sf["room"]["last_synced_at"], "2026-11-13T08:10:00+08:00")
        self.assertEqual(sf["migration_check"]["last_synced_at"], "2026-11-13T08:50:00+08:00")

    # ------------------------------------------------------------------
    # 26-32. Privacy Redaction, RBAC 403, Validation 400, Idempotency & Context Helper
    # ------------------------------------------------------------------

    def test_26_workspace_audits_partner_redaction_vs_subject_visibility(self) -> None:
        secret_admitted = "雨夜未公开独白手稿_v3"
        secret_trimmed = "只给自己看的半页涂鸦草稿"

        code, sub_mut = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_workspace_audit",
                "wake_reason": "semantic_opportunity:desk_lamp",
                "visible_candidate_count": 3,
                "entered_workspace_count": 2,
                "admitted_candidates": [
                    {"candidate_id": "c1", "title": "共同夜茶备忘卡", "visibility": "shared"},
                ],
                "private_draft_titles": [secret_admitted],
                "trimmed_reasons": [
                    {
                        "candidate_id": "c3",
                        "title": secret_trimmed,
                        "visibility": "private_draft",
                        "reason_code": "WORKSPACE_SLOT_CAP",
                        "reason_cn": "当轮焦点只放两项材料",
                    }
                ],
                "selection_rule": "checkpoint_priority",
            },
            actor_side="subject",
            now_iso="2026-11-13T16:00:00+08:00",
        )
        self.assertEqual(code, 200)
        self.assertIn(secret_admitted, json.dumps(sub_mut, ensure_ascii=False))

        _, partner_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T16:01:00+08:00",
        )
        partner_blob = json.dumps(partner_view, ensure_ascii=False)
        self.assertNotIn(secret_admitted, partner_blob)
        self.assertNotIn(secret_trimmed, partner_blob)
        self.assertIn("[未公开私人草稿]", partner_blob)
        audit_p = partner_view["workspace_audits"][-1]
        self.assertGreaterEqual(audit_p["private_draft_redacted_count"], 2)

        _, subject_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-13T16:01:00+08:00",
        )
        subject_blob = json.dumps(subject_view, ensure_ascii=False)
        self.assertIn(secret_admitted, subject_blob)
        self.assertIn(secret_trimmed, subject_blob)

    def test_27_auto_workspace_audit_redacts_local_workbench_private_drafts_for_partner(self) -> None:
        phone_dir = self.day_root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        local_secret = "抽屉最底层未公开诗稿"
        (phone_dir / "workbench_curio.json").write_text(
            json.dumps({
                "items": [{"id": "wb-1", "title": local_secret, "shared": False}],
            }),
            encoding="utf-8",
        )

        _, partner_mut = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "coalesce_probe", "family": "shelf_glance", "summary_cn": "看了一眼工作台"},
            actor_side="partner",
            now_iso="2026-11-13T16:30:00+08:00",
        )
        self.assertNotIn(local_secret, json.dumps(partner_mut, ensure_ascii=False))

        _, subject_read = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-13T16:31:00+08:00",
        )
        self.assertIn(local_secret, json.dumps(subject_read, ensure_ascii=False))

    def test_28_rbac_partner_forbidden_from_subject_only_actions_403(self) -> None:
        before = _snapshot_tree(self.tmp)
        for bad_call in (
            ({"action": "reverify_migrated_skill", "skill_id": "nuanced_draft_stitching"}, "partner"),
            ({"action": "record_workspace_audit", "wake_reason": "manual_check"}, "partner"),
            ({"action": "set_budget_mode", "mode": "NORMAL"}, "intruder_admin"),
            ({"action": "set_budget_mode", "mode": "NORMAL"}, ""),
        ):
            payload, actor = bad_call
            code, err = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                payload,
                actor_side=actor,
                now_iso="2026-11-13T17:00:00+08:00",
            )
            self.assertEqual(code, 403)
            self.assertFalse(err["ok"])
            self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")
            self.assertEqual(err["error_code"], "FORBIDDEN_ACTOR_SIDE")
        after = _snapshot_tree(self.tmp)
        self.assertEqual(before, after, "Rejected 403 requests must not write anything to disk")

    def test_29_malformed_unknown_blank_and_forged_identity_fields_rejected_400(self) -> None:
        before = _snapshot_tree(self.tmp)
        bad_payloads = [
            None,
            [],
            {},
            {"": "blank_key", "action": "set_budget_mode", "mode": "NORMAL"},
            {" action": "set_budget_mode", "mode": "NORMAL"},
            {"action": "unknown_magic_action"},
            {"action": "set_budget_mode", "mode": "NORMAL", "unexpected_extra": 1},
            {"action": "set_budget_mode", "mode": "NORMAL", "actor_side": "subject"},
            {"action": "set_budget_mode", "mode": "NORMAL", "has_subject_capability": True},
            {"action": "set_budget_mode", "mode": "NORMAL", "mood_delta": -10},
            {"action": "set_budget_mode", "mode": ""},
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": 0},
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": 101},
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": True},
            {"action": "record_carrier_check", "model_id": "   "},
            {"action": "record_carrier_check", "model_id": "m1", "preview_available": "yes"},
            {"action": "coalesce_probe", "family": "", "summary_cn": "风声"},
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "   "},
            {"action": "coalesce_probe", "family": "window_sound", "summary_cn": "风声", "is_control": 1},
            {"action": "pack_selective_suitcase", "profile_mode": "selective_practice", "keep_skill_ids": [""]},
            {"action": "pack_selective_suitcase", "profile_mode": "selective_practice", "keep_skill_ids": ["nonexistent_skill"]},
        ]
        for p in bad_payloads:
            code, err = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                p,
                actor_side="subject",
                now_iso="2026-11-13T17:10:00+08:00",
            )
            self.assertEqual(code, 400, f"Expected 400 for payload: {p!r}")
            self.assertEqual(err["reason_code"], "INVALID_FIELDS", f"Payload: {p!r}")
            self.assertEqual(err["error_code"], "INVALID_FIELDS")

        after = _snapshot_tree(self.tmp)
        self.assertEqual(before, after, "Rejected 400 INVALID_FIELDS requests must be zero-write")

    def test_30_invalid_modes_rejected_400_invalid_mode(self) -> None:
        before = _snapshot_tree(self.tmp)
        c1, e1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "PRETEND_SLEEPY_MODE"},
            actor_side="partner",
            now_iso="2026-11-13T17:20:00+08:00",
        )
        self.assertEqual(c1, 400)
        self.assertEqual(e1["reason_code"], "INVALID_MODE")

        c2, e2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "pack_selective_suitcase", "profile_mode": "export_entire_raw_database"},
            actor_side="partner",
            now_iso="2026-11-13T17:21:00+08:00",
        )
        self.assertEqual(c2, 400)
        self.assertEqual(e2["reason_code"], "INVALID_MODE")
        self.assertEqual(before, _snapshot_tree(self.tmp))

    def test_31_idempotent_operation_key_replay_and_conflict_guard(self) -> None:
        cmd = {
            "action": "coalesce_probe",
            "family": "urgent_knock",
            "summary_cn": "门外急促敲门一次",
            "is_control": True,
            "operation_key": "op-urgent-knock-001",
        }
        c1, r1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            cmd,
            actor_side="partner",
            now_iso="2026-11-13T18:00:00+08:00",
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["command_status"], "APPLIED")
        self.assertFalse(r1["idempotent_replay"])
        self.assertEqual(r1["revision"], 1)
        self.assertEqual(r1["wake_coalescer"]["control_lane_bypasses"], 1)

        c2, r2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            cmd,
            actor_side="partner",
            now_iso="2026-11-13T18:05:00+08:00",
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r2["command_status"], "DUPLICATE")
        self.assertTrue(r2["idempotent_replay"])
        self.assertEqual(r2["revision"], 1)
        self.assertEqual(r2["wake_coalescer"]["control_lane_bypasses"], 1)

        c3, r3 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {**cmd, "summary_cn": "篡改摘要内容"},
            actor_side="partner",
            now_iso="2026-11-13T18:06:00+08:00",
        )
        self.assertEqual(c3, 409)
        self.assertEqual(r3["reason_code"], "OPERATION_KEY_CONFLICT")

    def test_32_summarize_runtime_migration_for_context_across_lifecycle(self) -> None:
        s0 = rm_view.summarize_runtime_migration_for_context(
            self.day_root,
            now_iso="2026-11-13T09:00:00+08:00",
        )
        self.assertEqual(s0["health_state"], "QUIET_HEALTHY")
        self.assertTrue(s0["cognition_allowed"])
        self.assertFalse(s0["roaming_and_large_projects_paused"])
        self.assertFalse(s0["pretend_body_tired_or_bad_mood"])
        self.assertEqual(s0["context_hint_cn"], "")

        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": 10},
            now_iso="2026-11-13T09:10:00+08:00",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "record_carrier_check", "model_id": "model-next-gen", "preview_available": False},
            now_iso="2026-11-13T09:11:00+08:00",
        )
        s1 = rm_view.summarize_runtime_migration_for_context(
            self.day_root,
            now_iso="2026-11-13T09:12:00+08:00",
        )
        self.assertEqual(s1["health_state"], "LOW_BUDGET_CONSERVATION")
        self.assertTrue(s1["roaming_and_large_projects_paused"])
        self.assertEqual(s1["last_migration_verdict"], "CONTINUED_WITH_LOSS")
        self.assertIn("nuanced_draft_stitching", s1["unvalidated_skill_ids"])
        self.assertIn("interactive_layout_live_preview", s1["paused_capability_ids"])
        self.assertIn("省电低预算", s1["context_hint_cn"])

        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "DORMANT"},
            now_iso="2026-11-13T10:00:00+08:00",
        )
        s_dorm = rm_view.summarize_runtime_migration_for_context(
            self.day_root,
            now_iso="2026-11-13T12:00:00+08:00",
        )
        self.assertFalse(s_dorm["cognition_allowed"])
        self.assertEqual(s_dorm["health_state"], "DORMANT_NO_COGNITION")

        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "NORMAL"},
            now_iso="2026-11-14T10:00:00+08:00",
        )
        s_woke = rm_view.summarize_runtime_migration_for_context(
            self.day_root,
            now_iso="2026-11-14T10:01:00+08:00",
        )
        self.assertTrue(s_woke["cognition_allowed"])
        self.assertEqual(s_woke["dormant_gap_seconds"], 86400)
        self.assertTrue(s_woke["no_fake_backfill"])
        self.assertIn("严禁补造休眠期间的假经历或假日记", s_woke["context_hint_cn"])


if __name__ == "__main__":
    unittest.main()
