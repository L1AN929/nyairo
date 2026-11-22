#!/usr/bin/env python3
"""v0.8 Ch.44 日常环 / Ch.21 §3 微观诚实性：跨仓想念归零、私人角落小名、烫嘴热饮。

覆盖四件事：

1. 空目录 / 平静状态：日常扫描与 v0.8 手边线索都是严格零写盘、零字符静默；
2. 真实物件被消耗/丢弃后，原 ``matter:`` 跨仓想念与小忙由
   ``scan_emotional_tension_cues_zero_write`` 和日常扫描一起自动放下，
   不再伪造「想你」「帮个忙」候选；
3. 私人角落小名「雨棚」在 ``include_private=False``（伴侣默认上下文）下
   绝不泄露，显式公开分享后才以「也叫」出现；
4. 未过冷却期的热茶给出微阻滞，且不会被微意图伪造成「喝下了」的回执；
   整段 lived context 严格不超过 1800 字符。

Run: ``python -B -m unittest tests.test_v08_ch44_favor_and_burr_loop -v``.
"""

from __future__ import annotations

import hashlib
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
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.phone import emotional_tension_view as et  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

BJT = timezone(timedelta(hours=8))
T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


def _dt(minutes: int = 0, base: str = T0) -> datetime:
    return datetime.fromisoformat(base) + timedelta(minutes=minutes)


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _matter_doc(objects: dict[str, dict[str, Any]], **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"schema_version": 1, "matter_objects": objects}
    doc.update(extra)
    return doc


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
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


class Ch44FavorAndBurrLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch44-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _matter_path(self, root: Path | None = None) -> Path:
        return (root or self.root) / "world" / "v08_matter_space.json"

    def _state_bytes(self, root: Path | None = None) -> bytes:
        return et.emotional_tension_state_path(root or self.root).read_bytes()

    def _summary(self) -> dict[str, Any]:
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        self.assertIsInstance(collected, dict)
        return collected["gating"]["emotional_tension_summary"]

    # -- 1. calm / empty: zero write, zero-char micro line ----------------

    def test_01_calm_empty_is_zero_write_and_silent(self) -> None:
        empty_root = self.root / "empty-instance"
        (empty_root / "phone").mkdir(parents=True)
        before = _snapshot(empty_root)

        calm = et.scan_emotional_tension_cues_zero_write(empty_root, now=_dt())
        self.assertFalse(calm["in_low_mood"])
        self.assertEqual(calm["active_spaces_count"], 0)
        self.assertEqual(calm["open_ruptures_count"], 0)
        self.assertEqual(calm["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(calm["open_favor"])
        self.assertTrue(calm["read_only"])

        # A calm room contributes exactly nothing -- not a placeholder line.
        self.assertEqual(
            lcb._format_v08_living_traces_micro(
                {}, include_private=True, now_dt=_dt()
            ),
            "",
        )
        # The engine's default room is in-memory only; it may report an
        # ordinary desk scaffold, but never invents a scalding cup, a page mark
        # or a private corner name out of a calm, empty instance.
        default_room = lcb._read_matter_space_projection_zero_write(
            empty_root, include_private=True, now_iso=T0
        )
        self.assertIsInstance(default_room, dict)
        default_line = lcb._format_v08_living_traces_micro(
            {"matter_space": default_room}, include_private=True, now_dt=_dt()
        )
        self.assertNotIn("手边热饮还烫嘴", default_line)
        self.assertNotIn("书签在第", default_line)
        self.assertNotIn("[私人称呼", default_line)
        self.assertLessEqual(len(default_line), 160)

        collected = bridge.collect_v02_v03_loop_candidates(empty_root, now_iso=T0)
        summary = collected["gating"]["emotional_tension_summary"]
        self.assertEqual(summary["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(summary["open_favor"])
        self.assertTrue(summary["no_obligation_when_declined"])
        self.assertFalse(summary["manufactured_comfort_or_intimacy"])
        self.assertTrue(summary["read_only"])

        rendered = lcb.render_lived_companion_context(
            empty_root, {"phone": {"enabled": False}}, now_iso=T0
        )
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)
        # Nothing at all was created by either the scan, the render or the reads.
        self.assertEqual(_snapshot(empty_root), before)

    # -- 2. a consumed matter object releases cross-store missing/favor ---

    def test_02_consumed_matter_releases_missing_and_favor(self) -> None:
        _write_json(
            self._matter_path(),
            _matter_doc({
                "obj-miss-1": {
                    "object_id": "obj-miss-1",
                    "label_cn": "海边捡的贝壳",
                    "item_kind": "keepsake",
                    "parent_id": "bookshelf",
                },
                "obj-fav-1": {
                    "object_id": "obj-fav-1",
                    "label_cn": "未完成的画",
                    "item_kind": "paper",
                    "parent_id": "home_desk",
                },
            }),
        )

        note = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "FOUND",
                "source_ref": "matter:obj-miss-1",
                "summary": "捡到一个好看的贝壳",
            },
            now=_dt(),
            subject_view=True,
        )
        self.assertEqual(note["status"], "applied")
        favor = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "matter:obj-fav-1",
                "summary": "帮我看一眼这幅画",
            },
            now=_dt(),
            subject_view=True,
        )
        self.assertEqual(favor["status"], "applied")

        live = et.scan_emotional_tension_cues_zero_write(self.root, now=_dt(1))
        self.assertEqual(live["unsurfaced_missing_notes_count"], 1)
        self.assertIsNotNone(live["open_favor"])

        live_summary = self._summary()
        self.assertEqual(live_summary["unsurfaced_missing_notes_count"], 1)
        self.assertIsNotNone(live_summary["open_favor"])

        # The real objects are consumed/discarded in their owning repository.
        state_bytes = self._state_bytes()
        _write_json(
            self._matter_path(),
            _matter_doc({
                "obj-miss-1": {
                    "object_id": "obj-miss-1",
                    "label_cn": "海边捡的贝壳",
                    "item_kind": "keepsake",
                    "parent_id": "bookshelf",
                    "consumed": True,
                },
                "obj-fav-1": {
                    "object_id": "obj-fav-1",
                    "label_cn": "未完成的画",
                    "item_kind": "paper",
                    "parent_id": "home_desk",
                    "discarded": True,
                },
            }),
        )

        released = et.scan_emotional_tension_cues_zero_write(self.root, now=_dt(5))
        self.assertEqual(released["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(released["open_favor"])

        released_summary = self._summary()
        self.assertEqual(released_summary["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(released_summary["open_favor"])

        # No daily-loop candidate keeps riding a gone ``matter:`` source.
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        for desc in collected.get("descriptors") or []:
            ref = str(desc.get("source_ref") or "")
            self.assertFalse(ref.startswith("matter:"), ref)

        # Every scan/collect above was 100% read-only on the tension store.
        self.assertEqual(self._state_bytes(), state_bytes)

    # -- 3. private corner nickname stays private until shared -----------

    def _nickname_space(self, *, shared: bool) -> dict[str, Any]:
        return {
            "zones": [{"zone_id": "window_side", "label_cn": "窗边"}],
            "objects": [],
            "zone_nicknames": [
                {
                    "zone_id": "window_side",
                    "nickname_cn": "雨棚",
                    "shared_with_partner": shared,
                }
            ],
        }

    def test_03_private_corner_nickname_never_leaks(self) -> None:
        private_doc = {"matter_space": self._nickname_space(shared=False)}

        partner_line = lcb._format_v08_living_traces_micro(
            private_doc, include_private=False, now_dt=_dt()
        )
        self.assertNotIn("雨棚", partner_line)
        self.assertEqual(partner_line, "")

        owner_line = lcb._format_v08_living_traces_micro(
            private_doc, include_private=True, now_dt=_dt()
        )
        self.assertIn("雨棚", owner_line)
        self.assertIn("[私人称呼·伴侣未懂]", owner_line)

        shared_doc = {"matter_space": self._nickname_space(shared=True)}
        shared_partner_line = lcb._format_v08_living_traces_micro(
            shared_doc, include_private=False, now_dt=_dt()
        )
        self.assertIn("雨棚", shared_partner_line)
        self.assertIn("也叫", shared_partner_line)

        # The real engine projection filters the row by construction, so the
        # disk-read fallback can never smuggle it into a partner context.
        _write_json(
            self._matter_path(),
            _matter_doc(
                {},
                zone_nicknames={
                    "window_side": {
                        "zone_id": "window_side",
                        "nickname_cn": "雨棚",
                        "shared_with_partner": False,
                    }
                },
            ),
        )
        partner_view = lcb._read_matter_space_projection_zero_write(
            self.root, include_private=False, now_iso=T0
        )
        self.assertIsNotNone(partner_view)
        self.assertNotIn("雨棚", json.dumps(partner_view, ensure_ascii=False))

        owner_view = lcb._read_matter_space_projection_zero_write(
            self.root, include_private=True, now_iso=T0
        )
        self.assertIsNotNone(owner_view)
        self.assertIn("雨棚", json.dumps(owner_view, ensure_ascii=False))
        owner_projected_line = lcb._format_v08_living_traces_micro(
            {"matter_space": owner_view}, include_private=True, now_dt=_dt()
        )
        self.assertIn("雨棚", owner_projected_line)

    # -- 4. scalding tea: honest hold, never a fake sip receipt -----------

    def _write_scalding_tea(self) -> None:
        _write_json(
            self._matter_path(),
            _matter_doc({
                "tea-1": {
                    "object_id": "tea-1",
                    "label_cn": "刚沏的热茶",
                    "item_kind": "drink",
                    "parent_id": "dining_table",
                    "portions_left": 2,
                    "portions_total": 2,
                    "thermal": "hot",
                    "scalding_until_iso": _at(5),
                    "scalding_wait_minutes": 5,
                }
            }),
        )

    def _tea_portions(self, now: str) -> int:
        view = matter.read_matter_space_view(
            self.root, actor_side="subject", now_iso=now
        )
        for row in view["objects"]:
            if row["object_id"] == "tea-1":
                return int(row["portions_left"])
        raise AssertionError("tea-1 missing from the projection")

    def test_04_scalding_tea_holds_without_fake_receipt(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        self._write_scalding_tea()

        # The micro context names the honest wait while the deadline is live.
        subject_view = matter.read_matter_space_view(
            self.root, actor_side="subject", now_iso=T0
        )
        scalding_line = lcb._format_v08_living_traces_micro(
            {"matter_space": subject_view},
            include_private=True,
            now_dt=_dt(),
        )
        self.assertIn("手边热饮还烫嘴", scalding_line)

        rendered = lcb.render_lived_companion_context(
            self.root, {"phone": {"enabled": False}}, now_iso=T0
        )
        self.assertIn("手边热饮还烫嘴", rendered)
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertLessEqual(len(rendered), 1800)

        state_before = self._matter_path().read_bytes()
        held = bridge.execute_normalized_micro_intent(
            self.root,
            {
                "intent_cn": "喝一口",
                "steps": [{"op": "CONSUME_PORTION", "object_id": "tea-1"}],
            },
            now_iso=T0,
        )
        self.assertFalse(held["ok"])
        self.assertTrue(held["soft_landed"])
        self.assertEqual(held["status"], "SOFT_LANDED")
        self.assertEqual(held["reason_code"], "DRINK_SCALDING_WAIT")
        self.assertTrue(held["gentle_hold"])
        self.assertFalse(held["drunk"])
        self.assertFalse(held["thirst_quenched"])
        self.assertNotIn("receipt", held)
        # A refusal is not a write and never a phantom sip.
        self.assertEqual(self._tea_portions(T0), 2)
        self.assertEqual(self._matter_path().read_bytes(), state_before)

        # Once the real clock passes the deadline the very same act applies.
        receipt = bridge.execute_normalized_micro_intent(
            self.root,
            {
                "intent_cn": "喝一口",
                "steps": [{"op": "CONSUME_PORTION", "object_id": "tea-1"}],
            },
            now_iso=_at(6),
        )
        self.assertTrue(receipt["ok"])
        self.assertNotEqual(receipt.get("status"), "SOFT_LANDED")
        self.assertEqual(self._tea_portions(_at(6)), 1)

        # After cooling, no scalding cue is rendered any more.
        cooled_view = matter.read_matter_space_view(
            self.root, actor_side="subject", now_iso=_at(6)
        )
        cooled_line = lcb._format_v08_living_traces_micro(
            {"matter_space": cooled_view},
            include_private=True,
            now_dt=_dt(6),
        )
        self.assertNotIn("手边热饮还烫嘴", cooled_line)


if __name__ == "__main__":
    unittest.main()
