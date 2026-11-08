#!/usr/bin/env python3
"""G1 新封闭入口 g 的离线单元用例。

只测本入口新增的边界，不重跑 a/b/c/d/e/f 的旧套件，也不起真实链：

1. g 的封闭身份（root/authority/run/域/history/窗口/绝对线）与 a/b/c/f 映射不动；
2. 完整门投影的正控与十个反例（错作品/错 Activity/旧 receipt/未武装/顺序/
   NO_ACTION 后没续/CAS 非冲突/未对账/覆盖缺失）；
3. 既有 ``_apply_round`` 的纯数据反例仍然一步都不推进；
4. 离线夹具根守卫只认本批前缀。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_integrated_g as g  # noqa: E402
import g1_real_adoption as g1  # noqa: E402


class GProfileTest(unittest.TestCase):
    def test_g_is_a_closed_identity_and_abc_f_do_not_move(self):
        profile = g.PROFILE
        self.assertEqual(profile.key, "g1-integrated-g")
        self.assertEqual(str(profile.root), "/tmp/nyairo-g1-real-20261106-g")
        self.assertEqual(str(profile.authority_dir),
                         "/tmp/nyairo-g1-real-20261106-g-memory-authority")
        self.assertEqual(str(profile.authority_db),
                         "/tmp/nyairo-g1-real-20261106-g-memory-authority/authority.db")
        self.assertEqual(profile.run_id, "run:g1-real-20261106-g")
        self.assertEqual(profile.domain_id, "g1:real:20261106-g")
        self.assertEqual(profile.expected_history, 507)
        self.assertEqual(profile.window_seconds, 3600)
        self.assertEqual(profile.max_ticks, 1000)
        self.assertEqual(profile.trace_max_records, 10000)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-07T00:50:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-06T23:30:00+08:00")
        # a/b/c 原始映射与旧 CLI 元组不动；f 的封闭值也不动。
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertEqual(g1.BATCH_PROFILES["a"].expected_history, 98)
        self.assertEqual(g1.F_PROFILE.expected_history, 168)
        self.assertEqual(g1._profile_window_seconds(g1.F_PROFILE), 1800)
        self.assertEqual(g1._profile_max_ticks(g1.F_PROFILE), 900)
        self.assertIs(g1._select_batch(g.BATCH), profile)

    def test_g_gate_is_explicit_off_and_not_a_natural_batch(self):
        self.assertEqual(g1._world_intent_gate(g.PROFILE), "off")
        self.assertFalse(g1._is_natural(g.PROFILE))
        self.assertEqual(g1._phase_cleanup_seconds(g.PROFILE), 60.0)
        self.assertEqual(g1._phase_cleanup_seconds(g1.BATCH_PROFILES["a"]), 120.0)

    def test_g_cumulative_gate_is_1000_and_history_is_507(self):
        self.assertEqual(g1.G_TOTAL_ACCOUNTING_CAP, 1000)
        self.assertFalse(g1._total_accounting_exceeded(0, profile=g.PROFILE))
        self.assertFalse(g1._total_accounting_exceeded(20, profile=g.PROFILE))
        self.assertTrue(g1._total_accounting_exceeded(493, profile=g.PROFILE))
        # 旧批不进入本 g 的累计门
        self.assertFalse(g1._total_accounting_exceeded(900, profile=g1.BATCH_PROFILES["a"]))


class GateProjectionTest(unittest.TestCase):
    def test_the_complete_fixture_is_covered(self):
        p1, p2, report = g._complete_fixture()
        projection = g.gate_projection(p1, p2, report)
        self.assertEqual(projection["result"], g.RESULT_COVERED, projection)
        self.assertEqual(projection["missing"], [], projection)
        self.assertNotEqual(projection["pids"]["p1"], projection["pids"]["p2"])

    def test_every_wrong_answer_is_not_covered(self):
        p1, p2, report = g._complete_fixture()
        self.assertEqual(g.gate_projection(p1, p2, report)["result"],
                         g.RESULT_COVERED)
        negatives = g.gate_negatives()
        self.assertEqual(negatives["complete_control"], g.RESULT_COVERED, negatives)
        for name, result in negatives.items():
            if name == "complete_control":
                continue
            self.assertEqual(result, g.RESULT_NOT_COVERED, name)

    def test_missing_unknown_arm_alone_breaks_the_projection(self):
        p1, p2, report = g._complete_fixture()
        p2["unknown_armed_before_first_round"] = False
        projection = g.gate_projection(p1, p2, report)
        self.assertEqual(projection["result"], g.RESULT_NOT_COVERED)
        self.assertIn("unknown_armed_before_first_round", projection["missing"])

    def test_no_action_must_be_followed_by_a_real_continue(self):
        p1, p2, report = g._complete_fixture()
        p2["rounds"] = p2["rounds"][:-1]
        p2["final_state"] = g1.STATE_AWAIT_CONTINUE_AFTER_QUIET
        projection = g.gate_projection(p1, p2, report)
        self.assertEqual(projection["result"], g.RESULT_NOT_COVERED)
        self.assertIn("continue_after_no_action", projection["missing"])

    def test_ordered_subsequence_rejects_scrambled_states(self):
        self.assertTrue(g._ordered_subsequence(list(g._ORDER), g._ORDER))
        scrambled = [g1.STATE_AWAIT_CAS, g1.STATE_AWAIT_CONTINUE,
                     g1.STATE_AWAIT_UNKNOWN, g1.STATE_COVERED]
        self.assertFalse(g._ordered_subsequence(scrambled, g._ORDER))

    def test_apply_round_negatives_still_do_not_advance(self):
        negatives = g1._offline_state_negatives()
        self.assertEqual(negatives["different_activity"], g1.STATE_AWAIT_CONTINUE)
        self.assertEqual(negatives["other_work"], g1.STATE_AWAIT_CONTINUE)
        self.assertEqual(negatives["unreconciled"], g1.STATE_AWAIT_UNKNOWN)
        self.assertEqual(negatives["non_conflict_failed"], g1.STATE_AWAIT_CAS)
        self.assertEqual(negatives["concurrent_not_applied"], g1.STATE_AWAIT_CAS)


class ScratchGuardTest(unittest.TestCase):
    def test_offline_guard_only_accepts_the_g_prefix_under_temp(self):
        import tempfile

        good = Path(tempfile.gettempdir()) / "g1-integrated-g-offline-probe" / "root"
        bad = Path(tempfile.gettempdir()) / "other-probe" / "root"
        self.assertTrue(g._is_scratch_root(good))
        self.assertFalse(g._is_scratch_root(bad))
        self.assertFalse(g._is_scratch_root("/root/nyairo-g1-real-20261106-g"))


SENTINEL = "PRIVATE_SENTINEL_endpoint_key_detail_tokens"


def _nested_injected_result() -> dict:
    return {
        "ok": True,
        "detail": SENTINEL,
        "model_params": {"base_url": SENTINEL, "api_key": SENTINEL},
        "ledger": {"state": "ok", "run_window_calls": 3, "utc_day_calls": 3,
                   "run_window_max": 20, "utc_day_max": 300,
                   "recent_tokens": [SENTINEL], "history": {SENTINEL: SENTINEL}},
        "window": {"present": True, "deadline_basis": SENTINEL,
                   "started_wall": "2026-11-06T00:00:00+00:00"},
        "p1": {"phase": "p1",
               "model": {"base_url": SENTINEL, "api_key": SENTINEL,
                         "provider": SENTINEL},
               "rounds": [{"wake": {"decision": "CONTINUE", "detail": SENTINEL,
                                    "prompt": SENTINEL}}],
               "reconcile": {"result": "CONFIRMED_SUCCEEDED", "detail": SENTINEL}},
        "material": {"title": SENTINEL, "chars": 12,
                     "sha256": "0" * 64, "artifact_id": "artifact:art-1"},
    }


class SafeOutputTest(unittest.TestCase):
    def test_nested_private_fields_never_reach_the_cli_projection(self):
        projected = g._safe_result(_nested_injected_result())
        blob = json.dumps(projected, ensure_ascii=False, default=str)
        self.assertNotIn(SENTINEL, blob)
        self.assertNotIn("model_params", projected)
        self.assertNotIn("detail", projected)
        self.assertEqual(sorted(projected["ledger"]),
                         ["run_window_calls", "run_window_max", "state",
                          "utc_day_calls", "utc_day_max"])
        self.assertNotIn("recent_tokens", projected["ledger"])
        self.assertNotIn("deadline_basis", projected["window"])
        self.assertNotIn("title", projected["material"])
        self.assertEqual(projected["p1"]["model"]["present"], True)
        self.assertNotIn(SENTINEL, json.dumps(projected["p1"], default=str))

    def test_safe_phase_drops_arbitrary_nested_payloads(self):
        phase = g._safe_phase(_nested_injected_result()["p1"])
        self.assertNotIn(SENTINEL, json.dumps(phase, ensure_ascii=False, default=str))
        self.assertEqual(phase["phase"], "p1")
        self.assertEqual(phase["rounds"][0]["wake"]["decision"], "CONTINUE")

    def test_emit_never_prints_the_nested_sentinel(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = g._emit(g._safe_result(_nested_injected_result()), code=0)
        self.assertEqual(code, 0)
        self.assertNotIn(SENTINEL, buffer.getvalue())

    def test_collection_failure_reports_only_code_and_exception_type(self):
        with mock.patch.object(unittest.TestLoader, "discover",
                               side_effect=RuntimeError(SENTINEL)):
            result = g._run_new_tests()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "TEST_COLLECTION_FAILED")
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertNotIn(SENTINEL, json.dumps(result, default=str))

    def test_main_shell_reports_fixed_code_without_the_message(self):
        def boom(_argv):
            raise RuntimeError(SENTINEL)

        buffer = io.StringIO()
        with mock.patch.dict(g.COMMANDS, {"status": boom}):
            with contextlib.redirect_stdout(buffer):
                code = g.main(["status"])
        self.assertEqual(code, g.EXIT_REFUSED)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["reason_code"], "INTERNAL_ERROR")
        self.assertEqual(payload["exception_type"], "RuntimeError")
        self.assertNotIn(SENTINEL, buffer.getvalue())

    def test_system_exit_passes_through(self):
        def stop(_argv):
            raise SystemExit(3)

        with mock.patch.dict(g.COMMANDS, {"status": stop}):
            with self.assertRaises(SystemExit) as caught:
                g.main(["status"])
        self.assertEqual(caught.exception.code, 3)


class ReadOnlyInspectTest(unittest.TestCase):
    def _scratch(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="g1-integrated-g-test-"))

    def _prepared_root(self, base: Path) -> Path:
        root = base / "instance-root"
        experiment = root / "experiment-g1"
        experiment.mkdir(parents=True)
        state_root = root / "one-day"
        state_root.mkdir()
        profile = g.PROFILE
        g1._write_json(experiment / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": g.BATCH,
            "run_id": profile.run_id, "domain_id": profile.domain_id,
            "root": str(root), "state_root": str(state_root),
            "deadline": profile.deadline.isoformat(),
            "latest_start": profile.latest_start.isoformat(),
            "world_intent_gate": "off", "caps": g._expected_caps(),
        }, private=True)
        g1._write_json(experiment / g1.BATCH_FILENAME, {
            "schema": "g1.batch.v1", "batch": g.BATCH, "run_id": profile.run_id,
            "domain_id": profile.domain_id, "state": "prepared", "root": str(root),
            "state_root": str(state_root),
        }, private=True)
        g1._write_json(state_root / "life_request_budget.json", {
            "schema_version": "life.request_budget.v1", "kind": "life_request_budget",
            "active_day": "2026-11-06", "days": {"2026-11-06": 2},
            "windows": {profile.run_id: {"calls": 2,
                                         "opened_at": "2026-11-06T00:00:00+00:00"}},
            "run_id": profile.run_id, "tokens": [],
            "opened_at": "2026-11-06T00:00:00+00:00",
            "updated_at": "2026-11-06T00:00:00+00:00",
        }, private=True)
        return root

    @staticmethod
    def _snapshot(root: Path) -> tuple[dict, set]:
        files = {}
        dirs = set()
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(root))
            if path.is_dir():
                dirs.add(relative)
            else:
                stat = path.stat()
                files[relative] = (stat.st_size, stat.st_mtime_ns)
        return files, dirs

    def test_missing_root_is_not_created_by_status_or_audit(self):
        base = self._scratch()
        target = base / "missing-root"
        before = sorted(path.name for path in base.iterdir())
        with g1._use_batch(g.BATCH):
            missing = g.status(target, expected_root=target)
            audited = g.audit_readonly(target, expected_root=target)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason_code"], g1.REASON_NOT_PREPARED)
        self.assertFalse(audited["ok"])
        self.assertEqual(audited["reason_code"], g1.REASON_NOT_PREPARED)
        self.assertFalse(target.exists())
        self.assertEqual(sorted(path.name for path in base.iterdir()), before)

    def test_prepared_root_files_and_directories_do_not_change(self):
        base = self._scratch()
        root = self._prepared_root(base)
        files_before, dirs_before = self._snapshot(root)
        parent_before = sorted(path.name for path in base.iterdir())
        with g1._use_batch(g.BATCH):
            first = g.status(root, expected_root=root)
            second = g.audit_readonly(root, expected_root=root)
        files_after, dirs_after = self._snapshot(root)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["ledger"]["state"], "OK")
        self.assertEqual(first["ledger"]["run_window_calls"], 2)
        self.assertEqual(files_after, files_before)
        self.assertEqual(dirs_after, dirs_before)
        self.assertEqual(sorted(path.name for path in base.iterdir()), parent_before)
        self.assertFalse((root / "one-day" / "life_request_budget.lock").exists())

    def test_bad_ledger_reports_unknown_not_zero(self):
        base = self._scratch()
        root = self._prepared_root(base)
        (root / "one-day" / "life_request_budget.json").write_text(
            "{not-json", encoding="utf-8")
        read = g._read_ledger(root / "one-day")
        self.assertEqual(read["state"], "UNKNOWN")
        self.assertIsNone(read["run_window_calls"])
        self.assertIsNone(read["utc_day_calls"])
        self.assertEqual(read["run_window_max"], 20)
        self.assertEqual(read["utc_day_max"], 300)
        with g1._use_batch(g.BATCH):
            status = g.status(root, expected_root=root)
        self.assertEqual(status["ledger"]["state"], "UNKNOWN")
        self.assertIsNone(status["ledger"]["run_window_calls"])


if __name__ == "__main__":
    unittest.main()
