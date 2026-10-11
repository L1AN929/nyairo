#!/usr/bin/env bash
# v0.2 B8-8 / E14: the single-instance long chain, in one run.
#
#   run_e14_chain.sh chain            the six stages (default)
#   run_e14_chain.sh lifecycle        T06/T07: one full pause and one full abandon lifecycle
#   run_e14_chain.sh probe <mode>     one probe on its own (preconditions|baseline|collect|world|chat)
#
# Everything runs against the profile under $NYA02_E14_ROOT (default /srv/nya02/e14): its own
# subject, workspace, artifact grant, state root, Memory database and World instance.  Nothing
# here touches the shared /srv/nya02 instances, and nothing here edits the repository.
#
# The two probe programs this script drives are written into $E14/ at run time, so the whole
# chain is reproducible from this one file.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SELF_DIR/../../../.." && pwd)"
E14="${NYA02_E14_ROOT:-/srv/nya02/e14}"
PROFILE_DIR="${NYA02_RESIDENT_DIR:-$E14/resident}"
STATE="${NYA02_RESIDENT_STATE:-$PROFILE_DIR/state}"
SUBJECT="${NYA02_RESIDENT_SUBJECT:-chiyo-e14}"
SOCKET="${NYA02_SUPPLY_SOCKET:-/srv/nya02/sock/life-supply.sock}"
WORLD_INSTANCE="${NYA02_WORLD_INSTANCE:-$E14/world-instance}"
POLL="${NYA02_E14_POLL_SECONDS:-5}"
INTERVAL="${NYA02_E14_INTERVAL_MINUTES:-1}"
SVC_USER="${NYA02_SVC_USER:-nyasvc}"
RESIDENT="$REPO/components/life/tests/v02/run_resident.sh"
WORLD_DRIVER="$REPO/components/life/tests/v02/world_move_e2e.py"

write_programs() {
  mkdir -p "$E14"
  cat > "$E14/e14_probe.py" <<'E14_PROBE_PY'
#!/usr/bin/env python3
"""E14 (B8-8) probe: read back what the owners really hold, at each stage.

Every value printed here is read from a real owner -- the Supply owner over its socket, the
canonical Life state root, the M0 evidence database, the World gateway.  Nothing is inferred
from a log line when an owner can be asked instead.

Modes
-----
preconditions  what the profile resolves to, before anything is started
baseline       the state at the moment the resident is about to be started
collect <tag>  the state after one consideration window, plus the window's own record
world          a fresh World observation, the AG-0 set it supports, and a refusal
chat           the Hermes plugin's own context hook, then one real model turn
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(os.environ["NYA02_REPO"])
COMPONENTS = REPO / "components"
for _path in (str(COMPONENTS / "life" / "scripts"), str(COMPONENTS / "native"),
              str(COMPONENTS / "native" / "src"),
              str(COMPONENTS / "native" / "memory_runtime_v1"),
              str(COMPONENTS / "world" / "service" / "bridge"),
              str(REPO / "vendor" / "hermes"), str(REPO)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

STATE = Path(os.environ["NYA02_RESIDENT_STATE"])
SUBJECT = os.environ["NYA02_SUBJECT"]
SOCKET = os.environ["NYA02_SUPPLY_SOCKET"]
WORKSPACE = os.environ["NYA02_WORKSPACE_ID"]
GRANT = os.environ["NYA02_ARTIFACT_GRANT"]
MEMORY_DB = os.environ["NYA02_MEMORY_DB"]
CONTROLS_DB = os.environ.get("NYA02_MEMORY_CONTROLS_DB", "")


def emit(label, payload):
    print(json.dumps({"probe": label, "value": payload}, ensure_ascii=False, indent=1,
                     default=str))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def wake_records(limit=None):
    rows = []
    try:
        text = (STATE / "resident_journal.jsonl").read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:] if limit else rows


def windows():
    """Every real consideration window the runner recorded, in order."""

    out = []
    for record in wake_records():
        for wake in [record.get("due") or {}] + list(record.get("events") or []):
            if not wake:
                continue
            out.append({"at": wake.get("at"), "kind": wake.get("kind"),
                        "status": wake.get("status"), "class": wake.get("status_class"),
                        "outcome": wake.get("outcome"), "decision": wake.get("decision"),
                        "llm_calls": wake.get("llm_calls"),
                        "action_id": wake.get("action_id"),
                        "activity_ref": wake.get("activity_ref"),
                        "detail": (wake.get("detail") or "")[:200]})
    return out


def memory_rows():
    try:
        con = sqlite3.connect("file:%s?mode=ro" % MEMORY_DB, uri=True)
    except sqlite3.Error as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        cur = con.cursor()
        count = list(cur.execute("select count(*) from evidence_events"))[0][0]
        rows = []
        for row in cur.execute(
                "select event_id, occurred_at, memory_owner, source_origin, speaker, content,"
                " turn_id from evidence_events order by rowid desc limit 3"):
            rows.append({"event_id": row[0], "occurred_at": row[1], "memory_owner": row[2],
                         "source_origin": row[3], "speaker": row[4],
                         "content": str(row[5])[:300], "turn_id": row[6]})
        return {"rows": count, "latest": rows}
    finally:
        con.close()


def build_runtime():
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp

    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    return lrp.build_production_life_runtime(STATE, kill_switches=switches,
                                             install_root=STATE)


def canonical():
    runtime = build_runtime()
    try:
        state = runtime.canonical_state()
        activities = []
        for activity_id, row in dict(state.get("activities") or {}).items():
            progress = runtime.progress_read.get_progress_state(str(activity_id)) or {}
            activities.append({"activity_id": activity_id, "status": row.get("status"),
                               "kind": row.get("kind"), "revision": row.get("revision"),
                               "checkpoint_ref": row.get("checkpoint_ref"),
                               "progress_checkpoint": progress.get("checkpoint_ref"),
                               "progress_state": progress.get("progress_state")})
        return {"revision": state.get("revision"),
                "foreground_activity_ref": state.get("foreground_activity_ref"),
                "activities": activities}
    finally:
        runtime.close()


def port():
    from life_artifact_port import LifeArtifactPort

    return LifeArtifactPort(SOCKET, SUBJECT)


def work_view():
    p = port()
    point = p.latest_resumable()
    out = {"resumable": point.to_dict() if point is not None else None}
    if point is not None:
        out["head"] = p.read_head(point.artifact_id)
        out["versions"] = [{"version_number": row.get("version_number"),
                            "version_id": row.get("version_id"),
                            "parent_version_id": row.get("parent_version_id")}
                           for row in sorted(p.list_versions(point.artifact_id),
                                             key=lambda r: r.get("version_number") or 0)] \
            if hasattr(p, "list_versions") else "NOT_EXPOSED_BY_THE_NARROW_PORT"
    out["artifacts"] = [{"artifact_id": r.get("artifact_id"), "title": r.get("title"),
                         "lifecycle": r.get("lifecycle"), "revision": r.get("revision")}
                        for r in p.list_artifacts()]
    return out


def budget_switch():
    return {"switch": read_json(STATE / "autonomous_switch.json"),
            "budget": read_json(STATE / "autonomous_budget.json"),
            "summary": read_json(STATE / "resident_summary.json")}


def mode_preconditions():
    import autonomous_life_assembly as assembly

    supply = assembly.resolve_supply_configuration(os.environ)
    world = assembly.resolve_world_provider(os.environ)
    env_names = {name: bool(os.environ.get(name)) for name in (
        "LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "NYA02_SUPPLY_SOCKET",
        "CHIYO_LIFE_SUPPLY_SOCKET", "NYA02_SUBJECT", "NYA02_ARTIFACT_GRANT",
        "WORLD_BODY_SOCKET", "NYAIRO_WORLD_BRIDGE_DIR")}
    emit("preconditions", {
        "supply": {k: supply[k] for k in ("supply_socket", "subject_id", "artifact_grant_id",
                                          "missing")},
        "supply_env_names_present": env_names,
        "world": {k: world[k] for k in ("status", "reason")},
        "world_bridge_dir": world.get("bridge_dir"),
        "state_root": str(STATE),
        "workspace": WORKSPACE,
        "grant": GRANT,
        "memory_db": MEMORY_DB,
        "model": {"provider": os.environ.get("NYA02_MODEL_PROVIDER"),
                  "name": os.environ.get("NYA02_MODEL_NAME"),
                  "base_url": os.environ.get("NYA02_MODEL_BASE_URL"),
                  "key_present": bool(os.environ.get("NYA02_MODEL_API_KEY"))},
    })


def mode_baseline():
    emit("baseline", {"work": work_view(), "canonical": canonical(),
                      "memory": memory_rows(), "budget_switch": budget_switch(),
                      "windows": windows()})


def mode_collect(tag):
    payload = {"tag": tag, "work": work_view(), "canonical": canonical(),
               "memory": memory_rows(), "budget_switch": budget_switch(),
               "windows": windows()}
    acts = {}
    for row in payload["windows"]:
        if row.get("action_id"):
            acts[row["action_id"]] = None
    if acts:
        runtime = build_runtime()
        try:
            for action_id in acts:
                action = runtime.action_read.get_action(str(action_id)) or {}
                acts[action_id] = {k: action.get(k) for k in (
                    "action_id", "status", "action_kind", "provider_operation_ref",
                    "idempotency_key")}
        finally:
            runtime.close()
    payload["actions"] = acts
    emit("collect", payload)


def mode_world():
    """A fresh observation through the resident's own World seam, and what it supports."""

    import autonomous_life_assembly as assembly
    import world_action_bridge_adapter as wba
    from world_action_bridge_adapter import WorldBridgeResolver

    # The same resolution the resident's own assembly performs: one real provider over the
    # World gateway, or a named reason there is none.
    resolved = assembly.resolve_world_provider(os.environ)
    out = {"provider_status": resolved["status"], "provider_reason": resolved.get("reason")}
    provider = resolved.get("provider")
    if provider is None:
        emit("world", out)
        return
    observations = provider.observe()
    position = read_json(Path(os.environ["NYA02_WORLD_HOME"]) / "data/world/world_state.json",
                         {})
    out.update({
        "world_state": {"place_id": (position.get("location") or {}).get("place_id"),
                        "area_id": (position.get("location") or {}).get("area_id"),
                        "revision": position.get("revision")},
        "opportunities": [{"opportunity_id": item.get("opportunity_id"),
                           "target_refs": item.get("target_refs"),
                           "occurred_at": item.get("occurred_at")} for item in observations]})
    # What the World itself just exposed, exactly as the cycle sees it.
    snapshot = dict(provider.last_observation or {})
    space = dict(provider.last_action_space or {})
    out["observation_location"] = snapshot.get("location")
    out["observation_revision"] = snapshot.get("world_revision") or snapshot.get("revision")
    out["candidate_destinations"] = sorted(
        c["params"]["destination_location_id"] for c in (space.get("candidates") or [])
        if c.get("verb") == "MOVE")
    out["selector_visible_candidates"] = sorted(
        str(c.get("candidate_id")) for c in (space.get("candidates") or []))

    # Unreachable actions are still refused by the real resolver, and change nothing.  The
    # target is taken from names the World does NOT currently offer, so this is a genuinely
    # unreachable destination and not a legal move wearing the wrong label.
    offered = set(out["candidate_destinations"])
    unreachable = next((name for name in ("airport", "university", "narnia",
                                          "place_that_does_not_exist")
                        if name not in offered), None)
    out["unreachable_target"] = unreachable
    if unreachable is None:
        out["refusal"] = {"status": "NO_UNREACHABLE_TARGET_AVAILABLE",
                          "offered": sorted(offered)}
        emit("world", out)
        return
    resolver = WorldBridgeResolver(environment=os.environ,
                                   bridge_dir=os.environ.get("NYAIRO_WORLD_BRIDGE_DIR"))
    before = dict(out["world_state"])
    refused = resolver.resolve_action(idempotency_key="idem:e14:unreachable:%s" % unreachable,
                                      verb="MOVE", target=unreachable,
                                      expected_dependencies={})
    after = read_json(Path(os.environ["NYA02_WORLD_HOME"]) / "data/world/world_state.json", {})
    out["refusal"] = {"transport_result": refused.get("transport_result"),
                      "execution_id": refused.get("execution_id"),
                      "expected_definite_reject": wba.ar0.TRANSPORT_ERR_DEFINITE_REJECT,
                      "place_after": (after.get("location") or {}).get("place_id"),
                      "revision_after": after.get("revision"),
                      "place_before": before.get("place_id"),
                      "revision_before": before.get("revision")}
    emit("world", out)


def mode_chat():
    """The plugin's own context hook, then one real model turn over what it produced."""

    from chiyo_bundle import hermes_plugin
    from chiyo_bundle import presence
    from life_projection import life_projection_from_paths

    runtime = build_runtime()
    try:
        before = runtime.canonical_state()
        projection = life_projection_from_paths(
            runtime=runtime, supply_socket=SOCKET, subject_id=SUBJECT,
            evidence_db=MEMORY_DB, controls_db=CONTROLS_DB or None)

        class _Svc:
            """The plugin's service container, with only the members this hook reads."""

        svc = _Svc()
        svc.life_projection = projection
        svc.life_wrapper = None
        svc.supply_subject = SUBJECT
        # Falsy, so the hook takes its presence-context branch: the one that carries the life
        # projection into the turn without a memory recall.
        svc.memory = None
        svc.native = type("N", (), {})()

        engine = hermes_plugin.ChiyoContextEngine()
        # Exactly the state `on_session_start` leaves behind for an authorised session; the
        # container's unrelated members are not read by this hook.
        engine._authorized = True
        engine._service = svc
        engine._session_id = "e14-chat"
        engine._platform = "cli"

        user_message = "最近在做什么？"
        request = [{"role": "system", "content": "你是她的对话引擎。"},
                   {"role": "user", "content": user_message}]
        selected = engine.select_context(
            request, incoming_message={"role": "user", "content": user_message})

        injected = ""
        if selected:
            for message in selected:
                if message.get("role") == "user" and user_message in str(message.get("content")):
                    injected = str(message["content"])
        out = {"presence_context": presence.presence_context(svc),
               "injected_user_message": injected[:2000],
               "projection_diagnostics": projection.diagnostics()}
        # The chat turn must not disturb the life state it only reads.
        after = runtime.canonical_state()
        out["activity_unchanged"] = (
            json.dumps(before.get("activities"), sort_keys=True, default=str)
            == json.dumps(after.get("activities"), sort_keys=True, default=str))
        out["revision_before_after"] = [before.get("revision"), after.get("revision")]
        out["activity_status"] = [
            (row.get("activity_id"), row.get("status"))
            for row in dict(after.get("activities") or {}).values()]

        # One real model turn, over the context the plugin actually produced.
        if str(os.environ.get("NYA02_E14_CHAT_CALL") or "0").strip() != "1":
            out["model"] = {
                "status": "NOT_CALLED",
                "reason": "NYA02_E14_CHAT_CALL is not 1: the E14 provider budget was already "
                          "spent by the autonomous windows, and this batch may not recharge it"}
            emit("chat", out)
            return
        config = {}
        try:
            for line in (STATE.parent / "model_env").read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                config[name.strip()] = value.strip()
        except OSError:
            pass
        from profile_model_transport import OpenAICompatibleTransport

        transport = OpenAICompatibleTransport(
            base_url=config.get("NYA02_MODEL_BASE_URL") or os.environ["NYA02_MODEL_BASE_URL"],
            api_key=config.get("NYA02_MODEL_API_KEY") or os.environ["NYA02_MODEL_API_KEY"],
            model=config.get("NYA02_MODEL_NAME") or os.environ["NYA02_MODEL_NAME"],
            provider=config.get("NYA02_MODEL_PROVIDER") or "custom",
            timeout_s=60.0, purpose="chat", temperature=0.0)
        result = transport.complete(
            system_prompt="你是她的对话引擎。只根据给你的背景资料回答，不要编造。",
            user_prompt=injected or user_message, timeout_s=60.0, max_output_tokens=400)
        out["model"] = {"text": result.text, "latency_ms": result.latency_ms,
                        "calls": transport.call_count,
                        "model": transport.model}
        emit("chat", out)
    finally:
        runtime.close()


def main(argv):
    mode = (argv[1] if len(argv) > 1 else "baseline").strip()
    handlers = {"preconditions": mode_preconditions, "baseline": mode_baseline,
                "collect": lambda: mode_collect(argv[2] if len(argv) > 2 else ""),
                "world": mode_world, "chat": mode_chat}
    handler = handlers.get(mode)
    if handler is None:
        print("unknown mode %r" % mode, file=sys.stderr)
        return 2
    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
E14_PROBE_PY
  cat > "$E14/e14_lifecycle.py" <<'E14_LIFECYCLE_PY'
#!/usr/bin/env python3
"""E14 / B8-8: one full PAUSE lifecycle and one full ABANDON lifecycle.

The decision *word* is a controlled stand-in, exactly as in B8-4's matrix run: the point is
what the owners do with a legal decision, not what a model would decide.  Every state change
goes through the real chain -- AG-1 validation, AG-2 adoption, LR-2 ActivityCommandService --
and every assertion is read back from the canonical Activity store.

  T06  ACTIVE -> a legal PAUSE decision -> PAUSED, checkpoint retained, projection renders
  T07  ACTIVE -> a legal ABANDON decision -> ABANDONED, work kept, nothing auto-resumes
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

COMPONENTS = Path("/mnt/d/nyairo-v02/components")
for _path in (str(COMPONENTS / "life" / "scripts"), str(COMPONENTS / "native"),
              str(COMPONENTS / "native" / "src"),
              str(COMPONENTS / "native" / "memory_runtime_v1"),
              str(COMPONENTS.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class Deciding:
    """A controlled decision word.  The chain, the owners and the store are real."""

    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def invoke(self, request, *, schema_retry_index=0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        if self.decision in {"START", "CONTINUE", "RESUME"}:
            candidates = list(getattr(request, "candidates", []) or [])
            chosen = candidates[0].get("candidate_id") if candidates else None
        raw = {"decision": self.decision, "selected_candidate_id": chosen,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": "controlled %s" % self.decision}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:e14-lifecycle", model_id="e14-lifecycle",
            model_version="e14", raw_output=raw, latency_ms=0)


class IdentityWriter:
    def next_content(self, point, *, observed_at: str) -> str:
        return point.content or ""


def main() -> int:
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from autonomous_life_cycle import (AutonomousLifeCycle, OUTCOME_ABANDONED, OUTCOME_ACTED,
                                       OUTCOME_PAUSED)
    from life_artifact_port import LifeArtifactPort
    from life_projection import LifeProjection
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    subject = os.environ["NYA02_SUBJECT"]
    workspace_id = os.environ["NYA02_WORKSPACE_ID"]
    grant_id = os.environ["NYA02_ARTIFACT_GRANT"]
    install_root = Path(os.environ["NYA02_LIFE_STATE"]).parent
    counter = {"n": 0}

    def build(cognition, *, writer=None):
        counter["n"] += 1
        root = install_root / ("m-%02d" % counter["n"])
        state = root / "state"
        state.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
        adapter = SupplyManagedArtifactActionAdapter(
            port, grant_id=grant_id, workspace_id=workspace_id)
        runtime = lrp.build_production_life_runtime(
            root, kill_switches=switches, cognition_adapter=cognition,
            install_root=root, artifact_action_adapter=adapter)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant_id, writer=writer, enabled=True, max_rounds_per_day=8)
        return runtime, port, cycle

    def seed(port, tag):
        title = "E14 生命周期 %s" % tag
        created = port.create_artifact(
            workspace_id=workspace_id, title=title,
            content="# %s\n\n第一行。\n" % tag, grant_id=grant_id,
            operation_key="op:e14:lifecycle:%s:1" % tag)
        if created.artifact_id:
            return str(created.artifact_id)
        # A replayed create answers BLOCKED/OPERATION_ALREADY_COMMITTED without echoing the id,
        # so the id is read back from the owner instead of being assumed.
        for row in port.list_artifacts():
            if str(row.get("title")) == title:
                print("  note  the create was a replay (%s); resolved %s by title"
                      % (created.reason or created.status, row["artifact_id"]))
                return str(row["artifact_id"])
        raise SystemExit("no artifact titled %r" % title)

    def activity_row(runtime, activity_id):
        return dict(runtime.canonical_state()["activities"]).get(str(activity_id))

    def checkpoint_of(runtime, activity_id):
        return (runtime.progress_read.get_progress_state(str(activity_id)) or {}).get(
            "checkpoint_ref")

    def history(runtime, activity_id):
        return runtime.activity_read.get_transition_history(activity_id=str(activity_id))

    def note_transition(rows, label):
        if not rows:
            return
        row = rows[-1]
        print("  note  %s transition: %s" % (label, json.dumps(
            {k: row.get(k) for k in ("transition_id", "from_status", "to_status",
                                     "decision_ref", "adoption_ref", "result_revision",
                                     "reason_code", "entry_hash")}, ensure_ascii=False)))

    # ------------------------------------------------------------------ T06
    print("[T06] one full pause lifecycle: ACTIVE -> PAUSE -> PAUSED")
    deciding = Deciding("START")
    runtime, port, cycle = build(deciding, writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t06")
        started = cycle.evaluate_once(trigger_ref="evt:e14:t06:start")
        activity_ref = started.get("activity_ref")
        check(started.get("outcome") in {OUTCOME_ACTED, "ALREADY_DONE"} and bool(activity_ref),
              "a real round adopted an Activity", (started.get("outcome"), activity_ref))
        before = activity_row(runtime, activity_ref)
        check(str(before.get("status")) == "ACTIVE", "the Activity is ACTIVE before the pause",
              before.get("status"))
        checkpoint_before = checkpoint_of(runtime, activity_ref)
        head_before = (port.read_head(artifact_id) or {}).get("version_id")

        deciding.decision = "PAUSE"
        paused = cycle.evaluate_once(trigger_ref="evt:e14:t06:pause")
        print("  note  AG-1 was asked and answered %r; the chain reported %r"
              % (paused.get("decision"), paused.get("outcome")))
        check(paused.get("outcome") == OUTCOME_PAUSED,
              "the chain reports a real pause", (paused.get("outcome"), paused.get("detail")))
        after = activity_row(runtime, activity_ref)
        check(str(after.get("status")) == "PAUSED",
              "the canonical Activity is PAUSED", after.get("status"))
        check(checkpoint_of(runtime, activity_ref) == checkpoint_before,
              "its checkpoint survived the pause",
              (checkpoint_before, checkpoint_of(runtime, activity_ref)))
        check((port.read_head(artifact_id) or {}).get("version_id") == head_before,
              "the work was not touched by the pause")
        rows = [r for r in history(runtime, activity_ref)
                if str(r.get("transition_type")) == "PAUSE"]
        check(bool(rows), "the PAUSE transition is in the canonical history", len(rows))
        note_transition(rows, "pause")
        projection = LifeProjection(runtime=runtime, artifact_port=port)
        check(isinstance(projection.render(), str) and bool(projection.render()),
              "the chat projection still renders", projection.render()[:80])
        # By contract the store clears foreground_activity_ref only on a terminal status or on
        # WAIT-with-release, so a PAUSED Activity deliberately stays "the current one" -- that is
        # what keeps it resumable.  The observable consequence is that it is no longer ACTIVE.
        print("  note  foreground_activity_ref after the pause: %r (kept by contract)"
              % runtime.canonical_state().get("foreground_activity_ref"))
    finally:
        runtime.close()

    # ------------------------------------------------------------------ T07
    print()
    print("[T07] one full abandon lifecycle: ACTIVE -> ABANDON -> ABANDONED")
    deciding = Deciding("START")
    runtime, port, cycle = build(deciding, writer=IdentityWriter())
    try:
        artifact_id = seed(port, "t07")
        started = cycle.evaluate_once(trigger_ref="evt:e14:t07:start")
        activity_ref = started.get("activity_ref")
        check(started.get("outcome") in {OUTCOME_ACTED, "ALREADY_DONE"} and bool(activity_ref),
              "a real round adopted an Activity", (started.get("outcome"), activity_ref))
        check(str(activity_row(runtime, activity_ref).get("status")) == "ACTIVE",
              "the Activity is ACTIVE before the abandonment")

        deciding.decision = "ABANDON"
        abandoned = cycle.evaluate_once(trigger_ref="evt:e14:t07:abandon")
        print("  note  AG-1 was asked and answered %r; the chain reported %r"
              % (abandoned.get("decision"), abandoned.get("outcome")))
        check(abandoned.get("outcome") == OUTCOME_ABANDONED,
              "the chain reports a real abandonment",
              (abandoned.get("outcome"), abandoned.get("detail")))
        after = activity_row(runtime, activity_ref)
        check(str(after.get("status")) == "ABANDONED",
              "the canonical Activity is ABANDONED", after.get("status"))
        check(str(runtime.canonical_state().get("foreground_activity_ref") or "") == "",
              "it no longer holds the foreground",
              runtime.canonical_state().get("foreground_activity_ref"))
        check(not runtime.activity_read.list_open_activities(),
              "it is retired from the open set",
              runtime.activity_read.list_open_activities())
        content = port.read_artifact(artifact_id).get("content")
        check(bool(content), "the work is kept, not deleted", (content or "")[:40])
        rows = [r for r in history(runtime, activity_ref)
                if str(r.get("transition_type")) == "ABANDON"]
        check(bool(rows), "the ABANDON transition is in the canonical history", len(rows))
        note_transition(rows, "abandon")

        # Nothing auto-resumes into a second parallel Activity on the next opportunity.
        deciding.decision = "START"
        nxt = cycle.evaluate_once(trigger_ref="evt:e14:t07:next")
        open_now = runtime.activity_read.list_open_activities()
        check(len(open_now) <= 1,
              "nothing auto-resumes into a second parallel Activity",
              [(a.get("activity_id"), a.get("status")) for a in open_now])
        check(all(str(a.get("activity_id")) != str(activity_ref) for a in open_now),
              "the abandoned Activity stays retired", open_now)
        print("  note  the next opportunity was answered %r and reported %r"
              % (nxt.get("decision"), nxt.get("outcome")))
    finally:
        runtime.close()

    print()
    if FAILURES:
        print(f"E14_LIFECYCLE: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("E14_LIFECYCLE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
E14_LIFECYCLE_PY
  chmod 644 "$E14/e14_probe.py" "$E14/e14_lifecycle.py"
}

# The World instance's own names, exported only when that instance is really there.  It is
# this batch's own World (own home, own gateway socket), never the shared one.
load_world_env() {
  if [ -f "$WORLD_INSTANCE/env.sh" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$WORLD_INSTANCE/env.sh"
    set +a
  fi
}

# The runtime refuses a state root that is not 0o700 and owned by the service identity; the
# launcher's own `prepare_state` does the same thing before it starts a process.
prepare_state_root() {
  mkdir -p "$STATE"
  chmod 700 "$STATE"
  chown -R "$SVC_USER:$SVC_USER" "$STATE"
}

probe_env_args() {
  printf '%s\n' \
    "NYA02_REPO=$REPO" \
    "NYA02_RESIDENT_STATE=$STATE" \
    "NYA02_SUBJECT=$SUBJECT" \
    "NYA02_SUPPLY_SOCKET=$SOCKET" \
    "NYA02_WORKSPACE_ID=$(cat "$PROFILE_DIR/workspace_id")" \
    "NYA02_ARTIFACT_GRANT=$(cat "$PROFILE_DIR/artifact_grant")" \
    "NYA02_MEMORY_DB=${NYA02_MEMORY_DB:-$PROFILE_DIR/memory/evidence.db}" \
    "NYA02_MEMORY_CONTROLS_DB=${NYA02_MEMORY_CONTROLS_DB:-$PROFILE_DIR/memory/controls.db}" \
    "LIFE_SUPPLY_SOCKET=$SOCKET"
}

probe() {
  prepare_state_root
  local args=()
  while IFS= read -r line; do args+=("$line"); done < <(probe_env_args)
  runuser -u "$SVC_USER" -- env "${args[@]}" \
    WORLD_BODY_SOCKET="${WORLD_BODY_SOCKET:-}" \
    NYAIRO_WORLD_BRIDGE_DIR="${NYAIRO_WORLD_BRIDGE_DIR:-$REPO/components/world/service/bridge}" \
    NYA02_WORLD_HOME="${NYA02_WORLD_HOME:-$WORLD_INSTANCE/world}" \
    NYA02_E14_CHAT_CALL="${NYA02_E14_CHAT_CALL:-0}" \
    PYTHONPATH="$REPO/components/life/scripts:$REPO/components/native:$REPO/components/native/src:$REPO/components/native/memory_runtime_v1:$REPO/vendor/hermes:$REPO" \
    python3 "$E14/e14_probe.py" "$@"
}

resident() {
  runuser -u root -- env \
    NYA02_RESIDENT_DIR="$PROFILE_DIR" \
    NYA02_RESIDENT_STATE="$STATE" \
    NYA02_RESIDENT_SUBJECT="$SUBJECT" \
    NYA02_WORLD_INSTANCE="$WORLD_INSTANCE" \
    NYA02_RESIDENT_POLL_SECONDS="$POLL" \
    CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES="$INTERVAL" \
    CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY="${NYA02_E14_MAX_ROUNDS:-4}" \
    CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY="${NYA02_E14_MAX_ACTIONS:-4}" \
    bash "$RESIDENT" "$@"
}

enable_switch() {
  runuser -u "$SVC_USER" -- env PYTHONPATH="$REPO/components/life/scripts" python3 -c "
import autonomous_life_runner as r
print('switch:', r.AutonomousSwitch('$STATE').set(True, actor='e14-chain'))
"
}

count_windows() {
  # count_windows <field> <value>  -> how many consideration windows match
  python3 - "$STATE/resident_journal.jsonl" "$1" "$2" <<'PY'
import json, sys
path, field, want = sys.argv[1], sys.argv[2], sys.argv[3]
n = 0
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    text = ""
for line in text.splitlines():
    if not line.strip():
        continue
    try:
        record = json.loads(line)
    except ValueError:
        continue
    for wake in [record.get("due") or {}] + list(record.get("events") or []):
        if not wake:
            continue
        value = wake.get("outcome") if field == "outcome" else wake.get(field)
        if str(value) == want:
            n += 1
print(n)
PY
}

wait_for() {
  # wait_for <field> <value> <count> <timeout seconds>
  local field="$1" value="$2" want="$3" timeout="${4:-420}" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if [ "$(count_windows "$field" "$value")" -ge "$want" ]; then
      echo "   reached $field=$value x$want after ${waited}s"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  echo "   TIMEOUT after ${timeout}s waiting for $field=$value x$want (saw $(count_windows "$field" "$value"))"
  return 1
}

stage_1_life_start() {
  echo "== S1: one clean profile, one real artifact, one real runtime, one resident"
  probe preconditions
  probe baseline
  resident start
  enable_switch
  echo "-- the resident's own record"
  runuser -u "$SVC_USER" -- env NYA02_RESIDENT_STATE="$STATE" python3 \
    "$PROFILE_DIR/app/components/life/scripts/autonomous_life_resident.py" \
    --status --state-root "$STATE" | head -40
}

stage_2_first_creation() {
  echo
  echo "== S2: the first autonomous creation, with no user message"
  wait_for status_class acted 1 420 || return 1
  resident stop
  probe collect s2
}

stage_3_restart_and_continue() {
  echo
  echo "== S3: stop normally, restart the same profile, and let a second decision happen"
  resident stop
  resident start
  wait_for status_class acted 2 420 || return 1
  resident stop
  probe collect s3
}

stage_4_non_action() {
  echo
  echo "== S4: a legal but not-urgent opportunity, considered and not executed"
  resident start --no-act
  # The runner names a deferral `decision=DEFER` with `outcome=DEFERRED`; both spellings are
  # matched so a real deferral is never mistaken for a timeout.  The wait returns as soon as
  # the first one lands, and the resident is stopped at once, so no second window is spent.
  wait_for decision DEFER 1 240 || wait_for outcome NO_ACTION 1 60 || \
    wait_for outcome NO_MATERIAL 1 60 || return 1
  resident stop
  probe collect s4
}

stage_5_world_changes() {
  echo
  echo "== S5: the real World owner changes the world; the next observation follows it"
  local before after
  before="$(python3 - "$WORLD_INSTANCE/world/data/world/world_state.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
print("%s|%s" % (s["location"]["place_id"], s["revision"]))
PY
)"
  echo "   world before: $before"
  mkdir -p "$E14/world-move-life-state"
  chown -R "$SVC_USER:$SVC_USER" "$E14/world-move-life-state"
  chmod 700 "$E14/world-move-life-state"
  runuser -u "$SVC_USER" -- env \
    NYA02_WORLD_HOME="$WORLD_INSTANCE/world" \
    NYA02_WORLD_LIFE_STATE="$E14/world-move-life-state" \
    WORLD_BODY_SOCKET="${WORLD_BODY_SOCKET:-$WORLD_INSTANCE/world/run/gateway.sock}" \
    NYAIRO_WORLD_BRIDGE_DIR="${NYAIRO_WORLD_BRIDGE_DIR:-$REPO/components/world/service/bridge}" \
    CHIYO_LIFE_INSTALL_ROOT="$E14/world-move-life-state" \
    CHIYO_LIFE_PRODUCTION_CUTOVER=canonical LIFE_RUNTIME_ENABLED=true AGENCY_ENABLED=true \
    PYTHONPATH="$REPO/components/life/scripts:$REPO/components/world/service/bridge" \
    python3 "$WORLD_DRIVER" move
  after="$(python3 - "$WORLD_INSTANCE/world/data/world/world_state.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
print("%s|%s" % (s["location"]["place_id"], s["revision"]))
PY
)"
  echo "   world after:  $after"
  [ "$before" != "$after" ] || { echo "   THE WORLD DID NOT CHANGE"; return 1; }
  probe world
}

stage_6_chat() {
  echo
  echo "== S6: a normal chat turn consumes the same life state"
  probe chat
}

mode_chain() {
  local rc=0
  stage_1_life_start || rc=1
  [ "$rc" -eq 0 ] && { stage_2_first_creation || rc=1; }
  [ "$rc" -eq 0 ] && { stage_3_restart_and_continue || rc=1; }
  [ "$rc" -eq 0 ] && { stage_4_non_action || rc=1; }
  [ "$rc" -eq 0 ] && { stage_5_world_changes || rc=1; }
  [ "$rc" -eq 0 ] && { stage_6_chat || rc=1; }
  echo
  if [ "$rc" -eq 0 ]; then echo "E14_CHAIN: COMPLETE"; else echo "E14_CHAIN: INCOMPLETE (see above)"; fi
  return "$rc"
}

mode_lifecycle() {
  # A fresh Life state root per run: the T06/T07 scenario needs an ACTIVE Activity to start
  # from, and a root left over from a previous run would begin with a paused/abandoned one.
  rm -rf "$E14/matrix-run"
  mkdir -p "$E14/matrix-run/state"
  chown -R "$SVC_USER:$SVC_USER" "$E14/matrix-run"
  chmod 700 "$E14/matrix-run/state"
  runuser -u "$SVC_USER" -- env \
    NYA02_SUBJECT="$(cat "$E14/matrix-profile/subject")" \
    NYA02_WORKSPACE_ID="$(cat "$E14/matrix-profile/workspace_id")" \
    NYA02_ARTIFACT_GRANT="$(cat "$E14/matrix-profile/artifact_grant")" \
    NYA02_SUPPLY_SOCKET="$SOCKET" \
    NYA02_LIFE_STATE="$E14/matrix-run/state" \
    PYTHONPATH="$REPO/components/life/scripts:$REPO/components/native:$REPO/components/native/src:$REPO/components/native/memory_runtime_v1" \
    python3 "$E14/e14_lifecycle.py"
}

write_programs
load_world_env

case "${1:-chain}" in
  chain) shift || true; mode_chain;;
  lifecycle) mode_lifecycle;;
  probe) shift || true; probe "$@";;
  stage5) stage_5_world_changes;;
  stage6) stage_6_chat;;
  *) echo "usage: $0 chain|lifecycle|stage5|stage6|probe <mode>"; exit 2;;
esac
