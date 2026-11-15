"""Dynamic endpoint discovery and hot-reconnect helpers for live nyairo_day instances."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SHALLOW_SYSTEM_DIRS = frozenset({"/", "/root", "/tmp", "/var", "/home", "/mnt", "/usr", "/etc"})


def _expand_abs(raw: Any) -> Path | None:
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
    except Exception:
        return None
    if not path.is_absolute():
        return None
    return path


def _exists_on_disk(raw: Any) -> bool:
    path = _expand_abs(raw)
    if path is None:
        return False
    try:
        return os.path.exists(str(path))
    except OSError:
        return False


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(doc) if isinstance(doc, Mapping) else None


def _ancestor_candidates(raw_path: Any, *, max_levels: int = 5) -> list[Path]:
    path = _expand_abs(raw_path)
    if path is None:
        return []
    out: list[Path] = []
    cur = path
    for _ in range(max_levels):
        parent = cur.parent
        if parent == cur:
            break
        norm = str(parent).rstrip("/") or "/"
        if norm in _SHALLOW_SYSTEM_DIRS:
            break
        out.append(parent)
        cur = parent
    return out


def discover_day_instance(
    cfg: Mapping[str, Any] | None,
    *,
    supply_socket: str | None = None,
    world_socket: str | None = None,
    world_home: str | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    """Locate ``<day_root>/instance.json`` from profile config or stale socket paths."""
    settings = cfg if isinstance(cfg, Mapping) else {}
    phone_sec = settings.get("phone") if isinstance(settings.get("phone"), Mapping) else {}

    direct_roots: list[Path] = []
    for raw in (
        phone_sec.get("profile_home"),
        phone_sec.get("day_root"),
        settings.get("day_root"),
        settings.get("profile_home"),
    ):
        cand = _expand_abs(raw)
        if cand is not None and str(cand).rstrip("/") not in _SHALLOW_SYSTEM_DIRS:
            direct_roots.append(cand)

    derived_roots: list[Path] = []
    for raw_sock in (
        supply_socket,
        settings.get("life_supply_socket"),
    ):
        derived_roots.extend(_ancestor_candidates(raw_sock, max_levels=4))
    for raw_world in (
        world_socket,
        settings.get("world_body_socket"),
        world_home,
        settings.get("world_home"),
    ):
        derived_roots.extend(_ancestor_candidates(raw_world, max_levels=5))

    seen: set[str] = set()
    fallback: tuple[Path, dict[str, Any]] | None = None
    for cand in [*direct_roots, *derived_roots]:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        doc = _read_json_mapping(cand / "instance.json")
        if doc is None:
            continue
        schema = str(doc.get("schema_version") or "")
        if schema == "phone.instance.v1" or "supply_socket" in doc:
            return cand, doc
        if schema == "one-day.instance.v1":
            parent = cand.parent
            if str(parent).rstrip("/") not in _SHALLOW_SYSTEM_DIRS:
                parent_doc = _read_json_mapping(parent / "instance.json")
                if parent_doc is not None and (
                    str(parent_doc.get("schema_version") or "") == "phone.instance.v1"
                    or "supply_socket" in parent_doc
                ):
                    return parent, parent_doc
        if fallback is None:
            fallback = (cand, doc)

    if fallback is not None:
        return fallback
    return None, {}


def _superseded_by_instance(raw_path: str, inst_path: str, day_root: Path | None) -> bool:
    """Return True when ``raw_path`` is an older socket inside ``day_root`` replaced in ``instance.json``."""
    if not raw_path or not inst_path or day_root is None:
        return False
    if not _exists_on_disk(inst_path):
        return False
    try:
        rp = Path(raw_path).expanduser().resolve()
        ip = Path(inst_path).expanduser().resolve()
        dr = day_root.expanduser().resolve()
    except Exception:
        return False
    if rp == ip:
        return False
    return dr in rp.parents


def resolve_live_endpoints(
    cfg: Mapping[str, Any] | None,
    *,
    supply_socket: str | None = None,
    supply_subject: str | None = None,
    world_socket: str | None = None,
    artifact_grant: str | None = None,
    prefer_instance: bool = False,
) -> dict[str, Any]:
    """Resolve active sockets and grants, falling back to ``instance.json`` when missing/dead."""
    settings = cfg if isinstance(cfg, Mapping) else {}
    day_root, inst_doc = discover_day_instance(
        settings,
        supply_socket=supply_socket,
        world_socket=world_socket,
    )

    raw_supply = str(
        supply_socket if supply_socket is not None else (settings.get("life_supply_socket") or "")
    ).strip()
    inst_supply = str(inst_doc.get("supply_socket") or "").strip()
    supply_was_stale = False
    if (
        (prefer_instance or _superseded_by_instance(raw_supply, inst_supply, day_root))
        and inst_supply
        and _exists_on_disk(inst_supply)
        and (bool(settings.get("life")) or bool(raw_supply) or prefer_instance)
    ):
        eff_supply: str | None = inst_supply
        supply_was_stale = bool(raw_supply and raw_supply != inst_supply)
    elif raw_supply and _exists_on_disk(raw_supply):
        eff_supply = raw_supply
    elif inst_supply and (bool(settings.get("life")) or bool(raw_supply)):
        eff_supply = inst_supply
        supply_was_stale = bool(raw_supply)
    else:
        eff_supply = raw_supply or None

    raw_subj = str(
        supply_subject if supply_subject is not None else (settings.get("life_supply_subject") or "")
    ).strip()
    inst_subj = str(inst_doc.get("subject") or "").strip()
    eff_subj: str | None = raw_subj or (inst_subj if eff_supply else "") or None

    if settings.get("world_body_socket") is False:
        eff_world: str | None = None
    else:
        raw_world = str(
            world_socket if world_socket is not None else (settings.get("world_body_socket") or "")
        ).strip()
        inst_world = str(inst_doc.get("world_body_socket") or "").strip()
        if (
            (prefer_instance or _superseded_by_instance(raw_world, inst_world, day_root))
            and inst_world
            and _exists_on_disk(inst_world)
        ):
            eff_world = inst_world
        elif raw_world and _exists_on_disk(raw_world):
            eff_world = raw_world
        elif inst_world:
            eff_world = inst_world
        else:
            eff_world = raw_world or None

    raw_wh = str(settings.get("world_home") or "").strip()
    inst_wh = str(inst_doc.get("world_home") or "").strip()
    if raw_wh and _exists_on_disk(raw_wh):
        eff_wh: str | None = raw_wh
    else:
        eff_wh = inst_wh or raw_wh or None

    raw_grant = str(
        artifact_grant
        if artifact_grant is not None
        else (settings.get("life_supply_artifact_grant") or "")
    ).strip()
    inst_grant = str(inst_doc.get("artifact_grant_id") or "").strip()
    if not raw_grant:
        eff_grant: str | None = inst_grant or None
    elif supply_was_stale and inst_grant:
        eff_grant = inst_grant
    else:
        eff_grant = raw_grant

    fallback_grant: str | None = None
    if inst_grant and inst_grant != eff_grant:
        fallback_grant = inst_grant
    elif raw_grant and raw_grant != eff_grant:
        fallback_grant = raw_grant

    return {
        "day_root": day_root,
        "instance_doc": inst_doc,
        "supply_socket": eff_supply,
        "supply_subject": eff_subj,
        "world_socket": eff_world,
        "world_home": eff_wh,
        "artifact_grant": eff_grant,
        "fallback_artifact_grant": fallback_grant,
    }


def resolve_world_gate_for_day(
    day_root: Path | None,
    cfg: Mapping[str, Any] | None = None,
    *,
    world_socket: str | None = None,
) -> str:
    """Determine ``WORLD_BODY_INTENT_MUTATION_GATE`` from explicit config or ``nyairo-day`` state."""
    settings = cfg if isinstance(cfg, Mapping) else {}
    if settings.get("world_actions") is False:
        return "off"
    cfg_gate = str(settings.get("world_gate") or "").strip().lower()
    if cfg_gate in ("off", "canonical", "shadow"):
        return cfg_gate
    if settings.get("world_actions") is True:
        return "canonical"

    if day_root is not None:
        for rel in ("run/nyairo-day.json", "run/nyairo-day.instance.json"):
            doc = _read_json_mapping(day_root / rel)
            if doc is None:
                continue
            if doc.get("world_actions") is False:
                return "off"
            gate = str(doc.get("world_gate") or "").strip().lower()
            if gate in ("off", "canonical", "shadow"):
                return gate
            if doc.get("world_actions") is True:
                return "canonical"

    if world_socket and _exists_on_disk(world_socket):
        return "canonical"
    return "off"


def load_profile_settings(
    environment: Mapping[str, str] | None = None,
    *,
    hermes_home: str | Path | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    """Read ``<profile>/chiyo/config.json`` without mutating process state."""
    env = os.environ if environment is None else environment
    raw_home = hermes_home or env.get("HERMES_HOME")
    profile = _expand_abs(raw_home)
    if profile is None:
        try:
            from hermes_constants import get_hermes_home
            profile = _expand_abs(get_hermes_home())
        except Exception:
            profile = _expand_abs(Path.home() / ".hermes")
    if profile is None:
        return None, {}
    doc = _read_json_mapping(profile / "chiyo" / "config.json")
    return profile, (doc or {})


def is_world_move_explicitly_disabled(cfg: Mapping[str, Any] | None) -> bool:
    if not isinstance(cfg, Mapping):
        return False
    wm = cfg.get("world_move")
    if wm is False:
        return True
    if isinstance(wm, Mapping) and wm.get("enabled") is False:
        return True
    return False


def hydrate_world_move_env(
    base_env: Mapping[str, str] | None = None,
    *,
    hermes_home: str | Path | None = None,
) -> tuple[dict[str, str], bool]:
    """Hydrate world feature and gate environment from a connected ``nyairo_day`` instance.

    Returns ``(env_dict, explicitly_disabled)``. Never overwrites keys already present in
    ``base_env`` (except refreshing a non-existent ``WORLD_BODY_SOCKET`` when discovered).
    """
    source_env = os.environ if base_env is None else base_env
    env: dict[str, str] = {str(k): str(v) for k, v in source_env.items() if v is not None}
    _profile, settings = load_profile_settings(env, hermes_home=hermes_home)
    if is_world_move_explicitly_disabled(settings):
        return env, True

    # Isolated unit tests explicitly set CHIYO_WORLD_HOME; never cross-pollinate them.
    if source_env.get("CHIYO_WORLD_HOME") is not None:
        return env, False

    ep = resolve_live_endpoints(settings, world_socket=env.get("WORLD_BODY_SOCKET"))
    inst_doc = ep.get("instance_doc") or {}
    day_root = ep.get("day_root")
    if not inst_doc:
        return env, False

    features = inst_doc.get("features")
    if isinstance(features, Mapping):
        for fk, fv in features.items():
            if fv is None or str(fv) == "" or fk in env:
                continue
            if isinstance(fv, bool):
                env[str(fk)] = "1" if fv else "0"
            else:
                env[str(fk)] = str(fv)

    if ep.get("world_home") and "CHIYO_WORLD_HOME" not in env:
        env["CHIYO_WORLD_HOME"] = str(ep["world_home"])

    eff_world_sock = ep.get("world_socket")
    if eff_world_sock and (
        not env.get("WORLD_BODY_SOCKET") or not _exists_on_disk(env.get("WORLD_BODY_SOCKET"))
    ):
        env["WORLD_BODY_SOCKET"] = str(eff_world_sock)

    gate = resolve_world_gate_for_day(
        day_root,
        settings,
        world_socket=env.get("WORLD_BODY_SOCKET"),
    )
    if "WORLD_BODY_INTENT_MUTATION_GATE" not in source_env and gate in ("canonical", "shadow"):
        env["WORLD_BODY_INTENT_MUTATION_GATE"] = gate
        if "WORLD_BODY_INTEGRATION_MODE" not in source_env:
            env["WORLD_BODY_INTEGRATION_MODE"] = "canonical"

    if env.get("CHIYO_WORLD_HOME") and "WORLD_ACTION_LIFECYCLE_PATH" not in env:
        wh_path = _expand_abs(env["CHIYO_WORLD_HOME"])
        if wh_path is not None and (wh_path / "run").is_dir():
            env["WORLD_ACTION_LIFECYCLE_PATH"] = str(wh_path / "run" / "action_lifecycle.jsonl")

    return env, False
