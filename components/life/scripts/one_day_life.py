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
import life_runtime_coverage as runtime_coverage

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

#: Where the report's coverage numbers came from.  Only ``PROVEN_PROCESS_LEDGER`` -- a record
#: each process wrote with its own ``time.monotonic()`` -- can support a PASS.  A journal's
#: wake span and an old summary's wall span are both reported, both labelled unverified.
COVERAGE_BASIS_LEDGER = "PROVEN_PROCESS_LEDGER"
COVERAGE_BASIS_LEGACY = "LEGACY_SUMMARY_WALL_SPAN_UNVERIFIED"
COVERAGE_BASIS_JOURNAL = "JOURNAL_ONLY_UNVERIFIED"
COVERAGE_BASIS_ABSENT = "NO_COVERAGE_RECORD"

#: How a window with no owner output may be read.  None of these is "she chose to rest":
#: a rest is an owner-recorded decision, and a technical failure is not a choice.
QUIET_OWNER_OUTPUT = "OWNER_OUTPUT_PRESENT"
QUIET_NO_OWNER_OUTPUT = "QUIET_AND_UNBLOCKED"
QUIET_MODEL_UNAVAILABLE = "TECHNICAL_MODEL_UNAVAILABLE"
QUIET_BLOCKED = "TECHNICAL_BLOCKED"
QUIET_UNVERIFIED = "UNVERIFIED_NO_RECORD"

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
            max_cognition_calls_per_day: int = DEFAULT_MAX_COGNITION_CALLS_PER_DAY,
            run_id: Optional[str] = None,
            world_body_socket: Optional[Path | str] = None,
            world_bridge_dir: Optional[Path | str] = None,
            reading_title: str = "",
            reading_content: Optional[str] = None,
            private_items_title: str = "私人日常事项",
            create_private_items: bool = False,
            create_work_artifact: bool = True,
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
    # 额度必须是显式整数：默认 96/96/96 不变，显式新总限由调用方（fast_day prepare）
    # 绑定到这一新实例，不能靠改常量变成全项目默认。
    for name, value, minimum in (
            ("request_utc_day_max", request_utc_day_max, 0),
            ("request_run_window_max", request_run_window_max, 0),
            ("max_cognition_calls_per_day", max_cognition_calls_per_day, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise OneDayError("%s_INVALID: %r" % (name.upper(), value))

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
    artifact_id: Optional[str] = None
    created: Optional[dict[str, Any]] = None
    if create_work_artifact:
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
    else:
        # 专用开关：不建 demo 的占位作品。房间/物件/阅读材料仍然是真实的，但没有任何
        # 合成种子被当成她的历史；首次作品由她自己后来真实创建。
        steps.append({"step": "create_artifact", "skipped": "SEED_WORK_DISABLED"})

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
            str(int(max_cognition_calls_per_day)),
        "CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY": str(DEFAULT_MAX_ACTIONS_PER_DAY),
        # W05R: the hard *total request* ceilings.  The one-day entry always writes both,
        # so a real run can never end up with "no ceiling configured" by accident.
        REQUEST_UTC_DAY_MAX_ENV: str(int(request_utc_day_max)),
        REQUEST_RUN_WINDOW_MAX_ENV: str(int(request_run_window_max)),
        RUN_ID_ENV: str(run_id or ("run:" + root.name)),
        # D (life-v1): the explicit bindings, and the Memory boundary when the operator gave one.
        # A run that has no Memory database simply has no continuity reader; that is reported,
        # not papered over.  With no seeded work artifact there is no work binding to carry.
    }
    if artifact_id:
        environment[WORK_ARTIFACT_ENV] = artifact_id
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
        "artifact_title": title if create_work_artifact else "",
        "seed_provided_by": "operator" if create_work_artifact else "",
        "seed_version_id": (created or {}).get("version_id"),
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
    out["state"] = classify_state(out["resident"], out["pause"],
                                  activity=activity_view(state_root),
                                  windows=recent_windows(state_root))
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


#: The Activity authority's own file.  It is read here as a file on purpose: the canonical
#: store's read service takes the store's IO lock and creates ``run/`` before reading, and a
#: status view must create nothing on an instance that has not been started yet.
CANONICAL_ACTIVITY_STATE_FILENAME = "canonical_life_state.json"
#: The statuses that occupy the one foreground slot (``activity_continuity``'s own vocabulary).
OPEN_ACTIVITY_STATUSES = ("ACTIVE", "PAUSED", "WAITING")
#: A bounded journal read: enough for the newest windows, never the whole life of the instance.
JOURNAL_TAIL_BYTES = 65536
JOURNAL_TAIL_RECORDS = 40
#: A window that considered nothing (NOT_DUE/QUIET, or held by the operator pause) is not
#: evidence that anything was recovered, so it is skipped when looking for the last block.
SILENT_STATUS_CLASSES = frozenset({"silence"})


def activity_view(state_root: Path | str) -> dict[str, Any]:
    """The foreground Activity, read from the authority's own file.  Creates nothing.

    ``READ`` means the authority was parsed: ``foreground_activity_ref`` is then either a ref
    whose Activity is open (``foreground_status`` names ACTIVE/PAUSED/WAITING) or ``None``.
    Anything else is ``UNVERIFIED`` with a named reason, and no caller may turn that into
    "there is no open Activity".
    """

    path = Path(state_root) / CANONICAL_ACTIVITY_STATE_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"status": "UNVERIFIED", "reason_code": "CANONICAL_STATE_ABSENT",
                "source": path.name}
    except OSError as exc:
        return {"status": "UNVERIFIED", "reason_code": "CANONICAL_STATE_UNREADABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc), "source": path.name}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {"status": "UNVERIFIED", "reason_code": "CANONICAL_STATE_CORRUPT",
                "detail": "%s: %s" % (type(exc).__name__, exc), "source": path.name}
    if not isinstance(data, dict):
        return {"status": "UNVERIFIED", "reason_code": "CANONICAL_STATE_CORRUPT",
                "detail": "top-level JSON is not an object", "source": path.name}
    activities = data.get("activities")
    if activities is None:
        activities = {}
    if not isinstance(activities, Mapping):
        return {"status": "UNVERIFIED", "reason_code": "CANONICAL_STATE_CORRUPT",
                "detail": "activities is not an object", "source": path.name}
    ref = data.get("foreground_activity_ref")
    view: dict[str, Any] = {"status": "READ", "source": path.name,
                            "revision": data.get("revision"),
                            "foreground_activity_ref": ref,
                            "foreground_status": None, "foreground_kind": None}
    if ref is None:
        return view
    act = activities.get(ref)
    if not isinstance(act, Mapping):
        return {"status": "UNVERIFIED", "reason_code": "FOREGROUND_ACTIVITY_MISSING",
                "detail": "foreground_activity_ref does not exist in activities",
                "foreground_activity_ref": ref, "source": path.name}
    if act.get("status") not in OPEN_ACTIVITY_STATUSES:
        return {"status": "UNVERIFIED", "reason_code": "FOREGROUND_ACTIVITY_NOT_OPEN",
                "detail": "foreground activity status is %r" % act.get("status"),
                "foreground_activity_ref": ref, "source": path.name}
    view["foreground_status"] = act.get("status")
    view["foreground_kind"] = act.get("activity_kind")
    return view


def recent_windows(state_root: Path | str, *, max_bytes: int = JOURNAL_TAIL_BYTES,
                   limit: int = JOURNAL_TAIL_RECORDS) -> dict[str, Any]:
    """The newest resident windows, from a bounded tail of the journal.  Creates nothing."""

    path = Path(state_root) / resident_module.RESIDENT_JOURNAL_FILENAME
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max(0, int(max_bytes)))
            handle.seek(start)
            blob = handle.read()
    except FileNotFoundError:
        return {"status": "ABSENT", "records": [], "complete": True, "source": path.name}
    except OSError as exc:
        return {"status": "UNREADABLE", "records": [], "complete": False,
                "detail": "%s: %s" % (type(exc).__name__, exc), "source": path.name}
    lines = blob.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the first line may start mid-record
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return {"status": "READ", "records": records[-max(1, int(limit)):],
            "complete": start == 0, "source": path.name}


def classify_state(summary: Mapping[str, Any], pause_view: Mapping[str, Any], *,
                   activity: Optional[Mapping[str, Any]] = None,
                   windows: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Which technical state this instance is in, and on what evidence.

    The labels are technical, not personal: a paused instance is paused, a window that ended in
    a named block is blocked, and an instance that simply had nothing to do is idle.  No label
    here is a claim about a mood, a relationship or a personality.

    The open-Activity clause reads the Activity *authority* (``activity_view``), never the
    resident summary: the summary has no per-tick record at all.  The blocked clause reads the
    newest window that actually considered something (``recent_windows``); a silent window is
    not a recovery.  When either authority cannot be read the basis says so by name and makes
    no claim about open Activities.  The two optional arguments keep older two-argument callers
    working; such a caller simply did not supply those reads.
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
    last_considered: Optional[tuple[Mapping[str, Any], Mapping[str, Any]]] = None
    if isinstance(windows, Mapping) and windows.get("status") == "READ":
        for record in reversed(windows.get("records") or []):
            due = record.get("due") if isinstance(record, Mapping) else None
            if not isinstance(due, Mapping):
                continue
            status_class = str(due.get("status_class") or "")
            if not status_class or status_class in SILENT_STATUS_CLASSES:
                continue
            last_considered = (record, due)
            break
    if last_considered is not None and str(last_considered[1].get("status_class")) == "blocked":
        record, due = last_considered
        return {"state_class": STATE_BLOCKED,
                "basis": "the last window that considered anything was blocked at %s: %s"
                         % (record.get("at") or "an unrecorded time",
                            due.get("detail") or due.get("outcome") or due.get("status")),
                "source": "resident_journal.jsonl"}
    basis: list[str] = []
    sources: list[str] = []
    if isinstance(activity, Mapping) and activity.get("status") == "READ":
        sources.append(str(activity.get("source") or "canonical_life_state.json"))
        ref = activity.get("foreground_activity_ref")
        if ref:
            return {"state_class": STATE_WAITING,
                    "basis": "an Activity is open: %s (status %s, canonical revision %s)"
                             % (ref, activity.get("foreground_status"),
                                activity.get("revision")),
                    "source": "canonical_life_state.json"}
        basis.append("the Activity authority was read (canonical revision %s) and names no "
                     "foreground Activity" % activity.get("revision"))
    elif activity is None:
        basis.append("the Activity authority was not read by this caller, so this view makes "
                     "no claim about open Activities")
    else:
        sources.append(str(activity.get("source") or "canonical_life_state.json"))
        basis.append("the Activity authority could not be verified (%s); this view does not "
                     "claim that no Activity is open"
                     % (activity.get("reason_code") or "UNVERIFIED"))
    if last_considered is not None:
        record, due = last_considered
        sources.append("resident_journal.jsonl")
        basis.append("the last window that considered anything ended as %s at %s"
                     % (str(due.get("status_class")), record.get("at") or "an unrecorded time"))
    elif isinstance(windows, Mapping) and windows.get("status") == "READ":
        sources.append("resident_journal.jsonl")
        if windows.get("records"):
            basis.append("no window in the read tail considered anything (silent windows only)")
        else:
            basis.append("the resident journal records no window")
    elif windows is None:
        basis.append("the resident journal was not read by this caller")
    else:
        sources.append("resident_journal.jsonl")
        basis.append("the resident journal could not be read (%s)"
                     % (windows.get("status") or "UNREADABLE"))
    return {"state_class": STATE_IDLE, "basis": "; ".join(basis),
            "source": " + ".join(dict.fromkeys(sources))}


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

def _journal_window(records: list[dict[str, Any]], *, poll_seconds: int,
                    gap_factor: float) -> dict[str, Any]:
    """The old journal-only arithmetic, kept for old readers -- and never a verdict basis.

    A journal holds *wakes*.  The runner merges a QUIET window and a NOT_DUE window into a
    silent step, so the distance between two records is mostly "nothing was due", not "the
    process was down".  The numbers are still returned (an old record and an old reader need
    them), but they are labelled unverified and no verdict is decided from them.
    """

    moments = sorted(m for m in (_parse_iso(record.get("at")) for record in records) if m)
    first, last = (moments[0], moments[-1]) if moments else (None, None)
    span = int((last - first).total_seconds()) if first and last else 0
    threshold = max(1.0, float(poll_seconds) * float(gap_factor))
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(moments, moments[1:]):
        seconds = int((current - previous).total_seconds())
        if seconds > threshold:
            gaps.append({"from": previous.isoformat(timespec="seconds"),
                         "to": current.isoformat(timespec="seconds"), "seconds": seconds})
    gap_seconds = sum(gap["seconds"] for gap in gaps)
    return {"first": first, "last": last, "span_seconds": span, "gaps": gaps,
            "gap_seconds": gap_seconds, "observed_seconds": max(0, span - gap_seconds)}


def _report_quiet(summary: Mapping[str, Any], counters: Mapping[str, Any]) -> dict[str, Any]:
    """What a window with no owner output may and may not be read as.

    Zero actions and zero requests are not evidence that she was not alive, and they are not a
    choice to rest either: a rest is an owner-recorded decision, and nothing in this report is
    one.  What *can* be named is the technical reason a window produced nothing -- an
    unreachable model, a blocked owner, a refused request -- because that is not her doing.
    """

    detail = summary.get("model_calls_detail")
    detail = detail if isinstance(detail, Mapping) else {}
    requests = detail.get("total_requests")
    blocked: dict[str, int] = {}
    raw = counters.get("blocked_reasons")
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            try:
                count = int(value or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                blocked[str(key)] = count
    model = summary.get("model")
    probe = (model or {}).get("probe") if isinstance(model, Mapping) else None
    probe_status = probe.get("status") if isinstance(probe, Mapping) else None
    acted = int(counters.get("actions") or 0)
    confirmed = int(counters.get("owner_confirmed_versions") or 0)
    has_counters = isinstance(summary.get("counters"), Mapping) and bool(summary.get("counters"))
    recorded = (summary.get("present") is not False and isinstance(summary.get("kind"), str)
                and has_counters)

    model_blocked = sorted(reason for reason in blocked if "MODEL" in reason.upper())
    if not recorded:
        reading = QUIET_UNVERIFIED
    elif model_blocked or probe_status == "UNREACHABLE":
        reading = QUIET_MODEL_UNAVAILABLE
    elif blocked:
        reading = QUIET_BLOCKED
    elif acted or confirmed:
        reading = QUIET_OWNER_OUTPUT
    else:
        reading = QUIET_NO_OWNER_OUTPUT

    if reading == QUIET_UNVERIFIED:
        basis = ("no resident record was read: nothing here says whether the window was quiet "
                 "or whether the process was alive")
    elif reading == QUIET_MODEL_UNAVAILABLE:
        basis = ("the model was unavailable (%s): a technical failure is not a quiet day and not "
                 "her choosing to rest" % ", ".join(model_blocked or [str(probe_status)]))
    elif reading == QUIET_BLOCKED:
        basis = ("windows were blocked by their own owner (%s): that is a refusal, not a choice "
                 "to rest" % ", ".join(sorted(blocked)))
    elif reading == QUIET_OWNER_OUTPUT:
        basis = "%s owner-confirmed result(s) and %s acted window(s) are recorded" % (confirmed,
                                                                                     acted)
    else:
        basis = ("a zero-action window is legal: no owner output and no counted request is not "
                 "evidence that she was not alive, and it is not a recorded choice to rest "
                 "either")

    return {
        "reading": reading,
        "reading_basis": basis,
        "owner_output": {"actions": acted, "owner_confirmed_results": confirmed},
        "requests": requests,
        "requests_known": requests is not None,
        "requests_source": detail.get("total_source"),
        "model_probe": probe_status,
        "blocked_reasons": blocked,
        "reads_as_selected_rest": False,
        "note": ("zero owner output and zero requests are never read as \"not alive\"; "
                 "unavailability, blocked windows and model failures are named as technical, "
                 "never as rest"),
    }


def _current_tail(view: Mapping[str, Any], *, moment: datetime) -> dict[str, Any]:
    """Where the measured part ends, and whether that end was a real closure.

    ``closed_process_seconds`` is time a process measured *and* then stopped for;
    ``open_tail_seconds`` is time an open process measured up to its last heartbeat, whose
    interval was never handed over.  Everything after that heartbeat is not measured at all, so
    it is reported as a distance, never as life.
    """

    processes = [row for row in (view.get("processes") or []) if isinstance(row, Mapping)]
    last = dict(processes[-1]) if processes else None
    open_tail = bool(last) and str(last.get("closed")) != runtime_coverage.CLOSED_STOP
    out: dict[str, Any] = {
        "ledger_present": str((view.get("sources") or {}).get("ledger") or "ABSENT") == "PRESENT",
        "closed_process_seconds": int(view.get("closed_process_seconds") or 0),
        "open_tail_seconds": int(view.get("open_tail_seconds") or 0),
        "open": open_tail,
        "last_process": last,
        "last_edge": None,
        "since_last_edge_seconds": None,
        "unmeasured_after": None,
        "unmeasured_seconds": None,
    }
    if last:
        # Where the record itself ends: a stop for a closed process, the last heartbeat for an
        # open one.  The distance from there to ``now`` is reported as a distance -- wall clock
        # after the record is not life, and it is not counted as coverage either.
        edge_value = (last.get("stopped_at") or last.get("last_heartbeat_at")
                      or last.get("started_at"))
        edge = _parse_iso(edge_value)
        out["last_edge"] = edge_value
        out["since_last_edge_seconds"] = int((moment - edge).total_seconds()) if edge else None
        if open_tail:
            heartbeat_value = last.get("last_heartbeat_at") or last.get("started_at")
            heartbeat = _parse_iso(heartbeat_value)
            out["unmeasured_after"] = heartbeat_value
            out["unmeasured_seconds"] = (int((moment - heartbeat).total_seconds())
                                         if heartbeat else None)
    return out


def build_report(root: Path | str, *, target_seconds: int = DEFAULT_DURATION_SECONDS,
                 gap_factor: float = 2.5, now: Optional[datetime] = None) -> dict[str, Any]:
    """The 24h report.  It reads the coverage ledger and never mints PASS from a clock.

    ``PASS`` requires *proven* coverage: at least ``target_seconds`` of it, every process
    closed with its own monotonic measurement, no offline gap, no unknown remainder and no
    clock ambiguity.  A journal-only record has no such measurement, so it is reported as
    unverified and can never pass: the journal's first/last wake instant is a wall span, and
    the distance between two wakes is mostly silence -- the runner merges QUIET and NOT_DUE
    into one silent step -- not downtime.  Nothing here mints ``SOAK_VERIFIED``: a reached
    threshold is arithmetic over measured seconds, not a judgement that a day of life happened.
    ``now`` is injectable so a test can place the record a year away without waiting a second;
    an injected clock still proves nothing about a real 24h run.
    """

    root = Path(root)
    data = read_instance(root)
    state_root = life_state_root(root)
    resident = resident_module.AutonomousLifeResident(state_root=state_root)
    summary = resident.read_status()
    records = _journal_records(state_root)
    counters = dict(summary.get("counters") or {})
    moment = now if isinstance(now, datetime) else _now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    poll = int(summary.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    journal = _journal_window(records, poll_seconds=poll, gap_factor=gap_factor)
    view = resident.recorded_coverage(now=moment)
    sources = view.get("sources") or {}
    ledger_present = str(sources.get("ledger") or "ABSENT") == "PRESENT"
    legacy_present = str(sources.get("legacy_summary") or "ABSENT") == "PRESENT_UNPROVEN"
    if ledger_present:
        coverage_basis = COVERAGE_BASIS_LEDGER
    elif legacy_present:
        coverage_basis = COVERAGE_BASIS_LEGACY
    elif records:
        coverage_basis = COVERAGE_BASIS_JOURNAL
    else:
        coverage_basis = COVERAGE_BASIS_ABSENT

    covered = int(view.get("covered_seconds") or 0)
    offline = int(view.get("offline_gap_seconds") or 0)
    unknown = int(view.get("unknown_seconds") or 0)
    overlap_seconds = int(view.get("overlap_seconds") or 0)
    complete = bool(view.get("coverage_complete"))
    continuous = bool(view.get("continuous"))
    ambiguous = bool(view.get("time_ambiguity"))
    tail = _current_tail(view, moment=moment)
    soak = runtime_coverage.soak_labels(view)

    confirmed = int(counters.get("owner_confirmed_versions") or 0)
    acted = int(counters.get("actions") or 0)
    running = bool(summary.get("running"))
    quiet = _report_quiet(summary, counters)
    target = int(target_seconds)

    if coverage_basis == COVERAGE_BASIS_ABSENT:
        verdict = VERDICT_NO_EVIDENCE
        basis = ("no tick was ever journalled and no process coverage ledger exists; there is "
                 "nothing to judge")
    elif coverage_basis == COVERAGE_BASIS_LEDGER and running:
        verdict = VERDICT_RUNNING
        basis = ("the resident is still running: %ss closed + %ss open tail measured, and %ss "
                 "after the last heartbeat is not measured yet -- a running process is not a "
                 "closed day" % (tail["closed_process_seconds"], tail["open_tail_seconds"],
                                 tail["unmeasured_seconds"]
                                 if tail["unmeasured_seconds"] is not None else 0))
    elif coverage_basis == COVERAGE_BASIS_LEDGER and covered >= target and complete \
            and continuous and not ambiguous:
        verdict = VERDICT_PASS
        basis = ("proven coverage %ss >= %ss: every process closed with its own monotonic "
                 "measurement, no offline gap, no unknown remainder, no clock ambiguity (a "
                 "reached threshold is still not %s)" % (covered, target, soak["soak_verified"]))
    elif coverage_basis == COVERAGE_BASIS_LEDGER and covered >= target:
        verdict = VERDICT_INTERRUPTED
        basis = ("proven coverage %ss >= %ss but the measured time is not one continuous day: "
                 "%ss offline, %ss unknown, %ss overlapping, coverage_complete=%s, continuous=%s"
                 % (covered, target, offline, unknown, overlap_seconds, complete, continuous))
    elif coverage_basis == COVERAGE_BASIS_LEDGER:
        verdict = VERDICT_NOT_PASS
        basis = ("proven coverage %ss is below the %ss target (%ss closed + %ss open tail, %ss "
                 "unknown, %ss offline)" % (covered, target, tail["closed_process_seconds"],
                                            tail["open_tail_seconds"], unknown, offline))
    else:
        verdict = VERDICT_NOT_PASS
        if coverage_basis == COVERAGE_BASIS_LEGACY:
            basis = ("no per-process coverage ledger exists: the only record is an old summary's "
                     "wall span, and its %ss are counted as unknown (%s), never as coverage -- "
                     "this window is unverified and cannot pass" % (unknown, coverage_basis))
        else:
            basis = ("no per-process coverage ledger exists (%s): the %ss journal wake span and "
                     "its %ss of unnamed gaps are not proven coverage, so this window is "
                     "unverified and cannot pass"
                     % (coverage_basis, journal["span_seconds"], journal["gap_seconds"]))

    if quiet["reading"] != QUIET_UNVERIFIED:
        basis = "%s; %s" % (basis, quiet["reading_basis"])

    window = {
        "started_at": summary.get("started_at"),
        "this_process_started_at": summary.get("this_process_started_at"),
        "stopped_at": summary.get("stopped_at"),
        "running": running,
        "starts": summary.get("starts"),
        "ticks": summary.get("ticks"),
        "first_journal_at": (journal["first"].isoformat(timespec="seconds")
                             if journal["first"] else None),
        "last_journal_at": (journal["last"].isoformat(timespec="seconds")
                            if journal["last"] else None),
        "journal_span_seconds": journal["span_seconds"],
        # The old keys stay for old readers, each with its basis named beside it: they hold
        # *proven* coverage only when a ledger exists, and the journal's own numbers otherwise
        # (which are an observation span of wakes, not life and not downtime).
        "coverage_seconds": covered if ledger_present else journal["span_seconds"],
        "downtime_seconds": offline if ledger_present else journal["gap_seconds"],
        "downtime_seconds_basis": ("MEASURED_OFFLINE_GAP" if ledger_present
                                   else "JOURNAL_WAKE_GAP_UNVERIFIED_NOT_DOWNTIME"),
        "observed_seconds": covered if ledger_present else journal["observed_seconds"],
        "observed_seconds_basis": ("PROVEN_PROCESS_ONLINE_SECONDS" if ledger_present
                                   else "JOURNAL_WAKE_SPAN_UNVERIFIED"),
        "gaps": ([dict(gap) for gap in (view.get("gaps") or [])] if ledger_present
                 else list(journal["gaps"])),
        "coverage_basis": coverage_basis,
        "closed_process_seconds": int(view.get("closed_process_seconds") or 0),
        "open_tail_seconds": int(view.get("open_tail_seconds") or 0),
        "unknown_seconds": unknown,
        "unknown": [dict(entry) for entry in (view.get("unknown") or [])],
        "overlap_seconds": overlap_seconds,
        "time_ambiguity": ambiguous,
        "wall_rollback": bool(view.get("wall_rollback")),
        "covered_exceeds_wall_span": bool(view.get("covered_exceeds_wall_span")),
        "wall_span_seconds": view.get("wall_span_seconds"),
        "process_count": view.get("process_count"),
        "coverage_complete": complete,
        "continuous": continuous,
        "measured": view,
        "current_tail": tail,
        "target_seconds": target,
        "soak_label": soak["soak_24h"],
    }

    return {
        "kind": "one_day_life_report",
        "instance": str(root),
        "generated_at": _iso(moment),
        "read_only": True,
        "subject_id": data.get("subject_id"),
        "window": window,
        "soak": {
            "soak_24h": soak["soak_24h"],
            "soak_7d": soak["soak_7d"],
            "soak_verified": soak["soak_verified"],
            "soak_basis": soak["soak_basis"],
            "note": ("a reached threshold is not SOAK_VERIFIED: \"she really lived a day\" is a "
                     "reviewer's judgement over the journal, the coverage ledger and the owners' "
                     "results, never arithmetic over two clocks"),
        },
        "quiet": quiet,
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
            "a reached threshold is not SOAK_VERIFIED: a day of life is a reviewer's judgement, "
            "not a clock difference, and an injected clock proves nothing about a real run",
            "a gap between two journal wake records is not downtime: the runner merges quiet "
            "and not-due windows into one silent step",
            "zero owner output and zero requests are not evidence that she was not alive, and "
            "silence is never turned into a choice to rest",
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
    parser.add_argument("--supply-socket",
                        help="the Life Supply owner's own Unix socket for prepare")
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
    parser.add_argument("--max-cognition-calls-per-day", type=int,
                        default=DEFAULT_MAX_COGNITION_CALLS_PER_DAY)
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
    # ``--create-private-items`` is declared once, together with the other prepare flags above.
    parser.add_argument("--memory-db", help="the M0 evidence database, when there is one")
    parser.add_argument("--memory-controls-db")
    parser.add_argument("--memory-subject")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--unclear",
                        help="record that this page was not understood (an unclear marker)")
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
                             max_cognition_calls_per_day=args.max_cognition_calls_per_day,
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