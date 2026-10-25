#!/usr/bin/env python3
"""把一份真实可读的阅读材料装进**既有**日常实例，并把该实例的阅读候选指向它。

这是什么，不是什么
==================
* 材料仍是 Life Supply ``ManagedArtifact`` 的一个普通 artifact：本工具只调用既有
  ``LifeArtifactPort.create_artifact``（真实 owner 的 ``create_artifact`` 命令），不建 store、
  不写 SQLite/JSONL、不改任何 canonical 表。
* 这是**父级一次性工具**，不是新 owner、不是产品入口、不是第二个材料库。它默认只在
  ``install`` 时写两处**绑定元数据**：Life 实例文件里的 ``reading_artifact_id`` /
  ``CHIYO_LIFE_READING_ARTIFACT_ID``，以及手机实例文件里的 ``reading_artifact_id``。
  两份文件都只在内容真的需要变化时才写；写前各留一份字节备份。
* 旧材料、旧阅读凭据、私人事项停点、请求账本、run id、预算上限一律不碰。新 artifact 用**新**
  ``artifact_id``，所以旧的 ``source_id`` 取得/停点记录不会指向新正文。
* 真实模型一次都不调用；``install`` / ``verify`` 都只发 Supply owner 的只读/建件命令。
* ``verify`` 只读：不建 artifact、不改绑定、不写任何文件（它读 ``install`` 留在
  ``<root>/probe/reading-material-install.json`` 的工具回执来核对旧/新 id）。

服务归属
========
只启动本工具自己需要的 Supply 子进程（既有 ``phone.demo`` 子进程助手），``finally`` 里只停
自己启动的 pid；若实例当前 socket 上已有一个**活的** owner，就直接复用、**不拥有**也不停它。
不启动 World、不启动 resident、不抢 Life lease；若既有 lease 已被别的活进程持有，安装直接拒绝。

阅读材料身份
============
陶渊明《桃花源记》开篇节选，东晋原作，公版；正文逐字使用已核对的开篇，**不是全文**、不是
AI 生成、不是验收剧情。ManagedArtifact 只有 title/正文两种元数据，没有来源列；作者已写进
标题，完整来源 URL 与“节选/公版/operator 提供”的事实记在工具回执与报告里，不塞进正文。
不规定她必须读，也不替她记阅读位置。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from life_artifact_port import (  # noqa: E402
    LifeArtifactError,
    LifeArtifactPort,
    operation_key_for,
)

SCHEMA_INSTALL = "day_reading_material.install.v1"
SCHEMA_VERIFY = "day_reading_material.verify.v1"

RECEIPT_RELATIVE = ("probe", "reading-material-install.json")

#: 已核对的开篇原文（维基文库同一段），逐字使用；不添加任何任务指令。
MATERIAL_TITLE = "陶渊明《桃花源记》开篇"
MATERIAL_AUTHOR = "陶渊明（东晋）"
MATERIAL_WORK = "桃花源记"
MATERIAL_SOURCE = ("https://zh.wikisource.org/zh-hans/"
                   "%E6%A1%83%E8%8A%B1%E6%BA%90%E8%A8%98")
MATERIAL_EXCERPT = (
    "晋太元中，武陵人捕鱼为业。缘溪行，忘路之远近。忽逢桃花林，夹岸数百步，中无杂树，"
    "芳草鲜美，落英缤纷。渔人甚异之。复前行，欲穷其林。"
)
MATERIAL_PROVIDED_BY = "operator"
MATERIAL_EXCERPT_NOTE = "开篇节选，不是全文；东晋原作，公版；由 operator 提供，不是她的创作或选择。"

#: 新 artifact 的幂等键：同一份材料 = 同一个键，重试不会新造一版/一件。
MATERIAL_OPERATION_PARTS = ("day-reading-material", "v1", MATERIAL_TITLE, MATERIAL_EXCERPT)

EXIT_OK = 0
EXIT_REFUSED = 1


class ReadingMaterialError(RuntimeError):
    """A named refusal.  Nothing is faked and no binding is half-claimed."""


class OwnerSession:
    """One usable Supply owner socket: either a live owner (not ours) or our own child."""

    def __init__(self, socket_path: str, *, generation: Optional[str], pid: Optional[int],
                 owned: bool, stop_fn: Optional[Callable[[], Mapping[str, Any]]] = None,
                 reused: bool = False, log_path: Optional[str] = None) -> None:
        self.socket_path = str(socket_path)
        self.generation = generation
        self.pid = pid
        self.owned = bool(owned)
        self.reused = bool(reused)
        self.log_path = log_path
        self._stop_fn = stop_fn
        self._stopped: Optional[dict[str, Any]] = None

    def stop(self) -> dict[str, Any]:
        if self._stopped is not None:
            return dict(self._stopped)
        if self._stop_fn is None:
            self._stopped = {"name": "supply", "owned": self.owned, "stopped": False,
                             "reused": self.reused, "pid": self.pid}
        else:
            detail = dict(self._stop_fn() or {})
            self._stopped = {"name": "supply", "owned": self.owned, "reused": self.reused,
                             "stopped": True, "pid": self.pid, **detail}
        return dict(self._stopped)

    def view(self) -> dict[str, Any]:
        return {"name": "supply", "socket": self.socket_path,
                "generation": self.generation, "pid": self.pid,
                "owned_by_this_tool": self.owned, "reused_live_owner": self.reused,
                "log": self.log_path}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _exit_code(value: Any, default: int = EXIT_REFUSED) -> int:
    """Keep a real ``0``; a missing/``None`` code still fails closed."""

    if value is None or isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def material_content_sha256() -> str:
    return sha256(MATERIAL_EXCERPT.encode("utf-8")).hexdigest()


def material_identity() -> dict[str, Any]:
    return {
        "title": MATERIAL_TITLE,
        "author": MATERIAL_AUTHOR,
        "work": MATERIAL_WORK,
        "source": MATERIAL_SOURCE,
        "excerpt": MATERIAL_EXCERPT,
        "excerpt_chars": len(MATERIAL_EXCERPT),
        "content_sha256": material_content_sha256(),
        "excerpt_only": True,
        "full_text_claimed": False,
        "public_domain": True,
        "provided_by": MATERIAL_PROVIDED_BY,
        "model_generated": False,
        "acceptance_fixture_story": False,
        "note": MATERIAL_EXCERPT_NOTE,
    }


def _material_operation_key(subject: str, workspace_id: str) -> str:
    return operation_key_for(*MATERIAL_OPERATION_PARTS, subject, workspace_id,
                             material_content_sha256())


# ---------------------------------------------------------------------------------------------
# owner session (reuse a live owner, otherwise start and stop our own child)
# ---------------------------------------------------------------------------------------------


def _default_service_factory(root: Path, *, generation: str,
                             writer_instance: str) -> OwnerSession:
    from components.phone import demo, service as phone_service

    child = demo._supply_child(root, writer_instance=writer_instance, generation=generation)
    child.start()
    socket_path = str(child.socket_path)
    try:
        if not phone_service.wait_for_socket(socket_path, timeout=demo.CHILD_READY_TIMEOUT):
            raise ReadingMaterialError(
                "SUPPLY_SOCKET_TIMEOUT: %s" % (root / "logs" / "supply.log"))
        if not phone_service.supply_ready(socket_path):
            raise ReadingMaterialError("SUPPLY_NOT_READY")
    except Exception:
        child.stop()
        raise
    return OwnerSession(socket_path, generation=generation, pid=child.pid, owned=True,
                        stop_fn=child.stop, log_path=str(root / "logs" / "supply.log"))


def _open_owner(root: Path, phone: Mapping[str, Any], *,
                service_factory: Optional[Callable[..., OwnerSession]] = None) -> OwnerSession:
    """Use the instance's live owner when it answers; otherwise start exactly one of our own."""

    from components.phone import service as phone_service

    if service_factory is None:
        live = str(phone.get("supply_socket") or "").strip()
        if live and phone_service.supply_ready(live):
            return OwnerSession(live, generation=phone.get("supply_generation"), pid=None,
                                owned=False, reused=True)
    generation = "reading-%d-%d" % (int(time.time()), os.getpid())
    factory = service_factory or _default_service_factory
    return factory(root, generation=generation,
                   writer_instance="day-reading-material:%d" % os.getpid())


# ---------------------------------------------------------------------------------------------
# instance facts, snapshots, atomic metadata writes
# ---------------------------------------------------------------------------------------------


def _modules():
    from components.phone import life as phone_life
    from components.phone import service as phone_service

    import life_request_budget as budget
    import one_day_life as odl

    return phone_service, phone_life, odl, budget


def _load_instance(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    phone_service, phone_life, _odl, _budget = _modules()
    try:
        phone = dict(phone_service.load_instance(root))
    except phone_service.PhoneServiceError as exc:
        raise ReadingMaterialError("PHONE_INSTANCE_UNREADABLE: %s" % exc) from exc
    try:
        nested = phone_life.read_nested_life(root)
    except phone_life.PhoneLifeError as exc:
        raise ReadingMaterialError("NESTED_LIFE_INSTANCE_INVALID: %s" % exc) from exc
    if nested is None:
        raise ReadingMaterialError("NESTED_LIFE_INSTANCE_MISSING")
    return phone, nested


def _nested_paths(root: Path, nested: Mapping[str, Any]):
    _phone_service, phone_life, odl, _budget = _modules()
    directory = phone_life.nested_life_dir(root)
    return directory, odl.instance_path(directory), phone_life.life_state_root(root, nested)


def _receipt_path(root: Path) -> Path:
    return root.joinpath(*RECEIPT_RELATIVE)


def _read_receipt(root: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(_receipt_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_INSTALL:
        return None
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def _backup_file(path: Path) -> Path:
    backup = path.with_name("%s-backup-%s" % (path.name, _now_iso().replace(":", "")))
    counter = 1
    while backup.exists():
        backup = path.with_name("%s-backup-%s-%d"
                                % (path.name, _now_iso().replace(":", ""), counter))
        counter += 1
    shutil.copy2(path, backup)
    return backup


def _ledger_view(state_root: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    """Read the persisted request ledger without reserving or opening a window."""

    _phone_service, _phone_life, _odl, budget = _modules()
    path = budget.budget_path(state_root)
    if not path.exists():
        return {"state": "ABSENT", "utc_day_calls": None, "run_window_calls": None}
    try:
        ledger = budget.LifeRequestBudget(
            state_root,
            utc_day_max=_int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")),
            run_window_max=_int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")),
            run_id=environment.get("CHIYO_AUTONOMOUS_RUN_ID"))
        view = ledger.view()
    except Exception as exc:
        return {"state": "UNREADABLE", "error_type": type(exc).__name__}
    return {key: view.get(key) for key in
            ("state", "utc_day", "utc_day_calls", "utc_day_max", "run_id",
             "run_window_calls", "run_window_max")}


def _ledger_view_readonly(state_root: Path, environment: Mapping[str, str]
                          ) -> dict[str, Any]:
    """Read the ledger JSON without taking its lock or creating a lock file.

    ``verify`` promises zero writes; ``LifeRequestBudget.view()`` would open ``budget.lock``
    with ``a+``.  This reads the same persisted fields the budget's own read view reports.
    """

    _phone_service, _phone_life, _odl, budget = _modules()
    path = budget.budget_path(state_root)
    if not path.exists():
        return {"state": "ABSENT", "utc_day_calls": None, "run_window_calls": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"state": "UNREADABLE", "error_type": type(exc).__name__}
    if not isinstance(data, dict):
        return {"state": "UNREADABLE", "error_type": "NOT_AN_OBJECT"}
    stored = str(data.get("active_day") or "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    active = max(stored, today) if stored else today
    run_id = environment.get("CHIYO_AUTONOMOUS_RUN_ID")
    window = (data.get("windows") or {}).get(run_id) if run_id else None
    return {
        "state": "OK" if (data.get("days") or data.get("windows")) else "ABSENT",
        "utc_day": active,
        "utc_day_calls": int((data.get("days") or {}).get(active) or 0),
        "run_id": run_id,
        "run_window_calls": (int((window or {}).get("calls") or 0)
                             if window is not None else None),
    }


def _run_snapshot(nested: Mapping[str, Any], state_root: Path, *,
                  read_only: bool = False) -> dict[str, Any]:
    environment = dict(nested.get("environment") or {})
    ledger = (_ledger_view_readonly(state_root, environment) if read_only
              else _ledger_view(state_root, environment))
    return {
        "run_id": environment.get("CHIYO_AUTONOMOUS_RUN_ID"),
        "ceilings": {
            "utc_day": _int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")),
            "run_window": _int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")),
        },
        "ledger": ledger,
    }


def _lease(state_root: Path, probe: Optional[Callable[[Path], Mapping[str, Any]]]) -> dict[str, Any]:
    if probe is not None:
        return dict(probe(state_root))
    _phone_service, phone_life, _odl, _budget = _modules()
    return dict(phone_life.check_single_lease(state_root))


def _services_report(session: Optional[OwnerSession]) -> Any:
    """What the tool started/reused and whether it stopped it.  ``None`` = never opened."""

    return None if session is None else [session.view(), session.stop()]


def _refused(reason: str, detail: str = "", *, schema: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": schema, "ok": False, "status": "BLOCKED", "exit_code": EXIT_REFUSED,
        "reason": reason, "detail": detail, "model_called": False, "request_sent": False,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------------------------
# artifact readback
# ---------------------------------------------------------------------------------------------


def _read_material(port: Any, artifact_id: str) -> dict[str, Any]:
    view = port.read_artifact(artifact_id)
    artifact = dict(view.get("artifact") or {})
    head = dict(view.get("head") or {})
    content = view.get("content")
    return {
        "artifact_id": str(artifact.get("artifact_id") or artifact_id),
        "title": str(artifact.get("title") or ""),
        "lifecycle": artifact.get("lifecycle"),
        "revision": artifact.get("revision"),
        "version_id": head.get("version_id"),
        "version_number": head.get("version_number"),
        "content": content if isinstance(content, str) else None,
        "content_chars": len(content) if isinstance(content, str) else None,
        "content_sha256": (sha256(content.encode("utf-8")).hexdigest()
                           if isinstance(content, str) else None),
    }


def _material_matches(facts: Mapping[str, Any], *, title: str, content_sha256: str
                      ) -> bool:
    return (str(facts.get("title") or "") == str(title)
            and str(facts.get("content_sha256") or "") == str(content_sha256))


# ---------------------------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------------------------


def _bindings_view(phone: Mapping[str, Any], nested: Mapping[str, Any]) -> dict[str, Any]:
    environment = dict(nested.get("environment") or {})
    return {
        "phone_field": phone.get("reading_artifact_id"),
        "nested_top_level": nested.get("reading_artifact_id"),
        "nested_environment": environment.get("CHIYO_LIFE_READING_ARTIFACT_ID"),
    }


def _binding_changes(before: Mapping[str, Any], new_id: str) -> dict[str, bool]:
    return {
        "phone_field": str(before.get("phone_field") or "") != str(new_id),
        "nested_top_level": str(before.get("nested_top_level") or "") != str(new_id),
        "nested_environment": str(before.get("nested_environment") or "") != str(new_id),
    }


def _write_bindings(root: Path, *, phone: Mapping[str, Any], nested: Mapping[str, Any],
                    new_id: str, changes: Mapping[str, bool]) -> dict[str, Any]:
    """Write only the binding metadata that actually differs; back up each file first."""

    phone_service, phone_life, _odl, _budget = _modules()
    written: dict[str, Any] = {"phone": None, "nested": None, "backups": [],
                               "nested_written": False, "phone_written": False}

    if changes.get("nested_top_level") or changes.get("nested_environment"):
        directory, instance_file, _state_root = _nested_paths(root, nested)
        payload = dict(nested)
        payload["reading_artifact_id"] = str(new_id)
        environment = dict(payload.get("environment") or {})
        environment["CHIYO_LIFE_READING_ARTIFACT_ID"] = str(new_id)
        payload["environment"] = environment
        written["backups"].append(str(_backup_file(instance_file)))
        # The same atomic writer the existing instance-memory opt-in uses: only this payload's
        # fields change, the file mode is preserved and no other instance is touched.
        written["nested"] = str(phone_life._write_instance_file(directory, payload))
        written["nested_written"] = True

    if changes.get("phone_field"):
        payload = dict(phone)
        payload["reading_artifact_id"] = str(new_id)
        written["backups"].append(str(_backup_file(phone_service.instance_path(root))))
        written["phone"] = str(phone_service.write_instance(root, payload))
        written["phone_written"] = True
    return written


def install(root: Path | str, *, service_factory: Optional[Callable[..., OwnerSession]] = None,
            lease_probe: Optional[Callable[[Path], Mapping[str, Any]]] = None
            ) -> dict[str, Any]:
    """Install the operator-provided excerpt and point this instance's reading candidate at it."""

    root_path = Path(root).expanduser()
    if not root_path.exists():
        return _refused("ROOT_MISSING",
                        "the existing instance root does not exist; this tool never creates one",
                        schema=SCHEMA_INSTALL)
    if not root_path.is_dir():
        return _refused("ROOT_NOT_A_DIRECTORY", "the root is not a directory",
                        schema=SCHEMA_INSTALL)
    try:
        phone, nested = _load_instance(root_path)
    except ReadingMaterialError as exc:
        return _refused(str(exc), "", schema=SCHEMA_INSTALL)

    _directory, _instance_file, state_root = _nested_paths(root_path, nested)
    lease = _lease(state_root, lease_probe)
    if str(lease.get("status")) == "HELD":
        return _refused(
            "LEASE_HELD",
            "another live process holds this instance's writer lease; the binding is not "
            "changed while a resident may be reading the old material",
            schema=SCHEMA_INSTALL, lease=lease)

    before_run = _run_snapshot(nested, state_root)
    before_bindings = _bindings_view(phone, nested)
    subject = str(phone.get("subject") or nested.get("subject_id") or "")
    workspace_id = str(phone.get("workspace_id") or nested.get("workspace_id") or "")
    environment = dict(nested.get("environment") or {})
    grant_id = str(phone.get("artifact_grant_id")
                   or environment.get("NYA02_ARTIFACT_GRANT")
                   or environment.get("CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT") or "")
    material = material_identity()

    base: dict[str, Any] = {
        "root": str(root_path),
        "material": material,
        "owner": {"subject": subject, "workspace_id": workspace_id},
        "previous": None,
        "reading_artifact_id": None,
        "version_id": None,
        "version_number": None,
        "changed": {"artifact_created": False, "binding_written": False},
        "bindings": {"before": before_bindings, "after": before_bindings,
                     "changes": {"phone_field": False, "nested_top_level": False,
                                 "nested_environment": False}},
        "writes": {"product_truth": False, "artifact_content": True,
                   "instance_binding_metadata": False, "tool_receipt": True,
                   "old_material_modified": False, "reading_history_modified": False,
                   "ledger_modified": False, "run_window_changed": False},
        "run": {"before": before_run, "after": before_run, "same_run_id": True,
                "ceilings_unchanged": True, "ledger_unchanged": True},
        "services": None,
        "lease": lease,
        "model_called": False,
        "request_sent": False,
    }
    if not subject or not workspace_id or not grant_id:
        return _refused("INSTANCE_BINDING_INCOMPLETE",
                        "subject/workspace/artifact grant is missing; the owner cannot be asked",
                        schema=SCHEMA_INSTALL, **base)

    receipt = _read_receipt(root_path)
    session: Optional[OwnerSession] = None
    try:
        session = _open_owner(root_path, phone, service_factory=service_factory)
        port = LifeArtifactPort(session.socket_path, subject)

        # -- idempotent path: the receipt's artifact is still the bound one and still matches --
        if receipt is not None:
            installed_id = str(receipt.get("reading_artifact_id") or "")
            already = (_binding_changes(before_bindings, installed_id)
                       if installed_id else {})
            if installed_id and not any(already.values()):
                try:
                    facts = _read_material(port, installed_id)
                except Exception:
                    facts = None
                if facts is not None and _material_matches(
                        facts, title=str((receipt.get("material") or {}).get("title") or ""),
                        content_sha256=str(
                            (receipt.get("material") or {}).get("content_sha256") or "")):
                    report = {"schema": SCHEMA_INSTALL, **base}
                    report.update({
                        "ok": True, "status": "ALREADY_INSTALLED",
                        "exit_code": EXIT_OK,
                        "installed_at": receipt.get("installed_at"),
                        "checked_at": _now_iso(),
                        "previous": receipt.get("previous"),
                        "reading_artifact_id": installed_id,
                        "version_id": facts.get("version_id"),
                        "version_number": facts.get("version_number"),
                        "readback": _public_material_facts(facts),
                        "changed": {"artifact_created": False, "binding_written": False},
                        "writes": {**base["writes"], "artifact_content": False,
                                   "tool_receipt": False},
                        "services": _services_report(session),
                        "lease": lease,
                        "note": ("同一份材料、同一个 artifact、同一绑定：第二次 install 没有新建、"
                                 "没有改绑定、没有动阅读位置或任何历史"),
                    })
                    return report

        # -- create (or replay) exactly one artifact through the real owner -------------------
        key = _material_operation_key(subject, workspace_id)
        existing_row = port.operation_row(key)
        outcome = port.create_artifact(workspace_id=workspace_id, title=MATERIAL_TITLE,
                                       content=MATERIAL_EXCERPT, grant_id=grant_id,
                                       operation_key=key)
        if not outcome.applied:
            reason = ("OPERATION_KEY_CONFLICT" if outcome.effect_state == "CONFLICT"
                      else "CREATE_REFUSED")
            context = dict(base)
            context["services"] = _services_report(session)
            return _refused(reason, "owner_status=%s detail=%s"
                            % (outcome.status, str(outcome.detail or outcome.reason)[:160]),
                            schema=SCHEMA_INSTALL, **context)
        new_id = str(outcome.artifact_id or "")
        if not new_id:
            context = dict(base)
            context["services"] = _services_report(session)
            return _refused("CREATE_WITHOUT_ARTIFACT_ID", "the owner answered OK without an id",
                            schema=SCHEMA_INSTALL, **context)

        # -- read the real head back before binding anything ---------------------------------
        facts = _read_material(port, new_id)
        if not _material_matches(facts, title=MATERIAL_TITLE,
                                 content_sha256=material["content_sha256"]):
            context = dict(base)
            context.update({"reading_artifact_id": new_id,
                            "readback": _public_material_facts(facts),
                            "services": _services_report(session)})
            return _refused("READBACK_MISMATCH",
                            "the owner's own readback does not match the installed excerpt; "
                            "the binding was not changed",
                            schema=SCHEMA_INSTALL, **context)

        previous = None
        old_id = str(before_bindings.get("phone_field") or "")
        if old_id and old_id != new_id:
            try:
                old_facts = _read_material(port, old_id)
            except Exception:
                old_facts = None
            if old_facts is not None:
                previous = {
                    "artifact_id": old_id,
                    "title": old_facts.get("title"),
                    "version_id": old_facts.get("version_id"),
                    "version_number": old_facts.get("version_number"),
                    "content_sha256": old_facts.get("content_sha256"),
                    "content_chars": old_facts.get("content_chars"),
                }
        if previous is None and receipt is not None:
            previous = receipt.get("previous")
    except ReadingMaterialError as exc:
        context = dict(base)
        context["services"] = _services_report(session)
        return _refused("SUPPLY_OWNER_UNAVAILABLE", str(exc)[:240],
                        schema=SCHEMA_INSTALL, **context)
    except (LifeArtifactError, OSError) as exc:
        context = dict(base)
        context["services"] = _services_report(session)
        return _refused("SUPPLY_OPERATION_FAILED",
                        "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                        schema=SCHEMA_INSTALL, **context)
    finally:
        if session is not None:
            session.stop()

    # -- write the binding metadata only after both files and the owner agree ----------------
    changes = _binding_changes(before_bindings, new_id)
    try:
        written = _write_bindings(root_path, phone=phone, nested=nested, new_id=new_id,
                                  changes=changes)
    except Exception as exc:
        context = dict(base)
        context.update({"reading_artifact_id": new_id,
                        "readback": _public_material_facts(facts),
                        "previous": previous,
                        "services": _services_report(session)})
        report = _refused("BINDING_WRITE_FAILED", "%s: %s" % (type(exc).__name__, exc),
                          schema=SCHEMA_INSTALL, **context)
        report["changed"] = {"artifact_created": existing_row is None,
                             "binding_written": None,
                             "bindings_may_be_partial": True}
        report["previous"] = previous
        return report

    after_bindings = {"phone_field": new_id, "nested_top_level": new_id,
                      "nested_environment": new_id}
    # Re-read both files so the after snapshot is the files' own fact, not the intended write.
    try:
        _phone, nested_after = _load_instance(root_path)
        after_run = _run_snapshot(nested_after, state_root)
        after_bindings = _bindings_view(phone, nested_after)
        after_bindings["phone_field"] = _load_phone_reading(root_path)
    except ReadingMaterialError:
        nested_after = nested

    material_fact = {key: material[key] for key in
                     ("title", "author", "work", "source", "excerpt_chars", "content_sha256",
                      "excerpt_only", "full_text_claimed", "public_domain", "provided_by",
                      "model_generated", "acceptance_fixture_story", "note")}
    report = {
        "schema": SCHEMA_INSTALL,
        "ok": True,
        "status": "INSTALLED",
        "exit_code": EXIT_OK,
        "installed_at": _now_iso(),
        "root": str(root_path),
        "material": material_fact,
        "owner": {"subject": subject, "workspace_id": workspace_id},
        "previous": previous,
        "reading_artifact_id": new_id,
        "version_id": facts.get("version_id"),
        "version_number": facts.get("version_number"),
        "readback": _public_material_facts(facts),
        "operation_key": key,
        "changed": {"artifact_created": existing_row is None,
                    "artifact_replayed": existing_row is not None,
                    "binding_written": bool(written.get("nested_written")
                                            or written.get("phone_written")),
                    "nested_written": bool(written.get("nested_written")),
                    "phone_written": bool(written.get("phone_written"))},
        "bindings": {"before": before_bindings, "after": after_bindings, "changes": changes},
        "backups": list(written.get("backups") or []),
        "writes": {"product_truth": False, "artifact_content": True,
                   "instance_binding_metadata": bool(written.get("nested_written")
                                                     or written.get("phone_written")),
                   "tool_receipt": True,
                   "old_material_modified": False, "reading_history_modified": False,
                   "ledger_modified": False, "run_window_changed": False},
        "run": {"before": before_run, "after": after_run,
                "same_run_id": before_run.get("run_id") == after_run.get("run_id"),
                "ceilings_unchanged": before_run.get("ceilings") == after_run.get("ceilings"),
                "ledger_unchanged": (before_run.get("ledger") or {}).get("utc_day_calls")
                == (after_run.get("ledger") or {}).get("utc_day_calls")
                and (before_run.get("ledger") or {}).get("run_window_calls")
                == (after_run.get("ledger") or {}).get("run_window_calls")},
        "services": _services_report(session),
        "lease": lease,
        "model_called": False,
        "request_sent": False,
        "note": ("operator 提供的公版开篇节选装成该实例的阅读材料，并把阅读候选指向它；"
                 "旧材料/旧阅读凭据/账本/run 预算均未改，也不要求她读"),
    }
    receipt_payload = {
        "schema": SCHEMA_INSTALL,
        "installed_at": report["installed_at"],
        "root": str(root_path),
        "material": material_fact,
        "previous": previous,
        "reading_artifact_id": new_id,
        "version_id": facts.get("version_id"),
        "version_number": facts.get("version_number"),
        "operation_key": key,
        "run": {"run_id": after_run.get("run_id"), "ceilings": after_run.get("ceilings"),
                "ledger": after_run.get("ledger")},
        "model_called": False,
    }
    report["probe_report"] = str(_write_json_atomic(_receipt_path(root_path), receipt_payload))
    return report


def _public_material_facts(facts: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if facts is None:
        return None
    return {key: facts.get(key) for key in
            ("artifact_id", "title", "lifecycle", "revision", "version_id", "version_number",
             "content_chars", "content_sha256")}


def _load_phone_reading(root: Path) -> Optional[str]:
    from components.phone import service as phone_service

    try:
        return phone_service.load_instance(root).get("reading_artifact_id")
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# verify (read-only)
# ---------------------------------------------------------------------------------------------


def verify(root: Path | str, *, service_factory: Optional[Callable[..., OwnerSession]] = None,
           lease_probe: Optional[Callable[[Path], Mapping[str, Any]]] = None
           ) -> dict[str, Any]:
    """Read the real owner and the two bindings back.  No create, no write, no model."""

    root_path = Path(root).expanduser()
    if not root_path.exists():
        return _refused("ROOT_MISSING", "the instance root does not exist",
                        schema=SCHEMA_VERIFY, read_only=True, writes=False)
    try:
        phone, nested = _load_instance(root_path)
    except ReadingMaterialError as exc:
        return _refused(str(exc), "", schema=SCHEMA_VERIFY, read_only=True, writes=False)
    receipt = _read_receipt(root_path)
    if receipt is None:
        return _refused("NO_INSTALL_RECEIPT",
                        "no %s; verify cannot name the previous material without it"
                        % (_receipt_path(root_path),),
                        schema=SCHEMA_VERIFY, read_only=True, writes=False)

    _directory, _instance_file, state_root = _nested_paths(root_path, nested)
    lease = _lease(state_root, lease_probe)
    expected_id = str(receipt.get("reading_artifact_id") or "")
    expected_material = dict(receipt.get("material") or {})
    before_run = dict(receipt.get("run") or {})
    subject = str(phone.get("subject") or nested.get("subject_id") or "")
    bindings = _bindings_view(phone, nested)
    current_run = _run_snapshot(nested, state_root, read_only=True)
    environment = dict(nested.get("environment") or {})

    session: Optional[OwnerSession] = None
    try:
        session = _open_owner(root_path, phone, service_factory=service_factory)
        port = LifeArtifactPort(session.socket_path, subject)
        try:
            facts = _read_material(port, expected_id) if expected_id else None
        except Exception:
            # A missing/unreadable material is a failed check, not a crashed verifier.
            facts = None
        previous = receipt.get("previous")
        old_facts = None
        old_versions = None
        if isinstance(previous, Mapping) and previous.get("artifact_id"):
            try:
                old_facts = _read_material(port, str(previous["artifact_id"]))
            except Exception:
                old_facts = None
            try:
                old_versions = port.list_versions(str(previous["artifact_id"]))
            except Exception:
                old_versions = None
        try:
            artifacts = port.list_artifacts()
            artifact_ids = sorted(str(row.get("artifact_id") or "") for row in artifacts)
        except Exception:
            artifact_ids = None
    except ReadingMaterialError as exc:
        return _refused("SUPPLY_OWNER_UNAVAILABLE", str(exc)[:240],
                        schema=SCHEMA_VERIFY, read_only=True, writes=False, lease=lease)
    except (LifeArtifactError, OSError) as exc:
        return _refused("SUPPLY_OPERATION_FAILED",
                        "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                        schema=SCHEMA_VERIFY, read_only=True, writes=False, lease=lease)
    finally:
        if session is not None:
            session.stop()

    checks = {
        "material_readable": facts is not None,
        "title_matches": bool(facts) and str(facts.get("title") or "")
        == str(expected_material.get("title") or ""),
        "content_matches": bool(facts) and str(facts.get("content_sha256") or "")
        == str(expected_material.get("content_sha256") or ""),
        "version_matches_receipt": bool(facts)
        and str(facts.get("version_id") or "") == str(receipt.get("version_id") or ""),
        "phone_binding_points_to_material": str(bindings.get("phone_field") or "")
        == expected_id,
        "nested_binding_points_to_material": str(bindings.get("nested_top_level") or "")
        == expected_id,
        "nested_environment_points_to_material": str(bindings.get("nested_environment") or "")
        == expected_id,
        "previous_material_readable": old_facts is not None
        if isinstance(previous, Mapping) and previous.get("artifact_id") else None,
        "previous_head_unchanged": (not isinstance(previous, Mapping)
                                    or not previous.get("artifact_id")
                                    or (old_facts is not None
                                        and str(old_facts.get("version_id") or "")
                                        == str(previous.get("version_id") or ""))),
        "previous_content_unchanged": (not isinstance(previous, Mapping)
                                       or not previous.get("content_sha256")
                                       or (old_facts is not None
                                           and str(old_facts.get("content_sha256") or "")
                                           == str(previous.get("content_sha256") or ""))),
        "same_run_id": bool(before_run.get("run_id"))
        and str(current_run.get("run_id") or "") == str(before_run.get("run_id") or ""),
        "ceilings_unchanged": before_run.get("ceilings") == current_run.get("ceilings"),
        "ledger_not_reset": _ledger_not_reset(before_run.get("ledger"),
                                              current_run.get("ledger")),
        "no_new_run_window": not bool(environment.get("CHIYO_AUTONOMOUS_NEW_RUN_WINDOW")),
    }
    # A missing previous material is "not applicable" only when the instance never had one.
    if checks["previous_material_readable"] is None:
        checks.pop("previous_material_readable")

    failures = sorted(name for name, value in checks.items() if value is False)
    ok = not failures
    report: dict[str, Any] = {
        "schema": SCHEMA_VERIFY,
        "ok": ok,
        "status": "VERIFIED" if ok else "BLOCKED",
        "exit_code": EXIT_OK if ok else EXIT_REFUSED,
        "verified_at": _now_iso(),
        "root": str(root_path),
        "read_only": True,
        "writes": False,
        "model_called": False,
        "request_sent": False,
        "material": expected_material,
        "owner": {"subject": subject},
        "reading_artifact_id": expected_id,
        "readback": _public_material_facts(facts),
        "bindings": {"expected": expected_id, "observed": bindings},
        "previous": {
            "receipt": previous,
            "observed": _public_material_facts(old_facts),
            "versions": ({"count": (old_versions or {}).get("count"),
                          "total_versions": (old_versions or {}).get("total_versions"),
                          "complete": (old_versions or {}).get("complete")}
                         if old_versions is not None else None),
        },
        "run": {"receipt": before_run, "observed": current_run,
                "same_run_id": checks["same_run_id"],
                "ceilings_unchanged": checks["ceilings_unchanged"],
                "ledger_not_reset": checks["ledger_not_reset"]},
        "artifact_count": None if artifact_ids is None else len(artifact_ids),
        "checks": checks,
        "failures": failures,
        "services": _services_report(session),
        "lease": lease,
        "reason": None if ok else "VERIFY_CHECK_FAILED",
        "detail": None if ok else "failed: %s" % ", ".join(failures),
        "note": ("只读核对：真实 owner 回读新材料的标题/正文/版本；两份实例绑定都指向它；"
                 "旧材料仍在且未被改写；run/预算沿用、账本未重置；不发模型请求"),
    }
    return report


def _ledger_not_reset(before: Any, after: Any) -> bool:
    """A later install must never lower the persisted counters (a reset would be visible)."""

    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return True
    if str(after.get("state")) in ("UNREADABLE", "ABSENT") and before.get("state") not in (
            None, "ABSENT"):
        return False
    for key in ("utc_day_calls", "run_window_calls"):
        old, new = before.get(key), after.get(key)
        if isinstance(old, int) and isinstance(new, int) and new < old:
            return False
    return True


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def cmd_install(args: argparse.Namespace) -> int:
    report = install(Path(args.root))
    _emit(report)
    return _exit_code(report.get("exit_code"))


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify(Path(args.root))
    _emit(report)
    return _exit_code(report.get("exit_code"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="day_reading_material.py")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser(
        "install", help="install the operator-provided excerpt on an existing instance")
    install_parser.add_argument("--root", required=True,
                                help="an existing phone instance root; it is never created")
    install_parser.set_defaults(handler=cmd_install)

    verify_parser = sub.add_parser(
        "verify", help="read the material and bindings back through the real owner")
    verify_parser.add_argument("--root", required=True)
    verify_parser.set_defaults(handler=cmd_verify)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed its own usage
        return int(exc.code or 0)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
