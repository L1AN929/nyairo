#!/usr/bin/env python3
"""W05: one day of autonomous life, as one operator-facing entry.

Verbs
-----
    prepare    create an isolated instance through the owners' own commands
    preflight  read-only readiness check; separates configured / reachable / verified
    run        foreground run with a bounded duration (default 86400s)
    status     read-only view of a running, stopped or paused instance
    pause      persist the *life-side* pause (stops new decisions)
    resume     clear the life-side pause
    timeline   read-only event timeline, from the durable records
    read       read one real page of the reading material and leave a bookmark
    note       write a private memo, or save a reference without fetching it
    room       observe the room through the World owner, and link one item to an object
    invite     the finite co-presence protocol: invite / accept / defer / decline / end
    report     the 24h report, from the durable records, with an explicit verdict basis

D (life-v1) notes
-----------------
* The work artifact is bound *explicitly* (``CHIYO_LIFE_WORK_ARTIFACT_ID``) so the private
  items document and the reading material can never be mistaken for the work.
* ``read`` obtains the page through the existing ``LifeArtifactPort`` read ops and records
  what it actually obtained; an acquisition claim alone is never treated as "read".
* ``room`` reads the World owner's own projection; this entry never invents an object.

What this is not
----------------
* Not a scheduler and not a second platform: it drives the existing
  ``autonomous_life_resident`` loop, the existing runner, the existing cycle, the existing
  World journey engine and the existing Supply artifact owner.  It adds no owner, no state
  store of its own and no background thread.
* Not a second writer: it never writes ``world_state.json``, ``world_journey.json``,
  ``world_travel_delivery.json``, ``world_control.json`` or any Supply row.  ``prepare`` goes
  through the owners' own commands over their own sockets, and everything else is read-only
  except the Life-side pause bit and the Life state root the resident already owns.
* Not a 24h launcher: the duration is a parameter with a default, and nothing here starts a
  long process by itself.  A real 24h run needs the operator's own approval of model, budget
  and host (see ``docs/engineering/v03-one-day-runbook.md``).

Two switches, never confused
----------------------------
``pause``/``resume`` here write ``<life state root>/life_pause.json`` only.  The World's own
pause (``<world home>/data/world/world_control.json``, W04) is a different owner's bit and is
never touched by this entry; use the World runner for that one.
"""
from __future__ import annotations

import argparse
import json
import os
import socket as _socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
_COMPONENTS = HERE.parents[1]
_REPO = HERE.parents[2]
for _path in (str(HERE), str(_COMPONENTS), str(_COMPONENTS / "world" / "scripts"),
              str(_COMPONENTS / "world" / "service" / "bridge"),
              str(_COMPONENTS / "world" / "service"), str(_REPO / "chiyo_bundle"),
              str(_REPO)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import autonomous_life_control as control
import autonomous_life_resident as resident_module
import life_pause

INSTANCE_FILENAME = "instance.json"
MODEL_ENV_FILENAME = "model_env"
SCHEMA_VERSION = "life.one_day.v1"
KIND = "one_day_life_instance"

WORLD_HOME_DIRNAME = "world-home"
WORLD_PACKAGE_DIRNAME = "world-package"
LIFE_DIRNAME = "life"
LIFE_STATE_DIRNAME = "state"

DEFAULT_DURATION_SECONDS = 24 * 3600
DEFAULT_POLL_SECONDS = 1800
DEFAULT_CHECK_INTERVAL_MINUTES = 30
DEFAULT_MAX_ROUNDS_PER_DAY = 48
DEFAULT_MAX_COGNITION_CALLS_PER_DAY = 96
DEFAULT_MAX_ACTIONS_PER_DAY = 24

#: W05R: the *total model-request* ceilings the one-day entry always writes into an
#: instance.  These count requests, not rounds: decision, content and every retry are
#: charged against both, which is what a hard cost ceiling has to mean.  A window is one
#: run identity and survives a restart; only an explicit ``--new-run-window`` opens a new one.
DEFAULT_REQUEST_UTC_DAY_MAX = 96
DEFAULT_REQUEST_RUN_WINDOW_MAX = 96
REQUEST_UTC_DAY_MAX_ENV = "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"
REQUEST_RUN_WINDOW_MAX_ENV = "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"
RUN_ID_ENV = "CHIYO_AUTONOMOUS_RUN_ID"
NEW_RUN_WINDOW_ENV = "CHIYO_AUTONOMOUS_NEW_RUN_WINDOW"
#: The World's own gateway transport.  The instance owns these names, so a stray shell
#: export can never point a run at somebody else's World.
WORLD_BODY_SOCKET_ENV = "WORLD_BODY_SOCKET"
WORLD_BODY_MODE_ENV = "WORLD_BODY_INTEGRATION_MODE"
WORLD_BRIDGE_DIR_ENV = "NYAIRO_WORLD_BRIDGE_DIR"

#: D (life-v1): the explicit bindings.  Each artifact this instance owns is *named* here, so
#: the work, the private items document and the reading material can never be confused by
#: recency -- ``latest_resumable`` is not used anywhere in a prepared instance.
WORK_ARTIFACT_ENV = "CHIYO_LIFE_WORK_ARTIFACT_ID"
READING_ARTIFACT_ENV = "CHIYO_LIFE_READING_ARTIFACT_ID"
PRIVATE_ITEMS_ARTIFACT_ENV = "CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID"
#: The Memory boundary.  Without it C's continuity reader is not wired, and the cycle records
#: PAST_SOURCE_NOT_WIRED instead of "no history".
MEMORY_DB_ENV = "NYA02_MEMORY_DB"
MEMORY_CONTROLS_DB_ENV = "NYA02_MEMORY_CONTROLS_DB"
MEMORY_SUBJECT_ENV = "NYA02_MEMORY_SUBJECT"
#: The names the instance owns.  Anything else in the process environment is left alone, and
#: these are removed from it before the instance's own values are applied, so a stray shell
#: export can never point a run at another world, subject or state root.
OWNED_ENV_NAMES = (
    "CHIYO_WORLD_HOME", "CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_ID", "HERMES_HOME",
    "CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_STATE", "NYA02_LIFE_STATE",
    "LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "CHIYO_LIFE_SUPPLY_SOCKET",
    "NYA02_SUPPLY_SOCKET", "NYA02_SUBJECT", "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT",
    "CHIYO_LIFE_SUPPLY_SUBJECT", "NYA02_ARTIFACT_GRANT", "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT",
    "LIFE_SUPPLY_ARTIFACT_GRANT", "CHIYO_LIFE_SUPPLY_WORKSPACE_ID",
    "NYAIRO_WORLD_BRIDGE_DIR", "WORLD_BODY_SOCKET", "XINXI_DIR",
    WORK_ARTIFACT_ENV, READING_ARTIFACT_ENV, PRIVATE_ITEMS_ARTIFACT_ENV,
    MEMORY_DB_ENV, MEMORY_CONTROLS_DB_ENV, MEMORY_SUBJECT_ENV,
)

VERDICT_RUNNING = "RUNNING"
VERDICT_PASS = "PASS"
VERDICT_INTERRUPTED = "INTERRUPTED"
VERDICT_NOT_PASS = "NOT_PASS"
VERDICT_NO_EVIDENCE = "INSUFFICIENT_EVIDENCE"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_LEASE_HELD = 4


class OneDayError(RuntimeError):
    """A refusal with a reason code, never a partial instance."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------------------------
# instance facts
# ---------------------------------------------------------------------------------


def instance_path(root: Path | str) -> Path:
    return Path(root) / INSTANCE_FILENAME


def read_instance(root: Path | str) -> dict[str, Any]:
    path = instance_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OneDayError("NO_INSTANCE: %s does not exist" % path)
    except (OSError, ValueError) as exc:
        raise OneDayError("INSTANCE_UNREADABLE: %s" % exc)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise OneDayError("INSTANCE_INVALID: %s is not a %s file" % (path, SCHEMA_VERSION))
    return data


def life_state_root(root: Path | str) -> Path:
    return Path(root) / LIFE_DIRNAME / LIFE_STATE_DIRNAME


def load_model_env(root: Path | str) -> dict[str, str]:
    """Read ``<instance>/model_env`` (0600, ``NYA02_MODEL_*``), never printing it.

    A key handed over as ``env VAR=... python3`` is world-readable in ``ps``; a 0600 file the
    service identity owns is not.  An already-set variable still wins, so an operator who
    exported one deliberately is not overridden.
    """

    out: dict[str, str] = {}
    path = Path(root) / MODEL_ENV_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name.startswith("NYA02_MODEL_") and value:
            out.setdefault(name, value)
    return out


def instance_environment(root: Path | str, *, extra: Optional[Mapping[str, str]] = None
                         ) -> dict[str, str]:
    data = read_instance(root)
    env = dict(os.environ)
    for name in OWNED_ENV_NAMES:
        env.pop(name, None)
    env.update({str(k): str(v) for k, v in (data.get("environment") or {}).items()})
    for name, value in load_model_env(root).items():
        if not env.get(name):
            env[name] = value
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


# ---------------------------------------------------------------------------------
# owner transports (thin; the owners are elsewhere)
# ---------------------------------------------------------------------------------


def supply_call(socket_path: Path | str, request: Mapping[str, Any], *,
                timeout: float = 15.0) -> dict[str, Any]:
    """One JSON line over the Life Supply owner's own Unix socket.  Nothing else."""

    payload = json.dumps(dict(request), ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(payload + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise OneDayError("SUPPLY_NO_ANSWER: %s closed the connection" % socket_path)
    try:
        answer = json.loads(line.decode("utf-8"))
    except ValueError as exc:
        raise OneDayError("SUPPLY_MALFORMED_ANSWER: %s" % exc)
    if not isinstance(answer, dict):
        raise OneDayError("SUPPLY_MALFORMED_ANSWER: not an object")
    return answer


def _issue_grant(governance_socket: Path | str, *, subject: str, capability: str, scope: str,
                 target: str, authority_scope: str, use_mode: str,
                 operation_id: str) -> dict[str, Any]:
    answer = supply_call(governance_socket, {"op": "issue_grant", "grant": {
        "subject": subject, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if str(answer.get("status")) != "ALLOW" or not answer.get("grant_id"):
        raise OneDayError("GRANT_REFUSED: %s %s" % (capability, answer.get("status")))
    return answer

# ---------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------


def prepare(*, instance: Path | str, supply_socket: Path | str, governance_socket: Path | str,
            subject: str, title: str, world_package: Optional[Path | str] = None,
            seed_content: Optional[str] = None, world_seed: int = 929711,
            world_theme: str = "coastal_town",
            request_utc_day_max: int = DEFAULT_REQUEST_UTC_DAY_MAX,
            request_run_window_max: int = DEFAULT_REQUEST_RUN_WINDOW_MAX,
            run_id: Optional[str] = None,
            world_body_socket: Optional[Path | str] = None,
            world_bridge_dir: Optional[Path | str] = None,
            reading_title: str = "",
            reading_content: Optional[str] = None,
            private_items_title: str = "私人日常事项",
            create_private_items: bool = False,
            memory_db: Optional[Path | str] = None,
            memory_controls_db: Optional[Path | str] = None,
            memory_subject: Optional[str] = None) -> dict[str, Any]:
    """Create an isolated instance **through the owners' own commands**.

    The order is the owners' own order: issue a workspace grant, provision the workspace, issue
    an artifact grant, create the artifact, commit the operator's seed version.  Every step is a
    real owner operation over a real socket; a refusal stops the whole thing with the owner's own
    status code and leaves no ``instance.json`` behind.

    The seed text is the operator's, recorded as such: nothing here claims she wrote it.
    """

    root = Path(instance)
    if instance_path(root).exists():
        raise OneDayError("INSTANCE_EXISTS: %s already exists; refusing to touch it"
                          % instance_path(root))
    if not str(supply_socket).startswith("/") or not str(governance_socket).startswith("/"):
        raise OneDayError("SOCKET_PATH_NOT_ABSOLUTE")
    if not subject.strip() or not title.strip():
        raise OneDayError("SUBJECT_AND_TITLE_REQUIRED")

    steps: list[dict[str, Any]] = []

    workspace_grant = _issue_grant(
        governance_socket, subject=subject, capability="PROVISION_PERSONAL_WORKSPACE",
        scope="workspace:personal", target="workspace:%s" % subject,
        authority_scope="life-supply.workspace.v1", use_mode="ONE_SHOT",
        operation_id="issue:workspace:%s" % subject)
    steps.append({"step": "issue_workspace_grant", "grant_id": workspace_grant["grant_id"]})

    provision = supply_call(supply_socket, {
        "op": "provision_workspace", "grant_id": workspace_grant["grant_id"],
        # W05R: the operation key is scoped to the subject.  The owner treats an operation
        # key as idempotent globally, so a fixed key makes a *second* instance on the same
        # owner an unavoidable CONFLICT ("operation key payload mismatch") -- the one-day
        # entry could only ever prepare one instance per Supply owner.
        "operation_key": "one-day:workspace:%s" % subject,
        "workspace": {"subject_id": subject}})
    if str(provision.get("status")) != "OK":
        raise OneDayError("WORKSPACE_PROVISION_REFUSED: %s %s"
                          % (provision.get("status"), provision.get("detail")))
    workspace_id = (provision.get("workspace") or {}).get("workspace_id")
    if not workspace_id:
        raise OneDayError("WORKSPACE_PROVISION_REFUSED: no workspace_id in the answer")
    steps.append({"step": "provision_workspace", "workspace_id": workspace_id})

    artifact_grant = _issue_grant(
        governance_socket, subject=subject, capability="COMMIT_MANAGED_ARTIFACT",
        scope="artifact:personal", target="workspace:%s" % workspace_id,
        authority_scope="life-supply.artifact.v1", use_mode="REUSABLE",
        operation_id="issue:artifact:%s" % subject)
    steps.append({"step": "issue_artifact_grant", "grant_id": artifact_grant["grant_id"]})

    text = seed_content if seed_content is not None else (
        "# %s\n\n（operator 提供的初始种子，不是她的创作。）\n" % title)
    # ``create_artifact`` writes the first version itself, so the operator's seed text is the
    # version 1 the owner stores -- there is no separate "seed" write, and no second commit to
    # reconcile.  The text is recorded as the operator's, never as her writing.
    created = supply_call(supply_socket, {
        "op": "create_artifact", "grant_id": artifact_grant["grant_id"],
        "operation_key": "one-day:artifact:create:%s" % subject,
        "artifact": {"subject_id": subject, "workspace_id": workspace_id, "title": title,
                     "content": text}})
    if str(created.get("status")) != "OK" or not created.get("artifact_id"):
        raise OneDayError("ARTIFACT_CREATE_REFUSED: %s %s"
                          % (created.get("status"), created.get("detail")))
    artifact_id = created["artifact_id"]
    steps.append({"step": "create_artifact", "artifact_id": artifact_id,
                  "version_id": created.get("version_id")})

    # D (life-v1): the reading material is a *second* real artifact with its own explicit id.
    # Its versions are what a real page read is taken from; nothing here claims she read it.
    reading_artifact_id: Optional[str] = None
    if reading_content:
        reading = supply_call(supply_socket, {
            "op": "create_artifact", "grant_id": artifact_grant["grant_id"],
            "operation_key": "one-day:reading:create:%s" % subject,
            "artifact": {"subject_id": subject, "workspace_id": workspace_id,
                         "title": reading_title or "阅读材料", "content": str(reading_content)}})
        if str(reading.get("status")) != "OK" or not reading.get("artifact_id"):
            raise OneDayError("READING_MATERIAL_CREATE_REFUSED: %s %s"
                              % (reading.get("status"), reading.get("detail")))
        reading_artifact_id = str(reading["artifact_id"])
        steps.append({"step": "create_reading_material", "artifact_id": reading_artifact_id,
                      "version_id": reading.get("version_id"), "provided_by": "operator"})

    # D (life-v1): the typed private-items document (B's v1 prototype protocol), bound to its
    # own explicit artifact id.  A refusal here is recorded and reported, never hidden: the
    # instance is still usable, and ``private_items_artifact_id`` stays null.
    private_items_artifact_id: Optional[str] = None
    if create_private_items:
        try:
            import life_artifact_port as _lap
            import private_daily_items as _pdi

            _port = _lap.LifeArtifactPort(str(supply_socket), subject)
            private_items_artifact_id, _outcome = _pdi.create_document_artifact(
                _port, workspace_id=workspace_id, title=private_items_title,
                grant_id=artifact_grant["grant_id"])
            steps.append({"step": "create_private_items",
                          "artifact_id": private_items_artifact_id})
        except Exception as exc:
            steps.append({"step": "create_private_items", "refused":
                          "%s: %s" % (type(exc).__name__, exc)})

    world_home = root / WORLD_HOME_DIRNAME
    package_dir = Path(world_package) if world_package else (root / WORLD_PACKAGE_DIRNAME)
    world_id, package_note = _prepare_world(world_home, package_dir,
                                           seed=world_seed, theme=world_theme,
                                           provided=world_package is not None)
    steps.append({"step": "prepare_world", "world_id": world_id, "package": package_note})

    # D (life-v1): one real room observation, taken from the World owner's own projection over
    # this instance's world home (the same ``observe_world`` the World service uses).  Only the
    # objects that are really there are recorded, with the instant the read happened.
    room = _observe_room(world_home, package_dir)
    steps.append({"step": "observe_room", "status": room["status"],
                  "objects": len(room["objects"]), "reason": room.get("reason_code")})

    environment = {
        "CHIYO_WORLD_HOME": str(world_home),
        "HERMES_HOME": str(world_home),
        "CHIYO_WORLD_PACKAGE": str(package_dir),
        "CHIYO_WORLD_ID": world_id,
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
        "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
        "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        "CHIYO_LIFE_INSTALL_ROOT": str(root / LIFE_DIRNAME),
        "NYA02_RESIDENT_STATE": str(life_state_root(root)),
        "LIFE_SUPPLY_SOCKET": str(supply_socket),
        "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET": str(supply_socket),
        "NYA02_SUBJECT": subject,
        "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT": subject,
        "NYA02_ARTIFACT_GRANT": artifact_grant["grant_id"],
        "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT": artifact_grant["grant_id"],
        "CHIYO_LIFE_SUPPLY_WORKSPACE_ID": workspace_id,
        "CHIYO_AUTONOMOUS_LIFE_ENABLED": "1",
        "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES": str(DEFAULT_CHECK_INTERVAL_MINUTES),
        "CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY": str(DEFAULT_MAX_ROUNDS_PER_DAY),
        "CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY":
            str(DEFAULT_MAX_COGNITION_CALLS_PER_DAY),
        "CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY": str(DEFAULT_MAX_ACTIONS_PER_DAY),
        # W05R: the hard *total request* ceilings.  The one-day entry always writes both,
        # so a real run can never end up with "no ceiling configured" by accident.
        REQUEST_UTC_DAY_MAX_ENV: str(int(request_utc_day_max)),
        REQUEST_RUN_WINDOW_MAX_ENV: str(int(request_run_window_max)),
        RUN_ID_ENV: str(run_id or ("run:" + root.name)),
        # D (life-v1): the explicit bindings, and the Memory boundary when the operator gave one.
        # A run that has no Memory database simply has no continuity reader; that is reported,
        # not papered over.
        WORK_ARTIFACT_ENV: artifact_id,
    }
    if reading_artifact_id:
        environment[READING_ARTIFACT_ENV] = reading_artifact_id
    if private_items_artifact_id:
        environment[PRIVATE_ITEMS_ARTIFACT_ENV] = private_items_artifact_id
    if memory_db:
        environment[MEMORY_DB_ENV] = str(memory_db)
        environment[MEMORY_CONTROLS_DB_ENV] = str(memory_controls_db or memory_db)
        environment[MEMORY_SUBJECT_ENV] = str(memory_subject or subject)
    if world_body_socket:
        # Only when the operator points the instance at a real World gateway.  Without
        # it the World observation seam is unavailable and nothing is invented in its
        # place -- the instance simply has no World transport.
        environment[WORLD_BODY_SOCKET_ENV] = str(world_body_socket)
        environment[WORLD_BODY_MODE_ENV] = "canonical"
    if world_bridge_dir:
        environment[WORLD_BRIDGE_DIR_ENV] = str(world_bridge_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "instance_root": str(root),
        "prepared_at": _iso(),
        "subject_id": subject,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "artifact_grant_id": artifact_grant["grant_id"],
        "artifact_title": title,
        "seed_provided_by": "operator",
        "seed_version_id": created.get("version_id"),
        "supply_socket": str(supply_socket),
        "governance_socket": str(governance_socket),
        "world_id": world_id,
        "world_home": str(world_home),
        "world_package": str(package_dir),
        "life_state_root": str(life_state_root(root)),
        "work_artifact_id": artifact_id,
        "reading_artifact_id": reading_artifact_id,
        "private_items_artifact_id": private_items_artifact_id,
        "room_observation": room,
        "private_items_title": private_items_title,
        "environment": environment,
        "steps": steps,
        "notes": [
            "the seed text is the operator's; nothing here claims she wrote it",
            "the Life Supply write gates (REAL_WORKSPACE_PROVISION / REAL_ARTIFACT_ACTION) "
            "belong to the service process, not to this command",
        ],
    }
    life_state_root(root).mkdir(parents=True, exist_ok=True)
    os.chmod(life_state_root(root), 0o700)
    # The operator's own autonomous switch is persisted by the existing control surface.  A
    # prepared instance is enabled for exactly one purpose -- the run the operator is about to
    # start -- and the switch survives a restart, which is what "enable once" has to mean.
    control.set_enabled(str(root / LIFE_DIRNAME), True, actor="one-day:prepare")
    path = instance_path(root)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_json(payload), encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, "instance": str(root), "instance_file": str(path),
            "steps": steps, "world_id": world_id, "artifact_id": artifact_id,
            "workspace_id": workspace_id, "reading_artifact_id": reading_artifact_id,
            "private_items_artifact_id": private_items_artifact_id,
            "room_objects": [item["object_id"] for item in room["objects"]]}




def _observe_room(world_home: Path, package_dir: Optional[Path | str] = None) -> dict[str, Any]:
    """One real room observation, through the World owner's own projection.

    ``world_living_skeleton.observe_world`` is the same read the World service performs; the
    objects it reports are the ones actually placed in this area of the world package.  A
    world that cannot answer yields ``unavailable`` with a reason code and **no** objects:
    this entry never invents one.
    """

    try:
        import world_living_skeleton as wls

        environment = {"CHIYO_WORLD_HOME": str(world_home), "HERMES_HOME": str(world_home),
                       "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1"}
        if package_dir:
            # The World owner resolves the place map from its own package; without this the
            # projection answers "unavailable" and no object is ever named.
            environment["CHIYO_WORLD_PACKAGE"] = str(package_dir)
        projection = wls.observe_world(hermes_home=str(world_home), environment=environment,
                                       now=_now())
    except Exception as exc:
        return {"status": "unavailable", "reason_code": "ROOM_READ_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc), "objects": [],
                "placement_authority": "world"}
    if not isinstance(projection, dict) or projection.get("status") != "available":
        return {"status": "unavailable",
                "reason_code": str((projection or {}).get("reason_code")
                                   or "ROOM_OBSERVATION_UNAVAILABLE"),
                "objects": [], "placement_authority": "world"}
    environment_view = projection.get("local_environment") or {}
    objects = [
        {"object_id": str(row.get("object_id")), "name": str(row.get("name") or ""),
         "kind": str(row.get("kind") or ""), "note": str(row.get("note") or "")}
        for row in (environment_view.get("furniture") or [])
        if isinstance(row, dict) and row.get("object_id")
    ]
    return {
        "status": "available",
        "source_ref": "world_living_skeleton.observe_world",
        "reason_code": (None if environment_view else "WORLD_DECLARES_NO_LOCAL_ENVIRONMENT"),
        "observed_at": _iso(),
        "world_revision": projection.get("world_revision"),
        "place_id": ((projection.get("current_location") or {}).get("location_id")),
        "building": (environment_view.get("building") or {}).get("name"),
        "area": (environment_view.get("area") or {}).get("name"),
        "furniture_total": environment_view.get("furniture_total"),
        "objects": objects,
        # The same rows under the key the private-items adapter reads, so an observation link
        # can only name an object this read really returned.
        "local_environment": {"furniture": [dict(row) for row in objects],
                              "building": environment_view.get("building"),
                              "area": environment_view.get("area")},
        "placement_authority": "world",
    }


def _prepare_world(world_home: Path, package_dir: Path, *, seed: int, theme: str,
                   provided: bool) -> tuple[str, str]:
    """Create the world package (when not supplied) and initialize the world home.

    The world home is created by the World's own foundation module, which is the only thing
    that may write ``world_state.json``; this function never writes it directly.
    """

    if not provided:
        from components.worldgen import schema
        from components.worldgen.generator import generate_world

        docs = generate_world(seed=int(seed), theme=str(theme))
        schema.write_package(package_dir, docs)
    from components.worldgen.instance import init_instance
    import world_foundation as wf
    import world_runtime_data as wrd

    package = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(package_dir)})
    if not world_home.exists():
        # ``init_instance`` is the owner-side creator of an isolated instance: it writes the
        # world state, the timeline, the ledger and the substrate in the owners' own order, and
        # it refuses outright to touch a home that already exists.  This module never writes
        # ``world_state.json`` itself.
        init_instance(package_dir, world_home, world_id=package.world_id)
    state = wf.WorldStateStore(world_home / "data" / "world" / "world_state.json").load() or {}
    world_id = str(state.get("world_id") or "")
    if world_id != package.world_id:
        raise OneDayError("WORLD_HOME_IDENTITY_MISMATCH: %s != %s"
                          % (world_id, package.world_id))
    return world_id, ("supplied" if provided else "generated seed=%s theme=%s"
                      % (seed, theme))

# ---------------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------------


def preflight(root: Path | str) -> dict[str, Any]:
    """Read-only readiness.  ``configured`` / ``reachable`` / ``verified`` stay separate.

    No model request is made here and no key is ever printed: the model check is a TCP
    reachability probe plus a configuration description.  Whether a *model response* works is
    deliberately NOT claimed -- that needs an explicit paid call the operator must authorise.
    """

    root = Path(root)
    data = read_instance(root)
    checks: dict[str, Any] = {}

    world_home = Path(str(data["world_home"]))
    package_dir = Path(str(data["world_package"]))
    try:
        import world_foundation as wf
        import world_runtime_data as wrd

        package = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(package_dir)})
        state = wf.WorldStateStore(world_home / "data" / "world" / "world_state.json").load()
        ok = bool(state) and str(state.get("world_id")) == str(package.world_id)
        checks["world"] = {
            "status": "READY" if ok else "BLOCKED",
            "world_id": package.world_id,
            "world_home_state": "initialized" if state else "missing",
            "location_id": ((state or {}).get("location") or {}).get("place_id"),
        }
    except Exception as exc:
        checks["world"] = {"status": "BLOCKED", "detail": "%s: %s" % (type(exc).__name__, exc)}

    checks["supply"] = _preflight_supply(data)

    env = instance_environment(root)
    checks["model"] = _preflight_model(env)

    life_root = str(root / LIFE_DIRNAME)
    state_root = life_state_root(root)
    control_view = control.read_status(life_root)
    checks["life"] = {
        "status": "READY" if state_root.is_dir() else "BLOCKED",
        "state_root": str(state_root),
        "state_root_exists": state_root.is_dir(),
        "autonomous_switch": control_view.get("autonomous_switch"),
        "budget_today": control_view.get("budget_today"),
        "pause": life_pause.read(state_root),
        "writer_exclusion": _probe_writer(state_root),
    }

    world_pause: dict[str, Any] = {"configured": False}
    try:
        import world_control

        world_pause = dict(world_control.projection(str(world_home)))
        world_pause["configured"] = True
    except Exception as exc:
        world_pause = {"configured": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    checks["world_pause"] = world_pause

    blocked = [name for name, view in checks.items()
               if "status" in view and str(view.get("status")) != "READY"]
    return {
        "ok": not blocked,
        "instance": str(root),
        "checked_at": _iso(),
        "read_only": True,
        "checks": checks,
        "blocked": blocked,
        "verified_claims": {
            "configured": ["world package and home", "supply socket, grants and artifacts",
                           "model provider description", "budgets and switches"],
            "network_reachable": ["model endpoint (one TCP connect, no request, no tokens)"],
            "verified_model_response": False,
        },
        "note": "a reachable endpoint is not a working model; that needs an authorised call",
    }


def _preflight_supply(data: Mapping[str, Any]) -> dict[str, Any]:
    socket_path = str(data.get("supply_socket") or "")
    subject = str(data.get("subject_id") or "")
    out: dict[str, Any] = {"socket": socket_path, "subject": subject}
    try:
        health = supply_call(socket_path, {"op": "health"})
        out["health"] = health.get("status")
        listing = supply_call(socket_path, {"op": "list_artifacts", "subject": subject})
        out["artifacts"] = len(listing.get("artifacts") or [])
        grant = supply_call(str(data.get("governance_socket")),
                            {"op": "get_grant",
                             "grant_id": data.get("artifact_grant_id")})
        body = grant.get("grant") or {}
        out["grant"] = {"status": grant.get("status"), "capability": body.get("capability"),
                        "use_mode": body.get("use_mode"), "lifecycle": body.get("lifecycle")}
        ready = (str(out.get("health")) == "OK"
                 and str(grant.get("status")) in {"OK", "ALLOW", "ACTIVE"})
        out["status"] = "READY" if ready else "BLOCKED"
    except Exception as exc:
        out["status"] = "BLOCKED"
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def _preflight_model(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "BLOCKED"}
    try:
        from profile_model_config import describe_model_config, load_configured_model_config

        config = None
        base_url = str(env.get("NYA02_MODEL_BASE_URL") or "").strip()
        model = str(env.get("NYA02_MODEL_NAME") or "").strip()
        key = str(env.get("NYA02_MODEL_API_KEY") or "").strip()
        if base_url and model and key:
            config = {"provider": env.get("NYA02_MODEL_PROVIDER") or "custom", "model": model,
                      "base_url": base_url.rstrip("/"), "api_key": key}
        if config is None:
            config = load_configured_model_config(
                hermes_home=env.get("NYA02_PROFILE_HOME") or env.get("CHIYO_PROFILE_HOME"))
        described = describe_model_config(config or {})
        out["description"] = described
        if not described.get("usable"):
            out["detail"] = "the provider description is not usable (configuration incomplete)"
            return out
        out["probe"] = resident_module.probe_model_provider(env)
        out["status"] = "READY" if out["probe"].get("status") == "REACHABLE" else "BLOCKED"
    except Exception as exc:
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def _probe_writer(state_root: Path) -> dict[str, Any]:
    """Probe the canonical single-writer lease without keeping it."""

    try:
        import activity_continuity as ac

        lease = ac.ActivityAuthorityLease(state_root)
        lease.acquire(timeout_seconds=0.0)
        lease.release()
        return {"status": "FREE", "held_by_another_process": False}
    except Exception as exc:
        return {"status": "HELD", "held_by_another_process": True,
                "detail": "%s: %s" % (type(exc).__name__, exc)}

# ---------------------------------------------------------------------------------
# run / status / pause
# ---------------------------------------------------------------------------------


def run(root: Path | str, *, duration_seconds: int = DEFAULT_DURATION_SECONDS,
        poll_seconds: int = DEFAULT_POLL_SECONDS, max_ticks: Optional[int] = None,
        act: bool = True, once: bool = False, new_run_window: bool = False,
        extra_env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """The foreground run.  It drives the existing resident and nothing else.

    ``new_run_window`` is the *only* way a fresh request allowance is opened.  A plain
    restart carries the instance's own run identity and therefore continues the window it
    already had -- restarting is not a way to mint another 24h of budget.
    """

    root = Path(root)
    merged = dict(extra_env or {})
    if new_run_window:
        merged[NEW_RUN_WINDOW_ENV] = "1"
    env = instance_environment(root, extra=merged)
    state_root = life_state_root(root)
    # The Life runtime refuses a state root that is not 0700; this entry owns the instance's
    # root, so it is created with the mode the owner requires and never widened.
    state_root.mkdir(parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)
    resident = resident_module.AutonomousLifeResident(
        state_root=state_root, environment=env, poll_seconds=int(poll_seconds), act=bool(act))
    started = resident.start()
    if not started.get("ok"):
        detail = str(started.get("detail") or "")
        held = ("lock" in detail.lower() or "lease" in detail.lower()
                or "held" in detail.lower())
        return {"ok": False, "reason": started.get("reason"), "detail": detail,
                "exit_code": EXIT_LEASE_HELD if held else EXIT_BLOCKED}
    try:
        if once:
            tick = resident.tick()
            ran: dict[str, Any] = {"ticks": resident.ticks, "once": True, "tick": tick}
        else:
            ran = resident.run(max_ticks=max_ticks, duration_seconds=int(duration_seconds))
    finally:
        stopped = resident.stop()
    return {"ok": True, "started": started, "ran": ran, "stopped": stopped,
            "exit_code": EXIT_OK}


def status(root: Path | str) -> dict[str, Any]:
    """Read-only view.  It creates nothing, writes nothing and starts no runtime."""

    root = Path(root)
    data = read_instance(root)
    state_root = life_state_root(root)
    resident = resident_module.AutonomousLifeResident(state_root=state_root)
    out: dict[str, Any] = {
        "instance": str(root),
        "checked_at": _iso(),
        "read_only": True,
        "subject_id": data.get("subject_id"),
        "work_artifact_id": data.get("work_artifact_id") or data.get("artifact_id"),
        "reading_artifact_id": data.get("reading_artifact_id"),
        "private_items_artifact_id": data.get("private_items_artifact_id"),
        "workspace_id": data.get("workspace_id"),
        "artifact_id": data.get("artifact_id"),
        "world_home": data.get("world_home"),
        "life_state_root": str(state_root),
        "resident": resident.read_status(),
        "pause": life_pause.read(state_root),
        "control": control.read_status(str(root / LIFE_DIRNAME)),
    }
    try:
        import world_journey

        out["journey"] = world_journey.observe_journey(
            hermes_home=str(data.get("world_home")),
            environment=instance_environment(root))
    except Exception as exc:
        out["journey"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    out["work"] = _report_work(data)
    # D (life-v1): the technical state (and its evidence), the room as it was last observed, the
    # reading material's real head, and the private items' metadata view.  All read-only.
    out["state"] = classify_state(out["resident"], out["pause"])
    out["room"] = data.get("room_observation") or {"status": "unavailable",
                                                   "reason_code": "NO_ROOM_OBSERVATION"}
    out["reading"] = reading_view(root)
    out["private_items"] = private_view(root)
    out["situation"] = (out["resident"] or {}).get("last_situation")
    out["continuity"] = (out["resident"] or {}).get("last_continuity")
    return out


def set_pause(root: Path | str, paused: bool, *, reason: str = "operator") -> dict[str, Any]:
    """Write the Life-side pause bit only.  The World's own pause is not touched."""

    view = life_pause.set_paused(life_state_root(root), paused, reason=reason)
    return {"ok": True, "pause": view, "world_control_untouched": True}

# ---------------------------------------------------------------------------------
# D (life-v1): the room, the private items, reading and the timeline
# ---------------------------------------------------------------------------------

#: How the operator's reading material marks its pages.  A material without the marker is one
#: page; nothing here guesses a pagination.
PAGE_MARKER = "%%PAGE%%"

STATE_IDLE = "IDLE"
STATE_WAITING = "WAITING"
STATE_PAUSED = "PAUSED"
STATE_BLOCKED = "BLOCKED"
STATE_RUNNING = "RUNNING"
STATE_STOPPED = "STOPPED"


def artifact_port(data: Mapping[str, Any]) -> Any:
    """The existing narrow Life Supply port, bound to this instance's own socket/subject."""

    import life_artifact_port as lap

    return lap.LifeArtifactPort(str(data.get("supply_socket")), str(data.get("subject_id")))


def private_items_adapter(root: Path | str) -> Any:
    """The typed private-items adapter bound to this instance's own document, or ``None``.

    The artifact id comes from the instance file: this adapter never scans for a document, so
    the work of art cannot be mistaken for the private items (or the other way round).
    """

    data = read_instance(root)
    artifact_id = str(data.get("private_items_artifact_id") or "")
    if not artifact_id:
        return None
    import private_daily_items as pdi

    return pdi.PrivateDailyItems(artifact_port(data), artifact_id,
                                 workspace_id=data.get("workspace_id"),
                                 grant_id=data.get("artifact_grant_id"))


def _write_result(result: Any) -> Optional[dict[str, Any]]:
    """A JSON-safe view of one private-items write result."""

    if result is None:
        return None
    if hasattr(result, "to_dict"):
        try:
            return dict(result.to_dict())
        except Exception:  # pragma: no cover - a broken to_dict is reported, not guessed
            pass
    return {"repr": type(result).__name__}


def ensure_private_items(root: Path | str) -> dict[str, Any]:
    """Create this instance's private-items document if it does not have one yet.

    The document is created through the owner (B's ``create_document_artifact``) and its id is
    written back into the instance file, so every later reader -- the resident included -- binds
    to the same document.  Nothing is created when one already exists, and nothing is invented
    when the owner refuses: the refusal is raised.
    """

    data = read_instance(root)
    existing = str(data.get("private_items_artifact_id") or "")
    if existing:
        return {"ok": True, "created": False, "artifact_id": existing}
    workspace_id = str(data.get("workspace_id") or "")
    grant_id = str(data.get("artifact_grant_id") or "")
    if not workspace_id or not grant_id:
        raise OneDayError("INSTANCE_NOT_PREPARED: no workspace or artifact grant in the instance")
    import private_daily_items as pdi

    artifact_id, _outcome = pdi.create_document_artifact(
        artifact_port(data), workspace_id=workspace_id,
        title=str(data.get("private_items_title") or "私人日常事项"), grant_id=grant_id)
    data["private_items_artifact_id"] = artifact_id
    environment = dict(data.get("environment") or {})
    environment[PRIVATE_ITEMS_ARTIFACT_ENV] = artifact_id
    data["environment"] = environment
    path = instance_path(root)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_json(data), encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, "created": True, "artifact_id": artifact_id}


def _items_for_write(root: Path | str) -> Any:
    """The private-items adapter for a write: the document is created on demand, once."""

    ensure_private_items(root)
    items = private_items_adapter(root)
    if items is None:  # pragma: no cover - ensure_private_items raises before this
        raise OneDayError("NO_PRIVATE_ITEMS_DOCUMENT")
    return items



def reading_pages(content: str) -> list[str]:
    """The reading material's pages, split on the operator's own page marker."""

    if PAGE_MARKER not in str(content):
        return [str(content)]
    return [str(page) for page in str(content).split(PAGE_MARKER)]


def reading_source_id(artifact_id: str) -> str:
    """The private-items ``source_id`` for one reading artifact: ``artifact:<id>``, never doubled."""
    text = str(artifact_id or "").strip()
    return text if text.startswith("artifact:") else "artifact:%s" % text


def observe_room(root: Path | str) -> dict[str, Any]:
    """One fresh room observation through the World owner's own projection.

    This is a read, not a placement: World owns where the objects are.  A World that cannot
    answer is reported with its reason code and no objects.
    """

    data = read_instance(root)
    observation = _observe_room(Path(str(data.get("world_home"))), data.get("world_package"))
    return {"ok": observation["status"] == "available", "read_only": True,
            "observation": observation, "placement_authority": "world"}


def link_item_to_room_object(root: Path | str, *, item_id: str,
                             object_id: str) -> dict[str, Any]:
    """Link one private item to a room object that a real observation just returned."""

    items = _items_for_write(root)
    data = read_instance(root)
    observation = _observe_room(Path(str(data.get("world_home"))), data.get("world_package"))
    if observation["status"] != "available":
        raise OneDayError("ROOM_OBSERVATION_UNAVAILABLE: %s" % observation.get("reason_code"))
    result = items.note_item_at_observed_object(str(item_id), observation=observation,
                                               object_id=str(object_id), now=_iso())
    return {"ok": True, "item_id": str(item_id), "object_id": str(object_id),
            "write": _write_result(result),
            "observed_objects": [row["object_id"] for row in observation["objects"]],
            "placement_authority": "world"}


def read_page(root: Path | str, *, page: int = 1, unclear: Optional[str] = None,
              save_position: bool = True) -> dict[str, Any]:
    """Read one real page through the existing port, then record what was obtained.

    The read is the owner's: ``read_artifact`` returns the artifact's real head (version id and
    number) and its content.  The acquisition records *that* revision, the page and the number
    of characters actually taken; the reading position is a separate item, and ``understanding``
    stays ``UNKNOWN`` -- a page number is where she is, not what she understood.
    """

    data = read_instance(root)
    reading_id = str(data.get("reading_artifact_id") or "")
    if not reading_id:
        raise OneDayError("NO_READING_MATERIAL: this instance has no reading artifact")
    items = _items_for_write(root)
    port = artifact_port(data)
    view = port.read_artifact(reading_id)
    head = dict(view.get("head") or {})
    content = view.get("content")
    if not isinstance(content, str) or not content:
        raise OneDayError("READING_MATERIAL_EMPTY: the owner returned no content")
    pages = reading_pages(content)
    wanted = int(page)
    if wanted < 1 or wanted > len(pages):
        raise OneDayError("PAGE_NOT_PRESENT: page %s of %s" % (wanted, len(pages)))
    revision = str(head.get("version_id") or "")
    if not revision:
        raise OneDayError("READING_MATERIAL_NO_VERSION")
    text = pages[wanted - 1]
    source_id = reading_source_id(reading_id)
    stamp = _iso()
    acquisition = items.record_source_acquisition(
        source_id=source_id, source_revision=revision,
        receipt_ref="read:%s:%s:page-%s" % (reading_id, revision, wanted),
        obtained={"pages": [wanted], "chars": len(text)}, excerpt=text[:200], now=stamp)
    position = None
    if save_position:
        # A position is a page number, not comprehension: the adapter only accepts an int or a
        # non-empty locator string, and ``understanding`` stays UNKNOWN on its own.
        position = items.set_reading_position(source_id=source_id, source_revision=revision,
                                              position=wanted, now=stamp)
    unclear_result = None
    if unclear:
        unclear_result = items.mark_unclear(source_id=source_id, source_revision=revision,
                                            note=str(unclear), position=wanted, now=stamp)
    return {
        "ok": True,
        "artifact_id": reading_id,
        "version_id": revision,
        "version_number": head.get("version_number"),
        "page": wanted,
        "pages": len(pages),
        "obtained": {"pages": [wanted], "chars": len(text)},
        "acquisition": _write_result(acquisition),
        "reading_position": _write_result(position),
        "unclear": _write_result(unclear_result),
        "understanding": "UNKNOWN",
        "note": "the page was obtained through the owner's own read; a position is not comprehension",
    }


def add_memo(root: Path | str, *, body: str, memo_title: Optional[str] = None,
             memo_id: Optional[str] = None) -> dict[str, Any]:
    """Write a private memo.  Its text is private: the default view never returns it."""

    items = _items_for_write(root)
    result = items.add_memo(title=str(memo_title or "备忘"), body=str(body), memo_id=memo_id,
                            now=_iso())
    return {"ok": True, "write": _write_result(result)}


def save_reference(root: Path | str, *, source_id: str, source_revision: str, label: str,
                   receipt_ref: str, locator: Optional[str] = None) -> dict[str, Any]:
    """Save a reference to a source.  No full text is fetched and nothing becomes read."""

    items = _items_for_write(root)
    result = items.save_reference(source_id=str(source_id), source_revision=str(source_revision),
                                 label=str(label), receipt_ref=str(receipt_ref),
                                 locator=locator, now=_iso())
    return {"ok": True, "write": _write_result(result),
            "content_fetched": False, "read": False}


def private_view(root: Path | str) -> dict[str, Any]:
    """The private items' metadata view (no private text), or a named reason there is none."""

    items = private_items_adapter(root)
    if items is None:
        return {"available": False, "reason_code": "NO_PRIVATE_ITEMS_DOCUMENT"}
    try:
        return {"available": True, "metadata": items.metadata_view()}
    except Exception as exc:
        return {"available": False, "reason_code": "PRIVATE_ITEMS_READ_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def reading_view(root: Path | str) -> dict[str, Any]:
    """The reading material's real head, and where the reader actually is in it."""

    data = read_instance(root)
    reading_id = str(data.get("reading_artifact_id") or "")
    out: dict[str, Any] = {"artifact_id": reading_id or None}
    if reading_id:
        try:
            head = artifact_port(data).read_head(reading_id) or {}
            out.update({"head_version_id": head.get("version_id"),
                        "version_number": head.get("version_number"),
                        "source_id": reading_source_id(reading_id)})
        except Exception as exc:
            out["error"] = "%s: %s" % (type(exc).__name__, exc)
    items = private_items_adapter(root)
    if items is not None:
        try:
            metadata = items.metadata_view()
            out["positions"] = [row for row in (metadata.get("items") or [])
                                if row.get("item_type") == "reading_position"]
            out["needs_reread"] = metadata.get("needs_reread_count")
        except Exception as exc:
            out["private_error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def classify_state(summary: Mapping[str, Any], pause_view: Mapping[str, Any]) -> dict[str, Any]:
    """Which technical state this instance is in, and on what evidence.

    The labels are technical, not personal: a paused instance is paused, a window that ended in
    a named block is blocked, and an instance that simply had nothing to do is idle.  No label
    here is a claim about a mood, a relationship or a personality.
    """

    if pause_view.get("paused"):
        return {"state_class": STATE_PAUSED,
                "basis": "the life-side pause bit is set (%s)" % pause_view.get("reason_code"),
                "source": "life_pause.json"}
    if not summary or summary.get("present") is False or not summary.get("kind"):
        return {"state_class": STATE_STOPPED, "basis": "no resident record on this state root",
                "source": "resident_summary.json"}
    if summary.get("running"):
        return {"state_class": STATE_RUNNING, "basis": "the resident holds the runtime",
                "source": "resident_summary.json"}
    last_tick = summary.get("last_tick") or {}
    due = last_tick.get("due") or {}
    if str(due.get("status_class")) == "blocked":
        return {"state_class": STATE_BLOCKED,
                "basis": "the last window was blocked: %s"
                         % (due.get("detail") or due.get("outcome")),
                "source": "resident_journal.jsonl"}
    material = (due.get("material") or {})
    if material.get("foreground_activity"):
        return {"state_class": STATE_WAITING,
                "basis": "an Activity is open: %s" % material.get("foreground_activity"),
                "source": "resident_journal.jsonl"}
    return {"state_class": STATE_IDLE,
            "basis": "no pause, no open Activity, no blocked window in the last record",
            "source": "resident_journal.jsonl"}


def _step_receipts(state_root: Path) -> list[dict[str, Any]]:
    """The cycle's own step journal, read back as records."""

    path = state_root / "life_step_journal.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def timeline(root: Path | str, *, limit: int = 50) -> dict[str, Any]:
    """The instance's own event timeline, assembled from the durable records.  Read-only.

    The events are the ones the records really contain: the resident's ticks and wakes, the
    cycle's step receipts, the operator's own actions, and whatever is still UNKNOWN.  Nothing
    is synthesised for a period in which the process was down.
    """

    root = Path(root)
    data = read_instance(root)
    state_root = life_state_root(root)
    summary = resident_module.AutonomousLifeResident(state_root=state_root).read_status()
    pause_view = life_pause.read(state_root)
    events: list[dict[str, Any]] = []

    for record in _journal_records(state_root):
        moment = record.get("at")
        due = record.get("due") or {}
        events.append({"at": moment, "kind": "WINDOW", "tick": record.get("tick"),
                       "trigger": due.get("kind"), "status": due.get("status"),
                       "status_class": due.get("status_class"), "outcome": due.get("outcome"),
                       "llm_calls": due.get("llm_calls"), "detail": due.get("detail")})
        for wake in record.get("events") or []:
            events.append({"at": moment, "kind": "EVENT", "trigger": wake.get("kind"),
                           "status": wake.get("status"),
                           "status_class": wake.get("status_class"),
                           "outcome": wake.get("outcome"), "llm_calls": wake.get("llm_calls")})
        settlement = record.get("journey") or {}
        if settlement.get("arrived") or settlement.get("arrival_refused"):
            events.append({"at": moment, "kind": "JOURNEY",
                           "arrived": bool(settlement.get("arrived")),
                           "arrival_refused": bool(settlement.get("arrival_refused")),
                           "location_id": settlement.get("observed_location_id")})
        for reconciled in record.get("reconciled") or []:
            events.append({"at": moment, "kind": "RECONCILE", "detail": reconciled})

    for receipt in _step_receipts(state_root):
        events.append({"at": receipt.get("observed_at"), "kind": "STEP",
                       "effect_state": receipt.get("effect_state"),
                       "status": receipt.get("status"), "reason": receipt.get("reason"),
                       "artifact_id": receipt.get("artifact_id"),
                       "version_number": receipt.get("version_number"),
                       "activity_ref": receipt.get("activity_ref")})

    for action in control.list_recent_actions(str(root / LIFE_DIRNAME), limit=int(limit)):
        events.append({"at": action.get("recorded_at") or action.get("at"), "kind": "ACTION",
                       "action_id": action.get("action_id"),
                       "effect_state": action.get("effect_state") or action.get("state"),
                       "detail": action.get("detail")})

    events = [event for event in events if event.get("at")]
    events.sort(key=lambda event: str(event["at"]))
    state = classify_state(summary, pause_view)
    return {
        "instance": str(root),
        "generated_at": _iso(),
        "read_only": True,
        "subject_id": data.get("subject_id"),
        "state_class": state["state_class"],
        "state_basis": state["basis"],
        "pause": pause_view,
        "counts": {"events": len(events), "ticks": summary.get("ticks")},
        "pending_unknown": control.list_pending_unknown(str(root / LIFE_DIRNAME)),
        "events": events[-int(limit):],
        "not_claimed": [
            "a gap in this timeline is a stopped process, not an experience",
            "nothing here is back-filled from a clock",
        ],
    }



# ---------------------------------------------------------------------------------
# the 24h report
# ---------------------------------------------------------------------------------


def _journal_records(state_root: Path) -> list[dict[str, Any]]:
    path = state_root / resident_module.RESIDENT_JOURNAL_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

def build_report(root: Path | str, *, target_seconds: int = DEFAULT_DURATION_SECONDS,
                 gap_factor: float = 2.5) -> dict[str, Any]:
    """The 24h report.  It never mints PASS from a clock alone.

    ``PASS`` requires all three of: an observed coverage of at least ``target_seconds``, no
    unexplained gap between journal records, and at least one owner-confirmed result.  A window
    that filled up with blocked rounds, or a run whose process was down for part of it, is
    reported as exactly that.
    """

    root = Path(root)
    data = read_instance(root)
    state_root = life_state_root(root)
    resident = resident_module.AutonomousLifeResident(state_root=state_root)
    summary = resident.read_status()
    records = _journal_records(state_root)
    counters = dict(summary.get("counters") or {})

    moments = sorted(m for m in (_parse_iso(record.get("at")) for record in records) if m)
    first, last = (moments[0], moments[-1]) if moments else (None, None)
    coverage = int((last - first).total_seconds()) if first and last else 0
    poll = int(summary.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    threshold = max(1.0, float(poll) * float(gap_factor))
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(moments, moments[1:]):
        seconds = int((current - previous).total_seconds())
        if seconds > threshold:
            gaps.append({"from": previous.isoformat(timespec="seconds"),
                         "to": current.isoformat(timespec="seconds"), "seconds": seconds})
    downtime = sum(gap["seconds"] for gap in gaps)
    observed = max(0, coverage - downtime)

    confirmed = int(counters.get("owner_confirmed_versions") or 0)
    acted = int(counters.get("actions") or 0)
    running = bool(summary.get("running"))
    if not records:
        verdict = VERDICT_NO_EVIDENCE
        basis = "no tick was ever journalled; there is nothing to judge"
    elif running:
        verdict = VERDICT_RUNNING
        basis = ("the resident is still running; observed %ss of the %ss target"
                 % (observed, target_seconds))
    elif observed >= target_seconds and not gaps:
        if confirmed or acted:
            verdict = VERDICT_PASS
            basis = ("observed coverage %ss >= %ss with no gap and %s owner-confirmed result(s)"
                     % (observed, target_seconds, confirmed))
        else:
            verdict = VERDICT_NOT_PASS
            basis = ("the window was covered but no window produced a choice or an action: a "
                     "zero-action day is legal, and it does not by itself verify the autonomous "
                     "chain")
    elif observed >= target_seconds and gaps:
        verdict = VERDICT_INTERRUPTED
        basis = ("observed coverage %ss >= %ss but %ss of it is downtime in %s gap(s): a day "
                 "with a stopped process is not a day of life"
                 % (observed, target_seconds, downtime, len(gaps)))
    else:
        verdict = VERDICT_NOT_PASS
        basis = "observed coverage %ss is below the %ss target" % (observed, target_seconds)

    return {
        "kind": "one_day_life_report",
        "instance": str(root),
        "generated_at": _iso(),
        "read_only": True,
        "subject_id": data.get("subject_id"),
        "window": {
            "started_at": summary.get("started_at"),
            "this_process_started_at": summary.get("this_process_started_at"),
            "stopped_at": summary.get("stopped_at"),
            "running": running,
            "starts": summary.get("starts"),
            "ticks": summary.get("ticks"),
            "first_journal_at": first.isoformat(timespec="seconds") if first else None,
            "last_journal_at": last.isoformat(timespec="seconds") if last else None,
            "coverage_seconds": coverage,
            "downtime_seconds": downtime,
            "observed_seconds": observed,
            "gaps": gaps,
            "target_seconds": int(target_seconds),
            "soak_label": summary.get("soak_24h"),
        },
        "activity": {
            "check_windows": counters.get("check_windows"),
            "event_wakes": counters.get("event_wakes"),
            "no_action": counters.get("no_action"),
            "deferred": counters.get("defer"),
            "paused_windows": counters.get("paused_windows"),
            "acted": acted,
            "owner_confirmed_results": confirmed,
            "unknown_settlements": counters.get("unknown_settlements"),
            "by_class": counters.get("by_class"),
            "blocked_reasons": counters.get("blocked_reasons"),
            "model_calls": counters.get("model_calls"),
        },
        "model": {
            "requests": summary.get("model_calls_detail"),
            "tokens": "unavailable unless the provider reported usage in its own response",
            "budget": summary.get("budget"),
            "probe": (summary.get("model") or {}).get("probe"),
            # W05R: the persisted, atomically reserved request ledger.  This is the
            # ceiling that actually bounds what the operator pays for -- decision,
            # content and every retry -- so the report reads it instead of describing
            # "cognition calls only" and leaving content generation uncounted.
            "request_budget": _report_request_budget(root, data),
        },
        "pause": life_pause.read(state_root),
        "journey": summary.get("journey"),
        "actions": control.list_recent_actions(str(root / LIFE_DIRNAME), limit=20),
        "pending_unknown": control.list_pending_unknown(str(root / LIFE_DIRNAME)),
        "travel": _report_travel(root, data),
        "work": _report_work(data),
        "verdict": verdict,
        "verdict_basis": basis,
        "not_claimed": [
            "this report does not claim a real model was called; the request counts come from "
            "the instance's own records",
            "a stopped process is never counted as experience",
            "zero actions is a legal day and is not evidence that the chain was exercised",
        ],
    }


def _report_travel(root: Path | str, data: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import world_journey

        env = instance_environment(root)
        home = str(data.get("world_home"))
        state = world_journey.load_state(home)
        projection = world_journey.observe_journey(hermes_home=home, environment=env)
        history = []
        if isinstance(state, Mapping):
            for entry in list(state.get("history") or [])[-5:]:
                if isinstance(entry, Mapping):
                    history.append({"journey_id": entry.get("journey_id"),
                                    "status": entry.get("status"),
                                    "target_location_id": entry.get("target_location_id"),
                                    "closed_at": entry.get("closed_at")})
        return {"projection": projection, "recent_history": history}
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _report_work(data: Mapping[str, Any]) -> dict[str, Any]:
    """Read the *bound* work's real head, and a slice of its real text -- not just a hash.

    D (life-v1): the artifact is named in the instance file.  ``latest_resumable`` is only the
    fallback for an instance prepared before the binding existed, so a newer private-items
    document or reading material can never be reported as "the work".
    """

    try:
        import life_artifact_port as lap

        port = lap.LifeArtifactPort(str(data.get("supply_socket")), str(data.get("subject_id")))
        bound = str(data.get("work_artifact_id") or data.get("artifact_id") or "")
        selection = "BOUND_WORK" if bound else "LATEST_RESUMABLE"
        point = port.resume_point(bound) if bound else port.latest_resumable()
        if point is None:
            return {"resumable": False, "selected_by": selection}
        view = point.to_dict()
        view["selected_by"] = selection
        if point.content is not None:
            view["content_excerpt"] = point.content[:400]
            view["content_lines"] = point.content.count("\n") + 1
        return view
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _report_request_budget(root: Path | str, data: Mapping[str, Any]) -> dict[str, Any]:
    """The persisted request ledger, read-only.  Never guesses a number."""

    environment = dict(data.get("environment") or {})
    day_max = environment.get(REQUEST_UTC_DAY_MAX_ENV)
    window_max = environment.get(REQUEST_RUN_WINDOW_MAX_ENV)
    run_id = environment.get(RUN_ID_ENV)
    try:
        import life_request_budget as lrb

        budget = lrb.LifeRequestBudget(
            life_state_root(root),
            utc_day_max=(int(day_max) if day_max not in (None, "") else None),
            run_window_max=(int(window_max) if window_max not in (None, "") else None),
            run_id=(str(run_id) if run_id else None))
        view = budget.view()
    except Exception as exc:  # an unreadable ledger is reported, never rounded to zero
        return {"state": "unreadable", "detail": "%s: %s" % (type(exc).__name__, exc),
                "counts": "decision + content + retry, reserved before each attempt",
                "refund_policy": "no release API: a timeout or crash keeps its reservation"}
    view["counts"] = "decision + content + retry, reserved before each attempt"
    view["day_max_env"] = REQUEST_UTC_DAY_MAX_ENV
    view["run_window_max_env"] = REQUEST_RUN_WINDOW_MAX_ENV
    return view

# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], *, stream: Any = None) -> None:
    print(_json(dict(payload)), file=stream or sys.stdout, flush=True)


def _fail(reason: str, detail: str, code: int) -> int:
    _emit({"ok": False, "reason_code": reason, "detail": detail}, stream=sys.stderr)
    return code


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="One day of autonomous life (W05).")
    parser.add_argument("verb", choices=("prepare", "preflight", "run", "status", "pause",
                                         "resume", "report", "timeline", "read", "note",
                                         "room", "invite"))
    parser.add_argument("--instance", required=True, help="the isolated instance root")
    parser.add_argument("--create-private-items", action="store_true",
                        help="also create the typed private-items document at prepare time")
    parser.add_argument("--governance-socket")
    parser.add_argument("--subject", default="chiyo")
    parser.add_argument("--title", default="")
    parser.add_argument("--seed-content")
    parser.add_argument("--world-package")
    parser.add_argument("--world-seed", type=int, default=929711)
    parser.add_argument("--world-theme", default="coastal_town")
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-act", action="store_true")
    parser.add_argument("--reason", default="operator")
    # W05R: the total request ceilings and the World gateway transport.  Both ceilings are
    # always written into the instance; the run identity defaults to the instance's own, so
    # a restart continues the same window instead of minting a new one.
    parser.add_argument("--request-utc-day-max", type=int,
                        default=DEFAULT_REQUEST_UTC_DAY_MAX)
    parser.add_argument("--request-run-window-max", type=int,
                        default=DEFAULT_REQUEST_RUN_WINDOW_MAX)
    parser.add_argument("--run-id")
    parser.add_argument("--new-run-window", action="store_true",
                        help="explicitly open a new request window (a plain restart does not)")
    parser.add_argument("--world-body-socket",
                        help="the World service's own gateway.sock for this instance")
    parser.add_argument("--world-bridge-dir",
                        help="<repo>/components/world/service/bridge (for the World bridge)")
    parser.add_argument("--env", action="append", default=[],
                        help="extra KEY=VALUE for the run, repeatable")
    # D (life-v1): the private life surface, the reading material and the co-presence protocol.
    parser.add_argument("--reading-title", default="")
    parser.add_argument("--reading-content",
                        help="the operator's reading material (pages separated by %s)"
                             % "%%PAGE%%")
    parser.add_argument("--create-private-items", action="store_true",
                        help="also create the typed private-items document at prepare time")
    parser.add_argument("--memory-db", help="the M0 evidence database, when there is one")
    parser.add_argument("--memory-controls-db")
    parser.add_argument("--memory-subject")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--memo", help="the body of a private memo")
    parser.add_argument("--reference-source-id")
    parser.add_argument("--reference-source-revision")
    parser.add_argument("--reference-label")
    parser.add_argument("--reference-receipt-ref")
    parser.add_argument("--locator")
    parser.add_argument("--item-id")
    parser.add_argument("--room-object")
    parser.add_argument("--invitation", help="a JSON invitation/end event for the co-presence protocol")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    extra: dict[str, str] = {}
    for item in args.env:
        name, _, value = str(item).partition("=")
        extra[name.strip()] = value.strip()

    try:
        if args.verb == "prepare":
            if not args.supply_socket or not args.governance_socket or not args.title:
                return _fail("USAGE", "prepare needs --supply-socket, --governance-socket and "
                                      "--title", EXIT_USAGE)
            result = prepare(instance=args.instance, supply_socket=args.supply_socket,
                             governance_socket=args.governance_socket, subject=args.subject,
                             title=args.title, world_package=args.world_package,
                             seed_content=args.seed_content, world_seed=args.world_seed,
                             world_theme=args.world_theme,
                             request_utc_day_max=args.request_utc_day_max,
                             request_run_window_max=args.request_run_window_max,
                             run_id=args.run_id,
                             world_body_socket=args.world_body_socket,
                             world_bridge_dir=args.world_bridge_dir,
                             reading_title=args.reading_title,
                             reading_content=args.reading_content,
                             create_private_items=bool(args.create_private_items),
                             memory_db=args.memory_db,
                             memory_controls_db=args.memory_controls_db,
                             memory_subject=args.memory_subject)
        elif args.verb == "preflight":
            result = preflight(args.instance)
        elif args.verb == "run":
            result = run(args.instance, duration_seconds=args.duration_seconds,
                         poll_seconds=args.poll_seconds, max_ticks=args.max_ticks,
                         act=not args.no_act, once=args.once,
                         new_run_window=bool(args.new_run_window), extra_env=extra)
        elif args.verb == "status":
            result = status(args.instance)
        elif args.verb == "pause":
            result = set_pause(args.instance, True, reason=args.reason)
        elif args.verb == "resume":
            result = set_pause(args.instance, False, reason=args.reason)
        elif args.verb == "timeline":
            result = timeline(args.instance, limit=args.limit)
        elif args.verb == "read":
            result = read_page(args.instance, page=args.page, unclear=args.unclear)
        elif args.verb == "note":
            if args.memo:
                result = add_memo(args.instance, body=args.memo)
            elif args.reference_source_id and args.reference_source_revision \
                    and args.reference_label and args.reference_receipt_ref:
                result = save_reference(args.instance, source_id=args.reference_source_id,
                                        source_revision=args.reference_source_revision,
                                        label=args.reference_label,
                                        receipt_ref=args.reference_receipt_ref,
                                        locator=args.locator)
            else:
                return _fail("USAGE", "note needs --memo, or --reference-source-id "
                                      "--reference-source-revision --reference-label "
                                      "--reference-receipt-ref", EXIT_USAGE)
        elif args.verb == "room":
            if args.item_id and args.room_object:
                result = link_item_to_room_object(args.instance, item_id=args.item_id,
                                                  object_id=args.room_object)
            else:
                result = observe_room(args.instance)
        elif args.verb == "invite":
            import life_co_presence

            result = life_co_presence.operator_action(args.instance, raw=args.invitation)
        else:
            result = build_report(args.instance, target_seconds=args.duration_seconds)
    except OneDayError as exc:
        return _fail("REFUSED", str(exc), EXIT_REFUSED)
    except Exception as exc:  # a real failure is reported as one
        return _fail("FAILED", "%s: %s" % (type(exc).__name__, exc), EXIT_BLOCKED)

    code = int(result.pop("exit_code", EXIT_OK)) if isinstance(result, dict) else EXIT_OK
    _emit(result)
    if args.verb == "preflight" and not result.get("ok"):
        return EXIT_REFUSED
    return code


if __name__ == "__main__":
    raise SystemExit(main())