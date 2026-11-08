#!/usr/bin/env python3
"""G1 f 批（2026-10-31 决策单限定范围）的离线验收。

只测这一批新增的边界，不重跑 a/b/c/d/e 的旧套件：

1. f 的封闭身份与截止、独立 authority、history 168；a/b/c 映射与默认 a 不动；
2. 新入口 ``scripts/g1_next_live.py`` 的**实际子命令**：prepare → run（须
   --execute-approved）→ audit；一条真实 owner 的 p1/p2 链（脚本 transport、HTTP 硬拒）
   给出 A/actA 锚、跨 PID、新 Action、新 owner 版本；错根/错身份/二次 run/fresh 拒绝；
3. 共享 20 请求账本跨 PID：12+8 后第 21 次在 reserve 处拒；坏账本在发送前拒；
   provider 失败关锁并把停因持久化，换进程仍读得到；
4. f 的 World 写面按实例 ``world_home`` 隔离、gate 显式 off、父进程 env 不变、
   repo 默认 World 台账零新增（f 不启动 World 服务，这不是世界动作验收）。
"""

from __future__ import annotations

import json
import os
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

# f's registered deadline is in the past, so the offline harness refuses before doing the
# identity plumbing these cases check.  The expiry is real; no test-side clock override is
# installed here, so these cases remain red until an explicitly offline seam exists.

LIVE = REPO_ROOT / "scripts" / "g1_next_live.py"
TZ8 = timezone(timedelta(hours=8))
F_DEADLINE = datetime(2026, 11, 1, 1, 0, 0, tzinfo=TZ8)
_MAIN: dict | None = None
_PREPARED_ONLY: dict | None = None


def _run_live(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(LIVE), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT),
                          timeout=timeout)


def _json_out(done: subprocess.CompletedProcess) -> dict:
    text = (done.stdout or "").strip()
    if not text:
        return {}
    try:
        return dict(json.loads(text))
    except ValueError:
        return {"_raw": text[:400], "_stderr": (done.stderr or "")[-400:]}


def _repo_default_state() -> dict:
    path = Path(g1._REPO_DEFAULT_LIFECYCLE)
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class FProfileTest(unittest.TestCase):
    def test_f_is_a_closed_identity_and_abc_do_not_move(self):
        f = g1.F_PROFILE
        self.assertEqual(str(f.root), "/root/nyairo-g1-real-20261031-f")
        self.assertEqual(str(f.authority_dir), "/root/nyairo-g1-real-20261031-f-memory-authority")
        self.assertEqual(f.run_id, "run:g1-real-20261031-f")
        self.assertEqual(f.domain_id, "g1:real:20261031-f")
        self.assertEqual(f.expected_history, 168)
        self.assertEqual(f.deadline, F_DEADLINE)
        # a/b/c 的原始映射与默认 a 不动；f 只进运行期表。
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])
        self.assertIs(g1._select_batch("f"), f)
        # 旧 CLI 元组一个值都不动；f 只由 g1_next_live 的 _use_batch 进入。
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))

    def test_wrong_root_is_a_batch_identity_mismatch(self):
        with g1._use_batch("f"):
            refused = g1._check_root(g1.BATCH_PROFILES["a"].root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("root_belongs_to"), "a")
        with g1._use_batch("a"):
            refused = g1._check_root(g1.F_PROFILE.root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("root_belongs_to"), "f")

    def test_world_gate_is_explicit_off_for_f_and_abc_stay_untouched(self):
        self.assertEqual(g1._world_intent_gate(g1.F_PROFILE), "off")
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["a"]))
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["b"]))
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["c"]))
        self.assertEqual(g1._world_intent_gate(g1.D_PROFILE), "off")
        self.assertEqual(g1._world_intent_gate(g1.E_PROFILE), "canonical")

    def test_a_foreign_prereg_is_refused_before_audit_or_run(self):
        root = Path(tempfile.mkdtemp(prefix="g1-live-f-foreign-prereg-"))
        a = g1.BATCH_PROFILES["a"]
        g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": "a", "run_id": a.run_id,
            "domain_id": a.domain_id, "root": str(root),
            "state_root": str(root / "one-day"), "deadline": a.deadline.isoformat()})
        with g1._use_batch("f"):
            refused = g1._verify_instance_identity(root)
            audited = g1.audit(root, expected_root=root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(audited.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)

    def test_an_existing_authority_is_refused_before_prepare_writes(self):
        root = Path(tempfile.mkdtemp(prefix="g1-live-f-fresh-")) / "instance-root"
        authority = root.parent / ("%s-memory-authority" % root.name)
        authority.mkdir(parents=True)
        (authority / "authority.db").write_text("taken", encoding="utf-8")
        with g1._use_batch("f"), g1._http_forbidden():
            result = g1.prepare(root, expected_root=root, offline=True)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), g1.REASON_AUTHORITY_NOT_FRESH)
        self.assertFalse(root.exists(), "prepare must not create the root after refusing")


class FWorldIsolationTest(unittest.TestCase):
    """f 的 World 写面只来自实例 world_home；父 env 与 repo 默认台账都不动。"""

    def test_phase_env_maps_the_instance_world_home_without_touching_parent_env(self):
        root = Path(_MAIN["root"]) if _MAIN and _MAIN.get("root") else None
        if root is None or not root.exists():
            self.skipTest("offline fixture root unavailable")
        # 先让相关模块完成导入，避免把首次 import 的缓存差异当作 env 变化。
        with g1._use_batch("f"):
            g1._phase_child_env(g1.F_PROFILE, root)
            g1._runtime_env(root, offline=True)
        before = dict(os.environ)
        with g1._use_batch("f"):
            child_env = g1._phase_child_env(g1.F_PROFILE, root)
            runtime_env = g1._runtime_env(root, offline=True)
        after = dict(os.environ)
        self.assertEqual(before, after)
        expected_gate = "off"
        for label, env in (("child", child_env), ("runtime", runtime_env)):
            self.assertEqual(env.get("WORLD_BODY_INTENT_MUTATION_GATE"), expected_gate, label)
            home = Path(str(env.get("CHIYO_WORLD_HOME") or ""))
            self.assertTrue(home.is_absolute(), label)
            home.relative_to(root.resolve())
            self.assertEqual(env.get("HERMES_HOME"), str(home), label)
            self.assertEqual(Path(str(env["WORLD_ACTION_AUTH_REGISTRY_DIR"])),
                             home / "run", label)
            self.assertEqual(Path(str(env["WORLD_ACTION_LIFECYCLE_PATH"])),
                             home / "run" / "action_lifecycle.jsonl", label)

    def test_http_guard_really_blocks_network_connects(self):
        fired = ""
        sock = None
        with g1._http_forbidden():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", 9))
            except AssertionError as exc:
                fired = str(exc)
            finally:
                if sock is not None:
                    sock.close()
        self.assertEqual(fired, "OFFLINE_HTTP_FORBIDDEN")


class FLiveOfflineHarnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _MAIN, _PREPARED_ONLY
        if _MAIN is not None:
            return
        scratch = Path(tempfile.mkdtemp(prefix="g1-live-f-"))
        main_root = scratch / "instance-root"

        prepared = _run_live("prepare", "--root", str(main_root), "--offline-fixture")
        ran = _run_live("run", "--root", str(main_root), "--execute-approved",
                        "--offline-fixture")
        audited = _run_live("audit", "--root", str(main_root), "--offline-fixture")

        only_scratch = Path(tempfile.mkdtemp(prefix="g1-live-f-"))
        only_root = only_scratch / "prepared-only"
        only_prepare = _run_live("prepare", "--root", str(only_root), "--offline-fixture")
        only_audit = _run_live("audit", "--root", str(only_root), "--offline-fixture")

        _MAIN = {
            "scratch": str(scratch), "root": str(main_root),
            "prepare": prepared, "run": ran, "audit": audited,
            "prepare_out": _json_out(prepared), "run_out": _json_out(ran),
            "audit_out": _json_out(audited),
            "repo_default_after": _repo_default_state(),
        }
        _PREPARED_ONLY = {
            "root": str(only_root), "prepare": only_prepare, "audit": only_audit,
            "audit_out": _json_out(only_audit),
        }

    def setUp(self):
        if not _MAIN or not _MAIN.get("root"):
            self.skipTest("offline fixture unavailable")
        self.root = Path(_MAIN["root"])

    # -- 实际子命令：prepare / run / audit ------------------------------------------------

    def test_prepare_is_offline_safe_and_records_the_f_identity(self):
        done = _MAIN["prepare"]
        out = _MAIN["prepare_out"]
        self.assertEqual(done.returncode, 0, (done.stderr or "")[-800:])
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("batch"), "f")
        self.assertFalse(out.get("model_called"))
        self.assertFalse(out.get("request_sent"))
        prereg = g1._read_json(g1._experiment_dir(self.root) / g1.PREREG_FILENAME)
        f = g1.F_PROFILE
        self.assertEqual(prereg.get("batch"), "f")
        self.assertEqual(prereg.get("run_id"), f.run_id)
        self.assertEqual(prereg.get("domain_id"), f.domain_id)
        self.assertEqual(prereg.get("deadline"), f.deadline.isoformat())
        self.assertEqual((prereg.get("caps") or {}).get("expected_history"), 168)
        self.assertEqual((prereg.get("caps") or {}).get("request_cap"), 20)
        self.assertEqual(prereg.get("memory_authority"),
                         str(self.root.parent / (self.root.name + "-memory-authority")
                             / "authority.db"))
        self.assertEqual(prereg.get("world_intent_gate"), "off")

    def test_run_needs_execute_approved_and_then_covers_the_limited_anchor(self):
        out = _MAIN["run_out"]
        self.assertEqual(_MAIN["run"].returncode, 0, (_MAIN["run"].stderr or "")[-1200:])
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("batch"), "f")
        verdict = out.get("verdict") or {}
        self.assertEqual(verdict.get("result"), "COVERED_LIMITED", verdict)
        for name, ok in (verdict.get("criteria") or {}).items():
            self.assertTrue(ok, (name, verdict))
        anchor = verdict.get("anchor") or {}
        self.assertTrue(anchor.get("artifact_a"))
        self.assertTrue(anchor.get("activity_ref"))
        self.assertTrue(anchor.get("first_action_id"))
        self.assertTrue(anchor.get("continue_action_id"))
        self.assertNotEqual(anchor.get("continue_action_id"), anchor.get("first_action_id"))
        self.assertNotEqual((verdict.get("pids") or {}).get("p1"),
                            (verdict.get("pids") or {}).get("p2"))
        segments = verdict.get("segments") or {}
        self.assertEqual(segments.get("cross_pid_restart"), "CROSS_PID")
        self.assertEqual(segments.get("interrupt"), "PAUSED_BY_OPERATOR")
        self.assertEqual(segments.get("unknown"), "RECONCILED")
        self.assertEqual(segments.get("version_conflict"), "CAS_REFUSED")
        self.assertEqual(segments.get("no_action"), "NO_ACTION_CHOSEN")
        self.assertEqual(verdict.get("real_g1_evidence"), "NOT_COVERED")
        # 运行本身不是 G1 通过声明。
        self.assertTrue(verdict.get("note"))

    def test_both_phases_carry_f_identity_and_world_off_isolation(self):
        f = g1.F_PROFILE
        for phase in ("p1", "p2"):
            config = g1._read_json(g1._experiment_dir(self.root) / ("g1-config-%s.json" % phase))
            self.assertEqual(config.get("batch"), "f", phase)
            self.assertEqual(config.get("run_id"), f.run_id, phase)
            self.assertEqual(config.get("deadline"), f.deadline.isoformat(), phase)
            report = g1._read_json(g1._experiment_dir(self.root) / (g1.PHASE_FILENAME % phase))
            self.assertEqual(report.get("batch"), "f", phase)
            self.assertEqual(report.get("run_id"), f.run_id, phase)
            self.assertEqual(report.get("world_gate"), "off", phase)
            isolation = report.get("isolation") or {}
            self.assertTrue(isolation.get("world_home_is_instance"), phase)
            self.assertTrue(isolation.get("world_home_is_within_root"), phase)
            self.assertTrue(isolation.get("registry_is_within_root"), phase)
            self.assertTrue(isolation.get("lifecycle_is_within_root"), phase)
        self.assertEqual(_MAIN.get("repo_default_after"), _repo_default_state())

    def test_audit_only_reports_safe_fields_on_the_f_ledger(self):
        out = _MAIN["audit_out"]
        self.assertEqual(_MAIN["audit"].returncode, 0, (_MAIN["audit"].stderr or "")[-800:])
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("batch"), "f")
        self.assertEqual(out.get("run_id"), g1.F_PROFILE.run_id)
        self.assertEqual(out.get("deadline"), g1.F_PROFILE.deadline.isoformat())
        ledger = out.get("ledger") or {}
        self.assertEqual(ledger.get("run_id"), g1.F_PROFILE.run_id)
        self.assertEqual(int(ledger.get("run_window_max") or 0), 20)
        self.assertLessEqual(int(ledger.get("run_window_calls") or 0), 20)
        blob = json.dumps(out, ensure_ascii=False)
        for forbidden in (g1.MATERIAL_TEXT, g1.CONTROL_TEXT, "api_key", "sk-", "prompt"):
            self.assertNotIn(forbidden, blob)

    def test_a_prepared_only_root_audits_as_prepared_with_zero_started_and_ledger(self):
        out = _PREPARED_ONLY["audit_out"]
        self.assertEqual(_PREPARED_ONLY["prepare"].returncode, 0)
        self.assertEqual(_PREPARED_ONLY["audit"].returncode, 0,
                         (_PREPARED_ONLY["audit"].stderr or "")[-800:])
        self.assertEqual(out.get("state"), "prepared")
        self.assertIsNone(out.get("started_at"))
        self.assertEqual(int((out.get("ledger") or {}).get("run_window_calls") or 0), 0)

    def test_a_second_run_on_the_same_root_is_refused(self):
        again = _run_live("run", "--root", str(self.root), "--execute-approved",
                          "--offline-fixture")
        out = _json_out(again)
        self.assertEqual(again.returncode, g1.EXIT_REFUSED)
        self.assertEqual(out.get("reason_code"), g1.REASON_ALREADY_STARTED)

    def test_the_entry_refuses_a_root_outside_the_f_registration(self):
        done = _run_live("prepare", "--root", str(self.root))
        out = _json_out(done)
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(out.get("reason_code"),
                      (g1.REASON_ROOT_NOT_PREREGISTERED, g1.REASON_BATCH_IDENTITY_MISMATCH))


class FLedgerAndStopTest(unittest.TestCase):
    """共享账本与整批停：不依赖上面的长链，单独起小夹具。"""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="g1-live-f-ledger-"))

    def test_two_processes_share_the_20_cap_and_the_21st_is_refused(self):
        state = self.scratch / "cap"
        state.mkdir(parents=True, exist_ok=True)
        with g1._use_batch("f"):
            result = g1._cross_pid_cap(state, batch="f")
        self.assertEqual(result["first_rc"], 0)
        self.assertEqual(result["second_rc"], 0)
        self.assertEqual((result["first_out"] or {}).get("reserved"), 12)
        self.assertEqual((result["second_out"] or {}).get("reserved"), 8)
        self.assertNotEqual(result["third"], "reserved")
        self.assertEqual(result["calls"], 20)
        self.assertEqual(result["run_id"], g1.F_PROFILE.run_id)

    def test_a_corrupt_ledger_is_refused_before_send(self):
        from life_request_budget import budget_path

        state = self.scratch / "corrupt"
        state.mkdir(parents=True, exist_ok=True)
        budget_path(state).write_text("{not-json", encoding="utf-8")
        sends: list[int] = []

        def _send_guarded(budget):
            # 发送口现成契约：先 check，不通过就不调用内层（与 g1_next_offline 同一条）。
            verdict = budget.check()
            if not verdict.get("allowed"):
                raise RuntimeError(str(verdict.get("reason_code") or "BUDGET_REFUSED"))
            budget.reserve("decision")
            sends.append(1)

        with g1._use_batch("f"):
            budget = g1._budget(state)
            with self.assertRaises(RuntimeError) as caught:
                _send_guarded(budget)
        self.assertIn("REQUEST_BUDGET_STATE_CORRUPT", str(caught.exception))
        self.assertEqual(sends, [])

    def test_a_schema_retry_is_never_a_second_attempt(self):
        class _Inner:
            def invoke(self, request, *, schema_retry_index: int = 0):
                raise AssertionError("inner must not be called")

        with g1._use_batch("f"):
            gate = g1.G1CognitionGate(_Inner(), latch=g1.SendLatch(lambda: False),
                                      budget=g1._budget(self.scratch / "empty"))
            with self.assertRaises(g1.GateClosed) as caught:
                gate.invoke(object(), schema_retry_index=1)
        self.assertEqual(caught.exception.reason_code, "MODEL_ATTEMPT_RETRY_REFUSED")

    def test_a_provider_failure_closes_the_latch_and_persists_the_stop(self):
        stop_root = self.scratch / "stop"
        latch = g1.SendLatch(
            lambda: False,
            on_stop=lambda code: g1._record_batch_stop(stop_root, code))

        class _Boom:
            def invoke(self, request, *, schema_retry_index: int = 0):
                raise RuntimeError("boom")

        with g1._use_batch("f"):
            gate = g1.G1CognitionGate(_Boom(), latch=latch,
                                      budget=g1._budget(self.scratch / "empty"))
            with self.assertRaises(RuntimeError):
                gate.invoke(object())
            self.assertTrue(latch.closed)
            self.assertEqual(latch.stop_reason, g1.REASON_PROVIDER_FAILURE)
            self.assertEqual(g1._durable_stop(stop_root), g1.REASON_PROVIDER_FAILURE)
            # 新进程/新锁在发送前读到同一个持久停因，不能因为换了进程就复活。
            fresh = g1.SendLatch(lambda: False)
            self.assertEqual(g1._loop_stop(stop_root, g1._budget(self.scratch / "empty"), fresh),
                             g1.REASON_PROVIDER_FAILURE)
            self.assertTrue(fresh.closed)


if __name__ == "__main__":
    unittest.main()
