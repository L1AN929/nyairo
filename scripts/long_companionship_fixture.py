#!/usr/bin/env python3
"""长程陪伴六项日常切片：一份全新的本地合成根（零重复构造）。

    /tmp/rc104-venv/bin/python -B scripts/long_companionship_fixture.py --port 3860

直接复用 ``outing_places_fixture.build_stack`` 已经准备好的真实 owner：
* 真实 ``coastal_town`` World（含家中家具、室内盆栽与户外地点）；
* 真实 ``world_property_economy`` 虚拟币创世与拥有物品；
* 真实 ``PhoneReading`` 阅读书签、``PhoneAlbum`` 相册图片；
* 真实 ``PrivateProjectService`` 多版本作品（``revisit`` v1/v2 已投递、v3 未投递）与已归档作品；
* 在此基础上通过 ``create_derived_project`` 新增两件源自 ``(revisit.artifact_id, revisit.v1)`` 的真实
  ``derived`` 重画/改写作品：一件已完成 ``LOCAL_DELIVERED`` 本地分享，另一件保持未投递私密稿，
  供 ``memory_lantern`` 双层灯光在 ``subject`` 与 ``partner_default`` 两视角下真实核验。

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
from typing import Any, Mapping

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

import outing_places_fixture as outing_fixture  # noqa: E402
import wardrobe_pocket_fixture as pocket_fixture  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402

DERIVED_SHARED_TITLE = "一年后重画：雨停后的海堤灯影"
DERIVED_SHARED_TEXT = "把去年第一版里急促的线条放慢了，补上傍晚潮水退去后石阶上的暖色反光。"
DERIVED_SHARED_EXCERPT = "把去年那幅海堤速写重新画了一遍，这次把石阶上的灯影画得更安静了。"

DERIVED_PRIVATE_TITLE = "一年后私密改写：未公开的背面草稿"
DERIVED_PRIVATE_TEXT = "只留给自己看的笔触练习，记录当时还没想明白的构图留白。"


def _extract_project_info(answer: Mapping[str, Any]) -> tuple[str, str]:
    if not answer.get("ok"):
        raise RuntimeError("DERIVED_PROJECT_CREATE_REFUSED: %s" % (answer,))
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    proj = (
        data.get("project") if isinstance(data.get("project"), Mapping)
        else (answer.get("project") if isinstance(answer.get("project"), Mapping) else data)
    )
    artifact_id = str(proj.get("artifact_id") or data.get("artifact_id") or "")
    head_version = str(
        proj.get("head_version_id") or data.get("head_version_id") or proj.get("version_id") or ""
    )
    if not artifact_id or not head_version:
        raise RuntimeError("DERIVED_PROJECT_IDS_MISSING: %s" % (answer,))
    return artifact_id, head_version


def _seed_derived_lineage(stack: SimpleNamespace) -> dict[str, Any]:
    src_art = str(stack.revisit["artifact_id"])
    src_v1 = str(stack.revisit["v1"])

    ans_shared = stack.service.create_derived_project(
        title=DERIVED_SHARED_TITLE,
        content=DERIVED_SHARED_TEXT,
        source_artifact_id=src_art,
        source_version_id=src_v1,
        purpose="adapt",
        note="一年后按新节奏重画第一版",
        operation_key="longterm-derived-shared-v1",
    )
    shared_art, shared_v1 = _extract_project_info(ans_shared)
    shared_share_id = pocket_fixture._send_local(
        stack,
        source_id=shared_art,
        text=DERIVED_SHARED_EXCERPT,
        key="longterm-derived-shared-send-v1",
    )

    ans_priv = stack.service.create_derived_project(
        title=DERIVED_PRIVATE_TITLE,
        content=DERIVED_PRIVATE_TEXT,
        source_artifact_id=src_art,
        source_version_id=src_v1,
        purpose="quote",
        note="留在手账背面的未公开练习",
        operation_key="longterm-derived-private-v1",
    )
    priv_art, priv_v1 = _extract_project_info(ans_priv)

    return {
        "source_artifact_id": src_art,
        "source_version_id": src_v1,
        "shared_remake": {
            "artifact_id": shared_art,
            "v1": shared_v1,
            "share_id": shared_share_id,
        },
        "private_remake": {
            "artifact_id": priv_art,
            "v1": priv_v1,
        },
    }


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """Reuse `outing_places_fixture.build_stack` and seed real derived lineage works."""

    stack = outing_fixture.build_stack(root, port=port, capability=capability)
    try:
        room_ctx = stack.service._longterm_home_room_context()
        stack.home_plants = dict(room_ctx["plants_by_id"])
        stack.home_plant_ids = list(stack.home_plants.keys())
        stack.derived_lineage = _seed_derived_lineage(stack)
        return stack
    except BaseException:
        outing_fixture.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    outing_fixture.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    outing_fixture.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="long_companionship_fixture")
    parser.add_argument("--port", type=int, default=3860)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-long-companionship-%d-%d" % (
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
        "home_plant_ids": stack.home_plant_ids,
        "album_artifact_id": stack.album_artifact_id,
        "revisit": stack.revisit,
        "archived": stack.archived,
        "derived_lineage": stack.derived_lineage,
        "endpoints": [
            "/api/phone/longterm/first-room-habits",
            "/api/phone/longterm/gifts-lantern",
            "/api/phone/longterm/resume-hub",
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
