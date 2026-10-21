"""v0.4 B 整链：真 Supply/World/runtime/assembly/``runner_tick`` 上的「有内容的相处」。

对象是**父级可复用**的 ``SharedPracticeChainFixture``：整段链里只有*模型*是本地固定的原始
JSON（一条回答一次调用、严格 schema、不重试），其余全部走产品自己的正常入口：

1. 私人材料**真的**读第 1 页（AR-0 owner action 落书签，checkpoint 真实存在）；
2. 显式**材料邀请**（invitation 带 ``material_refs`` = 那份真实的阅读 artifact）；
3. 一次正常的 AG-1 Decision 接受 → 正常 handoff（私人 Activity ``WAITING`` 保留 checkpoint，
   joint Activity 成为单一前台，作品会话被**这一场 joint**绑定）；
4. 3 条真实用户文本，各**恰好一次**内容请求 → 她的最终可见回应 + 双方各自的真实贡献 +
   作品逐版推进；
5. 显式结束 → joint Activity ``COMPLETED``、原私人 Activity 的续作资格 ``OPEN``；
6. 首次正常资格窗里 fresh Decision ``RESUME`` 同一 Activity、读同一材料第 2 页；
7. 关闭再同实例重开：作品正文/贡献/seq/各自归属/事件结果/原断点都能读回，**无重放**，
   无新消息时 0 内容请求。

需要 POSIX（``fcntl`` + ``AF_UNIX``）；原生 Windows 上整链用例具名跳过（不是冒充通过）。
夹具根在 ``/tmp/day-life-parent-*`` 里**保留**：``tearDown`` 只关资源（close-only），
不调用会 ``rmtree`` 的 donor teardown，也没有全局 no-op 删除守卫。

父级怎么用（``class MyTest(SharedPracticeChainFixture)`` 或直接查这两个类）：

* ``setup() -> fixture``           真实例 → runtime → assembly → 把模型换成脚本原始 JSON；
* ``read(fixture) -> dict``        从 owner 读回 co_presence/作品/joint/私人/资格/待处理；
* ``step(fixture, payload) -> dict`` 投一条真实 transport 事件 + 跑一次 ``runner_tick``；
* ``tick(fixture, cognition=None)`` 只跑一次 ``runner_tick``（换/不换 AG-1 认知）；
* ``close(fixture)``               只关资源，数据保留；
* ``restart(fixture) -> fixture``  同一个实例、同一份数据根上的新 runtime + 新 assembly。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "integration"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_model_client as amc  # noqa: E402
import life_local_contact as lc  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import one_day_life as odl  # noqa: E402
import shared_practice_life as spl  # noqa: E402

#: 整链夹具与它依赖的 runtime 都需要 ``fcntl``/``AF_UNIX``：没有它们的平台只跳过整链，
#: 分派用例如常跑（见 ``test_v04_shared_practice_life_seam``）。
CHAIN_IMPORT_ERROR: str | None = None
try:
    from test_life_v1_integration import DScenarioBase, posix_only  # noqa: E402
    from test_one_day_life import StubCognition  # noqa: E402

    import autonomous_life_assembly as assembly  # noqa: E402
except Exception as _chain_exc:  # pragma: no cover - a platform without the POSIX owners
    CHAIN_IMPORT_ERROR = "%s: %s" % (type(_chain_exc).__name__, _chain_exc)
    DScenarioBase = object  # type: ignore[assignment]
    StubCognition = object  # type: ignore[assignment]
    assembly = None  # type: ignore[assignment]

    def posix_only(target):  # type: ignore[misc]
        return unittest.skip("the chain fixture needs the POSIX owners (%s)"
                             % CHAIN_IMPORT_ERROR)(target)


SCOPE = "一起把这两页排出来"
INVITE_BODY = "我把这一页放在桌上了，要不要一起看看怎么排？"

#: 三条真实用户输入（原样进 transport，原样成为用户那一半的贡献）。
USER_TEXTS = (
    "我刚翻到这一页，桌子那边空得太整齐了。",
    "第二段我读起来有点挤，你觉得呢？",
    "要不把开头那句留着，别动它。",
)
#: 每一次内容请求的严格原始 JSON：只有 reply / contribution / work_text 三个键。
STEP_ANSWERS = (
    {"reply": "我看到了，桌子那段太满了，先把行距放松一档试试。",
     "contribution": "把第一页桌子那段的行距放宽一档。",
     "work_text": "第一页：房间里的桌子与窗。\n第一版：桌子那段行距放宽一档，其余不动。"},
    {"reply": "第二段确实挤，我把它拆成两句，读起来会喘得过气。",
     "contribution": "把第二段拆成两句，中间留一行空。",
     "work_text": "第一页：房间里的桌子与窗。\n第一版：桌子那段行距放宽一档，其余不动。\n"
                  "第二页：第二段拆成两句，中间留一行空。"},
    {"reply": "开头那句我没动，只把后面两处顺手理了。",
     "contribution": "开头那句保留，只理后面两处标点。",
     "work_text": "第一页：房间里的桌子与窗。\n第一版：桌子那段行距放宽一档，其余不动。\n"
                  "第二页：第二段拆成两句，中间留一行空。\n"
                  "第三版：开头那句原样保留，后面两处标点理过。"},
)


class RawJSONClient:
    """The model, replaced by a strict raw-JSON script: one answer per call, no retry.

    ``send_split_capable`` is what the product's own admission handshake requires before it
    promises a send: this stand-in can separate the send phase from the wait (it answers at
    once), so a refused promise would be a real product refusal, not a fixture limitation.
    """

    provider = "stub"
    model = "stub:shared-practice-json"
    send_split_capable = True

    def __init__(self, answers=None) -> None:
        self.answers: list[str] = [json.dumps(row, ensure_ascii=False) for row in (answers or [])]
        self.calls: list[dict] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 max_output_tokens: int | None = None) -> amc.ModelCallResult:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt,
                           "max_output_tokens": max_output_tokens})
        text = self.answers.pop(0) if self.answers else ""
        return amc.ModelCallResult(purpose="shared_practice", provider=self.provider,
                                   model=self.model, text=text, latency_ms=1, status=200)


def _decision_response(cognition, raw: dict):
    import agency_decision_ag1 as ag1

    cognition.call_count += 1
    return ag1.CognitiveDecisionResponse(
        model_run_ref="model_run:stub:practice:%d" % cognition.call_count,
        model_id=cognition.model_id, model_version=cognition.model_version,
        raw_output=raw, latency_ms=1)


@posix_only
class SharedPracticeChainFixture(DScenarioBase):
    """父级可复用的整链夹具：真实正常入口，只把模型换成本地原始 JSON。

    ``setUp``/``tearDown`` 是这一层唯一和 donor 不同的地方：``tempfile.tempdir`` 指向 ``/tmp``，
    临时根都叫 ``day-life-parent-*``（Unix socket 路径要短），并且 ``tearDown`` **只关资源**——
    donor 的 ``tearDown`` 会 ``shutil.rmtree`` 掉实例树，这里整棵保留给父级事后核对。
    """

    TEMP_ROOT = "/tmp"
    TEMP_PREFIX = "day-life-parent-"

    def setUp(self) -> None:
        self._tempdir_saved = tempfile.tempdir
        tempfile.tempdir = self.TEMP_ROOT
        super().setUp()

    def tearDown(self) -> None:
        # close-only：先按生产顺序关 runtime（状态根还在的时候 flush），再**不删**夹具。
        for runtime in list(getattr(self, "_runtimes", []) or []):
            try:
                runtime.close()
            except Exception:  # a close failure must not hide the test result
                pass
        self._runtimes = []
        tempfile.tempdir = self._tempdir_saved

    def temp_dir(self, prefix: str = "wg-test-") -> Path:
        """Short, named roots: ``/tmp/day-life-parent-*`` (Unix sockets need the room)."""

        return super().temp_dir(prefix=self.TEMP_PREFIX + prefix)

    # -- the fixture -----------------------------------------------------------------------

    def setup(self, *, cognition=None, answers=None) -> SimpleNamespace:
        """Build the real chain and wire the scripted content model onto the real port."""

        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = cognition or self._no_action_cognition()
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.memory, fixture.env, fixture.cognition = prepared.memory, env, cognition
        contacts = fixture.assembled["contacts"]
        self.assertEqual(contacts["status"], "READY", contacts)
        self.assertEqual(contacts["shared_practice"]["status"], "READY", contacts)
        fixture.contacts = contacts
        fixture.inbox = lc.LocalContactInbox(
            odl.life_state_root(self.instance), subject_id=prepared.data["subject_id"],
            home_ref=contacts["home_ref"])
        fixture.contact_port = fixture.assembled["contact_port"]
        self.assertIsNotNone(fixture.contact_port, contacts)
        fixture.practice = fixture.contact_port.practice
        self.assertIsNotNone(fixture.practice, fixture.practice)
        fixture.protocol = fixture.assembled["cycle"].co_presence
        self.assertIsNotNone(fixture.protocol)
        return self._attach_model(fixture, answers=answers)

    def _attach_model(self, fixture, *, answers=None) -> SimpleNamespace:
        """The content model: one scripted raw JSON answer per request, on the real ledger.

        The ledger is the instance's **own persistent request budget** (the same object AG-1
        spends from), and every send passes the same admission handshake before the transport
        call -- exactly what ``resolve_shared_practice`` wires in production.  ``None`` for
        answers means "no script": any content request would then be a visible failure.
        """

        fixture.content_client = RawJSONClient(answers)
        fixture.content_admission = []

        def admit(kind: str, attempt: int) -> None:
            fixture.content_admission.append({"kind": str(kind), "attempt": int(attempt)})

        fixture.content_ledger = fixture.cycle.budget.requests
        self.assertIsNotNone(fixture.content_ledger, "the instance must carry a request ledger")
        fixture.practice.responder = spl.ModelPracticeResponder(
            lrb.BudgetedModelClient(fixture.content_client, fixture.content_ledger,
                                    admission=admit))
        return fixture

    # -- one step, one tick ----------------------------------------------------------------

    def tick(self, fixture, cognition=None) -> dict:
        """One bounded resident step.  ``cognition`` replaces this instance's AG-1 branch."""

        if cognition is not None:
            fixture.runtime.decision_service.cognition_adapter = cognition
        return assembly.runner_tick(fixture.assembled, act=True)

    def step(self, fixture, payload: dict, *, cognition=None) -> dict:
        """Submit one real transport event, then run one real ``runner_tick``."""

        submitted = fixture.inbox.submit(payload)
        self.assertTrue(submitted["ok"], submitted)
        event_ref = str(submitted["event_ref"])
        tick = self.tick(fixture, cognition)
        got = fixture.inbox.get_event(event_ref, include_body=True)
        self.assertTrue(got["ok"], got)
        return {"event_ref": event_ref, "event": dict(got["data"]["event"]), "tick": tick,
                "contacts": dict(tick.get("contacts") or {})}

    def _open_window(self, fixture):
        """Open the resident's check window with a controlled clock -- no sleep, no luck."""

        runner = fixture.assembled["runner"]
        base = datetime.now(timezone.utc)
        runner._clock = lambda: base + timedelta(minutes=int(runner.interval_minutes) + 1)
        return runner

    def _reading(self, fixture):
        """The private round really reads page 1 of the instance's own material."""

        runtime, cycle, data = fixture.runtime, fixture.cycle, fixture.data
        runtime.decision_service.cognition_adapter = self._reading_cognition(data)
        read = cycle.evaluate_once(trigger_ref="evt:practice:read", act=True)
        self.assertEqual(read["outcome"], "ACTED", read)
        self.assertEqual(read["reading"]["page"], 1, read["reading"])
        ref = str(read["activity_ref"])
        return ref, self.activity(runtime, ref)

    def _reading_page(self, fixture) -> int:
        import subject_private_materials as materials

        situation = materials.read_reading_situation(
            fixture.port, reading_artifact_id=fixture.data["reading_artifact_id"],
            private_items_artifact_id=fixture.data["private_items_artifact_id"])
        return int(situation["position"]["page"] or 0)

    def _state(self, fixture, activity_ref: str) -> dict:
        row = self.activity(fixture.runtime, activity_ref)
        return {"status": row.get("status"), "revision": row.get("revision"),
                "checkpoint_ref": row.get("checkpoint_ref"),
                "resume_condition_ref": row.get("resume_condition_ref"),
                "foreground": fixture.runtime.canonical_state().get("foreground_activity_ref")}

    def _joint_activities(self, fixture) -> list[dict]:
        """The joint Activities the owner currently reports as open (identity by kind)."""

        return [dict(row) for row in fixture.runtime.activity_read.list_open_activities()
                if str(row.get("activity_kind") or "") == "FINITE_CO_PRESENCE"]

    # -- read back / close / restart -------------------------------------------------------

    def read(self, fixture) -> dict:
        """Everything the acceptance reads back, from the owners (no cached view)."""

        session = dict(fixture.protocol.read_session() or {})
        joint_ref = str(session.get("joint_activity_ref") or "")
        private_ref = str(session.get("private_activity_ref") or "")
        return {
            "co_presence": session,
            "practice": fixture.practice.read_back(),
            "open_practice": fixture.practice.find_open(),
            "joint_activity": self.activity(fixture.runtime, joint_ref) if joint_ref else {},
            "private_activity": self.activity(fixture.runtime, private_ref) if private_ref else {},
            "foreground": fixture.runtime.canonical_state().get("foreground_activity_ref"),
            "pending": dict(fixture.inbox.pending(limit=1).get("data") or {}).get("matched"),
            "eligibilities": self.eligibilities(fixture.runtime),
            "reading_page": self._reading_page(fixture),
            "content_calls": len(fixture.content_client.calls),
            "requests": dict(fixture.content_ledger.view()),
        }

    def close(self, fixture) -> None:
        """Close the instance's own resources; the data stays (close-only, never rmtree)."""

        runtime = getattr(fixture, "runtime", None)
        if runtime is not None:
            runtime.close()

    def restart(self, fixture) -> SimpleNamespace:
        """Same instance, same data root, new process-shaped objects and a fresh model client.

        The scripted answers are deliberately **not** carried over: after a restart any content
        request would be a visible failure, so "no replay" is proven by zero calls, not by a
        reading of the log.
        """

        self.close(fixture)
        cognition = self._no_action_cognition()
        runtime, port = self.runtime_with_memory(cognition, fixture.env, fixture.data,
                                                 fixture.memory)
        again = self.assemble(runtime, fixture.env, cognition=cognition)
        again.runtime, again.port, again.data = runtime, port, fixture.data
        again.memory, again.env, again.cognition = fixture.memory, fixture.env, cognition
        contacts = again.assembled["contacts"]
        self.assertEqual(contacts["status"], "READY", contacts)
        self.assertEqual(contacts["shared_practice"]["status"], "READY", contacts)
        again.contacts = contacts
        again.inbox = lc.LocalContactInbox(
            odl.life_state_root(self.instance), subject_id=fixture.data["subject_id"],
            home_ref=contacts["home_ref"])
        again.contact_port = again.assembled["contact_port"]
        self.assertIsNotNone(again.contact_port, contacts)
        again.practice = again.contact_port.practice
        self.assertIsNotNone(again.practice, again.practice)
        again.protocol = again.assembled["cycle"].co_presence
        self.assertIsNotNone(again.protocol)
        return self._attach_model(again, answers=None)

    # -- raw-JSON AG-1 branches ------------------------------------------------------------

    def _no_action_cognition(self):
        class NoActionCognition(StubCognition):
            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                return _decision_response(inner, {
                    "decision": "NO_ACTION", "selected_candidate_id": None,
                    "reason_codes": allowed[:1], "short_rationale": "先不动",
                    "deferred_candidate_ids": []})

        return NoActionCognition("artifact")

    def _co_presence_cognition(self, decision: str = "START"):
        """The fresh AG-1 branch that considers the invitation's own candidate."""

        class CoPresenceCognition(StubCognition):
            def __init__(inner):
                super().__init__("artifact")
                inner.seen: list[list[dict]] = []

            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                candidates = [dict(item) for item in (getattr(request, "candidates", []) or [])]
                inner.seen.append([{"target_refs": list(item.get("target_refs") or []),
                                    "source_ref": item.get("source_ref"),
                                    "legal_decisions": list(item.get("legal_decisions") or [])}
                                   for item in candidates])
                for candidate in candidates:
                    refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
                    refs.append(str(candidate.get("source_ref") or ""))
                    if not any("co_presence" in ref for ref in refs):
                        continue
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if decision not in per:
                        continue
                    return _decision_response(inner, {
                        "decision": decision,
                        "selected_candidate_id": candidate["candidate_id"],
                        "reason_codes": allowed[:1],
                        "short_rationale": "有人带着材料来，我看看要不要一起",
                        "deferred_candidate_ids": []})
                return _decision_response(inner, {
                    "decision": "NO_ACTION", "selected_candidate_id": None,
                    "reason_codes": allowed[:1],
                    "short_rationale": "没有可选的来访候选", "deferred_candidate_ids": []})

        return CoPresenceCognition()


@posix_only
class SharedPracticeChainTest(SharedPracticeChainFixture):
    """整链：读页 1 → 材料邀请 → 3 次真内容步骤 → 结束 → RESUME 页 2 → 重开读回。"""

    # -- helpers ---------------------------------------------------------------------------

    def _chain(self, *, answers=None) -> SimpleNamespace:
        """读第 1 页 → 材料邀请 → 接受/handoff → 3 条真实用户文本（各一次内容请求）。"""

        fixture = self.setup(answers=list(STEP_ANSWERS if answers is None else answers))
        reading_ref, before = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        self.assertEqual(baseline["status"], "ACTIVE", baseline)
        self.assertTrue(baseline["checkpoint_ref"], baseline)
        self.assertEqual(baseline["foreground"], reading_ref)
        self.assertEqual(self._reading_page(fixture), 1)
        fixture.reading_ref, fixture.baseline = reading_ref, baseline

        # the material the invitation names is the instance's *own* readable reading artifact
        material = str(fixture.data["reading_artifact_id"])
        refs, refusal = spl.bound_material_refs([material])
        self.assertIsNone(refusal, (material, refusal))
        self.assertEqual(refs, [material], material)

        invitation = self.step(fixture, {
            "event_kind": "CO_PRESENCE_INVITATION", "scope": SCOPE, "text": INVITE_BODY,
            "material_refs": [material], "operation_key": "op:practice:invite"},
            cognition=self._co_presence_cognition("START"))
        contacts = invitation["contacts"]
        item = contacts["processed"][0]
        self.assertEqual(contacts["llm_calls"], 1, contacts)
        self.assertEqual(item["outcome"], "ACCEPTED", item)
        self.assertTrue(item["joint_activity_ref"], item)
        self.assertTrue(item["park_applied"], item)
        self.assertTrue(item["practice_bound"], item)
        session = fixture.protocol.read_session()
        self.assertEqual(session["state"], "OPEN", session)
        self.assertEqual(session["practice_session_ref"], item["practice_session_ref"], session)
        joint_ref = str(item["joint_activity_ref"])
        private = self._state(fixture, reading_ref)
        self.assertEqual(private["status"], "WAITING", private)
        self.assertEqual(private["checkpoint_ref"], baseline["checkpoint_ref"], private)
        self.assertEqual(self.activity(fixture.runtime, joint_ref)["status"], "ACTIVE")
        self.assertEqual(fixture.runtime.canonical_state().get("foreground_activity_ref"),
                         joint_ref)

        fixture.invitation = dict(invitation["event"])
        fixture.invitation_id = str(fixture.invitation["invitation"]["invitation_id"])
        fixture.joint_ref = joint_ref
        fixture.practice_session = str(item["practice_session_ref"])
        # opening is NOT acceptance: the work exists, the joint state is a candidate
        opened = fixture.practice.read_back(session_id=fixture.practice_session)
        self.assertEqual(opened["state"], "OPEN", opened)
        self.assertEqual(opened["joint_state"], "CANDIDATE", opened)
        self.assertEqual(opened["material_count"], 1, opened)

        # three real user messages: one strict content request each, both sides really write
        fixture.steps = []
        for index, text in enumerate(USER_TEXTS):
            before_ledger = int(fixture.content_ledger.view()["utc_day_calls"])
            out = self.step(fixture, {"event_kind": "LOCAL_MESSAGE", "text": text,
                                      "operation_key": "op:practice:msg:%d" % index})
            contact = out["contacts"]
            processed = contact["processed"][0]
            self.assertEqual(contact["llm_calls"], 1, (index, contact))
            self.assertEqual(processed["outcome"], "OBSERVED", processed)
            self.assertFalse(processed["pause_requested"], processed)
            self.assertFalse(processed["activity_started"], processed)
            practice = processed["practice"]
            self.assertTrue(practice and practice["ok"], (index, practice))
            self.assertEqual(practice["llm_calls"], 1, practice)
            self.assertEqual(practice["commitment_state"], "NONE", practice)
            self.assertTrue(practice["contribution_recorded"], practice)
            self.assertTrue(practice["work_version_id"], practice)
            self.assertEqual(int(fixture.content_ledger.view()["utc_day_calls"]) - before_ledger,
                             1, (index, fixture.content_ledger.view()))
            self.assertEqual(out["event"]["result"]["reason_code"],
                             "SHARED_PRACTICE_CONTENT_RECORDED", out["event"]["result"])
            fixture.steps.append({"text": text, "event_ref": out["event_ref"],
                                  "practice": practice})
        return fixture

    def _end_and_resume(self, fixture) -> dict:
        """显式结束 → joint COMPLETED + 原私人资格 OPEN；同一正常资格窗 fresh RESUME 读页 2。"""

        # No clock jump here: the end event's own eligibility feed drives the resumption, and a
        # jumped window would let LR-4 expire the private wait before the end could match it.
        resume_cognition = self._resume_cognition()
        end = self.step(fixture, {"event_kind": "CO_PRESENCE_END",
                                  "invitation_id": fixture.invitation_id,
                                  "explicit_end": True,
                                  "operation_key": "op:practice:end"},
                        cognition=resume_cognition)
        ended = end["contacts"]["processed"][0]
        self.assertEqual(ended["outcome"], "ENDED", ended)
        self.assertTrue(ended["joint_completed"], ended)
        self.assertEqual(self.activity(fixture.runtime, fixture.joint_ref)["status"], "COMPLETED")
        self.assertEqual((ended.get("private_activity") or {}).get("status"), "WAITING", ended)
        self.assertEqual(fixture.protocol.read_session()["state"], "CLOSED")
        self.assertTrue(ended.get("practice_ended"), ended)
        self.assertEqual(str((ended.get("shared_practice") or {}).get("state") or ""), "ENDED",
                         ended.get("shared_practice"))
        # the end itself resumed nothing: LR-4 only opened the private work's eligibility
        self.assertTrue(ended["new_eligibility_refs"], ended)
        self.assertEqual(self.activity(fixture.runtime, fixture.reading_ref)["status"], "ACTIVE")
        self.assertEqual(self._reading_page(fixture), 2)
        wakes = [*(end["tick"].get("events") or []), end["tick"].get("due") or {}]
        resumed = [row for row in wakes
                   if row.get("decision") == "RESUME" and row.get("status") == "ACTED"]
        self.assertTrue(resumed, wakes)
        after = self._state(fixture, fixture.reading_ref)
        self.assertEqual(after["status"], "ACTIVE", after)
        self.assertEqual(after["foreground"], fixture.reading_ref, after)
        # the private Activity is the same one: its own revision moved, its id did not
        self.assertEqual(self.activity(fixture.runtime, fixture.reading_ref)["activity_id"],
                         fixture.reading_ref)
        return {"ended": ended, "event_ref": end["event_ref"], "resume": resumed,
                "state": after}

    # -- 1. the whole chain ----------------------------------------------------------------

    def test_read_invite_three_real_steps_then_end_and_resume_page_two(self):
        fixture = self._chain()
        calls = fixture.content_client.calls
        self.assertEqual(len(calls), 3, calls)

        # (a) each request saw the real situation, and only the strict three-key schema
        first = json.loads(calls[0]["user_prompt"])
        self.assertEqual(first["session_id"], fixture.practice_session)
        self.assertEqual(first["state"], "OPEN")
        self.assertEqual(first["new_message_from_the_person"], USER_TEXTS[0])
        self.assertEqual(len(first["shared_materials"]), 1)
        self.assertEqual(first["shared_materials"][0]["artifact_id"],
                         fixture.data["reading_artifact_id"])
        self.assertTrue(first["shared_materials"][0]["excerpt"])
        self.assertEqual(first["joint_work_text"], INVITE_BODY)
        third = json.loads(calls[2]["user_prompt"])
        # the second version really became the text the third request was written against
        self.assertIn("第二段拆成两句", third["joint_work_text"])
        self.assertEqual(third["new_message_from_the_person"], USER_TEXTS[2])
        self.assertTrue(third["recent_entries"], third["recent_entries"])
        self.assertTrue(all(call["max_output_tokens"] == 700 for call in calls))

        # (b) both sides really contributed, and the work advanced one version per step
        read = self.read(fixture)
        practice = read["practice"]
        self.assertEqual(practice["counts"]["responses"], 3, practice)
        self.assertEqual(practice["counts"]["contributions"], 6, practice)
        self.assertEqual(practice["distinct_seqs"], [1, 2, 3, 4, 5, 6, 7, 8, 9], practice)
        self.assertEqual(practice["joint_state"], "JOINT", practice)
        texts = [row["text"] for row in practice["contributions"]
                 if row.get("contributor") == "user"]
        self.assertEqual(texts, list(USER_TEXTS), texts)
        her = [row["text"] for row in practice["responses"]]
        self.assertEqual(her, [row["reply"] for row in STEP_ANSWERS], her)
        versions = [step["practice"]["work_version_id"] for step in fixture.steps]
        self.assertEqual(len(set(versions)), 3, versions)
        # the last version is what the owner's own bytes say
        content = self.content(fixture.practice_session)
        self.assertIn("第三版：开头那句原样保留", content)
        # and the invitation's body really opened the work (bound to the invitation event)
        self.assertTrue(practice["title"] == SCOPE, practice["title"])

        # (c) every content request paid one reservation and was admitted before sending
        self.assertEqual(fixture.content_ledger is fixture.cycle.budget.requests, True)
        self.assertEqual(len(fixture.content_admission), 3, fixture.content_admission)
        self.assertTrue(all(row["kind"] == "content" for row in fixture.content_admission),
                        fixture.content_admission)

        # (d) the joint time is still the foreground and the private work is parked, untouched
        parked = self._state(fixture, fixture.reading_ref)
        self.assertEqual(parked["status"], "WAITING", parked)
        self.assertEqual(parked["checkpoint_ref"], fixture.baseline["checkpoint_ref"], parked)
        self.assertEqual(self._reading_page(fixture), 1)

        result = self._end_and_resume(fixture)
        self.assertTrue(result["resume"], result)
        self.assertEqual(self._reading_page(fixture), 2)
        # after the explicit end nothing is deleted: the work is readable, in full
        final = self.read(fixture)
        self.assertEqual(final["practice"]["state"], "ENDED", final["practice"])
        self.assertEqual(final["practice"]["counts"]["responses"], 3, final["practice"])
        self.assertEqual(final["practice"]["distinct_seqs"], [1, 2, 3, 4, 5, 6, 7, 8, 9],
                         final["practice"])

    # -- 2. a restart of the same instance -------------------------------------------------

    def test_a_restart_of_the_same_instance_reads_the_work_and_replays_nothing(self):
        fixture = self._chain()
        self._end_and_resume(fixture)
        before = self.read(fixture)
        self.assertEqual(before["practice"]["state"], "ENDED", before["practice"])
        self.assertIsNone(before["open_practice"], before["open_practice"])

        again = self.restart(fixture)
        after = self.read(again)

        # (a) the work, both sides' contributions, the seq numbers and the results are all there
        self.assertEqual(after["practice"]["session_id"], before["practice"]["session_id"])
        self.assertEqual(after["practice"]["state"], "ENDED")
        self.assertEqual(after["practice"]["counts"], {"contributions": 6, "responses": 3})
        self.assertEqual(after["practice"]["distinct_seqs"], [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(after["practice"]["head_version_id"],
                         before["practice"]["head_version_id"])
        self.assertEqual(after["practice"]["title"], SCOPE)
        self.assertEqual(after["practice"]["material_count"], 1)
        self.assertEqual([row["contributor"] for row in after["practice"]["contributions"]],
                         [row["contributor"] for row in before["practice"]["contributions"]])
        self.assertEqual([row["text"] for row in after["practice"]["responses"]],
                         [row["reply"] for row in STEP_ANSWERS])
        self.assertTrue(after["practice"]["text_chars"])

        # (b) the original stop point: the joint Activity is really COMPLETED, the private work
        #     is the same Activity, resumed on its own checkpoint, and the session is CLOSED
        self.assertEqual(after["co_presence"]["state"], "CLOSED")
        self.assertEqual(after["co_presence"]["invitation_id"], fixture.invitation_id)
        self.assertEqual(after["co_presence"]["practice_session_ref"], fixture.practice_session)
        self.assertEqual(after["joint_activity"]["status"], "COMPLETED")
        self.assertEqual(after["private_activity"]["activity_id"], fixture.reading_ref)
        self.assertEqual(after["private_activity"]["status"], "ACTIVE")
        self.assertEqual(self._reading_page(again), 2)
        # the private save point taken when the joint time began is still recorded as it was
        self.assertEqual(after["co_presence"]["private_checkpoint_ref"],
                         fixture.baseline["checkpoint_ref"])

        # (c) the transport results are the same facts they were: nothing is pending, nothing
        #     is re-decided and no old message is replayed
        self.assertEqual(after["pending"], 0)
        for step in fixture.steps:
            row = again.inbox.get_event(step["event_ref"], include_body=False)
            self.assertEqual(row["data"]["event"]["result"]["reason_code"],
                             "SHARED_PRACTICE_CONTENT_RECORDED")
            self.assertEqual(row["data"]["event"]["processing_state"], "PROCESSED")
        quiet = self.tick(again)
        self.assertEqual((quiet.get("contacts") or {}).get("processed"), [])
        self.assertEqual((quiet.get("contacts") or {}).get("llm_calls"), 0)
        self.assertEqual(len(again.content_client.calls), 0)
        self.assertIsNone(again.practice.last_step)

        # (d) a message arriving after the restart does not continue an ended joint time
        late = self.step(again, {"event_kind": "LOCAL_MESSAGE", "text": "我回来了。",
                                 "operation_key": "op:practice:after-restart"})
        item = late["contacts"]["processed"][0]
        self.assertEqual(item["llm_calls"], 0, item)
        self.assertIsNone(item["practice"], item)
        self.assertEqual(item["practice_gate"]["reason_code"], "CO_PRESENCE_NOT_OPEN", item)
        self.assertEqual(len(again.content_client.calls), 0)
        self.assertEqual(self.read(again)["practice"]["distinct_seqs"],
                         [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(int(self.read(again)["requests"]["utc_day_calls"]),
                         int(before["requests"]["utc_day_calls"]))

    # -- 3. no joint time, no content -------------------------------------------------------

    def test_a_message_before_any_joint_time_is_observed_and_asks_nothing(self):
        fixture = self.setup(answers=list(STEP_ANSWERS))
        reading_ref, _ = self._reading(fixture)
        baseline = self._state(fixture, reading_ref)
        # the message tick runs with a NO_ACTION branch: what is proven here is that the message
        # itself asked nothing, not that the private reading happened to decline on its own
        out = self.step(fixture, {"event_kind": "LOCAL_MESSAGE", "text": "在吗？",
                                  "operation_key": "op:practice:before"},
                        cognition=self._no_action_cognition())
        item = out["contacts"]["processed"][0]
        self.assertEqual(out["contacts"]["llm_calls"], 0, out["contacts"])
        self.assertEqual(item["outcome"], "OBSERVED", item)
        self.assertIsNone(item["practice"], item)
        self.assertEqual(item["practice_gate"]["reason_code"], "CO_PRESENCE_NOT_OPEN", item)
        self.assertEqual(fixture.content_client.calls, [])
        self.assertEqual(self._state(fixture, reading_ref), baseline)
        self.assertIsNone(fixture.protocol.read_session().get("practice_session_ref"))
        self.assertEqual(fixture.practice.find_open(), None)


_OWN_TESTS = (
    "test_read_invite_three_real_steps_then_end_and_resume_page_two",
    "test_a_restart_of_the_same_instance_reads_the_work_and_replays_nothing",
    "test_a_message_before_any_joint_time_is_observed_and_asks_nothing",
)
if CHAIN_IMPORT_ERROR is None:
    # ``DScenarioBase``/``CycleTest`` come with their own all-day cases; re-collecting them here
    # would run somebody else's tests in this module's order.  Only this class's own cases run,
    # and the fixture class keeps no collectable case at all (it is a fixture, not a suite).
    for _inherited in [name for name in dir(SharedPracticeChainTest) if name.startswith("test_")
                       and name not in _OWN_TESTS]:
        setattr(SharedPracticeChainTest, _inherited, None)
    for _inherited in [name for name in dir(SharedPracticeChainFixture) if name.startswith("test_")]:
        setattr(SharedPracticeChainFixture, _inherited, None)
    del DScenarioBase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
