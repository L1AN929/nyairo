#!/usr/bin/env python3
"""Canonical zero-write path normalization and cross-slice location bridge.

This module provides a single shared authority for resolving a NyAiro instance
root (both upward from ``<day_root>/one-day/world-home`` or
``<day_root>/one-day/life/state`` and downward to canonical World/Body/Life/
Memory/Ledger paths) without ever creating directories, files, locks, or WAL
sidecars on disk.

All helpers are 100% deterministic, zero-network, zero-LLM (``llm_calls == 0``),
and 100% zero-write.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_ECONOMY_FILENAME = "world_property_economy.json"
_AGENCY_DEPTH_FILENAME = "v02_agency_depth_state.json"
_CANONICAL_LEDGER_FILENAME = "canonical_life.sqlite3"
_MANAGED_MARKER_NAMES: tuple[str, ...] = (
    "nyairo-day.instance.json",
    "nyairo-day-managed.json",
)
_MAX_UPWARD_PARENTS = 5


@dataclass(frozen=True)
class InstancePaths:
    input_path: Path
    day_root: Path
    is_managed_instance: bool
    instance_json: Optional[Path]
    world_home: Path
    world_state_json: Path
    body_dir: Path
    body_runtime_json: Path
    body_substrate_json: Path
    body_transitions_jsonl: Path
    economy_state_json: Path
    world_package_dir: Optional[Path]
    life_install_root: Path
    life_state_root: Path
    agency_depth_state_json: Path
    legacy_nested_agency_depth_json: Path
    s03_claims_db: Path
    canonical_ledger_db: Path
    memory_dir: Path
    memory_evidence_db: Path
    memory_controls_db: Path
    memory_authority_db: Optional[Path]


# Canonical cross-slice location mapping:
#   world_place_id (or "place/area") -> (v04_place_id, v05_spot_id, v08_spot_id, is_outdoor)
CANONICAL_LOCATION_BRIDGE: dict[str, dict[str, Any]] = {
    "home": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/bedroom": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "window_side",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/living": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "window_side",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/living_room": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "window_side",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/study": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/kitchen": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "home",
    },
    "home/entryway": {
        "v04_place_id": "home",
        "v05_spot_id": "home",
        "v08_spot_id": "entryway",
        "is_outdoor": False,
        "kind": "home",
    },
    "porch": {
        "v04_place_id": "porch",
        "v05_spot_id": "home",
        "v08_spot_id": "porch_eaves",
        "is_outdoor": False,
        "kind": "porch",
    },
    "home/porch": {
        "v04_place_id": "porch",
        "v05_spot_id": "home",
        "v08_spot_id": "porch_eaves",
        "is_outdoor": False,
        "kind": "porch",
    },
    "yard": {
        "v04_place_id": "yard",
        "v05_spot_id": "home",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "yard",
    },
    "home/yard": {
        "v04_place_id": "yard",
        "v05_spot_id": "home",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "yard",
    },
    "courtyard": {
        "v04_place_id": "yard",
        "v05_spot_id": "home",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "yard",
    },
    "nearby_street": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "street",
    },
    "street_1": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "street",
    },
    "street_2": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "street",
    },
    "street_corner": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "street",
    },
    "convenience_store": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": False,
        "kind": "store",
    },
    "bakery": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": False,
        "kind": "store",
    },
    "bakery_corner": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "open_yard",
        "is_outdoor": False,
        "kind": "store",
    },
    "cafe": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "bakery_corner",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "bookstore": {
        "v04_place_id": "bookstore",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "bookshop": {
        "v04_place_id": "bookstore",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "old_bookshop": {
        "v04_place_id": "bookstore",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "home_goods_store": {
        "v04_place_id": "bookstore",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "stationery_store": {
        "v04_place_id": "bookstore",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "store",
    },
    "school": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "old_bookshop",
        "v08_spot_id": "desk_inner",
        "is_outdoor": False,
        "kind": "school",
    },
    "laundromat": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "coin_laundromat",
        "v08_spot_id": "entryway",
        "is_outdoor": False,
        "kind": "store",
    },
    "coin_laundromat": {
        "v04_place_id": "street_corner",
        "v05_spot_id": "coin_laundromat",
        "v08_spot_id": "entryway",
        "is_outdoor": False,
        "kind": "store",
    },
    "park": {
        "v04_place_id": "riverside",
        "v05_spot_id": "hillside_overlook",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "park",
    },
    "hillside": {
        "v04_place_id": "riverside",
        "v05_spot_id": "hillside_overlook",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "park",
    },
    "hillside_overlook": {
        "v04_place_id": "riverside",
        "v05_spot_id": "hillside_overlook",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "park",
    },
    "overlook": {
        "v04_place_id": "riverside",
        "v05_spot_id": "hillside_overlook",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "park",
    },
    "riverside": {
        "v04_place_id": "riverside",
        "v05_spot_id": "river_pier",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "waterfront",
    },
    "seawall": {
        "v04_place_id": "riverside",
        "v05_spot_id": "river_pier",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "waterfront",
    },
    "river_pier": {
        "v04_place_id": "riverside",
        "v05_spot_id": "river_pier",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "waterfront",
    },
    "pier": {
        "v04_place_id": "riverside",
        "v05_spot_id": "river_pier",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "waterfront",
    },
    "street_3": {
        "v04_place_id": "riverside",
        "v05_spot_id": "river_pier",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "street",
    },
    "blank_grove": {
        "v04_place_id": "riverside",
        "v05_spot_id": "blank_grove",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "grove",
    },
    "grove": {
        "v04_place_id": "riverside",
        "v05_spot_id": "blank_grove",
        "v08_spot_id": "open_yard",
        "is_outdoor": True,
        "kind": "grove",
    },
}


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _is_real_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _read_json_mapping(path: Path) -> Optional[dict[str, Any]]:
    if not _is_regular_file(path):
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def _find_direct_instance_json(directory: Path) -> Optional[Path]:
    for cand in (directory / "instance.json", directory / "phone" / "instance.json"):
        if _is_regular_file(cand):
            return cand
    return None


def _has_managed_marker(directory: Path) -> bool:
    run_dir = directory / "run"
    for name in _MANAGED_MARKER_NAMES:
        if _is_regular_file(run_dir / name):
            return True
    return False


def _has_instance_or_managed_marker(directory: Path) -> bool:
    return _find_direct_instance_json(directory) is not None or _has_managed_marker(directory)


def _resolve_maybe_relative(base_dir: Path, raw: Any) -> Optional[Path]:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    cand = Path(text).expanduser()
    if not cand.is_absolute():
        cand = base_dir / cand
    return cand


def _first_existing_file(candidates: list[Path], default: Path) -> Path:
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _is_regular_file(cand):
            return cand
    return default


def _first_env_value(
    env: Optional[Mapping[str, str]],
    keys: tuple[str, ...],
) -> Optional[str]:
    if not isinstance(env, Mapping):
        return None
    for key in keys:
        val = env.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _normalize_day_root(base_p: Path) -> tuple[Path, Optional[Path], bool]:
    """Resolve enclosing day_root without ever escaping isolated tmp_path roots."""
    ancestors: list[Path] = [base_p]
    cur = base_p
    for _ in range(_MAX_UPWARD_PARENTS):
        parent = cur.parent
        if parent == cur:
            break
        ancestors.append(parent)
        cur = parent

    # Find the nearest ancestor with an instance.json or managed marker.
    matched_idx: Optional[int] = None
    for idx, cand in enumerate(ancestors):
        if _has_instance_or_managed_marker(cand):
            matched_idx = idx
            break

    if matched_idx is None:
        return base_p, None, False

    chosen = ancestors[matched_idx]

    # Iteratively promote nested component roots (e.g. ``one-day/world-home`` ->
    # ``one-day`` -> outer ``<day_root>``) whenever an enclosing parent (or
    # grandparent for ``one-day/world-home``) carries an instance.json or
    # managed marker. Standalone ``world-home`` / ``one-day`` roots without a
    # managed enclosing ancestor remain isolated.
    for _ in range(3):
        parent = chosen.parent
        if parent == chosen:
            break
        if chosen.name == "world-home":
            if _has_instance_or_managed_marker(parent):
                chosen = parent
                continue
            if parent.name == "one-day":
                grandparent = parent.parent
                if grandparent != parent and _has_instance_or_managed_marker(grandparent):
                    chosen = grandparent
                    continue
            break
        if chosen.name == "one-day":
            if _has_instance_or_managed_marker(parent):
                chosen = parent
                continue
            break
        break

    inst_json = _find_direct_instance_json(chosen)
    is_managed = bool(inst_json is not None or _has_managed_marker(chosen))
    return chosen, inst_json, is_managed


def resolve_instance_paths(
    root_or_subpath: Path | str,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> InstancePaths:
    """Resolve canonical paths for a NyAiro instance or isolated test directory.

    Performs zero filesystem writes and zero directory creation.
    """
    input_path = Path(str(root_or_subpath)).expanduser()
    base_p = input_path if input_path.is_absolute() else Path(os.path.abspath(str(input_path)))

    day_root, instance_json, is_managed_instance = _normalize_day_root(base_p)
    instance_doc = _read_json_mapping(instance_json) if instance_json is not None else {}
    if instance_doc is None:
        instance_doc = {}

    nested_instance_path = day_root / "one-day" / "instance.json"
    nested_doc = _read_json_mapping(nested_instance_path) or {}
    nested_env = nested_doc.get("environment")
    nested_env_map: Mapping[str, Any] = nested_env if isinstance(nested_env, Mapping) else {}

    explicit_env = environment if isinstance(environment, Mapping) else None
    effective_env: Mapping[str, str] = explicit_env if explicit_env is not None else os.environ

    # 1. world_home
    world_home: Optional[Path] = None
    for raw_wh in (instance_doc.get("world_home"), nested_doc.get("world_home")):
        resolved_wh = _resolve_maybe_relative(day_root, raw_wh)
        if resolved_wh is not None:
            world_home = resolved_wh
            break
    if world_home is None:
        if _is_real_dir(day_root / "one-day" / "world-home"):
            world_home = day_root / "one-day" / "world-home"
        elif (
            _is_regular_file(day_root / "data" / "world" / "world_state.json")
            or _is_regular_file(day_root / "data" / "body" / "runtime.json")
            or _is_regular_file(day_root / "data" / "world" / "body_substrate.json")
        ):
            world_home = day_root
        elif _is_real_dir(day_root / "world-home"):
            world_home = day_root / "world-home"
        elif _is_real_dir(day_root / "one-day"):
            world_home = day_root / "one-day" / "world-home"
        else:
            world_home = day_root

    # 2. world_state_json
    world_state_default = world_home / "data" / "world" / "world_state.json"
    world_state_json = _first_existing_file(
        [
            world_state_default,
            day_root / "data" / "world" / "world_state.json",
            day_root / "world" / "world_state.json",
            day_root / "world_state.json",
        ],
        world_state_default,
    )

    # 3. body_dir, body_runtime_json, body_substrate_json, body_transitions_jsonl
    body_dir_default = world_home / "data" / "body"
    body_dir = body_dir_default
    for cand_dir in (
        body_dir_default,
        day_root / "data" / "body",
        day_root / "world" / "data" / "body",
    ):
        if _is_real_dir(cand_dir):
            body_dir = cand_dir
            break

    body_runtime_default = body_dir / "runtime.json"
    body_runtime_json = _first_existing_file(
        [
            world_home / "data" / "body" / "runtime.json",
            body_runtime_default,
            day_root / "data" / "body" / "runtime.json",
            day_root / "world" / "data" / "body" / "runtime.json",
        ],
        body_runtime_default,
    )

    body_substrate_default = world_home / "data" / "world" / "body_substrate.json"
    body_substrate_json = _first_existing_file(
        [
            body_substrate_default,
            day_root / "data" / "world" / "body_substrate.json",
            day_root / "world" / "body_substrate.json",
            day_root / "body_substrate.json",
        ],
        body_substrate_default,
    )

    body_transitions_jsonl = body_dir / "transitions.jsonl"

    # 4. economy_state_json
    eco_candidates: list[Path] = []
    inst_eco = _resolve_maybe_relative(day_root, instance_doc.get("economy_state"))
    if inst_eco is not None:
        eco_candidates.append(inst_eco)
    eco_default = inst_eco if inst_eco is not None else (world_home / "state" / _ECONOMY_FILENAME)
    eco_candidates.extend(
        [
            world_home / "state" / _ECONOMY_FILENAME,
            day_root / "one-day" / "world-home" / "state" / _ECONOMY_FILENAME,
            day_root / "state" / _ECONOMY_FILENAME,
            day_root / "world" / "state" / _ECONOMY_FILENAME,
            day_root / "one-day" / "world" / "state" / _ECONOMY_FILENAME,
        ]
    )
    economy_state_json = _first_existing_file(eco_candidates, eco_default)

    # 5. world_package_dir
    world_package_dir: Optional[Path] = None
    pkg_candidates: list[Path] = []
    for raw_pkg in (
        instance_doc.get("world_package"),
        nested_doc.get("world_package"),
        _first_env_value(explicit_env, ("CHIYO_WORLD_PACKAGE",)),
    ):
        resolved_pkg = _resolve_maybe_relative(day_root, raw_pkg)
        if resolved_pkg is not None:
            pkg_candidates.append(resolved_pkg)
    pkg_candidates.extend(
        [
            day_root / "world-package",
            day_root / "world_package",
            day_root / "one-day" / "world-package",
            day_root / "one-day" / "world_package",
            world_home / "world-package",
        ]
    )
    for cand_pkg in pkg_candidates:
        if _is_real_dir(cand_pkg):
            world_package_dir = cand_pkg
            break

    # 6. life_install_root & life_state_root
    recorded_install = _resolve_maybe_relative(day_root, nested_env_map.get("CHIYO_LIFE_INSTALL_ROOT"))
    if recorded_install is not None:
        life_install_root = recorded_install
    elif _is_real_dir(day_root / "one-day" / "life") or _is_real_dir(day_root / "one-day"):
        life_install_root = day_root / "one-day" / "life"
    elif _is_real_dir(day_root / "life"):
        life_install_root = day_root / "life"
    elif is_managed_instance:
        life_install_root = day_root / "one-day" / "life"
    else:
        life_install_root = day_root / "life"

    recorded_state = _resolve_maybe_relative(day_root, nested_env_map.get("NYA02_RESIDENT_STATE"))
    if recorded_state is not None:
        life_state_root = recorded_state
    else:
        life_state_root = life_install_root / "state"

    # 7. agency_depth_state_json & legacy double state/state/ fallback
    agency_depth_state_json = day_root / "state" / _AGENCY_DEPTH_FILENAME
    legacy_nested_agency_depth_json = (
        day_root / "one-day" / "life" / "state" / "state" / _AGENCY_DEPTH_FILENAME
    )
    alt_legacy = life_state_root / "state" / _AGENCY_DEPTH_FILENAME
    if not _is_regular_file(legacy_nested_agency_depth_json) and _is_regular_file(alt_legacy):
        legacy_nested_agency_depth_json = alt_legacy

    # 8. s03_claims_db
    env_s03 = _first_env_value(effective_env, ("CHIYO_S03_CLAIMS_DB", "NYAIRO_S03_CLAIMS_DB"))
    if env_s03 is not None:
        s03_claims_db = Path(env_s03).expanduser()
    else:
        inst_s03 = _resolve_maybe_relative(day_root, instance_doc.get("s03_claims_db"))
        nested_s03 = _resolve_maybe_relative(day_root, nested_doc.get("s03_claims_db"))
        s03_candidates: list[Path] = []
        if inst_s03 is not None:
            s03_candidates.append(inst_s03)
        if nested_s03 is not None:
            s03_candidates.append(nested_s03)
        s03_candidates.extend(
            [
                day_root / "one-day" / "s03" / "claims.sqlite3",
                day_root / "s03" / "claims.sqlite3",
                day_root / "life" / "state" / "s03_claims.sqlite3",
                life_state_root / "s03_claims.sqlite3",
            ]
        )
        if inst_s03 is not None:
            s03_default = inst_s03
        elif _is_real_dir(day_root / "one-day"):
            s03_default = day_root / "one-day" / "s03" / "claims.sqlite3"
        else:
            s03_default = day_root / "s03" / "claims.sqlite3"
        s03_claims_db = _first_existing_file(s03_candidates, s03_default)

    # 9. canonical_ledger_db
    ledger_default = day_root / "v02" / _CANONICAL_LEDGER_FILENAME
    canonical_ledger_db = _first_existing_file(
        [
            ledger_default,
            day_root / _CANONICAL_LEDGER_FILENAME,
            day_root / "reconciler" / _CANONICAL_LEDGER_FILENAME,
            life_state_root / "reconciler" / _CANONICAL_LEDGER_FILENAME,
            life_state_root / _CANONICAL_LEDGER_FILENAME,
        ],
        ledger_default,
    )

    # 10. memory paths
    cfg_evidence = _resolve_maybe_relative(
        day_root,
        _first_env_value(explicit_env, ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB"))
        or nested_env_map.get("NYA02_MEMORY_DB")
        or nested_env_map.get("CHIYO_LIFE_MEMORY_DB"),
    )
    cfg_controls = _resolve_maybe_relative(
        day_root,
        _first_env_value(explicit_env, ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB"))
        or nested_env_map.get("NYA02_MEMORY_CONTROLS_DB")
        or nested_env_map.get("CHIYO_LIFE_MEMORY_CONTROLS_DB"),
    )
    cfg_authority = _resolve_maybe_relative(
        day_root,
        _first_env_value(
            explicit_env, ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB")
        )
        or nested_env_map.get("NYA02_MEMORY_AUTHORITY_DB")
        or nested_env_map.get("CHIYO_LIFE_MEMORY_AUTHORITY_DB"),
    )

    if cfg_evidence is not None:
        memory_dir = cfg_evidence.parent
        memory_evidence_db = cfg_evidence
    else:
        default_mem_dir = life_install_root / "memory"
        if not _is_regular_file(default_mem_dir / "evidence.db") and _is_regular_file(
            day_root / "memory" / "evidence.db"
        ):
            memory_dir = day_root / "memory"
        else:
            memory_dir = default_mem_dir
        memory_evidence_db = memory_dir / "evidence.db"

    memory_controls_db = cfg_controls if cfg_controls is not None else (memory_dir / "controls.db")

    memory_authority_db: Optional[Path] = cfg_authority
    if memory_authority_db is None:
        sibling_auth = day_root.parent / f"{day_root.name}-memory-authority" / "authority.db"
        if _is_regular_file(sibling_auth):
            memory_authority_db = sibling_auth

    return InstancePaths(
        input_path=input_path,
        day_root=day_root,
        is_managed_instance=is_managed_instance,
        instance_json=instance_json,
        world_home=world_home,
        world_state_json=world_state_json,
        body_dir=body_dir,
        body_runtime_json=body_runtime_json,
        body_substrate_json=body_substrate_json,
        body_transitions_jsonl=body_transitions_jsonl,
        economy_state_json=economy_state_json,
        world_package_dir=world_package_dir,
        life_install_root=life_install_root,
        life_state_root=life_state_root,
        agency_depth_state_json=agency_depth_state_json,
        legacy_nested_agency_depth_json=legacy_nested_agency_depth_json,
        s03_claims_db=s03_claims_db,
        canonical_ledger_db=canonical_ledger_db,
        memory_dir=memory_dir,
        memory_evidence_db=memory_evidence_db,
        memory_controls_db=memory_controls_db,
        memory_authority_db=memory_authority_db,
    )


def map_world_place_to_slices(
    world_place_id: str,
    area_id: Optional[str] = None,
) -> dict[str, Any]:
    """Map canonical World location/area to v04, v05, v08 slice ids and outdoor flag."""
    raw_place = str(world_place_id or "").strip().lower()
    raw_area = str(area_id).strip().lower() if isinstance(area_id, str) and area_id.strip() else None

    if "/" in raw_place:
        place_part, area_part = raw_place.split("/", 1)
        place_part = place_part.strip()
        area_part = area_part.strip() or None
        if raw_area is None:
            raw_area = area_part
    else:
        place_part = raw_place

    if not place_part:
        place_part = "home"

    entry: Optional[dict[str, Any]] = None
    if raw_area is not None:
        entry = CANONICAL_LOCATION_BRIDGE.get(f"{place_part}/{raw_area}")
    if entry is None:
        entry = CANONICAL_LOCATION_BRIDGE.get(place_part)

    if entry is not None:
        v08_spot = str(entry["v08_spot_id"])
        if place_part == "home" and raw_area in ("bedroom", "living", "living_room"):
            v08_spot = "window_side"
        elif place_part == "home" and raw_area == "entryway":
            v08_spot = "entryway"
        elif place_part == "home" and raw_area == "porch":
            v08_spot = "porch_eaves"
        elif place_part == "home" and raw_area in ("yard", "courtyard"):
            v08_spot = "open_yard"
        return {
            "world_place_id": place_part,
            "area_id": raw_area,
            "v04_place_id": str(entry["v04_place_id"]),
            "v05_spot_id": str(entry["v05_spot_id"]),
            "v08_spot_id": v08_spot,
            "is_outdoor": bool(entry["is_outdoor"]),
        }

    # Bounded heuristic fallback for custom world-package place ids.
    outdoor_tokens = (
        "street",
        "road",
        "lane",
        "alley",
        "park",
        "hill",
        "river",
        "sea",
        "pier",
        "shore",
        "beach",
        "yard",
        "grove",
        "plaza",
        "square",
        "bridge",
        "overlook",
    )
    indoor_tokens = (
        "store",
        "shop",
        "cafe",
        "book",
        "laund",
        "home",
        "house",
        "room",
        "school",
        "library",
        "clinic",
        "station",
    )
    is_outdoor = any(tok in place_part for tok in outdoor_tokens) and not any(
        tok in place_part for tok in indoor_tokens
    )
    if any(tok in place_part for tok in ("book", "stationery", "home_goods")):
        v04_id, v05_id, v08_id = "bookstore", "old_bookshop", "desk_inner"
    elif any(tok in place_part for tok in ("laund",)):
        v04_id, v05_id, v08_id = "street_corner", "coin_laundromat", "entryway"
    elif any(tok in place_part for tok in ("grove",)):
        v04_id, v05_id, v08_id = "riverside", "blank_grove", "open_yard"
    elif any(tok in place_part for tok in ("park", "hill", "overlook")):
        v04_id, v05_id, v08_id = "riverside", "hillside_overlook", "open_yard"
    elif any(tok in place_part for tok in ("river", "sea", "pier", "shore", "beach", "seawall")):
        v04_id, v05_id, v08_id = "riverside", "river_pier", "open_yard"
    elif any(tok in place_part for tok in ("street", "corner", "bakery", "store", "cafe")):
        v04_id, v05_id, v08_id = "street_corner", "bakery_corner", "open_yard"
    elif "porch" in place_part:
        v04_id, v05_id, v08_id = "porch", "home", "porch_eaves"
    elif "yard" in place_part:
        v04_id, v05_id, v08_id = "yard", "home", "open_yard"
    else:
        v04_id, v05_id, v08_id = "home", "home", "desk_inner"

    return {
        "world_place_id": place_part,
        "area_id": raw_area,
        "v04_place_id": v04_id,
        "v05_spot_id": v05_id,
        "v08_spot_id": v08_id,
        "is_outdoor": bool(is_outdoor),
    }


def _extract_place_and_area(doc: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    loc = doc.get("location")
    if isinstance(loc, Mapping):
        pid = loc.get("place_id") or loc.get("location_id")
        aid = loc.get("area_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None)
    elif isinstance(loc, str) and loc.strip():
        return loc.strip(), None
    cur = doc.get("current_location")
    if isinstance(cur, Mapping):
        pid = cur.get("place_id") or cur.get("location_id")
        aid = cur.get("area_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None)
    elif isinstance(cur, str) and cur.strip():
        return cur.strip(), None
    for key in ("place_id", "location_id", "current_place_id", "current_location_id"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            aid = doc.get("area_id")
            return val.strip(), (str(aid).strip() if isinstance(aid, str) and aid.strip() else None)
    return None, None


def read_canonical_world_location_zero_write(
    root_or_subpath: Path | str,
) -> dict[str, Any]:
    """Read ``<world_home>/data/world/world_state.json`` zero-write and map to slice ids."""
    paths = resolve_instance_paths(root_or_subpath)
    doc = _read_json_mapping(paths.world_state_json)
    if doc is not None:
        place_id, area_id = _extract_place_and_area(doc)
        if place_id:
            mapped = map_world_place_to_slices(place_id, area_id)
            return {
                "found": True,
                "source_path": str(paths.world_state_json),
                "world_id": str(doc.get("world_id") or "").strip() or None,
                "revision": doc.get("revision"),
                **mapped,
            }

    fallback = map_world_place_to_slices("home", None)
    return {
        "found": False,
        "source_path": str(paths.world_state_json),
        "world_id": None,
        "revision": None,
        **fallback,
    }
