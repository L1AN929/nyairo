#!/usr/bin/env python3
"""创作托盘 / 小物履历 / 三样喜欢之物：纯投影 + 真实 owner loopback HTTP。

两层验证：

* 上半部分只喂真实形状的 owner 事实给纯投影/状态函数，覆盖 head 后继证明、位置 unknown、
  托盘幂等与三类件数、小物不造磨损/依恋、伴侣卡不进认知；
* 下半部分（POSIX）用 ``workbench_curio_fixture.build_stack``：真实 Supply owner、
  ``one_day_life.prepare``、``WorldGateway``、真实相册、正式资料导入、真实产权购买与
  substrate 物化、正式分享投递，再走真实 HTTP 读写。
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
from components.phone import service as phone_service  # noqa: E402
from components.phone import workbench_curio_view as view  # noqa: E402

SUBJECT = "chiyo"
CSRF = "workbench-curio-csrf"
CAP_TOKEN = "{{SECRET_8w1kq0pz}}"


def _economy_item(**overrides) -> dict:
    item = {
        "item_instance_id": "item:one", "catalog_item_id": "virtual_snack_wafer",
        "acquired_at": "2026-11-11T12:00:00Z", "acquisition_kind": "PURCHASE",
        "acquisition_provenance": {
            "source": "local_owner_phone_purchase",
            "delivery": "phone_virtual_store_home_delivery",
            "purchase_intent_id": "intent:one", "transaction_id": "txn:one"},
        "acquisition_price": 240, "currency": "JPY",
        "current_location": "home/bedroom", "disposition": "OWNED",
    }
    item.update(overrides)
    return item


class WorkbenchCurioProjectionTest(unittest.TestCase):
    # -- head succession ----------------------------------------------------------------
    def test_same_head_is_false_even_when_strings_are_equal(self):
        self.assertFalse(view.head_advanced(
            baseline_version_id="v1", baseline_version_number=1,
            head_version_id="v1", head_version_number=1,
            history_entries=[], history_complete=False))

    def test_successor_is_true_only_with_a_matching_history_entry(self):
        history = [{"version_id": "v2", "version_number": 2},
                   {"version_id": "v1", "version_number": 1}]
        self.assertTrue(view.head_advanced(
            baseline_version_id="v1", baseline_version_number=1,
            head_version_id="v2", head_version_number=2,
            history_entries=history, history_complete=True))
        self.assertEqual(view.head_advanced(
            baseline_version_id="v1", baseline_version_number=1,
            head_version_id="v9", head_version_number=9,
            history_entries=[], history_complete=False), "unknown")
        self.assertEqual(view.head_advanced(
            baseline_version_id="v1", baseline_version_number=2,
            head_version_id="v2", head_version_number=1,
            history_entries=history, history_complete=True), "unknown")

    # -- positions ----------------------------------------------------------------------
    def test_position_hand_room_and_transit_are_the_real_shapes(self):
        hand = view.position_block({"kind": "hand", "slot_id": "right_hand"},
                                   delivery_status=None, economy_location="home/bedroom")
        self.assertTrue(hand["carried"])
        self.assertEqual(hand["kind"], "hand")
        room = view.position_block({"kind": "room", "location_id": "home", "area_id": "bedroom"},
                                   delivery_status=None, economy_location="home/bedroom")
        self.assertFalse(room["carried"])
        self.assertEqual(room["area_id"], "bedroom")
        transit = view.position_block({"kind": "in_transit", "from_location_id": "shop",
                                       "to_location_id": "home"},
                                      delivery_status="PENDING",
                                      economy_location="in_transit/home/bedroom")
        self.assertEqual(transit["status"], "in_transit")
        self.assertEqual(transit["to_location_id"], "home")

    def test_unknown_position_never_falls_back_to_the_subject_area(self):
        pending = view.position_block(None, delivery_status="PENDING",
                                      economy_location="in_transit/home/bedroom")
        self.assertEqual(pending["status"], "pending_delivery")
        self.assertEqual(pending["reason_code"], "DELIVERY_PENDING")
        unknown = view.position_block(None, delivery_status=None,
                                      economy_location="home/bedroom")
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["reason_code"], "NO_PLACEMENT")
        self.assertNotIn("area_id", unknown)
        broken = view.position_block(None, delivery_status=None,
                                     economy_location="home/bedroom",
                                     substrate_unavailable=True)
        self.assertEqual(broken["reason_code"], "SUBSTRATE_UNAVAILABLE")

    def test_curio_row_carries_a_real_transaction_and_no_invented_wear(self):
        row = view.curio_row(item=_economy_item(), name="虚拟小点心",
                             placement={"kind": "room", "location_id": "home",
                                        "area_id": "bedroom"},
                             delivery=None)
        self.assertEqual(row["acquired"]["source"]["transaction_id"], "txn:one")
        self.assertEqual(row["acquired"]["source"]["purchase_intent_id"], "intent:one")
        self.assertEqual(row["acquired"]["acquired_at"], "2026-11-11T12:00:00Z")
        self.assertEqual(row["position"]["status"], "available")
        self.assertIsNone(row["remaining_uses"]["value"])
        self.assertEqual(row["remaining_uses"]["reason_code"],
                         view.NO_REMAINING_USES_AUTHORITY)
        self.assertTrue(row["no_fabricated_wear"])
        self.assertTrue(row["no_attachment_score"])
        self.assertEqual(row["delivery"]["status"], "unknown")
        self.assertEqual(row["delivery"]["reason_code"], "NO_DELIVERY_RECORD")

    # -- tray state ---------------------------------------------------------------------
    def test_set_tray_is_idempotent_and_note_change_is_a_new_intent(self):
        state = view.empty_tray_state()
        tray = {"images": [{"album_artifact_id": "artifact:img", "added_at": "t1"}],
                "materials": [{"artifact_id": "artifact:mat",
                               "baseline_source_revision": "v1", "added_at": "t1"}],
                "work": {"artifact_id": "artifact:work", "baseline_version_id": "v1",
                         "baseline_version_number": 1, "added_at": "t1"},
                "note": "先记一句"}
        first, changed = view.set_tray(state, tray, "2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(first["revision"], 1)
        replay, changed = view.set_tray(first, tray, "2026-11-11T12:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], 1)
        note_only = dict(tray)
        note_only["note"] = "换一句备注"
        third, changed = view.set_tray(replay, note_only, "2026-11-11T12:10:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(third["revision"], 2)
        cleared, changed = view.clear_tray(third, "2026-11-11T12:20:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(cleared["revision"], 3)
        again, changed = view.clear_tray(cleared, "2026-11-11T12:30:00+08:00")
        self.assertFalse(changed)

    def test_workbench_default_view_hides_refs_note_and_titles(self):
        state, changed = view.set_tray(view.empty_tray_state(), {
            "images": [{"album_artifact_id": "artifact:img"}],
            "materials": [{"artifact_id": "artifact:mat",
                           "baseline_source_revision": "v1"}],
            "work": {"artifact_id": "artifact:work", "baseline_version_id": "v1",
                     "baseline_version_number": 1},
            "note": "私人备注"}, "2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        block = view.workbench_block(
            state=state, state_reason=None, subject_view=False,
            images=[{"album_artifact_id": "artifact:img"}],
            materials=[{"artifact_id": "artifact:mat"}],
            work={"artifact_id": "artifact:work"})
        self.assertEqual(block["counts"], {"images": 1, "materials": 1, "works": 1})
        self.assertTrue(block["has_materials"])
        self.assertTrue(block["no_obligation"])
        dumped = json.dumps(block, ensure_ascii=False)
        for secret in ("artifact:img", "artifact:mat", "artifact:work", "私人备注"):
            self.assertNotIn(secret, dumped)
        subject = view.workbench_block(
            state=state, state_reason=None, subject_view=True,
            images=[{"album_artifact_id": "artifact:img"}],
            materials=[{"artifact_id": "artifact:mat"}],
            work={"artifact_id": "artifact:work"})
        self.assertEqual(subject["note"], "私人备注")
        self.assertEqual(subject["work"]["artifact_id"], "artifact:work")

    # -- trio state ---------------------------------------------------------------------
    def test_her_partner_picks_idempotency_and_bounds(self):
        state = view.empty_trio_state()
        picks = [{"share_id": "artifact:s1", "picked_at": "t1"}]
        first, changed = view.set_her_picks(state, picks, "t2")
        self.assertTrue(changed)
        again, changed = view.set_her_picks(first, picks, "t3")
        self.assertFalse(changed)
        self.assertEqual(again["revision"], 1)
        partner = [{"title": "海边的石头", "description": "灰蓝色", "partner_contributed": True,
                    "added_at": "t1"}]
        third, changed = view.set_partner_picks(again, partner, "t4")
        self.assertTrue(changed)
        block = view.trio_block(state=third, state_reason=None, her_rows=[],
                                partner_rows=[view.partner_pick_row(partner[0])])
        self.assertTrue(block["partner_contributed"])
        self.assertEqual(block["partner"]["picks"][0]["partner_contributed"], True)
        with self.assertRaises(ValueError):
            view.set_partner_picks(third, [{"title": "x" * 41, "description": ""}], "t5")
        with self.assertRaises(ValueError):
            view.set_her_picks(third, [{"share_id": "s%d" % n} for n in range(4)], "t5")

    def test_her_row_default_carries_bounded_share_text_only(self):
        row = view.her_pick_row(share_id="artifact:s1", picked_at="t1", status="available",
                                reason_code=None, text="分享正文", text_chars=4,
                                delivered_at="t0", state="LOCAL_DELIVERED")
        self.assertEqual(row["text"], "分享正文")
        self.assertFalse(row["text_withheld"])
        self.assertFalse(row["source_text_included"])
        withheld = view.her_pick_row(share_id="artifact:s2", picked_at="t1",
                                     status="withheld", reason_code="SHARE_NOT_DELIVERED")
        self.assertTrue(withheld["text_withheld"])
        self.assertIsNone(withheld["text"])


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply/World owner 与 Life/World socket 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class WorkbenchCurioOwnerHTTPTest(unittest.TestCase):
    """Real Supply/World/economy/album/share owners; real loopback HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        import workbench_curio_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="workbench-curio-"))
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
        # The two reference files are this feature's own scratch state; each case starts empty.
        # Owner facts (purchases/shares/versions/album) are never reset.
        view.write_tray_state(self.stack.root, view.empty_tray_state())
        view.write_trio_state(self.stack.root, view.empty_trio_state())

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
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _set_tray(self, **overrides) -> tuple[int, dict]:
        body = {"action": "set_tray",
                "album_artifact_ids": [self.stack.pictures["one"]],
                "material_ids": [self.stack.materials["one"]],
                "work_artifact_id": self.stack.work_a,
                "note": "今天想做的三样"}
        body.update(overrides)
        return self._call("POST", "/api/phone/home/workbench", body,
                          csrf=True, capability=CAP_TOKEN)

    # -- workbench ----------------------------------------------------------------------
    def test_tray_valid_chain_and_default_privacy(self):
        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_OK)
        self.assertEqual(payload["data"]["counts"],
                         {"images": 1, "materials": 1, "works": 1})

        status, payload = self._call("GET", "/api/phone/home/workbench", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "subject")
        self.assertEqual(data["note"], "今天想做的三样")
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["images"][0]["album_artifact_id"],
                         self.stack.pictures["one"])
        self.assertEqual(data["images"][0]["status"], "available")
        material = data["materials"][0]
        self.assertEqual(material["artifact_id"], self.stack.materials["one"])
        self.assertEqual(material["status"], "CURRENT")
        self.assertTrue(material["baseline_source_revision"])
        work = data["work"]
        self.assertEqual(work["artifact_id"], self.stack.work_a)
        self.assertEqual(work["status"], "available")
        self.assertFalse(work["head_advanced"])
        self.assertEqual(work["baseline_version_id"], work["current_head_version_id"])

        status, payload = self._call("GET", "/api/phone/home/workbench")
        self.assertEqual(status, 200, payload)
        default = payload["data"]
        self.assertEqual(default["view"], "partner_default")
        self.assertEqual(default["counts"], {"images": 1, "materials": 1, "works": 1})
        self.assertTrue(default["has_materials"])
        self.assertTrue(default["no_obligation"])
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.stack.pictures["one"], self.stack.materials["one"],
                       self.stack.work_a, "今天想做的三样"):
            self.assertNotIn(secret, dumped)

    def test_tray_rejects_invalid_and_over_count_refs_with_zero_write(self):
        status, payload = self._set_tray(album_artifact_ids=[self.stack.work_a])
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "NOT_AN_ALBUM_IMAGE")
        status, payload = self._set_tray(material_ids=[self.stack.work_a])
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "NOT_A_MATERIAL")
        status, payload = self._set_tray(work_artifact_id=self.stack.delivered_share_id)
        self.assertEqual(status, 409, payload)
        self.assertIn(payload["reason_code"],
                      ("WORK_IS_SHARE_ENVELOPE", "PROJECT_NOT_FOUND", "NOT_A_PRIVATE_WORK"))
        status, payload = self._set_tray(
            material_ids=[self.stack.materials["one"], self.stack.materials["two"],
                          self.stack.work_a])
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "TRAY_MATERIALS_INVALID")
        # Nothing above wrote anything.
        self.assertEqual(view.read_tray_state(self.stack.root)[0]["revision"], 0)

    def test_tray_requires_csrf_and_capability_and_never_trusts_actor(self):
        body = {"action": "set_tray", "album_artifact_ids": [self.stack.pictures["one"]]}
        status, payload = self._call("POST", "/api/phone/home/workbench", body)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._call("POST", "/api/phone/home/workbench", body, csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._call("POST", "/api/phone/home/workbench", body, csrf=True,
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        status, payload = self._call("GET", "/api/phone/home/workbench",
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        status, payload = self._call("GET", "/api/phone/home/workbench",
                                     headers={"X-Phone-Actor": "model"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["view"], "partner_default")

    def test_tray_replay_keeps_revision_and_baseline_then_advance_is_proven(self):
        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        revision = payload["data"]["revision"]
        baseline = view.read_tray_state(self.stack.root)[0]["tray"]["work"]
        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(payload["data"]["revision"], revision)
        self.assertEqual(view.read_tray_state(self.stack.root)[0]["tray"]["work"], baseline)

        project = self.stack.service.project(self.stack.work_a)["project"]
        committed = self.stack.service.commit_project(
            artifact_id=self.stack.work_a, content="续写一段新的内容。",
            expected_head=str(project["head_version_id"]),
            operation_key="workbench-curio-work-commit")
        self.assertTrue(committed.get("ok"), committed)

        status, payload = self._call("GET", "/api/phone/home/workbench", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        work = payload["data"]["work"]
        self.assertTrue(work["head_advanced"])
        self.assertEqual(work["baseline_version_id"], baseline["baseline_version_id"])
        self.assertNotEqual(work["current_head_version_id"], work["baseline_version_id"])

        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(view.read_tray_state(self.stack.root)[0]["tray"]["work"], baseline)

    def test_material_new_version_is_reported_stale(self):
        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        baseline = view.read_tray_state(self.stack.root)[0]["tray"]["materials"][0]
        read = self.stack.port.read_artifact(self.stack.materials["one"])
        head = str((read.get("head") or {}).get("version_id") or "")
        from research_workspace_service import build_material_content

        content = build_material_content(text="材料一的新版本：潮水退了。",
                                         source_kind="user_supplied",
                                         origin="workbench-curio-fixture",
                                         title=self.fixture.MATERIAL_ONE_TITLE)
        outcome = self.stack.port.commit_version(
            artifact_id=self.stack.materials["one"], expected_head=head, content=content,
            grant_id=str(self.stack.data["artifact_grant_id"]),
            operation_key="workbench-curio-material-commit")
        self.assertTrue(outcome.applied, outcome.to_dict())

        status, payload = self._call("GET", "/api/phone/home/workbench", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        material = payload["data"]["materials"][0]
        self.assertEqual(material["status"], "STALE")
        self.assertEqual(material["baseline_source_revision"],
                         baseline["baseline_source_revision"])
        self.assertNotEqual(material["current_source_revision"],
                            material["baseline_source_revision"])

    def test_empty_workbench_is_readable_and_clear_is_idempotent(self):
        status, payload = self._call("GET", "/api/phone/home/workbench")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["counts"],
                         {"images": 0, "materials": 0, "works": 0})
        self.assertEqual(payload["data"]["status"], "empty")
        status, payload = self._call("POST", "/api/phone/home/workbench",
                                     {"action": "clear_tray"}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)

    # -- curios -------------------------------------------------------------------------
    def test_curios_reports_a_real_purchase_and_real_position(self):
        status, payload = self._call("GET", "/api/phone/home/curios")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["no_fabricated_wear"])
        self.assertTrue(data["no_attachment_score"])
        rows = {row["item_instance_id"]: row for row in data["items"]}
        row = rows[self.stack.purchase["item_instance_id"]]
        self.assertEqual(row["name"], "虚拟小点心")
        self.assertEqual(row["acquired"]["source"]["transaction_id"],
                         self.stack.purchase["transaction_id"])
        self.assertEqual(row["acquired"]["source"]["purchase_intent_id"],
                         self.stack.purchase["purchase_intent_id"])
        self.assertTrue(row["acquired"]["acquired_at"])
        self.assertEqual(row["position"]["status"], "available")
        self.assertEqual(row["position"]["kind"], "room")
        self.assertEqual(row["position"]["location_id"], "home")
        self.assertEqual(row["position"]["area_id"], "bedroom")
        self.assertIsNone(row["remaining_uses"]["value"])
        self.assertEqual(row["remaining_uses"]["reason_code"],
                         view.NO_REMAINING_USES_AUTHORITY)

    # -- trio exchange ------------------------------------------------------------------
    def test_partner_cards_need_only_csrf_and_never_touch_her_picks(self):
        cards = [{"title": "海边的石头", "description": "灰蓝色，被浪磨圆"},
                 {"title": "旧钥匙", "description": "不知道开哪扇门"}]
        status, payload = self._call("POST", "/api/phone/home/trio-exchange",
                                     {"action": "set_partner_picks",
                                      "partner_picks": cards}, csrf=True)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/home/trio-exchange")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["partner"]["count"], 2)
        self.assertTrue(data["partner_contributed"])
        self.assertEqual(data["her"]["count"], 0)
        self.assertTrue(data["no_obligation"])
        self.assertTrue(all(row["partner_contributed"] for row in data["partner"]["picks"]))
        self.assertIn("不代表她看过", data["note"])

        status, payload = self._call("POST", "/api/phone/home/trio-exchange",
                                     {"action": "set_partner_picks",
                                      "partner_picks": cards}, csrf=True)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)

    def test_partner_cards_reject_over_count_wrong_fields_and_wrong_capability(self):
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_partner_picks",
             "partner_picks": [{"title": "a%d" % n, "description": ""} for n in range(4)]},
            csrf=True)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "TRIO_PARTNER_PICKS_INVALID")
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_partner_picks",
             "partner_picks": [{"title": "夹带", "description": "", "share_id": "x"}]},
            csrf=True)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "TRIO_PARTNER_PICKS_INVALID")
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_partner_picks", "partner_picks": [],
             "her_picks": [self.stack.delivered_share_id]}, csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:her_picks")
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_partner_picks", "partner_picks": []},
            csrf=True, capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")

    def test_foreign_fields_are_refused_by_presence_even_when_empty(self):
        """A field the action does not take is refused by presence, not truthiness.

        ``her_picks: []`` / ``null`` / ``""`` on a partner action (and the mirror case on a her
        action) used to slip through because the old check compared values.  Refusal must leave
        the selection state byte-for-byte untouched while the legitimate sides keep working.
        """
        trio = view.trio_path(self.stack.root)
        before_bytes = trio.read_bytes()
        state, reason = view.read_trio_state(self.stack.root)
        self.assertIsNone(reason)
        self.assertEqual(state["revision"], 0)
        card = [{"title": "海边的石头", "description": "灰蓝色的一小块，先放在这里"}]

        for value in ([], None, ""):
            status, payload = self._call(
                "POST", "/api/phone/home/trio-exchange",
                {"action": "set_partner_picks", "partner_picks": card,
                 "her_picks": value}, csrf=True)
            self.assertEqual(status, 400, payload)
            self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:her_picks")
        for value in ([], None, ""):
            status, payload = self._call(
                "POST", "/api/phone/home/trio-exchange",
                {"action": "set_her_picks",
                 "her_picks": [self.stack.delivered_share_id],
                 "partner_picks": value}, csrf=True, capability=CAP_TOKEN)
            self.assertEqual(status, 400, payload)
            self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:partner_picks")

        after_state, reason = view.read_trio_state(self.stack.root)
        self.assertIsNone(reason)
        self.assertEqual(after_state, state)
        self.assertEqual(trio.read_bytes(), before_bytes)

        # The legitimate sides still go through exactly as before.
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_partner_picks", "partner_picks": card}, csrf=True)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_OK)
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_her_picks", "her_picks": [self.stack.delivered_share_id]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/home/trio-exchange")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["her"]["count"], 1)
        self.assertEqual(payload["data"]["partner"]["count"], 1)

        # ``clear_tray`` takes only its action field; an empty-valued extra field is still
        # a field that action does not take.
        tray = view.tray_path(self.stack.root)
        tray_before = tray.read_bytes()
        for value in ([], None, ""):
            status, payload = self._call(
                "POST", "/api/phone/home/workbench",
                {"action": "clear_tray", "album_artifact_ids": value},
                csrf=True, capability=CAP_TOKEN)
            self.assertEqual(status, 400, payload)
            self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:album_artifact_ids")
        self.assertEqual(tray.read_bytes(), tray_before)
        status, payload = self._call(
            "POST", "/api/phone/home/workbench", {"action": "clear_tray"},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)

    def test_her_picks_need_capability_and_only_real_delivered_shares(self):
        body = {"action": "set_her_picks",
                "her_picks": [self.stack.delivered_share_id]}
        status, payload = self._call("POST", "/api/phone/home/trio-exchange", body,
                                     csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._call("POST", "/api/phone/home/trio-exchange", body,
                                     csrf=True, capability="wrong-token")
        self.assertEqual(status, 403, payload)
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_her_picks", "her_picks": [self.stack.draft_share_id]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "SHARE_NOT_DELIVERED")
        self.assertEqual(view.read_trio_state(self.stack.root)[0]["revision"], 0)

        status, payload = self._call("POST", "/api/phone/home/trio-exchange", body,
                                     csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/home/trio-exchange")
        self.assertEqual(status, 200, payload)
        row = payload["data"]["her"]["picks"][0]
        self.assertEqual(row["status"], "available")
        self.assertFalse(row["text_withheld"])
        self.assertTrue(row["text"])
        self.assertEqual(row["share_id"], self.stack.delivered_share_id)
        self.assertFalse(row["source_text_included"])

        status, payload = self._call("POST", "/api/phone/home/trio-exchange", body,
                                     csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)

    def test_her_pick_withholds_when_the_source_is_governed(self):
        # A dedicated work + delivered share keeps the seeded letter work intact; the owner's
        # own tombstone verb then makes the source genuinely unreadable (governance, not archive).
        created = self.stack.service.create_project(
            title="会消失的小作品", content="这件作品稍后会被治理掉。",
            operation_key="workbench-curio-governed-create")
        self.assertTrue(created.get("ok"), created)
        artifact_id = str(created["data"]["artifact_id"])
        share_id = self.fixture.keepsake_fixture._send_local(
            self.stack, source_id=artifact_id, text="先分享一句，之后来源会被治理。",
            key="workbench-curio-governed-share")
        status, payload = self._call(
            "POST", "/api/phone/home/trio-exchange",
            {"action": "set_her_picks", "her_picks": [share_id]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)

        read = self.stack.port.read_artifact(artifact_id)
        revision = int((read.get("artifact") or {}).get("revision") or 0)
        tombstone = self.fixture.keepsake_fixture._supply_call(
            self.stack.services.supply_path, {
                "op": "tombstone",
                "grant_id": str(self.stack.data["artifact_grant_id"]),
                "operation_key": "workbench-curio-governed-tombstone",
                "artifact": {"subject_id": SUBJECT, "artifact_id": artifact_id,
                             "expected_revision": revision},
            })
        self.assertIn(tombstone.get("status"), ("APPLIED", "OK"), tombstone)

        status, payload = self._call("GET", "/api/phone/home/trio-exchange")
        self.assertEqual(status, 200, payload)
        row = payload["data"]["her"]["picks"][0]
        self.assertEqual(row["status"], "withheld")
        self.assertEqual(row["reason_code"], "SHARE_SOURCE_NOT_READABLE")
        self.assertTrue(row["text_withheld"])
        self.assertIsNone(row["text"])

    # -- zero write ---------------------------------------------------------------------
    def _snapshot(self) -> dict[str, str]:
        from world_body_substrate import substrate_path

        snapshot: dict[str, str] = {}
        world_home = Path(str(self.stack.data["world_home"]))

        def add(path: Path) -> None:
            if path.is_file() and not path.name.endswith(".sock"):
                snapshot[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

        for path in (view.tray_path(self.stack.root), view.trio_path(self.stack.root),
                     world_home / "state" / "world_property_economy.json",
                     substrate_path(world_home)):
            add(Path(path))
        for artifact in (self.stack.work_a, self.stack.materials["one"],
                         self.stack.pictures["one"]):
            read = self.stack.port.read_artifact(artifact)
            snapshot["head:" + artifact] = str((read.get("head") or {}).get("version_id"))
            snapshot["revision:" + artifact] = str((read.get("artifact") or {}).get("revision"))
        snapshot["shares"] = json.dumps(self.stack.service.shares(), ensure_ascii=False,
                                        sort_keys=True)
        return snapshot

    def test_repeated_gets_change_no_state_bytes(self):
        status, payload = self._set_tray()
        self.assertEqual(status, 200, payload)
        self._call("POST", "/api/phone/home/trio-exchange",
                   {"action": "set_partner_picks",
                    "partner_picks": [{"title": "一张票根", "description": "旧电影"}]},
                   csrf=True)
        before = self._snapshot()
        paths = ("/api/phone/home/workbench", "/api/phone/home/curios",
                 "/api/phone/home/trio-exchange")
        first = [self._call("GET", path)[1] for path in paths]
        second = [self._call("GET", path)[1] for path in paths]
        self.assertEqual(first, second)
        self.assertEqual(before, self._snapshot())


if __name__ == "__main__":
    unittest.main()
