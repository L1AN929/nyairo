#!/usr/bin/env python3
"""G2 explore-c 的本功能接缝：C 身份/额度、10 对探索统计、维护读面、精确 H、发送前硬门。

这些是机械/接缝证据：不联网、不建正式根、不启动 runtime。真实短链在
``scripts/g2_explore_c.py offline-check`` 里单独跑。
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import autonomous_life_assembly as assembly  # noqa: E402
import g2_explore_c as explore  # noqa: E402
import g2_next_live as live  # noqa: E402
import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402


class CProfileTest(unittest.TestCase):
    def test_c_identity_caps_and_layout(self) -> None:
        profile = live.C_PROFILE
        self.assertEqual(profile.run_id, "run:g2-explore-20261103-c")
        self.assertEqual(profile.domain_id, "g2:explore:20261103-c")
        self.assertEqual(profile.expected_history, 234)
        self.assertEqual((profile.request_cap, profile.acquisition_cap,
                          profile.shadow_cap, profile.continuation_cap,
                          profile.reserved_cap, profile.pairs),
                         (30, 8, 20, 0, 2, 10))
        self.assertTrue(profile.exploratory)
        self.assertEqual(profile.caps()["pairs"], 10)
        self.assertEqual(profile.caps()["reserved"], 2)
        self.assertEqual(profile.caps()["ambient"], "0")
        self.assertEqual(profile.caps()["gate"], "off")
        self.assertEqual(profile.main_root, profile.batch_root / "main")
        self.assertEqual(profile.authority_dir, profile.batch_root / "memory-authority")
        self.assertEqual(profile.branches_root, profile.batch_root / "branches")
        self.assertEqual(profile.deadline.isoformat(), "2026-11-03T21:45:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-03T19:30:00+08:00")
        self.assertEqual(explore.trace_path(profile),
                         profile.batch_root / "maintain_trace.jsonl")

    def test_old_profiles_and_defaults_unchanged(self) -> None:
        a, b = live.FORMAL_PROFILE, live.B_PROFILE
        self.assertEqual((a.request_cap, a.shadow_cap, a.pairs), (52, 40, 20))
        self.assertIsNone(a.caps()["ambient"])  # 旧批不新增显式 ambient
        self.assertEqual((b.expected_history, b.request_cap), (186, 52))
        self.assertEqual(live.formal_profile("a").batch_root, live.FORMAL_BATCH_ROOT)
        self.assertEqual(live.formal_profile("b").batch_root, live.B_FORMAL_BATCH_ROOT)
        self.assertEqual(live.formal_profile("c").batch_root, live.C_FORMAL_BATCH_ROOT)
        signature = inspect.signature(baseline.run_shadow)
        self.assertIsNone(signature.parameters["pair_count"].default)
        self.assertEqual(len(baseline.PAIR_ORDER), baseline.PAIR_COUNT)


class ShadowSeamTest(unittest.TestCase):
    @staticmethod
    def _samples(pairs: int, a_h: bool, b_h: bool) -> dict:
        samples = []
        for pair in range(1, pairs + 1):
            for arm, hit in (("A", a_h), ("B", b_h)):
                samples.append({"pair": pair, "arm": arm,
                                "classification": "H" if hit else "OTHER_LEGAL"})
        return {"samples": samples, "stop_reason": "", "pairs": []}

    def test_full_protocol_keeps_original_conclusion(self) -> None:
        stats = baseline.shadow_statistics(self._samples(20, True, False), {})
        self.assertEqual(stats["pairs_target"], 20)
        self.assertFalse(stats["exploratory_only"])
        self.assertIsNotNone(stats["mcnemar_two_sided_p"])
        self.assertEqual(stats["conclusion"], "HISTORY_PACKAGE_CHANGED_H_CHOICE")

    def test_ten_pairs_are_exploratory_only(self) -> None:
        stats = baseline.shadow_statistics(self._samples(10, True, False), {},
                                           pair_count=10)
        self.assertEqual((stats["pairs_complete"], stats["pairs_target"]), (10, 10))
        self.assertTrue(stats["exploratory_only"])
        self.assertIsNone(stats["mcnemar_two_sided_p"])
        self.assertEqual(stats["conclusion"], "EXPLORATORY_ONLY")
        self.assertEqual(live._conclusion_from_stats(stats), live.RESULT_EXPLORATORY)
        incomplete = baseline.shadow_statistics(self._samples(4, True, False), {},
                                                pair_count=10)
        self.assertEqual(incomplete["conclusion"], "NOT_TESTED_INCOMPLETE")

    def test_two_arms_only_differ_in_history_text(self) -> None:
        arm_a = [{"artifact_ref": "artifact:a", "version_ref": "artifactversion:v2",
                  "text": "第一段\n第二段"},
                 {"artifact_ref": "artifact:b", "version_ref": "artifactversion:v1",
                  "text": "另一段"}]
        arm_b = baseline.neutralise_history(arm_a)
        for field in ("artifact_ref", "version_ref"):
            self.assertEqual([row[field] for row in arm_a], [row[field] for row in arm_b])
        self.assertEqual([len(row["text"]) for row in arm_a],
                         [len(row["text"]) for row in arm_b])
        self.assertEqual([row["text"].count("\n") for row in arm_a],
                         [row["text"].count("\n") for row in arm_b])
        self.assertTrue(all(a["text"] != b["text"] for a, b in zip(arm_a, arm_b)))


class ExactHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = {"prior_experiences": [
            {"artifact_ref": "artifact:abc123", "version_ref": "artifactversion:v2",
             "text": "x"}]}

    def test_exact_artifact_and_version_match(self) -> None:
        self.assertTrue(v2.history_contains_exact(self.view, h_ref="artifact:abc123"))
        self.assertTrue(v2.history_contains_exact(
            self.view, h_ref="artifact:abc123", h_version="artifactversion:v2"))
        self.assertTrue(v2.history_contains_exact(
            self.view, h_ref="artifact:abc123", h_version="v2"))

    def test_substring_wrong_or_missing_version_is_not_a_match(self) -> None:
        self.assertFalse(v2.history_contains_exact(
            self.view, h_ref="artifact:abc", h_version="v2"))
        self.assertFalse(v2.history_contains_exact(
            self.view, h_ref="artifact:abc123", h_version="v3"))
        view = {"prior_experiences": [{"artifact_ref": "artifact:abc123", "text": "x"}]}
        self.assertFalse(v2.history_contains_exact(
            view, h_ref="artifact:abc123", h_version="v2"))

    def test_default_version_keeps_legacy_substring_membership(self) -> None:
        # 缺省版本时保持旧调用语义：artifact 子串匹配（前版曾改成整体相等，本单恢复）。
        view = {"prior_experiences": [{"artifact_ref": "artifact:abc123", "text": "x"}]}
        self.assertTrue(v2.history_contains_exact(view, h_ref="artifact:abc"))
        self.assertTrue(v2.history_contains_exact(view, h_ref="artifact:abc123"))
        # 给了版本就收紧为整体相等 + 版本对齐，子串不再算命中。
        self.assertFalse(v2.history_contains_exact(
            view, h_ref="artifact:abc", h_version="v2"))


class TrajectoryDigestTest(unittest.TestCase):
    def test_digest_ignores_observer_side_output(self) -> None:
        base = {"ok": True,
                "steps": [{"step": "start", "status": "ACTED", "after": None,
                           "state": None, "open_for_h": None, "ok": None}],
                "checks": {"h_established": True, "owner_version_created": True,
                           "structural_gate_ok": True},
                "shadow": {"requests_used": 20}, "transport_calls": 3, "trace_rows": 2}
        quiet = dict(base, trace_rows=0)
        self.assertEqual(explore._trajectory_digest(base),
                         explore._trajectory_digest(quiet))
        changed = dict(base, transport_calls=4)
        self.assertNotEqual(explore._trajectory_digest(base),
                            explore._trajectory_digest(changed))


class MaintainTraceTest(unittest.TestCase):
    @staticmethod
    def _poller(names: tuple[str, ...]) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            feeds=[types.SimpleNamespace(name=name) for name in names])

    def test_row_fields_world_three_state_and_file_projection(self) -> None:
        row = assembly.maintain_trace_row(
            index=7, upkeep={"checked": 3, "changed": 0, "satisfied": None,
                             "emitted_eligibilities": 2},
            events=[{"kind": "ARTIFACT_CHANGED"}], feed_errors=[],
            due={"status": "ACTED"}, poller=self._poller(("artifact_head",)))
        self.assertEqual((row["tick"], row["checked"], row["changed"],
                          row["satisfied"], row["emitted_eligibilities"]),
                         (7, 3, 0, None, 2))
        self.assertEqual(row["feed_events"], ["ARTIFACT_CHANGED"])
        self.assertIsNone(row["quiet_reason"])
        # 没有真实前后 revision 值时，revision 字段必须是 UNKNOWN——签名事件不算。
        self.assertEqual((row["world_revision_changed"], row["world_revision_basis"]),
                         ("UNKNOWN", "NO_REVISION_READ_PORT"))
        for key in live.TRACE_FIELDS:
            self.assertIn(key, row)
        # 签名是另名字段；事件/稳定/缺失三态各自成立，不冒充 revision。
        self.assertEqual(assembly._trace_world_signature(
            [{"kind": "WORLD_OPPORTUNITY"}], [], self._poller(("world_opportunity",))),
            ("CHANGED", "WORLD_SIGNATURE_EVENT"))
        self.assertEqual(assembly._trace_world_signature(
            [], [], self._poller(("world_opportunity",))),
            ("NOT_CHANGED", "WORLD_SIGNATURE_STABLE"))
        self.assertEqual(assembly._trace_world_signature([], [], self._poller(())),
                         ("UNKNOWN", "NO_WORLD_SIGNATURE_READ_PORT"))
        self.assertEqual(assembly._trace_world_signature(
            [], [{"ref": "world_opportunity"}], self._poller(("world_opportunity",))),
            ("UNKNOWN", "WORLD_FEED_ERROR"))
        scratch = Path(tempfile.mkdtemp(prefix="g2-explore-trace-"))
        path = scratch / "maintain_trace.jsonl"
        live._maintain_trace_observer(path)(
            {"tick": 1, "checked": 1, "private_text": "must not leak"})
        written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(written), set(live.TRACE_FIELDS))
        self.assertNotIn("private_text", written)

    def test_list_maintenance_counts(self) -> None:
        """maintain_waiting 的 satisfied/emitted_eligibilities 是 list，按 len 计数。"""

        cases = [
            ("empty_list", [], 0),
            ("one_item", ["e1"], 1),
            ("two_items", ["e1", "e2"], 2),
            ("int_zero", 0, 0),
            ("int_positive", 5, 5),
            ("missing_none", None, None),
            ("bool_true", True, None),
            ("bool_false", False, None),
            ("string", "abc", None),
            ("dict", {"a": 1}, None),
        ]
        for name, value, expected in cases:
            with self.subTest(case=name, value=value):
                self.assertEqual(assembly._trace_count(value), expected)
        # 调用接缝用的是 maintain_waiting 的真实返回形状：checked/changed 是 int，
        # satisfied/emitted_eligibilities 是 list。
        row = assembly.maintain_trace_row(
            index=1,
            upkeep={"checked": 2, "changed": 1, "satisfied": ["a"],
                    "emitted_eligibilities": []},
            events=[], feed_errors=[], due={"status": "ACTED"},
            poller=self._poller(()))
        self.assertEqual((row["checked"], row["changed"], row["satisfied"],
                          row["emitted_eligibilities"]), (2, 1, 1, 0))
        # 缺字段仍是 UNKNOWN，不能补成 0。
        empty = assembly.maintain_trace_row(
            index=2, upkeep={}, events=[], feed_errors=[], due=None,
            poller=self._poller(()))
        self.assertEqual((empty["checked"], empty["changed"], empty["satisfied"],
                          empty["emitted_eligibilities"]), (None, None, None, None))

    def test_world_revision_uses_only_real_before_after_values(self) -> None:
        self.assertEqual(
            assembly._trace_world_revision({"world_revision_before": "wrev:r1",
                                            "world_revision_after": "wrev:r2"}),
            ("CHANGED", "REVISION_VALUES"))
        self.assertEqual(
            assembly._trace_world_revision({"world_revision_before": "wrev:r2",
                                            "world_revision_after": "wrev:r2"}),
            ("NOT_CHANGED", "REVISION_VALUES"))
        self.assertEqual(assembly._trace_world_revision({"checked": 2}),
                         ("UNKNOWN", "NO_REVISION_READ_PORT"))
        self.assertEqual(assembly._trace_world_revision(None),
                         ("UNKNOWN", "NO_REVISION_READ_PORT"))

    def test_quiet_reason_is_fixed_enum(self) -> None:
        due = {"status": "QUIET",
               "extra": {"reconsideration": {"reason_code": "SAME_STATE_ALREADY_CONSIDERED"}}}
        self.assertEqual(assembly._trace_quiet_reason(due), "SAME_STATE_ALREADY_CONSIDERED")
        self.assertIsNone(assembly._trace_quiet_reason({"status": "NOT_DUE"}))
        self.assertEqual(assembly._trace_quiet_reason({"status": "QUIET", "extra": {}}),
                         "UNKNOWN")

    def test_observer_error_does_not_change_control_flow(self) -> None:
        def broken(_row):
            raise RuntimeError("observer boom")

        assembled = {"tick_observer": broken}
        result = {"waiting": {}, "events": [], "feed_errors": [], "due": {"status": "ACTED"}}
        assembly._emit_tick_trace(assembled, observer=None, poller=None, result=result)
        self.assertEqual(assembled["tick_observer_errors"], ["RuntimeError"])
        self.assertEqual(assembled["tick_index"], 1)


class BudgetGateTest(unittest.TestCase):
    @staticmethod
    def _view(calls: int) -> dict:
        return {"run_window_calls": calls}

    def test_shadow_and_total_gates(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="g2-explore-gate-"))
        shadow = replace(live.C_PROFILE, batch_root=scratch / "gate-shadow")
        shadow_ledger = live.LiveStageLedger(shadow, type("B", (), {
            "view": lambda self: {"run_window_calls": 0}})())
        shadow_ledger.start_stage("shadow")
        self.assertEqual(shadow_ledger.assert_room(
            "shadow", self._view(19))["stage_remaining"], 1)
        with self.assertRaises(baseline.BatchRefused) as caught:
            shadow_ledger.assert_room("shadow", self._view(20))
        self.assertEqual(caught.exception.reason_code, live.R_STAGE_CAP)

        # 隔离阶段门，单看总账本 30→31；阶段 20 门由上面覆盖。
        total = replace(live.C_PROFILE, batch_root=scratch / "gate-total", shadow_cap=100)
        total_ledger = live.LiveStageLedger(total, type("B", (), {
            "view": lambda self: {"run_window_calls": 0}})())
        total_ledger.start_stage("shadow")
        self.assertEqual(total_ledger.assert_room(
            "shadow", self._view(29))["total_remaining"], 1)
        with self.assertRaises(baseline.BatchRefused) as caught:
            total_ledger.assert_room("shadow", self._view(30))
        self.assertEqual(caught.exception.reason_code, live.R_TOTAL_CAP)


class SafeProjectionTest(unittest.TestCase):
    def test_status_trace_fields_survive_projection(self) -> None:
        projected = live._public_run({
            "ok": True,
            "maintain_trace": {"present": True, "lines": 3, "tick": 3,
                               "checked": 1, "feed_events": ["ARTIFACT_CHANGED"],
                               "world_revision_changed": "UNKNOWN",
                               "world_signature_changed": "CHANGED",
                               "quiet_reason": "SAME_STATE_ALREADY_CONSIDERED",
                               "private_text": "must not leak"},
            "prepared": True})
        self.assertTrue(projected["maintain_trace"]["present"])
        self.assertEqual(projected["maintain_trace"]["tick"], 3)
        self.assertNotIn("private_text", projected["maintain_trace"])


if __name__ == "__main__":
    unittest.main()
