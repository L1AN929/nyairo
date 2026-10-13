#!/usr/bin/env python3
"""Regenerate MANIFEST.json for one source tree (pre-commit tool).

    python3 scripts/make_manifest.py --root . --version 0.2.0-rc1 \
        --revision R30_V02_RC1 --status SOURCE_REVIEWED_TARGETED_TESTS_PASSED \
        --release-gate "..." --write

Why it exists: MANIFEST.json has to describe the bytes that will actually be
committed, and `scripts/verify_manifest.py` hashes the files on disk.  On a
checkout whose line endings were rewritten (CRLF), those two disagree for every
text file -- so run this against an **LF tree** (e.g. `git archive <commit> | tar
-x -C <dir>`, or a Linux checkout with the repository's `.gitattributes` in
effect).  The release ZIP is built from a commit, which is always LF, and that is
the tree this manifest is meant to match.

What it computes, exactly (same recipe as `MANIFEST.json.tree_recipe`):

* `files`     -- sha256 per file, POSIX relative path, sorted; `MANIFEST.json`
                 itself is excluded because it cannot hash itself;
* `file_count`-- `len(files)`;
* `tree_sha256` -- sha256 of the concatenation of `"<sha>  <path>\\n"` lines,
                 sorted, UTF-8;
* every other top-level key (`schema`, `hermes`, `project_name`, ...) is carried
  over from the existing manifest so a description cannot be silently dropped.

Excluded from the walk, exactly like `verify_manifest.py`: `.git`, any
`__pycache__`, `vendor/hermes/.venv` and `vendor/hermes/venv`, and
`vendor/hermes/*.egg-info`.  Symlinks are recorded, not followed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

VALID_STATUSES = (
    "AUTOMATED_QUALIFIED_EXPERIENCE_CANDIDATE",
    "SOURCE_REVIEWED_TARGETED_TESTS_PASSED",
)


def installed_directory(relative: Path) -> bool:
    parts = relative.parts
    return (parts == (".git",) or "__pycache__" in parts
            or parts[:3] in (("vendor", "hermes", ".venv"), ("vendor", "hermes", "venv"))
            or (len(parts) == 3 and parts[:2] == ("vendor", "hermes")
                and parts[-1].endswith(".egg-info")))


def collect(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        base = Path(folder)
        for directory in list(dirs):
            path = base / directory
            relative = path.relative_to(root)
            if installed_directory(relative):
                dirs.remove(directory)
            elif path.is_symlink():
                files[relative.as_posix()] = hashlib.sha256(
                    os.readlink(path).encode("utf-8")).hexdigest()
                dirs.remove(directory)
        for name in names:
            relative = (base / name).relative_to(root)
            if relative.as_posix() in ("MANIFEST.json", ".git"):
                continue
            files[relative.as_posix()] = hashlib.sha256((base / name).read_bytes()).hexdigest()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", default=None, help="e.g. 0.2.0-rc1 (no leading v)")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--status", default=None, choices=VALID_STATUSES)
    parser.add_argument("--release-gate", default=None)
    parser.add_argument("--write", action="store_true",
                        help="write MANIFEST.json; without it only a summary is printed")
    args = parser.parse_args()

    root = args.root.resolve()
    manifest_path = root / "MANIFEST.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    files = collect(root)
    for key, value in (("version", args.version), ("revision", args.revision),
                       ("status", args.status), ("release_gate", args.release_gate)):
        if value is not None:
            data[key] = value
    data["file_count"] = len(files)
    data["files"] = dict(sorted(files.items()))
    lines = "".join("%s  %s\n" % (sha, name) for name, sha in sorted(files.items()))
    data["tree_sha256"] = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    data["tree_recipe"] = ("SHA256(sorted UTF-8 lines: sha256 + two spaces + POSIX relative path "
                           "+ LF); MANIFEST.json excluded")
    summary = {"root": str(root), "file_count": data["file_count"],
               "tree_sha256": data["tree_sha256"], "version": data.get("version"),
               "revision": data.get("revision"), "status": data.get("status"),
               "wrote": bool(args.write)}
    if args.write:
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
