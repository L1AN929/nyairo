#!/usr/bin/env python3
"""她的一天：用真实记录长出一条只读时间线，再读两次、重启一次。

这一次**实际使用**不是测试矩阵：

* root 由真实日常入口 ``nyairo_day.py init`` 创建（``daily_neighborhood_v1``），
  Supply/World/Body/PhoneService 都是仓库里的真实进程；
* 通过真实 phone HTTP/tool 各做一次：一个 World 动作（POSE）、一条日记写入、
  一张明确标注为**合成测试图**的本地 PNG 导入（不是相机、不是产品生图）、
  一笔既有虚拟商店交易（``virtual_snack_wafer``，不新造渠道）；
* 然后读 ``GET /api/phone/today/timeline`` 的两种视图：operator（无 actor 声明）与
  她自己的工具（``X-Phone-Actor: model``）。验证真实记录自己长出来、重复读不增不写、
  operator 骨架里没有日记正文/相册说明/图片字节，而原入口仍然能读；
* 正规停服后同根重开，时间线条目仍在。

边界：决定全部是脚本替身；没有调用任何产品模型；没有生成图片（PNG 是内置的 1×1
测试图）；世界写门只对本次进程树显式 canonical，生产默认仍是关闭的；日记正文/相册说明
只走各自原入口，聚合入口不复制。

用法（WSL / Linux，仓库根）::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_day_timeline_demo.py
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

PACKAGE = "daily_neighborhood_v1"
SUBJECT = "chiyo"

POSE_KEY = "timeline:pose:v1"
DIARY_KEY = "timeline:diary:v1"
ALBUM_KEY = "timeline:album:v1"
PURCHASE_KEY = "timeline:purchase:v1"

DIARY_BODY = "今天把窗边的光记一笔，只给自己看。"
ALBUM_CAPTION = "合成测试图：证明这是 import，不是拍照"
#: A real 1x1 transparent PNG.  Structural checks in the owner accept it; it is not a photo.
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
RESULT_PATH = ROOT / ".agents" / "worker-runs" / "20261027-phone-day-timeline-demo.json"


class DemoError(RuntimeError):
    """A named refusal of this demo; nothing is papered over."""


def say(tag: str, text: str) -> None:
    print("[%s] %s" % (tag, text), flush=True)


def _entry():
    return importlib.import_module("nyairo_day")


def _namespace(entry, **values: Any) -> argparse.Namespace:
    defaults = dict(
        root=None, memory_authority=None, run_id=None, world_package=None,
        lived_needs=False, lived_needs_hunger=None, lived_needs_thirst=None,
        lived_needs_toilet=None, room_living=None, world_actions=None,
        memory_authority_path=None, foreground=False, json=True, port=None,
        model=False, live_model=False, duration_seconds=None, max_new_requests=None,
    )
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _tool(profile_home: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    from chiyo_bundle import phone_tool

    raw = phone_tool.phone_handler(dict(payload), hermes_home=str(profile_home),
                                   platform="cli")
    return json.loads(raw)


def _envelope(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return dict(result) if isinstance(result, Mapping) else dict(tool_result)


def _data(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    data = _envelope(tool_result).get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _write_profile(home: Path, port: int) -> None:
    target = home / "chiyo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.getuid() if hasattr(os, "getuid") else None,
        "phone": {
            "enabled": True,
            "base_url": "http://127.0.0.1:%d" % int(port),
            "write_enabled": True,
            "private_artifacts": True,
        },
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def _http_json(port: int, path: str, *, method: str = "GET",
               body: Optional[Mapping[str, Any]] = None, csrf: Optional[str] = None,
               actor: Optional[str] = None, timeout: float = 20.0) -> dict[str, Any]:
    data = None if body is None else json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (int(port), path),
                                     data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if csrf:
        request.add_header("X-CSRF-Token", str(csrf))
    if actor:
        request.add_header("X-Phone-Actor", str(actor))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def _start_service(root: Path, *, ready_timeout: float = 90.0):
    from components.phone import demo

    stop_event = threading.Event()
    ready = threading.Event()
    announcement: dict[str, Any] = {}
    failure: dict[str, Any] = {}

    def on_ready(payload: Mapping[str, Any]) -> None:
        announcement.update(dict(payload))
        ready.set()

    def runner() -> None:
        try:
            demo.serve_instance(
                root, port=0, room_living=True, world_intent_gate="canonical",
                tick_seconds=3600.0, on_ready=on_ready, stop_event=stop_event,
                announce=True,
            )
        except Exception as exc:  # noqa: BLE001 - report the real failure
            failure["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            ready.set()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    if not ready.wait(ready_timeout):
        raise DemoError("SERVICE_NOT_READY_WITHIN_TIMEOUT")
    if failure:
        raise DemoError(failure["error"])
    return stop_event, thread, announcement


def _stop_service(stop_event: threading.Event, thread: threading.Thread) -> None:
    stop_event.set()
    thread.join(timeout=90.0)
    if thread.is_alive():
        raise DemoError("SERVICE_DID_NOT_STOP")


def _wait_http(port: int, timeout: float = 30.0) -> None:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", int(port))) == 0:
                    break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise DemoError("PHONE_HTTP_NOT_READY")
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/phone/state" % int(port), timeout=15.0) as answer:
        answer.read()


def _build_environment(root: Path, instance: Mapping[str, Any]) -> dict[str, str]:
    world_home = Path(str(instance["world_home"]))
    env = dict(os.environ)
    env.update({
        "CHIYO_WORLD_HOME": str(world_home),
        "HERMES_HOME": str(world_home),
        "CHIYO_WORLD_PACKAGE": str(instance.get("world_package") or ""),
        "CHIYO_WORLD_ID": str(instance.get("world_id") or ""),
        "CHIYO_WORLD_RUNTIME": str(ROOT / "components" / "world"),
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
        "CHIYO_WORLD_DELIVERY_TIME_ENABLED": "1",
        "CHIYO_WORLD_LOCAL_OWNER_PHONE": SUBJECT,
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(world_home / "run"),
        "NYAIRO_WORLD_BRIDGE_DIR": str(ROOT / "components" / "world" / "service" / "bridge"),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
        "BODY_RUNTIME_MODE": "canonical",
        "CHIYO_WORLD_BODY_AUTHORITY": "production",
        "WORLD_BODY_SOCKET": str(instance.get("world_body_socket")
                                 or world_home / "run" / "gateway.sock"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    os.environ.update(env)
    return env


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise DemoError(message)


def _entry_ids(payload: Mapping[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    return [str(row.get("entry_id") or "") for row in data.get("entries") or ()]


def _kinds(payload: Mapping[str, Any]) -> set[str]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    return {str(row.get("kind") or "") for row in data.get("entries") or ()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="retain the new root (it is always retained; kept for symmetry)")
    parser.parse_args(argv)
    if os.name != "posix" or not hasattr(os, "getuid"):
        print("这个演示只在 WSL/Linux 里运行（World/Supply 子进程需要 POSIX）。")
        return 2

    entry = _entry()
    parent = Path(tempfile.mkdtemp(prefix="nyairo-day-timeline-"))
    root = parent / "instance"
    profile_home = parent / "phone-profile"
    authority = parent / "memory-authority" / "authority.db"
    summary: dict[str, Any] = {
        "kind": "phone_day_timeline_demo",
        "root": str(root),
        "package": PACKAGE,
        "product_model_requests": 0,
        "image_generation_requests": 0,
        "scripted_decision": True,
        "steps": [],
    }
    stop_event = None
    thread = None
    try:
        rc = entry.cmd_init(_namespace(
            entry, root=str(root), world_package=PACKAGE,
            memory_authority=str(authority), json=False))
        summary["steps"].append({"step": "init", "exit": rc})
        if rc != 0:
            raise DemoError("INIT_REFUSED")
        from components.phone import service as phone_service

        instance = phone_service.load_instance(root)
        _build_environment(root, instance)
        say("init", "新隔离根 %s；世界包 %s；Supply/World 由真实入口准备" % (root, PACKAGE))

        stop_event, thread, announcement = _start_service(root)
        port = int(announcement.get("port") or 0)
        _assert(port, "the phone service did not publish a port")
        _write_profile(profile_home, port)
        _wait_http(port)
        say("service", "真 Supply/World + 手机 HTTP 已就绪：port=%s" % port)

        world = _tool(profile_home, {"action": "world"})
        _assert(_envelope(world).get("ok"), "world read refused")
        location = _data(world).get("location") or {}
        say("world", "当前位置：%s（%s）" % (location.get("place_id"), location.get("name")))

        # 1. one real World action through the same tool/HTTP surface she would use.
        posed = _tool(profile_home, {
            "action": "world_action", "world_action": "POSE",
            "params": {"pose": "seated"}, "operation_key": POSE_KEY,
        })
        posed_env = _envelope(posed)
        _assert(posed_env.get("ok"), "POSE was refused: %s" % posed_env.get("reason_code"))
        say("action", "World 动作 POSE seated：status=%s（脚本替身，不是模型选择）"
            % posed_env.get("status"))

        # 2. one diary write (real Supply owner; body is private text on purpose).
        diary = _tool(profile_home, {
            "action": "diary_write", "body": DIARY_BODY, "operation_key": DIARY_KEY,
        })
        diary_env = _envelope(diary)
        _assert(diary_env.get("ok"), "diary_write was refused: %s"
                % diary_env.get("reason_code"))
        diary_id = str((diary_env.get("data") or {}).get("id") or "")
        _assert(diary_id, "diary_write returned no entry id")
        say("diary", "日记写入：%s（正文只在原入口；聚合不复制）" % diary_id)

        # 3. one synthetic test PNG through the *existing* album import (not camera).
        state = _http_json(port, "/api/phone/state")
        csrf = str(state.get("csrf_token") or "")
        _assert(csrf, "the phone service published no CSRF token")
        imported = _http_json(port, "/api/phone/album/images", method="POST", csrf=csrf,
                              actor="model", body={
                                  "caption": ALBUM_CAPTION,
                                  "content_type": "image/png",
                                  "data": PNG_BASE64,
                                  "operation_key": ALBUM_KEY,
                              })
        _assert(imported.get("ok"), "album import was refused: %s"
                % imported.get("reason_code"))
        album_id = str((imported.get("data") or {}).get("artifact_id") or "")
        _assert(album_id, "album import returned no artifact id")
        say("album", "合成 1×1 PNG 经既有 import 保存：%s（记为 album_import，不是拍照）"
            % album_id)

        # 4. one existing virtual-shop transaction (no new channel).
        before_wallet = _data(_tool(profile_home, {"action": "wallet"}))
        purchased = _tool(profile_home, {
            "action": "purchase", "catalog_item_id": "virtual_snack_wafer",
            "operation_key": PURCHASE_KEY,
        })
        purchased_env = _envelope(purchased)
        _assert(purchased_env.get("ok"), "virtual purchase was refused: %s"
                % purchased_env.get("reason_code"))
        after_wallet = _data(_tool(profile_home, {"action": "wallet"}))
        say("wallet", "虚拟商店交易：余额 %s → %s（%s）"
            % (before_wallet.get("balance"), after_wallet.get("balance"),
               after_wallet.get("currency")))
        _assert(before_wallet.get("balance") != after_wallet.get("balance"),
                "the purchase did not produce a wallet transaction")

        # 5. read the timeline: operator skeleton and her own tool view.
        operator = _http_json(port, "/api/phone/today/timeline")
        subject_tool = _tool(profile_home, {"action": "day_timeline"})
        subject = _envelope(subject_tool)
        _assert(operator.get("ok"), "operator timeline refused: %s"
                % operator.get("reason_code"))
        _assert(subject.get("ok"), "subject timeline refused: %s"
                % subject.get("reason_code"))
        expected = {"world_action", "diary_write", "album_saved", "wallet_transaction"}
        _assert(expected <= _kinds(operator), "missing kinds: %s" % (_kinds(operator),))
        _assert(expected <= _kinds(subject), "missing kinds in subject view")
        operator_rendered = json.dumps(operator, ensure_ascii=False)
        subject_rendered = json.dumps(subject, ensure_ascii=False)
        _assert(DIARY_BODY not in operator_rendered,
                "the operator timeline leaked the diary body")
        _assert(DIARY_BODY not in subject_rendered,
                "the subject timeline copied the diary body into the aggregate")
        _assert(ALBUM_CAPTION not in operator_rendered,
                "the operator timeline leaked the album caption")
        _assert(ALBUM_CAPTION not in subject_rendered,
                "the subject timeline copied the album caption into the aggregate")
        op_data = operator.get("data") or {}
        sub_data = subject.get("data") or {}
        _assert(op_data.get("skeleton_only") is True, "operator view must be skeleton-only")
        _assert(sub_data.get("skeleton_only") is False, "subject view must expose detail refs")
        diary_rows = [row for row in op_data.get("entries") or ()
                      if row.get("kind") == "diary_write"]
        _assert(diary_rows and diary_rows[0].get("private_detail_withheld") is True,
                "operator diary row must name the withheld detail")
        say("timeline", "operator 时间线 %s 条；kinds=%s；正文/说明未进入聚合"
            % (op_data.get("count"), sorted(_kinds(operator))))

        # 6. repeated reads do not add or change entries.
        ids_first = _entry_ids(operator)
        operator_again = _http_json(port, "/api/phone/today/timeline")
        subject_again = _envelope(_tool(profile_home, {"action": "day_timeline"}))
        _assert(_entry_ids(operator_again) == ids_first, "a repeated read changed the entries")
        _assert(_entry_ids(subject_again) == _entry_ids(subject),
                "a repeated subject read changed the entries")
        say("repeat", "重复读取：entry_id 与条数不变（%d 条），没有新写" % len(ids_first))

        # 7. the original entries still answer their own details (control).
        album_detail = _tool(profile_home, {"action": "album_image", "artifact_id": album_id})
        album_env = _envelope(album_detail)
        _assert(album_env.get("ok"), "the original album detail is no longer readable")
        _assert(((album_env.get("image") or {}).get("caption")) == ALBUM_CAPTION,
                "the original album detail lost its caption")
        diary_detail = _tool(profile_home, {"action": "diary_read", "entry_id": diary_id})
        diary_env = _envelope(diary_detail)
        _assert(diary_env.get("ok"), "the original diary read is no longer readable")
        _assert(((diary_env.get("entry") or {}).get("body")) == DIARY_BODY,
                "the original diary read lost its body")
        operator_diary = _http_json(port, "/api/phone/diary/" + diary_id)
        _assert(operator_diary.get("body_withheld") is True,
                "the operator diary read must still withhold the body")
        say("control", "原入口仍可读：album caption 与 diary 正文都在；operator 的日记读仍扣留正文")

        summary["timeline"] = {
            "operator_count": op_data.get("count"),
            "subject_count": sub_data.get("count"),
            "kinds": sorted(_kinds(operator)),
            "entry_ids": ids_first,
            "operator_skeleton_only": op_data.get("skeleton_only"),
            "subject_skeleton_only": sub_data.get("skeleton_only"),
            "ordering": op_data.get("ordering"),
            "sources": {name: (block or {}).get("state")
                        for name, block in (op_data.get("sources") or {}).items()},
        }
        summary["actions"] = {
            "world_pose_status": posed_env.get("status"),
            "diary_entry_id": diary_id,
            "album_artifact_id": album_id,
            "album_source": "album_import (synthetic test PNG, not camera)",
            "wallet_before": before_wallet.get("balance"),
            "wallet_after": after_wallet.get("balance"),
            "currency": after_wallet.get("currency"),
        }

        # 8. normal stop, same root started again: the recorded entries are still there.
        _stop_service(stop_event, thread)
        stop_event, thread = None, None
        say("stop", "服务已正规关闭（自己的 Supply/World 子进程与 HTTP 都停了）")
        stop_event, thread, announcement2 = _start_service(root)
        port2 = int(announcement2.get("port") or 0)
        _assert(port2, "the restarted phone service did not publish a port")
        _write_profile(profile_home, port2)
        _wait_http(port2)
        restart = _http_json(port2, "/api/phone/today/timeline")
        _assert(restart.get("ok"), "timeline after restart refused: %s"
                % restart.get("reason_code"))
        restart_ids = _entry_ids(restart)
        _assert(set(ids_first) <= set(restart_ids),
                "the restart lost timeline entries: %s" % (restart_ids,))
        say("restart", "同根重开：时间线条目仍为 %d 条，原 entry_id 都在" % len(restart_ids))
        summary["restart"] = {"count": len(restart_ids),
                              "kept_all_original_ids": set(ids_first) <= set(restart_ids)}

        summary["ok"] = True
        say("done", "一次真实使用完成：World 动作 + 日记 + 合成图 import + 虚拟交易 → "
                    "同一时间线可读、重复读不增、operator 只有骨架、同根重开仍在")
        summary["local_action_gate"] = (
            "WORLD_BODY_INTENT_MUTATION_GATE=canonical for this process tree only; "
            "the production default stays off and was not changed.")
        say("boundary", "决定全部是脚本替身；没有产品模型调用、没有生图、没有外发；"
                        "日记正文与相册说明只走原入口；时间线是只读聚合，没有新 store")
    except Exception as exc:  # noqa: BLE001 - report the real reason
        summary["ok"] = False
        summary["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:600])
        say("error", summary["error"])
    finally:
        if stop_event is not None and thread is not None:
            try:
                _stop_service(stop_event, thread)
            except Exception as exc:  # noqa: BLE001
                summary.setdefault("cleanup_error", "%s: %s" % (type(exc).__name__, exc))
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1,
                                          sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        print("结果：%s" % RESULT_PATH)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
