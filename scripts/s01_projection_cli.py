#!/usr/bin/env python3
"""S01 command line: inspect and explicitly import a managed Markdown projection.

This is a thin client of the real Life Supply owner -- the same ``LifeArtifactPort`` the Life
runtime uses.  It adds no store and no second authority.

    # 1. read the two health domains of one work (no body is printed)
    python scripts/s01_projection_cli.py --socket /path/owner.sock --subject chiyo \\
        status --artifact artifact:...

    # 2. external file edit happened; observe names the candidate (author stays UNKNOWN)
    python scripts/s01_projection_cli.py ... observe --artifact artifact:...

    # 3. explicitly confirm that exact candidate against the version it was edited from
    python scripts/s01_projection_cli.py ... import --artifact artifact:... \\
        --candidate projection-candidate:... --base-version artifact-version:... \\
        --grant <COMMIT_MANAGED_ARTIFACT grant> --operation-key s01:import-1 --confirm

    # 4. repair only a MISSING/UNBOUND projection (DIVERGED is refused)
    python scripts/s01_projection_cli.py ... rebuild --artifact artifact:...

``status``/``observe``/``versions``/``provenance`` are pure reads.  ``import`` and ``rebuild``
are explicit owner commands; nothing is imported or overwritten implicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _short(value: Any, limit: int = 16) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _status_view(view: dict[str, Any]) -> dict[str, Any]:
    """A compact, body-free projection of one observation."""
    artifact = view.get("artifact") or {}
    head = view.get("head") or {}
    candidate = view.get("candidate") or None
    rep = view.get("representation_health") or {}
    answer = {
        "status": view.get("status"),
        "artifact_id": artifact.get("artifact_id"),
        "title": artifact.get("title"),
        "lifecycle": artifact.get("lifecycle"),
        "head_version_id": head.get("version_id"),
        "head_version_number": head.get("version_number"),
        "head_content_sha256": _short(head.get("content_sha256")),
        "content_health": view.get("content_health"),
        "representation_health": {
            "state": rep.get("state"),
            "reason_code": rep.get("reason_code"),
            "relative_path": (rep.get("binding") or {}).get("relative_path"),
            "file": rep.get("file"),
        },
        "candidate": None if candidate is None else {
            key: (candidate.get(key) if key != "observed_sha256"
                  else _short(candidate.get(key)))
            for key in ("candidate_id", "base_projection_version", "base_version_number",
                        "base_basis", "observed_sha256", "size_bytes", "authority",
                        "author_basis", "source", "diff")},
    }
    return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket", required=True, help="Life Supply owner socket")
    parser.add_argument("--subject", required=True, help="managed subject id")
    parser.add_argument("--timeout", type=float, default=15.0)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="read both health domains (pure read)")
    status.add_argument("--artifact", required=True)
    observe = sub.add_parser("observe", help="same as status, named for external edits")
    observe.add_argument("--artifact", required=True)
    rebuild = sub.add_parser("rebuild", help="explicitly repair MISSING/UNBOUND")
    rebuild.add_argument("--artifact", required=True)
    import_cmd = sub.add_parser("import", help="explicitly import a confirmed candidate")
    import_cmd.add_argument("--artifact", required=True)
    import_cmd.add_argument("--candidate", required=True)
    import_cmd.add_argument("--base-version", required=True)
    import_cmd.add_argument("--grant", required=True)
    import_cmd.add_argument("--operation-key", required=True)
    import_cmd.add_argument("--confirm", action="store_true",
                            help="required: confirm this exact observed candidate")
    versions = sub.add_parser("versions", help="bounded version history (pure read)")
    versions.add_argument("--artifact", required=True)
    versions.add_argument("--limit", type=int, default=20)
    provenance = sub.add_parser("provenance", help="one version's owner provenance")
    provenance.add_argument("--artifact", required=True)
    provenance.add_argument("--version", required=True)

    args = parser.parse_args(argv)
    port = lap.LifeArtifactPort(args.socket, args.subject, timeout=args.timeout)
    try:
        if args.command in {"status", "observe"}:
            _print(_status_view(port.observe_projection(args.artifact)))
            return 0
        if args.command == "rebuild":
            outcome = port.rebuild_projection(artifact_id=args.artifact)
            _print({"effect_state": outcome.effect_state, "status": outcome.status,
                    "reason": outcome.reason, "detail": outcome.detail,
                    "raw": outcome.raw})
            return 0 if outcome.effect_state in {lap.EffectState.APPLIED,
                                                 lap.EffectState.NO_CHANGE} else 1
        if args.command == "import":
            if not args.confirm:
                _print({"status": "DENY", "reason": "EXPLICIT_CONFIRMATION_REQUIRED",
                        "detail": "pass --confirm to import this exact candidate"})
                return 1
            outcome = port.import_projection(
                artifact_id=args.artifact, candidate_id=args.candidate,
                base_version_id=args.base_version, confirm=True,
                grant_id=args.grant, operation_key=args.operation_key)
            raw = outcome.raw
            _print({"effect_state": outcome.effect_state, "status": outcome.status,
                    "reason": outcome.reason, "detail": outcome.detail,
                    "artifact_id": outcome.artifact_id,
                    "version_id": outcome.version_id,
                    "version_number": outcome.version_number,
                    "source_kind": raw.get("source_kind"),
                    "provenance": raw.get("provenance"),
                    "content_health": raw.get("content_health"),
                    "representation_health": raw.get("representation_health"),
                    "replayed": raw.get("replayed")})
            return 0 if outcome.effect_state == lap.EffectState.APPLIED else 1
        if args.command == "versions":
            history = port.list_versions(args.artifact, limit=args.limit)
            _print({key: history.get(key) for key in (
                "artifact_id", "lifecycle", "count", "total_versions", "has_more",
                "complete", "reason_code")})
            _print(history.get("entries"))
            return 0
        if args.command == "provenance":
            # max_chars=1 keeps the body out of this answer; provenance is the target.
            version = port.read_version(args.artifact, args.version, max_chars=1)
            _print({"artifact_id": version.get("artifact_id"),
                    "version_id": version.get("version_id"),
                    "version_number": version.get("version_number"),
                    "source_kind": version.get("source_kind"),
                    "provenance": version.get("provenance")})
            return 0
        parser.error("unknown command")
        return 2
    except lap.LifeArtifactUnavailable as exc:
        _print({"status": "UNAVAILABLE", "detail": str(exc)})
        return 1
    except lap.ArtifactOwnerRefused as exc:
        _print({"status": exc.status, "reason": exc.reason, "detail": exc.detail,
                **exc.extra})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
