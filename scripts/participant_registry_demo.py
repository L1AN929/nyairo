#!/usr/bin/env python3
"""F-140 end-to-end demo: who a local arrival belongs to, through the real owners.

What this demo does (all local, zero product-model calls, no outbound message):

1. makes **persistent** isolated instance roots under ``/tmp`` (never deleted), each with a real
   ``instance.json`` and the standard Life state root ``<root>/one-day/life/state``,
2. delivers two real arrivals into A's ``LocalContactInbox``,
3. registers participants through the real operator CLI using ``--instance`` -- the CLI derives
   the state root, subject and home from the instance file, the caller does not supply them,
4. proves the entry refuses a wrong caller subject / wrong state root, an instance whose real
   inbox belongs to another subject, a symlinked state root and a shared-writable root, and that
   every refusal leaves no ledger behind,
5. including two people with the **same display name** (never merged),
6. binds two explicit endpoints to one person and proves a conflicting endpoint is refused,
7. links one real arrival explicitly (``UNRESOLVED`` -> ``LINKED``), and proves an unknown /
   foreign event is refused while the inbox bytes stay untouched,
8. serves the real phone HTTP surface, reads through ``chiyo_phone`` with a real profile,
   and proves there is no participant write route / tool action,
9. restarts the phone on the same root and reads the same ids, aliases, endpoints and links.

Every answer is a real owner answer.  Nothing here is an external send, a relationship score or
a permission: a participant is identity only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402
import participant_registry as pr  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402

CLI = ROOT / "scripts" / "participant_registry_cli.py"
SUBJECT = "chiyo-f140-demo"
HOME = "/tmp/f140-demo-home"
STATE_DIRS = ("one-day", "life", "state")
CHECKS: list[dict[str, Any]] = []


def check(name: str, condition: bool, **facts: Any) -> None:
    CHECKS.append({"check": name, "ok": bool(condition), **facts})
    if not condition:
        raise AssertionError("check failed: %s %s" % (name, facts))


def state_root_for(root: Path) -> Path:
    return root.joinpath(*STATE_DIRS)


def run_cli(*args: str) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(CLI), *args]
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        answer = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise AssertionError("CLI did not answer JSON: %s" % completed.stderr[-400:])
    return int(completed.returncode), answer


def cli_instance(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    return run_cli("--instance", str(root), *args)


def make_root(label: str, *, create_state: bool = True) -> Path:
    root = Path("/tmp/nyairo-f140-%s-%d-%d" % (label, os.getpid(), int(time.time())))
    root.mkdir(parents=True, exist_ok=False)
    if create_state:
        state = state_root_for(root)
        state.mkdir(parents=True, exist_ok=True)
        os.chmod(state, 0o700)
    phone_service.write_instance(root, instance_for(root))
    return root


def submit(root: Path, text: str, key: str, *, subject: str = SUBJECT) -> dict[str, Any]:
    inbox = lc.LocalContactInbox(state_root_for(root), subject_id=subject, home_ref=HOME)
    answer = inbox.submit({"event_kind": lc.KIND_MESSAGE, "text": text,
                           "operation_key": key})
    if not answer.get("ok"):
        raise AssertionError("inbox submit failed: %s" % answer.get("reason_code"))
    return answer


def write_profile(root: Path, port: int) -> Path:
    profile = root / "profile"
    (profile / "chiyo").mkdir(parents=True, exist_ok=True)
    payload = {
        "cli_owner": True,
        "local_owner_uid": int(os.geteuid()) if hasattr(os, "geteuid") else int(os.getuid()),
        "phone": {
            "enabled": True,
            "write_enabled": False,
            "private_artifacts": True,
            "base_url": "http://127.0.0.1:%d" % int(port),
            "profile_home": str(profile),
        },
    }
    (profile / "chiyo" / "config.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return profile


def instance_for(root: Path) -> dict[str, Any]:
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": root.name,
        "mode": "local_owner",
        "subject": SUBJECT,
        "profile_home": str(root / "profile"),
        "root": str(root),
        "world_home": str(root / "world"),
        "supply_socket": str(root / "missing.supply.sock"),
        "private_items_artifact_id": "artifact:f140-items",
        "artifact_grant_id": "grant:f140",
        "workspace_id": "workspace:f140",
        "contact_home_ref": HOME,
    }


def start_phone(root: Path) -> tuple[Any, threading.Thread, int]:
    service = phone_service.PhoneService(
        instance_for(root), environment={}, life_state_root=state_root_for(root))
    server = phone_server.create_server(service, csrf_token=os.urandom(16).hex(), port=0,
                                        log_path=root / "logs" / "demo.log")
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def stop_phone(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def tool(profile: Path, args: Mapping[str, Any]) -> dict[str, Any]:
    raw = phone_tool.phone_handler(dict(args), platform="cli", hermes_home=str(profile))
    answer = json.loads(raw)
    if "result" in answer and isinstance(answer["result"], Mapping):
        return dict(answer["result"])
    return answer


def main() -> int:
    root = make_root("demo")
    state_root = state_root_for(root)
    inbox_path = state_root / lc.STATE_FILENAME
    ledger_path = state_root / pr.DB_FILENAME

    # -- 1. real arrivals -------------------------------------------------------------
    first_event = str(submit(root, "来拿一下伞", "arrival:one")["event_ref"])
    second_event = str(submit(root, "路边看到一只猫", "arrival:two")["event_ref"])
    inbox_bytes = inbox_path.read_bytes()
    check("inbox has two real arrivals", inbox_path.is_file(), event_refs=[first_event,
                                                                          second_event])

    # -- 2. the instance entry derives the real facts, not the caller's word ----------
    code, bound = cli_instance(root, "binding")
    binding = (bound.get("data") or {}).get("binding") or {}
    check("instance entry derives the real state root and inbox subject", code == 0
          and bound.get("ok") is True
          and binding.get("instance_kind") == "phone_instance"
          and binding.get("state_root") == str(state_root)
          and binding.get("subject") == SUBJECT
          and binding.get("inbox_subject") == SUBJECT
          and binding.get("home_ref") == HOME, binding=binding)
    check("binding read creates no ledger", not ledger_path.exists())

    code, answer = cli_instance(root, "list")
    check("fresh list is ok and absent", code == 0 and answer.get("ok")
          and answer["data"]["ledger_state"] == "ABSENT", exit=code)
    check("fresh list has nobody", answer["data"]["count"] == 0)
    check("read did not create a ledger", not ledger_path.exists())

    code, wrong_subject = cli_instance(root, "--subject", "not-this-subject", "create",
                                       "--display-name", "Eve",
                                       "--operation-key", "op:wrong-subject")
    check("a caller subject that contradicts the instance is refused", code == 2
          and wrong_subject["reason_code"] == "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH"
          and not ledger_path.exists(), answer=wrong_subject)

    foreign_root = make_root("foreign")
    foreign_state = state_root_for(foreign_root)
    submit(foreign_root, "another instance's arrival", "arrival:foreign")
    code, wrong_root = cli_instance(root, "--state-root", str(foreign_state), "create",
                                    "--display-name", "Eve",
                                    "--operation-key", "op:wrong-root")
    check("a caller state root that contradicts the instance is refused", code == 2
          and wrong_root["reason_code"] == "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH"
          and not ledger_path.exists())

    mismatch_root = make_root("mismatch")
    submit(mismatch_root, "written by another identity", "arrival:mismatch",
           subject="another-instance")
    code, mismatch = cli_instance(mismatch_root, "create", "--display-name", "Eve",
                                  "--operation-key", "op:mismatch")
    check("an inbox bound to another subject refuses the first ledger", code == 3
          and mismatch["reason_code"] == "PARTICIPANT_INSTANCE_BINDING_MISMATCH"
          and not (state_root_for(mismatch_root) / pr.DB_FILENAME).exists())

    symlink_root = make_root("symlink", create_state=False)
    symlink_target = make_root("symlink-target")
    (symlink_root / "one-day" / "life").mkdir(parents=True, exist_ok=True)
    os.symlink(state_root_for(symlink_target), state_root_for(symlink_root))
    code, symlinked = cli_instance(symlink_root, "create", "--display-name", "Eve",
                                   "--operation-key", "op:symlink")
    check("a symlinked state root is refused", code == 3
          and symlinked["reason_code"] == "PARTICIPANT_STATE_ROOT_SYMLINK"
          and not (state_root_for(symlink_target) / pr.DB_FILENAME).exists())

    unsafe_root = make_root("unsafe")
    os.chmod(state_root_for(unsafe_root), 0o777)
    code, unsafe = cli_instance(unsafe_root, "create", "--display-name", "Eve",
                                "--operation-key", "op:unsafe")
    check("a shared-writable state root is refused", code == 3
          and unsafe["reason_code"] == "PARTICIPANT_STATE_ROOT_UNSAFE_MODE"
          and not (state_root_for(unsafe_root) / pr.DB_FILENAME).exists())

    # -- 3. two people with the same display name are never merged ---------------------
    code, first = cli_instance(root, "create", "--display-name", "小林",
                               "--alias", "Kobayashi", "--operation-key", "op:create-1")
    check("first participant created", code == 0 and first["status"] == "applied")
    code, second = cli_instance(root, "create", "--display-name", "小林",
                                "--operation-key", "op:create-2")
    check("second participant created", code == 0 and second["status"] == "applied")
    one = first["data"]["participant"]
    two = second["data"]["participant"]
    check("same name stays two opaque ids", one["participant_id"] != two["participant_id"]
          and "小林" not in one["participant_id"], ids=[one["participant_id"],
                                                        two["participant_id"]])
    check("identity is not authority", one["is_authority"] is False
          and one["inherits_grants"] is False and one["created_by"] == "operator:cli")

    code, replay = cli_instance(root, "create", "--display-name", "小林",
                                "--alias", "Kobayashi", "--operation-key", "op:create-1")
    check("same key replays the same person", code == 0
          and replay["status"] == "already_applied"
          and replay["data"]["participant"]["participant_id"] == one["participant_id"])
    code, conflict = cli_instance(root, "create", "--display-name", "别人",
                                  "--operation-key", "op:create-1")
    check("same key with another payload is refused", code == 2
          and conflict["reason_code"] == "PARTICIPANT_OPERATION_KEY_CONFLICT")
    code, listing = cli_instance(root, "list")
    check("no extra person from replay/conflict", listing["data"]["count"] == 2)

    # -- 4. explicit endpoints: two channels for one person, conflict refused ----------
    code, endpoint_one = cli_instance(root, "bind-endpoint", "--participant",
                                      one["participant_id"], "--provider", "local_inbox",
                                      "--account-key", "desk-1",
                                      "--operation-key", "op:endpoint-1")
    code_two, endpoint_two = cli_instance(root, "bind-endpoint", "--participant",
                                          one["participant_id"], "--provider", "telegram",
                                          "--account-key", "42",
                                          "--operation-key", "op:endpoint-2")
    check("same person can have two explicit endpoints", code == 0 and code_two == 0
          and endpoint_one["status"] == "applied" and endpoint_two["status"] == "applied")
    code, endpoint_conflict = cli_instance(root, "bind-endpoint", "--participant",
                                           two["participant_id"], "--provider", "local_inbox",
                                           "--account-key", "desk-1",
                                           "--operation-key", "op:endpoint-3")
    check("one endpoint cannot point at two people", code == 2
          and endpoint_conflict["reason_code"] == "PARTICIPANT_ENDPOINT_ALREADY_BOUND")

    # -- 5. arrival resolution: UNRESOLVED, then one explicit link --------------------
    code, unresolved = cli_instance(root, "resolve-event", "--event-ref", first_event)
    check("unlinked arrival is honestly UNRESOLVED", code == 0
          and unresolved["status"] == "UNRESOLVED")
    code, linked = cli_instance(root, "link-event", "--event-ref", first_event,
                                "--participant", one["participant_id"],
                                "--operation-key", "op:link-1")
    check("operator link applied", code == 0 and linked["status"] == "applied", answer=linked)
    code, resolved = cli_instance(root, "resolve-event", "--event-ref", first_event)
    check("arrival resolves to that participant", code == 0
          and resolved["status"] == "LINKED"
          and resolved["data"]["participant"]["participant_id"] == one["participant_id"])
    code, still_unresolved = cli_instance(root, "resolve-event", "--event-ref", second_event)
    check("a second arrival stays unresolved", still_unresolved["status"] == "UNRESOLVED")

    code, unknown = cli_instance(root, "link-event", "--event-ref",
                                 "contact_evt:" + "0" * 16, "--participant",
                                 one["participant_id"], "--operation-key", "op:link-unknown")
    check("unknown event refused", code == 3
          and unknown["reason_code"] == "PARTICIPANT_EVENT_NOT_FOUND")

    foreign_event = str(submit(foreign_root, "another instance's third arrival",
                               "arrival:foreign-3")["event_ref"])
    code, foreign = cli_instance(root, "link-event", "--event-ref", foreign_event,
                                 "--participant", one["participant_id"],
                                 "--operation-key", "op:link-foreign")
    check("event from another instance refused", code == 3
          and foreign["reason_code"] == "PARTICIPANT_EVENT_NOT_FOUND", answer=foreign)
    code, wrong_instance = run_cli("--state-root", str(state_root), "--subject",
                                   "not-this-subject", "list")
    check("wrong instance refuses to read the ledger", code == 3
          and wrong_instance["reason_code"] == "PARTICIPANT_INSTANCE_MISMATCH")
    check("refused links left the inbox untouched", inbox_path.read_bytes() == inbox_bytes)

    # -- 6. real phone HTTP + the model-side read tool ---------------------------------
    server, thread, port = start_phone(root)
    try:
        profile = write_profile(root, port)
        listed = tool(profile, {"action": "participants"})
        check("phone tool lists participants", listed.get("ok") is True
              and listed["data"]["count"] == 2, port=port)
        detail = tool(profile, {"action": "participant",
                                "participant_id": one["participant_id"]})
        check("phone tool reads one participant", detail.get("ok") is True
              and detail["data"]["participant"]["display_name"] == "小林"
              and detail["data"]["participant"]["linked_event_refs"] == [first_event])
        check("participant detail carries no message body",
              "body" not in json.dumps(detail["data"]).lower())
        check("participant detail redacts raw account keys",
              "account_key\"" not in json.dumps(detail["data"])
              and all(item.get("account_key_redacted") is True
                      for item in detail["data"]["participant"]["endpoints"]))

        blocked = tool(profile, {"action": "participant_create",
                                 "display_name": "Eve"})
        check("model has no participant write action", blocked.get("ok") is False
              and blocked.get("reason_code") == "UNKNOWN_ACTION")

        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/phone/participants" % port, data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5)
            status = 200
        except urllib.error.HTTPError as error:
            status = int(error.code)
        check("phone HTTP has no participant POST route", status == 404, http_status=status)

        # The inbox event detail carries the resolution without the text being re-attributed.
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/phone/contact/events/%s"
                % (port, urllib.parse.quote(first_event, safe=":")), timeout=5) as response:
            event_doc = json.loads(response.read().decode("utf-8"))
        participant_block = (event_doc.get("data") or {}).get("participant") or {}
        check("event detail joins the explicit link", event_doc.get("ok") is True
              and participant_block.get("resolution") == "LINKED")
        check("event join does not claim a relationship", participant_block.get(
            "private_text_included") is False)
    finally:
        stop_phone(server, thread)

    # -- 7. same root, a new phone process: everything is still there -----------------
    server, thread, port = start_phone(root)
    try:
        profile = write_profile(root, port)
        again = tool(profile, {"action": "participants"})
        detail = tool(profile, {"action": "participant",
                                "participant_id": one["participant_id"]})
        check("same root keeps both people", again.get("ok") is True
              and again["data"]["count"] == 2)
        check("same root keeps aliases/endpoints/links",
              detail["data"]["participant"]["aliases"] == ["Kobayashi"]
              and detail["data"]["participant"]["endpoint_count"] == 2
              and detail["data"]["participant"]["linked_event_refs"] == [first_event])
        code, replayed = cli_instance(root, "create", "--display-name", "小林",
                                      "--alias", "Kobayashi",
                                      "--operation-key", "op:create-1")
        code_count, final = cli_instance(root, "list")
        check("restart replay creates nobody new", code == 0
              and replayed["status"] == "already_applied" and final["data"]["count"] == 2)
    finally:
        stop_phone(server, thread)

    # -- 8. unreadable is not an empty list -------------------------------------------
    broken_root = make_root("broken")
    (state_root_for(broken_root) / pr.DB_FILENAME).write_text(
        "not a sqlite database", encoding="utf-8")
    code, broken = cli_instance(broken_root, "list")
    check("unreadable ledger is named, not empty", code == 3
          and broken["reason_code"] == "PARTICIPANT_STATE_CORRUPT")

    summary = {
        "ok": all(item["ok"] for item in CHECKS),
        "checks": CHECKS,
        "root": str(root),
        "foreign_root": str(foreign_root),
        "broken_root": str(broken_root),
        "instance_entry": "the CLI derived state root / subject / home from instance.json",
        "participants": [one["participant_id"], two["participant_id"]],
        "product_model_calls": 0,
        "external_send": False,
        "note": ("participants are identity only; this demo does not send, score relations or "
                 "inherit permissions, and no data was deleted"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
