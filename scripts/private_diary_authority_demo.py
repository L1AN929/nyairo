#!/usr/bin/env python3
"""私人日记本地能力凭据：真实生产 serve + 真实 Supply + 正式 ``chiyo_phone`` 工具。

这不是一个新 owner，也不改日记存储：日记仍写进实例已有的私人事项文档，读写仍归真实
Life Supply。演示只把“回环 HTTP 上任何客户端自称 ``X-Phone-Actor: model``”这条，换成本次
serve 签发、绑定 subject + 实际 endpoint 的不透明凭据：

* 正常 ``demo.serve_instance`` 启动后，凭据只出现在实例根的受管文件里，发布信息与 profile
  只有路径，没有 token；文件是真实 0600（POSIX）。
* 正式 ``phone_handler`` 从 profile 指向的文件读取凭据，写入/读回正文；同 key 幂等。
* operator 普通读面仍只有元数据；声明 model 但没有/用错凭据的读取被拒，写入零副作用。
* 凭据绑定 subject 与 endpoint：换 endpoint / 换 subject 时工具在发请求前拒绝，不把 token
  发给别的地址。
* 同一根正常停服再启动：新 token 生效，旧 token 被拒，原日记仍在；旧配置没有凭据时
  fail closed。
* 无法安全存放凭据时（受控故障注入）不留下 secret，私人日记明确不可用，普通元数据仍可读。

脚本只打印状态、拒绝码和计数，**绝不打印日记正文或 token**。运行（WSL/Linux）：

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/private_diary_authority_demo.py

夹具根留在临时目录下，不删除、不清理。
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import demo, server as phone_server, service as phone_service  # noqa: E402

#: One synthetic pair of sentences; used for assertions only and never printed.
FIRST_BODY = "合成日记第一则：把窗开了一条缝，风进来，灯没动。"
SECOND_BODY = "合成日记第二则：今天只翻了一页，没写别的。"


def _scratch_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-private-diary-capability" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-private-diary-capability")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _new_root() -> Path:
    # ``prepare_demo`` is the owner of creation: it refuses an existing root.
    root = _scratch_base() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    if root.exists():
        raise RuntimeError("fixture root already exists: %s" % root)
    return root


def _mode_is_private(path: Path) -> bool:
    if os.name == "nt":
        return False
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and (stat.S_IMODE(info.st_mode) & 0o077) == 0


def _write_profile(root: Path, base_url: str, capability_file: object = None) -> Path:
    profile = root / "profile"
    (profile / "chiyo").mkdir(parents=True, exist_ok=True)
    phone_section = {
        "enabled": True,
        "base_url": base_url,
        "profile_home": str(root),
        "write_enabled": True,
        "private_artifacts": True,
    }
    if capability_file is not None:
        phone_section["capability_file"] = str(capability_file)
    (profile / "chiyo" / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.geteuid(),
        "phone": phone_section,
    }, ensure_ascii=False), encoding="utf-8")
    return profile


def _tool(profile: Path, action: str, **fields) -> dict:
    args = {"action": action}
    args.update(fields)
    return json.loads(phone_tool.phone_handler(args, hermes_home=str(profile)))


class Serve:
    """One real ``demo.serve_instance`` in a background thread."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.stop = threading.Event()
        self.ready: dict = {}
        self.error: list[str] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            demo.serve_instance(self.root, port=0, stop_event=self.stop,
                                on_ready=lambda info: self.ready.update(info), announce=False)
        except Exception as exc:  # recorded, never hidden
            self.error.append("%s: %s" % (type(exc).__name__, exc))

    def start(self, timeout: float = 120.0) -> dict:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready.get("port") or self.error:
                break
            time.sleep(0.05)
        if self.error:
            raise RuntimeError("serve failed: %s" % self.error[0])
        if not self.ready.get("port"):
            raise RuntimeError("serve never became ready")
        return self.ready

    def stop_and_join(self, timeout: float = 120.0) -> None:
        self.stop.set()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            raise RuntimeError("serve did not stop after stop_event")


def _http(base: str, method: str, path: str, body: object = None, *,
          headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.loads(error.read())
        except ValueError:
            return int(error.code), {}


def _csrf(base: str) -> str:
    _code, doc = _http(base, "GET", "/api/phone/state")
    return str(doc.get("csrf_token") or "")


def _diary_count(base: str) -> int:
    code, doc = _http(base, "GET", "/api/phone/diary")
    return int(doc.get("count") or 0) if code == 200 and doc.get("ok") else -1


def _contains_body(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return any(body in text for body in (FIRST_BODY, SECOND_BODY))


class _TrapHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self.requests.append({key.lower(): value for key, value in self.headers.items()})
        payload = json.dumps({"ok": False, "reason_code": "TRAP"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TrapServer:
    """A loopback listener that records headers; used to prove no token goes elsewhere."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        _TrapHandler.requests = self.requests
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _TrapHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % int(self.httpd.server_address[1])

    def __enter__(self) -> "TrapServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


def _capability_refusal(
        base_url: str, capability_file: Path, expected_subject: str) -> str:
    """Call the tool's own pre-send gate and return its reason code (or ``SENT``)."""

    settings = {
        "base_url": base_url,
        "capability_file": str(capability_file),
    }
    state = {"instance": {"subject": expected_subject}}
    try:
        phone_tool._private_capability(settings, state)  # noqa: SLF001 - the demo's subject path
    except phone_tool.PhoneToolRefused as exc:
        return str(exc.reason_code)
    return "SENT"


def main() -> int:
    if not hasattr(os, "geteuid"):
        print("result=SKIP_NOT_POSIX")
        return 2
    root = _new_root()
    facts: dict[str, object] = {}
    serve: Serve | None = None
    serve2: Serve | None = None
    try:
        demo.prepare_demo(root, seed_work_artifact=False)
        instance = phone_service.load_instance(root)
        subject = str(instance.get("subject") or "")
        serve = Serve(root)
        info = serve.start()
        base = "http://127.0.0.1:%d" % int(info["port"])
        capability_file = Path(str(info.get("capability_file") or ""))
        capability = phone_capability.read_capability(capability_file)

        # 1) normal start: credential exists, is 0600, and never appears in public surfaces.
        facts["capability_ready"] = (
            info.get("capability_status") == "READY" and capability_file.exists())
        facts["capability_file_private"] = _mode_is_private(capability_file)
        facts["announcement_no_token"] = capability.token not in json.dumps(info, ensure_ascii=False)
        marker = root / "run" / "phone.serve.json"
        facts["serve_marker_no_token"] = (
            marker.exists() and capability.token not in marker.read_text(encoding="utf-8"))
        log_path = root / "logs" / "serve.log"
        facts["serve_log_no_token"] = (
            not log_path.exists()
            or capability.token not in log_path.read_text(encoding="utf-8", errors="replace"))

        profile = _write_profile(root, base, capability_file)
        facts["profile_no_token"] = capability.token not in (
            profile / "chiyo" / "config.json").read_text(encoding="utf-8")

        # 2) the formal tool path: write, replay, read back (assertions only, never printed).
        first = _tool(profile, "diary_write", body=FIRST_BODY, operation_key="cap:first")
        second = _tool(profile, "diary_write", body=SECOND_BODY, operation_key="cap:second")
        first_data = (first.get("result") or {}).get("data") or {}
        first_id = str(first_data.get("id") or "")
        facts["subject_write_applied"] = (
            first.get("ok") is True and first.get("status") == "applied" and bool(first_id)
            and second.get("ok") is True and second.get("status") == "applied")
        replay = _tool(profile, "diary_write", body=FIRST_BODY, operation_key="cap:first")
        facts["same_key_idempotent"] = replay.get("status") == "already_applied"
        read_back = _tool(profile, "diary_read", entry_id=first_id)
        read_entry = read_back.get("entry") if isinstance(read_back.get("entry"), dict) else {}
        facts["subject_read_body"] = read_entry.get("body") == FIRST_BODY
        facts["list_count"] = _diary_count(base)
        facts["list_count_two"] = facts["list_count"] == 2

        # 3) operator default: metadata only, no body anywhere.
        code, entry = _http(base, "GET", "/api/phone/diary/" + first_id)
        facts["operator_body_withheld"] = (
            code == 200 and entry.get("ok") is True and entry.get("body_withheld") is True
            and "body" not in (entry.get("entry") or {}) and not _contains_body(entry))
        list_code, listing = _http(base, "GET", "/api/phone/diary")
        facts["operator_list_metadata_only"] = (
            list_code == 200 and listing.get("private_text_included") is False
            and not _contains_body(listing))

        # 4) declared model without / with a wrong credential: refused, never an empty success.
        no_cred_code, no_cred = _http(
            base, "GET", "/api/phone/diary/" + first_id, headers={"X-Phone-Actor": "model"})
        facts["model_read_without_credential_refused"] = (
            no_cred_code == 403 and no_cred.get("ok") is False
            and str(no_cred.get("reason_code") or "").startswith("CAPABILITY_"))
        wrong_code, wrong = _http(
            base, "GET", "/api/phone/diary/" + first_id,
            headers={"X-Phone-Actor": "model", "Authorization": "Bearer not-the-token"})
        facts["model_read_wrong_token_refused"] = (
            wrong_code == 403 and str(wrong.get("reason_code")) == "CAPABILITY_INVALID")

        # A fake subject write (CSRF but no credential) must create zero entries/versions.
        csrf = _csrf(base)
        before = _diary_count(base)
        fake_code, fake = _http(
            base, "POST", "/api/phone/diary",
            {"body": "伪写入不应落地", "operation_key": "cap:fake"},
            headers={"X-CSRF-Token": csrf, "X-Phone-Actor": "model"})
        fake_wrong_code, fake_wrong = _http(
            base, "POST", "/api/phone/diary",
            {"body": "错凭据也不应落地", "operation_key": "cap:fake-wrong"},
            headers={"X-CSRF-Token": csrf, "X-Phone-Actor": "model",
                     "Authorization": "Bearer not-the-token"})
        after = _diary_count(base)
        facts["fake_write_refused"] = (
            fake_code == 403 and fake.get("ok") is False
            and fake_wrong_code == 403 and fake_wrong.get("ok") is False
            and before == after and before == 2)

        # 5) subject / endpoint binding: refused before any request could carry the token.
        with TrapServer() as trap:
            facts["endpoint_mismatch_refused"] = _capability_refusal(
                trap.base_url, capability_file, subject) == \
                "PHONE_CAPABILITY_ENDPOINT_MISMATCH"
            other_root = root / "other-subject"
            other_root.mkdir(parents=True, exist_ok=True)
            _other_path, _other = phone_capability.issue(
                other_root, subject="someone-else", endpoint=base)
            facts["subject_mismatch_refused"] = _capability_refusal(
                base, phone_capability.capability_path(other_root),
                subject) == "PHONE_CAPABILITY_SUBJECT_MISMATCH"
            facts["trap_received_no_request"] = trap.requests == []

        # 6) old configuration without a usable credential: fail closed, no write.
        missing_profile = _write_profile(
            root, base, root / "run" / "missing-capability.json")
        missing_read = _tool(missing_profile, "diary_read", entry_id=first_id)
        before_missing = _diary_count(base)
        missing_write = _tool(missing_profile, "diary_write", body=FIRST_BODY,
                              operation_key="cap:missing")
        after_missing = _diary_count(base)
        facts["missing_credential_read_refused"] = (
            missing_read.get("ok") is False
            and str(missing_read.get("reason_code") or "").startswith("PHONE_CAPABILITY_"))
        facts["missing_credential_write_zero_effect"] = (
            missing_write.get("ok") is False
            and str(missing_write.get("reason_code") or "").startswith("PHONE_CAPABILITY_")
            and before_missing == after_missing == 2)

        # 7) controlled insecure storage: no secret is left behind, private diary is unavailable.
        insecure_root = root / "insecure-storage"
        insecure_root.mkdir(parents=True, exist_ok=True)
        try:
            phone_capability.issue(
                insecure_root, subject=subject, endpoint=base,
                private_mode_check=lambda _path: False)
            insecure_refused = False
        except phone_capability.CapabilityUnavailable as exc:
            insecure_refused = exc.reason_code == "STORAGE_NOT_PRIVATE"
        insecure_path = phone_capability.capability_path(insecure_root)
        insecure_temp = list((insecure_root / "run").glob(".phone-capability.*.tmp"))
        facts["insecure_storage_refused"] = (
            insecure_refused and not insecure_path.exists() and not insecure_temp)

        # A server with no capability keeps metadata readable but refuses the private body.
        service = phone_service.PhoneService(phone_service.load_instance(root), environment={})
        uncapped = phone_server.create_server(service, csrf_token="private-diary-demo", port=0)
        uncapped_thread = threading.Thread(
            target=uncapped.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        uncapped_thread.start()
        try:
            uncapped_base = "http://127.0.0.1:%d" % int(uncapped.server_address[1])
            un_code, un_doc = _http(
                uncapped_base, "GET", "/api/phone/diary/" + first_id,
                headers={"X-Phone-Actor": "model"})
            meta_code, meta_doc = _http(uncapped_base, "GET", "/api/phone/diary/" + first_id)
            facts["unavailable_capability_fail_closed"] = (
                un_code == 403 and str(un_doc.get("reason_code")) == "CAPABILITY_UNAVAILABLE")
            facts["unavailable_capability_metadata_still_readable"] = (
                meta_code == 200 and meta_doc.get("body_withheld") is True)
        finally:
            uncapped.shutdown()
            uncapped.server_close()
            uncapped_thread.join(timeout=10)

        # 8) normal restart on the same root: new token works, old token is rejected, diary kept.
        old_token = capability.token
        serve.stop_and_join()
        facts["capability_file_removed_on_stop"] = not capability_file.exists()
        serve = None

        serve2 = Serve(root)
        info2 = serve2.start()
        base2 = "http://127.0.0.1:%d" % int(info2["port"])
        capability2 = phone_capability.read_capability(capability_file)
        facts["restart_new_token"] = capability2.token != old_token
        old_code, old_doc = _http(
            base2, "GET", "/api/phone/diary/" + first_id,
            headers={"X-Phone-Actor": "model", "Authorization": "Bearer " + old_token})
        facts["restart_old_token_rejected"] = (
            old_code == 403 and str(old_doc.get("reason_code")) == "CAPABILITY_INVALID")
        profile2 = _write_profile(root, base2, capability_file)
        restart_read = _tool(profile2, "diary_read", entry_id=first_id)
        restart_entry = (restart_read.get("entry")
                         if isinstance(restart_read.get("entry"), dict) else {})
        facts["restart_new_token_reads"] = restart_entry.get("body") == FIRST_BODY
        facts["restart_diary_kept"] = _diary_count(base2) == 2
        serve2.stop_and_join()
        serve2 = None
        facts["restart_clean_stop_no_file"] = not capability_file.exists()
    finally:
        for live in (serve, serve2):
            if live is not None:
                try:
                    live.stop_and_join()
                except Exception:
                    pass

    # Only statuses, refusal codes and counts.  No token, no body text.
    for name in (
        "capability_ready", "capability_file_private", "announcement_no_token",
        "serve_marker_no_token", "serve_log_no_token", "profile_no_token",
        "subject_write_applied", "same_key_idempotent", "subject_read_body",
        "list_count_two", "operator_body_withheld", "operator_list_metadata_only",
        "model_read_without_credential_refused", "model_read_wrong_token_refused",
        "fake_write_refused", "endpoint_mismatch_refused", "subject_mismatch_refused",
        "trap_received_no_request", "missing_credential_read_refused",
        "missing_credential_write_zero_effect", "insecure_storage_refused",
        "unavailable_capability_fail_closed",
        "unavailable_capability_metadata_still_readable", "capability_file_removed_on_stop",
        "restart_new_token", "restart_old_token_rejected", "restart_new_token_reads",
        "restart_diary_kept", "restart_clean_stop_no_file",
    ):
        print("%s=%s" % (name, "yes" if facts.get(name) else "no"))
    print("list_count=%s" % facts.get("list_count"))
    print("root=%s" % root)
    ok = all(bool(facts.get(name)) for name in (
        "capability_ready", "capability_file_private", "announcement_no_token",
        "serve_marker_no_token", "serve_log_no_token", "profile_no_token",
        "subject_write_applied", "same_key_idempotent", "subject_read_body",
        "list_count_two", "operator_body_withheld", "operator_list_metadata_only",
        "model_read_without_credential_refused", "model_read_wrong_token_refused",
        "fake_write_refused", "endpoint_mismatch_refused", "subject_mismatch_refused",
        "trap_received_no_request", "missing_credential_read_refused",
        "missing_credential_write_zero_effect", "insecure_storage_refused",
        "unavailable_capability_fail_closed",
        "unavailable_capability_metadata_still_readable", "capability_file_removed_on_stop",
        "restart_new_token", "restart_old_token_rejected", "restart_new_token_reads",
        "restart_diary_kept", "restart_clean_stop_no_file",
    ))
    print("demo=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
