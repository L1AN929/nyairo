#!/usr/bin/env python3
"""窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信：纯投影 + 真实 owner loopback HTTP。

两层验证：

* 上半部分只喂真实形状的 World/owner 事实给纯投影函数，覆盖 unknown、无假天气/生长、
  +08:00 午夜边界、source version 截止与缺时间戳的 bounded；
* 下半部分（POSIX）用 ``window_keepsake_fixture.build_stack``：真实 Supply owner、
  ``one_day_life.prepare``、``WorldGateway``、worldgen 真实室内家具、真实相册导入、
  两个 owner-clock commit 与正式 ``share_prepare/send_local``，再走真实 HTTP 三读一写。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
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
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import window_keepsake_view as view  # noqa: E402

SUBJECT = "chiyo"
CSRF = "window-keepsake-csrf"
CAP_TOKEN = "window-keepsake-test-capability-token"
DAY = "2026-11-11"


def _world(*, place: str = "home", area: str = "bedroom", env_status: str = "available",
           furniture: list | None = None, light: dict | None = None) -> dict:
    environment = {"status": env_status,
                   "reason_code": None if env_status == "available" else "LOCAL_ENV_UNREADABLE",
                   "area": {"area_id": area, "name": "卧室", "kind": "bedroom"},
                   "furniture": furniture if furniture is not None else [
                       {"object_id": "home_plant04", "name": "盆栽", "kind": "plant"},
                       {"object_id": "home_bed01", "name": "床", "kind": "bed"},
                   ],
                   "light": light if light is not None else {
                       "status": "unknown", "reason_code": "NO_DECLARED_LIGHT_HERE",
                       "power": None, "band": None, "declared_source": None,
                       "fixture_ids": []}}
    return {"ok": True, "status": "applied", "data": {
        "world": {"local_date": DAY, "local_time": DAY + "T12:00:00+09:00"},
        "location": {"status": "available", "home": place == "home", "place_id": place,
                     "area_id": area, "name": "家" if place == "home" else "广场"},
        "local_environment": environment,
    }}


def _observation(*, orientation: str | None = "south", view_text: str | None = "港口方向的海",
                 seating: str | None = "旧木长椅") -> dict:
    details = []
    if orientation is not None:
        details.append({"field": "bedroom.window_orientation", "value": orientation,
                        "chosen_by": "verified_main_self", "revision": 1})
    if view_text is not None:
        details.append({"field": "bedroom.window_view", "value": view_text,
                        "chosen_by": "verified_main_self", "revision": 2})
    if seating is not None:
        details.append({"field": "courtyard.seating", "value": seating,
                        "chosen_by": "verified_main_self", "revision": 3})
    facts = {
        "window_orientation": ({"status": "SET_BY_SELF", "value": orientation, "revision": 1}
                               if orientation is not None else {"status": "UNSET"}),
        "window_view": ({"status": "SET_BY_SELF", "value": view_text, "revision": 2}
                        if view_text is not None else {"status": "UNSET"}),
    }
    return {"status": "available", "spaces": [
        {"space_id": "bedroom", "facts": facts},
        {"space_id": "courtyard", "facts": {}, "objects": []},
    ], "personal_details": details, "undecided_fields": []}


class WindowProjectionTest(unittest.TestCase):
    def test_plant_identity_and_placement_come_from_the_world_package_only(self):
        block = view.window_block(world_result=_world(), home_observation=_observation(),
                                  soundscape=view.soundscape_unavailable("FEATURE_DISABLED"))
        self.assertTrue(block["at_home"])
        self.assertEqual(len(block["plants"]), 1)
        plant = block["plants"][0]
        self.assertEqual(plant["object_id"], "home_plant04")
        self.assertEqual(plant["kind"], "plant")
        self.assertEqual(plant["area_id"], "bedroom")
        self.assertEqual(plant["placement_area_source"], "world_package_scene")
        self.assertIsNone(plant["growth"]["value"])
        self.assertEqual(plant["growth"]["reason_code"], view.NO_PLANT_GROWTH_AUTHORITY)

    def test_unreadable_local_environment_is_named_not_an_empty_success(self):
        block = view.window_block(
            world_result=_world(env_status="unavailable", furniture=[]),
            home_observation=_observation(),
            soundscape=view.soundscape_unavailable("FEATURE_DISABLED"))
        self.assertEqual(block["plants"], [])
        self.assertEqual(block["plants_status"]["status"], "unavailable")
        self.assertEqual(block["plants_status"]["reason_code"], "LOCAL_ENV_UNREADABLE")

    def test_no_weather_authority_is_null_and_never_dressed_as_ambient(self):
        block = view.window_block(world_result=_world(), home_observation=_observation(),
                                  soundscape=view.soundscape_available([]))
        self.assertIsNone(block["weather"]["value"])
        self.assertEqual(block["weather"]["reason_code"], view.NO_WEATHER_AUTHORITY)
        self.assertFalse(block["weather"]["is_weather_authority"])
        self.assertFalse(block["soundscape"]["is_weather"])

    def test_unset_personal_space_is_undecided_not_a_fabricated_value(self):
        block = view.window_block(
            world_result=_world(), home_observation=_observation(
                orientation=None, view_text=None, seating=None),
            soundscape=view.soundscape_unavailable("FEATURE_DISABLED"))
        window = block["window"]
        self.assertEqual(window["window"]["orientation"]["status"], "UNSET")
        self.assertIsNone(window["window"]["orientation"]["value"])
        self.assertEqual(window["window"]["orientation"]["reason_code"], "UNDECIDED_BY_SELF")
        self.assertEqual(window["yard_seating"]["status"], "UNSET")

    def test_disabled_home_space_feature_is_not_reported_as_decided(self):
        block = view.window_block(
            world_result=_world(),
            home_observation={"status": "disabled", "reason_code": "FEATURE_DISABLED"},
            soundscape=view.soundscape_unavailable("FEATURE_DISABLED"))
        self.assertFalse(block["window"]["available"])
        self.assertEqual(block["window"]["reason_code"], "FEATURE_DISABLED")

    def test_out_of_home_soundscape_is_named_and_not_empty_weather(self):
        block = view.window_block(
            world_result=_world(place="square", area="plaza"),
            home_observation=_observation(),
            soundscape=view.soundscape_unavailable(view.NOT_AT_HOME, at_home=False))
        self.assertFalse(block["at_home"])
        self.assertFalse(block["soundscape"]["available"])
        self.assertEqual(block["soundscape"]["reason_code"], view.NOT_AT_HOME)
        self.assertIsNone(block["weather"]["value"])


class KeepsakeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="keepsake-state-"))

    def test_select_replay_keeps_revision_and_time(self):
        state, changed = view.select_lockscreen(
            view.empty_state(), "artifact:" + "a" * 32, "2026-11-11T10:00:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(state["revision"], 1)
        replay, changed = view.select_lockscreen(
            state, "artifact:" + "a" * 32, "2026-11-11T11:00:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(replay["updated_at"], "2026-11-11T10:00:00+08:00")

    def test_pin_and_unpin_are_idempotent_by_real_intent(self):
        state, changed = view.pin_share(view.empty_state(), "artifact:" + "b" * 32,
                                        "2026-11-11T10:00:00+08:00")
        self.assertTrue(changed)
        replay, changed = view.pin_share(state, "artifact:" + "b" * 32,
                                         "2026-11-11T10:01:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], 1)
        removed, changed = view.unpin_share(replay, "artifact:" + "b" * 32,
                                            "2026-11-11T10:02:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(removed["pins"], [])
        again, changed = view.unpin_share(removed, "artifact:" + "b" * 32,
                                          "2026-11-11T10:03:00+08:00")
        self.assertFalse(changed)

    def test_clear_lockscreen_changes_only_when_a_choice_exists(self):
        empty, changed = view.clear_lockscreen(view.empty_state(), "2026-11-11T10:00:00+08:00")
        self.assertFalse(changed)
        state, _ = view.select_lockscreen(empty, "artifact:" + "c" * 32,
                                          "2026-11-11T10:01:00+08:00")
        cleared, changed = view.clear_lockscreen(state, "2026-11-11T10:02:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(cleared["lockscreen"])

    def test_state_round_trip_is_atomic_and_corrupt_state_fails_closed(self):
        state, _ = view.select_lockscreen(view.empty_state(), "artifact:" + "d" * 32,
                                          "2026-11-11T10:00:00+08:00")
        view.write_state(self.root, state)
        loaded, reason = view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(loaded["lockscreen"]["album_artifact_id"], "artifact:" + "d" * 32)
        path = view.state_path(self.root)
        path.write_text("{not json", encoding="utf-8")
        fallback, reason = view.read_state(self.root)
        self.assertEqual(reason, "KEEPSAKE_STATE_UNREADABLE")
        self.assertEqual(fallback, view.empty_state())


class SlowLetterCoolingTest(unittest.TestCase):
    """慢信 3 日冷静期（设计 v3.0 第10章：动念起草到寄出必须至少间隔 3 个日历日）。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="keepsake-cooling-"))

    def test_post_inside_72h_cooling_window_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            view.post_slow_letter(
                self.root,
                title="雨窗的倒影",
                body="第4扇窗画完了，倒影重画了三次。",
                drafted_at="2026-11-12T10:01:00+08:00",  # 距寄出仅 47h59m
                now="2026-11-14T10:00:00+08:00",
            )
        self.assertEqual(str(ctx.exception), "SLOW_LETTER_COOLING_PERIOD")
        # 拒绝即拒绝：不落任何信件（fail-closed，不静默推迟）
        state, _ = view.read_state(self.root)
        self.assertEqual(state["slow_letters"], [])

    def test_post_after_72h_cooling_persists_drafted_at(self):
        res = view.post_slow_letter(
            self.root,
            title="雨窗的倒影",
            body="第4扇窗画完了，倒影重画了三次。",
            drafted_at="2026-11-11T09:59:00+08:00",  # 72h1m 前
            now="2026-11-14T10:00:00+08:00",
        )
        self.assertEqual(res["drafted_at"], "2026-11-11T09:59:00+08:00")
        state, reason = view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(len(state["slow_letters"]), 1)
        self.assertEqual(state["slow_letters"][0]["drafted_at"], "2026-11-11T09:59:00+08:00")
        self.assertEqual(state["slow_letters"][0]["sent_at"], "2026-11-14T10:00:00+08:00")

    def test_missing_drafted_at_defaults_to_creation_moment_and_legacy_rows_default_to_sent_at(self):
        # 未传 drafted_at：视为起草时刻 = 寄出时刻（最小诚实缺省，无冷静期债务）
        res = view.post_slow_letter(
            self.root,
            title="无草稿态的信",
            body="顺手写的短笺。",
            now="2026-11-14T10:00:00+08:00",
        )
        self.assertEqual(res["drafted_at"], res["sent_at"])
        # 历史存量信件（无 drafted_at 字段）读取时缺省视为创建（=sent_at）时刻
        raw_path = view.state_path(self.root)
        doc = json.loads(raw_path.read_text(encoding="utf-8"))
        legacy = dict(doc["slow_letters"][0])
        legacy["letter_id"] = "slow_letter_legacy0001"
        legacy.pop("drafted_at")
        doc["slow_letters"] = [legacy]
        raw_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        state, reason = view.read_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["slow_letters"][0]["drafted_at"], state["slow_letters"][0]["sent_at"])


class LettersDateTest(unittest.TestCase):
    def test_plus8_midnight_boundary_is_two_local_days(self):
        timeline = view.writing_days([
            {"version_number": 1, "created_at": "2026-11-10T23:30:00+08:00"},
            {"version_number": 2, "created_at": "2026-11-11T00:30:00+08:00"},
        ], cutoff_version_number=2, offset="+08:00")
        self.assertEqual(timeline["first_written_day"], "2026-11-10")
        self.assertEqual(timeline["last_written_day"], "2026-11-11")
        self.assertEqual(timeline["writing_days"], 2)
        self.assertEqual(timeline["elapsed_days_inclusive"], 2)

    def test_cutoff_excludes_versions_committed_after_the_share(self):
        timeline = view.writing_days([
            {"version_number": 3, "created_at": "2026-11-12T09:00:00+08:00"},
            {"version_number": 2, "created_at": "2026-11-11T09:00:00+08:00"},
            {"version_number": 1, "created_at": "2026-11-10T20:00:00+08:00"},
        ], cutoff_version_number=2, offset="+08:00")
        self.assertEqual(timeline["writing_days"], 2)
        self.assertEqual(timeline["last_written_day"], "2026-11-11")
        self.assertEqual(timeline["versions_after_cutoff_excluded"], 1)
        self.assertEqual(timeline["versions_considered"], 2)

    def test_missing_timestamp_is_partial_not_a_complete_postmark(self):
        timeline = view.writing_days([
            {"version_number": 1, "created_at": "2026-11-10T20:00:00+08:00"},
            {"version_number": 2, "created_at": None},
        ], cutoff_version_number=2, offset="+08:00")
        self.assertEqual(timeline["undated_versions"], 1)
        row = view.letter_row(share_row={"share_id": "artifact:x", "state": "DRAFT"},
                              timeline=timeline, history_complete=True, history_reason="",
                              text=None, delivered_at=None, status="draft", reason_code=None)
        self.assertEqual(row["bounded"]["date_completeness"], "partial")
        self.assertFalse(row["postmark"]["historical_business_day_provable"])

    def test_utc_z_timestamp_is_localized_to_the_business_offset(self):
        day = view.local_day("2026-11-10T16:30:00Z", "+08:00")
        self.assertEqual(day, "2026-11-11")

    def test_letter_rows_never_carry_source_work_body(self):
        row = view.letter_row(
            share_row={"share_id": "artifact:x", "state": "LOCAL_DELIVERED", "delivered": True},
            timeline={"first_written_day": "2026-11-10", "last_written_day": "2026-11-11",
                      "writing_days": 2, "elapsed_days_inclusive": 2,
                      "versions_considered": 2, "versions_after_cutoff_excluded": 0,
                      "undated_versions": 0, "unbounded_versions": 0,
                      "timezone_offset": "+08:00", "date_basis": "owner_timestamp_localized"},
            history_complete=True, history_reason="", text="只有分享正文",
            delivered_at="2026-11-11T10:00:00+08:00", status="delivered", reason_code=None)
        self.assertEqual(row["text"], "只有分享正文")
        self.assertFalse(row["source_work_body_included"])
        self.assertIsNone(row["letter_type"])


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply owner 与 Life/World socket 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class WindowKeepsakeOwnerHTTPTest(unittest.TestCase):
    """Real Supply/World/album/share/project owners; real loopback HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        import window_keepsake_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="window-keepsake-"))
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
        # The explicit-selection file is this feature's own scratch state; each case starts
        # from "nothing chosen".  Owner facts (shares/versions/album) are never reset.
        view.write_state(self.stack.root, view.empty_state())

    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None,
              headers: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _select(self, ref: str | None = None) -> tuple[int, dict]:
        return self._call("POST", "/api/phone/home/keepsakes", {
            "action": "select_lockscreen",
            "album_artifact_id": ref or self.stack.keepsakes["album_artifact_id"],
        }, csrf=True, capability=CAP_TOKEN)

    def _pin(self, share_id: str) -> tuple[int, dict]:
        return self._call("POST", "/api/phone/home/keepsakes", {
            "action": "pin_share", "share_id": share_id,
        }, csrf=True, capability=CAP_TOKEN)

    # -- window --------------------------------------------------------------------------
    def test_window_reads_real_plants_light_and_self_decided_window(self):
        status, payload = self._call("GET", "/api/phone/home/window")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["at_home"])
        self.assertEqual(data["location"]["place_id"], "home")
        plants = data["plants"]
        self.assertTrue(plants, data)
        self.assertTrue(all(plant["kind"] == "plant" for plant in plants))
        self.assertTrue(any(str(plant["object_id"]).startswith("home_plant") for plant in plants))
        self.assertTrue(all(plant["area_id"] == "bedroom" for plant in plants))
        self.assertTrue(all(plant["growth"]["value"] is None for plant in plants))
        self.assertEqual(data["light"]["source"], "world_room_environment")
        self.assertIsNone(data["weather"]["value"])
        self.assertEqual(data["weather"]["reason_code"], view.NO_WEATHER_AUTHORITY)
        self.assertTrue(data["soundscape"]["available"], data["soundscape"])
        self.assertFalse(data["soundscape"]["is_weather"])
        self.assertTrue(any(event["location_id"] == "home"
                            for event in data["soundscape"]["events"]))
        self.assertTrue(data["window"]["available"])
        self.assertEqual(data["window"]["window"]["orientation"]["value"], "south")
        self.assertIn("港口", data["window"]["window"]["view"]["value"])
        self.assertEqual(data["window"]["yard_seating"]["value"], "一张旧木长椅")
        self.assertFalse(data["private_text_included"])

    # -- keepsakes -----------------------------------------------------------------------
    def test_default_view_hides_lockscreen_reference_but_shows_delivered_pin_text(self):
        status, payload = self._pin(self.stack.keepsakes["pinned_share_id"])
        self.assertEqual(status, 200, payload)
        status, payload = self._select()
        self.assertEqual(status, 200, payload)

        status, payload = self._call("GET", "/api/phone/home/keepsakes")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "partner_default")
        self.assertTrue(data["lockscreen"]["has_lockscreen"])
        self.assertNotIn("album_artifact_id", data["lockscreen"])
        self.assertNotIn("caption", data["lockscreen"])
        self.assertEqual(data["fridge_pin_count"], 1)
        pin = data["fridge_pins"][0]
        self.assertEqual(pin["status"], "available")
        self.assertEqual(pin["text"], self.fixture.PIN_SHARE_TEXT)
        self.assertIsNotNone(pin["delivered_at"])
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.fixture.PICTURE_CAPTION, dumped)

        status, payload = self._call("GET", "/api/phone/home/keepsakes", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        lock = payload["data"]["lockscreen"]
        self.assertEqual(lock["album_artifact_id"], self.stack.keepsakes["album_artifact_id"])
        self.assertEqual(lock["caption"], self.fixture.PICTURE_CAPTION)
        self.assertIn("/api/phone/album/", lock["image_surface"])

    def test_select_requires_gates_and_rejects_a_non_album_artifact_then_replays(self):
        body = {"action": "select_lockscreen",
                "album_artifact_id": self.stack.keepsakes["album_artifact_id"]}
        status, payload = self._call("POST", "/api/phone/home/keepsakes", body)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._call("POST", "/api/phone/home/keepsakes", body, csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")

        status, payload = self._select(self.stack.work_a)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "NOT_AN_ALBUM_IMAGE")

        status, payload = self._select()
        self.assertEqual(status, 200, payload)
        revision = payload["data"]["revision"]
        state_after_first = view.state_path(self.stack.root).read_bytes()
        status, payload = self._select()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertFalse(payload["data"]["changed"])
        self.assertEqual(payload["data"]["revision"], revision)
        self.assertEqual(view.state_path(self.stack.root).read_bytes(), state_after_first)

    def test_pin_rejects_undelivered_share_and_replays_the_real_intent(self):
        draft = self.stack.letters["draft_share_id"]
        status, payload = self._pin(draft)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "PINNED_SHARE_NOT_DELIVERED")

        status, payload = self._pin(self.stack.keepsakes["pinned_share_id"])
        self.assertEqual(status, 200, payload)
        revision = payload["data"]["revision"]
        status, payload = self._pin(self.stack.keepsakes["pinned_share_id"])
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(payload["data"]["revision"], revision)

        status, payload = self._call(
            "POST", "/api/phone/home/keepsakes",
            {"action": "unpin_share", "share_id": self.stack.keepsakes["pinned_share_id"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        status, payload = self._call(
            "POST", "/api/phone/home/keepsakes",
            {"action": "unpin_share", "share_id": self.stack.keepsakes["pinned_share_id"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)

    def test_wrong_capability_is_403_and_actor_header_is_not_authority(self):
        for path in ("/api/phone/home/keepsakes", "/api/phone/home/letters"):
            status, payload = self._call("GET", path, capability="wrong-token")
            self.assertEqual(status, 403, payload)
            self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
            status, payload = self._call("GET", path, headers={"X-Phone-Actor": "model"})
            self.assertEqual(status, 200, payload)
            self.assertEqual(payload["data"]["view"], "partner_default")

    # -- letters -------------------------------------------------------------------------
    def test_letters_project_cross_day_work_and_stop_at_the_bound_version(self):
        status, payload = self._call("GET", "/api/phone/home/letters")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["letter_type_is_not_a_store"])
        self.assertFalse(data["historical_business_day_provable"])
        rows = {row["share_id"]: row for row in data["letters"]}
        delivered = rows[self.stack.letters["delivered_share_id"]]
        self.assertEqual(delivered["postmark"]["first_written_day"], "2026-11-10")
        self.assertEqual(delivered["postmark"]["last_written_day"], "2026-11-11")
        self.assertEqual(delivered["postmark"]["writing_days"], 2)
        self.assertEqual(delivered["postmark"]["elapsed_days_inclusive"], 2)
        self.assertEqual(delivered["postmark"]["date_basis"], "owner_timestamp_localized")
        self.assertFalse(delivered["postmark"]["historical_business_day_provable"])
        self.assertEqual(delivered["versions_considered"], 2)
        self.assertGreaterEqual(delivered["versions_after_cutoff_excluded"], 1)
        self.assertEqual(delivered["text"], self.fixture.LETTER_SHARE_TEXT)
        self.assertFalse(delivered["source_work_body_included"])

        draft = rows[self.stack.letters["draft_share_id"]]
        self.assertTrue(draft["text_withheld"])
        self.assertIsNone(draft["text"])
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.fixture.LETTER_DAY1_TEXT, self.fixture.LETTER_DAY2_TEXT,
                       self.fixture.LETTER_DAY3_TEXT, self.fixture.LETTER_DRAFT_TEXT):
            self.assertNotIn(secret, dumped)

        status, payload = self._call("GET", "/api/phone/home/letters", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        draft = {row["share_id"]: row for row in payload["data"]["letters"]}[
            self.stack.letters["draft_share_id"]]
        self.assertEqual(draft["text"], self.fixture.LETTER_DRAFT_TEXT)
        self.assertFalse(draft["text_withheld"])
        self.assertNotIn(self.fixture.LETTER_DAY3_TEXT, json.dumps(payload, ensure_ascii=False))

    # -- zero write ----------------------------------------------------------------------
    def _snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for base in (self.stack.instance_dir, self.stack.root / "data"):
            for path in sorted(Path(base).rglob("*")):
                if not path.is_file() or path.name.endswith(".sock"):
                    continue
                snapshot[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for artifact in (self.stack.work_a, self.stack.work_b,
                         self.stack.letters["artifact_id"],
                         str(self.stack.data["reading_artifact_id"]),
                         str(self.stack.data["private_items_artifact_id"])):
            read = self.stack.port.read_artifact(artifact)
            snapshot["head:" + artifact] = str((read.get("head") or {}).get("version_id"))
            snapshot["revision:" + artifact] = str((read.get("artifact") or {}).get("revision"))
        snapshot["shares"] = json.dumps(self.stack.service.shares(), ensure_ascii=False,
                                        sort_keys=True)
        snapshot["keepsakes"] = view.state_path(self.stack.root).read_text(encoding="utf-8")
        return snapshot

    def test_repeated_gets_change_no_state_bytes(self):
        self._select()
        self._pin(self.stack.keepsakes["pinned_share_id"])
        before = self._snapshot()
        first = [self._call("GET", path)[1] for path in (
            "/api/phone/home/window", "/api/phone/home/keepsakes",
            "/api/phone/home/letters")]
        second = [self._call("GET", path)[1] for path in (
            "/api/phone/home/window", "/api/phone/home/keepsakes",
            "/api/phone/home/letters")]
        self.assertEqual(first, second)
        self.assertEqual(before, self._snapshot())


if __name__ == "__main__":
    unittest.main()
