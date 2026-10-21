#!/usr/bin/env python3
"""``python -m components.phone`` == ``python scripts/phone.py``（同一个入口，不做第二套逻辑）。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import phone as phone_cli  # noqa: E402  (scripts/phone.py)


def main(argv: list[str] | None = None) -> int:
    return phone_cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
