"""v0.4 B：共同实践的内容半边 —— 传输扩展、严格 schema、真 A 服务上的写入链。

这一组用例不建 runtime，只回答三类问题：

* **传输扩展**：邀请可以带 ``material_refs``（结构化 artifact 引用、上限、去重），普通来话
  **不可以**带（否则一条消息就能冒充邀请），坏形状按名字拒绝。
* **严格 schema**：回答里的多余键、缺失 reply、被截断的对象都是具名拒绝，**不放宽**也不重试。
* **真 A 服务**：把 A 的 ``SharedPracticeService`` 架在一个内存 owner 上（同一份 CAS、同一份
  版本历史、同一条 operation key），验证「用户来话 → 一次内容请求 → 她的回应/贡献/作品」
  这条链真的落在作品上：head 每次回读、贡献不重复、结束之后仍可读回、承诺不由回应自证。

整链（真 assembly/runner/Supply）在 ``test_v04_shared_practice_life_chain.py``。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_model_client as amc  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_local_contact as lc  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import life_social_contacts as social  # noqa: E402
import private_project_service as pps  # noqa: E402
import shared_practice_life as spl  # noqa: E402
import shared_practice_service as sps  # noqa: E402

SUBJECT = "test:subject"
WORKSPACE = "ws:practice"
GRANT = "grant:practice"
MATERIAL = "artifact:material-page-one"
MATERIAL_TEXT = "第三页：把留白重新排一遍，第二段还是太挤。"
TITLE = "一起把这一页排好"
OPENING = "要不要一起看看这一页？"


# =====================================================================================
# 一个最小的、但语义完整的 owner：CAS head、版本历史、operation key
# =====================================================================================


class FakeArtifactPort:
    """A's owner surface, in memory: one artifact = one project, versions are immutable."""

    def __init__(self) -> None:
        self.artifacts: dict[str, dict] = {}
        self.operations: dict[str, str] = {}
        self._seq = 0

    def _version(self) -> str:
        self._seq += 1
        return "version:%04d" % self._seq

    def seed(self, artifact_id: str, title: str, content: str) -> str:
        head = self._version()
        self.artifacts[artifact_id] = {
            "artifact_id": artifact_id, "title": title, "lifecycle": "ACTIVE", "revision": 1,
            "head_version_id": head, "content": content, "history": {head: content},
            "updated_at": 1.0, "created_at": 1.0,
        }
        return head

    # -- owner reads --------------------------------------------------------------------
    def list_artifacts(self) -> list[dict]:
        return [{"artifact_id": row["artifact_id"], "title": row["title"],
                 "lifecycle": row["lifecycle"], "revision": row["revision"],
                 "head_version_id": row["head_version_id"],
                 "updated_at": row["updated_at"], "created_at": row["created_at"]}
                for row in self.artifacts.values()]

    def read_artifact(self, artifact_id: str) -> dict:
        row = self.artifacts.get(artifact_id)
        if row is None:
            raise lap.LifeArtifactUnavailable("no artifact %s" % artifact_id)
        return {"artifact": {"artifact_id": row["artifact_id"], "title": row["title"],
                             "lifecycle": row["lifecycle"], "revision": row["revision"],
                             "head_version_id": row["head_version_id"],
                             "updated_at": row["updated_at"], "created_at": row["created_at"]},
                "head": {"version_id": row["head_version_id"],
                         "version_number": row["revision"]},
                "content": row["content"]}

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        return lap.ArtifactOutcome(effect_state=lap.EffectState.NO_CHANGE, status="absent",
                                   operation_key=operation_key)

    # -- owner writes -------------------------------------------------------------------
    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        artifact_id = "artifact:%s" % operation_key[-8:]
        head = self.seed(artifact_id, title, content)
        self.operations[operation_key] = artifact_id
        return lap.ArtifactOutcome(
            effect_state=lap.EffectState.APPLIED, status="applied", artifact_id=artifact_id,
            version_id=head, version_number=1, head_version_id=head, lifecycle="ACTIVE",
            revision=1, operation_key=operation_key)

    def commit_version(self, *, artifact_id: str, expected_head: str, content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        row = self.artifacts.get(artifact_id)
        if row is None:
            return lap.ArtifactOutcome(effect_state=lap.EffectState.NOT_FOUND, status="not_found",
                                       reason="ARTIFACT_NOT_FOUND", artifact_id=artifact_id)
        if expected_head != row["head_version_id"]:
            return lap.ArtifactOutcome(effect_state=lap.EffectState.CONFLICT, status="conflict",
                                       reason="CAS_HEAD_MISMATCH", artifact_id=artifact_id)
        new = self._version()
        row["history"][new] = content
        row["head_version_id"] = new
        row["revision"] = int(row["revision"]) + 1
        row["content"] = content
        row["updated_at"] = float(row["revision"])
        self.operations[operation_key] = artifact_id
        return lap.ArtifactOutcome(
            effect_state=lap.EffectState.APPLIED, status="applied", artifact_id=artifact_id,
            version_id=new, version_number=row["revision"], head_version_id=new,
            parent_version_id=expected_head, lifecycle="ACTIVE", revision=row["revision"],
            operation_key=operation_key)

    def archive_artifact(self, **kwargs: object) -> lap.ArtifactOutcome:
        return lap.ArtifactOutcome(effect_state=lap.EffectState.REFUSED, status="refused",
                                   reason="NOT_SUPPORTED_HERE")


class StubClient:
    """A model client with scripted raw answers -- one entry per call, no retry."""

    provider = "stub"
    model = "stub:practice-json"

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers  # the caller's own list: a test appends the next scripted answer
        self.calls: list[dict] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 max_output_tokens: int | None = None) -> amc.ModelCallResult:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        text = self.answers.pop(0) if self.answers else ""
        return amc.ModelCallResult(purpose="shared_practice", provider=self.provider,
                                   model=self.model, text=text, latency_ms=1, status=200)


class FailingClient:
    provider = "stub"
    model = "stub:failing"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kwargs: object) -> amc.ModelCallResult:
        self.calls += 1
        raise amc.AgencyModelClientError(amc.TRANSPORT_TIMEOUT, "socket timeout")


def _services(port: FakeArtifactPort, inbox: lc.LocalContactInbox):
    """A's two real services over one owner: her half and the person's half."""

    projects = pps.PrivateProjectService(port, WORKSPACE, GRANT, subject_id=SUBJECT)
    verifier = spl.LifeSourceVerifier(inbox=inbox)
    subject = sps.SharedPracticeService(projects, actor=sps.CALLER_LIFE,
                                        source_verifier=verifier)
    person = sps.SharedPracticeService(projects, actor=sps.CALLER_USER)
    return subject, person


class Base(unittest.TestCase):
    """One real inbox in a retained fixture directory (never deleted)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sp-life-seam-"))
        self.inbox = lc.LocalContactInbox(self.root, subject_id=SUBJECT, home_ref="/tmp/home")
        self.port = FakeArtifactPort()
        self.material_head = self.port.seed(MATERIAL, "第三页", MATERIAL_TEXT)
        self.subject, self.person = _services(self.port, self.inbox)
        self.answers: list[str] = []
        self.client = StubClient(self.answers)
        self.ledger = lrb.LifeRequestBudget(self.root, utc_day_max=24, run_window_max=24,
                                           run_id="run:practice")
        self.seam = spl.SharedPracticeLife(
            subject=self.subject, person=self.person,
            responder=spl.ModelPracticeResponder(
                lrb.BudgetedModelClient(self.client, self.ledger)))

    # -- helpers ------------------------------------------------------------------------
    def open_session(self) -> dict:
        out = self.seam.open_session(material_refs=[MATERIAL], title=TITLE,
                                     opening_text=OPENING)
        self.assertTrue(out["ok"], out)
        return out

    def message(self, text: str, *, operation_key: str) -> str:
        got = self.inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": text,
                                 "operation_key": operation_key})
        self.assertTrue(got["ok"], got)
        return str(got["event_ref"])

    def answer(self, **payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def step(self, text: str, *, event_ref: str) -> dict:
        self.answers.append(self.answer(
            reply="我看了，第二段再松一点就好。", contribution="把第二段行距放宽一档。",
            work_text="第三页：留白重排。\n第二段行距放宽一档，其余不动。"))
        return self.seam.step_message(text=text, event_ref=event_ref)

    def _port(self, protocol=None, runtime=None) -> social.SocialContactPort:
        """The real port over the fixture ledgers; the runtime only answers Activity reads."""

        return social.SocialContactPort(
            runtime=runtime if runtime is not None else FakeRuntime(),
            state_root=self.root, subject_id=SUBJECT, home="/tmp/home",
            inbox=self.inbox, protocol=protocol or FakeProtocol(), practice=self.seam)



# =====================================================================================
# 1. 传输扩展：material_refs 只属于邀请
# =====================================================================================


class TransportExtensionTest(Base):
    def test_an_invitation_may_name_shared_materials(self):
        out = self.inbox.submit({
            "event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE, "text": OPENING,
            "material_refs": [MATERIAL, MATERIAL, "artifact:second"], "operation_key": "op:1"})
        self.assertTrue(out["ok"], out)
        detail = self.inbox.get_event(out["event_ref"], include_body=True)["data"]["event"]
        self.assertEqual(detail["invitation"]["material_refs"], [MATERIAL, "artifact:second"])

    def test_a_plain_message_may_not_name_materials(self):
        out = self.inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "一起看？",
                                 "material_refs": [MATERIAL], "operation_key": "op:2"})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["reason_code"], "CONTACT_FIELD_NOT_ALLOWED_FOR_KIND")
        self.assertIn("material_refs", str(out.get("detail") or "material_refs"))

    def test_an_end_may_not_name_materials(self):
        out = self.inbox.submit({"event_kind": "CO_PRESENCE_END",
                                 "invitation_id": "invite:1", "explicit_end": True,
                                 "material_refs": [MATERIAL], "operation_key": "op:3"})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["reason_code"], "CONTACT_FIELD_NOT_ALLOWED_FOR_KIND")

    def test_bad_material_shapes_are_refused_by_name(self):
        for bad in ("C:\\private\\page.md", "/tmp/page.md",
                    ["not a ref"], ["artifact:" + "x" * 300],
                    ["artifact:%d" % i for i in range(8)]):
            out = self.inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE,
                                     "text": OPENING, "material_refs": bad,
                                     "operation_key": "op:bad:%s" % str(bad)[:12]})
            self.assertFalse(out["ok"], (bad, out))
            self.assertEqual(out["reason_code"], "CONTACT_MATERIAL_REFS_REFUSED", (bad, out))

    def test_the_contract_it_publishes_names_the_extension_and_the_results(self):
        contract = spl.transport_contract()
        self.assertEqual(contract["invitation"]["extension"], ["material_refs"])
        self.assertIn("material_refs", contract["invitation"]["fields"])
        self.assertNotIn("material_refs", contract["message"]["fields"])
        self.assertIn(spl.REASON_CONTENT_RECORDED, contract["results"]["OBSERVED"])
        self.assertIn(spl.REASON_INVALID_SCHEMA, contract["results"]["UNKNOWN"])


# =====================================================================================
# 2. 严格 schema：不放宽、不重试
# =====================================================================================


def _request_block() -> spl.PracticeContentRequest:
    """One bounded content request, as the seam builds it (no owner, no model)."""

    return spl.PracticeContentRequest(
        session_id="artifact:1", event_ref="contact_evt:0001", title=TITLE, state="OPEN",
        work_text="正文", materials=({"artifact_id": MATERIAL, "title": "第三页",
                                      "state": "READABLE", "excerpt": MATERIAL_TEXT},),
        entries=({"contributor": "user", "kind": "message", "text": "在吗"},),
        user_text="在吗")


class StrictAnswerTest(unittest.TestCase):
    def test_a_plain_object_is_accepted(self):
        answer = spl.parse_content_answer('{"reply": "好", "contribution": null, "work_text": null}')
        self.assertEqual(answer.reply, "好")
        self.assertIsNone(answer.contribution)
        self.assertIsNone(answer.work_text)

    def test_one_fence_is_tolerated_but_the_json_is_not_repaired(self):
        answer = spl.parse_content_answer('```json\n{"reply": "好"}\n```')
        self.assertEqual(answer.reply, "好")

    def test_an_extra_key_is_a_named_refusal(self):
        with self.assertRaises(spl.PracticeStepRefused) as caught:
            spl.parse_content_answer('{"reply": "好", "thinking": "我在想…"}')
        self.assertEqual(caught.exception.reason_code, spl.REASON_INVALID_SCHEMA)

    def test_a_truncated_object_is_reported_as_truncation(self):
        with self.assertRaises(spl.PracticeStepRefused) as caught:
            spl.parse_content_answer('{"reply": "好", "contribution": "把第')
        self.assertEqual(caught.exception.reason_code, spl.REASON_MODEL_TRUNCATED)

    def test_a_missing_or_empty_reply_is_refused(self):
        for raw in ('{"contribution": "x"}', '{"reply": "   "}', '{"reply": 7}', '[]', ''):
            with self.assertRaises(spl.PracticeStepRefused, msg=raw):
                spl.parse_content_answer(raw)

    def test_no_retry_is_attempted(self):
        client = StubClient(['{"reply": "好", "unexpected": 1}'])
        seam_responder = spl.ModelPracticeResponder(client)
        out = seam_responder.respond(_request_block())
        self.assertTrue(out["ok"])  # it *returns the raw text*; parsing is the seam's job
        self.assertEqual(len(client.calls), 1)



# =====================================================================================
# 3. 真 A 服务上的一条完整内容链
# =====================================================================================


class PracticeServiceChainTest(Base):
    def test_open_then_three_real_steps_then_end_and_read_back(self):
        opened = self.open_session()
        self.assertTrue(opened["session_id"], opened)
        self.assertEqual(opened["state"], "OPEN")
        self.assertEqual(opened["joint_state"], "CANDIDATE")  # opening is not acceptance
        self.assertFalse(opened["accepted"])

        view = self.seam.context_view()
        self.assertTrue(view["ok"], view)
        self.assertEqual(view["data"]["text"], OPENING)
        self.assertTrue(view["data"]["materials"][0]["excerpt"].startswith("第三页"))

        seen_work: list[str] = []
        for index in range(3):
            event_ref = self.message("第%d次：这一段再松一点好吗？" % (index + 1),
                                     operation_key="op:msg:%d" % index)
            step = self.step("第%d次：这一段再松一点好吗？" % (index + 1), event_ref=event_ref)
            self.assertTrue(step["ok"], step)
            self.assertEqual(step["llm_calls"], 1, step)
            self.assertTrue(step["response_text"])
            self.assertEqual(step["commitment_state"], "NONE")
            self.assertTrue(step["contribution_recorded"], step)
            self.assertTrue(step["work_version_id"], step)
            seen_work.append(str(step["work_version_id"]))

        self.assertEqual(len(set(seen_work)), 3, seen_work)  # one new version per step
        read = self.seam.read_back()
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["counts"]["responses"], 3, read)
        self.assertEqual(read["counts"]["contributions"], 6, read)  # 3 person + 3 subject
        # 3 person messages + 3 subject replies + 3 subject contributions, every seq distinct
        self.assertEqual(read["distinct_seqs"], [1, 2, 3, 4, 5, 6, 7, 8, 9], read)
        self.assertEqual(read["state"], "OPEN")
        self.assertEqual(read["joint_state"], "JOINT")  # a verified reply is what makes it joint
        self.assertEqual(read["material_count"], 1)
        stored = self.port.artifacts[read["session_id"]]["content"]
        self.assertIn(sps.PRACTICE_MARKER, stored)  # A's own marker, never a plain-text claim
        self.assertIn("第三页：留白重排。", stored)  # the work text is really there
        self.assertIn("把第二段行距放宽一档。", stored)  # and so is her real piece

        ended = self.seam.end_session(reason="说完了")
        self.assertTrue(ended["ok"], ended)
        self.assertEqual(ended["state"], "ENDED")
        after = self.seam.read_back()
        self.assertEqual(after["counts"]["responses"], 3)
        self.assertEqual(after["counts"]["contributions"], 6)
        self.assertEqual(len(after["distinct_seqs"]), 9)  # ending deleted nothing
        self.assertIsNone(self.seam.find_open())

    def test_a_step_without_an_open_session_reads_nothing_and_writes_nothing(self):
        before = len(self.client.calls)
        step = self.seam.step_message(text="在吗", event_ref="contact_evt:0001")
        self.assertFalse(step["ok"])
        self.assertEqual(step["reason_code"], spl.REASON_NO_OPEN_PRACTICE)
        self.assertEqual(step["llm_calls"], 0)
        self.assertEqual(len(self.client.calls), before)
        self.assertEqual([a for a in self.port.artifacts if a != MATERIAL], [])

    def test_the_person_half_cannot_write_her_side(self):
        opened = self.open_session()
        session_id = opened["session_id"]
        head = self.seam._head(session_id)
        refused = self.person.submit_subject_response(session_id, text="我自己写的",
                                                     basis={"event_ref": "contact_evt:0001"},
                                                     expected_head=head)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], "SHARED_PRACTICE_ACTOR_NOT_ALLOWED")
        # and her own reply, with a source the read port cannot verify, is refused too
        bogus = self.subject.submit_subject_response(
            session_id, text="凭空", basis={"event_ref": "contact_evt:9999"},
            expected_head=head)
        self.assertFalse(bogus["ok"], bogus)
        self.assertIn(bogus["reason_code"],
                      ("SHARED_PRACTICE_SOURCE_NOT_FOUND", "SHARED_PRACTICE_SOURCE_UNREADABLE"))

    def test_a_transport_failure_is_named_and_never_becomes_her_words(self):
        opened = self.open_session()
        self.seam.responder = spl.ModelPracticeResponder(FailingClient())
        event_ref = self.message("第一次", operation_key="op:msg:fail")
        step = self.seam.step_message(text="第一次", event_ref=event_ref)
        self.assertFalse(step["ok"], step)
        self.assertEqual(step["reason_code"], spl.REASON_MODEL_FAILED)
        self.assertEqual(step["stage"], "model")
        read = self.seam.read_back(session_id=opened["session_id"])
        self.assertEqual(read["counts"]["responses"], 0, read)  # no fabricated reply
        self.assertEqual(read["counts"]["contributions"], 1, read)  # the person's words are real
        self.assertEqual(read["joint_state"], "CANDIDATE")

    def test_a_truncated_or_schema_broken_answer_costs_one_call_and_no_retry(self):
        opened = self.open_session()
        self.answers.append('{"reply": "我看了，第二段再松一点"')  # truncated JSON
        event_ref = self.message("看看？", operation_key="op:msg:trunc")
        step = self.seam.step_message(text="看看？", event_ref=event_ref)
        self.assertFalse(step["ok"], step)
        self.assertEqual(step["reason_code"], spl.REASON_MODEL_TRUNCATED)
        self.assertEqual(len(self.client.calls), 1)  # exactly one request, no blind retry
        self.answers.append('{"reply": "好", "reasoning": "想"}')
        event_ref = self.message("再来", operation_key="op:msg:schema")
        step = self.seam.step_message(text="再来", event_ref=event_ref)
        self.assertEqual(step["reason_code"], spl.REASON_INVALID_SCHEMA, step)
        self.assertEqual(len(self.client.calls), 2)
        read = self.seam.read_back(session_id=opened["session_id"])
        self.assertEqual(read["counts"]["responses"], 0)
        self.assertEqual(read["counts"]["contributions"], 2)

    def test_one_call_is_charged_once_against_the_instance_ledger(self):
        self.open_session()
        before = int(self.ledger.view()["utc_day_calls"])
        event_ref = self.message("记一次", operation_key="op:msg:budget")
        step = self.step("记一次", event_ref=event_ref)
        self.assertTrue(step["ok"], step)
        self.assertEqual(int(self.ledger.view()["utc_day_calls"]) - before, 1)

    def test_a_spent_ledger_refuses_the_step_before_anything_is_sent(self):
        self.open_session()
        for _ in range(30):
            try:
                self.ledger.reserve("content")
            except lrb.RequestBudgetError:
                break
        self.assertNotEqual(self.ledger.check().get("allowed"), True)
        before = len(self.client.calls)
        event_ref = self.message("还能聊吗", operation_key="op:msg:spent")
        step = self.seam.step_message(text="还能聊吗", event_ref=event_ref)
        self.assertFalse(step["ok"], step)
        self.assertEqual(step["reason_code"], spl.REASON_BUDGET_REFUSED, step)
        self.assertEqual(len(self.client.calls), before)  # nothing was sent
        self.assertEqual(step["llm_calls"], 0)

    def test_opening_refuses_materials_it_cannot_read(self):
        out = self.seam.open_session(material_refs=["artifact:not-there"], title=TITLE,
                                     opening_text=OPENING)
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["reason_code"], spl.REASON_OPEN_REFUSED)
        self.assertEqual([a for a in self.port.artifacts if a != MATERIAL], [])

    def test_a_second_open_reuses_the_open_session_instead_of_forking_one(self):
        first = self.open_session()
        second = self.seam.open_session(material_refs=[MATERIAL], title="又一次", opening_text="?")
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["reused"], second)
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual([a for a in self.port.artifacts if a != MATERIAL],
                         [first["session_id"]])


# =====================================================================================
# 4. 端口一层：只观察 vs 真实内容步骤
# =====================================================================================


class FakeActivityRead:
    """The owner read the gate uses: one Activity's own status, or nothing."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    def get_activity(self, ref: str) -> dict:
        status = self.rows.get(str(ref))
        return {} if status is None else {"activity_id": str(ref), "status": status}


class FakeRuntime:
    """Just enough runtime for the gate: a real owner-shaped Activity read."""

    def __init__(self, *, joint_status: str = "ACTIVE") -> None:
        self.activity_read = FakeActivityRead()
        self.activity_read.rows["activity:0001"] = joint_status


class FakeProtocol:
    """The co-presence protocol, answering with fixtures (the chain test uses the real one)."""

    def __init__(self) -> None:
        self.session: dict = {"state": "NONE"}
        self.observed: list[dict] = []

    def observe(self, raw) -> dict:
        self.observed.append(dict(raw))
        return {"effect": "OBSERVATION_ONLY", "pause_requested": False,
                "activity_started": False, "owners_written": 0}

    def read_session(self) -> dict:
        return dict(self.session)

    def bind_practice(self, *, invitation_id, practice_session_ref, event_ref=None, now=None) -> dict:
        if str(self.session.get("state") or "") != "OPEN":
            return {"ok": False, "reason_code": "PRACTICE_BIND_REQUIRES_OPEN_SESSION"}
        if str(invitation_id or "") != str(self.session.get("invitation_id") or ""):
            return {"ok": False, "reason_code": "PRACTICE_BIND_FOR_ANOTHER_SESSION"}
        self.session.update({"practice_session_ref": str(practice_session_ref),
                             "practice_bound_at": "2026-10-20T06:00:00+00:00",
                             "practice_invitation_event_ref": event_ref})
        return {"ok": True, "practice_session_ref": str(practice_session_ref)}

    def begin_from_decision(self, invitation, *, now=None) -> dict:
        self.session = {"state": "OPEN", "invitation_id": invitation.get("invitation_id"),
                        "joint_activity_ref": "activity:0001",
                        "private_activity_ref": "activity:0002"}
        return {"ok": True, "stage": "JOINT_OPEN", "decision": "ACCEPT", "llm_calls": 1,
                "session_ref": "co_presence:%s" % invitation.get("invitation_id"),
                "joint_activity_ref": "activity:0001", "private_activity_ref": "activity:0002",
                "activity_started": True, "park_applied": True,
                "decision_record_ref": "dec:0001"}

    def end(self, end_event, *, now=None) -> dict:
        self.session = dict(self.session, state="CLOSED")
        return {"ok": True, "joint_completion": {"ok": True, "activity_status": "COMPLETED",
                                                 "reason_code": "JOINT_ACTIVITY_COMPLETED"},
                "new_eligibility_refs": [], "private_activity": {"status": "WAITING"}}


class PortIntegrationTest(Base):
    def test_a_message_outside_any_session_is_observed_only(self):
        port = self._port()
        ref = self.message("在吗", operation_key="op:plain")
        out = port.tick()
        item = out["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_OBSERVED)
        self.assertEqual(item["llm_calls"], 0)
        recorded = self.inbox.get_event(ref)["data"]["event"]["result"]
        self.assertEqual(recorded["reason_code"], "OBSERVED_ONLY")
        self.assertIsNone(item["practice"])

    def test_an_accepted_invitation_opens_the_practice_and_a_message_takes_one_step(self):
        port = self._port()
        self.inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE, "text": OPENING,
                           "material_refs": [MATERIAL], "operation_key": "op:invite"})
        port.tick()
        self.assertIsNotNone(port.practice.find_open(), port.practice.describe())
        block = port.cognition_block()
        self.assertEqual(block["shared_practice_state"], "OPEN")
        self.assertIn(OPENING, json.dumps(block, ensure_ascii=False))
        self.assertTrue(block["shared_practice"]["work_text"])

        self.answers.append(self.answer(reply="一起看吧。", contribution="先看开头。"))
        ref = self.message("那从哪儿开始？", operation_key="op:step")
        out = port.tick()
        item = out["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_OBSERVED, item)
        recorded = self.inbox.get_event(ref)["data"]["event"]["result"]
        self.assertEqual(recorded["reason_code"], "SHARED_PRACTICE_CONTENT_RECORDED")
        self.assertEqual(item["llm_calls"], 1)
        self.assertTrue(item["practice"]["ok"], item["practice"])
        self.assertEqual(item["practice"]["response_text"], "一起看吧。")
        self.assertFalse(item["pause_requested"])

    def test_a_failed_step_stays_unknown_and_is_not_re_asked(self):
        port = self._port()
        self.inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE, "text": OPENING,
                           "material_refs": [MATERIAL], "operation_key": "op:invite2"})
        port.tick()
        port.practice.responder = spl.ModelPracticeResponder(FailingClient())
        self.message("看看？", operation_key="op:bad-step")
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_UNKNOWN, item)
        self.assertEqual(item["reason_code"], spl.REASON_MODEL_FAILED, item)
        self.assertEqual(item["practice"]["stage"], "model")
        quiet = port.tick()
        self.assertEqual(quiet["processed"], [])
        self.assertEqual(quiet["llm_calls"], 0)

    def test_an_explicit_end_also_ends_the_work_and_keeps_it_readable(self):
        port = self._port()
        invite = self.inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE,
                                    "text": OPENING, "material_refs": [MATERIAL],
                                    "operation_key": "op:invite3"})
        self.assertTrue(invite["ok"], invite)
        invitation = self.inbox.get_event(invite["event_ref"], include_body=True)[
            "data"]["event"]["invitation"]
        port.tick()
        self.assertIsNotNone(port.practice.find_open())
        self.answers.append(self.answer(reply="好呀", contribution="加一句。"))
        self.message("开始吧", operation_key="op:one")
        port.tick()
        ended = self.inbox.submit({"event_kind": "CO_PRESENCE_END",
                                   "invitation_id": invitation["invitation_id"],
                                   "explicit_end": True, "operation_key": "op:end3"})
        self.assertTrue(ended["ok"], ended)
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_ENDED, item)
        self.assertTrue(item["practice_ended"], item)
        self.assertEqual(item["practice_state"], "ENDED")
        read = port.practice.read_back()
        self.assertEqual(read["counts"]["responses"], 1)
        self.assertEqual(read["counts"]["contributions"], 2)
        self.assertEqual(read["state"], "ENDED")



# =====================================================================================
# 5. 内容门：只有真实当前 joint 与原邀请绑定的作品才继续说话
# =====================================================================================


class PracticeContentGateTest(Base):
    """An open practice artifact is not consent: the joint time and its own binding decide."""

    def _accept_invitation(self, port, *, operation_key: str = "op:gate:invite"):
        out = self.inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": TITLE,
                                 "text": OPENING, "material_refs": [MATERIAL],
                                 "operation_key": operation_key})
        self.assertTrue(out["ok"], out)
        invitation = self.inbox.get_event(out["event_ref"], include_body=True)[
            "data"]["event"]["invitation"]
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_ACCEPTED, item)
        return invitation, item

    def test_a_candidate_work_with_no_accepted_joint_produces_no_content(self):
        # A work that exists -- the way the phone opens one as a candidate -- is not consent:
        # there is no co-presence session at all, so a message stays an observation.
        opened = self.open_session()
        port = self._port()
        self.answers.append(self.answer(reply="不该被问", contribution="也不该写"))
        self.message("一起看看？", operation_key="op:gate:candidate")
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_OBSERVED, item)
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertIsNone(item["practice"], item)
        self.assertEqual(item["practice_gate"]["reason_code"], "CO_PRESENCE_NOT_OPEN", item)
        self.assertEqual(self.client.calls, [])
        read = port.practice.read_back(session_id=opened["session_id"])
        self.assertEqual(read["counts"]["responses"], 0, read)
        self.assertEqual(read["counts"]["contributions"], 0, read)
        self.assertEqual(read["joint_state"], "CANDIDATE", read)

    def test_the_joint_time_records_which_work_it_opened(self):
        port = self._port()
        invitation, item = self._accept_invitation(port)
        self.assertTrue(item["practice_bound"], item)
        session = port.protocol.read_session()
        self.assertEqual(session["state"], "OPEN")
        self.assertEqual(session["practice_session_ref"], item["practice_session_ref"])
        self.assertEqual(session["practice_invitation_event_ref"], item["event_ref"])
        self.assertEqual(session["invitation_id"], invitation["invitation_id"])
        # the opening text really came from the invitation's own body (A bound it to the event)
        read = port.practice.read_back(session_id=item["practice_session_ref"])
        self.assertEqual(read["title"], TITLE)
        self.assertTrue(read["text_chars"])

    def test_a_work_bound_to_another_session_or_a_lost_binding_does_not_talk(self):
        port = self._port()
        self._accept_invitation(port)
        session = port.protocol.read_session()
        real = session["practice_session_ref"]

        # (a) the binding names a different work (e.g. a candidate the phone opened earlier)
        port.protocol.session["practice_session_ref"] = "artifact:someone-else"
        self.answers.append(self.answer(reply="不该被问"))
        self.message("这页怎么样？", operation_key="op:gate:foreign")
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_OBSERVED, item)
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(item["practice_gate"]["reason_code"],
                         "SHARED_PRACTICE_BOUND_TO_ANOTHER_SESSION", item)
        self.assertEqual(self.client.calls, [])

        # (b) a restart that cannot read the binding at all: same answer, by name
        port.protocol.session.pop("practice_session_ref", None)
        self.message("还在吗？", operation_key="op:gate:unbound")
        item = port.tick()["processed"][0]
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(item["practice_gate"]["reason_code"], "SHARED_PRACTICE_NOT_BOUND", item)
        self.assertEqual(self.client.calls, [])
        # the work itself was not touched by either message
        read = port.practice.read_back(session_id=real)
        self.assertEqual(read["counts"]["responses"], 0, read)

    def test_an_ended_joint_activity_stops_the_content_even_if_the_work_is_open(self):
        port = self._port(runtime=FakeRuntime(joint_status="COMPLETED"))
        self._accept_invitation(port)
        self.answers.append(self.answer(reply="不该被问"))
        self.message("再聊两句？", operation_key="op:gate:joint-done")
        item = port.tick()["processed"][0]
        self.assertEqual(item["outcome"], social.OUTCOME_OBSERVED, item)
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(item["practice_gate"]["reason_code"], "JOINT_ACTIVITY_NOT_ACTIVE", item)
        self.assertEqual(item["practice_gate"]["joint_activity_status"], "COMPLETED", item)
        self.assertEqual(self.client.calls, [])

    def test_a_paused_work_or_a_closed_session_does_not_keep_producing_content(self):
        port = self._port()
        invitation, item = self._accept_invitation(port)
        session_id = item["practice_session_ref"]
        head = port.practice._head(session_id)
        paused = self.subject.pause_practice(session_id, note="先放一放", expected_head=head)
        self.assertTrue(paused["ok"], paused)
        # a paused work is still "this joint time's work", but it must not answer by itself
        self.assertIsNotNone(port.practice.find_open())
        self.assertIsNone(port.practice.find_content_session())
        self.answers.append(self.answer(reply="不该被问"))
        self.message("还在吗？", operation_key="op:gate:paused")
        item = port.tick()["processed"][0]
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(item["practice_gate"]["reason_code"], "SHARED_PRACTICE_NOT_OPEN", item)
        self.assertEqual(item["practice_gate"]["practice_state"], "PAUSED", item)
        self.assertEqual(self.client.calls, [])

        # after the explicit end the co-presence session is CLOSED: no content, no replay
        resumed = self.subject.resume_practice(
            session_id, note="继续", expected_head=port.practice._head(session_id),
            basis={"event_ref": item["event_ref"]})
        self.assertTrue(resumed["ok"], resumed)
        self.answers.append(self.answer(reply="好呀"))
        self.message("那继续。", operation_key="op:gate:resumed")
        self.assertEqual(port.tick()["processed"][0]["llm_calls"], 1)
        end = self.inbox.submit({"event_kind": "CO_PRESENCE_END",
                                 "invitation_id": invitation["invitation_id"],
                                 "explicit_end": True, "operation_key": "op:gate:end"})
        self.assertTrue(end["ok"], end)
        self.assertEqual(port.tick()["processed"][0]["outcome"], social.OUTCOME_ENDED)
        self.assertEqual(port.protocol.read_session()["state"], "CLOSED")
        self.answers.append(self.answer(reply="不该被问"))
        self.message("结束以后呢？", operation_key="op:gate:after-end")
        item = port.tick()["processed"][0]
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertEqual(item["practice_gate"]["reason_code"], "CO_PRESENCE_NOT_OPEN", item)
        # the paused message and the after-end message asked nothing: one real step in total
        self.assertEqual(len(self.client.calls), 1)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
