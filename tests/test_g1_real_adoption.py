#!/usr/bin/env python3
"""``scripts/g1_real_adoption.py`` 的快用例：只测纯函数与本机小门，不跑真实链。

完整的 owner/runtime 链在 ``offline-check`` 自己里跑（真实 owner + 明确替身 transport），
这里只保证：状态机不会替模型改决定、证据不对就不推进、截止/异常门 fail-closed、
共享账本真的到 20 就拒、落盘的停因能跨进程挡住下一次发送。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

#: 一次「真的续了同一件作品」的本轮证据。
CONTINUE_EVIDENCE = dict(activity_matches=True, same_work=True, round_has_receipt=True)
#: 一次真的 CAS：注入的并发写落 owner、head 推进，原提交权威原因是 CONFLICT。
CAS_OK = {"injected": True, "applied": True, "head_advanced": True, "owner_conflict": True}


class StateMachineTest(unittest.TestCase):
    def _injector(self, **kwargs):
        base = {"arm_unknown": False, "arm_concurrent": False,
                "unknown_fired": 0, "concurrent_fired": 0}
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_a_non_target_decision_is_never_rewritten(self):
        injector = self._injector()
        after = g1._apply_round(
            g1.STATE_AWAIT_CONTINUE,
            {"llm_calls": 1, "decision": "WAIT", "reason_codes": ["WAIT_FOR_QUIET"]},
            versions_before=3, versions_after=3, injector=injector)
        self.assertEqual(after, g1.STATE_AWAIT_CONTINUE)
        self.assertFalse(injector.arm_unknown)

    def test_a_continue_write_arms_the_single_unknown_injection(self):
        injector = self._injector()
        after = g1._apply_round(
            g1.STATE_AWAIT_CONTINUE,
            {"llm_calls": 1, "decision": "CONTINUE", "activity_ref": "activity:one"},
            versions_before=1, versions_after=2, injector=injector, **CONTINUE_EVIDENCE)
        self.assertEqual(after, g1.STATE_AWAIT_UNKNOWN)
        self.assertTrue(injector.arm_unknown)

    def test_a_write_on_another_activity_or_work_never_advances(self):
        # 同一件作品、但 Activity 不是 P1 的那个：不推进。
        self.assertEqual(
            g1._apply_round(
                g1.STATE_AWAIT_CONTINUE,
                {"llm_calls": 1, "decision": "CONTINUE", "action_status": "SUCCEEDED"},
                versions_before=1, versions_after=2, injector=self._injector(),
                activity_matches=False, same_work=True, round_has_receipt=True),
            g1.STATE_AWAIT_CONTINUE)
        # 同一个 Activity、但写的是另一件作品：也不推进。
        self.assertEqual(
            g1._apply_round(
                g1.STATE_AWAIT_CONTINUE,
                {"llm_calls": 1, "decision": "CONTINUE", "action_status": "SUCCEEDED"},
                versions_before=1, versions_after=2, injector=self._injector(),
                activity_matches=True, same_work=False, round_has_receipt=True),
            g1.STATE_AWAIT_CONTINUE)
        # 没有本轮 receipt 的「写入」不能证明指向 A。
        self.assertEqual(
            g1._apply_round(
                g1.STATE_AWAIT_CONTINUE,
                {"llm_calls": 1, "decision": "CONTINUE", "action_status": "SUCCEEDED"},
                versions_before=1, versions_after=2, injector=self._injector(),
                activity_matches=True, same_work=True, round_has_receipt=False),
            g1.STATE_AWAIT_CONTINUE)

    def test_only_an_unknown_action_moves_to_cas(self):
        injector = self._injector()
        stays = g1._apply_round(
            g1.STATE_AWAIT_UNKNOWN,
            {"llm_calls": 1, "decision": "CONTINUE", "action_status": "SUCCEEDED"},
            versions_before=2, versions_after=3, injector=injector, **CONTINUE_EVIDENCE)
        self.assertEqual(stays, g1.STATE_AWAIT_UNKNOWN)
        moved = g1._apply_round(
            g1.STATE_AWAIT_UNKNOWN,
            {"llm_calls": 1, "decision": "CONTINUE", "action_status": "UNKNOWN"},
            versions_before=2, versions_after=3, injector=self._injector(unknown_fired=1),
            reconcile_ok=True, **CONTINUE_EVIDENCE)
        self.assertEqual(moved, g1.STATE_AWAIT_CAS)

    def test_an_unreconciled_unknown_never_counts_as_reconciled(self):
        stays = g1._apply_round(
            g1.STATE_AWAIT_UNKNOWN,
            {"llm_calls": 1, "decision": "CONTINUE", "action_status": "UNKNOWN"},
            versions_before=2, versions_after=2,
            injector=self._injector(unknown_fired=1), reconcile_ok=False,
            **CONTINUE_EVIDENCE)
        self.assertEqual(stays, g1.STATE_AWAIT_UNKNOWN)
        coverage = g1._coverage_from_states(
            p1_write=True, p1_activity=True, pause_written=True, paused_zero=True,
            states=[g1.STATE_AWAIT_UNKNOWN], final_state=g1.STATE_AWAIT_UNKNOWN,
            unknown_reconciled=False, cas_refused=False)
        self.assertFalse(coverage["unknown_reconciled"])
        self.assertFalse(coverage["g1_chain_complete"])

    def test_cas_needs_the_injected_conflict_and_a_failed_action(self):
        wake = {"llm_calls": 1, "decision": "CONTINUE", "action_status": "FAILED"}
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_CAS, wake, versions_before=3, versions_after=3,
                            injector=self._injector(concurrent_fired=0), **CONTINUE_EVIDENCE),
            g1.STATE_AWAIT_CAS)
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_CAS, wake, versions_before=3, versions_after=3,
                            injector=self._injector(concurrent_fired=1), cas_evidence=CAS_OK,
                            **CONTINUE_EVIDENCE),
            g1.STATE_AWAIT_NO_ACTION)
        # 并发写没落 owner、或原 Action 不是 expected_head 冲突：都不算 CAS。
        for evidence in ({"injected": True, "applied": False, "head_advanced": False,
                          "owner_conflict": True},
                         {"injected": True, "applied": True, "head_advanced": True,
                          "owner_conflict": False}):
            self.assertEqual(
                g1._apply_round(g1.STATE_AWAIT_CAS, wake, versions_before=3, versions_after=3,
                                injector=self._injector(concurrent_fired=1),
                                cas_evidence=evidence, **CONTINUE_EVIDENCE),
                g1.STATE_AWAIT_CAS)
        # 即便 CAS 证据齐全，只要不是这件作品/这个 Activity，也不算。
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_CAS, wake, versions_before=3, versions_after=3,
                            injector=self._injector(concurrent_fired=1), cas_evidence=CAS_OK,
                            activity_matches=False, same_work=True, round_has_receipt=True),
            g1.STATE_AWAIT_CAS)

    def test_only_a_real_no_action_with_no_write_counts(self):
        injector = self._injector()
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_NO_ACTION,
                            {"llm_calls": 1, "decision": "NO_ACTION", "outcome": "NO_ACTION"},
                            versions_before=3, versions_after=3, injector=injector),
            g1.STATE_AWAIT_CONTINUE_AFTER_QUIET)
        # 零请求的 QUIET 不是模型选择。
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_NO_ACTION,
                            {"llm_calls": 0, "decision": None, "status": "QUIET"},
                            versions_before=3, versions_after=3, injector=injector),
            g1.STATE_AWAIT_NO_ACTION)
        # 只有枚举没有真实 outcome 的也不算。
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_NO_ACTION,
                            {"llm_calls": 1, "decision": "NO_ACTION"},
                            versions_before=3, versions_after=3, injector=injector),
            g1.STATE_AWAIT_NO_ACTION)
        # 带写入的 NO_ACTION 也不算。
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_NO_ACTION,
                            {"llm_calls": 1, "decision": "NO_ACTION", "outcome": "NO_ACTION"},
                            versions_before=3, versions_after=4, injector=injector),
            g1.STATE_AWAIT_NO_ACTION)
        # 带了附带 receipt 的 NO_ACTION 也不算。
        self.assertEqual(
            g1._apply_round(g1.STATE_AWAIT_NO_ACTION,
                            {"llm_calls": 1, "decision": "NO_ACTION", "outcome": "NO_ACTION"},
                            versions_before=3, versions_after=3, injector=injector,
                            round_has_receipt=True),
            g1.STATE_AWAIT_NO_ACTION)

    def test_the_final_continue_completes_the_chain(self):
        finished = g1._apply_round(
            g1.STATE_AWAIT_CONTINUE_AFTER_QUIET,
            {"llm_calls": 1, "decision": "CONTINUE"},
            versions_before=4, versions_after=5, injector=self._injector(),
            **CONTINUE_EVIDENCE)
        self.assertEqual(finished, g1.STATE_COVERED)
        coverage = g1._coverage_from_states(
            p1_write=True, p1_activity=True, pause_written=True, paused_zero=True,
            states=[g1.STATE_AWAIT_UNKNOWN, g1.STATE_AWAIT_CAS, g1.STATE_AWAIT_NO_ACTION,
                    g1.STATE_AWAIT_CONTINUE_AFTER_QUIET, g1.STATE_COVERED],
            final_state=finished, unknown_reconciled=True, cas_refused=True)
        self.assertTrue(coverage["g1_chain_complete"])
        self.assertEqual(coverage["not_covered"], [])


class GateTest(unittest.TestCase):
    def _window(self, root: Path, *, offset_seconds: float) -> None:
        now = datetime.now(timezone.utc)
        mono = time.monotonic()
        g1._write_json(g1._window_path(root), {
            "schema": "g1.window.v1", "run_id": g1.RUN_ID, "window_seconds": 1800,
            "started_monotonic": mono, "deadline_monotonic": mono + offset_seconds,
            "started_wall": g1._iso(now),
            "deadline_wall": g1._iso(now + timedelta(seconds=offset_seconds)),
        }, private=True)

    def test_the_deadline_fails_closed(self):
        missing = Path(tempfile.mkdtemp(prefix="g1-no-window-"))
        self.assertTrue(g1._deadline_passed(missing))  # 没有窗口文件 = 不能发
        expired = Path(tempfile.mkdtemp(prefix="g1-expired-window-"))
        self._window(expired, offset_seconds=-1)
        self.assertTrue(g1._deadline_passed(expired))
        live = Path(tempfile.mkdtemp(prefix="g1-live-window-"))
        self._window(live, offset_seconds=60)
        self.assertFalse(g1._deadline_passed(live))

    def test_a_closed_latch_never_calls_the_inner_sender(self):
        root = Path(tempfile.mkdtemp(prefix="g1-latch-window-"))
        self._window(root, offset_seconds=-1)
        calls = []

        class _Inner:
            def invoke(self, request, *, schema_retry_index: int = 0):
                calls.append(1)
                return SimpleNamespace(failure_class=None)

        budget_root = Path(tempfile.mkdtemp(prefix="g1-latch-budget-"))
        gate = g1.G1CognitionGate(_Inner(), latch=g1.SendLatch(
            lambda: g1._deadline_passed(root)), budget=g1._budget(budget_root))
        with self.assertRaises(g1.GateClosed) as caught:
            gate.invoke(object())
        self.assertEqual(caught.exception.reason_code, g1.REASON_DEADLINE_PASSED)
        self.assertEqual(calls, [])

    def test_a_provider_failure_stops_the_whole_batch(self):
        calls = []

        class _Failing:
            def invoke(self, request, *, schema_retry_index: int = 0):
                calls.append(1)
                return SimpleNamespace(failure_class="MODEL_TIMEOUT")

        budget_root = Path(tempfile.mkdtemp(prefix="g1-fail-budget-"))
        latch = g1.SendLatch(lambda: False)
        gate = g1.G1CognitionGate(_Failing(), latch=latch,
                                  budget=g1._budget(budget_root))
        gate.invoke(object())
        self.assertTrue(latch.closed)
        with self.assertRaises(g1.GateClosed):
            gate.invoke(object())
        self.assertEqual(len(calls), 1)

    def test_a_content_exception_stops_the_batch_before_the_next_send(self):
        calls = []

        class _Raising:
            def complete(self, *args, **kwargs):
                calls.append(1)
                raise RuntimeError("boom")

        root = Path(tempfile.mkdtemp(prefix="g1-content-window-"))
        self._window(root, offset_seconds=60)
        latch = g1.SendLatch(lambda: g1._deadline_passed(root))
        gate = g1.G1ContentGate(_Raising(), latch=latch,
                                budget=g1._budget(tempfile.mkdtemp(prefix="g1-content-budget-")))
        with self.assertRaises(RuntimeError):
            gate.complete()
        self.assertTrue(latch.closed)
        with self.assertRaises(g1.GateClosed) as caught:
            gate.complete()
        self.assertEqual(caught.exception.reason_code, g1.REASON_PROVIDER_FAILURE)
        self.assertEqual(len(calls), 1)

    def test_a_recorded_stop_blocks_a_fresh_latch(self):
        root = Path(tempfile.mkdtemp(prefix="g1-cross-stop-"))
        g1._write_window(root)
        g1._record_batch_stop(root, g1.REASON_PROVIDER_FAILURE)
        latch = g1.SendLatch(lambda: g1._deadline_passed(root))
        stop = g1._loop_stop(root, g1._budget(tempfile.mkdtemp(prefix="g1-cross-budget-")),
                             latch)
        self.assertEqual(stop, g1.REASON_PROVIDER_FAILURE)
        self.assertTrue(latch.closed)

    def test_schema_retry_is_refused_before_any_send(self):
        budget_root = Path(tempfile.mkdtemp(prefix="g1-retry-budget-"))
        gate = g1.G1CognitionGate(object(), latch=g1.SendLatch(lambda: False),
                                  budget=g1._budget(budget_root))
        with self.assertRaises(g1.GateClosed) as caught:
            gate.invoke(object(), schema_retry_index=1)
        self.assertEqual(caught.exception.reason_code, "MODEL_ATTEMPT_RETRY_REFUSED")


class SharedLedgerTest(unittest.TestCase):
    def test_two_processes_share_one_persistent_cap(self):
        state_root = Path(tempfile.mkdtemp(prefix="g1-shared-cap-"))
        script = str((REPO_ROOT / "scripts" / "g1_real_adoption.py").resolve())
        first = subprocess.run([sys.executable, "-B", script, "_reserve",
                                "--state-root", str(state_root), "--count", "12"],
                               capture_output=True, text=True)
        second = subprocess.run([sys.executable, "-B", script, "_reserve",
                                 "--state-root", str(state_root), "--count", "8"],
                                capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr[-400:])
        self.assertEqual(second.returncode, 0, second.stderr[-400:])
        budget = g1._budget(state_root)
        self.assertEqual(g1._ledger_calls(budget), g1.REQUEST_CAP)
        with self.assertRaises(Exception):
            budget.reserve("decision")


class BatchIdentityTest(unittest.TestCase):
    """批次身份是封闭的两档映射：缺省 a 不漂移，b 只有显式选择才生效。"""

    def test_the_default_batch_is_a_and_lands_on_the_original_mapping(self):
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])
        a = g1.BATCH_PROFILES["a"]
        self.assertEqual(str(a.root), "/root/nyairo-g1-real-20261030")
        self.assertEqual(a.run_id, "run:g1-real-20261030")
        self.assertEqual(a.domain_id, "g1:real:20261030")
        self.assertEqual(a.expected_history, 98)
        self.assertEqual(g1.RUN_ID, a.run_id)
        self.assertEqual(g1.PREREG_ROOT, a.root)

    def test_the_b_batch_is_a_closed_second_identity(self):
        b = g1.BATCH_PROFILES["b"]
        self.assertEqual(str(b.root), "/root/nyairo-g1-real-20261030-b")
        self.assertEqual(b.run_id, "run:g1-real-20261030-b")
        self.assertEqual(b.domain_id, "g1:real:20261030-b")
        self.assertEqual(b.expected_history, 101)
        self.assertEqual(str(b.authority_dir), "/root/nyairo-g1-real-20261030-b-memory-authority")
        # c 是后续另行批准的第三批：映射与截止在 tests/test_g1_batch_c.py 里单独固定。
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})

    def test_an_unknown_batch_is_refused(self):
        with self.assertRaises(g1.Refused) as caught:
            g1._select_batch("z")
        self.assertEqual(caught.exception.reason_code, g1.REASON_UNKNOWN_BATCH)

    def test_switching_batch_is_scoped_and_restored(self):
        with g1._use_batch("b"):
            self.assertIs(g1._identity(), g1.BATCH_PROFILES["b"])
            budget_root = Path(tempfile.mkdtemp(prefix="g1-b-budget-"))
            self.assertEqual(g1._ledger_view(g1._budget(budget_root))["run_id"],
                             g1.BATCH_PROFILES["b"].run_id)
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])

    def test_the_other_batch_root_and_identity_are_refused(self):
        with g1._use_batch("b"):
            # 默认 expected（当前批次 root）下拿 a 的根 → 具名错批。
            refused = g1._check_root(g1.BATCH_PROFILES["a"].root)
            self.assertEqual(refused["reason_code"], g1.REASON_BATCH_IDENTITY_MISMATCH)
            # 落盘 prereg 属于 a：即使路径对得上，也不许当成 b。
            root = Path(tempfile.mkdtemp(prefix="g1-wrong-batch-"))
            a = g1.BATCH_PROFILES["a"]
            g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
                "batch": "a", "run_id": a.run_id, "domain_id": a.domain_id,
                "root": str(root), "state_root": str(root / "one-day")})
            refused = g1._verify_instance_identity(root)
            self.assertEqual(refused["reason_code"], g1.REASON_BATCH_IDENTITY_MISMATCH)
        # 缺 identity 的 prereg 同样拒绝（a 批也不认没有 run_id 的记录）。
        root = Path(tempfile.mkdtemp(prefix="g1-missing-identity-"))
        g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
            "root": str(root), "state_root": str(root / "one-day")})
        refused = g1._verify_instance_identity(root)
        self.assertEqual(refused["reason_code"], g1.REASON_BATCH_IDENTITY_MISMATCH)


class RootAndAuditTest(unittest.TestCase):
    def test_only_the_pre_registered_root_is_accepted(self):
        self.assertIsNone(g1._check_root(g1.PREREG_ROOT, expected=g1.PREREG_ROOT))
        refused = g1._check_root(Path("/root/nyairo-fastday-20261026-m"),
                                 expected=g1.PREREG_ROOT)
        self.assertEqual(refused["reason_code"], g1.REASON_ROOT_NOT_PREREGISTERED)

    def test_the_owner_stop_line_is_read_structurally(self):
        log = Path(tempfile.mkdtemp(prefix="g1-stop-log-")) / "supply.log"
        log.write_text("noise\nlife-supply: graceful stop on signal 15 "
                       "(socket_handling=SOCKET_PATH_FREE, drained=True, inflight=0)\n",
                       encoding="utf-8")
        tokens = g1._socket_stop_tokens(log)
        self.assertTrue(tokens["seen"])
        self.assertTrue(tokens["drained"])
        self.assertEqual(tokens["signal"], 15)
        self.assertEqual(tokens["socket_handling"], "SOCKET_PATH_FREE")

    def test_the_safe_view_drops_free_text(self):
        view = g1._safe_wake({"kind": "CHECK_WINDOW", "status": "ACTED",
                              "decision": "CONTINUE", "llm_calls": 1,
                              "extra": {"owner_version_id": "artifactversion:abc"},
                              "detail": "a long detail that should be bounded"})
        self.assertEqual(view["owner_version_id"], "artifactversion:abc")
        self.assertNotIn("prompt", view)
        self.assertNotIn("content", view)


class P1VersionReferenceTest(unittest.TestCase):
    """P1 的成果识别：同一 owner 版本在现有链上有两种合法写法，不能按字符串拒掉。

    真实 runner 走的是 ``owner_evidence`` → ``extra.owner_version_id``，值是
    ``artifactversion:artifact-version:<hex>``；离线 ExplicitDriver 走的是 cycle 自己的顶层
    字段，值是 owner 自己的 ``artifact-version:<hex>``。两者都是既有正式链的返回形状。
    """

    HEX = "f79752e07a804eb388a70839967b5682"
    ARTIFACT = "ba763339199b48a88c130c016fdcfd7d"
    ACTIVITY = "actv_d4f21db04f9cf535"

    def _point(self, head, versions=2, *, artifact=None):
        # owner 重读回来的是「一件具体作品」：默认就是 receipt 指向的 ARTIFACT。
        return {"artifact": self.ARTIFACT if artifact is None else artifact,
                "head": head, "versions": versions}

    def _runner_wake(self, ref, *, created=True):
        # 真实 runner 的形状：证据在 extra 里，_safe_wake 负责把它抬到顶层。
        return g1._safe_wake({"kind": "CHECK_WINDOW", "status": "ACTED", "llm_calls": 1,
                              "extra": {"owner_version_created": created,
                                        "owner_version_id": ref}})

    def _confirm(self, wake, *, point=None, activity=None, artifact=None, excluded=()):
        created, owner = g1._owner_version_from_wakes([wake])
        return g1._p1_confirmed(
            owner_created=created, owner_version_id=owner,
            receipt_artifact=self.ARTIFACT if artifact is None else artifact,
            receipt_activity=self.ACTIVITY if activity is None else activity,
            excluded=excluded, point=self._point("artifact-version:" + self.HEX)
            if point is None else point)

    def test_the_runners_wrapped_ref_matches_the_owners_own_head(self):
        wrapped = "artifactversion:artifact-version:" + self.HEX
        wake = self._runner_wake(wrapped)
        # 旧入口直接比字符串会拒；这条断言固定住被修的那个缺陷。
        self.assertNotEqual("artifact-version:" + self.HEX, wake["owner_version_id"])
        self.assertTrue(self._confirm(wake))

    def test_both_existing_spellings_name_the_same_version(self):
        for ref in ("artifactversion:artifact-version:" + self.HEX,
                    "artifactversion:" + self.HEX,
                    "artifact-version:" + self.HEX):
            self.assertTrue(self._confirm(self._runner_wake(ref)), ref)

    def test_the_offline_drivers_top_level_shape_still_confirms(self):
        # ExplicitDriver 的返回形状：owner 自己的写法直接在顶层。
        wake = g1._safe_wake({"llm_calls": 1, "status": "ACTED",
                              "owner_version_created": True,
                              "owner_version_id": "artifact-version:" + self.HEX})
        self.assertTrue(self._confirm(wake))

    def test_a_different_version_never_confirms(self):
        other = "0123456789abcdef0123456789abcdef"
        self.assertFalse(self._confirm(
            self._runner_wake("artifactversion:artifact-version:" + other)))

    def test_an_unreadable_or_unknown_ref_fails_closed(self):
        for point in ({"head": "", "versions": 2}, {"head": "artifact-version:" + self.HEX,
                                                     "versions": 0},
                      {"head": None, "versions": 2}):
            self.assertFalse(self._confirm(self._runner_wake(
                "artifactversion:artifact-version:" + self.HEX), point=point))
        for ref in ("", "artifactversion:", "artifact-version:", "checkpoint:cp-1",
                    "receipt:r-1"):
            self.assertFalse(self._confirm(self._runner_wake(ref)), ref)

    def test_a_missing_activity_or_wrong_work_never_confirms(self):
        wake = self._runner_wake("artifactversion:artifact-version:" + self.HEX)
        self.assertFalse(self._confirm(wake, activity=""))
        self.assertFalse(self._confirm(wake, artifact="another-work"))
        # owner 没建版本（False/None）同样不算。
        self.assertFalse(self._confirm(self._runner_wake(
            "artifactversion:artifact-version:" + self.HEX, created=False)))
        self.assertFalse(self._confirm(g1._safe_wake({"llm_calls": 1})))

    def test_the_owner_read_must_be_the_receipts_own_work(self):
        # 同样的 head 值，但 owner 这次读的是另一件作品：配对不上，不能算本轮成果。
        self.assertFalse(self._confirm(
            self._runner_wake("artifactversion:artifact-version:" + self.HEX),
            point=self._point("artifact-version:" + self.HEX, artifact="another-work")))
        # owner 没回对象身份（读取失败/空）也失败关闭。
        self.assertFalse(self._confirm(
            self._runner_wake("artifactversion:artifact-version:" + self.HEX),
            point=self._point("artifact-version:" + self.HEX, artifact="")))
        # 正控：owner 自己的两种作品写法都指向 receipt 的 ARTIFACT。
        for spelling in ("artifact:" + self.ARTIFACT, "artifact:artifact:" + self.ARTIFACT):
            self.assertTrue(self._confirm(
                self._runner_wake("artifactversion:artifact-version:" + self.HEX),
                point=self._point("artifact-version:" + self.HEX, artifact=spelling)), spelling)

    def test_undefined_ref_spellings_never_get_matched_by_stripping(self):
        wrapped = "artifactversion:artifact-version:" + self.HEX
        # 重复包装和裸 hex 都不是现有链的写法，不能靠反复剥前缀认成同一版本。
        for ref in ("artifactversion:artifactversion:" + self.HEX,
                    "artifact-version:artifactversion:" + self.HEX,
                    self.HEX):
            self.assertFalse(self._confirm(self._runner_wake(ref)), ref)
        # 反过来，owner head 用了未定义写法时同样不认。
        self.assertFalse(self._confirm(
            self._runner_wake(wrapped),
            point=self._point("artifactversion:artifactversion:" + self.HEX)))


if __name__ == "__main__":
    unittest.main()
