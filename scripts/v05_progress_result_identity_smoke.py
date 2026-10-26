#!/usr/bin/env python3
"""v0.5 零模型真实 owner 冒烟：同一 Activity 两个确证结果 → 两条结果级交接。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Supply owner（自有 AF_UNIX +
   canonical db）、真实 M0 Memory（EvidenceStore + controls）、真实 ProductionLifeRuntime、
   AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、AR-0 受管作品适配器。Decision 与内容
   回答是脚本替身（明示），不宣称真实模型自然采用。
2. 第一轮：真实 owner 写作品 A 的版本 1，AG-2 以 ``lexp:result:`` 结果级身份交接。
3. 第二轮：同一 Activity 的 CONTINUE 写版本 2（Activity revision 不变）。修复前两轮会用
   同一个 ``lexp:`` id 并重投第一轮事件；现在两个版本各自得到明确的结果级 handoff。
4. 同一结果重投：原 id、原事件、Memory 不新增重复经历。
5. 同根新进程重建 runtime 再投同一结果：仍原 id、事件数不变。
6. 第三轮正式 ``method_trial`` 引用**第二个**经历；owner 保存的被评价版本与第二结果对上。
7. consumer 的 DEFER 负控：如实保留 pending 与 disposition，不改写成成功。
8. 升级重投：同一 Activity 先用旧调用（无结果参数）为真实第 4 版写旧命名域事件，再经
   LR-2 合法 checkpoint 推进 revision，最后以新结果模式重投同一结果 —— 必须复用那条
   更早的旧事件，事件数与经历数都不增加，旧载荷逐字段不变；同根新进程再投一次仍复用。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/v05_progress_result_identity_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

TS = "2026-11-10T09:00:00+00:00"
CHECKS: list[tuple[str, bool, object]] = []
TABLE: list[dict] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-66s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def bare(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def version(port, artifact_id) -> int:
    point = port.resume_point(artifact_id)
    return int(getattr(point, "version_number", 0) or 0)


def head_version(port, artifact_id) -> str:
    point = port.resume_point(artifact_id)
    return str(getattr(point, "head_version_id", "") or "")


def row(round_no, out, artifact, chain) -> dict:
    handoff = out.get("experience_handoff") or {}
    receipt = out.get("step_receipt") or {}
    activity_ref = receipt.get("activity_ref") or out.get("activity_ref")
    act = (chain.runtime.activity_read.get_activity(activity_ref)
           if activity_ref else None)
    entry = {
        "round": round_no,
        "decision": (out.get("decision") if isinstance(out.get("decision"), str)
                     else (out.get("decision") or {}).get("decision_kind")
                     or (out.get("decision") or {}).get("intent_kind")),
        "action": receipt.get("operation_key") or out.get("action_id"),
        "activity_ref": activity_ref,
        "activity_rev": (act or {}).get("revision"),
        "artifact_version": version(chain.port, artifact),
        "handoff": handoff.get("handoff_id"),
        "result_ref": handoff.get("progress_result_ref"),
        "consumer_disposition": handoff.get("disposition"),
        "experience_status": handoff.get("status"),
    }
    TABLE.append(entry)
    return entry


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import life_integration_ag2 as ag2
    import result_method_behavior_smoke as harness
    from v05_practice_continuation_smoke import (continue_candidate,
                                                 install_decision_view_recorder, run)

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("p1")
    chain.decision_views = install_decision_view_recorder(chain)
    runtime_closed = False
    try:
        # -- round 1: real owner version 1 + the Decision's own method proposal ------------
        seed = harness.seed_method_after_first_write(chain)
        method_ref = str(seed.method_ref)
        method_id = str(seed.method_id)
        receipt1 = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt1.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        v1 = head_version(chain.port, artifact_a)
        h1 = seed.round_out.get("experience_handoff") or {}
        exp1 = str(seed.experience_id)
        r1 = row(1, seed.round_out, artifact_a, chain)
        check("round 1 wrote a real owner version and a result-level handoff",
              bool(artifact_a) and bool(v1) and str(h1.get("handoff_id") or "").startswith(
                  "lexp:result:"), r1)
        check("round 1's handoff names the owner's own artifact+version ref",
              str(h1.get("progress_result_ref") or "")
              == "artifact:%s@artifactversion:%s" % (bare(artifact_a), v1),
              {"ref": h1.get("progress_result_ref"), "version": v1})

        # -- round 2: CONTINUE the *same* Activity; owner version 2 ------------------------
        candidate = continue_candidate(chain, artifact_a)
        check("round 2 offers exactly one exact CONTINUE candidate for A", bool(candidate),
              {"candidate": candidate, "artifact": artifact_a})
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "第二段：同一 Activity 的第二个确证版本。",
                                "self_note": "stub: write A v2"}
        out2, _view2 = run(chain, decision="CONTINUE", candidate=candidate,
                           fields={"method_ref": method_ref,
                                   "short_rationale": "stub: continue the same work"},
                           trigger="evt:v05-progress-result:2")
        v2 = head_version(chain.port, artifact_a)
        h2 = out2.get("experience_handoff") or {}
        r2 = row(2, out2, artifact_a, chain)
        experiences = chain.cycle.practice.experiences()
        exp2_ids = [str(item.get("experience_id")) for item in experiences]
        check("round 2 acted through the formal chain", out2.get("outcome") == "ACTED",
              {key: out2.get(key) for key in ("outcome", "detail", "practice_error")})
        check("two confirmed owner versions in one Activity got two distinct handoffs",
              bool(v2) and v1 != v2 and h1.get("handoff_id") and h2.get("handoff_id")
              and h1["handoff_id"] != h2["handoff_id"],
              {"v1": v1, "v2": v2, "h1": h1.get("handoff_id"), "h2": h2.get("handoff_id")})
        check("both handoffs are in the result-level namespace and name different results",
              str(h1.get("handoff_id") or "").startswith("lexp:result:")
              and str(h2.get("handoff_id") or "").startswith("lexp:result:")
              and h1.get("progress_result_ref") != h2.get("progress_result_ref"),
              {"r1": r1, "r2": r2})
        check("the existing admission rule accepted both results (no bypass)",
              str(h1.get("disposition") or "") in ("ACCEPT", "IGNORE")
              and str(h2.get("disposition") or "") in ("ACCEPT", "IGNORE")
              and len(exp2_ids) >= 2 and exp1 in exp2_ids,
              {"experiences": exp2_ids, "h1": h1.get("disposition"),
               "h2": h2.get("disposition")})
        exp2 = next((eid for eid in exp2_ids if eid != exp1), "")
        check("the second experience is a separate readable record", bool(exp2), exp2_ids)

        # -- same result re-delivered: same id, same event, no new experience --------------
        integration_id, activity_ref = _latest_integration(chain)
        confirmed2 = chain.cycle._owner_confirmed_context(
            artifact_ref=artifact_a, version_ref=v2)
        ref2 = ag2.canonical_owner_result_ref(confirmed2)
        before_events = len(chain.runtime.coordinator.integration_commit.read_state()
                            ["handoff_events_by_id"])
        before_exp = len(chain.cycle.practice.experiences())
        replay = chain.runtime.coordinator.emit_experience_handoff(
            integration_id=integration_id, activity_id=activity_ref,
            terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
            occurred_at=TS, artifact_refs=_confirmed_refs(confirmed2),
            owner_confirmed_context=confirmed2, progress_result_ref=ref2)
        after_events = len(chain.runtime.coordinator.integration_commit.read_state()
                           ["handoff_events_by_id"])
        after_exp = len(chain.cycle.practice.experiences())
        check("the same confirmed result replays the original event",
              replay["handoff_event"].handoff_id == h2.get("handoff_id")
              and bool(replay.get("idempotent_replay")), replay.get("handoff_event").handoff_id)
        check("the replay added no handoff event and no duplicate experience",
              before_events == after_events and before_exp == after_exp,
              {"events": [before_events, after_events], "experiences": [before_exp, after_exp]})

        # -- DEFER negative control: the disposition is not rewritten ----------------------
        original = chain.runtime.coordinator.experience_consumer

        class _Deferring:
            def receive_experience_handoff(self, _event):
                return {"disposition": "DEFER"}

        chain.runtime.coordinator.experience_consumer = _Deferring()
        try:
            deferred = chain.runtime.coordinator.emit_experience_handoff(
                integration_id=integration_id, activity_id=activity_ref,
                terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
                occurred_at=TS, artifact_refs=_confirmed_refs(confirmed2),
                owner_confirmed_context=confirmed2, progress_result_ref=ref2)
        finally:
            chain.runtime.coordinator.experience_consumer = original
        delivery = (deferred.get("dispatch_result") or {}).get("deliveries") or []
        pending_after_defer = ((deferred.get("dispatch_result") or {})
                               .get("pending_handoff_ids") or [])
        check("a deferred consumer keeps DEFER (never rewritten as success)",
              bool(delivery) and delivery[0].get("consumer_disposition") == "DEFER"
              and delivery[0].get("status") == "DEFERRED",
              {"deliveries": delivery, "pending": pending_after_defer,
               "note": "existing dispatch rule: an already-delivered id is not re-queued"})

        # -- same root, fresh process: rebuild the runtime and replay the same result ------
        chain.runtime.close()
        runtime_closed = True
        child = _child_replay(chain, artifact_a=artifact_a, version_ref=v2,
                              integration_id=integration_id, activity_ref=activity_ref)
        check("a fresh process on the same root replays the same result without a new event",
              child.get("handoff_id") == h2.get("handoff_id")
              and child.get("idempotent_replay") is True
              and child.get("event_count") == after_events, child)

        # -- round 3: a formal verdict that cites the *second* experience ------------------
        chain = _reopen_chain(chain)
        runtime_closed = False  # the reopened runtime owns its own lease and must be closed
        candidate = continue_candidate(chain, artifact_a)
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "第三段：基于第二个结果继续。",
                                "self_note": "stub: write A v3"}
        out3, _view3 = run(chain, decision="CONTINUE", candidate=candidate, fields={
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "第二个结果确实更顺",
                             "experience_ids": [exp2]},
            "short_rationale": "stub: verdict on the second result",
        }, trigger="evt:v05-progress-result:3")
        r3 = row(3, out3, artifact_a, chain)
        trial = out3.get("method_trial") or {}
        usage = out3.get("method_usage") or {}
        check("round 3 acted and recorded a formal trial", out3.get("outcome") == "ACTED"
              and bool(trial), {"outcome": out3.get("outcome"), "trial": trial, "r3": r3})
        check("the trial's evidence is the second experience, not the first",
              str(trial.get("experience_id") or "") in (exp2, "")
              or exp2 in json.dumps(trial, default=str),
              {"expected": exp2, "trial": trial})
        stored = chain.cycle.practice.read_method(method_id) or {}
        stored_trials = stored.get("trials") or []
        exp2_row = chain.cycle.practice.read_experience(exp2) or {}
        exp2_text = json.dumps(exp2_row, ensure_ascii=False, default=str)
        check("the owner's trial cites the second experience",
              bool(stored_trials)
              and exp2 in json.dumps(stored_trials[-1], ensure_ascii=False, default=str),
              {"exp2": exp2, "trial_row": stored_trials[-1] if stored_trials else None})
        check("the second experience really names the owner's second version",
              bool(v2) and v2 in exp2_text,
              {"v2": v2, "experience_keys": sorted(exp2_row),
               "experience": exp2_text[:600]})
        check("round 3 still continues the same work and method", bool(usage),
              {"usage": usage})

        # -- upgrade replay: an old-call event at an older Activity revision ----------------
        # A real fourth owner version is written through the real port (fixture: this is the
        # only write here that is not driven by a formal Decision -- the identity rule under
        # test is about the handoff lookup, not about who may write), then the *old* call
        # records a legacy event, a legal LR-2 checkpoint moves the Activity revision, and the
        # new result-level mode replays that same result.
        integration_id, activity_ref = _latest_integration(chain)
        v3 = head_version(chain.port, artifact_a)
        view = chain.port.read_artifact(artifact_a) or {}
        body = view.get("content") if isinstance(view.get("content"), str) else ""
        write4 = chain.port.commit_version(
            artifact_id=artifact_a, expected_head=v3,
            content=body.rstrip() + "\n\n第四段：升级重投兼容夹具。",
            grant_id=chain.data["artifact_grant_id"],
            operation_key="op:v05-progress-result-upgrade:v4")
        v4 = head_version(chain.port, artifact_a)
        confirmed4 = chain.cycle._owner_confirmed_context(
            artifact_ref=artifact_a, version_ref=v4)
        ref4 = ag2.canonical_owner_result_ref(confirmed4) if confirmed4 else ""
        check("the upgrade fixture wrote a fourth real owner version",
              bool(write4.applied) and bool(v4) and v4 != v3 and bool(ref4),
              {"v3": v3, "v4": v4, "status": write4.status, "ref": ref4})

        coordinator = chain.runtime.coordinator
        events_before = len(coordinator.integration_commit.read_state()
                            ["handoff_events_by_id"])
        legacy = coordinator.emit_experience_handoff(
            integration_id=integration_id, activity_id=activity_ref,
            terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
            occurred_at=TS, artifact_refs=_confirmed_refs(confirmed4),
            owner_confirmed_context=confirmed4)  # old call: no progress_result_ref
        legacy_id = legacy["handoff_event"].handoff_id
        legacy_revision = int(legacy["handoff_event"].activity_revision)
        legacy_payload = json.dumps(
            coordinator.integration_commit.read_state()
            ["handoff_events_by_id"][legacy_id], sort_keys=True)
        check("the old call still keys on the Activity revision (legacy namespace)",
              legacy_id.startswith("lexp:") and not legacy_id.startswith("lexp:result:")
              and len(coordinator.integration_commit.read_state()["handoff_events_by_id"])
              == events_before + 1,
              {"legacy_id": legacy_id, "activity_revision": legacy_revision})

        canonical, _journal = chain.runtime.activity_read.read_canonical_state(
            include_journal=False)
        chain.runtime.progress_coordinator.progress_consequence_port \
            .propose_checkpoint_or_progress(
                "CHECKPOINT", activity_id=activity_ref,
                checkpoint_ref="checkpoint:v05-progress-upgrade",
                expected_revision=int(canonical["revision"]),
                idempotency_key="ckpt:v05-progress-upgrade",
                source_refs=["evidence:v05-progress-upgrade"],
                occurred_at=TS, observed_at=TS)
        moved = chain.runtime.activity_read.get_activity(activity_ref) or {}
        check("a legal LR-2 checkpoint advanced the Activity revision",
              int(moved.get("revision") or 0) > legacy_revision,
              {"before": legacy_revision, "after": moved.get("revision")})

        # The old call above is the *one* handoff for this result and may legitimately have
        # produced its one experience; the upgrade must add neither a handoff nor a second one.
        experiences_before = len(chain.cycle.practice.experiences())
        events_before_upgrade = len(coordinator.integration_commit.read_state()
                                    ["handoff_events_by_id"])
        upgraded = coordinator.emit_experience_handoff(
            integration_id=integration_id, activity_id=activity_ref,
            terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
            occurred_at=TS, artifact_refs=_confirmed_refs(confirmed4),
            owner_confirmed_context=confirmed4, progress_result_ref=ref4)
        events_after_upgrade = len(coordinator.integration_commit.read_state()
                                   ["handoff_events_by_id"])
        experiences_after_upgrade = len(chain.cycle.practice.experiences())
        upgraded_payload = json.dumps(
            coordinator.integration_commit.read_state()
            ["handoff_events_by_id"][upgraded["handoff_event"].handoff_id], sort_keys=True)
        check("the upgraded replay reuses the older legacy event",
              upgraded["handoff_event"].handoff_id == legacy_id
              and bool(upgraded.get("reused_legacy_handoff"))
              and bool(upgraded.get("idempotent_replay")),
              {"legacy": legacy_id, "upgraded": upgraded["handoff_event"].handoff_id})
        check("the upgrade added no handoff event and no second experience",
              events_after_upgrade == events_before_upgrade
              and experiences_after_upgrade == experiences_before,
              {"events": [events_before_upgrade, events_after_upgrade],
               "experiences": [experiences_before, experiences_after_upgrade]})
        check("the reused legacy payload is not rewritten",
              upgraded_payload == legacy_payload,
              {"legacy": legacy_payload[:300]})
        TABLE.append({
            "round": "upgrade",
            "legacy_handoff": legacy_id,
            "upgraded_handoff": upgraded["handoff_event"].handoff_id,
            "reused_legacy_handoff": bool(upgraded.get("reused_legacy_handoff")),
            "legacy_activity_rev": legacy_revision,
            "current_activity_rev": moved.get("revision"),
            "owner_version": v4,
            "handoff_events": events_after_upgrade,
            "experiences": experiences_after_upgrade,
        })

        # -- same root, fresh process: the advanced revision must not defeat the reuse ----
        chain.runtime.close()
        runtime_closed = True
        child_upgrade = _child_replay(chain, artifact_a=artifact_a, version_ref=v4,
                                      integration_id=integration_id,
                                      activity_ref=activity_ref)
        check("a fresh process on the same root reuses the older legacy event",
              child_upgrade.get("handoff_id") == legacy_id
              and child_upgrade.get("reused_legacy_handoff") is True
              and child_upgrade.get("event_count") == events_after_upgrade,
              child_upgrade)

        return 0 if all(ok for _name, ok, _detail in CHECKS) else 1
    finally:
        try:
            if not runtime_closed:
                chain.runtime.close()
        except Exception as exc:
            print("  runtime close error:", type(exc).__name__, str(exc)[:200])
        try:
            chain.services.stop()
        except Exception as exc:
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        _print_table()
        print("CHECKS %d/%d" % (sum(1 for _n, ok, _d in CHECKS if ok), len(CHECKS)))


def _confirmed_refs(confirmed: dict) -> list:
    return [confirmed["artifact_id"], confirmed["version_ref"]]


def _latest_integration(chain):
    state = chain.runtime.coordinator.integration_commit.read_state()
    items = list((state.get("integrations_by_id") or {}).items())
    if not items:
        raise SystemExit("no integration record after a real write")
    integration_id, record = items[-1]
    return integration_id, str(record.get("activity_ref") or "")


def _child_replay(chain, *, artifact_a, version_ref, integration_id, activity_ref) -> dict:
    """Rebuild the real runtime in a fresh process on the same state root, then replay."""

    config = {
        "instance": str(chain.root / "instance"),
        "supply_socket": str(chain.services.supply_path),
        "evidence_db": str(chain.evidence_db),
        "controls_db": str(chain.controls_db),
        "artifact": artifact_a,
        "version_ref": version_ref,
        "integration_id": integration_id,
        "activity_ref": activity_ref,
    }
    path = Path(tempfile.mkdtemp(prefix="v05-progress-result-child-")) / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    code = (_CHILD_SOURCE.replace("__ROOT__", str(ROOT))
            .replace("__CONFIG__", str(path)))
    import subprocess
    completed = subprocess.run([sys.executable, "-B", "-c", code],
                               capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        print("  child stdout:", completed.stdout[-1500:])
        print("  child stderr:", completed.stderr[-1500:])
        return {"error": "child exit %d" % completed.returncode}
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        print("  child stdout:", completed.stdout[-1500:])
        return {"error": "child printed no JSON"}
    return json.loads(lines[-1])


def _reopen_chain(chain):
    """Reopen the same fixture in this process after the first runtime was closed."""

    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort

    data = odl.read_instance(chain.root / "instance")
    port = lap.LifeArtifactPort(str(chain.services.supply_path), data["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
    memory_port = LifeExperiencePort(str(chain.evidence_db),
                                     controls_db=str(chain.controls_db), subject="chiyo")
    consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        odl.life_state_root(chain.root / "instance"), kill_switches=switches,
        cognition_adapter=chain.cognition, install_root=chain.root / "instance" / "life",
        artifact_action_adapter=adapter, experience_consumer=consumer)
    import autonomous_life_assembly as assembly
    from model_continuation_writer import ModelContinuationWriter
    writer = ModelContinuationWriter(chain.client, max_new_chars=300)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=chain.env, state_root=odl.life_state_root(
            chain.root / "instance"), writer=writer, enabled=True)
    if assembled.get("state") != assembly.READY:
        raise SystemExit("reopen assembly not READY: %r" % (assembled,))
    from v05_practice_continuation_smoke import install_decision_view_recorder
    chain.runtime = runtime
    chain.port = port
    chain.cycle = assembled["cycle"]
    chain.decision_views = install_decision_view_recorder(chain)
    return chain


_CHILD_SOURCE = r"""
import json, sys
sys.path[:0] = [r"__ROOT__", r"__ROOT__/components",
                r"__ROOT__/components/life/scripts",
                r"__ROOT__/components/supply"]
from pathlib import Path
import life_artifact_port as lap
import life_integration_ag2 as ag2
import life_runtime_production as lrp
import managed_artifact_action_adapter as maaa
import one_day_life as odl
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
from life_experience_port import LifeExperiencePort

cfg = json.loads(Path(r"__CONFIG__").read_text(encoding="utf-8"))
instance = Path(cfg["instance"])
data = odl.read_instance(instance)
port = lap.LifeArtifactPort(cfg["supply_socket"], data["subject_id"])
adapter = maaa.SupplyManagedArtifactActionAdapter(
    port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
memory_port = LifeExperiencePort(cfg["evidence_db"], controls_db=cfg["controls_db"],
                                 subject="chiyo")
consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
switches = ag2.LifeRuntimeKillSwitches(
    life_runtime_enabled=True, agency_enabled=True,
    action_execution_enabled=True, proactive_enabled=False)
runtime = lrp.build_production_life_runtime(
    odl.life_state_root(instance), kill_switches=switches,
    install_root=instance / "life", artifact_action_adapter=adapter,
    experience_consumer=consumer)
try:
    view = port.read_artifact(cfg["artifact"])
    head = view.get("head") or {}
    version = str(head.get("version_id") or "")
    assert version == cfg["version_ref"], (version, cfg["version_ref"])
    artifact = view.get("artifact") or {}
    body = view.get("content") if isinstance(view.get("content"), str) else ""
    if body.lstrip().startswith("<!--"):
        end = body.find("-->")
        if end >= 0:
            body = body[end + len("-->"):]
    confirmed = {
        "artifact_id": "artifact:%s" % str(cfg["artifact"]).split(":")[-1],
        "version_ref": "artifactversion:%s" % version,
        "title": artifact.get("title") or None,
        "result_description": " ".join(body.split())[-240:],
        "outcome_confirmed": True,
    }
    res = runtime.coordinator.emit_experience_handoff(
        integration_id=cfg["integration_id"], activity_id=cfg["activity_ref"],
        terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
        occurred_at="2026-11-10T09:00:00+00:00",
        artifact_refs=[confirmed["artifact_id"], confirmed["version_ref"]],
        owner_confirmed_context=confirmed,
        progress_result_ref=ag2.canonical_owner_result_ref(confirmed))
    state = runtime.coordinator.integration_commit.read_state()
    print(json.dumps({
        "handoff_id": res["handoff_event"].handoff_id,
        "idempotent_replay": bool(res.get("idempotent_replay")),
        "reused_legacy_handoff": bool(res.get("reused_legacy_handoff")),
        "event_count": len(state["handoff_events_by_id"]),
    }, ensure_ascii=False))
finally:
    runtime.close()
"""


def _print_table() -> None:
    print("\nRESULT TABLE (safe structure only)")
    for entry in TABLE:
        print(json.dumps(entry, ensure_ascii=False, default=str))


if __name__ == "__main__":
    sys.exit(main())
