#!/usr/bin/env python3
"""居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定：纯投影 + 真实 owner loopback HTTP。

两层验证：

* 上半部分只喂真实形状的快照给纯投影函数：重复抓取不增 revision、身份不可比、
  未知页码/位置不猜、真实 id 集合而非计数、菜谱/暂定的重放与上限、待定不冒充安排；
* 下半部分（POSIX）用 ``home_hearth_fixture.build_stack``：真实 Supply owner、
  ``one_day_life.prepare``、``WorldGateway``、真实阅读停点、真实购买与已投递分享，
  再走真实 HTTP 三读三写（CSRF / capability / 幂等 / 负控 / 零写）。
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
from components.phone import home_hearth_view as view  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "chiyo"
CSRF = "home-hearth-csrf"
CAP_TOKEN = "{{SECRET_hearth9cap}}"


def _reading(*, revision: str = "rev-1", page: int | None = 2,
             status: str = "available", artifact: str = "artifact:read") -> dict:
    return {"status": status, "artifact_id": artifact, "source_revision": revision,
            "version_number": 1, "page": page, "pages": 6,
            "reason_code": None if page is not None else "READING_POSITION_UNKNOWN"}


def _snapshot(*, reading: dict | None = None, shares: list[str] | None = None,
              items: list[dict] | None = None, complete: bool = True) -> dict:
    return {
        "clock": {"source": "business_clock", "logical_at": "2026-11-11T12:00:00+08:00",
                  "reason_code": None, "day": "2026-11-11"},
        "clock_offset": "+08:00",
        "location": {"status": "available", "place_id": "home", "area_id": "bedroom",
                     "home": True, "reason_code": None},
        "reading": reading or _reading(),
        "delivered": {"status": "available",
                      "share_ids": shares if shares is not None else ["artifact:s1"],
                      "count": len(shares) if shares is not None else 1,
                      "truncated": False, "reason_code": None},
        "items": {"status": "available",
                  "items": items if items is not None else [
                      {"item_instance_id": "item-1", "name": "虚拟小点心",
                       "placement": {"status": "available", "kind": "room",
                                     "location_id": "home", "area_id": "bedroom"}}],
                  "count": len(items) if items is not None else 1,
                  "truncated": False, "reason_code": None},
        "complete": complete,
    }


class ReunionProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="hearth-state-"))

    def test_missing_state_is_empty_and_never_creates_a_file(self):
        state, reason = view.read_reunion_state(self.root)
        self.assertIsNone(reason)
        self.assertIsNone(state["anchor"])
        self.assertFalse(view.reunion_path(self.root).exists())
        self.assertFalse((self.root / "phone").exists())
        self.assertIsNone(view.read_recipes_state(self.root)[1])
        self.assertIsNone(view.read_plans_state(self.root)[1])
        self.assertFalse(view.recipes_path(self.root).exists())
        self.assertFalse(view.plans_path(self.root).exists())

    def test_repeated_capture_of_the_same_state_does_not_bump_revision(self):
        snapshot = _snapshot()
        state, changed = view.capture_anchor(view.empty_reunion_state(), snapshot,
                                             "2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(state["revision"], 1)
        replay, changed = view.capture_anchor(state, snapshot, "2026-11-11T12:00:05+08:00")
        self.assertFalse(changed)
        self.assertEqual(replay["revision"], 1)
        moved, changed = view.capture_anchor(replay, _snapshot(shares=["artifact:s1", "artifact:s2"]),
                                             "2026-11-11T12:01:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(moved["revision"], 2)

    def test_unavailable_source_is_not_zero_and_does_not_invent_a_delta(self):
        state, _ = view.capture_anchor(view.empty_reunion_state(), _snapshot(
            shares=["artifact:s1"]), "2026-11-11T12:00:00+08:00")
        anchor = state["anchor"]
        current = {"status": "unavailable", "reason_code": "SHARES_UNAVAILABLE",
                   "share_ids": [], "count": None}
        comparison = view.compare_delivered(anchor["delivered"], current)
        self.assertFalse(comparison["comparable"])
        self.assertIsNone(comparison["new_count"])
        self.assertEqual(comparison["reason_code"], view.DELIVERED_UNAVAILABLE)

    def test_reading_identity_change_and_unknown_page_are_not_deltas(self):
        anchor = _reading(revision="rev-1", page=2)
        same = view.compare_reading(anchor, _reading(revision="rev-1", page=4))
        self.assertTrue(same["comparable"])
        self.assertEqual(same["page_delta"], 2)
        changed = view.compare_reading(anchor, _reading(revision="rev-2", page=4))
        self.assertFalse(changed["comparable"])
        self.assertEqual(changed["reason_code"], view.READING_IDENTITY_CHANGED)
        unknown = view.compare_reading(anchor, _reading(revision="rev-1", page=None))
        self.assertFalse(unknown["comparable"])
        self.assertEqual(unknown["reason_code"], view.READING_POSITION_UNKNOWN)

    def test_new_delivered_shares_use_real_ids_not_a_count_guess(self):
        anchor = {"status": "available", "share_ids": ["artifact:a", "artifact:b"],
                  "count": 2, "truncated": False}
        current = {"status": "available",
                   "share_ids": ["artifact:b", "artifact:a", "artifact:c"],
                   "count": 3, "truncated": False}
        comparison = view.compare_delivered(anchor, current)
        self.assertEqual(comparison["new_share_ids"], ["artifact:c"])
        self.assertEqual(comparison["new_count"], 1)

    def test_unknown_position_is_not_reported_as_moved(self):
        bed = {"status": "available", "kind": "room", "location_id": "home",
               "area_id": "bedroom"}
        anchor = {"status": "available", "count": 1, "truncated": False,
                  "items": [{"item_instance_id": "item-1", "name": "杯子",
                             "placement": bed}]}
        current = {"status": "available", "count": 2, "truncated": False,
                   "items": [{"item_instance_id": "item-1", "name": "杯子",
                              "placement": {"status": "unknown",
                                            "reason_code": "NO_PLACEMENT"}},
                             {"item_instance_id": "item-2", "name": "点心",
                              "placement": bed}]}
        comparison = view.compare_items(anchor, current)
        self.assertEqual(comparison["new_item_ids"], ["item-2"])
        self.assertEqual(comparison["moved"], [])
        self.assertEqual(comparison["moved_unknown"], ["item-1"])

    def test_anchor_never_stores_private_text(self):
        snapshot = _snapshot()
        state, _ = view.capture_anchor(view.empty_reunion_state(), snapshot,
                                       "2026-11-11T12:00:00+08:00")
        dumped = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("text", dumped)
        self.assertNotIn("memo", dumped)
        self.assertNotIn("caption", dumped)
        self.assertTrue(state["anchor"]["no_absence_debt"])


class RecipeStateTest(unittest.TestCase):
    def test_replay_does_not_duplicate_or_bump(self):
        state, changed, card, error = view.upsert_recipe(
            view.empty_recipes_state(), recipe_id=None, title="夜茶",
            memo="热水泡开就好的那种。", author_side="her",
            now_iso="2026-11-11T12:00:00+08:00")
        self.assertIsNone(error)
        self.assertTrue(changed)
        recipe_id = card["recipe_id"]
        replay, changed, card, error = view.upsert_recipe(
            state, recipe_id=None, title="夜茶", memo="热水泡开就好的那种。",
            author_side="her", now_iso="2026-11-11T12:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(len(replay["recipes"]), 1)
        self.assertEqual(replay["recipes"][0]["recipe_id"], recipe_id)
        self.assertEqual(replay["recipes"][0]["revision"], 1)

    def test_edit_bumps_card_and_overall_revision_and_author_is_immutable(self):
        state, _, card, _ = view.upsert_recipe(
            view.empty_recipes_state(), recipe_id="recipe-local", title="夜茶",
            memo="第一版。", author_side="her", now_iso="2026-11-11T12:00:00+08:00")
        edited, changed, card, error = view.upsert_recipe(
            state, recipe_id="recipe-local", title="夜茶",
            memo="第二版，改小火。", author_side="her",
            now_iso="2026-11-11T12:10:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(card["revision"], 2)
        self.assertEqual(edited["revision"], 2)
        refused, changed, _, error = view.upsert_recipe(
            edited, recipe_id="recipe-local", title="夜茶", memo="抢过来。",
            author_side="partner", now_iso="2026-11-11T12:20:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "RECIPE_AUTHOR_MISMATCH")

    def test_twelve_card_limit_is_not_silently_exceeded(self):
        state = view.empty_recipes_state()
        for index in range(view.MAX_RECIPES):
            state, changed, _, error = view.upsert_recipe(
                state, recipe_id=None, title="菜%d" % index, memo="说明",
                author_side="partner", now_iso="2026-11-11T12:00:00+08:00")
            self.assertTrue(changed, error)
        state, changed, _, error = view.upsert_recipe(
            state, recipe_id=None, title="第十三道", memo="说明",
            author_side="partner", now_iso="2026-11-11T12:00:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "RECIPE_LIMIT_REACHED")


class PlanStateTest(unittest.TestCase):
    def test_propose_replay_returns_same_id_and_drop_is_idempotent(self):
        state, changed, plan, error = view.propose_plan(
            view.empty_plans_state(), plan_id=None, title="周末去海边",
            window_note="周六下午", proposer="partner",
            now_iso="2026-11-11T12:00:00+08:00")
        self.assertTrue(changed)
        plan_id = plan["plan_id"]
        replay, changed, plan, error = view.propose_plan(
            state, plan_id=None, title="周末去海边", window_note="周六下午",
            proposer="partner", now_iso="2026-11-11T12:10:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(plan["plan_id"], plan_id)
        self.assertEqual(len(replay["plans"]), 1)
        dropped, changed, plan, error = view.drop_plan(
            replay, plan_id=plan_id, now_iso="2026-11-11T12:20:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(plan["status"], "DROPPED")
        again, changed, plan, error = view.drop_plan(
            dropped, plan_id=plan_id, now_iso="2026-11-11T12:30:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(again["revision"], 2)

    def test_reschedule_requires_a_window_and_dropped_stays_dropped(self):
        state, _, plan, _ = view.propose_plan(
            view.empty_plans_state(), plan_id="plan-local", title="喝夜茶",
            window_note="", proposer="partner",
            now_iso="2026-11-11T12:00:00+08:00")
        _state, changed, _, error = view.reschedule_plan(
            state, plan_id="plan-local", window_note="  ",
            now_iso="2026-11-11T12:05:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "PLAN_WINDOW_INVALID")
        moved, changed, plan, error = view.reschedule_plan(
            state, plan_id="plan-local", window_note="周五晚上",
            now_iso="2026-11-11T12:10:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(plan["status"], "RESCHEDULED")
        self.assertEqual(plan["proposer"], "partner")
        dropped, _, _, _ = view.drop_plan(moved, plan_id="plan-local",
                                         now_iso="2026-11-11T12:20:00+08:00")
        refused, changed, _, error = view.reschedule_plan(
            dropped, plan_id="plan-local", window_note="再约一次",
            now_iso="2026-11-11T12:30:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "PLAN_DROPPED")

    def test_six_item_limit_and_default_view_hides_titles(self):
        state = view.empty_plans_state()
        for index in range(view.MAX_PLANS):
            state, changed, _, error = view.propose_plan(
                state, plan_id=None, title="约定%d" % index, window_note="",
                proposer="partner", now_iso="2026-11-11T12:00:00+08:00")
            self.assertTrue(changed, error)
        state, changed, _, error = view.propose_plan(
            state, plan_id=None, title="第七条", window_note="",
            proposer="partner", now_iso="2026-11-11T12:00:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(error, "PLAN_LIMIT_REACHED")
        arrangements = {"status": "available", "day": "2026-11-11",
                        "plan_count": 2, "obligation_count": 0,
                        "plan_titles": ["私事"], "plans_detail": [{"title": "私事"}]}
        partner_block = view.plans_block(state=state, state_reason=None,
                                        arrangements=arrangements, subject_view=False)
        self.assertNotIn("plan_titles", partner_block["arrangements"])
        self.assertNotIn("plans_detail", partner_block["arrangements"])
        subject_block = view.plans_block(state=state, state_reason=None,
                                        arrangements=arrangements, subject_view=True)
        self.assertEqual(subject_block["arrangements"]["plan_titles"], ["私事"])
        self.assertTrue(subject_block["tentative_are_not_arrangements"])


POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply owner 与 Life/World socket 需要 POSIX（在 WSL/Linux 上运行）")


@POSIX_ONLY
class HomeHearthOwnerHTTPTest(unittest.TestCase):
    """Real Supply/World/reading/economy owners; real loopback HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        import home_hearth_fixture as fixture
        import window_keepsake_fixture as keepsake_fixture

        cls.fixture = fixture
        cls.keepsake_fixture = keepsake_fixture
        cls.root = Path(tempfile.mkdtemp(prefix="home-hearth-"))
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
        # Local explicit state is this feature's own scratch; owner facts are never reset.
        view.write_reunion_state(self.stack.root, view.empty_reunion_state())
        view.write_recipes_state(self.stack.root, view.empty_recipes_state())
        view.write_plans_state(self.stack.root, view.empty_plans_state())

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

    def _capture(self) -> tuple[int, dict]:
        return self._call("POST", "/api/phone/home/reunion-anchor",
                          {"action": "capture"}, csrf=True)

    def _recipe(self, author: str, title: str, memo: str, *,
                recipe_id: str | None = None, capability: str | None = None,
                csrf: bool = True) -> tuple[int, dict]:
        body = {"action": "upsert", "title": title, "memo": memo,
                "author_side": author}
        if recipe_id is not None:
            body["recipe_id"] = recipe_id
        return self._call("POST", "/api/phone/home/recipes", body, csrf=csrf,
                          capability=capability)

    def _plan(self, action: str, *, proposer: str | None = None,
              plan_id: str | None = None, title: str | None = None,
              window_note: str | None = None, capability: str | None = None,
              csrf: bool = True) -> tuple[int, dict]:
        body: dict = {"action": action}
        if proposer is not None:
            body["proposer"] = proposer
        if plan_id is not None:
            body["plan_id"] = plan_id
        if title is not None:
            body["title"] = title
        if window_note is not None:
            body["window_note"] = window_note
        return self._call("POST", "/api/phone/home/tentative-plans", body, csrf=csrf,
                          capability=capability)

    # -- reunion -------------------------------------------------------------------------
    def test_reunion_capture_replays_without_writing_bytes(self):
        status, payload = self._call("GET", "/api/phone/home/reunion-anchor")
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["data"]["has_anchor"])

        status, payload = self._call("POST", "/api/phone/home/reunion-anchor",
                                     {"action": "capture"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")

        status, payload = self._capture()
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["changed"])
        first_bytes = view.reunion_path(self.stack.root).read_bytes()

        status, payload = self._capture()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertFalse(payload["data"]["changed"])
        self.assertEqual(view.reunion_path(self.stack.root).read_bytes(), first_bytes)

        status, payload = self._call("GET", "/api/phone/home/reunion-anchor")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["has_anchor"])
        self.assertTrue(data["at_home"])
        self.assertEqual(data["reading"]["current"]["page"], self.stack.reading_page)
        self.assertTrue(data["reading"]["comparable"])
        self.assertEqual(data["reading"]["page_delta"], 0)
        self.assertGreaterEqual(data["delivered"]["current_count"], 1)
        self.assertEqual(data["delivered"]["new_count"], 0)
        self.assertGreaterEqual(data["items"]["current_count"], 1)
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.keepsake_fixture.LETTER_DRAFT_TEXT, dumped)
        self.assertNotIn(self.keepsake_fixture.PIN_SHARE_TEXT, dumped)

    def test_reunion_real_reading_turn_delivery_and_purchase_show_as_deltas(self):
        status, payload = self._capture()
        self.assertEqual(status, 200, payload)

        # One real owner-committed stop two pages further on.
        material = self.stack.service.reading_material()
        self.assertTrue(material["ok"], material)
        data = material["data"]
        moved = self.stack.service.reading_position(
            int(data["page"]) + 2, str(data["source_revision"]),
            str(data["private_head"] or ""), "home-hearth-position")
        self.assertTrue(moved["ok"], moved)

        # One more real local delivery through the owner's formal prepare/send path.  A new
        # owner-committed version gives this share its own disclosure binding (the previous
        # fixture share's permit names the old version).
        project = self.stack.service.project(self.stack.work_a)
        self.assertTrue(project["ok"], project)
        head = str(project["project"]["head_version_id"])
        committed = self.stack.service.commit_project(
            artifact_id=self.stack.work_a, content="回家后补一句，另起一版。",
            expected_head=head, operation_key="home-hearth-commit")
        self.assertTrue(committed["ok"], committed)
        new_share = self.keepsake_fixture._send_local(
            self.stack, source_id=self.stack.work_a, text="回家后补一句。",
            key="home-hearth-new-share")

        # One more real purchase through the phone store.
        new_item = self.fixture.buy_another_snack(self.stack, "home-hearth-buy-2")

        status, payload = self._call("GET", "/api/phone/home/reunion-anchor")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertTrue(data["reading"]["comparable"], data["reading"])
        self.assertEqual(data["reading"]["page_delta"], 2)
        self.assertEqual(data["delivered"]["new_count"], 1)
        self.assertEqual(data["delivered"]["new_share_ids"], [new_share])
        self.assertEqual(data["items"]["new_count"], 1)
        self.assertEqual(data["items"]["new_item_ids"], [new_item])

    # -- recipes -------------------------------------------------------------------------
    def test_recipes_author_gate_replay_and_cross_author_refusal(self):
        status, payload = self._recipe("partner", "夜茶", "热水泡开就好的那种。",
                                       capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["recipe"]["partner_contributed"])
        partner_card = payload["data"]["recipe_id"]
        first_bytes = view.recipes_path(self.stack.root).read_bytes()

        status, payload = self._recipe("partner", "夜茶", "热水泡开就好的那种。")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(view.recipes_path(self.stack.root).read_bytes(), first_bytes)

        # Even a valid her capability cannot turn the partner's card into hers.
        status, payload = self._recipe("her", "夜茶", "抢过来。",
                                       recipe_id=partner_card, capability=CAP_TOKEN)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "RECIPE_AUTHOR_MISMATCH")

        status, payload = self._recipe("her", "她的茶", "少放一点糖。")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_REQUIRED")
        status, payload = self._recipe("her", "她的茶", "少放一点糖。",
                                       capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["recipe"]["author_side"], "her")
        her_card = payload["data"]["recipe_id"]
        status, payload = self._recipe("partner", "她的茶", "改一点。",
                                       recipe_id=her_card)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "RECIPE_AUTHOR_MISMATCH")

        status, payload = self._call("POST", "/api/phone/home/recipes", {
            "action": "upsert", "title": "夜茶", "memo": "说明",
            "author_side": "partner", "extra": []}, csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:extra")
        status, payload = self._recipe("partner", "夜茶", "")
        self.assertEqual(status, 400, payload)

        status, payload = self._call("GET", "/api/phone/home/recipes")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["count"], 2)
        reference = data["reference"]
        self.assertEqual(reference["status"], "available")
        self.assertTrue(reference["not_evidence_of_cooking"])
        self.assertFalse(reference["consumption_claimed"])
        self.assertFalse(reference["matched_by_title"])
        self.assertTrue(any(row["item_instance_id"] == self.stack.purchased_item_id
                            for row in reference["items"]))
        # No world declaration was altered for this fixture: the real purchase is visible, but
        # a purchase alone is never dressed up as a consumption.
        self.assertTrue(all(row["consumption"]["status"] in ("consumed", "not_consumed")
                            for row in reference["items"]))
        self.assertTrue(all(row["purchase"]["status"] == "available"
                            for row in reference["items"]))

    # -- tentative plans -----------------------------------------------------------------
    def test_tentative_plan_permissions_and_drop_semantics(self):
        status, payload = self._plan("propose", proposer="partner", title="周末去海边",
                                     window_note="周六下午")
        self.assertEqual(status, 200, payload)
        partner_plan = payload["data"]["plan_id"]
        self.assertEqual(payload["data"]["plan"]["status"], "TENTATIVE")
        self.assertFalse(payload["data"]["plan"]["auto_hijack_activity"])
        self.assertTrue(payload["data"]["plan"]["no_obligation"])

        status, payload = self._plan("propose", proposer="partner", title="周末去海边",
                                     window_note="周六下午")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        self.assertEqual(payload["data"]["plan_id"], partner_plan)

        status, payload = self._plan("propose", proposer="her", title="一起煮茶")
        self.assertEqual(status, 403, payload)
        status, payload = self._plan("propose", proposer="her", title="一起煮茶",
                                     capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        her_plan = payload["data"]["plan_id"]

        status, payload = self._plan("reschedule", plan_id=her_plan,
                                     window_note="周五晚上")
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "PLAN_FORBIDDEN")
        status, payload = self._plan("reschedule", plan_id=partner_plan,
                                     window_note="周日早上", capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["plan"]["status"], "RESCHEDULED")
        self.assertEqual(payload["data"]["plan"]["proposer"], "partner")

        status, payload = self._plan("drop", plan_id=partner_plan)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["plan"]["status"], "DROPPED")
        status, payload = self._plan("drop", plan_id=partner_plan)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], phone_service.STATUS_ALREADY)
        status, payload = self._plan("reschedule", plan_id=partner_plan,
                                     window_note="再约一次")
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["reason_code"], "PLAN_DROPPED")

        status, payload = self._call("POST", "/api/phone/home/tentative-plans", {
            "action": "reschedule", "plan_id": her_plan, "window_note": "改期",
            "proposer": "partner"}, csrf=True)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["reason_code"], "UNKNOWN_FIELD:proposer")

    def test_plans_never_touch_core_arrangements_or_activity(self):
        canonical = Path(self.stack.state_root) / "canonical_life_state.json"
        before_canonical = canonical.read_bytes()
        before_arrangements = json.dumps(
            self.stack.service.today_arrangements(), ensure_ascii=False, sort_keys=True)

        status, payload = self._plan("propose", proposer="partner", title="看一场电影",
                                     window_note="下周三")
        self.assertEqual(status, 200, payload)
        plan_id = payload["data"]["plan_id"]
        status, payload = self._plan("reschedule", plan_id=plan_id,
                                     window_note="下周四")
        self.assertEqual(status, 200, payload)
        status, payload = self._plan("drop", plan_id=plan_id)
        self.assertEqual(status, 200, payload)

        self.assertEqual(canonical.read_bytes(), before_canonical)
        self.assertEqual(
            json.dumps(self.stack.service.today_arrangements(), ensure_ascii=False,
                       sort_keys=True), before_arrangements)

        status, payload = self._call("GET", "/api/phone/home/tentative-plans")
        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["arrangements"]["status"], "available")
        self.assertNotIn("plan_titles", data["arrangements"])
        self.assertTrue(data["tentative_are_not_arrangements"])
        self.assertTrue(data["does_not_touch_core_arrangements"])
        self.assertFalse(data["auto_hijack_activity"])
        status, payload = self._call("GET", "/api/phone/home/tentative-plans",
                                     capability=CAP_TOKEN)
        self.assertEqual(status, 200, payload)
        self.assertIn("plan_titles", payload["data"]["arrangements"])

    # -- gates / zero write --------------------------------------------------------------
    def test_all_posts_require_csrf_and_wrong_capability_never_degrades(self):
        for path, body in (
            ("/api/phone/home/reunion-anchor", {"action": "capture"}),
            ("/api/phone/home/recipes",
             {"action": "upsert", "title": "茶", "memo": "说明", "author_side": "partner"}),
            ("/api/phone/home/tentative-plans",
             {"action": "propose", "proposer": "partner", "title": "约定"}),
        ):
            status, payload = self._call("POST", path, body)
            self.assertEqual(status, 403, payload)
            self.assertEqual(payload["reason_code"], "CSRF_TOKEN_MISSING")
        status, payload = self._recipe("partner", "茶", "说明", capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        status, payload = self._plan("propose", proposer="partner", title="约定",
                                     capability="wrong-token")
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["reason_code"], "CAPABILITY_INVALID")
        status, payload = self._call("GET", "/api/phone/home/tentative-plans",
                                     headers={"X-Phone-Actor": "model"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["view"], "partner_default")

    def test_unknown_or_empty_extra_fields_are_400_and_zero_write(self):
        self._recipe("partner", "茶", "说明")
        self._plan("propose", proposer="partner", title="约定")
        paths = (view.reunion_path(self.stack.root), view.recipes_path(self.stack.root),
                 view.plans_path(self.stack.root))
        before = [path.read_bytes() for path in paths]
        cases = (
            ("/api/phone/home/reunion-anchor",
             {"action": "capture", "captured_at": None}, "UNKNOWN_FIELD:captured_at"),
            ("/api/phone/home/reunion-anchor",
             {"action": "capture", "snapshot": {}}, "UNKNOWN_FIELD:snapshot"),
            ("/api/phone/home/recipes",
             {"action": "upsert", "title": "新", "memo": "说明", "author_side": "partner",
              "caption": ""}, "UNKNOWN_FIELD:caption"),
            ("/api/phone/home/tentative-plans",
             {"action": "propose", "proposer": "partner", "title": "新",
              "window_note": "", "extra": None}, "UNKNOWN_FIELD:extra"),
            ("/api/phone/home/tentative-plans",
             {"action": "drop", "plan_id": "plan-x", "proposer": "partner"},
             "UNKNOWN_FIELD:proposer"),
            ("/api/phone/home/tentative-plans",
             {"action": "reschedule", "plan_id": "plan-x", "window_note": "",
              "title": "夹带"}, "UNKNOWN_FIELD:title"),
        )
        for path, body, reason in cases:
            status, payload = self._call("POST", path, body, csrf=True)
            self.assertEqual(status, 400, (path, body, payload))
            self.assertEqual(payload["reason_code"], reason)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_three_gets_are_pure_reads_over_bytes_and_heads(self):
        status, payload = self._capture()
        self.assertEqual(status, 200, payload)
        self._recipe("partner", "夜茶", "说明")
        self._plan("propose", proposer="partner", title="一起煮茶")

        paths = ("/api/phone/home/reunion-anchor", "/api/phone/home/recipes",
                 "/api/phone/home/tentative-plans")
        state_files = (view.reunion_path(self.stack.root),
                       view.recipes_path(self.stack.root),
                       view.plans_path(self.stack.root))
        reading_head = self.stack.port.read_artifact(
            str(self.stack.data["reading_artifact_id"]))
        before = {
            "state": [path.read_bytes() for path in state_files],
            "reading_head": json.dumps(reading_head, ensure_ascii=False, sort_keys=True),
            "shares": json.dumps(self.stack.service.shares(), ensure_ascii=False,
                                 sort_keys=True),
            "canonical": (Path(self.stack.state_root)
                          / "canonical_life_state.json").read_bytes(),
        }
        first = [self._call("GET", path)[1] for path in paths]
        second = [self._call("GET", path)[1] for path in paths]
        self.assertEqual(first, second)
        after = {
            "state": [path.read_bytes() for path in state_files],
            "reading_head": json.dumps(self.stack.port.read_artifact(
                str(self.stack.data["reading_artifact_id"])), ensure_ascii=False,
                sort_keys=True),
            "shares": json.dumps(self.stack.service.shares(), ensure_ascii=False,
                                 sort_keys=True),
            "canonical": (Path(self.stack.state_root)
                          / "canonical_life_state.json").read_bytes(),
        }
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(before["canonical"]).hexdigest(),
                         hashlib.sha256(after["canonical"]).hexdigest())


if __name__ == "__main__":
    unittest.main()
