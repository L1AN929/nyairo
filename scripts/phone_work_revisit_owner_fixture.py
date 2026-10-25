#!/usr/bin/env python3
"""Preview fixture: real Supply owner + phone HTTP over a seeded work with three versions.

This is a *fixture*, not a business implementation.  It starts:

* the real ``LifeSupplyService`` on its own AF_UNIX socket in an isolated root
  (reusing the owner/service construction from ``tests/test_v04_work_revisit_history.py``);
* the real ``PhoneService`` HTTP surface on ``127.0.0.1:<port>``;
* one private work titled 夜航手记 with three genuinely different versions, seeded through the
  owner's own ``create_artifact`` / ``commit_version`` (no in-memory substitute).

Only non-sensitive metadata is printed (base URL, root, subject, artifact id).  The CSRF token is
a fixed test constant and is never printed.  Ctrl+C / SIGTERM shuts down only this process's own
HTTP server and owner; nothing global is killed or deleted.

    python3 scripts/phone_work_revisit_owner_fixture.py --port 3817
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import test_v04_work_revisit_history as base  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = base.SUBJECT
CSRF = "work-revisit-preview-csrf"
WORK_TITLE = "夜航手记"
SENTINEL = base.SENTINEL_OLD
V1 = "第一版：%s 夜航第一天，她把这句话写在手记最前面。" % SENTINEL
V2 = V1 + "\n第二版：途中她又补了一段，旧句子还留着。"
V3 = V2 + "\n第三版：靠岸之前，她删去重复的一行。"


def _issue(owner, capability: str, scope: str, target: str, authority_scope: str,
           use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise SystemExit("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def _provision_workspace(owner) -> str:
    grant = _issue(owner, "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                   "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                   "issue:workspace:%s" % SUBJECT)
    answer = owner.call({"op": "provision_workspace", "grant_id": grant,
                         "operation_key": "work-revisit-preview:workspace:%s" % SUBJECT,
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise SystemExit("workspace provisioning failed: %r" % (answer,))
    return str(answer["workspace"]["workspace_id"])


def _create(port, workspace_id: str, grant_id: str, title: str, content: str,
            tag: str) -> str:
    outcome = port.create_artifact(
        workspace_id=workspace_id, title=title, content=content, grant_id=grant_id,
        operation_key=lap.operation_key_for("work-revisit-preview", tag, SUBJECT))
    if not outcome.applied:
        raise SystemExit("artifact creation failed: %r" % (outcome,))
    return str(outcome.artifact_id)


def _commit(port, artifact_id: str, grant_id: str, content: str, tag: str) -> str:
    head = port.read_head(artifact_id) or {}
    outcome = port.commit_version(
        artifact_id=artifact_id, expected_head=str(head["version_id"]), content=content,
        grant_id=grant_id,
        operation_key=lap.operation_key_for("work-revisit-preview", tag, SUBJECT))
    if not outcome.applied:
        raise SystemExit("version commit failed: %r" % (outcome,))
    return str((port.read_head(artifact_id) or {})["version_id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3817, help="HTTP port (default 3817)")
    parser.add_argument("--root", default=None,
                        help="isolated root to create; existing roots are refused, never overwritten")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
        if root.exists():
            print("refusing existing root (not overwriting): %s" % root, file=sys.stderr)
            return 2
        root.mkdir(parents=True, exist_ok=False)
    else:
        stamp = "%d-%d" % (os.getpid(), time.time_ns())
        root = base.fixture_base() / ("work-revisit-preview-%s" % stamp)
        root.mkdir(parents=True, exist_ok=False)

    owner = base.SupplyOwner(root)
    httpd = None
    thread = None
    try:
        workspace_id = _provision_workspace(owner)
        grant_id = _issue(owner, "COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                          "workspace:%s" % workspace_id, "life-supply.artifact.v1",
                          "REUSABLE", "issue:artifact:%s" % SUBJECT)
        port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)

        work_id = _create(port, workspace_id, grant_id, WORK_TITLE, V1, "work-v1")
        _commit(port, work_id, grant_id, V2, "work-v2")
        _commit(port, work_id, grant_id, V3, "work-v3")
        items_id = _create(port, workspace_id, grant_id, "私人日常事项", "{}", "items")
        reading_id = _create(port, workspace_id, grant_id, "资料页",
                             "资料第一页：别人的文章，只能引用不能续写。", "reading")

        world_home = root / "world"
        (world_home / "run").mkdir(parents=True, exist_ok=True)
        instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "work-revisit-preview",
            "subject": SUBJECT,
            "local_owner_uid": os.geteuid(),
            "root": str(root),
            "profile_home": str(root),
            "world_home": str(world_home),
            "world_id": "w-work-revisit-preview",
            "supply_socket": str(owner.socket_path),
            "private_items_artifact_id": items_id,
            "reading_artifact_id": reading_id,
            "artifact_grant_id": grant_id,
            "workspace_id": workspace_id,
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }
        service = phone_service.PhoneService(instance, environment={})
        httpd = phone_server.create_server(service, csrf_token=CSRF, port=args.port)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:%d" % int(httpd.server_address[1])

        print("base_url=%s" % base_url)
        print("root=%s" % root)
        print("subject=%s" % SUBJECT)
        print("artifact_id=%s" % work_id)
        print("title=%s" % WORK_TITLE)
        print("versions=3")
        sys.stdout.flush()

        stop = threading.Event()

        def _stop(signum, frame):  # noqa: ARG001
            stop.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        while not stop.wait(0.5):
            pass
        return 0
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=10)
        owner.stop()


if __name__ == "__main__":
    raise SystemExit(main())
