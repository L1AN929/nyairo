#!/usr/bin/env python3
"""state_tx_guard 的事务测试：提交序淘汰、长程重试对账、过期窗口、并发冲突。

Run:  python -m unittest tests.test_state_tx_guard
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import state_tx_guard as guard  # noqa: E402


def _reload(state: dict) -> dict:
    """模拟一次 ``json.dumps(sort_keys=True)`` 落盘再读回。"""

    return json.loads(json.dumps(state, sort_keys=True, ensure_ascii=False))


class SubmissionOrderEvictionTests(unittest.TestCase):
    def test_evicts_oldest_op_seq_never_alphabetical(self) -> None:
        state: dict = {"revision": 7}
        submission = ["op_z", "op_a", "op_m", "op_b", "op_y", "op_c"]
        for index, key in enumerate(submission):
            guard.stamp_receipt(
                state,
                key,
                {"status": "applied", "ok": True},
                revision=index + 1,
                committed_at=f"2026-11-16T00:00:0{index}Z",
            )
            state = _reload(state)  # 每一轮都经过 sort_keys=True 的往返

        evicted = guard.trim_receipts_by_op_seq(state, max_keep=3, max_ring=32)
        # 最早提交的三条被淘汰；字母序第一的 "op_a" 只因提交第二早才被淘汰，
        # 而第一条必须是 "op_z"，绝不可能是 next(iter(...)) 得到的 "op_a"。
        self.assertEqual(evicted, ["op_z", "op_a", "op_m"])
        self.assertEqual(sorted(state["operations"]), ["op_b", "op_c", "op_y"])
        self.assertEqual(state["evicted_up_to_revision"], 3)

        # 再淘汰一次没有变化。
        self.assertEqual(guard.trim_receipts_by_op_seq(state, max_keep=3), [])
        self.assertEqual(len(state["operations"]), 3)

    def test_legacy_rows_ordered_by_committed_at_then_key(self) -> None:
        state = {
            "operations": {
                "legacy_z": {"status": "applied", "committed_at": "2026-01-01T00:00:00Z"},
                "legacy_a": {"status": "applied", "committed_at": "2026-02-01T00:00:00Z"},
            }
        }
        evicted = guard.trim_receipts_by_op_seq(state, max_keep=1, max_ring=8)
        # 旧行按 (committed_at, key) 补低位 op_seq：时间更早的 legacy_z 先走。
        self.assertEqual(evicted, ["legacy_z"])
        self.assertIn("legacy_a", state["operations"])
        # 新盖章的回执仍然拿到严格递增的正数序号，排在旧行之后。
        stamped = guard.stamp_receipt(state, "op_new", {"ok": True}, revision=2)
        self.assertGreaterEqual(stamped["op_seq"], 1)
        self.assertNotIn("op_new", guard.trim_receipts_by_op_seq(state, max_keep=1))

    def test_next_op_seq_survives_reload_and_legacy_high_watermark(self) -> None:
        state = {"operations": {"op_old": {"status": "applied", "op_seq": 5}}}
        stamped = guard.stamp_receipt(state, "op_new", {"ok": True}, revision=1)
        self.assertEqual(stamped["op_seq"], 6)
        self.assertEqual(state["next_op_seq"], 6)
        state = _reload(state)
        again = guard.stamp_receipt(state, "op_newer", {"ok": True}, revision=2)
        self.assertEqual(again["op_seq"], 7)


class LongHorizonRetryTests(unittest.TestCase):
    def _execute_eight(self) -> dict:
        state: dict = {"revision": 0}
        for index in range(1, 9):
            state["revision"] = index
            guard.stamp_receipt(
                state,
                f"op_{index}",
                {"status": "applied", "ok": True, "result": {"n": index}},
                revision=index,
                payload_hash=f"hash_{index}",
                committed_at=f"2026-11-16T00:00:0{index}Z",
            )
            guard.trim_receipts_by_op_seq(state, max_keep=5, max_ring=64)
            state = _reload(state)
        return state

    def test_evicted_op_replays_from_committed_ring(self) -> None:
        state = self._execute_eight()
        self.assertNotIn("op_1", state["operations"])
        self.assertEqual(state["evicted_up_to_revision"], 3)

        outcome, detail = guard.reconcile_evicted_or_active_op(
            None, state, "op_1", payload_hash="hash_1"
        )
        self.assertEqual(outcome, "hit_evicted")
        assert detail is not None
        self.assertTrue(detail["already_applied"])
        self.assertEqual(detail["reconciled_from"], "committed_op_ring")
        self.assertEqual(detail["revision"], 1)

        # 同一 operation_key 却换了载荷 → 冲突，不能当作旧结果放行。
        conflict, payload = guard.reconcile_evicted_or_active_op(
            None, state, "op_1", payload_hash="hash_other"
        )
        self.assertEqual(conflict, "conflict")
        self.assertEqual(payload["reason_code"], "OPERATION_KEY_CONFLICT")

        # 仍在活动回执里的键走 hit_full。
        full, full_detail = guard.reconcile_evicted_or_active_op(
            None, state, "op_8", payload_hash="hash_8"
        )
        self.assertEqual(full, "hit_full")
        self.assertEqual(full_detail["result"], {"n": 8})

    def test_evicted_op_replays_from_operations_journal_when_ring_exhausted(self) -> None:
        state = self._execute_eight()
        state["committed_op_ring"] = []  # 模拟 ring 被更长的历史挤空
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "phone" / "operations.jsonl"
            journal.parent.mkdir(parents=True)
            journal.write_text(
                json.dumps({"operation_key": "op_1", "status": "applied", "ok": True,
                            "revision": 1, "payload_hash": "hash_1"}) + "\n",
                encoding="utf-8",
            )
            outcome, detail = guard.reconcile_evicted_or_active_op(
                tmp, state, "op_1", payload_hash="hash_1"
            )
            self.assertEqual(outcome, "hit_evicted")
            assert detail is not None
            self.assertEqual(detail["reconciled_from"], "operations.jsonl")
            self.assertEqual(detail["revision"], 1)

            # journal 里记录的指纹不同 → 冲突而不是重放。
            conflict, payload = guard.reconcile_evicted_or_active_op(
                tmp, state, "op_1", payload_hash="hash_wrong"
            )
            self.assertEqual(conflict, "conflict")
            self.assertEqual(payload["reason_code"], "OPERATION_KEY_CONFLICT")


class ExpiredRetryWindowTests(unittest.TestCase):
    def test_outside_ring_and_journal_returns_expired_not_fresh_miss(self) -> None:
        state = {
            "revision": 10,
            "operations": {},
            "committed_op_ring": [],
            "evicted_up_to_revision": 6,
        }
        outcome, payload = guard.reconcile_evicted_or_active_op(
            None, state, "op_ancient", expected_revision=5
        )
        self.assertEqual(outcome, "expired")
        assert payload is not None
        self.assertEqual(payload["reason_code"], "RETRY_WINDOW_EXPIRED")
        self.assertEqual(payload["expected_revision"], 5)
        self.assertEqual(payload["evicted_up_to_revision"], 6)
        self.assertEqual(payload["current_revision"], 10)

        # 恰好落在淘汰边界上也算过期。
        edge, _ = guard.reconcile_evicted_or_active_op(
            None, state, "op_edge", expected_revision=6
        )
        self.assertEqual(edge, "expired")

        # 还没被淘汰的版本窗口仍可视为新请求。
        fresh, _ = guard.reconcile_evicted_or_active_op(
            None, state, "op_fresh", expected_revision=9
        )
        self.assertEqual(fresh, "miss")

        # 未携带期望版本时不做过期判定。
        no_hint, _ = guard.reconcile_evicted_or_active_op(None, state, "op_no_hint")
        self.assertEqual(no_hint, "miss")

    def test_expired_window_ignored_when_no_eviction_recorded(self) -> None:
        state = {"revision": 10, "operations": {}, "committed_op_ring": []}
        outcome, _ = guard.reconcile_evicted_or_active_op(
            None, state, "op_x", expected_revision=1
        )
        self.assertEqual(outcome, "miss")


class SameKeyPayloadConflictTests(unittest.TestCase):
    def test_active_receipt_conflict(self) -> None:
        state: dict = {"revision": 1}
        guard.stamp_receipt(
            state, "op_same", {"status": "applied", "ok": True}, revision=1,
            payload_hash=guard.payload_fingerprint({"action": "a", "amount": 3}),
        )
        expected = guard.payload_fingerprint({"amount": 3, "action": "a"})
        same, detail = guard.reconcile_evicted_or_active_op(
            None, state, "op_same", payload_hash=expected
        )
        self.assertEqual(same, "hit_full")
        self.assertEqual(detail["operation_key"], "op_same")

        different, payload = guard.reconcile_evicted_or_active_op(
            None, state, "op_same", payload_hash=guard.payload_fingerprint({"action": "b"})
        )
        self.assertEqual(different, "conflict")
        self.assertEqual(payload["reason_code"], "OPERATION_KEY_CONFLICT")

        # 不携带指纹时只按 operation_key 幂等放行。
        nothing, _ = guard.reconcile_evicted_or_active_op(None, state, "op_same")
        self.assertEqual(nothing, "hit_full")

    def test_fingerprint_is_key_order_insensitive(self) -> None:
        first = guard.payload_fingerprint({"b": 2, "a": [1, 2], "nested": {"y": 1, "x": 2}})
        second = guard.payload_fingerprint({"nested": {"x": 2, "y": 1}, "a": (1, 2), "b": 2})
        self.assertEqual(first, second)


class StateFileLockTests(unittest.TestCase):
    def test_lock_creates_sibling_lock_file_and_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "nested" / "deep" / "state.json"
            with guard.state_file_lock(state_path):
                lock_path = Path(str(state_path) + ".lock")
                self.assertTrue(lock_path.is_file())
            # 释放后锁文件保留在盘上（删除会让 POSIX 上新旧进程各锁一个 inode）。
            self.assertTrue(lock_path.is_file())
            self.assertFalse(state_path.exists())

    def test_reentrant_in_same_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with guard.state_file_lock(state_path):
                with guard.state_file_lock(state_path):
                    pass

    def test_real_rmw_serialization_one_success_one_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            guard.atomic_write_json(state_path, {"revision": 1, "value": 0})

            outcomes: list[str] = []
            outcomes_lock = threading.Lock()
            barrier = threading.Barrier(2)

            def attempt() -> None:
                barrier.wait(timeout=10)
                with guard.state_file_lock(state_path):
                    doc = json.loads(state_path.read_text(encoding="utf-8"))
                    if int(doc.get("revision", 0)) == 1:
                        doc["revision"] = 2
                        doc["value"] = int(doc.get("value", 0)) + 1
                        guard.atomic_write_json(state_path, doc)
                        verdict = "ok"
                    else:
                        verdict = "rejected"
                    with outcomes_lock:
                        outcomes.append(verdict)

            threads = [threading.Thread(target=attempt) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
                self.assertFalse(thread.is_alive())

            self.assertEqual(outcomes.count("ok"), 1)
            self.assertEqual(outcomes.count("rejected"), 1)
            final = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(final["revision"], 2)
            self.assertEqual(final["value"], 1)

    def test_cross_process_locking_serializes(self) -> None:
        script = r"""
import json
import sys
import time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from components.life.scripts.state_tx_guard import state_file_lock, atomic_write_json
path = Path(sys.argv[2])
with state_file_lock(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["counter"] = int(doc.get("counter", 0)) + 1
    time.sleep(0.25)  # 故意拉长临界区，若锁不串行两侧都会读到同一个旧值
    atomic_write_json(path, doc)
print("done")
"""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            guard.atomic_write_json(state_path, {"counter": 0})
            procs = [
                subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(REPO_ROOT), str(state_path)],
                    cwd=str(REPO_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(2)
            ]
            for proc in procs:
                stdout, stderr = proc.communicate(timeout=60)
                self.assertEqual(
                    proc.returncode,
                    0,
                    msg=f"stdout={stdout!r} stderr={stderr!r}",
                )
            final = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(final["counter"], 2)


class AtomicWriteTests(unittest.TestCase):
    def test_round_trip_and_no_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "doc.json"
            guard.atomic_write_json(target, {"b": 2, "a": 1})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1, "b": 2})
            leftovers = [name for name in Path(tmp).iterdir() if name.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
