"""``scripts/day_reading_material.py`` 的离线测试：真实 Supply owner，模型全替身。

这里验证的是工具本身，不是产品生活链：

* 安装走**真实** ``lifesupply.service.life_supply``（进程内 Unix socket、真实 canonical db），
  新 artifact、标题、正文、版本都由 owner 自己回读；
* 安装只改该实例的两处阅读绑定元数据，旧材料/旧版本/账本/run 预算保持原样；
* 第二次 install 是幂等的：不新建 artifact、不改文件、不动阅读位置；
* ``verify`` 纯读，能识破绑定漂移和账本被重置；
* 缺失 root 拒绝创建；安装中途失败时 ``finally`` 停掉自己启动的 owner；
* 不启动手机 HTTP、不启动 resident、不访问网络、不调模型。

夹具根在 ``/tmp/nyaw05r.jdK3q/day-reading-material-*``（可用 ``NYAIRO_RC1_READING_B_ROOT``
覆盖），唯一创建、保留不清理；需要 POSIX（AF_UNIX + fcntl），原生 Windows 自动跳过。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply"),
               str(ROOT / "components" / "life" / "scripts"), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import day_reading_material as drm  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_request_budget as budget  # noqa: E402
import private_daily_items as pdi  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_RC1_READING_B_ROOT") or "/tmp/nyaw05r.jdK3q")
SUBJECT = "test:day-reading-material"
OLD_TITLE = "验收用测试阅读材料（合成）"
OLD_CONTENT = "验收夹具第一页：这段文字由验收脚本写入。\n%%PAGE%%\n验收夹具第二页。"
RUN_ID = "run:day-reading-test"
CEILING = "96"

posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX 与 fcntl（在 WSL/Linux 上运行）")


def _unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(BASE)))


def _call(socket_path, request, *, timeout: float = 15.0) -> dict:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise AssertionError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise AssertionError("owner answer is not an object")
    return answer


class OwnerServices:
    """真实 Life Supply owner：一个 canonical db、一个 socket、Governance + Workspace + Artifact."""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="day-reading-material-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, request: dict) -> dict:
        return _call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


class _SharedOwnerFactory:
    """Test injection: point the tool at the already-running real owner; never own or stop it."""

    def __init__(self, services: OwnerServices) -> None:
        self.services = services

    def __call__(self, root, *, generation, writer_instance):
        return drm.OwnerSession(str(self.services.socket_path), generation=generation, pid=None,
                                owned=False, reused=False,
                                stop_fn=lambda: {"stopped": False, "test_shared": True})


class _OwnedOwnerFactory:
    """Test injection: an owner session this tool *does* own; records that stop was called."""

    def __init__(self, services: OwnerServices) -> None:
        self.services = services
        self.stops: list[dict] = []

    def __call__(self, root, *, generation, writer_instance):
        def stop():
            self.stops.append({"generation": generation})
            return {"stopped": True, "test_owned": True}

        return drm.OwnerSession(str(self.services.socket_path), generation=generation, pid=4242,
                                owned=True, stop_fn=stop, log_path=str(root / "logs" / "supply.log"))


class InstanceFixture:
    """One temp phone root with a real owner, real artifacts and a persisted ledger."""

    def __init__(self) -> None:
        self.root = _unique_root("day-reading-material-")
        self.services = OwnerServices(self.root)
        self.port = lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT)
        self.workspace_id = self._provision_workspace()
        self.grant = self._issue_artifact_grant()
        self.old_id = self._create_artifact(OLD_TITLE, OLD_CONTENT, "old-reading")
        self.work_id = self._create_artifact("她自己的日常记录", "种子作品", "work")
        self.private_items_id, _ = pdi.create_document_artifact(
            self.port, workspace_id=self.workspace_id, title="私人日常事项",
            grant_id=self.grant)
        self.state_root = self.root / "one-day" / "life" / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        (self.state_root / "run").mkdir(parents=True, exist_ok=True)
        self._write_instances()
        self._write_ledger()

    def stop(self) -> None:
        self.services.stop()

    # -- real owner plumbing ---------------------------------------------------------------
    def _issue(self, capability, scope, target, authority_scope, use_mode, operation_id):
        answer = self.services.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
            "authority_scope": authority_scope, "use_mode": use_mode,
            "operation_id": operation_id}})
        assert answer.get("status") == "ALLOW", answer
        return str(answer["grant_id"])

    def _provision_workspace(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                            "issue:workspace:%s" % SUBJECT)
        answer = self.services.call({"op": "provision_workspace", "grant_id": grant,
                                     "operation_key": "day-reading:workspace:%s" % SUBJECT,
                                     "workspace": {"subject_id": SUBJECT}})
        assert answer.get("status") == "OK", answer
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:artifact:%s" % SUBJECT)

    def _create_artifact(self, title: str, content: str, key: str) -> str:
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title=title, content=content,
            grant_id=self.grant,
            operation_key=lap.operation_key_for("test", "fixture", key, SUBJECT))
        assert outcome.applied, outcome
        return str(outcome.artifact_id)

    # -- instance files --------------------------------------------------------------------
    def _environment(self) -> dict:
        nested_dir = self.root / "one-day"
        return {
            "CHIYO_LIFE_INSTALL_ROOT": str(nested_dir / "life"),
            "NYA02_RESIDENT_STATE": str(self.state_root),
            "LIFE_SUPPLY_SOCKET": str(self.services.socket_path),
            "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET": str(self.services.socket_path),
            "NYA02_SUBJECT": SUBJECT,
            "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT": SUBJECT,
            "NYA02_ARTIFACT_GRANT": self.grant,
            "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT": self.grant,
            "CHIYO_LIFE_SUPPLY_WORKSPACE_ID": self.workspace_id,
            "CHIYO_LIFE_WORK_ARTIFACT_ID": self.work_id,
            "CHIYO_LIFE_READING_ARTIFACT_ID": self.old_id,
            "CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID": self.private_items_id,
            "CHIYO_AUTONOMOUS_LIFE_ENABLED": "1",
            "CHIYO_AUTONOMOUS_RUN_ID": RUN_ID,
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": CEILING,
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": CEILING,
            "CHIYO_WORLD_HOME": str(self.root / "world"),
        }

    def _write_instances(self) -> None:
        nested_dir = self.root / "one-day"
        (nested_dir / "life" / "state").mkdir(parents=True, exist_ok=True)
        nested = {
            "schema_version": "life.one_day.v1",
            "kind": "life_one_day_instance",
            "instance_root": str(nested_dir),
            "subject_id": SUBJECT,
            "workspace_id": self.workspace_id,
            "artifact_id": self.work_id,
            "artifact_grant_id": self.grant,
            "world_home": str(self.root / "world"),
            "life_state_root": str(self.state_root),
            "reading_artifact_id": self.old_id,
            "private_items_artifact_id": self.private_items_id,
            "environment": self._environment(),
        }
        (nested_dir / "instance.json").write_text(
            json.dumps(nested, ensure_ascii=False, indent=2), encoding="utf-8")
        phone = {
            "schema_version": "phone.instance.v1",
            "kind": "phone_local_instance",
            "root": str(self.root),
            "world_home": str(self.root / "world"),
            "world_id": "world:test",
            "supply_socket": str(self.services.socket_path),
            "supply_generation": "test",
            "workspace_id": self.workspace_id,
            "artifact_grant_id": self.grant,
            "private_items_artifact_id": self.private_items_id,
            "reading_artifact_id": self.old_id,
            "work_artifact_id": self.work_id,
            "subject": SUBJECT,
            "features": {},
        }
        phone_service.write_instance(self.root, phone)

    def _write_ledger(self) -> None:
        ledger = budget.LifeRequestBudget(self.state_root, utc_day_max=96, run_window_max=96,
                                          run_id=RUN_ID)
        ledger.ensure_window()
        ledger.reserve()
        ledger.reserve()

    def ledger_calls(self) -> tuple:
        ledger = budget.LifeRequestBudget(self.state_root, utc_day_max=96, run_window_max=96,
                                          run_id=RUN_ID)
        view = ledger.view()
        return (view.get("utc_day_calls"), view.get("run_window_calls"))

    def file_bytes(self) -> dict[str, bytes]:
        return {
            "phone": (self.root / "instance.json").read_bytes(),
            "nested": (self.root / "one-day" / "instance.json").read_bytes(),
            "receipt": (drm._receipt_path(self.root).read_bytes()
                        if drm._receipt_path(self.root).exists() else b""),
        }


@posix_only
class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = InstanceFixture()
        self.addCleanup(self.fixture.stop)
        self.factory = _SharedOwnerFactory(self.fixture.services)

    def test_install_creates_the_material_reads_it_back_and_rebinds(self):
        report = drm.install(self.fixture.root, service_factory=self.factory)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["status"], "INSTALLED")
        self.assertEqual(report["exit_code"], 0)
        new_id = report["reading_artifact_id"]
        self.assertTrue(new_id)
        self.assertNotEqual(new_id, self.fixture.old_id)
        self.assertEqual(report["previous"]["artifact_id"], self.fixture.old_id)
        self.assertTrue(report["changed"]["artifact_created"])
        self.assertTrue(report["changed"]["binding_written"])
        self.assertFalse(report["model_called"])
        self.assertFalse(report["request_sent"])

        # The *owner* is the one that says what was stored.
        facts = drm._read_material(self.fixture.port, new_id)
        self.assertEqual(facts["title"], drm.MATERIAL_TITLE)
        self.assertEqual(facts["content"], drm.MATERIAL_EXCERPT)
        self.assertEqual(facts["content_sha256"], drm.material_content_sha256())
        self.assertEqual(facts["version_number"], 1)
        self.assertEqual(report["version_id"], facts["version_id"])

        phone = phone_service.load_instance(self.fixture.root)
        nested = json.loads((self.fixture.root / "one-day" / "instance.json").read_text(
            encoding="utf-8"))
        self.assertEqual(phone["reading_artifact_id"], new_id)
        self.assertEqual(nested["reading_artifact_id"], new_id)
        self.assertEqual(nested["environment"]["CHIYO_LIFE_READING_ARTIFACT_ID"], new_id)

        # The old material and its version history are untouched.
        old = drm._read_material(self.fixture.port, self.fixture.old_id)
        self.assertEqual(old["title"], OLD_TITLE)
        self.assertEqual(old["content"], OLD_CONTENT)
        self.assertEqual(old["version_id"], report["previous"]["version_id"])

        # Run identity, ceilings and the persisted ledger are unchanged.
        self.assertTrue(report["run"]["same_run_id"], report["run"])
        self.assertTrue(report["run"]["ceilings_unchanged"], report["run"])
        self.assertTrue(report["run"]["ledger_unchanged"], report["run"])
        self.assertEqual(self.fixture.ledger_calls(), (2, 2))
        self.assertTrue(drm._receipt_path(self.fixture.root).exists())

    def test_the_second_install_creates_nothing_and_writes_nothing(self):
        first = drm.install(self.fixture.root, service_factory=self.factory)
        self.assertTrue(first["ok"], first)
        artifacts_before = len(self.fixture.port.list_artifacts())
        versions_before = self.fixture.port.list_versions(first["reading_artifact_id"])
        before = self.fixture.file_bytes()
        ledger_before = self.fixture.ledger_calls()

        second = drm.install(self.fixture.root, service_factory=self.factory)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["status"], "ALREADY_INSTALLED")
        self.assertEqual(second["exit_code"], 0)
        self.assertFalse(second["changed"]["artifact_created"])
        self.assertFalse(second["changed"]["binding_written"])
        self.assertEqual(second["reading_artifact_id"], first["reading_artifact_id"])
        self.assertEqual(len(self.fixture.port.list_artifacts()), artifacts_before)
        versions_after = self.fixture.port.list_versions(first["reading_artifact_id"])
        self.assertEqual(versions_after["count"], versions_before["count"])
        self.assertEqual(versions_after["total_versions"], versions_before["total_versions"])
        self.assertEqual(self.fixture.file_bytes(), before)
        self.assertEqual(self.fixture.ledger_calls(), ledger_before)

    def test_a_missing_root_is_refused_and_never_created(self):
        missing = self.fixture.root / "does-not-exist"
        called = []

        def factory(*args, **kwargs):  # pragma: no cover - must never run
            called.append(True)
            raise AssertionError("no owner may be started for a missing root")

        report = drm.install(missing, service_factory=factory)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "ROOT_MISSING")
        self.assertEqual(called, [])
        self.assertFalse(missing.exists())

    def test_a_refused_create_still_stops_the_owned_owner(self):
        phone = phone_service.load_instance(self.fixture.root)
        phone["artifact_grant_id"] = "grant:not-issued"
        phone_service.write_instance(self.fixture.root, phone)
        before = self.fixture.file_bytes()
        owned = _OwnedOwnerFactory(self.fixture.services)

        report = drm.install(self.fixture.root, service_factory=owned)

        self.assertFalse(report["ok"], report)
        self.assertEqual(report["reason"], "CREATE_REFUSED")
        self.assertEqual(len(owned.stops), 1, owned.stops)
        self.assertFalse(report["changed"]["binding_written"])
        self.assertEqual(self.fixture.file_bytes(), before)
        self.assertFalse(drm._receipt_path(self.fixture.root).exists())

    def test_a_held_lease_refuses_before_starting_an_owner(self):
        called = []

        def factory(*args, **kwargs):  # pragma: no cover - must never run
            called.append(True)
            raise AssertionError("no owner may be started while the lease is held")

        report = drm.install(self.fixture.root, service_factory=factory,
                             lease_probe=lambda _root: {"status": "HELD"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "LEASE_HELD")
        self.assertEqual(called, [])


@posix_only
class VerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = InstanceFixture()
        self.addCleanup(self.fixture.stop)
        self.factory = _SharedOwnerFactory(self.fixture.services)
        installed = drm.install(self.fixture.root, service_factory=self.factory)
        self.assertTrue(installed["ok"], installed)
        self.install_report = installed

    def test_verify_reads_back_through_the_owner_and_writes_nothing(self):
        before = self.fixture.file_bytes()
        report = drm.verify(self.fixture.root, service_factory=self.factory)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["status"], "VERIFIED")
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["writes"])
        self.assertFalse(report["model_called"])
        self.assertEqual(report["readback"]["content_sha256"],
                         drm.material_content_sha256())
        self.assertEqual(report["readback"]["title"], drm.MATERIAL_TITLE)
        self.assertTrue(report["checks"]["phone_binding_points_to_material"])
        self.assertTrue(report["checks"]["nested_binding_points_to_material"])
        self.assertTrue(report["checks"]["nested_environment_points_to_material"])
        self.assertTrue(report["checks"]["previous_head_unchanged"])
        self.assertTrue(report["checks"]["previous_content_unchanged"])
        self.assertTrue(report["checks"]["same_run_id"])
        self.assertTrue(report["checks"]["ceilings_unchanged"])
        self.assertTrue(report["checks"]["ledger_not_reset"])
        self.assertEqual(self.fixture.file_bytes(), before)
        self.assertEqual(self.fixture.ledger_calls(), (2, 2))

    def test_verify_without_a_receipt_refuses_instead_of_guessing(self):
        drm._receipt_path(self.fixture.root).unlink()
        report = drm.verify(self.fixture.root, service_factory=self.factory)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "NO_INSTALL_RECEIPT")

    def test_verify_detects_a_drifted_phone_binding(self):
        phone = phone_service.load_instance(self.fixture.root)
        phone["reading_artifact_id"] = self.fixture.old_id
        phone_service.write_instance(self.fixture.root, phone)

        report = drm.verify(self.fixture.root, service_factory=self.factory)
        self.assertFalse(report["ok"])
        self.assertIn("phone_binding_points_to_material", report["failures"])
        self.assertEqual(report["exit_code"], 1)

    def test_verify_detects_a_reset_ledger(self):
        budget.budget_path(self.fixture.state_root).unlink()
        report = drm.verify(self.fixture.root, service_factory=self.factory)
        self.assertFalse(report["ok"])
        self.assertIn("ledger_not_reset", report["failures"])


if __name__ == "__main__":
    unittest.main()
