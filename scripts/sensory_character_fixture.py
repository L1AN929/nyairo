#!/usr/bin/env python3
"""开源角色包与可组合身体 / 多通道感官场与文字声景卡：独立预览实例。

    /tmp/rc104-venv/bin/python -B scripts/sensory_character_fixture.py --port 3861

直接复用 ``somatic_comfort_fixture.build_stack`` 的真实 owner 栈：
* 真实 ``coastal_town`` World（位于 ``home/bedroom``，含窗边盆栽、床头灯与书桌）；
* 真实 ``world_ambient_events`` 与 ``world_room_environment`` 环境开关；
* 真实 ``somatic_comfort`` 窗户 / 区域 / 外套 / 热茶可交互接口；
* 启动时不预先落盘 ``character_pack_state.json`` 或 ``sensory_cards.json``，保证首次
  ``GET`` 直接展示开源中性默认预设 ``neutral_resident``（``"TA"``）并验证零写入。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import somatic_comfort_fixture as base  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = base.SUBJECT


def build_stack(
    root: Path | str, *, port: int = 0, capability: object = None
) -> SimpleNamespace:
    """Create an isolated preview instance at ``home`` with World/Somatic/Character/Sensory ready."""

    os.environ.setdefault("CHIYO_WORLD_LOCAL_OWNER_PHONE", SUBJECT)
    stack = base.build_stack(root, port=port, capability=capability)
    try:
        stack.service.environment["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"] = "1"
        stack.service.environment["CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"] = "1"
        # Reset cached PhoneWorld facade so updated environment flags take effect cleanly.
        stack.service._world = None
        snapshot = stack.service.world()
        data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        stack.location = {
            "place_id": location.get("place_id"),
            "area_id": location.get("area_id"),
        }
        return stack
    except BaseException:
        base.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    base.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    base.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sensory_character_fixture")
    parser.add_argument("--port", type=int, default=3861)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)

    root = (
        Path(args.root)
        if args.root
        else Path(tempfile.gettempdir())
        / ("nyairo-sensory-character-%d-%d" % (os.getpid(), time.time_ns()))
    )
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        parser.error("PORT_BIND_FAILED")

    stop_event = threading.Event()

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cap_obj = getattr(stack, "capability", None)
    cap_token = getattr(cap_obj, "token", None) if cap_obj is not None else None
    csrf_token = getattr(stack, "csrf_token", None) or getattr(server, "csrf_token", None)

    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "port": stack.port_number,
                "root": str(stack.root),
                "capability": cap_token,
                "capability_path": str(phone_capability.capability_path(stack.root)),
                "csrf": csrf_token,
                "location": stack.location,
                "endpoints": [
                    "/api/phone/character/pack",
                    "/api/phone/body/sensory",
                    "/api/phone/body/garment-window",
                    "/api/phone/body/care-offering",
                    "/api/phone/body/somatic",
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()

    serve_stack(stack)
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    finally:
        close_stack(stack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
