#!/usr/bin/env python3
"""v0.8 universal matter wired into HTTP, the clock, Hermes and the context bridge.

Coverage:
* the real ``/api/phone/v08/living-traces`` read is 100% zero-write yet still
  returns a full ``matter_space`` snapshot; an empty root is never created;
* partner matter acts (bring / move / propose) pass the CSRF door, while a
  probe at ``subject_private_drawer`` is an honest ``404 OBJECT_NOT_FOUND``;
* the partner door cannot call subject-only matter acts (403) and forged
  identity keys/steps are refused with ``409 CLIENT_IDENTITY_REFUSED``;
* a subject capability composes a cook-and-eat-half act; physical clocks then
  cool a hot dish, chill it in the fridge and objectively spoil a private item
  that the partner projection never sees;
* ``phone_tool`` lets ``compose_open_act`` through and still refuses partner
  impersonation; the guidance stays <= 1200 chars;
* ``lived_context_bridge`` reads a tomato off ``world/v08_matter_space.json``
  even without a trace file, and never leaks the private drawer.

Run (WSL): ``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_matter_http_and_hermes -v``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_universal_matter as matter  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402
from components.life.scripts import v03_clock_evaporation as clock  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _iso_add(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


class MatterViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-matter-view-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- 1 --------------------------------------------------------------
    def test_01_read_is_zero_write_and_still_carries_matter_space(self) -> None:
        absent = self.root / "never-created"
        before = _snapshot(self.root)
        code, body = view.read_v08_living_traces_view(absent, now_iso=T0)
        self.assertEqual(code, 200, body)
        self.assertFalse(absent.exists(), "a read must never create a directory")
        self.assertEqual(_snapshot(self.root), before)

        data = body.get("data") or {}
        space = data.get("matter_space") or {}
        self.assertEqual(space.get("schema"), matter.VIEW_SCHEMA)
        self.assertEqual(space.get("actor_side"), "partner")
        self.assertTrue(space.get("zones"), "the partner still sees shared zones")
        self.assertNotIn(
            "subject_private_drawer",
            {zone.get("zone_id") for zone in space.get("zones") or []},
            "the partner projection must hide the private drawer zone",
        )

        code_s, body_s = view.read_v08_living_traces_view(absent, has_subject_capability=True, now_iso=T0)
        self.assertEqual(code_s, 200)
        space_s = (body_s.get("data") or {}).get("matter_space") or {}
        self.assertEqual(space_s.get("actor_side"), "subject")
        self.assertIn(
            "subject_private_drawer",
            {zone.get("zone_id") for zone in space_s.get("zones") or []},
        )

    # -- 2 --------------------------------------------------------------
    def test_02_summary_hints_respect_private_visibility(self) -> None:
        matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把做好的番茄放上餐桌，把私密小盒收进抽屉",
                "steps": [
                    {"op": "INTRODUCE_ITEM", "label_cn": "桌上番茄", "item_kind": "prepared_food",
                     "target_parent_id": "dining_table", "thermal": "room", "portions": 1},
                    {"op": "INTRODUCE_ITEM", "label_cn": "私藏小点心", "item_kind": "prepared_food",
                     "target_parent_id": "subject_private_drawer", "portions": 1},
                ],
            },
            now_iso=T0,
        )
        before = _snapshot(self.root)
        partner_summary = view.summarize_living_traces_for_context(self.root, include_private=False, now_iso=T0)
        subject_summary = view.summarize_living_traces_for_context(self.root, include_private=True, now_iso=T0)
        self.assertEqual(_snapshot(self.root), before, "summaries must stay zero-write")

        partner_text = json.dumps(partner_summary.get("matter_hints"), ensure_ascii=False)
        subject_text = json.dumps(subject_summary.get("matter_hints"), ensure_ascii=False)
        self.assertIn("番茄", partner_text)
        self.assertNotIn("私密", partner_text)
        self.assertNotIn("抽屉", partner_text)
        self.assertNotIn("hidden_count", partner_text)
        self.assertIn("私藏小点心", subject_text)
        self.assertIn('"private": true', subject_text)
        self.assertLessEqual(len(partner_summary.get("matter_hints") or []), 3)

    # -- 3 --------------------------------------------------------------
    def test_03_clock_hook_cools_and_chills_and_reports_counts(self) -> None:
        intro = matter.compose_open_act(
            self.root,
            {"intent_cn": "取一颗生番茄", "steps": [
                {"op": "INTRODUCE_ITEM", "label_cn": "生番茄", "item_kind": "ingredient",
                 "target_parent_id": "kitchen_counter", "cook_state": "raw", "portions": 1},
            ]},
            now_iso=T0,
        )
        tomato_id = intro["step_results"][0]["object_id"]
        cooked = matter.compose_open_act(
            self.root,
            {"intent_cn": "上灶做一份热菜", "steps": [
                {"op": "TRANSFORM_CREATE", "source_object_ids": [tomato_id],
                 "output_label_cn": "现炒番茄", "medium_object_id": "kitchen_stove"},
            ]},
            now_iso=T0,
        )
        dish_id = cooked["step_results"][0]["output_object_id"]
        self.assertEqual(cooked["step_results"][0]["thermal"], "hot")

        code, body = clock.mutate_v03_clock_view(
            self.root, {"action": "advance_projection", "sync_v02": False},
            actor_side="subject", now_iso=_iso_add(40),
        )
        self.assertEqual(code, 200, body)
        receipt = body["data"]["clocks"]["world_clock"]["gap_receipts"][-1]
        self.assertIn("matter_physics_settled", receipt)
        self.assertGreaterEqual(int(receipt["matter_physics_settled"].get("cooled") or 0), 1)

        space = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=_iso_add(40))
        dish = next(obj for obj in space["objects"] if obj["object_id"] == dish_id)
        self.assertEqual(dish["thermal"], "room", "a hot dish cools in a room zone")

        matter.compose_open_act(
            self.root,
            {"intent_cn": "把菜放进冰箱", "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": dish_id, "target_parent_id": "home_fridge"},
            ]},
            now_iso=_iso_add(40),
        )
        code2, body2 = clock.mutate_v03_clock_view(
            self.root, {"action": "advance_projection", "sync_v02": False},
            actor_side="subject", now_iso=_iso_add(70),
        )
        self.assertEqual(code2, 200, body2)
        receipt2 = body2["data"]["clocks"]["world_clock"]["gap_receipts"][-1]
        self.assertGreaterEqual(int(receipt2["matter_physics_settled"].get("chilled") or 0), 1)

        space2 = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=_iso_add(70))
        dish2 = next(obj for obj in space2["objects"] if obj["object_id"] == dish_id)
        self.assertEqual(dish2["thermal"], "cold", "the fridge chills a room-temperature dish")


class MatterHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        import test_v02_phone_http_surfaces as v02http
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = "chiyo"
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v08-matter-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=v02http.CAP_TOKEN,
            subject="chiyo",
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-16T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base
        cls.CAP = v02http.CAP_TOKEN

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        Path(self.stack.root).joinpath("world", "v08_matter_space.json").unlink(missing_ok=True)
        self.stack.service._home_clock = lambda: {
            "source": "test", "reason_code": None,
            "logical_at": "2026-11-16T14:30:00+08:00",
            "day": "2026-11-16", "run_id": None, "domain_id": None,
        }

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict[str, Any]]:
        import urllib.error
        import urllib.request

        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _bring(self, **overrides: Any) -> tuple[int, dict[str, Any]]:
        body = {
            "action": "partner_bring_item",
            "label_cn": "一小篮番茄",
            "item_kind": "ingredient",
            "target_parent_id": "kitchen_counter",
            "cook_state": "raw",
            "portions": 1,
        }
        body.update(overrides)
        return self._http("POST", "/api/phone/v08/living-traces", body, csrf=True)

    # -- 4 --------------------------------------------------------------
    def test_04_get_zero_write_and_full_matter_space(self) -> None:
        path = "/api/phone/v08/living-traces"
        self.assertEqual(self._bring()[0], 200)
        before = _snapshot(Path(self.stack.root))

        code, res = self._http("GET", path)
        self.assertEqual(code, 200, res)
        space = (res.get("data") or {}).get("matter_space") or {}
        self.assertEqual(space.get("schema"), matter.VIEW_SCHEMA)
        self.assertEqual(space.get("actor_side"), "partner")
        self.assertIn("zones", space)
        self.assertIn("objects", space)
        self.assertEqual(space.get("llm_calls"), 0)

        code_s, res_s = self._http("GET", path, capability=self.CAP)
        self.assertEqual(code_s, 200, res_s)
        self.assertEqual(((res_s.get("data") or {}).get("matter_space") or {}).get("actor_side"), "subject")
        self.assertEqual(_snapshot(Path(self.stack.root)), before, "GET must not touch bytes or mtimes")

    # -- 5 --------------------------------------------------------------
    def test_05_partner_acts_pass_csrf_and_private_is_invisible(self) -> None:
        code, res = self._bring(label_cn="桌上的半个番茄", item_kind="prepared_food",
                                target_parent_id="dining_table", thermal="room", portions=2)
        self.assertEqual(code, 200, res)
        tomato_id = (res.get("data") or {})["object_id"]

        code_m, res_m = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "partner_move_shared_object", "object_id": tomato_id,
             "target_parent_id": "dining_table"},
            csrf=True,
        )
        self.assertEqual(code_m, 200, res_m)
        self.assertEqual(res_m["data"]["target_parent_id"], "dining_table")

        code_p, res_p = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "partner_propose_use", "object_id": tomato_id,
             "proposed_use_cn": "垫在画纸旁边当静物"},
            csrf=True,
        )
        self.assertEqual(code_p, 200, res_p)
        self.assertEqual(res_p["data"]["proposal_status"], "proposed")

        # A partner cannot see or mint anything into the private drawer.
        code_bad, res_bad = self._bring(target_parent_id="subject_private_drawer")
        self.assertEqual(code_bad, 404, res_bad)
        self.assertEqual(res_bad.get("reason_code"), "OBJECT_NOT_FOUND")

        code_move_bad, res_move_bad = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "partner_move_shared_object", "object_id": tomato_id,
             "target_parent_id": "subject_private_drawer"},
            csrf=True,
        )
        self.assertEqual(code_move_bad, 404, res_move_bad)
        self.assertEqual(res_move_bad.get("reason_code"), "OBJECT_NOT_FOUND")

    # -- 6 --------------------------------------------------------------
    def test_06_partner_door_cannot_call_subject_matter_acts(self) -> None:
        step = {"op": "OBSERVE_OBJECT", "object_id": "home_desk"}
        code, res = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "compose_open_act", "intent_cn": "看一眼桌面", "steps": [step]},
            csrf=True,
        )
        self.assertEqual(code, 403, res)
        self.assertEqual(res.get("reason_code"), "FORBIDDEN_PARTNER_ACTION")

        code_r, res_r = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "respond_proposed_use", "object_id": "home_desk", "decision": "adopt"},
            csrf=True,
        )
        self.assertEqual(code_r, 403, res_r)
        self.assertEqual(res_r.get("reason_code"), "FORBIDDEN_PARTNER_ACTION")

    # -- 7 --------------------------------------------------------------
    def test_07_identity_keys_are_refused(self) -> None:
        code, res = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "partner_bring_item", "label_cn": "番茄", "item_kind": "ingredient",
             "target_parent_id": "dining_table", "uid": "root"},
            csrf=True,
        )
        self.assertEqual(code, 409, res)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(res.get("reason_code") or ""))

        code_step, res_step = self._http(
            "POST", "/api/phone/v08/living-traces",
            {"action": "compose_open_act", "intent_cn": "冒充主体",
             "steps": [{"op": "OBSERVE_OBJECT", "object_id": "home_desk", "actor": "subject"}]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(code_step, 409, res_step)
        self.assertEqual(res_step.get("reason_code"), "CLIENT_IDENTITY_REFUSED")

    # -- 8 --------------------------------------------------------------
    def test_08_subject_cook_eat_half_then_objective_physics(self) -> None:
        path = "/api/phone/v08/living-traces"
        _, brought = self._bring()
        tomato_id = brought["data"]["object_id"]

        code_cook, res_cook = self._http(
            "POST", path,
            {"action": "compose_open_act", "intent_cn": "用番茄做一份热菜", "steps": [
                {"op": "TRANSFORM_CREATE", "source_object_ids": [tomato_id],
                 "output_label_cn": "番茄热菜", "medium_object_id": "kitchen_stove",
                 "shelf_life_hours": 1},
            ]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(code_cook, 200, res_cook)
        cook_step = res_cook["data"]["step_results"][0]
        dish_id = cook_step["output_object_id"]
        self.assertEqual(cook_step["thermal"], "hot")

        code_eat, res_eat = self._http(
            "POST", path,
            {"action": "compose_open_act", "intent_cn": "先吃一半", "steps": [
                {"op": "CONSUME_PORTION", "object_id": dish_id, "portions": 1},
            ]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(code_eat, 200, res_eat)
        self.assertEqual(res_eat["data"]["step_results"][0]["portions_left"], 1)

        # A small private perishable, objectively aged by the clock.
        code_priv, res_priv = self._http(
            "POST", path,
            {"action": "compose_open_act", "intent_cn": "把小点心收进抽屉", "steps": [
                {"op": "INTRODUCE_ITEM", "label_cn": "私藏小点心", "item_kind": "prepared_food",
                 "target_parent_id": "subject_private_drawer", "portions": 1, "shelf_life_hours": 1},
            ]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(code_priv, 200, res_priv)
        private_id = res_priv["data"]["step_results"][0]["object_id"]

        later = "2026-11-16T17:00:00+08:00"  # 2.5h room exposure
        counts = matter.settle_matter_physics(Path(self.stack.root), later)
        self.assertGreaterEqual(int(counts.get("spoiled") or 0), 1, counts)

        _, subj = self._http("GET", path, capability=self.CAP)
        subj_objects = {o["object_id"]: o for o in (subj.get("data") or {}).get("matter_space", {}).get("objects", [])}
        self.assertEqual(subj_objects[dish_id]["thermal"], "room", "a hot dish cools objectively")
        self.assertEqual(subj_objects[private_id]["freshness"], "spoiled")
        self.assertEqual(subj_objects[private_id]["visibility"], "private")

        _, partner = self._http("GET", path)
        partner_objects = {o["object_id"] for o in (partner.get("data") or {}).get("matter_space", {}).get("objects", [])}
        self.assertNotIn(private_id, partner_objects, "private matter must never surface on the partner door")
        self.assertNotIn("私藏小点心", json.dumps(partner, ensure_ascii=False))

        # The fridge slows spoilage: a fresh 6h item survives a 13h cold soak.
        code_f, res_f = self._http(
            "POST", path,
            {"action": "compose_open_act", "intent_cn": "把另一份收进冰箱", "steps": [
                {"op": "INTRODUCE_ITEM", "label_cn": "冰箱里的饭", "item_kind": "prepared_food",
                 "target_parent_id": "home_fridge", "portions": 1, "shelf_life_hours": 6},
            ]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(code_f, 200, res_f)
        fridge_id = res_f["data"]["step_results"][0]["object_id"]
        matter.settle_matter_physics(Path(self.stack.root), "2026-11-17T06:00:00+08:00")
        space = matter.read_matter_space_view(Path(self.stack.root), actor_side="subject",
                                              now_iso="2026-11-17T06:00:00+08:00")
        fridge_obj = next(o for o in space["objects"] if o["object_id"] == fridge_id)
        self.assertIn(fridge_obj["freshness"], ("fresh", "aging"),
                      "the fridge's 1/6 aging rate must delay spoilage")


class HermesAndBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-matter-hermes-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- 9 --------------------------------------------------------------
    def test_09_phone_tool_allows_compose_and_refuses_impersonation(self) -> None:
        import chiyo_bundle.phone_tool as phone_tool

        surface, path, body, cap_required = phone_tool._prepare_life_update(
            {
                "target_surface": "v08_living_traces",
                "payload": {
                    "action": "compose_open_act",
                    "intent_cn": "把桌上的番茄做成一份热菜",
                    "steps": [{"op": "OBSERVE_OBJECT", "object_id": "home_desk"}],
                },
            },
            "op-compose-1",
        )
        self.assertEqual(surface, "v08_living_traces")
        self.assertEqual(path, "/api/phone/v08/living-traces")
        self.assertEqual(body["operation_key"], "op-compose-1")
        self.assertTrue(cap_required)
        self.assertIn("compose_open_act",
                      phone_tool._SURFACE_SUPPORTED_HINTS["v08_living_traces"]["subject_actions"])

        partner_actions = ("partner_bring_item", "partner_move_shared_object", "partner_propose_use")
        for surface_name in ("v08_living_traces", "living_traces"):
            self.assertTrue(
                set(partner_actions) <= phone_tool._PARTNER_ONLY_SUBACTIONS[surface_name]
            )
        for action, payload in (
            ("partner_bring_item", {"label_cn": "番茄", "item_kind": "ingredient",
                                    "target_parent_id": "dining_table"}),
            ("partner_move_shared_object", {"object_id": "home_desk",
                                            "target_parent_id": "dining_table"}),
            ("partner_propose_use", {"object_id": "home_desk", "proposed_use_cn": "当镇纸"}),
        ):
            with self.assertRaises(phone_tool.PhoneToolRefused) as caught:
                phone_tool._prepare_life_update(
                    {"target_surface": "v08_living_traces",
                     "payload": dict(payload, action=action)},
                    "op-partner-" + action,
                )
            self.assertEqual(caught.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED")

    # -- 10 -------------------------------------------------------------
    def test_10_guidance_cap_and_bridge_reads_matter_only_root(self) -> None:
        import importlib.util
        from chiyo_bundle import lived_context_bridge

        spec = importlib.util.spec_from_file_location("chiyo_plugin_v08matter", ROOT / "plugins" / "chiyo" / "__init__.py")
        assert spec is not None and spec.loader is not None
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        guidance = plugin._PHONE_AND_WORLD_GUIDANCE
        self.assertLessEqual(len(guidance), 1200)
        for token in (
            "phone_life_update", "world_action", "TOILET", "work_context", "v03_clock",
            "v03_ecology", "v03_embodied_social", "v08_living_traces", "v03_encounters",
            "compose_open_act",
        ):
            self.assertIn(token, guidance)

        # Only world/v08_matter_space.json exists: no trace file at all.
        day_root = self.tmp / "day"
        matter.compose_open_act(
            day_root,
            {"intent_cn": "番茄上桌，私藏收抽屉", "steps": [
                {"op": "INTRODUCE_ITEM", "label_cn": "桌上番茄", "item_kind": "prepared_food",
                 "target_parent_id": "dining_table", "thermal": "room", "portions": 1},
                {"op": "INTRODUCE_ITEM", "label_cn": "私藏小点心", "item_kind": "prepared_food",
                 "target_parent_id": "subject_private_drawer", "portions": 1},
            ]},
            now_iso=T0,
        )
        self.assertTrue((day_root / "world" / "v08_matter_space.json").is_file())
        self.assertFalse((day_root / "phone" / "v08_living_traces.json").exists())

        home = self.tmp / "home"
        home.mkdir()
        before = _snapshot(self.tmp)
        restricted_cfg = {
            "day_root": str(day_root),
            "memory": True,
            "memory_tool_policy": "restricted",
            "phone": {"private_artifacts": False},
        }
        rendered = lived_context_bridge.render_lived_companion_context(home, restricted_cfg)
        self.assertEqual(_snapshot(self.tmp), before, "the bridge must stay zero-write")
        self.assertLessEqual(len(rendered), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("番茄", rendered)
        self.assertNotIn("私藏小点心", rendered)
        self.assertNotIn("私人抽屉", rendered)
        self.assertNotIn("抽屉", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
