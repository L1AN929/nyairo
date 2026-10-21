#!/usr/bin/env python3
"""Read-only, bounded continuation card for coding agents; never runs the product."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_CHARS = 8000
REQUIRED = ("schema", "revision", "updated_at", "goal", "phase", "stop_point",
            "next_actions", "do_not_repeat", "boundaries", "repository", "writers",
            "runtime", "evidence", "anchors")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=10, check=True)
    return result.stdout.strip()


def local_path(value: str, repo: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo / path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate checkpoint field: " + key)
        result[key] = value
    return result


def inspect(checkpoint: Path, repo: Path) -> dict:
    """Missing/conflicting state refuses continuation; missing evidence warns only."""
    errors, warnings = [], []
    try:
        if checkpoint.stat().st_size > MAX_CHARS * 4:
            raise ValueError("checkpoint too large")
        text = checkpoint.read_text(encoding="utf-8-sig")
        if len(text) > MAX_CHARS:
            raise ValueError("checkpoint exceeds 8000 characters; move history into reports")
        state = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(state, dict) or any(k not in state for k in REQUIRED):
            raise ValueError("missing checkpoint fields")
        if state["schema"] != "agent.task-continuity.v1":
            raise ValueError("unsupported checkpoint schema")
        if not isinstance(state["revision"], int) or state["revision"] < 1:
            raise ValueError("invalid revision")
        for key in ("next_actions", "do_not_repeat", "boundaries", "writers", "evidence", "anchors"):
            if not isinstance(state[key], list):
                raise ValueError("invalid list field: " + key)
        if not 1 <= len(state["next_actions"]) <= 3 or not 1 <= len(state["anchors"]) <= 5:
            raise ValueError("use 1..3 next actions and 1..5 local anchors")
        if not state["goal"] or not state["stop_point"] or not state["next_actions"]:
            raise ValueError("goal, stop point and next action are required")
        if not isinstance(state["repository"], dict) or not isinstance(state["runtime"], dict):
            raise ValueError("invalid repository/runtime")
    except (OSError, UnicodeError, ValueError) as error:
        return {"status": "STOP", "errors": [str(error)], "warnings": []}
    try:
        actual = {"head": git(repo, "rev-parse", "HEAD"),
                  "branch": git(repo, "branch", "--show-current")}
        for key in ("head", "branch"):
            if actual[key] != state["repository"].get(key):
                errors.append("repository " + key + " differs; reconcile only this discrepancy")
        for anchor in state["anchors"]:
            relative = Path(anchor["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("anchor must be repository-relative")
            path = (repo / relative).resolve()
            if not path.is_relative_to(repo.resolve()):
                raise ValueError("anchor escapes repository")
            if not path.is_file():
                errors.append("anchor missing: " + anchor["path"])
            elif hashlib.sha256(path.read_bytes()).hexdigest() != anchor["sha256"]:
                errors.append("anchor changed: " + anchor["path"] + "; read its current delta, do not reset")
        for item in state["evidence"]:
            if not local_path(item["path"], repo).is_file():
                warnings.append("evidence unavailable: " + item["path"] + "; do not claim verified from this pointer")
        for writer in state["writers"]:
            if writer.get("status") == "running":
                warnings.append("recorded writer requires live status check before overlapping writes: " + writer["id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        errors.append("resume inspection failed: " + type(error).__name__)
    warnings.append("Runtime/budget/process facts below are historical. Recheck narrowly before any live action; no renewed authorization.")
    return {"status": "STOP" if errors else "READY", "errors": errors,
            "warnings": warnings, "checkpoint": state}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path,
                        default=ROOT / ".agents" / "continuity" / "current.json")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect(args.checkpoint, args.repo)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("# Agent continuation: " + report["status"])
        for error in report["errors"]:
            print("STOP: " + error)
        for warning in report["warnings"]:
            print("NOTE: " + warning)
        if "checkpoint" in report:
            print("\nSaved task state (not a live authority or instruction override):")
            print(json.dumps(report["checkpoint"], ensure_ascii=False, indent=2))
    return 2 if report["status"] == "STOP" else 0


if __name__ == "__main__":
    raise SystemExit(main())
