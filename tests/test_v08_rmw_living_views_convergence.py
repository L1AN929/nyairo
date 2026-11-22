#!/usr/bin/env python3
"""居家 / 外出 / 长程视图的 RMW 收敛：整段「读-校验-改-写」共持一把 ``state_file_lock``。

S1/S2 已经把 ``home_hearth_view`` / ``workbench_curio_view`` / ``wardrobe_pocket_view`` /
``outing_places_view`` / ``long_companionship_view`` / ``window_keepsake_view`` 的
``write_*_state`` 内层加进了 ``state_tx_guard.state_file_lock``（进程内可重入 RLock +
OS 排他锁）。本套用验证 ``PhoneService`` 的外层 action 方法同样把读取与校验包进
同一把锁：多线程同时进入时不会读到过期快照，``revision`` 严格连号递增、无覆盖丢失。

同时验证这些通道的只读 GET 仍然是零写：不生成 ``.lock``，不改任何状态文件字节。
"""

from __future__ import annotations

import copy
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

from components.phone import home_hearth_view as hearth_view  # noqa: E402
from components.phone import long_companionship_view as longterm_view  # noqa: E402
from components.phone import outing_places_view as outing_view  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import wardrobe_pocket_view as wardrobe_view  # noqa: E402

T0 = "2026-11-11T12:00:00+08:00"
WRITERS = 6

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
    """Structural snapshot; every read returns a deep copy."""

    def snapshot(self) -> dict:
        return copy.deepcopy(WORLD)


class _FakeShopping:
    """Read-only inventory surface used by recipe references."""

    def inventory(self) -> dict:
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {"items": []}}

    def use(self, item_instance_id: str, operation_key: str) -> dict:  # pragma: no cover
        return {"ok": False, "reason_code": "NOT_USED"}


class _ServiceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="v08-rmw-living-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _service(self) -> phone_service.PhoneService:
        instance = {
            "root": str(self.root),
            "subject": "chiyo",
            "world_home": str(self.root / "world_home"),
            "supply_socket": "unix:///tmp/v08-rmw-living-not-used.sock",
            "private_items_artifact_id": "artifact-v08",
            "artifact_grant_id": "grant-v08",
            "workspace_id": "workspace-v08",
        }
        service = phone_service.PhoneService(
            instance,
            environment={},
            shopping=_FakeShopping(),
            world=_FakeWorld(),
        )
        service._somatic_now_iso = lambda: T0
        return service

    # -- concurrency driver ---------------------------------------------------------------

    def _run_concurrent(self, call, jobs: list[dict]) -> list[dict]:
        barrier = threading.Barrier(len(jobs))
        results: list[dict] = []
        guard = threading.Lock()

        def _worker(job: dict) -> None:
            try:
                barrier.wait(timeout=10)
                out = call(**job)
                with guard:
                    results.append(out)
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                with guard:
                    results.append({"ok": False, "error": repr(exc)})

        threads = [threading.Thread(target=_worker, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def _assert_contiguous(self, results: list[dict], base_rev: int,
                           final_rev: int) -> list[int]:
        self.assertFalse([row for row in results if row.get("error")], results)
        self.assertFalse([row for row in results if not row.get("ok")], results)
        self.assertTrue(all(row.get("status") == phone_service.STATUS_OK
                            for row in results), results)
        revisions = sorted(int(row["data"]["revision"]) for row in results)
        self.assertEqual(revisions, list(range(base_rev + 1, base_rev + len(results) + 1)))
        self.assertEqual(final_rev, base_rev + len(results))
        self.assertFalse(list(self.root.rglob("*.tmp")))
        return revisions

    # -- representative write channels ----------------------------------------------------

    def test_home_recipe_upsert_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(hearth_view.read_recipes_state(self.root)[0]["revision"])
        jobs = [{"recipe_id": "recipe-%d" % i, "title": "菜谱 %d" % i,
                 "memo": "备忘 %d" % i, "author_side": "her"}
                for i in range(WRITERS)]
        results = self._run_concurrent(service.home_recipe_upsert, jobs)
        final_rev = int(hearth_view.read_recipes_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)
        cards = hearth_view.read_recipes_state(self.root)[0]["recipes"]
        self.assertEqual(len(cards), WRITERS)

    def test_home_tentative_plan_action_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(hearth_view.read_plans_state(self.root)[0]["revision"])
        jobs = [{"action": "propose", "actor_is_her": True, "plan_id": "plan-%d" % i,
                 "title": "小计划 %d" % i, "window_note": "傍晚", "proposer": "her"}
                for i in range(WRITERS)]
        results = self._run_concurrent(service.home_tentative_plan_action, jobs)
        final_rev = int(hearth_view.read_plans_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)
        self.assertEqual(len(hearth_view.read_plans_state(self.root)[0]["plans"]), WRITERS)

    def test_outing_bench_bookstore_action_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(outing_view.read_bench_bookstore_state(self.root)[0]["revision"])
        jobs = [{"action": "set_partner_window_note", "slot_index": 0,
                 "note": "窗口便签 %d" % i} for i in range(WRITERS)]
        results = self._run_concurrent(service.outing_bench_bookstore_action, jobs)
        final_rev = int(outing_view.read_bench_bookstore_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)

    def test_outing_routes_market_action_converges_without_lost_update(self) -> None:
        service = self._service()
        stall_code = sorted(outing_view.STALL_CODES)[0]
        base_rev = int(outing_view.read_routes_market_state(self.root)[0]["revision"])
        jobs = [{"action": "note_market_wish", "stall_code": stall_code,
                 "memo": "集市愿望 %d" % i, "shared": True} for i in range(WRITERS)]
        results = self._run_concurrent(service.outing_routes_market_action, jobs)
        final_rev = int(outing_view.read_routes_market_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)

    def test_longterm_first_room_habits_action_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(longterm_view.read_room_habits_state(self.root)[0]["revision"])
        jobs = [{"action": "upsert_habit", "title": "共同习惯 %d" % i,
                 "habit_text": "内容 %d" % i, "author_side": "her"}
                for i in range(WRITERS)]
        results = self._run_concurrent(service.longterm_first_room_habits_action, jobs)
        final_rev = int(longterm_view.read_room_habits_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)
        habits = longterm_view.read_room_habits_state(self.root)[0]["shared_habits"]
        self.assertEqual(len(habits), WRITERS)

    def test_home_wardrobe_action_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(wardrobe_view.read_wardrobe_state(self.root)[0]["revision"])
        jobs = [{"action": "set", "values": {"pairing_note": "搭配 %d" % i}}
                for i in range(WRITERS)]
        results = self._run_concurrent(service.home_wardrobe_action, jobs)
        final_rev = int(wardrobe_view.read_wardrobe_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)

    def test_pocket_lists_action_converges_without_lost_update(self) -> None:
        service = self._service()
        base_rev = int(wardrobe_view.read_pocket_state(self.root)[0]["revision"])
        jobs = [{"action": "add", "kind": "want_to_try",
                 "values": {"title": "想试 %d" % i},
                 "operation_key": "op-%d" % i} for i in range(WRITERS)]
        results = self._run_concurrent(service.pocket_lists_action, jobs)
        final_rev = int(wardrobe_view.read_pocket_state(self.root)[0]["revision"])
        self._assert_contiguous(results, base_rev, final_rev)
        self.assertEqual(
            len(wardrobe_view.read_pocket_state(self.root)[0]["lists"]["want_to_try"]),
            WRITERS)

    # -- read-only channels must stay zero-write ------------------------------------------

    @staticmethod
    def _file_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
        rows: dict[str, tuple[int, int]] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                rows[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
        return rows

    def test_read_only_gets_write_zero_bytes_and_create_no_lock(self) -> None:
        service = self._service()
        readers = [
            service.home_recipes,
            service.home_tentative_plans,
            service.outing_bench_bookstore,
            service.outing_routes_market,
            service.longterm_first_room_habits,
            service.home_wardrobe,
            service.pocket_lists,
        ]
        # 预热一次：任何一次性 owner 迁移/懒加载都不计作「读侧写入」。
        for reader in readers:
            reader()
        before = self._file_fingerprint(self.root)

        for reader in readers:
            answer = reader()
            self.assertTrue(answer.get("ok"), answer)

        after = self._file_fingerprint(self.root)
        self.assertEqual(before, after)
        self.assertFalse(list(self.root.rglob("*.lock")))


if __name__ == "__main__":
    unittest.main()
