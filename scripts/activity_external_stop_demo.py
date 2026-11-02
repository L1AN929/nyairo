#!/usr/bin/env python3
"""Zero-model demo: a local operator stops one Activity across *real processes*.

What it really does (local only, 0 product model, 0 outbound network)
---------------------------------------------------------------------
1. Prepares a **real** instance through ``one_day_life.prepare`` with a real Life Supply
   owner on its own Unix socket, and runs one real round in this process (the scripted
   Decision double only picks inside the real AG-1 legal set) so Activity A has a committed
   owner version.
2. Serves the phone in a **separate process** (``--role phone``) with no runtime inside it,
   and runs a **separate production resident process** (``--role resident``) that holds the
   lease and drains the mailbox through the real AG-2 / LR-2 owner path.
3. The external HTTP answer is ``RUNTIME_MAILBOXED`` / not-accepted first; only after the
   resident consumes the signed request does the Activity carry an owner PENDING intent, and
   only after the owner confirms a real atomic-exit boundary does it become CANCELLED.
4. The prepared step is refused by the *production* submit gate while the intent is PENDING;
   another Activity is untouched; exactly one CANCELLED handoff per Activity; the committed
   version and checkpoint stay.
5. The CLI is driven against a local trap: a wrong endpoint, another instance, a wrong Life
   subject and a redirect target all receive **zero** requests and zero tokens.
6. A forged mailbox file and a correctly signed request from an *earlier serve epoch* are
   both refused by name; neither is turned into a stop.
7. C enters a real AR-0 UNKNOWN: the stop neither resends nor rolls back, and the original
   reconciliation settles it while the CANCELLED Activity stays terminal.
8. The phone serve is stopped through its own drain and served again in a **new process**:
   the old token/epoch are refused (even for an accepted operation key), a request mailed
   under the old serve is ``STALE_SERVE_EPOCH``, a new credential can read/request, and the
   intent the owner already accepted finishes once the LR-3 owner confirms the boundary.

Boundaries: no real model, no product instance, no external send, no World undo.  The operator
credential is a separate file from the private-diary one.  This is not G1, not a 24h run and
not a claim that a model naturally chooses anything.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/activity_external_stop_demo.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_external_stop as aes  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
from components.phone import operator_stop as phone_operator_stop  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SCRATCH = Path(os.environ.get("NYAIRO_EXTERNAL_STOP_ROOT")
               or "/tmp/nyairo-activity-external-stop")
API = "/api/phone"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:8000])
    raise SystemExit(1)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class _ScriptedDecisionModel:
    """Decision double: chooses only inside the real AG-1 legal set."""

    model_id = "stub:external-stop-demo"
    model_version = "external-stop-demo-v1"

    def __init__(self) -> None:
        self.policy = "start"
        self.target_artifact = ""
        self.calls = 0
        self.last_offered: dict = {}

    def _raw(self, candidates, allowed):
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.policy == "start":
            for row in candidates:
                refs = [str(ref) for ref in (row.get("target_refs") or [])]
                if ("START" in (row.get("legal_decisions") or [])
                        and not any(ref.startswith("closeout:") for ref in refs)
                        and (not self.target_artifact
                             or any(self.target_artifact in ref for ref in refs))):
                    return {"decision": "START", "selected_candidate_id": row["candidate_id"],
                            "reason_codes": reason, "short_rationale": "stub: start",
                            "deferred_candidate_ids": []}
        if self.policy == "continue":
            for row in candidates:
                if "CONTINUE" in (row.get("legal_decisions") or []):
                    return {"decision": "CONTINUE",
                            "selected_candidate_id": row["candidate_id"],
                            "reason_codes": reason, "short_rationale": "stub: continue",
                            "deferred_candidate_ids": []}
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": ["NO_ACTION_CHOSEN"],
                "short_rationale": "stub: no action", "deferred_candidate_ids": []}

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        self.last_offered = {
            "candidates": [{"candidate_id": row.get("candidate_id"),
                            "source_kind": row.get("source_kind"),
                            "activity_ref": row.get("activity_ref"),
                            "target_refs": row.get("target_refs"),
                            "legal_decisions": row.get("legal_decisions")}
                           for row in candidates],
        }
        raw = self._raw(candidates, allowed)
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:external-stop-demo:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class _Writer:
    def __init__(self) -> None:
        self.calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        return "%s\n\n（真实版本追加 %s）\n" % (point.content or "", observed_at)


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _head(port, artifact_id: str) -> str:
    row = port.read_head(artifact_id) or {}
    return str(row.get("version_id") or "")


def _new_activity_id(runtime, before: set[str]) -> str:
    after = {str(row.get("activity_id"))
             for row in runtime.activity_read.list_open_activities()}
    new = sorted(after - before)
    return new[0] if len(new) == 1 else ""


def _handoffs(runtime, activity_id: str, *, kind: str) -> list[dict]:
    state = runtime.coordinator.integration_commit.read_state() or {}
    return [dict(row) for row in (state.get("handoff_events_by_id") or {}).values()
            if str(row.get("activity_ref") or "") == activity_id
            and row.get("terminal_or_milestone_kind") == kind]


def _build_runtime(instance: Path, state_root: Path, data, cognition, writer,
                   port: lap.LifeArtifactPort):
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=instance / "life", artifact_action_adapter=adapter)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=odl.instance_environment(instance), state_root=state_root,
        writer=writer, enabled=True)
    _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
    return runtime, assembled["cycle"]


class _Http:
    """One real loopback client; the token is only ever put in the operator header."""

    def __init__(self, port: int) -> None:
        self.base = "http://127.0.0.1:%d" % int(port)

    def post(self, path: str, body: dict, *, token: Optional[str]) -> tuple[int, dict]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Sec-Fetch-Site", "none")
        if token is not None:
            request.add_header("X-Phone-Operator-Stop", token)
        return self._send(request)

    def get(self, path: str, *, token: Optional[str]) -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, method="GET")
        request.add_header("Sec-Fetch-Site", "none")
        if token is not None:
            request.add_header("X-Phone-Operator-Stop", token)
        return self._send(request)

    @staticmethod
    def _send(request) -> tuple[int, dict]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return int(exc.code), json.loads(raw)
            except ValueError:
                return int(exc.code), {"raw": raw[:200]}


class _Trap:
    """A local HTTP server that records every request it receives (never a token value)."""

    def __init__(self, status: int = 200, location: Optional[str] = None) -> None:
        self.hits: list[dict] = []
        trap = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                trap.hits.append({"method": "GET", "path": self.path,
                                  "has_operator_header": bool(
                                      self.headers.get("X-Phone-Operator-Stop")),
                                  "has_authorization": bool(self.headers.get("Authorization"))})
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = do_GET

            def log_message(self, *_args):  # silence
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def count(self) -> int:
        return len(self.hits)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _run_cli(*args: str) -> tuple[int, dict]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "activity_stop_cli.py"), *args]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError:
        payload = {"raw": (done.stdout or "")[:400], "stderr": (done.stderr or "")[:400]}
    return int(done.returncode), payload


# ---------------------------------------------------------------------------------------------
# child roles
# ---------------------------------------------------------------------------------------------


def role_phone(argv: list[str]) -> int:
    """The serving phone process: real HTTP server, **no** runtime in this process."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="phone")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--info-out", required=True)
    parser.add_argument("--stop-file", required=True)
    args = parser.parse_args(argv)

    instance = Path(args.instance).resolve()
    state_root = Path(args.state_root).resolve()
    data = odl.read_instance(instance)
    live_instance = {
        "root": str(instance),
        "subject": str(data["subject_id"]),
        "world_home": str(data["world_home"]),
        "supply_socket": str(data["supply_socket"]),
        "private_items_artifact_id": str(data.get("private_items_artifact_id") or ""),
        "artifact_grant_id": str(data["artifact_grant_id"]),
        "workspace_id": str(data["workspace_id"]),
        "reading_artifact_id": str(data.get("reading_artifact_id") or ""),
        "features": {},
        "environment": dict(data.get("environment") or {}),
    }
    try:
        life_subject = phone_service.canonical_life_subject(state_root)
        if not life_subject:
            raise RuntimeError("the phone child found no canonical Life subject")
        phone = phone_service.PhoneService(live_instance, activity_stop=None,
                                           life_state_root=state_root)
        httpd = phone_server.create_server(phone, csrf_token="demo-csrf", port=0,
                                           log_path=Path(args.info_out).with_suffix(".log"))
    except Exception as exc:
        Path(args.info_out).write_text(json.dumps({
            "role": "phone", "error": "%s: %s" % (type(exc).__name__, exc)}),
            encoding="utf-8")
        return 1
    bound = int(httpd.server_address[1])
    endpoint = "http://127.0.0.1:%d" % bound
    credential = phone_operator_stop.issue(
        instance, subject=str(live_instance["subject"]), life_subject=life_subject,
        endpoint=endpoint)[1]
    httpd.set_operator_stop(credential)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    Path(args.info_out).write_text(json.dumps({
        "role": "phone", "pid": os.getpid(), "port": bound, "endpoint": endpoint,
        "epoch": credential.epoch,
        "credential_file": str(phone_operator_stop.credential_path(instance)),
        "activity_stop_in_process": phone._activity_stop is not None,
    }), encoding="utf-8")
    stop_file = Path(args.stop_file)
    deadline = time.monotonic() + 300
    while not stop_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    httpd.shutdown()
    httpd.server_close()
    phone_operator_stop.discard(instance, credential)
    return 0


def role_resident(argv: list[str]) -> int:
    """The production resident process: real runtime, real lease, drains the mailbox."""

    from autonomous_life_resident import AutonomousLifeResident

    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="resident")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase", default="drain")
    parser.add_argument("--enter-atomic-activity", default="")
    parser.add_argument("--probe-step-activity", default="")
    parser.add_argument("--prepare-step-activity", default="")
    parser.add_argument("--observe-activity", default="")
    args = parser.parse_args(argv)

    instance = Path(args.instance).resolve()
    state_root = Path(args.state_root).resolve()
    out: dict[str, Any] = {"role": "resident", "phase": args.phase, "pid": os.getpid()}
    resident = AutonomousLifeResident(
        state_root=state_root, environment=odl.instance_environment(instance),
        act=False, offline_scripted=True, poll_seconds=1)
    code = 0
    try:
        started = resident.start()
        _check(started.get("ok") is True, "resident did not start", started)
        runtime = resident.runtime
        if args.enter_atomic_activity:
            region = runtime.interruption_coordinator.enter_atomic_region(
                activity_id=args.enter_atomic_activity,
                atomic_region_ref="atomic_reg:demo-a:1", max_duration_seconds=300)
            out["atomic_entered"] = region.to_dict()
        # A step prepared *before* the stop exists is the case the submit fence must catch.
        prepared_step: list[Any] = []
        if args.prepare_step_activity:
            data_now = odl.read_instance(instance)
            state = runtime.coordinator.integration_commit.read_state() or {}
            ids = list((state.get("integration_ids_by_activity") or {}).get(
                args.prepare_step_activity) or [])
            prepared: dict[str, Any] = {"integration_ids": len(ids), "action_id": ""}
            if ids:
                import autonomous_life_cycle as _alc

                record = (state.get("integrations_by_id") or {}).get(str(ids[0])) or {}
                artifact_id = str(data_now["artifact_id"])
                prepare_port = lap.LifeArtifactPort(
                    data_now["supply_socket"], data_now["subject_id"])
                head_now = dict(prepare_port.read_head(artifact_id) or {})
                bridge_res = runtime.coordinator.action_bridge.create_step_and_propose_action(
                    activity_id=args.prepare_step_activity,
                    decision_ref="decision:demo-prepared-step",
                    adoption_ref="adoption:demo-prepared-step",
                    correlation_id=str(record.get("correlation_id") or "demo-correlation"),
                    step_kind=ag2.STEP_KIND_FILE_ACTION,
                    action_kind=_alc.ACTION_KIND_FILE,
                    target_ref="artifact:" + artifact_id,
                    # The same payload shape the real cycle uses for a version commit.
                    payload_spec={
                        "op": _alc.OP_COMMIT_VERSION,
                        "artifact_id": artifact_id,
                        "expected_head": head_now.get("version_id"),
                        "content": (head_now.get("content") or "")
                        + "\n\n（外部叫停前已准备、未提交的版本）\n",
                        "grant_id": str(data_now["artifact_grant_id"]),
                    },
                    occurred_at=_iso(datetime.now(timezone.utc)))
                action = bridge_res.get("action") or {}
                prepared["action_id"] = str(action.get("action_id") or "")
                prepared["action_status"] = str(action.get("status") or "")
                prepared_step = [bridge_res.get("step_spec"), prepared["action_id"]]
            out["prepared_step"] = prepared
        out["ticks"] = resident.run(max_ticks=2).get("ticks")
        out["drain"] = _journal_stop_results(getattr(resident, "journal_path", None))
        if args.enter_atomic_activity:
            act = _activity(runtime, args.enter_atomic_activity)
            out["activity_after_drain"] = {
                "status": act.get("status"), "revision": act.get("revision"),
                "stop_state": (act.get("external_stop") or {}).get("state"),
                "intent_id": (act.get("external_stop") or {}).get("intent_id")}
        if prepared_step:
            # The step was already prepared before the intent existed; crossing the *submit*
            # fence must re-read the live Activity and refuse.  Nothing may be written.
            data_now = odl.read_instance(instance)
            port2 = lap.LifeArtifactPort(data_now["supply_socket"], data_now["subject_id"])
            submit: dict[str, Any] = {
                "action_id": prepared_step[1], "refused": False, "stage": "", "error": "",
                "head_before": _head(port2, data_now["artifact_id"]), "head_after": ""}
            try:
                runtime.coordinator.action_bridge.submit_step_action_and_apply_post_submit_behavior(
                    step_spec=prepared_step[0], action_id=prepared_step[1],
                    executor_capability=runtime.coordinator.executor_capability,
                    settle_immediately=True,
                    occurred_at=_iso(datetime.now(timezone.utc)))
            except ag2.ActivityExternalStopPending as exc:
                submit["refused"] = True
                submit["stage"] = str(exc.stage)
                submit["error"] = "%s at %s" % (exc.intent_id, exc.stage)
            except Exception as exc:
                submit["error"] = "%s: %s" % (type(exc).__name__, exc)
            submit["head_after"] = _head(port2, data_now["artifact_id"])
            out["prepared_submit"] = submit

        if args.probe_step_activity:
            # The *production* submit gate, on the real owner record, in this process.
            state = runtime.coordinator.integration_commit.read_state() or {}
            ids = list((state.get("integration_ids_by_activity") or {}).get(
                args.probe_step_activity) or [])
            artifact = str((odl.read_instance(instance) or {}).get("artifact_id") or "")
            probe = {"integration_ids": len(ids), "refused": False, "stage": "",
                     "error": "", "head_before": "", "head_after": ""}
            if ids:
                port = lap.LifeArtifactPort(
                    odl.read_instance(instance)["supply_socket"],
                    odl.read_instance(instance)["subject_id"])
                probe["head_before"] = _head(port, artifact)
                try:
                    runtime.coordinator.execute_activity_step(
                        integration_id=str(ids[0]), activity_id=args.probe_step_activity,
                        decision_ref="decision:demo-stop-gate", adoption_ref="adoption:demo",
                        step_spec_input={"step_kind": "ARTIFACT_CONTINUE",
                                         "target_ref": "artifact:" + artifact,
                                         "payload_spec": {}},
                        settle_immediately=True,
                        occurred_at=_iso(datetime.now(timezone.utc)))
                except ag2.ActivityExternalStopPending as exc:
                    probe["refused"] = True
                    probe["stage"] = str(exc.stage)
                    probe["error"] = "%s at %s" % (exc.intent_id, exc.stage)
                except Exception as exc:
                    probe["error"] = "%s: %s" % (type(exc).__name__, exc)
                probe["head_after"] = _head(port, artifact)
            out["step_probe"] = probe
        for activity_id in (args.enter_atomic_activity, args.probe_step_activity,
                            args.observe_activity):
            if not activity_id:
                continue
            act = _activity(runtime, activity_id)
            out.setdefault("activities", {})[activity_id] = {
                "status": act.get("status"), "revision": act.get("revision"),
                "stop_state": (act.get("external_stop") or {}).get("state"),
                "terminal_reason_code": act.get("terminal_reason_code"),
                "checkpoint_ref": act.get("checkpoint_ref"),
                "handoffs": len(_handoffs(runtime, activity_id,
                                         kind=ag2.HANDOFF_KIND_CANCELLED)),
                "handoff": (_handoffs(runtime, activity_id,
                                      kind=ag2.HANDOFF_KIND_CANCELLED) or [{}])[-1],
                "foreground": runtime.activity_read.get_current_life_view().get(
                    "foreground_activity_ref")}
        if args.probe_step_activity or args.observe_activity:
            artifact = str((odl.read_instance(instance) or {}).get("artifact_id") or "")
            out["artifact_head"] = _head(
                lap.LifeArtifactPort(odl.read_instance(instance)["supply_socket"],
                                     odl.read_instance(instance)["subject_id"]), artifact)
    except SystemExit as exc:  # ``_check`` already explained itself
        out["error"] = "CHECK_FAILED"
        code = int(exc.code or 1)
        if not out.get("drain") and resident.last_tick:
            out["drain"] = list(resident.last_tick.get("external_stop") or [])
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        code = 1
    finally:
        try:
            stopped = resident.stop()
            out["stop"] = {"stopped_at": stopped.get("stopped_at"),
                           "reason": stopped.get("reason")}
        except Exception as exc:
            out["stop"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=str, indent=1),
                             encoding="utf-8")
    return code


# ---------------------------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------------------------


def _journal_stop_results(journal_path) -> list[dict]:
    """Every mailbox answer this resident wrote, in order, straight from its own journal."""

    if journal_path is None:
        return []
    out: list[dict] = []
    try:
        lines = Path(journal_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        rows = record.get("external_stop")
        if isinstance(rows, list):
            out.extend(dict(row) for row in rows if isinstance(row, dict))
    return out


def _spawn(role: str, *args: str) -> subprocess.Popen:
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--role", role, *args]
    return subprocess.Popen(command, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def _wait_for(path: Path, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        time.sleep(0.1)
    raise SystemExit("DEMO_FAIL: timed out waiting for %s" % path)


def _mailbox(state_root: Path) -> Path:
    return state_root / "external-stop"


def _write_mailbox_file(state_root: Path, payload: dict, name: str) -> Path:
    directory = _mailbox(state_root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("%s.json" % name)
    target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True),
                      encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(prog="activity_external_stop_demo.py")
    parser.add_argument("--role", default="orchestrator",
                        choices=("orchestrator", "phone", "resident"))
    known, rest = parser.parse_known_args()
    if known.role == "phone":
        return role_phone(rest)
    if known.role == "resident":
        return role_resident(rest)

    from tests.test_one_day_life import Services

    run = SCRATCH / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run.mkdir(parents=True, exist_ok=True)
    services = Services(run / "services")
    runtime = None
    phone_child: Optional[subprocess.Popen] = None
    phone_stop_path: Optional[Path] = None
    summary: dict = {
        "root": str(run),
        "limits": [
            "Decision and content are scripted doubles; no product model was called",
            "the Supply owner, LR-2/LR-4/LR-5/AR-0/AR-1, AG-0/AG-1/AG-2 are real",
            "the phone HTTP server and the resident are different processes, each with its own "
            "PID; the resident holds the real single-writer lease and its own loop drains here",
            "the ATOMIC region is closed by the LR-3 owner's exit_atomic_region boundary entry, "
            "not by writing the store's active flag",
            "this is not G1, not a 24h run and not evidence of natural model adoption",
        ],
    }
    try:
        instance = run / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:external-stop",
            title="External stop demo",
            seed_content="种子正文第一句：真实 Supply 作品。\n")
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        state_root = odl.life_state_root(instance)
        seed = str(data["artifact_id"])
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        cognition = _ScriptedDecisionModel()
        writer = _Writer()
        cognition.target_artifact = seed

        # ---------------------------------------------------------- phase 1: real round A
        runtime, cycle = _build_runtime(instance, state_root, data, cognition, writer, port)
        t0 = datetime.now(timezone.utc)
        clock = {"n": 0}

        def _tick() -> str:
            clock["n"] += 1
            return _iso(t0 + timedelta(minutes=clock["n"]))

        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        first = cycle.evaluate_once(trigger_ref="evt:stop:1", observed_at=_tick(), act=True)
        act_a = _new_activity_id(runtime, open_before) or str(first.get("activity_ref") or "")
        _check(bool(act_a), "round 1 created no Activity", {
            "outcome": first.get("outcome"), "detail": first.get("detail"),
            "offered": cognition.last_offered})
        a_live = _activity(runtime, act_a)
        head_v1 = _head(port, seed)
        _check(a_live.get("status") == "ACTIVE", "A is not ACTIVE", a_live)
        _check(bool(head_v1), "the owner reports no head after the first step", a_live)

        a_revision = int(_activity(runtime, act_a).get("revision") or 0)
        runtime.close()
        runtime = None

        # ---------------------------------------------------------- phase 2: phone process
        info_path = run / "phone.info.json"
        phone_child = _spawn("phone", "--instance", str(instance),
                             "--state-root", str(state_root),
                             "--info-out", str(info_path),
                             "--stop-file", str(run / "phone.stop"))
        phone_stop_path = run / "phone.stop"
        info = _wait_for(info_path)
        _check("error" not in info, "the phone child failed", info)
        _check(info.get("activity_stop_in_process") is False,
               "the phone child has a runtime in process", info)
        _check(int(info["pid"]) != os.getpid(), "the phone is not a separate process", info)
        client = _Http(int(info["port"]))
        credential = phone_operator_stop.read_credential(
            phone_operator_stop.credential_path(instance))
        good = credential.token
        summary["processes"] = {"orchestrator": os.getpid(), "phone": info["pid"],
                                "phone_port": info["port"]}

        # ---------------------------------------------------------- phase 3: CLI traps
        trap = _Trap()
        redirect_target = _Trap()
        redirect = _Trap(status=302,
                         location="http://127.0.0.1:%d/api/phone/control/activity/stop"
                                  % redirect_target.port)
        cli_checks: dict[str, Any] = {}
        try:
            code, payload = _run_cli("--instance", str(instance), "--activity", act_a,
                                     "--revision", str(a_revision), "--endpoint",
                                     "http://127.0.0.1:%d" % trap.port)
            cli_checks["wrong_endpoint"] = {"exit": code, "reason": payload.get("reason_code"),
                                            "sent": payload.get("sent")}
            _check(code == 2 and payload.get("sent") is False
                   and payload.get("reason_code") == "ENDPOINT_NOT_BOUND",
                   "a wrong endpoint was not refused locally", payload)
            _check(trap.count == 0, "the wrong endpoint received a request", trap.hits)

            forged_root = run / "other-instance"
            (forged_root / "run").mkdir(parents=True, exist_ok=True)
            (forged_root / "life" / "state").mkdir(parents=True, exist_ok=True)
            (forged_root / "instance.json").write_text(json.dumps({
                "schema_version": "chiyo.life.one_day.v1", "subject": "test:external-stop"}),
                encoding="utf-8")
            (forged_root / "life" / "state" / "canonical_life_state.json").write_text(
                json.dumps({"subject_id": "someone.else"}), encoding="utf-8")
            copied = json.loads(phone_operator_stop.credential_path(instance).read_text(
                encoding="utf-8"))
            phone_operator_stop.credential_path(forged_root).write_text(
                json.dumps(copied), encoding="utf-8")
            code, payload = _run_cli("--instance", str(forged_root), "--activity", act_a,
                                     "--revision", "1", "--endpoint",
                                     "http://127.0.0.1:%d" % int(info["port"]))
            cli_checks["copied_credential"] = {
                "exit": code, "reason": payload.get("reason_code"),
                "sent": payload.get("sent")}
            _check(code == 2 and payload.get("reason_code") == "CREDENTIAL_INSTANCE_MISMATCH",
                   "a credential copied to another instance was not refused", payload)

            # A redirect can only be tested where the *bound* endpoint is the redirector: a
            # synthetic instance whose serve is that server.  The token must never follow it.
            redirect_root = run / "redirect-instance"
            (redirect_root / "run").mkdir(parents=True, exist_ok=True)
            (redirect_root / "life" / "state").mkdir(parents=True, exist_ok=True)
            (redirect_root / "instance.json").write_text(json.dumps({
                "schema_version": "chiyo.life.one_day.v1", "subject": "test:redirect"}),
                encoding="utf-8")
            (redirect_root / "life" / "state" / "canonical_life_state.json").write_text(
                json.dumps({"subject_id": "chiyo.global"}), encoding="utf-8")
            phone_operator_stop.issue(
                redirect_root, subject="test:redirect", life_subject="chiyo.global",
                endpoint="http://127.0.0.1:%d" % redirect.port)
            code, payload = _run_cli("--instance", str(redirect_root), "--activity", act_a,
                                     "--revision", "1", "--endpoint",
                                     "http://127.0.0.1:%d" % redirect.port)
            cli_checks["redirect"] = {"exit": code, "http": payload.get("http"),
                                      "reason": (payload.get("result") or {}).get(
                                          "reason_code")}
            _check(redirect.count == 1, "the bound endpoint did not receive the request",
                   redirect.hits)
            _check(redirect_target.count == 0,
                   "the operator token followed a redirect", redirect_target.hits)
            _check(((payload.get("result") or {}).get("reason_code"))
                   == "HTTP_REDIRECT_REFUSED",
                   "a redirect was not refused by the CLI", payload)
            _check(trap.count == 0 and redirect_target.count == 0,
                   "a trap received an operator request",
                   {"trap": trap.hits, "redirect_target": redirect_target.hits})
        finally:
            trap.stop()
            redirect.stop()
            redirect_target.stop()
        summary["cli_negative_controls"] = cli_checks
        summary["credential_traps"] = {"trap_hits": 0, "redirect_target_hits": 0}

        # ---------------------------------------------------------- phase 4: mail a stop
        body = {"activity_id": act_a, "expected_revision": a_revision,
                "operation_key": "demo:stop:a:1", "reason_code": "OPERATOR_REQUEST"}
        wrong_token = client.post(f"{API}/control/activity/stop", body, token="x" * 48)
        _check(wrong_token[0] == 403, "a wrong token was not refused", wrong_token)
        mailed = client.post(f"{API}/control/activity/stop", body, token=good)
        _check(mailed[0] == 200, "the signed stop request was not accepted", mailed)
        mailed_data = mailed[1].get("data") or {}
        _check(mailed_data.get("reason_code") == "RUNTIME_MAILBOXED",
               "the phone did not answer RUNTIME_MAILBOXED", mailed)
        _check(mailed_data.get("accepted_as_stop") is False,
               "a queued file was reported as an accepted stop", mailed)
        _check(_activity_readonly(state_root, act_a).get("status") == "ACTIVE",
               "a queued mailbox file changed the Activity", _activity_readonly(state_root, act_a))
        state_read = client.get(
            "%s/control/activity/stop/state?activity_id=%s" % (API, act_a), token=good)
        _check((state_read[1].get("data") or {}).get("stop_state") == "QUEUED",
               "the read face did not show the request as QUEUED", state_read)
        summary["queued_before_resident"] = {
            "phone_status": mailed[1].get("status"),
            "phone_reason": mailed_data.get("reason_code"),
            "accepted_as_stop": mailed_data.get("accepted_as_stop"),
            "read_face_stop_state": (state_read[1].get("data") or {}).get("stop_state"),
        }

        # ---------------------------------------------------------- phase 5: resident ATOMIC
        out1 = run / "resident-pending.json"
        child = _spawn("resident", "--instance", str(instance), "--state-root", str(state_root),
                       "--out", str(out1), "--phase", "pending",
                       "--enter-atomic-activity", act_a, "--probe-step-activity", act_a,
                       "--prepare-step-activity", act_a)
        code = child.wait(timeout=600)
        result1 = json.loads(out1.read_text(encoding="utf-8"))
        _check(code == 0, "the resident child exited non-zero", {"code": code,
                                                                 "result": result1})
        _check(int(result1["pid"]) not in (os.getpid(), int(info["pid"])),
               "the resident is not a separate process", result1)
        summary["processes"]["resident_pending"] = result1["pid"]
        _check(result1["activity_after_drain"]["stop_state"] == "PENDING",
               "the resident did not durably accept the intent", result1)
        _check(result1["activity_after_drain"]["status"] == "ACTIVE",
               "a pending stop cancelled the Activity before the boundary", result1)
        probe = result1.get("step_probe") or {}
        prepared = result1.get("prepared_step") or {}
        submit = result1.get("prepared_submit") or {}
        _check(bool(prepared.get("action_id")), "no Action was prepared before the stop",
               prepared)
        _check(submit.get("refused") is True,
               "the submit fence did not refuse the prepared step", submit)
        _check(submit.get("stage") == "before_submit",
               "the prepared step was refused at the wrong stage", submit)
        _check(submit.get("head_before") == submit.get("head_after"),
               "a prepared step was written after the stop intent", submit)
        _check(probe.get("refused") is True, "the proposal gate did not refuse a new step",
               probe)
        _check(probe.get("head_before") == probe.get("head_after"),
               "a step was written after the stop intent", probe)
        _check(result1["activities"][act_a]["handoffs"] == 0,
               "a handoff was emitted before the boundary", result1["activities"][act_a])
        summary["pending_and_gate"] = {
            "drain_statuses": [row.get("status") for row in result1.get("drain") or []],
            "stop_state": result1["activity_after_drain"]["stop_state"],
            "prepared_action": prepared.get("action_id"),
            "submit_fence_refused": submit.get("refused"),
            "submit_fence_stage": submit.get("stage"),
            "proposal_gate_refused": probe.get("refused"),
            "head_unchanged": (submit.get("head_before") == submit.get("head_after")
                               and probe.get("head_before") == probe.get("head_after")),
        }

        # ---------------------------------------------------------- phase 6: real boundary
        runtime, cycle = _build_runtime(instance, state_root, data, cognition, writer, port)
        a_now = _activity(runtime, act_a)
        bad_exit = ""
        try:
            runtime.interruption_coordinator.exit_atomic_region(
                activity_id=act_a, atomic_region_ref="atomic_reg:demo-a:1",
                boundary_ref="atomic_exit:demo-a:1",
                expected_activity_revision=int(a_now["revision"]) + 1)
        except Exception as exc:
            bad_exit = type(exc).__name__
        _check(bad_exit != "", "a wrong-revision atomic exit was accepted")
        _check(_activity(runtime, act_a).get("status") == "ACTIVE",
               "a refused boundary closed the Activity", _activity(runtime, act_a))
        region = runtime.interruption_coordinator.exit_atomic_region(
            activity_id=act_a, atomic_region_ref="atomic_reg:demo-a:1",
            boundary_ref="atomic_exit:demo-a:1",
            expected_activity_revision=int(a_now["revision"]))
        _check(region.get("active") is False and region.get("boundary_ref")
               == "atomic_exit:demo-a:1", "the owner did not record the boundary", region)
        runtime.close()
        runtime = None

        resend = client.post(f"{API}/control/activity/stop", body, token=good)
        _check(resend[0] == 200, "the finalizing request was not accepted", resend)

        out2 = run / "resident-final.json"
        child = _spawn("resident", "--instance", str(instance), "--state-root", str(state_root),
                       "--out", str(out2), "--phase", "final",
                       "--observe-activity", act_a)
        code = child.wait(timeout=600)
        result2 = json.loads(out2.read_text(encoding="utf-8"))
        _check(code == 0, "the finalizing resident exited non-zero", {"code": code,
                                                                      "result": result2})
        summary["processes"]["resident_final"] = result2["pid"]
        after_a = (result2.get("activities") or {}).get(act_a) or {}
        _check(after_a.get("status") == "CANCELLED",
               "A was not cancelled after the boundary", result2)
        _check(after_a.get("terminal_reason_code") == "CANCELLED_BY_EXTERNAL_OPERATOR",
               "the terminal reason is not the operator's", after_a)
        _check(after_a.get("handoffs") == 1, "CANCELLED did not emit exactly one handoff",
               after_a)
        _check(result2.get("artifact_head") == head_v1,
               "cancelling rewrote a committed version", result2)
        drain_final = (result2.get("drain") or [{}])[-1]
        handoff_final = dict(drain_final.get("handoff") or {})
        # The report must keep a *failed delivery* apart from a consumer that deferred.  In
        # this isolated root no Memory database is configured, so the production consumer is
        # the fail-closed ``RefusingExperienceConsumer``: it raises, AG-2 records
        # FAILED_PENDING_RETRY with a null disposition, and the handoff stays pending for a
        # later retry.  That is reported by name -- never rounded up to "delivered".
        _check(handoff_final.get("status") == handoff_final.get("delivery_status"),
               "the handoff report collapsed two different outcomes", handoff_final)
        if handoff_final.get("delivery_status") == "FAILED_PENDING_RETRY":
            _check(handoff_final.get("consumer_disposition") is None
                   and handoff_final.get("error_class") == "ProductionExecutionRefused",
                   "a failed delivery is not reported with its error class", handoff_final)
        summary["cancelled"] = {
            "status": after_a.get("status"),
            "stop_state": after_a.get("stop_state"),
            "reason": after_a.get("terminal_reason_code"),
            "handoffs": after_a.get("handoffs"),
            "handoff_id": handoff_final.get("handoff_id"),
            "handoff_status": handoff_final.get("status"),
            "handoff_delivery_status": handoff_final.get("delivery_status"),
            "handoff_disposition": handoff_final.get("consumer_disposition"),
            "handoff_error_class": handoff_final.get("error_class"),
            "handoff_error_reason": handoff_final.get("error_reason"),
            "pending_handoff_count": handoff_final.get("pending_handoff_count"),
            "version_kept": result2.get("artifact_head") == head_v1,
        }

        state_after = client.get(
            "%s/control/activity/stop/state?activity_id=%s" % (API, act_a), token=good)
        data_after = state_after[1].get("data") or {}
        _check(data_after.get("stop_state") == "FINALIZED",
               "the read face does not show FINALIZED", state_after)
        _check(data_after.get("activity_status") == "CANCELLED",
               "the read face does not show CANCELLED", state_after)
        summary["read_face_after"] = {"stop_state": data_after.get("stop_state"),
                                      "activity_status": data_after.get("activity_status")}

        # Replay of the same operation: no second handoff, no second cancel.
        replay = client.post(f"{API}/control/activity/stop", body, token=good)
        _check(replay[0] == 200, "a replay was refused", replay)
        _check(replay[1].get("status") == "CANCELLED",
               "a replay did not answer with the terminal", replay)

        # ------------------------------- phase 6b: UNKNOWN stop, then another Activity starts
        runtime, cycle = _build_runtime(instance, state_root, data, cognition, writer, port)
        artifact_c = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="第三件作品",
            content="第三件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("external-stop-demo", "create-c", "c"))
        _check(artifact_c.applied, "the owner did not create C", artifact_c.to_dict())
        c_id = str(artifact_c.artifact_id or "")
        cognition.target_artifact = c_id
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        third = cycle.evaluate_once(trigger_ref="evt:stop:3", observed_at=_tick(), act=True)
        act_c = _new_activity_id(runtime, open_before) or str(third.get("activity_ref") or "")
        _check(bool(act_c), "no Activity was started for C", {
            "outcome": third.get("outcome"), "detail": third.get("detail"),
            "offered": cognition.last_offered})
        c_head_before = _head(port, c_id)
        runtime.artifact_action_adapter.fault_injection_drop_response_after_write = True
        cognition.policy = "continue"
        fourth = cycle.evaluate_once(trigger_ref="evt:stop:4", observed_at=_tick(), act=True)
        runtime.artifact_action_adapter.fault_injection_drop_response_after_write = False
        _check(_head(port, c_id) != c_head_before,
               "the owner did not really write although the answer was lost", {
                   "before": c_head_before, "after": _head(port, c_id),
                   "round": {k: fourth.get(k) for k in ("outcome", "detail", "error")}})
        unknown_rows = [dict(row) for row in runtime.action_read.list_actions(status="UNKNOWN")]
        _check(bool(unknown_rows), "no real UNKNOWN Action was recorded",
               [row.get("status") for row in runtime.action_read.list_actions()])
        unknown_ref = str(unknown_rows[0]["action_id"])
        c_live = _activity(runtime, act_c)
        request_c = aes.build_request(
            activity_id=act_c, expected_revision=int(c_live["revision"]),
            operation_key="demo:stop:c:unknown", reason_code="OPERATOR_REQUEST",
            subject_id=str(c_live.get("subject_id") or ""),
            epoch=credential.epoch, unresolved_action_refs=[unknown_ref])
        stopped_c = runtime.activity_stop.handle(request_c)
        _check(stopped_c.get("status") == aes.STATUS_CANCELLED,
               "C was not cancelled", stopped_c)
        c_after = _activity(runtime, act_c)
        c_unresolved = list((c_after.get("external_stop") or {}).get(
            "unresolved_action_refs") or [])
        _check(c_unresolved == [unknown_ref], "the unresolved Action was not preserved",
               {"stored": c_unresolved, "unknown_ref": unknown_ref})
        status_before_reconcile = str(
            (runtime.action_read.get_action(unknown_ref) or {}).get("status"))
        reconcile_error = ""
        try:
            cycle.reconcile_unknown_step(action_id=unknown_ref, occurred_at=_tick())
        except Exception as exc:
            reconcile_error = "%s: %s" % (type(exc).__name__, exc)
        c_reconciled = _activity(runtime, act_c)
        c_head_after_reconcile = _head(port, c_id)
        _check(c_reconciled.get("status") == "CANCELLED",
               "reconciliation resurrected the CANCELLED Activity", c_reconciled)
        _check(c_head_after_reconcile == _head(port, c_id),
               "reconciliation replayed the write", c_head_after_reconcile)
        summary["unknown_then_stop"] = {
            "activity_ref": act_c, "action": unknown_ref,
            "action_status_before": status_before_reconcile,
            "stop_status": stopped_c.get("status"), "reconcile_error": reconcile_error,
            "activity_after": c_reconciled.get("status"), "no_resend": True}

        artifact_b = port.create_artifact(
            workspace_id=str(data["workspace_id"]), title="另一件作品",
            content="另一件作品的正文。\n", grant_id=str(data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("external-stop-demo", "create-b", "b"))
        _check(artifact_b.applied, "the owner did not create B", artifact_b.to_dict())
        b_id = str(artifact_b.artifact_id or "")
        cognition.target_artifact = b_id
        cognition.policy = "start"
        open_before = {str(row.get("activity_id"))
                       for row in runtime.activity_read.list_open_activities()}
        second = cycle.evaluate_once(trigger_ref="evt:stop:2", observed_at=_tick(), act=True)
        act_b = _new_activity_id(runtime, open_before) or str(second.get("activity_ref") or "")
        _check(bool(act_b), "no Activity was started after A was stopped", {
            "outcome": second.get("outcome"), "detail": second.get("detail"),
            "offered": cognition.last_offered})
        _check(_activity(runtime, act_a).get("status") == "CANCELLED",
               "starting B resurrected A", _activity(runtime, act_a))
        summary["another_activity_started"] = {"activity_ref": act_b,
                                               "a_still": _activity(runtime, act_a).get("status")}
        runtime.close()
        runtime = None

        # ---------------------------------------------------------- phase 7: forged mailbox
        live_cred = phone_operator_stop.read_credential(
            phone_operator_stop.credential_path(instance))
        forged = {"schema_version": aes.SCHEMA_STOP_REQUEST, "issuer": aes.MAILBOX_ISSUER,
                  "instance": str(instance), "activity_id": act_b,
                  "expected_revision": int(_activity_readonly(state_root, act_b)["revision"]),
                  "operation_key": "demo:forged:1", "reason_code": "SAFETY_HOLD",
                  "subject_id": live_cred.life_subject, "epoch": live_cred.epoch,
                  "requested_at": _iso(datetime.now(timezone.utc)),
                  "intent_id": "opstop:forged0000001", "boundary_mode": "IMMEDIATE",
                  "unresolved_action_refs": [], "mac": "0" * 64}
        b_revision = int(_activity_readonly(state_root, act_b)["revision"])
        _write_mailbox_file(state_root, forged, "demo-forged")
        stale = dict(forged)
        stale.update({"operation_key": "demo:stale-epoch:1", "intent_id": "opstop:stale0000001",
                      "epoch": "0" * 32, "mac": ""})
        stale.pop("mac")
        stale["mac"] = aes.mailbox_mac(stale, aes.mailbox_key(live_cred.token))
        _write_mailbox_file(state_root, stale, "demo-stale-epoch")
        out3 = run / "resident-forged.json"
        child = _spawn("resident", "--instance", str(instance), "--state-root", str(state_root),
                       "--out", str(out3), "--phase", "forged")
        code = child.wait(timeout=600)
        result3 = json.loads(out3.read_text(encoding="utf-8"))
        _check(code == 0, "the forged-phase resident exited non-zero", {"code": code,
                                                                       "result": result3})
        statuses = {row.get("operation_key"): row.get("reason_code")
                    for row in (result3.get("drain") or [])}
        _check(statuses.get("demo:forged:1") == aes.REFUSED_MAILBOX_FORGED,
               "a forged mailbox file was not refused", statuses)
        _check(statuses.get("demo:stale-epoch:1") == aes.REFUSED_MAILBOX_EPOCH,
               "an old-epoch request was not refused", statuses)
        b_after = _activity_readonly(state_root, act_b)
        _check(b_after.get("status") == "ACTIVE" and int(b_after["revision"]) == b_revision,
               "a refused mailbox file changed B", b_after)
        _check("external_stop" not in b_after,
               "a refused mailbox file wrote a stop intent", b_after)
        summary["mailbox_authority"] = {
            "forged_refused": statuses.get("demo:forged:1"),
            "stale_epoch_refused": statuses.get("demo:stale-epoch:1"),
            "b_untouched": {"status": b_after.get("status"),
                            "revision": b_after.get("revision")},
        }

        # ---------------------------------------------------------- phase 8: SAFE_BOUNDARY
        runtime, cycle = _build_runtime(instance, state_root, data, cognition, writer, port)
        runtime.interruption_coordinator.configure_activity_attention(
            activity_id=act_b, attention_occupancy="FOCUSED",
            interruptibility="SAFE_BOUNDARY")
        b_live = _activity(runtime, act_b)
        request_b = aes.build_request(
            activity_id=act_b, expected_revision=int(b_live["revision"]),
            operation_key="demo:stop:b:safe-boundary", reason_code="SAFETY_HOLD",
            subject_id=str(b_live.get("subject_id") or ""),
            epoch=credential.epoch)
        pending_b = runtime.activity_stop.handle(request_b)
        _check(pending_b.get("status") == aes.STATUS_PENDING,
               "SAFE_BOUNDARY did not keep the stop pending", pending_b)
        basis = (pending_b.get("boundary") or {}).get("basis")
        _check(basis == "SAFE_BOUNDARY_NOT_CONFIRMED_BY_OWNER",
               "the pending basis is not named", pending_b)
        _check(_activity(runtime, act_b).get("status") == "ACTIVE",
               "a pending SAFE_BOUNDARY stop cancelled the Activity", _activity(runtime, act_b))
        applied = (runtime.interruption_coordinator.interruption_store.load_state()
                   .get("applied_boundaries") or {})
        _check(not [row for row in applied.values()
                    if str(row.get("activity_id") or "") == act_b],
               "a safe boundary was fabricated before the owner confirmed one", applied)
        summary["safe_boundary"] = {"status": pending_b.get("status"), "basis": basis,
                                    "owner_boundary_records": 0}
        runtime.close()
        runtime = None

        # ---------------------------------------------------------- phase 10: reassembly
        runtime2, _cycle2 = _build_runtime(instance, state_root, data, cognition, writer, port)
        handoffs = {activity_id: len(_handoffs(runtime2, activity_id,
                                               kind=ag2.HANDOFF_KIND_CANCELLED))
                    for activity_id in (act_a, act_b, act_c)}
        _check(_activity(runtime2, act_a).get("status") == "CANCELLED",
               "re-assembly changed A", _activity(runtime2, act_a))
        _check(handoffs[act_a] == 1, "re-assembly duplicated A's handoff", handoffs)
        _check(handoffs[act_b] == 0, "a pending stop emitted a CANCELLED handoff", handoffs)
        _check(_activity(runtime2, act_b).get("status") == "ACTIVE",
               "B was stopped although only A was named", _activity(runtime2, act_b))
        _check(_activity(runtime2, act_c).get("status") == "CANCELLED",
               "re-assembly changed C", _activity(runtime2, act_c))
        _check(_head(port, seed) == head_v1, "re-assembly replayed A's version",
               _head(port, seed))
        summary["same_root_reassembly"] = {
            "a": _activity(runtime2, act_a).get("status"),
            "b": _activity(runtime2, act_b).get("status"),
            "b_stop_state": (_activity(runtime2, act_b).get("external_stop") or {}).get("state"),
            "c": _activity(runtime2, act_c).get("status"),
            "handoffs": handoffs}
        runtime2.close()
        runtime = None

        # ---------------------------------------------------------- phase 11: real restart
        # Stop this demo's own phone serve through its own stop file, then serve again in a
        # *new* process.  The old token is held in this process only to prove the rebuilt
        # serve refuses it; it is never printed.  A request mailed under the old serve but
        # never accepted by the owner is left in the mailbox and must not become a new
        # authorization, while the intent the owner already accepted must still finish.
        old_token = good
        old_epoch = credential.epoch
        canonical_subject = phone_service.canonical_life_subject(state_root)
        stale_request = aes.build_request(
            activity_id="actv_staleepoch0000001", expected_revision=0,
            operation_key="demo:stale-mailbox:1", reason_code="OPERATOR_REQUEST",
            subject_id=canonical_subject, epoch=old_epoch,
            requested_at=_iso(datetime.now(timezone.utc)))
        aes.StopControlChannel(state_root).submit(
            stale_request, key=aes.mailbox_key(old_token), instance=instance)

        (run / "phone.stop").write_text("stop", encoding="utf-8")
        phone_child.wait(timeout=120)
        phone_child = None
        info2_path = run / "phone2.info.json"
        phone_child = _spawn("phone", "--instance", str(instance),
                             "--state-root", str(state_root),
                             "--info-out", str(info2_path),
                             "--stop-file", str(run / "phone2.stop"))
        phone_stop_path = run / "phone2.stop"
        info2 = _wait_for(info2_path)
        _check("error" not in info2, "the restarted phone child failed", info2)
        _check(int(info2["pid"]) != int(info["pid"]), "the phone was not restarted", info2)
        new_credential = phone_operator_stop.read_credential(
            phone_operator_stop.credential_path(instance))
        _check(new_credential.epoch != old_epoch, "the restart did not mint a new epoch")
        _check(new_credential.token != old_token, "the restart did not mint a new token")
        new_client = _Http(int(info2["port"]))
        summary["processes"]["phone_restarted"] = info2["pid"]

        b_live = _activity_readonly(state_root, act_b)
        b_body = {"activity_id": act_b, "expected_revision": int(b_live["revision"]),
                  "operation_key": "demo:stop:b:safe-boundary", "reason_code": "SAFETY_HOLD"}
        old_get = new_client.get(
            "%s/control/activity/stop/state?activity_id=%s" % (API, act_b), token=old_token)
        _check(old_get[0] == 403, "the rebuilt serve accepted the old token", old_get)
        old_post = new_client.post(f"{API}/control/activity/stop", b_body, token=old_token)
        _check(old_post[0] == 403,
               "an old token was allowed to replay an accepted operation key", old_post)
        _check(_activity_readonly(state_root, act_b).get("status") == "ACTIVE",
               "an old token changed B", _activity_readonly(state_root, act_b))

        read_new = new_client.get(
            "%s/control/activity/stop/state?activity_id=%s" % (API, act_b),
            token=new_credential.token)
        _check(read_new[0] == 200, "the new credential could not read the stop state",
               read_new)
        _check((read_new[1].get("data") or {}).get("stop_state") == "PENDING",
               "B's accepted intent is not PENDING before the retry", read_new)
        retried = new_client.post(f"{API}/control/activity/stop", b_body,
                                  token=new_credential.token)
        _check(retried[0] == 200, "the new credential could not retry the stop", retried)
        _check((retried[1].get("data") or {}).get("mailboxed") is True,
               "the retry did not go through the mailbox", retried)

        out4 = run / "resident-rotation.json"
        child = _spawn("resident", "--instance", str(instance),
                       "--state-root", str(state_root),
                       "--out", str(out4), "--phase", "rotation",
                       "--observe-activity", act_b)
        code = child.wait(timeout=600)
        result4 = json.loads(out4.read_text(encoding="utf-8"))
        _check(code == 0, "the rotation-phase resident exited non-zero",
               {"code": code, "result": result4})
        summary["processes"]["resident_rotation"] = result4["pid"]
        rotation_rows = {str(row.get("operation_key")): row
                         for row in (result4.get("drain") or [])}
        _check(rotation_rows.get("demo:stale-mailbox:1", {}).get("reason_code")
               == aes.REFUSED_MAILBOX_EPOCH,
               "an old-serve mailbox request survived rotation", rotation_rows)
        b_after = (result4.get("activities") or {}).get(act_b) or {}
        _check(b_after.get("status") == "CANCELLED",
               "an accepted intent did not continue after rotation", result4)
        _check(b_after.get("stop_state") == "FINALIZED",
               "B's stop is not FINALIZED after the confirmed boundary", b_after)
        _check(b_after.get("handoffs") == 1,
               "the retry did not emit exactly one CANCELLED handoff", b_after)
        rotation_final = rotation_rows.get("demo:stop:b:safe-boundary") or {}
        _check(rotation_final.get("status") == aes.STATUS_CANCELLED,
               "the retry did not return CANCELLED", rotation_final)
        _check((rotation_final.get("boundary") or {}).get("basis")
               == "SAFE_BOUNDARY_CONFIRMED_BY_OWNER",
               "the boundary was not confirmed by the owner", rotation_final)
        rotation_handoff = dict(rotation_final.get("handoff") or {})
        _check(rotation_handoff.get("status") == rotation_handoff.get("delivery_status"),
               "the retry collapsed two different handoff outcomes", rotation_handoff)
        state_after_b = new_client.get(
            "%s/control/activity/stop/state?activity_id=%s" % (API, act_b),
            token=new_credential.token)
        b_state = state_after_b[1].get("data") or {}
        _check(state_after_b[0] == 200 and b_state.get("stop_state") == "FINALIZED"
               and b_state.get("activity_status") == "CANCELLED",
               "the read face does not show B finalized", state_after_b)
        summary["rotation"] = {
            "phone_restarted": True,
            "old_token_refused": old_get[0] == 403 and old_post[0] == 403,
            "new_credential_read": read_new[0] == 200,
            "new_credential_request": retried[0] == 200,
            "old_epoch_mailbox": rotation_rows.get("demo:stale-mailbox:1", {}).get(
                "reason_code"),
            "accepted_intent_finalized": b_after.get("status"),
            "boundary_basis": (rotation_final.get("boundary") or {}).get("basis"),
            "handoff_status": rotation_handoff.get("status"),
            "handoff_delivery_status": rotation_handoff.get("delivery_status"),
            "handoff_disposition": rotation_handoff.get("consumer_disposition"),
            "note": ("a real stop/drain and a second serve in a new process: the old "
                     "token/epoch are refused, a file mailed under the old serve is "
                     "STALE_SERVE_EPOCH, and an owner-accepted intent finishes through the "
                     "new credential"),
        }

        print(json.dumps({"ok": True, "status": "DEMO_OK", "root": str(run),
                          "summary": summary, "limits": summary["limits"]},
                         ensure_ascii=False, indent=1))
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        print(json.dumps({"ok": False, "status": "DEMO_FAIL", "root": str(run),
                          "error": "%s: %s" % (type(exc).__name__, exc),
                          "where": traceback.format_exc()[-1200:]},
                         ensure_ascii=False, indent=1))
        return 1
    finally:
        try:
            if phone_child is not None:
                if phone_stop_path is not None:
                    phone_stop_path.write_text("stop", encoding="utf-8")
                try:
                    phone_child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    phone_child.terminate()
        except Exception:
            pass
        try:
            services.stop()
        except Exception:
            pass
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


def _activity_readonly(state_root: Path, activity_id: str) -> dict:
    """Read one Activity straight from the canonical file -- no runtime, no lease."""

    path = Path(state_root) / "canonical_life_state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    act = (payload.get("activities") or {}).get(str(activity_id))
    return dict(act) if isinstance(act, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
