#!/usr/bin/env python3
"""G1 新隔离入口 h 的离线聚合用例。

最多六个聚合 case，只测本入口新增的边界，不重跑 g 的旧套件或旧 G1 19/28/37：

1. h 的封闭身份（root/authority/run/域/history 524/窗口/绝对线/累计门）与 g、a/b/c/f
   的旧默认都不漂；
2. 错 root / 别的批 prereg / 已占用 authority / 未知批身份在写入前失败关闭；
3. 两个独立进程共享 20 本 run 上限、第 21 次在 reserve 前拒，UTC 300 日门独立；
4. P1 失败锁对 P2 有效、坏账 fail-closed，失败后 next send/phase 不增；
5. status/audit 纯读安全投影、坏账 UNKNOWN 不猜 0、12 门缺项负控；
6. 一条真正复用正式双 PID Supply/AG1/AR0 链的脚本 transport 短链。
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
import g1_integrated_h as h  # noqa: E402
import g1_real_adoption as g1  # noqa: E402

SENTINEL = "PRIVATE_SENTINEL_endpoint_key_detail_tokens"


def _scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="g1-integrated-h-test-"))


def _prepared_root(base: Path) -> Path:
    root = base / "instance-root"
    experiment = root / "experiment-g1"
    experiment.mkdir(parents=True)
    state_root = root / "one-day"
    state_root.mkdir()
    profile = h.PROFILE
    g1._write_json(experiment / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": h.BATCH,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(root), "state_root": str(state_root),
        "deadline": profile.deadline.isoformat(),
        "latest_start": profile.latest_start.isoformat(),
        "world_intent_gate": "off", "caps": g._expected_caps(profile),
    }, private=True)
    g1._write_json(experiment / g1.BATCH_FILENAME, {
        "schema": "g1.batch.v1", "batch": h.BATCH, "run_id": profile.run_id,
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


class HIdentityTest(unittest.TestCase):
    def test_h_is_closed_and_g_abc_f_do_not_move(self):
        profile = h.PROFILE
        self.assertEqual(profile.key, "g1-integrated-h")
        self.assertEqual(str(profile.root), "/tmp/nyairo-g1-real-20261106-h")
        self.assertEqual(str(profile.authority_dir),
                         "/tmp/nyairo-g1-real-20261106-h-memory-authority")
        self.assertEqual(str(profile.authority_db),
                         "/tmp/nyairo-g1-real-20261106-h-memory-authority/authority.db")
        self.assertEqual(profile.run_id, "run:g1-real-20261106-h")
        self.assertEqual(profile.domain_id, "g1:real:20261106-h")
        self.assertEqual(profile.expected_history, 524)
        self.assertEqual(profile.window_seconds, 3600)
        self.assertEqual(profile.max_ticks, 1000)
        self.assertEqual(profile.trace_max_records, 10000)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-07T00:50:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-06T23:30:00+08:00")
        self.assertIs(g1._select_batch(h.BATCH), profile)
        # h 走非自然 p1/p2 链：gate 显式 off、60 秒收尾，累计门 1000。
        self.assertEqual(g1._world_intent_gate(profile), "off")
        self.assertFalse(g1._is_natural(profile))
        self.assertEqual(g1._phase_cleanup_seconds(profile), 60.0)
        self.assertFalse(g1._total_accounting_exceeded(0, profile=profile))
        self.assertFalse(g1._total_accounting_exceeded(20, profile=profile))
        self.assertTrue(g1._total_accounting_exceeded(476, profile=profile))
        # g 与 a/b/c/f 的旧默认一个都不动。
        self.assertEqual(g.PROFILE.key, "g1-integrated-g")
        self.assertEqual(g.PROFILE.expected_history, 507)
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertEqual(g1.BATCH_PROFILES["a"].expected_history, 98)
        self.assertEqual(g1.F_PROFILE.expected_history, 168)
        self.assertEqual(g1._profile_window_seconds(g1.F_PROFILE), 1800)
        self.assertEqual(g1._profile_max_ticks(g1.F_PROFILE), 900)
        self.assertFalse(g1._total_accounting_exceeded(900, profile=g1.BATCH_PROFILES["a"]))


class HIdentityRefusalTest(unittest.TestCase):
    def test_wrong_root_foreign_prereg_authority_and_unknown_batch_refuse(self):
        base = _scratch()
        with g1._use_batch(h.BATCH):
            wrong = g1._check_root(g.PROFILE.root, expected=h.PROFILE.root) or {}
            with self.assertRaises(g1.Refused):
                g1._select_batch("g1-integrated-x")
            foreign_root = base / "foreign-prereg"
            g1._write_json(g1._experiment_dir(foreign_root) / g1.PREREG_FILENAME, {
                "schema": "g1.real.prereg.v1", "batch": g.BATCH,
                "run_id": g.PROFILE.run_id, "domain_id": g.PROFILE.domain_id,
                "root": str(foreign_root), "state_root": str(foreign_root / "one-day"),
                "deadline": g.PROFILE.deadline.isoformat(),
                "latest_start": g.PROFILE.latest_start.isoformat(),
            }, private=True)
            foreign = g.audit_readonly(foreign_root, expected_root=foreign_root,
                                       profile=h.PROFILE)
        taken_root = base / "authority-taken" / "instance-root"
        authority = taken_root.parent / ("%s-memory-authority" % taken_root.name)
        authority.mkdir(parents=True, exist_ok=True)
        (authority / "authority.db").write_text("pre-existing authority", encoding="utf-8")
        with g1._use_batch(h.BATCH):
            taken = g1.prepare(taken_root, expected_root=taken_root, offline=True)
        self.assertEqual(wrong.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(foreign.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(taken.get("reason_code"), g1.REASON_AUTHORITY_NOT_FRESH)
        self.assertFalse(taken_root.exists())


class HCapTest(unittest.TestCase):
    def test_shared_twenty_cap_and_separate_utc_day_cap(self):
        base = _scratch()
        state_root = base / "cap-state"
        state_root.mkdir(parents=True)
        with g1._use_batch(h.BATCH):
            cap = g1._cross_pid_cap(state_root, batch=h.BATCH)
            view = g1._budget(state_root).view()
        self.assertEqual(cap["first_rc"], 0)
        self.assertEqual(cap["second_rc"], 0)
        self.assertEqual((cap["first_out"] or {}).get("reserved"), 12)
        self.assertEqual((cap["second_out"] or {}).get("reserved"), 8)
        self.assertNotEqual(cap["third"], "reserved")
        self.assertEqual(cap["calls"], 20)
        self.assertEqual(cap["run_id"], h.PROFILE.run_id)
        # 本 run 的 20 与 UTC 日 300 是两道独立门：用满 20 不会改写 300。
        self.assertEqual(view.get("run_window_calls"), 20)
        self.assertEqual(view.get("run_window_max"), 20)
        self.assertEqual(view.get("utc_day_max"), 300)
        self.assertLess(view.get("utc_day_calls"), 300)


class HFailureLatchTest(unittest.TestCase):
    def test_failure_stop_and_corrupt_ledger_block_the_next_send(self):
        base = _scratch()
        with g1._use_batch(h.BATCH):
            durable = g._cross_phase_stop(base / "cross-phase-stop")
            state = base / "corrupt-ledger"
            state.mkdir(parents=True, exist_ok=True)
            from life_request_budget import budget_path
            budget_path(state).write_text("{not-json", encoding="utf-8")
            budget = g1._budget(state)
            calls = g1._ledger_calls(budget)
            sends: list[int] = []
            latch = g1.SendLatch(lambda: False)
            gate = g1.G1CognitionGate(
                lambda _request, **_kwargs: sends.append(1), latch=latch, budget=budget)
            reason = ""
            try:
                gate.invoke(object())
            except g1.GateClosed as exc:
                reason = exc.reason_code
            pure = g._read_ledger(state, profile=h.PROFILE)
        self.assertEqual(durable["recorded"], g1.REASON_PROVIDER_FAILURE)
        self.assertEqual(durable["stop"], g1.REASON_PROVIDER_FAILURE)
        self.assertTrue(durable["latch_closed"])
        self.assertIsNone(calls)
        self.assertEqual(reason, g1.REASON_LEDGER_UNREADABLE)
        self.assertEqual(sends, [])
        self.assertEqual(pure["state"], "UNKNOWN")
        self.assertIsNone(pure["run_window_calls"])


class HReadOnlyAndGateTest(unittest.TestCase):
    def test_pure_read_status_audit_and_twelve_gate_negatives(self):
        base = _scratch()
        root = _prepared_root(base)
        files_before, dirs_before = _snapshot(root)
        parent_before = sorted(path.name for path in base.iterdir())
        with g1._use_batch(h.BATCH):
            first = g.status(root, expected_root=root, profile=h.PROFILE)
            second = g.audit_readonly(root, expected_root=root, profile=h.PROFILE)
        files_after, dirs_after = _snapshot(root)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["batch"], h.BATCH)
        self.assertEqual(first["run_id"], h.PROFILE.run_id)
        self.assertEqual(first["ledger"]["state"], "OK")
        self.assertEqual(first["ledger"]["run_window_calls"], 2)
        self.assertEqual(files_after, files_before)
        self.assertEqual(dirs_after, dirs_before)
        self.assertEqual(sorted(path.name for path in base.iterdir()), parent_before)
        self.assertFalse((root / "one-day" / "life_request_budget.lock").exists())

        # 坏账 → UNKNOWN/null，不猜 0。
        (root / "one-day" / "life_request_budget.json").write_text(
            "{not-json", encoding="utf-8")
        bad = g._read_ledger(root / "one-day", profile=h.PROFILE)
        self.assertEqual(bad["state"], "UNKNOWN")
        self.assertIsNone(bad["run_window_calls"])
        self.assertIsNone(bad["utc_day_calls"])

        # 终端安全投影：嵌套 detail/model_params/tokens 不得出现。
        injected = {
            "ok": True, "detail": SENTINEL,
            "model_params": {"base_url": SENTINEL, "api_key": SENTINEL},
            "ledger": {"state": "ok", "run_window_calls": 3, "utc_day_calls": 3,
                       "run_window_max": 20, "utc_day_max": 300,
                       "recent_tokens": [SENTINEL]},
            "p1": {"phase": "p1", "model": {"base_url": SENTINEL, "api_key": SENTINEL},
                   "rounds": [{"wake": {"decision": "CONTINUE", "detail": SENTINEL}}]},
        }
        projected = json.dumps(g._safe_result(injected), ensure_ascii=False, default=str)
        self.assertNotIn(SENTINEL, projected)
        self.assertNotIn("model_params", projected)
        self.assertNotIn("detail", projected)

        # 12 门缺项负控：完整样例 COVERED，十个变体全部 NOT_COVERED。
        negatives = g.gate_negatives()
        self.assertEqual(negatives["complete_control"], g.RESULT_COVERED)
        broken = {name: value for name, value in negatives.items()
                  if name != "complete_control"
                  and value != g.RESULT_NOT_COVERED}
        self.assertEqual(broken, {}, broken)
        p1, p2, report = g._complete_fixture()
        p2["unknown_armed_before_first_round"] = False
        projection = g.gate_projection(p1, p2, report)
        self.assertEqual(projection["result"], g.RESULT_NOT_COVERED)
        self.assertIn("unknown_armed_before_first_round", projection["missing"])


class HFormalChainTest(unittest.TestCase):
    def test_formal_dual_pid_chain_is_covered_with_offline_guard(self):
        base = _scratch()
        root = base / "instance-root"
        with g1._use_batch(h.BATCH), g1.offline_clock(h.PROFILE):
            with g1._offline_network_guard(base), g1._http_forbidden():
                prepared = g1.prepare(root, expected_root=root, offline=True)
                self.assertTrue(prepared.get("ok"), prepared.get("reason_code"))
                chain = g1.start_run(root, expected_root=root,
                                     execute_approved=True, offline=True)
                projection = g._projection_for_root(root, chain)
                guard = g1._offline_child_guard_report(root)
                again = g1.start_run(root, expected_root=root,
                                     execute_approved=True, offline=True)
        self.assertTrue(chain.get("ok"), chain.get("stop_reason"))
        self.assertEqual(chain.get("batch"), h.BATCH)
        self.assertEqual(projection["result"], g.RESULT_COVERED, projection)
        self.assertEqual(projection["missing"], [], projection)
        self.assertNotEqual(projection["pids"]["p1"], projection["pids"]["p2"])
        self.assertTrue(guard.get("ok") is True
                        and int(guard.get("found") or 0) >= 2, guard)
        self.assertEqual(again.get("reason_code"), g1.REASON_ALREADY_STARTED)


if __name__ == "__main__":
    unittest.main()
