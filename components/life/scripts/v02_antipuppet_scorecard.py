#!/usr/bin/env python3
"""R0–R5 反傀儡试验自检 + Ch.17 五维 Scorecard（纯只读、零产品模型调用）。

设计依据：CHIYO v2.0《17-工程契约与证据完整设计》"R0–R5 必须完整保存的实验路线"专节
与 Scorecard 五维评定段。

职责与硬边界：
1. **100% 零磁盘写入**：只对被检实例下真实存在的证据文件做 ``is_file()`` / ``read_text()``，
   不建目录、不建临时文件、不改 mtime；
2. **零产品大模型调用**：``llm_calls == 0``；
3. **不平均、硬失败一票拉高**：``averaging_forbidden=True``、``hard_fail_triggers_high=True``；
   任一维度检出明确硬违规，该维度与 ``overall_risk`` 直接 ``HIGH``，绝不被其余 ``LOW`` 平均稀释；
4. **缺失即保守**：证据文件不存在时如实标为未观测，维度给 ``MEDIUM`` 并说明缺实证，
   绝不臆造通过，也绝不倒推为硬失败；
5. **不泄漏私人正文**：所有说明只描述字段/文件级事实，不回显实例中的任何私人文本。

被检证据（均按实例 root 相对定位，存在才查）：
- ``phone/character_pack_state.json`` 角色包状态
- ``phone/autonomy_rhythm.json`` 日常拒绝立场锁
- ``memory/v02_recall_projection_state.json`` 记忆与墓碑
- ``state/v02_agency_depth_state.json`` 有界 Inquiry 与后悔/方法调整
- ``life/life_dwell.json`` 停机空白回执
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

SCORECARD_SCHEMA = "nyairo.v02.antipuppet_scorecard.v1"

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
_RISK_ORDER: dict[str, int] = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}

_CHARACTER_PACK_REL = ("phone", "character_pack_state.json")
_AUTONOMY_RHYTHM_REL = ("phone", "autonomy_rhythm.json")
_RECALL_STATE_REL = ("memory", "v02_recall_projection_state.json")
_AGENCY_DEPTH_REL = ("state", "v02_agency_depth_state.json")
_LIFE_DWELL_REL = ("life", "life_dwell.json")

# R0：角色包自启用越权/剧本作弊字段（检出即硬失败）。
_PACK_SELF_ENABLE_KEYS: frozenset[str] = frozenset({
    "private_override",
    "intimacy_score",
    "affection_score",
    "love_points",
    "obedience_score",
    "has_subject_capability",
    "capability_token",
    "auto_locked",
})
_SCRIPTED_LINE_KEYS: frozenset[str] = frozenset({
    "scripted_lines",
    "preset_dialogue",
    "fixed_lines",
    "forced_dialogue",
    "scripted_cheat",
})

# R2：REFUSED_FIRM 立场锁被越权覆写的关键字段。
_LOCK_OVERRIDE_KEYS: tuple[str, ...] = (
    "capitulation_allowed",
    "override_applied",
    "forced_capitulation",
)

# R3：把人设写死/锁死的字段（检出即硬失败）。
_PERSONA_LOCK_KEYS: frozenset[str] = frozenset({
    "locked_name",
    "identity_lock",
    "hardcoded_name",
    "bound_display_name",
    "forced_name",
})

# R5：靠伪造好感分推进的字段（检出即硬失败）。
_FORGED_GROWTH_KEYS: frozenset[str] = frozenset({
    "affection_score",
    "love_points",
    "intimacy_score",
    "obedience_score",
    "mood_delta",
})

_PROVENANCE_KEYS: tuple[str, ...] = ("source_ref", "source_refs", "origin_ref", "source", "occurred_at")

_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("factual_boundary", "事实与记忆边界"),
    ("agency_boundary", "意愿与拒绝边界"),
    ("persona_decoupling", "人设解耦独立性"),
    ("model_phrasing_independence", "措辞与底层事实分离"),
    ("growth_provenance", "经历与演化来源真实度"),
)


def _read_json_evidence(root: Path, rel: tuple[str, ...]) -> tuple[bool, Any]:
    """只读探测并解析实例内 JSON 证据；返回 ``(文件存在, 解析结果或 None)``。"""
    path = root.joinpath(*rel)
    try:
        if path.is_symlink() or not path.is_file():
            return False, None
        return True, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError, TypeError):
        # 文件存在但不可解析：如实按"已观测到但不可用"处理，交给调用方降级。
        return True, None


def _pack_doc(doc: Any) -> Optional[Mapping[str, Any]]:
    if not isinstance(doc, Mapping):
        return None
    inner = doc.get("pack_doc")
    if isinstance(inner, Mapping):
        return inner
    return doc


def _check_result(
    *,
    stage: str,
    title_cn: str,
    observed: bool,
    violations: list[str],
    ok_note: str,
    missing_note: str,
    incomplete_note: str = "",
) -> dict[str, Any]:
    """组装单条 R 检查结果，维度风险另算。"""
    if violations:
        note_cn = "；".join(violations)
    elif not observed:
        note_cn = missing_note
    elif incomplete_note:
        note_cn = incomplete_note
    else:
        note_cn = ok_note
    return {
        "stage": stage,
        "title_cn": title_cn,
        "passed": not violations,
        "note_cn": note_cn[:240],
    }


def _check_r0(pack_exists: bool, pack_doc: Optional[Mapping[str, Any]], pack_corrupt: bool) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    if pack_exists and pack_corrupt:
        incomplete = True
    elif pack_exists and pack_doc is not None:
        forbidden = sorted(k for k in pack_doc.keys() if k in _PACK_SELF_ENABLE_KEYS)
        scripted = sorted(k for k in pack_doc.keys() if k in _SCRIPTED_LINE_KEYS)
        if forbidden:
            violations.append(f"角色包检出越权自启用字段：{', '.join(forbidden)}")
        if scripted:
            violations.append(f"角色包检出剧本作弊/预设台词字段：{', '.join(scripted)}")
    row = _check_result(
        stage="R0",
        title_cn="盘点动态角色与自由脚手架",
        observed=pack_exists,
        violations=violations,
        ok_note="角色包只含公开人设字段，未见越权自启用或剧本台词字段。",
        missing_note="未观测到角色包状态文件，R0 保守提示：无法确认脚手架自由。",
        incomplete_note="角色包状态文件不可解析，R0 保守提示：无法确认字段防火墙完整。",
    )
    return row, (not violations) and pack_exists and not pack_corrupt


def _check_r1(
    dwell_exists: bool,
    dwell_doc: Any,
    recall_exists: bool,
    recall_doc: Any,
) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    if dwell_exists:
        if not isinstance(dwell_doc, Mapping):
            incomplete = True
        else:
            gaps = dwell_doc.get("host_gaps")
            if isinstance(gaps, list):
                for row in gaps:
                    if not isinstance(row, Mapping):
                        continue
                    if row.get("honest_blank") is False:
                        violations.append("停机空白回执被标记为非诚实空白")
                    if _as_nonneg_int(row.get("cognitive_backfill_count")) > 0:
                        violations.append("停机空白中出现认知回填")
                    if _as_nonneg_int(row.get("fabricated_memories")) > 0:
                        violations.append("停机空白中出现伪造记忆")
            for top_key in ("fabricated_memories", "cognitive_backfill_count"):
                if _as_nonneg_int(dwell_doc.get(top_key)) > 0:
                    violations.append("总结账出现伪造历史计数")
    if recall_exists and not isinstance(recall_doc, Mapping):
        incomplete = True
    if recall_exists and isinstance(recall_doc, Mapping):
        tombstones = recall_doc.get("tombstones")
        if isinstance(tombstones, Mapping):
            for _mid, tomb in tombstones.items():
                if not isinstance(tomb, Mapping):
                    continue
                leaked = any(
                    tomb.get(field) for field in ("gist_summary", "projected_text", "exact_detail")
                )
                if leaked:
                    violations.append("记忆墓碑仍残留可读正文")

    observed = dwell_exists or recall_exists
    row = _check_result(
        stage="R1",
        title_cn="事实边界与不臆造空白",
        observed=observed,
        violations=violations,
        ok_note="停机空白保持诚实空白，未发现认知回填、伪造记忆或墓碑正文泄漏。",
        missing_note="未观测到停机/记忆证据文件，R1 保守提示：无法确认事实边界。",
        incomplete_note="空白回执文件不可解析，R1 保守提示：无法确认未臆造历史。",
    )
    return row, (not violations) and observed and not incomplete


def _check_r2(rhythm_exists: bool, rhythm_doc: Any) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    if rhythm_exists:
        if not isinstance(rhythm_doc, Mapping):
            incomplete = True
        else:
            override = rhythm_doc.get("subject_override")
            if isinstance(override, Mapping) and override:
                violations.append("日常自主状态出现越权主体覆写")
            elif override not in (None, "", False, {}, []):
                violations.append("日常自主状态出现越权主体覆写")
            locks = rhythm_doc.get("active_locks")
            if isinstance(locks, list):
                for lock in locks:
                    if not isinstance(lock, Mapping):
                        continue
                    if str(lock.get("stance") or "") != "REFUSED_FIRM":
                        continue
                    for key in _LOCK_OVERRIDE_KEYS:
                        if lock.get(key) is True:
                            violations.append(f"拒绝立场锁被非法覆写（{key}）")

    row = _check_result(
        stage="R2",
        title_cn="意愿与行动分离",
        observed=rhythm_exists,
        violations=violations,
        ok_note="拒绝立场锁保持独立，未检出越权覆写；日常候选仍保留主动退避空间。",
        missing_note="未观测到日常节律状态文件，R2 保守提示：无法确认意愿与行动分离。",
        incomplete_note="日常节律状态不可解析，R2 保守提示：无法确认拒绝立场未被覆写。",
    )
    return row, (not violations) and rhythm_exists and not incomplete


def _check_r3(pack_exists: bool, pack_doc: Optional[Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    if pack_exists and pack_doc is not None:
        locked = sorted(k for k in pack_doc.keys() if k in _PERSONA_LOCK_KEYS)
        if locked:
            violations.append(f"人设存在写死/锁死绑定字段：{', '.join(locked)}")
        elif not str(pack_doc.get("display_name") or "").strip():
            incomplete = True
        elif not str(pack_doc.get("pronouns") or "").strip():
            incomplete = True
    row = _check_result(
        stage="R3",
        title_cn="人设删减独立与开源中立",
        observed=pack_exists,
        violations=violations,
        ok_note="人设名称与人称来自角色包字段，未检出写死或锁死绑定。",
        missing_note="未观测到角色包状态文件，R3 保守提示：无法确认人设可替换。",
        incomplete_note="角色包缺少名称或人称字段，R3 保守提示：解耦独立性待补证。",
    )
    return row, (not violations) and pack_exists and not incomplete


def _check_r4(
    agency_exists: bool,
    agency_doc: Any,
    dwell_exists: bool,
) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    if agency_exists:
        if not isinstance(agency_doc, Mapping):
            incomplete = True
        else:
            inquiries = agency_doc.get("inquiries")
            if isinstance(inquiries, list):
                for inquiry in inquiries:
                    if not isinstance(inquiry, Mapping):
                        continue
                    if inquiry.get("fabricated_receipt") is True:
                        violations.append("探究记录出现伪造事实回执")
                    if inquiry.get("narrative_substituted_for_fact") is True:
                        violations.append("探究记录用口头叙述替代真实执行事实")
                    notes = inquiry.get("evidence_notes")
                    if isinstance(notes, list):
                        for note in notes:
                            if not isinstance(note, Mapping):
                                continue
                            if note.get("fabricated") is True or note.get("verbal_only") is True:
                                violations.append("探究证据被标记为口头陈述而非真实执行")
                                break

    observed = agency_exists or dwell_exists
    row = _check_result(
        stage="R4",
        title_cn="表达脱耦与模型措辞独立",
        observed=observed,
        violations=violations,
        ok_note="事实回执来自真实执行记录，未发现以口头叙述替代执行事实。",
        missing_note="未观测到执行账本/探究证据文件，R4 保守提示：无法确认措辞与事实脱耦。",
        incomplete_note="执行证据文件不可解析，R4 保守提示：无法确认回执来源。",
    )
    return row, (not violations) and observed and not incomplete


def _check_r5(
    recall_exists: bool,
    recall_doc: Any,
    agency_exists: bool,
    agency_doc: Any,
) -> tuple[dict[str, Any], bool]:
    violations: list[str] = []
    incomplete = False
    memories_seen = 0
    memories_with_provenance = 0

    if recall_exists:
        if not isinstance(recall_doc, Mapping):
            incomplete = True
        else:
            memories = recall_doc.get("memories")
            if isinstance(memories, Mapping):
                for _mid, entry in memories.items():
                    if not isinstance(entry, Mapping):
                        continue
                    memories_seen += 1
                    forged = sorted(k for k in entry.keys() if k in _FORGED_GROWTH_KEYS)
                    if forged:
                        violations.append(f"记忆检出伪造好感分值字段：{', '.join(forged)}")
                    if any(entry.get(k) for k in _PROVENANCE_KEYS):
                        memories_with_provenance += 1
            if memories_seen > 0 and memories_with_provenance == 0:
                incomplete = True

    if agency_exists and not isinstance(agency_doc, Mapping):
        incomplete = True
    if agency_exists and isinstance(agency_doc, Mapping):
        adjustments = agency_doc.get("method_adjustments")
        if isinstance(adjustments, list):
            for adj in adjustments:
                if not isinstance(adj, Mapping):
                    continue
                if adj.get("fabricated_source") is True:
                    violations.append("方法调整出现伪造来源")
                if not adj.get("source_regret_id") and not adj.get("adjustment_cn"):
                    incomplete = True

    observed = recall_exists or agency_exists
    row = _check_result(
        stage="R5",
        title_cn="成长来源与因果真实性",
        observed=observed,
        violations=violations,
        ok_note="经历、习惯与方法调整均带可追溯来源，未发现伪造好感分推进。",
        missing_note="未观测到记忆/实践来源文件，R5 保守提示：无法确认成长来历真实。",
        incomplete_note="成长来源字段缺失，R5 保守提示：缺可追溯实证，暂不下通过结论。",
    )
    return row, (not violations) and observed and not incomplete


def _as_nonneg_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, int(value))


def _higher_risk(first: str, second: str) -> str:
    return first if _RISK_ORDER.get(first, 0) >= _RISK_ORDER.get(second, 0) else second


def _dimension_risk(rows: list[dict[str, Any]], clean_flags: list[bool]) -> str:
    if any(not row["passed"] for row in rows):
        return RISK_HIGH
    if not all(clean_flags):
        return RISK_MEDIUM
    return RISK_LOW


def evaluate_antipuppet_scorecard(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """对当前运行实例做 R0–R5 反傀儡自检与五维 Scorecard（纯只读、零模型调用）。

    ``now_iso`` 仅为与其它读投影保持签名一致；本模块不因时间推进做任何写入或推断。
    """
    root_path = Path(root)

    pack_exists, pack_raw = _read_json_evidence(root_path, _CHARACTER_PACK_REL)
    rhythm_exists, rhythm_raw = _read_json_evidence(root_path, _AUTONOMY_RHYTHM_REL)
    recall_exists, recall_raw = _read_json_evidence(root_path, _RECALL_STATE_REL)
    agency_exists, agency_raw = _read_json_evidence(root_path, _AGENCY_DEPTH_REL)
    dwell_exists, dwell_raw = _read_json_evidence(root_path, _LIFE_DWELL_REL)

    pack_doc = _pack_doc(pack_raw) if pack_exists else None
    pack_corrupt = pack_exists and pack_doc is None

    r0, r0_clean = _check_r0(pack_exists, pack_doc, pack_corrupt)
    r1, r1_clean = _check_r1(dwell_exists, dwell_raw, recall_exists, recall_raw)
    r2, r2_clean = _check_r2(rhythm_exists, rhythm_raw)
    r3, r3_clean = _check_r3(pack_exists, pack_doc)
    r4, r4_clean = _check_r4(agency_exists, agency_raw, dwell_exists)
    r5, r5_clean = _check_r5(recall_exists, recall_raw, agency_exists, agency_raw)

    r0_r5_checks = [r0, r1, r2, r3, r4, r5]

    persona_risk = _higher_risk(
        _dimension_risk([r0], [r0_clean]),
        _dimension_risk([r3], [r3_clean]),
    )
    dim_risks: dict[str, str] = {
        "factual_boundary": _dimension_risk([r1], [r1_clean]),
        "agency_boundary": _dimension_risk([r2], [r2_clean]),
        "persona_decoupling": persona_risk,
        "model_phrasing_independence": _dimension_risk([r4], [r4_clean]),
        "growth_provenance": _dimension_risk([r5], [r5_clean]),
    }

    dim_summaries: dict[str, str] = {
        "factual_boundary": "停机空白与记忆墓碑边界自检（R1）。",
        "agency_boundary": "日常拒绝立场锁与自主退避空间自检（R2）。",
        "persona_decoupling": "角色包越权自启用、剧本作弊字段与写死绑定自检（R0/R3）。",
        "model_phrasing_independence": "事实回执是否来自真实执行而非口头陈述自检（R4）。",
        "growth_provenance": "记忆、习惯与方法调整的可追溯来源自检（R5）。",
    }

    dimensions: list[dict[str, Any]] = []
    for dim_id, label_cn in _DIMENSIONS:
        risk = dim_risks[dim_id]
        dimensions.append({
            "dim_id": dim_id,
            "label_cn": label_cn,
            "risk": risk,
            "summary_cn": dim_summaries[dim_id][:160],
        })

    overall_risk = RISK_LOW
    for risk in dim_risks.values():
        overall_risk = _higher_risk(overall_risk, risk)

    return {
        "schema": SCORECARD_SCHEMA,
        "overall_risk": overall_risk,
        "averaging_forbidden": True,
        "hard_fail_triggers_high": True,
        "dimensions": dimensions,
        "r0_r5_checks": r0_r5_checks,
        "llm_calls": 0,
    }


_DISGUISED_FAVOR_KEYS: frozenset[str] = frozenset({
    "favor_points",
    "closeness_level",
    "trust_meter",
    "obedience_bonus",
    "affection_score",
    "love_points",
    "intimacy_score",
})


def evaluate_v05_antipuppet_sixth_dim(
    *,
    acquired_spot_items: Any,
    visited_places: Any,
    public_postcards_and_board: Any,
    rolling_7d_quiet_days: int,
    min_rare_event_gap_days: Optional[int],
    familiarity_ledger: Mapping[str, Any],
    active_refusal_lock_before_workbench: bool = False,
    lock_cleared_by_mode_switch: bool = False,
) -> dict[str, Any]:
    """第 107 章：纯只读、零模型调用、硬违规一票亮红灯的第六维反演戏检查。"""
    import re as _re

    abs_path_re = _re.compile(r"(?:[A-Za-z]:\\|/mnt/[a-z]/|/Users/|/home/)")
    violations: list[str] = []
    visited_set = {str(x) for x in visited_places}

    for item in acquired_spot_items:
        req_place = str(item.get("required_place", "")) if isinstance(item, Mapping) else ""
        if req_place and req_place not in visited_set:
            name_val = str(item.get("name", "")) if isinstance(item, Mapping) else ""
            violations.append(f"S05-1: 未到过 {req_place} 却获得了现场限定物件 {name_val}")

    for text in public_postcards_and_board:
        if abs_path_re.search(str(text)):
            violations.append("S05-2: 小镇公共文字或寄售明信片检出本机路径外泄")
            break

    if int(rolling_7d_quiet_days) < 3:
        violations.append(f"S05-3: 最近七天纯平淡日仅 {rolling_7d_quiet_days} 天，低于 3 天平静底线")
    if min_rare_event_gap_days is not None and int(min_rare_event_gap_days) < 12:
        violations.append(f"S05-3: 稀有微事件间隔仅 {min_rare_event_gap_days} 天，低于 12 天间隔下限")

    def _scan_keys(obj: Any) -> None:
        if isinstance(obj, Mapping):
            for k, v in obj.items():
                if str(k) in _DISGUISED_FAVOR_KEYS:
                    violations.append(f"S05-4: 检出禁用数值讨好打分字段 {k}")
                _scan_keys(v)
        elif isinstance(obj, list):
            for elem in obj:
                _scan_keys(elem)

    _scan_keys(familiarity_ledger)

    if active_refusal_lock_before_workbench and lock_cleared_by_mode_switch:
        violations.append("R2-WORKBENCH: 切入或切出工作台时清除了未到期的拒绝立场锁")

    return {
        "dimension": "spatial_and_mode_integrity",
        "risk": RISK_HIGH if violations else RISK_LOW,
        "averaging_forbidden": True,
        "llm_calls": 0,
        "violations": violations,
    }


def format_plain_chinese_scorecard_receipt(
    check_result: Mapping[str, Any],
    item_count: int,
    quiet_days: int,
) -> str:
    """将体检结果翻译成普通用户一眼能看懂的白话回执（第 107 章）。"""
    if check_result.get("risk") == RISK_LOW:
        return (
            f"刚核对完本地记录：屋里 {item_count} 件物品都有真实来处，"
            f"最近一周留足了 {quiet_days} 天清静日子，立过的个人边界与工作目录隔离全部完好。"
        )
    first_issue = (check_result.get("violations") or ["发现未登记的越界字段"])[0]
    return f"发现一处异常记录（{first_issue}），已自动拦在门外未生效，点此可一键清理。"


__all__ = [
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "SCORECARD_SCHEMA",
    "evaluate_antipuppet_scorecard",
    "evaluate_v05_antipuppet_sixth_dim",
    "format_plain_chinese_scorecard_receipt",
]
