#!/usr/bin/env python3
"""I-r2：**同一个地点、同一个意图、同一条规则**的 CLOSED/OPEN 对照（真实 owner，注入时钟）。

主链（``tests/test_world_daily_life_continuous.py``）只证明"模型读到了闭店这条来源事实，并选了
另一个真会执行的分支"。这里补的是对照本身：在**同一个地点**（门前街）、**同一个意图**
（去书店看会儿书）、**同一条规则**下，只让 World owner 自己的营业事实从 CLOSED 变成 OPEN：

  A. 工作日 07:00（书店 10 点才开）——``world_place_reading_point_availability_CLOSED`` 进了这次
     DecisionRequest 的输入；``place:reading_point`` 仍然**走得到**（闭店不拦出发，所以它的缺席
     不是拓扑造成的），但决策没有往关着的那一支走，而是选了"就地待一会儿"，并且真的 START；
     店里此刻没有任何"读一会儿"的机会。
  B. 同一个地点、同一个意图，把同一个注入时钟推进到当天 10:30——事实变成 OPEN，模型选了
     ``place:reading_point``，真的出发、真的到达；到了店里，``stay:reading_point:READ_HERE:600``
     这个合法机会才出现，而且被选中、真的 START。

Life 不写"闭店必去 X"的路由：营业事实由 World owner 给，候选由 AG-1 给，"怎么选"是模型那一侧的
同一个规则。夹具复用 ``EpisodeFixture``（同一个真实 runtime/World 服务/注入时钟），不继承主链的
drill 用例。所有 home 落在 ``/tmp/nyaw05r.jdK3q/`` 并**保留**。
"""

from __future__ import annotations

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
from test_world_daily_life_continuous import EpisodeFixture  # noqa: E402

WISH = "read_at:reading_point"
READ_STAY = "stay:reading_point:READ_HERE:600"


@posix_only
class PlanChangeContrastTest(EpisodeFixture):
    """同一地点、同一意图、同一规则：只让 owner 的营业事实变一次。"""

    def test_the_same_rule_waits_when_the_shop_is_closed_and_goes_when_it_opens(self):
        self.cognition.goal = WISH
        self.move("street_front", goal=WISH)
        self.assertEqual(self.current_place(), "street_front")
        here = [item["target_refs"][0] for item in self.provider()]
        self.assertIn("place:reading_point", here, here)

        # -- A) 工作日 07:00：书店还没开门 -----------------------------------------
        closed = self.round("plan:closed")
        read = self.cognition.reads[-1]
        self.assertEqual(read["facts"].get("reading_point"), "CLOSED", read)
        self.assertIn("world_place_reading_point_availability_CLOSED", read["fact_keys"], read)
        # 走得到（owner 的 action space 给出的 MOVE），但这一轮没有进店的机会
        self.assertIn("place:reading_point", read["offered_targets"], read)
        self.assertNotIn(READ_STAY, read["offered_targets"], read)
        self.assertNotEqual(read["chose"], "place:reading_point", read)
        self.assertTrue(str(read["chose"] or "").startswith("stay:street_front:"), read)
        stay = closed.get("stay") or {}
        self.assertEqual(closed.get("outcome"), "ACTED", closed)
        self.assertEqual(stay.get("phase"), "START", stay)
        self.assertEqual(stay.get("place"), "street_front", stay)
        print("R2 CONTRAST closed chosen=%s facts=%s" % (read["chose"], read["facts"]))
        # 把这一轮停留干净收尾（提前不许完成已由主链证过，这里只求前台释放）
        ended = self.round("plan:stay:end", advance=180, because="stay:end")
        self.assertEqual((ended.get("stay") or {}).get("phase"), "END", ended.get("stay"))
        self.assertIsNone(self.foreground(), self.runtime.canonical_state())

        # -- B) 同一个地点、同一个意图，同一个注入时钟推进到当天 10:30 --------------
        self.clock.advance(12600, because="same weekday, later")
        self.move("reading_point", goal=WISH)
        read_open = self.cognition.reads[-1]
        self.assertEqual(read_open["facts"].get("reading_point"), "OPEN", read_open)
        self.assertEqual(read_open["chose"], "place:reading_point", read_open)
        self.assertEqual(self.current_place(), "reading_point")
        print("R2 CONTRAST open   chosen=%s facts=%s" % (read_open["chose"], read_open["facts"]))

        # 到了店里：OPEN 才让"读一会儿"这个合法机会出现，而且模型选了它、真的开始
        offers = [item["target_refs"][0] for item in self.provider()]
        self.assertIn(READ_STAY, offers, offers)
        reading = self.round("plan:read")
        read_here = self.cognition.reads[-1]
        self.assertEqual(read_here["here"], "reading_point", read_here)
        self.assertEqual(read_here["chose"], READ_STAY, read_here)
        started = reading.get("stay") or {}
        self.assertEqual(reading.get("outcome"), "ACTED", reading)
        self.assertEqual(started.get("phase"), "START", started)
        self.assertEqual(started.get("affordance"), "READ_HERE", started)
        self.assertEqual(started.get("planned_seconds"), 600, started)
        self.assertEqual(self.current_place(), "reading_point")
        print("R2 CONTRAST reading stay=%s planned=%s" % (started.get("checkpoint_ref"),
                                                          started.get("planned_seconds")))


if __name__ == "__main__":
    unittest.main()
