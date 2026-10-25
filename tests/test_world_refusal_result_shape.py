"""正式 ``execute_activity_step`` 返回形状下的"明确没发"。

2026-10-24 的拒绝分类只按扁平的 bridge 结果写了测试：``submit_result`` 直接带
``action`` / ``attempt``。生产链其实多一层——``LifeIntegrationCoordinator.
execute_activity_step`` 返回的 ``submit_result`` 是 bridge 自己的输出，真正的 AR-0
结果在它的 ``submit_result`` 里。于是真实 gate-off 的 ``PRE_SUBMIT_FAILED`` 从没被
读到，回合落回 ``STEP_UNSETTLED`` / wake ``UNKNOWN``，owner 证据也留成 ``None``。

这里同时钉住两头：

1. 两种形状（扁平、真实嵌套）下的精确预提交拒绝都判成 ``STEP_REFUSED``；
   缺 attempt、错层、身份不一致、fenced/submitted、终态 Action 都不升级成拒绝。
2. 真实 production runtime + 真 World gateway/registry：gate off 经正式
   ``evaluate_once`` / runner wake 得到 ``BLOCKED``、Action 仍 ``PREPARED``、
   attempt ``PRE_SUBMIT_FAILED``、零回执/零票/零世界变化、owner 证据为 ``False``；
   gate canonical 的同一路径仍真出发并消费一张票，不被误判成拒绝。

Decision / 内容是脚本替身；观察候选来自真实 ``action_intent.build_action_space``，
只是在本轮开始前取好，好让"候选存在"和"提交时 gate 关闭"能同时成立。
"""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import (  # noqa: E402
    StubCognition, StubObservation, StubWriter, posix_only as _posix,
)
from test_one_day_life_w05r import W05RTestCase  # noqa: E402

import action_reality_ledger as ar0  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_runner as alr  # noqa: E402

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
POSE_TARGET = "bodyact:pose:seated:north"
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
REGISTRY_NAME = "action_authorizations.json"


# ---------------------------------------------------------------------------
# 1. 分类器：嵌套/扁平两种形状，以及所有不该变拒绝的负控
# ---------------------------------------------------------------------------


class SubmitAttemptShapeTest(unittest.TestCase):
    @staticmethod
    def _cycle():
        cycle = object.__new__(alc.AutonomousLifeCycle)
        cycle.runtime = SimpleNamespace(
            action_read=SimpleNamespace(list_receipts=lambda _action_id: []))
        cycle.last_outcome = None
        cycle._finish = lambda base, outcome, **kwargs: dict(
            base, outcome=outcome, detail=kwargs.get("detail", ""))
        return cycle

    @staticmethod
    def _action(*, status="PREPARED", action_id="actn:shape", verb="POSE"):
        return {"status": status, "action_id": action_id,
                "provider_operation_ref": None,
                "frozen_payload": {"verb": verb, "target": POSE_TARGET}}

    @staticmethod
    def _attempt(*, action_id="actn:shape", status="PRE_SUBMIT_FAILED",
                 transport=ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                 error_class="GATE_OFF"):
        return {"attempt_id": "aatt:shape", "action_id": action_id,
                "status": status, "transport_result": transport,
                "error_class": error_class,
                "provider_ref": "world-exec:never-sent"}

    @classmethod
    def _nested(cls, action, attempt=None, *, nested_action=None):
        """The production shape: bridge output under its own ``submit_result``."""

        bridge = {"action": nested_action if nested_action is not None else action,
                  "receipt": None}
        if attempt is not None:
            bridge["attempt"] = attempt
        return {"step_execution_result": {
            "submit_result": {
                "submit_result": bridge,
                "action": action,
                "activity": None,
                "waiting_applied": False,
            },
            "settlement_result": None,
        }}

    @classmethod
    def _flat(cls, action, attempt=None):
        submit = {"action": action}
        if attempt is not None:
            submit["attempt"] = attempt
        return {"step_execution_result": {"submit_result": submit}}

    def _classify(self, out):
        return self._cycle()._classify_world_step(out, {}, "now")

    def test_the_real_nested_shape_is_a_refusal(self):
        out = self._classify(self._nested(self._action(), self._attempt()))
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(out["pre_submit"]["reason_code"], "GATE_OFF", out)
        self.assertTrue(out["pre_submit"]["not_sent"], out)
        self.assertEqual(out["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT, out)

    def test_the_flat_bridge_shape_still_refuses(self):
        out = self._classify(self._flat(self._action(), self._attempt()))
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertTrue(out["pre_submit"]["not_sent"], out)

    def test_a_nested_attempt_from_another_action_is_never_stitched(self):
        # 外层 action 是 A，嵌套 action/attempt 都是 B：不跨层拼身份。
        out = self._classify(self._nested(
            self._action(action_id="actn:a"),
            self._attempt(action_id="actn:b"),
            nested_action=self._action(action_id="actn:b")))
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)

        # 嵌套层 action 与 attempt 属于同一 action，但仍不是外层这次提交的 action。
        out = self._classify(self._nested(
            self._action(action_id="actn:a"),
            self._attempt(action_id="actn:a"),
            nested_action=self._action(action_id="actn:b")))
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)

    def test_missing_or_malformed_attempt_stays_unsettled(self):
        action = self._action()
        cases = {
            "no attempt": self._nested(action),
            "attempt not a mapping": self._nested(action, "not-a-mapping"),
            "empty attempt": self._nested(action, {}),
            "submit_result not a mapping": {
                "step_execution_result": {"submit_result": {"submit_result": ["bad"],
                                                            "action": action}}},
            "nested status fenced": self._nested(
                action, self._attempt(status="FENCED")),
            "nested status submitted": self._nested(
                action, self._attempt(status="SUBMITTED")),
            "missing transport": self._nested(
                action, {key: value for key, value in self._attempt().items()
                         if key != "transport_result"}),
            "ambiguous transport": self._nested(
                action, self._attempt(
                    transport=ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT)),
            "definite rejection": self._nested(
                action, self._attempt(transport=ar0.TRANSPORT_ERR_DEFINITE_REJECT)),
        }
        for name, out in cases.items():
            with self.subTest(name):
                classified = self._classify(out)
                self.assertEqual(classified["outcome"],
                                 alc.OUTCOME_STEP_UNSETTLED, classified)

    def test_a_terminal_action_is_never_relabelled_as_pre_submit(self):
        for status, expected in (("UNKNOWN", alc.OUTCOME_STEP_UNKNOWN),
                                 ("FAILED", alc.OUTCOME_STEP_REFUSED)):
            out = self._classify(self._nested(
                self._action(status=status), self._attempt()))
            self.assertEqual(out["outcome"], expected, out)
        # FAILED 是 owner 自己的拒绝，不带"没发"的预提交证据。
        self.assertNotIn("pre_submit", out)

    def test_a_refusal_without_an_error_class_still_reports_not_sent(self):
        out = self._classify(self._nested(
            self._action(), self._attempt(error_class=None)))
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(out["pre_submit"]["reason_code"], "PRE_SUBMIT_FAILED", out)


class OwnerEvidenceShapeTest(unittest.TestCase):
    def test_a_classified_refusal_is_false_not_unknown(self):
        evidence = alc.AutonomousLifeCycle._owner_evidence(
            object.__new__(alc.AutonomousLifeCycle),
            {"pre_submit": {"not_sent": True, "reason_code": "GATE_OFF"}},
            alc.OUTCOME_STEP_REFUSED)
        self.assertIs(evidence["owner_version_created"], False, evidence)
        self.assertIsNone(evidence["owner_version_id"], evidence)

    def test_an_unsettled_or_unknown_round_stays_none(self):
        for outcome in (alc.OUTCOME_STEP_UNSETTLED, alc.OUTCOME_STEP_UNKNOWN):
            evidence = alc.AutonomousLifeCycle._owner_evidence(
                object.__new__(alc.AutonomousLifeCycle), {}, outcome)
            self.assertIsNone(evidence["owner_version_created"], (outcome, evidence))

    def test_the_shortcut_only_applies_to_the_classified_refusal(self):
        evidence = alc.AutonomousLifeCycle._owner_evidence(
            object.__new__(alc.AutonomousLifeCycle),
            {"pre_submit": {"not_sent": True}}, alc.OUTCOME_STEP_UNSETTLED)
        self.assertIsNone(evidence["owner_version_created"], evidence)


# ---------------------------------------------------------------------------
# 2. 真实 production runtime / AG2 正式链
# ---------------------------------------------------------------------------


@_posix
class FormalWorldRefusalChainTest(W05RTestCase):
    def _fixture(self):
        data = self._prepared_instance()
        env = self.instance_env()
        cognition, writer = StubCognition("move"), StubWriter()
        runtime, _port = self._runtime(cognition, env, data)
        observation = StubObservation(env)
        # 候选集必须来自真实 action space；gate 关闭时它本来就不提供 MOVE，
        # 所以先在 gate 打开时取好这一轮的真实候选，再在提交时关闸。
        with mock.patch.dict(os.environ, {GATE_ENV: "canonical"}):
            opportunities = observation()
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=None,
            world_opportunity_provider=lambda: copy.deepcopy(opportunities),
            writer=writer, enabled=True, env=env)
        runner = alr.AutonomousLifeRunner(cycle, env=env, enabled=True)
        return SimpleNamespace(runtime=runtime, cycle=cycle, runner=runner,
                               cognition=cognition, env=env, data=data)

    def _travels(self) -> list:
        path = self._travel_path()
        if not path.exists():
            return []
        import json

        return list(json.loads(path.read_text(encoding="utf-8")).get("travels") or [])

    def _authorizations(self, env) -> list:
        path = Path(env["WORLD_BODY_SOCKET"]).parent / REGISTRY_NAME
        if not path.exists():
            return []
        import json

        return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])

    def test_gate_off_through_the_formal_chain_is_a_refusal_with_no_resend(self):
        fixture = self._fixture()
        world_before = self._world_state_bytes()
        with mock.patch.dict(os.environ, {GATE_ENV: "off"}):
            record = fixture.runner.wake_on_event(
                {"kind": alr.EVENT_WORLD_OPPORTUNITY, "ref": "evt:shape:off"})

        self.assertEqual(record.status, alr.WAKE_BLOCKED, record.to_dict())
        self.assertEqual(record.status_class, "blocked", record.to_dict())
        self.assertEqual(record.outcome, alc.OUTCOME_STEP_REFUSED, record.to_dict())
        self.assertEqual(record.action_status, "PREPARED", record.to_dict())
        self.assertIs(record.extra.get("owner_version_created"), False, record.extra)
        self.assertIsNone(record.extra.get("owner_version_id"), record.extra)

        action_id = str(record.action_id or "")
        self.assertTrue(action_id, record.to_dict())
        action = fixture.runtime.action_read.get_action(action_id)
        attempts = fixture.runtime.action_read.list_attempts(action_id)
        self.assertEqual(action["status"], "PREPARED", action)
        self.assertEqual(len(attempts), 1, attempts)
        self.assertEqual(attempts[0]["status"], "PRE_SUBMIT_FAILED", attempts[0])
        self.assertEqual(attempts[0]["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT, attempts[0])
        self.assertEqual(attempts[0]["error_class"], "GATE_OFF", attempts[0])
        # 零回执、零票、零 travel、零世界变化；PREPARED 不入 UNKNOWN 对账队列。
        self.assertEqual(fixture.runtime.action_read.list_receipts(action_id), [])
        self.assertEqual(self._authorizations(fixture.env), [])
        self.assertEqual(self._travels(), [])
        self.assertEqual(self._world_state_bytes(), world_before)
        self.assertEqual(fixture.runner.pending_unknown_actions(), [])

    def test_a_canonical_round_still_departs_and_is_not_a_refusal(self):
        fixture = self._fixture()
        with mock.patch.dict(os.environ, {GATE_ENV: "canonical"}):
            out = fixture.cycle.evaluate_once(trigger_ref="evt:shape:on", act=True)

        self.assertEqual(out.get("outcome"), alc.OUTCOME_IN_TRANSIT, out)
        self.assertNotEqual(out.get("outcome"), alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(str(out.get("action_status") or ""), "SUCCEEDED", out)
        self.assertTrue((out.get("journey") or {}).get("journey_id"), out)
        self.assertIsNot((out.get("journey") or {}).get("arrived"), True, out)
        self.assertEqual(len(self._travels()), 1, out)
        records = self._authorizations(fixture.env)
        self.assertEqual(len(records), 1, records)
        self.assertEqual(str(records[0].get("state")).upper(), "CONSUMED", records)
        # 真实出发没有作品版本：证据保持原有的 None，不伪造成 False。
        self.assertIsNone((out.get("owner_evidence") or {}).get("owner_version_created"),
                          out.get("owner_evidence"))


# unittest 会收集继承来的 test_* 方法；父类用例由各自模块负责跑。
for _inherited in [name for name in dir(W05RTestCase) if name.startswith("test_")]:
    setattr(FormalWorldRefusalChainTest, _inherited, None)
del W05RTestCase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
