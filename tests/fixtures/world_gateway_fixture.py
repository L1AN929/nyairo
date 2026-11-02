"""共用夹具：给生产入口接一个真实 World gateway（真 socket / 真 registry / 真 Resolver）。

为什么需要它
============
旅程的每一段现在都是新的 Action，必须经 ``Intent Executor → gateway.sock → Resolver``
取得并消费一次性授权（见 ``2026-10-23-world-journey-segment-authorization``）。
只用 ``apply_move_to`` 或 ``DirectOwnerSubmitPort`` 的旧夹具会缺 socket/registry，
表现为 ``unknown``/``blocked``、没有 travel delivery 与 ``estimated_minutes``。

与 ``tests/test_one_day_life.py::WorldGateway`` 同源（同一套 ``world_body_config`` +
``world_body_service`` 正式服务），差别只有两点：

1. **业务时钟**：把测试的 ``now`` 作为服务的业务时钟交给 ``WorldBodyService(clock=...)``。
   Resolver 的 travel 出发/到达时间与 Body 初始时刻因此和旅程账本处在同一时间域；
   一次性授权的 TTL 与 gateway 消费仍是真实墙钟。
2. **环境**：请求环境补 socket/integration mode/bridge 目录；Intent Executor 只读
   进程环境的 ``WORLD_BODY_INTENT_MUTATION_GATE`` 与 ``WORLD_ACTION_LIFECYCLE_PATH``，
   也一并补上，停止时还原。

不手删 socket：停止时先唤醒 serve 的 selector，再由服务自己的 ``shutdown()`` 释放
授权租约并清理自有 socket。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(_ROOT), str(_ROOT / "components"),
               str(_ROOT / "components" / "world" / "scripts"),
               str(_ROOT / "components" / "world" / "service"),
               str(_ROOT / "components" / "world" / "service" / "bridge")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

WORLD_RUNTIME_ROOT = _ROOT / "components" / "world"

#: Intent Executor 只从**进程环境**读这两个键。
_PROCESS_KEYS = ("WORLD_BODY_INTENT_MUTATION_GATE", "WORLD_ACTION_LIFECYCLE_PATH")
_REGISTRY_NAME = "action_authorizations.json"


def _ensure_substrate(world_home: Path, world_id: str) -> None:
    """旧 ``world_read_service.initialize`` 只写 world_state/timeline；正式 MOVE
    需要 embodiment（pose）才能通过 fresh snapshot。缺失时按正式 substrate
    writer 补一份初始状态（与 ``world_read_service.serve`` 的种子一致）。"""
    import world_body_substrate as substrate

    store = substrate.SubstrateStore(substrate.substrate_path(world_home))
    if store.load() is None:
        store.save(substrate.initial_substrate(world_id=world_id))


class WorldGatewayFixture:
    """本 case 自己的真实 World gateway；``set_now`` 同步业务时钟。"""

    def __init__(self, world_home, *, world_id, environment=None, now=None):
        import world_body_config as wcfg
        import world_body_service as wbsvc

        self.world_home = Path(world_home)
        self._business_now = now or datetime.now(timezone.utc)
        self._saved_process_env = {key: os.environ.get(key) for key in _PROCESS_KEYS}
        _ensure_substrate(self.world_home, world_id)
        self.config = wcfg.load_config(
            dict(environment or {}), runtime_root=WORLD_RUNTIME_ROOT,
            world_home=self.world_home, runtime_mode="canonical",
            authority="production", world_id=world_id)
        self.service = wbsvc.WorldBodyService(
            self.config, tick_seconds=3600.0, clock=lambda: self._business_now,
            instance_id="gw-fixture-%d" % os.getpid())
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 1800.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not Path(self.socket_path).exists():
            if time.monotonic() > deadline:
                raise AssertionError("World gateway socket never appeared: %s"
                                     % self.socket_path)
            time.sleep(0.02)
        self.environment = {
            "WORLD_BODY_SOCKET": str(self.socket_path),
            "WORLD_BODY_INTEGRATION_MODE": "canonical",
            "NYAIRO_WORLD_BRIDGE_DIR": str(
                _ROOT / "components" / "world" / "service" / "bridge"),
            "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
            "WORLD_ACTION_LIFECYCLE_PATH": str(
                self.world_home / "run" / "action_lifecycle.jsonl"),
        }
        os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"
        os.environ["WORLD_ACTION_LIFECYCLE_PATH"] = self.environment[
            "WORLD_ACTION_LIFECYCLE_PATH"]

    # -- 业务时钟 ---------------------------------------------------------
    def set_now(self, moment) -> None:
        if moment is not None:
            self._business_now = moment

    # -- 环境 -------------------------------------------------------------
    def request_environment(self, base=None) -> dict:
        env = dict(base or {})
        env.update(self.environment)
        return env

    # -- 票证据 -----------------------------------------------------------
    def registry_path(self) -> Path:
        return self.world_home / "run" / _REGISTRY_NAME

    def authorizations(self) -> list:
        path = self.registry_path()
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])

    # -- 收尾 -------------------------------------------------------------
    def stop(self) -> None:
        try:
            # 先让 serve 的 selector 醒来，线程才能走到 _stop_requested 检查。
            self.service._stop_requested = True
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(2.0)
                    probe.connect(str(self.service.socket_path))
                    probe.sendall(b'{"command": "status"}\n')
                    probe.recv(65536)
            except OSError:
                pass
            self._thread.join(timeout=5)
            self.service.shutdown()
        finally:
            for key, value in self._saved_process_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
