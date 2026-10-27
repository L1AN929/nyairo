#!/usr/bin/env python3
"""One real, bounded use of the daily entry: new neighbourhood + optional body.

What it does (WSL/Linux, from the repo root):

1. creates one brand-new root through the real user entry
   (``scripts/nyairo_day.py init``) with the ``daily_neighborhood_v1`` package
   and the optional virtual body profile;
2. starts the same entry's service (real Supply/World/Body/Memory/PhoneService)
   with the explicit room-living switch and the explicit per-process-tree
   local-action gate;
3. calls the real ``chiyo_phone`` tool (HTTP -> phone service -> owner) to read
   world / wallet / inventory and the Body bands, and to submit one *legal*
   local action (POSE) that the owner actually commits;
4. stops the service properly, starts the *same* root again, and confirms the
   package/profile/state were kept (no re-init, no second move-in grant);
5. stops everything it started.

It never calls a product model, never sends a message, and never pretends that
the scripted choice was the model's own.  The clock/route is not simulated here:
the demo stays at home and reports what the owner says, including "the store
isn't reachable from here" as a real answer rather than faking arrival.

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/living_entry_demo.py
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

RESULT_PATH = ROOT / ".agents" / "worker-runs" / "20261027-living-entry-demo.json"
PACKAGE = "daily_neighborhood_v1"
#: Explicit synthetic initial values: they belong to this demo, not to a
#: measured body.  Hunger/toilet stay at 0 so the state still reads as virtual.
SYNTHETIC = {"hunger": 0.0, "thirst": 0.72, "toilet": 0.0}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _entry():
    return importlib.import_module("nyairo_day")


def _tool(profile_home: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    from chiyo_bundle import phone_tool

    raw = phone_tool.phone_handler(dict(payload), hermes_home=str(profile_home),
                                   platform="cli")
    return json.loads(raw)


def _write_profile(home: Path, port: int) -> None:
    target = home / "chiyo"
    target.mkdir(parents=True, exist_ok=True)
    # ``cli_owner`` / ``local_owner_uid`` are profile-root facts (the same rule
    # the real plugin gate reads); the phone section only names the loopback URL.
    (target / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.getuid() if hasattr(os, "getuid") else None,
        "phone": {
            "enabled": True,
            "base_url": "http://127.0.0.1:%d" % int(port),
            "write_enabled": True,
            "private_artifacts": True,
        },
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def _start_foreground(root: Path, port: int, *switches: str):
    """Start the real entry in foreground mode as a direct child (so we can reap it)."""

    import subprocess

    log = root / "logs" / "living-entry-demo-start.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("a", encoding="utf-8")
    try:
        child = subprocess.Popen(
            [sys.executable, "-B", str(ROOT / "scripts" / "nyairo_day.py"),
             "start", "--root", str(root), "--port", str(int(port)),
             "--foreground", *switches],
            stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
            cwd=str(ROOT), start_new_session=True, close_fds=True)
    finally:
        handle.close()
    entry = _entry()
    if not entry._wait_ready(child, root, port):
        tail = entry._log_tail(str(log))
        child.terminate()
        try:
            child.wait(timeout=20)
        except Exception:
            pass
        raise RuntimeError("start did not become ready: %s" % tail[-400:])
    return child


def _stop_foreground(child, root: Path) -> int:
    """SIGTERM is the entry's own stop signal; wait() reaps the child we spawned."""

    entry = _entry()
    child.terminate()
    try:
        child.wait(timeout=60)
    except Exception:
        child.kill()
        child.wait(timeout=20)
    # The entry process already wrote STOPPED in its own finally; the documented
    # stop command then confirms the bookkeeping (pid is gone, so no zombie).
    return entry.cmd_stop(_namespace(entry, root=str(root)))


def _namespace(entry, **values: Any) -> argparse.Namespace:
    defaults = dict(world_package=None, lived_needs=False,
                    lived_needs_hunger=None, lived_needs_thirst=None,
                    lived_needs_toilet=None, room_living=None, world_actions=None,
                    memory_authority=None, run_id=None, foreground=False, json=True,
                    port=None)
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _envelope(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    """The phone tool wraps the service's own envelope under ``result``."""

    result = tool_result.get("result")
    return dict(result) if isinstance(result, Mapping) else dict(tool_result)


def _data(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _envelope(tool_result)
    data = envelope.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _bands(world: Mapping[str, Any]) -> dict[str, Any]:
    body = _data(world).get("body")
    body = body if isinstance(body, Mapping) else {}
    return dict(body.get("needs") or {})


def _pose(world: Mapping[str, Any]) -> str:
    body = _data(world).get("body")
    body = body if isinstance(body, Mapping) else {}
    return str(body.get("pose") or "")


def _location(world: Mapping[str, Any]) -> dict[str, Any]:
    location = _data(world).get("location")
    return dict(location) if isinstance(location, Mapping) else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    if os.name != "posix" or not hasattr(os, "getuid"):
        print("这个演示只在 WSL/Linux 里运行（Windows 原生没有 Life 需要的 POSIX 能力）。")
        return 2

    entry = _entry()
    parent = Path(tempfile.mkdtemp(prefix="nyairo-living-entry-"))
    root = parent / "instance"
    profile_home = parent / "phone-profile"
    port = _free_port()
    summary: dict[str, Any] = {
        "kind": "living_entry_demo",
        "root": str(root),
        "package": PACKAGE,
        "synthetic_initial_profile": dict(SYNTHETIC),
        "product_model_requests": 0,
        "scripted_decision": True,
        "steps": [],
    }
    started = False
    stopped = False
    running_child = None
    try:
        rc = entry.cmd_init(_namespace(
            entry, root=str(root), world_package=PACKAGE, lived_needs=True,
            lived_needs_hunger=SYNTHETIC["hunger"],
            lived_needs_thirst=SYNTHETIC["thirst"],
            lived_needs_toilet=SYNTHETIC["toilet"]))
        summary["steps"].append({"step": "init", "exit": rc})
        if rc != 0:
            raise RuntimeError("init refused")
        config = entry._managed_config(root)
        summary["config_after_init"] = config
        if not (root / "world-package" / "world.json").exists():
            raise RuntimeError("the entry did not instantiate the selected package")

        child = _start_foreground(root, port, "--room-living", "--world-actions")
        running_child = child
        summary["steps"].append({"step": "start_foreground", "exit": 0})
        started = True
        record = entry._read_json(entry._record_path(root))
        actual_port = int(record.get("port") or port)
        summary["service"] = {"port": actual_port,
                              "room_living": record.get("room_living"),
                              "world_actions": record.get("world_actions")}
        _write_profile(profile_home, actual_port)

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            status_rc = entry.cmd_status(_namespace(entry, root=str(root), json=False))
            phone_rc = entry.cmd_phone(_namespace(entry, root=str(root), json=False))
        status_text = captured.getvalue()
        summary["status_command"] = {
            "status_exit": status_rc, "phone_exit": phone_rc,
            "names_neighborhood": "街区：日常街区" in status_text,
            "names_body": "虚拟身体：已启用" in status_text,
            "names_tool_entry": "chiyo_phone" in status_text,
        }
        if status_rc != 0 or phone_rc != 0 or not all(
                summary["status_command"][key] for key in
                ("names_neighborhood", "names_body", "names_tool_entry")):
            raise RuntimeError("status/phone did not show the entry configuration")

        world = _tool(profile_home, {"action": "world"})
        wallet = _tool(profile_home, {"action": "wallet"})
        inventory = _tool(profile_home, {"action": "inventory"})
        store = _tool(profile_home, {"action": "store"})
        store_envelope = _envelope(store)
        summary["reads"] = {
            "world_ok": bool(_envelope(world).get("ok")),
            "location": _location(world),
            "pose_before": _pose(world),
            "bands": _bands(world),
            "wallet_ok": bool(_envelope(wallet).get("ok")),
            "wallet": _data(wallet),
            "inventory_ok": bool(_envelope(inventory).get("ok")),
            "inventory": _data(inventory),
            "store_status": store_envelope.get("status"),
            "store_reason": store_envelope.get("reason_code"),
            "destinations": _data(world).get("destinations"),
        }
        if not _envelope(world).get("ok"):
            raise RuntimeError("world read failed")
        if _location(world).get("place_id") != "home":
            raise RuntimeError("the new instance did not start at home")
        if not _bands(world):
            raise RuntimeError("the body profile is not visible through the tool")
        if not _envelope(wallet).get("ok") or not _envelope(inventory).get("ok"):
            raise RuntimeError("wallet/inventory read failed through the tool")

        # One legal local action through the real tool -> HTTP -> owner chain.
        verbs = (_data(world).get("actions") or {}).get("verbs") or []
        pose_available = any(row.get("verb") == "POSE" and row.get("available")
                             for row in verbs if isinstance(row, Mapping))
        if not pose_available:
            raise RuntimeError("the owner does not offer POSE here")
        target_pose = "seated" if _pose(world) != "seated" else "standing"
        action = _tool(profile_home, {
            "action": "world_action", "world_action": "POSE",
            "params": {"pose": target_pose},
            "operation_key": "living-entry-demo:pose:v1",
        })
        after = _tool(profile_home, {"action": "world"})
        summary["action"] = {
            "verb": "POSE", "target_pose": target_pose,
            "status": action.get("status"), "ok": bool(action.get("ok")),
            "pose_after": _pose(after),
            "bands_after": _bands(after),
        }
        if action.get("status") not in ("applied", "already_applied"):
            raise RuntimeError("the owner refused the local action")
        if _pose(after) != target_pose:
            raise RuntimeError("the owner's snapshot did not show the committed pose")

        rc = _stop_foreground(child, root)
        summary["steps"].append({"step": "stop", "exit": rc})
        stopped = True
        if rc != 0:
            raise RuntimeError("stop refused")

        # Same root, same entry: nothing is re-initialised and the grant is not paid twice.
        before_restart = {
            "wallet": _data(wallet).get("balance"),
            "world_id": _location(world).get("place_id"),
        }
        child2 = _start_foreground(root, port)
        running_child = child2
        summary["steps"].append({"step": "restart_same_root", "exit": 0})
        started = True
        stopped = False
        record = entry._read_json(entry._record_path(root))
        if not record.get("room_living") or not record.get("world_actions"):
            raise RuntimeError("the same-root restart did not keep the explicit run switches")
        _write_profile(profile_home, int(record.get("port") or port))
        world2 = _tool(profile_home, {"action": "world"})
        wallet2 = _tool(profile_home, {"action": "wallet"})
        summary["restart"] = {
            "world_ok": bool(_envelope(world2).get("ok")),
            "location": _location(world2),
            "bands": _bands(world2),
            "pose": _pose(world2),
            "wallet_balance": _data(wallet2).get("balance"),
            "package_id": entry._managed_config(root).get("world_package"),
            "room_living": record.get("room_living"),
            "world_actions": record.get("world_actions"),
        }
        if not _envelope(world2).get("ok") or _location(world2).get("place_id") != "home":
            raise RuntimeError("the same root did not come back at home")
        if summary["restart"]["wallet_balance"] != before_restart["wallet"]:
            raise RuntimeError("the restart changed the wallet (a second grant?)")
        if not _bands(world2):
            raise RuntimeError("the body profile was lost across restart")
        rc = _stop_foreground(child2, root)
        summary["steps"].append({"step": "final_stop", "exit": rc})
        stopped = True
        if rc != 0:
            raise RuntimeError("final stop refused")
        summary["ok"] = True
    except Exception as exc:  # noqa: BLE001 - the demo reports the real reason
        summary["ok"] = False
        summary["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    finally:
        if started and not stopped and running_child is not None:
            try:
                _stop_foreground(running_child, root)
            except Exception:
                pass
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1,
                                          sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        print("结果：%s" % RESULT_PATH)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
