#!/usr/bin/env python3
"""相机现有链路**一次**验证夹具：真 Supply + 真手机 HTTP + 真本机转换/出图。

这不是新功能，也不是第二个图片 owner。它只把既有链路按一次真实调用串起来，供父级在本机
运行时（translator 18190 / sd 7860）已由 ``preview_start`` 拉起后执行：

* ``serve``   —— 在新根里建真实 Supply workspace/grant/私人事项文档、写真实手机实例与
  loopback HTTP（``127.0.0.1:<port>``），默认不启动 resident、不调用产品模型；
* ``verify``  —— 只作客户端，经真实 ``chiyo_phone`` 的 ``camera`` 工具走**一次**正式生产
  ``SceneTranslator`` 与本机出图，随后用 ``album``/``album_image`` 读回元数据、用手机 HTTP
  取图片字节；同一次脚本只允许一次尝试，失败/未知/完成后一律拒绝二次 verify；
* ``readback`` —— 只读复核：不再 camera、不翻译、不出图、不启动服务、不重写 instance。

画面是脚本常量（合成画面，不是她的选择）；跑通只证明“给定这句中文时这条技术链能出图并进
相册”，不证明她自主拍照，也不证明 G1/24h 或自然采用。旧 ``phone_camera_demo.py`` 的
``PHOTO_PATH``/``STATE_PATH`` 与旧根一律不读不写；本夹具只用新根。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
# The real Supply owner over one AF_UNIX socket: reused verbatim from the existing demo, so this
# fixture never invents a second owner or a second way to start one.
from phone_camera_demo import Owner, _get_bytes, _tool, _write_tool_profile  # noqa: E402

SUBJECT = "test:camera-chain-once"
ARTIFACT_GRANT = "camera-chain-once:artifact-grant"
WORKSPACE_KEY = "camera-chain-once:workspace"
ITEMS_TITLE = "她的私人日常事项"
CAPTURE_KEY = "camera-chain-once:white-cup"
SCENE = "一只白色茶杯放在木桌上。"
CAPTION = "脚本链路验证：桌上的白茶杯"
STATE_NAME = "chain-once-verify.json"
WORKER_RUNS = ROOT / ".agents" / "worker-runs"
PHOTO_PATH = WORKER_RUNS / "20261027-camera-chain-once.png"


class ChainRefused(RuntimeError):
    """A precondition this fixture will not pass, with a stable reason code."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(("%s:%s" % (reason_code, detail))[:300])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require_posix(what: str) -> None:
    if not hasattr(os, "geteuid"):
        raise ChainRefused("POSIX_REQUIRED", "%s needs POSIX AF_UNIX" % what)


# ---------------------------------------------------------------------------------------------
# serve: one new root, the real Supply owner, the real phone HTTP
# ---------------------------------------------------------------------------------------------


def _issue(owner: Owner, capability: str, scope: str, target: str, authority_scope: str,
           use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise ChainRefused("GRANT_REFUSED", str(answer.get("reason_code") or ""))
    return str(answer["grant_id"])


def _provision(owner: Owner) -> tuple[str, str]:
    grant = _issue(owner, "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                   "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                   WORKSPACE_KEY)
    answer = owner.call({"op": "provision_workspace", "grant_id": grant,
                         "operation_key": WORKSPACE_KEY,
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise ChainRefused("WORKSPACE_PROVISION_FAILED",
                           str(answer.get("reason_code") or ""))
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
        "created_at": _now(),
        "name": "camera-chain-once",
        "subject": SUBJECT,
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-camera-chain-once",
        "supply_socket": str(owner.socket_path),
        "private_items_artifact_id": items_id,
        "artifact_grant_id": grant_id,
        "workspace_id": workspace_id,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }
    phone_service.write_instance(root, instance)
    return instance


def _check_root_for_serve(root: Path) -> bool:
    """True when the root is absent (this call may create it); non-empty is refused, never reused.

    Validation is side-effect free so a refusal (including ``POSIX_REQUIRED``) never creates or
    touches a directory.
    """

    if root.exists():
        if not root.is_dir():
            raise ChainRefused("ROOT_NOT_A_DIRECTORY", str(root))
        if any(root.iterdir()):
            raise ChainRefused("ROOT_NOT_EMPTY", "existing root is never opened or overwritten")
        return False
    return True


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", int(port)))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _prepare(root: Path) -> tuple[Owner, dict]:
    """The same provisioning order as the existing demo, with this fixture's own subject."""

    owner = Owner(root / "data",
                  root / ("supply-%d-%d.sock" % (os.getpid(), time.time_ns())),
                  "camera-chain-once:%d" % os.getpid())
    try:
        workspace_id, grant_id = _provision(owner)
        items_id = _create_private_items(owner, workspace_id, grant_id)
        instance = _instance(root, owner, workspace_id, grant_id, items_id)
    except Exception:
        owner.stop()
        raise
    return owner, instance


def _serve(args: argparse.Namespace) -> int:
    root = Path(str(args.root)).expanduser()
    if not 0 < int(args.port) <= 65535:
        raise ChainRefused("PORT_INVALID", str(args.port))
    fresh = _check_root_for_serve(root)
    _require_posix("serve")
    if fresh:
        root.mkdir(parents=True, exist_ok=False)
    if not _port_free(int(args.port)):
        # Checked before the owner writes anything, so a busy port does not poison a new root.
        raise ChainRefused("PORT_NOT_FREE", str(args.port))

    owner = None
    httpd = None
    thread = None
    try:
        owner, instance = _prepare(root)
        base_url = "http://127.0.0.1:%d" % int(args.port)
        profile_dir = _write_tool_profile(root, base_url)
        service = phone_service.PhoneService(instance, environment={})
        album = service.album_list()
        if album.get("ok") is not True or album.get("count") != 0:
            raise ChainRefused("ALBUM_NOT_EMPTY",
                               "a new root must start with an empty album")
        httpd = phone_server.create_server(service, csrf_token=secrets.token_hex(16),
                                           port=int(args.port))
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()

        print("ready=yes")
        print("base_url=%s" % base_url)
        print("root=%s" % root)
        print("subject=%s" % instance.get("subject"))
        print("album_count=%s" % album.get("count"))
        print("profile=%s" % profile_dir)
        print("camera=real_scene_translator+real_local_picture_lab")
        print("product_model_calls=0")
        sys.stdout.flush()

        stop = threading.Event()

        def _stop(signum, frame):  # noqa: ARG001
            stop.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        while not stop.wait(0.5):
            pass
        return 0
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=10)
        if owner is not None:
            owner.stop()


# ---------------------------------------------------------------------------------------------
# the one-attempt guard
# ---------------------------------------------------------------------------------------------


def _state_path(root: Path) -> Path:
    return root / STATE_NAME


def _claim_attempt(root: Path) -> Path:
    """Create the started record exactly once; any existing record refuses a second verify."""

    path = _state_path(root)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ChainRefused("CHAIN_VERIFY_ALREADY_ATTEMPTED",
                           "this root already has a verify record") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"status": "started", "started_at": _now(), "pid": os.getpid()},
                  handle, ensure_ascii=False, sort_keys=True)
    return path


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _load_profile(root: Path) -> Path:
    if not root.is_dir():
        raise ChainRefused("ROOT_MISSING", str(root))
    try:
        phone_service.load_instance(root)
    except (OSError, phone_service.PhoneServiceError) as exc:
        raise ChainRefused("INSTANCE_UNREADABLE", type(exc).__name__) from None
    profile = root / "profile"
    if not (profile / "chiyo" / "config.json").is_file():
        raise ChainRefused("PROFILE_MISSING", str(profile))
    return profile


def _base_url(profile: Path) -> str:
    try:
        config = json.loads((profile / "chiyo" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ChainRefused("PROFILE_UNREADABLE", str(profile)) from None
    section = config.get("phone") if isinstance(config.get("phone"), Mapping) else {}
    url = str(section.get("base_url") or "")
    if not url:
        raise ChainRefused("PROFILE_BASE_URL_MISSING", str(profile))
    return url


def _answer_text(answer: Mapping[str, Any]) -> str:
    result = answer.get("result") if isinstance(answer.get("result"), Mapping) else {}
    return str(result.get("reason_code") or answer.get("reason_code") or "")


def _failure_from(answer: Mapping[str, Any]) -> str:
    reason = _answer_text(answer) or str(answer.get("status") or "unknown")
    return reason[:120]


# ---------------------------------------------------------------------------------------------
# verify: one real camera call, then the album and the real bytes
# ---------------------------------------------------------------------------------------------


def _photo_path(root: Path) -> Path:
    """The picture this run writes, inside *its own* new root.

    A single fixed ``WORKER_RUNS`` path would make every later run -- and every test case --
    collide with the first saved picture (``PNG_PATH_EXISTS``), so the fixture keeps this
    artifact with the run it belongs to.
    """

    return Path(root) / "chain-once-photo.png"


def _photo_path_free(root: Path) -> None:
    if _photo_path(root).exists():
        raise ChainRefused("PNG_PATH_EXISTS", "refusing to overwrite an existing picture")


def _save_bytes(data: bytes, root: Path) -> None:
    path = _photo_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _verify(args: argparse.Namespace) -> int:
    root = Path(str(args.root)).expanduser()
    profile = _load_profile(root)
    _photo_path_free(root)
    state_path = _claim_attempt(root)

    state: dict[str, Any] = {"status": "started", "ok": False, "root": str(root),
                             "subject": SUBJECT, "started_at": _now()}
    try:
        answer = _tool(profile, "camera", scene=SCENE, caption=CAPTION,
                       operation_key=CAPTURE_KEY)
        result = answer.get("result") if isinstance(answer.get("result"), Mapping) else {}
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        state["camera_status"] = str(answer.get("status") or "")
        state["camera_reason"] = _answer_text(answer)
        artifact_id = str(data.get("artifact_id") or "")
        applied = bool(answer.get("ok") is True and answer.get("status") == "applied"
                       and data.get("generated") is True
                       and data.get("generated_locally") is True and artifact_id)
        if not applied:
            # An ``already_applied`` answer means a picture exists from some earlier attempt:
            # this run did not exercise the conversion, so it is not counted as success.
            raise ChainRefused("CAMERA_NOT_APPLIED", _failure_from(answer))
        state["artifact_id"] = artifact_id
        state["scene"] = SCENE
        state["caption"] = CAPTION
        state["generation_prompt"] = str(data.get("generation_prompt") or "")
        translation = data.get("translation") if isinstance(data.get("translation"), Mapping) else {}
        state["translation"] = {"translated": translation.get("translated"),
                                "source": str(translation.get("source") or "")}
        # This run must have gone through the production converter, not caller-English or a
        # reused/fixture answer; anything else is not the chain this fixture claims to prove.
        if not (translation.get("translated") is True
                and str(translation.get("source") or "") == "local_llama_cpp"
                and str(data.get("generation_prompt") or "").strip()):
            raise ChainRefused("TRANSLATION_NOT_REAL",
                               str(translation.get("source") or "missing"))
        media = data.get("media") if isinstance(data.get("media"), Mapping) else {}
        state["media"] = {"sha256": str(media.get("sha256") or ""),
                          "bytes": int(media.get("bytes") or 0),
                          "name": str(media.get("name") or "")}

        listing = _tool(profile, "album")
        listed = listing.get("result") if isinstance(listing.get("result"), Mapping) else {}
        images = listed.get("images") if isinstance(listed.get("images"), list) else []
        row = images[0] if len(images) == 1 and isinstance(images[0], Mapping) else {}
        state["album_count"] = len(images)
        if not (listing.get("ok") is True and len(images) == 1
                and row.get("artifact_id") == artifact_id
                and row.get("caption") == CAPTION
                and row.get("generated_locally") is True
                and row.get("source_label") == "local_generated"
                and row.get("created_at")):
            raise ChainRefused("ALBUM_ROW_MISMATCH", "album_count=%d" % len(images))
        state["source_label"] = str(row.get("source_label") or "")
        state["created_at"] = str(row.get("created_at") or "")

        detail_answer = _tool(profile, "album_image", artifact_id=artifact_id)
        image = detail_answer.get("image") if isinstance(detail_answer.get("image"),
                                                         Mapping) else {}
        detail_ok = bool(
            detail_answer.get("ok") is True
            and image.get("artifact_id") == artifact_id
            and image.get("caption") == CAPTION
            and image.get("scene") == SCENE
            and image.get("generation_prompt") == state["generation_prompt"]
            and isinstance(image.get("translation"), Mapping)
            and image.get("generated_locally") is True
            and image.get("created_at"))
        if not detail_ok:
            raise ChainRefused("ALBUM_DETAIL_MISMATCH", "detail did not carry scene/prompt/time")
        state["detail_translation_source"] = str(
            (image.get("translation") or {}).get("source") or "")

        base_url = _base_url(profile)
        code, content_type, body = _get_bytes(
            base_url, "/api/phone/album/%s/image" % artifact_id)
        digest = hashlib.sha256(body).hexdigest() if body else ""
        http_ok = bool(code == 200 and content_type in ("image/png", "image/jpeg") and body
                       and int(media.get("bytes") or 0) == len(body)
                       and digest == str(media.get("sha256") or ""))
        state["image_http"] = {"status": int(code), "content_type": str(content_type),
                              "bytes": len(body), "sha256": digest}
        if not http_ok:
            raise ChainRefused("IMAGE_HTTP_MISMATCH",
                               "status=%s type=%s" % (code, content_type))
        _save_bytes(body, root)
        state["png_path"] = str(_photo_path(root))

        state.update({"status": "finished", "ok": True, "finished_at": _now()})
        _write_state(state_path, state)
        _print_facts(state, prefix="verify")
        return 0
    except ChainRefused as exc:
        state.update({"status": "failed", "ok": False, "finished_at": _now(),
                      "reason_code": exc.reason_code, "detail": exc.detail})
        _write_state(state_path, state)
        print("verify=FAIL")
        print("reason_code=%s" % exc.reason_code)
        print("detail=%s" % exc.detail)
        print("state=%s" % state_path)
        return 1
    except Exception as exc:  # never leak paths, tokens or raw exception text
        state.update({"status": "unknown", "ok": False, "finished_at": _now(),
                      "reason_code": "UNEXPECTED_ERROR", "error_type": type(exc).__name__})
        _write_state(state_path, state)
        print("verify=UNKNOWN")
        print("error_type=%s" % type(exc).__name__)
        print("state=%s" % state_path)
        return 1


def _print_facts(state: Mapping[str, Any], *, prefix: str) -> None:
    media = state.get("media") if isinstance(state.get("media"), Mapping) else {}
    http = state.get("image_http") if isinstance(state.get("image_http"), Mapping) else {}
    translation = state.get("translation") if isinstance(state.get("translation"), Mapping) else {}
    print("%s=OK" % prefix)
    print("artifact_id=%s" % state.get("artifact_id"))
    print("album_count=%s" % state.get("album_count"))
    print("source_label=%s" % state.get("source_label"))
    print("scene=%s" % state.get("scene"))
    print("caption=%s" % state.get("caption"))
    print("generation_prompt=%s" % state.get("generation_prompt"))
    print("translated=%s" % translation.get("translated"))
    print("translation_source=%s" % translation.get("source"))
    print("detail_translation_source=%s" % state.get("detail_translation_source"))
    print("created_at=%s" % state.get("created_at"))
    print("media_bytes=%s" % media.get("bytes"))
    print("media_sha256=%s" % media.get("sha256"))
    print("image_http_status=%s" % http.get("status"))
    print("image_http_bytes=%s" % http.get("bytes"))
    print("png=%s" % state.get("png_path"))
    print("product_model_calls=0")
    sys.stdout.flush()


# ---------------------------------------------------------------------------------------------
# readback: pure reads only, never a second capture
# ---------------------------------------------------------------------------------------------


def _finished_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ChainRefused("STATE_MISSING", str(path)) from None
    except ValueError:
        raise ChainRefused("STATE_NOT_JSON", str(path)) from None
    if not isinstance(state, dict) or state.get("status") != "finished" \
            or state.get("ok") is not True or not str(state.get("artifact_id") or ""):
        raise ChainRefused("STATE_NOT_FINISHED", "readback needs a finished verify record")
    return state


def _readback(args: argparse.Namespace) -> int:
    try:
        return _readback_checked(args)
    except ChainRefused:
        raise
    except Exception as exc:  # no capture, no retry: report the failure by type only
        print("readback=UNKNOWN")
        print("error_type=%s" % type(exc).__name__)
        print("new_inference=0")
        return 1


def _readback_checked(args: argparse.Namespace) -> int:
    root = Path(str(args.root)).expanduser()
    profile = _load_profile(root)
    state = _finished_state(root)
    artifact_id = str(state["artifact_id"])
    media = state.get("media") if isinstance(state.get("media"), Mapping) else {}

    listing = _tool(profile, "album")
    listed = listing.get("result") if isinstance(listing.get("result"), Mapping) else {}
    images = listed.get("images") if isinstance(listed.get("images"), list) else []
    row = images[0] if len(images) == 1 and isinstance(images[0], Mapping) else {}
    if not (listing.get("ok") is True and len(images) == 1
            and row.get("artifact_id") == artifact_id
            and row.get("caption") == state.get("caption")
            and row.get("generated_locally") is True
            and row.get("source_label") == "local_generated"):
        print("readback=FAIL")
        print("reason_code=ALBUM_ROW_MISMATCH")
        print("album_count=%d" % len(images))
        return 1

    detail_answer = _tool(profile, "album_image", artifact_id=artifact_id)
    image = detail_answer.get("image") if isinstance(detail_answer.get("image"),
                                                     Mapping) else {}
    if not (detail_answer.get("ok") is True
            and image.get("artifact_id") == artifact_id
            and image.get("scene") == state.get("scene")
            and image.get("generation_prompt") == state.get("generation_prompt")
            and image.get("created_at")):
        print("readback=FAIL")
        print("reason_code=ALBUM_DETAIL_MISMATCH")
        return 1

    base_url = _base_url(profile)
    code, content_type, body = _get_bytes(base_url, "/api/phone/album/%s/image" % artifact_id)
    digest = hashlib.sha256(body).hexdigest() if body else ""
    if not (code == 200 and content_type in ("image/png", "image/jpeg") and body
            and digest == str(media.get("sha256") or "")
            and len(body) == int(media.get("bytes") or 0)):
        print("readback=FAIL")
        print("reason_code=IMAGE_HTTP_MISMATCH")
        print("image_http_status=%s" % code)
        return 1

    print("readback=OK")
    print("artifact_id=%s" % artifact_id)
    print("album_count=%d" % len(images))
    print("source_label=%s" % row.get("source_label"))
    print("scene=%s" % image.get("scene"))
    print("caption=%s" % image.get("caption"))
    print("generation_prompt=%s" % image.get("generation_prompt"))
    detail_translation = (image.get("translation")
                          if isinstance(image.get("translation"), Mapping) else {})
    print("translation_source=%s" % detail_translation.get("source"))
    print("created_at=%s" % image.get("created_at"))
    print("media_bytes=%d" % len(body))
    print("media_sha256=%s" % digest)
    print("product_model_calls=0")
    print("new_inference=0")
    return 0


# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="one-shot camera chain fixture (serve / verify / readback)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="create a new root and serve the real phone HTTP")
    serve.add_argument("--root", required=True, help="a brand-new root (non-empty refused)")
    serve.add_argument("--port", type=int, default=3843, help="loopback HTTP port")
    verify = sub.add_parser("verify", help="one real camera call, then read it back")
    verify.add_argument("--root", required=True)
    readback = sub.add_parser("readback", help="read-only re-check of a finished verify")
    readback.add_argument("--root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "verify":
            return _verify(args)
        return _readback(args)
    except ChainRefused as exc:
        print("%s=REFUSED" % args.command)
        print("reason_code=%s" % exc.reason_code)
        if exc.detail:
            print("detail=%s" % exc.detail)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
