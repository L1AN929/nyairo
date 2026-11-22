#!/usr/bin/env python3
"""v0.4 Ch.40 §三/四/五/八: general conservation, reversible assembly, frozen
transcription and visible repair -- all deterministic (``llm_calls == 0``).

Coverage:
* ``SPLIT_MATERIAL`` never invents or destroys an integer ``material_units``
  (4 -> 1 + 3 stays 4) and refuses to split a 1-unit stock or take everything;
* ``ASSEMBLE_OR_DETACH_PART`` records an honest ``oversized_unfitted`` part that
  stays beside the host across a repair, then fits, rides along when the host
  moves, survives a refused discard, and detaches keeping its identity and note;
* ``TRANSCRIBE_TO_PAPER`` freezes ``(artifact_id, version_id, excerpt)`` on real
  paper; wet paper smudges then dries clear; private cards never reach the
  partner door and a shared card exposes only the frozen snapshot;
* ``PATCH_OR_REPAIR`` consumes exactly one patch unit (2 -> 1 then 1 -> 0) and
  leaves an honest ``wear_state == "patched"`` with its note;
* ``partner_bring_item`` accepts ``material_units`` / ``assembly_role`` through
  the phone door, and a fresh root stays 100% zero-write on reads/scans.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_matter_craft_conservation -v``
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

from components.world.scripts import v08_universal_matter as engine  # noqa: E402
from components.phone import v08_living_traces_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
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


class MatterCraftConservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-craft-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _view(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return engine.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _obj(self, object_id: str, *, actor_side: str = "subject", now: str = T0) -> dict:
        for row in self._view(actor_side=actor_side, now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _state_obj(self, object_id: str) -> dict:
        return engine._read_state_zero_write(self.root)["matter_objects"][object_id]

    def _act(self, payload: dict, *, now: str = T0) -> dict:
        receipt = engine.compose_open_act(self.root, payload, now_iso=now)
        self.assertEqual(receipt["llm_calls"], 0)
        return receipt

    def _introduce(self, object_id: str, label: str, kind: str, target: str, *, now: str = T0, **extra) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return self._act({"intent_cn": f"放入{label}", "steps": [step]}, now=now)

    # -- 1 ---------------------------------------------------------------

    def test_split_material_conserves_integer_units(self) -> None:
        self._introduce(
            "stock_01", "整张卡纸", "paper", "home_staging_corner",
            paper_like=True, material_units=4,
        )
        self.assertEqual(self._obj("stock_01")["material_units"], 4)

        first = self._act({
            "intent_cn": "裁下一角卡纸",
            "steps": [{
                "op": "SPLIT_MATERIAL", "object_id": "stock_01",
                "take_units": 1, "output_label_cn": "一小角卡纸",
                "assembly_role": "insert_part",
            }],
        })
        step = first["step_results"][0]
        self.assertEqual(step["source_units_before"], 4)
        self.assertEqual(step["source_units_after"], 3)
        self.assertEqual(step["output_material_units"], 1)
        self.assertEqual(step["conserved_total_units"], 4)
        self.assertEqual(self._obj("stock_01")["material_units"], 3)
        self.assertEqual(self._obj(step["output_object_id"])["material_units"], 1)
        self.assertEqual(self._obj(step["output_object_id"])["assembly_role"], "insert_part")

        second = self._act({
            "intent_cn": "再裁下两小角卡纸",
            "steps": [{
                "op": "SPLIT_MATERIAL", "object_id": "stock_01",
                "take_units": 2, "output_label_cn": "两小角卡纸",
            }],
        })
        step2 = second["step_results"][0]
        self.assertEqual(step2["source_units_after"], 1)
        self.assertEqual(step2["output_material_units"], 2)
        total = self._obj("stock_01")["material_units"] + self._obj(step["output_object_id"])["material_units"] \
            + self._obj(step2["output_object_id"])["material_units"]
        self.assertEqual(total, 4, "no unit may be invented or destroyed")

        # A one-unit stock cannot be split again.
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({
                "intent_cn": "再裁已经没有余量",
                "steps": [{"op": "SPLIT_MATERIAL", "object_id": "stock_01", "take_units": 1,
                           "output_label_cn": "不可能的一角"}],
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_TARGET")

        # Taking everything at once is refused (the source must keep >= 1).
        self._introduce("stock_02", "两格便签纸", "paper", "kitchen_counter", paper_like=True, material_units=2)
        with self.assertRaises(engine.V08MatterError) as ctx2:
            self._act({
                "intent_cn": "把两格都拿走",
                "steps": [{"op": "SPLIT_MATERIAL", "object_id": "stock_02", "take_units": 2,
                           "output_label_cn": "全拿走"}],
            })
        self.assertEqual(ctx2.exception.reason_code, "INVALID_FIELDS")
        self.assertEqual(self._obj("stock_02")["material_units"], 2, "a refused split changes nothing")

    # -- 2 ---------------------------------------------------------------

    def test_assemble_oversized_then_repair_fit_move_and_detach(self) -> None:
        self._introduce("frame_01", "木框", "everyday", "home_staging_corner",
                        assembly_role="host_frame")
        self._introduce("shelf_01", "隔板", "everyday", "home_staging_corner",
                        assembly_role="insert_part", material_units=2)

        # Ch.40 §八: a too-big part is not force-fitted; it stays beside the host.
        oversized = self._act({
            "intent_cn": "试着把隔板装进木框",
            "steps": [{
                "op": "ASSEMBLE_OR_DETACH_PART", "mode": "assemble",
                "host_object_id": "frame_01", "part_object_id": "shelf_01",
                "part_fits": False,
            }],
        })
        step = oversized["step_results"][0]
        self.assertFalse(step["fitted"])
        self.assertEqual(step["fit_status"], "oversized_unfitted")
        shelf = self._obj("shelf_01")
        self.assertIsNone(shelf["assembled_into_id"])
        self.assertEqual(shelf["fit_status"], "oversized_unfitted")
        self.assertEqual(shelf["parent_id"], self._obj("frame_01")["parent_id"])
        self.assertIn("偏大", shelf["derived_role_cn"])
        self.assertEqual(self._obj("frame_01")["assembled_parts_ids"], [])

        # Trim a real cardstock edge: the patch resets the fit and leaves a note.
        self._introduce("patch_stock_01", "蓝卡纸边角", "paper", "home_staging_corner",
                        paper_like=True, material_units=1)
        repaired = self._act({
            "intent_cn": "用蓝卡纸边角修一下隔板折痕",
            "steps": [{
                "op": "PATCH_OR_REPAIR", "object_id": "shelf_01",
                "patch_material_id": "patch_stock_01",
                "patch_note_cn": "用蓝卡纸边角重新补贴了标签折痕",
                "fixes_fit": True,
            }],
        })
        rep = repaired["step_results"][0]
        self.assertTrue(rep["patch_material_consumed"])
        self.assertEqual(rep["fit_status"], "none")
        self.assertIn("补过的", self._obj("shelf_01")["derived_role_cn"])
        self.assertTrue(self._state_obj("patch_stock_01")["consumed"])

        # Refit after the repair: now it really fits.
        fitted = self._act({
            "intent_cn": "重新把隔板装进木框",
            "steps": [{
                "op": "ASSEMBLE_OR_DETACH_PART", "mode": "assemble",
                "host_object_id": "frame_01", "part_object_id": "shelf_01",
                "part_fits": True,
            }],
        })
        self.assertTrue(fitted["step_results"][0]["fitted"])
        self.assertEqual(self._obj("shelf_01")["fit_status"], "fitted")
        self.assertEqual(self._obj("shelf_01")["assembled_into_id"], "frame_01")
        self.assertEqual(self._obj("frame_01")["assembled_parts_ids"], ["shelf_01"])
        self.assertIn("装着", self._obj("frame_01")["derived_role_cn"])
        self.assertIn("隔板", self._obj("frame_01")["derived_role_cn"])

        # Moving the host carries the fitted part to the new place.
        self._act({
            "intent_cn": "把木框挪到备餐台",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "frame_01",
                       "target_parent_id": "kitchen_counter"}],
        })
        self.assertEqual(self._obj("frame_01")["parent_id"], "kitchen_counter")
        self.assertEqual(self._obj("shelf_01")["parent_id"], "kitchen_counter")

        # A host with a fitted part cannot be discarded out from under it.
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({
                "intent_cn": "直接扔掉木框",
                "steps": [{"op": "DISCARD_ITEM", "object_id": "frame_01"}],
            })
        self.assertEqual(ctx.exception.reason_code, "CONTAINER_NOT_EMPTY")

        # Detach keeps the part's identity and its repair note.
        detached = self._act({
            "intent_cn": "把隔板拆出来",
            "steps": [{
                "op": "ASSEMBLE_OR_DETACH_PART", "mode": "detach",
                "host_object_id": "frame_01", "part_object_id": "shelf_01",
            }],
        })
        self.assertEqual(detached["step_results"][0]["fit_status"], "none")
        shelf = self._obj("shelf_01")
        self.assertIsNone(shelf["assembled_into_id"])
        self.assertEqual(shelf["fit_status"], "none")
        self.assertEqual(shelf["wear_state"], "patched")
        self.assertEqual(shelf["patch_note_cn"], "用蓝卡纸边角重新补贴了标签折痕")
        self.assertEqual(shelf["object_id"], "shelf_01")

    # -- 3 ---------------------------------------------------------------

    def test_transcribe_freezes_snapshot_and_respects_privacy(self) -> None:
        self._introduce("card_01", "记稿卡纸", "paper", "home_staging_corner", paper_like=True)
        excerpt = "第三稿开头：海边的风把纸吹得直响"
        receipt = self._act({
            "intent_cn": "把第三稿的开头誊到卡纸上",
            "steps": [{
                "op": "TRANSCRIBE_TO_PAPER", "object_id": "card_01",
                "artifact_id": "art-sea-wind", "version_id": "v3",
                "snapshot_excerpt_cn": excerpt,
            }],
        })
        step = receipt["step_results"][0]
        self.assertEqual(step["version_id"], "v3")
        card = self._obj("card_01")
        self.assertEqual(card["inscribed_text_cn"], excerpt)
        self.assertEqual(card["transcribed_source"]["artifact_id"], "art-sea-wind")
        self.assertEqual(card["transcribed_source"]["version_id"], "v3")
        self.assertEqual(card["transcribed_source"]["snapshot_excerpt_cn"], excerpt)
        self.assertEqual(card["note_legibility"], "clear")

        # Wet paper smudges the same frozen inscription, then dries clear.
        self._act({"intent_cn": "卡纸沾了点水",
                   "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "card_01"}]})
        self.assertEqual(self._obj("card_01")["note_legibility"], "smudged")
        engine.settle_matter_physics(self.root, now_iso=_at(130))
        self.assertEqual(self._obj("card_01", now=_at(130))["note_legibility"], "clear")
        self.assertEqual(self._obj("card_01", now=_at(130))["transcribed_source"]["version_id"], "v3")

        # A private transcription never reaches the partner door.
        self._introduce("secret_card", "私密记稿", "paper", "subject_private_drawer", paper_like=True)
        private_excerpt = "只给自己看的第三稿结尾"
        self._act({
            "intent_cn": "把私密结尾誊到私人卡纸上",
            "steps": [{
                "op": "TRANSCRIBE_TO_PAPER", "object_id": "secret_card",
                "artifact_id": "art-secret", "version_id": "v3",
                "snapshot_excerpt_cn": private_excerpt,
            }],
        })
        partner = self._view(actor_side="partner", now=_at(130))
        partner_ids = {row["object_id"] for row in partner["objects"]}
        self.assertNotIn("secret_card", partner_ids)
        partner_blob = json.dumps(partner, ensure_ascii=False)
        self.assertNotIn(private_excerpt, partner_blob)
        self.assertNotIn("art-secret", partner_blob)

        # The shared card exposes only its frozen snapshot, never a live draft.
        shared = self._obj("card_01", actor_side="partner", now=_at(130))
        self.assertEqual(set(shared["transcribed_source"]), {
            "artifact_id", "version_id", "snapshot_excerpt_cn", "transcribed_at",
        })
        self.assertEqual(shared["transcribed_source"]["snapshot_excerpt_cn"], excerpt)
        self.assertNotIn("draft_body", shared)
        self.assertNotIn("draft_text_cn", shared)

    # -- 4 ---------------------------------------------------------------

    def test_patch_or_repair_consumes_one_unit_and_leaves_honest_wear(self) -> None:
        self._introduce("label_01", "旧标签", "paper", "home_staging_corner", paper_like=True)
        self._introduce("patch_stock_02", "卡纸边角", "paper", "home_staging_corner",
                        paper_like=True, material_units=2)

        first = self._act({
            "intent_cn": "补贴标签折痕",
            "steps": [{
                "op": "PATCH_OR_REPAIR", "object_id": "label_01",
                "patch_material_id": "patch_stock_02",
                "patch_note_cn": "用蓝卡纸边角重新补贴了标签折痕",
            }],
        })
        step = first["step_results"][0]
        self.assertEqual(step["patch_units_before"], 2)
        self.assertEqual(step["patch_units_after"], 1)
        self.assertFalse(step["patch_material_consumed"])
        self.assertEqual(self._obj("patch_stock_02")["material_units"], 1)
        self.assertEqual(self._obj("label_01")["wear_state"], "patched")
        self.assertEqual(self._obj("label_01")["patch_note_cn"], "用蓝卡纸边角重新补贴了标签折痕")

        second = self._act({
            "intent_cn": "再用剩下的边角加固一下",
            "steps": [{
                "op": "PATCH_OR_REPAIR", "object_id": "label_01",
                "patch_material_id": "patch_stock_02",
                "patch_note_cn": "最后一角也贴上了",
            }],
        })
        step2 = second["step_results"][0]
        self.assertEqual(step2["patch_units_before"], 1)
        self.assertEqual(step2["patch_units_after"], 0)
        self.assertTrue(step2["patch_material_consumed"])
        stock = self._state_obj("patch_stock_02")
        self.assertTrue(stock["consumed"])
        self.assertIsNone(stock["parent_id"])
        self.assertEqual(stock["material_units"], 0)
        self.assertEqual(self._obj("label_01")["wear_state"], "patched")
        self.assertIn("补过的", self._obj("label_01")["derived_role_cn"])

        # An object cannot patch itself.
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({
                "intent_cn": "自己贴自己",
                "steps": [{"op": "PATCH_OR_REPAIR", "object_id": "label_01",
                           "patch_material_id": "label_01", "patch_note_cn": "无意义"}],
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_TARGET")

    # -- 5 ---------------------------------------------------------------

    def test_partner_bring_material_fields_and_zero_write_fresh_root(self) -> None:
        absent = self.root / "never-created"
        before = _snapshot(self.root)
        engine.read_matter_space_view(absent, actor_side="partner", now_iso=T0)
        self.assertEqual(engine.scan_matter_opportunities_zero_write(absent, now_iso=T0), [])
        view.summarize_living_traces_for_context(absent, include_private=False, now_iso=T0)
        self.assertEqual(_snapshot(self.root), before, "reads and scans on a fresh root are zero-write")
        self.assertFalse(absent.exists())
        self.assertFalse(engine.state_path(absent).exists())

        receipt = engine.partner_bring_item(
            self.root,
            {
                "label_cn": "备用隔板", "item_kind": "everyday",
                "target_parent_id": "home_staging_corner",
                "material_units": 4, "assembly_role": "insert_part",
            },
            now_iso=T0,
        )
        self.assertEqual(receipt["llm_calls"], 0)
        object_id = receipt["object_id"]
        row = self._obj(object_id)
        self.assertEqual(row["material_units"], 4)
        self.assertEqual(row["assembly_role"], "insert_part")

        for bad in (0, 25, True, "4"):
            with self.assertRaises(engine.V08MatterError):
                engine.partner_bring_item(
                    self.root,
                    {"label_cn": "坏隔板", "item_kind": "everyday",
                     "target_parent_id": "home_staging_corner", "material_units": bad},
                    now_iso=T0,
                )
        with self.assertRaises(engine.V08MatterError):
            engine.partner_bring_item(
                self.root,
                {"label_cn": "坏隔板", "item_kind": "everyday",
                 "target_parent_id": "home_staging_corner", "assembly_role": "bogus"},
                now_iso=T0,
            )

        # The phone door whitelist accepts the same two optional craft fields.
        code, body = view.mutate_v08_living_traces_view(
            self.root,
            {
                "action": "partner_bring_item", "label_cn": "门带来的线轴",
                "item_kind": "everyday", "target_parent_id": "home_staging_corner",
                "material_units": 3, "assembly_role": "host_frame",
            },
            actor_side="partner", now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        brought = (body.get("data") or {}).get("object_id")
        self.assertTrue(brought)
        self.assertEqual(self._obj(brought)["material_units"], 3)
        self.assertEqual(self._obj(brought)["assembly_role"], "host_frame")

        bad_code, bad_body = view.mutate_v08_living_traces_view(
            self.root,
            {"action": "partner_bring_item", "label_cn": "坏线轴",
             "item_kind": "everyday", "target_parent_id": "home_staging_corner",
             "material_units": 99},
            actor_side="partner", now_iso=T0,
        )
        self.assertEqual(bad_code, 400, bad_body)

    # -- 6 ---------------------------------------------------------------

    def test_scan_surfaces_craft_in_progress_zero_write(self) -> None:
        # Move the two desk pens away so the desk has room for a real craft item.
        self._act({
            "intent_cn": "把笔挪开腾出桌面",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"},
                {"op": "PLACE_OR_MOVE", "object_id": "pen_02", "target_parent_id": "home_staging_corner"},
            ],
        })
        self._introduce("frame_02", "小相框", "everyday", "home_desk", assembly_role="host_frame")
        self._introduce("shelf_02", "框内隔条", "everyday", "home_staging_corner",
                        assembly_role="insert_part")
        self._act({
            "intent_cn": "试着把隔条装进相框但装不下",
            "steps": [{
                "op": "ASSEMBLE_OR_DETACH_PART", "mode": "assemble",
                "host_object_id": "frame_02", "part_object_id": "shelf_02",
                "part_fits": False,
            }],
        })
        self.assertEqual(self._obj("shelf_02")["parent_id"], "home_desk")

        before = _snapshot(self.root)
        candidates = engine.scan_matter_opportunities_zero_write(self.root, now_iso=T0)
        self.assertEqual(_snapshot(self.root), before, "the opportunity scan is zero-write")
        self.assertIn(
            "MATTER_PART_OVERSIZED_UNFITTED",
            [c["availability_basis"] for c in candidates],
        )
        craft_ref = next(c for c in candidates if c["availability_basis"] == "MATTER_PART_OVERSIZED_UNFITTED")
        self.assertEqual(craft_ref["target_ref"], "v08_matter:craft_refit:shelf_02")

        # A separate fresh root: whole cardstock on the desk is offerable for a split.
        split_root = Path(tempfile.mkdtemp(prefix="v08-craft-split-"))
        try:
            engine.compose_open_act(split_root, {
                "intent_cn": "把笔挪开，放一整张卡纸在桌上",
                "steps": [
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"},
                    {"op": "PLACE_OR_MOVE", "object_id": "pen_02", "target_parent_id": "home_staging_corner"},
                    {"op": "INTRODUCE_ITEM", "object_id": "big_card", "label_cn": "整张白卡纸",
                     "item_kind": "paper", "target_parent_id": "home_desk",
                     "paper_like": True, "material_units": 3},
                ],
            }, now_iso=T0)
            split_candidates = engine.scan_matter_opportunities_zero_write(split_root, now_iso=T0)
            self.assertIn(
                "MATTER_STOCK_SPLITTABLE",
                [c["availability_basis"] for c in split_candidates],
            )
            self.assertNotIn(
                "MATTER_USED_OR_CLUTTERED",
                [c["availability_basis"] for c in split_candidates],
            )
        finally:
            import shutil
            shutil.rmtree(split_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
