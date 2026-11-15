#!/usr/bin/env python3
"""主体日常自主拒绝边界、挂着的话题余温、主动改画风与真实异步延迟回信：独立预览实例。

    /tmp/rc104-venv/bin/python -B scripts/autonomy_rhythm_fixture.py --port 3862

直接复用 ``sensory_character_fixture.build_stack`` 的真实 owner 栈：
* 真实 ``coastal_town`` World（位于 ``home/bedroom``，含外出移动与环境事件）；
* 真实 ``somatic_comfort`` / ``desk_presence`` / ``character_pack`` / ``body_sensory`` 接口；
* 启动时不预先落盘 ``autonomy_rhythm.json``，保证首次 ``GET /api/phone/autonomy/rhythm``
  直接展示开源中性默认预设 ``neutral_resident``（``"TA"``，``revision=0``）并验证 100% 零写入。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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

import sensory_character_fixture as base  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = base.SUBJECT


def build_stack(
    root: Path | str, *, port: int = 0, capability: object = None
) -> SimpleNamespace:
    """Create an isolated preview instance at ``home`` with Autonomy Rhythm & live owners ready."""

    return base.build_stack(root, port=port, capability=capability)


def serve_stack(stack: SimpleNamespace) -> None:
    base.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    base.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="autonomy_rhythm_fixture")
    parser.add_argument("--port", type=int, default=3862)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)

    created_temp_root = not bool(args.root)
    root = (
        Path(args.root)
        if args.root
        else Path(tempfile.gettempdir())
        / ("nyairo-autonomy-rhythm-%d-%d" % (os.getpid(), time.time_ns()))
    )
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        if created_temp_root:
            shutil.rmtree(root, ignore_errors=True)
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
                    "/api/phone/autonomy/rhythm",
                    "/api/phone/character/pack",
                    "/api/phone/body/sensory",
                    "/api/phone/desk/presence",
                    "/api/phone/body/garment-window",
                    "/api/phone/body/care-offering",
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
            stop_event.wait(timeout=0.5)
    finally:
        close_stack(stack)
        if created_temp_root:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
