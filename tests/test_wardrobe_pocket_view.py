#!/usr/bin/env python3
"""衣柜 / 小列表 / 作品旧版并排：纯投影 + 真实 owner loopback HTTP。

上半部分只喂真实形状的 economy/state 事实给纯投影函数，覆盖产权核对、默认扣留、
状态语义、列表上限、摘要边界与冻结文件；
下半部分（POSIX）用 ``wardrobe_pocket_fixture.build_stack``：真实 Supply/economy/购买/
相册/作品版本/正式分享与归档，再走真实 HTTP 三读两写。
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
from components.phone import wardrobe_pocket_view as view  # noqa: E402

SUBJECT = "chiyo"
CSRF = "wardrobe-pocket-csrf"
CAP_TOKEN = "{{SECRET_wp7q2n4v}}"
OTHER_SUBJECT = "someone_else"
NOW = "2026-11-11T10:00:00+08:00"

FROZEN_SHA256 = {
    "components/life/scripts/autonomous_life_assembly.py":
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}


class WardrobeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="wardrobe-state-"))

    def test_set_is_explicit_and_replay_keeps_revision(self):
        state, changed = view.apply_wardrobe_set(
            view.empty_wardrobe_state(),
            {"homewear_style": "宽松针织", "blanket_season": "winter"}, NOW)
        self.assertTrue(changed)
        self.assertEqual(state["revision"], 1)
        replay, changed = view.apply_wardrobe_set(
            state, {"homewear_style": "宽松针织", "blanket_season": "winter"},
            "2026-11-11T11:00:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(replay["updated_at"], NOW)

    def test_clear_only_named_fields_and_replay_is_noop(self):
        state, _ = view.apply_wardrobe_set(
            view.empty_wardrobe_state(),
            {"pairing_note": "和海边的颜色呼应", "cushion_season": "summer"}, NOW)
        cleared, changed = view.apply_wardrobe_clear(
            state, ["pairing_note"], "2026-11-11T10:05:00+08:00")
        self.assertTrue(changed)
        self.assertIsNone(cleared["selection"]["pairing_note"])
        self.assertEqual(cleared["selection"]["cushion_season"], "summer")
        again, changed = view.apply_wardrobe_clear(
            cleared, ["pairing_note"], "2026-11-11T10:06:00+08:00")
        self.assertFalse(changed)
        with self.assertRaises(view.PocketStateError):
            view.apply_wardrobe_clear(cleared, [], NOW)
        with self.assertRaises(view.PocketStateError):
            view.apply_wardrobe_clear(cleared, ["not_a_field"], NOW)

    def test_state_round_trip_and_corrupt_state_fails_closed(self):
        state, _ = view.apply_wardrobe_set(
            view.empty_wardrobe_state(), {"hairclip_style": "木夹"}, NOW)
        view.write_wardrobe_state(self.root, state)
        loaded, reason = view.read_wardrobe_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(loaded["selection"]["hairclip"]["style"], "木夹")
        view.wardrobe_state_path(self.root).write_text("{not json", encoding="utf-8")
        fallback, reason = view.read_wardrobe_state(self.root)
        self.assertEqual(reason, "WARDROBE_STATE_UNREADABLE")
        self.assertEqual(fallback, view.empty_wardrobe_state())

    def test_owned_item_check_rejects_every_non_honest_reference(self):
        economy = {"items": [
            {"item_instance_id": "item_snack", "catalog_item_id": "virtual_snack_wafer",
             "owner": SUBJECT, "disposition": "OWNED"},
            {"item_instance_id": "item_foreign", "catalog_item_id": "ribbon_clip",
             "owner": OTHER_SUBJECT, "disposition": "OWNED"},
            {"item_instance_id": "item_gifted", "catalog_item_id": "ribbon_clip",
             "owner": SUBJECT, "disposition": "GIFTED_AWAY"},
            {"item_instance_id": "item_ribbon", "catalog_item_id": "ribbon_clip",
             "owner": SUBJECT, "disposition": "OWNED"},
        ]}
        self.assertEqual(view.owned_item_check(
            economy, subject=SUBJECT, slot="hairband",
            item_instance_id="item_missing")["reason_code"], "ITEM_NOT_FOUND")
        self.assertEqual(view.owned_item_check(
            economy, subject=SUBJECT, slot="hairband",
            item_instance_id="item_foreign")["reason_code"], "ITEM_NOT_OWNED_BY_SUBJECT")
        self.assertEqual(view.owned_item_check(
            economy, subject=SUBJECT, slot="hairband",
            item_instance_id="item_gifted")["reason_code"], "ITEM_NOT_OWNED")
        # The current catalogue declares no wardrobe item: a real owned snack is not a hairband.
        self.assertEqual(view.owned_item_check(
            economy, subject=SUBJECT, slot="hairband",
            item_instance_id="item_snack")["reason_code"], "ITEM_CATEGORY_NOT_APPLICABLE")
        checked = view.owned_item_check(
            economy, subject=SUBJECT, slot="hairband", item_instance_id="item_ribbon",
            catalog_ids={"hairband": frozenset({"ribbon_clip"}),
                         "hairclip": frozenset(), "homewear": frozenset(),
                         "outdoor": frozenset()})
        self.assertEqual(checked["status"], "verified")
        self.assertEqual(checked["catalog_item_id"], "ribbon_clip")

    def test_related_owned_items_is_empty_for_a_snack_only_economy(self):
        related = view.related_owned_items(
            {"items": [{"item_instance_id": "item_snack",
                        "catalog_item_id": "virtual_snack_wafer",
                        "owner": SUBJECT, "disposition": "OWNED"}]},
            subject=SUBJECT)
        self.assertEqual(related["items"], [])
        self.assertEqual(related["status"], "none")
        self.assertEqual(related["reason_code"], view.NO_OWNED_RELEVANT_ITEMS)
        missing = view.related_owned_items(None, subject=SUBJECT)
        self.assertEqual(missing["reason_code"], view.GENESIS_NOT_INITIALIZED)


class PocketStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pocket-state-"))

    def test_playlist_resting_keeps_reason_and_shared_defaults_false(self):
        state, changed = view.pocket_add(
            view.empty_pocket_state(), kind="playlist", entry_id="pl_1",
            title="夜航歌单", values={"reason": "低音量刚好"}, status=None, shared=None,
            operation_key="op-1", now_iso=NOW)
        self.assertTrue(changed)
        entry = state["lists"]["playlist"][0]
        self.assertFalse(entry["shared"])
        updated, changed = view.pocket_update(
            state, kind="playlist", entry_id="pl_1", values={}, status="resting",
            shared=None, now_iso="2026-11-11T10:01:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(updated["lists"]["playlist"][0]["status"], "resting")
        self.assertEqual(updated["lists"]["playlist"][0]["reason"], "低音量刚好")

    def test_favorites_reviewed_is_self_report_and_want_states_carry_no_obligation(self):
        state, _ = view.pocket_add(
            view.empty_pocket_state(), kind="favorites", entry_id="fa_1",
            title="一本旧书", values={"expectation": "读前期待", "verdict": "读后取舍"},
            status="reviewed", shared=True, operation_key="op-2", now_iso=NOW)
        self.assertEqual(state["lists"]["favorites"][0]["status"], "reviewed")
        block = view.pocket_block(state=state, state_reason=None, subject_view=True)
        self.assertTrue(block["no_obligation"])
        self.assertTrue(block["no_future_items_created"])
        self.assertFalse(block["reading_or_model_calls"])
        self.assertFalse(block["memory_writes"])
        for status in ("active", "paused", "dropped", "done"):
            wound, _ = view.pocket_add(
                state, kind="want_to_try", entry_id="wt_" + status, title="想试试",
                values={"note": "先记下"}, status=status, shared=False,
                operation_key="op-w-" + status, now_iso=NOW)
            self.assertTrue(wound["lists"]["want_to_try"])

    def test_cross_category_status_and_list_cap_are_refused(self):
        with self.assertRaises(view.PocketStateError) as caught:
            view.pocket_add(
                view.empty_pocket_state(), kind="playlist", entry_id="pl_bad",
                title="歌单", values={}, status="reviewed", shared=None,
                operation_key="op-bad", now_iso=NOW)
        self.assertEqual(caught.exception.reason_code, "POCKET_STATUS_NOT_FOR_LIST")
        state = view.empty_pocket_state()
        for index in range(view.MAX_LIST_ENTRIES):
            state, _ = view.pocket_add(
                state, kind="want_to_try", entry_id="wt_%d" % index, title="想试 %d" % index,
                values={}, status=None, shared=False, operation_key="op-cap-%d" % index,
                now_iso=NOW)
        with self.assertRaises(view.PocketStateError) as caught:
            view.pocket_add(
                state, kind="want_to_try", entry_id="wt_over", title="多一条", values={},
                status=None, shared=False, operation_key="op-over", now_iso=NOW)
        self.assertEqual(caught.exception.reason_code, "POCKET_LIST_FULL")

    def test_default_view_withholds_private_ids_reasons_and_counts(self):
        state, _ = view.pocket_add(
            view.empty_pocket_state(), kind="playlist", entry_id="pl_secret",
            title="公开一首", values={"reason": "只给她看的理由"}, status="active",
            shared=True, operation_key="op-3", now_iso=NOW)
        state, _ = view.pocket_add(
            state, kind="favorites", entry_id="fa_private", title="私藏书",
            values={"expectation": "私密期待"}, status="saved_unread", shared=False,
            operation_key="op-4", now_iso=NOW)
        partner, _ = view.pocket_set_partner_suggestions(
            state, [{"title": "一起听", "note": "慢一点"}], NOW)
        default = view.pocket_block(state=partner, state_reason=None, subject_view=False)
        self.assertEqual(default["counts"]["playlist"], 1)
        self.assertEqual(default["counts"]["favorites"], 0)
        self.assertNotIn("私密期待", json.dumps(default, ensure_ascii=False))
        self.assertNotIn("只给她看的理由", json.dumps(default, ensure_ascii=False))
        self.assertNotIn("entry_id", json.dumps(default, ensure_ascii=False))
        self.assertTrue(default["partner_suggestions"][0]["partner_contributed"])
        subject = view.pocket_block(state=partner, state_reason=None, subject_view=True)
        self.assertEqual(subject["counts"]["favorites"], 1)
        self.assertIn("只给她看的理由", json.dumps(subject, ensure_ascii=False))


class WorkRevisitProjectionTest(unittest.TestCase):
    def test_excerpt_is_bounded_and_marks_truncation(self):
        excerpt = view.version_excerpt("x" * (view.MAX_EXCERPT + 20))
        self.assertEqual(excerpt["excerpt_chars"], view.MAX_EXCERPT)
        self.assertTrue(excerpt["excerpt_truncated"])
        short = view.version_excerpt("短句")
        self.assertFalse(short["excerpt_truncated"])

    def test_comparison_scope_differs_for_owner_and_partner(self):
        owner_left = view.owner_version_side(
            version={"content": "甲", "version_number": 1, "content_sha256": "hash-a"},
            version_id="v1", lifecycle="ACTIVE")
        owner_right = view.owner_version_side(
            version={"content": "乙", "version_number": 3, "content_sha256": "hash-b"},
            version_id="v3", lifecycle="ACTIVE")
        owner = view.comparison_block(left=owner_left, right=owner_right, subject_view=True)
        self.assertTrue(owner["changed"])
        self.assertEqual(owner["scope"], "full_version_content")
        self.assertTrue(owner["source_text_change_provable"])
        partner_left = view.delivered_share_side(
            version_id="v1", version_number=1, share_id="artifact:share1",
            text="第一版分享", delivered_at=NOW)
        partner_right = view.delivered_share_side(
            version_id="v2", version_number=2, share_id="artifact:share2",
            text="第二版分享", delivered_at=NOW)
        partner = view.comparison_block(left=partner_left, right=partner_right,
                                        subject_view=False)
        self.assertEqual(partner["scope"], "delivered_share_text_only")
        self.assertFalse(partner["source_text_change_provable"])
        self.assertFalse(partner["diff_fragments_included"])
        withheld = view.withheld_side(version_id="v3", version_number=3,
                                      reason_code=view.VERSION_NOT_DELIVERED,
                                      excerpt_basis=view.DELIVERED_SHARE_BASIS)
        blocked = view.comparison_block(left=partner_left, right=withheld, subject_view=False)
        self.assertIsNone(blocked["changed"])
        self.assertEqual(blocked["reason_code"], "COMPARISON_SIDE_WITHHELD")


class FrozenFilesTest(unittest.TestCase):
    def test_frozen_files_keep_their_baseline_sha256(self):
        for relative, expected in FROZEN_SHA256.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply/World/economy 与 Life socket 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class WardrobePocketOwnerHTTPTest(unittest.TestCase):
    """Real owners, real loopback HTTP: wardrobe / lists / work-revisit."""

    @classmethod
    def setUpClass(cls) -> None:
        import wardrobe_pocket_fixture as fixture

        cls.fixture = fixture
        cls.root = Path(tempfile.mkdtemp(prefix="wardrobe-pocket-"))
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
        # The two selection files are this feature's own scratch state.  Owner facts
        # (economy/versions/shares/archive) are never reset or rewritten.
        view.write_wardrobe_state(self.stack.root, view.empty_wardrobe_state())
        view.write_pocket_state(self.stack.root, view.empty_pocket_state())

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

    def _wardrobe_set(self, body: dict) -> tuple[int, dict]:
        return self._call("POST", "/api/phone/home/wardrobe",
                          dict(body, action="set"), csrf=True, capability=CAP_TOKEN)

    def _pocket(self, body: dict) -> tuple[int, dict]:
        return self._call("POST", "/api/phone/pocket/lists", body, csrf=True,
                          capability=CAP_TOKEN)

    def _revisit(self, *, artifact_id: str, left: str, right: str,
                 capability: str | None = None) -> tuple[int, dict]:
        from urllib.parse import urlencode

        query = urlencode({"artifact_id": artifact_id, "left_version_id": left,
                           "right_version_id": right})
        return self._call("GET", "/api/phone/pocket/work-revisit?" + query,
                          capability=capability)

    # -- wardrobe ------------------------------------------------------------------------
    def test_wardrobe_default_and_subject_views_are_separated(self):
        status, payload = self._call("GET", "/api/phone/home/wardrobe")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "partner_default")
        self.assertIsNone(data["outfit"]["homewear"]["style"])
        self.assertIsNone(data["pairing_note"])
        self.assertFalse(data["pairing_note_included"])
        self.assertTrue(data["selection_only"])
        self.assertTrue(data["not_ownership_claim"])
        self.assertTrue(data["does_not_change_somatic_garment_state"])
        self.assertEqual(data["related_owned_items"], [])
        self.assertEqual(data["related_owned_items_status"]["reason_code"],
                         view.NO_OWNED_RELEVANT_ITEMS)
        status, payload = self._call("GET", "/api/phone/home/wardrobe",
                                     capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["view"], "subject")

    def test_wardrobe_item_reference_requires_a_real_owned_applicable_instance(self):
        before = view.wardrobe_state_path(self.stack.root).read_bytes()
        snack = self.stack.purchase["item_instance_id"]
        status, payload = self._wardrobe_set({"hairband_item_instance_id": snack})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "ITEM_CATEGORY_NOT_APPLICABLE")
        status, payload = self._wardrobe_set(
            {"hairclip_item_instance_id": "item_deadbeefdeadbeef"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "ITEM_NOT_FOUND")
        self.assertEqual(view.wardrobe_state_path(self.stack.root).read_bytes(), before)

    def test_wardrobe_write_gates_and_strict_fields(self):
        body = {"action": "set", "homewear_style": "针织"}
        status, payload = self._call("POST", "/api/phone/home/wardrobe", body)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._call("POST", "/api/phone/home/wardrobe", body, csrf=True)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._call("POST", "/api/phone/home/wardrobe", body, csrf=True,
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        for bad in ({"unexpected": []}, {"fields": []}, {"homewear_style": ""},
                    {"blanket_season": "monsoon"}):
            status, payload = self._wardrobe_set(bad)
            self.assertEqual(status, 400, payload)
        status, payload = self._call(
            "POST", "/api/phone/home/wardrobe",
            {"action": "clear", "fields": []}, csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "WARDROBE_CLEAR_FIELDS_REQUIRED")

    def test_wardrobe_set_and_clear_round_trip_with_idempotent_replay(self):
        body = {"homewear_style": "宽松针织", "outdoor_style": "深色外套",
                "hairband_style": "细发带", "hairclip_style": "木夹",
                "pairing_note": "和海边的颜色呼应", "blanket_season": "winter",
                "cushion_season": "summer"}
        status, payload = self._wardrobe_set(body)
        self.assertEqual(status, 200, payload)
        revision = payload["data"]["revision"]
        status, payload = self._wardrobe_set(body)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(payload["data"]["revision"], revision)
        status, payload = self._call("GET", "/api/phone/home/wardrobe", capability=CAP_TOKEN)
        data = payload["data"]
        self.assertEqual(data["outfit"]["homewear"]["style"], "宽松针织")
        self.assertEqual(data["accessories"]["hairclip"]["style"], "木夹")
        self.assertEqual(data["pairing_note"], "和海边的颜色呼应")
        self.assertEqual(data["season_textiles"]["blanket"]["season"], "winter")
        self.assertEqual(data["season_textiles"]["cushion"]["season"], "summer")
        status, payload = self._call(
            "POST", "/api/phone/home/wardrobe",
            {"action": "clear", "fields": ["pairing_note", "cushion_season"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/home/wardrobe", capability=CAP_TOKEN)
        self.assertIsNone(payload["data"]["pairing_note"])
        self.assertIsNone(payload["data"]["season_textiles"]["cushion"]["season"])
        self.assertEqual(payload["data"]["season_textiles"]["blanket"]["season"], "winter")
        status, payload = self._call(
            "POST", "/api/phone/home/wardrobe",
            {"action": "clear", "fields": ["pairing_note", "cushion_season"]},
            csrf=True, capability=CAP_TOKEN)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)

    # -- pocket lists --------------------------------------------------------------------
    def test_pocket_personal_add_is_private_by_default_and_subject_gets_full(self):
        status, payload = self._pocket({
            "action": "add", "list": "playlist", "title": "夜航歌单",
            "reason": "低音量刚好", "operation_key": "pocket-op-1"})
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/pocket/lists")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["counts"]["playlist"], 0)
        self.assertEqual(data["lists"]["playlist"], [])
        self.assertNotIn("低音量刚好", json.dumps(payload, ensure_ascii=False))
        status, payload = self._call("GET", "/api/phone/pocket/lists",
                                     capability=CAP_TOKEN)
        entry = payload["data"]["lists"]["playlist"][0]
        self.assertEqual(entry["title"], "夜航歌单")
        self.assertEqual(entry["reason"], "低音量刚好")
        self.assertFalse(entry["shared"])
        self.assertTrue(entry["entry_id"])

    def test_pocket_shared_toggle_shows_then_withholds_immediately(self):
        self._pocket({"action": "add", "list": "favorites", "title": "一本旧书",
                      "expectation": "读前期待", "operation_key": "pocket-op-2"})
        status, payload = self._call("GET", "/api/phone/pocket/lists", capability=CAP_TOKEN)
        entry = payload["data"]["lists"]["favorites"][0]
        entry_id = entry["entry_id"]
        status, payload = self._pocket({
            "action": "update", "list": "favorites", "entry_id": entry_id,
            "shared": True})
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/pocket/lists")
        shared = payload["data"]["lists"]["favorites"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["title"], "一本旧书")
        self.assertNotIn("entry_id", json.dumps(shared, ensure_ascii=False))
        self.assertNotIn("读前期待", json.dumps(payload, ensure_ascii=False))
        status, payload = self._pocket({
            "action": "update", "list": "favorites", "entry_id": entry_id,
            "shared": False})
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/pocket/lists")
        self.assertEqual(payload["data"]["lists"]["favorites"], [])

    def test_partner_suggestions_need_csrf_only_are_capped_and_cannot_smuggle(self):
        self._pocket({"action": "add", "list": "playlist", "title": "她自己的歌单",
                      "operation_key": "pocket-op-3"})
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions",
             "suggestions": [{"title": "一起听", "note": "慢一点"}]})
        self.assertEqual(status, 403, payload)
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions",
             "suggestions": [{"title": "一起听", "note": "慢一点"}]}, csrf=True)
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/pocket/lists")
        suggestions = payload["data"]["partner_suggestions"]
        self.assertEqual(suggestions[0]["title"], "一起听")
        self.assertTrue(suggestions[0]["partner_contributed"])
        # Her own private list is untouched by a partner write.
        self.assertEqual(payload["data"]["counts"]["playlist"], 0)
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions", "suggestions": [], "list": "playlist"},
            csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:list")
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions",
             "suggestions": [{"title": "x", "shared": True}]}, csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"],
                         "UNKNOWN_SUGGESTION_FIELD:shared")
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions",
             "suggestions": [{"title": str(i)} for i in range(4)]}, csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "POCKET_PARTNER_TOO_MANY")
        status, payload = self._call(
            "POST", "/api/phone/pocket/lists",
            {"action": "set_partner_suggestions", "suggestions": []}, csrf=True)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["changed"])
        status, payload = self._call("GET", "/api/phone/pocket/lists")
        self.assertEqual(payload["data"]["partner_suggestions"], [])

    def test_pocket_status_vocabulary_is_finite_and_cross_category_is_refused(self):
        status, payload = self._pocket({
            "action": "add", "list": "playlist", "title": "休息中的歌单",
            "reason": "先留着理由", "status": "resting", "operation_key": "pocket-op-4"})
        self.assertEqual(status, 200, payload)
        status, payload = self._pocket({
            "action": "add", "list": "want_to_try", "title": "想试一次",
            "status": "dropped", "operation_key": "pocket-op-5"})
        self.assertEqual(status, 200, payload)
        status, payload = self._call("GET", "/api/phone/pocket/lists", capability=CAP_TOKEN)
        playlist = payload["data"]["lists"]["playlist"][0]
        self.assertEqual(playlist["status"], "resting")
        self.assertEqual(playlist["reason"], "先留着理由")
        self.assertEqual(payload["data"]["lists"]["want_to_try"][0]["status"], "dropped")
        self.assertTrue(payload["data"]["no_obligation"])
        self.assertTrue(payload["data"]["no_future_items_created"])
        status, payload = self._pocket({
            "action": "add", "list": "playlist", "title": "错状态",
            "status": "reviewed", "operation_key": "pocket-op-6"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "POCKET_STATUS_NOT_FOR_LIST")

    # -- work revisit --------------------------------------------------------------------
    def test_work_revisit_owner_and_partner_disclosure_basis(self):
        revisit = self.stack.revisit
        status, payload = self._revisit(artifact_id=revisit["artifact_id"],
                                        left=revisit["v1"], right=revisit["v3"],
                                        capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "subject")
        self.assertFalse(data["withheld"])
        self.assertEqual(data["versions"]["left"]["excerpt_basis"],
                         view.OWNER_EXCERPT_BASIS)
        self.assertEqual(data["versions"]["right"]["excerpt_basis"],
                         view.OWNER_EXCERPT_BASIS)
        self.assertTrue(data["versions"]["left"]["excerpt"].startswith(
            self.fixture.REVISIT_V1_TEXT[:10]))
        self.assertTrue(data["versions"]["right"]["excerpt"].startswith(
            self.fixture.REVISIT_V3_TEXT[:10]))
        self.assertTrue(data["comparison"]["changed"])
        self.assertEqual(data["comparison"]["scope"], "full_version_content")
        self.assertFalse(data["comparison"]["diff_fragments_included"])

        status, payload = self._revisit(artifact_id=revisit["artifact_id"],
                                        left=revisit["v1"], right=revisit["v2"])
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "partner_default")
        left = data["versions"]["left"]
        right = data["versions"]["right"]
        self.assertEqual(left["excerpt_basis"], view.DELIVERED_SHARE_BASIS)
        self.assertEqual(right["excerpt_basis"], view.DELIVERED_SHARE_BASIS)
        self.assertEqual(left["excerpt"], self.fixture.REVISIT_V1_SHARE)
        self.assertEqual(right["excerpt"], self.fixture.REVISIT_V2_SHARE)
        self.assertEqual(data["comparison"]["scope"], "delivered_share_text_only")
        self.assertFalse(data["comparison"]["source_text_change_provable"])
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.fixture.REVISIT_V1_TEXT, self.fixture.REVISIT_V2_TEXT,
                       self.fixture.REVISIT_V3_TEXT):
            self.assertNotIn(secret, dumped)

        status, payload = self._revisit(artifact_id=revisit["artifact_id"],
                                        left=revisit["v1"], right=revisit["v3"])
        self.assertEqual(status, 200, payload)
        right = payload["data"]["versions"]["right"]
        self.assertEqual(right["status"], "withheld")
        self.assertEqual(right["reason_code"], view.VERSION_NOT_DELIVERED)
        self.assertTrue(payload["data"]["withheld"])
        self.assertNotIn(self.fixture.REVISIT_V3_TEXT,
                         json.dumps(payload, ensure_ascii=False))

    def test_work_revisit_refuses_cross_work_and_withholds_archived_and_non_project(self):
        revisit = self.stack.revisit
        status, payload = self._revisit(artifact_id=revisit["artifact_id"],
                                        left=revisit["v1"],
                                        right=self.stack.other_work_version,
                                        capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], view.VERSION_NOT_IN_ARTIFACT)

        archived = self.stack.archived
        status, payload = self._revisit(artifact_id=archived["artifact_id"],
                                        left=archived["v1"], right=archived["v2"],
                                        capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["withheld"])
        self.assertEqual(data["artifact"]["reason_code"], view.ARCHIVED_SOURCE)
        self.assertEqual(data["versions"]["left"]["status"], "withheld")
        self.assertEqual(data["versions"]["right"]["status"], "withheld")
        dumped = json.dumps(payload, ensure_ascii=False)
        for secret in (self.fixture.ARCHIVE_V1_TEXT, self.fixture.ARCHIVE_V2_TEXT):
            self.assertNotIn(secret, dumped)

        status, payload = self._revisit(
            artifact_id=self.stack.keepsakes["album_artifact_id"],
            left=revisit["v1"], right=revisit["v2"], capability=CAP_TOKEN)
        self.assertIn(status, (404, 409), payload)
        self.assertIn(payload["reason_code"], (view.NOT_A_PROJECT,
                                               "PROJECT_IS_AN_ALBUM_IMAGE"))
        status, payload = self._revisit(
            artifact_id=revisit["v1_share"], left=revisit["v1"],
            right=revisit["v2"], capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "SHARE_ENVELOPE_NOT_A_PROJECT")

    def test_work_revisit_query_is_strict(self):
        revisit = self.stack.revisit
        base = "/api/phone/pocket/work-revisit"
        from urllib.parse import urlencode

        status, payload = self._call(
            "GET", base + "?" + urlencode({"artifact_id": revisit["artifact_id"],
                                           "left_version_id": revisit["v1"]}))
        self.assertEqual(status, 400, payload)
        self.assertIn("WORK_REVISIT_QUERY_REQUIRED", payload["reason_code"])
        status, payload = self._call(
            "GET", base + "?" + urlencode({"artifact_id": revisit["artifact_id"],
                                           "left_version_id": revisit["v1"],
                                           "right_version_id": revisit["v2"],
                                           "foo": "1"}))
        self.assertEqual(status, 400, payload)
        self.assertIn("UNKNOWN_QUERY", payload["reason_code"])
        status, payload = self._call(
            "GET", base + "?artifact_id=a&artifact_id=b&left_version_id=c"
                   "&right_version_id=d")
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "QUERY_REPEATED:artifact_id")
        status, payload = self._call(
            "GET", base + "?" + urlencode({"artifact_id": revisit["artifact_id"],
                                           "left_version_id": revisit["v1"],
                                           "right_version_id": revisit["v1"]}))
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "VERSIONS_NOT_DISTINCT")

    # -- zero write ----------------------------------------------------------------------
    def _snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for base in (self.stack.instance_dir, self.stack.root / "data"):
            for path in sorted(Path(base).rglob("*")):
                if not path.is_file() or path.name.endswith(".sock"):
                    continue
                snapshot[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for artifact in (self.stack.work_a, self.stack.work_b,
                         self.stack.revisit["artifact_id"],
                         self.stack.archived["artifact_id"]):
            read = self.stack.port.read_artifact(artifact)
            snapshot["head:" + artifact] = str((read.get("head") or {}).get("version_id"))
            snapshot["revision:" + artifact] = str(
                (read.get("artifact") or {}).get("revision"))
        snapshot["shares"] = json.dumps(self.stack.service.shares(), ensure_ascii=False,
                                        sort_keys=True)
        snapshot["wardrobe"] = view.wardrobe_state_path(
            self.stack.root).read_text(encoding="utf-8")
        snapshot["pocket"] = view.pocket_state_path(
            self.stack.root).read_text(encoding="utf-8")
        return snapshot

    def test_repeated_gets_change_no_state_bytes_or_owner_heads(self):
        self._wardrobe_set({"homewear_style": "针织"})
        self._pocket({"action": "add", "list": "playlist", "title": "歌单",
                      "operation_key": "pocket-zero"})
        revisit = self.stack.revisit
        before = self._snapshot()
        first = [self._call("GET", "/api/phone/home/wardrobe")[1],
                 self._call("GET", "/api/phone/pocket/lists")[1],
                 self._revisit(artifact_id=revisit["artifact_id"], left=revisit["v1"],
                               right=revisit["v2"])[1],
                 self._revisit(artifact_id=revisit["artifact_id"], left=revisit["v1"],
                               right=revisit["v2"], capability=CAP_TOKEN)[1]]
        second = [self._call("GET", "/api/phone/home/wardrobe")[1],
                  self._call("GET", "/api/phone/pocket/lists")[1],
                  self._revisit(artifact_id=revisit["artifact_id"], left=revisit["v1"],
                                right=revisit["v2"])[1],
                  self._revisit(artifact_id=revisit["artifact_id"], left=revisit["v1"],
                                right=revisit["v2"], capability=CAP_TOKEN)[1]]
        self.assertEqual(first, second)
        self.assertEqual(before, self._snapshot())


if __name__ == "__main__":
    unittest.main()
