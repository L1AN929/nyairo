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
import inspect
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


def resolve_research_service(port: Any, environment: Mapping[str, str]) -> dict[str, Any]:
    """B's ``ResearchWorkspaceService`` over *this* instance's owner port, or a named reason.

    ``READY`` means the real service object was built by B's own factory
    (``research_from_env``) from the same environment the private-project service uses.  Every
    other answer carries no service at all, so the cycle offers no research option and creates
    nothing -- there is deliberately no stand-in store, because a local copy of the research
    library would be a second place where "her work" could live.
    """

    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import research_work_context as rwc
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE", "reason": "RESEARCH_SEAM_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    module = rwc.research_module()
    if module is None:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "RESEARCH_SERVICE_NOT_IN_TREE",
                "detail": "no research_workspace_service module on the life scripts path"}
    factory = getattr(module, "research_from_env", None)
    if not callable(factory):
        return {"service": None, "status": "UNAVAILABLE", "reason": "RESEARCH_FACTORY_MISSING",
                "detail": "research_workspace_service.research_from_env is absent"}
    env = dict(environment or {})
    # The two non-project artifacts this instance already binds.  The research workspace reads its
    # own names for them, so they are handed over here (compose only -- the meaning stays B's): the
    # reading material is listed as *read-only material* and the private-items document is never
    # scanned as either material or research.
    bindings = resolve_artifact_bindings(env)
    if bindings.get("reading_artifact_id"):
        env.setdefault("NYAIRO_LIFE_READING_ARTIFACT_ID", str(bindings["reading_artifact_id"]))
    if bindings.get("private_items_artifact_id"):
        env.setdefault("NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                       str(bindings["private_items_artifact_id"]))
    try:
        # The documented signature is ``research_from_env(port, env)``; a factory that only takes
        # the environment is accepted too, so the seam never guesses wrong about the arity.
        positional = 0
        try:
            positional = len([p for p in inspect.signature(factory).parameters.values()
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
        except (TypeError, ValueError):
            positional = 2
        service = factory(port, env) if positional >= 2 else factory(env)
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE", "reason": "RESEARCH_FROM_ENV_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if service is None:
        return {"service": None, "status": "UNAVAILABLE", "reason": "RESEARCH_FROM_ENV_REFUSED",
                "detail": "the research factory answered with no service"}
    return {"service": service, "status": "READY", "reason": "",
            "service_type": type(service).__name__}

def resolve_practice_service(port: Any, environment: Mapping[str, str]) -> dict[str, Any]:
    """B's ``PersonalPracticeService`` over *this* instance's owner port, or a named reason.

    ``READY`` means the real service object was built by B's own factory (``practice_from_env``)
    from the same environment the private-project service uses -- which is also where A's Memory
    service comes from, so a review can only ever cite experiences this instance can recall.
    Every other answer carries no service at all: no review option is offered, no method is named
    and nothing can be written.  There is deliberately no stand-in workspace.
    """

    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import life_practice_context as lpc
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE", "reason": "PRACTICE_SEAM_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    module = lpc.practice_module()
    if module is None:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "PRACTICE_SERVICE_NOT_IN_TREE",
                "detail": "no personal_practice_service module on the life scripts path"}
    factory = getattr(module, "practice_from_env", None)
    if not callable(factory):
        return {"service": None, "status": "UNAVAILABLE", "reason": "PRACTICE_FACTORY_MISSING",
                "detail": "personal_practice_service.practice_from_env is absent"}
    env = dict(environment or {})
    try:
        # The documented signature is ``practice_from_env(port, env)``; a factory that only takes
        # the environment is accepted too, so the seam never guesses wrong about the arity.
        positional = 0
        try:
            positional = len([p for p in inspect.signature(factory).parameters.values()
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
        except (TypeError, ValueError):
            positional = 2
        service = factory(port, env) if positional >= 2 else factory(env)
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE", "reason": "PRACTICE_FROM_ENV_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if service is None:
        return {"service": None, "status": "UNAVAILABLE", "reason": "PRACTICE_FROM_ENV_REFUSED",
                "detail": "the practice factory answered with no service"}
    return {"service": service, "status": "READY", "reason": "",
            "service_type": type(service).__name__}



def resolve_sharing_service(environment: Mapping[str, str], *, subject_id: Any = None,
                            grant_id: Any = None) -> dict[str, Any]:
    """B's ``LifeSharingService`` (`sharing_from_env`), or a named reason there is none.

    ``READY`` means B's own factory built the service over the *same* owner bindings the
    private-project service uses, so a share is an artifact of this instance and its local inbox
    is this instance's inbox.  Every other answer carries no service at all: no draft is offered
    and nothing can be delivered, because a stand-in inbox would be a second owner.

    The three bindings (socket / workspace / subject + artifact grant) come from the environment,
    which is where the instance records them.  ``subject_id`` / ``grant_id`` are the caller's own
    facts for the same instance and only fill a spelling the environment does not carry -- the
    owner's own env names are used, never a new one.
    """

    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import life_sharing_context as lsc
    except Exception as exc:  # noqa: BLE001 - a seam that cannot be built is reported
        return {"service": None, "status": "UNAVAILABLE", "reason": "SHARING_SEAM_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    env = {str(key): str(value) for key, value in (environment or {}).items()}
    if subject_id and not any(env.get(name) for name in
                              ("NYAIRO_LIFE_SUBJECT", "CHIYO_LIFE_SUPPLY_SUBJECT",
                               "LIFE_SUPPLY_SUBJECT")):
        env["NYAIRO_LIFE_SUBJECT"] = str(subject_id)
    if grant_id and not any(env.get(name) for name in
                            ("NYAIRO_ARTIFACT_GRANT", "NYA02_ARTIFACT_GRANT",
                             "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT", "LIFE_SUPPLY_ARTIFACT_GRANT")):
        env["NYAIRO_ARTIFACT_GRANT"] = str(grant_id)
    service, reason = lsc.service_from_env(env)
    if service is None:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": reason or "SHARING_SERVICE_UNAVAILABLE", "detail": ""}
    return {"service": service, "status": "READY", "reason": "",
            "service_type": type(service).__name__}


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
        client=client, action_intent=world_action_intent, bridge_dir=text,
        environment=env, world_home=_first(env, WORLD_HOME_ENV))
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


def _practice_responder(environment: Mapping[str, str], *, timeout_s: float = 30.0,
                        requests: Any = None, admission: Any = None,
                        pause_guard: Any = None) -> Any:
    """The shared-practice content responder, over the instance's own provider, or ``None``.

    A content step is a real request about private material, so it is built from the *same*
    profile resolution, the same transport (with the same pause send guard) and the same request
    ledger as the other writers: one reservation per call, at the HTTP boundary.  A profile that
    cannot be resolved yields ``None`` -- no deterministic stand-in is ever used for her words.
    """

    try:
        import shared_practice_life as spl
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient
    except Exception:
        return None
    config = _model_config(environment)
    if config is None:
        return None
    transport = OpenAICompatibleTransport(
        base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
        provider=config.get("provider") or "custom", timeout_s=timeout_s,
        purpose="shared_practice", request_json_object=True, pause_guard=pause_guard)
    client = AgencyModelClient(config=config, transport=transport, purpose="shared_practice",
                               timeout_s=timeout_s, max_output_tokens=700)
    if requests is not None:
        from life_request_budget import BudgetedModelClient

        client = BudgetedModelClient(client, requests, admission=admission)
    return spl.ModelPracticeResponder(client, max_output_tokens=700)


def _model_reading_response(environment: Mapping[str, str], *, timeout_s: float = 30.0,
                            requests: Any = None, admission: Any = None,
                            pause_guard: Any = None) -> Any:
    """The private page-response writer (A's ``ModelReadingResponse``), or ``None``.

    It is built over the *same* profile resolution, the same transport (with the same pause send
    guard) and the same request ledger as the work writer, because a page response is a real
    model request about private material and must be admitted, bounded and budgeted exactly like
    one.  With no usable provider -- or while A's module is not present -- this returns ``None``
    and the cycle keeps its previous behaviour and reports ``NOT_CONFIGURED``: no stand-in
    answers, because an invented note would be a private work the subject never wrote.
    """

    try:
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient
        from subject_reading_response import ModelReadingResponse
    except Exception:
        return None
    config = _model_config(environment)
    if config is None:
        return None
    transport = OpenAICompatibleTransport(
        base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
        provider=config.get("provider") or "custom", timeout_s=timeout_s,
        purpose="reading_response", request_json_object=True, pause_guard=pause_guard)
    client = AgencyModelClient(config=config, transport=transport, purpose="reading_response",
                               timeout_s=timeout_s, max_output_tokens=512)
    if requests is not None:
        from life_request_budget import BudgetedModelClient

        client = BudgetedModelClient(client, requests, admission=admission)
    return ModelReadingResponse(client)


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


def resolve_shared_practice(runtime: Any, *, cycle: Any, env: Mapping[str, str],
                           subject_id: Any, inbox: Any = None,
                           admission: Any = None, requests: Any = None) -> dict[str, Any]:
    """v0.4 B: A's shared-practice service over *this* instance's own owner port, or a reason.

    The two A services are built by A's own factory from the same workspace/grant/subject
    bindings the private-project service already speaks; the subject half is bound to a source
    verifier made of this instance's *own* read surfaces (A's basis kinds), so a subject-side
    write can never be bound to a source somebody made up.

    ``status="READY"`` means the real service object exists.  Anything else names what is
    missing: a half-wired content half is never presented as a working one.
    """

    try:
        import shared_practice_life as spl
        import shared_practice_service as sps
        from life_artifact_port import LifeArtifactPort
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "SHARED_PRACTICE_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    subject = str(subject_id or "").strip()
    if not subject:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "SHARED_PRACTICE_SUBJECT_REQUIRED",
                "detail": "the instance has no Supply subject, so no practice can be bound"}
    supply = resolve_supply_configuration(env)
    if supply["missing"]:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "SUPPLY_CONFIGURATION_MISSING",
                "missing": list(supply["missing"]),
                "detail": "no owner port, so no shared practice"}
    try:
        port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    except Exception as exc:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "SHARED_PRACTICE_PORT_REFUSED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    verifier = spl.LifeSourceVerifier(
        inbox=inbox,
        decision_reader=getattr(runtime, "decision_read", None),
        action_reader=getattr(runtime, "action_read", None),
        adoption_reader=getattr(runtime, "integration_read", None))
    # A's factory resolves its bindings *by name*.  This instance already knows all three facts
    # -- they are exactly what the Supply port was opened with -- but the one-day profile spells
    # the subject ``NYA02_SUBJECT`` / ``CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT``, names A never looks
    # for.  So the owner-side facts are handed over under A's own names instead of hoping a
    # profile happens to use them: a content half that silently degrades to UNAVAILABLE on every
    # real profile is the same as having none.
    practice_env = dict(env)
    practice_env["NYAIRO_LIFE_SUBJECT"] = subject
    if supply.get("artifact_grant_id"):
        practice_env["NYAIRO_ARTIFACT_GRANT"] = str(supply["artifact_grant_id"])
    if supply.get("supply_socket"):
        practice_env["NYAIRO_LIFE_SUPPLY_SOCKET"] = str(supply["supply_socket"])
    workspace = _first(env, ("CHIYO_LIFE_SUPPLY_WORKSPACE_ID", "NYAIRO_LIFE_WORKSPACE_ID",
                             "LIFE_SUPPLY_WORKSPACE_ID"))
    if workspace:
        practice_env["NYAIRO_LIFE_WORKSPACE_ID"] = workspace
    subject_service = sps.shared_practice_service_from_env(
        port, practice_env, actor=sps.CALLER_LIFE, source_verifier=verifier)
    if subject_service is None:
        return {"service": None, "status": "UNAVAILABLE",
                "reason": "SHARED_PRACTICE_NOT_CONFIGURED",
                "detail": "the workspace / grant / subject bindings are incomplete"}
    person_service = sps.shared_practice_service_from_env(port, practice_env,
                                                          actor=sps.CALLER_USER)
    #: The content request shares the instance's *persistent* request ledger with AG-1 and the
    #: other writers (one object, one ledger), and it is admitted at the transport's send boundary:
    #: a bare request that skipped either would be a request nobody paid for.
    requests = requests if requests is not None else getattr(
        getattr(cycle, "budget", None), "requests", None)
    responder = _practice_responder(env, requests=requests, admission=admission,
                                    pause_guard=admission)
    service = spl.SharedPracticeLife(subject=subject_service, person=person_service,
                                     responder=responder)
    return {"service": service, "status": "READY", "reason": "",
            "model_ready": responder is not None,
            "person_half": person_service is not None,
            "source_verifier": verifier.describe()}


def resolve_local_contacts(runtime: Any, *, cycle: Any, state_root: Path | str, env: Mapping[str, str],
                          subject_id: Any, admission: Any = None,
                          requests: Any = None) -> dict[str, Any]:
    """v0.4 B: A's local-contact inbox, the co-presence protocol, and the resident's pass.

    All three are built from *this instance's* identity -- the Supply subject it already feeds
    FiniteCoPresence, and the home it already hands the World -- never from anything a client
    sent.  ``home_ref`` is the instance's ``CHIYO_WORLD_HOME`` when the profile has one and the
    Life state root otherwise; the inbox and the protocol are given the *same* string, because
    an invitation is only accepted when it names this home.

    A profile that cannot build the seam gets a named ``UNAVAILABLE`` record: no inbox, no
    protocol, no port.  There is deliberately no stand-in ledger.
    """
    try:
        import life_co_presence as cop
        import life_local_contact as lc
        import life_social_contacts as lsc
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": "CONTACT_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    subject = str(subject_id or "").strip()
    if not subject:
        return {"status": "UNAVAILABLE", "reason": "CONTACT_SUBJECT_REQUIRED",
                "detail": "the instance has no Supply subject, so no local contact can be bound"}
    home = _first(env, WORLD_HOME_ENV) or str(state_root)
    try:
        inbox = lc.LocalContactInbox(state_root, subject_id=subject, home_ref=home)
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": "CONTACT_INBOX_REFUSED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    protocol = cop.FiniteCoPresence(runtime, state_root=state_root, subject_id=subject,
                                    home=home, cycle=cycle)
    # v0.4 shared practice: the content half over this instance's own owner port.  It is wired
    # *before* the port so the port can hold it, and its status is reported either way.
    practice = resolve_shared_practice(runtime, cycle=cycle, env=env, subject_id=subject,
                                       inbox=inbox, admission=admission, requests=requests)
    port = lsc.SocialContactPort(runtime, state_root=state_root, subject_id=subject, home=home,
                                 inbox=inbox, protocol=protocol, cycle=cycle,
                                 practice=practice.get("service"))
    return {"status": "READY", "inbox": inbox, "protocol": protocol, "port": port,
            "subject_id": subject, "home_ref": home,
            "shared_practice": {key: value for key, value in practice.items()
                                if key != "service"},
            "ledger_path": str(getattr(inbox, "path", "") or "")}


def assemble_autonomous_life(
    runtime: Any,
    *,
    environment: Optional[Mapping[str, str]] = None,
    state_root: Optional[Path | str] = None,
    experience_provider: Any = None,
    world_provider: Any = None,
    writer: Any = None,
    reading_response_writer: Any = None,
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

    # rc1-v04: the private page response rides the same client, the same transport pause guard
    # and the same request ledger as the work writer.  ``None`` is a first-class answer: the
    # reading step then keeps its previous behaviour and reports ``NOT_CONFIGURED``.  It is
    # deliberately *not* a reason to block the assembly, because reading without an annotation
    # is still a real read.
    if reading_response_writer is None:
        reading_response_writer = _model_reading_response(
            env, requests=requests, admission=admission, pause_guard=admission)

    port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    artifact_adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))
    # v0.4 research: B's research workspace over the *same* owner port and the same env bindings
    # the private-project service uses.  ``None`` is a first-class answer: no research option is
    # offered and nothing can be created, because a stand-in store would be a second library.
    research = resolve_research_service(port, env)
    # v0.4 C: B's practice workspace (reviews + methods) over the *same* owner port and the same
    # env bindings.  ``None`` is a first-class answer: no review option is offered, no method is
    # named and nothing can be written, because a stand-in workspace would be a second library and
    # its memory would be a second Memory.
    practice = resolve_practice_service(port, env)
    # v0.4 sharing: B's sharing service over the *same* owner bindings (or a named reason).  With
    # none, the cycle offers no draft/action at all -- and nothing pretends a share was delivered.
    sharing = resolve_sharing_service(env, subject_id=supply["subject_id"],
                                      grant_id=supply["artifact_grant_id"])

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

    # v0.4 stage A: her own "later" and the private-project seam.
    #   * ``future`` is a store in this cycle's own state root (one JSON document, atomic writes).
    #   * ``project_gateway`` is B's owner service when it is wired; with none, the gateway is
    #     *unavailable* by name, no project option is offered and nothing can be created -- there
    #     is deliberately no local stand-in library of works.
    #   * ``questions`` reads her own private ``unclear`` markers, which is the material a project
    #     option may be seeded from.  An unreadable private document simply yields no option.
    future_items = None
    project_gateway = None
    project_questions = None
    try:
        import life_future_items as lfi
        import life_project_options as lpo

        future_items = lfi.FutureItems(root, subject_id=supply["subject_id"])
        project_gateway = lpo.resolve_gateway(
            environment=env, subject_id=supply["subject_id"],
            workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))
        _items_id = bindings.get("private_items_artifact_id")
        if _items_id:
            import private_daily_items as pdi

            _store = pdi.PrivateDailyItems(port, str(_items_id))

            def project_questions(_store=_store):
                # The metadata view is deliberately text-free, so the note itself is read through
                # the document's own explicit accessor (``full_text``) -- never inferred from a
                # flag.  A question with no note is not a seed and is skipped.
                try:
                    rows: list[dict[str, Any]] = []
                    for row in _store.items(pdi.ITEM_UNCLEAR_MARKER):
                        item_id = str(row.get("item_id") or "")
                        if not item_id or not row.get("has_note"):
                            continue
                        rows.append({
                            "marker_id": item_id,
                            "source_id": str(row.get("source_id") or ""),
                            "source_revision": str(row.get("source_revision") or ""),
                            "note": _store.full_text(item_id),
                        })
                    return rows
                except Exception:
                    return []
    except Exception as exc:  # a seam that cannot be built is reported, never faked
        project_questions = None
        _seam_error = f"{type(exc).__name__}: {exc}"[:200]

    cycle = alc.AutonomousLifeCycle(
        runtime,
        artifact_port=port,
        subject_id=supply["subject_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"),
        artifact_grant_id=supply["artifact_grant_id"],
        writer=writer,
        reading_response_writer=reading_response_writer,
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
        # v0.4 stage A: her own "later" store and the private-project seam (both optional).
        future_items=future_items,
        project_owner=project_gateway,
        project_questions_provider=project_questions,
        private_items_artifact_id=bindings["private_items_artifact_id"],
        # v0.4 research: B's workspace over this exact owner port (or ``None``).  With none, no
        # research option exists and nothing can be created; with one, the decision and writing
        # prompts carry bounded, versioned source text and the created work is an ordinary
        # continuable artifact written through AR-0.
        research_workspace=research["service"],
        # v0.4 C: the bounded practice seam over B's workspace (or ``None``).  With one, the
        # decision prompt sees her recallable experiences and her relevant methods, a review option
        # can be created and a method draft saved, and the writer's prompt carries the methods as
        # *document* context -- never as permission, code or persona.
        practice_workspace=practice["service"],
        # v0.4 sharing: B's owner seam (or ``None``).  With one, the decision prompt carries her
        # open drafts and the sources she could share from, and one owner call settles the single
        # sharing request a Decision may make.  ``LOCAL_DELIVERED`` stays a local-inbox fact.
        sharing_service=sharing["service"],
    )
    # v0.4 B: the local-contact seam (A's inbox + the finite co-presence protocol).  It is built
    # on the *same* subject and home the cycle already uses, so an invitation that names another
    # instance is declined by the protocol rather than accepted by a guess.  The port is attached
    # to the cycle (bounded cognition view) and handed back as ``contact_port`` for the resident's
    contacts = resolve_local_contacts(runtime, cycle=cycle, state_root=root, env=env,
                                      subject_id=supply["subject_id"], admission=admission,
                                      requests=requests)
    if contacts.get("status") == "READY":
        cycle.social_contacts = contacts["port"]
        cycle.contact_inbox = contacts["inbox"]
        cycle.co_presence = contacts["protocol"]
    contacts_record = {key: value for key, value in contacts.items()
                       if key not in {"inbox", "protocol", "port"}}
    contact_port = contacts.get("port")
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
        # rc1-v04: whether a private page response is wired at all, said plainly, so a reader can
        # tell "the model had nothing to add" (``NO_CHANGE``) from "nothing was asking".
        "reading_response": {
            "wired": reading_response_writer is not None,
            "state": "READY" if reading_response_writer is not None else "NOT_CONFIGURED",
        },
        # v0.4 research: what was really wired, said plainly, so a reader can tell "no workspace in
        # this tree" from "the workspace had no material".  The service object itself is not handed
        # back here -- it lives on the cycle, where the seam owns it.
        "research": {key: value for key, value in research.items() if key != "service"},
        # v0.4 C: what was really wired for practices, said plainly (the service object stays on
        # the cycle, where the seam owns it).
        "sharing": {key: value for key, value in sharing.items() if key != "service"},
        "practice": {key: value for key, value in practice.items() if key != "service"},
        # v0.4 B: the local-contact seam, said plainly -- which identity it was bound to, whether
        # A's ledger exists yet, and the live port the resident step drives.  The inbox and the
        # protocol objects are not re-exported here; they live on the cycle, where the seam owns
        # them, and this record only describes them.
        "contacts": contacts_record,
        "contact_port": contact_port,
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
    # v0.4 C: the bounded, zero-model upkeep runs **first, once per tick**, before anything is
    # polled.  Both reasons come from the real chain:
    #   (a) a closed window still has to re-check the dependency conditions and let LR-4 reach its
    #       own due/expired verdicts -- before this it only happened inside a wake, so a NOT_DUE
    #       tick with no event scanned nothing at all.  It costs no request and no round;
    #   (b) an eligibility LR-4 opens *in this tick* is then visible to the eligibility feed's own
    #       poll in the same tick instead of one tick later.
    # The wake below is told this tick already maintained (`maintain=False` + the report), so the
    # upkeep never runs twice for one tick, and nothing here bypasses the switch, the budget or the
    # lease: `wake` still checks all three.
    upkeep = runner.maintain()
    # v0.4 B: the local-contact pass runs **once per tick**, before the feeds are polled, so that
    #   (a) an ordinary message is merely observed (no pause, no Activity, no model call), and
    #   (b) an invitation's own Decision happens before anything this tick could adopt on its
    #       behalf -- and a joint time that just ended is visible to the eligibility feed's same
    #       tick poll.  The pass is bounded (`max_per_tick`), it claims before it works, and it
    #       records exactly one transport result per event.
    contacts = assembled.get("contact_port")
    contact_step = contacts.tick() if contacts is not None else None
    if contact_step and int(contact_step.get("llm_calls") or 0) > 0:
        # The invitation already used this tick's fresh decision. Leave feeds untouched so
        # their events remain available to the next normal tick, rather than asking twice.
        return {"ok": True, "waiting": upkeep, "contacts": contact_step, "events": [],
                "feed_errors": [], "due": {"kind": "CHECK_WINDOW", "status": "CONTACT_DECISION_HANDLED",
                                           "llm_calls": 0, "decision": None,
                                           "detail": "this tick already considered an explicit invitation"}}
    events: list[Any] = []
    if poller is not None:
        for event in poller.poll():
            events.append(runner.wake_on_event(event, act=act, maintain=False,
                                               maintained=upkeep))
    due = runner.run_due(act=act, maintain=False, maintained=upkeep)
    return {
        "ok": True,
        "waiting": upkeep,
        "contacts": contact_step,
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
    "resolve_sharing_service",
    "resolve_shared_practice",
    "resolve_body_port",
    "resolve_work_artifact_id",
    "resolve_research_service",
    "WORK_ARTIFACT_ENV",
    "WORLD_HOME_ENV",
]
