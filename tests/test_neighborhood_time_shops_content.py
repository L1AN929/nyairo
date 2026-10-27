#!/usr/bin/env python3
"""Neighbourhood time/shop content: package facts and the phone's world clock wiring.

Only the directly affected facts are checked here (no full-suite ceremony):

* ``daily_neighborhood_v1`` declares a public-domain reading material in its own
  ``content.json`` and its manifest still verifies;
* the café no longer advertises an unsupported ``BUY_DRINK``: it advertises the
  menu note and says the drinks are not sold;
* the reading corner opens at 10:00 and closes at 20:00, and the café closes at
  18:00 on weekdays;
* ``nyairo_day`` reads that package material for a new root;
* ``PhoneService`` forwards an explicit business clock to the World/economy
  facades, and keeps ``None`` when no clock was given.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    ROOT,
    ROOT / "components",
    ROOT / "components" / "world" / "scripts",
    ROOT / "components" / "life" / "scripts",
    ROOT / "scripts",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
posix_only = unittest.skipIf(
    os.name == "nt", "the phone service and Supply stack use POSIX sockets/locks"
)


def _json(name: str) -> dict:
    return json.loads((PACKAGE / name).read_text(encoding="utf-8"))


class PackageContentTest(unittest.TestCase):
    def test_reading_material_is_the_package_own_public_domain_text(self) -> None:
        entry = _json("content.json")["entries"]["reading_material"]
        self.assertEqual(entry["license"], "public-domain")
        self.assertIn("陶渊明", entry["author"])
        self.assertIn("桃花源记", entry["work"])
        self.assertTrue(entry["content"].startswith("晋太元中"))
        self.assertIn("wikisource.org", entry["source"])
        self.assertNotIn("%%PAGE%%", entry["content"])

        import nyairo_day

        loaded = nyairo_day._package_reading_material("daily_neighborhood_v1")
        self.assertEqual(loaded["title"], entry["title"])
        self.assertEqual(loaded["content"], entry["content"])
        self.assertEqual(loaded["source"], entry["source"])
        self.assertEqual(nyairo_day._package_reading_material(None)["content"], None)

    def test_manifest_still_verifies_after_the_content_change(self) -> None:
        from components.worldgen import schema

        schema.verify_manifest(PACKAGE)

    def test_cafe_advertises_a_menu_note_not_an_unsupported_drink_purchase(self) -> None:
        rules = _json("rules.json")["location_rules"]["cafe"]
        ids = [item["affordance_id"] for item in rules["affordances"]]
        self.assertIn("LOOK_AT_MENU", ids)
        self.assertNotIn("BUY_DRINK", ids)
        note = _json("content.json")["entries"]["cafe"]["menu_note"]
        self.assertIn("没有接入饮品售卖", note)
        self.assertIn("不能点单", note)
        timings = (rules["open_hour"], rules["close_hour"], rules["weekdays_only"])
        self.assertEqual(timings, (8, 18, True))

    def test_reading_corner_offers_a_real_reading_affordance_and_closes_at_20(self) -> None:
        rules = _json("rules.json")["location_rules"]["reading_point"]
        ids = [item["affordance_id"] for item in rules["affordances"]]
        self.assertIn("READ_HERE", ids)
        self.assertIn("BROWSE_BOOKS", ids)
        self.assertEqual((rules["open_hour"], rules["close_hour"]), (10, 20))
        note = _json("content.json")["entries"]["reading_point"]["shelf_note"]
        self.assertIn("公版", note)
        self.assertIn("不需要买下", note)


@posix_only
class PhoneServiceWorldClockTest(unittest.TestCase):
    def _service(self, world_clock):
        from components.phone import service as phone_service

        home = Path(tempfile.mkdtemp(prefix="nyairo-time-shops-test-"))
        instance = {
            "root": str(home),
            "subject": "chiyo",
            "world_home": str(home),
            "supply_socket": str(home / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private",
            "artifact_grant_id": "grant:test",
            "workspace_id": "workspace:test",
            "reading_artifact_id": "artifact:reading",
        }
        return phone_service.PhoneService(instance, world_clock=world_clock), home

    def test_explicit_clock_reaches_both_world_facades(self) -> None:
        import world_phone_shopping
        import world_phone_world

        captured = {}

        class FakeWorld:
            def __init__(self, *args, **kwargs):
                captured["world"] = kwargs

        class FakeShopping:
            def __init__(self, *args, **kwargs):
                captured["shopping"] = kwargs

        moment = object()
        service, _home = self._service(lambda: moment)
        with mock.patch.object(world_phone_world, "PhoneWorld", FakeWorld), \
                mock.patch.object(world_phone_shopping, "PhoneShopping", FakeShopping):
            service._world_instance()
            service._shopping_instance()
        self.assertIs(captured["world"]["clock"], service._world_clock)
        self.assertIs(captured["shopping"]["clock"], service._world_clock)
        self.assertIs(service._world_clock(), moment)

    def test_no_clock_keeps_the_old_none_behaviour(self) -> None:
        import world_phone_shopping
        import world_phone_world

        captured = {}

        class FakeWorld:
            def __init__(self, *args, **kwargs):
                captured["world"] = kwargs

        class FakeShopping:
            def __init__(self, *args, **kwargs):
                captured["shopping"] = kwargs

        service, _home = self._service(None)
        with mock.patch.object(world_phone_world, "PhoneWorld", FakeWorld), \
                mock.patch.object(world_phone_shopping, "PhoneShopping", FakeShopping):
            service._world_instance()
            service._shopping_instance()
        self.assertIsNone(captured["world"]["clock"])
        self.assertIsNone(captured["shopping"]["clock"])


if __name__ == "__main__":
    unittest.main()
