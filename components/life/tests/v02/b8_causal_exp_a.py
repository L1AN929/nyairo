#!/usr/bin/env python3
"""B8-5 experiment A: does the real state of her own work change what she prepares next?

The question the experiment must answer is not "does she feel different" but the narrower,
checkable one: *given two isolated instances whose works really differ, does each instance's
prepared step reflect its own work -- its own head version, its own checkpoint basis and its own
text -- and never the other instance's?*

Design
------
Three trials.  Each trial has two isolated groups:

* **A** -- a real work with a deeper history (head V2 / V3 / V4) whose tail is the part that
  needs work.
* **B** -- the same kind of work with head V1 and a marker in a different place.

Every difference is established by legal owner operations: ``create_artifact`` and
``commit_version`` over the Life Supply socket, as the service principal, under a real
``COMMIT_MANAGED_ARTIFACT`` grant.  Nothing is written into a database by hand.

Each group then runs the *formal* chain twice:

    round 1  Artifact Read -> AG-0 -> AG-1 -> AG-2 -> Action Preparation (and execution)
    round 2  the same chain on a fresh Life state root, facing the work round 1 just changed

Round 2 is the "next round" the work order asks about: its prepared step must reflect the work as
*that* group left it, which is a different version in every group.

What is measured
----------------
* the input work: head version id, version number, content length, and the content tail
* the prepared step: the artifact id, the compare-and-swap ``expected_head``, and the SHA-256 of
  the text the writer was handed -- which the owner receipt must echo as ``source_hash``
* the owner's answer: the new version id and number, or the refusal
* the LR-5 checkpoint the round recorded
* every version id and artifact id of the *other* group must be absent from this group's record

The decision is a controlled stand-in, so no model is called and nothing is paid for.  The work
order explicitly allows this: it says a different START/CONTINUE must not be forced, and the pass
condition is about the prepared step, not about the model's taste.

Environment
-----------
``NYA02_SUPPLY_SOCKET``    the real Life Supply owner
``NYA02_CAUSAL_ROOT``      a 0700 directory the harness may own
``NYA02_CAUSAL_GROUPS``    JSON: {"T1A": {"subject":..., "workspace_id":..., "grant_id":...}, ...}
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

FAILURES: list[str] = []
GROUPS_ENV = "NYA02_CAUSAL_GROUPS"
ROOT_ENV = "NYA02_CAUSAL_ROOT"


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


# ---------------------------------------------------------------------------------------
# The three trials.  A always carries a deeper history and a tail marker; B always has one
# version and a marker in a different place.  The depth, the title and the wording differ,
# so a single lucky sample cannot carry the result.
# ---------------------------------------------------------------------------------------
TRIALS: tuple[dict, ...] = (
    {
        "trial": "T1",
        "A": {"title": "夜里的手记", "marker": "结尾还太急", "contents": [
            "# 夜里的手记\n\n第一段：把窗户开了一条缝。\n",
            "# 夜里的手记\n\n第一段：把窗户开了一条缝。\n\n第二段：写到了结尾，结尾还太急。\n",
        ]},
        "B": {"title": "夜里的手记", "marker": "这里还没写完", "contents": [
            "# 夜里的手记\n\n第一段：把窗户开了一条缝，这里还没写完。\n",
        ]},
    },
    {
        "trial": "T2",
        "A": {"title": "清晨的清单", "marker": "最后一条要重写", "contents": [
            "# 清晨的清单\n\n一：烧水。\n",
            "# 清晨的清单\n\n一：烧水。\n\n二：把杯子洗干净。\n",
            "# 清晨的清单\n\n一：烧水。\n\n二：把杯子洗干净。\n\n三：写下来。最后一条要重写。\n",
        ]},
        "B": {"title": "清晨的清单", "marker": "第二行缺了一笔", "contents": [
            "# 清晨的清单\n\n一：烧水。第二行缺了一笔。\n",
        ]},
    },
    {
        "trial": "T3",
        "A": {"title": "巷口的旧画", "marker": "落款写错了", "contents": [
            "# 巷口的旧画\n\n起稿。\n",
            "# 巷口的旧画\n\n起稿。\n\n上色一遍。\n",
            "# 巷口的旧画\n\n起稿。\n\n上色一遍。\n\n把远处的墙压暗。\n",
            "# 巷口的旧画\n\n起稿。\n\n上色一遍。\n\n把远处的墙压暗。\n\n落款写错了。\n",
        ]},
        "B": {"title": "巷口的旧画", "marker": "左边还空着", "contents": [
            "# 巷口的旧画\n\n起稿。左边还空着。\n",
        ]},
    },
)


class _RecordingWriter:
    """Records the exact resume point it is handed, then writes real new text.

    The point it records *is* the prepared step's input: the artifact, the head the step is
    based on, and the bytes the model was asked to continue.  Returning new text makes the
    owner commit a real next version, so the round has a real result to compare.
    """

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.points: list[dict] = []

    def next_content(self, point, *, observed_at: str) -> str:
        content = point.content or ""
        self.points.append({
            "artifact_id": point.artifact_id,
            "head_version_id": point.head_version_id,
            "version_number": point.version_number,
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_tail": content[-40:],
        })
        written = content + f"\n{self.suffix}\n"
        self.points[-1]["written_sha256"] = hashlib.sha256(written.encode("utf-8")).hexdigest()
        self.points[-1]["written_chars"] = len(written)
        return written


class _StartCognition:
    """A controlled cognition output.  It decides nothing; it answers the artifact candidate."""

    def __init__(self, *, decision: str = "START") -> None:
        self.decision = decision
        self.calls = 0
        self.seen: list[dict] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        allowed = [str(x) for x in (getattr(request, "allowed_reason_codes", []) or [])]
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        chosen = candidates[0].get("candidate_id") if candidates else None
        self.seen.append({
            "candidate_kinds": [c.get("candidate_kind") for c in candidates],
            "candidate_ids": [c.get("candidate_id") for c in candidates],
            "selected_candidate_id": chosen,
            "prior_experiences": [dict(x) for x in (getattr(request, "prior_experiences", ()) or ())],
        })
        raw = {"decision": self.decision, "selected_candidate_id": chosen,
               "reason_codes": [allowed[0] if allowed else "DECISION_RECORDED"],
               "short_rationale": "controlled stand-in"}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:b8-causal-a", model_id="b8-causal-a",
            model_version="b8", raw_output=raw, latency_ms=0)


def _establish_work(port, entry: dict, spec: dict, *, tag: str) -> tuple[str, list[dict]]:
    """Build the group's real work with legal owner operations only."""
    created = port.create_artifact(
        workspace_id=entry["workspace_id"], title=spec["title"],
        content=spec["contents"][0], grant_id=entry["grant_id"],
        # The owner dedupes on the operation key, so it must name this instance's own intended
        # effect: a second run of the harness is a different work, not a replay of the first.
        operation_key=f"op:ca:{tag}:{entry['subject']}:create:1")
    artifact_id = str(created.artifact_id)
    heads = [dict(port.read_head(artifact_id) or {})]
    for index, content in enumerate(spec["contents"][1:], start=2):
        port.commit_version(
            artifact_id=artifact_id, expected_head=heads[-1].get("version_id"),
            content=content, grant_id=entry["grant_id"],
            operation_key=f"op:ca:{tag}:{entry['subject']}:v{index}")
        heads.append(dict(port.read_head(artifact_id) or {}))
    return artifact_id, heads


def _run_round(*, label: str, entry: dict, artifact_id: str, port, root: Path,
               writer, cognition) -> dict:
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
        install_root=root, artifact_action_adapter=adapter)
    try:
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=entry["subject"],
            workspace_id=entry["workspace_id"], artifact_grant_id=entry["grant_id"],
            writer=writer, enabled=True, max_rounds_per_day=4)
        head_before = dict(port.read_head(artifact_id) or {})
        out = cycle.evaluate_once(trigger_ref=f"evt:ca:{label}")
        head_after = dict(port.read_head(artifact_id) or {})
        # The checkpoint lives in LR-2/LR-5, which own it.  Read it there rather than
        # inferring it from the round report, so the recorded position is the owner's.
        activity = dict(runtime.activity_read.get_activity(str(out.get("activity_ref") or "")) or {})
        progress = dict(runtime.progress_read.get_progress_state(str(out.get("activity_ref") or "")) or {})
    finally:
        runtime.close()
    return {"head_before": head_before, "head_after": head_after, "out": out,
            "activity": activity, "progress": progress}


def _receipt_facts(out: dict) -> dict:
    receipt = out.get("step_receipt") or {}
    owner = out.get("owner_receipt")
    return {
        "artifact_id": receipt.get("artifact_id"),
        "expected_head": receipt.get("expected_head"),
        "effect_state": receipt.get("effect_state"),
        "status": receipt.get("status"),
        "version_id": receipt.get("version_id"),
        "version_number": receipt.get("version_number"),
        "owner_status": out.get("owner_status"),
        "owner_version_id": out.get("owner_version_id"),
        "owner_version_created": out.get("owner_version_created"),
        "owner_source_hash": (owner or {}).get("source_hash") if isinstance(owner, dict) else None,
        "checkpoint_ref": out.get("checkpoint_ref"),
        "progress_state": out.get("progress_state"),
    }


def _compact(round_result: dict, writer: _RecordingWriter, cognition: _StartCognition) -> dict:
    out = round_result["out"]
    return {
        "outcome": out.get("outcome"),
        "step_path": out.get("step_path"),
        "decision": out.get("decision"),
        "reason_codes": out.get("reason_codes"),
        "selected_candidate_ref": out.get("selected_candidate_ref"),
        "adoption_status": out.get("adoption_status"),
        "activity_ref": out.get("activity_ref"),
        "capacity_state": out.get("capacity_state"),
        "material": out.get("material"),
        "head_before": round_result["head_before"],
        "head_after": round_result["head_after"],
        "prepared_point": writer.points[-1] if writer.points else None,
        "step": _receipt_facts(out),
        "checkpoint": {
            "activity_status": round_result["activity"].get("status"),
            "activity_revision": round_result["activity"].get("revision"),
            "checkpoint_ref": round_result["activity"].get("checkpoint_ref"),
            "progress_state": round_result["progress"].get("progress_state"),
            "completion_status": round_result["progress"].get("completion_status"),
        },
        "experience_handoff": out.get("experience_handoff"),
        "candidates": cognition.seen[-1] if cognition.seen else None,
    }


def _leaves(value) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_leaves(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_leaves(item))
    elif isinstance(value, str):
        found.append(value)
    return found


def main() -> int:
    from life_artifact_port import LifeArtifactPort

    groups = json.loads(os.environ[GROUPS_ENV])
    root_base = Path(os.environ[ROOT_ENV])
    root_base.mkdir(parents=True, exist_ok=True)
    os.chmod(root_base, 0o700)
    socket_path = os.environ["NYA02_SUPPLY_SOCKET"]

    print("== experiment A: the real state of her own work vs what she prepares next")
    print(f"trials={[t['trial'] for t in TRIALS]} groups={sorted(groups)}")

    for trial in TRIALS:
        name = trial["trial"]
        print(f"\n--- {name}: A head deeper + tail marker / B head V1 + a different position")
        records: dict[str, dict] = {}
        for label in ("A", "B"):
            spec = trial[label]
            entry = groups[f"{name}{label}"]
            port = LifeArtifactPort(socket_path, entry["subject"])
            artifact_id, heads = _establish_work(port, entry, spec, tag=f"{name}{label}")
            expected_head_number = len(spec["contents"])
            check(heads[-1].get("version_number") == expected_head_number,
                  f"{name}{label} the work really has head V{expected_head_number}",
                  heads[-1].get("version_number"))
            check(spec["marker"] in (spec["contents"][-1]),
                  f"{name}{label} the marker is really in the work's own text", spec["marker"])

            group_record: dict = {
                "subject": entry["subject"],
                "artifact_id": artifact_id,
                "initial_head": heads[-1],
                "initial_version_ids": [h.get("version_id") for h in heads],
                "marker": spec["marker"],
                "rounds": [],
            }
            for round_index, suffix in ((1, "（第一轮补一句）"), (2, "（第二轮再补一句）")):
                writer = _RecordingWriter(suffix)
                cognition = _StartCognition()
                round_root = root_base / f"{name}{label}-r{round_index}"
                result = _run_round(label=f"{name}{label}-r{round_index}", entry=entry,
                                    artifact_id=artifact_id, port=port, root=round_root,
                                    writer=writer, cognition=cognition)
                group_record["rounds"].append(_compact(result, writer, cognition))
            records[label] = group_record
            port.close()

        # ---- the pass condition: each group reflects its own work, and never the other's -----
        for label in ("A", "B"):
            rec = records[label]
            other = records["B" if label == "A" else "A"]
            r1, r2 = rec["rounds"]
            check(r1["step"]["expected_head"] == rec["initial_head"].get("version_id"),
                  f"{name}{label} round 1 is prepared against its own initial head",
                  (r1["step"].get("expected_head"), rec["initial_head"].get("version_id")))
            check(r1["prepared_point"] is not None
                  and r1["prepared_point"].get("head_version_id") == rec["initial_head"].get("version_id"),
                  f"{name}{label} the writer was handed its own head version",
                  r1["prepared_point"])
            check(r1["prepared_point"] is not None
                  and rec["marker"] in (r1["prepared_point"].get("content_tail") or ""),
                  f"{name}{label} the writer was handed its own text (marker in the tail)",
                  (rec["marker"], r1["prepared_point"]))
            check(r1["step"].get("owner_source_hash")
                  == (r1["prepared_point"] or {}).get("written_sha256"),
                  f"{name}{label} the owner receipt echoes the prepared text's hash",
                  (r1["step"].get("owner_source_hash"),
                   (r1["prepared_point"] or {}).get("written_sha256")))
            check(r1["outcome"] == "ACTED" and r1["step"].get("owner_version_created") is True,
                  f"{name}{label} round 1 really committed a version at the owner",
                  (r1["outcome"], r1["step"]))
            check(r1["step"].get("version_id") == rec["rounds"][1]["head_before"].get("version_id"),
                  f"{name}{label} the version the owner confirmed is the work's new head",
                  (r1["step"].get("version_id"), rec["rounds"][1]["head_before"].get("version_id")))
            # round 2 is "the next round": a fresh instance facing the work round 1 changed
            check(r2["step"]["expected_head"] == r1["step"].get("version_id"),
                  f"{name}{label} round 2 is prepared against the version round 1 produced",
                  (r2["step"].get("expected_head"), r1["step"].get("version_id")))
            check(r2["step"].get("expected_head") != rec["initial_head"].get("version_id"),
                  f"{name}{label} round 2 is not prepared against the stale initial head",
                  r2["step"].get("expected_head"))
            check(r1["step"].get("artifact_id") == rec["artifact_id"]
                  and r2["step"].get("artifact_id") == rec["artifact_id"],
                  f"{name}{label} both rounds act on this group's own artifact",
                  (r1["step"].get("artifact_id"), r2["step"].get("artifact_id")))
            check(r1["checkpoint"].get("progress_state") == "ADVANCED"
                  and bool(r1["checkpoint"].get("checkpoint_ref")),
                  f"{name}{label} LR-5 really advanced a checkpoint for this activity",
                  r1["checkpoint"])
            check(r1["checkpoint"].get("activity_revision") is not None
                  and r1["checkpoint"].get("activity_status") == "ACTIVE",
                  f"{name}{label} the activity stayed ACTIVE (open-ended work)",
                  r1["checkpoint"])

            foreign = [v for v in other["initial_version_ids"] if v] + [other["artifact_id"]]
            text = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
            leaked = [token for token in foreign if token and token in text]
            check(not leaked,
                  f"{name}{label} never mentions the other group's artifact or versions", leaked)
            check(other["marker"] not in text,
                  f"{name}{label} never carries the other group's text", other["marker"])

        print(f"  {name} summary: " + json.dumps({
            label: {
                "initial_head_v": records[label]["initial_head"].get("version_number"),
                "round1_expected_head": records[label]["rounds"][0]["step"].get("expected_head"),
                "round1_new_version": records[label]["rounds"][0]["step"].get("version_id"),
                "round1_new_version_number": records[label]["rounds"][0]["step"].get("version_number"),
                "round2_expected_head": records[label]["rounds"][1]["step"].get("expected_head"),
                "checkpoint_ref": records[label]["rounds"][0]["checkpoint"].get("checkpoint_ref"),
                "checkpoint_from_cycle": records[label]["rounds"][0]["step"].get("checkpoint_ref"),
                "progress_state": records[label]["rounds"][0]["checkpoint"].get("progress_state"),
                "candidate_kinds": (records[label]["rounds"][0]["candidates"] or {}).get("candidate_kinds"),
                "decision": records[label]["rounds"][0]["decision"],
                "handoff": records[label]["rounds"][0].get("experience_handoff"),
            } for label in ("A", "B")}, ensure_ascii=False, indent=2))

    print()
    if FAILURES:
        print(f"EXPERIMENT_A: FAIL ({len(FAILURES)} checks)")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("EXPERIMENT_A: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
