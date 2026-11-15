#!/usr/bin/env python3
"""19 章 §一.5：处境指纹的身体档（``_situation_parts`` -> ``body_parts``）定向证据。

覆盖：

1. 本轮没读过身体（``last_body_context`` 缺失/为空）-> 整档 UNKNOWN 安全降级：
   ``available: False``、``reason_code: BODY_NOT_READ``，能力档/freshness/会话相位
   全部写 unknown/UNKNOWN，**不猜值**（没有 0、没有 "fine"、没有 healthy）。
2. 有一轮真实的 body 投影（``life_body_context.build``，源头是
   ``body_capacity_source.read_body_capacity`` 的 body 列）-> 档里带 owner 自己的
   能力档（LIMITED）、freshness、恢复会话相位与休息判定，全部有界、零自由文本。
3. ``_situation_parts`` 的 parts 里真的含 ``body`` 键，且 UNKNOWN 时优雅降级——
   parts 仍是一个可 JSON 化、可哈希拼装的有界字典（与 ``contact`` 档同规约）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "components" / "life" / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import autonomous_life_cycle as alc  # noqa: E402
import life_body_context as lbc  # noqa: E402
import life_step_readiness as lsr  # noqa: E402

_LIFE_ERROR: str | None = None
try:  # POSIX only（fcntl）；Windows 上跳过。
    import fcntl  # noqa: F401
except ImportError:
    _LIFE_ERROR = "fcntl unavailable"

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX（fcntl）：%s" % (_LIFE_ERROR or ""))

READINESS = lsr.StepReadiness(available=False, reason_code="STAY_CHECKPOINT_STEP",
                              activity_ref="activity:stay-at-home",
                              target_ref="stay:home", source_revision="rev-9")


class _FakeCycle:
    """只带 ``_situation_parts`` 会碰到的属性的最小替身（同 GateWiringTest 模式）。

    ``contact_parts`` / ``body_parts`` / ``open_resume_refs`` 直接借产品的真实实现，
    保证被测路径不是替身里的第二份编码。
    """

    contact_parts = alc.AutonomousLifeCycle.contact_parts
    body_parts = alc.AutonomousLifeCycle.body_parts
    open_resume_refs = alc.AutonomousLifeCycle.open_resume_refs
    _stay_plan_from_activity = staticmethod(lambda activity: None)

    def __init__(self, *, body_context=None) -> None:
        self._last_foreground_record = None
        self.private_material_situation = lambda *a, **k: None
        self.last_world_observation = {}
        self.last_world_opportunities = []
        self._round_future_items = []
        self._round_project_options = []
        self._round_reserved_options = []
        self.day_plan = None
        self.research = SimpleNamespace(fingerprint_parts=lambda: {})
        self.practice = SimpleNamespace(fingerprint_parts=lambda: {})
        self.social_contacts = None
        self.last_error = None
        self.runtime = SimpleNamespace(
            eligibility_store=SimpleNamespace(load_state=lambda: {}))
        self.last_body_context = body_context


def _fake_cycle(*, body_context=None) -> _FakeCycle:
    return _FakeCycle(body_context=body_context)


@needs_life
class BodyPartsUnknownSafetyTest(unittest.TestCase):
    """没读就是 unknown：档内没有任何被猜出来的值。"""

    def test_no_body_read_degrades_to_named_unknowns(self) -> None:
        for missing in (None, {}, "not-a-mapping"):
            with self.subTest(last_body_context=missing):
                fake = _fake_cycle(body_context=missing)
                part = alc.AutonomousLifeCycle.body_parts(fake)
                self.assertFalse(part["available"])
                self.assertEqual(part["reason_code"], "BODY_NOT_READ")
                self.assertEqual(part["capacity"], lbc.CAPACITY_UNKNOWN)
                self.assertEqual(part["freshness"], "unknown")
                self.assertEqual(part["recovery_session"], lbc.RECOVERY_SESSION_UNKNOWN)
                self.assertIsNone(part["rest_can_start"])
                # 降级档里没有第二个来源能借来的"健康"词。
                blob = json.dumps(part, ensure_ascii=False)
                for forbidden in ("AVAILABLE", "LIMITED", "OPEN", "true"):
                    self.assertNotIn('"%s"' % forbidden if forbidden != "true" else '"available": true',
                                     blob, part)

    def test_broken_capacity_band_is_never_passed_through(self) -> None:
        # body 列带了越界的能力词 -> 一律压回 UNKNOWN（owner 的词才是词）。
        fake = _fake_cycle(body_context={
            "capacity": "TOTALLY_FINE_TRUST_ME", "freshness": "stale",
            "recovery": {"session": "WHATEVER"}, "rest": {"can_start": True},
        })
        part = alc.AutonomousLifeCycle.body_parts(fake)
        self.assertEqual(part["capacity"], lbc.CAPACITY_UNKNOWN)


@needs_life
class BodyPartsFromRealProjectionTest(unittest.TestCase):
    """有一轮真实 body 投影时，档里带 owner 自己的词（有界、零文本）。"""

    def test_limited_body_with_open_session_is_carried_bounded(self) -> None:
        column = {
            "state": lbc.CAPACITY_LIMITED,
            "ref": "body_capacity:v1#b1",
            "source": "body_capacity_source",
            "reason_code": "OK_LIMITED_BY_BODY_SIGNAL",
            "freshness": "fresh",
            "observed_body_id": "body:main",
            "observed_revision": "rev-12",
            "evidence": [{"type": "fatigue", "band": "noticeable"}],
            "identity_bound": True, "revision_bound": True, "observation_time_bound": True,
        }
        context = lbc.build(body_column=column)
        fake = _fake_cycle(body_context=context)
        part = alc.AutonomousLifeCycle.body_parts(fake)
        self.assertTrue(part["available"])
        self.assertEqual(part["capacity"], lbc.CAPACITY_LIMITED)
        self.assertEqual(part["freshness"], "fresh")
        self.assertEqual(part["recovery_session"], lbc.RECOVERY_SESSION_UNKNOWN)
        # 有界：整个档 JSON 化后仍是一小段（不得夹带私人文本或原始数值）。
        blob = json.dumps(part, ensure_ascii=False)
        self.assertLessEqual(len(blob), 400)
        self.assertNotIn("noticeable", blob)  # 信号带不进指纹档

    def test_known_capacity_with_session_and_rest_verdict(self) -> None:
        column = {
            "state": "AVAILABLE",
            "reason_code": "OK_BODY_OBSERVED",
            "freshness": "fresh",
            "evidence": [],
            "identity_bound": True, "revision_bound": True, "observation_time_bound": True,
        }
        context = lbc.build(
            body_column=column,
            # REST 判定用 owner 自己的词形（start_available + current.*）。
            rest={"start_available": True, "stop_available": True,
                  "current": {"recovery_state": "active", "pose": "lying"}},
            recovery={"state": "active", "progress_band": "early", "trend": "steady"},
        )
        fake = _fake_cycle(body_context=context)
        part = alc.AutonomousLifeCycle.body_parts(fake)
        self.assertEqual(part["capacity"], "AVAILABLE")
        self.assertEqual(part["recovery_session"], lbc.RECOVERY_SESSION_OPEN)
        self.assertIs(part["rest_can_start"], True)


@needs_life
class SituationPartsIncludeBodyTest(unittest.TestCase):
    """``_situation_parts`` 的 parts 里真的含 body 键，且 UNKNOWN 时优雅降级。"""

    def test_parts_carry_body_and_degrade_gracefully_when_unknown(self) -> None:
        fake = _fake_cycle(body_context=None)  # 本轮没读过身体
        parts = alc.AutonomousLifeCycle._situation_parts(fake, material=None,
                                                         readiness=READINESS)
        self.assertIn("body", parts)
        self.assertEqual(parts["body"]["reason_code"], "BODY_NOT_READ")
        self.assertEqual(parts["body"]["capacity"], lbc.CAPACITY_UNKNOWN)
        # 整个 parts 仍是有界、可 JSON 化的 owner 事实字典（不抛错即通过）。
        blob = json.dumps(parts, ensure_ascii=False, sort_keys=True)
        self.assertIn('"body"', blob)
        self.assertLessEqual(len(blob), 8000)

    def test_parts_body_changes_with_a_new_owner_read(self) -> None:
        column = {"state": lbc.CAPACITY_LIMITED, "reason_code": "OK_LIMITED_BY_BODY_SIGNAL",
                  "freshness": "fresh", "evidence": [{"type": "fatigue", "band": "noticeable"}],
                  "identity_bound": True, "revision_bound": True, "observation_time_bound": True}
        fresh = _fake_cycle(body_context=lbc.build(body_column=column))
        parts = alc.AutonomousLifeCycle._situation_parts(fresh, material=None,
                                                         readiness=READINESS)
        self.assertEqual(parts["body"]["capacity"], lbc.CAPACITY_LIMITED)
        self.assertNotEqual(json.dumps(parts["body"], sort_keys=True),
                            json.dumps({"available": False, "reason_code": "BODY_NOT_READ",
                                        "capacity": lbc.CAPACITY_UNKNOWN,
                                        "freshness": "unknown",
                                        "recovery_session": lbc.RECOVERY_SESSION_UNKNOWN,
                                        "rest_can_start": None}, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
