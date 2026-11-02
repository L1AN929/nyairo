#!/usr/bin/env python3
"""d 批（非 G1 的单次材料观测）的最小离线验收。

本文件只测这一批新增的边界，**不重跑** a/b/c 的历史套件：

1. d 的封闭身份（root/独立 authority/run/域/历史 141/2026-10-31 01:00 截止），以及
   a/b/c 的既有映射、截止一个字段都没动；错批 root、错批截止的 prereg 仍然失败关闭。
2. 两条编译期固定文本与生产 ``pause_condition_view`` 的真实 ``how`` 对得上；
   两个布尔只做逐字包含判断，四种取值（都有/只有事实句/都没有/取不到）各自独立。
3. 观测行与 ``send_status`` 同一行，只落布尔/None；a/b/c 的 trace 一个字段都不多。
4. d 的完整离线链：真 owner/runtime、**真标准 World owner**、脚本 Decision/内容替身、
   HTTP 硬拒。``_provision_operator_material`` 与 ``SubmissionInjector`` 都被打成 raise——
   d 的路径从不调用它们（不是"计数为 0"）。prepare 不产生材料/对照，run 不注入、不暂停、
   不重启。natural phase 用同一实例根起标准 World（gate 显式 off），World 的
   ``get_status()`` 真实读回 READY，观察到的机会经 AG-0 进入正式请求。
5. cap 用满即停、错身份 phase 拒绝、同根不重跑。
6. 首步装配：World 的感知 provider 与动作 adapter 同时就位时，正式 ``evaluate_once``／
   AG-1 提交／AG-2／AR-0 真的构造出首步与 Action，并在 gate off 得到明确
   ``PRE_SUBMIT_FAILED``/``GATE_OFF`` 拒绝、零 World 效果。Decision 仍是明确标注的测试
   替身（只从真实 World 候选里挑），不是自然选择。

今天的事实（也写进结果文件）：这条装配下 World 真的 READY、世界机会真的进 AG-0，
离线 fixture 的脚本 Decision 因此会发出真实请求（``START``/``NO_ACTION`` 计划）；
两条固定句子在最终请求里分别可见，但 ``send_status`` 仍是 UNKNOWN，不冒送达。
a/b/c 的路径不在本文件覆盖内。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402

# d 的注册截止已过真实墙钟。本文件所有根都在 mkdtemp 下，因此显式进入本进程的离线夹具
# 参考时刻（本批截止前 1 小时）。它只改本进程内存，不写环境变量、不写任何持久字段；
# profile 常量与真实 run 语义不动，真实根带它会在写入之前被拒。
g1.enter_offline_fixture(g1.D_PROFILE)


def _install_world_isolation(root: Path):
    """进程内跑自然 phase/首步时，补上真实 phase 子进程本来就会拿到的 World 写面映射。

    ``_phase_child_env`` 会把 home/registry/lifecycle 七个变量显式交给子进程；进程内夹具
    没有那层子进程，AR-0 的 lifecycle 读写就会回落到仓库默认 ``data/world/run``。这里只在
    本进程临时设置并原样恢复，不写任何持久配置，也不碰父 shell。
    """

    home = g1._instance_world_home(root)
    if not home:
        return lambda: None
    wanted = g1._world_isolation_env(home)
    previous = {name: os.environ.get(name) for name in wanted}
    os.environ.update(wanted)

    def restore() -> None:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    return restore


#: 与本单批准的预登记逐字一致的第二份副本：这里故意再写一遍，钉住两条常量。
FACT_SENTENCE = ("While the Activity is PAUSED, ABANDON is not available through the "
                 "formal decision path; if you want to abandon it, make that choice while "
                 "it is still ACTIVE.")
TAIL = ("OPEN and nothing resumes by itself, and a later Decision may choose RESUME; a "
        "background RESUME never preempts another foreground activity")


def _rows(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _trace_path(root: Path) -> Path:
    return g1._experiment_dir(root) / (g1.CANDIDATE_TRACE_FILENAME % g1.PHASE_NATURAL)


class DProfileTest(unittest.TestCase):
    """d 是第四个封闭身份；a/b/c 一个字段都不动。"""

    def test_d_is_the_closed_fourth_identity(self):
        d = g1.D_PROFILE
        self.assertEqual(str(d.root), "/root/nyairo-pause-observe-20261030-d")
        self.assertEqual(str(d.authority_dir),
                         "/root/nyairo-pause-observe-20261030-d-memory-authority")
        self.assertEqual(str(d.authority_db),
                         "/root/nyairo-pause-observe-20261030-d-memory-authority/authority.db")
        self.assertEqual(d.run_id, "run:pause-observe-20261030-d")
        self.assertEqual(d.domain_id, "pause-observe:20261030-d")
        self.assertEqual(d.expected_history, 141)
        self.assertEqual(d.deadline.isoformat(), "2026-10-31T01:00:00+08:00")
        # 缺省仍是 a，d 只能用 --batch d 选中。
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])
        self.assertIs(g1._select_batch("d"), d)

    def test_a_b_c_keep_their_own_identity(self):
        self.assertEqual(str(g1.BATCH_PROFILES["a"].root), "/root/nyairo-g1-real-20261030")
        self.assertEqual(str(g1.BATCH_PROFILES["b"].root), "/root/nyairo-g1-real-20261030-b")
        self.assertEqual(str(g1.BATCH_PROFILES["c"].root), "/root/nyairo-g1-real-20261030-c")
        self.assertEqual(g1.BATCH_PROFILES["a"].deadline.isoformat(),
                         "2026-10-30T17:00:00+08:00")
        self.assertEqual(g1.BATCH_PROFILES["b"].expected_history, 101)
        self.assertEqual(g1.BATCH_PROFILES["c"].expected_history, 121)
        self.assertEqual(g1.DEADLINE, g1.BATCH_PROFILES["a"].deadline)

    def test_d_is_registered_outside_the_original_profile_set(self):
        # d 独立注册：旧集合仍是 a/b/c，旧硬编码枚举断言不需要跟着改。
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertNotIn("d", g1.BATCH_PROFILES)
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertIs(g1.MODE_PROFILES["d"], g1.D_PROFILE)
        # 原三批的映射值一个没动。
        self.assertEqual([g1.BATCH_PROFILES[key].run_name for key in ("a", "b", "c")],
                         ["g1-real-20261030", "g1-real-20261030-b",
                          "g1-real-20261030-c"])
        self.assertEqual({key: value.deadline.isoformat()
                          for key, value in g1.BATCH_PROFILES.items()},
                         {"a": "2026-10-30T17:00:00+08:00",
                          "b": "2026-10-30T17:00:00+08:00",
                          "c": "2026-10-31T01:00:00+08:00"})

    def test_each_batch_root_is_refused_by_the_others(self):
        with g1._use_batch("d"):
            self.assertIsNone(g1._check_root(g1.D_PROFILE.root))
            for key in ("a", "b", "c"):
                refused = g1._check_root(g1.BATCH_PROFILES[key].root)
                self.assertEqual(refused.get("reason_code"),
                                 g1.REASON_BATCH_IDENTITY_MISMATCH, key)
                self.assertEqual(refused.get("root_belongs_to"), key)
        with g1._use_batch("a"):
            refused = g1._check_root(g1.D_PROFILE.root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("root_belongs_to"), "d")

    def test_a_prereg_with_another_deadline_is_refused(self):
        d = g1.D_PROFILE
        root = Path(tempfile.mkdtemp(prefix="g1-d-prereg-"))
        g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": "d", "run_id": d.run_id,
            "domain_id": d.domain_id, "root": str(root),
            "state_root": str(root / "one-day"),
            "deadline": g1.BATCH_PROFILES["a"].deadline.isoformat()})
        with g1._use_batch("d"):
            refused = g1._verify_instance_identity(root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("field"), "deadline")


class PauseMaterialBooleansTest(unittest.TestCase):
    """两个布尔只认逐字包含；取不到是 None，不是 False。"""

    def _view(self, basis):
        import autonomous_life_cycle as alc

        return alc.AutonomousLifeCycle.pause_condition_view(
            SimpleNamespace(_last_foreground_record=None), basis)

    def test_the_two_constants_are_the_registered_sentences(self):
        self.assertEqual(g1.D_PAUSE_FACT_SENTENCE, FACT_SENTENCE)
        self.assertEqual(g1.D_PAUSE_TAIL, TAIL)

    def test_the_production_pause_view_carries_both_sentences(self):
        offered = self._view({"basis_ref": "waitbasis:test-d",
                              "source": "WORK_HEAD_AFTER_NO_CHANGE"})
        self.assertTrue(offered.get("available"))
        how = str(offered.get("how") or "")
        self.assertIn(FACT_SENTENCE, how)
        self.assertIn(TAIL, how)
        # 无 basis 是可命名的"没条件可绑"：事实句在，恢复尾段不在。
        none_offered = self._view(None)
        self.assertFalse(none_offered.get("available"))
        how_not = str(none_offered.get("how") or "")
        self.assertIn(FACT_SENTENCE, how_not)
        self.assertNotIn(TAIL, how_not)

    def _shape(self, how):
        """走一次真实 ``build_prompt``，拿它**同一次**返回的结构化 payload。"""

        from agency_cognition_adapter import ProductionAgencyCognitionAdapter

        pause = {"schema_version": "chiyo.life.pause_condition.v1", "available": True,
                 "condition_ref": "waitbasis:test-d", "source": "WORK_HEAD_AFTER_NO_CHANGE",
                 "activity_ref": "actv_testd", "how": how}
        request = SimpleNamespace(
            candidate_set_id="cset:testd", candidates=[],
            legal_decision_enums=("START", "PAUSE", "NO_ACTION"),
            current_activity_summary=None,
            situation_context={"schema_version": "chiyo.life.decision_situation.v1",
                               "pause_condition": pause})
        adapter = ProductionAgencyCognitionAdapter(client=object())
        _text, shape = adapter.build_prompt(request)
        self.assertIn("pause_condition", (shape.get("situation_context") or {}))
        return shape

    def test_the_final_shape_yields_two_independent_booleans(self):
        offered = self._view({"basis_ref": "waitbasis:test-d",
                              "source": "WORK_HEAD_AFTER_NO_CHANGE"})
        shape = self._shape(str(offered.get("how") or ""))
        self.assertEqual(g1._pause_material_booleans(shape),
                         {"fact_sentence_present": True, "tail_present": True})
        none_offered = self._view(None)
        shape_not = self._shape(str(none_offered.get("how") or ""))
        self.assertEqual(g1._pause_material_booleans(shape_not),
                         {"fact_sentence_present": True, "tail_present": False})
        neither = self._shape("choose WAIT, it keeps the page")
        self.assertEqual(g1._pause_material_booleans(neither),
                         {"fact_sentence_present": False, "tail_present": False})

    def test_a_missing_or_non_string_how_is_unknown_not_false(self):
        shapes = [
            # 真实 build_prompt 的两种"没有 how"：整块缺省 / how 为 None。
            self._shape_with_pause({}),
            self._shape_with_pause({"available": True, "how": None}),
            # 直接构造的坏形状：how 不是字符串、只有 available、整块缺失、没有映射。
            {"situation_context": {"pause_condition": {"available": True, "how": 17}}},
            {"situation_context": {"pause_condition": {"available": True}}},
            {"situation_context": None},
            {},
            None,
            "not a mapping",
        ]
        for shape in shapes:
            self.assertEqual(g1._pause_material_booleans(shape),
                             {"fact_sentence_present": None, "tail_present": None})

    @staticmethod
    def _shape_with_pause(pause):
        from agency_cognition_adapter import ProductionAgencyCognitionAdapter

        request = SimpleNamespace(
            candidate_set_id="cset:testd", candidates=[],
            legal_decision_enums=("PAUSE",), current_activity_summary=None,
            situation_context={"pause_condition": pause})
        return ProductionAgencyCognitionAdapter(client=object()).build_prompt(request)[1]


class TraceRowTest(unittest.TestCase):
    """两个布尔与 send_status 同一行；a/b/c 的 trace 一个字段都不多。"""

    def test_the_d_trace_puts_the_booleans_on_the_same_prompt_row(self):
        root = Path(tempfile.mkdtemp(prefix="g1-d-trace-"))
        how = str(PauseMaterialBooleansTest()._view(
            {"basis_ref": "waitbasis:test-d",
             "source": "WORK_HEAD_AFTER_NO_CHANGE"}).get("how") or "")
        shape = PauseMaterialBooleansTest()._shape(how)
        trace = g1.CandidateTrace(root, phase=g1.PHASE_NATURAL, enabled=True,
                                  pause_material=True)
        trace.record_prompt(shape, send_status=g1.SEND_STATUS_UNKNOWN,
                            evidence="RESPONSE_RETURNED")
        rows = _rows(_trace_path(root))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stage"], g1.STAGE_PROMPT)
        self.assertEqual(row["send_status"], g1.SEND_STATUS_UNKNOWN)
        self.assertIsNone(row["sent"])
        self.assertIs(row["fact_sentence_present"], True)
        self.assertIs(row["tail_present"], True)
        blob = json.dumps(rows, ensure_ascii=False)
        for text in (FACT_SENTENCE, TAIL, how):
            self.assertNotIn(text, blob)

    def test_a_non_d_trace_never_carries_the_two_fields(self):
        root = Path(tempfile.mkdtemp(prefix="g1-abc-trace-"))
        how = str(PauseMaterialBooleansTest()._view(
            {"basis_ref": "waitbasis:test-d",
             "source": "WORK_HEAD_AFTER_NO_CHANGE"}).get("how") or "")
        shape = PauseMaterialBooleansTest()._shape(how)
        trace = g1.CandidateTrace(root, phase="p1", enabled=True)
        trace.record_prompt(shape, send_status=g1.SEND_STATUS_UNKNOWN)
        row = _rows(_trace_path(root).parent / (g1.CANDIDATE_TRACE_FILENAME % "p1"))[0]
        self.assertNotIn("fact_sentence_present", row)
        self.assertNotIn("tail_present", row)


class _NeverConstructed:
    """任何一次构造都直接炸：用来证明 d 的路径根本没有这个东西。"""

    def __init__(self, *_args, **_kwargs):
        raise AssertionError("d must never construct this collaborator")


class DNaturalChainTest(unittest.TestCase):
    """d 的完整离线链：真 owner/runtime，脚本 Decision/内容，HTTP 硬拒。"""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="g1-d-chain-"))
        cls.d = g1.D_PROFILE
        cls.notes: dict[str, object] = {}
        saved_material = g1._provision_operator_material
        saved_injector = g1.SubmissionInjector

        def _boom_material(*_args, **_kwargs):
            raise AssertionError("d must never call _provision_operator_material")

        # -- A. prepare 路径：材料助手被打成 raise --------------------------------
        cls.root_a = cls.scratch / "root-a"
        g1._provision_operator_material = _boom_material
        try:
            with g1._use_batch("d"), g1._http_forbidden():
                cls.notes["prepared"] = g1.prepare(cls.root_a, expected_root=cls.root_a,
                                                   offline=True)
        finally:
            g1._provision_operator_material = saved_material

        # -- B. 进程内跑自然 phase：注入器构造被打成 raise -------------------------

        with g1._use_batch("d"):
            cls.notes["window"] = g1._write_window(cls.root_a)
        cls.notes["natural"] = cls._run_phase_in_process()

        # -- C. 同一根第二次 run：窗口已经写过，必须拒绝 ---------------------------
        with g1._use_batch("d"):
            cls.notes["again"] = g1.start_run(cls.root_a, expected_root=cls.root_a,
                                              execute_approved=True, offline=True)
            # -- D. 走父级真实入口（子进程）的完整链 -------------------------------
            cls.root_b = cls.scratch / "root-b"
            cls.notes["prepared_b"] = g1.prepare(cls.root_b, expected_root=cls.root_b,
                                                 offline=True)
            cls.notes["run_b"] = g1.start_run(cls.root_b, expected_root=cls.root_b,
                                              execute_approved=True, offline=True,
                                              capture_candidates=True)
            cls.notes["audit_b"] = g1.audit(cls.root_b, expected_root=cls.root_b)
            cls.notes["again_b"] = g1.start_run(cls.root_b, expected_root=cls.root_b,
                                                execute_approved=True, offline=True)
        cls.saved_injector = saved_injector

    @classmethod
    def _run_phase_in_process(cls) -> dict:
        saved_injector = g1.SubmissionInjector
        g1.SubmissionInjector = _NeverConstructed  # type: ignore[assignment]
        restore_env = _install_world_isolation(cls.root_a)
        try:
            with g1._use_batch("d"), g1._http_forbidden():
                return g1._phase_natural({
                    "root": str(cls.root_a), "batch": "d",
                    "run_id": cls.d.run_id, "domain_id": cls.d.domain_id,
                    "deadline": cls.d.deadline.isoformat(), "offline": True,
                    "capture_candidates": True, "plan": ["START", "NO_ACTION"]})
        finally:
            restore_env()
            g1.SubmissionInjector = saved_injector  # type: ignore[assignment]

    def test_prepare_never_provisions_material_or_a_control(self):
        prepared = self.notes["prepared"]
        self.assertTrue(prepared.get("ok"), prepared.get("reason_code"))
        self.assertIsNone(prepared.get("material"))
        self.assertIsNone(prepared.get("control"))
        prereg = g1._read_json(g1._experiment_dir(self.root_a) / g1.PREREG_FILENAME)
        self.assertIsNone(prereg.get("operator_material"))
        self.assertIsNone(prereg.get("operator_control"))
        self.assertEqual(prereg.get("batch"), "d")
        self.assertEqual(prereg.get("deadline"), self.d.deadline.isoformat())
        self.assertEqual((prereg.get("caps") or {}).get("expected_history"), 141)
        self.assertEqual((prereg.get("caps") or {}).get("request_cap"), 20)
        self.assertEqual(prereg.get("observation"), {
            "mode": "natural_phase", "natural_phase": True,
            "operator_material_provisioned": False, "control_created": False,
            "injector_constructed": False,
            "booleans": ["fact_sentence_present", "tail_present"]})

    def test_the_natural_phase_never_constructs_the_injector(self):
        report = self.notes["natural"]
        self.assertIs(report.get("natural_phase"), True)
        self.assertIs(report.get("operator_material_provisioned"), False)
        self.assertIs(report.get("control_created"), False)
        self.assertIs(report.get("injector_constructed"), False)
        self.assertNotIn("injections", report)
        self.assertEqual(report.get("batch"), "d")
        self.assertEqual(report.get("run_id"), self.d.run_id)
        self.assertGreaterEqual(int(report.get("round_count") or 0), 1)
        # 不按 G1 判：这里没有 coverage/injections 这类字段。
        self.assertNotIn("coverage", report)
        # 关掉 runtime/owner 后不留活 socket。
        close = report.get("close_supply") or {}
        self.assertFalse(close.get("socket_live_after"))
        # 观测文件只有白名单行，且不含正文/提示词。
        blob = "\n".join(path.read_text(encoding="utf-8") for path in
                         [_trace_path(self.root_a)] if path.exists())
        self.assertNotIn(g1.MATERIAL_TEXT, blob)
        self.assertNotIn(g1.CONTROL_TEXT, blob)
        for text in (FACT_SENTENCE, TAIL):
            self.assertNotIn(text, blob)

    def test_the_standard_world_is_started_and_feeds_ag0(self):
        # 标准 World owner 真的起来、真的读回 READY，并且它的机会真的进了 AG-0；
        # 不是外面塞 world_ready=True，也不是另造一份候选集。
        report = self.notes["natural"]
        world = report.get("world") or {}
        self.assertIs(world.get("started"), True)
        self.assertIs(world.get("ready"), True)
        self.assertEqual(world.get("status"), "READY")
        self.assertEqual(world.get("gate"), "off")
        self.assertTrue(str(world.get("socket") or "").endswith("gateway.sock"))
        self.assertFalse((report.get("close_world") or {}).get("socket_live_after"))
        observation = report.get("observation") or {}
        self.assertEqual(observation.get("status"), "OBSERVED")
        self.assertTrue(any(count > 0 for count in
                            observation.get("world_candidates_per_request") or []),
                        observation.get("world_candidates_per_request"))

    def test_a_second_run_on_the_same_root_is_refused(self):
        again = self.notes["again"]
        self.assertFalse(again.get("ok"))
        self.assertEqual(again.get("reason_code"), g1.REASON_ALREADY_STARTED)

    def test_the_parent_entry_runs_one_natural_phase_and_records_safe_metadata(self):
        run = self.notes["run_b"]
        self.assertTrue(run.get("ok"), run.get("stop_reason"))
        self.assertEqual(run.get("batch"), "d")
        self.assertEqual(run.get("run_id"), self.d.run_id)
        self.assertIsNone(run.get("p1"))
        self.assertIsNone(run.get("p2"))
        self.assertIsNotNone(run.get("natural"))
        self.assertEqual((run.get("natural") or {}).get("phase"), g1.PHASE_NATURAL)
        self.assertEqual(run.get("coverage"), {})
        self.assertIn(run.get("result"), {"OBSERVED", "NOT_COVERED"})
        self.assertNotEqual(run.get("result"), "COVERED")
        observation = run.get("observation") or {}
        self.assertIn(observation.get("status"), {"OBSERVED", "NO_MODEL_REQUEST"})
        # 零请求是合法结果；这时"合法集里有没有 PAUSE"必须是 UNKNOWN，不是 False。
        if observation.get("status") == "NO_MODEL_REQUEST":
            self.assertIsNone(observation.get("pause_choice_opportunity"))
            self.assertIsNone(observation.get("abandon_in_legal_set"))
        else:
            self.assertIsInstance(observation.get("pause_choice_opportunity"), bool)
            self.assertIsInstance(observation.get("abandon_in_legal_set"), bool)
            self.assertTrue(observation.get("prompt_rows"))
        for row in observation.get("prompt_rows") or []:
            self.assertIsInstance(row.get("fact_sentence_present"), bool)
            self.assertIsInstance(row.get("tail_present"), bool)
            self.assertIn(row.get("send_status"),
                          {g1.SEND_STATUS_SENT, g1.SEND_STATUS_NOT_SENT,
                           g1.SEND_STATUS_UNKNOWN})
        self.assertEqual(observation.get("pause_condition_same_work"), "UNKNOWN")
        ledger = run.get("ledger") or {}
        self.assertEqual(ledger.get("run_id"), self.d.run_id)
        self.assertEqual(int(ledger.get("run_window_max") or 0), 20)
        self.assertLessEqual(int(ledger.get("run_window_calls") or 0), 20)
        # 子进程的 config 也带 d 身份与同一个截止。
        config = g1._read_json(g1._experiment_dir(self.root_b)
                               / ("g1-config-%s.json" % g1.PHASE_NATURAL))
        self.assertEqual(config.get("batch"), "d")
        self.assertEqual(config.get("run_id"), self.d.run_id)
        self.assertEqual(config.get("domain_id"), self.d.domain_id)
        self.assertEqual(config.get("deadline"), self.d.deadline.isoformat())
        self.assertTrue(config.get("capture_candidates"))

    def test_the_audit_reads_the_natural_phase_for_d(self):
        audited = self.notes["audit_b"]
        self.assertTrue(audited.get("ok"), audited)
        self.assertEqual(audited.get("batch"), "d")
        self.assertEqual(audited.get("run_id"), self.d.run_id)
        self.assertEqual(audited.get("deadline"), self.d.deadline.isoformat())
        self.assertEqual(list((audited.get("phases") or {})), [g1.PHASE_NATURAL])
        self.assertIn("observation", audited)
        self.assertIsNone(audited.get("operator_material"))
        self.assertIsNone(audited.get("operator_control"))
        self.assertEqual(self.notes["again_b"].get("reason_code"),
                         g1.REASON_ALREADY_STARTED)


def _body_pose(client) -> dict:
    payload = client.get_snapshot().data or {}
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
    return {"pose": body.get("pose"), "orientation": body.get("orientation")}


class _WorldStartDecisionTransport(g1._ScriptedDecisionTransport):
    """测试替身（明确不是自然选择）：只从真实 World 候选里挑第一个合法 START。

    用它把首轮 Decision 确定落在 ``WORLD_OPPORTUNITY`` 上，只验「感知 provider 与动作
    adapter 同时就位后，正式首步／Action 真的构造出来」这一处装配；真实 run 仍由模型自选。
    """

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s=None, max_output_tokens=None):
        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        try:
            payload = json.loads(user_prompt or "{}")
        except ValueError:
            payload = {}
        allowed = [str(code) for code in (payload.get("allowed_reason_codes") or [])]
        candidates = [row for row in (payload.get("candidates") or [])
                      if isinstance(row, Mapping)]
        pool = [row for row in candidates
                if str(row.get("source_kind") or "") == "WORLD_OPPORTUNITY"
                and "START" in [str(code) for code in (row.get("legal_decisions") or [])]]
        pick = pool[0] if pool else None
        answer = {"decision": "NO_ACTION", "selected_candidate_id": None,
                  "reason_codes": allowed[:1] or ["NO_ACTION_CHOSEN"],
                  "deferred_candidate_ids": []}
        if pick is not None:
            answer.update({"decision": "START",
                           "selected_candidate_id": str(pick["candidate_id"])})
        return self._result(json.dumps(answer, ensure_ascii=False), "offline-decision")


class DWorldStepAssemblyTest(unittest.TestCase):
    """首步装配：正式 cycle 真的构造 step／Action，gate off 明确拒绝、零 World 效果。"""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="g1-d-world-step-"))
        cls.root = cls.scratch / "root"
        with g1._use_batch("d"):
            prepared = g1.prepare(cls.root, expected_root=cls.root, offline=True)
            if not prepared.get("ok"):
                raise AssertionError("d prepare failed: %r" % (prepared,))
            # 进程内首步也补上和 phase 子进程同样的 World 写面映射（类结束时原样恢复）。
            cls.addClassCleanup(_install_world_isolation(cls.root))
            g1._write_window(cls.root)
            cls.child = g1._start_supply(cls.root, tag="test-d-world-step")
            cls.addClassCleanup(g1._stop_supply, cls.child)
            cls.world_child = g1._start_world(cls.root, tag="test-d-world-step")
            cls.addClassCleanup(g1._stop_world, cls.world_child)
            env = g1._runtime_env(cls.root, offline=True)
            cls.env = env
            cls.world_socket = str(getattr(cls.world_child, "socket_path", "") or "")
            if str(env.get("WORLD_BODY_SOCKET") or "") != cls.world_socket:
                raise AssertionError("runtime env does not point at the live world child")
            state_root = Path(env["NYA02_RESIDENT_STATE"])
            budget = g1._budget(state_root)
            latch = g1.SendLatch(lambda: g1._deadline_passed(cls.root))
            saved_transport = g1._ScriptedDecisionTransport
            g1._ScriptedDecisionTransport = _WorldStartDecisionTransport
            try:
                with g1._http_forbidden():
                    cls.bundle = g1._build_runtime(
                        cls.root, state_root=state_root, budget=budget, latch=latch,
                        env=env, offline=True, plan=["START"], world_feed=True)
            finally:
                g1._ScriptedDecisionTransport = saved_transport
            cls.addClassCleanup(g1._release_runtime, cls.bundle)
            runtime = cls.bundle["runtime"]
            cls.cycle = cls.bundle["cycle"]
            from world_body_client import WorldBodyClient

            client = WorldBodyClient(environment=env)
            before = _body_pose(client)
            with g1._http_forbidden():
                out = cls.cycle.evaluate_once(trigger_ref="test:d:world-step", act=True,
                                              observed_at=g1._FixtureClock().next()) or {}
            after = _body_pose(client)
            cls.out = out
            cls.body_before = before
            cls.body_after = after
            cls.activity_ref = str(out.get("activity_ref") or "")
            cls.action_id = str(out.get("action_id") or "")
            commit_state = runtime.coordinator.integration_commit.read_state() or {}
            records = list((commit_state.get("integrations_by_id") or {}).values())
            record = next((row for row in records
                           if str(row.get("activity_ref") or "") == cls.activity_ref), None)
            cls.record = dict(record or {})
            cls.spec = runtime.adoption_coordinator.get_adoption_spec(
                str(out.get("selected_candidate_ref") or ""))
            cls.actions = [dict(row) for row in runtime.action_read.list_actions()]
            cls.attempts = ([dict(row) for row in runtime.action_read.list_attempts(cls.action_id)]
                            if cls.action_id else [])
            cls.receipts = ([dict(row) for row in runtime.action_read.list_receipts(cls.action_id)]
                            if cls.action_id else [])

    def test_the_round_really_constructs_the_world_step_and_action(self):
        out = self.out
        self.assertEqual(out.get("decision"), "START", out.get("detail"))
        self.assertEqual(out.get("adoption_status"), "APPLIED")
        self.assertTrue(self.activity_ref, out)
        self.assertTrue(self.action_id, out)
        # 首步／Action 的身份真的写进 AG-2 的 integration，不是 mock 出来的布尔。
        self.assertTrue(self.record, "no integration record for this Activity")
        self.assertTrue(self.record.get("executable_step_refs"), self.record)
        self.assertTrue(self.record.get("action_refs"), self.record)
        self.assertIsNotNone(self.spec)
        import autonomous_life_cycle as alc

        self.assertEqual(self.spec.activity_kind, alc.BODY_ACTION_ACTIVITY_KIND)
        self.assertTrue(list(self.spec.target_refs or []))
        # 既有合同：World/BODY 的首步由**本轮 Decision 绑定的 override**（``step_spec_override``）
        # 构造，adoption spec 的 ``initial_executable_step_spec`` 本来就是 None。首步是否真的
        # 构造，由上面 AG-2 该 Activity 的 step/action refs 证明；本单不改 components 去填它。
        self.assertIsNone(self.spec.initial_executable_step_spec)
        self.assertEqual(len(self.actions), 1)

    def test_gate_off_is_a_definite_pre_submit_refusal_with_no_world_effect(self):
        out = self.out
        self.assertEqual(out.get("outcome"), "STEP_REFUSED", out.get("detail"))
        self.assertEqual(out.get("action_status"), "PREPARED")
        pre = out.get("pre_submit") or {}
        self.assertEqual(pre.get("reason_code"), "GATE_OFF")
        self.assertIs(pre.get("not_sent"), True)
        self.assertEqual(len(self.attempts), 1)
        self.assertEqual(self.attempts[0].get("status"), "PRE_SUBMIT_FAILED")
        self.assertEqual(self.attempts[0].get("error_class"), "GATE_OFF")
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.body_after, self.body_before)


class DGuardTest(unittest.TestCase):
    """cap 用满即停、错身份 phase 拒绝。"""

    def test_the_cap_and_deadline_stop_the_loop(self):
        root = Path(tempfile.mkdtemp(prefix="g1-d-cap-"))
        with g1._use_batch("d"):
            g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
                "schema": "g1.real.prereg.v1", "batch": "d",
                "run_id": g1.D_PROFILE.run_id,
                "domain_id": g1.D_PROFILE.domain_id, "root": str(root),
                "state_root": str(root / "state"),
                "deadline": g1.D_PROFILE.deadline.isoformat()})
            g1._write_window(root)
            state_root = root / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            budget = g1._budget(state_root)
            budget.ensure_window(allow_new=True)
            latch = g1.SendLatch(lambda: g1._deadline_passed(root))
            self.assertEqual(g1._loop_stop(root, budget, latch), "")
            for _index in range(g1.REQUEST_CAP):
                budget.reserve("decision")
            self.assertEqual(g1._loop_stop(root, budget, latch), g1.REASON_REQUEST_CAP)

    def test_a_natural_phase_config_is_refused_for_another_batch(self):
        d = g1.D_PROFILE
        root = Path(tempfile.mkdtemp(prefix="g1-d-phase-"))
        base = {"root": str(root), "batch": "d", "run_id": d.run_id,
                "domain_id": d.domain_id, "deadline": d.deadline.isoformat()}
        cases = {
            # 声明 a 却带着 d 的 run/域：身份不一致。
            "foreign_batch": dict(base, batch="a"),
            "foreign_deadline": dict(base,
                                     deadline=g1.BATCH_PROFILES["a"].deadline.isoformat()),
            "missing_deadline": {key: value for key, value in base.items()
                                 if key != "deadline"},
        }
        for label, payload in cases.items():
            config = root / ("cfg-%s.json" % label)
            g1._write_json(config, payload)
            done = subprocess.run(
                [sys.executable, "-B", str(Path(g1.__file__)), "_phase",
                 "--phase", g1.PHASE_NATURAL, "--config", str(config)],
                capture_output=True, text=True)
            self.assertEqual(done.returncode, g1.EXIT_REFUSED, label)
            self.assertIn(g1.REASON_BATCH_IDENTITY_MISMATCH, done.stderr, label)
        # 自然 phase 只属于 d，p1/p2 只属于 a/b/c：两个方向都在读盘之前拒绝。
        for label, phase, batch in (("natural_under_a", g1.PHASE_NATURAL, "a"),
                                    ("p1_under_d", "p1", "d")):
            profile = g1.MODE_PROFILES[batch]
            config = root / ("cfg-%s.json" % label)
            payload = {"root": str(root), "batch": batch,
                       "run_id": profile.run_id,
                       "domain_id": profile.domain_id,
                       "deadline": profile.deadline.isoformat()}
            if g1._is_natural(profile):
                # d/e 的 gate 是闭集的一部分；带上本批自己的值，才能走到真正的
                # "phase 只属于单自然批 / a/b/c 才有 p1/p2" 检查。
                payload["world_gate"] = str(g1._world_intent_gate(profile) or "")
            g1._write_json(config, payload)
            done = subprocess.run(
                [sys.executable, "-B", str(Path(g1.__file__)), "_phase",
                 "--phase", phase, "--config", str(config)],
                capture_output=True, text=True)
            self.assertEqual(done.returncode, g1.EXIT_REFUSED, label)
            self.assertIn(g1.REASON_UNKNOWN_BATCH, done.stderr, label)


if __name__ == "__main__":
    unittest.main()
