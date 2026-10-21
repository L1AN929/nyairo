#!/usr/bin/env python3
"""rc1 P: 手机实例上的主体 Life 绑定（``life-preflight`` / ``life-run``）精确正负。

本文件只跑**这一条**新入口与它精确影响的 ``one_day_life`` parser 缺陷，不跑旧全库、不跑 24 小时：

* 绑定：手机实例的 live socket / subject / workspace / grant / work / reading / PDI / world_home /
  world_body_socket 覆盖 nested Life 清单里的同名值；run_id / 请求预算 / install root **沿用**；
  ``CHIYO_AUTONOMOUS_NEW_RUN_WINDOW`` 永远不出现（连 base env 里塞进去也会被剥掉）。
* 零写：preflight 前后对整棵树做 mode/size/mtime 快照，必须一字不差（含 state root 与 run 目录）。
* 零模型 / 零探测：preflight 的 model 行只有 ``configured`` 与 ``request_sent: false``；POSIX 上
  用真实 AF_UNIX listener 断言**没有**任何连接被接受。
* 诚实 BLOCKED：无 provider、开关没开、没预算、nested 缺失、cross-binding、陈旧 socket 代次都在
  **启动 resident 之前**返回 reason + exit code；同一个 ``resident_factory`` 记录到它到底被喂了什么。
* CLI：两个 verb 的 help、``--once``/``--max-ticks``/``--duration-seconds`` 转发，且**没有**
  开新窗口 / 打开自主开关 / 选择 payload 的 flag（argparse 直接拒绝）。

夹具根默认落在系统 temp（``NYAIRO_RC1_LIFE_ROOT`` 可覆盖），**保留不删**；不需要 POSIX 的用例
在原生 Windows 上也跑，需要 ``fcntl``/AF_UNIX 的用例自动跳过。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "scripts"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import phone as phone_cli  # noqa: E402  (scripts/phone.py)
import one_day_life as odl  # noqa: E402
from components.phone import life  # noqa: E402

posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX fcntl/AF_UNIX（在 WSL/Linux 上运行）")

BASE = Path(os.environ.get("NYAIRO_RC1_LIFE_ROOT") or tempfile.gettempdir())


def unique_root(prefix: str) -> Path:
    """A retained, unique fixture root.  Nothing here ever deletes one."""

    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    return root


def snapshot(root: Path) -> dict:
    """mode / size / mtime of everything under ``root`` (the zero-write witness)."""

    out: dict = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            info = path.lstat()
        except OSError:  # a path removed under us would be the bug; record it loudly
            out[str(path.relative_to(root))] = ("MISSING", 0, 0)
            continue
        out[str(path.relative_to(root))] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return out


PHONE_SOCKET_GENERATION = "serve-1"
NESTED_SOCKET_GENERATION = "prepare-0"


class PhoneLifeFixture(unittest.TestCase):
    """One synthetic phone root + one nested Life instance, both real files on disk."""

    def setUp(self) -> None:
        self.root = unique_root("rc1-life-bind-")
        self.root.mkdir(parents=True)
        (self.root / "run").mkdir(parents=True, exist_ok=True)
        state_root = self.root / "one-day" / "life" / "state"
        (state_root / "run").mkdir(parents=True, exist_ok=True)
        self.world_home = self.root / "one-day" / "world-home"
        self.world_home.mkdir(parents=True, exist_ok=True)
        self.state_root = state_root
        # ``one_day_life.prepare`` creates its state root 0700 (owner only); the fixture mirrors
        # that so the permission row has a real, expected value to report.
        if os.name != "nt":
            os.chmod(state_root, 0o700)
        self.profile_home = self.root / "profile-home"
        self.profile_home.mkdir(parents=True, exist_ok=True)

        self.phone = {
            "schema_version": "phone.instance.v1",
            "kind": "phone_local_instance",
            "created_at": "2026-10-23T00:00:00Z",
            "name": self.root.name,
            "mode": "local_owner",
            "subject": "chiyo",
            "local_owner_uid": int(getattr(os, "geteuid", lambda: 1000)()),
            "profile_home": str(self.root),
            "root": str(self.root),
            "world_home": str(self.world_home),
            "world_id": "coastal_town:929711",
            "world_package": str(self.root / "one-day" / "world-package"),
            "supply_socket": str(self.root / "run" / ("supply-%s.sock" % PHONE_SOCKET_GENERATION)),
            "supply_generation": PHONE_SOCKET_GENERATION,
            "supply_data_root": str(self.root / "supply" / "data"),
            "workspace_id": "ws-1",
            "artifact_grant_id": "grant-1",
            "private_items_artifact_id": "art-pdi",
            "reading_artifact_id": "art-read",
            "work_artifact_id": "art-work",
            "world_body_socket": str(self.world_home / "run" / "gateway.sock"),
            "economy_state": str(self.world_home / "state" / "economy.json"),
            "capabilities": str(self.world_home / "state" / "caps.json"),
            "features": {"CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
                         "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1"},
        }
        self.nested = {
            "schema_version": "life.one_day.v1",
            "kind": "one_day_life_instance",
            "instance_root": str(self.root / "one-day"),
            "subject_id": "chiyo",
            "workspace_id": "ws-1",
            "artifact_id": "art-work",
            "artifact_grant_id": "grant-1",
            "supply_socket": str(self.root / "run"
                                 / ("supply-%s.sock" % NESTED_SOCKET_GENERATION)),
            "governance_socket": str(self.root / "run"
                                     / ("supply-%s.sock" % NESTED_SOCKET_GENERATION)),
            "world_id": "coastal_town:929711",
            "world_home": str(self.world_home),
            "work_artifact_id": "art-work",
            "reading_artifact_id": "art-read",
            "private_items_artifact_id": "art-pdi",
            "environment": {
                "LIFE_SUPPLY_SOCKET": str(self.root / "run"
                                          / ("supply-%s.sock" % NESTED_SOCKET_GENERATION)),
                "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET": str(self.root / "run"
                                                       / ("supply-%s.sock"
                                                          % NESTED_SOCKET_GENERATION)),
                "NYA02_SUBJECT": "chiyo",
                "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT": "chiyo",
                "NYA02_ARTIFACT_GRANT": "grant-1",
                "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT": "grant-1",
                "CHIYO_LIFE_SUPPLY_WORKSPACE_ID": "ws-1",
                "CHIYO_LIFE_INSTALL_ROOT": str(self.root / "one-day" / "life"),
                "NYA02_RESIDENT_STATE": str(state_root),
                "CHIYO_AUTONOMOUS_RUN_ID": "run:existing-window",
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "96",
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "96",
                "CHIYO_LIFE_WORK_ARTIFACT_ID": "art-work",
                "CHIYO_LIFE_READING_ARTIFACT_ID": "art-read",
                "CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID": "art-pdi",
            },
        }
        self.write_phone()
        self.write_nested()
        self.write_switch(True)
        self.base_env = {"NYA02_PROFILE_HOME": str(self.profile_home)}

    # -- fixture writers (test-side only) ---------------------------------------------------

    def write_phone(self, **overrides) -> None:
        payload = dict(self.phone)
        payload.update(overrides)
        self.phone = payload
        (self.root / "instance.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_nested(self, *, environment: dict | None = None, **overrides) -> None:
        payload = dict(self.nested)
        payload.update(overrides)
        env = dict(self.nested["environment"])
        env.update(environment or {})
        payload["environment"] = env
        self.nested = payload
        (self.root / "one-day" / "instance.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_switch(self, enabled: bool) -> None:
        (self.state_root / "autonomous_switch.json").write_text(
            json.dumps({"enabled": bool(enabled), "changed_at": "2026-10-23T00:00:00Z",
                        "changed_by": "test"}), encoding="utf-8")

    def provider_env(self) -> dict:
        env = dict(self.base_env)
        env.update({"NYA02_MODEL_BASE_URL": "http://127.0.0.1:9/v1",
                    "NYA02_MODEL_NAME": "fixture-model",
                    "NYA02_MODEL_API_KEY": "fixture-placeholder-not-a-key"})
        return env


class RecordingResident:
    """The injected factory: records exactly what the shipped code bound."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.counters = {"model_calls": 0}
        self.ticks = 0
        self.started = False
        self.stopped = False
        RecordingResident.last = self

    def start(self):
        self.started = True
        return {"ok": True, "state_root": str(self.kwargs["state_root"])}

    def tick(self):
        self.ticks += 1
        return {"ok": True, "tick": self.ticks}

    def run(self, *, max_ticks=None, duration_seconds=None):
        return {"max_ticks": max_ticks, "duration_seconds": duration_seconds}

    def stop(self):
        self.stopped = True
        return {"ok": True}


class MustNotBeBuilt:
    """A factory that fails the test if anything tried to start a runtime."""

    def __init__(self, **kwargs):
        raise AssertionError("the resident must not be constructed: %r" % (sorted(kwargs),))


# ---------------------------------------------------------------------------------------------
# preflight: the binding, zero write, zero model
# ---------------------------------------------------------------------------------------------


class PreflightBindingTests(PhoneLifeFixture):
    def test_preflight_maps_live_binding_and_writes_nothing(self) -> None:
        before = snapshot(self.root)
        report = life.life_preflight(self.root, environment=self.base_env)
        after = snapshot(self.root)
        self.assertEqual(before, after, "preflight wrote something on disk")

        self.assertTrue(report["read_only"])
        self.assertFalse(report["model_called"])
        self.assertFalse(report["socket_created"])
        self.assertEqual(0, report["processes_started"])
        self.assertFalse(report["verified_claims"]["verified_model_response"])
        self.assertIn("socket connect", report["verified_claims"]["not_performed"])

        env = life.map_life_environment(self.phone, self.nested, base_env=self.base_env)
        # live socket wins over the nested generation, and the override is on the record
        self.assertEqual(self.phone["supply_socket"], env["LIFE_SUPPLY_SOCKET"])
        self.assertEqual(self.phone["supply_socket"],
                         env["CHIYO_NATIVE_LIFE_SUPPLY_SOCKET"])
        self.assertIn("LIFE_SUPPLY_SOCKET", report["binding"]["overridden_by_live_binding"])
        # Life's own run window / budget / root are carried over, never re-minted
        self.assertEqual("run:existing-window", env["CHIYO_AUTONOMOUS_RUN_ID"])
        self.assertEqual("96", env["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"])
        self.assertEqual(str(self.state_root), env["NYA02_RESIDENT_STATE"])
        self.assertNotIn(odl.NEW_RUN_WINDOW_ENV, env)
        self.assertFalse(report["binding"]["new_run_window_requested"])
        # the three explicit artifact bindings come from the phone instance
        for name, value in (("CHIYO_LIFE_WORK_ARTIFACT_ID", "art-work"),
                            ("CHIYO_LIFE_READING_ARTIFACT_ID", "art-read"),
                            ("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID", "art-pdi")):
            self.assertEqual(value, env[name])
            self.assertEqual("phone_live", report["bindings"][name]["source"])
        self.assertEqual(str(self.state_root),
                         str(life.life_state_root(self.root, self.nested)))
        self.assertEqual([], report["binding"]["cross_binding_conflicts"])

    def test_preflight_strips_a_stray_new_run_window_from_the_environment(self) -> None:
        stray = dict(self.base_env)
        stray[odl.NEW_RUN_WINDOW_ENV] = "1"
        stray["CHIYO_AUTONOMOUS_RUN_ID"] = "run:from-somebody-elses-shell"
        env = life.map_life_environment(self.phone, self.nested, base_env=stray)
        self.assertNotIn(odl.NEW_RUN_WINDOW_ENV, env)
        # the instance's own run identity is restored, not the shell's
        self.assertEqual("run:existing-window", env["CHIYO_AUTONOMOUS_RUN_ID"])
        self.assertEqual("nested_life", life.build_binding(
            self.phone, self.nested, base_env=stray)["sources"]["CHIYO_AUTONOMOUS_RUN_ID"])

    def test_preflight_does_not_invent_a_missing_reading_binding(self) -> None:
        self.write_phone(reading_artifact_id="")
        self.write_nested(reading_artifact_id=None,
                          environment={"CHIYO_LIFE_READING_ARTIFACT_ID": ""})
        report = life.life_preflight(self.root, environment=self.base_env)
        self.assertFalse(report["bindings"]["CHIYO_LIFE_READING_ARTIFACT_ID"]["bound"])
        self.assertEqual("absent",
                         report["bindings"]["CHIYO_LIFE_READING_ARTIFACT_ID"]["source"])
        self.assertIn("reading_artifact_id", report["binding"]["unknown"])
        env = life.map_life_environment(self.phone, self.nested, base_env=self.base_env)
        self.assertNotIn("CHIYO_LIFE_READING_ARTIFACT_ID", env)

    def test_preflight_reports_the_stale_supply_generation(self) -> None:
        (self.root / "run" / "phone.serve.json").write_text(
            json.dumps({"supply_generation": "serve-9",
                        "supply_socket": str(self.root / "run" / "supply-serve-9.sock")}),
            encoding="utf-8")
        report = life.life_preflight(self.root, environment=self.base_env)
        supply = report["checks"]["supply"]
        self.assertTrue(supply["stale"])
        self.assertEqual("BLOCKED", supply["status"])
        self.assertEqual("SUPPLY_BINDING_STALE", supply["detail"])
        self.assertIn("supply", report["blocked"])
        self.assertFalse(report["ok"])

    def test_preflight_calls_a_missing_socket_unavailable_not_live(self) -> None:
        report = life.life_preflight(self.root, environment=self.base_env)
        supply = report["checks"]["supply"]
        self.assertFalse(supply["present"])
        self.assertEqual("UNAVAILABLE", supply["status"])
        self.assertEqual("not_performed_read_only", supply["probe"])
        self.assertFalse(report["assembly"]["supply_socket_present"])

    def test_preflight_reports_the_nested_instance_missing(self) -> None:
        # A phone root without a Life instance: the run window and switch live there.
        lonely = unique_root("rc1-life-nonested-")
        lonely.mkdir(parents=True)
        payload = dict(self.phone, root=str(lonely),
                       supply_socket=str(lonely / "run" / "supply-serve-1.sock"))
        (lonely / "instance.json").write_text(json.dumps(payload, ensure_ascii=False),
                                             encoding="utf-8")
        report = life.life_preflight(lonely, environment=self.base_env)
        self.assertEqual("UNAVAILABLE", report["checks"]["nested_life"]["status"])
        self.assertIn("nested_life_instance", report["binding"]["unknown"])
        run = life.life_run(lonely, once=True, environment=self.provider_env(),
                            resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertFalse(run["ok"])
        self.assertEqual("NESTED_LIFE_INSTANCE_MISSING", run["reason"])
        self.assertEqual(life.EXIT_BLOCKED, run["exit_code"])

    def test_preflight_reports_a_cross_binding_conflict(self) -> None:
        self.write_nested(workspace_id="ws-OTHER",
                          environment={"CHIYO_LIFE_SUPPLY_WORKSPACE_ID": "ws-OTHER"})
        report = life.life_preflight(self.root, environment=self.base_env)
        self.assertIn("CHIYO_LIFE_SUPPLY_WORKSPACE_ID",
                      report["binding"]["cross_binding_conflicts"])
        run = life.life_run(self.root, once=True, environment=self.provider_env(),
                            resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual("CROSS_BINDING_CONFLICT", run["reason"])
        self.assertIn("CHIYO_LIFE_SUPPLY_WORKSPACE_ID", run["detail"])

    def test_preflight_reports_permissions_and_uid(self) -> None:
        report = life.life_preflight(self.root, environment=self.base_env)
        permissions = report["checks"]["permissions"]
        self.assertEqual(str(self.state_root), permissions["state_root"])
        self.assertTrue(permissions["state_root_exists"])
        if os.name != "nt":
            self.assertEqual("0700", permissions["state_root_mode"])
            self.assertTrue(permissions["state_root_secure"])
            self.assertTrue(permissions["uid_matches"])
            self.assertEqual(int(os.geteuid()), permissions["effective_uid"])
            self.assertEqual(int(os.geteuid()), permissions["recorded_local_owner_uid"])
        self.assertEqual(str(self.profile_home / "chiyo" / "config.json"),
                         report["checks"]["privacy"]["profile_config"])
        self.assertFalse(report["checks"]["privacy"]["private_content_read"])
        self.assertIsNone(report["checks"]["privacy"]["private_artifacts_permit"])

    def test_preflight_model_row_is_configuration_only(self) -> None:
        report = life.life_preflight(self.root, environment=self.base_env)
        model = report["checks"]["model"]
        self.assertFalse(model["configured"])
        self.assertFalse(model["request_sent"])
        self.assertEqual("not_performed", model["probe"])
        self.assertIn("model", report["blocked"])
        self.assertNotIn("api_key", model.get("description") or {})

        configured = life.life_preflight(self.root, environment=self.provider_env())
        model = configured["checks"]["model"]
        self.assertTrue(model["configured"])
        self.assertFalse(model["request_sent"])
        self.assertNotIn("api_key", model["description"])
        self.assertTrue(model["description"]["api_key_present"])
        self.assertNotIn("model", configured["blocked"])


# ---------------------------------------------------------------------------------------------
# run: honest refusals before anything is started, then the existing resident
# ---------------------------------------------------------------------------------------------


class RunRefusalTests(PhoneLifeFixture):
    def test_run_is_blocked_without_a_provider(self) -> None:
        before = snapshot(self.root)
        report = life.life_run(self.root, once=True, environment=self.base_env,
                               resident_factory=MustNotBeBuilt)
        self.assertEqual(before, snapshot(self.root), "a refused run wrote something")
        self.assertFalse(report["ok"])
        self.assertEqual("NO_MODEL_PROVIDER", report["reason"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])
        self.assertFalse(report["model_called"])

    def test_run_is_blocked_when_the_operator_switch_is_off(self) -> None:
        self.write_switch(False)
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual("AUTONOMOUS_SWITCH_OFF", report["reason"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])
        self.assertFalse(report["switch"]["recorded"])

    def test_run_never_turns_the_switch_on_by_itself(self) -> None:
        self.write_switch(False)
        before = (self.state_root / "autonomous_switch.json").read_text(encoding="utf-8")
        life.life_run(self.root, once=True, environment=self.provider_env(),
                      resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual(before,
                         (self.state_root / "autonomous_switch.json").read_text(encoding="utf-8"))

    def test_run_is_blocked_without_an_explicit_request_ceiling(self) -> None:
        self.write_nested(environment={
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "",
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": ""})
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual("REQUEST_CEILING_UNSET", report["reason"])
        self.assertIn("request_ceiling", report["binding"]["unknown"])

    def test_run_is_blocked_when_the_supply_owner_does_not_answer(self) -> None:
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=MustNotBeBuilt)
        self.assertEqual("SUPPLY_OWNER_UNREACHABLE", report["reason"])
        self.assertEqual("UNREACHABLE", report["supply"]["live_health"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])

    @posix_only
    def test_run_refuses_a_foreign_uid(self) -> None:
        self.write_phone(local_owner_uid=int(os.geteuid()) + 1)
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual("LOCAL_OWNER_UID_MISMATCH", report["reason"])
        self.assertEqual(life.EXIT_REFUSED, report["exit_code"])
        preflight = life.life_preflight(self.root, environment=self.provider_env())
        self.assertEqual("BLOCKED", preflight["checks"]["permissions"]["status"])
        self.assertIn("permissions", preflight["blocked"])


class RunUsesTheExistingResidentTests(PhoneLifeFixture):
    def test_run_binds_the_existing_resident_to_the_phone_instance(self) -> None:
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=RecordingResident, verify_owners=False)
        self.assertTrue(report["ok"], report.get("reason"))
        self.assertEqual(life.EXIT_OK, report["exit_code"])
        resident = RecordingResident.last
        self.assertTrue(resident.started)
        self.assertTrue(resident.stopped)
        self.assertEqual(str(self.state_root), str(resident.kwargs["state_root"]))
        self.assertEqual(self.phone["supply_socket"],
                         resident.kwargs["environment"]["LIFE_SUPPLY_SOCKET"])
        self.assertEqual("run:existing-window",
                         resident.kwargs["environment"]["CHIYO_AUTONOMOUS_RUN_ID"])
        self.assertNotIn(odl.NEW_RUN_WINDOW_ENV, resident.kwargs["environment"])
        self.assertTrue(resident.kwargs["act"])
        self.assertEqual(resident.kwargs["poll_seconds"], report["resident"]["poll_seconds"])
        self.assertTrue(report["once"])
        self.assertFalse(report["model_called"])
        self.assertEqual(1, resident.ticks)
        self.assertEqual("RecordingResident", report["resident"]["factory"])

    def test_run_forwards_the_finite_bounds_and_no_act(self) -> None:
        report = life.life_run(self.root, once=False, max_ticks=2, duration_seconds=1,
                               poll_seconds=7, act=False,
                               environment=self.provider_env(),
                               resident_factory=RecordingResident, verify_owners=False)
        self.assertTrue(report["ok"], report.get("reason"))
        self.assertEqual(7, RecordingResident.last.kwargs["poll_seconds"])
        self.assertFalse(RecordingResident.last.kwargs["act"])
        self.assertEqual({"max_ticks": 2, "duration_seconds": 1}, report["ran"])

    def test_run_reports_a_resident_that_refuses_to_start(self) -> None:
        class Refusing:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                return {"ok": False, "reason": "RUNTIME_REFUSED",
                        "detail": "another live instance holds life authority lock on x"}

        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=Refusing, verify_owners=False)
        self.assertFalse(report["ok"])
        self.assertEqual("RUNTIME_REFUSED", report["reason"])
        self.assertEqual(life.EXIT_LEASE_HELD, report["exit_code"])

    @posix_only
    def test_run_fails_closed_on_a_second_writer_for_the_same_root(self) -> None:
        import activity_continuity as ac

        lease = ac.ActivityAuthorityLease(self.state_root)
        lease.acquire(timeout_seconds=0.0)
        try:
            report = life.life_run(self.root, once=True, environment=self.provider_env(),
                                   resident_factory=MustNotBeBuilt, verify_owners=False)
            self.assertEqual("LEASE_HELD", report["reason"])
            self.assertEqual(life.EXIT_LEASE_HELD, report["exit_code"])
            self.assertEqual("HELD", life.check_single_lease(self.state_root)["status"])
        finally:
            lease.release()
        self.assertEqual("FREE", life.check_single_lease(self.state_root)["status"])

    @posix_only
    def test_the_lease_probe_leaves_no_files_behind(self) -> None:
        import activity_continuity as ac

        lease = ac.ActivityAuthorityLease(self.state_root)
        lease.acquire(timeout_seconds=0.0)
        lease.release()
        # release() removes its marker; the lock file it created stays, and the probe must
        # neither create a new one nor touch the existing one.
        empty_parent = self.root / "one-day" / "life" / "state" / "never-used"
        self.assertFalse(empty_parent.exists())
        result = life.check_single_lease(
            self.root / "one-day" / "life" / "state" / "never-used")
        self.assertEqual("FREE", result["status"])
        self.assertFalse(empty_parent.exists(), "the lease probe created the state root")


# ---------------------------------------------------------------------------------------------
# no connect / zero write witnesses
# ---------------------------------------------------------------------------------------------


@unittest.skipIf(os.name == "nt", "需要 AF_UNIX（在 WSL/Linux 上运行）")
class NoProbeTests(PhoneLifeFixture):
    def test_preflight_never_connects_to_the_supply_socket(self) -> None:
        socket_path = Path(self.phone["supply_socket"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen(1)
        try:
            before = snapshot(self.root)
            report = life.life_preflight(self.root, environment=self.base_env)
            self.assertEqual(before, snapshot(self.root))
            self.assertTrue(report["checks"]["supply"]["present"])
            self.assertTrue(report["checks"]["supply"]["is_socket"])
            self.assertEqual("READY", report["checks"]["supply"]["status"])
            listener.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                listener.accept()
        finally:
            listener.close()

    def test_preflight_reports_a_non_socket_path_loudly(self) -> None:
        socket_path = Path(self.phone["supply_socket"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("not a socket", encoding="utf-8")
        report = life.life_preflight(self.root, environment=self.base_env)
        self.assertFalse(report["checks"]["supply"]["is_socket"])
        self.assertEqual("BLOCKED", report["checks"]["supply"]["status"])
        self.assertEqual("SUPPLY_PATH_IS_NOT_A_SOCKET",
                         report["checks"]["supply"]["detail"])
        run = life.life_run(self.root, once=True, environment=self.provider_env(),
                            resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual("SUPPLY_PATH_IS_NOT_A_SOCKET", run["reason"])


# ---------------------------------------------------------------------------------------------
# CLI: the two new verbs, and the flags they deliberately do not have
# ---------------------------------------------------------------------------------------------


class PhoneLifeCliTests(PhoneLifeFixture):
    def test_usage_lists_both_new_verbs(self) -> None:
        stream = []
        with mock.patch("sys.stderr.write", side_effect=stream.append):
            self.assertEqual(phone_cli.EXIT_OK, phone_cli.main(["--help"]))
        usage = "".join(stream)
        self.assertIn("life-preflight", usage)
        self.assertIn("life-run", usage)

    def test_life_preflight_cli_help_and_dispatch(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            phone_cli.main(["life-preflight", "--help"])
        self.assertEqual(0, caught.exception.code)
        fake = {"ok": True, "exit_code": life.EXIT_OK, "read_only": True}
        with mock.patch.object(life, "life_preflight", return_value=fake) as patched, \
                mock.patch("sys.stdout"):
            self.assertEqual(life.EXIT_OK,
                             phone_cli.main(["life-preflight", "--instance", str(self.root)]))
        patched.assert_called_once_with(Path(str(self.root)))

    def test_life_preflight_cli_maps_a_blocked_report_to_exit_1(self) -> None:
        fake = {"ok": False, "exit_code": life.EXIT_REFUSED}
        with mock.patch.object(life, "life_preflight", return_value=fake), \
                mock.patch("sys.stdout"):
            self.assertEqual(life.EXIT_REFUSED,
                             phone_cli.main(["life-preflight", "--instance", str(self.root)]))

    def test_life_run_cli_forwards_its_finite_bounds(self) -> None:
        fake = {"ok": True, "exit_code": life.EXIT_OK}
        with mock.patch.object(life, "life_run", return_value=fake) as patched, \
                mock.patch("sys.stdout"):
            code = phone_cli.main(["life-run", "--instance", str(self.root), "--once",
                                   "--max-ticks", "3", "--duration-seconds", "60",
                                   "--poll-seconds", "5", "--no-act"])
        self.assertEqual(life.EXIT_OK, code)
        kwargs = patched.call_args.kwargs
        self.assertTrue(kwargs["once"])
        self.assertEqual(3, kwargs["max_ticks"])
        self.assertEqual(60, kwargs["duration_seconds"])
        self.assertEqual(5, kwargs["poll_seconds"])
        self.assertFalse(kwargs["act"])

    def test_life_run_cli_default_bound_is_finite(self) -> None:
        fake = {"ok": True, "exit_code": life.EXIT_OK}
        with mock.patch.object(life, "life_run", return_value=fake) as patched, \
                mock.patch("sys.stdout"):
            phone_cli.main(["life-run", "--instance", str(self.root)])
        kwargs = patched.call_args.kwargs
        self.assertFalse(kwargs["once"])
        self.assertEqual(life.DEFAULT_RUN_DURATION_SECONDS, kwargs["duration_seconds"])
        self.assertIsNone(kwargs["max_ticks"])

    def test_cli_exposes_no_injection_or_window_flags(self) -> None:
        for forbidden in ("--new-run-window", "--enable-autonomy", "--resident-factory",
                          "--cognition", "--writer", "--force"):
            with self.assertRaises(SystemExit) as caught:
                phone_cli.main(["life-run", "--instance", str(self.root), forbidden])
            self.assertEqual(2, caught.exception.code, forbidden)

    def test_cli_never_passes_an_injected_factory(self) -> None:
        fake = {"ok": True, "exit_code": life.EXIT_OK}
        with mock.patch.object(life, "life_run", return_value=fake) as patched, \
                mock.patch("sys.stdout"):
            phone_cli.main(["life-run", "--instance", str(self.root), "--once"])
        self.assertNotIn("resident_factory", patched.call_args.kwargs)
        self.assertNotIn("verify_owners", patched.call_args.kwargs)


# ---------------------------------------------------------------------------------------------
# the three exact parser defects in one_day_life.main
# ---------------------------------------------------------------------------------------------


class OneDayLifeParserTests(unittest.TestCase):
    def test_help_succeeds_so_create_private_items_is_declared_once(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            odl.main(["--help"])
        self.assertEqual(0, caught.exception.code)

    def test_read_forwards_page_and_unclear(self) -> None:
        with mock.patch.object(odl, "read_page", return_value={"ok": True}) as patched:
            code = odl.main(["read", "--instance", "/tmp/never-touched", "--page", "2",
                             "--unclear", "这一页没懂"])
        self.assertEqual(0, code)
        self.assertEqual(2, patched.call_args.kwargs["page"])
        self.assertEqual("这一页没懂", patched.call_args.kwargs["unclear"])

    def test_prepare_accepts_an_explicit_supply_socket(self) -> None:
        with mock.patch.object(odl, "prepare", return_value={"ok": True}) as patched:
            code = odl.main(["prepare", "--instance", "/tmp/never-touched",
                             "--supply-socket", "/tmp/supply.sock",
                             "--governance-socket", "/tmp/supply.sock",
                             "--title", "t", "--create-private-items"])
        self.assertEqual(0, code)
        kwargs = patched.call_args.kwargs
        self.assertEqual("/tmp/supply.sock", kwargs["supply_socket"])
        self.assertEqual("/tmp/supply.sock", kwargs["governance_socket"])
        self.assertTrue(kwargs["create_private_items"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
