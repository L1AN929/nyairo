#!/usr/bin/env python3
"""显式压缩逻辑日（COMPRESSED_DEMO）的唯一入口。

    python scripts/fast_day.py prepare --root /abs/new-root --run-id day-a
    python scripts/fast_day.py status  --root /abs/new-root
    python scripts/fast_day.py start   --root /abs/new-root [--clock-only]
    python scripts/fast_day.py pause   --root /abs/new-root
    python scripts/fast_day.py resume  --root /abs/new-root
    python scripts/fast_day.py stop    --root /abs/new-root

``prepare`` 默认建一个**现成的**实例（真 Supply/World owner、本实例开关、新 Memory、
operator 提供的公版节选）；``--clock-only`` 只准备时钟与实验额度，不建实例。
``start`` 在同一个有界窗口里起本根自己的 Supply/World 子进程与 resident（不绑
HTTP），World 读同一个业务时钟域；零模型冒烟用 ``--no-act --offline-scripted``。

这是什么
--------
用户批准的验收模式：**压缩逻辑日，不是真实 24 小时**。一个逻辑日（24 逻辑小时）
放进 3600–7200 真实秒（1–2h），rate = 86400/real_seconds，默认 12（2h）。

加速的只有业务时间：Life 的自然窗口/安静、agenda 与依赖到期、World/Body 恢复与
暴露、旅程进度、业务事件时序。**不加速**：provider/网络超时、重试退避、poll 与
stop/signal 超时、lease/锁/并发身份、能力 grant/权限过期、请求账本的真实 UTC 日
与 run 计数、成本账本、幂等/审计的真实 ``recorded_at``。系统时间、TZ、全局
``datetime`` 都不改。

边界
----
* 只接受显式指定的**全新根**；已有目录/已初始化实例、``/tmp``、旧实例根一律拒绝。
* 不 prepare/reset 旧实例；不 kill 旧进程；不读旧 root。
* 请求总上限仍是实例配置的 96；本实验额外硬上限 <=82（持久、跨重启、跨真实
  UTC 日都不刷新）。预留即计费，失败/取消不退款。
* ``--clock-only`` 只推进时钟并写双时间记录，**不是**一次逻辑一天成功，也不冒充
  她的自主活动；它证明的是时钟装配与控制。
* 正常生产路径不加载本模块；默认仍是 REAL_TIME。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import demo_business_clock as clockmod  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

MODE_FILENAME = "fastday.json"
RUN_FILENAME = "run.json"
TICK_FILENAME = "clock_ticks.jsonl"
LOG_DIRNAME = "logs"
RESIDENT_LOG = "resident.log"

MODE_COMPRESSED_DEMO = clockmod.MODE_COMPRESSED_DEMO
DEFAULT_POLL_SECONDS = 1.0
MAX_POLL_SECONDS = 30.0
#: 一次前台监督进程的真实上限就是用户批准的范围（<=7200 秒），不能按 24h 放大。
MAX_RUN_REAL_SECONDS = clockmod.MAX_RUN_REAL_SECONDS

#: 任务书明确不读不写不接管的旧实例根。
RESERVED_ROOTS = (
    "/root/nyairo-day-20261022",
    "/root/nyairo-usable-20261023",
)

REASON_ROOT_REQUIRED = "ROOT_REQUIRED_ABSOLUTE"
REASON_ROOT_FRESH_REQUIRED = "ROOT_NOT_FRESH"
REASON_ROOT_RESERVED = "ROOT_RESERVED_OLD_INSTANCE"
REASON_MODE_MISSING = "FASTDAY_MODE_MISSING"
REASON_MODE_CORRUPT = "FASTDAY_MODE_CORRUPT"
REASON_MODE_MISMATCH = "FASTDAY_MODE_MISMATCH"
REASON_RATE_INVALID = "CLOCK_RATE_INVALID"
REASON_STOPPED = "CLOCK_STOPPED"
REASON_NO_RUN = "FASTDAY_NOT_STARTED"
REASON_IDLE = "FASTDAY_IDLE"
REASON_STATE_ROOT_OUTSIDE = "STATE_ROOT_OUTSIDE_ROOT"
REASON_INHERITED_RUN_CONFLICT = "INHERITED_RUN_CONFLICT"
REASON_DURATION_OUT_OF_RANGE = "DURATION_OUT_OF_RANGE"
REASON_HORIZON_REACHED = "CLOCK_HORIZON_REACHED"
REASON_WALL_BUDGET_EXHAUSTED = clockmod.REASON_WALL_BUDGET_EXHAUSTED
REASON_RESIDENT_EXITED = "RESIDENT_EXITED"
REASON_INSTANCE_RUN_ID_MISMATCH = "INSTANCE_RUN_ID_MISMATCH"
REASON_REQUEST_LIMIT_INVALID = "REQUEST_LIMIT_INVALID"
REASON_WORLD_GATE_INVALID = "FASTDAY_WORLD_GATE_INVALID"

#: World 写闸门是**进程环境**开关（Intent Executor 直接读它）。显式 demo 只负责
#: 把监督进程里已经存在的值原样、规范化后交给 resident 子进程；默认仍是 off。
WORLD_GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
WORLD_GATE_DEFAULT = "off"
WORLD_GATE_VALUES = ("off", "shadow", "canonical")


def resolve_world_intent_gate(environment: Mapping[str, str]) -> dict[str, Any]:
    """解析并校验本实验要交给 resident 的 World 写闸门值。

    没有设置/空值 = 默认 off（不提高权限）；显式 off/shadow/canonical 原样生效；
    其它值在起任何子进程之前具名拒绝。只读进程环境，不改 ``os.environ``，因此不会
    把某个 run 的选择永久污染到同进程里的其它调用。
    """

    raw = str(environment.get(WORLD_GATE_ENV) or "").strip()
    if not raw:
        return {"ok": True, "value": WORLD_GATE_DEFAULT, "source": "default_off"}
    value = raw.lower()
    if value not in WORLD_GATE_VALUES:
        return {"ok": False, "reason_code": REASON_WORLD_GATE_INVALID,
                "value": raw, "source": "environment",
                "detail": "%s 只能是 %s，收到 %r"
                          % (WORLD_GATE_ENV, "/".join(WORLD_GATE_VALUES), raw)}
    return {"ok": True, "value": value, "source": "explicit"}


def validate_request_limits(total_limit: Any, historical_requests: Any,
                            max_new_requests: Any,
                            max_cognition_calls_per_day: Any = None) -> Optional[dict[str, Any]]:
    """显式额度绑定的统一校验；不合法时返回具名拒绝，且**不**写任何文件。

    默认 96/82 的旧路径仍走同一套规则；显式新总限（用户授权 300）只允许
    ``历史 + 新增 <= 总限``，并且总限本身不能超过已授权的 300。
    """

    for name, value in (("request_total_limit", total_limit),
                        ("historical_requests", historical_requests),
                        ("max_new_requests", max_new_requests)):
        if isinstance(value, bool) or not isinstance(value, int):
            return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                    "detail": "%s 必须是整数，收到 %r" % (name, value)}
    total = int(total_limit)
    historical = int(historical_requests)
    max_new = int(max_new_requests)
    if total < 1 or total > clockmod.MAX_TOTAL_REQUESTS_LIMIT:
        return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                "detail": "request_total_limit 必须在 1..%d 之间，收到 %r"
                          % (clockmod.MAX_TOTAL_REQUESTS_LIMIT, total_limit)}
    if historical < 0 or historical > total:
        return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                "detail": "historical_requests 必须在 0..总限(%d) 之间，收到 %r"
                          % (total, historical_requests)}
    if historical + max_new > total:
        return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                "detail": "历史 %d + 新增 %d 超过共同总限 %d"
                          % (historical, max_new, total)}
    ceiling = clockmod.experiment_cap_ceiling(total, historical)
    if max_new < 1 or max_new > ceiling:
        return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                "detail": "max_new_requests 必须在 1..%d 之间，收到 %r%s"
                          % (ceiling, max_new_requests,
                             "（默认 96 总配置不提高）" if total <= clockmod.HARD_CONFIG_MAX
                             else "")}
    if max_cognition_calls_per_day is not None:
        cognition = max_cognition_calls_per_day
        if isinstance(cognition, bool) or not isinstance(cognition, int) \
                or cognition < 1 or cognition > clockmod.MAX_TOTAL_REQUESTS_LIMIT:
            return {"ok": False, "reason_code": REASON_REQUEST_LIMIT_INVALID,
                    "detail": "max_cognition_calls_per_day 必须在 1..%d 之间，收到 %r"
                              % (clockmod.MAX_TOTAL_REQUESTS_LIMIT, cognition)}
    return None


def run_identity(run_id: str) -> str:
    """CLI 的 ``--run-id`` 映射到实例自己的 run 身份：沿用 ``run:<name>`` 惯例。

    同一个值会贯穿 clock、请求账本、实验上限、resident 环境与跨进程恢复；不是一个
    只用来显示的字段。
    """

    text = str(run_id or "").strip()
    if not text:
        return ""
    return text if text.startswith("run:") else "run:" + text


def _emit(payload: Mapping[str, Any], *, code: int = EXIT_OK) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True)
                     + "\n")
    sys.stdout.flush()
    return int(code)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
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


def mode_path(root: Path | str) -> Path:
    return Path(root) / MODE_FILENAME


def run_path(state_root: Path | str) -> Path:
    return clockmod.clock_dir(state_root) / RUN_FILENAME


def tick_path(state_root: Path | str) -> Path:
    return clockmod.clock_dir(state_root) / TICK_FILENAME


def state_root_of(root: Path | str) -> Path:
    return Path(root) / "state"


def _reserved(path: Path) -> Optional[str]:
    text = str(path)
    for reserved in RESERVED_ROOTS:
        if text == reserved or text.startswith(reserved.rstrip("/") + "/"):
            return reserved
    # 只拒绝把 /tmp 本身当实例根；显式给 /tmp/<唯一子目录> 的隔离夹具是允许的。
    tmp = str(Path(os.path.realpath("/tmp"))) if os.name == "posix" else ""
    if tmp and text.rstrip("/") == tmp.rstrip("/"):
        return tmp
    return None


# -- prepare ----------------------------------------------------------------

def _inside_root(root: Path, candidate: Path) -> bool:
    """``candidate`` 的**真实路径**必须落在 ``root`` 内（符号链接逃逸也算外面）。"""

    try:
        base = Path(os.path.realpath(str(root)))
        target = Path(os.path.realpath(str(candidate)))
    except OSError:
        return False
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return True


def _provision_instance(root: Path, *, run_id: str,
                        request_total_limit: int = clockmod.HARD_CONFIG_MAX,
                        max_cognition_calls_per_day: Optional[int] = None
                        ) -> dict[str, Any]:
    """用一个全新根建**现成的**实例：真 Supply/World owner、自主开关、新 Memory、公版材料。

    全部复用既有入口（``demo.prepare_demo`` / ``autonomous_life_control.set_enabled`` /
    ``phone_life.enable_instance_memory``），不复制 prepare、不另造 owner、不写任何密钥。
    返回 ``{"ok": bool, "reason_code": ..., "state_root": ..., "ledger_run_id": ...}``；
    失败时 ``partial`` 如实列出已经写下的东西，重试不会覆盖数据。
    """

    report: dict[str, Any] = {"ok": False, "provisioned": False, "partial": []}
    expected_run_id = run_identity(run_id)
    try:
        from components.phone import demo, life as phone_life
        import autonomous_life_control as control
        from day_reading_material import MATERIAL_EXCERPT, MATERIAL_TITLE, MATERIAL_WORK
    except Exception as exc:  # imports 失败就是没准备好，不继续猜
        report.update({"reason_code": "PROVISION_IMPORT_FAILED",
                       "detail": "%s: %s" % (type(exc).__name__, exc)})
        return report

    try:
        # 走实例自己的准备入口把本实验的 run 身份写进 ``CHIYO_AUTONOMOUS_RUN_ID``：
        # 不是事后补一个显示字段。专用开关同时禁掉 demo 的占位作品，房间里只有真实
        # 房间/物件与 operator 公版阅读材料，没有合成种子被冒充成她的历史。
        prepared = demo.prepare_demo(
            root, with_world=True, reading_title=MATERIAL_TITLE,
            reading_content=MATERIAL_EXCERPT,
            run_id=expected_run_id,
            seed_work_artifact=False,
            # 显式额度只写进这个新实例：默认路径仍是 96/96/96。
            request_utc_day_max=int(request_total_limit),
            request_run_window_max=int(request_total_limit),
            max_cognition_calls_per_day=int(max_cognition_calls_per_day
                                            if max_cognition_calls_per_day is not None
                                            else request_total_limit))
    except Exception as exc:
        report.update({
            "reason_code": "PREPARE_FAILED",
            "detail": "%s: %s" % (type(exc).__name__, exc),
            "partial": sorted(entry.name for entry in root.iterdir()) if root.exists() else [],
            "recovery": ("中间状态没有被覆盖；先检查这个根的 logs/ 与 one-day/，"
                         "确认只属于本实验后再人工清理，不能靠重试覆盖"),
        })
        return report
    if not isinstance(prepared, Mapping) or prepared.get("ok") is not True:
        report.update({"reason_code": "PREPARE_REFUSED",
                       "detail": "demo.prepare_demo 没有报 ok",
                       "partial": ["demo_root_created"]})
        return report
    report["partial"].append("demo_instance")

    try:
        phone = phone_life.load_phone_instance(root)
        nested = phone_life.read_nested_life(root)
        install_root = phone_life.life_install_root(root, nested)
        state_root = phone_life.life_state_root(root, nested)
    except Exception as exc:
        report.update({"reason_code": "INSTANCE_BINDING_FAILED",
                       "detail": "%s: %s" % (type(exc).__name__, exc)})
        return report
    if not _inside_root(root, state_root):
        report.update({"reason_code": REASON_STATE_ROOT_OUTSIDE,
                       "detail": "实例记录的状态根在实验根之外：%s" % state_root})
        return report

    try:
        switch = control.set_enabled(install_root, True, actor="fast-day-prepare")
        report["autonomous_switch"] = switch
    except Exception as exc:
        report.update({"reason_code": "AUTONOMY_SWITCH_FAILED",
                       "detail": "%s: %s" % (type(exc).__name__, exc)})
        return report
    report["partial"].append("autonomous_switch")

    subject = str(prepared.get("subject") or phone.get("subject") or "").strip()
    try:
        memory = phone_life.enable_instance_memory(
            root, subject, "fast-day:%s:memory" % run_id)
    except Exception as exc:
        report.update({"reason_code": "MEMORY_BIND_FAILED",
                       "detail": "%s: %s" % (type(exc).__name__, exc)})
        return report
    report["memory"] = {key: memory.get(key) for key in
                        ("ok", "status", "created", "reused", "owner", "reason", "detail")}
    if memory.get("ok") is not True:
        report.update({"reason_code": "MEMORY_BIND_REFUSED",
                       "detail": str(memory.get("reason") or memory.get("status") or "")})
        return report
    report["partial"].append("memory")

    binding = phone_life.build_binding(phone, nested)
    env = binding["environment"]
    ledger_run_id = str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
    if not ledger_run_id:
        report.update({"reason_code": "INSTANCE_RUN_ID_MISSING",
                       "detail": "实例没有记录 run 身份；实验上限无处绑定"})
        return report
    if ledger_run_id != expected_run_id:
        report.update({"reason_code": REASON_INSTANCE_RUN_ID_MISMATCH,
                       "detail": "实例 run %r 与请求的 %r 不同；不替它改身份"
                                 % (ledger_run_id, expected_run_id)})
        return report
    inherited = str(os.environ.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
    if inherited and inherited != ledger_run_id:
        report.update({"reason_code": REASON_INHERITED_RUN_CONFLICT,
                       "detail": "父进程环境的 run %r 与实例 run %r 不同；不 setdefault 透传"
                                 % (inherited, ledger_run_id)})
        return report
    world_extra = prepared.get("world") or {}
    room_objects = list((prepared.get("prepared") or {}).get("room_objects") or [])
    report.update({
        "ok": True,
        "provisioned": True,
        "state_root": str(state_root),
        "ledger_run_id": ledger_run_id,
        "subject": subject,
        "world_home": prepared.get("world_home"),
        "world_id": prepared.get("world_id"),
        "supply_socket": prepared.get("supply_socket"),
        "request_limits": {
            "utc_day_max": env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"),
            "run_window_max": env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"),
            "cognition_calls_per_day": env.get(
                "CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY"),
            "configured_total_limit": int(request_total_limit),
        },
        "material": {"title": MATERIAL_TITLE, "work": MATERIAL_WORK,
                     "provided_by": "operator", "public_domain_excerpt": True,
                     "content_in_instance": True,
                     "characters": len(MATERIAL_EXCERPT)},
        "initialized": {
            "world": {
                "rooms": len(world_extra.get("scene_locations") or []),
                "takeable_objects": len(world_extra.get("takeable") or []),
                "rest_furniture": bool(world_extra.get("rest_furniture")),
                "substrate_placements": len((world_extra.get("substrate") or {})
                                            .get("placements") or []),
                "room_objects_before_world_placement": len(room_objects),
            },
            "work_artifact": "none_seed_disabled",
            "reading_artifact": bool(prepared.get("reading_artifact_id")),
            "private_items_document": bool(prepared.get("private_items_artifact_id")),
            "memory": str(memory.get("status") or ""),
        },
        "model_called": False,
        "request_sent": False,
    })
    return report


def prepare(root: Path, *, run_id: str, real_seconds: float,
            logical_seconds: float, max_new_requests: int,
            request_total_limit: int = clockmod.HARD_CONFIG_MAX,
            historical_requests: int = 0,
            max_cognition_calls_per_day: Optional[int] = None,
            state_root: Optional[Path] = None,
            provision_instance: bool = False) -> dict[str, Any]:
    if not root.is_absolute():
        return {"ok": False, "reason_code": REASON_ROOT_REQUIRED,
                "detail": "必须显式给出绝对路径的全新根"}
    reserved = _reserved(root)
    if reserved:
        return {"ok": False, "reason_code": REASON_ROOT_RESERVED,
                "detail": "拒绝接管旧实例或临时目录：%s" % reserved}
    if root.exists():
        entries = [entry.name for entry in root.iterdir()]
        if entries:
            return {"ok": False, "reason_code": REASON_ROOT_FRESH_REQUIRED,
                    "detail": "目录已非空（%s）；新实验必须是全新根" % ", ".join(entries[:8])}
    if not math.isfinite(float(real_seconds)) or float(real_seconds) <= 0:
        return {"ok": False, "reason_code": REASON_RATE_INVALID,
                "detail": "real_seconds 必须为正的有限值"}
    try:
        rate = clockmod.rate_for_real_seconds(real_seconds, logical_seconds)
    except clockmod.BusinessClockError as exc:
        return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
    run_id = str(run_id or "").strip()
    if not run_id:
        return {"ok": False, "reason_code": REASON_MODE_CORRUPT, "detail": "run_id 为空"}
    # 额度绑定在任何写入之前校验：越界/负数/历史+新增超总限都不创建根、不建实例，
    # 也不留下半套时钟或上限文件。
    invalid = validate_request_limits(request_total_limit, historical_requests,
                                      max_new_requests, max_cognition_calls_per_day)
    if invalid is not None:
        return invalid
    cognition_max = int(max_cognition_calls_per_day
                        if max_cognition_calls_per_day is not None
                        else request_total_limit)
    expected_run_id = run_identity(run_id)
    # 父进程环境里带着**另一个** run 身份时，实例会绑定到 expected_run_id，二者不可能
    # 同时成立。在写任何东西之前具名拒绝，不 setdefault 透传、不事后覆盖。
    inherited = str(os.environ.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
    if inherited and inherited != expected_run_id:
        return {"ok": False, "reason_code": REASON_INHERITED_RUN_CONFLICT,
                "detail": "父进程环境的 run %r 与本次请求的 %r 不同"
                          % (inherited, expected_run_id)}
    if mode_path(root).exists():
        return {"ok": False, "reason_code": REASON_ROOT_FRESH_REQUIRED,
                "detail": "该根已经 prepare 过"}
    explicit_state_root = Path(state_root) if state_root is not None else None
    if explicit_state_root is not None and not _inside_root(root, explicit_state_root):
        return {"ok": False, "reason_code": REASON_STATE_ROOT_OUTSIDE,
                "detail": "状态根必须在实验根之内：%s" % explicit_state_root}

    if provision_instance:
        provision = _provision_instance(
            root, run_id=run_id, request_total_limit=int(request_total_limit),
            max_cognition_calls_per_day=cognition_max)
        if provision.get("ok") is not True:
            return {"ok": False, **provision}
        state_root = Path(str(provision["state_root"]))
        ledger_run_id = str(provision["ledger_run_id"])
        if ledger_run_id != expected_run_id:
            return {"ok": False, "reason_code": REASON_INSTANCE_RUN_ID_MISMATCH,
                    "detail": "实例 run %r 与请求的 %r 不同"
                              % (ledger_run_id, expected_run_id),
                    "provision": provision}
    else:
        root.mkdir(parents=True, exist_ok=True)
        state_root = explicit_state_root if explicit_state_root is not None \
            else state_root_of(root)
        ledger_run_id = expected_run_id
        provision = {}

    domain = clockmod.domain_for(run_id)
    try:
        clockmod.create_clock(state_root, run_id=ledger_run_id, rate=rate,
                              logical_seconds=logical_seconds, domain_id=domain)
        cap = clockmod.ExperimentRequestCap(
            state_root, run_id=ledger_run_id, max_new_requests=int(max_new_requests),
            total_limit=int(request_total_limit),
            historical_requests=int(historical_requests))
        # 历史基线只证明「历史 + 新增 <= 总限」，不写成本 run 的真实调用。
        cap.create(baseline_run_window_calls=0)
        clockmod.WallBudget(state_root, run_id=ledger_run_id).create()
    except clockmod.BusinessClockError as exc:
        return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail,
                "provision": provision or None}
    payload = {
        "kind": "fastday_instance",
        "schema_version": 1,
        "mode": MODE_COMPRESSED_DEMO,
        "run_id": run_id,
        "ledger_run_id": ledger_run_id,
        "domain_id": domain,
        "rate": rate,
        "logical_seconds": float(logical_seconds),
        "real_seconds_for_logical_day": float(logical_seconds) / rate,
        "state_root": str(state_root),
        "provisioned": bool(provision),
        # 显式、持久的额度绑定：只属于这个新实例；默认路径仍是 96/0/82。
        "request_total_limit": int(request_total_limit),
        "historical_requests": int(historical_requests),
        "max_new_requests": int(max_new_requests),
        "max_cognition_calls_per_day": int(cognition_max),
        "created_utc": _iso(clockmod.real_now()),
        "note": ("压缩逻辑日，不是真实 24 小时；安全/额度时钟不加速；"
                 "额度按 %d 总限/%d 历史/%d 新增显式绑定本实例"
                 % (int(request_total_limit), int(historical_requests),
                    int(max_new_requests))),
    }
    _write_json(mode_path(root), payload)
    snap = _status_payload(root)
    return {"ok": True, **payload, "provision": provision or None,
            "clock": snap.get("clock"), "experiment": snap.get("experiment")}


# -- status -----------------------------------------------------------------

def _clock_view(state_root: Path) -> dict[str, Any]:
    client = clockmod.BusinessClockClient(clockmod.clock_path(state_root))
    try:
        return client.snapshot()
    except clockmod.BusinessClockError as exc:
        return {"state": "UNAVAILABLE", "reason_code": exc.reason_code,
                "detail": exc.detail}


def _ledger_view(state_root: Path, run_id: str) -> dict[str, Any]:
    try:
        from life_request_budget import LifeRequestBudget

        return LifeRequestBudget(state_root, run_id=run_id).view()
    except Exception as exc:  # 读不出来就 fail closed，不猜 0
        return {"state": "corrupt", "reason_code": type(exc).__name__,
                "run_window_calls": None, "run_id": run_id}


def _experiment_view(state_root: Path, run_id: str,
                     expected: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    stored = _read_json(clockmod.experiment_path(state_root))
    limit = stored.get("max_new_requests")
    if isinstance(limit, bool) or not isinstance(limit, int):
        return {"state": "UNAVAILABLE",
                "reason_code": clockmod.REASON_EXPERIMENT_CAP_UNREADABLE,
                "detail": "实验上限文件缺失或不可读"}
    total = stored.get("authorized_total_limit", clockmod.HARD_CONFIG_MAX)
    historical = stored.get("historical_requests", 0)
    # 实例自己的 fastday.json 记录了授权绑定：上限文件被单独抬高时这里具名不一致，
    # 不把改过的元数据当成新额度。旧根没有这些字段时按文件（96/0）解释。
    if expected:
        for field, actual in (("request_total_limit", total),
                              ("historical_requests", historical),
                              ("max_new_requests", limit)):
            wanted = expected.get(field)
            if wanted is not None and wanted != actual:
                return {"state": "UNAVAILABLE",
                        "reason_code": clockmod.REASON_EXPERIMENT_CAP_UNREADABLE,
                        "detail": "%s 与实例配置不一致：文件 %r，配置 %r"
                                  % (field, actual, wanted)}
    try:
        cap = clockmod.ExperimentRequestCap(state_root, run_id=run_id,
                                            max_new_requests=limit,
                                            total_limit=total,
                                            historical_requests=historical)
        cap.load()
    except clockmod.BusinessClockError as exc:
        return {"state": "UNAVAILABLE", "reason_code": exc.reason_code,
                "detail": exc.detail}
    # 总是把账本视图交给闸门（缺失时它自己报 absent/0）：这样 status/start 也能看到
    # 「账本比预留数少」的回滚状态，而不是在账本被删后仍显示还有额度。
    ledger_view = _ledger_view(state_root, run_id)
    try:
        return cap.view(ledger_view)
    except clockmod.BusinessClockError as exc:
        return {"state": "UNAVAILABLE", "reason_code": exc.reason_code,
                "detail": exc.detail}


def _wall_view(state_root: Path, run_id: str) -> dict[str, Any]:
    try:
        return clockmod.WallBudget(state_root, run_id=run_id).view()
    except clockmod.BusinessClockError as exc:
        return {"state": "UNAVAILABLE", "reason_code": exc.reason_code,
                "detail": exc.detail, "allowed": False}


def real_model_state(environment: Mapping[str, str]) -> dict[str, Any]:
    """真实模式可用的模型配置（只回诊断，不回收据、密钥与正文）。

    复用 assembly 的同一份 profile 解析（``NYA02_MODEL_*`` 或实例自己的 profile 文件），
    ``None`` 就具名拒绝：真实模式绝不静默装配 ``DeterministicContinuationWriter``。
    """

    try:
        import autonomous_life_assembly as assembly
        config = assembly._model_config(environment)
    except Exception as exc:  # profile 模块缺失/解析抛错都算不可用
        return {"usable": False, "reason": "MODEL_CONFIG_UNRESOLVED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    if not config:
        return {"usable": False, "reason": "NO_REAL_MODEL_CONFIG",
                "detail": "profile 没有可用 provider；真实模式不回落确定性占位 writer"}
    return {
        "usable": True,
        "provider": config.get("provider"),
        "model": config.get("model"),
        "base_url_present": bool(config.get("base_url")),
        "api_key_present": bool(config.get("api_key")),
    }


def _resident_view(state_root: Path) -> dict[str, Any]:
    data = _read_json(state_root / "resident_summary.json")
    if not data:
        return {"present": False}
    counters = data.get("counters") or {}
    return {
        "present": True,
        "running": data.get("running"),
        "pid": data.get("pid"),
        "ticks": data.get("ticks"),
        "started_at": data.get("started_at"),
        "stopped_at": data.get("stopped_at"),
        "last_tick_at": data.get("last_tick_at"),
        "time_domain": data.get("time_domain"),
        "assembly": data.get("runtime_assembly"),
        "memory": data.get("memory_binding"),
        "offline_scripted": data.get("offline_scripted"),
        "world": data.get("world"),
        "budget": data.get("budget"),
        "work_selection": data.get("work_selection"),
        "journey": data.get("journey"),
        "wakes": counters.get("wakes"),
        "by_class": counters.get("by_class"),
        "actions": counters.get("actions"),
        "owner_version_refs": counters.get("owner_version_refs"),
        "model_calls": counters.get("model_calls"),
        "last_error": data.get("last_error"),
    }


def _status_payload(root: Path) -> dict[str, Any]:
    mode = _read_json(mode_path(root))
    if not mode:
        return {"ok": False, "reason_code": REASON_MODE_MISSING,
                "detail": "该根没有 fastday.json；不是本模式的实例"}
    if mode.get("mode") != MODE_COMPRESSED_DEMO:
        return {"ok": False, "reason_code": REASON_MODE_MISMATCH,
                "detail": "mode=%r" % (mode.get("mode"),)}
    run_id = str(mode.get("run_id") or "")
    ledger_run_id = str(mode.get("ledger_run_id") or run_id)
    state_root = Path(mode.get("state_root") or state_root_of(root))
    clock = _clock_view(state_root)
    experiment = _experiment_view(state_root, ledger_run_id, expected=mode)
    wall_budget = _wall_view(state_root, ledger_run_id)
    run = _read_json(run_path(state_root))
    control = clockmod.read_control(state_root)
    logical_at = clock.get("logical_at")
    real_at = clock.get("real_at") or _iso(clockmod.real_now())
    logical_elapsed = clock.get("logical_elapsed_seconds")
    resident = _resident_view(state_root)
    child_report = dict(run.get("child") or {})
    child_alive = bool(child_report.get("pid")) and _pid_alive(child_report.get("pid"))
    supervisor_alive = bool(run.get("pid")) and _pid_alive(run.get("pid"))
    return {
        "ok": True,
        "mode": MODE_COMPRESSED_DEMO,
        "not_real_24h": True,
        "root": str(root),
        "state_root": str(state_root),
        "run_id": run_id,
        "ledger_run_id": ledger_run_id,
        "domain_id": mode.get("domain_id"),
        "provisioned": bool(mode.get("provisioned")),
        # 三种执行模式必须一眼可分：真实模型 / 零模型占位 / 只推时钟。
        # 未启动时是 not_started，不拿未运行的 resident 冒充任何一种。
        "execution_mode": (str(run.get("execution_mode")) if run.get("execution_mode") else
                           "clock_only" if run.get("clock_only") else
                           "offline_scripted" if run.get("offline_scripted") else
                           "real" if run else "not_started"),
        "model": (run.get("model") or {}) if run else {},
        "world_intent_gate": run.get("world_intent_gate"),
        "rate": clock.get("rate", mode.get("rate")),
        "clock": clock,
        "logical": {"at": logical_at, "elapsed_seconds": logical_elapsed,
                    "horizon_logical": clock.get("horizon_logical"),
                    "horizon_remaining_seconds": clock.get("horizon_remaining_seconds"),
                    "state": clock.get("state")},
        "real": {"at": real_at, "now_utc": _iso(clockmod.real_now())},
        "control": control,
        "experiment": experiment,
        # 显式额度绑定（默认路径是 96/0/82；旧根没有这些字段时为 None）。
        "request_limits": {
            "total_limit": mode.get("request_total_limit"),
            "historical_requests": mode.get("historical_requests", 0),
            "max_new_requests": mode.get("max_new_requests"),
            "cognition_calls_per_day": mode.get("max_cognition_calls_per_day"),
        },
        "wall_budget": wall_budget,
        "process": {
            "supervisor_pid": run.get("pid"),
            "supervisor_alive": supervisor_alive,
            "child_pid": child_report.get("pid"),
            "child_alive": child_alive,
            "children": list(child_report.get("children") or []),
            "started_utc": run.get("started_utc"),
            "stopped_utc": run.get("stopped_utc"),
            "stop_reason": run.get("stop_reason"),
            "clock_only": run.get("clock_only"),
            "resident_exit_code": child_report.get("returncode"),
        },
        "resident": resident,
        # 装配/时钟健康是一个**可读的结论**，不是启动成功：子进程死了、时钟域不符、
        # 装配被拒都必须是 ok=false，不能把 RESIDENT_EXITED 报成完成。
        "health": {
            # 进程正常收尾（0/None）是健康；只有非 0 退出才叫失败，不能被
            # 「resident 已结束」这种正常状态冒认成故障或完成。
            "ok": child_report.get("returncode") in (None, 0, -15, -9),
            "supervisor_running": supervisor_alive,
            "resident_running": child_alive,
            "runtime_present": bool((resident.get("assembly") or {}).get("runtime_present")),
            "assembly_state": (resident.get("assembly") or {}).get("state"),
            "assembly_reason": (resident.get("assembly") or {}).get("reason"),
            "memory_bound": bool((resident.get("memory") or {}).get("database_configured")),
            "clock_state": clock.get("state"),
            "domain_matches_run": (clock.get("run_id") in (None, ledger_run_id)),
            "resident_exited": bool(run.get("pid")) and not supervisor_alive
            and child_report.get("returncode") not in (None, 0),
        },
        "note": "压缩逻辑日，不是真实 24 小时；真实超时/权限/请求 UTC 日不加速",
    }


# -- the supervisor ---------------------------------------------------------

def _append_tick(root: Path, record: Mapping[str, Any]) -> None:
    path = tick_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")


class FastDaySupervisor:
    """持有时钟权威、拉起本根自己的 Supply/World 与 resident，并在有界窗口内监督。

    子进程归属：只启动并停止``self.owners``里的两个 owner 与``self.child``里的
    resident；``fast_day stop``也只按本根``run.json``记录过的 pid 收尾，从不按名字
    扫进程、不碰旧实例。
    """

    def __init__(self, root: Path | str, *, poll_seconds: float = DEFAULT_POLL_SECONDS,
                 clock_only: bool = False, no_act: bool = False,
                 offline_scripted: bool = False,
                 sleeper: Optional[Callable[[float], None]] = None,
                 monotonic: Optional[Callable[[], float]] = None,
                 wall: Optional[Callable[[], datetime]] = None) -> None:
        self.root = Path(root)
        self.poll_seconds = min(MAX_POLL_SECONDS, max(0.05, float(poll_seconds)))
        self.clock_only = bool(clock_only)
        self.no_act = bool(no_act)
        self.offline_scripted = bool(offline_scripted)
        if self.offline_scripted and not self.no_act:
            raise ValueError("offline_scripted 只能和 no_act 一起用")
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        #: 真实墙钟预算用它计时；安全面仍读系统时间，测试可注入脚本替身。
        self._wall = wall or clockmod.real_now
        self._wall_ledger: Optional[clockmod.WallBudget] = None
        self._wall_report: dict[str, Any] = {}
        self._stop = False
        self.child: Optional[subprocess.Popen] = None
        self.authority: Optional[clockmod.BusinessClockAuthority] = None
        self.owners: dict[str, Any] = {}
        self.generation: Optional[str] = None
        self.child_stop_timeout = 20.0
        self.owner_stop_timeout = 30.0

    # -- signal / control -------------------------------------------------

    def request_stop(self, reason: str = "operator") -> None:
        self._stop = True
        self._stop_reason = reason

    _stop_reason: str = ""

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            self.request_stop("signal:%s" % signum)

        for name in ("SIGTERM", "SIGINT"):
            number = getattr(signal, name, None)
            if number is not None:
                try:
                    signal.signal(number, handler)
                except (ValueError, OSError):
                    pass

    # -- owners -----------------------------------------------------------

    def _start_owners(self, mode: Mapping[str, Any], state_root: Path) -> dict[str, Any]:
        """本根自己的 Supply/World 子进程；不带 HTTP，只开本地 owner socket。"""

        from components.phone import demo, service as phone_service

        generation = "fastday-%d-%d" % (int(time.time()), os.getpid())
        children = demo.owner_children(
            self.root, generation=generation,
            writer_instance="fast-day:%d" % os.getpid(),
            business_clock=clockmod.clock_path(state_root),
            clock_domain=str(mode.get("domain_id") or ""),
            clock_run_id=str(mode.get("ledger_run_id") or mode.get("run_id") or ""))
        supply, world = children["supply"], children["world"]
        started: list[str] = []
        try:
            for name, child, probe in (("supply", supply, "supply"),
                                       ("world", world, "gateway")):
                child.start()
                started.append(name)
                socket_path = child.socket_path
                if socket_path is None:
                    raise RuntimeError("CHILD_SOCKET_UNKNOWN:%s" % name)
                if not child.wait_ready(socket_path, timeout=demo.CHILD_READY_TIMEOUT,
                                        probe=probe):
                    raise RuntimeError("CHILD_NOT_READY:%s" % name)
        except Exception:
            for name in reversed(started):
                try:
                    children[name].stop()
                except Exception:
                    pass
            raise
        # 与 serve 一样把本次 live socket 写进实例文件：resident 的绑定从那里读，
        # 旧 generation 的 socket 文件原样保留。
        instance = dict(phone_service.load_instance(self.root))
        instance["supply_socket"] = str(supply.socket_path)
        instance["supply_generation"] = generation
        phone_service.write_instance(self.root, instance)
        self.owners = children
        self.generation = generation
        return {"generation": generation,
                "children": list(children),
                "supply": {"pid": supply.pid, "socket": str(supply.socket_path),
                           "log": str(supply.log_path)},
                "world": {"pid": world.pid, "socket": str(world.socket_path),
                          "log": str(world.log_path)}}

    def _stop_owners(self) -> dict[str, Any]:
        report: dict[str, Any] = {}
        for name, child in list(self.owners.items()):
            try:
                report[name] = child.stop(timeout=self.owner_stop_timeout)
            except Exception as exc:  # 收尾失败要如实报告，不吞
                report[name] = {"error": type(exc).__name__}
        self.owners = {}
        return report

    # -- resident ---------------------------------------------------------

    def _resident_env(self, mode: Mapping[str, Any], state_root: Path) -> dict[str, str]:
        """实例自己的绑定环境 + 本实验的时钟域；run 不符就拒绝，不 setdefault 透传。"""

        from components.phone import life as phone_life

        phone = phone_life.load_phone_instance(self.root)
        nested = phone_life.read_nested_life(self.root)
        if nested is None:
            raise RuntimeError("NESTED_LIFE_INSTANCE_MISSING")
        binding = phone_life.build_binding(phone, nested, base_env=dict(os.environ))
        env = dict(binding["environment"])
        run_id = str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
        expected = str(mode.get("ledger_run_id") or "").strip()
        if not run_id or (expected and run_id != expected):
            raise RuntimeError("RUN_ID_MISMATCH: instance=%r fastday=%r"
                               % (run_id, expected))
        inherited = str(os.environ.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
        if inherited and inherited != run_id:
            raise RuntimeError("INHERITED_RUN_CONFLICT: env=%r instance=%r"
                               % (inherited, run_id))
        env["NYA02_FASTDAY_CLOCK"] = str(clockmod.clock_path(state_root))
        env["NYA02_FASTDAY_DOMAIN"] = str(mode.get("domain_id") or "")
        env["NYA02_FASTDAY_RUN_ID"] = run_id
        # 显式贯通：resident 是检查 gate 的进程（Intent Executor 读进程环境），
        # 必须收到与 supervisor 记录一致的规范化值。默认 off 不会被这里改掉；
        # 只写子进程自己的 env，不改本进程 os.environ。
        gate = resolve_world_intent_gate(os.environ)
        if not gate.get("ok"):
            raise RuntimeError("%s: %s" % (gate["reason_code"], gate.get("detail") or ""))
        env[WORLD_GATE_ENV] = str(gate["value"])
        return env

    def _spawn_resident(self, state_root: Path, mode: Mapping[str, Any],
                        *, duration_seconds: float) -> Optional[dict[str, Any]]:
        resident = REPO_ROOT / "components" / "life" / "scripts" / "autonomous_life_resident.py"
        if not resident.exists():
            return {"ok": False, "reason_code": "RESIDENT_MISSING", "detail": str(resident)}
        try:
            env = self._resident_env(mode, state_root)
        except Exception as exc:
            message = str(exc)
            reason = message.split(":", 1)[0] or "RESIDENT_ENV_REFUSED"
            return {"ok": False, "reason_code": reason, "detail": message}
        log_dir = self.root / LOG_DIRNAME
        log_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", str(resident),
                   "--state-root", str(state_root),
                   "--poll-seconds", str(max(1, int(round(self.poll_seconds)))),
                   "--duration-seconds", str(max(1, int(round(duration_seconds))))]
        if self.no_act:
            command.append("--no-act")
        if self.offline_scripted:
            command.append("--offline-scripted")
        handle = (log_dir / RESIDENT_LOG).open("a", encoding="utf-8")
        try:
            child = subprocess.Popen(command, env=env, stdout=handle,
                                     stderr=subprocess.STDOUT, cwd=str(REPO_ROOT))
        except OSError as exc:
            handle.close()
            return {"ok": False, "reason_code": "RESIDENT_SPAWN_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            handle.close()
        self.child = child
        return None

    # -- start -------------------------------------------------------------

    def start(self, *, duration_seconds: Optional[float] = None) -> dict[str, Any]:
        mode = _read_json(mode_path(self.root))
        if not mode or mode.get("mode") != MODE_COMPRESSED_DEMO:
            return {"ok": False, "reason_code": REASON_MODE_MISSING,
                    "detail": "先用 fast_day prepare 建一个新根"}
        try:
            state_root = Path(str(mode.get("state_root") or state_root_of(self.root)))
            ledger_run_id = str(mode.get("ledger_run_id") or mode.get("run_id") or "")
            if not _inside_root(self.root, state_root):
                return {"ok": False, "reason_code": REASON_STATE_ROOT_OUTSIDE,
                        "detail": str(state_root)}
            gate = resolve_world_intent_gate(os.environ)
            if not gate.get("ok"):
                return {"ok": False, "reason_code": gate["reason_code"],
                        "detail": gate["detail"], "world_gate": gate}
            experiment = _experiment_view(state_root, ledger_run_id, expected=mode)
            if not experiment.get("allowed"):
                return {"ok": False,
                        "reason_code": clockmod.REASON_EXPERIMENT_CAP_EXHAUSTED,
                        "detail": "实验请求额度不足或账本不可读", "experiment": experiment}
            clock = _clock_view(state_root)
            if clock.get("state") == clockmod.STATE_STOPPED:
                return {"ok": False, "reason_code": REASON_STOPPED,
                        "detail": "该 run 已停止；不重新开跑"}
            rate = float(clock.get("rate") or mode.get("rate") or 0)
            remaining_logical = clock.get("horizon_remaining_seconds")
            real_remaining = (float(remaining_logical) / rate
                              if isinstance(remaining_logical, int) and rate > 0 else 0.0)
            requested = (clockmod.MAX_RUN_REAL_SECONDS if duration_seconds is None
                         else float(duration_seconds))
            if not math.isfinite(requested) or requested <= 0 \
                    or requested > clockmod.MAX_RUN_REAL_SECONDS:
                return {"ok": False, "reason_code": REASON_DURATION_OUT_OF_RANGE,
                        "detail": "duration_seconds 必须在 1..%g 真实秒之间"
                                  % clockmod.MAX_RUN_REAL_SECONDS}
            # 真实墙钟预算跨进程累计：上一次崩溃留下未收尾的段先按墙钟保守扣掉。
            # pause 冻结逻辑时钟但墙钟照走，重启/恢复都不能把这部分借回来。
            wall = clockmod.WallBudget(state_root, run_id=ledger_run_id, now=self._wall)
            try:
                wall_state = wall.recover()
            except clockmod.BusinessClockError as exc:
                return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
            wall_remaining = float(wall_state.get("remaining_seconds") or 0.0)
            if wall_remaining <= 0:
                return {"ok": False, "reason_code": REASON_WALL_BUDGET_EXHAUSTED,
                        "detail": "本 run 的真实墙钟预算已用完；不重开、不续额度",
                        "wall_budget": wall_state}
            # 逻辑余量与真实墙钟余量同时截断：重启只恢复逻辑余量，墙钟不退款。
            limit_real = min(requested, max(0.0, real_remaining), wall_remaining)
            if limit_real <= 0 and not self.clock_only:
                return {"ok": False, "reason_code": REASON_HORIZON_REACHED,
                        "detail": "这个 run 的逻辑 horizon 已经用完；不重开、不续额度"}

            # 真实模式必须在起任何子进程之前确认模型配置可用：缺配置就具名非 0 退出，
            # 不装配确定性占位 writer、不烧掉一段真实墙钟后才发现整天没有认知。
            if not self.clock_only and not self.offline_scripted:
                try:
                    preflight_env = self._resident_env(mode, state_root)
                except Exception as exc:
                    message = str(exc)
                    return {"ok": False,
                            "reason_code": message.split(":", 1)[0] or "RESIDENT_ENV_REFUSED",
                            "detail": message}
                model = real_model_state(preflight_env)
                if not model.get("usable"):
                    return {"ok": False, "reason_code": "NO_REAL_MODEL_CONFIG",
                            "detail": "真实模式没有可用的模型配置；"
                                      "不回落确定性占位 writer，也不开始这一段",
                            "model": model}
            elif self.offline_scripted:
                model = {"usable": False, "reason": "OFFLINE_SCRIPTED_PLACEHOLDER"}
            else:
                model = {"usable": False, "reason": "CLOCK_ONLY"}

            # 上一次 `stop` 留下的停止位只属于已结束的进程；run 本身没到 STOPPED 时，
            # 新的 start 明确开始一个新的有界窗口，不能让旧位立刻把新进程收掉。
            if clockmod.read_control(state_root).get("stop_requested"):
                clockmod.write_control(state_root, stop_requested=False, reason="start")
            try:
                wall.open()
            except clockmod.BusinessClockError as exc:
                return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
            self._wall_ledger = wall
            authority = clockmod.BusinessClockAuthority(
                state_root, run_id=ledger_run_id,
                domain_id=str(mode.get("domain_id") or ""),
                rate=float(mode.get("rate") or 0),
                logical_seconds=float(mode.get("logical_seconds") or
                                      clockmod.DEFAULT_LOGICAL_SECONDS),
                monotonic=self._monotonic)
            try:
                acquired = authority.acquire()
            except clockmod.BusinessClockError as exc:
                return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
            self.authority = authority
            self._stop_reason = ""
            self._install_signals()
            started_real = clockmod.real_now()
            record: dict[str, Any] = {
                "pid": os.getpid(),
                "started_utc": _iso(started_real),
                "domain_id": authority.domain_id,
                "run_id": ledger_run_id,
                "clock_only": self.clock_only,
                "offline_scripted": self.offline_scripted,
                # status 据此明确区分 clock-only / 零模型占位 / 真实模型，
                # 不把一次占位装配读成她真的活动过。
                "execution_mode": ("clock_only" if self.clock_only else
                                   "offline_scripted" if self.offline_scripted else "real"),
                "model": model,
                # World 写闸门是进程环境开关：这里只记录**解析后**的值与来源，
                # 不在本模式自行打开或关闭；resident 收到同一个显式值。
                "world_intent_gate": gate["value"],
                "world_intent_gate_source": gate["source"],
                "logical_start": _iso(authority.now()),
                "real_start": _iso(started_real),
                "acquired": acquired,
                "child": {},
                "owners": {},
                "duration_limit_seconds": limit_real,
                "wall_budget_start": wall_state,
            }
            _write_json(run_path(state_root), record)

            owners: dict[str, Any] = {}
            spawn_failure: Optional[dict[str, Any]] = None
            if not self.clock_only and limit_real > 0:
                try:
                    owners = self._start_owners(mode, state_root)
                except Exception as exc:
                    self._stop_owners()
                    authority.stop()
                    return {"ok": False, "reason_code": "OWNER_START_FAILED",
                            "detail": "%s: %s" % (type(exc).__name__, exc),
                            "log": str(self.root / LOGDIRNAME)}
                record["owners"] = owners
                _write_json(run_path(state_root), record)
                spawn_failure = self._spawn_resident(state_root, mode,
                                                     duration_seconds=limit_real)
                if spawn_failure is not None:
                    self._stop_owners()
                    authority.stop()
                    return spawn_failure

            summary: dict[str, Any] = {}
            closed: dict[str, Any] = dict(record)
            try:
                summary = self._loop(duration_seconds=limit_real)
            finally:
                child_report = self._terminate_child()
                owner_report = self._stop_owners()
                reason = self._stop_reason or "COMPLETED"
                # A run whose logical horizon is used up is over: it must land on the clock's
                # own existing ``STOPPED`` state instead of leaving a stale ``RUNNING`` label
                # behind a dead authority.  A short window that stopped before its horizon
                # keeps the old ``checkpoint``/RUNNING behaviour, so it can still be resumed.
                horizon_reached = bool(summary.get("horizon_reached"))
                if not horizon_reached:
                    try:
                        horizon_reached = bool(authority.exhausted)
                    except Exception:  # a clock read failure must not rewrite the label
                        horizon_reached = False
                if reason == "operator" or reason.startswith("signal:"):
                    stopped = authority.stop()
                    final_reason = "STOPPED_BY_OPERATOR"
                elif horizon_reached:
                    stopped = authority.stop()
                    final_reason = reason
                else:
                    authority.checkpoint()
                    stopped = authority.snapshot()
                    final_reason = reason
                closed.update({
                    "stopped_utc": _iso(clockmod.real_now()),
                    "stop_reason": final_reason,
                    "child": child_report,
                    "owners_stopped": owner_report,
                    "logical_end": stopped.get("logical_at"),
                    "real_end": _iso(clockmod.real_now()),
                })
                _write_json(run_path(state_root), closed)
            wall_closed = self._close_wall()
            resident_rc = child_report.get("returncode")
            resident_ok = resident_rc in (None, 0)
            result = {"ok": bool(resident_ok and not spawn_failure),
                      "supervisor": closed, "ran": summary,
                      "resident_exit_code": resident_rc,
                      "experiment": _experiment_view(state_root, ledger_run_id),
                      "wall_budget": wall_closed or _wall_view(state_root, ledger_run_id),
                      "resident": _resident_view(state_root)}
            if not resident_ok:
                result.update({"reason_code": REASON_RESIDENT_EXITED,
                               "detail": "resident 子进程以 %r 退出；不当成完成" % (resident_rc,)})
            return result
        except Exception as exc:  # 任何装配失败都必须具名返回，不半成功
            authority = self.authority
            if authority is not None and authority.acquired:
                try:
                    authority.stop()
                except Exception:
                    pass
            self._stop_owners()
            self._terminate_child()
            return {"ok": False, "reason_code": "SUPERVISOR_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            # 正常回报前已经 close；早退/异常路径在这里把仍开着的段收尾（不吞墙钟）。
            self._close_wall()

    def _close_wall(self) -> dict[str, Any]:
        ledger, self._wall_ledger = self._wall_ledger, None
        if ledger is None:
            return dict(self._wall_report)
        try:
            self._wall_report = ledger.close()
        except clockmod.BusinessClockError as exc:
            self._wall_report = {"state": "UNAVAILABLE", "allowed": False,
                                 "reason_code": exc.reason_code, "detail": exc.detail}
        return dict(self._wall_report)

    def _terminate_child(self) -> dict[str, Any]:
        child, self.child = self.child, None
        if child is None:
            return {}
        report: dict[str, Any] = {"pid": child.pid, "signalled": False}
        if child.poll() is None:
            try:
                if os.name == "posix":
                    child.send_signal(signal.SIGTERM)
                else:
                    child.terminate()
                report["signalled"] = True
            except OSError as exc:
                report["signal_error"] = type(exc).__name__
        deadline = self._monotonic() + self.child_stop_timeout
        while child.poll() is None and self._monotonic() < deadline:
            self._sleep(0.2)
        if child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass
            report["killed"] = True
        try:
            report["returncode"] = child.wait(timeout=5)
        except Exception:  # pragma: no cover - 只在极端平台路径
            report["returncode"] = None
        report["exited"] = child.poll() is not None
        return report

    # -- the bounded window ------------------------------------------------

    def _loop(self, *, duration_seconds: float) -> dict[str, Any]:
        authority = self.authority
        assert authority is not None
        state_root = Path(authority.state_root)
        began = self._monotonic()
        limit = min(clockmod.MAX_RUN_REAL_SECONDS, max(0.0, float(duration_seconds or 0)))
        ticks = 0
        horizon_reached = False
        child_exited = False
        while not self._stop:
            moment = authority.now()
            authority.checkpoint(ticks=ticks)
            snapshot = authority.snapshot()
            _append_tick(state_root, {
                "tick": ticks,
                "logical_at": _iso(moment),
                "real_at": _iso(clockmod.real_now()),
                "clock_state": snapshot.get("state"),
                "clock_only": self.clock_only,
                "note": ("时钟推进记录，不是她的自主活动" if self.clock_only
                         else "业务时钟检查点"),
            })
            ticks += 1
            if snapshot.get("state") == clockmod.STATE_STOPPED:
                self._stop_reason = "STOPPED_BY_OPERATOR"
                break
            if authority.exhausted:
                horizon_reached = True
                break
            if self.child is not None and self.child.poll() is not None:
                child_exited = True
                self._stop_reason = "RESIDENT_EXITED"
                break
            if (self._monotonic() - began) >= limit:
                self._stop_reason = "WALL_TIME_LIMIT"
                break
            remaining = limit - (self._monotonic() - began)
            self._sleep(min(self.poll_seconds, max(0.0, remaining)))
        return {"ticks": ticks, "horizon_reached": horizon_reached,
                "child_exited": child_exited,
                "wall_seconds": int(self._monotonic() - began),
                "stop_reason": self._stop_reason or "COMPLETED",
                "horizon_real_seconds": limit}

# -- commands ---------------------------------------------------------------

def cmd_prepare(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fast_day prepare")
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--real-seconds", type=float, default=7200.0,
                        help="一个逻辑日放进多少真实秒（推荐 3600–7200）")
    parser.add_argument("--rate", type=float, default=None,
                        help="显式比例；与 --real-seconds 二选一")
    parser.add_argument("--logical-seconds", type=float,
                        default=clockmod.DEFAULT_LOGICAL_SECONDS,
                        help="本实验的逻辑 horizon（默认 24 逻辑小时）")
    parser.add_argument("--max-new-requests", type=int, default=82)
    # 用户已授权的显式额度绑定：只在显式调用时生效，默认路径仍是 96/0/82。
    parser.add_argument("--request-total-limit", type=int,
                        default=clockmod.HARD_CONFIG_MAX,
                        help="本实例的共同总请求上限（默认 96；仅在 1..300 内显式绑定）")
    parser.add_argument("--historical-requests", type=int, default=0,
                        help="本实验之前已经真实花掉的历史请求数（只用于证明总限，不写成本 run 计数）")
    parser.add_argument("--max-cognition-calls-per-day", type=int, default=None,
                        help="认知每日配置（默认等于总限：默认 96，显式 300 时为 300）")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--clock-only", action="store_true",
                        help="只准备时钟/实验上限，不建实例（时钟冒烟用；不是可运行实例）")
    args = parser.parse_args(argv)
    if args.rate is not None:
        try:
            rate = clockmod.validate_rate(args.rate)
        except clockmod.BusinessClockError as exc:
            return _emit({"ok": False, "reason_code": exc.reason_code,
                          "detail": exc.detail}, code=EXIT_REFUSED)
        real_seconds = float(args.logical_seconds) / rate
    else:
        rate = None
        real_seconds = args.real_seconds
    result = prepare(Path(args.root), run_id=args.run_id, real_seconds=real_seconds,
                     logical_seconds=float(args.logical_seconds),
                     max_new_requests=int(args.max_new_requests),
                     request_total_limit=int(args.request_total_limit),
                     historical_requests=int(args.historical_requests),
                     max_cognition_calls_per_day=(None
                                                  if args.max_cognition_calls_per_day is None
                                                  else int(args.max_cognition_calls_per_day)),
                     state_root=(Path(args.state_root) if args.state_root else None),
                     provision_instance=not args.clock_only)
    if rate is not None and result.get("ok"):
        result["rate"] = rate
        result["real_seconds_for_logical_day"] = real_seconds
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fast_day status")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    payload = _status_payload(Path(args.root))
    return _emit(payload, code=EXIT_OK if payload.get("ok") else EXIT_REFUSED)


def cmd_start(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fast_day start")
    parser.add_argument("--root", required=True)
    parser.add_argument("--duration-seconds", type=float, default=None,
                        help="真实墙钟上限；默认按 horizon 的真实换算时长")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--clock-only", action="store_true",
                        help="只推进时钟，不拉起 resident（零模型冒烟）")
    parser.add_argument("--no-act", action="store_true")
    parser.add_argument("--offline-scripted", action="store_true",
                        dest="offline_scripted",
                        help="零模型冒烟：装配不发请求的既有确定性 writer 占位；必须与 --no-act 同用")
    args = parser.parse_args(argv)
    if args.offline_scripted and not args.no_act:
        return _emit({"ok": False, "reason_code": "OFFLINE_SCRIPTED_CANNOT_ACT",
                      "detail": "--offline-scripted 只能和 --no-act 一起用"}, code=EXIT_USAGE)
    supervisor = FastDaySupervisor(Path(args.root), poll_seconds=args.poll_seconds,
                                   clock_only=args.clock_only, no_act=args.no_act,
                                   offline_scripted=args.offline_scripted)
    result = supervisor.start(duration_seconds=args.duration_seconds)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def _state_root_for(root: Path) -> Path:
    mode = _read_json(mode_path(root))
    return Path(str(mode.get("state_root") or state_root_of(root)))


def _set_control(root: Path, *, pause: Optional[bool], reason: str) -> dict[str, Any]:
    return clockmod.write_control(_state_root_for(root), pause_requested=pause, reason=reason)


def _set_life_pause(root: Path, paused: bool, *, reason: str) -> dict[str, Any]:
    """沿用既有 admission：pause 之后不再发新请求（旧 Activity 的 PAUSE 语义不改）。"""

    mode = _read_json(mode_path(root))
    state_root = Path(mode.get("state_root") or state_root_of(root))
    try:
        import life_pause
    except Exception as exc:
        return {"ok": False, "reason_code": "LIFE_PAUSE_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    try:
        view = life_pause.set_paused(state_root, paused, reason=reason)
    except Exception as exc:
        return {"ok": False, "reason_code": "LIFE_PAUSE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "life_pause": view}


def cmd_pause(argv: list[str], *, paused: bool) -> int:
    parser = argparse.ArgumentParser(prog="fast_day %s" % ("pause" if paused else "resume"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--reason", default="operator")
    args = parser.parse_args(argv)
    root = Path(args.root)
    mode = _read_json(mode_path(root))
    if not mode:
        return _emit({"ok": False, "reason_code": REASON_MODE_MISSING}, code=EXIT_REFUSED)
    if not paused:
        # resume 前确认不是已结束的 run（不重新开跑旧 run）。
        clock = _clock_view(_state_root_for(root))
        if clock.get("state") == clockmod.STATE_STOPPED:
            return _emit({"ok": False, "reason_code": REASON_STOPPED,
                          "detail": "该 run 已停止；不重新开跑"}, code=EXIT_REFUSED)
    control = _set_control(root, pause=paused, reason=args.reason)
    life = _set_life_pause(root, paused, reason=args.reason)
    return _emit({"ok": bool(life.get("ok")), "paused": paused, "control": control,
                  "life_pause": life}, code=EXIT_OK if life.get("ok") else EXIT_REFUSED)


def cmd_stop(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fast_day stop")
    parser.add_argument("--root", required=True)
    parser.add_argument("--reason", default="operator")
    args = parser.parse_args(argv)
    root = Path(args.root)
    state_root = _state_root_for(root)
    record = _read_json(run_path(state_root))
    if not record:
        return _emit({"ok": False, "status": REASON_NO_RUN,
                      "detail": "本根没有本模式启动过的进程记录"}, code=EXIT_REFUSED)
    control = clockmod.write_control(state_root, stop_requested=True, reason=args.reason)
    # 只停本记录里的监督/resident/owner 进程；不去碰不是自己起的子进程。
    supervisor_pid = record.get("pid")
    child_pid = (record.get("child") or {}).get("pid")
    owner_pids = [((record.get("owners") or {}).get(name) or {}).get("pid")
                  for name in ("supply", "world")]
    pids = [pid for pid in [supervisor_pid, child_pid, *owner_pids] if _pid_alive(pid)]
    if not pids:
        return _emit({"ok": True, "status": "ALREADY_STOPPED", "control": control,
                      "supervisor_pid": supervisor_pid, "child_pid": child_pid,
                      "owner_pids": owner_pids})
    if os.name != "posix":
        return _emit({"ok": True, "status": "STOP_REQUESTED_COOPERATIVE",
                      "control": control, "supervisor_pid": supervisor_pid,
                      "child_pid": child_pid,
                      "detail": "非 POSIX 平台不发信号；监督进程按控制位收尾"},
                     code=EXIT_OK)
    signalled = []
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGTERM)
            signalled.append(pid)
        except OSError:
            pass
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline and any(_pid_alive(pid) for pid in pids):
        time.sleep(0.2)
    remaining = [pid for pid in pids if _pid_alive(pid)]
    return _emit({"ok": True, "status": "STOPPED" if not remaining else "STOP_REQUESTED",
                  "signalled": signalled, "remaining": remaining,
                  "supervisor_pid": supervisor_pid, "child_pid": child_pid,
                  "control": control})


def cmd_clock(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fast_day clock")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    clock = _clock_view(_state_root_for(Path(args.root)))
    return _emit({"ok": "reason_code" not in clock, "clock": clock,
                  "real_now": _iso(clockmod.real_now())},
                 code=EXIT_OK if "reason_code" not in clock else EXIT_REFUSED)


COMMANDS = {
    "prepare": cmd_prepare,
    "status": cmd_status,
    "start": cmd_start,
    "pause": lambda argv: cmd_pause(argv, paused=True),
    "resume": lambda argv: cmd_pause(argv, paused=False),
    "stop": cmd_stop,
    "clock": cmd_clock,
}


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_USAGE
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % arguments[0])
        return EXIT_USAGE
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
