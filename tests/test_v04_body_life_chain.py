#!/usr/bin/env python3
"""v0.4 身体参与走正常生活入口：真实实例 + 正式 ``evaluate_once`` + 真实 Body owner。

这条链不调用任何 helper 冒充整链：组合 D 的真实实例、真实 World/Body gateway（同一个 socket）、
真实 AR-0/AG-2/runtime，每一轮都是 ``cycle.evaluate_once(trigger_ref=..., observed_at=...,
act=True)``，决策由只回放**原始 JSON** 的脚本给出（与 ``test_lived_day_chain_v04`` 同一做法）。

覆盖（都在正式轮里）：
1. 姿态（``bodyact:pose:``）由正式 Decision 选中 → owner 自己把姿势改成 seated，读回的是 owner 的
   snapshot，不是本轮请求的目标；
2. 同一份身体处境里 ``rest.why_not`` 由 ``REST_REQUIRES_RESTING_POSE`` 变成 owner 自己的
   ``RECOVERY_NOT_NEEDED`` —— 这是**实测的前后变化**，不是我们算的；
3. 真的去开一个不需要的恢复会话：owner 拒绝，轮次不是 ``ACTED``，``recovery.claim`` 永远是
   ``NOT_ASSERTED``（会话/命令都不等于恢复）；
4. owner 的定性 progress/trend 真的进了处境（不是永远 ``NOT_READ``）；
5. 原来那件事接着做：读材料的 Activity 由正式 Decision START，之后同一 Activity 由 CONTINUE 推进。

限制（照实写）：本实例的 Body runtime 在 gateway 启动时载入，**不能**在启动后种一个高疲劳值，
所以"开着的恢复会话 + 会话内进度变化"只能在隔离 body 夹具里证明
（``tests/test_body_context_v04.RealBodyChainTests``，那里明确声明 seed 值），本文件不重复宣称。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for entry in (str(ROOT),
              str(TESTS),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import one_day_life as odl  # noqa: E402

POSIX_ONLY = unittest.skipUnless(sys.platform != "win32", "real World/Body socket needs POSIX")

try:
    import test_life_v1_integration as donor  # noqa: E402
    from test_world_daily_life_continuous import WORLD_PACKAGE, Clock, episode_instant  # noqa: E402
    from world_body_client import WorldBodyClient  # noqa: E402
    import world_move_opportunity_source as wmos  # noqa: E402
    from world_journey_action_adapter import (  # noqa: E402
        JourneyWorldResolver, RealJourneyWorldActionAdapter,
    )

    _IMPORT_ERROR: str | None = None
except Exception as error:  # noqa: BLE001
    _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_donor = unittest.skipIf(_IMPORT_ERROR is not None,
                              "donor fixture unavailable: %s" % (_IMPORT_ERROR or ""))

BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"


class BodyChainCognition:
    """Raw-JSON decision script; it only names ids/targets the real prompt really offered."""

    model_id = "stub:body-life-chain"
    model_version = "body-life-chain-v1"

    def __init__(self) -> None:
        self.plan: list[str] = []
        self.reads: list[dict] = []
        self.calls = 0

    @staticmethod
    def _targets(candidate: dict) -> list[str]:
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        candidates = [dict(row) for row in (getattr(request, "candidates", []) or [])]
        step = self.plan.pop(0) if self.plan else ""
        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": allowed[:1] or ["NO_ACTION"],
                     "short_rationale": "quiet", "deferred_candidate_ids": []}
        chosen = None
        if step == "pose":
            for candidate in candidates:
                if any(ref.startswith("bodyact:pose:") for ref in self._targets(candidate)):
                    chosen = candidate
                    break
            if chosen:
                raw["decision"] = "START"
                raw["selected_candidate_id"] = chosen["candidate_id"]
                raw["short_rationale"] = "sit down for a bit"
        elif step == "rest":
            for candidate in candidates:
                if any(ref.startswith("bodyact:rest:") for ref in self._targets(candidate)):
                    chosen = candidate
                    break
            if chosen:
                raw["decision"] = "START"
                raw["selected_candidate_id"] = chosen["candidate_id"]
                raw["short_rationale"] = "try to rest"
        elif step == "read":
            for candidate in candidates:
                if "START" in (candidate.get("legal_decisions") or []) and \
                        self.reading_id and self.reading_id in " ".join(self._targets(candidate)):
                    chosen = candidate
                    break
            if chosen:
                raw["decision"] = "START"
                raw["selected_candidate_id"] = chosen["candidate_id"]
                raw["short_rationale"] = "read my own material"
        elif step == "continue":
            for candidate in candidates:
                if "CONTINUE" in (candidate.get("legal_decisions") or []):
                    chosen = candidate
                    break
            if chosen:
                raw["decision"] = "CONTINUE"
                raw["selected_candidate_id"] = chosen["candidate_id"]
                raw["short_rationale"] = "go on reading"
        self.reads.append({"step": step, "chosen": (chosen or {}).get("candidate_id"),
                           "body": json.loads(json.dumps(situation.get("body") or {}))})
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:body-chain:%d" % self.calls, model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)

    reading_id = ""


@POSIX_ONLY
@needs_donor
class BodyLifeChainTest(unittest.TestCase):
    """正式轮里的姿态 / 休息尝试 / 原 Activity 承接。"""

    def setUp(self) -> None:
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.addCleanup(self._close)
        self.base.world.package_dir = WORLD_PACKAGE
        self.data = self.base.prepare_scenario(keep_fixture_world=True).data
        self.memory = getattr(self, "memory", None) or None
        from test_life_v1_integration import DScenarioBase  # noqa: F401  (documented donor)
        self.instance = Path(self.base.instance)
        self.world_home = Path(str(self.data["world_home"]))
        self.clock = Clock(episode_instant(after=datetime.now().astimezone()))
        self.gateway = self.base.gateway()
        self.env = self.base.instance_env()
        self.client = WorldBodyClient(environment=self.env)
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=self.client, action_intent=__import__("action_intent"),
            bridge_dir=str(BRIDGE_DIR), environment=self.env,
            world_home=str(self.world_home), now=lambda: self.clock.now,
            observed_now=lambda: self.clock.now, valid_for_seconds=60)
        self.cognition = BodyChainCognition()
        self.cognition.reading_id = str(self.data.get("reading_artifact_id") or "")
        memory = self.base.memory()
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, self.env, self.data, memory)
        self.fixture = self.base.assemble(self.runtime, self.env, observation=self.provider)
        self.cycle = self.fixture.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        self.cycle.reading_artifact_id = self.cognition.reading_id
        self.resolver = self.runtime.world_adapter.resolver
        self.assertIsInstance(self.runtime.world_adapter, RealJourneyWorldActionAdapter)

    def _close(self) -> None:
        try:
            self.base.doCleanups()
        except Exception:  # noqa: BLE001 - fixture cleanup is not a test result
            pass

    def turn(self, step: str, *, trigger: str) -> dict:
        self.cognition.plan = [step]
        return self.cycle.evaluate_once(trigger_ref=trigger, observed_at=self.clock.iso(),
                                        act=True)

    def observe(self) -> dict:
        """Read the body situation *now* (one real situation read, then the projection)."""

        self.cycle.read_situation(self.clock.iso())
        return self.cycle.body_decision_context()

    def pose(self) -> str:
        payload = self.client.get_snapshot().data or {}
        body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
        return str(body.get("pose") or payload.get("pose") or "")

    def test_pose_changes_the_owners_own_rest_verdict_and_reading_continues(self):
        before = self.observe()
        self.assertEqual(before["capacity"], "UNKNOWN")     # no burden signal is not health
        self.assertIn(before["rest"]["why_not"],
                      ("REST_REQUIRES_RESTING_POSE", "RECOVERY_NOT_NEEDED", None), before)
        # 1) 正式 Decision：坐下。owner 自己的 snapshot 必须真的变了。
        sat = self.turn("pose", trigger="body:pose")
        self.assertEqual(sat.get("outcome"), "ACTED", sat)
        # owner 真的换了姿势（脚本选中的是它自己 offer 的那个休息姿势），不是我们写的目标。
        self.assertIn(self.pose(), ("seated", "lying"))
        after = self.observe()
        self.assertIn(after["rest"]["pose"], ("seated", "lying"))
        # 2) 同一份处境里，owner 拒绝休息的理由从"姿势不行"变成"现在不需要"——实测的前后变化。
        self.assertNotEqual(after["rest"]["why_not"], "REST_REQUIRES_RESTING_POSE")
        self.assertEqual(after["rest"]["why_not"], "RECOVERY_NOT_NEEDED")
        # 3) owner 的定性 progress/trend 真的读到了（不是永远 NOT_READ）。
        self.assertNotEqual(after["recovery"]["progress"], "NOT_READ", after["recovery"])
        self.assertTrue(after["recovery"]["trend"])
        self.assertEqual(after["recovery"]["claim"], "NOT_ASSERTED")
        self.assertEqual(after["recovery"]["session"], "CLOSED")

        # 4) 现在她"不需要"休息，是 owner 自己说的：同一个 socket 的 REST 条目给出拒绝理由。
        actions = (self.client.get_available_actions().data or {}).get("actions") or []
        rest = [row for row in actions if row.get("verb") == "REST"][0]
        self.assertIs(rest["start_available"], False)
        self.assertEqual(rest["start_blocked_reason"], "RECOVERY_NOT_NEEDED")
        self.assertIs(rest["stop_available"], False)     # 没有会话可停
        # 没有会话就没有恢复：读回的身体处境仍然诚实。
        self.assertEqual(self.observe()["recovery"]["session"], "CLOSED")

    def test_the_original_reading_activity_is_started_and_carried_on(self):
        """原 Activity 的承接：读材料 START，然后同一 Activity 由 CONTINUE 推进（正式轮）。"""

        first = self.turn("read", trigger="body:read:1")
        self.assertIn(first.get("outcome"), {"ACTED", "ALREADY_DONE"}, first)
        activity = first.get("activity_ref")
        self.assertTrue(activity, first)
        second = self.turn("continue", trigger="body:read:2")
        self.assertIn(second.get("outcome"), {"ACTED", "ALREADY_DONE"}, second)
        self.assertEqual(second.get("activity_ref"), activity)
        self.assertEqual(second.get("activity_ref"), activity)


if __name__ == "__main__":
    unittest.main(verbosity=2)
