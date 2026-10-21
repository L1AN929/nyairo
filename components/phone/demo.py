#!/usr/bin/env python3
"""v0.4 手机 demo：显式一次真实 World + 真实 Supply + 一次真实 genesis。

两条命令（都只做自己声明的事）：

* ``prepare_demo(root, ...)`` —— 在**新根**里创建一次真实实例：真实 Life Supply owner 走它
  自己的命令建 workspace/artifact/私人事项文档，真 worldgen + 真 world home，真经济 genesis
  （10000 JPY 一次性 move-in grant），真世界声明（``consumable_catalog``）。已存在就拒绝，
  绝不覆盖、绝不重置、绝不每天补发。
* ``serve_instance(root, ...)`` —— 起自己的两个子进程（Supply owner、WorldBodyService 且
  显式 ``CHIYO_WORLD_LOCAL_OWNER_PHONE=chiyo``），再绑定 loopback HTTP。退出只停**自己的**
  pid；socket/数据/日志都留在根里。

物理文件从不删除（不做 ``rmtree``/``unlink`` 清理）；临时写盘用 ``.tmp`` + ``os.replace``。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from . import service as phone_service

REPO_ROOT = phone_service.REPO_ROOT
CLI_SCRIPT = REPO_ROOT / "scripts" / "phone.py"

SUBJECT = "chiyo"
DEFAULT_SEED = 929711
DEFAULT_THEME = "coastal_town"
DEFAULT_READING_TITLE = "她自己的阅读材料"
DEFAULT_READING_CONTENT = (
    "第一页：桌上摊着一本簿子，字是她自己写的。\n%%PAGE%%\n"
    "第二页：窗外有风，纸角翻了一下。\n%%PAGE%%\n"
    "第三页：天色渐暗，灯还没开。\n"
)
PRIVATE_ITEMS_TITLE = "她的私人日常事项"
WORK_TITLE = "她自己的日常记录"

SUPPLY_DATA_RELATIVE = ("supply", "data")
ONE_DAY_DIRNAME = "one-day"

CHILD_READY_TIMEOUT = 60.0
CHILD_STOP_TIMEOUT = 15.0

#: The ``--with-world`` demonstration generates real interiors for these places (the ones the
#: coastal-town package actually ships).  Missing names are skipped, never invented.
DEFAULT_WORLD_LOCATIONS = ("home", "cafe")
#: The demo's own World/Body owner tick.  Arrivals and recovery advance only in a tick, so the
#: demo asks its child for a short interval; the *simulated* walk time is never shortened
#: (indoor moves stay the world's real ``AREA_MOVE_SECONDS``).
DEFAULT_WORLD_TICK_SECONDS = 5.0

#: ``--with-room-living``: the one explicit switch a ``serve`` invocation can give.  It flips
#: exactly these two owner flags on *both* sides of the process boundary (the phone facade and
#: the World/Body child it starts) so the physical store and the room light agree.  Prices,
#: opening hours, timezone and the 7200s / instant delivery mode are the owner's own values and
#: are not touched here.
ROOM_LIVING_FEATURES: dict[str, str] = {
    "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
    "CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED": "1",
}


def effective_features(instance: Mapping[str, Any], *, room_living: bool = False
                       ) -> dict[str, str]:
    """The switches one ``serve`` runs with: the instance's recorded ``features`` + override.

    ``serve`` must not persist this: an old root keeps whatever it recorded, and the override
    lives only in this process and in the environment of the child it starts.
    """

    features = {str(k): str(v) for k, v in (instance.get("features") or {}).items()}
    if room_living:
        features.update(ROOM_LIVING_FEATURES)
    return features


class PhoneDemoError(RuntimeError):
    """The demo command refused.  Nothing is faked and nothing is overwritten."""


# ---------------------------------------------------------------------------------------------
# child processes (serve owns only what it started)
# ---------------------------------------------------------------------------------------------


class ChildService:
    """One real child process with its own log file.  Only its own pid is ever stopped."""

    def __init__(self, name: str, command: list[str], log_path: Path,
                 environment: Mapping[str, str]) -> None:
        self.name = name
        self.command = list(command)
        self.log_path = Path(log_path)
        self.environment = {str(k): str(v) for k, v in environment.items()}
        self.process: Optional[subprocess.Popen] = None
        self.log_handle = None
        self.socket_path: Optional[Path] = None

    def start(self) -> "ChildService":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("a", encoding="utf-8")
        self.log_handle.write("\n=== %s start %s ===\n" % (self.name, time.strftime("%FT%TZ", time.gmtime())))
        self.log_handle.write("$ %s\n" % " ".join(self.command))
        self.log_handle.flush()
        self.process = subprocess.Popen(
            self.command,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=self.environment,
            cwd=str(REPO_ROOT),
            start_new_session=True,
        )
        return self

    @property
    def pid(self) -> Optional[int]:
        return None if self.process is None else self.process.pid

    def stop(self, timeout: float = CHILD_STOP_TIMEOUT) -> dict[str, Any]:
        """SIGTERM the pid we started, then bounded wait, then SIGKILL.  Never touches sockets."""

        report: dict[str, Any] = {"name": self.name, "pid": self.pid}
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            deadline = time.monotonic() + float(timeout)
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        report["exit_code"] = None if process is None else process.poll()
        if self.log_handle is not None:
            try:
                self.log_handle.write("=== %s stop exit=%s ===\n"
                                      % (self.name, report["exit_code"]))
                self.log_handle.close()
            except OSError:
                pass
            self.log_handle = None
        return report


def _child_environment(extra: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    environment.setdefault("PYTHONUNBUFFERED", "1")
    environment.update({str(k): str(v) for k, v in extra.items()})
    return environment


def supply_socket_for(root: Path, generation: str) -> Path:
    """One socket path per serve generation.

    A restart must not need to remove (or steal) the previous run's socket: a new generation gets
    a brand-new name under the same data root, and the old socket file stays exactly where it is.
    """

    if not generation or not re.match(r"^[A-Za-z0-9._-]{1,64}$", generation):
        raise PhoneDemoError("SUPPLY_GENERATION_INVALID")
    return root / "run" / ("supply-%s.sock" % generation)


def _supply_child(root: Path, *, writer_instance: str, generation: str) -> ChildService:
    socket_path = supply_socket_for(root, generation)
    data_root = root.joinpath(*SUPPLY_DATA_RELATIVE)
    uid = str(os.geteuid())
    environment = _child_environment({
        "LIFE_SUPPLY_SOCKET": str(socket_path),
        "LIFE_SUPPLY_DATA_ROOT": str(data_root),
        "LIFE_SUPPLY_OPERATOR_UIDS": uid,
        "LIFE_SUPPLY_SERVICE_UIDS": uid,
        # The owner's own switches.  A demo is a single-user local sandbox, so the
        # dedicated-non-root UID requirement is explicitly declared off here; a multi-user
        # deployment must keep it ON (and then run with dedicated UIDs).
        "LIFE_SUPPLY_REQUIRE_NON_ROOT": "OFF",
        "LIFE_SUPPLY_SWITCH_WORKSPACE_PROVISION": "ON",
        "LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION": "ON",
        "LIFE_SUPPLY_WRITER_INSTANCE": writer_instance,
    })
    child = ChildService(
        "supply",
        [sys.executable, str(CLI_SCRIPT), "owner-supply",
         "--data-root", str(data_root), "--socket", str(socket_path),
         "--writer-instance", writer_instance],
        root / "logs" / "supply.log",
        environment,
    )
    child.socket_path = socket_path
    return child


def retire_socket(path: Path, generation: str) -> Optional[Path]:
    """Retire one pre-existing socket by *renaming* it; never delete, never steal a live one.

    This is the narrow retention hook P owns: a previous generation's ``gateway.sock`` /
    ``control.sock`` is moved aside to a unique retained name before the World service binds, so
    neither P nor W needs to unlink anything.  Anything that is not a socket refuses the start.
    """

    if not path.exists() and not path.is_symlink():
        return None
    try:
        info = path.lstat()
    except OSError as exc:
        raise PhoneDemoError("SOCKET_RETIRE_UNREADABLE:%s" % path.name) from exc
    if not stat.S_ISSOCK(info.st_mode):
        # Never rename or delete a non-socket: a wrong path is a hard refusal.
        raise PhoneDemoError("REFUSING_TO_REPLACE_NON_SOCKET:%s" % path.name)
    target = path.with_name("%s.retired-%s" % (path.name, generation))
    counter = 1
    while target.exists() or target.is_symlink():
        target = path.with_name("%s.retired-%s-%d" % (path.name, generation, counter))
        counter += 1
    os.rename(path, target)
    return target


def _world_child(root: Path, instance: Mapping[str, Any], *,
                 tick_seconds: float = DEFAULT_WORLD_TICK_SECONDS,
                 room_living: bool = False) -> ChildService:
    world_home = Path(str(instance["world_home"]))
    package = str(instance.get("world_package") or "")
    # The child reads the *same* effective switches the phone facade reads (instance ``features``
    # plus the serve-time override).  Without that, an old root's recorded
    # ``CHIYO_WORLD_DAILY_RHYTHM_ENABLED=0`` would be honoured by the phone while this hardcoded
    # "0" told the child the opposite -- the physical store could never be enabled consistently.
    features = effective_features(instance, room_living=room_living)
    extra = {
        "CHIYO_WORLD_HOME": str(world_home),
        "HERMES_HOME": str(world_home),
        "CHIYO_WORLD_PACKAGE": package,
        "CHIYO_WORLD_ID": str(instance.get("world_id") or ""),
        "CHIYO_WORLD_RUNTIME": str(REPO_ROOT / "components" / "world"),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": features.get("CHIYO_WORLD_DAILY_RHYTHM_ENABLED", "0"),
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        # The explicit, server-side local-owner (phone) binding.  Nothing a client sends can
        # substitute for this: the World/Body service reads it from its own environment.
        "CHIYO_WORLD_LOCAL_OWNER_PHONE": str(instance.get("subject") or SUBJECT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    room_flag = features.get("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED")
    if room_flag is not None:
        # Only a declared switch is passed explicitly: an undeclared one keeps inheriting the
        # operator's environment exactly as before (old default unchanged).
        extra["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"] = room_flag
    environment = _child_environment(extra)
    command = [sys.executable, str(CLI_SCRIPT), "owner-world",
               "--world-home", str(world_home), "--package", package,
               "--tick-seconds", ("%g" % float(tick_seconds))]
    child = ChildService("world", command, root / "logs" / "world.log", environment)
    child.socket_path = world_home / "run" / "gateway.sock"
    child.tick_seconds = float(tick_seconds)
    return child


# ---------------------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------------------


def _root_is_usable(root: Path) -> None:
    if not root.is_absolute():
        raise PhoneDemoError("ROOT_NOT_ABSOLUTE")
    if root.exists():
        if not root.is_dir():
            raise PhoneDemoError("ROOT_NOT_A_DIRECTORY")
        if any(root.iterdir()):
            raise PhoneDemoError("ROOT_NOT_EMPTY")


def _write_capabilities(world_home: Path, *, package_dir: Optional[Path] = None
                        ) -> dict[str, Any]:
    """Declare the phone store's one consumable, and (``--with-world``) the package's own
    object capabilities.

    An object is declared takeable only when the *world package itself* says so
    (``portable: true`` in ``scene.json`` **and** the ``pick_up`` action); everything else is
    declared fixed.  No label/name is ever pattern-matched into a capability.
    """

    path = world_home / "state" / "world_object_capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PhoneDemoError("CAPABILITIES_UNREADABLE") from exc
        if isinstance(loaded, dict):
            document = loaded
    document.setdefault("schema_version", "world.object.capabilities.v1")
    document.setdefault("objects", {})
    document.setdefault("consumables", {})
    catalog = document.get("consumable_catalog")
    if not isinstance(catalog, dict):
        catalog = {}
    catalog.setdefault("virtual_snack_wafer", {
        "consumable": True, "free_to_consume": False, "consumption_permit": "owned_purchase",
    })
    document["consumable_catalog"] = catalog
    objects = document.get("objects")
    if not isinstance(objects, dict):
        objects = {}
    if package_dir is not None:
        declarations, _facts = _package_capabilities(Path(package_dir))
        for object_id, capability in declarations.items():
            objects.setdefault(object_id, capability)
    document["objects"] = objects
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return document


def _entrance_area(navigation: Mapping[str, Any], place_id: str) -> str:
    nav = navigation.get(place_id) if isinstance(navigation, Mapping) else None
    if isinstance(nav, Mapping):
        entrance = nav.get("entrance")
        if isinstance(entrance, str) and entrance:
            return entrance
    return str(place_id)


def _package_objects(package_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The world package's own object declarations, joined with its own scene placements."""

    try:
        objects_doc = json.loads((Path(package_dir) / "objects.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PhoneDemoError("WORLD_PACKAGE_UNREADABLE") from exc
    placements: dict[str, Any] = {}
    navigation: dict[str, Any] = {}
    scene_path = Path(package_dir) / "scene.json"
    if scene_path.exists():
        try:
            scene_doc = json.loads(scene_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PhoneDemoError("WORLD_PACKAGE_UNREADABLE") from exc
        if isinstance(scene_doc, dict):
            raw = scene_doc.get("placements")
            placements = raw if isinstance(raw, dict) else {}
            raw_nav = scene_doc.get("navigation")
            navigation = raw_nav if isinstance(raw_nav, dict) else {}
    area_of: dict[str, str] = {}
    for _place, nav in navigation.items():
        if not isinstance(nav, Mapping):
            continue
        for area_id, members in (nav.get("furniture_map") or {}).items():
            for object_id in members or []:
                area_of[str(object_id)] = str(area_id)
    rows: list[dict[str, Any]] = []
    for row in objects_doc.get("objects") or []:
        if not isinstance(row, Mapping):
            continue
        object_id = str(row.get("object_id") or "")
        location_id = str(row.get("location_id") or "")
        if not object_id or not location_id:
            continue
        placement = placements.get(object_id)
        placement = placement if isinstance(placement, Mapping) else {}
        rows.append({
            "object_id": object_id,
            "name": str(row.get("name") or object_id),
            "location_id": location_id,
            "area_id": area_of.get(object_id) or _entrance_area(navigation, location_id),
            "portable": placement.get("portable") is True,
            "actions": [str(action) for action in placement.get("actions") or []],
        })
    return rows, navigation


def _package_capabilities(package_dir: Path) -> tuple[dict[str, dict[str, Any]],
                                                      list[dict[str, Any]]]:
    """Capability declarations read off the package's own facts (never off a name)."""

    rows, _navigation = _package_objects(package_dir)
    declarations: dict[str, dict[str, Any]] = {}
    for row in rows:
        handheld = row["portable"] and "pick_up" in row["actions"]
        declarations[row["object_id"]] = (
            {"takeable": True, "portable": True, "placeable": True, "fixed": False}
            if handheld else
            {"takeable": False, "portable": False, "placeable": True, "fixed": True})
    return declarations, rows


def _interior_locations(package_dir: Path) -> list[str]:
    try:
        from components.worldgen.loader import load as load_package

        known = set(load_package(Path(package_dir)).locations)
    except Exception as exc:  # an unreadable package is a refusal, not an empty world
        raise PhoneDemoError("WORLD_PACKAGE_UNREADABLE:%s" % type(exc).__name__) from exc
    locations = [name for name in DEFAULT_WORLD_LOCATIONS if name in known]
    if not locations:
        raise PhoneDemoError("WORLD_INTERIOR_LOCATION_MISSING")
    return locations


def _prepare_world(world_home: Path, package_dir: Path, *, world_id: str) -> dict[str, Any]:
    """``--with-world``: real interiors, real capability declarations, one honest placement pass.

    Order matters and nothing is invented:

    1. ``worldgen.apply_interiors_to_package`` writes the package's own ``scene.json`` /
       ``objects.json`` / ``MANIFEST.json`` (rooms, areas, furniture, portable flags);
    2. capability declarations are read off those facts (``portable: true`` *and* ``pick_up``);
    3. the substrate owner's own writer (``initial_substrate`` + ``place_object`` +
       ``SubstrateStore.save``) places those declared objects in this **brand-new** root, and
       places the package's own rest furniture as the declared rest surface.  An existing
       substrate is never touched.
    """

    package_path = Path(str(package_dir))
    if not package_path.is_dir():
        raise PhoneDemoError("WORLD_PACKAGE_MISSING")
    try:
        from components.worldgen.interior import InteriorError, apply_interiors_to_package
    except Exception as exc:
        raise PhoneDemoError("WORLDGEN_UNAVAILABLE:%s" % type(exc).__name__) from exc
    locations = _interior_locations(package_path)
    try:
        receipt = apply_interiors_to_package(package_path, locations=locations)
    except InteriorError as exc:
        raise PhoneDemoError("WORLD_INTERIOR_FAILED:%s" % str(exc)[:120]) from exc
    except Exception as exc:
        raise PhoneDemoError("WORLD_INTERIOR_FAILED:%s" % type(exc).__name__) from exc

    rows, _navigation = _package_objects(package_path)
    portable = [row for row in rows if row["portable"] and "pick_up" in row["actions"]]
    rest = [row for row in rows if "rest" in row["actions"]]
    if not portable:
        # A world with nothing to pick up says so; it is not padded with a made-up object.
        raise PhoneDemoError("WORLD_HAS_NO_TAKEABLE_OBJECT")
    if not rest:
        raise PhoneDemoError("WORLD_HAS_NO_REST_FURNITURE")

    import world_body_substrate as substrate

    path = substrate.substrate_path(world_home)
    surfaces = sorted(getattr(substrate, "SURFACES", {}) or {})
    if not surfaces:
        raise PhoneDemoError("WORLD_HAS_NO_REST_SURFACE")
    surface_id = surfaces[0]

    store = substrate.SubstrateStore(path)
    try:
        live = store.load()
    except substrate.BodySubstrateError as exc:
        raise PhoneDemoError("SUBSTRATE_STATE_ERROR") from exc
    except OSError as exc:
        raise PhoneDemoError("SUBSTRATE_UNREADABLE") from exc
    if live is None:
        state = substrate.initial_substrate(world_id=world_id)
        state["pose"] = "standing"
        state["orientation"] = "south"
    else:
        # ``one_day_life.prepare`` already created this root's empty substrate.  Only a pristine
        # one (no placement, revision 0, same world) may be populated here; an instance that has
        # any physical history is left exactly as it is.
        if live.get("placements") or int(live.get("revision") or 0) != 0:
            raise PhoneDemoError("SUBSTRATE_NOT_PRISTINE")
        if str(live.get("world_id") or "") != str(world_id):
            raise PhoneDemoError("SUBSTRATE_WORLD_MISMATCH")
        state = live
    base_revision = int(state.get("revision") or 0)
    placed: list[dict[str, Any]] = []
    try:
        for row in portable:
            target = {"kind": "room", "location_id": row["location_id"],
                      "area_id": row["area_id"]}
            state = substrate.place_object(state, row["object_id"], target)
            placed.append({"object_id": row["object_id"], "placement": target})
        bed = rest[0]
        target = {"kind": "surface", "location_id": bed["location_id"],
                  "surface_id": surface_id}
        state = substrate.place_object(state, bed["object_id"], target)
        placed.append({"object_id": bed["object_id"], "placement": target})
        saved = store.save(state, expected_revision=base_revision)
    except substrate.BodySubstrateError as exc:
        raise PhoneDemoError("SUBSTRATE_REJECTED:%s" % str(exc)[:120]) from exc
    except OSError as exc:
        raise PhoneDemoError("SUBSTRATE_UNWRITABLE") from exc

    return {
        "interiors": receipt.get("interiors") or [],
        "scene_locations": receipt.get("scene_locations") or [],
        "scene_placements": receipt.get("scene_placements"),
        "takeable": [{key: row[key] for key in
                      ("object_id", "name", "location_id", "area_id")} for row in portable],
        "rest_furniture": {key: rest[0][key] for key in
                           ("object_id", "name", "location_id", "area_id")},
        "rest_surface": surface_id,
        "substrate": {"path": str(path), "revision": saved.get("revision"),
                      "placements": placed},
        "notes": [
            "室内/场景/manifest 来自 worldgen.apply_interiors_to_package，没有手写房间或家具",
            "可拿物件只按世界包自己的 portable + pick_up 声明，不按名字猜",
            "substrate 只在全新根里由它自己的 writer 初始化一次；既有实例不碰",
            "室内移动是真实 30 秒路线（AREA_MOVE_SECONDS），不缩短模拟时间",
        ],
    }


def prepare_demo(
    root: Path | str,
    *,
    subject: str = SUBJECT,
    seed: int = DEFAULT_SEED,
    theme: str = DEFAULT_THEME,
    reading_title: str = DEFAULT_READING_TITLE,
    reading_content: str = DEFAULT_READING_CONTENT,
    with_world: bool = False,
) -> dict[str, Any]:
    """Create one real phone instance in a brand-new root.  Never overwrites, never resets."""

    root_path = Path(str(root)).expanduser()
    _root_is_usable(root_path)
    if subject != SUBJECT:
        raise PhoneDemoError("SUBJECT_MUST_BE_CHIYO")
    root_path.mkdir(parents=True, exist_ok=False)
    for relative in ("run", "logs", "phone", "supply"):
        (root_path / relative).mkdir(parents=True, exist_ok=True)

    generation = "prepare-%d-%d" % (int(time.time()), os.getpid())
    supply = _supply_child(root_path, writer_instance="phone-demo-prepare:%d" % os.getpid(),
                           generation=generation)
    socket_path = supply.socket_path
    supply.start()
    try:
        if not phone_service.wait_for_socket(socket_path, timeout=CHILD_READY_TIMEOUT):
            raise PhoneDemoError("SUPPLY_SOCKET_TIMEOUT")
        if not phone_service.supply_ready(socket_path):
            raise PhoneDemoError("SUPPLY_NOT_READY")
        prepared = _prepare_owners(root_path, socket_path, subject=subject, seed=seed,
                                   theme=theme, reading_title=reading_title,
                                   reading_content=reading_content, with_world=with_world)
    finally:
        supply.stop()

    # The prepare socket file is *kept* on disk as evidence; serve binds its own generation.
    instance = _instance_payload(root_path, prepared, supply_socket=socket_path,
                                 generation=generation)
    phone_service.write_instance(root_path, instance)
    return {
        "ok": True,
        "root": str(root_path),
        "instance_file": str(phone_service.instance_path(root_path)),
        "subject": instance["subject"],
        "world_home": instance["world_home"],
        "world_id": instance["world_id"],
        "supply_socket": instance["supply_socket"],
        "supply_generation": instance["supply_generation"],
        "private_items_artifact_id": instance["private_items_artifact_id"],
        "workspace_id": instance["workspace_id"],
        "economy_state": instance["economy_state"],
        "genesis": prepared["genesis"],
        "prepared": prepared["summary"],
        "world": prepared.get("world") or {},
    }


def _prepare_owners(root: Path, socket_path: Path, *, subject: str, seed: int, theme: str,
                    reading_title: str, reading_content: str,
                    with_world: bool = False) -> dict[str, Any]:
    """The owners' own commands: Supply artifacts + a real generated World."""

    import importlib

    life_scripts = str(REPO_ROOT / "components" / "life" / "scripts")
    if life_scripts not in sys.path:
        sys.path.insert(0, life_scripts)
    odl = importlib.import_module("one_day_life")

    instance_dir = root / ONE_DAY_DIRNAME

    report = odl.prepare(
        instance=instance_dir,
        supply_socket=str(socket_path),
        governance_socket=str(socket_path),
        subject=subject,
        title=WORK_TITLE,
        seed_content=None,
        world_seed=int(seed),
        world_theme=str(theme),
        reading_title=reading_title,
        reading_content=reading_content,
        private_items_title=PRIVATE_ITEMS_TITLE,
        create_private_items=True,
    )
    data = odl.read_instance(instance_dir)
    world_home = Path(str(data["world_home"]))
    if not (world_home / "data" / "world" / "world_state.json").exists():
        raise PhoneDemoError("WORLD_STATE_MISSING")

    import world_property_economy as economy

    environment = dict(os.environ)
    environment["CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED"] = "1"
    genesis = economy.initialize_genesis(hermes_home=world_home, environment=environment)
    if genesis.get("status") not in ("created", "already_initialized"):
        raise PhoneDemoError("GENESIS_REFUSED:%s" % genesis.get("status"))

    world_package = Path(str(data.get("world_package") or ""))
    world_extra: dict[str, Any] = {}
    if with_world:
        # Interiors first (they rewrite this package's own objects), then declarations, then the
        # one substrate placement pass.  A failure here leaves nothing claimed as prepared.
        world_extra = _prepare_world(world_home, world_package,
                                     world_id=str(data.get("world_id") or ""))
    capabilities = _write_capabilities(
        world_home, package_dir=world_package if with_world else None)
    return {
        "data": data,
        "genesis": genesis,
        "capabilities": capabilities,
        "world": world_extra,
        "economy_state": str(economy.state_path(world_home)),
        "summary": {"steps": report.get("steps"), "world_id": data.get("world_id"),
                    "room_objects": report.get("room_objects"),
                    "with_world": bool(with_world),
                    "world_package": str(world_package) if with_world else None},
    }


def _instance_payload(root: Path, prepared: Mapping[str, Any], *,
                      supply_socket: Path, generation: str) -> dict[str, Any]:
    data = dict(prepared["data"])
    world_home = Path(str(data["world_home"]))
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "name": root.name,
        "mode": "local_owner",
        "subject": str(data["subject_id"]),
        "local_owner_uid": int(os.geteuid()),
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": str(data.get("world_id") or ""),
        "world_package": str(data.get("world_package") or ""),
        "supply_socket": str(supply_socket),
        "supply_generation": str(generation),
        "supply_data_root": str(root.joinpath(*SUPPLY_DATA_RELATIVE)),
        "workspace_id": str(data["workspace_id"]),
        "artifact_grant_id": str(data["artifact_grant_id"]),
        "private_items_artifact_id": str(data.get("private_items_artifact_id") or ""),
        "reading_artifact_id": data.get("reading_artifact_id"),
        "work_artifact_id": str(data.get("work_artifact_id") or ""),
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "economy_state": str(prepared.get("economy_state")
                             or world_home / "state" / "world_property_economy.json"),
        "capabilities": str(world_home / "state" / "world_object_capabilities.json"),
        "features": {
            "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            # The phone process reads the same World switches it starts its owner child with, so
            # C's facade and the service agree (timed travel on; no simulated acceleration).
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
            "CHIYO_WORLD_RUNTIME": str(REPO_ROOT / "components" / "world"),
            "CHIYO_WORLD_PACKAGE": str(data.get("world_package") or ""),
            "CHIYO_WORLD_ID": str(data.get("world_id") or ""),
        },
    }


# ---------------------------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------------------------


def serve_instance(
    root: Path | str,
    *,
    port: int = 0,
    ready_timeout: float = CHILD_READY_TIMEOUT,
    on_ready: Optional[Any] = None,
    tick_seconds: float = DEFAULT_WORLD_TICK_SECONDS,
    room_living: bool = False,
    stop_event: Optional[threading.Event] = None,
    announce: bool = True,
) -> dict[str, Any]:
    """Start the two child owners, bind loopback HTTP, serve until a stop signal.

    ``room_living=True`` (``scripts/phone.py serve --with-room-living``) is the only explicit way
    to turn RC1's room chain on: the same two flags reach the World/Body child's environment and
    this process's ``PhoneService``.  Nothing about it is written back to ``instance.json``.
    """

    from .server import create_server

    root_path = Path(str(root)).expanduser().resolve()
    instance = phone_service.load_instance(root_path)
    if int(instance.get("local_owner_uid", -1)) != int(os.geteuid()):
        raise PhoneDemoError("LOCAL_OWNER_UID_MISMATCH")

    generation = "serve-%d-%d" % (int(time.time()), os.getpid())
    supply = _supply_child(root_path, writer_instance="phone-serve:%d" % os.getpid(),
                           generation=generation)
    world = _world_child(root_path, instance, tick_seconds=float(tick_seconds),
                         room_living=bool(room_living))
    world_home = Path(str(instance["world_home"]))
    children: list[ChildService] = [supply, world]
    server = None
    thread = None
    retired: list[str] = []
    event = stop_event or threading.Event()
    try:
        # Narrow retention: a previous generation's sockets are *renamed* aside (never deleted),
        # so the World service binds a fresh path without P/W unlinking anything.
        for name in ("gateway.sock", "control.sock"):
            moved = retire_socket(world_home / "run" / name, generation)
            if moved is not None:
                retired.append(str(moved))
        supply.start()
        world.start()
        socket_path = Path(str(supply.socket_path))
        gateway = Path(str(instance.get("world_body_socket")
                           or world_home / "run" / "gateway.sock"))
        if not phone_service.wait_for_socket(socket_path, timeout=ready_timeout, probe="supply"):
            raise PhoneDemoError("CHILD_SERVICE_TIMEOUT:supply:%s" % (root_path / "logs" / "supply.log"))
        if not phone_service.wait_for_socket(gateway, timeout=ready_timeout, probe="gateway"):
            raise PhoneDemoError("CHILD_SERVICE_TIMEOUT:world:%s" % (root_path / "logs" / "world.log"))

        # The live binding is part of the instance file (data untouched): a restart changes only
        # which socket this generation answers on.  ``features`` are copied as recorded -- the
        # ``--with-room-living`` override is passed to the service in memory and never written.
        live_instance = dict(instance)
        live_instance["supply_socket"] = str(socket_path)
        live_instance["supply_generation"] = generation
        phone_service.write_instance(root_path, live_instance)

        # Same effective switches as the child above: the facade and the owner it started cannot
        # disagree about the physical store or the room light.
        service = phone_service.PhoneService(
            live_instance,
            feature_overrides=ROOM_LIVING_FEATURES if room_living else None,
        )
        csrf_token = secrets.token_hex(32)
        server = create_server(service, csrf_token=csrf_token, port=port,
                               log_path=root_path / "logs" / "serve.log")
        bound_port = int(server.server_address[1])
        announcement = {
            "ok": True,
            "url": "http://127.0.0.1:%d/" % bound_port,
            "port": bound_port,
            "instance": str(root_path),
            "pid": os.getpid(),
            "supply_generation": generation,
            "supply_socket": str(socket_path),
            "children": {"supply": supply.pid, "world": world.pid},
            "world_tick_seconds": float(tick_seconds),
            # Evidence of what this generation actually runs with (the flags below are the
            # effective ones; nothing here is written back into the instance's features).
            "room_living": bool(room_living),
        }
        if retired:
            announcement["retired_sockets"] = retired
        _write_serve_marker(root_path, announcement)
        if announce:
            sys.stdout.write(json.dumps(announcement, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                                  daemon=True)
        thread.start()
        if on_ready is not None:
            on_ready(announcement)
        _wait_for_stop(event)
        return announcement
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        for child in reversed(children):
            child.stop()


def _wait_for_stop(event: threading.Event) -> None:
    """Block until SIGTERM/SIGINT or the caller's own event is set."""

    previous: dict[int, Any] = {}

    def handle(signum: int, _frame: Any) -> None:
        event.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[signum] = signal.signal(signum, handle)
        except (ValueError, OSError):  # not the main thread
            pass
    try:
        while not event.wait(0.5):
            pass
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


def _write_serve_marker(root: Path, announcement: Mapping[str, Any]) -> None:
    path = root / "run" / "phone.serve.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = dict(announcement)
    payload["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "CHILD_READY_TIMEOUT",
    "ChildService",
    "DEFAULT_WORLD_LOCATIONS",
    "DEFAULT_WORLD_TICK_SECONDS",
    "PhoneDemoError",
    "ROOM_LIVING_FEATURES",
    "SUBJECT",
    "effective_features",
    "prepare_demo",
    "retire_socket",
    "serve_instance",
    "supply_socket_for",
]
