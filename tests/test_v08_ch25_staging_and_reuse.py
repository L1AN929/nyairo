#!/usr/bin/env python3
"""Ch.25 staging corner ("顺手暂放角") and everyday reuse.

Coverage:
* an empty directory reads ``staging_corner`` with strict zero writes, zero
  occupation and no guilt/debt fields ever set;
* arbitrary zero-code drinks land in the corner, fill its four slots and an
  honest fifth placement is refused by real capacity -- moving one out frees a
  slot again (no escalating all-role punishment);
* a private note staged by the subject is neither counted nor named in the
  partner's ``staging_corner`` (no side-channel slot/label leak);
* a partner move keeps the subject's old impression honest (stale, not
  rewritten) and a proposed use only touches the real role after ``adopt``;
* the phone living-traces view passes ``matter_space["staging_corner"]``
  through untouched and stays zero-write.

Run: ``python -B -m unittest tests.test_v08_ch25_staging_and_reuse -v``.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_universal_matter as engine  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


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


class V08Ch25StagingAndReuseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch25-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _view(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return engine.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _staging(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return self._view(actor_side=actor_side, now=now)["staging_corner"]

    def _obj(self, object_id: str, *, now: str = T0) -> dict:
        for row in self._view(now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _introduce(self, object_id: str, label: str, kind: str, target: str, *, now: str = T0, **extra) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return engine.compose_open_act(
            self.root, {"intent_cn": f"放入{label}", "steps": [step]}, now_iso=now
        )

    # -- 1 --------------------------------------------------------------
    def test_empty_staging_corner_reads_zero_write_and_debt_free(self) -> None:
        before = _snapshot(self.root)
        subject = self._staging()
        partner = self._staging(actor_side="partner")
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.root / "world").exists())
        self.assertFalse(engine.state_path(self.root).exists())

        for corner in (subject, partner):
            self.assertEqual(corner["zone_id"], "home_staging_corner")
            self.assertEqual(corner["label_cn"], "顺手暂放角")
            self.assertEqual(corner["capacity_slots"], 4)
            self.assertEqual(corner["occupied_slots"], 0)
            self.assertEqual(corner["free_slots"], 4)
            self.assertEqual(corner["item_ids"], [])
            self.assertEqual(corner["item_labels_cn"], [])
            self.assertFalse(corner["is_full"])
            self.assertEqual(corner["obligation_debt"], 0)
            self.assertIs(corner["messy_guilt"], False)
            self.assertEqual(corner["note_cn"], "")

    # -- 2 --------------------------------------------------------------
    def test_zero_code_drinks_fill_slots_then_honest_capacity_limit(self) -> None:
        drinks = [
            ("cola_01", "冰可乐"),
            ("soda_01", "柠檬气泡水"),
            ("oolong_01", "冷泡乌龙"),
            ("lemonade_01", "手作柠檬水"),
        ]
        for object_id, label in drinks:
            self._introduce(object_id, label, "drink", "home_staging_corner")

        corner = self._staging()
        self.assertEqual(corner["occupied_slots"], 4)
        self.assertEqual(corner["free_slots"], 0)
        self.assertTrue(corner["is_full"])
        # Items are listed by ``object_id`` order, which is the honest sort.
        self.assertEqual(
            corner["item_labels_cn"],
            [label for _, label in sorted(drinks, key=lambda row: row[0])],
        )
        self.assertEqual(corner["note_cn"], "暂放角已经放满，先挪开一件再放新东西")

        # A fifth drink introduced elsewhere cannot be squeezed in -- the slot
        # limit is physical, not an escalated whole-role penalty.
        self._introduce("five_tea_01", "冰红茶", "drink", "dining_table")
        with self.assertRaises(engine.V08MatterError) as ctx:
            engine.compose_open_act(
                self.root,
                {
                    "intent_cn": "把第五杯放进暂放角",
                    "steps": [
                        {
                            "op": "PLACE_OR_MOVE",
                            "object_id": "five_tea_01",
                            "target_parent_id": "home_staging_corner",
                        }
                    ],
                },
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "CAPACITY_FULL")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._staging()["occupied_slots"], 4)

        # Taking one out frees a slot immediately; nothing lingers as a debt.
        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "把冰可乐拿去餐桌",
                "steps": [
                    {
                        "op": "PLACE_OR_MOVE",
                        "object_id": "cola_01",
                        "target_parent_id": "dining_table",
                    }
                ],
            },
            now_iso=T0,
        )
        freed = self._staging()
        self.assertEqual(freed["occupied_slots"], 3)
        self.assertEqual(freed["free_slots"], 1)
        self.assertFalse(freed["is_full"])
        self.assertEqual(freed["obligation_debt"], 0)
        self.assertIs(freed["messy_guilt"], False)
        self.assertNotIn("cola_01", freed["item_ids"])

        engine.compose_open_act(
            self.root,
            {
                "intent_cn": "把冰红茶放进空出来的位置",
                "steps": [
                    {
                        "op": "PLACE_OR_MOVE",
                        "object_id": "five_tea_01",
                        "target_parent_id": "home_staging_corner",
                    }
                ],
            },
            now_iso=T0,
        )
        refilled = self._staging()
        self.assertEqual(refilled["occupied_slots"], 4)
        self.assertIn("five_tea_01", refilled["item_ids"])

    # -- 3 --------------------------------------------------------------
    def test_partner_never_counts_or_names_private_staged_item(self) -> None:
        self._introduce(
            "private_note_01",
            "私密手稿纸条",
            "paper",
            "home_staging_corner",
            visibility="private",
            paper_like=True,
        )

        subject = self._staging()
        self.assertEqual(subject["occupied_slots"], 1)
        self.assertEqual(subject["item_ids"], ["private_note_01"])
        self.assertEqual(subject["item_labels_cn"], ["私密手稿纸条"])

        partner = self._staging(actor_side="partner")
        self.assertEqual(partner["occupied_slots"], 0)
        self.assertEqual(partner["free_slots"], 4)
        self.assertEqual(partner["item_ids"], [])
        self.assertEqual(partner["item_labels_cn"], [])
        self.assertFalse(partner["is_full"])
        self.assertEqual(partner["note_cn"], "")
        partner_object_ids = {
            row["object_id"] for row in self._view(actor_side="partner")["objects"]
        }
        self.assertNotIn("private_note_01", partner_object_ids)

    # -- 4 --------------------------------------------------------------
    def test_partner_move_keeps_subject_impression_and_proposal_not_adopted(self) -> None:
        engine.partner_move_shared_object(
            self.root,
            {"object_id": "pen_01", "target_parent_id": "home_staging_corner"},
            now_iso=T0,
        )

        # The subject still believes the pen is where she last saw it.
        subject_pen = self._obj("pen_01")
        self.assertEqual(subject_pen["parent_id"], "home_desk")
        self.assertEqual(subject_pen["subject_last_seen_parent"], "home_desk")
        self.assertNotIn("impression_stale", subject_pen)

        # The partner's God-view knows the real spot and flags the stale belief.
        partner_pen = next(
            row
            for row in self._view(actor_side="partner")["objects"]
            if row["object_id"] == "pen_01"
        )
        self.assertEqual(partner_pen["parent_id"], "home_staging_corner")
        self.assertTrue(partner_pen["impression_stale"])
        # The real staging corner holds the pen, while the subject's believed
        # corner still doesn't (she has not looked yet).
        self.assertIn("pen_01", self._staging(actor_side="partner")["item_ids"])
        self.assertNotIn("pen_01", self._staging()["item_ids"])

        # A proposal is only a suggestion until the subject adopts it.
        engine.partner_propose_object_use(
            self.root,
            {"object_id": "ceramic_cup_01", "proposed_use_cn": "临时插画笔"},
            now_iso=T0,
        )
        pending_role = self._obj("ceramic_cup_01")["derived_role_cn"]
        self.assertNotIn("已采纳用法", pending_role)
        self.assertIn("伴侣建议：临时插画笔（待决定）", pending_role)
        stored = engine._read_state_zero_write(self.root)["matter_objects"]["ceramic_cup_01"]
        self.assertEqual(stored["proposed_use"]["status"], "proposed")
        self.assertEqual(stored["proposed_use"]["proposed_by"], "partner")

        engine.respond_proposed_use(
            self.root,
            {"object_id": "ceramic_cup_01", "decision": "adopt"},
            now_iso=T0,
        )
        self.assertIn("已采纳用法：临时插画笔", self._obj("ceramic_cup_01")["derived_role_cn"])

    # -- 5 --------------------------------------------------------------
    def test_living_traces_passes_staging_corner_through_untouched(self) -> None:
        self._introduce("cola_01", "冰可乐", "drink", "home_staging_corner")
        expected = self._staging()
        self.assertEqual(expected["occupied_slots"], 1)

        before = _snapshot(self.root)
        code, subject_body = traces_view.read_v08_living_traces_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        code_partner, partner_body = traces_view.read_v08_living_traces_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(_snapshot(self.root), before)

        self.assertEqual(code, 200, subject_body)
        self.assertEqual(code_partner, 200, partner_body)
        subject_space = (subject_body.get("data") or {}).get("matter_space") or {}
        partner_space = (partner_body.get("data") or {}).get("matter_space") or {}
        self.assertIn("staging_corner", subject_space)
        self.assertIn("staging_corner", partner_space)
        subject_corner = subject_space["staging_corner"]
        partner_corner = partner_space["staging_corner"]
        self.assertEqual(subject_corner["zone_id"], "home_staging_corner")
        self.assertEqual(subject_corner["occupied_slots"], 1)
        self.assertEqual(subject_corner["item_ids"], ["cola_01"])
        # A shared item is legitimately visible to the partner too.
        self.assertEqual(partner_corner["occupied_slots"], 1)
        self.assertEqual(partner_corner["item_labels_cn"], ["冰可乐"])
        self.assertEqual(subject_corner["obligation_debt"], 0)
        self.assertIs(subject_corner["messy_guilt"], False)


if __name__ == "__main__":
    unittest.main()
