#!/usr/bin/env python3
"""G1 第三批 c 的快用例：封闭身份、按 profile 取到的截止，以及一次带观测的离线 harness。

本文件只测这批新增的边界，不重跑 a/b 的历史 offline-check，也不重跑
``tests/test_g1_candidate_trace.py``：

1. c 的固定身份（root/run/域/history 121/独立 authority）与 a/b 原有映射不漂移；
2. 运行时截止从**所选批次**取：a/b 仍是 2026-10-30 17:00 CST，c 是 2026-10-31 01:00 CST，
   窗口/deadline_basis/prereg/config/audit 全用同一个值；模块级 ``DEADLINE`` 仍是 a；
3. 错批 root、错批 prereg、缺/错截止的 prereg、预存 authority、重复 run 全部失败关闭；
4. c 的离线 harness（真 owner、正式接线、明确脚本 transport、HTTP 硬拒）能跑通，
   父子身份、账本 20 次上限与默认关闭的观测都落在 c 自己的根里；
5. 显式 ``--capture-candidates`` 时 p1/p2 都落阶段文件，``POST_TICK_SNAPSHOT`` 明确是
   tick 之后的只读旁证（不是发送前快照），观测文件不含材料正文。
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

# c 的固定截止已过真实墙钟。离线夹具用 ``g1.offline_clock()`` 显式进入一个参考时刻
# （本批截止前 1 小时），只在本进程内存与 mkdtemp 根内生效；不写环境变量、不写任何
# 持久字段，profile 常量、绝对截止与真实 run 语义都不动。真实根带这个参考时刻会在
# 写入之前被拒。

TZ8 = timezone(timedelta(hours=8))
DAY_DEADLINE = datetime(2026, 10, 30, 17, 0, 0, tzinfo=TZ8)
C_DEADLINE = datetime(2026, 10, 31, 1, 0, 0, tzinfo=TZ8)

_HARNESS: dict | None = None


def _rows(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _config(root: Path, phase: str) -> dict:
    # 与 ``_run_phase_process`` 写下的文件名一致；这里只读，不重建。
    return g1._read_json(g1._experiment_dir(root) / ("g1-config-%s.json" % phase))


def _phase_report(root: Path, phase: str) -> dict:
    return g1._read_json(g1._experiment_dir(root) / (g1.PHASE_FILENAME % phase))


def _trace_path(root: Path, phase: str) -> Path:
    return g1._experiment_dir(root) / (g1.CANDIDATE_TRACE_FILENAME % phase)


class BatchCProfileTest(unittest.TestCase):
    """c 是第三个封闭身份：只有显式 ``--batch c`` 才生效，a/b 一个字段都不动。"""

    def test_c_is_the_closed_third_identity(self):
        c = g1.BATCH_PROFILES["c"]
        self.assertEqual(str(c.root), "/root/nyairo-g1-real-20261030-c")
        self.assertEqual(str(c.authority_dir), "/root/nyairo-g1-real-20261030-c-memory-authority")
        self.assertEqual(c.run_id, "run:g1-real-20261030-c")
        self.assertEqual(c.domain_id, "g1:real:20261030-c")
        self.assertEqual(c.expected_history, 121)
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        # 缺省仍是 a：CLI 不给 --batch 时落回同一份映射。
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])
        self.assertIs(g1._select_batch("c"), c)

    def test_the_runtime_deadline_comes_from_the_selected_profile(self):
        # a/b 的历史截止原样保留；模块级兼容常量仍是 a，不被 c 的 01:00 改掉。
        self.assertEqual(g1.BATCH_PROFILES["a"].deadline, DAY_DEADLINE)
        self.assertEqual(g1.BATCH_PROFILES["b"].deadline, DAY_DEADLINE)
        self.assertEqual(g1.BATCH_PROFILES["c"].deadline, C_DEADLINE)
        self.assertEqual(g1.DEADLINE, g1.BATCH_PROFILES["a"].deadline)
        self.assertEqual(g1.DEADLINE.isoformat(), "2026-10-30T17:00:00+08:00")

        # 窗口写下去的是「now+window」和**本批**截止两者的较早者：
        # 给一个明显更长的窗口，落盘值就等于该批自己的截止。
        for key, expected in (("a", DAY_DEADLINE), ("b", DAY_DEADLINE),
                              ("c", C_DEADLINE)):
            root = Path(tempfile.mkdtemp(prefix="g1-batch-%s-window-" % key))
            with g1._use_batch(key):
                window = g1._write_window(root, window_seconds=10 ** 7)
            self.assertEqual(window["run_id"], g1.BATCH_PROFILES[key].run_id, key)
            self.assertEqual(datetime.fromisoformat(str(window["deadline_wall"])),
                             expected, key)
            self.assertIn(expected.isoformat(), str(window["deadline_basis"]), key)

    def test_the_c_root_is_refused_by_a_and_b_and_theirs_by_c(self):
        c = g1.BATCH_PROFILES["c"]
        with g1._use_batch("c"):
            self.assertIsNone(g1._check_root(c.root))
            for key in ("a", "b"):
                refused = g1._check_root(g1.BATCH_PROFILES[key].root)
                self.assertEqual(refused.get("reason_code"),
                                 g1.REASON_BATCH_IDENTITY_MISMATCH, key)
                self.assertEqual(refused.get("root_belongs_to"), key)
        for key in ("a", "b"):
            with g1._use_batch(key):
                refused = g1._check_root(c.root)
                self.assertEqual(refused.get("reason_code"),
                                 g1.REASON_BATCH_IDENTITY_MISMATCH, key)
                self.assertEqual(refused.get("root_belongs_to"), "c", key)

    def test_a_prereg_with_a_foreign_or_missing_deadline_is_refused(self):
        c = g1.BATCH_PROFILES["c"]
        base = {"schema": "g1.real.prereg.v1", "batch": "c", "run_id": c.run_id,
                "domain_id": c.domain_id}
        for label, deadline in (("foreign", DAY_DEADLINE.isoformat()),
                                ("missing", None)):
            root = Path(tempfile.mkdtemp(prefix="g1-batch-c-prereg-%s-" % label))
            row = dict(base, root=str(root), state_root=str(root / "one-day"))
            if deadline is not None:
                row["deadline"] = deadline
            g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, row)
            with g1._use_batch("c"):
                refused = g1._verify_instance_identity(root)
            self.assertIsNotNone(refused, label)
            self.assertEqual(refused.get("reason_code"),
                             g1.REASON_BATCH_IDENTITY_MISMATCH, label)
            self.assertEqual(refused.get("field"), "deadline", label)
        # a 的旧记录（17:00）在 a 批仍然被认，不被 c 的截止顺手改掉。
        a = g1.BATCH_PROFILES["a"]
        root = Path(tempfile.mkdtemp(prefix="g1-batch-a-prereg-"))
        g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": "a", "run_id": a.run_id,
            "domain_id": a.domain_id, "root": str(root),
            "state_root": str(root / "one-day"), "deadline": a.deadline.isoformat()})
        with g1._use_batch("a"):
            self.assertIsNone(g1._verify_instance_identity(root))

    def test_a_child_phase_refuses_another_batch_or_deadline_in_its_config(self):
        c = g1.BATCH_PROFILES["c"]
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-phase-"))
        base = {"batch": "c", "run_id": c.run_id, "domain_id": c.domain_id,
                "root": str(root), "deadline": c.deadline.isoformat()}
        cases = {
            "foreign_deadline": dict(base, deadline=DAY_DEADLINE.isoformat()),
            "foreign_run": dict(base, run_id=g1.BATCH_PROFILES["a"].run_id),
            "missing_deadline": {key: value for key, value in base.items()
                                 if key != "deadline"},
        }
        for label, payload in cases.items():
            config = root / ("cfg-%s.json" % label)
            g1._write_json(config, payload)
            done = subprocess.run(
                [sys.executable, "-B", str(Path(g1.__file__)), "_phase",
                 "--phase", "p1", "--config", str(config)],
                capture_output=True, text=True)
            self.assertEqual(done.returncode, g1.EXIT_REFUSED, label)
            self.assertIn(g1.REASON_BATCH_IDENTITY_MISMATCH, done.stderr, label)

    def test_a_run_after_the_profile_deadline_never_writes_a_window(self):
        # 把 c 的截止临时挪到过去：prepare 记录的就是这个截止，run 必须当场拒绝，
        # 且不写窗口/started。证明开跑前门读的是 profile，不是模块常量。
        late = dataclasses.replace(g1.BATCH_PROFILES["c"],
                                   deadline=datetime.now(timezone.utc)
                                   - timedelta(seconds=5))
        saved = g1.BATCH_PROFILES["c"]
        saved_mode = g1.MODE_PROFILES["c"]
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-late-")) / "late-root"
        g1.BATCH_PROFILES["c"] = late
        # Runtime identity resolves through MODE_PROFILES, so both maps must carry the
        # fixture deadline or start_run would still read the registered value.
        g1.MODE_PROFILES["c"] = late
        try:
            with g1._use_batch("c"), g1._http_forbidden():
                prepared = g1.prepare(root, expected_root=root, offline=True)
                self.assertTrue(prepared.get("ok"), prepared)
                refused = g1.start_run(root, expected_root=root,
                                       execute_approved=True, offline=True)
        finally:
            g1.BATCH_PROFILES["c"] = saved
            g1.MODE_PROFILES["c"] = saved_mode
        self.assertEqual(refused.get("reason_code"), g1.REASON_DEADLINE_PASSED)
        self.assertEqual(refused.get("deadline"), late.deadline.isoformat())
        self.assertFalse(g1._window_path(root).exists())
        batch = g1._read_json(g1._experiment_dir(root) / g1.BATCH_FILENAME)
        self.assertFalse((batch.get("run") or {}).get("started_at"))


class BatchCOfflineHarnessTest(unittest.TestCase):
    """c 自己的离线 harness：真 owner、正式接线、脚本 transport、HTTP 硬拒。

    走的是真正的 CLI 子进程（和父级验收同一条入口），不是在本进程里调用
    ``offline_check()``：后者会让 ``tests/test_g1_real_adoption.py`` 共享当前批次上下文，
    而那份用例固定以缺省 a 为前提。
    """

    @classmethod
    def setUpClass(cls):
        global _HARNESS
        if _HARNESS is None:
            done = subprocess.run(
                [sys.executable, "-B", str(Path(g1.__file__)),
                 "offline-check", "--batch", "c"],
                capture_output=True, text=True, cwd=str(REPO_ROOT))
            scratch = ""
            for line in done.stdout.splitlines():
                if line.startswith("scratch root:"):
                    scratch = line.split(":", 1)[1].strip()
            _HARNESS = {"returncode": done.returncode, "stdout": done.stdout,
                        "stderr": done.stderr, "scratch_root": scratch}
        cls.result = _HARNESS
        cls.c = g1.BATCH_PROFILES["c"]

    def setUp(self):
        if not self.result.get("scratch_root"):
            self.skipTest("offline harness produced no scratch root")
        self.root = Path(self.result["scratch_root"]) / "instance-root"

    def test_the_c_offline_harness_passes_every_check(self):
        self.assertEqual(self.result.get("returncode"), 0,
                         self.result.get("stderr", "")[-800:])
        self.assertIn("RESULT: PASS", self.result.get("stdout", ""))
        self.assertNotIn("FAILED:", self.result.get("stdout", ""))

    def test_prepare_and_both_phases_carry_the_c_identity_and_deadline(self):
        c = self.c
        prereg = g1._read_json(g1._experiment_dir(self.root) / g1.PREREG_FILENAME)
        self.assertEqual(prereg.get("batch"), "c")
        self.assertEqual(prereg.get("run_id"), c.run_id)
        self.assertEqual(prereg.get("domain_id"), c.domain_id)
        self.assertEqual(prereg.get("root"), str(self.root))
        self.assertEqual(prereg.get("deadline"), c.deadline.isoformat())
        self.assertEqual(prereg.get("memory_authority"),
                         str(self.root.parent / (self.root.name + "-memory-authority")
                             / "authority.db"))
        self.assertEqual((prereg.get("caps") or {}).get("request_cap"), 20)
        self.assertEqual((prereg.get("caps") or {}).get("window_seconds"), 1800)
        self.assertEqual((prereg.get("caps") or {}).get("expected_history"), 121)
        for phase in ("p1", "p2"):
            config = _config(self.root, phase)
            self.assertEqual(config.get("batch"), "c", phase)
            self.assertEqual(config.get("run_id"), c.run_id, phase)
            self.assertEqual(config.get("domain_id"), c.domain_id, phase)
            self.assertEqual(config.get("deadline"), c.deadline.isoformat(), phase)
            # 默认关闭：开关不传播成 true，也不建观测文件。
            self.assertFalse(config.get("capture_candidates"), phase)
            report = _phase_report(self.root, phase)
            self.assertEqual(report.get("batch"), "c", phase)
            self.assertEqual(report.get("run_id"), c.run_id, phase)
            self.assertFalse((_trace_path(self.root, phase)).exists(), phase)

    def test_the_shared_ledger_and_audit_stay_on_the_c_identity(self):
        c = self.c
        with g1._use_batch("c"), g1.offline_clock():
            audited = g1.audit(self.root, expected_root=self.root)
            # 同一根上第二次 run 必须被 started 挡住，且不写任何新东西。
            again = g1.start_run(self.root, expected_root=self.root,
                                 execute_approved=True, offline=True)
        self.assertTrue(audited.get("ok"), audited)
        self.assertEqual(audited.get("batch"), "c")
        self.assertEqual(audited.get("run_id"), c.run_id)
        self.assertEqual(audited.get("domain_id"), c.domain_id)
        self.assertEqual(audited.get("deadline"), c.deadline.isoformat())
        ledger = audited.get("ledger") or {}
        self.assertEqual(ledger.get("run_id"), c.run_id)
        self.assertEqual(int(ledger.get("run_window_max") or 0), 20)
        self.assertLessEqual(int(ledger.get("run_window_calls") or 0), 20)
        window = audited.get("window") or {}
        self.assertEqual(window.get("run_id"), c.run_id)
        self.assertIn(c.deadline.isoformat(), str(window.get("deadline_basis") or ""))
        self.assertEqual(again.get("reason_code"), g1.REASON_ALREADY_STARTED)


class BatchCCaptureTest(unittest.TestCase):
    """显式开启观测：p1/p2 都落阶段文件，POST_TICK 旁证与脱敏边界保持。"""

    def test_a_c_run_with_capture_on_records_staged_post_tick_trace(self):
        scratch = Path(tempfile.mkdtemp(prefix="g1-batch-c-capture-"))
        root = scratch / "capture-on"
        c = g1.BATCH_PROFILES["c"]
        with g1._use_batch("c"), g1._http_forbidden(), g1.offline_clock():
            prepared = g1.prepare(root, expected_root=root, offline=True)
            self.assertTrue(prepared.get("ok"), prepared)
            result = g1.start_run(root, expected_root=root, execute_approved=True,
                                  offline=True, capture_candidates=True)
        self.assertTrue(result.get("ok"), result.get("stop_reason"))
        self.assertEqual(result.get("run_id"), c.run_id)
        self.assertTrue((result.get("candidate_trace") or {}).get("enabled"))
        for phase in ("p1", "p2"):
            config = _config(root, phase)
            self.assertTrue(config.get("capture_candidates"), phase)
            self.assertEqual(config.get("deadline"), c.deadline.isoformat(), phase)
            trace_view = (result.get(phase) or {}).get("candidate_trace") or {}
            self.assertTrue(trace_view.get("enabled"), phase)
            self.assertIsNone(trace_view.get("error"), phase)
            path = _trace_path(root, phase)
            self.assertTrue(str(path).startswith(str(g1._experiment_dir(root))), phase)
            rows = _rows(path)
            self.assertTrue(rows, phase)
            stages = {row["stage"] for row in rows}
            self.assertIn(g1.STAGE_SNAPSHOT, stages, phase)
            self.assertIn(g1.STAGE_ROUND, stages, phase)
            self.assertIn(g1.STAGE_CANDIDATE_SET, stages, phase)
            self.assertTrue({g1.STAGE_REQUEST, g1.STAGE_PROMPT} & stages,
                            (phase, stages))
            snapshots = [row for row in rows if row["stage"] == g1.STAGE_SNAPSHOT]
            self.assertTrue(snapshots, phase)
            for row in snapshots:
                self.assertEqual(row.get("snapshot_time"), "POST_TICK_READ", phase)
                self.assertTrue(row.get("not_pre_send_atomic"), phase)
            # 观测副本不得含材料正文或 prompt/completion 字段。
            blob = json.dumps(rows, ensure_ascii=False)
            self.assertNotIn(g1.MATERIAL_TEXT, blob, phase)
            self.assertNotIn(g1.CONTROL_TEXT, blob, phase)
            for row in rows:
                self.assertNotIn("prompt", row, phase)
                self.assertNotIn("completion", row, phase)


class OfflineClockGuardTest(unittest.TestCase):
    """离线参考时刻只能落在 scratch 根：真实根/正式进程/无显式会话三条负控。"""

    def test_a_real_root_cannot_borrow_the_offline_clock(self):
        c = g1.BATCH_PROFILES["c"]
        with g1.offline_clock(c):
            refused = g1._offline_clock_refusal(c.root)
            # 显式声明 offline=True 也不许把参考钟借给正式根。
            refused_explicit = g1._offline_clock_refusal(c.root, offline=True)
            # 窗口守卫是同一道最内纯判据：拿一个非 scratch 的空路径证明它在写入前
            # 抛 Refused，不读写任何正式根，也不调用 prepare/start_run 这类正式入口。
            with self.assertRaises(g1.Refused) as caught:
                g1._write_window(REPO_ROOT / "offline-clock-window-probe")
        self.assertEqual(refused.get("reason_code"), g1.REASON_OFFLINE_CLOCK_REFUSED)
        self.assertEqual(refused_explicit.get("reason_code"), g1.REASON_OFFLINE_CLOCK_REFUSED)
        self.assertEqual(caught.exception.reason_code, g1.REASON_OFFLINE_CLOCK_REFUSED)
        self.assertFalse((REPO_ROOT / "offline-clock-window-probe").exists())

    def test_an_explicit_offline_false_call_is_refused_before_any_write_or_model_load(self):
        # 参考会话里再传 offline=False：不许静默退回真实墙钟/加载真实模型配置，
        # 必须在写窗口、解析模型配置、起服务之前就以同一理由拒绝。
        c = g1.BATCH_PROFILES["c"]
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-nonoffline-")) / "root"
        hits: list[str] = []

        def _boom(name):
            def _inner(*_args, **_kwargs):
                hits.append(name)
                raise AssertionError("must refuse before %s" % name)
            return _inner

        saved = (g1._write_json, g1._verify_model_config, g1._runtime_env)
        g1._write_json = _boom("_write_json")
        g1._verify_model_config = _boom("_verify_model_config")
        g1._runtime_env = _boom("_runtime_env")
        try:
            with g1._use_batch("c"), g1.offline_clock(c):
                prepared = g1.prepare(root, expected_root=root, offline=False)
                refused = g1.start_run(root, expected_root=root, execute_approved=True,
                                       offline=False)
        finally:
            g1._write_json, g1._verify_model_config, g1._runtime_env = saved
        for result in (prepared, refused):
            self.assertFalse(result.get("ok"), result)
            self.assertEqual(result.get("reason_code"), g1.REASON_OFFLINE_CLOCK_REFUSED)
        self.assertEqual(hits, [])
        self.assertFalse(root.exists())
        self.assertFalse(g1._window_path(root).exists())

    def test_a_non_offline_child_env_never_carries_the_session_marker(self):
        # 只有「本次实际离线调用 + 父进程确实在参考会话里」才给子进程补标记；
        # offline=False、或没有会话时，子进程 env 里都不许出现这个标记。
        c = g1.BATCH_PROFILES["c"]
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-env-"))
        with g1.offline_clock(c):
            non_offline = g1._phase_child_env(c, root, offline=False)
            offline_env = g1._phase_child_env(c, root, offline=True)
        without_session = g1._phase_child_env(c, root, offline=True)
        self.assertNotIn(g1.OFFLINE_FIXTURE_ENV, non_offline)
        self.assertEqual(offline_env.get(g1.OFFLINE_FIXTURE_ENV), "1")
        self.assertNotIn(g1.OFFLINE_FIXTURE_ENV, without_session)

    def test_a_scratch_root_uses_the_real_clock_without_an_explicit_session(self):
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-scratch-"))
        # 没有显式进入离线夹具：本进程仍是真实墙钟，scratch 根不被拒。
        self.assertIsNone(g1._offline_clock_refusal(root))

    def test_a_scratch_root_accepts_the_reference_inside_an_explicit_session(self):
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-scratch-"))
        with g1.offline_clock(g1.BATCH_PROFILES["c"]):
            self.assertIsNone(g1._offline_clock_refusal(root))

    def test_a_scratch_window_is_expired_for_a_real_process_and_alive_only_in_a_session(self):
        # 窗口的墙钟截止落在离线参考时刻之后、真实现在之前。落盘里没有任何"我是离线"
        # 标记：正式进程（没有显式会话）必须读成已到期；只有显式进入会话才按参考时刻判。
        c = g1.BATCH_PROFILES["c"]
        reference = g1.offline_reference(c)
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-expired-"))
        g1._write_json(g1._window_path(root), {
            "schema": "g1.window.v1", "run_id": c.run_id,
            "window_seconds": 1800,
            "started_monotonic": time.monotonic(),
            "deadline_monotonic": time.monotonic() + 1800,
            "started_wall": reference.isoformat(),
            "deadline_wall": (reference + timedelta(seconds=1800)).isoformat()})
        self.assertTrue(g1._deadline_passed(root))
        with g1.offline_clock(c):
            self.assertFalse(g1._deadline_passed(root))
        # 会话退出后仍按真实墙钟判：参考时刻没有渗到会话外面。
        self.assertTrue(g1._deadline_passed(root))

    def test_without_the_marker_the_real_deadline_still_refuses(self):
        root = Path(tempfile.mkdtemp(prefix="g1-batch-c-noclock-")) / "root"
        with g1._use_batch("c"), g1._http_forbidden():
            prepared = g1.prepare(root, expected_root=root, offline=True)
            self.assertTrue(prepared.get("ok"), prepared)
            refused = g1.start_run(root, expected_root=root, execute_approved=True,
                                   offline=True)
        self.assertEqual(refused.get("reason_code"), g1.REASON_DEADLINE_PASSED)
        self.assertFalse(g1._window_path(root).exists())


if __name__ == "__main__":
    unittest.main()
