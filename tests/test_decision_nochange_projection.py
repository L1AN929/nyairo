#!/usr/bin/env python3
"""已选择 CONTINUE 但 writer 无变化：正式决定与执行结果在同一读面可读。

本模块守一条真实缺口：AG-1 已经**正式提交**了一条 ``CONTINUE``（或 ``START``），
内容 writer 随后合法回答 ``no_change``。这既不是模型选了 ``NO_ACTION``，也不是写成功。
旧读面在 coordinator 内部懒物化 step 时抛出 ``ModelNoChange`` 的那条路径上丢掉了已提交
Decision：``base`` 还没写入 ``decision`` / ``decision_id`` / ``decision_route``（那段
``base.update`` 发生在 ``run_decision_and_adoption_cycle`` 正常返回之后），于是 wake 只剩
``outcome=NO_ACTION``、``decision`` 为空，容易被读成"模型明确选择安静"。

本模块只用正式链证明：正式 ``START`` 真写 → 同一作品/Activity 上真实 ``CONTINUE`` →
脚本 writer 合法 ``no_change`` → owner 版本 / Action / receipt / M0 都不增，而
cycle → runner → resident/report 的既有读面同一次就能看到 ``CONTINUE`` + route +
``work_no_change``（无新版本）。

真实件：真实 Supply owner、真实 M0 Memory、真实 ``ProductionLifeRuntime``、正式 runner、
AG-0/AG-1/AG-2/AR-0。替身只有 Decision 与内容回答（脚本，明示）与脚本 writer 的
``no_change`` 输入（现有 writer 自己的合法接口，非伪造已提交 Decision）。
POSIX 专用（``fcntl`` + AF_UNIX）；原生 Windows 上整模块 skip。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: "str | None" = None
driver = None
sharing = None
restart = None
if os.name == "posix":  # pragma: no branch
    try:
        import v05_low_budget_continuation_smoke as driver  # noqa: E402
        import v05_result_sharing_continuation_smoke as sharing  # noqa: E402
        import v05_restart_continuation_smoke as restart  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))

#: 6 小时静默 TTL 是 360 分钟；用 400 分钟隔开每一轮，保证是"新处境"而不是被台账吞掉。
STEP = 400
#: 只放进 writer 的 ``self_note``，验证它不会出现在任何读面。
SENTINEL = "PRIVACY-SENTINEL-DECISION-NOCHANGE-7f3a"


@POSIX_ONLY
@needs_fixture
class DecisionNoChangeProjectionTest(unittest.TestCase):
    """一条隔离的正式链：START 真写 → CONTINUE+writer no_change → NO_ACTION → 失败。"""

    def _open_chain(self, tag: str):
        root = Path(tempfile.mkdtemp(prefix="nyairo-decision-nochange-%s-" % tag, dir="/tmp"))
        restart.prepare_fixture(root)
        chain = driver.open_chain(root, tag, day_max=20, window_max=20, max_cog=20)
        self.addCleanup(lambda: driver.close_chain(chain))
        return chain

    def test_a_committed_continue_with_writer_no_change_keeps_both_facts(self):
        chain = self._open_chain("main")

        # -- 正式 START 真写：owner 写入成功不被误分类 -------------------------------
        seed = driver.seed_first_write(chain, 0)
        artifact = seed.artifact_id
        self.assertTrue(artifact, "START did not create a work")
        self.assertEqual(seed.wake.decision, "START")
        self.assertEqual(seed.wake.outcome, "ACTED")
        self.assertIs((seed.wake.extra or {}).get("owner_version_created"), True,
                      seed.wake.to_dict())
        work_before = driver.work_state(chain, artifact)
        actions_before = driver.action_count(chain)
        receipts_before = driver.receipt_tuple(chain)
        memory_before = driver.memory_rows(chain)
        decisions_before = driver.decision_count(chain)

        # -- 同一作品 / Activity 上真实 CONTINUE，writer 合法 no_change ----------------
        observed = driver.stamp(STEP)
        candidate = sharing.continue_candidate(chain, artifact, observed_at=observed)
        self.assertIsNotNone(candidate, "no CONTINUE candidate for the same work")
        chain.client.payload = {"kind": "no_change", "text": "", "self_note": SENTINEL}
        chain.planner.plan = [{"script": {"decision": "CONTINUE",
                                          "selected_candidate_id": candidate,
                                          "deferred_candidate_ids": []}}]
        wake = driver.run_window(chain, STEP, "continue-nochange")
        extra = dict(wake.extra or {})

        # 正式决定与执行结果必须同时可读，且不能伪装成模型明确 NO_ACTION。
        self.assertEqual(wake.decision, "CONTINUE", wake.to_dict())
        self.assertEqual(wake.outcome, "NO_ACTION", wake.to_dict())
        self.assertEqual(extra.get("decision_route"), "COGNITIVE_REQUIRED", wake.to_dict())
        self.assertTrue(str(extra.get("decision_id") or "").startswith("dec:"),
                        wake.to_dict())
        self.assertEqual(extra.get("selected_candidate_ref"), candidate, wake.to_dict())
        self.assertEqual(extra.get("owner_version_created"), False, wake.to_dict())
        self.assertEqual(extra.get("work_no_change"),
                         {"reason_code": "NO_CHANGE", "owner_called": False,
                          "version_created": False}, wake.to_dict())
        # 一次正式决定轮，内容是另一笔预算，不在 llm_calls 里重复计。
        self.assertEqual(int(wake.llm_calls), 1, wake.to_dict())

        # owner 版本 / Action / receipt / M0 都不因"决定 CONTINUE"而虚增。
        self.assertEqual(driver.work_state(chain, artifact), work_before)
        self.assertEqual(driver.action_count(chain), actions_before)
        self.assertEqual(driver.receipt_tuple(chain), receipts_before)
        self.assertEqual(driver.memory_rows(chain), memory_before)
        self.assertEqual(driver.decision_count(chain), decisions_before + 1)

        # -- 既有 resident / report 读面同一次就能看出 CONTINUE 与"无新版本" -----------
        import autonomous_life_resident as resident

        instance = resident.AutonomousLifeResident(state_root=chain.state_root,
                                                   environment=chain.env)
        instance.last_tick = {"due": wake.to_dict(), "at": wake.at, "tick": 1}
        due_view = instance._last_due_view()
        self.assertEqual(due_view["decision"], "CONTINUE", due_view)
        self.assertEqual(due_view["outcome"], "NO_ACTION", due_view)
        self.assertEqual(due_view["owner_version_created"], False, due_view)
        self.assertEqual(due_view["decision_route"], "COGNITIVE_REQUIRED", due_view)
        instance._count(wake.to_dict())
        self.assertEqual(instance.counters["no_action"], 1)
        self.assertEqual(instance.counters["no_action_with_decision"], 1)

        # -- 隐私：writer 的 self_note 不进任何读面 ----------------------------------
        report = json.dumps(wake.to_dict(), ensure_ascii=False)
        report += json.dumps(due_view, ensure_ascii=False)
        self.assertNotIn(SENTINEL, report)
        self.assertNotIn(SENTINEL, json.dumps(instance.counters, ensure_ascii=False))

        # -- 正控：真实模型 NO_ACTION 不能被重标成 CONTINUE --------------------------
        chain.planner.plan = [{"script": {"decision": "NO_ACTION",
                                          "selected_candidate_id": None,
                                          "deferred_candidate_ids": [],
                                          "reason_codes": ["NO_ACTION_CHOSEN"],
                                          "short_rationale": "stub: nothing to do"}}]
        quiet = driver.run_window(chain, STEP * 2, "model-no-action")
        quiet_extra = dict(quiet.extra or {})
        self.assertEqual(quiet.decision, "NO_ACTION", quiet.to_dict())
        self.assertEqual(quiet.outcome, "NO_ACTION", quiet.to_dict())
        self.assertIn("NO_ACTION_CHOSEN", quiet_extra.get("reason_codes") or [],
                      quiet.to_dict())
        self.assertNotEqual(quiet.decision, "CONTINUE")

        # -- 正控：没有提交 Decision 的失败轮不能沿用上一轮的 CONTINUE ----------------
        def _provider_down(*_args, **_kwargs):
            raise RuntimeError("provider down (scripted)")

        chain.planner.invoke = _provider_down
        failed = driver.run_window(chain, STEP * 3, "decision-failed")
        failed_extra = dict(failed.extra or {})
        self.assertIn(failed.outcome,
                      {"DECISION_FAILED", "STEP_REFUSED", "BUDGET_EXHAUSTED"},
                      failed.to_dict())
        self.assertFalse(failed.decision, failed.to_dict())
        self.assertNotEqual(failed_extra.get("decision_id"), extra.get("decision_id"))


@POSIX_ONLY
class RuleResolvedRouteControlTest(unittest.TestCase):
    """正控（小）：真实 AG-1 在空候选集上按规则解析，绝不调模型。"""

    def test_an_empty_candidate_set_is_ruled_no_action_instead_of_a_model_choice(self):
        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        observed_at = "2026-11-10T09:00:00+00:00"
        subject = ag0.GLOBAL_SUBJECT_ID
        opportunity = ag1.DecisionOpportunity.create(
            trigger_kind=ag1.TRIGGER_EXPLICIT_REEVALUATION,
            trigger_ref="evt:decision-nochange:empty",
            candidate_set=ag0.CandidateSet(
                candidate_set_id="cset:empty", subject_id=subject, candidate_refs=[],
                source_snapshot_refs=[], observed_at=observed_at, built_at=observed_at),
            observed_at=observed_at)
        capacity = ag1.AgencyCapacitySnapshot(
            capacity_snapshot_id="cap:test", subject_id=subject,
            captured_at=observed_at, source_revisions=["ctx:test"],
            body_capacity_state=ag1.CAPACITY_AVAILABLE,
            resource_availability_state=ag1.CAPACITY_AVAILABLE,
            runtime_load_state=ag1.CAPACITY_AVAILABLE,
            privacy_availability_state=ag1.CAPACITY_UNKNOWN,
            device_availability_state=ag1.CAPACITY_UNKNOWN,
            overall_capacity_state=ag1.CAPACITY_AVAILABLE,
            interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE)
        context = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:empty", subject_id=subject,
            opportunity_ref=opportunity.opportunity_id,
            candidate_set_ref="cset:empty", life_frame_ref="lframe:test",
            captured_at=observed_at, source_revisions={},
            snapshot_fingerprint="sfp:test")
        routed = ag1.DecisionPolicyRouter().route_opportunity(
            opportunity=opportunity, allowed_candidates=[], context_snapshot=context,
            capacity_snapshot=capacity)
        self.assertEqual(routed["route"], ag1.ROUTE_RULE_RESOLVED)
        self.assertEqual(routed["rule_output"]["decision"], ag1.DECISION_NO_ACTION)
        self.assertEqual(list(routed["rule_output"]["reason_codes"]),
                         [ag1.REASON_NO_LEGAL_CANDIDATE])


if __name__ == "__main__":
    unittest.main()
