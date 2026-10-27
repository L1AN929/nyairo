#!/usr/bin/env python3
"""唯一有界真实自主拍照：已有主体判断 -> 中文 scene -> 本机图 -> 相册。

这不是第二个主体、第二个相机或第二本相册。它只做三件已存在的事之间的连接：

* 用既有 ``phone.demo.prepare_demo`` 在一个**全新的隔离 root** 里建真实 Supply + 真实
  World + 手机实例（``prepare``：0 产品请求、0 张图）；
* 用既有的 Hermes 引擎（``run_agent.AIAgent`` + profile 里的 ``chiyo`` 插件）开一次
  工具会话，让**产品主体模型**自己决定要不要调用 ``chiyo_phone``；工具链仍是
  ``chiyo_phone`` -> ``PhoneService`` -> 既有相册/相机 owner；
* 每个产品请求先经过既有持久 ``LifeRequestBudget`` 预留，再发送；本 run 硬上限 3。

正式入口（写清楚，不拿临时人设冒主体）：
  Hermes ``run_agent.AIAgent``（CLI 用的同一个 agent loop）+ 现成 ``chiyo`` 插件注册的
  ``chiyo_phone`` 工具（toolset ``chiyo_world``）。profile 由现成
  ``scripts/setup_profile.py`` 生成，SOUL 用产品新实例的默认文本，不另写人设。

命令：
  prepare   建隔离 root、真实 owners、Hermes profile；0 产品 0 图
  status    只读：账本、实例、两个相机服务端口是否可达；不发模型
  run       唯一一次真实工具会话；必须显式 ``--live-model``；硬 cap 3；幂等防二次
  readback  只读：重开同一 root，读相册/详情/图片；不发模型、不生成

提示词只做中性告知：可用手机能力 + 隔离 World 自己报告的可见事实；不写
“请拍照/必须调用 camera/画某个东西”，也不由脚本补 scene。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "vendor" / "hermes")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

WORKER_RUNS = REPO_ROOT / ".agents" / "worker-runs"
STATE_PATH = WORKER_RUNS / "20261027-autonomous-camera-state.json"
RESULT_JSON = WORKER_RUNS / "20261027-autonomous-camera-run-result.json"
RESULT_MD = WORKER_RUNS / "20261027-autonomous-camera-result.md"
READBACK_JSON = WORKER_RUNS / "20261027-autonomous-camera-readback.json"
PHOTO_PATH = WORKER_RUNS / "20261027-autonomous-camera-photo.png"

DEFAULT_ROOT = Path("/root/nyairo-autonomous-camera-20261027")

SHARED_HISTORY_BEFORE = 94
SHARED_TOTAL = 300
RUN_REQUEST_CAP = 3

#: 已有 .claude/launch.json 里的两个具名配置；由父级用 preview_start 起。
PREVIEW_CONFIGS = ("camera-translator", "camera-sd")
CAMERA_TRANSLATOR_PORT = 18190
CAMERA_SD_PORT = 7860

ENTRY_DESCRIPTION = (
    "Hermes run_agent.AIAgent (the same agent loop the CLI uses) + the existing chiyo "
    "plugin's chiyo_phone tool, toolset chiyo_world"
)

SCHEMA_STATE = "autonomous_camera_once.state.v1"
SCHEMA_RUN = "autonomous_camera_once.run.v1"
SCHEMA_READBACK = "autonomous_camera_once.readback.v1"
SCHEMA_PREPARE = "autonomous_camera_once.prepare.v1"
SCHEMA_STATUS = "autonomous_camera_once.status.v1"

MODEL_ENV_READ = {
    "base_url": ("NYA02_MODEL_BASE_URL", "CHIYO_MODEL_BASE_URL"),
    "model": ("NYA02_MODEL_NAME", "CHIYO_MODEL"),
    "api_key": ("NYA02_MODEL_API_KEY", "CHIYO_MODEL_API_KEY"),
    "provider": ("NYA02_MODEL_PROVIDER",),
}

#: The one-image guard plugin, written into the isolated profile (never into components/).
CAMERA_GUARD_INIT = '''\
"""One-image guard for the bounded autonomous-camera session.

This plugin is not a persona and not a second authority.  It only enforces the operator's
stated budget for one bounded session: at most three camera tool invocations, and never a
second image once one has been produced.  It reads the production ``pre_tool_call`` /
``post_tool_call`` hooks; it does not change the phone tool, the model, or her choice.
"""
import json

_STATE = {"camera_calls": 0, "image_done": False}


def register(ctx):
    def pre(tool_name, args, **kwargs):
        if tool_name != "chiyo_phone" or not isinstance(args, dict):
            return None
        if str(args.get("action") or "") != "camera":
            return None
        if _STATE["image_done"]:
            return {"action": "block",
                    "message": "本次会话只生成一张图；已经有一张图，不能再发 camera。"}
        if _STATE["camera_calls"] >= 3:
            return {"action": "block",
                    "message": "本次会话的相机尝试已达上限，不能再发 camera。"}
        _STATE["camera_calls"] += 1
        return None

    def post(*, function_name=None, function_args=None, result=None, **kwargs):
        if function_name != "chiyo_phone" or not isinstance(function_args, dict):
            return None
        if str(function_args.get("action") or "") != "camera":
            return None
        try:
            doc = json.loads(result) if isinstance(result, str) else (result or {})
        except Exception:
            return None
        if not isinstance(doc, dict):
            return None
        inner = doc.get("result")
        data = inner.get("data") if isinstance(inner, dict) else None
        if isinstance(data, dict) and (data.get("generated") is True or data.get("reused") is True):
            _STATE["image_done"] = True
        return None

    ctx.register_hook("pre_tool_call", pre)
    ctx.register_hook("post_tool_call", post)
'''

CAMERA_GUARD_MANIFEST = """\
name: camguard
version: 0.1.0
description: Bounded one-image guard for the autonomous camera session
author: nyairo operator
"""


class Refused(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s%s" % (reason_code, (": " + detail) if detail else ""))
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _public_model_meta() -> dict[str, Any]:
    """Non-secret provider identity only; the key is never read into the report."""
    out: dict[str, Any] = {"configured": False, "api_key_present": False,
                           "api_key_recorded": False, "source": "process_environment"}
    for field, names in MODEL_ENV_READ.items():
        value = ""
        used = ""
        for name in names:
            candidate = str(os.environ.get(name) or "").strip()
            if candidate:
                value, used = candidate, name
                break
        if field == "api_key":
            out["api_key_present"] = bool(value)
            out["api_key_env"] = used or None
            continue
        if field == "base_url" and value:
            from urllib.parse import urlsplit
            parts = urlsplit(value)
            out["endpoint"] = ("%s://%s" % (parts.scheme, parts.netloc)
                               if parts.scheme and parts.netloc else None)
        out[field] = value or None
    out["configured"] = bool(out.get("base_url") and out.get("model") and out.get("api_key_present"))
    return out


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _model_env_for_agent() -> tuple[str, str, str, str]:
    base_url = _first_env(MODEL_ENV_READ["base_url"])
    model = _first_env(MODEL_ENV_READ["model"])
    api_key = _first_env(MODEL_ENV_READ["api_key"])
    provider = _first_env(MODEL_ENV_READ["provider"]) or "custom"
    if not (base_url and model and api_key):
        raise Refused("MODEL_ENV_MISSING",
                      "set NYA02_MODEL_BASE_URL / NYA02_MODEL_NAME / NYA02_MODEL_API_KEY in the process")
    return base_url, model, api_key, provider


#: Environment names that can silently point the services at an *old* root.  The task
#: forbids touching old day/fastday/product roots, so this harness drops any inherited
#: value; ``serve_instance`` sets the isolated instance's own values when it starts.
_STALE_ROOT_ENV = (
    "WORLD_BODY_SOCKET", "WORLD_BODY_INTEGRATION_MODE", "CHIYO_WORLD_HOME",
    "CHIYO_WORLD_PACKAGE", "CHIYO_WORLD_ID", "CHIYO_WORLD_RUNTIME",
    "CHIYO_WORLD_LOCAL_OWNER_PHONE", "CHIYO_WORLD_LIVING_SKELETON_ENABLED",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED", "CHIYO_WORLD_DAILY_RHYTHM_ENABLED",
    "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED", "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED",
    "LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_ENABLED",
    "NYA02_RESIDENT_STATE", "CHIYO_LIFE_INSTALL_ROOT", "CHIYO_AUTONOMOUS_RUN_ID",
    "CHIYO_LIFE_SUPPLY_SUBJECT", "NYA02_SUBJECT", "NYA02_ARTIFACT_GRANT",
    "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT", "CHIYO_LIFE_SUPPLY_WORKSPACE_ID",
)


def _clear_stale_root_env() -> None:
    for name in _STALE_ROOT_ENV:
        os.environ.pop(name, None)


def _run_interpreter() -> str:
    """The existing interpreter that already has the Hermes engine's dependencies.

    The repo source is still the current ``vendor/hermes``; this only selects an installed
    Python with ``openai``/pydantic available, so no dependency is downloaded or changed.
    """
    for candidate in ("/root/shigure-venv/bin/python", "/tmp/rc104-venv/bin/python"):
        if Path(candidate).is_file():
            return candidate
    return sys.executable


def _camera_services_status() -> dict[str, bool]:
    """Reachability through the *same* transport the camera will use.

    A plain WSL->Windows loopback TCP check is wrong on this box: the runtimes listen on
    Windows loopback, and the camera reaches them through the existing Windows bridge.  The
    probe therefore reuses the camera module's own request path (two read-only GETs; it never
    generates a picture) so ``run`` does not refuse a reachable runtime or waste a request.
    """
    status = {"translator_%d" % CAMERA_TRANSLATOR_PORT: False,
              "sd_%d" % CAMERA_SD_PORT: False}
    try:
        from components.phone.camera import LocalPictureLab, SceneTranslator

        lab = LocalPictureLab()
        lab._request("GET", "/sdapi/v1/progress", timeout=4)
        status["sd_%d" % CAMERA_SD_PORT] = True
    except Exception:
        pass
    try:
        from components.phone.camera import SceneTranslator

        translator = SceneTranslator()
        translator._request("GET", "/slots", timeout=4)
        status["translator_%d" % CAMERA_TRANSLATOR_PORT] = True
    except Exception:
        pass
    return status


def _load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    state = _read_json(path)
    if not isinstance(state, dict) or state.get("schema") != SCHEMA_STATE:
        raise Refused("STATE_MISSING", str(path))
    return state


def _require_posix() -> None:
    if not hasattr(os, "geteuid"):
        raise SystemExit("SKIP_NOT_POSIX: this harness uses the real Supply/World Unix-socket owners")


def _reserve_then_send(budget: Any, on_reserve: Any, send: Any) -> Any:
    """Ask the persistent ledger *before* the HTTP attempt; a refusal raises and nothing is sent.

    Both Hermes send boundaries (non-streaming and streaming) go through here, so the
    reservation and the send cannot drift apart.
    """
    record = budget.reserve("decision")
    if on_reserve is not None:
        on_reserve(record)
    return send()


def _previous_attempt(state: Mapping[str, Any], prior_result: Any) -> bool:
    """Whether a prior attempt already entered the agent call.

    The first real run died before its first reservation, so ``started`` stayed false while
    ``run_result`` was written.  An existing run result means the one attempt was already
    spent: refuse to enter it again.
    """
    return bool(state.get("started") or state.get("finished")
                or isinstance(prior_result, dict))


def _mark_attempt_started(state: Mapping[str, Any]) -> dict[str, Any]:
    """Persist "this run entered the agent call" *before* ``run_conversation``.

    ``_on_reserve`` alone is not enough: a run can fail before its first send and never
    touch the ledger, which is exactly how the first attempt left ``started`` false.
    """
    updated = dict(state)
    if not updated.get("started"):
        updated["started"] = True
        updated["started_at"] = _now()
    updated["attempt_entered_at"] = _now()
    _atomic_write_json(STATE_PATH, updated)
    return updated


# ---------------------------------------------------------------------------------------------
# paths / profile
# ---------------------------------------------------------------------------------------------

def _prepare_profile(hermes_home: Path, owner: str) -> None:
    setup = REPO_ROOT / "scripts" / "setup_profile.py"
    completed = subprocess.run(
        [sys.executable, "-B", str(setup), "--home", str(hermes_home),
         "--owner", owner, "--allow-local-owner"],
        capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise Refused("PROFILE_SETUP_FAILED",
                      (completed.stderr or completed.stdout or "").strip()[:400])
    guard = hermes_home / "plugins" / "camguard"
    guard.mkdir(parents=True, exist_ok=False)
    (guard / "__init__.py").write_text(CAMERA_GUARD_INIT, encoding="utf-8")
    (guard / "plugin.yaml").write_text(CAMERA_GUARD_MANIFEST, encoding="utf-8")
    # Add the guard to plugins.enabled without touching the existing chiyo entry.
    import yaml

    cfg_path = hermes_home / "config.yaml"
    value = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise Refused("PROFILE_CONFIG_INVALID")
    plugins = value.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if "chiyo" not in enabled:
        enabled.insert(0, "chiyo")
    if "camguard" not in enabled:
        enabled.append("camguard")
    plugins["enabled"] = enabled
    cfg_path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")


def _write_phone_section(hermes_home: Path, root: Path, base_url: str) -> None:
    """Point the existing phone tool at this isolated instance (profile-side only)."""
    path = hermes_home / "chiyo" / "config.json"
    config = _read_json(path)
    if not isinstance(config, dict):
        raise Refused("PROFILE_CONFIG_INVALID", str(path))
    config["phone"] = {
        "enabled": True,
        "base_url": base_url,
        "profile_home": str(root),
        "write_enabled": True,
        "private_artifacts": True,
    }
    _atomic_write_json(path, config)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _one_day_environment(root: Path) -> dict[str, Any]:
    path = root / "one-day" / "instance.json"
    doc = _read_json(path)
    if not isinstance(doc, dict):
        raise Refused("ONE_DAY_INSTANCE_MISSING", str(path))
    env = doc.get("environment") if isinstance(doc.get("environment"), Mapping) else {}
    return dict(env)


# ---------------------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------------------

def cmd_prepare(args: argparse.Namespace) -> int:
    _require_posix()
    _clear_stale_root_env()
    root = Path(str(args.root)).expanduser()
    if root.exists():
        raise Refused("ROOT_EXISTS", str(root))
    root.parent.mkdir(parents=True, exist_ok=True)

    from components.phone import demo as phone_demo

    prepared = phone_demo.prepare_demo(
        root,
        with_world=True,
        seed_work_artifact=False,
        reading_content="",
        request_utc_day_max=RUN_REQUEST_CAP,
        request_run_window_max=RUN_REQUEST_CAP,
    )
    env = _one_day_environment(root)
    run_id = str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or ("run:" + root.name))
    life_state_root = str(env.get("NYA02_RESIDENT_STATE") or "")
    if not life_state_root:
        raise Refused("LIFE_STATE_ROOT_MISSING")

    hermes_home = root / "hermes"
    _prepare_profile(hermes_home, owner="chiyo")

    model_meta = _public_model_meta()
    state = {
        "schema": SCHEMA_STATE,
        "root": str(root),
        "run_id": run_id,
        "hermes_home": str(hermes_home),
        "life_state_root": life_state_root,
        "budget_limits": {"utc_day_max": RUN_REQUEST_CAP,
                          "run_window_max": RUN_REQUEST_CAP},
        "shared_history_before": SHARED_HISTORY_BEFORE,
        "shared_total": SHARED_TOTAL,
        "prepared_at": _now(),
        "started": False,
        "started_at": None,
        "finished": False,
        "finished_at": None,
        "entry": ENTRY_DESCRIPTION,
        "model": model_meta,
        "camera_services": {"translator": "http://127.0.0.1:%d" % CAMERA_TRANSLATOR_PORT,
                            "sd": "http://127.0.0.1:%d" % CAMERA_SD_PORT},
        "preview_configs": list(PREVIEW_CONFIGS),
        "result_file": str(RESULT_MD),
        "run_result_json": str(RESULT_JSON),
        "photo_file": str(PHOTO_PATH),
    }
    _atomic_write_json(STATE_PATH, state)
    print(json.dumps({
        "schema": SCHEMA_PREPARE,
        "ok": True,
        "root": str(root),
        "run_id": run_id,
        "hermes_home": str(hermes_home),
        "life_state_root": life_state_root,
        "world_id": prepared.get("world", {}).get("data", {}).get("world_id")
        if isinstance(prepared.get("world"), dict) else None,
        "product_requests": 0,
        "images": 0,
        "model": model_meta,
        "preview_configs": list(PREVIEW_CONFIGS),
        "run_interpreter": _run_interpreter(),
        "run_command": ("wsl -e bash -lc 'cd %s && { set -a; [ -f /root/.nyairo-model.env ] && "
                        ". /root/.nyairo-model.env; set +a; }; exec env PYTHONDONTWRITEBYTECODE=1 "
                        "%s -B scripts/autonomous_camera_once.py run --live-model'"
                        % (REPO_ROOT, _run_interpreter())),
        "note": ("Parent must start camera-translator and camera-sd via preview_start first; "
                 "no product request or image was made here."),
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    state = _load_state(Path(args.state) if getattr(args, "state", None) else STATE_PATH)
    root = Path(str(state["root"]))
    out: dict[str, Any] = {
        "schema": SCHEMA_STATUS,
        "root": str(root),
        "root_exists": root.is_dir(),
        "started": bool(state.get("started")),
        "finished": bool(state.get("finished")),
        "run_id": state.get("run_id"),
        "entry": state.get("entry"),
        "model": _public_model_meta() if not state.get("model") else state.get("model"),
        "camera_services": _camera_services_status(),
        "preview_configs": list(PREVIEW_CONFIGS),
        "run_interpreter": _run_interpreter(),
        "product_requests_this_run": None,
        "shared_history_before": state.get("shared_history_before"),
        "shared_total": state.get("shared_total"),
    }
    if root.is_dir():
        try:
            from life_request_budget import LifeRequestBudget
            limits = state.get("budget_limits") or {}
            budget = LifeRequestBudget(
                state["life_state_root"],
                utc_day_max=limits.get("utc_day_max"),
                run_window_max=limits.get("run_window_max"),
                run_id=state.get("run_id"))
            view = budget.view()
            out["ledger"] = {key: view.get(key) for key in
                             ("state", "utc_day", "utc_day_calls", "utc_day_max",
                              "run_id", "run_window_calls", "run_window_max")}
            out["product_requests_this_run"] = view.get("run_window_calls")
        except Exception as exc:  # pragma: no cover - diagnostics only
            out["ledger"] = {"state": "unavailable", "error_type": type(exc).__name__}
    if state.get("finished") and RESULT_JSON.is_file():
        result = _read_json(RESULT_JSON) or {}
        out["result"] = {
            "camera_called": result.get("camera_called"),
            "image_generated": result.get("image_generated"),
            "not_covered": result.get("not_covered"),
            "failure_stage": result.get("failure_stage"),
            "api_calls": result.get("api_calls"),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------------------------
# service lifecycle
# ---------------------------------------------------------------------------------------------

def _start_services(root: Path, timeout: float = 180.0):
    from components.phone import demo as phone_demo

    stop_event = threading.Event()
    ready_event = threading.Event()
    holder: dict[str, Any] = {}

    def on_ready(announcement: Mapping[str, Any]) -> None:
        holder.update(dict(announcement))
        ready_event.set()

    def runner() -> None:
        try:
            phone_demo.serve_instance(root, port=0, ready_timeout=timeout,
                                      on_ready=on_ready, stop_event=stop_event,
                                      announce=False)
        except BaseException as exc:  # report readiness failure to the waiter
            holder.setdefault("error", "%s: %s" % (type(exc).__name__, exc))
            ready_event.set()

    thread = threading.Thread(target=runner, daemon=True, name="autocam-serve")
    thread.start()
    if not ready_event.wait(timeout + 30):
        stop_event.set()
        thread.join(timeout=20)
        raise Refused("PHONE_SERVICES_TIMEOUT")
    if holder.get("error"):
        stop_event.set()
        thread.join(timeout=20)
        raise Refused("PHONE_SERVICES_FAILED", str(holder["error"])[:300])
    if not holder.get("url"):
        stop_event.set()
        thread.join(timeout=20)
        raise Refused("PHONE_SERVICES_FAILED", "no url in announcement")

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=60)

    return holder, stop


# ---------------------------------------------------------------------------------------------
# the tool session
# ---------------------------------------------------------------------------------------------

def _phone_tool(hermes_home: Path, action: str, **fields) -> dict[str, Any]:
    from chiyo_bundle import phone_tool

    args = {"action": action}
    args.update(fields)
    raw = phone_tool.phone_handler(args, hermes_home=str(hermes_home))
    try:
        return json.loads(raw)
    except ValueError:
        return {"ok": False, "status": "unparsed", "message": raw[:200]}


def _world_facts(hermes_home: Path) -> str:
    """Only what the isolated World itself reports; never invented, never private prose."""
    answer = _phone_tool(hermes_home, "world")
    doc = answer.get("result") if isinstance(answer.get("result"), dict) else {}
    if not answer.get("ok") or not doc.get("ok"):
        reason = str(answer.get("reason_code") or doc.get("reason_code") or "WORLD_UNAVAILABLE")
        return "（世界快照暂时读不到：%s）" % reason
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    lines: list[str] = []

    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    place = location.get("name") or location.get("place_id") or location.get("area_id")
    if place:
        lines.append("- 你所在的地点：%s" % place)

    objects = data.get("objects") if isinstance(data.get("objects"), dict) else {}

    def _names(value: Any, limit: int = 8) -> list[str]:
        if not isinstance(value, list):
            return []
        out = []
        for item in value[:limit]:
            if isinstance(item, dict):
                label = item.get("name") or item.get("label") or item.get("object_id")
            else:
                label = item
            if label:
                out.append(str(label))
        return out

    here = _names(objects.get("here"))
    if here:
        lines.append("- 这里看得见的物件：%s" % "、".join(here))
    held = _names(objects.get("held"))
    if held:
        lines.append("- 你身上带着：%s" % "、".join(held))

    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    pose = body.get("pose") or data.get("pose")
    if pose:
        lines.append("- 当前姿势：%s" % pose)

    destinations = data.get("destinations")
    if not isinstance(destinations, list):
        world = data.get("world") if isinstance(data.get("world"), dict) else {}
        destinations = world.get("destinations")
    dest_names = _names(destinations)
    if dest_names:
        lines.append("- 现在能去的地方：%s" % "、".join(dest_names))

    travel = data.get("travel") if isinstance(data.get("travel"), dict) else {}
    if travel.get("status") and travel.get("status") != "IDLE":
        lines.append("- 行程状态：%s" % travel.get("status"))

    return "\n".join(lines) if lines else "（世界这次没有报告更多可见事实。）"


def _neutral_message(world_facts: str) -> str:
    # Neutral capability notice only: no "please take a photo", no scene suggestion.
    return (
        "你现在在自己的手机这一侧。这个会话里你能用 chiyo_phone 工具；它可以：\n"
        "- 只读地看你现在所在的世界、相册目录、今天的记录等；\n"
        "- 用 camera 动作，把你写的 scene（画面描述，中文也可以）和 caption（一句说明）"
        "交给本机生成一张图，存进你的相册；camera 需要你给一个 operation_key"
        "（同一次拍照重试要复用同一个 key）。\n"
        "以下是你现在所在世界自己报告的可见事实（不是我编的）：\n"
        "%s\n"
        "你可以按自己的想法使用手机，也可以什么都不做。"
    ) % world_facts


def _extract_camera(messages: Any) -> dict[str, Any]:
    """Find the first chiyo_phone camera tool call and its terminal tool result."""
    out: dict[str, Any] = {"called": False, "args": None, "result": None}
    if not isinstance(messages, list):
        return out
    pending: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                if str(function.get("name") or "") != "chiyo_phone":
                    continue
                raw_args = function.get("arguments")
                if isinstance(raw_args, dict):
                    args = dict(raw_args)
                else:
                    try:
                        args = json.loads(raw_args or "{}")
                    except (TypeError, ValueError):
                        args = {"_unparsed": True}
                if isinstance(args, dict) and str(args.get("action") or "") == "camera":
                    pending[str(call.get("id") or "")] = args
                    if out["args"] is None:
                        out["called"] = True
                        out["args"] = args
        elif message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id in pending and out["result"] is None:
                content = message.get("content")
                try:
                    out["result"] = json.loads(content) if isinstance(content, str) else content
                except ValueError:
                    out["result"] = {"ok": False, "status": "unparsed",
                                     "message": str(content)[:200]}
    return out


def _camera_result_summary(camera: Mapping[str, Any]) -> dict[str, Any]:
    result = camera.get("result") if isinstance(camera.get("result"), dict) else {}
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
    return {
        "status": result.get("status"),
        "ok": bool(result.get("ok")),
        "reason_code": result.get("reason_code") or data.get("reason_code"),
        "detail": result.get("detail") or data.get("detail"),
        "artifact_id": data.get("artifact_id"),
        "generated": data.get("generated"),
        "reused": data.get("reused"),
        "generation_prompt": data.get("generation_prompt"),
        "translation": data.get("translation"),
        "scene": data.get("scene"),
        "caption": data.get("caption"),
    }


def _budget_view(state: Mapping[str, Any]) -> dict[str, Any]:
    from life_request_budget import LifeRequestBudget

    limits = state.get("budget_limits") or {}
    budget = LifeRequestBudget(
        state["life_state_root"],
        utc_day_max=limits.get("utc_day_max"),
        run_window_max=limits.get("run_window_max"),
        run_id=state.get("run_id"))
    return budget.view()


def _read_album(hermes_home: Path) -> dict[str, Any]:
    listing = _phone_tool(hermes_home, "album")
    result = listing.get("result") if isinstance(listing.get("result"), dict) else {}
    return result


def _fetch_image(base_url: str, artifact_id: str) -> tuple[str, bytes]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/phone/album/%s/image" % artifact_id, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            return content_type, response.read()
    except urllib.error.HTTPError as error:
        try:
            return "", error.read()
        except Exception:
            return "", b""
    except (urllib.error.URLError, OSError):
        return "", b""


def _save_photo(body: bytes) -> bool:
    if not body:
        return False
    WORKER_RUNS.mkdir(parents=True, exist_ok=True)
    PHOTO_PATH.write_bytes(body)
    return True


def _record_album_readback(hermes_home: Path, phone_url: str, artifact_id: str,
                           facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Read the album (and one picture's real bytes) through the existing phone read faces."""
    album = _read_album(hermes_home)
    images = album.get("images") if isinstance(album.get("images"), list) else []
    if not artifact_id:
        for entry in images:
            if isinstance(entry, dict) and entry.get("generated_locally") is True:
                artifact_id = str(entry.get("artifact_id") or "")
                break
    row: dict[str, Any] = {}
    if artifact_id:
        detail = _phone_tool(hermes_home, "album_image", artifact_id=artifact_id)
        detail_result = detail.get("result") if isinstance(detail.get("result"), dict) else {}
        image = (detail.get("image") if isinstance(detail.get("image"), dict) else None) \
            or (detail_result.get("image") if isinstance(detail_result.get("image"), dict) else {})
        row = image
        content_type, body = _fetch_image(phone_url, artifact_id)
        if _save_photo(body):
            facts["photo_file"] = str(PHOTO_PATH)
            facts["photo_bytes"] = len(body)
            facts["photo_sha256"] = hashlib.sha256(body).hexdigest()
            facts["photo_content_type"] = content_type
    facts["album"] = {
        "count": len(images),
        "artifact_id": artifact_id or None,
        "scene": row.get("scene"),
        "generation_prompt": row.get("generation_prompt"),
        "caption": row.get("caption"),
        "created_at": row.get("created_at"),
        "generated_locally": row.get("generated_locally"),
        "source_label": row.get("source_label"),
        "media": row.get("media"),
    }
    return artifact_id, row


def _markdown(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _run_class():
    """Import the production Hermes engine and build the budget-gated subclass."""
    if str(REPO_ROOT / "vendor" / "hermes") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "vendor" / "hermes"))
    from run_agent import AIAgent  # noqa: E402

    class BudgetedAIAgent(AIAgent):
        """The stock AIAgent, with one product-request reservation before every send.

        ``_interruptible_api_call`` is the production loop's send boundary.  Reserving here
        means the existing persistent ledger is asked *before* the HTTP attempt; a refusal
        raises and nothing is sent.  The iteration-limit summary is suppressed so a bounded
        session cannot mint an extra toolless request outside the ledger.
        """

        _budget = None          # type: ignore[assignment]
        _on_reserve = None      # type: ignore[assignment]

        def _interruptible_api_call(self, api_kwargs):  # noqa: D401
            base = super()
            return _reserve_then_send(
                self._budget, self._on_reserve,
                lambda: base._interruptible_api_call(api_kwargs))

        def _interruptible_streaming_api_call(self, api_kwargs, *, on_first_delta=None):
            base = super()
            return _reserve_then_send(
                self._budget, self._on_reserve,
                lambda: base._interruptible_streaming_api_call(
                    api_kwargs, on_first_delta=on_first_delta))

        def _handle_max_iterations(self, messages, api_call_count):
            return "（本轮达到请求上限，没有继续请求。）"

    return BudgetedAIAgent


def _mark_started_once(record: Mapping[str, Any]) -> None:
    """Record the first reservation on the state written when the attempt was entered."""
    state = _read_json(STATE_PATH) or {}
    if state.get("schema") != SCHEMA_STATE:
        raise Refused("STATE_MISSING", str(STATE_PATH))
    state["first_reservation"] = {
        "kind": (record.get("reservation") or {}).get("kind")
        if isinstance(record.get("reservation"), dict) else "decision",
        "utc_day": record.get("utc_day"),
        "utc_day_calls": record.get("utc_day_calls"),
        "run_window_calls": record.get("run_window_calls"),
    }
    _atomic_write_json(STATE_PATH, state)


def cmd_run(args: argparse.Namespace) -> int:
    _require_posix()
    _clear_stale_root_env()
    if not args.live_model:
        raise Refused("LIVE_MODEL_FLAG_REQUIRED",
                      "pass --live-model explicitly; this command spends real product requests")
    state = _load_state()
    if _previous_attempt(state, _read_json(RESULT_JSON)):
        raise Refused("ALREADY_STARTED",
                      "this run already entered the agent call (%s); never re-send or re-run"
                      % (state.get("started_at") or state.get("finished_at") or "prior attempt"))
    root = Path(str(state["root"]))
    if not root.is_dir():
        raise Refused("ROOT_MISSING", str(root))
    hermes_home = Path(str(state["hermes_home"]))
    os.environ["HERMES_HOME"] = str(hermes_home)

    base_url_env, model_name, api_key, provider = _model_env_for_agent()
    services = _camera_services_status()
    missing = [name for name, ok in services.items() if not ok]
    if missing:
        raise Refused("CAMERA_SERVICES_NOT_READY",
                      "start camera-translator/camera-sd via preview_start first: %s"
                      % ", ".join(missing))

    holder: dict[str, Any] = {}
    stop_services = None
    agent = None
    started_at = _now()
    session_started = False
    facts: dict[str, Any] = {
        "schema": SCHEMA_RUN,
        "run_id": state.get("run_id"),
        "entry": ENTRY_DESCRIPTION,
        "started_at": started_at,
        "model": _public_model_meta(),
        "camera_called": False,
        "camera_args_source": None,
        "image_generated": False,
        "image_reused": False,
        "gpu_images": 0,
        "free_local_translations": 0,
        "album": None,
        "not_covered": False,
        "failure_stage": None,
        "budget_before": None,
        "budget_after": None,
    }
    try:
        holder, stop_services = _start_services(root)
        phone_url = str(holder["url"]).rstrip("/")
        _write_phone_section(hermes_home, root, phone_url)

        # 0-product pre-read: the isolated World's own visible facts.
        world_facts = _world_facts(hermes_home)
        message = _neutral_message(world_facts)

        before = _budget_view(state)
        facts["budget_before"] = {key: before.get(key) for key in
                                  ("run_window_calls", "utc_day_calls", "run_window_max")}
        BudgetedAIAgent = _run_class()
        agent = BudgetedAIAgent(
            base_url=base_url_env,
            api_key=api_key,
            provider=provider,
            api_mode="chat_completions",
            model=model_name,
            max_iterations=RUN_REQUEST_CAP,
            enabled_toolsets=["chiyo_world"],
            skip_context_files=True,
            load_soul_identity=True,
            skip_memory=True,
            skip_background_review=True,
            save_trajectories=False,
            quiet_mode=True,
            platform="cli",
            session_id="autocam-" + hashlib.sha256(
                (str(root) + started_at).encode("utf-8")).hexdigest()[:16],
            run_budget_seconds=300,
            request_overrides={"temperature": 0.7},
        )
        from life_request_budget import LifeRequestBudget

        limits = state.get("budget_limits") or {}
        budget = LifeRequestBudget(
            state["life_state_root"],
            utc_day_max=limits.get("utc_day_max"),
            run_window_max=limits.get("run_window_max"),
            run_id=state.get("run_id"))
        budget.ensure_window(allow_new=True)
        agent._budget = budget
        agent._on_reserve = _mark_started_once
        # Hermes counts ``_api_max_retries`` as the number of attempts (the init layer clamps
        # it to >= 1).  0 skipped the retry loop entirely: the send boundary never ran, so the
        # ledger stayed empty while the turn still reported one prepared API call.
        agent._api_max_retries = 1

        # Mark the attempt before entering the agent, not only on the first reservation.
        state = _mark_attempt_started(state)
        session_started = True
        result = agent.run_conversation(message)
        after = _budget_view(state)
        camera = _extract_camera(result.get("messages"))
        summary = _camera_result_summary(camera) if camera.get("called") else {}

        facts.update({
            "finished_at": _now(),
            "neutral_prompt_chars": len(message),
            "camera_called": bool(camera.get("called")),
            "camera_args_source": "model_tool_call" if camera.get("called") else None,
            "camera_args": camera.get("args") if camera.get("called") else None,
            "camera_status": summary.get("status"),
            "camera_ok": summary.get("ok"),
            "camera_reason": summary.get("reason_code"),
            "camera_detail": summary.get("detail"),
            "image_generated": bool(summary.get("generated") is True),
            "image_reused": bool(summary.get("reused") is True),
            "api_calls": result.get("api_calls"),
            "agent_completed": result.get("completed"),
            "budget_after": {key: after.get(key) for key in
                             ("run_window_calls", "utc_day_calls", "run_window_max")},
            "phone_url": phone_url,
            "services_pids": holder.get("children"),
        })

        # Read the album back through the existing phone tool (read-only), and the real bytes.
        _record_album_readback(hermes_home, phone_url,
                               str(summary.get("artifact_id") or ""), facts)
        if facts.get("album", {}).get("artifact_id"):
            facts["gpu_images"] = 1 if summary.get("generated") is True else 0
        translation = summary.get("translation") or {}
        if isinstance(translation, dict):
            facts["free_local_translations"] = 1 if translation.get("translated") else 0
            facts["translation_source"] = translation.get("source")

        if not facts["camera_called"]:
            facts["not_covered"] = True
            facts["failure_stage"] = None
            facts["verdict"] = "NOT_COVERED"
        elif facts["image_generated"] or facts["image_reused"]:
            facts["not_covered"] = False
            facts["failure_stage"] = None
            facts["verdict"] = "CAMERA_CHAIN_OK"
        else:
            facts["not_covered"] = False
            reason = str(facts.get("camera_reason") or "")
            if "TRANSLAT" in reason:
                facts["failure_stage"] = "camera_translation"
            elif "IMAGE" in reason or "BUSY" in reason or "JSON" in reason or "RUNTIME" in reason:
                facts["failure_stage"] = "camera_generation"
            else:
                facts["failure_stage"] = "camera_tool"
            facts["verdict"] = "CAMERA_FAILED"
    except Refused:
        raise
    except Exception as exc:  # a real provider/engine failure: keep it, do not re-run
        facts["finished_at"] = _now()
        facts["verdict"] = "RUN_FAILED"
        facts["failure_stage"] = "model_session" if session_started else "agent_setup"
        facts["error_type"] = type(exc).__name__
        try:
            after = _budget_view(state)
            facts["budget_after"] = {key: after.get(key) for key in
                                     ("run_window_calls", "utc_day_calls", "run_window_max")}
        except Exception:
            pass
        # If a picture was produced before the error, keep that truth even though the
        # session ended badly; the failure is still recorded, the image is not hidden.
        if session_started and holder.get("url"):
            try:
                _record_album_readback(hermes_home, str(holder["url"]).rstrip("/"), "", facts)
                album = facts.get("album") or {}
                if album.get("artifact_id") and album.get("generated_locally") is True:
                    facts["camera_called"] = True
                    facts["image_generated"] = True
                    facts["gpu_images"] = 1
                    facts["verdict"] = "CAMERA_CHAIN_OK"
                    facts["recovered_after_error"] = True
                    facts["failure_stage"] = "model_session_after_image"
            except Exception:
                pass
    finally:
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
        if stop_services is not None:
            stop_services()

    facts["shared_history_before"] = SHARED_HISTORY_BEFORE
    facts["shared_total"] = SHARED_TOTAL
    facts["shared_after"] = SHARED_HISTORY_BEFORE + int(
        (facts.get("budget_after") or {}).get("run_window_calls") or 0)
    facts.setdefault("finished_at", _now())

    state = _read_json(STATE_PATH) or state
    state["finished"] = True
    state["finished_at"] = facts.get("finished_at") or _now()
    state["verdict"] = facts.get("verdict")
    _atomic_write_json(STATE_PATH, state)
    _atomic_write_json(RESULT_JSON, facts)
    _write_run_markdown(facts)
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0 if facts.get("verdict") in ("CAMERA_CHAIN_OK", "NOT_COVERED") else 1


def _write_run_markdown(facts: Mapping[str, Any]) -> None:
    camera_called = "是" if facts.get("camera_called") else "否"
    scene_source = ("模型工具调用参数" if facts.get("camera_args_source") == "model_tool_call"
                    else "无（未选拍）")
    album = facts.get("album") if isinstance(facts.get("album"), dict) else {}
    lines = [
        "# 有界真实自主拍照 · 运行结果",
        "",
        "- 正式入口：%s" % facts.get("entry"),
        "- 运行命令：`python -B scripts/autonomous_camera_once.py run --live-model`（父级已起 18190/7860）",
        "- 真实判断是否选拍：%s（camera 状态 %s，原因 %s）" % (
            camera_called, facts.get("camera_status") or "无", facts.get("camera_reason") or "无"),
        "- scene 来源：%s；生成图：%s；复用旧图：%s" % (
            scene_source, "是" if facts.get("image_generated") else "否",
            "是" if facts.get("image_reused") else "否"),
        "- 相册读回：%s 张，artifact=%s，中文原话/英文/说明/时间=%s" % (
            album.get("count"), album.get("artifact_id") or "无",
            "齐" if (album.get("scene") and album.get("generation_prompt")
                     and album.get("caption") and album.get("created_at")) else "不全"),
        "- 产品预算：共享历史 %s/300 → 本 run 预留 %s，共同 %s/300" % (
            facts.get("shared_history_before"),
            (facts.get("budget_after") or {}).get("run_window_calls"),
            facts.get("shared_after")),
        "- 失败/未覆盖：%s；free 转换 %s 次、GPU 图 %s 张" % (
            facts.get("failure_stage") or ("NOT_COVERED" if facts.get("not_covered") else "无"),
            facts.get("free_local_translations"), facts.get("gpu_images")),
        "- 图片：%s" % (facts.get("photo_file") or "无"),
        "- 结论：%s" % facts.get("verdict"),
    ]
    RESULT_MD.write_text(_markdown(lines), encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# readback
# ---------------------------------------------------------------------------------------------

def cmd_readback(args: argparse.Namespace) -> int:
    _require_posix()
    _clear_stale_root_env()
    state = _load_state()
    root = Path(str(state["root"]))
    if not root.is_dir():
        raise Refused("ROOT_MISSING", str(root))
    hermes_home = Path(str(state["hermes_home"]))
    os.environ["HERMES_HOME"] = str(hermes_home)
    holder: dict[str, Any] = {}
    stop_services = None
    facts: dict[str, Any] = {"schema": SCHEMA_READBACK, "root": str(root),
                             "started": bool(state.get("started")),
                             "finished": bool(state.get("finished"))}
    try:
        holder, stop_services = _start_services(root)
        phone_url = str(holder["url"]).rstrip("/")
        _write_phone_section(hermes_home, root, phone_url)
        album = _read_album(hermes_home)
        images = album.get("images") if isinstance(album.get("images"), list) else []
        generated = [row for row in images
                     if isinstance(row, dict) and row.get("generated_locally") is True]
        facts["album_count"] = len(images)
        facts["locally_generated_count"] = len(generated)
        if generated:
            row = generated[0]
            artifact_id = str(row.get("artifact_id") or "")
            detail = _phone_tool(hermes_home, "album_image", artifact_id=artifact_id)
            detail_result = detail.get("result") if isinstance(detail.get("result"), dict) else {}
            image = detail_result.get("image") if isinstance(detail_result.get("image"), dict) else {}
            content_type, body = _fetch_image(phone_url, artifact_id)
            facts["image"] = {
                "artifact_id": artifact_id,
                "scene": image.get("scene"),
                "generation_prompt": image.get("generation_prompt"),
                "caption": image.get("caption"),
                "created_at": image.get("created_at"),
                "generated_locally": image.get("generated_locally"),
                "source_label": image.get("source_label"),
                "content_type": content_type,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest() if body else None,
            }
            facts["photo_saved"] = _save_photo(body)
            facts["photo_file"] = str(PHOTO_PATH) if facts["photo_saved"] else None
        else:
            facts["image"] = None
        facts["ledger"] = _budget_view(state)
    finally:
        if stop_services is not None:
            stop_services()
    _atomic_write_json(READBACK_JSON, facts)
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="bounded autonomous camera once")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="create the isolated root; 0 product requests")
    p_prepare.add_argument("--root", default=str(DEFAULT_ROOT))
    p_prepare.set_defaults(func=cmd_prepare)

    p_status = sub.add_parser("status", help="read-only status; no model")
    p_status.add_argument("--state", default=str(STATE_PATH))
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run", help="the one live tool session; explicit --live-model")
    p_run.add_argument("--live-model", action="store_true",
                       help="required: send real product-model requests (hard cap 3)")
    p_run.set_defaults(func=cmd_run)

    p_read = sub.add_parser("readback", help="read the album back; no model, no generation")
    p_read.set_defaults(func=cmd_readback)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Refused as exc:
        print(json.dumps({"ok": False, "reason": exc.reason_code, "detail": exc.detail},
                         ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
