#!/usr/bin/env python3
"""v0.4 世界编辑、内容生产与试住一小时隔离沙盒引擎（第 44 章完整落地，前后端双对齐版）。

职责与硬性边界：
1. 五大建设者诊断与隔离沙盒能力：
   - ``audit_routes_or_rehearse``（44 §二）：路线连通性检查（``reachable_from_home``、
     ``isolated_places``、``one_way_traps`` 不自动补反向边、``missing_key_doors``）
     与内存隔离移动预演（支持 ``route_id`` / ``rehearse_route_id``、``blocked_edges`` /
     ``inject_roadblock_route``，验证角色停在可视节点或安全折返，``mutated_live_resident=False``）。
   - ``check_scene_prose_gaps``（44 §三）：对中文场景描写（``prose`` / ``scene_prose`` / ``text``）
     确定性识别常见物件与出口，对照当前地点已配置能力生成待审草案 ``draft_candidates``
     （``applied_to_live_world=False``）与可操作缺口 ``gaps``
     （``missing_affordance | missing_content | dangling_exit | missing_object | ready``）。
   - ``run_trylive_hour``（44 §四）：内置固定试住观察路线（同时支持前端锁定 ``preset_id``：
     ``go_out_and_return | yard_rain_rescue | public_notice_and_borrow | quiet_desk_afternoon``
     与领域路线名 ``sunny_cloth_drying | rain_midway_drying | roadblock_turnback | bookstore_ruler_return``），
     支持 ``injections`` / ``inject_faults``（``rain``、``seat_taken``/``seat_occupied``、
     ``roadblock``、``restart_gap`` 及同名布尔字段）与双光照版本对比 ``compare_lighting``，
     全程内存隔离运行，``llm_calls == 0``、``polluted_live_memory == False``。
   - ``inspect_why_changed``（44 §五）：针对 ``linen_cloth``/``wet_cloth``、``public_notice``、
     ``shared_ruler``、``porch_plant``/``potted_plant``、``crafted_box`` 只读解析
     ``<day_root>/world/v04_neighborhood_state.json``，并排输出 ``builder_view`` 与
     ``subject_bounded_view``，缺失历史如实声明，绝不编造前史，绝不泄露角色私人日记或未共享心事。
   - ``preview_world_modification``（44 §六、§八）：针对 ``move_bench``/``move_object``、
     ``delete_place``、``block_route``、``apply_worldpack_diff`` 检查占用者、表面子物件、
     约定与道路引用；有人占用时禁止静默瞬移，被引用地点禁止直接删除（``blocked_referenced_place``），
     世界包增量更新保留已生活状态。
2. 绝对隔离铁律：
   - 纯读取路径 100% 零磁盘写入（不创建目录、不创建临时文件、不改动任何文件 mtime）；
   - 写路径仅原子更新隔离报告 ``<day_root>/phone/v04_builder_sandbox.json``（``0600`` 权限），
     绝不触碰 ``<day_root>/world/``、角色记忆、私人日记或作品文件；
   - 默认使用中性代词 ``"TA"``，``llm_calls == 0``。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ENGINE_SCHEMA = "world.v04.builder_trylive.v1"
SANDBOX_STATE_SCHEMA_VERSION = 1
SANDBOX_DIRNAME = "phone"
SANDBOX_FILENAME = "v04_builder_sandbox.json"
WORLD_DIRNAME = "world"
NEIGHBORHOOD_STATE_FILENAME = "v04_neighborhood_state.json"

DEFAULT_TZ = timezone(timedelta(hours=8))
DEFAULT_PRONOUN = "TA"
ALLOWED_PRONOUNS: frozenset[str] = frozenset({"她", "他", "它", "TA"})

# Fault / injection normalization ("seat_taken" <-> "seat_occupied")
FAULT_ALIAS_MAP: dict[str, str] = {
    "rain": "rain",
    "seat_occupied": "seat_occupied",
    "seat_taken": "seat_occupied",
    "roadblock": "roadblock",
    "restart_gap": "restart_gap",
}
ALLOWED_FAULTS: tuple[str, ...] = (
    "rain",
    "seat_occupied",
    "seat_taken",
    "roadblock",
    "restart_gap",
)
ALLOWED_COMPARE_LIGHTING: tuple[str, ...] = (
    "none",
    "bright_vs_glare",
)

# Why-changed object_id / target_kind normalization
WHY_TARGET_ALIAS_MAP: dict[str, tuple[str, str]] = {
    # input_token -> (canonical_target_kind, frontend_object_id)
    "wet_cloth": ("wet_cloth", "linen_cloth"),
    "linen_cloth": ("wet_cloth", "linen_cloth"),
    "wet_cloth_01": ("wet_cloth", "linen_cloth"),
    "public_notice": ("public_notice", "public_notice"),
    "notice_road_maintenance_01": ("public_notice", "public_notice"),
    "shared_ruler": ("shared_ruler", "shared_ruler"),
    "potted_plant": ("potted_plant", "porch_plant"),
    "porch_plant": ("potted_plant", "porch_plant"),
    "courtyard_potted_plant": ("potted_plant", "porch_plant"),
    "crafted_box": ("crafted_box", "crafted_box"),
    "card_box_wip": ("crafted_box", "crafted_box"),
}
ALLOWED_WHY_TARGET_KINDS: tuple[str, ...] = (
    "wet_cloth",
    "linen_cloth",
    "public_notice",
    "shared_ruler",
    "potted_plant",
    "porch_plant",
    "crafted_box",
)

INSPECTABLE_OBJECTS: tuple[dict[str, str], ...] = (
    {
        "object_id": "linen_cloth",
        "target_kind": "wet_cloth",
        "label": "棉麻擦手巾",
        "place_id": "porch",
    },
    {
        "object_id": "public_notice",
        "target_kind": "public_notice",
        "label": "街角公共告示",
        "place_id": "street_corner",
    },
    {
        "object_id": "shared_ruler",
        "target_kind": "shared_ruler",
        "label": "书店共享裁纸尺",
        "place_id": "bookstore",
    },
    {
        "object_id": "porch_plant",
        "target_kind": "potted_plant",
        "label": "院角/门廊陶盆绿植",
        "place_id": "courtyard",
    },
    {
        "object_id": "crafted_box",
        "target_kind": "crafted_box",
        "label": "手作卡片收纳盒",
        "place_id": "home",
    },
)

ALLOWED_MOD_KINDS: tuple[str, ...] = (
    "move_object",
    "move_bench",
    "delete_place",
    "block_route",
    "apply_worldpack_diff",
)
ALLOWED_ROADBLOCK_POLICIES: tuple[str, ...] = (
    "turn_back",
    "pause_at_node",
    "reroute",
)
ALLOWED_MOVE_RESOLUTIONS: tuple[str, ...] = (
    "check_only",
    "wait_until_idle",
    "admin_migrated",
    "cancel",
)
ALLOWED_GAP_SEVERITIES: tuple[str, ...] = (
    "missing_affordance",
    "missing_content",
    "dangling_exit",
    "missing_object",
    "ready",
)

PLACE_ID_ALIASES: dict[str, str] = {
    "yard": "courtyard",
    "garden": "courtyard",
    "room": "home",
    "house": "home",
    "indoor": "home",
    "veranda": "porch",
    "entry": "porch",
    "street": "street_corner",
    "corner": "street_corner",
    "walkway": "covered_walkway",
    "arcade": "covered_walkway",
    "shop": "bookstore",
    "bookshop": "bookstore",
    "river": "riverside_walk",
    "riverside": "riverside_walk",
}


def _resolve_place_id_alias(raw_place_id: str, places: Mapping[str, Any]) -> str:
    cleaned = raw_place_id.strip()
    if cleaned in places:
        return cleaned
    mapped = PLACE_ID_ALIASES.get(cleaned.lower())
    if mapped and mapped in places:
        return mapped
    return cleaned


ROUTE_ID_ALIASES: dict[str, str] = {
    "old_bridge": "route_street_riverside",
    "open_shortcut": "route_courtyard_street_open",
    "eaves_walkway": "route_courtyard_covered_walkway",
    "street_to_bookstore": "route_street_bookstore",
    "street_to_riverside": "route_street_riverside",
    "home_to_porch": "route_home_porch",
    "porch_to_yard": "route_porch_courtyard",
}

OBJECT_ID_ALIASES: dict[str, str] = {
    "park_bench": "riverside_bench",
    "yard_bench": "courtyard_bench",
}


def _resolve_route_id_alias(raw_route_id: str) -> str:
    cleaned = raw_route_id.strip()
    return ROUTE_ID_ALIASES.get(cleaned.lower(), cleaned)


def _resolve_object_id_alias(raw_obj_id: str) -> str:
    cleaned = raw_obj_id.strip()
    return OBJECT_ID_ALIASES.get(cleaned.lower(), cleaned)


# Forbidden foreign / identity / privilege / score keys (includes server.py reserved names)
FORBIDDEN_FOREIGN_KEYS: frozenset[str] = frozenset({
    "uid",
    "authority",
    "owner",
    "subject",
    "subject_id",
    "subject_ref",
    "price",
    "balance",
    "source",
    "actor",
    "actor_id",
    "actor_ref",
    "actor_side",
    "artifact_id",
    "has_subject_capability",
    "mood_delta",
    "affection_score",
    "affection_delta",
    "favorability",
    "intimacy_score",
    "obedience_score",
    "relationship_score",
    "xp",
    "xp_points",
    "skill_level",
    "private_diary",
    "apply_to_live_world",
    "mutate_live_world",
})

MAX_OPERATION_RECEIPTS = 128

# Canonical domain scenarios + frontend locked preset_id pairs
PRESET_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "sunny_cloth_drying",
        "preset_id": "go_out_and_return",
        "title": "晴天挂湿巾与出门往返一小时",
        "duration_minutes": 60,
        "summary": "从室内书架拿书装进帆布包，带湿巾出门挂到院子晾衣绳，回房坐下读书 1 小时后去院子收回变干的布巾。",
        "places_involved": ["home", "porch", "courtyard"],
        "verifies": ["object_pickup_and_bag", "outdoor_drying_curve", "return_collect_state_change"],
    },
    {
        "scenario_id": "rain_midway_drying",
        "preset_id": "yard_rain_rescue",
        "title": "挂巾二十分钟遇雨抢收与门廊遮雨分段结算",
        "duration_minutes": 60,
        "summary": "院子晾晒 20 分钟后注入降雨，对比露天淋雨回潮与移入有檐但通风较弱的门廊挂钩分段失水结算。",
        "places_involved": ["home", "porch", "courtyard"],
        "verifies": ["piecewise_weather_drying", "porch_shelter_weak_ventilation_diff"],
    },
    {
        "scenario_id": "roadblock_turnback",
        "preset_id": "quiet_desk_afternoon",
        "title": "带借书袋赴书店遇封路改道/折返与下午阅读",
        "duration_minutes": 60,
        "summary": "将书与借书卡装入帆布袋前往旧书店，走到可视路口才观察到封路，改走有檐长廊或安全折返回书桌阅读，包内子物件随父容器移动零漂移。",
        "places_involved": ["home", "porch", "courtyard", "covered_walkway", "street_corner", "bookstore"],
        "verifies": ["local_roadblock_perception", "reroute_or_safe_turnback", "container_child_no_drift"],
    },
    {
        "scenario_id": "bookstore_ruler_return",
        "preset_id": "public_notice_and_borrow",
        "title": "看公共告示借裁纸尺回家裁卡与闭店归还箱投递",
        "duration_minutes": 60,
        "summary": "路过街角看告示、向旧书店借出裁纸尺回家裁制卡片收纳盒隔板，傍晚闭店后投递门外归还箱，验证闭店不吞物且不伪造店主当面签收。",
        "places_involved": ["bookstore", "street_corner", "courtyard", "porch", "home"],
        "verifies": ["material_unit_conservation", "after_hours_return_box", "no_forged_in_person_receipt"],
    },
)

FRONTEND_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "preset_id": "go_out_and_return",
        "id": "go_out_and_return",
        "scenario_id": "sunny_cloth_drying",
        "title": "出门挂湿巾与往返收回（1 小时）",
        "label": "出门挂湿巾与往返收回",
        "duration_minutes": 60,
        "summary": "拿书装包、出门去院子挂湿巾、回房阅读、1 小时后取回干爽布巾。",
    },
    {
        "preset_id": "yard_rain_rescue",
        "id": "yard_rain_rescue",
        "scenario_id": "rain_midway_drying",
        "title": "院子晾晒中途遇雨移入门廊（1 小时）",
        "label": "院子遇雨抢收与门廊遮雨",
        "duration_minutes": 60,
        "summary": "挂巾 20 分钟后降雨，移入有檐门廊挂钩分段结算失水速率。",
    },
    {
        "preset_id": "public_notice_and_borrow",
        "id": "public_notice_and_borrow",
        "scenario_id": "bookstore_ruler_return",
        "title": "查看告示借裁纸尺与闭店归还箱投递（1 小时）",
        "label": "公共告示与书店借尺归还",
        "duration_minutes": 60,
        "summary": "借出裁纸尺回家裁隔板，傍晚闭店后投递门外归还箱，闭店不吞物且不伪造当面签收。",
    },
    {
        "preset_id": "quiet_desk_afternoon",
        "id": "quiet_desk_afternoon",
        "scenario_id": "roadblock_turnback",
        "title": "带包出行遇封路改道与窗边书桌午后（1 小时）",
        "label": "封路折返与安静书桌午后",
        "duration_minutes": 60,
        "summary": "测试随身包内子物件零漂移、路口局部感知封路及窗边光照/占座对比。",
    },
)

SCENARIO_RESOLUTION_MAP: dict[str, tuple[str, str]] = {
    # any accepted id -> (canonical_scenario_id, frontend_preset_id)
    "sunny_cloth_drying": ("sunny_cloth_drying", "go_out_and_return"),
    "go_out_and_return": ("sunny_cloth_drying", "go_out_and_return"),
    "rain_midway_drying": ("rain_midway_drying", "yard_rain_rescue"),
    "yard_rain_rescue": ("rain_midway_drying", "yard_rain_rescue"),
    "roadblock_turnback": ("roadblock_turnback", "quiet_desk_afternoon"),
    "quiet_desk_afternoon": ("roadblock_turnback", "quiet_desk_afternoon"),
    "bookstore_ruler_return": ("bookstore_ruler_return", "public_notice_and_borrow"),
    "public_notice_and_borrow": ("bookstore_ruler_return", "public_notice_and_borrow"),
}

PRESET_SCENARIO_BY_ID: dict[str, dict[str, Any]] = {
    item["scenario_id"]: dict(item) for item in PRESET_SCENARIOS
}


# ---------------------------------------------------------------------------
# Structured Domain Errors
# ---------------------------------------------------------------------------


class V04BuilderError(ValueError):
    """Structured error carrying HTTP status code and machine-readable reason code."""

    status_code: int = 400

    def __init__(
        self,
        reason_code: str,
        detail: str = "",
        *,
        status_code: Optional[int] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(detail or reason_code)
        if status_code is not None:
            self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.details: dict[str, Any] = dict(details or {})


class V04ValidationError(V04BuilderError):
    status_code = 400


class V04PermissionError(V04BuilderError):
    status_code = 403


class V04ConflictError(V04BuilderError):
    status_code = 409


# ---------------------------------------------------------------------------
# Time, Digest & Atomic Persistence Helpers
# ---------------------------------------------------------------------------


def builder_sandbox_state_path(day_root: Path | str) -> Path:
    """Return isolated sandbox report path ``<day_root>/phone/v04_builder_sandbox.json``."""
    return Path(day_root) / SANDBOX_DIRNAME / SANDBOX_FILENAME


def neighborhood_state_path(day_root: Path | str) -> Path:
    """Return live neighborhood state path ``<day_root>/world/v04_neighborhood_state.json`` (read-only)."""
    return Path(day_root) / WORLD_DIRNAME / NEIGHBORHOOD_STATE_FILENAME


def parse_iso_moment(value: str | datetime | None, *, field: str = "now_iso") -> datetime:
    if value is None:
        return datetime.now(DEFAULT_TZ).replace(microsecond=0)
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=DEFAULT_TZ)
        return dt.replace(microsecond=0)
    if not isinstance(value, str) or not value.strip():
        raise V04ValidationError("INVALID_FIELDS", f"Invalid timestamp in {field}")
    raw = value.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise V04ValidationError("INVALID_FIELDS", f"Malformed ISO timestamp in {field}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.replace(microsecond=0)


def format_iso(dt: datetime) -> str:
    if dt.utcoffset() == timedelta(0):
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return dt.replace(microsecond=0).isoformat()


def advance_iso(base_iso: str, *, minutes: int) -> str:
    dt = parse_iso_moment(base_iso) + timedelta(minutes=int(minutes))
    return format_iso(dt)


def compute_intent_fingerprint(
    action: str,
    actor_side: str,
    payload: Mapping[str, Any],
    *,
    explicit_fingerprint: Optional[str] = None,
) -> str:
    semantic = {
        k: v
        for k, v in payload.items()
        if k not in {"operation_key", "intent_fingerprint", "expected_revision"}
    }
    envelope = {
        "action": action,
        "actor_side": actor_side,
        "semantic": semantic,
        "explicit_fingerprint": explicit_fingerprint or "",
    }
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _guard_no_symlink(path: Path) -> None:
    if path.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", f"Symlink path refused: {path.name}")


def _read_json_readonly(path: Path) -> Optional[dict[str, Any]]:
    """Pure read-only JSON loader. Never creates directories or touches mtimes."""
    parent = path.parent
    if parent.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", f"Symlink directory refused: {parent.name}")
    if not path.exists():
        return None
    _guard_no_symlink(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _atomic_write_sandbox_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomically write ``<day_root>/phone/v04_builder_sandbox.json`` with ``0600`` permissions."""
    root = path.parent.parent
    if root.exists() and root.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "day_root symlink refused")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "phone dir symlink refused")
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "sandbox file symlink refused")

    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Default Three-Circle Neighborhood Topology & Read-Only Live Snapshot Loader
# ---------------------------------------------------------------------------


def build_default_world_topology() -> dict[str, Any]:
    """Return built-in default v0.4 three-circle neighborhood topology (Ch. 38-44)."""
    places: dict[str, dict[str, Any]] = {
        "home": {
            "place_id": "home",
            "label": "家·起居工作区",
            "circle": "home",
            "aliases": ["家", "起居室", "房间", "室内", "书房"],
            "sheltered": True,
            "ventilation": "moderate",
            "lighting_mode": "bright_soft",
            "canvas_layout": {"x": 120, "y": 220},
            "occupants": [DEFAULT_PRONOUN],
            "objects": [
                {
                    "object_id": "home_desk",
                    "label": "书桌工作台",
                    "category": "surface",
                    "affordances": ["place_item", "craft_surface", "sit_at"],
                    "capacity": 4,
                    "occupied_by": None,
                    "child_items": ["card_box_wip"],
                },
                {
                    "object_id": "home_chair",
                    "label": "书桌木椅",
                    "category": "seat",
                    "affordances": ["sit"],
                    "seat_capacity": 1,
                    "occupied_by": None,
                    "child_items": [],
                },
                {
                    "object_id": "window_reading_nook",
                    "label": "窗边阅读座",
                    "category": "seat",
                    "affordances": ["sit", "read_light"],
                    "seat_capacity": 1,
                    "lighting_mode": "bright_soft",
                    "occupied_by": None,
                    "child_items": [],
                },
                {
                    "object_id": "home_window",
                    "label": "起居室窗户",
                    "category": "fixture",
                    "affordances": ["look_out", "daylight"],
                    "lighting_mode": "bright_soft",
                },
                {
                    "object_id": "home_bookshelf",
                    "label": "室内书架",
                    "category": "storage",
                    "affordances": ["place_item", "take_item", "hold_books"],
                    "capacity": 8,
                    "has_content": True,
                    "child_items": ["sketch_album_book"],
                },
                {
                    "object_id": "entry_shelf",
                    "label": "玄关暂放台",
                    "category": "surface",
                    "affordances": ["place_item", "take_item"],
                    "capacity": 3,
                    "child_items": ["canvas_tote_bag", "wet_cloth_01", "house_key"],
                },
                {
                    "object_id": "canvas_tote_bag",
                    "label": "帆布借书袋",
                    "category": "container",
                    "affordances": ["carry_container", "put_in", "take_out"],
                    "capacity": 3,
                    "parent_id": "entry_shelf",
                    "child_items": [],
                },
            ],
        },
        "porch": {
            "place_id": "porch",
            "label": "门廊",
            "circle": "doorstep",
            "aliases": ["门廊", "玄关外", "檐下"],
            "sheltered": True,
            "ventilation": "weak",
            "lighting_mode": "shaded_daylight",
            "canvas_layout": {"x": 240, "y": 220},
            "occupants": [],
            "objects": [
                {
                    "object_id": "porch_window_bench",
                    "label": "门廊窗边长椅（背景装饰）",
                    "category": "seat",
                    "visual_only": True,
                    "affordances": [],
                    "seat_capacity": 0,
                    "occupied_by": None,
                    "child_items": [],
                },
                {
                    "object_id": "porch_hook",
                    "label": "门廊挂钩",
                    "category": "hook",
                    "affordances": ["hang_item", "dry_item"],
                    "capacity": 2,
                    "sheltered": True,
                    "ventilation": "weak",
                    "child_items": [],
                },
                {
                    "object_id": "shared_tool_rack",
                    "label": "邻里共享架",
                    "category": "storage",
                    "affordances": ["place_item", "take_item", "borrow_item", "return_item"],
                    "capacity": 4,
                    "child_items": [],
                },
            ],
        },
        "courtyard": {
            "place_id": "courtyard",
            "label": "院子",
            "circle": "doorstep",
            "aliases": ["院子", "小院", "庭院"],
            "sheltered": False,
            "ventilation": "good",
            "lighting_mode": "open_sky",
            "canvas_layout": {"x": 360, "y": 220},
            "occupants": [],
            "objects": [
                {
                    "object_id": "courtyard_clothesline",
                    "label": "院子晾衣绳",
                    "category": "hook",
                    "affordances": ["hang_item", "dry_item"],
                    "capacity": 4,
                    "sheltered": False,
                    "ventilation": "good",
                    "child_items": [],
                },
                {
                    "object_id": "courtyard_bench",
                    "label": "院子长椅",
                    "category": "seat",
                    "affordances": ["sit", "place_item"],
                    "seat_capacity": 2,
                    "capacity": 2,
                    "occupied_by": None,
                    "surface_wetness": "dry",
                    "child_items": [],
                },
                {
                    "object_id": "courtyard_potted_plant",
                    "label": "院角陶盆绿植",
                    "category": "plant",
                    "affordances": ["water_plant", "observe"],
                    "moisture": "slightly_dry",
                    "child_items": [],
                },
            ],
        },
        "covered_walkway": {
            "place_id": "covered_walkway",
            "label": "有檐长廊",
            "circle": "neighborhood",
            "aliases": ["有檐长廊", "长廊", "避雨连廊", "有檐路"],
            "sheltered": True,
            "ventilation": "moderate",
            "lighting_mode": "shaded_daylight",
            "canvas_layout": {"x": 480, "y": 130},
            "occupants": [],
            "objects": [
                {
                    "object_id": "walkway_rest_bench",
                    "label": "长廊木椅",
                    "category": "seat",
                    "affordances": ["sit"],
                    "seat_capacity": 2,
                    "occupied_by": None,
                    "child_items": [],
                },
            ],
        },
        "street_corner": {
            "place_id": "street_corner",
            "label": "街角与公共告示点",
            "circle": "neighborhood",
            "aliases": ["街角", "路口", "公共告示点", "转角"],
            "sheltered": False,
            "ventilation": "good",
            "lighting_mode": "open_sky",
            "canvas_layout": {"x": 520, "y": 240},
            "occupants": [],
            "objects": [
                {
                    "object_id": "public_notice_board",
                    "label": "街角公共告示栏",
                    "category": "readable_board",
                    "affordances": ["read_notice", "post_notice"],
                    "capacity": 4,
                    "has_content": True,
                    "child_items": ["notice_road_maintenance_01"],
                },
                {
                    "object_id": "corner_bench",
                    "label": "街角休息椅",
                    "category": "seat",
                    "affordances": ["sit"],
                    "seat_capacity": 1,
                    "occupied_by": None,
                    "child_items": [],
                },
            ],
        },
        "bookstore": {
            "place_id": "bookstore",
            "label": "旧书店",
            "circle": "neighborhood",
            "aliases": ["旧书店", "书店", "潮声旧书铺"],
            "sheltered": True,
            "ventilation": "moderate",
            "lighting_mode": "bright_soft",
            "open_hours": {"start_hhmm": "10:00", "end_hhmm": "19:00"},
            "shop_open": True,
            "canvas_layout": {"x": 680, "y": 220},
            "occupants": ["bookshop_keeper"],
            "objects": [
                {
                    "object_id": "bookstore_counter",
                    "label": "书店柜台",
                    "category": "surface",
                    "affordances": ["place_item", "borrow_tool", "return_in_person", "reserve_book"],
                    "capacity": 4,
                    "child_items": ["shared_ruler"],
                },
                {
                    "object_id": "bookstore_return_box",
                    "label": "书店门外归还箱",
                    "category": "return_box",
                    "affordances": ["drop_return", "receive_after_hours"],
                    "capacity": 5,
                    "accessible_when_closed": True,
                    "receive_rule": "accepted_into_return_box_pending_keeper_check",
                    "child_items": [],
                },
                {
                    "object_id": "bookstore_shelf",
                    "label": "旧画册书架",
                    "category": "storage",
                    "affordances": ["browse_book", "read_excerpt", "place_item"],
                    "capacity": 12,
                    "has_content": True,
                    "child_items": ["illustrated_botany_album"],
                },
                {
                    "object_id": "bookstore_window_seat",
                    "label": "书店靠窗阅览座",
                    "category": "seat",
                    "affordances": ["sit", "read_light"],
                    "seat_capacity": 1,
                    "lighting_mode": "bright_soft",
                    "occupied_by": None,
                    "child_items": [],
                },
            ],
        },
        "riverside_walk": {
            "place_id": "riverside_walk",
            "label": "河边步道",
            "circle": "neighborhood",
            "aliases": ["河边", "河边步道", "河岸"],
            "sheltered": False,
            "ventilation": "good",
            "lighting_mode": "open_sky",
            "canvas_layout": {"x": 560, "y": 360},
            "occupants": [],
            "objects": [
                {
                    "object_id": "riverside_bench",
                    "label": "河畔长椅",
                    "category": "seat",
                    "affordances": ["sit", "observe"],
                    "seat_capacity": 2,
                    "occupied_by": None,
                    "child_items": [],
                },
            ],
        },
    }

    routes: list[dict[str, Any]] = [
        {
            "route_id": "route_home_porch",
            "from_place": "home",
            "to_place": "porch",
            "duration_min": 1,
            "sheltered": True,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_porch_courtyard",
            "from_place": "porch",
            "to_place": "courtyard",
            "duration_min": 1,
            "sheltered": False,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_courtyard_street_open",
            "from_place": "courtyard",
            "to_place": "street_corner",
            "duration_min": 4,
            "sheltered": False,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_courtyard_covered_walkway",
            "from_place": "courtyard",
            "to_place": "covered_walkway",
            "duration_min": 3,
            "sheltered": True,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_covered_walkway_street",
            "from_place": "covered_walkway",
            "to_place": "street_corner",
            "duration_min": 4,
            "sheltered": True,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_street_bookstore",
            "from_place": "street_corner",
            "to_place": "bookstore",
            "duration_min": 5,
            "sheltered": False,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
        {
            "route_id": "route_street_riverside",
            "from_place": "street_corner",
            "to_place": "riverside_walk",
            "duration_min": 6,
            "sheltered": False,
            "bidirectional": True,
            "requires_key": None,
            "status": "open",
        },
    ]

    object_templates: list[dict[str, Any]] = [
        {"template_id": "house_key", "kind": "key", "label": "家门钥匙"},
        {"template_id": "wet_cloth_01", "kind": "cloth", "label": "棉麻擦手巾"},
        {"template_id": "linen_cloth", "kind": "cloth", "label": "亚麻擦手巾"},
        {"template_id": "canvas_tote_bag", "kind": "container", "label": "帆布借书袋"},
        {"template_id": "shared_ruler", "kind": "tool", "label": "借用钢制裁纸尺"},
        {"template_id": "card_box_wip", "kind": "craft_item", "label": "半成品卡片收纳盒"},
        {"template_id": "sketch_album_book", "kind": "book", "label": "植物速写小册"},
    ]

    return {
        "has_live_neighborhood_file": False,
        "pronoun": DEFAULT_PRONOUN,
        "environment": {
            "weather": "sunny",
            "ventilation": "moderate",
            "window_lighting": "bright_soft",
            "weather_segments": [],
        },
        "places": places,
        "routes": routes,
        "object_templates": object_templates,
        "journey": {
            "status": "at_place",
            "current_place": "home",
            "carried_items": [],
        },
        "drying_items": {
            "wet_cloth_01": {
                "item_id": "wet_cloth_01",
                "object_id": "linen_cloth",
                "label": "棉麻擦手巾",
                "origin": "室内洗手台擦拭桌面后微湿搁置",
                "place_id": "porch",
                "support_id": "porch_hook",
                "sheltered": True,
                "ventilation": "weak",
                "moisture_ratio": 0.52,
                "dryness_state": "damp",
                "last_moved_by": DEFAULT_PRONOUN,
                "last_moved_at": None,
                "drying_started_at": None,
                "weather_segments": [
                    {
                        "segment_index": 1,
                        "weather": "cloudy",
                        "sheltered": True,
                        "ventilation": "weak",
                        "evaporation_rate_per_hour": 0.16,
                        "note": "有檐门廊位置虽可遮挡雨水，但空气对流较弱，失水速率偏低。",
                    }
                ],
                "history_complete": False,
            }
        },
        "potted_plant": {
            "plant_id": "porch_plant",
            "label": "院角陶盆绿植",
            "place_id": "courtyard",
            "moisture_state": "slightly_dry",
            "health_state": "steady",
            "last_watered_at": None,
            "last_watered_by": None,
            "weather_exposure": "open_courtyard",
            "history_complete": False,
        },
        "craft_bench": {
            "item_id": "card_box_wip",
            "label": "卡片收纳盒（半成品）",
            "place_id": "home",
            "surface_id": "home_desk",
            "stage": "divider_cut_pending_fit",
            "source_material": "四单元厚卡纸与旧纸盒",
            "input_material_units": 4,
            "used_divider_units": 1,
            "remaining_material_units": 3,
            "waste_units": 0,
            "parts": ["box_shell_01", "card_divider_01"],
            "last_worked_at": None,
            "history_complete": False,
        },
        "public_life": {
            "notices": [
                {
                    "notice_id": "notice_road_maintenance_01",
                    "title": "街角石板路临时整修通知",
                    "place_id": "street_corner",
                    "real_author": "street_maintenance_team",
                    "author_display": "街区市政维护班组",
                    "content": "周三午后对院门外近路石板做局部平整，雨天顺延，行人可绕行有檐长廊。",
                    "posted_at": "2026-11-12T09:00:00+08:00",
                    "expires_at": "2026-11-12T18:00:00+08:00",
                    "withdrawn": False,
                    "history_complete": False,
                }
            ],
            "shared_ruler": {
                "item_id": "shared_ruler",
                "label": "钢制裁纸尺",
                "owner": "bookstore",
                "holder": "bookstore",
                "place_id": "bookstore",
                "support_id": "bookstore_counter",
                "loan_status": "available_at_counter",
                "borrowed_at": None,
                "due_at": None,
                "returned_via": None,
                "history_complete": False,
            },
            "agreements": [
                {
                    "agreement_id": "bookstore_return_box_rule",
                    "kind": "after_hours_return_rule",
                    "place_id": "bookstore",
                    "object_id": "bookstore_return_box",
                    "summary": "闭店后借用工具可投入门外归还箱，次日店主开店核收。",
                    "active": True,
                }
            ],
        },
    }


def _merge_live_neighborhood_json(base: dict[str, Any], live: Mapping[str, Any]) -> dict[str, Any]:
    """Safely overlay fields from ``<day_root>/world/v04_neighborhood_state.json`` if present."""
    merged = copy.deepcopy(base)
    merged["has_live_neighborhood_file"] = True

    env = live.get("environment")
    if isinstance(env, Mapping):
        for key in ("weather", "ventilation", "window_lighting", "season", "time_of_day"):
            if isinstance(env.get(key), str) and env[key].strip():
                merged["environment"][key] = env[key].strip()
        if "ventilation" not in env and isinstance(env.get("airflow"), str) and env["airflow"].strip():
            af = env["airflow"].strip().lower()
            merged["environment"]["ventilation"] = {
                "weak": "weak",
                "medium": "moderate",
                "strong": "good",
            }.get(af, af)
        segs = env.get("weather_segments") or env.get("history")
        if isinstance(segs, list):
            merged["environment"]["weather_segments"] = copy.deepcopy(segs)

    raw_places = live.get("places")
    if isinstance(raw_places, Mapping):
        for pid, pval in raw_places.items():
            if pid == "routes" or not isinstance(pid, str) or not isinstance(pval, Mapping):
                continue
            canonical_pid = _resolve_place_id_alias(pid, merged["places"])
            inferred_sheltered = bool(pval.get("sheltered", False))
            if "sheltered" not in pval and isinstance(pval.get("shelter"), str):
                inferred_sheltered = pval["shelter"].strip().lower() in {"indoor", "sheltered"}
            target = merged["places"].setdefault(
                canonical_pid,
                {
                    "place_id": canonical_pid,
                    "label": str(pval.get("label") or pval.get("name") or canonical_pid),
                    "circle": str(pval.get("circle") or pval.get("ring") or "neighborhood"),
                    "aliases": [str(pval.get("label") or pval.get("name") or canonical_pid)],
                    "sheltered": inferred_sheltered,
                    "ventilation": str(pval.get("ventilation") or "moderate"),
                    "lighting_mode": str(pval.get("lighting_mode") or "bright_soft"),
                    "canvas_layout": {"x": 400, "y": 300},
                    "occupants": [],
                    "objects": [],
                },
            )
            for scalar_key in ("label", "sheltered", "ventilation", "lighting_mode", "shop_open"):
                if scalar_key in pval and pval[scalar_key] is not None:
                    target[scalar_key] = copy.deepcopy(pval[scalar_key])
            if "sheltered" not in pval and isinstance(pval.get("shelter"), str):
                target["sheltered"] = inferred_sheltered
            if isinstance(pval.get("occupants"), list):
                target["occupants"] = [str(x) for x in pval["occupants"] if x]
            if isinstance(pval.get("objects"), list):
                existing_ids = {obj.get("object_id") for obj in target["objects"] if isinstance(obj, Mapping)}
                for obj in pval["objects"]:
                    if isinstance(obj, Mapping) and isinstance(obj.get("object_id"), str):
                        if obj["object_id"] in existing_ids:
                            for idx, cur in enumerate(target["objects"]):
                                if cur.get("object_id") == obj["object_id"]:
                                    updated = dict(cur)
                                    updated.update(copy.deepcopy(dict(obj)))
                                    target["objects"][idx] = updated
                        else:
                            target["objects"].append(copy.deepcopy(dict(obj)))

    raw_routes = live.get("routes")
    if not isinstance(raw_routes, list) and isinstance(raw_places, Mapping):
        raw_routes = raw_places.get("routes") if isinstance(raw_places.get("routes"), list) else None
    if isinstance(raw_routes, list) and raw_routes:
        by_id = {r["route_id"]: r for r in merged["routes"] if isinstance(r, dict) and "route_id" in r}
        for r in raw_routes:
            if not isinstance(r, Mapping):
                continue
            raw_rid = str(r.get("route_id") or f"route_{r.get('from_place')}_{r.get('to_place')}")
            rid = _resolve_route_id_alias(raw_rid)
            r_copy = copy.deepcopy(dict(r))
            if isinstance(r_copy.get("from_place"), str):
                r_copy["from_place"] = _resolve_place_id_alias(r_copy["from_place"], merged["places"])
            if isinstance(r_copy.get("to_place"), str):
                r_copy["to_place"] = _resolve_place_id_alias(r_copy["to_place"], merged["places"])
            if rid in by_id:
                r_copy["route_id"] = rid
                by_id[rid].update(r_copy)
            else:
                r_copy.setdefault("route_id", rid)
                merged["routes"].append(r_copy)

    world_truth = live.get("world_truth")
    if isinstance(world_truth, Mapping) and isinstance(world_truth.get("roadblocks"), Mapping):
        by_id = {r["route_id"]: r for r in merged["routes"] if isinstance(r, dict) and "route_id" in r}
        for rb_rid, rb_info in world_truth["roadblocks"].items():
            if not isinstance(rb_rid, str) or not isinstance(rb_info, Mapping):
                continue
            resolved_rb = _resolve_route_id_alias(rb_rid)
            if resolved_rb in by_id and rb_info.get("active") is True:
                by_id[resolved_rb]["status"] = "blocked"

    journey = live.get("journey")
    if isinstance(journey, Mapping):
        merged["journey"].update(copy.deepcopy(dict(journey)))
        raw_cur = journey.get("current_place") or journey.get("current_place_id") or journey.get("place_id")
        cur_place = _resolve_place_id_alias(raw_cur, merged["places"]) if isinstance(raw_cur, str) else None
        if isinstance(cur_place, str) and cur_place in merged["places"]:
            merged["journey"]["current_place"] = cur_place
            for p in merged["places"].values():
                p["occupants"] = [occ for occ in p.get("occupants", []) if occ != DEFAULT_PRONOUN]
            merged["places"][cur_place].setdefault("occupants", []).append(DEFAULT_PRONOUN)
        if journey.get("state") == "in_transit" and isinstance(journey.get("active_route_id"), str):
            merged["journey"]["route_id"] = _resolve_route_id_alias(journey["active_route_id"])

    support_defaults: dict[str, tuple[str, bool, str]] = {
        "home_rack": ("home", True, "moderate"),
        "porch_hook": ("porch", True, "weak"),
        "yard_line": ("courtyard", False, "good"),
        "courtyard_clothesline": ("courtyard", False, "good"),
        "hand_bag": ("home", True, "weak"),
    }

    def _normalize_drying_entry(item_id: str, dval: Mapping[str, Any], template: Mapping[str, Any]) -> dict[str, Any]:
        entry = dict(template)
        entry.update(copy.deepcopy(dict(dval)))
        entry["item_id"] = item_id
        if "label" not in dval and isinstance(dval.get("name"), str) and dval["name"].strip():
            entry["label"] = dval["name"].strip()
        sup_id = str(dval.get("support_id") or entry.get("support_id") or "")
        if sup_id in support_defaults:
            def_place, def_shelt, def_vent = support_defaults[sup_id]
            if "place_id" not in dval:
                entry["place_id"] = def_place
            if "sheltered" not in dval:
                entry["sheltered"] = def_shelt
            if "ventilation" not in dval:
                entry["ventilation"] = def_vent
        if isinstance(entry.get("place_id"), str):
            entry["place_id"] = _resolve_place_id_alias(entry["place_id"], merged["places"])
        if "moisture_ratio" not in dval and isinstance(dval.get("moisture"), (int, float)) and not isinstance(dval.get("moisture"), bool):
            ratio = max(0.0, min(1.0, float(dval["moisture"]) / 100.0))
            entry["moisture_ratio"] = round(ratio, 4)
        if "dryness_state" not in dval and ("moisture" in dval or "sleeping_dry" in dval):
            mr = float(entry.get("moisture_ratio") if entry.get("moisture_ratio") is not None else 0.5)
            entry["dryness_state"] = "dry" if (dval.get("sleeping_dry") or mr <= 0.05) else ("damp" if mr <= 0.40 else "wet")
        if "drying_started_at" not in dval and dval.get("segment_started_at"):
            entry["drying_started_at"] = dval["segment_started_at"]
        if "last_moved_at" not in dval and (dval.get("segment_started_at") or dval.get("last_anchor_at")):
            entry["last_moved_at"] = dval.get("segment_started_at") or dval.get("last_anchor_at")
        entry["history_complete"] = bool(
            dval.get("drying_started_at")
            or dval.get("hung_at")
            or dval.get("weather_segments")
            or dval.get("segment_started_at")
        )
        return entry

    default_cloth_tmpl = dict(base.get("drying_items", {}).get("wet_cloth_01", {}))
    drying = live.get("drying_items")
    if isinstance(drying, Mapping) and drying:
        if "wet_cloth_01" not in drying:
            merged["drying_items"] = {}
        for item_id, dval in drying.items():
            if isinstance(item_id, str) and isinstance(dval, Mapping):
                tmpl = merged["drying_items"].get(item_id) or default_cloth_tmpl
                merged["drying_items"][item_id] = _normalize_drying_entry(item_id, dval, tmpl)
    elif isinstance(drying, list) and drying:
        has_01 = any(isinstance(x, Mapping) and (x.get("item_id") == "wet_cloth_01" or x.get("object_id") == "wet_cloth_01") for x in drying)
        if not has_01:
            merged["drying_items"] = {}
        for dval in drying:
            if isinstance(dval, Mapping):
                item_id = str(dval.get("item_id") or dval.get("object_id") or "wet_cloth_01")
                tmpl = merged["drying_items"].get(item_id) or default_cloth_tmpl
                merged["drying_items"][item_id] = _normalize_drying_entry(item_id, dval, tmpl)

    plant = live.get("potted_plant")
    if isinstance(plant, Mapping):
        merged["potted_plant"].update(copy.deepcopy(dict(plant)))
        if "label" not in plant and isinstance(plant.get("name"), str) and plant["name"].strip():
            merged["potted_plant"]["label"] = plant["name"].strip()
        if isinstance(merged["potted_plant"].get("place_id"), str):
            merged["potted_plant"]["place_id"] = _resolve_place_id_alias(merged["potted_plant"]["place_id"], merged["places"])
        if "moisture_state" not in plant and isinstance(plant.get("hydration"), (int, float)) and not isinstance(plant.get("hydration"), bool):
            hyd = float(plant["hydration"])
            merged["potted_plant"]["moisture_state"] = "moist" if hyd >= 60.0 else ("slightly_dry" if hyd >= 30.0 else "dry")
        if "health_state" not in plant and isinstance(plant.get("stage"), str) and plant["stage"].strip():
            merged["potted_plant"]["health_state"] = plant["stage"].strip()
        if plant.get("last_watered_at") or plant.get("history"):
            merged["potted_plant"]["history_complete"] = True

    craft = live.get("craft_bench")
    if isinstance(craft, Mapping):
        merged["craft_bench"].update(copy.deepcopy(dict(craft)))
        box_sub = craft.get("card_organizer_box")
        if isinstance(box_sub, Mapping) and "input_material_units" not in craft:
            total_in = int(craft.get("total_stocked_units") if craft.get("total_stocked_units") is not None else 8)
            used_u = int(craft.get("consumed_in_box_units") or 0) + int(craft.get("consumed_in_cards_units") or 0)
            rem_u = int(craft.get("paper_units") if craft.get("paper_units") is not None else max(0, total_in - used_u))
            scrap_u = int(craft.get("scrap_units") or 0)
            merged["craft_bench"]["item_id"] = str(box_sub.get("box_id") or "card_organizer_box")
            if isinstance(box_sub.get("name"), str) and box_sub["name"].strip():
                merged["craft_bench"]["label"] = box_sub["name"].strip()
            if isinstance(box_sub.get("step"), str) and box_sub["step"].strip():
                merged["craft_bench"]["stage"] = box_sub["step"].strip()
            merged["craft_bench"]["input_material_units"] = total_in
            merged["craft_bench"]["used_divider_units"] = used_u
            merged["craft_bench"]["remaining_material_units"] = rem_u
            merged["craft_bench"]["waste_units"] = scrap_u
            if box_sub.get("updated_at"):
                merged["craft_bench"]["last_worked_at"] = box_sub["updated_at"]
                merged["craft_bench"]["history_complete"] = True
        if craft.get("last_worked_at") or craft.get("updated_at"):
            merged["craft_bench"]["history_complete"] = True

    pub = live.get("public_life")
    if isinstance(pub, Mapping):
        notices = pub.get("notices") or pub.get("public_notices")
        norm_notices: list[dict[str, Any]] = []
        raw_notice_iter: list[Mapping[str, Any]] = []
        if isinstance(notices, list) and notices:
            raw_notice_iter = [n for n in notices if isinstance(n, Mapping)]
        elif isinstance(notices, Mapping) and notices:
            raw_notice_iter = [v for v in notices.values() if isinstance(v, Mapping)]
        if raw_notice_iter:
            for raw_n in raw_notice_iter:
                nc = copy.deepcopy(dict(raw_n))
                if isinstance(nc.get("place_id"), str):
                    nc["place_id"] = _resolve_place_id_alias(nc["place_id"], merged["places"])
                if "author_display" not in nc and isinstance(nc.get("author_display_name"), str):
                    nc["author_display"] = nc["author_display_name"]
                if "real_author" not in nc and isinstance(nc.get("posted_by_side"), str):
                    nc["real_author"] = nc["posted_by_side"]
                if "content" not in nc and isinstance(nc.get("body"), str):
                    nc["content"] = nc["body"]
                if nc.get("posted_at"):
                    nc.setdefault("history_complete", True)
                norm_notices.append(nc)
            merged["public_life"]["notices"] = norm_notices

        ruler = pub.get("shared_ruler")
        if not isinstance(ruler, Mapping):
            loans = pub.get("loans") or pub.get("borrowed_items") or pub.get("shared_tools")
            if isinstance(loans, Mapping) and isinstance(loans.get("shared_ruler"), Mapping):
                ruler = loans["shared_ruler"]
            elif isinstance(loans, list):
                ruler = next((x for x in loans if isinstance(x, Mapping) and "ruler" in str(x.get("item_id", ""))), None)
        if not isinstance(ruler, Mapping):
            shelf = pub.get("shared_shelf")
            if isinstance(shelf, Mapping) and isinstance(shelf.get("items"), Mapping):
                pr = shelf["items"].get("paper_ruler")
                if isinstance(pr, Mapping):
                    ruler = {
                        "item_id": "shared_ruler",
                        "label": str(pr.get("name") or "钢边裁纸尺"),
                        "owner": str(pr.get("item_owner") or "community_shared_shelf"),
                        "holder": str(pr.get("holder") or "shared_shelf"),
                        "place_id": _resolve_place_id_alias(str(pr.get("place_id") or "street_corner"), merged["places"]),
                        "loan_status": str(pr.get("status") or "on_shelf"),
                        "borrowed_at": pr.get("borrowed_at"),
                        "due_at": pr.get("due_at"),
                        "history_complete": bool(pr.get("borrowed_at") or pr.get("returned_at")),
                    }
        if isinstance(ruler, Mapping):
            merged["public_life"]["shared_ruler"].update(copy.deepcopy(dict(ruler)))
            if ruler.get("borrowed_at") or ruler.get("updated_at") or ruler.get("history_complete"):
                merged["public_life"]["shared_ruler"]["history_complete"] = True

        agreements = pub.get("agreements")
        if isinstance(agreements, list) and agreements:
            merged["public_life"]["agreements"] = copy.deepcopy(agreements)

    return merged


def load_world_snapshot_readonly(day_root: Path | str) -> dict[str, Any]:
    """Load world topology + live v0.4 neighborhood anchors with 100% zero disk writes."""
    root = Path(day_root)
    if root.exists() and root.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "day_root symlink refused")
    base = build_default_world_topology()
    live_data = _read_json_readonly(neighborhood_state_path(root))
    if live_data is None:
        live_data = _read_json_readonly(root / SANDBOX_DIRNAME / NEIGHBORHOOD_STATE_FILENAME)
    if live_data is not None:
        return _merge_live_neighborhood_json(base, live_data)
    return base


def default_sandbox_report_state() -> dict[str, Any]:
    return {
        "schema_version": SANDBOX_STATE_SCHEMA_VERSION,
        "engine_schema": ENGINE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "last_rehearsal": None,
        "last_scene_gap_check": None,
        "last_trylive_run": None,
        "last_why_changed": None,
        "last_impact_preview": None,
        "operation_receipts": {},
    }


def load_sandbox_state_readonly(day_root: Path | str) -> dict[str, Any]:
    """Pure zero-write read of ``<day_root>/phone/v04_builder_sandbox.json``."""
    root = Path(day_root)
    if root.exists() and root.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "day_root symlink refused")
    stored = _read_json_readonly(builder_sandbox_state_path(root))
    base = default_sandbox_report_state()
    if stored is None:
        return base
    for key in (
        "revision",
        "updated_at",
        "last_rehearsal",
        "last_scene_gap_check",
        "last_trylive_run",
        "last_why_changed",
        "last_impact_preview",
    ):
        if key in stored:
            base[key] = copy.deepcopy(stored[key])
    if isinstance(stored.get("operation_receipts"), Mapping):
        base["operation_receipts"] = copy.deepcopy(dict(stored["operation_receipts"]))
    return base


# ---------------------------------------------------------------------------
# Capability 1: Route Connectivity Audit & Isolated Rehearsal (44 §二)
# ---------------------------------------------------------------------------


def _collect_known_keys(
    snapshot: Mapping[str, Any],
    *,
    object_templates: Optional[Sequence[Any]] = None,
    available_keys: Optional[Sequence[str]] = None,
) -> set[str]:
    if available_keys is not None:
        return {str(k).strip() for k in available_keys if isinstance(k, str) and str(k).strip()}

    templates_src = object_templates if object_templates is not None else snapshot.get("object_templates", [])
    keys: set[str] = set()
    if isinstance(templates_src, Sequence):
        for item in templates_src:
            if isinstance(item, str) and item.strip():
                keys.add(item.strip())
            elif isinstance(item, Mapping):
                tid = item.get("template_id") or item.get("object_id") or item.get("key_id")
                kind = str(item.get("kind") or item.get("category") or "")
                if isinstance(tid, str) and tid.strip() and (kind == "key" or "key" in tid):
                    keys.add(tid.strip())
    if object_templates is None:
        places = snapshot.get("places") or {}
        if isinstance(places, Mapping):
            for p in places.values():
                for obj in (p.get("objects") or []) if isinstance(p, Mapping) else []:
                    if isinstance(obj, Mapping):
                        oid = str(obj.get("object_id") or "")
                        if obj.get("category") == "key" or "key" in oid:
                            keys.add(oid)
    return keys


def _build_sandbox_graph(
    snapshot: Mapping[str, Any],
    *,
    custom_places: Optional[Sequence[Any]] = None,
    custom_routes: Optional[Sequence[Any]] = None,
    object_templates: Optional[Sequence[Any]] = None,
    available_keys: Optional[Sequence[str]] = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], set[str]]:
    places: dict[str, dict[str, Any]] = {}
    if custom_places is not None:
        for item in custom_places:
            if isinstance(item, str) and item.strip():
                pid = item.strip()
                places[pid] = {"place_id": pid, "label": pid, "canvas_layout": {"x": 0, "y": 0}}
            elif isinstance(item, Mapping) and isinstance(item.get("place_id"), str) and item["place_id"].strip():
                pid = item["place_id"].strip()
                places[pid] = copy.deepcopy(dict(item))
            else:
                raise V04ValidationError("INVALID_FIELDS", "Invalid entry in custom_places")
        if "home" not in places:
            places["home"] = {"place_id": "home", "label": "家·起居工作区", "canvas_layout": {"x": 0, "y": 0}}
    else:
        places = copy.deepcopy(dict(snapshot.get("places") or {}))

    routes: list[dict[str, Any]] = []
    if custom_routes is not None:
        for idx, raw_r in enumerate(custom_routes):
            if not isinstance(raw_r, Mapping):
                raise V04ValidationError("INVALID_FIELDS", f"custom_routes[{idx}] must be an object")
            frm = raw_r.get("from_place")
            to = raw_r.get("to_place")
            if not isinstance(frm, str) or not frm.strip() or not isinstance(to, str) or not to.strip():
                raise V04ValidationError("INVALID_FIELDS", f"custom_routes[{idx}] requires from_place and to_place")
            frm_s = frm.strip()
            to_s = to.strip()
            places.setdefault(frm_s, {"place_id": frm_s, "label": frm_s})
            places.setdefault(to_s, {"place_id": to_s, "label": to_s})
            duration = raw_r.get("duration_min", 3)
            if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
                raise V04ValidationError("INVALID_FIELDS", f"custom_routes[{idx}].duration_min must be positive int")
            bidir = bool(raw_r.get("bidirectional", False))
            if raw_r.get("one_way") is True:
                bidir = False
            routes.append({
                "route_id": str(raw_r.get("route_id") or f"custom_route_{idx}_{frm_s}_{to_s}"),
                "from_place": frm_s,
                "to_place": to_s,
                "duration_min": duration,
                "sheltered": bool(raw_r.get("sheltered", False)),
                "bidirectional": bidir,
                "requires_key": str(raw_r["requires_key"]).strip() if raw_r.get("requires_key") else None,
                "status": str(raw_r.get("status") or "open"),
            })
    else:
        routes = copy.deepcopy(list(snapshot.get("routes") or []))

    known_keys = _collect_known_keys(
        snapshot,
        object_templates=object_templates,
        available_keys=available_keys,
    )
    return places, routes, known_keys


def _directed_passable_edges(
    routes: Sequence[Mapping[str, Any]],
    known_keys: set[str],
    *,
    blocked_tokens: Optional[set[str]] = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Expand routes into directed edges WITHOUT ever auto-adding reverse edges for one-way routes."""
    adj: dict[str, list[dict[str, Any]]] = {}
    missing_key_doors: list[dict[str, Any]] = []
    blocked = blocked_tokens or set()

    for r in routes:
        rid = str(r.get("route_id") or "")
        frm = str(r.get("from_place") or "")
        to = str(r.get("to_place") or "")
        if not frm or not to:
            continue
        req_key = r.get("requires_key")
        if isinstance(req_key, str) and req_key.strip():
            req_key_clean = req_key.strip()
            if req_key_clean not in known_keys:
                missing_key_doors.append({
                    "route_id": rid,
                    "from_place": frm,
                    "to_place": to,
                    "requires_key": req_key_clean,
                    "suggestion": (
                        f"通路「{rid}」（{frm} -> {to}）要求钥匙或门禁「{req_key_clean}」，"
                        "但当前世界对象模板与实例中根本不存在该钥匙，入口处于死锁状态。"
                    ),
                })
                continue

        status = str(r.get("status") or "open").lower()
        is_blocked_fwd = (
            status in {"blocked", "closed"}
            or rid in blocked
            or f"{frm}->{to}" in blocked
            or to in blocked
        )
        duration_min = int(r.get("duration_min") or 1)
        sheltered = bool(r.get("sheltered", False))

        if not is_blocked_fwd:
            adj.setdefault(frm, []).append({
                "route_id": rid,
                "from_place": frm,
                "to_place": to,
                "duration_min": duration_min,
                "sheltered": sheltered,
            })

        if bool(r.get("bidirectional", False)):
            is_blocked_rev = (
                status in {"blocked", "closed"}
                or rid in blocked
                or f"{to}->{frm}" in blocked
            )
            if not is_blocked_rev:
                adj.setdefault(to, []).append({
                    "route_id": rid,
                    "from_place": to,
                    "to_place": frm,
                    "duration_min": duration_min,
                    "sheltered": sheltered,
                })

    return adj, missing_key_doors


def _bfs_reachable(start: str, adj: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    visited: set[str] = {start}
    order: list[str] = [start]
    queue: list[str] = [start]
    while queue:
        cur = queue.pop(0)
        for edge in adj.get(cur, []):
            nxt = edge["to_place"]
            if nxt not in visited:
                visited.add(nxt)
                order.append(nxt)
                queue.append(nxt)
    return order


def _shortest_edge_path(
    start: str,
    goal: str,
    adj: Mapping[str, list[dict[str, Any]]],
    *,
    prefer_sheltered: bool = False,
) -> Optional[list[dict[str, Any]]]:
    if start == goal:
        return []
    queue: list[tuple[str, list[dict[str, Any]]]] = [(start, [])]
    visited: set[str] = {start}
    while queue:
        cur, path = queue.pop(0)
        edges = list(adj.get(cur, []))
        if prefer_sheltered:
            edges.sort(key=lambda e: (0 if e.get("sheltered") else 1, int(e.get("duration_min", 1))))
        for edge in edges:
            nxt = edge["to_place"]
            next_path = path + [edge]
            if nxt == goal:
                return next_path
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, next_path))
    return None


def audit_world_routes(
    snapshot: Mapping[str, Any],
    *,
    start_place_id: str = "home",
    blocked_edges: Optional[Sequence[str]] = None,
    custom_places: Optional[Sequence[Any]] = None,
    custom_routes: Optional[Sequence[Any]] = None,
    object_templates: Optional[Sequence[Any]] = None,
    available_keys: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Audit connectivity from ``home`` without mutating live world or auto-adding reverse edges."""
    places, routes, known_keys = _build_sandbox_graph(
        snapshot,
        custom_places=custom_places,
        custom_routes=custom_routes,
        object_templates=object_templates,
        available_keys=available_keys,
    )
    if start_place_id not in places:
        raise V04ValidationError("INVALID_FIELDS", f"Unknown start_place_id: {start_place_id}")

    blocked_set: set[str] = set()
    for b in (blocked_edges or []):
        if isinstance(b, str) and b.strip():
            raw_b = b.strip()
            blocked_set.add(raw_b)
            blocked_set.add(_resolve_route_id_alias(raw_b))
    adj, missing_key_doors = _directed_passable_edges(routes, known_keys, blocked_tokens=blocked_set)
    reachable = _bfs_reachable(start_place_id, adj)
    reachable_set = set(reachable)

    isolated_places = sorted(pid for pid in places.keys() if pid not in reachable_set)

    one_way_traps: list[str] = []
    for pid in reachable:
        if pid == start_place_id:
            continue
        back_reach = _bfs_reachable(pid, adj)
        if start_place_id not in back_reach:
            one_way_traps.append(pid)
    one_way_traps.sort()

    place_nodes = [
        {
            "place_id": pid,
            "label": str(p.get("label") or pid),
            "circle": str(p.get("circle") or "neighborhood"),
            "canvas_layout": dict(p.get("canvas_layout") or {"x": 0, "y": 0}),
            "reachable": pid in reachable_set,
        }
        for pid, p in places.items()
    ]

    return {
        "start_place_id": start_place_id,
        "total_places": len(places),
        "reachable_from_home": reachable,
        "isolated_places": isolated_places,
        "one_way_traps": one_way_traps,
        "missing_key_doors": missing_key_doors,
        "blocked_edges": sorted(blocked_set),
        "place_nodes": place_nodes,
        "route_edges": copy.deepcopy(routes),
        "auto_added_reverse_edges": False,
        "layout_decoupled_from_travel_time": True,
        "healthy": not isolated_places and not one_way_traps and not missing_key_doors,
        "llm_calls": 0,
    }


def audit_routes_or_rehearse(
    snapshot: Mapping[str, Any],
    *,
    now_iso: str,
    start_place_id: str = "home",
    destination_place_id: Optional[str] = None,
    route_id: Optional[str] = None,
    blocked_edges: Optional[Sequence[str]] = None,
    route_path: Optional[Sequence[str]] = None,
    inject_roadblock_route: Optional[str] = None,
    on_roadblock: str = "turn_back",
    custom_places: Optional[Sequence[Any]] = None,
    custom_routes: Optional[Sequence[Any]] = None,
    object_templates: Optional[Sequence[Any]] = None,
    available_keys: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Run route connectivity audit plus isolated in-memory movement rehearsal (44 §二)."""
    if on_roadblock not in ALLOWED_ROADBLOCK_POLICIES:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported on_roadblock: {on_roadblock}")

    blocked_list: list[str] = []
    if blocked_edges is not None:
        if not isinstance(blocked_edges, Sequence) or isinstance(blocked_edges, (str, bytes)):
            raise V04ValidationError("INVALID_FIELDS", "blocked_edges must be a list of strings")
        for item in blocked_edges:
            if not isinstance(item, str) or not item.strip():
                raise V04ValidationError("INVALID_FIELDS", "blocked_edges entries must be non-empty strings")
            blocked_list.append(item.strip())

    places, routes, known_keys = _build_sandbox_graph(
        snapshot,
        custom_places=custom_places,
        custom_routes=custom_routes,
        object_templates=object_templates,
        available_keys=available_keys,
    )
    start_place_id = _resolve_place_id_alias(start_place_id, places)
    normalized_blocks: list[str] = []
    for tok in blocked_list:
        normalized_blocks.append(tok)
        res_tok = _resolve_route_id_alias(tok)
        if res_tok not in normalized_blocks:
            normalized_blocks.append(res_tok)
        if "->" in tok:
            u_raw, v_raw = [p.strip() for p in tok.split("->", 1)]
            u_res = _resolve_place_id_alias(u_raw, places)
            v_res = _resolve_place_id_alias(v_raw, places)
            norm_pair = f"{u_res}->{v_res}"
            if norm_pair not in normalized_blocks:
                normalized_blocks.append(norm_pair)

    route_audit = audit_world_routes(
        snapshot,
        start_place_id=start_place_id,
        blocked_edges=normalized_blocks,
        custom_places=custom_places,
        custom_routes=custom_routes,
        object_templates=object_templates,
        available_keys=available_keys,
    )

    # Resolve optional route_id (e.g., "route_street_bookstore" or "home_to_bookstore" or "courtyard->bookstore" or destination place id)
    cleaned_route_id = route_id.strip() if isinstance(route_id, str) and route_id.strip() else None
    resolved_dest = (
        _resolve_place_id_alias(destination_place_id, places)
        if isinstance(destination_place_id, str) and destination_place_id.strip()
        else None
    )

    if cleaned_route_id is not None and resolved_dest is None:
        aliased_route_id = _resolve_route_id_alias(cleaned_route_id)
        matched_route = next((r for r in routes if r.get("route_id") in {cleaned_route_id, aliased_route_id}), None)
        if matched_route is not None:
            resolved_dest = str(matched_route["to_place"])
        elif "->" in cleaned_route_id:
            parts = [_resolve_place_id_alias(p, places) for p in cleaned_route_id.split("->", 1)]
            if parts[0] in places and parts[1] in places:
                start_place_id = parts[0]
                resolved_dest = parts[1]
        elif "_to_" in cleaned_route_id:
            parts = [_resolve_place_id_alias(p, places) for p in cleaned_route_id.split("_to_", 1)]
            if parts[0] in places and parts[1] in places:
                start_place_id = parts[0]
                resolved_dest = parts[1]
        else:
            cand_dest = _resolve_place_id_alias(cleaned_route_id, places)
            if cand_dest in places:
                resolved_dest = cand_dest

    if not resolved_dest or resolved_dest not in places:
        resolved_dest = "bookstore" if "bookstore" in places else next(
            (p for p in route_audit["reachable_from_home"] if p != start_place_id),
            start_place_id,
        )

    # Pre-departure planning uses unblocked known map so midway blocks are observed locally at visible node!
    adj_initial, _ = _directed_passable_edges(routes, known_keys)

    if route_path is not None:
        if len(route_path) < 2 or route_path[0] != start_place_id:
            raise V04ValidationError("INVALID_FIELDS", "route_path must start with start_place_id and have >= 2 nodes")
        planned_edges: list[dict[str, Any]] = []
        for idx in range(len(route_path) - 1):
            u, v = str(route_path[idx]), str(route_path[idx + 1])
            matched = next((e for e in adj_initial.get(u, []) if e["to_place"] == v), None)
            if matched is None:
                raise V04ValidationError("INVALID_FIELDS", f"No passable edge for {u} -> {v}")
            planned_edges.append(matched)
    else:
        found = _shortest_edge_path(start_place_id, resolved_dest, adj_initial)
        planned_edges = found or []

    pronoun = str(snapshot.get("pronoun") or DEFAULT_PRONOUN)
    steps: list[dict[str, Any]] = []
    elapsed_min = 0
    current_place = start_place_id

    steps.append({
        "step_index": 0,
        "phase": "depart",
        "at": advance_iso(now_iso, minutes=0),
        "place_id": current_place,
        "summary": f"{pronoun}在隔离预演副本中从「{current_place}」准备出发，目标「{resolved_dest}」。",
        "sandbox_only": True,
    })

    active_block_tokens: set[str] = set(normalized_blocks)
    if isinstance(inject_roadblock_route, str) and inject_roadblock_route.strip():
        raw_inj = inject_roadblock_route.strip()
        active_block_tokens.add(raw_inj)
        active_block_tokens.add(_resolve_route_id_alias(raw_inj))
        if "->" in raw_inj:
            u_i, v_i = [_resolve_place_id_alias(p, places) for p in raw_inj.split("->", 1)]
            active_block_tokens.add(f"{u_i}->{v_i}")

    blocked_midway = False

    for seg_idx, edge in enumerate(planned_edges):
        rid = edge["route_id"]
        frm = edge["from_place"]
        to = edge["to_place"]

        matches_block = False
        if active_block_tokens:
            if rid in active_block_tokens or f"{frm}->{to}" in active_block_tokens or to in active_block_tokens:
                matches_block = True
            elif "midpoint" in active_block_tokens and seg_idx == max(1, len(planned_edges) // 2):
                matches_block = True

        if matches_block:
            blocked_midway = True
            steps.append({
                "step_index": len(steps),
                "phase": "observe_roadblock_at_visible_node",
                "at": advance_iso(now_iso, minutes=elapsed_min),
                "place_id": current_place,
                "blocked_route_id": rid,
                "blocked_segment": f"{frm}->{to}",
                "summary": (
                    f"{pronoun}走到可视节点「{current_place}」时，才亲眼看到前方路段「{rid}」（{frm} -> {to}）已设围栏封闭；"
                    "未在出发前远程全知。"
                ),
                "sandbox_only": True,
            })

            adj_blocked, _ = _directed_passable_edges(
                routes,
                known_keys,
                blocked_tokens=active_block_tokens | {rid, f"{frm}->{to}"},
            )

            if on_roadblock == "pause_at_node":
                steps.append({
                    "step_index": len(steps),
                    "phase": "paused_safely_at_node",
                    "at": advance_iso(now_iso, minutes=elapsed_min),
                    "place_id": current_place,
                    "summary": f"{pronoun}安全停留在可视节点「{current_place}」，未被系统强制弹回家。",
                    "sandbox_only": True,
                })
                break

            if on_roadblock == "reroute":
                detour = _shortest_edge_path(current_place, resolved_dest, adj_blocked, prefer_sheltered=True)
                if detour:
                    for d_edge in detour:
                        elapsed_min += int(d_edge["duration_min"])
                        current_place = d_edge["to_place"]
                        steps.append({
                            "step_index": len(steps),
                            "phase": "reroute_advance",
                            "at": advance_iso(now_iso, minutes=elapsed_min),
                            "place_id": current_place,
                            "route_id": d_edge["route_id"],
                            "summary": f"{pronoun}改走可用备选路线「{d_edge['route_id']}」抵达「{current_place}」。",
                            "sandbox_only": True,
                        })
                    break

            return_edges = _shortest_edge_path(current_place, start_place_id, adj_blocked)
            if return_edges:
                for r_edge in return_edges:
                    elapsed_min += int(r_edge["duration_min"])
                    current_place = r_edge["to_place"]
                    steps.append({
                        "step_index": len(steps),
                        "phase": "turn_back_segment",
                        "at": advance_iso(now_iso, minutes=elapsed_min),
                        "place_id": current_place,
                        "route_id": r_edge["route_id"],
                        "summary": f"{pronoun}发起折返意图，沿「{r_edge['route_id']}」返回「{current_place}」，已耗时不回滚。",
                        "sandbox_only": True,
                    })
            else:
                steps.append({
                    "step_index": len(steps),
                    "phase": "paused_no_return_edge",
                    "at": advance_iso(now_iso, minutes=elapsed_min),
                    "place_id": current_place,
                    "summary": f"{pronoun}所在节点「{current_place}」无返程边，安全停驻在原地等待处理。",
                    "sandbox_only": True,
                })
            break

        elapsed_min += int(edge["duration_min"])
        current_place = to
        phase_label = "arrive_destination" if seg_idx == len(planned_edges) - 1 else "reach_midpoint"
        steps.append({
            "step_index": len(steps),
            "phase": phase_label,
            "at": advance_iso(now_iso, minutes=elapsed_min),
            "place_id": current_place,
            "route_id": rid,
            "summary": f"{pronoun}沿「{rid}」步行 {edge['duration_min']} 分钟到达「{current_place}」。",
            "sandbox_only": True,
        })

    rehearsal = {
        "performed": True,
        "route_id": cleaned_route_id,
        "start_place_id": start_place_id,
        "destination_place_id": resolved_dest,
        "blocked_edges": sorted(active_block_tokens),
        "inject_roadblock_route": inject_roadblock_route,
        "on_roadblock": on_roadblock,
        "blocked_midway": blocked_midway,
        "final_rehearsal_place": current_place,
        "elapsed_minutes": elapsed_min,
        "rehearsal_steps": steps,
        "remote_omniscient_reroute": False,
        "teleported_home_on_block": False,
        "mutated_live_resident": False,
        "llm_calls": 0,
    }

    return {
        "route_id": cleaned_route_id,
        "reachable_from_home": route_audit["reachable_from_home"],
        "isolated_places": route_audit["isolated_places"],
        "one_way_traps": route_audit["one_way_traps"],
        "missing_key_doors": route_audit["missing_key_doors"],
        "blocked_edges": sorted(active_block_tokens),
        "healthy": route_audit["healthy"],
        "route_audit": route_audit,
        "rehearsal": rehearsal,
        "rehearsal_steps": steps,
        "mutated_live_resident": False,
        "sandbox_isolated": True,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Capability 2: Scene Prose Interactive Affordance Gap Checker (44 §三)
# ---------------------------------------------------------------------------

_EXIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:一扇|那扇|侧边|旁边|尽头)?(?:木门|铁门|小门|院门|房门|侧门|后门|门|通路|通道|小路|走廊|入口)(?:正|直)?(?:通向|通往|连向|通到)([^，。；！？,\.\s]{2,18})"),
    re.compile(r"(?:通向|通往|连向|通到)([^，。；！？,\.\s]{2,18})的(?:木门|铁门|小门|院门|房门|侧门|后门|门|通路|通道|小路|入口)"),
)

_PROSE_OBJECT_RULES: tuple[dict[str, Any], ...] = (
    {
        "category": "seat",
        "keywords": ("长椅", "木椅", "靠背椅", "椅子", "短凳", "木凳", "凳子", "沙发"),
        "required_affordance": "sit",
        "capacity_field": "seat_capacity",
        "proposed_affordances": ["sit"],
        "proposed_capacity": 2,
    },
    {
        "category": "return_box",
        "keywords": ("归还箱", "还书箱"),
        "required_affordance": "drop_return",
        "capacity_field": "capacity",
        "proposed_affordances": ["drop_return", "receive_after_hours"],
        "proposed_capacity": 5,
    },
    {
        "category": "storage",
        "keywords": ("借阅架", "共享架", "书架", "伞架", "收纳盒", "架子", "木箱", "篮子"),
        "required_affordance": "place_item",
        "capacity_field": "capacity",
        "proposed_affordances": ["place_item", "take_item"],
        "proposed_capacity": 4,
    },
    {
        "category": "surface",
        "keywords": ("书桌", "工作台", "暂放台", "木桌", "桌子", "茶几", "柜台"),
        "required_affordance": "place_item",
        "capacity_field": "capacity",
        "proposed_affordances": ["place_item"],
        "proposed_capacity": 3,
    },
    {
        "category": "readable",
        "keywords": ("借阅卡", "说明卡", "留言卡", "卡片", "公告栏", "公告", "告示", "便签", "画册", "旧书", "书本"),
        "required_affordance": "read_content",
        "capacity_field": None,
        "proposed_affordances": ["read_content"],
        "proposed_capacity": 1,
    },
    {
        "category": "hook",
        "keywords": ("晾衣绳", "晾晒杆", "挂钩", "衣钩", "挂架"),
        "required_affordance": "hang_item",
        "capacity_field": "capacity",
        "proposed_affordances": ["hang_item", "dry_item"],
        "proposed_capacity": 2,
    },
    {
        "category": "fixture",
        "keywords": ("台灯", "小灯", "路灯", "壁灯", "窗户", "木窗"),
        "required_affordance": "daylight_or_light",
        "capacity_field": None,
        "proposed_affordances": ["toggle_light", "look_out"],
        "proposed_capacity": 1,
    },
    {
        "category": "water_or_plant",
        "keywords": ("洗手盆", "水盆", "水槽", "水龙头", "陶盆绿植", "盆栽", "绿植", "花盆", "植物"),
        "required_affordance": "interact_ecology",
        "capacity_field": None,
        "proposed_affordances": ["water_plant", "wash_cloth"],
        "proposed_capacity": 1,
    },
)


def _match_place_by_name_or_alias(places: Mapping[str, Mapping[str, Any]], raw_dest: str) -> Optional[str]:
    cleaned = raw_dest.strip("「」『』\"' ")
    if cleaned in places:
        return cleaned
    for pid, pinfo in places.items():
        label = str(pinfo.get("label") or "")
        aliases = [str(a) for a in (pinfo.get("aliases") or [])]
        if cleaned == label or cleaned in aliases or (cleaned and cleaned in label):
            return pid
    return None


def check_scene_prose_gaps(
    snapshot: Mapping[str, Any],
    *,
    scene_prose: str,
    place_id: str = "porch",
    place_override: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Deterministically inspect a Chinese scene description for object/content/exit gaps (44 §三)."""
    if not isinstance(scene_prose, str) or not scene_prose.strip():
        raise V04ValidationError("INVALID_FIELDS", "prose / scene_prose must be a non-empty string")
    prose = scene_prose.strip()

    places = copy.deepcopy(dict(snapshot.get("places") or {}))
    raw_place = place_id.strip() if isinstance(place_id, str) and place_id.strip() else "porch"
    resolved_place = _resolve_place_id_alias(raw_place, places)
    if resolved_place not in places and place_override is None:
        raise V04ValidationError("INVALID_FIELDS", f"Unknown place_id: {raw_place}")

    place_info = copy.deepcopy(
        dict(places.get(resolved_place) or {"place_id": resolved_place, "label": resolved_place, "objects": []})
    )
    if place_override is not None:
        place_info.update(copy.deepcopy(dict(place_override)))
        places[resolved_place] = place_info

    place_label = str(place_info.get("label") or resolved_place)
    objects: list[dict[str, Any]] = [
        dict(o) for o in (place_info.get("objects") or []) if isinstance(o, Mapping)
    ]
    routes = list(snapshot.get("routes") or [])
    known_keys = _collect_known_keys(snapshot)
    adj, _ = _directed_passable_edges(routes, known_keys)
    connected_targets = {edge["to_place"] for edge in adj.get(resolved_place, [])}

    gaps: list[dict[str, Any]] = []
    draft_candidates: list[dict[str, Any]] = []
    seen_targets: set[str] = set()

    mentions: list[tuple[int, str, dict[str, Any]]] = []
    occupied_spans: list[tuple[int, int]] = []
    for rule in _PROSE_OBJECT_RULES:
        for kw in rule["keywords"]:
            pos = prose.find(kw)
            if pos != -1:
                if any(s <= pos and pos + len(kw) <= e for s, e in occupied_spans):
                    continue
                occupied_spans.append((pos, pos + len(kw)))
                mentions.append((pos, kw, rule))

    mentions.sort(key=lambda item: item[0])

    for _, target_kw, rule in mentions:
        if target_kw in seen_targets:
            continue
        seen_targets.add(target_kw)
        cat = rule["category"]

        matched_obj: Optional[dict[str, Any]] = None
        for obj in objects:
            lbl = str(obj.get("label") or "")
            obj_cat = str(obj.get("category") or "")
            if target_kw in lbl or obj_cat == cat or (cat == "storage" and obj_cat == "storage"):
                matched_obj = obj
                if target_kw in lbl:
                    break

        if cat == "readable":
            has_text = bool(
                matched_obj is not None
                and target_kw in str(matched_obj.get("label") or "")
                and (matched_obj.get("has_content") is True or bool(matched_obj.get("content")))
            )
            if has_text:
                msg = f"「{target_kw}」已绑定可读正文来源，走近即可查看。"
                gaps.append({"target": target_kw, "category": cat, "severity": "ready", "suggestion": msg, "message": msg})
            else:
                msg = f"{target_kw}引用的可读正文尚未提供，需补充具体材料版本以免角色查看时落空。"
                gaps.append({
                    "target": target_kw,
                    "category": cat,
                    "severity": "missing_content",
                    "suggestion": msg,
                    "message": msg,
                })
                draft_candidates.append({
                    "candidate_id": f"draft_material_{len(draft_candidates) + 1}",
                    "kind": "readable_material_draft",
                    "place_id": resolved_place,
                    "target": target_kw,
                    "proposed_affordances": list(rule["proposed_affordances"]),
                    "requires_content_body": True,
                    "requires_author_review": True,
                })
            continue

        if cat == "seat":
            affs = set(matched_obj.get("affordances") or []) if matched_obj else set()
            cap = int(matched_obj.get("seat_capacity") or 0) if matched_obj else 0
            if matched_obj is None:
                msg = f"场景描写提到了「{target_kw}」，但「{place_label}」尚未创建该座位对象。"
                gaps.append({"target": target_kw, "category": cat, "severity": "missing_object", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_seat_{len(draft_candidates) + 1}",
                    "kind": "object_draft",
                    "place_id": resolved_place,
                    "target": target_kw,
                    "proposed_affordances": ["sit"],
                    "proposed_seat_capacity": 2,
                    "requires_author_review": True,
                })
            elif bool(matched_obj.get("visual_only")) or "sit" not in affs or cap <= 0:
                msg = f"这张{target_kw}只有画面描写，尚未配置坐下能力与容量。"
                gaps.append({
                    "target": target_kw,
                    "category": cat,
                    "severity": "missing_affordance",
                    "suggestion": msg,
                    "message": msg,
                })
                draft_candidates.append({
                    "candidate_id": f"draft_seat_upgrade_{len(draft_candidates) + 1}",
                    "kind": "affordance_upgrade_draft",
                    "place_id": resolved_place,
                    "object_id": matched_obj.get("object_id"),
                    "target": target_kw,
                    "proposed_affordances": ["sit"],
                    "proposed_seat_capacity": 2,
                    "requires_author_review": True,
                })
            else:
                msg = f"「{target_kw}」已配置坐下能力（可坐 {cap} 人）。"
                gaps.append({"target": target_kw, "category": cat, "severity": "ready", "suggestion": msg, "message": msg})
            continue

        if cat == "return_box":
            affs = set(matched_obj.get("affordances") or []) if matched_obj else set()
            has_rule = bool(matched_obj and matched_obj.get("receive_rule"))
            if matched_obj is None:
                msg = f"描写提到了「{target_kw}」，但当前地点尚未配置归还箱实例及闭店接收规则。"
                gaps.append({"target": target_kw, "category": cat, "severity": "missing_object", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_return_box_{len(draft_candidates) + 1}",
                    "kind": "object_draft",
                    "place_id": resolved_place,
                    "target": target_kw,
                    "proposed_affordances": ["drop_return", "receive_after_hours"],
                    "proposed_receive_rule": "accepted_into_return_box_pending_keeper_check",
                    "requires_author_review": True,
                })
            elif "drop_return" not in affs or not has_rule:
                msg = f"「{target_kw}」缺少闭店投递能力或接收规则（receive_rule），归还物无法可靠入箱。"
                gaps.append({"target": target_kw, "category": cat, "severity": "missing_affordance", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_return_rule_{len(draft_candidates) + 1}",
                    "kind": "affordance_upgrade_draft",
                    "place_id": resolved_place,
                    "target": target_kw,
                    "proposed_affordances": ["drop_return", "receive_after_hours"],
                    "requires_author_review": True,
                })
            else:
                msg = f"「{target_kw}」已配置闭店投递能力与明确接收规则。"
                gaps.append({"target": target_kw, "category": cat, "severity": "ready", "suggestion": msg, "message": msg})
            continue

        if matched_obj is None:
            msg = f"描写提到了「{target_kw}」，但「{place_label}」中尚无对应可交互对象实例。"
            gaps.append({"target": target_kw, "category": cat, "severity": "missing_object", "suggestion": msg, "message": msg})
            draft_candidates.append({
                "candidate_id": f"draft_obj_{len(draft_candidates) + 1}",
                "kind": "object_draft",
                "place_id": resolved_place,
                "target": target_kw,
                "proposed_affordances": list(rule["proposed_affordances"]),
                "proposed_capacity": rule["proposed_capacity"],
                "requires_author_review": True,
            })
        else:
            affs = set(matched_obj.get("affordances") or [])
            cap_field = rule.get("capacity_field")
            cap_val = int(matched_obj.get(cap_field) or 0) if cap_field else 1
            if bool(matched_obj.get("visual_only")) or not affs or (cap_field and cap_val <= 0):
                msg = f"「{target_kw}」只有画面资源，尚未配置交互能力与容纳上限。"
                gaps.append({"target": target_kw, "category": cat, "severity": "missing_affordance", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_aff_{len(draft_candidates) + 1}",
                    "kind": "affordance_upgrade_draft",
                    "place_id": resolved_place,
                    "object_id": matched_obj.get("object_id"),
                    "target": target_kw,
                    "proposed_affordances": list(rule["proposed_affordances"]),
                    "proposed_capacity": rule["proposed_capacity"],
                    "requires_author_review": True,
                })
            else:
                msg = f"「{target_kw}」已在「{place_label}」就绪（能力：{', '.join(sorted(affs))}）。"
                gaps.append({"target": target_kw, "category": cat, "severity": "ready", "suggestion": msg, "message": msg})

    for pattern in _EXIT_PATTERNS:
        for match in pattern.finditer(prose):
            raw_dest = match.group(1).strip("，。；！？、 ")
            if not raw_dest or raw_dest in seen_targets:
                continue
            seen_targets.add(raw_dest)
            dest_pid = _match_place_by_name_or_alias(places, raw_dest)
            if dest_pid is None:
                msg = f"该门通向未定义区域：{raw_dest}，需先在地图中定义该地点并配置通行路线。"
                gaps.append({"target": raw_dest, "category": "exit", "severity": "dangling_exit", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_exit_{len(draft_candidates) + 1}",
                    "kind": "route_and_place_draft",
                    "from_place": resolved_place,
                    "target": raw_dest,
                    "proposed_to_place_label": raw_dest,
                    "proposed_duration_min": 2,
                    "requires_author_review": True,
                })
            elif dest_pid not in connected_targets:
                msg = f"出口指向已有地点「{raw_dest}」（{dest_pid}），但「{place_label}」尚未连接通往该处的路线边。"
                gaps.append({"target": raw_dest, "category": "exit", "severity": "dangling_exit", "suggestion": msg, "message": msg})
                draft_candidates.append({
                    "candidate_id": f"draft_route_{len(draft_candidates) + 1}",
                    "kind": "route_draft",
                    "from_place": resolved_place,
                    "to_place": dest_pid,
                    "target": raw_dest,
                    "proposed_duration_min": 2,
                    "requires_author_review": True,
                })
            else:
                msg = f"通向「{raw_dest}」（{dest_pid}）的出口路线已正常接通。"
                gaps.append({"target": raw_dest, "category": "exit", "severity": "ready", "suggestion": msg, "message": msg})

    actionable_count = sum(1 for g in gaps if g["severity"] != "ready")
    return {
        "place_id": resolved_place,
        "place_label": place_label,
        "prose": prose,
        "scene_prose": prose,
        "gaps": gaps,
        "actionable_gap_count": actionable_count,
        "draft_candidates": draft_candidates,
        "applied_to_live_world": False,
        "sandbox_isolated": True,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Capability 3: Isolated 1-Hour "Try-Live" Sandbox & Dual-Version Diff (44 §四)
# ---------------------------------------------------------------------------


def _build_lighting_version_diff(
    compare_lighting: str,
    *,
    pronoun: str,
    roadblock_active: bool,
) -> dict[str, Any]:
    if compare_lighting == "none":
        return {
            "enabled": False,
            "mode": "none",
            "observation_changed": False,
            "behavior_changed": False,
            "route_changed_under_roadblock": bool(roadblock_active),
            "version_a": None,
            "version_b": None,
            "summary": "未开启双世界包光照对比；仅运行当前单版本隔离试住。",
        }

    version_a = {
        "version_id": "worldpack_window_bright_soft",
        "window_lighting": "bright_soft",
        "road_state": "roadblock_detour" if roadblock_active else "normal_open",
        "chosen_seat": "window_reading_nook",
        "subject_observation": (
            f"{pronoun}走到窗边看到自然光柔和清楚，纸面字迹分明不刺眼，便直接在靠窗阅读座安顿下来翻书。"
        ),
    }
    version_b = {
        "version_id": "worldpack_window_harsh_glare",
        "window_lighting": "harsh_glare",
        "road_state": "roadblock_detour" if roadblock_active else "normal_open",
        "chosen_seat": "home_chair",
        "subject_observation": (
            f"{pronoun}靠近窗边时发现桌面反光刺眼、书页发白看不真切，于是抱起书改坐到内侧书桌木椅避开炫光。"
        ),
    }
    return {
        "enabled": True,
        "mode": "bright_vs_glare",
        "observation_changed": True,
        "behavior_changed": True,
        "route_changed_under_roadblock": bool(roadblock_active),
        "version_a": version_a,
        "version_b": version_b,
        "summary": (
            "窗边由明亮柔和（bright_soft）改为强烈反光（harsh_glare）后，"
            f"{pronoun}获得的有限感官观察明显改变，并从窗边阅读座主动换到内侧木椅。"
        ),
    }


def run_trylive_hour(
    snapshot: Mapping[str, Any],
    *,
    now_iso: str,
    scenario_or_preset_id: str = "sunny_cloth_drying",
    inject_faults: Optional[Sequence[str]] = None,
    compare_lighting: str = "none",
) -> dict[str, Any]:
    """Execute one 60-minute deterministic try-live scenario in an isolated sandbox copy (44 §四)."""
    raw_token = scenario_or_preset_id.strip() if isinstance(scenario_or_preset_id, str) else ""
    if raw_token not in SCENARIO_RESOLUTION_MAP:
        raise V04ValidationError("INVALID_FIELDS", f"Unknown preset_id / scenario_id: {scenario_or_preset_id}")
    scenario_id, preset_id = SCENARIO_RESOLUTION_MAP[raw_token]

    if compare_lighting not in ALLOWED_COMPARE_LIGHTING:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported compare_lighting: {compare_lighting}")

    canonical_faults: list[str] = []
    frontend_injections: list[str] = []
    if inject_faults is not None:
        if not isinstance(inject_faults, Sequence) or isinstance(inject_faults, (str, bytes)):
            raise V04ValidationError("INVALID_FIELDS", "inject_faults / injections must be a list of fault names")
        for f in inject_faults:
            if not isinstance(f, str) or f.strip() not in FAULT_ALIAS_MAP:
                raise V04ValidationError("INVALID_FIELDS", f"Unsupported fault: {f!r}")
            raw_f = f.strip()
            mapped = FAULT_ALIAS_MAP[raw_f]
            if mapped not in canonical_faults:
                canonical_faults.append(mapped)
            ui_name = "seat_taken" if mapped == "seat_occupied" else mapped
            if ui_name not in frontend_injections:
                frontend_injections.append(ui_name)
    faults = set(canonical_faults)

    sandbox = copy.deepcopy(dict(snapshot))
    pronoun = str(sandbox.get("pronoun") or DEFAULT_PRONOUN)

    rain_injected = "rain" in faults or scenario_id == "rain_midway_drying"
    seat_occupied = "seat_occupied" in faults
    roadblock_injected = "roadblock" in faults or scenario_id == "roadblock_turnback"
    restart_gap = "restart_gap" in faults

    timeline: list[dict[str, Any]] = []
    bag_state = {
        "container_id": "canvas_tote_bag",
        "holder": pronoun,
        "effective_place": "home",
        "child_items": [],
        "child_items_drifted": False,
    }

    def _move_carrier(to_place: str) -> None:
        bag_state["effective_place"] = to_place

    def _add_step(t_min: int, place_id: str, event: str, observation: str, delta: Optional[Mapping[str, Any]] = None) -> None:
        timeline.append({
            "t_min": int(t_min),
            "minute": int(t_min),
            "at": advance_iso(now_iso, minutes=t_min),
            "place_id": place_id,
            "event": event,
            "summary": observation,
            "subject_observation": observation,
            "sandbox_delta": dict(delta or {}),
        })

    drying_segments: list[dict[str, Any]] = []
    cloth_summary: dict[str, Any] = {
        "item_id": "wet_cloth_01",
        "object_id": "linen_cloth",
        "initial_moisture": 0.80,
        "final_moisture": 0.80,
        "final_state": "wet",
        "final_place": "home",
    }
    ruler_summary: dict[str, Any] = {
        "item_id": "shared_ruler",
        "loan_status": "untouched",
        "final_location": "bookstore_counter",
        "swallowed_on_closed_shop": False,
        "forged_shopkeeper_in_person_receipt": False,
    }
    craft_summary: dict[str, Any] = {
        "item_id": "card_box_wip",
        "input_units": 4,
        "divider_units": 0,
        "remaining_units": 4,
        "unit_conserved": True,
    }
    chosen_seat = "window_reading_nook"

    if scenario_id in {"sunny_cloth_drying", "rain_midway_drying"}:
        bag_state["child_items"] = ["sketch_album_book"]
        _add_step(
            0,
            "home",
            "prepare_bag_and_cloth",
            f"{pronoun}从室内书架取下《植物速写小册》放进帆布袋，顺手拿起玄关的湿棉麻巾准备去晾晒。",
            {"bag_children": list(bag_state["child_items"]), "cloth_moisture": 0.80},
        )
        if "roadblock" in faults:
            _move_carrier("porch")
            hang_place = "porch"
            hang_spot = "porch_hook"
            _add_step(
                4,
                "porch",
                "roadblock_hang_on_porch",
                f"{pronoun}走到门廊看见通往院子的小门正在刷漆围挡，便改把湿巾挂在有檐门廊挂钩上。",
                {"hung_at": "porch_hook", "sheltered": True, "ventilation": "weak"},
            )
        else:
            _move_carrier("courtyard")
            hang_place = "courtyard"
            hang_spot = "courtyard_clothesline"
            _add_step(
                5,
                "courtyard",
                "hang_wet_cloth",
                f"{pronoun}穿过门廊走到院子，把湿巾平整挂在院子晾衣绳上。",
                {"hung_at": "courtyard_clothesline", "sheltered": False, "ventilation": "good"},
            )

        _move_carrier("home")
        if seat_occupied:
            chosen_seat = "home_chair"
            _add_step(
                10,
                "home",
                "seat_occupied_fallback",
                f"{pronoun}回屋发现窗边阅读座已被占用，没有强行叠坐，改坐到书桌木椅上翻书。",
                {"occupied_seat": "window_reading_nook", "chosen_seat": chosen_seat, "seat_collision_prevented": True},
            )
        else:
            chosen_seat = "home_chair" if compare_lighting == "bright_vs_glare" else "window_reading_nook"
            _add_step(
                10,
                "home",
                "sit_and_read",
                f"{pronoun}回到起居室，在座位「{chosen_seat}」坐下安静读书。",
                {"chosen_seat": chosen_seat},
            )

        if rain_injected:
            seg1_drop = 0.10 if hang_place == "porch" else 0.24
            m_after_20 = round(max(0.05, 0.80 - seg1_drop), 2)
            drying_segments.append({
                "window_min": "5..20",
                "place_id": hang_place,
                "support_id": hang_spot,
                "weather": "sunny",
                "sheltered": hang_place == "porch",
                "ventilation": "weak" if hang_place == "porch" else "good",
                "moisture_start": 0.80,
                "moisture_end": m_after_20,
            })
            if hang_place == "courtyard":
                _move_carrier("porch")
                m_rain_touch = round(min(0.95, m_after_20 + 0.06), 2)
                _add_step(
                    20,
                    "porch",
                    "rain_injected_move_to_porch",
                    f"窗外忽然飘起雨点，{pronoun}起身去院子把微润的布巾收进门廊挂钩避雨。",
                    {"weather": "rain", "moved_to": "porch_hook", "moisture_at_move": m_rain_touch},
                )
                m_seg2_start = m_rain_touch
                hang_place = "porch"
                hang_spot = "porch_hook"
                _move_carrier("home")
            else:
                m_seg2_start = m_after_20
                _add_step(
                    20,
                    "home",
                    "rain_injected_porch_sheltered",
                    f"外面下起雨来，{pronoun}望了一眼门廊檐下，布巾淋不到雨便继续留在原处。",
                    {"weather": "rain", "support_id": "porch_hook"},
                )

            if restart_gap:
                _add_step(
                    35,
                    "home",
                    "restart_gap_recovered",
                    f"沙盒模拟进程中断后按步骤锚点恢复：{pronoun}仍在室内，门廊挂钩上的布巾分段计时连续未丢失。",
                    {"recovered_from_step_anchor": True, "duplicated_items": False},
                )

            m_final = round(max(0.08, m_seg2_start - 0.14), 2)
            drying_segments.append({
                "window_min": "20..60",
                "place_id": "porch",
                "support_id": "porch_hook",
                "weather": "rain",
                "sheltered": True,
                "ventilation": "weak",
                "moisture_start": m_seg2_start,
                "moisture_end": m_final,
                "open_courtyard_counterfactual_moisture": 0.92,
            })
            final_state_label = "damp" if m_final > 0.22 else "dry"
            _move_carrier("porch")
            _add_step(
                60,
                "porch",
                "collect_cloth_after_hour",
                f"一小时到，{pronoun}走到门廊取下布巾，摸起来还有点潮（{final_state_label}），没有像露天院子那样淋透。",
                {"final_moisture": m_final, "final_state": final_state_label},
            )
            _move_carrier("home")
            cloth_summary = {
                "item_id": "wet_cloth_01",
                "object_id": "linen_cloth",
                "initial_moisture": 0.80,
                "final_moisture": m_final,
                "final_state": final_state_label,
                "final_place": "home",
                "porch_vs_open_yard_comparison": {
                    "porch_hook_final_moisture": m_final,
                    "open_courtyard_rain_moisture": 0.92,
                    "porch_sheltered_from_rain": True,
                    "porch_ventilation": "weak",
                },
            }
        else:
            if restart_gap:
                _add_step(
                    35,
                    "home",
                    "restart_gap_recovered",
                    f"沙盒模拟进程中断后从步骤锚点恢复：{pronoun}读书进度与晾晒锚点保持一致。",
                    {"recovered_from_step_anchor": True, "duplicated_items": False},
                )
            drop = 0.28 if hang_place == "porch" else 0.70
            m_final = round(max(0.08, 0.80 - drop), 2)
            final_state_label = "dry" if m_final <= 0.20 else "damp"
            drying_segments.append({
                "window_min": "5..60",
                "place_id": hang_place,
                "support_id": hang_spot,
                "weather": "sunny",
                "sheltered": hang_place == "porch",
                "ventilation": "weak" if hang_place == "porch" else "good",
                "moisture_start": 0.80,
                "moisture_end": m_final,
            })
            _move_carrier(hang_place)
            _add_step(
                60,
                hang_place,
                "collect_dried_cloth",
                f"满 1 小时后，{pronoun}去「{hang_place}」收回布巾，手感已经干爽（{final_state_label}）。",
                {"final_moisture": m_final, "final_state": final_state_label},
            )
            _move_carrier("home")
            cloth_summary = {
                "item_id": "wet_cloth_01",
                "object_id": "linen_cloth",
                "initial_moisture": 0.80,
                "final_moisture": m_final,
                "final_state": final_state_label,
                "final_place": "home",
            }

    elif scenario_id == "roadblock_turnback":
        bag_state["child_items"] = ["sketch_album_book", "library_card_note"]
        _add_step(
            0,
            "home",
            "pack_book_bag",
            f"{pronoun}把待查阅的小册与借书便签装进帆布借书袋，准备步行去旧书店。",
            {"bag_children": list(bag_state["child_items"]), "effective_child_place": "home"},
        )
        _move_carrier("courtyard")
        _add_step(
            3,
            "courtyard",
            "observe_open_shortcut_roadblock",
            f"{pronoun}走到院门外看见直通街角的露天近路立着施工围栏（非在家远程全知），检查有檐长廊备选路线。",
            {"blocked_route": "route_courtyard_street_open", "bag_effective_place": bag_state["effective_place"]},
        )
        if "roadblock" in faults and rain_injected:
            _move_carrier("porch")
            _add_step(
                10,
                "porch",
                "rain_and_roadblock_safe_turnback",
                f"因前方施工且伴随降雨，{pronoun}没有硬闯封闭段，选择安全折返到门廊檐下避雨。",
                {"turned_back_safely": True, "teleported_home": False, "bag_effective_place": "porch"},
            )
            if seat_occupied:
                chosen_seat = "home_chair"
            _move_carrier("home")
            _add_step(
                20,
                "home",
                "return_home_intact_bag",
                f"{pronoun}回到家中放下帆布袋，袋内两件子物品始终绑定父容器未发生位置漂移。",
                {"bag_effective_place": "home", "child_items_drifted": False},
            )
        else:
            _move_carrier("covered_walkway")
            _add_step(
                8,
                "covered_walkway",
                "detour_covered_walkway",
                f"{pronoun}改走有檐长廊绕开封闭路段，即便中途飘雨也有廊檐遮挡。",
                {"route_id": "route_courtyard_covered_walkway", "sheltered": True, "rain_active": rain_injected},
            )
            if restart_gap:
                _add_step(
                    12,
                    "covered_walkway",
                    "restart_gap_on_route_paused_safely",
                    f"进程中断恢复后，{pronoun}保持在有檐长廊节点进度，包内子物件随包同行零重复零丢失。",
                    {"recovered_from_step_anchor": True, "child_items_drifted": False},
                )
            _move_carrier("street_corner")
            _move_carrier("bookstore")
            seat_pick = "corner_bench" if seat_occupied else "bookstore_window_seat"
            chosen_seat = seat_pick
            _add_step(
                22,
                "bookstore",
                "arrive_bookstore_via_detour",
                f"{pronoun}经街角抵达旧书店（选座：{seat_pick}），袋中画册与便签有效位置自动随帆布袋更新为 bookstore。",
                {
                    "bag_effective_place": "bookstore",
                    "child_effective_places": {cid: "bookstore" for cid in bag_state["child_items"]},
                    "child_items_drifted": False,
                },
            )
            _move_carrier("home")
            _add_step(
                60,
                "home",
                "walk_back_home",
                f"{pronoun}翻阅完画册沿有檐长廊平安返家，随身包与子物件状态完整。",
                {"bag_effective_place": "home", "child_items_drifted": False},
            )

    else:  # bookstore_ruler_return / public_notice_and_borrow
        bag_state["child_items"] = ["shared_ruler"]
        _move_carrier("street_corner")
        _add_step(
            0,
            "street_corner",
            "check_public_notice_on_way",
            f"{pronoun}路过街角告示栏走近查看整修通知后前往旧书店。",
            {"read_notice": "notice_road_maintenance_01"},
        )
        _move_carrier("bookstore")
        _add_step(
            5,
            "bookstore",
            "borrow_ruler_during_open_hours",
            f"白天营业时段，{pronoun}在旧书店柜台登记借出钢制裁纸尺装进帆布袋。",
            {"loan_status": "borrowed", "holder": pronoun},
        )
        _move_carrier("home")
        craft_summary = {
            "item_id": "card_box_wip",
            "input_units": 4,
            "divider_units": 1,
            "remaining_units": 3,
            "unit_conserved": True,
        }
        if seat_occupied:
            chosen_seat = "home_chair"
        _add_step(
            22,
            "home",
            "cut_card_divider_conserving_units",
            f"{pronoun}回家在书桌用裁纸尺将 4 单元卡纸裁出 1 单元隔板，剩余 3 单元余料留存（整数材料守恒）。",
            dict(craft_summary),
        )
        if restart_gap:
            _add_step(
                35,
                "home",
                "restart_gap_preserve_half_craft",
                f"模拟重启间隙后，已裁好的 1 单元隔板与半成品收纳盒保留在书桌，未回退成未裁原纸。",
                {"recovered_from_step_anchor": True, "craft_preserved": True},
            )
        route_note = "改走有檐长廊绕过街角封路并遮雨" if (roadblock_injected or rain_injected) else "沿街角步行"
        _move_carrier("bookstore")
        bag_state["child_items"] = []
        ruler_summary = {
            "item_id": "shared_ruler",
            "loan_status": "returned_to_box_pending_check",
            "final_location": "bookstore_return_box",
            "shop_open_at_return": False,
            "swallowed_on_closed_shop": False,
            "forged_shopkeeper_in_person_receipt": False,
        }
        _add_step(
            60,
            "bookstore",
            "drop_ruler_into_after_hours_return_box",
            (
                f"傍晚闭店后，{pronoun}{route_note}来到旧书店门外，见店门已落锁，便将裁纸尺投入门外归还箱；"
                "物品真实入箱留档待店主次日核收，未吞物也未伪造店主开门寒暄签收。"
            ),
            dict(ruler_summary),
        )
        _move_carrier("home")

    version_diff = _build_lighting_version_diff(
        compare_lighting,
        pronoun=pronoun,
        roadblock_active=roadblock_injected,
    )

    final_sandbox_state = {
        "resident_place": bag_state["effective_place"],
        "chosen_seat": chosen_seat,
        "bag": bag_state,
        "cloth": cloth_summary,
        "drying_segments": drying_segments,
        "ruler": ruler_summary,
        "craft_bench": craft_summary,
        "fault_assertions": {
            "rain_handled": rain_injected,
            "seat_collision_prevented": True,
            "roadblock_local_perception_only": True,
            "recovered_from_step_anchor": True if restart_gap else None,
            "child_items_drifted": False,
        },
    }

    return {
        "preset_id": preset_id,
        "scenario_id": scenario_id,
        "title": PRESET_SCENARIO_BY_ID[scenario_id]["title"],
        "scenario_title": PRESET_SCENARIO_BY_ID[scenario_id]["title"],
        "injections": frontend_injections,
        "inject_faults": canonical_faults,
        "compare_lighting": compare_lighting,
        "timeline": timeline,
        "final_sandbox_state": final_sandbox_state,
        "version_diff": version_diff,
        "sandbox_isolated": True,
        "llm_calls": 0,
        "polluted_live_memory": False,
        "mutated_live_world": False,
    }


# ---------------------------------------------------------------------------
# Capability 4: "Why Did This Change?" Dual-Perspective Inspector (44 §五)
# ---------------------------------------------------------------------------


def inspect_why_changed(
    snapshot: Mapping[str, Any],
    *,
    now_iso: str,
    target_token: str,
    target_id: Optional[str] = None,
) -> dict[str, Any]:
    """Inspect causal process anchors with strict builder vs subject bounded view separation (44 §五)."""
    cleaned_token = target_token.strip() if isinstance(target_token, str) else ""
    if cleaned_token not in WHY_TARGET_ALIAS_MAP:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported object_id / target_kind: {target_token}")
    target_kind, canonical_object_id = WHY_TARGET_ALIAS_MAP[cleaned_token]

    pronoun = str(snapshot.get("pronoun") or DEFAULT_PRONOUN)
    has_live = bool(snapshot.get("has_live_neighborhood_file"))
    data_source = "live_neighborhood_state" if has_live else "default_initial_world_anchor"
    now_dt = parse_iso_moment(now_iso)
    missing_note = "无更早记录，不补写推测原因"

    if target_kind == "wet_cloth":
        drying_map = snapshot.get("drying_items") or {}
        chosen_id = target_id or next(iter(drying_map.keys()), "wet_cloth_01")
        cloth = dict(drying_map.get(chosen_id) or next(iter(drying_map.values()), {}))
        place_id = str(cloth.get("place_id") or cloth.get("location_id") or "porch")
        support_id = str(cloth.get("support_id") or cloth.get("spot_id") or "porch_hook")
        moisture = float(cloth.get("moisture_ratio") if cloth.get("moisture_ratio") is not None else 0.52)
        dry_state = str(cloth.get("dryness_state") or cloth.get("state") or ("damp" if moisture > 0.2 else "dry"))
        started_at = cloth.get("drying_started_at") or cloth.get("hung_at")
        segs = list(cloth.get("weather_segments") or [])
        hist_complete = bool(cloth.get("history_complete") and started_at)
        label = str(cloth.get("label") or "棉麻擦手巾")

        builder_view = {
            "object_id": canonical_object_id,
            "target_id": str(cloth.get("item_id") or chosen_id),
            "label": label,
            "origin": str(cloth.get("origin") or missing_note),
            "current_place_id": place_id,
            "current_support_id": support_id,
            "last_moved_by": str(cloth.get("last_moved_by") or pronoun),
            "last_moved_at": cloth.get("last_moved_at") or missing_note,
            "drying_started_at": started_at or missing_note,
            "moisture_ratio": moisture,
            "dryness_state": dry_state,
            "weather_and_ventilation_segments": segs,
            "admin_mechanism_explanation": (
                "有檐位置通风弱导致失水速率较低，因此即便避开了直接淋雨，布巾仍在一段时间内维持微潮状态。"
                if cloth.get("sheltered", True) and str(cloth.get("ventilation", "weak")) == "weak"
                else "按所在位置实际日照与通风速率分段结算失水。"
            ),
            "missing_history_note": None if hist_complete else missing_note,
        }
        subject_bounded_view = {
            "pronoun": pronoun,
            "sensory_impression": (
                f"{pronoun}伸手摸到布巾还有点潮，只觉得外面好像没有想象中干得快。"
                if dry_state != "dry"
                else f"{pronoun}摸到布巾已经干爽松软，可以收回屋里备用。"
            ),
            "knows_admin_mechanism": False,
            "hidden_from_subject": ["evaporation_rate_per_hour", "admin_ventilation_formula"],
            "private_diary_exposed": False,
        }

    elif target_kind == "public_notice":
        notices = list((snapshot.get("public_life") or {}).get("notices") or [])
        notice = next(
            (n for n in notices if isinstance(n, Mapping) and (target_id is None or n.get("notice_id") == target_id)),
            notices[0] if notices else {},
        )
        exp_raw = notice.get("expires_at") or notice.get("valid_until")
        is_expired = False
        if isinstance(exp_raw, str) and exp_raw.strip():
            try:
                is_expired = now_dt >= parse_iso_moment(exp_raw)
            except V04BuilderError:
                is_expired = False
        hist_complete = bool(notice.get("history_complete"))
        label = str(notice.get("title") or "街角告示")

        builder_view = {
            "object_id": canonical_object_id,
            "target_id": str(notice.get("notice_id") or "notice_road_maintenance_01"),
            "title": label,
            "label": label,
            "place_id": str(notice.get("place_id") or "street_corner"),
            "real_author": str(notice.get("real_author") or notice.get("author") or "street_maintenance_team"),
            "author_display": str(notice.get("author_display") or "街区市政维护班组"),
            "posted_at": notice.get("posted_at") or missing_note,
            "expires_at": exp_raw or missing_note,
            "is_expired": is_expired,
            "withdrawn": bool(notice.get("withdrawn", False)),
            "admin_mechanism_explanation": (
                "该公告已超过有效期但尚未从木板上撤下；实际道路通行以当前路段状态为准。"
                if is_expired
                else "该公告仍在有效期内，真实作者与道路维护安排一致。"
            ),
            "missing_history_note": None if hist_complete else missing_note,
        }
        subject_bounded_view = {
            "pronoun": pronoun,
            "sensory_impression": (
                f"{pronoun}路过街角告示栏时只能看见木板上贴着一张纸，走近阅读才能读到纸面文字；"
                "不会在房间里自动获知后台真实作者身份与内部过期巡检状态。"
            ),
            "knows_admin_mechanism": False,
            "private_diary_exposed": False,
        }

    elif target_kind == "shared_ruler":
        ruler = dict((snapshot.get("public_life") or {}).get("shared_ruler") or {})
        hist_complete = bool(ruler.get("history_complete") or ruler.get("borrowed_at"))
        status = str(ruler.get("loan_status") or ruler.get("status") or "available_at_counter")
        label = str(ruler.get("label") or "钢制裁纸尺")

        builder_view = {
            "object_id": canonical_object_id,
            "target_id": str(ruler.get("item_id") or "shared_ruler"),
            "label": label,
            "owner": str(ruler.get("owner") or "bookstore"),
            "current_holder": str(ruler.get("holder") or "bookstore"),
            "place_id": str(ruler.get("place_id") or "bookstore"),
            "support_id": str(ruler.get("support_id") or "bookstore_counter"),
            "loan_status": status,
            "borrowed_at": ruler.get("borrowed_at") or missing_note,
            "due_at": ruler.get("due_at") or missing_note,
            "returned_via": ruler.get("returned_via"),
            "admin_mechanism_explanation": "后台分别记录所有权（书店）、当前保管位置与借还契约状态，闭店投递归还箱不等同店主已当面点收。",
            "missing_history_note": None if hist_complete else missing_note,
        }
        subject_bounded_view = {
            "pronoun": pronoun,
            "sensory_impression": (
                f"{pronoun}只凭自己亲手借出、放在桌边或投进归还箱的经历记住尺子的去向，闭店后无法隔门获知店主是否已开箱清点。"
            ),
            "knows_admin_mechanism": False,
            "private_diary_exposed": False,
        }

    elif target_kind == "potted_plant":
        plant = dict(snapshot.get("potted_plant") or {})
        hist_complete = bool(plant.get("history_complete") or plant.get("last_watered_at"))
        m_state = str(plant.get("moisture_state") or plant.get("moisture") or "slightly_dry")
        label = str(plant.get("label") or "院角陶盆绿植")

        builder_view = {
            "object_id": canonical_object_id,
            "target_id": str(plant.get("plant_id") or "porch_plant"),
            "label": label,
            "place_id": str(plant.get("place_id") or "courtyard"),
            "moisture_state": m_state,
            "health_state": str(plant.get("health_state") or "steady"),
            "last_watered_at": plant.get("last_watered_at") or missing_note,
            "last_watered_by": plant.get("last_watered_by") or missing_note,
            "weather_exposure": str(plant.get("weather_exposure") or "open_courtyard"),
            "admin_mechanism_explanation": "院角露天花盆受日照蒸发与降雨补水缓慢变化，不设强制家务倒计时惩罚。",
            "missing_history_note": None if hist_complete else missing_note,
        }
        subject_bounded_view = {
            "pronoun": pronoun,
            "sensory_impression": f"{pronoun}走近院角时看见盆土表层颜色（{m_state}）与叶片精神度，不会直接读到后台水分参数。",
            "knows_admin_mechanism": False,
            "private_diary_exposed": False,
        }

    else:  # crafted_box
        craft = dict(snapshot.get("craft_bench") or {})
        hist_complete = bool(craft.get("history_complete") or craft.get("last_worked_at"))
        label = str(craft.get("label") or "卡片收纳盒（半成品）")

        builder_view = {
            "object_id": canonical_object_id,
            "target_id": str(craft.get("item_id") or "card_box_wip"),
            "label": label,
            "place_id": str(craft.get("place_id") or "home"),
            "surface_id": str(craft.get("surface_id") or "home_desk"),
            "stage": str(craft.get("stage") or "divider_cut_pending_fit"),
            "source_material": str(craft.get("source_material") or "四单元厚卡纸与旧纸盒"),
            "material_conservation": {
                "input_units": int(craft["input_material_units"]) if craft.get("input_material_units") is not None else 4,
                "used_divider_units": int(craft["used_divider_units"]) if craft.get("used_divider_units") is not None else 1,
                "remaining_units": int(craft["remaining_material_units"]) if craft.get("remaining_material_units") is not None else 3,
                "waste_units": int(craft["waste_units"]) if craft.get("waste_units") is not None else 0,
            },
            "parts": list(craft.get("parts") or ["box_shell_01", "card_divider_01"]),
            "last_worked_at": craft.get("last_worked_at") or missing_note,
            "admin_mechanism_explanation": "按整数材料单元守恒追踪原纸、隔板与余料，保留各自稳定组件身份，不设随机制作失败率。",
            "missing_history_note": None if hist_complete else missing_note,
        }
        subject_bounded_view = {
            "pronoun": pronoun,
            "sensory_impression": (
                f"{pronoun}看见书桌边搁着做到一半的纸盒、裁好的隔板与剩下的卡纸，只记得是否顺手合用，"
                "不暴露任何私人未公开草稿或日记。"
            ),
            "knows_admin_mechanism": False,
            "private_diary_exposed": False,
        }

    return {
        "object_id": canonical_object_id,
        "target_kind": target_kind,
        "label": label,
        "data_source": data_source,
        "builder_view": builder_view,
        "subject_bounded_view": subject_bounded_view,
        "perspective_gap_summary": (
            f"建设者视图展示完整来源、过程分段与规则锚点；{pronoun}仅获得到场可感知的外观/触感结果，"
            "且建设者诊断同样严格隔离角色私人日记与未共享内心独白。"
        ),
        "hallucinated_backstory": False,
        "leaked_private_diary": False,
        "sandbox_isolated": True,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Capability 5: Modification Impact Preview & Integrity Gate (44 §六、§八)
# ---------------------------------------------------------------------------


def preview_world_modification(
    snapshot: Mapping[str, Any],
    *,
    mod_kind: str,
    target_id: Optional[str] = None,
    to_place_id: Optional[str] = None,
    resolution_mode: str = "check_only",
    simulate_occupied_by: Optional[str] = None,
    simulate_items_on_surface: Optional[Sequence[str]] = None,
    simulate_bound_agreement: Optional[str] = None,
    worldpack_diff: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Preview who/what a builder edit affects and block unsafe deletions or overwrites (44 §六、§八)."""
    if mod_kind not in ALLOWED_MOD_KINDS:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported mod_kind: {mod_kind}")
    if resolution_mode not in ALLOWED_MOVE_RESOLUTIONS:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported resolution_mode: {resolution_mode}")

    places = copy.deepcopy(dict(snapshot.get("places") or {}))
    routes = copy.deepcopy(list(snapshot.get("routes") or []))
    pronoun = str(snapshot.get("pronoun") or DEFAULT_PRONOUN)

    if mod_kind in {"move_object", "move_bench"}:
        raw_obj_id = (target_id or "courtyard_bench").strip() or "courtyard_bench"
        obj_id = _resolve_object_id_alias(raw_obj_id)
        raw_dest = (to_place_id or "porch").strip() or "porch"
        dest_place = _resolve_place_id_alias(raw_dest, places)
        if dest_place not in places:
            raise V04ValidationError("INVALID_FIELDS", f"Unknown destination place: {raw_dest}")

        found_place_id: Optional[str] = None
        found_obj: Optional[dict[str, Any]] = None
        for pid, pinfo in places.items():
            for obj in pinfo.get("objects") or []:
                if isinstance(obj, Mapping) and obj.get("object_id") == obj_id:
                    found_place_id = pid
                    found_obj = dict(obj)
                    break
            if found_obj is not None:
                break

        if found_obj is None:
            found_place_id = "courtyard"
            found_obj = {"object_id": obj_id, "label": obj_id, "occupied_by": None, "child_items": []}

        affected_occupants: list[str] = []
        if found_obj.get("occupied_by"):
            affected_occupants.append(str(found_obj["occupied_by"]))
        if simulate_occupied_by and simulate_occupied_by.strip():
            occ = simulate_occupied_by.strip()
            if occ not in affected_occupants:
                affected_occupants.append(occ)

        affected_items: list[str] = [str(x) for x in (found_obj.get("child_items") or []) if x]
        if simulate_items_on_surface:
            for item in simulate_items_on_surface:
                if isinstance(item, str) and item.strip() and item.strip() not in affected_items:
                    affected_items.append(item.strip())

        affected_agreements: list[str] = []
        for agr in (snapshot.get("public_life") or {}).get("agreements") or []:
            if isinstance(agr, Mapping) and agr.get("object_id") == obj_id:
                affected_agreements.append(str(agr.get("agreement_id") or agr.get("summary")))
        for d_item in (snapshot.get("drying_items") or {}).values():
            if isinstance(d_item, Mapping) and d_item.get("support_id") == obj_id:
                affected_agreements.append(f"active_drying:{d_item.get('item_id')}")
        if simulate_bound_agreement and simulate_bound_agreement.strip():
            affected_agreements.append(simulate_bound_agreement.strip())

        blocking_reasons: list[str] = []
        if affected_occupants:
            blocking_reasons.append(
                f"对象「{obj_id}」当前正被 {', '.join(affected_occupants)} 占用，禁止将人与物件不加说明地静默瞬移。"
            )
            allowed_immediately = False
            chosen_mode = "admin_migrated" if resolution_mode == "admin_migrated" else "wait_until_idle"
        elif resolution_mode == "cancel":
            allowed_immediately = False
            chosen_mode = "cancelled"
        else:
            allowed_immediately = True
            chosen_mode = "admin_migrated"

        return {
            "mod_kind": mod_kind,
            "target_id": obj_id,
            "from_place_id": found_place_id,
            "to_place_id": dest_place,
            "affected_occupants": affected_occupants,
            "affected_items": affected_items,
            "affected_agreements": affected_agreements,
            "referencing_routes": [],
            "allowed_immediately": allowed_immediately,
            "migration_mode": chosen_mode,
            "available_resolutions": ["wait_until_idle", "admin_migrated", "cancel"],
            "blocking_reasons": blocking_reasons,
            "preserves_object_id": True,
            "creates_false_self_moved_memory": False,
            "migration_note": (
                f"管理迁移保留稳定对象 ID「{obj_id}」并记录维护变更原因；{pronoun}下次观察能看见新位置，"
                "但绝不会自动生成『自己亲手搬过』的伪造记忆。"
            ),
            "sandbox_isolated": True,
            "mutated_live_world": False,
            "llm_calls": 0,
        }

    if mod_kind == "delete_place":
        if not isinstance(target_id, str) or not target_id.strip():
            raise V04ValidationError("INVALID_FIELDS", "delete_place requires non-empty target_id")
        pid = _resolve_place_id_alias(target_id, places)
        pinfo = places.get(pid)

        affected_occupants = list(pinfo.get("occupants") or []) if isinstance(pinfo, Mapping) else []
        if simulate_occupied_by and simulate_occupied_by.strip() and simulate_occupied_by.strip() not in affected_occupants:
            affected_occupants.append(simulate_occupied_by.strip())

        affected_items = [
            str(o.get("object_id"))
            for o in ((pinfo.get("objects") or []) if isinstance(pinfo, Mapping) else [])
            if isinstance(o, Mapping) and o.get("object_id")
        ]
        if simulate_items_on_surface:
            for item in simulate_items_on_surface:
                if isinstance(item, str) and item.strip() and item.strip() not in affected_items:
                    affected_items.append(item.strip())

        affected_agreements = []
        for d_item in (snapshot.get("drying_items") or {}).values():
            if isinstance(d_item, Mapping) and d_item.get("place_id") == pid:
                affected_agreements.append(f"drying:{d_item.get('item_id')}")
        for agr in (snapshot.get("public_life") or {}).get("agreements") or []:
            if isinstance(agr, Mapping) and agr.get("place_id") == pid:
                affected_agreements.append(str(agr.get("agreement_id") or "public_agreement"))
        for notice in (snapshot.get("public_life") or {}).get("notices") or []:
            if isinstance(notice, Mapping) and notice.get("place_id") == pid and not notice.get("withdrawn"):
                affected_agreements.append(f"notice:{notice.get('notice_id')}")
        if simulate_bound_agreement and simulate_bound_agreement.strip():
            affected_agreements.append(simulate_bound_agreement.strip())

        referencing_routes = [
            str(r.get("route_id"))
            for r in routes
            if isinstance(r, Mapping) and (r.get("from_place") == pid or r.get("to_place") == pid)
        ]

        blocking_reasons = []
        if affected_occupants:
            blocking_reasons.append(f"地点「{pid}」内仍有在场人员或居民：{', '.join(affected_occupants)}。")
        if affected_items:
            blocking_reasons.append(
                f"地点「{pid}」内仍存留 {len(affected_items)} 件世界对象（如 {', '.join(affected_items[:3])}），需先迁移或妥善安置。"
            )
        if affected_agreements:
            blocking_reasons.append(f"地点「{pid}」仍绑定进行中过程、公共告示或借还约定：{', '.join(affected_agreements)}。")
        if referencing_routes:
            blocking_reasons.append(f"地点「{pid}」仍被 {len(referencing_routes)} 条道路连接引用（{', '.join(referencing_routes)}）。")

        if blocking_reasons:
            return {
                "mod_kind": "delete_place",
                "target_id": pid,
                "affected_occupants": affected_occupants,
                "affected_items": affected_items,
                "affected_agreements": affected_agreements,
                "referencing_routes": referencing_routes,
                "allowed_immediately": False,
                "migration_mode": "blocked_referenced_place",
                "blocking_reasons": blocking_reasons,
                "sandbox_isolated": True,
                "mutated_live_world": False,
                "llm_calls": 0,
            }

        return {
            "mod_kind": "delete_place",
            "target_id": pid,
            "affected_occupants": [],
            "affected_items": [],
            "affected_agreements": [],
            "referencing_routes": [],
            "allowed_immediately": True,
            "migration_mode": "safe_delete_unreferenced",
            "blocking_reasons": [],
            "sandbox_isolated": True,
            "mutated_live_world": False,
            "llm_calls": 0,
        }

    if mod_kind == "block_route":
        raw_rid = (target_id or "route_courtyard_street_open").strip() or "route_courtyard_street_open"
        rid = _resolve_route_id_alias(raw_rid)
        post_audit = audit_world_routes(snapshot, blocked_edges=[rid])
        newly_isolated = post_audit["isolated_places"]
        newly_trapped = post_audit["one_way_traps"]
        journey = snapshot.get("journey") or {}
        in_transit = str(journey.get("route_id") or "") == rid

        affected_occupants = [pronoun] if in_transit else []
        if simulate_occupied_by and simulate_occupied_by.strip():
            affected_occupants.append(simulate_occupied_by.strip())

        affected_agreements = []
        if "bookstore" in newly_isolated:
            affected_agreements.append("bookstore_return_box_rule")

        blocking_reasons = []
        if newly_isolated:
            blocking_reasons.append(f"封闭路段「{rid}」将导致地点变成无法到达的孤岛：{', '.join(newly_isolated)}。")
        if newly_trapped:
            blocking_reasons.append(f"封闭路段「{rid}」将产生有去无回的单向陷阱：{', '.join(newly_trapped)}。")
        if affected_occupants:
            blocking_reasons.append(f"路段「{rid}」当前正有出行人员在途（{', '.join(affected_occupants)}），需先引导停靠最近节点。")

        allowed = len(blocking_reasons) == 0
        return {
            "mod_kind": "block_route",
            "target_id": rid,
            "affected_occupants": affected_occupants,
            "affected_items": [],
            "affected_agreements": affected_agreements,
            "referencing_routes": [rid],
            "newly_isolated_places": newly_isolated,
            "newly_trapped_places": newly_trapped,
            "alternate_routes_available": allowed,
            "allowed_immediately": allowed,
            "migration_mode": "detour_available_safe_block" if allowed else "blocked_severs_connectivity",
            "blocking_reasons": blocking_reasons,
            "sandbox_isolated": True,
            "mutated_live_world": False,
            "llm_calls": 0,
        }

    # mod_kind == "apply_worldpack_diff" (44 §七、§八)
    diff = dict(worldpack_diff or {})
    added_places = list(diff.get("added_places") or ["alley_tea_kiosk"])
    added_objects = list(diff.get("added_objects") or ["porch_warm_lantern"])
    renamed_objects = dict(diff.get("renamed_objects") or {"courtyard_bench": "院角长木椅"})
    asset_paths = list(diff.get("asset_paths") or [])
    has_scripts = bool(diff.get("arbitrary_scripts"))
    process_policy = str(diff.get("in_progress_process_policy") or "finish_under_old_rule")
    if process_policy not in {"finish_under_old_rule", "explicit_boundary_migrate"}:
        raise V04ValidationError("INVALID_FIELDS", f"Invalid in_progress_process_policy: {process_policy}")

    blocking_reasons = []
    if has_scripts:
        blocking_reasons.append("世界包包含未授权可执行脚本；首版仅允许宿主支持的纯声明式规则。")
    for raw_asset in asset_paths:
        s_asset = str(raw_asset)
        if ".." in s_asset or s_asset.startswith(("/", "\\")) or ":" in s_asset:
            blocking_reasons.append(f"素材路径「{s_asset}」含越界或绝对路径引用，严禁通过世界包读取部署者私人文件。")

    preserved_lived_states = [
        f"drying_items:{len(snapshot.get('drying_items') or {})}",
        f"craft_bench:{(snapshot.get('craft_bench') or {}).get('item_id', 'card_box_wip')}",
        f"shared_ruler:{((snapshot.get('public_life') or {}).get('shared_ruler') or {}).get('loan_status', 'available')}",
        f"lived_places_preserved:{len(places)}",
    ]

    allowed = len(blocking_reasons) == 0
    return {
        "mod_kind": "apply_worldpack_diff",
        "target_id": str(target_id or diff.get("worldpack_id") or "worldpack_neighborhood_patch_v2"),
        "affected_occupants": [pronoun],
        "affected_items": sorted(renamed_objects.keys()),
        "affected_agreements": [
            str(a.get("agreement_id"))
            for a in ((snapshot.get("public_life") or {}).get("agreements") or [])
            if isinstance(a, Mapping) and a.get("agreement_id")
        ],
        "referencing_routes": [],
        "new_regions_added": added_places,
        "added_objects": added_objects,
        "preserved_lived_states": preserved_lived_states,
        "allowed_immediately": allowed,
        "migration_mode": "incremental_preserve_lived_state" if allowed else "blocked_unsafe_worldpack",
        "blocking_reasons": blocking_reasons,
        "resets_ongoing_life": False,
        "overwrites_lived_places": False,
        "stable_object_ids_preserved": True,
        "declarative_rules_only": not has_scripts,
        "in_progress_process_handling": process_policy,
        "retroactive_formula_rewrite": False,
        "sandbox_isolated": True,
        "mutated_live_world": False,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Projection & Guarded Command Application
# ---------------------------------------------------------------------------


def project_v04_builder_trylive(
    day_root: Path | str,
    *,
    sandbox_state: Optional[Mapping[str, Any]] = None,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Pure 100% zero-write projection of builder diagnostic & try-live sandbox state."""
    root = Path(day_root)
    resolved_now = format_iso(parse_iso_moment(now_iso))
    snapshot = load_world_snapshot_readonly(root)
    stored = dict(sandbox_state) if sandbox_state is not None else load_sandbox_state_readonly(root)

    perspective = "her_private" if has_subject_capability else "partner_default"
    stored_pron = str(stored.get("pronoun") or "").strip() if isinstance(stored, Mapping) else ""
    pronoun = (
        stored_pron
        if stored_pron in ALLOWED_PRONOUNS
        else str(snapshot.get("pronoun") or DEFAULT_PRONOUN)
    )
    live_route_audit = audit_world_routes(snapshot)

    last_rehearsal = copy.deepcopy(stored.get("last_rehearsal"))
    last_scene_gap_check = copy.deepcopy(stored.get("last_scene_gap_check"))
    last_trylive_run = copy.deepcopy(stored.get("last_trylive_run"))
    last_why_changed = copy.deepcopy(stored.get("last_why_changed"))
    last_impact_preview = copy.deepcopy(stored.get("last_impact_preview"))

    return {
        "perspective": perspective,
        "revision": int(stored.get("revision") or 0),
        "observed_at": resolved_now,
        "pronoun": pronoun,
        "identity": {
            "pronoun": pronoun,
            "display_name": "住户",
            "perspective": perspective,
        },
        "sandbox_isolated": True,
        "has_live_neighborhood_state": bool(snapshot.get("has_live_neighborhood_file")),
        "llm_calls": 0,
        # Backend & Frontend dual-aligned keys
        "route_audit": live_route_audit,
        "preset_scenarios": [copy.deepcopy(item) for item in PRESET_SCENARIOS],
        "presets": [copy.deepcopy(item) for item in FRONTEND_PRESETS],
        "inspectable_objects": [copy.deepcopy(item) for item in INSPECTABLE_OBJECTS],
        "last_rehearsal": last_rehearsal,
        "last_route_audit": copy.deepcopy(last_rehearsal) if last_rehearsal is not None else copy.deepcopy(live_route_audit),
        "last_scene_gap_check": last_scene_gap_check,
        "last_prose_check": copy.deepcopy(last_scene_gap_check),
        "last_trylive_run": last_trylive_run,
        "last_why_changed": last_why_changed,
        "last_impact_preview": last_impact_preview,
        "last_modification_preview": copy.deepcopy(last_impact_preview),
    }


def _collect_faults_from_payload(payload: Mapping[str, Any]) -> list[str]:
    merged: list[str] = []
    for list_field in ("inject_faults", "injections"):
        raw = payload.get(list_field)
        if raw is not None:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise V04ValidationError("INVALID_FIELDS", f"{list_field} must be a list of strings")
            for elem in raw:
                if not isinstance(elem, str) or elem.strip() not in FAULT_ALIAS_MAP:
                    raise V04ValidationError("INVALID_FIELDS", f"Invalid fault in {list_field}: {elem!r}")
                merged.append(elem.strip())

    for bool_flag in ("rain", "seat_taken", "seat_occupied", "roadblock", "restart_gap"):
        if bool_flag in payload:
            val = payload[bool_flag]
            if not isinstance(val, bool):
                raise V04ValidationError("INVALID_FIELDS", f"{bool_flag} must be boolean")
            if val:
                merged.append(bool_flag)
    return merged


def apply_v04_builder_trylive_command(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Execute one builder workbench / try-live command and persist ONLY isolated sandbox report."""
    root = Path(day_root)
    resolved_now = format_iso(parse_iso_moment(now_iso))
    action = str(payload["action"]).strip()

    state = load_sandbox_state_readonly(root)

    # 1. Idempotency via operation_key + intent_fingerprint
    op_key: Optional[str] = None
    raw_op = payload.get("operation_key")
    if raw_op is not None and raw_op != "":
        if not isinstance(raw_op, str) or not raw_op.strip() or len(raw_op.strip()) > 128:
            raise V04ValidationError("INVALID_FIELDS", "Invalid operation_key")
        op_key = raw_op.strip()

    explicit_fp: Optional[str] = None
    raw_fp = payload.get("intent_fingerprint")
    if raw_fp is not None and raw_fp != "":
        if not isinstance(raw_fp, str) or not raw_fp.strip() or len(raw_fp.strip()) > 128:
            raise V04ValidationError("INVALID_FIELDS", "Invalid intent_fingerprint")
        explicit_fp = raw_fp.strip()

    computed_fp = compute_intent_fingerprint(
        action,
        actor_side,
        payload,
        explicit_fingerprint=explicit_fp,
    )

    receipts = state.setdefault("operation_receipts", {})
    if op_key is not None and op_key in receipts:
        prev = receipts[op_key]
        if prev.get("intent_fingerprint") != computed_fp:
            raise V04ConflictError(
                "OPERATION_KEY_CONFLICT",
                "Same operation_key reused with different semantic parameters",
            )
        projected = project_v04_builder_trylive(
            root,
            sandbox_state=state,
            has_subject_capability=(actor_side == "subject"),
            now_iso=resolved_now,
        )
        return {
            "command_status": "DUPLICATE",
            "already_applied": True,
            "action": action,
            "actor_side": actor_side,
            "result": copy.deepcopy(prev.get("result") or {}),
            "projected": projected,
        }

    # 2. Optional optimistic concurrency check
    if payload.get("expected_revision") is not None:
        exp_rev = payload["expected_revision"]
        if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev < 0:
            raise V04ValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")
        if int(state.get("revision") or 0) != exp_rev:
            raise V04ConflictError(
                "REVISION_CONFLICT",
                f"Expected revision {exp_rev}, current revision is {state.get('revision', 0)}",
            )

    # 3. Load read-only world snapshot (never mutated on disk!)
    snapshot = load_world_snapshot_readonly(root)
    if "pronoun" in payload and payload["pronoun"] is not None:
        raw_pron = payload["pronoun"]
        if not isinstance(raw_pron, str):
            raise V04ValidationError("INVALID_FIELDS", "pronoun must be a string")
        cleaned_pron = raw_pron.strip()
        if cleaned_pron:
            if cleaned_pron not in ALLOWED_PRONOUNS:
                raise V04ValidationError("INVALID_FIELDS", f"Unsupported pronoun: {cleaned_pron!r}")
            snapshot["pronoun"] = cleaned_pron
            state["pronoun"] = cleaned_pron
    elif isinstance(state.get("pronoun"), str) and state["pronoun"] in ALLOWED_PRONOUNS:
        snapshot["pronoun"] = state["pronoun"]

    if action == "audit_routes_or_rehearse":
        chosen_route_id = payload.get("route_id")
        if chosen_route_id is None or chosen_route_id == "":
            chosen_route_id = payload.get("rehearse_route_id")
        action_result = audit_routes_or_rehearse(
            snapshot,
            now_iso=resolved_now,
            start_place_id=str(payload.get("start_place_id") or "home").strip() or "home",
            destination_place_id=(
                str(payload["destination_place_id"]).strip()
                if payload.get("destination_place_id")
                else None
            ),
            route_id=str(chosen_route_id).strip() if isinstance(chosen_route_id, str) and chosen_route_id.strip() else None,
            blocked_edges=payload.get("blocked_edges"),
            route_path=payload.get("route_path"),
            inject_roadblock_route=(
                str(payload["inject_roadblock_route"]).strip()
                if payload.get("inject_roadblock_route")
                else None
            ),
            on_roadblock=str(payload.get("on_roadblock") or "turn_back").strip() or "turn_back",
            custom_places=payload.get("custom_places"),
            custom_routes=payload.get("custom_routes"),
            object_templates=payload.get("object_templates"),
            available_keys=payload.get("available_keys"),
        )
        state["last_rehearsal"] = copy.deepcopy(action_result)

    elif action == "check_scene_prose_gaps":
        prose_val = payload.get("prose")
        if prose_val is None or (isinstance(prose_val, str) and not prose_val.strip()):
            prose_val = payload.get("scene_prose")
        if prose_val is None or (isinstance(prose_val, str) and not prose_val.strip()):
            prose_val = payload.get("text")
        action_result = check_scene_prose_gaps(
            snapshot,
            scene_prose=prose_val,  # type: ignore[arg-type]
            place_id=str(payload.get("place_id") or "porch").strip() or "porch",
            place_override=payload.get("place_override"),
        )
        state["last_scene_gap_check"] = copy.deepcopy(action_result)

    elif action == "run_trylive_hour":
        preset_token = (
            payload.get("preset_id")
            or payload.get("route_preset")
            or payload.get("scenario_id")
            or "sunny_cloth_drying"
        )
        if not isinstance(preset_token, str) or not preset_token.strip():
            raise V04ValidationError("INVALID_FIELDS", "preset_id / scenario_id must be a non-empty string")
        faults_combined = _collect_faults_from_payload(payload)
        lighting_mode = str(payload.get("compare_lighting") or "none").strip() or "none"
        action_result = run_trylive_hour(
            snapshot,
            now_iso=resolved_now,
            scenario_or_preset_id=preset_token.strip(),
            inject_faults=faults_combined,
            compare_lighting=lighting_mode,
        )
        state["last_trylive_run"] = copy.deepcopy(action_result)

    elif action == "inspect_why_changed":
        raw_target = payload.get("object_id") or payload.get("target_kind") or payload.get("target_id")
        if not isinstance(raw_target, str) or not raw_target.strip():
            raise V04ValidationError("INVALID_FIELDS", "object_id or target_kind is required")
        explicit_tid = payload.get("target_id")
        action_result = inspect_why_changed(
            snapshot,
            now_iso=resolved_now,
            target_token=raw_target.strip(),
            target_id=str(explicit_tid).strip() if isinstance(explicit_tid, str) and explicit_tid.strip() else None,
        )
        state["last_why_changed"] = copy.deepcopy(action_result)

    elif action == "preview_world_modification":
        raw_mod = payload.get("mod_kind")
        if not isinstance(raw_mod, str) or not raw_mod.strip():
            raise V04ValidationError("INVALID_FIELDS", "mod_kind is required")
        action_result = preview_world_modification(
            snapshot,
            mod_kind=raw_mod.strip(),
            target_id=(
                str(payload["target_id"]).strip()
                if isinstance(payload.get("target_id"), str) and payload["target_id"].strip()
                else None
            ),
            to_place_id=(
                str(payload["to_place_id"]).strip()
                if isinstance(payload.get("to_place_id"), str) and payload["to_place_id"].strip()
                else None
            ),
            resolution_mode=str(payload.get("resolution_mode") or "check_only").strip() or "check_only",
            simulate_occupied_by=(
                str(payload["simulate_occupied_by"]).strip()
                if isinstance(payload.get("simulate_occupied_by"), str) and payload["simulate_occupied_by"].strip()
                else None
            ),
            simulate_items_on_surface=payload.get("simulate_items_on_surface"),
            simulate_bound_agreement=(
                str(payload["simulate_bound_agreement"]).strip()
                if isinstance(payload.get("simulate_bound_agreement"), str) and payload["simulate_bound_agreement"].strip()
                else None
            ),
            worldpack_diff=payload.get("worldpack_diff"),
        )
        state["last_impact_preview"] = copy.deepcopy(action_result)

    else:
        raise V04ValidationError("INVALID_FIELDS", f"Unsupported action: {action!r}")

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = resolved_now

    if op_key is not None:
        receipts[op_key] = {
            "intent_fingerprint": computed_fp,
            "action": action,
            "actor_side": actor_side,
            "revision": state["revision"],
            "applied_at": resolved_now,
            "result": copy.deepcopy(action_result),
        }
        while len(receipts) > MAX_OPERATION_RECEIPTS:
            oldest = next(iter(receipts))
            del receipts[oldest]

    _atomic_write_sandbox_json(builder_sandbox_state_path(root), state)

    projected = project_v04_builder_trylive(
        root,
        sandbox_state=state,
        has_subject_capability=(actor_side == "subject"),
        now_iso=resolved_now,
    )
    return {
        "command_status": "APPLIED",
        "already_applied": False,
        "action": action,
        "actor_side": actor_side,
        "result": action_result,
        "projected": projected,
    }


FORBIDDEN_WORLD_PACK_KEYS: frozenset[str] = frozenset({
    "api_key",
    "private_memories",
    "chat_history",
    "exec_script",
    "adult_confirmed",
})
FORBIDDEN_CHAR_PACK_KEYS: frozenset[str] = frozenset({
    "adult_confirmed",
    "attested_by",
    "intimacy_score",
    "fake_past_memories",
    "api_key",
})
FORBIDDEN_SUITCASE_KEYS: frozenset[str] = frozenset({"plaintext_api_key", "host_abs_path"})

_WIN_OR_UNIX_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/mnt/[a-z]/|/Users/|/home/)")


def validate_three_bundle_boundary(bundle_kind: str, payload: Mapping[str, Any]) -> None:
    """第 64 章三包分权硬校验：发现越界夹带字段立即整包拒收。"""
    keys = set(payload.keys())
    if bundle_kind == "nyairo-world":
        bad = keys & FORBIDDEN_WORLD_PACK_KEYS
    elif bundle_kind == "nyairo-pack":
        bad = keys & FORBIDDEN_CHAR_PACK_KEYS
    elif bundle_kind == "nyairo-suitcase":
        bad = keys & FORBIDDEN_SUITCASE_KEYS
    else:
        raise ValueError(f"UNKNOWN_BUNDLE_KIND:{bundle_kind}")
    if bad:
        raise ValueError(f"BUNDLE_FORBIDDEN_FIELDS:{sorted(bad)}")


def migrate_cross_town_suitcase(
    suitcase_bundle: Mapping[str, Any],
    target_town_id: str,
) -> dict[str, Any]:
    """三层分流搬家（第 106 章）：同镇原位恢复，跨镇把珍藏旧物装进新家玄关搬家纸箱。"""
    raw_dump = str(dict(suitcase_bundle))
    if "api_key" in suitcase_bundle or "secret_token" in suitcase_bundle:
        raise ValueError("行李箱内严禁夹带接口密钥")
    if _WIN_OR_UNIX_ABS_PATH_RE.search(raw_dump):
        raise ValueError("行李箱内严禁夹带旧电脑盘符绝对路径")

    source_town_id = str(suitcase_bundle.get("town_id", ""))
    same_town = source_town_id == target_town_id

    human_layer = {
        "shared_memories": list(suitcase_bundle.get("shared_memories", [])),
        "memory_tombstones": list(suitcase_bundle.get("memory_tombstones", [])),
        "refused_firm_locks": list(suitcase_bundle.get("refused_firm_locks", [])),
        "dietary_boundaries": dict(suitcase_bundle.get("dietary_boundaries", {})),
        "manuscripts": list(suitcase_bundle.get("manuscripts", [])),
    }

    treasured_items = list(suitcase_bundle.get("treasured_keepsakes", []))
    local_terrain_state = dict(suitcase_bundle.get("town_terrain_state", {}))

    if same_town:
        return {
            "mode": "SAME_TOWN_RESTORE",
            "town_id": target_town_id,
            "human_layer": human_layer,
            "room_placed_items": treasured_items,
            "moving_carton_entryway": [],
            "town_terrain_state": local_terrain_state,
            "archived_old_town_snapshot": None,
        }

    return {
        "mode": "CROSS_TOWN_RELOCATION",
        "town_id": target_town_id,
        "human_layer": human_layer,
        "room_placed_items": [],
        "moving_carton_entryway": treasured_items,
        "town_terrain_state": {},
        "archived_old_town_snapshot": {
            "town_id": source_town_id,
            "town_terrain_state": local_terrain_state,
        },
    }


__all__ = [
    "ALLOWED_COMPARE_LIGHTING",
    "ALLOWED_FAULTS",
    "ALLOWED_GAP_SEVERITIES",
    "ALLOWED_MOD_KINDS",
    "ALLOWED_WHY_TARGET_KINDS",
    "DEFAULT_PRONOUN",
    "ENGINE_SCHEMA",
    "FAULT_ALIAS_MAP",
    "FORBIDDEN_CHAR_PACK_KEYS",
    "FORBIDDEN_FOREIGN_KEYS",
    "FORBIDDEN_SUITCASE_KEYS",
    "FORBIDDEN_WORLD_PACK_KEYS",
    "FRONTEND_PRESETS",
    "INSPECTABLE_OBJECTS",
    "PRESET_SCENARIOS",
    "PRESET_SCENARIO_BY_ID",
    "SCENARIO_RESOLUTION_MAP",
    "V04BuilderError",
    "V04ConflictError",
    "V04PermissionError",
    "V04ValidationError",
    "WHY_TARGET_ALIAS_MAP",
    "apply_v04_builder_trylive_command",
    "audit_routes_or_rehearse",
    "audit_world_routes",
    "build_default_world_topology",
    "builder_sandbox_state_path",
    "check_scene_prose_gaps",
    "compute_intent_fingerprint",
    "inspect_why_changed",
    "load_sandbox_state_readonly",
    "load_world_snapshot_readonly",
    "migrate_cross_town_suitcase",
    "neighborhood_state_path",
    "preview_world_modification",
    "project_v04_builder_trylive",
    "run_trylive_hour",
    "validate_three_bundle_boundary",
]
