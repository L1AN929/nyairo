"""单 World 分支的候选匹配门：离线正负控。

只有 World 一条路径可走时，已提交 Decision 仍必须真的选中 World 候选：World 的 Activity
形状与 step 都不能在拒绝之前被构建，更不能落到私人事项/阅读/作品 fallback 上。合法 World
候选（含私人候选上的合法 World extras）按既有五类前缀谓词照常承接。

本文件不启动真实模型、不连任何 socket，只复用既有 Stage A 的替身与临时目录夹具。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import autonomous_life_cycle as alc  # noqa: E402
import life_future_items as lfi  # noqa: E402
from tests.test_v04_autonomous_day_stage import (  # noqa: E402
    _Decision, _FakePort, _FakeRuntime, _ScriptedCoordinator)

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

TS = "2026-11-02T09:40:00+00:00"
ART = "artifact:" + "a" * 32
HEAD = "artifact-version:" + "b" * 8
PRIVATE = "cand:private"
WORLD = "cand:world"
REFUSAL = "names no observed World target"


class _FakeWorldSource:
    """AG-0 的观察源替身：只记录被喂进来的机会，不扫描世界。"""

    def __init__(self):
        self.records = []

    def record_observed_opportunity(self, item):
        self.records.append(dict(item))

    def expire_opportunity(self, opportunity_id, observed_at=None):
        return None


class _FakeWorldProvider:
    """只读观察缝的替身：与真实 provider 同形（机会列表 + last_observation）。"""

    def __init__(self, *, target="place:park", place="home", revision=7):
        self.last_observation = {"location": {"place_id": place}, "world_revision": revision}
        self._target = target

    def __call__(self):
        return [{"opportunity_id": "wop:" + self._target,
                 "kind": "MOVE",
                 "target_ref": self._target,
                 "target_refs": [self._target],
                 "location_ref": "place:home",
                 "observed_at": TS}]


class _CountingCoordinator(_ScriptedCoordinator):
    """AG-1+AG-2 的脚本替身，额外记录被调用次数（证明没有重试/改写）。"""

    def __init__(self, script):
        super().__init__(script)
        self.calls = 0
        self.seen = []

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.calls += 1
        self.seen.append(kwargs)
        return super().run_decision_and_adoption_cycle(**kwargs)


class _Harness:
    def __init__(self, *, direction="private", artifact=False,
                 private_target_refs=None, world_target="place:park"):
        self.root = tempfile.mkdtemp(prefix="sw-guard-")
        self.runtime = _FakeRuntime(self.root)
        self.runtime.world_opportunity_source = _FakeWorldSource()
        self.runtime.world_action_adapter = object()
        self.port = _FakePort([])
        if artifact:
            self.port.add_artifact(ART, title="一件作品", head=HEAD)
        store = lfi.FutureItems(self.root, subject_id="subject:test")
        saved = store.propose(title="回头读一会儿", due_at=TS, source_refs=[ART], now=TS)
        self.item_ref = saved["item"]["item_ref"]
        self.cycle = alc.AutonomousLifeCycle(
            self.runtime, artifact_port=self.port, subject_id="subject:test",
            workspace_id="workspace:test", artifact_grant_id="grant:sw-guard", enabled=True,
            env={}, pause_state_root=self.root, continuity_state_root=self.root,
            future_items=store, world_opportunity_provider=_FakeWorldProvider(
                target=world_target))
        self.runtime.candidate_read.rows[PRIVATE] = {
            "candidate_id": PRIVATE,
            "candidate_kind": "CONSIDER_PERSONAL_OPPORTUNITY",
            "target_refs": list(private_target_refs or [ART]),
            "source_ref": self.item_ref,
            "title": "回头读一会儿",
        }
        self.runtime.candidate_read.rows[WORLD] = {
            "candidate_id": WORLD,
            "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
            "target_refs": [world_target],
            "source_ref": "wop:" + world_target,
            "title": "去她看见的地方",
        }
        self.selected = WORLD if direction == "world" else PRIVATE
        self.coordinator = _CountingCoordinator(
            [_Decision("START", selected=self.selected)])
        self.runtime.coordinator = self.coordinator
        self.calls = {}

    def spy(self):
        """包住既有 builder，数它们是否真的被调用（不是只改返回值）。"""

        for name, attr in (("world_step", "_world_step_spec"),
                           ("world_adoption", "_world_adoption_spec"),
                           ("work_step", "_work_step_spec_for_decision"),
                           ("reading", "_reading_step_spec"),
                           ("project", "_project_step_spec")):
            original = getattr(self.cycle, attr)
            self.calls[name] = 0

            def wrapper(*args, __original=original, __name=name, **kwargs):
                self.calls[__name] += 1
                return __original(*args, **kwargs)

            setattr(self.cycle, attr, wrapper)
        return self.calls

    def run(self):
        return self.cycle.evaluate_once(trigger_ref="evt:sw-guard", observed_at=TS, act=True)


@POSIX_ONLY
class SingleWorldCandidateGuardTest(unittest.TestCase):
    def test_single_world_round_refuses_a_personal_candidate_before_any_world_builder(self):
        """A：只有 World 可走，Decision 选了私人候选 —— 拒绝可观测，任何 builder 都不跑。"""

        harness = _Harness(direction="private")
        calls = harness.spy()
        decided = harness.coordinator.script[0]
        before = (decided.decision, decided.decision_id, decided.selected_candidate_ref)
        out = harness.run()

        self.assertEqual(out.get("outcome"), alc.OUTCOME_DECISION_FAILED, out)
        self.assertIn(REFUSAL, str(out.get("detail")), out)
        self.assertEqual(out.get("step_path"), "WORLD_AR0", out)
        # World 的 Activity 形状与 step 都没有被构建，更没有走任何别的承接路径。
        self.assertEqual(calls, {"world_step": 0, "world_adoption": 0, "work_step": 0,
                                 "reading": 0, "project": 0}, calls)
        self.assertEqual(harness.runtime.coordinator.built, {})
        self.assertEqual(harness.port.commits, [])
        # 已提交的决定原样保留：只问了一次，没有重试，也没有改写它的选择。
        self.assertEqual(harness.coordinator.calls, 1)
        self.assertEqual((decided.decision, decided.decision_id, decided.selected_candidate_ref),
                         before)
        self.assertTrue(callable(harness.coordinator.seen[0]["step_spec_override"]))
        self.assertIsInstance(harness.coordinator.seen[0]["step_spec_override"],
                              alc._DecisionBoundStepSpec)

    def test_an_unreadable_candidate_is_refused_not_defaulted_to_the_first_world_target(self):
        """C：读不到 candidate 就诚实拒绝，不拿观察到的第一个世界目标兜底。"""

        harness = _Harness(direction="private")
        calls = harness.spy()

        def broken(_candidate_id):
            raise RuntimeError("candidate store unavailable")

        harness.runtime.candidate_read.get_candidate = broken
        out = harness.run()

        self.assertEqual(out.get("outcome"), alc.OUTCOME_DECISION_FAILED, out)
        self.assertIn(REFUSAL, str(out.get("detail")), out)
        self.assertIn("RuntimeError", str(harness.cycle.last_world_error))
        self.assertEqual(calls["world_step"], 0)
        self.assertEqual(calls["world_adoption"], 0)
        self.assertEqual(harness.runtime.coordinator.built, {})

    def test_single_world_round_builds_the_real_world_step_for_a_world_candidate(self):
        """B：同场景合法 World 候选 —— 按既有路径得到正确 target/owner 输入。"""

        harness = _Harness(direction="world")
        calls = harness.spy()
        out = harness.run()

        self.assertEqual(out.get("step_path"), "WORLD_AR0", out)
        self.assertEqual(calls["world_step"], 1, calls)
        self.assertEqual(calls["world_adoption"], 1, calls)
        self.assertEqual(calls["work_step"], 0, calls)
        self.assertEqual(calls["reading"], 0, calls)
        step = harness.runtime.coordinator.built.get("step_spec")
        self.assertIsNotNone(step, harness.runtime.coordinator.built)
        self.assertEqual(step.get("target_ref"), "place:park")
        self.assertEqual(step.get("payload_spec", {}).get("verb"), alc.MOVE_VERB)
        adoption = harness.runtime.coordinator.built.get("adoption_spec")
        self.assertIsNotNone(adoption, harness.runtime.coordinator.built)
        self.assertEqual(adoption.get("activity_kind"), "GO_TO_OBSERVED_PLACE")
        self.assertIn("world_fact:place:park", adoption.get("target_refs") or [])

    def test_world_extras_on_a_personal_candidate_still_reach_the_world_step(self):
        """B/C：私人候选带合法 World extras 时沿用原谓词，单 World 分支照常承接。"""

        harness = _Harness(direction="private", private_target_refs=[ART, "place:park"])
        calls = harness.spy()
        out = harness.run()

        self.assertEqual(out.get("step_path"), "WORLD_AR0", out)
        self.assertEqual(calls["world_step"], 1, calls)
        self.assertEqual(calls["work_step"], 0, calls)
        step = harness.runtime.coordinator.built.get("step_spec")
        self.assertEqual(step.get("target_ref"), "place:park")
        self.assertEqual(step.get("payload_spec", {}).get("verb"), alc.MOVE_VERB)

    def test_the_world_predicate_keeps_all_five_target_namespaces(self):
        """C：五类前缀都算 World；纯 artifact/dopp 不算；顺序不改变判定。"""

        for ref in ("place:park", "object:cup", "stay:bench", "bodyact:rest",
                    "dailyact:wash"):
            with self.subTest(ref=ref):
                self.assertTrue(alc.AutonomousLifeCycle._candidate_is_world(
                    {"target_refs": [ref]}))
        self.assertTrue(alc.AutonomousLifeCycle._candidate_is_world(
            {"target_refs": [ART, "place:park"]}))
        for refs in ([ART], ["dopp:something"], [], None):
            with self.subTest(refs=refs):
                self.assertFalse(alc.AutonomousLifeCycle._candidate_is_world(
                    {"target_refs": refs}))
        self.assertFalse(alc.AutonomousLifeCycle._candidate_is_world(None))

    def test_multi_path_routing_still_sends_a_world_choice_to_the_world_owner(self):
        """D：多路径原路由不变 —— 合法 World 动作没有被新门挡掉。"""

        harness = _Harness(direction="world", artifact=True)
        calls = harness.spy()
        out = harness.run()

        self.assertEqual(out.get("step_path"), "AR0_WORLD_ROUTED", out)
        self.assertEqual(calls["world_step"], 1, calls)
        step = harness.runtime.coordinator.built.get("step_spec")
        self.assertEqual(step.get("target_ref"), "place:park")
        self.assertEqual(step.get("payload_spec", {}).get("verb"), alc.MOVE_VERB)


if __name__ == "__main__":
    unittest.main()
