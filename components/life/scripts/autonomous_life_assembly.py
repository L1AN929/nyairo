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

import os
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


def _model_writer(environment: Mapping[str, str], *, timeout_s: float = 30.0) -> Any:
    """The real content writer, over the instance's own provider.

    The writer is the one place a model produces text for a work, so it is built from
    the same profile resolution the rest of the instance uses.  A profile that cannot
    be resolved yields ``None`` and the caller blocks -- it never substitutes a
    deterministic writer, because that would be fabricated content.
    """

    try:
        from profile_model_config import describe_model_config, load_configured_model_config
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient
        from model_continuation_writer import ModelContinuationWriter
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
                hermes_home=environment.get("NYA02_PROFILE_HOME") or environment.get("CHIYO_PROFILE_HOME"))
        except Exception:
            config = None
    if not config or not describe_model_config(config).get("usable"):
        return None
    transport = OpenAICompatibleTransport(
        base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
        provider=config.get("provider") or "custom", timeout_s=timeout_s,
        purpose="artifact_edit", request_json_object=True)
    client = AgencyModelClient(config=config, transport=transport, purpose="artifact_edit",
                               timeout_s=timeout_s, max_output_tokens=512)
    return ModelContinuationWriter(client, max_new_chars=300)


def assemble_autonomous_life(
    runtime: Any,
    *,
    environment: Optional[Mapping[str, str]] = None,
    state_root: Optional[Path | str] = None,
    experience_provider: Any = None,
    world_provider: Any = None,
    writer: Any = None,
    enabled: bool = False,
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
        writer = _model_writer(env)
    if writer is None:
        return {"state": BLOCKED, "reason": "NO_CONTENT_WRITER",
                "detail": "the profile has no usable provider for the content writer"}

    port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    artifact_adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))

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
    )
    runner = alr.AutonomousLifeRunner(cycle, state_root=root, env=env,
                                      enabled=bool(enabled))
    poller = events.EventFeedPoller([
        events.ResumeEligibilityFeed(getattr(runtime, "waiting_coordinator", None), root)
        if getattr(runtime, "waiting_coordinator", None) is not None else None,
        events.ArtifactChangeFeed(port, root, subject_id=supply["subject_id"]),
    ] + ([events.WorldOpportunityFeed(world_provider, root)] if world_provider else []))
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
        "lease_instance": getattr(lease, "instance_id", None),
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
    "assemble_autonomous_life",
    "resolve_supply_configuration",
    "run_once",
    "runner_tick",
]
