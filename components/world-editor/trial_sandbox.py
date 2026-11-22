#!/usr/bin/env python3
"""v0.4 Ch.44 isolated try-living sandbox, builder provenance and lived-safe merge.

Three deterministic, zero-network, zero-LLM (``llm_calls == 0``) tools for the
world editor's loopback server:

* :func:`run_isolated_trial_hour` -- run one honest 1-hour "try living" hour
  against an *isolated* ``tempfile.TemporaryDirectory``.  It writes a minimal
  sandbox (the validated v3 package, neighbourhood weather, micro-zone window)
  then drives the shipped ``v08_universal_matter`` / ``v08_microzone_ecology``
  physics.  Nothing under the real lived instance directory is read or touched.
* :func:`explain_matter_provenance_for_builder` -- the builder (admin) view of
  an object's real physical provenance, kept strictly separate from the small
  physical surface the character naturally perceives.
* :func:`merge_package_preserving_lived_matter` -- non-destructive diff merge of
  a newly authored editor package over a lived instance, refusing to delete a
  room/zone/object that still holds lived matter.

Standard library plus existing repo modules only.  Reads are zero-write; the
only write in this module is an explicit ``dry_run=False`` merge write.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from model import WorldError, validate_package

# The matter/ecology modules live in ``components/world/scripts``; make the repo
# root importable no matter which working directory launched the editor.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOCAL_TZ = timezone(timedelta(hours=8))
TRIAL_DAY = datetime(2026, 11, 16, tzinfo=LOCAL_TZ)

WEATHER_CHOICES: tuple[str, ...] = ("clear", "drizzle", "rain")
WINDOW_STATES: tuple[str, ...] = ("closed", "ajar", "open")
SEASON_CHOICES: tuple[str, ...] = ("spring", "summer", "autumn", "winter")

_WEATHER_CN = {"clear": "晴天", "drizzle": "下着小雨", "rain": "在下雨"}
_SEASON_CN = {"spring": "春天", "summer": "夏天", "autumn": "秋天", "winter": "冬天"}
_WINDOW_CN = {"closed": "关着", "ajar": "半开着", "open": "敞开着"}
_LIGHT_CN = {
    "comfortable": "光线舒适",
    "glare": "光线有些刺眼",
    "dim": "光线偏暗",
    "soft_lamp": "台灯打出柔和的光",
}
_DRAFT_CN = {
    "calm": "空气很安静",
    "breeze": "有一点微风",
    "chilly_draft": "有股凉风",
    "rain_splash": "有雨点潲进来",
}
_MOISTURE_CN = {"dry": "干爽", "damp": "有点发潮", "wet": "已经湿了"}
_LEGIBILITY_CN = {"clear": "字迹清楚", "smudged": "字迹晕开了"}


# ---------------------------------------------------------------------------
# Lazy repo-module access
# ---------------------------------------------------------------------------


def _matter() -> Any:
    from components.world.scripts import v08_universal_matter

    return v08_universal_matter


def _microzone() -> Any:
    from components.world.scripts import v08_microzone_ecology

    return v08_microzone_ecology


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Parse an existing JSON file without creating or touching anything."""

    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _as_world_error(exc: Exception) -> WorldError:
    """Map a v08 domain refusal to the editor's WorldError contract."""

    code = str(getattr(exc, "reason_code", "") or "MATTER_REFUSED")
    status = int(getattr(exc, "status_code", 0) or 422)
    return WorldError(str(exc), code, status)


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _norm_weather(value: Any) -> str:
    if value is None:
        return "clear"
    if not isinstance(value, str) or value.strip() not in WEATHER_CHOICES:
        raise WorldError(
            "试住天气需要是 clear、drizzle 或 rain。", "INVALID_WEATHER", 422
        )
    return value.strip()


def _norm_window(value: Any) -> str:
    if value is None:
        return "closed"
    if not isinstance(value, str) or value.strip() not in WINDOW_STATES:
        raise WorldError(
            "窗户状态需要是 closed、ajar 或 open。", "INVALID_WINDOW_STATE", 422
        )
    return value.strip()


def _norm_season(value: Any) -> str:
    if value is None:
        return "autumn"
    if not isinstance(value, str) or value.strip() not in SEASON_CHOICES:
        raise WorldError(
            "试住季节需要是 spring、summer、autumn 或 winter。", "INVALID_SEASON", 422
        )
    return value.strip()


def _norm_hour(value: Any) -> int:
    if value is None:
        return 14
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise WorldError("试住时刻需要是 0–23 的整数。", "INVALID_HOUR", 422)
    return int(value)


def _norm_note(value: Any) -> str:
    if value is None:
        return "窗边晾着一张刚抄完的卡片"
    if not isinstance(value, str) or not value.strip():
        raise WorldError("试住卡片文字需要是非空文字。", "INVALID_NOTE", 422)
    return value.strip()[:120]


# ---------------------------------------------------------------------------
# Isolated sandbox
# ---------------------------------------------------------------------------


def _seed_sandbox(
    root: Path,
    package: Mapping[str, Any],
    *,
    weather: str,
    window_state: str,
    season: str,
    matter: Any,
) -> None:
    """Write the minimal isolated sandbox; never touches any real instance."""

    package_dir = root / "world-package"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "editor_package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    world_dir = root / "world"
    world_dir.mkdir(parents=True, exist_ok=True)
    (world_dir / "v04_neighborhood_state.json").write_text(
        json.dumps(
            {"environment": {"weather": weather, "airflow": "medium", "season": season}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (world_dir / "v08_microzone_state.json").write_text(
        json.dumps({"window_state": window_state}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # A clean sandbox space: default zones, no matter, and a slightly wider desk
    # so the trial card can rest there without evicting anyone's things.
    (world_dir / "v08_matter_space.json").write_text(
        json.dumps(
            {
                "schema_version": matter.SCHEMA_VERSION,
                "revision": 0,
                "zones": {
                    "home_desk": {
                        "zone_id": "home_desk",
                        "label_cn": "书桌台面",
                        "ambient_zone": "room",
                        "water_source": False,
                        "capacity_slots": 8,
                        "visibility": "shared",
                        "surface_cleanliness": "clean",
                    }
                },
                "matter_objects": {},
                "recent_acts": [],
                "operations": {},
                "discards": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _pick_trial_zone(view: Mapping[str, Any]) -> str:
    """Prefer the desk we know; fall back to an authored desk-like zone."""

    zones = [z for z in (view.get("zones") or []) if isinstance(z, Mapping)]
    zone_ids = {str(z.get("zone_id") or "") for z in zones}
    if "home_desk" in zone_ids:
        return "home_desk"
    for zone in zones:
        zone_id = str(zone.get("zone_id") or "")
        label = str(zone.get("label_cn") or "")
        if not zone_id:
            continue
        if "桌" in label or "desk" in zone_id.lower() or "table" in zone_id.lower():
            return zone_id
    return sorted(zone_ids - {""})[0] if len(zone_ids) > 1 else "home_desk"


def _zone_label(view: Mapping[str, Any], zone_id: str) -> str:
    for zone in view.get("zones") or []:
        if isinstance(zone, Mapping) and str(zone.get("zone_id") or "") == zone_id:
            return str(zone.get("label_cn") or zone_id)
    return zone_id


def _spot_observation(spot: Mapping[str, Any]) -> str:
    light = _LIGHT_CN.get(str(spot.get("light_quality") or ""), "光线平常")
    draft = _DRAFT_CN.get(str(spot.get("draft_state") or ""), "空气安静")
    return f"{light}，{draft}"


def _run_trial_once(
    package: Mapping[str, Any],
    *,
    weather: str,
    window_state: str,
    season: str,
    hour: int,
    seed_note_cn: str,
) -> dict[str, Any]:
    matter = _matter()
    micro = _microzone()

    start_dt = TRIAL_DAY.replace(hour=hour)
    end_dt = start_dt + timedelta(hours=1)
    start_text = start_dt.isoformat()
    end_text = end_dt.isoformat()

    with tempfile.TemporaryDirectory(prefix="v08_ch44_trial_") as tmp:
        root = Path(tmp)
        _seed_sandbox(
            root,
            package,
            weather=weather,
            window_state=window_state,
            season=season,
            matter=matter,
        )

        initial_view = matter.read_matter_space_view(root, now_iso=start_text)
        zone_id = _pick_trial_zone(initial_view)

        try:
            receipt = matter.compose_open_act(
                root,
                {
                    "intent_cn": "把一张刚抄完的卡片先放在桌上",
                    "steps": [
                        {
                            "op": "INTRODUCE_ITEM",
                            "label_cn": "试住纸片",
                            "item_kind": "paper",
                            "target_parent_id": zone_id,
                            "paper_like": True,
                            "inscribed_text_cn": seed_note_cn,
                        }
                    ],
                    "operation_key": f"ch44-trial-{weather}-{window_state}-{hour:02d}",
                },
                now_iso=start_text,
            )
            card_id = str(receipt["step_results"][0]["object_id"])
        except Exception as exc:  # noqa: BLE001 - map a named domain refusal
            if getattr(exc, "reason_code", None):
                raise _as_world_error(exc) from exc
            raise

        settle_counts = matter.settle_matter_physics(root, now_iso=end_text)
        view = matter.read_matter_space_view(root, now_iso=end_text)
        conditions = micro.resolve_local_conditions_zero_write(root, now_iso=end_text)

    objects = [o for o in (view.get("objects") or []) if isinstance(o, Mapping)]
    zones = [z for z in (view.get("zones") or []) if isinstance(z, Mapping)]
    note_row = next((o for o in objects if str(o.get("object_id") or "") == card_id), {})
    moisture = str(note_row.get("moisture") or "dry")
    legibility = note_row.get("note_legibility") or "clear"

    raw_hint = conditions.get("seasonal_hint_cn")
    seasonal_hint_cn = (
        str(raw_hint).strip()
        if isinstance(raw_hint, str) and raw_hint.strip()
        else None
    )
    season_note_cn = seasonal_hint_cn or (
        f"眼下是{_SEASON_CN[season]}，屋里屋外都按这个季节的温度和风来算"
    )

    spots = {
        str(s.get("spot_id") or ""): s
        for s in (conditions.get("spots") or [])
        if isinstance(s, Mapping)
    }
    window_spot = spots.get("window_side") or {}
    room_spot = spots.get("desk_inner") or {}

    discovered_zones = [
        {
            "zone_id": str(z.get("zone_id") or ""),
            "label_cn": z.get("label_cn"),
            "capacity_slots": int(z.get("capacity_slots") or 0),
            "occupancy": int(z.get("occupancy") or 0),
        }
        for z in zones
    ]
    discovered_objects = [
        {
            "object_id": str(o.get("object_id") or ""),
            "label_cn": o.get("label_cn"),
            "item_kind": o.get("item_kind"),
            "material_units": int(o.get("material_units") or 1),
            "assembly_role": o.get("assembly_role") or "none",
        }
        for o in objects
    ]

    trial_steps_cn = [
        f"在{hour:02d}点，把一张写着「{seed_note_cn}」的纸片放到{_zone_label(view, zone_id)}上。",
        f"窗外{_WEATHER_CN[weather]}，窗户{_WINDOW_CN[window_state]}；窗边{_spot_observation(window_spot)}。{season_note_cn}。",
        f"过了一个小时，纸片摸起来{_MOISTURE_CN.get(moisture, moisture)}，{_LEGIBILITY_CN.get(legibility, legibility)}。",
        f"这次隔离试住里一共看到 {len(discovered_zones)} 处可放东西的台面和 {len(discovered_objects)} 件物件。",
        "全程都在临时沙箱里进行，真实的生活状态没有被改动。",
    ]

    return {
        "hour": hour,
        "weather": weather,
        "window_state": window_state,
        "season": season,
        "seasonal_hint_cn": seasonal_hint_cn,
        "trial_zone_id": zone_id,
        "trial_card_object_id": card_id,
        "paper_note_state": {
            "object_id": card_id,
            "moisture": moisture,
            "note_legibility": legibility,
        },
        "microzone_summary": {
            "hour": hour,
            "weather": weather,
            "window_state": window_state,
            "season": season,
            "seasonal_hint_cn": season_note_cn,
            "window_side": {
                "light_quality": window_spot.get("light_quality"),
                "draft_state": window_spot.get("draft_state"),
                "observation_cn": _spot_observation(window_spot),
            },
            "room_side": {
                "spot_id": "desk_inner",
                "light_quality": room_spot.get("light_quality"),
                "draft_state": room_spot.get("draft_state"),
                "observation_cn": _spot_observation(room_spot),
            },
        },
        "discovered_zones": discovered_zones,
        "discovered_objects": discovered_objects,
        "settle_counts": dict(settle_counts),
        "trial_steps_cn": trial_steps_cn,
    }


def _ab_diff(base: Mapping[str, Any], compare: Mapping[str, Any]) -> dict[str, Any]:
    base_light = (base["microzone_summary"]["window_side"] or {}).get("light_quality")
    compare_light = (compare["microzone_summary"]["window_side"] or {}).get("light_quality")
    base_draft = (base["microzone_summary"]["window_side"] or {}).get("draft_state")
    compare_draft = (compare["microzone_summary"]["window_side"] or {}).get("draft_state")
    light_changed = bool(base_light != compare_light or base_draft != compare_draft)

    zone_delta = len(compare["discovered_zones"]) - len(base["discovered_zones"])
    object_delta = len(compare["discovered_objects"]) - len(base["discovered_objects"])
    legibility_changed = base["paper_note_state"]["note_legibility"] != compare["paper_note_state"]["note_legibility"]

    zone_word = "多" if zone_delta >= 0 else "少"
    object_word = "多" if object_delta >= 0 else "少"
    diff_summary_cn = (
        f"对比世界比原世界{zone_word}了 {abs(zone_delta)} 处台面、"
        f"{object_word}了 {abs(object_delta)} 件物件；"
        + ("窗边光线或风感不一样；" if light_changed else "窗边光线和风感一样；")
        + ("纸片字迹的清晰程度不同。" if legibility_changed else "纸片字迹的清晰程度相同。")
    )
    return {
        "light_changed": light_changed,
        "zone_count_delta": zone_delta,
        "object_count_delta": object_delta,
        "legibility_changed": legibility_changed,
        "diff_summary_cn": diff_summary_cn,
    }


def run_isolated_trial_hour(
    package: Any,
    *,
    weather: Any = "clear",
    window_state: Any = "closed",
    season: Any = "autumn",
    hour: Any = 14,
    compare_package: Any = None,
    seed_note_cn: Any = "窗边晾着一张刚抄完的卡片",
) -> dict[str, Any]:
    """Run one honest isolated try-living hour and (optionally) an A/B diff."""

    validated, _warnings = validate_package(package)
    weather_text = _norm_weather(weather)
    window_text = _norm_window(window_state)
    season_text = _norm_season(season)
    hour_int = _norm_hour(hour)
    note_text = _norm_note(seed_note_cn)

    base = _run_trial_once(
        validated,
        weather=weather_text,
        window_state=window_text,
        season=season_text,
        hour=hour_int,
        seed_note_cn=note_text,
    )

    ab_diff = None
    if compare_package is not None:
        compare_validated, _ = validate_package(compare_package)
        compare = _run_trial_once(
            compare_validated,
            weather=weather_text,
            window_state=window_text,
            season=season_text,
            hour=hour_int,
            seed_note_cn=note_text,
        )
        ab_diff = _ab_diff(base, compare)

    result = dict(base)
    result.update(
        {
            "status": "trial_ok",
            "ab_diff": ab_diff,
            "isolated_sandbox": True,
            "real_lived_state_touched": False,
            "llm_calls": 0,
        }
    )
    return result


# ---------------------------------------------------------------------------
# Builder provenance vs character surface view (Ch.44 §五)
# ---------------------------------------------------------------------------


def _zone_of_raw(state: Mapping[str, Any], obj: Mapping[str, Any]) -> Optional[str]:
    zones = state.get("zones") if isinstance(state.get("zones"), Mapping) else {}
    objects = state.get("matter_objects") if isinstance(state.get("matter_objects"), Mapping) else {}
    parent = obj.get("parent_id")
    for _ in range(8):
        if not parent:
            return None
        pid = str(parent)
        if isinstance(zones.get(pid), Mapping):
            return pid
        nxt = objects.get(pid)
        if not isinstance(nxt, Mapping):
            return None
        parent = nxt.get("parent_id")
    return None


def _recent_mentions(
    state: Mapping[str, Any],
    object_id: str,
    label_cn: Optional[str],
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    operations = state.get("operations")
    if isinstance(operations, Mapping):
        for key, entry in operations.items():
            receipt = entry.get("receipt") if isinstance(entry, Mapping) else None
            if not isinstance(receipt, Mapping):
                continue
            steps = [s for s in (receipt.get("step_results") or []) if isinstance(s, Mapping)]
            hit = any(
                object_id
                in {
                    str(step.get("object_id") or ""),
                    str(step.get("target_parent_id") or ""),
                    str(step.get("target_id") or ""),
                }
                for step in steps
            )
            if not hit:
                continue
            mentions.append(
                {
                    "source": "operation_receipt",
                    "operation_key": str(key),
                    "at": receipt.get("at"),
                    "intent_cn": receipt.get("intent_cn"),
                    "ops": [str(step.get("op") or "") for step in steps],
                }
            )

    acts = [a for a in (state.get("recent_acts") or []) if isinstance(a, Mapping)]
    if label_cn:
        for act in acts:
            intent = str(act.get("intent_cn") or "")
            if label_cn and label_cn in intent:
                mentions.append({"source": "recent_acts", "at": act.get("at"), "intent_cn": intent})

    mentions.sort(key=lambda row: str(row.get("at") or ""))
    return mentions[-6:]


def explain_matter_provenance_for_builder(
    instance_root: Path | str,
    object_id: str,
) -> dict[str, Any]:
    """Explain where an object really is for the builder; keep the character small.

    Every read is zero-write.  The character surface view carries only what the
    character physically perceives -- builder/admin provenance never leaks in.
    """

    oid = str(object_id or "").strip()
    if not oid:
        raise WorldError("需要提供物件 ID（object_id）。", "INVALID_OBJECT_ID", 422)

    root = Path(instance_root)
    matter = _matter()
    micro = _microzone()
    now_text = datetime.now(tz=LOCAL_TZ).isoformat(timespec="seconds")

    view = matter.read_matter_space_view(root, actor_side="subject", now_iso=now_text)
    row = next(
        (o for o in (view.get("objects") or []) if str(o.get("object_id") or "") == oid),
        None,
    )
    if row is None:
        raise WorldError(f"找不到物件：{oid}", "NOT_FOUND", 404)

    raw_state = _read_json_zero_write(matter.state_path(root)) or {}
    raw_objects = raw_state.get("matter_objects") if isinstance(raw_state.get("matter_objects"), Mapping) else {}
    raw_obj = raw_objects.get(oid) if isinstance(raw_objects, Mapping) else None
    raw_obj = dict(raw_obj) if isinstance(raw_obj, Mapping) else {}

    conditions = micro.resolve_local_conditions_zero_write(root, now_iso=now_text)
    neighborhood = _read_json_zero_write(root / "world" / "v04_neighborhood_state.json") or {}
    env = neighborhood.get("environment") if isinstance(neighborhood.get("environment"), Mapping) else {}
    weather = str(env.get("weather") or conditions.get("weather") or "clear")

    zone_id = _zone_of_raw(raw_state, raw_obj) or (
        str(row.get("parent_id") or "") if row.get("parent_id") else None
    )
    zone_label = None
    for zone in view.get("zones") or []:
        if isinstance(zone, Mapping) and str(zone.get("zone_id") or "") == str(zone_id or ""):
            zone_label = zone.get("label_cn")
            break

    factors: list[str] = []
    if weather in ("drizzle", "rain"):
        factors.append(f"外面{_WEATHER_CN[weather]}")
    window_state = str(conditions.get("window_state") or "closed")
    if window_state in ("ajar", "open") and bool(row.get("paper_like")):
        factors.append(f"窗户{_WINDOW_CN[window_state]}，潮气容易进来")
    if zone_label:
        for zone in view.get("zones") or []:
            if isinstance(zone, Mapping) and str(zone.get("zone_id") or "") == str(zone_id or ""):
                ambient = str(zone.get("ambient_zone") or "room")
                if ambient == "cold":
                    factors.append("放在冷藏区里")
                elif ambient == "heating":
                    factors.append("靠近加热热源")
                break

    mentions = _recent_mentions(raw_state, oid, row.get("label_cn"))
    seen_parent = raw_obj.get("subject_last_seen_parent")
    impression_stale = bool(row.get("impression_stale") or raw_obj.get("impression_stale"))
    moved = seen_parent is not None and str(seen_parent) != str(row.get("parent_id") or "")

    if (impression_stale or moved) and not mentions:
        missing_note = (
            "物件现在的位置和主体上次看到的位置不一样；中间这段挪动没有留下操作记录，"
            "所以这里不编造它怎么到这里的故事。"
        )
    elif not mentions:
        missing_note = "这个物件还没有留下任何操作记录（可能一开始就在那里）；没有记录就不补写来历。"
    else:
        missing_note = None

    builder = {
        "object_id": oid,
        "label_cn": row.get("label_cn"),
        "item_kind": row.get("item_kind"),
        "parent_zone_id": zone_id,
        "parent_zone_label_cn": zone_label,
        "moisture": row.get("moisture"),
        "thermal": row.get("thermal"),
        "freshness": row.get("freshness"),
        "last_thermal_at": raw_obj.get("last_thermal_at"),
        "last_moisture_at": raw_obj.get("last_moisture_at"),
        "subject_last_seen_parent": seen_parent,
        "impression_stale": impression_stale,
        "weather": weather,
        "airflow": conditions.get("airflow"),
        "window_state": window_state,
        "weather_factors_cn": factors,
        "recent_acts_mentioning_object": mentions,
        "missing_history_note_cn": missing_note,
    }

    character = {
        "label_cn": row.get("label_cn"),
        "parent_id": row.get("parent_id"),
        "moisture": row.get("moisture"),
        "thermal": row.get("thermal"),
        "note_legibility": row.get("note_legibility"),
        "derived_role_cn": row.get("derived_role_cn"),
    }

    return {
        "status": "ok",
        "object_id": oid,
        "builder_provenance": builder,
        "character_surface_view": character,
        "zero_write": True,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Non-destructive lived-safe merge (Ch.44 §六/§八)
# ---------------------------------------------------------------------------


def _lived_active(obj: Mapping[str, Any]) -> bool:
    return not obj.get("consumed") and not obj.get("discarded") and not obj.get("returned_out")


def _modification_reasons(obj: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if int(obj.get("material_units") or 1) != 1:
        reasons.append("已被拆分或改动过份数")
    if isinstance(obj.get("inscribed_text_cn"), str) and obj.get("inscribed_text_cn", "").strip():
        reasons.append("带着写下的字迹")
    if obj.get("loan_meta") is not None or obj.get("lent_by") is not None:
        reasons.append("涉及借还记录")
    if isinstance(obj.get("patch_note_cn"), str) and obj["patch_note_cn"].strip():
        reasons.append("有过修补记录")
    if str(obj.get("wear_state") or "intact") != "intact":
        reasons.append("有可见的磨损")
    if obj.get("assembled_into_id") or str(obj.get("fit_status") or "none") != "none":
        reasons.append("参与了装配")
    if obj.get("portions_left") is not None or obj.get("portions_total") is not None:
        reasons.append("被动过份数")
    if obj.get("transcribed_source") is not None:
        reasons.append("抄写过源文本")
    if obj.get("proposed_use") is not None:
        reasons.append("有过用法商议")
    return reasons


def _ancestor_ids(state: Mapping[str, Any], obj: Mapping[str, Any]) -> set[str]:
    objects = state.get("matter_objects") if isinstance(state.get("matter_objects"), Mapping) else {}
    out: set[str] = set()
    parent = obj.get("parent_id")
    for _ in range(12):
        if not parent:
            break
        pid = str(parent)
        if not pid or pid in out:
            break
        out.add(pid)
        parent_obj = objects.get(pid) if isinstance(objects, Mapping) else None
        parent = parent_obj.get("parent_id") if isinstance(parent_obj, Mapping) else None
    return out


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".editor_package_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def merge_package_preserving_lived_matter(
    instance_root: Path | str,
    new_editor_package: Any,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Diff-merge a newly authored package while preserving lived matter intact."""

    if not isinstance(dry_run, bool):
        raise WorldError("dry_run 需要是布尔值（true/false）。", "INVALID_DRY_RUN", 422)

    new_pkg, _ = validate_package(new_editor_package)

    root = Path(instance_root)
    matter = _matter()
    existing_raw = _read_json_zero_write(root / "world-package" / "editor_package.json")
    existing_pkg: Optional[Mapping[str, Any]] = None
    if existing_raw is not None:
        try:
            existing_pkg, _ = validate_package(existing_raw)
        except WorldError as exc:
            raise WorldError(
                f"现有编辑器世界包无法解析：{exc}", "INVALID_EXISTING_PACKAGE", 422
            ) from exc

    existing_rooms = {str(r.get("id") or "") for r in (existing_pkg or {}).get("rooms", [])}
    existing_objects = {str(o.get("id") or "") for o in (existing_pkg or {}).get("objects", [])}
    new_rooms = {str(r.get("id") or "") for r in new_pkg.get("rooms", [])}
    new_objects = {str(o.get("id") or "") for o in new_pkg.get("objects", [])}

    removed_rooms = sorted(existing_rooms - new_rooms)
    removed_objects = sorted(existing_objects - new_objects)
    removed_ids = set(removed_rooms) | set(removed_objects)
    added_rooms = sorted(new_rooms - existing_rooms)
    added_objects = sorted(new_objects - existing_objects)

    lived_raw = _read_json_zero_write(matter.state_path(root)) or {}
    lived_objects = lived_raw.get("matter_objects") if isinstance(lived_raw.get("matter_objects"), Mapping) else {}
    active_count = 0
    blocking: list[dict[str, Any]] = []
    for oid in sorted(lived_objects):
        obj = lived_objects[oid]
        if not isinstance(obj, Mapping) or not _lived_active(obj):
            continue
        active_count += 1
        reasons = _modification_reasons(obj)
        if oid in removed_ids:
            blocking.append(
                {
                    "kind": "removed_lived_object",
                    "object_id": str(oid),
                    "label_cn": obj.get("label_cn"),
                    "removed_id": str(oid),
                    "modification_reasons_cn": reasons,
                }
            )
            continue
        hit = sorted(_ancestor_ids(lived_raw, obj) & removed_ids)
        if hit:
            blocking.append(
                {
                    "kind": "object_on_removed_zone",
                    "object_id": str(oid),
                    "label_cn": obj.get("label_cn"),
                    "removed_id": hit[0],
                    "modification_reasons_cn": reasons,
                }
            )

    if blocking:
        return {
            "ok": False,
            "code": "ZONE_REFERENCED_BY_LIVED_MATTER",
            "blocking_refs": blocking,
            "removed_rooms": removed_rooms,
            "removed_objects": removed_objects,
            "lived_state_preserved": True,
            "dry_run": dry_run,
            "summary_cn": (
                f"新世界包要删掉 {len(removed_rooms)} 处区域、{len(removed_objects)} 件物件，"
                f"但其中仍有 {len(blocking)} 件生活物件在用；先把东西收走再删，"
                "生活状态一点没动。"
            ),
            "llm_calls": 0,
        }

    written = None
    if not dry_run:
        target = root / "world-package" / "editor_package.json"
        _atomic_write_json(target, new_pkg)
        written = str(target)

    return {
        "ok": True,
        "code": "MERGED",
        "dry_run": dry_run,
        "added_rooms": added_rooms,
        "added_objects": added_objects,
        "removed_rooms": removed_rooms,
        "removed_objects": removed_objects,
        "preserved_lived_objects_count": active_count,
        "lived_state_preserved": True,
        "written": written,
        "summary_cn": (
            f"新增区域 {len(added_rooms)} 处、新增物件 {len(added_objects)} 件；"
            f"原有 {active_count} 件生活物件的物理状态、字迹和份数原样保留。"
        ),
        "llm_calls": 0,
    }


__all__ = [
    "SEASON_CHOICES",
    "WEATHER_CHOICES",
    "WINDOW_STATES",
    "explain_matter_provenance_for_builder",
    "merge_package_preserving_lived_matter",
    "run_isolated_trial_hour",
]
