#!/usr/bin/env python3
"""双人书桌：四态木牌、留存短笺、明日便签的最小真实链。

真服务：真实 ``PhoneService`` + 真实 loopback ``PhoneHTTPServer``。World/阅读投影是本文件
注入的只读 facade（形状与 C 的 envelope 相同）；作品与短笺走 ``FakePort``（内存 owner：
CAS / operation_key 幂等 / 来源当前性）。Life canonical、业务时钟、FutureItems 都是本文件
自己写的真实文件。

覆盖：四态真实依据与 unknown、阅读页码/疑问数/作品 head、短笺 DRAFT/DEFERRED/DROPPED
留存与正文扣留、明日到期按业务本地日筛选（不是 now+24h）、凭据读写、错凭据拒绝、
GET 多次零写、锚点文件不在本测试写集内。
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
from components.phone import desk_presence_view as desk  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from life_future_items import FutureItems  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

CSRF = "desk-csrf-token"
CAP_TOKEN = "desk-test-capability-token"
SUBJECT = "chiyo"
WORK_ID = "artifact:" + "c" * 32
SOURCE_ID = "artifact:" + "d" * 32
DAY = "2026-11-11"
NEXT_DAY = "2026-11-12"


def _clock(day: str = DAY) -> dict:
    return {"source": "business_clock", "reason_code": None,
            "logical_at": day + "T12:00:00+08:00", "day": day,
            "run_id": "run:desk", "domain_id": "fastday:run:desk"}


class _World:
    def __init__(self, *, home: bool = True, recovery: dict | None = None,
                 location_status: str = "available") -> None:
        self.home = home
        self.recovery = recovery or {"state": "inactive", "mode": None}
        self.location_status = location_status

    def snapshot(self) -> dict:
        return {"ok": True, "status": "applied", "data": {
            "location": {"status": self.location_status, "home": self.home,
                         "place_id": "home" if self.home else "square",
                         "area_id": "bedroom" if self.home else "plaza",
                         "name": "家" if self.home else "广场"},
            "body": {"status": "available", "recovery": dict(self.recovery)},
        }}


class _Reading:
    def material(self, page=None) -> dict:
        return {"ok": True, "status": "applied", "data": {
            "available": True, "artifact_id": READING, "title": "窗边的读物",
            "page": 3, "pages": 9,
            "bookmark": {"page": 3, "needs_reread": False},
            "annotations": [{"kind": "unclear", "page": 2, "note": "PRIVATE_NOTE"},
                            {"kind": "note", "page": 1, "body": "PRIVATE_BODY"}],
            "private_head": "PRIVATE_HEAD",
        }}


class DoorClassificationTest(unittest.TestCase):
    """纯输入：每一档必须由真实证据驱动，未知不默认开放。"""

    def _classify(self, *, location, recovery, foreground, canonical_ok=True):
        canonical = {"available": canonical_ok, "reason_code": None if canonical_ok
                     else "CANONICAL_STATE_ABSENT", "foreground": foreground}
        return desk.classify_door(
            location=location, world_ok=True, world_reason="",
            canonical=canonical,
            body={"status": "available", "recovery": recovery})

    def test_out_of_home_is_town(self):
        result = self._classify(
            location={"status": "available", "home": False, "place_id": "square"},
            recovery={"state": "inactive", "mode": None}, foreground=None)
        self.assertEqual(result["door_sign"], desk.DOOR_OUT_IN_TOWN)

    def test_sleep_like_recovery_is_resting(self):
        result = self._classify(
            location={"status": "available", "home": True, "place_id": "home"},
            recovery={"state": "active", "mode": "sleep_like"}, foreground=None)
        self.assertEqual(result["door_sign"], desk.DOOR_RESTING)
        self.assertEqual(result["evidence"]["resting_basis"],
                         "OWNER_RECOVERY_ACTIVE_SLEEP_LIKE")

    def test_ordinary_rest_is_not_sleep_and_idle_home_is_open(self):
        result = self._classify(
            location={"status": "available", "home": True, "place_id": "home"},
            recovery={"state": "active", "mode": "rest"}, foreground=None)
        self.assertEqual(result["door_sign"], desk.DOOR_AT_HOME_OPEN)
        self.assertEqual(result["evidence"]["resting_basis"],
                         "OWNER_RECOVERY_ACTIVE_REST_NOT_SLEEP")

    def test_active_foreground_is_focused(self):
        result = self._classify(
            location={"status": "available", "home": True, "place_id": "home"},
            recovery={"state": "inactive", "mode": None},
            foreground={"ref": "actv_1", "status": "ACTIVE", "kind": "WRITING",
                        "interruptibility": "SAFE_BOUNDARY"})
        self.assertEqual(result["door_sign"], desk.DOOR_FOCUSED_WORK)

    def test_paused_or_waiting_foreground_is_not_focused(self):
        for status in ("PAUSED", "WAITING"):
            result = self._classify(
                location={"status": "available", "home": True, "place_id": "home"},
                recovery={"state": "inactive", "mode": None},
                foreground={"ref": "actv_1", "status": status, "kind": "WRITING",
                            "interruptibility": "SAFE_BOUNDARY"})
            self.assertEqual(result["door_sign"], desk.DOOR_AT_HOME_OPEN, status)

    def test_unreadable_location_is_not_at_home_open(self):
        result = desk.classify_door(
            location={"status": "unavailable", "reason_code": "WORLD_LOCATION_INVALID"},
            world_ok=True, world_reason="", canonical={"available": True, "foreground": None},
            body={"status": "unavailable", "recovery": None})
        self.assertIsNone(result["door_sign"])
        self.assertEqual(result["door_status"], desk.DOOR_STATUS_UNAVAILABLE)

    def test_unreadable_canonical_is_unknown(self):
        result = self._classify(
            location={"status": "available", "home": True, "place_id": "home"},
            recovery={"state": "inactive", "mode": None}, foreground=None,
            canonical_ok=False)
        self.assertIsNone(result["door_sign"])
        self.assertEqual(result["door_status"], desk.DOOR_STATUS_UNAVAILABLE)

    def test_missing_recovery_does_not_default_to_open(self):
        result = desk.classify_door(
            location={"status": "available", "home": True, "place_id": "home"},
            world_ok=True, world_reason="", canonical={"available": True, "foreground": None},
            body={"status": "unavailable", "recovery": None})
        self.assertIsNone(result["door_sign"])
        self.assertEqual(result["door_reason_code"], "BODY_RECOVERY_UNAVAILABLE")


class ForegroundWorkIdentityTest(unittest.TestCase):
    """The current work comes from the Activity's own binding, never ``subject_refs[0]``."""

    def test_single_valued_origin_wins_over_a_mixed_subject_ref_set(self):
        identity, reason = desk.foreground_work_identity({
            "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
            "origin_ref": WORK_ID,
            "subject_refs": [WORK_ID, "artifactversion:" + "e" * 32],
        })
        self.assertEqual(identity, WORK_ID)
        self.assertIsNone(reason)

    def test_ambiguous_target_set_is_not_guessed(self):
        identity, reason = desk.foreground_work_identity({
            "activity_kind": "READ_PERSONAL_MATERIAL",
            "subject_refs": ["artifact:" + "a" * 32, "artifact:" + "b" * 32],
        })
        self.assertIsNone(identity)
        self.assertEqual(reason, "FOREGROUND_TARGET_AMBIGUOUS")

    def test_world_target_is_not_a_work(self):
        identity, reason = desk.foreground_work_identity({
            "activity_kind": "BODY_ACTION_AT_OBSERVED_PLACE",
            "origin_ref": "world_fact:place:home",
            "subject_refs": ["world_fact:place:home", "place:home"],
        })
        self.assertIsNone(identity)
        self.assertEqual(reason, "FOREGROUND_TARGET_UNKNOWN")


class DeskHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="desk-presence-"))
        nested = self.root / "one-day"
        nested.mkdir(parents=True)
        (nested / "instance.json").write_text(
            json.dumps({"schema_version": "life.one_day.v1"}), encoding="utf-8")
        self.state_root = nested / "life" / "state"
        self.state_root.mkdir(parents=True)
        self._write_clock()
        self._write_activities("ACTIVE")
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{}")
        self.port.seed(artifact_id=WORK_ID, title="窗边手稿", content="这是作品的正文。")
        self.port.seed(artifact_id=SOURCE_ID, title="她的留白练习", content="来源正文。")
        Path(self.root / "run").mkdir(parents=True, exist_ok=True)
        Path(self.root / "run" / "supply-fixture.sock").write_text("", encoding="utf-8")
        self.world = _World()
        self._serial = 0
        self.service = self._service()
        self.capability = phone_capability.Capability(
            token=CAP_TOKEN, subject=SUBJECT, endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00")
        self.server = phone_server.create_server(
            self.service, csrf_token=CSRF, port=0, capability=self.capability)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    # -- fixtures -------------------------------------------------------------------------
    def _write_clock(self) -> None:
        path = self.state_root / "fastday" / "fastday_clock.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_clock(), ensure_ascii=False), encoding="utf-8")

    def _write_activities(self, foreground_status: str | None,
                          ref: str = "actv_desk") -> None:
        activities = {}
        foreground = None
        if foreground_status is not None:
            foreground = ref
            activities[ref] = {
                "activity_id": ref, "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
                "title": "写信", "status": foreground_status,
                "interruptibility": "SAFE_BOUNDARY",
                # The owner's own target binding: the work object is the Activity's
                # single-valued origin, and the whole observed set is subject_refs.
                "origin_ref": WORK_ID, "subject_refs": [WORK_ID],
                "started_at": DAY + "T09:00:00+08:00", "ended_at": None,
                "terminal_reason_code": None,
            }
        (self.state_root / "canonical_life_state.json").write_text(json.dumps({
            "schema_version": "life.activity.canonical.v1", "subject_id": SUBJECT,
            "revision": 4, "foreground_activity_ref": foreground,
            "activities": activities,
        }, ensure_ascii=False), encoding="utf-8")

    def _service(self) -> phone_service.PhoneService:
        instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "desk-fixture", "subject": SUBJECT, "local_owner_uid": 0,
            "profile_home": str(self.root), "root": str(self.root),
            "world_home": str(self.root / "world"), "world_id": "w-desk",
            "supply_socket": str(self.root / "run" / "supply-fixture.sock"),
            "private_items_artifact_id": ITEMS, "reading_artifact_id": READING,
            "artifact_grant_id": GRANT, "workspace_id": WORKSPACE,
            "work_artifact_id": WORK_ID,
            "world_body_socket": str(self.root / "world" / "run" / "gateway.sock"),
            "features": {},
        }
        (self.root / "world").mkdir(exist_ok=True)
        service = phone_service.PhoneService(instance, environment={},
                                             world=self.world, reading=_Reading())
        service._port = lambda: self.port
        return service

    def _future(self) -> FutureItems:
        return FutureItems(self.state_root, subject_id=SUBJECT)

    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", CSRF)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _prepare_share(self, text: str) -> str:
        self._serial += 1
        status, payload = self._call("POST", "/api/phone/shares", {
            "source_artifact_id": SOURCE_ID, "share_text": text,
            "operation_key": "desk-prepare-%d" % self._serial,
        }, csrf=True)
        self.assertEqual(status, 200, payload)
        return payload["data"]["share_id"]

    def _share_head(self, share_id: str) -> str:
        status, payload = self._call("GET", "/api/phone/shares/" + share_id)
        self.assertEqual(status, 200, payload)
        return payload["data"]["share"]["head_version_id"]

    # -- presence -------------------------------------------------------------------------
    def test_presence_returns_focused_work_with_real_heads_and_no_private_text(self):
        status, payload = self._call("GET", "/api/phone/desk/presence")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["door_sign"], desk.DOOR_FOCUSED_WORK)
        self.assertEqual(data["desk"]["current_work"]["artifact_id"], WORK_ID)
        self.assertEqual(data["desk"]["current_work"]["head_version_id"],
                         self.port.artifacts[WORK_ID]["head_version_id"])
        self.assertEqual(data["desk"]["reading"]["page"], 3)
        self.assertEqual(data["desk"]["reading"]["pages"], 9)
        self.assertEqual(data["desk"]["reading"]["unclear_count"], 1)
        self.assertIsNone(data["partner_presence"]["value"])
        self.assertEqual(data["partner_presence"]["status"], "NOT_RECORDED")
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in ("PRIVATE_NOTE", "PRIVATE_BODY", "这是作品的正文", "第一页"):
            self.assertNotIn(secret, dumped)

    def test_presence_out_in_town_and_unknown(self):
        self_world = self.world
        self_world.home = False
        self_world.recovery = {"state": "inactive", "mode": None}
        status, payload = self._call("GET", "/api/phone/desk/presence")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["door_sign"], desk.DOOR_OUT_IN_TOWN)

        self_world.home = True
        (self.state_root / "canonical_life_state.json").unlink()
        status, payload = self._call("GET", "/api/phone/desk/presence")
        self.assertEqual(status, 200, payload)
        self.assertIsNone(payload["data"]["door_sign"])
        self.assertEqual(payload["data"]["door_status"], desk.DOOR_STATUS_UNAVAILABLE)
        self.assertNotEqual(payload["data"]["door_sign"], desk.DOOR_AT_HOME_OPEN)

    # -- notes ----------------------------------------------------------------------------
    def test_notes_retain_draft_deferred_dropped_and_withhold_text(self):
        draft = self._prepare_share("第一句想说的话")
        deferred = self._prepare_share("第二句再等等")
        dropped = self._prepare_share("第三句算了")
        status, payload = self._call("POST", "/api/phone/shares/%s/actions" % deferred, {
            "action": "defer", "expected_head": self._share_head(deferred),
            "defer_condition": "等她回家", "operation_key": "desk-defer-1"}, csrf=True)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("POST", "/api/phone/shares/%s/actions" % dropped, {
            "action": "drop", "expected_head": self._share_head(dropped),
            "reason": "不想说了", "operation_key": "desk-drop-1"}, csrf=True)
        self.assertEqual(status, 200, payload)

        status, payload = self._call("GET", "/api/phone/desk/notes")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "partner_default")
        self.assertEqual(data["shares"]["counts"],
                         {"DRAFT": 1, "DEFERRED": 1, "DROPPED": 1})
        self.assertFalse(data["partner_preview_available"])
        for row in data["shares"]["retained"]:
            self.assertTrue(row["body_withheld"])
            self.assertNotIn("text", row)
            self.assertNotIn("title", row)
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in ("第一句想说的话", "第二句再等等", "第三句算了", "等她回家"):
            self.assertNotIn(secret, dumped)

    def test_subject_view_returns_bounded_text_and_defer_condition(self):
        draft = self._prepare_share("自己看的那一句")
        deferred = self._prepare_share("延后的那一句")
        self._call("POST", "/api/phone/shares/%s/actions" % deferred, {
            "action": "defer", "expected_head": self._share_head(deferred),
            "defer_condition": "等一个机会", "operation_key": "desk-defer-subject"},
            csrf=True)
        status, payload = self._call("GET", "/api/phone/desk/notes",
                                     capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["view"], "subject")
        rows = {row["share_id"]: row for row in payload["data"]["shares"]["retained"]}
        self.assertEqual(rows[draft]["text"], "自己看的那一句")
        self.assertFalse(rows[draft]["body_withheld"])
        self.assertEqual(rows[deferred]["defer_condition"], "等一个机会")

    def test_wrong_capability_and_model_actor_cannot_read_body(self):
        self._prepare_share("不能泄漏")
        status, payload = self._call("GET", "/api/phone/desk/notes",
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        self.assertNotIn("不能泄漏", json.dumps(payload, ensure_ascii=False))

        # 没有凭据的 actor=model 也只是默认视角；声明不是身份。
        request = urllib.request.Request(self.base + "/api/phone/desk/notes")
        request.add_header("X-Phone-Actor", "model")
        with urllib.request.urlopen(request, timeout=10) as answer:
            payload = json.loads(answer.read().decode("utf-8"))
        self.assertEqual(payload["data"]["view"], "partner_default")
        self.assertNotIn("不能泄漏", json.dumps(payload, ensure_ascii=False))

    def test_tomorrow_items_filtered_by_next_business_local_day(self):
        store = self._future()
        previous = store.propose(title="今天到期的事", due_at="2026-11-11T20:00:00+08:00")
        tomorrow = store.propose(title="明天要记得", due_at="2026-11-12T09:00:00+08:00")
        later = store.propose(title="后天的事", due_at="2026-11-13T09:00:00+08:00")
        status, payload = self._call("GET", "/api/phone/desk/notes")
        self.assertEqual(status, 200, payload)
        data = payload["data"]["tomorrow"]
        self.assertTrue(data["available"])
        self.assertEqual(data["local_day"], NEXT_DAY)
        refs = [row["item_ref"] for row in data["items"]]
        self.assertEqual(refs, [tomorrow["item"]["item_ref"]])
        self.assertNotIn(previous["item"]["item_ref"], refs)
        self.assertNotIn(later["item"]["item_ref"], refs)
        self.assertNotIn("明天要记得", json.dumps(payload, ensure_ascii=False))

    # -- writes ---------------------------------------------------------------------------
    def test_tomorrow_write_requires_csrf_and_capability_then_replays(self):
        body = {"action": "propose", "title": "睡前写一张便签",
                "due_at": "2026-11-12T08:00:00+08:00"}
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")

        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body, csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        self.assertEqual(self._future().items(), [])

        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body,
                                     csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        item_ref = payload["data"]["item_ref"]
        self.assertTrue(payload["data"]["created"])
        # 同意图重放：同一个对象，不产生第二条。
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body,
                                     csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["item_ref"], item_ref)
        self.assertFalse(payload["data"]["created"])
        self.assertEqual(len(self._future().open_items()), 1)

        status, payload = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)
        self.assertEqual([row["item_ref"] for row in payload["data"]["tomorrow"]["items"]],
                         [item_ref])
        self.assertEqual(payload["data"]["tomorrow"]["items"][0]["title"], "睡前写一张便签")

        status, payload = self._call("POST", "/api/phone/desk/tomorrow", {
            "action": "cancel", "item_ref": item_ref}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["item"]["status"], "CANCELLED")
        status, payload = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)
        self.assertEqual(payload["data"]["tomorrow"]["count"], 0)

    def test_tomorrow_reschedule_and_field_refusals(self):
        item = self._future().propose(title="改期的事",
                                      due_at="2026-11-12T09:00:00+08:00")["item"]
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", {
            "action": "reschedule", "item_ref": item["item_ref"],
            "due_at": "2026-11-12T21:00:00+08:00"}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["item"]["revision"], 2)
        self.assertEqual(self._future().get(item["item_ref"]).due_at,
                         "2026-11-12T13:00:00+00:00")

        status, payload = self._call("POST", "/api/phone/desk/tomorrow", {
            "action": "propose", "title": "没有到期时间"}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "DESK_DUE_TIME_AMBIGUOUS")

        status, payload = self._call("POST", "/api/phone/desk/tomorrow", {
            "action": "cancel", "item_ref": item["item_ref"], "title": "多余"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 400, payload)
        self.assertTrue(payload["reason_code"].startswith("UNKNOWN_FIELD"))

    # -- zero-write / privacy -------------------------------------------------------------
    def test_repeated_gets_write_nothing(self):
        self._prepare_share("只读检查")
        store = self._future()
        store.propose(title="明天", due_at="2026-11-12T09:00:00+08:00")
        before = {
            "canonical": hashlib.sha256(
                (self.state_root / "canonical_life_state.json").read_bytes()).hexdigest(),
            "future": hashlib.sha256(
                (self.state_root / "life_future_items.json").read_bytes()).hexdigest(),
            "versions": {key: len(value) for key, value in self.port.versions.items()},
            "operations": len(self.port.operations),
        }
        first = self._call("GET", "/api/phone/desk/presence")[1]
        second = self._call("GET", "/api/phone/desk/presence")[1]
        self.assertEqual(first, second)
        notes_one = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)[1]
        notes_two = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)[1]
        self.assertEqual(notes_one, notes_two)
        after = {
            "canonical": hashlib.sha256(
                (self.state_root / "canonical_life_state.json").read_bytes()).hexdigest(),
            "future": hashlib.sha256(
                (self.state_root / "life_future_items.json").read_bytes()).hexdigest(),
            "versions": {key: len(value) for key, value in self.port.versions.items()},
            "operations": len(self.port.operations),
        }
        self.assertEqual(before, after)


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply owner 与 Life/World socket 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class DeskOwnerIntegrationTest(unittest.TestCase):
    """One small real-owner chain: real Supply, World/Body gateway, Life START, real HTTP.

    The scaffold is the normal constructor path (``tests.test_one_day_life.Services`` +
    ``one_day_life.prepare`` + ``WorldGateway``) and is reused by the preview fixture.  Two
    real, different works are created: A is the instance's start-up ``work_artifact_id`` and B
    is the one the canonical foreground Activity was really adopted for.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import desk_presence_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="desk-owner-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN, subject=SUBJECT, endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00")
        cls.stack = fixture.build_stack(cls.root, port=0, start_world=True,
                                        capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)

    def _call(self, method: str, path: str, body: dict | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _state_snapshot(self) -> dict:
        """Every necessary state/DB/reading/draft/future byte + the owner heads this face reads."""

        snapshot: dict[str, str] = {}
        for base in (self.stack.instance_dir, self.stack.root / "data"):
            for path in sorted(Path(base).rglob("*")):
                if not path.is_file() or path.name.endswith(".sock"):
                    continue
                snapshot[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for artifact in (self.stack.work_a, self.stack.work_b,
                         str(self.stack.data["reading_artifact_id"]),
                         str(self.stack.data["private_items_artifact_id"])):
            view = self.stack.port.read_artifact(artifact)
            snapshot["head:" + artifact] = str((view.get("head") or {}).get("version_id"))
            snapshot["revision:" + artifact] = str(
                (view.get("artifact") or {}).get("revision"))
        snapshot["shares"] = json.dumps(
            self.stack.service.shares(states=["DRAFT", "DEFERRED", "DROPPED"]),
            ensure_ascii=False, sort_keys=True)
        return snapshot

    # -- current work: foreground B, never the start-up A --------------------------------
    def test_current_work_is_the_real_foreground_b_not_the_startup_binding_a(self):
        head = str((self.stack.port.read_artifact(self.stack.work_b).get("head") or {})
                   .get("version_id"))
        status, payload = self._call("GET", "/api/phone/desk/presence")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["door_sign"], desk.DOOR_FOCUSED_WORK)
        self.assertEqual(data["desk"]["current_work"]["artifact_id"], self.stack.work_b)
        self.assertEqual(data["desk"]["current_work"]["head_version_id"], head)
        self.assertNotEqual(data["desk"]["current_work"]["artifact_id"], self.stack.work_a)
        self.assertEqual(data["desk"]["bound_work"]["artifact_id"], self.stack.work_a)
        self.assertFalse(data["desk"]["bound_work"]["is_current"])
        self.assertEqual(data["desk"]["reading"]["page"], 2)
        self.assertEqual(data["desk"]["reading"]["pages"], self.fixture.READING_PAGES)
        self.assertEqual(data["desk"]["reading"]["unclear_count"], 1)
        self.assertEqual(data["partner_presence"]["status"], "NOT_RECORDED")
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.fixture.WORK_A_TEXT, self.fixture.WORK_B_TEXT,
                       "第1页", "合成页注"):
            self.assertNotIn(secret, dumped)

    def test_z_reading_foreground_never_falls_back_to_the_bound_work(self):
        self.fixture.switch_to_reading_activity(
            self.stack.state_root,
            reading_id=str(self.stack.data["reading_artifact_id"]),
            private_items_id=str(self.stack.data["private_items_artifact_id"]))
        status, payload = self._call("GET", "/api/phone/desk/presence")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["door_sign"], desk.DOOR_FOCUSED_WORK)
        self.assertFalse(data["desk"]["current_work"]["available"])
        self.assertEqual(data["desk"]["current_work"]["reason_code"],
                         "FOREGROUND_TARGET_NOT_A_WORK")
        self.assertNotEqual(data["desk"]["current_work"]["artifact_id"], self.stack.work_a)
        self.assertEqual(data["desk"]["bound_work"]["artifact_id"], self.stack.work_a)
        self.assertFalse(data["desk"]["bound_work"]["is_current"])

    # -- credentials / privacy -----------------------------------------------------------
    def test_default_view_withholds_private_text_and_wrong_capability_is_403(self):
        status, payload = self._call("GET", "/api/phone/desk/notes")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["view"], "partner_default")
        self.assertFalse(payload["data"]["partner_preview_available"])
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.fixture.SHARE_DRAFT_TEXT, self.fixture.SHARE_DEFERRED_TEXT,
                       self.fixture.SHARE_DEFER_CONDITION, self.fixture.TOMORROW_TITLE):
            self.assertNotIn(secret, dumped)

        status, payload = self._call("GET", "/api/phone/desk/notes",
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")

    def test_subject_view_reads_the_real_retained_shares_and_tomorrow_item(self):
        status, payload = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "subject")
        self.assertEqual(data["shares"]["counts"], {"DRAFT": 1, "DEFERRED": 1, "DROPPED": 0})
        texts = {row.get("text") for row in data["shares"]["retained"]}
        self.assertIn(self.fixture.SHARE_DRAFT_TEXT, texts)
        deferred_rows = [row for row in data["shares"]["retained"]
                         if row["state"] == "DEFERRED"]
        self.assertEqual(deferred_rows[0]["defer_condition"],
                         self.fixture.SHARE_DEFER_CONDITION)
        titles = [row.get("title") for row in data["tomorrow"]["items"]]
        self.assertIn(self.fixture.TOMORROW_TITLE, titles)

    # -- real HTTP future write ----------------------------------------------------------
    def test_tomorrow_write_over_http_requires_gates_and_replays(self):
        before = len(self.stack.service.desk_notes(subject_view=True)["data"]["tomorrow"]["items"])
        body = {"action": "propose", "title": "睡前再写一张",
                "due_at": self.stack.notes["tomorrow_due_at"]}
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body, csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", body,
                                     csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        item_ref = payload["data"]["item_ref"]
        self.assertTrue(payload["data"]["created"])
        after = self.stack.service.desk_notes(subject_view=True)["data"]["tomorrow"]
        self.assertIn(item_ref, [row["item_ref"] for row in after["items"]])
        self.assertEqual(after["count"], before + 1)
        status, payload = self._call("POST", "/api/phone/desk/tomorrow", {
            "action": "cancel", "item_ref": item_ref}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["item"]["status"], "CANCELLED")

    # -- zero write ----------------------------------------------------------------------
    def test_repeated_gets_change_no_state_bytes(self):
        before = self._state_snapshot()
        first = self._call("GET", "/api/phone/desk/presence")[1]
        second = self._call("GET", "/api/phone/desk/presence")[1]
        self.assertEqual(first, second)
        notes_one = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)[1]
        notes_two = self._call("GET", "/api/phone/desk/notes", capability=CAP_TOKEN)[1]
        self.assertEqual(notes_one, notes_two)
        self.assertEqual(before, self._state_snapshot())


if __name__ == "__main__":
    unittest.main()
