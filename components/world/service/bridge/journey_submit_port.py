#!/usr/bin/env python3
"""旅程单段的正式提交口：每一次 MOVE 都经 World 自己的授权链。

授权链不变（本模块不新增授权，也不写世界状态）：

    Intent Executor   铸造一次性授权、读 fresh snapshot、过 mutation gate
    gateway.sock      消费授权；调用方自带的 authority 一律拒绝
    Resolver          世界位置的唯一提交者

为什么放在 World 侧
-------------------
``chiyo_bundle.world_journey`` 与 ``world_runner`` / ``world_move_tool`` 都是
World 自己的推进器，不能反向依赖 Life 的 ``world_journey_action_adapter``。
这个模块只做**组装**：构造一段 MOVE 的 intent，交给现成的 Intent Executor，
再把结果翻译成 ``world_living_skeleton.apply_move_to`` 的 owner-status 形状。
它不铸造授权、不缓存 action space、不判断授权是否有效——那些仍然只由
``intent_executor`` / ``world_action_authorization`` / gateway 决定。

一段旅程的每一段都是新的 ``execution_id``，所以每一段都必须各自走这条链；
首段的票不能复用（gateway 会以 consumed 拒绝），也从不跨段共享。
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import os
import sys
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

#: ``intent_executor`` 目前只从**进程环境**读取 ``WORLD_ACTION_AUTH_REGISTRY_DIR``
#: （``execute_intent`` 没有环境参数），所以定位键只能在调用窗口内临时补。
#: 这把模块级锁把本端口自己的窗口串行化，避免同进程两个提交线程互相看到对方的
#: 临时值。剩余风险：窗口内如果有别的线程自行读取该变量（或直接调用
#: ``execute_intent``），仍会看到临时值；gateway 在独立进程，不受影响。没有更窄的
#: 现成参数通道可用，因此不改协议，只收窄这个窗口。
_REGISTRY_ENV_LOCK = threading.Lock()


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _authority_registry_dir(environment: Mapping[str, str]) -> Optional[Path]:
    """这次提交的授权必须落在**同一个 gateway 消费的那本 registry** 上。

    Intent Executor 读进程环境的 ``WORLD_ACTION_AUTH_REGISTRY_DIR`` /
    ``CHIYO_WORLD_HOME`` 来决定 issue 到哪里；CLI 用 ``--world-home`` 装配的
    请求环境未必等于进程环境。端口在调用窗口内只补这一个定位键，让
    "签发" 与 "消费" 指向同一目录——授权规则、TTL、门禁都不改。
    """

    if str(environment.get("WORLD_ACTION_AUTH_REGISTRY_DIR") or "").strip():
        return None
    socket_value = str(environment.get("WORLD_BODY_SOCKET") or "").strip()
    if socket_value:
        return Path(socket_value).parent
    home = str(environment.get("CHIYO_WORLD_HOME")
               or environment.get("HERMES_HOME") or "").strip()
    return (Path(home) / "run") if home else None


class _BridgeResolver:
    """本目录内自足的 World 桥：只加载现成三件套，组装一段 MOVE intent。"""

    def __init__(self, *, environment: Optional[Mapping[str, str]] = None,
                 bridge_dir: Optional[Path | str] = None) -> None:
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ)
        self._bridge_dir = Path(bridge_dir) if bridge_dir else None
        self._modules: Optional[tuple[Any, Any, Any]] = None
        self._client: Any = None

    def _bridge(self) -> tuple[Any, Any, Any]:
        if self._modules is None:
            directory = self._bridge_dir or Path(__file__).resolve().parent
            text = str(directory)
            if text not in sys.path:
                sys.path.insert(0, text)
            import action_intent as world_action_intent  # noqa: E402
            import intent_executor as world_intent_executor  # noqa: E402
            import world_body_client as world_body_client  # noqa: E402

            self._modules = (world_action_intent, world_intent_executor,
                             world_body_client)
        return self._modules

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._bridge()[2].WorldBodyClient(environment=self.environment)
        return self._client

    def build_intent(self, *, execution_id: str, verb: str, target: str,
                     decision_ref: Optional[str] = None,
                     created_at: Optional[str] = None) -> dict[str, Any]:
        """两参数以内的 MOVE intent；参数映射来自 Resolver 自己的契约。"""

        ai = self._bridge()[0]
        if verb != "MOVE":
            raise ValueError("journey submit port only expresses MOVE, got %r" % verb)
        moment = created_at or _iso_now()
        intent: dict[str, Any] = {
            "kind": ai.KIND_INTENT,
            "schema_version": ai.SCHEMA_INTENT,
            "intent_id": "world-intent:" + _digest("%s:%s:%s" % (execution_id, verb, target))[:32],
            "decision_ref": decision_ref,
            "actor_id": "chiyo",
            "action": "SELECT",
            "verb": verb,
            "params": {"destination_location_id": target},
            "target_summary": "%s -> %s" % (verb, target),
            "created_at": moment,
            "source_provenance": "ar0_world_bridge_adapter",
            "authorization_ref": "pending",
            "execution_id": execution_id,
            "intent_status": ai.STATUS_AUTHORIZED,
            # 真正的依赖证据由 authority 侧在提交时重读；这里只命名 intent。
            "snapshot_fingerprint": _digest("ar0:%s" % execution_id)[:32],
            "origin": "life_ar0",
            "intent_summary": "AR-0 %s to %s" % (verb, target),
        }
        return intent

    def describe(self) -> dict[str, Any]:
        return {
            "bridge_dir": str(self._bridge_dir or Path(__file__).resolve().parent),
            "integration_mode": self.environment.get("WORLD_BODY_INTEGRATION_MODE"),
            "socket": self.environment.get("WORLD_BODY_SOCKET"),
            "intent_gate": self.environment.get("WORLD_BODY_INTENT_MUTATION_GATE"),
        }


class FormalOwnerMoveSubmitPort:
    """提交旅程的一段（一次 MOVE），并翻译回 owner-status 形状。"""

    def __init__(self, *, environment: Optional[Mapping[str, str]] = None,
                 bridge_dir: Optional[Path | str] = None, bridge: Any = None) -> None:
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ)
        self._bridge_dir = bridge_dir
        self._bridge = bridge
        self.last_intent: Optional[dict[str, Any]] = None
        self.last_result: Optional[dict[str, Any]] = None
        #: 真正送出的提交数。executor 在联系 gateway 之前拒绝的（gate off / shadow /
        #: invalid）不计入——这个区别正是"没铸票"的证据。
        self.submissions = 0

    def _resolver(self) -> Any:
        if self._bridge is None:
            self._bridge = _BridgeResolver(environment=self.environment,
                                           bridge_dir=self._bridge_dir)
        return self._bridge

    def describe(self) -> dict[str, Any]:
        resolver = self._resolver()
        info = dict(resolver.describe()) if hasattr(resolver, "describe") else {}
        info.update({"kind": "formal_owner_move_submit_port",
                     "mints_authority": False,
                     "writes_world_state": False,
                     "uses_expected_dependencies": False,
                     "dependency_evidence_source": "authority_side_fresh_read"})
        return info

    def submit_move(self, *, destination: str, origin: str, execution_id: str,
                    hermes_home: Any, environment: Mapping[str, str],
                    now: Any = None) -> dict[str, Any]:
        bridge = self._resolver()
        ai, executor, _wbc = bridge._bridge()
        try:
            intent = bridge.build_intent(execution_id=str(execution_id), verb="MOVE",
                                         target=str(destination))
        except Exception as exc:  # intent 形状都构造不出来：还没碰 owner
            return {"kind": "MOVE_TO", "status": "unavailable", "formal": True,
                    "reason_code": "INTENT_BUILD_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        self.last_intent = copy.deepcopy(intent)
        registry_dir = _authority_registry_dir(environment)
        # 只在本调用窗口内补定位键；锁保证同进程的提交不会交错看到对方的临时值。
        # 见模块顶部 ``_REGISTRY_ENV_LOCK`` 的剩余风险说明。
        with _REGISTRY_ENV_LOCK:
            previous_registry = os.environ.get("WORLD_ACTION_AUTH_REGISTRY_DIR")
            if registry_dir is not None:
                os.environ["WORLD_ACTION_AUTH_REGISTRY_DIR"] = str(registry_dir)
            try:
                result = dict(executor.execute_intent(intent, client=bridge.client) or {})
            except Exception as exc:  # executor 根本联系不上：可能越过也未必
                return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                        "reason_code": "INTENT_SUBMIT_UNAVAILABLE",
                        "detail": "%s: %s" % (type(exc).__name__, exc)}
            finally:
                if registry_dir is not None:
                    if previous_registry is None:
                        os.environ.pop("WORLD_ACTION_AUTH_REGISTRY_DIR", None)
                    else:
                        os.environ["WORLD_ACTION_AUTH_REGISTRY_DIR"] = previous_registry
        self.last_result = copy.deepcopy(result)
        mapped = self._as_owner_status(ai, result)
        if str(mapped.get("reason_code") or "") not in {"GATE_OFF", "SHADOW_NO_COMMIT",
                                                        "INVALID"}:
            self.submissions += 1
        return mapped

    @staticmethod
    def _as_owner_status(ai: Any, result: Mapping[str, Any]) -> dict[str, Any]:
        status = str(result.get("intent_status") or "")
        outcome = str(result.get("outcome") or "")
        detail = str(result.get("detail") or "")[:200]
        if status == ai.STATUS_APPLIED:
            return {"kind": "MOVE_TO", "status": "applied", "formal": True,
                    "reason_code": outcome or None, "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_STALE:
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": "STALE_STATE", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_REJECTED:
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": outcome or "INTENT_REJECTED", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        if status == ai.STATUS_PROPOSED:
            return {"kind": "MOVE_TO", "status": "rejected", "formal": True,
                    "reason_code": "SHADOW_NO_COMMIT", "detail": detail,
                    "intent_status": status, "execution_id": result.get("execution_id")}
        return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                "reason_code": outcome or "INTENT_UNKNOWN", "detail": detail,
                "intent_status": status, "execution_id": result.get("execution_id")}


def default_submit_port(*, environment: Optional[Mapping[str, str]] = None,
                        bridge_dir: Optional[Path | str] = None) -> "FormalOwnerMoveSubmitPort":
    """构造默认端口。环境显式给出 ``NYAIRO_WORLD_BRIDGE_DIR`` 时只认它。"""

    env = dict(environment if environment is not None else os.environ)
    explicit = str(env.get("NYAIRO_WORLD_BRIDGE_DIR") or "").strip()
    directory = Path(explicit) if explicit else bridge_dir
    if directory is not None and not Path(directory).is_dir():
        raise FileNotFoundError("world bridge directory not found: %s" % directory)
    return FormalOwnerMoveSubmitPort(environment=env, bridge_dir=directory)


__all__ = ["FormalOwnerMoveSubmitPort", "default_submit_port"]
