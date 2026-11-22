#!/usr/bin/env python3
"""Unit & integration tests for v0.9 Pillar D (Emergent Object Reuse, Self-Invented Taxonomy & Capability Collector).

Verifies (llm_calls == 0):
1. Emptied biscuit tin reuse: STORE_ITEMS (ticket stubs + paper slips + capacity 409),
   ATTACH_LABEL (naming box "收音机" NEVER changes supported_capabilities or grants audio),
   ADD_NOTE (shared vs private visibility), and unsupported capability refusal.
2. Ceramic mug drinkware occupancy physics: check_vessel_ready_for_drink_zero_write AND
   somatic_comfort_view.is_cup_occupied_by_stationery / drink_care / offer block brewing
   when stationery is stored inside, and unblock cleanly once emptied.
3. Blank-start custom_categories ({} on new instance) -> create "我还没有被说服" &
   "以后想重新看看" -> assign & move item preserving full 8-field revision_history ->
   revise/rename/archive preserving history.
4. Blocked-attempt capability collector: TEMPORARY_WORLD_BLOCKER_CODES (ITEM_OUT_OF_STOCK,
   STORE_CLOSED, etc.) classified as WORLD_CONDITION_TEMPORARY (logged_as_capability_gap=False,
   zero_write=True), while 3 related attach attempts merge into 1 ranked capability cluster
   (occurrence_count == 3, bounded sample_attempts <= 5).
5. Private visibility withholding & partner history redaction, subject-only write guard (403),
   and smuggled payload field rejection (400).
6. 10-thread concurrency (capacity race & concurrent capability clustering), operation_key
   idempotency & 409 conflict, 0600 UTF-8 LF persistence, symlink rejection.
7. Unified Forget Barrier filtering (objects, stored items, notes, categories, items,
   capability samples), fail-closed behavior on corrupt control ledger, and 100% zero-write reads.
8. File hygiene (pure UTF-8 LF, no BOM, 0 unicode.So emojis, neutral TA) & 9 sealed files intact.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import v02_representation_lease_forget as _rlf  # noqa: E402
from components.phone import somatic_comfort_view as _somatic  # noqa: E402
from components.world.scripts import personal_objects_taxonomy as pot  # noqa: E402

T0 = "2026-11-21T10:00:00+08:00"
T1 = "2026-11-21T11:30:00+08:00"
T2 = "2026-11-21T14:00:00+08:00"
T3 = "2026-11-21T16:15:00+08:00"

SEALED_FILES_SHA256: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py": "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py": "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
    "components/native/native_recall/readonly_recall_adapter.py": "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a",
    "NOW.md": "d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152",
    "plugins/chiyo/__init__.py": "8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7",
    "components/native/native_recall/production_consumption_evaluator_v0_3.py": "141822d19049a8a5247b68885616cd0c156d616dea84d36ab099584605fc1b41",
    "components/native/native_recall/production_consumption_evaluator_v0.py": "f7fdeccecea1c1a8377f4806d1d09b1a203367eb58bdc56505e021c264eb2400",
    "components/native/native_recall/CANDIDATE_CONSUMPTION_POLICY_V0_3.json": "6228d24345249aed862f56484004c522e7573760d0288480ca3e87008a1514b2",
    "components/world/scripts/v05_unified_world_fabric.py": "e6d5ce671872285935c9840a1de0cb75687b5562fe89c45b5ba10a402ffc0772",
}


def _snapshot_tree(root: Path) -> list[tuple[str, str, int, int]]:
    if not root.exists():
        return []
    rows: list[tuple[str, str, int, int]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        kind = "L" if p.is_symlink() else ("D" if p.is_dir() else "F")
        size = int(st.st_size) if kind == "F" else 0
        rows.append((rel, kind, size, int(st.st_mtime_ns)))
    return rows


class PersonalObjectsAndTaxonomyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="v09-obj-tax-"))
        self.root = self.base / "instance_a"
        self.root.mkdir(parents=True, exist_ok=True)
        self._prev_control_root = os.environ.get("NYAIRO_V02_CONTROL_ROOT")
        os.environ["NYAIRO_V02_CONTROL_ROOT"] = str(self.root / "control_authority")

    def tearDown(self) -> None:
        if self._prev_control_root is None:
            os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        else:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_control_root
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Emptied Biscuit Tin Reuse, Label Physics & Radio Refusal
    # ------------------------------------------------------------------

    def test_01_empty_biscuit_tin_reuse_label_note_and_radio_refuses_audio(self) -> None:
        # Unemptied consumable container cannot be repurposed yet
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as full_ctx:
            pot.register_or_repurpose_object(
                self.root,
                object_id="tin_full_fail",
                object_kind="cookie_tin",
                consumable_portions_remaining=2,
                now_iso=T0,
            )
        self.assertEqual(full_ctx.exception.status_code, 409)
        self.assertEqual(full_ctx.exception.reason_code, "CONTAINER_NOT_EMPTY_YET")

        # Register emptied biscuit tin with capacity 2
        reg = pot.register_or_repurpose_object(
            self.root,
            object_id="tin_biscuit_01",
            object_kind="cookie_tin",
            base_name_cn="空黄油饼干铁盒",
            capacity_slots=2,
            drinkware=False,
            emptied_from_consumable=True,
            origin_provenance={
                "source_item_name_cn": "街角烘焙黄油曲奇",
                "acquired_from_store_id": "cafe",
            },
            visibility="shared",
            operation_key="op_reg_tin_01",
            now_iso=T0,
        )
        self.assertTrue(reg["ok"])
        self.assertEqual(reg["llm_calls"], 0)
        self.assertEqual(reg["object"]["supported_capabilities"], ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"])
        self.assertFalse(reg["object"]["drinkware"])
        self.assertTrue(reg["object"]["emptied_from_consumable"])

        # Store ticket stub + unfinished paper slip
        s1 = pot.store_item_in_object(
            self.root,
            object_id="tin_biscuit_01",
            item_label_cn="旧港书展门票根",
            item_kind="ticket_stub",
            operation_key="op_store_stub_1",
            now_iso=T0,
        )
        self.assertEqual(s1["object"]["stored_count"], 1)
        self.assertEqual(s1["object"]["available_slots"], 1)

        s2 = pot.store_item_in_object(
            self.root,
            object_id="tin_biscuit_01",
            item_label_cn="半截散文摘录纸条",
            item_kind="paper_slip",
            operation_key="op_store_slip_2",
            now_iso=T0,
        )
        self.assertEqual(s2["object"]["stored_count"], 2)
        self.assertEqual(s2["object"]["available_slots"], 0)

        # 3rd item refuses with 409 OBJECT_CAPACITY_FULL
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as cap_ctx:
            pot.store_item_in_object(
                self.root,
                object_id="tin_biscuit_01",
                item_label_cn="多出来的一张车票",
                item_kind="ticket_stub",
                now_iso=T1,
            )
        self.assertEqual(cap_ctx.exception.status_code, 409)
        self.assertEqual(cap_ctx.exception.reason_code, "OBJECT_CAPACITY_FULL")

        # Attach practical label first, then rename box "收音机" -> capabilities NEVER change!
        lbl1 = pot.attach_label_to_object(
            self.root,
            object_id="tin_biscuit_01",
            custom_label_cn="旧票根与半截草稿盒",
            operation_key="op_lbl_1",
            now_iso=T1,
        )
        self.assertEqual(lbl1["custom_label_cn"], "旧票根与半截草稿盒")

        lbl_radio = pot.attach_label_to_object(
            self.root,
            object_id="tin_biscuit_01",
            custom_label_cn="收音机",
            operation_key="op_lbl_radio",
            now_iso=T1,
        )
        self.assertEqual(lbl_radio["custom_label_cn"], "收音机")
        self.assertEqual(lbl_radio["supported_capabilities"], ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"])

        # Naming box "收音机" still refuses unsupported audio & drink capabilities!
        snap = _snapshot_tree(self.root)
        ok_audio, reason_audio = pot.check_object_capability_zero_write(self.root, "tin_biscuit_01", "PLAY_AUDIO")
        self.assertFalse(ok_audio)
        self.assertEqual(reason_audio, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT")
        ok_drink, reason_drink = pot.check_object_capability_zero_write(self.root, "tin_biscuit_01", "BREW_OR_DRINK")
        self.assertFalse(ok_drink)
        self.assertEqual(reason_drink, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT")
        self.assertEqual(snap, _snapshot_tree(self.root))

        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as audio_cmd_ctx:
            pot.apply_personal_objects_taxonomy_command(
                self.root,
                {
                    "action": "use_object_capability",
                    "object_id": "tin_biscuit_01",
                    "requested_capability": "AUDIO_PLAYBACK_DEVICE",
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(audio_cmd_ctx.exception.status_code, 409)
        self.assertEqual(audio_cmd_ctx.exception.reason_code, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT")

        # Add shared + private notes
        pot.add_note_to_object(
            self.root,
            object_id="tin_biscuit_01",
            note_cn="这个铁盒防潮，留着收街角票根。",
            visibility="shared",
            operation_key="op_note_shared",
            now_iso=T1,
        )
        pot.add_note_to_object(
            self.root,
            object_id="tin_biscuit_01",
            note_cn="私下记一句还没想好的草稿方向。",
            visibility="private",
            operation_key="op_note_private",
            now_iso=T1,
        )

        subj_view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T2)
        part_view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T2)
        self.assertEqual(len(subj_view["repurposed_objects"][0]["notes"]), 2)
        self.assertEqual(len(part_view["repurposed_objects"][0]["notes"]), 1)
        self.assertEqual(part_view["repurposed_objects"][0]["notes"][0]["visibility"], "shared")
        self.assertNotIn("私下记一句", json.dumps(part_view, ensure_ascii=False))

        # Object lacking STORE_ITEMS refuses store_item_in_object with 409 CAPABILITY_NOT_SUPPORTED_BY_OBJECT
        pot.register_or_repurpose_object(
            self.root,
            object_id="flat_coaster_01",
            object_kind="coaster",
            base_name_cn="软木杯垫",
            supported_capabilities=["ATTACH_LABEL", "ADD_NOTE"],
            capacity_slots=1,
            drinkware=False,
            now_iso=T1,
        )
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as no_store_ctx:
            pot.store_item_in_object(
                self.root,
                object_id="flat_coaster_01",
                item_label_cn="回形针",
                now_iso=T1,
            )
        self.assertEqual(no_store_ctx.exception.status_code, 409)
        self.assertEqual(no_store_ctx.exception.reason_code, "CAPABILITY_NOT_SUPPORTED_BY_OBJECT")

    # ------------------------------------------------------------------
    # 2. Ceramic Mug Occupancy Physics + somatic_comfort_view Bridge
    # ------------------------------------------------------------------

    def test_02_ceramic_mug_stationery_blocks_brew_and_somatic_until_emptied(self) -> None:
        # Blank root check must be 100% zero-write and non-blocking
        empty_root = self.base / "blank_somatic_root"
        empty_root.mkdir(parents=True, exist_ok=True)
        before_blank = _snapshot_tree(empty_root)
        ready0, reason0, labels0 = pot.check_vessel_ready_for_drink_zero_write(empty_root, "ceramic_mug")
        self.assertTrue(ready0)
        self.assertEqual(reason0, "NO_TAXONOMY_RECORD")
        self.assertEqual(labels0, [])
        self.assertFalse(_somatic.is_cup_occupied_by_stationery(empty_root, "ceramic_mug"))
        self.assertEqual(before_blank, _snapshot_tree(empty_root))

        # Register starter ceramic_mug (infers drinkware=True & BREW_OR_DRINK)
        mug = pot.register_or_repurpose_object(
            self.root,
            object_id="ceramic_mug",
            operation_key="op_reg_mug",
            now_iso=T0,
        )
        self.assertTrue(mug["object"]["drinkware"])
        self.assertIn("BREW_OR_DRINK", mug["object"]["supported_capabilities"])
        self.assertTrue(mug["object"]["drink_ready"])

        # Store fountain pen & brass clip inside ceramic_mug
        st1 = pot.store_item_in_object(
            self.root,
            object_id="ceramic_mug",
            item_label_cn="深蓝钢笔",
            item_kind="stationery",
            operation_key="op_mug_pen",
            now_iso=T0,
        )
        st2 = pot.store_item_in_object(
            self.root,
            object_id="ceramic_mug",
            item_label_cn="黄铜书签夹",
            item_kind="stationery",
            operation_key="op_mug_clip",
            now_iso=T0,
        )
        self.assertTrue(st2["object"]["occupied_for_drink"])
        self.assertFalse(st2["object"]["drink_ready"])

        # Zero-write checks block brewing in BOTH personal_objects_taxonomy AND somatic_comfort_view
        snap_before = _snapshot_tree(self.root)
        ready, reason, labels = pot.check_vessel_ready_for_drink_zero_write(self.root, "ceramic_mug")
        self.assertFalse(ready)
        self.assertEqual(reason, "VESSEL_OCCUPIED_BY_STORED_ITEMS")
        self.assertEqual(labels, ["深蓝钢笔", "黄铜书签夹"])
        self.assertTrue(pot.is_vessel_occupied_non_drink_zero_write(self.root, "ceramic_mug"))
        self.assertTrue(_somatic.is_cup_occupied_by_stationery(self.root, "ceramic_mug"))
        self.assertEqual(snap_before, _snapshot_tree(self.root))

        # somatic_comfort_view offer & drink_care both refuse 409 CUP_OCCUPIED_BY_STATIONERY
        code_offer, res_offer = _somatic.mutate_somatic_comfort_view(
            self.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T1,
        )
        self.assertEqual(code_offer, 409)
        self.assertEqual(res_offer["reason_code"], "CUP_OCCUPIED_BY_STATIONERY")

        code_drink, res_drink = _somatic.drink_care(
            self.root,
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            container_id="ceramic_mug",
            now_iso=T1,
        )
        self.assertEqual(code_drink, 409)
        self.assertEqual(res_drink["reason_code"], "CUP_OCCUPIED_BY_STATIONERY")

        # Removing only 1 of 2 items leaves cup still occupied
        rm1 = pot.remove_stored_item_from_object(
            self.root,
            object_id="ceramic_mug",
            stored_item_id=st1["stored_item"]["stored_item_id"],
            operation_key="op_rm_pen",
            now_iso=T1,
        )
        self.assertEqual(rm1["remaining_count"], 1)
        self.assertFalse(rm1["drink_ready"])
        self.assertTrue(_somatic.is_cup_occupied_by_stationery(self.root, "ceramic_mug"))

        # Emptying remaining stationery frees the mug cleanly
        rm_all = pot.remove_stored_item_from_object(
            self.root,
            object_id="ceramic_mug",
            remove_all=True,
            operation_key="op_empty_mug",
            now_iso=T2,
        )
        self.assertEqual(rm_all["remaining_count"], 0)
        self.assertTrue(rm_all["drink_ready"])
        ready_after, reason_after, labels_after = pot.check_vessel_ready_for_drink_zero_write(self.root, "ceramic_mug")
        self.assertTrue(ready_after)
        self.assertEqual(reason_after, "READY")
        self.assertEqual(labels_after, [])
        self.assertFalse(_somatic.is_cup_occupied_by_stationery(self.root, "ceramic_mug"))

        # Now offering, taking, and drinking hot tea in ceramic_mug succeeds!
        code_ok_offer, _ = _somatic.mutate_somatic_comfort_view(
            self.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T2,
        )
        self.assertEqual(code_ok_offer, 200)
        code_take, _ = _somatic.mutate_somatic_comfort_view(
            self.root,
            {"action": "take", "slot": "drink"},
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T2,
        )
        self.assertEqual(code_take, 200)
        code_consume, res_consume = _somatic.drink_care(
            self.root,
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            container_id="ceramic_mug",
            operation_key="op_drink_mug_free",
            now_iso=T2,
        )
        self.assertEqual(code_consume, 200, res_consume)
        self.assertTrue(res_consume["consumed"])

    # ------------------------------------------------------------------
    # 3. Blank-Start Custom Categories & Revision History
    # ------------------------------------------------------------------

    def test_03_blank_start_custom_categories_move_revision_history_and_revise(self) -> None:
        snap_init = _snapshot_tree(self.root)
        blank_view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T0)
        self.assertEqual(snap_init, _snapshot_tree(self.root))
        self.assertTrue(blank_view["zero_write"])
        self.assertEqual(blank_view["llm_calls"], 0)
        self.assertFalse(blank_view["state_exists"])
        self.assertEqual(blank_view["custom_categories"], [])
        self.assertEqual(blank_view["categorized_items"], [])
        self.assertEqual(blank_view["repurposed_objects"], [])

        # Self-invent two categories from blank state
        cat_a = pot.create_custom_category(
            self.root,
            title_cn="我还没有被说服",
            scope_kinds=["reading", "stance"],
            description_cn="论据还不够充分、先留着观察的内容",
            visibility="shared",
            operation_key="op_cat_a",
            now_iso=T0,
        )
        cat_b = pot.create_custom_category(
            self.root,
            title_cn="以后想重新看看",
            scope_kinds=["reading", "draft"],
            description_cn="过一阵子再翻出来对照经历的材料",
            visibility="shared",
            operation_key="op_cat_b",
            now_iso=T0,
        )
        cat_a_id = cat_a["category_id"]
        cat_b_id = cat_b["category_id"]

        # Assign reading booklet into "我还没有被说服"
        assigned = pot.assign_or_move_category_item(
            self.root,
            category_id=cat_a_id,
            target_kind="reading_booklet",
            target_ref="booklet:slow_city_notes",
            target_title_cn="慢城散步笔记册",
            annotation_cn="前半本结论下得太早，我还存疑。",
            visibility="shared",
            operation_key="op_assign_booklet",
            now_iso=T1,
        )
        self.assertFalse(assigned["moved"])
        self.assertEqual(assigned["item"]["revision_history"], [])

        # Move same item into "以后想重新看看" -> appends full dated revision_history entry
        moved = pot.assign_or_move_category_item(
            self.root,
            category_id=cat_b_id,
            target_kind="reading_booklet",
            target_ref="booklet:slow_city_notes",
            target_title_cn="慢城散步笔记册",
            annotation_cn="后半本关于街角观察的部分值得隔阵子重看。",
            reason_for_move_cn="读完后半本的具体例子后，不再单纯排斥，但仍保留前半本疑问",
            visibility="shared",
            operation_key="op_move_booklet",
            now_iso=T2,
        )
        self.assertTrue(moved["moved"])
        self.assertEqual(moved["item"]["item_id"], assigned["item"]["item_id"])
        self.assertEqual(moved["item"]["category_id"], cat_b_id)
        self.assertEqual(len(moved["item"]["revision_history"]), 1)
        rev = moved["item"]["revision_history"][0]
        self.assertEqual(rev["from_category_id"], cat_a_id)
        self.assertEqual(rev["from_category_title_cn"], "我还没有被说服")
        self.assertEqual(rev["to_category_id"], cat_b_id)
        self.assertEqual(rev["to_category_title_cn"], "以后想重新看看")
        self.assertEqual(rev["prior_annotation_cn"], "前半本结论下得太早，我还存疑。")
        self.assertEqual(rev["new_annotation_cn"], "后半本关于街角观察的部分值得隔阵子重看。")
        self.assertIn("不再单纯排斥", rev["reason_for_move_cn"])
        self.assertEqual(rev["moved_at"], T2)

        # Rename cat_b and archive cat_a -> history stays intact, archived category rejects new items
        renamed = pot.revise_custom_category(
            self.root,
            category_id=cat_b_id,
            title_cn="有一部分值得留下",
            reason_cn="收进来的几篇都更偏向局部有启发",
            operation_key="op_rename_b",
            now_iso=T3,
        )
        self.assertEqual(renamed["category"]["title_cn"], "有一部分值得留下")
        self.assertEqual(len(renamed["category"]["title_history"]), 1)

        pot.revise_custom_category(
            self.root,
            category_id=cat_a_id,
            archived=True,
            operation_key="op_archive_a",
            now_iso=T3,
        )
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as arch_ctx:
            pot.assign_or_move_category_item(
                self.root,
                category_id=cat_a_id,
                target_kind="draft",
                target_ref="draft:new_one",
                target_title_cn="新草稿",
                now_iso=T3,
            )
        self.assertEqual(arch_ctx.exception.status_code, 409)
        self.assertEqual(arch_ctx.exception.reason_code, "CATEGORY_ARCHIVED")

        view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T3)
        self.assertEqual(len(view["categorized_items"]), 1)
        self.assertEqual(view["categorized_items"][0]["category_title_cn"], "有一部分值得留下")
        self.assertEqual(len(view["categorized_items"][0]["revision_history"]), 1)

    # ------------------------------------------------------------------
    # 4. Blocked-Attempt Capability Collector
    # ------------------------------------------------------------------

    def test_04_blocked_capability_collector_filters_temporary_and_clusters_missing(self) -> None:
        # Every temporary world blocker is filtered out cleanly with 0 disk writes
        snap_before = _snapshot_tree(self.root)
        for code in sorted(pot.TEMPORARY_WORLD_BLOCKER_CODES):
            res = pot.record_blocked_attempt(
                self.root,
                goal_cn="买一本刚上架的旧书或泡一杯热茶",
                attempted_action_cn="执行常规世界动作",
                blocker_code=code,
                blocker_detail_cn=f"临时状态：{code}",
                now_iso=T0,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["classification"], "WORLD_CONDITION_TEMPORARY")
            self.assertFalse(res["logged_as_capability_gap"])
            self.assertTrue(res["zero_write"])
            self.assertEqual(res["llm_calls"], 0)
        self.assertEqual(snap_before, _snapshot_tree(self.root))

        # 3 related attach attempts merge into 1 canonical cluster: ATTACH_MEDIA_OR_CLIPPING
        a1 = pot.record_blocked_attempt(
            self.root,
            goal_cn="把街角旧书店剪报贴进读书笔记",
            attempted_action_cn="贴剪报图片到笔记页",
            blocker_code="MISSING_PRIMITIVE:ATTACH_IMAGE",
            target_ref="note:bookshop_clip_1",
            operation_key="op_att_1",
            now_iso=T0,
        )
        a2 = pot.record_blocked_attempt(
            self.root,
            goal_cn="把压干的银杏叶照片附在卡片旁",
            attempted_action_cn="贴照片附件",
            blocker_code="CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
            target_ref="note:leaf_photo_2",
            operation_key="op_att_2",
            now_iso=T1,
        )
        a3 = pot.record_blocked_attempt(
            self.root,
            goal_cn="把烘焙铺小票截图夹贴进清单",
            attempted_action_cn="插入贴纸与附图",
            blocker_code="UNSUPPORTED_CAPABILITY_TEMPLATE",
            target_ref="note:receipt_clip_3",
            operation_key="op_att_3",
            now_iso=T2,
        )
        self.assertEqual(a1["cluster_id"], "ATTACH_MEDIA_OR_CLIPPING")
        self.assertEqual(a2["cluster_id"], "ATTACH_MEDIA_OR_CLIPPING")
        self.assertEqual(a3["cluster_id"], "ATTACH_MEDIA_OR_CLIPPING")
        self.assertEqual(a3["classification"], "MISSING_CAPABILITY")
        self.assertTrue(a3["logged_as_capability_gap"])
        self.assertEqual(a3["occurrence_count"], 3)
        self.assertEqual(len(a3["cluster"]["sample_attempts"]), 3)

        # Add 2 map annotation attempts + 1 audio attempt + 1 custom primitive
        pot.record_blocked_attempt(
            self.root,
            goal_cn="在街区地图上标记雨天少积水的绕行路线",
            attempted_action_cn="地图标注路线标记",
            blocker_code="MISSING_PRIMITIVE:MAP_PIN",
            operation_key="op_map_1",
            now_iso=T2,
        )
        pot.record_blocked_attempt(
            self.root,
            goal_cn="在手绘平面图上给书店加标点",
            attempted_action_cn="添加空间标点",
            blocker_code="UNSUPPORTED_CAPABILITY_TEMPLATE",
            operation_key="op_map_2",
            now_iso=T2,
        )
        pot.record_blocked_attempt(
            self.root,
            goal_cn="用贴了收音机标签的铁盒听广播",
            attempted_action_cn="播放广播音频",
            blocker_code="CAPABILITY_NOT_SUPPORTED_BY_OBJECT",
            operation_key="op_aud_1",
            now_iso=T2,
        )
        cust = pot.record_blocked_attempt(
            self.root,
            goal_cn="记录调色盘干湿比例",
            attempted_action_cn="调色记录",
            blocker_code="MISSING_PRIMITIVE:WATERCOLOR_MIXING",
            operation_key="op_cust_1",
            now_iso=T2,
        )
        self.assertEqual(cust["cluster_id"], "CUSTOM:watercolor_mixing")

        # Verify ranking order: (-occurrence_count, cluster_id)
        view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T3)
        ranked_ids = [(r["cluster_id"], r["occurrence_count"]) for r in view["ranked_capability_gaps"]]
        self.assertEqual(
            ranked_ids,
            [
                ("ATTACH_MEDIA_OR_CLIPPING", 3),
                ("SPATIAL_MAP_ANNOTATION", 2),
                ("AUDIO_PLAYBACK_DEVICE", 1),
                ("CUSTOM:watercolor_mixing", 1),
            ],
        )
        self.assertEqual(view["capability_gaps_summary"]["total_clusters"], 4)
        self.assertEqual(view["capability_gaps_summary"]["total_occurrences"], 7)

        # Verify sample_attempts stays bounded at MAX_SAMPLE_ATTEMPTS_PER_CLUSTER (5) & deduplicates identical attempts
        for idx in range(4, 9):
            pot.record_blocked_attempt(
                self.root,
                goal_cn=f"第{idx}次想把剪报图片贴在书页旁",
                attempted_action_cn=f"贴图操作_{idx}",
                blocker_code="MISSING_PRIMITIVE:ATTACH_IMAGE",
                operation_key=f"op_att_{idx}",
                now_iso=T3,
            )
        # Repeat identical text as #8 -> occurrence increments, sample deduplicated
        dup_text = pot.record_blocked_attempt(
            self.root,
            goal_cn="第8次想把剪报图片贴在书页旁",
            attempted_action_cn="贴图操作_8",
            blocker_code="MISSING_PRIMITIVE:ATTACH_IMAGE",
            operation_key="op_att_8_again",
            now_iso=T3,
        )
        self.assertEqual(dup_text["occurrence_count"], 9)
        self.assertEqual(len(dup_text["cluster"]["sample_attempts"]), pot.MAX_SAMPLE_ATTEMPTS_PER_CLUSTER)
        self.assertEqual(dup_text["cluster"]["sample_attempts"][-1]["seen_count"], 2)

    # ------------------------------------------------------------------
    # 5. Private Visibility Withholding & Subject-Only Access Control
    # ------------------------------------------------------------------

    def test_05_private_visibility_withholding_and_actor_side_guard(self) -> None:
        # Partner write command refused with 403 FORBIDDEN_ACTOR_SIDE (zero-write)
        snap = _snapshot_tree(self.root)
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as p_ctx:
            pot.apply_personal_objects_taxonomy_command(
                self.root,
                {"action": "create_custom_category", "title_cn": "伴侣代建分类"},
                actor_side="partner",
                now_iso=T0,
            )
        self.assertEqual(p_ctx.exception.status_code, 403)
        self.assertEqual(p_ctx.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")
        self.assertEqual(snap, _snapshot_tree(self.root))

        # Smuggled forbidden keys refused with 400 FORBIDDEN_PAYLOAD_FIELD
        for bad_key in ("actor_side", "clear_reservations", "force_capabilities"):
            with self.assertRaises(pot.PersonalObjectsTaxonomyError) as smug_ctx:
                pot.apply_personal_objects_taxonomy_command(
                    self.root,
                    {"action": "create_custom_category", "title_cn": "测试", bad_key: True},
                    actor_side="subject",
                    now_iso=T0,
                )
            self.assertEqual(smug_ctx.exception.status_code, 400)
            self.assertEqual(smug_ctx.exception.reason_code, "FORBIDDEN_PAYLOAD_FIELD")

        # Populate mixed shared + private objects, categories, items, and capability gaps
        pot.register_or_repurpose_object(
            self.root,
            object_id="box_shared",
            object_kind="paper_card_box",
            custom_label_cn="公开卡片盒",
            visibility="shared",
            now_iso=T0,
        )
        pot.register_or_repurpose_object(
            self.root,
            object_id="box_private",
            object_kind="tea_canister",
            custom_label_cn="私密茶罐",
            visibility="private",
            now_iso=T0,
        )
        c_priv = pot.create_custom_category(
            self.root,
            title_cn="暂不公开的疑问架",
            visibility="private",
            now_iso=T0,
        )
        c_pub = pot.create_custom_category(
            self.root,
            title_cn="可以聊一聊的摘录",
            visibility="shared",
            now_iso=T0,
        )
        # Assign item in private category first, then move into shared category -> history must redact private title for partner!
        pot.assign_or_move_category_item(
            self.root,
            category_id=c_priv["category_id"],
            target_kind="reading_booklet",
            target_ref="booklet:quiet_essay",
            target_title_cn="安静散文小册",
            annotation_cn="先自己想想",
            visibility="private",
            now_iso=T0,
        )
        pot.assign_or_move_category_item(
            self.root,
            category_id=c_pub["category_id"],
            target_kind="reading_booklet",
            target_ref="booklet:quiet_essay",
            target_title_cn="安静散文小册",
            annotation_cn="现在愿意放出来一起看",
            reason_for_move_cn="理清了思路",
            visibility="shared",
            now_iso=T1,
        )
        pot.assign_or_move_category_item(
            self.root,
            category_id=c_pub["category_id"],
            target_kind="draft",
            target_ref="draft:secret_fragment",
            target_title_cn="未公开草稿片段",
            visibility="private",
            now_iso=T1,
        )
        pot.record_blocked_attempt(
            self.root,
            goal_cn="想把私密照片贴进日记",
            attempted_action_cn="贴私密照片",
            blocker_code="MISSING_PRIMITIVE:ATTACH_IMAGE",
            visibility="private",
            now_iso=T1,
        )

        part = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T2)
        subj = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T2)

        self.assertEqual([o["object_id"] for o in part["repurposed_objects"]], ["box_shared"])
        self.assertEqual(len(subj["repurposed_objects"]), 2)

        self.assertEqual([c["title_cn"] for c in part["custom_categories"]], ["可以聊一聊的摘录"])
        self.assertEqual(len(subj["custom_categories"]), 2)

        self.assertEqual([i["target_ref"] for i in part["categorized_items"]], ["booklet:quiet_essay"])
        self.assertEqual(
            part["categorized_items"][0]["revision_history"][0]["from_category_title_cn"],
            "未公开分类",
        )
        self.assertEqual(
            subj["categorized_items"][0]["revision_history"][0]["from_category_title_cn"],
            "暂不公开的疑问架",
        )
        self.assertEqual(part["blocked_capability_log"], [])
        self.assertEqual(len(subj["blocked_capability_log"]), 1)

        part_blob = json.dumps(part, ensure_ascii=False)
        self.assertNotIn("私密茶罐", part_blob)
        self.assertNotIn("暂不公开的疑问架", part_blob)
        self.assertNotIn("未公开草稿片段", part_blob)
        self.assertNotIn("私密照片", part_blob)

    # ------------------------------------------------------------------
    # 6. 10-Thread Concurrency, Idempotency & 0600 LF Persistence
    # ------------------------------------------------------------------

    def test_06_ten_thread_concurrency_idempotency_and_0600_lf_file(self) -> None:
        pot.register_or_repurpose_object(
            self.root,
            object_id="race_tin",
            object_kind="cookie_tin",
            capacity_slots=4,
            emptied_from_consumable=True,
            now_iso=T0,
        )

        # 10 threads racing for 4 slots in race_tin -> strictly 4 succeed, 6 get 409 OBJECT_CAPACITY_FULL
        barrier = threading.Barrier(10)
        ok_results: list[dict[str, Any]] = []
        full_errors: list[str] = []
        unexpected_errors: list[BaseException] = []
        guard = threading.Lock()

        def _store_worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                res = pot.store_item_in_object(
                    self.root,
                    object_id="race_tin",
                    item_label_cn=f"并发票根_{idx}",
                    operation_key=f"op_race_store_{idx}",
                    now_iso=T1,
                )
                with guard:
                    ok_results.append(res)
            except pot.PersonalObjectsTaxonomyError as exc:
                with guard:
                    if exc.status_code == 409 and exc.reason_code == "OBJECT_CAPACITY_FULL":
                        full_errors.append(exc.reason_code)
                    else:
                        unexpected_errors.append(exc)
            except BaseException as exc:  # pragma: no cover
                with guard:
                    unexpected_errors.append(exc)

        threads = [threading.Thread(target=_store_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(unexpected_errors, [])
        self.assertEqual(len(ok_results), 4)
        self.assertEqual(len(full_errors), 6)

        # 10 threads concurrently recording 10 distinct capability attempts -> occurrence_count == 10
        cap_barrier = threading.Barrier(10)
        cap_errors: list[BaseException] = []

        def _cap_worker(idx: int) -> None:
            try:
                cap_barrier.wait(timeout=10)
                pot.record_blocked_attempt(
                    self.root,
                    goal_cn=f"并发测试多日风干手作_{idx}",
                    attempted_action_cn=f"多日风干与发酵计时_{idx}",
                    blocker_code="MISSING_PRIMITIVE:TEMPORAL_CRAFT_PROCESS",
                    operation_key=f"op_race_cap_{idx}",
                    now_iso=T2,
                )
            except BaseException as exc:  # pragma: no cover
                with guard:
                    cap_errors.append(exc)

        cap_threads = [threading.Thread(target=_cap_worker, args=(i,)) for i in range(10)]
        for t in cap_threads:
            t.start()
        for t in cap_threads:
            t.join(timeout=15)

        self.assertEqual(cap_errors, [])
        view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T2)
        self.assertEqual(view["repurposed_objects"][0]["stored_count"], 4)
        craft_cluster = next(
            c for c in view["blocked_capability_log"] if c["cluster_id"] == "TEMPORAL_CRAFT_PROCESS"
        )
        self.assertEqual(craft_cluster["occurrence_count"], 10)
        self.assertEqual(len(craft_cluster["sample_attempts"]), pot.MAX_SAMPLE_ATTEMPTS_PER_CLUSTER)

        # 10 threads replaying the SAME operation_key -> exactly 1 applied, 9 replayed, total count +1 (11)
        dup_barrier = threading.Barrier(10)
        dup_results: list[dict[str, Any]] = []

        def _dup_worker() -> None:
            dup_barrier.wait(timeout=10)
            res = pot.record_blocked_attempt(
                self.root,
                goal_cn="幂等重试多日发酵计时",
                attempted_action_cn="发酵计时",
                blocker_code="MISSING_PRIMITIVE:TEMPORAL_CRAFT_PROCESS",
                operation_key="op_idem_shared_01",
                now_iso=T3,
            )
            with guard:
                dup_results.append(res)

        dup_threads = [threading.Thread(target=_dup_worker) for _ in range(10)]
        for t in dup_threads:
            t.start()
        for t in dup_threads:
            t.join(timeout=15)

        self.assertEqual(len(dup_results), 10)
        applied_count = sum(1 for r in dup_results if not r.get("already_applied"))
        replayed_count = sum(1 for r in dup_results if r.get("already_applied"))
        self.assertEqual(applied_count, 1)
        self.assertEqual(replayed_count, 9)

        view_after_dup = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T3)
        craft_after = next(
            c for c in view_after_dup["blocked_capability_log"] if c["cluster_id"] == "TEMPORAL_CRAFT_PROCESS"
        )
        self.assertEqual(craft_after["occurrence_count"], 11)

        # Same operation_key with conflicting payload -> 409 OPERATION_KEY_CONFLICT
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as conf_ctx:
            pot.record_blocked_attempt(
                self.root,
                goal_cn="完全不同的目标内容",
                attempted_action_cn="发酵计时",
                blocker_code="MISSING_PRIMITIVE:TEMPORAL_CRAFT_PROCESS",
                operation_key="op_idem_shared_01",
                now_iso=T3,
            )
        self.assertEqual(conf_ctx.exception.status_code, 409)
        self.assertEqual(conf_ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Persisted file is pure UTF-8 LF with 0600 permissions on POSIX
        state_file = pot.personal_objects_taxonomy_state_path(self.root)
        raw_bytes = state_file.read_bytes()
        self.assertEqual(raw_bytes.count(b"\r"), 0)
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"))
        parsed = json.loads(raw_bytes.decode("utf-8"))
        self.assertEqual(parsed["schema"], pot.SCHEMA)
        if os.name == "posix":
            mode = stat.S_IMODE(state_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

    # ------------------------------------------------------------------
    # 7. Symlink Rejection (Write & Read)
    # ------------------------------------------------------------------

    def test_07_symlink_rejected_on_write_and_read(self) -> None:
        if os.name != "posix":  # pragma: no cover - POSIX symlink verification runs on WSL
            self.skipTest("POSIX symlink test")

        sym_root = self.base / "sym_day_root"
        os.symlink(str(self.root), str(sym_root))
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as ctx1:
            pot.create_custom_category(sym_root, title_cn="不应写入软链目录", now_iso=T0)
        self.assertEqual(ctx1.exception.status_code, 400)
        self.assertEqual(ctx1.exception.reason_code, "UNSAFE_SYMLINK")

        read_sym = pot.read_personal_objects_taxonomy_zero_write(sym_root, actor_side="subject", now_iso=T0)
        self.assertTrue(read_sym["symlink_rejected"])
        self.assertEqual(read_sym["custom_categories"], [])

        # Symlink on state file itself
        world_dir = self.root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        outside = self.base / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        os.symlink(str(outside), str(world_dir / "personal_objects_taxonomy.json"))
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as ctx2:
            pot.register_or_repurpose_object(self.root, object_id="ceramic_mug", now_iso=T0)
        self.assertEqual(ctx2.exception.status_code, 400)
        self.assertEqual(ctx2.exception.reason_code, "UNSAFE_SYMLINK")

    # ------------------------------------------------------------------
    # 8. Unified Forget Barrier Filtering, Fail-Closed & Zero-Write Reads
    # ------------------------------------------------------------------

    def test_08_forget_barrier_filters_all_domains_and_fail_closed_zero_write(self) -> None:
        pot.register_or_repurpose_object(self.root, object_id="tin_keep", object_kind="cookie_tin", now_iso=T0)
        pot.register_or_repurpose_object(self.root, object_id="tin_forget", object_kind="tea_canister", now_iso=T0)
        pot.register_or_repurpose_object(self.root, object_id="ceramic_mug", now_iso=T0)

        kept_si = pot.store_item_in_object(
            self.root, object_id="tin_keep", item_label_cn="保留的书签", now_iso=T0
        )["stored_item"]["stored_item_id"]
        forget_si = pot.store_item_in_object(
            self.root, object_id="tin_keep", item_label_cn="待遗忘的旧票根", now_iso=T0
        )["stored_item"]["stored_item_id"]
        forget_mug_si = pot.store_item_in_object(
            self.root, object_id="ceramic_mug", item_label_cn="待遗忘的签字笔", now_iso=T0
        )["stored_item"]["stored_item_id"]
        forget_note = pot.add_note_to_object(
            self.root, object_id="tin_keep", note_cn="待遗忘的盒子便签", visibility="shared", now_iso=T0
        )["note"]["note_id"]

        cat_keep = pot.create_custom_category(self.root, title_cn="保留分类", now_iso=T0)["category_id"]
        cat_forget = pot.create_custom_category(self.root, title_cn="待遗忘分类", now_iso=T0)["category_id"]
        pot.assign_or_move_category_item(
            self.root,
            category_id=cat_keep,
            target_kind="reading_booklet",
            target_ref="ref_keep_1",
            target_title_cn="保留阅读册",
            now_iso=T0,
        )
        pot.assign_or_move_category_item(
            self.root,
            category_id=cat_forget,
            target_kind="reading_booklet",
            target_ref="ref_forget_1",
            target_title_cn="遗忘分类下阅读册",
            now_iso=T0,
        )
        pot.record_blocked_attempt(
            self.root,
            goal_cn="想在遗忘目标上贴图",
            attempted_action_cn="贴图附件",
            blocker_code="MISSING_PRIMITIVE:ATTACH_IMAGE",
            target_ref="target_cap_forget",
            now_iso=T0,
        )
        pot.record_blocked_attempt(
            self.root,
            goal_cn="想在保留地图上标点",
            attempted_action_cn="地图标注",
            blocker_code="MISSING_PRIMITIVE:SPATIAL_MAP_ANNOTATION",
            target_ref="target_cap_keep",
            now_iso=T0,
        )

        f_code, f_res = _rlf.submit_forget_command(
            self.root,
            command_text="忘掉相关盒子、分类和记录",
            operation_id="op_forget_v09_objects_01",
            explicit_scope=[
                "tin_forget",
                forget_si,
                forget_mug_si,
                forget_note,
                cat_forget,
                "target_cap_forget",
            ],
            explicit_confirmation=True,
            now_iso=T1,
        )
        self.assertEqual(f_code, 200, f_res)

        snap_before = _snapshot_tree(self.root)
        post_part = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T2)
        post_subj = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T2)
        mug_ready, mug_reason, mug_labels = pot.check_vessel_ready_for_drink_zero_write(self.root, "ceramic_mug")
        self.assertEqual(snap_before, _snapshot_tree(self.root))

        for view in (post_part, post_subj):
            self.assertTrue(view["zero_write"])
            self.assertEqual(view["llm_calls"], 0)
            self.assertTrue(view["forget_barrier_active"])
            self.assertFalse(view["forget_barrier_fail_closed"])
            obj_ids = {o["object_id"] for o in view["repurposed_objects"]}
            self.assertIn("tin_keep", obj_ids)
            self.assertNotIn("tin_forget", obj_ids)

            tin_keep = next(o for o in view["repurposed_objects"] if o["object_id"] == "tin_keep")
            self.assertEqual([it["stored_item_id"] for it in tin_keep["stored_items"]], [kept_si])
            self.assertEqual(tin_keep["notes"], [])

            self.assertEqual([c["category_id"] for c in view["custom_categories"]], [cat_keep])
            self.assertEqual([i["target_ref"] for i in view["categorized_items"]], ["ref_keep_1"])
            self.assertEqual(
                [c["cluster_id"] for c in view["blocked_capability_log"]],
                ["SPATIAL_MAP_ANNOTATION"],
            )

        # Forgotten stationery item label must NOT leak from check_vessel_ready_for_drink_zero_write
        self.assertTrue(mug_ready)
        self.assertEqual(mug_reason, "READY")
        self.assertEqual(mug_labels, [])

        # Corrupt control ledger -> fail-closed with 0 disk writes
        ctrl_path = self.root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_path.write_text("{corrupt-json", encoding="utf-8")
        snap_corrupt = _snapshot_tree(self.root)
        closed_view = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T3)
        ready_closed, reason_closed, labels_closed = pot.check_vessel_ready_for_drink_zero_write(
            self.root, "ceramic_mug"
        )
        self.assertEqual(snap_corrupt, _snapshot_tree(self.root))
        self.assertTrue(closed_view["forget_barrier_fail_closed"])
        self.assertEqual(closed_view["repurposed_objects"], [])
        self.assertEqual(closed_view["custom_categories"], [])
        self.assertEqual(closed_view["categorized_items"], [])
        self.assertEqual(closed_view["blocked_capability_log"], [])
        self.assertTrue(ready_closed)
        self.assertEqual(reason_closed, "FORGET_BARRIER_FAIL_CLOSED")
        self.assertEqual(labels_closed, [])

    def test_08b_repurpose_preserves_private_remove_checks_barrier_and_strict_bools(self) -> None:
        # 1. String "false" for boolean flags must raise 400 INVALID_FIELDS instead of coerced True
        for field_name, action_name, extra in (
            ("emptied_from_consumable", "register_or_repurpose_object", {"object_id": "tin_bool", "base_name_cn": "铁盒", "emptied_from_consumable": "false"}),
            ("remove_all", "remove_stored_item_from_object", {"object_id": "tin_bool", "remove_all": "false"}),
        ):
            with self.assertRaises(pot.PersonalObjectsTaxonomyError, msg=field_name) as bool_err:
                pot.apply_personal_objects_taxonomy_command(
                    self.root,
                    {"action": action_name, **extra},
                    actor_side="subject",
                    now_iso=T0,
                )
            self.assertEqual(bool_err.exception.status_code, 400)
            self.assertEqual(bool_err.exception.reason_code, "INVALID_FIELDS")

        # 2. Repurposing an existing visibility='private' object without visibility preserves 'private'
        pot.apply_personal_objects_taxonomy_command(
            self.root,
            {
                "action": "register_or_repurpose_object",
                "object_id": "tin_priv_keep",
                "object_kind": "cookie_tin",
                "base_name_cn": "私人小铁盒",
                "visibility": "private",
            },
            actor_side="subject",
            now_iso=T0,
        )
        pot.apply_personal_objects_taxonomy_command(
            self.root,
            {
                "action": "register_or_repurpose_object",
                "object_id": "tin_priv_keep",
                "object_kind": "cookie_tin",
                "base_name_cn": "私人小铁盒",
                "custom_label_cn": "只给自己看",
            },
            actor_side="subject",
            now_iso=T1,
        )
        part_v = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="partner", now_iso=T1)
        subj_v = pot.read_personal_objects_taxonomy_zero_write(self.root, actor_side="subject", now_iso=T1)
        self.assertFalse(any(o["object_id"] == "tin_priv_keep" for o in part_v["repurposed_objects"]))
        priv_obj = next(o for o in subj_v["repurposed_objects"] if o["object_id"] == "tin_priv_keep")
        self.assertEqual(priv_obj["visibility"], "private")

        # 3. remove_stored_item_from_object honors forget barrier (hides blocked note/item & fails closed on corrupt ledger)
        stored = pot.store_item_in_object(
            self.root,
            object_id="tin_priv_keep",
            item_label_cn="隐私小纸条",
            now_iso=T1,
        )
        note_res = pot.add_note_to_object(
            self.root,
            object_id="tin_priv_keep",
            note_cn="遗忘测试备注",
            visibility="shared",
            now_iso=T1,
        )
        si_id = stored["stored_item"]["stored_item_id"]
        note_id = note_res["note"]["note_id"]
        f_code, _ = _rlf.submit_forget_command(
            self.root,
            command_text="忘记备注与小纸条",
            operation_id="op_forget_remove_guard_01",
            explicit_scope=[si_id, note_id],
            explicit_confirmation=True,
            now_iso=T2,
        )
        self.assertEqual(f_code, 200)

        rem = pot.remove_stored_item_from_object(
            self.root,
            object_id="tin_priv_keep",
            stored_item_id=si_id,
            now_iso=T2,
        )
        self.assertEqual(rem["removed_items"], [])
        self.assertEqual(rem["object"]["notes"], [])

        ctrl_path = self.root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_path.write_text("{corrupt-json", encoding="utf-8")
        with self.assertRaises(pot.PersonalObjectsTaxonomyError) as fc_err:
            pot.remove_stored_item_from_object(
                self.root,
                object_id="tin_priv_keep",
                remove_all=True,
                now_iso=T3,
            )
        self.assertEqual(fc_err.exception.status_code, 503)
        self.assertEqual(fc_err.exception.reason_code, "FORGET_BARRIER_FAIL_CLOSED")

    # ------------------------------------------------------------------
    # 9. Source Hygiene & 9 Sealed Files Invariant
    # ------------------------------------------------------------------

    def test_09_hygiene_lf_no_bom_zero_emoji_neutral_ta_and_sealed_files(self) -> None:
        owned_files = (
            "components/world/scripts/personal_objects_taxonomy.py",
            "components/phone/somatic_comfort_view.py",
            "tests/test_v09_personal_objects_and_taxonomy.py",
        )
        for rel in owned_files:
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertEqual(raw.count(b"\r"), 0, f"CRLF found in {rel}")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {rel}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So symbols found in {rel}: {emojis[:5]}")
            if rel != "components/phone/somatic_comfort_view.py":
                self.assertEqual(text.count("\u5979"), 0, f"Pronoun leak in {rel}")
                self.assertEqual(text.count("\u4ed6"), 0, f"Pronoun leak in {rel}")
                self.assertEqual(text.count("\u5343\u4ee3"), 0, f"Hardcoded name leak in {rel}")

        for rel, expected_sha in SEALED_FILES_SHA256.items():
            actual_sha = hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, f"Sealed file modified: {rel}")


if __name__ == "__main__":
    unittest.main()
