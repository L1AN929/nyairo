#!/usr/bin/env python3
"""房间外六项日常切片：纯投影状态机 + 真实 owner loopback HTTP 验证。

上半部分覆盖纯函数：确定性橱窗/集市轮换、World 户外地点分类、长椅重访与可见性、
散步路线与随手拍脱敏、虚拟预算集市心愿、小展廊双视角投影、双人新地点蓝图与冻结文件 SHA256。
下半部分（POSIX）通过 ``outing_places_fixture.build_stack`` 启动真实 World / Supply /
economy / 相册 / 多版本作品与本地投递分享，走真实 loopback HTTP 验证三条读面与写面。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import outing_places_view as view  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "{{SECRET_op8k3m9z}}"
NOW = "2026-11-11T15:00:00+08:00"
TODAY = "2026-11-11"

FROZEN_SHA256 = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}


class PureBenchBookstoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="outing-bench-pure-"))

    def test_window_and_market_rotations_are_deterministic_and_distinct(self):
        slots_a = view.bookstore_window_for_date("2026-11-11")
        slots_b = view.bookstore_window_for_date("2026-11-11")
        slots_next = view.bookstore_window_for_date("2026-11-12")
        self.assertEqual(slots_a, slots_b)
        self.assertEqual(len({s["book_code"] for s in slots_a}), 3)
        self.assertNotEqual([s["book_code"] for s in slots_a],
                            [s["book_code"] for s in slots_next])

        stalls_a = view.market_stalls_for_date("2026-11-11")
        stalls_next = view.market_stalls_for_date("2026-11-12")
        self.assertEqual(len({s["stall_code"] for s in stalls_a}), 3)
        self.assertNotEqual([s["stall_code"] for s in stalls_a],
                            [s["stall_code"] for s in stalls_next])

    def test_classify_world_places_excludes_home_and_detects_bench_and_bookstore(self):
        classified = view.classify_world_places([
            {"place_id": "home", "name": "家", "kind": "residence"},
            {"place_id": "beach", "name": "海边木栈道", "kind": "scenic"},
            {"place_id": "bookstore", "name": "海风古书店", "kind": "shop"},
            {"place_id": "cafe", "name": "街角咖啡馆", "kind": "shop"},
        ], home_place_id="home")
        outdoor_ids = [p["place_id"] for p in classified["outdoor_places"]]
        self.assertEqual(outdoor_ids, ["beach", "bookstore", "cafe"])
        bench_ids = [p["place_id"] for p in classified["bench_candidates"]]
        self.assertIn("beach", bench_ids)
        self.assertTrue(classified["bookstore_in_world"])
        self.assertEqual(classified["bookstore_place_id"], "bookstore")

    def test_bench_visit_idempotency_visibility_cap_and_corrupt_fail_closed(self):
        state = view.empty_bench_bookstore_state()
        state, item, changed = view.apply_log_bench_visit(
            state, place_id="beach", valid_place_ids={"beach", "bookstore"},
            operation_key="bench-op-1", note="海风刚好", shared=False, now_iso=NOW)
        self.assertTrue(changed)
        self.assertEqual(state["revision"], 1)
        replay, _, changed_replay = view.apply_log_bench_visit(
            state, place_id="beach", valid_place_ids={"beach", "bookstore"},
            operation_key="bench-op-1", note="海风刚好", shared=False,
            now_iso="2026-11-11T16:00:00+08:00")
        self.assertFalse(changed_replay)
        self.assertEqual(replay["revision"], 1)

        with self.assertRaises(view.OutingStateError) as bad_place:
            view.apply_log_bench_visit(
                state, place_id="home", valid_place_ids={"beach"},
                operation_key="bench-op-home", now_iso=NOW)
        self.assertEqual(bad_place.exception.reason_code, "PLACE_NOT_IN_WORLD")

        partner_proj = view.bench_bookstore_block(
            state=state, state_reason=None,
            world_places=view.classify_world_places([{"place_id": "beach", "name": "海滩"}]),
            world_available=True, world_reason=None, local_date=TODAY, subject_view=False)
        self.assertEqual(partner_proj["bench"]["visits"], [])
        self.assertTrue(partner_proj["bench"]["no_streak_score"])
        self.assertFalse(partner_proj["bookstore_window"]["reading_completed"])
        self.assertFalse(partner_proj["bookstore_window"]["model_calls"])

        toggled, _, changed_vis = view.apply_set_bench_visibility(
            state, visit_id=item["visit_id"], shared=True, now_iso=NOW)
        self.assertTrue(changed_vis)
        partner_after = view.bench_bookstore_block(
            state=toggled, state_reason=None,
            world_places=view.classify_world_places([{"place_id": "beach", "name": "海滩"}]),
            world_available=True, world_reason=None, local_date=TODAY, subject_view=False)
        self.assertEqual(len(partner_after["bench"]["visits"]), 1)
        self.assertNotIn("operation_key",
                         json.dumps(partner_after["bench"]["visits"], ensure_ascii=False))

        view.write_bench_bookstore_state(self.root, toggled)
        loaded, reason = view.read_bench_bookstore_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(loaded["revision"], 2)
        view.bench_bookstore_state_path(self.root).write_text("{corrupt", encoding="utf-8")
        fallback, reason = view.read_bench_bookstore_state(self.root)
        self.assertEqual(reason, "BENCH_BOOKSTORE_STATE_UNREADABLE")
        self.assertEqual(fallback, view.empty_bench_bookstore_state())

    def test_window_bookmark_and_partner_note_projection(self):
        state = view.empty_bench_bookstore_state()
        state, bk, changed = view.apply_bookmark_window_book(
            state, slot_index=1, local_date=TODAY, shared=False,
            note="想周末翻翻", note_supplied=True, now_iso=NOW)
        self.assertTrue(changed)
        state, pnote, changed_p = view.apply_set_partner_window_note(
            state, slot_index=0, local_date=TODAY, note="这本插画很适合雨天看", now_iso=NOW)
        self.assertTrue(changed_p)
        self.assertTrue(pnote["partner_contributed"])

        partner_proj = view.bench_bookstore_block(
            state=state, state_reason=None, world_places=view.classify_world_places(()),
            world_available=True, world_reason=None, local_date=TODAY, subject_view=False)
        self.assertEqual(partner_proj["bookstore_window"]["her_bookmarks"], [])
        self.assertEqual(partner_proj["bookstore_window"]["partner_window_note"]["note"],
                         "这本插画很适合雨天看")
        state, bk2, _ = view.apply_bookmark_window_book(
            state, slot_index=1, local_date=TODAY, shared=True,
            note="想周末翻翻", note_supplied=False, now_iso=NOW)
        self.assertTrue(bk2["shared"])
        partner_shared = view.bench_bookstore_block(
            state=state, state_reason=None, world_places=view.classify_world_places(()),
            world_available=True, world_reason=None, local_date=TODAY, subject_view=False)
        her_shared = partner_shared["bookstore_window"]["her_bookmarks"]
        self.assertEqual(len(her_shared), 1)
        self.assertNotIn("想周末翻翻", json.dumps(her_shared, ensure_ascii=False))


class PureRoutesMarketTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="outing-routes-pure-"))

    def test_route_validation_and_partner_album_id_scrubbing(self):
        state = view.empty_routes_market_state()
        with self.assertRaises(view.OutingStateError) as dup:
            view.apply_save_walk_route(
                state, title="重复站点", place_ids=["beach", "beach"],
                valid_place_ids={"beach", "cafe"}, now_iso=NOW)
        self.assertEqual(dup.exception.reason_code, "ROUTE_PLACES_DUPLICATED")

        state, route, changed = view.apply_save_walk_route(
            state, title="傍晚看海散步", place_ids=["bookstore", "beach"],
            valid_place_ids={"bookstore", "beach", "cafe"},
            album_artifact_id="album_secret_001", album_supplied=True,
            caption="晚霞落在水面上", caption_supplied=True, shared=True, now_iso=NOW)
        self.assertTrue(changed)
        partner_proj = view.routes_market_block(
            state=state, state_reason=None,
            world_places=view.classify_world_places([
                {"place_id": "bookstore", "name": "书店"},
                {"place_id": "beach", "name": "海滩"},
            ]),
            world_available=True, world_reason=None,
            album_readability={"album_secret_001": True},
            wallet={"wallet_status": "unavailable",
                    "reason_code": view.GENESIS_NOT_INITIALIZED},
            local_date=TODAY, subject_view=False)
        self.assertEqual(len(partner_proj["routes"]), 1)
        self.assertEqual(partner_proj["routes"][0]["photo_status"], "available")
        self.assertNotIn("album_secret_001", json.dumps(partner_proj, ensure_ascii=False))
        self.assertNotIn("album_artifact_id", json.dumps(partner_proj, ensure_ascii=False))
        self.assertIsNone(partner_proj["festival_market"]["balance_jpy"])
        self.assertEqual(partner_proj["festival_market"]["wallet_reason_code"],
                         view.GENESIS_NOT_INITIALIZED)
        self.assertTrue(partner_proj["festival_market"]["virtual_currency_only"])
        self.assertTrue(partner_proj["festival_market"]["does_not_debit_wallet"])

    def test_market_wish_within_balance_and_corrupt_state(self):
        state = view.empty_routes_market_state()
        stall_code = view.MARKET_STALL_CATALOG[0]["stall_code"]
        state, wish, changed = view.apply_note_market_wish(
            state, stall_code=stall_code, memo="留着买一枚银杏书签",
            budget_cap_jpy=300, budget_supplied=True, shared=True, now_iso=NOW)
        self.assertTrue(changed)
        subject_proj = view.routes_market_block(
            state=state, state_reason=None, world_places=view.classify_world_places(()),
            world_available=True, world_reason=None, album_readability={},
            wallet={"wallet_status": "available", "balance_jpy": 500, "reason_code": None},
            local_date=TODAY, subject_view=True)
        self.assertTrue(subject_proj["festival_market"]["her_wishes"][0]["within_current_balance"])
        partner_proj = view.routes_market_block(
            state=state, state_reason=None, world_places=view.classify_world_places(()),
            world_available=True, world_reason=None, album_readability={},
            wallet={"wallet_status": "available", "balance_jpy": 500, "reason_code": None},
            local_date=TODAY, subject_view=False)
        self.assertNotIn("留着买一枚银杏书签",
                         json.dumps(partner_proj["festival_market"]["her_wishes"],
                                    ensure_ascii=False))

        view.routes_market_state_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        view.routes_market_state_path(self.root).write_text("[]", encoding="utf-8")
        _, reason = view.read_routes_market_state(self.root)
        self.assertEqual(reason, "ROUTES_MARKET_STATE_INVALID")


class PureGalleryBlueprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="outing-gallery-pure-"))

    def test_blueprint_author_separation_and_active_cap(self):
        state = view.empty_gallery_blueprint_state()
        state, her_card, changed = view.apply_upsert_blueprint(
            state, title="看海小木亭", sketch_note="木栏杆边留一排挂风铃的钩子",
            intended_use="傍晚吹风和画速写", author_side="her", now_iso=NOW)
        self.assertTrue(changed)
        with self.assertRaises(view.OutingStateError) as mismatch:
            view.apply_upsert_blueprint(
                state, blueprint_id=her_card["blueprint_id"], title="改她的图",
                sketch_note="越权修改", intended_use="越权",
                author_side="partner", now_iso=NOW)
        self.assertEqual(mismatch.exception.reason_code, "AUTHOR_MISMATCH")
        with self.assertRaises(view.OutingStateError) as mismatch_arch:
            view.apply_archive_blueprint(
                state, blueprint_id=her_card["blueprint_id"],
                author_side="partner", now_iso=NOW)
        self.assertEqual(mismatch_arch.exception.reason_code, "AUTHOR_MISMATCH")

        for idx in range(5):
            state, _, _ = view.apply_upsert_blueprint(
                state, title=f"新地点草图{idx}", sketch_note="铅笔线稿",
                intended_use="安静待一会儿", author_side="partner", now_iso=NOW)
        with self.assertRaises(view.OutingStateError) as full_err:
            view.apply_upsert_blueprint(
                state, title="第七张超限草图", sketch_note="超限",
                intended_use="超限", author_side="partner", now_iso=NOW)
        self.assertEqual(full_err.exception.reason_code, "BLUEPRINTS_FULL")

    def test_exhibit_projection_withholds_undelivered_for_partner(self):
        exhibit = {
            "exhibit_id": "exhibit-1",
            "artifact_id": "proj_1",
            "version_id": "ver_3",
            "wall_caption": "未投递版本的展签",
            "curated_at": NOW,
        }
        withheld = view.format_exhibit_projection(
            exhibit, subject_view=False, source_status="withheld",
            reason_code=view.VERSION_NOT_DELIVERED)
        self.assertEqual(withheld["status"], "withheld")
        self.assertIsNone(withheld["excerpt"])
        self.assertIsNone(withheld["wall_caption"])
        self.assertNotIn("artifact_id", withheld)

        view.gallery_blueprint_state_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        view.gallery_blueprint_state_path(self.root).write_text("{bad", encoding="utf-8")
        _, reason = view.read_gallery_blueprint_state(self.root)
        self.assertEqual(reason, "GALLERY_BLUEPRINT_STATE_UNREADABLE")

    def test_bench_visits_cap_is_enforced_at_twelve(self):
        state = view.empty_bench_bookstore_state()
        for idx in range(view.MAX_BENCH_VISITS):
            state, _, _ = view.apply_log_bench_visit(
                state, place_id="beach", valid_place_ids={"beach"},
                operation_key=f"op-{idx}", now_iso=NOW)
        with self.assertRaises(view.OutingStateError) as cap_err:
            view.apply_log_bench_visit(
                state, place_id="beach", valid_place_ids={"beach"},
                operation_key="op-overflow", now_iso=NOW)
        self.assertEqual(cap_err.exception.reason_code, "BENCH_VISITS_FULL")

    def test_window_slot_bounds_and_bookmark_replay_noop(self):
        state = view.empty_bench_bookstore_state()
        with self.assertRaises(view.OutingStateError):
            view.apply_bookmark_window_book(
                state, slot_index=3, local_date=TODAY, now_iso=NOW)
        state, _, changed = view.apply_bookmark_window_book(
            state, slot_index=0, local_date=TODAY, shared=True,
            note="留在橱窗前", note_supplied=True, now_iso=NOW)
        self.assertTrue(changed)
        replay, _, changed2 = view.apply_bookmark_window_book(
            state, slot_index=0, local_date=TODAY, shared=True,
            note="留在橱窗前", note_supplied=True, now_iso="2026-11-11T17:00:00+08:00")
        self.assertFalse(changed2)
        self.assertEqual(replay["revision"], state["revision"])

    def test_walk_route_stops_cap_and_twelve_routes_cap(self):
        state = view.empty_routes_market_state()
        valid = {f"p{i}" for i in range(10)}
        with self.assertRaises(view.OutingStateError) as too_many_stops:
            view.apply_save_walk_route(
                state, title="太长的散步", place_ids=[f"p{i}" for i in range(7)],
                valid_place_ids=valid, now_iso=NOW)
        self.assertEqual(too_many_stops.exception.reason_code, "ROUTE_PLACES_INVALID")

        for idx in range(view.MAX_WALK_ROUTES):
            state, _, _ = view.apply_save_walk_route(
                state, title=f"路线-{idx}", place_ids=["p0", "p1"],
                valid_place_ids=valid, now_iso=NOW)
        with self.assertRaises(view.OutingStateError) as full_routes:
            view.apply_save_walk_route(
                state, title="第十三条路线", place_ids=["p0"],
                valid_place_ids=valid, now_iso=NOW)
        self.assertEqual(full_routes.exception.reason_code, "WALK_ROUTES_FULL")

    def test_market_budget_bounds_and_tip_clear_noop(self):
        state = view.empty_routes_market_state()
        stall = view.MARKET_STALL_CATALOG[0]["stall_code"]
        with self.assertRaises(view.OutingStateError):
            view.apply_note_market_wish(
                state, stall_code=stall, memo="负数预算",
                budget_cap_jpy=-10, budget_supplied=True, now_iso=NOW)
        _, cleared = view.apply_clear_partner_market_tip(state, now_iso=NOW)
        self.assertFalse(cleared)

    def test_gallery_exhibit_cap_replay_and_remove_noop(self):
        state = view.empty_gallery_blueprint_state()
        for idx in range(view.MAX_GALLERY_EXHIBITS):
            state, _, _ = view.apply_curate_exhibit(
                state, artifact_id=f"art_{idx}", version_id="v1",
                wall_caption="展签", caption_supplied=True, now_iso=NOW)
        replay, _, changed = view.apply_curate_exhibit(
            state, artifact_id="art_0", version_id="v1",
            wall_caption="展签", caption_supplied=True, now_iso=NOW)
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], state["revision"])
        with self.assertRaises(view.OutingStateError) as full_ex:
            view.apply_curate_exhibit(
                state, artifact_id="art_overflow", version_id="v1", now_iso=NOW)
        self.assertEqual(full_ex.exception.reason_code, "GALLERY_FULL")
        _, rm_missing = view.apply_remove_exhibit(
            state, exhibit_id="exhibit-missing", now_iso=NOW)
        self.assertFalse(rm_missing)

    def test_blueprint_archive_replay_and_reactivate(self):
        state = view.empty_gallery_blueprint_state()
        state, card, _ = view.apply_upsert_blueprint(
            state, title="河畔凉亭", sketch_note="初稿", intended_use="乘凉",
            author_side="partner", now_iso=NOW)
        state, _, changed = view.apply_archive_blueprint(
            state, blueprint_id=card["blueprint_id"], author_side="partner", now_iso=NOW)
        self.assertTrue(changed)
        _, _, replay = view.apply_archive_blueprint(
            state, blueprint_id=card["blueprint_id"], author_side="partner", now_iso=NOW)
        self.assertFalse(replay)
        state, revived, revived_changed = view.apply_upsert_blueprint(
            state, blueprint_id=card["blueprint_id"], title="河畔凉亭二稿",
            sketch_note="加上木桌", intended_use="乘凉喝茶",
            author_side="partner", now_iso=NOW)
        self.assertTrue(revived_changed)
        self.assertEqual(revived["status"], "ACTIVE")

    def test_subject_exhibit_truncates_long_owner_excerpt_cleanly(self):
        long_text = "海" * (view.MAX_GALLERY_EXCERPT + 50)
        proj = view.format_exhibit_projection(
            {"exhibit_id": "ex-long", "artifact_id": "a1", "version_id": "v1",
             "wall_caption": "长文展签", "curated_at": NOW},
            subject_view=True, source_status="available", title="长文",
            version_number=1, owner_content=long_text)
        self.assertEqual(len(proj["excerpt"]), view.MAX_GALLERY_EXCERPT)
        self.assertTrue(proj["excerpt_truncated"])
        self.assertEqual(proj["excerpt_basis"], view.OWNER_EXCERPT_BASIS)

    def test_symlink_state_file_fails_closed_when_supported(self):
        target = self.root / "real_target.json"
        target.write_text("{}", encoding="utf-8")
        link_path = view.bench_bookstore_state_path(self.root)
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.exists():
            link_path.unlink()
        try:
            os.symlink(target, link_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not permitted in this environment")
        _, reason = view.read_bench_bookstore_state(self.root)
        self.assertEqual(reason, "BENCH_BOOKSTORE_STATE_SYMLINK")


class FrozenAnchorsTest(unittest.TestCase):
    def test_frozen_anchor_files_keep_exact_sha256(self):
        for relative, expected in FROZEN_SHA256.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 World / Supply / economy / Life socket 需要 POSIX（WSL/Linux）")


@POSIX_ONLY
class OutingPlacesOwnerHTTPTest(unittest.TestCase):
    """Real owners + real loopback HTTP across all 3 `/api/phone/outing/*` surfaces."""

    @classmethod
    def setUpClass(cls) -> None:
        import outing_places_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="outing-places-http-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN, subject=SUBJECT, endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00")
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)

    def setUp(self) -> None:
        for path in (
            view.bench_bookstore_state_path(self.stack.root),
            view.routes_market_state_path(self.stack.root),
            view.gallery_blueprint_state_path(self.stack.root),
        ):
            if path.exists():
                path.unlink()

    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_pure_gets_do_not_create_state_files_and_reject_bad_capability_or_query(self):
        for endpoint in (
            "/api/phone/outing/bench-bookstore",
            "/api/phone/outing/routes-market",
            "/api/phone/outing/gallery-blueprint",
        ):
            status, payload = self._call("GET", endpoint)
            self.assertEqual(status, 200, payload)
            self.assertEqual(payload["data"]["view"], "partner_default")
            status_bad, _ = self._call("GET", endpoint, capability="wrong-cap-token")
            self.assertEqual(status_bad, 403)
            status_q, q_payload = self._call("GET", endpoint + "?foo=1")
            self.assertEqual(status_q, 400)
            self.assertEqual(q_payload["reason_code"], "UNKNOWN_QUERY")

        self.assertFalse(view.bench_bookstore_state_path(self.stack.root).exists())
        self.assertFalse(view.routes_market_state_path(self.stack.root).exists())
        self.assertFalse(view.gallery_blueprint_state_path(self.stack.root).exists())

    def test_bench_visits_world_verification_visibility_and_idempotency(self):
        bench_place = (self.stack.bench_candidate_ids[0]
                       if self.stack.bench_candidate_ids
                       else self.stack.outdoor_place_ids[0])
        status, payload = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "log_bench_visit", "place_id": "home",
             "operation_key": "bench-home"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "PLACE_NOT_IN_WORLD")

        status, payload = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "log_bench_visit", "place_id": "nonexistent_mars_bench",
             "operation_key": "bench-mars"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "PLACE_NOT_IN_WORLD")

        body = {
            "action": "log_bench_visit",
            "place_id": bench_place,
            "note": "只给自己留的海风笔记",
            "shared": False,
            "operation_key": "bench-real-1",
        }
        status, payload = self._call("POST", "/api/phone/outing/bench-bookstore", body)
        self.assertEqual(status, 403)
        status, payload = self._call("POST", "/api/phone/outing/bench-bookstore", body, csrf=True)
        self.assertEqual(status, 403)

        status, payload = self._call(
            "POST", "/api/phone/outing/bench-bookstore", body,
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        visit_id = payload["data"]["visit_id"]
        rev1 = payload["data"]["revision"]

        status, replay = self._call(
            "POST", "/api/phone/outing/bench-bookstore", body,
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(replay["data"]["revision"], rev1)

        _, partner_get = self._call("GET", "/api/phone/outing/bench-bookstore")
        self.assertEqual(partner_get["data"]["bench"]["visits"], [])
        self.assertNotIn("只给自己留的海风笔记", json.dumps(partner_get, ensure_ascii=False))

        _, subject_get = self._call("GET", "/api/phone/outing/bench-bookstore",
                                    capability=CAP_TOKEN)
        self.assertEqual(len(subject_get["data"]["bench"]["visits"]), 1)

        status, _ = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "set_bench_visibility", "visit_id": visit_id, "shared": True},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200)
        _, partner_shared = self._call("GET", "/api/phone/outing/bench-bookstore")
        self.assertEqual(len(partner_shared["data"]["bench"]["visits"]), 1)
        self.assertNotIn("operation_key",
                         json.dumps(partner_shared["data"]["bench"]["visits"],
                                    ensure_ascii=False))

    def test_bookstore_window_bookmarks_partner_note_and_strict_fields(self):
        status, get_data = self._call("GET", "/api/phone/outing/bench-bookstore")
        self.assertEqual(status, 200)
        window = get_data["data"]["bookstore_window"]
        self.assertEqual(len(window["featured_slots"]), 3)
        self.assertTrue(window["bookstore_in_world"])

        status, bad = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "set_partner_window_note", "slot_index": 0,
             "note": "一起看这本", "place_id": ""},
            csrf=True)
        self.assertEqual(status, 400, bad)
        self.assertEqual(bad["reason_code"], "UNKNOWN_FIELD:place_id")

        status, ok_partner = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "set_partner_window_note", "slot_index": 0,
             "note": "橱窗第一本看着很安静"},
            csrf=True)
        self.assertEqual(status, 200, ok_partner)

        status, ok_her = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "bookmark_window_book", "slot_index": 2,
             "note": "私密书单备注", "shared": True},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, ok_her)

        _, partner_after = self._call("GET", "/api/phone/outing/bench-bookstore")
        w_after = partner_after["data"]["bookstore_window"]
        self.assertEqual(w_after["partner_window_note"]["note"], "橱窗第一本看着很安静")
        self.assertEqual(len(w_after["her_bookmarks"]), 1)
        self.assertNotIn("私密书单备注", json.dumps(w_after["her_bookmarks"], ensure_ascii=False))

        status, cleared = self._call(
            "POST", "/api/phone/outing/bench-bookstore",
            {"action": "clear_partner_window_note"}, csrf=True)
        self.assertEqual(status, 200, cleared)
        _, partner_cleared = self._call("GET", "/api/phone/outing/bench-bookstore")
        self.assertIsNone(partner_cleared["data"]["bookstore_window"]["partner_window_note"])

    def test_walk_routes_real_album_verification_and_partner_scrubbing(self):
        places = self.stack.outdoor_place_ids[:2]
        self.assertGreaterEqual(len(places), 2)

        status, bad_album = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "save_walk_route", "title": "假相册路线",
             "place_ids": places, "album_artifact_id": "album_missing_999"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, bad_album)
        self.assertEqual(bad_album["reason_code"], "ALBUM_IMAGE_NOT_FOUND")

        status, saved = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "save_walk_route", "title": "书店到海边慢走",
             "place_ids": places,
             "album_artifact_id": self.stack.album_artifact_id,
             "caption": "路过花店顺手拍了一张",
             "shared": True},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, saved)
        route_id = saved["data"]["route_id"]

        _, subject_view = self._call("GET", "/api/phone/outing/routes-market",
                                     capability=CAP_TOKEN)
        self.assertEqual(subject_view["data"]["routes"][0]["album_artifact_id"],
                         self.stack.album_artifact_id)
        self.assertEqual(subject_view["data"]["routes"][0]["photo_status"], "available")

        _, partner_view = self._call("GET", "/api/phone/outing/routes-market")
        self.assertEqual(len(partner_view["data"]["routes"]), 1)
        self.assertEqual(partner_view["data"]["routes"][0]["photo_status"], "available")
        dumped = json.dumps(partner_view, ensure_ascii=False)
        self.assertNotIn(self.stack.album_artifact_id, dumped)
        self.assertNotIn("album_artifact_id", dumped)

        status, removed = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "remove_walk_route", "route_id": route_id},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, removed)
        _, after_rm = self._call("GET", "/api/phone/outing/routes-market")
        self.assertEqual(after_rm["data"]["routes"], [])

    def test_festival_market_reads_real_wallet_without_debiting(self):
        _, before_get = self._call("GET", "/api/phone/outing/routes-market")
        market_before = before_get["data"]["festival_market"]
        self.assertEqual(market_before["wallet_status"], "available")
        balance_before = market_before["balance_jpy"]
        self.assertIsInstance(balance_before, int)
        stall_code = market_before["stalls"][0]["stall_code"]

        status, wish_res = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "note_market_wish", "stall_code": stall_code,
             "memo": "留着下次集市看看", "budget_cap_jpy": 300, "shared": True},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, wish_res)

        status, tip_res = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "set_partner_market_tip", "stall_code": stall_code,
             "tip": "这家的热茶摊很暖和"},
            csrf=True)
        self.assertEqual(status, 200, tip_res)

        status, bad_extra = self._call(
            "POST", "/api/phone/outing/routes-market",
            {"action": "set_partner_market_tip", "stall_code": stall_code,
             "tip": "带了多余字段", "memo": ""},
            csrf=True)
        self.assertEqual(status, 400, bad_extra)
        self.assertEqual(bad_extra["reason_code"], "UNKNOWN_FIELD:memo")

        _, after_get = self._call("GET", "/api/phone/outing/routes-market")
        market_after = after_get["data"]["festival_market"]
        self.assertEqual(market_after["balance_jpy"], balance_before)
        self.assertEqual(market_after["partner_market_tip"]["tip"], "这家的热茶摊很暖和")
        self.assertEqual(len(market_after["her_wishes"]), 1)
        self.assertNotIn("留着下次集市看看",
                         json.dumps(market_after["her_wishes"], ensure_ascii=False))

    def test_gallery_curate_withholds_undelivered_and_archived_versions(self):
        revisit = self.stack.revisit
        archived = self.stack.archived

        # Archived project cannot be curated
        status, arch_res = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "curate_exhibit", "artifact_id": archived["artifact_id"],
             "version_id": archived["v1"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, arch_res)
        self.assertEqual(arch_res["reason_code"], view.ARCHIVED_SOURCE)

        # Share envelope cannot be curated as a project
        status, share_res = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "curate_exhibit", "artifact_id": revisit["v1_share"],
             "version_id": revisit["v1"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, share_res)
        self.assertEqual(share_res["reason_code"], view.SHARE_ENVELOPE_NOT_A_PROJECT)

        # Cross-project version is refused
        status, cross_res = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "curate_exhibit", "artifact_id": revisit["artifact_id"],
             "version_id": self.stack.other_work_version},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, cross_res)
        self.assertEqual(cross_res["reason_code"], view.VERSION_NOT_IN_ARTIFACT)

        # Curate delivered v1 and undelivered v3
        status, ex1 = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "curate_exhibit", "artifact_id": revisit["artifact_id"],
             "version_id": revisit["v1"], "wall_caption": "第一版海边速写"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, ex1)
        status, ex3 = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "curate_exhibit", "artifact_id": revisit["artifact_id"],
             "version_id": revisit["v3"], "wall_caption": "还没投递的第三版草稿"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, ex3)

        # Subject sees both owner excerpts
        _, subj = self._call("GET", "/api/phone/outing/gallery-blueprint",
                             capability=CAP_TOKEN)
        self.assertEqual(subj["data"]["gallery"]["available_count"], 2)
        self.assertEqual(subj["data"]["gallery"]["withheld_count"], 0)

        # Partner sees v1 delivered share text ONLY, and v3 is strictly withheld
        _, part = self._call("GET", "/api/phone/outing/gallery-blueprint")
        exhibits = part["data"]["gallery"]["exhibits"]
        self.assertEqual(part["data"]["gallery"]["available_count"], 1)
        self.assertEqual(part["data"]["gallery"]["withheld_count"], 1)
        self.assertEqual(exhibits[0]["status"], "available")
        self.assertEqual(exhibits[0]["excerpt_basis"], view.DELIVERED_SHARE_BASIS)
        self.assertEqual(exhibits[0]["excerpt"], self.fixture.REVISIT_V1_SHARE)
        self.assertEqual(exhibits[1]["status"], "withheld")
        self.assertEqual(exhibits[1]["reason_code"], view.VERSION_NOT_DELIVERED)
        self.assertIsNone(exhibits[1]["wall_caption"])
        dumped = json.dumps(part, ensure_ascii=False)
        self.assertNotIn(self.fixture.REVISIT_V1_TEXT, dumped)
        self.assertNotIn(self.fixture.REVISIT_V3_TEXT, dumped)
        self.assertNotIn("还没投递的第三版草稿", dumped)

    def test_co_blueprints_her_vs_partner_auth_and_author_mismatch(self):
        # Partner cannot claim author_side="her" without capability
        status, spoof = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "upsert_blueprint", "title": "冒充她的草图",
             "sketch_note": "线稿", "intended_use": "看海", "author_side": "her"},
            csrf=True)
        self.assertEqual(status, 403, spoof)

        # Partner creates own blueprint with CSRF only
        status, p_bp = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "upsert_blueprint", "title": "河岸观星木台",
             "sketch_note": "两把木椅和一盏低矮防风灯",
             "intended_use": "秋夜一起认星座", "author_side": "partner"},
            csrf=True)
        self.assertEqual(status, 200, p_bp)
        partner_bp_id = p_bp["data"]["blueprint_id"]

        # Her creates own blueprint with CSRF + capability
        status, h_bp = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "upsert_blueprint", "title": "玻璃花房读书角",
             "sketch_note": "靠南窗放藤椅和小水壶",
             "intended_use": "下雨天边听雨边翻画册", "author_side": "her"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, h_bp)
        her_bp_id = h_bp["data"]["blueprint_id"]

        # Cross-author edit & archive both return 409 AUTHOR_MISMATCH
        status, cross_edit = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "upsert_blueprint", "blueprint_id": her_bp_id,
             "title": "篡改", "sketch_note": "篡改", "intended_use": "篡改",
             "author_side": "partner"},
            csrf=True)
        self.assertEqual(status, 409, cross_edit)
        self.assertEqual(cross_edit["reason_code"], "AUTHOR_MISMATCH")

        status, cross_arch = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "archive_blueprint", "blueprint_id": partner_bp_id,
             "author_side": "her"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, cross_arch)
        self.assertEqual(cross_arch["reason_code"], "AUTHOR_MISMATCH")

        # Partner archives own card cleanly
        status, arch_ok = self._call(
            "POST", "/api/phone/outing/gallery-blueprint",
            {"action": "archive_blueprint", "blueprint_id": partner_bp_id,
             "author_side": "partner"},
            csrf=True)
        self.assertEqual(status, 200, arch_ok)
        _, after = self._call("GET", "/api/phone/outing/gallery-blueprint")
        self.assertEqual(after["data"]["place_blueprints"]["active_count"], 1)
        self.assertTrue(after["data"]["place_blueprints"]["does_not_mutate_world_topology"])

    def _snapshot(self) -> dict[str, str]:
        snap: dict[str, str] = {}
        for base in (self.stack.instance_dir, self.stack.root / "data",
                     self.stack.root / "phone"):
            if not Path(base).exists():
                continue
            for path in sorted(Path(base).rglob("*")):
                if not path.is_file() or path.name.endswith(".sock"):
                    continue
                snap[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return snap

    def test_repeated_gets_are_strictly_zero_write(self):
        place = self.stack.outdoor_place_ids[0]
        self._call("POST", "/api/phone/outing/bench-bookstore",
                   {"action": "log_bench_visit", "place_id": place,
                    "shared": True, "operation_key": "zero-w-1"},
                   csrf=True, capability=CAP_TOKEN)
        self._call("POST", "/api/phone/outing/routes-market",
                   {"action": "save_walk_route", "title": "测试零写路线",
                    "place_ids": [place], "shared": True},
                   csrf=True, capability=CAP_TOKEN)
        self._call("POST", "/api/phone/outing/gallery-blueprint",
                   {"action": "upsert_blueprint", "title": "测试木亭",
                    "sketch_note": "速写", "intended_use": "吹风",
                    "author_side": "partner"},
                   csrf=True)

        before = self._snapshot()
        endpoints = (
            "/api/phone/outing/bench-bookstore",
            "/api/phone/outing/routes-market",
            "/api/phone/outing/gallery-blueprint",
        )
        first = [self._call("GET", ep)[1] for ep in endpoints] + [
            self._call("GET", ep, capability=CAP_TOKEN)[1] for ep in endpoints
        ]
        second = [self._call("GET", ep)[1] for ep in endpoints] + [
            self._call("GET", ep, capability=CAP_TOKEN)[1] for ep in endpoints
        ]
        self.assertEqual(first, second)
        self.assertEqual(before, self._snapshot())


if __name__ == "__main__":
    unittest.main()
