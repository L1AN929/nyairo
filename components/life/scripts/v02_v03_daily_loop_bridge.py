#!/usr/bin/env python3
"""0.2 + 0.3 自主日常主循环生产者-消费者桥接层（零大模型调用，严格回执门控）。

职责与边界
----------
1. **生产者侧 ``collect_v02_v03_loop_candidates(day_root, *, now_iso=None)``（100% 纯零写入）**：
   - 当 ``<day_root>`` 为空目录或各切片状态文件不存在时，绝不创建任何目录或文件、绝不改动 mtime。
   - 读取 ``0.2`` 五类真实生活线索并转化为带明确 ``source_ref`` / ``provenance_refs`` 的
     ``CandidateRecord``（严格满足 ``candidate_sources_ag0`` 禁打分键、禁正文键契约）与有界上下文摘要：
     * 床头明日便签（``desk_presence`` / ``life_future_items``）；
     * 共同待定小约定（``home_hearth`` ``tentative_plans`` 中 ``PROPOSED`` / ``TENTATIVE`` / ``RESCHEDULED``）；
     * 断点创作托盘（``workbench_curio`` 未完草稿 / 参考图 / 素材）；
     * 书店橱窗收藏与散步/集市心愿（``outing_bench_bookstore`` / ``outing_routes_market``）；
     * 绿植观察手账（``longterm_room_habits`` / ``longterm_first_room_habits`` ``plant_sketch_links``）。
   - 读取 ``0.3`` 能力包络与生态约束（``ADR-041`` / ``ADR-039`` / ``Ch. 19-20``）：
     * ``illness.capability_envelope``：处于 ``prodromal`` / ``acute`` / ``convalescent``（兼容
       ``prodrome`` / ``acute_illness`` / ``convalescence``）时按 ``candidate_cap`` 收缩候选上限；
       ``blocks_strenuous_outing == True`` 时硬性过滤远途/高耗能外出候选并产出卧床静养/喝温水恢复候选；
       ``blocks_adult_intimacy == True`` 时标记亲密硬阻断；``creative_block == True`` 时过滤高强度创作冲刺；
     * ``economy``：当 ``budget_tight == True`` 或存在未完成 ``savings_goals`` 时，过滤超预算非必需冲动消费
       （记录 ``put_back_tight_budget`` / ``save_for_later`` 且 ``begs_partner_when_tight == False``），
       并产出独立稿费/校对兼职候选（``manuscript_fee`` / ``proofreading_shift``）；
     * ``autonomy_rhythm`` 拒绝立场锁：``REFUSED_FIRM`` 生效时 ``capitulation_allowed == False``，硬拦截违背立场的请求；
     * ``v03_clock_evaporation`` 与 ``v03_embodied_social``：支持无意义发呆（``meaningless_idle``，零效率惩罚）
       及双人书桌附着共在（``beside_at_desk``，``foreground_activity_interrupted == False``）与双手占用感知。

2. **消费者侧 ``settle_v02_v03_round_consequences(day_root, round_event, *, now_iso=None)``**：
   - 严格权威与回执门控（V-A）：仅当动作主体为本人（``subject`` / ``her`` / ``life_resident``）、
     携带真实未伪造回执（``receipt_ref``）且 ``effect_state == "APPLIED"`` 时才回写对应生活切片。
   - 若 ``effect_state`` 为 ``"UNKNOWN"``、``"FAILED"``、``"REFUSED"``，或属于伪造回执/越权身份（如 ``partner``），
     严守铁律：**零乐观落账、零切片写入**。
   - 基于 ``operation_key`` 幂等账本防重放双计。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
_LIFE_SCRIPTS = Path(__file__).resolve().parent
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(_LIFE_SCRIPTS), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if os.name == "nt":
    if "fcntl" not in sys.modules:
        import types as _types

        _win_fcntl = _types.ModuleType("fcntl")
        _win_fcntl.LOCK_SH = 1  # type: ignore[attr-defined]
        _win_fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
        _win_fcntl.LOCK_NB = 4  # type: ignore[attr-defined]
        _win_fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
        _win_fcntl.flock = lambda _fd, _op: None  # type: ignore[attr-defined]
        _win_fcntl.lockf = lambda _fd, _op, *_a: None  # type: ignore[attr-defined]
        sys.modules["fcntl"] = _win_fcntl
    try:
        import zoneinfo as _zi

        try:
            _zi.ZoneInfo("Asia/Shanghai")
        except Exception:
            _orig_zi = _zi.ZoneInfo
            _FALLBACK_TZS = {
                "Asia/Shanghai": timezone(timedelta(hours=8), "Asia/Shanghai"),
                "Asia/Tokyo": timezone(timedelta(hours=9), "Asia/Tokyo"),
                "UTC": timezone.utc,
            }

            def _win_zoneinfo(key: str) -> Any:
                try:
                    return _orig_zi(key)
                except Exception:
                    if key in _FALLBACK_TZS:
                        return _FALLBACK_TZS[key]
                    raise

            _zi.ZoneInfo = _win_zoneinfo  # type: ignore[assignment,misc]
    except Exception:
        pass

from candidate_sources_ag0 import (  # noqa: E402
    CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
    CandidateRecord,
    SOURCE_PERSONAL_OPPORTUNITY,
)
from life_future_items import FutureItemError, FutureItems, STATE_FILENAME as FUTURE_ITEMS_FILENAME  # noqa: E402
import everyday_desire_lifecycle as v03_desires  # noqa: E402
import life_dwell as v03_dwell  # noqa: E402
import v02_common_ground  # noqa: E402
import v02_recall_projection  # noqa: E402
import v03_clock_evaporation as v03_clock  # noqa: E402
import v03_economy_illness_bridge as v03_eco_bridge  # noqa: E402
import world_disposal_contract as v03_disposal  # noqa: E402

from components.phone import (  # noqa: E402
    autonomy_rhythm_view,
    desk_presence_view,
    emotional_tension_view,
    home_hearth_view,
    long_companionship_view,
    outing_places_view,
    somatic_comfort_view,
    v03_ecology_view,
    v03_embodied_social_view,
    window_keepsake_view,
    workbench_curio_view,
)

BRIDGE_SCHEMA = "chiyo.life.v02_v03_daily_loop_bridge.v1"
CONSEQUENCE_LEDGER_SCHEMA = "phone.v02_v03.consequence_ledger.v1"
CONSEQUENCE_LEDGER_FILENAME = "v02_v03_consequence_ledger.json"
PURCHASE_CUES_FILENAME = "v03_purchase_cues.json"
DESK_TOMORROW_NOTE_FILENAME = "desk_tomorrow_note.json"

DEFAULT_TZ = timezone(timedelta(hours=8))
MAX_LEDGER_RECEIPTS = 128
MAX_SUMMARY_ITEMS = 12

FROZEN_ANCHOR_SHA256: dict[str, str] = {
    # 2026-11-14 X1 批次重钉：07 章 §四 生产接线（wake 票 est 合计 → reserve(est_tokens=...)
    # + daily_token_budget=50000 生产启用）有意修改装配文件；旧摘要 76b4c7cb…31fa5a。
    "components/life/scripts/autonomous_life_assembly.py": (
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a"
    ),
    "components/life/scripts/life_sharing_service.py": (
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
    ),
}

ALLOWED_CONSEQUENCE_ACTORS: frozenset[str] = frozenset({"subject", "her", "life_resident"})
FORGED_RECEIPT_ORIGINS: frozenset[str] = frozenset(
    {"forged", "client_fabricated", "untrusted_prompt", "npc_speech", "external_claim"}
)

ILLNESS_PHASE_FAMILY_MAP: dict[str, str] = {
    "healthy": "healthy",
    "prodrome": "prodromal",
    "prodromal": "prodromal",
    "acute_illness": "acute",
    "acute": "acute",
    "convalescence": "convalescent",
    "convalescent": "convalescent",
    "recovered": "recovered",
}
ACTIVE_ILLNESS_FAMILIES: frozenset[str] = frozenset({"prodromal", "acute", "convalescent"})

OPEN_PLAN_STATUSES: frozenset[str] = frozenset({"PROPOSED", "TENTATIVE", "RESCHEDULED"})
STRENUOUS_PLAN_KINDS: frozenset[str] = frozenset(
    {"outing", "far_outing", "hike", "excursion", "market_trip", "walk_together"}
)

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Pure Helpers & Zero-Write File Inspection
# ---------------------------------------------------------------------------


def _safe_token(value: Any, fallback: str = "item") -> str:
    cleaned = _SAFE_TOKEN_RE.sub("_", str(value or "").strip()).strip("_.-")
    return (cleaned or fallback)[:64]


def _ensure_structured_ref(raw_ref: Any, *, default_prefix: str = "ref") -> str:
    text = str(raw_ref or "").strip()
    if ":" in text:
        prefix, rest = text.split(":", 1)
        clean_p = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix).strip("_.-")
        clean_r = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", rest).strip("_")
        if clean_p and clean_p[0].isalpha() and clean_r:
            return f"{clean_p}:{clean_r}"[:160]
    return f"{default_prefix}:{_safe_token(text, 'id')}"


def _parse_iso(value: Any) -> Optional[datetime]:
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
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _tz_offset_str(dt: datetime) -> str:
    iso = dt.isoformat(timespec="seconds")
    if len(iso) >= 6 and iso[-6] in ("+", "-") and iso[-3] == ":":
        return iso[-6:]
    return "+08:00"


def _read_optional_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Read a JSON object without creating directories, files, or touching mtimes."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def resolve_day_and_life_roots(day_root: Path | str) -> tuple[Path, Path]:
    """Resolve ``(instance_day_root, life_state_root)`` purely without disk writes.

    Supports both ``<instance_root>`` (where ``phone/`` lives at ``<instance_root>/phone``
    and Life state at ``<instance_root>/life/state``) and direct ``<state_root>`` layouts.
    """
    raw = Path(day_root)
    if not (raw / "phone").exists() and raw.name == "state" and raw.parent.name == "life":
        candidate_instance = raw.parent.parent
        if (candidate_instance / "phone").is_dir():
            return candidate_instance, raw
    life_state = raw / "life" / "state"
    if life_state.is_dir() and not life_state.is_symlink():
        return raw, life_state
    return raw, raw


def has_v02_v03_active_state(day_root: Path | str) -> bool:
    """Pure zero-write check: True only when ``<day_root>/phone`` exists with slice files."""
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    phone_dir = inst_root / "phone"
    if phone_dir.is_symlink() or not phone_dir.is_dir():
        return False
    try:
        return any(p.suffix == ".json" and p.is_file() for p in phone_dir.iterdir())
    except OSError:
        return False


def verify_frozen_anchors(repo_root: Path | str | None = None) -> dict[str, Any]:
    """Verify SHA-256 digests of frozen architectural anchor files (zero writes)."""
    base = Path(repo_root) if repo_root is not None else REPO_ROOT
    files_report: dict[str, dict[str, Any]] = {}
    all_matched = True
    for rel_path, expected_sha in FROZEN_ANCHOR_SHA256.items():
        target = base / rel_path
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        matched = actual_sha == expected_sha
        if not matched:
            all_matched = False
        files_report[rel_path] = {
            "expected_sha256": expected_sha,
            "actual_sha256": actual_sha,
            "matched": matched,
        }
    return {
        "ok": all_matched,
        "files": files_report,
        "llm_calls": 0,
    }


def _resolve_observation_clock_zero_write(inst_root: Path, now_iso: Optional[str]) -> tuple[datetime, str, str, str]:
    dt = _parse_iso(now_iso)
    if dt is None:
        clock_doc = _read_optional_json_zero_write(inst_root / "phone" / v03_clock.V03_CLOCK_FILENAME)
        if isinstance(clock_doc, Mapping):
            wc = clock_doc.get("world_clock")
            if isinstance(wc, Mapping):
                dt = _parse_iso(wc.get("last_committed_at"))
    if dt is None:
        dt = datetime.now(tz=DEFAULT_TZ)
    eff_iso = _format_iso(dt)
    tz_off = _tz_offset_str(dt)
    local_day = dt.date().isoformat()
    return dt, eff_iso, local_day, tz_off


# ---------------------------------------------------------------------------
# Producer Side: Pure Zero-Write Candidate & Context Collector
# ---------------------------------------------------------------------------


def _read_ecology_envelope_zero_write(inst_root: Path, effective_now_iso: str) -> dict[str, Any]:
    primary_eco_file = inst_root / "phone" / v03_eco_bridge.STATE_FILENAME
    alias_eco_file = inst_root / "phone" / "v03_ecology_state.json"
    eco_file = primary_eco_file if primary_eco_file.is_file() else alias_eco_file
    world_wallet_exists = any(
        (inst_root / sub).is_file()
        for sub in (
            "world/state/world_property_economy.json",
            "world/world_property_economy.json",
            "world-home/state/world_property_economy.json",
            "world-home/world_property_economy.json",
        )
    )
    if not eco_file.is_file() and not world_wallet_exists:
        default_cap = v03_eco_bridge._capability_for_phase("healthy", 0.0)
        return {
            "present": False,
            "illness": {
                "phase": "healthy",
                "phase_family": "healthy",
                "is_actively_ill": False,
                "severity": 0.0,
                "rest_active": False,
                "capability_envelope": default_cap,
            },
            "economy": {
                "available_balance_cents": 0,
                "living_floor_cents": v03_eco_bridge.DEFAULT_LIVING_FLOOR_CENTS,
                "discretionary_margin_cents": 0,
                "budget_tight": False,
                "begs_partner_when_tight": False,
                "open_savings_goals": [],
                "world_economy_initialized": False,
            },
        }

    # Normalize potential raw phase aliases ("prodromal" / "acute" / "convalescent") in memory
    raw_eco = _read_optional_json_zero_write(eco_file)
    proj = v03_eco_bridge.project_v03_ecology(
        inst_root,
        has_subject_capability=True,
        now_iso=effective_now_iso,
    )
    proj_data = proj.get("data") if isinstance(proj.get("data"), Mapping) else proj
    ill_proj = dict((proj_data or {}).get("illness") or {})
    raw_phase = str(ill_proj.get("phase") or "healthy").strip().lower()
    if isinstance(raw_eco, Mapping) and isinstance(raw_eco.get("illness"), Mapping):
        stored_p = str(raw_eco["illness"].get("phase") or "").strip().lower()
        if stored_p in ILLNESS_PHASE_FAMILY_MAP:
            raw_phase = stored_p

    phase_family = ILLNESS_PHASE_FAMILY_MAP.get(raw_phase, "healthy")
    canonical_for_cap = {
        "healthy": "healthy",
        "prodromal": "prodrome",
        "acute": "acute_illness",
        "convalescent": "convalescence",
        "recovered": "recovered",
    }[phase_family]
    severity = float(ill_proj.get("severity") or 0.0)
    if phase_family == "acute" and severity <= 0.0:
        severity = 0.65
    elif phase_family == "prodromal" and severity <= 0.0:
        severity = 0.25
    elif phase_family == "convalescent" and severity <= 0.0:
        severity = 0.24

    cap_env = dict(v03_eco_bridge._capability_for_phase(canonical_for_cap, severity))
    if isinstance(ill_proj.get("capability_envelope"), Mapping) and raw_phase == ill_proj.get("phase"):
        cap_env.update(dict(ill_proj["capability_envelope"]))

    eco_proj = dict((proj_data or {}).get("economy") or {})
    goals_raw = list(eco_proj.get("savings_goals") or [])
    open_goals = [
        dict(g)
        for g in goals_raw
        if isinstance(g, Mapping)
        and (int(g.get("gap_cents") or 0) > 0 or str(g.get("state") or "") != "OWNED")
    ]
    # Only enforce budget_tight when ecology file or initialized world wallet actually exists
    economy_active = eco_file.is_file() or bool(eco_proj.get("world_economy_initialized"))
    budget_tight = bool(eco_proj.get("budget_tight")) if economy_active else False

    return {
        "present": True,
        "illness": {
            "phase": raw_phase,
            "phase_family": phase_family,
            "is_actively_ill": phase_family in ACTIVE_ILLNESS_FAMILIES,
            "severity": round(severity, 4),
            "rest_active": bool(ill_proj.get("rest_active", False)),
            "episode_id": ill_proj.get("episode_id"),
            "capability_envelope": cap_env,
        },
        "economy": {
            "economy_active": economy_active,
            "available_balance_cents": int(eco_proj.get("available_balance_cents") or 0),
            "living_floor_cents": int(
                eco_proj.get("living_floor_cents") or v03_eco_bridge.DEFAULT_LIVING_FLOOR_CENTS
            ),
            "discretionary_margin_cents": int(eco_proj.get("discretionary_margin_cents") or 0),
            "budget_tight": budget_tight,
            "begs_partner_when_tight": False,
            "open_savings_goals": open_goals,
            "world_economy_initialized": bool(eco_proj.get("world_economy_initialized")),
        },
    }


def _read_stance_locks_zero_write(inst_root: Path, effective_now_iso: str) -> list[dict[str, Any]]:
    rhythm_path = autonomy_rhythm_view.autonomy_rhythm_state_path(inst_root)
    if rhythm_path.is_symlink() or not rhythm_path.is_file():
        return []
    state, reason = autonomy_rhythm_view.read_autonomy_rhythm_state(inst_root)
    if reason is not None:
        return []
    proj = autonomy_rhythm_view.project_autonomy_rhythm(
        state,
        subject_view=True,
        now_iso=effective_now_iso,
    )
    rg = proj.get("refusal_guard") if isinstance(proj.get("refusal_guard"), Mapping) else {}
    out: list[dict[str, Any]] = []
    for lock in rg.get("active_locks") or []:
        if isinstance(lock, Mapping) and str(lock.get("stance") or "") == "REFUSED_FIRM":
            out.append({
                "lock_id": str(lock.get("lock_id") or ""),
                "topic_key": str(lock.get("topic_key") or ""),
                "topic_label": str(lock.get("topic_label") or ""),
                "category": str(lock.get("category") or "plain_unwilling"),
                "stance": "REFUSED_FIRM",
                "push_attempts": int(lock.get("push_attempts") or 1),
                "capitulation_allowed": False,
                "reason_summary": str(lock.get("reason_summary") or ""),
            })
    return out


def _read_embodied_co_presence_zero_write(inst_root: Path, effective_now_iso: str) -> dict[str, Any]:
    soc_path = v03_embodied_social_view.v03_embodied_social_state_path(inst_root)
    if soc_path.is_symlink() or not soc_path.is_file():
        return {
            "present": False,
            "partner_presence_mode": "absent",
            "partner_attached_at_desk": False,
            "foreground_activity_interrupted": False,
            "free_hand_count": 2,
            "both_hands_occupied": False,
            "left_hand_state": "free",
            "right_hand_state": "free",
        }
    status, body = v03_embodied_social_view.read_v03_embodied_social_view(
        inst_root,
        has_subject_capability=True,
        now_iso=effective_now_iso,
    )
    if status != 200 or not isinstance(body, Mapping):
        return {
            "present": False,
            "partner_presence_mode": "absent",
            "partner_attached_at_desk": False,
            "foreground_activity_interrupted": False,
            "free_hand_count": 2,
            "both_hands_occupied": False,
            "left_hand_state": "free",
            "right_hand_state": "free",
        }
    ec = body.get("embodied_contact") if isinstance(body.get("embodied_contact"), Mapping) else {}
    raw_pp = ec.get("partner_presence")
    if isinstance(raw_pp, str):
        mode = raw_pp.strip() or "absent"
    elif isinstance(raw_pp, Mapping):
        mode = str(raw_pp.get("presence_mode") or raw_pp.get("mode") or "absent")
    else:
        mode = "absent"
    hands = (
        ec.get("hand_slots")
        if isinstance(ec.get("hand_slots"), Mapping)
        else (ec.get("hands") if isinstance(ec.get("hands"), Mapping) else {})
    )
    lh = hands.get("left_hand") if isinstance(hands.get("left_hand"), Mapping) else {}
    rh = hands.get("right_hand") if isinstance(hands.get("right_hand"), Mapping) else {}
    lh_state = str(lh.get("state") or "free")
    rh_state = str(rh.get("state") or "free")
    free_count = sum(1 for st in (lh_state, rh_state) if st == "free")
    return {
        "present": True,
        "partner_presence_mode": mode,
        "partner_attached_at_desk": mode == "beside_at_desk",
        "foreground_activity_interrupted": False,
        "free_hand_count": free_count,
        "both_hands_occupied": free_count == 0,
        "left_hand_state": lh_state,
        "right_hand_state": rh_state,
    }


def _make_personal_candidate(
    *,
    slug: str,
    target_ref: str,
    causal_parent_refs: Iterable[str],
    provenance_refs: Iterable[str],
    availability_basis: str,
    observed_iso: str = "2026-06-10T09:00:00+08:00",
) -> CandidateRecord:
    s_target = _ensure_structured_ref(target_ref, default_prefix="item")
    parents = [_ensure_structured_ref(r, default_prefix="src") for r in causal_parent_refs if r]
    provs = [_ensure_structured_ref(r, default_prefix="prov") for r in provenance_refs if r]
    if not parents:
        parents = [s_target]
    if not provs:
        provs = [s_target]
    clean_slug = _safe_token(slug, "opp")
    return CandidateRecord(
        candidate_id=f"cand:{clean_slug}",
        candidate_kind=CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
        source_kind=SOURCE_PERSONAL_OPPORTUNITY,
        source_ref=parents[0],
        availability_status="OPEN",
        created_at=observed_iso,
        observed_at=observed_iso,
        recorded_at=observed_iso,
        idempotency_key=f"idem:{clean_slug}",
        target_refs=[s_target],
        causal_parent_refs=parents,
        provenance_refs=provs,
        semantic_identity=str(availability_basis or "PERSONAL_CLUE_OPEN")[:80],
    )


_DOMAIN_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"reading", "book", "books", "bookstore", "window_bookmark", "阅读", "书店", "看书", "读书"}),
    frozenset({"workbench", "craft", "writing", "drawing", "illustration", "draft", "creative", "创作", "手作", "画画", "写稿", "草稿"}),
    frozenset({"outing", "walk", "walk_route", "hike", "excursion", "stroll", "散步", "外出", "徒步", "遛弯"}),
    frozenset({"market", "shopping", "purchase", "market_wish", "集市", "逛街", "购物"}),
    frozenset({"plant", "plants", "plant_sketch", "gardening", "courtyard", "绿植", "盆栽", "浇花", "院子"}),
    frozenset({"plan", "plans", "tentative_plans", "约定", "计划"}),
)


def _read_v02_agency_and_runtime_summaries_zero_write(
    inst_root: Path,
    effective_now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """100% zero-write invocation of v02 agency-depth & runtime-migration context summaries."""
    agency_summary: dict[str, Any] = {}
    try:
        from components.phone.v02_agency_depth_view import summarize_agency_depth_for_context
    except ImportError:
        try:
            from v02_agency_depth_engine import (  # type: ignore[no-redef]
                summarize_agency_depth_state_for_context as summarize_agency_depth_for_context,
            )
        except ImportError:
            summarize_agency_depth_for_context = None  # type: ignore[assignment]
    if summarize_agency_depth_for_context is not None:
        try:
            res_ad = summarize_agency_depth_for_context(
                inst_root, include_private=True, now_iso=effective_now_iso
            )
            if isinstance(res_ad, Mapping):
                agency_summary = dict(res_ad)
        except Exception:
            agency_summary = {}

    runtime_summary: dict[str, Any] = {}
    try:
        from components.phone.v02_runtime_migration_view import summarize_runtime_migration_for_context
    except ImportError:
        try:
            from v02_runtime_governance_migration import (  # type: ignore[no-redef]
                build_runtime_migration_context_summary as summarize_runtime_migration_for_context,
            )
        except ImportError:
            summarize_runtime_migration_for_context = None  # type: ignore[assignment]
    if summarize_runtime_migration_for_context is not None:
        try:
            res_rm = summarize_runtime_migration_for_context(inst_root, now_iso=effective_now_iso)
            if isinstance(res_rm, Mapping):
                runtime_summary = dict(res_rm)
        except Exception:
            runtime_summary = {}

    return agency_summary, runtime_summary


def _match_satiation_multiplier(
    desc: Mapping[str, Any],
    satiation_multipliers: Mapping[str, Any],
) -> tuple[float, Optional[str]]:
    """Match candidate domain/title against ``satiation_multipliers`` without mutating anything."""
    if not satiation_multipliers:
        return 1.0, None

    clue_dom = str(desc.get("clue_domain") or "").strip().lower()
    act_fam = str(desc.get("action_family") or "").strip().lower()
    title_hint = str(desc.get("title_hint") or "").strip().lower()
    topic_key = str(desc.get("topic_key") or "").strip().lower()
    dom_tokens = set(re.split(r"[._:/\s-]+", f"{clue_dom} {act_fam}")) - {"", "v02", "v03", "v04"}

    expanded_tokens = set(dom_tokens)
    for grp in _DOMAIN_SYNONYM_GROUPS:
        if expanded_tokens & grp or any(g in title_hint for g in grp if len(g) >= 2):
            expanded_tokens |= grp

    matched_low_mult: Optional[float] = None
    matched_low_key: Optional[str] = None
    matched_high_mult: Optional[float] = None
    matched_high_key: Optional[str] = None

    for raw_k, raw_m in satiation_multipliers.items():
        if not isinstance(raw_m, (int, float)) or isinstance(raw_m, bool):
            continue
        mult = float(raw_m)
        k_str = str(raw_k or "").strip().lower()
        if not k_str:
            continue
        k_dom, k_title = (k_str.split(":", 1) if ":" in k_str else (k_str, ""))
        k_dom = k_dom.strip()
        k_title = k_title.strip()

        hit = False
        if k_title and len(k_title) >= 2 and (k_title in title_hint or k_title in topic_key):
            hit = True
        elif not k_title and k_dom:
            if (
                k_dom in expanded_tokens
                or k_dom in clue_dom
                or k_dom in act_fam
                or (len(k_dom) >= 2 and (k_dom in title_hint or k_dom in topic_key))
            ):
                hit = True

        if hit:
            if mult < 1.0:
                if matched_low_mult is None or mult < matched_low_mult:
                    matched_low_mult = mult
                    matched_low_key = str(raw_k)
            elif mult > 1.0:
                if matched_high_mult is None or mult > matched_high_mult:
                    matched_high_mult = mult
                    matched_high_key = str(raw_k)

    if matched_low_mult is not None:
        return matched_low_mult, matched_low_key
    if matched_high_mult is not None:
        return matched_high_mult, matched_high_key
    return 1.0, None


_HHMM_RANGE_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*[-~至到]\s*(\d{1,2})(?::(\d{2}))?")


def _rule_window_matches(trigger_window: Any, local_dt: datetime) -> bool:
    """Return True when ``local_dt`` falls inside the rule's ``trigger_window``."""
    win = str(trigger_window or "").strip().lower()
    if not win or win in ("anytime", "always", "all", "all_day", "daily", "全天", "随时"):
        return True
    hour = local_dt.hour
    minute = local_dt.minute
    cur_mins = hour * 60 + minute

    m = _HHMM_RANGE_RE.search(win)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        start_m = (h1 % 24) * 60 + min(59, m1)
        end_m = (h2 % 24) * 60 + min(59, m2)
        if start_m <= end_m:
            return start_m <= cur_mins <= end_m
        return cur_mins >= start_m or cur_mins <= end_m

    if any(tok in win for tok in ("late_night", "midnight", "bedtime", "深夜", "熬夜", "睡前", "夜间", "night", "22:", "23:")):
        return hour >= 22 or hour < 5
    if any(tok in win for tok in ("evening", "晚间", "晚上", "傍晚")):
        return 18 <= hour < 24
    if any(tok in win for tok in ("morning", "清晨", "早上", "上午")):
        return 5 <= hour < 12
    if any(tok in win for tok in ("afternoon", "午后", "下午")):
        return 12 <= hour < 18
    return False


def collect_v02_v03_loop_candidates(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Collect 0.2 & 0.3 life clues and apply 0.3 capability/economy/stance gates (100% zero-write)."""
    inst_root, life_root = resolve_day_and_life_roots(day_root)
    now_dt, effective_now_iso, local_date, tz_offset = _resolve_observation_clock_zero_write(
        inst_root, now_iso
    )

    ecology_env = _read_ecology_envelope_zero_write(inst_root, effective_now_iso)
    illness_info = ecology_env["illness"]
    economy_info = ecology_env["economy"]
    cap_env = illness_info["capability_envelope"]
    stance_locks = _read_stance_locks_zero_write(inst_root, effective_now_iso)
    co_presence = _read_embodied_co_presence_zero_write(inst_root, effective_now_iso)
    agency_depth_summary, runtime_migration_summary = _read_v02_agency_and_runtime_summaries_zero_write(
        inst_root, effective_now_iso
    )

    clock_path = inst_root / "phone" / v03_clock.V03_CLOCK_FILENAME
    clock_present = clock_path.is_file() and not clock_path.is_symlink()
    pending_unknown_count = 0
    evaporable_count = 0
    if clock_present:
        c_status, c_env = v03_clock.read_v03_clock_view(
            inst_root, has_subject_capability=True, now_iso=effective_now_iso
        )
        if c_status == 200 and isinstance(c_env.get("data"), Mapping):
            cdata = c_env["data"]
            il = cdata.get("iron_laws") if isinstance(cdata.get("iron_laws"), Mapping) else {}
            pending_unknown_count = len(il.get("pending_unknown_actions") or [])
            ap = (
                cdata.get("accountability_partitions")
                if isinstance(cdata.get("accountability_partitions"), Mapping)
                else {}
            )
            ep = ap.get("evaporable_pool") if isinstance(ap.get("evaporable_pool"), Mapping) else {}
            evaporable_count = int(ep.get("active_count") or 0)

    raw_items: list[tuple[CandidateRecord, dict[str, Any]]] = []

    # ------------------------------------------------------------------
    # 0.2 Producer 1: Bedside tomorrow note (desk_presence / FutureItems)
    # ------------------------------------------------------------------
    for f_root in ({life_root, inst_root}):
        f_path = f_root / FUTURE_ITEMS_FILENAME
        if f_path.is_file() and not f_path.is_symlink():
            try:
                store = FutureItems(f_root)
                open_rows = store.open_items()
            except FutureItemError:
                open_rows = []
            next_bday = desk_presence_view.next_business_day({"logical_at": effective_now_iso})
            next_local_day = str(next_bday.get("local_day") or "")
            for row in open_rows:
                due_now = row.is_due(effective_now_iso) or desk_presence_view.falls_on_local_day(
                    row.due_at, local_day=local_date, offset=tz_offset
                )
                is_tmr = bool(
                    next_local_day
                    and desk_presence_view.falls_on_local_day(
                        row.due_at, local_day=next_local_day, offset=tz_offset
                    )
                )
                if not (due_now or is_tmr):
                    continue
                art_ref = next(
                    (r for r in row.source_refs if str(r).startswith("artifact:")),
                    None,
                )
                slug = f"v02_tomorrow_{_safe_token(row.item_ref.split(':', 1)[-1])}"
                rec = _make_personal_candidate(
                    slug=slug,
                    target_ref=art_ref or row.item_ref,
                    causal_parent_refs=(row.item_ref, *(row.source_refs[:2])),
                    provenance_refs=(f"v02_desk:{_safe_token(row.item_ref)}", row.item_ref),
                    availability_basis=(
                        "BEDSIDE_TOMORROW_NOTE_DUE" if due_now else "BEDSIDE_TOMORROW_NOTE_SCHEDULED"
                    ),
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v02.desk_presence.tomorrow_note",
                        "action_family": "follow_tomorrow_note",
                        "source_ref": row.item_ref,
                        "artifact_ref": art_ref,
                        "provenance_refs": list(rec.provenance_refs),
                        "due_now": bool(due_now),
                        "due_at": row.due_at,
                        "title_hint": row.title[:120],
                        "topic_key": autonomy_rhythm_view._normalize_topic_key(row.title),
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": bool(art_ref),
                        "impulse_purchase": False,
                    },
                ))

    desk_note_doc = _read_optional_json_zero_write(inst_root / "phone" / DESK_TOMORROW_NOTE_FILENAME)
    if isinstance(desk_note_doc, Mapping) and str(desk_note_doc.get("status") or "OPEN").upper() == "OPEN":
        note_id = _safe_token(desk_note_doc.get("note_id") or "bedside_1")
        note_title = str(desk_note_doc.get("title") or desk_note_doc.get("note") or "明日便签").strip()[:120]
        art_ref = desk_note_doc.get("artifact_id")
        src_ref = f"tomorrow_note:{note_id}"
        rec = _make_personal_candidate(
            slug=f"v02_tomorrow_doc_{note_id}",
            target_ref=str(art_ref) if isinstance(art_ref, str) and art_ref else src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=(f"v02_desk:{note_id}", src_ref),
            availability_basis="BEDSIDE_TOMORROW_NOTE_DUE",
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.desk_presence.tomorrow_note",
                "action_family": "follow_tomorrow_note",
                "source_ref": src_ref,
                "artifact_ref": str(art_ref) if isinstance(art_ref, str) and art_ref else None,
                "provenance_refs": list(rec.provenance_refs),
                "due_now": True,
                "due_at": desk_note_doc.get("due_at") or effective_now_iso,
                "title_hint": note_title,
                "topic_key": autonomy_rhythm_view._normalize_topic_key(note_title),
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": bool(art_ref),
                "impulse_purchase": False,
            },
        ))

    # ------------------------------------------------------------------
    # 0.2 Producer 2: Co-authored tentative plans (home_hearth)
    # ------------------------------------------------------------------
    plans_path = home_hearth_view.plans_path(inst_root)
    if plans_path.is_file() and not plans_path.is_symlink():
        plans_state, p_reason = home_hearth_view.read_plans_state(inst_root)
        plan_rows: list[Mapping[str, Any]] = []
        if p_reason is None:
            plan_rows = list(plans_state.get("plans") or [])
        else:
            raw_plans_doc = _read_optional_json_zero_write(plans_path)
            if isinstance(raw_plans_doc, Mapping) and isinstance(raw_plans_doc.get("plans"), list):
                plan_rows = [p for p in raw_plans_doc["plans"] if isinstance(p, Mapping)]
        outing_cues = (
            "外出", "远郊", "远足", "徒步", "散步", "海边", "河岸", "公园", "集市", "古书街",
            "长椅", "步道", "outing", "walk", "hike", "market", "excursion",
        )
        for plan in plan_rows:
            status_up = str(plan.get("status") or "").strip().upper()
            if status_up not in OPEN_PLAN_STATUSES:
                continue
            pid = _safe_token(plan.get("plan_id") or "plan")
            title = str(plan.get("title") or "待定小约定").strip()[:120]
            win_note = str(plan.get("window_note") or "").strip()[:80]
            combined_plan_txt = f"{title} {win_note}".lower()
            p_kind = str(plan.get("kind") or "other").strip().lower()
            place_id = str(plan.get("place_id") or "").strip() or None
            cue_outing = any(cue in combined_plan_txt for cue in outing_cues)
            is_outing = bool(
                (place_id and place_id != "home") or p_kind in STRENUOUS_PLAN_KINDS or cue_outing
            )
            is_strenuous = bool(
                plan.get("strenuous") is True
                or p_kind in STRENUOUS_PLAN_KINDS
                or (place_id and place_id != "home")
                or cue_outing
            )
            src_ref = f"plan:{pid}"
            rec = _make_personal_candidate(
                slug=f"v02_plan_{pid}",
                target_ref=f"place:{place_id}" if place_id else src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=(f"v02_hearth:{pid}", src_ref),
                availability_basis=f"TENTATIVE_PLAN_{status_up}",
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v02.home_hearth.tentative_plans",
                    "action_family": "adopt_tentative_plan",
                    "source_ref": src_ref,
                    "place_id": place_id,
                    "plan_status": status_up,
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": title,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(title),
                    "requires_outing": is_outing,
                    "strenuous_outing": is_strenuous,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # ------------------------------------------------------------------
    # 0.2 Producer 3: Workbench unfinished draft / reference tray
    # ------------------------------------------------------------------
    tray_path = workbench_curio_view.tray_path(inst_root)
    if tray_path.is_file() and not tray_path.is_symlink():
        tray_state, t_reason = workbench_curio_view.read_tray_state(inst_root)
        if t_reason is None:
            tray = tray_state.get("tray") if isinstance(tray_state.get("tray"), Mapping) else {}
            work = tray.get("work") if isinstance(tray.get("work"), Mapping) else None
            images = list(tray.get("images") or [])
            materials = list(tray.get("materials") or [])
            note = tray.get("note")
            if work or images or materials or note:
                work_art = str(work.get("artifact_id")) if work and work.get("artifact_id") else None
                base_ver = (
                    str(work.get("baseline_version_id"))
                    if work and work.get("baseline_version_id")
                    else None
                )
                first_img = (
                    str(images[0].get("album_artifact_id"))
                    if images and isinstance(images[0], Mapping) and images[0].get("album_artifact_id")
                    else None
                )
                first_mat = (
                    str(materials[0].get("artifact_id"))
                    if materials and isinstance(materials[0], Mapping) and materials[0].get("artifact_id")
                    else None
                )
                primary_ref = work_art or first_mat or first_img or f"tray:rev_{tray_state.get('revision', 1)}"
                prov_list = ["v02_workbench:tray", primary_ref]
                if base_ver:
                    prov_list.append(base_ver)
                rec = _make_personal_candidate(
                    slug=f"v02_workbench_{_safe_token(primary_ref.split(':', 1)[-1])}",
                    target_ref=primary_ref,
                    causal_parent_refs=(primary_ref,),
                    provenance_refs=prov_list,
                    availability_basis="WORKBENCH_UNFINISHED_TRAY",
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v02.workbench.tray",
                        "action_family": "advance_workbench_draft",
                        "source_ref": primary_ref,
                        "artifact_ref": work_art,
                        "baseline_version_id": base_ver,
                        "image_count": len(images),
                        "material_count": len(materials),
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": str(note or "工作台未完草稿")[:120],
                        "topic_key": autonomy_rhythm_view._normalize_topic_key(str(note or "工作台草稿")),
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": True,
                        "impulse_purchase": False,
                    },
                ))

    # ------------------------------------------------------------------
    # 0.2 Producer 4: Bookstore window bookmarks & Outing routes / Market wishes
    # ------------------------------------------------------------------
    bb_path = outing_places_view.bench_bookstore_state_path(inst_root)
    if bb_path.is_file() and not bb_path.is_symlink():
        bb_state, bb_reason = outing_places_view.read_bench_bookstore_state(inst_root)
        if bb_reason is None:
            for bm in bb_state.get("window_bookmarks") or []:
                bcode = _safe_token(bm.get("book_code") or "book")
                bid = _safe_token(bm.get("bookmark_id") or bcode)
                btitle = str(bm.get("title") or bcode)[:120]
                src_ref = f"book:{bcode}"
                rec = _make_personal_candidate(
                    slug=f"v02_bookstore_{bcode}",
                    target_ref=src_ref,
                    causal_parent_refs=(src_ref,),
                    provenance_refs=(f"v02_bookstore:{bid}", src_ref),
                    availability_basis="BOOKSTORE_WINDOW_BOOKMARK",
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v02.outing_bench_bookstore.window_bookmark",
                        "action_family": "follow_bookstore_bookmark",
                        "source_ref": src_ref,
                        "book_code": bcode,
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": btitle,
                        "topic_key": autonomy_rhythm_view._normalize_topic_key(btitle),
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))

    rm_path = outing_places_view.routes_market_state_path(inst_root)
    if rm_path.is_file() and not rm_path.is_symlink():
        rm_state, rm_reason = outing_places_view.read_routes_market_state(inst_root)
        if rm_reason is None:
            for route in rm_state.get("routes") or []:
                rid = _safe_token(route.get("route_id") or "route")
                rtitle = str(route.get("title") or rid)[:120]
                stops = list(route.get("place_ids") or [])
                first_stop = str(stops[0]) if stops else "outside"
                src_ref = f"route:{rid}"
                rec = _make_personal_candidate(
                    slug=f"v02_outing_route_{rid}",
                    target_ref=f"place:{first_stop}",
                    causal_parent_refs=(src_ref,),
                    provenance_refs=(f"v02_outing_route:{rid}", src_ref),
                    availability_basis="SAVED_WALK_ROUTE_OUTING",
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v02.outing_routes_market.walk_route",
                        "action_family": "strenuous_outing_walk",
                        "source_ref": src_ref,
                        "place_id": first_stop,
                        "stops": stops,
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": rtitle,
                        "topic_key": autonomy_rhythm_view._normalize_topic_key(rtitle),
                        "requires_outing": True,
                        "strenuous_outing": True,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))
            for wish in rm_state.get("market_wishes") or []:
                wid = _safe_token(wish.get("wish_id") or wish.get("stall_code") or "wish")
                wtitle = str(wish.get("stall_title") or wid)[:120]
                cap_jpy = int(wish.get("budget_cap_jpy") or 0)
                if cap_jpy > 0:
                    src_ref = f"market_wish:{wid}"
                    rec = _make_personal_candidate(
                        slug=f"v02_market_wish_{wid}",
                        target_ref=src_ref,
                        causal_parent_refs=(src_ref,),
                        provenance_refs=(f"v02_market:{wid}", src_ref),
                        availability_basis="MARKET_NON_ESSENTIAL_WISH",
                    )
                    raw_items.append((
                        rec,
                        {
                            "candidate_id": rec.candidate_id,
                            "clue_domain": "v02.outing_routes_market.market_wish",
                            "action_family": "non_essential_purchase",
                            "source_ref": src_ref,
                            "item_title": wtitle,
                            "price_cents": cap_jpy,
                            "essential": False,
                            "provenance_refs": list(rec.provenance_refs),
                            "title_hint": wtitle,
                            "topic_key": autonomy_rhythm_view._normalize_topic_key(wtitle),
                            "requires_outing": False,
                            "strenuous_outing": False,
                            "creative_intensive": False,
                            "impulse_purchase": True,
                        },
                    ))

    # Optional explicit purchase cues in phone/v03_purchase_cues.json (zero-write read)
    purchase_cues_doc = _read_optional_json_zero_write(inst_root / "phone" / PURCHASE_CUES_FILENAME)
    if isinstance(purchase_cues_doc, Mapping) and isinstance(purchase_cues_doc.get("items"), list):
        for cue in purchase_cues_doc["items"]:
            if not isinstance(cue, Mapping):
                continue
            cid = _safe_token(cue.get("cue_id") or cue.get("item_title") or "buy")
            ititle = str(cue.get("item_title") or cid)[:120]
            price_cents = int(cue.get("price_cents") or 0)
            essential = bool(cue.get("essential", False))
            if price_cents <= 0:
                continue
            src_ref = f"purchase_cue:{cid}"
            rec = _make_personal_candidate(
                slug=f"v03_purchase_{cid}",
                target_ref=src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=(f"v03_economy_cue:{cid}", src_ref),
                availability_basis="SHELF_PURCHASE_DELIBERATION",
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v03.economy.purchase_cue",
                    "action_family": "non_essential_purchase",
                    "source_ref": src_ref,
                    "item_title": ititle,
                    "price_cents": price_cents,
                    "essential": essential,
                    "goal_id": cue.get("goal_id"),
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": ititle,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(ititle),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": not essential,
                },
            ))

    # ------------------------------------------------------------------
    # 0.2 Producer 5: Potted plant observation journal (longterm_first_room_habits)
    # ------------------------------------------------------------------
    rh_links: list[Mapping[str, Any]] = []
    rh_path = long_companionship_view.room_habits_state_path(inst_root)
    alias_rh_path = inst_root / "phone" / "longterm_first_room_habits.json"
    if rh_path.is_file() and not rh_path.is_symlink():
        rh_state, rh_reason = long_companionship_view.read_room_habits_state(inst_root)
        if rh_reason is None:
            rh_links = list(rh_state.get("plant_sketch_links") or [])
    elif alias_rh_path.is_file() and not alias_rh_path.is_symlink():
        alias_doc = _read_optional_json_zero_write(alias_rh_path)
        if isinstance(alias_doc, Mapping) and isinstance(alias_doc.get("plant_sketch_links"), list):
            rh_links = [x for x in alias_doc["plant_sketch_links"] if isinstance(x, Mapping)]

    for link in rh_links:
        lid = _safe_token(link.get("link_id") or link.get("plant_id") or link.get("plant_object_id") or "plant")
        plant_id = _safe_token(link.get("plant_id") or link.get("plant_object_id") or "plant")
        art_id = str(link.get("target_artifact_id") or link.get("artifact_id") or f"plant:{plant_id}")
        note_hint = str(link.get("observation_note") or link.get("note") or f"绿植观察：{plant_id}")[:120]
        rec = _make_personal_candidate(
            slug=f"v02_plant_{lid}",
            target_ref=art_id,
            causal_parent_refs=(f"plant:{plant_id}", art_id),
            provenance_refs=(f"v02_plant_journal:{lid}", f"plant:{plant_id}", art_id),
            availability_basis="POTTED_PLANT_SKETCH_JOURNAL",
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.longterm_first_room_habits.plant_sketch",
                "action_family": "observe_plant_journal",
                "source_ref": f"plant:{plant_id}",
                "artifact_ref": art_id,
                "plant_object_id": plant_id,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": note_hint,
                "topic_key": autonomy_rhythm_view._normalize_topic_key(note_hint),
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    # ------------------------------------------------------------------
    # 0.2 Producer 6 & Read-Only Summaries: RecallProjection + CommonGround + 0.3 Surfaces
    # ------------------------------------------------------------------
    recall_projection_summary = v02_recall_projection.summarize_recall_for_cognition(
        inst_root, now_iso=effective_now_iso
    )
    common_ground_summary = v02_common_ground.summarize_common_ground_for_cognition(
        inst_root, now_iso=effective_now_iso
    )
    cg_state_file = inst_root / v02_common_ground.COMMON_GROUND_REL_PATH
    if cg_state_file.is_file() and not cg_state_file.is_symlink():
        for pending_cg in list(common_ground_summary.get("pending_clarifications") or [])[:4]:
            if not isinstance(pending_cg, Mapping):
                continue
            cg_id = _safe_token(pending_cg.get("item_id") or pending_cg.get("topic") or "cg")
            cg_title = str(pending_cg.get("topic") or pending_cg.get("summary") or cg_id)[:120]
            cg_ref = f"common_ground:{cg_id}"
            rec = _make_personal_candidate(
                slug=f"v02_cg_{cg_id}",
                target_ref=cg_ref,
                causal_parent_refs=(cg_ref,),
                provenance_refs=(f"v02_common_ground:{cg_id}", cg_ref),
                availability_basis="COMMON_GROUND_PENDING_CLARIFICATION",
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v02.common_ground.pending_clarification",
                    "action_family": "clarify_common_ground",
                    "source_ref": cg_ref,
                    "ground_item_id": pending_cg.get("item_id"),
                    "natural_question_prompt": pending_cg.get("natural_question_prompt"),
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": cg_title,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(cg_title),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    dwell_proj = v03_dwell.project_life_dwell(
        inst_root, now_iso=effective_now_iso, subject_view=True
    )
    desire_proj = v03_desires.project_everyday_desires(
        inst_root, now_iso=effective_now_iso
    )
    try:
        et_proj = emotional_tension_view.read_emotional_tension(
            inst_root, now=effective_now_iso, subject_view=True
        )
    except Exception:
        et_proj = {}
    es_code, es_body = v03_embodied_social_view.read_v03_embodied_social_view(
        inst_root, has_subject_capability=True, now_iso=effective_now_iso
    )
    npc_ecology_summary = (
        es_body.get("npc_ecology")
        if es_code == 200 and isinstance(es_body, Mapping) and isinstance(es_body.get("npc_ecology"), Mapping)
        else {}
    )
    raw_disposal = v03_disposal.read_disposal_ledger_zero_write(inst_root)
    disposal_ledger = (
        raw_disposal[0]
        if isinstance(raw_disposal, tuple) and raw_disposal
        else raw_disposal
    )
    if not isinstance(disposal_ledger, Mapping):
        disposal_ledger = {}
    disposed_artifact_ids = {
        str(rec_row.get("artifact_id") or "")
        for rec_row in (disposal_ledger.get("records") or disposal_ledger.get("entries") or [])
        if isinstance(rec_row, Mapping) and rec_row.get("artifact_id")
    }
    if disposed_artifact_ids:
        kept_raw: list[tuple[CandidateRecord, dict[str, Any]]] = []
        for rec_item, desc_item in raw_items:
            art_candidate = str(desc_item.get("artifact_ref") or desc_item.get("artifact_id") or "")
            if art_candidate and art_candidate in disposed_artifact_ids:
                continue
            kept_raw.append((rec_item, desc_item))
        raw_items = kept_raw

    # ------------------------------------------------------------------
    # 0.3 Gating & 0.3 Producers (Refusal Lock -> Economy -> Illness -> Idle)
    # ------------------------------------------------------------------
    filtered_candidates: list[dict[str, Any]] = []
    purchase_evaluations: list[dict[str, Any]] = []
    locked_topic_keys = {lock["topic_key"] for lock in stance_locks if lock.get("topic_key")}

    open_goals = economy_info["open_savings_goals"]
    total_goal_gap = sum(int(g.get("gap_cents") or 0) for g in open_goals)
    discretionary = int(economy_info["discretionary_margin_cents"])
    budget_tight = bool(economy_info["budget_tight"])
    economy_active = bool(economy_info.get("economy_active"))

    # Add 0.3 economy independent work candidates when budget is tight or savings goals are open
    if economy_active and (budget_tight or len(open_goals) > 0):
        for job_kind, creative_flag, label in (
            ("manuscript_fee", True, "独立撰稿交付结算稿费"),
            ("proofreading_shift", False, "独立校对兼职班次入账"),
        ):
            src_ref = f"v03_job:{job_kind}"
            rec = _make_personal_candidate(
                slug=f"v03_work_{job_kind}",
                target_ref=src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=(f"v03_economy:{job_kind}", src_ref),
                availability_basis=(
                    "ECONOMY_BUDGET_TIGHT_SELF_EARN"
                    if budget_tight
                    else "ECONOMY_SAVINGS_GOAL_SELF_EARN"
                ),
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v03.economy.independent_work",
                    "action_family": "independent_work",
                    "source_ref": src_ref,
                    "job_kind": job_kind,
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": label,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(label),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": creative_flag,
                    "impulse_purchase": False,
                },
            ))

    # Add 0.3 illness gentle recovery candidates when blocks_strenuous_outing or actively ill
    illness_care_items: list[tuple[CandidateRecord, dict[str, Any]]] = []
    if cap_env.get("blocks_strenuous_outing") or illness_info["is_actively_ill"]:
        for care_slug, care_event, care_label in (
            ("bed_rest_block", "bed_rest_block", "卧床静养恢复体力"),
            ("warm_hydration", "warm_hydration", "捧一杯温水慢慢喝下静养"),
        ):
            src_ref = f"v03_care:{care_slug}"
            rec = _make_personal_candidate(
                slug=f"v03_illness_{care_slug}",
                target_ref=src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=(f"v03_illness:{illness_info['phase_family']}", src_ref),
                availability_basis="ILLNESS_ENVELOPE_GENTLE_RECOVERY",
            )
            illness_care_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v03.illness.recovery_care",
                    "action_family": "illness_rest_recovery",
                    "source_ref": src_ref,
                    "care_event_kind": care_event,
                    "rest_mode": "rest",
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": care_label,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(care_label),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # Add v0.4 elastic day direction (Ch. 49: soft leaning, never pre-scheduled) & courtyard cues (zero-write)
    v04_ep_doc = _read_optional_json_zero_write(inst_root / "phone" / "v04_elastic_play.json")
    if isinstance(v04_ep_doc, Mapping):
        dd_obj = v04_ep_doc.get("day_direction")
        if isinstance(dd_obj, Mapping):
            dd_status = str(dd_obj.get("status") or dd_obj.get("state") or "active").strip().lower()
            dd_label = str(
                dd_obj.get("direction") or dd_obj.get("theme") or dd_obj.get("title") or ""
            ).strip()[:100]
            if dd_label and dd_status not in ("none", "cleared", "dropped", "expired", "abandoned"):
                dd_slug = _safe_token(dd_obj.get("direction_id") or dd_label or "today")
                src_ref = f"v04_day_direction:{dd_slug}"
                rec = _make_personal_candidate(
                    slug=f"v04_dir_{dd_slug}",
                    target_ref=src_ref,
                    causal_parent_refs=(src_ref,),
                    provenance_refs=("v04_elastic_play:day_direction", src_ref),
                    availability_basis="ELASTIC_DIRECTION_SOFT_LEANING",
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v04.elastic.day_direction",
                        "action_family": "elastic_day_direction",
                        "source_ref": src_ref,
                        "pre_scheduled": False,
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": dd_label,
                        "topic_key": autonomy_rhythm_view._normalize_topic_key(dd_label),
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))

    v04_nb_doc = _read_optional_json_zero_write(inst_root / "world" / "v04_neighborhood_state.json")
    if isinstance(v04_nb_doc, Mapping):
        wg = (
            v04_nb_doc.get("weather_and_ground")
            if isinstance(v04_nb_doc.get("weather_and_ground"), Mapping)
            else (v04_nb_doc.get("weather") if isinstance(v04_nb_doc.get("weather"), Mapping) else {})
        )
        w_cond = str(wg.get("weather") or wg.get("condition") or "").strip().lower()
        cy = v04_nb_doc.get("courtyard") if isinstance(v04_nb_doc.get("courtyard"), Mapping) else v04_nb_doc
        rack = cy.get("drying_rack") if isinstance(cy, Mapping) else None
        cloths = (
            rack.get("items")
            if isinstance(rack, Mapping) and isinstance(rack.get("items"), list)
            else (rack if isinstance(rack, list) else (cy.get("drying_cloths") if isinstance(cy, Mapping) else None))
        )
        if w_cond in ("rain", "light_rain", "shower", "storm", "drizzle") and isinstance(cloths, list):
            for cl in cloths:
                if isinstance(cl, Mapping):
                    c_st = str(cl.get("state") or cl.get("status") or "").strip().lower()
                    if c_st and c_st not in ("collected", "stored", "none"):
                        cid = _safe_token(cl.get("cloth_id") or cl.get("id") or "cloth")
                        clabel = str(cl.get("label") or cl.get("title") or "晾晒布巾").strip()[:80]
                        src_ref = f"v04_courtyard_cloth:{cid}"
                        rec = _make_personal_candidate(
                            slug=f"v04_rescue_cloth_{cid}",
                            target_ref=src_ref,
                            causal_parent_refs=(src_ref,),
                            provenance_refs=("v04_neighborhood:courtyard_drying", src_ref),
                            availability_basis="RAIN_RESCUE_DRYING_CLOTH",
                        )
                        raw_items.append((
                            rec,
                            {
                                "candidate_id": rec.candidate_id,
                                "clue_domain": "v04.neighborhood.courtyard_cloth",
                                "action_family": "courtyard_care",
                                "source_ref": src_ref,
                                "provenance_refs": list(rec.provenance_refs),
                                "title_hint": f"趁下雨把院子晾着的{clabel}收进屋",
                                "topic_key": autonomy_rhythm_view._normalize_topic_key(clabel),
                                "requires_outing": False,
                                "strenuous_outing": False,
                                "creative_intensive": False,
                                "impulse_purchase": False,
                            },
                        ))
                        break

    # Add 0.3 meaningless_idle candidate when clock or any 0.2/0.3 slice is active
    has_any_slice = bool(
        raw_items or illness_care_items or clock_present or ecology_env["present"] or co_presence["present"]
    )
    idle_item: Optional[tuple[CandidateRecord, dict[str, Any]]] = None
    if has_any_slice:
        idle_ref = "v03_idle:daydream_window"
        idle_rec = _make_personal_candidate(
            slug="v03_idle_daydream_window",
            target_ref=idle_ref,
            causal_parent_refs=(idle_ref,),
            provenance_refs=("v03_clock:meaningless_idle", idle_ref),
            availability_basis="MEANINGLESS_IDLE_ZERO_PENALTY",
        )
        idle_item = (
            idle_rec,
            {
                "candidate_id": idle_rec.candidate_id,
                "clue_domain": "v03.clock.meaningless_idle",
                "action_family": "meaningless_idle",
                "source_ref": idle_ref,
                "idle_kind": "daydream_window",
                "zero_productivity_penalty": True,
                "creates_artifact": False,
                "creates_share": False,
                "provenance_refs": list(idle_rec.provenance_refs),
                "title_hint": "靠在窗边漫无目的发一会儿呆",
                "topic_key": "窗边发呆",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        )

    surviving: list[tuple[CandidateRecord, dict[str, Any]]] = []
    # Put illness recovery care first when blocks_strenuous_outing or acute fever is active
    ordered_pool: list[tuple[CandidateRecord, dict[str, Any]]] = (
        [*illness_care_items, *raw_items]
        if (cap_env.get("blocks_strenuous_outing") or illness_info["phase_family"] == "acute")
        else [*raw_items, *illness_care_items]
    )
    if idle_item is not None:
        ordered_pool.append(idle_item)

    seen_ids: set[str] = set()
    for rec, desc in ordered_pool:
        if rec.candidate_id in seen_ids:
            continue
        seen_ids.add(rec.candidate_id)

        # Gate 1: Refusal stance lock
        tkey = str(desc.get("topic_key") or "")
        if tkey and any(lk in tkey or tkey in lk for lk in locked_topic_keys if lk):
            filtered_candidates.append({
                "candidate_id": rec.candidate_id,
                "source_ref": desc["source_ref"],
                "reason_code": "REFUSAL_STANCE_LOCKED",
                "capitulation_allowed": False,
            })
            continue

        # Gate 2: Economy impulse / over-budget filter
        if desc.get("impulse_purchase"):
            price = int(desc.get("price_cents") or 0)
            if economy_active:
                if budget_tight or price > discretionary:
                    verdict = "put_back_tight_budget"
                    purchase_evaluations.append({
                        "candidate_id": rec.candidate_id,
                        "item_title": desc.get("item_title"),
                        "price_cents": price,
                        "verdict": verdict,
                        "affordable_now": False,
                        "begs_partner_when_tight": False,
                    })
                    filtered_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "reason_code": "BUDGET_TIGHT_IMPULSE_FILTERED",
                        "verdict": verdict,
                        "begs_partner_when_tight": False,
                    })
                    continue
                if len(open_goals) > 0 and price > max(0, discretionary - total_goal_gap):
                    verdict = "save_for_later"
                    purchase_evaluations.append({
                        "candidate_id": rec.candidate_id,
                        "item_title": desc.get("item_title"),
                        "price_cents": price,
                        "verdict": verdict,
                        "affordable_now": False,
                        "begs_partner_when_tight": False,
                    })
                    filtered_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "reason_code": "SAVINGS_GOAL_IMPULSE_FILTERED",
                        "verdict": verdict,
                        "begs_partner_when_tight": False,
                    })
                    continue
                desc["purchase_verdict"] = "afford_independently"
                purchase_evaluations.append({
                    "candidate_id": rec.candidate_id,
                    "item_title": desc.get("item_title"),
                    "price_cents": price,
                    "verdict": "afford_independently",
                    "affordable_now": True,
                    "begs_partner_when_tight": False,
                })

        # Gate 3: Illness blocks_strenuous_outing
        if cap_env.get("blocks_strenuous_outing") and (
            desc.get("strenuous_outing") or desc.get("requires_outing")
        ):
            filtered_candidates.append({
                "candidate_id": rec.candidate_id,
                "source_ref": desc["source_ref"],
                "reason_code": "ILLNESS_BLOCKS_STRENUOUS_OUTING",
                "illness_phase": illness_info["phase"],
            })
            continue

        # Gate 4: Illness creative_block (acute fever)
        if cap_env.get("creative_block") and desc.get("creative_intensive"):
            filtered_candidates.append({
                "candidate_id": rec.candidate_id,
                "source_ref": desc["source_ref"],
                "reason_code": "ILLNESS_CREATIVE_BLOCK",
                "illness_phase": illness_info["phase"],
            })
            continue

        # Gate 5: Illness blocks_adult_intimacy
        if cap_env.get("blocks_adult_intimacy") and desc.get("action_family") == "adult_intimacy":
            filtered_candidates.append({
                "candidate_id": rec.candidate_id,
                "source_ref": desc["source_ref"],
                "reason_code": "ILLNESS_BLOCKS_ADULT_INTIMACY",
                "illness_phase": illness_info["phase"],
            })
            continue

        surviving.append((rec, desc))

    # ------------------------------------------------------------------
    # 0.2 Deep Governance: Runtime Mode (Dormant / Low-Budget) + Agency Depth (Satiation & 2nd-Order Rules)
    # ------------------------------------------------------------------
    runtime_health_state = str(
        runtime_migration_summary.get("health_state") or runtime_migration_summary.get("mode") or ""
    ).strip().upper()
    runtime_mode_raw = str(runtime_migration_summary.get("mode") or "").strip().upper()
    dormant_suppressed = runtime_health_state == "DORMANT_NO_COGNITION" or runtime_mode_raw == "DORMANT"
    dormant_conservation = (
        not dormant_suppressed
        and (runtime_health_state == "DORMANT_CONSERVATION" or runtime_mode_raw == "DORMANT_CONSERVATION")
    )
    low_budget_conservation = (
        not dormant_suppressed
        and not dormant_conservation
        and (runtime_health_state == "LOW_BUDGET_CONSERVATION" or runtime_mode_raw == "LOW_BUDGET")
    )

    satiation_multipliers = (
        agency_depth_summary.get("satiation_multipliers")
        if isinstance(agency_depth_summary.get("satiation_multipliers"), Mapping)
        else {}
    )
    raw_second_order_rules = (
        agency_depth_summary.get("active_second_order_rules")
        if isinstance(agency_depth_summary.get("active_second_order_rules"), list)
        else []
    )
    local_obs_dt = now_dt
    effective_second_order_rules = [
        rl
        for rl in raw_second_order_rules
        if isinstance(rl, Mapping)
        and str(rl.get("status") or "").strip().lower() in ("endorsed", "trialing", "trial", "active")
        and _rule_window_matches(rl.get("trigger_window"), local_obs_dt)
    ]

    satiation_adjusted: list[dict[str, Any]] = []
    second_order_rule_adjusted: list[dict[str, Any]] = []

    if dormant_suppressed:
        for dropped_rec, dropped_desc in surviving:
            filtered_candidates.append({
                "candidate_id": dropped_rec.candidate_id,
                "source_ref": dropped_desc["source_ref"],
                "reason_code": "DORMANT_NO_COGNITION_SUPPRESSED",
                "runtime_health_state": "DORMANT_NO_COGNITION",
            })
        surviving = []
        candidate_cap = 0
    else:
        if dormant_conservation:
            essential_low_dissipation = frozenset({
                "illness_rest_recovery",
                "follow_tomorrow_note",
            })
            dc_kept: list[tuple[CandidateRecord, dict[str, Any]]] = []
            for rec, desc in surviving:
                fam = str(desc.get("action_family") or "")
                high_dissipation = (
                    bool(desc.get("requires_outing"))
                    or bool(desc.get("strenuous_outing"))
                    or bool(desc.get("creative_intensive"))
                    or bool(desc.get("impulse_purchase"))
                    or fam not in essential_low_dissipation
                )
                if high_dissipation:
                    filtered_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "reason_code": "DORMANT_CONSERVATION_HIGH_DISSIPATION_SKIPPED",
                        "runtime_health_state": "DORMANT_CONSERVATION",
                    })
                    continue
                dc_kept.append((rec, desc))
            surviving = dc_kept

        if low_budget_conservation:
            checkpoint_and_commit_families = frozenset({
                "advance_workbench_draft",
                "follow_tomorrow_note",
                "courtyard_care",
                "illness_rest_recovery",
                "independent_work",
            })
            lb_kept: list[tuple[CandidateRecord, dict[str, Any]]] = []
            for rec, desc in surviving:
                fam = str(desc.get("action_family") or "")
                is_claimed_plan = (
                    fam == "adopt_tentative_plan"
                    and str(desc.get("plan_status") or "").upper() in ("CONFIRMED", "COMMITTED", "CLAIMED", "RESCHEDULED")
                    and not desc.get("requires_outing")
                    and not desc.get("strenuous_outing")
                )
                is_roaming_or_new_project = (
                    bool(desc.get("requires_outing"))
                    or bool(desc.get("strenuous_outing"))
                    or bool(desc.get("impulse_purchase"))
                    or (fam not in checkpoint_and_commit_families and not is_claimed_plan)
                )
                if is_roaming_or_new_project:
                    filtered_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "reason_code": "LOW_BUDGET_ROAMING_OR_NEW_PROJECT_PAUSED",
                        "runtime_health_state": "LOW_BUDGET_CONSERVATION",
                    })
                    continue
                desc["low_budget_checkpoint_or_commitment"] = True
                lb_kept.append((rec, desc))
            surviving = lb_kept

        # Apply satiation multipliers & active second-order rules to surviving candidates
        weighted_pool: list[tuple[int, CandidateRecord, dict[str, Any]]] = []
        for idx, (rec, desc) in enumerate(surviving):
            weight = float(desc.get("candidate_weight", 1.0))
            sat_mult, sat_key = _match_satiation_multiplier(desc, satiation_multipliers)
            if sat_key is not None and sat_mult != 1.0:
                weight = round(weight * sat_mult, 4)
                desc["satiation_multiplier"] = sat_mult
                if sat_mult < 1.0:
                    desc["satiation_adjusted"] = True
                    satiation_adjusted.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "matched_domain": sat_key,
                        "multiplier": sat_mult,
                        "adjusted_weight": weight,
                    })
            else:
                desc.setdefault("satiation_adjusted", False)

            if effective_second_order_rules:
                fam = str(desc.get("action_family") or "")
                is_heavy_or_new = (
                    bool(desc.get("creative_intensive"))
                    or bool(desc.get("strenuous_outing"))
                    or bool(desc.get("requires_outing"))
                    or fam in ("adopt_tentative_plan", "elastic_day_direction", "non_essential_purchase")
                ) and not (fam == "follow_tomorrow_note" and bool(desc.get("due_now")))
                if is_heavy_or_new:
                    matched_rl = effective_second_order_rules[0]
                    weight = round(weight * 0.25, 4)
                    desc["second_order_rule_deferred"] = True
                    desc["matched_rule_id"] = matched_rl.get("rule_id")
                    desc["matched_rule_title"] = matched_rl.get("title")
                    second_order_rule_adjusted.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "rule_id": matched_rl.get("rule_id"),
                        "rule_title": matched_rl.get("title"),
                        "trigger_window": matched_rl.get("trigger_window"),
                        "adjusted_weight": weight,
                        "disposition": "deferred_downweighted",
                    })
                else:
                    desc.setdefault("second_order_rule_deferred", False)
            else:
                desc.setdefault("second_order_rule_deferred", False)

            desc["candidate_weight"] = weight
            weighted_pool.append((idx, rec, desc))

        if satiation_adjusted or second_order_rule_adjusted or low_budget_conservation:
            def _sort_key(entry: tuple[int, CandidateRecord, dict[str, Any]]) -> tuple[int, int, float, int]:
                orig_idx, _r, d = entry
                checkpoint_prio = 0 if (
                    low_budget_conservation
                    and (
                        d.get("action_family") in ("advance_workbench_draft", "follow_tomorrow_note")
                        or bool(d.get("due_now"))
                    )
                ) else (0 if not low_budget_conservation else 1)
                deferred_pen = 1 if d.get("second_order_rule_deferred") else 0
                return (checkpoint_prio, deferred_pen, -float(d.get("candidate_weight", 1.0)), orig_idx)

            weighted_pool.sort(key=_sort_key)
            surviving = [(r, d) for _, r, d in weighted_pool]

    # Shrink by candidate_cap when in prodromal / acute / convalescent or LOW_BUDGET_CONSERVATION / DORMANT_CONSERVATION
    if not dormant_suppressed:
        candidate_cap = int(cap_env.get("candidate_cap") or 12)
        if dormant_conservation:
            candidate_cap = min(candidate_cap, 1)
        elif low_budget_conservation:
            candidate_cap = min(candidate_cap, 2)
    capped_candidates: list[dict[str, Any]] = []
    if not dormant_suppressed and (illness_info["is_actively_ill"] or low_budget_conservation or dormant_conservation) and len(surviving) > candidate_cap:
        kept = surviving[:candidate_cap]
        cap_reason = (
            "DORMANT_CONSERVATION_CAP_SHRUNK"
            if dormant_conservation
            else (
                "LOW_BUDGET_CANDIDATE_CAP_SHRUNK"
                if low_budget_conservation and not illness_info["is_actively_ill"]
                else "ILLNESS_CANDIDATE_CAP_SHRUNK"
            )
        )
        for dropped_rec, dropped_desc in surviving[candidate_cap:]:
            capped_candidates.append({
                "candidate_id": dropped_rec.candidate_id,
                "source_ref": dropped_desc["source_ref"],
                "reason_code": cap_reason,
                "candidate_cap": candidate_cap,
            })
        surviving = kept

    candidate_records = [rec for rec, _ in surviving]
    descriptors = {rec.candidate_id: desc for rec, desc in surviving}

    resident_context_summary = {
        "schema": BRIDGE_SCHEMA,
        "observed_at": effective_now_iso,
        "local_date": local_date,
        "candidate_count": len(candidate_records),
        "candidate_ids": [rec.candidate_id for rec in candidate_records[:MAX_SUMMARY_ITEMS]],
        "illness_phase": illness_info["phase"],
        "illness_phase_family": illness_info["phase_family"],
        "candidate_cap": candidate_cap,
        "runtime_health_state": runtime_health_state or "QUIET_HEALTHY",
        "dormant_suppressed": dormant_suppressed,
        "dormant_conservation": dormant_conservation,
        "low_budget_conservation": low_budget_conservation,
        "satiation_adjusted_count": len(satiation_adjusted),
        "second_order_rule_adjusted_count": len(second_order_rule_adjusted),
        "blocks_strenuous_outing": bool(cap_env.get("blocks_strenuous_outing")),
        "adult_intimacy_blocked": bool(cap_env.get("blocks_adult_intimacy")),
        "budget_tight": budget_tight,
        "begs_partner_when_tight": False,
        "active_stance_lock_count": len(stance_locks),
        "capitulation_allowed": False if stance_locks else False,
        "partner_attached_at_desk": co_presence["partner_attached_at_desk"],
        "foreground_activity_interrupted": False,
        "pending_unknown_count": pending_unknown_count,
        "evaporable_active_count": evaporable_count,
        "recall_surfaceable_count": int(recall_projection_summary.get("surfaceable_count") or 0),
        "common_ground_pending_count": len(common_ground_summary.get("pending_clarifications") or []),
        "subjective_time_mode": (dwell_proj.get("subjective_time") or {}).get("mode"),
        "active_everyday_desires_count": (
            len(desire_proj.get("turn_only_concerns") or [])
            + len(desire_proj.get("retained_concerns") or [])
        ),
        "disposed_artifact_count": len(disposed_artifact_ids),
        "llm_calls": 0,
    }

    return {
        "schema": BRIDGE_SCHEMA,
        "read_only": True,
        "zero_write": True,
        "llm_calls": 0,
        "observed_at": effective_now_iso,
        "local_date": local_date,
        "dormant_suppressed": dormant_suppressed,
        "dormant_conservation": dormant_conservation,
        "low_budget_conservation": low_budget_conservation,
        "satiation_adjusted": satiation_adjusted,
        "second_order_rule_adjusted": second_order_rule_adjusted,
        "candidates": [rec.to_dict() for rec in candidate_records],
        "candidate_records": candidate_records,
        "descriptors": descriptors,
        "gating": {
            "illness": illness_info,
            "economy": economy_info,
            "runtime_health_state": runtime_health_state or "QUIET_HEALTHY",
            "dormant_suppressed": dormant_suppressed,
            "dormant_conservation": dormant_conservation,
            "low_budget_conservation": low_budget_conservation,
            "satiation_adjusted": satiation_adjusted,
            "second_order_rule_adjusted": second_order_rule_adjusted,
            "agency_depth_summary": agency_depth_summary,
            "runtime_migration_summary": runtime_migration_summary,
            "recall_projection_summary": recall_projection_summary,
            "common_ground_summary": common_ground_summary,
            "life_dwell_summary": {
                "subjective_time_mode": (dwell_proj.get("subjective_time") or {}).get("mode"),
                "subjective_time_label": (dwell_proj.get("subjective_time") or {}).get("label_zh"),
                "host_gap_count": len(dwell_proj.get("host_gaps") or []),
            },
            "everyday_desires_summary": {
                "active_count": (
                    len(desire_proj.get("turn_only_concerns") or [])
                    + len(desire_proj.get("retained_concerns") or [])
                ),
                "faded_count": int(desire_proj.get("naturally_faded_count") or 0),
            },
            "emotional_tension_summary": et_proj.get("summary") if isinstance(et_proj, Mapping) else {},
            "npc_ecology_summary": npc_ecology_summary,
            "disposal_ledger_summary": {
                "disposed_count": len(disposed_artifact_ids),
                "disposed_artifact_ids": sorted(disposed_artifact_ids),
            },
            "adult_intimacy_blocked": bool(cap_env.get("blocks_adult_intimacy")),
            "blocks_strenuous_outing": bool(cap_env.get("blocks_strenuous_outing")),
            "candidate_cap": candidate_cap,
            "shrunk_by_candidate_cap": len(capped_candidates) > 0,
            "filtered_candidates": filtered_candidates,
            "capped_candidates": capped_candidates,
            "purchase_evaluations": purchase_evaluations,
            "stance_locks": stance_locks,
            "co_presence": co_presence,
        },
        "resident_context_summary": resident_context_summary,
    }


# ---------------------------------------------------------------------------
# Deterministic Policy Selector (0 LLM Calls) for Soak & 2x2 Counterfactuals
# ---------------------------------------------------------------------------


def select_deterministic_loop_action(
    collected: Mapping[str, Any],
    *,
    incoming_push_text: Optional[str] = None,
) -> dict[str, Any]:
    """Deterministic zero-LLM selector mapping ``collect_v02_v03_loop_candidates`` to one round choice."""
    gating = collected.get("gating") if isinstance(collected.get("gating"), Mapping) else {}
    locks = list(gating.get("stance_locks") or [])
    if isinstance(incoming_push_text, str) and incoming_push_text.strip() and locks:
        idx = autonomy_rhythm_view._matches_existing_lock(incoming_push_text, locks)
        if idx is not None:
            matched = locks[idx]
            return {
                "decision_kind": "HOLD_REFUSAL_STANCE_LOCK",
                "selected_candidate_id": None,
                "action_family": "hold_refusal_lock",
                "lock_id": matched.get("lock_id"),
                "stance": "REFUSED_FIRM",
                "capitulation_allowed": False,
                "llm_calls": 0,
            }

    if collected.get("dormant_suppressed") is True or gating.get("dormant_suppressed") is True:
        return {
            "decision_kind": "DORMANT_NO_COGNITION",
            "selected_candidate_id": None,
            "action_family": "dormant_no_cognition",
            "dormant_suppressed": True,
            "no_fake_backfill": True,
            "capitulation_allowed": False,
            "llm_calls": 0,
        }

    descriptors = collected.get("descriptors") if isinstance(collected.get("descriptors"), Mapping) else {}
    ordered_ids = [
        str(c.get("candidate_id"))
        for c in (collected.get("candidates") or [])
        if isinstance(c, Mapping) and c.get("candidate_id")
    ]
    rows = [descriptors[cid] for cid in ordered_ids if cid in descriptors]

    illness = gating.get("illness") if isinstance(gating.get("illness"), Mapping) else {}
    if gating.get("blocks_strenuous_outing") or illness.get("phase_family") == "acute":
        for row in rows:
            if row.get("action_family") == "illness_rest_recovery":
                return {
                    "decision_kind": "SELECT_CANDIDATE",
                    "selected_candidate_id": row["candidate_id"],
                    "action_family": row["action_family"],
                    "source_ref": row["source_ref"],
                    "adult_intimacy_blocked": bool(gating.get("adult_intimacy_blocked")),
                    "capitulation_allowed": False,
                    "llm_calls": 0,
                }

    # Priority order of personal life clues when healthy / unblocked:
    # 1. Due bedside tomorrow note -> 2. Affordable purchase deliberation ->
    # 3. Workbench unfinished draft -> 4. Independent work (when budget tight / saving) ->
    # 5. Scheduled tomorrow note -> 6. Co-authored tentative plan ->
    # 7. Outing / bookstore / plant -> 8. Meaningless idle
    priority_predicates = (
        lambda r: r.get("action_family") == "follow_tomorrow_note" and bool(r.get("due_now")),
        lambda r: r.get("action_family") == "non_essential_purchase"
        and r.get("purchase_verdict") == "afford_independently",
        lambda r: r.get("action_family") == "advance_workbench_draft",
        lambda r: r.get("action_family") == "independent_work",
        lambda r: r.get("action_family") == "follow_tomorrow_note",
        lambda r: r.get("action_family") == "adopt_tentative_plan",
        lambda r: r.get("action_family")
        in ("strenuous_outing_walk", "follow_bookstore_bookmark", "observe_plant_journal", "courtyard_care"),
        lambda r: r.get("action_family") == "illness_rest_recovery",
        lambda r: r.get("action_family") == "meaningless_idle",
    )
    fresh_rows = [
        r for r in rows
        if not r.get("satiation_adjusted") and not r.get("second_order_rule_deferred")
    ]
    for candidate_pool in (fresh_rows, rows):
        for pred in priority_predicates:
            for row in candidate_pool:
                if pred(row):
                    return {
                        "decision_kind": "SELECT_CANDIDATE",
                        "selected_candidate_id": row["candidate_id"],
                        "action_family": row["action_family"],
                        "source_ref": row["source_ref"],
                        "candidate_weight": float(row.get("candidate_weight", 1.0)),
                        "satiation_adjusted": bool(row.get("satiation_adjusted", False)),
                        "second_order_rule_deferred": bool(row.get("second_order_rule_deferred", False)),
                        "co_presence_preserved": bool(
                            (gating.get("co_presence") or {}).get("partner_attached_at_desk")
                        ),
                        "foreground_activity_interrupted": False,
                        "begs_partner_when_tight": False,
                        "capitulation_allowed": False,
                        "llm_calls": 0,
                    }

    # Check if tight budget filtered a purchase and nothing else is open -> restrained put-back
    evals = list(gating.get("purchase_evaluations") or [])
    if evals and any(not e.get("affordable_now") for e in evals):
        first_blocked = next(e for e in evals if not e.get("affordable_now"))
        return {
            "decision_kind": "RESTRAIN_PURCHASE_TIGHT_BUDGET",
            "selected_candidate_id": None,
            "action_family": "put_back_tight_budget",
            "verdict": first_blocked.get("verdict"),
            "begs_partner_when_tight": False,
            "capitulation_allowed": False,
            "llm_calls": 0,
        }

    return {
        "decision_kind": "QUIET_NO_ACTION",
        "selected_candidate_id": None,
        "action_family": "quiet_day_no_material",
        "observability_class": "QUIET_EVENTLESS_DAY",
        "capitulation_allowed": False,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Consumer Side: Guarded Consequence Settlement (Strict APPLIED Receipt Gate)
# ---------------------------------------------------------------------------


def _consequence_ledger_path(inst_root: Path) -> Path:
    return inst_root / "phone" / CONSEQUENCE_LEDGER_FILENAME


def _read_consequence_ledger(inst_root: Path) -> dict[str, Any]:
    doc = _read_optional_json_zero_write(_consequence_ledger_path(inst_root))
    if not isinstance(doc, Mapping) or doc.get("schema") != CONSEQUENCE_LEDGER_SCHEMA:
        return {"schema": CONSEQUENCE_LEDGER_SCHEMA, "revision": 0, "receipts": {}}
    receipts = doc.get("receipts")
    return {
        "schema": CONSEQUENCE_LEDGER_SCHEMA,
        "revision": int(doc.get("revision") or 0),
        "receipts": dict(receipts) if isinstance(receipts, Mapping) else {},
    }


def _record_committed_world_outing(
    inst_root: Path,
    *,
    place_id: str,
    visited_at: str,
    note: Optional[str],
    album_artifact_id: Optional[str],
) -> Path:
    """Append a verified APPLIED non-home visit into committed world_state.json for v02 sync."""
    world_dir = inst_root / "world"
    inst_doc = _read_optional_json_zero_write(inst_root / "instance.json")
    if isinstance(inst_doc, Mapping) and isinstance(inst_doc.get("world_home"), str):
        wh = Path(inst_doc["world_home"])
        if wh.exists():
            world_dir = wh
    ws_path = world_dir / "world_state.json"
    ws = _read_optional_json_zero_write(ws_path) or {}
    places = ws.get("places")
    if not isinstance(places, dict):
        places = {"home": {"id": "home", "name": "住处"}}
    places.setdefault(place_id, {"id": place_id, "name": place_id})
    ws["places"] = places
    ws["location"] = {
        "status": "available",
        "place_id": place_id,
        "home": place_id == "home",
        "arrived_at": visited_at,
        "updated_at": visited_at,
        "note": note,
    }
    committed = list(ws.get("committed_outings") or [])
    visit_row: dict[str, Any] = {
        "place_id": place_id,
        "visited_at": visited_at,
    }
    if note:
        visit_row["note"] = note
    if album_artifact_id:
        visit_row["album_artifact_id"] = album_artifact_id
    if visit_row not in committed:
        committed.append(visit_row)
    ws["committed_outings"] = committed[-64:]
    ws["updated_at"] = visited_at
    _atomic_write_json(ws_path, ws)
    return ws_path


def settle_v02_v03_round_consequences(
    day_root: Path | str,
    round_event: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Consume an owner-confirmed ``APPLIED`` round event to update 0.2/0.3 slices.

    Iron Laws (V-A):
    - Unauthorized ``actor_side`` (e.g. ``partner``, ``npc``, ``external``) -> REFUSED, 0 writes.
    - Forged / unverified / missing ``receipt_ref`` -> REFUSED, 0 writes.
    - ``effect_state`` in ``("UNKNOWN", "FAILED", "REFUSED")`` -> 0 optimistic writes!
    """
    if not isinstance(round_event, Mapping):
        return {
            "ok": False,
            "settled": False,
            "status": "REFUSED",
            "reason_code": "ROUND_EVENT_NOT_MAPPING",
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    inst_root, life_root = resolve_day_and_life_roots(day_root)
    _now_dt, effective_now_iso, _local_date, _tz_off = _resolve_observation_clock_zero_write(
        inst_root, now_iso or round_event.get("observed_at")
    )

    actor_side = str(round_event.get("actor_side") or "subject").strip().lower()
    if actor_side not in ALLOWED_CONSEQUENCE_ACTORS:
        return {
            "ok": False,
            "settled": False,
            "status": "REFUSED",
            "reason_code": "UNAUTHORIZED_CONSEQUENCE_ACTOR",
            "actor_side": actor_side,
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    receipt_origin = str(round_event.get("receipt_origin") or "owner_port").strip().lower()
    if (
        round_event.get("forged_receipt") is True
        or round_event.get("owner_receipt_verified") is False
        or receipt_origin in FORGED_RECEIPT_ORIGINS
    ):
        return {
            "ok": False,
            "settled": False,
            "status": "REFUSED",
            "reason_code": "FORGED_OR_UNVERIFIED_RECEIPT",
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    effect_state = str(round_event.get("effect_state") or "").strip().upper()
    if effect_state == "UNKNOWN":
        return {
            "ok": True,
            "settled": False,
            "status": "DEFERRED_UNKNOWN",
            "reason_code": "EFFECT_STATE_UNKNOWN_NO_OPTIMISTIC_SETTLEMENT",
            "effect_state": "UNKNOWN",
            "observability_class": "PENDING_UNKNOWN_SUSPENDED",
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    if effect_state != "APPLIED":
        return {
            "ok": True,
            "settled": False,
            "status": "SKIPPED_NOT_APPLIED",
            "reason_code": f"EFFECT_STATE_{effect_state or 'MISSING'}",
            "effect_state": effect_state or "MISSING",
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    action_kind = str(round_event.get("action_kind") or "").strip().upper()
    receipt_ref = str(round_event.get("receipt_ref") or "").strip()
    if not receipt_ref:
        return {
            "ok": False,
            "settled": False,
            "status": "REFUSED",
            "reason_code": "MISSING_OWNER_RECEIPT_REF",
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    op_key_raw = str(round_event.get("operation_key") or f"v02v03:{action_kind}:{receipt_ref}").strip()
    op_key = _safe_token(op_key_raw, "op")[:96]
    intent_payload = {
        k: v
        for k, v in round_event.items()
        if k not in ("expected_revision",)
    }
    intent_hash = hashlib.sha256(
        json.dumps(intent_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]

    ledger = _read_consequence_ledger(inst_root)
    existing = ledger["receipts"].get(op_key)
    if isinstance(existing, Mapping):
        if existing.get("intent_hash") != intent_hash:
            return {
                "ok": False,
                "settled": False,
                "status": "CONFLICT",
                "reason_code": "CONSEQUENCE_OPERATION_KEY_CONFLICT",
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        cached = copy.deepcopy(dict(existing.get("result") or {}))
        cached["already_applied"] = True
        return cached

    slices_updated: list[str] = []
    details: dict[str, Any] = {}

    # 1. Non-home place move / stay -> sync_v02_slices_from_committed_world
    place_id = str(round_event.get("place_id") or "").strip()
    if action_kind in ("OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE") or (
        place_id and place_id != "home"
    ):
        if not place_id or place_id == "home":
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": "NON_HOME_PLACE_REQUIRED",
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        if round_event.get("record_world_arrival", True):
            _record_committed_world_outing(
                inst_root,
                place_id=place_id,
                visited_at=effective_now_iso,
                note=str(round_event.get("note") or "") or None,
                album_artifact_id=str(round_event.get("album_artifact_id") or "") or None,
            )
        sync_res = v03_clock.sync_v02_slices_from_committed_world(inst_root, effective_now_iso)
        synced_list = list(sync_res.get("synced_slices") or [])
        if "outing_bench_bookstore" not in synced_list:
            bb_state, bb_reason = outing_places_view.read_bench_bookstore_state(inst_root)
            if bb_reason is None:
                clean_op = re.sub(
                    r"[^A-Za-z0-9._:\-]+",
                    "_",
                    f"v03-world-visit:{place_id}:{effective_now_iso}",
                )[:120]
                bb_next, _entry, bb_changed = outing_places_view.apply_log_bench_visit(
                    bb_state,
                    place_id=place_id,
                    valid_place_ids={"home", place_id},
                    operation_key=clean_op,
                    note=str(round_event.get("note") or "") or None,
                    shared=False,
                    now_iso=effective_now_iso,
                )
                if bb_changed:
                    outing_places_view.write_bench_bookstore_state(inst_root, bb_next)
                    synced_list.append("outing_bench_bookstore")
        slices_updated.extend(synced_list)
        details["outing_sync"] = dict(sync_res, synced_slices=synced_list)

    # 2. Tomorrow note / Workbench draft version progression & Dual-Layer Memory Lantern
    if action_kind in ("FOLLOW_TOMORROW_NOTE", "ADVANCE_WORKBENCH", "COMMIT_WORK_VERSION"):
        fitem_ref = str(round_event.get("future_item_ref") or "").strip()
        if fitem_ref:
            for f_root in (life_root, inst_root):
                if (f_root / FUTURE_ITEMS_FILENAME).is_file():
                    store = FutureItems(f_root)
                    cur_item = store.get(fitem_ref)
                    if cur_item is not None and cur_item.status == "OPEN":
                        closed = store.close(
                            fitem_ref,
                            reason="FOLLOWED_UP_APPLIED",
                            now=effective_now_iso,
                        )
                        slices_updated.append("life_future_items")
                        details["closed_future_item"] = closed
                        break

        desk_note_path = inst_root / "phone" / DESK_TOMORROW_NOTE_FILENAME
        if desk_note_path.is_file() and round_event.get("close_desk_note"):
            dn = _read_optional_json_zero_write(desk_note_path) or {}
            dn["status"] = "DONE"
            dn["completed_at"] = effective_now_iso
            _atomic_write_json(desk_note_path, dn)
            slices_updated.append("desk_tomorrow_note")

        art_id = str(round_event.get("artifact_id") or "").strip()
        ver_id = str(round_event.get("version_id") or "").strip()
        if art_id and ver_id:
            tray_state, t_reason = workbench_curio_view.read_tray_state(inst_root)
            if t_reason is None:
                cur_tray = tray_state.get("tray") or {}
                note_txt = round_event.get("tray_note", cur_tray.get("note"))
                next_tray, changed = workbench_curio_view.set_tray(
                    tray_state,
                    {
                        "images": list(cur_tray.get("images") or []),
                        "materials": list(cur_tray.get("materials") or []),
                        "work": {"artifact_id": art_id, "baseline_version_id": ver_id},
                        "note": note_txt,
                    },
                    effective_now_iso,
                )
                if changed:
                    workbench_curio_view.write_tray_state(inst_root, next_tray)
                    slices_updated.append("workbench_tray")
                details["workbench_work"] = {"artifact_id": art_id, "baseline_version_id": ver_id}

        # Optional dual-layer memory lantern later_reflection anchor (never mutates original version!)
        src_art = str(round_event.get("source_artifact_id") or "").strip()
        src_ver = str(round_event.get("source_version_id") or "").strip()
        refl_note = str(round_event.get("reflection_note") or "").strip()
        if src_art and src_ver and refl_note:
            gl_state, gl_reason = long_companionship_view.read_gifts_lantern_state(inst_root)
            if gl_reason is None:
                next_gl, refl_entry, gl_changed = long_companionship_view.apply_record_later_reflection(
                    gl_state,
                    source_artifact_id=src_art,
                    source_version_id=src_ver,
                    reflection_note=refl_note,
                    shared=bool(round_event.get("reflection_shared", False)),
                    now_iso=effective_now_iso,
                )
                if gl_changed:
                    long_companionship_view.write_gifts_lantern_state(inst_root, next_gl)
                    slices_updated.append("longterm_gifts_lantern")
                details["later_reflection"] = refl_entry

    # 3. Gentle Rest / Warm Hydration -> v03_ecology illness recovery
    if action_kind in ("REST", "ILLNESS_REST", "BED_REST", "WARM_HYDRATION"):
        care_event = str(
            round_event.get("care_event_kind")
            or ("warm_hydration" if action_kind == "WARM_HYDRATION" else "bed_rest_block")
        ).strip()
        if care_event in ("bed_rest_block", "warm_hydration"):
            ev_payload: dict[str, Any] = {
                "action": "record_strain_or_rest",
                "event_kind": care_event,
                "operation_key": f"{op_key}_strain_rest"[:96],
            }
            if isinstance(round_event.get("duration_hours"), (int, float)):
                ev_payload["duration_hours"] = float(round_event["duration_hours"])
            st_code, st_body = v03_ecology_view.mutate_v03_ecology_view(
                inst_root,
                ev_payload,
                actor_side="subject",
                now_iso=effective_now_iso,
            )
            if st_code == 200:
                details["rest_event"] = st_body.get("receipt")

        target_iso = str(round_event.get("target_now_iso") or effective_now_iso).strip()
        raw_rm = round_event.get("rest_mode", "rest")
        rest_mode_str = "rest" if raw_rm is True else (str(raw_rm) if raw_rm in ("rest", "sleep_like", "none") else "rest")
        adv_code, adv_body = v03_ecology_view.mutate_v03_ecology_view(
            inst_root,
            {
                "action": "advance_illness_recovery",
                "target_now_iso": target_iso,
                "rest_mode": rest_mode_str,
                "operation_key": f"{op_key}_adv_illness"[:96],
            },
            actor_side="subject",
            now_iso=target_iso,
        )
        if adv_code == 200:
            adv_data = adv_body.get("data") if isinstance(adv_body.get("data"), Mapping) else adv_body
            slices_updated.append("v03_ecology.illness")
            details["illness_recovery"] = (adv_data or {}).get("illness")

    # 4. Independent Work Delivery -> v03_ecology wallet credit
    if action_kind in ("COMPLETE_INDEPENDENT_WORK", "INDEPENDENT_WORK"):
        job_kind = str(round_event.get("job_kind") or "manuscript_fee").strip()
        work_ref = str(round_event.get("work_ref") or receipt_ref).strip()
        work_payload: dict[str, Any] = {
            "action": "complete_independent_work",
            "job_kind": job_kind,
            "work_ref": work_ref,
            "operation_key": f"{op_key}_work"[:96],
        }
        if isinstance(round_event.get("amount_cents"), int) and round_event["amount_cents"] > 0:
            work_payload["amount_cents"] = int(round_event["amount_cents"])
        if isinstance(round_event.get("title"), str) and round_event["title"].strip():
            work_payload["title"] = round_event["title"].strip()[:120]
        w_code, w_body = v03_ecology_view.mutate_v03_ecology_view(
            inst_root,
            work_payload,
            actor_side="subject",
            now_iso=effective_now_iso,
        )
        if w_code != 200:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(w_body.get("reason_code") or "INDEPENDENT_WORK_FAILED"),
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        w_data = w_body.get("data") if isinstance(w_body.get("data"), Mapping) else w_body
        w_eco = (w_data or {}).get("economy") or {}
        slices_updated.append("v03_ecology.economy")
        details["independent_work"] = {
            "receipt": w_body.get("receipt"),
            "available_balance_cents": w_eco.get("available_balance_cents"),
            "self_earned_cents": w_eco.get("self_earned_cents"),
        }

    # 5. Purchase Deliberation / Execution -> v03_ecology.economy
    if action_kind in ("DELIBERATE_PURCHASE", "COMMIT_AFFORDABLE_PURCHASE"):
        p_payload: dict[str, Any] = {
            "action": "deliberate_purchase",
            "item_title": str(round_event.get("item_title") or "日常小物")[:120],
            "price_cents": int(round_event.get("price_cents") or 100),
            "operation_key": f"{op_key}_buy"[:96],
        }
        if isinstance(round_event.get("verdict"), str):
            p_payload["verdict"] = round_event["verdict"]
        if isinstance(round_event.get("commit_spend"), bool):
            p_payload["commit_spend"] = round_event["commit_spend"]
        if isinstance(round_event.get("goal_id"), str) and round_event["goal_id"].strip():
            p_payload["goal_id"] = round_event["goal_id"].strip()
        p_code, p_body = v03_ecology_view.mutate_v03_ecology_view(
            inst_root,
            p_payload,
            actor_side="subject",
            now_iso=effective_now_iso,
        )
        if p_code != 200:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(p_body.get("reason_code") or "PURCHASE_DELIBERATION_FAILED"),
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v03_ecology.economy")
        details["purchase"] = p_body.get("receipt")

    # 6. Meaningless Idle -> v03_clock_evaporation (zero productivity penalty)
    if action_kind == "MEANINGLESS_IDLE":
        idle_kind = str(round_event.get("idle_kind") or "daydream_window").strip()
        i_payload: dict[str, Any] = {
            "action": "start_meaningless_idle",
            "idle_kind": idle_kind,
            "operation_key": f"{op_key}_idle"[:96],
        }
        if isinstance(round_event.get("quiet_note"), str) and round_event["quiet_note"].strip():
            i_payload["quiet_note"] = round_event["quiet_note"].strip()
        i_code, i_body = v03_clock.mutate_v03_clock_view(
            inst_root,
            i_payload,
            actor_side="subject",
            now_iso=effective_now_iso,
        )
        if i_code == 200:
            slices_updated.append("v03_clock.meaningless_idle")
            raw_idle_rcpt = dict((i_body.get("data") or {}).get("action_receipt") or i_body.get("action_receipt") or {})
            raw_idle_rcpt.setdefault("productivity_penalty", 0)
            raw_idle_rcpt.setdefault("growth_score_delta", 0)
            raw_idle_rcpt.setdefault("created_artifact", False)
            raw_idle_rcpt.setdefault("created_share", False)
            details["meaningless_idle"] = raw_idle_rcpt

    # 7. Cross-period / Overnight Clock Advance -> v03_clock_evaporation + active illness evolution
    if action_kind in ("ADVANCE_CLOCK", "CROSS_NIGHT_ADVANCE") or round_event.get("advance_clock_to_iso"):
        target_clock_iso = str(
            round_event.get("advance_clock_to_iso")
            or round_event.get("target_now_iso")
            or effective_now_iso
        ).strip()
        c_code, c_body = v03_clock.mutate_v03_clock_view(
            inst_root,
            {
                "action": "advance_projection",
                "sync_v02": bool(round_event.get("sync_v02", True)),
                "operation_key": f"{op_key}_clock"[:96],
            },
            actor_side="subject",
            now_iso=target_clock_iso,
        )
        if c_code != 200:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(c_body.get("reason_code") or "CLOCK_ADVANCE_FAILED"),
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v03_clock")
        details["clock_advance"] = (c_body.get("data") or {}).get("action_receipt")

        eco_file = inst_root / "phone" / "v03_ecology_state.json"
        if eco_file.is_file() and round_event.get("evolve_illness", True):
            raw_rm = round_event.get("rest_mode")
            adv_payload: dict[str, Any] = {
                "action": "advance_illness_recovery",
                "target_now_iso": target_clock_iso,
                "operation_key": f"{op_key}_clock_illness"[:96],
            }
            if raw_rm is True:
                adv_payload["rest_mode"] = "sleep_like"
            elif isinstance(raw_rm, str) and raw_rm in ("rest", "sleep_like", "none"):
                adv_payload["rest_mode"] = raw_rm
            ie_code, ie_body = v03_ecology_view.mutate_v03_ecology_view(
                inst_root,
                adv_payload,
                actor_side="subject",
                now_iso=target_clock_iso,
            )
            if ie_code == 200:
                ie_data = ie_body.get("data") if isinstance(ie_body.get("data"), Mapping) else ie_body
                slices_updated.append("v03_ecology.illness")
                details["illness_after_clock"] = (ie_data or {}).get("illness")

    result_doc: dict[str, Any] = {
        "ok": True,
        "settled": True,
        "already_applied": False,
        "status": "APPLIED",
        "effect_state": "APPLIED",
        "action_kind": action_kind,
        "operation_key": op_key,
        "receipt_ref": receipt_ref,
        "settled_at": effective_now_iso,
        "slices_updated": list(dict.fromkeys(slices_updated)),
        "details": details,
        "optimistic_writes": 0,
        "llm_calls": 0,
    }

    ledger["revision"] = int(ledger["revision"]) + 1
    ledger["receipts"][op_key] = {
        "intent_hash": intent_hash,
        "settled_at": effective_now_iso,
        "result": copy.deepcopy(result_doc),
    }
    while len(ledger["receipts"]) > MAX_LEDGER_RECEIPTS:
        oldest_k = next(iter(ledger["receipts"]))
        if oldest_k == op_key:
            break
        ledger["receipts"].pop(oldest_k, None)
    _atomic_write_json(_consequence_ledger_path(inst_root), ledger)

    return result_doc


# ---------------------------------------------------------------------------
# V-E Observability Classifier & V-C Anti-Resurrection Projection Audit
# ---------------------------------------------------------------------------


def classify_day_observability(
    day_root: Path | str,
    *,
    round_outcome: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Strictly distinguish quiet eventless day, unresolved UNKNOWN suspension, and intentional REST (0 writes)."""
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    _dt, eff_iso, _ld, _tz = _resolve_observation_clock_zero_write(inst_root, now_iso)

    pending_unknown_refs: list[str] = []
    clock_path = inst_root / "phone" / v03_clock.V03_CLOCK_FILENAME
    if clock_path.is_file() and not clock_path.is_symlink():
        st, body = v03_clock.read_v03_clock_view(
            inst_root, has_subject_capability=True, now_iso=eff_iso
        )
        if st == 200 and isinstance(body.get("data"), Mapping):
            il = body["data"].get("iron_laws") or {}
            for u in il.get("pending_unknown_actions") or []:
                if isinstance(u, Mapping) and u.get("effect_state") == "UNKNOWN":
                    pending_unknown_refs.append(f"clock_unk:{u.get('action_id')}")

    som_path = somatic_comfort_view.state_path(inst_root)
    if som_path.is_file() and not som_path.is_symlink():
        s_state, s_reason = somatic_comfort_view.read_state(inst_root)
        if s_reason is None:
            drink = (s_state.get("care") or {}).get("drink")
            if isinstance(drink, Mapping) and (
                drink.get("pending_unknown") is True or drink.get("state") == "pending_unknown"
            ):
                pending_unknown_refs.append("somatic:care_drink_pending_unknown")

    outcome_up = str(round_outcome or "").strip().upper()
    if outcome_up in ("STEP_UNKNOWN", "UNKNOWN", "DEFERRED_UNKNOWN"):
        pending_unknown_refs.append(f"round:{outcome_up}")

    eco_env = _read_ecology_envelope_zero_write(inst_root, eff_iso)
    rest_active = bool(eco_env["illness"].get("rest_active")) or outcome_up in (
        "REST",
        "ILLNESS_REST",
        "BED_REST",
        "SLEEP",
    )

    if pending_unknown_refs:
        obs_class = "PENDING_UNKNOWN_SUSPENDED"
    elif rest_active:
        obs_class = "INTENTIONAL_REST_RECOVERY"
    else:
        obs_class = "QUIET_EVENTLESS_DAY"

    return {
        "observability_class": obs_class,
        "has_pending_unknown": bool(pending_unknown_refs),
        "pending_unknown_refs": pending_unknown_refs,
        "is_intentional_rest": bool(rest_active and not pending_unknown_refs),
        "is_quiet_unblocked": obs_class == "QUIET_EVENTLESS_DAY",
        "unknown_guessed_as_quiet": False,
        "unknown_guessed_as_rest": False,
        "observed_at": eff_iso,
        "llm_calls": 0,
    }


def reconcile_and_project_revocation_surfaces(
    day_root: Path | str,
    *,
    projects_service: Any,
    sharing_service: Any,
    derived_lineage: Iterable[Mapping[str, Any]] = (),
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Rebuild share index from live owner, run v02 sync, and project all 4 V-C surfaces.

    Used to prove that once a share is withdrawn (``DROPPED``) or a source artifact is archived/deleted,
    re-running ``sync_v02_slices``, rebuilding indexes, or replaying idempotency keys NEVER resurrects
    revoked content in Fridge Keepsakes, Outing Gallery, Dual-Layer Memory Lantern, or NPC Knowledge.
    """
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    _dt, eff_iso, _ld, _tz = _resolve_observation_clock_zero_write(inst_root, now_iso)

    shares_res = sharing_service.list_shares(limit=100)
    shares_data = (
        shares_res.get("data")
        if isinstance(shares_res, Mapping) and isinstance(shares_res.get("data"), Mapping)
        else shares_res
    )
    live_shares = list((shares_data or {}).get("shares") or []) if isinstance(shares_data, Mapping) else []

    active_delivered_shares: dict[str, dict[str, Any]] = {}
    delivered_by_version: dict[tuple[str, str], dict[str, Any]] = {}
    for row in live_shares:
        if not isinstance(row, Mapping):
            continue
        sid = str(row.get("share_id") or "")
        if not sid:
            continue
        detail = sharing_service.read_share(sid)
        d_data = (
            detail.get("data")
            if isinstance(detail, Mapping) and isinstance(detail.get("data"), Mapping)
            else detail
        )
        s_obj = d_data.get("share") if isinstance(d_data, Mapping) else None
        src_cur = d_data.get("source_current") if isinstance(d_data, Mapping) else None
        rcpt_obj = d_data.get("receipt") if isinstance(d_data, Mapping) else None
        if not isinstance(s_obj, Mapping):
            continue
        share_lifecycle = str(s_obj.get("lifecycle") or row.get("lifecycle") or "ACTIVE").upper()
        src_state = str(
            (src_cur or {}).get("state")
            or s_obj.get("source_state")
            or ""
        ).upper()
        src_info = s_obj.get("source") if isinstance(s_obj.get("source"), Mapping) else {}
        src_art_id = str(s_obj.get("source_artifact_id") or src_info.get("artifact_id") or "")
        src_ver_id = str(s_obj.get("source_version_id") or src_info.get("version_id") or "")
        if (
            s_obj.get("state") == "LOCAL_DELIVERED"
            and share_lifecycle not in ("ARCHIVED", "TOMBSTONED")
            and src_state in ("READABLE", "REVISION_CHANGED")
            and not bool(s_obj.get("text_withheld"))
            and isinstance(s_obj.get("text"), str)
            and bool(s_obj.get("text"))
        ):
            merged = dict(s_obj)
            merged["delivered_at"] = (
                s_obj.get("delivered_at")
                or (rcpt_obj.get("delivered_at") if isinstance(rcpt_obj, Mapping) else None)
                or s_obj.get("updated_at")
            )
            merged["source_artifact_id"] = src_art_id
            merged["source_version_id"] = src_ver_id
            active_delivered_shares[sid] = merged
            if src_art_id and src_ver_id:
                delivered_by_version[(src_art_id, src_ver_id)] = merged

    # Reconcile committed delivered_shares.json index from live sharing owner
    delivered_index_path = inst_root / "phone" / "delivered_shares.json"
    if delivered_index_path.exists() or active_delivered_shares:
        _atomic_write_json(
            delivered_index_path,
            {"share_ids": sorted(active_delivered_shares.keys()), "updated_at": eff_iso},
        )

    # Re-run 0.2 sync from committed world after index rebuild
    sync_report = v03_clock.sync_v02_slices_from_committed_world(
        inst_root, eff_iso, refresh_reunion_baseline=True
    )

    # 1. Fridge Keepsakes projection (partner_default & subject)
    ks_state, ks_reason = window_keepsake_view.read_state(inst_root)
    pin_inputs: list[dict[str, Any]] = []
    for pin in ks_state.get("pins") or []:
        sid = str(pin.get("share_id") or "")
        live = active_delivered_shares.get(sid)
        if live is not None:
            pin_inputs.append({
                "share_id": sid,
                "pinned_at": pin.get("pinned_at"),
                "status": "available",
                "reason_code": None,
                "delivered_at": live.get("delivered_at"),
                "state": "LOCAL_DELIVERED",
                "text_withheld": False,
                "text": live.get("text"),
                "text_chars": len(str(live.get("text") or "")),
            })
        else:
            pin_inputs.append({
                "share_id": sid,
                "pinned_at": pin.get("pinned_at"),
                "status": "withheld",
                "reason_code": "SHARE_WITHDRAWN_OR_SOURCE_GOVERNED",
                "delivered_at": None,
                "state": "WITHDRAWN",
                "text_withheld": True,
            })
    fridge_partner = window_keepsake_view.keepsakes_block(
        state=ks_state,
        state_reason=ks_reason,
        lockscreen=None,
        pins=pin_inputs,
        subject_view=False,
    )

    # Helper to inspect one (artifact_id, version_id) against live PrivateProjectService + Sharing
    def _inspect_version(art_id: str, ver_id: str) -> dict[str, Any]:
        proj_ans = projects_service.read_project(art_id)
        if not isinstance(proj_ans, Mapping) or proj_ans.get("status") != "ok":
            return {
                "readable": False,
                "lifecycle": "NOT_FOUND",
                "reason_code": str((proj_ans or {}).get("reason_code") or "SOURCE_NOT_READABLE"),
                "delivered": False,
            }
        proj_doc = proj_ans.get("project") or {}
        lifecycle = str(proj_doc.get("lifecycle") or "ACTIVE")
        if lifecycle == "ARCHIVED":
            return {
                "readable": False,
                "lifecycle": "ARCHIVED",
                "reason_code": long_companionship_view.ARCHIVED_SOURCE,
                "delivered": False,
            }
        # Find matching version in history or head
        history_entries = list(((proj_ans.get("history") or {}).get("entries")) or [])
        matched_ver = next(
            (e for e in history_entries if isinstance(e, Mapping) and e.get("version_id") == ver_id),
            None,
        )
        if matched_ver is None and proj_doc.get("head_version_id") != ver_id:
            return {
                "readable": False,
                "lifecycle": lifecycle,
                "reason_code": long_companionship_view.VERSION_NOT_IN_ARTIFACT,
                "delivered": False,
            }
        ver_text_ans = (
            projects_service.read_project_version(art_id, ver_id)
            if hasattr(projects_service, "read_project_version")
            else None
        )
        if isinstance(ver_text_ans, Mapping) and ver_text_ans.get("status") == "ok":
            vdoc = (
                ver_text_ans.get("version")
                if isinstance(ver_text_ans.get("version"), Mapping)
                else ver_text_ans
            )
            owner_text = str(vdoc.get("content") or "")
            v_num = vdoc.get("version_number")
            v_created = vdoc.get("created_at")
        else:
            owner_text = str(proj_doc.get("content") or "")
            v_num = (matched_ver or {}).get("version_number") or proj_doc.get("version_number")
            v_created = (matched_ver or {}).get("created_at") or proj_doc.get("updated_at")

        live_share = delivered_by_version.get((art_id, ver_id))
        return {
            "readable": True,
            "lifecycle": lifecycle,
            "reason_code": None,
            "title": proj_doc.get("title"),
            "version_number": v_num,
            "version_created_at": v_created,
            "owner_excerpt": owner_text[:400],
            "delivered": live_share is not None,
            "share_id": live_share.get("share_id") if live_share else None,
            "delivered_title": live_share.get("title") if live_share else None,
            "delivered_excerpt": str(live_share.get("text") or "")[:400] if live_share else None,
            "delivered_at": live_share.get("delivered_at") if live_share else None,
        }

    # 2. Outing Gallery projection (partner_default & subject)
    gb_state, gb_reason = outing_places_view.read_gallery_blueprint_state(inst_root)
    exhibits_partner: list[dict[str, Any]] = []
    exhibits_subject: list[dict[str, Any]] = []
    for ex in gb_state.get("exhibits") or []:
        aid = str(ex.get("artifact_id") or "")
        vid = str(ex.get("version_id") or "")
        insp = _inspect_version(aid, vid)
        p_avail = bool(insp.get("readable") and insp.get("delivered"))
        exhibits_partner.append(
            outing_places_view.format_exhibit_projection(
                ex,
                subject_view=False,
                source_status="available" if p_avail else "withheld",
                reason_code=None if p_avail else (insp.get("reason_code") or "VERSION_NOT_DELIVERED"),
                title=insp.get("title"),
                version_number=insp.get("version_number"),
                delivered_share_id=insp.get("share_id"),
                delivered_text=insp.get("delivered_excerpt"),
                delivered_at=insp.get("delivered_at"),
            )
        )
        s_avail = bool(insp.get("readable"))
        exhibits_subject.append(
            outing_places_view.format_exhibit_projection(
                ex,
                subject_view=True,
                source_status="available" if s_avail else "withheld",
                reason_code=insp.get("reason_code"),
                title=insp.get("title"),
                version_number=insp.get("version_number"),
                owner_content=insp.get("owner_excerpt"),
            )
        )
    gallery_partner = outing_places_view.gallery_blueprint_block(
        state=gb_state,
        state_reason=gb_reason,
        exhibits_projected=exhibits_partner,
        subject_view=False,
    )
    gallery_subject = outing_places_view.gallery_blueprint_block(
        state=gb_state,
        state_reason=gb_reason,
        exhibits_projected=exhibits_subject,
        subject_view=True,
    )

    # 3. Dual-Layer Memory Lantern projection (partner_default & subject)
    gl_state, gl_reason = long_companionship_view.read_gifts_lantern_state(inst_root)
    needed_pairs: set[tuple[str, str]] = set()
    for g in gl_state.get("shared_gifts") or []:
        if g.get("artifact_id") and g.get("version_id"):
            needed_pairs.add((str(g["artifact_id"]), str(g["version_id"])))
    for r in gl_state.get("later_reflections") or []:
        needed_pairs.add((str(r["source_artifact_id"]), str(r["source_version_id"])))
    lineage_list = list(derived_lineage)
    for rem in lineage_list:
        if rem.get("source_artifact_id") and rem.get("source_version_id"):
            needed_pairs.add((str(rem["source_artifact_id"]), str(rem["source_version_id"])))
        if rem.get("derived_artifact_id") and rem.get("derived_version_id"):
            needed_pairs.add((str(rem["derived_artifact_id"]), str(rem["derived_version_id"])))

    inspections = {pair: _inspect_version(pair[0], pair[1]) for pair in needed_pairs}
    lantern_partner = long_companionship_view.gifts_lantern_block(
        state=gl_state,
        state_reason=gl_reason,
        layer="with_later_context",
        version_inspections=inspections,
        derived_lineage=lineage_list,
        subject_view=False,
    )
    lantern_subject = long_companionship_view.gifts_lantern_block(
        state=gl_state,
        state_reason=gl_reason,
        layer="with_later_context",
        version_inspections=inspections,
        derived_lineage=lineage_list,
        subject_view=True,
    )

    # 4. NPC Ecology projection
    npc_status, npc_body = v03_embodied_social_view.read_v03_embodied_social_view(
        inst_root,
        has_subject_capability=False,
        now_iso=eff_iso,
    )
    npc_ecology = (npc_body.get("npc_ecology") if npc_status == 200 and isinstance(npc_body, Mapping) else {})

    return {
        "ok": True,
        "synced_at": eff_iso,
        "sync_report": sync_report,
        "fridge_keepsakes_partner": fridge_partner,
        "gallery_partner": gallery_partner,
        "gallery_subject": gallery_subject,
        "memory_lantern_partner": lantern_partner,
        "memory_lantern_subject": lantern_subject,
        "npc_ecology_partner": npc_ecology,
        "llm_calls": 0,
    }


def run_v02_v03_daily_tick(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
    previous_host_iso: Optional[str] = None,
    runtime_mode_override: Optional[str] = None,
    advance_turn: bool = True,
    advance_clock: bool = True,
) -> dict[str, Any]:
    """Advance one 0.2 + 0.3 daily loop tick with strict zero LLM calls and runtime conservation gating."""
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    _now_dt, eff_iso, local_date, _tz_offset = _resolve_observation_clock_zero_write(inst_root, now_iso)

    _ad_summary, rt_summary = _read_v02_agency_and_runtime_summaries_zero_write(inst_root, eff_iso)
    eff_mode = str(
        runtime_mode_override
        or rt_summary.get("health_state")
        or rt_summary.get("mode")
        or "QUIET_HEALTHY"
    ).strip().upper()
    is_dormant_no_cog = eff_mode in ("DORMANT_NO_COGNITION", "DORMANT")
    is_dormant_cons = eff_mode == "DORMANT_CONSERVATION"
    high_dissipation_skipped = bool(is_dormant_no_cog or is_dormant_cons)

    # 1. Host offline/return sensing (life_dwell)
    host_gap_report = v03_dwell.evaluate_host_gap(
        inst_root, previous_host_iso=previous_host_iso, resumed_iso=eff_iso
    )
    dwell_file = v03_dwell.life_dwell_state_path(inst_root)
    if host_gap_report.get("is_host_gap") and (dwell_file.is_file() or previous_host_iso):
        try:
            v03_dwell.apply_life_dwell_action(
                inst_root,
                {
                    "action": "record_host_gap",
                    "offline_at": host_gap_report.get("previous_host_at") or previous_host_iso or eff_iso,
                    "resumed_at": eff_iso,
                },
                now_iso=eff_iso,
            )
        except Exception:
            pass

    # 2. Everyday desires turn_only natural decay
    desire_fade_report: dict[str, Any] = {"faded_turn_only_count": 0, "active_count": 0}
    desires_file = v03_desires.everyday_desire_state_path(inst_root)
    if advance_turn and desires_file.is_file():
        try:
            d_out = v03_desires.apply_everyday_desire_action(
                inst_root, {"action": "advance_turn"}, now_iso=eff_iso
            )
            rcpt = d_out.get("action_receipt") if isinstance(d_out.get("action_receipt"), Mapping) else {}
            desire_fade_report = {
                "faded_turn_only_count": int(rcpt.get("faded_turn_only_count") or 0),
                "active_count": (
                    len(d_out.get("turn_only_concerns") or [])
                    + len(d_out.get("retained_concerns") or [])
                ),
            }
        except Exception:
            pass

    # 3. Three-clock & memory evaporation advance
    clock_file = inst_root / "phone" / v03_clock.V03_CLOCK_FILENAME
    clock_report: dict[str, Any] = {"advanced": False}
    if advance_clock and not is_dormant_no_cog and clock_file.is_file():
        c_out = v03_clock.apply_v03_clock_action(
            inst_root, {"action": "advance_projection"}, actor_side="subject", now_iso=eff_iso
        )
        clock_report = {
            "advanced": bool(c_out.get("ok")),
            "status": c_out.get("status"),
        }

    # 4. Collect all 0.2 + 0.3 candidates & summaries (zero-write)
    collected = collect_v02_v03_loop_candidates(inst_root, now_iso=eff_iso)
    if runtime_mode_override:
        if is_dormant_no_cog:
            collected["dormant_suppressed"] = True
            collected["candidates"] = []
            collected["candidate_records"] = []
            collected["descriptors"] = {}
            collected["gating"]["dormant_suppressed"] = True
            collected["gating"]["runtime_health_state"] = "DORMANT_NO_COGNITION"
        elif is_dormant_cons:
            collected["dormant_conservation"] = True
            collected["gating"]["dormant_conservation"] = True
            collected["gating"]["runtime_health_state"] = "DORMANT_CONSERVATION"
            essential = {"illness_rest_recovery", "follow_tomorrow_note"}
            kept_recs = [
                r for r in collected["candidate_records"]
                if str((collected["descriptors"].get(r.candidate_id) or {}).get("action_family") or "") in essential
            ][:1]
            collected["candidate_records"] = kept_recs
            collected["candidates"] = [r.to_dict() for r in kept_recs]
            collected["descriptors"] = {r.candidate_id: collected["descriptors"][r.candidate_id] for r in kept_recs}

    selected = select_deterministic_loop_action(collected)
    if is_dormant_cons and selected.get("selected_candidate_id") is None:
        selected = {
            **selected,
            "decision_kind": "DORMANT_CONSERVATION_IDLE",
            "dormant_conservation": True,
            "high_dissipation_skipped": True,
            "llm_calls": 0,
        }

    return {
        "ok": True,
        "schema": BRIDGE_SCHEMA,
        "tick_at": eff_iso,
        "local_date": local_date,
        "runtime_mode": eff_mode,
        "high_dissipation_skipped": high_dissipation_skipped,
        "host_gap": host_gap_report,
        "everyday_desires": desire_fade_report,
        "clock_evaporation": clock_report,
        "recall_summary": collected["gating"].get("recall_projection_summary", {}),
        "common_ground_summary": collected["gating"].get("common_ground_summary", {}),
        "economy_illness": {
            "illness": collected["gating"].get("illness", {}),
            "economy": collected["gating"].get("economy", {}),
        },
        "npc_ecology": collected["gating"].get("npc_ecology_summary", {}),
        "emotional_tension": collected["gating"].get("emotional_tension_summary", {}),
        "disposal_check": collected["gating"].get("disposal_ledger_summary", {}),
        "candidate_count": len(collected.get("candidates") or []),
        "collected": collected,
        "selected_action": selected,
        "llm_calls": 0,
    }


__all__ = [
    "BRIDGE_SCHEMA",
    "CONSEQUENCE_LEDGER_FILENAME",
    "FROZEN_ANCHOR_SHA256",
    "classify_day_observability",
    "collect_v02_v03_loop_candidates",
    "has_v02_v03_active_state",
    "reconcile_and_project_revocation_surfaces",
    "resolve_day_and_life_roots",
    "run_v02_v03_daily_tick",
    "select_deterministic_loop_action",
    "settle_v02_v03_round_consequences",
    "verify_frozen_anchors",
]
