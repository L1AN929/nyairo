#!/usr/bin/env python3
"""房间外六项日常切片：一份全新的本地合成根（零重复构造）。

    /tmp/rc104-venv/bin/python -B scripts/outing_places_fixture.py --port 3859

直接复用 ``wardrobe_pocket_fixture.build_stack`` 已经准备好的真实 owner：
* 真实 ``coastal_town`` World（含 ``beach`` / ``bookstore`` / ``cafe`` / ``flower_shop`` 等非 ``home`` 地点）；
* 真实 ``world_property_economy`` 虚拟币创世与点心购买（真实余额可读）；
* 真实 ``PhoneAlbum`` 导入图片（``album_artifact_id``）；
* 真实 ``PrivateProjectService`` 多版本作品（v1/v2 已本地投递分享，v3 未投递）与已归档作品。

零模型、零 secrets、不触碰任何历史 root，脚本不删除任何文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wardrobe_pocket_fixture as pocket_fixture  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = pocket_fixture.SUBJECT
REVISIT_TITLE = pocket_fixture.REVISIT_TITLE
REVISIT_V1_TEXT = pocket_fixture.REVISIT_V1_TEXT
REVISIT_V2_TEXT = pocket_fixture.REVISIT_V2_TEXT
REVISIT_V3_TEXT = pocket_fixture.REVISIT_V3_TEXT
REVISIT_V1_SHARE = pocket_fixture.REVISIT_V1_SHARE
REVISIT_V2_SHARE = pocket_fixture.REVISIT_V2_SHARE
ARCHIVE_TITLE = pocket_fixture.ARCHIVE_TITLE
ARCHIVE_V1_TEXT = pocket_fixture.ARCHIVE_V1_TEXT
ARCHIVE_V2_TEXT = pocket_fixture.ARCHIVE_V2_TEXT
ARCHIVE_V1_SHARE = pocket_fixture.ARCHIVE_V1_SHARE
ARCHIVE_V2_SHARE = pocket_fixture.ARCHIVE_V2_SHARE


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """Reuse `wardrobe_pocket_fixture.build_stack` and expose discovered outdoor IDs."""

    stack = pocket_fixture.build_stack(root, port=port, capability=capability)
    try:
        world_ctx = stack.service._outing_world_context()
        places = world_ctx["places"]
        stack.outdoor_place_ids = [
            row["place_id"] for row in (places.get("outdoor_places") or [])
        ]
        stack.bench_candidate_ids = [
            row["place_id"] for row in (places.get("bench_candidates") or [])
        ]
        stack.bookstore_place_id = places.get("bookstore_place_id")
        stack.album_artifact_id = str(stack.keepsakes["album_artifact_id"])
        return stack
    except BaseException:
        pocket_fixture.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    pocket_fixture.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    pocket_fixture.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="outing_places_fixture")
    parser.add_argument("--port", type=int, default=3859)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-outing-places-%d-%d" % (
                os.getpid(), time.time_ns())))
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        parser.error("PORT_BIND_FAILED")
    sys.stdout.write(json.dumps({
        "ok": True,
        "root": str(stack.root),
        "port": stack.port_number,
        "capability_issued": stack.capability is not None,
        "capability_path": str(phone_capability.capability_path(stack.root)),
        "world_package": str(stack.package),
        "outdoor_place_ids": stack.outdoor_place_ids,
        "bench_candidate_ids": stack.bench_candidate_ids,
        "bookstore_place_id": stack.bookstore_place_id,
        "album_artifact_id": stack.album_artifact_id,
        "revisit": stack.revisit,
        "archived": stack.archived,
        "other_work_artifact_id": stack.work_b,
        "other_work_version_id": stack.other_work_version,
        "endpoints": [
            "/api/phone/outing/bench-bookstore",
            "/api/phone/outing/routes-market",
            "/api/phone/outing/gallery-blueprint",
        ],
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    serve_stack(stack)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        close_stack(stack)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
