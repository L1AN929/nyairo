#!/usr/bin/env python3
"""B8-5 experiment B: does a *real, admitted* experience change the next decision?

Three layers, kept apart on purpose:

1. **Reachable** -- the experience really exists in M0, written by the one legal writer,
   with a real event id and a real AG-2 handoff source.  It is read back through the Life
   boundary port, governance applied.
2. **Prepared differently** -- with the same work, the same head, the same candidates, the
   same budget and the same World, only the visible experience differs, and the decision
   request really carries it (or really does not).
3. **Different consequence** -- *not* claimed here.  Whether the choice or the text changes
   is reported as what was observed, not as what must happen.

B1 forms the experiences from real execution results, never from a sentence someone typed:

* ``S1`` a real owner-confirmed new version  -> AG-2 ``SIGNIFICANT_PROGRESS`` (the cycle emits it)
* ``S2`` a real owner refusal on a stale head -> AG-2 ``CONFLICT`` (the cycle has no conflict
  handoff; AG-2's public ``emit_experience_handoff`` is called on the real integration the
  refused round produced)
* ``S3`` a real ``ABANDON`` of a real foreground Activity -> AG-2 ``ABANDONED`` (emitted by AG-2)

B2 then runs each scenario twice on fresh Life state roots at the *same* head: once with the
admitted experience in the request, once with an empty view (nothing deleted).

The deterministic halves use a controlled cognition, so they cost nothing.  The real-model
halves run only when the profile has a usable provider, and never exceed ``--max-calls``.

Environment
-----------
``NYA02_SUPPLY_SOCKET``      the real Life Supply owner
``NYA02_CAUSAL_ROOT``        a 0700 directory the harness may own
``NYA02_CAUSAL_B_GROUPS``    JSON: {"S1": {"subject":..., "workspace_id":..., "grant_id":...}, ...}
``NYA02_MODEL_BASE_URL`` etc. optional; without them the real-model half is NOT_RUN
``NYA02_MAX_MODEL_CALLS``    default 6
"""
from __future__ import annotations

import hashlib
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

#: What the M0 writer records for a verified execution outcome (see ``build_outcome_event``:
#: the consumer passes the event's own subject, which an AG-2 handoff does not carry, so the
#: memory owner is the instance subject -- the same one the live product uses).
M0_SUBJECT = "chiyo"

FAILURES: list[str] = []
B_GROUPS_ENV = "NYA02_CAUSAL_B_GROUPS"
ROOT_ENV = "NYA02_CAUSAL_ROOT"

SCENARIOS: tuple[dict, ...] = (
    {"key": "S1", "produce": "progress", "handoff_kind": "SIGNIFICANT_PROGRESS",
     "title": "B1 手记", "content": "# B1 手记\n\n第一段：记下今天的天气。\n"},
    {"key": "S2", "produce": "conflict", "handoff_kind": "CONFLICT",
     "title": "B2 手记", "content": "# B2 手记\n\n第一段：把昨天那一段接上。\n"},
    {"key": "S3", "produce": "abandon", "handoff_kind": "ABANDONED",
     "title": "B3 手记", "content": "# B3 手记\n\n第一段：想写点别的。\n"},
)


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


# ---------------------------------------------------------------------------------------
# controlled cognition and the writers that make each real outcome happen
# ---------------------------------------------------------------------------------------


class _FixedCognition:
    """A controlled cognition output.  It decides nothing; it answers the first candidate."""

    def __init__(self, decision: str = "START") -> None:
        self.decision = decision
        self.model_id = "b8-causal-b"
        self.model_version = "b8"
        self.calls = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        chosen = candidates[0].get("candidate_id") if candidates else None
        raw = {"decision": self.decision, "selected_candidate_id": chosen,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": "controlled stand-in"}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:b8-causal-b", model_id="b8-causal-b",
            model_version="b8", raw_output=raw, latency_ms=0)


class _ObservingCognition:
    """Records the request the decision was really made from, then delegates."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.seen: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def invoke(self, request, *, schema_retry_index: int = 0):
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        self.seen.append({
            "prior_experiences": [dict(x) for x in
                                  (getattr(request, "prior_experiences", ()) or ())],
            "candidate_kinds": [c.get("candidate_kind") for c in candidates],
            "candidate_ids": [c.get("candidate_id") for c in candidates],
            "legal_decision_enums": list(getattr(request, "legal_decision_enums", []) or []),
        })
        return self._inner.invoke(request, schema_retry_index=schema_retry_index)


class _HeadUnchangedWriter:
    """Returns the head, so the owner answers NO_CHANGE and no version is written."""

    def __init__(self) -> None:
        self.points: list[dict] = []

    def next_content(self, point, *, observed_at: str) -> str:
        self.points.append({"head_version_id": point.head_version_id,
                            "version_number": point.version_number})
        return point.content or ""


class _GrowWriter:
    """Appends real new text, so the owner commits a real new version."""

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.points: list[dict] = []

    def next_content(self, point, *, observed_at: str) -> str:
        self.points.append({"head_version_id": point.head_version_id,
                            "version_number": point.version_number})
        return (point.content or "") + f"\n{self.suffix}\n"


class _RacingWriter:
    """Someone else edits the work between our read and our commit."""

    def __init__(self, port, artifact_id: str, grant_id: str) -> None:
        self.port = port
        self.artifact_id = artifact_id
        self.grant_id = grant_id
        self.interference = None
        self.points: list[dict] = []

    def next_content(self, point, *, observed_at: str) -> str:
        self.points.append({"head_version_id": point.head_version_id})
        self.interference = self.port.commit_version(
            artifact_id=self.artifact_id, expected_head=point.head_version_id,
            content=(point.content or "") + "\n\n（别人先改了一笔）\n",
            grant_id=self.grant_id, operation_key=f"op:cb:race:{self.artifact_id}")
        return (point.content or "") + "\n\n（我这一笔）\n"


class _CoordinatorSpy:
    """Keeps the raw AG-2 result, so the integration the round created can be named."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.last: dict | None = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def run_decision_and_adoption_cycle(self, **kwargs):
        result = self._inner.run_decision_and_adoption_cycle(**kwargs)
        int_rec = result.get("integration_record")
        dec_rec = (result.get("decision_result") or {}).get("decision_record")
        adp_rec = (result.get("adoption_result") or {}).get("adoption_record")
        self.last = {
            "integration_id": getattr(int_rec, "integration_id", None),
            "activity_ref": getattr(int_rec, "activity_ref", None),
            "integration_status": getattr(int_rec, "status", None),
            "decision": getattr(dec_rec, "decision", None),
            "adoption_status": getattr(adp_rec, "adoption_status", None),
        }
        return result


# ---------------------------------------------------------------------------------------
# environment helpers
# ---------------------------------------------------------------------------------------


def _evidence_port(root: Path):
    """A real M0 evidence store plus its control ledger, both inside the isolated root."""
    from app.evidence import EvidenceStore
    import life_experience_port as lep
    from memory_control import MemoryControlLedger

    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    evidence_db = memory_dir / "evidence.db"
    controls_db = memory_dir / "controls.db"
    EvidenceStore(evidence_db)  # creates the M0 schema
    # The M0 writer records a life outcome under the *memory* subject, which for a life
    # outcome is the instance subject (``build_outcome_event`` is called with it by the
    # consumer).  It is not the Supply subject used to isolate the works above.
    MemoryControlLedger(controls_db, evidence_db, owner=M0_SUBJECT,
                        conversation=lep.life_conversation_id(M0_SUBJECT)).initialize()
    return lep.LifeExperiencePort(evidence_db, controls_db=controls_db, subject=M0_SUBJECT)


def _build(root: Path, entry: dict, port, *, cognition, writer, consumer=None):
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from autonomous_life_cycle import AutonomousLifeCycle
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "autonomous_budget.json").unlink(missing_ok=True)
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=entry["grant_id"], workspace_id=entry["workspace_id"])
    runtime = lrp.build_production_life_runtime(
        root, kill_switches=switches, cognition_adapter=cognition,
        install_root=root, artifact_action_adapter=adapter,
        experience_consumer=consumer)
    runtime.coordinator = _CoordinatorSpy(runtime.coordinator)
    cycle = AutonomousLifeCycle(
        runtime, artifact_port=port, subject_id=entry["subject"],
        workspace_id=entry["workspace_id"], artifact_grant_id=entry["grant_id"],
        writer=writer, enabled=True, max_rounds_per_day=8)
    return runtime, cycle


def _round(runtime, cycle, *, trigger: str) -> dict:
    out = cycle.evaluate_once(trigger_ref=trigger)
    spy = runtime.coordinator
    return {"out": out, "ag2": dict(spy.last or {})}


def _handoff_of(out: dict) -> dict:
    return dict(out.get("experience_handoff") or {})


def _model_factory(counter: dict, cfg: dict):
    def counted_client(*, purpose="agency_cognition", timeout_s=30.0, max_output_tokens=256):
        from profile_model_transport import OpenAICompatibleTransport
        from agency_model_client import AgencyModelClient

        counter["used"] += 1
        if counter["used"] > counter["cap"]:
            raise RuntimeError("MODEL_CALL_CAP_REACHED")
        print(f"  [model call {counter['used']}/{counter['cap']}] {purpose}", flush=True)
        transport = OpenAICompatibleTransport(
            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
            provider=cfg.get("provider") or "custom", timeout_s=timeout_s,
            purpose=purpose, request_json_object=True)
        return AgencyModelClient(config=cfg, transport=transport, purpose=purpose,
                                 timeout_s=timeout_s, max_output_tokens=max_output_tokens)
    return counted_client


def main() -> int:
    # The provider key arrives in a 0600 file, never on the command line (see the launcher).
    import autonomous_life_assembly as _assembly

    _assembly.load_model_env_file()

    from life_artifact_port import LifeArtifactPort
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer

    groups = json.loads(os.environ[B_GROUPS_ENV])
    root_base = Path(os.environ[ROOT_ENV])
    root_base.mkdir(parents=True, exist_ok=True)
    os.chmod(root_base, 0o700)
    socket_path = os.environ["NYA02_SUPPLY_SOCKET"]
    call_cap = int(os.environ.get("NYA02_MAX_MODEL_CALLS") or 6)
    counter = {"used": 0, "cap": call_cap}

    cfg = None
    if all(os.environ.get(n) for n in ("NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME",
                                       "NYA02_MODEL_API_KEY")):
        cfg = {"provider": os.environ.get("NYA02_MODEL_PROVIDER") or "custom",
               "model": os.environ["NYA02_MODEL_NAME"],
               "base_url": os.environ["NYA02_MODEL_BASE_URL"].rstrip("/"),
               "api_key": os.environ["NYA02_MODEL_API_KEY"],
               "key_env": "NYA02_MODEL_API_KEY", "api_key_present": True}

    print("== experiment B: a real admitted experience vs an empty view")
    print(f"scenarios={[s['key'] for s in SCENARIOS]} model={'configured' if cfg else 'NOT_RUN'} "
          f"call_cap={call_cap}")

    for spec in SCENARIOS:
        key = spec["key"]
        entry = groups[key]
        subject = entry["subject"]
        print(f"\n--- {key} ({spec['produce']} -> {spec['handoff_kind']})")
        port = LifeArtifactPort(socket_path, subject)
        created = port.create_artifact(
            workspace_id=entry["workspace_id"], title=spec["title"],
            content=spec["content"], grant_id=entry["grant_id"],
            operation_key=f"op:cb:{key}:{subject}:create:1")
        artifact_id = str(created.artifact_id)
        experience = _evidence_port(root_base / key)
        consumer = MemoryAdmissionExperienceConsumer(port=experience, enabled=True)

        # ================= B1: form the experience from a real execution result ==========
        if spec["produce"] == "progress":
            writer = _GrowWriter("（补一句）")
            runtime, cycle = _build(root_base / f"{key}-b1", entry, port,
                                    cognition=_FixedCognition("START"), writer=writer,
                                    consumer=consumer)
            try:
                step1 = _round(runtime, cycle, trigger=f"evt:cb:{key}:b1")
            finally:
                runtime.close()
            outcome = step1["out"]["outcome"]
            handoff = _handoff_of(step1["out"])
            check(outcome == "ACTED", f"{key} the round really changed the work",
                  (outcome, step1["out"].get("material"), step1["out"].get("detail"),
                   artifact_id))
            check(handoff.get("disposition") == "ACCEPT",
                  f"{key} M0 formally admitted the real outcome", handoff)
            check(handoff.get("status") == "DELIVERED",
                  f"{key} the handoff was delivered, not left pending", handoff)
            source = "cycle_auto"
        elif spec["produce"] == "conflict":
            writer = _RacingWriter(port, artifact_id, entry["grant_id"])
            runtime, cycle = _build(root_base / f"{key}-b1", entry, port,
                                    cognition=_FixedCognition("START"), writer=writer,
                                    consumer=consumer)
            try:
                step1 = _round(runtime, cycle, trigger=f"evt:cb:{key}:b1")
                check(writer.interference is not None
                      and getattr(writer.interference, "applied", False),
                      f"{key} another legal writer really landed first",
                      getattr(writer.interference, "status", None))
                check(step1["out"].get("outcome") == "STEP_REFUSED"
                      and step1["out"].get("owner_status") == "CONFLICT",
                      f"{key} the owner really refused the stale write",
                      (step1["out"].get("outcome"), step1["out"].get("owner_status")))
                integration_id = step1["ag2"].get("integration_id")
                activity_id = step1["ag2"].get("activity_ref")
                check(bool(integration_id and activity_id),
                      f"{key} the refused round produced a real AG-2 integration",
                      step1["ag2"])
                emitted = runtime.coordinator.emit_experience_handoff(
                    integration_id=str(integration_id), activity_id=str(activity_id),
                    terminal_or_milestone_kind="CONFLICT",
                    occurred_at=step1["out"].get("observed_at"))
            finally:
                runtime.close()
            deliveries = ((emitted.get("dispatch_result") or {}).get("deliveries") or [])
            handoff = {"handoff_id": getattr(emitted.get("handoff_event"), "handoff_id", None),
                       "status": deliveries[-1].get("status") if deliveries else None,
                       "disposition": (deliveries[-1].get("consumer_disposition")
                                       if deliveries else None)}
            check(handoff.get("disposition") == "ACCEPT",
                  f"{key} M0 formally admitted the real conflict", handoff)
            source = "ag2_public_api_on_real_integration"
        else:  # abandon
            runtime, cycle = _build(root_base / f"{key}-b1", entry, port,
                                    cognition=_FixedCognition("START"),
                                    writer=_HeadUnchangedWriter(), consumer=consumer)
            try:
                step1 = _round(runtime, cycle, trigger=f"evt:cb:{key}:b1")
                check(step1["out"].get("outcome") == "ALREADY_DONE",
                      f"{key} the owner really answered NO_CHANGE", step1["out"].get("outcome"))
                check(not _handoff_of(step1["out"]).get("handoff_id"),
                      f"{key} a NO_CHANGE round emits no experience at all",
                      _handoff_of(step1["out"]))
            finally:
                runtime.close()
            activity_ref = step1["out"].get("activity_ref")
            check(bool(activity_ref), f"{key} the round left a real foreground activity",
                  activity_ref)
            # A real ABANDON of that real activity.  AG-2 emits the handoff itself.
            # Same Life state root as the round above: the foreground Activity it created is
            # what an ABANDON needs, and a fresh root would have nothing to abandon.
            accepted_before = consumer.accepted
            runtime, cycle = _build(root_base / f"{key}-b1", entry, port,
                                    cognition=_FixedCognition("ABANDON"),
                                    writer=_HeadUnchangedWriter(), consumer=consumer)
            try:
                head_before = dict(port.read_head(artifact_id) or {})
                step2 = _round(runtime, cycle, trigger=f"evt:cb:{key}:b1b")
            finally:
                runtime.close()
            check(step2["out"].get("outcome") == "ABANDONED",
                  f"{key} the round really abandoned the activity",
                  (step2["out"].get("outcome"), step2["out"].get("detail"),
                   step2["out"].get("reason_codes")))
            # The ABANDON path returns from the cycle before it reads the adoption and the
            # integration record, so the round report carries no handoff for it -- the
            # admission is asserted where it really is, at the M0 writer.
            check(consumer.accepted >= accepted_before + 1,
                  f"{key} M0 formally admitted the real abandon",
                  (accepted_before, consumer.accepted, consumer.describe()))
            source = "cycle_auto_on_abandon"
            del head_before

        admitted = experience.prompt_view()
        check(len(admitted) >= 1, f"{key} the admitted experience is readable back through "
                                  "the boundary port", admitted)
        check(consumer.accepted >= 1 and consumer.duplicates == 0 and consumer.deferred == 0,
              f"{key} the admission counters show one real insert",
              consumer.describe())
        raw_rows = __import__("app.evidence", fromlist=["EvidenceStore"]).EvidenceStore(
            Path(experience.evidence_db)).recent_verified_outcomes(memory_owner=M0_SUBJECT, limit=8)
        check(bool(raw_rows), f"{key} the M0 row really exists in the store", len(raw_rows))
        print(f"  {key} admitted: " + json.dumps({
            "event_id": admitted[0]["experience_id"],
            "source_ref": raw_rows[0]["primary_source_ref_id"],
            "outcome_kind": admitted[0].get("owner_result"),
            "text": admitted[0]["text"],
            "emit_path": source,
        }, ensure_ascii=False))

        # ================= B2: the same head, only the visible view differs ==============
        halves: dict[str, dict] = {}
        for label in ("A", "B"):
            head_before = dict(port.read_head(artifact_id) or {})
            inner = _ObservingCognition(_FixedCognition("START"))
            writer = _HeadUnchangedWriter()
            provider = (lambda: experience.prompt_view()) if label == "A" else None
            runtime, cycle = _build(root_base / f"{key}-b2-{label}", entry, port,
                                    cognition=inner, writer=writer, consumer=consumer)
            cycle.experience_provider = provider
            try:
                step = _round(runtime, cycle, trigger=f"evt:cb:{key}:b2-{label}")
                head_after = dict(port.read_head(artifact_id) or {})
            finally:
                runtime.close()
            seen = inner.seen[-1] if inner.seen else {}
            halves[label] = {
                "head_before": head_before.get("version_id"),
                "head_after": head_after.get("version_id"),
                "experiences_in_request": [x.get("experience_id")
                                           for x in seen.get("prior_experiences", [])],
                "experience_text_in_request": [x.get("text")
                                               for x in seen.get("prior_experiences", [])],
                "candidate_kinds": seen.get("candidate_kinds"),
                "candidate_ids": seen.get("candidate_ids"),
                "legal_decision_enums": seen.get("legal_decision_enums"),
                "decision": step["out"].get("decision"),
                "reason_codes": step["out"].get("reason_codes"),
                "short_rationale": step["out"].get("short_rationale"),
                "selected_candidate_ref": step["out"].get("selected_candidate_ref"),
                "outcome": step["out"].get("outcome"),
                "step_path": step["out"].get("step_path"),
                "capacity_state": step["out"].get("capacity_state"),
                "prepared": (step["out"].get("step_receipt") or {}),
                "written_chars": [p.get("version_number") for p in writer.points],
            }
            print(f"  {key}/{label}: " + json.dumps({
                k: halves[label][k] for k in ("head_before", "head_after", "decision",
                                              "reason_codes", "outcome",
                                              "experiences_in_request",
                                              "candidate_kinds")}, ensure_ascii=False))

        a, b = halves["A"], halves["B"]
        check(a["head_before"] == b["head_before"] == a["head_after"] == b["head_after"],
              f"{key} both halves faced the same, unchanged head",
              (a["head_before"], b["head_before"], a["head_after"], b["head_after"]))
        check(a["candidate_ids"] == b["candidate_ids"],
              f"{key} both halves faced the same candidate set",
              (a["candidate_ids"], b["candidate_ids"]))
        check(a["legal_decision_enums"] == b["legal_decision_enums"]
              and a["capacity_state"] == b["capacity_state"],
              f"{key} both halves faced the same legal options and the same capacity",
              (a["legal_decision_enums"], b["legal_decision_enums"], a["capacity_state"],
               b["capacity_state"]))
        check(len(a["experiences_in_request"]) >= 1 and b["experiences_in_request"] == [],
              f"{key} the only difference is experience visibility",
              (a["experiences_in_request"], b["experiences_in_request"]))
        check(admitted[0]["text"][:20] in (a["experience_text_in_request"] or [""])[0],
              f"{key} the request carried the admitted text itself",
              a["experience_text_in_request"])

        # ================= real-model half (optional, capped) ============================
        if cfg is None or call_cap <= 0:
            why = ("call cap is zero for this run" if call_cap <= 0
                   else "no usable provider in this profile")
            print(f"  {key} real-model half: NOT_RUN ({why})")
            real = None
            # (a zero cap means: do not touch the provider at all in this run)
        else:
            from agency_cognition_adapter import ProductionAgencyCognitionAdapter
            factory = _model_factory(counter, cfg)
            real = {}
            for label in ("A", "B"):
                inner = _ObservingCognition(ProductionAgencyCognitionAdapter(
                    client=factory(purpose="agency_cognition", timeout_s=30.0,
                                   max_output_tokens=256),
                    timeout_s=30.0, max_output_tokens=256))
                runtime, cycle = _build(root_base / f"{key}-rm-{label}", entry, port,
                                        cognition=inner, writer=_HeadUnchangedWriter(),
                                        consumer=consumer)
                cycle.experience_provider = (
                    (lambda: experience.prompt_view()) if label == "A" else None)
                try:
                    step = _round(runtime, cycle, trigger=f"evt:cb:{key}:rm-{label}")
                finally:
                    runtime.close()
                seen = inner.seen[-1] if inner.seen else {}
                real[label] = {
                    "experiences_in_request": [x.get("experience_id")
                                               for x in seen.get("prior_experiences", [])],
                    "decision": step["out"].get("decision"),
                    "reason_codes": step["out"].get("reason_codes"),
                    "short_rationale": step["out"].get("short_rationale"),
                    "selected_candidate_ref": step["out"].get("selected_candidate_ref"),
                    "outcome": step["out"].get("outcome"),
                    "detail": step["out"].get("detail"),
                }
                print(f"  {key}/real-{label}: " + json.dumps(real[label], ensure_ascii=False))
            check(len(real["A"]["experiences_in_request"]) >= 1
                  and real["B"]["experiences_in_request"] == [],
                  f"{key} the real-model halves differed only in the visible experience",
                  (real["A"]["experiences_in_request"], real["B"]["experiences_in_request"]))

        port.close()

    print(f"\nmodel calls used: {counter['used']}/{counter['cap']}")
    print()
    if FAILURES:
        print(f"EXPERIMENT_B: FAIL ({len(FAILURES)} checks)")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("EXPERIMENT_B: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
