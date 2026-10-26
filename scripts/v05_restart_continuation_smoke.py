#!/usr/bin/env python3
"""v0.5 零模型真 owner 冒烟：同作品实践与本地表达在**两个不同 PID**里同根承接。

它做什么（全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发）：

1. 父调度器只建一个隔离夹具根（世界包 + ``odl.prepare`` 的实例 + 一份空 M0 Memory），
   然后在**独立子进程**里各起一次真实 Supply owner（同一个 canonical db、同一 socket
   路径），跑完由该进程自己正规关停并退出。父进程不持有 runtime、不持有 owner。
2. **P1**：正式 ``evaluate_once`` → AG-1 原始 JSON 脚本 Decision → AG-2 adoption →
   AR-0 真实作品写入。第一轮 START 作品 A 写出下一版并得到 owner 结果级身份
   （``artifact:<id>@artifactversion:<head>``）与 handoff；第二轮在同一 Activity 内
   CONTINUE A 再写一版，并用**真实 owner 经历**记一条 HELPED 评价，方法进入 TRIAL。
   第三轮从 A 当前 head 正式 prepare 草稿 D1，第四轮合法 defer。另建作品 B 作
   “不串目标”的对照。P1 关闭 runtime（释放 lease）并关停自己的 Supply owner。
3. **P2**（**不同 PID**）：重开同一个新根的 Supply canonical db、M0 Memory 与 AG-2
   store，不做内存接力。第一次正式请求纯读：owner head / 版本数 / handoff 数 /
   草稿状态都不变，也不自动续作或分享。随后合法 CONTINUE 同一 Activity 续写 A
   （新结果身份，不复用旧事件），D1 仍绑定旧版本；发送旧稿由 owner 如实拒
   ``stale_source`` 且零写；显式 prepare D2 绑定新版本，``send_local`` 只产生
   **一条**本地 receipt，重复发送仍是同一条。最后沿真实 owner 的 CAS 退休方法，
   旧 ``method_ref`` 被 AG-1 以 ``INVENTED_METHOD_REF`` 拒绝且不提交 Decision，
   不带旧 ref 的 NO_ACTION 仍合法提交。
4. **P3**（可选、纯读）：再开一次同根 runtime，只读回作品 A/B、D1/D2 与 handoff
   稳定，不跑任何 Decision、不写版本、不投递。

替身只有 Decision 回答与内容回答（脚本形状，明示）。真实件：Supply/Practice/
Sharing owner、M0 Memory、ProductionLifeRuntime、AG-0/AG-1/AG-2/AR-0。
不宣称真实模型自然采用、G1、24h 或 crash 矩阵；这是正常有序停启，不是崩溃恢复。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/v05_restart_continuation_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SUBJECT = "test:v05-restart-continuation"
D1_TEXT = "这一段来自作品刚写完的那一版，先留在这里。"
D2_TEXT = "换过一版之后，这一句才是现在想说的。"
B_BODY = "另一件作品的正文，只用来验证续写不会串目标。"

#: 每轮一个自己的观察时刻：静默账本按回合状态键控，冻结时刻会让后续回合变成
#: ``SAME_STATE_ALREADY_CONSIDERED``。步长 400 分钟越过既有 6 小时 TTL，推进的是
#: 夹具时钟，不是静默规则。这里的速度快进只影响这个隔离夹具，不是自然日。
_BASE_TIME = datetime.now().astimezone()
_ROUND_INDEX = [0]

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def next_instant() -> str:
    _ROUND_INDEX[0] += 1
    return (_BASE_TIME + timedelta(minutes=400 * _ROUND_INDEX[0])).isoformat()


def bare(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def work_ref(value) -> str:
    text = bare(value)
    return "artifact:%s" % text if text else ""


def head_version(chain, artifact_id) -> str:
    text = str(artifact_id or "").strip()
    if not text:
        return ""
    point = chain.port.resume_point(text)
    return str(getattr(point, "head_version_id", "") or "")


def version_count(chain, artifact_id) -> int:
    text = str(artifact_id or "").strip()
    if not text:
        return 0
    return int(chain.port.list_versions(text, limit=50)["total_versions"])


def handoff_state(chain) -> dict:
    return chain.runtime.coordinator.integration_commit.read_state()


def handoff_count(chain) -> int:
    return len((handoff_state(chain) or {}).get("handoff_events_by_id") or {})


def experience_ids(chain) -> list:
    return sorted(str(item.get("experience_id"))
                  for item in (chain.cycle.practice.experiences() or [])
                  if item.get("experience_id"))


def share_counts(chain) -> dict:
    data = (chain.cycle.sharing_service.list_shares(include_text=False).get("data") or {})
    return dict(data.get("counts") or {})


# ---------------------------------------------------------------------------------------------
# 两个阶段共用的 Decision 替身：只回显本轮真实请求里看到的 id/head，附加 practice 安全摘要
# ---------------------------------------------------------------------------------------------


def _safe_practice(practice: dict) -> dict:
    """Only identities and counters from the practice slice; never instruction bodies."""

    if not isinstance(practice, dict):
        return {}
    methods = []
    for row in practice.get("methods") or []:
        if not isinstance(row, dict):
            continue
        evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
        methods.append({
            "method_ref": str(row.get("method_ref") or ""),
            "method_state": str(row.get("method_state") or ""),
            "trial_count": len(row.get("trials") or []),
            "evidence_usable": row.get("evidence_usable"),
            "counterexample_count": int(row.get("counterexample_count") or 0),
            "evaluation": {
                "available": bool(evaluation.get("available")),
                "outcome": str(evaluation.get("outcome") or ""),
                "version_id": str(evaluation.get("version_id") or ""),
                "evidence_count": evaluation.get("evidence_count"),
                "reason_code": str(evaluation.get("reason_code") or ""),
            },
        })
    withheld = practice.get("withheld_methods") or {}
    return {
        "methods": methods,
        "method_refs": [str(ref) for ref in (practice.get("method_refs") or [])],
        "withheld_count": int((withheld or {}).get("count") or 0),
        "withheld_reasons": [str(code) for code in (withheld or {}).get("reason_codes") or []],
    }


def _safe_sharing(sharing: dict) -> dict:
    if not isinstance(sharing, dict):
        return {}
    shares = []
    for row in sharing.get("shares") or []:
        if not isinstance(row, dict):
            continue
        shares.append({
            "share_id": str(row.get("share_id") or ""),
            "state": str(row.get("state") or ""),
            "head_version_id": str(row.get("head_version_id") or ""),
            "source_current": dict(row.get("source_current") or {}),
            "text_available": row.get("text_available"),
            "text_withheld": row.get("text_withheld"),
            "withheld_reason_code": str(row.get("withheld_reason_code") or ""),
            "text_chars": int(row.get("text_chars") or 0),
        })
    sources = []
    for row in sharing.get("sources") or []:
        if not isinstance(row, dict):
            continue
        sources.append({"kind": str(row.get("kind") or ""),
                        "artifact_id": str(row.get("artifact_id") or "")})
    return {"shares": shares, "sources": sources}


class RestartCognition:
    """按计划回答原始 JSON 的 Decision 替身，并记录本轮请求的安全结构。"""

    model_id = "stub:v05-restart-continuation"
    model_version = "v05-restart-continuation-v1"

    def __init__(self, planner_cls) -> None:
        self._planner = planner_cls()
        self._planner.model_id = self.model_id
        self._planner.model_version = self.model_version
        self.captured: list[dict] = []
        self.call_count = 0

    @property
    def plan(self) -> list:
        return self._planner.plan

    @plan.setter
    def plan(self, value) -> None:
        self._planner.plan = value

    @property
    def last_head_seen(self) -> dict:
        return self._planner.last_head_seen

    def invoke(self, request, *, schema_retry_index: int = 0):
        situation = getattr(request, "situation_context", {}) or {}
        practice = situation.get("practice") if isinstance(situation, dict) else {}
        self.captured.append({"practice": _safe_practice(practice)})
        self.call_count += 1
        return self._planner.invoke(request, schema_retry_index=schema_retry_index)


def install_planner(chain, planner_cls) -> RestartCognition:
    cognition = RestartCognition(planner_cls)
    chain.cognition.invoke = cognition.invoke
    return cognition


def latest_view(planner: RestartCognition) -> dict:
    if not planner.captured:
        return {}
    block = planner._planner.captured[-1] if planner._planner.captured else {}
    return {"practice": planner.captured[-1]["practice"],
            "sharing": _safe_sharing((block or {}).get("sharing") or {}),
            "bounded": (block or {}).get("bounded") or {}}


def run_round(chain, planner, step, *, trigger: str, observed_at: str | None = None) -> dict:
    planner.plan = [step] if step else []
    return chain.cycle.evaluate_once(trigger_ref=trigger, act=True,
                                     observed_at=observed_at or next_instant())


# ---------------------------------------------------------------------------------------------
# 夹具：父调度器只准备根；每个阶段进程自己起停 owner
# ---------------------------------------------------------------------------------------------


def _start_services(root: Path):
    from tests.test_one_day_life import Services

    return Services(Path(root))


#: 与生产 ``run_service`` 的默认有界等待一致：先让在途请求结束，再动 socket 文件。
DRAIN_TIMEOUT_SECONDS = 10.0


def _stop_services(services) -> dict:
    """Stop our own owner in the same order as production ``run_service``.

    Order (mirrors ``life_supply.run_service``'s ``finally``):

    1. ``shutdown()`` — the accept loop ends (``serve_forever`` returns);
    2. ``server_close()`` — the listener is closed, so no new request is accepted;
    3. ``drain(timeout)`` — wait a bounded time for in-flight requests;
    4. ``prepare_socket_path()`` — the owner's own guard removes our stale socket,
       and refuses (without unlinking) a live socket, a non-socket, or a path
       owned by another uid;
    5. ``service.close()`` — close the db/service.

    The shared fixture's ``Services.stop()`` has no ``drain()`` step and never
    unlinks, so it cannot express this order; we use its public ``server`` and
    ``service`` attributes locally instead of changing the fixture for every test.

    Only the newly created isolated fixture root is acceptable here: anything
    else is refused before touching it.
    """

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


def prepare_fixture(root: Path | None = None) -> Path:
    """Build one isolated instance once; no runtime and no owner is kept afterwards."""

    import one_day_life as odl
    from components.worldgen import schema
    from components.worldgen.instance import init_instance
    from components.worldgen.tests._setup import make_valid_docs
    from tests.test_one_day_life import ENV_GATES

    import result_method_behavior_smoke as harness

    if root is None:
        root = harness._fresh_root("v05-restart")
    root = Path(root)
    package_dir = root / "package"
    schema.write_package(package_dir, make_valid_docs(seed=929711, theme="coastal_town"))
    world_home = root / "world-home"
    init_instance(package_dir, world_home)
    instance = root / "instance"
    services = _start_services(root / "supply")
    try:
        with mock.patch.dict(os.environ, ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=services.supply_path,
                                   governance_socket=services.gov_path,
                                   subject=SUBJECT, title="v0.5 restart continuation",
                                   world_package=package_dir, create_private_items=True)
        if not prepared.get("ok"):
            raise SystemExit("prepare failed: %r" % (prepared,))
    finally:
        _stop_services(services)
    (root / "memory").mkdir(exist_ok=True)
    return root


def open_chain(root: Path, tag: str):
    """Reopen the same real owners + a *new* runtime/assembly in this process."""

    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter
    from result_method_behavior_smoke import ScriptedClient, ScriptedCognition

    root = Path(root)
    services = _start_services(root / "supply")
    runtime = None
    try:
        instance = root / "instance"
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        memory_dir = root / "memory"
        memory_dir.mkdir(exist_ok=True)
        evidence_db = memory_dir / "memory.sqlite3"
        controls_db = memory_dir / "controls.sqlite3"
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
            "NYA02_MEMORY_DB": str(evidence_db),
            "NYA02_MEMORY_CONTROLS_DB": str(controls_db),
            "NYA02_MEMORY_SUBJECT": "chiyo",
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        cognition = ScriptedCognition()
        client = ScriptedClient()
        writer = ModelContinuationWriter(client, max_new_chars=300)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        memory_port = LifeExperiencePort(str(evidence_db), controls_db=str(controls_db),
                                         subject="chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=consumer)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        if (assembled.get("practice") or {}).get("status") != "READY":
            raise SystemExit("practice workspace not READY: %r" % (assembled.get("practice"),))
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], cognition=cognition, client=client,
                               data=data, env=env, port=port, memory_port=memory_port,
                               evidence_db=evidence_db, controls_db=controls_db, tag=tag)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        _stop_services(services)
        raise


def close_chain(chain) -> dict:
    report = {"closed": False, "service_stopped": False}
    try:
        chain.runtime.close()
        report["closed"] = not getattr(chain.runtime.lease, "held", True)
    except Exception as exc:  # noqa: BLE001 - printed, not hidden
        print("  runtime close error:", type(exc).__name__, str(exc)[:200])
    try:
        shutdown = _stop_services(chain.services)
        report["service_stopped"] = True
        report["service_drained"] = bool(shutdown["drained"])
        report["service_socket_handling"] = str(shutdown["socket_handling"])
    except Exception as exc:  # noqa: BLE001
        print("  supply stop error:", type(exc).__name__, str(exc)[:200])
    return report


# ---------------------------------------------------------------------------------------------
# P1：真实写 + 方法评价 + 草稿 D1 / defer；关闭自己的 owner
# ---------------------------------------------------------------------------------------------


def phase_p1(config: dict) -> dict:
    import life_sharing_context as lsc
    import result_method_behavior_smoke as harness
    import v05_result_sharing_continuation_smoke as sharing

    root = Path(config["root"])
    chain = open_chain(root, "p1")
    planner = None
    report: dict = {"phase": "p1", "pid": os.getpid(), "root": str(root)}
    try:
        # The seed round uses the fixture's own scripted cognition (it needs ``.script``);
        # the sharing planner is installed only after the real first write is committed.
        seed = harness.seed_method_after_first_write(chain)
        planner = install_planner(chain, sharing.PlanCognition)
        method_ref = str(seed.method_ref)
        method_id = str(seed.method_id)
        receipt1 = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt1.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        if not artifact_a:
            raise SystemExit("seed round wrote no work artifact: %r" % (seed.round_out,))
        chain.cycle.work_artifact_id = artifact_a
        v2_head = head_version(chain, artifact_a)
        handoff1 = seed.round_out.get("experience_handoff") or {}
        result_v2 = str(handoff1.get("progress_result_ref") or "")
        activity_ref = str((seed.round_out.get("step_receipt") or {}).get("activity_ref")
                           or seed.round_out.get("activity_ref") or "")
        report.update({
            "artifact_a": work_ref(artifact_a), "artifact_b": "", "method_id": method_id,
            "method_ref": method_ref, "v2_head": v2_head,
            "result_v2": result_v2, "handoff_v2": str(handoff1.get("handoff_id") or ""),
            "activity_ref": activity_ref,
            "seed_experience_id": str(seed.experience_id),
            "seed_outcome": seed.round_out.get("outcome"),
            "seed_checks_ok": all(ok for _n, ok, _d in harness._CHECKS),
        })

        # -- 同一 Activity 的第二次真实结果 + 一条有 owner 经历作证据的评价 ----------------
        stamp = next_instant()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=stamp)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate for A in P1: %r"
                             % (chain.runtime.candidate_materializer.build_candidate_set(
                                 observed_at=stamp).candidate_refs,))
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "第二段：同一 Activity 的第二个确证版本。",
                                "self_note": "stub: write A next version"}
        out_v3 = run_round(chain, planner, {"script": {
            "decision": "CONTINUE", "selected_candidate_id": candidate,
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "stub: the method helped",
                             "experience_ids": [seed.experience_id]},
            "short_rationale": "stub: continue A with the offered method",
        }}, trigger="evt:v05-restart:p1-continue", observed_at=stamp)
        v3_head = head_version(chain, artifact_a)
        handoff2 = out_v3.get("experience_handoff") or {}
        result_v3 = str(handoff2.get("progress_result_ref") or "")
        usage = out_v3.get("method_usage") or {}
        trial = out_v3.get("method_trial") or {}
        method_row = chain.cycle.practice.read_method(method_id)
        report.update({
            "v3_head": v3_head, "result_v3": result_v3,
            "handoff_v3": str(handoff2.get("handoff_id") or ""),
            "a_versions_p1": version_count(chain, artifact_a),
            "p1_continue_outcome": out_v3.get("outcome"),
            "p1_continue_decision": out_v3.get("decision"),
            "method_usage_version": str(usage.get("version_ref") or ""),
            "method_usage_refs": [str(ref) for ref in (usage.get("consequence_refs") or [])],
            "method_trial_state": str(trial.get("method_state") or ""),
            "method_trial_outcome": str(trial.get("outcome") or ""),
            "method_state_p1": str(method_row.get("method_state") or ""),
            "method_trial_count_p1": len(method_row.get("trials") or []),
            "method_head_p1": str(method_row.get("head_version_id") or ""),
            "writer_saw_method": bool(chain.client.prompts)
            and harness.METHOD_INSTRUCTION in channel_text(chain.client.prompts[-1]),
            "activity_after_v3": str((out_v3.get("step_receipt") or {}).get("activity_ref")
                                     or out_v3.get("activity_ref") or ""),
        })

        # -- 草稿 D1 绑定当前 head；合法 defer（原合同，不自动发送） ---------------------
        out_prepare = run_round(chain, planner,
                                {"op": "prepare", "text": D1_TEXT, "title": "刚写完那版"},
                                trigger="evt:v05-restart:p1-prepare-d1")
        prepared = out_prepare.get("sharing") or {}
        d1 = str(prepared.get("share_id") or "")
        d1_head = str(prepared.get("head_version_id") or "")
        owner_d1 = chain.cycle.sharing_service.read_share(d1, include_text=False) if d1 else {}
        d1_bound = str(((owner_d1.get("data") or {}).get("share") or {}).get("source", {})
                       .get("version_id") or "")
        out_defer = run_round(chain, planner, {"op": "defer", "target": d1},
                              trigger="evt:v05-restart:p1-defer-d1")
        deferred = out_defer.get("sharing") or {}
        # ``defer`` writes its own draft version, so the restart baseline is the head the
        # owner holds *after* the legal action, not the one returned by ``prepare``.
        d1_after_defer = lsc.read_draft(chain.cycle.sharing_service, d1)
        report.update({
            "d1": d1, "d1_head": d1_head, "d1_bound": d1_bound,
            "prepare_ok": prepared.get("ok"), "defer_ok": deferred.get("ok"),
            "prepare_outcome": str(out_prepare.get("outcome") or ""),
            "defer_outcome": str(out_defer.get("outcome") or ""),
            "d1_state_p1": str(deferred.get("state") or ""),
            "d1_head_after_defer": str(d1_after_defer.get("head_version_id") or ""),
            "d1_state_after_defer": str(d1_after_defer.get("state") or ""),
            "share_counts_p1": share_counts(chain),
        })

        # -- 作品 B：另一件真实作品，用来验证续写不串目标 --------------------------------
        b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品",
            content=B_BODY, grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="v05-restart:b-create:%s" % SUBJECT)
        if not getattr(b, "applied", False):
            raise SystemExit("artifact B was not created: %r" % (b,))
        b_id = str(b.artifact_id)
        b_head0 = head_version(chain, b_id)
        b_next = chain.port.commit_version(
            artifact_id=b_id, expected_head=b_head0,
            content=B_BODY + "\n第二段：B 的下一版。",
            grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="v05-restart:b-commit:%s" % SUBJECT)
        if not getattr(b_next, "applied", False):
            raise SystemExit("artifact B second version was not written: %r" % (b_next,))
        report.update({"artifact_b": work_ref(b_id), "b_head": head_version(chain, b_id),
                       "b_versions_p1": version_count(chain, b_id)})

        report.update({
            "handoff_count_p1": handoff_count(chain),
            "experience_count_p1": len(experience_ids(chain)),
            "handoff_ids_p1": sorted(
                str(key) for key in (handoff_state(chain).get("handoff_events_by_id") or {})),
            "integration_ids_p1": sorted(
                str(key) for key in (handoff_state(chain).get("integrations_by_id") or {})),
            "rounds": [dict(row) for row in sharing.ROUNDS],
        })
        return report
    finally:
        if planner is not None:
            report["live_heads"] = {
                "a": head_version(chain, report.get("artifact_a") or "") or "",
                "b": head_version(chain, report.get("artifact_b") or "") or "",
            }
        report.update(close_chain(chain))


def channel_text(prompt: dict) -> str:
    return "%s\n%s" % (prompt.get("system") or "", prompt.get("user") or "")


# ---------------------------------------------------------------------------------------------
# P2：不同 PID 重开同一根；纯读、续写、去重、stale 拒发、新草稿、一条 receipt、退休
# ---------------------------------------------------------------------------------------------


def phase_p2(config: dict) -> dict:
    import agency_decision_ag1 as ag1
    import life_integration_ag2 as ag2
    import life_sharing_context as lsc
    import life_sharing_service as lss
    import result_method_behavior_smoke as harness
    import v05_result_sharing_continuation_smoke as sharing

    root = Path(config["root"])
    artifact_a = str(config["artifact_a"])
    artifact_b = str(config["artifact_b"])
    method_id = str(config["method_id"])
    method_ref = str(config["method_ref"])
    d1 = str(config["d1"])
    handoff_v2 = str(config["handoff_v2"])
    result_v2 = str(config["result_v2"])
    v2_head = str(config["v2_head"])
    activity_ref_p1 = str(config.get("activity_ref") or "")

    chain = open_chain(root, "p2")
    planner = None
    report: dict = {"phase": "p2", "pid": os.getpid(), "root": str(root)}
    try:
        planner = install_planner(chain, sharing.PlanCognition)
        service = chain.cycle.sharing_service
        if not isinstance(service, lss.LifeSharingService):
            raise SystemExit("sharing service is not the real owner: %r" % (type(service),))
        chain.cycle.work_artifact_id = artifact_a

        # -- 启动基线：全部从真实 owner / AG-2 store 读回 --------------------------------
        d1_before = lsc.read_draft(service, d1)
        method_before = chain.cycle.practice.read_method(method_id)
        report.update({
            "a_head_at_start": head_version(chain, artifact_a),
            "a_versions_at_start": version_count(chain, artifact_a),
            "b_head_at_start": head_version(chain, artifact_b),
            "b_versions_at_start": version_count(chain, artifact_b),
            "d1_head_at_start": str(d1_before.get("head_version_id") or ""),
            "d1_state_at_start": str(d1_before.get("state") or ""),
            "method_state_at_start": str(method_before.get("method_state") or ""),
            "method_trials_at_start": len(method_before.get("trials") or []),
            "handoff_count_at_start": handoff_count(chain),
            "experience_count_at_start": len(experience_ids(chain)),
            "share_counts_at_start": share_counts(chain),
            "handoff_ids_at_start": sorted(
                str(key) for key in (handoff_state(chain).get("handoff_events_by_id") or {})),
            "integration_ids_at_start": sorted(
                str(key) for key in (handoff_state(chain).get("integrations_by_id") or {})),
            "d1_visible_at_start": any(
                str(row.get("share_id") or "") == d1
                for row in (chain.cycle.sharing_decision_context().get("shares") or [])),
        })

        # -- 第一次正式请求：纯读，不续作、不分享 --------------------------------------
        out_read = run_round(chain, planner, {"op": "none"},
                             trigger="evt:v05-restart:p2-read")
        view = latest_view(planner)
        d1_row_prompt = [row for row in (view.get("sharing") or {}).get("shares") or []
                         if row.get("share_id") == d1]
        method_row_prompt = [row for row in (view.get("practice") or {}).get("methods") or []
                             if row.get("method_ref") == method_ref]
        report.update({
            "read_outcome": out_read.get("outcome"),
            "read_llm_calls": out_read.get("llm_calls"),
            "read_a_head": head_version(chain, artifact_a),
            "read_a_versions": version_count(chain, artifact_a),
            "read_b_head": head_version(chain, artifact_b),
            "read_b_versions": version_count(chain, artifact_b),
            "read_handoff_count": handoff_count(chain),
            "read_experience_count": len(experience_ids(chain)),
            "read_share_counts": share_counts(chain),
            "read_d1_in_request": bool(d1_row_prompt),
            "read_d1_state": str((d1_row_prompt or [{}])[0].get("state") or ""),
            "read_d1_source_current": dict((d1_row_prompt or [{}])[0].get("source_current") or {}),
            "read_method_in_request": bool(method_row_prompt),
            "read_method_state": str((method_row_prompt or [{}])[0].get("method_state") or ""),
            "read_method_evaluation": dict((method_row_prompt or [{}])[0].get("evaluation") or {}),
            "read_saw_work_source": any(
                str(row.get("artifact_id") or "") == work_ref(artifact_a)
                for row in (view.get("sharing") or {}).get("sources") or []),
        })

        # -- 合法 CONTINUE 同一 Activity：新结果身份，不复用 v2 事件 ---------------------
        stamp = next_instant()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=stamp)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate in P2: %r"
                             % (chain.runtime.candidate_materializer.build_candidate_set(
                                 observed_at=stamp).candidate_refs,))
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "重启之后：她接着把同一件作品写下去。",
                                "self_note": "stub: continue after restart"}
        out_v4 = run_round(chain, planner, {"script": {
            "decision": "CONTINUE", "selected_candidate_id": candidate,
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "stub: still helps after restart",
                             "experience_ids": [config["seed_experience_id"]]},
            "short_rationale": "stub: continue the same work after restart",
        }}, trigger="evt:v05-restart:p2-continue", observed_at=stamp)
        v4_head = head_version(chain, artifact_a)
        handoff3 = out_v4.get("experience_handoff") or {}
        result_v4 = str(handoff3.get("progress_result_ref") or "")
        receipt4 = out_v4.get("step_receipt") or {}
        method_after_use = chain.cycle.practice.read_method(method_id)
        report.update({
            "continue_outcome": out_v4.get("outcome"),
            "continue_decision": out_v4.get("decision"),
            "continue_activity_ref": str(receipt4.get("activity_ref")
                                         or out_v4.get("activity_ref") or ""),
            "activity_same_as_p1": str(receipt4.get("activity_ref")
                                       or out_v4.get("activity_ref") or "") == activity_ref_p1,
            "v4_head": v4_head,
            "a_versions_after_continue": version_count(chain, artifact_a),
            "b_head_after_continue": head_version(chain, artifact_b),
            "b_versions_after_continue": version_count(chain, artifact_b),
            "result_v4": result_v4,
            "handoff_v4": str(handoff3.get("handoff_id") or ""),
            "handoff_count_after_continue": handoff_count(chain),
            "method_state_after_use": str(method_after_use.get("method_state") or ""),
            "method_trial_count_after_use": len(method_after_use.get("trials") or []),
            "writer_saw_method": bool(chain.client.prompts)
            and harness.METHOD_INSTRUCTION in channel_text(chain.client.prompts[-1]),
            "writer_prompt_count": len(chain.client.prompts),
        })

        # -- 去重：用既有 emit 接口重投 P1 的同一个确认结果 ------------------------------
        # confirmed context 从真实 owner 重读，不拿 P1 报告里的描述当事实。
        view_a = chain.port.read_artifact(artifact_a)
        read_head = str((view_a.get("head") or {}).get("version_id") or "")
        old = chain.port.read_version(artifact_a, v2_head)
        old_body = str((old or {}).get("content") or "")
        if old_body.lstrip().startswith("<!--"):
            end = old_body.find("-->")
            if end >= 0:
                old_body = old_body[end + len("-->"):]
        confirmed_v2 = {
            "artifact_id": "artifact:%s" % bare(artifact_a),
            "version_ref": "artifactversion:%s" % v2_head,
            "title": ((view_a.get("artifact") or {}).get("title") or None),
            "result_description": " ".join(old_body.split())[-240:],
            "outcome_confirmed": True,
        }
        state_before_replay = handoff_state(chain)
        integration_id = None
        for int_id, record in (state_before_replay.get("integrations_by_id") or {}).items():
            if str((record or {}).get("activity_ref") or "") == activity_ref_p1:
                integration_id = str(int_id)
                break
        if integration_id is None:
            raise SystemExit("no integration record for the P1 activity in the reopened store")
        replay = chain.runtime.coordinator.emit_experience_handoff(
            integration_id=integration_id, activity_id=activity_ref_p1,
            terminal_or_milestone_kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
            occurred_at=config.get("observed_at") or _BASE_TIME.isoformat(),
            artifact_refs=[confirmed_v2["artifact_id"], confirmed_v2["version_ref"]],
            owner_confirmed_context=confirmed_v2,
            progress_result_ref=result_v2)
        state_after_replay = handoff_state(chain)
        report.update({
            "replay_handoff_id": str(replay["handoff_event"].handoff_id),
            "replay_same_as_v2": str(replay["handoff_event"].handoff_id) == handoff_v2,
            "replay_idempotent": bool(replay.get("idempotent_replay")),
            "replay_reused_legacy": bool(replay.get("reused_legacy_handoff")),
            "replay_count_before": len(state_before_replay.get("handoff_events_by_id") or {}),
            "replay_count_after": len(state_after_replay.get("handoff_events_by_id") or {}),
            "replay_experience_count": len(experience_ids(chain)),
            "replay_writer_prompts": len(chain.client.prompts),
            "replay_a_head": head_version(chain, artifact_a),
            "replay_a_versions": version_count(chain, artifact_a),
        })

        # -- 旧稿 D1：真实请求显示 stale；发送被 owner 拒且零写 ------------------------
        stale_row = (lsc.read_draft(service, d1) or {})
        out_stale = run_round(chain, planner, {"op": "send_local", "target": d1},
                              trigger="evt:v05-restart:p2-stale-send")
        stale_prompt = [row for row in (latest_view(planner).get("sharing") or {})
                        .get("shares") or [] if row.get("share_id") == d1]
        stale_prompt_row = (stale_prompt or [{}])[0]
        report.update({
            "stale_source_current": dict(stale_row.get("source_current") or {}),
            "stale_text_available": stale_prompt_row.get("text_available"),
            "stale_text_chars": int(stale_prompt_row.get("text_chars") or 0),
        })
        stale = out_stale.get("sharing") or {}
        d1_after_stale = lsc.read_draft(service, d1)
        report.update({
            "stale_outcome": str(out_stale.get("outcome") or ""),
            "stale_ok": stale.get("ok"),
            "stale_status": str(stale.get("status") or ""),
            "stale_is_stale_source": str(stale.get("status") or "") == lss.STATUS_STALE_SOURCE,
            "stale_reason": str(stale.get("reason_code") or ""),
            "stale_delivered": stale.get("delivered"),
            "stale_head_after": str(d1_after_stale.get("head_version_id") or ""),
            "stale_state_after": str(d1_after_stale.get("state") or ""),
            "stale_receipt_after": d1_after_stale.get("receipt"),
            "stale_a_head_after": head_version(chain, artifact_a),
            "stale_b_head_after": head_version(chain, artifact_b),
        })

        # -- 显式 prepare D2 绑定新版本；send_local 恰好一条本地 receipt ---------------
        out_prepare2 = run_round(chain, planner,
                                 {"op": "prepare", "text": D2_TEXT, "title": "换版之后"},
                                 trigger="evt:v05-restart:p2-prepare-d2")
        prepared2 = out_prepare2.get("sharing") or {}
        d2 = str(prepared2.get("share_id") or "")
        d2_head = str(prepared2.get("head_version_id") or "")
        owner_d2 = service.read_share(d2, include_text=False) if d2 else {}
        d2_bound = str(((owner_d2.get("data") or {}).get("share") or {}).get("source", {})
                       .get("version_id") or "")
        out_send = run_round(chain, planner, {"op": "send_local", "target": d2},
                             trigger="evt:v05-restart:p2-send-d2")
        sent = out_send.get("sharing") or {}
        receipt2 = sent.get("receipt") or {}
        owner_receipt = (service.read_share(d2).get("data") or {}).get("receipt") or {}
        d2_after_send = lsc.read_draft(service, d2)
        delivered_head = str(d2_after_send.get("head_version_id") or "")
        replay_send = service.apply_action(share_id=d2, action="send_local",
                                           expected_head=delivered_head)
        inbox = (service.list_shares(include_text=False).get("data") or {}).get("inbox") or {}
        report.update({
            "d2": d2, "d2_head": d2_head, "d2_bound": d2_bound,
            "d2_head_after_send": delivered_head,
            "prepare2_ok": prepared2.get("ok"),
            "prepare2_outcome": str(out_prepare2.get("outcome") or ""),
            "send_outcome": str(out_send.get("outcome") or ""),
            "d1_head_after_prepare2": str(lsc.read_draft(service, d1).get("head_version_id") or ""),
            "send_ok": sent.get("ok"),
            "send_state": str(sent.get("state") or ""),
            "send_delivery_count": sent.get("delivery_count"),
            "send_delivery_id": str(receipt2.get("delivery_id") or ""),
            "owner_receipt_id": str(owner_receipt.get("delivery_id") or ""),
            "receipt_flags": {
                "local_only": owner_receipt.get("local_only"),
                "external_channel_sent": owner_receipt.get("external_channel_sent"),
                "external_delivery_confirmed": owner_receipt.get("external_delivery_confirmed"),
                "read_by_audience": owner_receipt.get("read_by_audience"),
                "acknowledged": owner_receipt.get("acknowledged"),
                "understood": owner_receipt.get("understood"),
            },
            "repeat_send_reason": str(replay_send.get("reason_code") or ""),
            "repeat_send_receipt": str((replay_send.get("data") or {}).get("receipt", {})
                                       .get("delivery_id") or ""),
            "inbox_delivered_count": int(inbox.get("delivered_count") or 0),
            "share_counts_after_send": share_counts(chain),
        })

        # -- 同一 prepare 意图重放：既有 rejected 语义，不产生第二份草稿 --------------
        frozen = service._now()
        replaying = lss.LifeSharingService(service.projects, clock=lambda: frozen)
        total_before_intent = int(share_counts(chain).get("total") or 0)
        replay_first = replaying.prepare_share(
            source_artifact_id=artifact_a, share_text="重放用的另一句。", title="重放")
        replay_second = replaying.prepare_share(
            source_artifact_id=artifact_a, share_text="重放用的另一句。", title="重放")
        total_after_intent = int(share_counts(chain).get("total") or 0)
        report.update({
            "replay_prepare_first": str(replay_first.get("status") or ""),
            "replay_prepare_second_ok": replay_second.get("ok"),
            "replay_prepare_second_reason": str(replay_second.get("reason_code") or ""),
            "replay_prepare_total_delta": total_after_intent - total_before_intent,
        })

        # -- 退休：真实 owner CAS → 旧 ref 被 AG-1 拒，不提交 Decision -----------------
        head_before_retire = str(chain.cycle.practice.read_method(method_id)["head_version_id"])
        retired = chain.cycle.practice.service.retire_method(
            method_id, expected_head=head_before_retire,
            operation_key="practice:v05-restart:retire")
        report.update({
            "retire_ok": retired.get("ok"),
            "retire_state": str((retired.get("data") or {}).get("method_state") or ""),
            "retire_content_kept": bool((retired.get("data") or {}).get("content_kept")),
        })
        refused = run_round(chain, planner, {"op": "none", "script": {
            "decision": "NO_ACTION", "selected_candidate_id": None,
            "reason_codes": ["NO_ACTION_CHOSEN"], "deferred_candidate_ids": [],
            "method_ref": method_ref,
            "short_rationale": "stub: still naming the retired method"}},
            trigger="evt:v05-restart:p2-retired-ref")
        report.update({
            "retired_ref_outcome": refused.get("outcome"),
            "retired_ref_detail": str(refused.get("detail") or ""),
            "retired_ref_decision": refused.get("decision"),
            "retired_ref_invented": str(refused.get("detail") or "")
            == ag1.FAILURE_CLASS_INVENTED_METHOD_REF,
        })
        legal = run_round(chain, planner, {"op": "none", "script": {
            "decision": "NO_ACTION", "selected_candidate_id": None,
            "reason_codes": ["NO_ACTION_CHOSEN"], "deferred_candidate_ids": [],
            "short_rationale": "stub: legal quiet round after retirement"}},
            trigger="evt:v05-restart:p2-legal-quiet")
        writing = chain.cycle.practice_writing_context("")
        writing_blob = json.dumps(writing, ensure_ascii=False) if writing else ""
        report.update({
            "legal_outcome": legal.get("outcome"),
            "legal_decision": legal.get("decision"),
            "legal_route": str(legal.get("decision_route") or ""),
            "writer_carries_retired_instruction": harness.METHOD_INSTRUCTION in writing_blob,
            "method_final_state": str(chain.cycle.practice.read_method(method_id)
                                      .get("method_state") or ""),
            "final_a_head": head_version(chain, artifact_a),
            "final_a_versions": version_count(chain, artifact_a),
            "final_b_head": head_version(chain, artifact_b),
            "final_b_versions": version_count(chain, artifact_b),
            "final_handoff_count": handoff_count(chain),
            "final_experience_count": len(experience_ids(chain)),
            "final_share_counts": share_counts(chain),
            "final_handoff_ids": sorted(
                str(key) for key in (handoff_state(chain).get("handoff_events_by_id") or {})),
            "d1_state_final": str(lsc.read_draft(service, d1).get("state") or ""),
            "d2_state_final": str(lsc.read_draft(service, d2).get("state") or ""),
            "activity_ref_final": str((out_v4.get("step_receipt") or {}).get("activity_ref")
                                      or out_v4.get("activity_ref") or ""),
        })
        return report
    finally:
        report.update(close_chain(chain))


# ---------------------------------------------------------------------------------------------
# P3：可选纯读重开；不跑 Decision、不写任何东西
# ---------------------------------------------------------------------------------------------


def phase_p3(config: dict) -> dict:
    import life_sharing_context as lsc

    root = Path(config["root"])
    chain = open_chain(root, "p3")
    report: dict = {"phase": "p3", "pid": os.getpid(), "root": str(root)}
    try:
        artifact_a = str(config["artifact_a"])
        artifact_b = str(config["artifact_b"])
        d1 = str(config["d1"])
        d2 = str(config["d2"])
        state = handoff_state(chain)
        report.update({
            "a_head": head_version(chain, artifact_a),
            "a_versions": version_count(chain, artifact_a),
            "b_head": head_version(chain, artifact_b),
            "b_versions": version_count(chain, artifact_b),
            "handoff_count": len(state.get("handoff_events_by_id") or {}),
            "handoff_ids": sorted(str(key)
                                  for key in (state.get("handoff_events_by_id") or {})),
            "experience_count": len(experience_ids(chain)),
            "share_counts": share_counts(chain),
            "d1_head": str(lsc.read_draft(chain.cycle.sharing_service, d1)
                           .get("head_version_id") or ""),
            "d1_state": str(lsc.read_draft(chain.cycle.sharing_service, d1).get("state") or ""),
            "d2_head": str(lsc.read_draft(chain.cycle.sharing_service, d2)
                           .get("head_version_id") or ""),
            "d2_state": str(lsc.read_draft(chain.cycle.sharing_service, d2).get("state") or ""),
            "d2_receipt": str((lsc.read_draft(chain.cycle.sharing_service, d2)
                               .get("receipt") or {}).get("delivery_id") or ""),
            "method_state": str(chain.cycle.practice.read_method(config["method_id"])
                                .get("method_state") or ""),
            "cognition_calls": 0,
            "writer_prompts": 0,
        })
        return report
    finally:
        report.update(close_chain(chain))


# ---------------------------------------------------------------------------------------------
# 父调度器：只调度与断言，不持有 runtime / owner
# ---------------------------------------------------------------------------------------------


def _run_phase(phase: str, config_path: Path, *, timeout: float = 900.0) -> dict:
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--phase", phase,
         "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if completed.returncode != 0:
        print("  %s stdout tail: %s" % (phase, completed.stdout[-1200:]))
        print("  %s stderr tail: %s" % (phase, completed.stderr[-1600:]))
        raise SystemExit("%s exited %d" % (phase, completed.returncode))
    lines = [line for line in completed.stdout.splitlines()
             if line.startswith("RESTART_REPORT ")]
    if not lines:
        print("  %s stdout tail: %s" % (phase, completed.stdout[-1200:]))
        raise SystemExit("%s printed no RESTART_REPORT" % phase)
    return json.loads(lines[-1].split(" ", 1)[1])


def _write_config(root: Path, payload: dict) -> Path:
    path = Path(tempfile.mkdtemp(prefix="v05-restart-config-")) / "config.json"
    path.write_text(json.dumps(dict(payload, root=str(root)), ensure_ascii=False),
                    encoding="utf-8")
    return path


def _run_chain() -> dict:
    root = prepare_fixture()
    print("fixture root:", root)
    p1 = _run_phase("p1", _write_config(root, {}))
    p2 = _run_phase("p2", _write_config(root, {
        "artifact_a": p1["artifact_a"], "artifact_b": p1["artifact_b"],
        "method_id": p1["method_id"], "method_ref": p1["method_ref"],
        "d1": p1["d1"], "handoff_v2": p1["handoff_v2"],
        "result_v2": p1["result_v2"], "v2_head": p1["v2_head"],
        "activity_ref": p1["activity_ref"],
        "seed_experience_id": p1.get("seed_experience_id") or "",
    }))
    p3 = _run_phase("p3", _write_config(root, {
        "artifact_a": p1["artifact_a"], "artifact_b": p1["artifact_b"],
        "d1": p1["d1"], "d2": p2["d2"], "method_id": p1["method_id"],
    }))
    return {"root": str(root), "p1": p1, "p2": p2, "p3": p3}


def _assert_chain(reports: dict) -> None:
    p1, p2, p3 = reports["p1"], reports["p2"], reports["p3"]

    # -- 双 PID / 同根 / 正常关停 ---------------------------------------------------
    check("P1/P2/P3 ran in three different PIDs",
          len({p1["pid"], p2["pid"], p3["pid"]}) == 3,
          [p1["pid"], p2["pid"], p3["pid"]])
    check("all phases reopened the same fixture root",
          p1["root"] == p2["root"] == p3["root"] == reports["root"],
          [p1["root"], p2["root"], p3["root"]])
    check("every phase released its runtime lease and stopped its own owner",
          all(row.get("closed") is True and row.get("service_stopped") is True
              for row in (p1, p2, p3)),
          {key: (p1.get(key), p2.get(key), p3.get(key))
           for key in ("closed", "service_stopped")})
    check("...after draining in-flight requests and letting the owner's own socket guard clean up",
          all(row.get("service_drained") is True
              and row.get("service_socket_handling") in ("STALE_SOCKET_REMOVED",
                                                         "SOCKET_PATH_FREE")
              for row in (p1, p2, p3)),
          {key: (p1.get(key), p2.get(key), p3.get(key))
           for key in ("service_drained", "service_socket_handling")})

    # -- P1：真实作品、方法评价、草稿、B ---------------------------------------------
    check("P1 wrote a real owner version through the formal chain",
          p1["seed_checks_ok"] is True and p1["seed_outcome"] == "ACTED"
          and bool(p1["v2_head"]) and int(p1["a_versions_p1"]) >= 2, p1.get("seed_outcome"))
    check("P1's first result carries the owner's result-level identity",
          p1["result_v2"] == "artifact:%s@artifactversion:%s"
          % (bare(p1["artifact_a"]), p1["v2_head"]), p1["result_v2"])
    check("P1 continued the same Activity into a second real result",
          p1["p1_continue_outcome"] == "ACTED" and p1["p1_continue_decision"] == "CONTINUE"
          and p1["v3_head"] and p1["v3_head"] != p1["v2_head"]
          and int(p1["a_versions_p1"]) >= 3,
          {"outcome": p1["p1_continue_outcome"], "v2": p1["v2_head"], "v3": p1["v3_head"]})
    check("P1's method verdict binds the version this round offered and a real receipt",
          p1["method_usage_version"] and p1["method_usage_version"] != ""
          and any(str(ref).startswith("artifactversion:")
                  for ref in p1["method_usage_refs"]),
          {"version": p1["method_usage_version"], "refs": p1["method_usage_refs"]})
    check("P1's typed review really moved the owner's method to TRIAL",
          p1["method_state_p1"] == "TRIAL" and p1["method_trial_state"] == "TRIAL"
          and int(p1["method_trial_count_p1"]) >= 1,
          {"state": p1["method_state_p1"], "trials": p1["method_trial_count_p1"]})
    check("P1's writer request carried the offered method instruction",
          p1["writer_saw_method"] is True)
    check("P1 prepared D1 on the current head and deferred it legally",
          p1["prepare_ok"] is True and p1["defer_ok"] is True
          and p1["d1_bound"] == p1["v3_head"] and bool(p1["d1_head"])
          and p1["d1_state_after_defer"] == "DEFERRED",
          {"prepare_ok": p1["prepare_ok"], "defer_ok": p1["defer_ok"],
           "bound": p1["d1_bound"], "v3": p1["v3_head"],
           "draft_head": p1["d1_head"], "state": p1["d1_state_after_defer"]})
    check("P1's separate work B has its own two versions",
          bool(p1["artifact_b"]) and int(p1["b_versions_p1"]) == 2, p1.get("b_versions_p1"))

    # -- P2：纯读阶段零写 -----------------------------------------------------------
    check("P2's first formal request read back the same store without writing",
          p2["read_outcome"] == "NO_ACTION"
          and p2["read_a_head"] == p1["v3_head"]
          and p2["read_a_versions"] == p1["a_versions_p1"]
          and p2["read_b_head"] == p1["b_head"]
          and p2["read_b_versions"] == p1["b_versions_p1"]
          and p2["read_handoff_count"] == p1["handoff_count_p1"]
          and p2["read_experience_count"] == p1["experience_count_p1"]
          and p2["read_share_counts"] == p2["share_counts_at_start"],
          {key: p2.get(key) for key in
           ("read_outcome", "read_a_head", "read_a_versions", "read_handoff_count")})
    check("...the request itself saw the deferred draft and the TRIAL method",
          p2["read_d1_in_request"] is True and p2["read_d1_state"] == "DEFERRED"
          and p2["read_method_in_request"] is True and p2["read_method_state"] == "TRIAL"
          and p2["read_saw_work_source"] is True,
          {"d1": p2["read_d1_state"], "method": p2["read_method_state"]})
    check("...including the bounded recent explicit evaluation",
          p2["read_method_evaluation"].get("available") is True
          and p2["read_method_evaluation"].get("outcome") == "HELPED"
          and int(p2["read_method_evaluation"].get("evidence_count") or 0) >= 1,
          p2["read_method_evaluation"])
    check("...and it did not auto-continue or auto-share",
          p2["read_llm_calls"] == 1 and p2["read_handoff_count"] == p1["handoff_count_p1"]
          and p2["read_share_counts"] == p2["share_counts_at_start"], p2["read_llm_calls"])

    # -- P2：续写同一 Activity、不写 B ----------------------------------------------
    check("P2's new Decision continued the same Activity after the restart",
          p2["continue_outcome"] == "ACTED" and p2["continue_decision"] == "CONTINUE"
          and p2["activity_same_as_p1"] is True
          and p2["v4_head"] and p2["v4_head"] != p1["v3_head"],
          {"activity": p2.get("continue_activity_ref"),
           "p1": p1.get("activity_ref"), "v4": p2.get("v4_head")})
    check("the other work B was not touched by the continuation",
          p2["b_head_after_continue"] == p1["b_head"]
          and p2["b_versions_after_continue"] == p1["b_versions_p1"],
          {"b": p2.get("b_head_after_continue")})
    check("the new result got a new result identity, not the old event",
          p2["result_v4"] == "artifact:%s@artifactversion:%s"
          % (bare(p1["artifact_a"]), p2["v4_head"])
          and p2["handoff_v4"] not in (p1["handoff_v2"], p1["handoff_v3"])
          and p2["handoff_count_after_continue"] == p1["handoff_count_p1"] + 1,
          {"result": p2["result_v4"], "handoff": p2["handoff_v4"]})
    check("P2's writer request carried the legal offered method",
          p2["writer_saw_method"] is True)
    check("the typed review after restart kept a real trial on the owner",
          p2["method_state_after_use"] == "TRIAL"
          and int(p2["method_trial_count_after_use"]) >= int(p1["method_trial_count_p1"]) + 1,
          {"state": p2["method_state_after_use"], "trials": p2["method_trial_count_after_use"]})

    # -- P2：同结果重投不增 ---------------------------------------------------------
    check("replaying P1's same confirmed result keeps the same handoff id",
          p2["replay_same_as_v2"] is True and p2["replay_handoff_id"] == p1["handoff_v2"],
          {"replay": p2["replay_handoff_id"], "original": p1["handoff_v2"]})
    check("...and it neither added an event nor replayed a Decision/Action/writer",
          p2["replay_count_after"] == p2["replay_count_before"]
          and p2["replay_a_head"] == p2["v4_head"]
          and p2["replay_a_versions"] == p2["a_versions_after_continue"]
          and p2["replay_experience_count"] == p1["experience_count_p1"] + 1
          and p2["replay_writer_prompts"] == p2["writer_prompt_count"],
          {"before": p2["replay_count_before"], "after": p2["replay_count_after"],
           "experiences": p2["replay_experience_count"],
           "prompts": [p2["replay_writer_prompts"], p2["writer_prompt_count"]]})

    # -- P2：旧稿 stale 拒发零写 ----------------------------------------------------
    check("the old draft is honestly reported stale after the source moved",
          p2["stale_source_current"].get("stale") is True
          and p2["stale_source_current"].get("matches_recorded") is False
          and p2["stale_source_current"].get("reason_code") == "SOURCE_REVISION_CHANGED"
          and p2["stale_text_available"] is True and int(p2["stale_text_chars"]) > 0,
          p2["stale_source_current"])
    check("sending the stale draft is refused by the owner with zero writes",
          p2["stale_ok"] is False and p2["stale_is_stale_source"] is True
          and p2["stale_delivered"] is False
          and p2["stale_head_after"] == p2["d1_head_at_start"]
          and p2["stale_state_after"] == "DEFERRED"
          and p2["stale_receipt_after"] is None
          and p2["stale_a_head_after"] == p2["v4_head"]
          and p2["stale_b_head_after"] == p1["b_head"],
          {key: p2.get(key) for key in
           ("stale_status", "stale_reason", "stale_state_after")})

    # -- P2：新草稿 + 一条本地 receipt ----------------------------------------------
    check("the explicit new prepare binds the new version",
          p2["prepare2_ok"] is True and p2["d2_bound"] == p2["v4_head"]
          and bool(p2["d2"]) and p2["d2"] != p1["d1"], p2.get("d2_bound"))
    check("the old draft was not rebound or rewritten by the new prepare",
          p2["d1_head_after_prepare2"] == p2["d1_head_at_start"],
          {"after": p2.get("d1_head_after_prepare2"),
           "at_start": p2.get("d1_head_at_start")})
    check("send_local produced exactly one local receipt",
          p2["send_ok"] is True and p2["send_delivery_count"] == 1
          and bool(p2["send_delivery_id"])
          and p2["send_delivery_id"] == p2["owner_receipt_id"], p2.get("send_delivery_id"))
    check("nothing external / read / acknowledged / understood is claimed",
          p2["receipt_flags"] == {"local_only": True, "external_channel_sent": False,
                                  "external_delivery_confirmed": False,
                                  "read_by_audience": False, "acknowledged": False,
                                  "understood": False}, p2["receipt_flags"])
    check("a repeated send keeps the one receipt",
          p2["repeat_send_receipt"] == p2["send_delivery_id"]
          and p2["inbox_delivered_count"] == 1, p2.get("repeat_send_reason"))
    check("the same prepare intent never mints a second draft",
          p2["replay_prepare_first"] in ("applied", "already_applied")
          and p2["replay_prepare_second_ok"] is False
          and int(p2["replay_prepare_total_delta"]) == 1,
          {key: p2.get(key) for key in
           ("replay_prepare_first", "replay_prepare_second_ok",
            "replay_prepare_second_reason", "replay_prepare_total_delta")})

    # -- P2：退休边界 ---------------------------------------------------------------
    check("the real owner's CAS retirement kept the document",
          p2["retire_ok"] is True and p2["retire_state"] == "RETIRED"
          and p2["retire_content_kept"] is True, p2.get("retire_state"))
    check("naming the retired handle is refused with INVENTED_METHOD_REF and no Decision",
          p2["retired_ref_invented"] is True
          and p2["retired_ref_outcome"] == "DECISION_FAILED"
          and p2["retired_ref_decision"] is None,
          {"outcome": p2.get("retired_ref_outcome"), "detail": p2.get("retired_ref_detail")})
    check("a legal Decision without the retired ref still commits",
          p2["legal_outcome"] == "NO_ACTION" and p2["legal_decision"] == "NO_ACTION",
          p2.get("legal_outcome"))
    check("the writer view carries no retired instruction",
          p2["writer_carries_retired_instruction"] is False)

    # -- P3：纯读重开稳定 -----------------------------------------------------------
    check("P3 reopened the same root read-only and saw the same final state",
          p3["a_head"] == p2["v4_head"] and p3["a_versions"] == p2["final_a_versions"]
          and p3["b_head"] == p1["b_head"] and p3["b_versions"] == p1["b_versions_p1"]
          and p3["handoff_count"] == p2["final_handoff_count"]
          and p3["handoff_ids"] == p2["final_handoff_ids"]
          and p3["experience_count"] == p2["final_experience_count"]
          and p3["d1_head"] == p2["d1_head_at_start"] and p3["d1_state"] == "DEFERRED"
          and p3["d2_head"] == p2["d2_head_after_send"]
          and p3["d2_state"] == "LOCAL_DELIVERED"
          and p3["d2_receipt"] == p2["send_delivery_id"]
          and p3["method_state"] == "RETIRED"
          and p3["cognition_calls"] == 0 and p3["writer_prompts"] == 0,
          {"a_head": p3.get("a_head"), "v4": p2.get("v4_head"),
           "a_versions": p3.get("a_versions"), "p2_versions": p2.get("final_a_versions"),
           "b_head": p3.get("b_head"), "b_versions": p3.get("b_versions"),
           "handoffs": [p3.get("handoff_count"), p2.get("final_handoff_count")],
           "experiences": [p3.get("experience_count"), p2.get("final_experience_count")],
           "d1_head": [p3.get("d1_head"), p2.get("d1_head_at_start")],
           "d2_head": [p3.get("d2_head"), p2.get("d2_head")],
           "d2_receipt": [p3.get("d2_receipt"), p2.get("send_delivery_id")],
           "method_state": p3.get("method_state")})

    # -- 安全表：只有结构，没有正文 --------------------------------------------------
    print("\nSAFE TABLE")
    print(json.dumps({
        "pids": [p1["pid"], p2["pid"], p3["pid"]],
        "root": reports["root"],
        "artifact_a": p1["artifact_a"],
        "artifact_b": p1["artifact_b"],
        "versions": {"p1_a": p1["a_versions_p1"], "p2_a": p2["final_a_versions"],
                     "b": p1["b_versions_p1"]},
        "heads": {"v2": p1["v2_head"], "v3": p1["v3_head"], "v4": p2["v4_head"]},
        "handoffs": {"p1": p1["handoff_count_p1"], "final": p2["final_handoff_count"]},
        "d1": {"share_id": p1["d1"], "head": p1["d1_head"], "final_state": p2["d1_state_final"]},
        "d2": {"share_id": p2["d2"], "head": p2["d2_head"], "delivery": p2["send_delivery_id"]},
        "receipt_counts": p2["final_share_counts"],
        "shutdown": {phase: {"drained": row.get("service_drained"),
                             "socket_handling": row.get("service_socket_handling")}
                     for phase, row in (("p1", p1), ("p2", p2), ("p3", p3))},
    }, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("p1", "p2", "p3"))
    parser.add_argument("--config")
    args = parser.parse_args(argv)

    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    if args.phase:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        runner = {"p1": phase_p1, "p2": phase_p2, "p3": phase_p3}[args.phase]
        report = runner(config)
        print("RESTART_REPORT " + json.dumps(report, ensure_ascii=False, default=str))
        return 0

    try:
        reports = _run_chain()
    except BaseException as exc:  # noqa: BLE001 - the chain's verdict is printed, not hidden
        print("CHAIN ERROR:", type(exc).__name__, str(exc)[:600])
        print("RESULT: ERROR (chain aborted)")
        return 1
    _assert_chain(reports)
    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
