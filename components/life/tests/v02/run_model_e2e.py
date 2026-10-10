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
    python3 components/life/tests/v02/run_model_e2e.py smoke|run [--max-calls N]
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
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "run"))
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=3)
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
