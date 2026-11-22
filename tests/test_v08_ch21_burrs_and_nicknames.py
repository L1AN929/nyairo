#!/usr/bin/env python3
"""Ch.21 §3 small honestities: a scalding cup, a private corner name, a page mark.

Coverage:
* a just-poured drink with ``scalding_minutes=5`` refuses to be drunk at once
  with a 409 ``DRINK_SCALDING_WAIT`` and unchanged portions; after the real
  clock passes the deadline the very same act succeeds;
* an unmarked soup/rice introduction consumes immediately, byte-identical to
  the old behaviour (no scalding fields ever appear);
* ``name_personal_corner`` keeps a private corner nickname wholly absent from
  the partner projection (no hidden count), refuses the partner door with 403,
  rejects unknown fields with 400, and shows the name once explicitly shared;
* a ``bookmark_page=37`` / ``revisit_offset=-2`` mark never claims the reader
  understood the chapter, and every read stays 100% zero-write.

Run: ``python -B -m unittest tests.test_v08_ch21_burrs_and_nicknames -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

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

from components.world.scripts import v08_universal_matter as matter  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


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


class Ch21BurrsAndNicknamesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch21-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _act(self, payload: dict, *, now: str = T0) -> dict:
        return matter.compose_open_act(self.root, payload, now_iso=now)

    def _view(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return matter.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _obj(self, object_id: str, *, actor_side: str = "subject", now: str = T0) -> dict:
        for row in self._view(actor_side=actor_side, now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _introduce(self, object_id: str, label: str, kind: str, target: str, **extra) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return self._act({"intent_cn": f"放入{label}", "steps": [step]})

    def _consume(self, object_id: str, *, now: str = T0) -> dict:
        return self._act(
            {"intent_cn": "喝一口", "steps": [{"op": "CONSUME_PORTION", "object_id": object_id}]},
            now=now,
        )

    # -- 1. scalding -----------------------------------------------------

    def test_scalding_tea_waits_real_minutes_then_drinks(self) -> None:
        self._introduce(
            "hot_tea_01", "刚沏的热茶", "drink", "dining_table",
            portions=2, thermal="hot", scalding_minutes=5,
        )
        tea = self._obj("hot_tea_01")
        self.assertTrue(tea["scalding"])
        self.assertEqual(tea["scalding_wait_minutes"], 5)
        self.assertEqual(tea["scalding_until_iso"], "2026-11-16T09:05:00+08:00")

        before = _snapshot(self.root)
        with self.assertRaises(matter.V08MatterError) as ctx:
            self._consume("hot_tea_01")
        self.assertEqual(ctx.exception.reason_code, "DRINK_SCALDING_WAIT")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("5", ctx.exception.message)  # honest remaining minutes
        self.assertEqual(self._obj("hot_tea_01")["portions_left"], 2)
        # A refusal is not a write: the state file is untouched.
        self.assertEqual(_snapshot(self.root), before)

        receipt = self._consume("hot_tea_01", now=_at(6))
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["step_results"][0]["portions_left"], 1)
        self.assertFalse(self._obj("hot_tea_01", now=_at(6))["scalding"])

        # ``boil_scalding=True`` is the plain shorthand for the 5-minute default.
        self._introduce(
            "hot_tea_02", "又一杯热茶", "drink", "kitchen_counter",
            portions=1, thermal="hot", boil_scalding=True,
        )
        self.assertEqual(self._obj("hot_tea_02")["scalding_wait_minutes"], 5)

    def test_unmarked_broth_and_rice_are_unchanged(self) -> None:
        self._introduce("soup_01", "热汤", "prepared_food", "kitchen_counter", portions=2)
        self._introduce("rice_01", "米饭", "prepared_food", "dining_table", portions=2)

        soup = self._obj("soup_01")
        self.assertIsNone(soup["scalding_until_iso"])
        self.assertEqual(soup["scalding_wait_minutes"], 0)
        self.assertFalse(soup["scalding"])

        soup_receipt = self._consume("soup_01")
        self.assertEqual(soup_receipt["step_results"][0]["portions_left"], 1)
        rice_receipt = self._consume("rice_01")
        self.assertEqual(rice_receipt["step_results"][0]["portions_left"], 1)

    # -- 2. private corner nickname --------------------------------------

    def _name_corner(self, payload: dict, *, actor_side: str = "subject"):
        body = {"action": "name_personal_corner"}
        body.update(payload)
        return view.mutate_v08_living_traces_view(
            self.root, body, actor_side=actor_side, now_iso=T0
        )

    def test_private_corner_name_stays_private_until_shared(self) -> None:
        status, envelope = self._name_corner({
            "zone_id": "window_side",
            "nickname_cn": "雨棚",
            "shared_with_partner": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(envelope["status"], "applied")
        self.assertEqual(envelope["zone_id"], "window_side")
        self.assertEqual(envelope["nickname_cn"], "雨棚")

        subject_view = self._view(actor_side="subject")
        names = {row["zone_id"]: row for row in subject_view["zone_nicknames"]}
        self.assertEqual(names["window_side"]["nickname_cn"], "雨棚")
        self.assertFalse(names["window_side"]["shared_with_partner"])

        partner_view = self._view(actor_side="partner")
        self.assertEqual(partner_view["zone_nicknames"], [])
        self.assertNotIn("雨棚", json.dumps(partner_view, ensure_ascii=False))

        # The partner door cannot coin or see a personal corner name.
        status_403, err = self._name_corner(
            {"zone_id": "window_side", "nickname_cn": "雨棚"},
            actor_side="partner",
        )
        self.assertEqual(status_403, 403)
        self.assertEqual(err["reason_code"], "FORBIDDEN_PARTNER_ACTION")

        # Explicit sharing is a separate decision and only then becomes visible.
        status_ok, _ = self._name_corner({
            "zone_id": "window_side",
            "nickname_cn": "雨棚",
            "shared_with_partner": True,
        })
        self.assertEqual(status_ok, 200)
        partner_after = self._view(actor_side="partner")
        self.assertEqual(
            [row["nickname_cn"] for row in partner_after["zone_nicknames"]], ["雨棚"]
        )

    def test_corner_name_rejects_unknown_fields_and_places(self) -> None:
        status_fields, err_fields = self._name_corner({
            "zone_id": "window_side",
            "nickname_cn": "雨棚",
            "colour": "blue",
        })
        self.assertEqual(status_fields, 400)
        self.assertEqual(err_fields["reason_code"], "INVALID_FIELDS")

        status_place, err_place = self._name_corner({
            "zone_id": "nowhere_at_all",
            "nickname_cn": "雨棚",
        })
        self.assertEqual(status_place, 400)
        self.assertEqual(err_place["reason_code"], "INVALID_TARGET")

        status_blank, err_blank = self._name_corner({"zone_id": "window_side"})
        self.assertEqual(status_blank, 400)
        self.assertEqual(err_blank["reason_code"], "INVALID_FIELDS")

    # -- 3. honest bookmark ----------------------------------------------

    def test_bookmark_never_claims_understanding_and_reads_are_zero_write(
        self,
    ) -> None:
        self._introduce(
            "old_book_01", "旧小说", "paper", "home_staging_corner", paper_like=True,
        )
        receipt = self._act({
            "intent_cn": "夹个书签",
            "steps": [{
                "op": "NOTE_ASSOCIATION",
                "target_id": "old_book_01",
                "text": "读到雨夜那章",
                "bookmark_page": 37,
                "revisit_offset": -2,
            }],
        })
        mark = receipt["step_results"][0]["reading_mark"]
        self.assertEqual(mark["bookmark_page"], 37)
        self.assertEqual(mark["revisit_offset"], -2)
        self.assertIs(mark["understood"], False)

        before = _snapshot(self.root)
        projected = self._obj("old_book_01")
        partner_projected = self._obj("old_book_01", actor_side="partner")
        self.assertEqual(projected["reading_mark"]["bookmark_page"], 37)
        self.assertIs(projected["reading_mark"]["understood"], False)
        self.assertIs(partner_projected["reading_mark"]["understood"], False)
        # Reading never writes anything, not even the projection.
        self.assertEqual(_snapshot(self.root), before)

        # A legacy association without bookmark fields invents no page mark.
        self._introduce("note_sheet_01", "便签纸", "paper", "home_staging_corner", paper_like=True)
        self._act({
            "intent_cn": "记一句",
            "steps": [{
                "op": "NOTE_ASSOCIATION",
                "target_id": "note_sheet_01",
                "text": "明天买酱油",
            }],
        })
        self.assertIsNone(self._obj("note_sheet_01")["reading_mark"])


if __name__ == "__main__":
    unittest.main()
