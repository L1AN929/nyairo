#!/usr/bin/env python3
"""S10 demo: Request / Attempt / Receipt / Reconciliation are different facts.

What is real (local only, 0 product model calls, 0 network)
----------------------------------------------------------
* a real Life Supply owner on its own Unix socket, a real instance prepared by
  ``one_day_life.prepare``, the real production Life runtime and
  ``assemble_autonomous_life``;
* real AG-0/AG-1/AG-2, LR-2/LR-5, AR-0/AR-1 and real Supply receipts/versions/CAS;
* every Decision is a scripted double that only selects inside the real AG-1 legal set, and
  every content answer is a scripted double.  No product model is called.

The chain demonstrates
----------------------
1. Formal ``evaluate_once`` success: a real START commits a real version; the Action has one
   attempt, one owner receipt and one observed result, and a second submit of the same logical
   action is an idempotent replay with no new attempt and no new version.
2. A real write whose answer is lost is an ``UNKNOWN`` Action, not a failure.  After an
   ordered same-root restart, ``reconcile_unknown_step`` resolves it through the **original
   owner operation**; the version and operation counts do not grow a second time.
3. An AR-0 request is frozen at ``PREPARED`` and is *not* shown as sent; a same-key/different
   payload is refused at prepare and at submit; the same key/payload replay writes nothing.
4. Content written by a *different real writer* is never attributed to this action: the
   controlled transport seam returns no mapping, and AR-0 keeps the action ``UNKNOWN`` with
   zero result evidence.  A hash match is not causation.
5. Named gaps: AR-0 has no child/DAG/PARTIAL outcome vector and no bounded query scheduler
   (``QUIESCED_UNRESOLVED``); these are printed as ``NOT_IMPLEMENTED``/STOP, not invented.
6. The read surface is ``scripts/s10_action_review_cli.py``, invoked here as a subprocess so
   the actual user entry (not an internal helper) is what is checked.

Boundaries: not G1, not 24h, not a crash matrix; the restart is an ordered stop/start.  The
World is not used.  ``UNKNOWN`` is preserved, never rewritten as ``FAILED``.  The formal
``evaluate_once`` rounds use a fixture business clock (mechanical time); the direct AR-0
calls use AR-0's own wall-clock stamps.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s10_action_outcomes_demo.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "phone"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_outcome_review as review  # noqa: E402
import action_reality_ledger as ar0  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402
import v05_restart_continuation_smoke as restart  # noqa: E402

BASE_TIME = datetime.now().astimezone()
_ROUND = [0]
FAILURES: list[str] = []


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-74s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1800])
    return ok


def next_instant() -> str:
    """A fixture business clock: one step per round, so silence TTLs never freeze a round."""

    _ROUND[0] += 1
    return (BASE_TIME + timedelta(minutes=400 * _ROUND[0])).isoformat()


def bare(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def target_ref(value: Any) -> str:
    return "artifact:%s" % bare(value)


def head(chain, artifact_id: Any) -> str:
    return str(chain.port.resume_point(str(artifact_id)).head_version_id or "")


def version_count(chain, artifact_id: Any) -> int:
    return int(chain.port.list_versions(str(artifact_id), limit=50)["total_versions"])


def op_key_for(action: Mapping[str, Any]) -> str:
    return maaa.SupplyManagedArtifactActionAdapter.operation_key_for(
        str(action.get("idempotency_key") or ""))


def action_row(chain, action_id: str) -> dict[str, Any]:
    return dict(chain.runtime.action_read.get_action(str(action_id)) or {})


# ---------------------------------------------------------------------------------------------
# the real user entry under test
# ---------------------------------------------------------------------------------------------


def run_cli(state_root: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "s10_action_review_cli.py"),
               "--state-root", str(state_root), "--format", "json", *extra]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        payload = {"ok": False, "reason": "CLI_OUTPUT_NOT_JSON",
                   "stdout": completed.stdout[-600:], "stderr": completed.stderr[-600:]}
    return int(completed.returncode), payload


def cli_action(envelope: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    for row in envelope.get("actions") or ():
        if str(row.get("action_id")) == str(action_id):
            return dict(row)
    return {}


# ---------------------------------------------------------------------------------------------
# formal evaluate_once helpers (real AG-1/AG-2, scripted decision/content)
# ---------------------------------------------------------------------------------------------


def candidate_refs(chain, observed_at: str) -> list[str]:
    return [str(ref) for ref in chain.runtime.candidate_materializer
            .build_candidate_set(observed_at=observed_at).candidate_refs]


def candidate_payload(chain, ref: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})


def find_work_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload: Mapping[str, Any]) -> bool:
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    for ref in candidate_refs(chain, observed_at):
        if matches(candidate_payload(chain, ref)):
            return ref
    return None


def find_current_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    """CONTINUE_CURRENT for this work, preferring the candidate that names the fresh head."""

    rows = []
    for ref in candidate_refs(chain, observed_at):
        payload = candidate_payload(chain, ref)
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            rows.append((ref, payload))
    if not rows:
        return None
    wanted = head(chain, artifact_id).split(":")[-1]
    if wanted:
        for ref, payload in rows:
            if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return rows[-1][0]


def choose(chain, decision: str, candidate_id: Optional[str]) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def round_out(chain, tag: str, observed_at: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:s10:%s" % tag, observed_at=observed_at, act=True) or {})


def brief(out: Mapping[str, Any]) -> dict[str, Any]:
    return {key: out.get(key) for key in (
        "outcome", "decision", "adoption_status", "step_path", "action_id", "activity_ref",
        "failure_class", "detail", "error", "exit")}


# ---------------------------------------------------------------------------------------------
# a controlled transport seam for the "no mapping" case
# ---------------------------------------------------------------------------------------------


class NoMappingArtifactAdapter:
    """Controlled transport seam: the request is never sent and no owner mapping is claimed.

    Only the transport and the reconcile query are replaced; prepare validation and the
    operation-key identity are the real adapter's own.  This is a test double, not a second
    owner: it never writes, and the UNKNOWN it produces is the ledger's own state.
    """

    capabilities = maaa.SupplyManagedArtifactActionAdapter.capabilities

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.submit_call_count = 0

    def validate_prepare(self, action: Mapping[str, Any], payload: Mapping[str, Any]):
        return self.inner.validate_prepare(action, payload)

    def submit(self, *, executor_capability: Any, action: Mapping[str, Any],
               attempt: Mapping[str, Any], payload: Mapping[str, Any],
               fault_hook: Optional[Any] = None):
        executor_capability.verify()
        self.submit_call_count += 1
        key = op_key_for(action)
        return ar0.AdapterSubmissionOutcome(
            transport_result=ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
            provider_operation_ref=key,
            error_class="CONTROLLED_NO_REQUEST_SENT",
            error_ref="err:%s:controlled" % key)

    def query_status_for_reconcile(self, *, action: Mapping[str, Any],
                                   attempt: Optional[Mapping[str, Any]]):
        return {"strategy": ar0.RECON_STRATEGY_PROVIDER_LOOKUP,
                "result": ar0.RECON_RESULT_STILL_UNKNOWN, "evidence_refs": []}

    def observed_result_for_action(self, *, action: Mapping[str, Any]):
        return None


# ---------------------------------------------------------------------------------------------
# chain lifecycle
# ---------------------------------------------------------------------------------------------


def close_chain(chain) -> dict[str, Any]:
    report = restart.close_chain(chain)
    check("the owner stopped with drain + its own socket guard",
          report.get("closed") is True and report.get("service_stopped") is True
          and report.get("service_drained") is True
          and str(report.get("service_socket_handling") or "") in {
              "STALE_SOCKET_REMOVED", "NO_SOCKET", "ABSENT", "CLEAN"},
          report)
    return report


def main() -> int:
    chain = base.build_chain("s10-action-outcomes")
    work = str(chain.data["artifact_id"])
    summary: dict[str, Any] = {"root": str(chain.root), "work": target_ref(work), "rounds": {}}

    # ================================================================================
    # 1. formal success through evaluate_once
    # ================================================================================
    say("\n== 1. formal evaluate_once: a real START -> Action SUCCEEDED ==")
    ts = next_instant()
    choose(chain, "START", find_work_candidate(chain, work, ts))
    started = round_out(chain, "formal-start", ts)
    summary["rounds"]["formal_start"] = brief(started)
    action_ok = str(started.get("action_id") or "")
    row_ok = action_row(chain, action_ok) if action_ok else {}
    versions_after_start = version_count(chain, work)
    check("the real START committed a version and the Action is SUCCEEDED",
          started.get("outcome") == "ACTED" and started.get("decision") == "START"
          and row_ok.get("status") == "SUCCEEDED" and versions_after_start >= 2,
          {"round": brief(started), "action": row_ok, "versions": versions_after_start})
    check("the successful Action has one attempt, one owner receipt and one observed result",
          int(row_ok.get("attempt_count") or 0) == 1
          and len(chain.runtime.action_read.list_attempts(action_ok)) == 1
          and len(chain.runtime.action_read.list_receipts(action_ok)) == 1
          and len(chain.runtime.action_read.list_result_evidences(action_ok)) == 1,
          {"attempts": chain.runtime.action_read.list_attempts(action_ok),
           "receipts": chain.runtime.action_read.list_receipts(action_ok),
           "evidence": chain.runtime.action_read.list_result_evidences(action_ok)})

    # same logical action, same frozen payload: a replay writes nothing new
    replay = chain.runtime.action_command.submit_action(
        action_id=action_ok, executor_capability=chain.runtime.executor_capability)
    after_replay = action_row(chain, action_ok)
    check("re-submitting the same action is an idempotent replay (no attempt, no version)",
          replay.get("idempotent_replay") is True
          and int(after_replay.get("attempt_count") or 0) == 1
          and version_count(chain, work) == versions_after_start,
          {"replay": {k: replay.get(k) for k in ("idempotent_replay",)},
           "attempts": after_replay.get("attempt_count"),
           "versions": version_count(chain, work)})

    code, envelope = run_cli(chain.runtime.state_root)
    cli_ok = cli_action(envelope, action_ok)
    check("the CLI (real user entry) shows SUCCEEDED with 1 attempt / 1 receipt / 1 evidence",
          code == 0 and cli_ok.get("status") == "SUCCEEDED"
          and cli_ok.get("phase") == review.PHASE_CLOSED
          and cli_ok.get("outcome") == review.OUTCOME_SUCCEEDED
          and len(cli_ok.get("attempts") or ()) == 1
          and len(cli_ok.get("receipts") or ()) == 1
          and len(cli_ok.get("result_evidence") or ()) == 1,
          {"code": code, "row": cli_ok, "envelope": envelope.get("reason")})

    # ================================================================================
    # 2. a lost answer is UNKNOWN, then reconciled by the original operation
    # ================================================================================
    say("\n== 2. owner wrote, answer lost: Action UNKNOWN (not FAILED) ==")
    ts = next_instant()
    chain.runtime.file_adapter.fault_injection_drop_response_after_write = True
    choose(chain, "CONTINUE", find_current_candidate(chain, work, ts))
    lost = round_out(chain, "lost-answer", ts)
    summary["rounds"]["lost_answer"] = brief(lost)
    action_unknown = str(lost.get("action_id") or "")
    row_unknown = action_row(chain, action_unknown) if action_unknown else {}
    versions_after_lost = version_count(chain, work)
    op_key_unknown = op_key_for(row_unknown)
    check("the lost answer is an Action UNKNOWN and the owner write really happened",
          lost.get("outcome") == "STEP_UNKNOWN"
          and row_unknown.get("status") == "UNKNOWN"
          and versions_after_lost == versions_after_start + 1,
          {"round": brief(lost), "action": row_unknown,
           "versions": versions_after_lost})
    check("the owner has one operation for the unknown action, and no recorded receipt",
          chain.port.operation_row(op_key_unknown) is not None
          and len(chain.runtime.action_read.list_receipts(action_unknown)) == 0
          and len(chain.runtime.action_read.list_result_evidences(action_unknown)) == 0,
          {"operation": chain.port.operation_row(op_key_unknown)})

    code, envelope = run_cli(chain.runtime.state_root)
    cli_unknown = cli_action(envelope, action_unknown)
    check("the CLI shows UNKNOWN/UNRESOLVED with zero receipts and NOT_RECORDED not_sent",
          code == 0 and cli_unknown.get("status") == "UNKNOWN"
          and cli_unknown.get("phase") == review.PHASE_UNRESOLVED
          and cli_unknown.get("outcome") == review.OUTCOME_UNKNOWN
          and len(cli_unknown.get("receipts") or ()) == 0
          and (cli_unknown.get("not_sent") or {}).get("value") == review.NOT_RECORDED,
          {"code": code, "row": cli_unknown})

    # ---- ordered same-root restart while the Action is UNKNOWN -------------------------
    say("\n== restart: the same root re-assembled with one UNKNOWN action ==")
    ok_before = action_row(chain, action_ok)
    ok_attempts_before = len(chain.runtime.action_read.list_attempts(action_ok))
    close_chain(chain)
    chain = restart.open_chain(chain.root, "s10-p2")
    reopened_ok = action_row(chain, action_ok)
    reopened_unknown = action_row(chain, action_unknown)
    check("the unresolved Action survived the restart and is still UNKNOWN",
          reopened_unknown.get("status") == "UNKNOWN"
          and int(reopened_unknown.get("attempt_count") or 0) == 1
          and variant_ids(reopened_unknown) == variant_ids(row_unknown),
          {"before": variant_ids(row_unknown), "after": variant_ids(reopened_unknown)})
    check("reading back does not replay the old success (same status/attempts/versions)",
          reopened_ok.get("status") == "SUCCEEDED"
          and int(reopened_ok.get("attempt_count") or 0) == int(ok_before.get("attempt_count") or 0)
          and len(chain.runtime.action_read.list_attempts(action_ok)) == ok_attempts_before
          and version_count(chain, work) == versions_after_lost,
          {"before": variant_ids(ok_before), "after": variant_ids(reopened_ok),
           "versions": version_count(chain, work)})

    op_row_before = chain.port.operation_row(op_key_unknown)
    reconciled = chain.cycle.reconcile_unknown_step(
        action_id=action_unknown, reason="S10 demo lost answer")
    summary["reconcile"] = reconciled
    resolved = action_row(chain, action_unknown)
    check("reconciliation used the original operation and confirmed SUCCEEDED",
          reconciled.get("ok") is True
          and (reconciled.get("reconciliation") or {}).get("result") == "CONFIRMED_SUCCEEDED"
          and reconciled.get("reason_code") == "RECONCILED_SUCCEEDED"
          and resolved.get("status") == "SUCCEEDED",
          reconciled)
    op_row_after = chain.port.operation_row(op_key_unknown)
    check("reconciliation wrote no second version, attempt or owner operation",
          version_count(chain, work) == versions_after_lost
          and int(resolved.get("attempt_count") or 0) == 1
          and op_row_after == op_row_before,
          {"versions": version_count(chain, work),
           "attempts": resolved.get("attempt_count"),
           "op_before": op_row_before, "op_after": op_row_after})
    check("the reconciled action has observed evidence but still no invented receipt",
          len(chain.runtime.action_read.list_result_evidences(action_unknown)) == 1
          and len(chain.runtime.action_read.list_receipts(action_unknown)) == 0,
          {"evidence": chain.runtime.action_read.list_result_evidences(action_unknown),
           "receipts": chain.runtime.action_read.list_receipts(action_unknown)})

    code, envelope = run_cli(chain.runtime.state_root)
    cli_reconciled = cli_action(envelope, action_unknown)
    check("the CLI shows the one reconciliation result and the observed evidence",
          code == 0 and cli_reconciled.get("status") == "SUCCEEDED"
          and len(cli_reconciled.get("reconciliation") or ()) == 1
          and (cli_reconciled.get("reconciliation") or [{}])[0].get("result")
          == "CONFIRMED_SUCCEEDED"
          and (cli_reconciled.get("result_evidence") or [{}])[0].get("verified") is True,
          {"code": code, "row": cli_reconciled})

    # ================================================================================
    # 3. AR-0 layer: frozen request, payload conflict, replay, no hash attribution
    # ================================================================================
    say("\n== 3. AR-0 layer: PREPARED is not sent; same key/different payload is refused ==")
    content_one = "S10 直接动作一：这条由正式 AR-0 写入。\n"
    key_one = "s10:direct:one"
    payload_one = {"op": maaa.OP_COMMIT_VERSION, "artifact_id": work,
                   "expected_head": head(chain, work), "content": content_one}
    proposed = chain.runtime.action_command.propose_action(
        action_kind=ar0.ACTION_KIND_FILE, target_ref=target_ref(work),
        payload_ref="pay:s10:direct:one", payload_data=payload_one,
        idempotency_key=key_one, source_refs=["evt:s10:direct:one"])
    action_one = str(proposed["action"]["action_id"])
    prepared = chain.runtime.action_command.prepare_action(action_id=action_one)
    summary["actions"] = {"success": action_ok, "unknown_reconciled": action_unknown,
                          "direct_one": action_one}
    check("a prepared Action is PREPARED, has zero attempts, and is not shown as sent",
          prepared.get("status") == "PREPARED"
          and int(prepared.get("attempt_count") or 0) == 0
          and len(chain.runtime.action_read.list_attempts(action_one)) == 0,
          prepared)
    code, envelope = run_cli(chain.runtime.state_root)
    cli_prepared = cli_action(envelope, action_one)
    check("the CLI labels the prepared request REQUESTED_FROZEN/PENDING with no attempt",
          code == 0 and cli_prepared.get("phase") == review.PHASE_REQUESTED_FROZEN
          and cli_prepared.get("outcome") == review.OUTCOME_PENDING
          and not (cli_prepared.get("attempts") or ())
          and (cli_prepared.get("facts") or {}).get("dispatch_admitted") == review.NOT_RECORDED,
          {"code": code, "row": cli_prepared})

    different_payload = dict(payload_one)
    different_payload["content"] = "S10 不同载荷：同一 key 不允许改写。\n"
    prepare_refusal = submit_refusal = None
    try:
        chain.runtime.action_command.prepare_action(
            action_id=action_one, payload_override=different_payload)
    except Exception as exc:  # expected: frozen payload cannot be rewritten
        prepare_refusal = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    try:
        chain.runtime.action_command.submit_action(
            action_id=action_one, executor_capability=chain.runtime.executor_capability,
            payload_check=different_payload)
    except Exception as exc:  # expected: submission-time tamper check
        submit_refusal = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    check("the same key with a different payload is refused at prepare and at submit",
          prepare_refusal is not None and "PayloadTamperError" in prepare_refusal
          and submit_refusal is not None and "PayloadTamperError" in submit_refusal
          and action_row(chain, action_one).get("status") == "PREPARED",
          {"prepare": prepare_refusal, "submit": submit_refusal,
           "status": action_row(chain, action_one).get("status")})

    versions_before_direct = version_count(chain, work)
    submitted_one = chain.runtime.action_command.submit_action(
        action_id=action_one, executor_capability=chain.runtime.executor_capability)
    replay_one = chain.runtime.action_command.submit_action(
        action_id=action_one, executor_capability=chain.runtime.executor_capability)
    check("the correctly frozen payload is dispatched once; a replay adds no attempt/version",
          submitted_one.get("action", {}).get("status") == "SUCCEEDED"
          and replay_one.get("idempotent_replay") is True
          and version_count(chain, work) == versions_before_direct + 1
          and action_row(chain, action_one).get("attempt_count") == 1,
          {"submitted": submitted_one.get("action", {}).get("status"),
           "replay": replay_one.get("idempotent_replay"),
           "versions": version_count(chain, work),
           "attempts": action_row(chain, action_one).get("attempt_count")})

    # ---- same content, another real writer: hash equality is not this action's success ----
    say("\n== 3b. same content by another real writer is not attributed to this action ==")
    content_two = "S10 同内容：另一个真实写者先写了这一句。\n"
    point = chain.port.resume_point(work)
    other = chain.port.commit_version(
        artifact_id=work, expected_head=str(point.head_version_id or ""),
        content=content_two, grant_id=str(chain.data["artifact_grant_id"]),
        operation_key=lap.operation_key_for("s10-demo", "other-writer", "same-content"))
    check("a different real writer really committed that content",
          getattr(other, "effect_state", None) == lap.EffectState.APPLIED, other.to_dict())
    head_after_other = chain.port.read_head(work) or {}
    other_op_key = str(head_after_other.get("operation_key") or "")
    wanted_hash = hashlib.sha256(content_two.encode("utf-8")).hexdigest()
    check("the owner head has that exact content, under the other writer's operation key",
          str(head_after_other.get("content_sha256") or "") == wanted_hash
          and other_op_key
          and other_op_key != maaa.SupplyManagedArtifactActionAdapter.operation_key_for(
              "s10:direct:two"),
          {"head": head_after_other, "other_op_key": other_op_key})

    controlled = NoMappingArtifactAdapter(chain.runtime.file_adapter)
    chain.runtime.action_command.register_adapter(ar0.ACTION_KIND_FILE, controlled)
    try:
        key_two = "s10:direct:two"
        payload_two = {"op": maaa.OP_COMMIT_VERSION, "artifact_id": work,
                       "expected_head": head(chain, work), "content": content_two}
        proposed_two = chain.runtime.action_command.propose_action(
            action_kind=ar0.ACTION_KIND_FILE, target_ref=target_ref(work),
            payload_ref="pay:s10:direct:two", payload_data=payload_two,
            idempotency_key=key_two, source_refs=["evt:s10:direct:two"])
        action_two = str(proposed_two["action"]["action_id"])
        chain.runtime.action_command.prepare_action(action_id=action_two)
        chain.runtime.action_command.submit_action(
            action_id=action_two, executor_capability=chain.runtime.executor_capability)
        row_two = action_row(chain, action_two)
        op_two = op_key_for(row_two)
        check("the controlled transport lost the answer: the Action is UNKNOWN",
              row_two.get("status") == "UNKNOWN", row_two)
        check("the owner has no operation mapping for this action, and no evidence was minted",
              chain.port.operation_row(op_two) is None
              and len(chain.runtime.action_read.list_result_evidences(action_two)) == 0,
              {"operation": chain.port.operation_row(op_two)})

        first = chain.runtime.action_command.reconcile_action(action_id=action_two)
        second = chain.runtime.action_command.reconcile_action(action_id=action_two)
        row_two_after = action_row(chain, action_two)
        summary["actions"]["no_mapping"] = action_two
        check("repeated reconcile stays STILL_UNKNOWN: content/hash never becomes SUCCEEDED",
              first["reconciliation"]["result"] == "STILL_UNKNOWN"
              and second["reconciliation"]["result"] == "STILL_UNKNOWN"
              and row_two_after.get("status") == "UNKNOWN"
              and int(row_two_after.get("attempt_count") or 0) == 1
              and len(chain.runtime.action_read.list_reconciliations(action_two)) == 2
              and len(chain.runtime.action_read.list_result_evidences(action_two)) == 0,
              {"first": first.get("reconciliation"), "second": second.get("reconciliation"),
               "row": row_two_after})
        check("querying again did not resend or change the owner head",
              str((chain.port.read_head(work) or {}).get("version_id") or "") == head(chain, work),
              chain.port.read_head(work))
        code, envelope = run_cli(chain.runtime.state_root)
        cli_two = cli_action(envelope, action_two)
        check("the CLI keeps it UNKNOWN with two reconciliation rows and zero evidence",
              code == 0 and cli_two.get("status") == "UNKNOWN"
              and cli_two.get("phase") == review.PHASE_RECONCILING
              and len(cli_two.get("reconciliation") or ()) == 2
              and not (cli_two.get("result_evidence") or ()),
              {"code": code, "row": cli_two})
    finally:
        chain.runtime.action_command.register_adapter(
            ar0.ACTION_KIND_FILE, chain.runtime.file_adapter)

    # ================================================================================
    # 4. named gaps (STOP, not invented)
    # ================================================================================
    say("\n== 4. named gaps: no child/DAG/PARTIAL vector, no bounded query scheduler ==")
    record_fields = {field.name for field in dataclasses.fields(ar0.ActionRecord)}
    child_like = {name for name in record_fields
                  if any(token in name for token in ("child", "dag", "parent_action", "partial"))}
    check("AR-0 has no PARTIAL status and no child/DAG fields on ActionRecord",
          "PARTIAL" not in ar0.VALID_ACTION_STATUSES and not child_like,
          {"child_like": sorted(child_like), "statuses": sorted(ar0.VALID_ACTION_STATUSES)})
    check("AR-0 has no QUIESCED_UNRESOLVED status and no reconciliation policy class",
          "QUIESCED_UNRESOLVED" not in ar0.VALID_ACTION_STATUSES
          and not hasattr(ar0, "ReconciliationPolicy"),
          {"statuses": sorted(ar0.VALID_ACTION_STATUSES)})
    summary["named_gaps"] = review.named_gaps()
    say("STOP_DEFERRED child_dag_partial: NOT_IMPLEMENTED at AR-0 (no child actions/DAG/"
        "PARTIAL vector; AG-2 INTEGRATION_STATUS_PARTIAL is an integration-transaction "
        "status, not this outcome vector). No parallel child store created.")
    say("STOP_DEFERRED bounded_query_scheduler: NOT_IMPLEMENTED (one deterministic reconcile "
        "per call; no delaysMs/maxQueries/QUIESCED_UNRESOLVED). No timer or retry counter added.")

    final_versions = version_count(chain, work)
    _code, final_envelope = run_cli(chain.runtime.state_root)
    try:
        close_chain(chain)
    except Exception as exc:  # printed, not hidden
        say("  close error: %s: %s" % (type(exc).__name__, str(exc)[:200]))
    summary["ok"] = not FAILURES
    summary["counts"] = {
        "versions": final_versions,
        "actions_seen": len(final_envelope.get("actions") or ()),
    }
    say("\n" + json.dumps(summary, ensure_ascii=False, default=str)[:4000])
    if FAILURES:
        say("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    say("S10_ACTION_OUTCOMES_DEMO_OK")
    return 0


def variant_ids(row: Mapping[str, Any]) -> dict[str, Any]:
    """Identity/status facts that must survive a read/restart without being replayed."""

    return {key: row.get(key) for key in (
        "action_id", "idempotency_key", "status", "attempt_count", "revision",
        "last_attempt_ref", "last_receipt_ref", "unknown_since")}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed demo, never silent
        say("DEMO_FAIL: %s: %s" % (type(exc).__name__, str(exc)[:400]))
        raise SystemExit(1)
