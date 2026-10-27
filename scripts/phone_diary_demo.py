#!/usr/bin/env python3
"""手机日记：真 Supply + 真 ``phone_handler`` 的最小可运行演示。

这不是一个新的 owner，也不复制一份日记存储：

* 日记条目写进这台实例**已有**的私人事项文档（``private_daily_items`` 的独立 item type），
  存储、版本、CAS 与权限仍归真实 Life Supply；
* 写/列/读全部经真实 ``chiyo_phone`` handler 走 loopback HTTP，正文由真实 owner 回读；
* operator 的普通读面（没有任何 actor 声明的 GET）只拿到时间/字数/数量；
* 正规停服后在同一根上重开 Supply 与手机服务，旧日记仍然可读。

脚本只打印保存/翻阅/隐私结果，**不打印任何日记正文**。运行（WSL/POSIX）：

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_diary_demo.py

夹具根保留在临时目录下，不删除、不清理。
"""
from __future__ import annotations

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

SUBJECT = "test:phone-diary-demo"
CSRF = "phone-diary-demo-csrf"
ARTIFACT_GRANT = "phone-diary-demo:artifact-grant"
WORKSPACE_KEY = "phone-diary-demo:workspace"
ITEMS_TITLE = "她的私人日常事项"

#: 合成演示文本，只在脚本内部用于比对，绝不打印。
FIRST_ENTRY = "合成日记第一则：风从窗缝里进来，我把灯往桌角挪了挪。"
SECOND_ENTRY = "合成日记第二则：今天把那页纸翻过去，什么也没写。"
BODIES = (FIRST_ENTRY, SECOND_ENTRY)


def _scratch_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-phone-diary" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-phone-diary")
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
            client.settimeout(20)
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
        "name": "phone-diary-demo",
        "subject": SUBJECT,
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-phone-diary-demo",
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
    """The real CLI profile ``chiyo_phone`` loads: loopback phone + entry binding."""

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


def _start_phone(instance: dict) -> tuple[object, threading.Thread, str]:
    service = phone_service.PhoneService(instance, environment={})
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


def _get(base_url: str, path: str, *, actor: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(base_url + path, method="GET")
    if actor:
        request.add_header("X-Phone-Actor", actor)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def _contains_body(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return any(body in text for body in BODIES)


def _new_root() -> Path:
    root = _scratch_base() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    root.mkdir(parents=True, exist_ok=False)
    return root


def _start_owner(root: Path, generation: int) -> Owner:
    socket_path = root / ("supply-%d-%d-%d.sock" % (os.getpid(), generation, time.time_ns()))
    return Owner(root / "data", socket_path, "phone-diary-demo:%d" % generation)


def main() -> int:
    if not hasattr(os, "geteuid"):
        print("result=SKIP_NOT_POSIX")
        return 2

    root = _new_root()
    owner = None
    httpd = None
    thread = None
    facts: dict[str, object] = {}
    try:
        # 1) real Supply + one private-items artifact (the diary lives inside it).
        owner = _start_owner(root, 1)
        workspace_id, grant_id = _provision(owner)
        items_id = _create_private_items(owner, workspace_id, grant_id)
        instance = _instance(root, owner, workspace_id, grant_id, items_id)

        httpd, thread, base_url = _start_phone(instance)
        profile = _write_tool_profile(root, base_url)

        # 2) the subject's own path: write two entries through the real tool handler.
        first = _tool(profile, "diary_write", body=FIRST_ENTRY,
                      operation_key="demo-diary:first")
        second = _tool(profile, "diary_write", body=SECOND_ENTRY,
                       operation_key="demo-diary:second")
        entries = first.get("result", {}).get("data", {})
        entries2 = second.get("result", {}).get("data", {})
        first_id = str(entries.get("id") or "")
        second_id = str(entries2.get("id") or "")
        facts["write_both_applied"] = bool(
            first.get("ok") and second.get("ok")
            and first.get("status") == "applied" and second.get("status") == "applied"
            and first_id and second_id and first_id != second_id)

        # 2b) the original key is idempotent; the same key with different words is refused.
        replay = _tool(profile, "diary_write", body=FIRST_ENTRY,
                       operation_key="demo-diary:first")
        swap = _tool(profile, "diary_write", body="换了一段不相关的话",
                     operation_key="demo-diary:first")
        swap_result = swap.get("result") if isinstance(swap.get("result"), dict) else {}
        facts["same_key_replay_no_duplicate"] = (
            replay.get("status") == "already_applied")
        facts["same_key_other_body_refused"] = bool(
            swap.get("ok") is False
            and str(swap_result.get("reason_code") or "") == "DIARY_OPERATION_KEY_REUSED")

        # 3) list by time, then read one entry's body back through the same tool path.
        listing = _tool(profile, "diary")
        list_result = listing.get("result") if isinstance(listing.get("result"), dict) else {}
        list_entries = list_result.get("entries") if isinstance(
            list_result.get("entries"), list) else []
        facts["list_count"] = int(list_result.get("count") or 0)
        facts["list_has_times"] = all(
            isinstance(row, dict) and row.get("created_at") for row in list_entries)
        facts["list_has_no_body"] = not _contains_body(list_result)
        facts["list_newest_first"] = bool(
            list_entries and list_entries[0].get("id") == second_id)

        read_back = _tool(profile, "diary_read", entry_id=first_id)
        read_entry = read_back.get("entry") if isinstance(read_back.get("entry"), dict) else {}
        facts["subject_read_body"] = read_entry.get("body") == FIRST_ENTRY
        facts["subject_read_private_text_flag"] = (
            (read_back.get("result") or {}).get("private_text_included") is True)

        # 4) the operator's ordinary path: no actor declaration, metadata only.
        op_code, op_entry = _get(base_url, "/api/phone/diary/" + first_id)
        op_body_withheld = (op_code == 200 and op_entry.get("ok") is True
                            and op_entry.get("body_withheld") is True
                            and "body" not in (op_entry.get("entry") or {})
                            and op_entry.get("private_text_included") is False)
        op_list_code, op_list = _get(base_url, "/api/phone/diary")
        op_list_meta = (op_list_code == 200 and op_list.get("ok") is True
                        and op_list.get("private_text_included") is False
                        and not _contains_body(op_list))
        facts["operator_body_withheld"] = bool(op_body_withheld)
        facts["operator_list_metadata_only"] = bool(op_list_meta)
        facts["operator_sees_dates"] = bool(
            op_list.get("count") == 2 and op_list.get("dates"))

        # 5) existing ordinary surfaces must not carry the diary body.
        surfaces = (
            "/api/phone/notes", "/api/phone/projects", "/api/phone/state",
            "/api/phone/today", "/api/phone/shares", "/api/phone/contact",
            # 私人事项 artifact 本身不是作品；即便直接点名它的项目/版本/来历/上下文读口，
            # 以及 Memory 搜索入口，也不能带出日记正文。
            "/api/phone/projects/" + items_id,
            "/api/phone/projects/" + items_id + "/versions",
            "/api/phone/projects/" + items_id + "/provenance",
            "/api/phone/projects/" + items_id + "/context",
            "/api/phone/memory/experiences?q=%E6%97%A5%E8%AE%B0",
        )
        leaked = []
        for path in surfaces:
            _code, doc = _get(base_url, path)
            if _contains_body(doc):
                leaked.append(path)
        facts["existing_surfaces_no_body"] = not leaked
        facts["surfaces_checked"] = len(surfaces)

        # 6) normal close, then reopen the same root and read the old entry again.
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

        restart_list = _tool(profile, "diary")
        restart_result = restart_list.get("result") if isinstance(
            restart_list.get("result"), dict) else {}
        restart_read = _tool(profile, "diary_read", entry_id=first_id)
        restart_entry = restart_read.get("entry") if isinstance(
            restart_read.get("entry"), dict) else {}
        facts["restart_still_readable"] = bool(
            restart_entry.get("body") == FIRST_ENTRY
            and int(restart_result.get("count") or 0) == 2)
        _op_code, op_after = _get(base_url, "/api/phone/diary/" + first_id)
        facts["restart_operator_still_withheld"] = bool(
            op_after.get("body_withheld") is True
            and "body" not in (op_after.get("entry") or {}))
    finally:
        _stop_phone(httpd, thread)
        if owner is not None:
            owner.stop()

    # Only results.  Body text is never printed by this script.
    print("write_both_applied=%s" % ("yes" if facts.get("write_both_applied") else "no"))
    print("same_key_replay_no_duplicate=%s"
          % ("yes" if facts.get("same_key_replay_no_duplicate") else "no"))
    print("same_key_other_body_refused=%s"
          % ("yes" if facts.get("same_key_other_body_refused") else "no"))
    print("list_count=%s" % facts.get("list_count"))
    print("list_has_times=%s" % ("yes" if facts.get("list_has_times") else "no"))
    print("list_newest_first=%s" % ("yes" if facts.get("list_newest_first") else "no"))
    print("list_has_no_body=%s" % ("yes" if facts.get("list_has_no_body") else "no"))
    print("subject_read_body=%s" % ("yes" if facts.get("subject_read_body") else "no"))
    print("operator_body_withheld=%s" % ("yes" if facts.get("operator_body_withheld") else "no"))
    print("operator_list_metadata_only=%s"
          % ("yes" if facts.get("operator_list_metadata_only") else "no"))
    print("operator_sees_dates=%s" % ("yes" if facts.get("operator_sees_dates") else "no"))
    print("existing_surfaces_no_body=%s"
          % ("yes" if facts.get("existing_surfaces_no_body") else "no"))
    print("restart_still_readable=%s"
          % ("yes" if facts.get("restart_still_readable") else "no"))
    print("restart_operator_still_withheld=%s"
          % ("yes" if facts.get("restart_operator_still_withheld") else "no"))
    print("root=%s" % root)
    ok = all(bool(facts.get(name)) for name in (
        "write_both_applied", "same_key_replay_no_duplicate", "same_key_other_body_refused",
        "list_has_times", "list_newest_first", "list_has_no_body", "subject_read_body",
        "operator_body_withheld", "operator_list_metadata_only", "operator_sees_dates",
        "existing_surfaces_no_body", "restart_still_readable",
        "restart_operator_still_withheld")) and int(facts.get("list_count") or 0) == 2
    print("demo=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
