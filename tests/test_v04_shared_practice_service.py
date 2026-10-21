#!/usr/bin/env python3
"""v0.4 A：共同实践服务（共享材料 / 双方贡献 / 作品正文 / 会话状态）——内存 owner 契约用例。

这个文件测什么
--------------
``components/life/scripts/shared_practice_service.py``：一段共同实践**就是**既有的
``ManagedArtifact``（经 ``PrivateProjectService``），没有第二作品库、没有聊天数据库、没有第二
Activity/关系 owner。覆盖：

* 第一行标记：只认第一行、正文逐字保留、坏标记按失败处理（不猜）、非本服务工件被点名拒绝；
* 角色：用户侧只能写用户自己的话（且最多把承诺挂成 PENDING），主体回应/作品/恢复/重开只能由
  Life 侧写且必须绑定经核对的来源；客户端不能自带来源；
* 来源核对：读面没接 / 没有这个来源 / 是另一个来源 / 明确读不出 → 四种具名拒绝，且一件不写；
* 材料治理：打开时读不到就拒绝；打开后归档/读不到 → 所有派生文本按构造扣留（sentinel 不外泄）；
  换版 → 文本仍可读但新写被拒，``reconfirm_materials`` 是唯一出口；
* 版本：CAS 陈旧 head 是 conflict；UNKNOWN 先对账、同 key 复用不写第二个版本；
* 状态：暂停/结束/重开的真实版本语义，结束后正文与贡献仍在，重开不重复贡献；
* 纯读：list/read/context 零写；context 带真实正文而不是机器标记。

证据分级（照实写）
------------------
* ``FakePort`` 是**内存里的 owner 替身**（复用 ``tests/test_v04_private_projects_stage.py``
  的同一份，按证据冻结的语义：operation_key 幂等、CAS、ARCHIVED 生命周期、UNKNOWN 需先对账），
  测的是本服务自己的契约。
* ``FakeSourceVerifier`` 是按 A 的读面**答复形状**冻结的替身（``get_decision`` /
  ``get_event`` / ``get_action`` 返回 dict 或 None）。
* 真实 ``ManagedArtifactStore``（真 sqlite、不可变版本、真 CAS、真账本）在
  ``tests/test_v04_shared_practice_service_supply.py`` 里跑（POSIX）。
* 不读凭据、不跑真实模型、不碰任何实例目录、不删除任何夹具。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import private_project_service as pps  # noqa: E402
import shared_practice_service as sps  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
    FakePort,
    _outcome,
    lap,
)

SUBJECT = "chiyo"
MATERIAL_TEXT = "第三页：把留白重新排一遍，第二段还是太挤。"
DECISION_REF = "dec:0123456789abcdef"
EVENT_REF = "contact_evt:0123456789abcdef"

#: Sentinels: distinct strings for each derived field, so a leak names *what* leaked.
TITLE_SENTINEL = "共同整页-标题-7d1a"
TEXT_SENTINEL = "共同整页-正文-3f92"
USER_SENTINEL = "用户贡献-他的话-8be4"
RESPONSE_SENTINEL = "主体回应-她的话-2c67"
NOTE_SENTINEL = "暂停说明-5a08"


class Clock:
    """A deterministic clock so recorded instants are stable and comparable."""

    def __init__(self, start: float = 1761000000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 60.0) -> float:
        self.now += float(seconds)
        return self.now


class FakeSourceVerifier:
    """A's read surface, frozen to its answer shapes: a dict, or ``None`` for "no such source"."""

    def __init__(self) -> None:
        self.decisions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.refuse: set[str] = set()
        self.raise_on: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def add_decision(self, ref: str = DECISION_REF, *, status: str = "COMMITTED") -> str:
        self.decisions[ref] = {"ok": True, "status": status, "decision_id": ref,
                               "revision": 1, "snapshot_fingerprint": "fp:seed"}
        return ref

    def add_event(self, ref: str = EVENT_REF) -> str:
        self.events[ref] = {"ok": True, "status": "ok", "data": {"event_ref": ref}}
        return ref

    def add_action(self, ref: str = "action:0123456789abcdef") -> str:
        self.actions[ref] = {"ok": True, "status": "SUCCEEDED", "action_id": ref}
        return ref

    def get_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_decision", decision_id))
        if decision_id in self.raise_on:
            raise RuntimeError("read port exploded")
        if decision_id in self.refuse:
            return {"ok": False, "status": "NOT_FOUND", "reason_code": "GOVERNED"}
        return self.decisions.get(decision_id)

    def get_event(self, event_ref: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_event", event_ref))
        if event_ref in self.refuse:
            return {"ok": False, "status": "not_found", "reason_code": "CONTACT_EVENT_NOT_FOUND"}
        return self.events.get(event_ref)

    def get_action(self, action_id: str) -> Optional[dict[str, Any]]:
        self.calls.append(("get_action", action_id))
        return self.actions.get(action_id)


def seeded_port(port: Any = None) -> Any:
    """One in-memory owner with her reading material, her private items and one usable page.

    Defaults to :class:`SupplyLikePort` (replay is ``NO_CHANGE``, history is listable) so the
    fixture answers like the real store does for the two behaviours this service depends on.
    """
    port = SupplyLikePort() if port is None else port
    port.seed(artifact_id=READING, title="她的阅读材料", content="第一页\n%%PAGE%%\n第二页")
    port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{\"items\":{}}")
    port.seed(artifact_id="artifact:material-bound", title="她的第三页",
              content=MATERIAL_TEXT)
    return port


def build(port: Any, *, actor: str, verifier: Any = None,
          clock: Any = None) -> sps.SharedPracticeService:
    """The reusable in-memory fixture: one projects owner + one caller role."""
    projects = pps.PrivateProjectService(port, WORKSPACE, GRANT,
                                         excluded_artifact_ids=(READING, ITEMS))
    return sps.SharedPracticeService(projects, actor=actor, source_verifier=verifier,
                                    clock=clock or Clock())


def session_service(port: Any = None, *, verifier: Any = None, clock: Any = None
                    ) -> tuple[Any, sps.SharedPracticeService]:
    """A Life-side service with one usable source verifier."""
    port = port if port is not None else seeded_port()
    verifier = verifier if verifier is not None else FakeSourceVerifier()
    if hasattr(verifier, "add_decision") and DECISION_REF not in verifier.decisions:
        verifier.add_decision()
    return port, build(port, actor=sps.CALLER_LIFE, verifier=verifier,
                       clock=clock or Clock())


def opened(port: Any = None, *, verifier: Any = None, material: str = "artifact:material-bound"
          ) -> tuple[Any, sps.SharedPracticeService, dict[str, Any]]:
    """Open one session and return ``(port, service, open_answer)``."""
    port, service = session_service(port, verifier=verifier)
    answer = service.open_practice(title=TITLE_SENTINEL, opening_text=TEXT_SENTINEL,
                                   material_refs=[material], operation_key="open:1")
    if not answer.get("ok"):
        raise AssertionError("fixture could not open a session: %r" % (answer,))
    return port, service, answer


def head_of(service: sps.SharedPracticeService, session_id: str) -> str:
    answer = service.read_practice(session_id)
    if not answer.get("ok"):
        raise AssertionError("fixture could not read the session: %r" % (answer,))
    return str(answer["data"]["project"]["head_version_id"])


def user_service_for(port: Any) -> sps.SharedPracticeService:
    """The person's side: its own role, no read surface, no source."""
    return build(port, actor=sps.CALLER_USER)

class SupplyLikePort(FakePort):
    """``FakePort`` plus the two owner behaviours this service relies on that the base double
    does not model, both taken from the real store (``lifesupply/artifact/store.py``):

    * a **replayed** operation key is ``NO_CHANGE`` -- one intended effect is one version
      (the base double answers ``APPLIED`` with the stored version, which is what a *new*
      effect looks like);
    * ``list_versions`` exists, so ``private_project_service._history`` can answer a
      complete history (the base double only ever reports the head).

    ``lose_answer_next`` models the owner's UNKNOWN that *did* land: the version is written
    and only the answer is lost, so a caller must reconcile instead of retrying.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lose_answer_next = False
        self.lose_create_answer = False
        self.unknown_resolution = False
        self.raise_on_resolve = False

    def commit_version(self, *, artifact_id: str, expected_head: Optional[str], content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        replay = self.operations.get(operation_key)
        if replay is not None:
            self.calls.append(("commit_version", {"artifact_id": artifact_id,
                                                  "expected_head": expected_head,
                                                  "operation_key": operation_key}))
            return _outcome(lap.EffectState.NO_CHANGE, status="NO_CHANGE",
                            artifact_id=artifact_id, version_id=replay.get("version_id"),
                            version_number=replay.get("version_number"))
        landing = super().commit_version(artifact_id=artifact_id, expected_head=expected_head,
                                         content=content, grant_id=grant_id,
                                         operation_key=operation_key)
        if not self.lose_answer_next:
            return landing
        self.lose_answer_next = False
        return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN", artifact_id=artifact_id,
                        version_id=landing.version_id, version_number=landing.version_number)

    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        created = super().create_artifact(workspace_id=workspace_id, title=title,
                                          content=content, grant_id=grant_id,
                                          operation_key=operation_key)
        if not self.lose_create_answer:
            return created
        # The artifact and its first version really exist; only the answer is lost.
        self.lose_create_answer = False
        return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        if self.raise_on_resolve:
            raise RuntimeError("receipt read exploded")
        if self.unknown_resolution:
            self.calls.append(("resolve_operation", {"operation_key": operation_key}))
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        return super().resolve_operation(operation_key)

    def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_versions", {"artifact_id": artifact_id}))
        return [dict(row) for row in self.versions.get(artifact_id, [])]


class NoLedgerPort(SupplyLikePort):
    """An owner with **no** operation ledger at all: no ``resolve_operation``, and a create that
    does not de-duplicate by key either.  This is the worst case the contract has to name: A
    cannot prove whether a key landed, so it says so instead of pretending."""

    resolve_operation = None  # type: ignore[assignment]

    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("create_artifact", {"workspace_id": workspace_id, "title": title,
                                               "content": content,
                                               "operation_key": operation_key}))
        artifact_id = self._id("artifact:")
        version_id = self.seed(artifact_id=artifact_id, title=title, content=content)
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id,
                        version_id=version_id, version_number=1)


class MarkerTests(unittest.TestCase):
    """第一行标记：全树唯一定义，正文永远原样。"""

    def test_the_marker_is_the_first_line_and_the_body_survives_verbatim(self) -> None:
        body = "## 我们一起排的那一页\n第一行\n\n<!-- 这行像标记，但它只是正文 -->\n最后一行"
        metadata = {"schema": sps.SCHEMA, "state": sps.STATE_OPEN, "title": "t",
                    "practice_revision": 1, "created_at": "2026-11-20T10:00:00+00:00",
                    "updated_at": "2026-11-20T10:00:00+00:00", "materials": [],
                    "contributions": [], "responses": [], "work": {"chars": len(body)},
                    "reopen_count": 0}
        content = sps.build_shared_practice_content(metadata, body)
        self.assertTrue(content.split("\n", 1)[0].startswith("<!-- nyairo-shared-practice:v1 "))
        parsed = sps.parse_shared_practice_content(content)
        self.assertEqual(parsed["parse_error"], "")
        self.assertEqual(parsed["state"], sps.STATE_OPEN)
        self.assertEqual(parsed["body"], body)
        self.assertTrue(sps.is_shared_practice_content(content))
        self.assertEqual(sps.shared_practice_state(content), sps.STATE_OPEN)

    def test_a_later_marker_line_is_only_text(self) -> None:
        content = "正文第一行\n<!-- nyairo-shared-practice:v1 {\"schema\":\"x\"} -->"
        self.assertFalse(sps.is_shared_practice_content(content))
        self.assertEqual(sps.parse_shared_practice_content(content)["state"], "")

    def test_a_broken_or_foreign_schema_fails_closed(self) -> None:
        broken = "<!-- nyairo-shared-practice:v1 {not json} -->\n\n正文"
        parsed = sps.parse_shared_practice_content(broken)
        self.assertEqual(parsed["parse_error"], "MARKER_JSON_INVALID")
        self.assertFalse(sps.is_shared_practice_content(broken))
        other = ("<!-- nyairo-shared-practice:v1 {\"schema\":\"nyairo.shared-practice.v9\","
                 "\"state\":\"OPEN\"} -->\n\n正文")
        self.assertEqual(sps.parse_shared_practice_content(other)["parse_error"],
                         "MARKER_SCHEMA_UNKNOWN:nyairo.shared-practice.v9")
        self.assertFalse(sps.is_shared_practice_content(other))

    def test_a_plain_artifact_is_refused_by_name(self) -> None:
        port = seeded_port()
        service = build(port, actor=sps.CALLER_LIFE, verifier=FakeSourceVerifier())
        # 一件普通的项目工件（读得到，但没有本服务的标记）→ 具名“不是共同实践的一段”。
        port.seed(artifact_id="artifact:plain", title="别的一页", content="就是普通正文，没有标记。")
        answer = service.read_practice("artifact:plain")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sps.REASON_NOT_A_SESSION)
        self.assertNotIn("就是普通正文", json.dumps(answer, ensure_ascii=False))
        # 她的私人阅读材料被 projects owner 明确排除：按 owner 自己的具名理由拒绝，不读正文。
        excluded = service.read_practice(READING)
        self.assertFalse(excluded["ok"])
        self.assertEqual(excluded["reason_code"], pps.REASON_ARTIFACT_EXCLUDED)
        self.assertNotIn("第一页", json.dumps(excluded, ensure_ascii=False))
        # 一件内容坏了标记的工件同样不开绿灯。
        port.seed(artifact_id="artifact:broken", title="坏的",
                  content="<!-- nyairo-shared-practice:v1 {bad} -->\n\n正文")
        answer = service.read_practice("artifact:broken")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sps.REASON_MARKER_INVALID)

    def test_the_marker_line_is_never_returned_as_content(self) -> None:
        port, service, answer = opened()
        view = service.context_view(answer["data"]["session_id"])
        self.assertTrue(view["ok"], view)
        self.assertEqual(view["data"]["text"], TEXT_SENTINEL)
        self.assertNotIn("nyairo-shared-practice:v1",
                         json.dumps(view["data"], ensure_ascii=False))


class RoleTests(unittest.TestCase):
    """谁写哪一半：用户侧写不了主体，主体侧不会替用户说话。"""

    def setUp(self) -> None:
        self.port, self.life, self.opened = opened()
        self.session = self.opened["data"]["session_id"]
        self.user = user_service_for(self.port)

    def test_the_user_side_writes_the_persons_own_words(self) -> None:
        answer = self.user.submit_user_contribution(
            self.session, text=USER_SENTINEL, expected_head=head_of(self.life, self.session),
            operation_key="user:1")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["entry"]["contributor"], sps.CONTRIBUTOR_USER)
        self.assertEqual(answer["data"]["entry"]["kind"], sps.KIND_MESSAGE)
        self.assertFalse(answer["data"]["accepted"])
        self.assertEqual(answer["data"]["joint_state"], sps.JOINT_CANDIDATE)

    def test_the_user_side_cannot_write_the_subjects_half(self) -> None:
        head = head_of(self.life, self.session)
        for method, kwargs in (
            ("submit_subject_response", {"text": "她的话", "basis": {"decision_ref": DECISION_REF}}),
            ("submit_subject_contribution", {"text": "她加的", "basis": {"decision_ref": DECISION_REF}}),
            ("commit_work_text", {"text": "改写正文", "basis": {"decision_ref": DECISION_REF}}),
            ("resume_practice", {"note": "", "basis": {"decision_ref": DECISION_REF}}),
            ("reopen_practice", {"reason": "", "basis": {"decision_ref": DECISION_REF}}),
        ):
            answer = getattr(self.user, method)(self.session, expected_head=head, **kwargs)
            self.assertFalse(answer["ok"], method)
            self.assertEqual(answer["reason_code"], sps.REASON_ACTOR_NOT_ALLOWED, method)
            self.assertEqual(head_of(self.life, self.session), head, method)

    def test_the_user_side_may_not_carry_a_source_at_all(self) -> None:
        answer = self.user.submit_user_contribution(
            self.session, text=USER_SENTINEL, expected_head=head_of(self.life, self.session),
            operation_key="user:withbasis")
        self.assertTrue(answer["ok"], answer)  # 用户侧默认就能写（没有来源）
        # 明确塞来源会被具名拒绝：来源只能由 Life 的读面给。
        guard = self.user._guard("open", basis={"decision_ref": DECISION_REF})
        self.assertEqual(guard["reason_code"], sps.REASON_SOURCE_NOT_ALLOWED)

    def test_the_life_side_may_not_speak_for_the_person(self) -> None:
        answer = self.life.submit_user_contribution(
            self.session, text=USER_SENTINEL, expected_head=head_of(self.life, self.session),
            operation_key="life:user")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sps.REASON_ACTOR_NOT_ALLOWED)

    def test_a_user_contribution_cannot_claim_agreement(self) -> None:
        answer = self.user.submit_user_contribution(
            self.session, text=USER_SENTINEL, expected_head=head_of(self.life, self.session),
            commitment_state=sps.COMMITMENT_ACCEPTED, operation_key="user:accept")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sps.REASON_COMMITMENT_NOT_USERS)
        pending = self.user.submit_user_contribution(
            self.session, text=USER_SENTINEL, expected_head=head_of(self.life, self.session),
            commitment_state=sps.COMMITMENT_PENDING, operation_key="user:pending")
        self.assertTrue(pending["ok"], pending)
        self.assertEqual(pending["data"]["entry"]["commitment_state"], sps.COMMITMENT_PENDING)
        # PENDING 仍然不是"已共同接纳"。
        self.assertFalse(pending["data"]["accepted"])

    def test_opening_is_a_candidate_not_an_acceptance(self) -> None:
        self.assertEqual(self.opened["data"]["joint_state"], sps.JOINT_CANDIDATE)
        self.assertFalse(self.opened["data"]["accepted"])
        view = self.life.read_practice(self.session)["data"]
        self.assertFalse(view["accepted"])
        self.assertEqual(view["joint_state"], sps.JOINT_CANDIDATE)


class SourceVerificationTests(unittest.TestCase):
    """来源只由 owner 的答复说：不信调用方声称。"""

    def test_a_subject_response_needs_a_verified_source(self) -> None:
        port = seeded_port()
        service = build(port, actor=sps.CALLER_LIFE, verifier=None)
        opened = service.open_practice(title=TITLE_SENTINEL, opening_text=TEXT_SENTINEL,
                                       material_refs=["artifact:material-bound"],
                                       operation_key="open:nov")
        self.assertTrue(opened["ok"], opened)
        session = opened["data"]["session_id"]
        head = head_of(service, session)
        unbound = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:unbound")
        self.assertFalse(unbound["ok"])
        self.assertEqual(unbound["reason_code"], sps.REASON_SOURCE_VERIFIER_UNBOUND)
        self.assertEqual(head_of(service, session), head)
        missing = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, expected_head=head, operation_key="resp:noBasis")
        self.assertEqual(missing["reason_code"], sps.REASON_SOURCE_REQUIRED)
        self.assertEqual(head_of(service, session), head)

    def test_four_named_refusals_and_nothing_written(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        verifier = service.source_verifier
        cases = [
            ("decision_ref", "dec:never-existed", sps.REASON_SOURCE_NOT_FOUND),
            ("decision_ref", "dec:other-owner-id", sps.REASON_SOURCE_MISMATCH),
            ("decision_ref", "dec:exploded", sps.REASON_SOURCE_UNREADABLE),
        ]
        verifier.decisions["dec:other-owner-id"] = {"ok": True, "status": "COMMITTED",
                                                    "decision_id": "dec:someone-else"}
        verifier.raise_on.add("dec:exploded")
        verifier.refuse.add("dec:governed")
        cases.append(("decision_ref", "dec:governed", sps.REASON_SOURCE_UNREADABLE))
        for index, (key, ref, expected) in enumerate(cases):
            answer = service.submit_subject_response(
                session, text=RESPONSE_SENTINEL, basis={key: ref}, expected_head=head,
                operation_key="resp:refused:%d" % index)
            self.assertFalse(answer["ok"], ref)
            self.assertEqual(answer["reason_code"], expected, ref)
            self.assertEqual(head_of(service, session), head, ref)
        # 读面确实被问过（不是本地拍脑袋）。
        self.assertTrue(verifier.calls)

    def test_a_verified_source_makes_the_session_joint(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        answer = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, commitment_state=sps.COMMITMENT_ACCEPTED,
            operation_key="resp:1")
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["accepted"])
        self.assertEqual(answer["data"]["joint_state"], sps.JOINT_JOINT)
        entry = answer["data"]["entry"]
        self.assertEqual(entry["contributor"], sps.CONTRIBUTOR_SUBJECT)
        self.assertEqual(entry["kind"], sps.KIND_RESPONSE)
        self.assertTrue(entry["basis"]["verified"])
        self.assertEqual(entry["basis"]["ref"], DECISION_REF)
        self.assertEqual(entry["basis"]["kind"], sps.BASIS_DECISION)
        self.assertEqual(entry["commitment_state"], sps.COMMITMENT_ACCEPTED)

    def test_an_event_reference_is_read_through_the_events_owner(self) -> None:
        port, service, opened_answer = opened()
        verifier = service.source_verifier
        verifier.add_event()
        session = opened_answer["data"]["session_id"]
        answer = service.submit_subject_contribution(
            session, text="她补的一段", basis={"event_ref": EVENT_REF},
            expected_head=head_of(service, session), operation_key="contrib:1")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["entry"]["basis"]["kind"], sps.BASIS_EVENT)
        self.assertIn(("get_event", EVENT_REF), verifier.calls)

    def test_a_source_shape_this_module_does_not_accept_raises(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        for bad in ({"decision_ref": DECISION_REF, "event_ref": EVENT_REF}, {},
                    {"decision_ref": DECISION_REF, "trust_me": "yes"}):
            with self.assertRaises(sps.SharedPracticeContractRefused):
                service.submit_subject_response(session, text="x", basis=bad,
                                                expected_head=head)


class MaterialGovernanceTests(unittest.TestCase):
    """共享材料：读的是 owner **现在**的样子，撤权之后派生文本一个字都不透。"""

    def test_opening_needs_a_readable_material(self) -> None:
        port = seeded_port()
        service = build(port, actor=sps.CALLER_LIFE, verifier=FakeSourceVerifier())
        none = service.open_practice(title=TITLE_SENTINEL, opening_text=TEXT_SENTINEL,
                                     material_refs=[], operation_key="open:none")
        self.assertEqual(none["reason_code"], sps.REASON_MATERIAL_REQUIRED)
        gone = service.open_practice(title=TITLE_SENTINEL, opening_text=TEXT_SENTINEL,
                                     material_refs=["artifact:not-here"],
                                     operation_key="open:gone")
        self.assertEqual(gone["reason_code"], sps.REASON_MATERIAL_UNREADABLE)
        self.assertEqual(gone["data"]["materials"][0]["state"], sps.MATERIAL_NOT_FOUND)
        # 被点名的材料读不到时，一件工件也不建。
        self.assertEqual(service.list_practices()["data"]["count"], 0)

    def test_an_archived_material_withholds_every_derived_text(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        user.submit_user_contribution(session, text=USER_SENTINEL,
                                      expected_head=head_of(service, session),
                                      operation_key="user:1")
        service.submit_subject_response(session, text=RESPONSE_SENTINEL,
                                        basis={"decision_ref": DECISION_REF},
                                        expected_head=head_of(service, session),
                                        operation_key="resp:1")
        service.pause_practice(session, note=NOTE_SENTINEL,
                               expected_head=head_of(service, session),
                               operation_key="pause:1")

        # 材料被归档（owner 自己说的），一切派生文本按构造扣留。
        port.artifacts["artifact:material-bound"]["lifecycle"] = "ARCHIVED"
        answer = service.read_practice(session)
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertTrue(data["governance"]["blocked"])
        self.assertEqual(data["governance"]["reason_code"], sps.REASON_GOVERNED)
        self.assertEqual(data["materials"][0]["state"], sps.MATERIAL_ARCHIVED)
        self.assertFalse(data["private_text_included"])
        blob = json.dumps(answer, ensure_ascii=False)
        for sentinel in (TITLE_SENTINEL, TEXT_SENTINEL, USER_SENTINEL, RESPONSE_SENTINEL,
                         NOTE_SENTINEL):
            self.assertNotIn(sentinel, blob, "受治理的材料挡住之后仍然漏出：%s" % sentinel)
        # 身份/状态/版本事实照旧可读。
        self.assertEqual(data["state"], sps.STATE_PAUSED)
        self.assertEqual(data["joint_state"], sps.JOINT_JOINT)
        self.assertTrue(data["project"]["head_version_id"])
        self.assertEqual(data["contribution_count"], 1)

    def test_a_blocked_material_refuses_new_writes(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        port.artifacts.pop("artifact:material-bound")
        refused = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:blocked")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], sps.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(head_of(service, session), head)
        # 用户侧同样写不进去。
        user = user_service_for(port)
        refused = user.submit_user_contribution(session, text=USER_SENTINEL,
                                                expected_head=head, operation_key="user:blocked")
        self.assertEqual(refused["reason_code"], sps.REASON_MATERIAL_UNAVAILABLE)

    def test_a_moved_material_keeps_the_text_but_refuses_writes_until_reconfirmed(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        # owner 那边材料进了一版新的。
        port.commit_version(artifact_id="artifact:material-bound", expected_head="version-material-bound-1",
                            content=MATERIAL_TEXT + "\n后来又补了一段。", grant_id=GRANT,
                            operation_key="material:v2")
        view = service.read_practice(session)["data"]
        self.assertFalse(view["governance"]["blocked"])
        self.assertTrue(view["governance"]["stale"])
        self.assertEqual(view["materials"][0]["state"], sps.MATERIAL_MOVED)
        self.assertEqual(view["work"]["text"], TEXT_SENTINEL)  # 文本仍然可读
        stale = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:stale")
        self.assertEqual(stale["reason_code"], sps.REASON_MATERIAL_STALE)
        self.assertEqual(head_of(service, session), head)

        reconfirmed = service.reconfirm_materials(session, expected_head=head,
                                                  operation_key="materials:reconfirm")
        self.assertTrue(reconfirmed["ok"], reconfirmed)
        self.assertEqual(reconfirmed["data"]["materials"][0]["version_id"],
                         port.artifacts["artifact:material-bound"]["head_version_id"])
        after = service.read_practice(session)["data"]
        self.assertFalse(after["governance"]["stale"])
        self.assertFalse(after["governance"]["blocked"])

    def test_reconfirm_refuses_a_material_that_is_gone(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        port.artifacts.pop("artifact:material-bound")
        answer = service.reconfirm_materials(session, expected_head=head,
                                             material_refs=["artifact:material-bound"],
                                             operation_key="materials:gone")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sps.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(head_of(service, session), head)


class VersionTests(unittest.TestCase):
    """CAS / 幂等 / UNKNOWN：不写第二个版本，也不盲重做。"""

    def test_a_stale_head_is_a_conflict(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        stale = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head="version-moved-on", operation_key="resp:stale")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], pps.STATUS_CONFLICT)
        self.assertEqual(len(port.versions[session]), 1)

    def test_a_missing_head_is_refused_locally(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        before = len(port.versions[session])
        # expected_head 是 CAS 前置条件：空串不是“随便写”，本地就拒绝（不惊动 owner）。
        with self.assertRaises(sps.SharedPracticeContractRefused):
            user.submit_user_contribution(session, text=USER_SENTINEL, expected_head="")
        self.assertEqual(len(port.versions[session]), before)

    def test_the_same_key_is_one_contribution(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        first = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:fixed")
        self.assertTrue(first["ok"], first)
        versions = len(port.versions[session])
        replay = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:fixed")
        self.assertEqual(replay["status"], pps.STATUS_ALREADY_APPLIED, replay)
        self.assertTrue(replay["data"]["replayed"])
        self.assertFalse(replay["data"]["new_version"])
        self.assertEqual(len(port.versions[session]), versions)
        view = service.read_practice(session)["data"]
        self.assertEqual(view["response_count"], 1)

    def test_the_same_key_with_another_payload_is_named(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        first = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head_of(service, session), operation_key="resp:reused")
        self.assertTrue(first["ok"], first)
        versions = len(port.versions[session])
        clash = service.submit_subject_response(
            session, text="完全不同的另一句", basis={"decision_ref": DECISION_REF},
            expected_head=head_of(service, session), operation_key="resp:reused")
        self.assertFalse(clash["ok"])
        self.assertEqual(clash["reason_code"], sps.REASON_OPERATION_KEY_REUSED)
        self.assertEqual(len(port.versions[session]), versions)
        view = service.read_practice(session)["data"]
        self.assertEqual([row["text"] for row in view["responses"]], [RESPONSE_SENTINEL])

    def test_an_unknown_is_reported_and_not_blindly_retried(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        user = user_service_for(port)
        port.unknown_next = True
        port.forward_on_unknown = False
        port.resolve_to = lap.EffectState.NO_CHANGE  # 真实 owner 对账：这次没有落盘
        answer = user.submit_user_contribution(session, text=USER_SENTINEL,
                                               expected_head=head,
                                               operation_key="user:unknown")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_UNKNOWN)
        self.assertEqual(answer["data"]["retryable"], True)
        self.assertEqual(answer["reason_code"], sps.REASON_OPERATION_NEVER_LANDED)
        self.assertEqual(len(port.versions[session]), 1)
        # owner 已经对账过；服务本身没有偷偷再发一次写。
        self.assertTrue(any(name == "resolve_operation" for name, _ in port.calls))
        self.assertEqual([name for name, _ in port.calls].count("commit_version"), 1)

    def test_an_unknown_that_really_landed_is_reconciled(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        head = head_of(service, session)
        user = user_service_for(port)
        # 版本真的写进去了，只是答复丢了：对账后才认，且不写第二版。
        port.lose_answer_next = True
        answer = user.submit_user_contribution(session, text=USER_SENTINEL,
                                               expected_head=head,
                                               operation_key="user:landed")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], pps.STATUS_APPLIED)
        self.assertTrue(answer["data"]["reconciled"])
        self.assertEqual(len(port.versions[session]), 2)
        view = service.read_practice(session)["data"]
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_SENTINEL])


class LifecycleTests(unittest.TestCase):
    """暂停 / 结束 / 重开：真实版本语义，正文与贡献都不重建。"""

    def test_pause_blocks_writes_and_resume_needs_a_verified_source(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        paused = service.pause_practice(session, note=NOTE_SENTINEL,
                                        expected_head=head_of(service, session),
                                        operation_key="pause:1")
        self.assertTrue(paused["ok"], paused)
        self.assertEqual(paused["data"]["state"], sps.STATE_PAUSED)
        head = head_of(service, session)
        blocked = service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head, operation_key="resp:paused")
        self.assertEqual(blocked["reason_code"], sps.REASON_STATE_NOT_ALLOWED)
        wrong = service.resume_practice(session, expected_head=head,
                                        basis={"event_ref": "contact_evt:never"},
                                        operation_key="resume:bad")
        self.assertEqual(wrong["reason_code"], sps.REASON_SOURCE_NOT_FOUND)
        verifier = service.source_verifier
        resumed = service.resume_practice(session, expected_head=head,
                                          basis={"decision_ref": DECISION_REF},
                                          operation_key="resume:1")
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["data"]["state"], sps.STATE_OPEN)
        self.assertIsNone(service.read_practice(session)["data"]["paused"])

    def test_resume_on_an_open_session_is_named(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        answer = service.resume_practice(session, expected_head=head_of(service, session),
                                         basis={"decision_ref": DECISION_REF},
                                         operation_key="resume:none")
        self.assertEqual(answer["reason_code"], sps.REASON_NOT_PAUSED)

    def test_end_keeps_the_text_and_every_contribution(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        user.submit_user_contribution(session, text=USER_SENTINEL,
                                      expected_head=head_of(service, session),
                                      operation_key="user:1")
        service.submit_subject_response(session, text=RESPONSE_SENTINEL,
                                        basis={"decision_ref": DECISION_REF},
                                        expected_head=head_of(service, session),
                                        operation_key="resp:1")
        ended = service.end_practice(session, reason="今天就到这儿",
                                     expected_head=head_of(service, session),
                                     operation_key="end:1")
        self.assertTrue(ended["ok"], ended)
        self.assertEqual(ended["data"]["state"], sps.STATE_ENDED)
        data = service.read_practice(session)["data"]
        self.assertEqual(data["state"], sps.STATE_ENDED)
        self.assertEqual(data["joint_state"], sps.JOINT_ENDED)
        self.assertEqual(data["work"]["text"], TEXT_SENTINEL)
        self.assertEqual([row["text"] for row in data["contributions"]], [USER_SENTINEL])
        self.assertEqual([row["text"] for row in data["responses"]], [RESPONSE_SENTINEL])
        self.assertTrue(data["ended"]["text"])
        appended = service.submit_subject_response(
            session, text="结束之后", basis={"decision_ref": DECISION_REF},
            expected_head=data["project"]["head_version_id"], operation_key="resp:after")
        self.assertEqual(appended["reason_code"], sps.REASON_ALREADY_ENDED)
        again = service.end_practice(session, expected_head=data["project"]["head_version_id"],
                                     operation_key="end:2")
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)

    def test_reopening_the_same_session_does_not_duplicate_anything(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        user.submit_user_contribution(session, text=USER_SENTINEL,
                                      expected_head=head_of(service, session),
                                      operation_key="user:1")
        service.end_practice(session, reason="先到这儿",
                             expected_head=head_of(service, session), operation_key="end:1")
        head = head_of(service, session)
        reopened = service.reopen_practice(session, reason="接着弄下一页",
                                           basis={"decision_ref": DECISION_REF},
                                           expected_head=head, operation_key="reopen:1")
        self.assertTrue(reopened["ok"], reopened)
        self.assertEqual(reopened["data"]["state"], sps.STATE_OPEN)
        self.assertEqual(reopened["data"]["reopen_count"], 1)
        data = service.read_practice(session)["data"]
        self.assertEqual(data["state"], sps.STATE_OPEN)
        self.assertEqual(data["reopen_count"], 1)
        self.assertEqual(data["work"]["text"], TEXT_SENTINEL)
        self.assertEqual(len(data["contributions"]), 1)  # 没有重复贡献
        self.assertIsNone(data["ended"])
        not_ended = service.reopen_practice(session, basis={"decision_ref": DECISION_REF},
                                           expected_head=data["project"]["head_version_id"],
                                           operation_key="reopen:2")
        self.assertEqual(not_ended["reason_code"], sps.REASON_NOT_ENDED)

    def test_the_work_text_is_replaced_as_one_new_version(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        new_text = TEXT_SENTINEL + "\n\n## 我们一起补的第二段\n她补了一句，我也补了一句。"
        answer = service.commit_work_text(session, text=new_text,
                                          basis={"decision_ref": DECISION_REF},
                                          expected_head=head_of(service, session),
                                          operation_key="work:1")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["text_chars"], len(new_text))
        data = service.read_practice(session, include_history=True)["data"]
        self.assertEqual(data["work"]["text"], new_text)
        self.assertEqual(data["work"]["updated_by"], sps.CALLER_LIFE)
        history = data["history"]
        # owner 的可变列版本史：v2 是 head，v1 仍在（没有被覆盖成“一个版本”）。
        self.assertTrue(history["complete"], history)
        self.assertEqual([row["version_number"] for row in history["entries"]], [2, 1])
        self.assertTrue(history["entries"][0]["is_head"])
        self.assertGreater(history["entries"][1]["content_chars"], 0)


class ReadOnlyTests(unittest.TestCase):
    """列表 / 读 / context 都是纯读：零 create、零 commit、零 archive。"""

    def test_reads_perform_no_owner_writes(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        user.submit_user_contribution(session, text=USER_SENTINEL,
                                      expected_head=head_of(service, session),
                                      operation_key="user:1")
        before = len(port.calls)
        listing = service.list_practices()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["count"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertNotIn("content", json.dumps(listing["data"]["sessions"], ensure_ascii=False))
        self.assertTrue(service.read_practice(session)["ok"])
        self.assertTrue(service.read_practice(session, include_history=True)["ok"])
        self.assertTrue(service.context_view(session)["ok"])
        self.assertTrue(user.list_practices()["ok"])
        writes = [name for name, _payload in port.calls[before:]
                  if name in ("create_artifact", "commit_version", "archive_artifact")]
        self.assertEqual(writes, [])

    def test_the_list_hides_the_text_and_keeps_archived_sessions_visible(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        service.end_practice(session, expected_head=head_of(service, session),
                             operation_key="end:1")
        listing = service.list_practices(include_ended=True)
        self.assertEqual(listing["data"]["count"], 1)
        row = listing["data"]["sessions"][0]
        self.assertEqual(row["state"], sps.STATE_ENDED)
        self.assertEqual(row["title"], TITLE_SENTINEL)
        self.assertNotIn(TEXT_SENTINEL, json.dumps(row, ensure_ascii=False))
        hidden = service.list_practices(include_ended=False)
        self.assertEqual(hidden["data"]["count"], 0)
        self.assertEqual(hidden["data"]["skipped"]["ended"], 1)


class ContextViewTests(unittest.TestCase):
    """context 必须正文实质：真实正文 + 真实贡献 + 明确的"不归我管"。"""

    def test_the_context_carries_the_real_text_and_contributions(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        user.submit_user_contribution(session, text=USER_SENTINEL,
                                      expected_head=head_of(service, session),
                                      commitment_state=sps.COMMITMENT_PENDING,
                                      operation_key="user:1")
        service.submit_subject_response(session, text=RESPONSE_SENTINEL,
                                        basis={"decision_ref": DECISION_REF},
                                        expected_head=head_of(service, session),
                                        operation_key="resp:1")
        view = service.context_view(session)["data"]
        self.assertEqual(view["text"], TEXT_SENTINEL)
        self.assertTrue(view["text_available"])
        self.assertEqual([row["text"] for row in view["contributions"]], [USER_SENTINEL])
        self.assertEqual(view["last_response"]["text"], RESPONSE_SENTINEL)
        self.assertEqual(len(view["pending_commitments"]), 1)
        self.assertEqual(view["pending_commitments"][0]["commitment_state"],
                         sps.COMMITMENT_PENDING)
        self.assertEqual(view["joint_state"], sps.JOINT_JOINT)
        self.assertFalse(view["not_authority"]["activity"])
        self.assertFalse(view["not_authority"]["session_willingness"])
        self.assertNotIn("nyairo-shared-practice:v1", json.dumps(view, ensure_ascii=False))

    def test_the_context_never_publishes_the_private_source_itself(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        plain = service.context_view(session)["data"]
        self.assertNotIn("excerpt", json.dumps(plain["materials"], ensure_ascii=False))
        asked = service.context_view(session, include_material_excerpt=True,
                                     excerpt_chars=20)["data"]
        self.assertIn(asked["materials"][0]["excerpt"], MATERIAL_TEXT)
        self.assertEqual(asked["materials"][0]["state"], sps.MATERIAL_READABLE)

    def test_an_excerpt_is_never_taken_from_a_blocked_source(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        port.artifacts.pop("artifact:material-bound")
        view = service.context_view(session, include_material_excerpt=True)["data"]
        self.assertTrue(view["governance"]["blocked"])
        self.assertNotIn("excerpt", json.dumps(view["materials"], ensure_ascii=False))
        self.assertNotIn(MATERIAL_TEXT, json.dumps(view, ensure_ascii=False))


class BoundsTests(unittest.TestCase):
    def test_a_full_session_refuses_more_entries(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        user = user_service_for(port)
        for index in range(sps.MAX_CONTRIBUTIONS):
            answer = user.submit_user_contribution(
                session, text="第 %d 句" % (index + 1),
                expected_head=head_of(service, session),
                operation_key="user:%d" % index)
            self.assertTrue(answer["ok"], answer)
        overflow = user.submit_user_contribution(session, text="第 25 句",
                                                 expected_head=head_of(service, session),
                                                 operation_key="user:overflow")
        self.assertEqual(overflow["reason_code"], sps.REASON_TOO_MANY)

    def test_the_total_content_bound_is_refused_before_the_owner(self) -> None:
        metadata = {"schema": sps.SCHEMA, "state": sps.STATE_OPEN, "title": "t",
                    "practice_revision": 1, "materials": [], "contributions": [],
                    "responses": [], "work": {}, "reopen_count": 0}
        with self.assertRaises(sps.SharedPracticeContractRefused):
            sps.build_shared_practice_content(metadata, "x" * (sps.MAX_WORK_CHARS + 1))
        with self.assertRaises(sps.SharedPracticeContractRefused):
            sps.build_shared_practice_content({"state": "NOT_A_STATE"}, "正文")


_OWNER_WRITES = ("create_artifact", "commit_version", "archive_artifact")


def owner_writes(port: Any) -> list[str]:
    """Every write verb the owner was asked to perform (empty = this call was pure read)."""
    return [name for name, _payload in port.calls if name in _OWNER_WRITES]


class BareProjects:
    """The smallest projects owner the service accepts: the five methods, no ledger surface."""

    def list_projects(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "status": "ok", "projects": []}

    def read_project(self, _artifact_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "status": pps.STATUS_NOT_FOUND, "reason_code": "PROJECT_NOT_FOUND"}

    def create_project(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "status": pps.STATUS_UNAVAILABLE, "reason_code": "PROJECT_UNAVAILABLE"}

    def commit_project(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "status": pps.STATUS_UNAVAILABLE, "reason_code": "PROJECT_UNAVAILABLE"}

    def close_project(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "status": pps.STATUS_UNAVAILABLE, "reason_code": "PROJECT_UNAVAILABLE"}


class OperationLedgerTests(unittest.TestCase):
    """只读原键对账面：只问 owner 的 receipt / operation 记录，不建第二份账本，绝不重发写。"""

    def test_an_unused_key_is_named_not_landed_and_writes_nothing(self) -> None:
        port = seeded_port()
        service = build(port, actor=sps.CALLER_LIFE)
        before = owner_writes(port)
        answer = service.operation("key:never-used")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_NOT_FOUND)
        self.assertEqual(answer["reason_code"], sps.REASON_OPERATION_NOT_FOUND)
        data = answer["data"]
        self.assertEqual(data["effect"], sps.OPERATION_NOT_LANDED)
        self.assertFalse(data["applied"])
        self.assertFalse(data["recorded"])
        self.assertTrue(data["read_only"])
        self.assertFalse(data["resend"])
        self.assertEqual(data["surface"], "port.resolve_operation")
        self.assertEqual(owner_writes(port), before)

    def test_a_landed_write_is_applied_with_the_session_facts(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        answer = service.operation("open:1")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], pps.STATUS_APPLIED)
        data = answer["data"]
        self.assertEqual(data["effect"], sps.OPERATION_APPLIED)
        self.assertTrue(data["applied"])
        self.assertTrue(data["recorded"])
        self.assertEqual(data["artifact_id"], session)
        self.assertEqual(data["version_id"], port.artifacts[session]["head_version_id"])
        self.assertTrue(data["shared_practice"])
        self.assertEqual(data["session_id"], session)
        self.assertEqual(data["state"], sps.STATE_OPEN)
        self.assertEqual(data["joint_state"], sps.JOINT_CANDIDATE)
        self.assertFalse(data["resend"])

    def test_reading_the_ledger_performs_no_owner_writes(self) -> None:
        port, service, opened_answer = opened()
        session = opened_answer["data"]["session_id"]
        self.assertTrue(service.submit_subject_response(
            session, text=RESPONSE_SENTINEL, basis={"decision_ref": DECISION_REF},
            expected_head=head_of(service, session), operation_key="resp:1")["ok"])
        before = owner_writes(port)
        for key in ("open:1", "resp:1", "key:absent"):
            service.operation(key)
        service.context_view(session)
        self.assertEqual(owner_writes(port), before)

    def test_a_key_for_another_kind_of_artifact_is_not_called_a_session(self) -> None:
        port = seeded_port()
        port.seed(artifact_id="artifact:plain", title="普通一页", content="普通正文，没有标记。")
        port.operations["key:other"] = {"artifact_id": "artifact:plain", "version_id": "v-1",
                                        "version_number": 1}
        service = build(port, actor=sps.CALLER_LIFE)
        answer = service.operation("key:other")
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["applied"])
        self.assertFalse(answer["data"]["shared_practice"])
        self.assertNotIn("session_id", answer["data"])
        self.assertEqual(answer["data"]["artifact_id"], "artifact:plain")

    def test_an_unknown_resolution_keeps_the_key_and_stops(self) -> None:
        port = seeded_port()
        port.unknown_resolution = True
        service = build(port, actor=sps.CALLER_LIFE)
        answer = service.operation("key:unknown")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_UNKNOWN)
        self.assertEqual(answer["reason_code"], sps.REASON_OPERATION_UNKNOWN)
        self.assertFalse(answer["data"]["retryable"])
        self.assertFalse(answer["data"]["resend"])

    def test_a_failed_receipt_read_is_unavailable_and_not_a_write(self) -> None:
        port = seeded_port()
        port.raise_on_resolve = True
        service = build(port, actor=sps.CALLER_LIFE)
        before = owner_writes(port)
        answer = service.operation("key:unreadable")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_UNAVAILABLE)
        self.assertEqual(answer["reason_code"], sps.REASON_OPERATION_UNAVAILABLE)
        self.assertEqual(answer["data"]["effect"], sps.OPERATION_UNAVAILABLE)
        self.assertEqual(answer["data"]["owner_reason"], "RuntimeError")
        self.assertEqual(owner_writes(port), before)

    def test_an_owner_without_a_ledger_is_named(self) -> None:
        service = sps.SharedPracticeService(BareProjects(), actor=sps.CALLER_LIFE)
        self.assertEqual(service.describe()["operation_lookup"], "")
        answer = service.operation("key:anything")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_UNAVAILABLE)
        self.assertEqual(answer["reason_code"], sps.REASON_OPERATION_UNSUPPORTED)
        self.assertEqual(answer["data"]["surface"], "")
        self.assertFalse(answer["data"]["resend"])

    def test_an_empty_key_is_a_contract_error(self) -> None:
        _port, service, _opened_answer = opened()
        for bad in ("", "   ", None, 7):
            with self.assertRaises(sps.SharedPracticeContractRefused):
                service.operation(bad)


class OpenReplayTests(unittest.TestCase):
    """同 key 同意图的开场：跨服务重建也是一个会话；同 key 异意图具名拒绝。"""

    OPEN_ARGS = dict(title=TITLE_SENTINEL, opening_text=TEXT_SENTINEL,
                     material_refs=["artifact:material-bound"])

    def _service(self, port: Any, actor: str = sps.CALLER_LIFE) -> sps.SharedPracticeService:
        return build(port, actor=actor, verifier=FakeSourceVerifier())

    def test_the_same_key_and_intent_returns_the_same_session(self) -> None:
        port = seeded_port()
        service = self._service(port)
        first = service.open_practice(operation_key="open:fixed", **self.OPEN_ARGS)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["data"]["operation_check"], sps.OPERATION_NOT_LANDED)
        self.assertFalse(first["data"]["replayed"])
        session = first["data"]["session_id"]
        artifacts = len(port.artifacts)
        versions = len(port.versions[session])
        before = owner_writes(port)

        again = service.open_practice(operation_key="open:fixed", **self.OPEN_ARGS)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(again["data"]["session_id"], session)
        self.assertEqual(again["data"]["operation_check"], sps.OPERATION_APPLIED)
        self.assertTrue(again["data"]["replayed"])
        self.assertFalse(again["data"]["new_version"])
        self.assertEqual(again["data"]["version_id"], first["data"]["version_id"])
        self.assertEqual(again["data"]["materials"][0]["artifact_id"], "artifact:material-bound")
        self.assertEqual(len(port.artifacts), artifacts)
        self.assertEqual(len(port.versions[session]), versions)
        self.assertEqual(owner_writes(port), before)

    def test_the_replay_holds_across_a_rebuilt_service(self) -> None:
        port = seeded_port()
        first = self._service(port).open_practice(operation_key="open:reboot", **self.OPEN_ARGS)
        session = first["data"]["session_id"]
        artifacts = len(port.artifacts)
        rebuilt = self._service(port)  # a fresh service object on the same owner state
        again = rebuilt.open_practice(operation_key="open:reboot", **self.OPEN_ARGS)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(again["data"]["session_id"], session)
        self.assertEqual(len(port.artifacts), artifacts)

    def test_a_replay_after_later_writes_still_matches_the_open(self) -> None:
        port = seeded_port()
        verifier = FakeSourceVerifier()
        verifier.add_decision()
        service = build(port, actor=sps.CALLER_LIFE, verifier=verifier)
        first = service.open_practice(operation_key="open:later", **self.OPEN_ARGS)
        session = first["data"]["session_id"]
        user = build(port, actor=sps.CALLER_USER)
        self.assertTrue(user.submit_user_contribution(
            session, text=USER_SENTINEL, expected_head=head_of(service, session),
            operation_key="user:1")["ok"])
        self.assertTrue(service.commit_work_text(
            session, text="改过的正文", basis={"decision_ref": DECISION_REF},
            expected_head=head_of(service, session), operation_key="work:1")["ok"])
        artifacts = len(port.artifacts)
        again = service.open_practice(operation_key="open:later", **self.OPEN_ARGS)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)
        self.assertEqual(again["data"]["session_id"], session)
        self.assertEqual(again["data"]["contribution_count"], 1)
        self.assertEqual(len(port.artifacts), artifacts)

    def test_a_different_intent_on_the_same_key_is_named(self) -> None:
        port = seeded_port()
        port.seed(artifact_id="artifact:plain", title="普通一页", content="普通正文，没有标记。")
        service = self._service(port)
        first = service.open_practice(operation_key="open:clash", **self.OPEN_ARGS)
        session = first["data"]["session_id"]
        artifacts = len(port.artifacts)
        for changed in ({"title": "另一个标题"},
                        {"opening_text": "另一段开场白"},
                        {"material_refs": ["artifact:plain"]}):
            args = dict(self.OPEN_ARGS)
            args.update(changed)
            clash = service.open_practice(operation_key="open:clash", **args)
            self.assertFalse(clash["ok"], changed)
            self.assertEqual(clash["reason_code"], sps.REASON_OPERATION_KEY_REUSED, changed)
            self.assertEqual(len(port.artifacts), artifacts, changed)
        self.assertEqual(len(port.versions[session]), 1)

    def test_an_unresolved_key_is_not_written_again(self) -> None:
        port = seeded_port()
        port.unknown_resolution = True
        service = self._service(port)
        artifacts = len(port.artifacts)
        answer = service.open_practice(operation_key="open:unclear", **self.OPEN_ARGS)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_UNKNOWN)
        self.assertEqual(answer["data"]["operation_check"], sps.OPERATION_UNKNOWN)
        self.assertEqual(len(port.artifacts), artifacts)
        self.assertEqual(owner_writes(port), [])

    def test_an_owner_without_a_ledger_says_the_idempotency_is_unverified(self) -> None:
        port = seeded_port(NoLedgerPort())
        service = self._service(port)
        first = service.open_practice(operation_key="open:noledger", **self.OPEN_ARGS)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["data"]["operation_check"], sps.OPERATION_UNAVAILABLE)
        self.assertTrue(first["data"]["idempotency_unverified"])
        artifacts = len(port.artifacts)
        second = service.open_practice(operation_key="open:noledger", **self.OPEN_ARGS)
        self.assertTrue(second["ok"], second)
        # 这个 owner 上 A 无法证明键没落过：如实再开一段并在答复里标出来，而不是静默分叉。
        self.assertEqual(len(port.artifacts), artifacts + 1)
        self.assertNotEqual(second["data"]["session_id"], first["data"]["session_id"])
        self.assertTrue(second["data"]["idempotency_unverified"])

    def test_an_open_without_a_key_is_marked_skipped(self) -> None:
        port = seeded_port()
        service = self._service(port)
        answer = service.open_practice(**self.OPEN_ARGS)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["operation_check"], "SKIPPED")
        self.assertTrue(str(answer["data"]["operation_key"]).startswith("shared-practice:"))
        self.assertFalse(answer["data"]["idempotency_unverified"])
        # 没给 key 就不去重：再开一次是**另一段**，不是重放。
        second = service.open_practice(**self.OPEN_ARGS)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(second["data"]["session_id"], answer["data"]["session_id"])


class FactoryTests(unittest.TestCase):
    def test_a_half_wired_environment_answers_none(self) -> None:
        self.assertIsNone(sps.shared_practice_service_from_env({}, {}, actor=sps.CALLER_USER))
        self.assertIsNone(sps.shared_practice_service_from_env(
            {}, {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE}, actor=sps.CALLER_USER))
        self.assertIsNone(sps.shared_practice_service_from_env(
            {}, {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE, "NYAIRO_ARTIFACT_GRANT": GRANT,
                 "NYAIRO_LIFE_SUBJECT": SUBJECT}, actor=sps.CALLER_USER))

    def test_a_ready_environment_builds_the_caller_role(self) -> None:
        env = {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE, "NYAIRO_ARTIFACT_GRANT": GRANT,
               "NYAIRO_LIFE_SUBJECT": SUBJECT}
        service = sps.shared_practice_service_from_env(seeded_port(), env,
                                                       actor=sps.CALLER_USER)
        self.assertIsNotNone(service)
        self.assertEqual(service.actor, sps.CALLER_USER)
        self.assertEqual(service.subject_id, SUBJECT)
        self.assertFalse(service.describe()["source_verifier_bound"])
        self.assertFalse(service.describe()["owns_activity"])
        # 用户侧没有读面：任何主体的写入都是具名拒绝，而不是硬造一个来源。
        listed = service.list_practices()
        self.assertTrue(listed["ok"], listed)

    def test_the_actor_is_mandatory(self) -> None:
        with self.assertRaises(TypeError):
            sps.SharedPracticeService(pps.PrivateProjectService(seeded_port(), WORKSPACE, GRANT))
        with self.assertRaises(sps.SharedPracticeContractRefused):
            build(seeded_port(), actor="administrator")

    def test_the_service_requires_a_projects_owner(self) -> None:
        with self.assertRaises(sps.SharedPracticeUnavailable):
            sps.SharedPracticeService(None, actor=sps.CALLER_USER)

    def test_the_operation_key_is_stable_and_scoped(self) -> None:
        first = sps.shared_practice_operation_key("practice", "user", "artifact:x", "h", "1")
        again = sps.shared_practice_operation_key("practice", "user", "artifact:x", "h", "1")
        other = sps.shared_practice_operation_key("practice", "user", "artifact:x", "h", "2")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("shared-practice:"))
        with self.assertRaises(sps.SharedPracticeContractRefused):
            sps.shared_practice_operation_key()


if __name__ == "__main__":
    unittest.main()
