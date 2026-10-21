#!/usr/bin/env python3
"""v0.4 A：共同实践服务跑在**真实 Supply ManagedArtifact owner** 上的用例。

证据分级（照实写）
------------------
* owner 是真的：``components/supply/lifesupply/artifact/store.py::ManagedArtifactStore`` ——
  **真 sqlite 文件**、不可变版本（``artifact_versions`` 有 no-update/no-delete 触发器）、真实
  ``artifact_operations`` / ``artifact_effect_receipts`` 账本、真实 CAS（``expected_head`` /
  ``expected_revision``）。
* 中间只有一层测试运输替身：``SupplyStorePort``（复用 ``tests/test_v04_sharing_supply_owner.py``
  里的同一份），把 store 的字典答复翻成 ``life_artifact_port.ArtifactOutcome``，语义与真实
  ``LifeArtifactPort`` 一致。真实部署里这一层是 Unix socket + Governance 预留，本文件**没有**
  起 socket、**没有**走 Governance。
* 来源核对用 ``FakeSourceVerifier``（按 A 自己的读面形状冻结：``get_decision`` / ``get_event`` /
  ``get_action`` → dict 或 None）。真实 Decision/Event owner 的接法是 B 的事；本文件只证明 A
  不轻信客户端自带的来源。
* 夹具根：``$NYAIRO_SHARED_PRACTICE_FIXTURE_ROOT`` 或系统临时目录下的
  ``nyairo-shared-practice-a``；**只创建不删除**（不用 ``TemporaryDirectory``，不 rmtree），
  每个用例一个独立 sqlite 文件名（带 pid 与时间戳），重复运行也不会撞车。
* store 需要 ``fcntl``：本文件在 POSIX（WSL 真实 owner）上跑，Windows 上整文件跳过；
  Windows 上的内存 owner 用例在 ``tests/test_v04_shared_practice_service.py``。
* ``SupplyPracticeFixture`` 是**给父级复用**的真实夹具：一个 store、两个调用方角色、一件真实材料。
* 不读凭据、不跑真实模型、不碰任何实例目录、不删除任何夹具数据。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "life" / "scripts"), str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
import shared_practice_service as sps  # noqa: E402
from test_v04_sharing_supply_owner import SupplyStorePort  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                "ManagedArtifactStore imports fcntl (real owner runs on WSL)")

# The store imports ``fcntl``: only import it where it can work.  On Windows the whole module is
# skipped anyway, but collection must not explode.
try:
    from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
except Exception:  # pragma: no cover - Windows (no fcntl)
    ManagedArtifactStore = None  # type: ignore[assignment]

SUBJECT = "chiyo"
WORKSPACE = "ws-shared-practice"
GRANT = "grant-shared-practice"
DECISION_REF = "dec:0123456789abcdef"

MATERIAL_TEXT = "第三页：把留白重新排一遍，第二段还是太挤。留白-材料-7d1a"
MATERIAL_V2 = "第三页（第二版）：留白重排过了，第二段拉开了。"
#: Sentinels: one distinct string per derived field, so a leak names *what* leaked.
TITLE = "一起排第三页-标题-4b21"
OPENING = "共同正文-开头-9c3e：先把这一页的留白看一遍。"
USER_TEXT = "用户贡献-他的话-8be4：第二段读了两遍还是挤，先拆成两小段？"
RESPONSE_TEXT = "主体回应-她的话-2c67：同意先拆第二段，我在页末补了一条备注。"
CONTRIB_TEXT = "主体贡献-她补的-1a05：备注——第二段拆两小段，中间留一行白。"
WORK_TEXT = OPENING + "\n\n## 一起改的第二段\n拆成两小段，中间留一行白。她补了备注。"


class WsAuthority:
    """The store's ``workspace_authority`` test double: one ACTIVE workspace of this subject."""

    def get_workspace_by_id(self, workspace_id: str) -> Optional[dict[str, Any]]:
        if workspace_id == WORKSPACE:
            return {"subject_id": SUBJECT, "lifecycle": "ACTIVE"}
        return None


class Clock:
    def __init__(self, start: float = 1760000000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class FakeSourceVerifier:
    """A's read surface: a dict answer, or ``None`` when the owner does not have that source."""
    def __init__(self) -> None:
        self.decisions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    def add_decision(self, ref: str = DECISION_REF) -> str:
        self.decisions[ref] = {"ok": True, "status": "COMMITTED", "decision_id": ref,
                               "revision": 1}
        return ref

    def get_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_decision", decision_id))
        return self.decisions.get(decision_id)

    def get_event(self, event_ref: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_event", event_ref))
        return self.events.get(event_ref)

    def get_action(self, action_id: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_action", action_id))
        return self.actions.get(action_id)


class LostAnswerPort(SupplyStorePort):
    """The real owner, except that the *next* commit's answer is lost in transport.

    The write really lands (real version, real receipt); the caller only sees ``UNKNOWN`` and must
    reconcile instead of retrying -- exactly the case this service's UNKNOWN path exists for.
    """

    def __init__(self, store: Any, **kwargs: Any) -> None:
        super().__init__(store, **kwargs)
        self.drop_next = False
        self.drop_create_next = False

    def commit_version(self, *, artifact_id: str, expected_head: Optional[str], content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        outcome = super().commit_version(artifact_id=artifact_id, expected_head=expected_head,
                                         content=content, grant_id=grant_id,
                                         operation_key=operation_key)
        if not self.drop_next:
            return outcome
        self.drop_next = False
        return lap.ArtifactOutcome(effect_state=lap.EffectState.UNKNOWN, status="UNKNOWN",
                                   detail="answer lost in transport", operation_key=operation_key,
                                   artifact_id=artifact_id)

    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        created = super().create_artifact(workspace_id=workspace_id, title=title, content=content,
                                          grant_id=grant_id, operation_key=operation_key)
        if not self.drop_create_next:
            return created
        # The artifact and its first version really exist (real receipt); only the answer is lost.
        self.drop_create_next = False
        return lap.ArtifactOutcome(effect_state=lap.EffectState.UNKNOWN, status="UNKNOWN",
                                   detail="answer lost in transport", operation_key=operation_key)


def fixture_root() -> Path:
    override = os.environ.get("NYAIRO_SHARED_PRACTICE_FIXTURE_ROOT", "").strip()
    base = (Path(override) if override
            else Path(os.environ.get("TMPDIR", "/tmp")) / "nyairo-shared-practice-a")
    base.mkdir(parents=True, exist_ok=True)
    return base


class SupplyPracticeFixture:
    """A reusable, **real** Supply owner with both caller roles and one real shared material.

    ::

        fx = SupplyPracticeFixture(fixture_root(), label="parent-check")
        fx.life, fx.user      # two roles, each on its own PrivateProjectService over one port
        fx.material_id        # her real artifact (one immutable version already)
        fx.material_version() # that artifact's current head_version_id
        opened = fx.open_session()
        fx.head(fx.life, opened["data"]["session_id"])
        fx.versions(artifact_id)   # rows in artifact_versions, read straight from sqlite
        fx.close()                 # close resources; never deletes data

    ``use_port`` rebuilds both services over another port object (e.g. :class:`LostAnswerPort`)
    while keeping the same store, clock and read surface.
    """

    def __init__(self, root: Any, *, label: str = "fixture", subject_id: str = SUBJECT,
                 workspace_id: str = WORKSPACE, grant_id: str = GRANT) -> None:
        stamp = "%s-%d-%d" % (str(label).replace("test_", ""), os.getpid(),
                              int(time.time() * 1000))
        self.db_path = str(Path(root) / ("%s.sqlite" % stamp))
        self.subject_id = subject_id
        self.workspace_id = workspace_id
        self.grant_id = grant_id
        self.clock = Clock()
        self.store = ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                          clock=self.clock)
        self.port: Any = SupplyStorePort(self.store, subject_id=subject_id)
        self.verifier = FakeSourceVerifier()
        self.life = self._service(sps.CALLER_LIFE, verifier=self.verifier)
        self.user = self._service(sps.CALLER_USER)
        self.material_id = self._seed_material()

    # -- wiring -----------------------------------------------------------------------
    def _service(self, actor: str, verifier: Any = None) -> sps.SharedPracticeService:
        projects = pps.PrivateProjectService(self.port, self.workspace_id, self.grant_id)
        return sps.SharedPracticeService(projects, actor=actor, source_verifier=verifier,
                                         clock=self.clock)

    def use_port(self, port: Any) -> Any:
        self.port = port
        self.life = self._service(sps.CALLER_LIFE, verifier=self.verifier)
        self.user = self._service(sps.CALLER_USER)
        return port

    def rebuild(self) -> "SupplyPracticeFixture":
        """A real restart: new store / port / services over the **same** sqlite file."""
        self.clock = Clock(self.clock())
        self.store = ManagedArtifactStore(self.db_path, workspace_authority=WsAuthority(),
                                          clock=self.clock)
        self.port = SupplyStorePort(self.store, subject_id=self.subject_id)
        self.life = self._service(sps.CALLER_LIFE, verifier=self.verifier)
        self.user = self._service(sps.CALLER_USER)
        return self

    def _seed_material(self) -> str:
        created = self.store.create_artifact(
            subject_id=self.subject_id, workspace_id=self.workspace_id, title="她的第三页",
            operation_key="seed-material", grant_reservation_id=self.grant_id)
        assert created.get("status") == "OK", created
        artifact_id = str(created["artifact_id"])
        committed = self.store.commit_markdown(
            artifact_id=artifact_id, authorized_subject_id=self.subject_id, expected_head=None,
            content=MATERIAL_TEXT, operation_key="seed-material:v1",
            grant_reservation_id=self.grant_id)
        assert committed.get("status") == "OK", committed
        self.verifier.add_decision()
        return artifact_id

    # -- helpers ----------------------------------------------------------------------
    def material_version(self) -> str:
        head = self.store.read_head(self.material_id)
        return str((head or {}).get("version_id") or "")

    def material_revision(self) -> int:
        return int((self.store.read_artifact(self.material_id) or {}).get("revision") or 0)

    def open_session(self, *, material_refs: Any = None) -> dict[str, Any]:
        answer = self.life.open_practice(
            title=TITLE, opening_text=OPENING,
            material_refs=[self.material_id] if material_refs is None else material_refs,
            operation_key="open:1")
        assert answer.get("ok"), answer
        return answer

    @staticmethod
    def head(service: sps.SharedPracticeService, session_id: str) -> str:
        answer = service.read_practice(session_id)
        assert answer.get("ok"), answer
        return str(answer["data"]["project"]["head_version_id"])

    def versions(self, artifact_id: str) -> int:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT count(*) FROM artifact_versions WHERE artifact_id=?",
                              (artifact_id,)).fetchone()
        finally:
            con.close()
        return int(row[0])

    def operations(self) -> int:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT count(*) FROM artifact_operations").fetchone()
        finally:
            con.close()
        return int(row[0])

    def artifacts(self) -> int:
        """How many ManagedArtifact rows this owner holds (one session = one row)."""
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT count(*) FROM managed_artifacts").fetchone()
        finally:
            con.close()
        return int(row[0])

    def close(self) -> None:
        """Close whatever the store exposes; never delete the fixture files."""
        closer = getattr(self.store, "close", None)
        if callable(closer):
            closer()


@POSIX_ONLY
class RealSupplyPracticeTest(unittest.TestCase):
    """A 的契约在真实 owner 上成立：双方贡献、幂等、CAS、UNKNOWN、治理、结束重开、纯读。"""

    def setUp(self) -> None:
        if ManagedArtifactStore is None:  # pragma: no cover - Windows
            self.skipTest("ManagedArtifactStore unavailable (needs fcntl)")
        self.fx = SupplyPracticeFixture(fixture_root(), label=self._testMethodName)
        self.store = self.fx.store
        self.life = self.fx.life
        self.user = self.fx.user
        self.material = self.fx.material_id
        self.session = self.fx.open_session()["data"]["session_id"]

    def tearDown(self) -> None:
        self.fx.close()

    # -- helpers ----------------------------------------------------------------------
    def head(self, service: Any = None) -> str:
        return self.fx.head(service or self.life, self.session)

    def contribute_user(self, text: str = USER_TEXT, *, key: str = "user:1",
                        expected_head: Any = None) -> dict[str, Any]:
        return self.user.submit_user_contribution(
            self.session, text=text,
            expected_head=self.head() if expected_head is None else expected_head,
            operation_key=key)

    def respond(self, *, key: str = "resp:1") -> dict[str, Any]:
        return self.life.submit_subject_response(
            self.session, text=RESPONSE_TEXT, basis={"decision_ref": DECISION_REF},
            expected_head=self.head(), commitment_state=sps.COMMITMENT_ACCEPTED,
            operation_key=key)

    # -- tests ------------------------------------------------------------------------
    def test_both_sides_contribute_real_content_on_the_real_owner(self) -> None:
        first = self.contribute_user()
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["data"]["entry"]["contributor"], sps.CONTRIBUTOR_USER)
        self.assertEqual(first["data"]["entry"]["kind"], sps.KIND_MESSAGE)
        self.assertFalse(first["data"]["accepted"])
        self.assertEqual(first["data"]["joint_state"], sps.JOINT_CANDIDATE)

        reply = self.respond()
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["data"]["accepted"])
        self.assertEqual(reply["data"]["joint_state"], sps.JOINT_JOINT)
        self.assertTrue(reply["data"]["entry"]["basis"]["verified"])
        self.assertEqual(reply["data"]["entry"]["basis"]["ref"], DECISION_REF)
        self.assertEqual(reply["data"]["entry"]["commitment_state"], sps.COMMITMENT_ACCEPTED)

        added = self.life.submit_subject_contribution(
            self.session, text=CONTRIB_TEXT, basis={"decision_ref": DECISION_REF},
            expected_head=self.head(), operation_key="contrib:1")
        self.assertTrue(added["ok"], added)
        work = self.life.commit_work_text(
            self.session, text=WORK_TEXT, basis={"decision_ref": DECISION_REF},
            expected_head=self.head(), operation_key="work:1")
        self.assertTrue(work["ok"], work)

        view = self.life.read_practice(self.session)["data"]
        self.assertEqual(view["state"], sps.STATE_OPEN)
        self.assertEqual(view["joint_state"], sps.JOINT_JOINT)
        self.assertEqual(view["work"]["text"], WORK_TEXT)
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_TEXT, CONTRIB_TEXT])
        self.assertEqual([row["text"] for row in view["responses"]], [RESPONSE_TEXT])
        self.assertEqual(view["project"]["head_version_id"], work["data"]["version_id"])
        # 一件 session = 一件普通 ManagedArtifact：打开 + 四次写入 = 五个不可变版本。
        self.assertEqual(self.fx.versions(self.session), 5)
        self.assertNotIn("nyairo-shared-practice:v1", json.dumps(view, ensure_ascii=False))
        self.assertNotIn("<!-- ", view["work"]["text"])

    def test_the_same_key_and_payload_reuse_one_version(self) -> None:
        first = self.contribute_user(key="user:fixed")
        self.assertTrue(first["ok"], first)
        versions = self.fx.versions(self.session)
        replay = self.contribute_user(key="user:fixed", expected_head=first["data"]["version_id"])
        self.assertTrue(replay["ok"], replay)
        # 同一个 operation_key 就是同一件事：服务在本地就认出来，没有第二次写。
        self.assertEqual(replay["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertTrue(replay["data"]["replayed"])
        self.assertFalse(replay["data"]["new_version"])
        self.assertEqual(replay["data"]["head_version_id"], first["data"]["version_id"])
        self.assertEqual(replay["data"]["entry"]["seq"], first["data"]["entry"]["seq"])
        self.assertEqual(self.fx.versions(self.session), versions)
        view = self.life.read_practice(self.session)["data"]
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_TEXT])
        self.assertEqual(view["project"]["head_version_id"], first["data"]["version_id"])
        self.assertIsInstance(self.store.lookup_operation(replay["data"]["operation_key"]), dict)

    def test_the_same_key_with_a_different_payload_is_refused(self) -> None:
        first = self.contribute_user(text=USER_TEXT, key="user:reuse")
        self.assertTrue(first["ok"], first)
        versions = self.fx.versions(self.session)
        clash = self.contribute_user(text="另一句完全不一样的话", key="user:reuse")
        self.assertFalse(clash["ok"])
        # 同一个 key 换了内容：服务在本地就具名拒绝，owner 的摘要守卫是第二道。
        self.assertEqual(clash["reason_code"], sps.REASON_OPERATION_KEY_REUSED)
        self.assertEqual(clash["status"], pps.STATUS_REJECTED)
        self.assertEqual(self.fx.versions(self.session), versions)
        view = self.life.read_practice(self.session)["data"]
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_TEXT])

    def test_a_stale_head_is_a_real_cas_conflict(self) -> None:
        stale_head = self.head()
        self.assertTrue(self.contribute_user(key="user:1")["ok"])
        versions = self.fx.versions(self.session)
        stale = self.contribute_user(text="第二句", key="user:2", expected_head=stale_head)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], pps.STATUS_CONFLICT)
        self.assertEqual(self.fx.versions(self.session), versions)
        self.assertEqual(self.head(), self.store.read_artifact(self.session)["head_version_id"])

    def test_an_unknown_reconciles_without_a_second_version(self) -> None:
        port = LostAnswerPort(self.store, subject_id=SUBJECT)
        self.fx.use_port(port)
        self.user = self.fx.user
        port.drop_next = True
        versions = self.fx.versions(self.session)
        answer = self.user.submit_user_contribution(
            self.session, text=USER_TEXT, expected_head=self.head(), operation_key="user:lost")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], pps.STATUS_APPLIED)
        self.assertTrue(answer["data"]["reconciled"])
        self.assertTrue(answer["data"]["new_version"])
        # 真的只落了一个版本：对账用的是 owner 的 receipt，不是重发一次写。
        self.assertEqual(self.fx.versions(self.session), versions + 1)
        self.assertIsInstance(self.store.read_effect_receipt(answer["data"]["operation_key"]), dict)
        view = self.life.read_practice(self.session)["data"]
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_TEXT])

    def test_a_replaced_material_keeps_the_text_and_refuses_writes_until_reconfirmed(self) -> None:
        self.assertTrue(self.contribute_user(key="user:1")["ok"])
        moved = self.store.commit_markdown(
            artifact_id=self.material, authorized_subject_id=SUBJECT,
            expected_head=self.fx.material_version(), content=MATERIAL_V2,
            operation_key="material:v2", grant_reservation_id=GRANT)
        self.assertEqual(moved.get("status"), "OK", moved)

        view = self.life.read_practice(self.session)["data"]
        self.assertEqual(view["materials"][0]["state"], sps.MATERIAL_MOVED)
        self.assertTrue(view["governance"]["stale"])
        self.assertFalse(view["governance"]["blocked"])
        self.assertEqual(view["work"]["text"], OPENING)  # 正文仍然可读

        stale = self.contribute_user(text="换版之后", key="user:stale")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason_code"], sps.REASON_MATERIAL_STALE)

        reconfirmed = self.life.reconfirm_materials(
            self.session, expected_head=self.head(), operation_key="materials:reconfirm")
        self.assertTrue(reconfirmed["ok"], reconfirmed)
        self.assertEqual(reconfirmed["data"]["materials"][0]["version_id"],
                         self.fx.material_version())
        after = self.life.read_practice(self.session)["data"]
        self.assertFalse(after["governance"]["stale"])
        self.assertTrue(after["governance"]["writes_allowed"])
        self.assertTrue(self.contribute_user(text="换版之后", key="user:after")["ok"])

    def test_an_archived_material_withholds_every_derived_text(self) -> None:
        self.assertTrue(self.contribute_user(key="user:1")["ok"])
        self.assertTrue(self.respond()["ok"])
        archived = self.store.archive(
            self.material, authorized_subject_id=SUBJECT,
            expected_revision=self.fx.material_revision(), operation_key="material:archive",
            grant_reservation_id=GRANT)
        self.assertIn(archived.get("status"), ("OK", "NO_CHANGE"), archived)

        view = self.life.read_practice(self.session)["data"]
        self.assertTrue(view["governance"]["blocked"])
        self.assertEqual(view["materials"][0]["state"], sps.MATERIAL_ARCHIVED)
        self.assertEqual(view["title"], "")
        self.assertEqual(view["work"]["text"], "")
        self.assertEqual([row["text"] for row in view["contributions"]], [""])
        self.assertEqual([row["text"] for row in view["responses"]], [""])
        self.assertEqual(view["work"]["chars"], 0)
        dumped = json.dumps(view, ensure_ascii=False)
        for sentinel in (TITLE, OPENING, USER_TEXT, RESPONSE_TEXT, MATERIAL_TEXT):
            self.assertNotIn(sentinel, dumped)
        self.assertIn("work.text", view["governance"]["redacted"])

        refused = self.contribute_user(text="归档之后", key="user:2")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], sps.REASON_MATERIAL_UNAVAILABLE)
        # 材料没了仍然可以正当结束（不靠正文、不删任何东西）。
        ended = self.life.end_practice(self.session, reason="材料被归档了，先到这儿",
                                       expected_head=self.head(), operation_key="end:archived")
        self.assertTrue(ended["ok"], ended)
        self.assertEqual(ended["data"]["state"], sps.STATE_ENDED)

    def test_end_and_reopen_keep_the_work_on_the_real_owner(self) -> None:
        self.assertTrue(self.contribute_user(key="user:1")["ok"])
        self.assertTrue(self.respond()["ok"])
        self.assertTrue(self.life.commit_work_text(
            self.session, text=WORK_TEXT, basis={"decision_ref": DECISION_REF},
            expected_head=self.head(), operation_key="work:1")["ok"])
        ended = self.life.end_practice(self.session, reason="今天就到这儿",
                                       expected_head=self.head(), operation_key="end:1")
        self.assertTrue(ended["ok"], ended)
        self.assertEqual(ended["data"]["state"], sps.STATE_ENDED)
        versions = self.fx.versions(self.session)

        closed = self.life.read_practice(self.session)["data"]
        self.assertEqual(closed["joint_state"], sps.JOINT_ENDED)
        self.assertEqual(closed["work"]["text"], WORK_TEXT)
        self.assertEqual([row["text"] for row in closed["contributions"]], [USER_TEXT])
        self.assertIn("今天就到这儿", closed["ended"]["text"])

        reopened = self.life.reopen_practice(
            self.session, reason="接着弄下一页", basis={"decision_ref": DECISION_REF},
            expected_head=self.head(), operation_key="reopen:1")
        self.assertTrue(reopened["ok"], reopened)
        self.assertEqual(reopened["data"]["state"], sps.STATE_OPEN)
        self.assertEqual(reopened["data"]["reopen_count"], 1)
        self.assertEqual(self.fx.versions(self.session), versions + 1)

        again = self.life.read_practice(self.session)["data"]
        self.assertEqual(again["state"], sps.STATE_OPEN)
        self.assertEqual(again["joint_state"], sps.JOINT_JOINT)
        self.assertEqual(again["work"]["text"], WORK_TEXT)  # 重开的是同一件作品，不是重建
        self.assertEqual([row["text"] for row in again["contributions"]], [USER_TEXT])
        self.assertIsNone(again["ended"])
        self.assertTrue(again["last_ended_at"])
        # 重开之后继续写是追加，不会重复旧贡献。
        self.assertTrue(self.contribute_user(text="重开之后的一句", key="user:2")["ok"])
        final = self.life.read_practice(self.session)["data"]
        self.assertEqual([row["text"] for row in final["contributions"]],
                         [USER_TEXT, "重开之后的一句"])

    def test_reads_write_nothing_on_the_real_owner(self) -> None:
        self.assertTrue(self.contribute_user(key="user:1")["ok"])
        before_operations = self.fx.operations()
        before_versions = self.fx.versions(self.session)
        before_material = self.fx.versions(self.material)

        listing = self.life.list_practices()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["count"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertEqual(listing["data"]["skipped"]["other"], 1)  # 材料本身不是一段共同实践
        self.assertNotIn(OPENING, json.dumps(listing["data"], ensure_ascii=False))

        self.assertTrue(self.life.read_practice(self.session)["ok"])
        self.assertTrue(self.life.read_practice(self.session, include_history=True)["ok"])
        context = self.life.context_view(self.session)
        self.assertTrue(context["ok"], context)
        self.assertTrue(self.user.list_practices()["ok"])

        self.assertEqual(self.fx.operations(), before_operations)
        self.assertEqual(self.fx.versions(self.session), before_versions)
        self.assertEqual(self.fx.versions(self.material), before_material)

    def test_the_context_carries_the_body_not_the_marker(self) -> None:
        view = self.life.context_view(self.session)
        self.assertTrue(view["ok"], view)
        data = view["data"]
        self.assertEqual(data["text"], OPENING)
        self.assertTrue(data["text_available"])
        self.assertNotIn("nyairo-shared-practice:v1", json.dumps(data, ensure_ascii=False))
        self.assertEqual(data["not_authority"]["activity"], False)
        self.assertEqual(data["not_authority"]["session_willingness"], False)
        self.assertEqual(data["head_version_id"], self.head())
        self.assertEqual(self.fx.versions(self.session), 1)


    # -- one operation key, on the real owner ------------------------------------------

    def test_an_open_retry_with_the_same_key_is_one_session(self) -> None:
        fx = self.fx
        first = fx.open_session()
        session = first["data"]["session_id"]
        artifacts = fx.artifacts()
        versions = fx.versions(session)
        operations = fx.operations()

        # A real restart: new store/port/projects/service objects over the same sqlite file.
        fx.rebuild()
        retry = fx.life.open_practice(title=TITLE, opening_text=OPENING,
                                      material_refs=[fx.material_id], operation_key="open:1")
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(retry["data"]["session_id"], session)
        self.assertEqual(retry["data"]["operation_check"], sps.OPERATION_APPLIED)
        self.assertTrue(retry["data"]["replayed"])
        self.assertFalse(retry["data"]["new_version"])
        self.assertEqual(retry["data"]["version_id"], first["data"]["version_id"])
        self.assertEqual(retry["data"]["materials"][0]["artifact_id"], fx.material_id)
        # Neither a second artifact nor a second version: the retry wrote nothing at all.
        self.assertEqual(fx.artifacts(), artifacts)
        self.assertEqual(fx.versions(session), versions)
        self.assertEqual(fx.operations(), operations)

    def test_a_lost_open_answer_reconciles_and_a_rebuilt_service_finds_it(self) -> None:
        fx = self.fx
        port = LostAnswerPort(fx.store, subject_id=SUBJECT)
        fx.use_port(port)
        port.drop_create_next = True
        artifacts = fx.artifacts()
        lost = fx.life.open_practice(title=TITLE, opening_text=OPENING,
                                     material_refs=[fx.material_id],
                                     operation_key="open:lost")
        # The open really landed; the answer was lost; A reconciled from the owner's receipt.
        self.assertTrue(lost["ok"], lost)
        self.assertTrue(lost["data"]["reconciled"])
        session = lost["data"]["session_id"]
        self.assertTrue(session)
        self.assertEqual(fx.artifacts(), artifacts + 1)
        self.assertEqual(fx.versions(session), 1)
        self.assertIsInstance(fx.store.read_effect_receipt("open:lost"), dict)

        fx.rebuild()
        again = fx.life.open_practice(title=TITLE, opening_text=OPENING,
                                      material_refs=[fx.material_id],
                                      operation_key="open:lost")
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(again["data"]["session_id"], session)
        self.assertEqual(fx.artifacts(), artifacts + 1)
        self.assertEqual(fx.versions(session), 1)

    def test_an_open_with_another_intent_on_the_same_key_is_refused(self) -> None:
        fx = self.fx
        first = fx.open_session()
        other = self.store.create_artifact(
            subject_id=SUBJECT, workspace_id=WORKSPACE, title="另一页",
            operation_key="seed-other-material", grant_reservation_id=GRANT)
        self.assertEqual(other.get("status"), "OK", other)
        other_id = str(other["artifact_id"])
        seeded = self.store.commit_markdown(
            artifact_id=other_id, authorized_subject_id=SUBJECT, expected_head=None,
            content="另一页正文，另外的材料。", operation_key="seed-other-material:v1",
            grant_reservation_id=GRANT)
        self.assertEqual(seeded.get("status"), "OK", seeded)
        artifacts = fx.artifacts()
        for changed in ({"title": TITLE + "（换了标题）"},
                        {"opening_text": OPENING + "补一句。"},
                        {"material_refs": [other_id]}):
            args = dict(title=TITLE, opening_text=OPENING, material_refs=[fx.material_id])
            args.update(changed)
            clash = fx.life.open_practice(operation_key="open:1", **args)
            self.assertFalse(clash["ok"], changed)
            self.assertEqual(clash["reason_code"], sps.REASON_OPERATION_KEY_REUSED, changed)
            self.assertEqual(fx.artifacts(), artifacts, changed)
        # 同一批材料只是重复点名，不算换意图：那还是原样交回原来那一段。
        same = fx.life.open_practice(title=TITLE, opening_text=OPENING,
                                     material_refs=[fx.material_id, fx.material_id],
                                     operation_key="open:1")
        self.assertTrue(same["ok"], same)
        self.assertEqual(same["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(same["data"]["session_id"], first["data"]["session_id"])
        self.assertEqual(fx.versions(first["data"]["session_id"]), 1)

    def test_the_operation_surface_reads_owner_receipts_and_writes_nothing(self) -> None:
        fx = self.fx
        self.assertTrue(self.contribute_user(key="user:op")["ok"])
        before_operations = fx.operations()
        before_versions = fx.versions(self.session)
        before_material = fx.versions(self.material)

        applied = self.life.operation("user:op")
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["status"], pps.STATUS_APPLIED)
        data = applied["data"]
        self.assertEqual(data["effect"], sps.OPERATION_APPLIED)
        self.assertTrue(data["applied"])
        self.assertEqual(data["artifact_id"], self.session)
        self.assertTrue(data["shared_practice"])
        self.assertEqual(data["session_id"], self.session)
        self.assertTrue(str(data["surface"]).startswith("port."), data["surface"])
        self.assertTrue(data["read_only"])
        self.assertFalse(data["resend"])

        landed = self.life.operation("key:never-used")
        self.assertFalse(landed["ok"])
        self.assertEqual(landed["status"], pps.STATUS_NOT_FOUND)
        self.assertEqual(landed["reason_code"], sps.REASON_OPERATION_NOT_FOUND)
        self.assertEqual(landed["data"]["effect"], sps.OPERATION_NOT_LANDED)

        # Pure read: no new operation row, no new version on either artifact.
        self.assertEqual(fx.operations(), before_operations)
        self.assertEqual(fx.versions(self.session), before_versions)
        self.assertEqual(fx.versions(self.material), before_material)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
