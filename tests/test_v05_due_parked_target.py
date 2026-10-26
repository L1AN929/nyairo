#!/usr/bin/env python3
"""v0.5：到期「稍后」事项指向一个 WAITING/PAUSED 活动的同一个作品时，不得再发新 START。

本模块只守这一条既有规则（``2026-10-26-v04-day-life-core-continuity.md`` §5）：

* 目标已经有 PAUSED/WAITING Activity 时，个人机会来源**不发新候选**
  （``PAUSED_TARGET_REQUIRES_RESUME``），``refresh`` 时撤回；回到那个活动只能靠
  LR-4 资格 + owner 自己的 RESUME。
* 这个过滤只按 owner 目标身份生效：别的可续作品、没有挂起同目标的正控照常供应；
  无对象事项仍只走既有项目选项。

真链用上一块「到期重新考虑」的隔离夹具（不改旧文件）：真实 Supply owner、
ProductionLifeRuntime、正式 ``AutonomousLifeRunner.run_due`` → AG-1 原始 JSON → AG-2/LR-2/LR-4。
Decision 与内容回答是脚本替身（明示）。不宣称真模型自然采用、G1 或 24h。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import artifact_continuation_source as acs  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips the classes below
    try:
        import v05_due_reconsideration_smoke as driver  # noqa: E402
        import agency_decision_ag1 as ag1  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        driver = None
        ag1 = None
else:  # pragma: no cover
    driver = None
    ag1 = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


class ExtraTargetFilterTest(unittest.TestCase):
    """来源层最小单测：只按 owner 目标身份拒绝，不误伤项目选项。"""

    def _source(self, paused, rows):
        return acs.ArtifactContinuationSource(
            object(), subject_id="subject:test",
            paused_target_provider=lambda: set(paused),
            extra_opportunity_provider=lambda: list(rows))

    def test_a_due_item_on_a_parked_artifact_is_not_offered(self):
        source = self._source({"abc"}, [{
            "source_ref": "fitem:one", "target_refs": ["artifact:abc"],
            "title": "later", "kind": "FUTURE_ITEM_DUE", "revision": 1}])
        offered = source._extra_raw_items("2026-10-23T09:00:00+00:00")
        self.assertEqual(offered, [])
        self.assertEqual(source.last_extra_skip_reasons.get("fitem:one"),
                         "PAUSED_TARGET_REQUIRES_RESUME")

    def test_an_artifact_without_a_parked_activity_is_still_offered(self):
        source = self._source({"abc"}, [
            {"source_ref": "fitem:one", "target_refs": ["artifact:def"],
             "title": "later", "kind": "FUTURE_ITEM_DUE", "revision": 1},
            {"source_ref": "popt:opt", "target_refs": ["popt:opt"],
             "title": "option", "kind": "PRIVATE_PROJECT_OPTION"}])
        offered = source._extra_raw_items("2026-10-23T09:00:00+00:00")
        self.assertEqual([row["source_ref"] for row in offered], ["fitem:one", "popt:opt"])

    def test_the_same_object_spelled_differently_is_still_the_same_target(self):
        source = self._source({"abc"}, [{
            "source_ref": "fitem:one", "target_refs": ["artifact:artifact:abc"],
            "title": "later", "kind": "FUTURE_ITEM_DUE", "revision": 1}])
        self.assertEqual(source._extra_raw_items("2026-10-23T09:00:00+00:00"), [])


class ParkedCognition(driver.ScriptedCognition if driver is not None else object):
    """在上一块脚本 Decision 之上补三条本模块需要的策略；其余沿用原实现。"""

    EXTRA_POLICIES = ("pause_current", "abandon_current", "start_named_extra",
                      "resume_current")

    def invoke(self, request, *, schema_retry_index: int = 0):
        if self.policy not in self.EXTRA_POLICIES:
            return super().invoke(request, schema_retry_index=schema_retry_index)
        wanted = self.policy
        self.policy = "no_action"
        try:
            base = super().invoke(request, schema_retry_index=schema_retry_index)
        finally:
            self.policy = wanted
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = (base.raw_output.get("reason_codes") or allowed[:1]
                  or ["NO_LEGAL_CANDIDATE"])
        raw = None

        def legal(candidate, word):
            return word in (candidate.get("legal_decisions") or [])

        if wanted in ("pause_current", "abandon_current"):
            word = "PAUSE" if wanted == "pause_current" else "ABANDON"
            chosen = next((c for c in candidates
                           if c.get("candidate_kind") == "CONTINUE_CURRENT"
                           and legal(c, word)), None)
            if chosen is not None:
                raw = {"decision": word, "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: close this out",
                       "deferred_candidate_ids": []}
        elif wanted == "start_named_extra":
            wanted_ref = str(self.payload.get("item_ref") or "")
            chosen = next((c for c in candidates
                           if str(c.get("source_ref") or "") == wanted_ref
                           and legal(c, "START")), None)
            if chosen is not None:
                raw = {"decision": "START", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: take it up",
                       "deferred_candidate_ids": []}
        elif wanted == "resume_current":
            chosen = next((c for c in candidates
                           if c.get("candidate_kind") == "RESUME_ACTIVITY"
                           and legal(c, "RESUME")), None)
            if chosen is not None:
                raw = {"decision": "RESUME", "selected_candidate_id": self._chosen(chosen),
                       "reason_codes": reason, "short_rationale": "stub: come back",
                       "deferred_candidate_ids": []}
        if raw is None:
            return base
        return ag1.CognitiveDecisionResponse(
            model_run_ref=str(base.model_run_ref), model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)


@POSIX_ONLY
@needs_fixture
class ParkedTargetRunnerTest(unittest.TestCase):
    """真 owner 最小正式链；每个用例独立隔离根。"""

    def setUp(self) -> None:
        self.root = driver._fresh_root()
        driver.prepare_fixture(self.root)
        self.clock = driver.Clock(driver.at(0))
        self.cognition = ParkedCognition()
        self.services = driver._start_services(self.root / "supply")
        self.chain = driver.open_chain(self.root, self.services, self.clock, self.cognition)
        self.cognition.seed_artifact = str(self.chain.data["artifact_id"])

    def tearDown(self) -> None:
        try:
            driver.close_chain(self.chain)
        finally:
            driver._stop_services(self.services)

    # -- helpers ---------------------------------------------------------------------------

    def window(self, minute: int, policy: str = "no_action", payload=None) -> dict:
        return driver.window(self.chain, driver.at(minute), policy, payload)

    def activity_on(self, artifact_id: str) -> list:
        return [row for row in self.chain.cycle.open_activities()
                if artifact_id in [str(ref) for ref in
                                   (self.chain.cycle._activity_target_refs(row) or [])]]

    def proposed_item(self, artifact_id: str, minute: int, delay: int) -> str:
        record = self.window(minute, "no_action_propose",
                             {"title": "回头把这一段再改一遍", "in_minutes": delay,
                              "source_refs": ["artifact:%s" % artifact_id]})
        self.assertEqual(record.get("decision"), "NO_ACTION", record)
        items = [row for row in driver.open_items(self.chain)
                 if "artifact:%s" % artifact_id in (row.get("source_refs") or [])]
        self.assertEqual(len(items), 1, items)
        return str(items[0].get("item_ref"))

    # -- the counterexample ----------------------------------------------------------------

    def test_a_due_item_cannot_start_a_second_activity_for_a_paused_work(self):
        chain = self.chain
        seed = str(chain.data["artifact_id"])
        started = self.window(0, "start_seed")
        self.assertEqual(started.get("outcome"), "ACTED", started)
        seed_activity = str(started.get("activity_ref") or "")
        self.assertTrue(seed_activity)
        head_before = driver.head_version(chain, seed)
        versions_before = driver.version_count(chain, seed)

        item_ref = self.proposed_item(seed, 1, 4)

        # 到点窗口：目标此刻仍是 ACTIVE，候选必须被展示（过滤不能顺手封掉活着的目标）。
        display = self.window(5, "pause_current")
        self.assertEqual(display.get("decision"), "PAUSE", display)
        shown = [dict(c) for c in (self.cognition.last_candidates or [])
                 if str(c.get("source_ref") or "") == item_ref]
        self.assertEqual(len(shown), 1, self.cognition.last_candidates)
        self.assertEqual(driver.activity_status(chain, seed_activity), "PAUSED", display)
        self.assertEqual(driver.foreground(chain), "", display)
        self.assertEqual(driver.eligibilities(chain, seed_activity), [])
        shown_id = str(shown[0].get("candidate_id") or "")

        writer_calls = int(chain.writer.call_count)
        calls_before = int(self.cognition.call_count)
        # 目标现在 PAUSED 且无恢复资格：同一到期事项不得再供 START。
        after = self.window(6, "start_named_extra", {"item_ref": item_ref})
        self.assertEqual(after.get("decision"), "NO_ACTION", after)
        self.assertNotIn(item_ref, driver.extra_items(chain))
        self.assertEqual(
            chain.cycle.artifact_source.last_extra_skip_reasons.get(item_ref),
            "PAUSED_TARGET_REQUIRES_RESUME")
        if int(self.cognition.call_count) > calls_before:
            self.assertEqual(
                [c for c in (self.cognition.last_candidates or [])
                 if str(c.get("source_ref") or "") == item_ref], [])
        # 采纳前刷新必须把已展示的旧候选撤回，且没有第二个 Activity。
        stale = chain.cycle.runtime.candidate_read.get_candidate(shown_id) or {}
        self.assertIn(str(stale.get("availability_status") or ""),
                      {"WITHDRAWN", "STALE"}, stale)
        self.assertEqual(str(stale.get("invalidation_reason") or ""),
                         "PAUSED_TARGET_REQUIRES_RESUME")
        activities = self.activity_on(seed)
        self.assertEqual(len(activities), 1, activities)
        self.assertEqual(str(activities[0].get("status") or ""), "PAUSED", activities)
        self.assertEqual(driver.head_version(chain, seed), head_before)
        self.assertEqual(driver.version_count(chain, seed), versions_before)
        self.assertEqual(int(chain.writer.call_count), writer_calls)
        self.assertEqual(driver.eligibilities(chain, seed_activity), [])

    def test_a_due_item_cannot_start_a_second_activity_for_a_waiting_work(self):
        chain = self.chain
        seed = str(chain.data["artifact_id"])
        started = self.window(0, "start_seed")
        self.assertEqual(started.get("outcome"), "ACTED", started)
        seed_activity = str(started.get("activity_ref") or "")
        # 事项先在前台还 ACTIVE 时提出；到期窗口里真实 WAIT 让同一个作品进入 WAITING。
        item_ref = self.proposed_item(seed, 1, 3)
        # 自身恢复时间只比到期晚一分钟：同一时刻以后由 LR-4 自己发 OPEN。
        parked = self.window(4, "park", {"in_minutes": 1})
        self.assertEqual(parked.get("decision"), "WAIT", parked)
        self.assertEqual(driver.activity_status(chain, seed_activity), "WAITING", parked)
        self.assertEqual(driver.foreground(chain), "", parked)
        # 目标还活着但已经 WAITING：到期事项在展示层仍应出现（不是全局封杀事项）。
        self.assertIn(item_ref, driver.extra_items(chain))

        head_before = driver.head_version(chain, seed)
        versions_before = driver.version_count(chain, seed)
        writer_calls = int(chain.writer.call_count)

        # 同一时刻：事项到期 + owner 的 TIME_AT 也到期。新 START 不得出现，只有正式 RESUME。
        back = self.window(5, "resume_current")
        self.assertNotIn(item_ref, driver.extra_items(chain))
        self.assertEqual(
            chain.cycle.artifact_source.last_extra_skip_reasons.get(item_ref),
            "PAUSED_TARGET_REQUIRES_RESUME")
        self.assertNotIn(
            item_ref, [str(c.get("source_ref") or "")
                       for c in (self.cognition.last_candidates or [])])
        resume_offers = [c for c in (self.cognition.last_candidates or [])
                         if c.get("candidate_kind") == "RESUME_ACTIVITY"
                         and "RESUME" in (c.get("legal_decisions") or [])]
        self.assertEqual(len(resume_offers), 1, self.cognition.last_candidates)
        self.assertEqual(back.get("decision"), "RESUME", back)
        self.assertEqual(str(back.get("activity_ref") or ""), seed_activity, back)
        self.assertEqual(driver.activity_status(chain, seed_activity), "ACTIVE", back)
        self.assertEqual(len(driver.eligibilities(chain, seed_activity, "CONSUMED")), 1)
        self.assertEqual(len(self.activity_on(seed)), 1)
        # RESUME 走的是同一个 Activity 的正常接续：它执行一步、写一版，不新建活动。
        self.assertNotEqual(driver.head_version(chain, seed), head_before)
        self.assertEqual(driver.version_count(chain, seed), versions_before + 1)
        self.assertEqual(int(chain.writer.call_count), writer_calls + 1)

    def test_a_due_item_on_a_continuable_work_is_still_offered_and_written(self):
        chain = self.chain
        seed = str(chain.data["artifact_id"])
        started = self.window(0, "start_seed")
        self.assertEqual(started.get("outcome"), "ACTED", started)
        seed_activity = str(started.get("activity_ref") or "")
        # 终止那个 Activity，但作品仍是 ACTIVE 可续：目标没有 PAUSED/WAITING，正控。
        abandoned = self.window(1, "abandon_current")
        self.assertEqual(abandoned.get("decision"), "ABANDON", abandoned)
        self.assertEqual(driver.activity_status(chain, seed_activity), "ABANDONED", abandoned)

        item_ref = self.proposed_item(seed, 2, 4)
        head_before = driver.head_version(chain, seed)
        versions_before = driver.version_count(chain, seed)
        taken = self.window(6, "start_named_extra", {"item_ref": item_ref})
        self.assertIn(item_ref, driver.extra_items(chain))
        shown = [c for c in (self.cognition.last_candidates or [])
                 if str(c.get("source_ref") or "") == item_ref]
        self.assertEqual(len(shown), 1, self.cognition.last_candidates)
        self.assertIn("START", shown[0].get("legal_decisions") or [])
        self.assertEqual(taken.get("decision"), "START", taken)
        self.assertEqual(taken.get("outcome"), "ACTED", taken)
        self.assertNotEqual(driver.head_version(chain, seed), head_before)
        self.assertGreater(driver.version_count(chain, seed), versions_before)
        self.assertEqual(len(self.activity_on(seed)), 1)
        self.assertEqual(str(self.activity_on(seed)[0].get("status") or ""), "ACTIVE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
