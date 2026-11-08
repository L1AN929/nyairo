#!/usr/bin/env python3
"""v0.6 D 入口的最小针对性用例。

三组，只覆盖本功能的封闭值与 client 边界：
1. 身份/预算/时间/ambient 开关都是 D 自己的封闭值，旧批不被覆盖。
2. 正式 `ProductionAgencyCognitionAdapter` 配可控脚本 client：ambient 事件 ID 与公开
   summary 必须以原文出现在 client 真正收到的输入里；没有事件/读不可用时如实报 0 或
   unavailable，不造事件、不把没有事件判成匹配失败。
3. 坏账 fail-closed 只覆盖 B/C/D，旧批语义不变。

这里用的是固定非产品 fixture（不是真实 World），只证明边界透传，不证明真实世界非空。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402
import v06_world_natural_d as d  # noqa: E402

EVENT_ID = "ambient-2026-11-03-home_window_sound"
EVENT_ID_2 = "ambient-2026-11-03-street_lawn_care"
EVENT_SUMMARY = "窗外传来一阵汽笛声。"
EVENT_SUMMARY_2 = "街角的草坪修剪机响了一会儿。"


def _ambient_world(*, events=None, status="available", reason=None):
    ambient = {"status": status, "source": "world_living_skeleton.observe_world.ambient_events"}
    if reason:
        ambient["reason_code"] = reason
    if events:
        ambient["events"] = list(events)
    return {
        "status": "observed",
        "source": "contract",
        "observed_at": "2026-11-03T08:20:00+08:00",
        "location": {"place_id": "home", "name": "家"},
        "ambient": ambient,
    }


def _request(world):
    return SimpleNamespace(
        opportunity_id="opp:0123456789abcdef",
        snapshot_fingerprint="fp:fixture",
        candidate_set_id="cset:0123456789abcdef",
        candidates=(),
        legal_decision_enums=("NO_ACTION",),
        current_activity_summary={},
        capacity_summary={},
        observed_fact_keys=[],
        prior_experiences=[],
        policy_version="ag0.candidate_policy.v1",
        allowed_reason_codes=["NO_ACTION_CHOSEN"],
        situation_context={"world": world},
    )


def _adapter_invoke(world):
    """正式 adapter + 可控脚本 client；prompt 由 adapter 自己构造，不是手拼。"""

    from agency_cognition_adapter import ProductionAgencyCognitionAdapter

    transport = g1._ScriptedDecisionTransport(["NO_ACTION"], capture_prompts=True)
    adapter = ProductionAgencyCognitionAdapter(client=transport, timeout_s=1.0,
                                               max_output_tokens=128)
    adapter.invoke(_request(world))
    return transport


class ClientBoundaryTest(unittest.TestCase):
    def test_ambient_id_and_public_summary_reach_the_client_verbatim(self):
        transport = _adapter_invoke(_ambient_world(events=[
            {"event_id": EVENT_ID, "summary": EVENT_SUMMARY,
             "location_id": "home", "local_date": "2026-11-03"},
            {"event_id": EVENT_ID_2, "summary": EVENT_SUMMARY_2,
             "location_id": "street_1", "local_date": "2026-11-03"},
        ]))
        view = g1._d_ambient_client_view(transport, ambient_env="1")
        self.assertEqual(view["requests"], 1)
        self.assertEqual(view["status"], "available")
        self.assertTrue(view["ambient_key_present"])
        self.assertEqual(view["event_count"], 2)
        self.assertEqual(sorted(view["event_ids"]), sorted([EVENT_ID, EVENT_ID_2]))
        self.assertIs(view["ids_exact_in_input"], True)
        self.assertIs(view["summaries_exact_in_input"], True)
        # 报告里只有布尔/ID/计数，没有 prompt 正文或公开 summary 原文。
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(EVENT_SUMMARY, blob)
        self.assertNotIn("situation_context", blob)

    def test_no_ambient_key_is_reported_as_absent_not_as_a_zero_event_match(self):
        # 明确关掉背景键：旧形状不应凭空出现 ambient。
        world = _ambient_world(events=[])
        world.pop("ambient")
        transport = _adapter_invoke(world)
        view = g1._d_ambient_client_view(transport, ambient_env="1")
        self.assertIs(view["ambient_key_present"], None)
        self.assertEqual(view["status"], "AMBIENT_ABSENT")
        self.assertEqual(view["event_count"], 0)
        self.assertIsNone(view["ids_exact_in_input"])
        self.assertIsNone(view["summaries_exact_in_input"])

    def test_an_available_read_with_zero_events_is_an_honest_zero(self):
        transport = _adapter_invoke(_ambient_world(events=[]))
        view = g1._d_ambient_client_view(transport, ambient_env="1")
        self.assertTrue(view["ambient_key_present"])
        self.assertEqual(view["status"], "available")
        self.assertEqual(view["event_count"], 0)
        self.assertEqual(view["event_ids"], [])
        self.assertIsNone(view["ids_exact_in_input"])
        self.assertIsNone(view["summaries_exact_in_input"])

    def test_an_unavailable_read_keeps_its_reason_code_and_no_events(self):
        transport = _adapter_invoke(
            _ambient_world(status="unavailable", reason="AMBIENT_READ_FAILED"))
        view = g1._d_ambient_client_view(transport, ambient_env="1")
        self.assertTrue(view["ambient_key_present"])
        self.assertEqual(view["status"], "unavailable")
        self.assertEqual(view["reason_code"], "AMBIENT_READ_FAILED")
        self.assertEqual(view["event_count"], 0)

    def test_no_captured_request_is_not_reported_as_success(self):
        transport = g1._ScriptedDecisionTransport([], capture_prompts=True)
        view = g1._d_ambient_client_view(transport, ambient_env="1")
        self.assertEqual(view["status"], "NO_REQUEST")
        self.assertEqual(view["requests"], 0)
        self.assertIsNone(view["ambient_key_present"])


class ClosedProfileTest(unittest.TestCase):
    def test_d_identity_and_window_are_closed(self):
        profile = g1.V06_D_PROFILE
        self.assertEqual(profile.key, "v06-natural-d")
        self.assertEqual(str(profile.root), "/tmp/nyairo-v06-natural-20261103-d")
        self.assertEqual(str(profile.authority_dir),
                         "/tmp/nyairo-v06-natural-20261103-d-memory-authority")
        self.assertEqual(profile.run_id, "run:v06-world-natural-20261103-d")
        self.assertEqual(profile.domain_id, "v06:world-natural:20261103-d")
        self.assertEqual(profile.expected_history, 212)
        self.assertEqual(profile.latest_start.isoformat(), "2026-11-04T00:15:00+08:00")
        self.assertEqual(profile.deadline.isoformat(), "2026-11-04T00:50:00+08:00")
        self.assertEqual(g1._profile_window_seconds(profile), 1800)
        self.assertEqual(g1._profile_max_ticks(profile), 900)
        self.assertEqual(g1._profile_trace_max_records(profile), 4000)
        self.assertEqual(g1._world_intent_gate(profile), "off")
        self.assertTrue(g1._is_natural(profile))

    def test_old_batches_keep_their_closed_values(self):
        self.assertEqual(g1.B_PROFILE.expected_history, 191)
        self.assertEqual(g1.B_PROFILE.deadline.isoformat(), "2026-11-02T20:00:00+08:00")
        self.assertEqual(g1.C_PROFILE.expected_history, 192)
        self.assertEqual(g1.C_PROFILE.deadline.isoformat(), "2026-11-02T23:05:00+08:00")
        self.assertEqual(g1._profile_window_seconds(g1.C_PROFILE), 10800)
        self.assertEqual(g1._profile_max_ticks(g1.C_PROFILE), 3000)
        self.assertEqual(g1._profile_trace_max_records(g1.C_PROFILE), 6000)
        self.assertEqual(g1._world_intent_gate(g1.D_PROFILE), "off")
        self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES["a"]))
        self.assertEqual(g1.NATURAL_PROFILES, frozenset({"d", "e"}))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertNotIn("v06-natural-d", g1.CLI_BATCH_CHOICES)

    def test_the_ambient_switch_is_d_only(self):
        self.assertEqual(g1._ambient_events_env(g1.V06_D_PROFILE), "1")
        for profile in (g1.B_PROFILE, g1.C_PROFILE, g1.E_PROFILE, g1.D_PROFILE,
                        g1.BATCH_PROFILES["a"], g1.F_PROFILE):
            self.assertIsNone(g1._ambient_events_env(profile), profile.key)

    def test_offline_reference_stays_before_the_latest_start(self):
        reference = g1.offline_reference(g1.V06_D_PROFILE)
        self.assertLess(reference, g1.V06_D_PROFILE.latest_start)
        self.assertLess(g1.V06_D_PROFILE.latest_start, g1.V06_D_PROFILE.deadline)
        self.assertGreaterEqual(g1._profile_max_ticks(g1.V06_D_PROFILE)
                                * g1.TICK_INTERVAL_SECONDS, 1800)

    def test_the_accounting_ceiling_is_local_and_never_trips_for_old_batches(self):
        self.assertEqual(g1.V06_D_TOTAL_ACCOUNTING_CAP, 1000)
        self.assertEqual(g1.UTC_DAY_CAP, 300)
        self.assertLessEqual(g1.V06_D_PROFILE.expected_history + g1.REQUEST_CAP, 1000)
        self.assertFalse(g1._total_accounting_exceeded(787, profile=g1.V06_D_PROFILE))
        self.assertTrue(g1._total_accounting_exceeded(788, profile=g1.V06_D_PROFILE))
        self.assertFalse(g1._total_accounting_exceeded(788, profile=g1.C_PROFILE))

    def test_d_cleanup_is_window_plus_sixty_seconds(self):
        now = 1000.0
        limit = g1._natural_phase_timeout(g1.V06_D_PROFILE, now + 1800, now=now)
        self.assertAlmostEqual(limit, 1800 + 60.0, places=6)
        old = g1._natural_phase_timeout(g1.E_PROFILE, now + g1.WINDOW_SECONDS, now=now)
        self.assertAlmostEqual(old, g1.WINDOW_SECONDS + 120.0, places=6)


class GateOffEvidenceTest(unittest.TestCase):
    """拒绝证据必须来自正式 attempt，不是「有 action 引用」就算过。"""

    def _view(self, **overrides):
        view = {
            "actions": [{"action_id": "act:0123456789abcdef", "status": "PREPARED"}],
            "attempts": {"att:1": {"action_id": "act:0123456789abcdef",
                                   "status": "PRE_SUBMIT_FAILED",
                                   "error_class": "GATE_OFF"}},
            "receipts": {},
            "lifecycle": [],
            "tickets": [],
            "world_before": {"world_state_present": True, "place_id": "home",
                             "area_id": "bedroom", "body_present": True,
                             "pose": "standing", "orientation": "south"},
            "world_after": {"world_state_present": True, "place_id": "home",
                            "area_id": "bedroom", "body_present": True,
                            "pose": "standing", "orientation": "south"},
        }
        view.update(overrides)
        return view

    def test_a_plain_action_reference_is_not_a_refusal(self):
        view = self._view(attempts={})
        self.assertFalse(d._gate_off_refusal(view)["qualifying"])

    def test_the_full_pre_submit_refusal_shape_qualifies(self):
        evidence = d._gate_off_refusal(self._view())
        self.assertTrue(evidence["qualifying"], evidence)
        self.assertEqual(evidence["attempt_statuses"], ["PRE_SUBMIT_FAILED"])
        self.assertEqual(evidence["attempt_error_classes"], ["GATE_OFF"])

    def test_a_receipt_or_a_consumed_ticket_breaks_the_qualification(self):
        receipt = {"act:0123456789abcdef": {"action_id": "act:0123456789abcdef"}}
        self.assertFalse(d._gate_off_refusal(self._view(receipts=receipt))["qualifying"])
        ticket = [{"execution_id": "exec:1", "state": "CONSUMED"}]
        self.assertFalse(d._gate_off_refusal(self._view(tickets=ticket))["qualifying"])

    def test_a_body_or_position_change_breaks_the_qualification(self):
        moved = self._view()
        moved["world_after"] = dict(moved["world_after"], pose="lying")
        self.assertFalse(d._gate_off_refusal(moved)["qualifying"])
        shifted = self._view()
        shifted["world_after"] = dict(shifted["world_after"], place_id="street_1")
        self.assertFalse(d._gate_off_refusal(shifted)["qualifying"])


class EndpointPreflightTest(unittest.TestCase):
    def test_a_real_endpoint_is_required_and_the_placeholder_is_refused(self):
        self.assertEqual(d._endpoint_refusal({}).get("reason_code"),
                         g1.REASON_NO_MODEL_CONFIG)
        self.assertEqual(
            d._endpoint_refusal({"NYA02_MODEL_BASE_URL": "https://gw.example.invalid/v1"})
            .get("reason_code"), d.REASON_ENDPOINT_PLACEHOLDER)
        self.assertEqual(
            d._endpoint_refusal({"NYA02_MODEL_BASE_URL": "ftp://example.org/v1"})
            .get("reason_code"), d.REASON_ENDPOINT_SCHEME)
        self.assertEqual(
            d._endpoint_refusal(
                {"NYA02_MODEL_BASE_URL": "https://user:pw@example.org/v1"})
            .get("reason_code"), d.REASON_ENDPOINT_CREDENTIALS)
        self.assertIsNone(
            d._endpoint_refusal({"NYA02_MODEL_BASE_URL": "https://model.example.org/v1"}))


class LedgerScopeTest(unittest.TestCase):
    class _Stub:
        def __init__(self, state):
            self.state = state

        def view(self):
            return {"state": self.state, "run_window_calls": 7}

    def test_corrupt_ledger_fails_closed_for_d_but_not_for_old_batches(self):
        with g1._use_batch(g1.V06_D_PROFILE.key):
            self.assertIsNone(g1._ledger_calls(self._Stub("corrupt")))
            self.assertIsNone(g1._ledger_calls(self._Stub("CORRUPT")))
            self.assertIsNone(g1._ledger_calls(self._Stub("UNREADABLE")))
        with g1._use_batch(g1.E_PROFILE.key):
            self.assertEqual(g1._ledger_calls(self._Stub("corrupt")), 7)
            self.assertIsNone(g1._ledger_calls(self._Stub("CORRUPT")))
            self.assertEqual(g1._ledger_calls(self._Stub("UNREADABLE")), 7)

    def test_total_accounting_stop_reason_is_named(self):
        self.assertEqual(g1.REASON_TOTAL_ACCOUNTING_CAP, "TOTAL_ACCOUNTING_CAP_REACHED")
        self.assertEqual(g1.REASON_AMBIENT_ENV_MISMATCH, "AMBIENT_ENV_MISMATCH")


if __name__ == "__main__":
    unittest.main()
