#!/usr/bin/env python3
"""同作品跨重启续作：正式输入链在真实 owner / runtime / writer 上到底送了什么。

这份文件补的是「证据缺口」，不是产品功能：

* P1 在自己的 OS 进程里用**正式** ``START`` / ``CONTINUE`` 把同一件作品写成两版（第二版
  ``revise_tail``，所以 head 里只剩第二版正文），正规 ``close`` 后退出。
* P2 在**另一个真实 PID**（unittest 进程）里重新装配同一个 scratch 根：暂停期间 0 请求；
  解除后脚本 Decision 明确 ``CONTINUE``（脚本，不是主体真实选择）。真实
  ``ModelContinuationWriter`` → ``AgencyModelClient`` → 脚本 transport 把请求发出去，
  测试在自己的进程内把 owner 确证的当前 head 与实际收到的 payload 逐项对齐。

四个聚合 case：

1. 跨双 PID 的当前版本/正文/旧版摘录来源；错误作品诱饵不得出现。
2. 同起点合法 ``no_change``：正式决定身份保留、owner 零写、不是模型 ``NO_ACTION``、
   ``self_note`` 不进任何落盘读面；随后合法新内容用正式 CAS 写出同 Activity 的新版本。
3. 内容等待边界上另一个 owner writer 推进 head：旧 ``expected_head`` 被 CAS 拒，
   不覆盖新 head、不裸重发；变化仍绑在同一作品/Activity。
4. 默认兼容与隐私：writer 的 cap 实参保留原值，transport 实收同一组值；
   ``self_note`` 不进报告/读面，合成正文仍由正规 owner 提交。

真实件：真实 Supply owner（AF_UNIX）、``ProductionLifeRuntime``、AG-0/AG-1/AG-2/AR-0、
``ModelContinuationWriter`` + ``AgencyModelClient`` + 脚本 transport。替身只有 Decision
与内容回答（脚本，明示），不调用真实 provider。沿用 G1 的离线硬禁网（AF_INET/AF_INET6/
DNS/create_connection 拒绝，AF_UNIX 放行），父进程与 P1 子进程各自装守卫并各自自检。

Run (POSIX/WSL)::

    python -B -m unittest tests.test_restart_writer_input_chain
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

#: 每一步 400 分钟：越过既有 6 小时静默 TTL；这是夹具时钟，不是自然日。
BASE_MOMENT = datetime.fromisoformat("2026-11-10T09:00:00+00:00")
STEP = 400

#: 合成哨兵：只用于证明"哪一版正文真的被送出去了"，不是任何真实私文。
V1_SENTINEL = "NRW-RW-V1-e3b1 第一版正文，第二版里已经不再出现。"
V2_SENTINEL = "NRW-RW-V2-9d4c head 里的正文，续写请求必须看到这一版。"
V3_SENTINEL = "NRW-RW-V3-2a77 重启之后再接一段。"
DECOY_SENTINEL = "NRW-RW-DECOY-c7a2 另一件作品的正文，不该进入续写请求。"

CASE2_V1 = "NRW-RW-C2-V1-6f0a 起点版本。"
CASE2_BODY = "NRW-RW-C2-BODY-41be 决定 CONTINUE 之后真正写出的一段。"
CASE2_SELF_NOTE = "NRW-RW-C2-SELFNOTE-9e17 这句话只属于模型回答，不该落盘。"

CASE3_MODEL_BODY = "NRW-RW-C3-MODEL-5d33 内容等待期间模型给出的正文。"
CASE3_OTHER_BODY = "NRW-RW-C3-OTHER-b8a4 等待期间另一个合法 writer 写下的正文。"

CASE4_V1 = "NRW-RW-C4-V1-77c0 起点版本。"
CASE4_BODY = "NRW-RW-C4-BODY-0af2 默认 cap 下写出的一段。"
CASE4_SELF_NOTE = "NRW-RW-C4-SELFNOTE-3cc9 只应留在进程内。"


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


# ---------------------------------------------------------------------------------------------
# 脚本 transport：真实 AgencyModelClient 会把它当成 provider 路径
# ---------------------------------------------------------------------------------------------


class ScriptedTransport:
    """Records exactly what the real client sent; replies with a scripted JSON edit."""

    backend = "scripted-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {"kind": "append_paragraph", "text": ""}
        self.prompts: list[dict[str, str]] = []
        self.calls: list[dict[str, Any]] = []
        self.on_complete: Any = None

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.prompts.append({"system": system_prompt, "user": user_prompt})
        self.calls.append({"timeout_s": float(timeout_s),
                           "max_output_tokens": int(max_output_tokens)})
        if self.on_complete is not None:
            hook, self.on_complete = self.on_complete, None
            hook()
        return ModelCallResult(purpose="chat", provider="scripted", model="scripted",
                               text=json.dumps(self.payload, ensure_ascii=False),
                               latency_ms=1)

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "calls": len(self.calls)}


# ---------------------------------------------------------------------------------------------
# 隔离装配：真实 Supply owner + ProductionLifeRuntime + 正式 runner
# ---------------------------------------------------------------------------------------------


def open_chain(root: Path | str, tag: str) -> Any:
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from agency_model_client import AgencyModelClient
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter
    from result_method_behavior_smoke import ScriptedCognition
    import v05_restart_continuation_smoke as restart

    root = Path(root)
    services = restart._start_services(root / "supply")
    runtime = None
    try:
        instance = root / "instance"
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        state_root = odl.life_state_root(instance)
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
        transport = ScriptedTransport()
        # Explicit config so AgencyModelClient never reads a provider file or the environment;
        # the injected transport *is* the provider path and never opens a socket.
        client = AgencyModelClient(config={"provider": "scripted", "model": "scripted"},
                                   transport=transport, purpose="artifact_edit",
                                   timeout_s=20.0, max_output_tokens=512)
        # Same constructor call as assembly._model_writer: the cap under test is the product one.
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
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=consumer)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root, writer=writer, enabled=True)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        if (assembled.get("practice") or {}).get("status") != "READY":
            raise SystemExit("practice workspace not READY: %r"
                             % (assembled.get("practice"),))
        return SimpleNamespace(
            root=root, tag=tag, services=services, runtime=runtime,
            cycle=assembled["cycle"], runner=assembled["runner"],
            cognition=cognition, planner=None, transport=transport, client=client,
            writer=writer, data=data, env=env, port=port,
            memory_port=memory_port, evidence_db=evidence_db, controls_db=controls_db,
            state_root=state_root)
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:  # noqa: BLE001 - printed, not hidden
                pass
        try:
            restart._stop_services(services)
        except Exception as exc:  # noqa: BLE001
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        raise


def close_chain(chain: Any) -> dict:
    import v05_restart_continuation_smoke as restart

    return restart.close_chain(chain)


def install_planner(chain: Any) -> Any:
    import v05_result_sharing_continuation_smoke as sharing

    planner = sharing.PlanCognition()
    chain.planner = planner
    chain.cognition.invoke = planner.invoke
    return planner


def run_window(chain: Any, minutes: int) -> Any:
    return chain.runner.run_due(act=True, now=moment(minutes))


# ---------------------------------------------------------------------------------------------
# 正式轮助手（真实候选 + 脚本 Decision + 真实 owner 写入）
# ---------------------------------------------------------------------------------------------


def start_candidate(chain: Any, observed_at: str) -> str:
    options = chain.cycle.project_options()
    wanted = [row for row in options if not bool(row.get("review"))]
    if not wanted:
        raise SystemExit("no project option on offer: %r" % (options,))
    option_ref = str(wanted[0]["option_ref"])
    refs = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at).candidate_refs
    for ref in refs:
        payload = chain.runtime.candidate_read.get_candidate(str(ref)) or {}
        if str(payload.get("source_ref")) == option_ref:
            return str(ref)
    raise SystemExit("the project option never became a candidate")


def seed_first_write(chain: Any, minutes: int, text: str) -> SimpleNamespace:
    import result_method_behavior_smoke as harness

    material_id, revision = harness.import_material(chain)
    harness.write_question(chain, material_id, revision)
    chain.transport.payload = {"kind": "append_paragraph", "text": text,
                               "self_note": "stub: write first version"}
    observed = stamp(minutes)
    candidate = start_candidate(chain, observed)
    chain.planner.plan = [{"script": {"decision": "START",
                                      "selected_candidate_id": candidate,
                                      "deferred_candidate_ids": []}}]
    wake = run_window(chain, minutes)
    created = dict(getattr(chain.cycle, "last_project_created", None) or {})
    artifact_id = str(created.get("artifact_id") or "")
    chain.cycle.work_artifact_id = artifact_id or chain.cycle.work_artifact_id
    return SimpleNamespace(wake=wake, artifact_id=artifact_id,
                           material_id=material_id, revision=revision)


def continue_candidate(chain: Any, artifact_id: str, *, observed_at: str):
    import v05_result_sharing_continuation_smoke as sharing

    return sharing.continue_candidate(chain, artifact_id, observed_at=observed_at)


def continue_round(chain: Any, artifact_id: str, minutes: int,
                   payload: Mapping[str, Any]) -> Any:
    observed = stamp(minutes)
    candidate = continue_candidate(chain, artifact_id, observed_at=observed)
    if not candidate:
        raise SystemExit("no exact CONTINUE candidate for %r at %s"
                         % (artifact_id, observed))
    chain.transport.payload = dict(payload)
    chain.planner.plan = [{"script": {"decision": "CONTINUE",
                                      "selected_candidate_id": candidate,
                                      "deferred_candidate_ids": []}}]
    wake = run_window(chain, minutes)
    return SimpleNamespace(wake=wake, candidate=candidate)


# ---------------------------------------------------------------------------------------------
# 安全读面（只有 id / 版本号 / 布尔 / 计数；不打印正文与提示词）
# ---------------------------------------------------------------------------------------------


def owner_work(chain: Any, artifact_id: str) -> dict[str, Any]:
    point = chain.port.resume_point(str(artifact_id))
    if point is None:
        return {}
    return {"head": str(getattr(point, "head_version_id", "") or ""),
            "version": int(getattr(point, "version_number", 0) or 0),
            "lifecycle": str(getattr(point, "lifecycle", "") or "")}


def owner_versions(chain: Any, artifact_id: str) -> int:
    return int(chain.port.list_versions(str(artifact_id), limit=50)["total_versions"])


def activity_ref_of(chain: Any, wake: Any) -> str:
    receipt = getattr(chain.cycle, "last_receipt", None)
    return str(getattr(wake, "activity_ref", "") or
               getattr(receipt, "activity_ref", "") or "")


def foreground_activity(chain: Any) -> str:
    state, _ = chain.runtime.activity_read.read_canonical_state()
    return str(state.get("foreground_activity_ref") or "")


def decision_count(chain: Any) -> int:
    try:
        data = json.loads(chain.runtime.decision_store.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(data.get("decisions_by_id") or {})


def action_count(chain: Any) -> int:
    try:
        return len(chain.runtime.action_read.list_actions() or [])
    except Exception:  # noqa: BLE001
        return -1


def memory_rows(chain: Any) -> int:
    path = Path(chain.evidence_db)
    if not path.exists():
        return 0
    try:
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error:
        return -1
    try:
        row = connection.execute("SELECT count(*) FROM evidence_events").fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def scan_for_text(root: Path, needle: str, *, limit_bytes: int = 4 * 1024 * 1024) -> list[str]:
    """Paths under ``root`` whose bytes contain ``needle``; bounded, best-effort."""

    hits: list[str] = []
    wanted = needle.encode("utf-8")
    for base, _dirs, files in os.walk(str(root)):
        for name in files:
            path = Path(base) / name
            try:
                if path.stat().st_size > limit_bytes:
                    continue
                if wanted in path.read_bytes():
                    hits.append(str(path))
            except OSError:
                continue
    return hits


# ---------------------------------------------------------------------------------------------
# P1：独立子进程里的两版正式写入
# ---------------------------------------------------------------------------------------------


def run_p1_phase(root: Path | str) -> dict[str, Any]:
    import g1_real_adoption as g1

    root = Path(root)
    report: dict[str, Any] = {"phase": "p1", "pid": os.getpid(), "root": str(root)}
    with g1._offline_network_guard(root):
        report["guard"] = g1._offline_guard_selfcheck()
        chain = open_chain(root, "p1")
        try:
            install_planner(chain)
            seed = seed_first_write(chain, 0, V1_SENTINEL)
            artifact_id = seed.artifact_id
            if not artifact_id:
                raise SystemExit("P1 START did not create a work: %r" % (seed.wake.to_dict(),))
            start_head = chain.port.read_head(artifact_id) or {}
            report.update({
                "start_decision": str(seed.wake.decision or ""),
                "start_outcome": str(seed.wake.outcome or ""),
                "artifact_id": artifact_id,
                # START creates the project with its seed text (version 1) and then writes the
                # model's first paragraph (version 2).  Version 2 is the one carrying V1.
                "earlier_head": str(start_head.get("version_id") or ""),
                "earlier_number": int(start_head.get("version_number") or 0),
            })

            # Second real version: revise_tail replaces the trailing paragraph, so the current
            # head no longer carries the V1 sentinel at all.
            second = continue_round(chain, artifact_id, STEP,
                                    {"kind": "revise_tail", "text": V2_SENTINEL,
                                     "self_note": "stub: second real version"})
            head = chain.port.read_head(artifact_id) or {}
            report.update({
                "continue_decision": str(second.wake.decision or ""),
                "continue_outcome": str(second.wake.outcome or ""),
                "head": str(head.get("version_id") or ""),
                "head_number": int(head.get("version_number") or 0),
                "version_count": owner_versions(chain, artifact_id),
                "activity_ref": activity_ref_of(chain, second.wake) or foreground_activity(chain),
            })

            # A decoy work: the writer's request must not wander onto it.
            decoy = chain.port.create_artifact(
                workspace_id=str(chain.data["workspace_id"]),
                title="另一件作品（负控）", content=DECOY_SENTINEL,
                grant_id=str(chain.data["artifact_grant_id"]),
                operation_key="nrw:restart-writer:decoy:%d" % os.getpid())
            if not getattr(decoy, "applied", False):
                raise SystemExit("decoy write refused: %r" % (decoy,))
            report["decoy_artifact_id"] = str(decoy.artifact_id)
            report["owner_work_at_close"] = owner_work(chain, artifact_id)
        finally:
            report["close"] = close_chain(chain)
    return report


def _run_p1_child(root: Path) -> dict[str, Any]:
    config = Path(root) / "p1-config.json"
    config.write_text(json.dumps({"root": str(root)}, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()),
         "--phase", "p1", "--config", str(config)],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if completed.returncode != 0:
        raise AssertionError("P1 child exited %d\nstdout tail: %s\nstderr tail: %s"
                             % (completed.returncode, completed.stdout[-1500:],
                                completed.stderr[-2000:]))
    lines = [line for line in completed.stdout.splitlines()
             if line.startswith("RESTART_WRITER_REPORT ")]
    if not lines:
        raise AssertionError("P1 child printed no RESTART_WRITER_REPORT\nstdout tail: %s"
                             % completed.stdout[-1500:])
    return json.loads(lines[-1].split(" ", 1)[1])


# ---------------------------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------------------------


@POSIX_ONLY
class RestartWriterInputChainTest(unittest.TestCase):
    def setUp(self) -> None:
        import g1_real_adoption as g1

        self._guard_cm = g1._offline_network_guard(tempfile.gettempdir())
        self._guard_cm.__enter__()
        self.addCleanup(self._guard_cm.__exit__, None, None, None)
        self.selfcheck = g1._offline_guard_selfcheck()

    def _root(self, tag: str) -> Path:
        return Path(tempfile.mkdtemp(prefix="nyairo-restart-writer-%s-" % tag, dir="/tmp"))

    def _prepare(self, root: Path) -> None:
        import v05_restart_continuation_smoke as restart

        restart.prepare_fixture(root)

    def _assert_guard(self, selfcheck: Mapping[str, Any], *, where: str) -> None:
        missing = [key for key in ("inet_refused", "inet6_refused", "dns_refused",
                                   "connect_refused", "unix_ok") if key not in selfcheck]
        self.assertFalse(missing, "%s guard selfcheck missing keys: %r" % (where, missing))
        self.assertTrue(selfcheck["inet_refused"], where)
        self.assertTrue(selfcheck["inet6_refused"], where)
        self.assertTrue(selfcheck["dns_refused"], where)
        self.assertTrue(selfcheck["connect_refused"], where)
        self.assertTrue(selfcheck["unix_ok"], where)

    # -- case 1 ------------------------------------------------------------------------
    def test_across_two_pids_the_writer_gets_the_current_head_and_real_sources(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("case1")
        self._prepare(root)
        p1 = _run_p1_child(root)

        # Two real OS processes, one root, one work, one Activity.
        self.assertNotEqual(int(p1["pid"]), os.getpid())
        self._assert_guard(p1.get("guard") or {}, where="p1-child")
        self.assertEqual(p1["start_decision"], "START")
        self.assertEqual(p1["start_outcome"], "ACTED")
        self.assertEqual(p1["continue_decision"], "CONTINUE")
        self.assertEqual(p1["continue_outcome"], "ACTED")
        self.assertEqual(p1["version_count"], 3)
        self.assertEqual(p1["owner_work_at_close"]["version"], 3)
        self.assertEqual(p1["owner_work_at_close"]["head"], p1["head"])
        self.assertTrue(p1["close"].get("closed") is True)
        self.assertTrue(p1["close"].get("service_stopped") is True)

        chain = open_chain(root, "p2")
        try:
            install_planner(chain)
            chain.cycle.work_artifact_id = p1["artifact_id"]
            head_at_start = chain.port.read_head(p1["artifact_id"]) or {}
            self.assertEqual(str(head_at_start.get("version_id") or ""), p1["head"])
            self.assertEqual(int(head_at_start.get("version_number") or 0), 3)
            self.assertEqual(owner_versions(chain, p1["artifact_id"]), 3)
            self.assertEqual(foreground_activity(chain), p1["activity_ref"])

            # Paused P2: nothing is considered, nothing is requested.
            import life_pause

            life_pause.set_paused(chain.state_root, True)
            paused = run_window(chain, STEP * 2)
            self.assertEqual(str(paused.outcome or ""), "PAUSED_BY_OPERATOR",
                             paused.to_dict())
            self.assertEqual(int(paused.llm_calls or 0), 0)
            self.assertEqual(len(chain.transport.prompts), 0)
            self.assertEqual(int(chain.client.call_count or 0), 0)
            self.assertEqual(int(chain.planner.call_count or 0), 0)
            life_pause.set_paused(chain.state_root, False)

            # Scripted CONTINUE — explicitly a script, not the subject's real choice.
            observed = stamp(STEP * 3)
            candidate = continue_candidate(chain, p1["artifact_id"], observed_at=observed)
            self.assertTrue(candidate, "no exact CONTINUE candidate after restart")
            chain.transport.payload = {"kind": "append_paragraph", "text": V3_SENTINEL,
                                       "self_note": "stub: continue after restart"}
            chain.planner.plan = [{"script": {"decision": "CONTINUE",
                                              "selected_candidate_id": candidate,
                                              "deferred_candidate_ids": []}}]
            wake = run_window(chain, STEP * 3)

            self.assertEqual(str(wake.decision or ""), "CONTINUE", wake.to_dict())
            self.assertEqual(str(wake.outcome or ""), "ACTED", wake.to_dict())
            self.assertEqual(activity_ref_of(chain, wake), p1["activity_ref"])
            self.assertEqual(len(chain.transport.prompts), 1)

            sent = json.loads(chain.transport.prompts[0]["user"])
            head_body = str((chain.port.read_version(p1["artifact_id"], p1["head"]) or {})
                            .get("content") or "")
            owner_view = chain.port.read_artifact(p1["artifact_id"]) or {}

            # Real request body, checked against the owner's own current head.
            self.assertEqual(str(sent.get("current_version_id") or ""), p1["head"])
            self.assertEqual(int(sent.get("current_version_number") or 0), 3)
            tail = str(sent.get("tail_of_current_text") or "")
            self.assertTrue(tail.strip().endswith(V2_SENTINEL), tail[:160])
            self.assertNotIn(V1_SENTINEL, tail,
                             "the writer was handed the stale previous version")
            self.assertNotIn(DECOY_SENTINEL, chain.transport.prompts[0]["user"],
                             "the writer's request wandered onto another work")
            self.assertEqual(str(sent.get("work_title") or ""),
                             str((owner_view.get("artifact") or {}).get("title") or ""))
            self.assertEqual(int(sent.get("already_written_chars") or 0), len(head_body))
            self.assertIn(V2_SENTINEL, head_body)
            self.assertNotIn(V1_SENTINEL, head_body)

            # The bounded earlier-version excerpt is the owner's *previous* version (2), the one
            # that really carries V1 -- read through the owner's read_version, distinct from the
            # head.  The excerpt is that version's body prefix (600 chars), so the V1 sentinel at
            # its tail is outside the bounded slice by design; the version and the body it came
            # from are both checked against the owner.
            earlier = sent.get("earlier_versions") or []
            self.assertEqual(len(earlier), 1, sent.keys())
            self.assertEqual(str(earlier[0].get("version_id") or ""), p1["earlier_head"])
            self.assertEqual(int(earlier[0].get("version_number") or 0),
                             int(p1["earlier_number"]))
            self.assertEqual(int(p1["earlier_number"]), 2)
            earlier_body = str((chain.port.read_version(p1["artifact_id"],
                                                        p1["earlier_head"]) or {})
                               .get("content") or "")
            excerpt = str(earlier[0].get("excerpt") or "")
            self.assertTrue(excerpt)
            self.assertLessEqual(len(excerpt), 600)
            self.assertEqual(excerpt, earlier_body[:len(excerpt)])
            self.assertIn(V1_SENTINEL, earlier_body)

            # The document context really is this work's own research material, read from the
            # owner -- not a fabricated or another work's source list.
            research = sent.get("research_context") or {}
            self.assertEqual(str(research.get("kind") or ""), "research_document_context")
            self.assertEqual(str(research.get("artifact_id") or ""), p1["artifact_id"])
            own = research.get("own_research") or {}
            self.assertEqual(str(own.get("artifact_id") or ""), p1["artifact_id"])
            self.assertIs(own.get("readable"), True, own.get("readable"))
            refs = own.get("source_refs") or []
            self.assertTrue(refs, "no real material source reached the prompt")
            for row in refs:
                self.assertTrue(str(row.get("artifact_id") or "").startswith("artifact:"))
                self.assertTrue(str(row.get("source_revision") or "").startswith(
                    "artifact-version:"))
            sources = own.get("sources") or []
            self.assertTrue(sources)
            self.assertIn(str(sources[0].get("artifact_id") or ""),
                          {str(row.get("artifact_id") or "") for row in refs})

            # Owner-confirmed result: exactly one new version, containing the new paragraph.
            head_after = chain.port.read_head(p1["artifact_id"]) or {}
            self.assertNotEqual(str(head_after.get("version_id") or ""), p1["head"])
            self.assertEqual(int(head_after.get("version_number") or 0), 4)
            self.assertEqual(owner_versions(chain, p1["artifact_id"]), 4)
            body = chain.port.resume_point(p1["artifact_id"]).content or ""
            self.assertIn(V3_SENTINEL, body)
            self.assertNotIn(V1_SENTINEL, body)
            # Which sources were really present is reported, not invented.
            for key in ("research_context", "practice_context", "work_context"):
                if key in sent:
                    self.assertIsInstance(sent[key], dict, key)
        finally:
            close_chain(chain)

    # -- case 2 ------------------------------------------------------------------------
    def test_committed_continue_no_change_keeps_identity_and_writes_nothing(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("case2")
        self._prepare(root)
        chain = open_chain(root, "case2")
        try:
            install_planner(chain)
            seed = seed_first_write(chain, 0, CASE2_V1)
            artifact_id = seed.artifact_id
            self.assertTrue(artifact_id)
            self.assertEqual(str(seed.wake.outcome or ""), "ACTED")
            activity_ref = activity_ref_of(chain, seed.wake) or foreground_activity(chain)
            work_before = owner_work(chain, artifact_id)
            versions_before = owner_versions(chain, artifact_id)
            actions_before = action_count(chain)
            receipts_before = getattr(chain.cycle, "last_receipt", None)
            memory_before = memory_rows(chain)
            decisions_before = decision_count(chain)

            no_change = continue_round(chain, artifact_id, STEP,
                                       {"kind": "no_change", "text": "",
                                        "self_note": CASE2_SELF_NOTE})
            wake = no_change.wake
            extra = dict(wake.extra or {})
            self.assertEqual(str(wake.decision or ""), "CONTINUE", wake.to_dict())
            self.assertEqual(str(wake.outcome or ""), "NO_ACTION", wake.to_dict())
            self.assertEqual(extra.get("decision_route"), "COGNITIVE_REQUIRED", wake.to_dict())
            self.assertTrue(str(extra.get("decision_id") or "").startswith("dec:"),
                            wake.to_dict())
            self.assertEqual(extra.get("selected_candidate_ref"), no_change.candidate)
            self.assertEqual(extra.get("owner_version_created"), False)
            self.assertEqual(extra.get("work_no_change"),
                             {"reason_code": "NO_CHANGE", "owner_called": False,
                              "version_created": False})
            self.assertNotIn("NO_ACTION_CHOSEN", extra.get("reason_codes") or [])

            # Zero owner effect from the no_change: same version, no Action, no Memory row.
            self.assertEqual(owner_work(chain, artifact_id), work_before)
            self.assertEqual(owner_versions(chain, artifact_id), versions_before)
            self.assertEqual(action_count(chain), actions_before)
            self.assertIs(getattr(chain.cycle, "last_receipt", None), receipts_before)
            self.assertEqual(memory_rows(chain), memory_before)
            self.assertEqual(decision_count(chain), decisions_before + 1)

            # The model's free-text answer is not stored by any life-side sink.
            self.assertNotIn(CASE2_SELF_NOTE, json.dumps(wake.to_dict(), ensure_ascii=False))
            for base in (chain.state_root, chain.root / "memory"):
                hits = scan_for_text(Path(base), CASE2_SELF_NOTE)
                self.assertEqual(hits, [], "self_note leaked into %r" % (hits,))

            # Same starting point, a legitimate new paragraph: real CAS write on the same
            # Activity, and the owner reads the very same bytes back.
            second = continue_round(chain, artifact_id, STEP * 2,
                                    {"kind": "append_paragraph", "text": CASE2_BODY,
                                     "self_note": "stub: now there is something to add"})
            wake2 = second.wake
            self.assertEqual(str(wake2.decision or ""), "CONTINUE", wake2.to_dict())
            self.assertEqual(str(wake2.outcome or ""), "ACTED", wake2.to_dict())
            self.assertEqual(activity_ref_of(chain, wake2), activity_ref)
            self.assertEqual(owner_versions(chain, artifact_id), versions_before + 1)
            self.assertGreater(action_count(chain), actions_before)
            self.assertEqual(decision_count(chain), decisions_before + 2)
            body = chain.port.resume_point(artifact_id).content or ""
            self.assertIn(CASE2_BODY, body)
            self.assertIn(CASE2_V1, body)
        finally:
            close_chain(chain)

    # -- case 3 ------------------------------------------------------------------------
    def test_head_moved_during_the_content_wait_is_a_real_cas_refusal(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("case3")
        self._prepare(root)
        chain = open_chain(root, "case3")
        try:
            install_planner(chain)
            seed = seed_first_write(chain, 0, CASE3_MODEL_BODY)
            artifact_id = seed.artifact_id
            self.assertTrue(artifact_id)
            activity_ref = activity_ref_of(chain, seed.wake) or foreground_activity(chain)
            head_before = owner_work(chain, artifact_id)
            versions_before = owner_versions(chain, artifact_id)
            actions_before = action_count(chain)
            prompts_before = len(chain.transport.prompts)
            other: dict[str, Any] = {}

            def _advance_head_while_waiting() -> None:
                # Another legitimate writer through the existing owner interface, at the
                # actual content wait boundary.
                outcome = chain.port.commit_version(
                    artifact_id=artifact_id,
                    expected_head=head_before["head"],
                    content=CASE3_OTHER_BODY,
                    grant_id=str(chain.data["artifact_grant_id"]),
                    operation_key="nrw:restart-writer:other:%d" % os.getpid())
                other["applied"] = bool(getattr(outcome, "applied", False))
                other["version_id"] = str(getattr(outcome, "version_id", "") or "")
                other["version_number"] = int(getattr(outcome, "version_number", 0) or 0)

            chain.transport.on_complete = _advance_head_while_waiting
            second = continue_round(chain, artifact_id, STEP,
                                    {"kind": "append_paragraph",
                                     "text": CASE3_MODEL_BODY + "（模型这次没写进去）",
                                     "self_note": "stub: race the head"})
            wake = second.wake

            self.assertTrue(other.get("applied"), other)
            self.assertEqual(other["version_number"], versions_before + 1)
            # Exactly one content request for this round: the refusal is not followed by a
            # bare retry.
            self.assertEqual(len(chain.transport.prompts), prompts_before + 1)
            # The stale expected_head did not overwrite the other writer's version.
            self.assertEqual(owner_versions(chain, artifact_id), versions_before + 1)
            self.assertEqual(owner_work(chain, artifact_id)["head"], other["version_id"])
            body = chain.port.resume_point(artifact_id).content or ""
            self.assertEqual(body.strip(), CASE3_OTHER_BODY)
            self.assertNotIn("（模型这次没写进去）", body)
            self.assertNotIn(CASE3_MODEL_BODY, body)
            # The refusal really is the owner's CAS answer against the *old* expected head.
            receipt = getattr(chain.cycle, "last_receipt", None)
            self.assertIsNotNone(receipt, wake.to_dict())
            self.assertEqual(str(getattr(receipt.effect_state, "name", receipt.effect_state)),
                             "REFUSED")
            self.assertEqual(str(receipt.expected_head), head_before["head"])
            self.assertIn("stale expected_head", str(receipt.detail or "").lower())
            # The change still belongs to this work / Activity, not to another object.
            self.assertEqual(activity_ref_of(chain, wake), activity_ref)
            self.assertEqual(foreground_activity(chain), activity_ref)
            self.assertIn(str(wake.outcome or ""),
                          {"STEP_REFUSED", "NO_STEP_AVAILABLE", "STEP_UNKNOWN",
                           "ALREADY_DONE"},
                          wake.to_dict())
            # One refused attempt at most: the refusal is not retried into a second action.
            self.assertLessEqual(action_count(chain), actions_before + 1)
        finally:
            close_chain(chain)

    # -- case 4 ------------------------------------------------------------------------
    def test_default_caps_are_still_the_product_ones_and_the_note_is_not_persisted(self):
        self._assert_guard(self.selfcheck, where="parent")
        root = self._root("case4")
        self._prepare(root)
        chain = open_chain(root, "case4")
        try:
            install_planner(chain)
            # Constructor caps are the product ones (assembly's own call), not a fallback
            # mistaken for what was really sent.
            self.assertEqual(int(chain.writer.max_new_chars), 300)
            self.assertEqual(int(chain.writer.max_prompt_chars), 4000)
            self.assertEqual(float(chain.writer.timeout_s), 20.0)
            self.assertEqual(int(chain.writer.max_output_tokens), 512)
            self.assertEqual(int(chain.client.max_output_tokens), 512)

            seed = seed_first_write(chain, 0, CASE4_V1)
            artifact_id = seed.artifact_id
            self.assertTrue(artifact_id)
            actions_before = action_count(chain)
            decisions_before = decision_count(chain)
            prompts_before = len(chain.transport.prompts)

            second = continue_round(chain, artifact_id, STEP,
                                    {"kind": "append_paragraph", "text": CASE4_BODY,
                                     "self_note": CASE4_SELF_NOTE})
            self.assertEqual(str(second.wake.outcome or ""), "ACTED", second.wake.to_dict())
            self.assertEqual(len(chain.transport.prompts), prompts_before + 1)
            call = chain.transport.calls[-1]
            self.assertEqual(call["max_output_tokens"], 512)
            self.assertEqual(call["timeout_s"], 20.0)
            self.assertEqual(action_count(chain), actions_before + 1)
            self.assertEqual(decision_count(chain), decisions_before + 1)

            # The body is in the owner; the free-text self_note is in no life-side sink.
            body = chain.port.resume_point(artifact_id).content or ""
            self.assertIn(CASE4_BODY, body)
            self.assertNotIn(CASE4_SELF_NOTE, json.dumps(second.wake.to_dict(),
                                                         ensure_ascii=False))
            for base in (chain.state_root, chain.root / "memory"):
                hits = scan_for_text(Path(base), CASE4_SELF_NOTE)
                self.assertEqual(hits, [], "self_note leaked into %r" % (hits,))
        finally:
            close_chain(chain)


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
        raise SystemExit("unknown phase: %s" % args.phase)
    report = run_p1_phase(Path(config["root"]))
    print("RESTART_WRITER_REPORT " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    if "--phase" in sys.argv[1:]:
        raise SystemExit(_child_main(sys.argv[1:]))
    unittest.main(verbosity=2)
