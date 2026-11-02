"""场景10：维护/测试动作的回执进不了个人生活准入。

定点：
* 没有 owner 只读读口的 consumer 一律拒写（形状不是来源，不存在「旧默认」跳过核对）；
* 维护回执（没有 Activity 锚点）在入 M0 之前被具名拒绝；
* 只有 owner 自己持久过、且内容逐字段一致的 handoff 才准入；伪造 actv、未知 id、
  篡改真实 id 的内容都在 M0 写前被具名拒绝；
* 真实 handoff 重投只 DUPLICATE，不多出经历；拒绝计数在 consumer 自己的描述里可见。

Decision/内容为脚本替身；M0 与控制账本是真的。根目录一律保留（mkdtemp，不删）。
生产装配同一套核对由 ``scripts/maintenance_experience_boundary_demo.py`` 用真实 AG-2
owner 签发的记录跑；本文件只验这道门本身。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.evidence import EvidenceStore  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402

OWNER = "chiyo"


class MaintenanceBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="nyairo-mxb-test-"))
        self.evidence = self.root / "evidence.db"
        self.controls = self.root / "controls.db"
        EvidenceStore(self.evidence)
        MemoryControlLedger(self.controls, self.evidence, owner=OWNER,
                            conversation=life_conversation_id(OWNER)).initialize()
        self.port = LifeExperiencePort(self.evidence, controls_db=self.controls,
                                       subject=OWNER, authority_db="")
        #: 这一层扮演 owner 的持久记录：consumer 只能通过读口看见它，看不见测试手里的字典。
        self.owner_store: dict[str, dict] = {}
        self.consumer = MemoryAdmissionExperienceConsumer(
            port=self.port, enabled=True, handoff_verifier=self.owner_store.get)
        self.unwired = MemoryAdmissionExperienceConsumer(port=self.port, enabled=True)

    def _rows(self) -> list[dict]:
        return EvidenceStore(self.evidence).recent_verified_outcomes(
            memory_owner=OWNER, limit=20)

    def _persist(self, event: dict) -> dict:
        self.owner_store[event["handoff_id"]] = dict(event)
        return dict(event)

    @staticmethod
    def _maintenance_receipt() -> dict:
        """一条 World 维护动作的 owner 回执，包成 handoff 形状但没有任何 Activity。"""
        return {
            "handoff_id": "lexp:maintenance0001",
            "correlation_id": "corr:maintenance",
            "activity_revision": 1,
            "activity_kind": "POSE",
            "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
            "decision_refs": [],
            "adoption_refs": [],
            "action_refs": ["world-exec:41d657fe9b2984967c3b328ec9e69377"],
            "settlement_refs": [],
            "progress_refs": [],
            "completion_evidence_ref": None,
            "fact_refs": [],
            "artifact_refs": [],
            "world_refs": ["world:pose:bodyact:pose:seated:north"],
            "occurred_at": "2026-10-29T02:00:00+00:00",
            "subject_id": "chiyo",
        }

    @staticmethod
    def _life_handoff(handoff_id: str = "lexp:life0000000001") -> dict:
        return {
            "handoff_id": handoff_id,
            "correlation_id": "corr:life",
            "activity_ref": "actv_boundary_positive",
            "activity_revision": 2,
            "activity_kind": "READING",
            "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
            "decision_refs": ["dec:boundary"],
            "adoption_refs": ["adp:boundary"],
            "action_refs": ["actn:boundary"],
            "settlement_refs": ["set:boundary"],
            "progress_refs": [],
            "completion_evidence_ref": None,
            "fact_refs": ["fact:boundary"],
            "artifact_refs": [],
            "world_refs": [],
            "occurred_at": "2026-10-29T02:00:00+00:00",
            "subject_id": "chiyo",
        }

    # -- refusals ----------------------------------------------------------------------

    def test_a_world_receipt_without_an_activity_is_refused_before_m0(self) -> None:
        receipt = self.consumer.receive_experience_handoff(self._maintenance_receipt())
        self.assertEqual(receipt["disposition"], "IGNORE", receipt)
        self.assertEqual(receipt["reason"], "LIFE_ACTIVITY_REF_MISSING")
        self.assertEqual(self._rows(), [])
        self.assertEqual(self.consumer.describe()["refused"], 1)
        self.assertEqual(self.consumer.describe()["accepted"], 0)

    def test_without_an_owner_read_port_nothing_is_written(self) -> None:
        receipt = self.unwired.receive_experience_handoff(self._life_handoff())
        self.assertEqual(receipt["reason"], "HANDOFF_SOURCE_UNVERIFIED", receipt)
        self.assertEqual(self._rows(), [])
        self.assertEqual(self.unwired.describe()["accepted"], 0)
        self.assertEqual(self.unwired.describe()["unverified"], 1)

    def test_a_handoff_the_owner_never_persisted_is_refused(self) -> None:
        # 指向一个真实的 Activity 也不能证明来源：owner 的记录里没有这个 id。
        forged = dict(self._life_handoff("lexp:forgedactivity01"),
                      decision_refs=["dec:forged"], adoption_refs=["adp:forged"])
        receipt = self.consumer.receive_experience_handoff(forged)
        self.assertEqual(receipt["reason"], "HANDOFF_SOURCE_NOT_FOUND", receipt)
        self.assertEqual(self._rows(), [])

    def test_a_tampered_copy_of_a_real_id_is_refused(self) -> None:
        real = self._persist(self._life_handoff())
        tampered = dict(real, action_refs=list(real["action_refs"])
                        + ["world-exec:41d657fe9b2984967c3b328ec9e69377"])
        receipt = self.consumer.receive_experience_handoff(tampered)
        self.assertEqual(receipt["reason"], "HANDOFF_SOURCE_MISMATCH", receipt)
        self.assertEqual(self._rows(), [])

    def test_an_unreadable_owner_is_a_refusal_not_a_default_yes(self) -> None:
        def broken(_handoff_id: str) -> dict:
            raise OSError("owner store unreadable")

        consumer = MemoryAdmissionExperienceConsumer(
            port=self.port, enabled=True, handoff_verifier=broken)
        receipt = consumer.receive_experience_handoff(self._life_handoff())
        self.assertEqual(receipt["reason"], "HANDOFF_SOURCE_UNREADABLE", receipt)
        self.assertEqual(self._rows(), [])

    # -- the legal path ----------------------------------------------------------------

    def test_a_persisted_life_handoff_is_admitted_once(self) -> None:
        self.consumer.receive_experience_handoff(self._maintenance_receipt())
        real = self._persist(self._life_handoff())
        first = self.consumer.receive_experience_handoff(real)
        again = self.consumer.receive_experience_handoff(real)
        self.assertEqual(first["disposition"], "ACCEPT", first)
        self.assertEqual(again["disposition"], "DUPLICATE", again)
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(len(self.port.recent(limit=10)), 1)
        self.assertEqual(self.consumer.describe()["refused"], 1)
        self.assertEqual(self.consumer.describe()["accepted"], 1)

    def test_a_refused_receipt_does_not_block_the_next_legal_handoff(self) -> None:
        self.consumer.receive_experience_handoff(self._maintenance_receipt())
        real = self._persist(self._life_handoff("lexp:life0000000002"))
        accepted = self.consumer.receive_experience_handoff(real)
        self.assertEqual(accepted["disposition"], "ACCEPT", accepted)
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(self.consumer.describe()["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
