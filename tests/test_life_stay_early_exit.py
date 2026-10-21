#!/usr/bin/env python3
"""停留的**提前结束**：AG-1 合法 Decision → AG-2 既有状态命令，状态必须准确。

本文件只补一类合同（真实 Activity、真实 owner、同一个实例、同一个注入时钟）：

1. **提前结束是它能真的发生的一条路**。她在公园坐着（``STAY_AT_OBSERVED_PLACE``，声明 300s），
   坐了一轮（``ONGOING``，未到点不许完成）之后**改主意**：模型给出合法的 ``ABANDON``（AG-1 在
   当前 Activity 上允许它）→ AG-2 用它既有的 LR-2 状态命令结束这个 Activity。结果由 owner 自己的
   记录说：``status=ABANDONED``、``ended_at``、``terminal_reason_code=ABANDONED_BY_DECISION``；
   Life 侧只写真实的起止、``completed: false``、剩余秒数，**不发**任何 ``world_stay_ref`` 完成值，
   也不篡改 elapsed 让提前结束看起来像待满。结束后前台真的空出来，下一段 MOVE 合法。

2. **不合法的退出必须被拒**。同样在停留里，模型把 ``ABANDON`` 投给一个只允许 ``DEFER/START`` 的
   候选：AG-1/AG-2 拒绝，Activity 仍是 ``ACTIVE``、前台还是它，没有任何"已经结束"的事实被写出来。

3. **场所关着就不许再造"她坐在那儿"**。时间早就过了声明的 300s，但 owner 的事实说这家店此刻
   ``CLOSED``：这一轮的停留 checkpoint 被安全边界拒绝（``WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT``），
   ``progress_state`` 不是 ``COMPLETION_ELIGIBLE``，Activity 保持 ``ACTIVE``、前台不释放——
   随后那一轮才是真正可行的出口（合法的 ``ABANDON``），而它的记录仍然写着 ``completed: false``：
   时间过去了 ≠ 待满了。

不继承任何既有 TestCase；夹具以**组合**方式复用
``tests/test_world_neighborhood_life.py`` 的 ``NeighborhoodFixture``（同一个真实 runtime / World
服务 / 注入时钟）。所有 home 落在 ``/tmp/nyaw05r.jdK3q/nb-life-*``（runner 把 ``shutil.rmtree``
置 no-op），保留不删。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402
import test_world_neighborhood_life as nb  # noqa: E402

TO_PARK = (("go", "street_front"), ("go", "street_corner"), ("go", "park"), ("stay", None))
TO_CAFE = (("go", "street_front"), ("go", "street_corner"), ("go", "cafe"), ("stay", None))


class AbandonNowCognition(nb.ScriptedNeighborhoodCognition):
    """The same stand-in, with one intention: leave the stay it is in *now*.

    ``ABANDON`` is only ever offered for a candidate whose own legal list contains it, so this
    still goes through AG-1's legality check -- it is a Decision, not a shortcut.
    """

    def _choose(self, candidates: list[dict], facts: dict, here: str) -> tuple:
        running = next((candidate for candidate in candidates
                        if "CONTINUE" in self._legal(candidate)), None)
        if running is not None and "ABANDON" in self._legal(running):
            return (running, "ABANDON", "改主意了：这一次不坐了")
        return super()._choose(candidates, facts, here)


class IllegalAbandonCognition(nb.ScriptedNeighborhoodCognition):
    """An *illegal* exit: ``ABANDON`` attached to a candidate that may not be abandoned.

    The candidate is picked from this round's **real** AG-1 offer, deterministically: its own
    ``legal_decisions`` must not contain ``ABANDON`` (so the Decision is illegal for *that*
    candidate) and must not contain ``CONTINUE`` (that is the running stay's own candidate).
    A ``DEFER``-only candidate qualifies, and so does one that may ``START``.  If this round
    offers no such candidate the stand-in raises with the whole offer printed -- it never falls
    back to an ``ABANDON`` that AG-1 would accept, because a legal abandon proves nothing.
    """

    def _choose(self, candidates: list[dict], facts: dict, here: str) -> tuple:
        for candidate in candidates:
            legal = self._legal(candidate)
            if "ABANDON" in legal or "CONTINUE" in legal:
                continue
            if "DEFER" in legal or "START" in legal:
                return (candidate, "ABANDON",
                        "想用一个不允许 ABANDON 的候选结束它：%s"
                        % ",".join(self._refs(candidate)))
        offered = [{"candidate_id": candidate.get("candidate_id"),
                    "kind": candidate.get("candidate_kind"),
                    "targets": self._refs(candidate),
                    "legal": self._legal(candidate)}
                   for candidate in candidates]
        raise AssertionError(
            "no candidate in this round refuses ABANDON, so the illegal exit cannot be "
            "constructed; offered=%s" % json.dumps(offered, ensure_ascii=False, sort_keys=True))


class StubbornContinueCognition(nb.ScriptedNeighborhoodCognition):
    """Keeps continuing the stay no matter what the World says about the place."""

    def _choose(self, candidates: list[dict], facts: dict, here: str) -> tuple:
        running = next((candidate for candidate in candidates
                        if "CONTINUE" in self._legal(candidate)), None)
        if running is not None:
            return (running, "CONTINUE", "接着坐着（不看营业事实）")
        return super()._choose(candidates, facts, here)


@posix_only
class StayEarlyExitTest(unittest.TestCase):
    """Positive and negative cases of ending a bounded stay *before* its declared end."""

    def setUp(self) -> None:
        self.fixture = nb.NeighborhoodFixture()
        self.addCleanup(self.fixture.close)

    # -- positive ---------------------------------------------------------------

    def test_a_stay_can_be_left_early_and_a_new_move_is_legal_again(self):
        field = self.fixture
        field.use(nb.ScriptedNeighborhoodCognition(plan=TO_PARK))
        field.travel("street_front")
        field.travel("street_corner")
        field.travel("park")
        park = field.start_stay("early:park:stay")
        self.assertEqual(park["stay"].get("place"), "park", park["stay"])
        planned = int(park["stay"]["planned_seconds"])
        self.assertEqual(planned, 300, park["stay"])

        ongoing = field.round("early:park:ongoing", advance=120, because="ongoing")
        self.assertEqual(ongoing.get("outcome"), "ACTED", ongoing)
        self.assertEqual((ongoing.get("stay") or {}).get("phase"), "ONGOING", ongoing)
        self.assertNotEqual(ongoing.get("progress_state"), "COMPLETION_ELIGIBLE", ongoing)
        self.assertEqual(field.foreground(), park["activity"])

        # 改主意：一个合法的 ABANDON（它自己所在的那个 Activity 此刻允许它）。
        field.use(AbandonNowCognition(plan=()))
        left = field.round("early:park:abandon", advance=60, because="abandon")
        stay = dict(left.get("stay") or {})
        self.assertEqual(left.get("outcome"), "ABANDONED", left)
        self.assertEqual(left.get("decision"), "ABANDON", left)
        self.assertEqual(left.get("step_path"), "STAY_ENDED_BY_CHOICE", left)
        self.assertEqual(stay.get("phase"), "ENDED_EARLY", stay)
        self.assertEqual(stay.get("exit_kind"), "ABANDON", stay)
        self.assertIs(stay.get("completed"), False, stay)
        self.assertIs(stay.get("completion_claimed"), False, stay)
        self.assertIs(stay.get("resumable_as_stay"), False, stay)
        # 时长是真实的：owner 的起点 + 这一轮的 observed_at，且确实还没到声明值。
        elapsed = int(stay.get("elapsed_seconds") or 0)
        self.assertGreaterEqual(elapsed, 180, stay)
        self.assertLess(elapsed, planned, stay)
        self.assertEqual(int(stay.get("remaining_seconds") or 0), planned - elapsed, stay)
        # 状态是 owner 自己的：结束时间、结束原因、过渡记录都在。
        self.assertEqual(stay.get("owner_status"), "ABANDONED", stay)
        self.assertEqual(stay.get("owner_terminal_reason_code"), "ABANDONED_BY_DECISION", stay)
        self.assertTrue(stay.get("owner_ended_at"), stay)
        self.assertTrue(stay.get("owner_last_transition_ref"), stay)
        self.assertEqual(field.activity(park["activity"]).get("status"), "ABANDONED")
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        # 提前结束不是待满：这条 Activity 上没有任何"到点"的 checkpoint。
        updates = field.checkpoints(park["activity"])
        self.assertTrue(updates, updates)
        self.assertEqual([item for item in updates
                          if str(item.get("new_checkpoint_ref") or "")
                          .startswith("checkpoint:stay:")], [], updates)

        # 旧停留不再占前台：下一段 MOVE 合法，并且真的走到。
        field.use(nb.ScriptedNeighborhoodCognition(plan=(("go", "street_corner"),)))
        field.travel("street_corner")
        self.assertEqual(field.current_place(), "street_corner")
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        print("EARLY EXIT ok place=park planned=%s elapsed=%s owner=%s"
              % (planned, elapsed, stay.get("owner_terminal_reason_code")))

    # -- negative + the honest exit --------------------------------------------

    def test_an_illegal_exit_is_refused_and_a_closed_place_is_never_claimed(self):
        field = self.fixture
        field.use(nb.ScriptedNeighborhoodCognition(plan=(("go", "street_front"),
                                                         ("go", "street_corner"))))
        field.travel("street_front")
        field.travel("street_corner")
        # 傍晚：咖啡店在工作日 8–18 开着，所以她真的能进去坐一会儿。
        field.clock.advance(10 * 3600 + 53 * 60 + 30, because="evening")
        field.use(nb.ScriptedNeighborhoodCognition(plan=(("go", "cafe"), ("stay", None))))
        field.travel("cafe")
        self.assertEqual(field.current_place(), "cafe")
        cafe = field.start_stay("early:cafe:stay")
        self.assertEqual(cafe["stay"].get("affordance"), "SIT_AT_CAFE", cafe["stay"])
        planned = int(cafe["stay"]["planned_seconds"])

        # -- 负控 1：不合法的退出被拒，什么都不变 --------------------------------
        field.use(IllegalAbandonCognition(plan=()))
        illegal = field.round("early:cafe:illegal", advance=60, because="illegal")
        self.assertIn(illegal.get("outcome"), {"DECISION_FAILED", "ADOPTION_REJECTED"}, illegal)
        self.assertIsNone(illegal.get("stay"), illegal)
        self.assertEqual(field.foreground(), cafe["activity"])
        self.assertEqual(field.activity(cafe["activity"]).get("status"), "ACTIVE")
        print("EARLY ILLEGAL outcome=%s detail=%s" % (illegal.get("outcome"),
                                                     str(illegal.get("detail"))[:90]))

        # -- 负控 2：时间早就过了声明时长，但店关门 → 安全边界拒绝 ---------------
        field.use(StubbornContinueCognition(plan=()))
        closed = field.round("early:cafe:closed", advance=planned, because="closed")
        closed_stay = dict(closed.get("stay") or {})
        self.assertEqual(closed.get("outcome"), "STEP_REFUSED", closed)
        self.assertEqual(closed_stay.get("place_availability"), "CLOSED", closed_stay)
        self.assertEqual(closed_stay.get("refused"),
                         "WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT", closed_stay)
        self.assertNotEqual(closed.get("progress_state"), "COMPLETION_ELIGIBLE", closed)
        # 没有假释放、没有假完成：Activity 还 ACTIVE，前台还是它。
        self.assertEqual(field.foreground(), cafe["activity"])
        self.assertEqual(field.activity(cafe["activity"]).get("status"), "ACTIVE")
        self.assertEqual([item for item in field.checkpoints(cafe["activity"])
                          if str(item.get("new_checkpoint_ref") or "")
                          .startswith("checkpoint:stay:")], [],
                         field.checkpoints(cafe["activity"]))
        print("EARLY CLOSED outcome=%s refused=%s progress=%s"
              % (closed.get("outcome"), closed_stay.get("refused"),
                 closed.get("progress_state")))

        # -- 真正可行的出口：合法的 ABANDON，状态准确 ----------------------------
        field.use(AbandonNowCognition(plan=()))
        left = field.round("early:cafe:abandon", advance=30, because="abandon")
        stay = dict(left.get("stay") or {})
        self.assertEqual(left.get("outcome"), "ABANDONED", left)
        self.assertEqual(stay.get("phase"), "ENDED_EARLY", stay)
        self.assertIs(stay.get("completed"), False, stay)
        self.assertEqual(stay.get("owner_status"), "ABANDONED", stay)
        self.assertEqual(stay.get("owner_terminal_reason_code"), "ABANDONED_BY_DECISION", stay)
        # 时间早就过了声明的 300s，但因为店关着，这条停留从来没有被判为"待满"。
        elapsed = int(stay.get("elapsed_seconds") or 0)
        self.assertGreaterEqual(elapsed, planned, stay)
        self.assertEqual(int(stay.get("remaining_seconds") or 0), 0, stay)
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        self.assertEqual(field.activity(cafe["activity"]).get("status"), "ABANDONED")
        print("EARLY CLOSED-EXIT planned=%s elapsed=%s completed=%s"
              % (planned, elapsed, stay.get("completed")))


if __name__ == "__main__":
    unittest.main()
