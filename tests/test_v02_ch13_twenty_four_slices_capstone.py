#!/usr/bin/env python3
"""Ch.13 §7 Capstone Verification: all 24 concrete lived slices end-to-end
against real backend owner modules and live ``PhoneService``.

Invariants enforced:
- ``ROOT = Path(__file__).resolve().parents[1]`` (zero hardcoded machine-specific root paths);
- ``tempfile.TemporaryDirectory()`` strictly outside repo root;
- Pure LF line endings, zero emojis, neutral third-person ("TA"), ``llm_calls == 0``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import zlib
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
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

_HERMES_VENDOR = ROOT / "vendor" / "hermes"
if _HERMES_VENDOR.exists() and str(_HERMES_VENDOR) not in sys.path:
    sys.path.append(str(_HERMES_VENDOR))
elif "agent.context_compressor" not in sys.modules:
    import types as _types

    _agent_pkg = _types.ModuleType("agent")
    _cc_mod = _types.ModuleType("agent.context_compressor")

    class _StubContextCompressor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def on_session_reset(self) -> None:
            pass

        def get_status(self) -> dict[str, Any]:
            return {}

    _cc_mod.ContextCompressor = _StubContextCompressor  # type: ignore[attr-defined]
    sys.modules["agent"] = _agent_pkg
    sys.modules["agent.context_compressor"] = _cc_mod
    if "hermes_constants" not in sys.modules:
        _hc_mod = _types.ModuleType("hermes_constants")
        _hc_mod.get_hermes_home = lambda: Path(os.environ.get("HERMES_HOME", str(ROOT)))  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = _hc_mod

import autonomy_rhythm_fixture as stack_fixture  # noqa: E402
import life_co_presence as cop  # noqa: E402
from chiyo_bundle import lived_context_bridge  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_agency_depth_engine as ade,
    v02_autobiography_skill_provenance as asp,
    v02_common_ground as cg,
    v02_crash_and_partial_reconciler as cpr,
    v02_lived_slices_and_vd_protocols as lived_slices,
    v02_recall_projection as rp,
    v02_representation_lease_forget as rlf,
    v02_runtime_governance_migration as rgm,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import workbench_curio_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v02-ch13-capstone-cap-20261118"
T0 = "2026-11-18T10:00:00+08:00"
T1 = "2026-11-18T11:30:00+08:00"
T2 = "2026-11-19T09:00:00+08:00"

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _build_bicolor_png(width: int = 32, height: int = 32) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    top = b"\x00" + bytes((220, 84, 48)) * width
    bot = b"\x00" + bytes((34, 72, 128)) * width
    raw = top * (height // 2) + bot * (height - height // 2)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


@POSIX_ONLY
class TestV02Ch13TwentyFourSlicesCapstone(unittest.TestCase):
    """All 24 concrete lived slices in Ch.13 §7 against real backend modules & PhoneService."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls._tmp = tempfile.TemporaryDirectory(prefix="v02-ch13-cap-")
        cls.temp_root = Path(cls._tmp.name)
        cls.cap = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at=T0,
        )
        cls.stack = stack_fixture.build_stack(cls.temp_root / "instance", port=0, capability=cls.cap)
        cls.service = cls.stack.service
        cls.day_root = cls.stack.root

    @classmethod
    def tearDownClass(cls) -> None:
        stack_fixture.close_stack(cls.stack)
        cls._tmp.cleanup()
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "phone/v02_honest_ux_state.json",
            "phone/v02_runtime_migration.json",
            "phone/v02_crossmodal.json",
            "phone/v02_common_ground.json",
            "phone/v02_recall_projection.json",
            "phone/v02_agency_depth.json",
            "phone/outing_bench_bookstore.json",
            "phone/home_hearth_tentative_plans.json",
            "phone/tentative_plans.json",
            "phone/desk_tomorrow_note.json",
            "governance/v02_representation_lease_forget_state.json",
            "governance/v02_vc_authority_watermark.json",
            "control_authority/v02_control_highwater_ledger.json",
            "state/v02_autobiography_skill_provenance.json",
            "state/v02_lived_slices_state.json",
            "state/v02_agency_depth_state.json",
            "state/v02_common_ground_state.json",
            "memory/v02_common_ground_state.json",
            "state/v02_crossmodal_state.json",
            "state/v02_recall_projection_state.json",
            "memory/v02_recall_projection_state.json",
            "state/v02_runtime_governance_migration.json",
            "canonical_life.sqlite3",
            "canonical_life.sqlite3-wal",
            "canonical_life.sqlite3-shm",
        ):
            (self.day_root / rel).unlink(missing_ok=True)
        workbench_curio_view.tray_path(self.day_root).unlink(missing_ok=True)
        for sub in ("workspace_ch13", "reconciler", "cop_slice", "suitcase_export"):
            dpath = self.day_root / sub
            if dpath.exists():
                shutil.rmtree(dpath, ignore_errors=True)
        self.service._world_clock = lambda: datetime(2026, 11, 18, 10, 30, tzinfo=timezone(timedelta(hours=8)))

    def test_slice_01_half_page_draft_on_desk_continues_after_restart(self) -> None:
        """Ch.13 §7 #1: 留在桌上的半页草稿 — 编辑回执->Artifact版本->Activity保存点; 重启后从实际位置继续."""
        ws = self.day_root / "workspace_ch13"
        ws.mkdir(parents=True, exist_ok=True)
        rlf._ensure_control_ledger_locked(self.day_root)
        c1, out1 = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_s01_draft",
            controller_principal_ref="owner_s01",
            subject_id=SUBJECT,
            initial_body="# Half Page Draft\nLine 1\n",
            operation_id="op_s01_create",
            title="desk_half_draft.md",
            visibility="shared",
            now_iso=T0,
        )
        self.assertEqual(c1, 200)
        head_v1 = out1["current_head"]
        rlf.bind_representation(
            self.day_root,
            artifact_id="art_s01_draft",
            workspace_root=ws,
            workspace_rel_path="desk_half_draft.md",
            operation_id="op_s01_bind",
            now_iso=T0,
        )
        c2, out2 = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_s01_draft",
            expected_head=head_v1,
            body="# Half Page Draft\nLine 1\nLine 2 checkpoint\n",
            operation_id="op_s01_commit2",
            now_iso=T1,
        )
        self.assertEqual(c2, 200)
        head_v2 = out2["current_head"]

        # Real SQLite checkpoint + simulated process restart (new store instance)
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_s01", request_id="req_s01", writer_epoch=1)
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_s01",
            request_id="req_s01",
            logical_action_id="la_s01",
            activity_title="Continue desk half draft",
            writer_epoch=1,
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"], writer_epoch=1)
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"], writer_epoch=1)
        rec.tx_t2_commit_artifact_version(
            artifact_id="art_s01_draft",
            action_id=t1["action_id"],
            content_body="# Half Page Draft\nLine 1\nLine 2 checkpoint\n",
            expected_head_version_id=None,
            projection_path=ws / "desk_half_draft_rec.md",
            writer_epoch=1,
        )
        rec.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id=td["attempt_id"],
            outcome="SUCCEEDED",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_s01",
            writer_epoch=1,
        )

        # Restart: brand-new store & tray projection check
        rec_restarted = cpr.CrashAndPartialReconcilerStore(self.day_root)
        snap = rec_restarted.snapshot_db()
        self.assertEqual(snap["artifacts"][0]["artifact_id"], "art_s01_draft")
        self.assertIn("Line 2 checkpoint", snap["artifact_versions"][0]["content_body"])
        self.assertTrue(str(snap["artifacts"][0]["head_version_id"]).startswith("ver_"))
        tray, _ = workbench_curio_view.set_tray(
            workbench_curio_view.empty_tray_state(),
            {"images": [], "materials": [], "work": {"artifact_id": "art_s01_draft", "baseline_version_id": head_v2}, "note": "half page checkpoint"},
            T1,
        )
        workbench_curio_view.write_tray_state(self.day_root, tray)

        collected = loop_bridge.collect_v02_v03_loop_candidates(self.day_root, now_iso=T2)
        self.assertEqual(collected["llm_calls"], 0)
        self.assertTrue(any(d.get("action_family") == "advance_workbench_draft" for d in collected["descriptors"].values()))
        st_code, hux = self.service.v02_honest_ux(subject_view=True)
        self.assertEqual(st_code, 200)
        self.assertEqual(hux["llm_calls"], 0)
        self.assertIn("provenance", hux["data"])

        # Continue writing on the SAME resumed artifact (v2 -> v3) from the saved checkpoint
        wb_desc = next(
            d for d in collected["descriptors"].values() if d.get("action_family") == "advance_workbench_draft"
        )
        cont_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, wb_desc["source_ref"], now_iso=T2, decision_id="dec_s01_cont"
        )
        assert cont_ev is not None
        cont_settled = loop_bridge.settle_v02_v03_round_consequences(
            self.day_root, cont_ev, now_iso=T2
        )
        self.assertTrue(cont_settled["settled"])
        self.assertIn("managed_artifact", cont_settled["slices_updated"])
        head_after_loop = cont_settled["details"]["workbench"]["committed_version_id"]
        self.assertNotEqual(head_after_loop, head_v2)
        ro_state_mid, ok_mid = rlf._load_state_readonly(self.day_root)
        self.assertTrue(ok_mid)
        self.assertEqual(ro_state_mid["artifacts"]["art_s01_draft"]["current_head"], head_after_loop)
        continued_body = "# Half Page Draft\nLine 1\nLine 2 checkpoint\nLine 3 continued from saved pin\n"
        c3, out3 = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_s01_draft",
            expected_head=head_after_loop,
            body=continued_body,
            operation_id="op_s01_commit3",
            now_iso=T2,
        )
        self.assertEqual(c3, 200)
        head_v3 = out3["current_head"]
        self.assertNotEqual(head_v3, head_v2)
        self.assertNotEqual(head_v3, head_after_loop)

        prev_rec_head = str(snap["artifacts"][0]["head_version_id"])
        rec_restarted.tx_t0_commit_decision(decision_id="dec_s01_r", request_id="req_s01_r", writer_epoch=1)
        t1_r = rec_restarted.tx_t1_commit_adoption_activity_action(
            decision_id="dec_s01_r",
            request_id="req_s01_r",
            logical_action_id="la_s01_r",
            activity_title="Resume desk half draft v3",
            writer_epoch=1,
        )
        td_r = rec_restarted.tx_td_prepare_dispatch(action_id=t1_r["action_id"], writer_epoch=1)
        rec_restarted.tx_ta_start_attempt(attempt_id=td_r["attempt_id"], writer_epoch=1)
        rec_restarted.tx_t2_commit_artifact_version(
            artifact_id="art_s01_draft",
            action_id=t1_r["action_id"],
            content_body=continued_body,
            expected_head_version_id=prev_rec_head,
            projection_path=ws / "desk_half_draft_rec.md",
            writer_epoch=1,
        )
        rec_restarted.tx_t3_admit_action_result(
            action_id=t1_r["action_id"],
            attempt_id=td_r["attempt_id"],
            outcome="SUCCEEDED",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_s01_cont",
            writer_epoch=1,
        )
        snap3 = rec_restarted.snapshot_db()
        new_rec_head = str(snap3["artifacts"][0]["head_version_id"])
        self.assertNotEqual(new_rec_head, prev_rec_head)
        head_row = next(v for v in snap3["artifact_versions"] if v["version_id"] == new_rec_head)
        c_rb, _ = rlf.rebuild_projection_from_canonical(
            self.day_root,
            artifact_id="art_s01_draft",
            workspace_root=ws,
            operation_id="op_s01_rebuild3",
            now_iso=T2,
        )
        self.assertEqual(c_rb, 200)
        self.assertIn("Line 2 checkpoint\nLine 3 continued from saved pin", head_row["content_body"])
        self.assertIn(
            "Line 2 checkpoint\nLine 3 continued from saved pin",
            (ws / "desk_half_draft.md").read_text(encoding="utf-8"),
        )

    def test_slice_02_today_no_open_old_collection_no_action(self) -> None:
        """Ch.13 §7 #2: 今天不打开旧收藏 — 已可见收藏+当下取舍->NO_ACTION; 不虚构不喜欢，也不偷偷浏览."""
        exp_a = lived_slices.run_experiment_a_affordance_x_narrative(num_tasks=12, paired_seeds=(101, 202, 303))
        self.assertEqual(exp_a["llm_calls"], 0)
        state, status = ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "collection_revisit",
                "chosen": "NO_ACTION",
                "options": ["NO_ACTION", "打开旧收藏夹", "整理旧收藏"],
                "choice_mode": "spontaneous",
                "forgone_cost": "暂不重温旧收藏条目",
                "note_cn": "看见旧收藏夹但今天重心在当前桌面草稿，先不翻旧收藏",
                "shared": True,
                "operation_key": "op_s02_no_action",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, "APPLIED")
        self.assertEqual(state["llm_calls"], 0)
        ep = state["choice_episodes"][0]
        self.assertEqual(ep["chosen"], "NO_ACTION")
        self.assertEqual(ep["resulting_status"], "TENTATIVE")

        # Zero-write GET through PhoneService
        before = _snapshot_tree(self.day_root)
        code, view = self.service.v02_agency_depth(subject_view=True)
        self.assertEqual(code, 200)
        self.assertEqual(before, _snapshot_tree(self.day_root))
        self.assertEqual(view["llm_calls"], 0)

    def test_slice_03_photo_as_palette_material_with_provenance(self) -> None:
        """Ch.13 §7 #3: 一张照片成为配色材料 — 实际取得图片->局部理解->作品新版本; 来源可溯，不晋升个人经历."""
        png_bytes = _build_bicolor_png()
        b64_png = base64.b64encode(png_bytes).decode("ascii")
        imported = self.service.album_import(
            caption="Seawall sunset reference",
            content_type="image/png",
            data=b64_png,
            operation_key="op_s03_album_import",
        )
        self.assertTrue(imported.get("ok"), imported)
        album_id = imported["data"]["artifact_id"]
        img_read = self.service.album_image(album_id)
        self.assertTrue(img_read.get("ok"), img_read)

        code_a, res_a = self.service.v02_crossmodal_action(
            payload={
                "action": "analyze_image_palette",
                "title": "Seawall sunset reference",
                "image_base64": b64_png,
                "share_with_room": True,
                "operation_key": "op_s03_palette",
            },
            subject_view=True,
        )
        self.assertEqual(code_a, 200, res_a)
        study = res_a["palette_studies"][0]
        study_id = study["study_id"]
        hex_colors = list(study["dominant_hex"])
        self.assertTrue(len(hex_colors) >= 1)

        code_w, res_w = self.service.v02_crossmodal_action(
            payload={
                "action": "apply_palette_to_work",
                "study_id": study_id,
                "work_id": "work:seawall_card_s03",
                "work_title": "Seawall color study",
                "selected_hex": hex_colors[:2],
                "usage_note_cn": "借用照片海面对比色调",
                "share_with_room": True,
                "operation_key": "op_s03_apply_work",
            },
            subject_view=True,
        )
        self.assertEqual(code_w, 200, res_w)
        # Crossmodal palette reuse does NOT fabricate personal autobiographical memory
        code_r, recall = self.service.v02_recall_projection(subject_view=True)
        self.assertEqual(code_r, 200)
        self.assertEqual(recall["data"]["direct_recall_items"], [])
        self.assertEqual(recall["llm_calls"], 0)

    def test_slice_04_read_page_without_understanding_stays_unknown(self) -> None:
        """Ch.13 §7 #4: 看过但没有读懂一页书 — 取得页面->阅读位置->理解状态未知; 不把页码当理解证明."""
        reading_before = self.service.reading()
        self.assertTrue(reading_before.get("ok"))
        self.assertEqual(reading_before.get("understanding"), "UNKNOWN")

        code, res = self.service.v02_crossmodal_action(
            payload={
                "action": "mark_reading_uncertain",
                "book_title": "海岸光学笔记",
                "page": 14,
                "passage_hint": "专业术语尚缺前提上下文",
                "share_with_room": True,
                "operation_key": "op_s04_read_uncertain",
            },
            subject_view=True,
        )
        self.assertEqual(code, 200, res)
        mark = res["reading_uncertainty_marks"][0]
        self.assertEqual(mark["page"], 14)
        self.assertEqual(mark["state"], "seen_not_understood")

        code_g, view = self.service.v02_crossmodal(subject_view=True)
        self.assertEqual(code_g, 200)
        self.assertEqual(view["data"]["reading_uncertainty_marks"][0]["state"], "seen_not_understood")
        self.assertEqual(view["llm_calls"], 0)

    def test_slice_05_phone_notification_arrived_unread_not_in_belief(self) -> None:
        """Ch.13 §7 #5: 手机通知到了但没看 — transport通知->尚未取得内容; 内容不能直接进入Belief."""
        coalescer = rgm.SemanticWakeCoalescer()
        routed = coalescer.route_event(
            family="partner_notification",
            summary_cn="收到一条新通知但尚未打开查看正文",
            is_control=False,
            lane="background",
            now_iso=T0,
        )
        self.assertEqual(routed["wake_disposition"], "EFFECTIVE_BACKGROUND_WAKE")

        code, res = self.service.v02_common_ground_action(
            payload={
                "action": "record_ground_item",
                "item_id": "cg_s05_unread_notif",
                "topic": "未读手机通知",
                "summary": "通知已送达设备横幅，内容尚未点开读取",
                "ground_status": "PRIVATE_UNSHARED",
                "item_kind": "notification",
                "delivery_state": "DELIVERED",
                "requires_agreement": False,
            },
            subject_view=True,
        )
        self.assertEqual(code, 200, res)
        cognition_cg = cg.summarize_common_ground_for_cognition(self.day_root, now_iso=T0)
        shared_ids = [i["item_id"] for i in cognition_cg.get("shared_items", [])]
        self.assertNotIn("cg_s05_unread_notif", shared_ids)

        code_p, cg_p = self.service.v02_common_ground(subject_view=False)
        self.assertEqual(code_p, 200)
        self.assertNotIn("cg_s05_unread_notif", json.dumps(cg_p, ensure_ascii=False))

    def test_slice_06_colistening_while_writing_own_work_no_global_pause(self) -> None:
        """Ch.13 §7 #6: 共同听歌仍在写自己的东西 — 有共在约定+资源不冲突->继续Activity; 回应不必全局pause."""
        _, reg = lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_s06_listen",
                "title": "共同听海边电台",
                "cadence_mode": "light_relay",
                "frequency_per_week": 3,
                "shared": True,
                "operation_key": "op_s06_reg",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(reg, "APPLIED")
        code_c, res_c = self.service.v02_honest_ux_action(
            payload={
                "action": "confirm_rescaled_copractice",
                "practice_id": "cop_s06_listen",
                "scale_mode": "light_relay",
                "confirmed": True,
                "operation_key": "op_s06_partner_confirm",
            },
            subject_view=False,
        )
        self.assertEqual(code_c, 200, res_c)
        self.assertTrue(res_c["data"]["partner_confirmed"])

        # Own writing activity continues without forced global pause
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        own_act = rec.create_unrelated_activity(decision_id="dec_s06_write", request_id="req_s06_write", title="Writing own notes")
        adv = rec.advance_unrelated_activity(activity_id=own_act["activity_id"])
        self.assertEqual(adv["status"], "COMPLETED")

    def test_slice_07_user_question_during_atomic_step(self) -> None:
        """Ch.13 §7 #7: 用户问一句话而她处于ATOMIC — 聊天表达与中断路径分开; 不破坏不可分操作."""
        class _AtomicViewCoord:
            def get_current_life_view(self) -> dict[str, Any]:
                return {
                    "foreground_activity_ref": "actv_s07_atomic_write",
                    "interruptibility": "ATOMIC",
                    "communication_availability": "BOUNDARY_DEFERRED",
                }

        class _Runtime:
            interruption_coordinator = _AtomicViewCoord()

        proto = cop.FiniteCoPresence(_Runtime(), state_root=self.day_root / "cop_slice", subject_id=SUBJECT, home=str(self.day_root))
        seen = proto.observe({"kind": "LOCAL_MESSAGE", "body": "还在忙这一段吗"})
        self.assertTrue(seen["respondable"])
        self.assertTrue(seen["foreground_atomic"])
        self.assertFalse(seen["pause_requested"])
        self.assertEqual(seen["owners_written"], 0)
        self.assertEqual(seen["communication_availability"], "BOUNDARY_DEFERRED")

        rhythm = self.service.autonomy_rhythm(subject_view=True)
        self.assertTrue(rhythm.get("ok"))

    def test_slice_08_tool_upgrade_invalidates_old_skill_pending_reverify(self) -> None:
        """Ch.13 §7 #8: 一个工具升级后旧手法失效 — 能力版本变化->Skill待复验; 不把旧成功当新环境掌握."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "skill_s08_macro",
                "tool_version": "2.4",
                "model_id": "m_base",
                "scope": "vector_pad",
                "attribution": "PRACTICED_MASTERY",
                "criteria_id": "crit_1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.18,
                "status": "VALIDATED",
            },
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "record_tool_keymap_card",
                "tool_id": "tool:vector_pad",
                "from_version": "2.4",
                "to_version": "3.0",
                "compatible_maps": [{"action_name": "toggle_grid", "old_shortcut": "Ctrl+G", "new_shortcut": "Alt+G", "verified_input": "probe_ok"}],
                "incompatible_maps": [{"action_name": "legacy_flip", "old_shortcut": "Ctrl+Shift+F", "reason": "removed_in_v3", "fallback_action": "menu_flip"}],
                "verified_probe_inputs": ["probe_ok", "probe_flip_missing"],
                "shared": True,
                "operation_key": "op_s08_keymap",
            },
            actor_side="subject",
            now_iso=T0,
        )
        asp.bump_carrier_or_tool_version(
            self.day_root,
            {"from_model_id": "m_base", "to_model_id": "m_base", "from_tool_version": "2.4", "to_tool_version": "3.0", "method_ids": ["skill_s08_macro"]},
            now_iso=T1,
        )

        _, hux = self.service.v02_honest_ux(subject_view=True)
        skills = {s["method_id"]: s for s in hux["data"]["skill_attributions"]["entries"]}
        self.assertEqual(skills["skill_s08_macro"]["status"], "UNVALIDATED_AFTER_MIGRATION")
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.day_root, now_iso=T1)
        self.assertIn("skill_s08_macro", collected["resident_context_summary"]["unvalidated_after_migration_skills"])

        reverified = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "skill_s08_macro",
                "tool_version": "3.0",
                "model_id": "m_base",
                "scope": "vector_pad",
                "bare_model_score": 0.55,
                "method_with_new_model_score": 0.85,
                "old_baseline_score": 0.80,
                "held_out_trials": 5,
            },
            now_iso=T2,
        )
        self.assertEqual(reverified["skill_release"]["status"], "VALIDATED")

    def test_slice_09_work_failure_leaves_salvaged_method_no_growth_drama(self) -> None:
        """Ch.13 §7 #9: 一次作品失败留下修补方法 — 真实失败->局部方法提案->新条件试用; 不是自述'我成长了'."""
        code_s, res_s = self.service.v02_honest_ux_action(
            payload={
                "action": "salvage_work_scrap",
                "scrap_id": "scrap_s09_glaze",
                "source_work_id": "work:failed_harbor_09",
                "source_version_id": "ver_02",
                "source_outcome": "failed",
                "fragment_title": "灰蓝海雾渐变调色参数",
                "reusable_payload": {"base": "#6E8294", "highlight": "#D4DEE6"},
                "applicability_tags": ["coastal", "watercolor"],
                "provenance_receipt": "rcpt:s09_ver02",
                "shared": True,
                "operation_key": "op_s09_salvage",
            },
            subject_view=True,
        )
        self.assertEqual(code_s, 200, res_s)
        matched = lived_slices.match_salvaged_scraps_for_task(self.day_root, task_tags=["coastal"], has_subject_capability=True, now_iso=T1)
        self.assertEqual(len(matched["matched_candidates"]), 1)
        self.assertFalse(matched["auto_inferred_liking"])

        _, reused = lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "reuse_salvaged_scrap",
                "scrap_id": "scrap_s09_glaze",
                "target_work_id": "work:new_coast_09",
                "task_tags": ["watercolor"],
                "operation_key": "op_s09_reuse",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(reused, "APPLIED")
        item = lived_slices.project_v02_lived_slices(self.day_root, has_subject_capability=True)["salvaged_scrap_folder"]["items"][0]
        self.assertFalse(item["growth_speech_fabricated"])
        self.assertFalse(item["auto_inferred_liking"])

    def test_slice_10_favorite_spot_switches_corner_without_random_contrast(self) -> None:
        """Ch.13 §7 #10: 喜欢的地方今天换一个角落 — 可见机会+当前负荷/兴趣->选择; 允许变化，不随机制造反差."""
        self.service.outing_bench_bookstore_action(
            action="visit_place",
            place_id="harbor_bench",
            note="海边长椅今天风偏大",
            note_supplied=True,
            shared=True,
            operation_key="op_s10_bench",
        )
        state, status = ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "record_choice_episode",
                "domain": "reading_spot",
                "chosen": "靠窗安静小桌",
                "options": ["靠窗安静小桌", "临街长桌", "二楼阅览角"],
                "choice_mode": "spontaneous",
                "forgone_cost": "少一点视野开阔，但负荷更低",
                "note_cn": "今天负荷偏高，靠窗小桌光线柔和更适合慢慢写完笔记",
                "shared": True,
                "operation_key": "op_s10_choice",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, "APPLIED")
        self.assertEqual(state["llm_calls"], 0)
        bench = self.service.outing_bench_bookstore(subject_view=True)
        self.assertTrue(bench.get("ok"))

    def test_slice_11_old_object_retires_method_preserves_provenance(self) -> None:
        """Ch.13 §7 #11: 一件旧物失去实际用途 — 对象保留+当前方法退休; 不因不活跃而抹掉来历."""
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "register_personal_script",
                "script_id": "scr_s11_legacy",
                "title": "旧版离线同步工具",
                "options": [
                    {"option_id": "opt_keep", "label": "导出本地索引", "used_count": 10, "maintenance_cost": 1.0},
                    {"option_id": "opt_retire", "label": "旧串口传输", "used_count": 0, "maintenance_cost": 3.0},
                ],
                "shared": True,
                "operation_key": "op_s11_reg",
            },
            actor_side="subject",
            now_iso=T0,
        )
        code_sim, res_sim = self.service.v02_honest_ux_action(
            payload={
                "action": "simplify_personal_tool",
                "script_id": "scr_s11_legacy",
                "removed_option_ids": ["opt_retire"],
                "reason": "旧串口渠道不再使用，退休对应方法保留导出能力",
                "operation_key": "op_s11_simp",
            },
            subject_view=True,
        )
        self.assertEqual(code_sim, 200, res_sim)
        state, _ = ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "settle_legacy_tool",
                "tool_id": "scr_s11_legacy",
                "title_cn": "旧版离线同步工具",
                "dependency_error_cn": "旧串口驱动已从宿主移除",
                "linked_work_refs": ["work:harbor_01"],
                "ending": "bid_farewell",
                "shared": True,
                "operation_key": "op_s11_legacy_settle",
            },
            actor_side="subject",
            now_iso=T1,
        )
        lt = state["legacy_tool_endings"][0]
        self.assertEqual(lt["status"], "ARCHIVED_FAREWELL")
        self.assertTrue(lt["linked_works_readable"])
        self.assertEqual(lt["linked_work_refs"], ["work:harbor_01"])

    def test_slice_12_wish_voluntarily_set_aside_dissolves_schedule_keeps_commitments(self) -> None:
        """Ch.13 §7 #12: 一项愿望被主动放下 — S08结束认领->安排解除; 相关真实责任另行承接."""
        ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_s12_wish",
                "title_cn": "周六画展整理小册子约定",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": T0,
                "shared": True,
                "operation_key": "op_s12_reg",
            },
            actor_side="subject",
            now_iso=T0,
        )
        with self.assertRaises(ade.V02ConflictError) as ctx_block:
            ade.apply_agency_depth_mutation(
                self.day_root,
                {
                    "action": "review_gentle_responsibility",
                    "resp_id": "resp_s12_wish",
                    "decision": "retire_keep_history",
                    "partner_agreed": False,
                    "operation_key": "op_s12_drop_no_handoff",
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx_block.exception.reason_code, "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        code, res = self.service.v02_agency_depth_action(
            payload={
                "action": "review_gentle_responsibility",
                "resp_id": "resp_s12_wish",
                "decision": "retire_keep_history",
                "partner_agreed": True,
                "operation_key": "op_s12_handoff_ok",
            },
            subject_view=True,
        )
        self.assertEqual(code, 200, res)
        self.assertEqual(res["llm_calls"], 0)

    def test_slice_13_booklet_completed_allows_idle_without_forced_next(self) -> None:
        """Ch.13 §7 #13: 一本册子确实收尾 — Artifact满足completion->Activity结束; 完成后允许IDLE."""
        rlf._ensure_control_ledger_locked(self.day_root)
        _, created = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_s13_booklet",
            controller_principal_ref="owner_s13",
            subject_id=SUBJECT,
            initial_body="# Booklet Draft\nPage 1\n",
            operation_id="op_s13_create",
            visibility="shared",
            now_iso=T0,
        )
        _, v2 = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_s13_booklet",
            expected_head=created["current_head"],
            body="# Booklet Final\nPage 1\nColophon complete.\n",
            operation_id="op_s13_final",
            now_iso=T1,
        )
        c_arch, arch = rlf.change_artifact_lifecycle(
            self.day_root,
            artifact_id="art_s13_booklet",
            target_lifecycle="ARCHIVED",
            expected_revision=v2["revision"],
            operation_id="op_s13_archive",
            now_iso=T1,
        )
        self.assertEqual(c_arch, 200)
        self.assertEqual(arch["lifecycle"], "ARCHIVED")

        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_s13", request_id="req_s13")
        t1 = rec.tx_t1_commit_adoption_activity_action(decision_id="dec_s13", request_id="req_s13", logical_action_id="la_s13")
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"])
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        rec.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id=td["attempt_id"],
            outcome="SUCCEEDED",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_s13",
        )
        ts = rec.tx_ts_create_settlement(action_id=t1["action_id"], source_fact_id="sf_s13", obligations=[("S01", "ARTIFACT", "art_s13_booklet", 1)])
        rec.tx_t4_commit_owner_effect(obligation_id=ts["obligations"][0]["obligation_id"], disposition="APPLIED")
        tc = rec.tx_tc_close_settlement(envelope_id=ts["envelope_id"])
        self.assertEqual(tc["status"], "CLOSED")
        adv = rec.advance_unrelated_activity(activity_id=t1["activity_id"])
        self.assertEqual(adv["status"], "COMPLETED")

        code_m, mig = self.service.v02_runtime_migration(subject_view=True)
        self.assertEqual(code_m, 200)
        self.assertEqual(mig["data"]["runtime_mode"]["health_state"], "QUIET_HEALTHY")

    def test_slice_14_purchased_digital_object_enters_room_after_economy_verify(self) -> None:
        """Ch.13 §7 #14: 买来的数字物件进入房间 — 经济结果核实->对象归属/位置; 不先生成拥有台词再补交易."""
        wallet_before = self.service.wallet()
        self.assertTrue(wallet_before.get("ok"), wallet_before)
        bal_before = int(wallet_before["data"]["balance"])

        # Attempting to purchase a non-existent / unverified item refuses without debiting balance
        bad_buy = self.service.purchase("nonexistent_magic_lamp", "op_s14_bad_buy")
        self.assertFalse(bad_buy.get("ok"))
        self.assertEqual(int(self.service.wallet()["data"]["balance"]), bal_before)

        # Real purchase of virtual_snack_wafer (240 JPY) from phone storefront debits wallet & updates inventory
        good_buy = self.service.purchase("virtual_snack_wafer", "op_s14_good_wafer")
        self.assertTrue(good_buy.get("ok"), good_buy)
        bal_after = int(self.service.wallet()["data"]["balance"])
        self.assertEqual(bal_after, bal_before - 240)
        inv = self.service.inventory()
        self.assertTrue(inv.get("ok"), inv)
        self.assertIn("virtual_snack_wafer", json.dumps(inv, ensure_ascii=False))

    def test_slice_15_message_timeout_stays_unknown_queries_operation(self) -> None:
        """Ch.13 §7 #15: 一条消息超时 — Action UNKNOWN->operation查询; 不盲重发、不宣告对方拒绝."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.create_decision(decision_id="dec_s15")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_s15",
            request_id="req_s15",
            logical_action_id="la_s15_msg",
            provider_supports_idempotency=False,
            conclusive_not_found=False,
            max_reconcile_queries=3,
        )
        action_id = t1["action_id"]
        td = rec.tx_td_prepare_dispatch(action_id=action_id)
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
        exec_res = rec.execute_external_attempt(action_id=action_id, attempt_id=td["attempt_id"], drop_ack=True)
        self.assertTrue(exec_res["ack_lost"])
        rec.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)

        for _ in range(3):
            r = rec.reconcile_action(action_id=action_id)
        self.assertEqual(r["phase"], "QUIESCED_UNRESOLVED")
        self.assertEqual(r["outcome"], "UNKNOWN")
        self.assertEqual(rec.provider.total_invocations(), 1)

        cg.record_ground_item(self.day_root, item_id="cg_s15_msg", topic="问候消息", summary="刚出门买咖啡", ground_status="SHARED", item_kind="message", delivery_state="UNKNOWN", now_iso=T0)
        code, hux = self.service.v02_honest_ux(subject_view=True)
        self.assertEqual(code, 200)
        self.assertEqual(hux["data"]["crash_reconciler_summary"]["unknown_actions"], 1)
        self.assertEqual(hux["data"]["provenance"]["epistemic_status"], "UNKNOWN")

    def test_slice_16_local_relationship_misunderstanding_repaired_scoped_only(self) -> None:
        """Ch.13 §7 #16: 关系局部误会修复 — 对象限定的事实->协商->相处规则; 不改所有对象的关系状态."""
        cg.register_object_belief(self.day_root, object_id="obj_kettle", object_label="烧水壶", spot="kitchen_counter", shared_with_partner=True, now_iso=T0)
        cg.register_object_belief(self.day_root, object_id="obj_umbrella", object_label="折叠伞", spot="door_hook", shared_with_partner=True, now_iso=T0)
        cg.record_partner_unseen_move(self.day_root, object_id="obj_kettle", new_spot="pantry_shelf", object_label="烧水壶", witnessed_by_subject=False, now_iso=T0)
        att = cg.attempt_use_or_fetch_object(self.day_root, object_id="obj_kettle", now_iso=T1)
        self.assertEqual(att["outcome"], "MISSED_AT_REMEMBERED_SPOT")
        self.assertTrue(att["should_search_or_ask"])

        resolved = cg.resolve_unseen_move_with_partner(self.day_root, object_id="obj_kettle", partner_told_spot="pantry_shelf", now_iso=T1)
        self.assertEqual(resolved["updated_believed_spot"], "pantry_shelf")
        self.service.v02_agency_depth_action(
            payload={
                "action": "propose_shared_gentle_rule",
                "rule_id": "rule_s16_kettle",
                "scope": "kitchen_utensils",
                "rule_text_cn": "水壶换位置时顺手留一句提示",
                "shared": True,
                "operation_key": "op_s16_rule",
            },
            subject_view=True,
        )
        cg_view = cg.read_v02_common_ground_view(self.day_root, viewer="partner", now_iso=T1)
        beliefs = {b["object_id"]: b for b in cg_view["object_beliefs"]}
        self.assertEqual(beliefs["obj_umbrella"]["subject_believed_spot"], "door_hook")
        self.assertEqual(beliefs["obj_kettle"]["subject_believed_spot"], "pantry_shelf")

    def test_slice_17_private_note_shares_single_sentence_zero_raw_leak(self) -> None:
        """Ch.13 §7 #17: 私人笔记只分享一句 — 授权披露view->发送Action; 不泄露未披露原材料."""
        raw_secret = "RAW_PRIVATE_NOTE_DO_NOT_DISCLOSE_777"
        disclosed_sentence = "今天在海堤边把调色试完了。"
        rlf._ensure_control_ledger_locked(self.day_root)
        rlf.ingest_source(self.day_root, source_id="src_s17_priv", text=f"{raw_secret}\n{disclosed_sentence}", visibility="private", operation_id="op_s17_src", now_iso=T0)

        p_hash = hashlib.sha256(disclosed_sentence.encode("utf-8")).hexdigest()
        c_g, g_resp = rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_s17_one",
            grantor="operator_admin",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="single_sentence",
            typed_target={"kind": "artifact", "id": "partner_chat"},
            audience="partner",
            operation_id="op_s17_grant",
            max_uses=1,
            payload_binding=p_hash,
            now_iso=T0,
        )
        self.assertEqual(c_g, 200, g_resp)
        delivered: list[str] = []
        c_d, d_res = rlf.execute_guarded_dispatch(
            self.day_root,
            grant_id="grant_s17_one",
            logical_action_id="la_s17_share",
            payload_hash=p_hash,
            payload_text=disclosed_sentence,
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="partner_chat",
            audience="partner",
            operation_id="op_s17_disp",
            source_id="src_s17_priv",
            transport_fn=lambda p: delivered.append(str(p["payload_text"])) or "SUCCEEDED",
            now_iso=T0,
        )
        self.assertEqual(c_d, 200, d_res)
        self.assertEqual(delivered, [disclosed_sentence])
        self.assertNotIn(raw_secret, json.dumps(delivered, ensure_ascii=False))

        _, hux_p = self.service.v02_honest_ux(subject_view=False)
        self.assertNotIn(raw_secret, json.dumps(hux_p, ensure_ascii=False))

    def test_slice_18_forgotten_schedule_retrieved_via_archive_lookup_keeps_obligation(self) -> None:
        """Ch.13 §7 #18: 忘了某次安排但有真实承诺 — 有限召回未命中->按需查档; 缓存不能取消义务."""
        old_t = "2026-09-01T10:00:00+08:00"
        rec_out = rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_s18_promise",
            topic="借书归还约定",
            exact_detail="下周三前把《海岸建筑》还给书店老板",
            gist_summary="借书归还约定",
            occurred_at=old_t,
            keepsake_ref="archive:booklist_2026",
            now_iso=old_t,
        )
        ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_s18_book",
                "title_cn": "归还《海岸建筑》",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": T0,
                "shared": True,
                "operation_key": "op_s18_resp",
            },
            actor_side="subject",
            now_iso=T0,
        )

        direct = rp.project_recall_set(self.day_root, now_iso=T2, query_mode="direct_recall", include_unavailable=False)
        direct_visible_exact = [m for m in direct["items"] if m["memory_id"] == "mem_s18_promise" and m.get("clarity") == "EXACT"]
        self.assertEqual(direct_visible_exact, [])

        raw_entry = rec_out.get("entry") or {
            "memory_id": "mem_s18_promise",
            "topic": "借书归还约定",
            "exact_detail": "下周三前把《海岸建筑》还给书店老板",
            "gist_summary": "借书归还约定",
            "occurred_at": old_t,
            "archive_ref": "archive:booklist_2026",
        }
        found = rp.project_memory_item(
            raw_entry,
            now_iso=T2,
            query_mode="archive_lookup",
            consulted_archive=True,
            archive_ref="archive:booklist_2026",
        )
        self.assertEqual(found["clarity"], "EXACT")
        self.assertTrue(found["reconstructed_from_archive"])
        self.assertIn("海岸建筑", found["projected_text"])

        _, ad_view = self.service.v02_agency_depth(subject_view=True)
        self.assertTrue(any(r["resp_id"] == "resp_s18_book" for r in ad_view["data"]["gentle_responsibility_reviews"]))

    def test_slice_19_quiet_day_survives_zero_llm_zero_drama(self) -> None:
        """Ch.13 §7 #19: 一天很安静 — 技术存活、队列正常、无事件; 允许0 LLM，不补剧情."""
        before = _snapshot_tree(self.day_root)
        obs = loop_bridge.classify_day_observability(self.day_root, now_iso=T0)
        self.assertEqual(obs["llm_calls"], 0)
        self.assertEqual(obs["observability_class"], "QUIET_EVENTLESS_DAY")
        self.assertTrue(obs["is_quiet_unblocked"])

        review = rgm.project_chatless_life_review(self.day_root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(review["llm_calls"], 0)
        self.assertTrue(review["chat_window_hidden"])
        self.assertTrue(review["nonverbal_evidence_only"])
        self.assertIn("works_and_versions", review)

        with tempfile.TemporaryDirectory(prefix="v02-calm-s19-") as td:
            bd = lived_context_bridge.measure_context_budget_breakdown(Path(td), cfg={}, incoming_text="", now_iso=T0)
            self.assertTrue(bd["calm_silent"])
            self.assertEqual(bd["total_chars"], 0)
            self.assertEqual(bd["rendered_text"], "")

        code, mig = self.service.v02_runtime_migration(subject_view=True)
        self.assertEqual(code, 200)
        self.assertEqual(mig["llm_calls"], 0)
        self.assertEqual(before, _snapshot_tree(self.day_root))

    def test_slice_20_sudden_budget_drop_preserves_checkpoints_and_obligations(self) -> None:
        """Ch.13 §7 #20: 预算突然减少 — 降级->保存点与未决责任保留; 活跃减少不变成假offline人生."""
        asp.configure_budget_and_survival(
            self.day_root,
            {
                "normal_daily_cost_cap": 100.0,
                "normal_daily_token_cap": 50000,
                "normal_daily_wake_cap": 20,
                "activity_checkpoints": [{"task": "draft_ckpt", "cursor": 42}],
                "pending_commitments": [{"commitment_id": "resp_s20_keep"}],
                "deletion_barriers": [{"case_id": "del_s20_bar"}],
            },
            now_iso=T0,
        )
        ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_s20_keep",
                "title_cn": "未决借物归还责任",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": T0,
                "shared": True,
                "operation_key": "op_s20_resp",
            },
            actor_side="subject",
            now_iso=T0,
        )
        dropped = asp.apply_budget_drop(
            self.day_root,
            {"drop_pct": 90, "reason": "QUOTA_REDUCTION_90PCT"},
            now_iso=T1,
        )
        self.assertEqual(dropped["llm_calls"], 0)
        b = dropped["budget"]
        self.assertEqual(b["budget_scale_pct"], 10)
        self.assertEqual(b["active_daily_cost_cap"], 10.0)
        self.assertFalse(b["excuse_fabricated"])
        self.assertEqual(b["preserved_survival_state"]["activity_checkpoints"][0]["task"], "draft_ckpt")
        self.assertEqual(b["preserved_survival_state"]["pending_commitments"][0]["commitment_id"], "resp_s20_keep")

        code, hux = self.service.v02_honest_ux(subject_view=True)
        self.assertEqual(code, 200)
        self.assertEqual(hux["data"]["budget_survival"]["active_daily_cost_cap"], 10.0)

    def test_slice_21_carrier_migration_preserves_work_position_and_loss_manifest(self) -> None:
        """Ch.13 §7 #21: 换载体仍知道作品停在哪 — Identity绑定+对象迁移+Activity恢复; 语气相似不是唯一验收."""
        rgm.apply_runtime_migration_mutation(
            self.day_root,
            {
                "action": "pack_selective_suitcase",
                "profile_mode": "records_and_objects",
                "leave_behind_note": "保留本地缓存，仅打包身份、作品进度与未决责任",
                "operation_key": "op_s21_pack",
            },
            actor_side="subject",
            now_iso=T0,
        )
        exp_state, exp_status = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "export_suitcase", "target_carrier": "new_laptop", "operation_key": "op_s21_exp"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(exp_status, "APPLIED")
        bundle = exp_state["suitcase_bundle"]

        imp_state, imp_status = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "import_suitcase", "bundle": bundle, "operation_key": "op_s21_imp"},
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(imp_status, "APPLIED")
        self.assertEqual(imp_state["llm_calls"], 0)
        self.assertIn("migration_loss_manifest", imp_state)
        code, mig = self.service.v02_runtime_migration(subject_view=True)
        self.assertEqual(code, 200)
        self.assertIn("antipuppet_scorecard", mig["data"])

    def test_slice_22_acceptance_admin_moves_item_excluded_from_personal_memory(self) -> None:
        """Ch.13 §7 #22: 管理验收搬过一件物品 — actor=acceptance的真实World事件; 不能形成TA自己的生活记忆."""
        _, res = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {
                "action": "record_workspace_audit",
                "wake_reason": "acceptance:move_item_inspection",
                "visible_candidate_count": 1,
                "entered_workspace_count": 0,
                "admitted_candidates": [],
                "trimmed_reasons": [
                    {
                        "candidate_id": "acc_move_1",
                        "title": "acceptance_move_item_inspection",
                        "reason": "actor_acceptance_excluded_from_life_memory",
                    }
                ],
                "operation_key": "op_s22_acc",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(res, "APPLIED")
        code, rec_view = self.service.v02_recall_projection(subject_view=True)
        self.assertEqual(code, 200)
        self.assertEqual(rec_view["data"]["direct_recall_items"], [])
        self.assertEqual(rec_view["data"]["p12_autobiographical_narrative"]["chapters"], [])

    def test_slice_23_external_deleted_photo_blocked_never_resurrected_by_embedding(self) -> None:
        """Ch.13 §7 #23: 外部删除了一张图 — 原来源失效->索引/摘要阻止使用; 不通过旧embedding复活."""
        png_bytes = _build_bicolor_png()
        b64_png = base64.b64encode(png_bytes).decode("ascii")
        c_an, r_an = self.service.v02_crossmodal_action(
            payload={"action": "analyze_image_palette", "title": "Deleted photo study", "image_base64": b64_png, "share_with_room": True, "operation_key": "op_s23_pal"},
            subject_view=True,
        )
        self.assertEqual(c_an, 200, r_an)
        study_id = r_an["palette_studies"][0]["study_id"]
        rlf._ensure_control_ledger_locked(self.day_root)
        rlf.ingest_source(self.day_root, source_id="alb_s23_del", text="SECRET_PHOTO_DESC_S23", visibility="shared", operation_id="op_s23_ing", now_iso=T0)
        rlf.register_derivation(
            self.day_root,
            node_id="emb_s23_del",
            surface="vector_index",
            source_refs=[{"source_id": "alb_s23_del", "revision": 1}],
            content="cached embedding vector for SECRET_PHOTO_DESC_S23",
            generation=1,
            operation_id="op_s23_emb",
            now_iso=T0,
        )
        self.service.v02_crossmodal_action(
            payload={"action": "invalidate_deleted_media_source", "study_id": study_id, "reason_cn": "external_file_deleted", "operation_key": "op_s23_inv"},
            subject_view=True,
        )
        c_f, _ = rlf.execute_explicit_forget(self.day_root, source_ids=["alb_s23_del"], operation_id="op_s23_fgt", now_iso=T1)
        self.assertEqual(c_f, 200)
        c_q, q_res = rlf.read_with_barrier(self.day_root, target_kind="derivation", target_id="emb_s23_del", query_mode="normal", has_subject_capability=True)
        self.assertEqual(c_q, 403)
        self.assertTrue(q_res["blocked"])
        self.assertNotIn("SECRET_PHOTO_DESC_S23", json.dumps(q_res, ensure_ascii=False))

    def test_slice_24_old_method_reused_years_later_with_revalidation_no_pedigree(self) -> None:
        """Ch.13 §7 #24: 多年后重新使用一段方法 — 来源仍有效+当前条件复验; 来历可追溯，不用血统等级判断价值."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "skill_s24_old_brush",
                "tool_version": "1.0",
                "model_id": "m_2023",
                "scope": "watercolor",
                "attribution": "PRACTICED_MASTERY",
                "criteria_id": "crit_wc",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.20,
                "status": "VALIDATED",
            },
            now_iso="2023-05-01T10:00:00+08:00",
        )
        later_iso = "2026-11-18T10:00:00+08:00"
        asp.bump_carrier_or_tool_version(
            self.day_root,
            {"from_model_id": "m_2023", "to_model_id": "m_2026", "from_tool_version": "1.0", "to_tool_version": "4.0", "method_ids": ["skill_s24_old_brush"]},
            now_iso=later_iso,
        )
        reval = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "skill_s24_old_brush",
                "tool_version": "4.0",
                "model_id": "m_2026",
                "scope": "watercolor",
                "bare_model_score": 0.50,
                "method_with_new_model_score": 0.86,
                "old_baseline_score": 0.82,
                "held_out_trials": 5,
            },
            now_iso=later_iso,
        )
        self.assertEqual(reval["skill_release"]["status"], "VALIDATED")
        self.assertEqual(reval["llm_calls"], 0)
        _, hux = self.service.v02_honest_ux(subject_view=True)
        skill_entry = next(s for s in hux["data"]["skill_attributions"]["entries"] if s["method_id"] == "skill_s24_old_brush")
        self.assertEqual(skill_entry["status"], "VALIDATED")
        self.assertEqual(skill_entry["attribution"], "PRACTICED_MASTERY")
        self.assertNotIn("pedigree_tier", skill_entry)


if __name__ == "__main__":
    unittest.main()
