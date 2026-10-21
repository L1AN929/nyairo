#!/usr/bin/env python3
"""一个带版本历史的手机夹具，供 Edge / 浏览器里点验项目历史链路。

它只做三件事：建一个内存 owner 替身、种一件有三版的作品、起真实的 loopback HTTP 面。
HTTP、四道闸门、前端都是真的；owner 是替身，所以它证明的是“界面链路”，
不是“真实 Supply owner 验收”。

用法::

    python scripts/phone_edge_fixture.py [--port 3816]

然后浏览器打开 http://localhost:<port>/ 。Ctrl+C 结束。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import fixture  # noqa: E402
from test_v04_private_projects_stage import GRANT, VersionListingPort  # noqa: E402


class EdgePort(VersionListingPort):
    """在测试替身上补上 read_version，让“这一版 / 对比”有真数据可读。"""

    def read_version(self, artifact_id, version_id):
        for row in self.versions.get(artifact_id, []):
            if str(row.get("version_id")) == str(version_id):
                body = row.get("content") or ""
                return {
                    "version_id": row.get("version_id"),
                    "artifact_id": artifact_id,
                    "version_number": row.get("version_number"),
                    "parent_version_id": row.get("parent_version_id"),
                    "content": body,
                    "created_at": row.get("created_at"),
                    "content_sha256": None,
                    "content_chars": len(body),
                    "lifecycle": (self.artifacts.get(artifact_id) or {}).get("lifecycle"),
                }
        raise KeyError(version_id)


def seed(port: EdgePort) -> str:
    artifact = "artifact:" + "a" * 32
    port.seed(artifact_id=artifact, title="夜航手记", content="第一版：海风很轻，灯还亮着。")
    head = port.artifacts[artifact]["head_version_id"]
    head = port.commit_version(
        artifact_id=artifact, expected_head=head,
        content="第一版：海风很轻，灯还亮着。\n第二版：她把窗关上，又打开。",
        grant_id=GRANT, operation_key="edge-seed-2").version_id
    port.commit_version(
        artifact_id=artifact, expected_head=head,
        content="第一版：海风很轻，灯还亮着。\n第二版：她把窗关上，又打开。\n第三版：船笛响了一声。",
        grant_id=GRANT, operation_key="edge-seed-3")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3816)
    args = parser.parse_args()
    port = EdgePort()
    seed(port)
    handle = fixture.start_fixture(project_port=port, port=args.port)
    print(json.dumps({"url": handle["url"], "csrf": handle["csrf_token"]}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        handle["close"]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
