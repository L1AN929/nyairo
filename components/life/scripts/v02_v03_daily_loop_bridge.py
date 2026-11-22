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
from collections.abc import Iterable, Mapping, Sequence
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
from v02_representation_lease_forget import (
    REP_DIVERGED,
    _empty_state as _empty_forget_state,
)
try:
    from . import v02_representation_lease_forget as v02_rlf  # type: ignore[no-redef]  # noqa: E402
except ImportError:  # pragma: no cover - flat life/scripts import path
    import v02_representation_lease_forget as v02_rlf  # type: ignore[no-redef]  # noqa: E402
from life_future_items import FutureItemError, FutureItems, STATE_FILENAME as FUTURE_ITEMS_FILENAME  # noqa: E402
import everyday_desire_lifecycle as v03_desires  # noqa: E402
import life_dwell as v03_dwell  # noqa: E402
import v02_common_ground  # noqa: E402
import v02_recall_projection  # noqa: E402
import v03_clock_evaporation as v03_clock  # noqa: E402

try:  # Ch.50/Ch.53 elastic-play readiness (package import keeps the shared tx guard).
    from . import v04_elastic_play_learning as v04_ep_learning  # noqa: E402
except ImportError:  # pragma: no cover - flat life/scripts import path
    import v04_elastic_play_learning as v04_ep_learning  # type: ignore[no-redef]  # noqa: E402

import v03_economy_illness_bridge as v03_eco_bridge  # noqa: E402
import v08_open_affordance as v08_open  # noqa: E402
import v08_universal_matter as v08_matter  # noqa: E402
import world_disposal_contract as v03_disposal  # noqa: E402

try:  # Phase B0 shared transaction guard (flat or package import).
    from . import state_tx_guard as _tx_guard  # type: ignore[no-redef]
except ImportError:  # pragma: no cover - flat life/scripts import path
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]

from components.phone import (  # noqa: E402
    adult_intimacy_view,
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
LIFE_UPDATE_SURFACES = 37
MAX_LIVED_CONTEXT_CHARS = 1800

# ---------------------------------------------------------------------------
# v0.8 Wave 2: v0.4 院子晾晒 / 盆栽 / 纸盒手作 / 街角借还 的真实生活候选
# ---------------------------------------------------------------------------
#: 只读查询的确定性状态文件（与 ``v04_neighborhood_living.STATE_FILENAME`` 一致）。
V04_NEIGHBORHOOD_STATE_RELPATH = ("world", "v04_neighborhood_state.json")
#: 会把露天晾晒布巾淋湿的天气族；天气改判与抢收候选都只看这一组。
V04_RAIN_WEATHER = frozenset({"rain", "light_rain", "shower", "storm", "drizzle", "heavy_rain"})
#: 长驻盆栽缺水阈值（低于此值视为"该浇水了"；低于 25 会进入 wilting 阶段）。
V04_PLANT_THIRSTY_HYDRATION = 35.0
#: 六类 v0.4 动作族的结算映射（兼容 action_kind / v04_action / source_ref 三种入口）。
V04_ACTION_KIND_TO_INTERNAL: dict[str, str] = {
    "HANG_DRYING_CLOTH": "hang_or_collect_cloth",
    "RESCUE_DRYING_CLOTH": "hang_or_collect_cloth",
    "COLLECT_DRIED_CLOTH": "hang_or_collect_cloth",
    "CARE_PORCH_PLANT": "water_or_move_plant",
    "ADVANCE_HOME_CRAFT": "craft_paper_or_box",
    "RETURN_NEIGHBORHOOD_LOAN": "shared_shelf_loan",
}
V04_ACTION_FAMILIES_SELECTABLE = frozenset({
    "hang_drying_cloth",
    "courtyard_care",
    "collect_dried_cloth",
    "care_porch_plant",
    "advance_home_craft",
    "return_neighborhood_loan",
    # v0.8 Wave 3: 街道 / 告示 / 小册 / 书店展陈四类真实日常。
    "advance_neighborhood_journey",
    "read_public_notice",
    "update_place_booklet",
    "submit_display_card",
    # v0.8 Ch.50/Ch.53: 共享小本子的未回接句与已备好未递出的小惊喜。
    # 两者都是可选、零债务的温和时机，不是排班或提醒。
    "elastic_shared_notebook",
    "elastic_offer_surprise",
    # Batch E: 第二位独立街坊店主（街角焙煎咖啡屋店主）的可选走动机会。
    "visit_cafe_resident",
})

FROZEN_ANCHOR_SHA256: dict[str, str] = {
    # 2026-11-14 X1 批次重钉：07 章 §四 生产接线（wake 票 est 合计 → reserve(est_tokens=...)
    # + daily_token_budget=50000 生产启用）有意修改装配文件；旧摘要 76b4c7cb…31fa5a。
    "components/life/scripts/autonomous_life_assembly.py": (
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
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

# ---------------------------------------------------------------------------
# Stage 2 (Pillars 1 & 2): Desire Arbitration & Acute Body-Care Constants
# ---------------------------------------------------------------------------
DESIRE_RAIN_STORM_WEATHERS: frozenset[str] = frozenset({
    "rain",
    "storm",
    "heavy_rain",
    "thunder_blackout",
    "sea_gale",
})
ACUTE_FATIGUE_THRESHOLD: float = 0.72
ACUTE_HUNGER_THRESHOLD: float = 0.70
ACUTE_THIRST_THRESHOLD: float = 0.70
ACUTE_WARMTH_CHILL_THRESHOLD: float = 0.35
ACUTE_WARMTH_OVERHEAT_THRESHOLD: float = 0.75
MAX_ARBITRATED_DESIRE_CANDIDATES: int = 5
ACUTE_BODY_CARE_FAMILIES: frozenset[str] = frozenset({
    "hang_outerwear",
    "acute_body_rest_sleep_like",
    "drink_warm_water",
    "eat_warm_meal",
    "cool_down_air",
})
_DESIRE_DOMAIN_TO_CATEGORY: dict[str, str] = {
    "comfort": "cozy_comfort",
    "exploration": "restlessness_outing",
    "creation": "craft_ambition",
    "possession": "appetite_craving",
    "companionship": "social_longing",
    "intimacy": "intimate_warmth",
}
_DESIRE_STRENUOUS_CATEGORIES: frozenset[str] = frozenset({
    "restlessness_outing",
    "craft_ambition",
    "mischief_rivalry",
})
_DESIRE_TEXT_NORM_RE = re.compile(r"[\s,，。、；;！!？?「」『』《》()（）\[\]【】\"'\-]+")

# ---------------------------------------------------------------------------
# v0.8 Wave 6: 0.2 (v2.0) daily-loop action families
# ---------------------------------------------------------------------------
#: The seven real ``0.2`` clue families whose selected candidate must settle through its
#: owner (tomorrow note, tentative plan, workbench, bookstore bookmark, walk route,
#: plant journal, common ground).  ``action_family`` is the producer's own field, so the
#: event builder maps it once here instead of matching a growing pile of ref prefixes.
V02_DAILY_FAMILY_TO_KIND: dict[str, str] = {
    "follow_tomorrow_note": "FOLLOW_TOMORROW_NOTE",
    "adopt_tentative_plan": "ADOPT_TENTATIVE_PLAN",
    "advance_workbench_draft": "ADVANCE_WORKBENCH",
    "follow_bookstore_bookmark": "FOLLOW_BOOKSTORE_BOOKMARK",
    "strenuous_outing_walk": "STRENUOUS_OUTING_WALK",
    "observe_plant_journal": "OBSERVE_PLANT_JOURNAL",
    "clarify_common_ground": "CLARIFY_COMMON_GROUND",
    "advance_bounded_inquiry": "ADVANCE_BOUNDED_INQUIRY",
    "follow_placed_memory_cue": "FOLLOW_PLACED_MEMORY_CUE",
    "act_optional_next_gesture": "ACT_OPTIONAL_NEXT_GESTURE",
    "open_future_capsule": "OPEN_FUTURE_CAPSULE",
    "consider_rescaled_copractice": "CONFIRM_RESCALED_COPRACTICE",
}

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


def _resolve_instance_paths_zero_write(day_root: Path | str) -> Any:
    """Resolve canonical InstancePaths zero-write via shared instance_paths module."""
    try:
        from components.world.scripts import instance_paths as _ip
    except ImportError:
        try:
            import instance_paths as _ip  # type: ignore[no-redef]
        except ImportError:
            return None
    try:
        return _ip.resolve_instance_paths(day_root)
    except Exception:
        return None


def _read_weather_zero_write(inst_root: Path) -> str:
    """Read current neighborhood weather zero-write (defaults to 'clear')."""
    v04_doc = _read_optional_json_zero_write(inst_root / "world" / "v04_neighborhood_state.json")
    if isinstance(v04_doc, Mapping):
        for key in ("environment", "weather_and_ground", "weather"):
            block = v04_doc.get(key)
            if isinstance(block, Mapping):
                w = str(block.get("weather") or block.get("condition") or "").strip().lower()
                if w:
                    return w
            elif key == "weather" and isinstance(block, str) and block.strip():
                return block.strip().lower()
    return "clear"


def resolve_day_and_life_roots(day_root: Path | str) -> tuple[Path, Path]:
    """Resolve ``(instance_day_root, life_state_root)`` purely without disk writes.

    Supports managed ``<day_root>/one-day/life/state`` & ``<day_root>/one-day/world-home``
    via ``resolve_instance_paths``, legacy ``<instance_root>/life/state``, and isolated
    ``<state_root>`` unit-test layouts.
    """
    raw = Path(day_root)
    paths = _resolve_instance_paths_zero_write(raw)
    if paths is not None and (paths.is_managed_instance or paths.day_root != raw):
        inst_root = paths.day_root
        if raw != inst_root and raw.name == "state" and raw.parent.name == "life":
            return inst_root, raw
        if paths.life_state_root.is_dir() and not paths.life_state_root.is_symlink():
            return inst_root, paths.life_state_root
        direct_life = inst_root / "life" / "state"
        if direct_life.is_dir() and not direct_life.is_symlink():
            return inst_root, direct_life
        return inst_root, inst_root

    if not (raw / "phone").exists() and raw.name == "state" and raw.parent.name == "life":
        candidate_instance = raw.parent.parent
        if (candidate_instance / "phone").is_dir():
            return candidate_instance, raw
        if candidate_instance.name == "one-day" and (candidate_instance.parent / "phone").is_dir():
            return candidate_instance.parent, raw
    life_state = raw / "life" / "state"
    if life_state.is_dir() and not life_state.is_symlink():
        return raw, life_state
    one_day_life_state = raw / "one-day" / "life" / "state"
    if one_day_life_state.is_dir() and not one_day_life_state.is_symlink():
        return raw, one_day_life_state
    return raw, raw


def _collect_v02_governance_signals_zero_write(day_root: Path) -> dict[str, Any]:
    """Zero-write inspection of Forget barrier, Representation DIVERGED, and UNVALIDATED_AFTER_MIGRATION skills."""
    from v02_representation_lease_forget import (
        REP_DIVERGED,
        inspect_representation,
        is_entity_forget_blocked,
        load_unified_forget_barrier_readonly,
    )

    barrier = load_unified_forget_barrier_readonly(day_root)
    state = barrier.get("raw_rlf") if isinstance(barrier.get("raw_rlf"), Mapping) else {}
    blocked_ids: set[str] = set(barrier.get("blocked_ids") or ())
    active_hold_ids: list[str] = sorted(str(x) for x in (barrier.get("active_hold_ids") or ()))
    barrier_active = bool(barrier.get("barrier_active"))
    diverged_artifact_ids: list[str] = []

    if isinstance(state, Mapping):
        reps = state.get("representations") if isinstance(state.get("representations"), Mapping) else {}
        for aid, binding in sorted(reps.items()):
            if not isinstance(binding, Mapping):
                continue
            if is_entity_forget_blocked({"artifact_id": str(aid)}, barrier):
                continue
            health = str(binding.get("health") or "")
            if health != REP_DIVERGED:
                try:
                    _rc, rep_info = inspect_representation(
                        day_root, artifact_id=str(aid), has_subject_capability=True
                    )
                    if isinstance(rep_info, Mapping):
                        health = str(rep_info.get("representation_health") or health)
                except Exception:
                    pass
            if health == REP_DIVERGED:
                diverged_artifact_ids.append(str(aid))

    # UNVALIDATED_AFTER_MIGRATION skills (zero-write)
    unvalidated_skills: list[str] = []
    asp_path = day_root / "state" / "v02_autobiography_skill_provenance.json"
    if asp_path.is_file() and not asp_path.is_symlink():
        try:
            import v02_autobiography_skill_provenance as _asp_mod

            sk_proj = _asp_mod.project_skill_provenance(day_root)
            for skey, srec in sorted((sk_proj.get("releases") or {}).items()):
                if not isinstance(srec, Mapping):
                    continue
                if str(srec.get("status") or "").upper() in (
                    "UNVALIDATED_AFTER_MIGRATION",
                    "SUSPENDED",
                    "DEGRADED",
                ):
                    unvalidated_skills.append(str(srec.get("method_id") or skey))
        except Exception:
            pass

    return {
        "barrier_active": barrier_active,
        "diverged": len(diverged_artifact_ids) > 0,
        "blocked_ids": sorted(blocked_ids),
        "active_hold_ids": sorted(active_hold_ids),
        "forget_barrier": barrier,
        "diverged_artifact_ids": diverged_artifact_ids,
        "unvalidated_skills": sorted(set(unvalidated_skills)),
    }


def check_barrier_and_divergence(day_root: Path | str) -> dict[str, Any]:
    """Zero-write check for active Forget holds/tombstones, DIVERGED .md bindings, and unvalidated skills."""
    inst_root, _ = resolve_day_and_life_roots(day_root)
    return _collect_v02_governance_signals_zero_write(inst_root)


def _resumable_intimacy_checkpoints_zero_write(inst_root: Path) -> list[dict[str, Any]]:
    """Read real resumable adult-intimacy entry checkpoints with 100% zero writes.

    A permit record qualifies only when ``checkpoint.resumable is True``, the checkpoint
    carries a non-empty ``activity_ref`` and it was not already consumed.  A missing,
    malformed or symlinked file yields ``[]`` and touches nothing.
    """
    state_path = adult_intimacy_view.adult_intimacy_state_path(inst_root)
    doc = _read_optional_json_zero_write(state_path)
    if not isinstance(doc, Mapping):
        return []
    permits = doc.get("permits") if isinstance(doc.get("permits"), Mapping) else {}
    records = permits.get("records") if isinstance(permits.get("records"), Mapping) else {}
    found: list[dict[str, Any]] = []
    for permit_id, rec in records.items():
        if not isinstance(rec, Mapping):
            continue
        checkpoint = rec.get("checkpoint") if isinstance(rec.get("checkpoint"), Mapping) else {}
        activity_ref = checkpoint.get("activity_ref")
        if checkpoint.get("resumable") is not True or checkpoint.get("consumed"):
            continue
        if not (isinstance(activity_ref, str) and activity_ref.strip()):
            continue
        found.append({
            "permit_id": str(permit_id),
            "activity_ref": activity_ref.strip(),
            "saved_at": checkpoint.get("saved_at"),
            "permit_state": str(rec.get("state") or ""),
        })
    found.sort(key=lambda row: row["permit_id"])
    return found


def has_v02_v03_active_state(day_root: Path | str) -> bool:
    """Pure zero-write check: True when the resolved instance root holds active living slices.

    Normalizes managed subpaths (``one-day/life/state``, ``one-day/world-home``) via
    ``resolve_instance_paths`` so bootstrapped managed roots are detected cleanly.
    The check only stats paths (no writes, no mtime touch) and refuses symlinks.
    """
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    for rel_file in (
        _v04_state_file(inst_root),
        inst_root / "world" / "v05_living_town.json",
        inst_root / "life" / "v05_inner_spark.json",
        inst_root / "life" / "everyday_desires.json",
        inst_root / "world" / "npc_commitments_state.json",
        inst_root / "life" / "v09_true_stance_state.json",
        inst_root / "world" / "personal_objects_taxonomy.json",
    ):
        if rel_file.is_file() and not rel_file.is_symlink():
            return True
    # 36A §11.1：一个可恢复的成年亲密入场断点本身就是真实可执行的日常线索。
    if _resumable_intimacy_checkpoints_zero_write(inst_root):
        return True
    phone_dir = inst_root / "phone"
    if phone_dir.is_symlink() or not phone_dir.is_dir():
        return False
    try:
        return any(
            p.suffix == ".json"
            and p.name != "instance.json"
            and p.is_file()
            and not p.is_symlink()
            for p in phone_dir.iterdir()
        )
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
    paths = _resolve_instance_paths_zero_write(inst_root)
    resolved_root = paths.day_root if paths is not None else inst_root
    primary_eco_file = resolved_root / "phone" / v03_eco_bridge.STATE_FILENAME
    alias_eco_file = resolved_root / "phone" / "v03_ecology_state.json"
    eco_file = primary_eco_file if primary_eco_file.is_file() else alias_eco_file
    wallet_candidates: list[Path] = []
    if paths is not None:
        wallet_candidates.append(paths.economy_state_json)
    for sub in (
        "one-day/world-home/state/world_property_economy.json",
        "one-day/world-home/world_property_economy.json",
        "world/state/world_property_economy.json",
        "world/world_property_economy.json",
        "world-home/state/world_property_economy.json",
        "world-home/world_property_economy.json",
    ):
        wallet_candidates.append(resolved_root / sub)
    world_wallet_path: Optional[Path] = None
    for cand in wallet_candidates:
        if cand.is_file() and not cand.is_symlink():
            world_wallet_path = cand
            break
    world_wallet_exists = world_wallet_path is not None
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
                "economy_active": False,
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
        resolved_root,
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
    if world_wallet_path is not None and not bool(eco_proj.get("world_economy_initialized")):
        raw_wallet = _read_optional_json_zero_write(world_wallet_path)
        if isinstance(raw_wallet, Mapping):
            w_bal = raw_wallet.get("available_balance_cents")
            if w_bal is None:
                w_bal = raw_wallet.get("balance_cents")
            if w_bal is None and isinstance(raw_wallet.get("balance"), (int, float)):
                w_bal = int(round(float(raw_wallet["balance"]) * 100))
            if w_bal is None and isinstance(raw_wallet.get("transactions"), list):
                tx_sum = 0
                for tx in raw_wallet["transactions"]:
                    if isinstance(tx, Mapping):
                        amt = tx.get("amount_cents")
                        if amt is None and isinstance(tx.get("amount"), (int, float)):
                            amt = int(round(float(tx["amount"]) * 100))
                        if isinstance(amt, (int, float)) and not isinstance(amt, bool):
                            tx_sum += int(amt)
                w_bal = tx_sum
            floor_cents = int(
                eco_proj.get("living_floor_cents") or v03_eco_bridge.DEFAULT_LIVING_FLOOR_CENTS
            )
            avail_cents = (
                int(w_bal)
                if isinstance(w_bal, (int, float)) and not isinstance(w_bal, bool)
                else int(eco_proj.get("available_balance_cents") or 0)
            )
            margin_cents = avail_cents - floor_cents
            eco_proj["world_economy_initialized"] = True
            eco_proj["available_balance_cents"] = avail_cents
            eco_proj["living_floor_cents"] = floor_cents
            eco_proj["discretionary_margin_cents"] = margin_cents
            eco_proj["budget_tight"] = margin_cents <= 0
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
            "economy_state_path": str(world_wallet_path) if world_wallet_path is not None else None,
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


def _read_pending_item_gift_zero_write(
    inst_root: Path,
    now_dt: datetime,
) -> Optional[dict[str, Any]]:
    """Read at most one non-expired ``OFFERED`` partner item gift from ``phone/v03_ecology.json`` (0 writes)."""
    primary = inst_root / "phone" / v03_eco_bridge.STATE_FILENAME
    alias = inst_root / "phone" / "v03_ecology_state.json"
    eco_path = primary if primary.is_file() else alias
    doc = _read_optional_json_zero_write(eco_path)
    if not isinstance(doc, Mapping):
        return None
    eco = doc.get("economy") if isinstance(doc.get("economy"), Mapping) else doc
    raw_escrow = eco.get("item_gift_escrow")
    rows: list[tuple[str, str, dict[str, Any]]] = []
    if isinstance(raw_escrow, Mapping):
        for gid, row in raw_escrow.items():
            if not isinstance(row, Mapping):
                continue
            gift_id = str(row.get("gift_id") or gid or "").strip()
            if gift_id:
                rows.append((str(row.get("offered_at") or ""), gift_id, dict(row, gift_id=gift_id)))
    elif isinstance(raw_escrow, list):
        for row in raw_escrow:
            if not isinstance(row, Mapping):
                continue
            gift_id = str(row.get("gift_id") or "").strip()
            if gift_id:
                rows.append((str(row.get("offered_at") or ""), gift_id, dict(row)))
    if not rows:
        return None
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _offered_at, _gid, row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status != "OFFERED":
            continue
        exp_dt = _parse_iso(row.get("expires_at"))
        if exp_dt is not None and now_dt >= exp_dt:
            continue
        return row
    return None


def _read_matured_subject_slow_letter_draft_zero_write(
    inst_root: Path,
    now_dt: datetime,
) -> Optional[dict[str, Any]]:
    """Read at most one matured (>=72h) subject-authored cooling draft from ``phone/window_keepsakes.json`` (0 writes).

    CRITICAL BILATERAL PRIVACY: Never inspect or surface ``author_side == "partner"`` cooling drafts.
    """
    doc = _read_optional_json_zero_write(inst_root / "phone" / "window_keepsakes.json")
    if not isinstance(doc, Mapping):
        return None
    raw_drafts = doc.get("slow_letter_drafts")
    if not isinstance(raw_drafts, list):
        return None
    matured: list[tuple[str, str, dict[str, Any]]] = []
    for row in raw_drafts:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("author_side") or "").strip().lower() != "subject":
            continue
        if str(row.get("status") or "").strip().lower() != "cooling_draft":
            continue
        draft_id = str(row.get("draft_id") or "").strip()
        if not draft_id:
            continue
        cools_dt = _parse_iso(row.get("cools_until"))
        if cools_dt is None or now_dt < cools_dt:
            continue
        matured.append((str(row.get("cools_until") or ""), draft_id, dict(row)))
    if not matured:
        return None
    matured.sort(key=lambda item: (item[0], item[1]))
    return matured[0][2]


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


def _read_retirement_status_zero_write(inst_root: Path) -> dict[str, Any]:
    """Pure zero-write inspection of F-148 dignified retirement seal status."""
    for cand in (
        inst_root / "phone" / "v02_runtime_migration.json",
        inst_root / "state" / "v02_runtime_governance_migration.json",
    ):
        doc = _read_optional_json_zero_write(cand)
        if not isinstance(doc, Mapping):
            continue
        ret_blk = (
            doc.get("retirement")
            if isinstance(doc.get("retirement"), Mapping)
            else (
                doc.get("retirement_status")
                if isinstance(doc.get("retirement_status"), Mapping)
                else None
            )
        )
        if isinstance(ret_blk, Mapping):
            st = str(ret_blk.get("status") or "ACTIVE").strip().upper() or "ACTIVE"
            return {
                "status": st,
                "retired_sealed": st == "RETIRED_ARCHIVE_SEALED",
                "farewell_note_cn": str(ret_blk.get("farewell_note_cn") or "").strip()[:240],
                "sealed_at": ret_blk.get("sealed_at"),
            }
    return {
        "status": "ACTIVE",
        "retired_sealed": False,
        "farewell_note_cn": "",
        "sealed_at": None,
    }


def _read_pending_rescaled_copractices_zero_write(inst_root: Path) -> list[dict[str, Any]]:
    """Pure zero-write scan of pending partner F-189 rescaled co-practice proposals in honest_ux state."""
    doc = _read_optional_json_zero_write(inst_root / "phone" / "v02_honest_ux_state.json")
    if not isinstance(doc, Mapping):
        return []
    raw_map = doc.get("proposed_rescaled_copractices")
    if not isinstance(raw_map, Mapping):
        return []
    pending: list[tuple[str, str, dict[str, Any]]] = []
    for pid_key, row in raw_map.items():
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").strip().upper()
        if status != "PROPOSED_BY_PARTNER":
            continue
        if row.get("subject_confirmed") is True:
            continue
        pid = str(row.get("practice_id") or pid_key or "").strip()
        if not pid:
            continue
        entry = dict(row)
        entry["practice_id"] = pid
        pending.append((str(row.get("updated_at") or ""), pid, entry))
    pending.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _ts, _pid, row in pending[:2]]


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


def _v04_state_file(inst_root: Path) -> Path:
    world_dir, filename = V04_NEIGHBORHOOD_STATE_RELPATH
    return Path(inst_root) / world_dir / filename


def _load_v04_neighborhood_state_zero_write(inst_root: Path) -> Optional[dict[str, Any]]:
    """Read the v0.4 neighborhood state with 100% zero writes.

    Missing file -> ``None`` (the zero-write guard: no directory, file or mtime is
    ever created here).  A malformed/symlinked file also answer ``None`` rather
    than raising into the daily loop.
    """

    path = _v04_state_file(inst_root)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        _v04_engine = _v04_engine_module()
        state, exists = _v04_engine.load_state_readonly(inst_root)
    except Exception:
        return None
    return state if exists else None


def _v04_engine_module() -> Any:
    try:
        from components.world.scripts import v04_neighborhood_living as _v04_engine
    except ImportError:
        import v04_neighborhood_living as _v04_engine  # type: ignore[no-redef]

    return _v04_engine


def _v04_weather(state: Mapping[str, Any]) -> str:
    env = state.get("environment") if isinstance(state.get("environment"), Mapping) else {}
    return str(env.get("weather") or "clear").strip().lower()


def _v04_engine_open_now(now_dt: datetime) -> bool:
    try:
        return bool(_v04_engine_module().is_poi_open("bookstore", now_dt))
    except Exception:
        return 10 <= now_dt.hour < 19


def _v08_context_cues_zero_write(inst_root: Path, now_dt: datetime) -> set[str]:
    """The environmental cues a saved routine can honestly match right now.

    ``any`` always matches.  Rain/chill come from the real v0.4 environment when it
    exists; morning/evening come from the observation clock.  ``focused`` is claimed
    only when ``v08_open_affordance.focused_state_zero_write`` sees a real, unsettled
    focus signal on disk (an in-progress workbench/reading/agency focus); that read is
    100% zero-write and a failure simply leaves the cue out.
    """

    cues: set[str] = {"any"}
    hour = int(now_dt.hour)
    if 5 <= hour < 11:
        cues.add("morning")
    if 17 <= hour < 24:
        cues.add("evening")
    try:
        if v08_open.focused_state_zero_write(inst_root):
            cues.add("focused")
    except Exception:  # noqa: BLE001 - an unreadable focus signal is not a focus claim
        pass
    state = _load_v04_neighborhood_state_zero_write(inst_root)
    if isinstance(state, Mapping):
        weather = _v04_weather(state)
        env = state.get("environment") if isinstance(state.get("environment"), Mapping) else {}
        season = str(env.get("season") or "").strip().lower()
        if weather in ("rain", "drizzle", "snow"):
            cues.add("rainy")
            cues.add("chilly")
        if season in ("winter", "late_autumn"):
            cues.add("chilly")
    return cues


V03_ENCOUNTERS_FILENAME = "v03_encounters.json"

#: v0.8 Wave 2 emergent echoes state file (zero-write reads, module-owned writes).
V08_EMERGENT_ECHOES_FILENAME = "v08_emergent_echoes.json"


class _EchoesActionUnavailable(Exception):
    """The emergent-echoes engine is absent or exposes no mutation door."""


def _emergent_echoes_module() -> Any:
    """Return the adjacent ``v08_emergent_echoes`` module, or ``None`` if absent.

    The bridge must import standalone (its own tests and every daily-loop consumer
    import it directly), so a missing/older engine degrades to "no echo candidates"
    instead of an ImportError at module load.
    """

    try:
        from components.life.scripts import v08_emergent_echoes as _echoes

        return _echoes
    except Exception:  # noqa: BLE001 - fall back to the flat sys.path import below
        pass
    try:
        import v08_emergent_echoes as _echoes  # type: ignore[no-redef]

        return _echoes
    except Exception:  # noqa: BLE001 - absence is a clean no-op, never a load failure
        return None


def _read_emergent_echoes_projection_zero_write(inst_root: Path) -> dict[str, Any]:
    """Read the echoes projection with 100% zero writes.

    Prefers the engine's subject-perspective zero-write reader (it sees private
    questions/sparks).  When the engine is absent, a plain read of
    ``<root>/phone/v08_emergent_echoes.json`` gives the same collection keys; no
    directory, file or mtime is ever created.
    """

    mod = _emergent_echoes_module()
    reader = getattr(mod, "read_emergent_echoes_zero_write", None) if mod is not None else None
    if callable(reader):
        try:
            projection = reader(inst_root, has_subject_capability=True)
            if isinstance(projection, Mapping):
                return dict(projection)
        except Exception:  # noqa: BLE001 - a broken file contributes no candidate
            return {}
    path = Path(inst_root) / "phone" / V08_EMERGENT_ECHOES_FILENAME
    if path.is_symlink():
        return {}
    doc = _read_optional_json_zero_write(path)
    return dict(doc) if isinstance(doc, Mapping) else {}


#: Candidate ``action_family`` values that plausibly continue an open craft act.
_CRAFT_HINT_ACTION_FAMILIES = frozenset({
    "compose_open_act", "open_living_trace", "open_shared_material",
})
_CRAFT_HINT_DOMAINS = ("craft", "creative", "writing", "prose", "paper", "repair", "draw", "sketch", "art")
_CRAFT_HINT_WORDS = ("写", "画", "稿", "手作", "修补", "拆", "拼", "抄", "纸", "笔", "材料", "布料")


def _candidate_is_craft_like(desc: Mapping[str, Any]) -> bool:
    if desc.get("creative_intensive") is True:
        return True
    if str(desc.get("action_family") or "") in _CRAFT_HINT_ACTION_FAMILIES:
        return True
    hay = " ".join(
        str(desc.get(key) or "")
        for key in ("clue_domain", "title_hint", "topic_key", "source_ref")
    ).lower()
    if any(token in hay for token in _CRAFT_HINT_DOMAINS):
        return True
    return any(word in hay for word in _CRAFT_HINT_WORDS)


def _annotate_workspace_scaffold_cues(
    raw_items: list[Any],
    scaffolds: Any,
) -> None:
    """Gently stamp a factual ``ready_at_hand`` cue onto related candidates.

    Purely additive and derived live from the physical surface: no phase score, no
    streak counter and no obligation.  When no related candidate exists nothing is
    touched -- the ready workspace still surfaces via the matter view / lived context.
    """

    if not isinstance(scaffolds, (list, tuple)):
        return
    ready: list[Mapping[str, Any]] = []
    for row in scaffolds:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("startup_ease") or "") != "ready_at_hand":
            continue
        if str(row.get("ready_domain") or "") not in (
            "craft_or_writing",
            "warm_tea_or_cooking",
        ):
            continue
        ready.append(row)
    if not ready:
        return
    row = ready[0]
    zone = str(row.get("zone_id") or row.get("surface_id") or "").strip()
    cue = str(row.get("summary_cn") or "").strip()[:80]
    basis = "V08_WORKSPACE_" + str(row.get("ready_domain") or "ready").upper()
    attached = 0
    for entry in raw_items:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        desc = entry[1]
        if not isinstance(desc, dict):
            continue
        family = str(desc.get("action_family") or "")
        ref = str(desc.get("source_ref") or "")
        related = family in _CRAFT_HINT_ACTION_FAMILIES or (bool(zone) and zone in ref)
        if not related:
            continue
        desc["scaffold_basis"] = basis
        desc["startup_ease"] = "ready_at_hand"
        if cue:
            desc["scaffold_summary_cn"] = cue
        attached += 1
        if attached >= 2:
            break


def _annotate_craft_genealogy_hints(
    raw_items: list[Any],
    inst_root: Path,
) -> None:
    """Annotate a craft-like candidate with the one applicable worked method.

    The private ``source_work_ref`` is never copied into the descriptor -- only the
    generalized operation/tip.  A boundary hit never annotates an applicable hint;
    at most the calm mismatch note is attached, so an old habit is not replayed where
    it fails.  Missing engine / empty corpus is a clean no-op.
    """

    mod = _emergent_echoes_module()
    matcher = getattr(mod, "match_applicable_craft_genealogy_zero_write", None) if mod else None
    if not callable(matcher):
        return
    for entry in raw_items:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        desc = entry[1]
        if not isinstance(desc, dict) or "craft_method_hint_cn" in desc:
            continue
        if not _candidate_is_craft_like(desc):
            continue
        situation = " ".join(
            str(desc.get(key) or "")
            for key in ("title_hint", "topic_key", "clue_domain", "source_ref")
        ).strip()
        if not situation:
            continue
        hay = situation.lower()
        domain = ""
        if "prose" in hay or "writing" in hay or "写" in hay or "稿" in hay:
            domain = "short_prose"
        elif "paper" in hay or "纸" in hay:
            domain = "paper_craft"
        elif "repair" in hay or "修补" in hay:
            domain = "repair"
        try:
            match = matcher(
                inst_root,
                domain_tag=domain,
                current_situation_cn=situation,
                include_private=True,
            )
        except Exception:  # noqa: BLE001 - a broken corpus contributes no hint
            continue
        if not isinstance(match, Mapping):
            continue
        mismatches = match.get("known_mismatches") or []
        if mismatches and isinstance(mismatches[0], Mapping):
            boundary = str(mismatches[0].get("mismatch_boundary_cn") or "").strip()
            if boundary:
                desc["craft_mismatch_note_cn"] = ("这段旧技法此刻未必合用：" + boundary)[:120]
        applicable = match.get("applicable_methods") or []
        if not applicable or not isinstance(applicable[0], Mapping):
            continue
        method = applicable[0]
        tip = str(method.get("operation_cn") or method.get("shareable_tip_cn") or "").strip()
        if not tip:
            continue
        desc["craft_method_hint_cn"] = ("可借旧技法：" + tip)[:120]
        genealogy_id = str(method.get("genealogy_id") or "").strip()
        if genealogy_id:
            desc["craft_genealogy_id"] = genealogy_id


#: v0.8 composable body private pack file (zero-write reads; module-owned writes).
V08_COMPOSABLE_BODY_FILENAME = "v08_composable_body_pack.json"


def _composable_body_module() -> Any:
    """Return the composable-body engine module, or ``None`` when it is absent.

    Worker 4A owns ``components/world/scripts/v08_composable_body.py``; that file may
    not have landed on disk yet, so the bridge must import standalone (its own tests and
    every daily-loop consumer import it directly) and degrade to "no body candidate"
    instead of raising ``ImportError`` at module load.
    """

    try:
        from components.world.scripts import v08_composable_body as _body

        return _body
    except Exception:  # noqa: BLE001 - fall back to the flat sys.path import below
        pass
    try:
        import v08_composable_body as _body  # type: ignore[no-redef]

        return _body
    except Exception:  # noqa: BLE001 - absence is a clean no-op, never a load failure
        return None


def _microzone_ecology_module() -> Any:
    """Return the v08 microzone-ecology engine, or ``None`` when it is absent.

    Worker 8A owns ``components/world/scripts/v08_microzone_ecology.py``; that file may
    not have landed yet, so the bridge must import standalone and degrade to "no
    microzone candidate" instead of raising ``ImportError`` at module load.
    """

    try:
        from components.world.scripts import v08_microzone_ecology as _mz

        return _mz
    except Exception:  # noqa: BLE001 - fall back to the flat sys.path import below
        pass
    try:
        import v08_microzone_ecology as _mz  # type: ignore[no-redef]

        return _mz
    except Exception:  # noqa: BLE001 - absence is a clean no-op, never a load failure
        return None


def _read_microzone_view_zero_write(inst_root: Path) -> Optional[Mapping[str, Any]]:
    """Read the microzone ecology projection once, 100% zero-write.

    Prefers the engine's own reader; accepts either the ``include_private`` or the
    ``has_subject_capability`` keyword, and a bare positional call as a last resort.
    A missing engine (or unreadable state) returns ``None`` -- never a write.
    """

    mod = _microzone_ecology_module()
    if mod is None:
        return None
    reader = getattr(mod, "read_microzone_ecology_view", None)
    if not callable(reader):
        return None
    try:
        # The daily loop is the subject's own life, so the subject door is correct.
        view = reader(inst_root, actor_side="subject")
    except TypeError:
        try:
            view = reader(inst_root, include_private=True)
        except TypeError:
            try:
                view = reader(inst_root)
            except Exception:  # noqa: BLE001
                return None
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    return view if isinstance(view, Mapping) else None


def _read_composable_body_projection_zero_write(inst_root: Path) -> dict[str, Any]:
    """Read the composable-body projection with 100% zero writes.

    Prefers the engine's subject-perspective zero-write reader.  When the engine is
    absent, a plain read of ``<root>/body/v08_composable_body_pack.json`` (or the legacy
    ``phone/`` path) gives the same flags; no directory, file or mtime is ever created.
    """

    mod = _composable_body_module()
    reader = getattr(mod, "read_composable_body_zero_write", None) if mod is not None else None
    if callable(reader):
        try:
            projection = reader(inst_root, has_subject_capability=True)
            if isinstance(projection, Mapping):
                return dict(projection)
        except Exception:  # noqa: BLE001 - a broken file contributes no candidate
            return {}
    for path in (
        Path(inst_root) / "body" / V08_COMPOSABLE_BODY_FILENAME,
        Path(inst_root) / "phone" / V08_COMPOSABLE_BODY_FILENAME,
    ):
        if path.is_symlink():
            continue
        doc = _read_optional_json_zero_write(path)
        if isinstance(doc, Mapping):
            return dict(doc)
    return {}


def _v08_body_flag(projection: Mapping[str, Any], pack: str, flag: str) -> bool:
    """Read one boolean flag from a pack block, a raw ``packs`` block, or the top level.

    The engine projection carries ``menstrual_cycle`` / ``nocturnal_emission`` directly;
    the raw on-disk state (the engine-absent fallback) nests the same packs under ``packs``.
    Both shapes must resolve so a missing engine never silently drops a real body need.
    """

    block = projection.get(pack)
    if isinstance(block, Mapping) and block.get(flag) is True:
        return True
    packs = projection.get("packs")
    if isinstance(packs, Mapping):
        nested = packs.get(pack)
        if isinstance(nested, Mapping) and nested.get(flag) is True:
            return True
    return projection.get(flag) is True


def _v08_body_candidate_from_projection(
    projection: Mapping[str, Any],
) -> tuple[Optional[CandidateRecord], Optional[dict[str, Any]]]:
    """Map the composable-body projection to at most one calm personal candidate.

    ``pending_garment_care`` becomes a garment-care act; otherwise
    ``needs_supply_change`` becomes a quiet cycle-supply act.  Never an outing, never
    strenuous, never a purchase.  A quiet/disabled pack yields ``None``.
    """

    if not isinstance(projection, Mapping):
        return None, None

    garment_care = _v08_body_flag(projection, "nocturnal_emission", "pending_garment_care")
    needs_supply = _v08_body_flag(projection, "menstrual_cycle", "needs_supply_change")

    if garment_care:
        nocturnal = projection.get("nocturnal_emission")
        raw_mode = ""
        if isinstance(nocturnal, Mapping):
            raw_mode = str(
                nocturnal.get("care_mode") or nocturnal.get("garment_care_mode") or ""
            )
        mode = raw_mode.strip().lower()
        if mode not in ("wash_garment", "change_garment"):
            mode = "wash_garment"
        ref = "v08_body:resolve_garment_care"
        rec = _make_personal_candidate(
            slug="v08_body_resolve_garment_care",
            target_ref=ref,
            causal_parent_refs=(ref,),
            provenance_refs=(ref,),
            availability_basis="V08_BODY_GARMENT_CARE_PENDING",
        )
        desc = {
            "candidate_id": rec.candidate_id,
            "clue_domain": "v08.composable_body.garment_care",
            "action_family": "resolve_garment_care",
            "source_ref": ref,
            "body_action_kind": "RESOLVE_GARMENT_CARE",
            "garment_care_mode": mode,
            "provenance_refs": list(rec.provenance_refs),
            "title_hint": (
                "先把夜里沾脏的衣物换下洗一洗"
                if mode == "wash_garment"
                else "先把夜里沾脏的衣物换下来"
            ),
            "topic_key": "body:garment_care",
            "requires_outing": False,
            "strenuous_outing": False,
            "creative_intensive": False,
            "impulse_purchase": False,
        }
        return rec, desc

    if needs_supply:
        ref = "v08_body:use_cycle_supply"
        rec = _make_personal_candidate(
            slug="v08_body_use_cycle_supply",
            target_ref=ref,
            causal_parent_refs=(ref,),
            provenance_refs=(ref,),
            availability_basis="V08_BODY_CYCLE_SUPPLY_NEEDED",
        )
        desc = {
            "candidate_id": rec.candidate_id,
            "clue_domain": "v08.composable_body.cycle_supply",
            "action_family": "use_cycle_supply",
            "source_ref": ref,
            "body_action_kind": "USE_CYCLE_SUPPLY",
            "provenance_refs": list(rec.provenance_refs),
            "title_hint": "顺手换一次周期用的补给",
            "topic_key": "body:cycle_supply",
            "requires_outing": False,
            "strenuous_outing": False,
            "creative_intensive": False,
            "impulse_purchase": False,
        }
        return rec, desc

    return None, None


def _emergent_echo_record_from_projection(
    projection: Any, action: str, fields: Mapping[str, Any]
) -> Optional[dict[str, Any]]:
    """Pick the one row an echo mutation touched out of the returned projection."""

    if not isinstance(projection, Mapping):
        return None
    if action == "revisit_future_question":
        key, id_field, wanted = (
            "future_self_questions",
            "question_id",
            fields.get("question_id"),
        )
    elif action == "settle_spark_attempt":
        key, id_field, wanted = (
            "style_and_interest_sparks",
            "spark_id",
            fields.get("spark_id"),
        )
    elif action == "plant_future_question":
        key, id_field, wanted = "future_self_questions", "question_id", None
    else:
        return None
    rows = [row for row in (projection.get(key) or []) if isinstance(row, Mapping)]
    if wanted:
        row = next((r for r in rows if str(r.get(id_field) or "") == str(wanted)), None)
        return copy.deepcopy(dict(row)) if row is not None else None
    return copy.deepcopy(dict(rows[-1])) if rows else None


def _call_emergent_echoes_action(
    inst_root: Path,
    action: str,
    fields: Mapping[str, Any],
    *,
    now_iso: str,
    operation_key: str,
) -> dict[str, Any]:
    """Call one echoes mutation once and normalise its receipt.

    The engine's own ``operation_key`` replay makes a replayed event a
    ``DUPLICATE`` (never a second write); the bridge consequence ledger already
    catches whole-event replays before this helper runs.  Raises
    ``_EchoesActionUnavailable`` when the module is absent.
    """

    mod = _emergent_echoes_module()
    if mod is None:
        raise _EchoesActionUnavailable()
    payload = dict(fields)
    payload["action"] = str(action)
    payload["operation_key"] = str(operation_key)[:128]
    mutator = getattr(mod, "apply_emergent_echoes_mutation", None)
    if callable(mutator):
        projection, status = mutator(
            inst_root, payload, actor_side="subject", now_iso=now_iso
        )
        return {
            "status": str(status or ""),
            "record": _emergent_echo_record_from_projection(projection, action, fields),
        }
    named = getattr(mod, action, None)
    if callable(named):
        try:
            result = named(inst_root, payload, actor_side="subject", now_iso=now_iso)
        except TypeError:
            result = named(inst_root, payload, now_iso=now_iso)
        return {
            "status": "APPLIED",
            "record": dict(result) if isinstance(result, Mapping) else None,
        }
    raise _EchoesActionUnavailable()


def _read_first_received_unopened_material_zero_write(
    inst_root: Path,
) -> Optional[dict[str, Any]]:
    """Read at most one partner-dropped material still awaiting an optional glance.

    Pure zero-write: a missing/symlinked/malformed ``<root>/phone/v03_encounters.json``
    yields ``None`` and touches no directory, file or mtime.  A ``WITHDRAWN`` (or already
    opened/used) material never becomes a candidate -- only ``RECEIVED_UNOPENED`` does.
    """

    doc = _read_optional_json_zero_write(Path(inst_root) / "phone" / V03_ENCOUNTERS_FILENAME)
    if not isinstance(doc, Mapping):
        return None
    materials = doc.get("shared_materials")
    if not isinstance(materials, list):
        return None
    for row in materials:
        if not isinstance(row, Mapping):
            continue
        material_id = str(row.get("material_id") or "").strip()
        if not material_id:
            continue
        if str(row.get("status") or "").strip().upper() != "RECEIVED_UNOPENED":
            continue
        return {
            "material_id": material_id,
            "title_cn": str(row.get("title_cn") or "").strip(),
            "topic_tag": str(row.get("topic_tag") or "").strip(),
            "page_count": row.get("page_count"),
        }
    return None


def _v04_make_desc(
    rec: CandidateRecord,
    *,
    clue_domain: str,
    action_family: str,
    source_ref: str,
    title: str,
    v04_action: str,
    v04_payload: Mapping[str, Any],
    v04_action_kind: str,
    requires_outing: bool = False,
    creative_intensive: bool = False,
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[CandidateRecord, dict[str, Any]]:
    desc: dict[str, Any] = {
        "candidate_id": rec.candidate_id,
        "clue_domain": clue_domain,
        "action_family": action_family,
        "source_ref": source_ref,
        "provenance_refs": list(rec.provenance_refs),
        "title": title[:120],
        "title_hint": title[:120],
        "topic_key": autonomy_rhythm_view._normalize_topic_key(title),
        "requires_outing": bool(requires_outing),
        "strenuous_outing": False,
        "creative_intensive": bool(creative_intensive),
        "impulse_purchase": False,
        #: 结算用的确定性动作与参数：resident 采纳后交给 settle_*，只走
        #: ``mutate_v04_neighborhood_view`` 的既有契约落账。
        "v04_action": v04_action,
        "v04_payload": {k: v for k, v in dict(v04_payload).items() if k != "action"},
        "v04_action_kind": v04_action_kind,
    }
    if extra:
        desc.update(dict(extra))
    return rec, desc


def _collect_v04_neighborhood_candidates(
    inst_root: Path,
    now_dt: datetime,
    *,
    observed_iso: str,
) -> list[tuple[CandidateRecord, dict[str, Any]]]:
    """Emit the six real v0.4 life opportunities from the persisted neighborhood state.

    Zero-write: when ``<inst_root>/world/v04_neighborhood_state.json`` does not
    exist this returns ``[]`` and touches nothing.  Every branch reads the raw
    state fields (``drying_items`` / ``potted_plant`` / ``craft_bench`` /
    ``public_life``) through the v0.4 engine's own helpers.
    """

    state = _load_v04_neighborhood_state_zero_write(inst_root)
    if state is None:
        return []
    engine = _v04_engine_module()
    weather = _v04_weather(state)
    items_out: list[tuple[CandidateRecord, dict[str, Any]]] = []

    # 1-3. 布巾：挂出 / 遇雨抢收 / 干透收回 -----------------------------------------
    drying = state.get("drying_items") if isinstance(state.get("drying_items"), Mapping) else {}
    for item_id in sorted(drying):
        item = drying.get(item_id)
        if not isinstance(item, Mapping):
            continue
        support = str(item.get("support_id") or "home_rack")
        spec = engine.CLOTH_SUPPORTS.get(support) or engine.CLOTH_SUPPORTS["home_rack"]
        shelter = str(spec.get("shelter") or "indoor")
        moisture = float(item.get("moisture", 75.0))
        band = engine.moisture_to_band(moisture)
        label = str(item.get("name") or item_id)

        # 1. 挂在室内且仍湿：晴天挂去露天晾晒绳，微雨挂去有檐门廊。
        if (
            support == "home_rack"
            and not bool(item.get("sleeping_dry"))
            and band in ("wet", "damp")
            and weather in ("clear", "drizzle")
        ):
            target = "yard_line" if weather == "clear" else "porch_hook"
            src = f"v04_courtyard_hang:{item_id}"
            rec = _make_personal_candidate(
                slug=f"v04_hang_cloth_{_safe_token(item_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:courtyard_hanging", src),
                availability_basis="COURTYARD_HANG_WET_CLOTH",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.courtyard_cloth",
                action_family="hang_drying_cloth",
                source_ref=src,
                title=f"把室内的{label}挂到{'院子晾衣绳' if target == 'yard_line' else '门廊檐下'}晾干",
                v04_action="hang_or_collect_cloth",
                v04_payload={"operation": "hang", "item_id": item_id, "target_support": target},
                v04_action_kind="HANG_DRYING_CLOTH",
                extra={"target_support": target},
            ))

        # 2. 遇雨抢收（保持既有命名 ``v04_courtyard_cloth:<id>``），门廊一步之遥。
        rescue_emitted = False
        if shelter == "open_air" and weather in V04_RAIN_WEATHER:
            src = f"v04_courtyard_cloth:{item_id}"
            rec = _make_personal_candidate(
                slug=f"v04_rescue_cloth_{_safe_token(item_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:courtyard_drying", src),
                availability_basis="RAIN_RESCUE_DRYING_CLOTH",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.courtyard_cloth",
                action_family="courtyard_care",
                source_ref=src,
                title=f"趁下雨把院子晾着的{label}抢收进有檐门廊",
                v04_action="hang_or_collect_cloth",
                v04_payload={"operation": "move", "item_id": item_id, "target_support": "porch_hook"},
                v04_action_kind="RESCUE_DRYING_CLOTH",
                extra={"target_support": "porch_hook"},
            ))
            rescue_emitted = True

        # 3. 干透收回屋内（露天或门廊上晾着且已达标）。
        if not rescue_emitted and band == "dry" and support in ("yard_line", "porch_hook"):
            src = f"v04_courtyard_collect:{item_id}"
            rec = _make_personal_candidate(
                slug=f"v04_collect_cloth_{_safe_token(item_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:courtyard_drying", src),
                availability_basis="DRY_CLOTH_COLLECTABLE",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.courtyard_cloth",
                action_family="collect_dried_cloth",
                source_ref=src,
                title=f"把晾干的{label}收回屋里台面",
                v04_action="hang_or_collect_cloth",
                v04_payload={"operation": "collect", "item_id": item_id, "target_support": "home_rack"},
                v04_action_kind="COLLECT_DRIED_CLOTH",
                extra={"target_support": "home_rack"},
            ))

    # 4. 廊下/院子盆栽照料 -----------------------------------------------------------
    plant = state.get("potted_plant") if isinstance(state.get("potted_plant"), Mapping) else {}
    if plant:
        plant_id = str(plant.get("plant_id") or "porch_fern")
        place = str(plant.get("place_id") or "porch")
        hydration = float(plant.get("hydration", 70.0))
        plant_stage = str(plant.get("stage") or "").strip().lower()
        if place == "yard" and weather in ("rain", "drizzle"):
            src = f"v04_porch_plant:{plant_id}"
            rec = _make_personal_candidate(
                slug=f"v04_porch_plant_{_safe_token(plant_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:potted_plant", src),
                availability_basis="POTTED_PLANT_NEEDS_SHELTER",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.potted_plant",
                action_family="care_porch_plant",
                source_ref=src,
                title="把露天小院里的盆栽搬回有檐门廊挡雨",
                v04_action="water_or_move_plant",
                v04_payload={"operation": "move", "plant_id": plant_id, "target_place": "porch"},
                v04_action_kind="CARE_PORCH_PLANT",
                extra={"plant_operation": "move"},
            ))
        elif plant_stage == "wilting_recoverable" or hydration <= V04_PLANT_THIRSTY_HYDRATION:
            src = f"v04_porch_plant:{plant_id}"
            rec = _make_personal_candidate(
                slug=f"v04_porch_plant_{_safe_token(plant_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:potted_plant", src),
                availability_basis="POTTED_PLANT_THIRSTY",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.potted_plant",
                action_family="care_porch_plant",
                source_ref=src,
                title=(
                    "门廊那盆常春藤叶尖发蔫了，可以浇点水让它缓过来"
                    if plant_stage == "wilting_recoverable"
                    else "给门廊那盆常春藤浇一点水"
                ),
                v04_action="water_or_move_plant",
                v04_payload={"operation": "water", "plant_id": plant_id, "water_amount": 45.0},
                v04_action_kind="CARE_PORCH_PLANT",
                extra={"plant_operation": "water"},
            ))

    # 5. 书桌纸盒 / 卡片手作（高负荷创作：生病发烧时由 Wave 1 创作门拦下） -------------
    craft = state.get("craft_bench") if isinstance(state.get("craft_bench"), Mapping) else {}
    if craft:
        box = craft.get("card_organizer_box") if isinstance(craft.get("card_organizer_box"), Mapping) else {}
        box_id = str(box.get("box_id") or "card_organizer_box")
        next_step = {
            "prepared": "divider_cut",
            "divider_cut": "trial_fitted",
            "trial_fitted": "finished",
        }.get(str(box.get("step") or ""))
        craft_src: Optional[str] = None
        craft_payload: dict[str, Any] = {}
        craft_title = ""
        if next_step:
            craft_src = f"v04_home_craft:{box_id}"
            craft_payload = {"operation": "box_step", "target_step": next_step}
            if next_step == "divider_cut":
                craft_payload["units"] = 1
            craft_title = "在书桌前继续折叠拼装旧纸盒的下一道工序"
        else:
            cards = craft.get("cards") if isinstance(craft.get("cards"), Mapping) else {}
            for card_id in sorted(cards):
                card = cards.get(card_id)
                if not isinstance(card, Mapping):
                    continue
                if int(card.get("inscribed_revision") or 0) > 0:
                    continue
                draft = card.get("draft_text")
                if isinstance(draft, str) and draft.strip():
                    craft_src = f"v04_home_craft:{card_id}"
                    craft_payload = {"operation": "inscribe_card", "card_id": card_id, "text": draft}
                    craft_title = "把纸卡草稿誊写到卡面上"
                    break
        if craft_src is not None:
            rec = _make_personal_candidate(
                slug=f"v04_home_craft_{_safe_token(craft_src.split(':', 1)[-1])}",
                target_ref=craft_src,
                causal_parent_refs=(craft_src,),
                provenance_refs=("v04_neighborhood:craft_bench", craft_src),
                availability_basis="HOME_CRAFT_UNFINISHED",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.home_craft",
                action_family="advance_home_craft",
                source_ref=craft_src,
                title=craft_title,
                v04_action="craft_paper_or_box",
                v04_payload=craft_payload,
                v04_action_kind="ADVANCE_HOME_CRAFT",
                # 关键联动 Wave 1：这是高强度创作，发烧 creative_block 时会被硬拒绝。
                creative_intensive=True,
            ))

    # 6. 街角共享架工具归还 / 旧书店借还跑腿 ----------------------------------------
    public = state.get("public_life") if isinstance(state.get("public_life"), Mapping) else {}
    shelf = (
        public.get("shared_shelf", {}).get("items")
        if isinstance(public.get("shared_shelf"), Mapping)
        else {}
    )
    for tool_id in sorted(shelf or {}):
        tool = shelf.get(tool_id)
        if not isinstance(tool, Mapping):
            continue
        if str(tool.get("status") or "") == "borrowed" and str(tool.get("holder") or "") == "subject":
            src = f"v04_errand_return:{tool_id}"
            label = str(tool.get("name") or tool_id)
            rec = _make_personal_candidate(
                slug=f"v04_errand_tool_{_safe_token(tool_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:shared_shelf", src),
                availability_basis="SHARED_TOOL_RETURN_DUE",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.shared_shelf_loan",
                action_family="return_neighborhood_loan",
                source_ref=src,
                title=f"把借来的{label}还回街角共享架",
                v04_action="shared_shelf_loan",
                v04_payload={"operation": "return", "item_id": tool_id},
                v04_action_kind="RETURN_NEIGHBORHOOD_LOAN",
                # 关键联动：外跑腿，生病 blocks_strenuous_outing 时会被门控拦下。
                requires_outing=True,
                extra={"v04_loan_kind": "shared_shelf"},
            ))

    bookstore = (
        public.get("bookstore") if isinstance(public.get("bookstore"), Mapping) else {}
    )
    books = bookstore.get("books") if isinstance(bookstore.get("books"), Mapping) else {}
    store_open = _v04_engine_open_now(now_dt)
    for book_id in sorted(books or {}):
        book = books.get(book_id)
        if not isinstance(book, Mapping):
            continue
        if str(book.get("status") or "") != "borrowed" or str(book.get("holder") or "") != "subject":
            continue
        src = f"v04_errand_return:{book_id}"
        title = str(book.get("title") or book_id)
        payload: dict[str, Any] = {
            "service": "return_book",
            "book_id": book_id,
        }
        if not store_open:
            payload["via_return_box"] = True
        rec = _make_personal_candidate(
            slug=f"v04_errand_book_{_safe_token(book_id)}",
            target_ref=src,
            causal_parent_refs=(src,),
            provenance_refs=("v04_neighborhood:bookstore_loan", src),
            availability_basis="BOOKSTORE_LOAN_RETURN_DUE",
            observed_iso=observed_iso,
        )
        items_out.append(_v04_make_desc(
            rec,
            clue_domain="v04.neighborhood.bookstore_loan",
            action_family="return_neighborhood_loan",
            source_ref=src,
            title=(
                f"把借来的《{title}》还回旧书店"
                if store_open
                else f"把借来的《{title}》投进书店还书箱（已闭店）"
            ),
            v04_action="bookstore_service",
            v04_payload=payload,
            v04_action_kind="RETURN_NEIGHBORHOOD_LOAN",
            requires_outing=True,
            extra={"v04_loan_kind": "bookstore", "via_return_box": not store_open},
        ))

    # 6b. Ch.26/Ch.53：店主柜台有真实动静时，最多发 1 条可选的书店走动机会。
    #     只有「未核实的传闻」或「卡片位置建议（待店主看 / 已定 / 已挪）」才算动静；
    #     平静的默认轮值不占配额，闭店时也从不发这条。
    if store_open:
        shop_life = (
            bookstore.get("shopkeeper_life")
            if isinstance(bookstore.get("shopkeeper_life"), Mapping)
            else {}
        )
        shop_ref_id = ""
        shop_title = ""
        if shop_life:
            for rumor in shop_life.get("heard_rumors") or []:
                if not isinstance(rumor, Mapping) or rumor.get("verified_firsthand"):
                    continue
                rid = str(rumor.get("rumor_id") or "").strip()
                if rid:
                    shop_ref_id = rid
                    shop_title = "去旧书店和店主聊两句，顺便问问那条还没核实的传闻"
                    break
            if not shop_ref_id:
                for sug in reversed(list(shop_life.get("card_spot_suggestions") or [])):
                    if not isinstance(sug, Mapping):
                        continue
                    status = str(sug.get("status") or "").strip().lower()
                    if status not in ("pending_shopkeeper_review", "adopted", "relocated"):
                        continue
                    sid = str(sug.get("suggestion_id") or sug.get("card_id") or "").strip()
                    if sid:
                        shop_ref_id = sid
                        shop_title = "去旧书店看看那张纸卡最后摆在了哪儿"
                        break
        if shop_ref_id:
            browse_id = sorted(books)[0] if books else "book_botanical_plates"
            src = f"v04_bookstore_shopkeeper:{_safe_token(shop_ref_id)}"
            rec = _make_personal_candidate(
                slug=f"v04_bookstore_shopkeeper_{_safe_token(shop_ref_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:bookstore_shopkeeper", src),
                availability_basis="BOOKSTORE_SHOPKEEPER_HAS_NEWS",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.bookstore_shopkeeper",
                action_family="visit_bookstore_shopkeeper",
                source_ref=src,
                title=shop_title,
                v04_action="bookstore_service",
                v04_payload={"service": "browse_book", "book_id": browse_id},
                v04_action_kind="VISIT_BOOKSTORE_SHOPKEEPER",
                requires_outing=True,
                extra={"shopkeeper_ref_id": shop_ref_id},
            ))

    # 6c. Batch E 第二位独立街坊居民（saeki_aoi，街角焙煎咖啡屋店主）：
    #     仅在 world/v04_neighborhood_state.json 已存在、咖啡屋当前营业（is_open == True），
    #     且存在未实地核实的公开传闻或尚未回应的伴侣杯垫便签时，至多发出 1 条 requires_outing=True 候选。
    cafe_raw = (
        public.get("cafe_resident")
        if isinstance(public.get("cafe_resident"), Mapping)
        else {}
    )
    try:
        cafe_proj = engine.project_second_resident_zero_write(
            inst_root, now_iso=observed_iso
        )
    except Exception:
        cafe_proj = {}
    raw_open_flag = (
        cafe_raw.get("is_open")
        if "is_open" in cafe_raw
        else cafe_raw.get("is_open_now")
    )
    cafe_open = (
        bool(raw_open_flag)
        if isinstance(raw_open_flag, bool)
        else bool(
            cafe_proj.get("is_open_now")
            if "is_open_now" in cafe_proj
            else cafe_proj.get("is_open")
        )
    )
    if cafe_open and isinstance(cafe_proj, Mapping):
        cafe_cue_key = ""
        cafe_title = ""
        cafe_payload: dict[str, Any] = {"operation": "check_neighbor_schedule"}

        for rumor in cafe_proj.get("heard_rumors") or []:
            if not isinstance(rumor, Mapping) or rumor.get("verified_firsthand"):
                continue
            ep_st = str(rumor.get("epistemic_status") or "hearsay_unverified").strip().lower()
            if ep_st not in ("hearsay_unverified", "unverified", ""):
                continue
            rid = str(rumor.get("rumor_id") or rumor.get("route_id") or "").strip()
            if rid:
                cafe_cue_key = _safe_token(rid, "rumor")
                cafe_title = "去街角焙煎咖啡屋和店主聊两句，顺便问问那条还没实地看过的传闻"
                break

        if not cafe_cue_key:
            op_receipts = (
                state.get("operation_receipts")
                if isinstance(state.get("operation_receipts"), Mapping)
                else {}
            )
            subject_coaster_ids: set[str] = set()
            for rcpt in op_receipts.values():
                if not isinstance(rcpt, Mapping):
                    continue
                if str(rcpt.get("actor_side") or "").strip().lower() not in ("subject", "her"):
                    continue
                res_obj = rcpt.get("result")
                if isinstance(res_obj, Mapping) and res_obj.get("coaster_id"):
                    subject_coaster_ids.add(str(res_obj["coaster_id"]))

            settled_cafe_refs: set[str] = set()
            ledger_doc = _read_consequence_ledger(inst_root)
            for l_rcpt in (ledger_doc.get("receipts") or {}).values():
                if not isinstance(l_rcpt, Mapping):
                    continue
                l_res = l_rcpt.get("result")
                if not isinstance(l_res, Mapping):
                    continue
                l_det = l_res.get("details") if isinstance(l_res.get("details"), Mapping) else {}
                nb_det = l_det.get("v04_neighborhood") if isinstance(l_det.get("v04_neighborhood"), Mapping) else {}
                cue_ref = str(nb_det.get("cue_ref") or "").strip()
                if cue_ref.startswith("v04_cafe_resident:"):
                    settled_cafe_refs.add(cue_ref)

            raw_coasters = list(cafe_raw.get("coaster_notes") or cafe_proj.get("coaster_notes") or [])
            for coaster in reversed(raw_coasters):
                if not isinstance(coaster, Mapping):
                    continue
                cid = str(coaster.get("coaster_id") or coaster.get("id") or "").strip()
                if not cid:
                    continue
                if (
                    coaster.get("acknowledged") is True
                    or coaster.get("acknowledged_by_subject") is True
                    or str(coaster.get("status") or "").strip().lower()
                    in ("acknowledged", "read_by_subject", "archived")
                ):
                    continue
                explicit_author = str(
                    coaster.get("author_side")
                    or coaster.get("actor_side")
                    or coaster.get("by")
                    or ""
                ).strip().lower()
                if explicit_author in ("subject", "her") or cid in subject_coaster_ids:
                    continue
                safe_cid = _safe_token(cid, "coaster")
                if f"v04_cafe_resident:{safe_cid}" in settled_cafe_refs:
                    continue
                cafe_cue_key = safe_cid
                cafe_title = "去街角焙煎咖啡屋看看吧台杯垫夹上的那张便签"
                break

        if cafe_cue_key:
            cafe_src = f"v04_cafe_resident:{cafe_cue_key}"
            cafe_rec = _make_personal_candidate(
                slug=f"v04_cafe_resident_{cafe_cue_key}",
                target_ref=cafe_src,
                causal_parent_refs=(cafe_src,),
                provenance_refs=("v04_neighborhood:cafe_resident", cafe_src),
                availability_basis="CAFE_RESIDENT_HAS_PUBLIC_CUE",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                cafe_rec,
                clue_domain="v04.neighborhood.cafe_resident",
                action_family="visit_cafe_resident",
                source_ref=cafe_src,
                title=cafe_title,
                v04_action="interact_cafe_resident",
                v04_payload=cafe_payload,
                v04_action_kind="VISIT_CAFE_RESIDENT",
                requires_outing=True,
                extra={"cafe_cue_key": cafe_cue_key},
            ))

    # 7. 批次 B：途中移动推进 / 折返（只按 journey 自己宣告的状态，不预知前方未触发的路障） -----
    journey = state.get("journey") if isinstance(state.get("journey"), Mapping) else {}
    journey_state = str(journey.get("state") or "")
    if journey_state in ("in_transit", "paused_at_node"):
        legs = journey.get("legs") if isinstance(journey.get("legs"), list) else []
        active_idx = int(journey.get("active_leg_index") or 0)
        active_route = str(journey.get("active_route_id") or "").strip()
        if not active_route and 0 <= active_idx < len(legs) and isinstance(legs[active_idx], Mapping):
            active_route = str(legs[active_idx].get("route_id") or "").strip()
        route_id = _safe_token(active_route or "transit")
        if journey_state == "paused_at_node":
            if str(journey.get("halt_reason") or "") == "ROADBLOCK_ENCOUNTERED":
                operation = "turn_back"
                title = "前路暂时过不去，沿原路折返"
            else:
                operation = "resume"
                title = "把刚才放下的那段路重新走起来"
        else:
            operation = "advance"
            target_place = str(journey.get("target_place_id") or "目的地")
            title = f"继续朝{target_place}往前走，让脚下的路一点点推进"
        src = f"v04_journey_step:{route_id}"
        journey_rec = _make_personal_candidate(
            slug=f"v04_journey_{route_id}",
            target_ref=src,
            causal_parent_refs=(src,),
            provenance_refs=("v04_neighborhood:journey", src),
            availability_basis="NEIGHBORHOOD_JOURNEY_IN_PROGRESS",
            observed_iso=observed_iso,
        )
        items_out.append(_v04_make_desc(
            journey_rec,
            clue_domain="v04.neighborhood.journey",
            action_family="advance_neighborhood_journey",
            source_ref=src,
            title=title,
            v04_action="advance_or_turnback_journey",
            v04_payload={"operation": operation},
            v04_action_kind="ADVANCE_NEIGHBORHOOD_JOURNEY",
            requires_outing=True,
            extra={"journey_state": journey_state, "journey_operation": operation},
        ))

    # 8. 批次 E：公共告示阅读（未读且未过期；正文不提前泄露） --------------------------
    notices = public.get("public_notices") if isinstance(public.get("public_notices"), list) else []
    for notice in notices:
        if not isinstance(notice, Mapping):
            continue
        notice_id = str(notice.get("notice_id") or "").strip()
        if not notice_id or notice.get("read_by_subject") is True:
            continue
        expires_dt = _parse_iso(notice.get("expires_at"))
        if notice.get("expired") is True or (expires_dt is not None and expires_dt <= now_dt):
            continue
        src = f"v04_public_notice:{_safe_token(notice_id)}"
        notice_title = str(notice.get("title") or "").strip()[:60]
        rec = _make_personal_candidate(
            slug=f"v04_notice_{_safe_token(notice_id)}",
            target_ref=src,
            causal_parent_refs=(src,),
            provenance_refs=("v04_neighborhood:public_notice", src),
            availability_basis="PUBLIC_NOTICE_UNREAD",
            observed_iso=observed_iso,
        )
        items_out.append(_v04_make_desc(
            rec,
            clue_domain="v04.neighborhood.public_notice",
            action_family="read_public_notice",
            source_ref=src,
            title=(f"去街角告示栏读一下那张还没读的纸：{notice_title}"
                   if notice_title else "去街角告示栏读一下那张还没读的纸"),
            v04_action="read_public_notice",
            v04_payload={"notice_id": notice_id},
            v04_action_kind="READ_PUBLIC_NOTICE",
            # 阅读不是创作冲刺：即使急性生病也不该被创造力门控错拦。
            requires_outing=False,
            creative_intensive=False,
            extra={"notice_id": notice_id, "body_withheld_until_read": True},
        ))

    # 9. 批次 F1：地点小册记录（亲历过、尚未记入、小册未暂停） --------------------------
    booklet = public.get("place_booklet") if isinstance(public.get("place_booklet"), Mapping) else {}
    if booklet and str(booklet.get("status") or "") != "paused":
        entries = booklet.get("entries") if isinstance(booklet.get("entries"), Mapping) else {}
        places = state.get("places") if isinstance(state.get("places"), Mapping) else {}
        for place_id in sorted(places):
            place = places.get(place_id)
            if not isinstance(place, Mapping):
                continue
            if str(place.get("knowledge_status") or "") != "visited" or place_id in entries:
                continue
            place_name = str(place.get("name") or place_id).strip() or place_id
            src = f"v04_place_booklet:{_safe_token(place_id)}"
            rec = _make_personal_candidate(
                slug=f"v04_booklet_{_safe_token(place_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:place_booklet", src),
                availability_basis="PLACE_BOOKLET_ENTRY_DUE",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.place_booklet",
                action_family="update_place_booklet",
                source_ref=src,
                title=f"把亲自去过的{place_name}记进安静读书地点小册",
                v04_action="update_place_booklet",
                v04_payload={
                    "operation": "record_entry",
                    "place_id": place_id,
                    "quietness_band": "quiet",
                    "impression": f"亲自走过{place_name}，记下此刻的安静与光线。",
                    # 不替她把私人小册公开给伴侣。
                    "shared_with_partner": False,
                },
                v04_action_kind="UPDATE_PLACE_BOOKLET",
                # 写小册是安静的整理工作，不是外跑腿；但属于高强度创作（发烧时会被门控拦下）。
                requires_outing=False,
                creative_intensive=True,
                extra={"place_id": place_id, "booklet_shared_with_partner": False},
            ))

    # 10. 批次 F2：书店卡片展陈（已誊写、未展陈、书店营业中） ---------------------------
    cards = craft.get("cards") if isinstance(craft.get("cards"), Mapping) else {}
    if store_open:
        for card_id in sorted(cards or {}):
            card = cards.get(card_id)
            if not isinstance(card, Mapping):
                continue
            if int(card.get("inscribed_revision") or 0) < 1:
                continue
            if card.get("displayed_at_bookstore") is True:
                continue
            src = f"v04_bookstore_display:{_safe_token(card_id)}"
            card_title = str(card.get("title") or "手写纸卡").strip()[:60]
            rec = _make_personal_candidate(
                slug=f"v04_display_card_{_safe_token(card_id)}",
                target_ref=src,
                causal_parent_refs=(src,),
                provenance_refs=("v04_neighborhood:bookstore_display", src),
                availability_basis="BOOKSTORE_DISPLAY_CARD_READY",
                observed_iso=observed_iso,
            )
            items_out.append(_v04_make_desc(
                rec,
                clue_domain="v04.neighborhood.bookstore_display",
                action_family="submit_display_card",
                source_ref=src,
                title=f"把誊写好的「{card_title}」带到旧书店展陈板上",
                v04_action="bookstore_service",
                v04_payload={"service": "submit_display_card", "card_id": card_id},
                v04_action_kind="SUBMIT_DISPLAY_CARD",
                # 外跑腿：生病 blocks_strenuous_outing 时会被门控拦下；闭店时不生成该候选。
                requires_outing=True,
                creative_intensive=False,
                extra={"card_id": card_id},
            ))

    return items_out


# ---------------------------------------------------------------------------
# Stage 2 (Pillars 1 & 2): Acute Body Snapshot, Desire Arbitration & Care
# ---------------------------------------------------------------------------


def _strict_float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    if fval != fval:  # NaN guard
        return None
    return fval


def _normalize_desire_text_key(text: Any) -> str:
    return _DESIRE_TEXT_NORM_RE.sub("", str(text or "").strip()).lower()


def _read_body_runtime_snapshot_zero_write(
    inst_root: Path,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Read acute body runtime.json + somatic_comfort garment wetness 100% zero-write."""
    paths = _resolve_instance_paths_zero_write(inst_root)
    rt_candidates: list[Path] = []
    if paths is not None:
        rt_candidates.append(paths.body_runtime_json)
    rt_candidates.extend([
        inst_root / "one-day" / "world-home" / "data" / "body" / "runtime.json",
        inst_root / "world-home" / "data" / "body" / "runtime.json",
        inst_root / "data" / "body" / "runtime.json",
    ])
    rt_doc: Optional[dict[str, Any]] = None
    for cand in rt_candidates:
        loaded = _read_optional_json_zero_write(cand)
        if isinstance(loaded, Mapping):
            rt_doc = dict(loaded)
            break

    somatic_block = (
        rt_doc.get("somatic")
        if isinstance(rt_doc, Mapping) and isinstance(rt_doc.get("somatic"), Mapping)
        else {}
    )
    lived_needs_block = (
        rt_doc.get("lived_needs")
        if isinstance(rt_doc, Mapping) and isinstance(rt_doc.get("lived_needs"), Mapping)
        else {}
    )
    levels_block = (
        lived_needs_block.get("levels")
        if isinstance(lived_needs_block.get("levels"), Mapping)
        else {}
    )
    recovery_block = (
        rt_doc.get("recovery")
        if isinstance(rt_doc, Mapping) and isinstance(rt_doc.get("recovery"), Mapping)
        else {}
    )
    rec_state = str(recovery_block.get("state") or "inactive").strip().lower()
    rec_mode = str(recovery_block.get("mode") or "").strip().lower()

    fatigue = _strict_float_or_none(somatic_block.get("fatigue"))
    warmth = _strict_float_or_none(somatic_block.get("warmth"))
    comfort = _strict_float_or_none(somatic_block.get("comfort"))
    tension = _strict_float_or_none(somatic_block.get("tension"))
    sensory_load = _strict_float_or_none(somatic_block.get("sensory_load"))
    hunger = _strict_float_or_none(levels_block.get("hunger"))
    thirst = _strict_float_or_none(levels_block.get("thirst"))
    toilet = _strict_float_or_none(levels_block.get("toilet"))

    # Read somatic_comfort.json zero-write for worn damp/wet outerwear.
    worn_damp_outerwear = False
    garment_id = ""
    garment_label = ""
    garment_dryness = ""
    som_path = somatic_comfort_view.state_path(inst_root)
    som_doc = _read_optional_json_zero_write(som_path)
    if isinstance(som_doc, Mapping):
        derived_garment: Optional[Mapping[str, Any]] = None
        try:
            derived_garment = somatic_comfort_view.derive_garment(som_doc, now_iso)
        except Exception:
            raw_g = som_doc.get("garment")
            derived_garment = raw_g if isinstance(raw_g, Mapping) else None
        if isinstance(derived_garment, Mapping):
            g_state = str(derived_garment.get("state") or "").strip().lower()
            g_dry = str(derived_garment.get("dryness") or "").strip().lower()
            if g_state == "wear" and g_dry in ("damp", "wet"):
                worn_damp_outerwear = True
                garment_id = str(derived_garment.get("garment_id") or "daily_coat").strip()[:64]
                garment_label = str(derived_garment.get("label") or "外衣").strip()[:40]
                garment_dryness = g_dry

    return {
        "present": rt_doc is not None,
        "fatigue": round(fatigue, 4) if fatigue is not None else 0.0,
        "warmth": round(warmth, 4) if warmth is not None else 0.5,
        "comfort": round(comfort, 4) if comfort is not None else 0.7,
        "tension": round(tension, 4) if tension is not None else 0.0,
        "sensory_load": round(sensory_load, 4) if sensory_load is not None else 0.0,
        "lived_needs_enabled": bool(lived_needs_block),
        "hunger": round(hunger, 4) if hunger is not None else 0.0,
        "thirst": round(thirst, 4) if thirst is not None else 0.0,
        "toilet": round(toilet, 4) if toilet is not None else 0.0,
        "recovery_state": rec_state,
        "recovery_mode": rec_mode,
        "sleep_like_active": rec_state == "active" and rec_mode == "sleep_like",
        "worn_damp_outerwear": worn_damp_outerwear,
        "garment_id": garment_id,
        "garment_label": garment_label,
        "garment_dryness": garment_dryness,
    }


def _normalize_desire_item(
    raw: Mapping[str, Any],
    *,
    source_store: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    status = str(raw.get("status") or raw.get("state") or "active").strip().lower()
    if status not in ("active", "live", "retained", "turn_only", ""):
        return None
    text = str(
        raw.get("text")
        or raw.get("expression")
        or raw.get("title")
        or raw.get("summary_zh")
        or ""
    ).strip()[:120]
    if not text:
        return None

    raw_domain = str(raw.get("domain") or "").strip().lower()
    category = str(raw.get("category") or "").strip().lower()
    if not category:
        category = _DESIRE_DOMAIN_TO_CATEGORY.get(raw_domain, "curiosity_mystery")

    layer = str(raw.get("layer") or "").strip().lower()
    if layer not in ("micro_craving", "mid_itch", "long_aspiration"):
        persistence = str(raw.get("persistence") or "").strip().lower()
        layer = "micro_craving" if persistence == "turn_only" else "mid_itch"

    desire_id = str(
        raw.get("desire_id")
        or raw.get("source_desire_id")
        or raw.get("spark_desire_id")
        or raw.get("concern_ref")
        or raw.get("id")
        or ""
    ).strip()
    if not desire_id:
        digest = hashlib.sha256(f"{source_store}:{layer}:{text}".encode("utf-8")).hexdigest()[:12]
        desire_id = f"des_{digest}"

    est_cost_jpy = 0
    raw_jpy = raw.get("estimated_cost_jpy")
    if isinstance(raw_jpy, (int, float)) and not isinstance(raw_jpy, bool) and raw_jpy > 0:
        est_cost_jpy = int(round(float(raw_jpy)))
    else:
        raw_cents = raw.get("estimated_cost_cents") or raw.get("price_cents")
        if isinstance(raw_cents, (int, float)) and not isinstance(raw_cents, bool) and raw_cents > 0:
            est_cost_jpy = max(1, int(round(float(raw_cents) / 100.0)))

    if "requires_outing" in raw and isinstance(raw.get("requires_outing"), bool):
        requires_outing = bool(raw["requires_outing"])
    else:
        cost_scale = str(raw.get("action_cost_scale") or "").strip().lower()
        requires_outing = bool(
            category == "restlessness_outing" or cost_scale == "macro_outing"
        )

    if "strenuous" in raw and isinstance(raw.get("strenuous"), bool):
        strenuous = bool(raw["strenuous"])
    else:
        strenuous = bool(requires_outing or category in _DESIRE_STRENUOUS_CATEGORIES)

    creative_intensive = bool(
        raw.get("creative_intensive") is True
        or category == "craft_ambition"
        or raw_domain == "creation"
    )

    intensity_val = _strict_float_or_none(raw.get("intensity"))
    intensity = round(max(0.0, min(1.0, intensity_val)), 4) if intensity_val is not None else 0.65

    raw_fc = raw.get("friction_count")
    friction_count = int(raw_fc) if isinstance(raw_fc, int) and not isinstance(raw_fc, bool) and raw_fc >= 0 else 0

    world_anchor = str(raw.get("world_anchor") or "").strip()[:120]
    if not world_anchor and isinstance(raw.get("origin_refs"), list) and raw["origin_refs"]:
        world_anchor = str(raw["origin_refs"][0] or "").strip()[:120]

    return {
        "desire_id": desire_id[:96],
        "layer": layer,
        "category": category,
        "text": text,
        "origin": str(raw.get("origin") or raw.get("origin_kind") or "self_emerged").strip()[:64],
        "world_anchor": world_anchor,
        "intensity": intensity,
        "friction_count": friction_count,
        "estimated_cost_jpy": est_cost_jpy,
        "requires_outing": requires_outing,
        "strenuous": strenuous,
        "creative_intensive": creative_intensive,
        "status": "active",
        "shared": bool(raw.get("shared", True)),
        "source_store": source_store,
        "concern_ref": str(raw.get("concern_ref") or "").strip()[:96] or None,
    }


def _dedupe_normalized_desires(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        did = str(item.get("desire_id") or "").strip()
        cref = str(item.get("concern_ref") or "").strip()
        tkey = _normalize_desire_text_key(item.get("text"))
        if (did and did in seen_ids) or (cref and cref in seen_ids) or (tkey and tkey in seen_texts):
            continue
        if did:
            seen_ids.add(did)
        if cref:
            seen_ids.add(cref)
        if tkey:
            seen_texts.add(tkey)
        out.append(item)
    return out


def _collect_active_desires_zero_write(
    inst_root: Path,
    *,
    now_iso: str,
    desire_proj: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Read and deduplicate active desires from BOTH v05_inner_spark.json and everyday_desires.json (0 writes)."""
    collected_norm: list[dict[str, Any]] = []

    # 1. Canonical v05_inner_spark.json (preferred richer schema)
    spark_path = inst_root / "life" / "v05_inner_spark.json"
    if spark_path.is_file() and not spark_path.is_symlink():
        spark_rows: list[Mapping[str, Any]] = []
        try:
            try:
                from components.life.scripts import v05_dreams_desires_foreshadow as _v05_spark_mod
            except ImportError:
                import v05_dreams_desires_foreshadow as _v05_spark_mod  # type: ignore[no-redef]
            proj_spark = _v05_spark_mod.project_v05_inner_spark_state(
                inst_root,
                has_subject_capability=True,
                now_iso=now_iso,
            )
            desires_blk = proj_spark.get("desires") if isinstance(proj_spark, Mapping) else None
            if isinstance(desires_blk, Mapping) and isinstance(desires_blk.get("items"), list):
                spark_rows = [r for r in desires_blk["items"] if isinstance(r, Mapping)]
        except Exception:
            raw_spark = _read_optional_json_zero_write(spark_path)
            if isinstance(raw_spark, Mapping) and isinstance(raw_spark.get("desires"), list):
                spark_rows = [r for r in raw_spark["desires"] if isinstance(r, Mapping)]
        for row in spark_rows:
            norm = _normalize_desire_item(row, source_store="v05_inner_spark")
            if norm is not None:
                collected_norm.append(norm)

    # 2. everyday_desires.json (Ch. 35 concerns + projected desires)
    ed_paths = (
        inst_root / "life" / "everyday_desires.json",
        inst_root / "phone" / "everyday_desires.json",
    )
    ed_exists = any(p.is_file() and not p.is_symlink() for p in ed_paths)
    if ed_exists:
        ed_view: Optional[Mapping[str, Any]] = desire_proj
        if ed_view is None:
            try:
                ed_view = v03_desires.project_everyday_desires(inst_root, now_iso=now_iso)
            except Exception:
                ed_view = None
        ed_rows: list[Mapping[str, Any]] = []
        if isinstance(ed_view, Mapping):
            for key in ("retained_concerns", "turn_only_concerns", "projected_desires", "active_desires", "desires"):
                seq = ed_view.get(key)
                if isinstance(seq, list):
                    ed_rows.extend(r for r in seq if isinstance(r, Mapping))
        if not ed_rows:
            for ed_p in ed_paths:
                raw_ed = _read_optional_json_zero_write(ed_p)
                if isinstance(raw_ed, Mapping):
                    for key in ("retained_concerns", "turn_only_buffer", "projected_desires", "active_desires", "desires"):
                        seq = raw_ed.get(key)
                        if isinstance(seq, list):
                            ed_rows.extend(r for r in seq if isinstance(r, Mapping))
                    break
        for row in ed_rows:
            norm = _normalize_desire_item(row, source_store="everyday_desires")
            if norm is not None:
                collected_norm.append(norm)

    return _dedupe_normalized_desires(collected_norm)


def _build_zero_cost_home_alternative(desire: Mapping[str, Any]) -> str:
    cat = str(desire.get("category") or "").strip().lower()
    if cat == "appetite_craving":
        return "手头先省着点，在家用现成食材做点简单的热食或泡杯茶"
    if cat in ("curiosity_mystery", "craft_ambition"):
        return "先不额外花钱，翻翻手边旧书或用家里现成材料试做一小段"
    if cat == "social_longing":
        return "手头预算偏紧，改成在屋里泡杯茶写几句轻松近况留着分享"
    return "手头预算偏紧，先把要花钱的念头换成家里零花费的轻松小事"


def _build_indoor_sheltered_alternative(desire: Mapping[str, Any]) -> str:
    cat = str(desire.get("category") or "").strip().lower()
    if cat == "appetite_craving":
        return "外头正落雨，先不出门，在屋里热一杯暖饮配点现成小点心"
    if cat == "craft_ambition":
        return "外头风雨大，先留在室内书桌或檐下安静整理手头材料"
    return "外头正落雨刮风，先留在窗边或檐下听会儿雨、泡杯温茶慢慢翻书"


def arbitrate_desire_candidates_for_loop(
    day_root: Path | str,
    *,
    desires: Optional[Sequence[Mapping[str, Any]]] = None,
    economy_info: Optional[Mapping[str, Any]] = None,
    illness_info: Optional[Mapping[str, Any]] = None,
    body_snapshot: Optional[Mapping[str, Any]] = None,
    weather: str = "clear",
    now_iso: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Arbitrate active desires against wallet tightness, rain/storm, and fatigue/illness (100% zero-write).

    Returns ``(emitted_candidates, arbitration_notes)``.
    """
    inst_root, _life_root = resolve_day_and_life_roots(day_root)
    _dt, eff_now_iso, _ld, _tz = _resolve_observation_clock_zero_write(inst_root, now_iso)

    if desires is not None and len(desires) > 0:
        norm_list: list[dict[str, Any]] = []
        for raw in desires:
            norm = _normalize_desire_item(
                raw, source_store=str(raw.get("source_store") or "caller_supplied")
            )
            if norm is not None:
                norm_list.append(norm)
        active_desires = _dedupe_normalized_desires(norm_list)
    else:
        active_desires = _collect_active_desires_zero_write(inst_root, now_iso=eff_now_iso)

    if not active_desires:
        return [], []

    eco = dict(economy_info) if isinstance(economy_info, Mapping) else {}
    ill = dict(illness_info) if isinstance(illness_info, Mapping) else {}
    body = dict(body_snapshot) if isinstance(body_snapshot, Mapping) else {}

    # Fatigue extraction (supports both flat body_snapshot and runtime.json shape)
    raw_fatigue = _strict_float_or_none(body.get("fatigue"))
    if raw_fatigue is None and isinstance(body.get("somatic"), Mapping):
        raw_fatigue = _strict_float_or_none(body["somatic"].get("fatigue"))
    fatigue = raw_fatigue if raw_fatigue is not None else 0.0

    cap_env = ill.get("capability_envelope") if isinstance(ill.get("capability_envelope"), Mapping) else {}
    raw_ill_phase = str(ill.get("phase") or ill.get("phase_family") or "").strip().lower()
    is_actively_ill = bool(
        ill.get("is_actively_ill") is True
        or ILLNESS_PHASE_FAMILY_MAP.get(raw_ill_phase, "healthy") in ACTIVE_ILLNESS_FAMILIES
        or cap_env.get("blocks_strenuous_outing") is True
    )
    blocks_adult_intimacy = bool(cap_env.get("blocks_adult_intimacy") is True or is_actively_ill)
    is_fatigued_or_ill = fatigue >= ACUTE_FATIGUE_THRESHOLD or is_actively_ill

    # Economy check: enforce when explicit margin/budget_tight is provided or wallet is active
    has_explicit_margin = (
        "discretionary_margin_cents" in eco and eco.get("discretionary_margin_cents") is not None
    )
    eco_uninitialized = (
        "world_economy_initialized" in eco
        and eco.get("world_economy_initialized") is False
        and eco.get("economy_active") is not True
        and not bool(eco.get("budget_tight"))
    )
    check_wallet = bool(eco.get("budget_tight")) or (has_explicit_margin and not eco_uninitialized)
    discretionary_margin_cents = int(eco.get("discretionary_margin_cents") or 0)
    budget_tight = bool(eco.get("budget_tight"))

    norm_weather = str(weather or "clear").strip().lower()
    is_rain_or_storm = norm_weather in DESIRE_RAIN_STORM_WEATHERS

    # Sort active desires deterministically by intensity descending (higher first)
    ordered_desires = sorted(
        active_desires,
        key=lambda d: (-float(d.get("intensity") or 0.0), str(d.get("desire_id") or "")),
    )

    emitted_candidates: list[dict[str, Any]] = []
    arbitration_notes: list[dict[str, Any]] = []
    rest_alt_emitted = False

    for desire in ordered_desires:
        if len(emitted_candidates) >= MAX_ARBITRATED_DESIRE_CANDIDATES:
            break
        did = str(desire["desire_id"])
        did_tok = _safe_token(did, "desire")
        text = str(desire["text"])
        category = str(desire["category"])
        layer = str(desire["layer"])
        cost_jpy = int(desire["estimated_cost_jpy"])
        requires_outing = bool(desire["requires_outing"])
        strenuous = bool(desire["strenuous"])
        creative_intensive = bool(desire["creative_intensive"])

        # 1. Fatigue >= 0.72 or acute illness -> defer strenuous/creative/outing desires
        if is_fatigued_or_ill and (
            requires_outing or strenuous or creative_intensive or cost_jpy > 0
        ):
            arbitration_notes.append({
                "desire_id": did,
                "original_text": text,
                "category": category,
                "layer": layer,
                "disposition": "deferred_for_rest",
                "friction_reason": "fatigue_or_illness",
                "increment_friction_on_settle": True,
                "frustration_debt": 0,
                "substituted_action": "rest_or_warm_drink",
                "fatigue": round(fatigue, 4),
                "is_actively_ill": is_actively_ill,
                "reason_cn": "眼下精力偏低或身体不适，先把费力的念头缓一缓，优先歇一会儿或喝杯温水",
            })
            if not rest_alt_emitted:
                alt_ref = f"v05_desire_alt:rest_{did_tok}"
                emitted_candidates.append({
                    "candidate_id": f"cand:v05_desire_alt_rest_{did_tok}",
                    "slug": f"v05_desire_alt_rest_{did_tok}",
                    "source_ref": alt_ref,
                    "target_ref": alt_ref,
                    "clue_domain": "v05.desire.arbitrated_alternative",
                    "action_family": "pursue_desire_alternative",
                    "alternative_kind": "rest_or_warm_drink",
                    "care_event_kind": "warm_hydration",
                    "rest_mode": "rest",
                    "desire_id": did,
                    "substituted_from_desire_id": did,
                    "category": "cozy_comfort",
                    "original_category": category,
                    "layer": layer,
                    "title_hint": "先顺着身体缓一缓，喝口温水靠着歇一会儿",
                    "topic_key": "休息喝温水",
                    "estimated_cost_jpy": 0,
                    "price_cents": 0,
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                    "arbitration_disposition": "deferred_for_rest",
                    "friction_reason": "fatigue_or_illness",
                    "is_alternative": True,
                    "zero_debt": True,
                    "frustration_debt": 0,
                    "begs_partner_when_tight": False,
                    "availability_basis": "DESIRE_DEFERRED_FOR_REST_CARE",
                    "provenance_refs": [f"v05_desire:{did_tok}", alt_ref],
                    "observed_iso": eff_now_iso,
                })
                rest_alt_emitted = True
            continue

        # 2. Wallet Tightness -> block paid impulse-spend & down-scale to zero-cost home alternative
        if check_wallet and cost_jpy > 0 and (
            budget_tight or discretionary_margin_cents < cost_jpy * 100
        ):
            alt_title = _build_zero_cost_home_alternative(desire)
            arbitration_notes.append({
                "desire_id": did,
                "original_text": text,
                "category": category,
                "layer": layer,
                "disposition": "downscaled_zero_cost_home",
                "friction_reason": "budget_tight",
                "increment_friction_on_settle": True,
                "frustration_debt": 0,
                "begs_partner_when_tight": False,
                "blocked_cost_jpy": cost_jpy,
                "discretionary_margin_cents": discretionary_margin_cents,
                "alternative_title": alt_title,
                "reason_cn": "手头预算偏紧，先把需要花钱的念头改换成零花费的居家替代",
            })
            alt_ref = f"v05_desire_alt:home_{did_tok}"
            emitted_candidates.append({
                "candidate_id": f"cand:v05_desire_alt_home_{did_tok}",
                "slug": f"v05_desire_alt_home_{did_tok}",
                "source_ref": alt_ref,
                "target_ref": alt_ref,
                "clue_domain": "v05.desire.arbitrated_alternative",
                "action_family": "pursue_desire_alternative",
                "alternative_kind": "zero_cost_home",
                "desire_id": did,
                "substituted_from_desire_id": did,
                "category": category,
                "layer": layer,
                "title_hint": alt_title[:120],
                "topic_key": autonomy_rhythm_view._normalize_topic_key(alt_title),
                "estimated_cost_jpy": 0,
                "price_cents": 0,
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
                "arbitration_disposition": "downscaled_zero_cost_home",
                "friction_reason": "budget_tight",
                "is_alternative": True,
                "zero_debt": True,
                "frustration_debt": 0,
                "begs_partner_when_tight": False,
                "availability_basis": "DESIRE_BUDGET_TIGHT_HOME_ALTERNATIVE",
                "provenance_refs": [f"v05_desire:{did_tok}", alt_ref],
                "observed_iso": eff_now_iso,
            })
            continue

        # 3. Rain / Storm -> substitute indoor/sheltered cozy alternative for outing desires
        if requires_outing and is_rain_or_storm:
            alt_title = _build_indoor_sheltered_alternative(desire)
            arbitration_notes.append({
                "desire_id": did,
                "original_text": text,
                "category": category,
                "layer": layer,
                "disposition": "substituted_indoor_sheltered",
                "friction_reason": "weather_rain",
                "increment_friction_on_settle": True,
                "frustration_debt": 0,
                "weather": norm_weather,
                "alternative_title": alt_title,
                "reason_cn": "外面正下雨刮风，把外出的念头换成室内或檐下的安稳去处",
            })
            alt_ref = f"v05_desire_alt:rain_{did_tok}"
            emitted_candidates.append({
                "candidate_id": f"cand:v05_desire_alt_rain_{did_tok}",
                "slug": f"v05_desire_alt_rain_{did_tok}",
                "source_ref": alt_ref,
                "target_ref": alt_ref,
                "clue_domain": "v05.desire.arbitrated_alternative",
                "action_family": "pursue_desire_alternative",
                "alternative_kind": "indoor_sheltered",
                "desire_id": did,
                "substituted_from_desire_id": did,
                "category": "cozy_comfort",
                "original_category": category,
                "layer": layer,
                "title_hint": alt_title[:120],
                "topic_key": autonomy_rhythm_view._normalize_topic_key(alt_title),
                "estimated_cost_jpy": 0,
                "price_cents": 0,
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
                "arbitration_disposition": "substituted_indoor_sheltered",
                "friction_reason": "weather_rain",
                "is_alternative": True,
                "zero_debt": True,
                "frustration_debt": 0,
                "begs_partner_when_tight": False,
                "availability_basis": "DESIRE_RAIN_SHELTERED_ALTERNATIVE",
                "provenance_refs": [f"v05_desire:{did_tok}", alt_ref],
                "observed_iso": eff_now_iso,
            })
            continue

        # 4. Private intimacy blocked during illness
        if category == "intimate_warmth" and blocks_adult_intimacy:
            arbitration_notes.append({
                "desire_id": did,
                "original_text": text,
                "category": category,
                "layer": layer,
                "disposition": "blocked_by_illness",
                "friction_reason": "fatigue_or_illness",
                "increment_friction_on_settle": False,
                "frustration_debt": 0,
                "reason_cn": "身体恢复期暂停亲密意向，先以休息为主",
            })
            continue

        # 5. Unconstrained -> emit directly
        src_ref = f"v05_desire:{did_tok}"
        arbitration_notes.append({
            "desire_id": did,
            "original_text": text,
            "category": category,
            "layer": layer,
            "disposition": "admitted",
            "friction_reason": None,
            "increment_friction_on_settle": False,
            "frustration_debt": 0,
        })
        emitted_candidates.append({
            "candidate_id": f"cand:v05_desire_{did_tok}",
            "slug": f"v05_desire_{did_tok}",
            "source_ref": src_ref,
            "target_ref": src_ref,
            "clue_domain": f"v05.desire.{category}",
            "action_family": "adult_intimacy" if category == "intimate_warmth" else "pursue_autonomous_desire",
            "desire_id": did,
            "category": category,
            "layer": layer,
            "world_anchor": desire.get("world_anchor") or "",
            "intensity": desire.get("intensity", 0.65),
            "title_hint": text[:120],
            "topic_key": autonomy_rhythm_view._normalize_topic_key(text),
            "estimated_cost_jpy": cost_jpy,
            "price_cents": cost_jpy * 100,
            "requires_outing": requires_outing,
            "strenuous_outing": strenuous,
            "creative_intensive": creative_intensive,
            "impulse_purchase": False,
            "arbitration_disposition": "admitted",
            "friction_reason": None,
            "is_alternative": False,
            "zero_debt": True,
            "frustration_debt": 0,
            "begs_partner_when_tight": False,
            "availability_basis": "AUTONOMOUS_DESIRE_ARBITRATED_OPEN",
            "provenance_refs": [src_ref, f"desire_store:{desire.get('source_store', 'v05_inner_spark')}"],
            "observed_iso": eff_now_iso,
        })

    return emitted_candidates, arbitration_notes


def record_desire_arbitration_friction(
    day_root: Path | str,
    *,
    desire_id: str,
    friction_reason: str,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Increment ``friction_count`` on settlement/tick without adding frustration debt."""
    inst_root, _ = resolve_day_and_life_roots(day_root)
    clean_id = str(desire_id or "").strip()
    clean_reason = str(friction_reason or "").strip()[:64]
    if not clean_id or not clean_reason:
        return {"ok": False, "updated": False, "frustration_debt": 0, "llm_calls": 0}

    _dt, eff_now_iso, _ld, _tz = _resolve_observation_clock_zero_write(inst_root, now_iso)
    spark_path = inst_root / "life" / "v05_inner_spark.json"
    if spark_path.is_symlink() or not spark_path.is_file():
        return {"ok": True, "updated": False, "reason_code": "SPARK_STATE_ABSENT", "frustration_debt": 0, "llm_calls": 0}

    try:
        try:
            from components.life.scripts import v05_dreams_desires_foreshadow as _spark_mod
        except ImportError:
            import v05_dreams_desires_foreshadow as _spark_mod  # type: ignore[no-redef]
        helper = getattr(_spark_mod, "record_desire_friction", None)
        if callable(helper):
            res = helper(
                inst_root,
                desire_id=clean_id,
                friction_reason=clean_reason,
                now_iso=eff_now_iso,
            )
            if isinstance(res, Mapping):
                out = dict(res)
                out.setdefault("updated", bool(out.get("changed")))
                out.setdefault("frustration_debt", 0)
                out.setdefault("llm_calls", 0)
                return out
    except Exception:
        pass

    with _tx_guard.state_file_lock(spark_path):
        doc = _read_optional_json_zero_write(spark_path)
        if not isinstance(doc, Mapping) or not isinstance(doc.get("desires"), list):
            return {"ok": True, "updated": False, "frustration_debt": 0, "llm_calls": 0}
        updated_doc = copy.deepcopy(dict(doc))
        matched: Optional[dict[str, Any]] = None
        for row in updated_doc["desires"]:
            if isinstance(row, dict) and str(row.get("desire_id") or "") == clean_id:
                prev = row.get("friction_count")
                new_count = (int(prev) if isinstance(prev, int) and not isinstance(prev, bool) and prev >= 0 else 0) + 1
                row["friction_count"] = new_count
                row["last_friction_reason"] = clean_reason
                row["updated_at"] = eff_now_iso
                matched = row
                break
        if matched is None:
            return {"ok": True, "updated": False, "frustration_debt": 0, "llm_calls": 0}
        updated_doc["revision"] = int(updated_doc.get("revision") or 0) + 1
        updated_doc["updated_at"] = eff_now_iso
        _atomic_write_json(spark_path, updated_doc)
        return {
            "ok": True,
            "updated": True,
            "desire_id": clean_id,
            "friction_count": matched["friction_count"],
            "friction_reason": clean_reason,
            "frustration_debt": 0,
            "llm_calls": 0,
        }


def _collect_acute_body_care_candidates_zero_write(
    inst_root: Path,
    *,
    body_snapshot: Mapping[str, Any],
    illness_info: Mapping[str, Any],
    observed_iso: str,
) -> list[tuple[CandidateRecord, dict[str, Any]]]:
    """Emit acute body-care candidates strictly when runtime.json / somatic_comfort.json shows real acute need."""
    out: list[tuple[CandidateRecord, dict[str, Any]]] = []
    rt_present = bool(body_snapshot.get("present"))
    fatigue = float(body_snapshot.get("fatigue") or 0.0)
    hunger = float(body_snapshot.get("hunger") or 0.0)
    thirst = float(body_snapshot.get("thirst") or 0.0)
    warmth = float(body_snapshot.get("warmth") if body_snapshot.get("warmth") is not None else 0.5)
    sleep_like_active = bool(body_snapshot.get("sleep_like_active"))
    worn_damp_outerwear = bool(body_snapshot.get("worn_damp_outerwear"))

    # 1. Worn damp/wet outerwear -> hang_outerwear
    if worn_damp_outerwear:
        g_id = str(body_snapshot.get("garment_id") or "daily_coat").strip() or "daily_coat"
        g_lbl = str(body_snapshot.get("garment_label") or "外衣").strip() or "外衣"
        g_dry = str(body_snapshot.get("garment_dryness") or "damp").strip().lower()
        src_ref = "v03_body_care:hang_outerwear"
        rec = _make_personal_candidate(
            slug="v03_body_care_hang_outerwear",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("somatic_comfort:garment", src_ref),
            availability_basis="SOMATIC_DAMP_OUTERWEAR_CARE",
            observed_iso=observed_iso,
        )
        title = (
            f"把淋湿的{g_lbl}换下来挂起晾干"
            if g_dry == "wet"
            else f"把身上微潮的{g_lbl}换下来挂起晾干"
        )
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.body_care.outerwear",
                "action_family": "hang_outerwear",
                "body_care_action": "hang_outerwear",
                "source_ref": src_ref,
                "garment_id": g_id,
                "garment_dryness": g_dry,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": title[:120],
                "topic_key": "换下微潮外衣晾干",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    if not rt_present:
        return out

    # 2. High fatigue (>= 0.72) and not already in sleep_like -> REST(sleep_like)
    if fatigue >= ACUTE_FATIGUE_THRESHOLD and not sleep_like_active:
        src_ref = "v03_body_care:rest_sleep_like"
        rec = _make_personal_candidate(
            slug="v03_body_care_rest_sleep_like",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("body_runtime:fatigue_high", src_ref),
            availability_basis="BODY_RUNTIME_FATIGUE_HIGH_REST",
            observed_iso=observed_iso,
        )
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.body_care.rest_sleep_like",
                "action_family": "acute_body_rest_sleep_like",
                "body_care_action": "REST(sleep_like)",
                "source_ref": src_ref,
                "world_rest_target": "bodyact:rest:start:sleep_like",
                "rest_mode": "sleep_like",
                "care_event_kind": "bed_rest_block",
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": "实在累得有些撑不住，先躺下好好睡一觉",
                "topic_key": "躺下休息入睡",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    # 3. High thirst (>= 0.70) or thermal chill (warmth <= 0.35) -> drink_warm_water
    if thirst >= ACUTE_THIRST_THRESHOLD or warmth <= ACUTE_WARMTH_CHILL_THRESHOLD:
        src_ref = "v03_body_care:drink_warm_water"
        rec = _make_personal_candidate(
            slug="v03_body_care_drink_warm_water",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("body_runtime:thirst_or_chill", src_ref),
            availability_basis="BODY_RUNTIME_THIRST_OR_CHILL_CARE",
            observed_iso=observed_iso,
        )
        if warmth <= ACUTE_WARMTH_CHILL_THRESHOLD and thirst >= ACUTE_THIRST_THRESHOLD:
            title = "口干又有点发凉，倒一杯温水慢慢喝暖暖身子"
        elif warmth <= ACUTE_WARMTH_CHILL_THRESHOLD:
            title = "手脚有点发凉，倒一杯温水捧着暖暖身子"
        else:
            title = "嗓子有些干了，倒一杯温水慢慢喝下解渴"
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.body_care.warm_water",
                "action_family": "drink_warm_water",
                "body_care_action": "drink_warm_water",
                "source_ref": src_ref,
                "care_event_kind": "warm_hydration",
                "rest_mode": "rest",
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": title[:120],
                "topic_key": "喝温水解渴暖身",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    # 4. High hunger (>= 0.70) -> eat_warm_meal
    if hunger >= ACUTE_HUNGER_THRESHOLD:
        src_ref = "v03_body_care:eat_warm_meal"
        rec = _make_personal_candidate(
            slug="v03_body_care_eat_warm_meal",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("body_runtime:hunger_high", src_ref),
            availability_basis="BODY_RUNTIME_HUNGER_HIGH_CARE",
            observed_iso=observed_iso,
        )
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.body_care.meal",
                "action_family": "eat_warm_meal",
                "body_care_action": "eat_warm_meal",
                "source_ref": src_ref,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": "肚子有些饿了，先找点热乎吃食垫一垫",
                "topic_key": "吃点热食垫肚子",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    # 5. Thermal overheat (warmth >= 0.75) -> cool_down_air
    if warmth >= ACUTE_WARMTH_OVERHEAT_THRESHOLD:
        src_ref = "v03_body_care:cool_down_air"
        rec = _make_personal_candidate(
            slug="v03_body_care_cool_down_air",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("body_runtime:overheat", src_ref),
            availability_basis="BODY_RUNTIME_OVERHEAT_CARE",
            observed_iso=observed_iso,
        )
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.body_care.thermal_cool",
                "action_family": "cool_down_air",
                "body_care_action": "cool_down_air",
                "source_ref": src_ref,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": "屋里偏热有些发闷，开会儿窗透透气降降温",
                "topic_key": "开窗透气降温",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    return out


# ---------------------------------------------------------------------------
# v0.9 Living Agency & Evolving World Producer (100% zero-write, barrier-filtered)
# ---------------------------------------------------------------------------

V09_ACTION_FAMILIES: frozenset[str] = frozenset({
    "pickup_arrived_commitment",
    "commit_blind_choice",
    "reflect_unformed_stance",
    "tidy_custom_shelf_or_tin",
})

V09_INDOOR_LOW_URGENCY_FAMILIES: frozenset[str] = frozenset({
    "commit_blind_choice",
    "reflect_unformed_stance",
    "tidy_custom_shelf_or_tin",
})


def _collect_v09_living_agency_and_world_candidates_zero_write(
    inst_root: Path,
    *,
    effective_now_iso: str,
    now_dt: datetime,
    forget_barrier: Mapping[str, Any],
    existing_raw_items: Sequence[tuple[CandidateRecord, dict[str, Any]]],
) -> list[tuple[CandidateRecord, dict[str, Any]]]:
    """Collect bounded v0.9 daily-loop candidates with 100% zero disk writes (Blueprint §6.3)."""
    if bool(forget_barrier.get("fail_closed")):
        return []

    out: list[tuple[CandidateRecord, dict[str, Any]]] = []
    from v02_representation_lease_forget import is_entity_forget_blocked as _is_rlf_blocked

    # 1. pickup_arrived_commitment: ARRIVED_HOLDING reservation before hold_until_at
    cmt_file = inst_root / "world" / "npc_commitments_state.json"
    if cmt_file.is_file() and not cmt_file.is_symlink():
        try:
            try:
                from components.world.scripts import world_commitments_engine as _wce
            except ImportError:
                import world_commitments_engine as _wce  # type: ignore[no-redef]
            cmt_view = _wce.read_world_commitments_zero_write(
                inst_root, actor_side="subject", now_iso=effective_now_iso
            )
            if isinstance(cmt_view, Mapping) and not cmt_view.get("forget_barrier_fail_closed"):
                arrived_rows: list[Mapping[str, Any]] = []
                for row in cmt_view.get("active_commitments") or ():
                    if not isinstance(row, Mapping):
                        continue
                    if str(row.get("kind") or "") != "BOOK_OR_ITEM_RESERVATION":
                        continue
                    if str(row.get("state") or "") != "ARRIVED_HOLDING":
                        continue
                    req_by = str(row.get("requested_by") or "subject").strip().lower()
                    if req_by not in ("subject", "partner"):
                        continue
                    hold_dt = _parse_iso(row.get("hold_until_at"))
                    if hold_dt is not None and now_dt >= hold_dt:
                        continue
                    cid = str(row.get("commitment_id") or "").strip()
                    if not cid or _is_rlf_blocked(row, forget_barrier) or _is_rlf_blocked(cid, forget_barrier):
                        continue
                    arrived_rows.append(row)
                arrived_rows.sort(
                    key=lambda r: (
                        0 if str(r.get("requested_by") or "") == "subject" else 1,
                        str(r.get("hold_until_at") or "9999"),
                        str(r.get("commitment_id") or ""),
                    )
                )
                if arrived_rows:
                    row = arrived_rows[0]
                    cid = str(row["commitment_id"])
                    venue_id = str(row.get("venue_id") or "bookstore")
                    title_cn = str(row.get("title_cn") or "预订物品").strip()
                    src_ref = f"v09_commitment:pickup:{cid}"
                    rec = _make_personal_candidate(
                        slug=f"v09_pickup_{_safe_token(cid)}",
                        target_ref=src_ref,
                        causal_parent_refs=(src_ref, f"commitment:{cid}"),
                        provenance_refs=(src_ref, f"venue:{_safe_token(venue_id)}"),
                        availability_basis="V09_RESERVATION_ARRIVED_HOLDING",
                        observed_iso=effective_now_iso,
                    )
                    out.append((
                        rec,
                        {
                            "candidate_id": rec.candidate_id,
                            "clue_domain": "v09.world_commitments.reservation",
                            "action_family": "pickup_arrived_commitment",
                            "source_ref": src_ref,
                            "commitment_id": cid,
                            "venue_id": venue_id,
                            "npc_id": str(row.get("npc_id") or ""),
                            "hold_until_at": row.get("hold_until_at"),
                            "availability_basis": "V09_RESERVATION_ARRIVED_HOLDING",
                            "provenance_refs": list(rec.provenance_refs),
                            "title_hint": f"去取到店留存的「{title_cn}」"[:120],
                            "topic_key": title_cn[:80],
                            "requires_outing": True,
                            "strenuous_outing": True,
                            "creative_intensive": False,
                            "impulse_purchase": False,
                        },
                    ))
        except Exception:
            pass

    # Pre-read dynamic catalog reading booklets (zero-write) for stance/taxonomy cues
    booklets: list[Mapping[str, Any]] = []
    cat_file = inst_root / "world" / "dynamic_content_catalog.json"
    if cat_file.is_file() and not cat_file.is_symlink():
        try:
            try:
                from components.world.scripts import world_content_catalog as _wcc
            except ImportError:
                import world_content_catalog as _wcc  # type: ignore[no-redef]
            cat_snap = _wcc.read_dynamic_catalog_zero_write(inst_root, now_iso=effective_now_iso)
            if isinstance(cat_snap, Mapping) and not cat_snap.get("barrier_fail_closed"):
                for item in cat_snap.get("items") or ():
                    if isinstance(item, Mapping) and str(item.get("template_id") or "") == "reading_booklet":
                        if not _is_rlf_blocked(item, forget_barrier):
                            booklets.append(item)
        except Exception:
            booklets = []

    # 2 & 3. commit_blind_choice & reflect_unformed_stance
    stance_file = inst_root / "life" / "v09_true_stance_state.json"
    if stance_file.is_file() and not stance_file.is_symlink():
        try:
            try:
                from components.life.scripts import v09_true_stance_engine as _tse
            except ImportError:
                import v09_true_stance_engine as _tse  # type: ignore[no-redef]
            st_view = _tse.read_true_stance_zero_write(
                inst_root, actor_side="subject", now_iso=effective_now_iso
            )
            if isinstance(st_view, Mapping) and not st_view.get("forget_barrier_fail_closed"):
                # 2. commit_blind_choice: awaiting TA's sealed choice
                for bc in st_view.get("active_blind_choices") or ():
                    if not isinstance(bc, Mapping):
                        continue
                    bc_status = str(bc.get("status") or "")
                    if bc_status not in ("OPEN_AWAITING_BOTH", "OPEN_PARTNER_COMMITTED"):
                        continue
                    choice_id = str(bc.get("choice_id") or "").strip()
                    if not choice_id or _is_rlf_blocked(bc, forget_barrier) or _is_rlf_blocked(choice_id, forget_barrier):
                        continue
                    topic_cn = str(bc.get("topic_cn") or "共同选择").strip()
                    src_ref = f"v09_blind_choice:commit:{choice_id}"
                    rec = _make_personal_candidate(
                        slug=f"v09_blind_choice_{_safe_token(choice_id)}",
                        target_ref=src_ref,
                        causal_parent_refs=(src_ref, f"choice:{choice_id}"),
                        provenance_refs=(src_ref,),
                        availability_basis="V09_BLIND_CHOICE_AWAITING_SUBJECT",
                        observed_iso=effective_now_iso,
                    )
                    out.append((
                        rec,
                        {
                            "candidate_id": rec.candidate_id,
                            "clue_domain": "v09.true_stance.blind_choice",
                            "action_family": "commit_blind_choice",
                            "source_ref": src_ref,
                            "choice_id": choice_id,
                            "topic_cn": topic_cn,
                            "options": copy.deepcopy(list(bc.get("options") or [])),
                            "availability_basis": "V09_BLIND_CHOICE_AWAITING_SUBJECT",
                            "provenance_refs": list(rec.provenance_refs),
                            "title_hint": f"先独立决定并封存想法：{topic_cn}"[:120],
                            "topic_key": topic_cn[:80],
                            "requires_outing": False,
                            "strenuous_outing": False,
                            "creative_intensive": False,
                            "impulse_purchase": False,
                        },
                    ))
                    break

                # 3. reflect_unformed_stance: UNFORMED / TENTATIVE topic matching reading booklet,
                #    recent receipt, or unevaluated partner persuasion
                ledger_doc = _read_optional_json_zero_write(_consequence_ledger_path(inst_root)) or {}
                ledger_receipts = ledger_doc.get("receipts") if isinstance(ledger_doc.get("receipts"), Mapping) else {}
                recent_evidence_hay: list[tuple[str, str, str]] = []
                for bk in booklets:
                    bk_id = str(bk.get("catalog_item_id") or "").strip()
                    mp = bk.get("matter_profile") if isinstance(bk.get("matter_profile"), Mapping) else {}
                    bk_tags = bk.get("topic_tags") or mp.get("topic_tags") or ()
                    bk_text = " ".join([
                        str(bk.get("name_cn") or bk.get("name") or ""),
                        str(bk.get("argument_thesis_cn") or mp.get("argument_thesis_cn") or ""),
                        str(bk.get("sample_excerpt_cn") or mp.get("sample_excerpt_cn") or ""),
                        " ".join(str(t) for t in bk_tags),
                    ]).lower()
                    if bk_id and bk_text.strip():
                        recent_evidence_hay.append(("read_knowledge", bk_id, bk_text))
                for r_key, r_val in list(ledger_receipts.items())[-12:]:
                    if not isinstance(r_val, Mapping):
                        continue
                    r_res = r_val.get("result") if isinstance(r_val.get("result"), Mapping) else {}
                    r_ref = str(r_res.get("receipt_ref") or r_key).strip()
                    r_text = json.dumps(r_res, ensure_ascii=False).lower()
                    if r_ref:
                        recent_evidence_hay.append(("firsthand_lived", r_ref, r_text))

                active_raw_text = " ".join(
                    str(d.get("title_hint") or "") + " " + str(d.get("topic_key") or "")
                    for _r, d in existing_raw_items
                    if isinstance(d, Mapping)
                ).lower()

                for st in st_view.get("growth_stances") or ():
                    if not isinstance(st, Mapping):
                        continue
                    st_status = str(st.get("status") or "").upper()
                    if st_status not in ("UNFORMED", "TENTATIVE"):
                        continue
                    stance_id = str(st.get("stance_id") or "").strip()
                    if not stance_id or _is_rlf_blocked(st, forget_barrier) or _is_rlf_blocked(stance_id, forget_barrier):
                        continue
                    topic_cn = str(st.get("topic_cn") or "").strip()
                    topic_key = str(st.get("topic_key") or "").strip()
                    toks = [
                        tok
                        for tok in re.split(r"[\s,，、。;；:：()（）「」<>【】/_-]+", f"{topic_cn} {topic_key}".lower())
                        if len(tok) >= 2
                    ]
                    matched_tier = ""
                    matched_ref = ""
                    for ev_tier, ev_ref, ev_hay in recent_evidence_hay:
                        if any(tok in ev_hay for tok in toks) or (topic_cn and topic_cn.lower() in ev_hay):
                            matched_tier = ev_tier
                            matched_ref = ev_ref
                            break
                    uneval_pers = [
                        p
                        for p in (st.get("partner_persuasions") or ())
                        if isinstance(p, Mapping) and not p.get("evaluated")
                    ]
                    if not matched_ref and uneval_pers:
                        matched_tier = "partner_heard"
                        matched_ref = str(uneval_pers[-1].get("persuasion_id") or "")
                    if not matched_ref and toks and any(tok in active_raw_text for tok in toks):
                        matched_tier = "tentative_guess"
                        matched_ref = f"cue:{stance_id}"
                    if not matched_ref:
                        continue
                    src_ref = f"v09_stance:reflect:{stance_id}"
                    rec = _make_personal_candidate(
                        slug=f"v09_stance_{_safe_token(stance_id)}",
                        target_ref=src_ref,
                        causal_parent_refs=(src_ref, f"stance:{stance_id}"),
                        provenance_refs=(src_ref, matched_ref),
                        availability_basis="V09_UNFORMED_STANCE_EVIDENCE_CUE",
                        observed_iso=effective_now_iso,
                    )
                    out.append((
                        rec,
                        {
                            "candidate_id": rec.candidate_id,
                            "clue_domain": "v09.true_stance.growth",
                            "action_family": "reflect_unformed_stance",
                            "source_ref": src_ref,
                            "stance_id": stance_id,
                            "topic_cn": topic_cn,
                            "stance_status": st_status,
                            "matched_provenance_tier": matched_tier,
                            "matched_evidence_ref": matched_ref,
                            "availability_basis": "V09_UNFORMED_STANCE_EVIDENCE_CUE",
                            "provenance_refs": list(rec.provenance_refs),
                            "title_hint": f"借最近线索想想自己的看法：{topic_cn}"[:120],
                            "topic_key": topic_cn[:80],
                            "requires_outing": False,
                            "strenuous_outing": False,
                            "creative_intensive": False,
                            "impulse_purchase": False,
                            "zero_debt": True,
                            "optional": True,
                        },
                    ))
                    break
        except Exception:
            pass

    # 4. tidy_custom_shelf_or_tin: low-urgency quiet indoor candidate for emptied reusable tin
    #    or uncategorized desk reading booklet
    tax_file = inst_root / "world" / "personal_objects_taxonomy.json"
    tax_view: Optional[Mapping[str, Any]] = None
    if tax_file.is_file() and not tax_file.is_symlink():
        try:
            try:
                from components.world.scripts import personal_objects_taxonomy as _pot
            except ImportError:
                import personal_objects_taxonomy as _pot  # type: ignore[no-redef]
            raw_tv = _pot.read_personal_objects_taxonomy_zero_write(
                inst_root, actor_side="subject", now_iso=effective_now_iso
            )
            if isinstance(raw_tv, Mapping) and not raw_tv.get("forget_barrier_fail_closed"):
                tax_view = raw_tv
        except Exception:
            tax_view = None

    tidy_target: Optional[dict[str, str]] = None
    if isinstance(tax_view, Mapping):
        for obj in tax_view.get("repurposed_objects") or ():
            if not isinstance(obj, Mapping):
                continue
            oid = str(obj.get("object_id") or "").strip()
            if not oid or _is_rlf_blocked(obj, forget_barrier) or _is_rlf_blocked(oid, forget_barrier):
                continue
            stored = obj.get("stored_items") or []
            custom_lbl = str(obj.get("custom_label_cn") or "").strip()
            if (bool(obj.get("emptied_from_consumable")) and (not custom_lbl or len(stored) == 0)) or (
                len(stored) == 0 and not custom_lbl
            ):
                base_name = str(obj.get("base_name_cn") or oid).strip()
                tidy_target = {
                    "target_kind": "repurposed_object",
                    "target_id": oid,
                    "title_cn": base_name,
                    "hint_cn": f"顺手给空着的「{base_name}」贴签或收些小物",
                }
                break
        if tidy_target is None and booklets:
            categorized_refs = {
                str(ci.get("target_ref") or "").strip()
                for ci in (tax_view.get("categorized_items") or ())
                if isinstance(ci, Mapping)
            }
            for bk in booklets:
                bk_id = str(bk.get("catalog_item_id") or "").strip()
                if bk_id and bk_id not in categorized_refs:
                    bk_name = str(bk.get("name_cn") or bk_id).strip()
                    tidy_target = {
                        "target_kind": "reading_booklet",
                        "target_id": bk_id,
                        "title_cn": bk_name,
                        "hint_cn": f"给桌上的小册「{bk_name}」顺手归个自己的分类",
                    }
                    break
    elif booklets:
        bk = booklets[0]
        bk_id = str(bk.get("catalog_item_id") or "").strip()
        if bk_id:
            bk_name = str(bk.get("name_cn") or bk_id).strip()
            tidy_target = {
                "target_kind": "reading_booklet",
                "target_id": bk_id,
                "title_cn": bk_name,
                "hint_cn": f"给桌上的小册「{bk_name}」顺手归个自己的分类",
            }

    if tidy_target is not None:
        t_id = tidy_target["target_id"]
        src_ref = f"v09_taxonomy:tidy:{_safe_token(t_id)}"
        rec = _make_personal_candidate(
            slug=f"v09_tidy_{_safe_token(t_id)}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref, t_id),
            provenance_refs=(src_ref,),
            availability_basis="V09_QUIET_TIDY_SHELF_OR_TIN",
            observed_iso=effective_now_iso,
        )
        out.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v09.personal_objects_taxonomy.tidy",
                "action_family": "tidy_custom_shelf_or_tin",
                "source_ref": src_ref,
                "target_kind": tidy_target["target_kind"],
                "target_id": t_id,
                "target_title_cn": tidy_target["title_cn"],
                "availability_basis": "V09_QUIET_TIDY_SHELF_OR_TIN",
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": tidy_target["hint_cn"][:120],
                "topic_key": tidy_target["title_cn"][:80],
                "candidate_weight": 0.75,
                "zero_debt": True,
                "optional": True,
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    return out


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
    gov_signals = _collect_v02_governance_signals_zero_write(inst_root)
    blocked_id_set = set(gov_signals.get("blocked_ids") or ())
    forget_barrier = gov_signals.get("forget_barrier") if isinstance(gov_signals.get("forget_barrier"), Mapping) else {}
    diverged_artifact_set = set(gov_signals.get("diverged_artifact_ids") or ())
    unvalidated_skill_set = set(gov_signals.get("unvalidated_skills") or ())
    for _sk in (runtime_migration_summary.get("unvalidated_skills") or ()):
        if isinstance(_sk, str) and _sk.strip():
            unvalidated_skill_set.add(_sk.strip())
        elif isinstance(_sk, Mapping) and _sk.get("skill_id"):
            unvalidated_skill_set.add(str(_sk["skill_id"]).strip())

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
                            "item_id": wid,
                            "wish_id": wid,
                            "quantity": 1,
                            "unit_price": cap_jpy,
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
                    "item_id": cid,
                    "quantity": int(cue.get("quantity") or 1),
                    "unit_price": price_cents,
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

    # v0.8 Wave 7 & 0.2 全纲收口：活跃有界 Inquiry / D01 可见摆件记忆线索 / §10.C 可拒绝下一步小把手 / D09 已到期未来时间胶囊
    # 严格仅当 state/v02_agency_depth_state.json 真实存在时零写读取，保持空目录零触碰。
    try:
        from components.life.scripts import v02_agency_depth_engine as _ad_mod
    except ImportError:  # pragma: no cover - flat life/scripts import path
        import v02_agency_depth_engine as _ad_mod  # type: ignore[no-redef]
    ad_state_file = inst_root.joinpath(*_ad_mod.STATE_REL_PATH)
    ad_proj: Optional[dict[str, Any]] = None
    full_inquiries_by_id: dict[str, Mapping[str, Any]] = {}
    if ad_state_file.is_file() and not ad_state_file.is_symlink():
        ad_proj = _ad_mod.project_v02_agency_depth(
            inst_root, has_subject_capability=True, now_iso=effective_now_iso
        )
        for _full_inq in list(ad_proj.get("inquiries") or []):
            if isinstance(_full_inq, Mapping) and _full_inq.get("inquiry_id"):
                full_inquiries_by_id[str(_full_inq.get("inquiry_id"))] = _full_inq

    for inq in list(agency_depth_summary.get("active_inquiries") or [])[:8]:
        if not isinstance(inq, Mapping):
            continue
        raw_inq_id = str(inq.get("inquiry_id") or "inquiry")
        inq_id = _safe_token(raw_inq_id)
        full_inq = full_inquiries_by_id.get(raw_inq_id) or full_inquiries_by_id.get(inq_id) or inq
        auto_conclude_ready, grounded_conclusion_hint = _evaluate_inquiry_auto_conclusion(full_inq)
        question = str(inq.get("question") or "有界探究").strip()[:120]
        src_ref = f"v02_inquiry:{inq_id}"
        rec = _make_personal_candidate(
            slug=f"v02_inquiry_{inq_id}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=(f"v02_agency_depth:{inq_id}", src_ref),
            availability_basis="BOUNDED_INQUIRY_ACTIVE",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.agency_depth.bounded_inquiry",
                "action_family": "advance_bounded_inquiry",
                "source_ref": src_ref,
                "inquiry_id": inq_id,
                "inquiry_status": str(inq.get("status") or "OPEN"),
                "topic_tag": str(inq.get("topic_tag") or ""),
                "shared": bool(inq.get("shared", False)),
                "steps_used": int(
                    full_inq.get("steps_spent")
                    if isinstance(full_inq.get("steps_spent"), int)
                    and not isinstance(full_inq.get("steps_spent"), bool)
                    else len(full_inq.get("evidence_notes") or [])
                ),
                "step_budget": int(full_inq.get("step_budget") or 6),
                "evidence_count": int(
                    len(full_inq.get("evidence_notes") or [])
                    if isinstance(full_inq.get("evidence_notes"), list)
                    else (inq.get("evidence_count") or 0)
                ),
                "auto_conclude_ready": auto_conclude_ready,
                "grounded_conclusion_hint": grounded_conclusion_hint,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": question,
                "topic_key": autonomy_rhythm_view._normalize_topic_key(question),
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    if ad_proj is not None:
        # D01 可见物理摆件记忆线索（仅 active_cues，被遮挡/移除后零排放）
        active_cues = list((ad_proj.get("placed_memory_cues") or {}).get("active_cues") or [])[:6]
        for cue in active_cues:
            if not isinstance(cue, Mapping):
                continue
            cue_id = _safe_token(cue.get("cue_id") or "cue")
            cue_text = str(cue.get("cue_text_cn") or "桌面摆放的记忆线索").strip()[:120]
            spot_id = str(cue.get("spot_id") or "").strip()[:80]
            art_ref = str(cue.get("artifact_ref") or "").strip()[:160]
            src_ref = f"v02_placed_cue:{cue_id}"
            rec = _make_personal_candidate(
                slug=f"v02_placed_cue_{cue_id}",
                target_ref=src_ref,
                causal_parent_refs=tuple(x for x in (src_ref, art_ref) if x),
                provenance_refs=(f"v02_placed_cue:{cue_id}", f"spot:{spot_id or 'room'}"),
                availability_basis="PLACED_MEMORY_CUE_VISIBLE",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v02.agency_depth.placed_memory_cue",
                    "action_family": "follow_placed_memory_cue",
                    "source_ref": src_ref,
                    "cue_id": cue_id,
                    "spot_id": spot_id,
                    "artifact_ref": art_ref or None,
                    "shared": bool(cue.get("shared", False)),
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": cue_text,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(cue_text),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

        # §10.C 可拒绝下一步小把手（仅 OFFERED/OPEN 且 claimed=False；peek 不改变认领态）
        for gest in list(ad_proj.get("optional_next_gestures") or [])[:6]:
            if not isinstance(gest, Mapping):
                continue
            st_up = str(gest.get("status") or "").strip().upper()
            if st_up not in ("OFFERED", "OPEN") or gest.get("claimed") is True:
                continue
            gid = _safe_token(gest.get("gesture_id") or "gesture")
            g_text = str(gest.get("gesture_cn") or "可选下一步").strip()[:120]
            art_ref = str(gest.get("artifact_ref") or "").strip()[:160]
            src_ref = f"v02_next_gesture:{gid}"
            rec = _make_personal_candidate(
                slug=f"v02_next_gesture_{gid}",
                target_ref=src_ref,
                causal_parent_refs=tuple(x for x in (src_ref, art_ref) if x),
                provenance_refs=(f"v02_next_gesture:{gid}",),
                availability_basis="OPTIONAL_NEXT_GESTURE_OFFERED",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v02.agency_depth.optional_next_gesture",
                    "action_family": "act_optional_next_gesture",
                    "source_ref": src_ref,
                    "gesture_id": gid,
                    "artifact_ref": art_ref or None,
                    "shared": bool(gest.get("shared", False)),
                    "omission_debt": 0,
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": g_text,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(g_text),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

        # D09 已到期未来时间胶囊（仅 status == AVAILABLE 且零亏欠债）
        for cap in list(ad_proj.get("future_self_capsules") or [])[:6]:
            if not isinstance(cap, Mapping):
                continue
            if str(cap.get("status") or "").strip().upper() != "AVAILABLE":
                continue
            cid = _safe_token(cap.get("capsule_id") or "capsule")
            c_title = str(cap.get("title_cn") or "给未来自己的信").strip()[:120]
            art_ref = str(cap.get("artifact_ref") or "").strip()[:160]
            src_ref = f"v02_future_capsule:{cid}"
            rec = _make_personal_candidate(
                slug=f"v02_future_capsule_{cid}",
                target_ref=src_ref,
                causal_parent_refs=tuple(x for x in (src_ref, art_ref) if x),
                provenance_refs=(f"v02_future_capsule:{cid}",),
                availability_basis="FUTURE_CAPSULE_MATURED",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                rec,
                {
                    "candidate_id": rec.candidate_id,
                    "clue_domain": "v02.agency_depth.future_capsule",
                    "action_family": "open_future_capsule",
                    "source_ref": src_ref,
                    "capsule_id": cid,
                    "artifact_ref": art_ref or None,
                    "shared": bool(cap.get("shared", False)),
                    "omission_debt": 0,
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": c_title,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(c_title),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # Wave B1 (F-175 / F-176): zero-write read of mid-scale absence observations &
    # autobiography spark opportunities, converting at most 1 unacknowledged absence and
    # at most 1 autobiography spark into AG0-compliant CandidateRecords before gating.
    midscale_absence_observations: list[dict[str, Any]] = []
    autobiography_spark_opportunities: list[dict[str, Any]] = []
    try:
        try:
            from . import v02_midscale_ecology_engine as _midscale_eco  # type: ignore[no-redef]
        except ImportError:
            import v02_midscale_ecology_engine as _midscale_eco  # type: ignore[no-redef]

        midscale_absence_observations = _midscale_eco.detect_absence_and_mismatch_zero_write(
            inst_root,
            has_subject_capability=True,
            now_iso=effective_now_iso,
        )
        autobiography_spark_opportunities = _midscale_eco.derive_autobiography_opportunities_zero_write(
            inst_root,
            has_subject_capability=True,
            now_iso=effective_now_iso,
        )
    except Exception:
        midscale_absence_observations = []
        autobiography_spark_opportunities = []

    for obs in midscale_absence_observations:
        if not isinstance(obs, Mapping):
            continue
        if bool(obs.get("acknowledged_by_partner", False)):
            continue
        raw_obs_id = str(obs.get("observation_id") or obs.get("item_id") or "absence").strip()
        obs_id = _safe_token(raw_obs_id, "absence")
        item_id = str(obs.get("item_id") or obs_id).strip()[:96]
        title_cn = str(obs.get("title_cn") or item_id).strip()[:120]
        abs_kind = str(obs.get("kind") or "MISSING_OBJECT").strip()
        src_ref = f"v02_absence:{obs_id}"
        rec = _make_personal_candidate(
            slug=f"v02_absence_{obs_id}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=(f"v02_midscale_ecology:{obs_id}", src_ref),
            availability_basis="MIDSCALE_ABSENCE_OBSERVED",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.midscale_ecology.absence",
                "action_family": "inspect_room_absence",
                "source_ref": src_ref,
                "observation_id": raw_obs_id[:120],
                "item_id": item_id,
                "absence_kind": abs_kind,
                "expected_place_cn": str(obs.get("expected_place_cn") or "")[:120],
                "observed_place_cn": str(obs.get("observed_place_cn") or "")[:120],
                "summary_cn": str(obs.get("summary_cn") or "")[:240],
                "shared": bool(obs.get("shared", True)),
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": f"查看物件变动：{title_cn}"[:120],
                "topic_key": autonomy_rhythm_view._normalize_topic_key(title_cn),
                "requires_outing": abs_kind == "SHOP_CLOSED_UNEXPECTED",
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))
        break

    for opp in autobiography_spark_opportunities:
        if not isinstance(opp, Mapping):
            continue
        raw_opp_id = str(opp.get("opp_id") or opp.get("source_episode_id") or "spark").strip()
        opp_id = _safe_token(raw_opp_id, "spark")
        src_ep_id = str(opp.get("source_episode_id") or "").strip()[:96]
        idea_title = str(opp.get("idea_title_cn") or "旧事新契机").strip()[:120]
        opp_domain = str(opp.get("domain") or "daily_life").strip()[:80]
        src_ref = f"v02_auto_spark:{opp_id}"
        prov_refs = [f"v02_midscale_ecology:{opp_id}", src_ref]
        if src_ep_id:
            prov_refs.append(f"autobiography_episode:{_safe_token(src_ep_id, 'ep')}")
        rec = _make_personal_candidate(
            slug=f"v02_auto_spark_{opp_id}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=tuple(prov_refs),
            availability_basis="AUTOBIOGRAPHY_SPARK_OPPORTUNITY",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.midscale_ecology.autobiography_spark",
                "action_family": "explore_autobiography_spark",
                "source_ref": src_ref,
                "opp_id": raw_opp_id[:120],
                "source_episode_id": src_ep_id,
                "source_outcome": str(opp.get("source_outcome") or "")[:40],
                "domain": opp_domain,
                "why_now_cn": str(opp.get("why_now_cn") or "")[:240],
                "zero_debt": True,
                "shared": bool(opp.get("shared", False)),
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": idea_title,
                "topic_key": autonomy_rhythm_view._normalize_topic_key(idea_title),
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))
        break

    # F-189: pending partner rescaled co-practice proposals in honest_ux state (100% zero-write, bounded <= 2)
    pending_rescaled_copractices = _read_pending_rescaled_copractices_zero_write(inst_root)
    for prop in pending_rescaled_copractices:
        raw_pid = str(prop.get("practice_id") or "copractice").strip()
        pid_tok = _safe_token(raw_pid, "copractice")
        title_cn = str(prop.get("title_cn") or prop.get("title") or "轻量共同练习").strip()[:120]
        scale_mode = str(prop.get("scale_mode") or "light_relay").strip().lower() or "light_relay"
        note_cn = str(prop.get("note_cn") or "").strip()[:200]
        src_ref = f"v02_rescaled_copractice:{pid_tok}"
        rec = _make_personal_candidate(
            slug=f"v02_rescaled_copractice_{pid_tok}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=(f"v02_honest_ux:{pid_tok}", src_ref),
            availability_basis="PARTNER_RESCALED_COPRACTICE_PROPOSED",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v02.honest_ux.rescaled_copractice",
                "action_family": "consider_rescaled_copractice",
                "source_ref": src_ref,
                "practice_id": raw_pid[:96],
                "scale_mode": scale_mode,
                "note_cn": note_cn,
                "shared": bool(prop.get("shared", True)),
                "zero_debt": True,
                "optional": True,
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": f"斟酌伴侣提议的缩减共同练习：{title_cn}"[:120],
                "topic_key": autonomy_rhythm_view._normalize_topic_key(title_cn),
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
    # Ch.44: the zero-write cue scan is the single source of truth for the gating
    # summary.  It already drops every source whose real object is gone
    # (``SOURCE_GONE``), so the counts below describe only what truly still exists.
    try:
        et_scan = emotional_tension_view.scan_emotional_tension_cues_zero_write(
            inst_root, now=effective_now_iso
        )
    except Exception:
        et_scan = {}
    if not isinstance(et_scan, Mapping):
        et_scan = {}
    emotional_tension_summary = {
        "in_low_mood": bool(et_scan.get("in_low_mood")),
        "active_spaces_count": int(et_scan.get("active_spaces_count") or 0),
        "open_ruptures_count": int(et_scan.get("open_ruptures_count") or 0),
        "unsurfaced_missing_notes_count": int(
            et_scan.get("unsurfaced_missing_notes_count") or 0
        ),
        "open_favor": et_scan.get("open_favor"),
        "declined_kinds_until_sleep": list(
            et_scan.get("declined_kinds_until_sleep") or []
        ),
        "latest_expired_favor_or_rejected_apology": et_scan.get(
            "latest_expired_favor_or_rejected_apology"
        ),
        # A declined favour is never a debt; a low-mood spell or an active request
        # for space never makes the loop manufacture a soothing/intimacy task.
        "no_obligation_when_declined": True,
        "manufactured_comfort_or_intimacy": False,
        "soothing_or_intimacy_tasks_suppressed": bool(
            et_scan.get("in_low_mood") or int(et_scan.get("active_spaces_count") or 0) > 0
        ),
        "read_only": True,
        "private_content_excluded": True,
    }
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

    # Ch.42 / Ch.44 §二: a candidate may only ride a source that really still
    # exists.  If a structured cross-store ref (``matter:`` / ``v04_card:`` /
    # ``v04_project:``) points at something already consumed or discarded in its
    # owning repository, the candidate is dropped here -- the daily loop never
    # emits a fake "missing you" or "help me" task for a gone object.
    if raw_items:
        kept_live: list[tuple[CandidateRecord, dict[str, Any]]] = []
        for rec_item, desc_item in raw_items:
            sref = desc_item.get("source_ref")
            gone: Optional[bool] = None
            if isinstance(sref, str) and sref.strip():
                try:
                    gone = emotional_tension_view._cross_store_source_exists(
                        inst_root, sref.strip()
                    )
                except Exception:
                    gone = None
            if gone is False:
                continue
            kept_live.append((rec_item, desc_item))
        raw_items = kept_live

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
                    "job_id": job_kind,
                    "payout_cents": int(getattr(v03_eco_bridge, "DEFAULT_JOB_PAY_CENTS", {}).get(job_kind, 0)),
                    "effort": "creative" if creative_flag else "routine",
                    "provenance_refs": list(rec.provenance_refs),
                    "title_hint": label,
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(label),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": creative_flag,
                    "impulse_purchase": False,
                },
            ))

    # GAP-05: 伴侣实物小礼 72h 托管待决项（至多 1 条非强制零负担候选，过期不发）
    pending_gift = _read_pending_item_gift_zero_write(inst_root, now_dt)
    if isinstance(pending_gift, Mapping):
        gift_id = str(pending_gift.get("gift_id") or "").strip()
        if gift_id:
            gift_tok = _safe_token(gift_id, "gift")
            gift_ref = f"v03_item_gift:{gift_tok}"
            gift_label = str(pending_gift.get("item_label_cn") or "小礼物").strip()[:40]
            gift_rec = _make_personal_candidate(
                slug=f"v03_item_gift_{gift_tok}",
                target_ref=gift_ref,
                causal_parent_refs=(gift_ref,),
                provenance_refs=("v03_ecology:item_gift_escrow", gift_ref),
                availability_basis="PARTNER_ITEM_GIFT_OFFERED_OPTIONAL",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                gift_rec,
                {
                    "candidate_id": gift_rec.candidate_id,
                    "clue_domain": "v03.economy.item_gift_escrow",
                    "action_family": "decide_partner_item_gift",
                    "source_ref": gift_ref,
                    "gift_id": gift_id,
                    "item_label_cn": gift_label,
                    "gift_decision": "accept",
                    "optional": True,
                    "zero_debt": True,
                    "omission_debt": 0,
                    "provenance_refs": list(gift_rec.provenance_refs),
                    "title_hint": f"看看待回应的小礼物「{gift_label}」（收下或婉拒皆无负担）"[:120],
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(gift_label or "小礼物"),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # Ch.10 §1.2: 主体自己撰写的 72h 慢信草稿已静置到期（至多 1 条非强制候选；绝不读取伴侣草稿！）
    matured_draft = _read_matured_subject_slow_letter_draft_zero_write(inst_root, now_dt)
    if isinstance(matured_draft, Mapping):
        draft_id = str(matured_draft.get("draft_id") or "").strip()
        if draft_id:
            draft_tok = _safe_token(draft_id, "draft")
            draft_ref = f"v03_slow_letter_draft:{draft_tok}"
            draft_title = str(matured_draft.get("title") or "").strip()[:60]
            draft_rec = _make_personal_candidate(
                slug=f"v03_slow_letter_draft_{draft_tok}",
                target_ref=draft_ref,
                causal_parent_refs=(draft_ref,),
                provenance_refs=("window_keepsakes:slow_letter_drafts", draft_ref),
                availability_basis="SLOW_LETTER_DRAFT_MATURED_72H_OPTIONAL",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                draft_rec,
                {
                    "candidate_id": draft_rec.candidate_id,
                    "clue_domain": "v03.keepsakes.slow_letter_draft",
                    "action_family": "post_matured_slow_letter_draft",
                    "source_ref": draft_ref,
                    "draft_id": draft_id,
                    "optional": True,
                    "zero_debt": True,
                    "omission_debt": 0,
                    "provenance_refs": list(draft_rec.provenance_refs),
                    "title_hint": (
                        f"静置满72小时的慢信草稿《{draft_title}》可以投递或继续留存"
                        if draft_title
                        else "静置满72小时的慢信草稿可以投递或继续留存"
                    )[:120],
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(draft_title or "慢信草稿"),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
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

    # Ch.45 §三: an active autonomous mini-project may offer at most ONE optional,
    # zero-debt inspiration cue.  A project shelved as ``resting_not_feeling_it``
    # never emits a reminder or continuation candidate -- no nagging, no debt.
    if isinstance(v04_ep_doc, Mapping):
        project_cue_added = False
        for raw_project in v04_ep_doc.get("personal_projects") or []:
            if project_cue_added:
                break
            if not isinstance(raw_project, Mapping):
                continue
            if str(raw_project.get("status") or "active").strip().lower() != "active":
                continue
            purpose = str(raw_project.get("purpose_cn") or "").strip()[:80]
            if not purpose:
                continue
            pid = _safe_token(raw_project.get("project_id") or "project")
            next_step = str(raw_project.get("next_step_cn") or "").strip()[:80]
            src_ref = f"v04_personal_project:{pid}"
            project_rec = _make_personal_candidate(
                slug=f"v04_project_{pid}",
                target_ref=src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=("v04_elastic_play:personal_project", src_ref),
                availability_basis="V04_PERSONAL_PROJECT_OPTIONAL_INSPIRATION",
            )
            raw_items.append((
                project_rec,
                {
                    "candidate_id": project_rec.candidate_id,
                    "clue_domain": "v04.elastic.personal_project",
                    "action_family": "personal_project_step",
                    "source_ref": src_ref,
                    "project_id": str(raw_project.get("project_id") or ""),
                    "medium_kind": str(raw_project.get("medium_kind") or ""),
                    "provenance_refs": list(project_rec.provenance_refs),
                    "title_hint": (next_step or f"想起来就做一点：{purpose}")[:120],
                    "topic_key": autonomy_rhythm_view._normalize_topic_key(purpose),
                    "optional": True,
                    "zero_debt": True,
                    "confidence_penalty": 0,
                    "forced_reflection": False,
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))
            project_cue_added = True

    # v0.8 Ch.50: 共享小本子上对方刚留的那一句，最多一张可选、零债务的温和候选。
    # 不计算“等了几天”，列表为空或最后一句已是主体自己写的时保持静默。
    if isinstance(v04_ep_doc, Mapping):
        snb_doc = v04_ep_doc.get("shared_notebook")
        notebook_entries: list[Any] = []
        if isinstance(snb_doc, Mapping):
            if isinstance(snb_doc.get("entries"), list):
                notebook_entries = snb_doc["entries"]
            elif isinstance(snb_doc.get("latest_entry"), Mapping):
                notebook_entries = [snb_doc["latest_entry"]]
        elif isinstance(snb_doc, list):
            notebook_entries = snb_doc
        last_author = ""
        if notebook_entries:
            last_raw = notebook_entries[-1]
            if isinstance(last_raw, Mapping):
                last_author = str(
                    last_raw.get("author") or last_raw.get("actor") or ""
                ).strip().lower()
        if last_author == "partner":
            src_ref = "v04_elastic_shared_notebook:latest_partner_line"
            nb_rec = _make_personal_candidate(
                slug="v04_elastic_shared_notebook_latest",
                target_ref=src_ref,
                causal_parent_refs=(src_ref,),
                provenance_refs=("v04_elastic_play:shared_notebook", src_ref),
                availability_basis="V04_SHARED_NOTEBOOK_PARTNER_LINE",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                nb_rec,
                {
                    "candidate_id": nb_rec.candidate_id,
                    "clue_domain": "v04.elastic.shared_notebook",
                    "action_family": "elastic_shared_notebook",
                    "source_ref": src_ref,
                    "provenance_refs": list(nb_rec.provenance_refs),
                    "title_hint": "看看共享小本子上对方留的新一句",
                    "topic_key": autonomy_rhythm_view._normalize_topic_key("共享小本子"),
                    "optional": True,
                    "zero_debt": True,
                    "confidence_penalty": 0,
                    "forced_reflection": False,
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # v0.8 Ch.53: 主体自己已备好、尚未递出的小惊喜 -> 至多一张“时机到了吗（不急）”
    # 的可选候选。只读 readiness 布尔/ID，绝不把 title / content 写进候选或公开摘要。
    try:
        surprise_readiness = v04_ep_learning.project_surprise_readiness_zero_write(inst_root)
    except Exception:  # noqa: BLE001 - a broken/missing file adds no candidate
        surprise_readiness = {}
    if isinstance(surprise_readiness, Mapping) and surprise_readiness.get(
        "has_prepared_waiting"
    ) is True:
        src_ref = "v04_elastic_prepared_surprise:waiting"
        sp_rec = _make_personal_candidate(
            slug="v04_elastic_prepared_surprise_waiting",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("v04_elastic_play:prepared_surprise", src_ref),
            availability_basis="V04_PREPARED_SURPRISE_WAITING",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            sp_rec,
            {
                "candidate_id": sp_rec.candidate_id,
                "clue_domain": "v04.elastic.prepared_surprise",
                "action_family": "elastic_offer_surprise",
                "source_ref": src_ref,
                "provenance_refs": list(sp_rec.provenance_refs),
                "title_hint": "心里那件小惊喜准备好拿出来了吗（不急）",
                "topic_key": autonomy_rhythm_view._normalize_topic_key("小惊喜时机"),
                "optional": True,
                "zero_debt": True,
                "confidence_penalty": 0,
                "forced_reflection": False,
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

    # v0.8 Wave 2: 真实 v0.4 院子/盆栽/手作/借还机会。状态文件不存在时零写入、零候选；
    # 存在时按真实物品/植物/手工/借出状态逐条发出，并带上结算用的 v04_action + payload。
    v04_neighborhood_state = _load_v04_neighborhood_state_zero_write(inst_root)
    if v04_neighborhood_state is not None:
        raw_items.extend(
            _collect_v04_neighborhood_candidates(
                inst_root, now_dt, observed_iso=effective_now_iso
            )
        )
    else:
        # 向后兼容：旧投影形状（weather_and_ground/courtyard.drying_rack）的只读文档。
        v04_nb_doc = _read_optional_json_zero_write(
            inst_root / "world" / "v04_neighborhood_state.json"
        )
        if isinstance(v04_nb_doc, Mapping):
            wg = (
                v04_nb_doc.get("weather_and_ground")
                if isinstance(v04_nb_doc.get("weather_and_ground"), Mapping)
                else (
                    v04_nb_doc.get("weather")
                    if isinstance(v04_nb_doc.get("weather"), Mapping)
                    else {}
                )
            )
            w_cond = str(wg.get("weather") or wg.get("condition") or "").strip().lower()
            cy = (
                v04_nb_doc.get("courtyard")
                if isinstance(v04_nb_doc.get("courtyard"), Mapping)
                else v04_nb_doc
            )
            rack = cy.get("drying_rack") if isinstance(cy, Mapping) else None
            cloths = (
                rack.get("items")
                if isinstance(rack, Mapping) and isinstance(rack.get("items"), list)
                else (
                    rack
                    if isinstance(rack, list)
                    else (cy.get("drying_cloths") if isinstance(cy, Mapping) else None)
                )
            )
            if w_cond in V04_RAIN_WEATHER and isinstance(cloths, list):
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

    # 36A §11.1 / 36B-4 §9：可恢复的成年亲密入场断点也是 0.3 日常的真实候选。
    for cp in _resumable_intimacy_checkpoints_zero_write(inst_root):
        permit_id = cp["permit_id"]
        activity_ref = cp["activity_ref"]
        src_ref = f"v03_intimacy_resume:{permit_id}"
        rec = _make_personal_candidate(
            slug=f"v03_intimacy_resume_{_safe_token(permit_id)}",
            target_ref=src_ref,
            causal_parent_refs=(src_ref,),
            provenance_refs=("v03_intimacy:resumable_checkpoint", src_ref),
            availability_basis="ADULT_INTIMACY_CHECKPOINT_RESUMABLE",
            observed_iso=effective_now_iso,
        )
        raw_items.append((
            rec,
            {
                "candidate_id": rec.candidate_id,
                "clue_domain": "v03.intimacy.resumable_checkpoint",
                "action_family": "RESUME_INTIMACY_CHECKPOINT",
                "source_ref": src_ref,
                "permit_id": permit_id,
                "activity_ref": activity_ref,
                "permit_state": cp.get("permit_state"),
                "provenance_refs": list(rec.provenance_refs),
                "title_hint": "回到刚才被停下的那段成年亲密活动断点",
                "topic_key": "成年亲密断点",
                "requires_outing": False,
                "strenuous_outing": False,
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))

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

    # ------------------------------------------------------------------
    # 0.8 Open Affordance: at most two candidates per round.
    #   1. One saved habit routine whose cue honestly matches the environment.
    #   2. One open gesture on a real reachable object -- preferring the object a
    #      partner just left a trace on, so the subject can decide whether to respond.
    # A missing/corrupt trace file simply contributes nothing (zero-write read).
    # ------------------------------------------------------------------
    v08_added = 0
    try:
        v08_view = v08_open.read_living_traces_view(
            inst_root, viewer="subject", now_iso=effective_now_iso
        )
    except Exception:  # noqa: BLE001 - a broken file adds no candidate
        v08_view = {}
    if isinstance(v08_view, Mapping):
        cues = _v08_context_cues_zero_write(inst_root, now_dt)
        routines = [
            row for row in (v08_view.get("routines") or [])
            if isinstance(row, Mapping)
            and str(row.get("status") or "active") != "retired"
        ]
        matched_routine = next(
            (row for row in routines if str(row.get("when_cue") or "any") in cues),
            None,
        )
        if matched_routine is not None:
            rid = str(matched_routine.get("routine_id") or "").strip()
            if rid:
                ref = f"v08_routine:{rid}"
                slug = f"v08_routine_{_safe_token(rid)}"
                rec = _make_personal_candidate(
                    slug=slug,
                    target_ref=ref,
                    causal_parent_refs=(ref,),
                    provenance_refs=(ref,),
                    availability_basis="V08_ROUTINE_CUE_MATCH",
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v08.open_affordance.routine",
                        "action_family": "run_personal_routine",
                        "source_ref": ref,
                        "routine_id": rid,
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": str(matched_routine.get("title_cn") or "顺手小套路")[:120],
                        "topic_key": str(matched_routine.get("title_cn") or "顺手小套路"),
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))
                v08_added += 1

        if v08_added < 2:
            objects = [
                row for row in (v08_view.get("reachable_objects") or [])
                if isinstance(row, Mapping) and str(row.get("object_id") or "").strip()
            ]
            object_by_id = {str(row.get("object_id")): row for row in objects}
            traces = [
                row for row in (v08_view.get("active_traces") or [])
                if isinstance(row, Mapping)
            ]
            partner_trace = next(
                (row for row in reversed(traces) if str(row.get("author_side") or "") == "partner"),
                None,
            )
            object_id = ""
            mode = "touch_object"
            if partner_trace is not None:
                candidate_object = str(partner_trace.get("object_id") or "").strip()
                if candidate_object in object_by_id:
                    object_id = candidate_object
                    mode = "respond_partner_trace"
            if not object_id:
                # Only a genuinely live open-affordance trace is a cue.  A quiet
                # root with reachable objects but no trace/intent/attention signal
                # must never manufacture a synthetic "touch something" puppet just
                # to keep a per-tick quota full.
                live_trace = next(
                    (
                        row for row in reversed(traces)
                        if str(row.get("object_id") or "").strip() in object_by_id
                    ),
                    None,
                )
                if live_trace is not None:
                    object_id = str(live_trace.get("object_id") or "").strip()
                    mode = "continue_trace"
            if object_id:
                label = str(object_by_id[object_id].get("label_cn") or "手边物件")
                ref = f"v08_open_act:{object_id}:{mode}"
                slug = f"v08_open_act_{_safe_token(object_id)}_{mode}"
                rec = _make_personal_candidate(
                    slug=slug,
                    target_ref=ref,
                    causal_parent_refs=(ref,),
                    provenance_refs=(ref,),
                    availability_basis=(
                        "V08_PARTNER_TRACE_WAITING"
                        if mode == "respond_partner_trace"
                        else "V08_OPEN_TRACE_CONTINUATION"
                    ),
                )
                raw_items.append((
                    rec,
                    {
                        "candidate_id": rec.candidate_id,
                        "clue_domain": "v08.open_affordance.open_act",
                        "action_family": "open_living_trace",
                        "source_ref": ref,
                        "object_id": object_id,
                        "open_mode": mode,
                        "provenance_refs": list(rec.provenance_refs),
                        "title_hint": (
                            f"看看{label}上刚留下的痕迹"
                            if mode == "respond_partner_trace"
                            else f"接着{label}上刚才那点动静"
                        )[:120],
                        "topic_key": label,
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))
                v08_added += 1

    # ------------------------------------------------------------------
    # v0.8 Wave 7 (v03 encounters bridge): at most one low-pressure candidate for a
    # partner-dropped material still in ``RECEIVED_UNOPENED``.  Pure zero-write read;
    # a missing/corrupt file, or a WITHDRAWN material, contributes nothing.  Opening it
    # stays optional -- settlement may advance it to ``OPENED_PARTIAL`` or the subject
    # may simply leave it untouched.
    # ------------------------------------------------------------------
    unopened_material = _read_first_received_unopened_material_zero_write(inst_root)
    if isinstance(unopened_material, Mapping):
        material_id = str(unopened_material.get("material_id") or "").strip()
        if material_id:
            material_token = _safe_token(material_id, "material")
            material_ref = f"v03_material:{material_token}"
            material_title = (
                str(unopened_material.get("title_cn") or "").strip() or "手边那页材料"
            )
            mat_rec = _make_personal_candidate(
                slug=f"v03_material_{material_token}",
                target_ref=material_ref,
                causal_parent_refs=(material_ref,),
                provenance_refs=(material_ref,),
                availability_basis="V03_SHARED_MATERIAL_RECEIVED_UNOPENED",
            )
            raw_items.append((
                mat_rec,
                {
                    "candidate_id": mat_rec.candidate_id,
                    "clue_domain": "v03.encounters.shared_material",
                    "action_family": "open_shared_material",
                    "source_ref": material_ref,
                    "material_id": material_id,
                    "open_mode": "open_partial",
                    "provenance_refs": list(mat_rec.provenance_refs),
                    "title_hint": f"翻一翻手边那页材料《{material_title}》"[:120],
                    "topic_key": str(unopened_material.get("topic_tag") or material_title),
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))

    # ------------------------------------------------------------------
    # v0.8 universal matter: one generic objective entry for whatever the real
    # physics currently offers (a raw ingredient next to a free stove, used
    # vessels / cluttered surfaces, an object a partner moved and the subject has
    # not looked at since).  This is the single generic door -- a future kitchen
    # or desk object needs no bespoke daily-loop handler.  The scan is 100%
    # zero-write; a missing state file simply contributes nothing.
    #
    # Half-finished craft/conservation opportunities the scan surfaces (split a
    # material, assemble/detach a part, transcribe onto paper, patch/repair) ride
    # the exact same path: ``open_act_payload`` is deep-copied whole, so no field
    # here inspects, allowlists or truncates ``steps[].op``.  The event builder maps
    # any ``v08_matter:*`` ref to ``OPEN_MATTER_ACT`` and
    # ``_settle_v08_matter_open_act`` hands the payload straight to the engine.
    # ------------------------------------------------------------------
    matter_added = 0
    try:
        matter_opportunities = v08_matter.scan_matter_opportunities_zero_write(
            inst_root, now_iso=effective_now_iso
        )
    except Exception:  # noqa: BLE001 - a broken scan contributes no candidate
        matter_opportunities = []
    for opp in matter_opportunities:
        if matter_added >= 2:
            break
        if not isinstance(opp, Mapping):
            continue
        target_ref = str(opp.get("target_ref") or "").strip()
        if not target_ref.startswith("v08_matter:"):
            continue
        payload = opp.get("open_act_payload")
        if not isinstance(payload, Mapping):
            continue
        # The scan only guarantees target_ref / title_hint / availability_basis /
        # open_act_payload; derive the slug from target_ref when no slug is given.
        slug = "v08_matter_" + _safe_token(
            str(opp.get("slug") or target_ref), "open_act"
        )
        matter_rec = _make_personal_candidate(
            slug=slug,
            target_ref=target_ref,
            causal_parent_refs=(target_ref,),
            provenance_refs=(target_ref,),
            availability_basis=str(
                opp.get("availability_basis") or "V08_MATTER_OPEN_OPPORTUNITY"
            ),
        )
        open_act_payload = copy.deepcopy(dict(payload))
        # A plain kitchen/desk act is never a strenuous outing; only an explicit
        # marker carried by the physics scan can set it.
        open_act_payload.setdefault("strenuous_outing", False)
        raw_items.append((
            matter_rec,
            {
                "candidate_id": matter_rec.candidate_id,
                "clue_domain": "v08.universal_matter.open_act",
                "action_family": "compose_open_act",
                "source_ref": target_ref,
                "matter_target_ref": target_ref,
                "availability_basis": str(opp.get("availability_basis") or ""),
                "open_act_payload": open_act_payload,
                "provenance_refs": list(matter_rec.provenance_refs),
                "title_hint": str(opp.get("title_hint") or "顺手处理手边真实物件")[:120],
                "topic_key": str(opp.get("title_hint") or "手边物件")[:120],
                "requires_outing": False,
                "strenuous_outing": bool(open_act_payload.get("strenuous_outing")),
                "creative_intensive": False,
                "impulse_purchase": False,
            },
        ))
        matter_added += 1

    # ------------------------------------------------------------------
    # v3.0 Ch.33 §五 (Worker 8C): one optional microzone-comfort candidate, only when
    # the active spot is genuinely salient -- hard glare at the window, or rain
    # splashing in.  A calm/comfortable/quiet microzone contributes nothing (the
    # Background-Silence gate).  The scan is 100% zero-write; a missing engine or
    # state file is a clean no-op.  Shifting desktops is never required: it offers to
    # move to ``desk_inner`` while keeping the half-finished work and breakpoint as-is.
    # ------------------------------------------------------------------
    _mz_state_files = (
        inst_root / "world" / "v08_microzone_state.json",
        inst_root / "world" / "v04_neighborhood_state.json",
    )
    if _microzone_ecology_module() is not None and any(p.is_file() for p in _mz_state_files):
        mz_view = _read_microzone_view_zero_write(inst_root)
        if isinstance(mz_view, Mapping):
            mz = mz_view.get("microzone_ecology")
            if not isinstance(mz, Mapping):
                mz = mz_view
            mz_spot = str(
                mz.get("active_spot_id") or mz.get("spot_id") or mz.get("current_spot") or ""
            ).strip().lower()
            mz_light = str(mz.get("light_quality") or "").strip().lower()
            mz_draft = str(mz.get("draft_state") or "").strip().lower()
            mz_salient = mz_light == "glare" or mz_draft == "rain_splash"
            if mz_spot == "window_side" and mz_salient:
                mz_reason = "rain_splash" if mz_draft == "rain_splash" else "glare"
                mz_target_ref = "v08_microzone:shift_from_glare_or_rain"
                mz_rec = _make_personal_candidate(
                    slug="v08_microzone_shift_from_glare_or_rain_" + _safe_token(mz_reason, "salient"),
                    target_ref=mz_target_ref,
                    causal_parent_refs=(mz_target_ref,),
                    provenance_refs=(mz_target_ref,),
                    availability_basis="V08_MICROZONE_SALIENT_CONDITION",
                )
                mz_title = (
                    "窗边溅进雨点，挪到里面坐，手头的活原样接着"
                    if mz_reason == "rain_splash"
                    else "窗边有点刺眼，挪到里面坐，手头的活原样接着"
                )
                raw_items.append((
                    mz_rec,
                    {
                        "candidate_id": mz_rec.candidate_id,
                        "clue_domain": "v08.microzone.salient_condition",
                        "action_family": "adjust_microzone_comfort",
                        "source_ref": mz_target_ref,
                        "microzone_target_ref": mz_target_ref,
                        "target_spot_id": "desk_inner",
                        "shift_reason": mz_reason,
                        "preserve_work_ref": str(mz.get("preserved_work_ref") or ""),
                        "preserve_breakpoint_cn": str(mz.get("preserved_breakpoint_cn") or ""),
                        "availability_basis": "V08_MICROZONE_SALIENT_CONDITION",
                        "provenance_refs": list(mz_rec.provenance_refs),
                        "title_hint": mz_title[:120],
                        "topic_key": "换个坐处",
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))

    # ------------------------------------------------------------------
    # v0.8 Wave 2 (v3.0 Ch.24 / v0.4 Ch.50): emergent echoes are calm, optional,
    # personal -- never a reminder debt.  At most one open "future self question"
    # re-encounter and at most one untried cross-domain spark are offered per scan.
    # Everything is read 100% zero-write; a missing engine/state contributes nothing,
    # and the ordinary illness/economy gates below still apply cleanly.
    # ------------------------------------------------------------------
    echo_projection = _read_emergent_echoes_projection_zero_write(inst_root)
    echo_added = 0
    if isinstance(echo_projection, Mapping):
        active_text = " ".join(
            " ".join(
                str(desc.get(key) or "")
                for key in ("source_ref", "title_hint", "topic_key", "clue_domain", "action_family")
            )
            for _rec, desc in raw_items
            if isinstance(desc, Mapping)
        ).lower()
        open_questions = [
            row
            for row in (echo_projection.get("future_self_questions") or [])
            if isinstance(row, Mapping) and str(row.get("status") or "open").strip().lower() == "open"
        ]
        if open_questions and echo_added < 1:
            def _question_overlaps(row: Mapping[str, Any]) -> bool:
                hay = " ".join([
                    str(row.get("question_cn") or ""),
                    str(row.get("anchor_ref") or ""),
                    *[str(k) for k in (row.get("keywords") or []) if str(k).strip()],
                ]).lower()
                tokens = [
                    tok for tok in re.split(r"[\s,，、。;；:：()（）「」<>【】]+", hay)
                    if len(tok) >= 2
                ]
                return any(tok in active_text for tok in tokens)

            chosen_question = next(
                (row for row in open_questions if _question_overlaps(row)), None
            )
            # On a free/calm step (nothing much else on the menu) TA may simply
            # stumble onto the question again, even without a live overlap.
            if chosen_question is None and len(raw_items) < 2:
                chosen_question = open_questions[0]
            if chosen_question is not None:
                question_id = str(chosen_question.get("question_id") or "").strip()
                if question_id:
                    question_ref = f"v08_echo:revisit_question:{question_id}"
                    question_text = str(chosen_question.get("question_cn") or "留给自己的问题").strip()
                    question_rec = _make_personal_candidate(
                        slug=f"v08_echo_question_{_safe_token(question_id)}",
                        target_ref=question_ref,
                        causal_parent_refs=(question_ref,),
                        provenance_refs=(question_ref,),
                        availability_basis="V08_FUTURE_QUESTION_REENCOUNTER",
                    )
                    raw_items.append((
                        question_rec,
                        {
                            "candidate_id": question_rec.candidate_id,
                            "clue_domain": "v08.emergent_echoes.future_question",
                            "action_family": "revisit_future_question",
                            "source_ref": question_ref,
                            "question_id": question_id,
                            "availability_basis": "V08_FUTURE_QUESTION_REENCOUNTER",
                            "resolution": "still_curious",
                            "note_cn": "",
                            "provenance_refs": list(question_rec.provenance_refs),
                            "title_hint": f"想想之前留给自己的问题：{question_text}"[:120],
                            "topic_key": f"future_question:{_safe_token(question_id)}",
                            "requires_outing": False,
                            "strenuous_outing": False,
                            "creative_intensive": False,
                            "impulse_purchase": False,
                        },
                    ))
                    echo_added += 1

        untried_sparks = [
            row
            for row in (echo_projection.get("style_and_interest_sparks") or [])
            if isinstance(row, Mapping)
            and str(row.get("status") or "untried").strip().lower() == "untried"
            and str(row.get("mood_pull_today") or "").strip().lower() in ("curious", "eager")
        ]
        if untried_sparks and echo_added < 2:
            spark = untried_sparks[0]
            spark_id = str(spark.get("spark_id") or "").strip()
            if spark_id:
                source_domain = str(spark.get("source_domain") or "手头的事").strip()
                target_domain = str(spark.get("target_domain") or "新尝试").strip()
                try_method = str(spark.get("try_method_cn") or "").strip()
                spark_ref = f"v08_echo:try_spark:{spark_id}"
                choice_trace = (
                    f"把{source_domain}的手感挪一点到{target_domain}，"
                    + (f"先按「{try_method}」试最小的一步" if try_method else "先试最小的一步")
                )[:140]
                spark_rec = _make_personal_candidate(
                    slug=f"v08_echo_spark_{_safe_token(spark_id)}",
                    target_ref=spark_ref,
                    causal_parent_refs=(spark_ref,),
                    provenance_refs=(spark_ref,),
                    availability_basis="V08_CROSS_DOMAIN_SPARK",
                )
                raw_items.append((
                    spark_rec,
                    {
                        "candidate_id": spark_rec.candidate_id,
                        "clue_domain": "v08.emergent_echoes.interest_spark",
                        "action_family": "settle_spark_attempt",
                        "source_ref": spark_ref,
                        "spark_id": spark_id,
                        "availability_basis": "V08_CROSS_DOMAIN_SPARK",
                        "outcome": "helped",
                        "choice_trace_cn": choice_trace,
                        "provenance_refs": list(spark_rec.provenance_refs),
                        "title_hint": f"试试把{source_domain}的手感挪到{target_domain}"[:120],
                        "topic_key": f"spark:{_safe_token(target_domain)}",
                        "requires_outing": False,
                        "strenuous_outing": False,
                        "creative_intensive": False,
                        "impulse_purchase": False,
                    },
                ))
                echo_added += 1

        # Ch.16 §三 #18 / §四 #7: a personal practice is a zero-debt option only.
        # At most one calm cue, and only for a variant still being carried along or an
        # open milestone consideration.  ``faded_quietly`` / omitted practices never
        # nag, and repetition counts never promote anything into a declared ritual.
        ritual_cue_added = False
        for ritual in (echo_projection.get("personal_rituals") or []):
            if ritual_cue_added:
                break
            if not isinstance(ritual, Mapping):
                continue
            ritual_status = str(ritual.get("status") or "").strip().lower()
            if ritual_status not in ("reused_variant", "milestone_option"):
                continue
            ritual_id = str(ritual.get("ritual_id") or "").strip()
            ritual_title = str(ritual.get("title_cn") or "").strip()
            if not ritual_id or not ritual_title:
                continue
            ritual_ref = f"v08_echo:ritual:{_safe_token(ritual_id)}"
            ritual_rec = _make_personal_candidate(
                slug=f"v08_echo_ritual_{_safe_token(ritual_id)}",
                target_ref=ritual_ref,
                causal_parent_refs=(ritual_ref,),
                provenance_refs=(ritual_ref,),
                availability_basis="V08_PERSONAL_RITUAL_ZERO_DEBT",
                observed_iso=effective_now_iso,
            )
            raw_items.append((
                ritual_rec,
                {
                    "candidate_id": ritual_rec.candidate_id,
                    "clue_domain": "v08.emergent_echoes.personal_ritual",
                    "action_family": "carry_personal_ritual",
                    "source_ref": ritual_ref,
                    "ritual_id": ritual_id,
                    "ritual_status": ritual_status,
                    "availability_basis": "V08_PERSONAL_RITUAL_ZERO_DEBT",
                    "omission_debt": 0,
                    "zero_debt": True,
                    "optional": True,
                    "confidence_penalty": 0,
                    "forced_reflection": False,
                    "provenance_refs": list(ritual_rec.provenance_refs),
                    "title_hint": f"想起来可以顺手延续的小习惯：{ritual_title}"[:120],
                    "topic_key": f"ritual:{_safe_token(ritual_id)}",
                    "requires_outing": False,
                    "strenuous_outing": False,
                    "creative_intensive": False,
                    "impulse_purchase": False,
                },
            ))
            ritual_cue_added = True

    # ------------------------------------------------------------------
    # v0.8 composable body: at most one calm, personal body-care candidate.
    # ``pending_garment_care`` outranks ``needs_supply_change`` (garment care is the more
    # concrete, time-sensitive act).  Everything is read 100% zero-write; a missing
    # engine/file contributes nothing, and ``strenuous_outing`` stays False.
    # ------------------------------------------------------------------
    body_projection = _read_composable_body_projection_zero_write(inst_root)
    if isinstance(body_projection, Mapping):
        body_rec, body_desc = _v08_body_candidate_from_projection(body_projection)
        if body_rec is not None and body_desc is not None:
            raw_items.append((body_rec, body_desc))

    # ------------------------------------------------------------------
    # Stage 2 (Pillars 1 & 2): Constraint-arbitrated autonomous desires
    # (wallet tightness, rain/storm, fatigue/illness) + acute body-care
    # candidates from runtime.json & somatic_comfort.json (100% zero-write).
    # ------------------------------------------------------------------
    current_weather = _read_weather_zero_write(inst_root)
    body_snapshot = _read_body_runtime_snapshot_zero_write(inst_root, now_iso=effective_now_iso)
    active_desires = _collect_active_desires_zero_write(
        inst_root, now_iso=effective_now_iso, desire_proj=desire_proj
    )
    arbitrated_desire_items: list[tuple[CandidateRecord, dict[str, Any]]] = []
    desire_arbitration_notes: list[dict[str, Any]] = []
    if active_desires:
        emitted_desire_dicts, desire_arbitration_notes = arbitrate_desire_candidates_for_loop(
            inst_root,
            desires=active_desires,
            economy_info=economy_info,
            illness_info=illness_info,
            body_snapshot=body_snapshot,
            weather=current_weather,
            now_iso=effective_now_iso,
        )
        for d_dict in emitted_desire_dicts:
            if not isinstance(d_dict, Mapping):
                continue
            d_slug = str(d_dict.get("slug") or _safe_token(d_dict.get("desire_id") or "desire"))
            d_src = str(d_dict.get("source_ref") or f"v05_desire:{d_slug}")
            d_rec = _make_personal_candidate(
                slug=d_slug,
                target_ref=str(d_dict.get("target_ref") or d_src),
                causal_parent_refs=(d_src,),
                provenance_refs=list(d_dict.get("provenance_refs") or (d_src,)),
                availability_basis=str(
                    d_dict.get("availability_basis") or "AUTONOMOUS_DESIRE_ARBITRATED_OPEN"
                ),
                observed_iso=effective_now_iso,
            )
            d_desc = dict(d_dict)
            d_desc["candidate_id"] = d_rec.candidate_id
            d_desc["source_ref"] = d_src
            d_desc["provenance_refs"] = list(d_rec.provenance_refs)
            arbitrated_desire_items.append((d_rec, d_desc))
            raw_items.append((d_rec, d_desc))

    acute_body_care_items = _collect_acute_body_care_candidates_zero_write(
        inst_root,
        body_snapshot=body_snapshot,
        illness_info=illness_info,
        observed_iso=effective_now_iso,
    )

    # ------------------------------------------------------------------
    # v0.9 Living Agency & Evolving World Producer (Blueprint §6.3):
    # Bounded zero-write candidates for arrived reservations, pending blind
    # choices, evidence-matched unformed stances, and quiet desk/tin tidying.
    # ------------------------------------------------------------------
    v09_items = _collect_v09_living_agency_and_world_candidates_zero_write(
        inst_root,
        effective_now_iso=effective_now_iso,
        now_dt=now_dt,
        forget_barrier=forget_barrier,
        existing_raw_items=raw_items,
    )
    if v09_items:
        raw_items.extend(v09_items)

    # ------------------------------------------------------------------
    # v3.0 Ch.16: a ready workspace eases starting the next act, and a learned
    # craft method can be recalled for a craft-like candidate.  Both are calm,
    # factual annotations derived live from real objects / the subject's own
    # history -- zero phase score, zero streak, zero obligation.  A missing
    # 9A/9B engine is a clean no-op (``try/except``), never a load failure.
    # ------------------------------------------------------------------
    try:
        scaffold_rows = v08_matter.project_workspace_scaffolds_zero_write(inst_root)
    except Exception:  # noqa: BLE001 - absence of the 9A helper degrades to no cue
        scaffold_rows = []
    _annotate_workspace_scaffold_cues(raw_items, scaffold_rows)
    _annotate_craft_genealogy_hints(raw_items, inst_root)

    surviving: list[tuple[CandidateRecord, dict[str, Any]]] = []
    # Put illness recovery care first when blocks_strenuous_outing or acute fever is active;
    # acute body-care items (damp coat, high fatigue/thirst/hunger/thermal) sit ahead of ordinary clues.
    # Under acute illness, exploratory autobiography sparks sit behind zero-penalty daydream_window
    # so they can never crowd out idle legitimacy under candidate_cap shrink.
    is_acute_illness = bool(cap_env.get("blocks_strenuous_outing") or illness_info["phase_family"] == "acute")
    primary_raw_items: list[tuple[CandidateRecord, dict[str, Any]]] = []
    exploratory_spark_items: list[tuple[CandidateRecord, dict[str, Any]]] = []
    for _item in raw_items:
        _desc = _item[1]
        if (
            str(_desc.get("source_ref") or "").startswith("v02_auto_spark:")
            or _desc.get("action_family") == "explore_autobiography_spark"
            or _desc.get("action_family") in V09_INDOOR_LOW_URGENCY_FAMILIES
        ):
            exploratory_spark_items.append(_item)
        else:
            primary_raw_items.append(_item)
    ordered_pool: list[tuple[CandidateRecord, dict[str, Any]]] = (
        [*illness_care_items, *acute_body_care_items, *primary_raw_items]
        if is_acute_illness
        else [*acute_body_care_items, *raw_items, *illness_care_items]
    )
    if idle_item is not None:
        ordered_pool.append(idle_item)
    if is_acute_illness and exploratory_spark_items:
        ordered_pool.extend(exploratory_spark_items)

    seen_ids: set[str] = set()
    intent_cooldowns = _read_intent_cooldowns_zero_write(inst_root)
    for rec, desc in ordered_pool:
        if rec.candidate_id in seen_ids:
            continue
        seen_ids.add(rec.candidate_id)

        # Gate 0: a previously soft-landed intent stays suppressed until its
        # cooldown expires or the zone/context explicitly changes.
        intent_key = str(desc.get("intent_key") or "").strip()
        if intent_key:
            cooling = intent_cooldowns.get(intent_key)
            if _intent_cooldown_entry_active(
                cooling,
                now_dt=now_dt,
                zone_id=str(desc.get("zone_id") or ""),
            ):
                filtered_candidates.append({
                    "candidate_id": rec.candidate_id,
                    "source_ref": desc["source_ref"],
                    "reason_code": "INTENT_COOLDOWN_ACTIVE",
                    "intent_key": intent_key,
                    "expires_at": cooling.get("expires_at"),
                })
                continue

        # Gate 0b: Forget / DeleteCase read-barrier & tombstone suppression
        if blocked_id_set or forget_barrier.get("barrier_active"):
            from v02_representation_lease_forget import is_entity_forget_blocked as _is_rlf_blocked

            ref_tokens: set[str] = set()
            for k_ref in ("source_ref", "artifact_ref", "candidate_id", "work_id", "inquiry_id", "cue_id", "capsule_id"):
                v_ref = desc.get(k_ref)
                if isinstance(v_ref, str) and v_ref.strip():
                    ref_tokens.add(v_ref.strip())
                    if ":" in v_ref:
                        ref_tokens.add(v_ref.split(":", 1)[1].strip())
            for pref in desc.get("provenance_refs") or ():
                spref = str(pref).strip()
                if spref:
                    ref_tokens.add(spref)
                    if ":" in spref:
                        ref_tokens.add(spref.split(":", 1)[1].strip())
            if (ref_tokens & blocked_id_set) or _is_rlf_blocked(desc, forget_barrier):
                filtered_candidates.append({
                    "candidate_id": rec.candidate_id,
                    "source_ref": desc["source_ref"],
                    "reason_code": "FORGET_BARRIER_HELD",
                })
                continue

        # Annotation: Representation DIVERGED (never silently overwrite) & UNVALIDATED_AFTER_MIGRATION
        if diverged_artifact_set:
            art_val = str(desc.get("artifact_ref") or "").strip()
            art_short = art_val.split(":", 1)[1].strip() if ":" in art_val else art_val
            if (art_val and art_val in diverged_artifact_set) or (art_short and art_short in diverged_artifact_set):
                desc["representation_diverged"] = True
                desc["auto_overwrite_blocked"] = True

        if unvalidated_skill_set:
            sk_val = str(desc.get("skill_id") or desc.get("method_id") or "").strip()
            if sk_val and sk_val in unvalidated_skill_set:
                desc["skill_unvalidated_after_migration"] = True
                desc["requires_reverify"] = True

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
    # 0.2 Deep Governance: Runtime Mode (Retirement Seal / Dormant / Low-Budget) + Agency Depth (Satiation & 2nd-Order Rules)
    # ------------------------------------------------------------------
    retirement_info = _read_retirement_status_zero_write(inst_root)
    retired_sealed = bool(retirement_info.get("retired_sealed"))
    runtime_health_state = str(
        runtime_migration_summary.get("health_state") or runtime_migration_summary.get("mode") or ""
    ).strip().upper()
    runtime_mode_raw = str(runtime_migration_summary.get("mode") or "").strip().upper()
    if retired_sealed:
        runtime_health_state = "DORMANT_NO_COGNITION"
        runtime_mode_raw = "DORMANT"
    dormant_suppressed = (
        retired_sealed
        or runtime_health_state == "DORMANT_NO_COGNITION"
        or runtime_mode_raw == "DORMANT"
    )
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
    second_order_constrained_candidates: list[dict[str, Any]] = []

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
        # F-101: evaluate active second-order rules (100% zero-write) and genuinely filter blocked candidates.
        _eval_second_order_fn = None
        _so_state_file = inst_root / "state" / "v02_agency_depth_state.json"
        if _so_state_file.is_file() and not _so_state_file.is_symlink():
            try:
                from components.life.scripts import v02_agency_depth_engine as _ad_so_mod
                _eval_second_order_fn = getattr(_ad_so_mod, "evaluate_second_order_constraints", None)
            except Exception:
                try:
                    import v02_agency_depth_engine as _ad_so_mod  # type: ignore[no-redef]
                    _eval_second_order_fn = getattr(_ad_so_mod, "evaluate_second_order_constraints", None)
                except Exception:
                    _eval_second_order_fn = None

        if callable(_eval_second_order_fn) and surviving:
            so_kept: list[tuple[CandidateRecord, dict[str, Any]]] = []
            for rec, desc in surviving:
                cand_kind = str(desc.get("action_family") or desc.get("clue_domain") or "")
                cand_title = str(
                    desc.get("title_hint")
                    or desc.get("title")
                    or desc.get("note")
                    or desc.get("topic_key")
                    or desc.get("source_ref")
                    or ""
                )
                cand_tags: list[str] = []
                for tk in ("tags", "applicability_tags"):
                    seq = desc.get(tk)
                    if isinstance(seq, (list, tuple)):
                        cand_tags.extend(str(x).strip() for x in seq if str(x).strip())
                for flag_name in (
                    "creative_intensive",
                    "strenuous_outing",
                    "requires_outing",
                    "impulse_purchase",
                ):
                    if desc.get(flag_name):
                        cand_tags.append(flag_name)
                if cand_kind:
                    cand_tags.append(cand_kind)
                so_res = _eval_second_order_fn(
                    inst_root,
                    candidate_kind=cand_kind,
                    candidate_title=cand_title,
                    tags=cand_tags,
                    now_iso=effective_now_iso,
                )
                if isinstance(so_res, Mapping) and so_res.get("constrained"):
                    blocking = list(so_res.get("blocking_rules") or [])
                    first_rl = blocking[0] if blocking else {}
                    second_order_constrained_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "candidate_ref": desc["source_ref"],
                        "source_ref": desc["source_ref"],
                        "action_family": cand_kind,
                        "candidate_title": cand_title,
                        "rule_id": str(first_rl.get("rule_id") or ""),
                        "rule_title": str(first_rl.get("title") or first_rl.get("rule_id") or ""),
                        "trigger_window": first_rl.get("trigger_window"),
                        "blocking_rules": blocking,
                        "reason_cn": str(so_res.get("reason_cn") or "受二阶小规矩约束延后"),
                        "disposition": "filtered_by_second_order_rule",
                    })
                    filtered_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "reason_code": "SECOND_ORDER_RULE_CONSTRAINED",
                        "rule_id": first_rl.get("rule_id"),
                        "rule_title": first_rl.get("title"),
                    })
                    second_order_rule_adjusted.append({
                        "candidate_id": rec.candidate_id,
                        "source_ref": desc["source_ref"],
                        "rule_id": first_rl.get("rule_id"),
                        "rule_title": first_rl.get("title"),
                        "trigger_window": first_rl.get("trigger_window"),
                        "adjusted_weight": 0.0,
                        "disposition": "filtered_by_second_order_rule",
                    })
                    continue
                so_kept.append((rec, desc))
            surviving = so_kept
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

            if effective_second_order_rules and _eval_second_order_fn is None:
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
                    second_order_constrained_candidates.append({
                        "candidate_id": rec.candidate_id,
                        "candidate_ref": desc["source_ref"],
                        "source_ref": desc["source_ref"],
                        "action_family": fam,
                        "candidate_title": str(
                            desc.get("title_hint")
                            or desc.get("title")
                            or desc.get("source_ref")
                            or fam
                        ),
                        "rule_id": str(matched_rl.get("rule_id") or ""),
                        "rule_title": str(matched_rl.get("title") or matched_rl.get("rule_id") or ""),
                        "trigger_window": matched_rl.get("trigger_window"),
                        "reason_cn": str(matched_rl.get("reason_cn") or "受二阶小规矩约束延后"),
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
        if (
            illness_info["is_actively_ill"]
            and not dormant_conservation
            and not low_budget_conservation
            and candidate_cap >= 1
        ):
            _idle_idx = next(
                (
                    i
                    for i, (_r, _d) in enumerate(surviving)
                    if str(_d.get("source_ref") or "") == "v03_idle:daydream_window"
                ),
                None,
            )
            if _idle_idx is not None and _idle_idx >= candidate_cap:
                _spark_idx = next(
                    (
                        i
                        for i in range(candidate_cap - 1, -1, -1)
                        if str(surviving[i][1].get("source_ref") or "").startswith("v02_auto_spark:")
                        or surviving[i][1].get("action_family") == "explore_autobiography_spark"
                        or surviving[i][1].get("action_family") in V09_INDOOR_LOW_URGENCY_FAMILIES
                    ),
                    None,
                )
                if _spark_idx is not None:
                    surviving[_spark_idx], surviving[_idle_idx] = surviving[_idle_idx], surviving[_spark_idx]
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

    matched_scrap_ids: list[str] = []
    lived_slices_path = inst_root / "state" / "v02_lived_slices_state.json"
    if lived_slices_path.is_file() and not lived_slices_path.is_symlink() and surviving:
        try:
            import v02_lived_slices_and_vd_protocols as _ls_mod

            for _rec, desc in surviving:
                raw_tags: list[str] = []
                for tk in ("tags", "applicability_tags"):
                    seq = desc.get(tk)
                    if isinstance(seq, (list, tuple)):
                        raw_tags.extend(str(x).strip() for x in seq if str(x).strip())
                for tk in ("topic_key", "title_hint", "clue_domain", "action_family"):
                    val = str(desc.get(tk) or "").strip()
                    if val:
                        raw_tags.extend(
                            tok
                            for tok in re.split(r"[\s,，、.;:：/_-]+", val)
                            if len(tok) >= 2
                        )
                if raw_tags:
                    m_res = _ls_mod.match_salvaged_scraps_for_task(
                        inst_root,
                        task_tags=raw_tags,
                        has_subject_capability=True,
                        now_iso=effective_now_iso,
                    )
                    from v02_representation_lease_forget import is_entity_forget_blocked as _is_rlf_blocked

                    cands = [
                        c
                        for c in (m_res.get("matched_candidates") or ())
                        if isinstance(c, Mapping)
                        and str(c.get("scrap_id") or "") not in blocked_id_set
                        and not _is_rlf_blocked(c, forget_barrier)
                    ]
                    if cands:
                        desc["matched_scraps"] = cands
                        for c in cands:
                            sid = str(c.get("scrap_id") or "")
                            if sid and sid not in matched_scrap_ids:
                                matched_scrap_ids.append(sid)
        except Exception:
            pass

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
        "retired_sealed": retired_sealed,
        "dormant_suppressed": dormant_suppressed,
        "dormant_conservation": dormant_conservation,
        "low_budget_conservation": low_budget_conservation,
        "satiation_adjusted_count": len(satiation_adjusted),
        "second_order_rule_adjusted_count": len(second_order_rule_adjusted),
        "second_order_constrained_count": len(second_order_constrained_candidates),
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
        "barrier_active": bool(gov_signals.get("barrier_active")),
        "diverged_artifact_ids": list(gov_signals.get("diverged_artifact_ids") or ()),
        "unvalidated_after_migration_skills": sorted(unvalidated_skill_set),
        "matched_scrap_ids": matched_scrap_ids,
        "midscale_absence_count": len(midscale_absence_observations),
        "autobiography_spark_opportunity_count": len(autobiography_spark_opportunities),
        "arbitrated_desire_count": len(arbitrated_desire_items),
        "acute_body_care_count": len(acute_body_care_items),
        "llm_calls": 0,
    }

    return {
        "schema": BRIDGE_SCHEMA,
        "read_only": True,
        "zero_write": True,
        "llm_calls": 0,
        "observed_at": effective_now_iso,
        "local_date": local_date,
        "retired_sealed": retired_sealed,
        "dormant_suppressed": dormant_suppressed,
        "dormant_conservation": dormant_conservation,
        "low_budget_conservation": low_budget_conservation,
        "satiation_adjusted": satiation_adjusted,
        "second_order_rule_adjusted": second_order_rule_adjusted,
        "second_order_constrained_candidates": second_order_constrained_candidates,
        "candidates": [rec.to_dict() for rec in candidate_records],
        "candidate_records": candidate_records,
        "descriptors": descriptors,
        "midscale_absence_observations": midscale_absence_observations,
        "autobiography_spark_opportunities": autobiography_spark_opportunities,
        "gating": {
            "illness": illness_info,
            "economy": economy_info,
            "runtime_health_state": runtime_health_state or "QUIET_HEALTHY",
            "retired_sealed": retired_sealed,
            "dormant_suppressed": dormant_suppressed,
            "dormant_conservation": dormant_conservation,
            "low_budget_conservation": low_budget_conservation,
            "satiation_adjusted": satiation_adjusted,
            "second_order_rule_adjusted": second_order_rule_adjusted,
            "second_order_constrained_candidates": second_order_constrained_candidates,
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
            "emotional_tension_summary": emotional_tension_summary,
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
            "governance_signals": gov_signals,
            "unvalidated_after_migration_skills": sorted(unvalidated_skill_set),
            "matched_scrap_ids": matched_scrap_ids,
            "weather": current_weather,
            "body_runtime_snapshot": body_snapshot,
            "desire_arbitration_notes": desire_arbitration_notes,
            "acute_body_care_emitted": [
                str(d.get("body_care_action") or d.get("action_family") or "")
                for _r, d in acute_body_care_items
            ],
        },
        "resident_context_summary": resident_context_summary,
    }


# ---------------------------------------------------------------------------
# v0.4 Ch.45 §三/§六: wake & interruption re-entry and situational capability
# awareness.  Both are pure zero-write readers (they never mkdir / create a byte).
# ---------------------------------------------------------------------------

WAKE_REENTRY_SCHEMA = "chiyo.life.v04_wake_reentry.v1"
SITUATIONAL_CAPABILITY_SCHEMA = "chiyo.life.v04_situational_capability.v1"

_DESK_SURFACE_TOKENS = ("desk", "table", "counter", "sill", "桌", "台")

_CAPABILITY_OBJECT_CUES: tuple[tuple[str, str], ...] = (
    ("paper", "手边有纸页，可以翻看或记两笔"),
    ("tool", "手边有笔一类的工具，可以写画"),
    ("vessel", "手边有杯碗，可以倒水或顺手收拾"),
    ("drink", "手边有饮品，可以先喝一口"),
    ("ingredient", "手边有食材，可以简单弄点吃的"),
    ("keepsake", "手边有留念小物，可以看看"),
)

_UNSUPPORTED_CAPABILITY_CN: dict[str, str] = {
    "ORIGAMI_FOLD_GEOMETRY": "把纸折成特定形状的几何动作",
    "IGNITION_OR_BURNING": "点火或烧东西",
    "PAPERWEIGHT_WIND_LOCK": "用镇纸压住风",
    "SEWING_OR_SOLDERING": "缝补或焊接",
}


def _wake_room_and_body_zero_write(
    base: Path,
) -> tuple[list[dict[str, Any]], str]:
    """Read the room's uncollected objects and a small body-comfort note."""

    room_items: list[dict[str, Any]] = []
    matter: Optional[Mapping[str, Any]] = None
    for rel in ("world/v08_universal_matter.json", "world/v08_matter_space.json"):
        doc = _read_optional_json_zero_write(base / rel)
        if isinstance(doc, Mapping):
            matter = doc
            break
    if isinstance(matter, Mapping):
        raw_objects = matter.get("matter_objects")
        if not isinstance(raw_objects, (Mapping, list)):
            raw_objects = matter.get("objects")
        if isinstance(raw_objects, Mapping):
            object_rows = list(raw_objects.values())
        elif isinstance(raw_objects, list):
            object_rows = raw_objects
        else:
            object_rows = []
        for row in object_rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("consumed") is True or row.get("discarded") is True:
                continue
            parent = str(row.get("parent_id") or row.get("zone_id") or "").strip()
            lowered = parent.lower()
            if not any(token in lowered for token in _DESK_SURFACE_TOKENS):
                continue
            label = str(
                row.get("label_cn") or row.get("label") or row.get("object_id") or ""
            ).strip()
            if not label:
                continue
            room_items.append({
                "kind": "uncollected_item",
                "object_id": str(row.get("object_id") or ""),
                "label_cn": label[:40],
                "parent_id": parent[:48],
                "note_cn": f"{label[:24]}还留在{parent or '原处'}上，没有替你收走",
            })
            if len(room_items) >= 6:
                break

    comfort_parts: list[str] = []
    somatic = _read_optional_json_zero_write(base / "phone" / "somatic_comfort.json")
    if isinstance(somatic, Mapping):
        window = somatic.get("window") if isinstance(somatic.get("window"), Mapping) else {}
        window_state = str(window.get("state") or somatic.get("window_state") or "").strip().lower()
        position = somatic.get("local_position") if isinstance(somatic.get("local_position"), Mapping) else {}
        zone = str(position.get("zone") or somatic.get("local_zone") or "").strip().lower()
        zone_cn = {"window_side": "在窗边", "desk_side": "在桌边"}.get(zone, "")
        if zone_cn:
            comfort_parts.append(zone_cn)
        if window_state in ("open", "ajar"):
            comfort_parts.append("窗扇开着一点" if window_state == "open" else "窗扇半掩")
        care = somatic.get("care") if isinstance(somatic.get("care"), Mapping) else {}
        slot = care.get("slot") or somatic.get("care_slot")
        if slot:
            comfort_parts.append("手边有照料的小物")
    body_comfort_note = (
        "；".join(comfort_parts)[:120] + "，先按身体舒服来" if comfort_parts else ""
    )
    return room_items, body_comfort_note


def project_wake_and_interruption_reentry_zero_write(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
    sleep_just_ended: bool = False,
    host_interrupted: bool = False,
) -> dict[str, Any]:
    """Zero-write wake / interruption re-entry: room + body FIRST, work optional.

    A ``resting_not_feeling_it`` project is deliberately excluded from
    ``resumable_threads``: waking up or being interrupted never forces a resume.
    A missing directory/file simply produces an empty, clean structure and is
    never created here.
    """

    base = Path(root)
    now_dt = _parse_iso(now_iso) or datetime.now(DEFAULT_TZ)
    observed_iso = _format_iso(now_dt)

    room_items, body_comfort_note = _wake_room_and_body_zero_write(base)

    threads: list[dict[str, Any]] = []
    ep = _read_optional_json_zero_write(base / "phone" / "v04_elastic_play.json")
    if isinstance(ep, Mapping):
        dd = ep.get("day_direction") if isinstance(ep.get("day_direction"), Mapping) else {}
        dd_status = str(dd.get("status") or "").strip().lower()
        dd_text = str(dd.get("direction") or dd.get("current_focus") or "").strip()
        if dd_text and dd_status not in ("", "idle", "cleared", "dropped"):
            threads.append({
                "kind": "day_direction",
                "ref": "v04_elastic_play:day_direction",
                "title_cn": dd_text[:80],
                "forced_resume": False,
            })
        for row in ep.get("personal_projects") or []:
            if not isinstance(row, Mapping):
                continue
            status = str(row.get("status") or "active").strip().lower()
            if status != "active":
                # Shelved projects must not become a wake-up nag.
                continue
            project_id = str(row.get("project_id") or "").strip()
            title = str(row.get("purpose_cn") or "").strip()
            if not project_id or not title:
                continue
            threads.append({
                "kind": "personal_project",
                "ref": f"v04_personal_project:{project_id}",
                "project_id": project_id,
                "title_cn": title[:80],
                "next_step_cn": str(row.get("next_step_cn") or "")[:80],
                "forced_resume": False,
            })
        for exp in ep.get("experiments") or []:
            if not isinstance(exp, Mapping):
                continue
            if str(exp.get("status") or "").strip().lower() != "active":
                continue
            title = str(exp.get("title") or "").strip()
            if title:
                threads.append({
                    "kind": "experiment",
                    "ref": f"v04_experiment:{exp.get('experiment_id') or ''}",
                    "title_cn": title[:80],
                    "forced_resume": False,
                })

    micro = _read_optional_json_zero_write(base / "world" / "v08_microzone_state.json")
    if not isinstance(micro, Mapping):
        micro = _read_optional_json_zero_write(base / "world" / "v04_neighborhood_state.json")
    if isinstance(micro, Mapping):
        breakpoint_cn = str(
            micro.get("preserved_breakpoint_cn") or micro.get("breakpoint_cn") or ""
        ).strip()
        preserved_ref = str(micro.get("preserved_work_ref") or "").strip()
        if breakpoint_cn or preserved_ref:
            threads.append({
                "kind": "work_breakpoint",
                "ref": preserved_ref or "v08_microzone:preserved_breakpoint",
                "title_cn": (breakpoint_cn or "手头的活停在原处")[:80],
                "forced_resume": False,
            })

    active_project_hint = ""
    for thread in threads:
        if thread.get("kind") == "personal_project":
            active_project_hint = (
                str(thread.get("next_step_cn") or "") or str(thread.get("title_cn") or "")
            )
            break

    return {
        "schema": WAKE_REENTRY_SCHEMA,
        "observed_at": observed_iso,
        "zero_write": True,
        "sleep_just_ended": bool(sleep_just_ended),
        "host_interrupted": bool(host_interrupted),
        "room_and_uncollected_first": room_items,
        "body_comfort_note": body_comfort_note,
        "resumable_threads": threads[:6],
        "active_project_hint": active_project_hint[:120],
        "force_midnight_summary": False,
        "guilt_debt": 0,
        "forced_resume": False,
        "llm_calls": 0,
    }


def _scoped_failure_phrase(category: str, reason_code: str) -> str:
    haystack = f"{category} {reason_code}".lower()
    if any(token in haystack for token in ("read", "page", "book", "阅读", "书页")):
        return "这次阅读读取未成功，不等于书不存在"
    if any(token in haystack for token in ("size", "sizing", "craft", "fold", "fit", "裁", "折", "尺寸")):
        return "这一步没做通，不代表手头别的事都不行"
    return "刚才那一步卡住了，只管这一步，不影响别的事"


def project_situational_capability_awareness_zero_write(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Derive "what I can do here / can't yet" from real objects and cooldowns only.

    No hardcoded personality, no sweeping generalization: a single failed read or
    size step stays scoped to that step and never lowers confidence elsewhere.
    """

    base = Path(root)
    now_dt = _parse_iso(now_iso) or datetime.now(DEFAULT_TZ)
    observed_iso = _format_iso(now_dt)

    matter: Optional[Mapping[str, Any]] = None
    for rel in ("world/v08_universal_matter.json", "world/v08_matter_space.json"):
        doc = _read_optional_json_zero_write(base / rel)
        if isinstance(doc, Mapping):
            matter = doc
            break
    kinds_present: set[str] = set()
    if isinstance(matter, Mapping):
        raw_objects = matter.get("matter_objects")
        if not isinstance(raw_objects, (Mapping, list)):
            raw_objects = matter.get("objects")
        rows = (
            list(raw_objects.values())
            if isinstance(raw_objects, Mapping)
            else (raw_objects if isinstance(raw_objects, list) else [])
        )
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("consumed") is True or row.get("discarded") is True:
                continue
            kind = str(row.get("item_kind") or "").strip().lower()
            if kind:
                kinds_present.add(kind)

    can_do_here: list[dict[str, Any]] = []
    for kind, phrase in _CAPABILITY_OBJECT_CUES:
        if kind in kinds_present:
            can_do_here.append({"item_kind": kind, "capability_cn": phrase})

    cannot_do_here: list[dict[str, Any]] = []
    scoped_failure_notes: list[dict[str, Any]] = []
    cooldowns = _read_intent_cooldowns_zero_write(base)
    for intent_key, row in cooldowns.items():
        if not _intent_cooldown_entry_active(row, now_dt=now_dt):
            continue
        category = str(row.get("category") or "unsupported_capability")
        reason_code = str(row.get("reason_code") or "")
        cannot_do_here.append({
            "category": category,
            "capability_cn": _UNSUPPORTED_CAPABILITY_CN.get(
                category, f"暂时做不了的：{category}"
            )[:80],
            "expires_at": str(row.get("expires_at") or ""),
        })
        scoped_failure_notes.append({
            "intent_key": str(intent_key),
            "category": category,
            "scoped_note_cn": _scoped_failure_phrase(category, reason_code),
            "generalized": False,
            "applies_to_other_tasks": False,
        })

    silent = not (can_do_here or cannot_do_here or scoped_failure_notes)
    return {
        "schema": SITUATIONAL_CAPABILITY_SCHEMA,
        "observed_at": observed_iso,
        "zero_write": True,
        "can_do_here": can_do_here[:8],
        "cannot_do_here": cannot_do_here[:8],
        "scoped_failure_notes": scoped_failure_notes[:8],
        "silent": silent,
        "generalized": False,
        "confidence_penalty": 0,
        "contract": {
            "basis": "real_reachable_objects_and_active_cooldowns",
            "hardcoded_personality": False,
            "single_failure_contained": True,
        },
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Deterministic Policy Selector (0 LLM Calls) for Soak & 2x2 Counterfactuals
# ---------------------------------------------------------------------------


def select_deterministic_loop_action(
    collected: Mapping[str, Any],
    *,
    incoming_push_text: Optional[str] = None,
    recent_note_streak: int = 0,
    is_final_closeout_step: bool = False,
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
            "retired_sealed": bool(
                collected.get("retired_sealed") or gating.get("retired_sealed")
            ),
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

    streak_count = max(0, int(recent_note_streak or 0))
    stuffy_info = compute_desk_stuffiness(
        ["continue_note"] * streak_count,
        is_final_closeout_step=bool(is_final_closeout_step),
    )
    weather_now = str(gating.get("weather") or collected.get("weather") or "clear").strip().lower()
    rain_heavy = weather_now in ("rain", "storm", "heavy_rain", "thunder_blackout", "sea_gale")
    anti_loop_active = bool(stuffy_info.get("stuffy")) and not rain_heavy
    outing_cue = (
        build_outing_reason(stuffy=True, rain_heavy=rain_heavy)
        if bool(stuffy_info.get("stuffy"))
        else None
    )

    # Priority order of personal life clues when healthy / unblocked:
    # 1. Due bedside tomorrow note -> 2. Affordable purchase deliberation ->
    # 3. Workbench unfinished draft -> 4. Independent work (when budget tight / saving) ->
    # 5. Scheduled tomorrow note -> 6. Co-authored tentative plan ->
    # 7. Outing / bookstore / plant -> 8. Meaningless idle
    if anti_loop_active:
        priority_predicates = (
            lambda r: r.get("action_family")
            in ("strenuous_outing_walk", "follow_bookstore_bookmark", "observe_plant_journal", "courtyard_care"),
            lambda r: r.get("action_family") in V04_ACTION_FAMILIES_SELECTABLE,
            lambda r: r.get("action_family") == "non_essential_purchase"
            and r.get("purchase_verdict") == "afford_independently",
            lambda r: r.get("action_family") == "follow_tomorrow_note" and bool(r.get("due_now")),
            lambda r: r.get("action_family") == "adopt_tentative_plan",
            lambda r: r.get("action_family") == "compose_open_act",
            lambda r: r.get("action_family") == "advance_workbench_draft",
            lambda r: r.get("action_family") == "independent_work",
            lambda r: r.get("action_family") == "follow_tomorrow_note",
            lambda r: r.get("action_family") == "advance_bounded_inquiry",
            lambda r: r.get("action_family")
            in ("decide_partner_item_gift", "post_matured_slow_letter_draft"),
            lambda r: r.get("action_family") == "illness_rest_recovery",
            lambda r: r.get("action_family") in ACUTE_BODY_CARE_FAMILIES,
            lambda r: r.get("action_family") in ("revisit_future_question", "settle_spark_attempt"),
            lambda r: r.get("action_family") in ("inspect_room_absence", "explore_autobiography_spark"),
            lambda r: r.get("action_family") == "consider_rescaled_copractice",
            lambda r: r.get("action_family") in ("pursue_autonomous_desire", "pursue_desire_alternative"),
            lambda r: r.get("action_family") in ("pickup_arrived_commitment", "commit_blind_choice", "reflect_unformed_stance", "tidy_custom_shelf_or_tin"),
            lambda r: r.get("action_family") == "meaningless_idle",
        )
    else:
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
            lambda r: r.get("action_family") == "advance_bounded_inquiry",
            # v0.8 Wave 2: 院子晾晒 / 盆栽 / 纸盒手作 / 街角借还也是真实的个人日常机会。
            lambda r: r.get("action_family") in V04_ACTION_FAMILIES_SELECTABLE,
            lambda r: r.get("action_family")
            in ("decide_partner_item_gift", "post_matured_slow_letter_draft"),
            lambda r: r.get("action_family") == "illness_rest_recovery",
            lambda r: r.get("action_family") in ACUTE_BODY_CARE_FAMILIES,
            # v0.8 universal matter: a real object combination (cook/eat/chill/clean/observe)
            # is a genuine everyday opportunity, above a merely meaningless idle.
            lambda r: r.get("action_family") == "compose_open_act",
            # v0.8 Wave 2: re-encountering one's own future question or testing one gentle
            # cross-domain spark is a real, low-pressure personal option -- above pure idle.
            lambda r: r.get("action_family") in ("revisit_future_question", "settle_spark_attempt"),
            # Wave B1 (F-175 / F-176): room absence inspection & zero-debt autobiography spark.
            lambda r: r.get("action_family") in ("inspect_room_absence", "explore_autobiography_spark"),
            # F-189: partner rescaled co-practice proposal (zero-debt, optional consideration).
            lambda r: r.get("action_family") == "consider_rescaled_copractice",
            # Stage 2: constraint-arbitrated autonomous desires & zero-debt alternatives.
            lambda r: r.get("action_family") in ("pursue_autonomous_desire", "pursue_desire_alternative"),
            lambda r: r.get("action_family") in ("pickup_arrived_commitment", "commit_blind_choice", "reflect_unformed_stance", "tidy_custom_shelf_or_tin"),
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
                    out: dict[str, Any] = {
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
                    if streak_count >= 2:
                        out["desk_stuffiness"] = dict(stuffy_info)
                        out["outdoor_push_suppressed"] = bool(rain_heavy or is_final_closeout_step)
                        if isinstance(outing_cue, Mapping) and outing_cue.get("label_cn"):
                            out["reason_hint_cn"] = str(outing_cue["label_cn"])
                    return out

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


def _v04_neighborhood_view_module() -> Any:
    from components.phone import v04_neighborhood_view as _v04_view

    return _v04_view


def _v04_operation_key(raw: Any, fallback: str = "v04") -> str:
    """Deterministic 128-char operation key (``_safe_token`` caps at 64, too short here)."""

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "")).strip("_.-")
    return (cleaned or fallback)[:128]


def _v04_clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, Mapping) and not value:
            continue
        cleaned[str(key)] = value
    return cleaned


def _v04_elastic_play_view_module() -> Any:
    from components.phone import v04_elastic_play_view as _v04_ep_view

    return _v04_ep_view


def _v04_elastic_settlement_command(
    round_event: Mapping[str, Any],
) -> Optional[tuple[str, Optional[dict[str, Any]]]]:
    """Resolve one round event into an elastic-play command, or ``None``.

    Returns ``("append_shared_notebook", None)`` when the subject chose to reply
    on the shared notebook but the round event carries no text: the caller then
    soft-lands with zero writes instead of inventing a sentence.  Non-elastic
    events (neighborhood / 0.2 daily families) return ``None`` so the existing
    settlement owners keep their exact paths.
    """

    family = str(round_event.get("action_family") or "").strip()
    action_kind = str(round_event.get("action_kind") or "").strip().upper()
    declared = str(round_event.get("v04_action") or "").strip()

    if declared in ("append_shared_notebook", "prepare_or_offer_surprise"):
        action = declared
    elif action_kind == "ELASTIC_SHARED_NOTEBOOK" or family == "elastic_shared_notebook":
        action = "append_shared_notebook"
    elif action_kind == "ELASTIC_OFFER_SURPRISE" or family == "elastic_offer_surprise":
        action = "prepare_or_offer_surprise"
    else:
        return None

    payload = dict(round_event.get("v04_payload") or {})
    payload["action"] = action
    if action == "append_shared_notebook":
        text = ""
        for key in ("notebook_text", "reply_text", "text", "body", "note_cn"):
            value = round_event.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            for key in ("text", "body"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
        if not text:
            return action, None
        payload["text"] = text[:500]
    else:
        # Only the "offer" stage is a daily-loop action; preparing stays a private act.
        payload["stage"] = "offer"
        surprise_id = str(round_event.get("surprise_id") or "").strip()
        if surprise_id:
            payload["surprise_id"] = surprise_id
    return action, _v04_clean_payload(payload)


def _v04_ref_id(round_event: Mapping[str, Any], prefix: str) -> Optional[str]:
    for key in ("v04_item_id", "item_id", "cloth_id", "plant_id", "card_id", "tool_id", "book_id", "box_id"):
        value = round_event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = round_event.get("source_ref") or round_event.get("v04_source_ref")
    if isinstance(raw, str) and raw.startswith(prefix + ":"):
        return raw.split(":", 1)[1].strip() or None
    return None


def _v04_next_craft_payload(state: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    craft = state.get("craft_bench") if isinstance(state.get("craft_bench"), Mapping) else {}
    box = craft.get("card_organizer_box") if isinstance(craft.get("card_organizer_box"), Mapping) else {}
    next_step = {
        "prepared": "divider_cut",
        "divider_cut": "trial_fitted",
        "trial_fitted": "finished",
    }.get(str(box.get("step") or ""))
    if next_step:
        payload: dict[str, Any] = {"operation": "box_step", "target_step": next_step}
        if next_step == "divider_cut":
            payload["units"] = 1
        return payload
    cards = craft.get("cards") if isinstance(craft.get("cards"), Mapping) else {}
    for card_id in sorted(cards):
        card = cards.get(card_id)
        if not isinstance(card, Mapping):
            continue
        if int(card.get("inscribed_revision") or 0) > 0:
            continue
        draft = card.get("draft_text")
        if isinstance(draft, str) and draft.strip():
            return {"operation": "inscribe_card", "card_id": card_id, "text": draft}
    return None


def _v04_return_payload(
    round_event: Mapping[str, Any],
    inst_root: Path,
    now_dt: datetime,
) -> Optional[dict[str, Any]]:
    item_id = _v04_ref_id(round_event, "v04_errand_return")
    if item_id is None:
        return None
    kind = str(round_event.get("v04_loan_kind") or "").strip().lower()
    state = _load_v04_neighborhood_state_zero_write(inst_root)
    if not kind and state is not None:
        books = (
            ((state.get("public_life") or {}).get("bookstore") or {}).get("books")
            if isinstance(state.get("public_life"), Mapping)
            else None
        )
        shelf = (
            ((state.get("public_life") or {}).get("shared_shelf") or {}).get("items")
            if isinstance(state.get("public_life"), Mapping)
            else None
        )
        if isinstance(books, Mapping) and item_id in books:
            kind = "bookstore"
        elif isinstance(shelf, Mapping) and item_id in shelf:
            kind = "shared_shelf"
    if not kind:
        kind = "bookstore" if item_id.startswith("book_") else "shared_shelf"
    if kind == "bookstore":
        payload: dict[str, Any] = {"service": "return_book", "book_id": item_id}
        via_box = round_event.get("via_return_box")
        if via_box is None:
            via_box = not _v04_engine_open_now(now_dt)
        if bool(via_box):
            payload["via_return_box"] = True
        return payload
    return {"operation": "return", "item_id": item_id}


def _v04_build_settlement_command(
    inst_root: Path,
    round_event: Mapping[str, Any],
    *,
    now_dt: datetime,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Resolve one round event into ``(action, payload)`` for the v0.4 view.

    Accepts, in order: an explicit ``v04_action`` + ``v04_payload`` (what the
    resident passes), a literal ``action="v04_..."`` shortcut, or one of the six
    named ``action_kind`` values.  Returns ``None`` when this event is not a
    neighborhood action at all.
    """

    declared_action = round_event.get("v04_action")
    if isinstance(declared_action, str) and declared_action.strip():
        action = declared_action.strip()
        # ``micro_intent`` is the Round C2 bridge door, not a v0.4 neighborhood
        # action; it settles through the matter classifier above this builder.
        if action.lower() == "micro_intent":
            return None
        payload = dict(round_event.get("v04_payload") or {})
        payload["action"] = action
        return action, _v04_clean_payload(payload)

    literal = str(round_event.get("action") or "").strip()
    if literal.startswith("v04_"):
        prefix = literal.split(":", 1)[0]
        item_id = _v04_ref_id(round_event, prefix)
        if prefix == "v04_courtyard_hang":
            target = str(round_event.get("target_support") or "yard_line").strip()
            return "hang_or_collect_cloth", _v04_clean_payload({
                "action": "hang_or_collect_cloth",
                "operation": str(round_event.get("operation") or "hang"),
                "item_id": item_id,
                "target_support": target,
            })
        if prefix == "v04_courtyard_cloth":
            return "hang_or_collect_cloth", _v04_clean_payload({
                "action": "hang_or_collect_cloth",
                "operation": str(round_event.get("operation") or "move"),
                "item_id": item_id,
                "target_support": str(round_event.get("target_support") or "porch_hook"),
            })
        if prefix == "v04_courtyard_collect":
            return "hang_or_collect_cloth", _v04_clean_payload({
                "action": "hang_or_collect_cloth",
                "operation": "collect",
                "item_id": item_id,
                "target_support": str(round_event.get("target_support") or "home_rack"),
            })
        if prefix == "v04_porch_plant":
            op = str(round_event.get("operation") or "water")
            payload = {"action": "water_or_move_plant", "operation": op, "plant_id": item_id}
            if op == "move":
                payload["target_place"] = str(round_event.get("target_place") or "porch")
            else:
                payload["water_amount"] = float(round_event.get("water_amount") or 45.0)
            return "water_or_move_plant", _v04_clean_payload(payload)
        if prefix == "v04_home_craft":
            payload = dict(round_event.get("v04_payload") or {})
            if not payload:
                state = _load_v04_neighborhood_state_zero_write(inst_root)
                payload = (_v04_next_craft_payload(state) if state is not None else None) or {}
            payload["action"] = "craft_paper_or_box"
            return "craft_paper_or_box", _v04_clean_payload(payload)
        if prefix == "v04_errand_return":
            payload = _v04_return_payload(round_event, inst_root, now_dt)
            if payload is None:
                return None
            if "service" in payload:
                payload["action"] = "bookstore_service"
                return "bookstore_service", _v04_clean_payload(payload)
            payload["action"] = "shared_shelf_loan"
            return "shared_shelf_loan", _v04_clean_payload(payload)
        if prefix == "v04_cafe_resident":
            payload = dict(round_event.get("v04_payload") or {})
            for k in ("operation", "service", "coaster_note", "note", "errand_note", "route_id", "claim_cn"):
                if k not in payload and round_event.get(k) is not None:
                    payload[k] = round_event[k]
            payload.setdefault("operation", "check_neighbor_schedule")
            payload["action"] = "interact_cafe_resident"
            return "interact_cafe_resident", _v04_clean_payload(payload)

    action_kind = str(round_event.get("action_kind") or "").strip().upper()
    if action_kind == "HANG_DRYING_CLOTH":
        item_id = _v04_ref_id(round_event, "v04_courtyard_hang")
        target = str(round_event.get("target_support") or "yard_line").strip()
        return "hang_or_collect_cloth", _v04_clean_payload({
            "action": "hang_or_collect_cloth",
            "operation": str(round_event.get("operation") or "hang"),
            "item_id": item_id,
            "target_support": target,
        })
    if action_kind == "RESCUE_DRYING_CLOTH":
        item_id = _v04_ref_id(round_event, "v04_courtyard_cloth")
        return "hang_or_collect_cloth", _v04_clean_payload({
            "action": "hang_or_collect_cloth",
            "operation": "move",
            "item_id": item_id,
            "target_support": str(round_event.get("target_support") or "porch_hook"),
        })
    if action_kind == "COLLECT_DRIED_CLOTH":
        item_id = _v04_ref_id(round_event, "v04_courtyard_collect")
        return "hang_or_collect_cloth", _v04_clean_payload({
            "action": "hang_or_collect_cloth",
            "operation": "collect",
            "item_id": item_id,
            "target_support": str(round_event.get("target_support") or "home_rack"),
        })
    if action_kind == "CARE_PORCH_PLANT":
        item_id = _v04_ref_id(round_event, "v04_porch_plant")
        op = str(round_event.get("operation") or "water")
        payload = {"action": "water_or_move_plant", "operation": op, "plant_id": item_id}
        if op == "move":
            payload["target_place"] = str(round_event.get("target_place") or "porch")
        else:
            payload["water_amount"] = float(round_event.get("water_amount") or 45.0)
        return "water_or_move_plant", _v04_clean_payload(payload)
    if action_kind == "ADVANCE_HOME_CRAFT":
        payload = dict(round_event.get("v04_payload") or {})
        if not payload:
            state = _load_v04_neighborhood_state_zero_write(inst_root)
            payload = (_v04_next_craft_payload(state) if state is not None else None) or {}
        payload["action"] = "craft_paper_or_box"
        return "craft_paper_or_box", _v04_clean_payload(payload)
    if action_kind == "RETURN_NEIGHBORHOOD_LOAN":
        payload = _v04_return_payload(round_event, inst_root, now_dt)
        if payload is None:
            return None
        if "service" in payload:
            payload["action"] = "bookstore_service"
            return "bookstore_service", _v04_clean_payload(payload)
        payload["action"] = "shared_shelf_loan"
        return "shared_shelf_loan", _v04_clean_payload(payload)
    raw_src_ref = str(round_event.get("source_ref") or "").strip()
    if action_kind in ("VISIT_CAFE_RESIDENT", "INTERACT_CAFE_RESIDENT") or raw_src_ref.startswith("v04_cafe_resident:"):
        payload = dict(round_event.get("v04_payload") or {})
        for k in ("operation", "service", "coaster_note", "note", "errand_note", "route_id", "claim_cn"):
            if k not in payload and round_event.get(k) is not None:
                payload[k] = round_event[k]
        payload.setdefault("operation", "check_neighbor_schedule")
        payload["action"] = "interact_cafe_resident"
        return "interact_cafe_resident", _v04_clean_payload(payload)
    return None


def _advance_v04_neighborhood_processes_zero_write(
    inst_root: Path,
    target_iso: str,
) -> dict[str, Any]:
    """Advance the real v0.4 drying/plant/journey segments; zero-write when uninitialized."""

    path = _v04_state_file(inst_root)
    if path.is_symlink() or not path.is_file():
        return {"present": False, "advanced": False}
    try:
        import v04_neighborhood_living as _v04_engine

        result = _v04_engine.advance_v04_neighborhood_processes(inst_root, now_iso=target_iso)
    except Exception as exc:  # reported, never fatal to the clock tick
        return {
            "present": True,
            "advanced": False,
            "error": f"{type(exc).__name__}: {exc}"[:160],
        }
    return {
        "present": True,
        "advanced": bool(result.get("advanced")),
        "changed": bool(result.get("changed")),
        "settled_count": int(result.get("settled_count") or 0),
        "revision": result.get("revision"),
        "observed_at": result.get("observed_at"),
    }


def _v04_mapped_extra_ref(raw_ref: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_ref).strip("-")[:72] or "cue"
    return f"fitem:v02v03-{token}"


def v02_v03_mapped_ref(raw_ref: str) -> str:
    """The ``fitem:v02v03-...`` spelling a bridge clue is offered under (public helper)."""
    return _v04_mapped_extra_ref(str(raw_ref or ""))


def _v02v03_candidate_spellings(raw_ref: Any) -> set[str]:
    """Every spelling a selected candidate ref can arrive under.

    The cycle offers bridge clues as ``fitem:v02v03-<token>``; the raw structured ref
    (``v03_care:bed_rest_block``), its colon-as-dash form and the mapped ``fitem`` form
    all name the same descriptor.  The reverse mapping is included so a caller passing
    the mapped spelling still reaches the raw descriptor.
    """

    text = str(raw_ref or "").strip()
    if not text:
        return set()
    spellings = {text, text.replace(":", "-"), _v04_mapped_extra_ref(text)}
    if text.startswith("fitem:"):
        body = text[len("fitem:"):]
        spellings.add(body)
        if body.startswith("v02v03-"):
            rest = body[len("v02v03-"):]
            spellings.add(rest)
            if "-" in rest:
                spellings.add(rest.replace("-", ":", 1))
    return spellings


def _v02v03_descriptor_match(
    collected: Mapping[str, Any],
    selected_ref: Any,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Resolve one selected ref to ``(candidate_id, raw descriptor)`` across every spelling."""

    wanted = str(selected_ref or "").strip()
    if not wanted:
        return None
    descriptors = (
        collected.get("descriptors")
        if isinstance(collected.get("descriptors"), Mapping)
        else {}
    )
    spellings = _v02v03_candidate_spellings(wanted)
    for candidate_id, desc in descriptors.items():
        if not isinstance(desc, Mapping):
            continue
        raw_ref = str(desc.get("source_ref") or "").strip()
        if not raw_ref:
            continue
        if (
            raw_ref == wanted
            or raw_ref in spellings
            or raw_ref.replace(":", "-") == wanted
            or _v04_mapped_extra_ref(raw_ref) == wanted
        ):
            return str(desc.get("candidate_id") or candidate_id), dict(desc)
    return None


def v02_v03_descriptor_for_selected_ref(
    collected: Mapping[str, Any],
    selected_ref: Any,
) -> Optional[dict[str, Any]]:
    """Resolve a selected candidate / source ref back to its raw bridge descriptor.

    The cycle renames every bridge clue into ``fitem:v02v03-...``; the raw descriptor is
    still the authority for the action it names.  Every spelling
    ``v02v03_round_event_for_selected_candidate`` accepts is honoured here so both
    callers resolve a selection identically.
    """

    match = _v02v03_descriptor_match(collected, selected_ref)
    return match[1] if match is not None else None


def _v02v03_operation_key(raw_ref: Any, decision_id: Any = None) -> str:
    """Deterministic bridge op key for one v0.3 daily action.

    Identical to the cycle's own ``_v02v03_operation_key``: the decision id is part of
    the key, so a replayed Decision reuses its own settlement while two different
    Decisions naming the same repeatable action are two logical actions.
    """

    seed = "%s|%s" % (str(raw_ref or ""), str(decision_id or ""))
    return "v02v03_daily_%s" % hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _v02_decision_marker(decision_id: Any) -> str:
    """A short deterministic marker that names one Decision inside a bounded note field.

    It only exists so two different Decisions that advance the same 0.2 item write two
    genuinely different notes (a replay of one Decision is caught by the consequence
    ledger *before* any owner write, so the marker never needs a wall clock).
    """

    text = re.sub(r"[^A-Za-z0-9]+", "", str(decision_id or ""))
    return text[-8:]


def _v02_ref_suffix(raw_ref: str) -> str:
    text = str(raw_ref or "").strip()
    return text.split(":", 1)[1].strip() if ":" in text else text


def _evaluate_inquiry_auto_conclusion(inquiry: Mapping[str, Any]) -> tuple[bool, str]:
    """Determine if an active inquiry should auto-conclude with a strictly grounded summary.

    Triggers ONLY when:
    - evidence count is still below ``MAX_EVIDENCE_NOTES_PER_INQUIRY`` (so full-capacity
      refusal semantics remain intact), AND
    - EITHER advancing would hit the final step of ``step_budget`` (``steps_used + 1 >= step_budget``)
      OR the inquiry already holds ``>= 2`` recorded evidence notes alongside a ``supported``
      or ``plausible`` explanation.
    Returns ``(should_conclude, grounded_conclusion_cn)`` derived strictly from recorded fields.
    """
    evidence_notes = [n for n in (inquiry.get("evidence_notes") or []) if isinstance(n, Mapping)]
    if len(evidence_notes) >= 8:
        return False, ""

    raw_spent = inquiry.get("steps_spent")
    if isinstance(raw_spent, int) and not isinstance(raw_spent, bool) and raw_spent >= 0:
        steps_used = raw_spent
    else:
        steps_used = len(evidence_notes)

    raw_budget = inquiry.get("step_budget")
    step_budget = (
        int(raw_budget)
        if isinstance(raw_budget, int) and not isinstance(raw_budget, bool) and raw_budget > 0
        else 6
    )
    step_budget_explicit = bool(inquiry.get("step_budget_explicit", False))

    best_supported = ""
    best_plausible = ""
    for exp in inquiry.get("candidate_explanations") or []:
        if not isinstance(exp, Mapping):
            continue
        stance = str(exp.get("stance") or "").strip().lower()
        summary_cn = str(exp.get("summary_cn") or "").strip()
        if not summary_cn:
            continue
        if stance == "supported" and not best_supported:
            best_supported = summary_cn
        elif stance == "plausible" and not best_plausible:
            best_plausible = summary_cn
    best_exp = best_supported or best_plausible

    is_final_budget_step = (
        step_budget > 0
        and (steps_used + 1 >= step_budget)
        and (step_budget_explicit or (steps_used + 1 == step_budget))
    )
    has_enough_evidence_and_explanation = (len(evidence_notes) >= 2) and bool(best_exp)
    if not (is_final_budget_step or has_enough_evidence_and_explanation):
        return False, ""

    latest_note = ""
    for n_row in reversed(evidence_notes):
        txt = str(n_row.get("note") or "").strip()
        if txt:
            latest_note = txt
            break

    question = str(inquiry.get("question") or inquiry.get("inquiry_id") or "有界探究").strip()
    if best_exp and latest_note:
        conclusion = f"{best_exp[:160]}（依据：{latest_note[:100]}）"
    elif best_exp:
        conclusion = f"基于已有记录收敛：{best_exp[:200]}"
    elif latest_note:
        conclusion = f"基于已有观察记录收敛：{latest_note[:200]}"
    else:
        conclusion = f"基于日常观察收敛：{question[:180]}"
    return True, conclusion[:320]


def _v02_daily_action_event(
    candidate_id: str,
    desc: Mapping[str, Any],
    family: str,
    decision_id: str,
) -> dict[str, Any]:
    """Build the executable event for one of the seven 0.2 daily-loop families.

    The event carries only descriptor facts (never the wall clock), so the Cycle and the
    Resident build byte-identical events -- and therefore the same consequence-ledger
    operation key -- for the same Decision.
    """

    raw_ref = str(desc.get("source_ref") or "").strip()
    kind = V02_DAILY_FAMILY_TO_KIND[family]
    suffix = _v02_ref_suffix(raw_ref)
    marker = _v02_decision_marker(decision_id)
    event: dict[str, Any] = {
        "actor_side": "life_resident",
        "effect_state": "APPLIED",
        "action_kind": kind,
        "action_family": family,
        "source_ref": raw_ref,
        "receipt_ref": f"v02v03:{candidate_id}",
        "decision_id": decision_id,
    }
    title = str(desc.get("title_hint") or "").strip()
    if title:
        event["title"] = title[:120]

    if family == "follow_tomorrow_note":
        if raw_ref.startswith("tomorrow_note:"):
            event["note_id"] = suffix
            event["close_desk_note"] = True
        else:
            event["future_item_ref"] = raw_ref
    elif family == "adopt_tentative_plan":
        event["plan_id"] = suffix
        event["strenuous_outing"] = bool(desc.get("strenuous_outing"))
        place_id = str(desc.get("place_id") or "").strip()
        if place_id:
            event["planned_place_id"] = place_id
    elif family == "advance_workbench_draft":
        artifact_ref = str(desc.get("artifact_ref") or "").strip()
        if artifact_ref:
            event["artifact_id"] = artifact_ref
        baseline = str(desc.get("baseline_version_id") or "").strip()
        if baseline:
            event["version_id"] = baseline
        event["workbench_ref"] = raw_ref
        note_head = title[:48] if title else "工作台草稿"
        event["tray_note"] = (f"推进《{note_head}》" + (f"〔{marker}〕" if marker else ""))[:120]
    elif family == "follow_bookstore_bookmark":
        event["book_code"] = str(desc.get("book_code") or suffix)
    elif family == "strenuous_outing_walk":
        event["route_id"] = suffix
        event["strenuous_outing"] = True
        stops = [str(stop) for stop in (desc.get("stops") or []) if str(stop or "").strip()]
        if stops:
            event["route_stops"] = stops
    elif family == "observe_plant_journal":
        event["plant_id"] = str(desc.get("plant_object_id") or suffix)
        artifact_ref = str(desc.get("artifact_ref") or "").strip()
        if artifact_ref:
            event["artifact_id"] = artifact_ref
    elif family == "clarify_common_ground":
        event["cg_id"] = str(desc.get("ground_item_id") or suffix)
    elif family == "advance_bounded_inquiry":
        event["inquiry_id"] = str(desc.get("inquiry_id") or suffix)
        if desc.get("auto_conclude_ready") is True:
            event["conclude"] = True
            hint_conc = str(desc.get("grounded_conclusion_hint") or "").strip()
            if hint_conc:
                event["conclusion"] = hint_conc[:320]
    elif family == "follow_placed_memory_cue":
        event["cue_id"] = str(desc.get("cue_id") or suffix)
    elif family == "act_optional_next_gesture":
        event["gesture_id"] = str(desc.get("gesture_id") or suffix)
        event["decision"] = str(desc.get("decision") or "adopt_step")
    elif family == "open_future_capsule":
        event["capsule_id"] = str(desc.get("capsule_id") or suffix)
        event["decision"] = str(desc.get("decision") or "open")
    elif family == "consider_rescaled_copractice":
        event["practice_id"] = str(desc.get("practice_id") or suffix)
        event["scale_mode"] = str(desc.get("scale_mode") or "light_relay")
        event["confirmed"] = True

    event["operation_key"] = _v02v03_operation_key(raw_ref, decision_id)
    return event


def _v02v03_event_from_descriptor(
    candidate_id: str,
    desc: Mapping[str, Any],
    *,
    decision_id: Any = None,
) -> Optional[dict[str, Any]]:
    """Build the executable round event for one non-v0.4 (0.2/0.3) bridge descriptor.

    Returns ``None`` when the descriptor names no executable 0.3 action, so callers can
    treat "not this wave's clue" and "no command" identically.  The event carries only
    stable descriptor facts (never a wall clock), so the Cycle and the Resident build the
    exact same event -- and therefore the exact same consequence-ledger operation key --
    for the same Decision.
    """

    raw_ref = str(desc.get("source_ref") or "").strip()
    if not raw_ref:
        return None
    did = str(decision_id or "").strip()
    family = str(desc.get("action_family") or "").strip()
    if family in V02_DAILY_FAMILY_TO_KIND:
        # v0.8 Wave 6: the seven 0.2 (v2.0) daily-loop clues are executable too.  The
        # producer's own ``action_family`` maps them, instead of a fragile prefix pile.
        return _v02_daily_action_event(candidate_id, desc, family, did)
    event: dict[str, Any] = {
        "actor_side": "life_resident",
        "effect_state": "APPLIED",
        "source_ref": raw_ref,
        "receipt_ref": f"v02v03:{candidate_id}",
        "decision_id": did,
    }

    if raw_ref.startswith("v03_care:"):
        care_kind = raw_ref.split(":", 1)[1].strip() or str(desc.get("care_event_kind") or "")
        if care_kind == "bed_rest_block":
            event.update({"action_kind": "REST", "care_event_kind": "bed_rest_block",
                          "rest_mode": "rest"})
        elif care_kind == "warm_hydration":
            event.update({"action_kind": "WARM_HYDRATION",
                          "care_event_kind": "warm_hydration", "rest_mode": "rest"})
        else:
            return None
        if isinstance(desc.get("duration_hours"), (int, float)):
            event["duration_hours"] = float(desc["duration_hours"])
    elif raw_ref.startswith("v03_idle:"):
        idle_kind = raw_ref.split(":", 1)[1].strip() or str(desc.get("idle_kind") or "daydream_window")
        event.update({"action_kind": "MEANINGLESS_IDLE", "idle_kind": idle_kind})
    elif raw_ref.startswith("v03_job:"):
        job_kind = str(desc.get("job_kind") or raw_ref.split(":", 1)[1] or "").strip()
        if not job_kind:
            return None
        event.update({
            "action_kind": "COMPLETE_INDEPENDENT_WORK",
            "job_kind": job_kind,
            "job_id": str(desc.get("job_id") or job_kind),
            "work_ref": raw_ref,
        })
        payout = desc.get("payout_cents")
        if isinstance(payout, int) and not isinstance(payout, bool) and payout > 0:
            event["amount_cents"] = payout
        effort = desc.get("effort")
        if isinstance(effort, str) and effort.strip():
            event["effort"] = effort.strip()
        title_hint = desc.get("title_hint")
        if isinstance(title_hint, str) and title_hint.strip():
            event["title"] = title_hint.strip()[:120]
    elif raw_ref.startswith("purchase_cue:") or raw_ref.startswith("market_wish:"):
        suffix = raw_ref.split(":", 1)[1].strip()
        price = desc.get("price_cents")
        if not isinstance(price, int) or isinstance(price, bool):
            price = desc.get("unit_price")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            return None
        item_title = str(desc.get("item_title") or suffix or "日常小物")[:120]
        event.update({
            "action_kind": "COMMIT_AFFORDABLE_PURCHASE",
            "item_title": item_title,
            "price_cents": int(price),
            "verdict": str(desc.get("purchase_verdict") or "afford_independently"),
            "commit_spend": True,
        })
        if raw_ref.startswith("market_wish:"):
            event["wish_id"] = str(desc.get("wish_id") or suffix)
        else:
            event["item_id"] = str(desc.get("item_id") or suffix)
        quantity = desc.get("quantity")
        if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
            event["quantity"] = quantity
        goal_id = desc.get("goal_id")
        if isinstance(goal_id, str) and goal_id.strip():
            event["goal_id"] = goal_id.strip()
    elif raw_ref.startswith("v03_intimacy_resume:"):
        permit_id = str(desc.get("permit_id") or raw_ref.split(":", 1)[1] or "").strip()
        activity_ref = str(desc.get("activity_ref") or "").strip()
        if not permit_id or not activity_ref:
            return None
        event.update({
            "action_kind": "RESUME_INTIMACY_CHECKPOINT",
            "permit_id": permit_id,
            "activity_ref": activity_ref,
        })
        # 同一 permit + activity 断点只恢复消费一次：重放（哪怕换了 decision）幂等。
        event["operation_key"] = _v04_operation_key(
            f"v03_intimacy_resume_{permit_id}_{activity_ref}"
        )
        return event
    elif raw_ref.startswith("v04_elastic_shared_notebook:"):
        # Ch.50：回接共享小本子。回复文字由轮次事件带入；没有文字时结算会温和返回，
        # 绝不替主体编一句假回复。
        event.update({
            "action_kind": "ELASTIC_SHARED_NOTEBOOK",
            "action_family": family or "elastic_shared_notebook",
        })
        reply_text = str(
            desc.get("notebook_text") or desc.get("reply_text") or ""
        ).strip()
        if reply_text:
            event["notebook_text"] = reply_text[:400]
    elif raw_ref.startswith("v04_elastic_prepared_surprise:"):
        # Ch.53：递出已备好的小惊喜，只带阶段 ID，不带 title / content。
        event.update({
            "action_kind": "ELASTIC_OFFER_SURPRISE",
            "action_family": family or "elastic_offer_surprise",
        })
        surprise_id = str(desc.get("surprise_id") or "").strip()
        if surprise_id:
            event["surprise_id"] = surprise_id
    elif raw_ref.startswith("v08_routine:"):
        routine_id = str(desc.get("routine_id") or raw_ref.split(":", 1)[1] or "").strip()
        if not routine_id:
            return None
        event.update({
            "action_kind": "RUN_PERSONAL_ROUTINE",
            "action_family": family or "run_personal_routine",
            "routine_id": routine_id,
        })
    elif raw_ref.startswith("v08_open_act:"):
        parts = raw_ref.split(":", 2)
        object_id = str(desc.get("object_id") or (parts[1] if len(parts) > 1 else "")).strip()
        mode = str(desc.get("open_mode") or (parts[2] if len(parts) > 2 else "touch_object")).strip()
        if not object_id:
            return None
        event.update({
            "action_kind": "OPEN_LIVING_TRACE",
            "action_family": family or "open_living_trace",
            "object_id": object_id,
            "open_mode": mode or "touch_object",
        })
    elif raw_ref.startswith("v03_item_gift:"):
        gift_id = str(desc.get("gift_id") or raw_ref.split(":", 1)[1] or "").strip()
        if not gift_id:
            return None
        event.update({
            "action_kind": "DECIDE_ITEM_GIFT",
            "action_family": family or "decide_partner_item_gift",
            "gift_id": gift_id,
            "gift_decision": str(desc.get("gift_decision") or "accept"),
        })
    elif raw_ref.startswith("v03_slow_letter_draft:"):
        draft_id = str(desc.get("draft_id") or raw_ref.split(":", 1)[1] or "").strip()
        if not draft_id:
            return None
        event.update({
            "action_kind": "POST_SLOW_LETTER_DRAFT",
            "action_family": family or "post_matured_slow_letter_draft",
            "draft_id": draft_id,
        })
    elif raw_ref.startswith("v03_material:"):
        material_id = str(
            desc.get("material_id") or raw_ref.split(":", 1)[1] or ""
        ).strip()
        if not material_id:
            return None
        event.update({
            "action_kind": "OPEN_SHARED_MATERIAL",
            "action_family": family or "open_shared_material",
            "material_id": material_id,
            "open_mode": str(desc.get("open_mode") or "open_partial"),
        })
    elif raw_ref.startswith("v08_matter:"):
        # Includes ``v08_matter:return_loan:<oid>``: returning a borrowed thing is an
        # ordinary physical act, so it rides the same generic path with no debt,
        # guilt or relationship penalty attached.
        payload = desc.get("open_act_payload")
        if not isinstance(payload, Mapping):
            return None
        event.update({
            "action_kind": "OPEN_MATTER_ACT",
            "action_family": family or "compose_open_act",
            "matter_target_ref": raw_ref,
            "open_act_payload": copy.deepcopy(dict(payload)),
        })
    elif raw_ref.startswith("v08_microzone:"):
        # A tiny optional microzone-comfort shift; the work ref and breakpoint ride
        # along untouched so the half-finished task is never lost by moving.
        event.update({
            "action_kind": "ADJUST_MICROZONE",
            "action_family": family or "adjust_microzone_comfort",
            "microzone_target_ref": raw_ref,
            "target_spot_id": str(desc.get("target_spot_id") or "desk_inner"),
            "shift_reason": str(desc.get("shift_reason") or ""),
            "preserve_work_ref": str(desc.get("preserve_work_ref") or ""),
            "preserve_breakpoint_cn": str(desc.get("preserve_breakpoint_cn") or ""),
        })
    elif raw_ref.startswith("v08_echo:"):
        parts = raw_ref.split(":", 2)
        echo_kind = parts[1].strip() if len(parts) > 1 else ""
        echo_id = str(
            desc.get("question_id")
            or desc.get("spark_id")
            or (parts[2].strip() if len(parts) > 2 else "")
        ).strip()
        if not echo_id:
            return None
        if echo_kind == "revisit_question":
            event.update({
                "action_kind": "REVISIT_FUTURE_QUESTION",
                "action_family": family or "revisit_future_question",
                "question_id": echo_id,
                "resolution": str(desc.get("resolution") or "still_curious"),
                "note_cn": str(desc.get("note_cn") or "")[:200],
            })
        elif echo_kind == "try_spark":
            event.update({
                "action_kind": "SETTLE_SPARK_ATTEMPT",
                "action_family": family or "settle_spark_attempt",
                "spark_id": echo_id,
                "outcome": str(desc.get("outcome") or "helped"),
                "choice_trace_cn": str(desc.get("choice_trace_cn") or "")[:140],
            })
        else:
            return None
    elif raw_ref.startswith("v08_body:"):
        body_kind = str(desc.get("body_action_kind") or "").strip().upper()
        if body_kind not in ("RESOLVE_GARMENT_CARE", "USE_CYCLE_SUPPLY"):
            return None
        event.update({
            "action_kind": body_kind,
            "action_family": family or (
                "resolve_garment_care"
                if body_kind == "RESOLVE_GARMENT_CARE"
                else "use_cycle_supply"
            ),
        })
        if body_kind == "RESOLVE_GARMENT_CARE":
            mode = str(desc.get("garment_care_mode") or "wash_garment").strip().lower()
            if mode not in ("wash_garment", "change_garment"):
                mode = "wash_garment"
            event["mode"] = mode
    elif raw_ref.startswith("v02_rescaled_copractice:"):
        practice_id = str(
            desc.get("practice_id") or raw_ref.split(":", 1)[1] or ""
        ).strip()
        if not practice_id:
            return None
        event.update({
            "action_kind": "CONFIRM_RESCALED_COPRACTICE",
            "action_family": family or "consider_rescaled_copractice",
            "practice_id": practice_id,
            "scale_mode": str(desc.get("scale_mode") or "light_relay"),
            "confirmed": True,
        })
    elif raw_ref.startswith("v03_body_care:"):
        care_sub = raw_ref.split(":", 1)[1].strip()
        if care_sub == "hang_outerwear":
            event.update({
                "action_kind": "HANG_OUTERWEAR",
                "action_family": family or "hang_outerwear",
                "garment_id": str(desc.get("garment_id") or "daily_coat"),
                "hang_position": str(desc.get("hang_position") or "entryway_rack"),
            })
        elif care_sub == "drink_warm_water":
            event.update({
                "action_kind": "WARM_HYDRATION",
                "action_family": family or "drink_warm_water",
                "care_event_kind": "warm_hydration",
                "rest_mode": "rest",
                "thirst_relief": 0.35,
            })
        elif care_sub == "rest_sleep_like":
            event.update({
                "action_kind": "REST",
                "action_family": family or "acute_body_rest_sleep_like",
                "care_event_kind": "bed_rest_block",
                "rest_mode": "sleep_like",
            })
        elif care_sub == "eat_warm_meal":
            event.update({
                "action_kind": "ACUTE_BODY_CARE_EAT",
                "action_family": family or "eat_warm_meal",
                "hunger_relief": 0.45,
            })
        elif care_sub == "cool_down_air":
            event.update({
                "action_kind": "ACUTE_BODY_CARE_COOL",
                "action_family": family or "cool_down_air",
                "window_state": "open",
            })
        else:
            return None
    elif raw_ref.startswith(("v05_desire:", "v05_desire_alt:")):
        desire_id = str(
            desc.get("desire_id") or desc.get("substituted_from_desire_id") or raw_ref.split(":", 1)[1] or ""
        ).strip()
        if not desire_id:
            return None
        event.update({
            "action_kind": "PURSUE_AUTONOMOUS_DESIRE",
            "action_family": family or (
                "pursue_desire_alternative"
                if raw_ref.startswith("v05_desire_alt:")
                else "pursue_autonomous_desire"
            ),
            "desire_id": desire_id,
            "is_alternative": bool(desc.get("is_alternative") or raw_ref.startswith("v05_desire_alt:")),
            "friction_reason": desc.get("friction_reason"),
            "alternative_kind": desc.get("alternative_kind"),
            "title": str(desc.get("title_hint") or "")[:120],
        })
    elif raw_ref.startswith("v09_commitment:pickup:"):
        cid = str(desc.get("commitment_id") or raw_ref.split(":", 2)[-1] or "").strip()
        if not cid:
            return None
        event.update({
            "action_kind": "PICKUP_ARRIVED_COMMITMENT",
            "action_family": family or "pickup_arrived_commitment",
            "commitment_id": cid,
            "venue_id": str(desc.get("venue_id") or "bookstore"),
            "npc_id": str(desc.get("npc_id") or ""),
            "contact_action": "pickup_reservation",
            "title_cn": str(desc.get("topic_key") or desc.get("title_hint") or "")[:120],
        })
    elif raw_ref.startswith("v09_blind_choice:commit:"):
        choice_id = str(desc.get("choice_id") or raw_ref.split(":", 2)[-1] or "").strip()
        if not choice_id:
            return None
        event.update({
            "action_kind": "COMMIT_BLIND_CHOICE",
            "action_family": family or "commit_blind_choice",
            "choice_id": choice_id,
            "selected_choice_id": "UNDECIDED",
            "title_cn": str(desc.get("topic_cn") or desc.get("title_hint") or "")[:120],
        })
    elif raw_ref.startswith("v09_stance:reflect:"):
        stance_id = str(desc.get("stance_id") or raw_ref.split(":", 2)[-1] or "").strip()
        if not stance_id:
            return None
        event.update({
            "action_kind": "REFLECT_UNFORMED_STANCE",
            "action_family": family or "reflect_unformed_stance",
            "stance_id": stance_id,
            "provenance_tier": str(desc.get("matched_provenance_tier") or "tentative_guess"),
            "matched_evidence_ref": str(desc.get("matched_evidence_ref") or ""),
            "title_cn": str(desc.get("topic_cn") or desc.get("title_hint") or "")[:120],
        })
    elif raw_ref.startswith("v09_taxonomy:tidy:"):
        target_id = str(desc.get("target_id") or raw_ref.split(":", 2)[-1] or "").strip()
        if not target_id:
            return None
        event.update({
            "action_kind": "TIDY_CUSTOM_SHELF_OR_TIN",
            "action_family": family or "tidy_custom_shelf_or_tin",
            "target_kind": str(desc.get("target_kind") or "repurposed_object"),
            "target_id": target_id,
            "title_cn": str(desc.get("target_title_cn") or desc.get("title_hint") or "")[:120],
        })
    else:
        return None

    event["operation_key"] = _v02v03_operation_key(raw_ref, did)
    return event


def v02_v03_round_event_for_selected_candidate(
    collected: Mapping[str, Any],
    selected_source_ref: Any,
    *,
    now_iso: Optional[str] = None,
    decision_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build the settlement round event for any selected 0.2/0.3 daily opportunity.

    This is the superset of ``v04_round_event_for_selected_candidate``: the six real
    v0.4 neighborhood commands keep their exact previous event (and operation key), and
    the 0.3 care / idle / independent-work / purchase / intimacy-resume clues get their
    own executable event.  Returns ``None`` when the selected ref names no executable
    command.
    """

    match = _v02v03_descriptor_match(collected, selected_source_ref)
    if match is None:
        return None
    candidate_id, desc = match
    raw_ref = str(desc.get("source_ref") or "").strip()
    if raw_ref.startswith("v04_") and str(desc.get("v04_action") or "").strip():
        event = v04_round_event_for_selected_candidate(collected, raw_ref, now_iso=now_iso)
    else:
        event = _v02v03_event_from_descriptor(candidate_id, desc, decision_id=decision_id)
    if isinstance(event, dict):
        raw_constrained = collected.get("second_order_constrained_candidates")
        if isinstance(raw_constrained, list) and raw_constrained:
            compact_constrained: list[dict[str, str]] = []
            for item in raw_constrained[:6]:
                if not isinstance(item, Mapping):
                    continue
                r_id = str(item.get("rule_id") or "").strip()
                if not r_id:
                    continue
                compact_constrained.append({
                    "rule_id": r_id[:96],
                    "rule_title": str(item.get("rule_title") or r_id).strip()[:160],
                    "candidate_ref": str(
                        item.get("candidate_ref")
                        or item.get("source_ref")
                        or item.get("candidate_id")
                        or "candidate"
                    ).strip()[:200],
                    "candidate_title": str(
                        item.get("candidate_title")
                        or item.get("candidate_ref")
                        or item.get("source_ref")
                        or "candidate"
                    ).strip()[:200],
                    "reason_cn": str(item.get("reason_cn") or "受二阶小规矩约束延后").strip()[:300],
                })
            if compact_constrained:
                event["second_order_constrained_candidates"] = compact_constrained
    return event


def v04_round_event_for_selected_candidate(
    collected: Mapping[str, Any],
    selected_source_ref: Any,
    *,
    now_iso: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build the settlement round event for a selected ``v04_*`` opportunity.

    The cycle renames a bridge clue's ``source_ref`` into ``fitem:v02v03-...``;
    the raw descriptor is still the authority for the action and its payload.
    Returns ``None`` when the selected candidate is not a v0.4 neighborhood one.
    """

    wanted = str(selected_source_ref or "").strip()
    if not wanted:
        return None
    descriptors = (
        collected.get("descriptors")
        if isinstance(collected.get("descriptors"), Mapping)
        else {}
    )
    for candidate_id, desc in descriptors.items():
        if not isinstance(desc, Mapping):
            continue
        raw_ref = str(desc.get("source_ref") or "").strip()
        if not raw_ref.startswith("v04_"):
            continue
        if wanted not in (raw_ref, raw_ref.replace(":", "-"), _v04_mapped_extra_ref(raw_ref)):
            continue
        action = desc.get("v04_action")
        if not isinstance(action, str) or not action.strip():
            continue
        return {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": str(desc.get("v04_action_kind") or "V04_NEIGHBORHOOD_ACTION"),
            "v04_action": action.strip(),
            "v04_payload": dict(desc.get("v04_payload") or {}),
            "source_ref": raw_ref,
            "receipt_ref": f"v04:{candidate_id}",
            "operation_key": _v04_operation_key(f"resident_v04_{raw_ref}"),
        }
    return None


def _v02_refuse(reason_code: str, *, detail: Any = None, **extra: Any) -> dict[str, Any]:
    """A named 0.2 settlement refusal: zero optimistic writes, zero LLM calls."""

    out: dict[str, Any] = {
        "ok": False,
        "settled": False,
        "status": "REFUSED",
        "reason_code": str(reason_code),
        "optimistic_writes": 0,
        "slices_updated": [],
        "llm_calls": 0,
    }
    if detail is not None:
        out["detail"] = str(detail)[:200]
    out.update({key: value for key, value in extra.items() if value is not None})
    return out


def _v02_match_stored_id(wanted: Any, candidates: Iterable[Any]) -> Optional[str]:
    """Find the stored id a collected ref names, tolerating ``_safe_token`` mangling.

    The collector runs every id through ``_safe_token`` before putting it in a ref, which
    can rewrite characters an owner allows.  Exact matches always win; a mangled match is
    accepted only when it names exactly one stored id, so two different ids can never be
    confused for one another.
    """

    text = str(wanted or "").strip()
    if not text:
        return None
    clean = [str(item or "").strip() for item in candidates]
    clean = [item for item in clean if item]
    if text in clean:
        return text
    token = _safe_token(text, "")
    mangled = [item for item in clean if token and _safe_token(item, "") == token]
    return mangled[0] if len(mangled) == 1 else None


def _settle_v02_follow_tomorrow_note(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Close the real tomorrow note / desk note this clue named (one-shot, replay-safe)."""

    fitem_ref = str(event.get("future_item_ref") or "").strip()
    note_id = str(event.get("note_id") or "").strip()
    touched = False
    already_closed = False
    closed_item: Any = None
    for f_root in (life_root, inst_root):
        path = f_root / FUTURE_ITEMS_FILENAME
        if path.is_symlink() or not path.is_file():
            continue
        try:
            store = FutureItems(f_root)
            current = store.get(fitem_ref) if fitem_ref else None
        except FutureItemError:
            continue
        if current is None:
            continue
        touched = True
        if current.status == "OPEN":
            closed_item = store.close(
                fitem_ref, reason="FOLLOWED_UP_APPLIED", now=now_iso
            )
            slices_updated.append("life_future_items")
        else:
            already_closed = True
        break

    desk_done = False
    if event.get("close_desk_note"):
        desk_path = inst_root / "phone" / DESK_TOMORROW_NOTE_FILENAME
        desk_doc = _read_optional_json_zero_write(desk_path)
        if isinstance(desk_doc, Mapping):
            touched = True
            if str(desk_doc.get("status") or "OPEN").upper() == "DONE":
                desk_done = True
            else:
                desk_doc = dict(desk_doc)
                desk_doc["status"] = "DONE"
                desk_doc["completed_at"] = now_iso
                _atomic_write_json(desk_path, desk_doc)
                slices_updated.append("desk_tomorrow_note")

    if not touched:
        return _v02_refuse(
            "TOMORROW_NOTE_TARGET_NOT_FOUND",
            fitem_ref=fitem_ref or None,
            note_id=note_id or None,
        )
    details["tomorrow_note"] = {
        "future_item_ref": fitem_ref or None,
        "note_id": note_id or None,
        "closed_future_item": closed_item,
        "already_closed": already_closed,
        "desk_note_closed": bool(event.get("close_desk_note")),
        "desk_note_already_done": desk_done,
    }
    return None


def _settle_v02_adopt_tentative_plan(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Carry out one tentative plan through the real home-hearth owner.

    ``home_hearth_view`` deliberately has no ACCEPTED/EXECUTED plan status ("只能提/
    改期/放下，不造 ACCEPTED"), so adoption is recorded as the owner's own terminal
    "put down" transition plus an explicit ADOPTED resolution in this receipt.  Inventing
    a new plan status would rewrite a load-bearing contract.
    """

    plan_id_raw = str(event.get("plan_id") or "").strip()
    if event.get("strenuous_outing"):
        try:
            ecology = _read_ecology_envelope_zero_write(inst_root, now_iso)
        except Exception as exc:  # noqa: BLE001 - an unreadable body is a named refusal
            return _v02_refuse("ECOLOGY_ENVELOPE_UNREADABLE", detail=f"{type(exc).__name__}: {exc}")
        illness = ecology.get("illness") if isinstance(ecology.get("illness"), Mapping) else {}
        cap = illness.get("capability_envelope") if isinstance(illness.get("capability_envelope"), Mapping) else {}
        if cap.get("blocks_strenuous_outing"):
            return _v02_refuse(
                "ILLNESS_BLOCKS_STRENUOUS_OUTING",
                phase=illness.get("phase"),
                plan_id=plan_id_raw or None,
            )

    state, reason = home_hearth_view.read_plans_state(inst_root)
    if reason is not None:
        return _v02_refuse("TENTATIVE_PLANS_STATE_INVALID", detail=reason)
    plan_ids = [
        str(row.get("plan_id") or "")
        for row in (state.get("plans") or [])
        if isinstance(row, Mapping)
    ]
    plan_id = _v02_match_stored_id(plan_id_raw, plan_ids)
    if plan_id is None:
        return _v02_refuse("TENTATIVE_PLAN_NOT_FOUND", plan_id=plan_id_raw or None)
    plan = home_hearth_view.find_plan(state, plan_id)
    if plan is None:
        return _v02_refuse("TENTATIVE_PLAN_NOT_FOUND", plan_id=plan_id)

    already_resolved = str(plan.get("status") or "") == "DROPPED"
    resolved_row: Any = plan
    if not already_resolved:
        next_state, changed, resolved_row, err = home_hearth_view.drop_plan(
            state, plan_id=plan_id, now_iso=now_iso
        )
        if err:
            return _v02_refuse(str(err), plan_id=plan_id)
        if changed:
            home_hearth_view.write_plans_state(inst_root, next_state)
            slices_updated.append("home_tentative_plans")

    details["tentative_plan"] = {
        "plan_id": plan_id,
        "title": (resolved_row or plan).get("title"),
        "plan_status": (resolved_row or plan).get("status"),
        "resolution": "ADOPTED",
        "adopted_at": now_iso,
        "decision_id": event.get("decision_id") or None,
        "operation_key": event.get("operation_key"),
        "already_resolved": already_resolved,
    }
    return None


def _settle_v02_advance_workbench(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Advance workbench tray, and commit a new canonical version when bound to a live ManagedArtifact."""

    tray_state, reason = workbench_curio_view.read_tray_state(inst_root)
    if reason is not None:
        return _v02_refuse("WORKBENCH_TRAY_STATE_INVALID", detail=reason)
    tray = tray_state.get("tray") if isinstance(tray_state.get("tray"), Mapping) else {}
    work = tray.get("work") if isinstance(tray.get("work"), Mapping) else None
    if not (work or tray.get("images") or tray.get("materials") or tray.get("note")):
        return _v02_refuse("WORKBENCH_TRAY_EMPTY")

    note = str(event.get("tray_note") or "").strip() or "推进草稿"
    next_work = dict(work) if work else None
    committed_ver_id: Optional[str] = None
    previous_head: Optional[str] = None

    art_id = str((next_work or {}).get("artifact_id") or "").strip()
    if art_id:
        try:
            rlf_state, rlf_exists = v02_rlf._load_state_readonly(inst_root)
        except Exception:
            rlf_state, rlf_exists = {}, False
        art_doc = (rlf_state.get("artifacts") or {}).get(art_id) if rlf_exists else None
        if isinstance(art_doc, Mapping) and art_doc.get("lifecycle") == v02_rlf.LIFECYCLE_ACTIVE:
            cur_head = str(art_doc.get("current_head") or "")
            head_ver = (rlf_state.get("versions") or {}).get(cur_head) or {}
            prev_body = str(((head_ver.get("content") or {}).get("body")) or "")
            addition = str(event.get("continuation_text") or "").strip() or f"{note}"
            new_body = (prev_body.rstrip("\n") + "\n" + addition + "\n") if prev_body else (addition + "\n")
            commit_op = str(
                event.get("operation_key")
                or event.get("decision_id")
                or f"wb_adv_{art_id}_{cur_head}"
            ).strip()
            c_st, c_res = v02_rlf.commit_artifact_version(
                inst_root,
                artifact_id=art_id,
                expected_head=str((next_work or {}).get("baseline_version_id") or cur_head),
                body=new_body,
                operation_id=f"wbcommit:{commit_op}",
                action_ref_id=str(event.get("action_id") or event.get("decision_id") or f"wb:{commit_op}"),
                subject_id=str(art_doc.get("subject_id") or "subject"),
                writer_epoch=int((rlf_state.get("control") or {}).get("writer_fence_epoch") or 1),
                now_iso=now_iso,
            )
            if c_st == 200 and c_res.get("ok"):
                previous_head = cur_head
                committed_ver_id = str(c_res.get("current_head") or c_res.get("version_id") or "")
                if next_work is not None and committed_ver_id:
                    next_work["baseline_version_id"] = committed_ver_id
                slices_updated.append("managed_artifact")
                try:
                    existing_rep = (rlf_state.get("representations") or {}).get(art_id)
                    safe_art = re.sub(r"[^A-Za-z0-9._-]+", "_", art_id).strip("_")[:64] or "work"
                    if not isinstance(existing_rep, Mapping):
                        v02_rlf.bind_representation(
                            inst_root,
                            artifact_id=art_id,
                            workspace_root=inst_root / "workspace",
                            workspace_rel_path=f"works/{safe_art}.md",
                            operation_id=f"wb_bind:{art_id}:{committed_ver_id or commit_op}"[:96],
                            write_projection=True,
                            now_iso=now_iso,
                        )
                    else:
                        _irc, rep_info = v02_rlf.inspect_representation(
                            inst_root, artifact_id=art_id, has_subject_capability=True
                        )
                        if not rep_info.get("external_edit_detected"):
                            bind_ws = Path(str(existing_rep.get("workspace_root") or (inst_root / "workspace")))
                            bind_rel = str(existing_rep.get("workspace_rel_path") or f"works/{safe_art}.md")
                            v02_rlf.bind_representation(
                                inst_root,
                                artifact_id=art_id,
                                workspace_root=bind_ws,
                                workspace_rel_path=bind_rel,
                                operation_id=f"wb_sync:{art_id}:{committed_ver_id or commit_op}"[:96],
                                write_projection=True,
                                now_iso=now_iso,
                            )
                except Exception:
                    pass

    candidate_tray = {
        "images": list(tray.get("images") or []),
        "materials": list(tray.get("materials") or []),
        "work": next_work,
        "note": note[:120],
    }
    next_state, changed = workbench_curio_view.set_tray(tray_state, candidate_tray, now_iso)
    if changed:
        workbench_curio_view.write_tray_state(inst_root, next_state)
        slices_updated.append("workbench_tray")
    details["workbench"] = {
        "note": note[:120],
        "already_current": not changed,
        "artifact_id": (next_work or {}).get("artifact_id"),
        "baseline_version_id": (next_work or {}).get("baseline_version_id"),
        "previous_head": previous_head,
        "committed_version_id": committed_ver_id,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_follow_bookstore_bookmark(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Advance the real bookstore window bookmark's follow-up note (no purchase, no debit)."""

    state, reason = outing_places_view.read_bench_bookstore_state(inst_root)
    if reason is not None:
        return _v02_refuse("BENCH_BOOKSTORE_STATE_INVALID", detail=reason)
    marks = [row for row in (state.get("window_bookmarks") or []) if isinstance(row, Mapping)]
    code = _v02_match_stored_id(
        event.get("book_code"), [str(row.get("book_code") or "") for row in marks]
    )
    if code is None:
        return _v02_refuse(
            "BOOKSTORE_BOOKMARK_NOT_FOUND", book_code=str(event.get("book_code") or "") or None
        )
    entry = next(row for row in marks if str(row.get("book_code") or "") == code)
    marker = _v02_decision_marker(event.get("decision_id"))
    base_note = str(entry.get("note") or "").strip()
    follow_note = (
        (f"{base_note} ｜ " if base_note else "")
        + "顺着橱窗又翻了几页"
        + (f"〔{marker}〕" if marker else "")
    )[:120]
    changed = False
    if entry.get("note") != follow_note:
        entry = dict(entry)
        entry["note"] = follow_note
        entry["updated_at"] = now_iso
        state["window_bookmarks"] = [
            entry if str(row.get("book_code") or "") == code else row for row in marks
        ]
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_iso
        outing_places_view.write_bench_bookstore_state(inst_root, state)
        slices_updated.append("outing_bench_bookstore")
        changed = True
    details["bookstore_bookmark"] = {
        "book_code": code,
        "title": entry.get("title"),
        "follow_note": follow_note,
        "already_current": not changed,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_strenuous_outing_walk(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Walk one saved route with a real body gate, route progress and visit record."""

    try:
        ecology = _read_ecology_envelope_zero_write(inst_root, now_iso)
    except Exception as exc:  # noqa: BLE001 - an unreadable body is a named refusal
        return _v02_refuse("ECOLOGY_ENVELOPE_UNREADABLE", detail=f"{type(exc).__name__}: {exc}")
    illness = ecology.get("illness") if isinstance(ecology.get("illness"), Mapping) else {}
    cap = illness.get("capability_envelope") if isinstance(illness.get("capability_envelope"), Mapping) else {}
    if cap.get("blocks_strenuous_outing"):
        return _v02_refuse(
            "ILLNESS_BLOCKS_STRENUOUS_OUTING",
            phase=illness.get("phase"),
            route_id=str(event.get("route_id") or "") or None,
        )

    state, reason = outing_places_view.read_routes_market_state(inst_root)
    if reason is not None:
        return _v02_refuse("ROUTES_MARKET_STATE_INVALID", detail=reason)
    routes = [row for row in (state.get("routes") or []) if isinstance(row, Mapping)]
    route_id = _v02_match_stored_id(
        event.get("route_id"), [str(row.get("route_id") or "") for row in routes]
    )
    if route_id is None:
        return _v02_refuse("WALK_ROUTE_NOT_FOUND", route_id=str(event.get("route_id") or "") or None)
    route = next(row for row in routes if str(row.get("route_id") or "") == route_id)
    stops = [str(place) for place in (route.get("place_ids") or []) if str(place or "").strip()]
    marker = _v02_decision_marker(event.get("decision_id"))
    caption = ("今天沿着这条路走完了一趟" + (f"〔{marker}〕" if marker else ""))[:120]
    next_state, row, changed = outing_places_view.apply_save_walk_route(
        state,
        route_id=route_id,
        title=str(route.get("title") or route_id),
        place_ids=stops,
        valid_place_ids=set(stops),
        caption=caption,
        caption_supplied=True,
        now_iso=now_iso,
    )
    if changed:
        outing_places_view.write_routes_market_state(inst_root, next_state)
        slices_updated.append("outing_routes_market")

    visit_entry: Any = None
    if stops:
        try:
            bench_state, bench_reason = outing_places_view.read_bench_bookstore_state(inst_root)
            if bench_reason is None:
                bench_next, visit_candidate, visit_changed = outing_places_view.apply_log_bench_visit(
                    bench_state,
                    place_id=stops[0],
                    valid_place_ids=set(stops),
                    operation_key=_safe_token(
                        f"{event.get('operation_key') or 'walk'}_visit", "walk_visit"
                    )[:120],
                    note=caption,
                    shared=False,
                    now_iso=now_iso,
                )
                if visit_changed:
                    outing_places_view.write_bench_bookstore_state(inst_root, bench_next)
                    slices_updated.append("outing_bench_bookstore")
                visit_entry = visit_candidate
        except Exception as exc:  # noqa: BLE001 - a full visit log is reported, not fatal
            details["walk_visit_error"] = f"{type(exc).__name__}: {exc}"[:160]

    details["strenuous_outing_walk"] = {
        "route_id": route_id,
        "title": (row or route).get("title"),
        "stops": stops,
        "caption": caption,
        "already_current": not changed,
        "visit": visit_entry,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_observe_plant_journal(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Append a real observation to the potted-plant sketch journal link."""

    if not long_companionship_view.room_habits_state_path(inst_root).is_file():
        return _v02_refuse("PLANT_JOURNAL_STATE_MISSING")
    state, reason = long_companionship_view.read_room_habits_state(inst_root)
    if reason is not None:
        return _v02_refuse("ROOM_HABITS_STATE_INVALID", detail=reason)
    links = [row for row in (state.get("plant_sketch_links") or []) if isinstance(row, Mapping)]
    plant_id = _v02_match_stored_id(
        event.get("plant_id"), [str(row.get("plant_id") or "") for row in links]
    )
    if plant_id is None:
        return _v02_refuse("PLANT_JOURNAL_LINK_NOT_FOUND", plant_id=str(event.get("plant_id") or "") or None)
    artifact_ref = str(event.get("artifact_id") or "").strip()
    named = [
        row for row in links
        if str(row.get("plant_id") or "") == plant_id
        and (not artifact_ref or str(row.get("target_artifact_id") or "") == artifact_ref)
    ]
    link = named[0] if named else next(
        row for row in links if str(row.get("plant_id") or "") == plant_id
    )
    marker = _v02_decision_marker(event.get("decision_id"))
    base_note = str(link.get("observation_note") or "").strip()
    observation = (
        (f"{base_note} ｜ " if base_note else "")
        + "又看了一眼"
        + (f"〔{marker}〕" if marker else "")
    )[:160]
    next_state, entry, changed = long_companionship_view.apply_link_plant_sketch(
        state,
        plant_id=plant_id,
        target_kind=str(link.get("target_kind") or "project"),
        target_artifact_id=str(link.get("target_artifact_id") or ""),
        observation_note=observation,
        shared=bool(link.get("shared")),
        plant_name=link.get("plant_name"),
        now_iso=now_iso,
    )
    if changed:
        long_companionship_view.write_room_habits_state(inst_root, next_state)
        slices_updated.append("longterm_room_habits")
    details["plant_journal"] = {
        "plant_id": plant_id,
        "link_id": entry.get("link_id"),
        "observation_note": observation,
        "already_current": not changed,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_clarify_common_ground(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Resolve one pending common-ground clarification through its real owner."""

    state_path = inst_root / v02_common_ground.COMMON_GROUND_REL_PATH
    if state_path.is_symlink() or not state_path.is_file():
        return _v02_refuse("COMMON_GROUND_STATE_MISSING")
    raw_state = _read_optional_json_zero_write(state_path) or {}
    items = raw_state.get("items") if isinstance(raw_state.get("items"), Mapping) else {}
    cg_id = _v02_match_stored_id(event.get("cg_id"), list(items.keys()))
    if cg_id is None:
        return _v02_refuse("COMMON_GROUND_ITEM_NOT_FOUND", cg_id=str(event.get("cg_id") or "") or None)
    item = items.get(cg_id) if isinstance(items.get(cg_id), Mapping) else {}
    if str(item.get("ground_status") or "") == v02_common_ground.SHARED_WITH_PARTNER:
        details["common_ground"] = {
            "cg_id": cg_id,
            "topic": item.get("topic"),
            "ground_status": item.get("ground_status"),
            "already_clarified": True,
            "decision_id": event.get("decision_id") or None,
        }
        return None

    marker = _v02_decision_marker(event.get("decision_id"))
    shared_note = ("当面问清楚了" + (f"〔{marker}〕" if marker else ""))[:160]
    result = v02_common_ground.mark_shared_with_partner(
        inst_root, item_id=cg_id, shared_note=shared_note, now_iso=now_iso
    )
    if not result.get("ok"):
        return _v02_refuse(str(result.get("error_code") or "COMMON_GROUND_CLARIFY_FAILED"), cg_id=cg_id)
    settled_item = result.get("item") if isinstance(result.get("item"), Mapping) else {}
    slices_updated.append("v02_common_ground")
    details["common_ground"] = {
        "cg_id": cg_id,
        "topic": settled_item.get("topic"),
        "ground_status": settled_item.get("ground_status"),
        "shared_at": settled_item.get("shared_at"),
        "already_clarified": False,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_advance_bounded_inquiry(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Advance one active bounded inquiry with a real, replay-safe observation note.

    The note is derived from the Decision (never the wall clock), so a replay of the same
    Decision is idempotent at the consequence ledger *and* the agency-depth operation
    ledger.  A terminal inquiry accepts no further evidence; a full inquiry refuses with a
    named capacity code instead of silently dropping the note.
    """

    inquiry_id = str(event.get("inquiry_id") or "").strip()
    if not inquiry_id:
        return _v02_refuse("INQUIRY_ID_REQUIRED")
    try:
        from components.life.scripts import v02_agency_depth_engine as ad_engine
    except ImportError:  # pragma: no cover - flat life/scripts import path
        import v02_agency_depth_engine as ad_engine  # type: ignore[no-redef]

    projected = ad_engine.project_v02_agency_depth(
        inst_root, has_subject_capability=True, now_iso=now_iso
    )
    target = None
    for inquiry in projected.get("inquiries") or []:
        if str(inquiry.get("inquiry_id")) == inquiry_id:
            target = inquiry
            break
    if target is None:
        return _v02_refuse("INQUIRY_NOT_FOUND", inquiry_id=inquiry_id)

    status = str(target.get("status") or "")
    if status in ad_engine.INQUIRY_TERMINAL_STATUSES:
        details["bounded_inquiry"] = {
            "inquiry_id": inquiry_id,
            "status": status,
            "already_settled": True,
        }
        return None

    marker = _v02_decision_marker(event.get("decision_id"))
    op_key = str(event.get("operation_key") or "").strip()
    raw_conclusion = str(event.get("conclusion") or "").strip()
    auto_conclude = False
    grounded_conclusion = ""
    if event.get("conclude") is not False:
        auto_conclude, grounded_conclusion = _evaluate_inquiry_auto_conclusion(target)
    wants_conclude = bool(event.get("conclude") is True) or bool(raw_conclusion) or auto_conclude

    if wants_conclude:
        optional_note = str(event.get("note") or "").strip()
        raw_spent = target.get("steps_spent")
        cur_steps = (
            int(raw_spent)
            if isinstance(raw_spent, int) and not isinstance(raw_spent, bool) and raw_spent >= 0
            else len(target.get("evidence_notes") or [])
        )
        cur_budget = int(target.get("step_budget") or ad_engine.DEFAULT_INQUIRY_STEP_BUDGET)
        would_auto_stop = bool(target.get("step_budget_explicit", False)) and (
            cur_steps + 1 >= cur_budget
        )
        if optional_note and not would_auto_stop:
            ev_payload: dict[str, Any] = {
                "action": "record_inquiry_evidence",
                "inquiry_id": inquiry_id,
                "note": optional_note[:320],
                "source_ref": str(event.get("source_ref") or f"daily_loop:{marker or 'round'}").strip()[:240],
            }
            if op_key:
                ev_payload["operation_key"] = f"{op_key}:ev"[:128]
            try:
                ad_engine.apply_agency_depth_mutation(
                    inst_root, ev_payload, actor_side="subject", now_iso=now_iso
                )
            except ad_engine.V02AgencyDepthError:
                pass

        conclusion_text = (
            raw_conclusion
            or grounded_conclusion
            or f"基于日常观察收敛：{str(target.get('question') or inquiry_id)[:180]}"
        )
        conclude_payload: dict[str, Any] = {
            "action": "conclude_or_stop_inquiry",
            "inquiry_id": inquiry_id,
            "outcome": "CONCLUDED",
            "conclusion": conclusion_text[:320],
        }
        prev_belief = str(event.get("previous_belief_cn") or "").strip()
        if prev_belief:
            conclude_payload["previous_belief_cn"] = prev_belief[:320]
        if op_key:
            conclude_payload["operation_key"] = f"{op_key}:conclude"[:128]
        try:
            updated_proj, cmd_status = ad_engine.apply_agency_depth_mutation(
                inst_root, conclude_payload, actor_side="subject", now_iso=now_iso
            )
        except ad_engine.V02AgencyDepthError as exc:
            return _v02_refuse(
                exc.reason_code,
                inquiry_id=inquiry_id,
                detail=exc.message,
            )

        updated = None
        for inquiry in updated_proj.get("inquiries") or []:
            if str(inquiry.get("inquiry_id")) == inquiry_id:
                updated = inquiry
                break
        matched_rev = None
        for rev in updated_proj.get("belief_revisions") or []:
            if isinstance(rev, Mapping) and str(rev.get("inquiry_id") or "") == inquiry_id:
                matched_rev = copy.deepcopy(dict(rev))
                break
        slices_updated.append("v02_agency_depth.inquiries")
        slices_updated.append("v02_agency_depth.belief_revisions")
        details["bounded_inquiry"] = {
            "inquiry_id": inquiry_id,
            "status": (updated or {}).get("status"),
            "evidence_count": len((updated or {}).get("evidence_notes") or []),
            "conclusion": (updated or {}).get("conclusion") or conclusion_text,
            "mutation_status": cmd_status,
            "belief_revision": matched_rev,
        }
        return None

    note = "日常回合真实观察记录" + (f"〔{marker}〕" if marker else "")
    source_ref = "daily_loop:" + (marker or "round")
    payload: dict[str, Any] = {
        "action": "record_inquiry_evidence",
        "inquiry_id": inquiry_id,
        "note": note,
        "source_ref": source_ref,
    }
    if op_key:
        payload["operation_key"] = op_key
    try:
        updated_proj, cmd_status = ad_engine.apply_agency_depth_mutation(
            inst_root, payload, actor_side="subject", now_iso=now_iso
        )
    except ad_engine.V02AgencyDepthError as exc:
        return _v02_refuse(
            exc.reason_code,
            inquiry_id=inquiry_id,
            detail=exc.message,
        )

    updated = None
    for inquiry in updated_proj.get("inquiries") or []:
        if str(inquiry.get("inquiry_id")) == inquiry_id:
            updated = inquiry
            break
    slices_updated.append("v02_agency_depth.inquiries")
    details["bounded_inquiry"] = {
        "inquiry_id": inquiry_id,
        "status": (updated or {}).get("status"),
        "evidence_count": len((updated or {}).get("evidence_notes") or []),
        "mutation_status": cmd_status,
        "note": note,
    }
    return None


def _settle_v02_follow_placed_memory_cue(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Verify and settle one D01 visible placed memory cue without fabricated success."""

    cue_id = str(event.get("cue_id") or _v02_ref_suffix(str(event.get("source_ref") or "")) or "").strip()
    if not cue_id:
        return _v02_refuse("PLACED_CUE_ID_REQUIRED")
    try:
        from components.life.scripts import v02_agency_depth_engine as ad_engine
    except ImportError:  # pragma: no cover - flat life/scripts import path
        import v02_agency_depth_engine as ad_engine  # type: ignore[no-redef]

    projected = ad_engine.project_v02_agency_depth(
        inst_root, has_subject_capability=True, now_iso=now_iso
    )
    active = next(
        (c for c in (projected.get("placed_memory_cues") or {}).get("active_cues") or [] if str(c.get("cue_id")) == cue_id),
        None,
    )
    if active is None:
        return _v02_refuse("PLACED_CUE_NOT_ACTIVE", cue_id=cue_id)
    details["placed_memory_cue"] = {
        "cue_id": cue_id,
        "spot_id": active.get("spot_id"),
        "artifact_ref": active.get("artifact_ref"),
        "observed_active": True,
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v02_act_optional_next_gesture(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Settle a §10.C optional next gesture through its real owner (never mutates on peek)."""

    gesture_id = str(event.get("gesture_id") or _v02_ref_suffix(str(event.get("source_ref") or "")) or "").strip()
    if not gesture_id:
        return _v02_refuse("GESTURE_ID_REQUIRED")
    decision = str(event.get("decision") or "adopt_step").strip()
    try:
        from components.life.scripts import v02_agency_depth_engine as ad_engine
    except ImportError:  # pragma: no cover - flat life/scripts import path
        import v02_agency_depth_engine as ad_engine  # type: ignore[no-redef]

    payload: dict[str, Any] = {
        "action": "act_or_archive_gesture",
        "gesture_id": gesture_id,
        "decision": decision,
        "operation_key": str(event.get("operation_key") or f"v02_gesture:{gesture_id}")[:96],
    }
    if event.get("new_question_cn"):
        payload["new_question_cn"] = str(event.get("new_question_cn"))[:160]
    try:
        updated_proj, cmd_status = ad_engine.apply_agency_depth_mutation(
            inst_root, payload, actor_side="subject", now_iso=now_iso
        )
    except ad_engine.V02AgencyDepthError as exc:
        return _v02_refuse(exc.reason_code, gesture_id=gesture_id, detail=exc.message)

    row = next(
        (g for g in updated_proj.get("optional_next_gestures") or [] if str(g.get("gesture_id")) == gesture_id),
        {},
    )
    slices_updated.append("v02_agency_depth.optional_next_gestures")
    details["optional_next_gesture"] = {
        "gesture_id": gesture_id,
        "decision": decision,
        "status": row.get("status"),
        "claimed": bool(row.get("claimed", False)),
        "omission_debt": int(row.get("omission_debt") or 0),
        "mutation_status": cmd_status,
    }
    return None


def _settle_v02_open_future_capsule(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Resolve a matured D09 future capsule through its owner (zero omission debt)."""

    capsule_id = str(event.get("capsule_id") or _v02_ref_suffix(str(event.get("source_ref") or "")) or "").strip()
    if not capsule_id:
        return _v02_refuse("CAPSULE_ID_REQUIRED")
    decision = str(event.get("decision") or "open").strip()
    try:
        from components.life.scripts import v02_agency_depth_engine as ad_engine
    except ImportError:  # pragma: no cover - flat life/scripts import path
        import v02_agency_depth_engine as ad_engine  # type: ignore[no-redef]

    payload: dict[str, Any] = {
        "action": "resolve_future_capsule",
        "capsule_id": capsule_id,
        "decision": decision,
        "operation_key": str(event.get("operation_key") or f"v02_capsule:{capsule_id}")[:96],
    }
    try:
        updated_proj, cmd_status = ad_engine.apply_agency_depth_mutation(
            inst_root, payload, actor_side="subject", now_iso=now_iso
        )
    except ad_engine.V02AgencyDepthError as exc:
        return _v02_refuse(exc.reason_code, capsule_id=capsule_id, detail=exc.message)

    row = next(
        (c for c in updated_proj.get("future_self_capsules") or [] if str(c.get("capsule_id")) == capsule_id),
        {},
    )
    slices_updated.append("v02_agency_depth.future_self_capsules")
    details["future_capsule"] = {
        "capsule_id": capsule_id,
        "decision": decision,
        "status": row.get("status"),
        "omission_debt": int(row.get("omission_debt") or 0),
        "mutation_status": cmd_status,
    }
    return None


def _settle_v08_run_routine(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Replay one saved habit routine on real state (partial runs stay honest)."""

    del life_root
    routine_id = str(event.get("routine_id") or "").strip()
    if not routine_id:
        return _v02_refuse("V08_ROUTINE_ID_MISSING")
    op_key = str(event.get("operation_key") or "").strip() or "v08_routine"
    try:
        result = v08_open.run_personal_routine(
            inst_root,
            {
                "action": "run_personal_routine",
                "routine_id": routine_id,
                "operation_key": (op_key + "_run")[:120],
            },
            now_iso=now_iso,
        )
    except v08_open.V08OpenAffordanceError as exc:
        return _v02_refuse(exc.reason_code, detail=exc.message)
    slices_updated.append("v08_living_traces")
    details["v08_routine"] = {
        "routine_id": routine_id,
        "title_cn": result.get("title_cn"),
        "executed_count": int(result.get("executed_count") or 0),
        "executed_steps": [
            {
                "step_index": row.get("step_index"),
                "kind": row.get("kind"),
                "object_id": row.get("object_id"),
            }
            for row in (result.get("executed_steps") or [])
            if isinstance(row, Mapping)
        ],
        "blocked_step": result.get("blocked_step"),
        "times_used": int(result.get("times_used") or 0),
        "already_applied": bool(result.get("already_applied")),
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v08_open_act(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """One real gesture on a reachable object, leaving a short-lived visible sticker."""

    del life_root
    object_id = str(event.get("object_id") or "").strip()
    if not object_id:
        return _v02_refuse("V08_OBJECT_ID_MISSING")
    mode = str(event.get("open_mode") or "touch_object").strip()
    op_key = str(event.get("operation_key") or "").strip() or "v08_open_act"

    label = ""
    try:
        view = v08_open.read_living_traces_view(inst_root, viewer="subject", now_iso=now_iso)
        for row in view.get("reachable_objects") or []:
            if isinstance(row, Mapping) and str(row.get("object_id") or "") == object_id:
                label = str(row.get("label_cn") or "").strip()
                break
    except Exception:  # noqa: BLE001 - the trace write below is the authority
        label = ""

    if mode == "respond_partner_trace":
        gesture = "伸手碰了碰%s，把刚留下的痕迹接住" % (label or "那件小物")
    else:
        gesture = "顺手碰了碰%s" % (label or "手边小物")
    sticker = "%s上留下一道刚被碰过的痕迹" % (label or "手边小物")

    try:
        result = v08_open.apply_living_trace(
            inst_root,
            {
                "action": "apply_living_trace",
                "object_id": object_id,
                "gesture_cn": gesture[:v08_open.MAX_GESTURE_CHARS],
                "state_sticker_cn": sticker[:v08_open.MAX_STICKER_CHARS],
                "fade_mode": "timed",
                "ttl_minutes": v08_open.DEFAULT_TTL_MINUTES,
                "visibility": "shared",
                "operation_key": (op_key + "_trace")[:120],
            },
            actor_side="subject",
            now_iso=now_iso,
        )
    except v08_open.V08OpenAffordanceError as exc:
        return _v02_refuse(exc.reason_code, detail=exc.message)
    slices_updated.append("v08_living_traces")
    details["v08_open_act"] = {
        "object_id": object_id,
        "open_mode": mode,
        "trace_id": result.get("trace_id"),
        "fade_mode": result.get("fade_mode"),
        "expires_at": result.get("expires_at"),
        "already_applied": bool(result.get("already_applied")),
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v08_matter_open_act(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """One generic composed multi-step act over the real physical space.

    The payload comes straight from the zero-write physics scan, so a new kitchen
    or desk object never needs a bespoke daily-loop handler.  Only a genuinely
    ``APPLIED`` owner receipt reaches here (V-A guards run upstream); the world
    engine's own ``operation_key`` replay keeps a replayed Decision from cooking
    or eating twice.
    """

    del life_root
    payload = event.get("open_act_payload")
    if not isinstance(payload, Mapping) or not payload.get("steps"):
        return _v02_refuse("V08_MATTER_PAYLOAD_MISSING")
    op_key = str(event.get("operation_key") or "").strip() or "v08_matter_open_act"
    composed = copy.deepcopy(dict(payload))
    composed["operation_key"] = (op_key + "_matter")[:120]
    composed.setdefault("strenuous_outing", False)
    try:
        receipt = v08_matter.compose_open_act(
            inst_root,
            composed,
            actor_side="subject",
            now_iso=now_iso,
        )
    except v08_matter.V08MatterError as exc:
        if exc.reason_code == "DRINK_SCALDING_WAIT":
            # Ch.21 §3: a still-scalding drink is a real physical wait.  Stop
            # gently and honestly -- never a faked sip or a quenched-thirst receipt.
            return _v02_refuse(
                exc.reason_code,
                detail=exc.message,
                gentle_hold=True,
                drunk=False,
                thirst_quenched=False,
            )
        return _v02_refuse(exc.reason_code, detail=exc.message)
    slices_updated.append("world.v08_matter_space")
    details["v08_matter_act"] = {
        "matter_target_ref": str(
            event.get("matter_target_ref") or event.get("source_ref") or ""
        ),
        "status": receipt.get("status"),
        "intent_cn": receipt.get("intent_cn"),
        "steps_total": receipt.get("steps_total"),
        "steps_applied": receipt.get("steps_applied"),
        "failed_step": receipt.get("failed_step"),
        "stop_reason": receipt.get("stop_reason"),
        "already_applied": bool(receipt.get("already_applied")),
        "decision_id": event.get("decision_id") or None,
    }

    # Emergent bonus (v0.4 Ch.50 §一): a genuinely new thing was created.  Plant at
    # most one bounded "next time" question -- never a reminder, never a debt -- and
    # only while fewer than 3 open questions exist.  The operation key is derived from
    # this act, so a replayed act can never plant a duplicate.  Any engine fault is
    # swallowed: the real owner act has already landed and stays the authority.
    if (
        str(receipt.get("status") or "").strip().lower() == "applied"
        and not receipt.get("already_applied")
    ):
        for step_result in receipt.get("step_results") or []:
            if (
                isinstance(step_result, Mapping)
                and str(step_result.get("op") or "") == "TRANSFORM_CREATE"
                and str(step_result.get("status") or "applied").strip().lower() == "applied"
            ):
                _plant_v08_echo_future_question(
                    inst_root,
                    step_result,
                    open_act_key=op_key,
                    now_iso=now_iso,
                    slices_updated=slices_updated,
                    details=details,
                )
                break
    return None


def _plant_v08_echo_future_question(
    inst_root: Path,
    created_step: Mapping[str, Any],
    *,
    open_act_key: str,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> None:
    """Plant one optional future-self question after a real created output."""

    if _emergent_echoes_module() is None:
        return
    output_id = str(created_step.get("output_object_id") or "").strip()
    if not output_id:
        return
    projection = _read_emergent_echoes_projection_zero_write(inst_root)
    open_questions = [
        row
        for row in (projection.get("future_self_questions") or [])
        if isinstance(row, Mapping) and str(row.get("status") or "open").strip().lower() == "open"
    ]
    if len(open_questions) >= 3:
        return
    for row in open_questions:
        if str(row.get("anchor_ref") or "") == output_id:
            return
        if output_id in str(row.get("question_cn") or ""):
            return
    label = str(created_step.get("output_label_cn") or "").strip() or "手边的成品"
    question_cn = (
        f"下次再打开或重做「{label}」时，火候或搭配还想微调哪里？"
    )[:160]
    keywords = [part for part in (label[:24], "火候", "搭配") if part][:6]
    op_key = "v08_echo_emergent_" + hashlib.sha256(
        f"{open_act_key}|{output_id}".encode("utf-8")
    ).hexdigest()[:32]
    try:
        receipt = _call_emergent_echoes_action(
            inst_root,
            "plant_future_question",
            {
                "question_cn": question_cn,
                "anchor_kind": "object",
                "anchor_ref": output_id,
                "keywords": keywords,
                "visibility": "private",
            },
            now_iso=now_iso,
            operation_key=op_key,
        )
    except Exception:  # noqa: BLE001 - the bonus never breaks the owner act
        return
    slices_updated.append("phone.v08_emergent_echoes")
    details["v08_emergent_bonus"] = {
        "question": copy.deepcopy(receipt.get("record") or {}),
        "status": receipt.get("status"),
        "output_object_id": output_id,
        "operation_key": op_key,
    }


def _settle_v08_echo_action(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Settle one optional emergent-echo action through the echoes engine.

    ``operation_key`` replay is bounded twice: the bridge consequence ledger catches
    a replayed whole event, and the engine's own ledger turns a replayed slice key
    into ``DUPLICATE`` instead of a second write.  A missing engine is a clean
    refusal, never an optimistic write.
    """

    del life_root
    action_kind = str(event.get("action_kind") or "").strip().upper()
    op_key = str(event.get("operation_key") or "").strip() or "v08_echo"
    if action_kind == "REVISIT_FUTURE_QUESTION":
        question_id = str(event.get("question_id") or "").strip()
        if not question_id:
            return _v02_refuse("V08_ECHO_QUESTION_ID_MISSING")
        action = "revisit_future_question"
        fields: dict[str, Any] = {
            "question_id": question_id,
            "resolution": str(event.get("resolution") or "still_curious"),
            "note_cn": str(event.get("note_cn") or "")[:200],
        }
    elif action_kind == "SETTLE_SPARK_ATTEMPT":
        spark_id = str(event.get("spark_id") or "").strip()
        if not spark_id:
            return _v02_refuse("V08_ECHO_SPARK_ID_MISSING")
        action = "settle_spark_attempt"
        fields = {
            "spark_id": spark_id,
            "outcome": str(event.get("outcome") or "helped"),
            "choice_trace_cn": str(event.get("choice_trace_cn") or "")[:140],
        }
    else:
        return _v02_refuse("V08_ECHO_ACTION_UNKNOWN")

    try:
        receipt = _call_emergent_echoes_action(
            inst_root,
            action,
            fields,
            now_iso=now_iso,
            operation_key=(op_key + "_echo")[:128],
        )
    except _EchoesActionUnavailable:
        return _v02_refuse("V08_ECHOES_UNAVAILABLE")
    except Exception as exc:  # noqa: BLE001 - a named engine refusal is a named refusal
        return _v02_refuse(
            str(getattr(exc, "reason_code", "") or "V08_ECHOES_ACTION_REFUSED"),
            detail=getattr(exc, "message", None) or exc,
        )
    slices_updated.append("phone.v08_emergent_echoes")
    details["v08_emergent_echo"] = {
        "action": action,
        "status": receipt.get("status"),
        "record": copy.deepcopy(receipt.get("record") or {}),
        "already_applied": str(receipt.get("status") or "").strip().upper() == "DUPLICATE",
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v08_body_action(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Settle one calm body-care act through the composable-body engine.

    ``operation_key`` replay is bounded twice: the bridge consequence ledger catches a
    replayed whole event, and the engine's own ledger turns a replayed slice key into a
    duplicate instead of a second write.  A missing engine is a clean refusal, never an
    optimistic write.
    """

    del life_root
    action_kind = str(event.get("action_kind") or "").strip().upper()
    op_key = str(event.get("operation_key") or "").strip() or "v08_body"

    mod = _composable_body_module()
    if mod is None:
        return _v02_refuse("V08_COMPOSABLE_BODY_UNAVAILABLE")

    if action_kind == "RESOLVE_GARMENT_CARE":
        action = "resolve_garment_care"
        mode = str(event.get("mode") or "wash_garment").strip().lower()
        if mode not in ("wash_garment", "change_garment"):
            mode = "wash_garment"
        fields: dict[str, Any] = {"action": action, "mode": mode}
    elif action_kind == "USE_CYCLE_SUPPLY":
        action = "use_cycle_supply"
        fields = {"action": action}
    else:
        return _v02_refuse("V08_BODY_ACTION_UNKNOWN")

    mutator = getattr(mod, action, None)
    if not callable(mutator):
        return _v02_refuse("V08_COMPOSABLE_BODY_UNAVAILABLE")
    fields["operation_key"] = (op_key + "_body")[:120]

    try:
        result = mutator(inst_root, fields, actor_side="subject", now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001 - a named engine refusal is a named refusal
        return _v02_refuse(
            str(getattr(exc, "reason_code", "") or "V08_BODY_ACTION_REFUSED"),
            detail=getattr(exc, "message", None) or exc,
        )

    status = str(
        (result.get("status") if isinstance(result, Mapping) else "") or ""
    ).strip()
    slices_updated.append("body.v08_composable_pack")
    details["v08_composable_body"] = {
        "action": action,
        "status": status or "applied",
        "record": copy.deepcopy(result) if isinstance(result, Mapping) else {},
        "already_applied": bool(
            result.get("already_applied") if isinstance(result, Mapping) else False
        ),
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v08_microzone_adjust(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Settle one optional microzone-comfort shift through the ecology engine.

    Only a genuinely ``APPLIED`` owner receipt reaches here; the engine's own
    ``operation_key`` replay keeps a replayed Decision from shifting twice.  A missing
    engine or mutator is a clean refusal, never an optimistic write.  The preserved
    work ref / breakpoint are passed straight through so moving never loses the thread.
    """

    del life_root
    mod = _microzone_ecology_module()
    if mod is None:
        return _v02_refuse("V08_MICROZONE_UNAVAILABLE")
    mutator = getattr(mod, "adjust_microzone_comfort", None)
    if not callable(mutator):
        return _v02_refuse("V08_MICROZONE_UNAVAILABLE")

    op_key = str(event.get("operation_key") or "").strip() or "v08_microzone"
    # ``shift_reason`` is an audit-only hint; the engine's validated field set does
    # not accept it, so it never rides inside the mutation payload.
    shift_reason = str(event.get("shift_reason") or "")
    fields: dict[str, Any] = {
        "operation_key": (op_key + "_microzone")[:120],
        "target_spot_id": str(event.get("target_spot_id") or "desk_inner"),
    }
    preserve_work_ref = str(event.get("preserve_work_ref") or "").strip()
    preserve_breakpoint_cn = str(event.get("preserve_breakpoint_cn") or "").strip()
    if preserve_work_ref:
        fields["preserve_work_ref"] = preserve_work_ref
    if preserve_breakpoint_cn:
        fields["preserve_breakpoint_cn"] = preserve_breakpoint_cn
    try:
        result = mutator(inst_root, fields, actor_side="subject", now_iso=now_iso)
    except Exception as exc:  # noqa: BLE001 - a named engine refusal is a named refusal
        return _v02_refuse(
            str(getattr(exc, "reason_code", "") or "V08_MICROZONE_REFUSED"),
            detail=getattr(exc, "message", None) or exc,
        )

    status = str(
        (result.get("status") if isinstance(result, Mapping) else "") or ""
    ).strip()
    slices_updated.append("world.v08_microzone_state")
    details["v08_microzone_adjust"] = {
        "target_spot_id": fields["target_spot_id"],
        "shift_reason": shift_reason,
        "status": status or "applied",
        "record": copy.deepcopy(result) if isinstance(result, Mapping) else {},
        "already_applied": bool(
            result.get("already_applied") if isinstance(result, Mapping) else False
        ),
        "decision_id": event.get("decision_id") or None,
    }
    return None


def _settle_v03_open_shared_material(
    inst_root: Path,
    life_root: Path,
    event: Mapping[str, Any],
    *,
    now_iso: str,
    slices_updated: list[str],
    details: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Optionally advance one partner-dropped material from RECEIVED_UNOPENED to OPENED_PARTIAL.

    This is a low-pressure, optional action: the material is *not* required to be read.  A
    material the partner has already withdrawn is refused with zero write.  When the real
    encounters engine (``v03_epistemic_encounters``) has landed, its own mutation owns the
    write; otherwise the bridge advances the one material with an atomic single-writer file
    replace.  Same ``decision_id`` replay is caught by the consequence ledger before this
    handler runs, so it never double-writes.
    """

    del life_root
    material_id = str(event.get("material_id") or "").strip()
    if not material_id:
        return _v02_refuse("V03_MATERIAL_ID_MISSING")

    enc_engine = None
    try:  # lazy: the encounters engine is an adjacent, independently landed module
        from components.life.scripts import v03_epistemic_encounters as enc_engine  # type: ignore
    except Exception:  # noqa: BLE001 - absence falls back to the atomic single-writer path
        enc_engine = None
    opener = getattr(enc_engine, "open_or_use_shared_material", None) if enc_engine else None
    if callable(opener):
        try:
            result = opener(
                inst_root,
                {
                    "action": "open_or_use_shared_material",
                    "material_id": material_id,
                    "mode": str(event.get("open_mode") or "open_partial"),
                },
                operation_key=str(event.get("operation_key") or "")[:120] or None,
                now_iso=now_iso,
            )
        except Exception as exc:  # noqa: BLE001 - a refusal is named, never an optimistic write
            reason = str(getattr(exc, "reason_code", "") or "V03_MATERIAL_OPEN_REFUSED")
            return _v02_refuse(reason, detail=getattr(exc, "message", None) or exc)
        slices_updated.append("phone.v03_encounters")
        details["v03_shared_material"] = {
            "material_id": material_id,
            "status": (result or {}).get("status") if isinstance(result, Mapping) else None,
            "already_opened": bool(
                (result or {}).get("already_applied") if isinstance(result, Mapping) else False
            ),
            "decision_id": event.get("decision_id") or None,
        }
        return None

    path = Path(inst_root) / "phone" / V03_ENCOUNTERS_FILENAME
    doc = _read_optional_json_zero_write(path)
    materials = doc.get("shared_materials") if isinstance(doc, Mapping) else None
    if not isinstance(materials, list):
        return _v02_refuse("V03_ENCOUNTERS_NOT_FOUND")
    stored_ids = [
        str(row.get("material_id") or "").strip()
        for row in materials
        if isinstance(row, Mapping)
    ]
    matched = _v02_match_stored_id(material_id, stored_ids)
    if matched is None:
        return _v02_refuse("V03_MATERIAL_NOT_FOUND")
    target = next(
        (row for row in materials
         if isinstance(row, Mapping) and str(row.get("material_id") or "").strip() == matched),
        None,
    )
    status = str((target or {}).get("status") or "").strip().upper()
    if status == "WITHDRAWN":
        return _v02_refuse("MATERIAL_WITHDRAWN")
    if status in ("OPENED_PARTIAL", "USED_IN_WORK"):
        details["v03_shared_material"] = {
            "material_id": matched,
            "status": status,
            "already_opened": True,
            "decision_id": event.get("decision_id") or None,
        }
        return None

    updated_materials: list[Any] = []
    for row in materials:
        if isinstance(row, Mapping) and str(row.get("material_id") or "").strip() == matched:
            updated = dict(row)
            updated["status"] = "OPENED_PARTIAL"
            opened_pages = updated.get("opened_pages")
            if not isinstance(opened_pages, list) or not opened_pages:
                page_count = updated.get("page_count")
                updated["opened_pages"] = (
                    [1]
                    if isinstance(page_count, int) and not isinstance(page_count, bool)
                    and page_count >= 1
                    else []
                )
            updated["updated_at"] = now_iso
            updated_materials.append(updated)
        else:
            updated_materials.append(row)
    new_doc = dict(doc)
    new_doc["shared_materials"] = updated_materials
    revision = new_doc.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        new_doc["revision"] = revision + 1
    if "updated_at" in new_doc:
        new_doc["updated_at"] = now_iso
    _atomic_write_json(path, new_doc)
    slices_updated.append("phone.v03_encounters")
    details["v03_shared_material"] = {
        "material_id": matched,
        "status": "OPENED_PARTIAL",
        "already_opened": False,
        "decision_id": event.get("decision_id") or None,
    }
    return None


#: One settlement owner per 0.2 daily-loop command kind.  The handlers share the same
#: signature and return ``None`` on success or a named refusal dict; the consequence
#: ledger write (and its idempotent replay) stays in ``settle_v02_v03_round_consequences``.
_V02_DAILY_SETTLE_HANDLERS: dict[str, Any] = {
    "FOLLOW_TOMORROW_NOTE": _settle_v02_follow_tomorrow_note,
    "ADOPT_TENTATIVE_PLAN": _settle_v02_adopt_tentative_plan,
    "ADVANCE_WORKBENCH": _settle_v02_advance_workbench,
    "FOLLOW_BOOKSTORE_BOOKMARK": _settle_v02_follow_bookstore_bookmark,
    "STRENUOUS_OUTING_WALK": _settle_v02_strenuous_outing_walk,
    "OBSERVE_PLANT_JOURNAL": _settle_v02_observe_plant_journal,
    "CLARIFY_COMMON_GROUND": _settle_v02_clarify_common_ground,
    "ADVANCE_BOUNDED_INQUIRY": _settle_v02_advance_bounded_inquiry,
    "FOLLOW_PLACED_MEMORY_CUE": _settle_v02_follow_placed_memory_cue,
    "ACT_OPTIONAL_NEXT_GESTURE": _settle_v02_act_optional_next_gesture,
    "OPEN_FUTURE_CAPSULE": _settle_v02_open_future_capsule,
    "RUN_PERSONAL_ROUTINE": _settle_v08_run_routine,
    "OPEN_LIVING_TRACE": _settle_v08_open_act,
    "OPEN_MATTER_ACT": _settle_v08_matter_open_act,
    "ADJUST_MICROZONE": _settle_v08_microzone_adjust,
    "OPEN_SHARED_MATERIAL": _settle_v03_open_shared_material,
    "REVISIT_FUTURE_QUESTION": _settle_v08_echo_action,
    "SETTLE_SPARK_ATTEMPT": _settle_v08_echo_action,
    "RESOLVE_GARMENT_CARE": _settle_v08_body_action,
    "USE_CYCLE_SUPPLY": _settle_v08_body_action,
}


# ---------------------------------------------------------------------------
# Round C2: thin deterministic micro-intent classifier (0 LLM calls)
# ---------------------------------------------------------------------------

MICRO_INTENT_SCHEMA = "chiyo.life.v02_v03_micro_intent.v1"
MICRO_INTENT_COOLDOWN_FILENAME = "v02_v03_intent_cooldowns.json"
MICRO_INTENT_COOLDOWN_HOURS = 4
UNSUPPORTED_INTENTS_LOG_RELPATH = ("notes", "unsupported_intents.jsonl")

#: Unimplemented physics/causality.  These are never faked; the caller soft-lands,
#: records a short cooldown and switches to another option instead.
_UNSUPPORTED_INTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"折纸|折成.{0,6}(?:纸鹤|千纸鹤|飞机|船|星星|花|形状)|纸折"), "ORIGAMI_FOLD_GEOMETRY"),
    (re.compile(r"点燃|点着火|点火|烧着|烧起来|烧焦|引燃|烤焦|生火"), "IGNITION_OR_BURNING"),
    (re.compile(r"镇纸.{0,8}(?:压住|锁住|挡住).{0,6}风|(?:压住|锁住).{0,4}风.{0,8}镇纸"), "PAPERWEIGHT_WIND_LOCK"),
    (re.compile(r"缝补|缝上|缝合|针线|钉住|钉上|焊接|焊上|锡焊"), "SEWING_OR_SOLDERING"),
)

_TAKE_CUE = re.compile(r"拿起|拿起来|取回|取出|握着|攥着|拎起")
_PLACE_CUE = re.compile(r"放进|放入|塞进|插进|插到|夹进|夹到|摆进|藏进|装进")
_ON_CUE = re.compile(r"放在|放到|摆在|摆到|搁在|搁到|挪到|挪进|移到|搭在|搭到|披在|挂到|挂上")
_DRAPE_CUE = re.compile(r"搭在|搭到|披在|披到|挂在|挂到")
_NOTE_CUE = re.compile(r"写一句|写下|写上|记一句|记下|备注|便签|纸条|贴一张")
_ATTACH_CUE = re.compile(r"贴(?:上|在|到)|粘(?:上|在|到)|别(?:上|在|到)")
_ARRANGE_CUE = re.compile(r"排成|摆成一排|排好|排整齐|整理成一排|归成一排|摆成一列|排成一行")
_PAGE_RE = re.compile(r"第\s*(\d{1,4})\s*页")

#: Free-form eating/drinking cues.  The bare ``喝`` / ``吃`` forms require a short
#: object tail, and idioms such as 吃惊 / 吃亏 / 喝醉 / 喝止 are excluded so
#: ordinary speech never turns into a fake sip.  The target itself is resolved
#: from real matter state, never from the product name hard-coded here.
_CONSUME_CUE = re.compile(
    r"喝一口|喝点|喝掉|喝完|喝杯|喝口|饮一口|吃一口|吃点|吃掉|吃完|尝一口|尝尝"
    r"|喝(?!醉|止|斥|彩|倒彩|问|令)(?=.{1,12})"
    r"|吃(?!惊|亏|醋|力|紧|香|不开|不消)(?=.{1,12})"
)

#: Free-form surface/vessel cleaning cues.  A non-empty vessel is still refused
#: by the real engine, so this never silently empties someone's container.
_CLEAN_CUE = re.compile(
    r"擦一擦|擦擦|擦干净|擦桌面|擦桌子|擦桌|擦拭|洗一洗|洗干净|洗杯子|涮杯子|冲洗|刷干净"
)

#: Item kinds the consume primitive can actually eat or drink.
_EDIBLE_ITEM_KINDS: tuple[str, ...] = ("drink", "prepared_food", "ingredient")
#: Default fallback surfaces for a wipe when no vessel is named.
_DEFAULT_CLEAN_ZONES: tuple[str, ...] = ("home_desk", "kitchen_counter", "dining_table")


def _micro_intent_text(intent: Mapping[str, Any]) -> str:
    return str(
        intent.get("intent_cn") or intent.get("intent") or intent.get("text") or ""
    ).strip()


def _micro_intent_zone(intent: Mapping[str, Any]) -> str:
    return str(
        intent.get("zone_id")
        or intent.get("target_zone_id")
        or intent.get("place_id")
        or ""
    ).strip()


def _micro_intent_target(intent: Mapping[str, Any]) -> str:
    for key in (
        "target_parent_id",
        "carrier_id",
        "container_id",
        "book_id",
        "vessel_id",
        "target_object_id",
        "target_id",
    ):
        value = str(intent.get(key) or "").strip()
        if value:
            return value
    return ""


def micro_intent_key(
    intent: Mapping[str, Any],
    *,
    classification: str = "",
    category: str = "",
) -> str:
    """Stable cooldown key for one micro-intent (never a wall clock)."""

    explicit = str(intent.get("intent_key") or "").strip()
    if explicit:
        return explicit
    # Deliberately independent of the classification label: the same spoken
    # intent must map to the same cooldown key whether it is scanned, classified
    # or filtered.  ``classification``/``category`` stay accepted for callers'
    # readability but never split the key.
    del classification, category
    basis = "|".join(
        (
            _micro_intent_text(intent),
            str(intent.get("object_id") or intent.get("object_ref") or ""),
            _micro_intent_target(intent),
        )
    )
    return "micro:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _micro_intent_cooldown_path(root: Path | str) -> Path:
    return Path(root) / "life" / MICRO_INTENT_COOLDOWN_FILENAME


def _read_intent_cooldowns_zero_write(root: Path | str) -> dict[str, Any]:
    """Read the cooldown registry without ever creating a byte."""

    path = _micro_intent_cooldown_path(root)
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    rows = raw.get("cooldowns")
    return dict(rows) if isinstance(rows, Mapping) else {}


def _intent_cooldown_entry_active(
    entry: Any,
    *,
    now_dt: datetime,
    zone_id: str = "",
) -> bool:
    """A cooldown holds until ``expires_at`` or an explicit zone/context change."""

    if not isinstance(entry, Mapping):
        return False
    entry_zone = str(entry.get("zone_id") or "").strip()
    if entry_zone and zone_id and entry_zone != zone_id:
        return False
    expires = _parse_iso(entry.get("expires_at"))
    if expires is None:
        return False
    try:
        if now_dt.tzinfo is None:
            now_cmp = now_dt.replace(tzinfo=expires.tzinfo)
        else:
            now_cmp = now_dt
        return now_cmp < expires
    except (TypeError, ValueError):
        return False


def filter_cooled_down_intents(
    intents: Iterable[Mapping[str, Any]],
    *,
    root: Path | str,
    now_iso: str = "",
    zone_id: str = "",
) -> list[dict[str, Any]]:
    """Drop intents whose cooldown is still active; pure zero-write."""

    cooldowns = _read_intent_cooldowns_zero_write(root)
    now_dt = _parse_iso(now_iso) or datetime.now(DEFAULT_TZ)
    kept: list[dict[str, Any]] = []
    for raw in intents:
        if not isinstance(raw, Mapping):
            continue
        intent = dict(raw)
        key = micro_intent_key(intent)
        row = cooldowns.get(key)
        intent_zone = zone_id or _micro_intent_zone(intent)
        if _intent_cooldown_entry_active(row, now_dt=now_dt, zone_id=intent_zone):
            continue
        kept.append(intent)
    return kept


def _record_intent_cooldown(
    root: Path | str,
    *,
    intent_key: str,
    category: str,
    zone_id: str,
    reason_code: str,
    now_iso: str = "",
) -> dict[str, Any]:
    """Persist one short-horizon cooldown under the shared state lock."""

    path = _micro_intent_cooldown_path(root)
    now_dt = _parse_iso(now_iso) or datetime.now(DEFAULT_TZ)
    expires_dt = now_dt + timedelta(hours=MICRO_INTENT_COOLDOWN_HOURS)
    entry = {
        "intent_key": intent_key,
        "category": str(category or "unsupported_capability"),
        "zone_id": str(zone_id or ""),
        "reason_code": str(reason_code or "UNSUPPORTED_CAPABILITY"),
        "recorded_at": now_dt.isoformat(),
        "expires_at": expires_dt.isoformat(),
    }
    with _tx_guard.state_file_lock(path):
        rows = _read_intent_cooldowns_zero_write(root)
        rows[intent_key] = entry
        # Bounded registry: never let an old intent file grow without limit.
        if len(rows) > 256:
            ordered = sorted(
                rows.values(),
                key=lambda row: str(row.get("recorded_at") or ""),
                reverse=True,
            )[:256]
            rows = {str(row.get("intent_key")): row for row in ordered}
        _atomic_write_json(
            path,
            {
                "schema": MICRO_INTENT_SCHEMA,
                "revision": len(rows),
                "cooldowns": rows,
            },
        )
    return entry


def _append_unsupported_intent_log(
    root: Path | str,
    *,
    log_path: Optional[Path | str],
    record: Mapping[str, Any],
) -> None:
    """Append exactly one compact JSON line; callers only reach this on a real write path."""

    path = (
        Path(log_path)
        if log_path is not None
        else Path(root).joinpath(*UNSUPPORTED_INTENTS_LOG_RELPATH)
    )
    line = json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n"
    with _tx_guard.state_file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def _matter_objects_zero_write(root: Path | str) -> dict[str, Any]:
    try:
        state = v08_matter._read_state_zero_write(root)
    except Exception:  # noqa: BLE001 - an unreadable space resolves no label
        return {}
    objects = state.get("matter_objects") if isinstance(state, Mapping) else None
    return dict(objects) if isinstance(objects, Mapping) else {}


def _matter_zones_zero_write(root: Path | str) -> dict[str, Any]:
    try:
        state = v08_matter._read_state_zero_write(root)
    except Exception:  # noqa: BLE001 - an unreadable space resolves no zone
        return {}
    zones = state.get("zones") if isinstance(state, Mapping) else None
    return dict(zones) if isinstance(zones, Mapping) else {}


def _explicit_micro_object(intent: Mapping[str, Any]) -> str:
    return str(
        intent.get("object_id") or intent.get("object_ref") or intent.get("item_id") or ""
    ).strip()


def _object_is_live(obj: Any) -> bool:
    """A consumed or discarded row is no longer physically here."""

    return isinstance(obj, Mapping) and not obj.get("consumed") and not obj.get("discarded")


def _object_holds_edible(objects: Mapping[str, Any], object_id: str) -> bool:
    for child in objects.values():
        if not _object_is_live(child):
            continue
        if str(child.get("parent_id") or "") != object_id:
            continue
        if str(child.get("item_kind") or "") in _EDIBLE_ITEM_KINDS:
            return True
    return False


def _is_consumable_object(objects: Mapping[str, Any], object_id: str) -> bool:
    obj = objects.get(object_id)
    if not _object_is_live(obj):
        return False
    kind = str(obj.get("item_kind") or "")
    if kind in _EDIBLE_ITEM_KINDS:
        return True
    # A vessel delegates to the real drink/food it currently holds.
    return kind == "vessel" and _object_holds_edible(objects, object_id)


def _objects_mentioned_in_text(
    objects: Mapping[str, Any],
    text: str,
    *,
    predicate: Optional[Any] = None,
) -> list[str]:
    """Real, non-terminal objects whose own ``label_cn`` appears verbatim.

    Longest label wins so a specific ``冰镇可乐`` is never shadowed by a shorter
    generic substring; the id is the tie-breaker, keeping the result stable.
    """

    matches: list[tuple[int, str]] = []
    for object_id, obj in objects.items():
        if not _object_is_live(obj):
            continue
        label = str(obj.get("label_cn") or "").strip()
        if not label or label not in text:
            continue
        if predicate is not None and not predicate(str(object_id)):
            continue
        matches.append((len(label), str(object_id)))
    matches.sort(key=lambda item: (-item[0], item[1]))
    return [object_id for _, object_id in matches]


def _resolve_micro_intent_object(
    root: Path | str,
    intent: Mapping[str, Any],
    hints: tuple[str, ...],
) -> str:
    explicit = _explicit_micro_object(intent)
    if explicit:
        return explicit
    objects = _matter_objects_zero_write(root)
    for object_id, obj in objects.items():
        if not isinstance(obj, Mapping):
            continue
        label = str(obj.get("label_cn") or "")
        if any(hint and hint in label for hint in hints):
            return str(object_id)
    text = _micro_intent_text(intent)
    for hint in hints:
        if hint and hint in text:
            for object_id, obj in objects.items():
                if isinstance(obj, Mapping) and str(obj.get("label_cn") or "") == hint:
                    return str(object_id)
    # Zero-code recognition: any real object whose own Chinese label appears in
    # the free text resolves without a per-product hard code.
    mentioned = _objects_mentioned_in_text(objects, text)
    if mentioned:
        return mentioned[0]
    return ""


def _resolve_consumable_micro_target(
    root: Path | str,
    intent: Mapping[str, Any],
    text: str,
) -> str:
    objects = _matter_objects_zero_write(root)
    explicit = _explicit_micro_object(intent)
    if explicit:
        # Never fall back to a different object when the caller named one that
        # is absent or inedible: that would be a fabricated success.
        return explicit if _is_consumable_object(objects, explicit) else ""
    mentioned = _objects_mentioned_in_text(
        objects, text, predicate=lambda oid: _is_consumable_object(objects, oid)
    )
    return mentioned[0] if mentioned else ""


def _micro_intent_portions(intent: Mapping[str, Any]) -> int:
    raw = intent.get("portions")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return min(12, raw)
    return 1


def _resolve_clean_micro_target(
    root: Path | str,
    intent: Mapping[str, Any],
    text: str,
) -> str:
    objects = _matter_objects_zero_write(root)
    zones = _matter_zones_zero_write(root)
    candidates: list[str] = []
    explicit = _explicit_micro_object(intent)
    if explicit:
        candidates.append(explicit)
    candidates.extend(_objects_mentioned_in_text(objects, text))
    # A real vessel wins over a flat surface when both are in play.
    for object_id in candidates:
        obj = objects.get(object_id)
        if not _object_is_live(obj):
            continue
        if str(obj.get("item_kind") or "") == "vessel" or int(obj.get("capacity_slots") or 0) > 0:
            return object_id
    target = _micro_intent_target(intent)
    if target and (target in objects or target in zones):
        return target
    if re.search(r"厨房|台面|灶台", text) and "kitchen_counter" in zones:
        return "kitchen_counter"
    if re.search(r"餐桌|饭桌", text) and "dining_table" in zones:
        return "dining_table"
    for object_id in candidates:
        if _object_is_live(objects.get(object_id)):
            return object_id
    for zone_id in _DEFAULT_CLEAN_ZONES:
        if zone_id in zones:
            return zone_id
    return ""


def _resolve_micro_carrier(
    root: Path | str,
    intent: Mapping[str, Any],
    hints: tuple[str, ...],
    fallback: str,
) -> str:
    target = _micro_intent_target(intent)
    if target:
        return target
    objects = _matter_objects_zero_write(root)
    for object_id, obj in objects.items():
        if not isinstance(obj, Mapping):
            continue
        label = str(obj.get("label_cn") or "")
        if any(hint and hint in label for hint in hints):
            return str(object_id)
    return fallback


def _extract_anchor_page(intent: Mapping[str, Any]) -> Optional[int]:
    raw = intent.get("anchor_page")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 2000:
        return raw
    match = _PAGE_RE.search(_micro_intent_text(intent))
    if match:
        page = int(match.group(1))
        if 1 <= page <= 2000:
            return page
    return None


def _normalize_steps(raw_steps: Any) -> Optional[list[dict[str, Any]]]:
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        return None
    typed: list[dict[str, Any]] = []
    for raw in raw_steps:
        if not isinstance(raw, Mapping):
            return None
        step = dict(raw)
        op = str(step.get("op") or "").strip().upper()
        if op not in v08_matter.OPEN_PRIMITIVES:
            return None
        step["op"] = op
        typed.append(step)
    return typed


def _unsupported_micro_intent(
    root: Path | str,
    intent: Mapping[str, Any],
    *,
    category: str,
    summary: str,
    zone_id: str,
    now_iso: str,
    log_path: Optional[Path | str],
    reason_code: str = "UNSUPPORTED_CAPABILITY",
) -> dict[str, Any]:
    key = micro_intent_key(intent, classification="unsupported_capability", category=category)
    cooldown = _record_intent_cooldown(
        root,
        intent_key=key,
        category=category,
        zone_id=zone_id,
        reason_code=reason_code,
        now_iso=now_iso,
    )
    logged = False
    if reason_code == "UNSUPPORTED_CAPABILITY":
        now_dt = _parse_iso(now_iso) or datetime.now(DEFAULT_TZ)
        _append_unsupported_intent_log(
            root,
            log_path=log_path,
            record={
                "ts": now_dt.isoformat(),
                "intent_key": key,
                "category": category,
                "summary": summary[:160],
                "zone_id": zone_id,
                "reason_code": reason_code,
            },
        )
        logged = True
    return {
        "schema": MICRO_INTENT_SCHEMA,
        "ok": False,
        "classification": "unsupported_capability",
        "intent_cn": _micro_intent_text(intent),
        "intent_key": key,
        "category": category,
        "reason_code": reason_code,
        "soft_landing": "defer_and_switch",
        "steps": [],
        "zone_id": zone_id,
        "cooldown": cooldown,
        "unsupported_logged": logged,
        "llm_calls": 0,
    }


def classify_and_normalize_micro_intent(
    root: Path | str,
    intent: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: str = "",
    log_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Deterministically classify one free-form micro-intent (0 LLM calls).

    Four honest outcomes:
    * ``direct_supported``      -> exactly one typed ``v08_universal_matter`` step;
    * ``compositional_supported`` -> a bounded 2..6 step sequence for
      ``compose_open_act``;
    * ``coarse_approximation``  -> a loosely achievable act with explicit
      ``what_is_approximated`` / ``cannot_guarantee`` (never fake exact physics);
    * ``unsupported_capability`` -> nothing is claimed, a cooldown is recorded and
      one compact line is appended to ``unsupported_intents.jsonl``.
    """

    if not isinstance(intent, Mapping):
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": False,
            "classification": "unsupported_capability",
            "reason_code": "INTENT_NOT_MAPPING",
            "soft_landing": "defer_and_switch",
            "steps": [],
            "llm_calls": 0,
        }

    text = _micro_intent_text(intent)
    zone_id = _micro_intent_zone(intent)
    effective_now = now_iso or datetime.now(DEFAULT_TZ).isoformat()

    # 0. Explicit typed steps win: they are the caller's own normalized form.
    explicit_steps = _normalize_steps(intent.get("steps"))
    if explicit_steps is not None:
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": (
                "direct_supported" if len(explicit_steps) == 1 else "compositional_supported"
            ),
            "intent_cn": text,
            "intent_key": key,
            "steps": explicit_steps,
            "approximation": False,
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    # 1. Unimplemented physics/causality -> never faked.
    for pattern, category in _UNSUPPORTED_INTENT_PATTERNS:
        if pattern.search(text):
            return _unsupported_micro_intent(
                root,
                intent,
                category=category,
                summary=text or category,
                zone_id=zone_id,
                now_iso=effective_now,
                log_path=log_path,
            )

    # 1b. Free-form eating/drinking: the target must be a real, live drink/food
    # (or a vessel holding one) whose own label appears in the text.  A missing
    # drink falls through to the honest unsupported soft landing instead of
    # conjuring a bottle that never existed in the room.
    if _CONSUME_CUE.search(text):
        consumable_id = _resolve_consumable_micro_target(root, intent, text)
        if consumable_id:
            key = micro_intent_key(intent, classification="direct_supported")
            return {
                "schema": MICRO_INTENT_SCHEMA,
                "ok": True,
                "classification": "direct_supported",
                "intent_cn": text,
                "intent_key": key,
                "approximation": False,
                "steps": [
                    {
                        "op": "CONSUME_PORTION",
                        "object_id": consumable_id,
                        "portions": _micro_intent_portions(intent),
                    }
                ],
                "zone_id": zone_id,
                "llm_calls": 0,
            }

    # 1c. Free-form washing/wiping: a real vessel first, else the named/default
    # surface.  The real engine still refuses a non-empty vessel
    # (``CONTAINER_NOT_EMPTY``) -- nothing is ever tipped out to fake it.
    if _CLEAN_CUE.search(text):
        clean_target_id = _resolve_clean_micro_target(root, intent, text)
        if clean_target_id:
            clean_step: dict[str, Any] = {
                "op": "CLEAN_SURFACE_OR_VESSEL",
                "target_id": clean_target_id,
            }
            clean_obj = _matter_objects_zero_write(root).get(clean_target_id)
            if isinstance(clean_obj, Mapping) and (
                str(clean_obj.get("item_kind") or "") == "vessel"
                or int(clean_obj.get("capacity_slots") or 0) > 0
            ):
                # A vessel is washed at the real sink, not wiped dry in place.
                clean_step["helper_object_id"] = "kitchen_sink"
            key = micro_intent_key(intent, classification="direct_supported")
            return {
                "schema": MICRO_INTENT_SCHEMA,
                "ok": True,
                "classification": "direct_supported",
                "intent_cn": text,
                "intent_key": key,
                "approximation": False,
                "steps": [clean_step],
                "zone_id": zone_id,
                "llm_calls": 0,
            }

    page = _extract_anchor_page(intent)
    object_id = _resolve_micro_intent_object(
        root, intent, ("外套", "大衣", "衣服", "围巾", "毯", "画笔", "铅笔", "干叶", "叶")
    )
    carrier = _micro_intent_target(intent)

    # 2. Coarse approximation: qualitative comfort/rest only, no numeric physics.
    if re.search(r"暖手|捂手|焐手|贴着.{0,4}(?:热|温)|捧.{0,3}暖", text) and re.search(
        r"杯|水|茶|咖啡|壶", text
    ):
        mug_id = object_id or _resolve_micro_intent_object(root, intent, ("杯", "茶", "咖啡"))
        if mug_id:
            key = micro_intent_key(intent, classification="coarse_approximation", category="WARMTH")
            return {
                "schema": MICRO_INTENT_SCHEMA,
                "ok": True,
                "classification": "coarse_approximation",
                "intent_cn": text,
                "intent_key": key,
                "approximation": True,
                "steps": [
                    {"op": "TAKE_OBJECT", "object_id": mug_id},
                    {
                        "op": "NOTE_ASSOCIATION",
                        "target_id": mug_id,
                        "text": "捧一会儿，手心慢慢暖过来",
                    },
                ],
                "what_is_approximated": "只表示捧着温热的杯子暖一暖手",
                "cannot_guarantee": "不保证具体温度读数，也不声称改变任何身体数值",
                "zone_id": zone_id,
                "llm_calls": 0,
            }

    if re.search(r"歇一会|歇会儿|歇一歇|坐一会|坐会儿|靠一会|躺一会|休息一下", text):
        seat_id = _resolve_micro_carrier(
            root, intent, ("椅", "床", "沙发", "凳"), "home_bed"
        )
        key = micro_intent_key(intent, classification="coarse_approximation", category="REST")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "coarse_approximation",
            "intent_cn": text,
            "intent_key": key,
            "approximation": True,
            "steps": [{"op": "OBSERVE_OBJECT", "object_id": seat_id}],
            "what_is_approximated": "只走到一个能坐下的地方歇一会儿",
            "cannot_guarantee": "不保证体力真的恢复，也不改变睡眠或疲劳数值",
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    # 3. Compositional: take/open carrier -> place inside -> note/association.
    has_note = bool(_NOTE_CUE.search(text))
    has_place_inside = bool(_PLACE_CUE.search(text))
    if page is not None and has_note:
        book_id = carrier or _resolve_micro_carrier(
            root, intent, ("书", "本子", "册", "日记", "笔记"), "home_desk"
        )
        key = micro_intent_key(intent, classification="compositional_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "compositional_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {"op": "TAKE_OBJECT", "object_id": object_id},
                {
                    "op": "PLACE_INSIDE",
                    "object_id": object_id,
                    "target_parent_id": book_id,
                    "placement_relation": "tucked_in",
                    "anchor_page": page,
                },
                {
                    "op": "NOTE_ASSOCIATION",
                    "target_id": book_id,
                    "text": "夹在这一页的痕迹",
                },
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _TAKE_CUE.search(text) and has_place_inside and has_note:
        arrangement_note = re.search(r"[「“\"]([^」”\"]{1,40})[」”\"]", text)
        note_text = arrangement_note.group(1) if arrangement_note else "顺手记下这一下"
        key = micro_intent_key(intent, classification="compositional_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "compositional_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {"op": "TAKE_OBJECT", "object_id": object_id},
                {
                    "op": "PLACE_INSIDE",
                    "object_id": object_id,
                    "target_parent_id": carrier or "home_desk",
                    "placement_relation": "inside",
                },
                {
                    "op": "NOTE_ASSOCIATION",
                    "target_id": carrier or object_id,
                    "text": note_text[:80],
                },
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    # 4. Direct single typed step.
    if _DRAPE_CUE.search(text):
        carrier_id = _resolve_micro_carrier(
            root, intent, ("椅", "床", "架", "钩", "杆"), "home_bed"
        )
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "MOVE_OBJECT",
                    "object_id": object_id,
                    "target_parent_id": carrier_id,
                    "placement_relation": "draped_on",
                    "posture_tag": "casual_drape",
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if page is not None:
        book_id = carrier or _resolve_micro_carrier(
            root, intent, ("书", "本子", "册", "日记", "笔记"), "home_desk"
        )
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "PLACE_INSIDE",
                    "object_id": object_id,
                    "target_parent_id": book_id,
                    "placement_relation": "tucked_in",
                    "anchor_page": page,
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _PLACE_CUE.search(text):
        target_id = carrier or "home_desk"
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "PLACE_INSIDE",
                    "object_id": object_id,
                    "target_parent_id": target_id,
                    "placement_relation": str(intent.get("placement_relation") or "inside"),
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _ARRANGE_CUE.search(text) or intent.get("member_ids"):
        members = intent.get("member_ids")
        member_ids = (
            [str(m) for m in members if str(m).strip()]
            if isinstance(members, (list, tuple))
            else []
        )
        if not member_ids and object_id:
            member_ids = [object_id]
        target_id = carrier or "home_desk"
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "ARRANGE_OBJECTS",
                    "zone_or_carrier_id": target_id,
                    "member_ids": member_ids,
                    "pattern": str(intent.get("pattern") or "row"),
                    "arrangement_id": str(intent.get("arrangement_id") or "") or None,
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _ATTACH_CUE.search(text):
        target_id = carrier or "home_desk"
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "ATTACH_NOTE",
                    "object_id": object_id,
                    "target_parent_id": target_id,
                    "placement_relation": "attached_to",
                    "posture_tag": "half_showing",
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _TAKE_CUE.search(text) and not _ON_CUE.search(text):
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [{"op": "TAKE_OBJECT", "object_id": object_id}],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _ON_CUE.search(text):
        target_id = carrier or "home_desk"
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {
                    "op": "MOVE_OBJECT",
                    "object_id": object_id,
                    "target_parent_id": target_id,
                    "placement_relation": str(intent.get("placement_relation") or "on"),
                    "posture_tag": str(intent.get("posture_tag") or ""),
                }
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    if _NOTE_CUE.search(text):
        target_id = carrier or object_id or "home_desk"
        note_text = str(intent.get("association_text") or intent.get("note_cn") or text)[:80]
        key = micro_intent_key(intent, classification="direct_supported")
        return {
            "schema": MICRO_INTENT_SCHEMA,
            "ok": True,
            "classification": "direct_supported",
            "intent_cn": text,
            "intent_key": key,
            "approximation": False,
            "steps": [
                {"op": "NOTE_ASSOCIATION", "target_id": target_id, "text": note_text}
            ],
            "zone_id": zone_id,
            "llm_calls": 0,
        }

    # 5. Truly unmapped -> soft landing, never a fabricated success.
    return _unsupported_micro_intent(
        root,
        intent,
        category="UNMAPPED_BRIDGE_ACTION",
        summary=text or "unmapped micro intent",
        zone_id=zone_id,
        now_iso=effective_now,
        log_path=log_path,
        reason_code="UNSUPPORTED_CAPABILITY",
    )


def execute_normalized_micro_intent(
    root: Path | str,
    intent: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: str = "",
    operation_key: str = "",
    log_path: Optional[Path | str] = None,
    accept_approximation: bool = False,
) -> dict[str, Any]:
    """Classify and (when honest/supported) run the act through the real engine.

    Unsupported or refused intents return ``settled=False, soft_landed=True`` and
    never claim success; coarse approximations run only when the caller explicitly
    accepts them.
    """

    if actor_side != "subject":
        return {
            "ok": False,
            "settled": False,
            "soft_landed": True,
            "status": "SOFT_LANDED",
            "reason_code": "UNAUTHORIZED_CONSEQUENCE_ACTOR",
            "llm_calls": 0,
        }

    normalized = classify_and_normalize_micro_intent(
        root,
        intent,
        actor_side=actor_side,
        now_iso=now_iso,
        log_path=log_path,
    )
    if not normalized.get("ok"):
        normalized.update(
            {
                "settled": False,
                "soft_landed": True,
                "status": "SOFT_LANDED",
            }
        )
        return normalized

    if normalized.get("approximation") and not accept_approximation:
        normalized.update(
            {
                "settled": False,
                "soft_landed": True,
                "status": "SOFT_LANDED",
                "reason_code": "COARSE_APPROXIMATION_NOT_ACCEPTED",
                "approval_required_for_steps": True,
            }
        )
        return normalized

    now_text = now_iso or datetime.now(DEFAULT_TZ).isoformat()
    steps = list(normalized.get("steps") or [])
    op_key = operation_key.strip() or ("micro_intent_" + str(normalized.get("intent_key") or "")[-24:])
    payload: dict[str, Any] = {
        "intent_cn": normalized.get("intent_cn") or "顺手整理一下",
        "steps": steps,
        "allow_partial": True,
        "operation_key": op_key[:120],
    }
    for key in ("placement_relation", "posture_tag", "anchor_page"):
        if intent.get(key) is not None:
            payload[key] = intent.get(key)

    try:
        receipt = v08_matter.compose_open_act(
            root, payload, actor_side="subject", now_iso=now_text
        )
    except v08_matter.V08MatterError as exc:
        soft = {
            "ok": False,
            "settled": False,
            "soft_landed": True,
            "status": "SOFT_LANDED",
            "reason_code": exc.reason_code,
            "detail": exc.message,
            "classification": normalized.get("classification"),
            "intent_key": normalized.get("intent_key"),
            "steps": steps,
            "llm_calls": 0,
        }
        if exc.reason_code == "DRINK_SCALDING_WAIT":
            # Ch.21 §3: wait honestly at the desk -- no sip, no thirst receipt.
            soft["gentle_hold"] = True
            soft["drunk"] = False
            soft["thirst_quenched"] = False
        return soft
    except Exception as exc:  # noqa: BLE001 - a broken engine is a named soft landing
        return {
            "ok": False,
            "settled": False,
            "soft_landed": True,
            "status": "SOFT_LANDED",
            "reason_code": "MICRO_INTENT_ENGINE_FAULT",
            "detail": f"{type(exc).__name__}: {exc}"[:200],
            "classification": normalized.get("classification"),
            "intent_key": normalized.get("intent_key"),
            "steps": steps,
            "llm_calls": 0,
        }

    status = str(receipt.get("status") or "").strip().lower()
    applied = status in ("applied", "partially_applied")
    return {
        "ok": True,
        "settled": applied,
        "soft_landed": status == "partially_applied",
        "status": status or "applied",
        "classification": normalized.get("classification"),
        "intent_key": normalized.get("intent_key"),
        "intent_cn": normalized.get("intent_cn"),
        "steps": steps,
        "receipt": receipt,
        "reason_code": receipt.get("stop_reason") if status == "partially_applied" else None,
        "llm_calls": 0,
    }


def _extract_rlf_creator_receipt_id(ver: Mapping[str, Any]) -> str:
    raw_cref = ver.get("creator_receipt_ref")
    if not raw_cref and isinstance(ver.get("source"), Mapping):
        raw_cref = ver["source"].get("creator_receipt_ref")
    if isinstance(raw_cref, Mapping):
        return str(raw_cref.get("id") or raw_cref.get("receipt_id") or "").strip()
    return str(raw_cref or "").strip()


def _verify_authoritative_owner_receipt_zero_write(
    inst_root: Path,
    round_event: Mapping[str, Any],
    receipt_ref: str,
    *,
    day_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[bool, str]:
    """Cross-check receipt_ref, action_id, kind, target artifact/version, and owner against persisted owners (zero-write)."""
    import sqlite3 as _sqlite3

    action_id = str(round_event.get("action_id") or "").strip()
    event_kind = str(round_event.get("action_kind") or "").strip().upper()
    event_art_id = str(round_event.get("artifact_id") or "").strip()
    event_ver_id = str(round_event.get("version_id") or round_event.get("target_version_id") or "").strip()
    event_owner = str(round_event.get("action_owner") or round_event.get("owner") or "").strip()
    require_auth = bool(round_event.get("require_authoritative_receipt"))
    has_auth_prefix = receipt_ref.startswith((
        "crcpt:",
        "prcpt:",
        "orrcpt:",
        "reconciler:",
        "sqlite:",
        "exec:",
        "oprcpt:",
        "arrcpt:",
        "prov_rcpt_",
    ))

    found_positive = False
    found_negative = False
    has_owner_store = False

    def _ev_json_matches(ev_raw: Any) -> bool:
        if not ev_raw:
            return True
        try:
            ev_obj = json.loads(str(ev_raw)) if isinstance(ev_raw, str) else ev_raw
        except (ValueError, TypeError):
            return True
        if not isinstance(ev_obj, Mapping):
            return True
        ev_art = str(ev_obj.get("artifact_id") or "").strip()
        if event_art_id and ev_art and ev_art != event_art_id:
            return False
        ev_ver = str(ev_obj.get("version_id") or "").strip()
        if event_ver_id and ev_ver and ev_ver != event_ver_id:
            return False
        ev_kind = str(ev_obj.get("action_kind") or "").strip().upper()
        if event_kind and ev_kind and ev_kind != event_kind:
            return False
        ev_own = str(ev_obj.get("owner") or ev_obj.get("action_owner") or "").strip()
        if event_owner and ev_own and ev_own != event_owner:
            return False
        return True

    _ledger_paths = _resolve_instance_paths_zero_write(inst_root)
    _db_candidates_raw: list[Path] = []
    if _ledger_paths is not None:
        _db_candidates_raw.extend(
            (
                _ledger_paths.canonical_ledger_db,
                _ledger_paths.day_root / "v02" / "canonical_life.sqlite3",
                _ledger_paths.life_state_root / "reconciler" / "canonical_life.sqlite3",
                _ledger_paths.life_state_root / "canonical_life.sqlite3",
            )
        )
    _db_candidates_raw.extend(
        (
            inst_root / "v02" / "canonical_life.sqlite3",
            inst_root / "canonical_life.sqlite3",
            inst_root / "reconciler" / "canonical_life.sqlite3",
        )
    )
    _db_candidates: list[Path] = []
    _seen_dbs: set[str] = set()
    for _dbc in _db_candidates_raw:
        _k = str(_dbc)
        if _k not in _seen_dbs:
            _seen_dbs.add(_k)
            _db_candidates.append(_dbc)

    for db_cand in _db_candidates:
        if db_cand.is_symlink() or not db_cand.is_file():
            continue
        conn = None
        try:
            shm_exists = db_cand.with_name(db_cand.name + "-shm").is_file()
            wal_path = db_cand.with_name(db_cand.name + "-wal")
            has_wal = wal_path.is_file() and wal_path.stat().st_size > 0
            q = "?mode=ro" if (shm_exists or has_wal) else "?mode=ro&immutable=1"
            conn = _sqlite3.connect(db_cand.resolve().as_uri() + q, uri=True, timeout=1.0)
            conn.row_factory = _sqlite3.Row
            tables = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "actions" in tables:
                has_owner_store = True
            if "action_receipts" in tables and "actions" in tables:
                r_rows = conn.execute(
                    "SELECT ar.receipt_id, ar.action_id, ar.receipt_kind, ar.claimed_outcome, "
                    "ar.authoritative_for_machine_success, ar.actor_id, ar.evidence_json, "
                    "a.outcome AS action_outcome, a.provider_account, a.effect_namespace "
                    "FROM action_receipts ar LEFT JOIN actions a ON a.action_id = ar.action_id "
                    "WHERE ar.receipt_id = ?",
                    (receipt_ref,),
                ).fetchall()
                for row in r_rows:
                    row_aid = str(row["action_id"] or "").strip()
                    if action_id and row_aid != action_id:
                        found_negative = True
                        continue
                    row_owner = str(row["provider_account"] or row["actor_id"] or "").strip()
                    if event_owner and row_owner and row_owner != event_owner:
                        found_negative = True
                        continue
                    if not _ev_json_matches(row["evidence_json"]):
                        found_negative = True
                        continue
                    target_ok = True
                    if (event_art_id or event_ver_id) and "artifact_versions" in tables:
                        av_rows = conn.execute(
                            "SELECT version_id, artifact_id FROM artifact_versions "
                            "WHERE creator_receipt_id = ? OR action_id = ?",
                            (receipt_ref, row_aid),
                        ).fetchall()
                        if av_rows:
                            target_ok = any(
                                (not event_art_id or str(av["artifact_id"] or "") == event_art_id)
                                and (not event_ver_id or str(av["version_id"] or "") == event_ver_id)
                                for av in av_rows
                            )
                    if (
                        target_ok
                        and int(row["authoritative_for_machine_success"] or 0) == 1
                        and str(row["claimed_outcome"] or "").upper() == "SUCCEEDED"
                        and str(row["action_outcome"] or "").upper() == "SUCCEEDED"
                    ):
                        found_positive = True
                    else:
                        found_negative = True
            if "actions" in tables and not found_positive and not found_negative:
                if not action_id or receipt_ref == action_id:
                    a_row = conn.execute(
                        "SELECT action_id, outcome, provider_account, effect_namespace FROM actions WHERE action_id = ?",
                        (receipt_ref,),
                    ).fetchone()
                    if a_row is not None:
                        a_owner = str(a_row["provider_account"] or "").strip()
                        owner_ok = (not event_owner) or (not a_owner) or (a_owner == event_owner)
                        target_ok = True
                        if (event_art_id or event_ver_id) and "artifact_versions" in tables:
                            av_rows = conn.execute(
                                "SELECT version_id, artifact_id FROM artifact_versions WHERE action_id = ?",
                                (str(a_row["action_id"]),),
                            ).fetchall()
                            if av_rows:
                                target_ok = any(
                                    (not event_art_id or str(av["artifact_id"] or "") == event_art_id)
                                    and (not event_ver_id or str(av["version_id"] or "") == event_ver_id)
                                    for av in av_rows
                                )
                        if owner_ok and target_ok and str(a_row["outcome"] or "").upper() == "SUCCEEDED":
                            found_positive = True
                        else:
                            found_negative = True
            if "artifact_versions" in tables and not found_positive and not found_negative:
                v_row = conn.execute(
                    "SELECT version_id, artifact_id, action_id FROM artifact_versions WHERE creator_receipt_id = ?",
                    (receipt_ref,),
                ).fetchone()
                if v_row is not None:
                    v_aid = str(v_row["action_id"] or "").strip()
                    v_art = str(v_row["artifact_id"] or "").strip()
                    v_ver = str(v_row["version_id"] or "").strip()
                    if (
                        (not action_id or not v_aid or v_aid == action_id)
                        and (not event_art_id or v_art == event_art_id)
                        and (not event_ver_id or v_ver == event_ver_id)
                    ):
                        found_positive = True
                    else:
                        found_negative = True
        except (_sqlite3.Error, OSError):
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except _sqlite3.Error:
                    pass

    for ext_rel in (
        ("external_provider_ledger.json",),
        ("reconciler", "external_provider_ledger.json"),
        ("control_authority", "external_provider_ledger.json"),
    ):
        ext_doc = _read_optional_json_zero_write(inst_root.joinpath(*ext_rel))
        if isinstance(ext_doc, Mapping):
            has_owner_store = True
            entries: list[Mapping[str, Any]] = []
            for key in ("invocations", "receipts", "entries", "actions", "operations"):
                blk = ext_doc.get(key)
                if isinstance(blk, Mapping):
                    entries.extend(v for v in blk.values() if isinstance(v, Mapping))
                elif isinstance(blk, list):
                    entries.extend(v for v in blk if isinstance(v, Mapping))
            for ent in entries:
                rid = str(
                    ent.get("provider_receipt_id")
                    or ent.get("receipt_id")
                    or ent.get("receipt_ref")
                    or ""
                ).strip()
                eid = str(ent.get("logical_action_id") or ent.get("action_id") or "").strip()
                seq_val = ent.get("invocation_seq")
                seq_rid = (
                    f"prov_rcpt_{int(seq_val):06d}"
                    if isinstance(seq_val, int) and not isinstance(seq_val, bool)
                    else ""
                )
                rcpt_match = (rid and rid == receipt_ref) or (seq_rid and seq_rid == receipt_ref)
                act_match = bool(action_id and eid and eid == action_id)
                if rcpt_match or act_match:
                    st = str(ent.get("outcome") or ent.get("status") or "").strip().upper()
                    ent_art = str(ent.get("artifact_id") or "").strip()
                    ent_ver = str(ent.get("version_id") or "").strip()
                    ent_kind = str(ent.get("action_kind") or "").strip().upper()
                    ent_owner = str(ent.get("owner") or ent.get("provider_account") or "").strip()
                    fields_ok = (
                        (not action_id or not eid or eid == action_id)
                        and (not rid or rid == receipt_ref or seq_rid == receipt_ref)
                        and (not event_art_id or not ent_art or ent_art == event_art_id)
                        and (not event_ver_id or not ent_ver or ent_ver == event_ver_id)
                        and (not event_kind or not ent_kind or ent_kind == event_kind)
                        and (not event_owner or not ent_owner or ent_owner == event_owner)
                    )
                    if rcpt_match and fields_ok and st in ("SUCCEEDED", "APPLIED", "CONFIRMED", "COMMITTED"):
                        found_positive = True
                    elif (not rid) and (not seq_rid) and act_match and fields_ok and st in ("SUCCEEDED", "APPLIED", "CONFIRMED", "COMMITTED"):
                        found_positive = True
                    else:
                        found_negative = True

    rlf_doc = _read_optional_json_zero_write(
        inst_root / "governance" / "v02_representation_lease_forget_state.json"
    )
    if isinstance(rlf_doc, Mapping) and not found_positive and not found_negative:
        rlf_versions: list[Mapping[str, Any]] = []
        top_vers = rlf_doc.get("versions")
        if isinstance(top_vers, Mapping):
            rlf_versions.extend(v for v in top_vers.values() if isinstance(v, Mapping))
        elif isinstance(top_vers, list):
            rlf_versions.extend(v for v in top_vers if isinstance(v, Mapping))
        for art in (rlf_doc.get("artifacts") or {}).values():
            if not isinstance(art, Mapping):
                continue
            av = art.get("versions")
            if isinstance(av, Mapping):
                rlf_versions.extend(v for v in av.values() if isinstance(v, Mapping))
            elif isinstance(av, list):
                rlf_versions.extend(v for v in av if isinstance(v, Mapping))
        for ver in rlf_versions:
            if _extract_rlf_creator_receipt_id(ver) == receipt_ref:
                ver_art_id = str(ver.get("artifact_id") or "").strip()
                ver_id = str(ver.get("version_id") or "").strip()
                src_blk = ver.get("source") if isinstance(ver.get("source"), Mapping) else {}
                cref_blk = src_blk.get("creator_receipt_ref") if isinstance(src_blk.get("creator_receipt_ref"), Mapping) else {}
                ver_owner = str(cref_blk.get("owner") or "").strip()
                if (
                    (not event_art_id or not ver_art_id or ver_art_id == event_art_id)
                    and (not event_ver_id or not ver_id or ver_id == event_ver_id)
                    and (not event_owner or not ver_owner or ver_owner == event_owner)
                ):
                    found_positive = True
                else:
                    found_negative = True
                break

    if found_negative:
        return False, "UNVERIFIED_OWNER_RECEIPT"
    if found_positive:
        return True, "OWNER_RECEIPT_VERIFIED"

    is_internal_bridge_event = False
    if has_owner_store and (
        str(round_event.get("operation_key") or "").startswith(
            ("v02v03:", "v02v03_daily_", "resident_v04_")
        )
        or receipt_ref.startswith(("v02v03:", "v04:"))
    ):
        actor_raw = str(round_event.get("actor_side") or "").strip().lower()
        src_raw = str(round_event.get("source_ref") or "").strip()
        if actor_raw == "life_resident" and src_raw:
            try:
                probe_root = day_root if day_root is not None else inst_root
                collected_now = collect_v02_v03_loop_candidates(probe_root, now_iso=now_iso)
                if (
                    _v02v03_descriptor_match(collected_now, src_raw) is not None
                    or v04_round_event_for_selected_candidate(collected_now, src_raw, now_iso=now_iso) is not None
                ):
                    is_internal_bridge_event = True
            except Exception:
                is_internal_bridge_event = False

    must_verify = (
        require_auth
        or bool(action_id)
        or has_auth_prefix
        or (has_owner_store and not is_internal_bridge_event)
    )
    if must_verify:
        return False, "UNVERIFIED_OWNER_RECEIPT"
    return True, "SYNTHETIC_COMPAT_ACCEPTED"


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

    # Round C2: an owner round that already reported a genuine capability/spot
    # boundary soft-lands here with zero optimistic writes -- never a fake success.
    incoming_reason = str(round_event.get("reason_code") or "").strip().upper()
    if incoming_reason in ("UNSUPPORTED_CAPABILITY", "OBJECT_NOT_AT_EXPECTED_SPOT"):
        return {
            "ok": True,
            "settled": False,
            "soft_landed": True,
            "status": "SOFT_LANDED",
            "reason_code": incoming_reason,
            "effect_state": str(round_event.get("effect_state") or "").strip().upper(),
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

    rcpt_ok, rcpt_reason = _verify_authoritative_owner_receipt_zero_write(
        inst_root, round_event, receipt_ref, day_root=day_root, now_iso=effective_now_iso
    )
    if not rcpt_ok:
        return {
            "ok": False,
            "settled": False,
            "status": "REFUSED",
            "reason_code": rcpt_reason,
            "optimistic_writes": 0,
            "slices_updated": [],
            "llm_calls": 0,
        }

    op_key_raw = str(round_event.get("operation_key") or f"v02v03:{action_kind}:{receipt_ref}").strip()
    op_key = _safe_token(op_key_raw, "op")[:96]
    # 36A §11.1：亲密入场断点的 op key 只由 permit + activity 决定（不随 Decision 变），
    # 所以它的身份哈希也不能包含 decision_id——否则换一个 Decision 重放同一断点会被误判成
    # 不同意图而 CONFLICT，违背"重放消费幂等"。其他动作的 op key 已编码 decision_id，
    # 不受此排除影响。
    intent_exclude = {"expected_revision"}
    if action_kind == "RESUME_INTIMACY_CHECKPOINT":
        intent_exclude.add("decision_id")
    intent_payload = {
        k: v
        for k, v in round_event.items()
        if k not in intent_exclude
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

    # 0. Round C2: one free-form micro-intent -> thin deterministic classifier ->
    #    real v0.8 matter acts.  Unsupported/refused intents soft-land with zero
    #    writes and never fabricate completion.
    v04_action_lower = str(round_event.get("v04_action") or "").strip().lower()
    if action_kind in ("MICRO_INTENT", "V04_MICRO_INTENT") or v04_action_lower == "micro_intent":
        raw_intent = round_event.get("micro_intent")
        if not isinstance(raw_intent, Mapping):
            raw_intent = {
                key: round_event.get(key)
                for key in (
                    "intent_cn", "object_id", "object_ref", "item_id",
                    "target_parent_id", "carrier_id", "container_id", "book_id",
                    "vessel_id", "target_object_id", "target_id", "member_ids",
                    "pattern", "arrangement_id", "anchor_page", "placement_relation",
                    "posture_tag", "zone_id", "association_text", "note_cn", "steps",
                )
                if round_event.get(key) is not None
            }
        micro_result = execute_normalized_micro_intent(
            inst_root,
            raw_intent,
            actor_side="subject",
            now_iso=effective_now_iso,
            operation_key=op_key,
            accept_approximation=bool(round_event.get("accept_approximation")),
        )
        if not micro_result.get("settled"):
            return {
                "ok": bool(micro_result.get("ok")),
                "settled": False,
                "soft_landed": True,
                "status": "SOFT_LANDED",
                "reason_code": str(
                    micro_result.get("reason_code") or "UNSUPPORTED_CAPABILITY"
                ),
                "classification": micro_result.get("classification"),
                "intent_key": micro_result.get("intent_key"),
                "operation_key": op_key,
                "effect_state": effect_state,
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("world.v08_matter_space")
        details["micro_intent"] = {
            "classification": micro_result.get("classification"),
            "intent_key": micro_result.get("intent_key"),
            "intent_cn": micro_result.get("intent_cn"),
            "status": micro_result.get("status"),
            "receipt": copy.deepcopy(micro_result.get("receipt") or {}),
        }

    # 0. v0.8 Wave 2: one real v0.4 neighborhood action (hang/rescue/collect cloth,
    #    plant care, home craft, shared-shelf/bookstore return).  The mutation goes
    #    through the phone view's strict field/actor gate, never by hand-writing
    #    ``v04_neighborhood_state.json``.  A refused command settles nothing.
    v04_command = _v04_build_settlement_command(
        inst_root,
        round_event,
        now_dt=_parse_iso(effective_now_iso) or datetime.now(tz=DEFAULT_TZ),
    )
    if v04_command is not None:
        v04_action, v04_payload = v04_command
        v04_payload = dict(v04_payload)
        v04_payload["action"] = v04_action
        v04_payload["operation_key"] = _v04_operation_key(
            f"{op_key}_{v04_action}"
        )
        if v04_action == "interact_cafe_resident":
            try:
                v04_engine = _v04_engine_module()
                cafe_status, cafe_result, _ = v04_engine.apply_v04_neighborhood_command(
                    inst_root,
                    v04_payload,
                    actor_side="subject",
                    now_iso=effective_now_iso,
                )
            except Exception as exc:
                reason = getattr(exc, "reason_code", None) or "V04_CAFE_RESIDENT_SETTLE_FAILED"
                return {
                    "ok": False,
                    "settled": False,
                    "status": "REFUSED",
                    "reason_code": str(reason),
                    "detail": str(getattr(exc, "detail", None) or exc)[:200],
                    "optimistic_writes": 0,
                    "slices_updated": [],
                    "llm_calls": 0,
                }
            slices_updated.append("v04_neighborhood")
            details["v04_neighborhood"] = {
                "action": v04_action,
                "status": cafe_status,
                "already_applied": cafe_status == "already_applied",
                "result": copy.deepcopy(cafe_result or {}),
                "cue_ref": str(round_event.get("source_ref") or ""),
            }
        else:
            try:
                v04_view = _v04_neighborhood_view_module()
                v04_code, v04_body = v04_view.mutate_v04_neighborhood_view(
                    inst_root,
                    v04_payload,
                    actor_side="subject",
                    now_iso=effective_now_iso,
                )
            except Exception as exc:  # a broken v0.4 surface is a named refusal
                return {
                    "ok": False,
                    "settled": False,
                    "status": "REFUSED",
                    "reason_code": "V04_NEIGHBORHOOD_SETTLE_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                    "optimistic_writes": 0,
                    "slices_updated": [],
                    "llm_calls": 0,
                }
            if v04_code != 200 or not v04_body.get("ok"):
                return {
                    "ok": False,
                    "settled": False,
                    "status": "REFUSED",
                    "reason_code": str(v04_body.get("reason_code") or "V04_NEIGHBORHOOD_REJECTED"),
                    "detail": str(v04_body.get("detail") or "")[:200],
                    "optimistic_writes": 0,
                    "slices_updated": [],
                    "llm_calls": 0,
                }
            slices_updated.append("v04_neighborhood")
            details["v04_neighborhood"] = {
                "action": v04_action,
                "status": v04_body.get("status"),
                "already_applied": bool(v04_body.get("already_applied") is True),
                "result": copy.deepcopy(v04_body.get("result") or {}),
            }

    # 0b. v0.8 Ch.50/Ch.53: shared-notebook reply and prepared-surprise hand-over.
    #    Both settle through the elastic-play view's strict field/actor gate.  A
    #    reply without text, or an offer with no waiting surprise, soft-lands with
    #    zero optimistic writes -- never a fabricated success.
    elastic_command = _v04_elastic_settlement_command(round_event)
    if elastic_command is not None:
        elastic_action, elastic_payload = elastic_command
        if elastic_payload is None:
            return {
                "ok": True,
                "settled": False,
                "soft_landed": True,
                "status": "SOFT_LANDED",
                "reason_code": "ELASTIC_SHARED_NOTEBOOK_TEXT_MISSING",
                "effect_state": effect_state,
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        elastic_payload = dict(elastic_payload)
        elastic_payload["action"] = elastic_action
        elastic_payload["operation_key"] = _v04_operation_key(
            f"{op_key}_{elastic_action}"
        )
        try:
            elastic_view = _v04_elastic_play_view_module()
            elastic_code, elastic_body = elastic_view.mutate_v04_elastic_play_view(
                inst_root,
                elastic_payload,
                actor_side="subject",
                now_iso=effective_now_iso,
            )
        except Exception as exc:  # a broken v0.4 surface is a named refusal
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": "V04_ELASTIC_PLAY_SETTLE_FAILED",
                "detail": f"{type(exc).__name__}: {exc}"[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        if elastic_code == 404 and elastic_action == "prepare_or_offer_surprise":
            return {
                "ok": True,
                "settled": False,
                "soft_landed": True,
                "status": "SOFT_LANDED",
                "reason_code": str(
                    elastic_body.get("reason_code") or "NO_PREPARED_SURPRISE_WAITING"
                ),
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        if elastic_code != 200 or not elastic_body.get("ok"):
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(
                    elastic_body.get("reason_code") or "V04_ELASTIC_PLAY_REJECTED"
                ),
                "detail": str(elastic_body.get("detail") or "")[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v04_elastic_play")
        details["v04_elastic_play"] = {
            "action": elastic_action,
            "status": elastic_body.get("status"),
            "already_applied": bool(elastic_body.get("already_applied") is True),
            "revision": elastic_body.get("revision"),
        }

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

    # 2b. v0.8 Wave 6: the seven 0.2 (v2.0) daily-loop commands settle through their own
    #     owner (tomorrow note, tentative plan, workbench, bookmark, route, plant, CG).
    #     Section 2 above still handles the legacy wave-2/3 work-version events; these
    #     handlers re-read the owner state and are no-ops when it is already current.
    daily_handler = _V02_DAILY_SETTLE_HANDLERS.get(action_kind)
    if daily_handler is not None:
        refusal = daily_handler(
            inst_root,
            life_root,
            round_event,
            now_iso=effective_now_iso,
            slices_updated=slices_updated,
            details=details,
        )
        if refusal is not None:
            return refusal

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
        if action_kind == "WARM_HYDRATION" and isinstance(round_event.get("thirst_relief"), (int, float)):
            try:
                relief_val = float(round_event["thirst_relief"])
                if relief_val > 0.0:
                    ln_res = somatic_comfort_view.apply_lived_needs_relief(
                        inst_root,
                        thirst_relief=relief_val,
                        receipt_id=f"{op_key}_warm_water"[:96],
                        now_iso=effective_now_iso,
                        auto_initialize=False,
                    )
                    if isinstance(ln_res, Mapping) and bool(ln_res.get("applied") or ln_res.get("ok")):
                        slices_updated.append("body.somatic")
                        details["lived_needs_relief"] = ln_res
            except Exception:
                pass

    # 3b. Stage 2 Acute Body-Care & Arbitrated Desire Settlement
    if action_kind == "HANG_OUTERWEAR":
        garment_id = str(round_event.get("garment_id") or "daily_coat").strip()[:64] or "daily_coat"
        hang_pos = str(round_event.get("hang_position") or "entryway_rack").strip()[:64] or "entryway_rack"
        h_code, h_body = somatic_comfort_view.mutate_somatic_comfort_view(
            inst_root,
            {
                "action": "hang_outerwear",
                "garment_id": garment_id,
                "hang_position": hang_pos,
            },
            actor_side="subject",
            now_iso=effective_now_iso,
        )
        if h_code == 200:
            slices_updated.append("body.somatic")
            details["hang_outerwear"] = h_body

    if action_kind == "ACUTE_BODY_CARE_EAT":
        try:
            hunger_rel = float(round_event.get("hunger_relief") or 0.45)
            ln_res = somatic_comfort_view.apply_lived_needs_relief(
                inst_root,
                hunger_relief=hunger_rel,
                receipt_id=f"{op_key}_eat"[:96],
                now_iso=effective_now_iso,
                auto_initialize=False,
            )
            if isinstance(ln_res, Mapping) and bool(ln_res.get("applied") or ln_res.get("ok")):
                slices_updated.append("body.somatic")
                details["lived_needs_relief"] = ln_res
        except Exception:
            pass

    if action_kind == "ACUTE_BODY_CARE_COOL":
        win_state = str(round_event.get("window_state") or "open").strip().lower()
        c_code, c_body = somatic_comfort_view.mutate_somatic_comfort_view(
            inst_root,
            {"action": "set_window", "state": win_state},
            actor_side="subject",
            now_iso=effective_now_iso,
        )
        if c_code == 200:
            slices_updated.append("body.somatic")
            details["cool_down_air"] = c_body

    if action_kind == "PURSUE_AUTONOMOUS_DESIRE":
        d_id = str(round_event.get("desire_id") or "").strip()
        f_reason = str(round_event.get("friction_reason") or "").strip()
        if d_id and f_reason:
            fr_res = record_desire_arbitration_friction(
                inst_root,
                desire_id=d_id,
                friction_reason=f_reason,
                now_iso=effective_now_iso,
            )
            if bool(fr_res.get("updated") or fr_res.get("changed")):
                slices_updated.append("life.v05_inner_spark")
            details["desire_friction"] = fr_res
        elif d_id and round_event.get("fulfill_desire") is True:
            try:
                try:
                    from components.life.scripts import v05_dreams_desires_foreshadow as _spark_settle
                except ImportError:
                    import v05_dreams_desires_foreshadow as _spark_settle  # type: ignore[no-redef]
                res_d = _spark_settle.resolve_subject_desire(
                    inst_root,
                    desire_id=d_id,
                    status="fulfilled",
                    resolution_note=str(round_event.get("resolution_note") or "顺心完成")[:120],
                    now_iso=effective_now_iso,
                    actor_side="subject",
                )
                slices_updated.append("life.v05_inner_spark")
                details["desire_resolved"] = res_d
            except Exception:
                pass

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

    # 5b. GAP-05: Partner Physical Item Gift Decision -> v03_economy_illness_bridge.decide_item_gift
    raw_source_ref = str(round_event.get("source_ref") or "").strip()
    if action_kind in ("DECIDE_ITEM_GIFT", "ACCEPT_ITEM_GIFT", "DECLINE_ITEM_GIFT") or raw_source_ref.startswith(
        "v03_item_gift:"
    ):
        raw_gift_id = str(round_event.get("gift_id") or _v02_ref_suffix(raw_source_ref) or "").strip()
        if not raw_gift_id:
            return _v02_refuse("GIFT_ID_REQUIRED")
        eco_doc = _read_optional_json_zero_write(inst_root / "phone" / v03_eco_bridge.STATE_FILENAME)
        if not isinstance(eco_doc, Mapping):
            eco_doc = _read_optional_json_zero_write(inst_root / "phone" / "v03_ecology_state.json")
        stored_gift_ids: list[str] = []
        if isinstance(eco_doc, Mapping):
            eco_sec = eco_doc.get("economy") if isinstance(eco_doc.get("economy"), Mapping) else {}
            raw_esc = eco_sec.get("item_gift_escrow")
            if isinstance(raw_esc, Mapping):
                stored_gift_ids = [str(k) for k in raw_esc.keys()]
            elif isinstance(raw_esc, list):
                stored_gift_ids = [
                    str(r.get("gift_id")) for r in raw_esc if isinstance(r, Mapping) and r.get("gift_id")
                ]
        gift_id = _v02_match_stored_id(raw_gift_id, stored_gift_ids) or raw_gift_id
        if action_kind == "DECLINE_ITEM_GIFT":
            gift_decision = "decline"
        else:
            gift_decision = str(
                round_event.get("gift_decision") or round_event.get("decision") or "accept"
            ).strip().lower()
        gift_note = round_event.get("note") if isinstance(round_event.get("note"), str) else None
        try:
            gift_res = v03_eco_bridge.decide_item_gift(
                inst_root,
                gift_id=gift_id,
                decision=gift_decision,
                note=gift_note,
                operation_key=f"{op_key}_gift"[:96],
                now_iso=effective_now_iso,
            )
        except v03_eco_bridge.V03EcologyError as exc:
            return _v02_refuse(exc.reason_code, detail=exc.details)
        except Exception as exc:
            return _v02_refuse("ITEM_GIFT_DECIDE_FAILED", detail=f"{type(exc).__name__}: {exc}")
        gift_rcpt = gift_res.get("receipt") if isinstance(gift_res.get("receipt"), Mapping) else {}
        slices_updated.append("v03_ecology.item_gift_escrow")
        if gift_decision == "accept" or gift_rcpt.get("materialized_object_id"):
            slices_updated.append("world.v08_matter_space")
        details["item_gift"] = {
            "gift_id": gift_id,
            "decision": gift_decision,
            "status": gift_rcpt.get("gift_status") or gift_res.get("status"),
            "materialized_object_id": gift_rcpt.get("materialized_object_id"),
            "receipt": copy.deepcopy(dict(gift_rcpt)),
        }

    # 5c. Ch.10 §1.2: Matured 72h Slow-Letter Draft -> window_keepsake_view.post_drafted_slow_letter
    if action_kind in ("POST_SLOW_LETTER_DRAFT", "POST_DRAFTED_SLOW_LETTER") or raw_source_ref.startswith(
        "v03_slow_letter_draft:"
    ):
        raw_draft_id = str(round_event.get("draft_id") or _v02_ref_suffix(raw_source_ref) or "").strip()
        if not raw_draft_id:
            return _v02_refuse("DRAFT_ID_REQUIRED")
        wk_doc = _read_optional_json_zero_write(inst_root / "phone" / "window_keepsakes.json")
        subj_draft_ids: list[str] = []
        if isinstance(wk_doc, Mapping) and isinstance(wk_doc.get("slow_letter_drafts"), list):
            for d_row in wk_doc["slow_letter_drafts"]:
                if isinstance(d_row, Mapping) and str(d_row.get("author_side") or "").strip().lower() == "subject":
                    if d_row.get("draft_id"):
                        subj_draft_ids.append(str(d_row["draft_id"]))
        draft_id = _v02_match_stored_id(raw_draft_id, subj_draft_ids) or raw_draft_id
        try:
            post_res = window_keepsake_view.post_drafted_slow_letter(
                inst_root,
                actor_side="subject",
                draft_id=draft_id,
                delay_hours=(
                    int(round_event["delay_hours"])
                    if isinstance(round_event.get("delay_hours"), int)
                    and not isinstance(round_event.get("delay_hours"), bool)
                    else None
                ),
                postmark_place=(
                    str(round_event["postmark_place"])
                    if isinstance(round_event.get("postmark_place"), str)
                    and round_event["postmark_place"].strip()
                    else None
                ),
                now=effective_now_iso,
                operation_key=f"{op_key}_slow_post"[:96],
            )
        except window_keepsake_view.SlowLetterCoolingError as exc:
            return _v02_refuse(
                "SLOW_LETTER_COOLING_PERIOD",
                detail=str(exc),
                cools_until=exc.cools_until,
                remaining_cooling_seconds=exc.remaining_cooling_seconds,
            )
        except KeyError as exc:
            return _v02_refuse("DRAFT_NOT_FOUND", detail=str(exc))
        except ValueError as exc:
            return _v02_refuse(str(exc) or "SLOW_LETTER_POST_INVALID")
        except Exception as exc:
            return _v02_refuse("SLOW_LETTER_POST_FAILED", detail=f"{type(exc).__name__}: {exc}")
        slices_updated.append("window_keepsakes.slow_letters")
        details["slow_letter_post"] = copy.deepcopy(dict(post_res))

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

    # 6b. 36A §11.1 / 36B-4 §9: resume one adult-intimacy entry checkpoint (idempotent).
    if action_kind == "RESUME_INTIMACY_CHECKPOINT":
        permit_id = str(round_event.get("permit_id") or "").strip()
        activity_ref = str(round_event.get("activity_ref") or "").strip()
        if not permit_id:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": "INTIMACY_PERMIT_ID_REQUIRED",
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        try:
            consumed = adult_intimacy_view.consume_resumable_checkpoint(
                inst_root,
                permit_id=permit_id,
                now=_parse_iso(effective_now_iso),
                decision_id=str(round_event.get("decision_id") or "") or None,
            )
        except Exception as exc:  # a broken intimacy surface is a named refusal
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": "INTIMACY_RESUME_FAILED",
                "detail": f"{type(exc).__name__}: {exc}"[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        if not consumed.get("ok"):
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(consumed.get("reason_code") or "INTIMACY_RESUME_REFUSED"),
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("phone.adult_intimacy")
        details["intimacy_resumes"] = {
            "permit_id": permit_id,
            "resumed_activity_ref": consumed.get("activity_ref") or activity_ref,
            "resumed_at": consumed.get("resumed_at"),
            "already_consumed": bool(consumed.get("already_consumed")),
            "decision_id": consumed.get("decision_id"),
        }

    # 7. Cross-period / Overnight Clock Advance -> v03_clock_evaporation + active illness evolution
    if action_kind in ("ADVANCE_CLOCK", "CROSS_NIGHT_ADVANCE") or round_event.get("advance_clock_to_iso"):
        target_clock_iso = str(
            round_event.get("advance_clock_to_iso")
            or round_event.get("target_now_iso")
            or effective_now_iso
        ).strip()
        # v0.8 Wave 2: settle the real v0.4 piecewise segments up to the target instant
        # (sun evaporation, rain wetting/pause, plant water loss).  Uninitialized
        # neighborhoods are a zero-write no-op; a v0.4 fault never breaks the clock.
        v04_advance = _advance_v04_neighborhood_processes_zero_write(
            inst_root, target_clock_iso
        )
        details["v04_process_advance"] = v04_advance
        if v04_advance.get("advanced"):
            slices_updated.append("v04_neighborhood.processes")
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

    # 8. F-101: Second-order rule deferral enforcement receipts -> v02_agency_depth.rule_enforcements
    raw_constrained_list = round_event.get("second_order_constrained_candidates")
    deferred_rule_id = str(round_event.get("second_order_deferred_rule_id") or "").strip()
    enforcement_specs: list[dict[str, str]] = []
    seen_enf_pairs: set[tuple[str, str]] = set()
    if deferred_rule_id:
        c_ref = str(
            round_event.get("second_order_deferred_candidate_ref")
            or round_event.get("source_ref")
            or round_event.get("candidate_id")
            or action_kind
            or "candidate"
        ).strip()[:200]
        seen_enf_pairs.add((deferred_rule_id[:96], c_ref))
        enforcement_specs.append({
            "rule_id": deferred_rule_id[:96],
            "rule_title": str(
                round_event.get("second_order_deferred_rule_title") or deferred_rule_id
            ).strip()[:160],
            "candidate_ref": c_ref,
            "candidate_title": str(
                round_event.get("second_order_deferred_candidate_title")
                or round_event.get("title")
                or round_event.get("title_cn")
                or action_kind
                or "candidate"
            ).strip()[:200],
            "effect": (
                str(round_event.get("second_order_enforcement_effect") or "DEFERRED").strip().upper()[:40]
                or "DEFERRED"
            ),
            "reason_cn": str(
                round_event.get("second_order_deferred_reason_cn") or "受二阶小规矩约束延后"
            ).strip()[:300],
        })
    if isinstance(raw_constrained_list, list):
        for c_item in raw_constrained_list[:6]:
            if not isinstance(c_item, Mapping):
                continue
            r_id = str(c_item.get("rule_id") or "").strip()[:96]
            if not r_id:
                continue
            c_ref = str(
                c_item.get("candidate_ref")
                or c_item.get("source_ref")
                or c_item.get("candidate_id")
                or action_kind
                or "candidate"
            ).strip()[:200]
            if (r_id, c_ref) in seen_enf_pairs:
                continue
            seen_enf_pairs.add((r_id, c_ref))
            enforcement_specs.append({
                "rule_id": r_id,
                "rule_title": str(c_item.get("rule_title") or r_id).strip()[:160],
                "candidate_ref": c_ref,
                "candidate_title": str(
                    c_item.get("candidate_title")
                    or c_item.get("title")
                    or c_ref
                ).strip()[:200],
                "effect": str(c_item.get("effect") or "DEFERRED").strip().upper()[:40] or "DEFERRED",
                "reason_cn": str(c_item.get("reason_cn") or "受二阶小规矩约束延后").strip()[:300],
            })
    if enforcement_specs:
        try:
            try:
                from components.life.scripts import v02_agency_depth_engine as _ad_enf
            except ImportError:  # pragma: no cover - flat life/scripts import path
                import v02_agency_depth_engine as _ad_enf  # type: ignore[no-redef]
            enf_entries: list[dict[str, Any]] = []
            for spec in enforcement_specs:
                enf_entries.append(
                    _ad_enf.record_second_order_enforcement(
                        inst_root,
                        rule_id=spec["rule_id"],
                        rule_title=spec["rule_title"],
                        candidate_ref=spec["candidate_ref"],
                        candidate_title=spec["candidate_title"],
                        effect=spec["effect"],
                        reason_cn=spec["reason_cn"],
                        now_iso=effective_now_iso,
                    )
                )
            if enf_entries:
                slices_updated.append("v02_agency_depth.rule_enforcements")
                details["second_order_enforcement"] = enf_entries[0]
                details["second_order_enforcements"] = enf_entries
        except Exception:
            pass

    if action_kind in ("CONFIRM_RESCALED_COPRACTICE", "CONSIDER_RESCALED_COPRACTICE"):
        practice_id = str(round_event.get("practice_id") or "").strip()
        if practice_id:
            try:
                from components.phone import v02_honest_ux_view as _hux_settle
                cop_payload: dict[str, Any] = {
                    "action": "confirm_rescaled_copractice",
                    "practice_id": practice_id,
                    "scale_mode": str(round_event.get("scale_mode") or "light_relay"),
                    "confirmed": bool(round_event.get("confirmed", True)),
                    "operation_key": f"op_cop_{op_key}"[:120],
                }
                c_code, c_body = _hux_settle.mutate_v02_honest_ux_view(
                    inst_root,
                    cop_payload,
                    actor_side="subject",
                    now_iso=effective_now_iso,
                )
                if c_code == 200 and c_body.get("ok"):
                    slices_updated.append("v02_honest_ux.rescaled_copractices")
                    details["rescaled_copractice"] = c_body.get("data") or {}
            except Exception:
                pass

    # 9. v0.9 Living Agency & Evolving World receipt-gated settlement + lived_memory_sink
    #    forwarding (Blueprint §6.3: only reached after effect_state == "APPLIED" & verified receipt).
    v09_fam = str(round_event.get("action_family") or "").strip().lower()
    if (
        action_kind in ("PICKUP_ARRIVED_COMMITMENT", "CONTACT_NPC_OR_VENUE", "FULFILL_SCHEDULED_APPOINTMENT")
        or v09_fam == "pickup_arrived_commitment"
    ):
        try:
            try:
                from components.world.scripts import world_commitments_engine as _wce_s
            except ImportError:
                import world_commitments_engine as _wce_s  # type: ignore[no-redef]
            v09_venue = str(round_event.get("venue_id") or "bookstore").strip() or "bookstore"
            v09_cid = str(round_event.get("commitment_id") or "").strip() or None
            v09_npc = str(round_event.get("npc_id") or "").strip() or None
            v09_cact = str(
                round_event.get("contact_action")
                or (
                    "fulfill_appointment"
                    if action_kind == "FULFILL_SCHEDULED_APPOINTMENT"
                    else "pickup_reservation"
                )
            ).strip()
            cmt_res = _wce_s.contact_npc_or_venue(
                inst_root,
                venue_id=v09_venue,
                npc_id=v09_npc,
                contact_action=v09_cact,
                commitment_id=v09_cid,
                actor_side="subject",
                operation_key=f"op_v09_cmt_{op_key}"[:96],
                now_iso=effective_now_iso,
            )
        except Exception as exc:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(getattr(exc, "reason_code", "") or "V09_COMMITMENT_SETTLE_FAILED"),
                "detail": str(exc)[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v09_world_commitments")
        details["v09_commitment"] = cmt_res
        cmt_obj = cmt_res.get("commitment") if isinstance(cmt_res.get("commitment"), Mapping) else {}
        cmt_id_eff = str(cmt_obj.get("commitment_id") or v09_cid or op_key)
        cmt_title = str(cmt_obj.get("title_cn") or round_event.get("title_cn") or "街坊约定").strip()[:100]
        cmt_state = str(cmt_obj.get("state") or "PICKED_UP")
        try:
            try:
                from components.life.scripts import lived_memory_sink as _v09_lms
            except ImportError:
                import lived_memory_sink as _v09_lms  # type: ignore[no-redef]
            sink_res = _v09_lms.emit_lived_episode_safe(
                inst_root,
                _v09_lms.LivedEpisodeEvent(
                    episode_key=f"v09_commitment:{cmt_id_eff}:{cmt_state}",
                    domain="v09_commitment",
                    topic=f"街坊约定：{cmt_title}"[:100],
                    exact_detail=f"到{v09_venue}完成{v09_cact}：「{cmt_title}」（状态：{cmt_state}）"[:300],
                    gist_summary=f"街坊约定「{cmt_title}」已落定（{cmt_state}）"[:180],
                    occurred_at=effective_now_iso,
                    emotional_salience=0.52,
                    affect_kind="affect_pulse",
                    affect_intensity=40,
                    shared=True,
                    source_refs=(
                        ("commitment_id", cmt_id_eff),
                        ("venue_id", v09_venue),
                        ("receipt_ref", receipt_ref),
                    ),
                ),
                now_iso=effective_now_iso,
            )
            details["lived_memory_sink"] = sink_res
        except Exception:
            pass

    elif action_kind == "RECORD_V09_COMMITMENT_MILESTONE":
        cmt_id_eff = str(round_event.get("commitment_id") or op_key).strip()
        cmt_title = str(round_event.get("title_cn") or "街坊约定").strip()[:100]
        cmt_state = str(round_event.get("commitment_state") or "MISSED_BY_SUBJECT").strip().upper()
        cmt_detail = str(round_event.get("summary_cn") or f"街坊约定「{cmt_title}」记录为 {cmt_state}").strip()[:300]
        slices_updated.append("v09_world_commitments.milestone")
        details["v09_commitment_milestone"] = {
            "commitment_id": cmt_id_eff,
            "state": cmt_state,
            "title_cn": cmt_title,
        }
        try:
            try:
                from components.life.scripts import lived_memory_sink as _v09_lms
            except ImportError:
                import lived_memory_sink as _v09_lms  # type: ignore[no-redef]
            sink_res = _v09_lms.emit_lived_episode_safe(
                inst_root,
                _v09_lms.LivedEpisodeEvent(
                    episode_key=f"v09_commitment:{cmt_id_eff}:{cmt_state}",
                    domain="v09_commitment",
                    topic=f"街坊约定：{cmt_title}"[:100],
                    exact_detail=cmt_detail,
                    gist_summary=cmt_detail[:180],
                    occurred_at=effective_now_iso,
                    emotional_salience=0.50,
                    affect_kind="affect_pulse",
                    affect_intensity=38,
                    shared=True,
                    source_refs=(("commitment_id", cmt_id_eff), ("receipt_ref", receipt_ref)),
                ),
                now_iso=effective_now_iso,
            )
            details["lived_memory_sink"] = sink_res
        except Exception:
            pass

    elif (
        action_kind in ("COMMIT_BLIND_CHOICE", "RESOLVE_BLIND_CHOICE")
        or v09_fam == "commit_blind_choice"
    ):
        try:
            try:
                from components.life.scripts import v09_true_stance_engine as _tse_s
            except ImportError:
                import v09_true_stance_engine as _tse_s  # type: ignore[no-redef]
            is_resolve = action_kind == "RESOLVE_BLIND_CHOICE"
            if is_resolve:
                cmd_payload: dict[str, Any] = {
                    "action": "resolve_blind_choice",
                    "choice_id": round_event.get("choice_id"),
                    "resolution_mode": round_event.get("resolution_mode") or "ACCEPT_CHOICE_KEEP_REASONS",
                    "operation_key": f"op_v09_bc_{op_key}"[:96],
                }
                for opt_k in (
                    "resolution_note_cn",
                    "adopted_action_choice_id",
                    "preferred_judgment_choice_id",
                    "compromise_option_id",
                    "compromise_label_cn",
                    "tradeoff_note_cn",
                    "retained_reservations",
                    "new_evidence",
                    "what_changed_mind_cn",
                    "new_choice_id",
                ):
                    if round_event.get(opt_k) is not None:
                        cmd_payload[opt_k] = round_event[opt_k]
            else:
                cmd_payload = {
                    "action": "subject_commit_blind_choice",
                    "choice_id": round_event.get("choice_id"),
                    "selected_choice_id": round_event.get("selected_choice_id") or "UNDECIDED",
                    "operation_key": f"op_v09_bc_{op_key}"[:96],
                }
                if round_event.get("reasons") is not None:
                    cmd_payload["reasons"] = round_event["reasons"]
            bc_res = _tse_s.apply_true_stance_command(
                inst_root,
                cmd_payload,
                actor_side="subject",
                now_iso=effective_now_iso,
            )
        except Exception as exc:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(getattr(exc, "reason_code", "") or "V09_BLIND_CHOICE_SETTLE_FAILED"),
                "detail": str(exc)[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v09_true_stance.blind_choices")
        details["v09_blind_choice"] = bc_res
        bc_obj = bc_res.get("blind_choice") if isinstance(bc_res.get("blind_choice"), Mapping) else {}
        bc_id = str(bc_res.get("choice_id") or round_event.get("choice_id") or op_key)
        bc_topic = str(bc_obj.get("topic_cn") or round_event.get("title_cn") or "共同选择").strip()[:100]
        bc_res_list = bc_obj.get("retained_reservations") if isinstance(bc_obj.get("retained_reservations"), list) else []
        if is_resolve or bc_res_list:
            res_texts = "、".join(
                str(r.get("text_cn") or "").strip()
                for r in bc_res_list[:3]
                if isinstance(r, Mapping) and str(r.get("text_cn") or "").strip()
            )
            bc_mode = str(bc_obj.get("resolution_mode") or "RESOLVED")
            bc_detail = (
                f"共同选择〈{bc_topic}〉落定（{bc_mode}），保留顾虑：{res_texts}"
                if res_texts
                else f"共同选择〈{bc_topic}〉落定（{bc_mode}）"
            )
            try:
                try:
                    from components.life.scripts import lived_memory_sink as _v09_lms
                except ImportError:
                    import lived_memory_sink as _v09_lms  # type: ignore[no-redef]
                sink_res = _v09_lms.emit_lived_episode_safe(
                    inst_root,
                    _v09_lms.LivedEpisodeEvent(
                        episode_key=f"v09_stance:choice:{bc_id}:{bc_mode}",
                        domain="v09_stance",
                        topic=f"共同选择：{bc_topic}"[:100],
                        exact_detail=bc_detail[:300],
                        gist_summary=bc_detail[:180],
                        occurred_at=effective_now_iso,
                        emotional_salience=0.55,
                        affect_kind="affect_pulse",
                        affect_intensity=42,
                        shared=True,
                        source_refs=(("choice_id", bc_id), ("receipt_ref", receipt_ref)),
                    ),
                    now_iso=effective_now_iso,
                )
                details["lived_memory_sink"] = sink_res
            except Exception:
                pass

    elif (
        action_kind in ("REFLECT_UNFORMED_STANCE", "ADVANCE_GROWTH_STANCE", "EVALUATE_PARTNER_PERSUASION")
        or v09_fam == "reflect_unformed_stance"
    ):
        try:
            try:
                from components.life.scripts import v09_true_stance_engine as _tse_s
            except ImportError:
                import v09_true_stance_engine as _tse_s  # type: ignore[no-redef]
            if action_kind == "EVALUATE_PARTNER_PERSUASION":
                st_payload: dict[str, Any] = {
                    "action": "evaluate_partner_persuasion",
                    "stance_id": round_event.get("stance_id"),
                    "persuasion_verdict": round_event.get("persuasion_verdict") or "PARTIALLY_TAKEN_KEEP_DOUBT",
                    "operation_key": f"op_v09_st_{op_key}"[:96],
                }
                for opt_k in ("persuasion_id", "subject_response_cn", "kept_doubt_cn", "stance_summary_cn"):
                    if round_event.get(opt_k) is not None:
                        st_payload[opt_k] = round_event[opt_k]
            else:
                prov = str(round_event.get("provenance_tier") or "firsthand_lived").strip().lower()
                st_payload = {
                    "action": "advance_stance_from_experience",
                    "stance_id": round_event.get("stance_id"),
                    "target_status": round_event.get("target_status") or (
                        "TENTATIVE" if prov in ("tentative_guess", "partner_heard") else "FORMED"
                    ),
                    "provenance_tier": prov,
                    "stance_summary_cn": round_event.get("stance_summary_cn") or round_event.get("summary_cn") or "结合经历有了自己的看法",
                    "operation_key": f"op_v09_st_{op_key}"[:96],
                }
                if prov == "firsthand_lived":
                    st_payload["receipt_ref"] = str(
                        round_event.get("evidence_receipt_ref") or receipt_ref
                    )
                elif prov == "read_knowledge":
                    st_payload["material_ref"] = str(
                        round_event.get("material_ref") or round_event.get("matched_evidence_ref") or receipt_ref
                    )
                for opt_k in (
                    "change_reason_cn",
                    "page_or_anchor",
                    "reasons",
                    "doubts_cn",
                    "replaces_stance_id",
                    "shared_with_partner",
                ):
                    if round_event.get(opt_k) is not None:
                        st_payload[opt_k] = round_event[opt_k]
            st_res = _tse_s.apply_true_stance_command(
                inst_root,
                st_payload,
                actor_side="subject",
                now_iso=effective_now_iso,
            )
        except Exception as exc:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(getattr(exc, "reason_code", "") or "V09_STANCE_SETTLE_FAILED"),
                "detail": str(exc)[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v09_true_stance.growth_stances")
        details["v09_growth_stance"] = st_res
        st_obj = st_res.get("growth_stance") if isinstance(st_res.get("growth_stance"), Mapping) else {}
        st_id = str(st_res.get("stance_id") or round_event.get("stance_id") or op_key)
        st_topic = str(st_obj.get("topic_cn") or round_event.get("title_cn") or "成长主见").strip()[:100]
        st_status = str(st_obj.get("status") or st_res.get("status") or "TENTATIVE")
        st_ver = int(st_obj.get("version") or 1)
        st_sum = str(st_obj.get("stance_summary_cn") or "").strip()
        if st_status in ("TENTATIVE", "FORMED", "REVISED"):
            try:
                try:
                    from components.life.scripts import lived_memory_sink as _v09_lms
                except ImportError:
                    import lived_memory_sink as _v09_lms  # type: ignore[no-redef]
                sink_res = _v09_lms.emit_lived_episode_safe(
                    inst_root,
                    _v09_lms.LivedEpisodeEvent(
                        episode_key=f"v09_stance:{st_id}:v{st_ver}:{st_status}",
                        domain="v09_stance",
                        topic=f"主见形成：{st_topic}"[:100],
                        exact_detail=f"关于〈{st_topic}〉形成{st_status}看法：{st_sum}"[:300],
                        gist_summary=f"〈{st_topic}〉({st_status})：{st_sum}"[:180],
                        occurred_at=effective_now_iso,
                        emotional_salience=0.54 if st_status in ("FORMED", "REVISED") else 0.45,
                        affect_kind="affect_pulse",
                        affect_intensity=42 if st_status in ("FORMED", "REVISED") else 34,
                        shared=bool(st_obj.get("shared_with_partner", False)),
                        source_refs=(("stance_id", st_id), ("receipt_ref", receipt_ref)),
                    ),
                    now_iso=effective_now_iso,
                )
                details["lived_memory_sink"] = sink_res
            except Exception:
                pass

    elif (
        action_kind in ("TIDY_CUSTOM_SHELF_OR_TIN", "PERSONAL_OBJECT_TAXONOMY_ACT")
        or v09_fam == "tidy_custom_shelf_or_tin"
    ):
        try:
            try:
                from components.world.scripts import personal_objects_taxonomy as _pot_s
            except ImportError:
                import personal_objects_taxonomy as _pot_s  # type: ignore[no-redef]
            raw_tax_cmd = round_event.get("taxonomy_command")
            if isinstance(raw_tax_cmd, Mapping):
                tax_cmd = dict(raw_tax_cmd)
                tax_cmd.setdefault("operation_key", f"op_v09_tax_{op_key}"[:96])
            else:
                tax_action = str(round_event.get("taxonomy_action") or "attach_label_to_object").strip()
                tax_cmd = {
                    "action": tax_action,
                    "operation_key": f"op_v09_tax_{op_key}"[:96],
                }
                for tk in (
                    "object_id",
                    "object_kind",
                    "base_name_cn",
                    "custom_label_cn",
                    "item_label_cn",
                    "item_kind",
                    "note_cn",
                    "category_id",
                    "title_cn",
                    "scope_kinds",
                    "description_cn",
                    "target_kind",
                    "target_ref",
                    "target_title_cn",
                    "annotation_cn",
                    "reason_for_move_cn",
                    "visibility",
                ):
                    if round_event.get(tk) is not None:
                        tax_cmd[tk] = round_event[tk]
                if tax_action == "attach_label_to_object" and not tax_cmd.get("object_id") and round_event.get("target_id"):
                    tax_cmd["object_id"] = str(round_event["target_id"])
                if tax_action == "attach_label_to_object" and not tax_cmd.get("custom_label_cn"):
                    tax_cmd["custom_label_cn"] = str(round_event.get("label_cn") or "桌角收纳盒")
            tax_res = _pot_s.apply_personal_objects_taxonomy_command(
                inst_root,
                tax_cmd,
                actor_side="subject",
                now_iso=effective_now_iso,
            )
        except Exception as exc:
            return {
                "ok": False,
                "settled": False,
                "status": "REFUSED",
                "reason_code": str(getattr(exc, "reason_code", "") or "V09_TAXONOMY_SETTLE_FAILED"),
                "detail": str(exc)[:200],
                "optimistic_writes": 0,
                "slices_updated": [],
                "llm_calls": 0,
            }
        slices_updated.append("v09_personal_objects_taxonomy")
        details["v09_taxonomy"] = tax_res

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

    if result_doc.get("settled") is True and result_doc.get("slices_updated"):
        _best_effort_feed_recall_and_autobiography(
            inst_root,
            round_event=round_event,
            details=details,
            slices_updated=result_doc["slices_updated"],
            op_key=op_key,
            action_kind=action_kind,
            settled_at=effective_now_iso,
        )
    if result_doc.get("settled") is True:
        _best_effort_feed_v02_autonomous_producers(
            inst_root,
            round_event=round_event,
            details=details,
            slices_updated=result_doc["slices_updated"],
            op_key=op_key,
            action_kind=action_kind,
            settled_at=effective_now_iso,
        )

    return result_doc


def _best_effort_feed_recall_and_autobiography(
    inst_root: Path,
    *,
    round_event: Mapping[str, Any],
    details: Mapping[str, Any],
    slices_updated: Sequence[str],
    op_key: str,
    action_kind: str,
    settled_at: str,
) -> None:
    """Populate episodic recall & autobiographical episode from settled life rounds."""
    try:
        private_slices = {"managed_artifact", "workbench_tray", "phone.adult_intimacy"}
        raw_vis = str(round_event.get("visibility") or "").strip().lower()
        if (
            round_event.get("private") is True
            or round_event.get("shared") is False
            or raw_vis == "private"
            or (bool(set(slices_updated) & private_slices) and round_event.get("shared") is not True and raw_vis != "shared")
        ):
            vis = "private"
        else:
            vis = "shared"

        topic = str(
            round_event.get("topic")
            or round_event.get("title_cn")
            or round_event.get("tray_note")
            or (details.get("workbench") or {}).get("note")
            or action_kind
            or "daily_life_round"
        ).strip()[:80] or "daily_life_round"
        summary = str(
            round_event.get("summary_cn")
            or round_event.get("note_cn")
            or round_event.get("tray_note")
            or f"完成日常回合：{topic}（涉及 {', '.join(slices_updated[:4])}）"
        ).strip()[:240]
        clean_op = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(op_key or "")).strip("_")[:32] or "round"
        period_id = settled_at[:7] if len(settled_at) >= 7 and settled_at[4] == "-" else "2026-11"
        domain = str(slices_updated[0] if slices_updated else (action_kind or "daily_life"))[:80]

        try:
            from components.life.scripts import v02_recall_projection as _rp
        except ImportError:
            import v02_recall_projection as _rp  # type: ignore[no-redef]
        _rp.record_episodic_trace(
            inst_root,
            memory_id=f"ep_settled_{clean_op}",
            topic=topic,
            exact_detail=summary,
            gist_summary=summary,
            occurred_at=settled_at,
            emotional_salience=0.45,
            shared=(vis == "shared"),
            visibility=vis,
            now_iso=settled_at,
            has_subject_capability=True,
        )

        try:
            from components.life.scripts import v02_autobiography_skill_provenance as _asp
        except ImportError:
            import v02_autobiography_skill_provenance as _asp  # type: ignore[no-redef]
        _asp.record_autobiographical_episode(
            inst_root,
            {
                "operation_key": f"op_bio_{clean_op}",
                "episode_id": f"bio_{clean_op}",
                "period_id": period_id,
                "domain": domain,
                "title": topic,
                "summary": summary,
                "outcome": "succeeded",
                "visibility": vis,
                "occurred_at": settled_at,
                "source_ref": f"settled_round:{op_key}",
            },
            now_iso=settled_at,
        )
    except Exception:
        return


_IDLE_NOOP_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "",
        "IDLE",
        "NOOP",
        "NO_OP",
        "WAIT",
        "POLL",
        "HEARTBEAT",
        "CHECK_CLOCK",
        "NOTHING_TO_DO",
        "DORMANT_CONSERVATION_IDLE",
        "QUIET_IDLE",
    }
)

_NOTABLE_DAILY_ROUND_ACTIONS: frozenset[str] = frozenset(
    {
        # Rain walk / outdoor move / outing
        "STRENUOUS_OUTING_WALK",
        "OUTING_VISIT",
        "MOVE_PLACE",
        "STAY_OUTSIDE",
        "VISIT_CAFE_RESIDENT",
        "INTERACT_CAFE_RESIDENT",
        "RETURN_NEIGHBORHOOD_LOAN",
        # Self-care / illness recovery
        "REST",
        "ILLNESS_REST",
        "BED_REST",
        "WARM_HYDRATION",
        "RESOLVE_GARMENT_CARE",
        "USE_CYCLE_SUPPLY",
        "ADJUST_MICROZONE",
        # Craft / workbench / inquiry / reading / social / gift rounds
        "ADVANCE_WORKBENCH",
        "COMMIT_WORK_VERSION",
        "ADVANCE_HOME_CRAFT",
        "COMPLETE_HOME_CRAFT",
        "FINISH_CRAFT_WORK",
        "COMPLETE_INDEPENDENT_WORK",
        "INDEPENDENT_WORK",
        "ADVANCE_BOUNDED_INQUIRY",
        "REVISIT_FUTURE_QUESTION",
        "SETTLE_SPARK_ATTEMPT",
        "FOLLOW_BOOKSTORE_BOOKMARK",
        "OPEN_SHARED_MATERIAL",
        "OBSERVE_PLANT_JOURNAL",
        "CARE_PORCH_PLANT",
        "POST_SLOW_LETTER_DRAFT",
        "POST_DRAFTED_SLOW_LETTER",
        "CLARIFY_COMMON_GROUND",
        "ADOPT_TENTATIVE_PLAN",
        "ELASTIC_SHARED_NOTEBOOK",
        "ELASTIC_OFFER_SURPRISE",
        "DECIDE_ITEM_GIFT",
        "ACCEPT_ITEM_GIFT",
        "OPEN_MATTER_ACT",
        "OPEN_LIVING_TRACE",
        "RUN_PERSONAL_ROUTINE",
        "RESOLVE_TENSION",
        "EXPRESS_MISSING",
        "SHARE_HELD_THREAD",
    }
)

_NOTABLE_DAILY_ROUND_FAMILIES: frozenset[str] = frozenset(
    {
        "strenuous_outing_walk",
        "outing_visit",
        "illness_rest_recovery",
        "resolve_garment_care",
        "use_cycle_supply",
        "adjust_microzone",
        "advance_workbench_draft",
        "advance_home_craft",
        "independent_work",
        "advance_bounded_inquiry",
        "revisit_future_question",
        "settle_spark_attempt",
        "follow_bookstore_bookmark",
        "open_shared_material",
        "observe_plant_journal",
        "courtyard_care",
        "post_matured_slow_letter_draft",
        "clarify_common_ground",
        "adopt_tentative_plan",
        "elastic_shared_notebook",
        "elastic_offer_surprise",
        "decide_partner_item_gift",
        "compose_open_act",
        "run_personal_routine",
    }
)


def _is_notable_daily_round_for_memory_sink(
    action_kind: str,
    action_family: str,
    v04_action: str,
    slice_set: set[str],
    round_event: Mapping[str, Any],
) -> bool:
    ak = str(action_kind or "").strip().upper()
    fam = str(action_family or "").strip().lower()
    v04_a = str(v04_action or "").strip().lower()
    if bool(round_event.get("idle_noop")) or (ak in _IDLE_NOOP_ACTION_KINDS and not fam and not v04_a):
        return False
    if ak in _NOTABLE_DAILY_ROUND_ACTIONS or fam in _NOTABLE_DAILY_ROUND_FAMILIES:
        return True
    if v04_a in ("start_journey", "advance_or_turnback_journey", "interact_cafe_resident", "advance_home_craft", "craft_paper_or_box"):
        return True
    if bool(slice_set & {"v03_ecology.illness", "body.somatic", "v08_composable_body", "workbench_tray", "managed_artifact"}):
        return True
    return False


def _best_effort_feed_v02_autonomous_producers(
    inst_root: Path,
    *,
    round_event: Mapping[str, Any],
    details: Mapping[str, Any],
    slices_updated: Sequence[str],
    op_key: str,
    action_kind: str,
    settled_at: str,
) -> None:
    """Soft-wrapped autonomous v0.2 producers triggered strictly after V-A APPLIED settlement.

    Each producer has its own exception boundary so a fault in any secondary surface can
    never break core settlement or leak private slices into shared partner views.
    """
    private_slices = {"managed_artifact", "workbench_tray", "phone.adult_intimacy"}
    raw_vis = str(round_event.get("visibility") or "").strip().lower()
    is_private_round = (
        round_event.get("private") is True
        or round_event.get("shared") is False
        or raw_vis == "private"
        or (
            bool(set(slices_updated) & private_slices)
            and round_event.get("shared") is not True
            and raw_vis != "shared"
        )
    )
    is_shared_round = not is_private_round

    ak = str(action_kind or "").strip().upper()
    fam = str(round_event.get("action_family") or "").strip().lower()
    v04_act = str(round_event.get("v04_action") or "").strip().lower()
    slice_set = {str(s) for s in (slices_updated or ())}
    clean_op = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(op_key or "")).strip("_")[:36] or "round"
    period_id = settled_at[:7] if len(settled_at) >= 7 and settled_at[4] == "-" else "2026-11"
    day_tok = settled_at[:10].replace("-", "") if len(settled_at) >= 10 else "daily"

    topic = str(
        round_event.get("topic")
        or round_event.get("title_cn")
        or round_event.get("title")
        or round_event.get("tray_note")
        or (details.get("workbench") or {}).get("note")
        or ak
        or "日常回合"
    ).strip()[:100] or "日常回合"
    summary = str(
        round_event.get("summary_cn")
        or round_event.get("note_cn")
        or round_event.get("tray_note")
        or f"完成日常回合：{topic}"
    ).strip()[:220]

    # 1. Auto Skill Book producer (F-134..136): craft / workbench / inquiry / reading / practice
    try:
        skill_spec: Optional[tuple[str, str, str, str]] = None
        if (
            ak in (
                "ADVANCE_WORKBENCH", "COMMIT_WORK_VERSION", "ADVANCE_HOME_CRAFT",
                "COMPLETE_INDEPENDENT_WORK", "INDEPENDENT_WORK",
                "OPEN_MATTER_ACT", "OPEN_LIVING_TRACE", "RUN_PERSONAL_ROUTINE",
            )
            or fam in (
                "advance_workbench_draft", "advance_home_craft", "independent_work",
                "compose_open_act", "run_personal_routine",
            )
            or v04_act in ("advance_home_craft",)
            or bool(slice_set & {"workbench_tray", "managed_artifact"})
        ):
            skill_spec = ("craft_workbench_flow", "workbench", "craft", "手作与工作台整理")
        elif (
            ak in ("ADVANCE_BOUNDED_INQUIRY", "REVISIT_FUTURE_QUESTION", "SETTLE_SPARK_ATTEMPT")
            or fam in ("advance_bounded_inquiry", "revisit_future_question", "settle_spark_attempt")
            or "v02_agency_depth.inquiries" in slice_set
        ):
            skill_spec = ("bounded_inquiry_flow", "inquiry", "inquiry", "有界探究与核实")
        elif (
            ak in ("FOLLOW_BOOKSTORE_BOOKMARK", "OPEN_SHARED_MATERIAL")
            or fam in ("follow_bookstore_bookmark", "open_shared_material")
            or any(s.startswith("reading") or s.startswith("outing.bookstore") for s in slice_set)
        ):
            skill_spec = ("reading_bookmark_flow", "reading", "reading", "阅读与书签跟进")
        elif (
            ak in (
                "OBSERVE_PLANT_JOURNAL", "CARE_PORCH_PLANT", "ACT_OPTIONAL_NEXT_GESTURE",
                "FOLLOW_PLACED_MEMORY_CUE", "ELASTIC_SHARED_NOTEBOOK",
            )
            or fam in (
                "observe_plant_journal", "courtyard_care", "act_optional_next_gesture",
                "follow_placed_memory_cue", "elastic_shared_notebook", "explore_autobiography_spark",
            )
        ):
            skill_spec = ("daily_practice_flow", "practice", "practice", "日常练习与养护")

        if skill_spec is not None:
            method_id, scope_name, domain_name, title_cn = skill_spec
            rt_doc = _read_optional_json_zero_write(inst_root / "phone" / "v02_runtime_migration.json")
            model_id = "local_runtime"
            if isinstance(rt_doc, Mapping):
                cm = rt_doc.get("carrier_migration")
                if isinstance(cm, Mapping):
                    fp = cm.get("carrier_fingerprint")
                    if isinstance(fp, Mapping) and str(fp.get("model_id") or "").strip():
                        model_id = str(fp["model_id"]).strip()[:100]
            tool_version = "v02_loop"
            try:
                from components.life.scripts import v02_autobiography_skill_provenance as _asp_sk
            except ImportError:
                import v02_autobiography_skill_provenance as _asp_sk  # type: ignore[no-redef]
            asp_raw = _read_optional_json_zero_write(
                inst_root / "state" / "v02_autobiography_skill_provenance.json"
            )
            releases = (
                (asp_raw.get("skills") or {}).get("releases")
                if isinstance(asp_raw, Mapping) and isinstance(asp_raw.get("skills"), Mapping)
                else {}
            )
            s_key = f"{method_id}::{tool_version}::{model_id}::{scope_name}"
            if not isinstance(releases, Mapping) or s_key not in releases:
                _asp_sk.register_skill_release(
                    inst_root,
                    {
                        "method_id": method_id,
                        "tool_version": tool_version,
                        "model_id": model_id,
                        "scope": scope_name,
                        "domain": domain_name,
                        "title_cn": title_cn,
                        "attribution": "MODEL_NATIVE",
                        "criteria_id": f"daily_{scope_name}_v1",
                        "standard_revision": "r1",
                        "status": "CANDIDATE",
                        "operation_key": f"op_skreg_{method_id}_{clean_op}"[:120],
                    },
                    now_iso=settled_at,
                )
            _asp_sk.record_skill_execution_outcome(
                inst_root,
                {
                    "method_id": method_id,
                    "tool_version": tool_version,
                    "model_id": model_id,
                    "scope": scope_name,
                    "outcome": "success",
                    "operation_key": f"op_skout_{method_id}_{clean_op}"[:120],
                },
                now_iso=settled_at,
            )
    except Exception:
        pass

    # 2. Auto Personality Drift snapshot (F-086): capture when 0 snapshots or >= 12h elapsed
    try:
        drift_raw = _read_optional_json_zero_write(
            inst_root / "state" / "v02_personality_drift_recheck.json"
        )
        snaps = (
            drift_raw.get("drift_snapshots")
            if isinstance(drift_raw, Mapping) and isinstance(drift_raw.get("drift_snapshots"), list)
            else []
        )
        should_capture = len(snaps) == 0
        if not should_capture:
            latest_dt = None
            for sn in reversed(snaps):
                if isinstance(sn, Mapping):
                    latest_dt = _parse_iso(sn.get("captured_at"))
                    if latest_dt is not None:
                        break
            cur_dt = _parse_iso(settled_at)
            if latest_dt is None or cur_dt is None:
                should_capture = True
            elif (cur_dt - latest_dt).total_seconds() >= 12 * 3600:
                should_capture = True
        if should_capture:
            try:
                from components.life.scripts import v02_personality_drift_and_model_recheck as _drift_mod
            except ImportError:
                import v02_personality_drift_and_model_recheck as _drift_mod  # type: ignore[no-redef]
            _drift_mod.capture_drift_snapshot(
                inst_root,
                now_iso=settled_at,
                payload={
                    "note_cn": "日常回合自动性格漂移快照",
                    "operation_key": f"op_drift_{clean_op}"[:120],
                },
            )
    except Exception:
        pass

    # 3. Auto Room Expectations baseline (F-175): 0 room_expectations + world_state.json has objects
    try:
        try:
            from components.life.scripts import v02_midscale_ecology_engine as _eco_mod
        except ImportError:
            import v02_midscale_ecology_engine as _eco_mod  # type: ignore[no-redef]
        world_exists, world_doc = _eco_mod._resolve_world_state_doc_zero_write(inst_root)
        if world_exists and _eco_mod._extract_world_state_entries(world_doc):
            eco_raw = _read_optional_json_zero_write(inst_root / "state" / "v02_midscale_ecology.json")
            existing_exp = (
                eco_raw.get("room_expectations")
                if isinstance(eco_raw, Mapping) and isinstance(eco_raw.get("room_expectations"), list)
                else []
            )
            if len(existing_exp) == 0:
                _eco_mod.snapshot_room_expectations(
                    inst_root,
                    {"operation_key": f"op_rmexp_{clean_op}"[:120]},
                    now_iso=settled_at,
                )
    except Exception:
        pass

    # 4. Auto Ecology Chain link (F-177): reading / inquiry / craft / outing / share / gift rounds
    try:
        eco_step_kind: Optional[str] = None
        if (
            ak in ("FOLLOW_BOOKSTORE_BOOKMARK", "OPEN_SHARED_MATERIAL")
            or fam in ("follow_bookstore_bookmark", "open_shared_material")
        ):
            eco_step_kind = "reading"
        elif (
            ak in ("ADVANCE_BOUNDED_INQUIRY", "REVISIT_FUTURE_QUESTION", "SETTLE_SPARK_ATTEMPT")
            or fam in ("advance_bounded_inquiry", "revisit_future_question", "settle_spark_attempt")
        ):
            eco_step_kind = "inquiry"
        elif (
            ak in (
                "ADVANCE_WORKBENCH", "COMMIT_WORK_VERSION", "ADVANCE_HOME_CRAFT",
                "COMPLETE_INDEPENDENT_WORK", "INDEPENDENT_WORK",
                "OPEN_MATTER_ACT", "OPEN_LIVING_TRACE", "OBSERVE_PLANT_JOURNAL", "CARE_PORCH_PLANT",
            )
            or fam in (
                "advance_workbench_draft", "advance_home_craft", "independent_work",
                "compose_open_act", "observe_plant_journal", "courtyard_care",
                "explore_autobiography_spark",
            )
            or v04_act in ("advance_home_craft",)
        ):
            eco_step_kind = "craft"
        elif (
            ak in (
                "STRENUOUS_OUTING_WALK", "OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE",
                "VISIT_CAFE_RESIDENT", "INTERACT_CAFE_RESIDENT", "RETURN_NEIGHBORHOOD_LOAN",
            )
            or fam in ("strenuous_outing_walk", "outing_visit")
        ):
            eco_step_kind = "outing"
        elif (
            ak in (
                "POST_SLOW_LETTER_DRAFT", "POST_DRAFTED_SLOW_LETTER",
                "CLARIFY_COMMON_GROUND", "ADOPT_TENTATIVE_PLAN",
                "ELASTIC_SHARED_NOTEBOOK", "ELASTIC_OFFER_SURPRISE",
            )
            or fam in (
                "post_matured_slow_letter_draft", "clarify_common_ground",
                "adopt_tentative_plan", "elastic_shared_notebook", "elastic_offer_surprise",
            )
        ):
            eco_step_kind = "share"
        elif ak in ("DECIDE_ITEM_GIFT", "ACCEPT_ITEM_GIFT") or fam in ("decide_partner_item_gift",):
            eco_step_kind = "gift"

        if eco_step_kind is not None:
            try:
                from components.life.scripts import v02_midscale_ecology_engine as _eco_ch
            except ImportError:
                import v02_midscale_ecology_engine as _eco_ch  # type: ignore[no-redef]
            chain_id = str(round_event.get("ecology_chain_id") or f"chain_day_{day_tok}").strip()[:96]
            _eco_ch.record_ecology_chain_link(
                inst_root,
                {
                    "chain_id": chain_id,
                    "theme_cn": str(round_event.get("ecology_theme_cn") or f"{day_tok}日常延续")[:120],
                    "step_kind": eco_step_kind,
                    "ref_id": f"settled:{clean_op}",
                    "title_cn": topic[:140],
                    "note_cn": summary[:240],
                    "shared": is_shared_round,
                    "operation_key": f"op_echain_{clean_op}"[:120],
                },
                now_iso=settled_at,
            )
    except Exception:
        pass

    # 5. Auto Body History (F-029): illness / rest / somatic comfort rounds
    try:
        is_body_round = (
            ak in (
                "REST", "ILLNESS_REST", "BED_REST", "WARM_HYDRATION",
                "RESOLVE_GARMENT_CARE", "USE_CYCLE_SUPPLY", "ADJUST_MICROZONE",
            )
            or fam in (
                "illness_rest_recovery", "resolve_garment_care",
                "use_cycle_supply", "adjust_microzone",
            )
            or bool(slice_set & {"v03_ecology.illness", "body.somatic", "v08_composable_body"})
        )
        if is_body_round:
            try:
                from components.life.scripts import v02_world_news_carrier_lineage as _wl_mod
            except ImportError:
                import v02_world_news_carrier_lineage as _wl_mod  # type: ignore[no-redef]
            strain_tag = str(
                round_event.get("care_event_kind")
                or round_event.get("illness_or_strain_tag")
                or ("warm_hydration" if ak == "WARM_HYDRATION" else "rest_care")
            ).strip()[:80]
            rest_adj = str(
                round_event.get("rest_adjustment_cn")
                or round_event.get("quiet_note")
                or summary
            ).strip()[:220]
            _wl_mod.record_body_history_entry(
                inst_root,
                {
                    "period_label": period_id,
                    "comfort_summary_cn": f"按体感照料休息：{topic}"[:320],
                    "illness_or_strain_tag": strain_tag,
                    "rest_adjustment_cn": rest_adj,
                    "shared": is_shared_round,
                    "operation_key": f"op_bhist_{clean_op}"[:120],
                },
                actor_side="subject",
                now_iso=settled_at,
            )
    except Exception:
        pass

    # 6. 0.2 Deep-Engine Seal: Canonical SQLite Ledger Mirror + S07 Durable Affect Pulse
    try:
        try:
            from components.life.scripts import v02_crash_and_partial_reconciler as _recon_mod
        except ImportError:
            import v02_crash_and_partial_reconciler as _recon_mod  # type: ignore[no-redef]
        _legacy_root_ledger = inst_root / "canonical_life.sqlite3"
        _v02_ledger = _recon_mod.canonical_ledger_path(inst_root)
        if _v02_ledger.is_file() or not _legacy_root_ledger.is_file():
            _recon_mod.record_live_cycle_settlement(
                inst_root,
                cycle_id=clean_op,
                action_kind=ak or fam or "daily_round",
                outcome_status="SETTLED",
                target_ref=str(round_event.get("source_ref") or f"round:{clean_op}")[:120],
                summary=summary[:240],
                now_iso=settled_at,
            )
    except Exception:
        pass

    try:
        try:
            from components.life.scripts import s07_affect_owner as _s07_mod
        except ImportError:
            import s07_affect_owner as _s07_mod  # type: ignore[no-redef]
        pulse_intensity = 48 if (ak in ("REST", "ILLNESS_REST", "BED_REST") or "tension" in fam) else 32
        pulse_kind = (
            _s07_mod.AFFECT_KIND_MOOD_SHIFT
            if ("tension" in fam or ak in ("RESOLVE_TENSION", "EXPRESS_MISSING", "SHARE_HELD_THREAD"))
            else _s07_mod.AFFECT_KIND_AFFECT_PULSE
        )
        _s07_mod.record_live_affect_pulse(
            inst_root,
            source_ref=f"round:{clean_op}",
            kind=pulse_kind,
            intensity=pulse_intensity,
            note=f"{topic}: {summary}"[:160],
            ttl_hours=6,
            operation_key=f"op_s07_{clean_op}"[:120],
            now_iso=settled_at,
        )
    except Exception:
        pass

    # 6b. Stage 4 Canonical Lived-Memory Sink (notable settled daily rounds, skipping routine idle no-ops)
    try:
        if _is_notable_daily_round_for_memory_sink(ak, fam, v04_act, slice_set, round_event):
            try:
                from components.life.scripts import lived_memory_sink as _lms
            except ImportError:
                import lived_memory_sink as _lms  # type: ignore[no-redef]
            cur_w = str(round_event.get("weather") or _read_weather_zero_write(inst_root) or "").strip().lower()
            is_rain_walk = (
                cur_w in ("rain", "heavy_rain", "storm", "drizzle")
                and ak in ("STRENUOUS_OUTING_WALK", "OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE")
            )
            is_recovery = (
                ak in ("REST", "ILLNESS_REST", "BED_REST", "WARM_HYDRATION", "RESOLVE_GARMENT_CARE")
                or fam in ("illness_rest_recovery", "resolve_garment_care")
            )
            if is_rain_walk:
                round_salience = 0.60
                round_affect_kind: Optional[str] = "mood_shift"
                round_intensity = 48
            elif is_recovery or "tension" in fam:
                round_salience = 0.56
                round_affect_kind = "mood_shift"
                round_intensity = 46
            else:
                round_salience = 0.48
                round_affect_kind = "affect_pulse"
                round_intensity = 38
            raw_src_ref = str(round_event.get("source_ref") or f"round:{clean_op}").strip()[:120]
            sink_refs: list[tuple[str, str]] = [("round_id", clean_op), ("event_id", clean_op)]
            if raw_src_ref and raw_src_ref != clean_op:
                sink_refs.append(("source_ref", raw_src_ref))
            if ak:
                sink_refs.append(("action_kind", ak))
            _lms.emit_lived_episode_safe(
                inst_root,
                _lms.LivedEpisodeEvent(
                    episode_key=f"daily_round:{clean_op}",
                    domain="daily_round",
                    topic=topic,
                    exact_detail=summary,
                    gist_summary=summary[:180],
                    occurred_at=settled_at,
                    emotional_salience=round_salience,
                    affect_kind=round_affect_kind,
                    affect_intensity=round_intensity,
                    shared=is_shared_round,
                    source_refs=tuple(sink_refs),
                ),
                now_iso=settled_at,
            )
    except Exception:
        pass

    # 7. 0.5 Living Town & Occasional Morning Dream auto-producers (strictly write-path, zero LLM)
    outreach_enabled_root = (
        (inst_root / "phone" / "v03_ecology.json").is_file()
        or bool(round_event.get("enable_lived_outreach"))
    )
    town_spectacle_facts: Optional[dict[str, Any]] = None
    try:
        town_file = inst_root / "world" / "v05_living_town.json"
        if town_file.is_file() or ak in ("STRENUOUS_OUTING_WALK", "OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE"):
            try:
                from components.world.scripts import v05_living_town_engine as _v05_town
            except ImportError:
                import v05_living_town_engine as _v05_town  # type: ignore[no-redef]
            _v05_town.advance_town_pulse(inst_root, elapsed_hours=1.0, now_iso=settled_at)
            if outreach_enabled_root and ak in ("STRENUOUS_OUTING_WALK", "OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE"):
                town_snap = _v05_town.read_living_town_snapshot(inst_root, now_iso=settled_at)
                raw_spec = town_snap.get("rare_spectacle") if isinstance(town_snap, Mapping) else None
                if isinstance(raw_spec, Mapping) and raw_spec.get("spectacle_id"):
                    town_spectacle_facts = dict(raw_spec)
    except Exception:
        pass

    morning_dream_facts: Optional[dict[str, Any]] = None
    try:
        if ak in ("SLEEP", "MORNING_WAKE") or bool(round_event.get("woke_from_sleep")):
            try:
                from components.life.scripts import v05_dreams_desires_foreshadow as _v05_spark
            except ImportError:
                import v05_dreams_desires_foreshadow as _v05_spark  # type: ignore[no-redef]
            dream_kwargs: dict[str, Any] = {
                "recent_echoes": [topic[:48]] if topic else (),
                "calm_quiet": False,
                "now_iso": settled_at,
                "actor_side": "subject",
            }
            if isinstance(round_event.get("force_roll_seed"), str) and round_event["force_roll_seed"].strip():
                dream_kwargs["force_roll_seed"] = round_event["force_roll_seed"].strip()
            dream_res = _v05_spark.settle_morning_dream_pulse(inst_root, **dream_kwargs)
            if (
                isinstance(dream_res, Mapping)
                and dream_res.get("dream_occurred")
                and isinstance(dream_res.get("morning_dream"), Mapping)
            ):
                morning_dream_facts = dict(dream_res["morning_dream"])
    except Exception:
        pass

    # 7b. Stage 3 High-Threshold Lived Outreach Producers (strictly honest_outbox, zero LLM, zero crash)
    if outreach_enabled_root:
        try:
            try:
                from components.life.scripts import proactive_outreach_producer as _outreach_prod
            except ImportError:
                import proactive_outreach_producer as _outreach_prod  # type: ignore[no-redef]

            if morning_dream_facts is not None:
                try:
                    _outreach_prod.evaluate_and_enqueue_lived_outreach(
                        inst_root,
                        trigger_kind="vivid_morning_dream",
                        context_facts=morning_dream_facts,
                        now_iso=settled_at,
                    )
                except Exception:
                    pass

            if town_spectacle_facts is not None:
                try:
                    _outreach_prod.evaluate_and_enqueue_lived_outreach(
                        inst_root,
                        trigger_kind="rare_town_spectacle",
                        context_facts=town_spectacle_facts,
                        now_iso=settled_at,
                    )
                except Exception:
                    pass

            if is_shared_round:
                craft_facts: Optional[dict[str, Any]] = None
                v04_det = details.get("v04_neighborhood") if isinstance(details.get("v04_neighborhood"), Mapping) else None
                v04_res = v04_det.get("result") if isinstance(v04_det, Mapping) and isinstance(v04_det.get("result"), Mapping) else None
                if (
                    isinstance(v04_det, Mapping)
                    and v04_det.get("action") == "craft_paper_or_box"
                    and isinstance(v04_res, Mapping)
                    and str(v04_res.get("step") or "").strip().lower() == "finished"
                ):
                    craft_facts = {
                        "work_id": f"v04_box_{clean_op}",
                        "title": str(round_event.get("title_cn") or round_event.get("title") or "手工分格收纳盒")[:60],
                        "completed": True,
                        "status": "finished",
                    }
                elif ak in ("COMMIT_WORK_VERSION", "ADVANCE_HOME_CRAFT", "COMPLETE_HOME_CRAFT", "FINISH_CRAFT_WORK") and (
                    round_event.get("completed") is True
                    or str(round_event.get("status") or round_event.get("target_step") or "").strip().lower()
                    in ("finished", "completed", "done")
                ):
                    craft_facts = {
                        "work_id": str(round_event.get("artifact_id") or round_event.get("work_id") or clean_op)[:80],
                        "title": topic[:60],
                        "completed": True,
                        "status": "finished",
                    }
                if craft_facts is not None:
                    try:
                        _outreach_prod.evaluate_and_enqueue_lived_outreach(
                            inst_root,
                            trigger_kind="finished_craft_work",
                            context_facts=craft_facts,
                            now_iso=settled_at,
                        )
                    except Exception:
                        pass

            try:
                cur_weather = str(round_event.get("weather") or _read_weather_zero_write(inst_root) or "").strip().lower()
                is_weather_event = (
                    ak in ("OUTING_VISIT", "MOVE_PLACE", "STAY_OUTSIDE", "ADVANCE_CLOCK", "CROSS_NIGHT_ADVANCE")
                    or bool(round_event.get("sudden_weather_shift"))
                    or v04_act == "set_local_weather"
                )
                if is_weather_event and (
                    cur_weather in ("rain", "heavy_rain", "storm", "snow")
                    or (cur_weather == "drizzle" and bool(round_event.get("sudden_weather_shift")))
                ):
                    v04_raw = _read_optional_json_zero_write(inst_root / "world" / "v04_neighborhood_state.json")
                    v04_env = v04_raw.get("environment") if isinstance(v04_raw, Mapping) and isinstance(v04_raw.get("environment"), Mapping) else {}
                    slot_key = str(round_event.get("weather_slot_key") or v04_env.get("weather_slot_key") or f"{day_tok}:{cur_weather}")
                    _outreach_prod.evaluate_and_enqueue_lived_outreach(
                        inst_root,
                        trigger_kind="sudden_weather_care",
                        context_facts={
                            "weather": cur_weather,
                            "previous_weather": str(round_event.get("previous_weather") or ""),
                            "weather_slot_key": slot_key,
                            "sudden_shift": True,
                        },
                        now_iso=settled_at,
                    )
            except Exception:
                pass
        except Exception:
            pass

    # 8. Auto Workspace Audit (F-187): record bounded workspace audit when runtime migration state exists
    try:
        rm_phone_file = inst_root / "phone" / "v02_runtime_migration.json"
        rm_state_file = inst_root / "state" / "v02_runtime_governance_migration.json"
        rm_exists = (rm_phone_file.is_file() and not rm_phone_file.is_symlink()) or (
            rm_state_file.is_file() and not rm_state_file.is_symlink()
        )
        ret_check = _read_retirement_status_zero_write(inst_root)
        if rm_exists and not ret_check.get("retired_sealed"):
            try:
                from components.life.scripts import v02_runtime_governance_migration as _rm_audit
            except ImportError:
                import v02_runtime_governance_migration as _rm_audit  # type: ignore[no-redef]
            raw_admitted = round_event.get("admitted_candidates")
            admitted_cands: list[dict[str, Any]] = []
            if isinstance(raw_admitted, list) and raw_admitted:
                for idx_a, a_item in enumerate(raw_admitted[:6]):
                    if isinstance(a_item, Mapping):
                        a_title = str(a_item.get("title") or a_item.get("title_cn") or topic).strip()[:120] or topic
                        a_priv = bool(a_item.get("is_private_draft", is_private_round))
                        admitted_cands.append({
                            "candidate_id": str(a_item.get("candidate_id") or f"cand-{idx_a + 1}").strip()[:96],
                            "source": str(a_item.get("source") or "daily_loop_round").strip()[:64],
                            "title": a_title,
                            "visibility": "private_draft" if a_priv else "shared",
                            "is_private_draft": a_priv,
                        })
                    elif isinstance(a_item, str) and a_item.strip():
                        admitted_cands.append({
                            "candidate_id": f"cand-{idx_a + 1}",
                            "source": "daily_loop_round",
                            "title": a_item.strip()[:120],
                            "visibility": "private_draft" if is_private_round else "shared",
                            "is_private_draft": bool(is_private_round),
                        })
            if not admitted_cands:
                admitted_cands = [
                    {
                        "candidate_id": str(round_event.get("source_ref") or f"round:{clean_op}")[:96],
                        "source": "daily_loop_round",
                        "title": topic[:120] or "日常回合",
                        "visibility": "private_draft" if is_private_round else "shared",
                        "is_private_draft": bool(is_private_round),
                    }
                ]
            raw_trimmed = (
                round_event.get("trimmed_reasons")
                or round_event.get("second_order_constrained_candidates")
                or []
            )
            trimmed_list: list[dict[str, Any]] = []
            if isinstance(raw_trimmed, list):
                for idx_t, t_item in enumerate(raw_trimmed[:6]):
                    if isinstance(t_item, Mapping):
                        t_title = str(
                            t_item.get("title")
                            or t_item.get("title_cn")
                            or t_item.get("candidate_title")
                            or "候选项"
                        ).strip()[:120] or "候选项"
                        t_priv = bool(t_item.get("is_private_draft", False))
                        trimmed_list.append({
                            "candidate_id": str(
                                t_item.get("candidate_id")
                                or t_item.get("candidate_ref")
                                or f"trimmed-{idx_t + 1}"
                            ).strip()[:96],
                            "source": str(t_item.get("source") or "daily_loop_candidate").strip()[:64],
                            "title": t_title,
                            "visibility": "private_draft" if t_priv else "shared",
                            "is_private_draft": t_priv,
                            "reason_code": str(
                                t_item.get("reason_code") or "WORKSPACE_SLOT_TRIMMED"
                            ).strip()[:64],
                            "reason_cn": str(
                                t_item.get("reason_cn") or "受当轮工作区容量或规矩约束未进入焦点"
                            ).strip()[:160],
                        })
            raw_entered = round_event.get("entered_workspace_count")
            entered_cnt = (
                int(raw_entered)
                if isinstance(raw_entered, int) and not isinstance(raw_entered, bool) and raw_entered >= 0
                else len(admitted_cands)
            )
            raw_visible = round_event.get("visible_candidate_count")
            visible_cnt = (
                int(raw_visible)
                if isinstance(raw_visible, int) and not isinstance(raw_visible, bool) and raw_visible >= entered_cnt
                else max(entered_cnt + len(trimmed_list), len(admitted_cands) + len(trimmed_list))
            )
            wake_reason = str(
                round_event.get("wake_reason") or ak or fam or "daily_loop_round"
            ).strip()[:80] or "daily_loop_round"
            _rm_audit.apply_runtime_migration_mutation(
                inst_root,
                {
                    "action": "record_workspace_audit",
                    "wake_reason": wake_reason,
                    "visible_candidate_count": visible_cnt,
                    "entered_workspace_count": entered_cnt,
                    "selection_rule": str(
                        round_event.get("selection_rule") or "daily_loop_bounded_admission"
                    ).strip()[:80],
                    "admitted_candidates": admitted_cands,
                    "trimmed_reasons": trimmed_list,
                    "operation_key": f"op_wsa_{clean_op}"[:120],
                },
                actor_side="subject",
                now_iso=settled_at,
            )
    except Exception:
        pass


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
    ret_info = _read_retirement_status_zero_write(inst_root)
    retired_sealed = bool(ret_info.get("retired_sealed"))
    if retired_sealed:
        eff_mode = "DORMANT_NO_COGNITION"
    else:
        eff_mode = str(
            runtime_mode_override
            or rt_summary.get("health_state")
            or rt_summary.get("mode")
            or "QUIET_HEALTHY"
        ).strip().upper()
    is_dormant_no_cog = retired_sealed or eff_mode in ("DORMANT_NO_COGNITION", "DORMANT")
    is_dormant_cons = (not is_dormant_no_cog) and eff_mode == "DORMANT_CONSERVATION"
    high_dissipation_skipped = bool(is_dormant_no_cog or is_dormant_cons)

    # 1. Host offline/return sensing (life_dwell).  ``evaluate_host_gap`` takes the two
    #    instants positionally (``previous_iso``, ``current_iso``) and returns ``None``
    #    unless the offline span strictly exceeds its threshold -- so only call it when a
    #    previous host instant is known, and record the gap only when a real report exists.
    host_gap_report: Optional[dict[str, Any]] = None
    if previous_host_iso and not retired_sealed:
        host_gap_report = v03_dwell.evaluate_host_gap(previous_host_iso, eff_iso)
    dwell_file = v03_dwell.life_dwell_state_path(inst_root)
    if not retired_sealed and isinstance(host_gap_report, Mapping) and (dwell_file.is_file() or previous_host_iso):
        try:
            v03_dwell.apply_life_dwell_action(
                inst_root,
                {
                    "action": "record_host_gap",
                    "from_iso": host_gap_report.get("from_iso") or previous_host_iso,
                    "to_iso": host_gap_report.get("to_iso") or eff_iso,
                    "kind": v03_dwell.HOST_GAP_KIND,
                },
                now_iso=eff_iso,
            )
        except Exception:
            pass

    # 2. Everyday desires turn_only natural decay
    desire_fade_report: dict[str, Any] = {"faded_turn_only_count": 0, "active_count": 0}
    desires_file = v03_desires.everyday_desire_state_path(inst_root)
    if advance_turn and not retired_sealed and desires_file.is_file():
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
    # v0.8 Wave 2: bring the real v0.4 courtyard/plant segments up to this tick's instant.
    # Zero-write when the neighborhood was never initialized or retired archive is sealed.
    if advance_clock and not retired_sealed:
        clock_report["v04_neighborhood"] = _advance_v04_neighborhood_processes_zero_write(
            inst_root, eff_iso
        )

    # 4. Collect all 0.2 + 0.3 candidates & summaries (zero-write)
    collected = collect_v02_v03_loop_candidates(inst_root, now_iso=eff_iso)
    if retired_sealed or runtime_mode_override:
        if is_dormant_no_cog:
            collected["dormant_suppressed"] = True
            if retired_sealed:
                collected["retired_sealed"] = True
            collected["candidates"] = []
            collected["candidate_records"] = []
            collected["descriptors"] = {}
            collected["gating"]["dormant_suppressed"] = True
            if retired_sealed:
                collected["gating"]["retired_sealed"] = True
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
    # Stage 2: increment desire friction_count on tick without frustration debt when arbitrated
    if not retired_sealed and not is_dormant_no_cog:
        arb_notes = (
            collected.get("gating", {}).get("desire_arbitration_notes")
            if isinstance(collected.get("gating"), Mapping)
            else None
        )
        if isinstance(arb_notes, list):
            for note in arb_notes:
                if (
                    isinstance(note, Mapping)
                    and note.get("increment_friction_on_settle") is True
                    and note.get("desire_id")
                    and note.get("friction_reason")
                ):
                    try:
                        record_desire_arbitration_friction(
                            inst_root,
                            desire_id=str(note["desire_id"]),
                            friction_reason=str(note["friction_reason"]),
                            now_iso=eff_iso,
                        )
                    except Exception:
                        pass
    if is_dormant_cons and selected.get("selected_candidate_id") is None:
        selected = {
            **selected,
            "decision_kind": "DORMANT_CONSERVATION_IDLE",
            "dormant_conservation": True,
            "high_dissipation_skipped": True,
            "llm_calls": 0,
        }

    # v0.8 Wave 2: a selected v0.4 neighborhood clue carries the exact command the
    # caller must settle -- action, payload and deterministic operation_key -- so a
    # chosen opportunity can never stop at "decided but the world did not move".
    selected_desc = (
        collected["descriptors"].get(str(selected.get("selected_candidate_id") or ""))
        if isinstance(collected.get("descriptors"), Mapping)
        else None
    )
    if isinstance(selected_desc, Mapping):
        selected_event = v02_v03_round_event_for_selected_candidate(
            collected,
            selected_desc.get("source_ref"),
            now_iso=eff_iso,
            decision_id=str(selected.get("decision_id") or "") or None,
        )
        if selected_event is not None:
            selected["v02_v03_round_event"] = selected_event
            # Backward-compatible spelling used by older callers for the v0.4 subset.
            if str(selected_event.get("source_ref") or "").startswith("v04_"):
                selected["v04_round_event"] = selected_event

    return {
        "ok": True,
        "schema": BRIDGE_SCHEMA,
        "tick_at": eff_iso,
        "local_date": local_date,
        "runtime_mode": eff_mode,
        "retired_sealed": retired_sealed,
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


def compute_desk_stuffiness(
    recent_actions: Sequence[str],
    is_final_closeout_step: bool = False,
) -> dict[str, Any]:
    """连写两轮及以上同一案头活时浮出微闷感；收尾最后一步豁免打断（第 85 章接缝三）。"""
    streak = 0
    for act in reversed(recent_actions):
        if str(act) == "continue_note":
            streak += 1
        else:
            break
    if is_final_closeout_step:
        return {"stuffy": False, "streak": streak, "discount": 0.0, "hint_cn": ""}
    if streak >= 2:
        discount = min(0.55, 0.28 * (streak - 1))
        return {
            "stuffy": True,
            "streak": streak,
            "discount": round(discount, 2),
            "hint_cn": "已经在书桌前连写两段，稍微有点坐闷了",
        }
    return {"stuffy": False, "streak": streak, "discount": 0.0, "hint_cn": ""}


def build_outing_reason(
    *,
    milk_empty: bool = False,
    low_tide_open: bool = False,
    passing_cart_title: Optional[str] = None,
    stuffy: bool = False,
    rain_heavy: bool = False,
) -> Optional[dict[str, Any]]:
    """把干巴巴的移动指令拼成带当下生活画面的自然由头；大雨时留屋歇茶不强催出门。"""
    if passing_cart_title and not rain_heavy:
        return {
            "action_id": "go_alley_cart",
            "kind": "outing",
            "label_cn": f"窗外刚飘来{passing_cart_title}的动静，趁这会儿下楼追上去买两块",
            "score": 0.92,
        }
    if low_tide_open and not rain_heavy:
        return {
            "action_id": "go_seawall_tide",
            "kind": "outing",
            "label_cn": "下午刚好退潮露出了旧石阶，拎包散步去海堤看看",
            "score": 0.86,
        }
    if milk_empty and not rain_heavy:
        return {
            "action_id": "go_store_milk",
            "kind": "outing",
            "label_cn": "家里牛奶喝空了，顺路去街口便利店捎一盒回来",
            "score": 0.82,
        }
    if stuffy and not rain_heavy:
        return {
            "action_id": "go_breeze_walk",
            "kind": "outing",
            "label_cn": "在屋里坐久了有点闷，推门沿着河边走十分钟吹吹风",
            "score": 0.78,
        }
    return None


def rank_daily_candidates(
    *,
    recent_actions: Sequence[str],
    hour: int,
    has_active_note: bool = True,
    is_final_closeout_step: bool = False,
    milk_empty: bool = False,
    low_tide_open: bool = False,
    passing_cart_title: Optional[str] = None,
    rain_heavy: bool = False,
    legacy_mode: bool = False,
) -> list[dict[str, Any]]:
    """0.5 呼吸节律候选排序：防止卧室连写打转、大雨留屋喝茶、保留收尾豁免。"""
    options: list[dict[str, Any]] = []
    if legacy_mode:
        if has_active_note:
            options.append({"action_id": "continue_note", "kind": "continue_note", "label_cn": "继续续写卧室笔记", "score": 0.85})
        options.append({"action_id": "go_fish_market", "kind": "outing", "label_cn": "前往鱼市", "score": 0.45})
        options.append({"action_id": "quiet_dwell", "kind": "quiet_dwell", "label_cn": "原地待着", "score": 0.40})
        return sorted(options, key=lambda x: float(x["score"]), reverse=True)

    stuffy_info = compute_desk_stuffiness(recent_actions, is_final_closeout_step=is_final_closeout_step)
    discount = float(stuffy_info["discount"])

    note_base = 0.84 if 8 <= hour <= 11 else (0.66 if 19 <= hour <= 21 else 0.58)
    if has_active_note:
        options.append({
            "action_id": "continue_note",
            "kind": "continue_note",
            "label_cn": "顺着上次页码接着改一小段稿子",
            "score": round(max(0.20, note_base - discount), 2),
        })

    outing_opt = build_outing_reason(
        milk_empty=milk_empty,
        low_tide_open=low_tide_open,
        passing_cart_title=passing_cart_title,
        stuffy=bool(stuffy_info["stuffy"]),
        rain_heavy=rain_heavy,
    )
    if outing_opt is not None:
        options.append(outing_opt)

    rest_score = 0.80 if (13 <= hour <= 14 or bool(stuffy_info["stuffy"]) or rain_heavy) else 0.52
    rest_label = "外头雨大，泡杯热红茶靠窗听雨歇会儿" if rain_heavy else "合上册子泡杯温茶，靠在沙发歇二十分钟"
    options.append({"action_id": "rest_or_tea", "kind": "rest_or_tea", "label_cn": rest_label, "score": rest_score})
    options.append({"action_id": "quiet_dwell", "kind": "quiet_dwell", "label_cn": "什么也不干，安静发会儿呆", "score": 0.50 if hour < 22 else 0.88})

    return sorted(options, key=lambda x: float(x["score"]), reverse=True)[:5]


__all__ = [
    "BRIDGE_SCHEMA",
    "CONSEQUENCE_LEDGER_FILENAME",
    "FROZEN_ANCHOR_SHA256",
    "arbitrate_desire_candidates_for_loop",
    "build_outing_reason",
    "classify_day_observability",
    "collect_v02_v03_loop_candidates",
    "compute_desk_stuffiness",
    "has_v02_v03_active_state",
    "rank_daily_candidates",
    "reconcile_and_project_revocation_surfaces",
    "record_desire_arbitration_friction",
    "resolve_day_and_life_roots",
    "run_v02_v03_daily_tick",
    "select_deterministic_loop_action",
    "settle_v02_v03_round_consequences",
    "v02_v03_descriptor_for_selected_ref",
    "v02_v03_mapped_ref",
    "v02_v03_round_event_for_selected_candidate",
    "v04_round_event_for_selected_candidate",
    "verify_frozen_anchors",
]
