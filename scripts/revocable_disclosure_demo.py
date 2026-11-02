#!/usr/bin/env python3
"""Zero-model, isolated demo: one revocable disclosure permit bound to an exact source version.

It runs the real chain, not a stand-in:

* the real ``LifeSupplyService`` (GovernanceAuthority + Workspace + ManagedArtifact + writer
  fences) on its own AF_UNIX socket under a fresh isolated root;
* the real ``LifeArtifactPort`` and ``PrivateProjectService`` the Life side already uses;
* the real formal assembly (``assemble_autonomous_life``), so the positive ``send_local`` is a
  scripted Decision -> ``evaluate_once`` -> ``LifeSharingService`` -> owner commit;
* the real operator CLI ``scripts/disclosure_authority_cli.py`` for issue / query / revoke.

What it proves (and what it does not):

* without a permit a local delivery is refused by name, with zero version/state/receipt change;
* an explicitly issued permit, bound to (subject, source artifact, exact source version,
  ``local:owner``), lets exactly one local receipt land; the five external/read/ack/understood
  bits stay false;
* a permit revoked *after* prepare but *before* send refuses the send by name
  (``DISCLOSURE_REVOKED``) and leaves the draft untouched; repeated revoke is idempotent;
* subject / source version / target / capability mismatches cannot borrow another permit;
* a real stop + socket-guard + start **in a new OS process** on the same root keeps the
  revocation effective and keeps the already-recorded receipt; a newly issued permit for the
  current exact version still sends;
* ordinary source reads are unaffected by disclosure permits.

It does **not** claim real-person authentication, cross-uid administrator protection, external
channels, multi-audience sharing, G1, 24h soak or EL4.  Decision and content answers are scripted
stand-ins (no product model, no network).  Output is ids/statuses/reasons/counts/booleans only --
never a body, prompt or token.  The isolated root is kept (no delete, no cleanup by rmtree).

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/revocable_disclosure_demo.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

#: Synthetic bodies.  Only their versions/receipts are asserted; they are never printed.
SOURCE_A = "合成来源A：只用于披露许可演示，不会打印。"
SOURCE_B = "合成来源B：另一件无关作品。"
SOURCE_C = "合成来源C：撤权演示专用。"
SHARE_TEXT = "这一段来自我刚写完的那一版，先留在这里。"
SHARE_TEXT_2 = "撤权前准备的另一段，不应发出。SENTINEL_DISCLOSURE_DEMO_DO_NOT_RETURN"
SHARE_TEXT_3 = "换版之后的新意图。"
SENTINEL = "SENTINEL_DISCLOSURE_DEMO_DO_NOT_RETURN"

_BASE = datetime.now().astimezone()
_ROUND = [0]
CHECKS: list[tuple[str, bool, Any]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-64s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:600])


def bare(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def ref(value: Any) -> str:
    text = bare(value)
    return "artifact:%s" % text if text else ""


def next_instant() -> str:
    _ROUND[0] += 1
    return (_BASE + timedelta(minutes=400 * _ROUND[0])).isoformat()


def head(port, artifact_id: str) -> str:
    point = port.read_head(artifact_id) or {}
    return str(point.get("version_id") or "")


def version_count(port, artifact_id: str) -> int:
    return int(port.list_versions(artifact_id)["count"])


def share_state(service, share_id: str) -> dict[str, Any]:
    answer = service.read_share(share_id, include_text=False)
    data = answer.get("data") or {}
    share = data.get("share") or {}
    return {"ok": bool(answer.get("ok")), "state": share.get("state"),
            "head": share.get("head_version_id"),
            "receipt": data.get("receipt"), "delivered": data.get("delivered"),
            "text_withheld": share.get("text_withheld"),
            "source": share.get("source")}


def owner_call(socket_path: str, request: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    """One direct request to the real owner socket (used only for negative controls)."""
    import socket as _socket

    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    return json.loads(line.decode("utf-8")) if line else {"status": "EMPTY_RESPONSE"}


def cli(socket_path: str, *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the real operator CLI as a separate process; parse its JSON status line."""
    command = [sys.executable, "-B", str(SCRIPT_DIR / "disclosure_authority_cli.py"),
               "--socket", str(socket_path), *args]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    line = (completed.stdout or "").strip().splitlines()
    payload: dict[str, Any] = {}
    if line:
        try:
            payload = json.loads(line[-1])
        except ValueError:
            payload = {"status": "UNPARSEABLE"}
    return completed.returncode, payload


class Planner:
    """The Decision model, scripted: it only echoes ids/CAS heads the real request offered."""

    model_id = "stub:revocable-disclosure"
    model_version = "revocable-disclosure-v1"

    def __init__(self) -> None:
        self.plan: list[dict] = []
        self.last_head: dict[str, str] = {}
        self.captured: list[dict] = []
        self.call_count = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        block = situation.get("sharing") if isinstance(situation, dict) else {}
        block = block if isinstance(block, dict) else {}
        self.captured.append(block)
        for row in block.get("shares") or []:
            if isinstance(row, dict) and row.get("share_id"):
                self.last_head[str(row["share_id"])] = str(row.get("head_version_id") or "")
        step = self.plan.pop(0) if self.plan else {"op": "none"}
        raw: dict[str, Any] = {"decision": "NO_ACTION", "selected_candidate_id": None,
                               "reason_codes": allowed[:1] or ["NO_ACTION"],
                               "short_rationale": "stub: quiet round",
                               "deferred_candidate_ids": []}
        op = str(step.get("op") or "none")
        if op == "prepare":
            sources = [row for row in (block.get("sources") or [])
                       if isinstance(row, dict) and row.get("kind") == "current_work"]
            if not sources:
                raise AssertionError("no current_work source in this round's request")
            raw["sharing"] = {"op": "prepare",
                              "source_artifact_id": sources[0]["artifact_id"],
                              "share_text": str(step["text"]),
                              "title": str(step.get("title") or "")}
        elif op == "send_local":
            target = str(step["target"])
            raw["sharing"] = {"op": "send_local", "share_id": target,
                              "expected_head": str(self.last_head.get(target) or "")}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:revocable-disclosure:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def round_once(chain, planner: Planner, step: dict) -> dict[str, Any]:
    planner.plan = [step]
    return chain.cycle.evaluate_once(trigger_ref="evt:revocable-disclosure:%d" % _ROUND[0],
                                     act=True, observed_at=next_instant())


def serve_child(root: str) -> int:
    """A separate OS process that owns the same root; used for the new-PID restart."""
    import signal
    import threading
    from tests.test_one_day_life import Services

    services = Services(Path(root))
    stop = threading.Event()

    def _handle(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    print("READY", flush=True)
    try:
        while not stop.wait(0.5):
            pass
    finally:
        services.server.drain(10.0)
        services.stop()
    return 0


def restart_owner(chain):
    """Regular stop -> formal socket guard -> a real new PID on the same root."""
    import signal
    from lifesupply.service.life_supply import prepare_socket_path

    services = chain.services
    socket_path = Path(services.socket_path)
    services.server.drain(10.0)
    services.stop()
    handled = prepare_socket_path(socket_path)
    child = subprocess.Popen(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--serve", str(socket_path.parent)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    line = child.stdout.readline().strip() if child.stdout else ""
    if line != "READY":
        child.terminate()
        raise RuntimeError("replacement owner did not become READY")
    deadline = time.monotonic() + 15.0
    while True:
        try:
            if owner_call(str(socket_path), {"op": "health"}).get("status") == "OK":
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=30)
            raise RuntimeError("replacement owner socket never answered")
        time.sleep(0.05)
    return child, handled


def stop_child(child) -> str:
    """SIGTERM, wait for the child's own drain/stop, then the formal socket guard."""
    from lifesupply.service.life_supply import prepare_socket_path

    if child is None:
        return "NOT_STARTED"
    child.terminate()
    try:
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    return prepare_socket_path(Path(child.args[-1]) / "life-supply.sock")


def main() -> int:
    if os.name != "posix":
        print("result=SKIP_NOT_POSIX")
        return 2
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        return serve_child(sys.argv[2])

    import result_method_behavior_smoke as harness

    chain = None
    replacement = None
    facts: dict[str, Any] = {}
    try:
        chain = harness.build_chain("revocable-disclosure")
        services = chain.services
        socket_path = str(services.socket_path)
        subject = str(chain.data["subject_id"])
        workspace_id = str(chain.data["workspace_id"])
        write_grant = str(chain.data["artifact_grant_id"])
        port = chain.port
        service = chain.cycle.sharing_service
        facts["root"] = str(chain.root)
        facts["socket"] = socket_path
        print("FIXTURE_ROOT %s" % chain.root)
        check("real assembly is wired to the real sharing owner",
              type(service).__name__ == "LifeSharingService", type(service).__name__)

        # -- sources: three real, subject-owned artifacts -----------------------------------
        created_a = port.create_artifact(workspace_id=workspace_id, title="披露来源A",
                                         content=SOURCE_A, grant_id=write_grant,
                                         operation_key="demo:create:a")
        created_b = port.create_artifact(workspace_id=workspace_id, title="无关作品B",
                                         content=SOURCE_B, grant_id=write_grant,
                                         operation_key="demo:create:b")
        created_c = port.create_artifact(workspace_id=workspace_id, title="披露来源C",
                                         content=SOURCE_C, grant_id=write_grant,
                                         operation_key="demo:create:c")
        created_d = port.create_artifact(workspace_id=workspace_id, title="披露来源D",
                                         content="合成来源D：跨重启撤权演示。",
                                         grant_id=write_grant, operation_key="demo:create:d")
        a = str(created_a.artifact_id or "")
        b = str(created_b.artifact_id or "")
        c = str(created_c.artifact_id or "")
        d = str(created_d.artifact_id or "")
        a_v1, c_v1, d_v1 = head(port, a), head(port, c), head(port, d)
        facts["sources"] = {"a": a, "b": b, "c": c, "d": d}
        check("four real source versions exist", bool(a and b and c and d and a_v1 and c_v1 and d_v1),
              {"a": bool(a), "b": bool(b), "c": bool(c), "d": bool(d)})

        # -- formal round 1: prepare a draft (no permit needed to draft) --------------------
        chain.cycle.work_artifact_id = ref(a)
        planner = Planner()
        chain.cognition.invoke = planner.invoke
        out_prepare = round_once(chain, planner, {"op": "prepare", "text": SHARE_TEXT,
                                                  "title": "先留一版"})
        prepared = out_prepare.get("sharing") or {}
        draft = str(prepared.get("share_id") or "")
        facts["draft"] = draft
        check("formal Decision prepares one real DRAFT bound to the exact source version",
              prepared.get("ok") is True and str(prepared.get("state") or "") == "DRAFT"
              and bool(draft), {k: prepared.get(k) for k in ("ok", "state", "reason_code")})
        before = share_state(service, draft) if draft else {}
        check("the draft is bound to source A at its exact head and is not delivered",
              str((before.get("source") or {}).get("version_id") or "") == a_v1
              and before.get("state") == "DRAFT" and not before.get("delivered"),
              {"bound": (before.get("source") or {}).get("version_id"), "head": a_v1})
        draft_versions = version_count(port, draft) if draft else 0
        draft_head = str(before.get("head") or "")

        # -- no permit: send is refused by name, zero write --------------------------------
        no_permit = service.apply_action(share_id=draft, action="send_local",
                                         expected_head=draft_head,
                                         operation_key="demo:send:no-permit")
        after = share_state(service, draft)
        check("without a disclosure permit the send is refused by name",
              no_permit.get("ok") is False
              and str(no_permit.get("reason_code") or "") == "DISCLOSURE_GRANT_REQUIRED",
              {"ok": no_permit.get("ok"), "reason": no_permit.get("reason_code"),
               "status": no_permit.get("status")})
        check("the refused send changed no version, state or receipt",
              version_count(port, draft) == draft_versions
              and after.get("state") == "DRAFT" and not after.get("delivered")
              and not after.get("receipt"),
              {"versions": version_count(port, draft), "was": draft_versions})

        # -- explicit operator CLI: issue the exact permit ---------------------------------
        code, issued = cli(socket_path, "issue", "--subject", subject,
                           "--source", a, "--version", a_v1,
                           "--operation-id", "demo:issue:a-v1")
        permit_a = str(issued.get("grant_id") or "")
        facts["permit_a"] = permit_a
        check("the operator CLI issues one exact permit", code == 0
              and str(issued.get("status") or "") == "ALLOW" and bool(permit_a), issued)
        code, queried = cli(socket_path, "query", "--subject", subject)
        matching = [row for row in (queried.get("grants") or [])
                    if row.get("grant_id") == permit_a]
        check("the CLI query sees exactly the issued permit (refs only)",
              code == 0 and len(matching) == 1
              and a_v1 in str(matching[0].get("scope") or "")
              and matching[0].get("target") == "local:owner", queried.get("count"))

        # -- ``--instance`` binds the instance's own socket and subject --------------------
        def cli_instance(*args: str) -> tuple[int, dict[str, Any]]:
            command = [sys.executable, "-B", str(SCRIPT_DIR / "disclosure_authority_cli.py"),
                       "--instance", str(chain.root / "instance"), *args]
            done = subprocess.run(command, capture_output=True, text=True, timeout=60)
            lines = (done.stdout or "").strip().splitlines()
            try:
                return done.returncode, (json.loads(lines[-1]) if lines else {})
            except ValueError:
                return done.returncode, {"status": "UNPARSEABLE"}

        code_i, queried_i = cli_instance("query")
        check("the CLI binds an instance's own socket and subject with --instance",
              code_i == 0 and str(queried_i.get("status") or "") == "OK"
              and any(row.get("grant_id") == permit_a
                      for row in (queried_i.get("grants") or [])),
              {"status": queried_i.get("status"), "count": queried_i.get("count")})
        code_w, wrong_subject = cli_instance("issue", "--subject", "test:someone-else",
                                             "--source", a, "--version", a_v1,
                                             "--operation-id", "demo:issue:wrong-subject")
        check("an explicit subject that disagrees with the instance is refused",
              code_w == 1
              and str(wrong_subject.get("reason") or "") == "SUBJECT_INSTANCE_MISMATCH",
              wrong_subject.get("reason"))

        # -- formal round 2: the scripted Decision sends; the owner consumes the permit ------
        out_send = round_once(chain, planner, {"op": "send_local", "target": draft})
        sent = out_send.get("sharing") or {}
        delivered = share_state(service, draft)
        receipt = delivered.get("receipt") or {}
        facts["delivery_id"] = receipt.get("delivery_id")
        check("formal Decision -> Sharing send lands exactly one local receipt",
              sent.get("ok") is True and delivered.get("state") == "LOCAL_DELIVERED"
              and delivered.get("delivered") is True and bool(receipt),
              {k: sent.get(k) for k in ("ok", "status", "reason_code")})
        check("the receipt stays local: external/read/ack/understood are all false",
              receipt.get("local_only") is True
              and receipt.get("external_channel_sent") is False
              and receipt.get("external_delivery_confirmed") is False
              and receipt.get("read_by_audience") is False
              and receipt.get("acknowledged") is False
              and receipt.get("understood") is False, sorted(receipt))
        check("exactly one new draft version was written for the delivery",
              version_count(port, draft) == draft_versions + 1,
              {"now": version_count(port, draft), "was": draft_versions})
        replay = service.apply_action(share_id=draft, action="send_local",
                                      expected_head=str(delivered.get("head") or ""),
                                      operation_key="demo:send:replay")
        again = share_state(service, draft)
        check("replaying the same intent never makes a second delivery",
              replay.get("ok") is False
              and str(replay.get("reason_code") or "") == "SHARE_ALREADY_DELIVERED"
              and (again.get("receipt") or {}).get("delivery_id")
              == receipt.get("delivery_id")
              and version_count(port, draft) == draft_versions + 1,
              {"reason": replay.get("reason_code"), "versions": version_count(port, draft)})

        # -- revoked after prepare, before send ---------------------------------------------
        prepared_c = service.prepare_share(source_artifact_id=ref(c), share_text=SHARE_TEXT_2,
                                           title="撤权演示", operation_key="demo:prepare:c")
        draft_c = str(prepared_c.get("share_id") or "")
        draft_c_head = str((share_state(service, draft_c).get("head") if draft_c else "") or "")
        draft_c_versions = version_count(port, draft_c) if draft_c else 0
        code, issued_c = cli(socket_path, "issue", "--subject", subject,
                             "--source", c, "--version", c_v1,
                             "--operation-id", "demo:issue:c-v1")
        permit_c = str(issued_c.get("grant_id") or "")
        check("a second exact permit is issued for the revocation scenario",
              code == 0 and bool(permit_c), issued_c)
        code, revoked = cli(socket_path, "revoke", "--grant-id", permit_c,
                            "--operation-id", "demo:revoke:c-v1")
        check("the operator CLI revokes the permit", code == 0
              and str(revoked.get("status") or "") == "REVOKED", revoked)
        sent_c = service.apply_action(share_id=draft_c, action="send_local",
                                      expected_head=draft_c_head,
                                      operation_key="demo:send:c-revoked")
        state_c = share_state(service, draft_c)
        check("a send after revoke is refused by the exact name DISCLOSURE_REVOKED",
              sent_c.get("ok") is False
              and str(sent_c.get("reason_code") or "") == "DISCLOSURE_REVOKED",
              {"reason": sent_c.get("reason_code"), "status": sent_c.get("status")})
        check("the revoked send wrote nothing",
              state_c.get("state") == "DRAFT" and not state_c.get("receipt")
              and not state_c.get("delivered")
              and version_count(port, draft_c) == draft_c_versions,
              {"state": state_c.get("state"), "versions": version_count(port, draft_c)})
        code, revoked_again = cli(socket_path, "revoke", "--grant-id", permit_c,
                                  "--operation-id", "demo:revoke:c-v1-again")
        check("repeated revoke is idempotent",
              code == 0 and str(revoked_again.get("status") or "") == "NO_CHANGE",
              revoked_again)
        read_c = service.projects.read_project(ref(c))
        check("ordinary source reads are unaffected by the revoked permit",
              bool(read_c.get("ok"))
              and (read_c.get("project") or {}).get("content") == SOURCE_C,
              {"ok": read_c.get("ok")})

        # -- strict mismatches cannot borrow another permit ---------------------------------
        # a write permit is not a disclosure permit
        wrong_cap = service.apply_action(share_id=draft_c, action="send_local",
                                         expected_head=draft_c_head,
                                         operation_key="demo:send:write-grant",
                                         disclosure_grant_id=write_grant)
        check("a write grant cannot pass as a disclosure permit",
              wrong_cap.get("ok") is False
              and str(wrong_cap.get("reason_code") or "")
              == "DISCLOSURE_CAPABILITY_MISMATCH", wrong_cap.get("reason_code"))
        # an unknown grant id
        unknown = service.apply_action(share_id=draft_c, action="send_local",
                                       expected_head=draft_c_head,
                                       operation_key="demo:send:unknown-grant",
                                       disclosure_grant_id="grant:does-not-exist")
        check("an unknown grant id is refused by name",
              unknown.get("ok") is False
              and str(unknown.get("reason_code") or "") == "DISCLOSURE_GRANT_UNKNOWN",
              unknown.get("reason_code"))
        # a wrong target cannot even be issued
        wrong_target = owner_call(socket_path, {
            "op": "issue_grant",
            "grant": {"subject": subject, "capability": "DISCLOSE_MANAGED_ARTIFACT",
                      "scope": "disclosure:{\"artifact_id\":\"%s\",\"version_id\":\"%s\"}"
                               % (c, c_v1),
                      "target": "local:someone", "operation_id": "demo:issue:wrong-target"}})
        check("a non-local disclosure target is refused at issue time",
              str(wrong_target.get("status") or "") == "DENY"
              and str(wrong_target.get("reason") or "") == "DISCLOSURE_TARGET_REFUSED",
              wrong_target.get("reason"))
        # exact source version: an old-version permit cannot cover a newer version
        code, issued_c_again = cli(socket_path, "issue", "--subject", subject,
                                   "--source", c, "--version", c_v1,
                                   "--operation-id", "demo:issue:c-v1-again")
        old_permit = str(issued_c_again.get("grant_id") or "")
        moved = port.commit_version(artifact_id=c, expected_head=c_v1,
                                    content=SOURCE_C + "\n第二版。", grant_id=write_grant,
                                    operation_key="demo:commit:c-v2")
        c_v2 = head(port, c)
        prepared_c2 = service.prepare_share(source_artifact_id=ref(c),
                                            share_text=SHARE_TEXT_3, title="换版新意图",
                                            operation_key="demo:prepare:c-v2")
        draft_c2 = str(prepared_c2.get("share_id") or "")
        draft_c2_head = str((share_state(service, draft_c2).get("head")
                             if draft_c2 else "") or "")
        check("the source moved to a new version and a fresh draft binds it",
              moved.applied and bool(c_v2) and c_v2 != c_v1 and bool(draft_c2)
              and str((share_state(service, draft_c2).get("source") or {}).get("version_id")
                      or "") == c_v2, {"old": c_v1, "new": c_v2})
        lookup_miss = service.apply_action(share_id=draft_c2, action="send_local",
                                           expected_head=draft_c2_head,
                                           operation_key="demo:send:new-version")
        check("an old-version permit is not auto-bound to the new version",
              lookup_miss.get("ok") is False
              and str(lookup_miss.get("reason_code") or "") == "DISCLOSURE_GRANT_REQUIRED",
              lookup_miss.get("reason_code"))
        explicit_old = service.apply_action(share_id=draft_c2, action="send_local",
                                            expected_head=draft_c2_head,
                                            operation_key="demo:send:explicit-old",
                                            disclosure_grant_id=old_permit)
        check("naming the old permit explicitly is a named scope mismatch",
              explicit_old.get("ok") is False
              and str(explicit_old.get("reason_code") or "")
              == "DISCLOSURE_SCOPE_MISMATCH", explicit_old.get("reason_code"))
        check("both version-mismatch sends left the new draft untouched",
              share_state(service, draft_c2).get("state") == "DRAFT"
              and version_count(port, draft_c2) == 1,
              {"versions": version_count(port, draft_c2)})

        # -- the canonical draft is the authority, not the client-authored header ----------
        from life_sharing_service import encode_share_content, parse_share_content

        created_e = port.create_artifact(workspace_id=workspace_id, title="披露来源E",
                                         content="合成来源E：草稿绑定演示。",
                                         grant_id=write_grant, operation_key="demo:create:e")
        e = str(created_e.artifact_id or "")
        e_v1 = head(port, e)
        prepared_e = service.prepare_share(source_artifact_id=ref(e),
                                           share_text="草稿绑定演示的正文。",
                                           title="草稿绑定", operation_key="demo:prepare:e")
        draft_e = str(prepared_e.get("share_id") or "")
        draft_e_head = str((share_state(service, draft_e).get("head") if draft_e else "") or "")
        draft_e_versions = version_count(port, draft_e) if draft_e else 0
        code, issued_e = cli(socket_path, "issue", "--subject", subject,
                             "--source", e, "--version", e_v1,
                             "--operation-id", "demo:issue:e-v1")
        permit_e = str(issued_e.get("grant_id") or "")
        check("a fifth source, its draft and its exact permit exist",
              bool(e and e_v1 and draft_e and permit_e),
              {"source": bool(e), "draft": bool(draft_e), "permit": bool(permit_e)})
        parsed_e = parse_share_content((port.read_head(draft_e) or {}).get("content") or "") or {}
        relabelled = dict(parsed_e)
        relabelled.update({"state": "LOCAL_DELIVERED", "source_artifact_id": ref(b),
                           "source_version_id": head(port, b)})
        relabelled_text = encode_share_content(relabelled, str(parsed_e.get("text") or ""))
        relabel_answer = owner_call(socket_path, {
            "op": "commit_markdown", "grant_id": write_grant,
            "operation_key": "demo:forged:relabel-e", "disclosure_grant_id": permit_e,
            "artifact": {"subject_id": subject, "artifact_id": draft_e,
                         "expected_head": draft_e_head, "content": relabelled_text}})
        check("a delivery that relabels its canonical draft is refused by name",
              str(relabel_answer.get("status") or "") == "DENY"
              and str(relabel_answer.get("reason") or "") == "DISCLOSURE_DRAFT_BINDING_MISMATCH",
              relabel_answer.get("reason"))
        check("the relabel refusal changed no version and left the draft undelivered",
              share_state(service, draft_e).get("state") == "DRAFT"
              and version_count(port, draft_e) == draft_e_versions,
              {"versions": version_count(port, draft_e), "was": draft_e_versions})

        duplicated = relabelled_text.replace("state=LOCAL_DELIVERED",
                                             "state=LOCAL_DELIVERED\nstate=LOCAL_DELIVERED", 1)
        malformed_answer = owner_call(socket_path, {
            "op": "commit_markdown", "grant_id": write_grant,
            "operation_key": "demo:forged:duplicate-e", "disclosure_grant_id": permit_e,
            "artifact": {"subject_id": subject, "artifact_id": draft_e,
                         "expected_head": draft_e_head, "content": duplicated}})
        check("a duplicate-header envelope is refused, not treated as ordinary text",
              str(malformed_answer.get("reason") or "") == "DISCLOSURE_ENVELOPE_MALFORMED"
              and version_count(port, draft_e) == draft_e_versions,
              malformed_answer.get("reason"))

        artifacts_before = int(services.service.artifact_store.artifact_count() or 0)
        create_answer = owner_call(socket_path, {
            "op": "create_artifact", "grant_id": write_grant,
            "operation_key": "demo:create:delivery",
            "artifact": {"subject_id": subject, "workspace_id": workspace_id,
                         "title": "不该被创建的投递", "content": relabelled_text}})
        artifacts_after = int(services.service.artifact_store.artifact_count() or 0)
        check("create_artifact cannot be used to land a delivery envelope",
              str(create_answer.get("reason") or "") == "DISCLOSURE_DRAFT_BINDING_MISSING"
              and artifacts_after == artifacts_before,
              {"reason": create_answer.get("reason"), "count": artifacts_after})

        bogus = dict(relabelled)
        bogus.update({"state": "DRAFT", "source_artifact_id": ref(e),
                      "source_version_id": head(port, b)})
        bogus_text = encode_share_content(bogus, "伪造来源组合的正文。")
        created_bogus = port.create_artifact(workspace_id=workspace_id,
                                             title="[share] 伪造来源组合", content=bogus_text,
                                             grant_id=write_grant,
                                             operation_key="demo:create:bogus-version")
        bogus_id = str(created_bogus.artifact_id or "")
        bogus_head = head(port, bogus_id)
        code, issued_bogus = cli(socket_path, "issue", "--subject", subject,
                                 "--source", e, "--version", head(port, b),
                                 "--operation-id", "demo:issue:bogus-version")
        bogus_permit = str(issued_bogus.get("grant_id") or "")
        bogus_delivered = dict(bogus)
        bogus_delivered["state"] = "LOCAL_DELIVERED"
        bogus_answer = owner_call(socket_path, {
            "op": "commit_markdown", "grant_id": write_grant,
            "operation_key": "demo:send:bogus-version", "disclosure_grant_id": bogus_permit,
            "artifact": {"subject_id": subject, "artifact_id": bogus_id,
                         "expected_head": bogus_head,
                         "content": encode_share_content(bogus_delivered, "伪造来源组合的正文。")}})
        check("a version that does not belong to the named source is refused by name",
              code == 0 and bool(bogus_permit)
              and str(bogus_answer.get("reason") or "") == "DISCLOSURE_SOURCE_VERSION_MISMATCH"
              and share_state(service, bogus_id).get("state") == "DRAFT"
              and version_count(port, bogus_id) == 1,
              {"reason": bogus_answer.get("reason")})

        # The genuine draft still sends once: the refusals consumed no permit and no version.
        sent_e = service.apply_action(share_id=draft_e, action="send_local",
                                      expected_head=draft_e_head,
                                      operation_key="demo:send:e")
        sent_e_state = share_state(service, draft_e)
        check("the genuine draft still sends once with the same permit after the refusals",
              sent_e.get("ok") is True and sent_e_state.get("state") == "LOCAL_DELIVERED"
              and bool(sent_e_state.get("receipt"))
              and sent_e_state.get("state") != "DRAFT", sent_e.get("reason_code"))

        # -- an external projection edit is not another writer of a delivery envelope ------
        created_f = port.create_artifact(workspace_id=workspace_id, title="投影导入演示",
                                         content="普通正文：不允许导入投递信封。",
                                         grant_id=write_grant, operation_key="demo:create:f")
        f = str(created_f.artifact_id or "")
        f_head = head(port, f)
        binding_f = services.service.artifact_store.read_projection_binding(f) or {}
        rel_f = binding_f.get("relative_path")
        check("the import negative has an owner projection binding", bool(rel_f), binding_f)
        if rel_f:
            projection_path = Path(services.service.artifact_store.projection_root) / str(rel_f)
            projection_path.write_text(relabelled_text, encoding="utf-8")
            seen = owner_call(socket_path, {"op": "observe_projection", "subject": subject,
                                            "artifact_id": f})
            candidate = seen.get("candidate") or {}
            imported = owner_call(socket_path, {
                "op": "import_projection", "grant_id": write_grant,
                "operation_key": "demo:import:delivery",
                "import": {"subject_id": subject, "artifact_id": f,
                           "candidate_id": str(candidate.get("candidate_id") or ""),
                           "base_version_id": f_head, "confirm": True}})
            check("an external projection edit cannot import a delivery envelope",
                  str(imported.get("reason") or "") == "DISCLOSURE_IMPORT_REFUSED"
                  and version_count(port, f) == 1,
                  {"reason": imported.get("reason"),
                   "versions": version_count(port, f)})
            # Put the representation back so the restart/recovery path is not disturbed.
            projection_path.write_text("普通正文：不允许导入投递信封。", encoding="utf-8")

        # -- a draft whose source does not move, so the restart test reaches the owner --------
        prepared_d = service.prepare_share(source_artifact_id=ref(d),
                                           share_text="跨重启撤权演示的草稿。",
                                           title="跨重启", operation_key="demo:prepare:d")
        draft_d = str(prepared_d.get("share_id") or "")
        draft_d_head = str((share_state(service, draft_d).get("head")
                            if draft_d else "") or "")
        draft_d_versions = version_count(port, draft_d) if draft_d else 0

        # -- exact binding: another subject, another source with the same version string ----
        subject_other = "test:result-method-other"
        code, other_subject = cli(socket_path, "issue", "--subject", subject_other,
                                  "--source", d, "--version", d_v1,
                                  "--operation-id", "demo:issue:other-subject")
        other_permit = str(other_subject.get("grant_id") or "")
        cross_subject = service.apply_action(share_id=draft_d, action="send_local",
                                             expected_head=draft_d_head,
                                             operation_key="demo:send:other-subject",
                                             disclosure_grant_id=other_permit)
        zero_d = (share_state(service, draft_d).get("state") == "DRAFT"
                  and version_count(port, draft_d) == draft_d_versions)
        check("a permit issued to another subject cannot deliver this subject's draft",
              code == 0 and bool(other_permit)
              and str(cross_subject.get("reason_code") or "") == "DISCLOSURE_SUBJECT_MISMATCH"
              and zero_d, {"reason": cross_subject.get("reason_code"), "zero_write": zero_d})
        code, other_source = cli(socket_path, "issue", "--subject", subject,
                                 "--source", b, "--version", d_v1,
                                 "--operation-id", "demo:issue:other-source")
        other_source_permit = str(other_source.get("grant_id") or "")
        cross_source = service.apply_action(share_id=draft_d, action="send_local",
                                            expected_head=draft_d_head,
                                            operation_key="demo:send:other-source",
                                            disclosure_grant_id=other_source_permit)
        zero_d = (share_state(service, draft_d).get("state") == "DRAFT"
                  and version_count(port, draft_d) == draft_d_versions)
        check("a same-version permit for another source cannot cover this draft",
              code == 0 and bool(other_source_permit)
              and str(cross_source.get("reason_code") or "") == "DISCLOSURE_SCOPE_MISMATCH"
              and zero_d, {"reason": cross_source.get("reason_code"), "zero_write": zero_d})

        code, issued_d = cli(socket_path, "issue", "--subject", subject,
                             "--source", d, "--version", d_v1,
                             "--operation-id", "demo:issue:d-v1")
        permit_d = str(issued_d.get("grant_id") or "")
        code_revoke_d, revoked_d = cli(socket_path, "revoke", "--grant-id", permit_d,
                                       "--operation-id", "demo:revoke:d-v1")
        check("the restart scenario's permit is issued and then revoked",
              bool(permit_d) and code_revoke_d == 0
              and str(revoked_d.get("status") or "") == "REVOKED",
              {"issued": bool(permit_d), "revoke": revoked_d.get("status")})

        # -- same root: regular stop, formal socket guard, start ----------------------------
        before_b = head(port, b)
        replacement, handled = restart_owner(chain)
        facts["restart_socket_handling"] = handled
        facts["restart_pid"] = replacement.pid
        check("the owner really restarted in a new PID on the same root",
              handled in {"SOCKET_PATH_FREE", "STALE_SOCKET_REMOVED"}
              and replacement.pid != os.getpid(),
              {"handling": handled, "pid": replacement.pid})

        after_restart = share_state(service, draft_d)
        revived = service.apply_action(share_id=draft_d, action="send_local",
                                       expected_head=str(after_restart.get("head") or ""),
                                       operation_key="demo:send:after-restart")
        check("the revoked permit stays revoked across a real restart",
              revived.get("ok") is False
              and str(revived.get("reason_code") or "") == "DISCLOSURE_REVOKED",
              revived.get("reason_code"))
        check("the old draft is still undelivered after restart",
              share_state(service, draft_d).get("state") == "DRAFT"
              and not share_state(service, draft_d).get("delivered"))
        kept = share_state(service, draft)
        check("the already-recorded local receipt survives the restart",
              kept.get("state") == "LOCAL_DELIVERED"
              and (kept.get("receipt") or {}).get("delivery_id")
              == facts.get("delivery_id"),
              {"state": kept.get("state")})
        check("an unrelated work and its head are untouched",
              head(port, b) == before_b)

        code, issued_v2 = cli(socket_path, "issue", "--subject", subject,
                              "--source", c, "--version", c_v2,
                              "--operation-id", "demo:issue:c-v2")
        new_permit = str(issued_v2.get("grant_id") or "")
        check("a fresh exact permit for the current version is issued after restart",
              code == 0 and bool(new_permit), issued_v2)
        sent_new = service.apply_action(share_id=draft_c2, action="send_local",
                                        expected_head=str(share_state(service,
                                                                      draft_c2).get("head") or ""),
                                        operation_key="demo:send:c-v2")
        new_state = share_state(service, draft_c2)
        check("the new exact permit sends the new draft once",
              sent_new.get("ok") is True and new_state.get("state") == "LOCAL_DELIVERED",
              {k: sent_new.get(k) for k in ("ok", "status", "reason_code")})
        check("the new delivery is still local-only",
              (new_state.get("receipt") or {}).get("local_only") is True
              and (new_state.get("receipt") or {}).get("external_channel_sent") is False)
        check("the old revoked draft was not revived by the new permit",
              share_state(service, draft_c).get("state") == "DRAFT"
              and str((share_state(service, draft_c).get("source") or {}).get("version_id")
                      or "") == c_v1)

        # The synthetic sentinel lives in a draft body; no answer or printed check may carry it.
        printed = json.dumps({"facts": facts,
                              "checks": [{"name": n, "detail": d} for n, _ok, d in CHECKS]},
                             ensure_ascii=False, default=str)
        check("no private sentinel leaked into any printed answer", SENTINEL not in printed)

    finally:
        if chain is not None:
            try:
                chain.services.server.drain(10.0)
            except Exception:
                pass
            try:
                chain.services.stop()
            except Exception:
                pass
        try:
            facts["final_socket_handling"] = stop_child(replacement)
        except Exception:
            pass

    passed = sum(1 for _name, ok, _detail in CHECKS if ok)
    print("RESULT %d/%d PASS" % (passed, len(CHECKS)))
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
