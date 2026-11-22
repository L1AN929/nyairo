#!/usr/bin/env python3
"""Stage 3 Pillar 3: High-Threshold Lived Proactive Outreach Producer (Zero-LLM, Honest Outbox).

Converts genuinely lived, high-threshold life moments into bounded proactive
outreach entries routed strictly through the unified 7-step anti-harassment gate
(`v03_economy_illness_bridge.attempt_proactive_outbound(..., delivery_mode="honest_outbox")`).

Iron laws:
1. Zero LLM calls (`llm_calls == 0`), zero randomness, zero background threads.
2. NEVER fakes `DELIVERED` — every enqueued item lands as `APPROVED_FOR_DELIVERY`
   (`ack_state="QUEUED_FOR_TRANSPORT"`, `delivered_at=None`) until a real
   transport adapter confirms delivery via `ack_proactive_outbound_delivery`.
3. Strict Calm Gate & Pre-Enqueue Suppression (zero-write on refusal):
   - Retired (`RETIRED_ARCHIVE_SEALED`) or `DORMANT` instance -> suppressed;
   - `emotional_tension` `in_space` or open `US`/relational rupture -> suppressed;
   - `illness.phase == "acute_illness"` -> suppressed (except an explicit calm
     `gentle_rest_status_note` still subject to the full 7-step gate);
   - Sub-threshold / routine / private facts -> suppressed with zero disk writes;
   - All 7 anti-harassment guards (backoff, quiet hours, enabled channels,
     daily/7d/desktop-note/3rd-week caps, >=48h spacing, anti-blind-retry).
4. Pure LF, UTF-8 without BOM, zero `unicode.So` symbols, neutral `"TA"` phrasing.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
_PHONE_DIR = REPO_ROOT / "components" / "phone"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS), str(_LIFE_SCRIPTS), str(_PHONE_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.world.scripts import v03_economy_illness_bridge as v03_eco
except ImportError:  # pragma: no cover
    import v03_economy_illness_bridge as v03_eco  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.phone import emotional_tension_view as et_view
except ImportError:  # pragma: no cover
    try:
        import emotional_tension_view as et_view  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        et_view = None  # type: ignore[assignment]

try:
    from components.world.scripts import v05_living_town_engine as v05_town
except ImportError:  # pragma: no cover
    try:
        import v05_living_town_engine as v05_town  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        v05_town = None  # type: ignore[assignment]


SCHEMA_VERSION = "nyairo.life.proactive_outreach_producer.v1"
DEFAULT_TZ = timezone(timedelta(hours=8))

HIGH_THRESHOLD_OUTREACH_TRIGGERS: tuple[str, ...] = (
    "vivid_morning_dream",
    "rare_town_spectacle",
    "finished_craft_work",
    "sudden_weather_care",
    "matured_missing_note",
)
HIGH_THRESHOLD_TRIGGER_SET: frozenset[str] = frozenset(HIGH_THRESHOLD_OUTREACH_TRIGGERS)

OUTREACH_TTL_HOURS_BY_TRIGGER: dict[str, int] = {
    "sudden_weather_care": 4,
    "vivid_morning_dream": 12,
    "rare_town_spectacle": 12,
    "finished_craft_work": 48,
    "matured_missing_note": 48,
}
ACUTE_GENTLE_REST_TTL_HOURS = 12

_FORBIDDEN_MISSING_DOMAINS: frozenset[str] = frozenset({
    "intimate",
    "solo",
    "private_body",
    "sexual",
})
_SUDDEN_CARE_WEATHERS: frozenset[str] = frozenset({
    "rain",
    "heavy_rain",
    "storm",
    "drizzle",
    "snow",
})
_STRONG_HEARTBEAT_WEATHERS: frozenset[str] = frozenset({
    "rain",
    "heavy_rain",
    "storm",
    "snow",
})
_FINISHED_CRAFT_STATUSES: frozenset[str] = frozenset({
    "finished",
    "completed",
    "done",
    "finalized",
    "committed",
})
_SPOT_LABELS_CN: dict[str, str] = {
    "home": "窗边",
    "river_pier": "渡口木亭",
    "bakery_corner": "巷口面包铺旁",
    "old_bookshop": "旧书铺街角",
    "coin_laundromat": "烘衣房外",
    "hillside_overlook": "坡顶石阶",
    "blank_grove": "老树空地",
}


def _clean_single_line(raw: Any, *, max_len: int = 120) -> str:
    if not isinstance(raw, str):
        return ""
    chars: list[str] = []
    for ch in raw:
        if ch in ("\r", "\n", "\t", "\x00"):
            chars.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        chars.append(ch)
    return " ".join("".join(chars).split())[:max_len].strip()


def _stable_op_key(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"outreach_{prefix}_{digest}"[:120]


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return dict(parsed) if isinstance(parsed, Mapping) else None
    except Exception:
        return None


def _resolve_root(day_root: Path | str) -> Path:
    raw = Path(day_root)
    try:
        return _instance_paths.resolve_instance_paths(raw).day_root
    except Exception:
        return raw


def _check_runtime_governance_suppression_zero_write(root: Path) -> Optional[str]:
    rm_doc = _read_json_zero_write(root / "phone" / "v02_runtime_migration.json")
    if not isinstance(rm_doc, Mapping):
        return None
    retirement = rm_doc.get("retirement")
    if isinstance(retirement, Mapping) and str(retirement.get("status") or "") == "RETIRED_ARCHIVE_SEALED":
        return "INSTANCE_RETIRED"
    mode_cfg = rm_doc.get("mode_config")
    if isinstance(mode_cfg, Mapping) and str(mode_cfg.get("mode") or "") == "DORMANT":
        return "DORMANT_BLOCKED"
    return None


def _check_emotional_tension_suppression_zero_write(
    root: Path,
    *,
    now_iso: str,
) -> tuple[Optional[str], dict[str, Any]]:
    et_path = root / "phone" / "emotional_tension.json"
    if et_view is None or not et_path.is_file() or et_path.is_symlink():
        return None, {}
    try:
        proj = et_view.read_emotional_tension(root, now=now_iso, subject_view=True)
    except Exception:
        return None, {}
    if not isinstance(proj, Mapping):
        return None, {}
    quarrel = proj.get("quarrel") if isinstance(proj.get("quarrel"), Mapping) else {}
    ctx = proj.get("context") if isinstance(proj.get("context"), Mapping) else {}
    if bool(quarrel.get("in_space", False)):
        return "SUPPRESSED_BY_IN_SPACE", {"in_space": True}
    open_rel = list(quarrel.get("open_relational_rupture_ids") or [])
    if not open_rel:
        for rup in quarrel.get("ruptures") or []:
            if (
                isinstance(rup, Mapping)
                and str(rup.get("status") or "") in ("OPEN", "IN_REPAIR")
                and str(rup.get("scope") or "") in ("US", "EMOTION", "INTIMATE")
            ):
                open_rel.append(str(rup.get("rupture_id") or "rupture"))
    if open_rel:
        return "SUPPRESSED_BY_OPEN_RUPTURE", {"open_relational_rupture_ids": open_rel}
    if bool(ctx.get("partner_urgent", False)):
        return "SUPPRESSED_BY_PARTNER_URGENT", {"partner_urgent": True}
    if bool(ctx.get("private_scene_active", False)):
        return "SUPPRESSED_BY_PRIVATE_SCENE", {"private_scene_active": True}
    return None, {}


def _check_illness_suppression_zero_write(
    eco_state: Mapping[str, Any],
    context_facts: Mapping[str, Any],
) -> tuple[Optional[str], bool]:
    ill = eco_state.get("illness") if isinstance(eco_state.get("illness"), Mapping) else {}
    phase = str(ill.get("phase") or "healthy").strip().lower()
    if phase != "acute_illness":
        return None, False
    if bool(context_facts.get("gentle_rest_status_note")):
        return None, True
    return "SUPPRESSED_BY_ACUTE_ILLNESS", False


def _compose_trigger_message(
    root: Path,
    *,
    trigger_kind: str,
    context_facts: Mapping[str, Any],
    now_dt: datetime,
    now_iso: str,
    acute_gentle_rest_mode: bool,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Return ``(spec_dict, suppression_reason)`` without writing to disk."""
    if bool(context_facts.get("private")) or context_facts.get("shared") is False:
        return None, "PRIVATE_CONTENT_NOT_OUTREACHABLE"

    day_key = now_dt.strftime("%Y-%m-%d")

    if acute_gentle_rest_mode:
        custom_note = _clean_single_line(
            context_facts.get("rest_note_cn") or context_facts.get("gentle_rest_note_cn"),
            max_len=120,
        )
        text = custom_note or "今天有点着凉发沉，先在屋里多歇一会儿，不急着回话。"
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "acute_rest", day_key, trigger_kind
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "trigger_kind": "acute_gentle_rest",
            "text": text,
            "operation_key": op_key,
            "fact_ref": f"acute_rest:{day_key}",
            "note_id": None,
            "expires_at": v03_eco.format_wall_iso(now_dt + timedelta(hours=ACUTE_GENTLE_REST_TTL_HOURS)),
        }, None

    if trigger_kind == "vivid_morning_dream":
        if context_facts.get("dream_occurred") is False or bool(context_facts.get("calm_quiet")):
            return None, "CALM_GATE_BELOW_THRESHOLD"
        fragment = _clean_single_line(
            context_facts.get("fragment_cn") or context_facts.get("dream_fragment_cn"),
            max_len=110,
        )
        echoes = context_facts.get("source_echoes")
        has_echoes = isinstance(echoes, (list, tuple)) and len(echoes) > 0
        dream_id = _clean_single_line(context_facts.get("dream_id"), max_len=80)
        is_vivid = (
            bool(context_facts.get("vivid"))
            or bool(context_facts.get("kept_in_notebook"))
            or bool(context_facts.get("shared_with_partner"))
            or has_echoes
            or bool(dream_id)
        )
        if len(fragment) < 6 or not is_vivid:
            return None, "CALM_GATE_BELOW_THRESHOLD"
        text = f"刚醒来还记得一点梦里的片段：{fragment}"[:140]
        fact_ref = dream_id or f"dream:{day_key}"
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "dream", fact_ref
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "trigger_kind": "vivid_morning_dream",
            "text": text,
            "operation_key": op_key,
            "fact_ref": fact_ref,
            "note_id": None,
            "expires_at": v03_eco.format_wall_iso(
                now_dt + timedelta(hours=OUTREACH_TTL_HOURS_BY_TRIGGER["vivid_morning_dream"])
            ),
        }, None

    if trigger_kind == "rare_town_spectacle":
        spec_id = _clean_single_line(context_facts.get("spectacle_id"), max_len=80)
        title_cn = _clean_single_line(context_facts.get("title_cn"), max_len=48)
        desc_cn = _clean_single_line(context_facts.get("description_cn"), max_len=90)
        if not spec_id or (not title_cn and not desc_cn):
            return None, "CALM_GATE_BELOW_THRESHOLD"
        spot_id = _clean_single_line(context_facts.get("spot_id"), max_len=48)
        spot_label = _SPOT_LABELS_CN.get(spot_id, "小镇坡道边")
        if title_cn and desc_cn:
            text = f"刚才在{spot_label}碰见难得的「{title_cn}」，{desc_cn}"[:140]
        elif title_cn:
            text = f"刚才在{spot_label}碰见难得的「{title_cn}」，顺手记下一句。"[:140]
        else:
            text = f"刚才在{spot_label}看见一阵难得的景致：{desc_cn}"[:140]
        fact_ref = f"spectacle:{spec_id}:{day_key}"
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "spectacle", spec_id, day_key
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "trigger_kind": "rare_town_spectacle",
            "text": text,
            "operation_key": op_key,
            "fact_ref": fact_ref,
            "note_id": None,
            "expires_at": v03_eco.format_wall_iso(
                now_dt + timedelta(hours=OUTREACH_TTL_HOURS_BY_TRIGGER["rare_town_spectacle"])
            ),
        }, None

    if trigger_kind == "finished_craft_work":
        if context_facts.get("completed") is False:
            return None, "CALM_GATE_BELOW_THRESHOLD"
        status_norm = _clean_single_line(
            context_facts.get("status") or context_facts.get("step"),
            max_len=32,
        ).lower()
        is_completed = (
            context_facts.get("completed") is True
            or context_facts.get("is_finished") is True
            or status_norm in _FINISHED_CRAFT_STATUSES
        )
        title = _clean_single_line(
            context_facts.get("title")
            or context_facts.get("title_cn")
            or context_facts.get("work_title")
            or context_facts.get("item_title"),
            max_len=60,
        )
        if not is_completed or not title:
            return None, "CALM_GATE_BELOW_THRESHOLD"
        text = f"手边的「{title}」刚收尾放好了，顺手留张小便签记一笔。"[:140]
        work_ref = _clean_single_line(
            context_facts.get("work_id")
            or context_facts.get("artifact_id")
            or context_facts.get("card_id")
            or title,
            max_len=80,
        )
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "craft", work_ref, day_key
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "trigger_kind": "finished_craft_work",
            "text": text,
            "operation_key": op_key,
            "fact_ref": f"craft:{work_ref}",
            "note_id": None,
            "expires_at": v03_eco.format_wall_iso(
                now_dt + timedelta(hours=OUTREACH_TTL_HOURS_BY_TRIGGER["finished_craft_work"])
            ),
        }, None

    if trigger_kind == "sudden_weather_care":
        weather = _clean_single_line(context_facts.get("weather"), max_len=32).lower()
        prev_weather = _clean_single_line(context_facts.get("previous_weather"), max_len=32).lower()
        sudden_shift = bool(context_facts.get("sudden_shift")) or bool(context_facts.get("temperature_drop"))
        shifted_from_dry = prev_weather in ("clear", "breezy_cool", "cold_clear", "partly_cloudy") and weather in _SUDDEN_CARE_WEATHERS
        strong_weather = weather in _STRONG_HEARTBEAT_WEATHERS
        if weather not in _SUDDEN_CARE_WEATHERS or not (sudden_shift or shifted_from_dry or strong_weather):
            return None, "CALM_GATE_BELOW_THRESHOLD"
        if weather == "snow":
            base_text = "外头飘雪降温了，出门的话多添件厚外套。"
        elif weather == "drizzle":
            base_text = "外头突然飘起细雨，出门顺手留意外套别淋潮。"
        else:
            base_text = "外头突然落雨起风了，出门的话记得顺手带把伞。"
        note_hint = _clean_single_line(context_facts.get("weather_note_cn"), max_len=60)
        text = f"{base_text}{note_hint}"[:140] if note_hint else base_text
        slot_key = _clean_single_line(context_facts.get("weather_slot_key"), max_len=48) or f"{day_key}:{weather}"
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "weather", slot_key, weather
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "sudden_weather_care",
            "trigger_kind": "sudden_weather_care",
            "text": text,
            "operation_key": op_key,
            "fact_ref": f"weather:{slot_key}",
            "note_id": None,
            "expires_at": v03_eco.format_wall_iso(
                now_dt + timedelta(hours=OUTREACH_TTL_HOURS_BY_TRIGGER["sudden_weather_care"])
            ),
        }, None

    if trigger_kind == "matured_missing_note":
        note_id = _clean_single_line(context_facts.get("note_id"), max_len=80)
        summary = _clean_single_line(context_facts.get("summary"), max_len=110)
        sdom = _clean_single_line(context_facts.get("source_domain") or "daily_life", max_len=32).lower()
        suggested = _clean_single_line(context_facts.get("suggested_text_cn"), max_len=130)
        if sdom in _FORBIDDEN_MISSING_DOMAINS:
            return None, "INTIMATE_SOURCE_FORBIDDEN_IN_MISSING"
        if (not note_id or not summary) and et_view is not None:
            mat = et_view.mature_missing_partner_notes_for_outreach(
                root,
                now=now_iso,
                mark_surfaced=False,
            )
            if mat.get("suppressed"):
                return None, str(mat.get("suppression_reason") or "SUPPRESSED_BY_EMOTIONAL_TENSION")
            cand = mat.get("candidate")
            if isinstance(cand, Mapping):
                note_id = _clean_single_line(cand.get("note_id"), max_len=80)
                summary = _clean_single_line(cand.get("summary"), max_len=110)
                sdom = _clean_single_line(cand.get("source_domain") or "daily_life", max_len=32).lower()
                suggested = _clean_single_line(cand.get("suggested_text_cn"), max_len=130)
        if sdom in _FORBIDDEN_MISSING_DOMAINS:
            return None, "INTIMATE_SOURCE_FORBIDDEN_IN_MISSING"
        if not note_id or not summary:
            return None, "CALM_GATE_BELOW_THRESHOLD"
        text = suggested or f"整理手边时想起之前记下的小事：{summary}"[:140]
        op_key = _clean_single_line(context_facts.get("operation_key"), max_len=120) or _stable_op_key(
            "missing", note_id
        )
        return {
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "trigger_kind": "matured_missing_note",
            "text": text,
            "operation_key": op_key,
            "fact_ref": f"missing_note:{note_id}",
            "note_id": note_id,
            "expires_at": v03_eco.format_wall_iso(
                now_dt + timedelta(hours=OUTREACH_TTL_HOURS_BY_TRIGGER["matured_missing_note"])
            ),
        }, None

    return None, "UNKNOWN_OUTREACH_TRIGGER"


def evaluate_and_enqueue_lived_outreach(
    day_root: Path | str,
    *,
    trigger_kind: str,
    context_facts: Mapping[str, Any],
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate one high-threshold lived outreach trigger and enqueue via honest_outbox.

    Strictly zero-LLM, zero-write on refusal, and ALWAYS commits with
    ``delivery_mode="honest_outbox"`` (``status="APPROVED_FOR_DELIVERY"``).
    """
    norm_trigger = str(trigger_kind or "").strip()
    if norm_trigger not in HIGH_THRESHOLD_TRIGGER_SET:
        return {
            "ok": False,
            "enqueued": False,
            "suppressed": True,
            "reason_code": "UNKNOWN_OUTREACH_TRIGGER",
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "llm_calls": 0,
        }

    facts = dict(context_facts) if isinstance(context_facts, Mapping) else {}
    root = _resolve_root(day_root)
    now_dt = v03_eco.parse_iso_moment(now_iso)
    effective_now_iso = v03_eco.format_wall_iso(now_dt)

    # 1. Runtime governance suppression (retired / dormant) — zero-write
    gov_reason = _check_runtime_governance_suppression_zero_write(root)
    if gov_reason is not None:
        return {
            "ok": True,
            "enqueued": False,
            "suppressed": True,
            "reason_code": gov_reason,
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # 2. Emotional tension suppression (in_space / open US rupture / urgent / private scene) — zero-write
    et_reason, et_details = _check_emotional_tension_suppression_zero_write(root, now_iso=effective_now_iso)
    if et_reason is not None:
        return {
            "ok": True,
            "enqueued": False,
            "suppressed": True,
            "reason_code": et_reason,
            "details": et_details,
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # 3. Illness suppression (acute_illness suppressed unless explicit gentle_rest_status_note) — zero-write
    eco_state, eco_err = v03_eco.read_stored_ecology_zero_write(root)
    if eco_err is not None:
        return {
            "ok": False,
            "enqueued": False,
            "suppressed": True,
            "reason_code": eco_err,
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }
    ill_reason, acute_gentle_mode = _check_illness_suppression_zero_write(eco_state, facts)
    if ill_reason is not None:
        return {
            "ok": True,
            "enqueued": False,
            "suppressed": True,
            "reason_code": ill_reason,
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # 4. High-threshold Calm Gate & neutral message composition — zero-write
    spec, compose_reason = _compose_trigger_message(
        root,
        trigger_kind=norm_trigger,
        context_facts=facts,
        now_dt=now_dt,
        now_iso=effective_now_iso,
        acute_gentle_rest_mode=acute_gentle_mode,
    )
    if spec is None:
        return {
            "ok": True,
            "enqueued": False,
            "suppressed": True,
            "reason_code": compose_reason or "CALM_GATE_BELOW_THRESHOLD",
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # Optional channel override (must be valid proactive channel if provided)
    raw_channel = facts.get("channel")
    if isinstance(raw_channel, str) and raw_channel.strip() in v03_eco.PROACTIVE_CHANNELS:
        spec["channel"] = raw_channel.strip()

    # 5. Zero-write dedup check against existing operation_receipts
    receipts = eco_state.get("operation_receipts") if isinstance(eco_state.get("operation_receipts"), Mapping) else {}
    op_key = str(spec["operation_key"])
    if op_key in receipts:
        prior = receipts[op_key]
        return {
            "ok": True,
            "enqueued": False,
            "already_enqueued": True,
            "suppressed": False,
            "reason_code": "ALREADY_ENQUEUED",
            "trigger_kind": norm_trigger,
            "outbound_id": prior.get("outbound_id") if isinstance(prior, Mapping) else None,
            "outbound_status": prior.get("outbound_status") if isinstance(prior, Mapping) else None,
            "delivery_mode": "honest_outbox",
            "operation_key": op_key,
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # 6. Unified 7-step anti-harassment gate (strictly delivery_mode="honest_outbox")
    try:
        res = v03_eco.attempt_proactive_outbound(
            root,
            text=str(spec["text"]),
            channel=str(spec["channel"]),
            reason_kind=str(spec["reason_kind"]),
            delivery_mode="honest_outbox",
            operation_key=op_key,
            expires_at=spec.get("expires_at"),
            trigger_kind=spec.get("trigger_kind") or norm_trigger,
            fact_ref=spec.get("fact_ref"),
            now_iso=effective_now_iso,
        )
    except v03_eco.V03EcologyError as exc:
        return {
            "ok": True,
            "enqueued": False,
            "suppressed": True,
            "reason_code": exc.reason_code,
            "details": dict(exc.details),
            "trigger_kind": norm_trigger,
            "delivery_mode": "honest_outbox",
            "observed_at": effective_now_iso,
            "llm_calls": 0,
        }

    # 7. Mark matured missing_partner note as surfaced for outreach so heartbeat won't re-emit
    if norm_trigger == "matured_missing_note" and spec.get("note_id") and et_view is not None:
        try:
            et_view.mature_missing_partner_notes_for_outreach(
                root,
                now=effective_now_iso,
                mark_surfaced=True,
            )
        except Exception:
            pass

    receipt = res.get("receipt") if isinstance(res.get("receipt"), Mapping) else {}
    return {
        "ok": True,
        "enqueued": True,
        "already_enqueued": res.get("command_status") == "DUPLICATE",
        "suppressed": False,
        "reason_code": None,
        "trigger_kind": norm_trigger,
        "outbound_id": receipt.get("outbound_id"),
        "outbound_status": receipt.get("outbound_status", "APPROVED_FOR_DELIVERY"),
        "ack_state": receipt.get("ack_state", "QUEUED_FOR_TRANSPORT"),
        "delivered_at": receipt.get("delivered_at"),
        "expires_at": receipt.get("expires_at") or spec.get("expires_at"),
        "delivery_mode": "honest_outbox",
        "channel": str(spec["channel"]),
        "text": str(spec["text"]),
        "fact_ref": str(spec["fact_ref"]),
        "operation_key": op_key,
        "command_status": res.get("command_status"),
        "observed_at": effective_now_iso,
        "llm_calls": 0,
    }


def _fast_outbox_gate_check_zero_write(
    eco_state: Mapping[str, Any],
    now_dt: datetime,
) -> Optional[str]:
    rb = eco_state.get("reality_bridge") if isinstance(eco_state.get("reality_bridge"), Mapping) else {}
    policy = rb.get("outbound_policy") if isinstance(rb.get("outbound_policy"), Mapping) else {}
    backoff_limit = int(policy.get("backoff_streak_limit") or v03_eco.DEFAULT_BACKOFF_STREAK_LIMIT)
    if int(rb.get("unanswered_streak") or 0) >= backoff_limit:
        return "PROACTIVE_PAUSED_BY_BACKOFF"
    q_start = str(policy.get("quiet_start_hhmm") or v03_eco.DEFAULT_QUIET_START_HHMM)
    q_end = str(policy.get("quiet_end_hhmm") or v03_eco.DEFAULT_QUIET_END_HHMM)
    if v03_eco._is_quiet_hours(now_dt, q_start, q_end):
        return "QUIET_HOURS_ACTIVE"
    min_interval_hours = float(
        policy["min_outbound_interval_hours"]
        if policy.get("min_outbound_interval_hours") is not None
        else v03_eco.DEFAULT_MIN_OUTBOUND_INTERVAL_HOURS
    )
    last_dt: Optional[datetime] = None
    for rec in rb.get("outbound_records") or []:
        if not isinstance(rec, Mapping):
            continue
        if rec.get("status") not in v03_eco.OUTBOUND_QUOTA_COUNTING_STATUSES:
            continue
        rec_dt = v03_eco._parse_stored_iso(rec.get("created_at"))
        if rec_dt is not None and (last_dt is None or rec_dt > last_dt):
            last_dt = rec_dt
    if last_dt is not None:
        elapsed_h = (now_dt - last_dt).total_seconds() / 3600.0
        if elapsed_h < min_interval_hours:
            return "OUTBOUND_INTERVAL_TOO_SOON"
    return None


def evaluate_heartbeat_outreach_triggers(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bounded zero-LLM heartbeat scanner for genuine high-threshold outreach triggers.

    100% zero-write on uninitialized roots (when ``phone/v03_ecology.json`` is absent)
    and 100% calm-silent on freshly bootstrapped baseline roots with no lived sparks.
    Enqueues at most 1 item per tick, always via ``delivery_mode="honest_outbox"``.
    """
    root = _resolve_root(day_root)
    eco_path = v03_eco.ecology_state_path(root)
    if not eco_path.is_file() or eco_path.is_symlink():
        return {
            "ok": True,
            "status": "skipped_uninitialized",
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    now_dt = v03_eco.parse_iso_moment(now_iso)
    effective_now_iso = v03_eco.format_wall_iso(now_dt)

    gov_reason = _check_runtime_governance_suppression_zero_write(root)
    if gov_reason is not None:
        return {
            "ok": True,
            "status": "suppressed",
            "reason_code": gov_reason,
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    eco_state, eco_err = v03_eco.read_stored_ecology_zero_write(root)
    if eco_err is not None:
        return {
            "ok": False,
            "status": "degraded",
            "reason_code": eco_err,
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    ill = eco_state.get("illness") if isinstance(eco_state.get("illness"), Mapping) else {}
    if str(ill.get("phase") or "healthy").strip().lower() == "acute_illness":
        return {
            "ok": True,
            "status": "suppressed",
            "reason_code": "SUPPRESSED_BY_ACUTE_ILLNESS",
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    gate_fast_reason = _fast_outbox_gate_check_zero_write(eco_state, now_dt)
    if gate_fast_reason is not None:
        return {
            "ok": True,
            "status": "suppressed",
            "reason_code": gate_fast_reason,
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    et_reason, _ = _check_emotional_tension_suppression_zero_write(root, now_iso=effective_now_iso)
    if et_reason is not None:
        return {
            "ok": True,
            "status": "suppressed",
            "reason_code": et_reason,
            "evaluated_triggers": [],
            "enqueued_count": 0,
            "enqueued": [],
            "zero_write": True,
            "llm_calls": 0,
        }

    evaluated: list[str] = []
    candidates: list[tuple[str, dict[str, Any]]] = []
    today_key = now_dt.strftime("%Y-%m-%d")

    # 1. Vivid morning dream (life/v05_inner_spark.json)
    spark_doc = _read_json_zero_write(root / "life" / "v05_inner_spark.json")
    if isinstance(spark_doc, Mapping):
        evaluated.append("vivid_morning_dream")
        sw = spark_doc.get("sleep_wake_state")
        dream = sw.get("morning_dream") if isinstance(sw, Mapping) else None
        if isinstance(dream, Mapping):
            fades_after = int(dream.get("fades_after_hour") or 12)
            if (
                str(dream.get("date_key") or "") == today_key
                and now_dt.hour < fades_after
                and _clean_single_line(dream.get("fragment_cn"), max_len=110)
            ):
                candidates.append(("vivid_morning_dream", dict(dream)))

    # 2. Rare town spectacle (world/v05_living_town.json — only when TA actually moved/visited town)
    town_doc = _read_json_zero_write(root / "world" / "v05_living_town.json")
    if isinstance(town_doc, Mapping) and int(town_doc.get("revision") or 0) > 0 and v05_town is not None:
        evaluated.append("rare_town_spectacle")
        has_town_activity = bool(town_doc.get("edge_walk_counts")) or bool(
            (town_doc.get("third_spaces") or {}).get("recent_third_space_visits")
            if isinstance(town_doc.get("third_spaces"), Mapping)
            else False
        )
        try:
            snap = v05_town.read_living_town_snapshot(root, now_iso=effective_now_iso)
            ta_loc = str(((snap.get("ta_presence") or {}).get("location_id")) or "home")
            spec = snap.get("rare_spectacle")
            if (has_town_activity or ta_loc != "home") and isinstance(spec, Mapping) and spec.get("spectacle_id"):
                candidates.append(("rare_town_spectacle", dict(spec)))
        except Exception:
            pass

    # 3. Finished craft work & sudden weather care (world/v04_neighborhood_state.json)
    v04_doc = _read_json_zero_write(root / "world" / "v04_neighborhood_state.json")
    if isinstance(v04_doc, Mapping) and int(v04_doc.get("revision") or 0) > 0:
        cb = v04_doc.get("craft_bench")
        if isinstance(cb, Mapping):
            evaluated.append("finished_craft_work")
            box = cb.get("card_organizer_box")
            if isinstance(box, Mapping) and str(box.get("step") or "").strip().lower() == "finished":
                upd_dt = v03_eco._parse_stored_iso(box.get("updated_at"))
                if upd_dt is not None and 0.0 <= (now_dt - upd_dt).total_seconds() <= 24 * 3600:
                    candidates.append((
                        "finished_craft_work",
                        {
                            "work_id": f"card_organizer_box:{box.get('updated_at')}",
                            "title": "手工分格收纳盒",
                            "completed": True,
                            "status": "finished",
                        },
                    ))
        env = v04_doc.get("environment")
        if isinstance(env, Mapping):
            evaluated.append("sudden_weather_care")
            weather = str(env.get("weather") or "clear").strip().lower()
            slot_key = env.get("weather_slot_key")
            if weather in _STRONG_HEARTBEAT_WEATHERS and isinstance(slot_key, str) and slot_key.strip():
                candidates.append((
                    "sudden_weather_care",
                    {
                        "weather": weather,
                        "weather_slot_key": slot_key.strip(),
                        "sudden_shift": True,
                    },
                ))

    # 4. Matured missing_partner note (phone/emotional_tension.json)
    et_path = root / "phone" / "emotional_tension.json"
    if et_view is not None and et_path.is_file() and not et_path.is_symlink():
        evaluated.append("matured_missing_note")
        try:
            mat = et_view.mature_missing_partner_notes_for_outreach(
                root,
                now=effective_now_iso,
                mark_surfaced=False,
            )
            cand = mat.get("candidate") if not mat.get("suppressed") else None
            if isinstance(cand, Mapping) and cand.get("note_id"):
                candidates.append(("matured_missing_note", dict(cand)))
        except Exception:
            pass

    enqueued: list[dict[str, Any]] = []
    for t_kind, t_facts in candidates:
        res = evaluate_and_enqueue_lived_outreach(
            root,
            trigger_kind=t_kind,
            context_facts=t_facts,
            now_iso=effective_now_iso,
        )
        if res.get("enqueued"):
            enqueued.append(res)
            break

    return {
        "ok": True,
        "status": "enqueued" if enqueued else "calm_noop",
        "observed_at": effective_now_iso,
        "evaluated_triggers": evaluated,
        "enqueued_count": len(enqueued),
        "enqueued": enqueued,
        "zero_write": len(enqueued) == 0,
        "llm_calls": 0,
    }


__all__ = [
    "HIGH_THRESHOLD_OUTREACH_TRIGGERS",
    "HIGH_THRESHOLD_TRIGGER_SET",
    "SCHEMA_VERSION",
    "evaluate_and_enqueue_lived_outreach",
    "evaluate_heartbeat_outreach_triggers",
]
