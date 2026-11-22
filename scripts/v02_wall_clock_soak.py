#!/usr/bin/env python3
"""Step 3B: Real continuous 24-hour wall-clock soak runner for Nyairo 0.2.

Honesty rules:
- Uses real wall-clock elapsed time only (never fabricates or compresses hours).
- Runs an isolated live nyairo_day.py instance (default /root/.nyairo-v02-soak-24h on port 3849)
  so the user's primary :3831 instance is never disturbed.
- Zero LLM calls during soak: exercises real 21-route zero-write HTTP GET sweeps, external
  NYAIRO_V02_CONTROL_ROOT enforcement, EventFeedPoller wake coalescing, RSS/FD stability,
  and one graceful stop/start restart continuity check.
- Evaluates collected evidence with ``verify_wall_clock_soak_gate(..., min_wall_hours=24.0)``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.life.scripts import autonomous_life_events as ale
from components.life.scripts import v02_lived_slices_and_vd_protocols as vd_proto

DEFAULT_SOAK_ROOT = Path("/root/.nyairo-v02-soak-24h")
DEFAULT_SOAK_PORT = 3849
DEFAULT_INTERVAL_SECONDS = 600
DEFAULT_TARGET_HOURS = 24.0

GET_ROUTES = (
    "/api/phone/state",
    "/api/phone/living",
    "/api/phone/desk/presence",
    "/api/phone/home/window",
    "/api/phone/autonomy/rhythm",
    "/api/phone/v03/ecology",
    "/api/phone/v03/embodied-social",
    "/api/phone/v03/encounters",
    "/api/phone/v04/neighborhood",
    "/api/phone/v04/builder-trylive",
    "/api/phone/v04/elastic-play",
    "/api/phone/v02/agency-depth",
    "/api/phone/v02/crossmodal",
    "/api/phone/v02/runtime-migration",
    "/api/phone/v02/honest-ux",
    "/api/phone/v02/recall-projection",
    "/api/phone/v02/common-ground",
    "/api/phone/v02/ecology-growth",
    "/api/phone/v02/world-lineage",
    "/api/phone/v05/living-town",
    "/api/phone/v05/inner-spark",
    "/api/phone/v05/home-craft",
    "/api/phone/v08/living-traces",
    "/api/phone/v08/emergent-echoes",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _evidence_path(soak_root: Path) -> Path:
    return soak_root / "soak_evidence.json"


def _samples_path(soak_root: Path) -> Path:
    return soak_root / "soak_samples.jsonl"


def _monitor_pid_path(soak_root: Path) -> Path:
    return soak_root / "soak_monitor.pid"


def _instance_root(soak_root: Path) -> Path:
    return soak_root / "instance"


def _authority_root(soak_root: Path) -> Path:
    inst = _instance_root(soak_root)
    return inst.parent / f"{inst.name}-memory-authority"


def evaluate_soak_evidence(
    evidence: Mapping[str, Any],
    *,
    min_wall_hours: float = DEFAULT_TARGET_HOURS,
) -> dict[str, Any]:
    """Combine wall-clock gate with strict runtime health checks."""
    gate = vd_proto.verify_wall_clock_soak_gate(evidence, min_wall_hours=float(min_wall_hours))
    failures = int(evidence.get("failures_total") or 0)
    aux_leaks = int(evidence.get("aux_file_leaks_total") or 0)
    non_200 = int(evidence.get("non_200_total") or 0)
    healthy = (failures == 0) and (aux_leaks == 0) and (non_200 == 0)

    passed = bool(gate.get("passed")) and healthy
    reason_code = str(gate.get("reason_code") or "UNKNOWN")
    if bool(gate.get("passed")) and not healthy:
        reason_code = "SOAK_HEALTH_FAILURES_DETECTED"

    return {
        "passed": passed,
        "reason_code": reason_code,
        "required_wall_hours": float(min_wall_hours),
        "observed_wall_hours": round(float(gate.get("observed_wall_hours") or 0.0), 4),
        "simulated": bool(gate.get("simulated")),
        "evidence_tier": str(gate.get("evidence_tier") or ""),
        "samples_total": int(evidence.get("samples_total") or 0),
        "failures_total": failures,
        "aux_file_leaks_total": aux_leaks,
        "non_200_total": non_200,
        "restart_continuity_verified": bool(evidence.get("restart_continuity_verified")),
        "llm_calls": 0,
    }


def _find_day_proc_stats(soak_root: Path) -> dict[str, Any]:
    root_str = str(_instance_root(soak_root))
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = p.joinpath("cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            if "nyairo_day.py" not in cmd or root_str not in cmd:
                continue
            pid = int(p.name)
            rss_kb = 0
            for line in p.joinpath("status").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        rss_kb = int(parts[1])
                    break
            fd_count = len(list(p.joinpath("fd").iterdir()))
            ctrl_env = ""
            for entry in p.joinpath("environ").read_bytes().split(b"\x00"):
                if entry.startswith(b"NYAIRO_V02_CONTROL_ROOT="):
                    ctrl_env = entry.split(b"=", 1)[1].decode("utf-8", "replace")
                    break
            return {
                "alive": True,
                "pid": pid,
                "rss_kb": rss_kb,
                "fd_count": fd_count,
                "control_root_env": ctrl_env,
            }
        except OSError:
            continue
    return {"alive": False, "pid": None, "rss_kb": 0, "fd_count": 0, "control_root_env": ""}


def _snapshot_instance(soak_root: Path) -> tuple[dict[str, tuple[int, int]], list[str]]:
    inst = _instance_root(soak_root)
    snap: dict[str, tuple[int, int]] = {}
    aux: list[str] = []
    if not inst.exists():
        return snap, aux
    for p in sorted(inst.rglob("*")):
        rel = str(p.relative_to(inst))
        try:
            st = p.stat()
        except OSError:
            continue
        snap[rel] = (st.st_size, int(st.st_mtime_ns))
        if rel.endswith("-wal") or rel.endswith("-shm") or rel.endswith("-journal"):
            aux.append(rel)
    return snap, aux


def _probe_once(soak_root: Path, port: int, expected_ctrl: str) -> dict[str, Any]:
    base = f"http://127.0.0.1:{port}"
    proc_stats = _find_day_proc_stats(soak_root)
    before_snap, _ = _snapshot_instance(soak_root)

    statuses: dict[str, Any] = {}
    receipt = ""
    for route in GET_ROUTES:
        req = urllib.request.Request(base + route, headers={"Origin": base}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                body = resp.read().decode("utf-8", "replace")
                statuses[route] = resp.status
                if route == "/api/phone/v02/runtime-migration":
                    doc = json.loads(body)
                    receipt = str(
                        (((doc.get("data") or {}).get("antipuppet_scorecard") or {}).get("plain_chinese_receipt") or "")
                    )
        except urllib.error.HTTPError as exc:
            statuses[route] = exc.code
        except Exception as exc:
            statuses[route] = f"ERR:{type(exc).__name__}"

    after_snap, after_aux = _snapshot_instance(soak_root)
    diffs = [k for k in sorted(set(before_snap) | set(after_snap)) if before_snap.get(k) != after_snap.get(k)]
    # Ignore background 60s clock tick file if a periodic clock tick overlapped this exact second
    unexpected_diffs = [k for k in diffs if k != "phone/v03_clock_state.json"]
    non_200 = {k: v for k, v in statuses.items() if v != 200}

    # Zero-LLM wake coalescer sanity probe (in-memory only on a fresh poller without fabricating state)
    poller = ale.EventFeedPoller([], day_root=_instance_root(soak_root))
    admitted = poller.coalesce_events(
        [
            {"source": "world", "kind": "ambient", "summary": "soak-probe"},
            {"source": "world", "kind": "ambient", "summary": "soak-probe-dup"},
        ],
        now_iso=_utc_iso(),
    )

    ok = bool(
        proc_stats.get("alive")
        and proc_stats.get("control_root_env") == expected_ctrl
        and not non_200
        and not after_aux
        and not unexpected_diffs
        and bool(receipt)
    )
    return {
        "sampled_at_utc": _utc_iso(),
        "ok": ok,
        "proc": proc_stats,
        "routes_total": len(GET_ROUTES),
        "non_200": non_200,
        "unexpected_diffs": unexpected_diffs,
        "post_aux": after_aux,
        "receipt_non_empty": bool(receipt),
        "coalescer_admitted": len(admitted),
        "coalescer_saved": len(poller.last_coalesced_events),
        "llm_calls": 0,
    }


def _ensure_soak_instance(soak_root: Path, port: int) -> None:
    py = sys.executable
    inst = _instance_root(soak_root)
    auth_db = _authority_root(soak_root) / "authority.db"
    if not (inst / "instance.json").is_file():
        subprocess.run(
            [
                py,
                str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                "init",
                "--root",
                str(inst),
                "--memory-authority",
                str(auth_db),
                "--starter-reading",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        [
            py,
            str(REPO_ROOT / "scripts" / "nyairo_day.py"),
            "start",
            "--root",
            str(inst),
            "--port",
            str(port),
            "--v03-clock-interval",
            "0",
            "--json",
        ],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )


def _restart_soak_instance(soak_root: Path, port: int) -> bool:
    py = sys.executable
    inst = _instance_root(soak_root)
    r_stop = subprocess.run(
        [py, str(REPO_ROOT / "scripts" / "nyairo_day.py"), "stop", "--root", str(inst)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    time.sleep(0.8)
    r_start = subprocess.run(
        [
            py,
            str(REPO_ROOT / "scripts" / "nyairo_day.py"),
            "start",
            "--root",
            str(inst),
            "--port",
            str(port),
            "--v03-clock-interval",
            "0",
            "--json",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    time.sleep(1.5)
    return r_stop.returncode == 0 and r_start.returncode == 0


def _run_monitor_loop(
    soak_root: Path,
    port: int,
    interval_seconds: int,
    target_hours: float,
    restart_check_sample: int,
) -> int:
    stop_flag = {"stop": False}

    def _handle_sig(_sig: int, _frame: Any) -> None:
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    expected_ctrl = str(_authority_root(soak_root) / "v02-control")
    _ensure_soak_instance(soak_root, port)
    time.sleep(1.5)

    started_dt = _utc_now()
    started_mono = time.monotonic()
    evidence: dict[str, Any] = {
        "schema": "nyairo.v02.wall_clock_soak.v1",
        "soak_root": str(soak_root),
        "port": int(port),
        "expected_control_root": expected_ctrl,
        "started_at_utc": _utc_iso(started_dt),
        "last_sample_at_utc": _utc_iso(started_dt),
        "target_wall_hours": float(target_hours),
        "continuous_wall_hours": 0.0,
        "simulated": False,
        "evidence_tier": "WALL_CLOCK_IN_PROGRESS",
        "samples_total": 0,
        "failures_total": 0,
        "aux_file_leaks_total": 0,
        "non_200_total": 0,
        "max_rss_kb": 0,
        "max_fd_count": 0,
        "restart_continuity_verified": False,
        "llm_calls": 0,
        "monitor_pid": os.getpid(),
    }
    _monitor_pid_path(soak_root).write_text(f"{os.getpid()}\n", encoding="utf-8")

    sample_idx = 0
    while not stop_flag["stop"]:
        sample_idx += 1
        if restart_check_sample > 0 and sample_idx == restart_check_sample and not evidence["restart_continuity_verified"]:
            evidence["restart_continuity_verified"] = _restart_soak_instance(soak_root, port)

        sample = _probe_once(soak_root, port, expected_ctrl)
        elapsed_hours = max(0.0, (time.monotonic() - started_mono) / 3600.0)
        evidence["last_sample_at_utc"] = sample["sampled_at_utc"]
        evidence["continuous_wall_hours"] = round(elapsed_hours, 6)
        evidence["samples_total"] = int(evidence["samples_total"]) + 1
        if not sample["ok"]:
            evidence["failures_total"] = int(evidence["failures_total"]) + 1
        evidence["aux_file_leaks_total"] = int(evidence["aux_file_leaks_total"]) + len(sample["post_aux"])
        evidence["non_200_total"] = int(evidence["non_200_total"]) + len(sample["non_200"])
        proc = sample.get("proc") or {}
        evidence["max_rss_kb"] = max(int(evidence["max_rss_kb"]), int(proc.get("rss_kb") or 0))
        evidence["max_fd_count"] = max(int(evidence["max_fd_count"]), int(proc.get("fd_count") or 0))
        evidence["last_sample"] = sample

        if elapsed_hours >= float(target_hours) and int(evidence["failures_total"]) == 0:
            evidence["evidence_tier"] = "WALL_CLOCK_VERIFIED"
        else:
            evidence["evidence_tier"] = "WALL_CLOCK_IN_PROGRESS"

        evidence["gate_verdict"] = evaluate_soak_evidence(evidence, min_wall_hours=target_hours)
        _atomic_write_json(_evidence_path(soak_root), evidence)
        with open(_samples_path(soak_root), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")

        # After first probe, run a fast 10s confirmation probe (sample 2) & restart check (sample 3), then steady interval
        wait_s = 8 if sample_idx < 3 else max(10, int(interval_seconds))
        for _ in range(wait_s):
            if stop_flag["stop"]:
                break
            time.sleep(1.0)

    return 0


def cmd_start(args: argparse.Namespace) -> int:
    soak_root = args.root.expanduser().resolve()
    soak_root.mkdir(parents=True, exist_ok=True)
    if args.foreground:
        return _run_monitor_loop(
            soak_root,
            port=args.port,
            interval_seconds=args.interval_seconds,
            target_hours=args.target_hours,
            restart_check_sample=args.restart_check_sample,
        )

    # Stop any stale monitor on the same root first
    cmd_stop(args, quiet=True)
    log_path = soak_root / "soak_monitor.log"
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "start",
                "--root",
                str(soak_root),
                "--port",
                str(args.port),
                "--interval-seconds",
                str(args.interval_seconds),
                "--target-hours",
                str(args.target_hours),
                "--restart-check-sample",
                str(args.restart_check_sample),
                "--foreground",
            ],
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            start_new_session=True,
        )
    # Wait for initial sample to land
    deadline = time.time() + 30.0
    ev_path = _evidence_path(soak_root)
    while time.time() < deadline:
        if ev_path.is_file():
            try:
                doc = json.loads(ev_path.read_text(encoding="utf-8"))
                if int(doc.get("samples_total") or 0) >= 1:
                    break
            except Exception:
                pass
        time.sleep(0.5)
    print(json.dumps({"ok": True, "monitor_pid": proc.pid, "soak_root": str(soak_root), "port": args.port}, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    soak_root = args.root.expanduser().resolve()
    ev_path = _evidence_path(soak_root)
    if not ev_path.is_file():
        print(json.dumps({"ok": False, "reason_code": "SOAK_NOT_STARTED", "soak_root": str(soak_root)}, ensure_ascii=False))
        return 1
    doc = json.loads(ev_path.read_text(encoding="utf-8"))
    verdict = evaluate_soak_evidence(doc, min_wall_hours=args.target_hours)
    doc["gate_verdict"] = verdict
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    soak_root = args.root.expanduser().resolve()
    ev_path = _evidence_path(soak_root)
    if not ev_path.is_file():
        out = {"passed": False, "reason_code": "SOAK_EVIDENCE_MISSING", "llm_calls": 0}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    doc = json.loads(ev_path.read_text(encoding="utf-8"))
    verdict = evaluate_soak_evidence(doc, min_wall_hours=args.target_hours)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["passed"] else 1


def cmd_stop(args: argparse.Namespace, *, quiet: bool = False) -> int:
    soak_root = args.root.expanduser().resolve()
    pid_path = _monitor_pid_path(soak_root)
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
        except Exception:
            pass
        try:
            pid_path.unlink()
        except OSError:
            pass
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "nyairo_day.py"), "stop", "--root", str(_instance_root(soak_root))],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if not quiet:
        print(json.dumps({"ok": True, "stopped": True, "soak_root": str(soak_root)}, ensure_ascii=False))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Nyairo 0.2 real 24h wall-clock soak runner.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("start", "status", "verify", "stop"):
        sp = sub.add_parser(name)
        sp.add_argument("--root", type=Path, default=DEFAULT_SOAK_ROOT)
        sp.add_argument("--port", type=int, default=DEFAULT_SOAK_PORT)
        sp.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
        sp.add_argument("--target-hours", type=float, default=DEFAULT_TARGET_HOURS)
        sp.add_argument("--restart-check-sample", type=int, default=3)
        if name == "start":
            sp.add_argument("--foreground", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    return cmd_stop(args)


if __name__ == "__main__":
    raise SystemExit(main())
