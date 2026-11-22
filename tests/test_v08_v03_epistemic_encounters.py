#!/usr/bin/env python3
"""v3.0 第 23/26/27/28 章：有限视野三次遇见、递一页材料、店主手头事与听说边界。

Coverage:
* three-stage encounter progression (FIRST_GLANCE -> QUESTION_DRIVEN_REVISIT ->
  NUANCED_RECOGNITION) keeping a local disagreement and never fabricating a trial;
* unseen pages stay withheld and an out-of-range page is refused;
* partner projection strips ``shared is False`` encounters without leaking a count;
* shared material: drop (unopened, no debt) -> partial open -> used in work ->
  withdraw erases the excerpt immediately and blocks any later open / context quote;
* shopkeeper errands vs hearsay: hearsay needs a source and is not "seen in person",
  verified-in-person requires a place note, help status distinguishes a favour from a commission;
* the real HTTP surface: zero-write GET, partner filtering, capability boundary,
  CSRF, identity refusal and withdrawn-material suppression;
* ``chiyo_phone`` read/write wiring, partner-impersonation refusal, bounded guidance;
* no hard-coded pronoun/name in the two new modules and ``llm_calls == 0``.

Run (WSL):
``PYTHONDONTWRITEBYTECODE=1 python -B -m unittest tests.test_v08_v03_epistemic_encounters -v``
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v03_epistemic_encounters as engine  # noqa: E402
from components.phone import v03_encounters_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


class V03EpistemicEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v03-encounters-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(self, payload: dict[str, Any], *, actor_side: str = "subject") -> dict[str, Any]:
        projected, _status = engine.apply_encounters_mutation(
            self.root, payload, actor_side=actor_side, now_iso=T0
        )
        return projected

    # -- zero write ----------------------------------------------------------
    def test_01_reads_are_zero_write(self) -> None:
        absent = Path(tempfile.mkdtemp(prefix="v03-empty-")) / "never"
        projected = engine.project_v03_encounters(absent, has_subject_capability=True)
        self.assertFalse(absent.exists(), "a pure read must never create a directory")
        self.assertEqual(projected["llm_calls"], 0)
        self.assertEqual(projected["encounters"], [])

        before = _snapshot(self.root)
        view.read_v03_encounters_view(self.root, has_subject_capability=True, now_iso=T0)
        view.summarize_encounters_for_context(self.root, include_private=True)
        after = _snapshot(self.root)
        self.assertEqual(before, after, "reads must be byte- and mtime-identical")

    # -- three glances -------------------------------------------------------
    def test_02_three_glances_keep_unseen_pages_and_local_disagreement(self) -> None:
        first = self._mutate({
            "action": "record_first_glance",
            "target_ref": "reading:seawall_plants",
            "target_title_cn": "海边植物图鉴",
            "seen_pages": [3, 1, 2],
            "total_pages": 10,
            "current_impression_cn": "只翻了几页，图注比正文耐看",
            "operation_key": "enc-first",
        })
        enc = first["encounters"][0]
        enc_id = enc["encounter_id"]
        self.assertEqual(enc["stage"], "FIRST_GLANCE")
        self.assertEqual(enc["seen_pages"], [1, 2, 3])
        self.assertTrue(enc["unseen_pages_withheld"])
        self.assertIsNone(enc["tried_in_work_ref"])
        self.assertEqual(enc["prior_impressions"], [])

        second = self._mutate({
            "action": "revisit_with_question",
            "encounter_id": enc_id,
            "newly_seen_pages": [4, 5],
            "active_question_cn": "为什么同一种海草在两页的叶形画得不一样？",
            "noticed_detail_cn": "第 5 页角落有一行小字说明是幼株",
            "current_impression_cn": "带着问题再看，幼株那页的标注原来是关键",
            "operation_key": "enc-revisit",
        })
        enc = second["encounters"][0]
        self.assertEqual(enc["stage"], "QUESTION_DRIVEN_REVISIT")
        self.assertEqual(enc["seen_pages"], [1, 2, 3, 4, 5])
        self.assertEqual(enc["active_question_cn"], "为什么同一种海草在两页的叶形画得不一样？")
        self.assertEqual(enc["noticed_detail_cn"], "第 5 页角落有一行小字说明是幼株")
        self.assertIn("只翻了几页，图注比正文耐看", enc["prior_impressions"])

        third = self._mutate({
            "action": "record_nuanced_recognition",
            "encounter_id": enc_id,
            "current_impression_cn": "画得细，但幼株那页的配色偏艳，我不太同意",
            "partial_disagreement_kept": "整体喜欢，仍保留对幼株页配色的不同意见",
            "operation_key": "enc-nuanced",
        })
        enc = third["encounters"][0]
        self.assertEqual(enc["stage"], "NUANCED_RECOGNITION")
        self.assertEqual(enc["partial_disagreement_kept"], "整体喜欢，仍保留对幼株页配色的不同意见")
        self.assertIsNone(enc["tried_in_work_ref"], "an absent trial reference must never be invented")

        fourth = self._mutate({
            "action": "record_nuanced_recognition",
            "encounter_id": enc_id,
            "current_impression_cn": "照着画了一页，果然脉络比想象中密",
            "partial_disagreement_kept": "仍觉得图注字号太小",
            "tried_in_work_ref": "work:seawall_sketch#v2",
            "operation_key": "enc-nuanced-tried",
        })
        self.assertEqual(fourth["encounters"][0]["tried_in_work_ref"], "work:seawall_sketch#v2")

    def test_03_out_of_range_pages_are_refused(self) -> None:
        with self.assertRaises(engine.V03ValidationError) as ctx:
            self._mutate({
                "action": "record_first_glance",
                "target_ref": "reading:small",
                "target_title_cn": "薄册子",
                "seen_pages": [1, 9],
                "total_pages": 4,
                "current_impression_cn": "只看了开头",
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")
        self.assertFalse((self.root / "phone" / "v03_encounters.json").exists())

    def test_04_partner_projection_hides_unshared_without_count(self) -> None:
        self._mutate({
            "action": "record_first_glance",
            "target_ref": "private:diary_fragment",
            "target_title_cn": "没打算给人看的片段",
            "seen_pages": [1],
            "total_pages": 2,
            "current_impression_cn": "先记着，不公开",
            "shared": False,
        })
        self._mutate({
            "action": "record_first_glance",
            "target_ref": "shared:map",
            "target_title_cn": "街区手绘地图",
            "seen_pages": [1, 2],
            "total_pages": 4,
            "current_impression_cn": "只看了上半张",
            "shared": True,
        })
        partner = engine.project_v03_encounters(self.root, has_subject_capability=False, now_iso=T0)
        titles = [row["target_title_cn"] for row in partner["encounters"]]
        self.assertEqual(titles, ["街区手绘地图"])
        dumped = json.dumps(partner, ensure_ascii=False)
        self.assertNotIn("没打算给人看的片段", dumped)
        self.assertNotIn("unshared_count", dumped)

        subject = engine.project_v03_encounters(self.root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(len(subject["encounters"]), 2)

    # -- shared material -----------------------------------------------------
    def test_05_drop_open_use_withdraw_and_no_resurrection(self) -> None:
        partner = self._mutate({
            "action": "drop_shared_material",
            "title_cn": "一段关于潮汐的旧笔记",
            "excerpt_cn": "退潮后半小时，礁石缝里会留下小小的水洼。",
            "topic_tag": "海",
            "page_count": 3,
            "operation_key": "mat-drop",
        }, actor_side="partner")
        material = partner["shared_materials"][0]
        material_id = material["material_id"]
        self.assertEqual(material["status"], "RECEIVED_UNOPENED")
        self.assertEqual(material["opened_pages"], [])

        partial = self._mutate({
            "action": "open_or_use_shared_material",
            "material_id": material_id,
            "mode": "open_partial",
            "opened_pages": [1],
            "reaction_cn": "第一页写得很轻，像随手记的",
            "operation_key": "mat-open",
        })
        material = partial["shared_materials"][0]
        self.assertEqual(material["status"], "OPENED_PARTIAL")
        self.assertEqual(material["opened_pages"], [1])

        used = self._mutate({
            "action": "open_or_use_shared_material",
            "material_id": material_id,
            "mode": "use_in_work",
            "used_in_artifact_id": "work:tide_notes#v1",
            "operation_key": "mat-use",
        })
        material = used["shared_materials"][0]
        self.assertEqual(material["status"], "USED_IN_WORK")
        self.assertEqual(material["used_in_artifact_id"], "work:tide_notes#v1")

        withdrawn = self._mutate({
            "action": "withdraw_shared_material",
            "material_id": material_id,
            "operation_key": "mat-withdraw",
        }, actor_side="partner")
        material = withdrawn["shared_materials"][0]
        self.assertEqual(material["status"], "WITHDRAWN")
        self.assertEqual(material["excerpt_cn"], "")
        self.assertEqual(material["reaction_cn"], "")
        self.assertIsNone(material["used_in_artifact_id"])

        with self.assertRaises(engine.V03ConflictError) as ctx:
            self._mutate({
                "action": "open_or_use_shared_material",
                "material_id": material_id,
                "mode": "open_partial",
                "opened_pages": [2],
            })
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason_code, "MATERIAL_WITHDRAWN")

        summary = view.summarize_encounters_for_context(self.root, include_private=True)
        self.assertEqual(summary["shared_materials"], [])
        self.assertNotIn("礁石缝", json.dumps(summary, ensure_ascii=False))

    def test_06_partner_door_cannot_use_subject_actions(self) -> None:
        with self.assertRaises(engine.V03PermissionError) as ctx:
            engine.apply_encounters_mutation(
                self.root,
                {
                    "action": "record_first_glance",
                    "target_ref": "x",
                    "target_title_cn": "x",
                    "seen_pages": [1],
                    "total_pages": 1,
                    "current_impression_cn": "x",
                },
                actor_side="partner",
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")

    # -- shopkeeper errands vs hearsay ---------------------------------------
    def test_07_shopkeeper_errand_and_hearsay_honesty(self) -> None:
        with self.assertRaises(engine.V03ValidationError):
            self._mutate({
                "action": "record_npc_errand_or_hearsay",
                "npc_id": "shopkeeper",
                "npc_label_cn": "杂货铺老板",
                "place_id": "corner_shop",
                "kind": "hearsay",
                "summary_cn": "听说码头要修新的台阶",
            })

        hearsay = self._mutate({
            "action": "record_npc_errand_or_hearsay",
            "npc_id": "shopkeeper",
            "npc_label_cn": "杂货铺老板",
            "place_id": "corner_shop",
            "kind": "hearsay",
            "summary_cn": "听说码头要修新的台阶",
            "hearsay_source_cn": "买酱油的常客",
            "operation_key": "npc-hearsay",
        })
        entry = hearsay["npc_errands_and_hearsay"][0]
        self.assertEqual(entry["kind"], "hearsay")
        self.assertEqual(entry["hearsay_source_cn"], "买酱油的常客")
        self.assertFalse(entry["verified_in_person"])

        with self.assertRaises(engine.V03ValidationError):
            self._mutate({
                "action": "record_npc_errand_or_hearsay",
                "npc_id": "shopkeeper",
                "npc_label_cn": "杂货铺老板",
                "place_id": "corner_shop",
                "kind": "ongoing_errand",
                "summary_cn": "在搬新到的米袋",
                "verified_in_person": True,
            })

        errand = self._mutate({
            "action": "record_npc_errand_or_hearsay",
            "npc_id": "shopkeeper",
            "npc_label_cn": "杂货铺老板",
            "place_id": "corner_shop",
            "kind": "ongoing_errand",
            "summary_cn": "正把新到的米袋码上架",
            "verified_in_person": True,
            "verified_place_note": "我站在门口亲眼看见",
            "subject_help_status": "suggested",
            "operation_key": "npc-errand",
        })
        entry = errand["npc_errands_and_hearsay"][-1]
        self.assertEqual(entry["kind"], "ongoing_errand")
        self.assertTrue(entry["verified_in_person"])
        self.assertEqual(entry["subject_help_status"], "suggested")

    # -- idempotency ---------------------------------------------------------
    def test_08_operation_key_replays_and_conflicts(self) -> None:
        payload = {
            "action": "record_first_glance",
            "target_ref": "reading:replay",
            "target_title_cn": "复习册",
            "seen_pages": [1],
            "total_pages": 2,
            "current_impression_cn": "先看一页",
            "operation_key": "same-key",
        }
        self._mutate(payload)
        _projected, status = engine.apply_encounters_mutation(
            self.root, payload, actor_side="subject", now_iso=T0
        )
        self.assertEqual(status, "DUPLICATE")
        conflicted = dict(payload)
        conflicted["current_impression_cn"] = "改成另一句话"
        with self.assertRaises(engine.V03ConflictError) as ctx:
            engine.apply_encounters_mutation(
                self.root, conflicted, actor_side="subject", now_iso=T0
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

    def test_09_state_schema_and_path(self) -> None:
        self._mutate({
            "action": "record_first_glance",
            "target_ref": "reading:path",
            "target_title_cn": "路径检查",
            "seen_pages": [1],
            "total_pages": 1,
            "current_impression_cn": "看看落盘",
        })
        path = self.root / "phone" / "v03_encounters.json"
        self.assertTrue(path.is_file())
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema_version"], "life.v03.encounters.v1")


class V03WiringTest(unittest.TestCase):
    def test_01_phone_tool_read_write_and_impersonation_guard(self) -> None:
        from chiyo_bundle import phone_tool

        self.assertIn("v03_encounters", phone_tool.READ_ACTIONS)
        self.assertEqual(phone_tool.READ_ACTIONS["v03_encounters"], "/api/phone/v03/encounters")
        self.assertIn("v03_encounters", phone_tool.ACTIONS)
        self.assertIn("v03_encounters", phone_tool._SUBJECT_VIEW_READ_ACTIONS)
        self.assertIn("v03_encounters", phone_tool.LIFE_UPDATE_SURFACES)
        self.assertIn("v03_encounters", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v03_encounters", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)

        surface, path, body, cap_required = phone_tool._prepare_life_update(
            {
                "target_surface": "v03_encounters",
                "payload": {
                    "action": "record_first_glance",
                    "target_ref": "reading:wiring",
                    "target_title_cn": "接线检查",
                    "seen_pages": [1],
                    "total_pages": 3,
                    "current_impression_cn": "只看了一页",
                },
            },
            "op-v03-enc-1",
        )
        self.assertEqual(surface, "v03_encounters")
        self.assertEqual(path, "/api/phone/v03/encounters")
        self.assertEqual(body["operation_key"], "op-v03-enc-1")
        self.assertTrue(cap_required)

        for partner_action in ("drop_shared_material", "withdraw_shared_material"):
            with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
                phone_tool._prepare_life_update(
                    {
                        "target_surface": "v03_encounters",
                        "payload": {"action": partner_action, "title_cn": "x", "excerpt_cn": "y"},
                    },
                    "op-v03-partner",
                )
            self.assertEqual(ctx.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED")

    def test_02_guidance_is_bounded_and_names_the_surface(self) -> None:
        import importlib.util

        plugin_path = ROOT / "plugins" / "chiyo" / "__init__.py"
        spec = importlib.util.spec_from_file_location("chiyo_plugin_v03_enc", plugin_path)
        assert spec is not None and spec.loader is not None
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        guidance = plugin._PHONE_AND_WORLD_GUIDANCE
        self.assertLessEqual(len(guidance), 1200, f"guidance length {len(guidance)}")
        self.assertIn("v03_encounters", guidance)
        # Existing action names must not have been dropped while trimming.
        for name in (
            "phone_life_update", "world_action", "TOILET", "work_context",
            "v03_clock", "v03_ecology", "v03_embodied_social", "v08_living_traces",
        ):
            self.assertIn(name, guidance)

    def test_03_bridge_mask_and_bounded_summary(self) -> None:
        from chiyo_bundle import lived_context_bridge as bridge

        self.assertEqual(bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        doc = {
            "encounters": [
                {
                    "target_title_cn": "海边植物图鉴",
                    "seen_pages": [1, 2],
                    "total_pages": 10,
                    "stage": "FIRST_GLANCE",
                    "shared": True,
                },
                {
                    "target_title_cn": "不公开的片段",
                    "seen_pages": [1],
                    "total_pages": 2,
                    "stage": "FIRST_GLANCE",
                    "shared": False,
                },
            ],
            "shared_materials": [
                {"title_cn": "旧笔记", "status": "RECEIVED_UNOPENED"},
                {"title_cn": "已撤回的纸", "status": "WITHDRAWN"},
            ],
            "npc_entries": [
                {
                    "npc_label_cn": "杂货铺老板",
                    "kind": "hearsay",
                    "summary_cn": "码头要修新台阶",
                    "hearsay_source_cn": "买酱油的常客",
                    "verified_in_person": False,
                }
            ],
        }
        hidden = bridge._format_v03_epistemic_encounters_micro(doc, include_private=False)
        self.assertLessEqual(len(hidden), 140)
        self.assertIn("有限视野", hidden)
        self.assertIn("没催着读", hidden)
        self.assertIn("听说自买酱油的常客", hidden)
        self.assertNotIn("不公开的片段", hidden)
        self.assertNotIn("已撤回的纸", hidden)

        shown = bridge._format_v03_epistemic_encounters_micro(doc, include_private=True)
        self.assertIn("不公开的片段", shown)
        self.assertNotIn("已撤回的纸", shown)
        self.assertLessEqual(len(shown), 140)

    def test_04_bridge_reads_disk_state_and_hides_withdrawn_text(self) -> None:
        from chiyo_bundle import lived_context_bridge as bridge

        tmp = tempfile.TemporaryDirectory(prefix="v03-bridge-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        engine.apply_encounters_mutation(
            root,
            {
                "action": "record_first_glance",
                "target_ref": "reading:bridge",
                "target_title_cn": "潮汐手记",
                "seen_pages": [1],
                "total_pages": 6,
                "current_impression_cn": "只看了一页",
            },
            actor_side="subject",
            now_iso=T0,
        )
        projected, _ = engine.apply_encounters_mutation(
            root,
            {
                "action": "drop_shared_material",
                "title_cn": "一段旧笔记",
                "excerpt_cn": "SECRET_EXCERPT_SHOULD_NOT_LEAK",
                "page_count": 2,
            },
            actor_side="partner",
            now_iso=T0,
        )
        material_id = projected["shared_materials"][0]["material_id"]
        engine.apply_encounters_mutation(
            root,
            {
                "action": "withdraw_shared_material",
                "material_id": material_id,
            },
            actor_side="partner",
            now_iso=T0,
        )

        cfg = {"phone": {"enabled": False, "day_root": str(root)}}
        rendered = bridge.build_lived_turn_context(root, cfg)
        self.assertTrue(rendered)
        self.assertLessEqual(len(rendered), 1800)
        self.assertIn("有限视野", rendered)
        self.assertNotIn("SECRET_EXCERPT_SHOULD_NOT_LEAK", rendered)
        self.assertNotIn("一段旧笔记", rendered)


def _new_modules_have_no_hardcoded_identity() -> None:
    """Person-neutral copy: the engine and view must never hard-code a name or pronoun."""

    for rel in (
        "components/life/scripts/v03_epistemic_encounters.py",
        "components/phone/v03_encounters_view.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "千代" in text:
            raise AssertionError(f"{rel} hard-codes 千代")
        # U+5979 is the feminine third-person pronoun; the copy uses TA instead.
        if "她" in text:
            raise AssertionError(f"{rel} hard-codes the feminine pronoun")


class V03PersonNeutralCopyTest(unittest.TestCase):
    def test_01_no_hardcoded_identity(self) -> None:
        _new_modules_have_no_hardcoded_identity()


POSIX_ONLY = unittest.skipUnless(
    hasattr(os, "getuid") and os.name == "posix",
    "Requires a POSIX loopback server (run inside WSL)",
)


@POSIX_ONLY
class V03HTTPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        import test_v02_phone_http_surfaces as v02http
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = "chiyo"
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v03-enc-http-"))
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
        (self.stack.root / "phone" / "v03_encounters.json").unlink(missing_ok=True)

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

    def test_01_get_zero_write_partner_view_and_guards(self) -> None:
        path = "/api/phone/v03/encounters"
        # Seed one hidden and one shared encounter as the subject over HTTP.
        c_hidden, _ = self._http(
            "POST", path,
            {"action": "record_first_glance", "target_ref": "private:a",
             "target_title_cn": "不公开的片段", "seen_pages": [1], "total_pages": 2,
             "current_impression_cn": "不公开", "shared": False},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_hidden, 200)
        c_shared, _ = self._http(
            "POST", path,
            {"action": "record_first_glance", "target_ref": "shared:b",
             "target_title_cn": "公开的地图", "seen_pages": [1, 2], "total_pages": 4,
             "current_impression_cn": "只看了半张", "shared": True},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_shared, 200)

        before = _snapshot(Path(self.stack.root))
        code_p, res_p = self._http("GET", path)
        self.assertEqual(code_p, 200, res_p)
        data = res_p.get("data") or {}
        self.assertEqual(data.get("schema"), "nyairo.v03.encounters.v1")
        self.assertEqual(data.get("perspective"), "partner")
        self.assertEqual(data.get("llm_calls"), 0)
        titles = [row.get("target_title_cn") for row in data.get("encounters") or []]
        self.assertEqual(titles, ["公开的地图"])
        self.assertNotIn("不公开的片段", json.dumps(res_p, ensure_ascii=False))

        code_s, res_s = self._http("GET", path, capability=self.CAP)
        self.assertEqual(code_s, 200, res_s)
        subject_titles = sorted(
            row.get("target_title_cn") for row in (res_s.get("data") or {}).get("encounters") or []
        )
        self.assertEqual(subject_titles, sorted(["不公开的片段", "公开的地图"]))

        code_q, res_q = self._http("GET", path + "?unexpected=1")
        self.assertEqual(code_q, 400)
        self.assertEqual(res_q.get("reason_code"), "UNKNOWN_QUERY")
        code_bad, _ = self._http("GET", path, capability="forged-capability-token")
        self.assertEqual(code_bad, 403)
        after = _snapshot(Path(self.stack.root))
        self.assertEqual(before, after, "GET must be 100% zero-write (bytes and mtimes)")

    def test_02_post_permission_identity_and_withdraw_suppression(self) -> None:
        path = "/api/phone/v03/encounters"

        self.assertEqual(self._http("POST", path, {"action": "drop_shared_material"})[0], 403)
        self.assertEqual(
            self._http("POST", path + "?x=1", {"action": "drop_shared_material"}, csrf=True)[0], 400
        )
        self.assertEqual(self._http("POST", path, {}, csrf=True)[0], 400)

        c_uid, res_uid = self._http(
            "POST", path,
            {"action": "drop_shared_material", "title_cn": "x", "excerpt_cn": "y", "uid": "root"},
            csrf=True,
        )
        self.assertEqual(c_uid, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(res_uid.get("reason_code") or ""))
        c_author, res_author = self._http(
            "POST", path,
            {"action": "drop_shared_material", "title_cn": "x", "excerpt_cn": "y",
             "author_side": "subject"},
            csrf=True,
        )
        self.assertEqual(c_author, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(res_author.get("reason_code") or ""))

        # Subject-only action without a credential is the partner door: refused.
        c_forbid, res_forbid = self._http(
            "POST", path,
            {"action": "record_first_glance", "target_ref": "x", "target_title_cn": "x",
             "seen_pages": [1], "total_pages": 1, "current_impression_cn": "x"},
            csrf=True,
        )
        self.assertEqual(c_forbid, 403, res_forbid)
        self.assertEqual(res_forbid.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        # The partner's own drop works with CSRF only.
        dropped = self._http(
            "POST", path,
            {"action": "drop_shared_material", "title_cn": "一段旧笔记",
             "excerpt_cn": "SECRET_HTTP_EXCERPT", "page_count": 2},
            csrf=True,
        )
        self.assertEqual(dropped[0], 200, dropped[1])
        material_id = (dropped[1].get("data") or {}).get("shared_materials", [{}])[0].get("material_id")
        self.assertTrue(material_id)

        withdrawn = self._http(
            "POST", path,
            {"action": "withdraw_shared_material", "material_id": material_id},
            csrf=True,
        )
        self.assertEqual(withdrawn[0], 200, withdrawn[1])

        code_g, res_g = self._http("GET", path, capability=self.CAP)
        self.assertEqual(code_g, 200)
        self.assertNotIn("SECRET_HTTP_EXCERPT", json.dumps(res_g, ensure_ascii=False))
        materials = (res_g.get("data") or {}).get("shared_materials") or []
        self.assertEqual([row.get("status") for row in materials], ["WITHDRAWN"])
        self.assertEqual(materials[0].get("excerpt_cn"), "")

        c_reopen, res_reopen = self._http(
            "POST", path,
            {"action": "open_or_use_shared_material", "material_id": material_id,
             "mode": "open_partial", "opened_pages": [1]},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_reopen, 409, res_reopen)
        self.assertEqual(res_reopen.get("reason_code"), "MATERIAL_WITHDRAWN")


if __name__ == "__main__":
    unittest.main()
