#!/usr/bin/env python3
"""v0.8 Wave J3: Ch.26 shopkeeper salience & Ch.53 fictional scope.

Covers the daily-loop and lived-context seams added for:

* Ch.26 living shopkeeper -- the counter only ever surfaces *non-default*
  salience: an unverified secondhand rumor is rendered source-attributed as
  hearsay (``待实地核实``) and never claimed as a confirmed roadblock, and a
  recently resolved card-spot suggestion renders ``已摆`` / ``店主另挪``.  A
  quiet ``current_task`` rotation emits nothing, so calm rooms keep the Wave 8
  zero-char silence gate.
* Ch.53 §二 fictional-frame isolation -- an active ``fictional_role_cn`` in
  ``temporary_game`` renders a concise ``不改真实屋子`` boundary inside the
  1800-char budget.
* The optional ``v04_bookstore_shopkeeper:*`` daily-loop candidate appears only
  when the bookstore is open and the counter has real news (unverified rumor or
  a pending/resolved card-spot suggestion), is absent when quiet or closed,
  settles through ``bookstore_service``, and every read stays 100% zero-write.

Run (native Python)::

    python -B -m unittest tests.test_v08_ch26_ch53_loop_and_context -v
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

# Bind the shared transaction guard before the daily-loop bridge is imported.  On
# native Windows the bridge installs a no-op ``fcntl`` shim into ``sys.modules``;
# if ``state_tx_guard`` imports that shim first its OS-level lock silently
# degrades to a no-op and unrelated concurrent writers can double-apply.  This
# import makes the guard pick its real ``msvcrt`` fallback first.
from components.life.scripts import state_tx_guard as _state_tx_guard  # noqa: E402,F401

from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
)

T_OPEN = "2026-11-16T11:00:00+08:00"
T_CLOSED = "2026-11-16T23:00:00+08:00"
ROUTE = "street_to_riverside"
SHOP_PREFIX = "v04_bookstore_shopkeeper:"


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


def _default_shopkeeper_life() -> dict[str, Any]:
    """Mirror of the engine's quiet default: current_task only, zero counter news."""
    return {
        "shopkeeper_name": "旧书店主",
        "current_task": "shelve_returned_books",
        "current_task_cn": "把还回来的书按字母上架",
        "current_task_started_at": "2026-11-16T10:00:00+08:00",
        "ambient_changes": ["擦了擦窗台"],
        "privilege_boundary": "public_counter_only",
        "private_room_knowledge": False,
        "remembered_interactions": [],
        "card_spot_suggestions": [],
        "heard_rumors": [],
    }


def _nb_with_life(life: dict[str, Any]) -> dict[str, Any]:
    return {"public_life": {"bookstore": {"shopkeeper_life": life}}}


class FormatterSalienceTest(unittest.TestCase):
    """Direct ``_format_v04_neighborhood_and_elastic`` behaviour."""

    def test_01_calm_default_shopkeeper_is_fully_silent(self) -> None:
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=_nb_with_life(_default_shopkeeper_life()),
            v04_ep=None,
        )
        self.assertIsNone(out, "a quiet default shopkeeper rotation must emit 0 chars")

    def test_02_unverified_rumor_is_attributed_hearsay_not_roadblock(self) -> None:
        life = _default_shopkeeper_life()
        life["heard_rumors"] = [
            {
                "rumor_id": "rumor_1",
                "source_name": "旧书店主",
                "route_id": ROUTE,
                "claim_cn": "河边那条路在挖沟，最近不太好走",
                "heard_at": "2026-11-16T10:30:00+08:00",
                "verified_firsthand": False,
            }
        ]
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=_nb_with_life(life), v04_ep=None
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("听说:旧书店主「", out)
        self.assertIn("待实地核实", out)
        # Task line is only a side note to real counter news.
        self.assertIn("店主:", out)
        for banned in ("封路", "已确认", "已核实", "路障", "无法通过"):
            self.assertNotIn(banned, out)

        # Once verified firsthand it is no longer hearsay -> calm again.
        life["heard_rumors"][0]["verified_firsthand"] = True
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(
                v04_nb=_nb_with_life(life), v04_ep=None
            )
        )

    def test_03_resolved_card_spot_surfaces_status_cn(self) -> None:
        now = bridge._parse_iso("2026-11-16T11:00:00+08:00")
        life = _default_shopkeeper_life()
        life["card_spot_suggestions"] = [
            {
                "suggestion_id": "sug_adopt",
                "card_id": "画册荐书签",
                "status": "adopted",
                "resolved_at": "2026-11-16T10:10:00+08:00",
            }
        ]
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=_nb_with_life(life), v04_ep=None, now_dt=now
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("书店卡片:画册荐书签(已摆)", out)

        life["card_spot_suggestions"][0]["status"] = "relocated"
        out2 = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=_nb_with_life(life), v04_ep=None, now_dt=now
        )
        assert out2 is not None
        self.assertIn("书店卡片:画册荐书签(店主另挪)", out2)

        # Pending review is not resolved news -> stays silent.
        life["card_spot_suggestions"][0]["status"] = "pending_shopkeeper_review"
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(
                v04_nb=_nb_with_life(life), v04_ep=None, now_dt=now
            )
        )

    def test_04_fantasy_role_marks_physical_boundary(self) -> None:
        ep = {
            "temporary_game": {
                "active": True,
                "prop_label": "搪瓷杯",
                "fictional_role_cn": "桌面飞船",
                "fantasy_boundary_note_cn": "假想设定只在这次十分钟小游戏里有效，屋里的真物件保持原样",
            }
        }
        out = bridge._format_v04_neighborhood_and_elastic(v04_nb=None, v04_ep=ep)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("小游戏假想:搪瓷杯暂作「桌面飞船」", out)
        self.assertIn("不改真实屋子", out)
        self.assertLessEqual(len(out), bridge.MAX_LIVED_CONTEXT_CHARS)

        # Inactive game => silence.
        ep["temporary_game"]["active"] = False
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(v04_nb=None, v04_ep=ep)
        )

    def test_05_combined_news_stays_within_budget(self) -> None:
        life = _default_shopkeeper_life()
        life["heard_rumors"] = [
            {
                "rumor_id": "rumor_1",
                "source_name": "旧书店主",
                "claim_cn": "河边那条路在挖沟，最近不太好走",
                "verified_firsthand": False,
            }
        ]
        life["card_spot_suggestions"] = [
            {
                "suggestion_id": "sug_1",
                "card_id": "画册荐书签",
                "status": "adopted",
                "resolved_at": "2026-11-16T10:10:00+08:00",
            }
        ]
        ep = {
            "temporary_game": {
                "active": True,
                "prop_label": "搪瓷杯",
                "fictional_role_cn": "桌面飞船",
            }
        }
        out = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=_nb_with_life(life),
            v04_ep=ep,
            now_dt=bridge._parse_iso("2026-11-16T11:00:00+08:00"),
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertLessEqual(len(out), bridge.MAX_LIVED_CONTEXT_CHARS)


class ContextBridgeCalmSilenceTest(unittest.TestCase):
    """The full turn-context builder keeps the calm-silence gate."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-j3-calm-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "phone").mkdir(parents=True)
        (self.root / "world").mkdir(parents=True)
        (self.root / "world" / "v04_neighborhood_state.json").write_text(
            json.dumps(
                {"public_life": {"bookstore": {"shopkeeper_life": _default_shopkeeper_life()}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_01_default_shopkeeper_keeps_context_at_zero_chars(self) -> None:
        # The formatter itself emits nothing for a quiet default rotation.
        self.assertIsNone(
            bridge._format_v04_neighborhood_and_elastic(
                v04_nb={
                    "public_life": {
                        "bookstore": {"shopkeeper_life": _default_shopkeeper_life()}
                    }
                },
                v04_ep=None,
                now_dt=bridge._parse_iso(T_OPEN),
            )
        )

        before = _snap(self.root)
        diag = bridge.measure_context_budget_breakdown(
            self.root, _permitted_cfg(self.root), now_iso=T_OPEN
        )
        self.assertEqual(_snap(self.root), before, "context read must be zero-write")
        # Only the always-on identity/boundary header remains; the v0.4 line is 0 chars.
        self.assertEqual(diag["per_section_chars"].get("v04_line", 0), 0)
        self.assertNotIn("店主", diag["rendered_text"])
        self.assertNotIn("街区与弹性玩心", diag["rendered_text"])

        rendered = bridge.build_lived_turn_context(
            self.root, _permitted_cfg(self.root), now_iso=T_OPEN
        )
        self.assertNotIn("店主", rendered)
        self.assertNotIn("待实地核实", rendered)
        self.assertEqual(_snap(self.root), before)


class DailyLoopShopkeeperCandidateTest(unittest.TestCase):
    """Optional ``v04_bookstore_shopkeeper:*`` daily-loop candidate."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-j3-loop-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # ---------------------------------------------------------------- helpers
    def _mutate(self, payload: dict[str, Any], *, now_iso: str = T_OPEN):
        return mutate_v04_neighborhood_view(
            self.root, payload, actor_side="subject", now_iso=now_iso
        )

    def _seed_rumor(self, *, now_iso: str = T_OPEN) -> str:
        code, body = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": ROUTE,
            },
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        return str(body["result"]["rumor_id"])

    def _make_inscribed_card(self, card_id: str = "card_spot") -> None:
        code, _ = self._mutate(
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": card_id}
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": card_id,
                "title": "画册荐书签",
                "text": "第三章关于石阶青苔的配图很耐看。",
                "draft_text": "草稿不要贴出去",
            }
        )
        self.assertEqual(code, 200)

    def _shop_descriptors(self, collected: dict[str, Any]) -> list[dict[str, Any]]:
        descriptors = collected.get("descriptors") or {}
        return [
            dict(desc)
            for desc in descriptors.values()
            if isinstance(desc, dict)
            and str(desc.get("source_ref") or "").startswith(SHOP_PREFIX)
        ]

    # ------------------------------------------------------------------ tests
    def test_01_open_counter_with_rumor_emits_one_settleable_candidate(self) -> None:
        rumor_id = self._seed_rumor()
        before = _snap(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_OPEN)
        self.assertEqual(_snap(self.root), before, "collect must be zero-write")
        self.assertEqual(collected["llm_calls"], 0)
        self.assertTrue(collected["zero_write"])

        shop = self._shop_descriptors(collected)
        self.assertEqual(len(shop), 1)
        desc = shop[0]
        self.assertEqual(desc["source_ref"], f"{SHOP_PREFIX}{rumor_id}")
        self.assertEqual(desc["v04_action"], "bookstore_service")
        self.assertEqual(desc["v04_payload"]["service"], "browse_book")
        self.assertTrue(desc["requires_outing"])

        event = loop_bridge.v04_round_event_for_selected_candidate(
            collected, desc["source_ref"]
        )
        self.assertIsNotNone(event)
        assert event is not None
        command = loop_bridge._v04_build_settlement_command(
            self.root, event, now_dt=bridge._parse_iso(T_OPEN)
        )
        self.assertIsNotNone(command)
        assert command is not None
        action, payload = command
        self.assertEqual(action, "bookstore_service")
        self.assertEqual(payload["service"], "browse_book")

        code, body = mutate_v04_neighborhood_view(
            self.root,
            dict(payload, action=action),
            actor_side="subject",
            now_iso=T_OPEN,
        )
        self.assertEqual(code, 200, body)

    def test_02_pending_card_suggestion_also_qualifies(self) -> None:
        self._make_inscribed_card()
        code, body = self._mutate(
            {
                "action": "bookstore_service",
                "service": "suggest_card_spot",
                "card_id": "card_spot",
                "spot_note": "靠窗第二格左边",
            }
        )
        self.assertEqual(code, 200, body)
        suggestion_id = str(body["result"]["suggestion_id"])

        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_OPEN)
        shop = self._shop_descriptors(collected)
        self.assertEqual(len(shop), 1)
        self.assertEqual(shop[0]["source_ref"], f"{SHOP_PREFIX}{suggestion_id}")

    def test_03_quiet_counter_and_closed_store_emit_nothing(self) -> None:
        # Quiet but initialized counter: no rumor, no suggestion.
        code, _ = self._mutate(
            {
                "action": "bookstore_service",
                "service": "shop_ambient_shift",
                "change_note": "把柜台边的书抹了抹灰",
            }
        )
        self.assertEqual(code, 200)
        quiet = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_OPEN)
        self.assertEqual(self._shop_descriptors(quiet), [])

        # Real news but the bookstore is closed: no candidate either.
        self._seed_rumor(now_iso=T_OPEN)
        closed = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_CLOSED)
        self.assertEqual(self._shop_descriptors(closed), [])

    def test_04_absent_state_stays_zero_write_and_zero_candidate(self) -> None:
        before = _snap(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_OPEN)
        self.assertEqual(_snap(self.root), before)
        self.assertEqual(self._shop_descriptors(collected), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
