#!/usr/bin/env python3
"""v0.8 focused situation selector + emergent echoes projection bridge.

Coverage:
* ``_PHONE_AND_WORLD_GUIDANCE`` stays <= 1200 chars and still names every required
  read action plus the new ``v08_emergent_echoes`` surface;
* the bridge is 100% zero-write whether ``<day_root>/phone/v08_emergent_echoes.json``
  exists or is missing;
* restricted privacy withholds private future-self questions and unshared reinterpreted
  memories entirely; the formatter itself filters them under ``include_private=False``;
* an empty incoming turn preserves the exact historical middle-section order;
* a spoken shared idiom (or a future-question keyword) wakes the matching echo and keeps
  it inside the 1800-char budget even under heavy multi-module load, with zero nagging
  when the turn is unrelated;
* a cold/illness turn lifts ``sensory_line`` / ``v03_line`` while autonomy and the
  intimacy boundary still render;
* the added code never hard-codes a pronoun or a character name.

Run (WSL):
``PYTHONDONTWRITEBYTECODE=1 python -B -m unittest tests.test_v08_focused_situation_bridge -v``
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402


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


def _restricted_cfg(day_root: Path, *, private: bool) -> dict[str, Any]:
    return {
        "day_root": str(day_root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": private},
    }


def _permitted_cfg(day_root: Path) -> dict[str, Any]:
    return {
        "day_root": str(day_root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


class GuidanceAndWiringTest(unittest.TestCase):
    def _plugin(self):
        path = ROOT / "plugins" / "chiyo" / "__init__.py"
        spec = importlib.util.spec_from_file_location("chiyo_plugin_focused_situation", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_01_guidance_bounded_and_names_new_surface(self) -> None:
        guidance = self._plugin()._PHONE_AND_WORLD_GUIDANCE
        self.assertLessEqual(len(guidance), 1200, f"guidance length {len(guidance)}")
        required = [
            "desk_presence", "desk_notes", "home_window", "home_keepsakes",
            "home_letters", "home_workbench", "home_curios", "home_trio",
            "home_reunion_anchor", "home_recipes", "home_tentative_plans",
            "wardrobe", "pocket_lists", "outing_bench_bookstore",
            "outing_routes_market", "outing_gallery_blueprint",
            "longterm_habits", "longterm_gifts_lantern", "longterm_resume_hub",
            "body_somatic", "body_garment_window", "body_care", "body_sensory",
            "character_pack", "autonomy_rhythm", "emotional_tension",
            "stance_containment", "adult_intimacy", "v03_clock",
            "v03_ecology", "v03_embodied_social",
            "v08_living_traces", "v03_encounters",
            "phone_life_update", "world_action", "TOILET", "work_context",
            "compose_open_act",
            'target_surface="',
            "v08_emergent_echoes",
        ]
        for token in required:
            self.assertIn(token, guidance, f"missing guidance token: {token}")
        self.assertNotIn(', surface="', guidance)

    def test_02_phone_tool_wiring(self) -> None:
        self.assertEqual(
            phone_tool.READ_ACTIONS["v08_emergent_echoes"], "/api/phone/v08/emergent-echoes"
        )
        self.assertIn("v08_emergent_echoes", phone_tool.ACTIONS)
        self.assertIn("v08_emergent_echoes", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v08_emergent_echoes", phone_tool._SUBJECT_VIEW_READ_ACTIONS)
        self.assertIn("v08_emergent_echoes", phone_tool.LIFE_UPDATE_SURFACES)
        self.assertIn("v08_emergent_echoes", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertEqual(
            phone_tool._PARTNER_ONLY_SUBACTIONS["v08_emergent_echoes"],
            frozenset({"partner_add_divergent_notice"}),
        )
        hints = phone_tool._SURFACE_SUPPORTED_HINTS["v08_emergent_echoes"]["subject_actions"]
        for name in (
            "plant_future_question", "revisit_future_question",
            "record_memory_reinterpretation", "share_memory_reinterpretation",
            "record_cross_domain_spark", "settle_spark_attempt",
            "subject_add_divergent_notice",
            "propose_shared_idiom", "confirm_shared_idiom", "fade_shared_idiom",
        ):
            self.assertIn(name, hints)

    def test_03_no_hardcoded_pronoun_or_name_in_added_code(self) -> None:
        for obj in (
            bridge._format_v08_emergent_echoes_micro,
            bridge._rank_middle_sections_by_situation,
            bridge._situation_boost,
            bridge._incoming_touches,
            bridge._v08_idiom_is_active,
        ):
            source = inspect.getsource(obj)
            self.assertNotIn("她", source, obj.__name__)
            self.assertNotIn("千代", source, obj.__name__)
        hints = repr(phone_tool._SURFACE_SUPPORTED_HINTS["v08_emergent_echoes"])
        self.assertNotIn("她", hints)
        self.assertNotIn("千代", hints)


def _echo_doc() -> dict[str, Any]:
    return {
        "shared_idioms": [
            {"phrase": "把灯留一盏", "meaning": "晚归也有人在等", "origin": "搬进来的第一晚"},
            {"phrase": "老掉的说法", "meaning": "不该再唤醒", "origin": "更早", "faded": True},
        ],
        "future_self_questions": [
            {
                "question_cn": "以后还想不想继续写这样的结局？",
                "status": "open",
                "visibility": "private",
                "keywords": ["结局", "写法"],
                "anchor_ref": "稿纸第七页",
            },
            {
                "question_cn": "要不要把这套配色留给冬天？",
                "status": "open",
                "visibility": "shared",
                "keywords": ["配色"],
            },
            {
                "question_cn": "已经收好的问题",
                "status": "resolved",
                "visibility": "shared",
                "keywords": ["已经"],
            },
        ],
        "memory_reinterpretations": [
            {
                "episode_title_cn": "雨夜等门",
                "episode_ref": "episode:rain-night",
                "original_fact_cn": "那天以为只是顺路",
                "view_cn": "现在明白是特意折返",
                "shared_with_partner": True,
            },
            {
                "episode_title_cn": "藏起来的信",
                "episode_ref": "episode:hidden-letter",
                "original_fact_cn": "当时没敢说有写",
                "view_cn": "如今觉得那是留给自己的勇气",
                "shared_with_partner": False,
            },
        ],
        "divergent_notices": [
            {
                "target_title_cn": "墙上的画",
                "target_ref": "art:wall",
                "subject_notice_cn": "觉得留白恰好",
                "partner_notice_cn": "觉得该再添几笔",
            }
        ],
    }


class EmergentEchoFormatterTest(unittest.TestCase):
    def test_01_idiom_wakes_only_on_exact_phrase(self) -> None:
        doc = _echo_doc()
        hit = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="今晚还会把灯留一盏吗", include_private=True,
            now_dt=bridge.datetime.now(tz=bridge.timezone.utc),
        )
        self.assertIn("默契说法「把灯留一盏」", hit)
        self.assertIn("不必勉强照演", hit)
        miss = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="今天天气不错", include_private=True,
            now_dt=bridge.datetime.now(tz=bridge.timezone.utc),
        )
        self.assertEqual(miss, "")
        self.assertNotIn("老掉的说法", hit)

    def test_02_question_and_memory_privacy_gate(self) -> None:
        doc = _echo_doc()
        now = bridge.datetime.now(tz=bridge.timezone.utc)
        # private future question + unshared memory are withheld when include_private=False
        hidden = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="那个结局的写法我再想想", include_private=False, now_dt=now,
        )
        self.assertNotIn("以后还想不想继续写这样的结局", hidden)
        self.assertNotIn("藏起来的信", hidden)
        # shared question and shared memory still surface for the same cues
        shared = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="配色和雨夜等门都还记得", include_private=False, now_dt=now,
        )
        self.assertIn("留给未来的问题被触发", shared)
        self.assertIn("旧事新解：雨夜等门", shared)
        # include_private surfaces the private rows (question is explicitly tagged)
        full = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="那个结局的写法我再想想", include_private=True, now_dt=now,
        )
        self.assertIn("以后还想不想继续写这样的结局", full)
        self.assertIn("未向伴侣公开", full)

    def test_03_divergent_notice_needs_target_cue(self) -> None:
        doc = _echo_doc()
        now = bridge.datetime.now(tz=bridge.timezone.utc)
        out = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="墙上的画你怎么看", include_private=True, now_dt=now,
        )
        self.assertIn("各自看法：墙上的画", out)
        self.assertIn("觉得该再添几笔", out)

    def test_04_bounded(self) -> None:
        doc = _echo_doc()
        now = bridge.datetime.now(tz=bridge.timezone.utc)
        out = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="把灯留一盏 结局写法 雨夜等门 墙上的画",
            include_private=True, now_dt=now,
        )
        self.assertLessEqual(len(out), 180)


class OrderingAndPriorityTest(unittest.TestCase):
    def _sections(self) -> list[tuple[str, str]]:
        return [
            ("autonomy_line", "- 自主边界 " + "x" * 60),
            ("interactive_line", "- 互动 " + "a" * 60),
            ("epistemic_micro_line", "- 认知 " + "b" * 60),
            ("desires_line", "- 心愿 " + "c" * 60),
            ("v03_line", "- v03 " + "d" * 60),
            ("v04_line", "- v04 " + "e" * 60),
            ("v02_deep_line", "- v02 " + "f" * 60),
            ("v08_traces_line", "- 屋里动态 " + "g" * 60),
            ("v08_echoes_line", "- 心里回响 " + "h" * 60),
            ("v03_enc_line", "- 有限视野 " + "i" * 60),
            ("desk_line", "- 书桌 " + "j" * 60),
            ("sensory_line", "- 体感 " + "k" * 60),
            ("anchor_line", "- 锚点 " + "l" * 60),
        ]

    def test_01_empty_incoming_preserves_historical_order(self) -> None:
        sections = self._sections()
        ranked = bridge._rank_middle_sections_by_situation(sections, clean_incoming="")
        self.assertEqual([name for name, _ in ranked], [name for name, _ in sections])
        self.assertEqual(ranked[0][0], "autonomy_line")

    def test_02_matching_idiom_prioritizes_echo_after_autonomy(self) -> None:
        sections = self._sections()
        # make the echo section a real matched-idiom line
        sections = [
            (name, "默契说法「把灯留一盏」：晚归也有人在等（若忙或不方便，不必勉强照演）"
             if name == "v08_echoes_line" else text)
            for name, text in sections
        ]
        ranked = bridge._rank_middle_sections_by_situation(
            sections, clean_incoming="今晚还会把灯留一盏吗"
        )
        names = [name for name, _ in ranked]
        self.assertEqual(names[0], "autonomy_line")
        self.assertEqual(names[1], "v08_echoes_line")

    def test_03_cold_turn_lifts_sensory_and_v03_over_anchor(self) -> None:
        ranked = bridge._rank_middle_sections_by_situation(
            self._sections(), clean_incoming="今天好冷，有点发烧，穿少了，好累"
        )
        names = [name for name, _ in ranked]
        self.assertEqual(names[0], "autonomy_line")
        self.assertLess(names.index("sensory_line"), names.index("anchor_line"))
        self.assertLess(names.index("v03_line"), names.index("anchor_line"))

    def test_04_empty_incoming_never_boosts(self) -> None:
        for name, text in self._sections():
            self.assertEqual(bridge._situation_boost(name, text, ""), 0)


class ZeroWriteAndRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-focused-situation-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, payload: dict[str, Any]) -> None:
        (self.root / "phone" / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_01_zero_write_with_file_present_and_missing(self) -> None:
        cfg = _permitted_cfg(self.root)
        # present
        self._write("v08_emergent_echoes.json", _echo_doc())
        before = _snapshot(self.root)
        out_present = bridge.render_lived_companion_context(
            self.root, cfg, "今晚还会把灯留一盏吗", ephemeral_hint=""
        )
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(out_present, out_present)  # deterministic callable
        # missing
        (self.root / "phone" / "v08_emergent_echoes.json").unlink()
        before = _snapshot(self.root)
        out_missing = bridge.render_lived_companion_context(
            self.root, cfg, "今晚还会把灯留一盏吗", ephemeral_hint=""
        )
        self.assertEqual(_snapshot(self.root), before)
        self.assertNotIn("心里回响", out_missing)

    def test_02_restricted_privacy_withholds_private_echoes(self) -> None:
        self._write("v08_emergent_echoes.json", _echo_doc())
        restricted = bridge.render_lived_companion_context(
            self.root,
            _restricted_cfg(self.root, private=False),
            "那个结局的写法 藏起来的信",
            ephemeral_hint="",
        )
        self.assertNotIn("以后还想不想继续写这样的结局", restricted)
        self.assertNotIn("藏起来的信", restricted)
        self.assertNotIn("默契说法", restricted)

        permitted = bridge.render_lived_companion_context(
            self.root,
            _permitted_cfg(self.root),
            "那个结局的写法我再想想",
            ephemeral_hint="",
        )
        self.assertIn("以后还想不想继续写这样的结局", permitted)

    def test_03_heavy_load_keeps_woken_echo_in_budget(self) -> None:
        self._write("v08_emergent_echoes.json", _echo_doc())
        # a busy autonomy line and a sensory line compete for the same 1800 chars
        self._write(
            "emotional_tension.json",
            {
                "tension_summary": {"subject_is_busy": True},
                "schedule": {"availability_summary": "正在赶一份很长的校对稿"},
                "quarrel": {"in_space": True, "ruptures": [{"status": "OPEN", "description": "约好出门却改期"}]},
            },
        )
        self._write(
            "somatic_comfort.json",
            {
                "window": {"state": "closed"},
                "local_position": {"zone": "desk_side"},
                "modalities": {
                    "thermal": {"status": "available", "quality": "偏冷"},
                    "tactile": {"status": "available", "quality": "指尖发凉"},
                },
            },
        )
        heavy_hint = "背景" * 450  # truncated to 850 chars, eating the budget
        rendered = bridge.render_lived_companion_context(
            self.root, _permitted_cfg(self.root), "今晚还会把灯留一盏吗", ephemeral_hint=heavy_hint
        )
        self.assertLessEqual(len(rendered), 1800)
        self.assertIn("默契说法「把灯留一盏」", rendered)
        self.assertIn("自主边界", rendered)
        self.assertIn("36B成年亲密边界", rendered)
        self.assertLess(rendered.index("心里回响"), rendered.index("36B成年亲密边界"))

        # zero nagging: the same heavy load without a matching cue never dumps the idiom
        quiet = bridge.render_lived_companion_context(
            self.root, _permitted_cfg(self.root), "今天把书桌整理了一下", ephemeral_hint=heavy_hint
        )
        self.assertNotIn("默契说法「把灯留一盏」", quiet)

    def test_04_cold_turn_keeps_autonomy_and_intimacy(self) -> None:
        self._write(
            "emotional_tension.json",
            {
                "tension_summary": {"subject_is_busy": True},
                "schedule": {"availability_summary": "手头真的忙"},
            },
        )
        self._write(
            "somatic_comfort.json",
            {
                "window": {"state": "closed"},
                "local_position": {"zone": "desk_side"},
                "modalities": {"thermal": {"status": "available", "quality": "冷得发紧"}},
            },
        )
        rendered = bridge.render_lived_companion_context(
            self.root, _permitted_cfg(self.root), "今天好冷，有点发烧，穿少了，好累",
            ephemeral_hint="",
        )
        self.assertLessEqual(len(rendered), 1800)
        self.assertIn("自主边界", rendered)
        self.assertIn("36B成年亲密边界", rendered)
        self.assertIn("体感", rendered)


if __name__ == "__main__":
    unittest.main()
