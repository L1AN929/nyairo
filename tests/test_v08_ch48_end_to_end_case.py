#!/usr/bin/env python3
"""Two flagship outline benchmarks as honest end-to-end unit tests (``llm_calls == 0``).

Story 1 -- v0.4 Ch.48 §九 four-condition case (bounded, non-renewing lingering thread +
the urgency exception).  The real echoes engine writes // reads its own state file; the
bridge is exercised through ``build_lived_turn_context`` exactly as a live turn would use
it:

* Condition A (ordinary arrival at +30m): the lingering thread is injected, still stored,
  and no urgency exception fires;
* Condition B (urgent arrival): the concise exception cue fires, survives inside the
  ``<= 1800`` char ceiling, and the echoes line is boosted right behind ``autonomy_line``;
* Condition C (natural fade after +130m, reading never extends TTL): the thread stops being
  injected while the record stays on disk as calm history (not deleted);
* Condition D (``private_artifacts=False``): the private lingering thread and the private
  future-self question are withheld entirely.

Story 2 -- v3.0 Ch.25 §七 "一只杯子的一周" (A Cup's Week).  The real universal-matter engine
runs the whole week:

* Day 1: ``compose_open_act`` drops two pens into ``ceramic_cup_01`` and its live
  ``derived_role_cn`` becomes a temporary pen holder;
* Day 2: trying to ``CONSUME_PORTION`` the cup is refused ``409 VESSEL_OCCUPIED_OR_SOILED``;
* Day 4: a partner moves the cup to ``home_staging_corner``; ``subject_last_seen_parent``
  stays ``home_desk`` and ``impression_stale`` is ``True`` -- zero mind-teleportation;
* Day 5: ``scan_matter_opportunities_zero_write`` surfaces ``v08_matter:observe:ceramic_cup_01``
  (strictly zero-write); ``OBSERVE_OBJECT`` + returning the pens to ``home_desk`` restores the
  cup to ``空置可用的容器``.

No user-visible string hard-codes ``她`` or ``千代``.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_ch48_end_to_end_case -v``
"""

from __future__ import annotations

import json
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
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

#: One fixed reference instant for the deterministic formatter-level checks.
_T0 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

_THREAD_SUMMARY = "刚才修改卡住有点挫败"


def _utc(minutes_ago: int = 0) -> str:
    """The current real UTC instant shifted back, as the engine's ``now_iso`` wants it."""

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _cfg(root: Path, *, private: bool) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": private},
    }


def _write_elastic_play(root: Path, *, preference: str) -> Path:
    """A raw ``v04_elastic_play`` state file with a learned deferred-reply preference."""

    path = root / "phone" / "v04_elastic_play.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "nyairo.v04.elastic_play_state.v1",
        "revision": 2,
        "updated_at": _utc(),
        "day_direction": {
            "direction_id": "dd-1",
            "direction": "继续改第二段",
            "status": "in_progress",
            "updated_at": _utc(),
        },
        "scoped_preferences": [
            {
                "pref_id": "pref-partner_explanation",
                "scope": "partner_explanation",
                "instruction": preference,
                "preference": preference,
                "taught_by": "partner",
                "status": "active",
                "applies_to_private_writing": False,
                "updated_at": _utc(),
            }
        ],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _note_thread(root: Path, *, minutes_ago: int, visibility: str = "private",
                 summary: str = _THREAD_SUMMARY, ttl_minutes: int = 120) -> dict[str, Any]:
    projected, _status = echoes.apply_emergent_echoes_mutation(
        root,
        {
            "action": "note_lingering_thread",
            "summary_cn": summary,
            "scope_kind": "work",
            "scope_ref": "work:second_paragraph",
            "ttl_minutes": ttl_minutes,
            "visibility": visibility,
        },
        actor_side="subject",
        now_iso=_utc(minutes_ago),
    )
    return projected["lingering_threads"][-1]


def _sketch_direction(root: Path, *, visibility: str = "shared") -> dict[str, Any]:
    projected, _status = echoes.apply_emergent_echoes_mutation(
        root,
        {
            "action": "sketch_day_direction",
            "horizon_note_cn": "继续改第二段",
            "next_decision_boundary_cn": "写不动就先停一下",
            "visibility": visibility,
        },
        actor_side="subject",
        now_iso=_utc(),
    )
    return projected["day_directions"][-1]


def _plant_question(root: Path, *, visibility: str = "private") -> dict[str, Any]:
    projected, _status = echoes.apply_emergent_echoes_mutation(
        root,
        {
            "action": "plant_future_question",
            "question_cn": "第二段到底想写成什么样子",
            "anchor_kind": "work",
            "anchor_ref": "work:second_paragraph",
            "keywords": ["第二段"],
            "visibility": visibility,
        },
        actor_side="subject",
        now_iso=_utc(),
    )
    return projected["future_self_questions"][-1]


class Ch48FourConditionTest(unittest.TestCase):
    """v0.4 Ch.48 §九: the four-condition lingering-thread / urgency case."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch48-e2e-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_condition_a_ordinary_arrival_injects_thread_without_exception(self) -> None:
        _note_thread(self.root, minutes_ago=30, visibility="private")
        _sketch_direction(self.root)
        _plant_question(self.root, visibility="private")
        _write_elastic_play(self.root, preference="忙的时候可以先收尾再聊")

        rendered = bridge.build_lived_turn_context(
            self.root, _cfg(self.root, private=True),
            incoming_text="我在楼下等你，一起吃个饭吧",
        )
        self.assertLessEqual(len(rendered), bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("挂着的一缕余绪", rendered)
        self.assertIn(_THREAD_SUMMARY, rendered)
        # Ordinary words never trigger the urgency exception.
        self.assertNotIn(bridge._URGENCY_EXCEPTION_CUE, rendered)
        self.assertNotIn("对方提及急事", rendered)

    def test_condition_b_urgent_arrival_fires_exception_in_top_block(self) -> None:
        _note_thread(self.root, minutes_ago=30, visibility="private")
        _sketch_direction(self.root)
        _plant_question(self.root, visibility="private")
        _write_elastic_play(self.root, preference="忙的时候可以先收尾再聊")

        rendered = bridge.build_lived_turn_context(
            self.root, _cfg(self.root, private=True),
            incoming_text="我有急事，马上帮我看看！",
        )
        self.assertLessEqual(len(rendered), bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("对方提及急事", rendered)
        self.assertIn(bridge._URGENCY_EXCEPTION_CUE, rendered)
        # The exception cue floats before the always-present intimacy boundary tail.
        self.assertLess(rendered.index("对方提及急事"), rendered.index("36B成年亲密边界"))

    def test_condition_c_thread_fades_without_deleting_history(self) -> None:
        # Noted 130 minutes ago with a 120-minute TTL: it is already expired and must not be
        # injected.  Reading never renews TTL, so it stays expired on every later render.
        _note_thread(self.root, minutes_ago=130, visibility="private")

        for _ in range(2):
            rendered = bridge.build_lived_turn_context(
                self.root, _cfg(self.root, private=True),
                incoming_text="我回来了，先喝口水",
            )
            self.assertLessEqual(len(rendered), bridge.MAX_LIVED_CONTEXT_CHARS)
            self.assertNotIn("挂着的一缕余绪", rendered)
            self.assertNotIn(_THREAD_SUMMARY, rendered)

        # The record is still calmly stored as history -- expiry is not deletion.
        state = json.loads(
            (self.root / "phone" / "v08_emergent_echoes.json").read_text(encoding="utf-8")
        )
        stored = [row for row in state["lingering_threads"]
                  if row.get("summary_cn") == _THREAD_SUMMARY]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "active", "expiry is not resolution")

    def test_condition_d_private_rows_withheld_without_private_artifacts(self) -> None:
        _note_thread(self.root, minutes_ago=30, visibility="private")
        _sketch_direction(self.root)
        _plant_question(self.root, visibility="private")
        _write_elastic_play(self.root, preference="忙的时候可以先收尾再聊")

        rendered = bridge.build_lived_turn_context(
            self.root, _cfg(self.root, private=False),
            incoming_text="那个第二段的写法，我在楼下等你聊",
        )
        self.assertNotIn("挂着的一缕余绪", rendered)
        self.assertNotIn(_THREAD_SUMMARY, rendered)
        self.assertNotIn("第二段到底想写成什么样子", rendered)

    def test_formatter_ttl_and_private_gate_are_deterministic(self) -> None:
        doc = {
            "lingering_threads": [
                {
                    "summary_cn": _THREAD_SUMMARY,
                    "status": "active",
                    "visibility": "private",
                    "expires_at": "2026-11-16T11:00:00Z",
                }
            ]
        }
        alive = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=True, now_dt=_T0 + timedelta(minutes=30),
        )
        self.assertIn("挂着的一缕余绪", alive)
        self.assertIn(_THREAD_SUMMARY, alive)

        faded = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=True, now_dt=_T0 + timedelta(minutes=130),
        )
        self.assertNotIn("挂着的一缕余绪", faded)

        withheld = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=False, now_dt=_T0 + timedelta(minutes=30),
        )
        self.assertNotIn(_THREAD_SUMMARY, withheld)

    def test_formatter_urgency_needs_live_context(self) -> None:
        direction_only = {
            "day_directions": [
                {
                    "status": "direction_only",
                    "horizon_note_cn": "继续改第二段",
                    "visibility": "shared",
                    "executed_in_world": False,
                }
            ]
        }
        urgent = bridge._format_v08_emergent_echoes_micro(
            direction_only, incoming_text="我有急事，马上帮我看看！",
            include_private=True, now_dt=_T0,
        )
        self.assertIn(bridge._URGENCY_EXCEPTION_CUE, urgent)

        calm = bridge._format_v08_emergent_echoes_micro(
            direction_only, incoming_text="今天天气不错，慢慢来",
            include_private=True, now_dt=_T0,
        )
        self.assertNotIn(bridge._URGENCY_EXCEPTION_CUE, calm)

        # Urgent words alone, with nothing to override, never invent an exception.
        empty = bridge._format_v08_emergent_echoes_micro(
            {}, incoming_text="十万火急", include_private=True, now_dt=_T0,
        )
        self.assertNotIn(bridge._URGENCY_EXCEPTION_CUE, empty)

    def test_ranking_urgency_places_echoes_right_after_autonomy(self) -> None:
        sections = [
            ("autonomy_line", "- 自主边界：手头有正事"),
            ("interactive_line", "- 互动：小游戏"),
            ("v08_echoes_line", "- 心里回响：" + bridge._URGENCY_EXCEPTION_CUE),
            ("desk_line", "- 书桌共在：桌面整洁"),
            ("anchor_line", "- 居家微锚点：穿搭"),
        ]
        ranked = bridge._rank_middle_sections_by_situation(
            sections, clean_incoming="我有急事，马上帮我看看！"
        )
        names = [name for name, _text in ranked]
        self.assertEqual(names[0], "autonomy_line")
        self.assertEqual(names[1], "v08_echoes_line")

    def test_no_hardcoded_identity_in_added_strings(self) -> None:
        for text in (bridge._URGENCY_EXCEPTION_CUE, "挂着的一缕余绪：", _THREAD_SUMMARY):
            self.assertNotIn("她", text)
            self.assertNotIn("千代", text)


def _obj(root: Path, object_id: str) -> dict[str, Any]:
    view = matter.read_matter_space_view(root, actor_side="subject", now_iso=_utc())
    for row in view["objects"]:
        if row["object_id"] == object_id:
            return row
    raise AssertionError(f"object not in view: {object_id}")


class ACupWeekTest(unittest.TestCase):
    """v3.0 Ch.25 §七: ``一只杯子的一周`` -- one cup across five days, real engine only."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-cup-week-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_day1_pens_make_a_temporary_holder(self) -> None:
        self.assertEqual(_obj(self.root, "ceramic_cup_01")["derived_role_cn"], "空置可用的容器")
        receipt = matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把两支笔放进空杯里",
                "steps": [
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                     "target_parent_id": "ceramic_cup_01"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02",
                     "target_parent_id": "ceramic_cup_01"},
                ],
            },
            now_iso=_utc(),
        )
        self.assertEqual(receipt["llm_calls"], 0)
        self.assertEqual(receipt["status"], "applied")
        role = _obj(self.root, "ceramic_cup_01")["derived_role_cn"]
        self.assertTrue(role.startswith("临时装着"), role)
        self.assertIn("腾空", role)
        self.assertEqual(_obj(self.root, "ceramic_cup_01")["occupancy"], 2)

    def test_02_day2_drinking_an_occupied_cup_is_refused_409(self) -> None:
        matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把两支笔放进空杯里",
                "steps": [
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                     "target_parent_id": "ceramic_cup_01"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02",
                     "target_parent_id": "ceramic_cup_01"},
                ],
            },
            now_iso=_utc(),
        )
        with self.assertRaises(matter.V08MatterError) as ctx:
            matter.compose_open_act(
                self.root,
                {"intent_cn": "用这个杯子喝口水",
                 "steps": [{"op": "CONSUME_PORTION", "object_id": "ceramic_cup_01"}]},
                now_iso=_utc(),
            )
        self.assertEqual(ctx.exception.reason_code, "VESSEL_OCCUPIED_OR_SOILED")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_03_day4_partner_move_does_not_teleport_mind(self) -> None:
        matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把两支笔放进空杯里",
                "steps": [
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                     "target_parent_id": "ceramic_cup_01"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02",
                     "target_parent_id": "ceramic_cup_01"},
                ],
            },
            now_iso=_utc(),
        )
        receipt = matter.partner_move_shared_object(
            self.root,
            {"object_id": "ceramic_cup_01", "target_parent_id": "home_staging_corner"},
            now_iso=_utc(),
        )
        self.assertEqual(receipt["llm_calls"], 0)
        # The subject still believes the cup is on her desk; the God-view alone
        # knows the real spot and that her impression has gone stale.
        cup = _obj(self.root, "ceramic_cup_01")
        self.assertEqual(cup["parent_id"], "home_desk")
        self.assertEqual(cup["subject_last_seen_parent"], "home_desk")
        self.assertNotIn("impression_stale", cup)
        partner_cup = next(
            row for row in matter.read_matter_space_view(
                self.root, actor_side="partner", now_iso=_utc()
            )["objects"]
            if row["object_id"] == "ceramic_cup_01"
        )
        self.assertEqual(partner_cup["parent_id"], "home_staging_corner")
        self.assertEqual(partner_cup["subject_last_seen_parent"], "home_desk")
        self.assertTrue(partner_cup["impression_stale"])

    def test_04_day5_scan_observe_and_restore_empty_cup(self) -> None:
        matter.compose_open_act(
            self.root,
            {
                "intent_cn": "把两支笔放进空杯里",
                "steps": [
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                     "target_parent_id": "ceramic_cup_01"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02",
                     "target_parent_id": "ceramic_cup_01"},
                ],
            },
            now_iso=_utc(),
        )
        matter.partner_move_shared_object(
            self.root,
            {"object_id": "ceramic_cup_01", "target_parent_id": "home_staging_corner"},
            now_iso=_utc(),
        )

        # The subject reaches for the cup where she last saw it and honestly
        # misses; only then can the scan offer a "go look" cue (never before).
        with self.assertRaises(matter.V08MatterError) as miss_ctx:
            matter.compose_open_act(
                self.root,
                {"intent_cn": "想拿起桌上的杯子",
                 "steps": [{"op": "PLACE_OR_MOVE", "object_id": "ceramic_cup_01",
                            "target_parent_id": "home_desk"}]},
                now_iso=_utc(),
            )
        self.assertEqual(miss_ctx.exception.reason_code, "OBJECT_NOT_AT_EXPECTED_SPOT")

        candidates = matter.scan_matter_opportunities_zero_write(self.root, now_iso=_utc())
        target_refs = [c["target_ref"] for c in candidates]
        self.assertIn("v08_matter:observe:ceramic_cup_01", target_refs)

        observe_candidate = next(
            c for c in candidates if c["target_ref"] == "v08_matter:observe:ceramic_cup_01"
        )
        receipt = matter.compose_open_act(
            self.root,
            {
                "intent_cn": "看一眼杯子和笔现在在哪，再把笔收回桌上",
                "steps": [
                    {"op": "OBSERVE_OBJECT", "object_id": "ceramic_cup_01"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                     "target_parent_id": "home_desk"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02",
                     "target_parent_id": "home_desk"},
                ],
            },
            now_iso=_utc(),
        )
        self.assertEqual(receipt["llm_calls"], 0)
        self.assertEqual(receipt["status"], "applied")
        observed = receipt["step_results"][0]
        self.assertTrue(observed["moved_since_last_seen"])
        self.assertEqual(observed["observed_parent"], "home_staging_corner")
        self.assertNotIn("impression_stale", _obj(self.root, "ceramic_cup_01"))
        self.assertEqual(_obj(self.root, "ceramic_cup_01")["parent_id"], "home_staging_corner")
        self.assertEqual(_obj(self.root, "ceramic_cup_01")["derived_role_cn"], "空置可用的容器")
        self.assertIn("observe", observe_candidate["open_act_payload"]["steps"][0]["op"].lower())


class WiringHintsTest(unittest.TestCase):
    def test_preview_placement_registered_on_living_traces(self) -> None:
        hint = phone_tool._SURFACE_SUPPORTED_HINTS["v08_living_traces"]
        self.assertIn("preview_placement", hint["subject_actions"])
        self.assertEqual(
            hint["core_fields"]["preview_placement"],
            ["target_parent_id", "object_id", "label_cn", "item_kind"],
        )

    def test_lingering_thread_actions_registered_on_emergent_echoes(self) -> None:
        hint = phone_tool._SURFACE_SUPPORTED_HINTS["v08_emergent_echoes"]
        self.assertIn("note_lingering_thread", hint["subject_actions"])
        self.assertIn("resolve_lingering_thread", hint["subject_actions"])
        self.assertEqual(
            hint["core_fields"]["note_lingering_thread"],
            ["summary_cn", "scope_kind", "scope_ref", "ttl_minutes"],
        )
        self.assertEqual(
            hint["core_fields"]["resolve_lingering_thread"],
            ["thread_id", "resolution"],
        )
        self.assertIn("note_lingering_thread", phone_tool._V08_EMERGENT_ECHOES_OPKEY_SUBACTIONS)
        self.assertIn("resolve_lingering_thread",
                      phone_tool._V08_EMERGENT_ECHOES_CAPABILITY_SUBACTIONS)

    def test_hints_carry_no_hardcoded_identity(self) -> None:
        for surface in ("v08_living_traces", "v08_emergent_echoes"):
            blob = repr(phone_tool._SURFACE_SUPPORTED_HINTS[surface])
            self.assertNotIn("她", blob)
            self.assertNotIn("千代", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
