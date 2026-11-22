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
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
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
#: 日常自主运行（start / run-day）默认允许本地世界动作。旧版把 gate 默认关着，
#: 于是即使世界给了可达地点她也出不了门；0.7 起日常入口默认 canonical，显式
#: ``--world-gate off`` / ``--no-world-actions`` 仍然能把它关回去。实验入口
#: （fast_day / g1_real_adoption）不受影响。
DEFAULT_WORLD_GATE = "canonical"
WORLD_GATES = ("off", "shadow", "canonical")
RECORD_NAME = "nyairo-day.json"
READY_NAME = "nyairo-day.ready.json"
MANAGED_NAME = "nyairo-day.instance.json"
LOG_NAME = "nyairo-day.log"
MEMORY_OPERATION_KEY = "nyairo-day-memory-v1"
READY_TIMEOUT_SECONDS = 90.0
DEFAULT_V03_CLOCK_INTERVAL_SECONDS = 60.0
ENV_V03_CLOCK_INTERVAL = "NYAIRO_V03_CLOCK_INTERVAL"

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
    "WORLD_CLOCK_MONOTONIC_VIOLATION": "给出的时间早于世界时钟上次提交点，单调时钟拒绝回拨（未改动数据，模型调用 0 次）。",
    "NOW_ISO_INVALID": "时间格式不合法；请写带时区的 ISO-8601 时间（例如 2026-11-12T08:00:00+00:00）。",
    "V03_CLOCK_INTERVAL_INVALID": "0.3 后台时钟推进间隔必须是合法数字（<= 0 表示关闭后台线程）。",
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


def _managed_world_gate(record: Mapping[str, Any]) -> str:
    """本实例记录的世界动作 gate；旧记录没有 ``world_gate`` 时按旧开关还原。

    新实例在创建时写 ``world_gate: canonical``（日常默认）。旧记录若只有
    ``world_actions``，保留它当时的选择（true→canonical，false→off），不在读取时
    偷偷迁移；显式 ``--world-gate`` 仍可随时覆盖。
    """

    raw = str(record.get("world_gate") or "").strip().lower()
    if raw in WORLD_GATES:
        return raw
    if "world_actions" in record:
        return "canonical" if bool(record.get("world_actions")) else "off"
    return DEFAULT_WORLD_GATE


def _managed_config(root: Path) -> dict[str, Any]:
    """The entry's own bounded config: package identity, profile, run switches."""

    record = _read_json(_managed_path(root))
    gate = _managed_world_gate(record)
    return {
        "world_package": dict(record.get("world_package") or {}),
        "lived_needs": dict(record.get("lived_needs") or {}),
        "room_living": bool(record.get("room_living")),
        "world_gate": gate,
        "world_actions": gate != "off",
        "ambient_events": bool(record.get("ambient_events")),
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
    explicit_enable = bool(getattr(args, "lived_needs", False))
    explicit_disable = bool(getattr(args, "no_lived_needs", False))
    if explicit_enable and explicit_disable:
        raise RuntimeError("LIVED_NEEDS_INVALID:cannot combine --lived-needs and --no-lived-needs")
    if not explicit_enable:
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


def _resolve_world_gate(args: argparse.Namespace, stored: Any) -> str:
    """World-action gate for one run: explicit flag wins, then the recorded choice.

    ``--world-gate off|shadow|canonical`` is the direct spelling; the older
    ``--world-actions`` / ``--no-world-actions`` remain as canonical/off aliases so an
    existing command line keeps its meaning.  With neither, the entry's own daily default
    is canonical -- an unset shell environment can no longer keep her indoors.
    """

    explicit = str(getattr(args, "world_gate", None) or "").strip().lower()
    if explicit:
        if explicit not in WORLD_GATES:
            raise RuntimeError("WORLD_GATE_INVALID")
        return explicit
    legacy = getattr(args, "world_actions", None)
    if legacy is not None:
        return "canonical" if bool(legacy) else "off"
    gate = str(stored or "").strip().lower()
    return gate if gate in WORLD_GATES else DEFAULT_WORLD_GATE


def _persist_run_switches(root: Path, *, room_living: bool,
                          world_gate: Optional[str] = None,
                          world_actions: Optional[bool] = None,
                          ambient_events: bool = False) -> None:
    path = _managed_path(root)
    record = _read_json(path)
    if not record:
        return
    if world_gate is None and world_actions is not None:
        gate = "canonical" if bool(world_actions) else "off"
    else:
        gate = str(world_gate or DEFAULT_WORLD_GATE).strip().lower()
    if gate not in WORLD_GATES:
        gate = DEFAULT_WORLD_GATE
    record.update({"room_living": bool(room_living),
                   "world_gate": gate,
                   "world_actions": gate != "off",
                   "ambient_events": bool(ambient_events)})
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


def _default_v02_control_root(root: Path) -> Path:
    """v0.2 遗忘高水位与 V-C 水印独立放在业务存档根目录外，避免回滚业务快照时一并回退。"""

    return root.parent / (root.name + "-memory-authority") / "v02-control"


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


def bootstrap_living_foundation_state(
    root: Path,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotently seed calm default living-foundation files ONLY when absent on disk.

    Intended strictly for managed NyAiro instances inside ``_ensure_instance(...)``.
    Never overwrites existing state files and never invokes any LLM (``llm_calls == 0``).
    """

    root_path = Path(root)
    effective_iso = str(now_iso).strip() if isinstance(now_iso, str) and now_iso.strip() else _now_iso()
    now_dt = _parse_any_iso_utc(effective_iso)
    if now_dt is None:
        effective_iso = _now_iso()
        now_dt = _parse_any_iso_utc(effective_iso) or datetime.now(timezone.utc)

    created: list[str] = []
    skipped_existing: list[str] = []

    def _seed_if_absent(rel_key: str, target_path: Path, builder: Any) -> None:
        if target_path.is_symlink() or target_path.exists():
            skipped_existing.append(rel_key)
            return
        parent = target_path.parent
        if parent.exists() and parent.is_symlink():
            skipped_existing.append(rel_key)
            return
        payload = builder()
        if not isinstance(payload, Mapping):
            return
        parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json_0600(target_path, payload)
        created.append(rel_key)

    # 1. phone/v03_clock_state.json
    def _build_v03_clock() -> dict[str, Any]:
        try:
            from components.life.scripts import v03_clock_evaporation as v03_clk
        except Exception:  # pragma: no cover
            import v03_clock_evaporation as v03_clk  # type: ignore[no-redef]
        state = v03_clk.default_v03_clock_state()
        clk_dt = v03_clk._parse_iso(effective_iso)
        state["updated_at"] = effective_iso
        state["world_clock"]["last_committed_at"] = effective_iso
        state["world_clock"]["ambient_date"] = v03_clk._to_local_business(clk_dt).strftime("%Y-%m-%d")
        return state

    _seed_if_absent(
        "phone/v03_clock_state.json",
        root_path / "phone" / "v03_clock_state.json",
        _build_v03_clock,
    )

    # 2. phone/v03_ecology.json
    def _build_v03_ecology() -> dict[str, Any]:
        try:
            from components.world.scripts import v03_economy_illness_bridge as v03_eco
        except Exception:  # pragma: no cover
            import v03_economy_illness_bridge as v03_eco  # type: ignore[no-redef]
        state = v03_eco.default_ecology_state()
        state["updated_at"] = effective_iso
        state["illness"]["phase"] = "healthy"
        rb = state.setdefault("reality_bridge", {})
        policy = rb.setdefault("outbound_policy", {})
        policy["enabled"] = True
        state["proactive_policy"] = {"enabled": True}
        return state

    _seed_if_absent(
        "phone/v03_ecology.json",
        root_path / "phone" / "v03_ecology.json",
        _build_v03_ecology,
    )

    # 3. world/v04_neighborhood_state.json
    def _build_v04_neighborhood() -> dict[str, Any]:
        try:
            from components.world.scripts import v04_neighborhood_living as v04_nb
        except Exception:  # pragma: no cover
            import v04_neighborhood_living as v04_nb  # type: ignore[no-redef]
        state = v04_nb.default_state()
        state["updated_at"] = effective_iso
        env = state.setdefault("environment", {})
        env["weather"] = "clear"
        env.setdefault("airflow", "medium")
        env["season"] = None
        env["season_explicit"] = False
        env.setdefault("weather_slot_key", None)
        env.setdefault("manual_pin_until", None)
        env["updated_at"] = None
        return state

    _seed_if_absent(
        "world/v04_neighborhood_state.json",
        root_path / "world" / "v04_neighborhood_state.json",
        _build_v04_neighborhood,
    )

    # 4. phone/autonomy_rhythm.json
    def _build_autonomy_rhythm() -> dict[str, Any]:
        from components.phone import autonomy_rhythm_view as ar_view
        state = ar_view.default_autonomy_rhythm_state()
        state["updated_at"] = effective_iso
        return state

    _seed_if_absent(
        "phone/autonomy_rhythm.json",
        root_path / "phone" / "autonomy_rhythm.json",
        _build_autonomy_rhythm,
    )

    # 5. life/everyday_desires.json
    def _build_everyday_desires() -> dict[str, Any]:
        try:
            from components.life.scripts import everyday_desire_lifecycle as edl
        except Exception:  # pragma: no cover
            import everyday_desire_lifecycle as edl  # type: ignore[no-redef]
        state = edl.default_everyday_desire_state()
        state["updated_at"] = effective_iso
        return state

    _seed_if_absent(
        "life/everyday_desires.json",
        root_path / "life" / "everyday_desires.json",
        _build_everyday_desires,
    )

    # 6. world/v05_living_town.json
    def _build_v05_living_town() -> dict[str, Any]:
        try:
            from components.world.scripts import v05_living_town_engine as v05_town
        except Exception:  # pragma: no cover
            import v05_living_town_engine as v05_town  # type: ignore[no-redef]
        town_dt = v05_town._parse_now(effective_iso)
        ctx = v05_town._extract_world_context_zero_write(root_path, town_dt)
        state = v05_town._default_state(ctx)
        state["updated_at"] = effective_iso
        state["last_pulse_at"] = effective_iso
        return state

    _seed_if_absent(
        "world/v05_living_town.json",
        root_path / "world" / "v05_living_town.json",
        _build_v05_living_town,
    )

    # 7. life/v05_inner_spark.json
    def _build_v05_inner_spark() -> dict[str, Any]:
        try:
            from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark
        except Exception:  # pragma: no cover
            import v05_dreams_desires_foreshadow as v05_spark  # type: ignore[no-redef]
        state = v05_spark._empty_state()
        state["updated_at"] = effective_iso
        return state

    _seed_if_absent(
        "life/v05_inner_spark.json",
        root_path / "life" / "v05_inner_spark.json",
        _build_v05_inner_spark,
    )

    return {
        "ok": True,
        "root": str(root_path),
        "bootstrapped_at": effective_iso,
        "created": created,
        "skipped_existing": skipped_existing,
        "created_count": len(created),
        "llm_calls": 0,
    }


def _ensure_instance(root: Path, *, authority: Optional[Path] = None,
                     run_id: Optional[str] = None,
                     world_package: Optional[str] = None,
                     lived_needs: Optional[Mapping[str, Any]] = None,
                     no_lived_needs: bool = False,
                     starter_reading: bool = False) -> dict[str, Any]:
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
        foundation_bootstrap = bootstrap_living_foundation_state(root)
        return {"ok": str(memory.get("status")) == "READY",
                "status": "ALREADY_PREPARED", "root": str(root),
                "created": False, "phone": dict(reloaded), "memory": memory,
                "living_foundation_bootstrap": foundation_bootstrap,
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

    effective_lived_needs = lived_needs
    if effective_lived_needs is None and not no_lived_needs:
        effective_lived_needs = {"enabled": True, "hunger": 0.0, "thirst": 0.0, "toilet": 0.0}

    reading = _package_reading_material(world_package)
    # 包自带阅读材料优先；只有在明确 opt-in 且包没有声明时才用主题+seed 的本地分页读物。
    starter = bool(starter_reading) and reading["content"] is None
    prepared = demo.prepare_demo(
        root, with_world=True,
        # 阅读材料只来自所选世界包自己的 content.json；没有声明就不预置。
        # 不预置占位作品：她的第一件作品由她自己产生，或由操作者 import 显式给。
        reading_title=reading["title"], reading_content=reading["content"],
        starter_reading=starter,
        seed_work_artifact=False,
        run_id=run_id,
        world_package=world_package,
        lived_needs=effective_lived_needs)
    prepared["package_reading_material"] = reading
    package_identity = dict(prepared.get("world_package") or {})
    body_dir = Path(str(prepared["world_home"])) / "data" / "body"
    lived_report = None
    if effective_lived_needs:
        from body_lived_needs_enable import enable_profile

        lived_report = enable_profile(
            body_dir, hunger=float(effective_lived_needs["hunger"]),
            thirst=float(effective_lived_needs["thirst"]),
            toilet=float(effective_lived_needs["toilet"]),
            note="nyairo_day explicit virtual profile; not a human measurement")
    target_authority = authority or _default_authority(root)
    # 先留归管标记：即使 Memory 这一步失败，重试 start 也只修 Memory，不会重建实例、不会接管别人的根。
    _write_json(_managed_path(root), {
        "schema": SCHEMA_MANAGED, "created_at": _now_iso(), "root": str(root),
        "memory_authority": str(target_authority),
        "world_package": package_identity,
        "lived_needs": dict(effective_lived_needs or {}),
        "room_living": False, "world_gate": DEFAULT_WORLD_GATE,
        "world_actions": DEFAULT_WORLD_GATE != "off", "ambient_events": False,
        "note": "this instance was created by scripts/nyairo_day.py; foreign instances are never adopted",
    })
    memory = phone_life.enable_instance_memory(
        root, None, MEMORY_OPERATION_KEY, authority_path=target_authority)
    foundation_bootstrap = (
        bootstrap_living_foundation_state(root)
        if _is_instance(root) and _is_managed(root)
        else None
    )
    return {"ok": str(memory.get("status")) == "READY", "status": "PREPARED", "root": str(root),
            "created": True, "prepared": prepared, "memory": memory,
            "lived_needs": lived_report,
            "living_foundation_bootstrap": foundation_bootstrap,
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
            lived_needs=lived_needs,
            no_lived_needs=bool(getattr(args, "no_lived_needs", False)),
            starter_reading=bool(getattr(args, "starter_reading", False)))
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
        "时间线": base + "/api/phone/today/timeline",
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
    gate = str(config.get("world_gate") or DEFAULT_WORLD_GATE)
    lines.append("本地动作：%s；房间链：%s；环境事件：%s" % (
        ("关闭（显式 gate off）" if gate == "off" else "开启（gate %s）" % gate),
        "显式开启" if config.get("room_living") else "关闭",
        "显式开启" if config.get("ambient_events") else "关闭"))
    lines.append("手机工具：chiyo_phone 的 world / store / inventory / wallet（身体档位在 world 的 body.needs）")
    return lines


def _start_text(root: Path, port: int, *, already: bool) -> str:
    lines = ["服务就绪，尚未开始自主活动。" if not already else "服务已经在运行。"]
    lines.append("  实例：%s" % root)
    for name, url in _urls(port).items():
        lines.append("  %s：%s" % (name, url))
    lines.extend(_config_lines(root))
    try:
        from components.phone import capability as phone_capability

        capability_file = phone_capability.capability_path(root)
        if capability_file.exists():
            lines.append("日记正文凭据：%s（只对本次 serve 有效，重启轮换）" % capability_file)
        else:
            lines.append("日记正文凭据：本次 serve 未发布；私人正文与主体日记写入 fail closed。")
    except Exception:
        # A probing line must never turn a healthy serve into a failed start.
        lines.append("日记正文凭据：状态未读取；私人日记按服务端实际校验。")
    try:
        from components.phone import operator_stop as phone_operator_stop

        operator_file = phone_operator_stop.credential_path(root)
        if operator_file.exists():
            # 2026-10-28: a *separate* credential.  `stop` below stops the service; this
            # stops one Activity.  The two are deliberately different acts.
            lines.append("外部叫停凭据：%s（只对本次 serve 有效，与日记凭据不通用）" % operator_file)
            lines.append("叫停某个活动：python3 scripts/activity_stop_cli.py --instance %s "
                         "--activity <actv_...> --revision <revision>" % root)
        else:
            lines.append("外部叫停凭据：本次 serve 未发布；叫停路由 fail closed（不影响其它读）。")
    except Exception:
        lines.append("外部叫停凭据：状态未读取；叫停路由按服务端实际校验。")
    lines.append("")
    lines.append("看状态：python3 scripts/nyairo_day.py status --root %s" % root)
    lines.append("给材料：python3 scripts/nyairo_day.py import --root %s --file 你的材料.txt" % root)
    lines.append("让她真的行动（会花模型额度；服务可以继续开着）：")
    lines.append("  python3 scripts/nyairo_day.py run-day --root %s --live-model "
                 "--duration-seconds 120 --max-new-requests 1" % root)
    lines.append("停止：python3 scripts/nyairo_day.py stop --root %s" % root)
    return "\n".join(lines)


def _resolve_v03_clock_interval(
    explicit: Optional[float] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> float:
    """Resolve background 0.3 clock interval seconds (``<= 0`` disables the thread)."""

    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, (int, float)):
            raise RuntimeError("V03_CLOCK_INTERVAL_INVALID")
        val = float(explicit)
        if math.isnan(val) or math.isinf(val):
            raise RuntimeError("V03_CLOCK_INTERVAL_INVALID")
        return val if val > 0.0 else 0.0

    env = os.environ if environment is None else environment
    raw = str(env.get(ENV_V03_CLOCK_INTERVAL, "") or "").strip()
    if not raw:
        return DEFAULT_V03_CLOCK_INTERVAL_SECONDS
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_V03_CLOCK_INTERVAL_SECONDS
    if math.isnan(val) or math.isinf(val):
        return DEFAULT_V03_CLOCK_INTERVAL_SECONDS
    return val if val > 0.0 else 0.0


def _read_rain_neglect_signals_zero_write(
    root_path: Path,
    target_dt: datetime,
    upstream_som: Mapping[str, Any],
) -> dict[str, Any]:
    """Read damp/wet outerwear ``wetted_at`` and body warmth/cold_tendency with 0 disk writes."""
    croot = root_path
    body_candidates: list[Path] = []
    try:
        from components.world.scripts import instance_paths as _instance_paths
    except Exception:  # pragma: no cover
        try:
            import instance_paths as _instance_paths  # type: ignore[no-redef]
        except Exception:
            _instance_paths = None  # type: ignore[assignment]
    if _instance_paths is not None:
        try:
            resolved = _instance_paths.resolve_instance_paths(root_path)
            croot = resolved.day_root
            body_candidates.append(resolved.body_runtime_json)
            body_candidates.append(resolved.world_home / "data" / "body" / "runtime.json")
        except Exception:
            pass
    body_candidates.extend([
        croot / "one-day" / "world-home" / "data" / "body" / "runtime.json",
        croot / "data" / "body" / "runtime.json",
        croot / "world" / "data" / "body" / "runtime.json",
    ])

    upstream_garment = upstream_som.get("garment") if isinstance(upstream_som.get("garment"), Mapping) else {}
    garment_state = upstream_garment.get("state")
    garment_dryness = upstream_garment.get("dryness")
    wetted_at_raw: Any = upstream_garment.get("wetted_at")

    somatic_path = croot / "phone" / "somatic_comfort.json"
    if not somatic_path.is_symlink() and somatic_path.is_file():
        try:
            sdoc = json.loads(somatic_path.read_text(encoding="utf-8"))
            if isinstance(sdoc, Mapping) and isinstance(sdoc.get("garment"), Mapping):
                gdoc = sdoc["garment"]
                if gdoc.get("state") is not None:
                    garment_state = gdoc.get("state")
                if gdoc.get("dryness") is not None:
                    garment_dryness = gdoc.get("dryness")
                if gdoc.get("wetted_at") is not None:
                    wetted_at_raw = gdoc.get("wetted_at")
        except Exception:
            pass

    damp_outerwear_worn = (
        upstream_som.get("damp_outerwear_worn") is True
        or (garment_state == "wear" and garment_dryness in ("damp", "wet"))
    )

    wetted_dt = _parse_any_iso_utc(wetted_at_raw)
    target_utc = target_dt.astimezone(timezone.utc)
    wetted_elapsed_seconds: Optional[float] = None
    if wetted_dt is not None:
        wetted_elapsed_seconds = (target_utc - wetted_dt).total_seconds()

    raw_fatigue = upstream_som.get("body_fatigue")
    body_fatigue: Optional[float] = (
        float(raw_fatigue)
        if isinstance(raw_fatigue, (int, float)) and not isinstance(raw_fatigue, bool)
        else None
    )
    body_warmth: Optional[float] = None
    cold_tendency = False

    seen_paths: set[str] = set()
    for bpath in body_candidates:
        bkey = str(bpath)
        if bkey in seen_paths:
            continue
        seen_paths.add(bkey)
        if bpath.is_symlink() or not bpath.is_file():
            continue
        try:
            bdoc = json.loads(bpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(bdoc, Mapping):
            continue
        somatic_map = bdoc.get("somatic") if isinstance(bdoc.get("somatic"), Mapping) else {}
        if body_fatigue is None:
            cand_f = somatic_map.get("fatigue", bdoc.get("fatigue"))
            if isinstance(cand_f, (int, float)) and not isinstance(cand_f, bool):
                body_fatigue = float(cand_f)
        cand_w = somatic_map.get("warmth", bdoc.get("warmth"))
        if isinstance(cand_w, (int, float)) and not isinstance(cand_w, bool):
            body_warmth = float(cand_w)
            if body_warmth <= 0.35:
                cold_tendency = True
        latch = bdoc.get("signal_latch")
        if isinstance(latch, Mapping) and latch.get("cold_tendency") is True:
            cold_tendency = True
        raw_signals = bdoc.get("signals")
        if isinstance(raw_signals, list):
            for sig in raw_signals:
                if sig == "cold_tendency" or (
                    isinstance(sig, Mapping)
                    and str(sig.get("type") or sig.get("signal") or "") == "cold_tendency"
                ):
                    cold_tendency = True
        break

    return {
        "damp_outerwear_worn": damp_outerwear_worn,
        "garment_state": garment_state,
        "garment_dryness": garment_dryness,
        "wetted_at": wetted_at_raw if isinstance(wetted_at_raw, str) else None,
        "wetted_elapsed_seconds": wetted_elapsed_seconds,
        "body_fatigue": body_fatigue,
        "body_warmth": body_warmth,
        "cold_tendency": cold_tendency,
    }


def _maybe_tick_v03_ecology(root_path: Path, now_iso: Optional[str]) -> Optional[dict[str, Any]]:
    """Advance existing v03_ecology slow variables (illness recovery / expired DELIVERED notes).

    Never creates ``v03_ecology.json`` when absent, never touches ``UNKNOWN`` outbound records,
    and performs zero disk writes when healthy with no expired outbound notes.
    """

    fast_eco_path = root_path / "phone" / "v03_ecology.json"
    if fast_eco_path.is_symlink() or not fast_eco_path.exists() or not fast_eco_path.is_file():
        return None

    try:
        from components.world.scripts import v03_economy_illness_bridge as v03_eco
    except Exception:  # pragma: no cover
        import v03_economy_illness_bridge as v03_eco  # type: ignore[no-redef]

    eco_path = v03_eco.ecology_state_path(root_path)
    if eco_path.is_symlink() or not eco_path.exists() or not eco_path.is_file():
        return None
    eco_state, eco_reason = v03_eco.read_stored_ecology_zero_write(root_path)
    if eco_reason is not None:
        return None

    try:
        target_dt = v03_eco.parse_iso_moment(now_iso)
    except v03_eco.V03EcologyError:
        return None
    effective_iso = v03_eco.format_wall_iso(target_dt)

    ill = eco_state.get("illness") or {}
    ill_phase = str(ill.get("phase") or "healthy")
    has_illness_or_strain = (
        ill_phase != "healthy"
        or float(ill.get("strain_load") or 0.0) > 0.0
        or float(ill.get("severity") or 0.0) > 0.0
        or float(ill.get("sleep_debt_hours") or 0.0) > 0.0
    )
    last_eval_raw = ill.get("last_evaluated_at")
    last_eval_dt = None
    if isinstance(last_eval_raw, str) and last_eval_raw.strip():
        try:
            last_eval_dt = v03_eco.parse_iso_moment(last_eval_raw)
        except v03_eco.V03EcologyError:
            last_eval_dt = None
    can_advance_illness = has_illness_or_strain and (last_eval_dt is None or target_dt > last_eval_dt)

    can_record_neglect_strain = False
    neglect_intensity = 0.40
    if ill_phase in ("healthy", "prodrome"):
        upstream_som = v03_eco._read_upstream_somatic_and_body_zero_write(root_path, effective_iso)
        rain_sig = _read_rain_neglect_signals_zero_write(root_path, target_dt, upstream_som)
        damp_worn = bool(rain_sig["damp_outerwear_worn"])
        raw_fatigue = rain_sig["body_fatigue"]
        wetted_elapsed = rain_sig["wetted_elapsed_seconds"]
        garment_dryness = rain_sig["garment_dryness"]
        cold_tendency = bool(rain_sig["cold_tendency"])

        # Backward compatibility when wetted_at is absent: damp outerwear + high fatigue (>= 0.82)
        legacy_neglect = (
            damp_worn
            and wetted_elapsed is None
            and raw_fatigue is not None
            and raw_fatigue >= 0.82
        )
        # Realistic Stage 2 rain/chill neglect: damp/wet outerwear worn unchanged >= 45m (2700s)
        # AND (fatigue >= 0.55 OR cold_tendency / warmth <= 0.35 OR dryness == "wet")
        realistic_neglect = (
            damp_worn
            and wetted_elapsed is not None
            and wetted_elapsed >= 2700.0
            and (
                (raw_fatigue is not None and raw_fatigue >= 0.55)
                or cold_tendency
                or garment_dryness == "wet"
            )
        )
        neglect_active = legacy_neglect or realistic_neglect
        if neglect_active:
            neglect_intensity = 0.65 if garment_dryness == "wet" else 0.40
            latest_strain_dt: Optional[datetime] = None
            for s_ev in ill.get("strain_events") or []:
                if not isinstance(s_ev, Mapping):
                    continue
                rec_raw = s_ev.get("recorded_at")
                if isinstance(rec_raw, str) and rec_raw.strip():
                    try:
                        s_dt = v03_eco.parse_iso_moment(rec_raw)
                        if latest_strain_dt is None or s_dt > latest_strain_dt:
                            latest_strain_dt = s_dt
                    except v03_eco.V03EcologyError:
                        continue
            if latest_strain_dt is None or (target_dt - latest_strain_dt).total_seconds() >= 1800.0:
                if last_eval_dt is None or target_dt > last_eval_dt:
                    can_record_neglect_strain = True
            elif ill_phase == "healthy":
                # Cooldown active while neglect persists: avoid 60s disk-write spam.
                can_advance_illness = False

    rb = eco_state.get("reality_bridge") or {}
    has_expired_outbound = False
    for rec in rb.get("outbound_records") or []:
        if isinstance(rec, Mapping) and rec.get("status") == "DELIVERED":
            exp_raw = rec.get("expires_at")
            if isinstance(exp_raw, str) and exp_raw.strip():
                try:
                    exp_dt = v03_eco.parse_iso_moment(exp_raw)
                    if target_dt >= exp_dt:
                        has_expired_outbound = True
                        break
                except v03_eco.V03EcologyError:
                    continue

    if not can_record_neglect_strain and not can_advance_illness and not has_expired_outbound:
        return None

    illness_rcpt: Optional[dict[str, Any]] = None
    neglect_strain_recorded = False
    withdrawn_ids: list[str] = []
    try:
        if can_record_neglect_strain:
            strain_proj = v03_eco.record_strain_or_rest(
                root_path,
                event_kind="caught_in_rain",
                intensity=neglect_intensity,
                unchanged_damp_clothes=True,
                now_iso=effective_iso,
            )
            illness_rcpt = dict(
                strain_proj.get("receipt")
                or strain_proj.get("last_receipt")
                or ((strain_proj.get("data") or {}).get("illness") or {})
            )
            neglect_strain_recorded = True
        elif can_advance_illness:
            proj = v03_eco.advance_illness_recovery(root_path, effective_iso)
            illness_rcpt = dict(
                proj.get("receipt")
                or proj.get("last_receipt")
                or ((proj.get("data") or {}).get("illness") or {})
            )
        if has_expired_outbound:
            out_proj = v03_eco.apply_v03_ecology_command(
                root_path,
                {"action": "withdraw_expired_outbound"},
                actor_side="subject",
                now_iso=effective_iso,
            )
            out_rcpt = dict(out_proj.get("receipt") or out_proj.get("last_receipt") or {})
            withdrawn_ids = list(out_rcpt.get("withdrawn_ids") or [])
    except v03_eco.V03EcologyError:
        return None

    if illness_rcpt is None and not withdrawn_ids:
        return None

    prev_phase = str(illness_rcpt.get("previous_phase") or ill_phase) if illness_rcpt else ill_phase
    cur_phase = str(illness_rcpt.get("phase") or ill_phase) if illness_rcpt else ill_phase
    return {
        "illness_advanced": illness_rcpt is not None,
        "neglect_strain_recorded": neglect_strain_recorded,
        "previous_phase": prev_phase,
        "phase": cur_phase,
        "phase_changed": prev_phase != cur_phase,
        "withdrawn_expired_outbound": withdrawn_ids,
        "llm_calls": 0,
    }


def _maybe_tick_v04_neighborhood(
    root_path: Path,
    now_iso: Optional[str],
) -> Optional[dict[str, Any]]:
    """Advance existing v0.4 neighborhood weather & processes (cloth drying, journey, holds, notices) with 0 LLM calls.

    Strictly zero-write when ``world/v04_neighborhood_state.json`` is absent on disk.
    """

    nb_candidates = (
        root_path / "world" / "v04_neighborhood_state.json",
        root_path / "phone" / "v04_neighborhood_state.json",
    )
    if not any(p.exists() or p.is_symlink() for p in nb_candidates):
        return None

    try:
        from components.world.scripts import v04_neighborhood_living as v04_nb
    except Exception:  # pragma: no cover
        try:
            import v04_neighborhood_living as v04_nb  # type: ignore[no-redef]
        except Exception:
            return None

    effective_iso = now_iso or _now_iso()
    weather_report: Optional[dict[str, Any]] = None
    weather_changed = False
    weather_fn = getattr(v04_nb, "advance_autonomous_weather_pulse", None)
    if callable(weather_fn):
        try:
            raw_weather = weather_fn(root_path, now_iso=effective_iso)
            if isinstance(raw_weather, Mapping):
                weather_report = dict(raw_weather)
                weather_changed = bool(weather_report.get("changed"))
        except Exception:
            weather_report = None

    try:
        report = v04_nb.advance_v04_neighborhood_processes(root_path, now_iso=effective_iso)
    except Exception:
        report = None

    if not isinstance(report, Mapping):
        if weather_report is None:
            return None
        out = {
            "ok": True,
            "advanced": weather_changed,
            "changed": weather_changed,
            "settled_count": 1 if weather_changed else 0,
            "changes_count": 1 if weather_changed else 0,
            "llm_calls": 0,
        }
    else:
        out = dict(report)
        if weather_changed:
            out["changed"] = True
            out["advanced"] = True
            out["settled_count"] = int(out.get("settled_count") or 0) + 1
            out["changes_count"] = int(out.get("changes_count") or 0) + 1
    if weather_report is not None:
        out["weather_pulse"] = weather_report
    out["weather_changed"] = weather_changed
    out.setdefault("llm_calls", 0)
    return out


def _maybe_tick_v05_living_town(
    root_path: Path,
    now_iso: Optional[str],
    *,
    weather_changed: bool = False,
) -> Optional[dict[str, Any]]:
    """Advance existing ``world/v05_living_town.json`` when >=900s elapsed or weather/day changed.

    Strictly zero-write when ``world/v05_living_town.json`` is absent or not due.
    """

    town_path = root_path / "world" / "v05_living_town.json"
    if town_path.is_symlink() or not town_path.exists() or not town_path.is_file():
        return None

    effective_iso = now_iso or _now_iso()
    now_dt = _parse_any_iso_utc(effective_iso)
    if now_dt is None:
        return None

    try:
        raw_doc = json.loads(town_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw_doc, Mapping):
        return None

    anchor_raw = raw_doc.get("last_pulse_at") or raw_doc.get("updated_at")
    anchor_dt = _parse_any_iso_utc(anchor_raw)
    biz_tz = timezone(timedelta(hours=8))
    day_changed = (
        anchor_dt is not None
        and anchor_dt.astimezone(biz_tz).date() != now_dt.astimezone(biz_tz).date()
    )
    elapsed_seconds = (
        (now_dt - anchor_dt).total_seconds() if anchor_dt is not None else 900.0
    )
    if (
        anchor_dt is not None
        and elapsed_seconds < 900.0
        and not weather_changed
        and not day_changed
    ):
        return None

    try:
        from components.world.scripts import v05_living_town_engine as v05_town
    except Exception:  # pragma: no cover
        try:
            import v05_living_town_engine as v05_town  # type: ignore[no-redef]
        except Exception:
            return None

    elapsed_hours = max(1.0 / 60.0, min(48.0, max(0.0, elapsed_seconds) / 3600.0))
    try:
        report = v05_town.advance_town_pulse(
            root_path,
            elapsed_hours=elapsed_hours,
            now_iso=effective_iso,
        )
    except Exception:
        return None
    if not isinstance(report, Mapping):
        return None
    out = dict(report)
    out.setdefault("llm_calls", 0)
    return out


def _maybe_tick_v05_inner_spark(
    root_path: Path,
    now_iso: Optional[str],
) -> Optional[dict[str, Any]]:
    """Advance existing ``life/v05_inner_spark.json`` (daylight frost melt & real wake dream only).

    Strictly zero-write when ``life/v05_inner_spark.json`` is absent or nothing changed.
    """

    spark_path = root_path / "life" / "v05_inner_spark.json"
    if spark_path.is_symlink() or not spark_path.exists() or not spark_path.is_file():
        return None

    try:
        from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark
    except Exception:  # pragma: no cover
        try:
            import v05_dreams_desires_foreshadow as v05_spark  # type: ignore[no-redef]
        except Exception:
            return None

    effective_iso = now_iso or _now_iso()
    try:
        report = v05_spark.advance_inner_spark_heartbeat(
            root_path,
            now_iso=effective_iso,
            woke_from_sleep=False,
        )
    except Exception:
        return None
    if not isinstance(report, Mapping) or not report.get("changed"):
        return None
    out = dict(report)
    out.setdefault("llm_calls", 0)
    return out


def _maybe_compact_body_transitions(root_path: Path) -> Optional[dict[str, Any]]:
    """Atomically compact ``body/transitions.jsonl`` when oversized, with 0 writes when absent or bounded."""

    try:
        from components.world.scripts import instance_paths as _instance_paths
    except Exception:  # pragma: no cover
        try:
            import instance_paths as _instance_paths  # type: ignore[no-redef]
        except Exception:
            return None

    paths = _instance_paths.resolve_instance_paths(root_path)
    tr_path = paths.body_transitions_jsonl
    if tr_path.is_symlink() or not tr_path.exists() or not tr_path.is_file():
        return None

    try:
        from components.world.scripts import body_runtime as br
    except Exception:  # pragma: no cover
        try:
            import body_runtime as br  # type: ignore[no-redef]
        except Exception:
            return None

    try:
        report = br.BodyStore(paths.body_dir).compact_transitions_if_needed()
    except Exception:
        return None
    if not isinstance(report, Mapping) or not report.get("compacted"):
        return None
    out = dict(report)
    out.setdefault("llm_calls", 0)
    return out


def _maybe_tick_autonomy_rhythm(
    root_path: Path,
    now_iso: Optional[str],
) -> Optional[dict[str, Any]]:
    """Promote due or live-released ``QUEUED_ON_PHONE`` deferred replies to ``READY_TO_REPLY`` with 0 LLM calls.

    Reads live zero-write World/Body/Desk/Somatic snapshots via
    ``build_autonomy_live_snapshots_zero_write`` and passes them into
    ``tick_autonomy_rhythm_readiness``. Strictly zero-write when
    ``phone/autonomy_rhythm.json`` is absent or nothing needs promotion or sync.
    """

    rhythm_path = root_path / "phone" / "autonomy_rhythm.json"
    if rhythm_path.is_symlink() or not rhythm_path.exists() or not rhythm_path.is_file():
        return None

    try:
        from components.phone.autonomy_rhythm_view import (
            build_autonomy_live_snapshots_zero_write,
            tick_autonomy_rhythm_readiness,
        )
    except Exception:  # pragma: no cover
        return None

    try:
        snapshots = build_autonomy_live_snapshots_zero_write(
            root_path,
            now_iso=now_iso,
        )
        return tick_autonomy_rhythm_readiness(
            root_path,
            now_iso=now_iso,
            world_result=snapshots.get("world_result"),
            body_view=snapshots.get("body_view"),
            desk_result=snapshots.get("desk_result"),
            somatic_state=snapshots.get("somatic_state"),
        )
    except Exception:
        return None


def _maybe_tick_proactive_outreach(
    root_path: Path,
    now_iso: Optional[str],
) -> Optional[dict[str, Any]]:
    """Evaluate high-threshold lived outreach triggers ONLY on initialized roots with 0 LLM calls.

    Strictly zero-write when ``phone/v03_ecology.json`` is absent (preserving bare-dir
    ``skipped_uninitialized``) or when no high-threshold trigger enqueues an item.
    """

    eco_path = root_path / "phone" / "v03_ecology.json"
    if eco_path.is_symlink() or not eco_path.exists() or not eco_path.is_file():
        return None
    if eco_path.parent.exists() and eco_path.parent.is_symlink():
        return None

    try:
        from components.life.scripts import proactive_outreach_producer as outreach_prod
    except Exception:  # pragma: no cover
        try:
            import proactive_outreach_producer as outreach_prod  # type: ignore[no-redef]
        except Exception:
            return None

    eval_fn = getattr(outreach_prod, "evaluate_heartbeat_outreach_triggers", None)
    if not callable(eval_fn):
        return None

    try:
        report = eval_fn(root_path, now_iso=now_iso)
    except Exception:
        return None
    if not isinstance(report, Mapping):
        return None
    if int(report.get("enqueued_count") or 0) <= 0:
        return None
    out = dict(report)
    out.setdefault("llm_calls", 0)
    return out


def _atomic_write_json_0600(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise RuntimeError("SYMLINK_FORBIDDEN")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise RuntimeError("SYMLINK_FORBIDDEN")
    encoded = (json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _parse_any_iso_utc(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sweep_legacy_phone_everyday_desires(
    cand: Path,
    now_dt: datetime,
    effective_iso: str,
) -> tuple[int, int, int]:
    """Sweep real ``created_at + ttl_seconds`` & optional ``expires_at`` on legacy ``phone/everyday_desires.json``."""
    try:
        raw_doc = json.loads(cand.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0, 0
    if not isinstance(raw_doc, dict):
        return 0, 0, 0

    doc = json.loads(json.dumps(raw_doc))
    file_faded = 0
    file_decayed = 0

    if isinstance(doc.get("turn_only_buffer"), list):
        kept_buf: list[dict[str, Any]] = []
        buf_faded = 0
        for item in doc["turn_only_buffer"]:
            if not isinstance(item, Mapping):
                buf_faded += 1
                continue
            expired = False
            exp_dt = _parse_any_iso_utc(item.get("expires_at") or item.get("ttl_until"))
            if exp_dt is not None and now_dt >= exp_dt:
                expired = True
            if not expired:
                created_dt = _parse_any_iso_utc(item.get("created_at"))
                raw_ttl = item.get("ttl_seconds")
                ttl_sec = (
                    int(raw_ttl)
                    if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) and raw_ttl > 0
                    else 900
                )
                if created_dt is not None and (now_dt - created_dt).total_seconds() >= ttl_sec:
                    expired = True
            if expired:
                buf_faded += 1
            else:
                kept_buf.append(dict(item))
        if buf_faded > 0:
            file_faded += buf_faded
            doc["turn_only_buffer"] = kept_buf
            doc["naturally_faded_count"] = int(doc.get("naturally_faded_count") or 0) + buf_faded

    if isinstance(doc.get("retained_concerns"), list):
        kept_ret: list[dict[str, Any]] = []
        ret_faded = 0
        for entry in doc["retained_concerns"]:
            if not isinstance(entry, Mapping):
                ret_faded += 1
                continue
            row = dict(entry)
            exp_dt = _parse_any_iso_utc(row.get("expires_at") or row.get("ttl_until"))
            if exp_dt is not None and now_dt >= exp_dt:
                ret_faded += 1
                continue
            created_dt = _parse_any_iso_utc(row.get("created_at"))
            raw_ttl = row.get("ttl_seconds")
            if (
                created_dt is not None
                and isinstance(raw_ttl, int)
                and not isinstance(raw_ttl, bool)
                and raw_ttl > 0
                and (now_dt - created_dt).total_seconds() >= raw_ttl
            ):
                ret_faded += 1
                continue
            decay_rate = row.get("decay_per_hour")
            intensity = row.get("intensity")
            if (
                isinstance(decay_rate, (int, float))
                and not isinstance(decay_rate, bool)
                and float(decay_rate) > 0.0
                and isinstance(intensity, (int, float))
                and not isinstance(intensity, bool)
            ):
                anchor_dt = _parse_any_iso_utc(
                    row.get("updated_at") or row.get("created_at") or doc.get("updated_at")
                )
                if anchor_dt is not None and now_dt > anchor_dt:
                    elapsed_h = (now_dt - anchor_dt).total_seconds() / 3600.0
                    if elapsed_h > 0.0:
                        new_int = round(max(0.0, float(intensity) - float(decay_rate) * elapsed_h), 4)
                        if new_int <= 0.08:
                            ret_faded += 1
                            continue
                        if new_int != round(float(intensity), 4):
                            row["intensity"] = new_int
                            row["updated_at"] = effective_iso
                            file_decayed += 1
            kept_ret.append(row)
        if ret_faded > 0:
            file_faded += ret_faded
            doc["retained_concerns"] = kept_ret
            doc["naturally_faded_count"] = int(doc.get("naturally_faded_count") or 0) + ret_faded
        elif file_decayed > 0:
            doc["retained_concerns"] = kept_ret

    if file_faded > 0 or file_decayed > 0:
        next_rev = int(doc.get("revision") or 0) + 1
        doc["revision"] = next_rev
        doc["updated_at"] = effective_iso
        try:
            _atomic_write_json_0600(cand, doc)
        except Exception:
            return 0, 0, 0
        return file_faded, file_decayed, next_rev
    return 0, 0, 0


def _is_spark_desire_tick_due_zero_write(
    spark_path: Path,
    now_dt: datetime,
    *,
    weather_changed: bool,
) -> bool:
    if weather_changed:
        return True
    try:
        raw_doc = json.loads(spark_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw_doc, Mapping):
        return False
    anchor_raw = (
        raw_doc.get("last_desire_tick_at")
        or raw_doc.get("last_emerge_at")
        or raw_doc.get("updated_at")
    )
    anchor_dt = _parse_any_iso_utc(anchor_raw)
    if anchor_dt is None or (now_dt - anchor_dt).total_seconds() >= 900.0:
        return True
    for item in raw_doc.get("desires") or []:
        if not isinstance(item, Mapping) or item.get("status") != "active":
            continue
        exp_dt = _parse_any_iso_utc(item.get("expires_at") or item.get("ttl_until"))
        if exp_dt is not None and now_dt >= exp_dt:
            return True
        created_dt = _parse_any_iso_utc(item.get("created_at"))
        raw_ttl = item.get("ttl_seconds")
        if (
            created_dt is not None
            and isinstance(raw_ttl, int)
            and not isinstance(raw_ttl, bool)
            and raw_ttl > 0
            and (now_dt - created_dt).total_seconds() >= raw_ttl
        ):
            return True
        item_anchor = _parse_any_iso_utc(
            item.get("updated_at") or item.get("created_at") or raw_doc.get("updated_at")
        )
        raw_int = item.get("intensity")
        raw_rate = item.get("decay_per_hour")
        if (
            item_anchor is not None
            and now_dt > item_anchor
            and isinstance(raw_int, (int, float))
            and not isinstance(raw_int, bool)
            and isinstance(raw_rate, (int, float))
            and not isinstance(raw_rate, bool)
            and float(raw_rate) > 0.0
        ):
            elapsed_h = (now_dt - item_anchor).total_seconds() / 3600.0
            if float(raw_int) - float(raw_rate) * elapsed_h <= 0.08:
                return True
    return False


def _maybe_tick_everyday_desires(
    root_path: Path,
    now_iso: Optional[str],
    *,
    weather_changed: bool = False,
) -> Optional[dict[str, Any]]:
    """Advance real desire TTL/decay & bounded autonomous emergence ONLY when state exists & due.

    Never creates ``everyday_desires.json`` or ``v05_inner_spark.json`` on bare unbootstrapped
    directories and performs zero disk writes when nothing is due or changed.
    """

    life_desires_path = root_path / "life" / "everyday_desires.json"
    phone_desires_path = root_path / "phone" / "everyday_desires.json"
    spark_path = root_path / "life" / "v05_inner_spark.json"

    def _is_safe_file(p: Path) -> bool:
        return (
            not p.is_symlink()
            and p.exists()
            and p.is_file()
            and not (p.parent.exists() and p.parent.is_symlink())
        )

    life_exists = _is_safe_file(life_desires_path)
    phone_exists = _is_safe_file(phone_desires_path)
    spark_exists = _is_safe_file(spark_path)

    if not life_exists and not phone_exists and not spark_exists:
        return None

    effective_iso = now_iso or _now_iso()
    now_dt = _parse_any_iso_utc(effective_iso)
    if now_dt is None:
        return None

    total_faded = 0
    total_decayed = 0
    total_emerged = 0
    total_evicted = 0
    updated_paths: list[str] = []
    last_rev = 0
    spark_tick_rep: Optional[dict[str, Any]] = None
    spark_emerge_rep: Optional[dict[str, Any]] = None
    everyday_rep: Optional[dict[str, Any]] = None

    spark_due = spark_exists and _is_spark_desire_tick_due_zero_write(
        spark_path,
        now_dt,
        weather_changed=weather_changed,
    )

    if spark_due:
        try:
            from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark
        except Exception:  # pragma: no cover
            try:
                import v05_dreams_desires_foreshadow as v05_spark  # type: ignore[no-redef]
            except Exception:
                v05_spark = None  # type: ignore[assignment]
        if v05_spark is not None:
            try:
                tick_res = v05_spark.tick_desire_lifecycle(root_path, now_iso=effective_iso)
                if isinstance(tick_res, Mapping) and tick_res.get("changed"):
                    spark_tick_rep = dict(tick_res)
                    total_faded += int(tick_res.get("faded_count") or 0)
                    total_decayed += int(tick_res.get("decayed_count") or 0)
                    total_evicted += int(tick_res.get("evicted_count") or 0)
                    if tick_res.get("spark_changed"):
                        updated_paths.append(str(spark_path))
                    if tick_res.get("everyday_changed") and life_exists:
                        updated_paths.append(str(life_desires_path))
                    if isinstance(tick_res.get("everyday_desires_receipt"), Mapping):
                        everyday_rep = dict(tick_res["everyday_desires_receipt"])
            except Exception:
                pass
            try:
                emerge_res = v05_spark.emerge_autonomous_desires(root_path, now_iso=effective_iso)
                if isinstance(emerge_res, Mapping) and emerge_res.get("changed"):
                    spark_emerge_rep = dict(emerge_res)
                    total_emerged += int(emerge_res.get("emerged_count") or 0)
                    total_evicted += len(emerge_res.get("evicted_desires") or [])
                    if str(spark_path) not in updated_paths:
                        updated_paths.append(str(spark_path))
                    if (
                        life_exists
                        and isinstance(emerge_res.get("everyday_sync"), Mapping)
                        and emerge_res["everyday_sync"].get("changed")
                        and str(life_desires_path) not in updated_paths
                    ):
                        updated_paths.append(str(life_desires_path))
                    if isinstance(emerge_res.get("revision"), int):
                        last_rev = max(last_rev, int(emerge_res["revision"]))
            except Exception:
                pass
    elif life_exists:
        try:
            from components.life.scripts import everyday_desire_lifecycle as edl
        except Exception:  # pragma: no cover
            try:
                import everyday_desire_lifecycle as edl  # type: ignore[no-redef]
            except Exception:
                edl = None  # type: ignore[assignment]
        if edl is not None:
            try:
                # When spark decay/emergence isn't due (<900s), only sweep real expired TTLs &
                # source-lost concerns without 60s continuous intensity float churn.
                ed_res = edl.sweep_everyday_desires_ttl_and_decay(
                    root_path,
                    now_iso=effective_iso,
                    decay_intensity=not spark_exists,
                )
                if isinstance(ed_res, Mapping) and ed_res.get("changed"):
                    everyday_rep = dict(ed_res)
                    total_faded += int(ed_res.get("faded_count") or 0)
                    total_decayed += int(ed_res.get("decayed_count") or 0)
                    updated_paths.append(str(life_desires_path))
                    if isinstance(ed_res.get("revision"), int):
                        last_rev = max(last_rev, int(ed_res["revision"]))
            except Exception:
                pass

    if phone_exists:
        pf, pd, prev = _sweep_legacy_phone_everyday_desires(
            phone_desires_path,
            now_dt,
            effective_iso,
        )
        if pf > 0 or pd > 0:
            total_faded += pf
            total_decayed += pd
            updated_paths.append(str(phone_desires_path))
            last_rev = max(last_rev, prev)

    if not updated_paths:
        return None

    out: dict[str, Any] = {
        "ok": True,
        "changed": True,
        "faded_count": total_faded,
        "decayed_count": total_decayed,
        "emerged_count": total_emerged,
        "evicted_count": total_evicted,
        "updated_paths": updated_paths,
        "revision": last_rev,
        "updated_at": effective_iso,
        "frustration_debt": 0,
        "llm_calls": 0,
    }
    if everyday_rep is not None:
        out["everyday_desires_receipt"] = everyday_rep
    if spark_tick_rep is not None:
        out["spark_tick_receipt"] = spark_tick_rep
    if spark_emerge_rep is not None:
        out["spark_emerge_receipt"] = spark_emerge_rep
    return out


def _extract_weather_from_nb_report(nb_report: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Read the current weather string from a v0.4 neighborhood tick receipt (zero-write)."""
    if not isinstance(nb_report, Mapping):
        return None
    wp = nb_report.get("weather_pulse")
    if isinstance(wp, Mapping):
        w = str(wp.get("weather") or "").strip().lower()
        if w:
            return w
    env = nb_report.get("environment")
    if isinstance(env, Mapping):
        w = str(env.get("weather") or "").strip().lower()
        if w:
            return w
    w = str(nb_report.get("weather") or "").strip().lower()
    return w or None


def _maybe_tick_v09_world_commitments(
    root_path: Path,
    now_iso: Optional[str],
    *,
    weather_changed: bool = False,
    new_weather: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """O(1) zero-spin 60s heartbeat hook for v0.9 NPC & venue commitments (Blueprint §6.2).

    Calls ``world_commitments_engine.check_heartbeat_needed_zero_write`` first and
    returns ``None`` immediately (0 disk writes, 0 ``.lock`` files, 0 LLM calls) when
    the state file is absent OR when ``now < min_next_due_at`` and weather has not
    materially affected an open weather-sensitive commitment.
    """
    try:
        from components.world.scripts import world_commitments_engine as _wce
    except Exception:  # pragma: no cover
        try:
            import world_commitments_engine as _wce  # type: ignore[no-redef]
        except Exception:
            return None

    effective_iso = now_iso or _now_iso()
    clean_weather = (
        str(new_weather).strip().lower()
        if isinstance(new_weather, str) and new_weather.strip()
        else None
    )

    try:
        if not _wce.check_heartbeat_needed_zero_write(
            root_path,
            now_iso=effective_iso,
            current_weather=clean_weather,
        ):
            return None
    except Exception:
        return None

    transitions: list[dict[str, Any]] = []
    affected_commitments: list[dict[str, Any]] = []
    notices_posted: list[dict[str, Any]] = []
    shelf_releases: list[dict[str, Any]] = []
    disk_written = False
    min_next_due_at: Optional[str] = None
    weather_receipt: Optional[dict[str, Any]] = None
    due_receipt: Optional[dict[str, Any]] = None

    if weather_changed and clean_weather:
        try:
            w_res = _wce.notify_world_condition_changed(
                root_path,
                condition_kind="weather",
                new_value=clean_weather,
                now_iso=effective_iso,
            )
            if isinstance(w_res, Mapping):
                weather_receipt = dict(w_res)
                affected_commitments.extend(list(w_res.get("affected_commitments") or []))
                notices_posted.extend(list(w_res.get("notices_posted") or []))
                if w_res.get("disk_written"):
                    disk_written = True
                if w_res.get("min_next_due_at") is not None:
                    min_next_due_at = str(w_res.get("min_next_due_at"))
        except Exception:
            weather_receipt = None

    still_needed = False
    try:
        still_needed = _wce.check_heartbeat_needed_zero_write(
            root_path,
            now_iso=effective_iso,
            current_weather=clean_weather,
        )
    except Exception:
        still_needed = False

    if still_needed:
        try:
            d_res = _wce.advance_due_commitments(
                root_path,
                now_iso=effective_iso,
                current_weather=clean_weather,
            )
            if isinstance(d_res, Mapping):
                due_receipt = dict(d_res)
                transitions.extend(list(d_res.get("transitions") or []))
                notices_posted.extend(list(d_res.get("notices_posted") or []))
                shelf_releases.extend(list(d_res.get("shelf_releases") or []))
                if d_res.get("disk_written"):
                    disk_written = True
                min_next_due_at = (
                    str(d_res.get("min_next_due_at"))
                    if d_res.get("min_next_due_at") is not None
                    else None
                )
        except Exception:
            due_receipt = None

    if not disk_written and not transitions and not affected_commitments:
        return None

    out: dict[str, Any] = {
        "ok": True,
        "status": "advanced" if (transitions or affected_commitments) else "weather_synced",
        "advanced_count": len(transitions),
        "affected_count": len(affected_commitments),
        "transitions": transitions,
        "affected_commitments": affected_commitments,
        "notices_posted": notices_posted,
        "shelf_releases": shelf_releases,
        "min_next_due_at": min_next_due_at,
        "disk_written": bool(disk_written),
        "llm_calls": 0,
    }
    if weather_receipt is not None:
        out["weather_condition_receipt"] = weather_receipt
    if due_receipt is not None:
        out["due_advance_receipt"] = due_receipt
    return out


def run_v03_background_clock_tick(
    root: Path,
    *,
    now_iso: Optional[str] = None,
    force_init: bool = False,
) -> dict[str, Any]:
    """Advance 0.3/v0.4 deterministic world clock & slow variables with ``llm_calls == 0``.

    When ``force_init=False`` and ``v03_clock_state.json`` is absent on disk, no new
    JSON state file is created so clean/uninitialized directories remain 100% zero-write.
    """

    try:
        from components.life.scripts.v03_clock_evaporation import (
            mutate_v03_clock_view,
            v03_clock_state_path,
        )
    except Exception:  # pragma: no cover
        from v03_clock_evaporation import (  # type: ignore[no-redef]
            mutate_v03_clock_view,
            v03_clock_state_path,
        )

    root_path = Path(root)
    clock_path = v03_clock_state_path(root_path)
    clock_exists = clock_path.exists() or clock_path.is_symlink()

    if not force_init and not clock_exists:
        nb_report = _maybe_tick_v04_neighborhood(root_path, now_iso)
        nb_weather_changed = bool((nb_report or {}).get("weather_changed"))
        nb_new_weather = _extract_weather_from_nb_report(nb_report)
        town_report = _maybe_tick_v05_living_town(
            root_path,
            now_iso,
            weather_changed=nb_weather_changed,
        )
        spark_report = _maybe_tick_v05_inner_spark(root_path, now_iso)
        eco_report = _maybe_tick_v03_ecology(root_path, now_iso)
        rhythm_report = _maybe_tick_autonomy_rhythm(root_path, now_iso)
        desire_report = _maybe_tick_everyday_desires(
            root_path,
            now_iso,
            weather_changed=nb_weather_changed,
        )
        outreach_report = _maybe_tick_proactive_outreach(root_path, now_iso)
        v09_commitments_report = _maybe_tick_v09_world_commitments(
            root_path,
            now_iso,
            weather_changed=nb_weather_changed,
            new_weather=nb_new_weather,
        )
        compact_report = _maybe_compact_body_transitions(root_path)
        if (
            nb_report is None
            and town_report is None
            and spark_report is None
            and eco_report is None
            and rhythm_report is None
            and desire_report is None
            and outreach_report is None
            and v09_commitments_report is None
            and compact_report is None
        ):
            return {
                "ok": True,
                "status": "skipped_uninitialized",
                "root": str(root_path),
                "llm_calls": 0,
                "model_called": False,
                "slices_ticked": [],
                "settled_slow_vars": 0,
                "evaporated_count": 0,
                "faded_missing_notes": 0,
                "unknown_actions_guessed": 0,
                "cognitive_backfill_count": 0,
            }
        ticked_only: list[str] = []
        settled_only = 0
        if nb_report is not None:
            ticked_only.append("v04_neighborhood")
            settled_only += int(nb_report.get("settled_count") or nb_report.get("changes_count") or (1 if nb_report.get("changed") else 0))
        if town_report is not None:
            ticked_only.append("v05_living_town")
            settled_only += 1
        if spark_report is not None:
            ticked_only.append("v05_inner_spark")
            settled_only += (
                (1 if spark_report.get("frost_updated") else 0)
                + (1 if spark_report.get("morning_dream_settled") else 0)
            )
        if eco_report is not None:
            ticked_only.append("v03_ecology")
            settled_only += (
                (1 if (eco_report.get("phase_changed") or eco_report.get("neglect_strain_recorded")) else 0)
                + len(eco_report.get("withdrawn_expired_outbound") or [])
            )
        if rhythm_report is not None:
            ticked_only.append("autonomy_rhythm")
            settled_only += int(rhythm_report.get("promoted_count") or 1)
        if desire_report is not None:
            ticked_only.append("everyday_desires")
            settled_only += int(
                (desire_report.get("faded_count") or 0)
                + (desire_report.get("decayed_count") or 0)
                + (desire_report.get("emerged_count") or 0)
            ) or 1
        if outreach_report is not None:
            ticked_only.append("proactive_outreach")
            settled_only += int(outreach_report.get("enqueued_count") or 1)
        if v09_commitments_report is not None:
            ticked_only.append("v09_world_commitments")
            settled_only += int(
                (v09_commitments_report.get("advanced_count") or 0)
                + (v09_commitments_report.get("affected_count") or 0)
            ) or 1
        if compact_report is not None:
            ticked_only.append("body_transitions_compaction")
        partial_out: dict[str, Any] = {
            "ok": True,
            "status": "ticked",
            "http_status": 200,
            "root": str(root_path),
            "at": now_iso or _now_iso(),
            "llm_calls": 0,
            "model_called": False,
            "slices_ticked": ticked_only,
            "settled_slow_vars": settled_only,
            "evaporated_count": 0,
            "faded_missing_notes": 0,
            "unknown_actions_guessed": 0,
            "cognitive_backfill_count": 0,
        }
        if nb_report is not None:
            partial_out["v04_neighborhood_receipt"] = nb_report
        if town_report is not None:
            partial_out["v05_living_town_receipt"] = town_report
        if spark_report is not None:
            partial_out["v05_inner_spark_receipt"] = spark_report
        if eco_report is not None:
            partial_out["ecology_receipt"] = eco_report
        if rhythm_report is not None:
            partial_out["autonomy_rhythm_receipt"] = rhythm_report
        if desire_report is not None:
            partial_out["everyday_desires_receipt"] = desire_report
        if outreach_report is not None:
            partial_out["proactive_outreach_receipt"] = outreach_report
        if v09_commitments_report is not None:
            partial_out["v09_world_commitments_receipt"] = v09_commitments_report
        if compact_report is not None:
            partial_out["body_transitions_compaction_receipt"] = compact_report
        return partial_out

    http_status, clock_envelope = mutate_v03_clock_view(
        root_path,
        {"action": "advance_projection", "sync_v02": True},
        actor_side="subject",
        now_iso=now_iso,
    )
    if not clock_envelope.get("ok"):
        reason_code = str(clock_envelope.get("reason_code") or "CLOCK_TICK_REJECTED")
        return {
            "ok": False,
            "status": str(clock_envelope.get("status") or "rejected"),
            "http_status": int(http_status),
            "reason_code": reason_code,
            "reason": reason_code,
            "root": str(root_path),
            "llm_calls": 0,
            "model_called": False,
            "slices_ticked": [],
            "settled_slow_vars": 0,
            "evaporated_count": 0,
            "faded_missing_notes": 0,
            "unknown_actions_guessed": 0,
            "cognitive_backfill_count": 0,
        }

    clock_data = dict(clock_envelope.get("data") or {})
    action_receipt = dict(clock_data.get("action_receipt") or {})
    committed_at = str(
        action_receipt.get("to_at")
        or ((clock_data.get("clocks") or {}).get("wall_clock") or {}).get("now_iso")
        or now_iso
        or _now_iso()
    )
    slices_ticked = ["v03_clock"]
    settled_slow_vars = int(action_receipt.get("settled_slow_vars") or 0)

    nb_report = _maybe_tick_v04_neighborhood(root_path, committed_at)
    if nb_report is not None:
        slices_ticked.append("v04_neighborhood")
        settled_slow_vars += int(
            nb_report.get("settled_count")
            or nb_report.get("changes_count")
            or (1 if nb_report.get("changed") else 0)
        )

    town_report = _maybe_tick_v05_living_town(
        root_path,
        committed_at,
        weather_changed=bool((nb_report or {}).get("weather_changed")),
    )
    if town_report is not None:
        slices_ticked.append("v05_living_town")
        settled_slow_vars += 1

    spark_report = _maybe_tick_v05_inner_spark(root_path, committed_at)
    if spark_report is not None:
        slices_ticked.append("v05_inner_spark")
        settled_slow_vars += (
            (1 if spark_report.get("frost_updated") else 0)
            + (1 if spark_report.get("morning_dream_settled") else 0)
        )

    eco_report = _maybe_tick_v03_ecology(root_path, committed_at)
    if eco_report is not None:
        slices_ticked.append("v03_ecology")
        settled_slow_vars += (
            (1 if (eco_report.get("phase_changed") or eco_report.get("neglect_strain_recorded")) else 0)
            + len(eco_report.get("withdrawn_expired_outbound") or [])
        )

    rhythm_report = _maybe_tick_autonomy_rhythm(root_path, committed_at)
    if rhythm_report is not None:
        slices_ticked.append("autonomy_rhythm")
        settled_slow_vars += int(rhythm_report.get("promoted_count") or 1)

    desire_report = _maybe_tick_everyday_desires(
        root_path,
        committed_at,
        weather_changed=bool((nb_report or {}).get("weather_changed")),
    )
    if desire_report is not None:
        slices_ticked.append("everyday_desires")
        settled_slow_vars += int(
            (desire_report.get("faded_count") or 0)
            + (desire_report.get("decayed_count") or 0)
            + (desire_report.get("emerged_count") or 0)
        ) or 1

    outreach_report = _maybe_tick_proactive_outreach(root_path, committed_at)
    if outreach_report is not None:
        slices_ticked.append("proactive_outreach")
        settled_slow_vars += int(outreach_report.get("enqueued_count") or 1)

    v09_commitments_report = _maybe_tick_v09_world_commitments(
        root_path,
        committed_at,
        weather_changed=bool((nb_report or {}).get("weather_changed")),
        new_weather=_extract_weather_from_nb_report(nb_report),
    )
    if v09_commitments_report is not None:
        slices_ticked.append("v09_world_commitments")
        settled_slow_vars += int(
            (v09_commitments_report.get("advanced_count") or 0)
            + (v09_commitments_report.get("affected_count") or 0)
        ) or 1

    compact_report = _maybe_compact_body_transitions(root_path)
    if compact_report is not None:
        slices_ticked.append("body_transitions_compaction")

    out: dict[str, Any] = {
        "ok": True,
        "status": "ticked",
        "http_status": int(http_status),
        "root": str(root_path),
        "at": committed_at,
        "revision": int(clock_data.get("revision") or 0),
        "llm_calls": 0,
        "model_called": False,
        "slices_ticked": slices_ticked,
        "elapsed_seconds": int(action_receipt.get("elapsed_seconds") or 0),
        "settled_slow_vars": settled_slow_vars,
        "evaporated_count": int(action_receipt.get("evaporated_count") or 0),
        "faded_missing_notes": int(action_receipt.get("faded_missing_notes") or 0),
        "ambient_date_switched": bool(action_receipt.get("ambient_date_switched", False)),
        "unknown_actions_guessed": 0,
        "cognitive_backfill_count": 0,
        "cognitive_backfill_forbidden": True,
        "pending_unknown_count": len(
            ((clock_data.get("iron_laws") or {}).get("pending_unknown_actions") or [])
        ),
        "v02_synced_slices": list(
            ((action_receipt.get("v02_sync") or {}).get("synced_slices") or [])
        ),
        "clock_receipt": action_receipt,
    }
    if nb_report is not None:
        out["v04_neighborhood_receipt"] = nb_report
    if town_report is not None:
        out["v05_living_town_receipt"] = town_report
    if spark_report is not None:
        out["v05_inner_spark_receipt"] = spark_report
    if eco_report is not None:
        out["ecology_receipt"] = eco_report
    if rhythm_report is not None:
        out["autonomy_rhythm_receipt"] = rhythm_report
    if desire_report is not None:
        out["everyday_desires_receipt"] = desire_report
    if outreach_report is not None:
        out["proactive_outreach_receipt"] = outreach_report
    if v09_commitments_report is not None:
        out["v09_world_commitments_receipt"] = v09_commitments_report
    if compact_report is not None:
        out["body_transitions_compaction_receipt"] = compact_report
    return out


def _start_v03_clock_heartbeat(
    root: Path,
    stop: threading.Event,
    interval_seconds: Optional[float] = None,
) -> Optional[threading.Thread]:
    """Start a lightweight zero-LLM background daemon thread gated on ``stop.wait(interval)``."""

    interval = _resolve_v03_clock_interval(interval_seconds)
    if interval <= 0.0 or stop.is_set():
        return None

    root_path = Path(root)

    def _loop() -> None:
        while not stop.wait(interval):
            if stop.is_set():
                break
            try:
                run_v03_background_clock_tick(root_path, force_init=False)
            except Exception:
                pass

    worker = threading.Thread(
        target=_loop,
        name="nyairo-v03-clock-heartbeat",
        daemon=True,
    )
    worker.start()
    return worker


def _serve_forever(root: Path, port: int, *, room_living: bool = False,
                   world_gate: str = DEFAULT_WORLD_GATE,
                   ambient_events: bool = False,
                   v03_clock_interval: Optional[float] = None) -> int:
    """The child process: the existing ``demo.serve_instance`` plus a clean stop event."""

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
            "world_gate": str(world_gate), "world_actions": str(world_gate) != "off",
            "ambient_events": bool(ambient_events),
            "foreground": True,
            "log": str(_log_path(root)),
        })
        sys.stdout.write("服务就绪，尚未开始自主活动。\n")
        sys.stdout.flush()

    os.environ.setdefault("NYAIRO_V02_CONTROL_ROOT", str(_default_v02_control_root(root)))

    gate = str(world_gate or DEFAULT_WORLD_GATE).strip().lower()
    if gate not in WORLD_GATES:
        gate = DEFAULT_WORLD_GATE
    heartbeat_thread: Optional[threading.Thread] = None
    try:
        heartbeat_thread = _start_v03_clock_heartbeat(
            root, stop, interval_seconds=v03_clock_interval
        )
        demo.serve_instance(root, port=int(port), stop_event=stop, on_ready=_on_ready,
                            room_living=bool(room_living),
                            ambient_events=bool(ambient_events),
                            # 日常入口按自己的 gate 对**本进程树**开启本地世界动作；显式
                            # off 会作为 off 传给子进程，不被外部环境或旧实例特征改回去。
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
        stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
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
                      world_gate: str = DEFAULT_WORLD_GATE,
                      ambient_events: bool = False,
                      v03_clock_interval: Optional[float] = None
                      ) -> tuple[Optional[subprocess.Popen], str]:
    log_path = _log_path(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "_serve",
               "--root", str(root), "--port", str(port),
               "--world-gate", str(world_gate)]
    if room_living:
        command.append("--room-living")
    if ambient_events:
        command.append("--ambient-events")
    if v03_clock_interval is not None:
        command.extend(["--v03-clock-interval", str(float(v03_clock_interval))])
    child_env = dict(os.environ)
    child_env.setdefault("NYAIRO_V02_CONTROL_ROOT", str(_default_v02_control_root(root)))
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT), env=child_env, start_new_session=True, close_fds=True)
    finally:
        handle.close()
    _write_json(_record_path(root), {
        "schema": SCHEMA_RECORD, "pid": child.pid, "port": int(port), "root": str(root),
        "started_at": _now_iso(), "status": "STARTING", "model_enabled": False,
        "room_living": bool(room_living), "world_gate": str(world_gate),
        "world_actions": str(world_gate) != "off",
        "ambient_events": bool(ambient_events),
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


_SUPPLY_SERVE_SOCK_RE = re.compile(r"^supply-serve-(?:.*-)?(\d+)\.sock$")


def _unix_socket_has_listener(sock_path: Path) -> bool:
    """Return True if an active listener responds to connect() on this UNIX socket."""
    if not hasattr(socket, "AF_UNIX"):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(str(sock_path))
        return True
    except OSError:
        return False


def _cleanup_stale_supply_sockets(root: Path) -> list[str]:
    """Safely unlink orphan ``supply-serve-*.sock`` files under ``<root>/run/``.

    Never unlinks a symlink, a non-socket file, the live recorded daemon's active
    ``supply_socket``, a socket whose embedded PID is still alive, or a socket with
    an active listener.
    """
    run_dir = Path(root) / "run"
    if not run_dir.is_dir() or run_dir.is_symlink():
        return []

    protected_paths: set[str] = set()
    record = _read_json(_record_path(Path(root)))
    if _pid_alive(record.get("pid")):
        inst = _read_json(Path(root) / "instance.json")
        active_sock = str(inst.get("supply_socket") or "").strip()
        if active_sock:
            try:
                protected_paths.add(str(Path(active_sock).resolve()))
            except OSError:
                protected_paths.add(active_sock)

    removed: list[str] = []
    try:
        entries = list(run_dir.iterdir())
    except OSError:
        return removed

    for entry in entries:
        match = _SUPPLY_SERVE_SOCK_RE.match(entry.name)
        if not match:
            continue
        try:
            st = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISSOCK(st.st_mode):
            continue
        try:
            resolved_entry = str(entry.resolve())
        except OSError:
            resolved_entry = str(entry)
        if resolved_entry in protected_paths or str(entry) in protected_paths:
            continue
        embedded_pid = int(match.group(1))
        if embedded_pid <= 0 or _pid_alive(embedded_pid):
            continue
        if _unix_socket_has_listener(entry):
            continue
        try:
            entry.unlink()
            removed.append(entry.name)
        except OSError:
            pass
    return removed


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
            lived_needs=lived_needs,
            no_lived_needs=bool(getattr(args, "no_lived_needs", False)))
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
    try:
        world_gate = _resolve_world_gate(args, config.get("world_gate"))
        v03_clock_interval = getattr(args, "v03_clock_interval", None)
        if v03_clock_interval is not None:
            _resolve_v03_clock_interval(v03_clock_interval)
    except RuntimeError as exc:
        return _refuse(_reason_of(exc), str(exc), as_json=json_output)
    ambient_events = _resolve_run_switch(args, "ambient_events", config.get("ambient_events"))

    port = int(getattr(args, "port", None) or DEFAULT_PORT)
    if _port_open(port):
        return _refuse("PORT_IN_USE",
                       "端口 %d 已经被另一个服务占用；换一个 --port，或先停掉旧服务。" % port,
                       as_json=json_output)
    # The run switches are this entry's own small preference: record them before
    # serving so a same-root restart (including foreground Ctrl-C) keeps them.
    _persist_run_switches(root, room_living=room_living, world_gate=world_gate,
                          ambient_events=ambient_events)
    _cleanup_stale_supply_sockets(root)

    if getattr(args, "foreground", False):
        sys.stdout.write("正在启动（前台；Ctrl-C 停止）…\n")
        sys.stdout.flush()
        return _serve_forever(root, port, room_living=room_living,
                              world_gate=world_gate,
                              ambient_events=ambient_events,
                              v03_clock_interval=v03_clock_interval)

    child, log_path = _spawn_background(root, port, room_living=room_living,
                                        world_gate=world_gate,
                                        ambient_events=ambient_events,
                                        v03_clock_interval=v03_clock_interval)
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
                   "world_gate": str(world_gate), "world_actions": str(world_gate) != "off",
                   "ambient_events": bool(ambient_events)})
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
        _cleanup_stale_supply_sockets(root)
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
    _cleanup_stale_supply_sockets(root)
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


def _timeline_read(root: Path, port: Optional[int]) -> dict[str, Any]:
    """She today's recorded timeline: HTTP when the service is up, otherwise a direct pure read."""

    if port:
        try:
            return _http_json(port, "/api/phone/today/timeline")
        except Exception:
            pass
    try:
        from components.phone import service as phone_service

        return phone_service.PhoneService(phone_service.load_instance(root)).day_timeline()
    except Exception as exc:
        return {"ok": False, "reason_code": "TIMELINE_READ_FAILED",
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
                state: Optional[Mapping[str, Any]] = None,
                timeline: Optional[Mapping[str, Any]] = None) -> str:
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
    if isinstance(timeline, Mapping):
        if timeline.get("ok"):
            data = dict(timeline.get("data") or {})
            ordering = dict(data.get("ordering") or {})
            lines.append("时间线：%s 条（%s；%s）" % (
                data.get("count"),
                "同域可比较" if ordering.get("comparable") else "多时域，按域分组",
                "只有骨架" if data.get("skeleton_only") else "含原入口引用"))
        else:
            lines.append("时间线：读不到（%s）" % (timeline.get("reason_code") or "UNKNOWN"))
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
    timeline = _timeline_read(root, port)
    try:
        state = _http_json(port, "/api/phone/state", timeout=10.0)
        # The CSRF token is for the page, not for an operator readout.
        state.pop("csrf_token", None)
    except Exception:
        state = {}
    payload = {"ok": True, "root": str(root), "port": port, "urls": _urls(port),
               "today": today, "arrangements": arrangements, "projects": projects,
               "timeline": timeline, "state": state, "config": _managed_config(root),
               "model_called": False}
    _emit(payload, as_json=json_output,
          text=None if json_output else _phone_text(root, port, today, arrangements, projects,
                                                    state, timeline))
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

    config = _managed_config(root) if _is_managed(root) else {}
    ambient_events = _resolve_run_switch(args, "ambient_events", config.get("ambient_events"))
    try:
        world_gate = _resolve_world_gate(args, config.get("world_gate"))
    except RuntimeError as exc:
        return _refuse(_reason_of(exc), str(exc), as_json=json_output)
    if _is_managed(root) and (getattr(args, "ambient_events", None) is not None
                              or getattr(args, "world_gate", None) is not None
                              or getattr(args, "world_actions", None) is not None):
        _persist_run_switches(root,
                              room_living=bool(config.get("room_living")),
                              world_gate=world_gate,
                              ambient_events=ambient_events)

    from components.phone import live_day

    sys.stdout.write(
        "准备真实模型运行（会花模型额度）：最长 %s 秒，最多 %s 次新请求；"
        "模型没配置好就不会发出任何请求。\n"
        "这些请求从本实例自己的持久账本继续扣，不重置、不换 run；失败也不退款。\n"
        % (duration, requests))
    sys.stdout.flush()
    try:
        # 显式 gate 值总传给真实的 serve/resident 环境：默认 canonical 让日常能出门，
        # 显式 off 也必须真的覆盖，而不是只改父进程后又被实例特征改回去。
        _run_overrides = {
            "WORLD_BODY_INTENT_MUTATION_GATE": world_gate,
            "NYAIRO_V02_CONTROL_ROOT": str(_default_v02_control_root(root)),
        }
        report = live_day.run_segment(
            root, duration_seconds=int(duration), max_new_requests=int(requests),
            poll_seconds=2, act=True, start_services=live_day.SERVICE_AUTO,
            room_living=bool(config.get("room_living")),
            ambient_events=ambient_events,
            feature_overrides=_run_overrides)
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
# one offline / manual deterministic 0.3 clock tick (zero LLM calls)
# ---------------------------------------------------------------------------------------------


def _clock_tick_text(report: Mapping[str, Any]) -> str:
    if report.get("status") == "skipped_uninitialized":
        return (
            "0.3 时钟状态尚未初始化（磁盘上没有 v03_clock_state.json，本次未写入任何文件，模型调用 0 次）。\n"
            "如果需要显式初始化并推进，请加上 --force-init。"
        )
    slices = ",".join(str(s) for s in (report.get("slices_ticked") or [])) or "none"
    return (
        "0.3 确定性时钟已推进至 %s（切片：%s）：结算慢变量 %s 项，自然蒸发闪念 %s 条，"
        "未决动作猜测 %s 项（模型调用 0 次）。"
        % (
            report.get("at") or "-",
            slices,
            report.get("settled_slow_vars", 0),
            report.get("evaporated_count", 0),
            report.get("unknown_actions_guessed", 0),
        )
    )


def cmd_clock_tick(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    json_output = bool(getattr(args, "json", False))
    if not root.is_absolute():
        return _refuse("ROOT_NOT_ABSOLUTE", "实例目录要写绝对路径",
                       as_json=json_output, extra={"llm_calls": 0})
    if root.exists() and not root.is_dir():
        return _refuse("ROOT_NOT_A_DIRECTORY", "这个路径不是目录",
                       as_json=json_output, extra={"llm_calls": 0})
    report = run_v03_background_clock_tick(
        root,
        now_iso=getattr(args, "now", None),
        force_init=bool(getattr(args, "force_init", False)),
    )
    if not report.get("ok"):
        reason = str(report.get("reason_code") or report.get("reason") or "CLOCK_TICK_REJECTED")
        _emit(report, as_json=json_output, text=None if json_output else _human(reason))
        return EXIT_REFUSED
    _emit(report, as_json=json_output, text=None if json_output else _clock_tick_text(report))
    return EXIT_OK


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
    parser.add_argument("--no-lived-needs", action="store_true", dest="no_lived_needs",
                        help="disable the default virtual hunger/thirst/toilet profile on a new instance")
    parser.add_argument("--lived-needs-hunger", type=float, default=None,
                        dest="lived_needs_hunger", help="explicit initial hunger 0..1 (default 0)")
    parser.add_argument("--lived-needs-thirst", type=float, default=None,
                        dest="lived_needs_thirst", help="explicit initial thirst 0..1 (default 0)")
    parser.add_argument("--lived-needs-toilet", type=float, default=None,
                        dest="lived_needs_toilet", help="explicit initial toilet 0..1 (default 0)")
    parser.add_argument("--starter-reading", action="store_true", dest="starter_reading",
                        help="when the package declares no reading material, give the new root "
                             "a deterministic theme+seed starter reading (explicit opt-in)")


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
    start.add_argument("--ambient-events", action="store_true", dest="ambient_events",
                       default=None,
                       help="显式开启世界环境事件（同步传入 World/Phone 与后续 run-day）；默认沿用本实例上次选择")
    start.add_argument("--no-ambient-events", action="store_false", dest="ambient_events",
                       help="显式关闭世界环境事件")
    start.add_argument("--world-gate", choices=WORLD_GATES, default=None, dest="world_gate",
                       help="本地世界动作 gate（日常默认 canonical；off 显式关闭）")
    start.add_argument("--world-actions", action="store_true", dest="world_actions", default=None,
                       help="旧写法：等同于 --world-gate canonical")
    start.add_argument("--no-world-actions", action="store_false", dest="world_actions",
                       help="旧写法：等同于 --world-gate off")
    start.add_argument("--v03-clock-interval", type=float, default=None,
                       dest="v03_clock_interval",
                       help="0.3 后台确定性时钟推进间隔秒数（默认读 NYAIRO_V03_CLOCK_INTERVAL 或 60 秒；<=0 不启线程）")
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
    run.add_argument("--ambient-events", action="store_true", dest="ambient_events",
                     default=None,
                     help="显式为本次运行开启世界环境事件；不写时沿用本实例上次 start/run-day 的选择（默认关闭）")
    run.add_argument("--no-ambient-events", action="store_false", dest="ambient_events",
                     help="显式关闭世界环境事件")
    run.add_argument("--world-gate", choices=WORLD_GATES, default=None, dest="world_gate",
                     help="本次日常运行的世界动作 gate（默认 canonical）")
    run.add_argument("--world-actions", action="store_true", dest="world_actions", default=None,
                     help="旧写法：等同于 --world-gate canonical")
    run.add_argument("--no-world-actions", action="store_false", dest="world_actions",
                     help="旧写法：等同于 --world-gate off")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=cmd_run_day)

    stop = sub.add_parser("stop", help="停止本入口启动的服务")
    stop.add_argument("--root", required=True)
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(handler=cmd_stop)

    clock_tick = sub.add_parser("clock-tick",
                                help="单步推进一次 0.3 零模型调用确定性时钟（默认只推进已有状态文件）")
    clock_tick.add_argument("--root", required=True)
    clock_tick.add_argument("--now", default=None, dest="now",
                            help="可选的带时区 ISO-8601 时间戳（默认当前 UTC 时间）")
    clock_tick.add_argument("--force-init", action="store_true", dest="force_init",
                            help="磁盘尚无 v03_clock_state.json 时显式初始化并推进")
    clock_tick.add_argument("--json", action="store_true")
    clock_tick.set_defaults(handler=cmd_clock_tick)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_serve":
        # 内部子进程入口：不出现在用户帮助里，只由 start 自己调用。
        internal = argparse.ArgumentParser(prog="nyairo_day.py _serve")
        internal.add_argument("--root", required=True)
        internal.add_argument("--port", type=int, required=True)
        internal.add_argument("--room-living", action="store_true", dest="room_living")
        internal.add_argument("--world-gate", choices=WORLD_GATES, default=DEFAULT_WORLD_GATE,
                              dest="world_gate")
        internal.add_argument("--ambient-events", action="store_true", dest="ambient_events")
        internal.add_argument("--v03-clock-interval", type=float, default=None,
                              dest="v03_clock_interval")
        parsed = internal.parse_args(arguments[1:])
        return _serve_forever(Path(parsed.root).expanduser(), int(parsed.port),
                              room_living=bool(parsed.room_living),
                              world_gate=str(parsed.world_gate),
                              ambient_events=bool(parsed.ambient_events),
                              v03_clock_interval=parsed.v03_clock_interval)
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
