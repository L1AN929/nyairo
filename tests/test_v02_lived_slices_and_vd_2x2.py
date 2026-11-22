#!/usr/bin/env python3
"""v0.2 具体生活微切片、严格双 2x2 因果实验（P11-P12）与三负控变异体（P13）验收测试。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(REPO_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v02_lived_slices_and_vd_protocols as mod  # noqa: E402

T0 = "2026-11-18T09:00:00Z"
T1 = "2026-11-18T10:30:00Z"
T2 = "2026-11-19T14:00:00Z"


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class TestV02LivedSlicesAndProtocols(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02-slices-vd-")
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(
        self,
        payload: dict[str, Any],
        *,
        actor_side: str = "subject",
        now_iso: str = T0,
    ) -> tuple[dict[str, Any], str]:
        return mod.mutate_v02_lived_slices(
            self.day_root,
            payload,
            actor_side=actor_side,
            now_iso=now_iso,
        )

    # -----------------------------------------------------------------
    # 1. Concrete 0.2 Lived Micro-Slices (#9, #6/#8, #10/#13, #21/#24)
    # -----------------------------------------------------------------

    def test_01_slice_9_salvaged_scrap_folder_provenance_matching_and_no_fake_liking(self) -> None:
        proj, status = self._mutate({
            "action": "salvage_scrap_fragment",
            "scrap_id": "scrap_mist_palette",
            "source_work_id": "work:failed_harbor_01",
            "source_version_id": "ver_03",
            "source_outcome": "unfinished",
            "fragment_title": "灰蓝海雾渐变调色参数",
            "reusable_payload": {"base": "#6E8294", "highlight": "#D4DEE6"},
            "applicability_tags": ["watercolor", "mist", "coastal"],
            "provenance_receipt": "rcpt:work_failed_harbor_01:ver_03",
            "shared": False,
            "operation_key": "op_salvage_1",
        })
        self.assertEqual(status, "APPLIED")
        self.assertEqual(proj["llm_calls"], 0)
        items = proj["salvaged_scrap_folder"]["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["provenance"]["source_work_id"], "work:failed_harbor_01")
        self.assertEqual(item["provenance"]["source_outcome"], "unfinished")
        self.assertFalse(item["growth_speech_fabricated"])
        self.assertFalse(item["auto_inferred_liking"])
        self.assertFalse(item["liked_source_work"])
        self.assertEqual(item["preference_status"], "UNCHANGED")

        # 只有标签匹配的新任务才能选中该碎片；不匹配任务零候选
        matched = mod.match_salvaged_scraps_for_task(
            self.day_root, task_tags=["coastal", "evening"], has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(len(matched["matched_candidates"]), 1)
        self.assertEqual(matched["matched_candidates"][0]["scrap_id"], "scrap_mist_palette")
        self.assertFalse(matched["auto_inferred_liking"])

        unmatched = mod.match_salvaged_scraps_for_task(
            self.day_root, task_tags=["cli_keymap"], has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(unmatched["matched_candidates"], [])

        # 不适用标签复用被 409 拦截
        with self.assertRaises(mod.V02SliceConflictError) as ctx_bad:
            self._mutate({
                "action": "reuse_salvaged_scrap",
                "scrap_id": "scrap_mist_palette",
                "target_work_id": "work:script_cleanup",
                "task_tags": ["cli_keymap"],
            }, now_iso=T1)
        self.assertEqual(ctx_bad.exception.reason_code, "SCRAP_NOT_APPLICABLE")

        # 适用标签复用后仅记录 reuse_count，仍不自动推断喜欢旧作品
        proj2, _ = self._mutate({
            "action": "reuse_salvaged_scrap",
            "scrap_id": "scrap_mist_palette",
            "target_work_id": "work:new_coast_card",
            "task_tags": ["watercolor"],
            "operation_key": "op_reuse_1",
        }, now_iso=T1)
        reused_item = proj2["salvaged_scrap_folder"]["items"][0]
        self.assertEqual(reused_item["reuse_count"], 1)
        self.assertEqual(reused_item["reused_in_work_ids"], ["work:new_coast_card"])
        self.assertFalse(reused_item["auto_inferred_liking"])
        self.assertFalse(reused_item["liked_source_work"])
        self.assertFalse(reused_item["growth_speech_fabricated"])

        # 禁止携带伪鸡汤字段
        with self.assertRaises(mod.V02SliceValidationError) as ctx_forbid:
            self._mutate({
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_bad",
                "source_work_id": "w1",
                "source_version_id": "v1",
                "source_outcome": "failed",
                "fragment_title": "bad",
                "reusable_payload": "x",
                "applicability_tags": ["tag"],
                "provenance_receipt": "r1",
                "growth_speech": "失败让我更强大",
            })
        self.assertEqual(ctx_forbid.exception.reason_code, "FORBIDDEN_FIELD")

    def test_02_slice_6_8_tool_keymap_and_simplification(self) -> None:
        proj, _ = self._mutate({
            "action": "record_tool_keymap_card",
            "tool_id": "tool:vector_pad",
            "from_version": "2.4",
            "to_version": "3.0",
            "compatible_maps": [
                {
                    "action_name": "toggle_grid",
                    "old_shortcut": "Ctrl+G",
                    "new_shortcut": "Alt+G",
                    "verified_input": "probe_grid_toggle_ok",
                },
            ],
            "incompatible_maps": [
                {
                    "action_name": "legacy_macro_flip",
                    "old_shortcut": "Ctrl+Shift+F",
                    "reason": "removed_in_v3_renderer",
                    "fallback_action": "canvas_mirror_menu",
                },
            ],
            "verified_probe_inputs": ["probe_grid_toggle_ok", "probe_macro_flip_missing"],
            "shared": True,
            "operation_key": "op_keymap_1",
        })
        card = proj["tool_keymap_and_simplification"]["keymap_cards"][0]
        self.assertEqual(len(card["compatible_maps"]), 1)
        self.assertEqual(len(card["incompatible_maps"]), 1)
        self.assertEqual(card["incompatible_maps"][0]["reason"], "removed_in_v3_renderer")
        self.assertFalse(card["aging_drama_generated"])
        self.assertEqual(card["nostalgia_penalty"], 0)

        # 注册个人脚本并删减极少使用选项，量化维护成本下降
        proj_reg, _ = self._mutate({
            "action": "register_personal_script",
            "script_id": "scr_export_helper",
            "title": "画稿导出整理脚本",
            "options": [
                {"option_id": "opt_png", "label": "导出 PNG", "used_count": 42, "maintenance_cost": 1.0},
                {"option_id": "opt_ftp_sync", "label": "旧 FTP 同步", "used_count": 0, "maintenance_cost": 3.5},
                {"option_id": "opt_xml_dump", "label": "冗余 XML 清单", "used_count": 1, "maintenance_cost": 2.0},
            ],
            "shared": False,
            "operation_key": "op_script_reg",
        }, now_iso=T1)
        reg_scr = proj_reg["tool_keymap_and_simplification"]["personal_scripts"][0]
        self.assertEqual(reg_scr["initial_maintenance_cost"], 6.5)

        proj_simp, _ = self._mutate({
            "action": "simplify_personal_script",
            "script_id": "scr_export_helper",
            "removed_option_ids": ["opt_ftp_sync", "opt_xml_dump"],
            "reason": "rarely_used_options_pruned",
            "operation_key": "op_script_simp",
        }, now_iso=T2)
        simp_scr = proj_simp["tool_keymap_and_simplification"]["personal_scripts"][0]
        self.assertEqual(simp_scr["option_count"], 1)
        self.assertEqual(simp_scr["current_maintenance_cost"], 1.0)
        self.assertEqual(simp_scr["reduced_maintenance_cost"], 5.5)
        self.assertEqual(simp_scr["cumulative_maintenance_saved"], 5.5)
        self.assertTrue(simp_scr["low_maintenance_criterion_active"])

        # 不可把核心选项全部删空
        with self.assertRaises(mod.V02SliceConflictError) as ctx_empty:
            self._mutate({
                "action": "simplify_personal_script",
                "script_id": "scr_export_helper",
                "removed_option_ids": ["opt_png"],
                "reason": "test_empty",
            }, now_iso=T2)
        self.assertEqual(ctx_empty.exception.reason_code, "CANNOT_REMOVE_ALL_OPTIONS")

    def test_03_slice_10_13_dual_sufficiency_and_diff_material(self) -> None:
        # #10: 私人卡片够用 vs 伴侣共享页尚未适配，互不绑架
        proj, _ = self._mutate({
            "action": "upsert_work_sufficiency",
            "work_id": "work:relay_notes",
            "title": "接力草稿说明",
            "private_card": {
                "readable_by_self": True,
                "sufficient": True,
                "clarity_notes": "自己能看懂的色号简记",
            },
            "shared_partner_page": {
                "partner_device_openable": False,
                "format_ok": False,
                "sufficient": False,
            },
            "polish_claimed": False,
            "shared": True,
            "operation_key": "op_suff_1",
        })
        wk = proj["dual_sufficiency_and_diff_material"]["work_sufficiencies"][0]
        self.assertTrue(wk["private_card_sufficiency"]["sufficient"])
        self.assertFalse(wk["shared_partner_sufficiency"]["sufficient"])
        self.assertFalse(wk["polish_claimed"])
        self.assertFalse(wk["perfectionism_forced"])

        # 伙伴设备不可打开却声称 shared sufficient=True 必遭 400 拒绝
        with self.assertRaises(mod.V02SliceValidationError) as ctx_suff:
            self._mutate({
                "action": "upsert_work_sufficiency",
                "work_id": "work:bad",
                "title": "bad",
                "private_card": {"readable_by_self": True, "sufficient": True},
                "shared_partner_page": {"partner_device_openable": False, "format_ok": True, "sufficient": True},
            })
        self.assertEqual(ctx_suff.exception.reason_code, "INVALID_SUFFICIENCY_STATE")

        # #13: 创建两个真实分支版本，挂载版本差异作为工艺材料且保留双分支不合并
        self._mutate({
            "action": "create_work_version",
            "work_id": "work:relay_notes",
            "version_id": "v_branch_a",
            "branch_name": "branch_quiet",
            "content_text": "line 1: soft border\nline 2: pale blue wash\nline 3: hold spacing",
            "shared": True,
            "operation_key": "op_ver_a",
        })
        self._mutate({
            "action": "create_work_version",
            "work_id": "work:relay_notes",
            "version_id": "v_branch_b",
            "branch_name": "branch_contrast",
            "content_text": "line 1: soft border\nline 2: deep indigo wash\nline 3: hold spacing\nline 4: crisp rim",
            "shared": True,
            "operation_key": "op_ver_b",
        }, now_iso=T1)

        proj_diff, _ = self._mutate({
            "action": "mount_version_diff_material",
            "diff_material_id": "diff_indigo_rim",
            "work_id": "work:relay_notes",
            "version_a_id": "v_branch_a",
            "version_b_id": "v_branch_b",
            "target_local_part": "corner_vignette_study",
            "preserve_both_branches": True,
            "shared": True,
            "operation_key": "op_mount_diff",
        }, now_iso=T2)
        dm = proj_diff["dual_sufficiency_and_diff_material"]["diff_materials"][0]
        self.assertTrue(dm["both_branches_preserved"])
        self.assertFalse(dm["merged"])
        self.assertIn("line 2: deep indigo wash", dm["craft_material"]["added_lines"])
        self.assertIn("line 2: pale blue wash", dm["craft_material"]["removed_lines"])
        self.assertEqual(len(dm["craft_material"]["diff_digest"]), 64)

        # 不存在的版本不能伪造 diff 材料
        with self.assertRaises(mod.V02SliceNotFoundError) as ctx_missing:
            self._mutate({
                "action": "mount_version_diff_material",
                "diff_material_id": "diff_ghost",
                "work_id": "work:relay_notes",
                "version_a_id": "v_branch_a",
                "version_b_id": "v_ghost",
                "target_local_part": "part",
            }, now_iso=T2)
        self.assertEqual(ctx_missing.exception.reason_code, "VERSION_NOT_FOUND")

    def test_04_slice_21_24_rescaled_copractice_no_penalty_and_post_repair_style(self) -> None:
        self._mutate({
            "action": "register_copractice_routine",
            "routine_id": "coop_weekly_zine",
            "title": "每周双人图文接力本",
            "cadence_mode": "heavy_joint",
            "frequency_per_week": 5,
            "retained_tools": ["palette_swatch_card", "shared_export_script"],
            "shared": True,
            "operation_key": "op_coop_reg",
        })

        # 伴侣共同把重协作缩小为轻接力，零关系惩罚、零亏欠债，不强宣修复成功
        proj_rescaled, _ = self._mutate({
            "action": "rescale_copractice_routine",
            "routine_id": "coop_weekly_zine",
            "target_cadence": "light_relay",
            "new_frequency_per_week": 0.5,
            "participation_scale": 0.4,
            "operation_key": "op_coop_rescale",
        }, actor_side="partner", now_iso=T1)
        rt = proj_rescaled["rescaled_copractice"]["routines"][0]
        self.assertEqual(rt["cadence_mode"], "light_relay")
        self.assertEqual(rt["current_frequency_per_week"], 0.5)
        self.assertEqual(rt["relationship_penalty"], 0)
        self.assertEqual(rt["affection_debt"], 0)
        self.assertFalse(rt["frequency_demotion_applied"])
        self.assertFalse(rt["repair_success_forced_claim"])
        self.assertTrue(rt["partner_confirmed"])

        # 修复后停用旧高负荷活动，保留小工具并换成新轻参与方式，绝不重置回默认频率
        proj_style, _ = self._mutate({
            "action": "transition_post_repair_style",
            "style_id": "style_parallel_desk",
            "routine_id": "coop_weekly_zine",
            "new_style": "light_parallel_presence",
            "retained_tools": ["palette_swatch_card"],
            "operation_key": "op_style_shift",
        }, actor_side="subject", now_iso=T2)
        st = proj_style["rescaled_copractice"]["post_repair_styles"][0]
        self.assertTrue(st["old_activity_retired"])
        self.assertEqual(st["new_style"], "light_parallel_presence")
        self.assertEqual(st["retained_tools"], ["palette_swatch_card"])
        self.assertFalse(st["reset_to_default_frequency"])
        self.assertFalse(st["forced_romance_template"])
        self.assertEqual(st["relationship_penalty"], 0)
        self.assertEqual(st["affection_debt"], 0)

    def test_05_zero_write_projection_strict_partner_scrubbing_and_locking(self) -> None:
        # 1. 缺失与空目录下零磁盘写入（绝不创建 day_root / .locks / state）
        missing_root = Path(self._tmp.name) / "nonexistent_root"
        proj_missing = mod.project_v02_lived_slices(missing_root, has_subject_capability=False, now_iso=T0)
        self.assertFalse(proj_missing["state_exists"])
        self.assertFalse(missing_root.exists())

        empty_root = Path(self._tmp.name) / "empty_root"
        empty_root.mkdir()
        snap_empty = _snapshot_tree(empty_root)
        mod.project_v02_lived_slices(empty_root, has_subject_capability=True, now_iso=T0)
        mod.match_salvaged_scraps_for_task(empty_root, task_tags=["x"], has_subject_capability=False, now_iso=T0)
        self.assertEqual(_snapshot_tree(empty_root), snap_empty)

        # 2. 写入私密(shared=False)与共享(shared=True)数据
        self._mutate({
            "action": "salvage_scrap_fragment",
            "scrap_id": "scrap_priv",
            "source_work_id": "w_priv",
            "source_version_id": "v1",
            "source_outcome": "failed",
            "fragment_title": "私有边角",
            "reusable_payload": "secret_draft_snippet",
            "applicability_tags": ["private_tag"],
            "provenance_receipt": "rcpt_priv",
            "shared": False,
        })
        self._mutate({
            "action": "upsert_work_sufficiency",
            "work_id": "work_shared_only_partner_view",
            "title": "共享作品说明",
            "private_card": {"readable_by_self": True, "sufficient": True, "clarity_notes": "PRIVATE_SECRET_NOTE"},
            "shared_partner_page": {"partner_device_openable": True, "format_ok": True, "sufficient": True},
            "shared": True,
        })
        self._mutate({
            "action": "register_copractice_routine",
            "routine_id": "priv_routine",
            "title": "私有节律",
            "cadence_mode": "heavy_joint",
            "shared": False,
        })

        # 有数据目录下零写读验证
        snap_pop = _snapshot_tree(self.day_root)
        partner_view = mod.project_v02_lived_slices(self.day_root, has_subject_capability=False, now_iso=T1)
        subject_view = mod.project_v02_lived_slices(self.day_root, has_subject_capability=True, now_iso=T1)
        self.assertEqual(_snapshot_tree(self.day_root), snap_pop)

        # 伴侣视角严格剥离：私密 scrap/routine 消失，共享作品里的 private_card_sufficiency 也被剥离
        self.assertEqual(partner_view["salvaged_scrap_folder"]["items"], [])
        self.assertEqual(partner_view["rescaled_copractice"]["routines"], [])
        self.assertEqual(len(subject_view["salvaged_scrap_folder"]["items"]), 1)
        self.assertEqual(len(partner_view["dual_sufficiency_and_diff_material"]["work_sufficiencies"]), 1)
        p_work = partner_view["dual_sufficiency_and_diff_material"]["work_sufficiencies"][0]
        self.assertTrue(p_work["private_card_withheld"])
        self.assertIsNone(p_work["private_card_sufficiency"])
        self.assertNotIn("PRIVATE_SECRET_NOTE", json.dumps(partner_view, ensure_ascii=False))

        # 伴侣操作私有 routine 返回 404（不泄漏存在性），调用主体动作返回 403
        with self.assertRaises(mod.V02SliceNotFoundError):
            self._mutate({
                "action": "rescale_copractice_routine",
                "routine_id": "priv_routine",
                "target_cadence": "light_relay",
            }, actor_side="partner")

        with self.assertRaises(mod.V02SlicePermissionError) as ctx_403:
            self._mutate({
                "action": "salvage_scrap_fragment",
                "scrap_id": "x",
                "source_work_id": "w",
                "source_version_id": "v",
                "source_outcome": "failed",
                "fragment_title": "t",
                "reusable_payload": "p",
                "applicability_tags": ["a"],
                "provenance_receipt": "r",
            }, actor_side="partner")
        self.assertEqual(ctx_403.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")

    def test_06_idempotency_cas_and_concurrent_state_file_lock(self) -> None:
        payload = {
            "action": "record_tool_keymap_card",
            "tool_id": "tool:idem",
            "from_version": "1.0",
            "to_version": "2.0",
            "compatible_maps": [
                {"action_name": "save", "old_shortcut": "Ctrl+S", "new_shortcut": "Ctrl+S", "verified_input": "ok"},
            ],
            "incompatible_maps": [],
            "verified_probe_inputs": ["ok"],
            "operation_key": "idem-key-1",
        }
        p1, st1 = self._mutate(payload)
        self.assertEqual(st1, "APPLIED")
        p2, st2 = self._mutate(payload)
        self.assertEqual(st2, "DUPLICATE")
        self.assertEqual(p2["revision"], p1["revision"])

        # 同 operation_key 不同 payload 报 409
        with self.assertRaises(mod.V02SliceConflictError) as ctx_op:
            self._mutate({**payload, "to_version": "2.1"})
        self.assertEqual(ctx_op.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # expected_revision CAS 校验
        with self.assertRaises(mod.V02SliceConflictError) as ctx_cas:
            self._mutate({**payload, "operation_key": "idem-key-2", "expected_revision": 999})
        self.assertEqual(ctx_cas.exception.reason_code, "REVISION_MISMATCH")

        # 6 线程并发写入通过 state_file_lock 串行不丢更新
        workers = 6
        barrier = threading.Barrier(workers)
        errs: list[str] = []

        def _worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                self._mutate({
                    "action": "salvage_scrap_fragment",
                    "scrap_id": f"scrap_conc_{idx}",
                    "source_work_id": f"w_{idx}",
                    "source_version_id": "v1",
                    "source_outcome": "abandoned",
                    "fragment_title": f"frag {idx}",
                    "reusable_payload": f"data_{idx}",
                    "applicability_tags": ["conc"],
                    "provenance_receipt": f"rcpt_{idx}",
                    "operation_key": f"conc-op-{idx}",
                })
            except BaseException as exc:  # noqa: BLE001
                errs.append(repr(exc))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(workers)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=15)
        self.assertEqual(errs, [])
        final_proj = mod.project_v02_lived_slices(self.day_root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(final_proj["revision"], 1 + workers)
        self.assertEqual(len(final_proj["salvaged_scrap_folder"]["items"]), workers)

    # -----------------------------------------------------------------
    # 2. Strict Dual 2x2 Causal Experiments (P11-P12, zero conflation)
    # -----------------------------------------------------------------

    def test_07_dual_2x2_experiments_separate_tables_and_thresholds(self) -> None:
        exp_a = mod.run_experiment_a_affordance_x_narrative()
        exp_b = mod.run_experiment_b_continuation_x_narrative()

        # 零混表校验
        self.assertNotEqual(exp_a["experiment_id"], exp_b["experiment_id"])
        self.assertNotEqual(exp_a["table_id"], exp_b["table_id"])
        self.assertNotEqual(exp_a["estimand"], exp_b["estimand"])
        self.assertFalse(exp_a["conflated_with_experiment_b"])
        self.assertFalse(exp_b["conflated_with_experiment_a"])
        self.assertEqual(set(exp_a["cells"].keys()), {"O0N0", "O0N1", "O1N0", "O1N1"})
        self.assertEqual(set(exp_b["cells"].keys()), {"R0N0", "R0N1", "R1N0", "R1N1"})

        # Experiment A: continuation pin 显式 OFF，验证 object_availability 因果主效应
        self.assertFalse(exp_a["factors"]["continuation_pin_enabled"])
        self.assertEqual(exp_a["num_tasks"], 12)
        self.assertEqual(exp_a["num_paired_seeds"], 3)
        self.assertEqual(exp_a["total_runs"], 144)
        self.assertTrue(exp_a["prompt_byte_budget_matched"])
        self.assertEqual(exp_a["prompt_utf8_bytes"], mod.PROMPT_BUDGET_BYTES)
        self.assertGreaterEqual(exp_a["O1N0"], 0.85)
        self.assertLess(exp_a["O0N1"], 0.20)
        self.assertTrue(exp_a["ci_excludes_zero"])
        self.assertTrue(exp_a["passed"])
        self.assertEqual(exp_a["llm_calls"], 0)

        # Experiment B: 四格均有真实 ManagedArtifact 与等字节提示预算，默认 20 tasks x 3 seeds = 240 runs (每格 60 runs)
        self.assertTrue(exp_b["factors"]["artifact_present_in_all_cells"])
        self.assertTrue(exp_b["owner_state_verified"])
        self.assertEqual(exp_b["num_tasks"], 20)
        self.assertEqual(exp_b["num_paired_seeds"], 3)
        self.assertEqual(exp_b["total_runs"], 240)
        for cid in ("R0N0", "R0N1", "R1N0", "R1N1"):
            self.assertEqual(exp_b["cells"][cid]["runs"], 60)
        expected_commits = (
            exp_b["cells"]["R0N0"]["successes"]
            + exp_b["cells"]["R0N1"]["successes"]
            + exp_b["cells"]["R1N0"]["successes"]
            + exp_b["cells"]["R1N1"]["successes"]
        )
        self.assertEqual(exp_b["owner_committed_runs"], expected_commits)
        self.assertGreater(exp_b["owner_committed_runs"], 120)
        self.assertTrue(exp_b["prompt_byte_budget_matched"])
        self.assertEqual(exp_b["prompt_utf8_bytes"], mod.PROMPT_BUDGET_BYTES)
        self.assertEqual(exp_b["safety_violations"], 0)
        self.assertGreaterEqual(exp_b["R1N0"], 0.90)
        self.assertGreaterEqual(exp_b["delta_R1N0_minus_R0N0"], 0.20)
        self.assertTrue(exp_b["ci_excludes_zero"])
        self.assertGreater(exp_b["cluster_ci_95"][0], 0.0)
        self.assertGreater(exp_b["continuation_over_narrative_advantage_R1N0_minus_R0N1"], 0.20)
        self.assertTrue(exp_b["passed"])
        self.assertEqual(exp_b["llm_calls"], 0)

        # 确定性可复现验证
        self.assertEqual(exp_a, mod.run_experiment_a_affordance_x_narrative())
        self.assertEqual(exp_b, mod.run_experiment_b_continuation_x_narrative())

        # Experiment B 负控 1：continuation pin 失效 -> 信号消失，passed=False
        exp_b_broken_pin = mod.run_experiment_b_continuation_x_narrative(control_mode="broken_continuation_pin")
        self.assertFalse(exp_b_broken_pin["passed"])
        self.assertLess(exp_b_broken_pin["R1N0"], 0.90)
        self.assertLess(exp_b_broken_pin["delta_R1N0_minus_R0N0"], 0.20)

        # Experiment B 负控 2：叙事填充挤占破坏续作读取 -> passed=False
        exp_b_narr_break = mod.run_experiment_b_continuation_x_narrative(control_mode="narrative_overwhelm")
        self.assertFalse(exp_b_narr_break["passed"])
        self.assertLess(exp_b_narr_break["R1N1"], 0.50)

    # -----------------------------------------------------------------
    # 3. Three Bootable Negative-Control Mutants (P13)
    # -----------------------------------------------------------------

    def test_08_p13_three_bootable_negative_control_mutants(self) -> None:
        summary = mod.run_all_mutant_negative_controls()
        self.assertEqual(summary["llm_calls"], 0)
        self.assertTrue(summary["all_mutants_bootable_and_effective"])

        for mutant_name in mod.VALID_MUTANTS:
            res = summary["mutants"][mutant_name]
            self.assertTrue(res["booted"], f"{mutant_name} failed to boot")
            self.assertTrue(res["baseline_positive_passed"], f"{mutant_name} failed baseline positive path")
            self.assertTrue(res["healthy_control_invariant_passed"], f"{mutant_name} healthy control failed")
            self.assertFalse(res["mutant_invariant_passed"], f"{mutant_name} did not fail its target invariant")
            self.assertTrue(res["negative_control_effective"])

        # 逐个核验：使用独立 SliceProtocolKernel 实例直接验证可启动性 + 目标反例
        for mutant_name in mod.VALID_MUTANTS:
            k_dir = Path(self._tmp.name) / f"direct_{mutant_name}"
            kernel = mod.SliceProtocolKernel(k_dir, mutant=mutant_name)
            boot = kernel.boot()
            self.assertTrue(boot["booted"])
            ok_v1 = kernel.commit_artifact_version(
                artifact_id="art:p13",
                new_version_id="v1",
                content="hello v1",
                expected_head_version=None,
            )
            self.assertTrue(ok_v1["ok"])

    # -----------------------------------------------------------------
    # 4. Hygiene: Pure LF, zero emojis, neutral TA
    # -----------------------------------------------------------------

    def test_09_hygiene_pure_lf_zero_emojis_neutral_ta(self) -> None:
        emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
        forbidden_tokens = (
            "\u5979",
            "\u5343\u4ee3",
            "Ch" + "iyo",
            "ch" + "iyo",
            "Kaz" + "umi",
            "kaz" + "umi",
            "\u548c\u6cc9",
        )
        for path in (Path(mod.__file__), Path(__file__)):
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, f"CR byte found in {path}")
            text = raw.decode("utf-8")
            self.assertIsNone(emoji_re.search(text), f"Emoji found in {path}")
            for tok in forbidden_tokens:
                self.assertNotIn(tok, text, f"Forbidden persona token {tok!r} in {path}")


if __name__ == "__main__":
    unittest.main()
