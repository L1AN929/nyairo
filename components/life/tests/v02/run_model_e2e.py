"""v0.2 B4/E06/E10: the real-model end-to-end scenario for the booklet.

This is the **real model** evidence, kept separate from ``components/check_life_v02.py`` (which
uses an explicit test double for reproducible branch coverage).  The two must never be reported
as one thing.

What it does
------------
Uses the provider the instance is *already* configured with (resolved from the profile, never
hardcoded), then runs the real chain:

    round 1  AG-1 decides with the real model  -> AG-2 adopts -> AR-0 -> Supply v2
    round 2  reads the committed text back from the owner, continues the same work -> v3
    round 3  decides again with the real model: continue, pause or do nothing

Cost control
------------
``--max-calls`` (default 6) is a hard cap on provider calls across the whole run; the runner
stops before a round that would exceed it and says so.  ``--smoke`` makes exactly one call.

Usage (as the service principal, inside the isolated environment)
----------------------------------------------------------------
    NYA02_SUPPLY_SOCKET=... NYA02_SUBJECT=... NYA02_WORKSPACE_ID=... NYA02_ARTIFACT_GRANT=... \
    NYA02_PROFILE_HOME=/root/.nyairo NYA02_LIFE_STATE=/srv/nya02/life-state \
    python3 components/life/tests/v02/run_model_e2e.py smoke|run|ab [--max-calls N]

``ab`` is the B5-1 control: the same work, the same head and the same candidate
set, decided once with the admitted experience in view and once without it, then
written once with and once without.  ``--skip-content`` measures only the decision
half (two calls instead of four).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIFE_SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(LIFE_SCRIPTS))

REQUIRED_ENV = ("NYA02_SUPPLY_SOCKET", "NYA02_SUBJECT", "NYA02_WORKSPACE_ID",
                "NYA02_ARTIFACT_GRANT")


class CallLedger:
    """Counts provider calls across every client and refuses to exceed the cap."""

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self.used = 0

    def may_call(self) -> bool:
        return self.used < self.cap

    def note(self, label: str) -> None:
        self.used += 1
        print(f"  [model call {self.used}/{self.cap}] {label}", flush=True)


def main() -> int:
    # The provider key arrives in a 0600 file, never on the command line (see the launcher).
    import autonomous_life_assembly as _assembly

    _assembly.load_model_env_file()

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "run", "ab"))
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--skip-content", action="store_true",
                        help="A/B: measure only the decision half (2 calls instead of 4)")
    args = parser.parse_args()

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"NOT_RUN: isolated Life Supply environment not configured (missing {missing})")
        return 0

    from profile_model_config import describe_model_config, load_configured_model_config



    import agency_decision_ag1 as ag1
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    from agency_model_client import AgencyModelClient
    from autonomous_life_cycle import AutonomousLifeCycle
    from life_artifact_port import LifeArtifactPort
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    def _cfg_from_env() -> dict | None:
        """A pre-resolved config handed over by the launcher, so a profile the service
        identity cannot read (0600 under /root) is never copied anywhere."""
        base_url = os.environ.get("NYA02_MODEL_BASE_URL")
        model = os.environ.get("NYA02_MODEL_NAME")
        key = os.environ.get("NYA02_MODEL_API_KEY")
        if not (base_url and model and key):
            return None
        return {"provider": os.environ.get("NYA02_MODEL_PROVIDER") or "custom",
                "model": model, "base_url": base_url.rstrip("/"), "api_key": key,
                "key_env": "NYA02_MODEL_API_KEY", "api_key_present": True,
                "hermes_home": os.environ.get("NYA02_PROFILE_HOME"),
                "resolved_by": "launcher_env"}

    cfg = _cfg_from_env() or load_configured_model_config(
        hermes_home=os.environ.get("NYA02_PROFILE_HOME"))
    summary = describe_model_config(cfg)
    print("model config:", json.dumps(summary, ensure_ascii=False))
    if not summary["usable"]:
        print("NOT_RUN: no usable configured model provider in this profile "
              "(needs a base_url, a model and a key already present in the profile)")
        return 0
    from model_continuation_writer import ModelContinuationWriter

    ledger = CallLedger(args.max_calls)

    def counted_client(*, purpose="agency_cognition", timeout_s=30.0, max_output_tokens=512):
        """A client over the injected transport, refusing once the run's call budget is spent."""
        from profile_model_transport import OpenAICompatibleTransport

        transport = OpenAICompatibleTransport(
            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
            provider=cfg["provider"], timeout_s=timeout_s, purpose=purpose,
            request_json_object=True)
        original = transport.complete

        def complete(**call_kwargs):
            if not ledger.may_call():
                from agency_model_client import AgencyModelClientError
                raise AgencyModelClientError("RUN_CALL_BUDGET_EXHAUSTED",
                                             f"cap {ledger.cap} reached")
            ledger.note(purpose)
            return original(**call_kwargs)

        transport.complete = complete
        return AgencyModelClient(config=cfg, transport=transport, purpose=purpose,
                                 timeout_s=timeout_s, max_output_tokens=max_output_tokens)
    install_root = Path(os.environ.get("NYA02_LIFE_STATE") or tempfile.mkdtemp(
        prefix="v02-model-state-")).resolve()
    install_root.mkdir(parents=True, exist_ok=True)
    os.chmod(install_root, 0o700)
    os.environ["CHIYO_LIFE_INSTALL_ROOT"] = str(install_root)
    os.environ["CHIYO_LIFE_PRODUCTION_CUTOVER"] = "canonical"
    os.environ["LIFE_RUNTIME_ENABLED"] = "true"
    os.environ["AGENCY_ENABLED"] = "true"

    subject = os.environ["NYA02_SUBJECT"]
    workspace_id = os.environ["NYA02_WORKSPACE_ID"]
    grant_id = os.environ["NYA02_ARTIFACT_GRANT"]

    if args.mode == "smoke":
        print("[smoke] one real decision call")
        cognition = ProductionAgencyCognitionAdapter(
            client=counted_client(timeout_s=30.0, max_output_tokens=256), timeout_s=30.0,
            max_output_tokens=256)
        runtime, port, cycle, adapter = _build(
            lrp, ag2, install_root, subject, workspace_id, grant_id, cognition,
            ModelContinuationWriter(counted_client(purpose="artifact_edit",
                                                   max_output_tokens=256),
                                    max_new_chars=200))
        try:
            out = cycle.evaluate_once(trigger_ref="evt:b4:smoke:1")
            print("smoke outcome:", json.dumps(
                {k: out.get(k) for k in ("outcome", "detail", "decision", "reason_codes",
                                         "step_path", "action_status", "owner_status",
                                         "llm_calls")}, ensure_ascii=False))
            print(f"provider calls used: {ledger.used}/{ledger.cap}")
        finally:
            runtime.close()
        return 0

    if args.mode == "ab":
        return _ab(ledger, cfg, counted_client, lrp, ag2, install_root, subject,
                   workspace_id, grant_id, skip_content=args.skip_content)

    # ---- the real three-round scenario ------------------------------------------------
    print("[1] round one: create the work and write the first continuation")
    cognition = ProductionAgencyCognitionAdapter(
        client=counted_client(timeout_s=30.0, max_output_tokens=256), timeout_s=30.0,
        max_output_tokens=256)
    writer = ModelContinuationWriter(counted_client(purpose="artifact_edit",
                                                   max_output_tokens=512),
                                     max_new_chars=300)
    runtime, port, cycle, adapter = _build(
        lrp, ag2, install_root, subject, workspace_id, grant_id, cognition, writer)
    try:
        created = port.create_artifact(
            workspace_id=workspace_id, title="夜里的手记",
            content="# 夜里的手记\n\n第一行：今天把窗户开了一条缝。\n",
            grant_id=grant_id, operation_key="op:b4:create:1")
        print("  artifact:", created.artifact_id, "v1:", created.version_id)
        artifact_id = created.artifact_id
        r1 = cycle.evaluate_once(trigger_ref="evt:b4:round:1")
        _report_round("round 1", r1)
        v2 = (port.read_head(artifact_id) or {}).get("version_id")
        text2 = port.read_artifact(artifact_id).get("content") or ""
        print("  v2 text:", json.dumps(text2[-160:], ensure_ascii=False))
    finally:
        runtime.close()

    print("[2] round two: read the committed text back and continue the same work")
    cognition = ProductionAgencyCognitionAdapter(
        client=counted_client(timeout_s=30.0, max_output_tokens=256), timeout_s=30.0,
        max_output_tokens=256)
    writer = ModelContinuationWriter(counted_client(purpose="artifact_edit",
                                                   max_output_tokens=512),
                                     max_new_chars=300)
    runtime, port, cycle, adapter = _build(
        lrp, ag2, install_root, subject, workspace_id, grant_id, cognition, writer)
    try:
        before = port.read_artifact(artifact_id).get("content") or ""
        r2 = cycle.evaluate_once(trigger_ref="evt:b4:round:2")
        _report_round("round 2", r2)
        after = port.read_artifact(artifact_id).get("content") or ""
        head = port.read_head(artifact_id) or {}
        print("  version now:", head.get("version_number"), head.get("version_id"))
        print("  previous text preserved in the new version:", before.strip()[:40] in after)
        print("  changed:", after != before)
        print("  same Activity:", r1.get("activity_ref") == r2.get("activity_ref"),
              r2.get("activity_ref"))
    finally:
        runtime.close()

    print("[3] round three: decide again -- continue, pause or do nothing")
    cognition = ProductionAgencyCognitionAdapter(
        client=counted_client(timeout_s=30.0, max_output_tokens=256), timeout_s=30.0,
        max_output_tokens=256)
    writer = ModelContinuationWriter(counted_client(purpose="artifact_edit",
                                                   max_output_tokens=512),
                                     max_new_chars=300)
    runtime, port, cycle, adapter = _build(
        lrp, ag2, install_root, subject, workspace_id, grant_id, cognition, writer)
    try:
        r3 = cycle.evaluate_once(trigger_ref="evt:b4:round:3")
        _report_round("round 3", r3)
    finally:
        runtime.close()

    print()
    print("summary:")
    head = port.read_head(artifact_id) or {}
    print("  artifact:", artifact_id, "versions:", head.get("version_number"),
          "head:", head.get("version_id"))
    print("  activities:", list(runtime.canonical_state()["activities"].keys())
          if runtime else "closed")
    print("  provider calls used:", ledger.used, "/", ledger.cap)
    print("  step journal:", install_root / "life_step_journal.jsonl")
    return 0


# ---------------------------------------------------------------------------
# B5-1: the real-model A/B control
# ---------------------------------------------------------------------------

#: The one admitted experience both halves of the control see (group A) or do not
#: (group B).  It is the same text the deterministic memory check uses, so the two
#: evidence sets describe the same thing.
AB_EXPERIENCE = {
    "experience_id": "lexp:ab:1",
    "text": "上次写窗缝那一段，她觉得结尾收得太快了。",
    "artifact_ref": None,
    "version_ref": None,
}


class _ObservingCognition:
    """The real adapter, with the request recorded before it is sent.

    It adds no behaviour: the decision is still made by the real model, through the
    real adapter, validated by the real AG-1.  It only lets the control prove that
    the experience view actually reached the request.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.model_id = inner.model_id
        self.model_version = inner.model_version
        self.seen: list[dict] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.seen.append({
            "prior_experiences": [dict(x) for x in
                                  (getattr(request, "prior_experiences", ()) or ())],
            "legal_decision_enums": list(getattr(request, "legal_decision_enums", []) or []),
            "candidate_kinds": [c.get("candidate_kind")
                                for c in (getattr(request, "candidates", []) or [])],
        })
        return self._inner.invoke(request, schema_retry_index=schema_retry_index)


class _HeadUnchangedWriter:
    """A continuation writer that returns the head, so no version is written.

    The A/B must compare decisions at ONE head.  A writer that never changes the
    work keeps the artifact exactly where it was, so the second half of the control
    faces the same work, the same head and the same candidate set.
    """

    def next_content(self, point, *, observed_at: str) -> str:
        return point.content or ""


def _ab(ledger, cfg, counted_client, lrp, ag2, install_root, subject, workspace_id, grant_id,
        *, skip_content: bool = False):
    """Same work, same head, same candidate set -- with and without her memories."""

    from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    from autonomous_life_cycle import AutonomousLifeCycle
    from life_artifact_port import ArtifactResumePoint, LifeArtifactPort
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter
    from model_continuation_writer import ModelContinuationWriter

    port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
    created = port.create_artifact(
        workspace_id=workspace_id, title="夜里的手记",
        content="# 夜里的手记\n\n第一行：今天把窗户开了一条缝。\n",
        grant_id=grant_id, operation_key="op:b5ab:create:1")
    artifact_id = created.artifact_id
    print("artifact:", artifact_id, "v1:", created.version_id)

    def provider_for(with_experience: bool):
        if not with_experience:
            return None
        return lambda: [dict(AB_EXPERIENCE, artifact_ref=artifact_id)]

    def one_round(label: str, *, with_experience: bool) -> dict:
        # Each half gets its own Life state root.  Without that the first half's
        # adoption would leave a foreground Activity behind, and the second half
        # would face a different candidate set -- which would confound the control
        # with something other than the memory view.
        half_root = install_root / f"ab-{label.lower()}"
        half_root.mkdir(parents=True, exist_ok=True)
        os.chmod(half_root, 0o700)
        inner = ProductionAgencyCognitionAdapter(
            client=counted_client(timeout_s=30.0, max_output_tokens=256),
            timeout_s=30.0, max_output_tokens=256)
        cognition = _ObservingCognition(inner)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        adapter = SupplyManagedArtifactActionAdapter(
            port, grant_id=grant_id, workspace_id=workspace_id)
        runtime = lrp.build_production_life_runtime(
            half_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=half_root, artifact_action_adapter=adapter)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant_id, writer=_HeadUnchangedWriter(),
            experience_provider=provider_for(with_experience),
            max_rounds_per_day=4, enabled=True)
        try:
            head = port.read_head(artifact_id) or {}
            out = cycle.evaluate_once(trigger_ref=f"evt:b5ab:{label}")
        finally:
            runtime.close()
        seen = cognition.seen[-1] if cognition.seen else {}
        record = {
            "label": label,
            "with_experience": with_experience,
            "head_before": head.get("version_id"),
            "head_after": (port.read_head(artifact_id) or {}).get("version_id"),
            "decision": out.get("decision"),
            "reason_codes": out.get("reason_codes"),
            "short_rationale": out.get("short_rationale"),
            "selected_candidate_ref": out.get("selected_candidate_ref"),
            "outcome": out.get("outcome"),
            "experiences_in_request": [x.get("experience_id")
                                       for x in seen.get("prior_experiences", [])],
            "candidate_kinds": seen.get("candidate_kinds"),
        }
        print(f"  {label}: " + json.dumps(
            {k: record[k] for k in ("decision", "reason_codes", "short_rationale",
                                    "outcome", "experiences_in_request",
                                    "candidate_kinds")}, ensure_ascii=False))
        return record

    print("[A] AG-1 with the admitted experience in view")
    a = one_round("A", with_experience=True)
    print("[B] AG-1 with an empty view (the same writer, the same head)")
    b = one_round("B", with_experience=False)

    print("[C] content generation, same resume point, with and without")
    point = port.latest_resumable()
    if point is None:
        print("  no resumable artifact; skipping the content half")
        return 1
    if skip_content:
        a_text = b_text = None
        a_ids = b_ids = None
        print("  skipped (--skip-content); the decision half is what is being measured")
    else:
        a_writer = ModelContinuationWriter(
            counted_client(purpose="artifact_edit", max_output_tokens=512),
            experience_provider=provider_for(True), max_new_chars=200)
        a_text = a_writer.next_content(point, observed_at="2026-10-10T21:00:00+08:00")
        b_writer = ModelContinuationWriter(
            counted_client(purpose="artifact_edit", max_output_tokens=512),
            experience_provider=None, max_new_chars=200)
        b_text = b_writer.next_content(point, observed_at="2026-10-10T21:00:00+08:00")
        a_ids, b_ids = a_writer.last_experience_ids, b_writer.last_experience_ids

    print()
    print("result (reported as measured, not as expected):")
    print("  the two halves faced the same head:",
          a["head_before"] == b["head_before"] == b["head_after"],
          (a["head_before"], b["head_before"], b["head_after"]))
    print("  the two halves faced the same candidate set:",
          a["candidate_kinds"] == b["candidate_kinds"],
          (a["candidate_kinds"], b["candidate_kinds"]))
    print("  the experience reached AG-1 in A only:",
          a["experiences_in_request"] == [AB_EXPERIENCE["experience_id"]]
          and b["experiences_in_request"] == [],
          (a["experiences_in_request"], b["experiences_in_request"]))
    if not skip_content:
        print("  the same experience reached the content prompt in A only:",
              a_ids == [AB_EXPERIENCE["experience_id"]] and b_ids == [],
              (a_ids, b_ids))
    print("  the decision differed:", a["decision"] != b["decision"],
          (a["decision"], b["decision"]))
    print("  the reason codes differed:", a["reason_codes"] != b["reason_codes"],
          (a["reason_codes"], b["reason_codes"]))
    if not skip_content:
        print("  the produced text differed:", a_text != b_text)
        print("  A text:", json.dumps(a_text[-120:], ensure_ascii=False))
        print("  B text:", json.dumps(b_text[-120:], ensure_ascii=False))
    print("  provider calls used:", ledger.used, "/", ledger.cap)
    return 0


def _build(lrp, ag2, install_root, subject, workspace_id, grant_id, cognition, writer):
    """Assemble the runtime with the real cognition adapter and the real artifact adapter."""
    from autonomous_life_cycle import AutonomousLifeCycle
    from life_artifact_port import LifeArtifactPort
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
    adapter = SupplyManagedArtifactActionAdapter(port, grant_id=grant_id,
                                                workspace_id=workspace_id)
    runtime = lrp.build_production_life_runtime(
        install_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=install_root, artifact_action_adapter=adapter)
    cycle = AutonomousLifeCycle(
        runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
        artifact_grant_id=grant_id, writer=writer, max_rounds_per_day=4, enabled=True)
    return runtime, port, cycle, adapter


def _report_round(label: str, out: dict) -> None:
    keep = ("outcome", "detail", "decision", "reason_codes", "short_rationale",
            "step_path", "action_status", "owner_status", "owner_version_id",
            "progress_state", "checkpoint_ref", "llm_calls")
    print(f"  {label}:", json.dumps({k: out.get(k) for k in keep}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
