#!/usr/bin/env python3
"""BODY 动作身份：``bodyact:`` step 属于已提交的 Decision，而不只是观测坐标。

覆盖（精确，不 discover 继承旧套）：

1. 同一 Decision、同一 target：观测 place/revision/姿态变了，key 仍是同一个执行；
2. 不同 Decision、同 place/revision/target：key 不同，不会共用一个 step/Action；
3. 没有 Decision 身份的手工调用：保留旧的确定性 key（兼容路径，不是正式安全保证）；
4. 编码、payload 形状、效果谓词、拒绝边界都不因身份参数改变；
5. 正式接线：``AutonomousLifeCycle._world_step_spec`` 真的把 committed
   ``decision_id`` 交给 builder（不是只多了一个没人用的 helper 参数）。

Run（POSIX / WSL）:  python3 tests/test_body_action_identity.py
原生 Windows 导入 Life 模块会因 ``fcntl`` 失败，整类跳过（与既有 BODY 用例同一限制）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_body_action_targets as body_actions  # noqa: E402

#: Life 模块经 ``candidate_sources_ag0`` -> ``activity_continuity`` -> ``fcntl``；
#: 原生 Windows 不可导入。导入失败只跳过，不静默改行为。
_IMPORT_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
except Exception as error:  # noqa: BLE001 - 环境限制，不是产品行为
    _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(
    _IMPORT_ERROR is not None,
    "BODY 身份用例需要 POSIX（fcntl）：%s" % _IMPORT_ERROR)

POSE = "bodyact:pose:seated:north"
REST_START = "bodyact:rest:start:rest"
PLACE_HOME = "place:home"
DECISION_A = "dec:aaaaaaaaaaaaaaaa"
DECISION_B = "dec:bbbbbbbbbbbbbbbb"


class _CandidateRead:
    def __init__(self, candidate: dict | None) -> None:
        self.candidate = candidate
        self.reads: list[str] = []

    def get_candidate(self, candidate_id: str) -> dict | None:
        self.reads.append(str(candidate_id))
        return dict(self.candidate) if self.candidate else None


def _cycle(candidate: dict | None) -> alc.AutonomousLifeCycle:
    """The real cycle method, with only its read port and last observation stubbed."""

    cycle = object.__new__(alc.AutonomousLifeCycle)
    cycle.runtime = SimpleNamespace(candidate_read=_CandidateRead(candidate))
    cycle.last_world_observation = {
        "location": {"place_id": PLACE_HOME},
        "world_revision": 7,
        "pose": "standing",
        "orientation": "south",
    }
    cycle.last_world_target_ref = ""
    cycle.last_world_error = None
    return cycle


def _decision(candidate_ref: str, decision_id: str | None) -> SimpleNamespace:
    record = SimpleNamespace(selected_candidate_ref=candidate_ref)
    if decision_id is not None:
        record.decision_id = decision_id
    return record


def _candidate(*targets: str) -> dict:
    return {"candidate_id": "cand:body-identity", "target_refs": list(targets)}


@needs_life
class BodyActionDecisionIdentityTests(unittest.TestCase):
    """step 的唯一键绑定已提交 Decision，而不是观测坐标。"""

    def test_same_decision_keeps_one_execution_across_observation_drift(self) -> None:
        first = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        # 同一 Decision 的重试/丢答对账：owner 已推进 revision、她的姿态/朝向也已不同，
        # 仍必须是同一个执行。
        retried = body_actions.build_body_action_step(
            POSE, place="place:street_1", revision=8, decision_ref=DECISION_A)
        self.assertEqual(first["idempotency_key"], retried["idempotency_key"])
        self.assertIn("dec%3Aaaaaaaaaaaaaaaaa", first["idempotency_key"])
        self.assertNotIn(":home:", first["idempotency_key"])
        self.assertNotIn(":r7:", first["idempotency_key"])

    def test_a_different_decision_is_a_different_execution(self) -> None:
        a = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        b = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_B)
        self.assertNotEqual(a["idempotency_key"], b["idempotency_key"])

    def test_without_a_decision_the_old_deterministic_key_is_kept(self) -> None:
        one = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7)
        two = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7)
        self.assertEqual(one["idempotency_key"], two["idempotency_key"])
        self.assertEqual(
            one["idempotency_key"],
            "idem_step:v02:world:bodyact:place:home:r7:bodyact:pose:seated:north")
        self.assertNotIn("decision", one["idempotency_key"])
        # 兼容 fallback 与正式 Decision 身份不会撞在一起。
        with_decision = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        self.assertNotEqual(one["idempotency_key"], with_decision["idempotency_key"])
        # 观测不同仍然是不同的手工 key：旧语义没有被身份参数改掉。
        other_revision = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=8)
        self.assertNotEqual(one["idempotency_key"], other_revision["idempotency_key"])

    def test_distinct_body_targets_never_collide_under_the_same_decision(self) -> None:
        pose = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        rest = body_actions.build_body_action_step(
            REST_START, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        self.assertNotEqual(pose["idempotency_key"], rest["idempotency_key"])
        self.assertIn(POSE, pose["idempotency_key"])
        self.assertIn(REST_START, rest["idempotency_key"])

    def test_payload_shape_predicates_and_refusals_are_unchanged(self) -> None:
        step = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        self.assertEqual(step["step_kind"], "WORLD_ACTION")
        self.assertEqual(step["action_kind"], "WORLD_ACTION")
        self.assertEqual(step["target_ref"], POSE)
        self.assertEqual(set(step["payload_spec"]),
                         {"verb", "target", "expected_dependencies"})
        self.assertEqual(step["payload_spec"]["verb"], "POSE")
        self.assertEqual(step["payload_spec"]["target"], POSE)
        self.assertEqual(step["payload_spec"]["expected_dependencies"], {})
        self.assertEqual(step["expected_effect_predicates"], ["body_pose_changed"])
        rest = body_actions.build_body_action_step(
            REST_START, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        self.assertEqual(rest["expected_effect_predicates"],
                         ["recovery_session_started"])
        # 非 bodyact 目标仍被拒；身份参数不制造新的准入。
        with self.assertRaises(body_actions.BodyActionTargetError):
            body_actions.build_body_action_step(
                "place:home", place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        with self.assertRaises(body_actions.BodyActionTargetError):
            body_actions.build_body_action_step(
                "bodyact:rest:start:sleep_like", place=PLACE_HOME, revision=7,
                decision_ref=DECISION_A)
        # Decision id 不是无限容器；超长直接拒绝，而不是悄悄截断成可能碰撞的前缀。
        with self.assertRaises(body_actions.BodyActionTargetError):
            body_actions.build_body_action_step(
                POSE, place=PLACE_HOME, revision=7, decision_ref="dec:" + "x" * 300)


@needs_life
class BodyActionCycleWiringTests(unittest.TestCase):
    """正式 cycle 的 BODY 分支把 committed Decision 交给同一个 builder。"""

    def test_the_cycle_passes_the_committed_decision_into_the_body_step(self) -> None:
        cycle = _cycle(_candidate(POSE))
        decision = _decision("cand:body-identity", DECISION_A)
        step = cycle._world_step_spec(decision)
        expected = body_actions.build_body_action_step(
            POSE, place=PLACE_HOME, revision=7, decision_ref=DECISION_A)
        self.assertEqual(step["idempotency_key"], expected["idempotency_key"])
        self.assertIn(DECISION_A.split(":", 1)[1], step["idempotency_key"])
        # 真实 cycle 读的是提交时选中的候选，不重新猜目标。
        self.assertEqual(cycle.runtime.candidate_read.reads, ["cand:body-identity"])
        self.assertEqual(cycle.last_world_target_ref, POSE)

    def test_two_decisions_in_the_same_observed_state_get_two_step_identities(self) -> None:
        cycle = _cycle(_candidate(POSE))
        first = cycle._world_step_spec(_decision("cand:body-identity", DECISION_A))
        # 第二次观测没有任何变化：place/revision/target 完全一样。
        second = cycle._world_step_spec(_decision("cand:body-identity", DECISION_B))
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])

    def test_the_same_decision_stays_stable_when_the_observation_moves(self) -> None:
        cycle = _cycle(_candidate(POSE))
        first = cycle._world_step_spec(_decision("cand:body-identity", DECISION_A))
        cycle.last_world_observation = {
            "location": {"place_id": "place:street_1"},
            "world_revision": 9,
            "pose": "lying",
            "orientation": "east",
        }
        again = cycle._world_step_spec(_decision("cand:body-identity", DECISION_A))
        self.assertEqual(first["idempotency_key"], again["idempotency_key"])

    def test_a_decision_without_an_id_falls_back_to_the_old_key(self) -> None:
        cycle = _cycle(_candidate(POSE))
        step = cycle._world_step_spec(_decision("cand:body-identity", None))
        self.assertEqual(
            step["idempotency_key"],
            "idem_step:v02:world:bodyact:place:home:r7:bodyact:pose:seated:north")


if __name__ == "__main__":
    unittest.main()
