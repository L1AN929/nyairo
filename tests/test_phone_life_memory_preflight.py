#!/usr/bin/env python3
"""0.4 P: 手机 ``life-preflight`` / ``life-run`` 的 Memory 参与预检（UNBOUND/CONFIGURED/READY/BLOCKED）。

本文件只跑这一项新检查的 exact 用例，不跑旧全库、不跑真实模型、不碰任何真实实例：

* **UNBOUND 是有意的可选降级**：没绑 Memory 时不进 ``blocked``、不挡 ``run_ready``、不挡 run，
  但报告里必须出现 ``MEMORY_OPTIONAL_UNBOUND`` / ``NOT_BOUND_OPTIONAL_DEGRADATION`` ——
  "没绑记忆"永远不能被读成"她没有过去"。
* **"配置路径存在" != "健康可用"**：M0 文件在、能读，但控制账本回落到 M0 自身
  （生产 ``one_day_life.prepare`` 在没给 controls 时就是这样写 ``NYA02_MEMORY_CONTROLS_DB``）→
  治理投影不健康 → ``BLOCKED``，不能报 READY。
* **身份分域**：Memory persona 与手机 Supply workspace subject 是两个域
  （`one_day_life.prepare(memory_subject=...)` 允许不同），只分别报告、永不强制相等。
* **fail-closed**：缺文件 / 目录冒充数据库 / 非 SQLite / 账本不健康都具名 ``BLOCKED``；
  显式绑定但健康未证实（``CONFIGURED``）时 ``life-run`` 在构造 resident 之前返回
  ``MEMORY_HEALTH_UNVERIFIED``，``BLOCKED`` 时返回 ``MEMORY_BINDING_BROKEN``；
  ``UNBOUND`` 仍是可选降级，不挡 run。
* **零写**：fixture 根在 preflight 前后做 mode/size/mtime 快照必须相等；Memory 文件只以 SQLite
  ``mode=ro`` 打开，不建库、不建表、不迁移、不生成 ``-wal``/``-shm``。

夹具根默认落在系统 temp（``NYAIRO_V04_MEMORY_ROOT`` 可覆盖），**全部保留不删**：没有 unlink、
没有 rmtree、没有 TemporaryDirectory、没有全局 no-op unlink。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "scripts"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

try:  # the genuine M0 store imports ``fcntl``, so it only loads on POSIX.  The check never needs it.
    from app.evidence import EvidenceStore  # noqa: E402
    from life_experience_port import build_outcome_event, life_conversation_id  # noqa: E402

    M0_STORE_AVAILABLE = True
except Exception:  # pragma: no cover - Windows
    EvidenceStore = None
    build_outcome_event = None
    M0_STORE_AVAILABLE = False

from memory_control import MemoryControlLedger  # noqa: E402
from components.phone import life  # noqa: E402

posix_only = unittest.skipIf(
    os.name == "nt", "需要 POSIX：真实 M0 EvidenceStore 在模块层 import fcntl（产品检查不需要它）")

BASE = Path(os.environ.get("NYAIRO_V04_MEMORY_ROOT") or tempfile.gettempdir())

PHONE_SOCKET_GENERATION = "serve-1"
NESTED_SOCKET_GENERATION = "prepare-0"



def life_conversation(owner: str) -> str:
    """``life_experience_port.life_conversation_id``, with its literal fallback where that module
    cannot load (it imports ``fcntl`` through the M0 store)."""

    return life_conversation_id(owner) if M0_STORE_AVAILABLE else "life:" + owner


def write_stand_in_m0(path: Path, *, owner: str) -> None:
    """The M0 ``evidence_events`` table, column for column from
    ``components/native/src/app/evidence.py:176-226``, for platforms where the genuine
    ``EvidenceStore`` cannot be imported at all.  One verified Life outcome row is inserted so the
    read-only counter has something real to count; the POSIX-only test below proves the genuine
    store's own schema is accepted by the same check."""

    occurred = "2026-10-24T00:00:00+00:00"
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE evidence_events ("
            "event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, memory_owner TEXT NOT NULL,"
            " source_origin TEXT NOT NULL, delivery_status TEXT NOT NULL,"
            " epistemic_role TEXT NOT NULL, speaker TEXT NOT NULL, content TEXT NOT NULL,"
            " conversation_id TEXT NOT NULL, turn_id TEXT NOT NULL,"
            " primary_source_ref_id TEXT NOT NULL, source_refs_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO evidence_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ev-fixture-1", occurred, owner, "LIFE_VERIFIED_OUTCOME", "VERIFIED",
             "VERIFIED_EXECUTION_OUTCOME", "chiyo", "fixture verified outcome",
             life_conversation(owner), "life:fixture-outcome-1", "life:fixture-outcome-1",
             json.dumps([{"kind": "life_outcome", "id": "life:fixture-outcome-1"}]),
             occurred))
        connection.commit()
    finally:
        connection.close()


def unique_root(prefix: str) -> Path:
    """A retained, unique fixture root.  Nothing here ever deletes one."""

    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000000) % 100000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % (root,))
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


class MemoryPreflightFixture(unittest.TestCase):
    """One synthetic phone root + one nested Life instance, both real files on disk."""

    def setUp(self) -> None:
        self.root = unique_root("v04-memory-preflight-")
        self.root.mkdir(parents=True)
        (self.root / "run").mkdir(parents=True, exist_ok=True)
        state_root = self.root / "one-day" / "life" / "state"
        (state_root / "run").mkdir(parents=True, exist_ok=True)
        self.world_home = self.root / "one-day" / "world-home"
        self.world_home.mkdir(parents=True, exist_ok=True)
        self.state_root = state_root
        self.profile_home = self.root / "profile-home"
        self.profile_home.mkdir(parents=True, exist_ok=True)

        self.phone = {
            "schema_version": "phone.instance.v1",
            "kind": "phone_local_instance",
            "created_at": "2026-10-24T00:00:00Z",
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
            "features": {"CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0"},
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
            json.dumps({"enabled": bool(enabled), "changed_at": "2026-10-24T00:00:00Z",
                        "changed_by": "test"}), encoding="utf-8")

    def provider_env(self) -> dict:
        env = dict(self.base_env)
        env.update({"NYA02_MODEL_BASE_URL": "http://127.0.0.1:9/v1",
                    "NYA02_MODEL_NAME": "fixture-model",
                    "NYA02_MODEL_API_KEY": "fixture-placeholder-not-a-key"})
        return env

    # -- memory fixture (real M0 schema + real control ledger, both written once) ------------

    def make_memory(self, *, owner: str = "chiyo") -> dict:
        """A genuine M0 database plus a genuine control ledger, both inside the fixture root."""

        directory = self.root / "memory"
        directory.mkdir(parents=True, exist_ok=True)
        evidence = directory / "evidence.db"
        controls = directory / "controls.db"
        if evidence.exists() or controls.exists():
            raise AssertionError("memory fixture already exists under %s" % (directory,))
        if M0_STORE_AVAILABLE:
            EvidenceStore(evidence).append(build_outcome_event(
                subject=owner, source_ref="life:fixture-outcome-1",
                text="fixture verified outcome", occurred_at="2026-10-24T00:00:00+00:00"))
        else:
            write_stand_in_m0(evidence, owner=owner)
        MemoryControlLedger(controls, evidence, owner=owner,
                            conversation=life_conversation(owner)).initialize()
        return {"directory": directory, "evidence": evidence, "controls": controls}

    def bind_memory(self, *, owner: str = "chiyo", with_controls: bool = True,
                    database: Path | None = None) -> dict:
        files = self.make_memory(owner=owner)
        environment = {"NYA02_MEMORY_DB": str(database or files["evidence"]),
                       "NYA02_MEMORY_SUBJECT": owner}
        if with_controls:
            environment["NYA02_MEMORY_CONTROLS_DB"] = str(files["controls"])
        self.write_nested(environment=environment)
        return files

    def memory_env(self) -> dict:
        return life.map_life_environment(self.phone, self.nested, base_env=self.base_env)


# ---------------------------------------------------------------------------------------------
# UNBOUND: the deliberate, optional degradation
# ---------------------------------------------------------------------------------------------


class UnboundMemoryTests(MemoryPreflightFixture):
    def test_preflight_reports_memory_unbound_as_an_optional_degradation(self) -> None:
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("UNBOUND", memory["status"])
        self.assertEqual("MEMORY_OPTIONAL_UNBOUND", memory["reason"])
        self.assertTrue(memory["optional"])
        self.assertTrue(memory["degraded"])
        self.assertFalse(memory["blocking"])
        self.assertFalse(memory["recall_ready"])
        self.assertFalse(memory["path_configured"])
        self.assertIsNone(memory["database"])
        self.assertIn("PAST_SOURCE_NOT_WIRED", memory["detail"])
        self.assertNotIn("memory", report["blocked"])
        self.assertEqual("UNBOUND", report["assembly"]["memory_status"])
        self.assertFalse(report["assembly"]["memory_bound"])
        self.assertTrue(report["assembly"]["memory_degraded"])
        self.assertTrue(report["assembly"]["memory_optional"])
        self.assertEqual("NOT_BOUND_OPTIONAL_DEGRADATION", report["memory"]["participation"])
        self.assertIn("memory table creation or migration",
                      report["verified_claims"]["not_performed"])

    def test_memory_unbound_does_not_change_the_no_model_provider_refusal(self) -> None:
        unbound = life.life_run(self.root, once=True, environment=self.base_env,
                                resident_factory=MustNotBeBuilt)
        self.assertEqual("NO_MODEL_PROVIDER", unbound["reason"])
        self.assertEqual(life.EXIT_BLOCKED, unbound["exit_code"])
        self.bind_memory()
        bound = life.life_run(self.root, once=True, environment=self.base_env,
                              resident_factory=MustNotBeBuilt)
        self.assertEqual("NO_MODEL_PROVIDER", bound["reason"])
        self.assertEqual(life.EXIT_BLOCKED, bound["exit_code"])
        self.assertFalse(bound["ok"])
        self.assertFalse(bound["model_called"])
        self.assertEqual(0, bound["processes_started"])

    def test_run_reports_memory_unbound_without_blocking_the_limited_run(self) -> None:
        before = snapshot(self.root)
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=RecordingResident, verify_owners=False)
        self.assertEqual(before, snapshot(self.root), "a memory-unbound run wrote something")
        self.assertTrue(report["ok"])
        self.assertEqual(life.EXIT_OK, report["exit_code"])
        self.assertEqual("UNBOUND", report["memory"]["status"])
        self.assertEqual("NOT_BOUND_OPTIONAL_DEGRADATION", report["memory"]["participation"])
        self.assertFalse(report["model_called"])
        self.assertEqual(0, report["model_calls"])
        self.assertIn("Memory participation=NOT_BOUND_OPTIONAL_DEGRADATION", report["note"])
        self.assertTrue(RecordingResident.last.started)
        self.assertTrue(RecordingResident.last.stopped)


# ---------------------------------------------------------------------------------------------
# A recorded path is not health: READY only when M0 and the ledger really read back
# ---------------------------------------------------------------------------------------------


class MemoryHealthTests(MemoryPreflightFixture):
    def test_preflight_is_ready_only_when_m0_and_the_ledger_are_readable(self) -> None:
        files = self.bind_memory()
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("READY", memory["status"])
        self.assertIsNone(memory["reason"])
        self.assertTrue(memory["recall_ready"])
        self.assertFalse(memory["degraded"])
        self.assertFalse(memory["blocking"])
        self.assertTrue(memory["path_configured"])
        self.assertTrue(memory["path_exists"])
        self.assertTrue(memory["is_file"])
        self.assertTrue(memory["readable"])
        self.assertEqual("ok", memory["integrity"])
        self.assertTrue(memory["m0_table_present"])
        self.assertTrue(memory["governance_healthy"])
        self.assertEqual(1, memory["life_outcomes"])
        self.assertEqual(0, memory["controls"])
        self.assertEqual(str(files["evidence"]), memory["database"])
        self.assertEqual(str(files["controls"]), memory["controls_database"])
        self.assertEqual("NYA02_MEMORY_DB", memory["database_env"])
        self.assertEqual("NYA02_MEMORY_CONTROLS_DB", memory["controls_env"])
        self.assertFalse(memory["controls_defaulted"])
        self.assertEqual("chiyo", memory["owner"])
        self.assertIsNone(memory["subject_matches_phone"])  # NOT_COMPARABLE by design
        self.assertEqual("chiyo", memory["phone_subject"])
        self.assertEqual("life:chiyo", memory["conversation"])
        self.assertEqual("read_only_sqlite_mode_ro", memory["probe"])
        self.assertFalse(memory["writes"])
        self.assertFalse(memory["created"])
        self.assertNotIn("memory", report["blocked"])
        self.assertEqual("READY", report["assembly"]["memory_status"])
        self.assertTrue(report["assembly"]["memory_recall_ready"])
        self.assertEqual("BOUND_READY_READONLY_VERIFIED", report["memory"]["participation"])

    def test_a_present_database_alone_is_not_health_because_the_ledger_defaults_to_it(self) -> None:
        # Exactly what ``one_day_life.prepare`` records when no controls database is given.
        files = self.bind_memory(with_controls=False)
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertTrue(memory["path_exists"])
        self.assertTrue(memory["readable"])
        self.assertTrue(memory["m0_table_present"])
        self.assertTrue(memory["controls_defaulted"])
        self.assertEqual(str(files["evidence"]), memory["controls_database"])
        self.assertFalse(memory["governance_healthy"])
        self.assertFalse(memory["recall_ready"])
        self.assertEqual("BLOCKED", memory["status"])
        self.assertEqual("MEMORY_CONTROLS_UNHEALTHY", memory["reason"])
        self.assertTrue(memory["blocking"])
        self.assertIn("memory", report["blocked"])
        self.assertFalse(report["ok"])
        self.assertEqual("BOUND_BUT_UNUSABLE", report["memory"]["participation"])

    def test_missing_database_file_is_blocked_not_unbound(self) -> None:
        absent = self.root / "memory" / "absent.db"
        self.write_nested(environment={"NYA02_MEMORY_DB": str(absent),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("BLOCKED", memory["status"])
        self.assertEqual("MEMORY_DB_MISSING", memory["reason"])
        self.assertTrue(memory["path_configured"])
        self.assertFalse(memory["path_exists"])
        self.assertFalse(memory["degraded"])
        self.assertTrue(memory["blocking"])
        self.assertIn("memory", report["blocked"])

    def test_a_non_sqlite_file_is_blocked(self) -> None:
        directory = self.root / "memory"
        directory.mkdir(parents=True, exist_ok=True)
        impostor = directory / "not-a-database.db"
        impostor.write_text("this is not a database", encoding="utf-8")
        self.write_nested(environment={"NYA02_MEMORY_DB": str(impostor),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("BLOCKED", memory["status"])
        self.assertEqual("MEMORY_DB_UNREADABLE", memory["reason"])
        self.assertTrue(memory["path_exists"])
        self.assertTrue(memory["is_file"])
        self.assertIsNone(memory["m0_table_present"])
        self.assertIn("error", memory)

    def test_a_directory_in_place_of_the_database_is_blocked(self) -> None:
        directory = self.root / "memory"
        directory.mkdir(parents=True, exist_ok=True)
        self.write_nested(environment={"NYA02_MEMORY_DB": str(directory),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("BLOCKED", memory["status"])
        self.assertEqual("MEMORY_DB_NOT_A_FILE", memory["reason"])
        self.assertTrue(memory["path_exists"])
        self.assertFalse(memory["is_file"])

    def test_a_different_memory_persona_is_legal_not_cross_bound(self) -> None:
        # ``one_day_life.prepare(memory_subject=...)`` lets the Memory persona and the Supply
        # workspace subject be different domains; they are reported, never equated.  This fixture
        # is a real M0 database whose owner is that other persona (not "another instance pulled in":
        # the owner is whatever *this* environment records).
        self.bind_memory(owner="chiyo.persona")
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("READY", memory["status"])
        self.assertIsNone(memory["reason"])
        self.assertIsNone(memory["subject_matches_phone"])
        self.assertEqual("chiyo.persona", memory["owner"])
        self.assertEqual("NYA02_MEMORY_SUBJECT", memory["owner_env"])
        self.assertEqual("chiyo", memory["phone_subject"])
        self.assertEqual("life:chiyo.persona", memory["conversation"])
        self.assertEqual(1, memory["life_outcomes"])
        self.assertTrue(memory["governance_healthy"])
        self.assertTrue(memory["recall_ready"])
        self.assertNotIn("memory", report["blocked"])
        self.assertEqual("READY", report["memory"]["status"])
        self.assertEqual("chiyo.persona", report["memory"]["owner"])

    def test_an_unanswered_health_probe_is_configured_not_ready(self) -> None:
        self.bind_memory()
        env = self.memory_env()
        self.assertEqual(str(self.root / "memory" / "evidence.db"), env["NYA02_MEMORY_DB"])

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("read-only probe unavailable")

        memory = life.check_memory(env, phone=self.phone, probe=unavailable)
        self.assertEqual("CONFIGURED", memory["status"])
        self.assertEqual("MEMORY_HEALTH_UNVERIFIED", memory["reason"])
        self.assertTrue(memory["path_configured"])
        self.assertTrue(memory["degraded"])
        self.assertFalse(memory["blocking"])
        self.assertFalse(memory["recall_ready"])
        self.assertIn("probe unavailable", memory["detail"])

    def test_the_check_only_reads_the_names_the_resident_reads(self) -> None:
        files = self.make_memory()
        # An invented spelling is not a Memory binding: no guessing, no extra env name.
        self.write_nested(environment={"NYA02_LIFE_MEMORY_DB": str(files["evidence"]),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        invented = life.life_preflight(self.root, environment=self.base_env)
        self.assertEqual("UNBOUND", invented["checks"]["memory"]["status"])
        self.assertNotIn("NYA02_LIFE_MEMORY_DB", invented["bindings"])
        # The resident's own second spelling is read (``CHIYO_LIFE_MEMORY_DB``).
        self.write_nested(environment={"CHIYO_LIFE_MEMORY_DB": str(files["evidence"]),
                                       "CHIYO_LIFE_MEMORY_CONTROLS_DB": str(files["controls"])})
        fallback = life.life_preflight(self.root, environment=self.base_env)
        memory = fallback["checks"]["memory"]
        self.assertEqual("READY", memory["status"])
        self.assertEqual("CHIYO_LIFE_MEMORY_DB", memory["database_env"])
        self.assertEqual("CHIYO_LIFE_MEMORY_CONTROLS_DB", memory["controls_env"])

    def test_preflight_opens_memory_read_only_and_creates_nothing(self) -> None:
        self.bind_memory()
        memory_dir = self.root / "memory"
        before = snapshot(self.root)
        names_before = sorted(path.name for path in memory_dir.iterdir())
        report = life.life_preflight(self.root, environment=self.base_env)
        self.assertEqual("READY", report["checks"]["memory"]["status"])
        self.assertEqual(before, snapshot(self.root), "the memory check wrote something")
        self.assertEqual(names_before, sorted(path.name for path in memory_dir.iterdir()))
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(any(name.endswith(suffix) for name in names_before),
                             "a read-only probe must not leave %s" % (suffix,))

    def test_an_operator_shell_export_cannot_bind_memory(self) -> None:
        files = self.make_memory()
        stray = dict(self.base_env)
        stray.update({"NYA02_MEMORY_DB": str(files["evidence"]),
                      "NYA02_MEMORY_CONTROLS_DB": str(files["controls"])})
        report = life.life_preflight(self.root, environment=stray)
        self.assertEqual("UNBOUND", report["checks"]["memory"]["status"])
        self.assertNotIn("NYA02_MEMORY_DB", life.map_life_environment(
            self.phone, self.nested, base_env=stray))

    def test_a_damaged_ledger_is_distinguishable_from_a_missing_one(self) -> None:
        files = self.make_memory()
        # A ledger file that exists but has no ``memory_controls`` table is damage, not absence.
        damaged = files["directory"] / "damaged-controls.db"
        sqlite3.connect(damaged).close()
        self.write_nested(environment={"NYA02_MEMORY_DB": str(files["evidence"]),
                                       "NYA02_MEMORY_CONTROLS_DB": str(damaged),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        damaged_report = life.life_preflight(self.root, environment=self.base_env)
        damaged_memory = damaged_report["checks"]["memory"]
        self.assertEqual("BLOCKED", damaged_memory["status"])
        self.assertEqual("MEMORY_CONTROLS_UNHEALTHY", damaged_memory["reason"])
        self.assertTrue(damaged_memory["controls_exists"])
        self.assertFalse(damaged_memory["controls_defaulted"])

        absent = files["directory"] / "absent-controls.db"
        self.write_nested(environment={"NYA02_MEMORY_CONTROLS_DB": str(absent)})
        absent_report = life.life_preflight(self.root, environment=self.base_env)
        absent_memory = absent_report["checks"]["memory"]
        self.assertEqual("BLOCKED", absent_memory["status"])
        self.assertEqual("MEMORY_CONTROLS_DB_MISSING", absent_memory["reason"])
        self.assertNotEqual(damaged_memory["reason"], absent_memory["reason"])

    def test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one(self) -> None:
        # Real damage, not a missing file: the M0 database still opens ``mode=ro`` and still has its
        # ``evidence_events`` table, but ``PRAGMA quick_check`` reports a problem (a torn copy /
        # truncated page).  That is the ``MEMORY_DB_INTEGRITY_FAILURE`` tier and it must not be
        # reported as "unbound", "missing" or "unreadable".
        directory = self.root / "memory"
        directory.mkdir(parents=True, exist_ok=True)
        evidence = directory / "torn.db"
        controls = directory / "torn-controls.db"
        write_stand_in_m0(evidence, owner="chiyo")
        MemoryControlLedger(controls, evidence, owner="chiyo",
                            conversation=life_conversation("chiyo")).initialize()
        payload = evidence.read_bytes()
        evidence.write_bytes(payload[:-100])  # damage the M0 file in place; nothing is deleted
        self.assertLess(evidence.stat().st_size, len(payload))
        self.write_nested(environment={"NYA02_MEMORY_DB": str(evidence),
                                       "NYA02_MEMORY_CONTROLS_DB": str(controls),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        before = snapshot(self.root)
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual(before, snapshot(self.root), "the preflight repaired or wrote the fixture")
        self.assertEqual("BLOCKED", memory["status"])
        self.assertEqual("MEMORY_DB_INTEGRITY_FAILURE", memory["reason"])
        self.assertTrue(memory["path_exists"])
        self.assertTrue(memory["is_file"])
        self.assertTrue(memory["readable"])
        self.assertNotEqual("ok", str(memory["integrity"]).lower())
        self.assertTrue(memory["m0_table_present"])
        self.assertFalse(memory["recall_ready"])
        self.assertTrue(memory["blocking"])
        self.assertIn("memory", report["blocked"])
        self.assertEqual("BOUND_BUT_UNUSABLE", report["memory"]["participation"])

    @posix_only
    def test_a_genuine_m0_store_database_is_ready(self) -> None:
        # The genuine ``EvidenceStore`` schema (real migrations, WAL) must be accepted as it is.
        directory = self.root / "memory"
        directory.mkdir(parents=True, exist_ok=True)
        evidence = directory / "genuine.db"
        controls = directory / "genuine-controls.db"
        EvidenceStore(evidence).append(build_outcome_event(
            subject="chiyo", source_ref="life:genuine-outcome-1",
            text="genuine verified outcome", occurred_at="2026-10-24T00:00:00+00:00"))
        MemoryControlLedger(controls, evidence, owner="chiyo",
                            conversation=life_conversation("chiyo")).initialize()
        self.write_nested(environment={"NYA02_MEMORY_DB": str(evidence),
                                       "NYA02_MEMORY_CONTROLS_DB": str(controls),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        report = life.life_preflight(self.root, environment=self.base_env)
        memory = report["checks"]["memory"]
        self.assertEqual("READY", memory["status"])
        self.assertTrue(memory["governance_healthy"])
        self.assertEqual(1, memory["life_outcomes"])


# ---------------------------------------------------------------------------------------------
# life-run: a broken configured Memory is never silently ignored
# ---------------------------------------------------------------------------------------------


class RunMemoryGateTests(MemoryPreflightFixture):
    def test_run_refuses_when_memory_is_bound_but_unusable(self) -> None:
        self.write_nested(environment={"NYA02_MEMORY_DB": str(self.root / "memory" / "gone.db"),
                                       "NYA02_MEMORY_SUBJECT": "chiyo"})
        before = snapshot(self.root)
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual(before, snapshot(self.root), "a refused run wrote something")
        self.assertFalse(report["ok"])
        self.assertEqual("MEMORY_BINDING_BROKEN", report["reason"])
        self.assertIn("MEMORY_DB_MISSING", report["detail"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])
        self.assertEqual("BLOCKED", report["memory"]["status"])
        self.assertEqual("BOUND_BUT_UNUSABLE", report["memory"]["participation"])
        self.assertFalse(report["model_called"])
        self.assertEqual(0, report["processes_started"])

    def test_run_refuses_when_memory_health_is_unverified(self) -> None:
        # Explicitly bound and present, but the health probe cannot answer: that is **not** the
        # optional ``UNBOUND`` degradation, so the run must not start on an unverified past.
        self.bind_memory()
        before = snapshot(self.root)
        with mock.patch.object(life, "_memory_probe",
                               side_effect=RuntimeError("read-only probe unavailable")):
            report = life.life_run(self.root, once=True, environment=self.provider_env(),
                                   resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual(before, snapshot(self.root), "a refused run wrote something")
        self.assertFalse(report["ok"])
        self.assertEqual("MEMORY_HEALTH_UNVERIFIED", report["reason"])
        self.assertIn("unavailable", report["detail"])
        self.assertEqual("CONFIGURED", report["memory"]["status"])
        self.assertEqual("BOUND_HEALTH_UNVERIFIED", report["memory"]["participation"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])
        self.assertFalse(report["model_called"])
        self.assertEqual(0, report["processes_started"])

    def test_run_refuses_an_unresolved_memory_binding_before_a_resident_exists(self) -> None:
        # ``UNKNOWN`` is the reserved "the binding itself could not be resolved" tier: no shipped
        # path produces it today (every failure lands on a named ``BLOCKED`` or on ``CONFIGURED``),
        # and if one ever does, ``life_run`` must fail closed exactly like ``CONFIGURED`` — before a
        # resident exists, never as a silent "no past".
        self.assertEqual("UNRESOLVED", life.MEMORY_PARTICIPATION[life.UNKNOWN])
        unresolved = {"status": life.UNKNOWN, "reason": None, "detail": "binding unresolved",
                      "optional": True, "degraded": True, "blocking": False,
                      "path_configured": True, "recall_ready": False, "database": None,
                      "controls_database": None, "controls_defaulted": False, "owner": "chiyo",
                      "phone_subject": "chiyo", "conversation": None, "life_outcomes": None,
                      "governance_healthy": None, "error": None}
        before = snapshot(self.root)
        with mock.patch.object(life, "check_memory", return_value=dict(unresolved)):
            report = life.life_run(self.root, once=True, environment=self.provider_env(),
                                   resident_factory=MustNotBeBuilt, verify_owners=False)
        self.assertEqual(before, snapshot(self.root), "a refused run wrote something")
        self.assertFalse(report["ok"])
        self.assertEqual("MEMORY_HEALTH_UNVERIFIED", report["reason"])
        self.assertEqual("UNKNOWN", report["memory"]["status"])
        self.assertEqual("UNRESOLVED", report["memory"]["participation"])
        self.assertEqual(life.EXIT_BLOCKED, report["exit_code"])
        self.assertFalse(report["model_called"])
        self.assertEqual(0, report["processes_started"])
        self.assertNotIn("resident", report)

    def test_run_records_a_ready_memory_without_claiming_a_model_call(self) -> None:
        self.bind_memory()
        report = life.life_run(self.root, once=True, environment=self.provider_env(),
                               resident_factory=RecordingResident, verify_owners=False)
        self.assertTrue(report["ok"])
        self.assertEqual(life.EXIT_OK, report["exit_code"])
        self.assertEqual("READY", report["memory"]["status"])
        self.assertEqual("BOUND_READY_READONLY_VERIFIED", report["memory"]["participation"])
        self.assertFalse(report["model_called"])
        self.assertEqual(0, report["model_calls"])
        self.assertIn("Memory participation=BOUND_READY_READONLY_VERIFIED", report["note"])
        self.assertEqual(str(self.state_root), report["resident"]["state_root"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
