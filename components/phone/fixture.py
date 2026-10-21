#!/usr/bin/env python3
"""A runnable **phone fixture**: one real instance root + one real loopback HTTP face.

给父级做真实 Edge / 整链验收用的最小启动/关闭入口。它不是替身：

* ``PhoneService`` 用的是**正式装配**（实例绑定 + A 自己的 factory，``actor="user"``）；
* HTTP 面是 ``create_server`` 的真 loopback 服务，四道闸门（Host / Origin / Sec-Fetch / CSRF）都在；
* 传输是本实例自己的 ``LocalContactInbox``（真 JSON 账本），事件落在 ``<root>/one-day/life/state``；
* 夹具根**建在磁盘上并保留**（不删、不清理），方便父级事后按路径核对。

用法（父级）::

    from components.phone import fixture
    handle = fixture.start_fixture(project_port=port)   # port: 同一位 owner 的端口对象（可选）
    handle["url"]        # http://127.0.0.1:<port>/
    handle["csrf_token"] # POST 需要 X-CSRF-Token
    handle["root"]       # 实例根（保留）
    handle["instance"]   # 实例 payload
    handle["service"]    # PhoneService（可直接读，便于断言）
    handle["close"]()    # 关闭 HTTP 面 + 线程；根与账本保留

不带 ``project_port`` 时，端口按实例自己的 supply socket 建（POSIX 上可真 Supply）；
socket 不可用则 A 面诚实 ``SHARED_PRACTICE_UNBOUND``，HTTP 面仍然起得来。
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import server as phone_server
from . import service as phone_service


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _base_dir() -> Path:
    override = (os.environ.get("NYAIRO_PHONE_FIXTURE_ROOT")
                or os.environ.get("PI_SCRATCH_DIR"))
    base = ((Path(override) / "nyairo-phone-fixture") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-phone-fixture"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def instance_payload(root: Path, *, subject: str) -> dict:
    """The instance document a ``prepare-demo`` root would carry (same shape, honest ids)."""

    world_home = root / "one-day" / "world-home"
    world_home.mkdir(parents=True, exist_ok=True)
    state_root = root / "one-day" / "life" / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    socket_path = run_dir / "supply.sock"
    if not socket_path.exists():
        socket_path.write_text("", encoding="utf-8")
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "phone-fixture",
        "subject": subject,
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-phone-fixture",
        "supply_socket": str(socket_path),
        "private_items_artifact_id": "artifact:" + "1" * 32,
        "artifact_grant_id": "grant:" + "2" * 32,
        "workspace_id": "workspace:" + "3" * 32,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


def start_fixture(*, root: Optional[Path | str] = None, subject: str = "chiyo",
                  project_port: Any = None, port: int = 0,
                  csrf_token: Optional[str] = None) -> dict[str, Any]:
    """Start one real phone fixture.  Returns a handle with ``close()``."""

    if root is None:
        target = _base_dir() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    else:
        target = Path(root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("fixture root is not empty: %s" % target)
    target.mkdir(parents=True, exist_ok=True)
    instance = instance_payload(target, subject=subject)
    service = phone_service.PhoneService(
        instance, environment={"CHIYO_WORLD_HOME": instance["world_home"]},
        project_port=project_port)
    token = csrf_token or secrets.token_hex(16)
    httpd = phone_server.create_server(service, csrf_token=token,
                                       port=port or _free_port(),
                                       log_path=target / "logs" / "serve.log")
    bound = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    state = {"closed": False}

    def close() -> dict[str, Any]:
        """Shut the HTTP face down once.  The instance root and its ledgers stay on disk."""

        if state["closed"]:
            return {"ok": True, "already_closed": True, "root": str(target)}
        state["closed"] = True
        try:
            httpd.shutdown()
        finally:
            httpd.server_close()
        thread.join(timeout=10)
        return {"ok": True, "already_closed": False, "root": str(target)}

    return {
        "ok": True,
        "root": str(target),
        "url": "http://127.0.0.1:%d/" % bound,
        "api": "http://127.0.0.1:%d/api/phone" % bound,
        "port": bound,
        "csrf_token": token,
        "instance": instance,
        "service": service,
        "close": close,
    }


__all__ = ["instance_payload", "start_fixture"]
