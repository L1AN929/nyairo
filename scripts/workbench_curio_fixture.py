#!/usr/bin/env python3
"""创作托盘 / 小物履历 / 三样喜欢之物：一份全新的本地合成根。

    /tmp/rc104-venv/bin/python -B scripts/workbench_curio_fixture.py --port 3855

这里没有手写的 World 快照、相册 manifest、分享信封、产权账本或作品版本历史：

* 真实 Supply owner、``one_day_life.prepare``、``WorldGateway``、worldgen 真实室内家具、
  真实相册导入、两个 owner-clock commit 与正式 ``share_prepare`` + ``send_local``
  全部复用上一弹的 ``window_keepsake_fixture.build_stack``；
* 资料通过 ``ResearchWorkspaceService`` 的正式 ``import_material`` 入口创建；
* 小物通过 World 产权 economy 的正式 ``initialize_genesis`` + ``PhoneShopping.purchase``
  真实购买并在 ``home/bedroom`` 真实物化；交易号/购买时间都来自 owner 自己的账本；
* 相册再加一张真实图片，供托盘的「两张图」路径使用。

零模型、零 secrets、不复用历史 root；根目录与包目录保留，脚本不删除任何文件。
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

import window_keepsake_fixture as keepsake_fixture  # noqa: E402

from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = "chiyo"
SNACK_CATALOG_ITEM = "virtual_snack_wafer"
PURCHASE_KEY = "workbench-curio-purchase-1"

MATERIAL_ONE_TITLE = "海风与旧码头"
MATERIAL_ONE_TEXT = "材料一：港口的旧码头在涨潮时会没进水里。"
MATERIAL_TWO_TITLE = "窗台光照记录"
MATERIAL_TWO_TEXT = "材料二：朝南窗台下午三点的光最长。"
SECOND_PICTURE_CAPTION = "码头边的傍晚"

#: The real 1x1 PNG the previous wave already proves the owner accepts.
PNG_BASE64 = keepsake_fixture.PNG_BASE64


def _import_material(stack: SimpleNamespace, *, title: str, text: str, key: str) -> str:
    answer = stack.service.import_material(
        title=title, text=text, source_kind="user_supplied",
        origin="workbench-curio-fixture", operation_key=key)
    if not answer.get("ok"):
        raise RuntimeError("MATERIAL_IMPORT_REFUSED: %s" % (answer,))
    material = (answer.get("data") or {}).get("material") or {}
    artifact_id = str(material.get("artifact_id") or "")
    if not artifact_id:
        raise RuntimeError("MATERIAL_IMPORT_NO_ID: %s" % (answer,))
    return artifact_id


def _import_second_picture(stack: SimpleNamespace) -> str:
    answer = stack.service.album_import(
        caption=SECOND_PICTURE_CAPTION, content_type="image/png", data=PNG_BASE64,
        operation_key="workbench-curio-picture-2")
    if not answer.get("ok"):
        raise RuntimeError("ALBUM_IMPORT_REFUSED: %s" % (answer,))
    return str((answer.get("data") or {}).get("artifact_id") or "")


def _seed_economy_purchase(stack: SimpleNamespace) -> dict:
    """Initialize the real economy and buy one instance through the phone store."""

    import world_property_economy as economy

    world_home = Path(str(stack.data["world_home"]))
    environment = dict(stack.service.environment)
    genesis = economy.initialize_genesis(hermes_home=world_home, environment=environment)
    if genesis.get("status") not in ("created", "already_initialized"):
        raise RuntimeError("ECONOMY_GENESIS_REFUSED: %s" % (genesis,))
    bought = stack.service.purchase(SNACK_CATALOG_ITEM, PURCHASE_KEY)
    if not bought.get("ok"):
        raise RuntimeError("PURCHASE_REFUSED: %s" % (bought,))
    data = dict(bought.get("data") or {})
    item_id = str(data.get("item_instance_id") or "")
    if not item_id:
        raise RuntimeError("PURCHASE_NO_INSTANCE: %s" % (bought,))
    return {"item_instance_id": item_id,
            "transaction_id": data.get("transaction_id"),
            "purchase_intent_id": data.get("purchase_intent_id"),
            "materialization": data.get("materialization"),
            "placement": data.get("placement")}


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """One fresh root built from the previous wave's real owner constructors."""

    stack = keepsake_fixture.build_stack(root, port=port, capability=capability)
    try:
        # The phone facade reads this instance's own environment; this is the same switch
        # the owner uses in production (no second configuration source).
        stack.service.environment["CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED"] = "1"
        stack.materials = {
            "one": _import_material(stack, title=MATERIAL_ONE_TITLE,
                                    text=MATERIAL_ONE_TEXT,
                                    key="workbench-curio-material-1"),
            "two": _import_material(stack, title=MATERIAL_TWO_TITLE,
                                    text=MATERIAL_TWO_TEXT,
                                    key="workbench-curio-material-2"),
        }
        stack.pictures = {
            "one": str(stack.keepsakes["album_artifact_id"]),
            "two": _import_second_picture(stack),
        }
        stack.purchase = _seed_economy_purchase(stack)
        stack.delivered_share_id = str(stack.keepsakes["pinned_share_id"])
        stack.draft_share_id = str(stack.letters["draft_share_id"])
        return stack
    except BaseException:
        keepsake_fixture.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    keepsake_fixture.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    keepsake_fixture.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="workbench_curio_fixture")
    parser.add_argument("--port", type=int, default=3855)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-workbench-curio-%d-%d" % (
                os.getpid(), time.time_ns())))
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        parser.error("PORT_BIND_FAILED")
    sys.stdout.write(json.dumps({
        "ok": True, "root": str(stack.root), "port": stack.port_number,
        "capability_issued": stack.capability is not None,
        "capability_path": str(phone_capability.capability_path(stack.root)),
        "world_package": str(stack.package),
        "work_artifact_id": stack.work_a,
        "work_head_version_id": str(
            ((stack.service.project(stack.work_a).get("project") or {})
             .get("head_version_id")) or ""),
        "materials": stack.materials,
        "pictures": stack.pictures,
        "purchased_item_instance_id": stack.purchase["item_instance_id"],
        "purchase_transaction_id": stack.purchase["transaction_id"],
        "delivered_share_id": stack.keepsakes["pinned_share_id"],
        "draft_share_id": stack.letters["draft_share_id"],
        "endpoints": ["/api/phone/home/workbench", "/api/phone/home/curios",
                      "/api/phone/home/trio-exchange"],
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
