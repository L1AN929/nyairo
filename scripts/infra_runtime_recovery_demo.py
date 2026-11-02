#!/usr/bin/env python3
"""INFRA F161-167 demo: recovery before a new action, short persistence boundaries, and no
store lock held across an I/O wait.

Everything runs in one fresh isolated fixture on this machine: a real Life Supply owner on its
own Unix socket, a real instance, the real ``ProductionLifeRuntime`` (LR-2/LR-4/LR-5, AG-0/1/2,
AR-0/AR-1), real ManagedArtifact versions.  Decision and content answers are scripted doubles;
no product model, no external network, no old instance, no old root.

What it shows, one stage per check group:

1. read-only review of a fresh root says ASSEMBLABLE, and the CLI exits 0;
2. worker A (its own PID) performs one formal START -> a real Supply version + Action SUCCEEDED;
3. worker B (a *different* PID, same root) assembles the real runtime, sees the same Activity /
   Action / head, and the existing recovery entry adds no action and no version;
4. worker C (new PID) is killed with SIGKILL at the adapter's real
   ``response_lost_after_write`` hook -- the owner write already landed, the answer did not.
   After the kill the CLI reports NEEDS_EXISTING_RECOVERY (open fence), not "safe";
5. worker D (new PID) runs the existing ``recover_all_and_reconcile``: AR-0 turns the fence into
   UNKNOWN, reconciles by the *original* operation key, and the version count does not move;
6. a stale ``expected_head`` proposal is refused by the Supply owner (CAS), with zero new
   version; submitting an already SUCCEEDED action is an idempotent replay with no owner call;
7. while worker E blocks inside its cognition call, the read-only CLI and a real Supply owner
   read still complete, and a non-blocking probe of the AG-1 store IO lock file succeeds: no
   store transaction lock spans that wait.  (The LR-2 writer lease *is* held for the process
   lifetime -- that is the writer fence, named separately, not a transaction lock.)
8. writer conflict: while worker F holds the lifecycle lease, a second assembly is refused by
   the lease before any business write, and the CLI says MANUAL;
9. F165/F166 have no formal backup/restore or migration entry; the review reports the named gap
   and this demo does not copy, restore or migrate anything.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/infra_runtime_recovery_demo.py

Exit 0 only when every expectation holds; it prints short CHECK lines only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

BASE_TIME = datetime.now().astimezone()
_ROUND = [0]
FAILURES: list[str] = []


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-78s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1800])
    return ok


def next_instant() -> str:
    _ROUND[0] += 1
    return (BASE_TIME + timedelta(minutes=400 * _ROUND[0])).isoformat()


# ---------------------------------------------------------------------------------------------
# fixture root (real owners) -- parent side
# ---------------------------------------------------------------------------------------------


def _prepare_root(root: Path, *, supply_socket: str, governance_socket: str) -> dict[str, Any]:
    import one_day_life as odl
    from components.worldgen import schema
    from components.worldgen.instance import init_instance
    from components.worldgen.tests._setup import make_valid_docs
    from tests.test_one_day_life import ENV_GATES
    from unittest import mock

    package_dir = root / "package"
    schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
    world_home = root / "world-home"
    init_instance(package_dir, world_home)
    instance = root / "instance"
    with mock.patch.dict(os.environ, ENV_GATES):
        prepared = odl.prepare(instance=instance, supply_socket=supply_socket,
                               governance_socket=governance_socket,
                               subject="test:infra-recovery", title="Infra recovery fixture",
                               world_package=package_dir, create_private_items=True)
    if not prepared.get("ok"):
        raise SystemExit("prepare failed: %r" % (prepared,))
    data = odl.read_instance(instance)
    env = odl.instance_environment(instance)
    memory_dir = root / "memory"
    memory_dir.mkdir()
    evidence_db = memory_dir / "memory.sqlite3"
    controls_db = memory_dir / "controls.sqlite3"
    env.update({
        "NYAIRO_LIFE_SUPPLY_SOCKET": str(supply_socket),
        "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
        "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
        "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        "NYA02_MEMORY_DB": str(evidence_db),
        "NYA02_MEMORY_CONTROLS_DB": str(controls_db),
        "NYA02_MEMORY_SUBJECT": "chiyo",
    })
    if data.get("private_items_artifact_id"):
        env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                       str(data["private_items_artifact_id"]))
    if data.get("s03_claims_db"):
        env.setdefault("CHIYO_S03_CLAIMS_DB", str(data["s03_claims_db"]))
    return {"instance": instance, "data": data, "env": env,
            "evidence_db": evidence_db, "controls_db": controls_db}


def _init_memory(controls_db: Path, evidence_db: Path) -> None:
    from life_experience_port import life_conversation_id
    from memory_control import MemoryControlLedger

    MemoryControlLedger(str(controls_db), str(evidence_db), owner="chiyo",
                        conversation=life_conversation_id("chiyo")).initialize()


# ---------------------------------------------------------------------------------------------
# worker side: rebuild the real runtime on an existing root
# ---------------------------------------------------------------------------------------------


def _build(env: Mapping[str, str], instance: Path, *, drop_response: bool = False):
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter
    from result_method_behavior_smoke import ScriptedCognition, ScriptedClient

    data = odl.read_instance(instance)
    state_root = odl.life_state_root(instance)
    cognition = ScriptedCognition()
    client = ScriptedClient()
    writer = ModelContinuationWriter(client, max_new_chars=300)
    port = lap.LifeArtifactPort(str(env["NYAIRO_LIFE_SUPPLY_SOCKET"]), data["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"],
        fault_injection_drop_response_after_write=drop_response)
    memory_port = LifeExperiencePort(str(env["NYA02_MEMORY_DB"]),
                                     controls_db=str(env["NYA02_MEMORY_CONTROLS_DB"]),
                                     subject=str(env.get("NYA02_MEMORY_SUBJECT") or "chiyo"))
    consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=instance / "life", artifact_action_adapter=adapter,
        experience_consumer=consumer)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=dict(env), state_root=state_root, writer=writer, enabled=True)
    if assembled["state"] != assembly.READY:
        raise SystemExit("assembly not READY: %r" % (assembled,))
    return SimpleNamespace(runtime=runtime, cycle=assembled["cycle"], cognition=cognition,
                           client=client, port=port, adapter=adapter, data=data)


def _candidate_refs(chain, observed_at: str) -> list[str]:
    return [str(ref) for ref in chain.runtime.candidate_materializer
            .build_candidate_set(observed_at=observed_at).candidate_refs]


def _candidate_payload(chain, ref: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})


def _find_work_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload: Mapping[str, Any]) -> bool:
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    for ref in _candidate_refs(chain, observed_at):
        if matches(_candidate_payload(chain, ref)):
            return ref
    return None


def _find_current_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    rows = []
    for ref in _candidate_refs(chain, observed_at):
        payload = _candidate_payload(chain, ref)
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            rows.append((ref, payload))
    if not rows:
        return None
    wanted = str(chain.port.resume_point(str(artifact_id)).head_version_id or "").split(":")[-1]
    if wanted:
        for ref, payload in rows:
            if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return rows[-1][0]


def _choose(chain, decision: str, candidate_id: Optional[str]) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def _round_out(chain, tag: str, observed_at: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(trigger_ref="evt:infra-recovery:%s" % tag,
                                          observed_at=observed_at, act=True) or {})


def _versions(chain, artifact_id: str) -> int:
    return int(chain.port.list_versions(str(artifact_id), limit=50)["total_versions"])


def _emit(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n",
                    encoding="utf-8")


def _worker_main(args: argparse.Namespace) -> int:
    env = json.loads(Path(args.env_file).read_text(encoding="utf-8"))
    instance = Path(args.instance)
    out = Path(args.out)
    work = str(env["WORK_ARTIFACT_ID"])
    result: dict[str, Any] = {"worker": args.worker, "pid": os.getpid()}

    if args.worker == "hold":
        chain = _build(env, instance)
        try:
            _emit(Path(args.marker), {"pid": os.getpid(), "state": "lease_held"})
            deadline = time.monotonic() + 180.0
            release = Path(args.release)
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            result["released"] = release.exists()
        finally:
            chain.runtime.close()
        _emit(out, result)
        return 0

    if args.worker == "block":
        chain = _build(env, instance)
        release = Path(args.release)
        original_invoke = chain.cognition.invoke

        def blocking_invoke(request, *, schema_retry_index: int = 0):
            _emit(Path(args.marker), {"pid": os.getpid(), "state": "cognition_blocked"})
            deadline = time.monotonic() + 180.0
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            result["released"] = release.exists()
            return original_invoke(request, schema_retry_index=schema_retry_index)

        chain.cognition.invoke = blocking_invoke
        try:
            ts = next_instant()
            _choose(chain, "NO_ACTION", None)
            r = _round_out(chain, "block", ts)
            result.update({"outcome": r.get("outcome"), "llm_calls": r.get("llm_calls")})
            result["versions"] = _versions(chain, work)
        finally:
            chain.runtime.close()
        _emit(out, result)
        return 0

    if args.worker == "conflict":
        # Another process holds the writer authority; assembling must be refused before any
        # business write.  This process is expected to fail to build.
        try:
            chain = _build(env, instance)
        except Exception as exc:
            result.update({"refused": True, "error": "%s: %s" % (type(exc).__name__, exc),
                           "versions": None})
            _emit(out, result)
            return 0
        chain.runtime.close()
        result.update({"refused": False, "error": None})
        _emit(out, result)
        return 1

    chain = _build(env, instance, drop_response=(args.worker == "crash"))
    try:
        if args.worker == "begin":
            ts = next_instant()
            candidate = _find_work_candidate(chain, work, ts)
            if candidate is None:
                result["error"] = "NO_WORK_CANDIDATE"
                _emit(out, result)
                return 1
            _choose(chain, "START", candidate)
            r = _round_out(chain, "start", ts)
            action_id = str(r.get("action_id") or "")
            action = dict(chain.runtime.action_read.get_action(action_id) or {})
            result.update({
                "outcome": r.get("outcome"), "action_id": action_id,
                "action_status": action.get("status"),
                "activity_ref": r.get("activity_ref"),
                "versions": _versions(chain, work),
                "head_version_id": (chain.port.read_head(work) or {}).get("version_id"),
                "candidate": candidate,
            })
            _emit(out, result)
            return 0 if (r.get("outcome") == "ACTED"
                         and action.get("status") == "SUCCEEDED") else 1

        if args.worker == "restart":
            before_actions = len(chain.runtime.action_read.list_actions())
            before_versions = _versions(chain, work)
            recovery = chain.runtime.coordinator.recover_all_and_reconcile(
                auto_reconcile_unknown_actions=False)
            after_actions = len(chain.runtime.action_read.list_actions())
            after_versions = _versions(chain, work)
            result.update({
                "actions_before": before_actions, "actions_after": after_actions,
                "versions_before": before_versions, "versions_after": after_versions,
                "llm_calls": recovery.get("llm_calls"),
                "ar0_recovery": recovery.get("ar0_recovery"),
                "head_version_id": (chain.port.read_head(work) or {}).get("version_id"),
            })
            _emit(out, result)
            return 0 if (after_actions == before_actions
                         and after_versions == before_versions) else 1

        if args.worker == "crash":
            marker = Path(args.marker)
            original_hook = chain.cycle._pause_fault_hook

            def killing_hook(name: str) -> None:
                original_hook(name)
                if name == "response_lost_after_write":
                    # The owner write already landed.  Record where we are, then die the way a
                    # real crash does: no Python cleanup, no AR-0 result accepted.
                    _emit(marker, {"pid": os.getpid(), "hook": name,
                                   "versions": _versions(chain, work)})
                    os.kill(os.getpid(), signal.SIGKILL)

            chain.cycle._pause_fault_hook = killing_hook
            ts = next_instant()
            candidate = _find_current_candidate(chain, work, ts)
            if candidate is None:
                result["error"] = "NO_CONTINUE_CANDIDATE"
                _emit(out, result)
                return 1
            chain.client.payload = {"kind": "append_paragraph",
                                    "text": "崩溃点之后写下的真实一段。"}
            _choose(chain, "CONTINUE", candidate)
            r = _round_out(chain, "crash", ts)
            # Reaching here means the hook did not fire; that is a failed fixture, not a crash.
            result.update({"hook_not_reached": True, "outcome": r.get("outcome")})
            _emit(out, result)
            return 1

        if args.worker == "recover":
            before_versions = _versions(chain, work)
            recovery = chain.runtime.coordinator.recover_all_and_reconcile(
                auto_reconcile_unknown_actions=True)
            # The coordinator reconciles actions it can link to an integration record.  Any
            # unresolved action still open is reconciled through AR-0's own entry, which asks
            # the owner about the *original* operation key; nothing new is minted.
            extra_reconciled: list[str] = []
            for row in chain.runtime.action_read.list_actions(status="UNKNOWN"):
                extra_reconciled.append(str(row.get("action_id")))
                chain.runtime.action_command.reconcile_action(
                    action_id=str(row["action_id"]))
            recovery["extra_reconciled"] = extra_reconciled
            after_versions = _versions(chain, work)
            actions = [dict(row) for row in chain.runtime.action_read.list_actions()]
            result.update({
                "versions_before": before_versions, "versions_after": after_versions,
                "actions": [{"action_id": row.get("action_id"), "status": row.get("status"),
                             "reconciliation_refs": row.get("reconciliation_refs")}
                            for row in actions],
                "llm_calls": recovery.get("llm_calls"),
                "ar0_recovery": recovery.get("ar0_recovery"),
                "adapter_submit_calls": getattr(chain.adapter, "submit_call_count", None),
            })
            _emit(out, result)
            return 0

        if args.worker == "cas":
            import action_reality_ledger as ar0

            versions_before = _versions(chain, work)
            page = chain.port.list_versions(work, limit=50)
            entries = list(page.get("entries") or [])
            stale_head = str(entries[-1]["version_id"]) if entries else None
            propose = chain.runtime.action_command.propose_action(
                action_kind=ar0.ACTION_KIND_FILE, target_ref=work,
                payload_ref="payload:stale-head",
                payload_data={"op": "commit_artifact_version",
                              "artifact_id": work, "expected_head": stale_head,
                              "content": "基于旧 head 的提案，不应写入。"},
                idempotency_key="infra-recovery:stale-head:%d" % os.getpid(),
                source_refs=["demo:infra-recovery"])
            action_id = str((propose.get("action") or {}).get("action_id") or "")
            chain.runtime.action_command.prepare_action(action_id=action_id)
            submit = chain.runtime.action_command.submit_action(
                action_id=action_id, executor_capability=chain.runtime.executor_capability)
            action = dict(chain.runtime.action_read.get_action(action_id) or {})
            versions_after = _versions(chain, work)

            succeeded = [row for row in chain.runtime.action_read.list_actions()
                         if str(row.get("status")) == "SUCCEEDED"]
            replay = None
            submit_calls_before = int(getattr(chain.adapter, "submit_call_count", 0))
            if succeeded:
                replay = chain.runtime.action_command.submit_action(
                    action_id=str(succeeded[0]["action_id"]),
                    executor_capability=chain.runtime.executor_capability)
            submit_calls_after = int(getattr(chain.adapter, "submit_call_count", 0))

            result.update({
                "stale_head": stale_head,
                "stale_action_id": action_id,
                "stale_action_status": action.get("status"),
                "stale_receipt_refs": action.get("receipt_refs"),
                "versions_before": versions_before, "versions_after": versions_after,
                "idempotent_replay": bool((replay or {}).get("idempotent_replay")),
                "adapter_submit_calls_delta": submit_calls_after - submit_calls_before,
            })
            _emit(out, result)
            return 0

        result["error"] = "UNKNOWN_WORKER"
        _emit(out, result)
        return 2
    finally:
        try:
            chain.runtime.close()
        except Exception as exc:
            say("  runtime close error: %s: %s" % (type(exc).__name__, str(exc)[:200]))


# ---------------------------------------------------------------------------------------------
# parent side
# ---------------------------------------------------------------------------------------------


def _run_cli(instance: Path, *extra: str) -> tuple[int, dict[str, Any], str]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "infra_runtime_recovery_cli.py"),
               "--instance", str(instance), "--json", *extra]
    done = subprocess.run(command, capture_output=True, text=True, timeout=90)
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError:
        payload = {"ok": False, "raw_stdout": done.stdout[-800:],
                   "raw_stderr": done.stderr[-800:]}
    return int(done.returncode), payload, done.stderr[-400:]


def _run_worker(mode: str, *, instance: Path, env_file: Path, out: Path,
                marker: Optional[Path] = None, release: Optional[Path] = None,
                timeout: float = 180.0) -> subprocess.CompletedProcess:
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", mode,
               "--instance", str(instance), "--env-file", str(env_file), "--out", str(out)]
    if marker is not None:
        command += ["--marker", str(marker)]
    if release is not None:
        command += ["--release", str(release)]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _wait_for(path: Path, *, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _summary_line(row: Mapping[str, Any]) -> str:
    return json.dumps({k: row.get(k) for k in
                       ("worker", "pid", "outcome", "action_id", "action_status",
                        "versions", "versions_after", "released")}, ensure_ascii=False)


def main() -> int:
    import fcntl
    import one_day_life as odl
    import runtime_recovery_review as review
    from tests.test_one_day_life import Services

    # AF_UNIX path cap: keep the fixture root short.
    root = Path("/tmp/nyf-infra-rec-%d" % os.getpid())
    if root.exists():
        raise SystemExit("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    services = Services(root / "supply")
    record: dict[str, Any] = {"root": str(root), "stages": {}}
    children: list[subprocess.Popen] = []

    def _stop_children() -> None:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                # kill 之后必须再 wait，确认自有子进程真的停住，不遗留 writer 进程。
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    say("  child still alive after SIGKILL: pid=%s" % child.pid)

    try:
        prepared = _prepare_root(root, supply_socket=str(services.supply_path),
                                 governance_socket=str(services.gov_path))
        instance = Path(prepared["instance"])
        # The workers use scripted decision/content doubles; they need no model configuration,
        # and a model key (or any unrelated operator environment) must never be written into a
        # fixture file.  Keep only the instance-owned names assembly actually reads.
        _keep = ("NYAIRO_", "NYA02_MEMORY_", "CHIYO_", "WORLD_", "REAL_")
        env = {name: value for name, value in prepared["env"].items()
               if name.startswith(_keep)}
        work = str(prepared["data"]["artifact_id"])
        env["WORK_ARTIFACT_ID"] = work
        _init_memory(Path(prepared["controls_db"]), Path(prepared["evidence_db"]))
        env_file = root / "worker-env.json"
        env_file.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        supply = str(services.supply_path)
        subject = str(prepared["data"]["subject_id"])
        record["work"] = work
        record["subject"] = subject

        # -- 1. read-only review of a fresh root -------------------------------------------------
        say("\n== 1. 只读检查：全新根可装配 ==")
        rc, cli, stderr = _run_cli(instance, "--supply-socket", supply, "--subject", subject)
        record["stages"]["cli_fresh"] = {"exit": rc, "class": cli.get("class"),
                                         "reasons": cli.get("reasons")}
        check("全新隔离根：CLI 判 ASSEMBLABLE 且 exit 0",
              rc == 0 and cli.get("class") == review.CLASS_ASSEMBLABLE,
              {"exit": rc, "class": cli.get("class"), "stderr": stderr})
        check("CLI 明确未取 flock、未重放 WAL",
              cli.get("flock_taken") is False
              and "no load_state" in str(cli.get("store_reads")),
              cli.get("store_reads"))
        check("备份恢复/迁移无正式入口，如实报缺口",
              (cli.get("gates") or {}).get("backup_restore", {}).get("status")
              == "NOT_IMPLEMENTED"
              and (cli.get("gates") or {}).get("migration", {}).get("status")
              == "NOT_IMPLEMENTED",
              cli.get("gates"))

        # -- 2. worker A: formal START, real owner version --------------------------------------
        say("\n== 2. 子进程 A：正式 START，真实 Supply 版本/Action ==")
        out_a = root / "out-begin.json"
        done_a = _run_worker("begin", instance=instance, env_file=env_file, out=out_a)
        a = _read(out_a)
        record["stages"]["begin"] = dict(a)
        check("子进程 A 正常结束并真实写出版本",
              done_a.returncode == 0 and a.get("outcome") == "ACTED"
              and a.get("action_status") == "SUCCEEDED" and int(a.get("versions") or 0) >= 2,
              {"exit": done_a.returncode, "worker": _summary_line(a),
               "stderr": done_a.stderr[-600:]})
        action_a = str(a.get("action_id") or "")
        activity_a = str(a.get("activity_ref") or "")
        versions_after_a = int(a.get("versions") or 0)

        # -- 3. worker B: different PID, same root, no replay -----------------------------------
        say("\n== 3. 子进程 B：不同 PID 同根装配，现有恢复入口不重发成功 ==")
        out_b = root / "out-restart.json"
        done_b = _run_worker("restart", instance=instance, env_file=env_file, out=out_b)
        b = _read(out_b)
        record["stages"]["restart"] = dict(b)
        check("子进程 B 是另一个 PID，状态与版本原样保留",
              done_b.returncode == 0 and int(b.get("pid") or 0) != int(a.get("pid") or -1)
              and int(b.get("versions_after") or -1) == versions_after_a,
              {"exit": done_b.returncode, "a_pid": a.get("pid"), "b_pid": b.get("pid"),
               "versions": b.get("versions_after"), "stderr": done_b.stderr[-600:]})
        check("B 的现有恢复入口没有新增 Action、没有新版本、0 次模型调用",
              int(b.get("actions_after") or -1) == int(b.get("actions_before") or -2)
              and int(b.get("versions_after") or -1) == int(b.get("versions_before") or -2)
              and int(b.get("llm_calls") or 0) == 0,
              b)

        # -- 4. worker C: kill at the real post-write hook --------------------------------------
        say("\n== 4. 子进程 C：owner 已提交、答复丢失处真强杀 ==")
        out_c = root / "out-crash.json"
        marker_c = root / "crash-hook.json"
        done_c = _run_worker("crash", instance=instance, env_file=env_file, out=out_c,
                             marker=marker_c)
        hook = _read(marker_c)
        record["stages"]["crash"] = {"exit": done_c.returncode, "hook": hook}
        check("强杀前已到达 response_lost_after_write 钩子",
              bool(hook.get("hook") == "response_lost_after_write")
              and int(hook.get("pid") or 0) != os.getpid(),
              {"hook": hook, "stderr": done_c.stderr[-600:]})
        check("子进程真的被信号杀死（returncode 为负）",
              done_c.returncode is not None and done_c.returncode < 0,
              {"exit": done_c.returncode})
        rc, cli_crash, stderr = _run_cli(instance, "--supply-socket", supply, "--subject", subject)
        record["stages"]["cli_crashed"] = {"exit": rc, "class": cli_crash.get("class"),
                                           "reasons": cli_crash.get("reasons")}
        operations = dict(cli_crash.get("owner_operations") or {})
        check("崩溃后 CLI 判 NEEDS_EXISTING_RECOVERY 而不是“安全”",
              rc == 0 and cli_crash.get("class") == review.CLASS_NEEDS_RECOVERY
              and any("ACTION_UNRESOLVED" in str(item) for item in cli_crash.get("reasons") or []),
              {"exit": rc, "class": cli_crash.get("class"),
               "reasons": cli_crash.get("reasons"), "stderr": stderr})
        check("CLI 通过 owner 只读操作查到效果已落地",
              operations.get("status") == "QUERIED"
              and any(row.get("result") == "OWNER_SAYS_LANDED"
                      for row in operations.get("rows") or []),
              operations)

        # -- 5. worker D: existing recovery entry, same operation -------------------------------
        say("\n== 5. 子进程 D：现有恢复入口按原 operation 对账，不写第二次 ==")
        out_d = root / "out-recover.json"
        done_d = _run_worker("recover", instance=instance, env_file=env_file, out=out_d,
                             timeout=240)
        d = _read(out_d)
        record["stages"]["recover"] = dict(d)
        check("恢复后版本数不变（owner 效果只落地一次）",
              done_d.returncode == 0
              and int(d.get("versions_after") or -1) == int(d.get("versions_before") or -2),
              {"exit": done_d.returncode, "versions": [d.get("versions_before"),
                                                       d.get("versions_after")],
               "stderr": done_d.stderr[-800:]})
        crash_action = str((hook.get("action_id") or "") if isinstance(hook, dict) else "")
        recovered = [row for row in d.get("actions") or []
                     if str(row.get("status")) == "SUCCEEDED"]
        check("恢复后该 Action 已 SUCCEEDED 且有 reconciliation 记录",
              bool(recovered) and any(row.get("reconciliation_refs") for row in recovered),
              d.get("actions"))
        check("恢复进程自己没有调用 owner 适配器提交",
              int(d.get("adapter_submit_calls") or 0) == 0
              and int(d.get("llm_calls") or 0) == 0,
              d)
        rc, cli_after, stderr = _run_cli(instance, "--supply-socket", supply, "--subject", subject)
        record["stages"]["cli_recovered"] = {"exit": rc, "class": cli_after.get("class")}
        check("恢复后 CLI 回到 ASSEMBLABLE",
              rc == 0 and cli_after.get("class") == review.CLASS_ASSEMBLABLE,
              {"exit": rc, "class": cli_after.get("class"), "stderr": stderr})

        # -- 6. CAS / idempotent replay ---------------------------------------------------------
        say("\n== 6. 旧 revision 提案被现有 CAS 拒，成功动作不重放 ==")
        out_e = root / "out-cas.json"
        done_e = _run_worker("cas", instance=instance, env_file=env_file, out=out_e)
        e = _read(out_e)
        record["stages"]["cas"] = dict(e)
        check("旧 expected_head 被拒且没有新版本",
              done_e.returncode == 0
              and int(e.get("versions_after") or -1) == int(e.get("versions_before") or -2)
              and str(e.get("stale_action_status")) in {"FAILED", "UNKNOWN"},
              {"exit": done_e.returncode, "worker": e, "stderr": done_e.stderr[-600:]})
        check("已 SUCCEEDED 的 Action 再次提交是幂等回放，不调用 owner",
              e.get("idempotent_replay") is True
              and int(e.get("adapter_submit_calls_delta")
                      if e.get("adapter_submit_calls_delta") is not None else -1) == 0,
              e)

        # -- 7. blocked I/O must not hold a store transaction lock ------------------------------
        say("\n== 7. 认知阻塞在 I/O 时，只读检查与 owner 读仍可继续 ==")
        out_f = root / "out-block.json"
        marker_f = root / "block-ready.json"
        release_f = root / "block-release"
        from life_artifact_port import LifeArtifactPort as _Port

        versions_before_block = int(_Port(supply, subject)
                                    .list_versions(work, limit=50)["total_versions"])
        block = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "block",
             "--instance", str(instance), "--env-file", str(env_file), "--out", str(out_f),
             "--marker", str(marker_f), "--release", str(release_f)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(block)
        started = _wait_for(marker_f, timeout=90)
        marker_row = _read(marker_f)
        check("阻塞子进程已进入认知调用等待", started
              and marker_row.get("state") == "cognition_blocked", marker_row)

        cli_ok = False
        cli_detail: dict[str, Any] = {}
        if started:
            rc, cli_block, stderr = _run_cli(instance, "--supply-socket", supply,
                                             "--subject", subject)
            cli_ok = rc in {0, 3}
            cli_detail = {"exit": rc, "class": cli_block.get("class"),
                          "writer": (cli_block.get("writer") or {}).get("status"),
                          "stderr": stderr}
        check("阻塞等待中只读 CLI 仍能返回（不跨等待持锁）", cli_ok, cli_detail)

        owner_read: dict[str, Any] = {}
        try:
            from life_artifact_port import LifeArtifactPort

            port = LifeArtifactPort(supply, subject)
            head = port.read_head(work) or {}
            owner_read = {"read": True, "version_id": head.get("version_id")}
        except Exception as exc:
            owner_read = {"read": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        check("阻塞等待中真实 Supply owner 读仍可继续", bool(owner_read.get("read")),
              owner_read)

        lock_probe: dict[str, Any] = {"probed": False}
        try:
            state_root = odl.life_state_root(instance)
            lock_path = state_root / "run" / ".agency_decision_io.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    lock_probe = {"probed": True, "held_by_worker": False,
                                  "lock": str(lock_path)}
                except OSError as exc:
                    lock_probe = {"probed": True, "held_by_worker": True,
                                  "detail": "%s: %s" % (type(exc).__name__, exc)}
            import action_reality_ledger  # noqa: F401  (named fact: IO lock is defined only)

            lock_probe["io_lock_call_sites"] = "none in action_reality_ledger"
        except Exception as exc:
            lock_probe = {"probed": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        check("等待中 AG-1 store 事务锁无人持有（真实写锁探测）",
              lock_probe.get("probed") is True
              and lock_probe.get("held_by_worker") is False, lock_probe)

        release_f.write_text("go\n", encoding="utf-8")
        _, block_err = block.communicate(timeout=180)
        children.remove(block)
        f = _read(out_f)
        record["stages"]["block"] = {"exit": block.returncode, "worker": f,
                                     "cli": cli_detail, "owner_read": owner_read,
                                     "lock_probe": lock_probe}
        check("解除阻塞后子进程正常收尾并退出，且没有新版本",
              block.returncode == 0
              and int(f.get("versions") if f.get("versions") is not None else -1)
              == versions_before_block,
              {"exit": block.returncode, "worker": f,
               "versions_before_block": versions_before_block,
               "stderr": (block_err or "")[-600:]})

        # -- 8. writer conflict: second assembly refused before a business write ---------------
        say("\n== 8. 写者冲突：第二个装配在业务写之前被 lease 拒 ==")
        out_h = root / "out-hold.json"
        marker_h = root / "hold-ready.json"
        release_h = root / "hold-release"
        hold = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "hold",
             "--instance", str(instance), "--env-file", str(env_file), "--out", str(out_h),
             "--marker", str(marker_h), "--release", str(release_h)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(hold)
        held = _wait_for(marker_h, timeout=90)
        rc, cli_held, stderr = _run_cli(instance, "--supply-socket", supply, "--subject", subject)
        record["stages"]["cli_held"] = {"exit": rc, "class": cli_held.get("class"),
                                        "writer": (cli_held.get("writer") or {}).get("status")}
        check("另一进程持 lease 时 CLI 判 MANUAL",
              held and rc == 3 and cli_held.get("class") == review.CLASS_MANUAL
              and (cli_held.get("writer") or {}).get("status") == review.CLASS_MANUAL,
              {"held": held, "exit": rc, "class": cli_held.get("class"), "stderr": stderr})

        out_conflict = root / "out-conflict.json"
        done_conflict = _run_worker("conflict", instance=instance, env_file=env_file,
                                    out=out_conflict)
        conflict = _read(out_conflict)
        release_h.write_text("go\n", encoding="utf-8")
        _, hold_err = hold.communicate(timeout=180)
        children.remove(hold)
        record["stages"]["conflict"] = {"exit": done_conflict.returncode, "worker": conflict}
        check("第二个真实装配被 writer 权威拒（零业务写）",
              done_conflict.returncode == 0 and conflict.get("refused") is True,
              {"exit": done_conflict.returncode, "worker": conflict,
               "stderr": done_conflict.stderr[-600:]})

        # -- 9. F165/F166 named gap --------------------------------------------------------------
        say("\n== 9. 备份/迁移：没有正式入口，如实留缺口 ==")
        rc, cli_final, stderr = _run_cli(instance, "--supply-socket", supply, "--subject", subject)
        gates = dict(cli_final.get("gates") or {})
        record["stages"]["cli_final"] = {"exit": rc, "class": cli_final.get("class"),
                                         "gates": gates}
        check("最终只读检查回到 ASSEMBLABLE，且不把 normal restart 称作 backup restore",
              rc == 0 and cli_final.get("class") == review.CLASS_ASSEMBLABLE
              and (gates.get("backup_restore") or {}).get("status") == "NOT_IMPLEMENTED",
              {"exit": rc, "class": cli_final.get("class"), "gates": gates, "stderr": stderr})

        record["failures"] = list(FAILURES)
        (root / "record.json").write_text(
            json.dumps(record, ensure_ascii=False, default=str, indent=2) + "\n",
            encoding="utf-8")
        say("\nRECORD " + json.dumps({"root": str(root), "failures": FAILURES},
                                     ensure_ascii=False))
        if FAILURES:
            say("FAILURES: %d" % len(FAILURES))
            return 1
        say("ALL_EXPECTATIONS_MET")
        return 0
    finally:
        _stop_children()
        try:
            services.stop()
        except Exception as exc:
            say("  supply stop error: %s: %s" % (type(exc).__name__, str(exc)[:200]))
        # 成功与失败都保留隔离根和 record：本演示不删除任何已产出的数据。
        try:
            record["failures"] = list(FAILURES)
            record["retained"] = True
            _emit(root / "record.json", record)
            say("KEPT " + json.dumps({"root": str(root),
                                      "record": str(root / "record.json"),
                                      "failures": list(FAILURES)}, ensure_ascii=False))
        except Exception as exc:
            say("  record write error: %s: %s" % (type(exc).__name__, str(exc)[:200]))


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="INFRA runtime recovery demo / worker")
    parser.add_argument("--worker", default=None,
                        choices=[None, "begin", "restart", "crash", "recover", "cas",
                                 "hold", "block", "conflict"])
    parser.add_argument("--instance", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--marker", default=None)
    parser.add_argument("--release", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    parsed = _parse()
    if parsed.worker:
        raise SystemExit(_worker_main(parsed))
    raise SystemExit(main())
