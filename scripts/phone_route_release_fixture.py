#!/usr/bin/env python3
"""Preview fixture: the phone HTTP surface for the TOILET route.

Two modes, one HTTP layer:

* default (**stub owner**) -- the ``tests/test_room_living_api.py`` fixture: a real
  ``PhoneService`` plus the real loopback ``create_server``, with the store and world
  facades replaced by recording stubs (``StubShopping`` / ``StubWorld``).  A request
  proves only P's own layer -- routing, whitelist, identity refusal and envelope
  mapping -- while the owner answer is the stub's.
* ``--real-world`` -- a **fresh, isolated** World home on this machine: the real
  ``WorldBodyService`` (real gateway socket, Resolver, ledger, one-shot ticket
  registry), a fixture-declared toilet + explicit fixture lived-needs profile, and
  the real ``PhoneWorld`` facade behind the same HTTP routes.  A successful POST
  therefore proves the whole local chain for one declared facility.  It still loads
  no model secret, starts nothing outside this process, and touches no existing
  instance.  Requires a real uid (WSL/Linux), because the local-owner binding checks
  ``os.getuid()``.

Every run uses a fresh ``mkdtemp`` root under the system temp directory (the World
home in real mode is a second retained temp root) and leaves both in place (nothing
is ever unlinked).  The CSRF token lives only in this process and is *not* printed: a
browser gets it the normal way, ``GET /api/phone/state``, and adds it as
``X-CSRF-Token`` as before.

    python3 scripts/phone_route_release_fixture.py --port 3847
    python3 scripts/phone_route_release_fixture.py --port 3847 --real-world

Only the listening URL is printed on stdout.  Ctrl+C / SIGTERM shuts down this
process's own HTTP server through ``shutdown()`` / ``server_close()`` and, in real
mode, the World service through its own shutdown (no socket file or directory is
removed by hand).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402


def _load_module(name: str, path: Path):
    """Import a fixture module by path (``tests/`` has no package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - only on a broken tree
        raise RuntimeError("cannot load the fixture: %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_room_fixture():
    """Import the existing room fixture module by path."""
    return _load_module("nyairo_room_fixture", ROOT / "tests" / "test_room_living_api.py")


def _load_toilet_chain_fixture():
    """The new real-owner fixture: helper declarations + the shared PhoneWorld base."""
    return _load_module("nyairo_toilet_chain_fixture",
                        ROOT / "tests" / "test_toilet_phone_chain.py")


def _start_real_world(chain, owner):
    """One fresh World home + the real service on its real gateway socket."""

    world_home = owner.unique_home()
    owner.seed_world(world_home)
    owner.seed_substrate(world_home)
    owner.seed_body(world_home)
    chain.declare_facility(world_home, "home-toilet-01")
    chain.enable_lived_needs(world_home)

    # Server-side opt-in: the service reads this at construction, before the socket
    # exists.  It is this preview process's own binding, never a client value.
    os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = owner.wpe.OWNER
    for key, value in owner.ENVIRONMENT.items():
        os.environ[key] = value

    config = owner.wbc.load_config(
        dict(owner.ENVIRONMENT), runtime_root=owner.RUNTIME_ROOT,
        world_home=world_home, runtime_mode="canonical",
        authority="production", world_id="shiomi_city")
    service = owner.wbsvc.WorldBodyService(
        config, now=owner.T0, instance_id="route-preview", tick_seconds=0.05)
    service.startup()
    thread = threading.Thread(
        target=service.serve, kwargs={"max_seconds": 1800.0}, daemon=True)
    thread.start()
    socket_path = world_home / "run" / "gateway.sock"
    deadline = time.monotonic() + 10.0
    while not socket_path.exists():
        if time.monotonic() > deadline:
            service.shutdown()
            raise RuntimeError("real gateway socket never appeared: %s" % socket_path)
        time.sleep(0.02)
    return world_home, service, thread


def _stop_real_world(service, thread) -> None:
    """The service's own shutdown path -- never an unlink of its socket."""

    try:
        service._stop_requested = True
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            probe.connect(str(service.socket_path))
            probe.sendall(b'{"command": "status"}\n')
            probe.recv(65536)
    except OSError:
        pass
    thread.join(timeout=5)
    service.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phone TOILET-route preview fixture")
    parser.add_argument("--port", type=int, default=3847)
    parser.add_argument("--root", default=None,
                        help="reuse an existing preview root instead of a fresh mkdtemp")
    parser.add_argument("--real-world", action="store_true",
                        help="use the real World service + PhoneWorld (fresh isolated home)")
    args = parser.parse_args(argv)

    fixture = _load_room_fixture()
    root = Path(args.root) if args.root else Path(tempfile.mkdtemp(prefix="nyairo-route-preview-"))
    root.mkdir(parents=True, exist_ok=True)

    world_service = None
    world_thread = None
    try:
        if args.real_world:
            chain = _load_toilet_chain_fixture()
            # The chain module already imported the shared base by name; reuse that
            # exact module (seed helpers + fixture environment), do not import twice.
            owner = sys.modules.get("test_phone_world_owner")
            if owner is None:  # pragma: no cover - only if the chain import changed
                owner = _load_module(
                    "test_phone_world_owner",
                    ROOT / "tests" / "test_phone_world_owner.py")
            world_home, world_service, world_thread = _start_real_world(chain, owner)
            instance = fixture.instance_for(root)
            instance["world_home"] = str(world_home)
            instance["world_body_socket"] = str(world_home / "run" / "gateway.sock")
            instance["world_id"] = "shiomi_city"
            kind = "real"
            service = phone_service.PhoneService(
                instance, environment=dict(owner.ENVIRONMENT),
                shopping=fixture.StubShopping(), world=None)
        else:
            kind = "stub"
            instance = fixture.instance_for(root)
            world = fixture.StubWorld()
            service = phone_service.PhoneService(instance, environment={},
                                                 shopping=fixture.StubShopping(),
                                                 world=world)

        httpd = phone_server.create_server(service, csrf_token=fixture.CSRF, port=args.port)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()

        stopping = threading.Event()

        def _stop(_signum=None, _frame=None) -> None:
            stopping.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        # stdout stays exactly the listening URL (the old contract); the mode note
        # goes to stderr so a URL parser is never confused.
        print("mode=%s" % kind, file=sys.stderr, flush=True)
        print("http://127.0.0.1:%d" % int(httpd.server_address[1]), flush=True)
        try:
            stopping.wait()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=10)
    finally:
        if world_service is not None:
            _stop_real_world(world_service, world_thread)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
