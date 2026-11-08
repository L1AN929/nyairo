"""Unit tests for v0.4 Ch.47/50 lightweight companion extensions:
1. Shared Lexicon ("只有彼此懂的说法") — deterministic match vs 0-token unmatched, /chiyo_phrase, ChiyoContextEngine injection.
2. Questions to Future Self ("给未来自己的问题") — CRUD + chiyo_phone(action="work_context") integration.
3. Embodied Chat Action Receipts — chiyo_phone & world_move receipt recording + diary body privacy guard.
4. Phone & World guidance prompt section selection.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chiyo_bundle import companion_extras as ce
from chiyo_bundle import phone_tool


class CompanionExtrasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = Path(self.tmp.name) / "profile"
        (self.profile / "chiyo").mkdir(parents=True, exist_ok=True)
        (self.profile / "chiyo" / "config.json").write_text(
            json.dumps({
                "owner": "local-owner",
                "cli_owner": True,
                "local_owner_uid": os.getuid() if hasattr(os, "getuid") else None,
                "memory": False,
                "life": False,
                "phone": {
                    "enabled": True,
                    "write_enabled": True,
                    "private_artifacts": True,
                    "base_url": "http://127.0.0.1:3831",
                },
            }),
            encoding="utf-8",
        )

    def test_shared_phrases_crud_and_zero_cost_when_unmatched(self) -> None:
        saved = ce.upsert_shared_phrase(
            self.profile,
            phrase="窗边时间",
            meaning="不用急着找话题，两个人安静待一会儿就好",
            origin="雨天在窗边定下的说法",
        )
        self.assertTrue(saved["id"].startswith("phr_"))
        self.assertEqual(len(ce.list_shared_phrases(self.profile)), 1)

        # Unmatched text -> empty list, 0 extra prompt characters!
        unmatched = ce.match_shared_phrases(self.profile, "今天午饭吃什么好呢？")
        self.assertEqual(unmatched, [])
        self.assertEqual(ce.render_matched_phrases_block(unmatched), "")

        # Matched text -> returns exact explanation framed as non-directive meaning
        matched = ce.match_shared_phrases(self.profile, "忙完啦，今晚开启窗边时间吧")
        self.assertEqual(len(matched), 1)
        block = ce.render_matched_phrases_block(matched)
        self.assertIn("「窗边时间」", block)
        self.assertIn("不用急着找话题", block)
        self.assertIn("雨天在窗边定下的说法", block)
        self.assertIn("不代表自动执行固定指令", block)

        # Updating same phrase deduplicates instead of creating a second row
        ce.upsert_shared_phrase(
            self.profile,
            phrase="窗边时间",
            meaning="各自看书或发呆，不用勉强找话聊",
            origin="更新后的默契",
        )
        rows = ce.list_shared_phrases(self.profile)
        self.assertEqual(len(rows), 1)
        self.assertIn("各自看书或发呆", rows[0]["meaning"])

        self.assertTrue(ce.delete_shared_phrase(self.profile, "窗边时间"))
        self.assertEqual(ce.list_shared_phrases(self.profile), [])

    def test_future_questions_and_phone_work_context_injection(self) -> None:
        q = ce.upsert_future_question(
            self.profile,
            artifact_id="artifact:poem-01",
            artifact_title="凪浦防波堤观察志",
            version_id="ver:1",
            version_number=1,
            question="等入冬再重读第一版，还会觉得结尾写得太淡吗？",
        )
        self.assertTrue(q["id"].startswith("fq_"))
        self.assertEqual(len(ce.list_future_questions(self.profile, artifact_id="artifact:poem-01")), 1)
        self.assertEqual(len(ce.list_future_questions(self.profile, artifact_id="artifact:other")), 0)

        # Answer / reflection
        answered = ce.answer_future_question(
            self.profile,
            question_id=q["id"],
            reflection="重读发现留白刚刚好，不需要加浓。",
        )
        self.assertIsNotNone(answered)
        self.assertEqual(answered["reflection"], "重读发现留白刚刚好，不需要加浓。")
        self.assertTrue(bool(answered["answered_at"]))

        # Verify chiyo_phone(action="work_context") includes future_questions when present
        fake_state = ({"ok": True, "csrf_token": "tok"}, "tok")
        fake_ctx = (200, {
            "ok": True,
            "status": "ready",
            "data": {"artifact_id": "artifact:poem-01", "head": {"version_number": 2}},
        })
        with patch.object(phone_tool, "_state", return_value=fake_state), \
             patch.object(phone_tool, "_request", return_value=fake_ctx):
            raw_out = phone_tool.phone_handler(
                {"action": "work_context", "artifact_id": "artifact:poem-01"},
                hermes_home=str(self.profile),
                authorized_personal_context=True,
            )
        parsed = json.loads(raw_out)
        self.assertTrue(parsed["ok"])
        self.assertIn("future_questions", parsed["result"])
        self.assertEqual(parsed["result"]["future_questions"][0]["question"], "等入冬再重读第一版，还会觉得结尾写得太淡吗？")

    def test_action_receipts_and_diary_privacy(self) -> None:
        # 1. Room MOVE receipt
        r1 = ce.record_phone_action_receipt(
            self.profile,
            "world_action",
            {"world_action": "MOVE", "params": {"destination_location_id": "home/living_room"}},
            {"ok": True, "status": "applied"},
            operation_key="op-move-1",
        )
        self.assertIsNotNone(r1)
        self.assertEqual(r1["label"], "已前往 · 客厅")
        self.assertEqual(r1["nav_target"], "world:map")

        # 2. Private diary write MUST NEVER leak diary body into receipts!
        secret_body = "这是她只写给自己看的私密心事正文，绝不能出现在回执标签里"
        r2 = ce.record_phone_action_receipt(
            self.profile,
            "diary_write",
            {"body": secret_body},
            {"ok": True, "status": "applied"},
            operation_key="op-diary-1",
        )
        self.assertIsNotNone(r2)
        self.assertEqual(r2["label"], "写下了一篇私密日记")
        raw_file = ce.action_receipts_path(self.profile).read_text(encoding="utf-8")
        self.assertNotIn(secret_body, raw_file)

        # 3. Rejected actions do NOT create false-positive receipts
        r3 = ce.record_phone_action_receipt(
            self.profile,
            "world_action",
            {"world_action": "MOVE", "params": {"destination_location_id": "moon"}},
            {"ok": False, "status": "rejected"},
            operation_key="op-fail-1",
        )
        self.assertIsNone(r3)

    def test_plugin_phone_guidance_enabled_when_phone_on(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "chiyo_plugin_init", ROOT / "plugins" / "chiyo" / "__init__.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch("chiyo_bundle.hermes_plugin.configuration", return_value=(
            self.profile, {"phone": {"enabled": True}}
        )):
            g = mod._world_move_guidance()
        self.assertIn("chiyo_phone", g)
        self.assertIn("world_action", g)
        self.assertLessEqual(len(g), 1200)

    def test_chiyo_world_tools_not_deferred_by_hermes_tool_search(self) -> None:
        import types
        hermes_root = ROOT / "vendor" / "hermes"
        if str(hermes_root) not in sys.path:
            sys.path.insert(0, str(hermes_root))
        if "snowballstemmer" not in sys.modules:
            try:
                import snowballstemmer  # noqa: F401
            except ImportError:
                fake_sb = types.ModuleType("snowballstemmer")
                fake_sb.stemmer = lambda _lang: types.SimpleNamespace(stemWord=lambda w: w)
                sys.modules["snowballstemmer"] = fake_sb
        from tools import tool_search
        from tools.registry import registry
        from chiyo_bundle.hermes_plugin import configure_host_llm
        from chiyo_bundle.phone_tool import PHONE_TOOL_SCHEMA, phone_handler
        from chiyo_bundle.world_move_tool import MOVE_TOOL_SCHEMA, move_handler

        registry.register(
            name="chiyo_phone",
            toolset="chiyo_world",
            schema=PHONE_TOOL_SCHEMA,
            handler=phone_handler,
        )
        registry.register(
            name="world_move",
            toolset="chiyo_world",
            schema=MOVE_TOOL_SCHEMA,
            handler=move_handler,
        )
        configure_host_llm(None)
        self.assertIn("chiyo_world", tool_search._DIRECT_SURFACE_TOOLSETS)
        self.assertFalse(
            tool_search.is_deferrable_tool_name(
                "chiyo_phone", tool_search._DEFAULT_DEFERRED_TOOLS
            )
        )
        self.assertFalse(
            tool_search.is_deferrable_tool_name(
                "world_move", tool_search._DEFAULT_DEFERRED_TOOLS
            )
        )

    def test_living_recorded_switches_forwards_world_actions_gate(self) -> None:
        from components.phone import living
        rdir = living.run_dir(self.profile)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / living.MANAGED_DAY_FILENAME).write_text(
            json.dumps({
                "room_living": True,
                "ambient_events": True,
                "world_actions": True,
            }),
            encoding="utf-8",
        )
        switches = living._recorded_switches(self.profile)
        self.assertTrue(switches.get("world_actions"))
        overrides = living._switch_overrides(switches) or {}
        self.assertEqual(overrides.get("WORLD_BODY_INTENT_MUTATION_GATE"), "canonical")

    def test_intent_executor_respects_client_environment_gate_and_paths(self) -> None:
        bridge_dir = ROOT / "components" / "world" / "service" / "bridge"
        if str(bridge_dir) not in sys.path:
            sys.path.insert(0, str(bridge_dir))
        import intent_executor as ie
        self.assertEqual(
            ie.intent_gate({"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}),
            "canonical",
        )
        world_run = self.profile / "world" / "run"
        world_run.mkdir(parents=True, exist_ok=True)
        lc = ie._lifecycle({"CHIYO_WORLD_HOME": str(self.profile / "world")})
        self.assertTrue(str(lc.path).startswith(str(self.profile / "world")))

    def test_phone_server_living_pause_and_stop_accept_operation_key(self) -> None:
        from components.phone import server as phone_server
        self.assertIn("operation_key", phone_server.LIVING_PAUSE_FIELDS)
        self.assertIn("paused", phone_server.LIVING_PAUSE_FIELDS)

    def test_phone_tool_projects_action_and_facility_id_schema(self) -> None:
        from chiyo_bundle import phone_tool
        self.assertIn("projects", phone_tool.READ_ACTIONS)
        self.assertIn("projects", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        props = (
            phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]["params"]["properties"]
        )
        self.assertIn("facility_id", props)


if __name__ == "__main__":
    unittest.main()
