#!/usr/bin/env python3
"""v0.6 E 入口的最小针对性用例。

只覆盖本功能的封闭值与边界，不重跑 D 的 ambient client 矩阵：
1. E 的身份/历史 232/窗口/预算/ambient/gate 都是自己的封闭值，D 没有被覆盖。
2. 旧自然批与 CLI 元组没有增长，E 只能由薄入口经 ``_use_batch`` 进入。
3. 坏账 fail-closed 覆盖 v0.6 D/E，旧批语义不变。
4. E 的收尾冗余是窗口 + 60 秒，最晚启动线在绝对截止之前。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402
import v06_world_natural_e as e  # noqa: E402


class ClosedProfileTest(unittest.TestCase):
    def test_e_identity_and_window_are_closed(self):
        profile = g1.V06_E_PROFILE
        self.assertEqual(profile.key, "v06-natural-e")
        self.assertEqual(str(profile.root), "/tmp/nyairo-v06-natural-20261103-e")
        self.assertEqual(str(profile.authority_dir),
                         "/tmp/nyairo-v06-natural-20261103-e-memory-authority")
        self.assertEqual(str(profile.authority_db),
                         "/tmp/nyairo-v06-natural-20261103-e-memory-authority/authority.db")
        self.assertEqual(profile.run_id, "run:v06-world-natural-20261103-e")
        self.assertEqual(profile.domain_id, "v06:world-natural:20261103-e")
        self.assertEqual(profile.expected_history, 232)
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-04T00:15:00+08:00")
        self.assertEqual(profile.deadline.isoformat(), "2026-11-04T00:50:00+08:00")
        self.assertEqual(g1._profile_window_seconds(profile), 1800)
        self.assertEqual(g1._profile_max_ticks(profile), 900)
        self.assertEqual(g1._profile_trace_max_records(profile), 4000)
        self.assertEqual(g1._world_intent_gate(profile), "off")
        self.assertEqual(g1._ambient_events_env(profile), "1")
        self.assertTrue(g1._is_natural(profile))
        self.assertTrue(g1._is_v06_natural(profile))

    def test_directions_do_not_overwrite_d_or_old_batches(self):
        d = g1.V06_D_PROFILE
        self.assertEqual(d.expected_history, 212)
        self.assertEqual(str(d.root), "/tmp/nyairo-v06-natural-20261103-d")
        self.assertEqual(g1._world_intent_gate(d), "off")
        self.assertEqual(g1._ambient_events_env(d), "1")
        self.assertEqual(g1.B_PROFILE.expected_history, 191)
        self.assertEqual(g1.C_PROFILE.expected_history, 192)
        self.assertEqual(g1._profile_window_seconds(g1.C_PROFILE), 10800)
        self.assertEqual(g1._world_intent_gate(g1.E_PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.B_PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.C_PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.D_PROFILE), "off")
        self.assertEqual(g1._world_intent_gate(g1.F_PROFILE), "off")
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["a"]))

    def test_the_ambient_switch_covers_only_v06_d_and_e(self):
        self.assertEqual(g1._ambient_events_env(g1.V06_E_PROFILE), "1")
        self.assertEqual(g1._ambient_events_env(g1.V06_D_PROFILE), "1")
        for profile in (g1.B_PROFILE, g1.C_PROFILE, g1.E_PROFILE, g1.D_PROFILE,
                        g1.BATCH_PROFILES["a"], g1.F_PROFILE):
            self.assertIsNone(g1._ambient_events_env(profile), profile.key)

    def test_the_old_natural_set_and_cli_tuples_did_not_grow(self):
        self.assertEqual(g1.NATURAL_PROFILES, frozenset({"d", "e"}))
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertNotIn("v06-natural-e", g1.BATCH_CHOICES)
        self.assertNotIn("v06-natural-e", g1.CLI_BATCH_CHOICES)
        self.assertIn("v06-natural-e", g1.MODE_PROFILES)

    def test_the_accounting_ceiling_is_local_and_never_trips_for_old_batches(self):
        self.assertEqual(g1.V06_TOTAL_ACCOUNTING_CAP, 1000)
        self.assertEqual(g1.V06_D_TOTAL_ACCOUNTING_CAP, 1000)
        self.assertEqual(g1.UTC_DAY_CAP, 300)
        self.assertLessEqual(g1.V06_E_PROFILE.expected_history + g1.REQUEST_CAP, 1000)
        self.assertFalse(g1._total_accounting_exceeded(767, profile=g1.V06_E_PROFILE))
        self.assertTrue(g1._total_accounting_exceeded(768, profile=g1.V06_E_PROFILE))
        self.assertFalse(g1._total_accounting_exceeded(768, profile=g1.C_PROFILE))

    def test_offline_reference_stays_before_the_latest_start(self):
        reference = g1.offline_reference(g1.V06_E_PROFILE)
        self.assertLess(reference, g1.V06_E_PROFILE.latest_start)
        self.assertLess(g1.V06_E_PROFILE.latest_start, g1.V06_E_PROFILE.deadline)
        self.assertGreaterEqual(g1._profile_max_ticks(g1.V06_E_PROFILE)
                                * g1.TICK_INTERVAL_SECONDS, 1800)

    def test_e_cleanup_is_window_plus_sixty_seconds(self):
        now = 1000.0
        limit = g1._natural_phase_timeout(g1.V06_E_PROFILE, now + 1800, now=now)
        self.assertAlmostEqual(limit, 1800 + 60.0, places=6)
        old = g1._natural_phase_timeout(g1.E_PROFILE, now + g1.WINDOW_SECONDS, now=now)
        self.assertAlmostEqual(old, g1.WINDOW_SECONDS + 120.0, places=6)


class EntryModuleTest(unittest.TestCase):
    def test_the_thin_entry_binds_only_the_e_profile(self):
        self.assertEqual(e.BATCH, "v06-natural-e")
        self.assertIs(e.PROFILE, g1.V06_E_PROFILE)
        self.assertTrue(e.OFFLINE_PREFIX.startswith("v06-natural-e-offline-"))
        self.assertEqual(g1._ledger_calls.__name__, "_ledger_calls")

    def test_the_endpoint_preflight_is_reused_from_the_previous_entry(self):
        self.assertEqual(e.dmod._endpoint_refusal({}).get("reason_code"),
                         g1.REASON_NO_MODEL_CONFIG)
        self.assertEqual(
            e.dmod._endpoint_refusal({"NYA02_MODEL_BASE_URL": "ftp://example.org/v1"})
            .get("reason_code"), e.dmod.REASON_ENDPOINT_SCHEME)


class LedgerScopeTest(unittest.TestCase):
    class _Stub:
        def __init__(self, state):
            self.state = state

        def view(self):
            return {"state": self.state, "run_window_calls": 7}

    def test_corrupt_ledger_fails_closed_for_e_and_d_but_not_old_batches(self):
        with g1._use_batch(g1.V06_E_PROFILE.key):
            self.assertIsNone(g1._ledger_calls(self._Stub("corrupt")))
            self.assertIsNone(g1._ledger_calls(self._Stub("CORRUPT")))
            self.assertIsNone(g1._ledger_calls(self._Stub("UNREADABLE")))
        with g1._use_batch(g1.V06_D_PROFILE.key):
            self.assertIsNone(g1._ledger_calls(self._Stub("corrupt")))
        with g1._use_batch(g1.E_PROFILE.key):
            self.assertEqual(g1._ledger_calls(self._Stub("corrupt")), 7)
            self.assertIsNone(g1._ledger_calls(self._Stub("CORRUPT")))
            self.assertEqual(g1._ledger_calls(self._Stub("UNREADABLE")), 7)


if __name__ == "__main__":
    unittest.main()
