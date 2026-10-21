#!/usr/bin/env python3
"""``serve --with-room-living``：一组开关，两端一致，且不写回旧实例。

只测配置构造（不 prepare、不起子进程、不绑 HTTP）：

* ``demo.effective_features()`` 是「实例自己记录的 features + 本次 serve 的覆盖」的唯一算法；
* ``demo._world_child()`` 给 World/Body 子进程的**环境**用的是同一组有效开关（旧代码这里硬编码
  ``CHIYO_WORLD_DAILY_RHYTHM_ENABLED=0``，外部 env 永远进不去，手机面与子进程各说各话）；
* ``PhoneService`` 的 ``feature_overrides`` 能压过旧实例 features 里记录的 ``0``，默认（不给覆盖）
  仍按旧实例的话走；
* 命令行真的接受 ``--with-room-living``。

夹具目录建在系统临时目录下且**保留**（不删除），名字唯一。
"""
from __future__ import annotations

import inspect
import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import demo, server as phone_server, service as phone_service  # noqa: E402

CLI = ROOT / "scripts" / "phone.py"

#: An old root's recorded switches: daily rhythm (and therefore the physical store) is OFF,
#: the economy/skeleton/travel switches are already on.  P must never rewrite this block.
OLD_ROOT_FEATURES = {
    "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
}

#: Unique per call: several ``setUp``s can land in the same millisecond, and a fixture root is
#: never reused (and never deleted).
_SEQUENCE = itertools.count()


def fixture_root() -> Path:
    """One retained fixture root per process; never deleted, never reused across classes."""

    base = Path(os.environ.get("NYAIRO_ROOM_TEST_ROOT")
                or (Path(tempfile.gettempdir()) / "nyairo-room-pi"))
    base.mkdir(parents=True, exist_ok=True)
    root = base / ("rc1-room-flag-%d-%d-%d" % (os.getpid(), int(time.time() * 1000),
                                               next(_SEQUENCE)))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def instance_document(root: Path) -> dict:
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "room-flag-fixture",
        "subject": "chiyo",
        "local_owner_uid": os.geteuid(),
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(root / "world"),
        "world_id": "w-flag",
        "world_package": str(root / "world" / "package"),
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "supply_generation": "fixture",
        "private_items_artifact_id": "artifact-flag",
        "artifact_grant_id": "grant-flag",
        "workspace_id": "ws-flag",
        "world_body_socket": str(root / "world" / "run" / "gateway.sock"),
        "features": dict(OLD_ROOT_FEATURES),
    }


class EffectiveFeaturesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = fixture_root()
        self.instance = instance_document(self.root)

    def test_the_default_keeps_exactly_the_recorded_switches(self) -> None:
        features = demo.effective_features(self.instance)
        self.assertEqual(features, OLD_ROOT_FEATURES)
        self.assertEqual(features["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "0")

    def test_the_flag_turns_on_exactly_two_switches_and_nothing_else(self) -> None:
        features = demo.effective_features(self.instance, room_living=True)
        self.assertEqual(set(features), set(OLD_ROOT_FEATURES)
                         | {"CHIYO_WORLD_DAILY_RHYTHM_ENABLED",
                            "CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"})
        self.assertEqual(features["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "1")
        self.assertEqual(features["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"], "1")
        # Every other recorded switch is unchanged: no price / opening-hours / timezone /
        # delivery-mode key is invented here (those are the owner's own values, not switches).
        for key, value in OLD_ROOT_FEATURES.items():
            if key == "CHIYO_WORLD_DAILY_RHYTHM_ENABLED":
                continue
            self.assertEqual(features[key], value, key)
        for banned in ("PRICE", "HOUR", "TIMEZONE", "TZ", "DELIVERY_MODE", "DURATION"):
            self.assertEqual([key for key in features if banned in key], [], banned)

    def test_the_instance_document_itself_is_not_mutated(self) -> None:
        before = dict(self.instance["features"])
        demo.effective_features(self.instance, room_living=True)
        self.assertEqual(self.instance["features"], before)


class WorldChildEnvironmentTest(unittest.TestCase):
    """The World/Body child must get the same switches the phone facade reads."""

    def setUp(self) -> None:
        self.root = fixture_root()
        self.instance = instance_document(self.root)

    def child(self, **kwargs) -> demo.ChildService:
        # No process is started here: ``start()`` is what spawns, and this test never calls it.
        return demo._world_child(self.root, self.instance, tick_seconds=5.0, **kwargs)

    def test_without_the_flag_the_old_child_environment_is_unchanged(self) -> None:
        child = self.child()
        self.assertEqual(child.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "0")
        # An undeclared room flag keeps inheriting the operator's environment (old behaviour).
        if "CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED" not in os.environ:
            self.assertNotIn("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED", child.environment)
        self.assertIsNone(child.process)

    def test_with_the_flag_the_child_gets_both_switches(self) -> None:
        child = self.child(room_living=True)
        self.assertEqual(child.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "1")
        self.assertEqual(child.environment["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"], "1")
        self.assertIsNone(child.process)

    def test_the_child_environment_matches_the_effective_features_one_for_one(self) -> None:
        for room_living in (False, True):
            with self.subTest(room_living=room_living):
                features = demo.effective_features(self.instance, room_living=room_living)
                environment = self.child(room_living=room_living).environment
                for key in ("CHIYO_WORLD_DAILY_RHYTHM_ENABLED",
                            "CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"):
                    if key in features:
                        self.assertEqual(environment[key], features[key], key)


class PhoneServiceEnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = fixture_root()
        self.instance = instance_document(self.root)

    def service(self, **kwargs) -> phone_service.PhoneService:
        return phone_service.PhoneService(self.instance, environment={"PATH": "/usr/bin"},
                                          shopping=object(), world=object(), **kwargs)

    def test_the_recorded_switch_wins_over_the_process_environment_by_default(self) -> None:
        """Old behaviour, kept: features beat the env, so an old root stays OFF."""
        service = phone_service.PhoneService(
            self.instance,
            environment={"PATH": "/usr/bin", "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1"},
            shopping=object(), world=object())
        self.assertEqual(service.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "0")

    def test_the_serve_override_wins_over_the_recorded_switch(self) -> None:
        service = self.service(feature_overrides=demo.ROOM_LIVING_FEATURES)
        self.assertEqual(service.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "1")
        self.assertEqual(service.environment["CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"], "1")
        self.assertEqual(service.environment["PATH"], "/usr/bin")

    def test_the_service_and_the_child_agree_on_both_ends(self) -> None:
        service = self.service(feature_overrides=demo.ROOM_LIVING_FEATURES)
        child = demo._world_child(self.root, self.instance, tick_seconds=5.0, room_living=True)
        for key in demo.ROOM_LIVING_FEATURES:
            self.assertEqual(service.environment[key], child.environment[key], key)
            self.assertEqual(service.environment[key],
                             demo.effective_features(self.instance, room_living=True)[key], key)

    def test_no_override_leaves_the_instance_features_untouched(self) -> None:
        service = self.service()
        self.assertEqual(service.environment["CHIYO_WORLD_DAILY_RHYTHM_ENABLED"], "0")
        self.assertNotIn("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED", service.environment)
        self.assertEqual(self.instance["features"], OLD_ROOT_FEATURES)


class ServeWiringTest(unittest.TestCase):
    def test_serve_instance_accepts_the_flag_and_defaults_to_off(self) -> None:
        signature = inspect.signature(demo.serve_instance)
        parameter = signature.parameters.get("room_living")
        self.assertIsNotNone(parameter, "serve_instance has no room_living switch")
        self.assertIs(parameter.default, False)

    def test_the_command_line_accepts_the_flag(self) -> None:
        result = subprocess.run([sys.executable, str(CLI), "serve", "--help"],
                                capture_output=True, text=True, timeout=120,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--with-room-living", result.stdout)

class ServePersistenceTest(unittest.TestCase):
    """A serve run writes only the socket binding: the override never reaches the instance file."""

    def test_serve_does_not_write_the_override_into_the_instance_file(self) -> None:
        root = fixture_root()
        instance = instance_document(root)
        phone_service.write_instance(root, instance)

        class FakeServer:
            server_address = ("127.0.0.1", 0)

            def serve_forever(self, **_kwargs) -> None:  # pragma: no cover - never reached
                return None

            def shutdown(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        stop = threading.Event()
        stop.set()
        with mock.patch.object(demo.ChildService, "start", lambda self: self), \
                mock.patch.object(phone_service, "wait_for_socket", lambda *_a, **_k: True), \
                mock.patch.object(phone_server, "create_server",
                                  lambda *_a, **_k: FakeServer()):
            announcement = demo.serve_instance(root, port=0, room_living=True,
                                               stop_event=stop, announce=False)

        self.assertIs(announcement["room_living"], True)
        marker = json.loads((root / "run" / "phone.serve.json").read_text(encoding="utf-8"))
        self.assertIs(marker["room_living"], True)

        reloaded = json.loads((root / phone_service.INSTANCE_FILENAME).read_text(encoding="utf-8"))
        # Exactly the recorded switches: the in-memory override was not persisted.
        self.assertEqual(reloaded["features"], OLD_ROOT_FEATURES)
        self.assertNotIn("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED", reloaded["features"])
        # The normal socket binding *was* written (that part of serve is unchanged).
        self.assertNotEqual(reloaded["supply_generation"], instance["supply_generation"])
        self.assertNotEqual(reloaded["supply_socket"], instance["supply_socket"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
