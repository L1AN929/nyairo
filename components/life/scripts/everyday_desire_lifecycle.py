#!/usr/bin/env python3
"""v0.3 日常小心愿生命周期（turn_only 自然淡出）与 6 关切多层冲突裁决引擎（第 35 章）。

设计对齐
--------
* 第 35 章（欲望的形成、冲突与消退）：
  - 覆盖 6 大关切域（``CONCERN_DOMAINS``）：
    1. ``comfort``（舒适与身体休整，如解渴、添衣、困倦歇息）
    2. ``exploration``（探索与眼前机会，如翻新书、去街角散步）
    3. ``creation``（创作与持续手头作品，受断点/原子性保护）
    4. ``possession``（拥有与消费取舍，受真实预算信封约束）
    5. ``companionship``（交往、陪伴或独处分寸）
    6. ``intimacy``（私人亲密意向，普通/公开渠道严格隐藏）
  - 4 类合法来源（``ORIGIN_KINDS``）：
    ``body_need`` / ``immediate_opportunity`` / ``sustained_interest`` / ``relational_wish``，
    必须附带可核验 ``origin_refs``，严禁从世界全库凭空生成未见之物的欲望。
  - ``persistence`` 双轨：
    * ``turn_only``：一闪而过的念头只留在当轮窗口，换轮（``advance_turn``）、切换场景
      （``leave_scene``）或超时后静默自然淡出，**绝不落永久积压行、零挫败债、零逾期惩罚**；
    * ``retained``：跨回合保留的关切，可随时主动放下（``drop_concern``）或因来源失效退出，
      完成后亦不附送虚假“幸福分”。
  - 分层协调裁决（``arbitrate_six_concerns``）：严禁总效用分排行榜（``total_utility_score_forbidden=True``），
    按「可执行与权限过滤 -> 当前活动中断代价校验 -> 多时间尺度并存/推迟/礼让休息」给出可解释取舍。

铁律：纯 Python 标准库、零 ``random``、零 LLM 调用、``GET`` 路径 100% 零落盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # imported as a package member (tests / phone service)
    from components.life.scripts.state_tx_guard import (  # type: ignore[import-not-found]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )
except ImportError:  # imported as a top-level module (components/life/scripts on sys.path)
    from state_tx_guard import (  # type: ignore[no-redef]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )

try:
    from components.world.scripts import instance_paths as _instance_paths  # type: ignore[import-not-found]
except ImportError:
    try:
        import instance_paths as _instance_paths  # type: ignore[no-redef]
    except ImportError:
        _instance_paths = None  # type: ignore[assignment]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf  # type: ignore[import-not-found]
except ImportError:
    try:
        import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
    except ImportError:
        _rlf = None  # type: ignore[assignment]

CONCERN_ITEM_KIND = "proposed.current_concern.v1"
DESIRE_STATE_SCHEMA = "life.v03.everyday_desire.v1"
DESIRE_VIEW_SCHEMA = "life.v03.everyday_desire_projection.v1"
STATE_SCHEMA_VERSION = 1

CONCERN_DOMAINS = (
    "comfort",
    "exploration",
    "creation",
    "possession",
    "companionship",
    "intimacy",
)

ORIGIN_KINDS = (
    "body_need",
    "immediate_opportunity",
    "sustained_interest",
    "relational_wish",
)

URGENCY_LEVELS = ("optional", "timely", "pressing_body")
PERSISTENCE_MODES = ("turn_only", "retained")
ACTION_COST_SCALES = ("micro_inline", "needs_breakpoint", "macro_outing")
CHANNEL_VISIBILITIES = ("public_or_shared", "ordinary_work", "subject_private", "private_intimate")

DEFAULT_TURN_ONLY_TTL_SECONDS = 900
DEFAULT_RETAINED_STALE_SECONDS = 7 * 86400
DESIRE_FADE_INTENSITY_THRESHOLD = 0.08
MAX_RETAINED_CONCERNS = 16
MAX_EPHEMERAL_CONCERNS = 8
MAX_ACTIVE_DESIRES_PROJECTION = 16
MAX_IDEMPOTENCY_RECORDS = 64

CATEGORY_TO_CONCERN_DOMAIN: dict[str, str] = {
    "appetite_craving": "comfort",
    "cozy_comfort": "comfort",
    "restlessness_outing": "exploration",
    "curiosity_mystery": "exploration",
    "craft_ambition": "creation",
    "mischief_rivalry": "companionship",
    "social_longing": "companionship",
    "intimate_warmth": "intimacy",
}

#: Structured origin-ref prefixes that must be grounded in TA's *observed* world.
OBSERVED_ORIGIN_PREFIXES = ("object:", "material:", "place:")
SUBJECT_UNKNOWN_PARENT = "__subject_unknown__"
MATERIAL_WITHDRAWN = "WITHDRAWN"
MATERIAL_RECEIVED_UNOPENED = "RECEIVED_UNOPENED"
MATERIAL_DISCARDED = "DISCARDED"


class EverydayDesireError(ValueError):
    """Structured HTTP-aligned exception for everyday desire lifecycle."""

    def __init__(self, reason_code: str, message: str = "", http_status: int = 400) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code
        self.http_status = int(http_status)


def _canonical_day_root(day_root: Path | str) -> Path:
    if _instance_paths is not None:
        try:
            return _instance_paths.resolve_instance_paths(day_root).day_root
        except Exception:
            pass
    return Path(day_root)


def everyday_desire_state_path(day_root: Path | str) -> Path:
    return _canonical_day_root(day_root) / "life" / "everyday_desires.json"


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _require_now_dt(now_iso: str | datetime | None) -> datetime:
    if now_iso is None:
        return datetime.now(timezone.utc).astimezone()
    parsed = _parse_iso(now_iso)
    if parsed is None:
        raise EverydayDesireError("INVALID_TIMESTAMP", "Invalid ISO-8601 timestamp.", 400)
    return parsed


def _stable_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


# ---------------------------------------------------------------------------------------------
# Ch.35 §一/§五 来源核验：结构化 origin_ref 必须真的被她「看见过」
# ---------------------------------------------------------------------------------------------


def split_structured_origin_ref(origin_ref: Any) -> tuple[str, str] | None:
    """Return ``(kind, ref_id)`` for a known structured prefix, else ``None``.

    Plain strings (``"今天窗边很亮"``) stay valid for historical callers; only the
    three structured prefixes are grounded in her observed world view.
    """

    text = str(origin_ref or "").strip()
    for prefix in OBSERVED_ORIGIN_PREFIXES:
        if text.startswith(prefix):
            ref_id = text[len(prefix):].strip()
            if ref_id:
                return prefix[:-1], ref_id
            return None
    return None


def _read_json_zero_write(path: Path) -> Any:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _load_matter_space_view_zero_write(day_root: Path) -> dict[str, Any] | None:
    """Read-only matter view, preferring the engine and falling back to the state file."""

    try:  # PYTHONPATH=components/world/scripts (worker / tests)
        import v08_universal_matter as _matter  # type: ignore[import-not-found]
    except ImportError:
        try:
            from components.world.scripts import v08_universal_matter as _matter  # type: ignore[no-redef]
        except ImportError:
            _matter = None  # type: ignore[assignment]
    if _matter is not None:
        try:
            return _matter.read_matter_space_view(day_root, actor_side="subject")
        except Exception:
            return None
    for rel in ("world/v08_matter_space.json", "world/v08_universal_matter.json"):
        doc = _read_json_zero_write(day_root / rel)
        if isinstance(doc, Mapping):
            objects = doc.get("matter_objects") or doc.get("objects")
            rows = (
                list(objects.values())
                if isinstance(objects, Mapping)
                else (objects if isinstance(objects, list) else [])
            )
            return {
                "zones": [],
                "objects": [dict(row) for row in rows if isinstance(row, Mapping)],
            }
    return None


def _verify_object_origin(day_root: Path, object_id: str) -> tuple[bool, str]:
    view = _load_matter_space_view_zero_write(day_root)
    if not isinstance(view, Mapping):
        return False, "object_not_observed_or_missing"
    objects: dict[str, Mapping[str, Any]] = {}
    for row in view.get("objects") or []:
        if isinstance(row, Mapping) and str(row.get("object_id") or ""):
            objects[str(row["object_id"])] = row
    if object_id not in objects:
        return False, "object_not_observed_or_missing"
    obj = objects[object_id]
    if obj.get("consumed") is True or obj.get("discarded") is True:
        return False, "object_not_observed_or_missing"
    parent_id = str(obj.get("parent_id") or "").strip()
    if not parent_id or parent_id == SUBJECT_UNKNOWN_PARENT:
        return False, "object_not_observed_or_missing"
    zone_ids = {
        str(row.get("zone_id") or "")
        for row in view.get("zones") or []
        if isinstance(row, Mapping)
    }
    if parent_id not in zone_ids and parent_id not in objects:
        return False, "object_not_observed_or_missing"
    return True, "object_observed"


def _verify_material_origin(day_root: Path, material_id: str) -> tuple[bool, str]:
    rows: list[Mapping[str, Any]] = []
    for rel in (
        ("phone", "v03_encounters.json"),
        ("life", "v03_epistemic_encounters.json"),
    ):
        doc = _read_json_zero_write(day_root.joinpath(*rel))
        if isinstance(doc, Mapping) and isinstance(doc.get("shared_materials"), list):
            rows = [row for row in doc["shared_materials"] if isinstance(row, Mapping)]
            break
    for row in rows:
        if str(row.get("material_id") or "") != material_id:
            continue
        status = str(row.get("status") or "").strip().upper()
        if status in (MATERIAL_WITHDRAWN, MATERIAL_RECEIVED_UNOPENED, MATERIAL_DISCARDED, ""):
            return False, "material_withdrawn_or_unopened"
        return True, "material_observed"
    return False, "material_withdrawn_or_unopened"


def _verify_place_origin(day_root: Path, place_id: str) -> tuple[bool, str]:
    doc = _read_json_zero_write(day_root / "world" / "v04_neighborhood_state.json")
    places = doc.get("places") if isinstance(doc, Mapping) else None
    if not isinstance(places, Mapping):
        return False, "place_not_visited"
    row = places.get(place_id)
    if not isinstance(row, Mapping):
        return False, "place_not_visited"
    if str(row.get("knowledge_status") or "") != "visited":
        return False, "place_not_visited"
    return True, "place_visited"


def verify_concern_origin_observed_readonly(
    day_root: Path | str, origin_ref: Any
) -> tuple[bool, str]:
    """Ch.35 §一: zero-write check that a structured origin was genuinely observed.

    ``object:<id>`` must exist in her subject view with a real, known spot;
    ``material:<id>`` must have been opened/used (not withdrawn or still unopened);
    ``place:<id>`` must carry ``knowledge_status == "visited"``.  Plain unstructured
    strings return ``(True, "unstructured_ref_allowed")`` so historical callers keep
    working unless they opt into ``require_observed_origin=True``.
    """

    parsed = split_structured_origin_ref(origin_ref)
    if parsed is None:
        return True, "unstructured_ref_allowed"
    kind, ref_id = parsed
    root = _canonical_day_root(day_root)
    if kind == "object":
        return _verify_object_origin(root, ref_id)
    if kind == "material":
        return _verify_material_origin(root, ref_id)
    return _verify_place_origin(root, ref_id)


def _concern_lost_origin(
    day_root: Path | str, concern: Mapping[str, Any]
) -> tuple[bool, str]:
    """True when any structured origin_ref is no longer present (source-loss)."""

    origin_refs = concern.get("origin_refs")
    if not isinstance(origin_refs, list):
        return False, ""
    for ref in origin_refs:
        if split_structured_origin_ref(ref) is None:
            continue
        ok, reason = verify_concern_origin_observed_readonly(day_root, ref)
        if not ok:
            return True, reason
    return False, ""


def default_everyday_desire_state() -> dict[str, Any]:
    return {
        "schema": DESIRE_STATE_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "turn_index": 0,
        "current_scene_id": "study_desk",
        "updated_at": None,
        "turn_only_buffer": [],
        "retained_concerns": [],
        "active_desires": [],
        "naturally_faded_count": 0,
        "satisfied_count": 0,
        "dropped_count": 0,
        "last_arbitration": None,
        "idempotency": {},
        # Submission-order receipt bookkeeping (shared ``state_tx_guard`` contract).
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
    }


def _validate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema") != DESIRE_STATE_SCHEMA:
        raise ValueError("invalid everyday_desire schema")
    rev = raw.get("revision", 0)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise ValueError("invalid revision")
    base = default_everyday_desire_state()
    base["revision"] = rev
    base["turn_index"] = max(0, int(raw.get("turn_index") or 0))
    base["current_scene_id"] = str(raw.get("current_scene_id") or "study_desk")
    base["updated_at"] = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None
    if isinstance(raw.get("turn_only_buffer"), list):
        base["turn_only_buffer"] = [
            copy.deepcopy(dict(x)) for x in raw["turn_only_buffer"] if isinstance(x, Mapping)
        ][-MAX_EPHEMERAL_CONCERNS:]
    if isinstance(raw.get("retained_concerns"), list):
        base["retained_concerns"] = [
            copy.deepcopy(dict(x)) for x in raw["retained_concerns"] if isinstance(x, Mapping)
        ][-MAX_RETAINED_CONCERNS:]
    if isinstance(raw.get("active_desires"), list):
        base["active_desires"] = [
            copy.deepcopy(dict(x)) for x in raw["active_desires"] if isinstance(x, Mapping)
        ][-MAX_ACTIVE_DESIRES_PROJECTION:]
    base["naturally_faded_count"] = max(0, int(raw.get("naturally_faded_count") or 0))
    base["satisfied_count"] = max(0, int(raw.get("satisfied_count") or 0))
    base["dropped_count"] = max(0, int(raw.get("dropped_count") or 0))
    if isinstance(raw.get("last_arbitration"), Mapping):
        base["last_arbitration"] = copy.deepcopy(dict(raw["last_arbitration"]))
    if isinstance(raw.get("idempotency"), Mapping):
        # Never slice alphabetically: ``json.dumps(sort_keys=True)`` destroys insertion
        # order, so retention is decided by ``op_seq`` at write time via
        # ``trim_receipts_by_op_seq`` instead of an arbitrary mapping-order slice here.
        base["idempotency"] = {
            str(k): copy.deepcopy(v)
            for k, v in raw["idempotency"].items()
            if isinstance(k, str) and isinstance(v, Mapping)
        }
    raw_seq = raw.get("next_op_seq")
    if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) and raw_seq >= 0:
        base["next_op_seq"] = int(raw_seq)
    ring = raw.get("committed_op_ring")
    if isinstance(ring, list):
        base["committed_op_ring"] = [
            copy.deepcopy(dict(row)) for row in ring if isinstance(row, Mapping)
        ]
    raw_evicted = raw.get("evicted_up_to_revision")
    if isinstance(raw_evicted, int) and not isinstance(raw_evicted, bool) and raw_evicted >= 0:
        base["evicted_up_to_revision"] = int(raw_evicted)
    return base


def read_everyday_desire_state(day_root: Path | str) -> tuple[dict[str, Any], str | None]:
    """100% zero-write read of stored everyday_desire state."""
    path = everyday_desire_state_path(day_root)
    if path.exists() and path.is_symlink():
        return default_everyday_desire_state(), "DESIRE_STATE_SYMLINK"
    if not path.exists():
        return default_everyday_desire_state(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _validate_state(raw), None
    except Exception:
        return default_everyday_desire_state(), "DESIRE_STATE_CORRUPTED"


def _atomic_write_state(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_state(state)
    path = everyday_desire_state_path(day_root)
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise EverydayDesireError("DESIRE_STATE_SYMLINK", "Symlink directory forbidden.", 503)
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise EverydayDesireError("DESIRE_STATE_SYMLINK", "Symlink file forbidden.", 503)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".everyday_desires_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return normalized


def _is_turn_only_live(
    item: Mapping[str, Any],
    *,
    now_dt: datetime,
    current_turn: int,
    current_scene_id: str,
) -> bool:
    if int(item.get("born_turn", current_turn)) < current_turn:
        return False
    bound_scene = item.get("scene_id")
    if isinstance(bound_scene, str) and bound_scene and bound_scene != current_scene_id:
        return False
    exp_dt = _parse_iso(item.get("expires_at") or item.get("ttl_until"))
    if exp_dt is not None and now_dt >= exp_dt:
        return False
    created_dt = _parse_iso(item.get("created_at"))
    raw_ttl = item.get("ttl_seconds")
    ttl_sec = (
        int(raw_ttl)
        if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) and raw_ttl > 0
        else DEFAULT_TURN_ONLY_TTL_SECONDS
    )
    if created_dt is not None and (now_dt - created_dt).total_seconds() >= ttl_sec:
        return False
    return True


def arbitrate_six_concerns(
    concerns: list[Mapping[str, Any]],
    *,
    foreground_activity: Mapping[str, Any] | None = None,
    body_context: Mapping[str, Any] | None = None,
    budget_context: Mapping[str, Any] | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
    ownership_context: Mapping[str, Any] | None = None,
    day_root: Path | str | None = None,
) -> dict[str, Any]:
    """Deterministic Ch. 35 §3 multi-scale 6-concern conflict arbitration (ZERO scalar utility score!)."""
    if channel_visibility not in CHANNEL_VISIBILITIES:
        channel_visibility = "ordinary_work"

    fg = dict(foreground_activity or {})
    fg_active = str(fg.get("status") or ("ACTIVE" if fg.get("activity_id") else "IDLE")).upper() == "ACTIVE"
    fg_interruptibility = str(fg.get("interruptibility") or "CHECKPOINTABLE").upper()
    fg_domain = str(fg.get("domain") or "creation")

    body = dict(body_context or {})
    high_fatigue = bool(body.get("high_fatigue")) or str(body.get("fatigue_band") or "") in ("strong", "high")
    thirst_noticeable = str(body.get("thirst_band") or "") in ("mild", "noticeable", "strong")

    budget = dict(budget_context or {})
    available_jpy = (
        budget.get("available_small_treat_jpy")
        if budget.get("available_small_treat_jpy") is not None
        else budget.get("available_jpy")
    )
    saving_for = budget.get("saving_for_label") or budget.get("saving_for")

    # 18 章 §二 Day2（「历史影响下一次选择」A/B 闭环）：心愿候选层对**已拥有同款**
    # 的 possession 关切做抑制。证据由调用方从经济 owner 的持有事实（OWNED 实例）
    # 只读带来（``owned_catalog_item_ids``），本模块零 I/O、零猜测：focus_ref 或
    # origin_refs 命中已在持有清单里的目录 id，即按 ALREADY_OWNED_SUPPRESSED 过滤。
    owned_catalog_item_ids: set[str] = set()
    if isinstance(ownership_context, Mapping):
        for ref in ownership_context.get("owned_catalog_item_ids") or []:
            text = str(ref or "").strip()
            if text:
                owned_catalog_item_ids.add(text)

    eligible: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []

    for raw_c in concerns:
        if not isinstance(raw_c, Mapping):
            continue
        c = copy.deepcopy(dict(raw_c))
        domain = str(c.get("domain") or "")
        cref = str(c.get("concern_ref") or "")
        origin_refs = c.get("origin_refs")
        if domain not in CONCERN_DOMAINS:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "INVALID_DOMAIN"})
            continue
        if not isinstance(origin_refs, list) or not [x for x in origin_refs if str(x).strip()]:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "MISSING_ORIGIN_EVIDENCE"})
            continue
        if domain == "intimacy":
            if not adult_gate_open:
                filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "ADULT_GATE_CLOSED"})
                continue
            if channel_visibility != "private_intimate":
                filtered_out.append(
                    {"concern_ref": cref, "domain": domain, "reason_code": "SENSITIVE_CONCERN_WITHHELD_ON_CHANNEL"}
                )
                continue
        if _is_public_or_partner_channel(channel_visibility) and _is_private_desire_item(c):
            filtered_out.append(
                {"concern_ref": cref, "domain": domain, "reason_code": "PRIVATE_CONCERN_WITHHELD_ON_CHANNEL"}
            )
            continue
        if c.get("reachable", True) is False or c.get("permitted", True) is False:
            filtered_out.append({"concern_ref": cref, "domain": domain, "reason_code": "UNEXECUTABLE_OR_UNPERMITTED"})
            continue
        if day_root is not None:
            lost, lost_reason = _concern_lost_origin(day_root, c)
            if lost:
                filtered_out.append(
                    {
                        "concern_ref": cref,
                        "domain": domain,
                        "reason_code": "ORIGIN_NO_LONGER_PRESENT",
                        "origin_reason": lost_reason,
                        "frustration_debt": 0,
                        "overdue_penalty": 0,
                    }
                )
                continue
        if domain == "possession" and owned_catalog_item_ids:
            focus = str(c.get("focus_ref") or "").strip()
            origin_pool = {str(r or "").strip() for r in (c.get("origin_refs") or [])}
            if (focus and focus in owned_catalog_item_ids) or (
                    origin_pool & owned_catalog_item_ids):
                filtered_out.append(
                    {
                        "concern_ref": cref,
                        "domain": domain,
                        "reason_code": "ALREADY_OWNED_SUPPRESSED",
                    }
                )
                continue
        cost_jpy = c.get("estimated_cost_jpy")
        if (
            domain == "possession"
            and isinstance(cost_jpy, int)
            and isinstance(available_jpy, int)
            and cost_jpy > available_jpy
        ):
            filtered_out.append(
                {
                    "concern_ref": cref,
                    "domain": domain,
                    "reason_code": "DEFERRED_BY_BUDGET_ENVELOPE",
                    "saving_for": saving_for,
                }
            )
            continue
        eligible.append(c)

    coexisting_micro: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    chosen_primary: dict[str, Any] | None = None
    coordination_mode = "NO_ACTION_QUIET"

    # Separate inline micro-actions (e.g., sipping water at desk) from macro/breakpoint shifts
    pressing_comfort = [
        c
        for c in eligible
        if c.get("domain") == "comfort"
        and (c.get("urgency") == "pressing_body" or high_fatigue)
        and c.get("cost_scale") != "micro_inline"
    ]
    inline_micro = [
        c
        for c in eligible
        if str(c.get("cost_scale") or "needs_breakpoint") == "micro_inline"
        or (c.get("domain") == "comfort" and thirst_noticeable and c.get("cost_scale") != "macro_outing")
    ]
    macro_candidates = [c for c in eligible if c not in inline_micro]

    if pressing_comfort:
        chosen_primary = pressing_comfort[0]
        coordination_mode = "YIELD_TO_REST_OR_BODY_RECOVERY"
        for c in eligible:
            if c is not chosen_primary:
                if c in inline_micro and c.get("domain") == "comfort":
                    coexisting_micro.append(c)
                else:
                    deferred.append(
                        {
                            "concern_ref": c.get("concern_ref"),
                            "domain": c.get("domain"),
                            "reason_code": "DEFERRED_FOR_REST_RECOVERY",
                            "frustration_debt": 0,
                        }
                    )
    elif fg_active and fg_interruptibility == "ATOMIC":
        # Foreground work is in an uninterruptible atomic step (e.g., wet watercolor wash)
        creation_match = next((c for c in eligible if c.get("domain") == fg_domain), None)
        chosen_primary = creation_match
        coexisting_micro = list(inline_micro)
        coordination_mode = (
            "CONTINUE_ATOMIC_WORK_WITH_INLINE_MICRO"
            if coexisting_micro
            else "CONTINUE_ATOMIC_WORK_DEFER_OTHERS"
        )
        for c in macro_candidates:
            if c is not chosen_primary:
                deferred.append(
                    {
                        "concern_ref": c.get("concern_ref"),
                        "domain": c.get("domain"),
                        "reason_code": "FOREGROUND_ATOMIC_NOT_INTERRUPTED",
                        "frustration_debt": 0,
                    }
                )
    else:
        coexisting_micro = list(inline_micro)
        # Stable domain priority without numeric utility leaderboard:
        # explicit sustained creation/project -> companionship/shared -> exploration -> possession -> intimacy
        domain_order = ("creation", "comfort", "companionship", "exploration", "possession", "intimacy")
        macro_sorted = sorted(
            macro_candidates,
            key=lambda x: (
                0 if x.get("urgency") == "timely" else 1,
                domain_order.index(str(x.get("domain"))) if str(x.get("domain")) in domain_order else 99,
                str(x.get("concern_ref") or ""),
            ),
        )
        if macro_sorted:
            chosen_primary = macro_sorted[0]
            # Check if possession candidate chooses voluntary restraint for a bigger goal
            if (
                chosen_primary.get("domain") == "possession"
                and saving_for
                and chosen_primary.get("urgency") == "optional"
            ):
                deferred.append(
                    {
                        "concern_ref": chosen_primary.get("concern_ref"),
                        "domain": "possession",
                        "reason_code": "VOLUNTARY_RESTRAINT_SAVING_FOR_GOAL",
                        "saving_for": saving_for,
                        "frustration_debt": 0,
                    }
                )
                chosen_primary = macro_sorted[1] if len(macro_sorted) > 1 else None
                coordination_mode = (
                    "SAVE_BREAKPOINT_AND_SWITCH" if chosen_primary else "NO_ACTION_VOLUNTARY_RESTRAINT"
                )
            else:
                coordination_mode = (
                    "COEXIST_PRIMARY_AND_MICRO"
                    if coexisting_micro
                    else ("SAVE_BREAKPOINT_AND_SWITCH" if fg_active else "SELECT_FEASIBLE_CONCERN")
                )
            for c in macro_sorted[1:]:
                if c is not chosen_primary:
                    deferred.append(
                        {
                            "concern_ref": c.get("concern_ref"),
                            "domain": c.get("domain"),
                            "reason_code": "DEFERRED_TO_LATER_WINDOW",
                            "frustration_debt": 0,
                        }
                    )
        elif coexisting_micro:
            coordination_mode = "INLINE_MICRO_ONLY"

    return {
        "coordination_mode": coordination_mode,
        "channel_visibility": channel_visibility,
        "chosen_primary": (
            {
                "concern_ref": chosen_primary.get("concern_ref"),
                "domain": chosen_primary.get("domain"),
                "expression": chosen_primary.get("expression"),
            }
            if chosen_primary
            else None
        ),
        "coexisting_micro_actions": [
            {
                "concern_ref": m.get("concern_ref"),
                "domain": m.get("domain"),
                "expression": m.get("expression"),
            }
            for m in coexisting_micro
        ],
        "deferred_concerns": deferred,
        "filtered_concerns": filtered_out,
        "total_utility_score_forbidden": True,
        "overdue_penalty": 0,
        "frustration_debt": 0,
        "happiness_bonus_on_completion": 0,
    }


def _is_private_desire_item(row: Mapping[str, Any]) -> bool:
    """Return True when a desire or concern row is subject-private / unshared."""
    if row.get("shared") is False:
        return True
    if bool(row.get("private")):
        return True
    if str(row.get("visibility") or "").strip().lower() == "private":
        return True
    return False


def _is_public_or_partner_channel(channel_visibility: str) -> bool:
    """Return True for public, ordinary-work, or partner-facing visibility channels."""
    vis = str(channel_visibility or "").strip().lower()
    return vis not in ("subject_private", "private_intimate")


def _scrub_private_from_arbitration(
    arbitration: Any,
    hidden_refs: set[str],
) -> Any:
    if not isinstance(arbitration, Mapping) or not hidden_refs:
        return copy.deepcopy(arbitration)
    out = copy.deepcopy(dict(arbitration))
    primary = out.get("chosen_primary")
    if isinstance(primary, Mapping):
        pref = str(primary.get("concern_ref") or "").strip()
        pdes = str(primary.get("desire_id") or "").strip()
        if (pref and pref in hidden_refs) or (pdes and pdes in hidden_refs):
            out["chosen_primary"] = None
    for list_key in ("coexisting_micro_actions", "deferred_concerns", "filtered_concerns"):
        rows = out.get(list_key)
        if isinstance(rows, list):
            kept: list[Any] = []
            for r in rows:
                if isinstance(r, Mapping):
                    rref = str(r.get("concern_ref") or "").strip()
                    rdes = str(r.get("desire_id") or "").strip()
                    if (rref and rref in hidden_refs) or (rdes and rdes in hidden_refs):
                        continue
                kept.append(r)
            out[list_key] = kept
    return out


def project_everyday_desires(
    day_root: Path | str,
    *,
    now_iso: str | datetime | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
    forget_barrier: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """100% zero-write read projection of active retained & live turn_only concerns."""
    now_dt = _require_now_dt(now_iso)
    state, err = read_everyday_desire_state(day_root)
    cur_turn = int(state["turn_index"])
    cur_scene = str(state["current_scene_id"])
    is_public_partner = _is_public_or_partner_channel(channel_visibility)

    hidden_private_refs: set[str] = set()

    def _record_hidden(row: Mapping[str, Any]) -> None:
        cref = str(row.get("concern_ref") or "").strip()
        did = str(row.get("desire_id") or "").strip()
        if cref:
            hidden_private_refs.add(cref)
        if did:
            hidden_private_refs.add(did)

    live_turn_only: list[dict[str, Any]] = []
    faded_in_projection = 0
    for item in state.get("turn_only_buffer", []):
        if _is_turn_only_live(item, now_dt=now_dt, current_turn=cur_turn, current_scene_id=cur_scene):
            if item.get("domain") == "intimacy" and (
                not adult_gate_open or channel_visibility != "private_intimate"
            ):
                _record_hidden(item)
                continue
            if is_public_partner and _is_private_desire_item(item):
                _record_hidden(item)
                continue
            live_turn_only.append(copy.deepcopy(item))
        else:
            faded_in_projection += 1

    visible_retained: list[dict[str, Any]] = []
    auto_subsided: list[dict[str, Any]] = []
    for item in state.get("retained_concerns", []):
        if item.get("status", "ACTIVE") != "ACTIVE":
            continue
        if item.get("domain") == "intimacy" and (
            not adult_gate_open or channel_visibility != "private_intimate"
        ):
            _record_hidden(item)
            continue
        if is_public_partner and _is_private_desire_item(item):
            _record_hidden(item)
            continue
        # Ch.35 §五: a wholly source-lost concern quietly subsides in the projection
        # (zero disk write, zero frustration debt / penalty / resentment / illness).
        lost, lost_reason = _concern_lost_origin(day_root, item)
        if lost:
            auto_subsided.append(
                {
                    "concern_ref": item.get("concern_ref"),
                    "domain": item.get("domain"),
                    "expression": item.get("expression"),
                    "origin_refs": copy.deepcopy(item.get("origin_refs") or []),
                    "reason": "origin_no_longer_present",
                    "origin_reason": lost_reason,
                    "frustration_debt": 0,
                    "penalty": 0,
                    "resentment_delta": 0,
                    "illness_delta": 0,
                }
            )
            continue
        visible_retained.append(copy.deepcopy(item))

    last_arbitration: Any = copy.deepcopy(state.get("last_arbitration"))
    if is_public_partner and hidden_private_refs:
        last_arbitration = _scrub_private_from_arbitration(last_arbitration, hidden_private_refs)

    # Zero-write unified forget barrier enforcement (fail-closed -> empty collections).
    barrier: Mapping[str, Any] | None = forget_barrier
    if barrier is None and _rlf is not None and day_root is not None:
        barrier = _rlf.load_unified_forget_barrier_readonly(_canonical_day_root(day_root))
    if _rlf is not None and isinstance(barrier, Mapping):
        if barrier.get("fail_closed"):
            live_turn_only = []
            visible_retained = []
            auto_subsided = []
            last_arbitration = None
        else:
            live_turn_only = _rlf.filter_collection_by_barrier(live_turn_only, barrier)
            visible_retained = _rlf.filter_collection_by_barrier(visible_retained, barrier)
            auto_subsided = _rlf.filter_collection_by_barrier(auto_subsided, barrier)
            if barrier.get("barrier_active") and isinstance(last_arbitration, Mapping):
                last_arbitration = _rlf.filter_document_by_barrier(
                    last_arbitration,
                    barrier,
                    nullable_entity_keys=("chosen_primary",),
                )

    return {
        "schema": DESIRE_VIEW_SCHEMA,
        "state_schema": DESIRE_STATE_SCHEMA,
        "read_only": True,
        "state_status": "degraded" if err else "ok",
        "state_reason_code": err,
        "revision": state["revision"],
        "turn_index": cur_turn,
        "current_scene_id": cur_scene,
        "observed_at": _format_iso(now_dt),
        "channel_visibility": channel_visibility,
        "turn_only_concerns": live_turn_only,
        "retained_concerns": visible_retained,
        "auto_subsided_concerns": auto_subsided,
        "naturally_faded_count": int(state["naturally_faded_count"]) + faded_in_projection,
        "satisfied_count": int(state["satisfied_count"]),
        "dropped_count": int(state["dropped_count"]),
        "last_arbitration": last_arbitration,
        "guarantees": {
            "supported_domains": list(CONCERN_DOMAINS),
            "total_utility_score_forbidden": True,
            "turn_only_writes_permanent_row": False,
            "overdue_penalty": 0,
            "frustration_debt": 0,
            "sensitive_intimacy_isolated": True,
            "source_loss_auto_subsides": True,
        },
        "llm_calls": 0,
    }


def apply_everyday_desire_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: str | datetime | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
) -> dict[str, Any]:
    """Deterministic mutation entrypoint for Ch. 35 everyday desires & 6-concern arbitration.

    The whole read-check-mutate-write cycle runs under ``state_file_lock`` so two
    writers can never interleave and lose a receipt.
    """
    if not isinstance(payload, Mapping):
        raise EverydayDesireError("INVALID_PAYLOAD", "Payload must be a JSON object.", 400)
    root = Path(day_root)
    with state_file_lock(everyday_desire_state_path(root)):
        return _apply_everyday_desire_action_locked(
            root,
            payload,
            now_iso=now_iso,
            channel_visibility=channel_visibility,
            adult_gate_open=adult_gate_open,
        )


def _apply_everyday_desire_action_locked(
    root: Path,
    payload: Mapping[str, Any],
    *,
    now_iso: str | datetime | None = None,
    channel_visibility: str = "subject_private",
    adult_gate_open: bool = False,
) -> dict[str, Any]:
    now_dt = _require_now_dt(now_iso)
    now_str = _format_iso(now_dt)

    state, err = read_everyday_desire_state(root)
    if err is not None:
        raise EverydayDesireError(err, "Corrupted everyday_desires state.", 503)

    action = str(payload.get("action") or "").strip()
    if not action:
        raise EverydayDesireError("MISSING_ACTION", "Field 'action' is required.", 400)

    op_key = payload.get("operation_key")
    fp_body = {k: v for k, v in payload.items() if k != "operation_key"}
    fp_hash = hashlib.sha256(
        json.dumps(fp_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if op_key is not None:
        if not isinstance(op_key, str) or not op_key.strip():
            raise EverydayDesireError("INVALID_OPERATION_KEY", "operation_key must be non-empty.", 400)
        op_key = op_key.strip()
        outcome, detail = reconcile_evicted_or_active_op(
            root,
            state,
            op_key,
            dict_key="idempotency",
            payload_hash=fp_hash,
        )
        if outcome == "conflict":
            raise EverydayDesireError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "Conflicting payload for operation_key.",
                409,
            )
        if outcome == "expired":
            raise EverydayDesireError(
                "RETRY_WINDOW_EXPIRED",
                "operation_key is older than the retained retry window.",
                409,
            )
        if outcome in ("hit_full", "hit_evicted") and isinstance(detail, Mapping):
            legacy_fp = detail.get("fingerprint")
            if legacy_fp and str(legacy_fp) != fp_hash:
                raise EverydayDesireError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "Conflicting payload for operation_key.",
                    409,
                )
            cached_receipt = detail.get("receipt")
            if not isinstance(cached_receipt, Mapping):
                cached_receipt = detail
            proj = project_everyday_desires(
                root,
                now_iso=now_dt,
                channel_visibility=channel_visibility,
                adult_gate_open=adult_gate_open,
            )
            proj["status"] = "already_applied"
            proj["replayed"] = True
            proj["reconciled_from"] = "committed_op_ring" if outcome == "hit_evicted" else "idempotency"
            proj["action_receipt"] = copy.deepcopy(dict(cached_receipt))
            return proj

    # Sweep expired turn_only items cleanly before mutating
    cur_turn = int(state["turn_index"])
    cur_scene = str(state["current_scene_id"])
    still_live: list[dict[str, Any]] = []
    swept_faded = 0
    for item in state["turn_only_buffer"]:
        if _is_turn_only_live(item, now_dt=now_dt, current_turn=cur_turn, current_scene_id=cur_scene):
            still_live.append(item)
        else:
            swept_faded += 1
    state["turn_only_buffer"] = still_live
    state["naturally_faded_count"] = int(state["naturally_faded_count"]) + swept_faded

    receipt: dict[str, Any]

    if action == "emerge_concern":
        domain = str(payload.get("domain") or "").strip()
        if domain not in CONCERN_DOMAINS:
            raise EverydayDesireError("INVALID_CONCERN_DOMAIN", f"Unsupported domain: {domain}", 400)
        origin_kind = str(payload.get("origin_kind") or "immediate_opportunity").strip()
        if origin_kind not in ORIGIN_KINDS:
            raise EverydayDesireError("INVALID_ORIGIN_KIND", f"Unsupported origin_kind: {origin_kind}", 400)
        origin_refs_raw = payload.get("origin_refs")
        if not isinstance(origin_refs_raw, list):
            raise EverydayDesireError("MISSING_ORIGIN_REFS", "origin_refs must be a non-empty list.", 400)
        origin_refs = [str(r).strip() for r in origin_refs_raw if isinstance(r, str) and str(r).strip()]
        if not origin_refs:
            raise EverydayDesireError("MISSING_ORIGIN_REFS", "Cannot invent desire without valid origin_refs.", 409)

        # Ch.35 §一: structured origins must be genuinely observed.  Historical plain
        # strings stay valid unless the caller explicitly demands full grounding.
        require_all = payload.get("require_observed_origin") is True
        for ref in origin_refs:
            structured = split_structured_origin_ref(ref)
            if structured is None and not require_all:
                continue
            if structured is None:
                raise EverydayDesireError(
                    "CONCERN_ORIGIN_NOT_OBSERVED",
                    f"Unstructured origin {ref!r} cannot satisfy require_observed_origin.",
                    409,
                )
            ok, reason = verify_concern_origin_observed_readonly(root, ref)
            if not ok:
                raise EverydayDesireError(
                    "CONCERN_ORIGIN_NOT_OBSERVED",
                    f"Origin {ref!r} is not observed ({reason}).",
                    409,
                )

        expression = str(payload.get("expression") or "").strip()[:200]
        if not expression:
            raise EverydayDesireError("EMPTY_EXPRESSION", "expression is required.", 400)
        persistence = str(payload.get("persistence") or "turn_only").strip()
        if persistence not in PERSISTENCE_MODES:
            raise EverydayDesireError("INVALID_PERSISTENCE", f"Unsupported persistence: {persistence}", 400)
        urgency = str(payload.get("urgency") or "optional").strip()
        if urgency not in URGENCY_LEVELS:
            raise EverydayDesireError("INVALID_URGENCY", f"Unsupported urgency: {urgency}", 400)
        cost_scale = str(payload.get("cost_scale") or "needs_breakpoint").strip()
        if cost_scale not in ACTION_COST_SCALES:
            raise EverydayDesireError("INVALID_COST_SCALE", f"Unsupported cost_scale: {cost_scale}", 400)

        focus_ref = str(payload.get("focus_ref") or origin_refs[0]).strip()[:120]
        cref = str(payload.get("concern_ref") or _stable_id("ccn", domain, focus_ref, now_str, state["revision"] + 1))

        concern_doc: dict[str, Any] = {
            "kind": CONCERN_ITEM_KIND,
            "concern_ref": cref,
            "subject_ref": str(payload.get("subject_ref") or "runtime_subject"),
            "domain": domain,
            "origin_kind": origin_kind,
            "focus_ref": focus_ref,
            "origin_refs": origin_refs,
            "expression": expression,
            "urgency": urgency,
            "persistence": persistence,
            "cost_scale": cost_scale,
            "scene_id": cur_scene,
            "born_turn": cur_turn,
            "created_at": now_str,
            "status": "ACTIVE",
            "estimated_cost_jpy": payload.get("estimated_cost_jpy")
            if isinstance(payload.get("estimated_cost_jpy"), int) and not isinstance(payload.get("estimated_cost_jpy"), bool)
            else None,
        }
        if persistence == "turn_only":
            ttl_sec = payload.get("ttl_seconds", DEFAULT_TURN_ONLY_TTL_SECONDS)
            if isinstance(ttl_sec, bool) or not isinstance(ttl_sec, int) or ttl_sec <= 0:
                raise EverydayDesireError("INVALID_TTL", "ttl_seconds must be positive int.", 400)
            concern_doc["ttl_seconds"] = ttl_sec
            # Deduplicate identical focus_ref + domain in same turn
            state["turn_only_buffer"] = [
                x
                for x in state["turn_only_buffer"]
                if not (x.get("domain") == domain and x.get("focus_ref") == focus_ref)
            ]
            state["turn_only_buffer"].append(concern_doc)
            state["turn_only_buffer"] = state["turn_only_buffer"][-MAX_EPHEMERAL_CONCERNS:]
        else:
            state["retained_concerns"] = [
                x
                for x in state["retained_concerns"]
                if not (x.get("domain") == domain and x.get("focus_ref") == focus_ref and x.get("status") == "ACTIVE")
            ]
            state["retained_concerns"].append(concern_doc)
            state["retained_concerns"] = state["retained_concerns"][-MAX_RETAINED_CONCERNS:]

        receipt = {"action": "emerge_concern", "concern": copy.deepcopy(concern_doc)}

    elif action in ("advance_turn", "leave_scene"):
        faded_now = len(state["turn_only_buffer"])
        state["turn_only_buffer"] = []
        state["naturally_faded_count"] = int(state["naturally_faded_count"]) + faded_now
        if action == "advance_turn":
            state["turn_index"] = cur_turn + 1
        else:
            new_scene = str(payload.get("new_scene_id") or "elsewhere").strip() or "elsewhere"
            state["current_scene_id"] = new_scene
        receipt = {
            "action": action,
            "faded_turn_only_count": faded_now,
            "frustration_debt": 0,
            "failure_recorded": False,
        }

    elif action in ("satisfy_concern", "drop_concern", "invalidate_origin"):
        target_ref = str(payload.get("concern_ref") or "").strip()
        origin_ref = str(payload.get("origin_ref") or "").strip()
        matched = 0
        next_turn_only: list[dict[str, Any]] = []
        for item in state["turn_only_buffer"]:
            hit = (target_ref and item.get("concern_ref") == target_ref) or (
                origin_ref and origin_ref in (item.get("origin_refs") or [])
            )
            if hit:
                matched += 1
                if action == "satisfy_concern":
                    state["satisfied_count"] = int(state["satisfied_count"]) + 1
                else:
                    state["naturally_faded_count"] = int(state["naturally_faded_count"]) + 1
            else:
                next_turn_only.append(item)
        state["turn_only_buffer"] = next_turn_only

        next_retained: list[dict[str, Any]] = []
        for item in state["retained_concerns"]:
            hit = (target_ref and item.get("concern_ref") == target_ref) or (
                origin_ref and origin_ref in (item.get("origin_refs") or [])
            )
            if hit:
                matched += 1
                if action == "satisfy_concern":
                    state["satisfied_count"] = int(state["satisfied_count"]) + 1
                else:
                    state["dropped_count"] = int(state["dropped_count"]) + 1
            else:
                next_retained.append(item)
        state["retained_concerns"] = next_retained

        if matched == 0 and target_ref:
            raise EverydayDesireError("CONCERN_NOT_FOUND", f"No active concern {target_ref}.", 404)

        receipt = {
            "action": action,
            "resolved_count": matched,
            "happiness_score_bonus": 0,
            "frustration_debt": 0,
        }

    elif action == "arbitrate_concerns":
        combined = list(state["retained_concerns"]) + list(state["turn_only_buffer"])
        arb = arbitrate_six_concerns(
            combined,
            foreground_activity=payload.get("foreground_activity")
            if isinstance(payload.get("foreground_activity"), Mapping)
            else None,
            body_context=payload.get("body_context")
            if isinstance(payload.get("body_context"), Mapping)
            else None,
            budget_context=payload.get("budget_context")
            if isinstance(payload.get("budget_context"), Mapping)
            else None,
            ownership_context=payload.get("ownership_context")
            if isinstance(payload.get("ownership_context"), Mapping)
            else None,
            channel_visibility=str(payload.get("channel_visibility") or channel_visibility),
            adult_gate_open=bool(payload.get("adult_gate_open", adult_gate_open)),
            day_root=root,
        )
        state["last_arbitration"] = copy.deepcopy(arb)
        receipt = {"action": "arbitrate_concerns", "arbitration": arb}

    else:
        raise EverydayDesireError("UNKNOWN_ACTION", f"Unsupported everyday_desire action: {action}", 400)

    state["revision"] = int(state["revision"]) + 1
    state["updated_at"] = now_str
    if op_key is not None:
        persisted = stamp_receipt(
            state,
            op_key,
            receipt,
            revision=int(state["revision"]),
            dict_key="idempotency",
            payload_hash=fp_hash,
            committed_at=now_str,
        )
        persisted["fingerprint"] = fp_hash
        # Retention follows submission order (``op_seq``), never alphabetical key order.
        trim_receipts_by_op_seq(
            state,
            dict_key="idempotency",
            max_keep=MAX_IDEMPOTENCY_RECORDS,
        )
    _atomic_write_state(root, state)

    eff_vis = str(payload.get("channel_visibility") or channel_visibility)
    eff_gate = bool(payload.get("adult_gate_open", adult_gate_open))
    proj = project_everyday_desires(
        root,
        now_iso=now_dt,
        channel_visibility=eff_vis,
        adult_gate_open=eff_gate,
    )
    proj["read_only"] = False
    proj["status"] = "applied"
    proj["replayed"] = False
    proj["action_receipt"] = receipt
    return proj


def _sweep_entry_intensity_and_ttl(
    entry: Mapping[str, Any],
    *,
    now_dt: datetime,
    now_str: str,
    fallback_updated_at: str | None,
    stale_max_age_seconds: int,
    decay_intensity: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Evaluate TTL, intensity decay, and stale age for one retained/active entry.

    Returns ``(updated_entry_or_None, outcome)`` where ``outcome`` is one of:
    ``"keep"``, ``"decayed"``, ``"faded_ttl"``, ``"faded_decay"``, ``"faded_stale"``.
    """
    row = copy.deepcopy(dict(entry))
    exp_dt = _parse_iso(row.get("expires_at") or row.get("ttl_until"))
    if exp_dt is not None and now_dt >= exp_dt:
        return None, "faded_ttl"

    created_dt = _parse_iso(row.get("created_at"))
    raw_ttl = row.get("ttl_seconds")
    if (
        created_dt is not None
        and isinstance(raw_ttl, int)
        and not isinstance(raw_ttl, bool)
        and raw_ttl > 0
        and (now_dt - created_dt).total_seconds() >= raw_ttl
    ):
        return None, "faded_ttl"

    decay_rate = row.get("decay_per_hour")
    intensity = row.get("intensity")
    has_decay_model = (
        isinstance(decay_rate, (int, float))
        and not isinstance(decay_rate, bool)
        and float(decay_rate) > 0.0
        and isinstance(intensity, (int, float))
        and not isinstance(intensity, bool)
    )
    anchor_dt = _parse_iso(row.get("updated_at") or row.get("created_at") or fallback_updated_at)

    if has_decay_model:
        cur_int = float(intensity)
        if cur_int <= DESIRE_FADE_INTENSITY_THRESHOLD:
            return None, "faded_decay"
        if decay_intensity and anchor_dt is not None and now_dt > anchor_dt:
            elapsed_h = (now_dt - anchor_dt).total_seconds() / 3600.0
            if elapsed_h > 0.0:
                new_int = round(max(0.0, cur_int - float(decay_rate) * elapsed_h), 4)
                if new_int <= DESIRE_FADE_INTENSITY_THRESHOLD:
                    return None, "faded_decay"
                if new_int != round(cur_int, 4):
                    row["intensity"] = new_int
                    row["updated_at"] = now_str
                    return row, "decayed"
        return row, "keep"

    if stale_max_age_seconds > 0 and anchor_dt is not None:
        if (now_dt - anchor_dt).total_seconds() >= stale_max_age_seconds:
            return None, "faded_stale"

    return row, "keep"


def _evaluate_sweep_plan(
    root: Path,
    state: Mapping[str, Any],
    *,
    now_dt: datetime,
    now_str: str,
    retained_max_age_seconds: int,
    decay_intensity: bool = True,
) -> dict[str, Any]:
    cur_turn = int(state.get("turn_index") or 0)
    cur_scene = str(state.get("current_scene_id") or "study_desk")
    fallback_ts = state.get("updated_at") if isinstance(state.get("updated_at"), str) else None

    next_turn_only: list[dict[str, Any]] = []
    turn_only_faded = 0
    for item in state.get("turn_only_buffer") or []:
        if not isinstance(item, Mapping):
            turn_only_faded += 1
            continue
        if _is_turn_only_live(
            item,
            now_dt=now_dt,
            current_turn=cur_turn,
            current_scene_id=cur_scene,
        ):
            next_turn_only.append(copy.deepcopy(dict(item)))
        else:
            turn_only_faded += 1

    next_retained: list[dict[str, Any]] = []
    source_lost_count = 0
    stale_retained_count = 0
    retained_decay_faded = 0
    retained_decayed = 0
    for item in state.get("retained_concerns") or []:
        if not isinstance(item, Mapping):
            stale_retained_count += 1
            continue
        if str(item.get("status") or "ACTIVE").upper() != "ACTIVE":
            stale_retained_count += 1
            continue
        lost, _ = _concern_lost_origin(root, item)
        if lost:
            source_lost_count += 1
            continue
        updated_row, outcome = _sweep_entry_intensity_and_ttl(
            item,
            now_dt=now_dt,
            now_str=now_str,
            fallback_updated_at=fallback_ts,
            stale_max_age_seconds=retained_max_age_seconds,
            decay_intensity=decay_intensity,
        )
        if updated_row is None:
            if outcome == "faded_decay":
                retained_decay_faded += 1
            else:
                stale_retained_count += 1
            continue
        if outcome == "decayed":
            retained_decayed += 1
        next_retained.append(updated_row)

    next_active_desires: list[dict[str, Any]] = []
    active_faded = 0
    active_decayed = 0
    for item in state.get("active_desires") or []:
        if not isinstance(item, Mapping):
            active_faded += 1
            continue
        st = str(item.get("status") or "active").strip().lower()
        if st != "active":
            active_faded += 1
            continue
        updated_row, outcome = _sweep_entry_intensity_and_ttl(
            item,
            now_dt=now_dt,
            now_str=now_str,
            fallback_updated_at=fallback_ts,
            stale_max_age_seconds=retained_max_age_seconds,
            decay_intensity=decay_intensity,
        )
        if updated_row is None:
            active_faded += 1
            continue
        if outcome == "decayed":
            active_decayed += 1
        next_active_desires.append(updated_row)

    # Deduplicate faded counts when a synced v05 desire faded in both retained_concerns and active_desires
    retained_faded_total = source_lost_count + stale_retained_count + retained_decay_faded
    total_faded = turn_only_faded + retained_faded_total + (active_faded if retained_faded_total == 0 else 0)
    total_decayed = max(retained_decayed, active_decayed)
    changed = (
        turn_only_faded > 0
        or source_lost_count > 0
        or stale_retained_count > 0
        or retained_decay_faded > 0
        or retained_decayed > 0
        or active_faded > 0
        or active_decayed > 0
    )
    return {
        "changed": changed,
        "next_turn_only": next_turn_only,
        "next_retained": next_retained,
        "next_active_desires": next_active_desires,
        "turn_only_faded_count": turn_only_faded,
        "source_lost_count": source_lost_count,
        "stale_retained_count": stale_retained_count,
        "retained_decay_faded_count": retained_decay_faded,
        "faded_count": total_faded,
        "decayed_count": total_decayed,
    }


def sweep_everyday_desires_ttl_and_decay(
    day_root: Path | str,
    *,
    now_iso: str | datetime | None = None,
    retained_max_age_seconds: int = DEFAULT_RETAINED_STALE_SECONDS,
    decay_intensity: bool = True,
) -> dict[str, Any]:
    """Sweep expired ``turn_only_buffer`` (``created_at + ttl_seconds``) and stale/decayed ``retained_concerns``.

    Strictly 100% zero-write when ``life/everyday_desires.json`` is absent, corrupt, or has
    no expired/decayed/source-lost items. Zero frustration debt, zero LLM calls.
    """
    now_dt = _require_now_dt(now_iso)
    now_str = _format_iso(now_dt)
    root = _canonical_day_root(day_root)
    path = everyday_desire_state_path(root)

    if path.is_symlink() or not path.exists() or not path.is_file():
        return {
            "ok": True,
            "changed": False,
            "reason": "state_absent",
            "faded_count": 0,
            "decayed_count": 0,
            "turn_only_faded_count": 0,
            "source_lost_count": 0,
            "stale_retained_count": 0,
            "frustration_debt": 0,
            "llm_calls": 0,
        }

    pre_state, pre_err = read_everyday_desire_state(root)
    if pre_err is not None:
        return {
            "ok": False,
            "changed": False,
            "reason": pre_err,
            "faded_count": 0,
            "decayed_count": 0,
            "turn_only_faded_count": 0,
            "source_lost_count": 0,
            "stale_retained_count": 0,
            "frustration_debt": 0,
            "llm_calls": 0,
        }

    pre_plan = _evaluate_sweep_plan(
        root,
        pre_state,
        now_dt=now_dt,
        now_str=now_str,
        retained_max_age_seconds=retained_max_age_seconds,
        decay_intensity=decay_intensity,
    )
    if not pre_plan["changed"]:
        return {
            "ok": True,
            "changed": False,
            "reason": "no_change",
            "faded_count": 0,
            "decayed_count": 0,
            "turn_only_faded_count": 0,
            "source_lost_count": 0,
            "stale_retained_count": 0,
            "revision": int(pre_state["revision"]),
            "updated_at": pre_state.get("updated_at"),
            "frustration_debt": 0,
            "llm_calls": 0,
        }

    with state_file_lock(path):
        state, err = read_everyday_desire_state(root)
        if err is not None:
            return {
                "ok": False,
                "changed": False,
                "reason": err,
                "faded_count": 0,
                "decayed_count": 0,
                "turn_only_faded_count": 0,
                "source_lost_count": 0,
                "stale_retained_count": 0,
                "frustration_debt": 0,
                "llm_calls": 0,
            }
        plan = _evaluate_sweep_plan(
            root,
            state,
            now_dt=now_dt,
            now_str=now_str,
            retained_max_age_seconds=retained_max_age_seconds,
            decay_intensity=decay_intensity,
        )
        if not plan["changed"]:
            return {
                "ok": True,
                "changed": False,
                "reason": "no_change",
                "faded_count": 0,
                "decayed_count": 0,
                "turn_only_faded_count": 0,
                "source_lost_count": 0,
                "stale_retained_count": 0,
                "revision": int(state["revision"]),
                "updated_at": state.get("updated_at"),
                "frustration_debt": 0,
                "llm_calls": 0,
            }
        state["turn_only_buffer"] = plan["next_turn_only"]
        state["retained_concerns"] = plan["next_retained"]
        state["active_desires"] = plan["next_active_desires"]
        state["naturally_faded_count"] = int(state.get("naturally_faded_count") or 0) + int(plan["faded_count"])
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str
        saved = _atomic_write_state(root, state)

    return {
        "ok": True,
        "changed": True,
        "reason": "swept",
        "faded_count": int(plan["faded_count"]),
        "decayed_count": int(plan["decayed_count"]),
        "turn_only_faded_count": int(plan["turn_only_faded_count"]),
        "source_lost_count": int(plan["source_lost_count"]),
        "stale_retained_count": int(plan["stale_retained_count"]),
        "revision": int(saved["revision"]),
        "updated_at": now_str,
        "path": str(path),
        "frustration_debt": 0,
        "llm_calls": 0,
    }


tick_everyday_desires_lifecycle = sweep_everyday_desires_ttl_and_decay


def _project_v05_desire_to_everyday_records(
    desire: Mapping[str, Any],
    *,
    cur_turn: int,
    cur_scene: str,
    now_str: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    desire_id = str(desire.get("desire_id") or "").strip()
    layer = str(desire.get("layer") or "micro_craving")
    category = str(desire.get("category") or "curiosity_mystery")
    domain = CATEGORY_TO_CONCERN_DOMAIN.get(category, "exploration")
    text = str(desire.get("text") or "").strip()[:200]
    world_anchor = desire.get("world_anchor")
    anchor_str = str(world_anchor).strip() if isinstance(world_anchor, str) and world_anchor.strip() else ""
    origin_ref = anchor_str or f"spark:{desire_id}"
    # Avoid structured prefixes that would falsely trigger Ch.35 object/material/place source-loss
    if split_structured_origin_ref(origin_ref) is not None:
        origin_ref = f"spark:{origin_ref}"
    origin_val = str(desire.get("origin") or "self_emerged")
    if category in ("appetite_craving", "cozy_comfort"):
        origin_kind = "body_need"
    elif category == "craft_ambition":
        origin_kind = "sustained_interest"
    elif category in ("social_longing", "mischief_rivalry", "intimate_warmth"):
        origin_kind = "relational_wish"
    else:
        origin_kind = "immediate_opportunity"
    intensity = float(desire.get("intensity") if isinstance(desire.get("intensity"), (int, float)) else 0.65)
    decay_rate = float(desire.get("decay_per_hour") if isinstance(desire.get("decay_per_hour"), (int, float)) else 0.06)
    friction_count = max(0, int(desire.get("friction_count") or 0))
    est_cost = max(0, int(desire.get("estimated_cost_jpy") or 0))
    requires_outing = bool(desire.get("requires_outing", False))
    shared = False if category == "intimate_warmth" else bool(desire.get("shared", True))
    created_at = str(desire.get("created_at") or now_str)
    updated_at = str(desire.get("updated_at") or created_at)
    expires_at = desire.get("expires_at") if isinstance(desire.get("expires_at"), str) else None

    active_row: dict[str, Any] = {
        "desire_id": desire_id,
        "layer": layer,
        "category": category,
        "domain": domain,
        "text": text,
        "origin": origin_val,
        "world_anchor": anchor_str or None,
        "intensity": round(max(0.0, min(1.0, intensity)), 4),
        "decay_per_hour": round(max(0.0, decay_rate), 4),
        "friction_count": friction_count,
        "estimated_cost_jpy": est_cost,
        "requires_outing": requires_outing,
        "status": "active",
        "shared": shared,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    if expires_at is not None:
        active_row["expires_at"] = expires_at

    concern_row: dict[str, Any] = {
        "kind": CONCERN_ITEM_KIND,
        "concern_ref": f"v05_{desire_id}",
        "desire_id": desire_id,
        "synced_from": "v05_inner_spark",
        "subject_ref": "runtime_subject",
        "domain": domain,
        "category": category,
        "layer": layer,
        "origin_kind": origin_kind,
        "focus_ref": anchor_str or desire_id,
        "origin_refs": [origin_ref],
        "world_anchor": anchor_str or None,
        "expression": text,
        "urgency": "optional",
        "persistence": "retained",
        "cost_scale": "macro_outing" if requires_outing else "needs_breakpoint",
        "scene_id": cur_scene,
        "born_turn": cur_turn,
        "created_at": created_at,
        "updated_at": updated_at,
        "status": "ACTIVE",
        "intensity": active_row["intensity"],
        "decay_per_hour": active_row["decay_per_hour"],
        "friction_count": friction_count,
        "estimated_cost_jpy": est_cost,
        "requires_outing": requires_outing,
        "shared": shared,
    }
    if expires_at is not None:
        concern_row["expires_at"] = expires_at
    return active_row, concern_row


def sync_v05_desires_projection(
    day_root: Path | str,
    desires: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    now_iso: str | datetime | None = None,
    create_if_absent: bool = False,
) -> dict[str, Any]:
    """Sync active ``v05_inner_spark`` desires into existing ``life/everyday_desires.json``.

    Strictly zero-write when ``everyday_desires.json`` does not exist (unless ``create_if_absent=True``)
    or when the projected slice is already identical on disk. Preserves all native Ch. 35 concerns.
    """
    now_dt = _require_now_dt(now_iso)
    now_str = _format_iso(now_dt)
    root = _canonical_day_root(day_root)
    path = everyday_desire_state_path(root)

    if path.is_symlink():
        return {"ok": False, "changed": False, "reason": "DESIRE_STATE_SYMLINK", "llm_calls": 0}
    if not path.exists() and not create_if_absent:
        return {"ok": True, "changed": False, "reason": "state_absent", "llm_calls": 0}

    active_inputs = [
        d
        for d in desires
        if isinstance(d, Mapping)
        and str(d.get("status") or "active").strip().lower() == "active"
        and str(d.get("desire_id") or "").strip()
        and str(d.get("text") or "").strip()
    ]

    if path.exists():
        pre_state, pre_err = read_everyday_desire_state(root)
        if pre_err is not None:
            return {"ok": False, "changed": False, "reason": pre_err, "llm_calls": 0}
        pre_active: list[dict[str, Any]] = []
        pre_synced_concerns: list[dict[str, Any]] = []
        for d in active_inputs:
            arow, crow = _project_v05_desire_to_everyday_records(
                d,
                cur_turn=int(pre_state.get("turn_index") or 0),
                cur_scene=str(pre_state.get("current_scene_id") or "study_desk"),
                now_str=now_str,
            )
            pre_active.append(arow)
            pre_synced_concerns.append(crow)
        existing_native = [
            row
            for row in pre_state.get("retained_concerns") or []
            if isinstance(row, Mapping) and row.get("synced_from") != "v05_inner_spark"
        ]
        merged_pre_retained = (existing_native + pre_synced_concerns)[-MAX_RETAINED_CONCERNS:]
        if (
            pre_state.get("active_desires", []) == pre_active[-MAX_ACTIVE_DESIRES_PROJECTION:]
            and pre_state.get("retained_concerns", []) == merged_pre_retained
        ):
            return {
                "ok": True,
                "changed": False,
                "reason": "no_change",
                "synced_count": len(pre_active),
                "revision": int(pre_state["revision"]),
                "llm_calls": 0,
            }

    with state_file_lock(path):
        if not path.exists() and not create_if_absent:
            return {"ok": True, "changed": False, "reason": "state_absent", "llm_calls": 0}
        state, err = read_everyday_desire_state(root)
        if err is not None:
            return {"ok": False, "changed": False, "reason": err, "llm_calls": 0}
        cur_turn = int(state.get("turn_index") or 0)
        cur_scene = str(state.get("current_scene_id") or "study_desk")

        projected_active: list[dict[str, Any]] = []
        projected_concerns: list[dict[str, Any]] = []
        for d in active_inputs:
            arow, crow = _project_v05_desire_to_everyday_records(
                d,
                cur_turn=cur_turn,
                cur_scene=cur_scene,
                now_str=now_str,
            )
            projected_active.append(arow)
            projected_concerns.append(crow)

        native_retained = [
            copy.deepcopy(dict(row))
            for row in state.get("retained_concerns") or []
            if isinstance(row, Mapping) and row.get("synced_from") != "v05_inner_spark"
        ]
        next_retained = (native_retained + projected_concerns)[-MAX_RETAINED_CONCERNS:]
        next_active = projected_active[-MAX_ACTIVE_DESIRES_PROJECTION:]

        if (
            path.exists()
            and state.get("active_desires", []) == next_active
            and state.get("retained_concerns", []) == next_retained
        ):
            return {
                "ok": True,
                "changed": False,
                "reason": "no_change",
                "synced_count": len(next_active),
                "revision": int(state["revision"]),
                "llm_calls": 0,
            }

        state["active_desires"] = next_active
        state["retained_concerns"] = next_retained
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str
        saved = _atomic_write_state(root, state)

    return {
        "ok": True,
        "changed": True,
        "reason": "synced",
        "synced_count": len(next_active),
        "revision": int(saved["revision"]),
        "updated_at": now_str,
        "path": str(path),
        "llm_calls": 0,
    }


__all__ = [
    "ACTION_COST_SCALES",
    "CATEGORY_TO_CONCERN_DOMAIN",
    "CHANNEL_VISIBILITIES",
    "CONCERN_DOMAINS",
    "CONCERN_ITEM_KIND",
    "DEFAULT_RETAINED_STALE_SECONDS",
    "DEFAULT_TURN_ONLY_TTL_SECONDS",
    "DESIRE_FADE_INTENSITY_THRESHOLD",
    "DESIRE_STATE_SCHEMA",
    "DESIRE_VIEW_SCHEMA",
    "EverydayDesireError",
    "ORIGIN_KINDS",
    "PERSISTENCE_MODES",
    "URGENCY_LEVELS",
    "apply_everyday_desire_action",
    "arbitrate_six_concerns",
    "default_everyday_desire_state",
    "everyday_desire_state_path",
    "OBSERVED_ORIGIN_PREFIXES",
    "project_everyday_desires",
    "read_everyday_desire_state",
    "split_structured_origin_ref",
    "sweep_everyday_desires_ttl_and_decay",
    "sync_v05_desires_projection",
    "tick_everyday_desires_lifecycle",
    "verify_concern_origin_observed_readonly",
]
