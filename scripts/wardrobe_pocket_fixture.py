#!/usr/bin/env python3
"""衣柜 / 小列表 / 作品旧版并排：一份全新的本地合成根。

    /tmp/rc104-venv/bin/python -B scripts/wardrobe_pocket_fixture.py --port 3858

这里没有手写的产权账本、作品版本、分享信封或衣柜状态：

* 真实 Supply owner、``one_day_life.prepare``、``WorldGateway``、真实 economy 与真实
  购买全部复用上一弹的 ``workbench_curio_fixture.build_stack``（它自己又复用
  ``window_keepsake_fixture`` 的真实相册/分享/作品构造）；
* 旧版并排的作品用 owner 自己的 ``create_project`` / ``commit_project`` 正常提交：
  v1 正式 prepare + issue grant + send_local，再 commit v2 再正式投递，再 commit 一个
  未寄出的 v3；
* 归档负控用同一套正常写口再建一件作品，投递 v1/v2 后走正式 ``close_project``（归档，
  不是删除、不是 tombstone）；
* 衣柜的「已购但不适用」负控用 ``workbench_curio_fixture`` 已经真实买到的点心实例。

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
from typing import Mapping

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
import workbench_curio_fixture as curio_fixture  # noqa: E402

from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = "chiyo"

REVISIT_TITLE = "海边的旧稿"
REVISIT_V1_TEXT = "第一版：只写到潮水刚退。"
REVISIT_V2_TEXT = "第二版：把潮水退下去之后也写完了。"
REVISIT_V3_TEXT = "第三版：投递之后又改的一句，还没寄出去。"
REVISIT_V1_SHARE = "先看第一版，潮水刚退。"
REVISIT_V2_SHARE = "第二版补完了退潮之后。"
ARCHIVE_TITLE = "已经收进抽屉的稿子"
ARCHIVE_V1_TEXT = "归档稿第一版。"
ARCHIVE_V2_TEXT = "归档稿第二版。"
ARCHIVE_V1_SHARE = "归档稿第一版的分享。"
ARCHIVE_V2_SHARE = "归档稿第二版的分享。"


def _create_work(stack: SimpleNamespace, *, title: str, text: str, key: str) -> tuple[str, str]:
    answer = stack.service.create_project(title=title, content=text, operation_key=key)
    if not answer.get("ok"):
        raise RuntimeError("PROJECT_CREATE_REFUSED: %s" % (answer,))
    data = dict(answer.get("data") or {})
    artifact_id = str(data.get("artifact_id") or "")
    head = str(data.get("head_version_id") or data.get("version_id") or "")
    if not artifact_id or not head:
        raise RuntimeError("PROJECT_CREATE_INCOMPLETE: %s" % (answer,))
    return artifact_id, head


def _commit_work(stack: SimpleNamespace, *, artifact_id: str, head: str, text: str,
                 key: str) -> str:
    answer = stack.service.commit_project(artifact_id=artifact_id, content=text,
                                          expected_head=head, operation_key=key)
    if not answer.get("ok"):
        raise RuntimeError("PROJECT_COMMIT_REFUSED: %s" % (answer,))
    data = dict(answer.get("data") or {})
    next_head = str(data.get("head_version_id") or data.get("version_id") or "")
    if not next_head:
        raise RuntimeError("PROJECT_COMMIT_INCOMPLETE: %s" % (answer,))
    return next_head


def _send_local(stack: SimpleNamespace, *, source_id: str, text: str, key: str) -> str:
    """The previous wave's real prepare → grant → send_local helper, unchanged."""

    return keepsake_fixture._send_local(stack, source_id=source_id, text=text, key=key)


def _seed_revisit_work(stack: SimpleNamespace) -> dict[str, str]:
    """v1 delivered, v2 delivered, v3 committed but never sent."""

    artifact_id, v1 = _create_work(stack, title=REVISIT_TITLE, text=REVISIT_V1_TEXT,
                                   key="wardrobe-revisit-create")
    v1_share = _send_local(stack, source_id=artifact_id, text=REVISIT_V1_SHARE,
                           key="wardrobe-revisit-v1")
    v2 = _commit_work(stack, artifact_id=artifact_id, head=v1, text=REVISIT_V2_TEXT,
                      key="wardrobe-revisit-commit-v2")
    v2_share = _send_local(stack, source_id=artifact_id, text=REVISIT_V2_SHARE,
                           key="wardrobe-revisit-v2")
    v3 = _commit_work(stack, artifact_id=artifact_id, head=v2, text=REVISIT_V3_TEXT,
                      key="wardrobe-revisit-commit-v3")
    return {"artifact_id": artifact_id, "v1": v1, "v2": v2, "v3": v3,
            "v1_share": v1_share, "v2_share": v2_share}


def _seed_archived_work(stack: SimpleNamespace) -> dict[str, str]:
    """A real work with two delivered shares, then formally archived by its owner."""

    artifact_id, v1 = _create_work(stack, title=ARCHIVE_TITLE, text=ARCHIVE_V1_TEXT,
                                   key="wardrobe-archive-create")
    v1_share = _send_local(stack, source_id=artifact_id, text=ARCHIVE_V1_SHARE,
                           key="wardrobe-archive-v1")
    v2 = _commit_work(stack, artifact_id=artifact_id, head=v1, text=ARCHIVE_V2_TEXT,
                      key="wardrobe-archive-commit-v2")
    v2_share = _send_local(stack, source_id=artifact_id, text=ARCHIVE_V2_SHARE,
                           key="wardrobe-archive-v2")
    detail = stack.service.project(artifact_id)
    if not detail.get("ok"):
        raise RuntimeError("ARCHIVE_PROJECT_READ_REFUSED: %s" % (detail,))
    project = detail.get("project") if isinstance(detail.get("project"), Mapping) else {}
    revision = project.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise RuntimeError("ARCHIVE_REVISION_MISSING: %s" % (detail,))
    closed = stack.service.close_project(artifact_id=artifact_id,
                                         expected_revision=revision,
                                         operation_key="wardrobe-archive-close")
    if not closed.get("ok"):
        raise RuntimeError("ARCHIVE_CLOSE_REFUSED: %s" % (closed,))
    return {"artifact_id": artifact_id, "v1": v1, "v2": v2,
            "v1_share": v1_share, "v2_share": v2_share}


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """One fresh root built from the previous waves' real owner constructors."""

    stack = curio_fixture.build_stack(root, port=port, capability=capability)
    try:
        stack.revisit = _seed_revisit_work(stack)
        stack.archived = _seed_archived_work(stack)
        # A real version of a *different* work, used for the forged cross-work binding case.
        stack.other_work_version = str(stack.work_b_head)
        return stack
    except BaseException:
        curio_fixture.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    curio_fixture.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    curio_fixture.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wardrobe_pocket_fixture")
    parser.add_argument("--port", type=int, default=3858)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-wardrobe-pocket-%d-%d" % (
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
        "owned_item_instance_id": stack.purchase["item_instance_id"],
        "owned_item_catalog_item_id": curio_fixture.SNACK_CATALOG_ITEM,
        "revisit": stack.revisit,
        "archived": stack.archived,
        "other_work_artifact_id": stack.work_b,
        "other_work_version_id": stack.other_work_version,
        "endpoints": ["/api/phone/home/wardrobe", "/api/phone/pocket/lists",
                      "/api/phone/pocket/work-revisit"],
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
