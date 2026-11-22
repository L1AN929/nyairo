#!/usr/bin/env python3
"""0.2 V-D 受控反事实消融检验器（Ch.14 §4/§6 & Ch.15 §9，零大模型调用）。

本模块实现三组严格分离变量的可机验反事实对照实验：
1. ``exp1_history_vs_placeholder``：固定同一运行时与机会集合，仅干预“真实续作/探究历史”vs“无真实谱系的占位材料”；
2. ``exp2_d01_placed_cue_vs_occluded_vs_decor``：同一房间同一时钟，仅干预 D01 物理摆件线索的“桌面可见 / 被遮挡收起 / 普通无记忆摆件”；
3. ``exp3_mutual_sustaining_perturbation``：保持数据库与运行态完全健康的前提下，扰动“作品↔技法↔习惯偏好”互相支撑回路中的一环，检验下游候选权重按预期衰减（绝不靠删库报错伪造因果）。

红线：全程 ``llm_calls == 0``，每组实验显式声明 ``fixed_variables`` 与 ``intervened_variable``，严禁混表。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LIFE_SCRIPTS = Path(__file__).resolve().parent
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import v02_agency_depth_engine as ad_engine  # noqa: E402
import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import workbench_curio_view  # noqa: E402

SCHEMA_VERSION = "nyairo.v02.causal_ablation_runner.v1"
DEFAULT_NOW_ISO = "2026-11-18T10:00:00+08:00"


def _seed_now_iso(seed: int) -> str:
    tz = timezone(timedelta(hours=8))
    base = datetime(2026, 11, 18, 10, 0, 0, tzinfo=tz)
    offset_mins = abs(int(seed)) % 120
    return (base + timedelta(minutes=offset_mins)).isoformat(timespec="seconds")


def _run_exp1_history_vs_placeholder(obs_iso: str) -> dict[str, Any]:
    """2x2 变量声明：固定运行时/机会骨架/候选上限，仅干预真实延续历史 vs 中性占位材料。"""
    with tempfile.TemporaryDirectory(prefix="vd-exp1-") as tmp:
        base = Path(tmp)
        real_root = base / "arm_real_history"
        placeholder_root = base / "arm_placeholder"
        (real_root / "phone").mkdir(parents=True)
        (placeholder_root / "phone").mkdir(parents=True)

        # 真实历史臂：工作台未完草稿 + 真实有界探究 + 方法微调
        tray_state = workbench_curio_view.empty_tray_state()
        tray_state, _ = workbench_curio_view.set_tray(
            tray_state,
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": "artifact:coastal_sketch_v2", "baseline_version_id": "ver_002"},
                "note": "海堤写生第二稿暗部水彩层次待补",
            },
            obs_iso,
        )
        workbench_curio_view.write_tray_state(real_root, tray_state)
        ad_engine.apply_agency_depth_mutation(
            real_root,
            {
                "action": "open_bounded_inquiry",
                "inquiry_id": "inq_wash_ratio",
                "question": "湿画法海雾渐变用几成清水最稳",
                "topic_tag": "sketch",
                "shared": True,
                "step_budget": 4,
                "operation_key": "vd_exp1_inq",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )

        # 对照占位臂：同样存在正常可读的 phone 托盘文件与默认空结构，但没有任何真实历史延续证据
        empty_tray = workbench_curio_view.empty_tray_state()
        workbench_curio_view.write_tray_state(placeholder_root, empty_tray)
        (placeholder_root / "phone" / "v02_placeholder_notice.json").write_text(
            json.dumps({"note": "neutral_placeholder_padding_block", "revision": 1}, ensure_ascii=False),
            encoding="utf-8",
        )

        real_collected = loop_bridge.collect_v02_v03_loop_candidates(real_root, now_iso=obs_iso)
        real_selected = loop_bridge.select_deterministic_loop_action(real_collected)
        ph_collected = loop_bridge.collect_v02_v03_loop_candidates(placeholder_root, now_iso=obs_iso)
        ph_selected = loop_bridge.select_deterministic_loop_action(ph_collected)

    real_families = [
        str(d.get("action_family") or "")
        for d in (real_collected.get("descriptors") or {}).values()
        if isinstance(d, dict)
    ]
    ph_families = [
        str(d.get("action_family") or "")
        for d in (ph_collected.get("descriptors") or {}).values()
        if isinstance(d, dict)
    ]
    causal_delta = (
        ("advance_workbench_draft" in real_families or "advance_bounded_inquiry" in real_families)
        and len(real_collected.get("candidates") or []) > len(ph_collected.get("candidates") or [])
        and real_selected.get("selected_candidate_id") is not None
        and ph_selected.get("selected_candidate_id") is None
    )
    return {
        "experiment_id": "exp1_history_vs_placeholder",
        "fixed_variables": ["runtime_engine", "clock_iso", "candidate_policy", "healthy_store_io"],
        "intervened_variable": "continuation_history_presence",
        "arms": {
            "real_history": {
                "candidate_count": len(real_collected.get("candidates") or []),
                "action_families": real_families,
                "selected_candidate_id": real_selected.get("selected_candidate_id"),
            },
            "placeholder_material": {
                "candidate_count": len(ph_collected.get("candidates") or []),
                "action_families": ph_families,
                "selected_candidate_id": ph_selected.get("selected_candidate_id"),
            },
        },
        "causal_delta_verified": bool(causal_delta),
        "llm_calls": 0,
    }


def _run_exp2_d01_placed_cue_ablation(obs_iso: str) -> dict[str, Any]:
    """D01 物理摆件记忆线索对照：桌面可见 vs 被遮挡/移走 vs 普通无记忆摆件。"""
    with tempfile.TemporaryDirectory(prefix="vd-exp2-") as tmp:
        base = Path(tmp)
        vis_root = base / "arm_visible_cue"
        occ_root = base / "arm_occluded_cue"
        decor_root = base / "arm_plain_decor"
        for r in (vis_root, occ_root, decor_root):
            (r / "phone").mkdir(parents=True)

        # Arm A: 可见 D01 摆放记忆线索
        ad_engine.apply_agency_depth_mutation(
            vis_root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_harbor_ticket",
                "artifact_ref": "artifact:harbor_sketch_v1",
                "spot_id": "desk_lamp_base",
                "cue_text_cn": "压在台灯底座下的那张港町渡轮旧票根",
                "visible": True,
                "shared": True,
                "operation_key": "vd_exp2_vis",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )

        # Arm B: 同一线索，但随后被遮挡（occluded=True）
        ad_engine.apply_agency_depth_mutation(
            occ_root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_harbor_ticket",
                "artifact_ref": "artifact:harbor_sketch_v1",
                "spot_id": "desk_lamp_base",
                "cue_text_cn": "压在台灯底座下的那张港町渡轮旧票根",
                "visible": True,
                "shared": True,
                "operation_key": "vd_exp2_occ_place",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )
        ad_engine.apply_agency_depth_mutation(
            occ_root,
            {
                "action": "update_placed_cue_visibility",
                "cue_id": "cue_harbor_ticket",
                "occluded": True,
                "operation_key": "vd_exp2_occ_hide",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )

        # Arm C: 普通摆件（无 D01 记忆线索挂载，仅普通桌面托盘物品描述）
        (decor_root / "phone" / "plain_desk_decor.json").write_text(
            json.dumps({"decor": [{"item": "素色陶瓷杯垫", "spot": "desk_lamp_base"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        vis_proj = ad_engine.project_v02_agency_depth(vis_root, has_subject_capability=True, now_iso=obs_iso)
        occ_proj = ad_engine.project_v02_agency_depth(occ_root, has_subject_capability=True, now_iso=obs_iso)
        dec_proj = ad_engine.project_v02_agency_depth(decor_root, has_subject_capability=True, now_iso=obs_iso)

        vis_loop = loop_bridge.collect_v02_v03_loop_candidates(vis_root, now_iso=obs_iso)
        occ_loop = loop_bridge.collect_v02_v03_loop_candidates(occ_root, now_iso=obs_iso)
        dec_loop = loop_bridge.collect_v02_v03_loop_candidates(decor_root, now_iso=obs_iso)

    vis_refs = [str(d.get("source_ref") or "") for d in (vis_loop.get("descriptors") or {}).values()]
    occ_refs = [str(d.get("source_ref") or "") for d in (occ_loop.get("descriptors") or {}).values()]
    dec_refs = [str(d.get("source_ref") or "") for d in (dec_loop.get("descriptors") or {}).values()]

    vis_active = len((vis_proj.get("placed_memory_cues") or {}).get("active_cues") or [])
    occ_active = len((occ_proj.get("placed_memory_cues") or {}).get("active_cues") or [])
    dec_active = len((dec_proj.get("placed_memory_cues") or {}).get("active_cues") or [])

    occluded_withdrawn = occ_active == 0 and "v02_placed_cue:cue_harbor_ticket" not in occ_refs
    plain_decor_silent = dec_active == 0 and len(dec_refs) == 0
    causal_delta = (
        vis_active == 1
        and "v02_placed_cue:cue_harbor_ticket" in vis_refs
        and occluded_withdrawn
        and plain_decor_silent
    )
    return {
        "experiment_id": "exp2_d01_placed_cue_vs_occluded_vs_decor",
        "fixed_variables": ["room_layout", "spot_id", "clock_iso", "subject_capability"],
        "intervened_variable": "d01_cue_visibility_and_memory_binding",
        "arms": {
            "visible_cue": {"active_cues": vis_active, "source_refs": vis_refs},
            "occluded_cue": {"active_cues": occ_active, "source_refs": occ_refs},
            "plain_decor": {"active_cues": dec_active, "source_refs": dec_refs},
        },
        "occluded_withdrawn": occluded_withdrawn,
        "plain_decor_silent": plain_decor_silent,
        "causal_delta_verified": bool(causal_delta),
        "llm_calls": 0,
    }


def _run_exp3_mutual_sustaining_perturbation(obs_iso: str) -> dict[str, Any]:
    """第 15 章 §9 互相支撑回路单环扰动：保持数据库健康，仅让技法/看腻环饱和或移走线索，验证权重与选择变化。"""
    with tempfile.TemporaryDirectory(prefix="vd-exp3-") as tmp:
        base = Path(tmp)
        intact_root = base / "arm_intact_loop"
        perturbed_root = base / "arm_perturbed_loop"
        for r in (intact_root, perturbed_root):
            (r / "phone").mkdir(parents=True)
            # 两臂共享同一个作品草稿与口味起点
            tray = workbench_curio_view.empty_tray_state()
            tray, _ = workbench_curio_view.set_tray(
                tray,
                {
                    "images": [],
                    "materials": [],
                    "work": {"artifact_id": "artifact:watercolor_01", "baseline_version_id": "v1"},
                    "note": "傍晚水彩练习",
                },
                obs_iso,
            )
            workbench_curio_view.write_tray_state(r, tray)
            ad_engine.apply_agency_depth_mutation(
                r,
                {
                    "action": "record_choice_episode",
                    "domain": "workbench",
                    "chosen": "水彩",
                    "choice_mode": "spontaneous",
                    "options": ["水彩", "walking", "tea"],
                    "shared": True,
                    "operation_key": "vd_exp3_pref",
                },
                actor_side="subject",
                now_iso=obs_iso,
            )
            ad_engine.apply_agency_depth_mutation(
                r,
                {
                    "action": "place_memory_cue",
                    "cue_id": "cue_brush_rest",
                    "artifact_ref": "artifact:watercolor_01",
                    "spot_id": "desk_corner",
                    "cue_text_cn": "搁在笔洗旁的水彩笔",
                    "visible": True,
                    "shared": True,
                    "operation_key": "vd_exp3_cue",
                },
                actor_side="subject",
                now_iso=obs_iso,
            )

        # 仅在扰动臂推进连写看腻饱和 + 撤掉桌面可见提示，保持存储健康无报错
        for idx in range(3):
            ad_engine.apply_agency_depth_mutation(
                perturbed_root,
                {
                    "action": "record_exposure_satiation",
                    "domain": "workbench",
                    "title": "水彩",
                    "intensity": 0.45,
                    "operation_key": f"vd_exp3_sat_{idx}",
                },
                actor_side="subject",
                now_iso=obs_iso,
            )
        ad_engine.apply_agency_depth_mutation(
            perturbed_root,
            {
                "action": "update_placed_cue_visibility",
                "cue_id": "cue_brush_rest",
                "removed": True,
                "operation_key": "vd_exp3_rm_cue",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )

        intact_loop = loop_bridge.collect_v02_v03_loop_candidates(intact_root, now_iso=obs_iso)
        pert_loop = loop_bridge.collect_v02_v03_loop_candidates(perturbed_root, now_iso=obs_iso)

    intact_mult = float(
        (((intact_loop.get("gating") or {}).get("agency_depth_summary") or {}).get("satiation_multipliers") or {}).get(
            "workbench", 1.0
        )
    )
    pert_mult = float(
        (((pert_loop.get("gating") or {}).get("agency_depth_summary") or {}).get("satiation_multipliers") or {}).get(
            "workbench", 1.0
        )
    )
    intact_wb_desc = next(
        (d for d in (intact_loop.get("descriptors") or {}).values() if d.get("action_family") == "advance_workbench_draft"),
        {},
    )
    pert_wb_desc = next(
        (d for d in (pert_loop.get("descriptors") or {}).values() if d.get("action_family") == "advance_workbench_draft"),
        {},
    )
    downstream_attenuated = (
        pert_mult < intact_mult
        and bool(pert_wb_desc.get("satiation_softened") or (pert_loop.get("gating") or {}).get("satiation_adjusted"))
        and len(pert_loop.get("candidates") or []) < len(intact_loop.get("candidates") or [])
    )
    return {
        "experiment_id": "exp3_mutual_sustaining_perturbation",
        "fixed_variables": ["healthy_storage_schema", "initial_work_tray", "initial_preference_support"],
        "intervened_variable": "satiation_and_desk_cue_support_links",
        "arms": {
            "intact_loop": {
                "creative_work_multiplier": intact_mult,
                "candidate_count": len(intact_loop.get("candidates") or []),
                "workbench_satiation_softened": bool(intact_wb_desc.get("satiation_softened")),
            },
            "perturbed_loop": {
                "creative_work_multiplier": pert_mult,
                "candidate_count": len(pert_loop.get("candidates") or []),
                "workbench_satiation_softened": bool(pert_wb_desc.get("satiation_softened")),
            },
        },
        "runtime_healthy": True,
        "downstream_attenuated": bool(downstream_attenuated),
        "causal_delta_verified": bool(downstream_attenuated),
        "llm_calls": 0,
    }


def run_v02_counterfactual_suite(*, seed: int = 20261118) -> dict[str, Any]:
    """运行 0.2 V-D 三组独立受控反事实消融实验（零模型调用、零外部写入）。"""
    obs_iso = _seed_now_iso(seed)
    exp1 = _run_exp1_history_vs_placeholder(obs_iso)
    exp2 = _run_exp2_d01_placed_cue_ablation(obs_iso)
    exp3 = _run_exp3_mutual_sustaining_perturbation(obs_iso)
    all_passed = bool(
        exp1.get("causal_delta_verified")
        and exp2.get("causal_delta_verified")
        and exp3.get("causal_delta_verified")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": int(seed),
        "observed_at": obs_iso,
        "all_passed": all_passed,
        "exp1_history_vs_placeholder": exp1,
        "exp2_d01_placed_cue_vs_occluded_vs_decor": exp2,
        "exp3_mutual_sustaining_perturbation": exp3,
        "llm_calls": 0,
    }


__all__ = [
    "SCHEMA_VERSION",
    "run_v02_counterfactual_suite",
]
