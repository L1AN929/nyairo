#!/usr/bin/env python3
"""暂停作品的正式回来链：条件 PAUSE -> owner 证明 -> OPEN 资格 -> 新 RESUME -> 同作续接。

这份文件补的是「整链证据」，不是新的产品功能：

* 条件 PAUSE 用的 basis 是正式 WAIT 已经提供的同一个 owner 依赖（作品 head / 材料修订）；
  脚本 Decision 只把本轮 ``situation_context.pause_condition.condition_ref`` 原样抄进
  ``reevaluation_hint_ref``，AG-1 的 validator 照常校验，AG-2 照常采纳，LR-4 在同一
  store 里绑定。
* 条件满足由真实 Supply owner 真正提交新 head 触发；``AutonomousLifeRunner.maintain``
  只把 owner 读到的变化交给 LR-4，LR-4 是唯一写 SATISFIED / 发 OPEN 资格的 owner。
* 候选供给来自正式 ``CandidateMaterializer``；回来仍是一轮新鲜 Decision 自己选 RESUME，
  同一 Activity 恢复后再由下一步正常的 CONTINUE 写出版本。
* P1 在独立 OS 进程里做完 START / 合法 no_change / 条件 PAUSE 后正规关闭；P2 是本测试
  进程，另一个 PID 在同一 scratch 根上重新装配。两个 PID 各自装既有离线硬禁网守卫并自检。

替身只有 Decision 回答与内容回答（脚本 transport，明示），真实件是 Supply owner、
ProductionLifeRuntime、AG-0/AG-1/AG-2/LR-2/LR-4、ModelContinuationWriter 与
AgencyModelClient。不声称真实模型会自然采用条件 PAUSE，也不对心理动机作任何断言。

四个聚合 case：

1. 双 PID 的正式整链：真 START 写 A -> 合法 no_change -> 条件 PAUSE -> 重启后条件未满足
   不供 RESUME -> owner 真改 head -> runner maintain 发 OPEN -> 新鲜 RESUME 回同一 Activity
   -> 正常 CONTINUE 写出新版本。
2. 负控：无条件 PAUSE 不生成条件/资格；Decision 点名一个本轮未提供（或不在 allow-list）
   的 hint 被正式 validator 拒绝且 Activity 不变；owner 不可读时不发伪 OPEN。
3. 有另一件 B 占执行槽：候选/合法集拒绝后台 RESUME，不抢占 B；正式暂停 B 后也不会自动
   回 A，新鲜 NO_ACTION 零写仍合法，再新鲜 RESUME 才消费 A 的资格。
4. 回到同 A 后的合法 no_change：owner 零新提交、Decision 身份保留；重复条件事件与同一
   恢复操作按既有幂等不多消费资格、不产生重复动作。

Run (POSIX/WSL)::

    python -B -m unittest tests.test_paused_work_resume_chain
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "real Supply socket needs POSIX (WSL/Linux)")

#: 每轮 400 分钟：越过既有 6 小时静默 TTL；这是夹具时钟，不是自然日。
BASE_MOMENT = datetime.now(timezone.utc).replace(microsecond=0)
STEP = 400

#: 合成哨兵：只用于确认 owner 版本真的存在，不是任何真实私文。
START_TEXT = "PRC-START-7f3a 由脚本内容替身写下的第一段。"
CONTINUE_TEXT = "PRC-CONTINUE-2c91 回来后正常续写的一段。"
OWNER_TEXT = "PRC-OWNER-b45e owner 自己提交的新版本。"
NO_CHANGE_NOTE = "PRC-NOCHANGE-9d10 只属于脚本回答，不该落盘。"

#: 安全报告只允许这些固定枚举/布尔/计数/ID。
SAFE_WAKE_KEYS = ("outcome", "decision", "adoption_status", "activity_ref",
                  "selected_candidate_ref", "detail")
SAFE_CANDIDATE_KEYS = ("candidate_id", "candidate_kind", "source_kind", "activity_ref",
                       "resume_eligibility_ref", "target_refs", "legal_decisions")


def moment(minutes: int) -> datetime:
    return BASE_MOMENT + timedelta(minutes=minutes)


def stamp(minutes: int) -> str:
    return moment(minutes).isoformat()


def bare(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def work_ref(value: Any) -> str:
    text = bare(value)
    return "artifact:%s" % text if text else ""


def ensure_bound_work(chain: Any, artifact_id: str) -> None:
    """The work binding must come from the instance environment, not from this test."""

    actual = bare(getattr(chain.cycle, "work_artifact_id", ""))
    if actual != bare(artifact_id):
        raise AssertionError("WORK_BINDING_MISMATCH")


def safe_wake(wake: Any) -> dict[str, Any]:
    row = getattr(wake, "to_dict", None)
    data = dict(row() if callable(row) else (wake or {}))
    out = {key: data.get(key) for key in SAFE_WAKE_KEYS if key in data}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    for key in ("decision_id", "decision_route", "selected_candidate_ref",
                "owner_version_created", "owner_version_id", "work_no_change",
                "reason_codes"):
        if key in extra:
            out[key] = extra[key]
    return out


def safe_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {key: row.get(key) for key in SAFE_CANDIDATE_KEYS if key in row}
    refs = [str(ref) for ref in (row.get("target_refs") or [])]
    out["target_refs"] = refs[:6]
    legal = [str(x) for x in (row.get("legal_decisions") or [])]
    out["legal_decisions"] = legal
    return out


# ---------------------------------------------------------------------------------------------
# 脚本 transport：真实 AgencyModelClient 会把它当成 provider 路径；从不打开 socket
# ---------------------------------------------------------------------------------------------


class DecisionTransport:
    """按脚本策略回答 Decision；只记录本轮请求的安全结构。"""

    backend = "scripted-decision-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.policy = "no_action"
        self.seed_artifact = ""
        self.target_artifact = ""
        self.calls = 0
        self.last_offered: dict[str, Any] = {}

    def _select(self, payload: Mapping[str, Any], policy: str):
        candidates = [dict(row) for row in (payload.get("candidates") or [])
                      if isinstance(row, Mapping)]
        allowed = [str(code) for code in (payload.get("allowed_reason_codes") or [])]
        reason = [allowed[0]] if allowed else ["NO_ACTION_CHOSEN"]
        seed = bare(self.target_artifact or self.seed_artifact)

        def has(row: Mapping[str, Any], word: str) -> bool:
            return word in [str(x) for x in (row.get("legal_decisions") or [])]

        def is_closeout(row: Mapping[str, Any]) -> bool:
            return any(str(ref).startswith("closeout:")
                       for ref in (row.get("target_refs") or []))

        def about_seed(row: Mapping[str, Any]) -> bool:
            return any(bare(ref) == seed for ref in (row.get("target_refs") or []))

        if policy == "start":
            rows = [row for row in candidates
                    if has(row, "START") and about_seed(row) and not is_closeout(row)]
            if len(rows) == 1:
                return {"decision": "START", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "script: start the work",
                        "deferred_candidate_ids": []}
        if policy == "continue":
            rows = [row for row in candidates
                    if has(row, "CONTINUE") and about_seed(row) and not is_closeout(row)]
            if len(rows) == 1:
                return {"decision": "CONTINUE", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "script: continue the work",
                        "deferred_candidate_ids": []}
        if policy in {"pause_condition", "pause_bad_hint"}:
            pause = (payload.get("situation_context") or {}).get("pause_condition") or {}
            rows = [row for row in candidates if has(row, "PAUSE")]
            if rows:
                raw = {"decision": "PAUSE",
                       "selected_candidate_id": rows[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "script: pause on the offered condition",
                       "deferred_candidate_ids": []}
                if policy == "pause_condition":
                    if pause.get("available") and pause.get("condition_ref"):
                        raw["reevaluation_hint_ref"] = str(pause["condition_ref"])
                    else:
                        return None
                else:
                    raw["reevaluation_hint_ref"] = "wref:not-offered-by-this-round"
                return raw
        if policy == "pause_unconditional":
            rows = [row for row in candidates if has(row, "PAUSE")]
            if rows:
                return {"decision": "PAUSE",
                        "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason,
                        "short_rationale": "script: unconditional pause",
                        "deferred_candidate_ids": []}
        if policy == "resume":
            rows = [row for row in candidates if has(row, "RESUME")]
            if len(rows) == 1:
                return {"decision": "RESUME", "selected_candidate_id": rows[0]["candidate_id"],
                        "reason_codes": reason, "short_rationale": "script: resume the work",
                        "deferred_candidate_ids": []}
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": reason, "short_rationale": "script: no action",
                "deferred_candidate_ids": []}

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.calls += 1
        try:
            payload = json.loads(user_prompt)
        except ValueError:
            payload = {}
        candidates = [safe_candidate(row) for row in (payload.get("candidates") or [])
                      if isinstance(row, Mapping)]
        pause = (payload.get("situation_context") or {}).get("pause_condition") or {}
        self.last_offered = {
            "policy": self.policy,
            "candidates": candidates,
            "pause_condition_available": bool(pause.get("available")),
            "pause_condition_ref": str(pause.get("condition_ref") or ""),
        }
        raw = self._select(payload, self.policy)
        if raw is None:
            # The round did not offer the condition this policy needs; answer an honest no-op
            # instead of inventing one, so the test can see the offer was absent.
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "script: absent",
                   "deferred_candidate_ids": []}
        return ModelCallResult(purpose="agency_cognition", provider="scripted",
                               model="scripted", text=json.dumps(raw, ensure_ascii=False),
                               latency_ms=1)


class WriterTransport:
    """内容回答替身；真实 ModelContinuationWriter 决定 no_change / 写入语义。"""

    backend = "scripted-writer-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.mode = "write"
        self.text = START_TEXT
        self.calls = 0

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.calls += 1
        if self.mode == "no_change":
            payload = {"kind": "no_change", "text": "", "self_note": NO_CHANGE_NOTE}
        else:
            payload = {"kind": "append_paragraph", "text": self.text,
                       "self_note": NO_CHANGE_NOTE}
        return ModelCallResult(purpose="artifact_edit", provider="scripted",
                               model="scripted", text=json.dumps(payload, ensure_ascii=False),
                               latency_ms=1)


# ---------------------------------------------------------------------------------------------
# 隔离装配：真实 Supply owner + ProductionLifeRuntime + 正式 runner
# ---------------------------------------------------------------------------------------------


def _start_services(root: Path | str):
    from tests.test_one_day_life import Services

    return Services(Path(root))


DRAIN_TIMEOUT_SECONDS = 10.0


def _stop_services(services: Any) -> dict[str, Any]:
    """Stop our own owner in the same order as production ``run_service``."""

    from lifesupply.service.life_supply import prepare_socket_path

    socket_path = Path(services.supply_path)
    expected = Path(services.root) / "life-supply.sock"
    if socket_path != expected:
        raise RuntimeError("REFUSING_TO_STOP_UNKNOWN_SOCKET:%s" % socket_path)
    server = services.server
    server.shutdown()
    server.server_close()
    drained = server.drain(DRAIN_TIMEOUT_SECONDS)
    try:
        socket_handling = prepare_socket_path(socket_path)
    finally:
        services.service.close()
    return {"socket_path": str(socket_path), "drained": bool(drained),
            "socket_handling": socket_handling}


def prepare_fixture(root: Path) -> Path:
    """Create one isolated instance through the owners' own commands; keep no runtime."""

    import g1_real_adoption as g1
    import one_day_life as odl
    from unittest import mock

    from tests.test_one_day_life import ENV_GATES

    # The prepare step itself runs under the same process-wide offline guard as the rest.
    selfcheck = g1._offline_guard_selfcheck()
    if not all(bool(selfcheck.get(key)) for key in (
            "inet_refused", "inet6_refused", "dns_refused", "connect_refused", "unix_ok")):
        raise AssertionError("prepare guard selfcheck incomplete")
    root = Path(root)
    services = _start_services(root / "supply")
    try:
        with mock.patch.dict(os.environ, ENV_GATES):
            prepared = odl.prepare(
                instance=root / "instance",
                supply_socket=services.supply_path,
                governance_socket=services.gov_path,
                subject="test:paused-resume-chain",
                title="暂停作品回来链夹具",
                seed_content="PRC-SEED-0a11 操作者提供的种子正文。\n")
        if not prepared.get("ok"):
            raise AssertionError("instance preparation failed")
    finally:
        _stop_services(services)
    return root


def open_chain(root: Path | str, tag: str) -> Any:
    import autonomous_life_assembly as assembly
    import one_day_life as odl
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    from agency_model_client import AgencyModelClient
    from model_continuation_writer import ModelContinuationWriter

    root = Path(root)
    services = _start_services(root / "supply")
    runtime = None
    try:
        instance = root / "instance"
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        state_root = odl.life_state_root(instance)
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        decision_transport = DecisionTransport()
        decision_transport.seed_artifact = str(data["artifact_id"])
        decision_transport.target_artifact = str(data["artifact_id"])
        decision_client = AgencyModelClient(
            config={"provider": "scripted", "model": "scripted"},
            transport=decision_transport, purpose="agency_cognition",
            timeout_s=20.0, max_output_tokens=2048)
        cognition = ProductionAgencyCognitionAdapter(
            client=decision_client, model_id="stub:paused-work-resume-chain",
            model_version="paused-work-resume-chain-v1",
            timeout_s=20.0, max_output_tokens=2048)
        writer_transport = WriterTransport()
        writer_client = AgencyModelClient(
            config={"provider": "scripted", "model": "scripted"},
            transport=writer_transport, purpose="artifact_edit",
            timeout_s=20.0, max_output_tokens=2048)
        writer = ModelContinuationWriter(writer_client, max_new_chars=300, timeout_s=20.0,
                                         max_output_tokens=2048)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
        if assembled["state"] != assembly.READY:
            raise AssertionError("assembly not READY: %r" % (assembled.get("reason"),))
        return SimpleNamespace(
            root=root, tag=tag, services=services, runtime=runtime,
            cycle=assembled["cycle"], runner=assembled["runner"],
            decision_transport=decision_transport, cognition=cognition,
            writer_transport=writer_transport, writer=writer,
            data=data, env=env, port=port, state_root=state_root,
            services_stopped=False)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        try:
            _stop_services(services)
        except Exception:
            pass
        raise


def close_chain(chain: Any) -> dict[str, Any]:
    report = {"closed": False, "service_stopped": False, "drained": None}
    try:
        chain.runtime.close()
        report["closed"] = not getattr(chain.runtime.lease, "held", True)
    except Exception as exc:
        report["close_error"] = type(exc).__name__
    if not chain.services_stopped:
        try:
            stopped = _stop_services(chain.services)
            chain.services_stopped = True
            report["service_stopped"] = True
            report["drained"] = bool(stopped["drained"])
            report["socket_handling"] = str(stopped["socket_handling"])
        except Exception as exc:
            report["stop_error"] = type(exc).__name__
    return report


# ---------------------------------------------------------------------------------------------
# 正式轮助手
# ---------------------------------------------------------------------------------------------


def run_window(chain: Any, minutes: int) -> Any:
    return chain.runner.run_due(act=True, now=moment(minutes))


def maintain(chain: Any, minutes: int) -> dict[str, Any]:
    return dict(chain.runner.maintain(now=moment(minutes)) or {})


def activity(chain: Any, activity_id: str) -> dict[str, Any]:
    return dict(chain.runtime.activity_read.get_activity(activity_id) or {})


def foreground(chain: Any) -> str:
    state, _ = chain.runtime.activity_read.read_canonical_state()
    return str(state.get("foreground_activity_ref") or "")


def conditions(chain: Any) -> dict[str, Any]:
    state = chain.runtime.waiting_coordinator.condition_store.load_state() or {}
    return dict(state.get("conditions") or {})


def eligibilities(chain: Any) -> dict[str, Any]:
    state = chain.runtime.waiting_coordinator.eligibility_store.load_state() or {}
    return dict(state.get("eligibilities") or {})


def open_eligibilities(chain: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in eligibilities(chain).values()
            if str(row.get("status") or "") == "OPEN"]


def open_resume_candidates(chain: Any, observed_at: str) -> list[dict[str, Any]]:
    cset = chain.runtime.candidate_materializer.build_candidate_set(observed_at=observed_at)
    refs = list(getattr(cset, "candidate_refs", []) or [])
    rows: list[dict[str, Any]] = []
    for ref in refs:
        row = chain.runtime.candidate_read.get_candidate(str(ref)) or {}
        if str(row.get("source_kind") or "") == "RESUME_ELIGIBLE":
            rows.append(dict(row))
    return rows


def owner_head(chain: Any, artifact_id: str) -> str:
    row = chain.port.read_head(artifact_id) or {}
    return str(row.get("version_id") or "")


def owner_versions(chain: Any, artifact_id: str) -> int:
    return int(chain.port.list_versions(artifact_id, limit=50)["total_versions"])


def active_activity_ids(chain: Any) -> list[str]:
    return [str(row.get("activity_id") or "")
            for row in chain.runtime.activity_read.list_open_activities()
            if str(row.get("status") or "") == "ACTIVE"]


def latest_adoption(chain: Any, decision_id: str) -> dict[str, Any]:
    try:
        state = chain.runtime.coordinator.integration_commit.read_state() or {}
    except Exception:
        return {}
    rows = (state.get("adoptions_by_id") or {}).values()
    matched = [dict(row) for row in rows
               if str(row.get("decision_ref") or "") == str(decision_id)]
    return matched[-1] if matched else {}


def first_condition(chain: Any) -> dict[str, Any]:
    rows = list(conditions(chain).values())
    if not rows:
        return {}
    rows.sort(key=lambda row: str(row.get("condition_id") or ""))
    return dict(rows[0])


def decision_ids_from(wake: Any) -> str:
    extra = dict(getattr(wake, "extra", None) or {})
    return str(extra.get("decision_id") or "")


# ---------------------------------------------------------------------------------------------
# P1：独立 OS 进程里的 START / no_change / 条件 PAUSE
# ---------------------------------------------------------------------------------------------


def run_p1_phase(root: Path | str) -> dict[str, Any]:
    import g1_real_adoption as g1

    root = Path(root)
    report: dict[str, Any] = {"phase": "p1", "pid": os.getpid(), "root": str(root)}
    guard = g1._offline_network_guard(root)
    guard.__enter__()
    try:
        report["guard"] = g1._offline_guard_selfcheck()
        chain = open_chain(root, "p1")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)

            # 1. real START, real content version.
            chain.decision_transport.policy = "start"
            chain.writer_transport.mode = "write"
            chain.writer_transport.text = START_TEXT
            started = run_window(chain, 0)
            report["start"] = safe_wake(started)
            activity_id = str(getattr(started, "activity_ref", "") or "")
            if not activity_id:
                open_ids = active_activity_ids(chain)
                activity_id = open_ids[0] if len(open_ids) == 1 else ""
            if not activity_id:
                raise AssertionError("START created no Activity: %r offered=%r"
                                     % (safe_wake(started),
                                        chain.decision_transport.last_offered))
            ensure_bound_work(chain, artifact_id)
            start_head = owner_head(chain, artifact_id)
            versions_after_start = owner_versions(chain, artifact_id)
            report.update({"activity_ref": activity_id, "artifact_id": artifact_id,
                           "run_id": str((chain.data.get("environment") or {}).get(
                               "CHIYO_AUTONOMOUS_RUN_ID") or ""),
                           "start_head": start_head,
                           "versions_after_start": versions_after_start})

            # 2. a legal no_change on the owner's current head makes the dependency real.
            chain.decision_transport.policy = "continue"
            chain.writer_transport.mode = "no_change"
            no_change = run_window(chain, STEP)
            report["no_change"] = safe_wake(no_change)
            if str(no_change.decision or "") != "CONTINUE":
                raise AssertionError("the no_change round was not a CONTINUE")
            head_after_no_change = owner_head(chain, artifact_id)
            versions_after_no_change = owner_versions(chain, artifact_id)
            report.update({"head_after_no_change": head_after_no_change,
                           "versions_after_no_change": versions_after_no_change})

            # 3. fresh Decision: PAUSE with the one offered condition.
            chain.decision_transport.policy = "pause_condition"
            paused_wake = run_window(chain, STEP * 2)
            report["pause"] = safe_wake(paused_wake)
            paused = activity(chain, activity_id)
            condition = first_condition(chain)
            pause_revision = int(paused.get("revision") or -1)
            report.update({
                "pause_revision": pause_revision,
                "foreground_after_pause": foreground(chain),
                "foreground_released": bool(paused.get("foreground_released")),
                "condition_id": str(condition.get("condition_id") or ""),
                "condition_status": str(condition.get("status") or ""),
                "condition_activity_revision_at_bind": int(
                    condition.get("activity_revision_at_bind") or -1),
                "condition_waiting_on_ref": str(condition.get("waiting_on_ref") or ""),
                "condition_baseline_head_ref": str(
                    (condition.get("parameters") or {}).get("baseline_head_ref") or ""),
                "offered_pause_condition_ref": str(
                    chain.decision_transport.last_offered.get("pause_condition_ref") or ""),
                "condition_count": len(conditions(chain)),
                "eligibility_count": len(eligibilities(chain)),
                "head_after_pause": owner_head(chain, artifact_id),
                "versions_after_pause": owner_versions(chain, artifact_id),
                "writer_calls_after_pause": int(chain.writer_transport.calls),
            })
        finally:
            report["close"] = close_chain(chain)
        return report
    finally:
        guard.__exit__(None, None, None)


def _run_p1_child(root: Path) -> dict[str, Any]:
    config = Path(root) / "p1-config.json"
    config.write_text(json.dumps({"root": str(root)}, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()),
         "--phase", "p1", "--config", str(config)],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if completed.returncode != 0:
        raise AssertionError("P1 child failed (exit=%d)" % completed.returncode)
    lines = [line for line in completed.stdout.splitlines()
             if line.startswith("PAUSED_RESUME_REPORT ")]
    if not lines:
        raise AssertionError("P1 child printed no report")
    return json.loads(lines[-1].split(" ", 1)[1])


# ---------------------------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------------------------


@POSIX_ONLY
class _ChainCase(unittest.TestCase):
    def setUp(self) -> None:
        import g1_real_adoption as g1

        self._guard_cm = g1._offline_network_guard(tempfile.gettempdir())
        self._guard_cm.__enter__()
        self.addCleanup(self._guard_cm.__exit__, None, None, None)
        self.selfcheck = g1._offline_guard_selfcheck()

    def _assert_guard(self, row: Mapping[str, Any], *, where: str) -> None:
        for key in ("inet_refused", "inet6_refused", "dns_refused", "connect_refused"):
            self.assertTrue(bool(row.get(key)), "%s guard %s" % (where, key))
        self.assertTrue(bool(row.get("unix_ok")), "%s guard unix_ok" % where)

    def _root(self, tag: str) -> Path:
        return Path(tempfile.mkdtemp(prefix="nyairo-paused-resume-%s-" % tag, dir="/tmp"))

    def _prepare(self, root: Path) -> None:
        prepare_fixture(root)

    # -- case 1 ------------------------------------------------------------------------
    def test_two_pid_formal_chain_pause_owner_proof_resume_and_continue(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("chain")
        self._prepare(root)
        p1 = _run_p1_child(root)

        self.assertNotEqual(int(p1["pid"]), os.getpid())
        self._assert_guard(p1.get("guard") or {}, where="p1")
        self.assertEqual(p1["start"]["decision"], "START")
        self.assertEqual(p1["start"]["outcome"], "ACTED")
        self.assertEqual(p1["no_change"]["decision"], "CONTINUE")
        self.assertEqual(p1["no_change"]["outcome"], "NO_ACTION")
        self.assertEqual(p1["pause"]["decision"], "PAUSE")
        self.assertEqual(p1["pause"]["outcome"], "PAUSED")
        self.assertEqual(p1["condition_count"], 1)
        self.assertEqual(p1["eligibility_count"], 0)
        self.assertEqual(p1["condition_status"], "PENDING")
        self.assertEqual(p1["condition_activity_revision_at_bind"], p1["pause_revision"])
        self.assertTrue(p1["offered_pause_condition_ref"])
        self.assertEqual(p1["condition_baseline_head_ref"], p1["head_after_no_change"])
        self.assertTrue(p1["condition_waiting_on_ref"])
        self.assertFalse(p1["foreground_after_pause"] or "")
        self.assertTrue(p1["foreground_released"])
        self.assertEqual(p1["head_after_pause"], p1["start_head"])
        self.assertEqual(p1["versions_after_pause"], p1["versions_after_start"])
        self.assertTrue(p1["close"].get("closed"))
        self.assertTrue(p1["close"].get("service_stopped"))

        artifact_id = p1["artifact_id"]
        activity_id = p1["activity_ref"]
        pause_revision = int(p1["pause_revision"])

        chain = open_chain(root, "p2")
        try:
            ensure_bound_work(chain, artifact_id)
            self.assertEqual(str(chain.env.get("CHIYO_AUTONOMOUS_RUN_ID") or ""),
                             p1["run_id"])
            self.assertEqual(owner_head(chain, artifact_id), p1["start_head"])
            paused = activity(chain, activity_id)
            self.assertEqual(str(paused.get("status") or ""), "PAUSED")
            self.assertEqual(int(paused.get("revision") or -1), pause_revision)
            self.assertFalse(foreground(chain) or "")
            condition = first_condition(chain)
            self.assertEqual(str(condition.get("condition_id") or ""), p1["condition_id"])

            # Condition not satisfied: no OPEN eligibility and no RESUME in the legal set.
            chain.decision_transport.policy = "no_action"
            unsat = run_window(chain, STEP * 3)
            self.assertEqual(str(unsat.decision or ""), "NO_ACTION")
            self.assertEqual(open_eligibilities(chain), [])
            self.assertFalse(any("RESUME" in row["legal_decisions"]
                                 for row in chain.decision_transport.last_offered["candidates"]))
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "PAUSED")
            writer_calls_before = int(chain.writer_transport.calls)

            # The owner really commits a new version of the referenced source.
            head_before = owner_head(chain, artifact_id)
            committed = chain.port.commit_version(
                artifact_id=artifact_id, expected_head=head_before,
                content="%s\n\n%s\n" % (chain.port.resume_point(artifact_id).content or "",
                                       OWNER_TEXT),
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="prc:owner:%d" % os.getpid())
            self.assertTrue(bool(getattr(committed, "applied", False)))
            head_after_owner_proof = owner_head(chain, artifact_id)
            versions_after_owner_proof = owner_versions(chain, artifact_id)
            self.assertEqual(versions_after_owner_proof,
                             int(p1["versions_after_pause"]) + 1)
            maintained = maintain(chain, STEP * 4)
            self.assertEqual(list(maintained.get("satisfied") or []), [p1["condition_id"]])
            self.assertEqual(len(list(maintained.get("emitted_eligibilities") or [])), 1)
            self.assertEqual(int(maintained.get("llm_calls") or 0), 0)
            self.assertFalse(bool(maintained.get("auto_resumed")))
            self.assertEqual(int(chain.writer_transport.calls), writer_calls_before)
            rows = open_eligibilities(chain)
            self.assertEqual(len(rows), 1)
            eligibility = rows[0]
            self.assertEqual(str(eligibility.get("activity_id") or ""), activity_id)
            self.assertEqual(int(eligibility.get("activity_revision") or -1), pause_revision)
            self.assertEqual(str(eligibility.get("status") or ""), "OPEN")
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "PAUSED")

            # The formal materializer offers exactly the matching RESUME candidate.
            materialized = open_resume_candidates(chain, stamp(STEP * 4))
            self.assertEqual(len(materialized), 1)
            self.assertEqual(str(materialized[0].get("resume_eligibility_ref") or ""),
                             str(eligibility.get("eligibility_id") or ""))
            self.assertEqual(str(materialized[0].get("activity_ref") or ""), activity_id)

            # Fresh Decision chooses RESUME; same Activity comes back and eligibility is consumed.
            chain.decision_transport.policy = "resume"
            resumed_wake = run_window(chain, STEP * 5)
            self.assertEqual(str(resumed_wake.decision or ""), "RESUME")
            self.assertEqual(str(resumed_wake.outcome or ""), "ACTED")
            resumed = activity(chain, activity_id)
            self.assertEqual(str(resumed.get("status") or ""), "ACTIVE")
            self.assertEqual(str(resumed.get("activity_id") or ""), activity_id)
            consumed = eligibilities(chain)[str(eligibility["eligibility_id"])]
            self.assertEqual(str(consumed.get("status") or ""), "CONSUMED")
            self.assertEqual(foreground(chain), activity_id)
            versions_after_resume = owner_versions(chain, artifact_id)
            head_after_resume = owner_head(chain, artifact_id)

            # If RESUME only restored the Activity, the next normal CONTINUE writes the version.
            if versions_after_resume == p1["versions_after_pause"]:
                chain.decision_transport.policy = "continue"
                chain.writer_transport.mode = "write"
                chain.writer_transport.text = CONTINUE_TEXT
                continued = run_window(chain, STEP * 6)
                self.assertEqual(str(continued.decision or ""), "CONTINUE")
                self.assertEqual(str(continued.outcome or ""), "ACTED")
                self.assertEqual(str(getattr(continued, "activity_ref", "") or ""), activity_id)
                self.assertEqual(owner_versions(chain, artifact_id), versions_after_resume + 1)
                self.assertNotEqual(owner_head(chain, artifact_id), head_after_resume)
                body = chain.port.resume_point(artifact_id).content or ""
                self.assertIn(CONTINUE_TEXT, body)
            # Either the RESUME round itself or the next normal CONTINUE created exactly one
            # real owner version on the same work.
            self.assertEqual(owner_versions(chain, artifact_id),
                             int(versions_after_owner_proof) + 1,
                             "owner_proof_versions=%r resume_versions=%r writer_calls=%r"
                             % (versions_after_owner_proof, versions_after_resume,
                                chain.writer_transport.calls))
            self.assertNotEqual(owner_head(chain, artifact_id), head_after_owner_proof)
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "ACTIVE")
        finally:
            close_chain(chain)

    # -- case 2 ------------------------------------------------------------------------
    def test_negative_controls_unconditional_bad_hint_and_unreadable_owner(self):
        self._assert_guard(self.selfcheck, where="parent")
        artifact_id = ""
        activity_id = ""

        # 2a. Unconditional PAUSE: offered condition exists, Decision names none.
        root = self._root("negative-unconditional")
        self._prepare(root)
        chain = open_chain(root, "neg-uncond")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)
            started = self._start(chain, 0)
            activity_id = started
            self._no_change(chain, STEP)
            chain.decision_transport.policy = "pause_unconditional"
            paused_wake = run_window(chain, STEP * 2)
            self.assertEqual(str(paused_wake.decision or ""), "PAUSE")
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "PAUSED")
            self.assertEqual(conditions(chain), {})
            self.assertEqual(eligibilities(chain), {})
            head_before = owner_head(chain, artifact_id)
            chain.port.commit_version(
                artifact_id=artifact_id, expected_head=head_before,
                content="%s\n\n%s\n" % (chain.port.resume_point(artifact_id).content or "",
                                       OWNER_TEXT),
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="prc:neg:uncond:%d" % os.getpid())
            maintained = maintain(chain, STEP * 3)
            self.assertEqual(list(maintained.get("satisfied") or []), [])
            self.assertEqual(open_eligibilities(chain), [])
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "PAUSED")
        finally:
            close_chain(chain)

        # 2b. A hint this round did not offer is refused by the formal validator.
        root = self._root("negative-hint")
        self._prepare(root)
        chain = open_chain(root, "neg-hint")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)
            activity_id = self._start(chain, 0)
            self._no_change(chain, STEP)
            chain.decision_transport.policy = "pause_bad_hint"
            refused = run_window(chain, STEP * 2)
            self.assertIn(str(refused.outcome or ""),
                          {"DECISION_FAILED", "ADOPTION_REJECTED"},
                          safe_wake(refused))
            self.assertEqual(conditions(chain), {})
            self.assertEqual(eligibilities(chain), {})
            still = activity(chain, activity_id)
            self.assertEqual(str(still.get("status") or ""), "ACTIVE")
            self.assertEqual(foreground(chain), activity_id)
        finally:
            close_chain(chain)

        # 2c. Owner unreadable: a real PENDING condition must not become a pseudo-OPEN.
        root = self._root("negative-unreadable")
        self._prepare(root)
        chain = open_chain(root, "neg-unreadable")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)
            activity_id = self._start(chain, 0)
            self._no_change(chain, STEP)
            chain.decision_transport.policy = "pause_condition"
            paused_wake = run_window(chain, STEP * 2)
            self.assertEqual(str(paused_wake.decision or ""), "PAUSE")
            self.assertEqual(len(conditions(chain)), 1)
            condition = first_condition(chain)
            _stop_services(chain.services)
            chain.services_stopped = True
            maintained = maintain(chain, STEP * 3)
            self.assertEqual(list(maintained.get("satisfied") or []), [])
            self.assertEqual(open_eligibilities(chain), [])
            self.assertEqual(str(first_condition(chain).get("status") or ""), "PENDING")
            self.assertEqual(str(activity(chain, activity_id).get("status") or ""), "PAUSED")
            self.assertTrue(any(str(code).startswith("OWNER_HEAD_UNREADABLE")
                                or str(code) == "OWNER_HEAD_UNREADABLE"
                                for code in (maintained.get("errors") or [])))
            self.assertEqual(str(condition.get("condition_id") or ""),
                             str(first_condition(chain).get("condition_id") or ""))
        finally:
            close_chain(chain)

    # -- case 3 ------------------------------------------------------------------------
    def test_foreground_conflict_does_not_preempt_and_resume_only_after_pausing_b(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("conflict")
        self._prepare(root)
        chain = open_chain(root, "conflict")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)
            a_id = self._start(chain, 0)
            self._no_change(chain, STEP)
            chain.decision_transport.policy = "pause_condition"
            self.assertEqual(str(run_window(chain, STEP * 2).decision or ""), "PAUSE")
            pause_revision = int(activity(chain, a_id).get("revision") or -1)

            head_before = owner_head(chain, artifact_id)
            chain.port.commit_version(
                artifact_id=artifact_id, expected_head=head_before,
                content="%s\n\n%s\n" % (chain.port.resume_point(artifact_id).content or "",
                                       OWNER_TEXT),
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="prc:conflict:owner:%d" % os.getpid())
            maintained = maintain(chain, STEP * 3)
            self.assertEqual(len(list(maintained.get("emitted_eligibilities") or [])), 1)
            rows = open_eligibilities(chain)
            self.assertEqual(len(rows), 1)
            eligibility = rows[0]

            # A second real work B: created by the owner, not by the model.
            created_b = chain.port.create_artifact(
                workspace_id=str(chain.data["workspace_id"]),
                title="另一件作品（占槽负控）",
                content="PRC-B-SEED-1f2c 另一件作品的种子正文。\n",
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="prc:conflict:b:%d" % os.getpid())
            self.assertTrue(bool(getattr(created_b, "applied", False)))
            b_artifact = str(created_b.artifact_id)
            self.assertTrue(b_artifact)
            self.assertNotEqual(bare(b_artifact), bare(artifact_id))

            # Start B while the slot is free; A stays PAUSED.
            chain.decision_transport.target_artifact = b_artifact
            chain.decision_transport.policy = "start"
            chain.writer_transport.mode = "write"
            chain.writer_transport.text = START_TEXT
            b_wake = run_window(chain, STEP * 4)
            self.assertEqual(str(b_wake.decision or ""), "START",
                             "%r offered=%r" % (safe_wake(b_wake),
                                                chain.decision_transport.last_offered))
            b_id = str(getattr(b_wake, "activity_ref", "") or "")
            if not b_id:
                active = active_activity_ids(chain)
                b_id = active[0] if len(active) == 1 else ""
            self.assertTrue(b_id)
            self.assertNotEqual(b_id, a_id)
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "PAUSED")
            self.assertEqual(str(activity(chain, b_id).get("status") or ""), "ACTIVE")
            self.assertEqual(foreground(chain), b_id)

            # Candidate/legal set refuses a background RESUME while B holds the slot.
            chain.decision_transport.policy = "no_action"
            blocked = run_window(chain, STEP * 5)
            self.assertEqual(str(blocked.decision or ""), "NO_ACTION")
            self.assertFalse(any("RESUME" in row["legal_decisions"]
                                 for row in chain.decision_transport.last_offered["candidates"]))
            self.assertEqual(open_resume_candidates(chain, stamp(STEP * 5)), [])
            self.assertEqual(str(activity(chain, b_id).get("status") or ""), "ACTIVE")
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "PAUSED")
            self.assertEqual(open_eligibilities(chain)[0]["status"], "OPEN")

            # Formally pause B; no automatic return to A.
            chain.decision_transport.policy = "pause_unconditional"
            paused_b = run_window(chain, STEP * 6)
            self.assertEqual(str(paused_b.decision or ""), "PAUSE")
            self.assertEqual(str(activity(chain, b_id).get("status") or ""), "PAUSED")
            self.assertFalse(foreground(chain) or "")
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "PAUSED")

            # Fresh NO_ACTION: legal, zero owner writes, A's eligibility stays OPEN.
            versions_before = owner_versions(chain, artifact_id)
            head_before_no_action = owner_head(chain, artifact_id)
            chain.decision_transport.policy = "no_action"
            no_action = run_window(chain, STEP * 7)
            self.assertEqual(str(no_action.decision or ""), "NO_ACTION")
            self.assertEqual(owner_versions(chain, artifact_id), versions_before)
            self.assertEqual(owner_head(chain, artifact_id), head_before_no_action)
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "PAUSED")
            self.assertEqual(open_eligibilities(chain)[0]["status"], "OPEN")

            # Fresh RESUME consumes A's eligibility; B remains PAUSED.
            chain.decision_transport.policy = "resume"
            resumed = run_window(chain, STEP * 8)
            self.assertEqual(str(resumed.decision or ""), "RESUME")
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "ACTIVE")
            self.assertEqual(str(activity(chain, b_id).get("status") or ""), "PAUSED")
            self.assertEqual(foreground(chain), a_id)
            consumed = eligibilities(chain)[str(eligibility["eligibility_id"])]
            self.assertEqual(str(consumed.get("status") or ""), "CONSUMED")
            self.assertEqual(str(consumed.get("activity_id") or ""), a_id)
            self.assertNotEqual(str(consumed.get("activity_id") or ""), b_id)
            self.assertGreaterEqual(int(activity(chain, a_id).get("revision") or -1),
                                    pause_revision + 1)
        finally:
            close_chain(chain)

    # -- case 4 ------------------------------------------------------------------------
    def test_no_change_after_return_and_repeated_condition_event_are_idempotent(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("idempotent")
        self._prepare(root)
        chain = open_chain(root, "idempotent")
        try:
            artifact_id = str(chain.data["artifact_id"])
            ensure_bound_work(chain, artifact_id)
            a_id = self._start(chain, 0)
            self._no_change(chain, STEP)
            chain.decision_transport.policy = "pause_condition"
            self.assertEqual(str(run_window(chain, STEP * 2).decision or ""), "PAUSE")
            head_before = owner_head(chain, artifact_id)
            chain.port.commit_version(
                artifact_id=artifact_id, expected_head=head_before,
                content="%s\n\n%s\n" % (chain.port.resume_point(artifact_id).content or "",
                                       OWNER_TEXT),
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="prc:idem:owner:%d" % os.getpid())
            maintained = maintain(chain, STEP * 3)
            self.assertEqual(len(list(maintained.get("emitted_eligibilities") or [])), 1)
            eligibility_id = str(open_eligibilities(chain)[0]["eligibility_id"])

            # Let the return round have real content available, so the *next* round still has a
            # legal CONTINUE.  Whether the version is written by RESUME itself or by that next
            # round is the contract's own choice; the no_change below is checked against whatever
            # head really exists at that point.
            chain.writer_transport.mode = "write"
            chain.writer_transport.text = CONTINUE_TEXT
            chain.decision_transport.policy = "resume"
            resume_wake = run_window(chain, STEP * 4)
            self.assertEqual(str(resume_wake.decision or ""), "RESUME")
            decision_id = decision_ids_from(resume_wake)
            self.assertTrue(decision_id.startswith("dec:"))
            decision_count_before = self._decision_count(chain)
            action_count_before = self._action_count(chain)

            # Back on the same A: a legal no_change writes nothing and keeps the Decision identity.
            chain.decision_transport.policy = "continue"
            chain.writer_transport.mode = "no_change"
            versions_before = owner_versions(chain, artifact_id)
            head_before_no_change = owner_head(chain, artifact_id)
            no_change = run_window(chain, STEP * 5)
            self.assertEqual(str(no_change.decision or ""), "CONTINUE",
                             "%r offered=%r" % (safe_wake(no_change),
                                                chain.decision_transport.last_offered))
            self.assertEqual(str(no_change.outcome or ""), "NO_ACTION")
            self.assertEqual(owner_versions(chain, artifact_id), versions_before)
            self.assertEqual(owner_head(chain, artifact_id), head_before_no_change)
            self.assertEqual(self._decision_count(chain), decision_count_before + 1)
            self.assertIn(decision_id, self._decision_state(chain).get("decisions_by_id") or {})
            self.assertEqual(str(activity(chain, a_id).get("status") or ""), "ACTIVE")

            # Repeating the same condition event must not emit a second eligibility.
            condition = first_condition(chain)
            self.assertEqual(str(condition.get("status") or ""), "SATISFIED")
            event = self._head_event(chain, condition, owner_head(chain, artifact_id))
            repeated = chain.runtime.waiting_coordinator.evaluate_event(event) or {}
            self.assertEqual(list(repeated.get("emitted_eligibilities") or []), [])
            self.assertEqual(len(eligibilities(chain)), 1)

            # Repeating the same resume operation is refused/idempotent, not a second transition.
            from activity_waiting_agenda import EligibilityNotOpenError

            adoption = latest_adoption(chain, decision_id)
            adoption_id = str(adoption.get("adoption_id") or "")
            before_revision = int(activity(chain, a_id).get("revision") or -1)
            with self.assertRaises(EligibilityNotOpenError):
                chain.runtime.waiting_coordinator.explicit_resume_from_eligibility(
                    eligibility_id=eligibility_id,
                    expected_revision=before_revision,
                    idempotency_key="lr4_resume:%s:%s" % (decision_id, adoption_id),
                    decision_ref=decision_id,
                    adoption_ref=adoption_id or "adp:not-available",
                    occurred_at=stamp(STEP * 6),
                )
            self.assertEqual(int(activity(chain, a_id).get("revision") or -1), before_revision)
            self.assertEqual(str(eligibilities(chain)[eligibility_id]["status"]), "CONSUMED")
            self.assertEqual(self._action_count(chain), action_count_before)
        finally:
            close_chain(chain)

    # -- shared helpers ----------------------------------------------------------------
    def _start(self, chain: Any, minutes: int) -> str:
        chain.decision_transport.policy = "start"
        chain.writer_transport.mode = "write"
        chain.writer_transport.text = START_TEXT
        wake = run_window(chain, minutes)
        self.assertEqual(str(wake.decision or ""), "START")
        activity_id = str(getattr(wake, "activity_ref", "") or "")
        if not activity_id:
            active = active_activity_ids(chain)
            activity_id = active[0] if len(active) == 1 else ""
        self.assertTrue(activity_id)
        return activity_id

    def _no_change(self, chain: Any, minutes: int) -> Any:
        chain.decision_transport.policy = "continue"
        chain.writer_transport.mode = "no_change"
        wake = run_window(chain, minutes)
        self.assertEqual(str(wake.decision or ""), "CONTINUE")
        self.assertEqual(str(wake.outcome or ""), "NO_ACTION")
        return wake

    @staticmethod
    def _decision_state(chain: Any) -> dict[str, Any]:
        try:
            return dict(json.loads(
                chain.runtime.decision_store.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    @classmethod
    def _decision_count(cls, chain: Any) -> int:
        return len(cls._decision_state(chain).get("decisions_by_id") or {})

    @staticmethod
    def _action_count(chain: Any) -> int:
        try:
            return len(chain.runtime.action_read.list_actions() or [])
        except Exception:
            return -1

    @staticmethod
    def _head_event(chain: Any, condition: Mapping[str, Any], head: str) -> dict[str, Any]:
        import life_wait_basis as lwb

        basis = lwb.basis_from_condition(condition)
        event = lwb.head_change_event(basis, current_head=head,
                                      observed_at=stamp(STEP * 5))
        if not event:
            raise AssertionError("no head-change event from the bound condition")
        return event


# ---------------------------------------------------------------------------------------------
# 子进程入口（P1 phase）
# ---------------------------------------------------------------------------------------------


def _child_main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.phase != "p1":
        raise SystemExit("unknown phase")
    report = run_p1_phase(Path(config["root"]))
    print("PAUSED_RESUME_REPORT " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    if "--phase" in sys.argv[1:]:
        raise SystemExit(_child_main(sys.argv[1:]))
    unittest.main(verbosity=2)
