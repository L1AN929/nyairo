#!/usr/bin/env python3
"""v0.8 Ch.41: plant care seam in the daily loop & lived-context silence gate.

Covers the two functional seams added for ``41-天气季节与小尺度生态`` §五:

* the daily loop only offers a gentle plant-rescue candidate when the porch
  plant is actually ``wilting_recoverable`` (same ``water_or_move_plant``
  protocol, calmer title); a healthy, watered plant produces no forced-water
  candidate and the calm default keeps zero daily quota;
* the lived-context neighbourhood line stays at **0 chars** whenever the
  greens are calm, but surfaces one compact, honest note for a wilting plant,
  a new-leaf milestone, or winter dormancy, and only mentions the street-corner
  market when it is genuinely active or when real physical traces were actually
  seen at ``street_corner`` (never a fabricated shared memory);
* the whole rendered context stays strictly ``<= 1800`` chars under load and
  every read is 100% zero-write.

Run (native Python on Windows)::

    python -B -m unittest tests.test_v08_ch41_loop_and_context -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
for _p in (str(ROOT), str(ROOT / "components"), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402

# Bind the shared transaction guard before the daily-loop bridge is imported.
# On native Windows the bridge installs a no-op ``fcntl`` shim into
# ``sys.modules``; if ``state_tx_guard`` imports that shim first, its OS-level
# lock silently degrades to a no-op.  This import makes the guard pick its real
# ``msvcrt`` fallback first (same rationale as the Ch.26 sister test).
from components.life.scripts import state_tx_guard as _state_tx_guard  # noqa: E402,F401

from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

T_WILT_BASE = "2026-09-01T10:00:00+08:00"
T_WILTED = "2026-09-16T10:00:00+08:00"
T_LEAF_BASE = "2026-09-01T08:00:00+08:00"
T_LEAF_AFTER = "2026-09-02T04:00:00+08:00"
T_FEST_BASE = "2026-11-12T09:00:00+08:00"
T_MISSED = "2026-11-13T10:07:00+08:00"
PLANT_PREFIX = "v04_porch_plant:"
WILTING_TITLE = "门廊那盆常春藤叶尖发蔫了，可以浇点水让它缓过来"


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            out[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            out[rel] = (0, int(stat.st_mtime_ns), "dir")
    return out


def _permitted_cfg(root: Path) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


def _calm_plant() -> dict[str, Any]:
    return {
        "plant_id": "porch_fern",
        "name": "门廊常春藤",
        "place_id": "porch",
        "hydration": 70.0,
        "growth_points": 10.0,
        "stage": "seedling",
        "seasonal_state": "active_growth",
        "new_leaf_note_cn": "",
        "leaf_count": 3,
    }


def _calm_nb() -> dict[str, Any]:
    return {
        "environment": {
            "weather": "clear",
            "season": "autumn",
            "ground_wetness": "dry",
            "airflow": "calm",
        },
        "potted_plant": _calm_plant(),
        "journey": {"state": "idle", "visited_place_ids": []},
        "public_life": {
            "seasonal_festival": {
                "phase": "quiet",
                "attended_firsthand": False,
                "residual_traces": [],
            }
        },
    }


class CalmSilenceTest(unittest.TestCase):
    """A calm green plant and a quiet street keep the v0.4 line at 0 chars."""

    def test_01_calm_default_formatter_is_silent(self) -> None:
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(
                v04_nb=_calm_nb(),
                v04_ep=None,
                now_dt=bridge._parse_iso("2026-11-16T11:00:00+08:00"),
            )
        )

    def test_02_full_context_keeps_zero_chars_and_zero_write(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-ch41-calm-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir(parents=True)
        (root / "world").mkdir(parents=True)
        (root / "world" / "v04_neighborhood_state.json").write_text(
            json.dumps(_calm_nb(), ensure_ascii=False),
            encoding="utf-8",
        )

        before = _snap(root)
        diag = bridge.measure_context_budget_breakdown(
            root, _permitted_cfg(root), now_iso="2026-11-16T11:00:00+08:00"
        )
        self.assertEqual(_snap(root), before, "context read must be zero-write")
        self.assertEqual(diag["per_section_chars"].get("v04_line", 0), 0)
        rendered = diag["rendered_text"]
        for banned in ("盆栽", "季候小集市", "街区与弹性玩心"):
            self.assertNotIn(banned, rendered)

        rendered2 = bridge.build_lived_turn_context(
            root, _permitted_cfg(root), now_iso="2026-11-16T11:00:00+08:00"
        )
        self.assertNotIn("盆栽", rendered2)
        self.assertEqual(_snap(root), before)


class PlantLoopAndContextTest(unittest.TestCase):
    """Wilting -> gentle rescue candidate; watering recovers; new leaf surfaces."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-ch41-plant-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "phone").mkdir()

    # ---------------------------------------------------------------- helpers
    def _mutate(self, payload: dict[str, Any], *, now_iso: str):
        return mutate_v04_neighborhood_view(
            self.root, payload, actor_side="subject", now_iso=now_iso
        )

    def _init_clear_autumn(self, *, now_iso: str) -> None:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)

    def _collect(self, now_iso: str) -> dict[str, Any]:
        return loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    def _plant_descriptor(self, collected: dict[str, Any]) -> dict[str, Any]:
        for desc in (collected.get("descriptors") or {}).values():
            if str(desc.get("source_ref") or "").startswith(PLANT_PREFIX):
                return dict(desc)
        raise AssertionError("missing household plant candidate")

    @staticmethod
    def _event_for(desc: dict[str, Any], *, tag: str) -> dict[str, Any]:
        return {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": desc.get("v04_action_kind"),
            "v04_action": desc["v04_action"],
            "v04_payload": dict(desc.get("v04_payload") or {}),
            "source_ref": desc["source_ref"],
            "receipt_ref": f"test:{tag}",
            "operation_key": f"test_{tag}",
        }

    # ------------------------------------------------------------------ tests
    def test_01_wilting_plant_gets_gentle_water_candidate_then_recovers(self) -> None:
        self._init_clear_autumn(now_iso=T_WILT_BASE)
        code, body = self._mutate(
            {
                "action": "water_or_move_plant",
                "operation": "move",
                "target_place": "yard",
            },
            now_iso=T_WILT_BASE,
        )
        self.assertEqual(code, 200, body)

        engine.advance_v04_neighborhood_processes(self.root, now_iso=T_WILTED)
        _, wilted = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_WILTED
        )
        self.assertEqual(wilted["potted_plant"]["stage"], "wilting_recoverable")

        before = _snap(self.root)
        collected = self._collect(T_WILTED)
        self.assertEqual(_snap(self.root), before, "collect must be zero-write")
        desc = self._plant_descriptor(collected)
        self.assertEqual(desc["title"], WILTING_TITLE)
        self.assertEqual(desc["v04_action"], "water_or_move_plant")
        self.assertEqual(desc["v04_payload"]["operation"], "water")
        self.assertFalse(desc["requires_outing"])

        hydrated_before = float(wilted["potted_plant"]["hydration"])
        settled = loop_bridge.settle_v02_v03_round_consequences(
            self.root, self._event_for(desc, tag="water"), now_iso=T_WILTED
        )
        self.assertEqual(settled["status"], "APPLIED", settled)

        _, recovered = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_WILTED
        )
        plant = recovered["potted_plant"]
        self.assertGreater(float(plant["hydration"]), hydrated_before)
        self.assertNotEqual(plant["stage"], "wilting_recoverable")
        self.assertGreaterEqual(int(plant["wilt_recovered_count"]), 1)

        # Watered again: no forced-water candidate remains in the same window.
        after = self._collect(T_WILTED)
        remaining = [
            desc2
            for desc2 in (after.get("descriptors") or {}).values()
            if str(desc2.get("source_ref") or "").startswith(PLANT_PREFIX)
        ]
        self.assertEqual(remaining, [])

    def test_02_healthy_watered_plant_emits_no_forced_candidate(self) -> None:
        self._init_clear_autumn(now_iso=T_LEAF_BASE)
        collected = self._collect(T_LEAF_BASE)
        self.assertEqual(
            [
                desc
                for desc in (collected.get("descriptors") or {}).values()
                if str(desc.get("source_ref") or "").startswith(PLANT_PREFIX)
            ],
            [],
            "a calm, hydrated plant must not force a watering cue",
        )

    def test_03_new_leaf_enters_compact_context(self) -> None:
        self._init_clear_autumn(now_iso=T_LEAF_BASE)
        _, calm_view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_LEAF_BASE
        )
        calm_out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=calm_view, v04_ep=None, now_dt=bridge._parse_iso(T_LEAF_BASE)
        )
        self.assertNotIn("盆栽", calm_out or "", "calm greens must stay silent")

        engine.advance_v04_neighborhood_processes(self.root, now_iso=T_LEAF_AFTER)
        _, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_LEAF_AFTER
        )
        plant = view["potted_plant"]
        self.assertTrue(plant["new_leaf_note_cn"])
        self.assertGreater(plant["leaf_count"], engine.PLANT_LEAF_BASE_COUNT)

        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=view, v04_ep=None, now_dt=bridge._parse_iso(T_LEAF_AFTER)
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("盆栽刚抽新叶(共", out)
        self.assertIn(f"{plant['leaf_count']}片)", out)
        self.assertLessEqual(len(out), bridge.MAX_LIVED_CONTEXT_CHARS)


class MissedMarketContextTest(unittest.TestCase):
    """A missed market is only real traces seen at the corner -- never shared."""

    @staticmethod
    def _festival_nb(
        *, phase: str, attended: bool, visited: list[str]
    ) -> dict[str, Any]:
        return {
            "environment": {"weather": "clear", "season": "autumn"},
            "potted_plant": _calm_plant(),
            "journey": {"state": "arrived", "visited_place_ids": visited},
            "public_life": {
                "seasonal_festival": {
                    "phase": phase,
                    "attended_firsthand": attended,
                    "festival_name_cn": "秋晒叶市",
                    "residual_traces": [
                        "街角檐下还没撤下的纸灯笼串",
                        "巷口集市撤场后的旧木分类回收箱",
                    ],
                }
            },
        }

    def test_01_residual_without_visit_is_silent(self) -> None:
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(
                v04_nb=self._festival_nb(
                    phase="residual_traces", attended=False, visited=[]
                ),
                v04_ep=None,
            )
        )

    def test_02_residual_after_visiting_corner_is_honest_traces(self) -> None:
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=self._festival_nb(
                phase="residual_traces", attended=False, visited=["street_corner"]
            ),
            v04_ep=None,
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("街角集市刚散", out)
        self.assertIn("纸灯笼串", out)
        self.assertIn("回收箱", out)
        self.assertIn("(未亲历)", out)
        self.assertNotIn("已亲历", out)
        for fabricated in ("一起逛", "我们一起", "共同参加", "我们赶上"):
            self.assertNotIn(fabricated, out)

    def test_03_firsthand_residual_and_active_market(self) -> None:
        seen = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=self._festival_nb(
                phase="residual_traces", attended=True, visited=["street_corner"]
            ),
            v04_ep=None,
        )
        assert seen is not None
        self.assertIn("(已亲历)", seen)
        self.assertNotIn("未亲历", seen)

        active = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=self._festival_nb(phase="active", attended=False, visited=[]),
            v04_ep=None,
        )
        assert active is not None
        self.assertIn("街角今日有季候小集市", active)

    def test_04_real_engine_missed_market_shows_traces_only_after_corner(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-ch41-market-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        code, body = mutate_v04_neighborhood_view(
            root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso=T_FEST_BASE,
        )
        self.assertEqual(code, 200, body)

        # Stand at the corner the day after the market; only traces remain.
        code, _ = mutate_v04_neighborhood_view(
            root,
            {"action": "start_journey", "target_place": "street_corner"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        code, _ = mutate_v04_neighborhood_view(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso="2026-11-13T10:06:00+08:00",
        )
        self.assertEqual(code, 200)

        _, view = read_v04_neighborhood_view(
            root, has_subject_capability=True, now_iso=T_MISSED
        )
        festival = view["public_life"]["seasonal_festival"]
        self.assertEqual(festival["phase"], "residual_traces")
        self.assertFalse(festival["attended_firsthand"])
        self.assertIn("street_corner", view["journey"]["visited_place_ids"])

        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=view, v04_ep=None, now_dt=bridge._parse_iso(T_MISSED)
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("街角集市刚散", out)
        self.assertIn("(未亲历)", out)
        for fabricated in ("一起逛", "共同参加", "我们赶上"):
            self.assertNotIn(fabricated, out)

        state, exists = engine.load_state_readonly(root)
        self.assertTrue(exists)
        self.assertEqual(state["public_life"]["attended_festivals"], [])


class HighLoadBudgetTest(unittest.TestCase):
    """Under a packed neighborhood + elastic-play load the context stays <= 1800."""

    @staticmethod
    def _rich_nb() -> dict[str, Any]:
        plant = _calm_plant()
        plant.update(
            {
                "hydration": 8.0,
                "stage": "wilting_recoverable",
                "seasonal_state": "resting_dry",
                "wilt_recovered_count": 1,
            }
        )
        return {
            "environment": {
                "weather": "rain",
                "season": "autumn",
                "ground_wetness": "wet",
                "wind_level": "strong",
                "temperature_feel": "chilly",
                "power_water_notice": "楼道今晚检修水管，水压偏低",
            },
            "potted_plant": plant,
            "journey": {
                "state": "in_transit",
                "target_place_id": "street_corner",
                "visited_place_ids": ["street_corner"],
            },
            "courtyard": {
                "drying_rack": [
                    {"label": "旧棉布巾", "state": "damp"},
                ]
            },
            "public_notices": [
                {"title": "周末旧书交换角挪到檐下"},
                {"title": "巷口路灯这两日傍晚晚些亮"},
            ],
            "shared_shelf": [
                {"label": "藤编针线盒", "status": "borrowed"},
                {"label": "黄铜书签", "status": "on_shelf"},
            ],
            "public_life": {
                "seasonal_festival": {
                    "phase": "residual_traces",
                    "attended_firsthand": False,
                    "residual_traces": [
                        "街角檐下还没撤下的纸灯笼串",
                        "巷口集市撤场后的旧木分类回收箱",
                    ],
                },
                "bookstore": {
                    "shopkeeper_life": {
                        "current_task_cn": "把还回来的书按字母上架",
                        "heard_rumors": [
                            {
                                "source_name": "旧书店主",
                                "claim_cn": "河边那条路在挖沟，最近不太好走",
                                "verified_firsthand": False,
                            }
                        ],
                        "card_spot_suggestions": [
                            {
                                "card_id": "画册荐书签",
                                "status": "adopted",
                                "resolved_at": "2026-11-16T10:10:00+08:00",
                            }
                        ],
                    }
                },
            },
        }

    @staticmethod
    def _rich_ep() -> dict[str, Any]:
        return {
            "temporary_game": {
                "active": True,
                "prop_label": "搪瓷杯",
                "fictional_role_cn": "桌面飞船",
            },
            "day_direction": {"status": "active", "direction": "把窗台和书桌慢慢理顺"},
            "shared_notebook": {"entries": [{"text": "明天想试试新的便当配菜"}]},
            "scoped_preferences": [
                {"scope": "雨天", "preference": "说话慢一点"},
                {"scope": "创作", "preference": "先给草稿不要催"},
            ],
            "experiments": [{"title": "睡前十分钟关灯聊天", "status": "active"}],
            "prepared_surprises": [{"status": "offered"}],
        }

    def test_01_formatter_under_load_stays_within_budget(self) -> None:
        now = bridge._parse_iso("2026-11-16T11:00:00+08:00")
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=self._rich_nb(), v04_ep=self._rich_ep(), now_dt=now
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertLessEqual(len(out), bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("门廊盆栽偏干发蔫(浇水能缓)", out)
        self.assertIn("街角集市刚散", out)

    def test_02_full_render_under_load_stays_within_budget(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-ch41-load-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir(parents=True)
        (root / "world").mkdir(parents=True)
        (root / "world" / "v04_neighborhood_state.json").write_text(
            json.dumps(self._rich_nb(), ensure_ascii=False), encoding="utf-8"
        )
        (root / "phone" / "v04_elastic_play.json").write_text(
            json.dumps(self._rich_ep(), ensure_ascii=False), encoding="utf-8"
        )

        before = _snap(root)
        diag = bridge.measure_context_budget_breakdown(
            root, _permitted_cfg(root), now_iso="2026-11-16T11:00:00+08:00"
        )
        self.assertEqual(_snap(root), before, "context read must be zero-write")
        self.assertTrue(diag["within_budget"], diag["total_chars"])
        self.assertLessEqual(diag["total_chars"], bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertLessEqual(len(diag["rendered_text"]), bridge.MAX_LIVED_CONTEXT_CHARS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
