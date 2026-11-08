#!/usr/bin/env python3
"""reconsideration 门禁：同一情形连续被拦 N 次后强制放行一轮。

本模块只守这一件事，四条：

1. 同一指纹连续被拦到 ``MAX_CONSECUTIVE_SAME_STATE_BLOCKS`` 次之后，下一次**放行**，
   且放行的 reason_code 与「ttl 自然到期」放行（``STATE_NOT_CONSIDERED_YET``）不同名，
   原来的拦截结论以 ``suppressed_reason_code`` 保留，可审计。
2. 放行之后计数真的清零：放行只买一次，之后仍要再被拦 N 次才会放行第二次，
   不会退化成「每轮都问」。
3. 指纹变了（换位置 / 换姿势 / 换 revision）各自独立计数，一个指纹的放行不会顶掉另一个。
4. 这条逻辑真的接在回合门禁上（``AutonomousLifeCycle.reconsideration_block`` 走它），
   不是一段没人调用的死代码。

只用真账本（``lsr.ReconsiderationLedger``，temp 目录里的真文件），不起 Supply、
不跑 owner、不花任何模型请求；平台无关，Windows/WSL 都能跑。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "alpha"), str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as alc  # noqa: E402
import life_step_readiness as lsr  # noqa: E402

#: 一个固定的业务时刻；所有 `at` 都由它偏移，避免用例依赖真实墙钟。
BASE_AT = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
#: 账本自己的自然到期放行名，用来和本工单的强制放行名对照。
REASON_TTL_RELEASE = "STATE_NOT_CONSIDERED_YET"
REASON_BLOCKED = "SAME_STATE_ALREADY_CONSIDERED"

#: 这些用例都以「连续 N 次」为界，直接读模块常量，改默认值不会让断言说谎。
N = alc.MAX_CONSECUTIVE_SAME_STATE_BLOCKS


def _at(minutes: float) -> str:
    return (BASE_AT + timedelta(minutes=minutes)).isoformat()


def situation(**overrides) -> dict:
    """一个完整情形（键取自 ``lsr.SITUATION_KEYS``），可按需改一个部分。"""

    parts = {
        "foreground_activity_ref": "activity:stay-at-home",
        "foreground_status": "ACTIVE",
        "foreground_kind": "STAY_AT_OBSERVED_PLACE",
        "target_ref": "stay:home",
        "target_revision": "rev-9",
        "step_reason": "STAY_CHECKPOINT_STEP",
        "place": "home",
        "pose": "seated",
        "opportunities": ["wopp:stay:home:r9:aa"],
    }
    parts.update(overrides)
    return parts


def _declined(ledger: lsr.ReconsiderationLedger, parts: dict, *, at: str,
              outcome: str = alc.OUTCOME_NO_ACTION) -> None:
    """一轮真的被问过、模型答了 no_action（或被拦的既有记录）。"""

    ledger.record_situation(parts=parts, outcome=outcome, at=at)


class ForcedReleaseTest(unittest.TestCase):
    """连续拦截到 N 次就放行一次，且放行原因与 ttl 到期可区分。"""

    def test_the_nth_consecutive_block_lets_the_next_round_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = lsr.ReconsiderationLedger(Path(tmp))
            _declined(ledger, situation(), at=_at(0))

            for index in range(1, N + 1):
                view = alc.reconsideration_gate(ledger, parts=situation(), at=_at(index * 10))
                self.assertTrue(view["blocked"], view)
                self.assertFalse(view["forced_release"], view)
                self.assertEqual(view["consecutive_blocks"], index, view)
                self.assertEqual(view["reason_code"], REASON_BLOCKED, view)

            forced = alc.reconsideration_gate(ledger, parts=situation(), at=_at((N + 1) * 10))
            self.assertFalse(forced["blocked"], forced)
            self.assertTrue(forced["forced_release"], forced)
            self.assertEqual(forced["reason_code"], alc.REASON_FORCED_RELEASE)
            self.assertEqual(forced["consecutive_blocks"], N, forced)
            # 被放行不等于撤销判断：原来的拦截结论按名留着，报告里能看出这是「本该被拦」。
            self.assertEqual(forced["suppressed_reason_code"], REASON_BLOCKED, forced)

    def test_the_forced_reason_is_not_the_ttl_reason(self):
        self.assertNotEqual(alc.REASON_FORCED_RELEASE, REASON_TTL_RELEASE)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = lsr.ReconsiderationLedger(Path(tmp))
            expired_parts = situation(place="garden", target_revision="rev-4")
            _declined(ledger, expired_parts, at=_at(0))

            # 越过账本自己的 ttl（默认 6 逻辑小时）：这是「自然到期」放行，不是本工单的强制放行。
            ttl_view = alc.reconsideration_gate(
                ledger, parts=expired_parts,
                at=_at((ledger.ttl_seconds + 60) / 60.0))
            self.assertFalse(ttl_view["blocked"], ttl_view)
            self.assertEqual(ttl_view["reason_code"], REASON_TTL_RELEASE, ttl_view)
            self.assertFalse(ttl_view["forced_release"], ttl_view)
            self.assertNotEqual(ttl_view["reason_code"], alc.REASON_FORCED_RELEASE)


class ReleaseClearsTheCountTest(unittest.TestCase):
    """放行后计数清零：一次放行只买一次机会，不会变成每轮都问。"""

    def test_a_release_drops_the_row_and_the_streak_starts_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = lsr.ReconsiderationLedger(Path(tmp))
            _declined(ledger, situation(), at=_at(0))

            for index in range(1, N + 1):
                alc.reconsideration_gate(ledger, parts=situation(), at=_at(index * 10))
            forced = alc.reconsideration_gate(ledger, parts=situation(), at=_at((N + 1) * 10))
            self.assertTrue(forced["forced_release"], forced)
            key = str(forced["fingerprint"])

            # 计数清零：这一条已经从台账里撤掉，下一轮是「没记过」的状态。
            self.assertEqual([row for row in ledger.situations()
                              if str(row.get("fingerprint")) == key], [], ledger.situations())
            self.assertEqual(forced["released"], [key], forced)
            again = alc.reconsideration_gate(ledger, parts=situation(), at=_at((N + 1) * 10 + 1))
            self.assertFalse(again["blocked"], again)
            self.assertEqual(again["reason_code"], REASON_TTL_RELEASE, again)

            # 被放行的这一轮真的问了模型、仍然 no_action：从 1 重新累计。
            _declined(ledger, situation(), at=_at((N + 2) * 10))
            fresh = [row for row in ledger.situations() if str(row.get("fingerprint")) == key]
            self.assertEqual(len(fresh), 1, fresh)
            self.assertEqual(int(fresh[0]["count"]), 1, fresh)

            for index in range(1, N + 1):
                view = alc.reconsideration_gate(ledger, parts=situation(), at=_at((N + 3 + index) * 10))
                self.assertTrue(view["blocked"], view)
                self.assertFalse(view["forced_release"], view)
                self.assertEqual(view["consecutive_blocks"], index, view)
            second = alc.reconsideration_gate(ledger, parts=situation(), at=_at((2 * N + 4) * 10))
            self.assertTrue(second["forced_release"], second)


class IndependentFingerprintsTest(unittest.TestCase):
    """指纹不同就各自计数，一个的放行不顶掉另一个。"""

    def test_two_situations_count_separately(self):
        home = situation()
        garden = situation(place="garden", pose="standing", target_revision="rev-7",
                           opportunities=["wopp:walk:garden:r7:bb"])
        self.assertNotEqual(lsr.situation_fingerprint(home), lsr.situation_fingerprint(garden))

        with tempfile.TemporaryDirectory() as tmp:
            ledger = lsr.ReconsiderationLedger(Path(tmp))
            _declined(ledger, home, at=_at(0))
            _declined(ledger, garden, at=_at(0))

            # 交替拦截：两边的连续次数各自累加，互不相干（garden 少拦一次）。
            for index in range(1, N + 1):
                home_view = alc.reconsideration_gate(ledger, parts=home, at=_at(index * 10))
                self.assertTrue(home_view["blocked"], home_view)
                self.assertEqual(home_view["consecutive_blocks"], index, home_view)
                if index < N:
                    garden_view = alc.reconsideration_gate(ledger, parts=garden,
                                                           at=_at(index * 10 + 1))
                    self.assertTrue(garden_view["blocked"], garden_view)
                    self.assertEqual(garden_view["consecutive_blocks"], index, garden_view)

            home_forced = alc.reconsideration_gate(ledger, parts=home, at=_at((N + 1) * 10))
            self.assertTrue(home_forced["forced_release"], home_forced)
            # home 的放行没有波及 garden：它仍被拦，且自己的计数接着往下走。
            garden_after = alc.reconsideration_gate(ledger, parts=garden, at=_at((N + 1) * 10 + 1))
            self.assertTrue(garden_after["blocked"], garden_after)
            self.assertFalse(garden_after["forced_release"], garden_after)
            self.assertEqual(garden_after["consecutive_blocks"], N, garden_after)

            # 换姿势就是换情形：home 的放行也不让「home/seated」这个旧情形复活成新问题。
            moved = situation(pose="standing")
            self.assertNotEqual(lsr.situation_fingerprint(moved), lsr.situation_fingerprint(home))
            moved_view = alc.reconsideration_gate(ledger, parts=moved, at=_at((N + 1) * 10 + 2))
            self.assertFalse(moved_view["blocked"], moved_view)
            self.assertEqual(moved_view["reason_code"], REASON_TTL_RELEASE, moved_view)


class GateWiringTest(unittest.TestCase):
    """回合门禁真的走这条计数逻辑（不然就是死代码）。"""

    def test_the_cycle_gate_counts_suppressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = lsr.ReconsiderationLedger(Path(tmp))
            _declined(ledger, situation(), at=_at(0))
            readiness = lsr.StepReadiness(available=False, reason_code="STAY_CHECKPOINT_STEP",
                                          activity_ref="activity:stay-at-home",
                                          target_ref="stay:home", source_revision="rev-9")
            fake = SimpleNamespace(reconsideration=ledger, _round_situation=None,
                                  last_reconsideration=None,
                                  _situation_parts=lambda **kwargs: situation())

            views = [alc.AutonomousLifeCycle.reconsideration_block(
                fake, material={}, readiness=readiness, observed_at=_at(index * 10))
                for index in range(1, N + 2)]

            self.assertEqual([bool(view["blocked"]) for view in views],
                             [True] * N + [False], views)
            self.assertEqual(str(fake.last_reconsideration["reason_code"]),
                             alc.REASON_FORCED_RELEASE)
            self.assertTrue(fake.last_reconsideration["forced_release"])
            self.assertEqual(dict(fake._round_situation), situation())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
