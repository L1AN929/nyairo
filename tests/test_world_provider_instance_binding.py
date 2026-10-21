#!/usr/bin/env python3
"""The production World factory must retain the selected instance's catalog root.

Only transport readiness and the catalog reader are doubles. The factory and
WorldMoveOpportunityProvider are the production implementations; no service,
model call, filesystem fixture or business mutation is needed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "components" / "world" / "service" / "bridge"
for entry in (ROOT, ROOT / "components" / "life" / "scripts", BRIDGE):
    sys.path.insert(0, str(entry))

try:
    import autonomous_life_assembly as assembly
    import world_action_bridge_adapter as bridge_adapter
    import world_move_opportunity_source as source
    import world_body_client as wbc
except ImportError as error:
    IMPORT_ERROR = str(error)
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, "POSIX Life imports: %s" % IMPORT_ERROR)
class WorldProviderInstanceBindingTest(unittest.TestCase):
    def _check(self, environment, expected_home):
        client = SimpleNamespace(
            get_status=lambda: SimpleNamespace(outcome=wbc.OK, data={"ready": True}))
        catalog = {"status": "available", "current_place_id": "home",
                   "opportunities": [{"location_id": "home", "name": "实例自己的家"}]}
        with mock.patch.object(bridge_adapter, "WorldBridgeResolver",
                               return_value=SimpleNamespace(client=client)), \
                mock.patch.object(source, "read_location_catalog", return_value=catalog) as read:
            result = assembly.resolve_world_provider(environment, bridge_dir=str(BRIDGE))
            self.assertEqual(result["status"], "READY")
            provider = result["provider"]
            self.assertIsInstance(provider, source.WorldMoveOpportunityProvider)
            self.assertEqual(provider.environment, environment)
            self.assertEqual(provider.world_home, expected_home)
            self.assertIs(provider.client, client)
            observed_catalog = provider._catalog()
            read.assert_called_once()
            args, kwargs = read.call_args
            self.assertEqual(args[0], environment)
            self.assertEqual(kwargs["hermes_home"], expected_home)
            self.assertEqual(observed_catalog["opportunities"][0]["name"], "实例自己的家")
        self.assertEqual(environment, provider.environment)

    def test_explicit_world_home_wins_over_profile_and_process_defaults(self):
        environment = {"WORLD_BODY_SOCKET": "/instance-b/gateway.sock",
                       "CHIYO_WORLD_HOME": "/instance-b/nondefault-world",
                       "HERMES_HOME": "/instance-b/profile"}
        with mock.patch.dict("os.environ", {"HERMES_HOME": "/other-instance"}):
            self._check(environment, "/instance-b/nondefault-world")

    def test_profile_home_fallback_still_uses_the_selected_instance(self):
        environment = {"WORLD_BODY_SOCKET": "/instance-c/gateway.sock",
                       "HERMES_HOME": "/instance-c/nondefault-profile"}
        self._check(environment, "/instance-c/nondefault-profile")


if __name__ == "__main__":
    unittest.main()
