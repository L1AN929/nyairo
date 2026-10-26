#!/usr/bin/env python3
"""v0.5：同作品实践与本地表达在两进程同根承接（跨 PID 正式链）。

本模块只守这一块，不重跑前三块（A43/B37、A44/B36、A67/B24、A15/B38）：

1. 父进程只准备一个隔离夹具根；**P1** 与 **P2** 是两个真正不同的子进程，各自起
   一次真实 Supply owner（同一个 canonical db、同一 socket 路径）并各自正规关停。
   父进程在整个链里不持有 runtime / owner。
2. P1：正式 ``evaluate_once`` 写出作品 A 的第二版与第三版（同一 Activity），得到
   owner 结果身份、handoff 与一条真实经历证据的 HELPED 评价（方法 TRIAL）；
   正式 prepare 草稿 D1 并合法 defer；另建作品 B 作不串目标对照。
3. P2：重开同一个新根的 Supply db、M0 Memory 与 AG-2 store。第一次正式请求纯读，
   不续作、不分享；随后合法 CONTINUE 同一 Activity 续写 A（新结果身份），B 不变；
   同结果重投不增事件/经历；旧稿 D1 由 owner 以 ``stale_source`` 拒绝且零写；
   显式 prepare D2 绑定新版本、只产生一条本地 receipt；真实 owner CAS 退休后旧
   ``method_ref`` 被 AG-1 以 ``INVENTED_METHOD_REF`` 拒绝且不提交 Decision。
4. P3：纯读重开，只核对最终落盘稳定；不跑 Decision、不写版本、不投递。
5. 父进程最后自己再起一次只读 owner，从真实 canonical db 读回 A/B 的 head 与版本数、
   D1/D2 的状态与 receipt —— 不只信子进程报告。

替身只有 Decision 与内容回答（脚本形状，明示）。不宣称真模型自然采用、G1、24h
或 crash 矩阵；这是正常有序停启，不是崩溃恢复。POSIX 专用（fcntl + AF_UNIX）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips the class below
    try:
        import life_artifact_port as lap  # noqa: E402
        import life_sharing_context as lsc  # noqa: E402
        import life_sharing_service as lss  # noqa: E402
        import one_day_life as odl  # noqa: E402
        import v05_restart_continuation_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        lap = lsc = lss = odl = driver = None
else:  # pragma: no cover
    lap = lsc = lss = odl = driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


@POSIX_ONLY
@needs_fixture
class RestartContinuationTest(unittest.TestCase):
    """一条正式链：P1 真写 → P1 退出 → P2 不同 PID 同根续写 → P3 纯读稳定。"""

    def test_same_root_continuation_across_two_real_pids(self) -> None:
        root = driver.prepare_fixture()
        self.addCleanup(lambda: None)  # the fixture root is intentionally kept for review

        p1 = driver._run_phase("p1", driver._write_config(root, {}))
        p2 = driver._run_phase("p2", driver._write_config(root, {
            "artifact_a": p1["artifact_a"], "artifact_b": p1["artifact_b"],
            "method_id": p1["method_id"], "method_ref": p1["method_ref"],
            "d1": p1["d1"], "handoff_v2": p1["handoff_v2"],
            "result_v2": p1["result_v2"], "v2_head": p1["v2_head"],
            "activity_ref": p1["activity_ref"],
            "seed_experience_id": p1["seed_experience_id"],
        }))
        p3 = driver._run_phase("p3", driver._write_config(root, {
            "artifact_a": p1["artifact_a"], "artifact_b": p1["artifact_b"],
            "d1": p1["d1"], "d2": p2["d2"], "method_id": p1["method_id"],
        }))

        # -- 真的是两个/三个不同 PID，同一个根，各自正规关停 ------------------------
        self.assertEqual(len({p1["pid"], p2["pid"], p3["pid"]}), 3, (p1, p2, p3))
        for report in (p1, p2, p3):
            self.assertNotEqual(report["pid"], os.getpid(), report)
            self.assertEqual(report["root"], str(root), report)
            self.assertTrue(report.get("closed"), report)
            self.assertTrue(report.get("service_stopped"), report)

        # -- P1：真实结果、方法评价、草稿、作品 B ---------------------------------
        self.assertEqual(p1["seed_outcome"], "ACTED", p1)
        self.assertTrue(p1["seed_checks_ok"], p1)
        self.assertEqual(p1["result_v2"],
                         "artifact:%s@artifactversion:%s"
                         % (driver.bare(p1["artifact_a"]), p1["v2_head"]), p1)
        self.assertEqual(p1["p1_continue_outcome"], "ACTED", p1)
        self.assertEqual(p1["p1_continue_decision"], "CONTINUE", p1)
        self.assertEqual(int(p1["a_versions_p1"]), 3, p1)
        self.assertNotEqual(p1["v2_head"], p1["v3_head"], p1)
        self.assertEqual(p1["method_state_p1"], "TRIAL", p1)
        self.assertGreaterEqual(int(p1["method_trial_count_p1"]), 1, p1)
        self.assertEqual(p1["d1_bound"], p1["v3_head"], p1)
        self.assertEqual(p1["d1_state_after_defer"], lss.STATE_DEFERRED, p1)
        self.assertEqual(int(p1["b_versions_p1"]), 2, p1)

        # -- P2 第一次正式请求：纯读，什么都不写 ----------------------------------
        self.assertEqual(p2["read_outcome"], "NO_ACTION", p2)
        self.assertEqual(int(p2["read_llm_calls"]), 1, p2)
        self.assertEqual(p2["read_a_head"], p1["v3_head"], p2)
        self.assertEqual(int(p2["read_a_versions"]), 3, p2)
        self.assertEqual(p2["read_b_head"], p1["b_head"], p2)
        self.assertEqual(int(p2["read_handoff_count"]), int(p1["handoff_count_p1"]), p2)
        self.assertEqual(int(p2["read_experience_count"]), int(p1["experience_count_p1"]), p2)
        self.assertEqual(p2["read_share_counts"], p2["share_counts_at_start"], p2)
        self.assertTrue(p2["read_d1_in_request"], p2)
        self.assertEqual(p2["read_d1_state"], lss.STATE_DEFERRED, p2)
        self.assertEqual(p2["read_method_state"], "TRIAL", p2)
        self.assertTrue(p2["read_saw_work_source"], p2)

        # -- 续写同一 Activity；新结果身份；B 不串 ---------------------------------
        self.assertEqual(p2["continue_outcome"], "ACTED", p2)
        self.assertEqual(p2["continue_decision"], "CONTINUE", p2)
        self.assertTrue(p2["activity_same_as_p1"], p2)
        self.assertEqual(int(p2["a_versions_after_continue"]), 4, p2)
        self.assertEqual(p2["b_head_after_continue"], p1["b_head"], p2)
        self.assertEqual(int(p2["b_versions_after_continue"]), 2, p2)
        self.assertEqual(p2["result_v4"],
                         "artifact:%s@artifactversion:%s"
                         % (driver.bare(p1["artifact_a"]), p2["v4_head"]), p2)
        self.assertNotIn(p2["handoff_v4"], (p1["handoff_v2"], p1["handoff_v3"]), p2)
        self.assertEqual(int(p2["handoff_count_after_continue"]),
                         int(p1["handoff_count_p1"]) + 1, p2)
        self.assertTrue(p2["writer_saw_method"], p2)

        # -- 同结果重投只对账，不重放决策/动作/writer ------------------------------
        self.assertEqual(p2["replay_handoff_id"], p1["handoff_v2"], p2)
        self.assertEqual(int(p2["replay_count_after"]), int(p2["replay_count_before"]), p2)
        self.assertEqual(p2["replay_a_head"], p2["v4_head"], p2)
        self.assertEqual(int(p2["replay_a_versions"]), 4, p2)

        # -- 旧稿 stale 拒发且零写 ------------------------------------------------
        self.assertTrue(p2["stale_source_current"].get("stale"), p2)
        self.assertFalse(p2["stale_source_current"].get("matches_recorded"), p2)
        self.assertEqual(p2["stale_source_current"].get("reason_code"),
                         "SOURCE_REVISION_CHANGED", p2)
        self.assertTrue(p2["stale_text_available"], p2)
        self.assertGreater(int(p2["stale_text_chars"]), 0, p2)
        self.assertFalse(p2["stale_ok"], p2)
        self.assertTrue(p2["stale_is_stale_source"], p2)
        self.assertFalse(p2["stale_delivered"], p2)
        self.assertEqual(p2["stale_head_after"], p2["d1_head_at_start"], p2)
        self.assertEqual(p2["stale_state_after"], lss.STATE_DEFERRED, p2)
        self.assertIsNone(p2["stale_receipt_after"], p2)

        # -- 新草稿 + 一条本地 receipt -------------------------------------------
        self.assertTrue(p2["prepare2_ok"], p2)
        self.assertEqual(p2["d2_bound"], p2["v4_head"], p2)
        self.assertEqual(p2["d1_head_after_prepare2"], p2["d1_head_at_start"], p2)
        self.assertTrue(p2["send_ok"], p2)
        self.assertEqual(int(p2["send_delivery_count"]), 1, p2)
        self.assertEqual(p2["send_delivery_id"], p2["owner_receipt_id"], p2)
        self.assertEqual(p2["receipt_flags"], {
            "local_only": True, "external_channel_sent": False,
            "external_delivery_confirmed": False, "read_by_audience": False,
            "acknowledged": False, "understood": False}, p2)
        self.assertEqual(p2["repeat_send_receipt"], p2["send_delivery_id"], p2)
        self.assertEqual(int(p2["inbox_delivered_count"]), 1, p2)

        # -- 退休边界 -----------------------------------------------------------
        self.assertTrue(p2["retire_ok"], p2)
        self.assertEqual(p2["retire_state"], "RETIRED", p2)
        self.assertTrue(p2["retired_ref_invented"], p2)
        self.assertEqual(p2["retired_ref_outcome"], "DECISION_FAILED", p2)
        self.assertIsNone(p2["retired_ref_decision"], p2)
        self.assertEqual(p2["legal_outcome"], "NO_ACTION", p2)
        self.assertFalse(p2["writer_carries_retired_instruction"], p2)

        # -- P3 纯读稳定 ---------------------------------------------------------
        self.assertEqual(p3["a_head"], p2["v4_head"], p3)
        self.assertEqual(int(p3["a_versions"]), 4, p3)
        self.assertEqual(p3["b_head"], p1["b_head"], p3)
        self.assertEqual(int(p3["b_versions"]), 2, p3)
        self.assertEqual(int(p3["handoff_count"]), int(p2["final_handoff_count"]), p3)
        self.assertEqual(p3["handoff_ids"], p2["final_handoff_ids"], p3)
        self.assertEqual(p3["d1_head"], p2["d1_head_at_start"], p3)
        self.assertEqual(p3["d1_state"], lss.STATE_DEFERRED, p3)
        self.assertEqual(p3["d2_head"], p2["d2_head_after_send"], p3)
        self.assertEqual(p3["d2_state"], lss.STATE_LOCAL_DELIVERED, p3)
        self.assertEqual(p3["d2_receipt"], p2["send_delivery_id"], p3)
        self.assertEqual(p3["method_state"], "RETIRED", p3)
        self.assertEqual(int(p3["cognition_calls"]), 0, p3)
        self.assertEqual(int(p3["writer_prompts"]), 0, p3)

        # -- 父进程自己从真实 canonical db 读回，不只信子进程报告 -------------------
        observed = self._read_owner_store(root, {
            "a": p1["artifact_a"], "b": p1["artifact_b"],
            "d1": p1["d1"], "d2": p2["d2"],
        })
        self.assertEqual(observed["a_head"], p2["v4_head"], observed)
        self.assertEqual(int(observed["a_versions"]), 4, observed)
        self.assertEqual(observed["b_head"], p1["b_head"], observed)
        self.assertEqual(int(observed["b_versions"]), 2, observed)
        self.assertEqual(observed["d1_state"], lss.STATE_DEFERRED, observed)
        self.assertEqual(observed["d1_head"], p2["d1_head_at_start"], observed)
        self.assertEqual(observed["d2_state"], lss.STATE_LOCAL_DELIVERED, observed)
        self.assertEqual(observed["d2_receipt"], p2["send_delivery_id"], observed)

    # -- shutdown safety: drain first, and only the owner's own guard may unlink ---------

    def test_shutdown_drains_and_only_the_owner_guard_removes_its_own_socket(self) -> None:
        """负控：live / 普通文件都被既有 guard 拒绝且不删；已停的自有 socket 经 guard 清理。"""

        from lifesupply.service.life_supply import prepare_socket_path

        root = Path(tempfile.mkdtemp(prefix="v05-restart-socket-guard-"))

        # 普通文件：不是 socket，guard 原样报错并保留路径
        ordinary = root / "supply" / "ordinary.txt"
        ordinary.parent.mkdir(parents=True, exist_ok=True)
        ordinary.write_text("not a socket", encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            prepare_socket_path(ordinary)
        self.assertIn("REFUSING_TO_UNLINK_NON_SOCKET", str(caught.exception))
        self.assertTrue(ordinary.exists())

        # 仍在监听的自有 socket：guard 拒绝且保留，只能先正规关停
        services = driver._start_services(root / "supply")
        try:
            live_path = Path(services.supply_path)
            self.assertTrue(live_path.exists())
            with self.assertRaises(RuntimeError) as caught:
                prepare_socket_path(live_path)
            self.assertIn("REFUSING_TO_UNLINK_LIVE_SOCKET", str(caught.exception))
            self.assertTrue(live_path.exists())
        finally:
            shutdown = driver._stop_services(services)

        # 关停顺序：先排空在途请求，再让同一 guard 清理已停的自有 socket
        self.assertTrue(shutdown["drained"], shutdown)
        self.assertEqual(shutdown["socket_handling"], "STALE_SOCKET_REMOVED", shutdown)
        self.assertFalse(Path(services.supply_path).exists())

    # -- independent read-back from the real owner store --------------------------

    def _read_owner_store(self, root: Path, ids: dict) -> dict:
        services = driver._start_services(Path(root) / "supply")
        try:
            instance = Path(root) / "instance"
            data = odl.read_instance(instance)
            env = odl.instance_environment(instance)
            env.update({
                "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
                "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
                "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
                "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
            })
            port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
            sharing = lss.sharing_from_env(env)
            a_head = str(port.read_head(ids["a"])["version_id"])
            b_head = str(port.read_head(ids["b"])["version_id"])
            d1 = lsc.read_draft(sharing, ids["d1"])
            d2 = lsc.read_draft(sharing, ids["d2"])
            return {
                "a_head": a_head,
                "a_versions": int(port.list_versions(ids["a"], limit=50)["total_versions"]),
                "b_head": b_head,
                "b_versions": int(port.list_versions(ids["b"], limit=50)["total_versions"]),
                "d1_head": str(d1.get("head_version_id") or ""),
                "d1_state": str(d1.get("state") or ""),
                "d2_head": str(d2.get("head_version_id") or ""),
                "d2_state": str(d2.get("state") or ""),
                "d2_receipt": str((d2.get("receipt") or {}).get("delivery_id") or ""),
            }
        finally:
            driver._stop_services(services)


if __name__ == "__main__":
    unittest.main(verbosity=2)
