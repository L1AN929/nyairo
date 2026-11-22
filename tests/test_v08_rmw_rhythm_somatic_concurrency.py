#!/usr/bin/env python3
"""RMW 并发守卫：autonomy_rhythm 前台/后台串行化与 somatic 同饮只扣一次。

覆盖两处 ``state_tx_guard.state_file_lock`` 接入后的并发边界（纯本机线程，不需要
WSL/POSIX owner 栈）：

* ``autonomy_rhythm_view``：多线程同时 ``apply_autonomy_rhythm_action``（含带与不带
  ``expected_revision`` 的 CAS），与后台心跳 ``tick_autonomy_rhythm_readiness`` 交错，
  断言状态修订单调无空洞、消息队列不丢项、旧版本 CAS 被 ``REVISION_CONFLICT`` 拒收；
* ``somatic_comfort_view``：同饮品并发 ``drink`` / ``mutate_somatic_comfort_view`` 写入，
  断言饮品只消费一次、``lived_needs.thirst`` 只降一次、状态文件无损坏且修订一致。

读路径（``read_state`` / ``read_lived_needs_snapshot``）必须保持零写入、不拿锁。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(Path(__file__).resolve().parent),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402

T0 = "2026-11-11T12:00:00+08:00"
T_LATER = "2026-11-11T14:30:00+08:00"


class AutonomyRhythmRmwConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rmw-rhythm-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _apply(self, payload: dict, now_iso: str = T0) -> dict:
        return arv.apply_autonomy_rhythm_action(self.root, payload, now_iso=now_iso)

    def test_tick_and_apply_serialize_without_losing_messages(self) -> None:
        aside = self._apply({"action": "set_phone_aside", "mode": "deep_focus",
                             "minutes": 120})
        self.assertEqual(aside["revision"], 1)

        texts = [f"今天楼下那棵树好像新长了两片叶子 编号{i}" for i in range(6)]
        barrier = threading.Barrier(len(texts) + 3)
        results: list[tuple[str, object]] = []
        lock = threading.Lock()

        def _record(kind: str, value: object) -> None:
            with lock:
                results.append((kind, value))

        def _evaluate(text: str) -> None:
            try:
                barrier.wait(timeout=10)
                out = self._apply({"action": "evaluate_turn", "user_text": text})
                _record("apply", out["revision"])
            except BaseException as exc:  # noqa: BLE001 - surfaced as a failure below
                _record("error", repr(exc))

        def _tick() -> None:
            try:
                barrier.wait(timeout=10)
                out = arv.tick_autonomy_rhythm_readiness(self.root, T_LATER)
                _record("tick", None if out is None else out["revision"])
            except BaseException as exc:  # noqa: BLE001
                _record("error", repr(exc))

        threads = [threading.Thread(target=_evaluate, args=(t,)) for t in texts]
        threads += [threading.Thread(target=_tick) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([r for r in results if r[0] == "error"], results)
        self.assertEqual(len([r for r in results if r[0] == "apply"]), len(texts))

        state, reason = arv.read_autonomy_rhythm_state(self.root)
        self.assertIsNone(reason)
        final_rev = int(state["revision"])

        written_revisions = [1]
        written_revisions += [int(r[1]) for r in results if r[0] == "apply"]
        written_revisions += [int(r[1]) for r in results
                              if r[0] == "tick" and r[1] is not None]
        # Every successful write bumps exactly one revision under the lock: no gap,
        # no duplicate, and the last one equals the file's revision.
        self.assertEqual(sorted(written_revisions),
                         list(range(1, final_rev + 1)))
        self.assertEqual(len(set(written_revisions)), len(written_revisions))

        pending = state["pending_messages"]
        self.assertEqual(len(pending), len(texts))
        self.assertEqual(len({m["msg_id"] for m in pending}), len(texts))
        self.assertEqual({m["full_text"] for m in pending}, set(texts))
        for message in pending:
            self.assertIn(message["status"], ("QUEUED_ON_PHONE", "READY_TO_REPLY"))

        # The background heartbeat never clobbered the foreground stance.
        self.assertIsInstance(state["subject_override"], dict)
        self.assertEqual(state["subject_override"]["mode"], "deep_focus")
        self.assertEqual(state["preferences"]["away_delay_minutes"],
                         arv.DEFAULT_AWAY_DELAY_MIN)

    def test_expected_revision_cas_rejects_stale_and_accepts_current(self) -> None:
        self._apply({"action": "set_phone_aside", "mode": "aside", "minutes": 30})
        stale, _ = arv.read_autonomy_rhythm_state(self.root)
        stale_rev = int(stale["revision"])

        self._apply({"action": "update_preferences", "away_delay_minutes": 60})
        current, _ = arv.read_autonomy_rhythm_state(self.root)
        self.assertEqual(int(current["revision"]), stale_rev + 1)

        with self.assertRaises(arv.AutonomyRhythmConflictError) as caught:
            self._apply({"action": "update_preferences", "away_delay_minutes": 90,
                         "expected_revision": stale_rev})
        self.assertEqual(caught.exception.reason_code, "REVISION_CONFLICT")

        after_conflict, _ = arv.read_autonomy_rhythm_state(self.root)
        self.assertEqual(int(after_conflict["revision"]), stale_rev + 1)
        self.assertEqual(after_conflict["preferences"]["away_delay_minutes"], 60)

        applied = self._apply({"action": "update_preferences",
                               "away_delay_minutes": 90,
                               "expected_revision": stale_rev + 1})
        self.assertEqual(applied["revision"], stale_rev + 2)
        self.assertEqual(applied["preferences"]["away_delay_minutes"], 90)

    def test_threaded_cas_on_one_snapshot_admits_exactly_one_writer(self) -> None:
        writer_count = 8
        barrier = threading.Barrier(writer_count)
        results: list[tuple[str, object]] = []
        lock = threading.Lock()

        def _worker(index: int) -> None:
            state, _ = arv.read_autonomy_rhythm_state(self.root)
            snapshot_rev = int(state["revision"])
            try:
                barrier.wait(timeout=10)
                out = self._apply({"action": "update_preferences",
                                   "away_delay_minutes": 15 + index,
                                   "expected_revision": snapshot_rev})
                with lock:
                    results.append(("ok", out["revision"]))
            except arv.AutonomyRhythmConflictError as exc:
                with lock:
                    results.append(("conflict", exc.reason_code))
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append(("error", repr(exc)))

        threads = [threading.Thread(target=_worker, args=(i,))
                   for i in range(writer_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([r for r in results if r[0] == "error"], results)
        self.assertEqual(len([r for r in results if r[0] == "ok"]), 1)
        self.assertEqual(len([r for r in results if r[0] == "conflict"]),
                         writer_count - 1)
        self.assertTrue(all(r[1] == "REVISION_CONFLICT"
                            for r in results if r[0] == "conflict"))
        final, _ = arv.read_autonomy_rhythm_state(self.root)
        self.assertEqual(int(final["revision"]), 1)

    def test_tick_on_absent_state_is_zero_write(self) -> None:
        self.assertIsNone(arv.tick_autonomy_rhythm_readiness(self.root))
        self.assertFalse(arv.autonomy_rhythm_state_path(self.root).exists())


class SomaticComfortRmwConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rmw-somatic-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _seed_lived_needs(self) -> None:
        ack = somatic_view.apply_lived_needs_relief(
            self.root, receipt_id="seed-ack", now_iso=T0
        )
        self.assertTrue(ack["applied"])

    def _prepare_taken_drink(self) -> int:
        code, out = somatic_view.mutate_somatic_comfort_view(
            self.root,
            {"action": "offer", "slot": "drink", "kind": "warm_water",
             "actor_side": "partner", "position": "desk_side"},
            now_iso=T0,
        )
        self.assertEqual(code, 200, out)
        code, out = somatic_view.mutate_somatic_comfort_view(
            self.root, {"action": "take", "slot": "drink"}, now_iso=T0
        )
        self.assertEqual(code, 200, out)
        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["care"]["drink"]["state"], "taken")
        return int(state["revision"])

    def _thirst(self) -> float:
        levels = somatic_view.read_lived_needs_snapshot(self.root)["levels"]
        return float(levels["thirst"])

    def test_concurrent_drink_care_consumes_and_relieves_exactly_once(self) -> None:
        self._seed_lived_needs()
        taken_revision = self._prepare_taken_drink()
        thirst_before = self._thirst()

        drinkers = 5
        barrier = threading.Barrier(drinkers)
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def _drink(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                code, out = somatic_view.drink_care(
                    self.root, operation_key=f"op-{index}", now_iso=T_LATER
                )
                with lock:
                    results.append((code, out))
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append((-1, {"error": repr(exc)}))

        threads = [threading.Thread(target=_drink, args=(i,))
                   for i in range(drinkers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([r for r in results if r[0] != 200], results)
        applied = [out for code, out in results if out.get("status") == "applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(len([out for _code, out in results if out.get("replayed")]),
                         drinkers - 1)

        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        drink = state["care"]["drink"]
        self.assertEqual(drink["state"], "consumed")
        self.assertIsNotNone(drink["consumed_at"])
        self.assertEqual(int(state["revision"]), taken_revision + 1)

        recipe = somatic_view.DRINK_NUTRIENT_RECIPES["warm_water"]
        self.assertAlmostEqual(thirst_before - self._thirst(),
                               float(recipe["thirst_relief"]), places=6)

        acks_path = self.root / "body" / "lived_needs_acks.json"
        acks = json.loads(acks_path.read_text(encoding="utf-8"))
        self.assertEqual(len(acks["acks"]), 2)  # seed ack + exactly one drink ack
        self.assertEqual(int(acks["revision"]), 2)

        self.assertFalse(list((self.root / "phone").glob("*.tmp")))

    def test_concurrent_mutate_drink_reenters_lock_and_consumes_once(self) -> None:
        self._seed_lived_needs()
        taken_revision = self._prepare_taken_drink()
        thirst_before = self._thirst()

        drinkers = 4
        barrier = threading.Barrier(drinkers)
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def _mutate_drink(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                code, out = somatic_view.mutate_somatic_comfort_view(
                    self.root,
                    {"action": "drink", "slot": "drink",
                     "operation_key": f"mut-{index}"},
                    now_iso=T_LATER,
                )
                with lock:
                    results.append((code, out))
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append((-1, {"error": repr(exc)}))

        threads = [threading.Thread(target=_mutate_drink, args=(i,))
                   for i in range(drinkers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([r for r in results if r[0] != 200], results)
        self.assertEqual(len([out for _c, out in results
                              if out.get("status") == "applied"]), 1)

        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["care"]["drink"]["state"], "consumed")
        self.assertEqual(int(state["revision"]), taken_revision + 1)

        recipe = somatic_view.DRINK_NUTRIENT_RECIPES["warm_water"]
        self.assertAlmostEqual(thirst_before - self._thirst(),
                               float(recipe["thirst_relief"]), places=6)

    def test_concurrent_mutate_window_writes_lose_no_update(self) -> None:
        somatic_view.write_state(self.root, somatic_view.empty_state())
        base, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        base_revision = int(base["revision"])

        targets = ["open", "ajar", "closed", "open", "ajar", "closed", "open", "ajar"]
        barrier = threading.Barrier(len(targets))
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def _set_window(value: str) -> None:
            try:
                barrier.wait(timeout=10)
                code, out = somatic_view.mutate_somatic_comfort_view(
                    self.root, {"action": "set_window", "state": value},
                    now_iso=T0,
                )
                with lock:
                    results.append((code, out))
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append((-1, {"error": repr(exc)}))

        threads = [threading.Thread(target=_set_window, args=(v,)) for v in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([r for r in results if r[0] != 200], results)
        changed = [out for _code, out in results if out.get("changed") is True]
        changed_revisions = [int(out["revision"]) for out in changed]
        self.assertEqual(len(changed_revisions), len(set(changed_revisions)))

        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertIn(state["window"]["state"], somatic_view.WINDOW_STATES)
        self.assertEqual(int(state["revision"]), base_revision + len(changed))
        self.assertEqual(int(state["revision"]), max(changed_revisions))
        self.assertFalse(list((self.root / "phone").glob("*.tmp")))

    def test_read_paths_stay_zero_write_and_lock_free(self) -> None:
        self.assertFalse((self.root / "phone").exists())
        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(int(state["revision"]), 0)
        self.assertEqual(somatic_view.read_lived_needs_snapshot(self.root)["status"],
                         "unconfigured")
        self.assertFalse((self.root / "phone").exists())


if __name__ == "__main__":
    unittest.main()
