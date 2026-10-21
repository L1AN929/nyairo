#!/usr/bin/env python3
"""v0.4 A: Memory 产品化接缝（LifeMemoryService / 只读检索 / 显式实例绑定 / consumer owner）。

本文件只跑本阶段新增的 A 面用例，不跑旧全库、不碰任何真实实例、不做真实模型调用：

* **未绑定是具名的 UNBOUND**，不是空库：``status``/``list_experiences``/``context`` 都必须
  带状态与原因码，且绝不创建任何文件。
* **GET 零写**：``list_experiences`` / ``read_experience`` / ``context`` 前后对 fixture 根做
  mode/size/mtime 快照必须相等，``memory/`` 下不许冒出 ``-wal``/``-shm``/``-journal``；
  ``EvidenceStore.__init__`` 的建库/迁移/chmod 一次都不许发生。
* **真正检索到较旧的相关经历**：最近 6 条都不相关时，``query`` 仍必须命中更早的两条。
* **owner 分域**：Memory owner 只来自绑定本身；另一个 owner 的 id 读出来是 NOT_FOUND 而不是正文。
* **治理阻断正文**：DELETE 之后该经历既不出现在列表/上下文里，``read_experience`` 也不再返回旧正文。
* **故障具名**：缺库、账本缺表、账本不可投影都是具名 BLOCKED/UNAVAILABLE，``experiences`` 是 ``None``。
* **consumer 用 port.subject**，不用 handoff 的 ``subject_id``；``owner_confirmed_context`` 只有
  型别正确、引用真实且 ``outcome_confirmed is True`` 时才有界进入正文，模型 self_note 不是事实。
* **显式 enable 只在这一个入口写盘**：先备份配置、只写三个 Memory 名、幂等、不覆盖已有显式绑定、
  不重建不健康库、不动 run/预算、不启动服务。

夹具根默认落在系统 temp（``NYAIRO_V04_MEMORY_PRODUCT_ROOT`` 可覆盖），**全部保留不删**：没有
unlink、没有 rmtree、没有 TemporaryDirectory、没有全局 no-op unlink。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "scripts"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

try:  # the genuine M0 store imports ``fcntl``, so it only loads on POSIX
    from app.evidence import EvidenceStore  # noqa: E402
    from life_experience_port import (  # noqa: E402
        LifeExperiencePort,
        build_outcome_event,
        life_conversation_id,
    )

    PORT_AVAILABLE = True
except Exception:  # pragma: no cover - Windows
    EvidenceStore = None
    LifeExperiencePort = None
    build_outcome_event = None
    PORT_AVAILABLE = False

from memory_control import MemoryControlLedger  # noqa: E402
from components.phone import life as phone_life  # noqa: E402
try:  # the consumer imports the boundary port, which imports the M0 store (``fcntl``, POSIX only)
    from life_experience_memory_consumer import (  # noqa: E402
        DISPOSITION_ACCEPT,
        DISPOSITION_DEFER,
        MemoryAdmissionExperienceConsumer,
    )

    CONSUMER_AVAILABLE = True
except Exception:  # pragma: no cover - Windows
    DISPOSITION_ACCEPT = "ACCEPT"
    DISPOSITION_DEFER = "DEFER"
    MemoryAdmissionExperienceConsumer = None
    CONSUMER_AVAILABLE = False

from life_memory_service import LifeMemoryService, memory_service_from_env  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_V04_MEMORY_PRODUCT_ROOT") or tempfile.gettempdir())

owner_bound_only = unittest.skipIf(
    not (PORT_AVAILABLE and CONSUMER_AVAILABLE),
    "真实 M0 EvidenceStore / 边界 port 在模块层 import fcntl，只在 POSIX 可用")
windows_only = unittest.skipIf(
    PORT_AVAILABLE, "只在 M0 store 无法 import 的平台上才有意义（Windows）")


# ---------------------------------------------------------------------------------------------
# retained fixtures (nothing here ever deletes one)
# ---------------------------------------------------------------------------------------------


def unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000000) % 100000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % (root,))
    return root


def snapshot(root: Path) -> dict:
    """mode / size / mtime of everything under ``root`` —— the zero-write witness."""

    out: dict = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            info = path.lstat()
        except OSError:
            out[str(path.relative_to(root))] = ("MISSING", 0, 0)
            continue
        out[str(path.relative_to(root))] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return out


def _iso(minutes: int) -> str:
    base = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).isoformat()


def build_memory(root: Path, *, owner: str = "chiyo", rows=()) -> dict:
    """One genuine M0 database plus a genuine, *separate* control ledger."""

    directory = Path(root) / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    evidence = directory / "evidence.db"
    controls = directory / "controls.db"
    store = EvidenceStore(evidence)
    for row in rows:
        store.append(build_outcome_event(
            subject=owner, source_ref=row["source_ref"], text=row["text"],
            occurred_at=row["occurred_at"], refs=list(row.get("refs") or [])))
    MemoryControlLedger(controls, evidence, owner=owner,
                        conversation=life_conversation_id(owner)).initialize()
    return {"directory": directory, "evidence": evidence, "controls": controls, "owner": owner}


def memory_env(binding: dict, *, owner: str | None = None, controls: bool = True) -> dict:
    env = {"NYA02_MEMORY_DB": str(binding["evidence"]),
           "NYA02_MEMORY_SUBJECT": owner or binding["owner"]}
    if controls:
        env["NYA02_MEMORY_CONTROLS_DB"] = str(binding["controls"])
    return env


def write_nested(root: Path, environment: dict | None = None) -> dict:
    """A minimal but genuine ``one-day/instance.json`` (the only thing ``enable`` needs)."""

    (Path(root) / "one-day").mkdir(parents=True, exist_ok=True)
    default = {
        "CHIYO_LIFE_INSTALL_ROOT": str(Path(root) / "one-day" / "life"),
        "NYA02_RESIDENT_STATE": str(Path(root) / "one-day" / "life" / "state"),
        "NYA02_SUBJECT": "chiyo",
        "CHIYO_AUTONOMOUS_RUN_ID": "run:existing-window",
        "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "96",
        "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "96",
    }
    default.update(environment or {})
    payload = {"schema_version": "life.one_day.v1", "kind": "one_day_life_instance",
               "instance_root": str(Path(root) / "one-day"), "subject_id": "chiyo",
               "environment": default}
    (Path(root) / "one-day" / "instance.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def nested_payload(root: Path) -> dict:
    return json.loads((Path(root) / "one-day" / "instance.json").read_text(encoding="utf-8"))


SHOPPING_ROWS = [
    {"source_ref": "lexp:shop-%d" % index,
     "text": "在房间里整理购物袋（第%d次）" % index,
     "occurred_at": _iso(index)}
    for index in range(6)
]
#: Two genuinely *older* experiences, about something else entirely.
OLDER_RELEVANT_ROWS = [
    {"source_ref": "lexp:old-recipe",
     "text": "读完了三十页笔记，写下一条做法：先复述再提问",
     "occurred_at": _iso(-120),
     "refs": [("artifact", "v04:artifact-notes"), ("artifactversion", "v04:version-2")]},
    {"source_ref": "lexp:old-reading",
     "text": "复习阅读笔记，把不懂的句子抄下来",
     "occurred_at": _iso(-60)},
]


# ---------------------------------------------------------------------------------------------
# status: unbound / ready / named failures
# ---------------------------------------------------------------------------------------------


class StatusTests(unittest.TestCase):
    def test_unbound_is_named_and_creates_nothing(self) -> None:
        root = unique_root("v04-mem-unbound-")
        root.mkdir(parents=True)
        before = snapshot(root)
        service = memory_service_from_env({})
        self.assertIsInstance(service, LifeMemoryService)
        status = service.status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "UNBOUND")
        self.assertEqual(status["data"]["binding_status"], "UNBOUND")
        self.assertEqual(status["data"]["recall_ready"], False)
        self.assertIsNone(status["data"]["owner"])
        self.assertEqual(status["data"]["participation"], "NOT_BOUND_OPTIONAL_DEGRADATION")
        listed = service.list_experiences()
        self.assertEqual(listed["status"], "UNBOUND")
        self.assertIsNone(listed["data"]["experiences"], "an unbound service must not say []")
        self.assertEqual(service.context()["status"], "UNBOUND")
        self.assertEqual(service.read_experience("ev-1")["status"], "UNBOUND")
        self.assertEqual(snapshot(root), before)

    @owner_bound_only
    def test_ready_status_reports_the_bound_owner(self) -> None:
        root = unique_root("v04-mem-ready-")
        root.mkdir(parents=True)
        binding = build_memory(root, rows=SHOPPING_ROWS[:1])
        service = memory_service_from_env(memory_env(binding))
        status = service.status()
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["status"], "READY")
        self.assertEqual(status["data"]["owner"], "chiyo")
        self.assertTrue(status["data"]["recall_ready"])
        self.assertEqual(status["data"]["participation"], "BOUND_READY_READONLY_VERIFIED")
        self.assertEqual(status["data"]["controls"], 0)

    @owner_bound_only
    def test_a_missing_database_is_a_named_failure_not_an_empty_library(self) -> None:
        root = unique_root("v04-mem-missing-")
        root.mkdir(parents=True)
        missing = root / "memory" / "evidence.db"
        service = memory_service_from_env({"NYA02_MEMORY_DB": str(missing),
                                           "NYA02_MEMORY_CONTROLS_DB": str(missing),
                                           "NYA02_MEMORY_SUBJECT": "chiyo"})
        status = service.status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "BLOCKED")
        self.assertEqual(status["data"]["reason"], "MEMORY_DB_MISSING")
        listed = service.list_experiences()
        self.assertEqual(listed["status"], "BLOCKED")
        self.assertIsNone(listed["data"]["experiences"])
        self.assertEqual(listed["reason"], "MEMORY_DB_MISSING")
        self.assertFalse(missing.exists(), "a read path must not create the database")

    @owner_bound_only
    def test_a_ledger_defaulting_to_m0_is_named(self) -> None:
        root = unique_root("v04-mem-nodefaults-")
        root.mkdir(parents=True)
        binding = build_memory(root, rows=SHOPPING_ROWS[:1])
        service = memory_service_from_env(memory_env(binding, controls=False))
        status = service.status()
        self.assertEqual(status["status"], "BLOCKED")
        self.assertEqual(status["data"]["reason"], "MEMORY_CONTROLS_UNHEALTHY")
        self.assertTrue(status["data"]["controls_defaulted"])

    @owner_bound_only
    def test_a_corrupt_ledger_is_named_not_silently_empty(self) -> None:
        root = unique_root("v04-mem-corrupt-")
        root.mkdir(parents=True)
        binding = build_memory(root, rows=SHOPPING_ROWS[:1])
        binding["controls"].write_bytes(b"not a database at all")
        service = memory_service_from_env(memory_env(binding))
        listed = service.list_experiences()
        self.assertEqual(listed["status"], "BLOCKED")
        self.assertIsNone(listed["data"]["experiences"])
        self.assertIn(str(listed["reason"]),
                      {"MEMORY_CONTROLS_UNHEALTHY", "MEMORY_GOVERNANCE_PROBE_FAILED"})


# ---------------------------------------------------------------------------------------------
# reads are read-only, and actually relevant
# ---------------------------------------------------------------------------------------------


class ReadOnlyReadTests(unittest.TestCase):
    def setUp(self) -> None:
        if not PORT_AVAILABLE:
            self.skipTest("真实 M0 EvidenceStore 只在 POSIX 可用")
        self.root = unique_root("v04-mem-read-")
        self.root.mkdir(parents=True)
        self.binding = build_memory(self.root, rows=OLDER_RELEVANT_ROWS + SHOPPING_ROWS)
        self.service = memory_service_from_env(memory_env(self.binding))

    def test_reads_create_nothing_at_all(self) -> None:
        before = snapshot(self.root)
        listed = self.service.list_experiences(limit=20)
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["data"]["count"], 8)
        first = listed["data"]["experiences"][0]
        read = self.service.read_experience(first["experience_id"])
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["data"]["experience"]["experience_id"],
                         first["experience_id"])
        context = self.service.context(limit=5)
        self.assertTrue(context["ok"], context)
        after = snapshot(self.root)
        self.assertEqual(after, before, "a GET must not write, migrate or chmod anything")
        self.assertEqual([name for name in after if name.endswith(("-wal", "-shm", "-journal"))],
                         [])

    def test_an_older_relevant_experience_is_found_not_just_the_newest_five(self) -> None:
        newest = self.service.list_experiences(limit=6)["data"]["experiences"]
        self.assertEqual([row["text"] for row in newest][-1], SHOPPING_ROWS[0]["text"])
        newest_ids = {row["experience_id"] for row in newest}
        context = self.service.context(query="笔记", limit=5)
        self.assertTrue(context["ok"], context)
        texts = [row["text"] for row in context["data"]["experiences"]]
        self.assertEqual(len(texts), 2, texts)
        self.assertTrue(all("笔记" in text for text in texts), texts)
        found_ids = {row["experience_id"] for row in context["data"]["experiences"]}
        self.assertFalse(found_ids & newest_ids,
                         "the query must reach the older rows, not the newest six")

    def test_list_without_a_query_is_newest_first(self) -> None:
        listed = self.service.list_experiences(limit=3)
        occurred = [row["occurred_at"] for row in listed["data"]["experiences"]]
        self.assertEqual(occurred, sorted(occurred, reverse=True))
        self.assertEqual(occurred[0], SHOPPING_ROWS[-1]["occurred_at"])

    def test_since_bounds_the_window(self) -> None:
        listed = self.service.list_experiences(since=_iso(3))
        occurred = [row["occurred_at"] for row in listed["data"]["experiences"]]
        self.assertEqual(len(occurred), 2, occurred)
        self.assertTrue(all(value > _iso(3) for value in occurred))

    def test_a_row_carries_the_refs_and_outcome_kind(self) -> None:
        listed = self.service.list_experiences(query="复述")
        rows = listed["data"]["experiences"]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["artifact_ref"], "v04:artifact-notes")
        self.assertEqual(row["version_ref"], "v04:version-2")
        self.assertEqual(row["source_ref"], "lexp:old-recipe")
        for field in ("experience_id", "event_id", "occurred_at", "text", "activity_ref",
                      "action_ref", "owner_result", "outcome_kind"):
            self.assertIn(field, row)

    def test_the_context_body_is_bounded(self) -> None:
        long_text = "很长的记录：" + ("细节" * 800)
        root = unique_root("v04-mem-long-")
        root.mkdir(parents=True)
        binding = build_memory(root, rows=[{"source_ref": "lexp:long-1", "text": long_text,
                                           "occurred_at": _iso(0)}])
        service = memory_service_from_env(memory_env(binding))
        rows = service.context(limit=1)["data"]["experiences"]
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]["text"]), 601)
        self.assertTrue(rows[0]["text"].endswith("…"))


class OwnerDomainTests(unittest.TestCase):
    @owner_bound_only
    def test_another_owner_never_sees_this_stream(self) -> None:
        root = unique_root("v04-mem-domain-")
        root.mkdir(parents=True)
        binding = build_memory(root, rows=SHOPPING_ROWS[:2])
        chiyo_service = memory_service_from_env(memory_env(binding))
        mine = chiyo_service.list_experiences()
        self.assertEqual(mine["data"]["count"], 2)
        event_id = mine["data"]["experiences"][0]["experience_id"]

        persona_service = memory_service_from_env(memory_env(binding, owner="chiyo.persona"))
        status = persona_service.status()
        self.assertEqual(status["status"], "READY", status)
        self.assertEqual(status["data"]["owner"], "chiyo.persona")
        other = persona_service.list_experiences()
        self.assertEqual(other["data"]["experiences"], [])
        read = persona_service.read_experience(event_id)
        self.assertEqual(read["status"], "NOT_FOUND")
        self.assertIsNone(read["data"]["experience"])
        self.assertNotIn("购物袋", json.dumps(read, ensure_ascii=False))
    def setUp(self) -> None:
        if not PORT_AVAILABLE:
            self.skipTest("真实 M0 EvidenceStore 只在 POSIX 可用")

class GovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        if not PORT_AVAILABLE:
            self.skipTest("真实 M0 EvidenceStore 只在 POSIX 可用")
        self.root = unique_root("v04-mem-governance-")
        self.root.mkdir(parents=True)
        self.binding = build_memory(self.root, rows=OLDER_RELEVANT_ROWS + SHOPPING_ROWS[:2])
        self.service = memory_service_from_env(memory_env(self.binding))

    def _delete(self, event_id: str) -> None:
        ledger = MemoryControlLedger(self.binding["controls"], self.binding["evidence"],
                                     owner="chiyo",
                                     conversation=life_conversation_id("chiyo"))
        self.assertEqual(ledger.apply(operation_id="test-delete-" + event_id, kind="DELETE",
                                      target=event_id, actor="chiyo"), "APPLIED")

    def test_a_deleted_experience_is_not_recallable_any_more(self) -> None:
        target = self.service.list_experiences(query="复述")["data"]["experiences"][0]
        self._delete(target["experience_id"])
        read = self.service.read_experience(target["experience_id"])
        self.assertEqual(read["status"], "NOT_FOUND")
        self.assertIsNone(read["data"]["experience"])
        self.assertNotIn("复述", json.dumps(read, ensure_ascii=False))
        self.assertEqual(self.service.list_experiences(query="复述")["data"]["experiences"], [])
        context = self.service.context(query="复述")
        self.assertEqual(context["data"]["experiences"], [])
        remaining = self.service.list_experiences()
        self.assertNotIn("复述", json.dumps(remaining, ensure_ascii=False))

    def test_the_deleted_row_does_not_lend_its_text_to_a_neighbour(self) -> None:
        first, second = self.service.list_experiences(query="笔记")["data"]["experiences"]
        self._delete(first["experience_id"])
        rows = self.service.list_experiences(query="笔记")["data"]["experiences"]
        ids = {row["experience_id"] for row in rows}
        self.assertNotIn(first["experience_id"], ids)
        self.assertNotIn("复述", json.dumps(rows, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------
# consumer: owner comes from the port, and only real confirmed content is written
# ---------------------------------------------------------------------------------------------


class RecordingPort:
    """A port stub that keeps exactly what the consumer asked M0 to append."""

    def __init__(self, subject: str = "chiyo") -> None:
        self.subject = subject
        self.events: list = []

    def append_outcome(self, event) -> dict:
        self.events.append(event)
        return {"status": "inserted", "event_id": event.event_id}


def handoff(**overrides) -> dict:
    payload = {
        "handoff_id": "lexp:abcdef0123456789",
        "correlation_id": "corr:1",
        "activity_ref": "act:1",
        "activity_revision": 2,
        "activity_kind": "WRITE",
        "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
        "decision_refs": ["dec:1"],
        "adoption_refs": ["adp:1"],
        "action_refs": ["actn:1"],
        "settlement_refs": ["set:1"],
        "progress_refs": [],
        "completion_evidence_ref": None,
        "fact_refs": ["fact:v04:version-2"],
        "artifact_refs": ["v04:artifact-notes"],
        "world_refs": [],
        "occurred_at": "2026-10-24T09:00:00+00:00",
        "subject_id": "chiyo",
    }
    payload.update(overrides)
    return payload


class ConsumerOwnerTests(unittest.TestCase):
    @owner_bound_only
    def test_the_owner_is_the_ports_subject_never_the_handoff_spelling(self) -> None:
        port = RecordingPort("chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
        receipt = consumer.receive_experience_handoff(handoff(subject_id="chiyo.global.other"))
        self.assertEqual(receipt["disposition"], DISPOSITION_ACCEPT, receipt)
        self.assertEqual(receipt["owner"], "chiyo")
        self.assertEqual([event.memory_owner for event in port.events], ["chiyo"])
        self.assertEqual(port.events[0].conversation_id, "life:chiyo")

    @owner_bound_only
    def test_a_persona_port_writes_under_its_own_owner_even_when_the_handoff_says_chiyo(
            self) -> None:
        port = RecordingPort("chiyo.persona")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
        receipt = consumer.receive_experience_handoff(handoff(subject_id="chiyo"))
        self.assertEqual(receipt["disposition"], DISPOSITION_ACCEPT, receipt)
        self.assertEqual([event.memory_owner for event in port.events], ["chiyo.persona"])

    @owner_bound_only
    def test_a_port_without_an_owner_defers_instead_of_guessing(self) -> None:
        port = RecordingPort("")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
        receipt = consumer.receive_experience_handoff(handoff(subject_id="chiyo.global"))
        self.assertEqual(receipt["disposition"], DISPOSITION_DEFER)
        self.assertEqual(receipt["reason"], "MEMORY_OWNER_UNRESOLVED")
        self.assertEqual(port.events, [])

    @owner_bound_only
    def test_a_handoff_still_writes_the_owner_it_was_bound_to(self) -> None:
        root = unique_root("v04-mem-consumer-owner-")
        root.mkdir(parents=True)
        binding = build_memory(root)
        port = LifeExperiencePort(binding["evidence"], controls_db=binding["controls"],
                                  subject="chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
        self.assertEqual(consumer.receive_experience_handoff(
            handoff(subject_id="chiyo.host"))["disposition"], DISPOSITION_ACCEPT)
        service = memory_service_from_env(memory_env(binding))
        listed = service.list_experiences()
        self.assertEqual(listed["data"]["count"], 1)
        persona = memory_service_from_env(memory_env(binding, owner="chiyo.persona"))
        self.assertEqual(persona.list_experiences()["data"]["experiences"], [])


class ConfirmedContextTests(unittest.TestCase):
    def _run(self, **overrides):
        port = RecordingPort("chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
        receipt = consumer.receive_experience_handoff(handoff(**overrides))
        return receipt, port.events[0]

    @owner_bound_only
    def test_a_type_correct_anchored_mapping_is_used_within_bounds(self) -> None:
        receipt, event = self._run(owner_confirmed_context={
            "artifact_id": "v04:artifact-notes",
            "version_ref": "fact:v04:version-2",
            "title": "夜间阅读记录",
            "result_description": "把昨天读的三页写成了笔记，所有者确认了新版本",
            "outcome_confirmed": True,
        })
        self.assertEqual(receipt["owner_confirmed_context"], "USED", receipt)
        self.assertIn("夜间阅读记录", event.content)
        self.assertIn("写成了笔记", event.content)
        self.assertIn("v04:version-2", event.content)
        self.assertLessEqual(len(event.content), 600)
        refs = {item["kind"]: item["id"] for item in event.source_refs}
        self.assertEqual(refs["artifact"], "v04:artifact-notes")
        self.assertEqual(refs["artifactversion"], "fact:v04:version-2")
        self.assertEqual(refs["outcome_kind"], "SIGNIFICANT_PROGRESS")

    @owner_bound_only
    def test_a_version_that_the_event_does_not_really_carry_is_rejected(self) -> None:
        receipt, event = self._run(owner_confirmed_context={
            "artifact_id": "v04:artifact-notes",
            "version_ref": "v04:version-999",
            "title": "夜间阅读记录",
            "result_description": "这句话不该被写成事实",
            "outcome_confirmed": True,
        })
        self.assertEqual(receipt["owner_confirmed_context"], "VERSION_NOT_REFERENCED")
        self.assertNotIn("这句话不该被写成事实", event.content)
        self.assertNotIn("夜间阅读记录", event.content)
        self.assertIn("完成了一次真实修改", event.content)

    @owner_bound_only
    def test_an_artifact_outside_the_handoff_is_rejected(self) -> None:
        receipt, event = self._run(owner_confirmed_context={
            "artifact_id": "v04:artifact-other",
            "version_ref": "fact:v04:version-2",
            "result_description": "别的作品的结果",
            "outcome_confirmed": True,
        })
        self.assertEqual(receipt["owner_confirmed_context"], "ARTIFACT_NOT_REFERENCED")
        self.assertNotIn("别的作品的结果", event.content)

    @owner_bound_only
    def test_confirmation_must_be_literally_true(self) -> None:
        for value in (False, "true", 1, None):
            receipt, event = self._run(owner_confirmed_context={
                "artifact_id": "v04:artifact-notes",
                "version_ref": "fact:v04:version-2",
                "result_description": "未确证的结果",
                "outcome_confirmed": value,
            })
            self.assertEqual(receipt["owner_confirmed_context"], "NOT_CONFIRMED", value)
            self.assertNotIn("未确证的结果", event.content)

    @owner_bound_only
    def test_a_model_self_note_is_never_promoted_to_fact(self) -> None:
        receipt, event = self._run(owner_confirmed_context={
            "artifact_id": "v04:artifact-notes",
            "version_ref": "fact:v04:version-2",
            "result_description": "所有者确认：写入了新版本",
            "outcome_confirmed": True,
            "self_note": "我觉得这次写得特别有灵魂",
        })
        self.assertEqual(receipt["owner_confirmed_context"], "USED_WITH_IGNORED_KEYS")
        self.assertNotIn("灵魂", event.content)
        self.assertIn("写入了新版本", event.content)

    @owner_bound_only
    def test_an_unsettled_outcome_is_not_upgraded_by_a_confirmed_mapping(self) -> None:
        receipt, event = self._run(
            outcome_kind="UNKNOWN_EFFECT",
            owner_confirmed_context={
                "artifact_id": "v04:artifact-notes",
                "version_ref": "fact:v04:version-2",
                "result_description": "成功了",
                "outcome_confirmed": True,
            })
        self.assertEqual(receipt["owner_confirmed_context"], "UNSETTLED")
        self.assertNotIn("成功了", event.content)
        refs = {item["kind"]: item["id"] for item in event.source_refs}
        self.assertEqual(refs["outcome_kind"], "SIGNIFICANT_PROGRESS",
                         "the row keeps the milestone kind it really carried")
        self.assertNotIn("artifactversion", refs,
                         "a rejected confirmed mapping adds no version reference")

    @owner_bound_only
    def test_a_non_mapping_is_rejected(self) -> None:
        receipt, event = self._run(owner_confirmed_context="完成了一次修改")
        self.assertEqual(receipt["owner_confirmed_context"], "NOT_A_MAPPING")
        self.assertNotIn("完成了一次修改", event.content)


# ---------------------------------------------------------------------------------------------
# explicit opt-in: enable_instance_memory
# ---------------------------------------------------------------------------------------------


class EnableInstanceMemoryTests(unittest.TestCase):
    def _root(self, prefix: str) -> Path:
        root = unique_root(prefix)
        root.mkdir(parents=True)
        write_nested(root)
        return root

    @owner_bound_only
    def test_enable_creates_a_backed_up_binding_and_starts_nothing(self) -> None:
        root = self._root("v04-mem-enable-")
        original = (root / "one-day" / "instance.json").read_text(encoding="utf-8")
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-1")
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["created"])
        self.assertFalse(report["reused"])
        self.assertEqual(report["status"], "READY")
        self.assertTrue(report["restart_required"])
        self.assertEqual(report["processes_started"], 0)
        self.assertFalse(report["service_started"])
        memory = root / "one-day" / "life" / "memory"
        self.assertTrue((memory / "evidence.db").is_file())
        self.assertTrue((memory / "controls.db").is_file())
        backups = sorted((root / "one-day").glob("instance.json-backup-*"))
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        payload = nested_payload(root)
        env = payload["environment"]
        self.assertEqual(env["NYA02_MEMORY_DB"], str(memory / "evidence.db"))
        self.assertEqual(env["NYA02_MEMORY_CONTROLS_DB"], str(memory / "controls.db"))
        self.assertEqual(env["NYA02_MEMORY_SUBJECT"], "chiyo")
        self.assertEqual(report["binding"]["owner"], "chiyo")

    @owner_bound_only
    def test_enable_writes_only_the_three_memory_names_and_keeps_run_and_budget(self) -> None:
        root = self._root("v04-mem-enable-env-")
        before = dict(nested_payload(root)["environment"])
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-2")
        self.assertTrue(report["ok"], report)
        after = dict(nested_payload(root)["environment"])
        added = set(after) - set(before)
        self.assertEqual(added, {"NYA02_MEMORY_DB", "NYA02_MEMORY_CONTROLS_DB",
                                 "NYA02_MEMORY_SUBJECT"})
        for name, value in before.items():
            self.assertEqual(after[name], value, name)
        self.assertEqual(after["CHIYO_AUTONOMOUS_RUN_ID"], "run:existing-window")
        self.assertEqual(after["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"], "96")

    @owner_bound_only
    def test_enable_is_idempotent_and_rewrites_nothing_on_a_second_call(self) -> None:
        root = self._root("v04-mem-enable-idem-")
        first = phone_life.enable_instance_memory(root, "chiyo", "enable-3")
        self.assertTrue(first["created"])
        before = snapshot(root)
        second = phone_life.enable_instance_memory(root, "chiyo", "enable-3")
        self.assertTrue(second["reused"], second)
        self.assertFalse(second["created"])
        self.assertEqual(second["status"], "READY")
        self.assertEqual(second["binding"], first["binding"])
        self.assertEqual(snapshot(root), before, "an idempotent call must not touch a single byte")
        self.assertEqual(len(sorted((root / "one-day").glob("instance.json-backup-*"))), 1)

    @owner_bound_only
    def test_enable_never_overwrites_an_existing_explicit_binding(self) -> None:
        root = self._root("v04-mem-enable-existing-")
        binding = build_memory(root / "elsewhere", rows=SHOPPING_ROWS[:1])
        write_nested(root, memory_env(binding))
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-4")
        self.assertTrue(report["reused"], report)
        self.assertFalse(report["created"])
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["binding"]["database"], str(binding["evidence"]))
        self.assertEqual(snapshot(root), before, "an existing binding is never re-pointed")
        self.assertEqual(nested_payload(root)["environment"]["NYA02_MEMORY_DB"],
                         str(binding["evidence"]))

    @owner_bound_only
    def test_enable_reports_an_unhealthy_existing_binding_without_rebuilding_it(self) -> None:
        root = self._root("v04-mem-enable-unhealthy-")
        binding = build_memory(root / "elsewhere", rows=SHOPPING_ROWS[:1])
        write_nested(root, memory_env(binding, controls=False))
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-5")
        self.assertTrue(report["reused"], report)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["health"]["reason"], "MEMORY_CONTROLS_UNHEALTHY")
        self.assertEqual(snapshot(root), before, "an unhealthy binding is reported, not replaced")

    @owner_bound_only
    def test_enable_refuses_a_missing_nested_instance_and_creates_nothing(self) -> None:
        root = unique_root("v04-mem-enable-noinstance-")
        root.mkdir(parents=True)
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-6")
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "NESTED_LIFE_INSTANCE_MISSING")
        self.assertEqual(snapshot(root), before)

    def test_enable_requires_an_explicit_operation_key(self) -> None:
        root = self._root("v04-mem-enable-nokey-")
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "")
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "MEMORY_OPERATION_KEY_REQUIRED")
        self.assertEqual(report["created"], False)
        self.assertEqual(snapshot(root), before)

    def test_enable_refuses_half_created_files_it_did_not_bind(self) -> None:
        root = self._root("v04-mem-enable-halfdone-")
        memory = root / "one-day" / "life" / "memory"
        memory.mkdir(parents=True)
        (memory / "evidence.db").write_bytes(b"stale but real")
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-7")
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "MEMORY_FILES_EXIST_UNBOUND")
        self.assertEqual(snapshot(root), before)

    @windows_only
    def test_enable_without_a_posix_store_is_a_named_refusal(self) -> None:
        root = self._root("v04-mem-enable-windows-")
        before = snapshot(root)
        report = phone_life.enable_instance_memory(root, "chiyo", "enable-8")
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "UNAVAILABLE")
        self.assertEqual(report["reason"], "MEMORY_STORE_UNAVAILABLE")
        self.assertEqual(report["created"], False)
        self.assertFalse((root / "one-day" / "life" / "memory").exists())
        self.assertEqual(snapshot(root), before)

    @owner_bound_only
    @owner_bound_only
    def test_memory_health_after_enabling_is_read_only(self) -> None:
        root = self._root("v04-mem-enable-health-")
        self.assertTrue(phone_life.enable_instance_memory(root, "chiyo", "enable-9")["ok"])
        environment = dict(nested_payload(root)["environment"])
        before = snapshot(root)
        state = phone_life.check_memory(environment)
        self.assertEqual(state["status"], "READY", state)
        self.assertTrue(state["recall_ready"])
        self.assertEqual(state["owner"], "chiyo")
        self.assertFalse(state["controls_defaulted"])
        self.assertEqual(snapshot(root), before, "a health check must stay a pure read")
if __name__ == "__main__":
    unittest.main()
