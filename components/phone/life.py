#!/usr/bin/env python3
"""rc1 P: 手机实例上的主体 Life 绑定入口（``life-preflight`` / ``life-run``）——只有读与有限跑。

为什么要有这一层
----------------
手机实例（``components/phone/demo.py`` 建的 ``instance.json``）与 Life 实例（``one_day_life.prepare``
写的 ``<root>/one-day/instance.json``）是**两份文件，字段同源但不共写**。手机 ``serve`` 每一代都会把
**新的** ``supply_socket``/``supply_generation`` 写回手机实例（``demo.serve_instance``），而 Life 实例的
``environment`` 还停在做准备那一代的 socket 上。所以"用手机实例去起主体"这件事不能靠读 Life 清单，
必须**显式用手机实例的 live 绑定**（socket / subject / workspace / grant / work / reading / PDI /
world_home / world_body_socket），再把 Life 自己的 run_id / 请求预算 / install root 原样沿用。

本模块只做两件事（外加一项纯读的 Memory 参与检查），都不新建任何 store、不新开窗口、不装第二个总脑：

* :func:`life_preflight` —— 纯读、零写、零模型、无 socket 连接：把两份清单映射成一份可核对的绑定表，
  分别报出 available / missing / unknown，并声明"装配可行 != 模型已调用"。
* :func:`life_run` —— 复用**现有** ``autonomous_life_resident.AutonomousLifeResident``（同一个类、
  同一个 state root、同一把 lease），默认有限（``--once`` 或 ``--max-ticks`` / ``--duration-seconds``），
  没有真实 provider 配置就诚实 BLOCKED，绝不塞 fake 生产替身、绝不替操作者打开自主开关。
* :func:`check_memory` —— 纯读 SQLite ``mode=ro``：这个实例的 Memory 到底**有没有绑定**、绑了以后
  后端**是否健康可召回**。未绑定是明确降级（生活照跑，但报告里不许写"过去参与了"）；明确指定却缺损/
  不可读/治理不健康/主人不匹配一律 fail-closed，``life-run`` 不会把它当没看见。

红线（写死在实现里，不给调用者开关）
----------------------------------
* 不写任何 ``instance.json``（手机或 Life），不 chmod、不删、不 unlink 任何 socket；
* 不设置 ``CHIYO_AUTONOMOUS_NEW_RUN_WINDOW``（进程环境里若有也先剥掉），run 窗口与 run_id 只沿用；
* 不启动 Supply / World 子进程，不建第二个 socket——手机 ``serve`` 已经在跑的时候这里只起 Life writer；
* 输出里没有正文、没有笔记、没有 key：只出现路径、id、计数与布尔；
* CLI 不暴露任何注入口；测试用的 ``resident_factory`` / ``verify_owners`` 只在 Python API 上存在。

契约见 ``docs/engineering/rc1-subject-materials-runbook.md``；结果见
``docs/engineering/rc1-subject-materials-binding-result.md``。
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import service as phone_service

REPO_ROOT = phone_service.REPO_ROOT

#: 与 ``one_day_life`` / ``demo`` 的目录名保持一致（同一个真实根，不另建）。
ONE_DAY_DIRNAME = "one-day"
LIFE_DIRNAME = "life"
LIFE_STATE_DIRNAME = "state"
BRIDGE_DIRNAME = ("components", "world", "service", "bridge")

SCHEMA_PREFLIGHT = "phone.life.preflight.v1"
SCHEMA_RUN = "phone.life.run.v1"

#: 与 ``one_day_life`` 相同的退出码语汇（同一套运营手册，不另造一套）。
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_LEASE_HELD = 4

#: Life runtime 自己的要求：state root 只允许 owner 读写。
STATE_ROOT_MODE = 0o700

#: 有限跑的默认上界：没有 ``--once`` 也不给 ``--max-ticks`` 时仍然是有界的，常驻必须显式要求。
DEFAULT_RUN_DURATION_SECONDS = 1800

READY = "READY"
BLOCKED = "BLOCKED"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

#: Memory 参与的第五种状态：路径/绑定**记录在案**，但后端是否健康可召回还没被证实。
#: 它与 ``READY`` 分开写，就是为了让"配置路径存在"永远不等于"记忆真的能用"。
CONFIGURED = "CONFIGURED"
#: 这个实例**从来没有**绑定 Memory。它是有意的可选降级，不是故障、也不是"她没有过去"。
UNBOUND = "UNBOUND"

#: 进程环境里这些前缀只属于实例自己：先整段剥掉，再按"nested 旧清单 -> 手机 live"重放。
#: ``CHIYO_AUTONOMOUS_*`` 一起剥，是为了让 ``CHIYO_AUTONOMOUS_NEW_RUN_WINDOW`` 不可能从
#: 操作者的 shell 漏进来（那等于偷偷开一个新额度窗口）。
OWNED_PREFIXES = ("CHIYO_AUTONOMOUS_",)

#: 只有手机实例才是 live 真相的字段；这些一律覆盖 nested 清单里的同名值。
PHONE_LIVE_FIELDS = {
    "supply_socket": ("LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET"),
    "subject": ("NYA02_SUBJECT", "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT"),
    "artifact_grant_id": ("NYA02_ARTIFACT_GRANT", "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT"),
    "workspace_id": ("CHIYO_LIFE_SUPPLY_WORKSPACE_ID",),
    "world_home": ("CHIYO_WORLD_HOME", "HERMES_HOME"),
    "world_id": ("CHIYO_WORLD_ID",),
    "world_package": ("CHIYO_WORLD_PACKAGE",),
    "world_body_socket": ("WORLD_BODY_SOCKET",),
}

#: ``autonomous_life_resident`` 自己读的那几个名字（``:69-71``：``MEMORY_DB_ENV`` /
#: ``MEMORY_CONTROLS_ENV`` / ``MEMORY_SUBJECT_ENV``）。真正解析时从模块里取，取不到才退回这些
#: 字面量——本层绝不发明第五种拼法，也不猜任何默认路径。
MEMORY_ENV_FALLBACK = {
    "database": ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB"),
    "controls": ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB"),
    "owner": ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT"),
}
#: ``_memory_ports`` / ``_continuity_source`` 在没有 ``NYA02_MEMORY_SUBJECT`` 时用的字面量。
MEMORY_DEFAULT_OWNER = "chiyo"
#: ``life_experience_port.LIFE_CONVERSATION_PREFIX``（``:50``）：Life 的 M0 事件自成一条流，
#: 不借用任何真实会话。这里只是那一行契约的兜底字面量——能 import 时以模块为准。
MEMORY_CONVERSATION_PREFIX = "life:"
#: M0 里 Life 已验证结果的来源标记（``components/native/src/app/evidence.py:25``）。只用于一次
#: ``count(*)``，不是第二套语义。
LIFE_OUTCOME_ORIGIN = "LIFE_VERIFIED_OUTCOME"

#: 报告里 Memory 的参与口径：让"没绑记忆"永远不可能被读成"她没有过去"。
MEMORY_PARTICIPATION = {
    UNBOUND: "NOT_BOUND_OPTIONAL_DEGRADATION",
    CONFIGURED: "BOUND_HEALTH_UNVERIFIED",
    READY: "BOUND_READY_READONLY_VERIFIED",
    BLOCKED: "BOUND_BUT_UNUSABLE",
    UNKNOWN: "UNRESOLVED",
}

class PhoneLifeError(RuntimeError):
    """A named refusal.  Nothing is written, nothing is started."""


# ---------------------------------------------------------------------------------------------
# lazy Life imports (the Life scripts are not a package; one path entry, no second copy)
# ---------------------------------------------------------------------------------------------


def _life_modules() -> dict[str, Any]:
    import importlib

    scripts = str(REPO_ROOT / "components" / "life" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return {
        "odl": importlib.import_module("one_day_life"),
        "assembly": importlib.import_module("autonomous_life_assembly"),
        "resident": importlib.import_module("autonomous_life_resident"),
        "control": importlib.import_module("autonomous_life_control"),
    }


def _euid() -> Optional[int]:
    getter = getattr(os, "geteuid", None)
    return int(getter()) if callable(getter) else None


def _int_or_none(value: Any) -> Optional[int]:
    # ``0`` is a real value here (uid 0 is root): only a missing/blank entry is "unknown".
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# instance facts (read-only, never written back)
# ---------------------------------------------------------------------------------------------


def phone_root(root: Path | str) -> Path:
    return Path(str(root)).expanduser()


def load_phone_instance(root: Path | str) -> dict[str, Any]:
    """The phone instance as ``serve`` last wrote it.  Raises :class:`PhoneLifeError`."""

    try:
        return dict(phone_service.load_instance(phone_root(root)))
    except phone_service.PhoneServiceError as exc:
        raise PhoneLifeError(str(exc)) from exc


def nested_life_dir(root: Path | str) -> Path:
    """The instance's own Life root (``demo`` prepares ``odl.prepare(instance=<root>/one-day)``)."""

    return phone_root(root) / ONE_DAY_DIRNAME


def read_nested_life(root: Path | str) -> Optional[dict[str, Any]]:
    """The nested Life instance, or ``None`` when this phone root never had one.

    An unreadable/invalid nested file is a refusal (never silently ignored): the Life root is
    where the state, the switch and the run window live, and guessing it would be a second brain.
    """

    odl = _life_modules()["odl"]
    directory = nested_life_dir(root)
    if not odl.instance_path(directory).exists():
        return None
    try:
        return dict(odl.read_instance(directory))
    except odl.OneDayError as exc:
        raise PhoneLifeError("NESTED_LIFE_INSTANCE_INVALID: %s" % exc) from exc


def life_install_root(root: Path | str, nested: Optional[Mapping[str, Any]] = None
                      ) -> Path:
    """``<root>/one-day/life`` —— ``one_day_life.prepare`` 里的 ``CHIYO_LIFE_INSTALL_ROOT``。"""

    environment = dict((nested or {}).get("environment") or {})
    recorded = str(environment.get("CHIYO_LIFE_INSTALL_ROOT") or "").strip()
    return Path(recorded) if recorded else nested_life_dir(root) / LIFE_DIRNAME


def life_state_root(root: Path | str, nested: Optional[Mapping[str, Any]] = None) -> Path:
    """The one real Life state root (``one_day_life.life_state_root`` over the nested root)."""

    environment = dict((nested or {}).get("environment") or {})
    recorded = str(environment.get("NYA02_RESIDENT_STATE") or "").strip()
    if recorded:
        return Path(recorded)
    return life_install_root(root, nested) / LIFE_STATE_DIRNAME


def serve_marker(root: Path | str) -> Optional[dict[str, Any]]:
    """``run/phone.serve.json`` —— what the last ``serve`` generation claimed.  Read-only."""

    path = phone_root(root) / "run" / "phone.serve.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------------------------
# the environment mapping (explicit, provenance-tracked, never persisted)
# ---------------------------------------------------------------------------------------------


def map_life_environment(phone: Mapping[str, Any], nested: Optional[Mapping[str, Any]],
                         *, base_env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The environment the existing Life runtime is assembled with.  See :func:`build_binding`."""

    return dict(build_binding(phone, nested, base_env=base_env)["environment"])


def build_binding(phone: Mapping[str, Any], nested: Optional[Mapping[str, Any]],
                  *, base_env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Map *one* phone instance onto the Life runtime's own environment names.

    Order is the whole design: an operator's stray shell export can never decide anything
    (``one_day_life.OWNED_ENV_NAMES`` and every ``CHIYO_AUTONOMOUS_*`` name are stripped), then
    the nested Life instance's own recorded environment is copied in (its business list is
    *not* rewritten — only read), then the phone instance's live values win.  Nothing here
    writes; the nested file keeps whatever it had.

    Returns ``environment``, per-name ``sources``, the list of names the live binding actually
    ``overridden`` (evidence that the nested socket generation differs), plus ``missing`` /
    ``unknown`` for the report.
    """

    odl = _life_modules()["odl"]
    nested_env = {str(k): str(v) for k, v in dict((nested or {}).get("environment") or {}).items()}

    env: dict[str, str] = dict(os.environ if base_env is None else base_env)
    for name in list(env):
        if name.startswith(OWNED_PREFIXES) or name in odl.OWNED_ENV_NAMES:
            env.pop(name, None)

    sources: dict[str, str] = {}
    for name, value in nested_env.items():
        #: An empty recorded value means "not bound"; carrying it over would let
        #: ``bindings[*].source`` claim a provenance for an empty binding.
        if not str(value).strip():
            continue
        env[name] = value
        sources[name] = "nested_life"

    overridden: list[str] = []
    conflicts: list[str] = []

    def put(names: tuple[str, ...], value: Any, source: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for name in names:
            previous = env.get(name)
            if previous and previous != text:
                overridden.append(name)
            env[name] = text
            sources[name] = source

    for field, names in PHONE_LIVE_FIELDS.items():
        put(names, phone.get(field), "phone_live")
    #: The World gateway is canonical-mode only: the same socket the phone's own child binds.
    put(("WORLD_BODY_INTEGRATION_MODE",), "canonical" if str(phone.get("world_body_socket") or "")
        else "", "phone_live")
    #: The three explicit artifact bindings.  An empty reading id is *honest*: no reading
    #: candidate exists then, and this layer never substitutes another artifact for it.
    put(("CHIYO_LIFE_WORK_ARTIFACT_ID",), phone.get("work_artifact_id"), "phone_live")
    put(("CHIYO_LIFE_READING_ARTIFACT_ID",), phone.get("reading_artifact_id"), "phone_live")
    put(("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",), phone.get("private_items_artifact_id"),
        "phone_live")
    #: The phone's own recorded switches (daily rhythm / room environment / world runtime).
    for name, value in dict(phone.get("features") or {}).items():
        put((str(name),), value, "phone_features")

    #: Life's own root/state/switch facts.  Recorded values win; the defaults are the very paths
    #: ``one_day_life.prepare`` would have written for this root.
    install_root = life_install_root(phone.get("root") or "", nested)
    state_root = life_state_root(phone.get("root") or "", nested)
    put(("CHIYO_LIFE_INSTALL_ROOT",), install_root, "life_root")
    put(("NYA02_RESIDENT_STATE",), state_root, "life_root")

    #: The instance's own provider file (``<root>/one-day/model_env``), exactly like
    #: ``one_day_life.instance_environment``: an already-present variable still wins.
    try:
        for name, value in odl.load_model_env(nested_life_dir(phone.get("root") or "")).items():
            if not env.get(name):
                env[name] = value
                sources[name] = "nested_model_env"
    except Exception:  # an unreadable model_env is "no key", never a fabricated one
        pass

    #: Hard invariant, asserted instead of trusted: this process never opens a run window.
    env.pop(odl.NEW_RUN_WINDOW_ENV, None)
    sources.pop(odl.NEW_RUN_WINDOW_ENV, None)

    missing: list[str] = []
    for field in ("supply_socket", "subject", "workspace_id", "artifact_grant_id",
                  "world_home", "private_items_artifact_id"):
        if not str(phone.get(field) or "").strip():
            missing.append(field)
    for name in ("CHIYO_LIFE_WORK_ARTIFACT_ID", "LIFE_SUPPLY_SOCKET", "NYA02_SUBJECT"):
        if not env.get(name):
            missing.append(name)

    unknown: list[str] = []
    if not str(phone.get("reading_artifact_id") or "").strip():
        unknown.append("reading_artifact_id")
    if not nested:
        unknown.append("nested_life_instance")
    if not env.get(odl.RUN_ID_ENV):
        unknown.append(odl.RUN_ID_ENV)
    if _int_or_none(env.get(odl.REQUEST_UTC_DAY_MAX_ENV)) is None \
            and _int_or_none(env.get(odl.REQUEST_RUN_WINDOW_MAX_ENV)) is None:
        unknown.append("request_ceiling")

    #: A nested value that survives into the environment *and* disagrees with the phone is only
    #: acceptable for the socket generation (that is exactly what ``overridden`` records).  Any
    #: other disagreement is a cross-binding and is reported, never silently merged.
    for name in ("NYA02_ARTIFACT_GRANT", "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT",
                 "CHIYO_LIFE_SUPPLY_WORKSPACE_ID", "NYA02_SUBJECT"):
        nested_value = nested_env.get(name)
        live_value = env.get(name)
        if nested_value and live_value and nested_value != live_value:
            conflicts.append(name)

    return {"environment": env, "sources": sources, "overridden": sorted(set(overridden)),
            "conflicts": conflicts, "missing": missing, "unknown": unknown}


# ---------------------------------------------------------------------------------------------
# individual checks (all read-only; none of them connects to anything)
# ---------------------------------------------------------------------------------------------


def check_model(environment: Mapping[str, str]) -> dict[str, Any]:
    """Is a *real* provider configured?  Pure configuration reading: no request, no probe.

    Resolution goes through the same private helper the resident itself uses
    (``autonomous_life_assembly._model_config``), and falls back to the public
    ``profile_model_config`` primitives when that helper is absent — so this cannot claim
    "configured" for a provider the assembly would refuse, or the reverse.
    """

    out: dict[str, Any] = {"status": BLOCKED, "configured": False, "probe": "not_performed",
                           "request_sent": False}
    try:
        assembly = _life_modules()["assembly"]
        from profile_model_config import describe_model_config

        resolver = getattr(assembly, "_model_config", None)
        config = None
        if callable(resolver):
            config = resolver(dict(environment))
        else:  # pragma: no cover - the helper is present in this tree
            from profile_model_config import load_configured_model_config

            base_url = str(environment.get("NYA02_MODEL_BASE_URL") or "").strip()
            model = str(environment.get("NYA02_MODEL_NAME") or "").strip()
            key = str(environment.get("NYA02_MODEL_API_KEY") or "").strip()
            if base_url and model and key:
                config = {"provider": environment.get("NYA02_MODEL_PROVIDER") or "custom",
                          "model": model, "base_url": base_url, "api_key": key}
            if config is None:
                config = load_configured_model_config(
                    hermes_home=environment.get("NYA02_PROFILE_HOME")
                    or environment.get("CHIYO_PROFILE_HOME"))
        if config is None:
            out["detail"] = "this instance has no usable provider configuration"
            out["description"] = describe_model_config({})
            return out
        description = describe_model_config(config)
        out["description"] = description
        out["configured"] = bool(description.get("usable"))
        out["status"] = READY if out["configured"] else BLOCKED
        if not out["configured"]:
            out["detail"] = "the provider description is not usable (configuration incomplete)"
        return out
    except Exception as exc:
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
        return out


def check_supply_binding(phone: Mapping[str, Any], *, environment: Optional[Mapping[str, str]]
                         = None) -> dict[str, Any]:
    """Is the socket binding present, a socket, and the *live* generation?  No connect."""

    socket_path = str(phone.get("supply_socket") or "").strip()
    out: dict[str, Any] = {
        "status": UNAVAILABLE,
        "socket": socket_path,
        "generation": str(phone.get("supply_generation") or ""),
        "present": False,
        "is_socket": False,
        "probe": "not_performed_read_only",
        "stale": False,
    }
    path = Path(socket_path) if socket_path else None
    if path is not None:
        try:
            info = path.lstat()
        except OSError:
            out["status"] = UNAVAILABLE
            out["detail"] = "SUPPLY_SOCKET_MISSING"
        else:
            out["present"] = True
            out["is_socket"] = stat.S_ISSOCK(info.st_mode)
            out["mode"] = "%04o" % (stat.S_IMODE(info.st_mode),)
            out["owner_uid"] = int(info.st_uid)
            out["status"] = READY if out["is_socket"] else BLOCKED
            if not out["is_socket"]:
                out["detail"] = "SUPPLY_PATH_IS_NOT_A_SOCKET"
    marker = serve_marker(str(phone.get("root") or ""))
    if marker:
        out["serve_marker_generation"] = marker.get("supply_generation")
        if str(marker.get("supply_generation") or "") != out["generation"]:
            out["stale"] = True
            out["status"] = BLOCKED
            out["detail"] = "SUPPLY_BINDING_STALE"
    if environment is not None:
        bound = str(environment.get("LIFE_SUPPLY_SOCKET") or "").strip()
        out["environment_binding_matches"] = bool(bound) and bound == socket_path
    return out


def check_world_binding(phone: Mapping[str, Any]) -> dict[str, Any]:
    """The World gateway path and the bridge dir — existence only, no connect."""

    socket_path = str(phone.get("world_body_socket") or "").strip()
    bridge = REPO_ROOT.joinpath(*BRIDGE_DIRNAME)
    out: dict[str, Any] = {"status": UNKNOWN, "socket": socket_path, "present": False,
                           "bridge_dir": str(bridge), "bridge_dir_exists": bridge.is_dir(),
                           "probe": "not_performed_read_only"}
    if socket_path:
        try:
            info = Path(socket_path).lstat()
        except OSError:
            out["detail"] = "WORLD_GATEWAY_MISSING"
        else:
            out["present"] = True
            out["is_socket"] = stat.S_ISSOCK(info.st_mode)
            out["status"] = READY if out["is_socket"] else BLOCKED
    else:
        out["detail"] = "WORLD_BODY_SOCKET not bound in this instance"
    return out


def check_permissions(root: Path | str, phone: Mapping[str, Any]) -> dict[str, Any]:
    """Ownership and modes.  Reported, never "fixed" — this layer chmods nothing."""

    root_path = phone_root(root)
    euid = _euid()
    recorded = _int_or_none(phone.get("local_owner_uid"))
    out: dict[str, Any] = {
        "status": UNKNOWN,
        "phone_root": str(root_path),
        "recorded_local_owner_uid": recorded,
        "effective_uid": euid,
        "uid_matches": None if (euid is None or recorded is None) else euid == recorded,
    }
    try:
        info = root_path.stat()
    except OSError as exc:
        out["detail"] = "PHONE_ROOT_UNREADABLE: %s" % type(exc).__name__
        return out
    out["phone_root_mode"] = "%04o" % (stat.S_IMODE(info.st_mode),)
    out["phone_root_owner_uid"] = int(info.st_uid)
    nested = None
    try:
        nested = read_nested_life(root_path)
    except PhoneLifeError:
        nested = None
    state_root = life_state_root(root_path, nested)
    out["state_root"] = str(state_root)
    out["state_root_exists"] = state_root.is_dir()
    if out["state_root_exists"]:
        try:
            state_info = state_root.stat()
        except OSError as exc:
            out["state_root_mode"] = None
            out["detail"] = "STATE_ROOT_UNREADABLE: %s" % type(exc).__name__
            return out
        out["state_root_mode"] = "%04o" % (stat.S_IMODE(state_info.st_mode),)
        out["state_root_secure"] = (stat.S_IMODE(state_info.st_mode) & 0o077) == 0
        out["state_root_owner_uid"] = int(state_info.st_uid)
    else:
        out["state_root_secure"] = None
    if out["uid_matches"] is False:
        out["status"] = BLOCKED
        out["detail"] = "LOCAL_OWNER_UID_MISMATCH"
    elif out["uid_matches"] is None:
        out["status"] = UNKNOWN
    else:
        out["status"] = READY
    return out


def check_single_lease(state_root: Path | str, *, probe: Optional[Callable[..., Any]] = None
                       ) -> dict[str, Any]:
    """The canonical single-writer lease, *without* creating or deleting anything.

    ``ActivityAuthorityLease.acquire()`` would create ``run/``, the lock file and a marker (and
    ``release()`` would unlink the marker), so it is not a read-only probe.  Instead the existing
    lock file is opened read-only and ``flock``ed non-blocking: an absent file means nothing ever
    took the lease here, a successful lock means free, EAGAIN/EACCES means somebody holds it.
    The marker is read for evidence and never touched.  Where ``fcntl`` does not exist the check
    is honestly ``UNVERIFIED`` instead of being reported free.
    """

    if probe is not None:
        return dict(probe(Path(state_root)))
    out: dict[str, Any] = {"status": UNKNOWN, "probe": "read_only_flock"}
    try:
        import activity_continuity as ac
        import fcntl
        import errno
    except Exception as exc:
        out["reason"] = "LEASE_PROBE_UNAVAILABLE"
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
        return out

    root = Path(state_root)
    run_dir = root / "run"
    lock_path = run_dir / ac.LOCK_FILENAME
    marker_path = run_dir / ac.MARKER_FILENAME
    out["lock_path"] = str(lock_path)
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = None
    if isinstance(marker, dict):
        out["marker"] = {"instance_id": marker.get("instance_id"),
                         "pid": marker.get("pid"),
                         "acquired_at": marker.get("acquired_at")}
    if not lock_path.exists():
        out["status"] = "FREE"
        out["held_by_another_process"] = False
        return out
    try:
        handle = lock_path.open("r")
    except OSError as exc:
        out["status"] = UNKNOWN
        out["detail"] = "LOCK_UNREADABLE: %s" % type(exc).__name__
        return out
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                out["status"] = "HELD"
                out["held_by_another_process"] = True
                return out
            out["status"] = UNKNOWN
            out["detail"] = "LOCK_PROBE_FAILED: %s" % type(exc).__name__
            return out
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:  # pragma: no cover - unlocking a fresh handle
            pass
        out["status"] = "FREE"
        out["held_by_another_process"] = False
        return out
    finally:
        handle.close()


def check_switch(root: Path | str, nested: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The *existing* operator switch and today's budget, read from disk only."""

    control = _life_modules()["control"]
    install_root = life_install_root(root, nested)
    out: dict[str, Any] = {"install_root": str(install_root), "status": UNKNOWN}
    try:
        view = control.read_status(str(install_root))
    except Exception as exc:
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    switch = dict(view.get("autonomous_switch") or {})
    out["autonomous_switch"] = switch
    out["budget_today"] = view.get("budget_today")
    recorded = switch.get("enabled")
    out["recorded"] = recorded
    out["status"] = READY if recorded is True else BLOCKED
    if recorded is not True:
        out["detail"] = "AUTONOMOUS_SWITCH_OFF"
    return out


def check_privacy(environment: Mapping[str, str]) -> dict[str, Any]:
    """The chat-side private permit, read-only, and an explicit "nothing private was read".

    ``phone.private_artifacts`` is the *chat tool*'s permit (``chiyo_bundle/phone_tool.py``); the
    Life runtime is the subject's own process and is bound by the instance's owner grant.  This
    layer never widens that permit, never reads the private-items document, and says so.
    """

    home = str(environment.get("NYA02_PROFILE_HOME") or environment.get("CHIYO_PROFILE_HOME")
               or "").strip()
    path = (Path(home) if home else Path.home() / ".hermes") / "chiyo" / "config.json"
    out: dict[str, Any] = {
        "status": UNKNOWN,
        "profile_config": str(path),
        "private_artifacts_permit": None,
        "private_content_read": False,
        "note": "只报 permit 与绑定 id；从不读私人笔记正文，也从不改这个 permit",
    }
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["detail"] = "PROFILE_PHONE_SECTION_ABSENT"
        return out
    section = (config.get("phone") if isinstance(config, dict) else None) or {}
    if isinstance(section, dict):
        out["private_artifacts_permit"] = section.get("private_artifacts") is True
        out["phone_enabled"] = section.get("enabled") is True
        out["write_enabled"] = section.get("write_enabled") is True
        out["status"] = READY
    return out


# ---------------------------------------------------------------------------------------------
# Memory participation: what the runtime actually reads, and whether it can recall anything
# ---------------------------------------------------------------------------------------------


def _native_paths() -> None:
    """``components/native`` on ``sys.path``.  Module imports only; no store is constructed."""

    native = REPO_ROOT / "components" / "native"
    for entry in (native, native / "src", native / "memory_runtime_v1"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


def memory_env_names() -> dict[str, tuple[str, ...]]:
    """The exact names the shipped resident reads for Memory — never a name this layer invented."""

    names = {key: tuple(value) for key, value in MEMORY_ENV_FALLBACK.items()}
    try:
        resident = _life_modules()["resident"]
    except Exception:  # the Life scripts are not importable here: the frozen names still hold
        return names
    for key, attribute in (("database", "MEMORY_DB_ENV"), ("controls", "MEMORY_CONTROLS_ENV"),
                           ("owner", "MEMORY_SUBJECT_ENV")):
        value = getattr(resident, attribute, None)
        if isinstance(value, str) and value.strip():
            names[key] = (value.strip(),)
        elif isinstance(value, (tuple, list)) and value:
            names[key] = tuple(str(item) for item in value if str(item).strip())
    return names


def _first_bound(environment: Mapping[str, str], names: tuple[str, ...]
                 ) -> tuple[Optional[str], Optional[str]]:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value, name
    return None, None


def _readonly_sqlite(path: Path | str):
    """Open an *existing* SQLite file ``mode=ro``: it can neither create nor migrate a file."""

    import sqlite3

    return sqlite3.connect(Path(str(path)).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def _memory_probe(database: Path, controls: Path, *, owner: str, conversation: str
                  ) -> dict[str, Any]:
    """Read-only facts about the two configured files.  No owner constructor, no DDL, no write.

    ``EvidenceStore(...)`` would ``mkdir`` the parent, run migrations and ``chmod`` the file, and a
    ledger ``initialize()`` would create tables; neither is acceptable in a preflight.  This opens
    ``mode=ro`` handles instead and asks the governance projection for its verdict — the *same*
    read-only projection the runtime's own continuity probe uses
    (``autonomous_life_resident.py:347``), so "healthy" means the same thing here and at run time.
    """

    facts: dict[str, Any] = {
        "probe": "read_only_sqlite_mode_ro",
        "database": str(database),
        "controls_database": str(controls),
        "database_exists": False,
        "database_is_file": False,
        "controls_exists": False,
        "controls_is_file": False,
        "readable": None,
        "integrity": None,
        "m0_table_present": None,
        "life_outcomes": None,
        "governance_healthy": None,
        "controls": None,
        "error": None,
    }
    for key, path in (("database", database), ("controls", controls)):
        try:
            info = Path(path).lstat()
        except OSError:
            continue
        facts[key + "_exists"] = True
        facts[key + "_is_file"] = stat.S_ISREG(info.st_mode)
    if not (facts["database_is_file"] and facts["controls_is_file"]):
        return facts
    try:
        connection = _readonly_sqlite(database)
    except Exception as exc:
        facts["error"] = "OPEN_FAILED: %s: %s" % (type(exc).__name__, exc)
        return facts
    try:
        facts["readable"] = True
        facts["integrity"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        facts["m0_table_present"] = bool(connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_events'"
        ).fetchone())
        if facts["m0_table_present"] and facts["integrity"].lower() == "ok":
            facts["life_outcomes"] = int(connection.execute(
                "SELECT count(*) FROM evidence_events WHERE memory_owner=? AND source_origin=?",
                (owner, LIFE_OUTCOME_ORIGIN)).fetchone()[0])
    except Exception as exc:
        facts["error"] = "READ_FAILED: %s: %s" % (type(exc).__name__, exc)
        return facts
    finally:
        connection.close()
    try:
        _native_paths()
        from memory_control import MemoryControlLedger

        projection = MemoryControlLedger(controls, database, owner=owner,
                                         conversation=conversation).projection()
    except Exception as exc:
        facts["error"] = "GOVERNANCE_PROBE_FAILED: %s: %s" % (type(exc).__name__, exc)
        return facts
    facts["governance_healthy"] = bool(getattr(projection, "healthy", False))
    facts["controls"] = int(getattr(projection, "controls", 0) or 0)
    return facts


def check_memory(environment: Mapping[str, str], *, phone: Optional[Mapping[str, Any]] = None,
                 probe: Optional[Callable[..., Mapping[str, Any]]] = None) -> dict[str, Any]:
    """Is Memory *bound* on this instance, and can it actually be recalled from?

    Five outcomes, and none of them may impersonate another:

    * ``UNBOUND`` — no Memory database is recorded.  A deliberate, optional degradation: the
      resident wires no past source and the cycle says ``PAST_SOURCE_NOT_WIRED``.  It is **not**
      a fault and it is **not** "she has no past", but it also never claims memory took part.
    * ``CONFIGURED`` — a database is recorded and present, yet backend health could not be
      established (the probe itself did not answer).  "Path exists" stays a weaker claim than
      "healthy and recallable", and a run on it is refused (see :func:`life_run`).
    * ``READY`` — the M0 file opens ``mode=ro``, passes ``quick_check``, has ``evidence_events``,
      and the control ledger's own read-only projection is healthy for this owner.
    * ``BLOCKED`` — explicitly configured but missing / not a file / unreadable / schema
      mismatch / ledger unhealthy.  Fail closed: it is reported, never ignored.
    * ``UNKNOWN`` — the binding itself could not be resolved.

    The Memory owner and the phone's Supply subject live in **different domains** and are never
    compared for equality: ``one_day_life.prepare(memory_subject=...)`` lets a Memory persona such as
    ``chiyo.persona`` sit next to a workspace subject such as ``chiyo`` / ``chiyo.global``.  Both
    identities are reported (``owner`` / ``phone_subject``) and ``subject_matches_phone`` is always
    ``None`` = NOT_COMPARABLE.

    Nothing is created, migrated or written; the two files are only ever opened ``mode=ro``.
    ``probe`` exists so a test can simulate an unavailable probe without touching the real one.
    """

    names = memory_env_names()
    database, database_env = _first_bound(environment, names["database"])
    controls, controls_env = _first_bound(environment, names["controls"])
    owner, owner_env = _first_bound(environment, names["owner"])
    owner = owner or MEMORY_DEFAULT_OWNER
    #: The phone's own ``subject`` is the **Supply workspace** identity; the Memory persona is a
    #: different domain (``one_day_life.prepare(memory_subject=...)`` at ``:438-441`` deliberately
    #: lets them differ).  It is reported, never compared for equality.
    phone_subject = str((phone or {}).get("subject") or "").strip()
    out: dict[str, Any] = {
        "status": UNBOUND,
        "reason": "MEMORY_OPTIONAL_UNBOUND",
        "detail": None,
        "optional": True,
        "degraded": True,
        "blocking": False,
        "path_configured": False,
        "recall_ready": False,
        "database": database,
        "database_env": database_env,
        "controls_database": None,
        "controls_env": None,
        "controls_defaulted": False,
        "owner": owner,
        "owner_env": owner_env,
        "owner_defaulted": owner_env is None,
        #: Always ``None`` = NOT_COMPARABLE.  A Memory persona that differs from the phone's supply
        #: subject is *legal* (``one_day_life.prepare(memory_subject=...)``), so this can never be a
        #: ``False`` that turns into a refusal; both identities are simply reported side by side.
        "subject_matches_phone": None,
        "phone_subject": phone_subject or None,
        "conversation": None,
        "path_exists": None,
        "is_file": None,
        "controls_exists": None,
        "controls_is_file": None,
        "readable": None,
        "integrity": None,
        "m0_table_present": None,
        "life_outcomes": None,
        "governance_healthy": None,
        "controls": None,
        "error": None,
        "probe": "read_only_sqlite_mode_ro",
        "writes": False,
        "created": False,
        "env_names": {key: list(value) for key, value in names.items()},
        "note": "只读 mode=ro 打开已存在文件；不建库、不建表、不迁移、不调 owner 构造器",
    }
    if not database:
        out["detail"] = ("this instance records no Memory database (%s); the resident wires no "
                         "past source and the cycle reports PAST_SOURCE_NOT_WIRED — an optional "
                         "degradation, not 'she has no past'"
                         % (", ".join(names["database"]),))
        return out

    out["path_configured"] = True
    out["status"] = CONFIGURED
    out["reason"] = "MEMORY_HEALTH_UNVERIFIED"
    controls_path = Path(controls) if controls else Path(database)
    out["controls_database"] = str(controls_path)
    out["controls_env"] = controls_env
    out["controls_defaulted"] = controls_env is None

    #: Identity is *reported*, never equated: a Memory persona such as ``chiyo.persona`` next to a
    #: Supply workspace subject such as ``chiyo`` / ``chiyo.global`` is a legitimate, explicitly
    #: supported split (``one_day_life.prepare(memory_subject=...)`` at ``:438-441``), so it is not a
    #: refusal.  Nothing here can pull another instance's binding in either: the owner used below is
    #: the one the runtime itself resolves from *this* environment.
    #: The conversation id is a one-line contract (``life_experience_port.LIFE_CONVERSATION_PREFIX``
    #: at ``:50``).  It is verified against that module whenever the module is importable; the
    #: literal is only the fallback because the Memory boundary imports ``fcntl`` (via the M0 store)
    #: and therefore cannot be imported on Windows at all.
    conversation = MEMORY_CONVERSATION_PREFIX + owner
    try:
        _native_paths()
        from life_experience_port import life_conversation_id
    except Exception:
        pass
    else:
        conversation = str(life_conversation_id(owner))
    out["conversation"] = conversation

    try:
        if probe is not None:
            facts = dict(probe(Path(database), controls_path, owner=owner,
                               conversation=conversation))
        else:
            facts = _memory_probe(Path(database), controls_path, owner=owner,
                                  conversation=conversation)
    except Exception as exc:
        out["detail"] = ("the memory health probe did not answer (%s: %s); the binding is "
                         "recorded but its health is unverified" % (type(exc).__name__, exc))
        return out

    out["path_exists"] = facts.get("database_exists")
    out["is_file"] = facts.get("database_is_file")
    out["controls_exists"] = facts.get("controls_exists")
    out["controls_is_file"] = facts.get("controls_is_file")
    out["readable"] = facts.get("readable")
    out["integrity"] = facts.get("integrity")
    out["m0_table_present"] = facts.get("m0_table_present")
    out["life_outcomes"] = facts.get("life_outcomes")
    out["governance_healthy"] = facts.get("governance_healthy")
    out["controls"] = facts.get("controls")
    out["error"] = facts.get("error")

    def refused(reason: str, detail: str) -> dict[str, Any]:
        out.update({"status": BLOCKED, "reason": reason, "degraded": False, "blocking": True,
                    "detail": detail})
        return out

    if not facts.get("database_exists"):
        return refused("MEMORY_DB_MISSING",
                       "the configured Memory database does not exist: %s" % (database,))
    if not facts.get("database_is_file"):
        return refused("MEMORY_DB_NOT_A_FILE",
                       "the configured Memory database is not a regular file: %s" % (database,))
    if not facts.get("controls_exists"):
        return refused("MEMORY_CONTROLS_DB_MISSING",
                       "the configured control ledger does not exist: %s" % (controls,))
    if not facts.get("controls_is_file"):
        return refused("MEMORY_CONTROLS_DB_NOT_A_FILE",
                       "the configured control ledger is not a regular file: %s" % (controls,))
    error = str(facts.get("error") or "")
    if error:
        if error.startswith("GOVERNANCE_PROBE_FAILED"):
            out["detail"] = ("the control ledger could not be projected read-only (%s); the "
                             "binding is recorded but its health is unverified" % (error,))
            return out
        return refused("MEMORY_DB_UNREADABLE",
                       "the Memory database cannot be read read-only (%s)" % (error,))
    if str(facts.get("integrity") or "").lower() != "ok":
        return refused("MEMORY_DB_INTEGRITY_FAILURE",
                       "PRAGMA quick_check on the Memory database did not return ok (%r)"
                       % (facts.get("integrity"),))
    if not facts.get("m0_table_present"):
        return refused("MEMORY_DB_SCHEMA_MISMATCH",
                       "the configured database has no evidence_events table, so it is not M0")
    if facts.get("governance_healthy") is not True:
        detail = ("the control ledger projection is unhealthy, so the runtime would recall "
                  "nothing at all from this Memory")
        if out["controls_defaulted"]:
            detail += (" — no %s is recorded, so the runtime uses the M0 database itself as the "
                       "ledger and finds no memory_controls table there"
                       % (", ".join(names["controls"]),))
        return refused("MEMORY_CONTROLS_UNHEALTHY", detail)
    out.update({"status": READY, "reason": None, "degraded": False, "blocking": False,
                "recall_ready": True})
    return out

# ---------------------------------------------------------------------------------------------
# explicit opt-in: build this instance's own Memory (never on a GET, never for another instance)
# ---------------------------------------------------------------------------------------------

MEMORY_DIRNAME = "memory"
#: The pair ``run_resident.sh`` / ``run_resident_soak.sh`` already use under ``memory/``.
MEMORY_EVIDENCE_FILENAME = "evidence.db"
MEMORY_CONTROLS_FILENAME = "controls.db"
SCHEMA_ENABLE_INSTANCE_MEMORY = "phone.life.enable-instance-memory.v1"


def memory_root(root: Path | str, nested: Optional[Mapping[str, Any]] = None) -> Path:
    """``<root>/one-day/life/memory`` —— 该实例自己 install root 下的目录，不是任何全局路径。"""

    return life_install_root(root, nested) / MEMORY_DIRNAME


def _utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _backup_config(path: Path) -> Path:
    """``<name>-backup-<utc stamp>`` beside the file: a byte copy, never an overwrite."""

    import shutil

    backup = path.with_name(path.name + "-backup-" + _utc_stamp())
    if backup.exists():
        raise PhoneLifeError("CONFIG_BACKUP_COLLISION: %s" % (backup.name,))
    shutil.copy2(path, backup)
    return backup


def _write_instance_file(directory: Path, payload: Mapping[str, Any]) -> Path:
    """Replace the Life instance file atomically, keeping its own mode.  No other field changes."""

    odl = _life_modules()["odl"]
    target = odl.instance_path(directory)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    temp = target.with_name("%s.%d.tmp" % (target.name, os.getpid()))
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, mode)
    os.replace(temp, target)
    return target


def _memory_constructors() -> tuple[Callable[..., Any], Callable[..., Any], Callable[[str], str]]:
    """The genuine M0 store, ledger and conversation id —— imported only for an explicit write.

    Importing the M0 store is POSIX-only (``fcntl``); when it is unavailable this raises a named
    refusal *before* any file is written, instead of half-creating a binding.
    """

    _native_paths()
    try:
        from app.evidence import EvidenceStore
        from life_experience_port import life_conversation_id
        from memory_control import MemoryControlLedger
    except Exception as exc:
        raise PhoneLifeError("MEMORY_STORE_UNAVAILABLE: %s: %s"
                             % (type(exc).__name__, exc)) from exc
    return EvidenceStore, MemoryControlLedger, life_conversation_id


def enable_instance_memory(root: Path | str, owner: Optional[str] = None,
                           operation_key: Optional[str] = None, *,
                           store_factory: Optional[Callable[..., Any]] = None,
                           ledger_factory: Optional[Callable[..., Any]] = None,
                           conversation_fn: Optional[Callable[[str], str]] = None
                           ) -> dict[str, Any]:
    """Explicitly give **this** instance its own Memory: one M0 store plus a separate ledger.

    只在这个函数被显式调用时写盘，而且只写三处：新建的 ``<install>/memory/evidence.db``、
    独立的 ``memory/controls.db``、以及该实例 Life 环境里那三个 Memory 名字。配置先备份
    （``instance.json-backup-<utc stamp>``）。run_id / 请求预算 / 历史一律不碰，也不启动任何服务。

    四条硬边界：

    * **幂等**：已经记录了显式绑定时不新建、不覆盖、不重建；只做一次只读健康检查后如实报告。
    * **不接管半套文件**：``memory/`` 下有文件却没记录绑定 → 具名拒绝，绝不"顺手"收编。
    * **不重置 run/预算**：只写 ``NYA02_MEMORY_DB`` / ``NYA02_MEMORY_CONTROLS_DB`` /
      ``NYA02_MEMORY_SUBJECT``（resident 真正读的那三个名字），其余字段原样保留。
    * **不启动服务**：``processes_started=0``、``service_started=False``；``restart_required``
      说明正在跑的 resident 需要由操作者自己重启才会用上新绑定。

    ``store_factory`` / ``ledger_factory`` / ``conversation_fn`` 只存在于 Python API，供测试注入；
    CLI 不暴露任何注入点。
    """

    root_path = phone_root(root)
    names = memory_env_names()
    report: dict[str, Any] = {
        "schema": SCHEMA_ENABLE_INSTANCE_MEMORY,
        "instance": str(root_path),
        "ok": False,
        "status": UNKNOWN,
        "created": False,
        "reused": False,
        "processes_started": 0,
        "service_started": False,
        "restart_required": False,
        "operation_key": str(operation_key).strip() if operation_key else None,
        "owner": None,
        "binding": None,
        "backup": None,
        "written": [],
        "env_names": {key: list(value) for key, value in names.items()},
        "note": "显式一次性的实例自身 Memory 绑定；不启动服务、不改 run/预算、不碰任何其它实例",
    }

    def refused(reason: str, detail: str, status: str = BLOCKED) -> dict[str, Any]:
        report.update({"status": status, "reason": reason, "detail": detail,
                       "ok": False, "exit_code": EXIT_BLOCKED})
        return report

    try:
        nested = read_nested_life(root_path)
    except PhoneLifeError as exc:
        return refused("NESTED_LIFE_INSTANCE_INVALID", str(exc))
    if nested is None:
        return refused("NESTED_LIFE_INSTANCE_MISSING",
                       "this phone root has no one-day/instance.json; prepare the instance first "
                       "(this command creates no Life instance)")
    if not report["operation_key"]:
        return refused("MEMORY_OPERATION_KEY_REQUIRED",
                       "an explicit operation key is required for this opt-in write")
    try:
        phone: Optional[Mapping[str, Any]] = load_phone_instance(root_path)
    except PhoneLifeError:
        phone = None

    environment = dict(nested.get("environment") or {})
    existing, existing_env = _first_bound(environment, names["database"])
    existing_controls, controls_env = _first_bound(environment, names["controls"])
    existing_owner, owner_env = _first_bound(environment, names["owner"])
    requested = str(owner or "").strip()

    # -- an already explicit binding: report, never overwrite, never rebuild ------------------
    if existing:
        bound_owner = existing_owner or MEMORY_DEFAULT_OWNER
        report.update({
            "owner": bound_owner,
            "reused": True,
            "restart_required": False,
            "binding": {
                "owner": bound_owner,
                "database": existing,
                "controls_database": existing_controls or existing,
                "database_env": existing_env,
                "controls_env": controls_env,
                "owner_env": owner_env,
                "controls_defaulted": existing_controls is None,
                "explicit": True,
            },
        })
        if requested and requested != bound_owner:
            return refused("MEMORY_BINDING_OWNER_MISMATCH",
                           "this instance already records Memory owner %s; an explicit binding is "
                           "never re-pointed at another owner" % (bound_owner,))
        state = check_memory(environment, phone=phone)
        report.update({
            "status": str(state.get("status")),
            "ok": str(state.get("status")) == READY,
            "health": {key: state.get(key) for key in
                       ("status", "reason", "recall_ready", "degraded", "blocking",
                        "life_outcomes", "governance_healthy", "controls")},
            "detail": ("an explicit Memory binding is already recorded for this instance; nothing "
                       "was created, overwritten or rebuilt — a healthy one is not rebuilt and an "
                       "unhealthy one is reported, not replaced"),
            "exit_code": EXIT_OK if str(state.get("status")) == READY else EXIT_BLOCKED,
        })
        return report

    # -- a new binding for this instance only ------------------------------------------------
    target_owner = requested or MEMORY_DEFAULT_OWNER
    if not target_owner:
        return refused("MEMORY_OWNER_REQUIRED", "an explicit Memory owner is required")
    directory = memory_root(root_path, nested)
    evidence = directory / MEMORY_EVIDENCE_FILENAME
    controls = directory / MEMORY_CONTROLS_FILENAME
    if evidence.exists() or controls.exists():
        return refused("MEMORY_FILES_EXIST_UNBOUND",
                       "files already exist under %s but this instance records no Memory binding; "
                       "refusing to adopt or overwrite them" % (directory,))
    nested_path = nested_life_dir(root_path)
    try:
        odl = _life_modules()["odl"]
        instance_file = odl.instance_path(nested_path)
    except Exception as exc:
        return refused("NESTED_LIFE_INSTANCE_INVALID", "%s: %s" % (type(exc).__name__, exc))
    try:
        if store_factory is None or ledger_factory is None or conversation_fn is None:
            genuine_store, genuine_ledger, genuine_conversation = _memory_constructors()
            store_factory = store_factory or genuine_store
            ledger_factory = ledger_factory or genuine_ledger
            conversation_fn = conversation_fn or genuine_conversation
    except PhoneLifeError as exc:
        return refused("MEMORY_STORE_UNAVAILABLE", str(exc), status=UNAVAILABLE)
    try:
        backup = _backup_config(instance_file)
    except (OSError, PhoneLifeError) as exc:
        return refused("CONFIG_BACKUP_FAILED", "%s: %s" % (type(exc).__name__, exc))
    report["backup"] = str(backup)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        store_factory(evidence)
        ledger_factory(controls, evidence, owner=target_owner,
                       conversation=str(conversation_fn(target_owner))).initialize()
    except Exception as exc:
        report["written"] = [str(path) for path in (evidence, controls) if path.exists()]
        return refused("MEMORY_CREATE_FAILED", "%s: %s" % (type(exc).__name__, exc),
                       status=UNAVAILABLE)

    updated = dict(environment)
    updated[names["database"][0]] = str(evidence)
    updated[names["controls"][0]] = str(controls)
    updated[names["owner"][0]] = target_owner
    payload = dict(nested)
    payload["environment"] = updated
    try:
        written_file = _write_instance_file(nested_path, payload)
    except OSError as exc:
        report["written"] = [str(evidence), str(controls)]
        return refused("CONFIG_WRITE_FAILED", "%s: %s" % (type(exc).__name__, exc))
    report["written"] = [str(evidence), str(controls), str(written_file)]

    state = check_memory(updated, phone=phone)
    report.update({
        "owner": target_owner,
        "created": True,
        "status": str(state.get("status")),
        "ok": str(state.get("status")) == READY,
        "restart_required": True,
        "binding": {
            "owner": target_owner,
            "database": str(evidence),
            "controls_database": str(controls),
            "database_env": names["database"][0],
            "controls_env": names["controls"][0],
            "owner_env": names["owner"][0],
            "controls_defaulted": False,
            "explicit": True,
        },
        "health": {key: state.get(key) for key in
                   ("status", "reason", "recall_ready", "degraded", "blocking",
                    "life_outcomes", "governance_healthy", "controls")},
        "exit_code": EXIT_OK if str(state.get("status")) == READY else EXIT_BLOCKED,
    })
    if not report["ok"]:
        report["reason"] = str(state.get("reason") or "MEMORY_BINDING_UNVERIFIED")
        report["detail"] = ("the binding was written and backed up, but its read-only health check "
                            "did not reach READY (%s); the files are left in place and nothing is "
                            "rebuilt" % (report["reason"],))
    else:
        report["detail"] = ("this instance's own Memory was created and bound; the running "
                            "resident picks it up on its next start (no service was started here)")
    return report


# ---------------------------------------------------------------------------------------------
# preflight: pure read, zero write, zero model, no connect
# ---------------------------------------------------------------------------------------------


def life_preflight(root: Path | str, *, environment: Optional[Mapping[str, str]] = None
                   ) -> dict[str, Any]:
    """Read-only readiness of the subject chain on this phone instance.

    Nothing is created, started, chmod-ed or connected: the supply/World rows are *file* facts
    (``present`` / ``is_socket`` / live generation), the model row is *configuration*, and the
    lease row is a read-only ``flock`` probe.  "Assemblable" is therefore reported separately
    from "a model was called" — the latter is always ``false`` here.
    """

    root_path = phone_root(root)
    report: dict[str, Any] = {
        "schema": SCHEMA_PREFLIGHT,
        "instance": str(root_path),
        "read_only": True,
        "model_called": False,
        "socket_created": False,
        "processes_started": 0,
        "ok": False,
    }
    checks: dict[str, Any] = {}
    try:
        phone = load_phone_instance(root_path)
    except PhoneLifeError as exc:
        report.update({"ok": False, "status": "BLOCKED", "reason": str(exc),
                       "exit_code": EXIT_REFUSED})
        return report

    report["subject"] = phone.get("subject")
    try:
        nested = read_nested_life(root_path)
    except PhoneLifeError as exc:
        nested = None
        checks["nested_life"] = {"status": BLOCKED, "detail": str(exc)}
    else:
        checks["nested_life"] = {
            "status": READY if nested else UNAVAILABLE,
            "path": str(nested_life_dir(root_path)),
            "life_state_root": str(life_state_root(root_path, nested)),
            "life_install_root": str(life_install_root(root_path, nested)),
            "detail": None if nested else "NESTED_LIFE_INSTANCE_MISSING",
        }

    binding = build_binding(phone, nested, base_env=environment if environment is not None else None)
    env = binding["environment"]
    report["binding"] = {
        "sources": binding["sources"],
        "overridden_by_live_binding": binding["overridden"],
        "cross_binding_conflicts": binding["conflicts"],
        "missing": binding["missing"],
        "unknown": binding["unknown"],
        "run_id": env.get("CHIYO_AUTONOMOUS_RUN_ID"),
        "new_run_window_requested": False,
    }
    memory_names = memory_env_names()
    report["bindings"] = {
        name: {"bound": bool(env.get(name)), "source": binding["sources"].get(name, "absent")}
        for name in (("LIFE_SUPPLY_SOCKET", "NYA02_SUBJECT", "NYA02_ARTIFACT_GRANT",
                      "CHIYO_LIFE_SUPPLY_WORKSPACE_ID", "CHIYO_LIFE_WORK_ARTIFACT_ID",
                      "CHIYO_LIFE_READING_ARTIFACT_ID", "CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                      "CHIYO_WORLD_HOME", "WORLD_BODY_SOCKET", "CHIYO_LIFE_INSTALL_ROOT",
                      "NYA02_RESIDENT_STATE", "CHIYO_AUTONOMOUS_RUN_ID")
                     + memory_names["database"] + memory_names["controls"]
                     + memory_names["owner"])
    }

    checks["supply"] = check_supply_binding(phone, environment=env)
    checks["world"] = check_world_binding(phone)
    checks["permissions"] = check_permissions(root_path, phone)
    checks["lease"] = check_single_lease(life_state_root(root_path, nested))
    checks["switch"] = check_switch(root_path, nested)
    checks["model"] = check_model(env)
    checks["privacy"] = check_privacy(env)
    checks["memory"] = check_memory(env, phone=phone)

    blocking = [name for name, row in checks.items()
                if str(row.get("status")) == BLOCKED]
    if binding["missing"]:
        blocking.append("binding.missing")
    report["checks"] = checks
    report["blocked"] = blocking
    #: Memory 的参与状态单独平铺一份：``UNBOUND`` 是有意的可选降级（不进 ``blocked``、不挡 run），
    #: ``CONFIGURED`` 只说"路径记录在案"（健康/召回仍未证实），``READY`` 才代表只读检查真的通过。
    report["memory"] = {
        name: checks["memory"].get(name)
        for name in ("status", "reason", "detail", "optional", "degraded", "blocking",
                     "path_configured", "recall_ready", "database", "controls_database",
                     "controls_defaulted", "owner", "phone_subject", "conversation", "life_outcomes",
                     "governance_healthy", "error")
    }
    report["memory"]["participation"] = MEMORY_PARTICIPATION.get(
        str(checks["memory"].get("status")), "UNRESOLVED")
    report["assembly"] = {
        "feasible": not blocking,
        "detail": ("装配可行只表示现有 runtime 能在这个绑定上装起来；"
                   "不代表模型被调用、也不代表她做过任何事"),
        "model_configured": bool(checks["model"].get("configured")),
        "switch_on": bool(checks["switch"].get("recorded") is True),
        "single_writer_lease": checks["lease"].get("status"),
        "supply_socket_present": bool(checks["supply"].get("present")),
        "request_ceiling_set": "request_ceiling" not in binding["unknown"],
        "memory_status": checks["memory"].get("status"),
        "memory_bound": bool(checks["memory"].get("path_configured")),
        "memory_recall_ready": bool(checks["memory"].get("recall_ready")),
        "memory_degraded": bool(checks["memory"].get("degraded")),
        "memory_optional": True,
    }
    #: ``feasible`` 是"能装起来"；``run_ready`` 才是"现在真能跑一步"（socket 在、开关在、预算设了）。
    #: 两者分开写，避免把"装配成功"读成"她开始过日子了"。Memory 未绑定**不进**这里：记忆是可选项，
    #: 不能借机变成所有生活的前置硬门。
    report["run_ready"] = bool(report["assembly"]["feasible"]
                               and report["assembly"]["supply_socket_present"]
                               and report["assembly"]["switch_on"]
                               and report["assembly"]["request_ceiling_set"])
    report["verified_claims"] = {
        "read_only": ["phone instance file", "nested Life instance file", "modes/uid",
                      "socket paths (lstat, no connect)", "lease (read-only flock)",
                      "autonomous switch", "provider configuration",
                      "memory databases (sqlite mode=ro, quick_check, sqlite_master)"],
        "not_performed": ["model request", "socket connect", "socket create", "process start",
                          "any file write", "run window open",
                          "memory table creation or migration", "memory owner construction"],
        "verified_model_response": False,
    }
    report["ok"] = bool(report["assembly"]["feasible"])
    report["status"] = READY if report["ok"] else BLOCKED
    report["exit_code"] = EXIT_OK if report["ok"] else EXIT_REFUSED
    return report


# ---------------------------------------------------------------------------------------------
# run: the existing resident, finitely, never with a fabricated provider
# ---------------------------------------------------------------------------------------------


def life_run(root: Path | str, *, once: bool = False,
             max_ticks: Optional[int] = None,
             duration_seconds: int = DEFAULT_RUN_DURATION_SECONDS,
             poll_seconds: Optional[int] = None,
             act: bool = True,
             environment: Optional[Mapping[str, str]] = None,
             resident_factory: Optional[Callable[..., Any]] = None,
             verify_owners: bool = True) -> dict[str, Any]:
    """Run the **existing** resident once (or finitely) on this phone instance's live binding.

    ``resident_factory`` and ``verify_owners`` exist only so a test can prove *what* was bound
    without a real Supply owner; the CLI never passes them, so no caller can swap in a factory, a
    cognition adapter or a writer payload.  Everything else is the shipped chain: the same
    ``AutonomousLifeResident``, the same state root, the same lease, the same run window.

    Refusals are honest and side-effect free: no provider configuration, the operator's switch
    off, no request ceiling, no Supply owner, a stale binding or a lease already held all return
    ``ok: False`` with a reason code and an exit code, *before* anything is started.
    """

    root_path = phone_root(root)
    report: dict[str, Any] = {
        "schema": SCHEMA_RUN,
        "instance": str(root_path),
        "once": bool(once),
        "max_ticks": max_ticks,
        "duration_seconds": int(duration_seconds),
        "act": bool(act),
        "injected_factory": resident_factory is not None,
        "owners_verified": bool(verify_owners),
        "model_called": False,
        "processes_started": 0,
        "exit_code": EXIT_BLOCKED,
        "ok": False,
    }

    try:
        phone = load_phone_instance(root_path)
    except PhoneLifeError as exc:
        report.update({"reason": str(exc), "exit_code": EXIT_REFUSED})
        return report
    try:
        nested = read_nested_life(root_path)
    except PhoneLifeError as exc:
        report.update({"reason": str(exc), "exit_code": EXIT_REFUSED})
        return report
    if nested is None:
        report["reason"] = "NESTED_LIFE_INSTANCE_MISSING"
        report["detail"] = ("this phone root has no %s/instance.json; the Life root, switch and "
                            "run window live there" % ONE_DAY_DIRNAME)
        return report

    binding = build_binding(phone, nested, base_env=environment)
    env = binding["environment"]
    state_root = life_state_root(root_path, nested)
    report["state_root"] = str(state_root)
    report["run_id"] = env.get("CHIYO_AUTONOMOUS_RUN_ID")
    report["binding"] = {"overridden_by_live_binding": binding["overridden"],
                         "cross_binding_conflicts": binding["conflicts"],
                         "missing": binding["missing"], "unknown": binding["unknown"]}
    if binding["conflicts"]:
        report["reason"] = "CROSS_BINDING_CONFLICT"
        report["detail"] = "the nested instance and the phone instance disagree on %s" % (
            ", ".join(binding["conflicts"]),)
        return report

    permissions = check_permissions(root_path, phone)
    report["permissions"] = permissions
    if permissions.get("uid_matches") is False:
        report["reason"] = "LOCAL_OWNER_UID_MISMATCH"
        report["detail"] = "this process is not the instance's recorded local owner"
        report["exit_code"] = EXIT_REFUSED
        return report

    supply = check_supply_binding(phone, environment=env)
    report["supply"] = supply
    if str(supply.get("status")) == BLOCKED:
        report["reason"] = str(supply.get("detail") or "SUPPLY_BINDING_UNUSABLE")
        return report

    model = check_model(env)
    report["model"] = model
    if not model.get("configured"):
        report["reason"] = "NO_MODEL_PROVIDER"
        report["detail"] = ("this instance has no usable provider configuration; a run would "
                            "either fabricate a writer or do nothing, so nothing was started")
        return report

    memory = check_memory(env, phone=phone)
    report["memory"] = {
        name: memory.get(name)
        for name in ("status", "reason", "detail", "optional", "degraded", "blocking",
                     "path_configured", "recall_ready", "database", "controls_database",
                     "controls_defaulted", "owner", "phone_subject", "conversation", "life_outcomes",
                     "governance_healthy", "error")
    }
    report["memory"]["participation"] = MEMORY_PARTICIPATION.get(
        str(memory.get("status")), "UNRESOLVED")
    memory_status = str(memory.get("status"))
    if memory_status == BLOCKED:
        #: Explicitly recorded but unusable: a finite run must not silently pretend the past is
        #: simply empty.  ``UNBOUND`` never reaches this branch — an unbound instance stays a
        #: reported degradation, not a hard gate.
        report["reason"] = "MEMORY_BINDING_BROKEN"
        report["detail"] = ("a Memory database is explicitly recorded for this instance but cannot "
                            "be used (%s); running without the past would be a silent half-run"
                            % (memory.get("reason") or "UNKNOWN",))
        return report
    if memory_status in (CONFIGURED, UNKNOWN):
        #: Explicitly bound but health not established is **not** the same as unbound.  Fail closed
        #: before a resident exists: an unverified past must not be read as "no past".
        report["reason"] = "MEMORY_HEALTH_UNVERIFIED"
        report["detail"] = ("a Memory database is explicitly recorded but its backend health could "
                            "not be established (%s); a finite run is not started on an unverified "
                            "past" % (memory.get("detail") or memory.get("reason") or "UNKNOWN",))
        return report

    switch = check_switch(root_path, nested)
    report["switch"] = switch
    if switch.get("recorded") is not True:
        report["reason"] = "AUTONOMOUS_SWITCH_OFF"
        report["detail"] = ("the operator's own persisted switch is not on; this command never "
                            "turns autonomy on by itself")
        return report

    if _int_or_none(env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")) is None \
            and _int_or_none(env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")) is None:
        report["reason"] = "REQUEST_CEILING_UNSET"
        report["detail"] = "no explicit request ceiling is configured for this instance"
        return report

    lease = check_single_lease(state_root)
    report["lease"] = lease
    if lease.get("status") == "HELD":
        report["reason"] = "LEASE_HELD"
        report["detail"] = "another live instance already holds this state root's writer lease"
        report["exit_code"] = EXIT_LEASE_HELD
        return report

    if verify_owners:
        ready = False
        try:
            ready = bool(phone_service.supply_ready(str(phone.get("supply_socket") or "")))
        except Exception as exc:  # a probe failure is "not ready", never a pass
            report["supply"]["probe_detail"] = "%s: %s" % (type(exc).__name__, exc)
        report["supply"]["live_health"] = "OK" if ready else "UNREACHABLE"
        if not ready:
            report["reason"] = "SUPPLY_OWNER_UNREACHABLE"
            report["detail"] = ("the phone instance's Supply socket does not answer; start the "
                                "phone's own serve first (this command starts no service)")
            return report

    resident_module = _life_modules()["resident"]
    factory = resident_factory or resident_module.AutonomousLifeResident
    kwargs: dict[str, Any] = {
        "state_root": state_root,
        "environment": env,
        "poll_seconds": int(poll_seconds if poll_seconds is not None
                            else resident_module.DEFAULT_POLL_SECONDS),
        "act": bool(act),
    }
    try:
        resident = factory(**kwargs)
    except Exception as exc:
        report["reason"] = "RESIDENT_REFUSED"
        report["detail"] = "%s: %s" % (type(exc).__name__, exc)
        return report
    report["resident"] = {"factory": getattr(factory, "__name__", type(factory).__name__),
                          "state_root": str(state_root),
                          "poll_seconds": kwargs["poll_seconds"]}

    started = resident.start()
    report["started"] = started
    if not isinstance(started, Mapping) or not started.get("ok"):
        detail = str((started or {}).get("detail") or "") if isinstance(started, Mapping) else ""
        reason = str((started or {}).get("reason") or "NOT_STARTED") \
            if isinstance(started, Mapping) else "NOT_STARTED"
        held = any(word in detail.lower() for word in ("lock", "lease", "held"))
        report.update({"reason": reason, "detail": detail,
                       "exit_code": EXIT_LEASE_HELD if held else EXIT_BLOCKED})
        return report

    try:
        if once:
            ran: Any = {"ticks": getattr(resident, "ticks", None), "once": True,
                        "tick": resident.tick()}
        else:
            ran = resident.run(max_ticks=max_ticks, duration_seconds=int(duration_seconds))
    except Exception as exc:
        report["reason"] = "RUN_FAILED"
        report["detail"] = "%s: %s" % (type(exc).__name__, exc)
        ran = None
    finally:
        stopped = resident.stop()

    counters = dict(getattr(resident, "counters", {}) or {})
    report.update({
        "ran": ran,
        "stopped": stopped,
        "counters": counters,
        "model_calls": int(counters.get("model_calls") or 0),
        "model_called": bool(int(counters.get("model_calls") or 0) > 0),
        "ok": "reason" not in report,
        "exit_code": EXIT_OK if "reason" not in report else EXIT_BLOCKED,
    })
    participation = str((report.get("memory") or {}).get("participation") or "UNRESOLVED")
    report["note"] = ("同一个 resident、同一个 state root、同一把 lease；"
                      "ACTED 只表示 owner 确认过一次真实写入，NO_ACTION/DEFER 就是这一次没动作；"
                      "Memory participation=%s" % (participation,))
    return report


__all__ = [
    "BRIDGE_DIRNAME",
    "DEFAULT_RUN_DURATION_SECONDS",
    "EXIT_BLOCKED",
    "EXIT_LEASE_HELD",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "ONE_DAY_DIRNAME",
    "PhoneLifeError",
    "SCHEMA_ENABLE_INSTANCE_MEMORY",
    "SCHEMA_PREFLIGHT",
    "SCHEMA_RUN",
    "STATE_ROOT_MODE",
    "build_binding",
    "check_memory",
    "check_model",
    "check_permissions",
    "check_privacy",
    "check_single_lease",
    "check_supply_binding",
    "check_switch",
    "check_world_binding",
    "enable_instance_memory",
    "life_install_root",
    "life_preflight",
    "life_run",
    "life_state_root",
    "load_phone_instance",
    "map_life_environment",
    "memory_env_names",
    "memory_root",
    "nested_life_dir",
    "phone_root",
    "read_nested_life",
    "serve_marker",
]
