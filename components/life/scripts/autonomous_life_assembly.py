#!/usr/bin/env python3
"""B8-2: assemble the autonomous chain onto an *existing* Life runtime.

The problem
-----------
B7 and B8-1 built an ``AutonomousLifeCycle``, an ``AutonomousLifeRunner`` and three
event feeds, and the live plugin assembled none of them: it only built
``ProductionLifeRuntime``.  So ``run-once`` had no real handle, and the event door
had no caller in a real instance.

What this module does, and the rules it obeys
---------------------------------------------
* It **reuses the runtime that already holds the canonical owner lease**.  It never
  builds a second ``ProductionLifeRuntime`` for the same state root -- two of those
  would be two canonical writers, which is exactly what must not happen.  The lease
  the cycle uses is the one the supplied runtime already holds, so the existing
  Activity/Integration exclusivity *is* the mutual exclusion; nothing extra is
  invented unless that turns out to be insufficient.
* It resolves ``supply socket``, ``subject`` and the artifact grant from the
  instance's own configuration.  It never hardcodes one and never invents a
  permission.
* When a required piece is missing it returns ``BLOCKED`` **with the specific
  reason**.  It never returns a fake success, and it never degrades into
  ``NO_CYCLE``-as-OK.
* Autonomous execution stays **off** until the operator enables it.  Chat, Shadow
  and the read-only projection are untouched by anything here.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

BLOCKED = "BLOCKED"
READY = "READY"

#: Accepted environment names for the three facts that must come from the profile.
#: More than one name is accepted because the plugin, the setup tool and the
#: isolated harnesses each already spell them slightly differently; accepting all
#: three is cheaper than renaming a shipped configuration.
SUPPLY_SOCKET_ENV = ("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "LIFE_SUPPLY_SOCKET",
                     "CHIYO_LIFE_SUPPLY_SOCKET")
SUPPLY_SUBJECT_ENV = ("CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT", "NYA02_SUBJECT",
                      "CHIYO_LIFE_SUPPLY_SUBJECT")
ARTIFACT_GRANT_ENV = ("CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT", "NYA02_ARTIFACT_GRANT",
                      "LIFE_SUPPLY_ARTIFACT_GRANT")


def _first(environment: Mapping[str, str], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value
    return None


def load_model_env_file(environment: Any = None, *, path: Optional[str] = None) -> dict[str, Any]:
    """Fill ``NYA02_MODEL_*`` from a 0600 file, so the key never sits on a command line.

    A key handed over as ``env VAR=... python3`` is world-readable in ``ps``; a 0600 file the
    service identity owns is not.  A value already present in the environment wins, so an
    operator who exported one deliberately is never overridden.
    """

    env = os.environ if environment is None else environment
    target = path or str(env.get("NYA02_MODEL_ENV_FILE") or "").strip()
    if not target:
        return {}
    try:
        text = Path(target).read_text(encoding="utf-8")
    except OSError:
        return {}
    loaded: dict[str, Any] = {}
    for line in text.splitlines():
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name.startswith("NYA02_MODEL_") and value and not env.get(name):
            env[name] = value
            loaded[name] = True
    return loaded


def resolve_supply_configuration(environment: Mapping[str, str]) -> dict[str, Any]:
    """The three facts, or a named list of what is missing."""

    socket = _first(environment, SUPPLY_SOCKET_ENV)
    subject = _first(environment, SUPPLY_SUBJECT_ENV)
    grant = _first(environment, ARTIFACT_GRANT_ENV)
    missing = []
    if not socket:
        missing.append(SUPPLY_SOCKET_ENV[0])
    if not subject:
        missing.append(SUPPLY_SUBJECT_ENV[0])
    if not grant:
        missing.append(ARTIFACT_GRANT_ENV[0])
    return {"supply_socket": socket, "subject_id": subject, "artifact_grant_id": grant,
            "missing": missing}


#: The World's own configuration names.  A profile that carries neither of these has no World
#: service, and the feed is then recorded UNAVAILABLE instead of being invented.
WORLD_SOCKET_ENV = "WORLD_BODY_SOCKET"
WORLD_BRIDGE_DIR_ENV = "NYAIRO_WORLD_BRIDGE_DIR"


#: W05R: the hard *request* ceilings.  These count every HTTP attempt -- a decision, a
#: content generation and a schema retry -- against both a UTC day and one run window.
ENV_REQUEST_UTC_DAY_MAX = "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"
ENV_REQUEST_RUN_WINDOW_MAX = "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"
ENV_RUN_ID = "CHIYO_AUTONOMOUS_RUN_ID"
ENV_NEW_RUN_WINDOW = "CHIYO_AUTONOMOUS_NEW_RUN_WINDOW"


def _int_env(environment: Mapping[str, str], name: str) -> Optional[int]:
    raw = str(environment.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def request_budget(environment: Mapping[str, str], state_root: Path | str) -> Any:
    """The persisted request budget for this instance, or ``None`` when unconfigured.

    ``None`` means "this profile sets no request ceiling at all", which is a *stated*
    configuration rather than a silent fallback: the one-day entry always writes both
    ceilings into the instance, so a real run cannot end up here by accident.

    A window is opened the first time an identity is seen, and a plain restart carrying
    the same identity continues it.  A *new* identity is refused unless the caller
    explicitly asked for a new window, so restarting cannot mint a fresh allowance.
    """

    from life_request_budget import LifeRequestBudget

    day_max = _int_env(environment, ENV_REQUEST_UTC_DAY_MAX)
    window_max = _int_env(environment, ENV_REQUEST_RUN_WINDOW_MAX)
    if day_max is None and window_max is None:
        return None
    run_id = str(environment.get(ENV_RUN_ID) or "").strip() or (
        "run:" + hashlib.sha256(str(state_root).encode("utf-8")).hexdigest()[:16])
    budget = LifeRequestBudget(Path(state_root), utc_day_max=day_max,
                               run_window_max=window_max, run_id=run_id)
    allow_new = str(environment.get(ENV_NEW_RUN_WINDOW) or "").strip().lower() in {
        "1", "true", "yes", "on"}
    budget.ensure_window(allow_new=allow_new)
    return budget


#: D (life-v1): the explicit work binding and the World/Body read seam.  The work artifact is
#: *named*, never picked by recency: a private-items document (or any other newer artifact)
#: must never be mistaken for the work being continued.
WORK_ARTIFACT_ENV = "CHIYO_LIFE_WORK_ARTIFACT_ID"
READING_ARTIFACT_ENV = "CHIYO_LIFE_READING_ARTIFACT_ID"
PRIVATE_ITEMS_ARTIFACT_ENV = "CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID"
WORLD_HOME_ENV = ("CHIYO_WORLD_HOME", "HERMES_HOME")


def resolve_artifact_bindings(environment: Mapping[str, str]) -> dict[str, Optional[str]]:
    """The three explicitly bound artifacts of this instance (work / reading / private items)."""

    def _get(name: str) -> Optional[str]:
        value = str(environment.get(name) or "").strip()
        return value or None

    return {"work_artifact_id": _get(WORK_ARTIFACT_ENV),
            "reading_artifact_id": _get(READING_ARTIFACT_ENV),
            "private_items_artifact_id": _get(PRIVATE_ITEMS_ARTIFACT_ENV)}


def resolve_work_artifact_id(environment: Mapping[str, str]) -> Optional[str]:
    """The artifact this instance continues, or ``None`` (then the port picks the newest)."""

    value = str(environment.get(WORK_ARTIFACT_ENV) or "").strip()
    return value or None


def _load_module_by_path(name: str, path: Path) -> Any:
    """Load one module from an explicit file, for the copies that are not on ``sys.path``."""

    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s from %s" % (name, path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def resolve_body_port(environment: Mapping[str, str]) -> dict[str, Any]:
    """The real World/Body read port, or a named reason there is none.

    ``READY`` means a real ``WorldBodyReadPort`` was built over the instance's own gateway
    socket.  Everything else returns no port plus a reason, and A's situation view then
    reports the body column UNKNOWN -- it never invents a healthy body.
    """

    env = dict(environment or {})
    socket_path = str(env.get(WORLD_SOCKET_ENV) or "").strip()
    if not socket_path:
        return {"port": None, "status": "UNAVAILABLE", "reason": "BODY_SEAM_NOT_CONFIGURED",
                "detail": "no %s in this profile" % WORLD_SOCKET_ENV}
    try:
        here = str(Path(__file__).resolve().parent)
        bridge = (str(env.get(WORLD_BRIDGE_DIR_ENV) or "").strip()
                  or str(Path(__file__).resolve().parents[2] / "world" / "service" / "bridge"))
        integration = str(Path(__file__).resolve().parents[2] / "native" / "integration")
        for entry in (here, bridge, integration):
            if entry and entry not in sys.path:
                sys.path.insert(0, entry)
        import world_body_client as wbc

        try:
            from world_body_read_port import WorldBodyReadPort
        except ImportError:
            WorldBodyReadPort = _load_module_by_path(
                "assembly_world_body_read_port",
                Path(integration) / "world_body_read_port.py").WorldBodyReadPort
        client = wbc.WorldBodyClient(socket_path=socket_path, mode=wbc.MODE_CANONICAL,
                                     read_retries=0)
    except Exception as exc:
        return {"port": None, "status": "UNAVAILABLE", "reason": "BODY_PORT_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"port": WorldBodyReadPort(client), "status": "READY", "reason": "",
            "socket": socket_path}


def resolve_situation_source(environment: Mapping[str, str], *, home: Any = None) -> dict[str, Any]:
    """A's situation reader (``life_situation_view.LifeSituationView``), or a named reason.

    The subject is the *canonical* Life subject (the Body is the global subject's body); the
    Supply workspace subject is a different concept and is deliberately not passed here.
    """

    try:
        import life_situation_view as lsv
    except Exception as exc:
        return {"source": None, "status": "UNAVAILABLE", "reason": "SITUATION_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    env = dict(environment or {})
    world_home = str(home or "").strip() or str(_first(env, WORLD_HOME_ENV) or "").strip()
    if not world_home or not Path(world_home).is_dir():
        return {"source": None, "status": "UNAVAILABLE", "reason": "WORLD_HOME_NOT_BOUND",
                "detail": "no %s directory in this profile" % WORLD_HOME_ENV[0]}
    body = resolve_body_port(env)
    try:
        source = lsv.LifeSituationView(home=world_home, body_port=body["port"])
    except Exception as exc:
        return {"source": None, "status": "UNAVAILABLE", "reason": "SITUATION_WIRING_REFUSED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"source": source, "status": "READY", "reason": "", "home": world_home,
            "body_seam": body["status"], "body_seam_reason": body["reason"]}


def resolve_world_provider(environment: Mapping[str, str], *,
                           bridge_dir: Optional[str] = None) -> dict[str, Any]:
    """The World observation seam, or a named reason there is none.

    ``READY`` means a real provider was built *and* the World answered a bounded status read.
    Everything else returns no provider plus a reason, so the caller records the feed as
    ``UNAVAILABLE`` rather than pretending the World was consulted.  Nothing here guesses an
    opportunity: an unreadable World yields nothing at all.
    """

    env = dict(environment or {})
    configured = bool(str(env.get(WORLD_SOCKET_ENV) or "").strip()
                      or str(env.get(WORLD_BRIDGE_DIR_ENV) or "").strip()
                      or bridge_dir)
    if not configured:
        return {"provider": None, "status": "UNAVAILABLE", "reason": "WORLD_NOT_CONFIGURED",
                "detail": "no %s and no %s in this profile"
                          % (WORLD_SOCKET_ENV, WORLD_BRIDGE_DIR_ENV)}
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from world_action_bridge_adapter import WorldBridgeResolver, discover_bridge_dir
        import world_move_opportunity_source as wmos
    except Exception as exc:
        return {"provider": None, "status": "UNAVAILABLE",
                "reason": "WORLD_ADAPTER_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}

    directory = (bridge_dir or str(env.get(WORLD_BRIDGE_DIR_ENV) or "").strip()
                 or discover_bridge_dir())
    if not directory:
        return {"provider": None, "status": "UNAVAILABLE", "reason": "WORLD_BRIDGE_NOT_FOUND",
                "detail": "set %s to <repo>/components/world/service/bridge"
                          % WORLD_BRIDGE_DIR_ENV}
    text = str(directory)
    try:
        if text not in sys.path:
            sys.path.insert(0, text)
        import action_intent as world_action_intent
        import world_body_client as wbc
        client = WorldBridgeResolver(environment=env, bridge_dir=directory).client
        status = client.get_status()
    except Exception as exc:
        return {"provider": None, "status": "UNAVAILABLE", "reason": "WORLD_UNREACHABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc), "bridge_dir": text}

    ready = (getattr(status, "outcome", None) == wbc.OK
             and bool((getattr(status, "data", None) or {}).get("ready")))
    if not ready:
        return {"provider": None, "status": "UNAVAILABLE", "reason": "WORLD_NOT_READY",
                "detail": "status=%s" % (getattr(status, "outcome", None),),
                "bridge_dir": text}
    provider = wmos.WorldMoveOpportunityProvider(
        client=client, action_intent=world_action_intent, bridge_dir=text)
    return {"provider": provider, "status": "READY", "reason": "", "bridge_dir": text}


def _model_config(environment: Mapping[str, str]) -> Any:
    """The instance's own provider, resolved once for both the decision and the writing."""

    try:
        from profile_model_config import describe_model_config, load_configured_model_config
    except Exception:
        return None
    config = None
    base_url = str(environment.get("NYA02_MODEL_BASE_URL") or "").strip()
    model = str(environment.get("NYA02_MODEL_NAME") or "").strip()
    key = str(environment.get("NYA02_MODEL_API_KEY") or "").strip()
    if base_url and model and key:
        config = {"provider": environment.get("NYA02_MODEL_PROVIDER") or "custom",
                  "model": model, "base_url": base_url.rstrip("/"), "api_key": key,
                  "key_env": "NYA02_MODEL_API_KEY", "api_key_present": True}
    if config is None:
        try:
            config = load_configured_model_config(
                hermes_home=environment.get("NYA02_PROFILE_HOME")
                or environment.get("CHIYO_PROFILE_HOME"))
        except Exception:
            config = None
    if not config or not describe_model_config(config).get("usable"):
        return None
    return config


def pause_request_admission(state_root: Path | str) -> Any:
    """W05R-R1c: the pause send guard bound to one instance's pause bit.

    It is handed to the real transport (which holds the shared confirmation lock across the
    finite *send phase* of every decision, schema retry and content request, and releases it
    before waiting for the response) and to the transport wrappers (which require that
    capability, and refuse the send when a transport cannot provide it).
    """

    import life_pause

    return life_pause.RequestSendGuard(state_root)


def _model_writer(environment: Mapping[str, str], *, timeout_s: float = 30.0,
                  requests: Any = None, admission: Any = None,
                  pause_guard: Any = None) -> Any:
    """The real content writer, over the instance's own provider.

    The writer is the one place a model produces text for a work, so it is built from the
    same profile resolution the rest of the instance uses.  A profile that cannot
    be resolved yields ``None`` and the caller blocks -- it never substitutes a
    deterministic writer, because that would be fabricated content.
    """

    try:
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient
        from model_continuation_writer import ModelContinuationWriter
    except Exception:
        return None
    config = _model_config(environment)
    if config is None:
        return None
    transport = OpenAICompatibleTransport(
        base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
        provider=config.get("provider") or "custom", timeout_s=timeout_s,
        purpose="artifact_edit", request_json_object=True, pause_guard=pause_guard)
    client = AgencyModelClient(config=config, transport=transport, purpose="artifact_edit",
                               timeout_s=timeout_s, max_output_tokens=512)
    if requests is not None:
        # W05R: the reservation happens here, at the HTTP boundary, *before* the request
        # leaves.  A refusal raises and the round is reported as a budget refusal.
        from life_request_budget import BudgetedModelClient

        client = BudgetedModelClient(client, requests, admission=admission)
    return ModelContinuationWriter(client, max_new_chars=300)


def model_cognition(environment: Mapping[str, str], *, timeout_s: float = 30.0,
                    requests: Any = None, admission: Any = None,
                    pause_guard: Any = None) -> Any:
    """The real decision adapter over the same provider, or ``None``.

    The decision is the one place a model may speak, so it is built from the same profile
    resolution the content writer uses.  A profile with no usable provider yields ``None``
    and the caller blocks; nothing here substitutes a stand-in, because a fabricated
    decision is not a decision.
    """

    try:
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient
        from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    except Exception:
        return None
    config = _model_config(environment)
    if config is None:
        return None
    transport = OpenAICompatibleTransport(
        base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
        provider=config.get("provider") or "custom", timeout_s=timeout_s,
        purpose="agency_cognition", request_json_object=True, pause_guard=pause_guard)
    client = AgencyModelClient(config=config, transport=transport,
                               purpose="agency_cognition", timeout_s=timeout_s,
                               max_output_tokens=512)
    adapter = ProductionAgencyCognitionAdapter(client=client, timeout_s=timeout_s,
                                               max_output_tokens=512)
    if requests is not None or admission is not None:
        # W05R: every cognition attempt -- the decision and each schema retry -- is
        # reserved against the persisted ledger before the HTTP request is made; W05R-R1b
        # adds the pause request-start handshake at that same boundary.
        from life_request_budget import BudgetedCognitionAdapter

        if requests is None:
            return adapter
        return BudgetedCognitionAdapter(adapter, requests, admission=admission)
    return adapter


def assemble_autonomous_life(
    runtime: Any,
    *,
    environment: Optional[Mapping[str, str]] = None,
    state_root: Optional[Path | str] = None,
    experience_provider: Any = None,
    world_provider: Any = None,
    writer: Any = None,
    requests: Any = None,
    pause_admission: Any = None,
    enabled: bool = False,
    situation_source: Any = None,
    continuity_source: Any = None,
    appraisal_source: Any = None,
    work_artifact_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the cycle, the runner and the feed poller on top of ``runtime``.

    Returns ``{"state": READY, ...}`` or ``{"state": BLOCKED, "reason": ..., ...}``.
    Never raises for a missing configuration, and never fabricates one.
    """

    env = dict(os.environ if environment is None else environment)
    if runtime is None:
        return {"state": BLOCKED, "reason": "RUNTIME_UNAVAILABLE",
                "detail": "the Life runtime was not loaded, so nothing can be assembled"}
    lease = getattr(runtime, "lease", None)
    if lease is None or not getattr(lease, "held", False):
        # The supplied runtime must be the one holding the canonical writer lease.
        # Assembling on top of a runtime that does not hold it would create a second
        # writer for the same state root.
        return {"state": BLOCKED, "reason": "NO_CANONICAL_LEASE",
                "detail": "the runtime does not hold the canonical writer lease"}

    supply = resolve_supply_configuration(env)
    if supply["missing"]:
        return {"state": BLOCKED, "reason": "SUPPLY_CONFIGURATION_MISSING",
                "missing": list(supply["missing"]),
                "detail": "no autonomous execution is possible without the Supply "
                          "socket, the subject and a real artifact grant"}

    if state_root is not None:
        # The caller passed the Life *state* root itself (the plugin passes
        # INSTALL_ROOT/"state"); appending another "state" would nest it twice and
        # silently split the switch and the budget from the ones the CLI reads.
        root = Path(state_root)
    else:
        root = Path(env.get("CHIYO_LIFE_INSTALL_ROOT") or "./data/life") / "state"
    #: W05R-R1b: every real request attempt is admitted at its send boundary against the
    #: instance's own pause bit (the same root the operator's ``pause`` writes).
    admission = (pause_admission if pause_admission is not None
                 else pause_request_admission(root))
    try:
        import sys

        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import autonomous_life_cycle as alc
        import autonomous_life_events as events
        import autonomous_life_runner as alr
        from life_artifact_port import LifeArtifactPort
        from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter
    except Exception as exc:
        return {"state": BLOCKED, "reason": "ASSEMBLY_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}

    if writer is None:
        writer = _model_writer(env, requests=requests, admission=admission,
                               pause_guard=admission)
    if writer is None:
        return {"state": BLOCKED, "reason": "NO_CONTENT_WRITER",
                "detail": "the profile has no usable provider for the content writer"}

    port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    artifact_adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))

    # D (life-v1): the two injected readers and the explicit work binding.
    #   * ``situation`` is A's view: the Body column comes from the instance's own gateway
    #     socket, and with no socket it is UNKNOWN rather than healthy.
    #   * ``continuity`` is C's read-only view; the resident builds it because it is the
    #     process that already holds the Memory boundary port.
    #   * the work artifact is *named* (``CHIYO_LIFE_WORK_ARTIFACT_ID``), so a private-items
    #     document cannot be selected as if it were the work.
    work_artifact = ((str(work_artifact_id).strip() if work_artifact_id else "")
                     or resolve_work_artifact_id(env) or None)
    bindings = resolve_artifact_bindings(env)
    situation = ({"source": situation_source, "status": "READY", "reason": "",
                  "detail": "supplied by the caller"}
                 if situation_source is not None
                 else resolve_situation_source(env, home=env.get("CHIYO_WORLD_HOME")))
    situation_source = situation["source"]
    situation_record = {k: v for k, v in situation.items() if k != "source"}

    cycle = alc.AutonomousLifeCycle(
        runtime,
        artifact_port=port,
        subject_id=supply["subject_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"),
        artifact_grant_id=supply["artifact_grant_id"],
        writer=writer,
        experience_provider=experience_provider,
        world_opportunity_provider=world_provider,
        # The chain is *capable* once it is assembled; whether it actually runs is the
        # runner's operator switch, which is OFF by default.  Leaving the cycle's own
        # master switch off would make the persisted operator switch unable to turn
        # anything on, which is a different bug wearing the same clothes.
        enabled=True,
        env=env,
        requests=requests,
        situation_source=situation_source,
        continuity_source=continuity_source,
        appraisal_source=appraisal_source,
        work_artifact_id=work_artifact,
        reading_artifact_id=bindings["reading_artifact_id"],
        private_items_artifact_id=bindings["private_items_artifact_id"],
    )
    # The World feed is assembled only when a real observation seam exists.  Without one the
    # cycle must not consult the World at all -- that is the behaviour every pre-B6 profile
    # keeps -- and a profile with no World service must never be handed a guessed opportunity.
    world = ({"provider": world_provider, "status": "READY", "reason": "",
              "detail": "supplied by the caller"}
             if world_provider is not None else resolve_world_provider(env))
    world_provider = world["provider"]

    # The artifact feed is built before the poller so the cycle can be told about its own
    # confirmed writes.  Without that, a head we just wrote looks like somebody else's change on
    # the next poll, and one autonomous write becomes the next autonomous wake.
    artifact_feed = events.ArtifactChangeFeed(port, root, subject_id=supply["subject_id"])
    cycle.own_write_recorder = artifact_feed.note_own_write
    # The record must stay serializable: the live provider is an object, so it is handed back on
    # its own key and never inside the descriptive dict.
    world_record = {k: v for k, v in world.items() if k != "provider"}

    runner = alr.AutonomousLifeRunner(cycle, state_root=root, env=env,
                                      enabled=bool(enabled))
    feeds = [
        events.ResumeEligibilityFeed(getattr(runtime, "waiting_coordinator", None), root)
        if getattr(runtime, "waiting_coordinator", None) is not None else None,
        artifact_feed,
    ]
    if world_provider is not None:
        feeds.append(events.WorldOpportunityFeed(world_provider, root))
    poller = events.EventFeedPoller(feeds)
    poller.feeds = [feed for feed in poller.feeds if feed is not None]
    return {
        "state": READY,
        "cycle": cycle,
        "runner": runner,
        "feed_poller": poller,
        "artifact_port": port,
        "artifact_action_adapter": artifact_adapter,
        "subject_id": supply["subject_id"],
        "artifact_grant_id": supply["artifact_grant_id"],
        "state_root": str(root),
        "enabled": runner.enabled,
        "world": world_record,
        "world_provider": world_provider,
        "feeds": [getattr(feed, "name", type(feed).__name__) for feed in poller.feeds],
        "lease_instance": getattr(lease, "instance_id", None),
        "request_budget": (requests.view() if requests is not None else None),
        # D (life-v1): what was actually wired, so a reader can tell "no body seam" from
        # "the body said nothing".  No reader object is handed back.
        "work_artifact_id": work_artifact,
        "situation": situation_record,
        "continuity_wired": continuity_source is not None,
        "appraisal_wired": appraisal_source is not None,
    }


def runner_tick(assembled: Mapping[str, Any], *, act: bool = True) -> dict[str, Any]:
    """One bounded step of the resident runner.

    Poll the real feeds, hand any fresh event to the runner, and run the check window
    if it is due.  It is deliberately a *step* and not a loop: this batch adds no
    scheduler and no background thread, so the host drives it (the plugin's own turn
    hook, or ``run-once`` from the CLI).
    """

    if assembled.get("state") != READY:
        return {"ok": False, "reason": assembled.get("reason", BLOCKED),
                "detail": assembled.get("detail")}
    runner = assembled["runner"]
    poller = assembled.get("feed_poller")
    events = poller.wake(runner, act=act) if poller is not None else []
    due = runner.run_due(act=act)
    return {
        "ok": True,
        "events": [record.to_dict() for record in events],
        "feed_errors": list(getattr(poller, "last_errors", []) or []),
        "due": due.to_dict(),
    }


def run_once(assembled: Mapping[str, Any], *, act: bool = True) -> dict[str, Any]:
    """The CLI's ``run-once``: one real consideration window, or a named refusal."""

    if assembled.get("state") != READY:
        return {"ok": False, "reason": assembled.get("reason", BLOCKED),
                "detail": assembled.get("detail"), "missing": assembled.get("missing")}
    record = assembled["runner"].run_due(act=act)
    return {"ok": True, "wake": record.to_dict()}


__all__ = [
    "ARTIFACT_GRANT_ENV",
    "BLOCKED",
    "READY",
    "SUPPLY_SOCKET_ENV",
    "SUPPLY_SUBJECT_ENV",
    "WORLD_BRIDGE_DIR_ENV",
    "WORLD_SOCKET_ENV",
    "assemble_autonomous_life",
    "model_cognition",
    "resolve_world_provider",
    "resolve_supply_configuration",
    "run_once",
    "runner_tick",
    "ENV_REQUEST_RUN_WINDOW_MAX",
    "ENV_REQUEST_UTC_DAY_MAX",
    "ENV_RUN_ID",
    "request_budget",
    "resolve_situation_source",
    "resolve_body_port",
    "resolve_work_artifact_id",
    "WORK_ARTIFACT_ENV",
    "WORLD_HOME_ENV",
]
