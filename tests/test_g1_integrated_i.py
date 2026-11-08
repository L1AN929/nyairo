#!/usr/bin/env python3
"""G1 新隔离入口 i 的离线聚合用例。

最多六个聚合 case，只测本入口新增的边界，不重跑 g/h 旧套件或旧 G1 19/28/37：

1. i 的封闭身份（root/authority/run/域/history 539/窗口/绝对线/累计门）与
   h、g、a/b/c/f 的旧默认都不漂；
2. 观察器 on/off 时 transport 实收字节、调用次数、timeout/max_output_tokens
   参数、返回与异常行为完全一致；
3. work-only/mixed 正控、wrongartifact/坏 JSON/无 work 负控、privacy sentinel
   不落 summary，绑定在 finally 清空，最多 20 条；
4. 20→21 前拒与跨 phase 失败锁；
5. status/audit 纯读安全投影、坏账 UNKNOWN 不猜 0；
6. 一条真正复用正式双 PID Supply/AG1/AR0 链的脚本 transport 短链，phase/owner
   子进程装同一份网络守卫，并自带 i 专属安全 summary。
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_integrated_g as g  # noqa: E402
import g1_integrated_i as i  # noqa: E402
import g1_real_adoption as g1  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
from agency_model_client import AgencyModelClient, ModelCallResult  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

OBSERVED_AT = "2026-11-07T12:00:00+00:00"
SENTINEL = "PRIVATE_SENTINEL_endpoint_key_detail_tokens_work_body"


class RecordingTransport:
    """最外层 transport 替身：记录真正收到的 user_prompt 与参数，并按模式回复。"""

    backend = "scripted-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.mode = "edit"
        self.prompts: list[dict[str, str]] = []
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: float | None = None,
                 max_output_tokens: int | None = None) -> Any:
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        self.calls.append({"timeout_s": float(timeout_s or 0.0),
                           "max_output_tokens": int(max_output_tokens or 0)})
        if self.mode == "raise":
            raise RuntimeError("TRANSPORT_BOOM")
        if self.mode == "no_change":
            text = json.dumps({"kind": "no_change", "text": "",
                               "self_note": "今天不写了"}, ensure_ascii=False)
        else:
            text = json.dumps({"kind": "append_paragraph", "text": "合成的一小段。"},
                              ensure_ascii=False)
        return ModelCallResult(purpose="artifact_edit", provider=self.provider,
                               model=self.model, text=text, latency_ms=1, status=200,
                               usage_source="none")

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "calls": len(self.calls)}


def _scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="g1-integrated-i-test-"))


def _point() -> ArtifactResumePoint:
    return ArtifactResumePoint(
        artifact_id="artifact:test-work", title="一本私人手记", head_version_id="v1",
        version_number=1, content="旧的一段。\n", lifecycle="ACTIVE", revision=1)


def _work_block(state: str = "OK", artifact_id: str = "artifact:test-work") -> dict[str, Any]:
    return {"schema_version": "chiyo.life.work_context.v1",
            "artifact_id": artifact_id, "state": state,
            "work": {"available": True, "title": SENTINEL},
            "results": {"state": "EMPTY", "rows": []},
            "note": SENTINEL}


def _writer_pair() -> tuple[mcw.ModelContinuationWriter, RecordingTransport,
                            mcw.ModelContinuationWriter, RecordingTransport,
                            g1.WorkContextObserver]:
    def build() -> tuple[mcw.ModelContinuationWriter, RecordingTransport]:
        transport = RecordingTransport()
        client = AgencyModelClient(config={"provider": "scripted", "model": "scripted"},
                                   transport=transport, purpose="artifact_edit")
        writer = mcw.ModelContinuationWriter(client, max_new_chars=300, timeout_s=30.0,
                                             max_output_tokens=2048)
        return writer, transport

    plain_writer, plain_transport = build()
    watched_writer, watched_transport = build()
    observer = g1.WorkContextObserver(phase="p1")
    g1._install_work_observer(watched_writer, watched_transport, observer)
    return plain_writer, plain_transport, watched_writer, watched_transport, observer


def _run_sequence(writer: mcw.ModelContinuationWriter,
                  transport: RecordingTransport) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    calls = (
        ("edit", {"work_context": _work_block()}),
        ("edit", None),
        ("no_change", {"work_context": _work_block(state="PARTIAL"),
                       "research_context": {"own_research": {"question": SENTINEL}},
                       "practice_context": {"methods": []},
                       "earlier_versions": [{"version_id": "v0", "excerpt": SENTINEL}],
                       "previous_experiences": [{"text": SENTINEL}]}),
        ("raise", {"work_context": _work_block()}),
    )
    for mode, context in calls:
        transport.mode = mode
        try:
            if context is None:
                result = writer.next_content(_point(), observed_at=OBSERVED_AT)
            else:
                result = writer.next_content(_point(), observed_at=OBSERVED_AT,
                                             context=context)
            outcomes.append({"kind": "return", "value": result})
        except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
            outcomes.append({"kind": "raise", "type": type(exc).__name__,
                             "message": str(exc)})
    return outcomes


def _prepared_root(base: Path) -> Path:
    root = base / "instance-root"
    experiment = root / "experiment-g1"
    experiment.mkdir(parents=True)
    state_root = root / "one-day"
    state_root.mkdir()
    profile = i.PROFILE
    g1._write_json(experiment / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": i.BATCH,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(root), "state_root": str(state_root),
        "deadline": profile.deadline.isoformat(),
        "latest_start": profile.latest_start.isoformat(),
        "world_intent_gate": "off", "caps": g._expected_caps(profile),
    }, private=True)
    g1._write_json(experiment / g1.BATCH_FILENAME, {
        "schema": "g1.batch.v1", "batch": i.BATCH, "run_id": profile.run_id,
        "domain_id": profile.domain_id, "state": "prepared", "root": str(root),
        "state_root": str(state_root),
    }, private=True)
    g1._write_json(state_root / "life_request_budget.json", {
        "schema_version": "life.request_budget.v1", "kind": "life_request_budget",
        "active_day": "2026-11-07", "days": {"2026-11-07": 2},
        "windows": {profile.run_id: {"calls": 2,
                                     "opened_at": "2026-11-07T00:00:00+00:00"}},
        "run_id": profile.run_id, "tokens": [],
        "opened_at": "2026-11-07T00:00:00+00:00",
        "updated_at": "2026-11-07T00:00:00+00:00",
    }, private=True)
    return root


def _snapshot(root: Path) -> tuple[dict, set]:
    files = {}
    dirs = set()
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            dirs.add(relative)
        else:
            stat = path.stat()
            files[relative] = (stat.st_size, stat.st_mtime_ns)
    return files, dirs


class IIdentityTest(unittest.TestCase):
    def test_i_is_closed_and_h_g_abc_f_do_not_move(self):
        profile = i.PROFILE
        self.assertEqual(profile.key, "g1-integrated-i")
        self.assertEqual(str(profile.root), "/tmp/nyairo-g1-real-20261107-i")
        self.assertEqual(str(profile.authority_dir),
                         "/tmp/nyairo-g1-real-20261107-i-memory-authority")
        self.assertEqual(str(profile.authority_db),
                         "/tmp/nyairo-g1-real-20261107-i-memory-authority/authority.db")
        self.assertEqual(profile.run_id, "run:g1-real-20261107-i")
        self.assertEqual(profile.domain_id, "g1:real:20261107-i")
        self.assertEqual(profile.expected_history, 539)
        self.assertEqual(profile.window_seconds, 3600)
        self.assertEqual(profile.max_ticks, 1000)
        self.assertEqual(profile.trace_max_records, 10000)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-08T00:50:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-07T23:30:00+08:00")
        self.assertIs(g1._select_batch(i.BATCH), profile)
        # i 走非自然 p1/p2 链：gate 显式 off、60 秒收尾，累计门 1000。
        self.assertEqual(g1._world_intent_gate(profile), "off")
        self.assertFalse(g1._is_natural(profile))
        self.assertEqual(g1._phase_cleanup_seconds(profile), 60.0)
        self.assertFalse(g1._total_accounting_exceeded(0, profile=profile))
        self.assertFalse(g1._total_accounting_exceeded(460, profile=profile))
        self.assertTrue(g1._total_accounting_exceeded(461, profile=profile))
        # h / g / a/b/c/f 的旧默认一个都不动。
        self.assertEqual(g1.H_PROFILE.key, "g1-integrated-h")
        self.assertEqual(g1.H_PROFILE.expected_history, 524)
        self.assertEqual(g.PROFILE.key, "g1-integrated-g")
        self.assertEqual(g.PROFILE.expected_history, 507)
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertEqual(g1.BATCH_PROFILES["a"].expected_history, 98)
        self.assertEqual(g1.F_PROFILE.expected_history, 168)
        self.assertEqual(g1._profile_window_seconds(g1.F_PROFILE), 1800)
        self.assertEqual(g1._profile_max_ticks(g1.F_PROFILE), 900)
        self.assertFalse(g1._total_accounting_exceeded(900, profile=g1.BATCH_PROFILES["a"]))
        # 可选投影：旧 phase 报告没有该键时 g/h 输出形状不变；只有 i 才出现。
        self.assertNotIn("work_observation", g._safe_phase({"phase": "p1"}))
        projected = g._safe_phase({"phase": "p1", "work_observation": {
            "mode": g1.WORK_OBSERVATION_MODE, "state": "NOT_EXERCISED", "calls": []}})
        self.assertEqual(projected["work_observation"]["mode"], g1.WORK_OBSERVATION_MODE)


class ObserverNoOpTest(unittest.TestCase):
    def test_on_and_off_are_byte_and_behavior_identical(self):
        plain_writer, plain_transport, watched_writer, watched_transport, observer = \
            _writer_pair()
        # 安装器保持 ``context`` 关键字，cycle 的 accepts_context 判定不变。
        self.assertIn("context", inspect.signature(watched_writer.next_content).parameters)

        plain = _run_sequence(plain_writer, plain_transport)
        watched = _run_sequence(watched_writer, watched_transport)

        self.assertEqual(watched_transport.prompts, plain_transport.prompts)
        self.assertEqual(watched_transport.calls, plain_transport.calls)
        self.assertEqual(watched, plain)
        self.assertEqual(len(watched_transport.prompts), 4)
        self.assertEqual(watched_transport.calls[0]["timeout_s"], 30.0)
        self.assertEqual(watched_transport.calls[0]["max_output_tokens"], 2048)
        # 四次调用各一条记录；异常/失败没有被观测器吞掉或重发。
        self.assertEqual(len(observer.view()["calls"]), 4)
        # 绑定在 finally 清空，不会跨调用残留。
        self.assertEqual(observer._expected, "")


class ObserverMatchingTest(unittest.TestCase):
    def test_positives_negatives_privacy_and_cap(self):
        observer = g1.WorkContextObserver(phase="p2")
        observer.bind_expected("artifact:test-work")

        observer.observe(json.dumps({"work_context": _work_block()}, ensure_ascii=False))
        observer.observe(json.dumps({
            "work_context": _work_block(state="PARTIAL"),
            "research_context": {"own_research": {"question": SENTINEL}},
            "practice_context": {"methods": []},
            "earlier_versions": [{"version_id": "v0", "excerpt": SENTINEL}],
            "previous_experiences": [{"text": SENTINEL}, {"text": SENTINEL}],
        }, ensure_ascii=False))
        observer.observe(json.dumps({"work_context": _work_block(
            artifact_id="artifact:other-work")}, ensure_ascii=False))
        observer.observe(json.dumps({"previous_experiences": []}, ensure_ascii=False))
        observer.observe("not-json")

        view = observer.view()
        first, second, wrong, absent, bad = view["calls"]
        self.assertTrue(first["parse_ok"])
        self.assertTrue(first["work_present"])
        self.assertEqual(first["work_state"], "OK")
        self.assertTrue(first["expected_work_matches"])
        self.assertEqual(second["work_state"], "PARTIAL")
        self.assertTrue(second["expected_work_matches"])
        self.assertTrue(second["research_present"])
        self.assertTrue(second["practice_present"])
        self.assertEqual(second["earlier_count"], 1)
        self.assertEqual(second["experience_count"], 2)
        self.assertFalse(wrong["expected_work_matches"])
        self.assertFalse(absent["work_present"])
        self.assertFalse(absent["expected_work_matches"])
        self.assertFalse(bad["parse_ok"])
        self.assertEqual(bad["work_state"], "UNKNOWN")
        self.assertEqual(bad["expected_work_matches"], "UNKNOWN")
        self.assertEqual(bad["earlier_count"], "UNKNOWN")
        self.assertEqual(bad["experience_count"], "UNKNOWN")
        self.assertEqual([row["call_index"] for row in view["calls"]], [1, 2, 3, 4, 5])
        # 所有记录都只含允许字段；没有正文/哨兵/异常 message。
        self.assertEqual({key for key in first},
                         {"phase", "call_index", "parse_ok", "work_present",
                          "work_state", "expected_work_matches", "research_present",
                          "practice_present", "earlier_count", "experience_count"})
        self.assertNotIn(SENTINEL, json.dumps(view, ensure_ascii=False))

        # 20 条上限：第 21 次不再追加。
        capped = g1.WorkContextObserver(phase="p1")
        capped.bind_expected("artifact:test-work")
        for _index in range(21):
            capped.observe(json.dumps({"work_context": _work_block()},
                                      ensure_ascii=False))
        self.assertEqual(len(capped.view()["calls"]), g1.WORK_OBSERVATION_MAX_RECORDS)
        self.assertEqual(g1.WORK_OBSERVATION_MAX_RECORDS, 20)
        # 无调用时明确 NOT_EXERCISED，而不是猜 0 条成功。
        self.assertEqual(g1.WorkContextObserver(phase="p1").view()["state"],
                         "NOT_EXERCISED")


class CapAndFailureLockTest(unittest.TestCase):
    def test_shared_twenty_cap_and_cross_phase_failure_lock(self):
        base = _scratch()
        state_root = base / "cap-state"
        state_root.mkdir(parents=True)
        with g1._use_batch(i.BATCH):
            cap = g1._cross_pid_cap(state_root, batch=i.BATCH)
            durable = g._cross_phase_stop(base / "cross-phase-stop")
        self.assertEqual(cap["first_rc"], 0)
        self.assertEqual(cap["second_rc"], 0)
        self.assertEqual((cap["first_out"] or {}).get("reserved"), 12)
        self.assertEqual((cap["second_out"] or {}).get("reserved"), 8)
        self.assertNotEqual(cap["third"], "reserved")
        self.assertEqual(cap["calls"], 20)
        self.assertEqual(cap["run_id"], i.PROFILE.run_id)
        self.assertEqual(durable["recorded"], g1.REASON_PROVIDER_FAILURE)
        self.assertEqual(durable["stop"], g1.REASON_PROVIDER_FAILURE)
        self.assertTrue(durable["latch_closed"])


class PureReadTest(unittest.TestCase):
    def test_status_audit_are_pure_and_corrupt_ledger_is_unknown(self):
        base = _scratch()
        root = _prepared_root(base)
        files_before, dirs_before = _snapshot(root)
        with g1._use_batch(i.BATCH):
            first = g.status(root, expected_root=root, profile=i.PROFILE)
            second = g.audit_readonly(root, expected_root=root, profile=i.PROFILE)
        files_after, dirs_after = _snapshot(root)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["batch"], i.BATCH)
        self.assertEqual(first["run_id"], i.PROFILE.run_id)
        self.assertEqual(first["ledger"]["state"], "OK")
        self.assertEqual(first["ledger"]["run_window_calls"], 2)
        self.assertEqual(files_after, files_before)
        self.assertEqual(dirs_after, dirs_before)
        self.assertFalse((root / "one-day" / "life_request_budget.lock").exists())

        (root / "one-day" / "life_request_budget.json").write_text(
            "{not-json", encoding="utf-8")
        bad = g._read_ledger(root / "one-day", profile=i.PROFILE)
        self.assertEqual(bad["state"], "UNKNOWN")
        self.assertIsNone(bad["run_window_calls"])
        self.assertIsNone(bad["utc_day_calls"])


class FormalChainTest(unittest.TestCase):
    def test_formal_dual_pid_chain_is_covered_with_safe_summary_and_guard(self):
        base = _scratch()
        root = base / "instance-root"
        with g1._use_batch(i.BATCH), g1.offline_clock(i.PROFILE):
            with g1._offline_network_guard(base), g1._http_forbidden():
                prepared = g1.prepare(root, expected_root=root, offline=True)
                self.assertTrue(prepared.get("ok"), prepared.get("reason_code"))
                chain = g1.start_run(root, expected_root=root,
                                     execute_approved=True, offline=True)
                projection = g._projection_for_root(root, chain)
                guard = g1._offline_child_guard_report(root)
                status = g.status(root, expected_root=root, profile=i.PROFILE)
                again = g1.start_run(root, expected_root=root,
                                     execute_approved=True, offline=True)
        self.assertTrue(chain.get("ok"), chain.get("stop_reason"))
        self.assertEqual(chain.get("batch"), i.BATCH)
        self.assertEqual(projection["result"], g.RESULT_COVERED, projection)
        self.assertEqual(projection["missing"], [], projection)
        self.assertNotEqual(projection["pids"]["p1"], projection["pids"]["p2"])
        self.assertTrue(guard.get("ok") is True
                        and int(guard.get("found") or 0) >= 2, guard)
        self.assertEqual(again.get("reason_code"), g1.REASON_ALREADY_STARTED)
        for phase in ("p1", "p2"):
            view = (status.get("phases") or {}).get(phase) or {}
            observation = view.get("work_observation") or {}
            self.assertEqual(observation.get("mode"), g1.WORK_OBSERVATION_MODE)
            self.assertIn(observation.get("state"), {"OBSERVED", "NOT_EXERCISED"})
            for row in observation.get("calls") or []:
                self.assertEqual(set(row),
                                 {"phase", "call_index", "parse_ok", "work_present",
                                  "work_state", "expected_work_matches",
                                  "research_present", "practice_present",
                                  "earlier_count", "experience_count"})
                self.assertIn(row["work_state"], g1.WORK_OBSERVATION_STATES)


if __name__ == "__main__":
    unittest.main()
