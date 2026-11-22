#!/usr/bin/env python3
"""看法修订谱系、物件容纳与位置印象分离、双人书桌附着共在与餐桌份数守恒单元测试。"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import sensory_character_view as scv  # noqa: E402
from components.phone import stance_containment_view as view  # noqa: E402

T0 = "2026-11-12T14:00:00+08:00"
T_WARM = "2026-11-12T14:25:00+08:00"
T_COOLED = "2026-11-12T15:10:00+08:00"
T_LATE = "2026-11-12T18:00:00+08:00"


class StanceContainmentViewTest(unittest.TestCase):
    """Comprehensive deterministic unit tests (>=28 cases) for ``stance_containment_view``."""

    def test_01_ast_zero_random_and_zero_llm_imports(self) -> None:
        source_path = ROOT / "components" / "phone" / "stance_containment_view.py"
        source_text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source_text)
        forbidden_modules = {"random", "secrets", "openai", "anthropic", "httpx", "requests"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden_modules)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden_modules)

    def test_02_absent_file_read_is_100_percent_zero_write_and_neutral_ta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            partner_proj = view.read_stance_containment(root, now=T0, subject_view=False)
            subject_proj = view.read_stance_containment(root, now=T0, subject_view=True)

            self.assertFalse((root / "phone").exists())
            self.assertFalse(view.stance_containment_state_path(root).exists())

            self.assertEqual(partner_proj["schema"], "phone.stance.containment.v1")
            self.assertEqual(partner_proj["revision"], 0)
            self.assertEqual(partner_proj["view"], "partner_default")
            self.assertEqual(subject_proj["view"], "subject")
            self.assertEqual(partner_proj["character"]["display_name"], "TA")
            self.assertEqual(partner_proj["character"]["pronouns"], "TA")
            self.assertFalse(partner_proj["boundaries"]["locked_to_chiyo"])
            self.assertNotIn("千代", json.dumps(partner_proj, ensure_ascii=False))

    def test_03_dynamic_character_pack_and_read_character_pack_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            haru_state = scv.apply_select_preset(
                scv.default_character_pack_state(), preset_id="haru", now_iso=T0
            )
            scv.write_character_pack_state(root, haru_state)
            proj = view.read_stance_containment(root, now=T0, subject_view=True)
            self.assertEqual(proj["character"]["display_name"], "晴")
            self.assertEqual(proj["character"]["pronouns"], "他")
            self.assertIn("晴", proj["shared_desk_table"]["copresence"]["status_line"])
            self.assertFalse(view.stance_containment_state_path(root).exists())

            fake_pack_fn = mock.MagicMock(
                return_value={
                    "active_pack": {
                        "pack_id": "custom_aki",
                        "display_name": "秋",
                        "pronouns": "她",
                    }
                }
            )
            with mock.patch.object(scv, "read_character_pack", fake_pack_fn, create=True):
                proj2 = view.read_stance_containment(root, now=T0, subject_view=False)
                fake_pack_fn.assert_called_once_with(root, subject_view=True)
                self.assertEqual(proj2["character"]["display_name"], "秋")
                self.assertEqual(proj2["character"]["pronouns"], "她")

    def test_04_corrupt_state_fails_closed_on_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = view.stance_containment_state_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{corrupt-json", encoding="utf-8")

            degraded = view.read_stance_containment(root, now=T0, subject_view=False)
            self.assertEqual(degraded["state_status"], "degraded")
            self.assertEqual(
                degraded["state_reason_code"], "STANCE_CONTAINMENT_STATE_CORRUPT"
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "{corrupt-json")

            with self.assertRaises(view.StanceContainmentConflictError) as ctx:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "partner_bring_snack",
                        "title": "热麦茶",
                        "portions": 2,
                    },
                    now=T0,
                    subject_view=False,
                )
            self.assertEqual(ctx.exception.reason_code, "STANCE_CONTAINMENT_STATE_CORRUPT")

    # -----------------------------------------------------------------------------------------
    # Subdomain 1: Stance Revisions (Doc 30 + Review P1 #6)
    # -----------------------------------------------------------------------------------------

    def test_05_closed_facet_enum_and_multi_facet_coexistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(view.StanceContainmentValidationError) as bad_facet:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "propose_stance",
                        "scope_ref": "book:shadow-notes",
                        "source_revision": "rev-1",
                        "page_or_anchor": "cover",
                        "facet": "composition",
                        "text": "非法方面键",
                    },
                    now=T0,
                    subject_view=True,
                )
            self.assertEqual(bad_facet.exception.reason_code, "STANCE_FACET_INVALID")

            r1 = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:shadow-notes",
                    "source_revision": "rev-1",
                    "page_or_anchor": "cover",
                    "facet": "appearance",
                    "text": "这个封面配色不太喜欢",
                    "shared_with_partner": True,
                },
                now=T0,
                subject_view=True,
            )
            r2 = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:shadow-notes",
                    "source_revision": "rev-1",
                    "page_or_anchor": 2,
                    "facet": "method_fit",
                    "text": "第 2 页保留暗部的办法值得试试",
                    "shared_with_partner": True,
                },
                now=T0,
                subject_view=True,
            )
            self.assertEqual(r1["revision"], 1)
            self.assertEqual(r2["revision"], 2)
            current = r2["stance_revisions"]["current_stances"]
            self.assertEqual(len(current), 2)
            self.assertEqual({c["facet"] for c in current}, {"appearance", "method_fit"})

    def test_06_stance_privacy_default_unshared_and_partner_stripping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:essay-01",
                    "source_revision": "v1",
                    "page_or_anchor": "p1",
                    "facet": "content",
                    "text": "未公开的私密阅读批注正文",
                    "private_note": "只给自己看的小想法",
                },
                now=T0,
                subject_view=True,
            )
            stance_id = created["stance"]["stance_id"]
            self.assertFalse(created["stance"]["shared_with_partner"])

            partner_read = view.read_stance_containment(root, now=T0, subject_view=False)
            self.assertEqual(partner_read["stance_revisions"]["current_stances"], [])
            self.assertEqual(partner_read["stance_revisions"]["lineage_history"], [])
            dumped_partner = json.dumps(partner_read, ensure_ascii=False)
            self.assertNotIn("未公开的私密阅读批注正文", dumped_partner)
            self.assertNotIn("只给自己看的小想法", dumped_partner)
            self.assertNotIn("total_non_withdrawn_count", partner_read["stance_revisions"])

            # Share the stance; text appears in partner view, but private_note stays stripped!
            view.apply_stance_containment_action(
                root,
                {
                    "action": "set_stance_sharing",
                    "stance_id": stance_id,
                    "shared_with_partner": True,
                },
                now=T0,
                subject_view=True,
            )
            partner_after = view.read_stance_containment(root, now=T0, subject_view=False)
            self.assertEqual(len(partner_after["stance_revisions"]["current_stances"]), 1)
            shared_row = partner_after["stance_revisions"]["current_stances"][0]
            self.assertEqual(shared_row["text"], "未公开的私密阅读批注正文")
            self.assertNotIn("private_note", shared_row)

    def test_07_fix_stance_typo_cas_and_preserves_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            res = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:craft",
                    "source_revision": "v1",
                    "page_or_anchor": "p3",
                    "facet": "content",
                    "text": "这段留白处理很清晰（有错字）",
                },
                now=T0,
                subject_view=True,
            )
            sid = res["stance"]["stance_id"]

            # Wrong expected_version fails with 409 CAS conflict
            with self.assertRaises(view.StanceContainmentConflictError) as cas_err:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "fix_stance_typo",
                        "stance_id": sid,
                        "expected_version": 2,
                        "text": "这段留白处理很清晰",
                    },
                    now=T0,
                    subject_view=True,
                )
            self.assertEqual(cas_err.exception.reason_code, "STANCE_VERSION_CONFLICT")

            fixed = view.apply_stance_containment_action(
                root,
                {
                    "action": "fix_stance_typo",
                    "stance_id": sid,
                    "expected_version": 1,
                    "text": "这段留白处理很清晰",
                },
                now=T0,
                subject_view=True,
            )
            self.assertEqual(fixed["stance"]["stance_id"], sid)
            self.assertEqual(fixed["stance"]["text_version"], 2)
            self.assertIsNone(fixed["stance"]["replaces_stance_id"])
            self.assertIsNone(fixed["stance"]["superseded_by"])
            self.assertEqual(len(fixed["stance_revisions"]["lineage_history"]), 1)

    def test_08_revise_stance_bidirectional_lineage_never_overwrites_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1 = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:lighting",
                    "source_revision": "v1",
                    "page_or_anchor": "p1",
                    "facet": "method_fit",
                    "text": "起初觉得硬光勾边太生硬，不太适合当前稿子",
                    "shared_with_partner": False,
                },
                now=T0,
                subject_view=True,
            )["stance"]

            rev_res = view.apply_stance_containment_action(
                root,
                {
                    "action": "revise_stance",
                    "replaces_stance_id": s1["stance_id"],
                    "page_or_anchor": "p2",
                    "text": "读到第 2 页后改观了：局部硬光留暗部很适合雨夜屋檐",
                    "shared_with_partner": True,
                },
                now=T_WARM,
                subject_view=True,
            )
            s2 = rev_res["stance"]
            self.assertNotEqual(s2["stance_id"], s1["stance_id"])
            self.assertEqual(s2["replaces_stance_id"], s1["stance_id"])
            self.assertEqual(s2["lineage_depth"], 1)

            # Subject view sees current_stances=[s2], and lineage_history=[s1(SUPERSEDED), s2(ACTIVE)]
            subj = view.read_stance_containment(root, now=T_WARM, subject_view=True)
            self.assertEqual(
                [x["stance_id"] for x in subj["stance_revisions"]["current_stances"]],
                [s2["stance_id"]],
            )
            hist_by_id = {
                x["stance_id"]: x for x in subj["stance_revisions"]["lineage_history"]
            }
            self.assertEqual(hist_by_id[s1["stance_id"]]["status"], "SUPERSEDED")
            self.assertEqual(hist_by_id[s1["stance_id"]]["superseded_by"], s2["stance_id"])
            self.assertEqual(
                hist_by_id[s1["stance_id"]]["text"],
                "起初觉得硬光勾边太生硬，不太适合当前稿子",
            )

            # Partner view sees s2, NEVER leaks unshared s1 text or ID!
            partner = view.read_stance_containment(root, now=T_WARM, subject_view=False)
            self.assertEqual(len(partner["stance_revisions"]["current_stances"]), 1)
            p_s2 = partner["stance_revisions"]["current_stances"][0]
            self.assertIsNone(p_s2["replaces_stance_id"])
            self.assertTrue(p_s2["has_prior_revision"])
            self.assertNotIn(
                "起初觉得硬光勾边太生硬", json.dumps(partner, ensure_ascii=False)
            )

            # Racing second revise on already-superseded s1 fails with 409
            with self.assertRaises(view.StanceContainmentConflictError) as dup_rev:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "revise_stance",
                        "replaces_stance_id": s1["stance_id"],
                        "text": "并发竞争覆盖旧立场",
                    },
                    now=T_WARM,
                    subject_view=True,
                )
            self.assertEqual(dup_rev.exception.reason_code, "STANCE_ALREADY_SUPERSEDED")

    def test_09_mark_source_updated_marks_old_stance_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1 = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "doc:manual",
                    "source_revision": "rev-1",
                    "page_or_anchor": "p1",
                    "facet": "content",
                    "text": "针对第一版第 1 页的看法",
                    "shared_with_partner": True,
                },
                now=T0,
                subject_view=True,
            )["stance"]

            upd = view.apply_stance_containment_action(
                root,
                {
                    "action": "mark_source_updated",
                    "scope_ref": "doc:manual",
                    "new_source_revision": "rev-2",
                },
                now=T_WARM,
                subject_view=False,
            )
            self.assertEqual(upd["stale_stance_ids"], [s1["stance_id"]])
            self.assertEqual(upd["stance_revisions"]["current_stances"], [])
            self.assertEqual(len(upd["stance_revisions"]["stale_stances"]), 1)
            self.assertEqual(
                upd["stance_revisions"]["stale_stances"][0]["status"],
                "SOURCE_REVISION_STALE",
            )

            # Submitting new stance claiming stale rev-1 is rejected
            with self.assertRaises(view.StanceContainmentConflictError) as stale_sub:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "propose_stance",
                        "scope_ref": "doc:manual",
                        "source_revision": "rev-1",
                        "page_or_anchor": "p2",
                        "facet": "content",
                        "text": "试图绑在旧版上",
                    },
                    now=T_WARM,
                    subject_view=True,
                )
            self.assertEqual(
                stale_sub.exception.reason_code, "STANCE_SOURCE_REVISION_MISMATCH"
            )

    def test_10_revoke_stance_source_purges_and_never_resurrects_on_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "doc:withdrawn-book",
                    "source_revision": "v1",
                    "page_or_anchor": "p1",
                    "facet": "content",
                    "text": "撤权后绝对不能残留或复活的敏感正文",
                    "private_note": "撤权私密备注",
                    "shared_with_partner": True,
                    "operation_key": "op-propose-withdrawn-1",
                },
                now=T0,
                subject_view=True,
            )

            revoked = view.apply_stance_containment_action(
                root,
                {
                    "action": "revoke_stance_source",
                    "scope_ref": "doc:withdrawn-book",
                    "reason": "author_revoked_access",
                },
                now=T_WARM,
                subject_view=False,
            )
            self.assertEqual(revoked["withdrawn_count"], 1)

            # Rebuild index & check both subject and partner projections + raw disk file
            rebuilt = view.apply_stance_containment_action(
                root, {"action": "rebuild_stance_index"}, now=T_COOLED, subject_view=True
            )
            self.assertEqual(rebuilt["rebuilt_active_stance_ids"], [])
            self.assertEqual(rebuilt["stance_revisions"]["current_stances"], [])
            self.assertEqual(rebuilt["stance_revisions"]["lineage_history"], [])

            partner_proj = view.read_stance_containment(root, now=T_COOLED, subject_view=False)
            self.assertEqual(partner_proj["stance_revisions"]["current_stances"], [])
            self.assertEqual(partner_proj["stance_revisions"]["lineage_history"], [])

            raw_disk = view.stance_containment_state_path(root).read_text(encoding="utf-8")
            self.assertNotIn("撤权后绝对不能残留或复活的敏感正文", raw_disk)
            self.assertNotIn("撤权私密备注", raw_disk)

            # Replaying old operation_key or proposing new stance on withdrawn source fails 409
            with self.assertRaises(view.StanceContainmentConflictError) as replay_err:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "propose_stance",
                        "scope_ref": "doc:withdrawn-book",
                        "source_revision": "v1",
                        "page_or_anchor": "p1",
                        "facet": "content",
                        "text": "撤权后绝对不能残留或复活的敏感正文",
                        "private_note": "撤权私密备注",
                        "shared_with_partner": True,
                        "operation_key": "op-propose-withdrawn-1",
                    },
                    now=T_COOLED,
                    subject_view=True,
                )
            self.assertEqual(replay_err.exception.reason_code, "STANCE_SOURCE_WITHDRAWN")

    def test_11_operation_key_idempotent_replay_and_mismatch_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "action": "propose_stance",
                "scope_ref": "book:idem",
                "source_revision": "v1",
                "page_or_anchor": "p1",
                "facet": "appearance",
                "text": "装帧很素净",
                "operation_key": "op-idem-001",
            }
            first = view.apply_stance_containment_action(
                root, payload, now=T0, subject_view=True
            )
            second = view.apply_stance_containment_action(
                root, payload, now=T_WARM, subject_view=True
            )
            self.assertEqual(first["revision"], 1)
            self.assertEqual(second["revision"], 1)
            self.assertEqual(second["status"], "already_applied")
            self.assertTrue(second["replayed"])
            self.assertFalse(second["changed"])
            self.assertEqual(len(second["stance_revisions"]["current_stances"]), 1)

            # Same operation_key with changed text -> 409 OPERATION_KEY_CONFLICT
            mutated = dict(payload, text="偷偷换掉正文")
            with self.assertRaises(view.StanceContainmentConflictError) as ctx:
                view.apply_stance_containment_action(
                    root, mutated, now=T_WARM, subject_view=True
                )
            self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

    # -----------------------------------------------------------------------------------------
    # Subdomain 2: Object Containment & Spatial Impressions (Doc 31 + Review P1 #7)
    # -----------------------------------------------------------------------------------------

    def test_12_container_capacity_partial_completion_leaves_third_pen_in_hand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # pen_cup capacity is 2
            r1 = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            self.assertTrue(r1["ok"])
            self.assertTrue(r1["placed_in_container"])

            r2 = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "gel_pen",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            self.assertTrue(r2["ok"])
            self.assertEqual(r2["object_containment"]["hand_slots"]["right_hand"], None)

            # 3rd pen: pick up succeeds, pen_cup is FULL -> stays in right_hand!
            r3 = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "sketch_pencil",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            self.assertFalse(r3["ok"])
            self.assertEqual(r3["status"], "partial_completed")
            self.assertEqual(r3["reason_code"], "CONTAINER_FULL")
            self.assertTrue(r3["partial_completed"])
            self.assertFalse(r3["rolled_back"])
            self.assertEqual(r3["held_in_hand"], "right_hand")
            self.assertEqual(
                r3["hand_slots"], {"left_hand": None, "right_hand": "sketch_pencil"}
            )

            # Verify persisted disk state via fresh read_stance_containment (no fake rollback!)
            fresh = view.read_stance_containment(root, now=T0, subject_view=True)
            self.assertEqual(
                fresh["object_containment"]["hand_slots"]["right_hand"], "sketch_pencil"
            )
            pencil_row = next(
                i
                for i in fresh["object_containment"]["items"]
                if i["item_id"] == "sketch_pencil"
            )
            self.assertEqual(pencil_row["placement_kind"], "hand")
            self.assertEqual(pencil_row["held_in_hand"], "right_hand")

    def test_13_unreachable_container_partial_completion_keeps_item_in_hand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Subject is at study_desk; brass_bookmark is at study_desk, tote_bag is at bookshelf
            res = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "brass_bookmark",
                    "container_id": "tote_bag",
                    "hand_slot": "left_hand",
                },
                now=T0,
                subject_view=True,
            )
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "partial_completed")
            self.assertEqual(res["reason_code"], "CONTAINER_NOT_REACHABLE")
            self.assertTrue(res["partial_completed"])
            self.assertFalse(res["rolled_back"])
            self.assertEqual(res["held_in_hand"], "left_hand")

            # Now move subject to bookshelf and complete putting into tote_bag
            view.apply_stance_containment_action(
                root,
                {"action": "set_subject_location", "place_id": "bookshelf"},
                now=T0,
                subject_view=True,
            )
            done = view.apply_stance_containment_action(
                root,
                {
                    "action": "put_in_container",
                    "item_id": "brass_bookmark",
                    "container_id": "tote_bag",
                },
                now=T0,
                subject_view=True,
            )
            self.assertTrue(done["ok"])
            self.assertIsNone(done["object_containment"]["hand_slots"]["left_hand"])
            tote = next(
                c
                for c in done["object_containment"]["containers"]
                if c["container_id"] == "tote_bag"
            )
            self.assertIn("brass_bookmark", tote["child_item_ids"])

    def test_14_mug_derived_use_and_class_rejection_without_magic_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial = view.read_stance_containment(root, now=T0, subject_view=True)
            mug0 = next(
                c
                for c in initial["object_containment"]["containers"]
                if c["container_id"] == "ceramic_mug"
            )
            self.assertEqual(mug0["derived_use"], "empty_drinkable")
            self.assertTrue(mug0["can_pour_drink_now"])

            after_insert = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "ceramic_mug",
                },
                now=T0,
                subject_view=True,
            )
            mug1 = next(
                c
                for c in after_insert["object_containment"]["containers"]
                if c["container_id"] == "ceramic_mug"
            )
            self.assertEqual(mug1["derived_use"], "holding_items")
            self.assertFalse(mug1["can_pour_drink_now"])

            # Take pen back out and place on surface -> mug reverts to empty_drinkable
            view.apply_stance_containment_action(
                root,
                {
                    "action": "take_from_container",
                    "item_id": "fountain_pen",
                    "container_id": "ceramic_mug",
                },
                now=T0,
                subject_view=True,
            )
            restored = view.apply_stance_containment_action(
                root,
                {"action": "place_on_surface", "item_id": "fountain_pen"},
                now=T0,
                subject_view=True,
            )
            mug2 = next(
                c
                for c in restored["object_containment"]["containers"]
                if c["container_id"] == "ceramic_mug"
            )
            self.assertEqual(mug2["derived_use"], "empty_drinkable")
            self.assertTrue(mug2["can_pour_drink_now"])

            # Eyewear reading_glasses rejected by ceramic_mug -> stays in hand
            rej = view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "reading_glasses",
                    "container_id": "ceramic_mug",
                },
                now=T0,
                subject_view=True,
            )
            self.assertEqual(rej["reason_code"], "CONTAINER_CLASS_REJECTED")
            self.assertEqual(rej["held_in_hand"], "right_hand")

    def test_15_partner_move_container_updates_physics_not_subject_impression_until_observe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            # Subject leaves room
            view.apply_stance_containment_action(
                root,
                {"action": "set_subject_location", "place_id": "outside", "present": False},
                now=T0,
                subject_view=True,
            )
            # Partner moves pen_cup (with fountain_pen inside) from study_desk to window_sill
            moved = view.apply_stance_containment_action(
                root,
                {
                    "action": "partner_move_container",
                    "container_id": "pen_cup",
                    "to_place_id": "window_sill",
                },
                now=T_WARM,
                subject_view=False,
            )
            self.assertEqual(moved["moved_child_item_ids"], ["fountain_pen"])
            self.assertFalse(moved["subject_impression_updated"])

            # Check subject view BEFORE observing room: the moved destination must
            # not leak. She still believes the pen cup is on study_desk and the
            # God-view staleness flag is withheld from her projection.
            before_obs = view.read_stance_containment(root, now=T_WARM, subject_view=True)
            cup_before = next(
                c
                for c in before_obs["object_containment"]["containers"]
                if c["container_id"] == "pen_cup"
            )
            pen_before = next(
                i
                for i in before_obs["object_containment"]["items"]
                if i["item_id"] == "fountain_pen"
            )
            self.assertEqual(cup_before["place_id"], "study_desk")
            self.assertEqual(pen_before["place_id"], "study_desk")
            self.assertEqual(cup_before["effective_place_id"], "study_desk")
            self.assertEqual(pen_before["effective_place_id"], "study_desk")
            self.assertEqual(cup_before["subject_last_seen_place"], "study_desk")
            self.assertEqual(pen_before["subject_last_seen_place"], "study_desk")
            self.assertNotIn("impression_stale", cup_before)
            self.assertNotIn("impression_stale", pen_before)

            # Partner (God) view is not masked: the physical move is visible cleanly.
            before_obs_partner = view.read_stance_containment(
                root, now=T_WARM, subject_view=False
            )
            cup_partner = next(
                c
                for c in before_obs_partner["object_containment"]["containers"]
                if c["container_id"] == "pen_cup"
            )
            pen_partner = next(
                i
                for i in before_obs_partner["object_containment"]["items"]
                if i["item_id"] == "fountain_pen"
            )
            self.assertEqual(cup_partner["place_id"], "window_sill")
            self.assertEqual(pen_partner["place_id"], "window_sill")
            self.assertEqual(cup_partner["effective_place_id"], "window_sill")
            self.assertEqual(pen_partner["effective_place_id"], "window_sill")
            self.assertTrue(cup_partner["impression_stale"])
            self.assertTrue(pen_partner["impression_stale"])
            pen_cue = [
                c
                for c in before_obs["object_containment"]["subject_cognitive_cues"]
                if "墨水钢笔" in c
            ][0]
            self.assertIn("study_desk", pen_cue)
            self.assertNotIn("window_sill", pen_cue)

            # Subject returns and observes study_room -> impressions refresh!
            after_obs = view.apply_stance_containment_action(
                root,
                {"action": "observe_current_room", "place_id": "study_desk"},
                now=T_COOLED,
                subject_view=True,
            )
            cup_after = next(
                c
                for c in after_obs["object_containment"]["containers"]
                if c["container_id"] == "pen_cup"
            )
            pen_after = next(
                i
                for i in after_obs["object_containment"]["items"]
                if i["item_id"] == "fountain_pen"
            )
            self.assertEqual(cup_after["subject_last_seen_place"], "window_sill")
            self.assertEqual(pen_after["subject_last_seen_place"], "window_sill")
            self.assertEqual(cup_after["effective_place_id"], "window_sill")
            self.assertEqual(pen_after["effective_place_id"], "window_sill")
            self.assertFalse(cup_after["not_at_last_seen_place"])
            self.assertFalse(pen_after["not_at_last_seen_place"])
            # impression_stale is a God-view fact and stays withheld from her view.
            self.assertNotIn("impression_stale", cup_after)
            self.assertNotIn("impression_stale", pen_after)

    def test_16_cross_room_move_marks_missing_from_old_spot_without_guessing_destination(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Partner moves ceramic_mug to dining_table (kitchen_dining room)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "partner_move_container",
                    "container_id": "ceramic_mug",
                    "to_place_id": "dining_table",
                },
                now=T0,
                subject_view=False,
            )
            # Subject observes study_room: sees mug is gone from study_desk, still doesn't know dining_table!
            obs_study = view.apply_stance_containment_action(
                root,
                {"action": "observe_current_room"},
                now=T_WARM,
                subject_view=True,
            )
            self.assertIn("ceramic_mug", obs_study["missing_from_last_seen_ids"])
            mug_mid = next(
                c
                for c in obs_study["object_containment"]["containers"]
                if c["container_id"] == "ceramic_mug"
            )
            self.assertTrue(mug_mid["not_at_last_seen_place"])
            # She knows it is gone from study_desk but must not learn the destination.
            self.assertEqual(mug_mid["effective_place_id"], "study_desk")
            self.assertEqual(mug_mid["subject_last_seen_place"], "study_desk")
            self.assertNotIn("impression_stale", mug_mid)

            # Once subject walks to dining_table and observes kitchen_dining, impression updates
            obs_kitchen = view.apply_stance_containment_action(
                root,
                {"action": "observe_current_room", "place_id": "dining_table"},
                now=T_COOLED,
                subject_view=True,
            )
            mug_final = next(
                c
                for c in obs_kitchen["object_containment"]["containers"]
                if c["container_id"] == "ceramic_mug"
            )
            self.assertEqual(mug_final["subject_last_seen_place"], "dining_table")
            self.assertFalse(mug_final["not_at_last_seen_place"])
            self.assertNotIn("impression_stale", mug_final)

    def test_17_non_empty_container_removal_refused_and_nested_container_forbidden(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "memo_clip",
                    "container_id": "desk_tray",
                },
                now=T0,
                subject_view=True,
            )
            with self.assertRaises(view.StanceContainmentConflictError) as rm_err:
                view.apply_stance_containment_action(
                    root,
                    {"action": "remove_container", "container_id": "desk_tray"},
                    now=T0,
                    subject_view=True,
                )
            self.assertEqual(rm_err.exception.reason_code, "CONTAINER_NOT_EMPTY")

            with self.assertRaises(view.StanceContainmentConflictError) as nest_err:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "pick_and_put_in_container",
                        "item_id": "pen_cup",
                        "container_id": "desk_tray",
                    },
                    now=T0,
                    subject_view=True,
                )
            self.assertEqual(nest_err.exception.reason_code, "NESTED_CONTAINER_FORBIDDEN")

    def test_18_private_impression_note_withheld_in_partner_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "set_item_impression_note",
                    "target_id": "fountain_pen",
                    "private_impression_note": "这支钢笔出水顺滑，我习惯放在左手边",
                },
                now=T0,
                subject_view=True,
            )
            subj = view.read_stance_containment(root, now=T0, subject_view=True)
            partner = view.read_stance_containment(root, now=T0, subject_view=False)
            self.assertIn(
                "这支钢笔出水顺滑", json.dumps(subj, ensure_ascii=False)
            )
            self.assertNotIn(
                "这支钢笔出水顺滑", json.dumps(partner, ensure_ascii=False)
            )
            self.assertNotIn("subject_cognitive_cues", partner["object_containment"])

    # -----------------------------------------------------------------------------------------
    # Subdomain 3: Shared Desk Attached Co-Presence & Table Consumables (Docs 12/13/14 + P0 #1)
    # -----------------------------------------------------------------------------------------

    def test_19_copresence_proposal_countersign_and_all_four_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Partner proposes quiet_parallel
            prop = view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_copresence",
                    "actor": "partner",
                    "target_mode": "quiet_parallel",
                    "declared_minutes": 60,
                },
                now=T0,
                subject_view=False,
            )
            self.assertEqual(prop["shared_desk_table"]["copresence"]["mode"], "off")
            self.assertIsNotNone(
                prop["shared_desk_table"]["copresence"]["pending_proposal"]
            )

            # Partner cannot self-accept own proposal (409 SELF_COUNTERSIGN_FORBIDDEN)
            with self.assertRaises(view.StanceContainmentConflictError) as self_sign:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "respond_copresence",
                        "actor": "partner",
                        "decision": "accept",
                    },
                    now=T0,
                    subject_view=False,
                )
            self.assertEqual(self_sign.exception.reason_code, "SELF_COUNTERSIGN_FORBIDDEN")

            # Subject accepts -> enters quiet_parallel attached to primary_activity
            acc = view.apply_stance_containment_action(
                root,
                {
                    "action": "respond_copresence",
                    "actor": "subject",
                    "decision": "accept",
                },
                now=T0,
                subject_view=True,
            )
            cp = acc["shared_desk_table"]["copresence"]
            self.assertEqual(cp["mode"], "quiet_parallel")
            self.assertEqual(cp["attached_to_activity_id"], "act-desk-work-01")
            self.assertFalse(cp["hijacks_primary_activity"])

            # Switch to focused_do_not_disturb, then break_chat, then exit to off
            s_dnd = view.apply_stance_containment_action(
                root,
                {"action": "switch_copresence_mode", "mode": "focused_do_not_disturb"},
                now=T0,
                subject_view=True,
            )
            self.assertEqual(
                s_dnd["shared_desk_table"]["copresence"]["mode"],
                "focused_do_not_disturb",
            )

            s_chat = view.apply_stance_containment_action(
                root,
                {"action": "switch_copresence_mode", "mode": "break_chat"},
                now=T0,
                subject_view=False,
            )
            self.assertEqual(
                s_chat["shared_desk_table"]["copresence"]["mode"], "break_chat"
            )

            exited = view.apply_stance_containment_action(
                root,
                {"action": "exit_copresence"},
                now=T_WARM,
                subject_view=False,
            )
            self.assertEqual(exited["shared_desk_table"]["copresence"]["mode"], "off")

    def test_20_copresence_exit_never_terminates_primary_activity_and_atomic_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "set_primary_activity",
                    "activity_id": "act-watercolor-export",
                    "label": "导出水彩分层原稿",
                    "status": "ACTIVE",
                    "interruptibility": "ATOMIC",
                    "private_note": "正在写盘收尾",
                },
                now=T0,
                subject_view=True,
            )
            # Quiet parallel attaches cleanly even during ATOMIC work
            view.apply_stance_containment_action(
                root,
                {"action": "switch_copresence_mode", "mode": "quiet_parallel"},
                now=T0,
                subject_view=False,
            )
            # Trying to switch into break_chat while primary_activity is ATOMIC -> 409!
            with self.assertRaises(view.StanceContainmentConflictError) as atomic_err:
                view.apply_stance_containment_action(
                    root,
                    {"action": "switch_copresence_mode", "mode": "break_chat"},
                    now=T0,
                    subject_view=False,
                )
            self.assertEqual(
                atomic_err.exception.reason_code, "PRIMARY_ACTIVITY_ATOMIC_BOUNDARY"
            )

            # Exiting copresence leaves primary_activity 100% ACTIVE and intact
            after_exit = view.apply_stance_containment_action(
                root, {"action": "exit_copresence"}, now=T_WARM, subject_view=False
            )
            pa = after_exit["shared_desk_table"]["primary_activity"]
            self.assertEqual(pa["activity_id"], "act-watercolor-export")
            self.assertEqual(pa["status"], "ACTIVE")
            self.assertFalse(after_exit["shared_desk_table"]["hijacks_primary_activity"])
            self.assertNotIn("private_note", pa)  # partner view strips private_note

    def test_21_copresence_hard_upper_bound_expires_deterministically_on_zero_write_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            started = view.apply_stance_containment_action(
                root,
                {
                    "action": "switch_copresence_mode",
                    "mode": "quiet_parallel",
                    "declared_minutes": 30,
                },
                now=T0,
                subject_view=True,
            )
            rev_before = started["revision"]
            mtime_before = view.stance_containment_state_path(root).stat().st_mtime_ns

            expired_proj = view.read_stance_containment(
                root, now=T_LATE, subject_view=False
            )
            self.assertEqual(
                expired_proj["shared_desk_table"]["copresence"]["mode"], "off"
            )
            self.assertTrue(
                expired_proj["shared_desk_table"]["copresence"]["expired_by_clock"]
            )
            self.assertEqual(expired_proj["revision"], rev_before)
            self.assertEqual(
                view.stance_containment_state_path(root).stat().st_mtime_ns,
                mtime_before,
            )

    def test_22_table_dish_cooling_curve_hot_warm_cooled_zero_write_on_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = view.apply_stance_containment_action(
                root,
                {
                    "action": "prepare_table_dish",
                    "title": "热焙茶一壶",
                    "kind": "tea_pot",
                    "portions": 3,
                    "initial_temperature": "hot",
                },
                now=T0,
                subject_view=True,
            )
            self.assertEqual(created["temperature_band"], "hot")
            rev_saved = created["revision"]

            warm_proj = view.read_stance_containment(root, now=T_WARM, subject_view=False)
            self.assertEqual(
                warm_proj["shared_desk_table"]["table_dishes"][0]["temperature_band"],
                "warm",
            )
            cooled_proj = view.read_stance_containment(
                root, now=T_COOLED, subject_view=False
            )
            self.assertEqual(
                cooled_proj["shared_desk_table"]["table_dishes"][0]["temperature_band"],
                "cooled",
            )
            self.assertEqual(cooled_proj["revision"], rev_saved)

    def test_23_table_portion_conservation_depletion_to_zero_and_no_infinite_refill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snack = view.apply_stance_containment_action(
                root,
                {
                    "action": "partner_bring_snack",
                    "title": "红豆鲷鱼烧两枚",
                    "kind": "snack_plate",
                    "portions": 2,
                },
                now=T0,
                subject_view=False,
            )
            dish_id = snack["dish_id"]
            self.assertTrue(snack["no_hidden_affection_score"])

            # Subject takes 1 portion -> 1 left
            t1 = view.apply_stance_containment_action(
                root,
                {"action": "take_portion", "dish_id": dish_id, "consumer": "subject"},
                now=T0,
                subject_view=True,
            )
            self.assertEqual(t1["portions_remaining"], 1)
            self.assertFalse(t1["is_empty_plate"])

            # Requesting 2 when only 1 left -> 409 INSUFFICIENT_PORTIONS_REMAINING
            with self.assertRaises(view.StanceContainmentConflictError) as over_take:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "take_portion",
                        "dish_id": dish_id,
                        "consumer": "partner",
                        "portions": 2,
                    },
                    now=T_WARM,
                    subject_view=False,
                )
            self.assertEqual(
                over_take.exception.reason_code, "INSUFFICIENT_PORTIONS_REMAINING"
            )

            # Partner takes last 1 portion -> 0 left, EMPTY plate stays on table
            t2 = view.apply_stance_containment_action(
                root,
                {"action": "take_portion", "dish_id": dish_id, "consumer": "partner"},
                now=T_WARM,
                subject_view=False,
            )
            self.assertEqual(t2["portions_remaining"], 0)
            self.assertTrue(t2["is_empty_plate"])
            dish_row = t2["shared_desk_table"]["table_dishes"][0]
            self.assertEqual(dish_row["status"], "EMPTY")
            self.assertEqual(dish_row["consumed_by_subject"], 1)
            self.assertEqual(dish_row["consumed_by_partner"], 1)
            self.assertTrue(dish_row["no_hidden_affection_score"])

            # Attempting another bite on empty plate -> 409 DISH_EMPTY_NO_PORTIONS_LEFT
            with self.assertRaises(view.StanceContainmentConflictError) as empty_err:
                view.apply_stance_containment_action(
                    root,
                    {"action": "take_portion", "dish_id": dish_id},
                    now=T_COOLED,
                    subject_view=False,
                )
            self.assertEqual(
                empty_err.exception.reason_code, "DISH_EMPTY_NO_PORTIONS_LEFT"
            )

    def test_24_clear_table_dish_leftover_requires_explicit_discard_not_intake(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tea = view.apply_stance_containment_action(
                root,
                {
                    "action": "prepare_table_dish",
                    "title": "麦茶",
                    "kind": "warm_drink",
                    "portions": 2,
                },
                now=T0,
                subject_view=True,
            )
            did = tea["dish_id"]

            # Clearing non-empty dish without discard_remaining=True -> 409
            with self.assertRaises(view.StanceContainmentConflictError) as clr_err:
                view.apply_stance_containment_action(
                    root,
                    {"action": "clear_table_dish", "dish_id": did},
                    now=T_COOLED,
                    subject_view=True,
                )
            self.assertEqual(
                clr_err.exception.reason_code, "DISH_NOT_EMPTY_REQUIRE_DISCARD_FLAG"
            )

            cleared = view.apply_stance_containment_action(
                root,
                {
                    "action": "clear_table_dish",
                    "dish_id": did,
                    "discard_remaining": True,
                },
                now=T_COOLED,
                subject_view=True,
            )
            self.assertEqual(cleared["discarded_portions"], 2)
            self.assertFalse(cleared["counted_as_intake"])
            self.assertEqual(cleared["shared_desk_table"]["table_dishes"], [])

    # -----------------------------------------------------------------------------------------
    # Validation, Permission, NotFound & CAS Conflict Contract Guards
    # -----------------------------------------------------------------------------------------

    def test_25_permission_guard_blocks_subject_only_actions_and_impersonation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for subject_action_payload in (
                {
                    "action": "propose_stance",
                    "scope_ref": "book:a",
                    "source_revision": "v1",
                    "page_or_anchor": "p1",
                    "facet": "content",
                    "text": "越权新建看法",
                },
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "pen_cup",
                },
                {"action": "observe_current_room"},
                {
                    "action": "prepare_table_dish",
                    "title": "越权泡茶",
                    "portions": 2,
                },
                {
                    "action": "propose_copresence",
                    "actor": "subject",
                    "target_mode": "quiet_parallel",
                },
                {
                    "action": "take_portion",
                    "dish_id": "dish-any",
                    "consumer": "subject",
                },
            ):
                with self.assertRaises(view.StanceContainmentPermissionError) as perm:
                    view.apply_stance_containment_action(
                        root, subject_action_payload, now=T0, subject_view=False
                    )
                self.assertEqual(perm.exception.http_status, 403)
                self.assertEqual(perm.exception.reason_code, "SUBJECT_CAPABILITY_REQUIRED")
            self.assertFalse(view.stance_containment_state_path(root).exists())

    def test_26_strict_unknown_field_foreign_key_and_empty_string_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_payloads = [
                {"action": "exit_copresence", "extra_field": 123},
                {"action": "exit_copresence", "affection_score": 10},
                {"action": "exit_copresence", "after_state": {"mode": "off"}},
                {"action": "exit_copresence", "note": ""},
                {"action": "exit_copresence", "note": "   "},
                {"action": "exit_copresence", "note": None},
                {"action": "unknown_magic_action"},
                {},
            ]
            for bad in bad_payloads:
                with self.assertRaises(view.StanceContainmentValidationError) as val_err:
                    view.apply_stance_containment_action(
                        root, bad, now=T0, subject_view=True
                    )
                self.assertEqual(val_err.exception.http_status, 400)
            self.assertFalse(view.stance_containment_state_path(root).exists())

    def test_27_not_found_404_across_all_three_subdomains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            not_found_cases = [
                {
                    "action": "fix_stance_typo",
                    "stance_id": "stance-missing",
                    "expected_version": 1,
                    "text": "修错字",
                },
                {
                    "action": "revise_stance",
                    "replaces_stance_id": "stance-missing",
                    "text": "修订不存在看法",
                },
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "item-missing",
                    "container_id": "pen_cup",
                },
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "container-missing",
                },
                {"action": "respond_copresence", "decision": "accept"},
                {"action": "take_portion", "dish_id": "dish-missing"},
            ]
            for payload in not_found_cases:
                with self.assertRaises(view.StanceContainmentNotFoundError) as nf_err:
                    view.apply_stance_containment_action(
                        root, payload, now=T0, subject_view=True
                    )
                self.assertEqual(nf_err.exception.http_status, 404)

    def test_28_expected_revision_cas_and_float_portions_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Float portions rejected by strict integer conservation rule
            with self.assertRaises(view.StanceContainmentValidationError):
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "partner_bring_snack",
                        "title": "半块饼干",
                        "portions": 1.5,
                    },
                    now=T0,
                    subject_view=False,
                )

            ok_res = view.apply_stance_containment_action(
                root,
                {
                    "action": "partner_bring_snack",
                    "title": "曲奇",
                    "portions": 2,
                    "expected_revision": 0,
                },
                now=T0,
                subject_view=False,
            )
            self.assertEqual(ok_res["revision"], 1)

            with self.assertRaises(view.StanceContainmentConflictError) as rev_err:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "partner_bring_snack",
                        "title": "第二盘曲奇",
                        "portions": 2,
                        "expected_revision": 0,
                    },
                    now=T0,
                    subject_view=False,
                )
            self.assertEqual(rev_err.exception.http_status, 409)
            self.assertEqual(rev_err.exception.reason_code, "REVISION_CONFLICT")

    def test_29_subject_move_container_updates_own_impression_and_datetime_now_input(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dt_now = datetime(2026, 11, 12, 16, 30, 0, tzinfo=timezone.utc)
            view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "gel_pen",
                    "container_id": "pen_cup",
                },
                now=dt_now,
                subject_view=True,
            )
            moved = view.apply_stance_containment_action(
                root,
                {
                    "action": "subject_move_container",
                    "container_id": "pen_cup",
                    "to_place_id": "desk_right",
                },
                now=dt_now,
                subject_view=True,
            )
            self.assertEqual(moved["moved_child_item_ids"], ["gel_pen"])
            self.assertEqual(moved["object_containment"]["stale_impression_count"], 0)
            pen_row = next(
                i
                for i in moved["object_containment"]["items"]
                if i["item_id"] == "gel_pen"
            )
            self.assertEqual(pen_row["effective_place_id"], "desk_right")
            self.assertEqual(pen_row["subject_last_seen_place"], "desk_right")
            # impression_stale is a God-view fact and stays withheld from her view.
            self.assertNotIn("impression_stale", pen_row)

    def test_30_hand_slot_full_rejects_third_pickup_atomically_zero_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view.apply_stance_containment_action(
                root,
                {"action": "pick_up_item", "item_id": "fountain_pen", "hand_slot": "right_hand"},
                now=T0,
                subject_view=True,
            )
            r2 = view.apply_stance_containment_action(
                root,
                {"action": "pick_up_item", "item_id": "gel_pen", "hand_slot": "left_hand"},
                now=T0,
                subject_view=True,
            )
            rev_before = r2["revision"]
            with self.assertRaises(view.StanceContainmentConflictError) as full_hand:
                view.apply_stance_containment_action(
                    root,
                    {
                        "action": "pick_and_put_in_container",
                        "item_id": "sketch_pencil",
                        "container_id": "pen_cup",
                    },
                    now=T0,
                    subject_view=True,
                )
            self.assertEqual(full_hand.exception.reason_code, "HAND_SLOT_OCCUPIED")
            after = view.read_stance_containment(root, now=T0, subject_view=True)
            self.assertEqual(after["revision"], rev_before)


if __name__ == "__main__":
    unittest.main()
