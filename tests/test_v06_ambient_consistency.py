#!/usr/bin/env python3
"""v0.6 ambient 背景双读一致性：同一世界/位置/有效期事实不得互相矛盾。

背景读（``world_living_skeleton.observe_world``）和位置/动作空间读（网关 socket）是两条
读取。本组用例只核**已有明确事实**：world 身份、place/area、事件有效区间；缺失字段按
UNKNOWN 处理，不当作矛盾，也不发明毫秒阈值或原子快照。

标记 ``TEST_INTERLEAVING`` 的用例用确定性端口替身让第二次读返回不同 world/place 或
已失效记录——它只证明过滤/未知分支，不表示真实世界发生过移动或并发。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import world_move_opportunity_source as wmos  # noqa: E402

EVENT_ID = "ambient-2026-11-03-home_window_sound"
SUMMARY = "窗外能听到轮船的汽笛声"
OBSERVED_AT = "2026-11-03T00:03:00+09:00"
WORLD_ID = "coastal_town_929711"


def _event(*, event_id=EVENT_ID, summary=SUMMARY, location_id="home",
           valid_from="2026-11-03T00:00:00+09:00",
           valid_until="2026-11-04T00:00:00+09:00"):
    return {
        "event_id": event_id,
        "event_type": "home_window_sound",
        "location_id": location_id,
        "local_date": "2026-11-03",
        "valid_from": valid_from,
        "valid_until": valid_until,
        "payload": {"summary": summary},
    }


def _observation(**overrides):
    base = {
        "status": "available",
        "world_id": WORLD_ID,
        "world_revision": 7,
        "local_time": OBSERVED_AT,
        "current_location": {"location_id": "home/bedroom", "area_id": "bedroom",
                             "kind": "home"},
        "ambient_events": [_event()],
    }
    base.update(overrides)
    return base


class AmbientConsistencyTests(unittest.TestCase):
    def setUp(self):
        import world_living_skeleton as wls
        import world_runtime_data as wrd

        self._wls = wls
        self._wrd = wrd
        self._original_observe = wls.observe_world
        self._original_package_root = wrd.package_root
        wrd.package_root = lambda environment=None: "/tmp/ambient-consistency-package"

    def tearDown(self):
        self._wls.observe_world = self._original_observe
        self._wrd.package_root = self._original_package_root

    def _install(self, observation):
        self._wls.observe_world = lambda *_args, **_kwargs: observation

    def _provider(self):
        provider = wmos.WorldMoveOpportunityProvider(
            client=None, action_intent=None,
            environment={"CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1",
                         "CHIYO_WORLD_PACKAGE": "/tmp/ambient-consistency-package"},
            world_home="/tmp/ambient-consistency-world")
        # The gateway snapshot this round: same world and same place as the World read.
        provider.last_observation = {
            "world_id": WORLD_ID,
            "world_revision": 7,
            "location": {"place_id": "home", "area_id": "bedroom"},
            "visible_objects": [],
        }
        return provider

    def test_matching_world_place_and_window_is_presented(self):
        self._install(_observation())
        context = self._provider().daily_life_context()
        self.assertEqual(context["ambient"]["status"], "available")
        event = context["ambient"]["events"][0]
        self.assertEqual(event["event_id"], EVENT_ID)
        self.assertEqual(event["summary"], SUMMARY)

    def test_second_read_from_another_world_is_not_presented(self):
        """TEST_INTERLEAVING: the second read names a different world."""

        self._install(_observation(world_id="some_other_world"))
        context = self._provider().daily_life_context()
        self.assertEqual(context["ambient"]["status"], "unavailable")
        self.assertEqual(context["ambient"]["reason_code"], "AMBIENT_WORLD_MISMATCH")
        self.assertEqual(context["ambient"]["events"], [])

    def test_second_read_from_another_place_is_not_presented(self):
        """TEST_INTERLEAVING: the second read is taken somewhere else."""

        self._install(_observation(current_location={
            "location_id": "street_1/entrance", "area_id": "entrance",
            "kind": "street"}))
        context = self._provider().daily_life_context()
        self.assertEqual(context["ambient"]["status"], "unavailable")
        self.assertEqual(context["ambient"]["reason_code"], "AMBIENT_LOCATION_MISMATCH")
        self.assertEqual(context["ambient"]["events"], [])

    def test_expired_event_is_not_presented_as_current(self):
        """TEST_INTERLEAVING: the second read carries a record from before its window."""

        self._install(_observation(
            local_time="2026-11-04T01:00:00+09:00",
            ambient_events=[_event(valid_until="2026-11-04T00:00:00+09:00")]))
        context = self._provider().daily_life_context()
        self.assertEqual(context["ambient"]["status"], "unavailable")
        self.assertEqual(context["ambient"]["reason_code"],
                         "AMBIENT_EVENT_WINDOW_MISMATCH")
        self.assertEqual(context["ambient"]["events"], [])

    def test_place_node_area_is_not_a_false_mismatch(self):
        """A place node reports the place's default area; the state area is not comparable.

        Standing at the entrance, ``observe_world`` names the place node ``home`` (whose
        projected area is the package default) while the gateway reports the state area.
        The place agrees, so this is not a location contradiction.
        """

        self._install(_observation(current_location={
            "location_id": "home", "area_id": "bedroom", "kind": "home"}))
        provider = self._provider()
        provider.last_observation["location"] = {"place_id": "home", "area_id": "living"}
        context = provider.daily_life_context()
        self.assertEqual(context["ambient"]["status"], "available")
        self.assertEqual(context["ambient"]["events"][0]["event_id"], EVENT_ID)

    def test_unknown_identity_is_not_a_contradiction(self):
        """An older double that does not report identity stays UNKNOWN, not a mismatch."""

        self._install(_observation(world_id=None, current_location=None,
                                   local_time=None))
        context = self._provider().daily_life_context()
        self.assertEqual(context["ambient"]["status"], "available")
        self.assertEqual(context["ambient"]["events"][0]["event_id"], EVENT_ID)


if __name__ == "__main__":
    unittest.main()
