#!/usr/bin/env python3
"""NyAiro 日常入口：一个新人只需要记住这一条命令。

    python3 scripts/nyairo_day.py init   --root /root/nyairo-day
    python3 scripts/nyairo_day.py start  --root /root/nyairo-day
    python3 scripts/nyairo_day.py status --root /root/nyairo-day
    python3 scripts/nyairo_day.py phone  --root /root/nyairo-day
    python3 scripts/nyairo_day.py import --root /root/nyairo-day --file ./材料.txt
    python3 scripts/nyairo_day.py run-day --root /root/nyairo-day --live-model \\
        --duration-seconds 120 --max-new-requests 1
    python3 scripts/nyairo_day.py stop   --root /root/nyairo-day

它只是把**已经在用**的入口包了一层，不新造运行平台：

* ``init``      —— 既有 ``phone.demo.prepare_demo``（真实 Supply/World、全新实例）；
                   再用既有 ``phone.life.enable_instance_memory`` 给这个实例自己的 Memory，
                   删除底账必须放在实例目录之外（没有默认值，本入口把默认位置算在同级目录）。
* ``start``     —— 既有 ``phone.demo.serve_instance``：Supply/World 子进程 + 本机 HTTP。
                   **默认不发任何模型请求**；它只把服务起起来，状态里会明确写
                   “服务就绪，尚未开始自主活动”。
* ``run-day``   —— 既有 ``phone.live_day.run_segment``：一次明确、有界、会花模型额度的请求段。
                   必须显式 ``--live-model``，并且必须给出时长与新请求数；预算沿用实例自己的
                   持久账本，不重置、不换 run、不隐藏重跑。
* ``status`` / ``phone`` / ``import`` / ``stop`` —— 纯读或单次显式操作，不启动模型。

不做的事：不改默认 World 写门、不改 Memory 准入、不强迫她行动、不迁移旧实例、
不新建网页或第二个运行平台。说明见 ``docs/DAY_START.zh-CN.md``。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "world" / "scripts"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DEFAULT_PORT = 3831
SCHEMA_RECORD = "nyairo.day.service.v1"
SCHEMA_READY = "nyairo.day.ready.v1"
SCHEMA_MANAGED = "nyairo.day.managed.v1"
RECORD_NAME = "nyairo-day.json"
READY_NAME = "nyairo-day.ready.json"
MANAGED_NAME = "nyairo-day.instance.json"
LOG_NAME = "nyairo-day.log"
MEMORY_OPERATION_KEY = "nyairo-day-memory-v1"
READY_TIMEOUT_SECONDS = 90.0

#: reason code -> 一句人话。没有编新规则，只是把既有失败原因转成用户能懂的句子。
HUMAN_REASONS = {
    "ROOT_NOT_ABSOLUTE": "实例目录要写绝对路径，例如 /root/nyairo-day。",
    "ROOT_NOT_A_DIRECTORY": "这个路径不是一个目录，换一个目录。",
    "ROOT_NOT_EMPTY": "这个目录里已经有文件了。为了不覆盖旧数据，请换一个还不存在的目录。",
    "ROOT_EXISTS": "这个目录已经存在。为了不覆盖，请换一个还不存在的目录。",
    "SUBJECT_MUST_BE_CHIYO": "这个实例只支持固定主体 chiyo。",
    "SUPPLY_SOCKET_TIMEOUT": "Supply owner 在等待时间内没有就绪；看实例 logs/supply.log 的最后几行。",
    "SUPPLY_NOT_READY": "Supply owner 起来了但没有应答；看实例 logs/supply.log。",
    "WORLD_STATE_MISSING": "世界状态没有生成，实例不完整；换一个新目录重试。",
    "PHONE_INSTANCE_UNREADABLE": "这个目录读不出手机实例，可能不是 NyAiro 实例，或者 instance.json 坏了。",
    "INSTANCE_SCHEMA_UNKNOWN": "实例文件的格式不认识；它可能来自别的版本。",
    "NESTED_LIFE_INSTANCE_MISSING": "这个目录缺少 Life 实例（one-day/instance.json），先运行 init。",
    "NESTED_LIFE_INSTANCE_INVALID": "这个目录的 Life 实例读不了，先检查 instance.json。",
    "LEASE_NOT_FREE": "这个实例正被另一个进程写着；先停掉它，再导入材料。",
    "LEASE_HELD": "这个实例正被另一个进程写着；先停掉它，再导入材料。",
    "MEMORY_AUTHORITY_PATH_REQUIRED": "Memory 删除底账必须放在实例目录之外；用 --memory-authority 指定。",
    "MEMORY_AUTHORITY_PATH_INSIDE_INSTANCE": "Memory 删除底账不能放在实例目录里，否则会跟实例一起被回滚；换一个实例外的路径。",
    "MEMORY_FILES_EXIST_UNBOUND": "实例里已经有 Memory 文件但没有记录启用；为免接管半套文件，先人工核对。",
    "MEMORY_BINDING_OWNER_MISMATCH": "这个实例已经绑定过另一个 Memory owner，不会改绑。",
    "NO_MODEL_PROVIDER": "模型还没有配置好，所以这次没有发任何请求。按说明填 NYA02_MODEL_* 后再试。",
    "NO_REAL_MODEL_CONFIG": "模型配置不完整，所以这次没有发任何请求。按说明填 NYA02_MODEL_* 后再试。",
    "SIGNAL_STOP_UNSUPPORTED_ON_PLATFORM": "这个平台不支持按 pid 停止；请在前台运行时用 Ctrl-C。",
    "PLATFORM_UNSUPPORTED": "这个入口在 WSL/Linux 上运行；Windows 原生没有 Life 需要的 POSIX 能力。",
    "PORT_IN_USE": "这个端口已经被别的服务占用；换一个 --port，或先停掉旧服务。",
    "SERVICE_NOT_READY": "服务没有在等待时间内就绪；原因代码和最后几行日志在下面的输出里。",
    "SERVICE_NOT_RUNNING": "服务没在运行，手机读口打不开；先运行 start。",
    "NO_SERVICE_RECORD": "没有本入口启动的服务记录；如果它在前台运行，请在那个窗口按 Ctrl-C。",
    "STOP_REFUSED": "记录里的进程不是本入口为这个目录启动的，不会去动它。",
    "STOP_TIMEOUT": "已经发出停止信号，但进程还没退出；过一会儿再看 status。",
    "LIVE_MODEL_NOT_ENABLED": "没有加 --live-model，所以一个模型请求都没有发。",
    "BOUND_REQUIRED": "run-day 必须写清楚 --duration-seconds 和 --max-new-requests。",
    "MATERIAL_UNREADABLE": "读不到这份材料文件；检查路径和编码（用 UTF-8 文本）。",
    "MATERIAL_EMPTY": "这份材料是空的，换一份有内容的文本文件。",
    "IMPORT_FAILED": "材料没有导入成功；原因代码和细节在输出里。",
    "CREATE_REFUSED": "owner 拒绝了这次建件；材料没有导入。",
    "READBACK_MISMATCH": "owner 回读的内容和导入的正文不一致；没有绑定，也没有假装成功。",
    "STATUS_READ_FAILED": "状态读不出来；先确认这个目录是完整实例。",
    "INSTANCE_NOT_CREATED_BY_THIS_ENTRY": "这个目录已经是别的实例；为了不接管旧数据，本入口不会给它新建 Memory 或改动它。换一个新目录。",
    "SERVE_FAILED": "服务进程启动失败；看实例 logs/nyairo-day.log 的最后几行。",
    "FINITE_BOUND_REQUIRED": "必须给有限的时长或 tick 上限；这个入口不会无限运行。",
    "DURATION_OUT_OF_RANGE": "时长超出既有范围；改成一个合法的秒数。",
    "REQUEST_CAP_INVALID": "新请求数必须是大于 0 的整数。",
    "MEMORY_BINDING_BROKEN": "这个实例记录了 Memory，但现在读不了；先按日志修好 Memory，再运行。",
    "CROSS_BINDING_CONFLICT": "手机实例和 Life 实例的记录互相矛盾；先停服务核对，不能强行运行。",
    "WORLD_PACKAGE_MISSING": "找不到这个街区包；写 examples/worlds 下的包名，或一个完整的包目录。",
    "WORLD_PACKAGE_UNREADABLE": "这个街区包读不了（world.json 缺失或坏了）；换一个包。",
    "NEED_NEW_ROOT_FOR_WORLD_PACKAGE": "这个实例已经绑定了一个街区；换街区要建一个新目录，不会原地迁移。",
    "NEED_NEW_ROOT_FOR_LIVED_NEEDS": "这个实例的虚拟身体档案已经定下（或从未启用）；改初始值要建一个新目录。",
    "LIVED_NEEDS_INVALID": "虚拟身体初值必须是 0 到 1 之间的数字（0 表示不渴不饿）。",
    "FOREGROUND_NEEDS_CTRL_C": "这个服务是在前台运行的；请回到那个窗口按 Ctrl-C 停止。",
}


def _emit(payload: Mapping[str, Any], *, as_json: bool = False,
          text: Optional[str] = None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    elif text is not None:
        sys.stdout.write(text.rstrip("\n") + "\n")
    else:
        sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_path(root: Path) -> Path:
    return root / "run" / RECORD_NAME


def _ready_path(root: Path) -> Path:
    return root / "run" / READY_NAME


def _managed_path(root: Path) -> Path:
    return root / "run" / MANAGED_NAME


def _is_managed(root: Path) -> bool:
    """Did *this* entry create the instance?  A foreign instance is never adopted."""

    return _read_json(_managed_path(root)).get("schema") == SCHEMA_MANAGED


def _managed_config(root: Path) -> dict[str, Any]:
    """The entry's own bounded config: package identity, profile, run switches."""

    record = _read_json(_managed_path(root))
    return {
        "world_package": dict(record.get("world_package") or {}),
        "lived_needs": dict(record.get("lived_needs") or {}),
        "room_living": bool(record.get("room_living")),
        "world_actions": bool(record.get("world_actions")),
    }


def _normalize_lived_needs(value: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Explicit 0..1 initial values only; ``None`` means the v1 body (no profile)."""

    if value is None:
        return None
    out: dict[str, Any] = {"enabled": True}
    for name in ("hunger", "thirst", "toilet"):
        raw = value.get(name, 0.0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RuntimeError("LIVED_NEEDS_INVALID:%s" % name)
        number = float(raw)
        if not (0.0 <= number <= 1.0):
            raise RuntimeError("LIVED_NEEDS_INVALID:%s" % name)
        out[name] = number
    return out


def _lived_needs_from_args(args: argparse.Namespace) -> Optional[dict[str, Any]]:
    if not getattr(args, "lived_needs", False):
        if any(getattr(args, name, None) is not None for name in
               ("lived_needs_hunger", "lived_needs_thirst", "lived_needs_toilet")):
            # Explicit values without the enabling switch would be silently ignored.
            raise RuntimeError("LIVED_NEEDS_INVALID:add --lived-needs to enable the profile")
        return None
    value = {
        "hunger": getattr(args, "lived_needs_hunger", None) or 0.0,
        "thirst": getattr(args, "lived_needs_thirst", None) or 0.0,
        "toilet": getattr(args, "lived_needs_toilet", None) or 0.0,
    }
    return _normalize_lived_needs(value)


def _resolve_run_switch(args: argparse.Namespace, name: str, stored: Any) -> bool:
    """An explicit on/off wins; otherwise the entry's last recorded choice applies."""

    value = getattr(args, name, None)
    if value is None:
        return bool(stored)
    return bool(value)


def _persist_run_switches(root: Path, *, room_living: bool, world_actions: bool) -> None:
    path = _managed_path(root)
    record = _read_json(path)
    if not record:
        return
    record.update({"room_living": bool(room_living),
                   "world_actions": bool(world_actions)})
    _write_json(path, record)


def _same_world_package(existing: Mapping[str, Any], requested: Any) -> bool:
    """Compare the package's own id, not the directory it happens to live in."""

    if not requested:
        return True
    from components.phone import demo

    identity = demo.world_package_identity(demo.resolve_world_package(str(requested)))
    return str(identity.get("world_id") or "") == str(existing.get("world_id") or "")


def _config_conflict(marker: Mapping[str, Any], *, world_package: Optional[str],
                     lived_needs: Optional[Mapping[str, Any]]) -> Optional[str]:
    if world_package and not _same_world_package(
            dict(marker.get("world_package") or {}), world_package):
        return "NEED_NEW_ROOT_FOR_WORLD_PACKAGE"
    stored_needs = dict(marker.get("lived_needs") or {})
    if lived_needs is not None:
        if not stored_needs or not stored_needs.get("enabled"):
            return "NEED_NEW_ROOT_FOR_LIVED_NEEDS"
        for name in ("hunger", "thirst", "toilet"):
            if float(stored_needs.get(name, -1.0)) != float(lived_needs.get(name, -2.0)):
                return "NEED_NEW_ROOT_FOR_LIVED_NEEDS"
    return None


def _log_path(root: Path) -> Path:
    return root / "logs" / LOG_NAME


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _human(code: str, detail: str = "") -> str:
    text = HUMAN_REASONS.get(str(code))
    if text:
        return text
    if str(code).startswith("GENESIS_REFUSED"):
        return "世界经济的初始化被拒绝（%s）；实例不完整，换一个新目录重试。" % code
    if str(code).startswith("CHILD_SERVICE_TIMEOUT"):
        return "服务子进程在等待时间内没有就绪（%s）；看实例 logs/ 下的日志。" % code
    tail = ("（原因代码 %s）" % code) if code else ""
    if detail:
        tail += "：%s" % detail
    return "没有成功%s。" % tail if tail else "没有成功。"


def _reason_of(exc: BaseException) -> str:
    """The owner's own uppercase prefix, if the exception carries one."""

    text = str(exc)
    head = text.split(":", 1)[0].strip()
    if head and head.isupper():
        return head
    return type(exc).__name__


def _refuse(reason: str, detail: str = "", *, text: Optional[str] = None,
            as_json: bool = False, extra: Optional[Mapping[str, Any]] = None) -> int:
    payload: dict[str, Any] = {"ok": False, "status": "BLOCKED", "reason": reason,
                               "detail": detail, "model_called": False}
    if extra:
        payload.update(extra)
    _emit(payload, as_json=as_json, text=None if as_json else (text or _human(reason, detail)))
    return EXIT_REFUSED


# ---------------------------------------------------------------------------------------------
# instance preparation
# ---------------------------------------------------------------------------------------------


def _default_authority(root: Path) -> Path:
    """删除底账放在实例业务根之外的同级目录；不跟随实例备份一起回滚。"""

    return root.parent / (root.name + "-memory-authority") / "authority.db"


def _model_configured(environment: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """纯读配置，不发请求；把 ``NYA02_MODEL_ENV_FILE`` 读进一份临时 env 再判断。"""

    from components.phone import life as phone_life

    env = dict(os.environ if environment is None else environment)
    try:
        from autonomous_life_assembly import load_model_env_file

        load_model_env_file(env)
    except Exception:
        pass
    try:
        return dict(phone_life.check_model(env))
    except Exception as exc:
        return {"configured": False, "detail": type(exc).__name__}


def _package_reading_material(world_package: Optional[str]) -> dict[str, Any]:
    """The selected package's own public reading material, if it declares one.

    The world package is the content source: ``content.json`` may carry a
    ``reading_material`` entry (title/author/source/content).  A new root gets
    that text as a real Supply artifact through the existing ``prepare_demo``
    path.  Nothing is fetched from the network and no material is invented here;
    a package without the entry keeps the previous behaviour (no material).
    """

    empty = {"title": "", "content": None, "source": None}
    if not world_package:
        return empty
    from components.phone import demo

    try:
        package_dir = demo.resolve_world_package(world_package)
        document = json.loads((Path(package_dir) / "content.json").read_text(
            encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(document, Mapping):
        return empty
    entries = document.get("entries")
    entry = entries.get("reading_material") if isinstance(entries, Mapping) else None
    if not isinstance(entry, Mapping):
        return empty
    title = str(entry.get("title") or "").strip()
    content = entry.get("content")
    if not title or not isinstance(content, str) or not content.strip():
        return empty
    return {
        "title": title[:200],
        "content": content,
        "source": entry.get("source") or None,
        "work": entry.get("work") or None,
        "author": entry.get("author") or None,
        "excerpt_note": entry.get("excerpt_note") or None,
    }


def _is_instance(root: Path) -> bool:
    from components.phone import service as phone_service

    try:
        phone_service.load_instance(root)
    except Exception:
        return False
    return True


def _ensure_instance(root: Path, *, authority: Optional[Path] = None,
                     run_id: Optional[str] = None,
                     world_package: Optional[str] = None,
                     lived_needs: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Prepare exactly one new instance, or reuse the one already at ``root``.

    Package/profile are *creation* choices: an existing managed root accepts the
    same package/profile (no rewrite) and refuses a different one with a named
    "use a new root" reason.  Room-living / local-action switches are run-time
    choices and live in the same small marker, not in world/body data.
    """

    from components.phone import demo, life as phone_life, service as phone_service

    if _is_instance(root):
        if not _is_managed(root):
            return {"ok": False, "status": "BLOCKED", "root": str(root), "created": False,
                    "reason": "INSTANCE_NOT_CREATED_BY_THIS_ENTRY",
                    "detail": "an existing instance without this entry's managed marker is "
                              "never adopted, migrated or given a new Memory"}
        marker = _managed_config(root)
        conflict = _config_conflict(marker, world_package=world_package,
                                    lived_needs=lived_needs)
        if conflict:
            return {"ok": False, "status": "BLOCKED", "root": str(root), "created": False,
                    "reason": conflict,
                    "detail": "creation choices are fixed for an existing root; "
                              "use a brand-new directory"}
        reloaded = phone_service.load_instance(root)
        memory = phone_life.enable_instance_memory(
            root, None, MEMORY_OPERATION_KEY,
            authority_path=authority or _default_authority(root))
        return {"ok": str(memory.get("status")) == "READY",
                "status": "ALREADY_PREPARED", "root": str(root),
                "created": False, "phone": dict(reloaded), "memory": memory,
                "config": marker}

    if not root.is_absolute():
        raise RuntimeError("ROOT_NOT_ABSOLUTE")
    if root.exists():
        if not root.is_dir():
            raise RuntimeError("ROOT_NOT_A_DIRECTORY")
        if any(root.iterdir()):
            raise RuntimeError("ROOT_NOT_EMPTY")
        # demo.prepare_demo 只接受“不存在”的新根；空目录也拒绝，避免悄悄占用别人的目录。
        raise RuntimeError("ROOT_EXISTS")

    reading = _package_reading_material(world_package)
    prepared = demo.prepare_demo(
        root, with_world=True,
        # 阅读材料只来自所选世界包自己的 content.json；没有声明就不预置。
        # 不预置占位作品：她的第一件作品由她自己产生，或由操作者 import 显式给。
        reading_title=reading["title"], reading_content=reading["content"],
        seed_work_artifact=False,
        run_id=run_id,
        world_package=world_package,
        lived_needs=lived_needs)
    prepared["package_reading_material"] = reading
    package_identity = dict(prepared.get("world_package") or {})
    body_dir = Path(str(prepared["world_home"])) / "data" / "body"
    lived_report = None
    if lived_needs:
        from body_lived_needs_enable import enable_profile

        lived_report = enable_profile(
            body_dir, hunger=float(lived_needs["hunger"]),
            thirst=float(lived_needs["thirst"]), toilet=float(lived_needs["toilet"]),
            note="nyairo_day explicit virtual profile; not a human measurement")
    target_authority = authority or _default_authority(root)
    # 先留归管标记：即使 Memory 这一步失败，重试 start 也只修 Memory，不会重建实例、不会接管别人的根。
    _write_json(_managed_path(root), {
        "schema": SCHEMA_MANAGED, "created_at": _now_iso(), "root": str(root),
        "memory_authority": str(target_authority),
        "world_package": package_identity,
        "lived_needs": dict(lived_needs or {}),
        "room_living": False, "world_actions": False,
        "note": "this instance was created by scripts/nyairo_day.py; foreign instances are never adopted",
    })
    memory = phone_life.enable_instance_memory(
        root, None, MEMORY_OPERATION_KEY, authority_path=target_authority)
    return {"ok": str(memory.get("status")) == "READY", "status": "PREPARED", "root": str(root),
            "created": True, "prepared": prepared, "memory": memory,
            "lived_needs": lived_report,
            "config": _managed_config(root),
            "phone": phone_service.load_instance(root)}


def _prepare_text(report: Mapping[str, Any]) -> str:
    root = str(report.get("root") or "")
    memory = dict(report.get("memory") or {})
    binding = dict(memory.get("binding") or {})
    config = dict(report.get("config") or {})
    lines = []
    if report.get("created"):
        lines.append("实例已准备好：%s" % root)
    else:
        lines.append("这个目录已经是实例：%s（没有重建、没有覆盖）" % root)
    package = dict(config.get("world_package") or {})
    if package.get("world_id"):
        lines.append("街区：%s（%s）" % (package.get("name") or package.get("world_id"),
                                      package.get("world_id")))
    else:
        lines.append("街区：入口生成的默认世界（没有显式选包）")
    reading = dict((report.get("prepared") or {}).get("package_reading_material") or {})
    if reading.get("title"):
        lines.append("阅读材料：%s（来自世界包内容，公版/操作者提供；给材料不等于强迫她读）"
                     % reading.get("title"))
    needs = dict(config.get("lived_needs") or {})
    if needs.get("enabled"):
        lines.append("虚拟身体：已启用（饿 %.2f / 渴 %.2f / 如厕 %.2f，0 表示不渴不饿）"
                     % (needs.get("hunger", 0.0), needs.get("thirst", 0.0),
                        needs.get("toilet", 0.0)))
    else:
        lines.append("虚拟身体：未启用（旧 v1 身体，没有饿/渴/如厕）")
    if str(memory.get("status")) == "READY":
        lines.append("Memory：已启用（owner %s）" % (memory.get("owner") or "chiyo"))
        if binding.get("authority_database"):
            lines.append("  删除底账在实例目录之外：%s" % binding.get("authority_database"))
            lines.append("  （同机换目录不等于抵抗整机回滚，这条边界如实写在这里）")
    else:
        lines.append("Memory：没有启用（%s）" % (memory.get("reason") or memory.get("status")))
    model = _model_configured()
    lines.append("模型：%s" % ("已配置，可以按需运行 run-day" if model.get("configured")
                               else "未配置（不影响启动；启动后不会自主活动、也不花额度）"))
    lines.append("")
    if str(memory.get("status")) == "READY":
        lines.append("下一步：")
        lines.append("  python3 scripts/nyairo_day.py start --root %s" % root)
    else:
        lines.append("先修好 Memory（原因见上），再运行 start；实例目录不会再重建。")
    return "\n".join(lines)


def cmd_init(args: argparse.Namespace) -> int:
    if os.name != "posix":
        return _refuse("PLATFORM_UNSUPPORTED",
                       "这个入口在 WSL/Linux 上运行；Windows 原生没有 Life 需要的 POSIX 能力。",
                       as_json=bool(getattr(args, "json", False)))
    root = Path(args.root).expanduser()
    authority = (Path(args.memory_authority).expanduser()
                 if getattr(args, "memory_authority", None) else None)
    if authority is not None and not authority.is_absolute():
        return _refuse("MEMORY_AUTHORITY_PATH_REQUIRED", "删除底账要写绝对路径",
                       as_json=bool(getattr(args, "json", False)))
    try:
        lived_needs = _lived_needs_from_args(args)
        report = _ensure_instance(
            root, authority=authority, run_id=args.run_id,
            world_package=getattr(args, "world_package", None),
            lived_needs=lived_needs)
    except Exception as exc:
        return _refuse(_reason_of(exc), str(exc), as_json=bool(getattr(args, "json", False)))
    if str(report.get("reason") or "").startswith("INSTANCE_"):
        return _refuse(str(report.get("reason")), str(report.get("detail") or ""),
                       as_json=bool(getattr(args, "json", False)))
    _emit(report, as_json=bool(getattr(args, "json", False)), text=_prepare_text(report))
    return EXIT_OK if report.get("ok") else EXIT_REFUSED


# ---------------------------------------------------------------------------------------------
# service start / stop
# ---------------------------------------------------------------------------------------------


def _http_json(port: int, path: str, timeout: float = 2.0) -> dict[str, Any]:
    url = "http://127.0.0.1:%d%s" % (int(port), path)
    request = urllib.request.Request(url, headers={"Host": "127.0.0.1:%d" % int(port)})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # A named 503 is still the server's real answer; keep its envelope instead of guessing.
        raw = exc.read()
    payload = json.loads(raw.decode("utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _port_answers(port: int) -> bool:
    try:
        _http_json(port, "/api/phone/state", timeout=1.5)
    except Exception:
        return False
    return True


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", int(port))) == 0


def _record_running(root: Path) -> dict[str, Any]:
    record = _read_json(_record_path(root))
    if not record or not _pid_alive(record.get("pid")):
        return {}
    port = record.get("port")
    if not _port_answers(int(port or 0)):
        return {}
    return record


def _urls(port: int) -> dict[str, str]:
    base = "http://127.0.0.1:%d" % int(port)
    return {
        "手机页面": base + "/",
        "此刻": base + "/api/phone/living",
        "今天": base + "/api/phone/today",
        "安排": base + "/api/phone/today/arrangements",
        "作品": base + "/api/phone/projects",
        "世界": base + "/api/phone/world",
        "钱包": base + "/api/phone/wallet",
        "物品": base + "/api/phone/inventory",
        "实体店": base + "/api/phone/store",
        "身体档位（在状态里）": base + "/api/phone/state",
    }


def _body_bands(root: Path) -> Optional[dict[str, Any]]:
    """Read-only Body bands (never raw levels); ``None`` when the profile is off."""

    try:
        from components.phone import service as phone_service

        instance = phone_service.load_instance(root)
        world_home = Path(str(instance.get("world_home") or ""))
    except Exception:
        return None
    try:
        import body_lived_needs as bln
        import body_runtime as br

        state = br.BodyStore(world_home / "data" / "body").load_runtime()
        if state is None:
            return {"enabled": True, "bands": {}, "note": "profile 已写；服务启动后会生成 v2 身体"}
        bands = bln.bands(state)
        if bands is None:
            return None
        return {"enabled": True, "bands": bands}
    except Exception as exc:
        return {"enabled": True, "bands": {}, "reason": type(exc).__name__}


def _config_lines(root: Path) -> list[str]:
    config = _managed_config(root)
    package = dict(config.get("world_package") or {})
    needs = dict(config.get("lived_needs") or {})
    lines = []
    if package.get("world_id"):
        lines.append("街区：%s（%s）" % (package.get("name") or package.get("world_id"),
                                      package.get("world_id")))
    else:
        lines.append("街区：默认生成世界")
    bands = _body_bands(root)
    if needs.get("enabled"):
        bands_text = "；".join("%s=%s" % (name, band)
                               for name, band in sorted((bands or {}).get("bands", {}).items()))
        lines.append("虚拟身体：已启用%s" % (("（%s）" % bands_text) if bands_text else ""))
    else:
        lines.append("虚拟身体：未启用（v1，没有饿/渴/如厕）")
    lines.append("本地动作：%s；房间链：%s" % (
        "显式开启" if config.get("world_actions") else "关闭（默认 gate off）",
        "显式开启" if config.get("room_living") else "关闭"))
    lines.append("手机工具：chiyo_phone 的 world / store / inventory / wallet（身体档位在 world 的 body.needs）")
    return lines


def _start_text(root: Path, port: int, *, already: bool) -> str:
    lines = ["服务就绪，尚未开始自主活动。" if not already else "服务已经在运行。"]
    lines.append("  实例：%s" % root)
    for name, url in _urls(port).items():
        lines.append("  %s：%s" % (name, url))
    lines.extend(_config_lines(root))
    lines.append("")
    lines.append("看状态：python3 scripts/nyairo_day.py status --root %s" % root)
    lines.append("给材料：python3 scripts/nyairo_day.py import --root %s --file 你的材料.txt" % root)
    lines.append("让她真的行动（会花模型额度；服务可以继续开着）：")
    lines.append("  python3 scripts/nyairo_day.py run-day --root %s --live-model "
                 "--duration-seconds 120 --max-new-requests 1" % root)
    lines.append("停止：python3 scripts/nyairo_day.py stop --root %s" % root)
    return "\n".join(lines)


def _serve_forever(root: Path, port: int, *, room_living: bool = False,
                   world_actions: bool = False) -> int:
    """The child process: the existing ``demo.serve_instance`` plus a clean stop event."""

    import threading

    from components.phone import demo

    stop = threading.Event()

    def _on_signal(_signum: int, _frame: Any) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _on_signal)
            except (OSError, ValueError):
                pass

    def _on_ready(announcement: Mapping[str, Any]) -> None:
        _write_json(_ready_path(root), {
            "schema": SCHEMA_READY, "pid": os.getpid(), "port": int(announcement["port"]),
            "url": announcement.get("url"), "ready_at": _now_iso(),
        })
        # The foreground process owns the same bounded service record as the
        # background one, so status can see it and a later stop can close it
        # out cleanly (the process itself is stopped by Ctrl-C/SIGTERM).
        _write_json(_record_path(root), {
            "schema": SCHEMA_RECORD, "pid": os.getpid(), "port": int(announcement["port"]),
            "root": str(root), "started_at": _now_iso(), "status": "RUNNING",
            "ready_at": _now_iso(), "url": announcement.get("url"),
            "model_enabled": False, "room_living": bool(room_living),
            "world_actions": bool(world_actions), "foreground": True,
            "log": str(_log_path(root)),
        })
        sys.stdout.write("服务就绪，尚未开始自主活动。\n")
        sys.stdout.flush()

    gate = "canonical" if world_actions else None
    try:
        demo.serve_instance(root, port=int(port), stop_event=stop, on_ready=_on_ready,
                            room_living=bool(room_living),
                            # 本地工具动作只在显式要求时对**本进程树**开启；默认 off 不变。
                            world_intent_gate=gate,
                            # 用户入口允许“停止后立刻重新打开同一端口”：只放过 TIME_WAIT，
                            # 不会让第二个活着的服务抢同一端口。旧调用方默认仍是不复用。
                            reuse_address=True)
    except demo.PhoneDemoError as exc:
        sys.stdout.write(json.dumps({"ok": False, "reason_code": str(exc),
                                     "error_type": "PhoneDemoError"}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return EXIT_REFUSED
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "reason_code": "SERVE_FAILED",
                                     "error_type": type(exc).__name__,
                                     "detail": str(exc)[:300]}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return EXIT_REFUSED
    finally:
        ready = _read_json(_ready_path(root))
        if str(ready.get("pid")) == str(os.getpid()):
            try:
                _ready_path(root).unlink()
            except OSError:
                pass
        record = _read_json(_record_path(root))
        if str(record.get("pid")) == str(os.getpid()):
            record.update({"stopped_at": _now_iso(), "status": "STOPPED"})
            try:
                _write_json(_record_path(root), record)
            except OSError:
                pass
    return EXIT_OK


def _spawn_background(root: Path, port: int, *, room_living: bool = False,
                      world_actions: bool = False
                      ) -> tuple[Optional[subprocess.Popen], str]:
    log_path = _log_path(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "_serve",
               "--root", str(root), "--port", str(port)]
    if room_living:
        command.append("--room-living")
    if world_actions:
        command.append("--world-actions")
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT), start_new_session=True, close_fds=True)
    finally:
        handle.close()
    _write_json(_record_path(root), {
        "schema": SCHEMA_RECORD, "pid": child.pid, "port": int(port), "root": str(root),
        "started_at": _now_iso(), "status": "STARTING", "model_enabled": False,
        "room_living": bool(room_living), "world_actions": bool(world_actions),
        "log": str(log_path),
    })
    return child, str(log_path)


def _wait_ready(child: Optional[subprocess.Popen], root: Path, port: int) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _port_answers(port):
            return True
        if child is not None and child.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def _log_tail(path: str, lines: int = 4) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def cmd_start(args: argparse.Namespace) -> int:
    if os.name != "posix":
        return _refuse("PLATFORM_UNSUPPORTED",
                       "这个入口在 WSL/Linux 上运行；Windows 原生没有 Life 需要的 POSIX 能力。")
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    running = _record_running(root)
    if running:
        _emit(running, as_json=json_output,
              text=_start_text(root, int(running["port"]), already=True))
        return EXIT_OK

    # 一条命令就能开工：目录不存在时先按同一个入口准备实例；已存在时只做幂等的
    # Memory 检查。不是本入口创建的旧实例一律拒绝，绝不接管/迁移/替它新建 Memory。
    authority = (Path(args.memory_authority).expanduser()
                 if getattr(args, "memory_authority", None) else None)
    try:
        lived_needs = _lived_needs_from_args(args)
        prepared = _ensure_instance(
            root, authority=authority,
            world_package=getattr(args, "world_package", None),
            lived_needs=lived_needs)
    except Exception as exc:
        return _refuse(_reason_of(exc), str(exc), as_json=json_output)
    if prepared.get("created"):
        sys.stdout.write(_prepare_text(prepared) + "\n\n")
        sys.stdout.flush()
    if not prepared.get("ok"):
        memory = dict(prepared.get("memory") or {})
        reason = str(prepared.get("reason") or memory.get("reason") or "MEMORY_NOT_READY")
        text = None
        if reason.startswith("NEED_NEW_ROOT"):
            text = _human(reason)
        elif not reason.startswith("INSTANCE_"):
            text = ("实例目录已经建好，但 Memory 没有启用；先按上面的原因修好，"
                    "可以换一个实例外的底账路径：start --memory-authority /绝对/路径")
        return _refuse(reason, str(prepared.get("detail") or memory.get("detail") or ""),
                       as_json=json_output, text=text)

    config = dict(prepared.get("config") or _managed_config(root))
    room_living = _resolve_run_switch(args, "room_living", config.get("room_living"))
    world_actions = _resolve_run_switch(args, "world_actions", config.get("world_actions"))

    port = int(getattr(args, "port", None) or DEFAULT_PORT)
    if _port_open(port):
        return _refuse("PORT_IN_USE",
                       "端口 %d 已经被另一个服务占用；换一个 --port，或先停掉旧服务。" % port,
                       as_json=json_output)
    # The run switches are this entry's own small preference: record them before
    # serving so a same-root restart (including foreground Ctrl-C) keeps them.
    _persist_run_switches(root, room_living=room_living, world_actions=world_actions)

    if getattr(args, "foreground", False):
        sys.stdout.write("正在启动（前台；Ctrl-C 停止）…\n")
        sys.stdout.flush()
        return _serve_forever(root, port, room_living=room_living,
                              world_actions=world_actions)

    child, log_path = _spawn_background(root, port, room_living=room_living,
                                        world_actions=world_actions)
    if not _wait_ready(child, root, port):
        tail = _log_tail(log_path)
        record = _read_json(_record_path(root))
        reason = "SERVICE_NOT_READY"
        if tail:
            try:
                last = json.loads(tail.splitlines()[-1])
                reason = str(last.get("reason_code") or reason)
            except (ValueError, IndexError):
                pass
        # 先收掉这次没等到就绪的子进程，绝不把半启动的服务留在后台。
        if child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=20)
            except Exception:
                pass
        _write_json(_record_path(root), {**record, "status": "FAILED", "stopped_at": _now_iso()})
        return _refuse(reason, "日志：%s\n%s" % (log_path, tail), as_json=json_output,
                       extra={"log": log_path})
    record = _read_json(_record_path(root))
    record.update({"status": "RUNNING", "ready_at": _now_iso(),
                   "url": _urls(port)["手机页面"], "model_enabled": False,
                   "room_living": bool(room_living),
                   "world_actions": bool(world_actions)})
    _write_json(_record_path(root), record)
    _emit(record, as_json=json_output, text=_start_text(root, port, already=False))
    return EXIT_OK


def _own_service(pid: Any, root: Path) -> bool:
    """Only signal a process whose own argv proves it is this entry serving this root."""

    try:
        raw = Path("/proc/%d/cmdline" % int(pid)).read_bytes()
    except (OSError, ValueError):
        return False
    parts = [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]
    text = " ".join(parts)
    return "nyairo_day.py" in text and "_serve" in text and str(root) in text


def cmd_stop(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    record = _read_json(_record_path(root))
    pid = record.get("pid")
    if not record or not pid:
        return _refuse("NO_SERVICE_RECORD",
                       "没有本入口启动的服务记录。如果它在前台运行，请在那个窗口按 Ctrl-C。",
                       as_json=json_output)
    if not _pid_alive(pid):
        record.update({"status": "STOPPED", "stopped_at": _now_iso()})
        _write_json(_record_path(root), record)
        _emit(record, as_json=json_output,
              text="服务已经不在了。下次继续：python3 scripts/nyairo_day.py start --root %s" % root)
        return EXIT_OK
    if record.get("foreground") and not _own_service(pid, root):
        return _refuse("FOREGROUND_NEEDS_CTRL_C",
                       "the recorded service runs in the foreground",
                       as_json=json_output)
    if os.name != "posix" or not _own_service(pid, root):
        return _refuse("STOP_REFUSED",
                       "记录里的进程不是本入口为这个目录启动的，不会去动它。",
                       as_json=json_output)
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError as exc:
        return _refuse("SIGNAL_FAILED", type(exc).__name__, as_json=json_output)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.3)
    alive = _pid_alive(pid)
    record.update({"status": "STOP_REQUESTED" if alive else "STOPPED",
                   "stopped_at": _now_iso()})
    _write_json(_record_path(root), record)
    if alive:
        return _refuse("STOP_TIMEOUT",
                       "已经发出停止信号，但进程 30 秒内还没退出；不要强行杀，过一会儿再看 status。",
                       as_json=json_output)
    _emit(record, as_json=json_output,
          text="已停止。材料和记录都在，不会重置。\n"
               "下次继续：python3 scripts/nyairo_day.py start --root %s" % root)
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# status / phone
# ---------------------------------------------------------------------------------------------


def _safe_living(root: Path, *, live: bool = False) -> dict[str, Any]:
    from components.phone import living

    return living.living_view(root, live=bool(live))


def _reading_summary(root: Path, port: Optional[int],
                     view: Mapping[str, Any]) -> dict[str, Any]:
    """Bound material title/page, from the real owner when the service is up."""

    block = dict(view.get("reading") or {})
    if block.get("title"):
        return {"bound": True, "title": block.get("title"), "page": block.get("page"),
                "pages": block.get("pages"), "source": "living_view"}
    try:
        from components.phone import service as phone_service

        instance = phone_service.load_instance(root)
    except Exception as exc:
        return {"bound": None, "reason": type(exc).__name__}
    artifact_id = str(instance.get("reading_artifact_id") or "")
    if not artifact_id:
        return {"bound": False}
    if port:
        try:
            answer = _http_json(port, "/api/phone/reading/material", timeout=10.0)
            data = dict(answer.get("data") or {})
            if answer.get("ok") and data.get("title"):
                return {"bound": True, "title": data.get("title"), "page": data.get("page"),
                        "pages": data.get("pages"), "artifact_id": artifact_id,
                        "source": "owner_live"}
            return {"bound": True, "artifact_id": artifact_id, "title": None,
                    "reason": answer.get("reason_code") or "OWNER_UNREADABLE"}
        except Exception as exc:
            return {"bound": True, "artifact_id": artifact_id, "title": None,
                    "reason": type(exc).__name__}
    return {"bound": True, "artifact_id": artifact_id, "title": None,
            "reason": "SERVICE_NOT_RUNNING"}


def _today_read(root: Path, port: Optional[int]) -> dict[str, Any]:
    if port:
        try:
            return _http_json(port, "/api/phone/today")
        except Exception:
            pass
    try:
        from components.phone import service as phone_service

        return phone_service.PhoneService(phone_service.load_instance(root)).today()
    except Exception as exc:
        return {"ok": False, "reason_code": "TODAY_READ_FAILED",
                "detail": type(exc).__name__}


def _arrangements_read(root: Path, port: Optional[int]) -> dict[str, Any]:
    if port:
        try:
            return _http_json(port, "/api/phone/today/arrangements")
        except Exception:
            pass
    try:
        from components.phone import service as phone_service

        return phone_service.PhoneService(phone_service.load_instance(root)).today_arrangements()
    except Exception as exc:
        return {"ok": False, "reason_code": "ARRANGEMENTS_READ_FAILED",
                "detail": type(exc).__name__}


def _projects_read(root: Path, port: Optional[int]) -> dict[str, Any]:
    if port:
        try:
            return _http_json(port, "/api/phone/projects")
        except Exception:
            pass
    try:
        from components.phone import service as phone_service

        return phone_service.PhoneService(phone_service.load_instance(root)).projects()
    except Exception as exc:
        return {"ok": False, "reason_code": "PROJECTS_READ_FAILED",
                "detail": type(exc).__name__}


def _persisted_budget(view: Mapping[str, Any]) -> dict[str, Any]:
    """The persisted request ledger, preferring a view that actually carries the counts."""

    window = dict((view.get("run") or {}).get("window") or {})
    candidates = [
        dict(view.get("budget") or {}),
        dict(window.get("ledger") or {}),
        dict((window.get("windows") or {}).get("ledger") or {}),
    ]
    for candidate in candidates:
        if candidate.get("utc_day_calls") is not None \
                or candidate.get("run_window_calls") is not None:
            return candidate
    return next((candidate for candidate in candidates if candidate), {})


def _ledger_lines(view: Mapping[str, Any]) -> list[str]:
    budget = _persisted_budget(view)
    if not budget:
        return []
    day_calls, day_max = budget.get("utc_day_calls"), budget.get("utc_day_max")
    run_calls, run_max = budget.get("run_window_calls"), budget.get("run_window_max")
    if day_calls is None and run_calls is None:
        return []
    run_text = ("%s/%s" % (run_calls, run_max) if run_calls is not None
                else "0/%s（这个 run 还没有窗口记录）" % run_max)
    run_id = str(budget.get("run_id") or "")
    suffix = ("；运行窗口 %s" % run_id) if run_id else ""
    return ["预算：今天 %s/%s；本窗口 %s%s（持久账本，重启不重置）"
            % (day_calls if day_calls is not None else 0, day_max, run_text, suffix)]


def _status_text(root: Path, view: Mapping[str, Any], today: Mapping[str, Any],
                 arrangements: Mapping[str, Any], projects: Mapping[str, Any],
                 running: Mapping[str, Any], reading: Optional[Mapping[str, Any]] = None) -> str:
    lines: list[str] = []
    if running:
        lines.append("服务：已启动（pid %s，端口 %s）" % (running.get("pid"), running.get("port")))
        lines.append("手机页面：%s" % _urls(int(running["port"]))["手机页面"])
    else:
        lines.append("服务：未启动。先运行 start；下面是她上次留下的真实状态。")
    lines.append("此刻：%s" % (view.get("sentence") or "读不到她的当前状态。"))

    where = dict(view.get("where") or {})
    unknowns = list(where.get("unknowns") or [])
    if where.get("status") == "READY":
        lines.append("位置：%s" % (where.get("place_name") or where.get("place_id")))
    elif unknowns and unknowns != ["NOT_REQUESTED"]:
        lines.append("位置：未知（%s）" % ",".join(unknowns))
    elif where:
        lines.append("位置：这次没有向 World 查询（start 后由手机页面实时读）")

    stop = dict(view.get("work") or {})
    if stop.get("title"):
        lines.append("作品停点：《%s》%s" % (stop.get("title"), stop.get("head_version_id") or ""))
    reading = dict(reading if reading is not None else (view.get("reading") or {}))
    if reading.get("title"):
        lines.append("阅读：《%s》 第 %s/%s 页" % (reading.get("title"), reading.get("page"),
                                              reading.get("pages")))
    elif reading.get("bound"):
        reason = str(reading.get("reason") or "UNKNOWN")
        if reason == "SERVICE_NOT_RUNNING":
            lines.append("阅读：已绑定一份材料（服务没在运行；start 后能读到标题）")
        else:
            lines.append("阅读：已绑定一份材料（owner 此刻读不到标题：%s）" % reason)
    else:
        lines.append("阅读：还没有绑定材料（用 import 给一份；给材料不等于强迫她读）")

    if today.get("ok"):
        data = dict(today.get("data") or {})
        lines.append("今天（%s）：活动 %s 件，作品版本 %s 件" % (
            data.get("day"), data.get("activity_count"), data.get("work_count")))
    else:
        lines.append("今天：读不到（%s），不把它当成什么都没做" % (
            today.get("reason_code") or today.get("reason") or "UNKNOWN"))
        calls = _persisted_budget(view).get("utc_day_calls")
        if (str(today.get("reason_code") or today.get("reason")) == "CANONICAL_STATE_ABSENT"
                and calls == 0):
            lines.append("说明：她还没有开始自主活动（活动记录尚未产生，账本 0 次模型请求）。")
    if arrangements.get("ok"):
        data = dict(arrangements.get("data") or {})
        plan = dict(data.get("plan") or {})
        obligations = dict(data.get("obligations") or {})
        lines.append("安排：自己排的 %s 条，外部义务 %s 条" % (
            plan.get("count"), obligations.get("count")))
    else:
        lines.append("安排：读不到（%s）" % (
            arrangements.get("reason_code") or arrangements.get("reason") or "UNKNOWN"))
    if projects.get("ok"):
        rows = list(projects.get("projects") or [])
        names = "、".join(str(row.get("title") or "") for row in rows[:3] if row.get("title"))
        lines.append("作品：%d 件%s" % (len(rows), ("（%s）" % names) if names else ""))
    else:
        lines.append("作品：读不到（%s）" % (
            projects.get("reason_code") or projects.get("reason") or "UNKNOWN"))

    memory = dict(view.get("memory") or {})
    if memory.get("binding_status"):
        lines.append("Memory：%s（recall_ready=%s，经历 %s 条）" % (
            memory.get("binding_status"), memory.get("recall_ready"),
            memory.get("experience_count")))
    lines.extend(_config_lines(root))
    lines.extend(_ledger_lines(view))
    next_at = (view.get("next") or {}).get("check_window_at")
    if next_at:
        lines.append("下一次自然窗口：%s" % next_at)

    model = _model_configured()
    if not model.get("configured"):
        if running:
            lines.append("模型：本终端没有读到配置；服务启动时是否读到，以 run-day 的实际结果为准。")
        else:
            lines.append("模型：未配置。start 不会花额度；要她真的行动，先按说明配置，再用 run-day。")
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    if not _is_instance(root):
        return _refuse("PHONE_INSTANCE_UNREADABLE", "这个目录还不是实例；先运行 init 或 start。",
                       as_json=json_output)
    running = _record_running(root)
    port = int(running["port"]) if running else None
    try:
        view = _safe_living(root, live=bool(running))
    except Exception as exc:
        return _refuse("STATUS_READ_FAILED", type(exc).__name__, as_json=json_output)
    today = _today_read(root, port)
    arrangements = _arrangements_read(root, port)
    projects = _projects_read(root, port)
    reading = _reading_summary(root, port, view)
    text = _status_text(root, view, today, arrangements, projects, running, reading)
    payload = {"ok": True, "status": "RUNNING" if running else "STOPPED",
               "root": str(root), "service": running or None,
               "view": view, "today": today, "arrangements": arrangements,
               "projects": projects, "reading": reading,
               "config": _managed_config(root), "body": _body_bands(root),
               "model_configured": _model_configured().get("configured"),
               "model_called": False}
    _emit(payload, as_json=json_output, text=None if json_output else text)
    return EXIT_OK


def _phone_text(root: Path, port: int, today: Mapping[str, Any],
                arrangements: Mapping[str, Any], projects: Mapping[str, Any],
                state: Optional[Mapping[str, Any]] = None) -> str:
    lines = ["AI 手机读口（都是只读，打开不会让她行动）："]
    for name, url in _urls(port).items():
        lines.append("  %s：%s" % (name, url))
    apps = dict((state or {}).get("apps") or {})
    world_app = dict(apps.get("world") or {})
    world_data = dict(world_app.get("data") or {})
    location = dict(world_data.get("location") or {})
    if world_data:
        lines.append("世界：%s（%s；%s）" % (
            location.get("name") or location.get("place_id") or "读不到位置",
            world_data.get("world_id") or "unknown world",
            world_app.get("status") or "unknown"))
    body_app = dict(apps.get("body") or {})
    body_data = dict(body_app.get("data") or {})
    needs = body_data.get("needs")
    if isinstance(needs, Mapping) and needs:
        lines.append("身体档位：%s" % "；".join("%s=%s" % (name, band)
                                              for name, band in sorted(needs.items())))
    elif body_app:
        lines.append("身体档位：%s" % ("未启用（v1）" if body_app.get("status") == "available"
                                     else "读不到（%s）" % (body_app.get("status") or "unknown")))
    if today.get("ok"):
        data = dict(today.get("data") or {})
        lines.append("今天：%s，活动 %s 件，作品版本 %s 件" % (
            data.get("day"), data.get("activity_count"), data.get("work_count")))
    else:
        reason = str(today.get("reason_code") or "UNKNOWN")
        lines.append("今天：读不到（%s）%s" % (
            reason, "。她还没有开始过一天：活动记录尚未产生，这不等于今天什么都没做"
                    if reason == "CANONICAL_STATE_ABSENT" else ""))
    if arrangements.get("ok"):
        data = dict(arrangements.get("data") or {})
        lines.append("安排：计划 %s 条，义务 %s 条" % (
            (data.get("plan") or {}).get("count"),
            (data.get("obligations") or {}).get("count")))
    else:
        reason = str(arrangements.get("reason_code") or "UNKNOWN")
        lines.append("安排：读不到（%s）%s" % (
            reason, "（还没开始过一天时也会这样）" if reason == "CANONICAL_STATE_ABSENT" else ""))
    if projects.get("ok"):
        rows = list(projects.get("projects") or [])
        names = "、".join(str(row.get("title") or "") for row in rows[:5] if row.get("title"))
        lines.append("作品：%d 件%s" % (len(rows), ("（%s）" % names) if names else ""))
    else:
        lines.append("作品：读不到（%s）" % (projects.get("reason_code") or "UNKNOWN"))
    lines.extend(_config_lines(root))
    return "\n".join(lines)


def cmd_phone(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    running = _record_running(root)
    if not running:
        return _refuse("SERVICE_NOT_RUNNING",
                       "服务没在运行，读口打不开。先运行 start，再运行 phone。",
                       as_json=json_output,
                       extra={"urls": _urls(int(getattr(args, "port", None) or DEFAULT_PORT))})
    port = int(running["port"])
    today = _today_read(root, port)
    arrangements = _arrangements_read(root, port)
    projects = _projects_read(root, port)
    try:
        state = _http_json(port, "/api/phone/state", timeout=10.0)
        # The CSRF token is for the page, not for an operator readout.
        state.pop("csrf_token", None)
    except Exception:
        state = {}
    payload = {"ok": True, "root": str(root), "port": port, "urls": _urls(port),
               "today": today, "arrangements": arrangements, "projects": projects,
               "state": state, "config": _managed_config(root),
               "model_called": False}
    _emit(payload, as_json=json_output,
          text=None if json_output else _phone_text(root, port, today, arrangements, projects,
                                                    state))
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# one explicit material import
# ---------------------------------------------------------------------------------------------


def _import_material(root: Path, *, title: str, content: str) -> dict[str, Any]:
    """Create one artifact through the real owner, read it back, then bind it as reading."""

    import day_reading_material as drm
    from components.phone import life as phone_life
    from life_artifact_port import LifeArtifactPort, operation_key_for

    phone, nested = drm._load_instance(root)
    _directory, _instance_file, state_root = drm._nested_paths(root, nested)
    lease = phone_life.check_single_lease(state_root)
    if str(lease.get("status")) == "HELD":
        return {"ok": False, "status": "BLOCKED", "reason": "LEASE_HELD",
                "detail": "another live process holds this instance's writer lease"}

    subject = str(phone.get("subject") or nested.get("subject_id") or "")
    workspace_id = str(phone.get("workspace_id") or nested.get("workspace_id") or "")
    environment = dict(nested.get("environment") or {})
    grant_id = str(phone.get("artifact_grant_id")
                   or environment.get("NYA02_ARTIFACT_GRANT")
                   or environment.get("CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT") or "")
    if not subject or not workspace_id or not grant_id:
        return {"ok": False, "status": "BLOCKED", "reason": "INSTANCE_BINDING_INCOMPLETE",
                "detail": "subject/workspace/artifact grant is missing"}

    content_sha = sha256(content.encode("utf-8")).hexdigest()
    key = operation_key_for("nyairo-day-import", "v1", title, content_sha,
                            subject, workspace_id, content_sha)
    session = None
    try:
        session = drm._open_owner(root, phone)
        port = LifeArtifactPort(session.socket_path, subject)
        outcome = port.create_artifact(workspace_id=workspace_id, title=title,
                                       content=content, grant_id=grant_id,
                                       operation_key=key)
        if not outcome.applied:
            return {"ok": False, "status": "BLOCKED", "reason": "CREATE_REFUSED",
                    "detail": "owner_status=%s" % outcome.status}
        artifact_id = str(outcome.artifact_id or "")
        if not artifact_id:
            return {"ok": False, "status": "BLOCKED", "reason": "CREATE_WITHOUT_ARTIFACT_ID",
                    "detail": "owner answered OK without an id"}
        facts = drm._read_material(port, artifact_id)
        if (str(facts.get("title") or "") != title
                or str(facts.get("content_sha256") or "") != content_sha):
            return {"ok": False, "status": "BLOCKED", "reason": "READBACK_MISMATCH",
                    "detail": "the owner's own readback does not match the imported text"}
    finally:
        if session is not None:
            session.stop()

    before = drm._bindings_view(phone, nested)
    changes = drm._binding_changes(before, artifact_id)
    written = drm._write_bindings(root, phone=phone, nested=nested,
                                  new_id=artifact_id, changes=changes)
    return {"ok": True, "status": "IMPORTED", "artifact_id": artifact_id,
            "title": facts.get("title"), "version_id": facts.get("version_id"),
            "version_number": facts.get("version_number"),
            "content_chars": facts.get("content_chars"),
            "binding_written": bool(written.get("nested_written") or written.get("phone_written")),
            "model_called": False, "request_sent": False}


def cmd_import(args: argparse.Namespace) -> int:
    if os.name != "posix":
        return _refuse("PLATFORM_UNSUPPORTED",
                       "这个入口在 WSL/Linux 上运行；Windows 原生没有 Life 需要的 POSIX 能力。")
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    if not _is_instance(root):
        return _refuse("PHONE_INSTANCE_UNREADABLE", "这个目录还不是实例；先运行 init。",
                       as_json=json_output)
    source = Path(args.file).expanduser()
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as exc:
        return _refuse("MATERIAL_UNREADABLE", "%s: %s" % (source, type(exc).__name__),
                       text="读不到这份材料：%s" % source, as_json=json_output)
    if not content.strip():
        return _refuse("MATERIAL_EMPTY", "the file is empty",
                       text="这份材料是空的，换一份有内容的文本文件。", as_json=json_output)
    title = str(getattr(args, "title", None) or source.stem or "一份材料").strip()
    try:
        result = _import_material(root, title=title, content=content)
    except Exception as exc:
        return _refuse("IMPORT_FAILED", "%s: %s" % (type(exc).__name__, exc),
                       as_json=json_output)
    if not result.get("ok"):
        return _refuse(str(result.get("reason")), str(result.get("detail") or ""),
                       as_json=json_output)
    if _record_running(root):
        tail = "正在运行的服务要下次 start 后才会用上新绑定。"
    else:
        tail = "下次 start 后她会看到这份材料。"
    text = ("已导入《%s》：%s 字，owner 回读版本 %s。\n%s\n"
            "给材料不等于强迫她读；她是否读、什么时候读，仍由她自己的选择决定。"
            % (result.get("title"), result.get("content_chars"),
               result.get("version_number"), tail))
    _emit(result, as_json=json_output, text=None if json_output else text)
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# one explicit, bounded model run
# ---------------------------------------------------------------------------------------------


def cmd_run_day(args: argparse.Namespace) -> int:
    if os.name != "posix":
        return _refuse("PLATFORM_UNSUPPORTED",
                       "这个入口在 WSL/Linux 上运行；Windows 原生没有 Life 需要的 POSIX 能力。")
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    if not getattr(args, "live_model", False):
        return _refuse("LIVE_MODEL_NOT_ENABLED",
                       "add --live-model to allow real model requests",
                       text="没有加 --live-model，所以一个模型请求都没有发。\n"
                            "想让她真的行动，加上 --live-model，并给定时长与新请求数。",
                       as_json=json_output)
    if not _is_instance(root):
        return _refuse("PHONE_INSTANCE_UNREADABLE", "这个目录还不是实例；先运行 init。",
                       as_json=json_output)
    duration = getattr(args, "duration_seconds", None)
    requests = getattr(args, "max_new_requests", None)
    if not duration or not requests:
        return _refuse("BOUND_REQUIRED",
                       "--duration-seconds and --max-new-requests are required",
                       text="run-day 必须写清楚 --duration-seconds 和 --max-new-requests；"
                            "没有边界就不会开始。",
                       as_json=json_output)

    from components.phone import live_day

    sys.stdout.write(
        "准备真实模型运行（会花模型额度）：最长 %s 秒，最多 %s 次新请求；"
        "模型没配置好就不会发出任何请求。\n"
        "这些请求从本实例自己的持久账本继续扣，不重置、不换 run；失败也不退款。\n"
        % (duration, requests))
    sys.stdout.flush()
    try:
        report = live_day.run_segment(
            root, duration_seconds=int(duration), max_new_requests=int(requests),
            poll_seconds=2, act=True, start_services=live_day.SERVICE_AUTO)
    except Exception as exc:
        return _refuse("RUN_DAY_FAILED", type(exc).__name__, as_json=json_output)
    calls = report.get("model_calls")
    lines = ["这一段：%s，本段请求 %s 次。" % (report.get("status"),
                                          calls if calls is not None else 0)]
    if report.get("reason"):
        lines.append("原因：%s" % _human(str(report.get("reason")), str(report.get("detail") or "")))
    try:
        lines.extend(_ledger_lines(_safe_living(root)))
    except Exception:
        pass
    lines.append("看状态：python3 scripts/nyairo_day.py status --root %s" % root)
    _emit(report, as_json=json_output, text=None if json_output else "\n".join(lines))
    return int(report.get("exit_code") or EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _add_creation_options(parser: argparse.ArgumentParser) -> None:
    """Choices that are baked into a *new* instance (an existing root refuses changes)."""

    parser.add_argument("--world-package", default=None, dest="world_package",
                        help="explicit world package: a name under examples/worlds "
                             "(e.g. daily_neighborhood_v1) or a package directory")
    parser.add_argument("--lived-needs", action="store_true", dest="lived_needs",
                        help="enable the optional virtual hunger/thirst/toilet profile")
    parser.add_argument("--lived-needs-hunger", type=float, default=None,
                        dest="lived_needs_hunger", help="explicit initial hunger 0..1 (default 0)")
    parser.add_argument("--lived-needs-thirst", type=float, default=None,
                        dest="lived_needs_thirst", help="explicit initial thirst 0..1 (default 0)")
    parser.add_argument("--lived-needs-toilet", type=float, default=None,
                        dest="lived_needs_toilet", help="explicit initial toilet 0..1 (default 0)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyairo_day.py",
        description="NyAiro 日常入口：准备、启动、看状态、给材料、停止。")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="准备一个全新的实例（含 Supply/World/这个实例自己的 Memory）")
    init.add_argument("--root", required=True, help="全新的实例目录（绝对路径）")
    init.add_argument("--memory-authority", default=None, dest="memory_authority",
                      help="Memory 删除底账文件；必须在该实例目录之外（默认在同级目录）")
    init.add_argument("--run-id", default=None, dest="run_id",
                      help="可选的 run 名字；默认沿用实例自己的 run:<目录名>")
    _add_creation_options(init)
    init.add_argument("--json", action="store_true")
    init.set_defaults(handler=cmd_init)

    start = sub.add_parser("start", help="启动手机与 Supply/World 服务（默认不发模型请求）")
    start.add_argument("--root", required=True)
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--memory-authority", default=None, dest="memory_authority",
                       help="只在 start 自动准备新实例时使用；必须在该实例目录之外")
    _add_creation_options(start)
    start.add_argument("--room-living", action="store_true", dest="room_living", default=None,
                       help="显式开启房间链（实体店/交付/布置/灯）；默认沿用本实例上次选择")
    start.add_argument("--no-room-living", action="store_false", dest="room_living",
                       help="显式关闭房间链")
    start.add_argument("--world-actions", action="store_true", dest="world_actions", default=None,
                       help="显式允许本进程树的本地世界动作（临时 canonical；默认 gate off）")
    start.add_argument("--no-world-actions", action="store_false", dest="world_actions",
                       help="显式关闭本地世界动作")
    start.add_argument("--foreground", action="store_true", help="前台运行，Ctrl-C 停止")
    start.add_argument("--json", action="store_true")
    start.set_defaults(handler=cmd_start)

    status = sub.add_parser("status", help="用中文看她在做什么、今天/作品/安排/预算")
    status.add_argument("--root", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    phone = sub.add_parser("phone", help="读出 AI 手机的 today / arrangements / 作品入口")
    phone.add_argument("--root", required=True)
    phone.add_argument("--port", type=int, default=None)
    phone.add_argument("--json", action="store_true")
    phone.set_defaults(handler=cmd_phone)

    imp = sub.add_parser("import", help="导入一份文本材料，绑定为她的阅读材料")
    imp.add_argument("--root", required=True)
    imp.add_argument("--file", required=True)
    imp.add_argument("--title", default=None)
    imp.add_argument("--json", action="store_true")
    imp.set_defaults(handler=cmd_import)

    run = sub.add_parser("run-day", help="一次明确、有界、会花模型额度的日常运行")
    run.add_argument("--root", required=True)
    run.add_argument("--live-model", action="store_true", dest="live_model",
                     help="必须显式给出，才会允许真实模型请求")
    run.add_argument("--duration-seconds", type=int, default=None, dest="duration_seconds")
    run.add_argument("--max-new-requests", type=int, default=None, dest="max_new_requests")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=cmd_run_day)

    stop = sub.add_parser("stop", help="停止本入口启动的服务")
    stop.add_argument("--root", required=True)
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(handler=cmd_stop)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_serve":
        # 内部子进程入口：不出现在用户帮助里，只由 start 自己调用。
        internal = argparse.ArgumentParser(prog="nyairo_day.py _serve")
        internal.add_argument("--root", required=True)
        internal.add_argument("--port", type=int, required=True)
        internal.add_argument("--room-living", action="store_true", dest="room_living")
        internal.add_argument("--world-actions", action="store_true", dest="world_actions")
        parsed = internal.parse_args(arguments[1:])
        return _serve_forever(Path(parsed.root).expanduser(), int(parsed.port),
                              room_living=bool(parsed.room_living),
                              world_actions=bool(parsed.world_actions))
    parser = build_parser()
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return int(args.handler(args))
    except ModuleNotFoundError as exc:
        sys.stderr.write("缺少运行依赖：%s。按 README 建好 venv 再运行；本入口不会自动安装。\n"
                         % (exc.name or exc))
        return EXIT_REFUSED
    except Exception as exc:  # keep the real reason, but say it in one line
        sys.stderr.write("这条命令没有完成：%s: %s\n" % (type(exc).__name__, str(exc)[:240]))
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
