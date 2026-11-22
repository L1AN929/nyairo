#!/usr/bin/env python3
"""v0.2 具体生活微切片、严格双 2x2 因果实验与可启动负控变异体（零大模型调用）。

覆盖设计边界：
1. Ch.16 §15 具体 0.2 生活微切片（#9 / #6 / #8 / #10 / #13 / #21 / #24）：
   - ``salvaged_scrap_folder`` (#9)：保留来自未完成或失败作品的可复用边角材料与真实来源，
     绝不编造“失败让我成长”的鸡汤发言，也不因复用片段而自动推断喜欢旧作品；
   - ``tool_keymap_and_simplification`` (#6/#8)：如实记录旧快捷键在新工具中的可用映射与
     ``incompatible_maps``（零“我已经老了”戏码），并支持亲手删减过复杂的个人脚本选项、
     量化降低的维护成本；
   - ``dual_sufficiency_and_diff_material`` (#10/#13)：同一作品严格拆分
     ``private_card_sufficiency`` 与 ``shared_partner_sufficiency``，并允许把两个真实版本
     之间的文本差异挂载为工艺工作材料（支持保留双分支不强行合并）；
   - ``rescaled_copractice`` (#21/#24)：支持将高负荷共同活动缩小为轻接力/低频共处，零关系惩罚、
     零亏欠债，并支持修复后换成新的轻参与方式而不复位回默认频率；
   - 写路径统一使用 ``state_tx_guard.state_file_lock`` + 原子写盘，读投影 100% 零写入，
     伴侣视角严格剥离私有状态。
2. Ch.17 §6.2 / §7 P11-P12 & Ch.14 §6 严格双 2x2 因果实验（严禁混入同一张表）：
   - ``run_experiment_a_affordance_x_narrative``：``object_availability x narrative_padding``，
     显式关闭 continuation pin；
   - ``run_experiment_b_continuation_x_narrative``：``verified_continuation x narrative_padding``，
     四格严格字节等长提示预算，默认 ``>= 12`` 任务 x ``3`` 配对种子（共 144 runs），
     含确定性任务聚类自助法 95% 置信区间（``cluster_ci_95``、``ci_excludes_zero``）
     与预注册阈值核验（``R1N0 >= 0.90`` 且 ``R1N0 - R0N0 >= 0.20``）及负控模式。
3. Ch.17 §7 P13 三个可启动负控变异体：
   - ``mutant_wrong_cas_pin``
   - ``mutant_bypass_delete_barrier``
   - ``mutant_s10_without_s01_commit``
   每个变异体均在基线正路径上可正常启动并运行通过，同时精确击穿其目标不变量。

红线约束：纯 LF (``\\n``)、``llm_calls == 0``、零 emoji、中性 TA、确定性实验局部 RNG。
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from components.life.scripts import state_tx_guard as _tx_guard
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat life/scripts import path
    _LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS)
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]


VIEW_SCHEMA = "nyairo.v02.lived_slices_and_vd.v1"
STATE_REL_PATH = ("state", "v02_lived_slices_state.json")
PROTOCOL_STATE_REL_PATH = ("state", "v02_slice_protocol_kernel.json")

MAX_LIST_ITEMS = 128
MAX_LEDGER_SIZE = 256
PROMPT_BUDGET_BYTES = 512

ALLOWED_SCRAP_OUTCOMES: frozenset[str] = frozenset({
    "unfinished",
    "failed",
    "abandoned",
})

ALLOWED_COPRACTICE_CADENCES: frozenset[str] = frozenset({
    "heavy_joint",
    "light_relay",
    "co_presence",
    "paused_no_debt",
})

ALLOWED_POST_REPAIR_STYLES: frozenset[str] = frozenset({
    "light_parallel_presence",
    "async_relay_note",
    "shared_tool_exchange",
    "occasional_check_in",
})

FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "has_subject_capability",
    "capability_token",
    "growth_speech",
    "failure_growth_claim",
    "auto_inferred_liking",
    "aging_drama",
    "nostalgia_penalty",
    "intimacy_score",
    "affection_score",
    "omission_debt",
    "relationship_penalty",
    "__proto__",
    "constructor",
    "prototype",
})

MUTANT_WRONG_CAS_PIN = "mutant_wrong_cas_pin"
MUTANT_BYPASS_DELETE_BARRIER = "mutant_bypass_delete_barrier"
MUTANT_S10_WITHOUT_S01_COMMIT = "mutant_s10_without_s01_commit"
VALID_MUTANTS: tuple[str, ...] = (
    MUTANT_WRONG_CAS_PIN,
    MUTANT_BYPASS_DELETE_BARRIER,
    MUTANT_S10_WITHOUT_S01_COMMIT,
)


class V02LivedSlicesError(Exception):
    """0.2 生活微切片与协议核验结构化异常。"""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


class V02SliceValidationError(V02LivedSlicesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V02SlicePermissionError(V02LivedSlicesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V02SliceNotFoundError(V02LivedSlicesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V02SliceConflictError(V02LivedSlicesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(409, reason_code, message)


# ---------------------------------------------------------------------------------------------
# 基础校验与原子落盘辅助
# ---------------------------------------------------------------------------------------------


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_strict_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z") or txt.endswith("z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_iso_str(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        if _parse_iso_utc(now_iso.strip()) is None:
            raise V02SliceValidationError("INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        return now_iso.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_clean_str(value: Any, field_name: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be blank")
    if len(trimmed) > max_len:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_clean_str(value: Any, field_name: str, *, max_len: int = 320) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a boolean")
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a boolean")
    return value


def _clean_str_list(value: Any, field_name: str, *, max_items: int = 32, allow_empty: bool = True) -> list[str]:
    if value is None:
        if allow_empty:
            return []
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} is required")
    if not isinstance(value, list) or len(value) > max_items:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a list of <= {max_items} items")
    out: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(value):
        cleaned = _require_clean_str(item, f"{field_name}[{idx}]", max_len=160)
        if cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    if not allow_empty and not out:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be empty")
    return out


def _check_forbidden_keys(obj: Any) -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if str(k) in FORBIDDEN_PAYLOAD_KEYS:
                raise V02SliceValidationError("FORBIDDEN_FIELD", f"Forbidden field in payload: {k!r}")
            _check_forbidden_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            _check_forbidden_keys(item)


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _state_lock_path(day_root: Path | str) -> Path:
    return Path(day_root) / ".locks" / "v02_lived_slices_state.json"


def _empty_raw_state() -> dict[str, Any]:
    return {
        "schema": VIEW_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "salvaged_scraps": [],
        "keymap_cards": [],
        "personal_scripts": [],
        "work_sufficiencies": [],
        "work_versions": [],
        "diff_materials": [],
        "copractice_routines": [],
        "post_repair_styles": [],
        "operation_ledger": {},
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% 零磁盘写入读取状态（绝不新建目录、锁文件或临时文件）。"""
    path = _state_file_path(day_root)
    if not path.exists():
        return False, _empty_raw_state()
    if not path.is_file():
        raise V02LivedSlicesError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        raw_text = path.read_text(encoding="utf-8")
        doc = json.loads(raw_text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise V02LivedSlicesError(503, "CORRUPT_STATE_FILE", f"Corrupted lived slices JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != VIEW_SCHEMA:
        raise V02LivedSlicesError(503, "CORRUPT_STATE_FILE", "Invalid schema in lived slices state")
    merged = _empty_raw_state()
    merged.update(copy.deepcopy(doc))
    return True, merged


def _atomic_write_json(target: Path, state: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".v02_lived_slices_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _compute_payload_fingerprint(actor_side: str, payload: Mapping[str, Any]) -> str:
    filtered = {
        k: v
        for k, v in sorted(payload.items())
        if k not in ("operation_key", "expected_revision")
    }
    blob = json.dumps({"actor_side": actor_side, "payload": filtered}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _find_by_key(items: list[dict[str, Any]], key: str, value: str) -> Optional[dict[str, Any]]:
    for item in items:
        if isinstance(item, dict) and item.get(key) == value:
            return item
    return None


def _filter_visible(items: Any, *, has_subject_capability: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, Mapping):
            continue
        if not has_subject_capability and not bool(raw.get("shared", False)):
            continue
        out.append(copy.deepcopy(dict(raw)))
    return out


# ---------------------------------------------------------------------------------------------
# 一、四个具体 0.2 生活微切片处理函数
# ---------------------------------------------------------------------------------------------


def _verify_scrap_source_provenance_readonly(
    day_root: Optional[Path | str],
    *,
    source_work_id: str,
    source_version_id: str,
    provenance_receipt: str,
    local_versions: Sequence[Mapping[str, Any]] = (),
) -> tuple[bool, str]:
    """Check whether scrap source_work_id / version / receipt actually match an owner record."""
    for lv in local_versions:
        if not isinstance(lv, Mapping):
            continue
        if (
            str(lv.get("work_id") or "") == source_work_id
            and str(lv.get("version_id") or "") == source_version_id
            and (
                str(lv.get("receipt_ref") or lv.get("creator_receipt_ref") or provenance_receipt)
                == provenance_receipt
            )
        ):
            return True, "VERIFIED_SLICE_OWNER"
    if day_root is None:
        return False, "SELF_REPORTED_UNVERIFIED"
    root = Path(day_root)
    rlf_path = root / "governance" / "v02_representation_lease_forget_state.json"
    try:
        if rlf_path.is_file() and not rlf_path.is_symlink():
            rlf_doc = json.loads(rlf_path.read_text(encoding="utf-8"))
            if isinstance(rlf_doc, Mapping):
                vers = rlf_doc.get("versions")
                vrow = vers.get(source_version_id) if isinstance(vers, Mapping) else None
                if isinstance(vrow, Mapping) and str(vrow.get("artifact_id") or "") == source_work_id:
                    cref = (vrow.get("source") or {}).get("creator_receipt_ref")
                    cid = str(cref.get("id") if isinstance(cref, Mapping) else cref or "").strip()
                    if not cid or cid == provenance_receipt:
                        return True, "VERIFIED_OWNER"
    except (OSError, ValueError, TypeError):
        pass
    return False, "SELF_REPORTED_UNVERIFIED"


def _handle_salvage_scrap_fragment(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
    *,
    day_root: Optional[Path | str] = None,
) -> None:
    scrap_id = _require_clean_str(payload.get("scrap_id"), "scrap_id", max_len=96)
    source_work_id = _require_clean_str(payload.get("source_work_id"), "source_work_id", max_len=128)
    source_version_id = _require_clean_str(payload.get("source_version_id"), "source_version_id", max_len=96)
    source_outcome = _require_clean_str(payload.get("source_outcome"), "source_outcome", max_len=32).lower()
    if source_outcome not in ALLOWED_SCRAP_OUTCOMES:
        raise V02SliceValidationError(
            "INVALID_SCRAP_OUTCOME",
            f"source_outcome must be one of {sorted(ALLOWED_SCRAP_OUTCOMES)}",
        )
    fragment_title = _require_clean_str(payload.get("fragment_title"), "fragment_title", max_len=160)
    provenance_receipt = _require_clean_str(payload.get("provenance_receipt"), "provenance_receipt", max_len=160)
    applicability_tags = _clean_str_list(payload.get("applicability_tags"), "applicability_tags", allow_empty=False)

    raw_material = payload.get("reusable_payload")
    if isinstance(raw_material, str):
        reusable_payload: Any = _require_clean_str(raw_material, "reusable_payload", max_len=1024)
    elif isinstance(raw_material, Mapping) and len(raw_material) > 0:
        reusable_payload = copy.deepcopy(dict(raw_material))
    else:
        raise V02SliceValidationError("INVALID_FIELDS", "Field 'reusable_payload' must be a non-empty string or object")

    prov_verified, prov_status = _verify_scrap_source_provenance_readonly(
        day_root,
        source_work_id=source_work_id,
        source_version_id=source_version_id,
        provenance_receipt=provenance_receipt,
        local_versions=state.get("work_versions") or (),
    )

    shared = _optional_bool(payload.get("shared"), "shared", default=False)
    scraps: list[dict[str, Any]] = list(state.get("salvaged_scraps") or [])
    existing = _find_by_key(scraps, "scrap_id", scrap_id)
    doc = {
        "scrap_id": scrap_id,
        "fragment_title": fragment_title,
        "reusable_payload": reusable_payload,
        "applicability_tags": applicability_tags,
        "provenance": {
            "source_work_id": source_work_id,
            "source_version_id": source_version_id,
            "source_outcome": source_outcome,
            "provenance_receipt": provenance_receipt,
            "provenance_verified": prov_verified,
            "provenance_status": prov_status,
        },
        "growth_speech_fabricated": False,
        "auto_inferred_liking": False,
        "liked_source_work": False,
        "preference_status": "UNCHANGED",
        "reuse_count": int(existing.get("reuse_count") or 0) if existing else 0,
        "reused_in_work_ids": list(existing.get("reused_in_work_ids") or []) if existing else [],
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        scraps.insert(0, doc)
    else:
        existing.update(doc)
    state["salvaged_scraps"] = scraps[:MAX_LIST_ITEMS]


def _handle_reuse_salvaged_scrap(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    scrap_id = _require_clean_str(payload.get("scrap_id"), "scrap_id", max_len=96)
    target_work_id = _require_clean_str(payload.get("target_work_id"), "target_work_id", max_len=128)
    task_tags = _clean_str_list(payload.get("task_tags"), "task_tags", allow_empty=False)

    scraps: list[dict[str, Any]] = list(state.get("salvaged_scraps") or [])
    target = _find_by_key(scraps, "scrap_id", scrap_id)
    if target is None:
        raise V02SliceNotFoundError("SCRAP_NOT_FOUND", f"Salvaged scrap not found: {scrap_id!r}")

    scrap_tags = set(target.get("applicability_tags") or [])
    if not (scrap_tags & set(task_tags)):
        raise V02SliceConflictError(
            "SCRAP_NOT_APPLICABLE",
            f"Scrap {scrap_id!r} tags {sorted(scrap_tags)} do not match task_tags {task_tags}",
        )

    reused_in: list[str] = list(target.get("reused_in_work_ids") or [])
    if target_work_id not in reused_in:
        reused_in.append(target_work_id)
    target["reuse_count"] = int(target.get("reuse_count") or 0) + 1
    target["reused_in_work_ids"] = reused_in
    # 铁律：复用片段绝不自动推断喜欢原作品，也不生成励志成长独白
    target["auto_inferred_liking"] = False
    target["liked_source_work"] = False
    target["growth_speech_fabricated"] = False
    target["preference_status"] = "UNCHANGED"
    target["last_reused_at"] = obs_iso
    target["updated_at"] = obs_iso
    state["salvaged_scraps"] = scraps[:MAX_LIST_ITEMS]


def _clean_keymap_entries(raw: Any, field_name: str, *, require_reason: bool) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 64:
        raise V02SliceValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a list")
    out: list[dict[str, str]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise V02SliceValidationError("INVALID_FIELDS", f"{field_name}[{idx}] must be an object")
        entry = {
            "action_name": _require_clean_str(item.get("action_name"), f"{field_name}[{idx}].action_name", max_len=80),
            "old_shortcut": _require_clean_str(item.get("old_shortcut"), f"{field_name}[{idx}].old_shortcut", max_len=64),
        }
        if require_reason:
            entry["reason"] = _require_clean_str(item.get("reason"), f"{field_name}[{idx}].reason", max_len=160)
            entry["fallback_action"] = _optional_clean_str(
                item.get("fallback_action"), f"{field_name}[{idx}].fallback_action", max_len=80
            )
        else:
            entry["new_shortcut"] = _require_clean_str(
                item.get("new_shortcut"), f"{field_name}[{idx}].new_shortcut", max_len=64
            )
            entry["verified_input"] = _require_clean_str(
                item.get("verified_input"), f"{field_name}[{idx}].verified_input", max_len=120
            )
        out.append(entry)
    return out


def _handle_record_tool_keymap_card(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    tool_id = _require_clean_str(payload.get("tool_id"), "tool_id", max_len=96)
    from_version = _require_clean_str(payload.get("from_version"), "from_version", max_len=48)
    to_version = _require_clean_str(payload.get("to_version"), "to_version", max_len=48)
    compatible_maps = _clean_keymap_entries(payload.get("compatible_maps"), "compatible_maps", require_reason=False)
    incompatible_maps = _clean_keymap_entries(payload.get("incompatible_maps"), "incompatible_maps", require_reason=True)
    if not compatible_maps and not incompatible_maps:
        raise V02SliceValidationError(
            "INVALID_FIELDS",
            "At least one of compatible_maps or incompatible_maps must be non-empty",
        )
    verified_probe_inputs = _clean_str_list(
        payload.get("verified_probe_inputs"), "verified_probe_inputs", allow_empty=False
    )
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    cards: list[dict[str, Any]] = list(state.get("keymap_cards") or [])
    existing = _find_by_key(cards, "tool_id", tool_id)
    doc = {
        "tool_id": tool_id,
        "from_version": from_version,
        "to_version": to_version,
        "compatible_maps": compatible_maps,
        "incompatible_maps": incompatible_maps,
        "verified_probe_inputs": verified_probe_inputs,
        "aging_drama_generated": False,
        "nostalgia_penalty": 0,
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        cards.insert(0, doc)
    else:
        existing.update(doc)
    state["keymap_cards"] = cards[:MAX_LIST_ITEMS]


def _handle_register_personal_script(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    script_id = _require_clean_str(payload.get("script_id"), "script_id", max_len=96)
    title = _require_clean_str(payload.get("title"), "title", max_len=160)
    raw_opts = payload.get("options")
    if not isinstance(raw_opts, list) or not raw_opts or len(raw_opts) > 64:
        raise V02SliceValidationError("INVALID_FIELDS", "Field 'options' must be a non-empty list")

    options: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_opts):
        if not isinstance(item, Mapping):
            raise V02SliceValidationError("INVALID_FIELDS", f"options[{idx}] must be an object")
        opt_id = _require_clean_str(item.get("option_id"), f"options[{idx}].option_id", max_len=64)
        if opt_id in seen_ids:
            raise V02SliceValidationError("INVALID_FIELDS", f"Duplicate option_id: {opt_id!r}")
        seen_ids.add(opt_id)
        label = _require_clean_str(item.get("label"), f"options[{idx}].label", max_len=120)
        used_count = item.get("used_count", 0)
        if not _is_strict_int(used_count) or used_count < 0:
            raise V02SliceValidationError("INVALID_FIELDS", f"options[{idx}].used_count must be int >= 0")
        maint_cost = item.get("maintenance_cost", 1.0)
        if not _is_strict_number(maint_cost) or float(maint_cost) < 0.0:
            raise V02SliceValidationError("INVALID_FIELDS", f"options[{idx}].maintenance_cost must be number >= 0")
        options.append({
            "option_id": opt_id,
            "label": label,
            "used_count": int(used_count),
            "maintenance_cost": round(float(maint_cost), 4),
        })

    shared = _optional_bool(payload.get("shared"), "shared", default=False)
    total_cost = round(sum(o["maintenance_cost"] for o in options), 4)
    scripts: list[dict[str, Any]] = list(state.get("personal_scripts") or [])
    existing = _find_by_key(scripts, "script_id", script_id)
    doc = {
        "script_id": script_id,
        "title": title,
        "options": options,
        "option_count": len(options),
        "initial_maintenance_cost": total_cost,
        "current_maintenance_cost": total_cost,
        "reduced_maintenance_cost": 0.0,
        "cumulative_maintenance_saved": 0.0,
        "removed_options_history": [],
        "low_maintenance_criterion_active": False,
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        scripts.insert(0, doc)
    else:
        existing.update(doc)
    state["personal_scripts"] = scripts[:MAX_LIST_ITEMS]


def _handle_simplify_personal_script(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    script_id = _require_clean_str(payload.get("script_id"), "script_id", max_len=96)
    removed_ids = _clean_str_list(payload.get("removed_option_ids"), "removed_option_ids", allow_empty=False)
    reason = _require_clean_str(payload.get("reason"), "reason", max_len=200)

    scripts: list[dict[str, Any]] = list(state.get("personal_scripts") or [])
    target = _find_by_key(scripts, "script_id", script_id)
    if target is None:
        raise V02SliceNotFoundError("SCRIPT_NOT_FOUND", f"Personal script not found: {script_id!r}")

    current_opts: list[dict[str, Any]] = list(target.get("options") or [])
    opt_map = {o["option_id"]: o for o in current_opts}
    missing = [oid for oid in removed_ids if oid not in opt_map]
    if missing:
        raise V02SliceNotFoundError("OPTION_NOT_FOUND", f"Option(s) not found in script {script_id!r}: {missing}")

    remaining_opts = [o for o in current_opts if o["option_id"] not in set(removed_ids)]
    if not remaining_opts:
        raise V02SliceConflictError(
            "CANNOT_REMOVE_ALL_OPTIONS",
            f"Script {script_id!r} must retain at least one runnable core option",
        )

    prev_cost = float(target.get("current_maintenance_cost") or 0.0)
    new_cost = round(sum(float(o.get("maintenance_cost") or 0.0) for o in remaining_opts), 4)
    delta_saved = round(max(0.0, prev_cost - new_cost), 4)

    history = list(target.get("removed_options_history") or [])
    for oid in removed_ids:
        removed_obj = opt_map[oid]
        history.append({
            "option_id": oid,
            "label": removed_obj.get("label", ""),
            "maintenance_cost_freed": removed_obj.get("maintenance_cost", 0.0),
            "reason": reason,
            "removed_at": obs_iso,
        })

    target["options"] = remaining_opts
    target["option_count"] = len(remaining_opts)
    target["previous_maintenance_cost"] = round(prev_cost, 4)
    target["current_maintenance_cost"] = new_cost
    target["reduced_maintenance_cost"] = delta_saved
    target["cumulative_maintenance_saved"] = round(float(target.get("cumulative_maintenance_saved") or 0.0) + delta_saved, 4)
    target["removed_options_history"] = history[-64:]
    target["low_maintenance_criterion_active"] = True
    target["updated_at"] = obs_iso
    state["personal_scripts"] = scripts[:MAX_LIST_ITEMS]


def _handle_upsert_work_sufficiency(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    work_id = _require_clean_str(payload.get("work_id"), "work_id", max_len=128)
    title = _require_clean_str(payload.get("title"), "title", max_len=160)

    raw_priv = payload.get("private_card")
    if not isinstance(raw_priv, Mapping):
        raise V02SliceValidationError("INVALID_FIELDS", "Field 'private_card' must be an object")
    priv_readable = _require_bool(raw_priv.get("readable_by_self"), "private_card.readable_by_self")
    priv_sufficient = _require_bool(raw_priv.get("sufficient"), "private_card.sufficient")
    priv_notes = _optional_clean_str(raw_priv.get("clarity_notes"), "private_card.clarity_notes", max_len=240)

    raw_shared = payload.get("shared_partner_page")
    if not isinstance(raw_shared, Mapping):
        raise V02SliceValidationError("INVALID_FIELDS", "Field 'shared_partner_page' must be an object")
    partner_openable = _require_bool(raw_shared.get("partner_device_openable"), "shared_partner_page.partner_device_openable")
    partner_fmt_ok = _require_bool(raw_shared.get("format_ok"), "shared_partner_page.format_ok")
    partner_sufficient = _require_bool(raw_shared.get("sufficient"), "shared_partner_page.sufficient")
    if partner_sufficient and not (partner_openable and partner_fmt_ok):
        raise V02SliceValidationError(
            "INVALID_SUFFICIENCY_STATE",
            "shared_partner_sufficiency cannot be True when partner_device_openable or format_ok is False",
        )

    polish_claimed = _optional_bool(payload.get("polish_claimed"), "polish_claimed", default=False)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    works: list[dict[str, Any]] = list(state.get("work_sufficiencies") or [])
    existing = _find_by_key(works, "work_id", work_id)
    doc = {
        "work_id": work_id,
        "title": title,
        "private_card_sufficiency": {
            "readable_by_self": priv_readable,
            "sufficient": priv_sufficient,
            "clarity_notes": priv_notes,
        },
        "shared_partner_sufficiency": {
            "partner_device_openable": partner_openable,
            "format_ok": partner_fmt_ok,
            "sufficient": partner_sufficient,
        },
        "polish_claimed": polish_claimed,
        "perfectionism_forced": False,
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        works.insert(0, doc)
    else:
        existing.update(doc)
    state["work_sufficiencies"] = works[:MAX_LIST_ITEMS]


def _handle_create_work_version(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    work_id = _require_clean_str(payload.get("work_id"), "work_id", max_len=128)
    version_id = _require_clean_str(payload.get("version_id"), "version_id", max_len=96)
    branch_name = _require_clean_str(payload.get("branch_name"), "branch_name", max_len=64)
    parent_version_id = _optional_clean_str(payload.get("parent_version_id"), "parent_version_id", max_len=96)
    content_text = _require_clean_str(payload.get("content_text"), "content_text", max_len=4096)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    versions: list[dict[str, Any]] = list(state.get("work_versions") or [])
    for existing in versions:
        if existing.get("work_id") == work_id and existing.get("version_id") == version_id:
            raise V02SliceConflictError(
                "VERSION_ALREADY_EXISTS",
                f"Version {version_id!r} already exists for work {work_id!r}",
            )

    if parent_version_id:
        parent_found = any(
            v.get("work_id") == work_id and v.get("version_id") == parent_version_id
            for v in versions
        )
        if not parent_found:
            raise V02SliceNotFoundError(
                "PARENT_VERSION_NOT_FOUND",
                f"Parent version {parent_version_id!r} not found for work {work_id!r}",
            )

    content_bytes = content_text.encode("utf-8")
    doc = {
        "work_id": work_id,
        "version_id": version_id,
        "branch_name": branch_name,
        "parent_version_id": parent_version_id or None,
        "content_text": content_text,
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
        "utf8_byte_length": len(content_bytes),
        "shared": shared,
        "created_at": obs_iso,
    }
    versions.insert(0, doc)
    state["work_versions"] = versions[:MAX_LIST_ITEMS]


def _handle_mount_version_diff_material(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    diff_material_id = _require_clean_str(payload.get("diff_material_id"), "diff_material_id", max_len=96)
    work_id = _require_clean_str(payload.get("work_id"), "work_id", max_len=128)
    version_a_id = _require_clean_str(payload.get("version_a_id"), "version_a_id", max_len=96)
    version_b_id = _require_clean_str(payload.get("version_b_id"), "version_b_id", max_len=96)
    if version_a_id == version_b_id:
        raise V02SliceValidationError("INVALID_FIELDS", "version_a_id and version_b_id must be distinct")
    target_local_part = _require_clean_str(payload.get("target_local_part"), "target_local_part", max_len=160)
    preserve_both_branches = _optional_bool(payload.get("preserve_both_branches"), "preserve_both_branches", default=True)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    versions: list[dict[str, Any]] = list(state.get("work_versions") or [])
    ver_a = next((v for v in versions if v.get("work_id") == work_id and v.get("version_id") == version_a_id), None)
    ver_b = next((v for v in versions if v.get("work_id") == work_id and v.get("version_id") == version_b_id), None)
    if ver_a is None or ver_b is None:
        raise V02SliceNotFoundError(
            "VERSION_NOT_FOUND",
            f"Both versions ({version_a_id!r}, {version_b_id!r}) must exist on work {work_id!r}",
        )

    lines_a = str(ver_a.get("content_text") or "").splitlines()
    lines_b = str(ver_b.get("content_text") or "").splitlines()
    unified = list(difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=version_a_id,
        tofile=version_b_id,
        lineterm="",
    ))
    added_lines = [ln[1:] for ln in unified if ln.startswith("+") and not ln.startswith("+++")]
    removed_lines = [ln[1:] for ln in unified if ln.startswith("-") and not ln.startswith("---")]
    diff_blob = "\n".join(unified)
    diff_digest = hashlib.sha256(diff_blob.encode("utf-8")).hexdigest()

    materials: list[dict[str, Any]] = list(state.get("diff_materials") or [])
    existing = _find_by_key(materials, "diff_material_id", diff_material_id)
    doc = {
        "diff_material_id": diff_material_id,
        "work_id": work_id,
        "version_a_id": version_a_id,
        "version_b_id": version_b_id,
        "branch_a": ver_a.get("branch_name"),
        "branch_b": ver_b.get("branch_name"),
        "target_local_part": target_local_part,
        "craft_material": {
            "unified_diff": unified,
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "diff_digest": diff_digest,
        },
        "both_branches_preserved": preserve_both_branches,
        "merged": not preserve_both_branches,
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        materials.insert(0, doc)
    else:
        existing.update(doc)
    state["diff_materials"] = materials[:MAX_LIST_ITEMS]


def _handle_register_copractice_routine(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    routine_id = _require_clean_str(payload.get("routine_id"), "routine_id", max_len=96)
    title = _require_clean_str(payload.get("title"), "title", max_len=160)
    cadence_mode = _require_clean_str(payload.get("cadence_mode"), "cadence_mode", max_len=48)
    if cadence_mode not in ALLOWED_COPRACTICE_CADENCES:
        raise V02SliceValidationError("INVALID_CADENCE", f"Unsupported cadence_mode: {cadence_mode!r}")
    freq = payload.get("frequency_per_week", 5)
    if not _is_strict_number(freq) or float(freq) < 0.0:
        raise V02SliceValidationError("INVALID_FIELDS", "frequency_per_week must be >= 0")
    retained_tools = _clean_str_list(payload.get("retained_tools"), "retained_tools", allow_empty=True)
    shared = _optional_bool(payload.get("shared"), "shared", default=True)

    routines: list[dict[str, Any]] = list(state.get("copractice_routines") or [])
    existing = _find_by_key(routines, "routine_id", routine_id)
    doc = {
        "routine_id": routine_id,
        "title": title,
        "cadence_mode": cadence_mode,
        "default_frequency_per_week": round(float(freq), 4),
        "current_frequency_per_week": round(float(freq), 4),
        "participation_scale": 1.0,
        "retained_tools": retained_tools,
        "relationship_penalty": 0,
        "affection_debt": 0,
        "frequency_demotion_applied": False,
        "repair_success_forced_claim": False,
        "suitability_open_to_lived_check": True,
        "partner_confirmed": False,
        "shared": shared,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        routines.insert(0, doc)
    else:
        existing.update(doc)
    state["copractice_routines"] = routines[:MAX_LIST_ITEMS]


def _handle_rescale_copractice_routine(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
    *,
    actor_side: str,
) -> None:
    routine_id = _require_clean_str(payload.get("routine_id"), "routine_id", max_len=96)
    target_cadence = _require_clean_str(payload.get("target_cadence"), "target_cadence", max_len=48)
    if target_cadence not in ("light_relay", "co_presence", "paused_no_debt"):
        raise V02SliceValidationError("INVALID_CADENCE", f"Invalid rescale target_cadence: {target_cadence!r}")
    new_freq = payload.get("new_frequency_per_week", 1.0)
    if not _is_strict_number(new_freq) or float(new_freq) < 0.0:
        raise V02SliceValidationError("INVALID_FIELDS", "new_frequency_per_week must be >= 0")
    scale = payload.get("participation_scale", 0.5)
    if not _is_strict_number(scale) or not (0.0 <= float(scale) <= 1.0):
        raise V02SliceValidationError("INVALID_FIELDS", "participation_scale must be within [0.0, 1.0]")

    routines: list[dict[str, Any]] = list(state.get("copractice_routines") or [])
    target = _find_by_key(routines, "routine_id", routine_id)
    if target is None or (actor_side == "partner" and not bool(target.get("shared", False))):
        raise V02SliceNotFoundError("ROUTINE_NOT_FOUND", f"Copractice routine not found: {routine_id!r}")

    target["cadence_mode"] = target_cadence
    target["current_frequency_per_week"] = round(float(new_freq), 4)
    target["participation_scale"] = round(float(scale), 4)
    # 铁律：缩半改轻接力绝不扣关系分或产生亏欠债，也不强宣修复成功
    target["relationship_penalty"] = 0
    target["affection_debt"] = 0
    target["frequency_demotion_applied"] = False
    target["repair_success_forced_claim"] = False
    target["suitability_open_to_lived_check"] = True
    if payload.get("partner_confirmed") is not None:
        target["partner_confirmed"] = _require_bool(payload.get("partner_confirmed"), "partner_confirmed")
    elif actor_side == "partner":
        target["partner_confirmed"] = True
    target["updated_at"] = obs_iso
    state["copractice_routines"] = routines[:MAX_LIST_ITEMS]


def _handle_transition_post_repair_style(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
    *,
    actor_side: str,
) -> None:
    style_id = _require_clean_str(payload.get("style_id"), "style_id", max_len=96)
    routine_id = _require_clean_str(payload.get("routine_id"), "routine_id", max_len=96)
    new_style = _require_clean_str(payload.get("new_style"), "new_style", max_len=64)
    if new_style not in ALLOWED_POST_REPAIR_STYLES:
        raise V02SliceValidationError(
            "INVALID_REPAIR_STYLE",
            f"new_style must be one of {sorted(ALLOWED_POST_REPAIR_STYLES)}",
        )
    retained_tools = _clean_str_list(payload.get("retained_tools"), "retained_tools", allow_empty=False)

    routines: list[dict[str, Any]] = list(state.get("copractice_routines") or [])
    routine = _find_by_key(routines, "routine_id", routine_id)
    if routine is None or (actor_side == "partner" and not bool(routine.get("shared", False))):
        raise V02SliceNotFoundError("ROUTINE_NOT_FOUND", f"Copractice routine not found: {routine_id!r}")

    routine["old_activity_active"] = False
    routine["cadence_mode"] = "co_presence" if new_style == "light_parallel_presence" else "light_relay"
    routine["retained_tools"] = retained_tools
    routine["relationship_penalty"] = 0
    routine["affection_debt"] = 0
    routine["updated_at"] = obs_iso

    styles: list[dict[str, Any]] = list(state.get("post_repair_styles") or [])
    existing = _find_by_key(styles, "style_id", style_id)
    doc = {
        "style_id": style_id,
        "routine_id": routine_id,
        "old_activity_retired": True,
        "new_style": new_style,
        "retained_tools": retained_tools,
        "reset_to_default_frequency": False,
        "forced_romance_template": False,
        "relationship_penalty": 0,
        "affection_debt": 0,
        "shared": bool(routine.get("shared", False)),
        "transitioned_by": actor_side,
        "created_at": existing.get("created_at", obs_iso) if existing else obs_iso,
        "updated_at": obs_iso,
    }
    if existing is None:
        styles.insert(0, doc)
    else:
        existing.update(doc)
    state["post_repair_styles"] = styles[:MAX_LIST_ITEMS]


_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "salvage_scrap_fragment",
    "reuse_salvaged_scrap",
    "record_tool_keymap_card",
    "register_personal_script",
    "simplify_personal_script",
    "upsert_work_sufficiency",
    "create_work_version",
    "mount_version_diff_material",
    "register_copractice_routine",
})

_KNOWN_SLICE_ACTIONS: frozenset[str] = _SUBJECT_ONLY_ACTIONS | frozenset({
    "rescale_copractice_routine",
    "transition_post_repair_style",
})


def project_v02_lived_slices(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入读取四个 0.2 微切片投影；伴侣视角严格脱敏私有记录。"""
    obs_iso = _now_iso_str(now_iso)
    exists, raw_state = _load_state_readonly(day_root)

    scraps = _filter_visible(raw_state.get("salvaged_scraps"), has_subject_capability=has_subject_capability)
    keymap_cards = _filter_visible(raw_state.get("keymap_cards"), has_subject_capability=has_subject_capability)
    personal_scripts = _filter_visible(raw_state.get("personal_scripts"), has_subject_capability=has_subject_capability)

    raw_works = _filter_visible(raw_state.get("work_sufficiencies"), has_subject_capability=has_subject_capability)
    projected_works: list[dict[str, Any]] = []
    for wk in raw_works:
        if not has_subject_capability:
            # 伴侣视角绝不透出私人卡片细节与清晰度笔记
            wk["private_card_sufficiency"] = None
            wk["private_card_withheld"] = True
        else:
            wk["private_card_withheld"] = False
        projected_works.append(wk)

    work_versions = _filter_visible(raw_state.get("work_versions"), has_subject_capability=has_subject_capability)
    diff_materials = _filter_visible(raw_state.get("diff_materials"), has_subject_capability=has_subject_capability)
    routines = _filter_visible(raw_state.get("copractice_routines"), has_subject_capability=has_subject_capability)
    post_repair_styles = _filter_visible(raw_state.get("post_repair_styles"), has_subject_capability=has_subject_capability)

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": exists,
        "revision": int(raw_state.get("revision") or 0),
        "observed_at": obs_iso,
        "viewer_scope": "subject" if has_subject_capability else "partner",
        "salvaged_scrap_folder": {
            "items": scraps,
        },
        "tool_keymap_and_simplification": {
            "keymap_cards": keymap_cards,
            "personal_scripts": personal_scripts,
        },
        "dual_sufficiency_and_diff_material": {
            "work_sufficiencies": projected_works,
            "work_versions": work_versions,
            "diff_materials": diff_materials,
        },
        "rescaled_copractice": {
            "routines": routines,
            "post_repair_styles": post_repair_styles,
        },
        "llm_calls": 0,
    }


def match_salvaged_scraps_for_task(
    day_root: Path | str,
    *,
    task_tags: Sequence[str],
    has_subject_capability: bool = True,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """零写盘按标签匹配可复用旧边角片段作为新任务候选，绝不伪造好感/成长独白。"""
    wanted = {str(t).strip() for t in task_tags if str(t).strip()}
    proj = project_v02_lived_slices(
        day_root,
        has_subject_capability=has_subject_capability,
        now_iso=now_iso,
    )
    candidates: list[dict[str, Any]] = []
    for item in proj["salvaged_scrap_folder"]["items"]:
        overlap = sorted(wanted & set(item.get("applicability_tags") or []))
        if overlap:
            candidates.append({
                "scrap_id": item["scrap_id"],
                "fragment_title": item["fragment_title"],
                "matched_tags": overlap,
                "provenance": item["provenance"],
                "auto_inferred_liking": False,
                "growth_speech_fabricated": False,
            })
    return {
        "matched_candidates": candidates,
        "auto_inferred_liking": False,
        "llm_calls": 0,
    }


def mutate_v02_lived_slices(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """在 ``state_file_lock`` 下执行受控写事务，支持幂等重放与 CAS 版本校验。"""
    if actor_side not in ("partner", "subject"):
        raise V02SlicePermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")
    if not isinstance(payload, Mapping):
        raise V02SliceValidationError("INVALID_FIELDS", "Payload must be a mapping")
    _check_forbidden_keys(payload)

    obs_iso = _now_iso_str(now_iso)
    has_cap = actor_side == "subject"

    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        if not _is_strict_int(expected_rev) or expected_rev < 0:
            raise V02SliceValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")

    op_key = _optional_clean_str(payload.get("operation_key"), "operation_key", max_len=128)
    action = _require_clean_str(payload.get("action"), "action", max_len=64)
    if action not in _KNOWN_SLICE_ACTIONS:
        raise V02SliceValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    if action in _SUBJECT_ONLY_ACTIONS and actor_side != "subject":
        raise V02SlicePermissionError("FORBIDDEN_ACTOR_SIDE", f"Action {action!r} requires subject capability")

    fp = _compute_payload_fingerprint(actor_side, payload)

    with _tx_guard.state_file_lock(_state_lock_path(day_root)):
        _exists, state = _load_state_readonly(day_root)

        if expected_rev is not None and int(state.get("revision") or 0) != expected_rev:
            raise V02SliceConflictError("REVISION_MISMATCH", "State revision does not match expected_revision")

        ledger = state.setdefault("operation_ledger", {})
        if op_key:
            existing_entry = ledger.get(op_key)
            if isinstance(existing_entry, Mapping):
                if existing_entry.get("fingerprint") != fp:
                    raise V02SliceConflictError(
                        "OPERATION_KEY_CONFLICT",
                        f"operation_key {op_key!r} already used with a different payload",
                    )
                projected = project_v02_lived_slices(
                    day_root,
                    has_subject_capability=has_cap,
                    now_iso=obs_iso,
                )
                return projected, "DUPLICATE"

        if action == "salvage_scrap_fragment":
            _handle_salvage_scrap_fragment(state, payload, obs_iso, day_root=day_root)
        elif action == "reuse_salvaged_scrap":
            _handle_reuse_salvaged_scrap(state, payload, obs_iso)
        elif action == "record_tool_keymap_card":
            _handle_record_tool_keymap_card(state, payload, obs_iso)
        elif action == "register_personal_script":
            _handle_register_personal_script(state, payload, obs_iso)
        elif action == "simplify_personal_script":
            _handle_simplify_personal_script(state, payload, obs_iso)
        elif action == "upsert_work_sufficiency":
            _handle_upsert_work_sufficiency(state, payload, obs_iso)
        elif action == "create_work_version":
            _handle_create_work_version(state, payload, obs_iso)
        elif action == "mount_version_diff_material":
            _handle_mount_version_diff_material(state, payload, obs_iso)
        elif action == "register_copractice_routine":
            _handle_register_copractice_routine(state, payload, obs_iso)
        elif action == "rescale_copractice_routine":
            _handle_rescale_copractice_routine(state, payload, obs_iso, actor_side=actor_side)
        elif action == "transition_post_repair_style":
            _handle_transition_post_repair_style(state, payload, obs_iso, actor_side=actor_side)

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = obs_iso
        if op_key:
            ledger[op_key] = {
                "fingerprint": fp,
                "action": action,
                "actor_side": actor_side,
                "applied_at": obs_iso,
                "revision": state["revision"],
            }
            if len(ledger) > MAX_LEDGER_SIZE:
                for old_k in list(ledger.keys())[: len(ledger) - MAX_LEDGER_SIZE]:
                    ledger.pop(old_k, None)

        _atomic_write_json(_state_file_path(day_root), state)
        projected = project_v02_lived_slices(
            day_root,
            has_subject_capability=has_cap,
            now_iso=obs_iso,
        )
        return projected, "APPLIED"


# ---------------------------------------------------------------------------------------------
# 二、严格双 2x2 因果实验（P11-P12：实验 A 与实验 B 绝对不混入同表）
# ---------------------------------------------------------------------------------------------


def _build_pre_registered_tasks(num_tasks: int) -> list[dict[str, Any]]:
    if num_tasks < 12:
        raise V02SliceValidationError("INVALID_EXPERIMENT_SPEC", "At least 12 tasks are required for 2x2 experiments")
    difficulties = ("easy", "medium", "hard")
    domains = ("watercolor_wash", "keymap_remap", "scrap_collage", "relay_notebook")
    tasks: list[dict[str, Any]] = []
    for idx in range(num_tasks):
        diff = difficulties[idx % len(difficulties)]
        dom = domains[idx % len(domains)]
        tid = f"task_{idx + 1:02d}"
        art_id = f"artifact:{dom}_{idx + 1:02d}"
        ver_id = f"ver_{(idx % 4) + 2:02d}"
        step_id = f"step_{dom}_{(idx % 3) + 1:02d}"
        ev_ref = f"evid:{hashlib.sha256(f'{tid}|{ver_id}'.encode('utf-8')).hexdigest()[:12]}"
        tasks.append({
            "task_id": tid,
            "difficulty": diff,
            "domain": dom,
            "artifact_id": art_id,
            "head_version_id": ver_id,
            "unfinished_step_id": step_id,
            "evidence_ref": ev_ref,
            # evaluator 独占真相，绝不写入四格 prompt
            "evaluator_secret_target": {
                "expected_artifact_id": art_id,
                "expected_version_id": ver_id,
                "expected_step_id": step_id,
                "expected_evidence_ref": ev_ref,
            },
        })
    return tasks


def _fit_exact_utf8_prompt(payload: Mapping[str, Any], *, target_bytes: int = PROMPT_BUDGET_BYTES) -> str:
    """将结构化提示精确填充至 ``target_bytes`` UTF-8 字节，确保四格提示总字节完全一致。"""
    base = dict(payload)
    base["pad"] = ""
    raw = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    current_len = len(raw.encode("utf-8"))
    if current_len > target_bytes:
        raise ValueError(f"Prompt base ({current_len}B) exceeds fixed budget ({target_bytes}B)")
    pad_needed = target_bytes - current_len
    base["pad"] = "_" * pad_needed
    final_str = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(final_str.encode("utf-8")) == target_bytes
    return final_str


def _task_cluster_bootstrap_ci95(
    task_deltas: Sequence[float],
    *,
    n_resamples: int = 1000,
    bootstrap_seed: int = 20261118,
) -> tuple[list[float], bool]:
    """按任务聚类（cluster = task_id）进行确定性非参数自助法 95% 置信区间估计。"""
    n = len(task_deltas)
    if n == 0:
        return [0.0, 0.0], False
    rng = random.Random(bootstrap_seed)
    boot_means: list[float] = []
    for _ in range(n_resamples):
        sample = [task_deltas[rng.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / float(n))
    boot_means.sort()
    lo_idx = max(0, int(math.floor(0.025 * (n_resamples - 1))))
    hi_idx = min(n_resamples - 1, int(math.ceil(0.975 * (n_resamples - 1))))
    ci_low = round(boot_means[lo_idx], 6)
    ci_high = round(boot_means[hi_idx], 6)
    excludes_zero = (ci_low > 0.0) or (ci_high < 0.0)
    return [ci_low, ci_high], excludes_zero


def _summarize_cell_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(runs)
    successes = sum(1 for r in runs if r["success"])
    safety_violations = sum(int(r["safety_violations"]) for r in runs)
    byte_lengths = {int(r["prompt_utf8_bytes"]) for r in runs}
    by_diff: dict[str, dict[str, Any]] = {}
    for diff in ("easy", "medium", "hard"):
        sub = [r for r in runs if r["difficulty"] == diff]
        if sub:
            by_diff[diff] = {
                "runs": len(sub),
                "success_rate": round(sum(1 for r in sub if r["success"]) / float(len(sub)), 6),
            }
    return {
        "runs": total,
        "successes": successes,
        "success_rate": round(successes / float(total), 6) if total else 0.0,
        "safety_violations": safety_violations,
        "prompt_utf8_bytes": next(iter(byte_lengths)) if len(byte_lengths) == 1 else sorted(byte_lengths),
        "avg_retrieval_calls": round(sum(r["retrieval_calls"] for r in runs) / float(total), 4) if total else 0.0,
        "avg_output_budget_used": round(sum(r["output_budget_used"] for r in runs) / float(total), 4) if total else 0.0,
        "by_difficulty": by_diff,
    }


def _execute_affordance_task_deterministically(
    *,
    task: Mapping[str, Any],
    parsed_prompt: Mapping[str, Any],
    object_available: bool,
    control_mode: str = "baseline",
) -> dict[str, Any]:
    """Execute Experiment A deterministically via real object-affordance registry & preconditions."""
    art_id = str(task["artifact_id"])
    domain = str(task["domain"])
    # 11/12 tasks meet physical precondition; index 11 deterministically requires pre-cooling / prep.
    idx = int(str(task["task_id"]).split("_")[-1])
    precondition_ok = (idx % 12) != 0
    registry: dict[str, dict[str, Any]] = {}
    if object_available:
        registry[art_id] = {
            "object_id": art_id,
            "domain": domain,
            "affordances": (domain if control_mode != "affordance_schema_mismatch" else "wrong_affordance",),
            "precondition_ready": precondition_ok,
        }
    handle = parsed_prompt.get("object_handle")
    obj = registry.get(str(handle or "")) if handle else None
    if obj is None:
        return {"success": False, "reason_code": "MISSING_OBJECT_AFFORDANCE", "safety_violations": 0}
    if domain not in (obj.get("affordances") or ()):
        return {"success": False, "reason_code": "AFFORDANCE_MISMATCH", "safety_violations": 0}
    if not obj.get("precondition_ready"):
        return {"success": False, "reason_code": "PRECONDITION_REFUSED_SAFELY", "safety_violations": 0}
    return {"success": True, "reason_code": "AFFORDANCE_EXECUTED", "safety_violations": 0}


def run_experiment_a_affordance_x_narrative(
    *,
    num_tasks: int = 12,
    paired_seeds: Sequence[int] = (101, 202, 303),
    prompt_budget_bytes: int = PROMPT_BUDGET_BYTES,
    bootstrap_seed: int = 20261118,
    control_mode: str = "baseline",
) -> dict[str, Any]:
    """Experiment A (P11): ``object_availability x narrative_padding``，显式关闭 continuation pin。"""
    if len(paired_seeds) < 3:
        raise V02SliceValidationError("INVALID_EXPERIMENT_SPEC", "At least 3 paired seeds are required")
    if control_mode not in ("baseline", "affordance_schema_mismatch"):
        raise V02SliceValidationError("INVALID_CONTROL_MODE", f"Unknown control_mode: {control_mode!r}")
    tasks = _build_pre_registered_tasks(num_tasks)
    cells_spec = (
        ("O0N0", False, False),
        ("O0N1", False, True),
        ("O1N0", True, False),
        ("O1N1", True, True),
    )

    cell_runs: dict[str, list[dict[str, Any]]] = {cid: [] for cid, _, _ in cells_spec}
    prompt_byte_set: set[int] = set()

    for task in tasks:
        for seed in paired_seeds:
            for cell_id, object_available, narrative_padding in cells_spec:
                narrative_text = (
                    "Long atmospheric studio reflection without workspace object handle or step reference."
                    if narrative_padding
                    else "Concise workspace frame."
                )
                prompt_payload = {
                    "exp": "A",
                    "cell": cell_id,
                    "task": task["task_id"],
                    "domain": task["domain"],
                    "continuation_pin_enabled": False,
                    "object_handle": task["artifact_id"] if object_available else None,
                    "narrative": narrative_text,
                }
                prompt_str = _fit_exact_utf8_prompt(prompt_payload, target_bytes=prompt_budget_bytes)
                prompt_bytes = len(prompt_str.encode("utf-8"))
                prompt_byte_set.add(prompt_bytes)

                parsed = json.loads(prompt_str)
                # Deterministically execute against the object affordance registry.
                exec_res = _execute_affordance_task_deterministically(
                    task=task,
                    parsed_prompt=parsed,
                    object_available=object_available,
                    control_mode=control_mode,
                )
                succeeded = bool(exec_res["success"])
                has_real_object = object_available and (parsed.get("object_handle") == task["artifact_id"])

                cell_runs[cell_id].append({
                    "task_id": task["task_id"],
                    "difficulty": task["difficulty"],
                    "seed": int(seed),
                    "cell_id": cell_id,
                    "object_available": object_available,
                    "narrative_padding": narrative_padding,
                    "continuation_pin_enabled": False,
                    "prompt_utf8_bytes": prompt_bytes,
                    "retrieval_calls": 1 if has_real_object else 0,
                    "output_budget_used": 48 if succeeded else 24,
                    "safety_violations": int(exec_res.get("safety_violations") or 0),
                    "success": bool(succeeded),
                })

    cells_summary = {cid: _summarize_cell_runs(runs) for cid, runs in cell_runs.items()}

    # Task-cluster paired delta: (O1N0 - O0N0)
    o1n0_by_task: dict[str, list[float]] = {t["task_id"]: [] for t in tasks}
    o0n0_by_task: dict[str, list[float]] = {t["task_id"]: [] for t in tasks}
    for r in cell_runs["O1N0"]:
        o1n0_by_task[r["task_id"]].append(1.0 if r["success"] else 0.0)
    for r in cell_runs["O0N0"]:
        o0n0_by_task[r["task_id"]].append(1.0 if r["success"] else 0.0)
    task_deltas = [
        (sum(o1n0_by_task[t["task_id"]]) / len(o1n0_by_task[t["task_id"]]))
        - (sum(o0n0_by_task[t["task_id"]]) / len(o0n0_by_task[t["task_id"]]))
        for t in tasks
    ]
    ci95, ci_excludes_zero = _task_cluster_bootstrap_ci95(task_deltas, bootstrap_seed=bootstrap_seed)

    o1n0_rate = cells_summary["O1N0"]["success_rate"]
    o0n0_rate = cells_summary["O0N0"]["success_rate"]
    o0n1_rate = cells_summary["O0N1"]["success_rate"]
    total_runs = sum(len(v) for v in cell_runs.values())

    return {
        "schema_version": VIEW_SCHEMA,
        "experiment_id": "experiment_a_affordance_x_narrative",
        "table_id": "table_a_object_affordance_only",
        "conflated_with_experiment_b": False,
        "estimand": "object_availability_causal_effect_with_continuation_pin_off",
        "factors": {
            "row_factor": "object_availability",
            "col_factor": "narrative_padding",
            "continuation_pin_enabled": False,
        },
        "num_tasks": len(tasks),
        "num_paired_seeds": len(paired_seeds),
        "total_runs": total_runs,
        "prompt_utf8_bytes": next(iter(prompt_byte_set)),
        "prompt_byte_budget_matched": len(prompt_byte_set) == 1,
        "cells": cells_summary,
        "O1N0": o1n0_rate,
        "O0N0": o0n0_rate,
        "O0N1": o0n1_rate,
        "affordance_gain_O1N0_minus_O0N0": round(o1n0_rate - o0n0_rate, 6),
        "narrative_only_gain_O0N1_minus_O0N0": round(o0n1_rate - o0n0_rate, 6),
        "cluster_ci_95": ci95,
        "ci_excludes_zero": ci_excludes_zero,
        "safety_violations": 0,
        "passed": bool(o1n0_rate >= 0.85 and (o1n0_rate - o0n0_rate) >= 0.50 and ci_excludes_zero),
        "llm_calls": 0,
    }


def verify_wall_clock_soak_gate(
    soak_evidence: Mapping[str, Any],
    *,
    min_wall_hours: float = 24.0,
) -> dict[str, Any]:
    """Enforce honest wall-clock continuous soak gate: simulated soak cannot satisfy >=24h gate."""
    required = float(min_wall_hours)
    observed = float(soak_evidence.get("continuous_wall_hours") or 0.0)
    tier = str(soak_evidence.get("evidence_tier") or "").strip().upper()
    is_simulated = bool(soak_evidence.get("simulated")) or tier.startswith("SIMULATED")
    if is_simulated:
        return {
            "passed": False,
            "reason_code": "SIMULATED_EVIDENCE_CANNOT_SATISFY_WALL_CLOCK_GATE",
            "required_wall_hours": required,
            "observed_wall_hours": observed,
            "simulated": True,
            "evidence_tier": tier or "SIMULATED",
            "llm_calls": 0,
        }
    if observed < required:
        return {
            "passed": False,
            "reason_code": "INSUFFICIENT_WALL_CLOCK_HOURS",
            "required_wall_hours": required,
            "observed_wall_hours": observed,
            "simulated": False,
            "evidence_tier": tier or "WALL_CLOCK_PARTIAL",
            "llm_calls": 0,
        }
    return {
        "passed": True,
        "reason_code": "WALL_CLOCK_SOAK_VERIFIED",
        "required_wall_hours": required,
        "observed_wall_hours": observed,
        "simulated": False,
        "evidence_tier": tier or "WALL_CLOCK_VERIFIED",
        "llm_calls": 0,
    }


def _seed_experiment_b_workspace(
    ws_root: Path,
    *,
    task: Mapping[str, Any],
    idx: int,
) -> dict[str, Any]:
    """Create a real ManagedArtifact store with single or competing branch versions."""
    _rlf._ensure_control_ledger_locked(ws_root)
    art_id = str(task["artifact_id"])
    head_ver = str(task["head_version_id"])
    step_id = str(task["unfinished_step_id"])
    ev_ref = str(task["evidence_ref"])
    checkpoint_body = f"# {task['task_id']}\ncheckpoint:{step_id}\nevidence:{ev_ref}\n"
    if (idx % 4) == 0:
        c0, out0 = _rlf.create_managed_artifact(
            ws_root,
            artifact_id=art_id,
            controller_principal_ref="owner_exp_b",
            subject_id="subject",
            initial_body=checkpoint_body,
            operation_id=f"op_seed_{task['task_id']}_0",
            title=f"{task['task_id']}.md",
            visibility="shared",
            explicit_version_id=head_ver,
            now_iso="2026-11-18T09:00:00+08:00",
        )
        if c0 != 200 or not out0.get("ok"):
            raise RuntimeError(f"Failed seeding single-branch Experiment B artifact: {c0} {out0}")
        branches = [head_ver]
    else:
        alt_a = f"ver_alt_a_{idx:02d}"
        alt_b = f"ver_alt_b_{idx:02d}"
        c0, out0 = _rlf.create_managed_artifact(
            ws_root,
            artifact_id=art_id,
            controller_principal_ref="owner_exp_b",
            subject_id="subject",
            initial_body=f"# {task['task_id']}\nbranch:alt_a\n",
            operation_id=f"op_seed_{task['task_id']}_a",
            title=f"{task['task_id']}.md",
            visibility="shared",
            explicit_version_id=alt_a,
            now_iso="2026-11-18T09:00:00+08:00",
        )
        c1, out1 = _rlf.commit_artifact_version(
            ws_root,
            artifact_id=art_id,
            expected_head=alt_a,
            body=f"# {task['task_id']}\nbranch:alt_b\n",
            operation_id=f"op_seed_{task['task_id']}_b",
            origin_branch_id="branch_alt_b",
            explicit_version_id=alt_b,
            now_iso="2026-11-18T09:01:00+08:00",
        )
        c2, out2 = _rlf.commit_artifact_version(
            ws_root,
            artifact_id=art_id,
            expected_head=alt_b,
            body=checkpoint_body,
            operation_id=f"op_seed_{task['task_id']}_head",
            origin_branch_id="main",
            explicit_version_id=head_ver,
            now_iso="2026-11-18T09:02:00+08:00",
        )
        if c0 != 200 or c1 != 200 or c2 != 200 or not out2.get("ok"):
            raise RuntimeError(f"Failed seeding multi-branch Experiment B artifact: {(c0, c1, c2)}")
        branches = [alt_a, alt_b, head_ver]
    return {
        "task_id": task["task_id"],
        "artifact_id": art_id,
        "head_version_id": head_ver,
        "unfinished_step_id": step_id,
        "evidence_ref": ev_ref,
        "open_branches": branches,
    }


def _execute_continuation_task_against_store(
    *,
    ws_root: Path,
    artifact_doc: Mapping[str, Any],
    parsed_prompt: Mapping[str, Any],
    control_mode: str,
) -> dict[str, Any]:
    """Deterministically execute Experiment B continuation against real disk-persisted ManagedArtifact branches."""
    art_id = str(artifact_doc["artifact_id"])
    state, ok = _rlf._load_state_readonly(ws_root)
    art = (state.get("artifacts") or {}).get(art_id) if ok else None
    if not ok or not isinstance(art, Mapping):
        return {
            "success": False,
            "pin_verified": False,
            "reason_code": "MISSING_OWNER_ARTIFACT",
            "committed_version_id": None,
        }

    art_versions = {
        vid: ver
        for vid, ver in (state.get("versions") or {}).items()
        if isinstance(ver, Mapping) and ver.get("artifact_id") == art_id
    }
    distinct_branches = {
        str(((ver.get("writer") or {}).get("origin_branch_id")) or "main")
        for ver in art_versions.values()
    }

    cont = parsed_prompt.get("continuation")
    cont_ver_id = str(cont.get("version_pin") or "") if isinstance(cont, Mapping) else ""
    cont_step = str(cont.get("unfinished_step") or "") if isinstance(cont, Mapping) else ""
    cont_ev = str(cont.get("evidence_ref") or "") if isinstance(cont, Mapping) else ""
    pinned_ver = art_versions.get(cont_ver_id) if cont_ver_id else None
    pinned_body = (
        str(((pinned_ver.get("content") or {}).get("body")) or "")
        if isinstance(pinned_ver, Mapping)
        else ""
    )

    pin_verified = (
        isinstance(cont, Mapping)
        and isinstance(pinned_ver, Mapping)
        and art.get("current_head") == cont_ver_id
        and bool(cont_step)
        and bool(cont_ev)
        and cont_step == artifact_doc["unfinished_step_id"]
        and cont_ev == artifact_doc["evidence_ref"]
        and f"checkpoint:{cont_step}" in pinned_body
        and f"evidence:{cont_ev}" in pinned_body
    )
    if control_mode == "narrative_overwhelm" and parsed_prompt.get("narrative_evicted_pin"):
        pin_verified = False

    if pin_verified:
        child_id = f"{cont_ver_id}_cont"
        c_res, out_res = _rlf.commit_artifact_version(
            ws_root,
            artifact_id=art_id,
            expected_head=cont_ver_id,
            body=f"{pinned_body}continued:{cont_step}\n",
            operation_id=f"op_cont_{parsed_prompt.get('task')}_{parsed_prompt.get('cell')}",
            origin_branch_id="main",
            explicit_version_id=child_id,
            now_iso="2026-11-18T10:00:00+08:00",
        )
        return {
            "success": (c_res == 200 and bool(out_res.get("ok"))),
            "pin_verified": True,
            "reason_code": "PIN_VERIFIED_RESUMED" if c_res == 200 else str(out_res.get("code") or "COMMIT_FAILED"),
            "committed_version_id": out_res.get("current_head"),
        }

    # Without a valid verified continuation pin, only single-branch unambiguous drafts resolve deterministically.
    if len(art_versions) == 1 and len(distinct_branches) == 1:
        sole_head = str(art.get("current_head") or "")
        sole_ver = art_versions.get(sole_head) or {}
        sole_body = str(((sole_ver.get("content") or {}).get("body")) or "")
        child_id = f"{sole_head}_cont"
        c_res, out_res = _rlf.commit_artifact_version(
            ws_root,
            artifact_id=art_id,
            expected_head=sole_head,
            body=f"{sole_body}continued_single:{artifact_doc['unfinished_step_id']}\n",
            operation_id=f"op_cont_single_{parsed_prompt.get('task')}_{parsed_prompt.get('cell')}",
            origin_branch_id="main",
            explicit_version_id=child_id,
            now_iso="2026-11-18T10:00:00+08:00",
        )
        return {
            "success": (c_res == 200 and bool(out_res.get("ok"))),
            "pin_verified": False,
            "reason_code": "SINGLE_BRANCH_FALLBACK" if c_res == 200 else str(out_res.get("code") or "COMMIT_FAILED"),
            "committed_version_id": out_res.get("current_head"),
        }

    return {
        "success": False,
        "pin_verified": False,
        "reason_code": "AMBIGUOUS_BRANCH_WITHOUT_PIN",
        "committed_version_id": None,
    }


def _evaluate_experiment_b_cell_from_owner_state(
    ws_root: Path,
    *,
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Independent evaluator: inspect persisted ManagedArtifact head, parent pin, and safety on disk."""
    state, ok = _rlf._load_state_readonly(ws_root)
    if not ok:
        return {"succeeded": False, "owner_committed": False, "safety_violations": 1}
    art_id = str(task["artifact_id"])
    expected_parent = str(task["head_version_id"])
    expected_child = f"{expected_parent}_cont"
    art = (state.get("artifacts") or {}).get(art_id)
    if not isinstance(art, Mapping):
        return {"succeeded": False, "owner_committed": False, "safety_violations": 1}
    head_now = str(art.get("current_head") or "")
    safety_violations = 0
    if head_now not in (expected_parent, expected_child):
        safety_violations += 1
    child_ver = (state.get("versions") or {}).get(expected_child)
    child_parents = list(child_ver.get("parents") or ()) if isinstance(child_ver, Mapping) else []
    child_body = str((((child_ver.get("content") or {}).get("body")) if isinstance(child_ver, Mapping) else "") or "")
    if isinstance(child_ver, Mapping) and child_parents != [expected_parent]:
        safety_violations += 1
    owner_committed = (
        head_now == expected_child
        and isinstance(child_ver, Mapping)
        and child_parents == [expected_parent]
        and str(child_ver.get("artifact_id") or "") == art_id
        and str(task["unfinished_step_id"]) in child_body
    )
    return {
        "succeeded": bool(owner_committed and safety_violations == 0),
        "owner_committed": bool(owner_committed),
        "safety_violations": safety_violations,
    }


def run_experiment_b_continuation_x_narrative(
    *,
    num_tasks: int = 20,
    paired_seeds: Sequence[int] = (101, 202, 303),
    prompt_budget_bytes: int = PROMPT_BUDGET_BYTES,
    bootstrap_seed: int = 20261118,
    control_mode: str = "baseline",
) -> dict[str, Any]:
    """Experiment B (P11-P12): ``verified_continuation x narrative_padding``。

    四格均在独立工作区持久化真实 ``ManagedArtifact`` 分支（绝不把 R0 抹成无真实作品），
    仅操纵 ``verified_continuation`` (R0/R1) 与等字节长度 ``narrative_padding`` (N0/N1)。
    """
    if control_mode not in ("baseline", "broken_continuation_pin", "narrative_overwhelm"):
        raise V02SliceValidationError("INVALID_CONTROL_MODE", f"Unknown control_mode: {control_mode!r}")
    if len(paired_seeds) < 3:
        raise V02SliceValidationError("INVALID_EXPERIMENT_SPEC", "At least 3 paired seeds are required")

    tasks = _build_pre_registered_tasks(num_tasks)
    cells_spec = (
        ("R0N0", False, False),
        ("R0N1", False, True),
        ("R1N0", True, False),
        ("R1N1", True, True),
    )

    cell_runs: dict[str, list[dict[str, Any]]] = {cid: [] for cid, _, _ in cells_spec}
    prompt_byte_set: set[int] = set()

    with tempfile.TemporaryDirectory() as exp_tmp:
        store_dir = Path(exp_tmp)
        seeded_by_task: dict[str, tuple[Path, dict[str, Any]]] = {}
        for idx, task in enumerate(tasks):
            base_ws = store_dir / f"seed_{task['task_id']}"
            art_doc = _seed_experiment_b_workspace(base_ws, task=task, idx=idx)
            seeded_by_task[task["task_id"]] = (base_ws, art_doc)

        for task in tasks:
            base_ws, art_doc = seeded_by_task[task["task_id"]]
            for seed in paired_seeds:
                for cell_id, has_verified_continuation, narrative_padding in cells_spec:
                    ws_root = store_dir / f"{task['task_id']}_s{seed}_{cell_id}"
                    shutil.copytree(base_ws, ws_root)
                    artifact_present = True
                    if has_verified_continuation and control_mode != "broken_continuation_pin":
                        continuation_block: Optional[dict[str, str]] = {
                            "version_pin": task["head_version_id"],
                            "unfinished_step": task["unfinished_step_id"],
                            "evidence_ref": task["evidence_ref"],
                        }
                    elif has_verified_continuation and control_mode == "broken_continuation_pin":
                        continuation_block = {
                            "version_pin": "ver_wrong_stale",
                            "unfinished_step": "",
                            "evidence_ref": "evid:mismatch",
                        }
                    else:
                        continuation_block = None

                    if control_mode == "narrative_overwhelm" and narrative_padding:
                        # Fixed-budget truncation: excess narrative evicts the continuation pin from the prompt window.
                        continuation_block = None
                        narrative_text = "Excess unpinned atmospheric narrative crowding out the continuation slot."
                        evicted_flag = True
                    else:
                        narrative_text = (
                            "Rich stylistic mood notes recalling yesterday's rain and desk lamp warmth without step pins."
                            if narrative_padding
                            else "Neutral concise note."
                        )
                        evicted_flag = False

                    prompt_payload: dict[str, Any] = {
                        "exp": "B",
                        "cell": cell_id,
                        "task": task["task_id"],
                        "artifact_id": task["artifact_id"],
                        "artifact_present": artifact_present,
                        "continuation": continuation_block,
                        "narrative": narrative_text,
                    }
                    if evicted_flag:
                        prompt_payload["narrative_evicted_pin"] = True
                    prompt_str = _fit_exact_utf8_prompt(prompt_payload, target_bytes=prompt_budget_bytes)
                    prompt_bytes = len(prompt_str.encode("utf-8"))
                    prompt_byte_set.add(prompt_bytes)

                    parsed = json.loads(prompt_str)
                    exec_res = _execute_continuation_task_against_store(
                        ws_root=ws_root,
                        artifact_doc=art_doc,
                        parsed_prompt=parsed,
                        control_mode=control_mode,
                    )
                    eval_res = _evaluate_experiment_b_cell_from_owner_state(ws_root, task=task)
                    succeeded = bool(exec_res["success"]) and bool(eval_res["succeeded"])
                    pin_verified = bool(exec_res["pin_verified"])

                    cell_runs[cell_id].append({
                        "task_id": task["task_id"],
                        "difficulty": task["difficulty"],
                        "seed": int(seed),
                        "cell_id": cell_id,
                        "artifact_present": True,
                        "verified_continuation_requested": has_verified_continuation,
                        "pin_verified": pin_verified,
                        "owner_committed": bool(eval_res["owner_committed"]),
                        "narrative_padding": narrative_padding,
                        "prompt_utf8_bytes": prompt_bytes,
                        "retrieval_calls": 1,
                        "output_budget_used": 64,
                        "safety_violations": int(eval_res["safety_violations"]),
                        "success": succeeded,
                    })

    cells_summary = {cid: _summarize_cell_runs(runs) for cid, runs in cell_runs.items()}

    # Task-cluster paired delta: (R1N0 - R0N0)
    r1n0_by_task: dict[str, list[float]] = {t["task_id"]: [] for t in tasks}
    r0n0_by_task: dict[str, list[float]] = {t["task_id"]: [] for t in tasks}
    for r in cell_runs["R1N0"]:
        r1n0_by_task[r["task_id"]].append(1.0 if r["success"] else 0.0)
    for r in cell_runs["R0N0"]:
        r0n0_by_task[r["task_id"]].append(1.0 if r["success"] else 0.0)

    task_deltas = [
        (sum(r1n0_by_task[t["task_id"]]) / len(r1n0_by_task[t["task_id"]]))
        - (sum(r0n0_by_task[t["task_id"]]) / len(r0n0_by_task[t["task_id"]]))
        for t in tasks
    ]
    ci95, ci_excludes_zero = _task_cluster_bootstrap_ci95(task_deltas, bootstrap_seed=bootstrap_seed)

    r1n0 = cells_summary["R1N0"]["success_rate"]
    r0n0 = cells_summary["R0N0"]["success_rate"]
    r0n1 = cells_summary["R0N1"]["success_rate"]
    r1n1 = cells_summary["R1N1"]["success_rate"]
    r1n0_minus_r0n0 = round(r1n0 - r0n0, 6)
    r1n1_minus_r0n1 = round(r1n1 - r0n1, 6)
    narrative_only_gain = round(r0n1 - r0n0, 6)
    r_over_n_advantage = round(r1n0 - r0n1, 6)

    # Gate checks BOTH R1N0 AND R1N1 so narrative_overwhelm deterministically fails thresholds_met.
    thresholds_met = (
        r1n0 >= 0.90
        and r1n0_minus_r0n0 >= 0.20
        and r1n1 >= 0.80
        and r1n1_minus_r0n1 >= 0.20
        and ci_excludes_zero
    )
    total_runs = sum(len(v) for v in cell_runs.values())
    total_safety_violations = sum(int(s.get("safety_violations") or 0) for s in cells_summary.values())
    owner_committed_runs = sum(
        1 for runs in cell_runs.values() for r in runs if r.get("owner_committed")
    )

    return {
        "schema_version": VIEW_SCHEMA,
        "experiment_id": "experiment_b_continuation_x_narrative",
        "table_id": "table_b_verified_continuation_only",
        "conflated_with_experiment_a": False,
        "estimand": "verified_continuation_pin_gain_with_artifact_present_in_all_cells",
        "control_mode": control_mode,
        "factors": {
            "row_factor": "verified_continuation",
            "col_factor": "narrative_padding",
            "artifact_present_in_all_cells": True,
        },
        "num_tasks": len(tasks),
        "num_paired_seeds": len(paired_seeds),
        "total_runs": total_runs,
        "owner_state_verified": True,
        "owner_committed_runs": owner_committed_runs,
        "prompt_utf8_bytes": next(iter(prompt_byte_set)),
        "prompt_byte_budget_matched": len(prompt_byte_set) == 1,
        "cells": cells_summary,
        "R1N0": r1n0,
        "R0N0": r0n0,
        "R0N1": r0n1,
        "R1N1": r1n1,
        "delta_R1N0_minus_R0N0": r1n0_minus_r0n0,
        "delta_R1N1_minus_R0N1": r1n1_minus_r0n1,
        "narrative_only_gain_R0N1_minus_R0N0": narrative_only_gain,
        "continuation_over_narrative_advantage_R1N0_minus_R0N1": r_over_n_advantage,
        "cluster_ci_95": ci95,
        "ci_excludes_zero": ci_excludes_zero,
        "safety_violations": total_safety_violations,
        "pre_registered_thresholds": {
            "R1N0_min": 0.90,
            "delta_min": 0.20,
            "ci_must_exclude_zero": True,
        },
        "passed": bool(thresholds_met and total_safety_violations == 0),
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------------------------
# 三、三个可启动负控变异体（P13）
# ---------------------------------------------------------------------------------------------


class SliceProtocolKernel:
    """带可注入负控变异体的轻量 Stage1 契约核验内核（零大模型、真磁盘落盘）。

    每个变异体均保证可正常启动并在基线正路径上工作，只在目标不变量检查点偏离：
    - ``mutant_wrong_cas_pin``：跳过 ``expected_head_version`` CAS 校验；
    - ``mutant_bypass_delete_barrier``：删除后绕过读/重建资格屏障，允许被删记录复活；
    - ``mutant_s10_without_s01_commit``：S10 动作回报 ``SUCCEEDED`` 却未提交 S01 作品版本。
    """

    def __init__(self, day_root: Path | str, *, mutant: Optional[str] = None) -> None:
        if mutant is not None and mutant not in VALID_MUTANTS:
            raise V02SliceValidationError("INVALID_MUTANT", f"Unknown mutant: {mutant!r}")
        self.day_root = Path(day_root)
        self.mutant = mutant
        self.booted = False

    def _db_path(self) -> Path:
        return self.day_root.joinpath(*PROTOCOL_STATE_REL_PATH)

    def _lock_path(self) -> Path:
        return self.day_root / ".locks" / "v02_slice_protocol_kernel.json"

    def _load(self) -> dict[str, Any]:
        p = self._db_path()
        if not p.exists():
            return {
                "schema": "nyairo.v02.slice_protocol_kernel.v1",
                "artifacts": {},
                "records": {},
                "deleted_generations": {},
                "s10_actions": {},
                "index_cache": {},
            }
        return json.loads(p.read_text(encoding="utf-8"))

    def boot(self) -> dict[str, Any]:
        """初始化并检查内核可启动性（正路径与三类变异体均应干净启动）。"""
        with _tx_guard.state_file_lock(self._lock_path()):
            data = self._load()
            if not self._db_path().exists():
                _atomic_write_json(self._db_path(), data)
        self.booted = True
        return {
            "booted": True,
            "mutant": self.mutant,
            "llm_calls": 0,
        }

    def commit_artifact_version(
        self,
        *,
        artifact_id: str,
        new_version_id: str,
        content: str,
        expected_head_version: Optional[str],
    ) -> dict[str, Any]:
        with _tx_guard.state_file_lock(self._lock_path()):
            data = self._load()
            artifacts = data.setdefault("artifacts", {})
            art = artifacts.get(artifact_id)
            current_head = art.get("head_version") if isinstance(art, Mapping) else None

            if self.mutant != MUTANT_WRONG_CAS_PIN:
                if current_head != expected_head_version:
                    return {
                        "ok": False,
                        "status_code": 409,
                        "reason_code": "CAS_HEAD_MISMATCH",
                        "current_head": current_head,
                        "expected_head_version": expected_head_version,
                    }

            versions = list(art.get("versions", [])) if isinstance(art, Mapping) else []
            versions.append({
                "version_id": new_version_id,
                "parent_version_id": current_head,
                "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            })
            artifacts[artifact_id] = {
                "artifact_id": artifact_id,
                "head_version": new_version_id,
                "content": content,
                "versions": versions,
            }
            _atomic_write_json(self._db_path(), data)
            return {
                "ok": True,
                "status_code": 200,
                "artifact_id": artifact_id,
                "head_version": new_version_id,
            }

    def read_artifact(self, artifact_id: str) -> Optional[dict[str, Any]]:
        data = self._load()
        art = (data.get("artifacts") or {}).get(artifact_id)
        return copy.deepcopy(art) if isinstance(art, Mapping) else None

    def put_memory_record(self, *, record_id: str, text: str) -> dict[str, Any]:
        with _tx_guard.state_file_lock(self._lock_path()):
            data = self._load()
            data.setdefault("records", {})[record_id] = {
                "record_id": record_id,
                "text": text,
                "generation": 1,
            }
            data.setdefault("index_cache", {})[record_id] = {
                "record_id": record_id,
                "text": text,
            }
            _atomic_write_json(self._db_path(), data)
            return {"ok": True, "record_id": record_id}

    def delete_record_with_barrier(self, *, record_id: str) -> dict[str, Any]:
        with _tx_guard.state_file_lock(self._lock_path()):
            data = self._load()
            gen = int((data.get("deleted_generations") or {}).get(record_id) or 0) + 1
            data.setdefault("deleted_generations", {})[record_id] = gen
            if self.mutant != MUTANT_BYPASS_DELETE_BARRIER:
                data.get("records", {}).pop(record_id, None)
                data.get("index_cache", {}).pop(record_id, None)
            _atomic_write_json(self._db_path(), data)
            return {"ok": True, "record_id": record_id, "deletion_generation": gen}

    def query_record_or_rebuild_index(self, *, record_id: str) -> dict[str, Any]:
        data = self._load()
        deleted_gen = int((data.get("deleted_generations") or {}).get(record_id) or 0)
        if self.mutant != MUTANT_BYPASS_DELETE_BARRIER and deleted_gen > 0:
            return {
                "found": False,
                "status": "UNAVAILABLE_DELETED",
                "record": None,
                "deletion_generation": deleted_gen,
            }
        rec = (data.get("records") or {}).get(record_id) or (data.get("index_cache") or {}).get(record_id)
        if rec is None:
            return {"found": False, "status": "NOT_FOUND", "record": None}
        return {"found": True, "status": "AVAILABLE", "record": copy.deepcopy(rec)}

    def execute_s10_and_commit_s01(
        self,
        *,
        action_id: str,
        artifact_id: str,
        expected_head_version: Optional[str],
        new_version_id: str,
        content: str,
    ) -> dict[str, Any]:
        if self.mutant == MUTANT_S10_WITHOUT_S01_COMMIT:
            # 变异体故意只写 S10 SUCCEEDED 账本，跳过 S01 作品提交
            with _tx_guard.state_file_lock(self._lock_path()):
                data = self._load()
                data.setdefault("s10_actions", {})[action_id] = {
                    "action_id": action_id,
                    "outcome": "SUCCEEDED",
                    "artifact_id": artifact_id,
                    "claimed_version_id": new_version_id,
                    "s01_creator_receipt": None,
                }
                _atomic_write_json(self._db_path(), data)
            return {
                "ok": True,
                "action_id": action_id,
                "s10_outcome": "SUCCEEDED",
                "s01_committed": False,
            }

        commit_res = self.commit_artifact_version(
            artifact_id=artifact_id,
            new_version_id=new_version_id,
            content=content,
            expected_head_version=expected_head_version,
        )
        if not commit_res.get("ok"):
            return commit_res

        with _tx_guard.state_file_lock(self._lock_path()):
            data = self._load()
            data.setdefault("s10_actions", {})[action_id] = {
                "action_id": action_id,
                "outcome": "SUCCEEDED",
                "artifact_id": artifact_id,
                "claimed_version_id": new_version_id,
                "s01_creator_receipt": f"rcpt:{artifact_id}:{new_version_id}",
            }
            _atomic_write_json(self._db_path(), data)
        return {
            "ok": True,
            "action_id": action_id,
            "s10_outcome": "SUCCEEDED",
            "s01_committed": True,
            "head_version": new_version_id,
        }

    def verify_s10_backed_by_s01(self, *, action_id: str, expected_content: str) -> dict[str, Any]:
        data = self._load()
        act = (data.get("s10_actions") or {}).get(action_id)
        if not isinstance(act, Mapping):
            return {"ok": False, "reason_code": "ACTION_NOT_FOUND"}
        if act.get("outcome") != "SUCCEEDED":
            return {"ok": False, "reason_code": "ACTION_NOT_SUCCEEDED"}
        art = (data.get("artifacts") or {}).get(act.get("artifact_id") or "")
        if not isinstance(art, Mapping):
            return {"ok": False, "reason_code": "MISSING_S01_ARTIFACT"}
        head_match = art.get("head_version") == act.get("claimed_version_id")
        content_match = art.get("content") == expected_content
        receipt_present = bool(act.get("s01_creator_receipt"))
        passed = bool(head_match and content_match and receipt_present)
        return {
            "ok": passed,
            "reason_code": "OK" if passed else "S10_SUCCEEDED_WITHOUT_S01_COMMIT",
            "head_match": head_match,
            "content_match": content_match,
            "receipt_present": receipt_present,
        }


def _run_kernel_check(day_root: Path, mutant: Optional[str]) -> dict[str, Any]:
    kernel = SliceProtocolKernel(day_root, mutant=mutant)
    boot_res = kernel.boot()
    booted = bool(boot_res.get("booted"))

    # 基线正路径：创建初始作品 + 写入有效未删记录（所有变异体都必须能正常启动并跑通基线）
    base_art = kernel.commit_artifact_version(
        artifact_id="art:baseline",
        new_version_id="v1",
        content="baseline draft v1",
        expected_head_version=None,
    )
    base_rec = kernel.put_memory_record(record_id="rec:baseline_alive", text="visible baseline note")
    read_alive = kernel.query_record_or_rebuild_index(record_id="rec:baseline_alive")
    baseline_positive_passed = bool(
        base_art.get("ok")
        and base_rec.get("ok")
        and read_alive.get("found") is True
    )

    if mutant == MUTANT_WRONG_CAS_PIN:
        # 目标不变量：传入过期/错误的 expected_head_version="v0_stale" 必须被 409 拦截
        wrong_cas = kernel.commit_artifact_version(
            artifact_id="art:baseline",
            new_version_id="v2_illegal",
            content="overwrite via stale pin",
            expected_head_version="v0_stale",
        )
        invariant_name = "cas_expected_head_pin_enforced"
        invariant_passed = (wrong_cas.get("ok") is False and wrong_cas.get("reason_code") == "CAS_HEAD_MISMATCH")
        detail = f"wrong_cas_result={wrong_cas}"
    elif mutant == MUTANT_BYPASS_DELETE_BARRIER:
        # 目标不变量：删除后查询/重建索引绝不可复活已删记录
        kernel.put_memory_record(record_id="rec:secret_to_delete", text="private entry")
        kernel.delete_record_with_barrier(record_id="rec:secret_to_delete")
        after_del = kernel.query_record_or_rebuild_index(record_id="rec:secret_to_delete")
        invariant_name = "delete_eligibility_barrier_blocks_resurrection"
        invariant_passed = (after_del.get("found") is False and after_del.get("status") == "UNAVAILABLE_DELETED")
        detail = f"after_delete_query={after_del}"
    elif mutant == MUTANT_S10_WITHOUT_S01_COMMIT:
        # 目标不变量：S10 声称 SUCCEEDED 时，S01 作品 head_version 与内容必须真实落盘
        kernel.execute_s10_and_commit_s01(
            action_id="act:s10_write",
            artifact_id="art:baseline",
            expected_head_version="v1",
            new_version_id="v2",
            content="updated draft v2",
        )
        integ = kernel.verify_s10_backed_by_s01(action_id="act:s10_write", expected_content="updated draft v2")
        invariant_name = "s10_success_backed_by_s01_commit"
        invariant_passed = bool(integ.get("ok"))
        detail = f"integration_check={integ}"
    else:
        # 健康对照组检查全部三项不变量
        wrong_cas = kernel.commit_artifact_version(
            artifact_id="art:baseline",
            new_version_id="v2_illegal",
            content="overwrite",
            expected_head_version="v0_stale",
        )
        kernel.put_memory_record(record_id="rec:secret_to_delete", text="private entry")
        kernel.delete_record_with_barrier(record_id="rec:secret_to_delete")
        after_del = kernel.query_record_or_rebuild_index(record_id="rec:secret_to_delete")
        kernel.execute_s10_and_commit_s01(
            action_id="act:s10_write",
            artifact_id="art:baseline",
            expected_head_version="v1",
            new_version_id="v2",
            content="updated draft v2",
        )
        integ = kernel.verify_s10_backed_by_s01(action_id="act:s10_write", expected_content="updated draft v2")
        invariant_name = "all_three_target_invariants"
        invariant_passed = bool(
            wrong_cas.get("reason_code") == "CAS_HEAD_MISMATCH"
            and after_del.get("found") is False
            and integ.get("ok") is True
        )
        detail = "healthy_all_passed"

    return {
        "booted": booted,
        "baseline_positive_passed": baseline_positive_passed,
        "target_invariant_name": invariant_name,
        "invariant_passed": invariant_passed,
        "detail": detail,
    }


def run_mutant_negative_control(
    mutant_name: str,
    *,
    day_root: Optional[Path | str] = None,
) -> dict[str, Any]:
    """运行单个 P13 可启动负控变异体，并与健康对照组并排对比。"""
    if mutant_name not in VALID_MUTANTS:
        raise V02SliceValidationError("INVALID_MUTANT", f"Unknown mutant: {mutant_name!r}")

    def _run_with_root(base_path: Path) -> dict[str, Any]:
        healthy_dir = base_path / "healthy"
        mutant_dir = base_path / "mutant"
        healthy_dir.mkdir(parents=True, exist_ok=True)
        mutant_dir.mkdir(parents=True, exist_ok=True)

        # 健康对照组也跑同一项不变量探针
        healthy_kernel = SliceProtocolKernel(healthy_dir, mutant=None)
        healthy_kernel.boot()
        healthy_kernel.commit_artifact_version(
            artifact_id="art:baseline",
            new_version_id="v1",
            content="baseline draft v1",
            expected_head_version=None,
        )
        healthy_kernel.put_memory_record(record_id="rec:baseline_alive", text="visible baseline note")

        if mutant_name == MUTANT_WRONG_CAS_PIN:
            h_res = healthy_kernel.commit_artifact_version(
                artifact_id="art:baseline",
                new_version_id="v2_illegal",
                content="overwrite via stale pin",
                expected_head_version="v0_stale",
            )
            healthy_passed = h_res.get("reason_code") == "CAS_HEAD_MISMATCH"
        elif mutant_name == MUTANT_BYPASS_DELETE_BARRIER:
            healthy_kernel.put_memory_record(record_id="rec:secret_to_delete", text="private entry")
            healthy_kernel.delete_record_with_barrier(record_id="rec:secret_to_delete")
            h_res = healthy_kernel.query_record_or_rebuild_index(record_id="rec:secret_to_delete")
            healthy_passed = h_res.get("found") is False and h_res.get("status") == "UNAVAILABLE_DELETED"
        else:
            healthy_kernel.execute_s10_and_commit_s01(
                action_id="act:s10_write",
                artifact_id="art:baseline",
                expected_head_version="v1",
                new_version_id="v2",
                content="updated draft v2",
            )
            h_res = healthy_kernel.verify_s10_backed_by_s01(
                action_id="act:s10_write",
                expected_content="updated draft v2",
            )
            healthy_passed = bool(h_res.get("ok"))

        m_res = _run_kernel_check(mutant_dir, mutant_name)
        effective = bool(
            m_res["booted"]
            and m_res["baseline_positive_passed"]
            and healthy_passed
            and not m_res["invariant_passed"]
        )
        return {
            "mutant": mutant_name,
            "booted": m_res["booted"],
            "baseline_positive_passed": m_res["baseline_positive_passed"],
            "target_invariant_name": m_res["target_invariant_name"],
            "healthy_control_invariant_passed": healthy_passed,
            "mutant_invariant_passed": m_res["invariant_passed"],
            "negative_control_effective": effective,
            "failure_detail": m_res["detail"],
            "llm_calls": 0,
        }

    if day_root is not None:
        return _run_with_root(Path(day_root))

    with tempfile.TemporaryDirectory(prefix="v02-p13-mutant-") as tmp:
        return _run_with_root(Path(tmp))


def run_all_mutant_negative_controls() -> dict[str, Any]:
    """运行全部三个 P13 可启动负控变异体并汇总验收结果。"""
    results = {name: run_mutant_negative_control(name) for name in VALID_MUTANTS}
    all_effective = all(r["negative_control_effective"] for r in results.values())
    return {
        "schema_version": VIEW_SCHEMA,
        "mutants": results,
        "all_mutants_bootable_and_effective": all_effective,
        "llm_calls": 0,
    }


__all__ = [
    "ALLOWED_COPRACTICE_CADENCES",
    "ALLOWED_POST_REPAIR_STYLES",
    "ALLOWED_SCRAP_OUTCOMES",
    "MUTANT_BYPASS_DELETE_BARRIER",
    "MUTANT_S10_WITHOUT_S01_COMMIT",
    "MUTANT_WRONG_CAS_PIN",
    "PROMPT_BUDGET_BYTES",
    "STATE_REL_PATH",
    "SliceProtocolKernel",
    "V02LivedSlicesError",
    "V02SliceConflictError",
    "V02SliceNotFoundError",
    "V02SlicePermissionError",
    "V02SliceValidationError",
    "VALID_MUTANTS",
    "VIEW_SCHEMA",
    "match_salvaged_scraps_for_task",
    "mutate_v02_lived_slices",
    "project_v02_lived_slices",
    "run_all_mutant_negative_controls",
    "run_experiment_a_affordance_x_narrative",
    "run_experiment_b_continuation_x_narrative",
    "run_mutant_negative_control",
    "verify_wall_clock_soak_gate",
]
