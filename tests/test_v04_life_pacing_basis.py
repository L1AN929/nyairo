#!/usr/bin/env python3
"""v0.4 A（有松有紧的生活）：身体条件断点的**来源、证据与不变量**（纯 + 决定处境投影）。

这一份只钉三件事，不冒充整链：

1. ``life_wait_basis`` 的身体那一半：什么才算"我可以为身体放下这件事"的可核对依赖 ——
   必须是 owner 自己报的 ``LIMITED`` + 具名负荷信号 + 身体身份/版本/观测时刻；UNKNOWN、
   没信号、读不到、没有身份/版本，一律**没有**基础（"没信号不是健康"，也不是等待理由）。
2. 满足条件必须来自**同一个身体更新的一次读**：身份不符、不是 FRESH、身体版本没变、
   观测时刻不比基线新、证据没有真的动，一律 ``None``；真的动了才产出 LR-4 事件，而且事件
   只是"可以问"，不是"恢复"。
3. 这份断点进的是**真实 prompt**：``situation_context.breakpoint`` 带"留下什么 / 等什么 /
   怎么回来"，超长裁剪时它和现场、身体一起留到最后；系统提示写清楚"会话不是恢复、
   回来必须是她自己的 RESUME"。

整链（阅读→断点→坐下→REST start→owner tick→REST stop→RESUME 下一页）在
``tests/test_v04_life_pacing_chain.py``；这里全是纯函数/投影与 prompt，不跑真 socket。

夹具：纯函数段无夹具；cycle/prompt 段的目录建在 kept 根下（只建不删）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "native" / "integration")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_wait_basis as lwb  # noqa: E402
import waiting_ref_vocab  # noqa: E402

#: All fixtures live under one retained root (created once, never removed).
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-pacing-basis-"))

MOMENT = "2026-11-14T09:00:00+00:00"
LATER = "2026-11-14T09:40:00+00:00"
BODY = "chiyo_body"
REV_3 = "bodyrev:3"
REV_7 = "bodyrev:7"

_LIFE_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
    import agency_cognition_adapter as aa  # noqa: E402
except Exception as error:  # noqa: BLE001 - environment limit, not product behaviour
    _LIFE_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX（fcntl）：%s" % (_LIFE_ERROR or ""))


def _burden(*pairs):
    return [{"signal": signal, "band": band} for signal, band in pairs]


def _basis(**overrides):
    kwargs = dict(body_id=BODY, revision=REV_3, capacity="LIMITED",
                  burden=_burden(("fatigue_noticeable", "strong")),
                  recovery_progress="mild", trend="stalled", session="CLOSED",
                  observed_at=MOMENT, activity_ref="actv:reading:1")
    kwargs.update(overrides)
    return lwb.body_capacity_basis(**kwargs)


# ---------------------------------------------------------------------------------------
# 1. 什么才算依赖（纯）
# ---------------------------------------------------------------------------------------
class PacingBodyBasisTest(unittest.TestCase):
    def test_a_limited_body_with_a_named_burden_is_a_checkable_dependency(self):
        basis = _basis()
        self.assertIsNotNone(basis)
        self.assertEqual(basis["source"], lwb.SOURCE_BODY_REST)
        self.assertEqual(basis["baseline_capacity"], "LIMITED")
        self.assertEqual(basis["baseline_body_ref"], BODY)
        self.assertEqual(basis["baseline_revision"], REV_3)
        self.assertEqual(basis["baseline_burden"],
                         [{"signal": "fatigue_noticeable", "band": "strong"}])
        # 依赖拼在等待域自己的词表里（LR-2/LR-4 都认），不是自造命名空间。
        self.assertEqual(waiting_ref_vocab.waiting_ref_kind(basis["waiting_on_ref"]),
                         waiting_ref_vocab.WaitingRefKind.WAIT_DEP)
        self.assertIn(basis["condition_kind"], waiting_ref_vocab.CONDITION_KIND_WAITING_REF_KINDS)
        self.assertEqual(basis["basis_ref"].split(":")[0], lwb.BODY_BASIS_REF_PREFIX)

    def test_the_dependency_is_bound_to_the_evidence_not_to_the_body_alone(self):
        light = _basis(burden=_burden(("fatigue_noticeable", "mild")))
        strong = _basis(burden=_burden(("fatigue_noticeable", "strong")))
        later = _basis(revision=REV_7)
        self.assertNotEqual(light["waiting_on_ref"], strong["waiting_on_ref"])
        self.assertNotEqual(strong["waiting_on_ref"], later["waiting_on_ref"])
        # 换了基线就是另一个依赖：旧 ref 不可能满足新条件。
        self.assertNotEqual(light["basis_ref"], strong["basis_ref"])

    def test_an_unknown_quiet_or_unread_body_is_never_a_dependency(self):
        for name, overrides in (
                ("UNKNOWN capacity", dict(capacity="UNKNOWN")),
                ("AVAILABLE capacity", dict(capacity="AVAILABLE")),
                ("no burden signal", dict(burden=[])),
                ("no body identity", dict(body_id="")),
                ("no revision", dict(revision="")),
                ("no observation instant", dict(observed_at=""))):
            with self.subTest(name):
                self.assertIsNone(_basis(**overrides), name)

    def test_the_waiting_spec_releases_the_slot_and_invents_no_deadline(self):
        spec = lwb.body_waiting_spec(_basis())
        self.assertIs(spec["release_foreground"], True)
        self.assertEqual(spec["condition_kind"], lwb.CONDITION_KIND)
        self.assertEqual(spec["parameters"]["waiting_on_ref"], _basis()["waiting_on_ref"])
        self.assertNotIn("condition_expires_at", spec)
        self.assertNotIn("schedule_agenda", spec)
        # 基线证据随参数一起走，maintain 不必记住任何东西。
        self.assertEqual(spec["parameters"][lwb.PARAM_BASELINE_REVISION], REV_3)
        self.assertEqual(spec["parameters"][lwb.PARAM_BASELINE_BURDEN],
                         ["fatigue_noticeable:strong"])

    def test_the_spec_is_a_condition_this_module_owns_and_can_rebuild(self):
        spec = lwb.body_waiting_spec(_basis())
        condition = {"condition_id": "rcond:x", "activity_id": "actv:reading:1",
                     "status": "PENDING", "condition_kind": spec["condition_kind"],
                     "waiting_on_ref": spec["waiting_on_ref"],
                     "parameters": spec["parameters"]}
        self.assertTrue(lwb.is_body_capacity_condition(condition))
        rebuilt = lwb.body_basis_from_condition(condition)
        self.assertEqual(rebuilt["baseline_revision"], REV_3)
        self.assertEqual(rebuilt["baseline_burden"],
                         [{"signal": "fatigue_noticeable", "band": "strong"}])
        # 别人的条件不是本模块的（不会被 maintain 误读）。
        self.assertFalse(lwb.is_body_capacity_condition(
            {"parameters": {"basis_source": lwb.SOURCE_WORK_HEAD}}))
        self.assertIsNone(lwb.body_basis_from_condition({"parameters": {}}))


# ---------------------------------------------------------------------------------------
# 2. 什么才能满足（纯）
# ---------------------------------------------------------------------------------------
class PacingBodyEasingTest(unittest.TestCase):
    def _event(self, **overrides):
        kwargs = dict(observed_body_id=BODY, observed_revision=REV_7, burden=[], capacity="UNKNOWN",
                      recovery_progress="noticeable", trend="improving", freshness="FRESH",
                      observed_at=LATER)
        kwargs.update(overrides)
        return lwb.body_easing_event(_basis(), **kwargs)

    def test_real_easing_produces_one_bounded_lr4_event(self):
        event = self._event()
        self.assertIsNotNone(event)
        self.assertEqual(event["event_category"], lwb.EVENT_CATEGORY)
        self.assertEqual(event["event_kind"], lwb.EVENT_KIND_BODY_EASED)
        self.assertEqual(event["waiting_on_ref"], _basis()["waiting_on_ref"])
        self.assertTrue(event["is_observed_by_chiyo"])
        self.assertIn("PROGRESS_BAND_ADVANCED", event["attributes"]["eased_by"])
        self.assertIn("BURDEN_FATIGUE_NOTICEABLE_NOT_REPORTED", event["attributes"]["eased_by"])
        self.assertEqual(event["attributes"]["observed_body_revision"], REV_7)
        # 观测时刻来自这次读本身，不是记下来的时钟。
        self.assertEqual(event["observed_at"], LATER)

    def test_an_unchanged_body_releases_nothing(self):
        # 版本没变 —— 包括"同一份观测被评估两次"。
        self.assertIsNone(self._event(observed_revision=REV_3))
        # 版本变了但证据一动没动（同样负荷、同样恢复档位、不是 improving 趋势）。
        self.assertIsNone(self._event(
            burden=_burden(("fatigue_noticeable", "strong")), recovery_progress="mild",
            trend="stalled"))
        # 观测时刻不比基线新（过期/乱序的读）。
        self.assertIsNone(self._event(observed_at=MOMENT))

    def test_wrong_identity_a_stale_read_and_an_unread_body_release_nothing(self):
        self.assertIsNone(self._event(observed_body_id="someone_else_body"))
        self.assertIsNone(self._event(observed_body_id=""))
        self.assertIsNone(self._event(freshness="STALE"))
        self.assertIsNone(self._event(observed_revision=""))

    def test_one_named_burden_easing_is_enough_and_capacity_is_not_the_measure(self):
        event = self._event(burden=_burden(("fatigue_noticeable", "mild")),
                            recovery_progress="mild", trend="stalled", capacity="LIMITED")
        self.assertIsNotNone(event)
        self.assertEqual(event["attributes"]["eased_by"], ["BURDEN_FATIGUE_NOTICEABLE_LIGHTER"])
        # 容量从 LIMITED 变成 AVAILABLE 但证据没动，不算（实测容量会在一次会话里来回跳）。
        self.assertIsNone(self._event(burden=_burden(("fatigue_noticeable", "strong")),
                                      recovery_progress="mild", trend="stalled",
                                      capacity="AVAILABLE"))

    def test_the_event_is_not_a_recovery_claim(self):
        event = self._event()
        text = json.dumps(event, ensure_ascii=False).lower()
        self.assertNotIn("recovered", text)
        self.assertNotIn("rested", text)
        self.assertNotIn("recovery_claim", text)


# ---------------------------------------------------------------------------------------
# 3. 决定处境里的断点（cycle 投影 + 真实 prompt）
# ---------------------------------------------------------------------------------------
class _SituationSource:
    def __init__(self, view):
        self.view = dict(view)
        self.calls = 0

    def build(self, *, now, budget, observations):
        self.calls += 1
        return dict(self.view)


class _Runtime:
    def __init__(self, root, *, status="ACTIVE"):
        self.state_root = Path(root)
        self.world_opportunity_source = SimpleNamespace(
            record_observed_opportunity=lambda item: None,
            expire_opportunity=lambda ref, observed_at=None: None)
        self.candidate_read = None
        self.activity_read = SimpleNamespace(
            get_current_life_view=lambda: {"foreground_activity_ref": "actv:reading:1",
                                           "foreground_activity_kind": "READ_PERSONAL_MATERIAL"},
            get_activity=lambda ref: {"activity_id": "actv:reading:1", "status": status,
                                      "activity_kind": "READ_PERSONAL_MATERIAL"},
            list_open_activities=lambda: [])
        self.ag2_progress_port = None
        self.world_action_adapter = None
        self.artifact_action_adapter = None
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})


def _body_column(*, evidence=("fatigue_noticeable:strong",), capacity="LIMITED"):
    return {
        "state": capacity,
        "ref": "bodycap:%s:%s" % (BODY, REV_3),
        "source": "world_body_read_port.get_body_signals",
        "reason_code": ("OK_LIMITED_BY_BODY_SIGNAL" if capacity == "LIMITED"
                        else "NO_BURDEN_EVIDENCE"),
        "derived_by": "body_capacity_source.read_body_capacity",
        "owner_asserted": False,
        "freshness": "FRESH",
        "observed_at": MOMENT,
        "observed_body_id": BODY,
        "observed_revision": REV_3,
        "evidence": list(evidence),
        "identity_bound": True,
        "revision_bound": True,
        "observation_time_bound": True,
    }


def _situation_view(*, evidence=("fatigue_noticeable:strong",), capacity="LIMITED"):
    return {
        "schema_version": "chiyo.life.situation_view.v1",
        "kind": "life_situation_view",
        "observed_at": MOMENT,
        "identity_bound": True,
        "body": _body_column(evidence=evidence, capacity=capacity),
        "resource_budget": {"state": "AVAILABLE", "ref": "budget:ledger:2026-11-14",
                            "source": "life_request_budget.check", "allowed": True},
        "dimensions": {"body": capacity, "resource_budget": "AVAILABLE"},
        "limiting_dimensions": ["body"] if capacity == "LIMITED" else [],
        "unknown_dimensions": [],
    }


class _Client:
    provider = "scripted"
    model = "scripted-decision"


def _request(situation_context, *, candidates=None):
    return SimpleNamespace(
        opportunity_id="opp:pacing-1", snapshot_fingerprint="fp:pacing-1",
        policy_version="1", legal_decision_enums=["WAIT", "NO_ACTION"],
        allowed_reason_codes=["OK"], current_activity_summary=None, capacity_summary=None,
        observed_fact_keys=["current_step_unavailable_true"],
        prior_experiences=[],
        situation_context=situation_context,
        candidates=candidates or [{"candidate_id": "cand:continue", "candidate_kind": "CONTINUE_CURRENT",
                                   "source_kind": "CURRENT_ACTIVITY",
                                   "source_ref": "actv:reading:1",
                                   "target_refs": ["actv:reading:1"],
                                   "legal_decisions": ["CONTINUE", "WAIT", "NO_ACTION"]}],
        max_output_tokens=512,
    )


@needs_life
class PacingBreakpointCycleTest(unittest.TestCase):
    def _cycle(self, *, view=None, status="ACTIVE", name="breakpoint"):
        root = Path(tempfile.mkdtemp(prefix="%s-" % name, dir=str(FIXTURE_ROOT)))
        source = _SituationSource(view if view is not None else _situation_view())
        cycle = alc.AutonomousLifeCycle(
            _Runtime(root, status=status), enabled=True, env={},
            world_opportunity_provider=SimpleNamespace(last_available_actions=None),
            situation_source=source)
        cycle.read_situation(MOMENT)
        cycle._last_foreground_record = {"activity_ref": "actv:reading:1", "status": status,
                                         "activity_kind": "READ_PERSONAL_MATERIAL"}
        return cycle

    def test_a_running_activity_on_a_limited_body_is_offered_exactly_one_breakpoint(self):
        cycle = self._cycle()
        basis = cycle.body_wait_basis(observed_at=MOMENT)
        self.assertIsNotNone(basis)
        self.assertIsNone(cycle.last_body_wait_reason)
        view = cycle.body_breakpoint_view()
        self.assertTrue(view["available"])
        self.assertEqual(view["kind"], lwb.SOURCE_BODY_REST)
        self.assertEqual(view["activity_ref"], "actv:reading:1")
        self.assertEqual(view["keeps"]["activity_ref"], "actv:reading:1")
        self.assertEqual(view["condition"]["waiting_on_ref"], basis["waiting_on_ref"])
        self.assertEqual(view["condition"]["baseline"]["capacity"], "LIMITED")
        self.assertEqual(view["condition"]["baseline"]["burden"],
                         [{"signal": "fatigue_noticeable", "band": "strong"}])
        self.assertTrue(view["condition"]["releases_foreground"])
        self.assertEqual(view["not_recovery"], "an eased body is not a claim that she recovered")
        # 断点不是候选，也不是许可。
        self.assertNotIn("candidate_id", view)
        self.assertNotIn("grant", view)

    def test_each_way_of_not_having_a_breakpoint_is_named(self):
        unknown = self._cycle(view=_situation_view(evidence=(), capacity="UNKNOWN"), name="unknown")
        self.assertIsNone(unknown.body_wait_basis(observed_at=MOMENT))
        self.assertEqual(unknown.last_body_wait_reason,
                         alc.AutonomousLifeCycle.BODY_BREAKPOINT_NOT_LIMITED)
        self.assertEqual(unknown.body_breakpoint_view()["why_not"],
                         alc.AutonomousLifeCycle.BODY_BREAKPOINT_NOT_LIMITED)

        quiet = self._cycle(view=_situation_view(evidence=(), capacity="LIMITED"), name="quiet")
        self.assertIsNone(quiet.body_wait_basis(observed_at=MOMENT))
        self.assertEqual(quiet.last_body_wait_reason,
                         alc.AutonomousLifeCycle.BODY_BREAKPOINT_NO_BURDEN)

        waiting = self._cycle(status="WAITING", name="waiting")
        self.assertIsNone(waiting.body_wait_basis(observed_at=MOMENT))
        self.assertEqual(waiting.last_body_wait_reason,
                         alc.AutonomousLifeCycle.BODY_BREAKPOINT_NO_ACTIVITY)

    def test_the_breakpoint_reaches_the_real_prompt_and_survives_the_last_trim(self):
        cycle = self._cycle(name="prompt")
        # 一轮里断点是先被算出来的（``evaluate_once`` 的顺序），随后处境才描述它。
        self.assertIsNotNone(cycle.body_wait_basis(observed_at=MOMENT))
        view = cycle.body_breakpoint_view()
        view = cycle.body_breakpoint_view()
        situation = {"schema_version": "chiyo.life.decision_situation.v1",
                     "world": {"status": "observed",
                               "location": {"name": "家", "place_id": "home"},
                               "body": {"pose": "standing", "orientation": "north"},
                               "nearby": [{"place_id": "home", "walk_seconds": 0}],
                               "opportunities": [{"verb": "REST", "label": "歇一会儿"}],
                               "unknowns": []},
                     "body": dict(cycle.last_body_context),
                     "breakpoint": view,
                     "now": MOMENT}
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted")
        text, payload = adapter.build_prompt(_request(situation))
        json.loads(text)
        block = payload["situation_context"]["breakpoint"]
        self.assertTrue(block["available"])
        self.assertEqual(block["condition"]["waiting_on_ref"], view["condition"]["waiting_on_ref"])
        self.assertEqual(block["condition"]["baseline"]["burden"],
                         [{"signal": "fatigue_noticeable", "band": "strong"}])
        self.assertEqual(block["keeps"]["activity_ref"], "actv:reading:1")
        self.assertIn("RESUME", block["how"])
        self.assertIn("not a claim that she recovered", block["not_recovery"])

        # 一个明显放不下的上限：逼裁剪走到最后一步（只留现场、身体与断点）。
        situation["practice"] = {"methods": [{"method_ref": "pm:%d" % i, "title": "x" * 400,
                                              "instruction": "y" * 400} for i in range(3)]}
        situation["later_items"] = [{"item_ref": "fitem:%d" % i, "title": "l" * 300}
                                    for i in range(6)]
        situation["project_options"] = [{"option_ref": "opt:%d" % i, "title": "t" * 300,
                                         "seed_excerpt": "s" * 300} for i in range(4)]
        tight = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted",
                                                   max_prompt_chars=1200)
        text, payload = tight.build_prompt(_request(situation))
        json.loads(text)
        kept = payload["situation_context"]
        self.assertIn("breakpoint", kept)
        self.assertTrue(kept["breakpoint"]["available"])
        self.assertIn("body", kept)
        steps = {row["step"] for row in tight.last_prompt_trim["dropped"]}
        self.assertIn("situation_except_world_and_body", steps)

    def test_the_system_prompt_states_what_a_breakpoint_is_and_is_not(self):
        prompt = aa.SYSTEM_PROMPT
        self.assertIn("situation_context.breakpoint", prompt)
        self.assertIn("frees the slot", prompt)
        self.assertIn("RESUME she has to choose herself", prompt)
        self.assertIn("claim that she recovered", prompt)
        self.assertIn("available: false", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
