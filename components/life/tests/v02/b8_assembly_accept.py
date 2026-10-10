#!/usr/bin/env python3
"""B8-2 acceptance: the real assembly, the real CLI entry, the lease and a restart.

Runs inside the isolated environment (a real Life Supply owner on a socket, a real
subject and a real artifact grant).  The *decision* is a controlled stand-in, so no
model is called and nothing is paid for; the assembly, the lease, the CLI path, the
event feed and the persistence are all real code.

Environment: NYA02_SUPPLY_SOCKET / NYA02_SUBJECT / NYA02_ARTIFACT_GRANT / NYA02_LIFE_STATE.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
COMPONENTS = HERE.parents[3]
LIFE_SCRIPTS = COMPONENTS / "life" / "scripts"
NATIVE = COMPONENTS / "native"
for _path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
              str(NATIVE / "memory_runtime_v1"), str(COMPONENTS.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class _Cognition:
    """A controlled stand-in.  It never decides anything by itself."""

    def __init__(self, decision="NO_ACTION"):
        self.decision = decision
        self.calls = 0

    def invoke(self, request, *, schema_retry_index=0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        raw = {"decision": self.decision, "selected_candidate_id": None,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": "controlled stand-in"}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:b8-assembly", model_id="b8-double",
            model_version="b8", raw_output=raw, latency_ms=0)


class _Writer:
    """Returns the head unchanged, so a round can run without producing content."""

    def next_content(self, point, *, observed_at: str) -> str:
        return point.content or ""


def _build_runtime(state_root: Path, cognition):
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter
    from life_artifact_port import LifeArtifactPort

    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], os.environ["NYA02_SUBJECT"])
    adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=os.environ["NYA02_ARTIFACT_GRANT"])
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=state_root, artifact_action_adapter=adapter)
    return runtime, port, adapter


def main() -> int:
    import autonomous_life_assembly as assembly
    import autonomous_life_control as control

    state = Path(os.environ["NYA02_LIFE_STATE"])
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    install_root = state.parent
    env = dict(os.environ)
    env["CHIYO_LIFE_INSTALL_ROOT"] = str(install_root)

    # ---- 5. missing configuration is a named block, never a fake permission ------
    print("[5] missing Supply configuration blocks with a specific reason")
    cognition = _Cognition()
    runtime, port, adapter = _build_runtime(state, cognition)
    try:
        blocked = assembly.assemble_autonomous_life(
            runtime, environment={}, state_root=state, writer=_Writer())
        check(blocked["state"] == assembly.BLOCKED
              and blocked["reason"] == "SUPPLY_CONFIGURATION_MISSING",
              "no supply configuration -> SUPPLY_CONFIGURATION_MISSING",
              (blocked.get("state"), blocked.get("reason")))
        check(len(blocked.get("missing") or []) == 3,
              "and it names every missing fact", blocked.get("missing"))
        no_writer = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state, writer=None)
        check(no_writer["state"] == assembly.BLOCKED
              and no_writer["reason"] in {"NO_CONTENT_WRITER", "SUPPLY_CONFIGURATION_MISSING"},
              "no usable provider -> a named block, not a deterministic writer",
              (no_writer.get("state"), no_writer.get("reason")))

        # ---- 1/2. the real assembly and a real run-once --------------------------
        print("[1/2] the real assembly, and one real run-once")
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state, writer=_Writer())
        check(assembled["state"] == assembly.READY,
              "the chain assembles on the runtime that holds the lease",
              (assembled.get("state"), assembled.get("reason"), assembled.get("detail")))
        check(assembled.get("lease_instance") is not None,
              "and it reports the lease it reused", assembled.get("lease_instance"))
        check(assembled.get("cycle") is not None and assembled.get("runner") is not None,
              "the CLI now has a real cycle and runner handle")
        check(assembled["runner"].enabled is False,
              "autonomous execution is OFF by default", assembled["runner"].enabled)

        # ---- 3. an empty candidate window is silence and costs nothing -----------
        print("[3] an empty window is silence, and a disabled instance does nothing")
        disabled = assembly.run_once(assembled)
        check((disabled.get("wake") or {}).get("status") == "DISABLED",
              "while off, the window is refused before anything is considered",
              disabled.get("wake"))
        check((disabled.get("wake") or {}).get("llm_calls") == 0 and cognition.calls == 0,
              "and it costs nothing", (disabled.get("wake"), cognition.calls))
        control.set_enabled(install_root, True)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state, writer=_Writer())
        check(assembled["runner"].enabled is True,
              "the operator switch turns it on", assembled["runner"].enabled)
        result = assembly.run_once(assembled)
        check(result.get("ok") is True, "run-once reached the cycle", result)
        wake = result.get("wake") or {}
        check(wake.get("status") == "IDLE_NO_CANDIDATE",
              "and reported normal silence", wake.get("status"))
        check(wake.get("llm_calls") == 0 and cognition.calls == 0,
              "no model call was made or charged",
              (wake.get("llm_calls"), cognition.calls))

        # ---- 4. a real event goes through the feed into the runner ---------------
        print("[4] a real event reaches the runner through the feed")
        feed = assembled["feed_poller"]
        check(len(feed.feeds) >= 1, "the artifact feed is wired", [type(f).__name__ for f in feed.feeds])
        artifact = port.create_artifact(
            workspace_id=None if False else _workspace_id(port),
            title="B8 装配验收", content="# B8\n\n第一行。\n",
            grant_id=os.environ["NYA02_ARTIFACT_GRANT"], operation_key="op:b8:create:1")
        check(str(getattr(artifact, "effect_state", "")) == "APPLIED"
              or str(getattr(artifact, "status", "")) == "OK",
              "the owner created a real artifact", artifact.status)
        feed.poll()                                   # first sighting is not news
        port.commit_version(artifact_id=artifact.artifact_id,
                            expected_head=artifact.version_id, content="# B8\n\n第二行。\n",
                            grant_id=os.environ["NYA02_ARTIFACT_GRANT"],
                            operation_key="op:b8:other-writer:1")
        events = feed.poll()
        check([e["kind"] for e in events] == ["ARTIFACT_CHANGED"],
              "another writer's change is a real event", events)
        # The event was already consumed by the assertion above, so the *next* real
        # change is what drives the runner -- poll and wake are the same door.
        port.commit_version(artifact_id=artifact.artifact_id,
                            expected_head=(port.read_head(artifact.artifact_id) or {}).get("version_id"),
                            content="# B8\n\n第三行。\n",
                            grant_id=os.environ["NYA02_ARTIFACT_GRANT"],
                            operation_key="op:b8:other-writer:2")
        records = feed.wake(assembled["runner"])
        check(len(records) == 1, "it opened exactly one window", len(records))
        check(records and records[0].reason == "ARTIFACT_CHANGED",
              "and the window names the real event", records[0].reason if records else None)
        check(feed.wake(assembled["runner"]) == [],
              "polling again with nothing new opens nothing")

        # ---- 6. a restart keeps the switch, the budget and the event memory ------
        print("[6] a restart keeps switch, budget and event consumption")
        control.set_enabled(install_root, True)
        budget_before = assembled["runner"].cycle.budget.snapshot()
        runtime.close()
        cognition2 = _Cognition()
        runtime2, _port2, _adapter2 = _build_runtime(state, cognition2)
        try:
            assembled2 = assembly.assemble_autonomous_life(
                runtime2, environment=env, state_root=state, writer=_Writer())
            check(assembled2["state"] == assembly.READY, "the chain reassembles after a restart",
                  assembled2.get("reason"))
            check(assembled2["runner"].enabled is True,
                  "the persisted switch survived", assembled2["runner"].enabled)
            check(assembled2["runner"].cycle.budget.snapshot() == budget_before,
                  "the budget survived",
                  (budget_before, assembled2["runner"].cycle.budget.snapshot()))
            check(assembled2["feed_poller"].poll() == [],
                  "and the already-observed head is not news again")
        finally:
            runtime2.close()
    finally:
        try:
            runtime.close()
        except Exception:
            pass

    # ---- 7. a second executor cannot hold the same canonical state root ----------
    print("[7] a second executor cannot operate the same canonical state root")
    holder, _p, _a = _build_runtime(state, _Cognition())
    try:
        second_error = None
        try:
            second, _p2, _a2 = _build_runtime(state, _Cognition())
            second.close()
        except Exception as exc:
            second_error = "%s: %s" % (type(exc).__name__, exc)
        check(second_error is not None,
              "a second runtime for the same state root is refused", second_error)
        check(any(word in str(second_error).lower()
                  for word in ("lease", "lock", "held", "another live instance")),
              "and the refusal names the exclusivity it respects", second_error)
    finally:
        holder.close()

    # ---- 8. disabled autonomous life leaves chat and the projection alone --------
    print("[8] with autonomous life off, chat and the read-only projection still work")
    try:
        import presence  # noqa: F401  - present only in the deployed bundle layout
        presence_available = True
    except Exception:
        presence_available = False
    from life_projection import LifeProjection

    projection = LifeProjection(runtime=None, runner=None, artifact_port=None)
    check(projection.render() == "", "with no facts the chat block is empty")
    check(isinstance(control.read_status(install_root), dict),
          "status still answers with the switch, the budget and the last wake")
    check(presence_available, "the chat-path projection module is importable in this layout")

    print()
    if FAILURES:
        print(f"B8_ASSEMBLY: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_ASSEMBLY: PASS")
    return 0


def _workspace_id(port):
    """The workspace the grant targets, read from the owner's own view."""

    try:
        rows = port.list_artifacts()
    except Exception:
        rows = []
    for row in rows or []:
        if row.get("workspace_id"):
            return row["workspace_id"]
    return os.environ.get("NYA02_WORKSPACE_ID")


if __name__ == "__main__":
    raise SystemExit(main())
