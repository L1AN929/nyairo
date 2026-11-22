#!/usr/bin/env python3
"""PhoneService 的 RMW 收敛：旧的未加锁旁路全部改走共享锁边界。

覆盖三处曾经绕开 ``state_tx_guard.state_file_lock`` 的 service 写入者：

* ``PhoneService.autonomy_rhythm_action`` 现在直接调用视图的
  ``apply_autonomy_rhythm_action``（同一个 ``state_file_lock`` 读-检查-改-写），
  因此调用方携带的 ``expected_revision`` 会被真正执行：旧版本并发写只有一个能过，
  其余返回 ``rejected`` / ``REVISION_CONFLICT``；
* ``PhoneService.body_garment_window_action`` 整段读-改-写裹进
  ``state_file_lock(view.state_path(root))``，多线程并发不丢更新、版本严格单调、
  落盘 JSON 不损坏；
* ``PhoneService.body_care_action`` 的非 drink 动作同样持锁；``drink`` 先在锁外
  调用一次幂等的世界 owner ``use``（同一 operation key），再持锁重读并校验
  ``offer_id`` / ``state == "taken"`` 后才标记，保证同一份照顾只被消费一次。

所有用例只用本机线程与注入的假 World / Shopping，不依赖 POSIX owner 栈。
"""
from __future__ import annotations

import copy
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
from components.phone import service as phone_service  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402

T0 = "2026-11-11T12:00:00+08:00"

WORLD = {
    "ok": True,
    "status": "applied",
    "data": {
        "location": {"status": "available", "place_id": "home", "area_id": "bedroom",
                     "home": True},
        "world": {"local_time": T0, "local_date": "2026-11-11", "revision": 7},
        "local_environment": {
            "status": "available",
            "furniture": [{"object_id": "desk-1", "kind": "table", "name": "书桌"}],
            "light": {"status": "available", "power": "on", "band": "moderate"},
        },
        "travel": {"status": "idle"},
    },
}


class _FakeWorld:
    """A structurally real snapshot; every read is a copy, never shared state."""

    def snapshot(self) -> dict:
        return copy.deepcopy(WORLD)


class _FakeShopping:
    """``PhoneShopping.use`` semantics: one operation key applies exactly once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ops: dict[str, dict] = {}
        self.applied_calls = 0

    def use(self, item_instance_id: str, operation_key: str) -> dict:
        with self._lock:
            stored = self._ops.get(operation_key)
            if stored is not None:
                return {"ok": True, "status": phone_service.STATUS_ALREADY,
                        "data": copy.deepcopy(stored)}
            self.applied_calls += 1
            stored = {
                "receipt": {"receipt_id": "rcpt_%d" % self.applied_calls},
                "applied_to_body": {"thirst": 0.2},
                "item_instance_id": item_instance_id,
            }
            self._ops[operation_key] = stored
            return {"ok": True, "status": phone_service.STATUS_OK,
                    "data": copy.deepcopy(stored)}

    def inventory(self) -> dict:
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {"items": []}}


class _ServiceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="v08-service-rmw-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _service(self, shopping: _FakeShopping | None = None) -> phone_service.PhoneService:
        instance = {
            "root": str(self.root),
            "subject": "chiyo",
            "world_home": str(self.root / "world_home"),
            "supply_socket": "unix:///tmp/v08-service-rmw-not-used.sock",
            "private_items_artifact_id": "artifact-v08",
            "artifact_grant_id": "grant-v08",
            "workspace_id": "workspace-v08",
        }
        service = phone_service.PhoneService(
            instance,
            environment={},
            shopping=shopping if shopping is not None else _FakeShopping(),
            world=_FakeWorld(),
        )
        service._somatic_now_iso = lambda: T0  # one fixed business clock
        return service

    def _state(self) -> dict:
        state, reason = somatic_view.read_state(self.root)
        self.assertIsNone(reason)
        return state


class AutonomyRhythmServiceConvergenceTest(_ServiceFixture):
    """``autonomy_rhythm_action`` must go through the view's locked channel."""

    def _service_with_live_context(self) -> phone_service.PhoneService:
        service = self._service()
        # The live context is a pure read; this fixture only needs the action channel.
        service._autonomy_live_context = lambda: {
            "character_state": None,
            "world_result": None,
            "body_view": None,
            "desk_result": None,
            "somatic_state": None,
            "sensory_projection": None,
            "now_iso": T0,
        }
        return service

    def test_action_applies_and_stale_expected_revision_is_rejected(self) -> None:
        service = self._service_with_live_context()

        first = service.autonomy_rhythm_action(
            action="set_phone_aside", payload={"mode": "aside", "minutes": 30})
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], phone_service.STATUS_OK)
        first_rev = int(first["data"]["revision"])
        self.assertEqual(first_rev, 1)
        self.assertEqual(first["data"]["action"], "set_phone_aside")
        self.assertTrue(first["data"]["changed"])
        self.assertNotIn("ok", first["data"])

        stale = service.autonomy_rhythm_action(
            action="update_preferences",
            payload={"away_delay_minutes": 90, "expected_revision": first_rev - 1})
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(stale["status"], phone_service.STATUS_REJECTED)
        self.assertEqual(stale["reason_code"], "REVISION_CONFLICT")

        unchanged, reason = arv.read_autonomy_rhythm_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(int(unchanged["revision"]), first_rev)
        self.assertNotEqual(unchanged["preferences"]["away_delay_minutes"], 90)

        accepted = service.autonomy_rhythm_action(
            action="update_preferences",
            payload={"away_delay_minutes": 90, "expected_revision": first_rev})
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(int(accepted["data"]["revision"]), first_rev + 1)

    def test_threaded_cas_on_one_snapshot_admits_exactly_one_service_writer(self) -> None:
        service = self._service_with_live_context()
        service.autonomy_rhythm_action(
            action="set_phone_aside", payload={"mode": "aside", "minutes": 30})
        snapshot, reason = arv.read_autonomy_rhythm_state(self.root)
        self.assertIsNone(reason)
        snapshot_rev = int(snapshot["revision"])

        writers = 8
        barrier = threading.Barrier(writers)
        results: list[tuple[str, object]] = []
        lock = threading.Lock()

        def _worker(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                out = service.autonomy_rhythm_action(
                    action="update_preferences",
                    payload={"away_delay_minutes": 15 + index,
                             "expected_revision": snapshot_rev})
                with lock:
                    results.append(("ok" if out.get("ok") else "rejected",
                                    out.get("reason_code")))
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                with lock:
                    results.append(("error", repr(exc)))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([row for row in results if row[0] == "error"], results)
        self.assertEqual(len([row for row in results if row[0] == "ok"]), 1)
        self.assertEqual(len([row for row in results if row[0] == "rejected"]),
                         writers - 1)
        self.assertTrue(all(row[1] == "REVISION_CONFLICT"
                            for row in results if row[0] == "rejected"))

        final, reason = arv.read_autonomy_rhythm_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(int(final["revision"]), snapshot_rev + 1)


class SomaticServiceConvergenceTest(_ServiceFixture):
    """Garment / care writes share one locked JSON file."""

    def test_concurrent_window_actions_lose_no_update(self) -> None:
        service = self._service()
        somatic_view.write_state(self.root, somatic_view.empty_state())
        base_rev = int(self._state()["revision"])

        targets = ["open", "ajar", "closed", "open", "ajar", "closed", "open", "ajar"]
        barrier = threading.Barrier(len(targets))
        results: list[dict] = []
        lock = threading.Lock()

        def _set_window(value: str) -> None:
            try:
                barrier.wait(timeout=10)
                out = service.body_garment_window_action(
                    action="set_window", state_value=value)
                with lock:
                    results.append(out)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append({"ok": False, "error": repr(exc)})

        threads = [threading.Thread(target=_set_window, args=(value,))
                   for value in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([row for row in results if not row.get("ok")], results)
        changed = [row for row in results if row["data"].get("changed") is True]
        changed_revisions = [int(row["data"]["revision"]) for row in changed]
        self.assertEqual(len(changed_revisions), len(set(changed_revisions)))

        final = self._state()  # valid JSON, no torn write
        self.assertIn(final["window"]["state"], somatic_view.WINDOW_STATES)
        self.assertEqual(int(final["revision"]), base_rev + len(changed))
        self.assertEqual(int(final["revision"]), max(changed_revisions))
        self.assertFalse(list((self.root / "phone").glob("*.tmp")))

    def test_mixed_garment_and_care_writes_keep_versions_strictly_monotonic(self) -> None:
        service = self._service()
        somatic_view.write_state(self.root, somatic_view.empty_state())
        base_rev = int(self._state()["revision"])

        jobs: list[tuple[str, dict]] = [
            ("window", {"action": "set_window", "state_value": "open"}),
            ("care", {"action": "offer", "slot": "cushion", "kind": "floor_cushion",
                      "actor_side": "partner", "position": "hand_side"}),
            ("window", {"action": "set_window", "state_value": "ajar"}),
            ("care", {"action": "offer", "slot": "cushion", "kind": "floor_cushion",
                      "actor_side": "her", "position": "hand_side"}),
            ("window", {"action": "set_window", "state_value": "closed"}),
        ]
        barrier = threading.Barrier(len(jobs))
        results: list[dict] = []
        lock = threading.Lock()

        def _run(kind: str, kwargs: dict) -> None:
            try:
                barrier.wait(timeout=10)
                if kind == "window":
                    out = service.body_garment_window_action(**kwargs)
                else:
                    out = service.body_care_action(**kwargs)
                with lock:
                    results.append(out)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append({"ok": False, "error": repr(exc)})

        threads = [threading.Thread(target=_run, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([row for row in results if not row.get("ok")], results)
        changed = [row for row in results if row["data"].get("changed") is True]
        revisions = sorted(int(row["data"]["revision"]) for row in changed)
        final = self._state()
        final_rev = int(final["revision"])
        self.assertEqual(revisions, list(range(base_rev + 1, final_rev + 1)))
        self.assertFalse(list((self.root / "phone").glob("*.tmp")))

    def _seed_taken_drink(self, service: phone_service.PhoneService) -> int:
        state = somatic_view.empty_state()
        state, changed, error, _offer = somatic_view.offer_care(
            state, slot="drink", kind="warm_water", actor_side="partner",
            position="hand_side", place_id="home", area_id="bedroom", now_iso=T0,
            bound_item={"item_instance_id": "water-1", "label": "温水"})
        self.assertTrue(changed)
        self.assertIsNone(error)
        somatic_view.write_state(self.root, state)

        taken = service.body_care_action(action="take", slot="drink")
        self.assertTrue(taken["ok"], taken)
        self.assertTrue(taken["data"]["changed"], taken)
        state = self._state()
        self.assertEqual(state["care"]["drink"]["state"], "taken")
        return int(state["revision"])

    def test_concurrent_drink_actions_consume_the_same_care_exactly_once(self) -> None:
        shopping = _FakeShopping()
        service = self._service(shopping=shopping)
        taken_rev = self._seed_taken_drink(service)

        drinkers = 6
        barrier = threading.Barrier(drinkers)
        results: list[dict] = []
        lock = threading.Lock()

        def _drink(_index: int) -> None:
            try:
                barrier.wait(timeout=10)
                out = service.body_care_action(action="drink", slot="drink")
                with lock:
                    results.append(out)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    results.append({"ok": False, "error": repr(exc)})

        threads = [threading.Thread(target=_drink, args=(i,)) for i in range(drinkers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse([row for row in results if not row.get("ok")], results)
        self.assertEqual(shopping.applied_calls, 1)
        changed = [row for row in results if row["data"].get("changed") is True]
        self.assertEqual(len(changed), 1, results)
        self.assertTrue(all(row["data"]["consumed"] is True for row in results))

        final = self._state()
        self.assertEqual(final["care"]["drink"]["state"], "consumed")
        self.assertIsNotNone(final["care"]["drink"]["consumed_at"])
        self.assertEqual(int(final["revision"]), taken_rev + 1)
        self.assertFalse(list((self.root / "phone").glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
