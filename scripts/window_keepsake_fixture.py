#!/usr/bin/env python3
"""窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信：一份全新的本地合成根。

    /tmp/rc104-venv/bin/python -B scripts/window_keepsake_fixture.py --port 3854

这里没有手写的 World 快照、相册 manifest、分享信封或作品版本历史：

* 真实 Supply owner、``one_day_life.prepare``、``WorldGateway``、真实 ``PhoneReading``
  由既有 ``desk_presence_fixture.build_stack`` 构造；
* 世界包先由 worldgen 自己的 ``generate_world`` 生成，再由它自己的
  ``apply_interiors_to_package`` 给 home 生成室内家具（含卧室真实 plant 对象），
  然后交给 ``prepare(world_package=...)``；
* 窗向/窗景/院子座位走 ``world_home_space.customize_home_detail`` 的真实写口；
* 分日作品用 owner 自己的 ``clock`` 注入分别落两个真实 commit，再走正式
  ``share_prepare`` + ``share_action(send_local)``；
* 冰箱贴/锁屏照片走真实 ``album_import``，锁屏选择与 pin 都走新的 HTTP 写口。

零模型、零 secrets、不复用历史 root；根目录与包目录保留，脚本不删除任何文件。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

import desk_presence_fixture as desk_fixture  # noqa: E402
import one_day_life as odl  # noqa: E402

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "chiyo"
WORLD_SEED = 929711
WORLD_THEME = "coastal_town"
#: Seed 1 deterministically places a bedroom plant + bedside lamp in the procedural home.
INTERIOR_SEED = 1

LETTER_TITLE = "分日慢慢写的信"
LETTER_DAY1_TEXT = "十一月十日。今天只想先写一个开头。"
LETTER_DAY2_TEXT = "十一月十一日。把昨天那句接着写完了。"
LETTER_DAY3_TEXT = "十一月十二日。投递之后又改的一句，不该算进那封信。"
LETTER_SHARE_TEXT = "写好了一点，先放在这里。"
LETTER_DRAFT_TEXT = "还想再改一句，先别发。"
PIN_SHARE_TEXT = "冰箱上那张：今天海风很凉。"
PICTURE_CAPTION = "窗边的光"

WINDOW_ORIENTATION = "south"
WINDOW_VIEW = "能看到港口方向的海"
YARD_SEATING = "一张旧木长椅"

#: A real 1x1 PNG: the owner's own signature/IHDR/IEND checks accept it.
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)

TZ8 = timezone(timedelta(hours=8))
DAY1 = datetime(2026, 11, 10, 20, 0, tzinfo=TZ8)
DAY2 = datetime(2026, 11, 11, 9, 0, tzinfo=TZ8)
DAY3 = datetime(2026, 11, 12, 9, 0, tzinfo=TZ8)


def package_dir_for(root: Path) -> Path:
    return root.parent / (root.name + "-world-package")


def _prepare_world_package(root: Path) -> Path:
    """Generate a real package and let worldgen's own interior pass place home furniture."""

    from components.worldgen import schema
    from components.worldgen.generator import generate_world
    from components.worldgen.interior import apply_interiors_to_package

    package = package_dir_for(root)
    if package.exists() and any(package.iterdir()):
        raise RuntimeError("PACKAGE_EXISTS: pass a fresh root")
    package.mkdir(parents=True, exist_ok=True)
    docs = generate_world(seed=WORLD_SEED, theme=WORLD_THEME)
    schema.write_package(package, docs)
    apply_interiors_to_package(package, locations=["home"], seed_override=INTERIOR_SEED)
    return package


@contextmanager
def _prepare_with_package(package: Path):
    """``odl.prepare`` is the normal constructor; only its existing ``world_package`` is pinned."""

    original = odl.prepare

    def patched(**kwargs):
        kwargs.setdefault("world_package", str(package))
        return original(**kwargs)

    odl.prepare = patched
    try:
        yield
    finally:
        odl.prepare = original


def _enable_features(stack: SimpleNamespace) -> None:
    # The service's own environment is what the World facade / ambient / home-space read.
    stack.service.environment["CHIYO_WORLD_HOME_SPACE_ENABLED"] = "1"
    stack.service.environment["CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"] = "1"
    # Deterministic World-local day whose rotating template set really includes the home
    # window sound.  This is the owner's own existing clock injection, not a fake event.
    stack.service._world_clock = lambda: datetime(2026, 11, 14, 12, 0, tzinfo=timezone(
        timedelta(hours=9)))


def _seed_home_details(stack: SimpleNamespace) -> None:
    from world_home_space import customize_home_detail

    environment = dict(stack.service.environment)
    chosen = datetime(2026, 11, 11, 8, 30, tzinfo=TZ8)
    for field, value in (
        ("bedroom.window_orientation", WINDOW_ORIENTATION),
        ("bedroom.window_view", WINDOW_VIEW),
        ("courtyard.seating", YARD_SEATING),
    ):
        answer = customize_home_detail(
            field=field, value=value, main_self_verified=True,
            source_turn_id="window-keepsake-fixture", chosen_at=chosen,
            hermes_home=stack.data["world_home"], environment=environment)
        if answer.get("status") != "applied":
            raise RuntimeError("HOME_DETAIL_REFUSED: %s" % (answer,))


def _with_owner_clock(supply, moment: datetime, call):
    """Use the owner's *existing* clock injection for two real commits; restore afterwards."""

    epoch = float(moment.timestamp())
    previous_service_clock = supply.clock
    store = getattr(supply, "artifact", None)
    previous_store_clock = getattr(store, "clock", None)
    supply.clock = lambda: epoch
    if store is not None:
        store.clock = lambda: epoch
    try:
        return call()
    finally:
        supply.clock = previous_service_clock
        if store is not None and previous_store_clock is not None:
            store.clock = previous_store_clock


def _supply_call(socket_path: Path | str, request: dict) -> dict:
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30.0)
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        return {"status": "UNAVAILABLE", "reason": "EMPTY_RESPONSE"}
    try:
        answer = json.loads(line.decode("utf-8"))
    except (UnicodeError, ValueError):
        return {"status": "UNAVAILABLE", "reason": "MALFORMED_RESPONSE"}
    return answer if isinstance(answer, dict) else {"status": "UNAVAILABLE"}


def _issue_disclosure_grant(stack: SimpleNamespace, *, source_id: str, version_id: str,
                            operation_id: str) -> str:
    """Issue the real local-owner permit the owner checks inside the send transaction."""

    from lifesupply.governance.authority import (
        DISCLOSE_CAPABILITY,
        DISCLOSURE_TARGET_LOCAL_OWNER,
        disclosure_scope,
    )

    answer = _supply_call(stack.services.supply_path, {
        "op": "issue_grant",
        "grant": {
            "subject": SUBJECT,
            "capability": DISCLOSE_CAPABILITY,
            "scope": disclosure_scope(source_id, version_id),
            "target": DISCLOSURE_TARGET_LOCAL_OWNER,
            "operation_id": operation_id,
            "use_mode": "ONE_SHOT",
            "max_uses": 1,
        },
    })
    if answer.get("status") != "ALLOW":
        raise RuntimeError("DISCLOSURE_GRANT_REFUSED: %s" % (answer,))
    return str(answer.get("grant_id") or "")


def _send_local(stack: SimpleNamespace, *, source_id: str, text: str, key: str) -> str:
    service = stack.service
    prepared = service.share_prepare(source_artifact_id=source_id, share_text=text,
                                     operation_key=key + "-prepare")
    if not prepared.get("ok"):
        raise RuntimeError("SHARE_PREPARE_REFUSED: %s" % (prepared,))
    share_id = str(prepared["data"]["share_id"])
    detail = service.share(share_id)
    if not detail.get("ok"):
        raise RuntimeError("SHARE_DETAIL_REFUSED: %s" % (detail,))
    share = detail["data"]["share"]
    head = str(share["head_version_id"])
    source = share.get("source") if isinstance(share.get("source"), Mapping) else {}
    source_version = str(source.get("version_id") or "")
    if not source_version:
        raise RuntimeError("SHARE_SOURCE_VERSION_MISSING: %s" % (share,))
    _issue_disclosure_grant(stack, source_id=source_id, version_id=source_version,
                            operation_id=key + "-grant")
    acted = service.share_action(share_id=share_id, action="send_local",
                                 expected_head=head, operation_key=key + "-send")
    if not acted.get("ok"):
        raise RuntimeError("SHARE_SEND_REFUSED: %s" % (acted,))
    return share_id


def _seed_letter_work(stack: SimpleNamespace) -> dict:
    """One work really committed on two owner-clock days, then a third *after* the shares."""

    supply = stack.services.service
    created = _with_owner_clock(supply, DAY1, lambda: stack.service.create_project(
        title=LETTER_TITLE, content=LETTER_DAY1_TEXT,
        operation_key="window-letter-create"))
    if not created.get("ok"):
        raise RuntimeError("LETTER_CREATE_REFUSED: %s" % (created,))
    artifact_id = str(created["data"]["artifact_id"])
    head = str(created["data"].get("head_version_id") or created["data"].get("version_id") or "")
    committed = _with_owner_clock(supply, DAY2, lambda: stack.service.commit_project(
        artifact_id=artifact_id, content=LETTER_DAY2_TEXT, expected_head=head,
        operation_key="window-letter-commit-day2"))
    if not committed.get("ok"):
        raise RuntimeError("LETTER_COMMIT_REFUSED: %s" % (committed,))

    delivered = _send_local(stack, source_id=artifact_id,
                            text=LETTER_SHARE_TEXT, key="window-letter-delivered")
    prepared = stack.service.share_prepare(
        source_artifact_id=artifact_id, share_text=LETTER_DRAFT_TEXT,
        operation_key="window-letter-draft-prepare")
    if not prepared.get("ok"):
        raise RuntimeError("LETTER_DRAFT_REFUSED: %s" % (prepared,))
    draft_id = str(prepared["data"]["share_id"])

    # Deliberately *after* both shares: the letters projection must stop at the pinned version.
    head2 = str(committed["data"].get("head_version_id") or committed["data"].get("version_id") or "")
    third = _with_owner_clock(supply, DAY3, lambda: stack.service.commit_project(
        artifact_id=artifact_id, content=LETTER_DAY3_TEXT, expected_head=head2,
        operation_key="window-letter-commit-day3"))
    if not third.get("ok"):
        raise RuntimeError("LETTER_COMMIT_3_REFUSED: %s" % (third,))
    return {"artifact_id": artifact_id, "delivered_share_id": delivered,
            "draft_share_id": draft_id, "bound_head": head2,
            "head_after_third": third["data"].get("head_version_id")}


def _seed_pin_and_picture(stack: SimpleNamespace) -> dict:
    pinned = _send_local(stack, source_id=stack.work_a,
                         text=PIN_SHARE_TEXT, key="window-keepsake-pin")
    picture = stack.service.album_import(
        caption=PICTURE_CAPTION, content_type="image/png", data=PNG_BASE64,
        operation_key="window-keepsake-picture")
    if not picture.get("ok"):
        raise RuntimeError("ALBUM_IMPORT_REFUSED: %s" % (picture,))
    return {"pinned_share_id": pinned,
            "album_artifact_id": str(picture["data"]["artifact_id"])}


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """One fresh root plus a real package; the caller owns both directories."""

    root = Path(str(root))
    package = _prepare_world_package(root)
    with _prepare_with_package(package):
        stack = desk_fixture.build_stack(root, port=port, start_world=True,
                                         capability=capability)
    try:
        _enable_features(stack)
        _seed_home_details(stack)
        stack.letters = _seed_letter_work(stack)
        stack.keepsakes = _seed_pin_and_picture(stack)
        stack.package = package
        return stack
    except BaseException:
        close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    desk_fixture.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    """Normal shutdown; an unserved server is closed without ``shutdown()`` blocking forever."""

    server = getattr(stack, "server", None)
    if server is not None:
        if getattr(stack, "thread", None) is not None:
            server.shutdown()
        server.server_close()
    thread = getattr(stack, "thread", None)
    if thread is not None:
        thread.join(timeout=10)
    world = getattr(stack, "world", None)
    if world is not None:
        world.stop()
    services = getattr(stack, "services", None)
    if services is not None:
        services.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="window_keepsake_fixture")
    parser.add_argument("--port", type=int, default=3854)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-window-keepsake-%d-%d" % (
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
        "album_artifact_id": stack.keepsakes["album_artifact_id"],
        "pinned_share_id": stack.keepsakes["pinned_share_id"],
        "letter_work": stack.letters["artifact_id"],
        "letter_delivered_share": stack.letters["delivered_share_id"],
        "letter_draft_share": stack.letters["draft_share_id"],
        "endpoints": ["/api/phone/home/window", "/api/phone/home/keepsakes",
                      "/api/phone/home/letters"],
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
