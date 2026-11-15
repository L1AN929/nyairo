#!/usr/bin/env python3
"""v0.3 有界留白（Dwell）、离线诚实缺口（HOST_GAP）、主观时间三态（流/滞/织）与日常毛刺引擎。

设计对齐
--------
* 第 03 / 06 / 18 章 & ADR-037 / ERR-10：
  - ``DwellWindow`` 有界留白（``declared_seconds``，随时可退 ``exit_anytime=True``，
    不入动作问责账本、不强制生成记忆或成长分、中途重读不重置 ``began_at`` 且过期不自续）；
  - 宿主停机超过 15 分钟（``> 900s``）诚实记录 ``HOST_GAP``（兼容历史别名 ``HOST_DOWN``
    并归一为 ``HOST_GAP``），严禁离线认知回填或编造停机经历。
* 第 08 章（主观时间感协议 T-Protocol & 真空期自立三支柱）：
  - 主观时间三态 ``FLOW``（流）/ ``STAGNANT``（滞）/ ``WEAVE``（织）；
  - 密度诚实（``T2``）：低事件密度时诚实呈现 ``STAGNANT``，禁止表演性充实；
  - 零重逢债务（``reunion_zero_debt=True``）。
* 第 21 章（无功利日常与生活毛刺：痕迹合法，档案非法）：
  - 支持 6 类日常毛刺与半途搁置：
    1) ``misplaced_item_search``：自己随手放错地方找半天；
    2) ``spilled_water_wipe_desk``：手滑洒水与擦桌子恢复纸面可用；
    3) ``forgot_umbrella_damp_coat``：忘带伞淋湿外套并增加有界受凉负荷（``chill_load_delta``）；
    4) ``eraser_torn_page``：橡皮擦破纸页 / 橡皮小洞物理痕迹；
    5) ``playlist_typo``：歌单打错字（如《晚风》->《碗风》）保留原样不提炼人格标签；
    6) ``stone_polishing_settled_incomplete``：打磨石头 / 排石子半途搁置（``settled_incomplete``，零未完成债）。

铁律：纯 Python 标准库、零 ``random``、零 LLM 调用、``GET`` 路径 100% 零落盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DWELL_STATE_SCHEMA = "life.v03.dwell.v1"
DWELL_VIEW_SCHEMA = "life.v03.dwell_projection.v1"
STATE_SCHEMA_VERSION = 1

MIN_DECLARED_DWELL_SECONDS = 30
DEFAULT_DECLARED_DWELL_SECONDS = 900
MAX_DECLARED_DWELL_SECONDS = 7200

# ERR-10: Canonical offline gap marker when host downtime exceeds 15 minutes (> 900s).
HOST_GAP_KIND = "HOST_GAP"
HOST_GAP_LEGACY_ALIASES = frozenset({"HOST_GAP", "HOST_DOWN"})
HOST_GAP_MIN_SECONDS = 900

SUBJECTIVE_TIME_MODES = ("FLOW", "STAGNANT", "WEAVE")
SUBJECTIVE_TIME_LABELS = {
    "FLOW": "流",
    "STAGNANT": "滞",
    "WEAVE": "织",
}

EVERYDAY_GLITCH_KINDS = (
    "misplaced_item_search",
    "spilled_water_wipe_desk",
    "forgot_umbrella_damp_coat",
    "eraser_torn_page",
    "playlist_typo",
    "stone_polishing_settled_incomplete",
)

MAX_HOST_GAP_HISTORY = 32
MAX_DWELL_HISTORY = 24
MAX_GLITCH_TRACES = 32
MAX_IDEMPOTENCY_RECORDS = 64


class LifeDwellError(ValueError):
    """Structured HTTP-aligned error for life_dwell operations."""

    def __init__(self, reason_code: str, message: str = "", http_status: int = 400) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code
        self.http_status = int(http_status)


def life_dwell_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / "life" / "life_dwell.json"


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
        raise LifeDwellError("INVALID_TIMESTAMP", "Invalid ISO-8601 timestamp.", 400)
    return parsed


def _stable_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def default_life_dwell_state() -> dict[str, Any]:
    return {
        "schema": DWELL_STATE_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "active_dwell": None,
        "recent_dwells": [],
        "host_gaps": [],
        "subjective_time": {
            "last_mode": "WEAVE",
            "focused_work_minutes": 0,
            "meaningful_event_count": 0,
            "idle_or_waiting_minutes": 0,
            "pillars": {
                "small_project": None,
                "neighborhood_tie": None,
                "daily_ritual": None,
            },
            "future_hooks": ["unfinished_desk_work"],
        },
        "chill_load": {
            "current_load": 0.0,
            "updated_at": None,
            "recovery_per_hour": 0.08,
            "source": None,
        },
        "glitch_traces": [],
        "idempotency": {},
    }


def _normalize_host_gap_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_kind = str(row.get("kind") or HOST_GAP_KIND).strip().upper()
    canonical_kind = HOST_GAP_KIND if raw_kind in HOST_GAP_LEGACY_ALIASES else HOST_GAP_KIND
    offline_sec = int(row.get("offline_seconds") or 0)
    return {
        "gap_id": str(row.get("gap_id") or _stable_id("hgap", row.get("from_iso"), row.get("to_iso"))),
        "kind": canonical_kind,
        "original_kind": raw_kind,
        "from_iso": str(row.get("from_iso") or ""),
        "to_iso": str(row.get("to_iso") or ""),
        "offline_seconds": max(0, offline_sec),
        "honest_blank": True,
        "cognitive_backfill_count": 0,
        "fabricated_memories": 0,
    }


def _validate_dwell_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema") != DWELL_STATE_SCHEMA:
        raise ValueError("invalid life_dwell schema")
    rev = raw.get("revision", 0)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise ValueError("invalid revision")
    base = default_life_dwell_state()
    base["revision"] = rev
    base["updated_at"] = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None
    if isinstance(raw.get("active_dwell"), Mapping):
        base["active_dwell"] = copy.deepcopy(dict(raw["active_dwell"]))
    if isinstance(raw.get("recent_dwells"), list):
        base["recent_dwells"] = [
            copy.deepcopy(dict(x)) for x in raw["recent_dwells"] if isinstance(x, Mapping)
        ][-MAX_DWELL_HISTORY:]
    if isinstance(raw.get("host_gaps"), list):
        base["host_gaps"] = [
            _normalize_host_gap_row(x) for x in raw["host_gaps"] if isinstance(x, Mapping)
        ][-MAX_HOST_GAP_HISTORY:]
    if isinstance(raw.get("subjective_time"), Mapping):
        st = dict(raw["subjective_time"])
        mode = str(st.get("last_mode") or "WEAVE").upper()
        if mode not in SUBJECTIVE_TIME_MODES:
            mode = "WEAVE"
        pillars_raw = st.get("pillars") if isinstance(st.get("pillars"), Mapping) else {}
        hooks_raw = st.get("future_hooks") if isinstance(st.get("future_hooks"), list) else ["unfinished_desk_work"]
        hooks = [str(h).strip() for h in hooks_raw if isinstance(h, str) and str(h).strip()]
        base["subjective_time"] = {
            "last_mode": mode,
            "focused_work_minutes": max(0, int(st.get("focused_work_minutes") or 0)),
            "meaningful_event_count": max(0, int(st.get("meaningful_event_count") or 0)),
            "idle_or_waiting_minutes": max(0, int(st.get("idle_or_waiting_minutes") or 0)),
            "pillars": {
                "small_project": pillars_raw.get("small_project"),
                "neighborhood_tie": pillars_raw.get("neighborhood_tie"),
                "daily_ritual": pillars_raw.get("daily_ritual"),
            },
            "future_hooks": hooks or ["unfinished_desk_work"],
        }
    if isinstance(raw.get("chill_load"), Mapping):
        cl = dict(raw["chill_load"])
        val = float(cl.get("current_load") or 0.0)
        base["chill_load"] = {
            "current_load": round(min(1.0, max(0.0, val)), 4),
            "updated_at": cl.get("updated_at") if isinstance(cl.get("updated_at"), str) else None,
            "recovery_per_hour": round(min(0.5, max(0.01, float(cl.get("recovery_per_hour") or 0.08))), 4),
            "source": cl.get("source") if isinstance(cl.get("source"), str) else None,
        }
    if isinstance(raw.get("glitch_traces"), list):
        base["glitch_traces"] = [
            copy.deepcopy(dict(x)) for x in raw["glitch_traces"] if isinstance(x, Mapping)
        ][-MAX_GLITCH_TRACES:]
    if isinstance(raw.get("idempotency"), Mapping):
        base["idempotency"] = {
            str(k): copy.deepcopy(v)
            for k, v in list(raw["idempotency"].items())[-MAX_IDEMPOTENCY_RECORDS:]
            if isinstance(k, str) and isinstance(v, Mapping)
        }
    return base


def read_life_dwell_state(day_root: Path | str) -> tuple[dict[str, Any], str | None]:
    """Zero-write read of stored life_dwell state."""
    path = life_dwell_state_path(day_root)
    if path.exists() and path.is_symlink():
        return default_life_dwell_state(), "DWELL_STATE_SYMLINK"
    if not path.exists():
        return default_life_dwell_state(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _validate_dwell_state(raw), None
    except Exception:
        return default_life_dwell_state(), "DWELL_STATE_CORRUPTED"


def _atomic_write_dwell_state(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_dwell_state(state)
    path = life_dwell_state_path(day_root)
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise LifeDwellError("DWELL_STATE_SYMLINK", "Symlink parent directory forbidden.", 503)
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise LifeDwellError("DWELL_STATE_SYMLINK", "Symlink state file forbidden.", 503)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".life_dwell_", suffix=".tmp", dir=str(parent))
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


# ---------------------------------------------------------------------------
# Pure Derivations: HOST_GAP, Dwell Window, Subjective Time & Chill Load
# ---------------------------------------------------------------------------


def evaluate_host_gap(
    previous_iso: str | datetime | None,
    current_iso: str | datetime | None,
    *,
    threshold_seconds: int = HOST_GAP_MIN_SECONDS,
) -> dict[str, Any] | None:
    """Return canonical ``HOST_GAP`` receipt iff offline gap strictly exceeds ``threshold_seconds`` (15m)."""
    prev_dt = _parse_iso(previous_iso)
    curr_dt = _parse_iso(current_iso)
    if prev_dt is None or curr_dt is None:
        return None
    elapsed = (curr_dt - prev_dt).total_seconds()
    if elapsed <= float(threshold_seconds):
        return None
    from_str = _format_iso(prev_dt)
    to_str = _format_iso(curr_dt)
    return {
        "gap_id": _stable_id("hgap", from_str, to_str),
        "kind": HOST_GAP_KIND,
        "original_kind": HOST_GAP_KIND,
        "from_iso": from_str,
        "to_iso": to_str,
        "offline_seconds": int(elapsed),
        "honest_blank": True,
        "cognitive_backfill_count": 0,
        "fabricated_memories": 0,
    }


def derive_chill_load(chill_state: Mapping[str, Any], now_dt: datetime) -> dict[str, Any]:
    base_load = float(chill_state.get("current_load") or 0.0)
    upd_dt = _parse_iso(chill_state.get("updated_at"))
    rate = float(chill_state.get("recovery_per_hour") or 0.08)
    if upd_dt is not None and now_dt > upd_dt and base_load > 0.0:
        elapsed_h = (now_dt - upd_dt).total_seconds() / 3600.0
        projected = max(0.0, base_load - rate * elapsed_h)
    else:
        projected = max(0.0, base_load)
    projected = round(min(1.0, projected), 4)
    if projected >= 0.45:
        band = "noticeable"
    elif projected >= 0.15:
        band = "mild"
    elif projected > 0.0:
        band = "trace"
    else:
        band = "none"
    return {
        "projected_load": projected,
        "band": band,
        "source": chill_state.get("source") if projected > 0.0 else None,
        "punitive_illness_penalty": False,
    }


def derive_dwell_window(active_dwell: Mapping[str, Any] | None, now_dt: datetime) -> dict[str, Any] | None:
    if not isinstance(active_dwell, Mapping):
        return None
    began_dt = _parse_iso(active_dwell.get("began_at"))
    end_dt = _parse_iso(active_dwell.get("latest_end_at"))
    declared_sec = int(active_dwell.get("declared_seconds") or DEFAULT_DECLARED_DWELL_SECONDS)
    if began_dt is None:
        return None
    if end_dt is None:
        end_dt = began_dt + timedelta(seconds=declared_sec)
    elapsed_sec = max(0, int((now_dt - began_dt).total_seconds()))
    expired = now_dt >= end_dt
    remaining_sec = 0 if expired else max(0, int((end_dt - now_dt).total_seconds()))
    return {
        "dwell_id": str(active_dwell.get("dwell_id") or ""),
        "began_at": _format_iso(began_dt),
        "declared_seconds": declared_sec,
        "latest_end_at": _format_iso(end_dt),
        "place_node": str(active_dwell.get("place_node") or "window_side"),
        "label": active_dwell.get("label"),
        "elapsed_seconds": min(elapsed_sec, declared_sec) if expired else elapsed_sec,
        "remaining_seconds": remaining_sec,
        "status": "EXPIRED_NO_AUTO_RENEW" if expired else "ACTIVE",
        "exit_anytime": True,
        "auto_renew_forbidden": True,
        "enters_action_ledger": False,
        "forced_memory_write": False,
        "growth_score_delta": 0,
        "productivity_penalty": 0,
    }


def derive_subjective_time_texture(
    *,
    focused_work_minutes: int = 0,
    meaningful_event_count: int = 0,
    idle_or_waiting_minutes: int = 0,
    pillars: Mapping[str, Any] | None = None,
    future_hooks: list[str] | None = None,
    active_dwell_status: str | None = None,
) -> dict[str, Any]:
    """Deterministic T-Protocol subjective time derivation (Ch. 08: FLOW / STAGNANT / WEAVE)."""
    pillars_map = dict(pillars or {})
    active_pillars = [k for k in ("small_project", "neighborhood_tie", "daily_ritual") if pillars_map.get(k)]
    hooks = [str(h) for h in (future_hooks or ["unfinished_desk_work"]) if str(h).strip()]
    if not hooks:
        hooks = ["unfinished_desk_work"]

    # T2 Density honesty: sparse events + long idle/waiting cannot fake "FLOW"!
    if focused_work_minutes >= 40 and idle_or_waiting_minutes <= focused_work_minutes // 2:
        mode = "FLOW"
        texture_note = "沉浸手头创作或阅读，抬头才发觉时间过去了"
    elif (
        meaningful_event_count <= 1
        and (idle_or_waiting_minutes >= 30 or active_dwell_status == "ACTIVE")
        and len(active_pillars) < 2
    ):
        mode = "STAGNANT"
        texture_note = "安静放空或低密度度过，时间显得缓慢拉长"
    elif len(active_pillars) >= 2 or meaningful_event_count >= 2:
        mode = "WEAVE"
        texture_note = "日常小事与自主节律彼此交织，日子有清楚的纹理"
    elif idle_or_waiting_minutes > focused_work_minutes:
        mode = "STAGNANT"
        texture_note = "没什么大事发生，一下午慢悠悠地晃过去了"
    else:
        mode = "WEAVE"
        texture_note = "按自己的步调安静运转"

    return {
        "mode": mode,
        "label_zh": SUBJECTIVE_TIME_LABELS[mode],
        "texture_note": texture_note,
        "focused_work_minutes": max(0, int(focused_work_minutes)),
        "meaningful_event_count": max(0, int(meaningful_event_count)),
        "idle_or_waiting_minutes": max(0, int(idle_or_waiting_minutes)),
        "active_pillars": active_pillars,
        "pillar_count": len(active_pillars),
        "future_hooks": hooks,
        "has_forward_hook": len(hooks) >= 1,
        "performative_fulfillment_forbidden": True,
        "reunion_zero_debt": True,
    }


def project_life_dwell(
    day_root: Path | str,
    *,
    now_iso: str | datetime | None = None,
    subject_view: bool = True,
) -> dict[str, Any]:
    """100% zero-write read projection for life_dwell."""
    now_dt = _require_now_dt(now_iso)
    state, err = read_life_dwell_state(day_root)
    dwell_proj = derive_dwell_window(state.get("active_dwell"), now_dt)
    st_raw = state["subjective_time"]
    st_proj = derive_subjective_time_texture(
        focused_work_minutes=int(st_raw.get("focused_work_minutes") or 0),
        meaningful_event_count=int(st_raw.get("meaningful_event_count") or 0),
        idle_or_waiting_minutes=int(st_raw.get("idle_or_waiting_minutes") or 0),
        pillars=st_raw.get("pillars"),
        future_hooks=list(st_raw.get("future_hooks") or []),
        active_dwell_status=dwell_proj["status"] if dwell_proj else None,
    )
    chill_proj = derive_chill_load(state["chill_load"], now_dt)

    traces_out: list[dict[str, Any]] = []
    for tr in state.get("glitch_traces", []):
        item = copy.deepcopy(tr)
        if not subject_view:
            item.pop("private_mutter", None)
        traces_out.append(item)

    return {
        "schema": DWELL_VIEW_SCHEMA,
        "state_schema": DWELL_STATE_SCHEMA,
        "read_only": True,
        "state_status": "degraded" if err else "ok",
        "state_reason_code": err,
        "revision": state["revision"],
        "observed_at": _format_iso(now_dt),
        "active_dwell": dwell_proj,
        "recent_dwell_count": len(state.get("recent_dwells") or []),
        "host_gaps": copy.deepcopy(state.get("host_gaps") or []),
        "host_gap_contract": {
            "canonical_kind": HOST_GAP_KIND,
            "threshold_seconds": HOST_GAP_MIN_SECONDS,
            "legacy_alias_accepted": True,
            "cognitive_backfill_forbidden": True,
        },
        "subjective_time": st_proj,
        "chill_load": chill_proj,
        "everyday_glitches": {
            "supported_kinds": list(EVERYDAY_GLITCH_KINDS),
            "traces": traces_out,
            "trace_is_physical_archive_is_forbidden": True,
            "persona_tag_extracted": False,
            "methodology_extracted": False,
            "affection_delta": 0,
        },
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Mutations: Dwell, HOST_GAP, Subjective Time & 6 Everyday Glitches
# ---------------------------------------------------------------------------


def _sync_damp_outerwear_if_available(
    day_root: Path,
    *,
    garment_id: str,
    label: str,
    now_str: str,
) -> bool:
    """Bridge forgot-umbrella damp coat to somatic_comfort.json when available."""
    try:
        from components.phone import somatic_comfort_view as somatic_view

        s_state, s_reason = somatic_view.read_state(day_root)
        if s_reason is not None:
            return False
        next_state, changed, err = somatic_view.wear_outerwear(
            s_state,
            garment_id=garment_id,
            label=label,
            dryness="damp",
            now_iso=now_str,
        )
        if err is None and changed:
            somatic_view.write_state(day_root, next_state)
            return True
        return err is None
    except Exception:
        return False


def apply_life_dwell_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: str | datetime | None = None,
    subject_view: bool = True,
) -> dict[str, Any]:
    """Execute deterministic mutation on life_dwell state."""
    if not isinstance(payload, Mapping):
        raise LifeDwellError("INVALID_PAYLOAD", "Payload must be a JSON object.", 400)
    root = Path(day_root)
    now_dt = _require_now_dt(now_iso)
    now_str = _format_iso(now_dt)

    state, err = read_life_dwell_state(root)
    if err is not None:
        raise LifeDwellError(err, "Cannot mutate corrupted life_dwell state.", 503)

    action = str(payload.get("action") or "").strip()
    if not action:
        raise LifeDwellError("MISSING_ACTION", "Field 'action' is required.", 400)

    op_key = payload.get("operation_key")
    fp_body = {k: v for k, v in payload.items() if k != "operation_key"}
    fp_hash = hashlib.sha256(
        json.dumps(fp_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if op_key is not None:
        if not isinstance(op_key, str) or not op_key.strip():
            raise LifeDwellError("INVALID_OPERATION_KEY", "operation_key must be non-empty string.", 400)
        op_key = op_key.strip()
        cached = state["idempotency"].get(op_key)
        if cached is not None:
            if cached.get("fingerprint") != fp_hash:
                raise LifeDwellError("OPERATION_KEY_CONFLICT", "Conflicting payload for operation_key.", 409)
            proj = project_life_dwell(root, now_iso=now_dt, subject_view=subject_view)
            proj["status"] = "already_applied"
            proj["replayed"] = True
            proj["action_receipt"] = copy.deepcopy(cached.get("receipt") or {})
            return proj

    receipt: dict[str, Any]

    if action == "start_dwell":
        raw_sec = payload.get("declared_seconds", DEFAULT_DECLARED_DWELL_SECONDS)
        if isinstance(raw_sec, bool) or not isinstance(raw_sec, int):
            raise LifeDwellError("INVALID_DECLARED_SECONDS", "declared_seconds must be integer.", 400)
        if raw_sec < MIN_DECLARED_DWELL_SECONDS or raw_sec > MAX_DECLARED_DWELL_SECONDS:
            raise LifeDwellError(
                "DECLARED_SECONDS_OUT_OF_RANGE",
                f"declared_seconds must be within [{MIN_DECLARED_DWELL_SECONDS}, {MAX_DECLARED_DWELL_SECONDS}].",
                400,
            )
        place_node = str(payload.get("place_node") or "window_side").strip() or "window_side"
        label = str(payload.get("label") or "窗边留白发呆").strip()[:80]

        # If an unexpired dwell is already active, do NOT overwrite began_at unless force_replace is set
        existing = derive_dwell_window(state.get("active_dwell"), now_dt)
        if existing is not None and existing["status"] == "ACTIVE" and not payload.get("replace_active"):
            raise LifeDwellError(
                "DWELL_ALREADY_ACTIVE",
                "An active DwellWindow is already running; exit first or wait until latest_end_at.",
                409,
            )
        if existing is not None and existing["status"] == "EXPIRED_NO_AUTO_RENEW":
            state["recent_dwells"].append(
                {
                    "dwell_id": existing["dwell_id"],
                    "began_at": existing["began_at"],
                    "ended_at": existing["latest_end_at"],
                    "declared_seconds": existing["declared_seconds"],
                    "place_node": existing["place_node"],
                    "outcome": "completed_bounded_blank",
                    "growth_score_delta": 0,
                    "forced_memory_write": False,
                }
            )
            state["recent_dwells"] = state["recent_dwells"][-MAX_DWELL_HISTORY:]

        end_dt = now_dt + timedelta(seconds=raw_sec)
        dwell_id = _stable_id("dwell", now_str, place_node, state["revision"] + 1)
        dwell_obj = {
            "dwell_id": dwell_id,
            "began_at": now_str,
            "declared_seconds": raw_sec,
            "latest_end_at": _format_iso(end_dt),
            "place_node": place_node,
            "label": label,
            "exit_anytime": True,
            "enters_action_ledger": False,
            "forced_memory_write": False,
            "growth_score_delta": 0,
            "productivity_penalty": 0,
        }
        state["active_dwell"] = dwell_obj
        state["subjective_time"]["idle_or_waiting_minutes"] = int(
            state["subjective_time"].get("idle_or_waiting_minutes") or 0
        ) + max(1, raw_sec // 60)
        receipt = {
            "action": "start_dwell",
            "dwell": copy.deepcopy(dwell_obj),
        }

    elif action == "end_dwell":
        existing = derive_dwell_window(state.get("active_dwell"), now_dt)
        if existing is None:
            raise LifeDwellError("NO_ACTIVE_DWELL", "No active DwellWindow to end.", 409)
        reason = str(payload.get("reason") or "natural_return").strip()[:80]
        closed_at = existing["latest_end_at"] if existing["status"] == "EXPIRED_NO_AUTO_RENEW" else now_str
        archived = {
            "dwell_id": existing["dwell_id"],
            "began_at": existing["began_at"],
            "ended_at": closed_at,
            "declared_seconds": existing["declared_seconds"],
            "place_node": existing["place_node"],
            "exit_reason": reason,
            "outcome": "early_exit_clean" if existing["status"] == "ACTIVE" else "completed_bounded_blank",
            "growth_score_delta": 0,
            "productivity_penalty": 0,
            "forced_memory_write": False,
        }
        state["active_dwell"] = None
        state["recent_dwells"].append(archived)
        state["recent_dwells"] = state["recent_dwells"][-MAX_DWELL_HISTORY:]
        receipt = {"action": "end_dwell", "archived_dwell": archived}

    elif action == "record_host_gap":
        from_iso = payload.get("from_iso")
        to_iso = payload.get("to_iso") or now_str
        raw_kind = str(payload.get("kind") or HOST_GAP_KIND).strip().upper()
        if raw_kind not in HOST_GAP_LEGACY_ALIASES:
            raise LifeDwellError("INVALID_GAP_KIND", f"Unsupported gap kind: {raw_kind}", 400)
        gap = evaluate_host_gap(from_iso, to_iso)
        if gap is None:
            raise LifeDwellError(
                "GAP_BELOW_HOST_GAP_THRESHOLD",
                f"Offline duration must exceed {HOST_GAP_MIN_SECONDS}s (15m) to record HOST_GAP.",
                409,
            )
        gap["original_kind"] = raw_kind
        gap["kind"] = HOST_GAP_KIND
        state["host_gaps"].append(gap)
        state["host_gaps"] = state["host_gaps"][-MAX_HOST_GAP_HISTORY:]
        receipt = {"action": "record_host_gap", "host_gap": copy.deepcopy(gap)}

    elif action == "update_subjective_time":
        st = state["subjective_time"]
        if "focused_work_minutes" in payload:
            val = payload["focused_work_minutes"]
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise LifeDwellError("INVALID_MINUTES", "focused_work_minutes must be non-negative int.", 400)
            st["focused_work_minutes"] = val
        if "meaningful_event_count" in payload:
            val = payload["meaningful_event_count"]
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise LifeDwellError("INVALID_COUNT", "meaningful_event_count must be non-negative int.", 400)
            st["meaningful_event_count"] = val
        if "idle_or_waiting_minutes" in payload:
            val = payload["idle_or_waiting_minutes"]
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise LifeDwellError("INVALID_MINUTES", "idle_or_waiting_minutes must be non-negative int.", 400)
            st["idle_or_waiting_minutes"] = val
        if isinstance(payload.get("pillars"), Mapping):
            for pk in ("small_project", "neighborhood_tie", "daily_ritual"):
                if pk in payload["pillars"]:
                    pval = payload["pillars"][pk]
                    st["pillars"][pk] = str(pval).strip()[:120] if isinstance(pval, str) and pval.strip() else None
        if isinstance(payload.get("future_hooks"), list):
            hooks = [str(h).strip()[:120] for h in payload["future_hooks"] if isinstance(h, str) and str(h).strip()]
            if not hooks:
                raise LifeDwellError("EMPTY_FUTURE_HOOKS", "At least 1 forward hook required (Ch.08 T3).", 400)
            st["future_hooks"] = hooks
        derived = derive_subjective_time_texture(
            focused_work_minutes=st["focused_work_minutes"],
            meaningful_event_count=st["meaningful_event_count"],
            idle_or_waiting_minutes=st["idle_or_waiting_minutes"],
            pillars=st["pillars"],
            future_hooks=st["future_hooks"],
        )
        st["last_mode"] = derived["mode"]
        receipt = {"action": "update_subjective_time", "subjective_time": derived}

    elif action == "trigger_everyday_glitch":
        glitch_kind = str(payload.get("glitch_kind") or "").strip()
        if glitch_kind not in EVERYDAY_GLITCH_KINDS:
            raise LifeDwellError("INVALID_GLITCH_KIND", f"Unknown glitch_kind: {glitch_kind}", 400)

        trace_id = _stable_id("glitch", glitch_kind, now_str, state["revision"] + 1)
        base_trace: dict[str, Any] = {
            "trace_id": trace_id,
            "glitch_kind": glitch_kind,
            "occurred_at": now_str,
            "archive_generated": False,
            "persona_tag_extracted": False,
            "methodology_extracted": False,
            "affection_delta": 0,
            "failure_penalty": 0,
        }

        if glitch_kind == "misplaced_item_search":
            item_id = str(payload.get("item_id") or "reading_glasses").strip()[:80]
            expected_spot = str(payload.get("expected_spot") or "study_desk").strip()[:80]
            actual_spot = str(payload.get("actual_spot") or "window_sill").strip()[:80]
            resolved = bool(payload.get("resolved", True))
            search_seconds = max(15, min(1800, int(payload.get("search_seconds") or 180)))
            base_trace.update(
                {
                    "physical_trace": "item_temporarily_misplaced_by_self",
                    "item_id": item_id,
                    "expected_spot": expected_spot,
                    "actual_spot": actual_spot,
                    "first_reach_result": "ITEM_NOT_AT_EXPECTED_SPOT",
                    "found_after_search": resolved,
                    "search_seconds": search_seconds,
                    "private_mutter": f"明明记得随手放在 {expected_spot}，翻了一圈原来在 {actual_spot}。",
                }
            )

        elif glitch_kind == "spilled_water_wipe_desk":
            surface_id = str(payload.get("surface_id") or "study_desk").strip()[:80]
            wiped = bool(payload.get("wiped_clean", True))
            base_trace.update(
                {
                    "physical_trace": "desk_wiped_clean" if wiped else "desk_damp_water_ring",
                    "surface_id": surface_id,
                    "wiped_clean": wiped,
                    "paper_safe_on_surface": wiped,
                    "cleanup_seconds": 90 if wiped else 0,
                }
            )

        elif glitch_kind == "forgot_umbrella_damp_coat":
            garment_id = str(payload.get("garment_id") or "coat_daily").strip()[:80]
            garment_label = str(payload.get("garment_label") or "日常外套").strip()[:40]
            chill_delta = 0.14
            prev_chill = derive_chill_load(state["chill_load"], now_dt)["projected_load"]
            new_chill = round(min(1.0, prev_chill + chill_delta), 4)
            state["chill_load"] = {
                "current_load": new_chill,
                "updated_at": now_str,
                "recovery_per_hour": 0.08,
                "source": "forgot_umbrella_damp_outerwear",
            }
            somatic_synced = _sync_damp_outerwear_if_available(
                root, garment_id=garment_id, label=garment_label, now_str=now_str
            )
            base_trace.update(
                {
                    "physical_trace": "outerwear_damp_from_rain",
                    "garment_id": garment_id,
                    "outerwear_dryness": "damp",
                    "chill_load_delta": chill_delta,
                    "chill_load_after": new_chill,
                    "somatic_outerwear_synced": somatic_synced,
                }
            )

        elif glitch_kind == "eraser_torn_page":
            target_ref = str(payload.get("target_ref") or "sketchbook_page_07").strip()[:80]
            base_trace.update(
                {
                    "physical_trace": "slight_paper_fiber_scuff_and_eraser_dent",
                    "target_ref": target_ref,
                    "origin_story_archived": False,
                    "usable_by_shifting_half_grid": True,
                }
            )

        elif glitch_kind == "playlist_typo":
            intended_title = str(payload.get("intended_title") or "晚风").strip()[:60]
            actual_title = str(payload.get("actual_title") or "碗风").strip()[:60]
            base_trace.update(
                {
                    "physical_trace": "playlist_title_typo_kept",
                    "intended_title": intended_title,
                    "displayed_title": actual_title,
                    "promoted_to_inside_joke_db": False,
                }
            )

        elif glitch_kind == "stone_polishing_settled_incomplete":
            object_id = str(payload.get("object_id") or "river_stones_tray").strip()[:80]
            completed_steps = max(1, int(payload.get("completed_units") or 7))
            remaining_steps = max(1, int(payload.get("remaining_units") or 3))
            base_trace.update(
                {
                    "physical_trace": "half_finished_stone_arrangement",
                    "object_id": object_id,
                    "completed_units": completed_steps,
                    "remaining_units": remaining_steps,
                    "terminal_state": "settled_incomplete",
                    "incomplete_debt": 0,
                    "auto_resume_scheduled": False,
                }
            )

        state["glitch_traces"].append(base_trace)
        state["glitch_traces"] = state["glitch_traces"][-MAX_GLITCH_TRACES:]
        receipt = {"action": "trigger_everyday_glitch", "glitch_trace": copy.deepcopy(base_trace)}

    elif action == "wipe_damp_surface":
        surface_id = str(payload.get("surface_id") or "study_desk").strip()[:80]
        updated_count = 0
        for tr in state["glitch_traces"]:
            if tr.get("glitch_kind") == "spilled_water_wipe_desk" and tr.get("surface_id") == surface_id:
                if not tr.get("wiped_clean"):
                    tr["wiped_clean"] = True
                    tr["paper_safe_on_surface"] = True
                    tr["physical_trace"] = "desk_wiped_clean"
                    updated_count += 1
        receipt = {
            "action": "wipe_damp_surface",
            "surface_id": surface_id,
            "paper_safe_on_surface": True,
            "updated_traces": updated_count,
        }

    else:
        raise LifeDwellError("UNKNOWN_ACTION", f"Unsupported life_dwell action: {action}", 400)

    state["revision"] = int(state["revision"]) + 1
    state["updated_at"] = now_str
    if op_key is not None:
        state["idempotency"][op_key] = {
            "fingerprint": fp_hash,
            "receipt": copy.deepcopy(receipt),
        }
    _atomic_write_dwell_state(root, state)

    proj = project_life_dwell(root, now_iso=now_dt, subject_view=subject_view)
    proj["read_only"] = False
    proj["status"] = "applied"
    proj["replayed"] = False
    proj["action_receipt"] = receipt
    return proj


__all__ = [
    "DEFAULT_DECLARED_DWELL_SECONDS",
    "DWELL_STATE_SCHEMA",
    "DWELL_VIEW_SCHEMA",
    "EVERYDAY_GLITCH_KINDS",
    "HOST_GAP_KIND",
    "HOST_GAP_LEGACY_ALIASES",
    "HOST_GAP_MIN_SECONDS",
    "LifeDwellError",
    "MAX_DECLARED_DWELL_SECONDS",
    "MIN_DECLARED_DWELL_SECONDS",
    "SUBJECTIVE_TIME_LABELS",
    "SUBJECTIVE_TIME_MODES",
    "apply_life_dwell_action",
    "default_life_dwell_state",
    "derive_chill_load",
    "derive_dwell_window",
    "derive_subjective_time_texture",
    "evaluate_host_gap",
    "life_dwell_state_path",
    "project_life_dwell",
    "read_life_dwell_state",
]
