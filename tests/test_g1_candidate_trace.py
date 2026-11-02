#!/usr/bin/env python3
"""``g1_real_adoption`` 实验候选观测（``--capture-candidates``）的用例。

四组：
1. 脱敏与边界（纯函数）：只落固定枚举/已知 ID 形状/计数；任意 ASCII 私文诱饵、伪造枚举、
   未知 ID 前缀都不落盘；超过观测副本上限只截副本并标 ``truncated``。
2. 阶段文件：默认关不建文件/不额外读；开启后每个阶段只写白名单字段；异常消息不进
   ``error``/``view``；非法 refs 保留实际 total 并计 unknown；缺/坏结构写 UNKNOWN/ERROR。
3. 门级集成（假 adapter）：开关不改变内层调用次数、参数、额度与返回；请求/最终 prompt
   两段都记到同一轮；只有「发送前明确拒绝」才记 NOT_SENT，普通异常/正常返回一律 UNKNOWN。
4. 真实 harness 集成冒烟：既有 ``prepare`` + ``start_run(offline=True)``（真 owner、明确
   替身 transport、HTTP 硬拒），默认关不产文件；开启后 p1/p2 都按阶段落盘，顺序不变，
   最终 prompt 是请求集合的有序子集；并核对真实 ``build_prompt`` 的 12000 字符结构裁剪
   确实被观测到；on/off 的覆盖结果与账本等价。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

# 注册截止已过真实墙钟。离线 harness 用 ``g1.offline_clock()`` 显式注入参考时刻
# （本批截止前 1 小时），只在 mkdtemp 根内生效；profile 常量与真实 run 语义不动。
# 纯函数用例不依赖这个接缝。

ID_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.\-]{0,95}$")
CAND_A = "cand:0123456789abcdef"
CAND_B = "cand:fedcba9876543210"
CSET = "cset:0123456789abcdef"
ACTV = "actv_0123456789abcdef"
DEC = "dec:0123456789abcdef"
WAITBASIS = "waitbasis:0123456789abcdef"
WAITDEP = "wait_dep:artifact.0123456789abcdef.0123456789abcdef"
RCOND = "rcond:0123456789abcdef"
RELIG = "relig:0123456789abcdef"


def _rows(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _trace_path(root: Path, phase: str) -> Path:
    return g1._experiment_dir(root) / (g1.CANDIDATE_TRACE_FILENAME % phase)


class _FakeBudget:
    """只实现观测用到的两个口：``view().run_window_calls`` 与 ``reserve``。"""

    def __init__(self) -> None:
        self.calls = 0

    def view(self) -> dict:
        return {"state": "OK", "run_window_calls": self.calls}

    def reserve(self, kind: str) -> dict:
        self.calls += 1
        return {"kind": kind}


class _BudgetRefusal(RuntimeError):
    """发送前明确拒绝的替身：带正式预算原因码。"""

    reason_code = "REQUEST_BUDGET_RUN_WINDOW_LIMIT"


class _FakeInner:
    """模拟 ProductionAgencyCognitionAdapter 的最小面：同一次调用产生一条 prompt shape。"""

    def __init__(self, *, shape=None, budget=None, failure=None, raise_exc=None,
                 reject=False) -> None:
        self.requests_seen: list = []
        self.calls: list = []
        self.shape = shape
        self.budget = budget
        self.failure = failure
        self.raise_exc = raise_exc
        self.reject = reject

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls.append({"request": request, "schema_retry_index": schema_retry_index})
        # 真实 adapter 的顺序：build_prompt（shape 进入 requests_seen）先于 reserve。
        self.requests_seen.append(self.shape)
        if self.budget is not None:
            self.budget.reserve("decision")
        if self.reject:
            raise _BudgetRefusal("budget refused")
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(failure_class=self.failure)


def _candidate(cid: str, *, source="WORLD_OPPORTUNITY", kind="CONSIDER_WORLD_OPPORTUNITY",
               legal=("START", "NO_ACTION"), targets=("artifact:surface",), prompt=False):
    row = {"candidate_id": cid, "source_kind": source, "candidate_kind": kind,
           "legal_decisions": list(legal), "executable_now": True,
           "target_refs": list(targets)}
    if prompt:
        row["description"] = "WORLD_OPPORTUNITY from WORLD_OPPORTUNITY (artifact:surface)"
    return row


def _request(candidates, *, cset_id=CSET, activity=ACTV, pause=None, elig=None):
    context = {"schema_version": "chiyo.life.decision_situation.v1",
               "pause_condition": pause if pause is not None else {
                   "schema_version": "chiyo.life.pause_condition.v1",
                   "available": True, "condition_ref": WAITBASIS,
                   "source": "READING_MATERIAL_NEXT_VERSION", "activity_ref": activity,
                   "waiting_on_ref": WAITDEP,
                   "how": "先选 PAUSE，再把 condition_ref 抄进 reevaluation_hint_ref"},
               "later_items": [{"item_ref": "later:1", "title": "一段不该落盘的私人标题"}]}
    if elig is not None:
        context["resume_eligibility"] = elig
    return SimpleNamespace(
        candidate_set_id=cset_id,
        candidates=tuple(candidates),
        legal_decision_enums=("START", "CONTINUE", "PAUSE", "NO_ACTION"),
        current_activity_summary={"activity_ref": activity, "status": "ACTIVE",
                                  "interruptibility": "IMMEDIATE"},
        situation_context=context)


class TraceRedactionTest(unittest.TestCase):
    def test_only_whitelisted_fields_are_kept(self):
        row = {"candidate_id": CAND_A, "source_kind": "WORLD_OPPORTUNITY",
               "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
               "legal_decisions": ["START", "NO_ACTION"],
               "executable_now": False, "target_refs": ["a", "b", "c"],
               "bounded_description": "她的私人日记正文：今天她写了……",
               "description": "私人正文不得落盘", "private_note": "也不能落盘"}
        item, unknown = g1._trace_candidate(row, prompt=True)
        self.assertEqual(item["candidate_id"], CAND_A)
        self.assertEqual(item["source_kind"], "WORLD_OPPORTUNITY")
        self.assertEqual(item["legal_decisions"], ["START", "NO_ACTION"])
        self.assertEqual(item["target_ref_count"], 3)
        self.assertTrue(item["has_description"])
        self.assertEqual(unknown, 0)
        blob = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("私人", blob)
        self.assertNotIn("private_note", blob)
        self.assertNotIn("bounded_description", blob)

    def test_an_arbitrary_ascii_enum_is_not_a_legal_enum(self):
        # 旧实现只看 ASCII 形状，PRIVATE_SECRET_BAIT 会被当成合法枚举；现在必须 UNKNOWN。
        row = {"candidate_id": CAND_A, "source_kind": "PRIVATE_SECRET_BAIT",
               "candidate_kind": "PRIVATE_SECRET_BAIT",
               "legal_decisions": ["START", "PRIVATE_SECRET_BAIT"]}
        item, unknown = g1._trace_candidate(row)
        self.assertEqual(item["source_kind"], "UNKNOWN")
        self.assertEqual(item["candidate_kind"], "UNKNOWN")
        self.assertEqual(item["legal_decisions"], ["START"])
        self.assertGreaterEqual(unknown, 3)
        blob = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("PRIVATE_SECRET_BAIT", blob)

    def test_unknown_id_prefixes_and_plain_words_are_not_ids(self):
        for bad in ("PRIVATE_SECRET_BAIT", "secret:0123456789abcdef",
                    "cand:not-a-hash", "actv_private prose", ""):
            self.assertIsNone(g1._trace_id(bad), bad)
        self.assertEqual(g1._trace_id(CAND_A), CAND_A)
        self.assertEqual(g1._trace_id(ACTV), ACTV)
        self.assertEqual(g1._trace_id(WAITDEP), WAITDEP)
        # 兼容正式链里已有的版本引用：owner 原始写法是 hex，Life 会再包一层已知前缀。
        version = "artifactversion:artifact-version:f79752e07a804eb388a70839967b5682"
        self.assertEqual(g1._trace_id(version), version)
        self.assertEqual(g1._trace_id("artifactversion:f79752e07a804eb388a70839967b5682"),
                         "artifactversion:f79752e07a804eb388a70839967b5682")
        self.assertIsNone(g1._trace_id("artifactversion:PRIVATE_SECRET_BAIT"))
        self.assertIsNone(g1._trace_id("artifact:PRIVATE_SECRET_BAIT"))

    def test_a_bait_id_is_counted_as_unknown_and_never_written(self):
        bait = _candidate("她的私人日记原文：今天")
        listing = g1._trace_candidate_list([bait, _candidate(CAND_B)])
        self.assertEqual(listing["unknown"], 1)
        self.assertEqual(listing["total"], 2)
        blob = json.dumps(listing, ensure_ascii=False)
        self.assertNotIn("私人日记", blob)
        self.assertIn(CAND_B, blob)

    def test_missing_fields_are_counted_not_guessed(self):
        listing = g1._trace_candidate_list([{"source_kind": "WORLD_OPPORTUNITY"}, "not-a-row"])
        self.assertEqual(listing["unknown"], 2)
        self.assertEqual(listing["count"], 1)  # 非 mapping 不伪造条目
        self.assertEqual(listing["total"], 2)

    def test_the_observation_copy_is_bounded_but_the_total_is_reported(self):
        many = [_candidate("cand:%016x" % index) for index in range(50)]
        listing = g1._trace_candidate_list(many)
        self.assertEqual(listing["count"], g1.CANDIDATE_TRACE_MAX_CANDIDATES)
        self.assertEqual(listing["total"], 50)
        self.assertTrue(listing["truncated"])

    def test_pause_and_activity_snapshots_drop_free_text(self):
        context = {"pause_condition": {"available": True, "condition_ref": WAITBASIS,
                                       "source": "READING_MATERIAL_NEXT_VERSION",
                                       "activity_ref": ACTV, "waiting_on_ref": WAITDEP,
                                       "how": "PRIVATE_SECRET_BAIT", "note": "PRIVATE_SECRET_BAIT"},
                   "later_items": [{"title": "PRIVATE_SECRET_BAIT"}]}
        pause = g1._trace_pause_condition(context)
        self.assertEqual(pause["condition_ref"], WAITBASIS)
        self.assertEqual(pause["source"], "READING_MATERIAL_NEXT_VERSION")
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(pause, ensure_ascii=False))
        activity = g1._trace_activity({"activity_ref": ACTV, "status": "ACTIVE",
                                       "detail": "PRIVATE_SECRET_BAIT"})
        self.assertEqual(activity["activity_ref"], ACTV)
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(activity, ensure_ascii=False))
        # 没有同轮快照：写 UNKNOWN，不当 EMPTY。
        self.assertEqual(g1._trace_pause_condition({})["status"], "UNKNOWN")
        self.assertEqual(g1._trace_activity(None)["status"], "UNKNOWN")

    def test_an_exception_message_is_never_kept(self):
        self.assertEqual(g1._trace_error(OSError("PRIVATE_SECRET_BAIT /secret/path")), "OSError")

        class _BaitError(Exception):
            pass

        self.assertEqual(g1._trace_error(_BaitError("PRIVATE_SECRET_BAIT")), "ERROR")
        self.assertNotIn("PRIVATE_SECRET_BAIT",
                         g1._trace_error(OSError("PRIVATE_SECRET_BAIT")))

    def test_a_wake_view_never_copies_arbitrary_fields(self):
        view, unknown = g1._trace_wake({
            "kind": "CHECK_WINDOW", "status": "ACTED", "outcome": "ACTED",
            "decision": "START", "llm_calls": 1, "decision_id": DEC,
            "activity_ref": ACTV, "detail": "PRIVATE_SECRET_BAIT",
            "reason_codes": ["NO_ACTION_CHOSEN", "PRIVATE_SECRET_BAIT"],
            "selected_candidate_ref": CAND_A,
            "extra": {"owner_version_id": "artifactversion:0123456789abcdef",
                      "private_note": "PRIVATE_SECRET_BAIT"}})
        self.assertEqual(view["decision"], "START")
        self.assertEqual(view["decision_id"], DEC)
        self.assertEqual(view["reason_codes"], ["NO_ACTION_CHOSEN"])
        self.assertEqual(unknown, 1)
        self.assertTrue(view["model_decision"])
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(view, ensure_ascii=False))


class TraceFileTest(unittest.TestCase):
    def _root(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="g1-trace-file-"))

    def test_disabled_writes_nothing_and_reads_no_extra_state(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=False)
        trace.begin_round(1, trigger="p1")
        trace.record_request(_request([_candidate(CAND_A)]))
        trace.record_prompt({"candidates": [_candidate(CAND_A, prompt=True)]},
                            send_status=g1.SEND_STATUS_UNKNOWN)
        trace.end_round([{"llm_calls": 1}])
        trace.event("OPERATOR_PAUSE_SET", paused=True)
        trace.observe_after_tick([{"decision_id": DEC}])
        self.assertFalse(_trace_path(root, "p1").exists())
        self.assertEqual(trace.records, 0)
        self.assertFalse(trace.view()["enabled"])

    def test_enabled_writes_only_whitelisted_structured_rows(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        trace.begin_round(1, trigger="p1")
        trace.record_request(_request([_candidate(CAND_A), {"candidate_id": "PRIVATE_SECRET_BAIT",
                                                            "source_kind": "PRIVATE_SECRET_BAIT"}]))
        trace.record_prompt({"candidates": [_candidate(CAND_A, prompt=True)]},
                            send_status=g1.SEND_STATUS_UNKNOWN,
                            evidence="RESPONSE_RETURNED")
        trace.end_round([{"llm_calls": 1, "decision": "START",
                          "detail": "PRIVATE_SECRET_BAIT"}])
        rows = _rows(trace.path)
        stages = [row["stage"] for row in rows]
        self.assertIn(g1.STAGE_REQUEST, stages)
        self.assertIn(g1.STAGE_PROMPT, stages)
        self.assertIn(g1.STAGE_ROUND, stages)
        self.assertNotIn("PRIVATE_SECRET_BAIT", trace.path.read_text(encoding="utf-8"))

    def test_a_write_failure_is_reported_without_the_exception_message(self):
        root = self._root()
        # 占住 experiment-g1 这个名字，让目录创建失败。
        g1._experiment_dir(root).write_text("not a directory", encoding="utf-8")
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        trace.record_request(_request([_candidate(CAND_A)]))
        trace.end_round([{"llm_calls": 1}])
        trace.observe_after_tick([])
        self.assertTrue(trace.error)
        # 只保留有界异常类型名，不夹带路径/消息正文。
        self.assertNotIn(str(root), trace.error)
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(trace.view(), ensure_ascii=False))
        self.assertEqual(trace.records, 0)
        self.assertGreaterEqual(trace.dropped_records, 1)

    def test_the_materialized_set_is_read_from_the_same_request_id(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        cset = SimpleNamespace(to_dict=lambda: {
            "candidate_set_id": CSET,
            "candidate_refs": [CAND_B, CAND_A, "cand:0123456789abcdee"],
            "global_truncated": True,
            "truncated_sources": {"WORLD_OPPORTUNITY": {"discovered": 30, "emitted": 25}}})
        trace.attach(SimpleNamespace(candidate_materializer=SimpleNamespace(
            _candidate_sets_by_id={CSET: cset})))
        request = _request([_candidate(CAND_B), _candidate(CAND_A)])
        trace.record_request(request)
        trace.record_request(request)  # 同一个 cset 只写一次
        rows = [row for row in _rows(trace.path) if row["stage"] == g1.STAGE_CANDIDATE_SET]
        self.assertEqual(len(rows), 1)
        self.assertEqual([item["candidate_id"] for item in rows[0]["candidates"]],
                         [CAND_B, CAND_A, "cand:0123456789abcdee"])
        self.assertEqual(rows[0]["total"], 3)
        self.assertTrue(rows[0]["global_truncated"])
        self.assertEqual(rows[0]["truncated_sources"]["WORLD_OPPORTUNITY"]["emitted"], 25)
        self.assertIn("not the raw discovery set", rows[0]["scope"])

    def test_unknown_refs_keep_the_real_total_and_are_counted(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        cset = SimpleNamespace(to_dict=lambda: {
            "candidate_set_id": CSET,
            "candidate_refs": [CAND_A, "PRIVATE_SECRET_BAIT", "secret:0123456789abcdef"]})
        trace.attach(SimpleNamespace(candidate_materializer=SimpleNamespace(
            _candidate_sets_by_id={CSET: cset})))
        trace.record_request(_request([_candidate(CAND_A)]))
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_CANDIDATE_SET][0]
        self.assertEqual(row["status"], "OBSERVED")
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["total"], 3)  # 实际 total 不被过滤后缩小
        self.assertEqual(row["unknown"], 2)
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(row, ensure_ascii=False))

    def test_a_missing_candidate_set_shape_is_unknown_not_empty(self):
        for payload in ({"candidate_set_id": CSET},
                        {"candidate_set_id": CSET, "candidate_refs": "PRIVATE_SECRET_BAIT"}):
            root = self._root()
            trace = g1.CandidateTrace(root, phase="p1", enabled=True)
            cset = SimpleNamespace(to_dict=lambda p=payload: dict(p))
            trace.attach(SimpleNamespace(candidate_materializer=SimpleNamespace(
                _candidate_sets_by_id={CSET: cset})))
            trace.record_request(_request([_candidate(CAND_A)]))
            row = [item for item in _rows(trace.path)
                   if item["stage"] == g1.STAGE_CANDIDATE_SET][0]
            self.assertEqual(row["status"], "UNKNOWN", payload)
            self.assertNotIn("count", row)

    def test_an_unreadable_candidate_set_is_error_not_empty(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)

        def _boom():
            raise OSError("PRIVATE_SECRET_BAIT /secret/path")

        cset = SimpleNamespace(to_dict=_boom)
        trace.attach(SimpleNamespace(candidate_materializer=SimpleNamespace(
            _candidate_sets_by_id={CSET: cset})))
        trace.record_request(_request([_candidate(CAND_A)]))
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_CANDIDATE_SET][0]
        self.assertEqual(row["status"], "ERROR")
        self.assertEqual(row["error"], "OSError")
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(row, ensure_ascii=False))

    def test_an_unknown_candidate_set_is_recorded_as_unknown(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        trace.record_request(_request([_candidate(CAND_A)], cset_id="cset:missing"))
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_CANDIDATE_SET][0]
        self.assertEqual(row["status"], "UNKNOWN")

    def test_a_prompt_or_request_without_a_candidate_sequence_is_unknown(self):
        root = self._root()
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        # prompt shape 是 mapping，但 candidates 缺失/不是序列：不能写 count=0 冒空集合。
        trace.record_prompt({}, send_status=g1.SEND_STATUS_UNKNOWN)
        trace.record_prompt({"candidates": "PRIVATE_SECRET_BAIT"},
                            send_status=g1.SEND_STATUS_UNKNOWN)
        prompt_rows = [item for item in _rows(trace.path)
                       if item["stage"] == g1.STAGE_PROMPT]
        for row in prompt_rows:
            self.assertEqual(row["status"], "UNKNOWN")
            self.assertEqual(row["count"], 0)
            self.assertIn("no candidate sequence", row["why"])
        # request 侧同样不能把缺失属性读成空集合。
        trace.record_request(SimpleNamespace(candidate_set_id=None))
        request_row = [item for item in _rows(trace.path)
                       if item["stage"] == g1.STAGE_REQUEST][0]
        self.assertEqual(request_row["status"], "UNKNOWN")
        self.assertEqual(request_row["count"], 0)
        self.assertIn("missing or not a sequence", request_row["why"])
        self.assertNotIn("PRIVATE_SECRET_BAIT",
                         trace.path.read_text(encoding="utf-8"))


class _FakeActivityRead:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = 0

    def list_open_activities(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)


class _FakeCoordinator:
    def __init__(self, *, conditions=None, eligibilities=None, error=None):
        self.conditions = conditions if conditions is not None else []
        self.eligibilities = eligibilities if eligibilities is not None else []
        self.error = error

    def list_conditions(self, *, activity_id=None, only_open=False):
        if self.error is not None:
            raise self.error
        return [row for row in self.conditions if activity_id in (None, row.get("activity_id"))]

    def list_eligibilities(self, *, activity_id=None, only_open=False):
        if self.error is not None:
            raise self.error
        return [row for row in self.eligibilities
                if activity_id in (None, row.get("activity_id"))]


class _FakeDecisionRead:
    def __init__(self, record=None, error=None):
        self.record = record
        self.error = error

    def get_decision(self, decision_id):
        if self.error is not None:
            raise self.error
        return self.record


class _FakeRuntime:
    def __init__(self, *, activity_read=None, coordinator=None, decision_read=None):
        self.activity_read = activity_read or _FakeActivityRead()
        self.waiting_coordinator = coordinator or _FakeCoordinator()
        self.decision_read = decision_read or _FakeDecisionRead()


class SnapshotTest(unittest.TestCase):
    def _trace(self, runtime, root=None):
        trace = g1.CandidateTrace(root or Path(tempfile.mkdtemp(prefix="g1-snap-")),
                                  phase="p1", enabled=True)
        trace.attach(runtime)
        return trace

    def test_disabled_snapshot_performs_no_read(self):
        class _Untouchable:
            def __getattr__(self, name):
                raise AssertionError("PRIVATE_SECRET_BAIT: trace read while disabled")
        trace = g1.CandidateTrace(Path(tempfile.mkdtemp(prefix="g1-snap-off-")),
                                  phase="p1", enabled=False)
        trace.attach(_Untouchable())
        trace.observe_after_tick([{"decision_id": DEC}])
        self.assertEqual(trace.records, 0)

    def test_paused_condition_and_hint_are_observed_without_fabricating_open(self):
        runtime = _FakeRuntime(
            activity_read=_FakeActivityRead([
                {"activity_id": ACTV, "status": "PAUSED", "revision": 3},
                {"activity_id": "actv_1111111111111111", "status": "ACTIVE"},
            ]),
            coordinator=_FakeCoordinator(
                conditions=[{"activity_id": ACTV, "condition_id": RCOND,
                             "status": "PENDING", "condition_kind": "EXTERNAL_RESULT_EVENT",
                             "waiting_on_ref": WAITDEP, "activity_revision_at_bind": 3}],
                eligibilities=[]),
            decision_read=_FakeDecisionRead({"reevaluation_hint_ref": WAITBASIS}))
        trace = self._trace(runtime)
        trace.observe_after_tick([{"decision_id": DEC}])
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_SNAPSHOT][0]
        self.assertEqual(row["snapshot_time"], "POST_TICK_READ")
        self.assertTrue(row["not_pre_send_atomic"])
        self.assertEqual(row["submitted_hint"]["hint_ref"], WAITBASIS)
        paused = row["paused_activities"]
        self.assertEqual(paused["status"], "OBSERVED")
        self.assertEqual(paused["count"], 1)
        self.assertEqual(paused["activities"][0]["activity_id"], ACTV)
        condition = paused["activities"][0]["conditions"]["items"][0]
        self.assertEqual(condition["condition_id"], RCOND)
        self.assertEqual(condition["status"], "PENDING")
        # 没有资格就照实写 0，绝不手造 OPEN。
        elig = paused["activities"][0]["eligibilities"]
        self.assertEqual(elig["status"], "OBSERVED")
        self.assertEqual(elig["count"], 0)

    def test_no_decision_id_keeps_the_submitted_hint_unknown(self):
        trace = self._trace(_FakeRuntime())
        trace.observe_after_tick([])
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_SNAPSHOT][0]
        self.assertEqual(row["submitted_hint"]["status"], "UNKNOWN")

    def test_a_read_failure_is_error_not_empty(self):
        trace = self._trace(_FakeRuntime(
            activity_read=_FakeActivityRead(error=OSError("PRIVATE_SECRET_BAIT"))))
        trace.observe_after_tick([{"decision_id": DEC}])
        row = [item for item in _rows(trace.path)
               if item["stage"] == g1.STAGE_SNAPSHOT][0]
        self.assertEqual(row["paused_activities"]["status"], "ERROR")
        self.assertEqual(row["paused_activities"]["error"], "OSError")
        self.assertNotIn("PRIVATE_SECRET_BAIT", json.dumps(row, ensure_ascii=False))


class GateTraceTest(unittest.TestCase):
    def _gate(self, inner, *, budget=None, enabled=True, root=None):
        root = root or Path(tempfile.mkdtemp(prefix="g1-gate-trace-"))
        trace = g1.CandidateTrace(root, phase="p1", enabled=enabled)
        latch = g1.SendLatch(lambda: False)
        return g1.G1CognitionGate(inner, latch=latch,
                                  budget=budget or _FakeBudget(), trace=trace), trace

    def test_capture_does_not_change_the_inner_call_budget_or_result(self):
        request = _request([_candidate(CAND_A)])
        shape = {"candidates": [_candidate(CAND_A, prompt=True)]}
        result_off = _FakeInner(shape=shape, budget=_FakeBudget())
        result_on = _FakeInner(shape=shape, budget=_FakeBudget())
        gate_off, trace_off = self._gate(result_off, budget=result_off.budget, enabled=False)
        gate_on, trace_on = self._gate(result_on, budget=result_on.budget, enabled=True)
        out_off = gate_off.invoke(request)
        out_on = gate_on.invoke(request)
        self.assertEqual(out_off.failure_class, out_on.failure_class)
        self.assertEqual(len(result_off.calls), len(result_on.calls))
        self.assertEqual(result_off.calls[0]["schema_retry_index"],
                         result_on.calls[0]["schema_retry_index"])
        self.assertIs(result_on.calls[0]["request"], request)
        self.assertEqual(result_off.budget.calls, result_on.budget.calls)
        self.assertEqual(trace_off.records, 0)
        self.assertGreater(trace_on.records, 0)
        # 关闭时 gate 不保留 trace：发送路径连只读计数都不会多做。
        self.assertIsNone(gate_off.trace)
        self.assertIs(gate_on.trace, trace_on)

    def test_request_and_final_prompt_are_recorded_from_the_same_call(self):
        request = _request([_candidate(CAND_B), _candidate(CAND_A)])
        shape = {"candidates": [_candidate(CAND_B, prompt=True)]}
        gate, trace = self._gate(_FakeInner(shape=shape, budget=_FakeBudget()))
        gate.invoke(request)
        rows = _rows(trace.path)
        request_row = [row for row in rows if row["stage"] == g1.STAGE_REQUEST][0]
        prompt_row = [row for row in rows if row["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual([item["candidate_id"] for item in request_row["candidates"]],
                         [CAND_B, CAND_A])
        self.assertEqual([item["candidate_id"] for item in prompt_row["candidates"]],
                         [CAND_B])
        # 正常返回只证明拿到响应：不据此声称实际网络发送。
        self.assertEqual(prompt_row["send_status"], "UNKNOWN")
        self.assertIsNone(prompt_row["sent"])
        self.assertEqual(prompt_row["send_evidence"], "RESPONSE_RETURNED")
        self.assertEqual(request_row["current_activity"]["activity_ref"], ACTV)
        self.assertEqual(request_row["pause_condition"]["condition_ref"], WAITBASIS)
        # 没有同轮资格快照就写 UNKNOWN，不造 id。
        self.assertEqual(request_row["resume_eligibility"]["status"], "UNKNOWN")

    def test_only_a_certified_pre_send_refusal_is_not_sent(self):
        request = _request([_candidate(CAND_A)])
        shape = {"candidates": [_candidate(CAND_A, prompt=True)]}
        gate, trace = self._gate(_FakeInner(shape=shape, budget=_FakeBudget(), reject=True))
        with self.assertRaises(RuntimeError):
            gate.invoke(request)
        rows = _rows(trace.path)
        self.assertIn(g1.STAGE_REQUEST, [row["stage"] for row in rows])
        unsent = [row for row in rows if row["stage"] == g1.STAGE_PROMPT_UNSENT]
        self.assertEqual(len(unsent), 1)
        self.assertEqual(unsent[0]["send_status"], "NOT_SENT")
        self.assertFalse(unsent[0]["sent"])
        self.assertEqual(unsent[0]["error"], "REQUEST_BUDGET_RUN_WINDOW_LIMIT")

    def test_a_generic_exception_is_unknown_not_not_sent(self):
        # 异常也可能发生在发送之后（超时/丢答）：没有直接证据只能 UNKNOWN。
        request = _request([_candidate(CAND_A)])
        shape = {"candidates": [_candidate(CAND_A, prompt=True)]}
        gate, trace = self._gate(_FakeInner(
            shape=shape, budget=_FakeBudget(),
            raise_exc=RuntimeError("PRIVATE_SECRET_BAIT after send")))
        with self.assertRaises(RuntimeError):
            gate.invoke(request)
        rows = _rows(trace.path)
        self.assertNotIn(g1.STAGE_PROMPT_UNSENT, [row["stage"] for row in rows])
        prompt_row = [row for row in rows if row["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual(prompt_row["send_status"], "UNKNOWN")
        self.assertIsNone(prompt_row["sent"])
        self.assertEqual(prompt_row["error"], "RuntimeError")
        self.assertNotIn("PRIVATE_SECRET_BAIT",
                         trace.path.read_text(encoding="utf-8"))

    def test_a_provider_failure_keeps_the_prompt_row_with_its_error(self):
        request = _request([_candidate(CAND_A)])
        shape = {"candidates": [_candidate(CAND_A, prompt=True)]}
        gate, trace = self._gate(_FakeInner(shape=shape, budget=_FakeBudget(),
                                            failure="MODEL_TIMEOUT"))
        gate.invoke(request)
        prompt_row = [row for row in _rows(trace.path)
                      if row["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual(prompt_row["send_status"], "UNKNOWN")
        self.assertEqual(prompt_row["error"], "MODEL_TIMEOUT")
        self.assertTrue(gate.latch.closed)

    def test_no_structured_prompt_is_reported_as_unknown_not_rebuilt(self):
        request = _request([_candidate(CAND_A)])
        inner = _FakeInner(shape=None, budget=_FakeBudget())
        gate, trace = self._gate(inner)
        gate.invoke(request)
        prompt_row = [row for row in _rows(trace.path)
                      if row["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual(prompt_row["count"], 0)
        self.assertEqual(prompt_row["error"], "NO_STRUCTURED_PROMPT")
        self.assertEqual(prompt_row["status"], "UNKNOWN")
        self.assertEqual(len(inner.requests_seen), 1)

    def test_the_prompt_limit_is_observed_not_reconstructed(self):
        # 请求里有 30 条；最终 prompt 只带 16 条（MAX_CANDIDATES_IN_PROMPT）。观测只记
        # inner 真正返回的 16 条，不从请求补到 30、也不猜第 17 条。
        request = _request([_candidate("cand:%016x" % index) for index in range(30)])
        shape = {"candidates": [_candidate("cand:%016x" % index, prompt=True)
                                for index in range(16)]}
        gate, trace = self._gate(_FakeInner(shape=shape, budget=_FakeBudget()))
        gate.invoke(request)
        prompt_row = [row for row in _rows(trace.path)
                      if row["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual(prompt_row["count"], 16)
        self.assertEqual(prompt_row["total"], 16)
        self.assertFalse(prompt_row["truncated"])
        self.assertEqual([item["candidate_id"] for item in prompt_row["candidates"]][-1],
                         "cand:000000000000000f")
        request_row = [row for row in _rows(trace.path)
                       if row["stage"] == g1.STAGE_REQUEST][0]
        self.assertEqual(request_row["total"], 30)

    def test_the_real_build_prompt_trim_is_observed_not_a_preseeded_shape(self):
        # 不是 FakeInner 预塞 16 行：真的调用正式 adapter.build_prompt，让 12000 字符结构
        # 裁剪实际发生，再核对观测到的是裁剪后的同一份结构。
        from agency_cognition_adapter import ProductionAgencyCognitionAdapter

        candidates = []
        for index in range(20):
            row = _candidate("cand:%016x" % index, prompt=True)
            row["description"] = "D" * 160
            candidates.append(row)
        request = _request(candidates)
        request.policy_version = "ag0.candidate_policy.v1"
        request.allowed_reason_codes = ["NO_ACTION_CHOSEN"]
        request.observed_fact_keys = []
        request.capacity_summary = {}
        request.prior_experiences = [{"text": "P" * 600} for _ in range(3)]
        request.situation_context = {
            "shared_practice": {
                "available": True, "title": "T" * 100, "state": "OPEN",
                "work_text": "W" * 4000,
                "materials": [{"artifact_id": "artifact:%02d" % i, "title": "M" * 80,
                               "state": "READY", "excerpt": "E" * 600} for i in range(6)],
                "entries": [{"contributor": "c", "kind": "k", "text": "x" * 400}
                            for _ in range(6)],
            },
        }
        adapter = ProductionAgencyCognitionAdapter(client=None, timeout_s=1.0,
                                                   max_output_tokens=128)
        text, shape = adapter.build_prompt(request)
        self.assertLessEqual(len(text), adapter.max_prompt_chars)
        self.assertTrue(adapter.last_prompt_trim.get("trimmed"), adapter.last_prompt_trim)
        expected = ["cand:%016x" % index for index in range(16)]
        self.assertEqual([item["candidate_id"] for item in shape["candidates"]], expected)

        trace = g1.CandidateTrace(Path(tempfile.mkdtemp(prefix="g1-real-prompt-")),
                                  phase="p1", enabled=True)
        trace.record_prompt(shape, send_status=g1.SEND_STATUS_UNKNOWN, evidence="DIRECT_BUILD")
        row = [item for item in _rows(trace.path) if item["stage"] == g1.STAGE_PROMPT][0]
        self.assertEqual(row["count"], 16)
        self.assertEqual(row["total"], 16)
        self.assertEqual([item["candidate_id"] for item in row["candidates"]], expected)
        self.assertEqual(row["send_status"], "UNKNOWN")
        self.assertEqual(row["send_evidence"], "DIRECT_BUILD")

    def test_a_zero_request_round_is_recorded_as_such(self):
        gate, trace = self._gate(_FakeInner(shape={"candidates": []}))
        trace.begin_round(3, trigger="p1")
        trace.end_round([{"status": "QUIET", "llm_calls": 0}])
        row = [item for item in _rows(trace.path) if item["stage"] == g1.STAGE_ROUND][0]
        self.assertEqual(row["stage"], g1.STAGE_ROUND)
        self.assertFalse(row["request_present"])
        self.assertFalse(row["model_decision_present"])
        self.assertEqual(row["llm_calls"], 0)


class HarnessIntegrationTest(unittest.TestCase):
    """最小实际接线冒烟：既有 prepare + offline run，真 owner、替身 transport、HTTP 硬拒。"""

    def _run(self, scratch: Path, name: str, *, capture: bool):
        root = scratch / name
        with g1._http_forbidden(), g1.offline_clock():
            prepared = g1.prepare(root, expected_root=root, offline=True)
            self.assertTrue(prepared.get("ok"), prepared)
            result = g1.start_run(root, expected_root=root, execute_approved=True,
                                  offline=True, capture_candidates=capture)
        return root, result

    def test_default_off_writes_nothing_and_capture_is_staged(self):
        scratch = Path(tempfile.mkdtemp(prefix="g1-trace-harness-"))
        off_root, off = self._run(scratch, "off", capture=False)
        on_root, on = self._run(scratch, "on", capture=True)

        # -- 默认关：没有观测文件，phase 报告里也明确关闭 -------------------------
        for phase in ("p1", "p2"):
            self.assertFalse(_trace_path(off_root, phase).exists(), phase)
            self.assertFalse(((off.get(phase) or {}).get("candidate_trace") or {})
                             .get("enabled"), phase)
            config = g1._read_json(g1._experiment_dir(off_root) / ("g1-config-%s.json" % phase))
            self.assertFalse(config.get("capture_candidates"), phase)

        # -- 开启：开关随 phase config 传播，两个 phase 都落盘 --------------------
        for phase in ("p1", "p2"):
            config = g1._read_json(g1._experiment_dir(on_root) / ("g1-config-%s.json" % phase))
            self.assertTrue(config.get("capture_candidates"), phase)
            trace_view = (on.get(phase) or {}).get("candidate_trace") or {}
            self.assertTrue(trace_view.get("enabled"), phase)
            self.assertGreater(int(trace_view.get("records") or 0), 0, phase)
            self.assertIsNone(trace_view.get("error"), phase)
            self.assertTrue(_trace_path(on_root, phase).exists(), phase)

        # -- 阶段齐全，顺序仍在：最终 prompt 是请求集合的有序子集 ----------------
        request_ids: list[str] = []
        prompt_ids: list[str] = []
        for phase in ("p1", "p2"):
            rows = _rows(_trace_path(on_root, phase))
            stages = {row["stage"] for row in rows}
            self.assertIn(g1.STAGE_CANDIDATE_SET, stages, phase)
            self.assertIn(g1.STAGE_ROUND, stages, phase)
            self.assertIn(g1.STAGE_SNAPSHOT, stages, phase)
            self.assertTrue({g1.STAGE_REQUEST, g1.STAGE_PROMPT}
                            & stages, (phase, stages))
            per_round_request: dict[int, list[str]] = {}
            per_round_prompt: dict[int, list[str]] = {}
            for row in rows:
                ids = [item["candidate_id"] for item in row.get("candidates", [])
                       if item.get("candidate_id")]
                if row["stage"] == g1.STAGE_REQUEST:
                    per_round_request.setdefault(row["round"], []).extend(ids)
                    request_ids.extend(ids)
                if row["stage"] == g1.STAGE_PROMPT:
                    per_round_prompt.setdefault(row["round"], []).extend(ids)
                    prompt_ids.extend(ids)
                for cid in ids:
                    self.assertRegex(cid, ID_SHAPE)
                if row["stage"] == g1.STAGE_SNAPSHOT:
                    self.assertEqual(row["snapshot_time"], "POST_TICK_READ", phase)
                    self.assertTrue(row["not_pre_send_atomic"], phase)
                # 观测文件本身不得出现材料正文或 prompt/completion 字段名。
                self.assertNotIn(g1.MATERIAL_TEXT, json.dumps(row, ensure_ascii=False))
                self.assertNotIn(g1.CONTROL_TEXT, json.dumps(row, ensure_ascii=False))
                self.assertNotIn("prompt", row)
                self.assertNotIn("completion", row)
            # 同一轮内：最终 prompt 的候选保持请求里的相对顺序（trim/截断只删尾）。
            for round_index, ids in per_round_prompt.items():
                base = per_round_request.get(round_index) or []
                ordered = [cid for cid in base if cid in set(ids)]
                self.assertEqual(ids, ordered, (phase, round_index, base, ids))
        self.assertTrue(request_ids)
        self.assertTrue(prompt_ids)

        # -- 默认关不改变行为：覆盖结果与账本等价 ---------------------------------
        self.assertEqual((off.get("coverage") or {}).get("g1_chain_complete"),
                         (on.get("coverage") or {}).get("g1_chain_complete"))
        self.assertTrue(((on.get("coverage") or {}).get("g1_chain_complete")), on.get("coverage"))
        self.assertTrue(((on.get("p1") or {}).get("write_confirmed")))
        self.assertEqual(((off.get("ledger") or {}).get("run_window_calls")),
                         ((on.get("ledger") or {}).get("run_window_calls")))

        # 暂停轮是零请求的事实同样照实落盘。
        p2_rounds = [row for row in _rows(_trace_path(on_root, "p2"))
                     if row["stage"] == g1.STAGE_ROUND]
        self.assertTrue(any(row["request_present"] is False and row["llm_calls"] == 0
                            for row in p2_rounds), p2_rounds)


if __name__ == "__main__":
    unittest.main()
