#!/usr/bin/env python3
"""手机拍照：真 Supply + 真 ``chiyo_phone`` + 本机文生图的一次完整演示。

**这是合成演示**：下面的 ``SCENE``/``CAPTION`` 是脚本自己的常量，不是她的决定；跑通它只说明
“给定一句话时这条技术链能把图生成并存进相册”，不证明她会自己选择拍什么，也不证明中文本地
转换一定可用（转换层另见 ``scripts/camera_translation_demo.py``）。

这不是一个新的 owner，也不是第二种相册存储：

* 她在工具里给出画面描述与一句说明，手机服务让**本机**的 loopback 文生图运行时生成一张图
  （默认 ``http://127.0.0.1:7860``；WSL 通过既有 Windows 互操作桥转发同一个 HTTP 请求，
  不改 Windows 服务的监听地址）；
* 图片字节随即走**既有**的私人相册导入（Supply ``ManagedArtifact`` + 媒体文件 + 清单），
  所以存储、版本、权限与生命周期仍归真实 Life Supply；
* 相册列表按时间给出说明与来源，图片字节由手机自己的 GET 地址真实读回；
* 正规停服后在同一根上重开 Supply 与手机服务，旧照片仍然可读；
* 同一个 operation_key 重试直接读回原图，不会再让本机生成第二张。

脚本只打印结果，不打印画面描述、说明正文或生成参数。运行（WSL/POSIX）：

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_camera_demo.py

生成成功后续跑（父级复读，不重新生成）：

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_camera_demo.py --reuse-last

夹具根保留在临时目录下，不删除、不清理。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "test:phone-camera-demo"
CSRF = "phone-camera-demo-csrf"
ARTIFACT_GRANT = "phone-camera-demo:artifact-grant"
WORKSPACE_KEY = "phone-camera-demo:workspace"
ITEMS_TITLE = "她的私人日常事项"
CAPTURE_KEY = "demo-camera:first"
SCENE = "厨房的木桌上放着两只红苹果和一把银色小刀，窗外正在下雪。"
#: The exact English this scene's Chinese text produced in one real local conversion
#: (``.agents/worker-runs/camera-translation-confirm.json``).  The demo may reuse that
#: already-proven output instead of asking the same runtime a second time; when it does,
#: the record says ``local_llama_cpp_reused`` and no new conversion call is claimed.
REUSED_PROMPT = "kitchen, wooden table, two red apples, silver small knife, snow outside window"
CAPTION = "桌上的两只红苹果"

WORKER_RUNS = ROOT / ".agents" / "worker-runs"
PHOTO_PATH = WORKER_RUNS / "20261027-camera-photo.png"
STATE_PATH = WORKER_RUNS / "20261027-camera-state.json"


class ReusedConfirmedTranslation:
    """Stand-in for the converter that returns one already-proven output, verbatim.

    Used only by this demo when reusing a successful conversion instead of asking the local
    text runtime again.  It refuses any scene other than the one whose output was confirmed,
    and labels itself honestly so the capture record never reads as a fresh conversion.
    """

    source = "local_llama_cpp_reused"

    def __init__(self, scene: str, prompt: str) -> None:
        self.scene = scene.strip()
        self.prompt = prompt

    def prompt_for(self, scene) -> dict:
        from components.phone.camera import CameraRefused

        text = scene.strip() if isinstance(scene, str) else ""
        if text != self.scene:
            raise CameraRefused("CAMERA_TRANSLATION_UNAVAILABLE", "not_the_confirmed_scene")
        return {"prompt": self.prompt, "translated": True, "source": self.source}



def _scratch_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-phone-camera" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-phone-camera")
    base.mkdir(parents=True, exist_ok=True)
    return base


class Owner:
    """The real Life Supply owner on one generation socket over ``<root>/data``."""

    def __init__(self, data_root: Path, socket_path: Path, writer: str) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists() or socket_path.is_symlink():
            raise RuntimeError("REFUSING_TO_REUSE_SOCKET_PATH:%s" % socket_path)
        uid = os.geteuid()
        self.socket_path = socket_path
        self.service = LifeSupplyService(
            data_root, operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance=writer)
        self.server = LifeSupplySocketServer(str(socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        import socket

        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(12 * 1024 * 1024)
        return json.loads(line.decode("utf-8"))

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        # Sockets are only added, never removed.
        self.service.close()


def _issue(owner: Owner, capability: str, scope: str, target: str, authority_scope: str,
           use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise SystemExit("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def _provision(owner: Owner) -> tuple[str, str]:
    grant = _issue(owner, "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                   "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                   WORKSPACE_KEY)
    answer = owner.call({"op": "provision_workspace", "grant_id": grant,
                         "operation_key": WORKSPACE_KEY,
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise SystemExit("workspace provisioning failed: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    artifact_grant = _issue(owner, "COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                            "workspace:%s" % workspace_id, "life-supply.artifact.v1",
                            "REUSABLE", ARTIFACT_GRANT)
    return workspace_id, artifact_grant


def _create_private_items(owner: Owner, workspace_id: str, grant_id: str) -> str:
    from private_daily_items import create_document_artifact

    port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    artifact_id, _outcome = create_document_artifact(
        port, workspace_id=workspace_id, title=ITEMS_TITLE, grant_id=grant_id)
    return artifact_id


def _instance(root: Path, owner: Owner, workspace_id: str, grant_id: str,
              items_id: str) -> dict:
    world_home = root / "world"
    (world_home / "run").mkdir(parents=True, exist_ok=True)
    instance = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "phone-camera-demo",
        "subject": SUBJECT,
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-phone-camera-demo",
        "supply_socket": str(owner.socket_path),
        "private_items_artifact_id": items_id,
        "artifact_grant_id": grant_id,
        "workspace_id": workspace_id,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }
    phone_service.write_instance(root, instance)
    return instance


def _write_tool_profile(root: Path, base_url: str) -> Path:
    profile = root / "profile"
    (profile / "chiyo").mkdir(parents=True, exist_ok=True)
    (profile / "chiyo" / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.geteuid(),
        "phone": {
            "enabled": True,
            "base_url": base_url,
            "profile_home": str(root),
            "write_enabled": True,
            "private_artifacts": True,
        },
    }, ensure_ascii=False), encoding="utf-8")
    return profile


def _start_phone(instance: dict, translator=None) -> tuple[object, threading.Thread, str]:
    service = phone_service.PhoneService(instance, environment={})
    if translator is not None:
        # One demo-local converter that returns an already-proven conversion; the record
        # keeps its own honest source label.  Production still builds the real translator.
        service._camera_translator = translator
    httpd = phone_server.create_server(service, csrf_token=CSRF, port=0)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return httpd, thread, "http://127.0.0.1:%d" % int(httpd.server_address[1])


def _stop_phone(httpd, thread) -> None:
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    if thread is not None:
        thread.join(timeout=10)


def _tool(profile: Path, action: str, **fields) -> dict:
    args = {"action": action}
    args.update(fields)
    raw = phone_tool.phone_handler(args, hermes_home=str(profile))
    return json.loads(raw)


def _get(base_url: str, path: str) -> tuple[int, dict]:
    request = urllib.request.Request(base_url + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def _get_bytes(base_url: str, path: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(base_url + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return (int(response.status), str(response.headers.get("Content-Type") or ""),
                    response.read())
    except urllib.error.HTTPError as error:
        return int(error.code), str(error.headers.get("Content-Type") or ""), error.read()


def _new_root() -> Path:
    root = _scratch_base() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    root.mkdir(parents=True, exist_ok=False)
    return root


def _start_owner(root: Path, generation: int) -> Owner:
    socket_path = root / ("supply-%d-%d-%d.sock" % (os.getpid(), generation, time.time_ns()))
    return Owner(root / "data", socket_path, "phone-camera-demo:%d" % generation)


def _capture(profile: Path) -> tuple[dict, dict]:
    answer = _tool(profile, "camera", scene=SCENE, caption=CAPTION,
                   operation_key=CAPTURE_KEY)
    result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return answer, data


def _read_album(profile: Path) -> dict:
    listing = _tool(profile, "album")
    result = listing.get("result") if isinstance(listing.get("result"), dict) else {}
    return result


def _save_photo(data: bytes) -> None:
    WORKER_RUNS.mkdir(parents=True, exist_ok=True)
    PHOTO_PATH.write_bytes(data)


def _reuse_last() -> int:
    if not STATE_PATH.is_file():
        print("reuse=NO_STATE")
        return 2
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("reuse=BAD_STATE")
        return 2
    root = Path(str(state.get("root") or ""))
    if not root.is_dir():
        print("reuse=ROOT_MISSING")
        return 2
    owner = None
    httpd = None
    thread = None
    facts: dict[str, object] = {}
    try:
        owner = _start_owner(root, 2)
        instance = phone_service.load_instance(root)
        instance["supply_socket"] = str(owner.socket_path)
        phone_service.write_instance(root, instance)
        httpd, thread, base_url = _start_phone(instance)
        profile = _write_tool_profile(root, base_url)
        answer, data = _capture(profile)
        listing = _read_album(profile)
        images = listing.get("images") if isinstance(listing.get("images"), list) else []
        artifact_id = str(data.get("artifact_id") or "")
        image = images[0] if images else {}
        code, content_type, body = (
            _get_bytes(base_url, "/api/phone/album/%s/image" % artifact_id)
            if artifact_id else (0, "", b""))
        facts["no_second_generation"] = bool(
            answer.get("ok") and answer.get("status") == "already_applied"
            and data.get("reused") is True and data.get("generated") is False)
        facts["album_still_one"] = bool(len(images) == 1
                                        and image.get("artifact_id") == artifact_id)
        facts["image_still_readable"] = bool(
            code == 200 and content_type in ("image/png", "image/jpeg") and body
            and hashlib.sha256(body).hexdigest() == str((image.get("media") or {}).get("sha256")))
        _save_photo(body)
    finally:
        _stop_phone(httpd, thread)
        if owner is not None:
            owner.stop()
    print("reuse_no_second_generation=%s"
          % ("yes" if facts.get("no_second_generation") else "no"))
    print("reuse_album_still_one=%s" % ("yes" if facts.get("album_still_one") else "no"))
    print("reuse_image_readable=%s" % ("yes" if facts.get("image_still_readable") else "no"))
    print("photo=%s" % PHOTO_PATH)
    ok = all(bool(facts.get(name)) for name in (
        "no_second_generation", "album_still_one", "image_still_readable"))
    print("demo=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="phone camera demo")
    parser.add_argument("--reuse-last", action="store_true",
                        help="re-open the last successful root and read the same photo back")
    parser.add_argument("--reuse-confirmed-translation", action="store_true",
                        help="reuse the already-proven English for SCENE instead of asking "
                             "the local text runtime again; the record labels it as reused")
    args = parser.parse_args()
    if args.reuse_last:
        if not hasattr(os, "geteuid"):
            print("result=SKIP_NOT_POSIX")
            return 2
        return _reuse_last()
    if not hasattr(os, "geteuid"):
        print("result=SKIP_NOT_POSIX")
        return 2

    root = _new_root()
    owner = None
    httpd = None
    thread = None
    facts: dict[str, object] = {}
    try:
        # 1) real Supply + one private-items artifact (the phone instance still needs it).
        owner = _start_owner(root, 1)
        workspace_id, grant_id = _provision(owner)
        items_id = _create_private_items(owner, workspace_id, grant_id)
        instance = _instance(root, owner, workspace_id, grant_id, items_id)

        httpd, thread, base_url = _start_phone(
            instance, ReusedConfirmedTranslation(SCENE, REUSED_PROMPT)
            if args.reuse_confirmed_translation else None)
        profile = _write_tool_profile(root, base_url)

        # 2) her explicit capture: scene + caption + one operation key.
        answer, data = _capture(profile)
        artifact_id = str(data.get("artifact_id") or "")
        capture_result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
        facts["capture_status"] = str(answer.get("status") or "")
        facts["capture_reason"] = str(capture_result.get("reason_code") or "")
        facts["capture_detail"] = str((capture_result.get("data") or {}).get("detail") or "")
        facts["capture_applied"] = bool(
            answer.get("ok") is True and answer.get("status") == "applied" and artifact_id
            and data.get("generated") is True and data.get("generated_locally") is True)
        media = data.get("media") if isinstance(data.get("media"), dict) else {}
        facts["capture_has_media"] = bool(
            isinstance(media.get("sha256"), str) and int(media.get("bytes") or 0) > 0)

        # 3) the album lists it by time with her caption and the honest source label.
        listing = _read_album(profile)
        images = listing.get("images") if isinstance(listing.get("images"), list) else []
        row = images[0] if images else {}
        facts["album_count"] = len(images)
        facts["album_has_row"] = bool(
            row.get("artifact_id") == artifact_id and row.get("caption") == CAPTION
            and row.get("created_at") and row.get("generated_locally") is True
            and row.get("source_label") == "local_generated")
        facts["album_source_not_camera"] = bool(
            "local_generated" in json.dumps(row, ensure_ascii=False)
            and "camera" not in str(row.get("source_label") or ""))

        # 4) one picture's detail through the tool, and the real bytes through the phone HTTP.
        detail = _tool(profile, "album_image", artifact_id=artifact_id)
        detail_image = detail.get("image") if isinstance(detail.get("image"), dict) else {}
        facts["detail_matches"] = bool(
            detail.get("ok") is True and detail_image.get("caption") == CAPTION
            and detail_image.get("generated_locally") is True)
        actual_prompt = str(data.get("generation_prompt") or "")
        translation = data.get("translation") if isinstance(data.get("translation"), dict) else {}
        facts["translation_source"] = str(translation.get("source") or "")
        facts["actual_prompt"] = actual_prompt
        # The detail must carry her original Chinese words, the English that was actually sent,
        # the caption and the time -- the four things the phone should show.
        facts["detail_bilingual"] = bool(
            detail_image.get("scene") == SCENE
            and detail_image.get("generation_prompt") == actual_prompt
            and actual_prompt
            and detail_image.get("created_at")
            and isinstance(detail_image.get("translation"), dict))
        code, content_type, body = _get_bytes(
            base_url, "/api/phone/album/%s/image" % artifact_id)
        facts["image_served"] = bool(
            code == 200 and content_type in ("image/png", "image/jpeg") and body
            and int(media.get("bytes") or 0) == len(body)
            and hashlib.sha256(body).hexdigest() == str(media.get("sha256")))
        if body:
            _save_photo(body)

        # 5) the original key is idempotent: same picture back, no second generation.
        replay_answer, replay_data = _capture(profile)
        replay_list = _read_album(profile)
        facts["replay_no_new_photo"] = bool(
            replay_answer.get("status") == "already_applied"
            and replay_data.get("reused") is True
            and replay_data.get("generated") is False
            and replay_data.get("artifact_id") == artifact_id
            and int(replay_list.get("count") or 0) == 1)

        # 6) a normal close, then the same root re-opened: the picture is still there.
        _stop_phone(httpd, thread)
        httpd = thread = None
        owner.stop()
        owner = None

        owner = _start_owner(root, 2)
        instance = phone_service.load_instance(root)
        instance["supply_socket"] = str(owner.socket_path)
        phone_service.write_instance(root, instance)
        httpd, thread, base_url = _start_phone(instance)
        profile = _write_tool_profile(root, base_url)
        restart_list = _read_album(profile)
        restart_images = (restart_list.get("images")
                          if isinstance(restart_list.get("images"), list) else [])
        restart_row = restart_images[0] if restart_images else {}
        restart_code, restart_type, restart_body = _get_bytes(
            base_url, "/api/phone/album/%s/image" % artifact_id)
        facts["restart_still_readable"] = bool(
            len(restart_images) == 1 and restart_row.get("artifact_id") == artifact_id
            and restart_row.get("caption") == CAPTION
            and restart_code == 200 and restart_type in ("image/png", "image/jpeg")
            and hashlib.sha256(restart_body).hexdigest() == str(media.get("sha256")))
        _save_photo(restart_body or body)

        WORKER_RUNS.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "root": str(root), "operation_key": CAPTURE_KEY,
            "scene": SCENE, "caption": CAPTION,
            "generation_prompt": actual_prompt,
            "translation_source": facts.get("translation_source"),
        }, ensure_ascii=False), encoding="utf-8")
    finally:
        _stop_phone(httpd, thread)
        if owner is not None:
            owner.stop()

    # Results only.  The scene text, the caption and the generated bytes are never printed.
    print("capture_applied=%s" % ("yes" if facts.get("capture_applied") else "no"))
    print("capture_status=%s" % facts.get("capture_status"))
    print("capture_reason=%s" % facts.get("capture_reason"))
    print("capture_detail=%s" % facts.get("capture_detail"))
    print("album_count=%s" % facts.get("album_count"))
    print("album_has_row=%s" % ("yes" if facts.get("album_has_row") else "no"))
    print("album_source_label=%s" % ("local_generated" if facts.get("album_source_not_camera")
                                     else "unknown"))
    print("detail_matches=%s" % ("yes" if facts.get("detail_matches") else "no"))
    print("detail_bilingual=%s" % ("yes" if facts.get("detail_bilingual") else "no"))
    print("translation_source=%s" % facts.get("translation_source"))
    print("generation_prompt=%s" % facts.get("actual_prompt"))
    print("image_served=%s" % ("yes" if facts.get("image_served") else "no"))
    print("replay_no_new_photo=%s" % ("yes" if facts.get("replay_no_new_photo") else "no"))
    print("restart_still_readable=%s"
          % ("yes" if facts.get("restart_still_readable") else "no"))
    print("photo=%s" % PHOTO_PATH)
    print("root=%s" % root)
    ok = (all(bool(facts.get(name)) for name in (
        "capture_applied", "capture_has_media", "album_has_row", "album_source_not_camera",
        "detail_matches", "detail_bilingual", "image_served", "replay_no_new_photo",
        "restart_still_readable"))
        and int(facts.get("album_count") or 0) == 1)
    print("demo=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
