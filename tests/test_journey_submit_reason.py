"""旅程提交口的拒因解层：读正式回执里的 canonical ``reason``，不误取 ``outcome``。

计时旅程的每一段都经 Intent Executor → gateway.sock → Resolver。Resolver 已经把
owner 的 legacy 拒因（如 ``LOCATION_CLOSED``）归一成 canonical 码（``NOT_ALLOWED``），
gateway 只转发 canonical。executor 结果里 ``reason`` 就是这个 canonical 码，
``outcome`` 只是闭环词表里的 ``FAILURE``/``TIMEOUT`` 等传输结论。

本模块只验证 ``_as_owner_status`` 的翻译，不新增回执字段、不改 gateway/client 协议。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "components" / "world" / "service" / "bridge"
for _entry in (str(ROOT), str(BRIDGE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import action_intent as ai  # noqa: E402
import journey_submit_port as port_module  # noqa: E402


def _map(result: dict) -> dict:
    return port_module.FormalOwnerMoveSubmitPort._as_owner_status(ai, result)


class OwnerStatusReasonTest(unittest.TestCase):
    def test_rejected_reads_canonical_reason_not_generic_outcome(self) -> None:
        # 真实 executor 形状：outcome 是 FAILURE，reason 是 Resolver 的 canonical 码。
        mapped = _map({
            "intent_status": ai.STATUS_REJECTED,
            "outcome": "FAILURE",
            "reason": "NOT_ALLOWED",
            "detail": "location closed",
            "execution_id": "exec-1",
        })
        self.assertEqual(mapped["status"], "rejected")
        self.assertEqual(mapped["reason_code"], "NOT_ALLOWED")

    def test_rejected_keeps_explicit_gate_reason(self) -> None:
        # gate off 的拒因必须保持原样：submissions 计数依赖这个值。
        mapped = _map({
            "intent_status": ai.STATUS_REJECTED,
            "outcome": "GATE_OFF",
            "reason": "GATE_OFF",
        })
        self.assertEqual(mapped["status"], "rejected")
        self.assertEqual(mapped["reason_code"], "GATE_OFF")

    def test_rejected_without_reason_falls_back_conservatively(self) -> None:
        # 缺失 reason 时保留原来的保守回落；不凭空造 canonical 码。
        mapped = _map({
            "intent_status": ai.STATUS_REJECTED,
            "outcome": "FAILURE",
        })
        self.assertEqual(mapped["status"], "rejected")
        self.assertEqual(mapped["reason_code"], "FAILURE")

    def test_applied_is_not_reported_as_rejected(self) -> None:
        mapped = _map({
            "intent_status": ai.STATUS_APPLIED,
            "outcome": "SUCCESS",
            "reason": None,
            "execution_id": "exec-2",
        })
        self.assertEqual(mapped["status"], "applied")
        self.assertNotEqual(mapped["status"], "rejected")

    def test_unknown_timeout_stays_uncertain(self) -> None:
        mapped = _map({
            "intent_status": ai.STATUS_UNKNOWN,
            "outcome": "TIMEOUT",
            "reason": "transport_fault:TIMEOUT",
        })
        self.assertEqual(mapped["status"], "uncertain")
        self.assertEqual(mapped["reason_code"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
