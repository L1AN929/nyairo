#!/usr/bin/env python3
"""居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定：一份全新的本地合成根。

    /tmp/rc104-venv/bin/python -B scripts/home_hearth_fixture.py --port 3856

这里没有手写的 World 快照、相册 manifest、分享信封、产权账本、阅读页或今日安排：

* 直接复用上一弹 ``workbench_curio_fixture.build_stack``（它又复用
  ``window_keepsake_fixture`` / ``desk_presence_fixture``）：真实 Supply owner、
  ``one_day_life.prepare``、``WorldGateway``、真实相册导入、两个 owner-clock commit、
  正式 ``share_prepare`` + ``send_local``、真实产权购买与物化、真实阅读停点（第 2 页）；
* 本弹只额外提供 ``buy_another_snack``：用手机自己的正式 ``PhoneShopping.purchase``
  再买一份小点心，证明「新增物件」由真实 owner 造事实，不手写账本。

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

import workbench_curio_fixture as base  # noqa: E402

from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = base.SUBJECT
SNACK_CATALOG_ITEM = base.SNACK_CATALOG_ITEM


def buy_another_snack(stack: SimpleNamespace, operation_key: str) -> str:
    """One more real purchase through the owner's own phone store path (never a fake row)."""

    bought = stack.service.purchase(SNACK_CATALOG_ITEM, operation_key)
    if not bought.get("ok"):
        raise RuntimeError("PURCHASE_REFUSED: %s" % (bought,))
    item_id = str((bought.get("data") or {}).get("item_instance_id") or "")
    if not item_id:
        raise RuntimeError("PURCHASE_NO_INSTANCE: %s" % (bought,))
    return item_id


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """One fresh root from the previous waves' real owner constructors."""

    stack = base.build_stack(root, port=port, capability=capability)
    try:
        stack.reading_page = int(stack.reading["page"])
        stack.reading_pages = int(stack.reading["pages"])
        stack.delivered_share_id = str(stack.keepsakes["pinned_share_id"])
        stack.draft_share_id = str(stack.letters["draft_share_id"])
        stack.purchased_item_id = str(stack.purchase["item_instance_id"])
        return stack
    except BaseException:
        base.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    base.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    base.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="home_hearth_fixture")
    parser.add_argument("--port", type=int, default=3856)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-home-hearth-%d-%d" % (
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
        "reading_page": stack.reading_page,
        "reading_pages": stack.reading_pages,
        "purchased_item_instance_id": stack.purchased_item_id,
        "delivered_share_id": stack.delivered_share_id,
        "draft_share_id": stack.draft_share_id,
        "work_artifact_id": stack.work_a,
        "endpoints": ["/api/phone/home/reunion-anchor", "/api/phone/home/recipes",
                      "/api/phone/home/tentative-plans"],
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
