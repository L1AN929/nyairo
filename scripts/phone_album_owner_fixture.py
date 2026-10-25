#!/usr/bin/env python3
"""Preview fixture: the real Supply owner + the phone HTTP surface for the private album.

This is a *fixture*, not a business implementation.  It starts:

* the real ``LifeSupplyService`` on its own AF_UNIX socket inside ``<root>/data``;
* the real ``PhoneService`` / ``PhoneHTTPServer`` on ``127.0.0.1:<port>``;
* for a **new** root only: one workspace, one artifact grant and the two bookkeeping
  artifacts the phone instance already requires.  The album itself starts **empty** -- every
  picture must be chosen and imported by the browser, never seeded here.

An **existing** root is reused only when it carries a readable ``instance.json``: the owner is
started over the same database and only the live ``supply_socket`` binding is updated.  Nothing
is re-seeded, overwritten or deleted.  A directory without a valid instance is refused rather
than mistaken for a new root.

Sockets follow the repository rule that they are only ever added, never removed: every run
binds a new generation path and leaves older socket files untouched.  This fixture never calls
an unlink helper.

    python3 scripts/phone_album_owner_fixture.py --port 3818
    python3 scripts/phone_album_owner_fixture.py --root /tmp/album-preview --port 3818 --check

``--check`` starts the same chain, reads ``GET /api/phone/album`` once, prints the count and exits
normally (used by the supervisor as a smoke test).  Ctrl+C / SIGTERM shuts down only this
process's own HTTP server and owner.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "test:album-preview"
CSRF = "album-preview-csrf"
ARTIFACT_GRANT = "album-preview:artifact-grant"
WORKSPACE_KEY = "album-preview:workspace"
ITEMS_KEY = "album-preview:items"
READING_KEY = "album-preview:reading"


def _fixture_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-album-preview" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-album-preview")
    base.mkdir(parents=True, exist_ok=True)
    return base


class Owner:
    """The real Life Supply owner on one socket over ``<root>/data``."""

    def __init__(self, data_root: Path, socket_path: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists() or socket_path.is_symlink():
            # A generation path must be new; refusing is the only option that never deletes.
            raise RuntimeError("REFUSING_TO_REUSE_SOCKET_PATH:%s" % socket_path)
        uid = os.geteuid()
        self.socket_path = socket_path
        self.service = LifeSupplyService(
            data_root, operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="album-preview")
        self.server = LifeSupplySocketServer(str(socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        import socket

        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(20)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(12 * 1024 * 1024)
        return json.loads(line.decode("utf-8"))

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        # The socket file is deliberately left on disk: sockets are only added, never removed.
        self.service.close()


def _issue(owner: Owner, capability: str, scope: str, target: str, authority_scope: str,
           use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise SystemExit("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def _provision(owner: Owner) -> tuple[str, str]:
    grant = _issue(owner, "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                   "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                   WORKSPACE_KEY)
    answer = owner.call({"op": "provision_workspace", "grant_id": grant,
                         "operation_key": WORKSPACE_KEY,
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise SystemExit("workspace provisioning failed: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    artifact_grant = _issue(owner, "COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                            "workspace:%s" % workspace_id, "life-supply.artifact.v1",
                            "REUSABLE", ARTIFACT_GRANT)
    return workspace_id, artifact_grant


def _create_bookkeeping(port, workspace_id: str, grant_id: str) -> tuple[str, str]:
    items = port.create_artifact(workspace_id=workspace_id, title="私人日常事项",
                                 content="{}", grant_id=grant_id,
                                 operation_key=lap.operation_key_for("album-preview", ITEMS_KEY))
    reading = port.create_artifact(workspace_id=workspace_id, title="资料页",
                                   content="资料第一页：别人的文章，只能引用不能续写。",
                                   grant_id=grant_id,
                                   operation_key=lap.operation_key_for("album-preview",
                                                                       READING_KEY))
    if not items.applied or not reading.applied:
        raise SystemExit("bookkeeping artifact creation failed: %r / %r" % (items, reading))
    return str(items.artifact_id), str(reading.artifact_id)


def _new_instance(root: Path, owner: Owner, workspace_id: str, grant_id: str,
                  items_id: str, reading_id: str) -> dict:
    world_home = root / "world"
    (world_home / "run").mkdir(parents=True, exist_ok=True)
    instance = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "album-preview",
        "subject": SUBJECT,
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-album-preview",
        "supply_socket": str(owner.socket_path),
        "private_items_artifact_id": items_id,
        "reading_artifact_id": reading_id,
        "artifact_grant_id": grant_id,
        "workspace_id": workspace_id,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }
    phone_service.write_instance(root, instance)
    return instance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3818, help="HTTP port (default 3818)")
    parser.add_argument("--root", default=None,
                        help="explicit root; an existing root is reused, never overwritten")
    parser.add_argument("--check", action="store_true",
                        help="run one smoke read, print the album count, then exit")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
        existed = root.exists()
        if existed and not root.is_dir():
            print("refusing: --root is not a directory: %s" % root, file=sys.stderr)
            return 2
        if not existed:
            root.mkdir(parents=True, exist_ok=True)
    else:
        existed = False
        stamp = "%d-%d" % (os.getpid(), time.time_ns())
        root = _fixture_base() / ("album-preview-%s" % stamp)
        root.mkdir(parents=True, exist_ok=False)

    # Sockets are only added: a fresh generation path each run, older files left untouched.
    socket_path = root / ("supply-album-%d-%d.sock" % (os.getpid(), time.time_ns()))
    instance = None
    if existed:
        if not phone_service.instance_path(root).is_file():
            print("refusing: existing root has no instance.json: %s" % root, file=sys.stderr)
            return 2
        try:
            instance = phone_service.load_instance(root)
        except phone_service.PhoneServiceError as exc:
            print("refusing unreadable instance.json: %s" % exc, file=sys.stderr)
            return 2

    owner = None
    httpd = None
    thread = None
    try:
        owner = Owner(root / "data", socket_path)
        if instance is None:
            workspace_id, grant_id = _provision(owner)
            port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
            items_id, reading_id = _create_bookkeeping(port, workspace_id, grant_id)
            instance = _new_instance(root, owner, workspace_id, grant_id, items_id, reading_id)
            bindings = "created"
        else:
            # Same root: only the live socket binding moves.  The existing workspace, grants,
            # artifact bindings and imported pictures are reused as they are.
            instance["supply_socket"] = str(socket_path)
            phone_service.write_instance(root, instance)
            bindings = "reused"

        service = phone_service.PhoneService(instance, environment={})
        httpd = phone_server.create_server(service, csrf_token=CSRF, port=args.port)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:%d" % int(httpd.server_address[1])

        if args.check:
            request = urllib.request.Request(base_url + "/api/phone/album", method="GET")
            with urllib.request.urlopen(request, timeout=30) as response:
                answer = json.loads(response.read().decode("utf-8"))
            print("album_count=%s" % answer.get("count"))
            print("check=OK")
            return 0

        print("base_url=%s" % base_url)
        print("root=%s" % root)
        print("subject=%s" % instance.get("subject"))
        print("reset=%s" % ("no" if existed else "new-root"))
        print("bindings=%s" % bindings)
        # An observed count, not an assumption about an existing root being empty.
        album_count = "unavailable"
        try:
            request = urllib.request.Request(base_url + "/api/phone/album", method="GET")
            with urllib.request.urlopen(request, timeout=30) as response:
                listing = json.loads(response.read().decode("utf-8"))
            if isinstance(listing, dict) and isinstance(listing.get("count"), int):
                album_count = str(listing["count"])
        except (OSError, ValueError):
            pass
        print("album_count=%s" % album_count)
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
        if owner is not None:
            owner.stop()


if __name__ == "__main__":
    raise SystemExit(main())
